"""Task 2: the REST API (docs/SPEC.md 2) with a fake runner, checked with xebit-api's own reader logic."""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from manager.app import create_app
from manager.config import Settings
from manager.runner import RunnerError, Started, bot_token
from manager.store import Store
from xebit_logic import (
    _GONE_CODES,
    _JOINED_CODES,
    STOP_SCREENSHARE_BODY,
    _latest_recall_status_code,
    _result_count,
    create_bot_body,
    start_screenshare_body,
)

KEY = "test-key-123"
AUTH = {"Authorization": f"Token {KEY}", "Content-Type": "application/json", "Accept": "application/json"}
MEET = "https://meet.google.com/abc-defg-hij"
CAMERA = "https://xebit.example/static/demo_v2-bridge.html?session_id=s1&token=SECRET"
SCREEN = "https://xebit.example/static/screencast-viewer.html?session_id=s1&token=SECRET2"


class FakeRunner:
    def __init__(self):
        self.started = []
        self.commands = []
        self.states: dict[str, str] = {}
        self.stopped = []
        self.removed = []
        self.fail_start = False
        self.unreachable = False
        self.reply = (200, {"ok": True})

    def start(self, bot, token):
        if self.fail_start:
            raise RunnerError("no docker")
        self.started.append((bot.id, token))
        self.states[bot.id] = "running"
        return Started(container=f"xbot-{bot.id[5:]}", live_view_url="http://localhost:49153")

    def command(self, bot, action, body=None, timeout=10):
        if self.unreachable:
            raise RunnerError("connection refused")
        self.commands.append((bot.id, action, body))
        return self.reply

    def state(self, bot):
        return self.states.get(bot.id, "missing")

    def stop(self, bot):
        self.stopped.append(bot.id)

    def remove(self, bot):
        self.removed.append(bot.id)


@pytest.fixture
def env(tmp_path):
    settings = Settings(api_key=KEY, state_file=tmp_path / "bots.json", max_bots=2)
    runner = FakeRunner()
    store = Store(settings.state_file)
    app = create_app(settings, runner, store, watch=False)
    with TestClient(app) as client:
        yield client, runner, store, app


def full_body(**kw):
    return create_bot_body(MEET, bot_name="Maya", webhook_url="https://xebit.example", demo_script_id="d1",
                           output_media_url=CAMERA, session_id="s1", **kw)


def make(client, body=None):
    r = client.post("/api/v1/bot/", headers=AUTH, json=body or full_body())
    assert r.status_code == 201, r.text
    return r.json()


def report(client, store, bot_id, code, sub_code=None, message=""):
    r = client.post(f"/internal/bots/{bot_id}/status", headers={"X-Bot-Token": bot_token(store.secret, bot_id)},
                    json={"code": code, "sub_code": sub_code, "message": message})
    assert r.status_code == 200
    return r.json()["accepted"]


# ---- auth ----

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Token wrong"}, {"Authorization": f"Bearer {KEY}"},
                                     {"Authorization": KEY}])
def test_bad_or_missing_key_is_401(env, headers):
    client, *_ = env
    for method, path in [("get", "/api/v1/bot/"), ("get", "/api/v1/bot/xbot_x/"), ("post", "/api/v1/bot/")]:
        r = getattr(client, method)(path, headers=headers)
        assert r.status_code == 401
        assert "detail" in r.json()


def test_health_needs_no_key(env):
    client, *_ = env
    assert client.get("/health").json() == {"ok": True}


def test_preflight_check_reads_401_as_bad_key(env):
    client, *_ = env
    r = client.get("/api/v1/bot/", headers={"Authorization": "Token nope"}, params={"status": "in_call_recording"})
    assert r.status_code in (401, 403)


# ---- create ----

def test_create_accepts_xebit_body_exactly(env):
    client, runner, store, _ = env
    body = full_body(join_at="2026-11-05T04:30:00Z")
    bot = make(client, body)
    assert re.fullmatch(r"xbot_[0-9a-f]{32}", bot["id"])
    assert bot["meeting_url"] == MEET and bot["bot_name"] == "Maya"
    assert bot["metadata"] == {"demo_script_id": "d1"}
    assert _latest_recall_status_code(bot) == "joining_call"
    assert bot["live_view_url"] == "http://localhost:49153"
    saved = store.get(bot["id"])
    assert saved.camera_url == CAMERA
    assert [e["events"] for e in saved.endpoints] == [
        ["transcript.data", "transcript.partial_data"],
        ["participant_events.join", "participant_events.leave"],
        ["video_separate_png.data"],
        ["audio_separate_raw.data"],
    ]
    assert runner.started[0] == (bot["id"], bot_token(store.secret, bot["id"]))


