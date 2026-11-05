"""Task R2: the recordings in the run folder (pure file logic, no Docker)."""
from __future__ import annotations

import base64
import struct
import wave

from fastapi.testclient import TestClient

import media
from manager.app import create_app
from manager.config import Settings
from manager.store import Bot, Store
from test_api import FakeRunner


def tone(n: int, value: int = 1000) -> bytes:
    return struct.pack(f"<{n}h", *([value] * n))


def read(path):
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


def test_wav_lines_up_with_silence_and_drops_overlaps(tmp_path):
    w = media.WavWriter(tmp_path / "a.wav", t0=100.0)
    w.write_at(100.5, tone(3200))          # 0.5 s of silence first, then 200 ms
    w.write_at(100.6, tone(3200, 2000))    # overlaps by 100 ms: only its second half is kept
    w.pad_to(101.0)
    w.close()
    data = struct.unpack(f"<{len(read(tmp_path / 'a.wav')) // 2}h", read(tmp_path / "a.wav"))
    assert len(data) == 16000
    assert set(data[:8000]) == {0}
    assert set(data[8000:11200]) == {1000}
    assert set(data[11200:12800]) == {2000}
    assert set(data[12800:]) == {0}


def test_wav_is_valid_while_still_open(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "PATCH_EVERY", 0)
    w = media.WavWriter(tmp_path / "b.wav", t0=0.0)
    w.write_at(0.0, tone(1600))
    w.write_at(0.1, tone(1600))
    assert len(read(tmp_path / "b.wav")) == 6400   # the header already says 200 ms (a crash keeps it)
    w.close()


def test_video_parts_in_order_and_one_file_per_recorder(tmp_path):
    files = media.VideoFiles(tmp_path / "video", lambda m: None)
    names = {"p1": "Asha"}.get
    piece = lambda rec, seq, data: {"kind": "video", "rec": rec, "pid": "p1", "seq": seq,
                                    "data": base64.b64encode(data).decode()}
    files.handle({"kind": "video_start", "rec": 1, "pid": "p1"}, names)
    files.handle(piece(1, 1, b"BB"), names)   # arrives early: waits for piece 0
    files.handle(piece(1, 0, b"AA"), names)
    files.handle({"kind": "video_start", "rec": 2, "pid": "p1"}, names)  # the element came back
    files.handle(piece(2, 0, b"CC"), names)
    paths = files.close()
    assert sorted(p.name for p in paths) == ["Asha-2.webm", "Asha.webm"]
    assert (tmp_path / "video" / "Asha.webm").read_bytes() == b"AABB"
    assert (tmp_path / "video" / "Asha-2.webm").read_bytes() == b"CC"


def test_a_recorder_with_no_pieces_leaves_no_file(tmp_path):
    files = media.VideoFiles(tmp_path / "video", lambda m: None)
    files.handle({"kind": "video_start", "rec": 1, "pid": "p2"}, {"p2": "Ravi"}.get)
    files.handle({"kind": "video_end", "rec": 1, "pid": "p2"}, {"p2": "Ravi"}.get)
    assert files.close() == []
    assert not list((tmp_path / "video").glob("*"))


def test_people_files_never_clash_with_the_slot_files(tmp_path):
    saver = media.MediaSaver(tmp_path, lambda m: None)
    saver.person("slot-1", saver.t0, tone(320))
    saver.person("Asha / Q", saver.t0, tone(320))
    saver.close()
    assert sorted(p.name for p in (tmp_path / "audio").glob("*.wav")) == ["Asha _ Q.wav", "mixed.wav", "person slot-1.wav"]


def test_file_list_gives_lengths(tmp_path):
    saver = media.MediaSaver(tmp_path, lambda m: None)
    saver.mixed(tone(16000), saver.t0)
    saver.slot(0, saver.t0 + 0.5, tone(8000))
    saver.close()
    text = media.write_file_list(tmp_path)
    lines = dict(line.rsplit("  ", 1) for line in text.splitlines())
    assert set(lines) == {"audio/mixed.wav", "audio/slot-1.wav"}
    assert float(lines["audio/slot-1.wav"].split()[0]) >= 1.0
    assert (tmp_path / "media-files.txt").read_text() == text


def test_try_sh_reads_the_file_list(tmp_path):
    settings = Settings(api_key="k", state_file=tmp_path / "s.json", output_dir=tmp_path / "out")
    store = Store(settings.state_file)
    store.add(Bot(id="xbot_1", meeting_url="https://meet.google.com/abc-defg-hij", bot_name="M", run_dir="20261105-run"))
    (settings.output_dir / "20261105-run").mkdir(parents=True)
    (settings.output_dir / "20261105-run" / "media-files.txt").write_text("audio/mixed.wav  12.0 s\n")
    c = TestClient(create_app(settings, FakeRunner(), store, watch=False))
    assert c.get("/testrx/files?bot=xbot_1").status_code == 401
    r = c.get("/testrx/files?bot=xbot_1", headers={"Authorization": "Token k"})
    assert r.text == "audio/mixed.wav  12.0 s\n"
