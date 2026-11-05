"""Task 8: who is in the call (docs/SPEC.md 3.1 and 5.4), without Docker."""
from __future__ import annotations

from types import SimpleNamespace

import participants as pp
from botconfig import BotConfig
from xebit_logic import HeadCount


def E(pid, name, **kw):
    return pp.Entry(pid, name, **kw)


def test_join_once_then_leave_only_after_10s_missing():
    r = pp.Roster("Maya")
    ev = r.update([E("p1", "Asha"), E("p2", "Ravi")], reliable=True, now=0)
    assert [(k, p.name) for k, p in ev] == [("join", "Asha"), ("join", "Ravi")]
    assert r.update([E("p1", "Asha"), E("p2", "Ravi")], reliable=True, now=1) == []
    assert r.update([E("p1", "Asha")], reliable=True, now=2) == []          # Ravi missing since 2
    assert r.update([E("p1", "Asha")], reliable=True, now=11.9) == []       # 9.9 s: not yet
    ev = r.update([E("p1", "Asha")], reliable=True, now=12)
    assert [(k, p.id) for k, p in ev] == [("leave", "p2")]
    assert r.count() == 1


def test_unreliable_reads_never_cause_a_leave():
    r = pp.Roster("Maya")
    r.update([E("p1", "Asha"), E("p2", "Ravi")], reliable=True, now=0)
    for t in range(1, 120):
        assert r.update([E("p1", "Asha")], reliable=False, now=t) == []
    assert r.count() == 2


def test_coming_back_resets_the_clock():
    r = pp.Roster("Maya")
    r.update([E("p1", "Asha")], reliable=True, now=0)
    r.update([], reliable=True, now=1)
    r.update([E("p1", "Asha")], reliable=False, now=8)    # seen again (even in a tile view)
    assert r.update([], reliable=True, now=12) == []      # missing again only since 12
    assert r.update([], reliable=True, now=21) == []
    assert [k for k, _ in r.update([], reliable=True, now=22)] == ["leave"]


def test_the_bot_never_counts():
    r = pp.Roster("Maya Test")
    ev = r.update([E("self1", "Maya Test (You)", is_self=True), E("p1", "Asha"), E("x", "maya test")], reliable=True, now=0)
    assert [p.name for _, p in ev] == ["Asha"]
    assert "self1" in r.self_ids and "x" in r.self_ids
    # once known, the id stays the bot's even if a later read misses the marker
    assert r.update([E("self1", "Someone"), E("p1", "Asha")], reliable=True, now=1) == []


def test_same_name_different_people():
    r = pp.Roster("Bot")
    ev = r.update([E("p1", "Notetaker"), E("p2", "Notetaker")], reliable=True, now=0)
    assert len(ev) == 2


def test_name_only_entries_get_their_real_id_without_a_second_join():
    r = pp.Roster("Bot")
    assert len(r.update([E(None, "Asha")], reliable=False, now=0)) == 1
    assert r.update([E("p1", "Asha")], reliable=True, now=1) == []
    assert list(r.present) == ["p1"]
    assert r.update([E(None, "Asha")], reliable=True, now=2) == []


def test_the_id_sent_to_xebit_never_changes():
    r = pp.Roster("Bot")
    [(_, person)] = r.update([E(None, "Asha")], reliable=False, now=0)
    first = pp.webhook_body("join", "b", person)["data"]["data"]["participant"]["id"]
    r.update([E("p1", "Asha")], reliable=True, now=1)
    [(kind, gone)] = r.update([], reliable=True, now=1.5) or r.update([], reliable=True, now=12)
    assert kind == "leave"
    assert pp.webhook_body("leave", "b", gone)["data"]["data"]["participant"]["id"] == first == "name:Asha"


def test_name_suffixes_are_cut():
    assert pp.clean_name("Asha (Guest)") == "Asha"
    assert pp.clean_name("Maya Test (You)") == "Maya Test"
    assert pp.clean_name("Ravi (Presenting)") == "Ravi"


def test_names_from_real_tile_texts():
    # evidence/guest-join/20261104-094544-gyz-vvrw-dsv/03-call_ended.html
    assert pp.name_from_text("Akarshit\nAkarshit\ndevices") == "Akarshit"
    own = "visual_effects\nBackgrounds and effects\nShow in a tile\nmore_vert\nMore options for Notetaker\n" \
          "Others might still see your full video.\nNotetaker\nNotetaker\ndevices"
    assert pp.name_from_text(own) == "Notetaker"
    # evidence/signed-in-join/.../08-in-call.json: the own tile starts with frame_person / Reframe
    assert pp.name_from_text("frame_person\nReframe\nvisual_effects\nBackgrounds and effects\nMaya") == "Maya"
    assert pp.name_from_text("Maya Test (You)") == "Maya Test"
    assert pp.name_from_text("devices\nmore_vert") == ""


def test_meets_hover_buttons_are_not_names():
    # The second real test (2026-11-05) saved camera pictures as "Pin Akarshit to your main screen.png".
    assert pp.name_from_text("Pin Akarshit to your main screen\nAkarshit") == "Akarshit"
    assert pp.name_from_text("Unpin Subrat Parida from your main screen\nSubrat Parida") == "Subrat Parida"
    assert pp.name_from_text("Pin Akarshit to your main screen") == ""
    assert pp.name_from_text("Pinky Shah") == "Pinky Shah"