def test_create_without_camera_page_or_endpoints(env):
    client, _, store, _ = env
    bot = make(client, {"meeting_url": MEET})
    assert bot["bot_name"] == "Meeting Bot"
    assert store.get(bot["id"]).camera_url is None
    assert store.get(bot["id"]).endpoints == []


def test_create_without_trailing_slash(env):
    client, *_ = env
    r = client.post("/api/v1/bot", headers=AUTH, json={"meeting_url": MEET})
    assert r.status_code == 201


@pytest.mark.parametrize("url", ["https://zoom.us/j/123", "http://meet.google.com/abc-defg-hij", "https://meet.google.com/",
                                 "meet.google.com/abc-defg-hij", None, 42])
def test_create_rejects_non_meet_links(env, url):
    client, *_ = env
    r = client.post("/api/v1/bot/", headers=AUTH, json={"meeting_url": url})
    assert r.status_code == 400
    assert r.json()["detail"]


def test_create_rejects_bad_json(env):
    client, *_ = env
    r = client.post("/api/v1/bot/", headers=AUTH, content=b"{nope")
    assert r.status_code == 400 and "detail" in r.json()


def test_unknown_fields_are_ignored(env):
    client, *_ = env
    make(client, {"meeting_url": MEET, "foo": {"bar": 1}, "variant": {"google_meet": "web_4_core"}})


def test_max_bots_gives_429(env):
    client, *_ = env
    make(client)
    make(client)
    r = client.post("/api/v1/bot/", headers=AUTH, json=full_body())
    assert r.status_code == 429 and "detail" in r.json()


def test_finished_bots_do_not_count_toward_max(env):
    client, _, store, _ = env
    a = make(client)
    make(client)
    report(client, store, a["id"], "fatal", "google_meet_bot_blocked")
    make(client)


def test_start_failure_is_503_and_fatal(env):
    client, runner, store, _ = env
    runner.fail_start = True
    r = client.post("/api/v1/bot/", headers=AUTH, json=full_body())
    assert r.status_code == 503 and "detail" in r.json()
    [bot] = store.all()
    assert bot.latest == "fatal"


# ---- get / list ----

def test_get_unknown_is_404(env):
    client, *_ = env
    r = client.get("/api/v1/bot/xbot_" + "0" * 32 + "/", headers=AUTH)
    assert r.status_code == 404 and "detail" in r.json()


def test_get_with_and_without_slash(env):
    client, *_ = env
    bot = make(client)
    for path in (f"/api/v1/bot/{bot['id']}/", f"/api/v1/bot/{bot['id']}"):
        assert client.get(path, headers=AUTH).json()["id"] == bot["id"]


def test_status_changes_shape(env):
    client, _, store, _ = env
    bot = make(client)
    report(client, store, bot["id"], "in_waiting_room")
    report(client, store, bot["id"], "in_call_not_recording")
    got = client.get(f"/api/v1/bot/{bot['id']}/", headers=AUTH).json()
    assert [s["code"] for s in got["status_changes"]] == ["joining_call", "in_waiting_room", "in_call_not_recording"]
    for change in got["status_changes"]:
        assert set(change) == {"code", "sub_code", "message", "created_at"}
        assert change["sub_code"] is None and change["message"] == ""
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", change["created_at"])
    assert _latest_recall_status_code(got) in _JOINED_CODES


