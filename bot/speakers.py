"""Who spoke when (docs/PLAN.md task 12, step 1): `speakers.jsonl` in the run folder.

About 5 times a second, each visible tile of a person in the roster is checked for Meet's speaking
indicator, through locators only. Lines: {"t", "id", "name", "event": "started" | "stopped"}.

What the indicator looks like on the real page is not known yet (no real snapshot catches someone
talking). So this reads the signals that are plausible and cheap, and logs what it sees:
- an attribute on the tile or its People-panel entry: `data-speaking="true"`, or an `aria-label` that
  says "speaking";
- a class name on the tile that contains one of SPEAKING_CLASSES (env, default "speaking");
- for the morning test: every 10 s the class names of each tile's direct children go to `timeline.jsonl`
  as `tile_classes` when they change, so the real indicator can be spotted by comparing them with when
  someone talked.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone

EVERY = 0.2
DIAGNOSE_EVERY = 10.0
SPEAKING_WORD = re.compile(r"\bspeaking\b|\bis talking\b", re.I)


def classes_hint() -> list[str]:
    return [c.strip() for c in os.environ.get("SPEAKING_CLASSES", "speaking").split(",") if c.strip()]


class SpeakerTimeline:
    def __init__(self, page, people, rep):
        self.page = page
        self.people = people
        self.rep = rep
        self.path = rep.run_dir / "speakers.jsonl"
        self.speaking: dict[str, float] = {}
        self.last_tick = 0.0
        self.last_diagnose = 0.0
        self.tile_classes: dict[str, str] = {}
        self.hints = classes_hint()
        self.changes = 0
        self.listeners = []
        rep.log("speakers: writing who speaks when to speakers.jsonl")

    def selector(self) -> str:
        """One query for every element that says "this person is speaking" (tiles and panel entries)."""
        parts = [f'[data-participant-id][class*="{h}"]' for h in self.hints if re.fullmatch(r"[\w-]+", h)]
        parts += ['[data-participant-id][data-speaking="true"]', '[data-participant-id][aria-label*="speaking" i]']
        return ", ".join(parts)

    def _diagnose(self, tile, pid: str) -> None:
        children = tile.locator(":scope > *")
        classes = []
        for i in range(min(children.count(), 12)):
            classes.append(children.nth(i).get_attribute("class", timeout=300) or "")
        signature = (tile.get_attribute("class", timeout=300) or "") + " > " + " | ".join(classes)
        if self.tile_classes.get(pid) != signature:
            self.tile_classes[pid] = signature
            self.rep.event("tile_classes", id=pid, classes=signature[:600])

    def write(self, pid: str, name: str, event: str) -> None:
        line = {"t": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "id": pid, "name": name, "event": event}
        with open(self.path, "a") as f:
            f.write(json.dumps(line) + "\n")
        self.changes += 1
        for listener in self.listeners:
            listener(pid, event == "started")

    def tick(self) -> None:
        now = time.monotonic()
        if now - self.last_tick < EVERY:
            return
        self.last_tick = now
        present = self.people.roster.present if self.people else {}
        hits = self.page.locator(self.selector())
        speaking_now: set[str] = set()
        for i in range(min(hits.count(), 20)):
            try:
                pid = hits.nth(i).get_attribute("data-participant-id", timeout=300)
            except Exception:  # noqa: BLE001 - it can vanish while we read it
                continue
            if pid and pid in present:
                speaking_now.add(pid)
        for pid in speaking_now - set(self.speaking):
            self.speaking[pid] = now
            self.write(pid, present[pid].name, "started")
        for pid in set(self.speaking) - speaking_now:
            del self.speaking[pid]
            self.write(pid, present[pid].name if pid in present else pid, "stopped")
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

    def current(self) -> set[str]:
        return set(self.speaking)

    def stop(self) -> None:
        for pid in list(self.speaking):
            self.write(pid, pid, "stopped")
        self.speaking.clear()
        self.rep.log(f"speakers: stopped after {self.changes} changes")
