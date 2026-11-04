"""Each person's camera picture to xebit: the `video_separate_png.data` websocket (docs/SPEC.md 3.3).

About twice a second: ONE screenshot of Meet's page (no code runs in Meet: Playwright takes it through
the DevTools protocol), then each person's tile is cut out by its box (from locators), scaled to at most
320 px wide and sent as a PNG. A person whose picture hasn't changed is skipped (but re-sent every 5 s).
Only people in the roster are sent, so never the bot itself. The latest picture of each person is kept
in the run folder (`camera/<name>.png`).

This runs on its own: any failure is logged and must never touch the call, the sound or the screen share.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
from datetime import datetime, timezone

from PIL import Image

from audio import Socket

EVERY = 0.5
MAX_WIDTH = 320
RESEND_AFTER = 5.0
SAVE_EVERY = 2.0
PRESENTATION = re.compile(r"\bpresent(ing|ation)\b", re.I)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def frame(bot_id: str, png: bytes, participant: dict, kind: str = "webcam") -> str:
    return json.dumps({
        "event": "video_separate_png.data",
        "data": {
            "data": {
                "buffer": base64.b64encode(png).decode(),
                "type": kind,
                "participant": participant,
                "timestamp": {"absolute": iso_now()},
            },
            "bot": {"id": bot_id},
        },
    })


def crop_png(shot: Image.Image, box: dict, max_width: int = MAX_WIDTH) -> bytes | None:
    left, top = max(0, int(box["x"])), max(0, int(box["y"]))
    right = min(shot.width, int(box["x"] + box["width"]))
    bottom = min(shot.height, int(box["y"] + box["height"]))
    if right - left < 16 or bottom - top < 16:
        return None
    tile = shot.crop((left, top, right, bottom))
    if tile.width > max_width:
        tile = tile.resize((max_width, max(1, round(tile.height * max_width / tile.width))), Image.BILINEAR)
    out = io.BytesIO()
    tile.save(out, format="PNG", optimize=False, compress_level=3)
    return out.getvalue()


def safe_name(name: str) -> str:
    return re.sub(r"[^\w .-]+", "_", name).strip() or "person"


class CameraPictures:
    def __init__(self, page, people, bot_id: str, url: str, rep, connect=None):
        self.page = page
        self.people = people
        self.bot_id = bot_id
        self.rep = rep
        self.socket = Socket(url, rep.log, "pictures", **({"connect": connect} if connect else {}))
        self.last_tick = 0.0
        self.last_sent: dict[str, tuple[str, float]] = {}
        self.last_saved: dict[str, float] = {}
        self.sent: dict[str, int] = {}
        self.folder = rep.run_dir / "camera"
        self.folder.mkdir(exist_ok=True)
        self.cost: list[float] = []
        self.last_note = time.monotonic()
        rep.log("pictures: sending each person's camera picture about twice a second (video_separate_png.data)")

    def boxes(self) -> list[tuple[str, dict, str]]:
        """(participant id, box, kind) of each visible tile of a person in the roster."""
        present = self.people.roster.present if self.people else {}
        tiles = self.page.locator("[data-participant-id]")
        out, seen = [], set()
        for i in range(min(tiles.count(), 40)):
            tile = tiles.nth(i)
            try:
                pid = tile.get_attribute("data-participant-id", timeout=500)
                if not pid or pid in seen or pid not in present or not tile.is_visible():
                    continue
                box = tile.bounding_box(timeout=500)
            except Exception:  # noqa: BLE001 - a tile can vanish while we read it
                continue
            if box:
                seen.add(pid)
                label = tile.get_attribute("aria-label", timeout=500) or ""
                out.append((pid, box, "screenshare" if PRESENTATION.search(label) else "webcam"))
        return out

    def tick(self) -> None:
        now = time.monotonic()
        if now - self.last_tick < EVERY:
            return
        self.last_tick = now
        started = time.perf_counter()
        boxes = self.boxes()
        if not boxes:
            return
        # JPEG: much faster for Chrome to make than PNG (the same pixels still give the same bytes, so an
        # unchanged picture is still skipped). caret="initial": Playwright then adds no style to the page.
        shot = Image.open(io.BytesIO(self.page.screenshot(type="jpeg", quality=90, scale="css", caret="initial")))
        shot = shot.convert("RGB")
        present = self.people.roster.present
        for pid, box, kind in boxes:
            png = crop_png(shot, box)
            person = present.get(pid)
            if png is None or person is None:
                continue
            digest = hashlib.blake2b(png, digest_size=12).hexdigest()
            previous = self.last_sent.get(pid)
            if previous and previous[0] == digest and now - previous[1] < RESEND_AFTER:
                continue
            participant = {"id": pid, "name": person.name, "is_bot": False}
            if self.socket.send(frame(self.bot_id, png, participant, kind)):
                self.last_sent[pid] = (digest, now)
                self.sent[person.name] = self.sent.get(person.name, 0) + 1
            if now - self.last_saved.get(pid, 0) > SAVE_EVERY:
                self.last_saved[pid] = now
                (self.folder / f"{safe_name(person.name)}.png").write_bytes(png)
        self.cost.append(time.perf_counter() - started)
        if time.monotonic() - self.last_note > 60:
            self.last_note = time.monotonic()
            self.rep.log("pictures: " + self.summary())

    def summary(self) -> str:
        sent = ", ".join(f"{name} {n}" for name, n in sorted(self.sent.items())) or "none"
        avg = sum(self.cost[-100:]) / max(1, len(self.cost[-100:])) * 1000
        return f"sent {sent}; one tick takes {avg:.0f} ms"

    def stop(self) -> None:
        self.socket.close()
        self.rep.log("pictures: stopped; " + self.summary())
