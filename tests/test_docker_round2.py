"""Round 2 (docs/PLAN.md R1 to R4): one bot in the FAKE meeting with every test option on, like ./try.sh
(needs Docker; `pytest -m docker`).

The fake meeting plays each person through 3 Meet-like <audio> slots (Asha 440 Hz, Ravi 660 Hz, Meera
880 Hz), shows who is speaking, and Meera joins at 20 s, Ravi leaves at 40 s. The bot shares its screen
for a few seconds, then leaves at about 52 s. Then the run folder is checked:
R1 - dom/ snapshots of the page.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dockerstack import (API, ROOT, TESTKIT, api_key, codes, compose, remove_bot_containers, request, request_text,
                         wait_http, wait_status)

pytestmark = pytest.mark.docker
MEET = "https://meet.google.com/abc-defg-hij"
BOT_NAME = "Maya Test"
SCREEN = "http://testkit:9000/screen.html?session_id=s1&token=SECRET2"


def kit(path, body=None):
    return request("POST" if body is not None else "GET", path, body, base=TESTKIT)[1]


@pytest.fixture(scope="module")
def run():
    compose("up", "-d", "--build", env={"TEST_MEET_URL": "http://testkit:9000/meet/abc-defg-hij?audio=perperson"})
    wait_http(API + "/health")
    wait_http(TESTKIT + "/health")
    key = api_key()
    kit("/api/reset", {})
    status, bot = request("POST", "/api/v1/bot/", {
        "meeting_url": MEET, "bot_name": BOT_NAME,
        "output_media": {"camera": {"kind": "webpage", "config": {"url": "http://testkit:9000/camera.html?token=SECRET"}}},
        "recording_config": {"realtime_endpoints": [
            {"type": "webhook", "url": "http://testkit:9000/hooks/call-events", "events": ["participant_events.join", "participant_events.leave"]},
            {"type": "websocket", "url": "ws://testkit:9000/ws/audio", "events": ["audio_separate_raw.data"]},
            {"type": "websocket", "url": "ws://testkit:9000/ws/video", "events": ["video_separate_png.data"]}]},
        "xbot_options": {"save_dom": True, "save_media": True, "record_screen": True},
    }, key=key)
    assert status == 201, bot
    bot_id = bot["id"]
    seen = {"bot_id": bot_id}
    try:
        wait_status(key, bot_id, "in_call_recording", timeout=180)
        seen["recording_at"] = time.time()
        seen["run_dir"] = request_text(f"/testrx/run-dir?bot={bot_id}", key)
        admitted = kit("/api/state")["admitted_at"] / 1000
        seen["admitted_at"] = admitted
        while time.time() < admitted + 12:
            time.sleep(1)
        seen["share_start"] = request("POST", f"/api/v1/bot/{bot_id}/output_media/",
                                      {"screenshare": {"kind": "webpage", "config": {"url": SCREEN}}}, key=key)
        time.sleep(7)
        seen["share_on"] = kit("/api/state")["share"]
        seen["share_stop"] = request("DELETE", f"/api/v1/bot/{bot_id}/output_media/", {"screenshare": True}, key=key)
        time.sleep(3)
        seen["share_off"] = kit("/api/state")["share"]
        while time.time() < admitted + 52:
            time.sleep(1)
        seen["video"], seen["video_t"] = kit("/api/video"), time.time()
        seen["meet_state"] = kit("/api/state")
        seen["left_at"] = time.time()
        assert request("POST", f"/api/v1/bot/{bot_id}/leave_call/", key=key)[0] == 200
        seen["final"] = wait_status(key, bot_id, "done", timeout=120)
        seen["hooks"] = kit("/api/hooks")
    finally:
        status, bot = request("GET", f"/api/v1/bot/{bot_id}/", key=key)
        if codes(bot) and codes(bot)[-1] not in ("done", "fatal"):
            request("POST", f"/api/v1/bot/{bot_id}/leave_call/", key=key)
            time.sleep(10)
        remove_bot_containers()
        compose("up", "-d", "manager")  # back to the default fake meeting
    seen["dir"] = ROOT / "output" / "test" / Path(seen["run_dir"]).name
    return seen


def test_the_call_went_as_planned(run):
    assert codes(run["final"])[-2:] == ["call_ended", "done"]
    assert run["share_start"][0] == 200 and run["share_on"]["active"] is True


# ---- R1: the real page, saved ----

def test_dom_snapshots(run):
    folder = run["dir"] / "dom"
    html = sorted(folder.glob("*.html"))
    aria = sorted(folder.glob("*.aria.yml"))
    assert html and len(html) == len(aria), (html, aria)
    reasons = " ".join(p.stem for p in html)
    for why in ("in_call_recording", "people", "share_start", "share_stop", "every_30s"):
        assert why in reasons, f"no snapshot for {why}: {reasons}"
    first = html[0].read_text()
    assert "data-participant-id" in first and "Leave call" in first
    tree = aria[0].read_text()
    assert 'button "Leave call"' in tree, tree[:500]
    assert "Asha" in tree
