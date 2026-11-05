"""See the real page (docs/PLAN.md task R1): `--save-dom`, off by default in service mode, on in ./try.sh.

Saves what Meet's page looks like during the call, so a real run shows the real structure of the tiles,
the video elements, the captions and the speaking indicator:

- `dom/NNN-HHMMSS-<why>.html`: `page.content()` (the whole page's HTML);
- `dom/NNN-HHMMSS-<why>.aria.yml`: Playwright's `aria_snapshot()` of the body (roles, names, texts).

Both are read in Playwright's isolated (utility) world, never in Meet's page world. When: at
`in_call_recording`, after the captions turn on, at every people change, at the start and stop of a
screen share, and every 30 s. Any failure is logged and never touches the call.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

EVERY = 30.0
MIN_GAP = 2.0  # several reasons at once (a join during a share start) give one snapshot


class DomSaver:
    def __init__(self, page, rep, every: float = EVERY):
        self.page = page
        self.rep = rep
        self.every = every
        self.folder = rep.run_dir / "dom"
        self.folder.mkdir(exist_ok=True)
        self.count = 0
        self.last = 0.0
        self.next_periodic = time.monotonic() + every
        self.failures = 0
        self.pending: list[str] = []
        rep.log("dom: saving Meet's page (HTML and accessibility tree) to dom/ at every change and every 30 s")

    def request(self, why: str) -> None:
        """Ask for a snapshot at the next tick (cheap; safe to call from anywhere in the main thread)."""
        if why not in self.pending:
            self.pending.append(why)

    def save(self, why: str) -> bool:
        if self.failures >= 5:
            return False
        self.count += 1
        self.last = time.monotonic()
        stem = self.folder / f"{self.count:03d}-{datetime.now():%H%M%S}-{re.sub(r'[^a-z0-9_-]+', '_', why.lower())}"
        started = time.perf_counter()
        try:
            stem.with_suffix(".html").write_text(self.page.content())
            stem.with_suffix(".aria.yml").write_text(self.page.locator("body").aria_snapshot(timeout=5_000))
            self.failures = 0
        except Exception as exc:  # noqa: BLE001 - a debug feature must never touch the call
            self.failures += 1
            self.rep.log(f"dom: snapshot '{why}' failed ({type(exc).__name__}: {str(exc)[:120]})")
            if self.failures >= 5:
                self.rep.log("dom: 5 failures in a row; no more page snapshots in this run")
            return False
        took = (time.perf_counter() - started) * 1000
        if self.count <= 2 or took > 1500:
            self.rep.log(f"dom: saved {stem.name} ({took:.0f} ms)")
        return True

    def tick(self) -> None:
        now = time.monotonic()
        if self.pending and now - self.last >= MIN_GAP:
            why = "+".join(self.pending)
            self.pending = []
            self.save(why)
        elif now >= self.next_periodic:
            self.next_periodic = now + self.every
            self.save("every_30s")

    def stop(self) -> None:
        self.rep.log(f"dom: {self.count} page snapshots in dom/")
