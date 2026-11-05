"""Recordings in the run folder (docs/PLAN.md task R2). For testing: xebit gets the live streams.

`--save-media` (on in ./try.sh, off by default in service mode):
- `audio/mixed.wav`: the whole call's sound, exactly the frames streamed to xebit (16 kHz mono);
- `audio/slot-N.wav`: Meet's sound slots (its `<audio>` elements), raw and unnamed;
- `audio/<name>.wav`, `audio/unknown.wav`: each person's sound (task R3, bot/voices.py);
- `video/<name>.webm`: each person's camera (their tile's `<video>`), recorded from the isolated world.
`--record-screen` (on in ./try.sh): `screen.mp4`, the bot's whole screen at 5 frames a second (ffmpeg).

Every sound file starts at the same moment (when the recording started) and has silence where nothing
came, so they line up. Every file stays playable even if the bot dies: WAV headers are rewritten every
2 s, WebM pieces are appended as they come, the MP4 is written in fragments. On a normal end the WebM
and MP4 files are copied once more by ffmpeg (no re-encoding) so that players see their length.
`media-files.txt` lists every file with its length in seconds.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

RATE = 16_000
PATCH_EVERY = 2.0


def safe_name(name: str) -> str:
    return re.sub(r"[^\w .-]+", "_", name).strip() or "person"


class WavWriter:
    """16-bit mono WAV that is always valid on disk, with silence wherever no sound came (so files line up)."""

    def __init__(self, path: Path, t0: float, rate: int = RATE):
        self.path = path
        self.t0 = t0
        self.rate = rate
        self.samples = 0
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "wb")
        self.f.write(self._header())
        self.f.flush()
        self.last_patch = time.monotonic()
        self.closed = False

    def _header(self) -> bytes:
        data = self.samples * 2
        return (b"RIFF" + struct.pack("<I", 36 + data) + b"WAVE" + b"fmt " +
                struct.pack("<IHHIIHH", 16, 1, 1, self.rate, self.rate * 2, 2, 16) + b"data" + struct.pack("<I", data))

    @property
    def seconds(self) -> float:
        return self.samples / self.rate

    def write_at(self, t: float, pcm: bytes) -> None:
        """`pcm` whose first sample was heard at wall-clock time `t`. A gap before it becomes silence; a
        piece that overlaps what is already written loses its overlapping start."""
        with self.lock:
            if self.closed:
                return
            start = max(0, round((t - self.t0) * self.rate))
            if start > self.samples:
                self._write(b"\0\0" * (start - self.samples))
            elif start < self.samples:
                skip = (self.samples - start) * 2
                if skip >= len(pcm):
                    return
                pcm = pcm[skip:]
            self._write(pcm)

    def pad_to(self, t: float) -> None:
        """Silence up to time `t` (so a file ends when the recording ends)."""
        with self.lock:
            if self.closed:
                return
            end = round((t - self.t0) * self.rate)
            if end > self.samples:
                self._write(b"\0\0" * (end - self.samples))

    def _write(self, pcm: bytes) -> None:
        pcm = pcm[: len(pcm) // 2 * 2]
        self.f.write(pcm)
        self.samples += len(pcm) // 2
        if time.monotonic() - self.last_patch >= PATCH_EVERY:
            self._patch()

    def _patch(self) -> None:
        self.last_patch = time.monotonic()
        self.f.flush()
        here = self.f.tell()
        self.f.seek(0)
        self.f.write(self._header())
        self.f.seek(here)
        self.f.flush()

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self._patch()
            self.f.close()
            self.closed = True


def ffprobe_seconds(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def remux(path: Path, extra: list[str] = ()) -> bool:
    """Copy a file through ffmpeg without re-encoding, so it gets its length and index (players can seek)."""
    if not shutil.which("ffmpeg") or not path.exists() or path.stat().st_size == 0:
        return False
    tmp = path.with_name(path.stem + ".remux" + path.suffix)
    try:
        done = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-c", "copy",
                               *extra, str(tmp)], capture_output=True, timeout=120)
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)
        return False
    if done.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, path)
    return True


class ScreenRecorder:
    """`screen.mp4`: the bot's whole display (what the live view shows), 5 frames a second, with ffmpeg."""

    def __init__(self, path: Path, log: Callable[[str], None], fps: int = 5):
        self.path = path
        self.log = log
        size = os.environ.get("SCREEN_SIZE", "1280x720")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "x11grab", "-draw_mouse", "1", "-framerate", str(fps),
               "-video_size", size, "-i", os.environ.get("DISPLAY", ":99"),
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", "-g", str(fps * 2),
               # Fragments: the file stays playable even if the bot is killed before it can close it.
               "-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", str(path)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=open(path.with_suffix(".log"), "wb"))
        self.started = time.time()
        log(f"screen: recording the whole screen to {path.name} ({fps} frames a second)")

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q")
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        else:
            self.log(f"screen: ffmpeg had stopped by itself (exit {self.proc.returncode}); see screen.log")
        # A plain MP4 with its index at the front: QuickTime and every browser play it.
        remux(self.path, ["-movflags", "+faststart"])
        log = self.path.with_suffix(".log")
        if log.exists() and log.stat().st_size == 0:
            log.unlink()


