"""Tasks 12 and R3: the live per-person stream's safety net and what it sends (no Docker)."""
import base64
import json

import per_person_audio as ppa


class FakeRep:
    def __init__(self):
        self.lines, self.events = [], []

    def log(self, m):
        self.lines.append(m)

    def event(self, kind, **d):
        self.events.append((kind, d))


class Person:
    def __init__(self, pid, name):
        self.id = self.sent_id = pid
        self.name = name


def bare(monkeypatch, people=()):
    """A PerPersonAudio with a fake socket and a fake mixed streamer."""
    started, sent = [], []

    class FakeStreamer:
        def __init__(self, url, bot_id, log):
            started.append(url)

        def start(self):
            return self

        def stop(self):
            pass

    import audio
    monkeypatch.setattr(audio, "AudioStreamer", FakeStreamer)
    obj = ppa.PerPersonAudio.__new__(ppa.PerPersonAudio)
    obj.rep, obj.cfg = FakeRep(), type("C", (), {"bot_id": "b"})()
    obj.socket = type("S", (), {"url": "ws://x/a", "close": lambda self: None,
                                "send": lambda self, t: sent.append(json.loads(t)) or True})()
    obj.people = type("P", (), {"roster": type("R", (), {"present": {p.id: p for p in people}})()})()
    obj.sent, obj.unnamed, obj.chunks_seen, obj.fallback = {}, 0, 0, None
    obj.started = obj.last_note = ppa.time.monotonic()
    return obj, started, sent


def piece(t=1.0):
    return {"slot": 0, "t": int(t * 1000), "db": -20, "pcm": base64.b64encode(b"\x01\x00" * 3200).decode()}


def test_named_pieces_go_out_with_the_person_and_the_time_heard(monkeypatch):
    obj, _, sent = bare(monkeypatch, [Person("spaces/x/devices/1", "Asha")])
    obj.deliver([("spaces/x/devices/1", piece(1793859000.2)), (None, piece()), ("someone-gone", piece())])
    assert len(sent) == 1 and obj.unnamed == 2
    data = sent[0]["data"]["data"]
    assert data["participant"] == {"id": "spaces/x/devices/1", "name": "Asha", "is_bot": False}
    assert data["timestamp"]["absolute"].startswith("2026-11-05T06:")
    assert obj.sent == {"Asha": 1}


def test_falls_back_to_the_mix_when_nobody_can_be_named(monkeypatch):
    obj, started, _ = bare(monkeypatch)
    obj.feed([piece()] * 60)
    obj.deliver([(None, piece())] * 60)
    obj.tick()
    assert started == ["ws://x/a"] and obj.fallback is not None
    assert any("switching to the mixed stream" in line for line in obj.rep.lines)


def test_falls_back_when_no_sound_arrives(monkeypatch):
    obj, started, _ = bare(monkeypatch)
    obj.started -= 16
    obj.tick()
    assert started == ["ws://x/a"]
