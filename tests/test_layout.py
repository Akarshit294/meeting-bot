"""Task 1: the repo layout the rest of the build relies on."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_layout_exists():
    for path in ("manager", "bot/meet_bot.py", "bot/Dockerfile", "docs/SPEC.md", "docs/PLAN.md", "STATUS.md"):
        assert (ROOT / path).exists(), path


def test_env_files_are_ignored():
    ignored = (ROOT / ".gitignore").read_text().split()
    assert ".env" in ignored and "output/" in ignored
