"""Each person's camera picture to xebit: the `video_separate_png.data` websocket (docs/SPEC.md 3.3).

About twice a second for every person whose tile is on screen: each person's tile is cut out of ONE grab
of the screen, scaled to at most 320 px wide and sent as a PNG. Only people in the roster are sent, so
never the bot itself; where the bot's own small self-view sits over someone's tile, it is painted over.
The latest picture of each person is kept in the run folder (`camera/<name>.png`, from the first picture
on, then every 2 s).

Round 2 (docs/PLAN.md R4), after the first real test sent only 5 pictures in 45 s, and none in a run where
the bot was busy starting a screen share:
- every picture is sent, changed or not (the old "skip an unchanged picture, re-send every 5 s" rule meant
  one picture every 5 s for a person with the camera off, whose tile never changes);
- the pictures no longer wait for the bot's main loop: a thread of their own grabs the screen from the X
  server (no Playwright, no code in Meet) and cuts the tiles at the boxes the main loop last read (with
  locators, about twice a second). A picture is sent only if the tile had the same box at the read before
  the grab and at the read after it (a picture cut while the layout moves would show part of another tile,
  or whoever moved into a leaver's place), and boxes are dropped whenever the bot itself changes the layout
  (screen share, People panel, captions). So a picture leaves up to half a second after it was taken, with
  the time it was taken.
If the screen can't be grabbed (no X display), pictures fall back to a Playwright screenshot of Meet's page
in the main loop, as before.

This runs on its own: any failure is logged and must never touch the call, the sound or the screen share.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

from PIL import Image, ImageDraw

from audio import Socket

EVERY = 0.5
READ_EVERY = 0.25
MAX_WIDTH = 320
SAVE_EVERY = 2.0
MAX_AGE = 10.0
MEET_BACKGROUND = (32, 33, 36)
PRESENTATION = re.compile(r"\bpresent(ing|ation)\b", re.I)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def frame(bot_id: str, png: bytes, participant: dict, kind: str = "webcam", stamp: str | None = None) -> str:
    return json.dumps({
        "event": "video_separate_png.data",
        "data": {
            "data": {
                "buffer": base64.b64encode(png).decode(),
                "type": kind,
                "participant": participant,
                "timestamp": {"absolute": stamp or iso_now()},
            },
            "bot": {"id": bot_id},
        },
    })


def crop_png(shot: Image.Image, box: dict, max_width: int = MAX_WIDTH, cover: list[dict] = ()) -> bytes | None:
    """The tile at `box` (in the shot's coordinates) as a PNG, with the `cover` boxes painted over."""
    left, top = max(0, int(box["x"])), max(0, int(box["y"]))
    right = min(shot.width, int(box["x"] + box["width"]))
    bottom = min(shot.height, int(box["y"] + box["height"]))
    if right - left < 16 or bottom - top < 16:
        return None
    tile = shot.crop((left, top, right, bottom))
    if cover:
        draw = ImageDraw.Draw(tile)
        for c in cover:
            x0, y0 = max(0, int(c["x"]) - left), max(0, int(c["y"]) - top)
            x1, y1 = min(tile.width, int(c["x"] + c["width"]) - left), min(tile.height, int(c["y"] + c["height"]) - top)
            if x1 > x0 and y1 > y0:
                draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=MEET_BACKGROUND)
    if tile.width > max_width:
        tile = tile.resize((max_width, max(1, round(tile.height * max_width / tile.width))), Image.BILINEAR)
    out = io.BytesIO()
    tile.save(out, format="PNG", optimize=False, compress_level=3)
    return out.getvalue()


def safe_name(name: str) -> str:
    return re.sub(r"[^\w .-]+", "_", name).strip() or "person"


def x_grabber():
    """A function that grabs part of the X screen as a PIL image (its own X connection: thread-safe)."""
    from Xlib import X
    from Xlib import display as xdisplay

    display = xdisplay.Display(os.environ.get("DISPLAY"))
    root = display.screen().root
    geo = root.get_geometry()

    def grab(x: int, y: int, w: int, h: int) -> Image.Image:
        x, y = max(0, x), max(0, y)
        w, h = min(w, geo.width - x), min(h, geo.height - y)
        raw = root.get_image(x, y, w, h, X.ZPixmap, 0xFFFFFFFF)
        data = raw.data if isinstance(raw.data, bytes) else bytes(raw.data)
        return Image.frombuffer("RGB", (w, h), data, "raw", "BGRX", 0, 1)

    return grab


def _overlaps(a: dict, b: dict) -> bool:
    return a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"] and \
        a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"]