class VideoFiles:
    """`video/<name>.webm`, one per recorder; a person whose video element goes away and comes back gets
    `<name>-2.webm`, and so on."""

    def __init__(self, folder: Path, log: Callable[[str], None]):
        self.folder = folder
        self.log = log
        self.files: dict[int, dict] = {}  # recorder id -> {"path", "f", "next", "waiting", "bytes"}
        self.parts: dict[str, int] = {}

    def handle(self, message: dict, name_of: Callable[[str], str | None]) -> None:
        rec = message.get("rec")
        kind = message.get("kind")
        if kind == "video_start" or (kind == "video" and rec not in self.files):
            if rec in self.files:
                return
            name = safe_name(name_of(message.get("pid", "")) or "unknown")
            n = self.parts.get(name, 0) + 1
            self.parts[name] = n
            path = self.folder / (f"{name}.webm" if n == 1 else f"{name}-{n}.webm")
            self.folder.mkdir(parents=True, exist_ok=True)
            self.files[rec] = {"path": path, "f": None, "next": 0, "waiting": {}, "bytes": 0, "pid": message.get("pid")}
            self.log(f"media: recording {path.name} ({message.get('w', '?')}x{message.get('h', '?')})")
        if kind == "video":
            import base64

            entry = self.files[rec]
            entry["waiting"][message.get("seq", 0)] = base64.b64decode(message.get("data", ""))
            while entry["next"] in entry["waiting"]:  # the pieces must be written in order
                data = entry["waiting"].pop(entry["next"])
                entry["next"] += 1
                if entry["f"] is None:
                    entry["f"] = open(entry["path"], "wb")
                entry["f"].write(data)
                entry["f"].flush()
                entry["bytes"] += len(data)

    def close(self) -> list[Path]:
        paths = []
        for entry in self.files.values():
            for seq in sorted(entry["waiting"]):  # a piece that never came: keep what follows anyway
                if entry["f"] is None:
                    entry["f"] = open(entry["path"], "wb")
                entry["f"].write(entry["waiting"].pop(seq))
            if entry["f"] is not None:
                entry["f"].close()
                remux(entry["path"])
                paths.append(entry["path"])
        self.files.clear()
        return paths


class MediaSaver:
    """Everything `--save-media` writes. Sound pieces may come from other threads (the mixed stream)."""

    def __init__(self, run_dir: Path, log: Callable[[str], None]):
        self.run_dir = run_dir
        self.audio_dir = run_dir / "audio"
        self.audio_dir.mkdir(exist_ok=True)
        self.log = log
        self.t0 = time.time()
        self.mixed_wav = WavWriter(self.audio_dir / "mixed.wav", self.t0)
        self.slots: dict[int, WavWriter] = {}
        self.people: dict[str, WavWriter] = {}
        self.video = VideoFiles(run_dir / "video", log)
        self.own_reader: threading.Thread | None = None
        self._stop = threading.Event()
        self.closed = False
        log(f"media: saving the call's sound to audio/ and each person's camera to video/ (all files start at "
            f"{self.t0:.3f}, wall clock)")

    # -- sound --

    def mixed(self, pcm: bytes, started: float) -> None:
        self.mixed_wav.write_at(started, pcm)

    def record_mixed_myself(self) -> None:
        """When no mixed stream goes to xebit (per-person mode, or no audio endpoint), read the same sound
        (meet_out.monitor) only for the file."""
        from audio import parec_chunks

        def run():
            while not self._stop.is_set():
                try:
                    for pcm, started in parec_chunks(stop=self._stop):
                        self.mixed(pcm, started)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"media: mixed sound reader failed ({type(exc).__name__}: {exc}); restarting it")
                self._stop.wait(1)

        self.own_reader = threading.Thread(target=run, name="media-mixed", daemon=True)
        self.own_reader.start()

    def slot(self, slot: int, t: float, pcm: bytes) -> None:
        writer = self.slots.get(slot)
        if writer is None:
            writer = self.slots[slot] = WavWriter(self.audio_dir / f"slot-{slot + 1}.wav", self.t0)
        writer.write_at(t, pcm)

    def person(self, name: str, t: float, pcm: bytes) -> None:
        key = safe_name(name)
        if key.startswith("slot-") or key == "mixed":
            key = "person " + key
        writer = self.people.get(key)
        if writer is None:
            writer = self.people[key] = WavWriter(self.audio_dir / f"{key}.wav", self.t0)
        writer.write_at(t, pcm)

    # -- video --

    def video_message(self, message: dict, name_of: Callable[[str], str | None]) -> None:
        try:
            self.video.handle(message, name_of)
        except Exception as exc:  # noqa: BLE001 - a file problem must never touch the call
            self.log(f"media: video piece not saved ({type(exc).__name__}: {exc})")

    # -- the end --

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._stop.set()
        if self.own_reader:
            self.own_reader.join(5)
        end = time.time()
        for writer in [self.mixed_wav, *self.slots.values(), *self.people.values()]:
            writer.pad_to(end)
            writer.close()
        self.video.close()


def list_files(run_dir: Path) -> list[tuple[str, float | None]]:
    """Every recording in the run folder with its length in seconds (also written to media-files.txt)."""
    out = []
    for path in sorted(run_dir.glob("audio/*.wav")) + sorted(run_dir.glob("video/*.webm")) + sorted(run_dir.glob("*.mp4")):
        if path.suffix == ".wav":
            try:
                size = path.stat().st_size
                seconds = max(0, size - 44) / 2 / RATE
            except OSError:
                seconds = None
        else:
            seconds = ffprobe_seconds(path)
        out.append((str(path.relative_to(run_dir)), seconds))
    return out


def write_file_list(run_dir: Path) -> str:
    rows = list_files(run_dir)
    text = "".join(f"{name}  {'?' if s is None else f'{s:.1f} s'}\n" for name, s in rows)
    (run_dir / "media-files.txt").write_text(text or "(no recordings)\n")
    return text
