"""Task 11: the camera picture frames (docs/SPEC.md 3.3), without Docker."""
import base64
import io
import json

from PIL import Image

import pictures
from xebit_logic import decode_video


def test_crop_is_a_png_at_most_320_wide():
    shot = Image.new("RGB", (1279, 632), (20, 20, 20))
    shot.paste((217, 48, 37), (100, 100, 700, 500))
    png = pictures.crop_png(shot, {"x": 100, "y": 100, "width": 600, "height": 400})
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.width == 320 and img.height == 213
    assert img.convert("RGB").getpixel((160, 100)) == (217, 48, 37)
    assert pictures.crop_png(shot, {"x": 1270, "y": 10, "width": 100, "height": 100}) is None  # mostly off the page


def test_frame_reads_like_xebit():
    raw = pictures.frame("xbot_1", b"\x89PNG...", {"id": "spaces/x/devices/1", "name": "Asha", "is_bot": False})
    got = decode_video(raw)
    assert got == {"participant_id": "spaces/x/devices/1", "participant_name": "Asha", "type": "webcam",
                   "buffer": base64.b64encode(b"\x89PNG...").decode()}
    data = json.loads(raw)
    assert data["data"]["bot"]["id"] == "xbot_1" and data["data"]["data"]["timestamp"]["absolute"].endswith("Z")
    bot = pictures.frame("xbot_1", b"x", {"id": "me", "name": "Maya", "is_bot": True})
    assert decode_video(bot) is None


def test_safe_file_names():
    assert pictures.safe_name("Asha / ../x") == "Asha _ .._x"
    assert pictures.safe_name("   ") == "person"


def test_the_bots_own_view_over_a_tile_is_painted_over():
    shot = Image.new("RGB", (1279, 632), (217, 48, 37))
    shot.paste((240, 240, 240), (500, 300, 600, 380))   # the bot's self-view, floating over the tile
    png = pictures.crop_png(shot, {"x": 100, "y": 100, "width": 600, "height": 300},
                            cover=[{"x": 500, "y": 300, "width": 100, "height": 80}])
    img = Image.open(io.BytesIO(png)).convert("RGB")
    assert (240, 240, 240) not in {c for _, c in img.getcolors(100000)}
    assert img.getpixel((10, 10)) == (217, 48, 37)


class Person:
    def __init__(self, pid, name):
        self.id = self.sent_id = pid
        self.name = name


class Rep:
    def __init__(self, tmp_path):
        self.run_dir = tmp_path
        self.lines = []

    def log(self, m):
        self.lines.append(m)


def test_every_round_sends_every_tile_and_keeps_the_latest_picture(tmp_path):
    """Pictures come from screen grabs on their own thread: twice a second for every tile on screen, changed
    or not (a camera-off tile never changes), and camera/<name>.png is there from the first picture."""
    sent = []
    people = type("P", (), {"roster": type("R", (), {"present": {"p1": Person("p1", "Ravi")}, "self_ids": set()})()})()
    grabs = []

    def grabber():
        def grab(x, y, w, h):
            grabs.append((x, y, w, h))
            return Image.new("RGB", (w, h), (30, 142, 62))
        return grab

    cam = pictures.CameraPictures(None, people, "xbot_1", "ws://x", Rep(tmp_path), origin=(0, 88), grabber=grabber,
                                  connect=lambda url, timeout: type("W", (), {"send": lambda s, t: sent.append(t),
                                                                              "settimeout": lambda s, t: None,
                                                                              "recv": lambda s: (_ for _ in ()).throw(OSError()),
                                                                              "close": lambda s, **k: None})())
    cam._stop.set()  # drive the rounds by hand
    box = {"x": 10, "y": 20, "width": 200, "height": 120}
    cam.boxes = lambda: ([("p1", box, "webcam")], [])
    cam.tick()                     # the first read: not yet known to stand still
    assert cam.targets == []
    cam.last_read = 0
    cam.tick()                     # the same box twice: pictures from now on
    import time as t
    for _ in range(50):
        if cam.socket.connected:
            break
        t.sleep(0.02)

    def read_then_round():
        cam.last_read = 0
        cam.tick()                 # a read after the last grab confirms the tile was still there
        cam._send_round(cam.grab)  # sends what is confirmed, grabs again

    for _ in range(3):
        read_then_round()
    assert grabs[0] == (10, 108, 201, 121), "the grab is the tile's place on the screen (below Chrome's bars)"
    assert (tmp_path / "camera" / "Ravi.png").exists()
    frames = [json.loads(x) for x in sent]
    assert frames and all(f["data"]["data"]["participant"]["name"] == "Ravi" for f in frames)
    assert len(frames) >= 2, "an unchanged picture is sent every round"
    # A tile that moves between the grab and the next read: that picture is never sent.
    before = len(sent)
    cam._send_round(cam.grab)
    cam.boxes = lambda: ([("p1", {**box, "x": 300}, "webcam")], [])
    cam.last_read = 0
    cam.tick()
    cam._send_round(cam.grab)
    assert len(sent) == before
    cam.invalidate()
    assert cam.targets == [] and cam.held == []
    cam.stop()
