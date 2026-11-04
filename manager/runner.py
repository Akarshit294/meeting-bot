"""How the manager starts, talks to and stops bot containers.

The API only uses the `Runner` interface, so tests can swap in a fake runner (no Docker).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .config import Settings
from .store import Bot

log = logging.getLogger("manager.runner")

CONTROL_PORT = 8080
LIVE_VIEW_PORT = 6080


def bot_token(secret: str, bot_id: str) -> str:
    """The token a bot uses on the manager's internal endpoints. Each bot gets its own."""
    return hmac.new(secret.encode(), bot_id.encode(), hashlib.sha256).hexdigest()


@dataclass
class Started:
    container: str
    live_view_url: str | None


class RunnerError(Exception):
    pass


class Runner(Protocol):
    def start(self, bot: Bot, token: str) -> Started:
        """Start the bot's container. Raises RunnerError if it can't."""

    def command(self, bot: Bot, action: str, body: dict | None = None, timeout: float = 10) -> tuple[int, dict]:
        """Send a command to the bot's control server: `leave`, `screenshare`, `screenshare_stop`.
        Returns (HTTP status, JSON body). Raises RunnerError if the bot can't be reached."""

    def state(self, bot: Bot) -> str:
        """`running`, `exited` or `missing`."""

    def stop(self, bot: Bot) -> None:
        """Ask the container to stop (SIGTERM: the bot leaves the call and reports, as on Ctrl+C)."""

    def remove(self, bot: Bot) -> None:
        """Remove the stopped container."""


def post_json(url: str, body: dict | None, timeout: float, method: str = "POST", token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body or {}).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Bot-Token"] = token
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            payload = json.loads(err.read() or b"{}")
        except ValueError:
            payload = {}
        return err.code, payload
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as err:
        raise RunnerError(f"bot control server unreachable: {err}") from err


class DockerRunner:
    """One container per bot, from the bot image, on the manager's Docker network (docs/SPEC.md 6)."""

    def __init__(self, settings: Settings):
        import docker  # only needed when running for real

        self.settings = settings
        self.client = docker.from_env()
        self.token_for = lambda bot_id: None  # set by the app: each bot's own token

    def _name(self, bot: Bot) -> str:
        return f"xbot-{bot.id.removeprefix('xbot_')}"

    def start(self, bot: Bot, token: str) -> Started:
        import docker.errors

        s = self.settings
        env = {
            "BOT_ID": bot.id,
            "MANAGER_URL": s.internal_url,
            "BOT_TOKEN": token,
        }
        volumes = {}
        if s.host_output_dir:
            volumes[s.host_output_dir] = {"bind": "/output", "mode": "rw"}
        try:
            container = self.client.containers.run(
                s.bot_image,
                command=list(s.bot_args),
                name=self._name(bot),
                detach=True,
                environment=env,
                network=s.docker_network,
                ports={f"{LIVE_VIEW_PORT}/tcp": ("127.0.0.1", None)},
                volumes=volumes,
                nano_cpus=int(s.cpus * 1e9),
                mem_limit=s.memory,
                shm_size=s.shm_size,
                extra_hosts={"host.docker.internal": "host-gateway"},
                labels={"meeting-bot.id": bot.id},
            )
        except docker.errors.DockerException as err:
            raise RunnerError(f"could not start the bot container: {err}") from err
        live = None
        try:
            container.reload()
            binding = (container.ports or {}).get(f"{LIVE_VIEW_PORT}/tcp") or []
            if binding:
                live = f"http://{s.live_view_host}:{binding[0]['HostPort']}"
        except docker.errors.DockerException:
            pass
        return Started(container=container.name, live_view_url=live)

    def command(self, bot: Bot, action: str, body: dict | None = None, timeout: float = 10) -> tuple[int, dict]:
        return post_json(f"http://{self._name(bot)}:{CONTROL_PORT}/{action}", body, timeout, token=self.token_for(bot.id))

    def _container(self, bot: Bot):
        import docker.errors

        try:
            return self.client.containers.get(bot.container or self._name(bot))
        except docker.errors.NotFound:
            return None

    def state(self, bot: Bot) -> str:
        import docker.errors

        try:
            container = self._container(bot)
        except docker.errors.DockerException:
            return "running"  # can't tell: don't declare it dead
        if container is None:
            return "missing"
        return "exited" if container.status in ("exited", "dead") else "running"

    def stop(self, bot: Bot) -> None:
        import docker.errors

        try:
            container = self._container(bot)
            if container is not None and container.status == "running":
                container.kill(signal="SIGTERM")
        except docker.errors.DockerException as err:
            log.warning("stop %s: %s", bot.id, err)

    def remove(self, bot: Bot) -> None:
        import docker.errors

        try:
            container = self._container(bot)
            if container is not None:
                container.remove(force=True)
        except docker.errors.DockerException as err:
            log.warning("remove %s: %s", bot.id, err)
