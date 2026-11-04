"""Task 12: naming Meet's 3 audio slots by the speaking indicator (pure logic, no Docker)."""
import per_person_audio as ppa


def test_one_slot_one_speaker_is_named():
    n = ppa.SlotNamer()
    assert n.update({0}, {"asha"}) == {0: "asha"}
    assert n.update({0}, {"asha"}) == {0: "asha"}


def test_a_slot_keeps_its_person_while_both_last():
    n = ppa.SlotNamer()
    n.update({0}, {"asha"})
    assert n.update({0, 1}, {"asha", "ravi"}) == {0: "asha", 1: "ravi"}
    assert n.update({1}, {"ravi"}) == {1: "ravi"}            # asha stopped: slot 0 is free again
    assert n.update({0, 1}, {"ravi", "meera"}) == {1: "ravi", 0: "meera"}


def test_no_guessing_when_it_is_not_clear():
    n = ppa.SlotNamer()
    assert n.update({0, 1}, {"asha", "ravi"}) == {}           # two new slots, two people: can't tell
    assert n.update({0}, set()) == {}                         # sound, nobody shown speaking
    assert n.update({0}, {"asha", "ravi"}) == {}              # one slot, two people


def test_a_slot_that_moves_to_someone_else():
    n = ppa.SlotNamer()
    n.update({0}, {"asha"})
    assert n.update({0}, {"ravi"}) == {0: "ravi"}             # asha stopped, ravi now plays in slot 0


class FakeRep:
    def __init__(self):
        self.lines, self.events = [], []

    def log(self, m):
        self.lines.append(m)

    def event(self, kind, **d):
        self.events.append((kind, d))


def bare(monkeypatch):
    """A PerPersonAudio without a browser (its constructor needs a page)."""
    started = []

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
    obj.socket = type("S", (), {"url": "ws://x/a", "close": lambda self: None, "send": lambda self, t: True})()
    obj.namer, obj.pending, obj.waiting, obj.sent = ppa.SlotNamer(), [], {}, {}
    obj.unnamed, obj.chunks_seen, obj.fallback, obj.speakers, obj.people = 0, 0, None, None, None
    obj.started = obj.last_note = ppa.time.monotonic()
    return obj, started


def test_falls_back_to_the_mix_when_nobody_can_be_named(monkeypatch):
    obj, started = bare(monkeypatch)
    loud = {"slot": 0, "t": 0, "db": -20, "pcm": ""}
    for _ in range(12):
        obj.pending = [loud] * 5
        obj.tick()
    assert started == ["ws://x/a"] and obj.fallback is not None
    assert any("switching to the mixed stream" in line for line in obj.rep.lines)


def test_falls_back_when_no_sound_arrives(monkeypatch):
    obj, started = bare(monkeypatch)
    obj.started -= 16
    obj.tick()
    assert started == ["ws://x/a"]
