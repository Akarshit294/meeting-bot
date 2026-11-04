from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, Playwright, sync_playwright

import human_mouse
import live_session

Outcome = tuple[str, str | None, str]

PREJOIN_TIMEOUT = 60

PREJOIN_ERRORS = [
    (re.compile(r"check your meeting code|invalid video call name|meeting code (that )?you entered doesn't work"), "meeting_not_found"),
    (re.compile(r"your meeting code has expired"), "meeting_link_expired"),
    (re.compile(r"you can't join this video call"), "google_meet_bot_blocked"),
    (re.compile(r"problem connecting to this video call"), "google_meet_internal_error"),
]
WAITING_TEXT = re.compile(r"please wait until a meeting host brings you into the call|asking to be let in"
                          r"|someone will let you in|when someone lets you in|waiting for the host")
WAITING_ROOM_EXITS = [
    (re.compile(r"denied your request to join|you left the meeting"), "call_ended", "bot_kicked_from_waiting_room"),
    (re.compile(r"no one (has )?responded to your request"), "call_ended", "call_ended_by_platform_waiting_room_timeout"),
    (re.compile(r"you can't join this video call"), "fatal", "google_meet_bot_blocked"),
]
REMOVED_FROM_CALL = re.compile(r"removed (you )?from the (meeting|call)|you've been removed|you were removed")
CALL_ENDED = re.compile(r"(meeting|call) (has )?ended|ended the (meeting|call)")

NAME_INPUT = 'input[aria-label*="your name" i], input[placeholder*="your name" i]'
JOIN_BUTTON = re.compile(r"ask to join|join now|join anyway", re.I)
LEAVE_BUTTON = re.compile(r"^leave call", re.I)
TIP_BUTTON = re.compile(r"^(got it|dismiss|no thanks)$", re.I)
BLOCKER_BUTTON = re.compile(r"^(reject all|continue without microphone( and camera)?)$", re.I)
CONSENT_BUTTON = re.compile(r"^(join now|join|continue)$", re.I)

LAYOUT_TARGETS = {
    "mic": lambda page: page.get_by_role("button", name=re.compile("^turn off microphone", re.I)),
    "camera": lambda page: page.get_by_role("button", name=re.compile("^turn off camera", re.I)),
    "join": lambda page: page.get_by_role("button", name=JOIN_BUTTON),
    "name": lambda page: page.locator(NAME_INPUT),
    "other": lambda page: page.get_by_role("button", name=re.compile("other ways to join", re.I)),
    "preview": lambda page: page.locator("video"),
}

QUIET_AFTER_KNOCK = 8

STOPPED: Outcome = ("call_ended", "bot_received_leave_call", "stop requested (SIGINT/SIGTERM)")
stop_requested = False
mouse: human_mouse.HumanMouse


def request_stop(_signum, _frame) -> None:
    global stop_requested
    stop_requested = True


class Reporter:
    def __init__(self, run_dir: Path):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir = run_dir
        self.count = 0

    def log(self, message: str) -> None:
        print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {message}", flush=True)

    def status(self, code: str, sub_code: str | None = None, message: str = "", page: Page | None = None) -> None:
        self.count += 1
        event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code": code, "sub_code": sub_code, "message": message}
        with open(self.run_dir / "events.jsonl", "a") as f:
            f.write(json.dumps(event) + "\n")
        self.log(f"STATUS {code}" + (f" [{sub_code}]" if sub_code else "") + (f" - {message}" if message else ""))
        if page is not None:
            self.snapshot(page, f"{self.count:02d}-{code}", html=code in ("fatal", "call_ended"))

    def snapshot(self, page: Page, name: str, html: bool = False) -> None:
        try:
            page.screenshot(path=self.run_dir / f"{name}.png")
            if html:
                (self.run_dir / f"{name}.html").write_text(page.content())
        except PlaywrightError:
            pass


