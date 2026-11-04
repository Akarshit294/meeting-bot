"""Tasks 5 to 8: one bot, in its container, in the FAKE meeting (needs Docker; `pytest -m docker`).

The bot joins through the real join code (same clicks, typing and state reading), then the test checks:
task 5 - joins for the people already there, never for the bot;
task 6 - sound both ways, and the frames xebit's own decoder reads;
task 7 - screen share on and off through the API;
task 8 - a join at ~20 s, a leave ~10 s after the removal at 40 s, and the end order.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime

import numpy as np
import pytest

from dockerstack import API, TESTKIT, api_key, codes, compose, container_exists, remove_bot_containers, request, wait_http, wait_status
from xebit_logic import HeadCount, _decode_recall, _is_bot_participant, _latest_recall_status_code, decode_video

pytestmark = pytest.mark.docker
MEET = "https://meet.google.com/abc-defg-hij"
BOT_NAME = "Maya Test"
SCREEN = "http://testkit:9000/screen.html?session_id=s1&token=SECRET2"


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def kit(path: str, body=None, method=None):
    return request(method or ("POST" if body is not None else "GET"), path, body, base=TESTKIT)[1]


def spectrum_peak(pcm: bytes, rate: int = 16000) -> tuple[float, np.ndarray, np.ndarray]:
    samples = np.frombuffer(pcm, dtype="<i2").astype(float)
    spec = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), 1 / rate)
    return float(freqs[spec.argmax()]), spec, freqs


def band(spec, freqs, f, width=15.0) -> float:
    return float(spec[(freqs > f - width) & (freqs < f + width)].max())


@pytest.fixture(scope="module")
def run():
    """One bot through the whole fake meeting. Returns everything observed along the way."""
    compose("up", "-d", "--build")
    wait_http(API + "/health")
    wait_http(TESTKIT + "/health")
    key = api_key()
    kit("/api/reset", {})
    body = {
        "meeting_url": MEET, "bot_name": BOT_NAME, "metadata": {"demo_script_id": "d1"},
        "output_media": {"camera": {"kind": "webpage", "config": {"url": "http://testkit:9000/camera.html?session_id=s1&token=SECRET"}}},
        "recording_config": {"realtime_endpoints": [
            {"type": "webhook", "url": "http://testkit:9000/hooks/transcript", "events": ["transcript.data", "transcript.partial_data"]},
            {"type": "webhook", "url": "http://testkit:9000/hooks/call-events", "events": ["participant_events.join", "participant_events.leave"]},
            {"type": "websocket", "url": "ws://testkit:9000/ws/video?token=V", "events": ["video_separate_png.data"]},
            {"type": "websocket", "url": "ws://testkit:9000/ws/audio?token=A", "events": ["audio_separate_raw.data"]},
        ]},
    }
    status, bot = request("POST", "/api/v1/bot/", body, key=key)
    assert status == 201, bot
    bot_id = bot["id"]
    seen = {"bot_id": bot_id, "key": key}
    try:
        bot = wait_status(key, bot_id, "in_call_recording", timeout=180)
        seen["recording_at"] = time.time()
        time.sleep(6)
        seen["meet_state"] = kit("/api/state")
        seen["camera_state"] = kit("/api/camera-state")
        seen["audio"] = kit("/api/audio?last=40")

        # screen share on, check, replace, off
        t0 = time.time()
        seen["share_start"] = request("POST", f"/api/v1/bot/{bot_id}/output_media/",
                                      {"screenshare": {"kind": "webpage", "config": {"url": SCREEN}}}, key=key)
        seen["share_took"] = time.time() - t0
        time.sleep(5)
        seen["share_on"] = kit("/api/state")["share"]
        seen["share_replace"] = request("POST", f"/api/v1/bot/{bot_id}/output_media/",
                                        {"screenshare": {"kind": "webpage", "config": {"url": SCREEN + "&bg=blue"}}}, key=key)
        time.sleep(4)
        seen["share_replaced"] = kit("/api/state")["share"]
        seen["share_stop"] = request("DELETE", f"/api/v1/bot/{bot_id}/output_media/", {"screenshare": True}, key=key)
        time.sleep(3)
        seen["share_off"] = kit("/api/state")["share"]

        # wait for the leave ~10 s after the removal at 40 s
        admitted = kit("/api/state")["admitted_at"] / 1000
        seen["admitted_at"] = admitted
        while time.time() < admitted + 62:
            time.sleep(2)
        seen["video"] = kit("/api/video?frames=2")
        seen["hooks"] = kit("/api/hooks")
        seen["camera_before_leave"] = kit("/api/camera-state")

        assert request("POST", f"/api/v1/bot/{bot_id}/leave_call/", key=key)[0] == 200
        seen["final"] = wait_status(key, bot_id, "done", timeout=90)
        time.sleep(3)
        seen["camera_after"] = kit("/api/camera-state")
        seen["meet_after"] = kit("/api/state")
        for _ in range(20):
            if not container_exists(bot_id):
                break
            time.sleep(1)
        seen["container_gone"] = not container_exists(bot_id)
    finally:
        status, bot = request("GET", f"/api/v1/bot/{bot_id}/", key=key)
        seen["last_bot"] = bot
        if codes(bot) and codes(bot)[-1] not in ("done", "fatal"):
            request("POST", f"/api/v1/bot/{bot_id}/leave_call/", key=key)
            time.sleep(10)
        remove_bot_containers()
    return seen


# ---- task 5: the fake meeting, joined through the real join code ----

def test_joined_through_the_real_join_code(run):
    st = run["meet_state"]
    assert st["name"] == BOT_NAME, "the bot typed its name into 'Your name'"
    assert st["join_trusted"] is True, "Ask to join was clicked by real (VNC) input"
    assert all(trusted for *_, trusted in st["clicks"]), "every click on the page came from real input"
    assert codes(run["final"])[:4] == ["joining_call", "in_waiting_room", "in_call_not_recording", "in_call_recording"]


def test_clicks_in_the_call_are_released_at_once(run):
    holds = [h for h in run["meet_after"]["holds"] if h[0] != "prejoin"]
    assert holds, "the bot clicked in the call"
    assert all(ms < 1000 for _, ms, _ in holds), holds


def test_joins_for_people_already_there_not_the_bot(run):
    joins = [h["body"] for h in run["hooks"] if h["body"]["event"] == "participant_events.join"]
    names = [j["data"]["data"]["participant"]["name"] for j in joins]
    assert names[:2] == ["Asha", "Ravi"]
    assert BOT_NAME not in names
    for j in joins:
        p = j["data"]["data"]["participant"]
        assert j["data"]["bot"]["id"] == run["bot_id"]
        assert p["id"].startswith("spaces/fakeSpace/devices/") and p["id"] != "spaces/fakeSpace/devices/250"
        assert p["is_bot"] is False and not _is_bot_participant(p)
        epoch(j["data"]["data"]["timestamp"]["absolute"])
    assert all(h["path"] == "call-events" for h in run["hooks"]), "nothing goes to the transcript webhook in v1"


# ---- task 6: sound both ways ----

def test_meeting_hears_the_camera_page(run):
    st = run["meet_state"]
    assert st["mic_on"] is True, "the bot turned its microphone on"
    assert st["cam_on"] is False, "the camera stays off in v1"
    assert abs(st["mic"]["freq"] - 1000) <= 15, st["mic"]
    assert st["mic"]["level_db"] > -50, st["mic"]


def test_camera_page_hears_the_meeting(run):
    cam = run["camera_state"]
    assert cam["gum"] and not str(cam["gum"]).startswith("error"), cam
    assert cam["ctx"] == "running", "autoplay without a click"
    assert cam["level_db"] > -50, cam
    assert abs(cam["freq"] - 440) <= 15, cam


def test_audio_frames_decode_like_xebit(run):
    frames = [_decode_recall(raw, "s1") for raw in run["audio"]["frames"]]
    assert len(frames) >= 25 and all(frames)
    for f in frames:
        assert f.participant_id == "meeting-mix" and f.participant_name == "Meeting" and f.is_bot is False
        assert len(f.pcm) == 6400, "200 ms of 16 kHz mono s16le"
        assert f.captured_at is not None
    gaps = np.diff([f.captured_at for f in frames])
    assert 0.15 < float(np.median(gaps)) < 0.25
    assert abs(time.time() - frames[-1].captured_at) < 120
    raw = json.loads(run["audio"]["frames"][-1])
    assert raw["data"]["bot"]["id"] == run["bot_id"]


def test_audio_is_the_meeting_and_not_the_bot(run):
    frames = [_decode_recall(raw, "s1") for raw in run["audio"]["frames"]]
    pcm = b"".join(f.pcm for f in frames[-25:])
    peak, spec, freqs = spectrum_peak(pcm)
    assert abs(peak - 440) <= 10, f"strongest frequency {peak} Hz"
    assert band(spec, freqs, 1000) < band(spec, freqs, 440) / 100, "the bot's own 1000 Hz voice must not be in it"


# ---- task 7: screen share ----

def test_screen_share_starts_and_shows_our_page(run):
    status, bot = run["share_start"]
    assert status == 200, bot
    assert bot["id"] == run["bot_id"]
    share = run["share_on"]
    assert share["active"] is True, share
    assert share["surface"] == "window", share
    assert share["frames"] > 20, share
    assert share["lit"] > 0.9 and share["mean"] > 100, f"frames are not black and show our bright page: {share}"


def test_screen_share_replaces_the_page(run):
    assert run["share_replace"][0] == 200
    before, after = run["share_on"], run["share_replaced"]
    assert after["active"] is True and after["started"] == before["started"], "the same share keeps running"
    assert after["lit"] > 0.9 and abs(after["mean"] - before["mean"]) > 20, f"it shows the new page: {before} -> {after}"


def test_screen_share_stops(run):
    assert run["share_stop"][0] == 200
    share = run["share_off"]
    assert share["active"] is False, share


# ---- task 8: participants and leaving ----

def test_join_at_20s_and_leave_10s_after_40s(run):
    hooks = [h for h in run["hooks"] if h["path"] == "call-events"]
    admitted = run["admitted_at"]
    meera = [h for h in hooks if h["body"]["event"] == "participant_events.join"
             and h["body"]["data"]["data"]["participant"]["name"] == "Meera"]
    assert len(meera) == 1 and 18 <= meera[0]["t"] - admitted <= 26
    leaves = [h for h in hooks if h["body"]["event"] == "participant_events.leave"]
    assert [h["body"]["data"]["data"]["participant"]["name"] for h in leaves] == ["Ravi"]
    assert 49 <= leaves[0]["t"] - admitted <= 58, leaves[0]["t"] - admitted
    ravi_join = next(h for h in hooks if h["body"]["data"]["data"]["participant"]["name"] == "Ravi")
    assert leaves[0]["body"]["data"]["data"]["participant"]["id"] == ravi_join["body"]["data"]["data"]["participant"]["id"]


def test_xebit_head_count(run):
    count = HeadCount(configured_bot_name=BOT_NAME)
    for h in run["hooks"]:
        count.feed(h["body"])
    assert count.count == 2
    assert sorted(count.roster.values()) == ["Asha", "Meera"]


def test_leave_call_end_order(run):
    final = run["final"]
    assert codes(final)[-2:] == ["call_ended", "done"]
    assert final["status_changes"][-2]["sub_code"] == "bot_received_leave_call"
    assert _latest_recall_status_code(final) == "done"
    assert run["meet_after"]["phase"] == "ended" and run["meet_after"]["end"] == "You left the meeting"
    ended = epoch(final["status_changes"][-2]["created_at"])
    last_camera = run["camera_after"]["received"]
    assert last_camera > ended - 1.5, "the camera page was still open when call_ended was reported"
    assert last_camera < epoch(final["status_changes"][-1]["created_at"]) + 1.5, "and it closed before done"
    assert run["container_gone"]
