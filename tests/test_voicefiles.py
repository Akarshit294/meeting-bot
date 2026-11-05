"""Each person's sound files, made at the end of the call from the slots and speakers.jsonl (bot/voicefiles.py)."""
import json
import math
import struct
from datetime import datetime, timezone

import voicefiles as vf

T0 = 1_800_000_000.0  # when the files start (wall clock)
RATE = 16_000


def write_wav(path, seconds, pieces):
    """A 16-bit mono WAV `seconds` long with tones at (start, end, hz)."""
    samples = [0] * int(seconds * RATE)
    for start, end, hz in pieces:
        for i in range(int(start * RATE), min(len(samples), int(end * RATE))):
            samples[i] = int(8000 * math.sin(2 * math.pi * hz * i / RATE))
    data = struct.pack(f"<{len(samples)}h", *samples)
    path.write_bytes(vf._header(len(samples)) + data)


def speakers_line(t, key, name, event, source="captions"):
    iso = datetime.fromtimestamp(T0 + t, timezone.utc).isoformat(timespec="milliseconds")
    return json.dumps({"t": iso, "id": key, "name": name, "event": event, "source": source})


def make_run(tmp_path, seconds, slots, spans):
    """slots: {n: [(start, end, hz)]}; spans: [(key, name, start, stop, source)] as speakers.jsonl would show."""
    audio = tmp_path / "audio"
    audio.mkdir()
    for n, pieces in slots.items():
        write_wav(audio / f"slot-{n}.wav", seconds, pieces)
    write_wav(audio / "mixed.wav", seconds, [p for pieces in slots.values() for p in pieces])
    lines = []
    for key, name, start, stop, source in spans:
        lines.append((start, speakers_line(start, key, name, "started", source)))
        lines.append((stop, speakers_line(stop, key, name, "stopped", source)))
    (tmp_path / "speakers.jsonl").write_text("".join(line + "\n" for _, line in sorted(lines)))
    (tmp_path / "bot.log").write_text(f"[10:00:00] media: saving ... (all files start at {T0:.3f}, wall clock)\n")
    return audio