def probe(page: Page) -> dict:
    try:
        text = page.locator("body").inner_text(timeout=5_000)
    except PlaywrightError:
        text = ""
    tiles = page.locator("[data-participant-id]")
    ids = {tiles.nth(i).get_attribute("data-participant-id") for i in range(min(tiles.count(), 50))}
    return {
        "text": text.replace("‘", "'").replace("’", "'").lower(),
        "inCall": page.locator('[aria-label^="Leave call" i]').count() > 0,
        "participants": len(ids),
    }


def excerpt(text: str, limit: int = 200) -> str:
    return " ".join(text.split())[:limit]


def human_click(page: Page, target: Locator) -> None:
    mouse.click_element(page, target)


def dismiss_popups(page: Page, names: re.Pattern = TIP_BUTTON) -> None:
    buttons = page.get_by_role("button", name=names).all()
    buttons += page.get_by_role("alertdialog").get_by_role("button", name=CONSENT_BUTTON).all()
    for button in buttons:
        try:
            if button.is_visible():
                human_click(page, button)
        except PlaywrightError:
            pass


def turn_off(page: Page, device: str) -> bool:
    off = page.get_by_role("button", name=re.compile(f"^turn off {device}", re.I)).first
    on = page.get_by_role("button", name=re.compile(f"^turn on {device}", re.I)).first
    for _ in range(10):
        if on.is_visible():
            return True
        if off.is_visible():
            try:
                human_click(page, off)
            except PlaywrightError:
                pass
        page.wait_for_timeout(500)
    return False


def turn_off_devices(page: Page, rep: Reporter) -> None:
    for device in ("microphone", "camera"):
        if not turn_off(page, device):
            rep.log(f"warning: could not confirm the {device} is off")


def leave(page: Page) -> None:
    try:
        human_click(page, page.get_by_role("button", name=LEAVE_BUTTON).first)
        page.wait_for_timeout(2_000)
    except PlaywrightError:
        pass


def measure_layout(page: Page) -> dict:
    def boxes(keys) -> dict:
        found = {}
        for key in keys:
            try:
                box = LAYOUT_TARGETS[key](page).first.bounding_box(timeout=1_000)
            except PlaywrightError:
                box = None
            if box:
                found[key] = [round(box[k], 1) for k in ("x", "y", "width", "height")]
        return found

    clicked = boxes(human_mouse.MEET_STEPS)
    still_since = started = time.monotonic()
    while time.monotonic() - still_since < 2 and time.monotonic() - started < 15:
        page.wait_for_timeout(250)
        now = boxes(human_mouse.MEET_STEPS)
        if now != clicked:
            clicked, still_since = now, time.monotonic()
    context = boxes(key for key in LAYOUT_TARGETS if key not in human_mouse.MEET_STEPS)
    return {"viewport": mouse.viewport, **clicked, **context}


def sequence_mismatch(page: Page, layout: dict, seq: dict) -> str | None:
    miss = human_mouse.presses_miss(layout, seq)
    if miss:
        return miss
    ox, oy = mouse.origin
    px, py = mouse.position()
    if abs(px - ox - seq["start"]["x"]) > 1 or abs(py - oy - seq["start"]["y"]) > 1:
        return "the pointer is no longer where the recording started"
    return None


