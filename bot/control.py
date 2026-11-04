"""The bot's small control server (port 8080): the manager sends leave and screen-share requests here.

Playwright is driven from the main thread only, so screen-share requests are queued for the main loop,
which runs them between its checks and hands the result back. A leave request just sets the stop flag,
so it works at any time (before admission it simply stops the bot).
"""
from __future__ import annotations

import hmac
import json
import queue
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


@dataclass
class Command:
    action: str
    body: dict
    done: threading.Event = field(default_factory=threading.Event)
    result: tuple[int, dict] = (504, {"detail": "the bot did not answer in time"})
    abandoned: bool = False  # the caller stopped waiting: don't run it any more

    def reply(self, status: int, body: dict) -> None:
        self.result = (status, body)
        self.done.set()


class ControlServer:
    ACTIONS = {"screenshare", "screenshare_stop"}

    def __init__(self, port: int, token: str | None, on_leave: Callable[[], None], host: str = "0.0.0.0",
                 wait: float = 25):
        self.commands: "queue.Queue[Command]" = queue.Queue()
        self.in_call = threading.Event()
        self.status = "starting"
        self._token = token
        self._on_leave = on_leave
        self._wait = wait
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.split("?")[0] == "/health":
                    return self._send(200, {"ok": True, "status": server.status, "in_call": server.in_call.is_set()})
                self._send(404, {"detail": "not found"})

            def do_POST(self):
                if server._token and not hmac.compare_digest(self.headers.get("X-Bot-Token") or "", server._token):
                    return self._send(401, {"detail": "bad token"})
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                except ValueError:
                    return self._send(400, {"detail": "bad JSON"})
                if not isinstance(body, dict):
                    return self._send(400, {"detail": "the body must be a JSON object"})
                action = self.path.split("?")[0].strip("/")
                if action == "leave":
                    server._on_leave()
                    return self._send(200, {"ok": True})
                if action not in server.ACTIONS:
                    return self._send(404, {"detail": "unknown action"})
                if action == "screenshare" and not isinstance(body.get("url"), str):
                    return self._send(400, {"detail": "url is required"})
                if not server.in_call.is_set():
                    if action == "screenshare_stop":
                        return self._send(200, {"ok": True, "detail": "nothing to stop"})
                    return self._send(409, {"detail": "the bot is not in the call"})
                status, reply = server.submit(action, body)
                self._send(status, reply)

            def _send(self, status: int, body: dict):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    def start(self) -> "ControlServer":
        threading.Thread(target=self.httpd.serve_forever, name="control", daemon=True).start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def submit(self, action: str, body: dict) -> tuple[int, dict]:
        command = Command(action, body)
        self.commands.put(command)
        if not command.done.wait(self._wait):
            command.abandoned = True
        return command.result

    def next_command(self) -> Command | None:
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return None
            if not command.abandoned:
                return command

    def fail_pending(self, status: int = 409, detail: str = "the bot left the call") -> None:
        while (command := self.next_command()) is not None:
            command.reply(status, {"detail": detail})