def test_webhook_body_is_what_xebit_reads():
    person = pp.Person("spaces/x/devices/1", "Asha")
    body = pp.webhook_body("join", "xbot_1", person)
    assert body["event"] == "participant_events.join"
    p = body["data"]["data"]["participant"]
    assert p == {"id": "spaces/x/devices/1", "name": "Asha", "is_host": False, "is_bot": False}
    assert body["data"]["data"]["timestamp"]["absolute"].endswith("Z")
    count = HeadCount("Xebit AI")
    count.feed(body)
    count.feed(pp.webhook_body("leave", "xbot_1", person))
    count.feed(pp.webhook_body("join", "xbot_1", pp.Person("spaces/x/devices/2", "Ravi")))
    assert count.count == 1 and count.roster == {"spaces/x/devices/2": "Ravi"}


class FakeReader:
    def __init__(self):
        self.reads = []

    def read(self):
        return self.reads.pop(0)

    def people_button(self):
        return None

    def panel_list(self):
        return None


class FakeRep:
    def __init__(self):
        self.lines, self.events = [], []

    def log(self, m):
        self.lines.append(m)

    def event(self, kind, **data):
        self.events.append((kind, data))

    def snapshot(self, *a, **k):
        pass


class FakeHooks:
    def __init__(self):
        self.sent = []

    def send(self, url, body):
        self.sent.append((url, body))


def test_people_sends_webhooks_to_each_endpoint():
    cfg = BotConfig.from_dict({"meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Maya", "bot_id": "xbot_9",
                               "endpoints": [{"type": "webhook", "url": "https://x/hook?token=1",
                                              "events": ["participant_events.join", "participant_events.leave"]},
                                             {"type": "webhook", "url": "https://x/t", "events": ["transcript.data"]}]})
    clock = SimpleNamespace(t=0.0)
    reader, rep, hooks = FakeReader(), FakeRep(), FakeHooks()
    people = pp.People(page=None, cfg=cfg, rep=rep, webhooks=hooks, reader=reader, clock=lambda: clock.t)
    reader.reads = [pp.Read([E("me", "Maya", is_self=True), E("p1", "Asha")], True, "tiles", 2)]
    people.tick(initial=True)
    assert [(u, b["event"]) for u, b in hooks.sent] == [("https://x/hook?token=1", "participant_events.join")]
    for t in (1, 2, 13):
        clock.t = t
        reader.reads.append(pp.Read([E("me", "Maya", is_self=True)], True, "tiles", 1))
        people.tick()
    assert [b["event"] for _, b in hooks.sent] == ["participant_events.join", "participant_events.leave"]
    assert hooks.sent[1][1]["data"]["data"]["participant"]["id"] == "p1"
    assert people.count_others() == 0
    assert any(kind == "entry_seen" for kind, _ in rep.events)


def test_a_failing_read_is_survived():
    cfg = BotConfig.from_dict({"meeting_url": "https://meet.google.com/abc-defg-hij"})

    class Broken(FakeReader):
        def read(self):
            raise RuntimeError("page is navigating")

    rep = FakeRep()
    people = pp.People(page=None, cfg=cfg, rep=rep, webhooks=FakeHooks(), reader=Broken(), clock=lambda: 5.0)
    people.tick(initial=True)
    assert any("read failed" in line for line in rep.lines)


# ---- webhooks: retries that are safe for xebit's head count ----

def make_sender(results):
    import socket as _socket
    import urllib.error

    from webhooks import WebhookSender

    calls = []

    def post(url, payload):
        calls.append(payload["event"])
        outcome = results.pop(0)
        if outcome == "refused":
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        if outcome == "dns":
            raise urllib.error.URLError(_socket.gaierror("no such host"))
        if outcome == "timeout":
            raise TimeoutError("timed out")
        if outcome == "500":
            raise urllib.error.HTTPError("https://x/hook", 500, "boom", {}, None)

    logs = []
    return WebhookSender(logs.append, backoff=0.01, post=post), calls, logs


def body(event, pid):
    return {"event": f"participant_events.{event}", "data": {"bot": {"id": "b"}, "data": {"participant": {"id": pid, "name": pid}}}}


def test_webhook_retries_only_when_it_never_arrived():
    sender, calls, _ = make_sender(["refused", "dns", "ok"])
    assert sender.deliver("https://x/hook", body("join", "p1"))
    assert calls == ["participant_events.join"] * 3
    sender.close()


def test_webhook_timeout_or_error_answer_is_not_retried():
    for outcome in ("timeout", "500"):
        sender, calls, logs = make_sender([outcome, "ok"])
        assert not sender.deliver("https://x/hook?token=SECRET", body("leave", "p1"))
        assert calls == ["participant_events.leave"]
        assert all("SECRET" not in line for line in logs)
        sender.close()


def test_no_leave_for_a_person_whose_join_failed():
    sender, calls, logs = make_sender(["500", "ok", "ok"])
    assert not sender.deliver("https://x/hook", body("join", "p1"))
    assert not sender.deliver("https://x/hook", body("leave", "p1"))
    assert calls == ["participant_events.join"]
    assert sender.deliver("https://x/hook", body("join", "p2"))
    assert sender.deliver("https://x/hook", body("leave", "p2"))
    sender.close()
