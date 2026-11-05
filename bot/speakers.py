"""Who spoke when: `speakers.jsonl` in the run folder (docs/PLAN.md task 12 step 1, and R3).

Two sources, merged:
1. Meet's captions (bot/captions.py, task R3): a person starts speaking when their caption block starts or
   grows, and stops about 1 s after it stops changing. This is the main source: it works whatever Meet's
   class names are.
2. The speaking indicator on the tiles or People-panel entries, read about 5 times a second through
   locators only: `data-speaking="true"`, an `aria-label` that says "speaking", or a class name containing
   one of SPEAKING_CLASSES (env, default "speaking"; an entry starting with "." means "a descendant with
   this class", e.g. ".kssMZb"). Real Meet's indicator is not known for sure yet (docs/per-person-audio.md),
   so this source may stay silent there. Every 10 s each tile's class names go to `timeline.jsonl`
   (`tile_classes`) to help find it.

Lines: {"t", "id", "name", "event": "started" | "stopped", "source": "captions" | "indicator" | "both"}.
`id` is the participant id of the join webhook (null for a caption name that matches nobody in the roster).
The voice splitter (bot/voices.py) asks `speaking_between()` who talked around a given moment.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone

EVERY = 0.2
DIAGNOSE_EVERY = 10.0
HOLD = 1.0  # a caption that stopped changing: the person stopped about 1 s later
KEEP_HISTORY = 120.0
SPEAKING_WORD = re.compile(r"\bspeaking\b|\bis talking\b", re.I)


def classes_hint() -> list[str]:
    return [c.strip() for c in os.environ.get("SPEAKING_CLASSES", "speaking").split(",") if c.strip()]


class SpeakerTimeline:
    def __init__(self, page, people, rep):
        self.page = page
        self.people = people
        self.rep = rep
        self.path = rep.run_dir / "speakers.jsonl"
        self.speaking: dict[str, str] = {}  # key -> source
        self.names: dict[str, str] = {}
        self.last_tick = 0.0
        self.last_diagnose = 0.0
        self.tile_classes: dict[str, str] = {}
        self.hints = classes_hint()
        self.changes = 0
        self.caption_last: dict[str, float] = {}  # key -> monotonic time of the last caption change
        self.caption_wall: dict[str, list[float]] = {}  # key -> wall-clock times of caption changes
        self.block_starts: dict[str, list[float]] = {}  # key -> wall-clock times their new caption blocks began
        self.indicator_spans: dict[str, list[list]] = {}  # key -> [[start, end or None], ...] (wall clock)
        self.indicator_now: set[str] = set()
        rep.log("speakers: writing who speaks when to speakers.jsonl (from the captions and the speaking indicator)")

    # -- sources --

    def heard(self, pid: str | None, name: str, wall: float, new_block: bool = False) -> None:
        """A caption block of this person started (`new_block`) or changed (bot/captions.py)."""
        key = pid or f"name:{name}"
        self.names[key] = name
        self.caption_last[key] = time.monotonic()
        for store, keep in ((self.caption_wall, True), (self.block_starts, new_block)):
            if keep:
                times = store.setdefault(key, [])
                times.append(wall)
                while times and times[0] < wall - KEEP_HISTORY:
                    times.pop(0)

    def selector(self) -> str:
        """One query for every element that says "this person is speaking" (tiles and panel entries)."""
        parts = []
        for hint in self.hints:
            if re.fullmatch(r"\.[\w-]+", hint):
                parts.append(f"[data-participant-id]:has({hint})")
            elif re.fullmatch(r"[\w-]+", hint):
                parts.append(f'[data-participant-id][class*="{hint}"]')
        parts += ['[data-participant-id][data-speaking="true"]', '[data-participant-id][aria-label*="speaking" i]']
        return ", ".join(parts)

    def _read_indicator(self, present: dict) -> set[str]:
        hits = self.page.locator(self.selector())
        found: set[str] = set()
        for i in range(min(hits.count(), 20)):
            try:
                pid = hits.nth(i).get_attribute("data-participant-id", timeout=300)
            except Exception:  # noqa: BLE001 - it can vanish while we read it
                continue
            if pid and pid in present:
                found.add(present[pid].sent_id)
                self.names[present[pid].sent_id] = present[pid].name
        return found

    def _diagnose(self, tile, pid: str) -> None:
        children = tile.locator(":scope > *")
        classes = []
        for i in range(min(children.count(), 12)):
            classes.append(children.nth(i).get_attribute("class", timeout=300) or "")
        signature = (tile.get_attribute("class", timeout=300) or "") + " > " + " | ".join(classes)
        if self.tile_classes.get(pid) != signature:
            self.tile_classes[pid] = signature
            self.rep.event("tile_classes", id=pid, classes=signature[:600])

    # -- the timeline --

    def write(self, key: str, event: str, source: str) -> None:
        line = {"t": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "id": None if key.startswith("name:") else key, "name": self.names.get(key, key), "event": event,
                "source": source}
        with open(self.path, "a") as f:
            f.write(json.dumps(line) + "\n")
        self.changes += 1

    def tick(self) -> None:
        now = time.monotonic()
        if now - self.last_tick < EVERY:
            return
        self.last_tick = now
        present = self.people.roster.present if self.people else {}
        indicator = self._read_indicator(present)
        wall = time.time()
        for key in indicator - self.indicator_now:
            self.indicator_spans.setdefault(key, []).append([wall, None])
        for key in self.indicator_now - indicator:
            spans = self.indicator_spans.get(key)
            if spans and spans[-1][1] is None:
                spans[-1][1] = wall
        self.indicator_now = indicator
        by_captions = {key for key, t in self.caption_last.items() if now - t < HOLD}
        speaking_now = indicator | by_captions
        for key in sorted(speaking_now):
            source = "both" if key in indicator and key in by_captions else "indicator" if key in indicator else "captions"
            if key not in self.speaking:
                self.write(key, "started", source)
            self.speaking[key] = source
        for key in sorted(set(self.speaking) - speaking_now):
            self.write(key, "stopped", self.speaking.pop(key))
        if now - self.last_diagnose > DIAGNOSE_EVERY:
            self.last_diagnose = now
            tiles = self.page.locator("[data-participant-id]")
            for i in range(min(tiles.count(), 20)):
                try:
                    tile = tiles.nth(i)
                    pid = tile.get_attribute("data-participant-id", timeout=300)
                    if pid in present and tile.is_visible():
                        self._diagnose(tile, pid)
                except Exception:  # noqa: BLE001
                    continue

    def speaking_between(self, start: float, end: float) -> dict[str, int]:
        """Who talked between two wall-clock times: key -> how many caption changes (the indicator counts 1)."""
        out: dict[str, int] = {}
        for key, times in self.caption_wall.items():
            n = sum(1 for t in times if start <= t <= end)
            if n:
                out[key] = n
        for key, spans in self.indicator_spans.items():
            if any(s <= end and (e is None or e >= start) for s, e in spans):
                out[key] = out.get(key, 0) + 1
        return out

    def started_between(self, start: float, end: float) -> set[str]:
        """Who began a new caption block between two wall-clock times (a new speaker, usually)."""
        return {key for key, times in self.block_starts.items() if any(start <= t <= end for t in times)}

    def current(self) -> set[str]:
        return set(self.speaking)

    def stop(self) -> None:
        for key in list(self.speaking):
            self.write(key, "stopped", self.speaking.pop(key))
        self.rep.log(f"speakers: stopped after {self.changes} changes")
