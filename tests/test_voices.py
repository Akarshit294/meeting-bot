"""Task R3: captions parsing, the speaker timeline from captions, and giving Meet's slots to people (no Docker)."""
from __future__ import annotations

import captions
import voices


# ---- captions ----

def test_parse_blocks_by_speaker_names():
    text = "Asha\nhello there how are\nyou today\nYou\nfine thanks\narrow_downward\nJump to bottom\nRavi\nok"
    assert captions.parse(text, {"Asha", "Ravi", "You"}) == [
        ("Asha", "hello there how are you today"), ("You", "fine thanks"), ("Ravi", "ok")]
    assert captions.parse("some text before any name\nAsha\nhi", {"Asha"}) == [("Asha", "hi")]


def test_align_follows_growth_and_blocks_leaving_the_top():
    prev = [{"name": "Asha", "text": "hello there"}, {"name": "Ravi", "text": "ok so"}]
    assert captions.align(prev, [("Asha", "hello there"), ("Ravi", "ok so then")]) == 0
    assert captions.align(prev, [("Ravi", "ok so then"), ("Asha", "yes")]) == 1  # Asha's old block dropped off
    # nothing lines up: every old block counts as gone, every shown block as new
    assert captions.align(prev, [("Meera", "something else entirely"), ("Ravi", "x")]) == 2
    assert captions.align([], [("Asha", "hi")]) == 0


class Rep:
    def __init__(self, tmp_path):
        self.run_dir = tmp_path
        self.lines = []

    def log(self, m):
        self.lines.append(m)

    def event(self, *a, **k):
        pass


class Person:
    def __init__(self, pid, name):
        self.id = self.sent_id = pid
        self.name = name


class People:
    def __init__(self, *people):
        self.roster = type("R", (), {"present": {p.id: p for p in people}})()


def test_caption_reader_writes_changes_and_finals_and_skips_the_bot(tmp_path, monkeypatch):
    import json

    heard = []
    reader = captions.CaptionReader(None, People(Person("p1", "Asha")), Rep(tmp_path), "Maya",
                                    on_speech=lambda pid, name, wall, new: heard.append((pid, name, new)))
    shown = []
    monkeypatch.setattr(reader, "read", lambda: list(shown))
    clock = [100.0]
    monkeypatch.setattr(captions.time, "monotonic", lambda: clock[0])

    def step(blocks, dt=0.5):
        shown[:] = blocks
        clock[0] += dt
        reader.tick()

    step([("Ravi", "said before the bot could read")])  # the first read: kept, but not as speech now
    step([("Ravi", "said before the bot could read"), ("Asha", "hello")])
    step([("Ravi", "said before the bot could read"), ("Asha", "hello there")])
    step([("Ravi", "said before the bot could read"), ("Asha", "hello there"), ("You", "hi Asha")])  # the bot: not written
    step([("Asha", "hello there"), ("You", "hi Asha")], dt=2.0)  # Ravi's old block scrolled away
    lines = [json.loads(line) for line in (tmp_path / "captions.jsonl").read_text().splitlines()]
    assert [(x["name"], x["text"], x["changing"]) for x in lines] == [
        ("Ravi", "said before the bot could read", False),
        ("Asha", "hello", True), ("Asha", "hello there", True), ("Asha", "hello there", False)]
    assert lines[1]["id"] == "p1"
    assert heard == [("p1", "Asha", True), ("p1", "Asha", False)]


# ---- the speaker timeline from captions ----

def test_speakers_from_captions(tmp_path, monkeypatch):
    import json

    import speakers

    timeline = speakers.SpeakerTimeline(None, People(Person("p1", "Asha")), Rep(tmp_path))
    monkeypatch.setattr(timeline, "_read_indicator", lambda present: set())
    clock = [10.0]
    monkeypatch.setattr(speakers.time, "monotonic", lambda: clock[0])
    timeline.last_diagnose = 1e9
    timeline.heard("p1", "Asha", 1000.0)
    timeline.tick()
    clock[0] += 0.5
    timeline.heard(None, "Stranger", 1000.5)
    timeline.tick()
    clock[0] += 0.6
    timeline.tick()   # Asha's caption stopped changing 1.1 s ago: she stopped
    clock[0] += 0.6
    timeline.tick()
    lines = [json.loads(line) for line in (tmp_path / "speakers.jsonl").read_text().splitlines()]
    assert [(x["name"], x["event"], x["source"]) for x in lines] == [
        ("Asha", "started", "captions"), ("Stranger", "started", "captions"),
        ("Asha", "stopped", "captions"), ("Stranger", "stopped", "captions")]
    assert lines[0]["id"] == "p1" and lines[1]["id"] is None
    assert timeline.speaking_between(999.0, 1000.2) == {"p1": 1}


