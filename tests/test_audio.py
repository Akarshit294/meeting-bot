"""Task 6: the call's sound to xebit (docs/SPEC.md 3.2), without Docker: the frame format (read with xebit's
own decoder), the 200 ms chunks, the reconnect backoff, and answering the server's pings."""
from __future__ import annotations

import math
import struct
import threading
import time

import pytest
from websockets.sync.server import serve

import audio
from xebit_logic import _decode_recall


def tone(freq: float, n: int = 3200, rate: int = 16000) -> bytes:
    return struct.pack(f"<{n}h", *(int(8000 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))


def test_frame_reads_like_xebit():
    pcm = tone(440)
    raw = audio.frame("xbot_abc", pcm, 1_793_800_000.25)
    decoded = _decode_recall(raw, "s1")
    assert decoded.participant_id == "meeting-mix" and decoded.participant_name == "Meeting"
    assert decoded.pcm == pcm and decoded.is_bot is False
    assert decoded.captured_at == pytest.approx(1_793_800_000.25, abs=1e-6)
    assert '"bot": {"id": "xbot_abc"}' in raw
    assert audio.iso(1_793_800_000.25).endswith(".250000Z")


def test_chunk_is_200ms():
    assert audio.CHUNK == 6400 == audio.RATE // 5 * 2


class Server:
    """A websocket server that pings every 0.3 s and records what it gets."""

    def __init__(self, drop_first_after: int | None = None):
        self.messages: list[str] = []
        self.connections = 0
        self.drop_first_after = drop_first_after

        def handler(ws):
            self.connections += 1
            mine = self.connections
            try:
                for message in ws:
                    self.messages.append(message)
                    if mine == 1 and self.drop_first_after and len(self.messages) >= self.drop_first_after:
                        ws.close()
                        return
            except Exception:  # noqa: BLE001
                pass

        self.server = serve(handler, "127.0.0.1", 0, ping_interval=0.3, ping_timeout=0.3)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/audio?token=t"

    def close(self):
        self.server.shutdown()


def paced_chunks(stop: threading.Event):
    while not stop.is_set():
        time.sleep(0.05)
        yield tone(440), time.time()


def test_stream_survives_server_pings():
    server = Server()
    logs: list[str] = []
    streamer = audio.AudioStreamer(server.url, "xbot_1", logs.append, chunks=paced_chunks).start()
    time.sleep(3)  # ten ping rounds: an unanswered ping would close the socket after 0.6 s
    streamer.stop()
    server.close()
    assert server.connections == 1, logs
    assert len(server.messages) > 30
    assert all(_decode_recall(m, "s") for m in server.messages[:5])
    assert not any("dropped" in line and "reconnecting" in line for line in logs), logs


def test_stream_reconnects_after_a_drop():
    server = Server(drop_first_after=5)
    logs: list[str] = []
    streamer = audio.AudioStreamer(server.url, "xbot_1", logs.append, chunks=paced_chunks).start()
    deadline = time.time() + 8
    while time.time() < deadline and server.connections < 2:
        time.sleep(0.1)
    time.sleep(0.5)
    streamer.stop()
    server.close()
    assert server.connections == 2, logs
    assert any("reconnect 1" in line for line in logs), logs


def test_backoff_when_the_server_is_down():
    attempts: list[float] = []

    def refuse(url, timeout):
        attempts.append(time.monotonic())
        raise ConnectionRefusedError("down")

    logs: list[str] = []
    socket = audio.Socket("ws://nowhere/ws?token=SECRET", logs.append, "audio", connect=refuse)
    time.sleep(3.5)
    socket.close()
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert len(attempts) == 3 and gaps[0] == pytest.approx(1, abs=0.3) and gaps[1] == pytest.approx(2, abs=0.3)
    assert audio.BACKOFF == (1, 2, 4, 8)
    assert not socket.send("x") and socket.dropped == 1
    assert all("SECRET" not in line for line in logs), "never log the token"
