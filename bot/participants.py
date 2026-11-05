"""Who is in the call, and the join/leave webhooks (docs/SPEC.md 3.1 and 5.4).

- The People panel is the list to trust; the video tiles are only a VIEW (they move and hide during a
  screen share or with many people). The panel is opened once after admission; tiles are the fallback.
- A join is sent the first time a person is seen. A leave only after the person has been missing from
  RELIABLE reads for 10 seconds or more: a false leave can end a live demo (xebit ends the session 60 s
  after its head count reaches 0).
- The bot itself never counts: entries marked "(You)", the own tile (it has "Backgrounds and effects"),
  or the bot's own typed name are skipped, and once an id is known as the bot it stays skipped.
- Meet is read only through Playwright locators (they run in an isolated world). No `evaluate`, no
  `all_inner_texts` (those run in Meet's page world).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

LEAVE_AFTER = 10.0
READ_EVERY = 1.0

PEOPLE_BUTTON = re.compile(r"^(people|show everyone|participants)\b", re.I)
COUNT_BUTTON = re.compile(r"^\d{1,3}$")
PANEL_LIST = re.compile(r"participant|people|in (the )?(call|meeting)", re.I)
YOU = re.compile(r"\(\s*you\s*\)", re.I)
# Labels Meet may add after a name: "Asha (Guest)", "Asha (Presenting)"...
NAME_SUFFIX = re.compile(r"\s*\((you|guest|host|meeting host|presenting|presentation|external)\)\s*$", re.I)
HOST = re.compile(r"meeting host|\bhost\b", re.I)
ALONE = re.compile(r"no one else is (in this (meeting|call)|here)|you're the only one here|you are the only one here", re.I)
SELF_TILE = ('[aria-label*="Backgrounds and effects" i], [aria-label^="Reframe" i], [aria-label*="visual effects" i], '
             '[data-self-name]')
# Lines in a tile or list entry that are not the person's name: icon ligatures and Meet's labels.
NOT_A_NAME = re.compile(
    r"^([a-z0-9]+(_[a-z0-9]+)+|devices|more_vert|mic|mic_off|videocam|videocam_off|keep|keep_off|pin|"
    r"backgrounds and effects|show in a tile|reframe|more options( for .*)?|others might still see your full video\.?|"
    r"meeting host|you|\(you\)|presentation|presenting|.* is presenting|you are presenting|you're presenting|"
    # The buttons Meet shows over a tile under the mouse (the second real test saved camera pictures as
    # "Pin Akarshit to your main screen"):
    r"(un)?pin .+ (to|from) (your |the )?main screen|remove .+ from (your |the )?main screen|"
    r".+ (to|from) your main screen)$",
    re.I,
)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def clean_name(text: str) -> str:
    text = YOU.sub("", text).strip()
    while NAME_SUFFIX.search(text):
        text = NAME_SUFFIX.sub("", text).strip()
    return text


def name_from_text(text: str) -> str:
    """The person's name from a tile's or list entry's visible text (first line that is not an icon or label)."""
    for line in (line.strip() for line in (text or "").splitlines()):
        if line and not NOT_A_NAME.match(line):
            return clean_name(line)
    return ""


@dataclass
class Entry:
    """One person as read from the page."""

    id: str | None
    name: str
    is_self: bool = False
    is_host: bool = False
    attrs: dict = field(default_factory=dict)


@dataclass
class Person:
    id: str
    name: str
    is_host: bool = False
    joined_at: float = 0.0
    missing_since: float | None = None
    sent_id: str = ""  # the id xebit got in the join; the leave carries the same one

    def __post_init__(self):
        self.sent_id = self.sent_id or self.id


