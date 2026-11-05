"""The bot's own CPU and memory, from its container's cgroup (the numbers `docker stats` shows), in bot.log:
every EVERY seconds in the call, and a summary at the end. No need to watch `docker stats` during a test.

- CPU in cores (1.0 = one core; the container may use 2.5, docs/SPEC.md 4.4);
- "at the limit": the share of moments (100 ms slices) in which the container wanted more CPU than it may
  use and was held back. Above about 10%, everything in the bot slows down: clicks, pictures, sound.
- memory in GB (the limit is 5).

Reads cgroup v2 files (`cpu.stat`, `cpu.max`, `memory.current`). Where they are missing (cgroup v1, or not
in a container) it says so once and stays quiet.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

CGROUP = Path("/sys/fs/cgroup")
EVERY = 30.0


def read_cpu_stat(root: Path = CGROUP) -> dict[str, int] | None:
    try:
        text = (root / "cpu.stat").read_text()
    except OSError:
        return None
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out if "usage_usec" in out else None


def cpu_limit(root: Path = CGROUP) -> float | None:
    """Cores the container may use (`cpu.max` "250000 100000" = 2.5), or None if unlimited."""
    try:
        quota, period = (root / "cpu.max").read_text().split()[:2]
        return None if quota == "max" else int(quota) / int(period)
    except (OSError, ValueError):
        return None


def memory_bytes(root: Path = CGROUP) -> int | None:
    try:
        return int((root / "memory.current").read_text().strip())
    except (OSError, ValueError):
        return None


class Usage:
    def __init__(self, log: Callable[[str], None], every: float = EVERY, root: Path = CGROUP,
                 clock: Callable[[], float] = time.monotonic):
        self.log = log
        self.every = every
        self.root = root
        self.clock = clock
        self.limit = cpu_limit(root)
        self.last: tuple[float, dict[str, int]] | None = None
        self.windows: list[tuple[float, float]] = []  # (cores, share at the limit) per window
        self.peak_memory = 0
        self.off = False

    def tick(self) -> None:
        if self.off:
            return
        now = self.clock()
        if self.last and now - self.last[0] < self.every:
            return
        stat = read_cpu_stat(self.root)
        if stat is None:
            self.off = True
            self.log("usage: this container's CPU numbers can't be read (no cgroup v2), so none are logged")
            return
        if self.last:
            self._report(now - self.last[0], self.last[1], stat)
        self.last = (now, stat)

    def _report(self, seconds: float, before: dict[str, int], after: dict[str, int]) -> None:
        cores = (after["usage_usec"] - before["usage_usec"]) / 1e6 / max(seconds, 1e-6)
        periods = after.get("nr_periods", 0) - before.get("nr_periods", 0)
        held = (after.get("nr_throttled", 0) - before.get("nr_throttled", 0)) / periods if periods > 0 else 0.0
        memory = memory_bytes(self.root)
        if memory:
            self.peak_memory = max(self.peak_memory, memory)
        self.windows.append((cores, held))
        limit = f" of {self.limit:g}" if self.limit else ""
        at_limit = f", at the limit {held:.0%} of the time" if self.limit else ""
        mem = f", memory {memory / 2**30:.2f} GB" if memory else ""
        self.log(f"usage: CPU {cores:.2f} cores{limit}{at_limit}{mem}")

    def summary(self) -> str | None:
        if not self.windows:
            return None
        cores = [c for c, _ in self.windows]
        held = [h for _, h in self.windows]
        limit = f" (limit {self.limit:g})" if self.limit else ""
        worst = f"; at the limit {max(held):.0%} of the time at worst" if self.limit else ""
        mem = f"; memory {self.peak_memory / 2**30:.2f} GB at most" if self.peak_memory else ""
        return (f"usage over the call: CPU {sum(cores) / len(cores):.2f} cores on average, {max(cores):.2f} at "
                f"most{limit}{worst}{mem}")