def test_list_filter_matches_preflight(env):
    client, _, store, _ = env
    a, b = make(client), make(client)
    for code in ("in_waiting_room", "in_call_not_recording", "in_call_recording"):
        report(client, store, a["id"], code)
    r = client.get("/api/v1/bot/", headers=AUTH, params={"status": "in_call_recording"})
    assert r.status_code == 200
    assert _result_count(r) == 1
    assert r.json()["results"][0]["id"] == a["id"]
    assert set(r.json()) == {"count", "next", "previous", "results"}
    assert r.json()["next"] is None and r.json()["previous"] is None
    everyone = client.get("/api/v1/bot", headers=AUTH)
    assert _result_count(everyone) == 2 and everyone.json()["count"] == 2
    assert b["id"] in {x["id"] for x in everyone.json()["results"]}


# ---- status rules ----

def test_nothing_after_fatal(env):
    client, _, store, _ = env
    bot = make(client)
    assert report(client, store, bot["id"], "fatal", "google_meet_bot_blocked", "You can't join this video call")
    for code in ("done", "call_ended", "in_call_recording", "fatal"):
        assert not report(client, store, bot["id"], code)
    got = client.get(f"/api/v1/bot/{bot['id']}/", headers=AUTH).json()
    assert _latest_recall_status_code(got) == "fatal"
    assert got["status_changes"][-1]["sub_code"] == "google_meet_bot_blocked"


def test_only_done_after_call_ended(env):
    client, _, store, _ = env
    bot = make(client)
    report(client, store, bot["id"], "in_call_recording")
    assert report(client, store, bot["id"], "call_ended", "bot_received_leave_call")
    assert not report(client, store, bot["id"], "in_call_recording")
    assert not report(client, store, bot["id"], "fatal")
    assert report(client, store, bot["id"], "done")
    assert not report(client, store, bot["id"], "done")
    codes = [s["code"] for s in store.get(bot["id"]).status_changes]
    assert codes == ["joining_call", "in_call_recording", "call_ended", "done"]


def test_unknown_code_and_duplicates_are_dropped(env):
    client, _, store, _ = env
    bot = make(client)
    assert not report(client, store, bot["id"], "joining_call")
    assert not report(client, store, bot["id"], "dancing")


def test_internal_endpoints_need_the_bots_own_token(env):
    client, _, store, _ = env
    a, b = make(client), make(client)
    r = client.post(f"/internal/bots/{a['id']}/status", headers={"X-Bot-Token": bot_token(store.secret, b["id"])},
                    json={"code": "fatal"})
    assert r.status_code == 401
    assert client.get(f"/internal/bots/{a['id']}/config").status_code == 401
    cfg = client.get(f"/internal/bots/{a['id']}/config", headers={"X-Bot-Token": bot_token(store.secret, a["id"])}).json()
    assert cfg["meeting_url"] == MEET and cfg["camera_url"] == CAMERA and cfg["bot_name"] == "Maya"
    assert len(cfg["endpoints"]) == 4 and cfg["meet_url_override"] is None


# ---- leave ----

def test_leave_forwards_and_is_safe_to_repeat(env):
    client, runner, store, _ = env
    bot = make(client)
    report(client, store, bot["id"], "in_call_recording")
    r = client.post(f"/api/v1/bot/{bot['id']}/leave_call/", headers=AUTH)
    assert r.status_code == 200 and r.json()["id"] == bot["id"]
    assert runner.commands[-1][:2] == (bot["id"], "leave")
    report(client, store, bot["id"], "call_ended", "bot_received_leave_call")
    report(client, store, bot["id"], "done")
    for _ in range(2):
        r = client.post(f"/api/v1/bot/{bot['id']}/leave_call", headers=AUTH)
        assert r.status_code == 200
        assert _latest_recall_status_code(r.json()) in _GONE_CODES


def test_leave_while_starting_stops_the_container(env):
    client, runner, store, _ = env
    bot = make(client)
    runner.unreachable = True
    r = client.post(f"/api/v1/bot/{bot['id']}/leave_call/", headers=AUTH)
    assert r.status_code == 200
    assert runner.stopped == [bot["id"]]


def test_leave_unknown_is_404(env):
    client, *_ = env
    assert client.post("/api/v1/bot/xbot_nope/leave_call/", headers=AUTH).status_code == 404


# ---- screen share ----