class Roster:
    """Pure bookkeeping: entries in, join/leave events out."""

    def __init__(self, bot_name: str, leave_after: float = LEAVE_AFTER):
        self.bot_name = bot_name.strip().casefold()
        self.leave_after = leave_after
        self.present: dict[str, Person] = {}
        self.self_ids: set[str] = set()

    def _is_self(self, entry: Entry) -> bool:
        if entry.is_self or (entry.id and entry.id in self.self_ids):
            return True
        return bool(self.bot_name) and entry.name.strip().casefold() == self.bot_name

    def _resolve(self, entry: Entry) -> str:
        if entry.id:
            # A person first seen without an id (by name) gets their real id from now on.
            for pid, person in list(self.present.items()):
                if pid.startswith("name:") and person.name == entry.name:
                    del self.present[pid]
                    person.id = entry.id
                    self.present[entry.id] = person
            return entry.id
        for pid, person in self.present.items():
            if person.name == entry.name:
                return pid
        return f"name:{entry.name}"

    def update(self, entries: list[Entry], reliable: bool, now: float | None = None) -> list[tuple[str, Person]]:
        now = time.monotonic() if now is None else now
        events: list[tuple[str, Person]] = []
        seen: set[str] = set()
        for entry in entries:
            if self._is_self(entry):
                if entry.id:
                    self.self_ids.add(entry.id)
                continue
            if not entry.name and not entry.id:
                continue
            pid = self._resolve(entry)
            seen.add(pid)
            person = self.present.get(pid)
            if person is None:
                person = Person(pid, entry.name or "Guest", entry.is_host, now)
                self.present[pid] = person
                events.append(("join", person))
            else:
                person.missing_since = None
                if entry.name and entry.name != person.name:
                    person.name = entry.name
        if reliable:
            for pid, person in list(self.present.items()):
                if pid in seen:
                    continue
                if person.missing_since is None:
                    person.missing_since = now
                elif now - person.missing_since >= self.leave_after:
                    del self.present[pid]
                    events.append(("leave", person))
        return events

    def count(self) -> int:
        return len(self.present)


def webhook_body(event: str, bot_id: str, person: Person) -> dict:
    return {
        "event": f"participant_events.{event}",
        "data": {
            "bot": {"id": bot_id},
            "data": {
                "participant": {"id": person.sent_id, "name": person.name, "is_host": person.is_host, "is_bot": False},
                "timestamp": {"absolute": iso_now()},
            },
        },
    }


@dataclass
class Read:
    entries: list[Entry]
    reliable: bool
    source: str
    shown_count: int | None = None


class MeetReader:
    """Reads the People panel (or the tiles) with locators only."""

    def __init__(self, page, bot_name: str):
        self.page = page
        self.bot_name = bot_name

    def people_button(self):
        named = self.page.get_by_role("button", name=PEOPLE_BUTTON)
        if named.count():
            return named.first
        counted = self.page.get_by_role("button", name=COUNT_BUTTON)
        return counted.first if counted.count() else None

    def shown_count(self) -> int | None:
        """The number on the People button (everyone, with the bot)."""
        button = self.people_button()
        if button is None:
            return None
        try:
            text = (button.get_attribute("aria-label", timeout=1_000) or "") + " " + button.inner_text(timeout=1_000)
        except Exception:  # noqa: BLE001
            return None
        match = re.search(r"\d+", text)
        return int(match.group(0)) if match else None

    def panel_list(self):
        lists = self.page.get_by_role("list", name=PANEL_LIST)
        try:
            for i in range(min(lists.count(), 5)):
                candidate = lists.nth(i)
                if candidate.is_visible() and candidate.get_by_role("listitem").count():
                    return candidate
        except Exception:  # noqa: BLE001
            return None
        return None

    def _entry(self, el, source: str) -> Entry:
        pid = el.get_attribute("data-participant-id", timeout=1_000)
        if not pid:
            inner = el.locator("[data-participant-id]")
            if inner.count():
                pid = inner.first.get_attribute("data-participant-id", timeout=1_000)
        label = el.get_attribute("aria-label", timeout=1_000) or ""
        text = el.inner_text(timeout=1_000)
        name = clean_name(label) if label and source == "panel" else ""
        name = name or name_from_text(text)
        is_self = bool(YOU.search(text) or YOU.search(label)) or el.locator(SELF_TILE).count() > 0 \
            or el.get_attribute("data-self-name", timeout=1_000) is not None
        return Entry(pid or None, name, is_self, bool(HOST.search(text)),
                     {"source": source, "data-participant-id": pid, "aria-label": label,
                      "text": " | ".join(t.strip() for t in text.splitlines() if t.strip())[:200]})

    def read(self) -> Read:
        shown = self.shown_count()
        panel = self.panel_list()
        if panel is not None:
            items = panel.get_by_role("listitem")
            entries = [self._entry(items.nth(i), "panel") for i in range(min(items.count(), 100))]
            entries = [e for e in entries if e.name or e.id]
            # Complete only if it has everyone Meet counts (a list still rendering can hold just the bot).
            reliable = len(entries) > 0 and (shown is None or len(entries) == shown)
            return Read(entries, reliable=reliable, source="panel", shown_count=shown)
        tiles = self.page.locator("[data-participant-id]")
        entries = []
        for i in range(min(tiles.count(), 60)):
            try:
                tile = tiles.nth(i)
                if not tile.is_visible():  # e.g. the entries of a closed People panel
                    continue
                entries.append(self._entry(tile, "tile"))
            except Exception:  # noqa: BLE001 - a tile can vanish while we read it
                continue
        ids = {e.id for e in entries if e.id}
        alone = False
        try:
            texts = self.page.get_by_text(ALONE)
            alone = any(texts.nth(i).is_visible() for i in range(min(texts.count(), 3)))
        except Exception:  # noqa: BLE001
            pass
        # Tiles are complete only if Meet's own count says so (or Meet says the bot is alone).
        reliable = (shown is not None and len(ids) == shown and len(ids) > 0) or (alone and len(ids) <= 1)
        return Read(entries, reliable=reliable, source="tiles", shown_count=shown)


