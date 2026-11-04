from __future__ import annotations

import json
import statistics
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket
from Xlib import XK, X
from Xlib import display as xdisplay
from Xlib.ext import record, xtest
from Xlib.protocol import rq

SESSION_FILE = "live-session.json"
KINDS = {X.KeyPress: "key_down", X.KeyRelease: "key_up", X.ButtonPress: "button_down",
         X.ButtonRelease: "button_up", X.MotionNotify: "move"}
FAKE = {"key_down": X.KeyPress, "key_up": X.KeyRelease, "button_down": X.ButtonPress, "button_up": X.ButtonRelease}


class InputRecorder:
    def __init__(self):
        self.events: list[list] = []
        self.start_pointer: list[int] | None = None
        self._recording = xdisplay.Display()
        self._control = xdisplay.Display()
        self._context = self._recording.record_create_context(0, [record.AllClients], [{
            "core_requests": (0, 0), "core_replies": (0, 0),
            "ext_requests": (0, 0, 0, 0), "ext_replies": (0, 0, 0, 0),
            "delivered_events": (0, 0), "device_events": (X.KeyPress, X.MotionNotify),
            "errors": (0, 0), "client_started": False, "client_died": False,
        }])
        self._offset = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        pointer = self._control.screen().root.query_pointer()
        self.start_pointer = [pointer.root_x, pointer.root_y]
        self._t0 = time.perf_counter()
        self._thread.start()

    def stop(self) -> list[list]:
        self._control.record_disable_context(self._context)
        self._control.flush()
        self._thread.join(timeout=5)
        return self.events

    def _run(self) -> None:
        self._recording.record_enable_context(self._context, self._on_data)
        self._recording.record_free_context(self._context)

    def _on_data(self, reply) -> None:
        if reply.category != record.FromServer or reply.client_swapped or not reply.data or reply.data[0] < 2:
            return
        received = (time.perf_counter() - self._t0) * 1000
        data = reply.data
        while data:
            event, data = rq.EventField(None).parse_binary_value(data, self._recording.display, None, None)
            if self._offset is None:
                self._offset = received - event.time
            t = round(event.time + self._offset, 1)
            kind = KINDS.get(event.type)
            if kind == "move":
                self.events.append([t, kind, event.root_x, event.root_y])
            elif kind:
                self.events.append([t, kind, event.detail])


def replay(events: list[list]) -> None:
    display = xdisplay.Display()
    t0 = time.perf_counter()
    for t, kind, *args in events:
        delay = t0 + t / 1000 - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        if kind == "move":
            xtest.fake_input(display, X.MotionNotify, x=args[0], y=args[1])
        else:
            xtest.fake_input(display, FAKE[kind], args[0])
        display.flush()
    display.sync()
    display.close()


class VncClient:
    def __init__(self, url: str = "ws://localhost:6080/websockify"):
        self._ws = websocket.create_connection(url, subprotocols=["binary"], timeout=10)
        self._buffer = b""
        if not self._read(12).startswith(b"RFB 003."):
            raise RuntimeError("not a VNC server")
        self._ws.send_binary(b"RFB 003.008\n")
        offered = self._read(self._read(1)[0])
        if 1 not in offered:
            raise RuntimeError(f"the VNC server wants authentication types {list(offered)}")
        self._ws.send_binary(b"\x01")
        if self._read(4) != b"\x00\x00\x00\x00":
            raise RuntimeError("the VNC server refused the connection")
        self._ws.send_binary(b"\x01")
        self._read(20)
        self._read(struct.unpack(">I", self._read(4))[0])
        self._lock = threading.Lock()
        self._open = True
        self._send(struct.pack(">BxHi", 2, 1, 0))
        threading.Thread(target=self._view, daemon=True).start()
        threading.Thread(target=self._discard, daemon=True).start()

    def pointer(self, x: int, y: int, buttons: int) -> None:
        self._send(struct.pack(">BBHH", 5, buttons, x, y))

    def key(self, keysym: int, down: bool) -> None:
        self._send(struct.pack(">BBxxI", 4, down, keysym))

    def close(self) -> None:
        self._open = False
        self._ws.close()

    def _send(self, message: bytes) -> None:
        with self._lock:
            self._ws.send_binary(message)

    def _view(self) -> None:
        while self._open:
            try:
                self._send(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))
            except (websocket.WebSocketException, OSError):
                return
            time.sleep(0.02)

    def _discard(self) -> None:
        while self._open:
            try:
                self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except (websocket.WebSocketException, OSError):
                return

    def _read(self, n: int) -> bytes:
        while len(self._buffer) < n:
            self._buffer += self._ws.recv()
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data


