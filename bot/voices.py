"""Each person's sound (docs/PLAN.md task R3): Meet's 3 sound slots, given to the person speaking.

Meet plays the loudest speakers through 3 `<audio>` elements ("slots"); which person is in which slot
changes. The bot hears each slot separately (bot/taps.py) and knows who talked when (bot/speakers.py: the
captions, and the speaking indicator when it can be read). So, moment by moment (200 ms pieces):

1. Wait DELAY seconds first: captions come a little after the words, so who talked at a moment is only
   known a bit later.
2. The people talking around that moment are those whose captions changed between BEFORE seconds before
   and AFTER seconds after it (or whom the indicator showed speaking).
3. A slot that has sound keeps the person it had, while that person is still talking.
4. A slot with sound and no person goes to a talking person no other sounding slot has: first one whose
   new caption block starts about then (a new speaker), then the one whose caption grows the most. When
   several such slots start together, the one with the most sound goes first. A slot that goes quiet for
   0.3 s forgets its person (the next voice in it may be someone else).
5. Sound nobody can be given is "unknown" (`audio/unknown.wav`; not sent to xebit).

Pure logic: `feed()` slot pieces in, `ready()` gives (person key or None, piece) out. Used for the files
(`--save-media`) and for the live per-person stream (`--per-person-audio`).
"""
from __future__ import annotations

import time
from typing import Callable

ACTIVE_DB = -50.0
DELAY = 2.5
BEFORE = 0.2
AFTER = 1.5   # captions come after the words: a person talks at t if their caption changes up to 1.5 s later
GROUP = 0.1   # pieces of different slots that start within 100 ms are "the same moment"
RELEASE_AFTER = 0.3  # a slot quiet this long forgets its person (the next sound may be someone else)


class VoiceSplitter:
    def __init__(self, speaking_between: Callable[[float, float], dict[str, int]], delay: float = DELAY,
                 started_between: Callable[[float, float], set[str]] | None = None):
        self.speaking_between = speaking_between
        self.started_between = started_between or (lambda a, b: set())
        self.delay = delay
        self.waiting: list[dict] = []
        self.owner: dict[int, str] = {}
        self.last_loud: dict[int, float] = {}  # slot -> time of its last loud piece
        self.named = 0
        self.unknown = 0

    def feed(self, chunks: list[dict]) -> None:
        """Slot pieces from the tap: {"slot", "t" (ms, wall clock), "db", "pcm"}."""
        self.waiting.extend(chunks)

    def ready(self, now: float | None = None, flush: bool = False) -> list[tuple[str | None, dict]]:
        """The pieces old enough to name: (person key or None, piece). Quiet pieces are left out."""
        now = time.time() if now is None else now
        self.waiting.sort(key=lambda c: c.get("t", 0))
        cut = len(self.waiting) if flush else next(
            (i for i, c in enumerate(self.waiting) if c.get("t", 0) / 1000 > now - self.delay), len(self.waiting))
        todo, self.waiting = self.waiting[:cut], self.waiting[cut:]
        out: list[tuple[str | None, dict]] = []
        i = 0
        while i < len(todo):
            j = i + 1
            while j < len(todo) and todo[j].get("t", 0) - todo[i].get("t", 0) < GROUP * 1000:
                j += 1
            out += self._name_group(todo[i:j])
            i = j
        return out

    def _name_group(self, group: list[dict]) -> list[tuple[str | None, dict]]:
        t = group[0].get("t", 0) / 1000
        loud = [c for c in group if c.get("db", -120) > ACTIVE_DB]
        for slot in list(self.owner):  # a pause: the slot's next sound may be someone else
            if t - self.last_loud.get(slot, -1e9) > RELEASE_AFTER:
                del self.owner[slot]
        for c in loud:
            self.last_loud[c["slot"]] = t
        if not loud:
            return []
        talking = self.speaking_between(t - BEFORE, t + AFTER)
        for slot in list(self.owner):  # a slot keeps its person while that person still talks
            if self.owner[slot] not in talking:
                del self.owner[slot]
        busy = {self.owner[c["slot"]] for c in loud if c["slot"] in self.owner}
        # Who starts a new caption block about now (a new speaker), and whose caption grows from this moment on
        # (a new speaker's does; one who just stopped only gets a last correction).
        fresh = self.started_between(t - BEFORE, t + AFTER)
        ahead = self.speaking_between(t, t + AFTER)

        def score(k):
            return (k in fresh, ahead.get(k, 0), talking[k])

        free = sorted((k for k in talking if k not in busy), key=score, reverse=True)
        unowned = sorted((c for c in loud if c["slot"] not in self.owner), key=lambda c: -c.get("db", -120))
        for n, c in enumerate(unowned):
            if not free:
                break
            left = len(unowned) - n
            if len(free) > left and score(free[left - 1]) == score(free[left]):
                break  # a tie between people who could own it: don't guess
            self.owner[c["slot"]] = free.pop(0)
        out = []
        for c in loud:
            who = self.owner.get(c["slot"])
            if who is None:
                self.unknown += 1
            else:
                self.named += 1
            out.append((who, c))
        return out