class CameraPictures:
    def __init__(self, page, people, bot_id: str, url: str, rep, origin=(0, 0), connect=None, grabber=x_grabber):
        self.page = page
        self.people = people
        self.bot_id = bot_id
        self.rep = rep
        self.origin = origin or (0, 0)
        self.socket = Socket(url, rep.log, "pictures", **({"connect": connect} if connect else {}))
        self.last_read = 0.0
        self.last_boxes: dict[str, tuple] = {}
        self.last_saved: dict[str, float] = {}
        self.sent: dict[str, int] = {}
        self.folder = rep.run_dir / "camera"
        self.folder.mkdir(exist_ok=True)
        self.cost: list[float] = []
        self.last_note = time.monotonic()
        self.lock = threading.Lock()
        self.targets: list[tuple] = []  # (participant, kind, box in page coordinates, self boxes over it)
        self.targets_at = 0.0
        self.read_at = 0.0
        self.held: list[tuple] = []  # grabbed pictures waiting for the next read to confirm the tile was still there
        self.ready: list[tuple] = []  # confirmed, to send at the next round
        self.dropped = {"layout": 0, "moved": 0, "late": 0}
        self.rounds = 0
        self._stop = threading.Event()
        self.thread = None
        self.grab = None
        try:
            self.grab = grabber() if grabber else None
        except Exception as exc:  # noqa: BLE001 - no X display: the main loop takes the pictures
            rep.log(f"pictures: can't grab the screen ({type(exc).__name__}: {exc}); using page screenshots instead")
        if self.grab:
            self.thread = threading.Thread(target=self._run, name="pictures", daemon=True)
            self.thread.start()
        rep.log("pictures: sending each person's camera picture about twice a second (video_separate_png.data)"
                + (", from screen grabs on their own thread" if self.grab else ""))

    # -- main thread: where the tiles are --

    def boxes(self) -> tuple[list[tuple[str, dict, str]], list[dict]]:
        """(participant id, box, kind) of each visible tile of a person in the roster, and the boxes of the
        bot's own tiles."""
        present = self.people.roster.present if self.people else {}
        self_ids = self.people.roster.self_ids if self.people else set()
        tiles = self.page.locator("[data-participant-id]")
        out, own, seen = [], [], set()
        for i in range(min(tiles.count(), 40)):
            tile = tiles.nth(i)
            try:
                pid = tile.get_attribute("data-participant-id", timeout=500)
                if not pid or pid in seen or (pid not in present and pid not in self_ids) or not tile.is_visible():
                    continue
                box = tile.bounding_box(timeout=500)
            except Exception:  # noqa: BLE001 - a tile can vanish while we read it
                continue
            if not box:
                continue
            seen.add(pid)
            if pid in self_ids:
                own.append(box)
                continue
            label = tile.get_attribute("aria-label", timeout=500) or ""
            out.append((pid, box, "screenshare" if PRESENTATION.search(label) else "webcam"))
        return out, own

    def invalidate(self) -> None:
        """The layout is about to change (the bot clicks a screen share, the People panel, captions; or someone
        came or went): no new picture until the tiles have stood still again. The pictures already grabbed are
        checked against the tiles as they are right now, before the change: those still right are sent."""
        try:
            read_at = time.monotonic()
            found, _ = self.boxes()
            keys = {pid: tuple(round(box[k]) for k in ("x", "y", "width", "height")) for pid, box, _ in found}
        except Exception:  # noqa: BLE001
            read_at, keys = 0.0, {}
        with self.lock:
            for item in self.held:
                grabbed, participant, _kind, key, _png, _stamp = item
                if grabbed < read_at and keys.get(participant["id"]) == key:
                    self.ready.append(item)
                else:
                    self.dropped["layout"] += 1
            self.held = []
            self.targets = []
            self.last_boxes = {}

    def tick(self) -> None:
        now = time.monotonic()
        # Read the boxes 4 times a second: each read also confirms the pictures grabbed before it.
        if now - self.last_read < READ_EVERY:
            return
        self.last_read = now
        read_started = time.monotonic()
        found, own = self.boxes()
        # Only tiles that are where they were at the last read: a picture cut while the layout moves (someone
        # joins or leaves, a share starts) would show part of another tile.
        keys = {pid: tuple(round(box[k]) for k in ("x", "y", "width", "height")) for pid, box, _ in found}
        present = self.people.roster.present
        targets = []
        for pid, box, kind in found:
            person = present.get(pid)
            if person is None or self.last_boxes.get(pid) != keys[pid]:
                continue
            participant = {"id": person.sent_id, "name": person.name, "is_bot": False}
            targets.append((participant, kind, box, [o for o in own if _overlaps(o, box)]))
        with self.lock:
            self.last_boxes = keys
            self.targets, self.targets_at = targets, time.monotonic()
            self.read_at = read_started  # pictures grabbed before this read can now be checked against it
        if not self.grab:  # no screen grabs: take the pictures here, from a screenshot of the page
            self._send_round(None)
        elif self.thread is None:  # (tests) no thread: one round per read
            self._send_round(self.grab)
        if time.monotonic() - self.last_note > 60:
            self.last_note = time.monotonic()
            self.rep.log("pictures: " + self.summary())

    # -- the pictures (their own thread, or the main loop without X) --

    def _run(self) -> None:
        failures = 0
        while not self._stop.wait(EVERY):
            try:
                self._send_round(self.grab)
                failures = 0
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures in (1, 20):
                    self.rep.log(f"pictures: round failed ({type(exc).__name__}: {str(exc)[:120]})")

    def _send_round(self, grab) -> None:
        """Send the pictures a later read has confirmed, then grab new ones.

        A picture is sent only if the person's tile had the same box at the read before the grab (the targets)
        AND at the first read after it: so the tile stood still all along. Without the second check, a tile that
        moves away between two reads (someone leaves and the others shift) would give a picture of whoever
        moved into its place."""
        with self.lock:
            targets, age = list(self.targets), time.monotonic() - self.targets_at
            read_at, keys = self.read_at, dict(self.last_boxes)
            held, self.held = self.held, []
            ready, self.ready = self.ready, []
        for _grabbed, participant, kind, _key, png, stamp in ready:
            self._send(participant, kind, png, stamp)
        keep = []
        for item in held:
            grabbed, participant, kind, key, png, stamp = item
            if read_at <= grabbed:
                if time.monotonic() - grabbed < MAX_AGE:
                    keep.append(item)  # not confirmed yet
                else:
                    self.dropped["late"] += 1
                continue
            if keys.get(participant["id"]) == key:
                self._send(participant, kind, png, stamp)
            else:
                self.dropped["moved"] += 1
        self.rounds += 1
        if not targets or age > MAX_AGE:
            self.empty_rounds = getattr(self, "empty_rounds", 0) + 1
            with self.lock:
                self.held = keep + self.held
            return
        started = time.perf_counter()
        grabbed, stamp = time.monotonic(), iso_now()
        ox, oy = self.origin
        if grab:
            left = int(min(t[2]["x"] for t in targets))
            top = int(min(t[2]["y"] for t in targets))
            right = int(max(t[2]["x"] + t[2]["width"] for t in targets)) + 1
            bottom = int(max(t[2]["y"] + t[2]["height"] for t in targets)) + 1
            shot = grab(int(ox) + left, int(oy) + top, right - left, bottom - top)
            shift = (left, top)
        else:
            # caret="initial": Playwright then adds no style to the page. JPEG: much faster for Chrome to make.
            shot = Image.open(io.BytesIO(self.page.screenshot(type="jpeg", quality=90, scale="css", caret="initial")))
            shot = shot.convert("RGB")
            shift = (0, 0)
        for participant, kind, box, cover in targets:
            moved = lambda b: {**b, "x": b["x"] - shift[0], "y": b["y"] - shift[1]}
            png = crop_png(shot, moved(box), cover=[moved(c) for c in cover])
            if png is not None:
                key = tuple(round(box[k]) for k in ("x", "y", "width", "height"))
                keep.append((grabbed, participant, kind, key, png, stamp))
        del keep[:-60]  # at most a few seconds' worth wait for a read
        with self.lock:
            self.held = keep + self.held
        self.cost.append(time.perf_counter() - started)
        del self.cost[:-200]

    def _send(self, participant: dict, kind: str, png: bytes, stamp: str) -> None:
        name = participant["name"]
        if self.socket.send(frame(self.bot_id, png, participant, kind, stamp)):
            self.sent[name] = self.sent.get(name, 0) + 1
        now = time.monotonic()
        if now - self.last_saved.get(participant["id"], -1e9) >= SAVE_EVERY:
            self.last_saved[participant["id"]] = now
            self._save(name, png)

    def _save(self, name: str, png: bytes) -> None:
        path = self.folder / f"{safe_name(name)}.png"
        tmp = path.with_suffix(".png.tmp")
        try:
            tmp.write_bytes(png)
            os.replace(tmp, path)  # never a half-written picture
        except OSError as exc:
            self.rep.log(f"pictures: {path.name} not saved ({exc})")

    def summary(self) -> str:
        sent = ", ".join(f"{name} {n}" for name, n in sorted(self.sent.items())) or "none"
        recent = self.cost[-100:]
        avg = sum(recent) / max(1, len(recent)) * 1000
        dropped = ", ".join(f"{k} {v}" for k, v in self.dropped.items())
        return (f"sent {sent}; one round takes {avg:.0f} ms; {self.rounds} rounds, "
                f"{getattr(self, 'empty_rounds', 0)} with no tile known to stand still; not sent: {dropped}")

    def stop(self) -> None:
        self._stop.set()
        if self.thread:
            self.thread.join(3)
        self.socket.close()
        self.rep.log("pictures: stopped; " + self.summary())
