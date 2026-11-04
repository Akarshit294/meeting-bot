"""Webhooks to xebit-api (participant join/leave), sent in order from one background thread.

xebit-api changes its head count BEFORE steps that can fail, so a retried request can count twice (two
leaves for one person can end a live demo). So a webhook is retried (up to 3 times) only when the
connection could not even be made: then xebit never saw it. A timeout or an error answer is not retried.
A leave is not sent for a person whose join never got through (it would subtract someone else).
Failures are logged with the URL's host and path only.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from botconfig import safe_url

RETRIES = 3


def never_arrived(err: Exception) -> bool:
    """True only when the request surely did not reach the server (safe to send again)."""
    reason = getattr(err, "reason", err) if isinstance(err, urllib.error.URLError) and not isinstance(err, urllib.error.HTTPError) else err
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))


def participant_id(payload: dict):
    return payload.get("data", {}).get("data", {}).get("participant", {}).get("id")


class WebhookSender:
    def __init__(self, log: Callable[[str], None], timeout: float = 10, backoff: float = 1.0, post=None):
        self._log = log
        self._timeout = timeout
        self._backoff = backoff
        self._post = post or self._http_post
        self._queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="webhooks", daemon=True)
        self.sent = 0
        self.failed = 0
        self.failed_joins: set[tuple[str, object]] = set()
        self._thread.start()

    def send(self, url: str, payload: dict) -> None:
        self._queue.put((url, payload))

    def close(self, wait: float = 10) -> None:
        self._queue.put(None)
        self._thread.join(wait)

    def _http_post(self, url: str, payload: dict) -> None:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "meeting-bot/1"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            resp.read()

    def deliver(self, url: str, payload: dict) -> bool:
        event = payload.get("event")
        pid = participant_id(payload)
        if event == "participant_events.leave" and (url, pid) in self.failed_joins:
            self.failed_joins.discard((url, pid))
            self._log(f"webhook leave for {pid} not sent to {safe_url(url)}: its join never got through")
            return False
        for attempt in range(1 + RETRIES):
            try:
                self._post(url, payload)
                self.sent += 1
                return True
            except (urllib.error.URLError, OSError, ValueError) as err:
                reason = getattr(err, "code", None) or getattr(err, "reason", None) or err
                if attempt < RETRIES and never_arrived(err):
                    self._log(f"webhook {event} to {safe_url(url)} could not connect ({reason}); retrying")
                    time.sleep(self._backoff * (attempt + 1))
                    continue
                self.failed += 1
                self._log(f"webhook {event} to {safe_url(url)} failed ({reason}); not retried")
                if event == "participant_events.join":
                    self.failed_joins.add((url, pid))
                return False
        return False

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                self.deliver(*item)
            except Exception as exc:  # noqa: BLE001 - the sender thread must not die
                self._log(f"webhook sender: {type(exc).__name__}: {exc}")
