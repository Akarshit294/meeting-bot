"""Each person's sound files, made at the end of the call: `audio/<name>.wav` and `audio/unknown.wav`, from
Meet's sound slots (`audio/slot-N.wav`) and who spoke when (`speakers.jsonl`).

Meet plays each speaker through one of its 3 `<audio>` elements ("slots"). In the second real test
(2026-11-05, two people) each person stayed in their own slot for the whole call, while the captions came
on only 30 s after the bot got in and lagged 1 to 3 s behind the words. Splitting moment by moment on the
captions put 33 of 37 s of speech in unknown.wav. So the split is made once, at the end, per slot:

1. Each slot is cut into stretches of sound (a pause of 0.3 s or more ends a stretch).
2. A stretch is named after the person `speakers.jsonl` clearly shows talking during it. Meet's speaking
   indicator is on time; the captions come 0.5 to 2.5 s after the words, so a span that started or stopped
   by the captions is moved earlier (SHIFT_START, SHIFT_END).
3. A slot where one person has at least 80% of the named stretches belongs to that person: all its sound
   goes to them, also from before the captions came on and short words no caption showed. Only a stretch
   clearly named after someone who owns no other slot (or whose own slot is quiet then) stays theirs.
4. In a slot nobody owns (in a bigger call Meet moves people between slots), each stretch goes to the person
   it was named after, and an unnamed one to unknown.wav.

Every file starts when the recording started and is as long as `mixed.wav`, so they all line up.

Run it again on any run folder (Python 3.9 or later, no extra packages):
    python3 bot/voicefiles.py output/<run folder>
The per-person files of that folder are replaced; the earlier ones are kept in `audio/old-split/`.
"""
from __future__ import annotations

import array
import json
import math
import re
import shutil
import struct
import sys
import warnings
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import audioop  # Python 3.12 and older: fast level and mixing
    except ImportError:  # Python 3.13+: plain Python (slower, same result)
        audioop = None

RATE = 16_000
FRAME = 0.1           # seconds per level reading
LOUD_DB = -50.0       # a reading louder than this is sound (Meet's silence measured -85 dB or lower)
JOIN_GAP = 0.2        # quiet this long or shorter does not end a stretch (a pause between words)
EDGE = 0.1            # a stretch keeps this much around its sound (the ends of words are quiet)
OWN_SHARE = 0.8       # a slot belongs to one person when they have this share of its named sound
OWN_MIN = 1.0         # ...and at least this many seconds of it
CLEAR = 1.5           # a stretch is clearly one person's when their evidence is 1.5 times anyone else's
MIN_EVIDENCE = 0.3    # seconds of evidence needed to name a stretch (less for a short stretch)
# Where the words are, against the times in speakers.jsonl, by the source of the "started" / "stopped" line.
SHIFT_START = {"indicator": -0.3, "both": -0.3, "captions": -2.0}
SHIFT_END = {"indicator": 0.0, "both": 0.0, "captions": -1.0}
KEEP = {"mixed.wav"}  # never replaced (and slot-N.wav)
START_LINE = re.compile(r"all files start at (\d+(?:\.\d+)?)")


@dataclass
class Stretch:
    slot: int          # 1, 2, 3 (as in slot-N.wav)
    start: float       # seconds from the start of the files
    end: float
    named: str | None = None  # the person the evidence names (a key from speakers.jsonl)
    score: float = 0.0         # seconds of evidence for them
    person: str | None = None  # where the sound goes in the end (None: unknown.wav)


@dataclass
class Span:
    key: str
    start: float
    end: float


def safe_name(name: str) -> str:
    return re.sub(r"[^\w .-]+", "_", name).strip() or "person"


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


# -- reading --

def read_samples(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path.name}: not 16-bit mono")
        return w.readframes(w.getnframes())


def level_db(chunk: bytes) -> float:
    if not chunk:
        return -120.0
    if audioop:
        rms = audioop.rms(chunk, 2)
    else:
        samples = array.array("h", chunk)
        if sys.byteorder == "big":
            samples.byteswap()
        picked = samples[::4]  # every 4th sample is enough to tell sound from silence
        rms = math.sqrt(sum(x * x for x in picked) / len(picked))
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


def levels(pcm: bytes) -> list[float]:
    step = round(FRAME * RATE) * 2
    return [level_db(pcm[i:i + step]) for i in range(0, len(pcm), step)]


def stretches_of(slot: int, readings: list[float]) -> list[Stretch]:
    """The stretches of sound in one slot."""
    out: list[Stretch] = []
    loud = [db > LOUD_DB for db in readings]
    gap = round(JOIN_GAP / FRAME)
    total = len(loud) * FRAME
    i = 0
    while i < len(loud):
        if not loud[i]:
            i += 1
            continue
        last = j = i
        while j < len(loud) and j - last <= gap:
            if loud[j]:
                last = j
            j += 1
        out.append(Stretch(slot, max(0.0, i * FRAME - EDGE), min(total, (last + 1) * FRAME + EDGE)))
        i = last + 1
    return out


