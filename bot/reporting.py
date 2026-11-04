"""The run folder and the status list.

Every status goes to `events.jsonl` in the run folder and, in service mode, to the manager. The rules:
after `fatal` nothing more is reported; after `call_ended` only `done`; after `done` nothing.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

FINAL = {"done", "fatal"}


def allowed(previous: str | None, code: str) -> bool:
    if previous in FINAL:
        return False
    if previous == "call_ended":
        return code == "done"
    return code != previous


class Tee:
    """Copy everything printed to the bot's log file in the run folder."""

    def __init__(self, stream, path: Path):
        self.stream = stream
        self.file = open(path, "a", buffering=1)
        self.lock = threading.Lock()

    def write(self, data):
        with self.lock:
            self.stream.write(data)
            try:
                self.file.write(data)
            except ValueError:
                pass
        return len(data)

    def flush(self):
        self.stream.flush()
        try:
            self.file.flush()
        except ValueError:
            pass

    def __getattr__(self, name):
        return getattr(self.stream, name)


class Reporter:
    def __init__(self, run_dir: Path, send: Callable[[dict], bool] | None = None, tee: bool = False):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir = run_dir
        self.count = 0
        self.last: str | None = None
        self.codes: list[str] = []
        self._send = send
        self._lock = threading.Lock()
        if tee and not isinstance(sys.stdout, Tee):
            sys.stdout = Tee(sys.stdout, run_dir / "bot.log")
            sys.stderr = Tee(sys.stderr, run_dir / "bot.log")

    def log(self, message: str) -> None:
        print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {message}", flush=True)

    def status(self, code: str, sub_code: str | None = None, message: str = "", page=None) -> bool:
        with self._lock:
            if not allowed(self.last, code):
                if self.last in FINAL or self.last == "call_ended":
                    self.log(f"not reporting {code}: nothing but done may follow {self.last}")
                return False
            self.last = code
            self.codes.append(code)
            self.count += 1
            event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": code, "sub_code": sub_code,
                     "message": message}
            with open(self.run_dir / "events.jsonl", "a") as f:
                f.write(json.dumps(event) + "\n")
            self.log(f"STATUS {code}" + (f" [{sub_code}]" if sub_code else "") + (f" - {message}" if message else ""))
            if self._send:
                self._send({"code": code, "sub_code": sub_code, "message": message, "run_dir": self.run_dir.name})
        if page is not None:
            self.snapshot(page, f"{self.count:02d}-{code}", html=code in ("fatal", "call_ended"))
        return True

    def event(self, kind: str, **data) -> None:
        """Other things worth keeping, one line each in `timeline.jsonl` (participants, screen share...)."""
        line = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": kind, **data}
        with self._lock, open(self.run_dir / "timeline.jsonl", "a") as f:
            f.write(json.dumps(line) + "\n")

    def snapshot(self, page, name: str, html: bool = False) -> None:
        from playwright.sync_api import Error as PlaywrightError

        try:
            page.screenshot(path=self.run_dir / f"{name}.png")
            if html:
                (self.run_dir / f"{name}.html").write_text(page.content())
        except PlaywrightError:
            pass
