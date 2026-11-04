"""The bots the manager knows about, their status lists, and the JSON state file.

Status rules (docs/SPEC.md 2.2):
- codes come in the order the bot reports them; the same code twice in a row is dropped;
- after `fatal`, nothing more is ever added (xebit-api only notices a failure while `fatal` is the
  latest code);
- after `call_ended`, only `done` may follow;
- after `done`, nothing more.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_CODES = (
    "joining_call",
    "in_waiting_room",
    "in_call_not_recording",
    "in_call_recording",
    "call_ended",
    "done",
    "fatal",
)
IN_CALL = {"in_call_not_recording", "in_call_recording"}
GONE = {"call_ended", "done", "fatal"}
FINAL = {"done", "fatal"}


def now_iso() -> str:
    """Recall's time format: microseconds and a Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_bot_id() -> str:
    return "xbot_" + secrets.token_hex(16)


@dataclass
class Bot:
    id: str
    meeting_url: str
    bot_name: str
    metadata: dict[str, Any] = field(default_factory=dict)
    camera_url: str | None = None
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    status_changes: list[dict[str, Any]] = field(default_factory=list)
    live_view_url: str | None = None
    created_at: str = field(default_factory=now_iso)
    container: str | None = None
    run_dir: str | None = None
    screenshare_url: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def latest(self) -> str:
        return self.status_changes[-1]["code"] if self.status_changes else ""

    @property
    def active(self) -> bool:
        """Counts toward MAX_BOTS: anything that has not finished."""
        return self.latest not in FINAL

    def public(self) -> dict[str, Any]:
        """The bot object the API returns (Recall's shape, the fields xebit-api reads)."""
        return {
            "id": self.id,
            "meeting_url": self.meeting_url,
            "bot_name": self.bot_name,
            "metadata": self.metadata,
            "status_changes": list(self.status_changes),
            "live_view_url": self.live_view_url,
            "created_at": self.created_at,
            "recordings": [],
        }


def accepts(latest: str, code: str) -> bool:
    """Whether `code` may follow `latest` in a bot's status list."""
    if code not in STATUS_CODES:
        return False
    if latest in FINAL:
        return False
    if latest == "call_ended":
        return code == "done"
    return code != latest


class Store:
    """Thread-safe, saved to a JSON file after every change (so a manager restart keeps live bots)."""

    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.RLock()
        self._bots: dict[str, Bot] = {}
        self.secret = secrets.token_hex(32)
        if path and path.exists():
            data = json.loads(path.read_text())
            self.secret = data.get("secret") or self.secret
            for raw in data.get("bots", []):
                bot = Bot(**raw)
                self._bots[bot.id] = bot
        self._save()

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"secret": self.secret, "bots": [asdict(b) for b in self._bots.values()]}, indent=1))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def add(self, bot: Bot) -> Bot:
        with self._lock:
            self._bots[bot.id] = bot
            self._save()
            return bot

    def get(self, bot_id: str) -> Bot | None:
        with self._lock:
            return self._bots.get(bot_id)

    def all(self) -> list[Bot]:
        with self._lock:
            return sorted(self._bots.values(), key=lambda b: b.created_at, reverse=True)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for b in self._bots.values() if b.active)

    def update(self, bot_id: str, **fields: Any) -> Bot | None:
        with self._lock:
            bot = self._bots.get(bot_id)
            if bot is None:
                return None
            for key, value in fields.items():
                setattr(bot, key, value)
            self._save()
            return bot

    def add_status(self, bot_id: str, code: str, sub_code: str | None = None, message: str = "") -> bool:
        """Append a status if the rules allow it. Returns whether it was added."""
        with self._lock:
            bot = self._bots.get(bot_id)
            if bot is None or not accepts(bot.latest, code):
                return False
            bot.status_changes.append({
                "code": code,
                "sub_code": sub_code or None,
                "message": message or "",
                "created_at": now_iso(),
            })
            self._save()
            return True