def read_spans(path: Path, t0: float, length: float) -> tuple[list[Span], dict[str, str]]:
    """Who talked when, from speakers.jsonl, moved to where the words are."""
    spans: list[Span] = []
    names: dict[str, str] = {}
    open_: dict[str, tuple[float, str]] = {}
    lines = path.read_text().splitlines() if path.exists() else []
    for line in lines:
        try:
            x = json.loads(line)
            key = x.get("id") or f"name:{x.get('name')}"
            t = epoch(x["t"]) - t0
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        names[key] = x.get("name") or key.removeprefix("name:")
        source = x.get("source", "captions")
        if x.get("event") == "started" and key not in open_:
            open_[key] = (t, source)
        elif x.get("event") == "stopped" and key in open_:
            start, began = open_.pop(key)
            spans.append(_span(key, start, began, t, source))
    for key, (start, began) in open_.items():
        spans.append(_span(key, start, began, length, "indicator"))
    return spans, names


def _span(key: str, start: float, began: str, end: float, ended: str) -> Span:
    a = start + SHIFT_START.get(began, SHIFT_START["captions"])
    b = end + SHIFT_END.get(ended, SHIFT_END["captions"])
    return Span(key, a, max(b, a + 0.2))


# -- naming --

def name_stretches(stretches: list[Stretch], spans: list[Span]) -> None:
    """Step 2: name a stretch after the one person the evidence clearly shows talking during it."""
    for s in stretches:
        scores: dict[str, float] = {}
        for span in spans:
            overlap = min(s.end, span.end) - max(s.start, span.start)
            if overlap > 0:
                scores[span.key] = scores.get(span.key, 0.0) + overlap
        if not scores:
            continue
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best, value = ranked[0]
        if value < min(MIN_EVIDENCE, 0.5 * (s.end - s.start)):
            continue
        if len(ranked) > 1 and value < CLEAR * ranked[1][1]:
            continue
        s.named, s.score = best, value
    one_slot_at_a_time(stretches)


def one_slot_at_a_time(stretches: list[Stretch]) -> None:
    """A person talks in one slot at a time. When stretches in two slots at the same moment are named after the
    same person (their late captions also cover the other person's sound), the evidence is about one of them:
    the one in the slot where that person's other named sound is, else the one it covers most. The other
    stretch loses its name (its slot's owner may still get it)."""
    named = [s for s in stretches if s.named]
    pairs = [(a, b) for i, a in enumerate(named) for b in named[i + 1:]
             if a.named == b.named and a.slot != b.slot and a.start < b.end and b.start < a.end]
    if not pairs:
        return
    in_pairs = {id(x) for pair in pairs for x in pair}
    home: dict[str, dict[int, float]] = {}
    for s in named:
        if id(s) not in in_pairs:
            per = home.setdefault(s.named, {})
            per[s.slot] = per.get(s.slot, 0.0) + s.end - s.start
    for a, b in pairs:
        if a.named is None or a.named != b.named:
            continue  # settled by an earlier pair
        per = home.get(a.named, {})
        here, there = per.get(a.slot, 0.0), per.get(b.slot, 0.0)
        keep_a = here > there if here != there else a.score >= b.score
        (b if keep_a else a).named = None


def owners_of(stretches: list[Stretch]) -> dict[int, str]:
    """Step 3: the slots that belong to one person."""
    named: dict[int, dict[str, float]] = {}
    for s in stretches:
        if s.named:
            per = named.setdefault(s.slot, {})
            per[s.named] = per.get(s.named, 0.0) + s.end - s.start
    owners: dict[int, tuple[str, float]] = {}
    for slot, per in named.items():
        total = sum(per.values())
        key, value = max(per.items(), key=lambda kv: kv[1])
        if value >= OWN_MIN and value >= OWN_SHARE * total:
            owners[slot] = (key, value)
    # One person, one slot: if two slots look like the same person's, the one with more of them keeps it.
    best: dict[str, tuple[int, float]] = {}
    for slot, (key, value) in owners.items():
        if key not in best or value > best[key][1]:
            best[key] = (slot, value)
    return {slot: key for slot, (key, _) in owners.items() if best[key][0] == slot}


def assign(stretches: list[Stretch], owners: dict[int, str]) -> None:
    """Steps 3 and 4: where each stretch's sound goes."""
    slot_of = {key: slot for slot, key in owners.items()}
    by_slot: dict[int, list[Stretch]] = {}
    for s in stretches:
        by_slot.setdefault(s.slot, []).append(s)

    def sounding(slot: int, start: float, end: float) -> bool:
        return any(o.start < end and start < o.end for o in by_slot.get(slot, []))

    for s in stretches:
        owner = owners.get(s.slot)
        if owner is None:
            s.person = s.named
        elif s.named is None or s.named == owner:
            s.person = owner
        else:
            # Named after someone else. If that person has their own slot with sound at this moment, the
            # evidence is about that sound (captions lag): the stretch stays the owner's.
            other = slot_of.get(s.named)
            s.person = owner if other is not None and sounding(other, s.start, s.end) else s.named