def prejoin_clicks(page: Page, rep: Reporter, layout: dict, join_button: Locator, knock: bool) -> None:
    seq = mouse.sequence
    reason = sequence_mismatch(page, layout, seq) if seq else None
    if seq and reason:
        rep.log(f"recorded Meet sequence not used: {reason}")
    if seq and not reason:
        offset = max(abs(a - b) for key in human_mouse.MEET_STEPS for a, b in zip(layout[key][:2], seq["layout"][key][:2]))
        rep.log(f"replaying your recorded Meet sequence exactly (buttons within {offset:g} px of where they were when "
                f"you recorded; every click still lands inside its button)" + ("" if knock else ", stopping before Ask to join"))
        mouse.play_sequence(seq, mouse.origin, before=None if knock else "join")
        if knock:
            time.sleep(QUIET_AFTER_KNOCK)
            if join_button.is_hidden():
                return
            rep.log("the recorded knock didn't register; clicking Ask to join again")
    turn_off_devices(page, rep)
    if knock:
        human_click(page, join_button)
        time.sleep(QUIET_AFTER_KNOCK)
        try:
            join_button.wait_for(state="hidden", timeout=10_000)
        except PlaywrightError:
            raise RuntimeError("clicked Ask to join but the click didn't register. Is another live-view tab "
                               "(an older, interactive one) sending input?") from None


def live_join(page: Page, args: argparse.Namespace, rep: Reporter, join_button: Locator) -> str:
    path = args.mouse.with_name(live_session.SESSION_FILE)
    label = join_button.inner_text(timeout=10_000).strip()
    layout = measure_layout(page)
    recorder = live_session.InputRecorder()
    recorder.start()
    if args.record_session:
        rep.log("RECORDING. Now, at http://localhost:6080/control.html: click the name field, type the name, turn the mic and camera off, "
                "then click Ask to join. Plain typing only, no keyboard shortcuts.")
        player = None
    else:
        session = live_session.load(path)
        via = "through VNC, the way your live view sends input" if args.replay_vnc else "through XTEST"
        rep.log(f"replaying your recorded join {via}: {len(session['events'])} input events over "
                f"{session['events'][-1][0] / 1000:.0f} s; the bot adds nothing of its own")
        if args.replay_vnc:
            replay = (live_session.replay_over_vnc, (args.vnc_client, session["events"], session["start_pointer"]))
        else:
            replay = (live_session.replay, (session["events"],))
        player = threading.Thread(target=replay[0], args=replay[1], daemon=True)
        player.start()

    deadline = time.monotonic() + 300
    while join_button.is_visible():
        if player and not player.is_alive():
            raise RuntimeError("the replay finished but Ask to join was never clicked")
        if time.monotonic() > deadline:
            raise RuntimeError("no knock within 5 minutes")
        page.wait_for_timeout(500)
    if player:
        player.join()
    else:
        page.wait_for_timeout(2_000)
    events = recorder.stop()
    if args.record_session:
        live_session.save(path, recorder, layout)
        rep.log(f"you clicked {label!r}; saved your join to {path}: {len(events)} input events")
    else:
        rep.log(f"the replay clicked {label!r}. The X server saw it as: " + live_session.compare(session["events"], events))
    return label


