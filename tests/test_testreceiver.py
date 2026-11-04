"""Task 9: the manager's test pages and the small receiver ./try.sh reads (no xebit needed)."""
from __future__ import annotations

import base64
import struct

from fastapi.testclient import TestClient

from manager.app import create_app
from manager.config import Settings
from manager.store import Store
from test_api import FakeRunner

KEY = "k"


def client(tmp_path):
    settings = Settings(api_key=KEY, state_file=tmp_path / "s.json")
    return TestClient(create_app(settings, FakeRunner(), Store(settings.state_file), watch=False))


def test_test_pages_are_served_without_a_key(tmp_path):
    c = client(tmp_path)
    voice = c.get("/test/voice.html")
    assert voice.status_code == 200 and "getUserMedia" in voice.text
    screen = c.get("/test/screen.html")
    assert screen.status_code == 200 and "<title>Xebit Screencast</title>" in screen.text


def test_receiver_counts_and_stats_need_the_key(tmp_path):
    c = client(tmp_path)
    bot = {"id": "xbot_1"}
    for event, pid, name in (("join", "p1", "Asha"), ("join", "p2", "Ravi"), ("leave", "p2", "Ravi")):
        c.post("/testrx/hooks", json={"event": f"participant_events.{event}",
                                      "data": {"bot": bot, "data": {"participant": {"id": pid, "name": name}}}})
    pcm = struct.pack("<3200h", *([8000, -8000] * 1600))
    with c.websocket_connect("/testrx/audio") as ws:
        for _ in range(5):
            ws.send_text('{"event":"audio_separate_raw.data","data":{"data":{"buffer":"%s","participant":{"id":"meeting-mix","name":"Meeting"}},"bot":{"id":"xbot_1"}}}'
                         % base64.b64encode(pcm).decode())
    with c.websocket_connect("/testrx/video") as ws:
        ws.send_text('{"event":"video_separate_png.data","data":{"data":{"buffer":"x","participant":{"id":"p1","name":"Asha"}},"bot":{"id":"xbot_1"}}}')
    c.post("/testrx/voice?tag=t1", json={"heard_db": -30})
    assert c.get("/testrx/stats?bot=xbot_1").status_code == 401
    stats = c.get("/testrx/stats?bot=xbot_1&tag=t1", headers={"Authorization": f"Token {KEY}"}).json()
    assert stats["present"] == ["Asha"] and len(stats["joins"]) == 2 and len(stats["leaves"]) == 1
    assert stats["audio_frames"] == 5 and -13 < stats["audio_db"] < -11
    assert stats["audio_participants"] == {"Meeting": 5}
    assert stats["pictures"] == {"Asha": 1}
    assert stats["voice"]["heard_db"] == -30
