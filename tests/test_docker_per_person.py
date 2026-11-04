"""Task 12: EXPERIMENTAL one stream per person, in the fake meeting (needs Docker; `pytest -m docker`).

The fake meeting plays each person through Meet-like <audio> slots with their own tone (Asha 440 Hz,
Ravi 660 Hz, Meera 880 Hz) and shows who is speaking. Each person's frames must carry that person's tone.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

from dockerstack import (API, ROOT, TESTKIT, api_key, compose, remove_bot_containers, request, request_text, wait_http,
                         wait_status)
from xebit_logic import _decode_recall

pytestmark = pytest.mark.docker
TONES = {"Asha": 440, "Ravi": 660, "Meera": 880}


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
        "meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Per Person",
        "output_media": {"camera": {"kind": "webpage", "config": {"url": "http://testkit:9000/camera.html"}}},
        "recording_config": {"realtime_endpoints": [
            {"type": "webhook", "url": "http://testkit:9000/hooks/call-events", "events": ["participant_events.join", "participant_events.leave"]},
            {"type": "websocket", "url": "ws://testkit:9000/ws/audio", "events": ["audio_separate_raw.data"]}]},
        "xbot_options": {"per_person_audio": True},
    }, key=key)
    assert status == 201, bot
    seen = {}
    try:
        wait_status(key, bot["id"], "in_call_recording", timeout=180)
        time.sleep(30)
        seen["audio"] = kit("/api/audio?last=200")
        seen["hooks"] = kit("/api/hooks")
        seen["run_dir"] = request_text(f"/testrx/run-dir?bot={bot['id']}", key)
        request("POST", f"/api/v1/bot/{bot['id']}/leave_call/", key=key)
        wait_status(key, bot["id"], "done", timeout=60)
    finally:
        remove_bot_containers()
        compose("up", "-d", "manager")  # back to the default fake meeting
    return seen


def test_each_person_gets_their_own_stream_with_their_tone(run):
    frames = [_decode_recall(raw, "s") for raw in run["audio"]["frames"]]
    assert frames and all(frames)
    by_person = defaultdict(list)
    for f in frames:
        assert f.participant_id != "meeting-mix", "per-person mode sends no mixed stream"
        assert len(f.pcm) == 6400 and f.captured_at
        by_person[(f.participant_id, f.participant_name)].append(f.pcm)
    names = {name for _, name in by_person}
    assert {"Asha", "Ravi"} <= names, names
    joins = {h["body"]["data"]["data"]["participant"]["name"]: h["body"]["data"]["data"]["participant"]["id"]
             for h in run["hooks"] if h["body"]["event"] == "participant_events.join"}
    for (pid, name), chunks in by_person.items():
        assert joins[name] == pid, "the same ids as the join webhooks"
        samples = np.frombuffer(b"".join(chunks), dtype="<i2").astype(float)
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        peak = float(np.fft.rfftfreq(len(samples), 1 / 16000)[spectrum.argmax()])
        assert abs(peak - TONES[name]) <= 15, f"{name}: strongest {peak} Hz, expected {TONES[name]} Hz"


def test_speaker_timeline_is_written(run):
    path = ROOT / "output" / "test" / Path(run["run_dir"]).name / "speakers.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    started = {line["name"] for line in lines if line["event"] == "started"}
    assert {"Asha", "Ravi"} <= started
    assert {line["event"] for line in lines} == {"started", "stopped"}
