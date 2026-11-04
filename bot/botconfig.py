"""The bot's orders: from the manager (service mode) or from the command line (as before).

In service mode the container gets only BOT_ID, MANAGER_URL and BOT_TOKEN in its environment; the rest
(meeting link, name, camera page, endpoints) comes from the manager, so URLs that carry xebit tokens are
never in the container's environment.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class Endpoint:
    type: str
    url: str
    events: list[str] = field(default_factory=list)


@dataclass
class BotConfig:
    meeting_url: str
    bot_name: str
    bot_id: str = "xbot_local"
    camera_url: str | None = None
    endpoints: list[Endpoint] = field(default_factory=list)
    meet_url_override: str | None = None

    def endpoint_for(self, event: str, kind: str) -> Endpoint | None:
        """The first endpoint of this type that asked for this event."""
        for endpoint in self.endpoints:
            if endpoint.type == kind and event in endpoint.events:
                return endpoint
        return None

    def webhooks_for(self, event: str) -> list[Endpoint]:
        return [e for e in self.endpoints if e.type == "webhook" and event in e.events]

    @classmethod
    def from_dict(cls, data: dict) -> "BotConfig":
        endpoints = []
        for item in data.get("endpoints") or []:
            if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("type") in ("webhook", "websocket"):
                endpoints.append(Endpoint(item["type"], item["url"], [str(e) for e in item.get("events") or []]))
        return cls(
            meeting_url=str(data["meeting_url"]),
            bot_name=str(data.get("bot_name") or "Meeting Bot"),
            bot_id=str(data.get("bot_id") or "xbot_local"),
            camera_url=data.get("camera_url") or None,
            endpoints=endpoints,
            meet_url_override=data.get("meet_url_override") or None,
        )


@dataclass
class ManagerLink:
    url: str
    bot_id: str
    token: str

    @classmethod
    def from_env(cls) -> "ManagerLink | None":
        url, bot_id, token = (os.environ.get(k) for k in ("MANAGER_URL", "BOT_ID", "BOT_TOKEN"))
        if url and bot_id and token:
            return cls(url.rstrip("/"), bot_id, token)
        return None

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float = 5) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}/internal/bots/{self.bot_id}/{path}", data=data, method=method,
                                     headers={"X-Bot-Token": self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")

    def fetch_config(self, attempts: int = 10) -> BotConfig:
        last = None
        for i in range(attempts):
            try:
                return BotConfig.from_dict(self._request("GET", "config"))
            except (urllib.error.URLError, OSError, ValueError, KeyError) as err:
                last = err
                time.sleep(min(2 ** i, 5))
        raise RuntimeError(f"could not read the bot's config from the manager: {last}")

    def report(self, body: dict, attempts: int = 3) -> bool:
        for i in range(attempts):
            try:
                self._request("POST", "status", body, timeout=3)
                return True
            except (urllib.error.URLError, OSError, ValueError) as err:
                if i == attempts - 1:
                    print(f"warning: could not report {body.get('code') or 'info'} to the manager: {err}", flush=True)
                else:
                    time.sleep(0.5 * (i + 1))
        return False


def safe_url(url: str | None) -> str:
    """A URL from xebit, for logs: host and path only (the query carries tokens)."""
    if not url:
        return "-"
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.hostname or ''}{f':{p.port}' if p.port else ''}{p.path}"
    except ValueError:
        return "<bad url>"


def origin(url: str | None) -> str | None:
    if not url:
        return None
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme in ("http", "https") and p.netloc else None


def needs_secure_flag(url: str | None) -> bool:
    """getUserMedia/getDisplayMedia need a secure origin; http://localhost already is one."""
    p = urlparse(url or "")
    return p.scheme == "http" and p.hostname not in ("localhost", "127.0.0.1")
