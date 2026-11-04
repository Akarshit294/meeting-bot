"""Manager settings, all from environment variables (docs/SPEC.md 6)."""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    api_key: str
    bot_image: str = "meeting-bot:latest"
    port: int = 8090
    max_bots: int = 4
    state_file: Path | None = Path("state/bots.json")
    # How bot containers reach the manager (on the shared Docker network).
    internal_url: str = "http://manager:8090"
    docker_network: str = "meeting-bot"
    # The run folders: where bot containers write (a path on the Docker HOST), and where the
    # manager itself sees the same folder.
    host_output_dir: str | None = None
    output_dir: Path = Path("output")
    # Extra command-line options for every bot (the proven join: type the name, wait 15 s).
    bot_args: list[str] = field(default_factory=lambda: ["--type-name", "--knock-delay", "15"])
    # Which host name the live-view link uses.
    live_view_host: str = "localhost"
    # TEST ONLY: open this page instead of the real Meet link (the fake meeting, docs/PLAN.md task 5).
    test_meet_url: str | None = None
    # Container limits (docs/SPEC.md 4.4).
    cpus: float = 2.5
    memory: str = "5g"
    shm_size: str = "2g"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        key = (env("BOT_API_KEY") or "").strip()
        if not key:
            raise SystemExit("BOT_API_KEY is required (put it in .env; ./try.sh makes one for you)")
        state = env("STATE_FILE", "state/bots.json")
        return cls(
            api_key=key,
            bot_image=env("BOT_IMAGE", "meeting-bot:latest"),
            port=int(env("MANAGER_PORT", "8090")),
            max_bots=int(env("MAX_BOTS", "4")),
            state_file=Path(state) if state else None,
            internal_url=env("MANAGER_INTERNAL_URL", "http://manager:8090"),
            docker_network=env("DOCKER_NETWORK", "meeting-bot"),
            host_output_dir=env("HOST_OUTPUT_DIR") or None,
            output_dir=Path(env("OUTPUT_DIR", "output")),
            bot_args=shlex.split(env("BOT_ARGS", "--type-name --knock-delay 15")),
            live_view_host=env("LIVE_VIEW_HOST", "localhost"),
            test_meet_url=env("TEST_MEET_URL") or None,
            cpus=float(env("BOT_CPUS", "2.5")),
            memory=env("BOT_MEMORY", "5g"),
            shm_size=env("BOT_SHM_SIZE", "2g"),
        )
