"""The call's sound to xebit-api: the `audio_separate_raw.data` websocket (docs/SPEC.md 3.2).

`parec` reads `meet_out.monitor` (what the Meet browser plays: everyone but the bot) as 16 kHz mono
s16le. Every 200 ms (6400 bytes) one JSON text frame goes out. If the socket drops, it reconnects after
1, 2, 4, 8, 8... seconds; sound read while it is down is dropped (it is live audio).
"""
from __future__ import annotations

import base64
import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Iterator

import websocket

from botconfig import safe_url

RATE = 16_000
CHUNK = 6_400  # 200 ms of 16 kHz mono 16-bit
MIX = {"id": "meeting-mix", "name": "Meeting", "is_bot": False}
BACKOFF = (1, 2, 4, 8)
SEND_TIMEOUT = 5


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def frame(bot_id: str, pcm: bytes, started_at: float, participant: dict = MIX) -> str:
    return json.dumps({
        "event": "audio_separate_raw.data",
        "data": {
            "data": {
                "buffer": base64.b64encode(pcm).decode(),
                "timestamp": {"absolute": iso(started_at)},
                "participant": participant,
            },
            "bot": {"id": bot_id},
        },
    })


def parec_chunks(device: str = "meet_out.monitor", stop: threading.Event | None = None) -> Iterator[tuple[bytes, float]]:
    """(200 ms of PCM, wall-clock time of its first sample) from PulseAudio, until `stop` is set."""
    proc = subprocess.Popen(["parec", f"--device={device}", "--format=s16le", f"--rate={RATE}", "--channels=1",
                             "--raw", "--latency-msec=40"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while not (stop and stop.is_set()):
            data = proc.stdout.read(CHUNK)
            if not data or len(data) < CHUNK:
                break
            yield data, time.time() - CHUNK / 2 / RATE
    finally:
        proc.kill()
        proc.wait(timeout=5)


class Socket:
    """One websocket client that reconnects with backoff in the background."""

    def __init__(self, url: str, log: Callable[[str], None], name: str, connect=websocket.create_connection):
        self.url = url
        self.log = log
        self.name = name
        self._connect = connect
        self._ws = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.connects = 0
        self.sent = 0
        self.dropped = 0
        threading.Thread(target=self._keep_connected, name=f"{name}-ws", daemon=True).start()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def _keep_connected(self) -> None:
        failures = 0
        while not self._stop.is_set():
            if self._ws is None:
                try:
                    ws = self._connect(self.url, timeout=10)
                    ws.settimeout(SEND_TIMEOUT)  # a send that can't get through in time drops the socket
                    with self._lock:
                        self._ws = ws
                    # Read (and drop) what the server sends: this answers its pings. Servers like
                    # uvicorn close a socket whose pings go unanswered (after about 40 s).
                    threading.Thread(target=self._drain, args=(ws,), name=f"{self.name}-ws-read", daemon=True).start()
                    self.connects += 1
                    failures = 0
                    self.log(f"{self.name}: connected to {safe_url(self.url)}"
                             + (f" (reconnect {self.connects - 1})" if self.connects > 1 else ""))
                except Exception as exc:  # noqa: BLE001
                    delay = BACKOFF[min(failures, len(BACKOFF) - 1)]
                    failures += 1
                    if failures <= 3 or failures % 20 == 0:
                        self.log(f"{self.name}: can't connect to {safe_url(self.url)} ({type(exc).__name__}: "
                                 f"{str(exc)[:120]}); retry in {delay}s")
                    self._stop.wait(delay)
                    continue
            self._wake.wait(1)
            self._wake.clear()

    def _drain(self, ws) -> None:
        while not self._stop.is_set() and self._ws is ws:
            try:
                ws.recv()
            except websocket.WebSocketTimeoutException:
                continue  # nothing came; keep listening (the socket's timeout also bounds every send)
            except Exception:  # noqa: BLE001 - closed: the sender notices and reconnects
                return

    def send(self, text: str) -> bool:
        with self._lock:
            ws = self._ws
            if ws is None:
                self.dropped += 1
                return False
            try:
                ws.send(text)
                self.sent += 1
                return True
            except Exception as exc:  # noqa: BLE001
                self.log(f"{self.name}: socket dropped ({type(exc).__name__}: {str(exc)[:120]}); reconnecting")
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ws = None
                self.dropped += 1
                self._wake.set()
                return False

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        locked = self._lock.acquire(timeout=2)  # never hang here: a stuck send must not stop the bot's end
        try:
            ws, self._ws = self._ws, None
            if ws is not None:
                try:
                    ws.close(timeout=1)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if locked:
                self._lock.release()


class AudioStreamer:
    """The whole call's sound as one participant, `meeting-mix` (the v1 default)."""

    def __init__(self, url: str, bot_id: str, log: Callable[[str], None],
                 chunks: Callable[[threading.Event], Iterator[tuple[bytes, float]]] | None = None,
                 connect=websocket.create_connection, tap: Callable[[bytes, float], None] | None = None):
        self.bot_id = bot_id
        self.log = log
        self.tap = tap  # --save-media: every frame also goes to audio/mixed.wav
        self.socket = Socket(url, log, "audio", connect)
        self._chunks = chunks or (lambda stop: parec_chunks(stop=stop))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="audio", daemon=True)
        self.frames = 0

    def start(self) -> "AudioStreamer":
        self._thread.start()
        self.log("audio: streaming the call's sound (meet_out.monitor, 16 kHz mono, 200 ms frames) as 'meeting-mix'")
        return self

    def _run(self) -> None:
        last_note = time.monotonic()
        while not self._stop.is_set():
            try:
                for pcm, started in self._chunks(self._stop):
                    self.frames += 1
                    self.socket.send(frame(self.bot_id, pcm, started))
                    if self.tap:
                        try:
                            self.tap(pcm, started)
                        except Exception as exc:  # noqa: BLE001 - the file must never stop the stream
                            self.log(f"audio: not saved to the file ({type(exc).__name__}: {exc})")
                            self.tap = None
                    if time.monotonic() - last_note > 60:
                        last_note = time.monotonic()
                        self.log(f"audio: {self.socket.sent} frames sent, {self.socket.dropped} dropped while disconnected")
            except Exception as exc:  # noqa: BLE001
                self.log(f"audio: reader failed ({type(exc).__name__}: {exc}); restarting it")
            self._stop.wait(1)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(5)
        self.socket.close()
        self.log(f"audio: stopped after {self.socket.sent} frames sent, {self.socket.dropped} dropped")


def start_for(args, cfg, url: str, rep, people=None, taps=None, tap=None):
    """The audio stream for this bot: one mixed stream, or one per person with --per-person-audio.
    `tap` gets every mixed frame too (--save-media)."""
    if getattr(args, "per_person_audio", False):
        try:
            import per_person_audio

            return per_person_audio.start(args, cfg, url, rep, people, taps)
        except Exception as exc:  # noqa: BLE001 - the mixed stream is the safe default
            rep.log(f"per-person audio not started ({type(exc).__name__}: {exc}); using the mixed stream")
    return AudioStreamer(url, cfg.bot_id, rep.log, tap=tap).start()