def test_screenshare_start_and_stop(env):
    client, runner, store, _ = env
    bot = make(client)
    report(client, store, bot["id"], "in_call_recording")
    r = client.post(f"/api/v1/bot/{bot['id']}/output_media/", headers=AUTH, json=start_screenshare_body(SCREEN))
    assert r.status_code == 200 and r.json()["id"] == bot["id"]
    assert runner.commands[-1] == (bot["id"], "screenshare", {"url": SCREEN})
    r = client.request("DELETE", f"/api/v1/bot/{bot['id']}/output_media/", headers=AUTH, json=STOP_SCREENSHARE_BODY)
    assert r.status_code == 200
    assert runner.commands[-1][1] == "screenshare_stop"


def test_screenshare_not_in_call_is_409(env):
    client, runner, store, _ = env
    bot = make(client)
    r = client.post(f"/api/v1/bot/{bot['id']}/output_media/", headers=AUTH, json=start_screenshare_body(SCREEN))
    assert r.status_code == 409 and "detail" in r.json()
    assert runner.commands == []


def test_screenshare_bot_says_409(env):
    client, runner, store, _ = env
    bot = make(client)
    report(client, store, bot["id"], "in_call_not_recording")
    runner.reply = (409, {"detail": "not in the call"})
    r = client.post(f"/api/v1/bot/{bot['id']}/output_media", headers=AUTH, json=start_screenshare_body(SCREEN))
    assert r.status_code == 409


def test_camera_body_is_400(env):
    client, _, store, _ = env
    bot = make(client)
    report(client, store, bot["id"], "in_call_recording")
    r = client.post(f"/api/v1/bot/{bot['id']}/output_media/", headers=AUTH,
                    json={"camera": {"kind": "webpage", "config": {"url": SCREEN}}})
    assert r.status_code == 400 and "detail" in r.json()


def test_stop_screenshare_without_share_or_bot_gone_is_200(env):
    client, runner, store, _ = env
    bot = make(client)
    r = client.request("DELETE", f"/api/v1/bot/{bot['id']}/output_media/", headers=AUTH, json=STOP_SCREENSHARE_BODY)
    assert r.status_code == 200
    report(client, store, bot["id"], "fatal", "bot_errored")
    runner.unreachable = True
    r = client.request("DELETE", f"/api/v1/bot/{bot['id']}/output_media", headers=AUTH, json=STOP_SCREENSHARE_BODY)
    assert r.status_code == 200


# ---- the watcher and the state file ----

def test_container_dying_without_report_is_fatal(env):
    client, runner, store, app = env
    bot = make(client)
    report(client, store, bot["id"], "in_call_recording")
    runner.states[bot["id"]] = "exited"
    app.state.watch_once()
    got = client.get(f"/api/v1/bot/{bot['id']}/", headers=AUTH).json()
    assert got["status_changes"][-1]["code"] == "fatal"
    assert got["status_changes"][-1]["sub_code"] == "bot_errored"
    assert runner.removed == [bot["id"]]


def test_container_gone_after_call_ended_gives_done(env):
    client, runner, store, app = env
    bot = make(client)
    report(client, store, bot["id"], "call_ended", "bot_kicked_from_call")
    runner.states[bot["id"]] = "exited"
    app.state.watch_once()
    assert [s["code"] for s in store.get(bot["id"]).status_changes][-2:] == ["call_ended", "done"]


def test_container_gone_after_done_is_left_alone(env):
    client, runner, store, app = env
    bot = make(client)
    report(client, store, bot["id"], "call_ended")
    report(client, store, bot["id"], "done")
    runner.states[bot["id"]] = "exited"
    app.state.watch_once()
    assert store.get(bot["id"]).latest == "done"
    assert runner.removed == [bot["id"]]
    app.state.watch_once()
    assert runner.removed == [bot["id"]]


def test_state_file_survives_restart(env, tmp_path):
    client, runner, store, _ = env
    bot = make(client)
    report(client, store, bot["id"], "in_call_recording")
    data = json.loads((tmp_path / "bots.json").read_text())
    assert data["bots"][0]["id"] == bot["id"]
    again = Store(tmp_path / "bots.json")
    assert again.secret == store.secret
    assert again.get(bot["id"]).latest == "in_call_recording"
    assert again.get(bot["id"]).camera_url == CAMERA


def test_responses_never_echo_the_camera_page_url(env):
    client, *_ = env
    bot = make(client)
    assert "SECRET" not in json.dumps(bot)