def loud_seconds(path):
    """The 0.1 s frames with sound, as (start, end) runs in seconds."""
    data = path.read_bytes()[44:]
    step = int(0.1 * RATE) * 2
    loud = [vf.level_db(data[i:i + step]) > vf.LOUD_DB for i in range(0, len(data), step)]
    runs, start = [], None
    for i, on in enumerate(loud + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((round(start * 0.1, 1), round(i * 0.1, 1)))
            start = None
    return runs


def covers(runs, start, end):
    return any(a <= start + 0.05 and end - 0.05 <= b for a, b in runs)


def test_each_person_keeps_their_slot_like_the_real_test(tmp_path):
    # Akarshit in slot 1, Subrat in slot 2 for the whole call (as in the real test of 2026-11-05). The captions
    # came on late: only the stretches at 12-14 (Akarshit) and 15-17 (Subrat) have any; they lag ~1.5 s.
    audio = make_run(tmp_path, 20.0, {
        1: [(1, 3, 300), (8, 9, 300), (12, 14, 300), (15.5, 16.5, 300)],
        2: [(2, 6, 500), (15, 17, 500)],
        3: [],
    }, [
        ("dev/142", "Akarshit", 13.5, 15.5, "captions"),
        ("dev/145", "Subrat Parida", 16.5, 18.6, "captions"),
    ])
    summary = vf.split(audio, tmp_path / "speakers.jsonl", T0)
    assert summary.startswith("slot 1 is Akarshit's; slot 2 is Subrat Parida's; slot 3 had no sound."), summary
    akarshit, subrat = loud_seconds(audio / "Akarshit.wav"), loud_seconds(audio / "Subrat Parida.wav")
    for start, end in ((1, 3), (8, 9), (12, 14), (15.5, 16.5)):  # also before the captions, and while Subrat talks
        assert covers(akarshit, start, end), (start, end, akarshit)
    for start, end in ((2, 6), (15, 17)):
        assert covers(subrat, start, end), (start, end, subrat)
    assert not covers(akarshit, 4, 5.5) and not covers(subrat, 8, 9)
    assert loud_seconds(audio / "unknown.wav") == []
    assert sorted(p.name for p in audio.glob("*.wav")) == [
        "Akarshit.wav", "Subrat Parida.wav", "mixed.wav", "slot-1.wav", "slot-2.wav", "slot-3.wav", "unknown.wav"]


def test_people_taking_turns_in_one_slot_like_the_fake_meeting(tmp_path):
    # The fake meeting puts whoever speaks in the first free slot: everyone in slot 1, 2.6 s turns, 0.4 s pauses.
    # Its captions come 0.5 s behind the voice; a speaker "stops" ~2.3 s after their voice does.
    turns = [("a", "Asha", 1.0, 300), ("r", "Ravi", 4.0, 500), ("m", "Meera", 7.0, 700), ("a", "Asha", 10.0, 300)]
    audio = make_run(tmp_path, 24.0, {
        1: [(on, on + 2.6, hz) for _, _, on, hz in turns] + [(20, 21, 900)],  # 20-21: nobody's captions
        2: [], 3: [],
    }, [(key, name, on + 0.5, on + 2.6 + 2.3, "captions") for key, name, on, _ in turns])
    summary = vf.split(audio, tmp_path / "speakers.jsonl", T0)
    assert "slot 1 has more than one person" in summary, summary
    asha, ravi, meera = (loud_seconds(audio / f"{n}.wav") for n in ("Asha", "Ravi", "Meera"))
    assert covers(asha, 1.0, 3.6) and covers(asha, 10.0, 12.6) and not covers(asha, 4.0, 6.6)
    assert covers(ravi, 4.0, 6.6) and not covers(ravi, 1.0, 3.6) and not covers(ravi, 7.0, 9.6)
    assert covers(meera, 7.0, 9.6) and not covers(meera, 10.0, 12.6)
    assert covers(loud_seconds(audio / "unknown.wav"), 20, 21)  # no clue and no owner: unknown


def test_the_speaking_indicator_is_taken_as_on_time(tmp_path):
    # Two people one after the other in slot 1, no pause between them that would end a stretch... but the
    # indicator (on time) names each stretch; captions (late) would have named the second one wrongly.
    audio = make_run(tmp_path, 12.0, {1: [(1, 4, 300), (4.5, 7, 500)], 2: [], 3: []}, [
        ("a", "Asha", 1.1, 4.3, "indicator"),
        ("r", "Ravi", 4.6, 7.3, "indicator"),
    ])
    vf.split(audio, tmp_path / "speakers.jsonl", T0)
    assert covers(loud_seconds(audio / "Asha.wav"), 1, 4) and not covers(loud_seconds(audio / "Asha.wav"), 5, 7)
    assert covers(loud_seconds(audio / "Ravi.wav"), 4.5, 7)


def test_a_third_person_in_someone_elses_slot_keeps_their_sound(tmp_path):
    # Slot 1 is Asha's; Meera (no slot of her own) says something in it once, clearly shown by the indicator.
    audio = make_run(tmp_path, 20.0, {1: [(1, 4, 300), (6, 9, 300), (12, 13, 700)], 2: [], 3: []}, [
        ("a", "Asha", 1.1, 4.2, "indicator"),
        ("a", "Asha", 6.1, 9.2, "indicator"),
        ("m", "Meera", 12.1, 13.2, "indicator"),
    ])
    vf.split(audio, tmp_path / "speakers.jsonl", T0)
    assert covers(loud_seconds(audio / "Meera.wav"), 12, 13)
    assert not covers(loud_seconds(audio / "Asha.wav"), 12, 13)


def test_rerun_on_a_run_folder_keeps_the_old_files(tmp_path, capsys):
    audio = make_run(tmp_path, 6.0, {1: [(1, 3, 300)], 2: [], 3: []}, [("a", "unknown", 1.1, 3.2, "indicator")])
    (audio / "Asha.wav").write_bytes(vf._header(0))  # what an earlier split left
    assert vf.main(["voicefiles.py", str(tmp_path)]) == 0
    assert "slot 1 is unknown's" in capsys.readouterr().out
    assert (audio / "old-split" / "Asha.wav").exists()
    assert covers(loud_seconds(audio / "person unknown.wav"), 1, 3)  # a person called "unknown" is not unknown.wav
    assert loud_seconds(audio / "unknown.wav") == []
    every = {p.name: p.stat().st_size for p in audio.glob("*.wav")}
    assert len(set(every.values())) == 1, every  # all the same length: they line up


def test_no_slots_no_files(tmp_path):
    (tmp_path / "audio").mkdir()
    assert vf.split(tmp_path / "audio", tmp_path / "speakers.jsonl", T0) == "no slot files, so no file per person"
    assert vf.main(["voicefiles.py", str(tmp_path / "missing")]) == 1
