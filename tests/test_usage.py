"""The bot's own CPU and memory in bot.log (bot/usage.py), from a made-up cgroup folder."""
from pathlib import Path

import usage


def write(root: Path, usec: int, periods: int, throttled: int, memory: int, limit: str = "250000 100000") -> None:
    (root / "cpu.stat").write_text(f"usage_usec {usec}\nuser_usec 1\nsystem_usec 1\nnr_periods {periods}\n"
                                   f"nr_throttled {throttled}\nthrottled_usec 5\n")
    (root / "cpu.max").write_text(limit + "\n")
    (root / "memory.current").write_text(f"{memory}\n")


def test_cores_time_at_the_limit_and_memory(tmp_path):
    lines, now = [], [100.0]
    write(tmp_path, usec=1_000_000, periods=100, throttled=0, memory=2**30)
    u = usage.Usage(lines.append, every=30, root=tmp_path, clock=lambda: now[0])
    u.tick()
    assert lines == [] and u.limit == 2.5
    now[0] = 110.0
    u.tick()  # too soon: nothing
    assert lines == []
    now[0] = 130.0
    write(tmp_path, usec=1_000_000 + 45_000_000, periods=400, throttled=30, memory=3 * 2**29)
    u.tick()  # 45 s of CPU in 30 s = 1.5 cores; 30 of 300 slices held back
    assert lines == ["usage: CPU 1.50 cores of 2.5, at the limit 10% of the time, memory 1.50 GB"]
    now[0] = 160.0
    write(tmp_path, usec=46_000_000 + 75_000_000, periods=700, throttled=180, memory=2**30)
    u.tick()
    assert lines[-1].startswith("usage: CPU 2.50 cores of 2.5, at the limit 50% of the time")
    assert u.summary() == ("usage over the call: CPU 2.00 cores on average, 2.50 at most (limit 2.5); at the "
                           "limit 50% of the time at worst; memory 1.50 GB at most")


def test_no_limit_and_no_cgroup(tmp_path):
    lines = []
    write(tmp_path, usec=0, periods=0, throttled=0, memory=0, limit="max 100000")
    assert usage.cpu_limit(tmp_path) is None
    missing = usage.Usage(lines.append, root=tmp_path / "nowhere")
    missing.tick()
    missing.tick()
    assert len(lines) == 1 and "can't be read" in lines[0]
    assert missing.summary() is None
