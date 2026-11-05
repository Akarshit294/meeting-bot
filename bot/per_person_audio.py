"""EXPERIMENTAL (`--per-person-audio`, off by default): one sound stream per person (docs/PLAN.md tasks 12, R3).

Meet does not send one stream per person: it plays the loudest speakers through 3 `<audio>` elements
("slots"), and which person is in which slot changes. So, after admission only:

1. The bot hears each slot separately, from an ISOLATED world in Meet's tab (bot/taps.py): nothing runs in
   Meet's page world.
2. Each slot's sound is given, moment by moment, to the person talking at that moment (bot/voices.py:
   Meet's captions say who talked when, and the speaking indicator when it can be read). The same naming
   makes `audio/<name>.wav` with `--save-media`.
3. Each person's frames go out like the mixed stream's (docs/SPEC.md 3.2), with that person's id and name
   (the same ids as the join/leave webhooks), about 2.5 s late (captions come after the words) but with
   the time the sound was heard. Sound nobody can be given is not sent.

Safety net: if 10 s of sound can't be given to anyone, or no sound comes from Meet's slots for 15 s, the
bot goes back to the mixed stream for the rest of the call. If the slots can't be tapped at all, it starts
with the mixed stream (see audio.start_for).
"""
from __future__ import annotations

import base64
import math
import struct
import time

from audio import Socket, frame

UNNAMED_LIMIT = 50  # 10 s of sound


def pcm_db(pcm: bytes) -> float:
    n = len(pcm) // 2
    if not n:
        return -120.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return 10 * math.log10(sum(s * s for s in samples) / n / 1073741824 + 1e-12)


class PerPersonAudio:
    def __init__(self, cfg, url: str, rep, people, connect=None):
        self.cfg = cfg
        self.rep = rep
        self.people = people
        self.socket = Socket(url, rep.log, "audio", **({"connect": connect} if connect else {}))
        self.sent: dict[str, int] = {}
        self.unnamed = 0
        self.started = time.monotonic()
        self.chunks_seen = 0
        self.fallback = None
        self.last_note = time.monotonic()
        rep.log("per-person audio (EXPERIMENTAL): Meet's <audio> slots, tapped from an isolated world; each slot is "
                "given to whoever is talking (captions, speaking indicator)")

    def start(self) -> "PerPersonAudio":
        return self

    def feed(self, chunks: list[dict]) -> None:
        """Slot sound from the tap (bot/taps.py): only counted here (the naming is bot/voices.py)."""
        self.chunks_seen += len(chunks)

    def deliver(self, named: list[tuple[str | None, dict]]) -> None:
        """Pieces named by the voice splitter: (participant id or None, piece)."""
        if self.fallback is not None:
            return
        by_id = {p.sent_id: p for p in (self.people.roster.present.values() if self.people else [])}
        for key, chunk in named:
            person = by_id.get(key) if key else None
            if person is None:
                self.unnamed += 1
                continue
            participant = {"id": person.sent_id, "name": person.name, "is_bot": False}
            if self.socket.send(frame(self.cfg.bot_id, base64.b64decode(chunk["pcm"]), chunk["t"] / 1000, participant)):
                self.sent[person.name] = self.sent.get(person.name, 0) + 1

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
        if not self.chunks_seen and time.monotonic() - self.started > 15:
            return self._fall_back("no sound came from Meet's <audio> elements in 15 s")
        if not self.sent and self.unnamed >= UNNAMED_LIMIT:
            return self._fall_back("10 s of sound but nobody could be named (no captions, no speaking indicator)")
        if time.monotonic() - self.last_note > 60:
            self.last_note = time.monotonic()
            self.rep.log("per-person audio: " + self.summary())

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
