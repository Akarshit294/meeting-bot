"""EXPERIMENTAL (`--per-person-audio`, off by default): one sound stream per person (docs/PLAN.md task 12).

Meet does not send one stream per person: it plays the loudest speakers through 3 `<audio>` elements
("slots"), and which person is in which slot changes. So, after admission only:

1. The bot creates an ISOLATED world in Meet's tab (DevTools `Page.createIsolatedWorld`). Code there
   shares the page's elements but not its JavaScript: Meet's scripts can't see it, and nothing runs in
   Meet's page world. It hooks each `<audio>` element's stream into its own 16 kHz AudioContext and hands
   200 ms of PCM per slot to the bot through a DevTools binding.
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
import json
import math
import struct
import time

from audio import Socket, frame

WORLD = "xbot-audio"
BINDING = "xbotAudio"
ACTIVE_DB = -50.0
WAIT_CHUNKS = 5  # 1 s of a slot's sound may wait for its name

HOOK_JS = r"""
(() => {
  if (window.__xbotAudio) return "already";
  const S = window.__xbotAudio = { ctx: new AudioContext({ sampleRate: 16000 }), hooked: new Map(), next: 0 };
  const CHUNK = 3200;
  function hook(el) {
    const stream = el.srcObject;
    if (!stream || !(stream instanceof MediaStream) || !stream.getAudioTracks().length) return;
    const old = S.hooked.get(el);
    if (old && old.stream === stream) return;
    if (old) { try { old.src.disconnect(); old.proc.disconnect(); } catch (e) {} }
    const slot = old ? old.slot : S.next++;
    const src = S.ctx.createMediaStreamSource(stream);
    const proc = S.ctx.createScriptProcessor(1024, 1, 1);
    const mute = S.ctx.createGain(); mute.gain.value = 0;
    let buf = new Int16Array(CHUNK), n = 0, start = 0;
    proc.onaudioprocess = (e) => {
      const x = e.inputBuffer.getChannelData(0);
      for (let i = 0; i < x.length; i++) {
        if (n === 0) start = Date.now() - Math.round((x.length - i) / 16);
        const v = Math.max(-1, Math.min(1, x[i]));
        buf[n++] = v < 0 ? v * 32768 : v * 32767;
        if (n === CHUNK) {
          let s = 0; for (let k = 0; k < CHUNK; k++) s += buf[k] * buf[k];
          const db = 10 * Math.log10(s / CHUNK / 1073741824 + 1e-12);
          let bin = ""; const bytes = new Uint8Array(buf.buffer);
          for (let k = 0; k < bytes.length; k += 4096) bin += String.fromCharCode.apply(null, bytes.subarray(k, k + 4096));
          globalThis.xbotAudio(JSON.stringify({ slot, t: start, db, pcm: btoa(bin) }));
          buf = new Int16Array(CHUNK); n = 0;
        }
      }
    };
    src.connect(proc); proc.connect(mute); mute.connect(S.ctx.destination);
    S.hooked.set(el, { stream, src, proc, slot });
  }
  const scan = () => { for (const el of document.querySelectorAll("audio")) hook(el); };
  scan(); S.timer = setInterval(scan, 1000);
  S.ctx.resume();
  return "hooked";
})()
"""


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
    def __init__(self, page, cfg, url: str, rep, people, connect=None):
        self.page = page
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
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Runtime.bindingCalled", self._on_binding)
        self.cdp.send("Runtime.enable")
        # The binding first, so the world gets it when it is made; only that world (not Meet's) has it.
        self.cdp.send("Runtime.addBinding", {"name": BINDING, "executionContextName": WORLD})
        frame_id = self.cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
        context = self.cdp.send("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": WORLD})["executionContextId"]
        result = self.cdp.send("Runtime.evaluate", {"expression": HOOK_JS, "contextId": context, "returnByValue": True})
        if result.get("exceptionDetails"):
            raise RuntimeError(f"hook failed: {result['exceptionDetails'].get('text')}")
        rep.log("per-person audio (EXPERIMENTAL): Meet's <audio> slots hooked from an isolated world; each slot is "
                "named by the speaking indicator")

    def start(self) -> "PerPersonAudio":
        return self

    def _on_binding(self, event: dict) -> None:
        if event.get("name") != BINDING:
            return
        try:
            self.pending.append(json.loads(event["payload"]))
        except (ValueError, KeyError):
            pass

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
        try:
            self.cdp.detach()
        except Exception:  # noqa: BLE001
            pass
        self.rep.log("per-person audio: stopped; " + self.summary())


def start(args, cfg, url: str, rep, people) -> PerPersonAudio:
    return PerPersonAudio(people.page if people else None, cfg, url, rep, people)
