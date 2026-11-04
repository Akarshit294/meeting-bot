"""Task 8: the other way a call ends for the bot - the host removes it (needs Docker; `pytest -m docker`)."""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from dockerstack import API, TESTKIT, api_key, codes, compose, remove_bot_containers, request, wait_http, wait_status

pytestmark = pytest.mark.docker


def kit(path, body=None):
    return request("POST" if body is not None else "GET", path, body, base=TESTKIT)[1]


def test_removed_by_the_host():
    compose("up", "-d", "--build")
    wait_http(API + "/health")
    wait_http(TESTKIT + "/health")
    key = api_key()
    kit("/api/reset", {})
    status, bot = request("POST", "/api/v1/bot/", {
        "meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Removal Test",
        "output_media": {"camera": {"kind": "webpage", "config": {"url": "http://testkit:9000/camera.html"}}},
    }, key=key)
    assert status == 201
    try:
        wait_status(key, bot["id"], "in_call_recording", timeout=180)
        time.sleep(3)
        kit("/api/command", {"do": "remove_bot"})
        final = wait_status(key, bot["id"], "done", timeout=60)
        assert codes(final)[-2:] == ["call_ended", "done"]
        assert final["status_changes"][-2]["sub_code"] == "bot_kicked_from_call"
        ended = datetime.fromisoformat(final["status_changes"][-2]["created_at"].replace("Z", "+00:00")).timestamp()
        time.sleep(2)
        assert kit("/api/camera-state")["received"] > ended - 1.5, "camera page still open at call_ended"
    finally:
        request("POST", f"/api/v1/bot/{bot['id']}/leave_call/", key=key)
        time.sleep(3)
        remove_bot_containers()
