"""Meet's captions (docs/PLAN.md task R3): who says what, read from the bot's own screen.

1. After admission (after the microphone and the camera page), the bot turns Meet's captions on with the
   human mouse. Two versions of the button exist (2026-10/11):
   - A: "Turn on captions" (icon `closed_caption_off`); when on it reads "Turn off captions".
   - B: "Captions and translation" (icon `translate_cc`, `aria-pressed`), which opens a small menu with a
     "Turn on captions" switch.
   Captions show only on the bot's screen; nobody else in the call sees them. If the button can't be found,
   the bot presses `c` (Meet's shortcut), and if that fails too it logs it and carries on.
2. The captions area is `role="region"` named "Captions". Each speaker turn is a block: the speaker's
   avatar (`img`, its `alt` is the name), the name, then the text, which Meet rewrites in place while the
   person talks. The bot's own voice is captioned as "You" and is skipped.
   It is read through locators only (Playwright's isolated world): one `inner_text` of the area and the
   avatars' `alt` texts. A line that is a speaker's name starts a block; the lines after it are the text.
3. `captions.jsonl`: one line each time a block's text changes ({"t", "name", "id", "text",
   "changing": true}), and one when it has not changed for 1.5 s ({"changing": false}).
   Each change also tells the speaker timeline (bot/speakers.py) that this person is talking.
"""
from __future__ import annotations

import difflib
import json
import re
import time
from datetime import datetime, timezone
from typing import Callable

READ_EVERY = 0.3
STABLE_AFTER = 1.5
KEEP_BLOCKS = 8  # only the latest blocks matter (Meet keeps a scrollable history)

TURN_ON = re.compile(r"^turn on (live )?captions", re.I)
TURN_OFF = re.compile(r"^turn off (live )?captions", re.I)
CC_MENU = re.compile(r"^captions and translation", re.I)
REGION = re.compile(r"^captions$", re.I)
# Lines inside the captions area that are not captions: icon names and Meet's buttons.
NOT_CAPTION = re.compile(r"^([a-z0-9]+(_[a-z0-9]+)+|jump to (the )?(bottom|most recent captions?)|arrow_downward|"
                         r"settings|caption settings|english|close|more_vert)$", re.I)
YOU = {"you"}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse(text: str, names: set[str]) -> list[tuple[str, str]]:
    """The captions area's text as (speaker, text) blocks. A line that is a known speaker name starts a block."""
    folded = {n.casefold(): n for n in names if n}
    blocks: list[list] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or NOT_CAPTION.match(line):
            continue
        if line.casefold() in folded:
            blocks.append([folded[line.casefold()], []])
        elif blocks:
            blocks[-1][1].append(line)
    return [(name, " ".join(lines)) for name, lines in blocks]


def similar(a: str, b: str) -> bool:
    """The same caption block, maybe rewritten (Meet revises words while the person talks)."""
    if not a or not b:
        return True
    if a.startswith(b[:15]) or b.startswith(a[:15]):
        return True
    return difflib.SequenceMatcher(None, a[:200], b[:200]).ratio() > 0.5


def align(previous: list[dict], current: list[tuple[str, str]]) -> int | None:
    """How many old blocks dropped off the top: current[i] is previous[i + d]. None if nothing lines up."""
    for d in range(len(previous) + 1):
        overlap = list(zip(previous[d:], current))
        if all(p["name"] == c[0] and similar(p["text"], c[1]) for p, c in overlap):
            return d
    return None


