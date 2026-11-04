"""Task 4: the manager starts, watches and stops real bot containers (needs Docker; `pytest -m docker`)."""
from __future__ import annotations

import time

import pytest

from dockerstack import (TESTKIT, api_key, codes, compose, container_exists, remove_bot_containers, request, wait_http,
                         wait_status, API)

pytestmark = pytest.mark.docker
MEET = "https://meet.google.com/abc-defg-hij"


@pytest.fixture(scope="module")
def key():
    # The bot opens a page that is not a meeting; we leave it before its 60 s pre-join timeout.
    compose("up", "-d", "--build", "manager", env={"TEST_MEET_URL": "http://manager:8090/health"})
    wait_http(API + "/health")
    yield api_key()
    remove_bot_containers()


def test_create_join_leave_done_container_gone(key):
    status, bot = request("POST", "/api/v1/bot/", {"meeting_url": MEET, "bot_name": "Test Bot"}, key=key)
    assert status == 201, bot
    bot_id = bot["id"]
    assert codes(bot) == ["joining_call"]
    assert bot["live_view_url"] and bot["live_view_url"].startswith("http://localhost:")
    assert container_exists(bot_id)
    time.sleep(8)
    status, _ = request("POST", f"/api/v1/bot/{bot_id}/leave_call/", key=key)
    assert status == 200
    bot = wait_status(key, bot_id, "done", timeout=90)
    assert codes(bot)[-2:] == ["call_ended", "done"]
    assert bot["status_changes"][-2]["sub_code"] == "bot_received_leave_call"
    for _ in range(20):
        if not container_exists(bot_id):
            break
        time.sleep(1)
    assert not container_exists(bot_id)
    # leave again: still 200
    assert request("POST", f"/api/v1/bot/{bot_id}/leave_call/", key=key)[0] == 200


def test_killed_container_becomes_fatal(key):
    import subprocess

    status, bot = request("POST", "/api/v1/bot/", {"meeting_url": MEET}, key=key)
    assert status == 201
    time.sleep(5)
    subprocess.run(["docker", "kill", "xbot-" + bot["id"].removeprefix("xbot_")], capture_output=True)
    bot = wait_status(key, bot["id"], "fatal", timeout=30)
    assert bot["status_changes"][-1]["sub_code"] == "bot_errored"
    status, listed = request("GET", "/api/v1/bot/?status=fatal", key=key)
    assert bot["id"] in [b["id"] for b in listed["results"]]


def test_bad_key_is_401_on_the_real_service(key):
    assert request("GET", "/api/v1/bot/?status=in_call_recording", key="wrong")[0] == 401
    status, body = request("GET", "/api/v1/bot/?status=in_call_recording", key=key)
    assert status == 200 and isinstance(body["results"], list)