# -- writing --

def _header(samples: int) -> bytes:
    data = samples * 2
    return (b"RIFF" + struct.pack("<I", 36 + data) + b"WAVE" + b"fmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16) + b"data" + struct.pack("<I", data))


def _mix(a: bytes, b: bytes) -> bytes:
    if audioop:
        return audioop.add(a, b, 2)
    x, y = array.array("h", a), array.array("h", b)
    return array.array("h", (max(-32768, min(32767, p + q)) for p, q in zip(x, y))).tobytes()


def write_file(path: Path, samples: int, pieces: list[tuple[int, bytes]]) -> None:
    """A WAV `samples` long, silent except for the pieces (sample offset, PCM); overlapping pieces are mixed."""
    with open(path, "wb") as f:
        f.write(_header(samples))
        zeros = b"\0\0" * RATE
        left = samples
        while left > 0:
            n = min(left, RATE)
            f.write(zeros[: n * 2])
            left -= n
    with open(path, "r+b") as f:
        for offset, pcm in sorted(pieces, key=lambda p: p[0]):
            pcm = pcm[: max(0, samples - offset) * 2]
            if not pcm:
                continue
            f.seek(44 + offset * 2)
            there = f.read(len(pcm))
            if there.count(0) != len(there):
                pcm = _mix(there, pcm)
            f.seek(44 + offset * 2)
            f.write(pcm)


def split(audio_dir: Path, speakers: Path, t0: float) -> str:
    """Write audio/<name>.wav and audio/unknown.wav. Returns a one-line summary for the log."""
    slot_paths = sorted(audio_dir.glob("slot-*.wav"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not slot_paths:
        return "no slot files, so no file per person"
    pcm = {int(re.sub(r"\D", "", p.stem)): read_samples(p) for p in slot_paths}
    mixed = audio_dir / "mixed.wav"
    samples = max(len(x) // 2 for x in pcm.values())
    if mixed.exists():
        with wave.open(str(mixed), "rb") as w:
            samples = max(samples, w.getnframes())
    length = samples / RATE
    spans, names = read_spans(speakers, t0, length)
    stretches = [s for slot, data in pcm.items() for s in stretches_of(slot, levels(data))]
    name_stretches(stretches, spans)
    owners = owners_of(stretches)
    assign(stretches, owners)

    # The files: one per person with sound, unknown.wav for the rest (always written, so its absence is no question).
    files: dict[str, str] = {}
    taken: set[str] = set()
    for key in sorted({s.person for s in stretches if s.person}, key=lambda k: names.get(k, k)):
        base = safe_name(names.get(key, key.removeprefix("name:")))
        if base.lower().startswith("slot-") or base.lower() in ("mixed", "unknown"):
            base = "person " + base
        name, n = base, 1
        while name.lower() in taken:
            n += 1
            name = f"{base}-{n}"
        taken.add(name.lower())
        files[key] = name
    pieces: dict[str, list[tuple[int, bytes]]] = {name: [] for name in files.values()}
    pieces["unknown"] = []
    seconds: dict[str, float] = {name: 0.0 for name in pieces}
    for s in stretches:
        name = files[s.person] if s.person else "unknown"
        a, b = round(s.start * RATE), round(s.end * RATE)
        pieces[name].append((a, pcm[s.slot][a * 2:b * 2]))
        seconds[name] += s.end - s.start

    old = [p for p in audio_dir.glob("*.wav") if p.name not in KEEP and not p.name.startswith("slot-")]
    if old:
        keep = audio_dir / "old-split"
        if keep.exists():
            shutil.rmtree(keep)
        keep.mkdir()
        for path in old:
            path.rename(keep / path.name)
    for name, parts in pieces.items():
        write_file(audio_dir / f"{name}.wav", samples, parts)

    slots = []
    for slot in sorted(pcm):
        if slot in owners:
            slots.append(f"slot {slot} is {names.get(owners[slot], owners[slot])}'s")
        elif any(s.slot == slot for s in stretches):
            slots.append(f"slot {slot} has more than one person")
        else:
            slots.append(f"slot {slot} had no sound")
    people = ", ".join(f"{name} {seconds[name]:.1f} s" for name in sorted(files.values(), key=str.lower))
    return f"{'; '.join(slots)}. Sound per file: {people + ', ' if people else ''}unknown {seconds['unknown']:.1f} s"


def start_time(run_dir: Path) -> float | None:
    """When the run's sound files start (wall clock), from bot.log."""
    log = run_dir / "bot.log"
    found = START_LINE.search(log.read_text(errors="replace")) if log.exists() else None
    return float(found.group(1)) if found else None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python3 bot/voicefiles.py output/<run folder>")
        return 2
    run_dir = Path(argv[1])
    t0 = start_time(run_dir)
    if t0 is None:
        print(f"{run_dir}: no 'all files start at' line in bot.log (was --save-media on?)")
        return 1
    print(split(run_dir / "audio", run_dir / "speakers.jsonl", t0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