# ---- giving slots to people ----

def chunk(slot, t, db=-20.0):
    return {"slot": slot, "t": int(t * 1000), "db": db, "pcm": ""}


def splitter(script):
    """script: {person: [(start, end), ...]} of caption changes every 0.3 s (shifted 0.5 s after the voice)."""
    changes = {k: [round(s + 0.5 + 0.3 * i, 2) for s, e in spans for i in range(int((e - s) / 0.3) + 1)]
               for k, spans in script.items()}

    def between(a, b):
        return {k: n for k, ts in changes.items() if (n := sum(1 for t in ts if a <= t <= b))}

    return voices.VoiceSplitter(between)


def named(sp, chunks):
    sp.feed(chunks)
    return [(who, c["slot"], c["t"] / 1000) for who, c in sp.ready(flush=True)]


def test_one_speaker_one_slot():
    sp = splitter({"asha": [(0, 3)]})
    out = named(sp, [chunk(0, t / 5) for t in range(15)])
    assert {who for who, *_ in out} == {"asha"}


def test_turns_in_the_same_slot_with_a_pause():
    sp = splitter({"asha": [(0, 3)], "ravi": [(3.4, 6.4)]})
    pieces = [chunk(0, t / 5) for t in range(15)] + [chunk(0, 3.4 + t / 5) for t in range(15)]
    out = named(sp, pieces)
    asha = [t for who, _, t in out if who == "asha"]
    ravi = [t for who, _, t in out if who == "ravi"]
    assert max(asha) < 3.0 and min(ravi) >= 3.4
    assert len(ravi) >= 13, "almost all of Ravi's turn is his"


def test_two_at_once_loudest_slot_to_the_growing_caption():
    sp = splitter({"asha": [(0, 4)], "ravi": [(2, 4)]})
    pieces = [chunk(0, t / 5) for t in range(20)] + [chunk(1, 2 + t / 5, -30) for t in range(10)]
    out = named(sp, pieces)
    assert {who for who, slot, _ in out if slot == 0} == {"asha"}
    assert {who for who, slot, _ in out if slot == 1} == {"ravi"}


def test_sound_with_nobody_talking_is_unknown():
    sp = splitter({})
    out = named(sp, [chunk(2, t / 5) for t in range(5)])
    assert {who for who, *_ in out} == {None} and sp.unknown == 5


def test_quiet_pieces_are_left_out_and_pieces_wait_for_captions():
    sp = splitter({"asha": [(0, 3)]})
    sp.feed([chunk(0, 1.0, db=-80)])
    assert sp.ready(flush=True) == []
    sp.feed([chunk(0, 1.0)])
    assert sp.ready(now=2.0) == [], "too recent: its captions may not be there yet"
    assert [who for who, _ in sp.ready(now=1.0 + voices.DELAY + 0.1)] == ["asha"]


def test_after_a_pause_the_new_caption_block_wins_over_trailing_corrections():
    # Asha stops at 3.0; her caption still gets corrections until 3.8. Ravi starts at 3.4 in the same slot,
    # with a new caption block from 3.9. Without the block start, Asha's trailing changes could win.
    changes = {"asha": [0.5 + 0.3 * i for i in range(10)] + [3.6, 3.8], "ravi": [3.9, 4.6, 5.3]}
    starts = {"asha": [0.5], "ravi": [3.9]}
    sp = voices.VoiceSplitter(
        lambda a, b: {k: n for k, ts in changes.items() if (n := sum(1 for t in ts if a <= t <= b))},
        started_between=lambda a, b: {k for k, ts in starts.items() if any(a <= t <= b for t in ts)})
    out = named(sp, [chunk(0, t / 5) for t in range(15)] + [chunk(0, 3.4 + t / 5) for t in range(8)])
    assert [who for who, _, t in out if t >= 3.4] == ["ravi"] * 8
