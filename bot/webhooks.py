"""Webhooks to xebit-api (participant join/leave), sent in order from one background thread.

A failed push is logged (host and path only) and retried up to 3 times; then the bot moves on.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from botconfig import safe_url

RETRIES = 3


class WebhookSender:
    def __init__(self, log: Callable[[str], None], timeout: float = 10, backoff: float = 1.0):
        self._log = log
        self._timeout = timeout
        self._backoff = backoff
        self._queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="webhooks", daemon=True)
        self.sent = 0
        self.failed = 0
        self._thread.start()

    def send(self, url: str, payload: dict) -> None:
        self._queue.put((url, payload))

    def close(self, wait: float = 10) -> None:
        self._queue.put(None)
        self._thread.join(wait)

    def _post(self, url: str, payload: dict) -> None:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "meeting-bot/1"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            resp.read()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            url, payload = item
            for attempt in range(1 + RETRIES):
                try:
                    self._post(url, payload)
                    self.sent += 1
                    break
                except (urllib.error.URLError, OSError, ValueError) as err:
                    reason = getattr(err, "code", None) or getattr(err, "reason", None) or err
                    if attempt == RETRIES:
                        self.failed += 1
                        self._log(f"webhook {payload.get('event')} to {safe_url(url)} failed after {RETRIES} retries: {reason}")
                    else:
                        self._log(f"webhook {payload.get('event')} to {safe_url(url)} failed ({reason}); retrying")
                        time.sleep(self._backoff * (attempt + 1))
