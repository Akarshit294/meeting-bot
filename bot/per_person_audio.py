"""EXPERIMENTAL (`--per-person-audio`, off by default): one sound stream per person (docs/PLAN.md task 12).

Meet does not send one stream per person: it plays the loudest speakers through 3 `<audio>` elements
("slots"), and which person is in which slot changes. So, after admission only:

1. The bot hears each slot from an ISOLATED world in Meet's tab (bot/taps.py, bot/isolated.py): nothing
   runs in Meet's page world. 200 ms of PCM per slot comes back through a DevTools binding.
2. Each active slot is given a name by the speaking indicator (SpeakerTimeline): a slot that is the only
   active one while exactly one person is shown speaking belongs to that person; it keeps that person
   while it stays active and that person still speaks; a new active slot goes to the one speaking person
   no other active slot has.
3. Each person's frames go out like the mixed stream's (docs/SPEC.md 3.2), with that person's id and name
   (the same ids as the join/leave webhooks). Sound from a slot that can't be named is not sent.

If any of this fails to start, the bot falls back to the mixed stream (see audio.start_for).
"""
from __future__ import annotations

import base64
import math
import struct
import time

from audio import Socket, frame

ACTIVE_DB = -50.0
WAIT_CHUNKS = 5  # 1 s of a slot's sound may wait for its name


def pcm_db(pcm: bytes) -> float:
    n = len(pcm) // 2
    if not n:
        return -120.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return 10 * math.log10(sum(s * s for s in samples) / n / 1073741824 + 1e-12)


class SlotNamer:
    """Pure: which person each active slot belongs to, from the slots that have sound and the people shown
    speaking."""

    def __init__(self):
        self.owner: dict[int, str] = {}

    def update(self, active: set[int], speaking: set[str]) -> dict[int, str]:
        for slot in list(self.owner):
            if slot not in active or self.owner[slot] not in speaking:
                del self.owner[slot]
        free_people = speaking - set(self.owner.values())
        unnamed = [slot for slot in sorted(active) if slot not in self.owner]
        if len(unnamed) == 1 and len(free_people) == 1:
            self.owner[unnamed[0]] = next(iter(free_people))
        return dict(self.owner)


class PerPersonAudio:
    def __init__(self, cfg, url: str, rep, people, connect=None):
        self.cfg = cfg
        self.rep = rep
        self.people = people
        self.speakers = None  # set by extras.start, once the speaker timeline runs
        self.socket = Socket(url, rep.log, "audio", **({"connect": connect} if connect else {}))
        self.namer = SlotNamer()
        self.pending: list[dict] = []
        self.waiting: dict[int, list[dict]] = {}  # per slot: sound not named yet (the indicator lags the sound)
        self.sent: dict[str, int] = {}
        self.unnamed = 0
        self.started = time.monotonic()
        self.chunks_seen = 0
        self.fallback = None
        self.last_note = time.monotonic()
        rep.log("per-person audio (EXPERIMENTAL): Meet's <audio> slots, tapped from an isolated world; each slot is "
                "named by the speaking indicator")

    def feed(self, chunks: list[dict]) -> None:
        """Slot sound from the tap (bot/taps.py), handed over every tick."""
        if self.fallback is None:
            self.pending.extend(chunks)

    def start(self) -> "PerPersonAudio":
        return self

    def _fall_back(self, why: str) -> None:
        """The agent must hear the call: go back to the mixed stream (v1's default)."""
        from audio import AudioStreamer

        self.rep.log(f"per-person audio: {why}; switching to the mixed stream ('meeting-mix') for the rest of the call")
        self.rep.event("per_person_audio", action="fallback_to_mix", why=why)
        self.socket.close()
        self.fallback = AudioStreamer(self.socket.url, self.cfg.bot_id, self.rep.log).start()

    def tick(self) -> None:
        if self.fallback is not None:
            return
        if not self.chunks_seen and not self.pending and time.monotonic() - self.started > 15:
            return self._fall_back("no sound came from Meet's <audio> elements in 15 s")
        if not self.sent and self.unnamed >= 50:
            return self._fall_back("10 s of sound but nobody could be named (the speaking indicator is not read)")
        if not self.pending:
            return
        chunks, self.pending = self.pending, []
        self.chunks_seen += len(chunks)
        active = {c["slot"] for c in chunks if c.get("db", -120) > ACTIVE_DB}
        speaking = self.speakers.current() if self.speakers else set()
        owners = self.namer.update(active, speaking)
        present = self.people.roster.present if self.people else {}
        for chunk in chunks:
            if chunk.get("db", -120) <= ACTIVE_DB:
                continue
            slot = chunk["slot"]
            pid = owners.get(slot)
            person = present.get(pid) if pid else None
            if person is None:
                # Keep up to 1 s: once the slot is named, this start of the sentence goes to that person.
                queue = self.waiting.setdefault(slot, [])
                queue.append(chunk)
                if len(queue) > WAIT_CHUNKS:
                    queue.pop(0)
                    self.unnamed += 1
                continue
            for early in self.waiting.pop(slot, []):
                self._send(person, early)
            self._send(person, chunk)
        for slot in list(self.waiting):
            if slot not in active:  # the slot went quiet before anyone could be named: drop it
                self.unnamed += len(self.waiting.pop(slot))
        if time.monotonic() - self.last_note > 60:
            self.last_note = time.monotonic()
            self.rep.log("per-person audio: " + self.summary())

    def _send(self, person, chunk: dict) -> None:
        participant = {"id": person.sent_id, "name": person.name, "is_bot": False}
        if self.socket.send(frame(self.cfg.bot_id, base64.b64decode(chunk["pcm"]), chunk["t"] / 1000, participant)):
            self.sent[person.name] = self.sent.get(person.name, 0) + 1

    def summary(self) -> str:
        sent = ", ".join(f"{k} {v}" for k, v in sorted(self.sent.items())) or "none"
        return f"frames sent {sent}; {self.unnamed} frames with sound could not be named"

    def stop(self) -> None:
        if self.fallback is not None:
            self.fallback.stop()
        self.socket.close()
        self.rep.log("per-person audio: stopped; " + self.summary())


def start(args, cfg, url: str, rep, people, taps=None) -> PerPersonAudio:
    if taps is None or not taps.audio:
        raise RuntimeError("Meet's <audio> slots could not be tapped")
    return PerPersonAudio(cfg, url, rep, people)
