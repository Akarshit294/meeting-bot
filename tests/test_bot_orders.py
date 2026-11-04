"""Task 3: the bot takes orders - config parsing, the status rules, the control server, the run folder."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import meet_bot
from botconfig import BotConfig, ManagerLink, needs_secure_flag, safe_url
from control import ControlServer
from reporting import Reporter, allowed

MEET = "https://meet.google.com/abc-defg-hij"
TODAY_FLAGS = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]


# ---- command line, as today ----

def test_cli_still_works_like_today():
    args = meet_bot.parse_args(["https://meet.google.com/meet/gyz-vvrw-dsv", "--name", "Notetaker", "--type-name",
                                "--knock-delay", "15"], environ={})
    assert args.url == "https://meet.google.com/gyz-vvrw-dsv"
    assert args.name == "Notetaker" and args.type_name and args.knock_delay == 15 and args.input == "vnc"
    assert not args.join_only and args.manager is None
    assert args.config.meeting_url == args.url and args.config.bot_name == "Notetaker"
    assert args.config.camera_url is None and args.config.endpoints == []


@pytest.mark.parametrize("url", ["https://zoom.us/j/1", "https://meet.google.com/", None])
def test_cli_needs_a_meet_link(url):
    with pytest.raises(SystemExit):
        meet_bot.parse_args([url] if url else [], environ={})


def test_cli_service_options():
    args = meet_bot.parse_args([MEET, "--camera-url", "https://x.example/cam?token=T", "--audio-url", "wss://x.example/a?token=U",
                                "--events-url", "https://x.example/hook", "--meet-url", "http://testkit:9000/meet/abc"],
                               environ={})
    cfg = args.config
    assert cfg.camera_url == "https://x.example/cam?token=T"
    assert cfg.endpoint_for("audio_separate_raw.data", "websocket").url == "wss://x.example/a?token=U"
    assert [e.url for e in cfg.webhooks_for("participant_events.leave")] == ["https://x.example/hook"]
    assert args.meet_url == "http://testkit:9000/meet/abc"


def test_env_defaults():
    args = meet_bot.parse_args([MEET], environ={"JOIN_ONLY": "1", "KNOCK_DELAY": "15", "TYPE_NAME": "1", "BOT_NAME": "Maya"})
    assert args.join_only and args.knock_delay == 15 and args.type_name and args.name == "Maya"


def test_options_file_has_no_tokens():
    args = meet_bot.parse_args([MEET, "--camera-url", "https://x.example/cam?token=SECRET", "--audio-url",
                                "wss://x.example/a?token=SECRET"], environ={})
    text = json.dumps(meet_bot.options_for_file(args))
    assert "SECRET" not in text and "x.example/cam" in text


def test_redact_cuts_urls_in_text():
    from botconfig import redact

    text = "WebSocket connection to 'wss://x.example/api/ws/demo?token=SECRET' failed; see https://y.example/a?b=SECRET2."
    out = redact(text)
    assert "SECRET" not in out and "wss://x.example/api/ws/demo" in out and "https://y.example/a" in out


def test_safe_url_drops_the_query():
    assert safe_url("https://h.example:8443/p/q?token=abc#x") == "https://h.example:8443/p/q"


def test_run_folder_name():
    args = meet_bot.parse_args([MEET, "--output", "/tmp/out"], environ={})
    folder = meet_bot.run_folder(args)
    assert folder.parent == Path("/tmp/out") and folder.name.endswith("-abc-defg-hij")


# ---- Chrome flags ----

def test_join_only_flags_are_exactly_todays():
    args = meet_bot.parse_args([MEET, "--join-only"], environ={})
    flags = meet_bot.meet_flags(args)
    assert flags[:2] == TODAY_FLAGS and flags[2].startswith("--use-file-for-fake-audio-capture=")
    assert flags[3:] == ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--lang=en-US",
                         "--window-position=0,0", "--window-size=1280,720"]
    assert meet_bot.meet_flags(None) == flags  # the mouse demo keeps them too


def test_service_flags():
    args = meet_bot.parse_args([MEET], environ={})
    flags = meet_bot.meet_flags(args)
    assert not any(f.startswith(("--use-fake", "--use-file-for-fake")) for f in flags)
    assert "--auto-select-desktop-capture-source=xbot screen share" in flags
    for f in ("--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
              "--disable-renderer-backgrounding", "--disable-blink-features=AutomationControlled"):
        assert f in flags
    assert not any("unsafely" in f for f in flags)


def test_insecure_origin_flag_only_for_the_test_meeting():
    args = meet_bot.parse_args([MEET, "--meet-url", "http://testkit:9000/meet/abc-defg-hij"], environ={})
    assert "--unsafely-treat-insecure-origin-as-secure=http://testkit:9000" in meet_bot.meet_flags(args)
    assert needs_secure_flag("http://host.docker.internal:8000/x") and not needs_secure_flag("http://localhost:8000/x")
    assert not needs_secure_flag("https://xebit.example/x")


# ---- config from the manager ----

class FakeManager:
    def __init__(self, config: dict, token: str = "tok"):
        self.config, self.token, self.reports = config, token, []
        manager = self

        class Handler(BaseHTTPRequestHandler):
            def _ok(self):
                return self.headers.get("X-Bot-Token") == manager.token

            def do_GET(self):
                if not self._ok():
                    return self._send(401, {})
                self._send(200, manager.config)

            def do_POST(self):
                if not self._ok():
                    return self._send(401, {})
                manager.reports.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self._send(200, {"accepted": True})

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"


def test_config_from_manager_and_reports():
    manager = FakeManager({
        "bot_id": "xbot_" + "a" * 32, "meeting_url": MEET, "bot_name": "Maya", "camera_url": "https://x/cam?token=1",
        "endpoints": [{"type": "websocket", "url": "wss://x/a?token=2", "events": ["audio_separate_raw.data"]},
                      {"type": "webhook", "url": "https://x/hook", "events": ["participant_events.join"]},
                      {"type": "carrier-pigeon", "url": "x"}],
        "meet_url_override": None,
    })
    link = ManagerLink(manager.url, "xbot_" + "a" * 32, "tok")
    cfg = link.fetch_config(attempts=1)
    assert cfg.bot_name == "Maya" and cfg.camera_url == "https://x/cam?token=1"
    assert len(cfg.endpoints) == 2
    assert link.report({"code": "joining_call"})
    assert manager.reports == [{"code": "joining_call"}]
    bad = ManagerLink(manager.url, "xbot_x", "wrong")
    with pytest.raises(RuntimeError):
        bad.fetch_config(attempts=1)
    manager.httpd.shutdown()


def test_manager_link_from_env(monkeypatch):
    monkeypatch.setenv("MANAGER_URL", "http://manager:8090/")
    monkeypatch.setenv("BOT_ID", "xbot_1")
    monkeypatch.setenv("BOT_TOKEN", "t")
    link = ManagerLink.from_env()
    assert link.url == "http://manager:8090" and link.bot_id == "xbot_1"
    monkeypatch.delenv("BOT_TOKEN")
    assert ManagerLink.from_env() is None


def test_bot_config_from_dict_defaults():
    cfg = BotConfig.from_dict({"meeting_url": MEET})
    assert cfg.bot_name == "Meeting Bot" and cfg.camera_url is None and cfg.endpoints == []


# ---- the status rules ----

def test_status_rules():
    assert allowed(None, "joining_call")
    assert not allowed("joining_call", "joining_call")
    assert allowed("call_ended", "done") and not allowed("call_ended", "fatal")
    assert not allowed("fatal", "done") and not allowed("done", "fatal")


def test_reporter_sequence(tmp_path):
    sent = []
    rep = Reporter(tmp_path / "run", send=lambda body: sent.append(body) or True)
    for code in ("joining_call", "in_waiting_room", "in_call_not_recording", "in_call_recording"):
        assert rep.status(code)
    assert rep.status("call_ended", "bot_received_leave_call")
    assert not rep.status("in_call_recording")
    assert rep.status("done")
    assert not rep.status("done")
    lines = [json.loads(line) for line in (tmp_path / "run" / "events.jsonl").read_text().splitlines()]
    assert [x["code"] for x in lines] == ["joining_call", "in_waiting_room", "in_call_not_recording",
                                         "in_call_recording", "call_ended", "done"]
    assert [x["code"] for x in sent] == [x["code"] for x in lines]
    assert sent[0]["run_dir"] == "run"


def test_reporter_nothing_after_fatal(tmp_path):
    sent = []
    rep = Reporter(tmp_path / "run", send=lambda body: sent.append(body) or True)
    rep.status("joining_call")
    rep.status("fatal", "google_meet_bot_blocked", "You can't join this video call")
    assert not rep.status("done")
    assert [x["code"] for x in sent] == ["joining_call", "fatal"]


# ---- the control server ----

def call(port, path, body=None, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"X-Bot-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


@pytest.fixture
def server():
    leaves = []
    srv = ControlServer(0, "tok", lambda: leaves.append(1), host="127.0.0.1", wait=5).start()
    yield srv, leaves
    srv.stop()


def test_control_leave_any_time(server):
    srv, leaves = server
    assert call(srv.port, "/leave", token="tok") == (200, {"ok": True})
    assert leaves == [1]


def test_control_needs_token(server):
    srv, leaves = server
    assert call(srv.port, "/leave")[0] == 401
    assert call(srv.port, "/leave", token="nope")[0] == 401
    assert leaves == []


def test_control_screenshare_needs_the_call(server):
    srv, _ = server
    assert call(srv.port, "/screenshare", {"url": "https://x/s"}, token="tok")[0] == 409
    assert call(srv.port, "/screenshare_stop", {}, token="tok")[0] == 200
    assert call(srv.port, "/screenshare", {}, token="tok")[0] == 400
    assert call(srv.port, "/dance", {}, token="tok")[0] == 404


def test_control_screenshare_runs_on_the_main_loop(server):
    srv, _ = server
    srv.in_call.set()
    results = {}

    def client():
        results["start"] = call(srv.port, "/screenshare", {"url": "https://x/s"}, token="tok")

    t = threading.Thread(target=client)
    t.start()
    command = None
    for _ in range(100):
        command = srv.next_command()
        if command:
            break
        threading.Event().wait(0.05)
    assert command is not None and command.action == "screenshare" and command.body == {"url": "https://x/s"}
    command.reply(200, {"ok": True, "detail": "presenting"})
    t.join(5)
    assert results["start"] == (200, {"ok": True, "detail": "presenting"})


def test_control_pending_commands_fail_when_the_call_ends(server):
    srv, _ = server
    srv.in_call.set()
    results = {}
    t = threading.Thread(target=lambda: results.setdefault("r", call(srv.port, "/screenshare", {"url": "https://x"}, token="tok")))
    t.start()
    for _ in range(100):
        if not srv.commands.empty():
            break
        threading.Event().wait(0.05)
    srv.fail_pending()
    t.join(5)
    assert results["r"][0] == 409