class People:
    """Ties the reader, the roster and the webhooks together; ticked from the bot's main loop."""

    def __init__(self, page, cfg, rep, webhooks, open_panel: Callable | None = None, reader: MeetReader | None = None,
                 clock: Callable[[], float] = time.monotonic, wait: Callable[[float], None] | None = None):
        self.page = page
        self.cfg = cfg
        self.rep = rep
        self.webhooks = webhooks
        self.reader = reader or MeetReader(page, cfg.bot_name)
        self.roster = Roster(cfg.bot_name)
        self.open_panel = open_panel
        self.wait = wait or (lambda ms: page.wait_for_timeout(ms))
        self.clock = clock
        self.panel_tries = 0
        self.changes = 0  # joins and leaves so far (others watch it to react to a people change)
        self.last_read = 0.0
        self.logged: set[str] = set()
        self.source = None
        self.started = clock()
        self.urls = {kind: [e.url for e in cfg.webhooks_for(f"participant_events.{kind}")] for kind in ("join", "leave")}
        if not (self.urls["join"] or self.urls["leave"]):
            rep.log("no participant_events webhook: joins and leaves are only logged")

    def _try_open_panel(self) -> None:
        if not self.open_panel or self.panel_tries >= 2:
            return
        self.panel_tries += 1
        button = self.reader.people_button()
        if button is None:
            self.rep.log("People button not found: reading the video tiles instead")
            self.panel_tries = 2
            return
        try:
            label = (button.get_attribute("aria-label", timeout=1_000) or button.inner_text(timeout=1_000)).strip()
            self.open_panel(button)
            self.wait(1_500)
        except Exception as exc:  # noqa: BLE001
            self.rep.log(f"could not click the People button: {type(exc).__name__}")
            return
        if self.reader.panel_list() is not None:
            self.rep.log(f"People panel open (clicked {label!r}); reading the list from it")
        else:
            self.rep.log(f"clicked {label!r} but found no participant list; reading the video tiles instead")
            self.panel_tries = 2  # don't click again: it might close a panel we just can't read

    def tick(self, initial: bool = False) -> None:
        now = self.clock()
        if not initial and now - self.last_read < READ_EVERY:
            return
        self.last_read = now
        try:
            read = self.reader.read()
        except Exception as exc:  # noqa: BLE001 - never let a bad read end the call
            self.rep.log(f"people: read failed ({type(exc).__name__}: {str(exc)[:120]})")
            return
        if read.source != self.source:
            self.rep.log(f"people: reading from the {read.source} ({len(read.entries)} entries, Meet shows "
                         f"{read.shown_count if read.shown_count is not None else '?'})")
            self.source = read.source
        for entry in read.entries:
            key = entry.id or f"name:{entry.name}"
            if key not in self.logged:
                self.logged.add(key)
                self.rep.event("entry_seen", self_=entry.is_self or self.roster._is_self(entry), **entry.attrs)
        events = self.roster.update(read.entries, read.reliable, now)
        self.changes += len(events)
        for kind, person in events:
            self.rep.log(f"people: {kind} {person.name!r} ({person.id}) - {self.roster.count()} other(s) in the call")
            self.rep.event(f"participant_{kind}", id=person.id, name=person.name, source=read.source)
            for url in self.urls[kind]:
                self.webhooks.send(url, webhook_body(kind, self.cfg.bot_id, person))
        if events:
            self.rep.snapshot(self.page, f"people-{int(time.time())}")
        # The panel is opened a little later, so the sound and the camera page start first.
        if not initial and read.source == "tiles" and self.panel_tries < 2 and now - self.started > 3:
            self._try_open_panel()

    def count_others(self) -> int:
        return self.roster.count()

    def close(self) -> None:
        pass