class CaptionReader:
    def __init__(self, page, people, rep, bot_name: str,
                 on_speech: Callable[[str | None, str, float, bool], None] | None = None):
        self.page = page
        self.people = people
        self.rep = rep
        self.bot_names = {bot_name.strip().casefold()} | YOU
        self.on_speech = on_speech
        self.path = rep.run_dir / "captions.jsonl"
        self.blocks: list[dict] = []  # {"name", "text", "changed", "final"}
        self.last_read = 0.0
        self.lines = 0
        self.failures = 0
        self.seen_region = False
        self.started = False
        self.logged_empty = False

    # -- reading --

    def region(self):
        return self.page.get_by_role("region", name=REGION).first

    def names(self) -> set[str]:
        names = set(YOU) | {"You"}
        present = self.people.roster.present if self.people else {}
        names |= {p.name for p in present.values()}
        try:
            avatars = self.region().locator("img")
            for i in range(min(avatars.count(), 20)):
                alt = avatars.nth(i).get_attribute("alt", timeout=300)
                if alt and alt.strip():
                    names.add(alt.strip())
        except Exception:  # noqa: BLE001 - an avatar can vanish while we read it
            pass
        return names

    def read(self) -> list[tuple[str, str]] | None:
        region = self.region()
        if not region.count():
            return None
        if not self.seen_region:
            self.seen_region = True
            self.rep.log("captions: the captions area is on screen; reading it")
        return parse(region.inner_text(timeout=1_000), self.names())[-KEEP_BLOCKS:]

    def id_of(self, name: str) -> str | None:
        present = self.people.roster.present if self.people else {}
        for pid, person in present.items():
            if person.name.casefold() == name.casefold():
                return person.sent_id
        return None

    def write(self, block: dict, changing: bool) -> None:
        line = {"t": iso_now(), "name": block["name"], "id": self.id_of(block["name"]), "text": block["text"],
                "changing": changing}
        with open(self.path, "a") as f:
            f.write(json.dumps(line) + "\n")
        self.lines += 1

    def tick(self) -> None:
        now = time.monotonic()
        if now - self.last_read < READ_EVERY:
            return
        self.last_read = now
        try:
            current = self.read()
            self.failures = 0
        except Exception as exc:  # noqa: BLE001 - captions must never touch the call
            self.failures += 1
            if self.failures in (1, 20):
                self.rep.log(f"captions: read failed ({type(exc).__name__}: {str(exc)[:120]})")
            return
        if current is None:
            return
        if not self.started:
            # The first read: what is already there was said before the bot could read it. Keep it as said,
            # without telling the speaker timeline (the time would be wrong); only later changes count.
            self.started = True
            for name, text in current:
                block = {"name": name, "text": text, "changed": now, "final": True}
                if text and name.casefold() not in self.bot_names:
                    self.write(block, False)
                self.blocks.append(block)
            return
        offset = align(self.blocks, current)
        if offset is None:  # nothing lines up (the area was redrawn): start over from what is shown now
            for block in self.blocks:
                if not block["final"]:
                    self.write(block, False)
            offset, self.blocks = len(self.blocks), []
        old = self.blocks[offset:]
        for block in self.blocks[:offset]:  # dropped off the top
            if not block["final"]:
                self.write(block, False)
        wall = time.time()
        blocks = []
        for i, (name, text) in enumerate(current):
            block = old[i] if i < len(old) else {"name": name, "text": "", "changed": now, "final": False}
            if text != block["text"] or (i >= len(old) and not text):
                new_block = not block["text"]
                block.update(text=text, changed=now, final=False)
                if name.casefold() not in self.bot_names:
                    self.write(block, True)
                    if self.on_speech:
                        self.on_speech(self.id_of(name), name, wall, new_block)
            blocks.append(block)
        self.blocks = blocks
        for block in self.blocks:
            if not block["final"] and now - block["changed"] >= STABLE_AFTER:
                block["final"] = True
                if block["name"].casefold() not in self.bot_names and block["text"]:
                    self.write(block, False)

    def stop(self) -> None:
        for block in self.blocks:
            if not block["final"] and block["text"] and block["name"].casefold() not in self.bot_names:
                self.write(block, False)
        self.rep.log(f"captions: stopped; {self.lines} lines in captions.jsonl")


# -- turning captions on (with the human mouse) --

def _visible(locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:  # noqa: BLE001
        return False


def captions_on(page) -> bool:
    if _visible(page.get_by_role("button", name=TURN_OFF)):
        return True
    try:
        menu = page.get_by_role("button", name=CC_MENU)
        if menu.count() and menu.first.get_attribute("aria-pressed", timeout=500) == "true":
            return True
    except Exception:  # noqa: BLE001
        pass
    return _visible(page.get_by_role("region", name=REGION))


def wait_on(page, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if captions_on(page):
            return True
        page.wait_for_timeout(300)
    return captions_on(page)


def turn_on(page, click: Callable, press_key: Callable[[str], None] | None, log: Callable[[str], None]) -> bool:
    """Turn Meet's captions on. Returns whether they are on. Never raises."""
    try:
        if captions_on(page):
            log("captions: already on")
            return True
        direct = page.get_by_role("button", name=TURN_ON)
        if _visible(direct):  # version A: one button
            click(direct.first)
            if wait_on(page, 4):
                log("captions: turned on (the 'Turn on captions' button)")
                return True
        menu = page.get_by_role("button", name=CC_MENU)
        if _visible(menu):  # version B: a menu with a "Turn on captions" switch
            click(menu.first)
            page.wait_for_timeout(800)
            switch = page.get_by_role("switch", name=TURN_ON)
            for candidate in (switch, page.get_by_role("menuitemcheckbox", name=TURN_ON),
                              page.get_by_role("menuitem", name=TURN_ON), page.get_by_role("button", name=TURN_ON),
                              page.get_by_role("checkbox", name=TURN_ON)):
                if _visible(candidate):
                    click(candidate.first)
                    break
            on = wait_on(page, 4)
            if press_key:
                press_key("Escape")  # close the little menu
            if on:
                log("captions: turned on (the 'Captions and translation' menu)")
                return True
        if press_key:  # Meet's own shortcut, as a person might use it
            press_key("c")
            if wait_on(page, 4):
                log("captions: turned on (the 'c' key)")
                return True
        log("warning: captions could not be turned on (no captions button found); who spoke when then comes "
            "only from the speaking indicator")
    except Exception as exc:  # noqa: BLE001 - the call must not suffer
        log(f"warning: turning captions on failed ({type(exc).__name__}: {str(exc)[:120]})")
    return False
