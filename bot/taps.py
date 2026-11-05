"""What the bot takes from Meet's media elements, from its ISOLATED world (bot/isolated.py), after admission.

- SlotTap: Meet's `<audio>` elements ("slots": Meet plays the loudest speakers through 3 of them). Each
  slot's stream goes into the world's own 16 kHz AudioContext; every 200 ms each slot's PCM (16-bit) and
  its level come back through the binding. Used by `--per-person-audio` and by `--save-media`
  (`audio/slot-N.wav`, and each person's sound).
- VideoTap: the `<video>` element inside each person's tile (`[data-participant-id]`). A MediaRecorder per
  element records that person's camera as WebM (VP8); 1 s pieces come back through the binding. Only
  people the bot allows (the roster: never the bot itself, never its own presentation) and only a live,
  playing camera (a camera that is off gives no file).

Nothing here runs in Meet's page world, and none of it starts before admission.
"""
from __future__ import annotations

import base64
import json

from isolated import IsolatedWorld

BINDING = "xbotMedia"

AUDIO_JS = r"""
(() => {
  if (window.__xbotAudio) return "already";
  const send = (o) => globalThis.xbotMedia(JSON.stringify(o));
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
          send({ kind: "audio", slot, t: start, db, pcm: btoa(bin) });
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

VIDEO_JS = r"""
(() => {
  if (window.__xbotVideo) return "already";
  const send = (o) => globalThis.xbotMedia(JSON.stringify(o));
  const V = window.__xbotVideo = { allow: new Set(), recs: new Map(), next: 1 };
  const pidOf = (el) => { const t = el.closest("[data-participant-id]"); return t ? t.getAttribute("data-participant-id") : null; };
  const mime = ["video/webm;codecs=vp8", "video/webm"].find((m) => MediaRecorder.isTypeSupported(m));
  function stop(el) {
    const r = V.recs.get(el); if (!r) return;
    V.recs.delete(el);
    try { if (r.rec.state !== "inactive") r.rec.stop(); } catch (e) {}
  }
  function start(el, pid, track) {
    const id = V.next++;
    let seq = 0;
    const rec = new MediaRecorder(new MediaStream([track]), { mimeType: mime, videoBitsPerSecond: 600000 });
    rec.ondataavailable = async (e) => {
      const n = seq++;
      if (!e.data || !e.data.size) return;
      const bytes = new Uint8Array(await e.data.arrayBuffer());
      let bin = "";
      for (let k = 0; k < bytes.length; k += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(k, k + 0x8000));
      send({ kind: "video", rec: id, pid, seq: n, data: btoa(bin) });
    };
    rec.onstop = () => send({ kind: "video_end", rec: id, pid });
    rec.start(1000);
    V.recs.set(el, { rec, pid, track, id });
    send({ kind: "video_start", rec: id, pid, w: el.videoWidth, h: el.videoHeight });
  }
  function scan() {
    const busy = new Set();
    for (const [el, r] of V.recs) {
      const s = el.srcObject;
      const still = el.isConnected && pidOf(el) === r.pid && V.allow.has(r.pid) && r.track.readyState === "live"
                    && s instanceof MediaStream && s.getVideoTracks().includes(r.track);
      if (!still) stop(el); else busy.add(r.pid);
    }
    for (const el of document.querySelectorAll("[data-participant-id] video")) {
      if (V.recs.has(el)) continue;
      const pid = pidOf(el), s = el.srcObject;
      if (!pid || busy.has(pid) || !V.allow.has(pid) || !(s instanceof MediaStream)) continue;
      const track = s.getVideoTracks().find((t) => t.readyState === "live" && !t.muted);
      if (!track || !el.videoWidth || el.paused) continue;  // a camera that is off gives no file
      busy.add(pid);
      start(el, pid, track);
    }
  }
  V.scan = scan;
  V.timer = setInterval(scan, 1000);
  return "hooked";
})()
"""


class MediaTaps:
    """One isolated world for both taps. `drain()` (main thread) hands out what came in since last time."""

    def __init__(self, page, audio: bool, video: bool):
        self.audio_chunks: list[dict] = []
        self.video_messages: list[dict] = []
        self.world = IsolatedWorld(page, {BINDING: self._on_message})
        self.audio = self.video = False
        if audio:
            self.world.evaluate(AUDIO_JS)
            self.audio = True
        if video:
            self.world.evaluate(VIDEO_JS)
            self.video = True
        self.allowed: tuple = ()

    def _on_message(self, payload: str) -> None:
        try:
            message = json.loads(payload)
        except ValueError:
            return
        if message.get("kind") == "audio":
            self.audio_chunks.append(message)
        else:
            self.video_messages.append(message)

    def allow_video(self, ids) -> None:
        """Only these participants' cameras are recorded (the roster: never the bot, never its presentation)."""
        ids = tuple(sorted(ids))
        if not self.video or ids == self.allowed:
            return
        self.allowed = ids
        self.world.evaluate(f"window.__xbotVideo.allow = new Set({json.dumps(list(ids))}); window.__xbotVideo.scan(); 1")

    def drain(self) -> tuple[list[dict], list[dict]]:
        audio, self.audio_chunks = self.audio_chunks, []
        video, self.video_messages = self.video_messages, []
        return audio, video

    def stop_video(self) -> None:
        """Stop every recorder so its last piece comes back (the caller keeps draining for a moment)."""
        if self.video:
            self.world.evaluate("window.__xbotVideo.allow = new Set(); window.__xbotVideo.scan(); 1")

    def close(self) -> None:
        self.world.close()


def pcm_of(chunk: dict) -> bytes:
    return base64.b64decode(chunk["pcm"])