def join(page: Page, args: argparse.Namespace, rep: Reporter) -> Outcome | None:
    page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)

    name_input = page.locator(NAME_INPUT).first
    join_button = page.get_by_role("button", name=JOIN_BUTTON).first
    deadline = time.monotonic() + PREJOIN_TIMEOUT
    while True:
        if stop_requested:
            return STOPPED
        if "accounts.google.com" in page.url:
            return ("fatal", "meeting_requires_sign_in", "Meet redirected to Google sign-in")
        text = probe(page)["text"]
        for pattern, sub_code in PREJOIN_ERRORS:
            if pattern.search(text):
                return ("fatal", sub_code, excerpt(text))
        dismiss_popups(page, BLOCKER_BUTTON)
        if name_input.is_visible() or join_button.is_visible():
            break
        if time.monotonic() > deadline:
            return ("fatal", "bot_errored", f"no pre-join screen after {PREJOIN_TIMEOUT}s: {excerpt(text)}")
        page.wait_for_timeout(1_000)

    if args.record_session or args.replay_session or args.replay_vnc:
        label = live_join(page, args, rep, join_button)
    else:
        if args.warmup_click and not args.dry_run and name_input.is_visible():
            rep.log("clicking into the name field, as a person does on arrival")
            human_click(page, name_input)
        if args.type_name and name_input.is_visible():
            session = args.mouse.with_name(live_session.SESSION_FILE)
            rhythm = live_session.typing_rhythm(live_session.load(session)["events"]) if session.exists() else []
            rep.log("typing the name key by key through VNC" + (", in the rhythm of your recorded typing" if rhythm else ""))
            human_click(page, name_input)
            live_session.type_over_vnc(mouse.vnc, args.name, rhythm)
        elif name_input.is_visible():
            name_input.fill(args.name)
        label = join_button.inner_text(timeout=10_000).strip()
        if args.knock_delay and not args.dry_run:
            rep.log(f"staying on the pre-join screen for {args.knock_delay} s before knocking")
            page.wait_for_timeout(args.knock_delay * 1_000)
        layout = measure_layout(page)
        if args.save_layout:
            path = args.mouse.with_name(human_mouse.LAYOUT_FILE)
            captured = datetime.now(timezone.utc).isoformat(timespec="seconds")
            path.write_text(json.dumps({**layout, "bot_name": args.name, "captured_at": captured}, indent=1))
            rep.log(f"saved the pre-join layout to {path}: {layout}")
        rep.snapshot(page, "prejoin")
        prejoin_clicks(page, rep, layout, join_button, knock=not args.dry_run)
        if args.dry_run:
            return ("call_ended", "dry_run", f'pre-join screen OK, stopped before clicking "{label}"')
        rep.log(f'clicked "{label}"')

    if "join now" not in label.lower():
        page.wait_for_timeout(2_000)
        rep.status("in_waiting_room", page=page)
    deadline = time.monotonic() + args.waiting_room_timeout
    while time.monotonic() < deadline:
        if stop_requested:
            return STOPPED
        state = probe(page)
        if state["inCall"] and not WAITING_TEXT.search(state["text"]):
            return None
        for pattern, code, sub_code in WAITING_ROOM_EXITS:
            if pattern.search(state["text"]):
                return (code, sub_code, excerpt(state["text"]))
        dismiss_popups(page)
        page.wait_for_timeout(1_000)
    return ("call_ended", "timeout_exceeded_waiting_room", f"not admitted within {args.waiting_room_timeout}s")


def attend(page: Page, args: argparse.Namespace, rep: Reporter) -> Outcome:
    rep.status("in_call_not_recording", page=page)
    turn_off_devices(page, rep)

    started = time.monotonic()
    seen_others = False
    alone_since = None
    controls_missing = 0
    last_count = None
    while True:
        if stop_requested:
            leave(page)
            return STOPPED
        if args.max_duration and time.monotonic() - started > args.max_duration:
            leave(page)
            return ("call_ended", "timeout_exceeded_in_call_not_recording", f"max duration of {args.max_duration}s reached")

        state = probe(page)
        if REMOVED_FROM_CALL.search(state["text"]):
            return ("call_ended", "bot_kicked_from_call", excerpt(state["text"]))
        if state["inCall"]:
            controls_missing = 0
        else:
            controls_missing += 1
            if controls_missing >= 3:
                sub_code = "call_ended_by_host" if CALL_ENDED.search(state["text"]) else None
                return ("call_ended", sub_code, excerpt(state["text"]))

        count = state["participants"]
        if count != last_count:
            rep.log(f"participant tiles on screen: {count}")
            last_count = count
        if count > 1:
            seen_others, alone_since = True, None
        elif count == 1 and seen_others and args.everyone_left_timeout:
            if alone_since is None:
                alone_since = time.monotonic()
            if time.monotonic() - alone_since >= args.everyone_left_timeout:
                leave(page)
                return ("call_ended", "timeout_exceeded_everyone_left", f"alone for {args.everyone_left_timeout}s")

        dismiss_popups(page)
        page.wait_for_timeout(1_000)


