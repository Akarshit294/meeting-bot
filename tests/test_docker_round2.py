"""Round 2 (docs/PLAN.md R1 to R4): one bot in the FAKE meeting with every test option on, like ./try.sh
(needs Docker; `pytest -m docker`).

The fake meeting plays each person through 3 Meet-like <audio> slots (Asha 440 Hz, Ravi 660 Hz, Meera
880 Hz), shows who is speaking, and Meera joins at 20 s, Ravi leaves at 40 s. The bot shares its screen
for a few seconds, then leaves at about 52 s. Then the run folder is checked:
R1 - dom/ snapshots of the page.
R2 - the recordings: audio/mixed.wav, audio/slot-N.wav, video/<name>.webm per camera-on person (Asha gets a
     new <video> element at 30 s: Asha-2.webm), none for Ravi (camera off), screen.mp4, media-files.txt.
R4 - pictures about twice a second for everyone (camera off too, during the screen share too) and
     camera/<name>.png for everyone; "Stop presenting" clicked on screen and confirmed before the screen
     page closes.
R3 - captions (the "Captions and translation" version of the button; no speaking indicator, so who spoke
     when comes only from the captions): captions.jsonl, speakers.jsonl against the fake's script, and
     audio/<name>.wav carrying that person's tone.
"""
from __future__ import annotations

import io
import json
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from dockerstack import (API, ROOT, TESTKIT, api_key, codes, compose, remove_bot_containers, request, request_text,
                         wait_http, wait_status)

pytestmark = pytest.mark.docker
MEET = "https://meet.google.com/abc-defg-hij"
BOT_NAME = "Maya Test"
SCREEN = "http://testkit:9000/screen.html?session_id=s1&token=SECRET2"
MEET_PAGE = "http://testkit:9000/meet/abc-defg-hij?audio=perperson&cc=b&indicator=0"
TONES = {"Asha": 440, "Ravi": 660, "Meera": 880}


