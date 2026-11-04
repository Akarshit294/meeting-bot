"""Helpers for the Docker tests (`pytest -m docker`): bring the stack up, call the API, wait for statuses."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("TEST_API", "http://localhost:8090")
TESTKIT = os.environ.get("TEST_TESTKIT", "http://localhost:9000")
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "-f", str(ROOT / "tests/docker-compose.test.yml")]


def api_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("BOT_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("no BOT_API_KEY in .env")


def compose(*args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([*COMPOSE, *args], cwd=ROOT, env={**os.environ, "PWD": str(ROOT), **(env or {})},
                          capture_output=True, text=True, check=check, timeout=1800)


def request(method: str, path: str, body: dict | None = None, key: str | None = None, base: str = API,
            timeout: float = 70) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Token {key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, {"raw": raw.decode(errors="replace")}


def wait_http(url: str, timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    raise TimeoutError(f"{url} did not answer")


def codes(bot: dict) -> list[str]:
    return [s["code"] for s in bot.get("status_changes", [])]


def wait_status(key: str, bot_id: str, code: str, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    bot = {}
    while time.time() < deadline:
        status, bot = request("GET", f"/api/v1/bot/{bot_id}/", key=key)
        if status == 200 and code in codes(bot):
            return bot
        if status == 200 and code not in ("fatal",) and codes(bot) and codes(bot)[-1] == "fatal":
            raise AssertionError(f"bot went fatal while waiting for {code}: {bot['status_changes'][-1]}")
        time.sleep(1)
    raise TimeoutError(f"{bot_id} never reached {code}: {codes(bot)}")


def container_exists(bot_id: str) -> bool:
    name = "xbot-" + bot_id.removeprefix("xbot_")
    out = subprocess.run(["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
                         capture_output=True, text=True).stdout
    return name in out.split()


def remove_bot_containers() -> None:
    names = subprocess.run(["docker", "ps", "-aq", "--filter", "label=meeting-bot.id"], capture_output=True, text=True).stdout.split()
    if names:
        subprocess.run(["docker", "rm", "-f", *names], capture_output=True)