def silent_wav() -> str:
    path = Path(tempfile.gettempdir()) / "silence.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48_000)
        f.writeframes(b"\0\0" * 48_000)
    return str(path)


def launch(p: Playwright):
    width, height = os.environ.get("SCREEN_SIZE", "1280x720").split("x")
    return p.chromium.launch(
        channel="chrome" if shutil.which("google-chrome") else None,
        headless=False,
        args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={silent_wav()}",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--lang=en-US",
            "--window-position=0,0",
            f"--window-size={width},{height}",
        ],
        ignore_default_args=["--enable-automation"],
        handle_sigint=False,
        handle_sigterm=False,
    )


def parse_args() -> argparse.Namespace:
    env = os.environ.get
    parser = argparse.ArgumentParser(description="Join a Google Meet call as a signed-out guest.")
    parser.add_argument("url", nargs="?", default=env("MEET_URL"), help="Meet link, e.g. https://meet.google.com/abc-defg-hij")
    parser.add_argument("--name", default=env("BOT_NAME", "Meeting Notetaker"), help="display name shown to participants")
    parser.add_argument("--waiting-room-timeout", type=int, default=int(env("WAITING_ROOM_TIMEOUT", "600")),
                        help="seconds to wait for someone to admit the bot (Meet itself gives up after ~10 min)")
    parser.add_argument("--everyone-left-timeout", type=int, default=int(env("EVERYONE_LEFT_TIMEOUT", "60")),
                        help="leave after being alone in the call this many seconds (0 = never)")
    parser.add_argument("--max-duration", type=int, default=int(env("MAX_DURATION", "7200")),
                        help="leave after this many seconds in the call (0 = no limit)")
    parser.add_argument("--output", type=Path, default=Path(env("OUTPUT_DIR", "/output")), help="where run folders are written")
    parser.add_argument("--trace", action="store_true", default=env("TRACE") == "1", help="also save a Playwright trace.zip")
    parser.add_argument("--dry-run", action="store_true", default=env("DRY_RUN") == "1",
                        help="go through the pre-join screen but stop before asking to join")
    parser.add_argument("--hold", action="store_true", default=env("HOLD") == "1",
                        help="keep the browser open after the run (for the live view) until Ctrl+C")
    parser.add_argument("--mouse", type=Path, default=Path(env("MOUSE_RECORDINGS", str(human_mouse.DEFAULT_RECORDINGS))),
                        help="recorded human mouse movements to replay for every click (see human_mouse.py)")
    parser.add_argument("--save-layout", action="store_true", default=env("SAVE_LAYOUT") == "1",
                        help="save where the pre-join buttons are, next to --mouse, for the /meet recorder (use with --dry-run)")
    parser.add_argument("--knock-delay", type=int, default=int(env("KNOCK_DELAY", "0")),
                        help="seconds to stay on the pre-join screen before knocking, as a person might")
    parser.add_argument("--warmup-click", action="store_true", default=env("WARMUP_CLICK") == "1",
                        help="click into the name field when the pre-join screen appears (use with --knock-delay)")
    parser.add_argument("--record-session", action="store_true",
                        help="you join by hand in the live view (http://localhost:6080/control.html) while the bot "
                             "records exactly what you do (needs a writable /mouse)")
    parser.add_argument("--replay-session", action="store_true",
                        help="replay your recorded join exactly, keystrokes included; the bot adds nothing")
    parser.add_argument("--replay-vnc", action="store_true",
                        help="replay it through the live view's own path (VNC over websockify into x11vnc), "
                             "like noVNC sends your input")
    parser.add_argument("--input", choices=("vnc", "xtest"), default=env("INPUT", "vnc"),
                        help="how the bot's clicks reach the display: vnc = through x11vnc, like a person's "
                             "live-view input; xtest = directly (blocked by Meet in testing)")
    parser.add_argument("--type-name", action="store_true", default=env("TYPE_NAME") == "1",
                        help="click into the name field and type the name key by key through VNC, in the rhythm of "
                             "your recorded live join, instead of filling it in (needs --input vnc)")
    args = parser.parse_args()
    if not args.url or urlparse(args.url).hostname != "meet.google.com" or not urlparse(args.url).path.strip("/"):
        parser.error("a meeting link like https://meet.google.com/abc-defg-hij is required")
    if args.replay_session:
        args.input = "xtest"
    if args.replay_vnc:
        args.input = "vnc"
    if args.input == "vnc" and env("ENABLE_VNC", "1") != "1":
        parser.error("--input vnc sends the bot's clicks through the live view's VNC server: don't set ENABLE_VNC=0")
    if args.type_name and args.input != "vnc":
        parser.error("--type-name types through the VNC server: use it with --input vnc (the default)")
    args.vnc_client = None
    code = re.search(r"\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b", urlparse(args.url).path)
    if code:
        args.url = f"https://meet.google.com/{code.group(0)}"
    return args