def replay_over_vnc(client: VncClient, events: list[list], start: list[int]) -> None:
    keymap = xdisplay.Display()
    shifts = {keymap.keysym_to_keycode(XK.XK_Shift_L), keymap.keysym_to_keycode(XK.XK_Shift_R)}
    x, y = start
    buttons, shifted, held = 0, False, {}
    t0 = time.perf_counter()
    for t, kind, *args in events:
        delay = t0 + t / 1000 - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        if kind == "move":
            x, y = args
            client.pointer(x, y, buttons)
        elif kind.startswith("button"):
            bit = 1 << (args[0] - 1)
            buttons = buttons | bit if kind == "button_down" else buttons & ~bit
            client.pointer(x, y, buttons)
        else:
            code, down = args[0], kind == "key_down"
            if code in shifts:
                shifted = down
            keysym = keymap.keycode_to_keysym(code, 1 if shifted and code not in shifts else 0) if down else held.pop(code, None)
            if down:
                held[code] = keysym
            client.key(keysym or keymap.keycode_to_keysym(code, 0), down)
    keymap.close()


def typing_rhythm(events: list[list]) -> list[tuple[float, float]]:
    keymap = xdisplay.Display()
    shifts = {keymap.keysym_to_keycode(XK.XK_Shift_L), keymap.keysym_to_keycode(XK.XK_Shift_R)}
    keymap.close()
    downs, presses = {}, []
    for t, kind, *args in events:
        if kind == "key_down" and args[0] not in shifts:
            downs[args[0]] = t
        elif kind == "key_up" and args[0] in downs:
            presses.append((downs.pop(args[0]), t))
    presses.sort()
    return [(up - down, (presses[i + 1][0] if i + 1 < len(presses) else up + 150) - down)
            for i, (down, up) in enumerate(presses)]


def type_over_vnc(client: VncClient, text: str, rhythm: list[tuple[float, float]]) -> None:
    shift = XK.XK_Shift_L
    for i, ch in enumerate(text):
        hold, interval = rhythm[i % len(rhythm)] if rhythm else (90, 190)
        keysym = ord(ch) if ord(ch) < 0x100 else 0x01000000 | ord(ch)
        if ch.isupper():
            client.key(shift, True)
            time.sleep(0.04)
        client.key(keysym, True)
        time.sleep(hold / 1000)
        client.key(keysym, False)
        if ch.isupper():
            time.sleep(0.03)
            client.key(shift, False)
        time.sleep(max(interval - hold, 30) / 1000)


def save(path: Path, recorder: InputRecorder, layout: dict) -> None:
    path.write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "start_pointer": recorder.start_pointer, "layout": layout, "events": recorder.events,
    }))


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def compare(recorded: list[list], seen: list[list]) -> str:
    matched, lags, j = 0, [], 0
    for event in recorded:
        for k in range(j, min(j + 4, len(seen))):
            if seen[k][1:] == event[1:]:
                matched += 1
                lags.append(seen[k][0] - event[0])
                j = k + 1
                break
    if not lags:
        return f"0/{len(recorded)} events matched"
    shift = statistics.median(lags)
    jitter = [abs(lag - shift) for lag in lags]
    return (f"{matched}/{len(recorded)} events identical (same kind, position, button or key, same order); "
            f"timing off by median {statistics.median(jitter):.1f} ms, max {max(jitter):.1f} ms")
