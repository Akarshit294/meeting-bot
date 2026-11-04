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