def main() -> int:
    global mouse
    args = parse_args()
    meeting = urlparse(args.url).path.strip("/").replace("/", "_") or "meet"
    rep = Reporter(args.output / f"{datetime.now():%Y%m%d-%H%M%S}-{meeting}")
    options = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items() if k != "vnc_client"}
    (rep.run_dir / "options.json").write_text(json.dumps(options, indent=1))
    mouse = human_mouse.HumanMouse(args.mouse)
    rep.log(f"mouse: replaying your {len(mouse.recordings)} recorded clicks" if mouse.recordings
            else f"mouse: nothing recorded at {args.mouse}, using synthetic human-like movement")
    if mouse.sequences:
        rep.log(f"mouse: {len(mouse.sequences)} recorded Meet sequences; one is replayed exactly if the layout matches")
    if mouse.outdated_sequences:
        rep.log(f"mouse: {mouse.outdated_sequences} older Meet sequences ignored (their clicks would miss the buttons "
                f"of the latest layout capture)")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, request_stop)

    admitted = False
    with sync_playwright() as p:
        browser = launch(p)
        context = browser.new_context(no_viewport=True, locale="en-US", permissions=["camera", "microphone"])
        if args.trace:
            context.tracing.start(screenshots=True, snapshots=True)
        page = context.new_page()
        mouse.calibrate(page)
        if args.input == "vnc":
            mouse.vnc = args.vnc_client = live_session.VncClient()
        rep.log("input: through the VNC server (x11vnc), the path a person's live-view clicks take"
                if mouse.vnc else "input: straight through XTEST")
        if args.replay_vnc or args.replay_session:
            mouse.move_to(*live_session.load(args.mouse.with_name(live_session.SESSION_FILE))["start_pointer"])
        elif mouse.sequence:
            ox, oy = mouse.origin
            mouse.move_to(ox + mouse.sequence["start"]["x"], oy + mouse.sequence["start"]["y"])
        rep.status("joining_call", message=f"{args.url} as {args.name!r}")
        try:
            outcome = join(page, args, rep)
            if outcome is None:
                admitted = True
                outcome = attend(page, args, rep)
        except Exception as exc:
            outcome = ("fatal", "bot_errored", f"{type(exc).__name__}: {exc}".splitlines()[0])
        rep.status(*outcome, page=page)
        if args.trace:
            context.tracing.stop(path=rep.run_dir / "trace.zip")
        rep.status("done", message=f"artifacts in {rep.run_dir}")
        if args.hold:
            rep.log("holding the browser open for the live view; Ctrl+C or docker stop to exit")
            try:
                while not stop_requested:
                    page.wait_for_timeout(1_000)
            except PlaywrightError:
                pass
        try:
            browser.close()
        except PlaywrightError:
            pass
    return 0 if admitted or outcome[1] == "dry_run" else 1


if __name__ == "__main__":
    sys.exit(main())
