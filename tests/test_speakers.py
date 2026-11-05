"""Who spoke when (bot/speakers.py): the readings that are not Docker tests."""
import json
from datetime import datetime

import speakers

WALL = 1_800_000_000.0


class Nothing:
    def count(self):
        return 0


class Page:
    def locator(self, _selector):
        return Nothing()


class People:
    class roster:
        present = {}


class Rep:
    def __init__(self, folder):
        self.run_dir = folder
        self.lines = []

    def log(self, text):
        self.lines.append(text)

    def event(self, *_a, **_k):
        pass


def test_meets_speaking_class_is_looked_for_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SPEAKING_CLASSES", raising=False)
    sp = speakers.SpeakerTimeline(Page(), People(), Rep(tmp_path))
    assert "[data-participant-id]:has(.wEsLMd)" in sp.selector()
    assert '[data-participant-id][class*="speaking"]' in sp.selector()  # the fake meeting's


def test_a_stall_ends_open_spans_where_the_last_reading_saw_them(tmp_path, monkeypatch):
    clock, wall = [100.0], [WALL]
    monkeypatch.setattr(speakers.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(speakers.time, "time", lambda: wall[0])

    def later(seconds):
        clock[0] += seconds
        wall[0] += seconds

    sp = speakers.SpeakerTimeline(Page(), People(), Rep(tmp_path))
    sp.heard("dev/1", "Asha", wall[0])
    sp.tick()
    later(0.3)
    sp.heard("dev/1", "Asha", wall[0])
    sp.tick()
    later(4.0)  # the bot was busy for 4 s (a share starting); Asha's caption still changes
    sp.heard("dev/1", "Asha", wall[0])
    sp.tick()
    lines = [json.loads(x) for x in (tmp_path / "speakers.jsonl").read_text().splitlines()]
    got = [(x["event"], round(datetime.fromisoformat(x["t"]).timestamp() - WALL, 1)) for x in lines]
    # Stopped where the last reading (0.3 s) saw her, plus one reading; started again after the stall.
    assert got == [("started", 0.0), ("stopped", 0.5), ("started", 4.3)]