def epoch(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def kit(path, body=None):
    return request("POST" if body is not None else "GET", path, body, base=TESTKIT)[1]


@pytest.fixture(scope="module")
def run():
    compose("up", "-d", "--build", env={"TEST_MEET_URL": MEET_PAGE})
    wait_http(API + "/health")
    wait_http(TESTKIT + "/health")
    key = api_key()
    kit("/api/reset", {})
    status, bot = request("POST", "/api/v1/bot/", {
        "meeting_url": MEET, "bot_name": BOT_NAME,
        "output_media": {"camera": {"kind": "webpage", "config": {"url": "http://testkit:9000/camera.html?token=SECRET"}}},
        "recording_config": {"realtime_endpoints": [
            {"type": "webhook", "url": "http://testkit:9000/hooks/call-events", "events": ["participant_events.join", "participant_events.leave"]},
            {"type": "websocket", "url": "ws://testkit:9000/ws/audio", "events": ["audio_separate_raw.data"]},
            {"type": "websocket", "url": "ws://testkit:9000/ws/video", "events": ["video_separate_png.data"]}]},
        "xbot_options": {"save_dom": True, "save_media": True, "record_screen": True},
    }, key=key)
    assert status == 201, bot
    bot_id = bot["id"]
    seen = {"bot_id": bot_id}
    try:
        wait_status(key, bot_id, "in_call_recording", timeout=180)
        seen["recording_at"] = time.time()
        seen["run_dir"] = request_text(f"/testrx/run-dir?bot={bot_id}", key)
        admitted = kit("/api/state")["admitted_at"] / 1000
        seen["admitted_at"] = admitted
        seen["video_early"], seen["video_early_t"] = kit("/api/video"), time.time()
        while time.time() < admitted + 12:
            time.sleep(1)
        seen["share_start"] = request("POST", f"/api/v1/bot/{bot_id}/output_media/",
                                      {"screenshare": {"kind": "webpage", "config": {"url": SCREEN}}}, key=key)
        time.sleep(7)
        seen["share_on"] = kit("/api/state")["share"]
        seen["share_stop"] = request("DELETE", f"/api/v1/bot/{bot_id}/output_media/", {"screenshare": True}, key=key)
        time.sleep(3)
        seen["share_off"] = kit("/api/state")["share"]
        while time.time() < admitted + 30:
            time.sleep(1)
        seen["video_mid"], seen["video_mid_t"] = kit("/api/video"), time.time()
        kit("/api/command", {"do": "recam", "id": "spaces/fakeSpace/devices/201"})  # Asha's video element is replaced
        seen["recam_at"] = time.time()
        while time.time() < admitted + 52:
            time.sleep(1)
        seen["video"], seen["video_t"] = kit("/api/video"), time.time()
        seen["meet_state"] = kit("/api/state")
        seen["left_at"] = time.time()
        assert request("POST", f"/api/v1/bot/{bot_id}/leave_call/", key=key)[0] == 200
        seen["final"] = wait_status(key, bot_id, "done", timeout=120)
        seen["hooks"] = kit("/api/hooks")
        seen["files_text"] = request_text(f"/testrx/files?bot={bot_id}", key)
    finally:
        status, bot = request("GET", f"/api/v1/bot/{bot_id}/", key=key)
        if codes(bot) and codes(bot)[-1] not in ("done", "fatal"):
            request("POST", f"/api/v1/bot/{bot_id}/leave_call/", key=key)
            time.sleep(10)
        remove_bot_containers()
        compose("up", "-d", "manager")  # back to the default fake meeting
    seen["dir"] = ROOT / "output" / "test" / Path(seen["run_dir"]).name
    return seen


def test_the_call_went_as_planned(run):
    assert codes(run["final"])[-2:] == ["call_ended", "done"]
    assert run["share_start"][0] == 200 and run["share_on"]["active"] is True


# ---- R1: the real page, saved ----

def test_dom_snapshots(run):
    folder = run["dir"] / "dom"
    html = sorted(folder.glob("*.html"))
    aria = sorted(folder.glob("*.aria.yml"))
    assert html and len(html) == len(aria), (html, aria)
    reasons = " ".join(p.stem for p in html)
    for why in ("in_call_recording", "captions_on", "people", "share_start", "share_stop", "every_30s"):
        assert why in reasons, f"no snapshot for {why}: {reasons}"
    first = html[0].read_text()
    assert "data-participant-id" in first and "Leave call" in first
    tree = aria[0].read_text()
    assert 'button "Leave call"' in tree, tree[:500]
    assert "Asha" in tree


# ---- R2: the recordings ----

COLOURS = {"Asha": (0xd9, 0x30, 0x25), "Meera": (0x1a, 0x73, 0xe8)}


def in_image(*cmd: str, folder: Path) -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe from the bot image on the run folder (the host may have neither)."""
    return subprocess.run(["docker", "run", "--rm", "-v", f"{folder}:/m:ro", "--entrypoint", cmd[0], "meeting-bot:latest",
                           *cmd[1:]], capture_output=True, timeout=120)


def probe(run, rel: str) -> dict:
    out = in_image("ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", f"/m/{rel}", folder=run["dir"])
    assert out.returncode == 0, out.stderr.decode()
    return json.loads(out.stdout)


def frame_colour(run, rel: str, at: float) -> tuple[int, ...]:
    from PIL import Image

    out = in_image("ffmpeg", "-v", "error", "-ss", str(at), "-i", f"/m/{rel}", "-frames:v", "1", "-f", "image2pipe",
                   "-vcodec", "png", "-", folder=run["dir"])
    assert out.returncode == 0 and out.stdout, out.stderr.decode()
    img = np.asarray(Image.open(io.BytesIO(out.stdout)).convert("RGB")).reshape(-1, 3)
    return tuple(int(v) for v in np.median(img, axis=0))


def wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float)


def peaks(samples: np.ndarray, rate: int = 16000) -> set[int]:
    """The tones present (strongest frequency of each loud 200 ms piece, to the nearest 10 Hz)."""
    out = set()
    for i in range(0, len(samples) - 3200, 3200):
        piece = samples[i:i + 3200]
        if np.sqrt(np.mean(piece ** 2)) < 300:
            continue
        spec = np.abs(np.fft.rfft(piece * np.hanning(len(piece))))
        out.add(int(round(np.fft.rfftfreq(len(piece), 1 / rate)[spec.argmax()] / 10) * 10))
    return out


def test_sound_files_line_up_and_carry_the_call(run):
    audio = run["dir"] / "audio"
    mixed = wav(audio / "mixed.wav")
    seconds = len(mixed) / 16000
    call = run["left_at"] - run["recording_at"]
    assert call - 5 <= seconds <= call + 25, (seconds, call)
    assert {440, 660, 880} <= peaks(mixed), "the mixed file holds everyone's sound"
    slots = sorted(audio.glob("slot-*.wav"))
    assert [p.name for p in slots] == ["slot-1.wav", "slot-2.wav", "slot-3.wav"]
    heard = set()
    for path in slots:
        samples = wav(path)
        assert abs(len(samples) / 16000 - seconds) < 1.5, f"{path.name} is as long as mixed.wav"
        heard |= peaks(samples)
    assert {440, 660, 880} <= heard


def test_one_video_per_camera_on_person_in_their_colour(run):
    folder = run["dir"] / "video"
    names = sorted(p.name for p in folder.glob("*.webm"))
    assert names == ["Asha-2.webm", "Asha.webm", "Meera.webm"], names
    admitted, left, recam = run["admitted_at"], run["left_at"], run["recam_at"]
    in_call = next(epoch(s["created_at"]) for s in run["final"]["status_changes"] if s["code"] == "in_call_not_recording")
    # Recording starts when the bot is in the call; each file ends when its element goes (recam) or the bot leaves.
    expected = {"Asha.webm": recam - in_call, "Asha-2.webm": left - recam, "Meera.webm": left - (admitted + 20)}
    for name, length in expected.items():
        info = probe(run, f"video/{name}")
        assert info["streams"][0]["codec_name"] in ("vp8", "vp9")
        got = float(info["format"]["duration"])
        assert length - 6 <= got <= length + 8, f"{name}: {got:.1f} s, expected about {length:.1f} s"
        colour = frame_colour(run, f"video/{name}", min(2.0, got / 2))
        want = COLOURS[name.split("-")[0].split(".")[0]]
        assert all(abs(a - b) <= 40 for a, b in zip(colour, want)), f"{name}: {colour} vs {want}"


def test_screen_recording(run):
    info = probe(run, "screen.mp4")
    video = info["streams"][0]
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert (video["width"], video["height"]) == (1280, 720)
    assert video["r_frame_rate"] in ("5/1", "5/1.0")
    mixed = wav(run["dir"] / "audio" / "mixed.wav")
    assert abs(float(info["format"]["duration"]) - len(mixed) / 16000) < 4
    head = (run["dir"] / "screen.mp4").read_bytes()[:200_000]
    assert 0 <= head.find(b"moov") < head.find(b"mdat"), "the index is at the front (QuickTime, browsers)"


def test_file_list_with_lengths(run):
    listed = (run["dir"] / "media-files.txt").read_text()
    assert run["files_text"] == listed
    for name in ("audio/mixed.wav", "audio/slot-1.wav", "video/Asha.webm", "video/Meera.webm", "screen.mp4"):
        line = next(line for line in listed.splitlines() if line.startswith(name + "  "))
        assert line.endswith(" s") and float(line.split()[-2]) > 5, line


# ---- R3: captions, who spoke when, each person's sound ----

def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_captions_turned_on_with_the_menu_version(run):
    log = (run["dir"] / "bot.log").read_text()
    assert "captions: turned on (the 'Captions and translation' menu)" in log, log[-3000:]
    assert run["meet_state"]["captions_on"] is True


def test_captions_jsonl_matches_what_meet_showed(run):
    lines = jsonl(run["dir"] / "captions.jsonl")
    assert lines
    assert {x["name"] for x in lines} <= {"Asha", "Ravi", "Meera"}, "never the bot's own 'You'"
    assert {x["name"] for x in lines} >= {"Asha", "Ravi"}
    shown = {(name, text) for _, name, text in run["meet_state"]["caption_log"]}
    finals = [x for x in lines if not x["changing"]]
    assert finals and any(x["changing"] for x in lines)
    ids = {h["body"]["data"]["data"]["participant"]["name"]: h["body"]["data"]["data"]["participant"]["id"]
           for h in run["hooks"] if h["body"]["event"] == "participant_events.join"}
    for x in lines:  # null only while the person is not in the bot's people list yet (just joined)
        assert x["id"] in (ids[x["name"]], None), "the same ids as the join webhooks"
    assert sum(x["id"] is None for x in lines) <= 5
    growing = [x for x in lines if x["changing"]]
    matched = sum((x["name"], x["text"]) in shown for x in growing)
    assert matched >= 0.9 * len(growing), f"{matched}/{len(growing)} caption texts are what the page showed"


def turns(run) -> list[tuple[str, float, float]]:
    """The fake meeting's script: (name, voice on, voice off), wall clock."""
    names = {"spaces/fakeSpace/devices/201": "Asha", "spaces/fakeSpace/devices/202": "Ravi",
             "spaces/fakeSpace/devices/203": "Meera"}
    out, open_ = [], {}
    for t, pid, on in run["meet_state"]["speak_log"]:
        if on:
            open_[pid] = t / 1000
        elif pid in open_:
            out.append((names[pid], open_.pop(pid), t / 1000))
    return out


def test_speakers_follow_the_script(run):
    lines = jsonl(run["dir"] / "speakers.jsonl")
    assert lines and all(x["source"] == "captions" for x in lines), "no indicator in this run: captions only"
    starts = [(x["name"], epoch(x["t"])) for x in lines if x["event"] == "started"]
    stops = [(x["name"], epoch(x["t"])) for x in lines if x["event"] == "stopped"]
    checked = ok = 0
    # Turns from when the bot first read the captions area on (its first captions.jsonl line or speaker line).
    first = jsonl(run["dir"] / "captions.jsonl")[:1] + lines[:1]
    begin = min(epoch(x["t"]) for x in first) + 0.5
    for name, on, off in turns(run):
        if on < begin or off > run["left_at"] - 2:
            continue
        checked += 1
        # The fake's captions come 0.5 s behind the voice and settle ~0.8 s after it stops; a speaker stops ~1 s
        # after their caption stops changing; the bot reads about 3 times a second.
        started = any(n == name and on <= t <= on + 2.0 for n, t in starts)
        stopped = any(n == name and off + 0.5 <= t <= off + 3.5 for n, t in stops)
        ok += started and stopped
    assert checked >= 8 and ok >= checked - 1, f"{ok} of {checked} turns found in speakers.jsonl: {lines[:6]}"


def test_each_person_file_carries_their_tone(run):
    audio = run["dir"] / "audio"
    for name, tone in TONES.items():
        samples = wav(audio / f"{name}.wav")
        pieces = [samples[i:i + 3200] for i in range(0, len(samples) - 3200, 3200)]
        loud = [p for p in pieces if np.sqrt(np.mean(p ** 2)) >= 300]
        assert len(loud) >= 10, f"{name}: only {len(loud)} loud pieces"
        right = 0
        for piece in loud:
            spec = np.abs(np.fft.rfft(piece * np.hanning(len(piece))))
            right += abs(np.fft.rfftfreq(len(piece), 1 / 16000)[spec.argmax()] - tone) <= 15
        # Turn edges can go to the wrong person when the bot is busy (see docs/per-person-audio.md).
        assert right >= 0.8 * len(loud), f"{name}: {right} of {len(loud)} pieces are {tone} Hz"
        mixed = wav(audio / "mixed.wav")
        assert abs(len(samples) - len(mixed)) / 16000 < 1.5, "lines up with the other files"


# ---- R4: fixes from the real test ----

def test_pictures_twice_a_second_for_everyone(run):
    ids = {"Asha": "spaces/fakeSpace/devices/201", "Ravi": "spaces/fakeSpace/devices/202"}
    early, mid, late = run["video_early"]["count"], run["video_mid"]["count"], run["video"]["count"]
    whole = run["video_t"] - run["video_early_t"]  # the screen share started and stopped in this window
    asha = (late[ids["Asha"]] - early.get(ids["Asha"], 0)) / whole
    assert 1.4 <= asha <= 2.6, f"Asha: {asha:.2f} pictures a second"
    # This window holds most layout changes (share start and stop, the People panel, Meera's join): after each,
    # pictures wait until the tiles stand still again.
    ravi = (mid[ids["Ravi"]] - early.get(ids["Ravi"], 0)) / (run["video_mid_t"] - run["video_early_t"])
    assert 1.2 <= ravi <= 2.6, f"Ravi (camera off): {ravi:.2f} pictures a second"
    assert {p.name for p in (run["dir"] / "camera").glob("*.png")} == {"Asha.png", "Ravi.png", "Meera.png"}


def test_stop_presenting_clicks_the_real_button_and_confirms(run):
    assert run["share_off"]["active"] is False
    assert run["share_off"]["stopped"] == "Stop presenting clicked (top bar)", run["share_off"]
    log = (run["dir"] / "bot.log").read_text()
    stopped = log.index("screen share stopped (Meet no longer shows the bot presenting)")
    assert stopped < log.index("screen page closed"), "confirmed before the screen page closed"
    assert "Meet still shows the bot presenting" not in log
