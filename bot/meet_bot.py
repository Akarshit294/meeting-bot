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
from botconfig import BotConfig, ManagerLink, needs_secure_flag, origin, redact, safe_url
from control import ControlServer
from reporting import Reporter

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

STOPPED: Outcome = ("call_ended", "bot_received_leave_call", "leave requested (manager, SIGINT or SIGTERM)")
stop_requested = False
mouse: human_mouse.HumanMouse
# Called before every click in Meet (service mode: keeps our own page windows under Meet's window).
before_click = None


def request_stop(_signum, _frame) -> None:
    global stop_requested
    stop_requested = True


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
    if before_click:
        before_click()
    mouse.click_element(page, target)
    if before_click:
        nudge()  # in the call: make sure the release has reached the page


def nudge() -> None:
    """A 1-pixel move. x11vnc sometimes holds a click's release until the next pointer event (seen in the
    fake-meeting tests: a press on the name field was released 8 s later, on the next movement); a
    tiny move delivers it."""
    time.sleep(0.15)
    try:
        x, y = mouse.position()
        mouse.move_to(x + 1, y)
    except Exception:  # noqa: BLE001 - never let this break a click
        pass


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


def prejoin_clicks(page: Page, rep: Reporter, layout: dict, join_button: Locator, knock: bool,
                   retry_knock: bool = False) -> None:
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
        for attempt in (1, 2) if retry_knock else (1,):
            human_click(page, join_button)
            time.sleep(QUIET_AFTER_KNOCK)
            try:
                join_button.wait_for(state="hidden", timeout=10_000)
                return
            except PlaywrightError:
                if attempt == 1 and retry_knock:
                    # Service mode only (--join-only keeps today's single click). First a 1-pixel move: it
                    # delivers a release x11vnc may still hold; click again only if that wasn't it.
                    rep.log("the click on Ask to join didn't register; moving the mouse a pixel")
                    nudge()
                    try:
                        join_button.wait_for(state="hidden", timeout=5_000)
                        rep.log("it registered after the move")
                        return
                    except PlaywrightError:
                        rep.log("still not registered; clicking Ask to join again")
        raise RuntimeError("clicked Ask to join but the click didn't register. Is another live-view tab "
                           "(an older, interactive one) sending input?")


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
    page.goto(args.meet_url or args.url, wait_until="domcontentloaded", timeout=60_000)

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
        prejoin_clicks(page, rep, layout, join_button, knock=not args.dry_run, retry_knock=not args.join_only)
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


def attend_join_only(page: Page, args: argparse.Namespace, rep: Reporter) -> Outcome:
    """Today's in-call behaviour, unchanged (--join-only): devices off, watch until the call ends."""
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


# ---- service mode: everything after admission (docs/SPEC.md 5.3) ----

MIC_ON = re.compile(r"^turn on microphone", re.I)
MIC_OFF = re.compile(r"^turn off microphone", re.I)
SHARE_BUTTON = re.compile(r"^(share screen|present now|present)\b", re.I)
SHARE_MENU_WINDOW = re.compile(r"\b(a )?window\b", re.I)
STOP_SHARE = re.compile(r"^stop (presenting|sharing|screen ?shar)", re.I)
PRESENTING_TEXT = re.compile(r"you('re| are) presenting", re.I)
SHARE_CONFIRM = 10
TICK_MS = 200
PROBE_EVERY = 1.0
MAX_TICK_FAILURES = 40


def turn_on_mic(page: Page, rep: Reporter) -> bool:
    off = page.get_by_role("button", name=MIC_OFF).first
    on = page.get_by_role("button", name=MIC_ON).first
    for _ in range(12):
        if off.is_visible():
            return True
        if on.is_visible():
            try:
                human_click(page, on)
            except PlaywrightError:
                pass
        page.wait_for_timeout(500)
    return off.is_visible()


def is_presenting(page: Page) -> bool:
    try:
        if page.get_by_role("button", name=STOP_SHARE).first.is_visible():
            return True
        return bool(page.get_by_text(PRESENTING_TEXT).first.is_visible())
    except PlaywrightError:
        return False


def probe_in_call(page: Page) -> dict:
    """Like probe(), with short timeouts: in the call nothing may hang for 30 s."""
    try:
        text = page.locator("body").inner_text(timeout=3_000)
    except PlaywrightError:
        text = ""
    leave_buttons = page.locator('[aria-label^="Leave call" i]')
    return {
        "text": text.replace("‘", "'").replace("’", "'").lower(),
        # In the page at all (a control bar that hides itself must not look like the end of the call)...
        "inCall": leave_buttons.count() > 0,
        # ...and on screen (a removal text with a usable Leave button is only chat).
        "leaveShown": leave_buttons.filter(visible=True).count() > 0,
        "participants": page.locator("[data-participant-id]").count(),
    }


def safe_leave(page: Page) -> None:
    try:
        leave(page)
    except Exception:  # noqa: BLE001 - leaving must never crash the bot before it reports
        pass


class InCall:
    """The service side of a call: the camera page, sound, people, screen share."""

    def __init__(self, p: Playwright, page: Page, args: argparse.Namespace, rep: Reporter, cfg: BotConfig,
                 control: ControlServer | None):
        self.p, self.page, self.args, self.rep, self.cfg, self.control = p, page, args, rep, cfg, control
        self.pages = None
        self.audio = None
        self.people = None
        self.webhooks = None
        self.pictures = None
        self.speakers = None
        self.sharing_url: str | None = None
        self.share_pending: dict | None = None
        self.share_lost_logged = False
        self.camera_reopens = 0

    def _page_browser(self, first_url: str):
        import pages

        global before_click
        if self.pages is None:
            self.pages = pages.PageBrowser(self.p, self.rep.log, insecure=[self.cfg.camera_url, first_url],
                                           share_title=self.args.share_title)
            before_click = self.pages.before_click
        return self.pages

    # -- start (docs/SPEC.md 5.3, steps 2 to 6) --

    def start(self) -> Outcome | None:
        import participants
        from webhooks import WebhookSender

        page, rep, cfg = self.page, self.rep, self.cfg
        self.webhooks = WebhookSender(rep.log)
        self.people = participants.People(page, cfg, rep, self.webhooks, open_panel=self._click)
        self.people.tick(initial=True)
        if stop_requested:
            return STOPPED

        audio_endpoint = cfg.endpoint_for("audio_separate_raw.data", "websocket")
        if audio_endpoint:
            import audio

            self.audio = audio.start_for(self.args, cfg, audio_endpoint.url, rep, self.people)
        else:
            rep.log("no audio_separate_raw.data endpoint: the call's sound is not streamed")

        if cfg.camera_url:
            self._page_browser(cfg.camera_url)
            # Microphone first: the camera page may start speaking as soon as it loads (until it plays,
            # the microphone carries silence).
            if turn_on_mic(page, rep):
                rep.log("microphone ON in Meet: the camera page will be the bot's voice")
            else:
                rep.log("warning: could not confirm the microphone is on")
            if stop_requested:
                return STOPPED
            if not self.pages.open_camera(cfg.camera_url, should_stop=lambda: stop_requested):
                return STOPPED if stop_requested else ("fatal", "bot_errored", "the camera page could not be opened")
            if not turn_off(page, "camera"):
                rep.log("warning: could not confirm the camera is off")
        else:
            rep.log("no camera page: the bot only joins and listens")
            turn_off_devices(page, rep)
        if stop_requested:
            return STOPPED

        self._start_extras()
        rep.status("in_call_recording", page=page)
        return None

    def _start_extras(self) -> None:
        """Task 11 and 12: camera pictures and the speaker timeline. Failures here never touch the call."""
        try:
            import extras

            self.pictures, self.speakers = extras.start(self)
        except Exception as exc:  # noqa: BLE001 - optional features must never end a call
            self.rep.log(f"extras not started: {type(exc).__name__}: {exc}")

    def _click(self, target: Locator) -> None:
        human_click(self.page, target)

    # -- every tick (any exception here is caught by the loop in attend) --

    def tick(self) -> Outcome | None:
        if self.control:
            while (command := self.control.next_command()) is not None:
                self.handle(command)
        if self.people:
            self.people.tick()
        if self.pages:
            self.pages.tick()
            if self.pages.camera_lost:
                outcome = self._reopen_camera()
                if outcome:
                    return outcome
        if self.share_pending:
            self._confirm_share()
        elif self.sharing_url:
            self._watch_share()
        if self.audio is not None and hasattr(self.audio, "tick"):  # --per-person-audio
            try:
                self.audio.tick()
            except Exception as exc:  # noqa: BLE001 - experimental: never end the call for it
                self.rep.log(f"per-person audio tick failed: {type(exc).__name__}: {exc}")
        for extra in (self.pictures, self.speakers):
            if extra:
                try:
                    extra.tick()
                except Exception as exc:  # noqa: BLE001
                    self.rep.log(f"{type(extra).__name__} failed and is turned off: {type(exc).__name__}: {exc}")
                    if extra is self.pictures:
                        self.pictures = None
                    else:
                        self.speakers = None
        return None

    def _reopen_camera(self) -> Outcome | None:
        """The camera page crashed or closed: without it the agent is mute and deaf."""
        self.camera_reopens += 1
        self.rep.log(f"the camera page is gone ({self.pages.camera_lost}); reopening it (try {self.camera_reopens})")
        if self.camera_reopens <= 3 and self.pages.open_camera(self.cfg.camera_url, attempts=2,
                                                                should_stop=lambda: stop_requested):
            return None
        return ("fatal", "bot_errored", "the camera page crashed and could not be reopened")

    def others(self) -> int | None:
        return self.people.count_others() if self.people else None

    # -- screen share (docs/SPEC.md 5.5) --

    def handle(self, command) -> None:
        try:
            if command.action == "screenshare":
                ok, detail = self.start_share(command.body["url"])
                command.reply(200 if ok else 500, {"ok": ok, "detail": detail})
            elif command.action == "screenshare_stop":
                self.stop_share()
                command.reply(200, {"ok": True})
            else:
                command.reply(404, {"detail": "unknown action"})
        except Exception as exc:  # noqa: BLE001 - a failed share must not end the call
            self.rep.log(redact(f"{command.action} failed: {type(exc).__name__}: {exc}"))
            command.reply(500, {"detail": redact(f"{type(exc).__name__}: {exc}")[:300]})

    def _click_share(self) -> bool:
        page = self.page
        button = page.get_by_role("button", name=SHARE_BUTTON).first
        try:
            button.wait_for(state="visible", timeout=5_000)
            human_click(page, button)
        except PlaywrightError as exc:
            self.rep.log(f"no Share screen button ({type(exc).__name__})")
            return False
        page.wait_for_timeout(800)
        menu = page.get_by_role("menuitem", name=SHARE_MENU_WINDOW).first
        try:
            if menu.is_visible():
                human_click(page, menu)
        except PlaywrightError:
            pass
        return True

    def start_share(self, url: str) -> tuple[bool, str]:
        """Answer as soon as the click is made (docs/SPEC.md 2.4); the bot confirms it in its log."""
        rep, page = self.rep, self.page
        pages = self._page_browser(url)
        replacing = self.sharing_url is not None and (self.share_pending is not None or is_presenting(page))
        if not pages.open_screen(url):
            return False, "the screen page could not be opened"
        rep.event("screenshare", action="replace" if replacing else "start", url=safe_url(url))
        self.sharing_url = url
        if replacing:
            rep.log(f"screen share now shows {safe_url(url)}")
            rep.snapshot(page, f"share-replaced-{int(time.time())}")
            return True, "replaced the page"
        if not self._click_share():
            pages.close_screen()
            self.sharing_url = None
            return False, "no Share screen button in Meet"
        self.share_pending = {"since": time.monotonic(), "attempt": 1}
        self.share_lost_logged = False
        return True, "clicked Share screen"

    def _confirm_share(self) -> None:
        page, rep, pending = self.page, self.rep, self.share_pending
        self.pages.keep_below(force=True)  # Chrome raises the window it starts capturing
        if is_presenting(page):
            self.share_pending = None
            rep.log(f"screen share started: Meet shows the bot presenting ({safe_url(self.sharing_url)})")
            rep.snapshot(page, f"share-started-{int(time.time())}")
            page.wait_for_timeout(1_000)
            self.pages.keep_below(force=True)
            self.grab_screen("share-started")
            return
        if time.monotonic() - pending["since"] < SHARE_CONFIRM:
            return
        rep.log(f"share attempt {pending['attempt']}: Meet does not show the bot presenting after {SHARE_CONFIRM}s")
        rep.snapshot(page, f"share-not-confirmed-{pending['attempt']}-{int(time.time())}")
        if pending["attempt"] == 1 and self._click_share():
            self.share_pending = {"since": time.monotonic(), "attempt": 2}
            return
        rep.log("the screen share did not start")
        rep.event("screenshare", action="failed")
        self.share_pending = None
        self.sharing_url = None
        self.pages.close_screen()

    def stop_share(self) -> None:
        page, rep = self.page, self.rep
        self.share_pending = None
        if is_presenting(page):
            try:
                human_click(page, page.get_by_role("button", name=STOP_SHARE).first)
            except PlaywrightError:
                pass
            for _ in range(10):
                if not is_presenting(page):
                    break
                page.wait_for_timeout(500)
            rep.log("screen share stopped" if not is_presenting(page) else "warning: Meet still shows the bot presenting")
            rep.snapshot(page, f"share-stopped-{int(time.time())}")
            self.grab_screen("share-stopped")
        if self.pages:
            self.pages.close_screen()
        if self.sharing_url:
            rep.event("screenshare", action="stop")
        self.sharing_url = None

    def grab_screen(self, name: str) -> None:
        """The whole display (what the live view shows), and its windows: shows whether anything of
        Chrome's (like a "sharing" bar) covers Meet's buttons."""
        try:
            import xwin

            (self.rep.run_dir / f"screen-{name}-{int(time.time())}.png").write_bytes(xwin.grab_screen())
            self.rep.event("windows", at=name, windows=xwin.describe())
        except Exception as exc:  # noqa: BLE001
            self.rep.log(f"screen grab failed: {type(exc).__name__}: {exc}")

    def _watch_share(self) -> None:
        """Only logs: a People panel or a new layout can hide Meet's "presenting" signs while the share
        still runs, so the bot never stops a share on its own."""
        if is_presenting(self.page):
            self.share_lost_logged = False
        elif not self.share_lost_logged:
            self.share_lost_logged = True
            self.rep.log("Meet no longer shows the bot presenting (the share may have been stopped in Meet)")
            self.rep.event("screenshare", action="not_shown_in_meet")

    # -- the end (docs/SPEC.md 4.3 and 5.6): called AFTER call_ended is reported --

    def close(self) -> None:
        for name, fn in (("pages", lambda: self.pages and self.pages.close()),
                         ("audio", lambda: self.audio and self.audio.stop()),
                         ("pictures", lambda: self.pictures and self.pictures.stop()),
                         ("speakers", lambda: self.speakers and self.speakers.stop()),
                         ("people", lambda: self.people and self.people.close()),
                         ("webhooks", lambda: self.webhooks and self.webhooks.close())):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self.rep.log(f"closing {name}: {type(exc).__name__}: {exc}")


def attend(p: Playwright, page: Page, args: argparse.Namespace, rep: Reporter, cfg: BotConfig,
           control: ControlServer | None) -> tuple[Outcome, InCall]:
    rep.status("in_call_not_recording", page=page)
    call = InCall(p, page, args, rep, cfg, control)
    if control:
        control.in_call.set()  # requests now wait for the start steps; one that waits too long is dropped
    outcome = call.start()
    if outcome:
        safe_leave(page)
        return outcome, call

    started = time.monotonic()
    seen_others = False
    alone_since = None
    controls_missing = 0
    last_count = None
    last_probe = 0.0
    failures = 0
    removed_text_logged = False
    while True:
        if stop_requested:
            safe_leave(page)
            return STOPPED, call
        if args.max_duration and time.monotonic() - started > args.max_duration:
            safe_leave(page)
            return ("call_ended", "timeout_exceeded_in_call_recording", f"max duration of {args.max_duration}s reached"), call
        try:
            if time.monotonic() - last_probe >= PROBE_EVERY:
                last_probe = time.monotonic()
                rep.flush()
                state = probe_in_call(page)
                if REMOVED_FROM_CALL.search(state["text"]):
                    if not state["leaveShown"]:
                        return ("call_ended", "bot_kicked_from_call", excerpt(state["text"])), call
                    if not removed_text_logged:  # e.g. someone typed it in the chat
                        removed_text_logged = True
                        rep.log("the page mentions a removal, but Meet's call controls are still there: staying")
                if state["inCall"]:
                    controls_missing = 0
                else:
                    controls_missing += 1
                    if controls_missing >= 3:
                        sub_code = "call_ended_by_host" if CALL_ENDED.search(state["text"]) else None
                        return ("call_ended", sub_code, excerpt(state["text"])), call
                others = call.others()
                count = state["participants"] if others is None else others + 1
                if count != last_count:
                    rep.log(f"people in the call (with the bot): {count}")
                    last_count = count
                if count > 1:
                    seen_others, alone_since = True, None
                elif count == 1 and seen_others and args.everyone_left_timeout:
                    if alone_since is None:
                        alone_since = time.monotonic()
                    if time.monotonic() - alone_since >= args.everyone_left_timeout:
                        safe_leave(page)
                        return ("call_ended", "timeout_exceeded_everyone_left", f"alone for {args.everyone_left_timeout}s"), call
                dismiss_popups(page)
            end = call.tick()
            if end:
                safe_leave(page)
                return end, call
            failures = 0
            page.wait_for_timeout(TICK_MS)
        except Exception as exc:  # noqa: BLE001 - one bad tick must not end a live call
            if page.is_closed():
                return ("fatal", "bot_errored", "Meet's page closed (the browser crashed?)"), call
            failures += 1
            if failures in (1, 5) or failures % 20 == 0:
                rep.log(redact(f"in-call check failed ({failures} in a row): {type(exc).__name__}: {str(exc)[:200]}"))
            if failures >= MAX_TICK_FAILURES:
                safe_leave(page)
                return ("fatal", "bot_errored", redact(f"in-call checks kept failing: {type(exc).__name__}"[:200])), call
            time.sleep(0.5)


def silent_wav() -> str:
    path = Path(tempfile.gettempdir()) / "silence.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48_000)
        f.writeframes(b"\0\0" * 48_000)
    return str(path)


ANTI_THROTTLE = ["--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
                 "--disable-renderer-backgrounding"]


def meet_flags(args: argparse.Namespace | None = None) -> list[str]:
    """Browser A's flags. --join-only (and the mouse demo): exactly today's. Service mode: no fake devices
    (sound goes through PulseAudio; no camera device, so the camera is simply off), no fake UI (it would
    skip the screen picker, so the auto-select flag could not pick our window), our window picked by title
    for "Share screen", and no background throttling."""
    width, height = os.environ.get("SCREEN_SIZE", "1280x720").split("x")
    common = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--lang=en-US",
        "--window-position=0,0",
        f"--window-size={width},{height}",
    ]
    if args is None or args.join_only:
        flags = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                 f"--use-file-for-fake-audio-capture={silent_wav()}", *common]
    else:
        flags = [*common, f"--auto-select-desktop-capture-source={args.share_title}", *ANTI_THROTTLE]
    if args is not None and args.meet_url and needs_secure_flag(args.meet_url):
        # TEST ONLY (the fake meeting is served over plain http on the test network; never with real Meet).
        flags.append(f"--unsafely-treat-insecure-origin-as-secure={origin(args.meet_url)}")
    return flags


def launch(p: Playwright, args: argparse.Namespace | None = None):
    env = None
    if args is not None and not args.join_only:
        env = {**os.environ, "PULSE_SINK": "meet_out", "PULSE_SOURCE": "agent_mic"}
    return p.chromium.launch(
        channel="chrome" if shutil.which("google-chrome") else None,
        headless=False,
        args=meet_flags(args),
        env=env,
        ignore_default_args=["--enable-automation"],
        handle_sigint=False,
        handle_sigterm=False,
    )


def parse_args(argv: list[str] | None = None, environ: dict | None = None) -> argparse.Namespace:
    env = (environ if environ is not None else os.environ).get
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
    # ---- service mode ----
    parser.add_argument("--join-only", action="store_true", default=env("JOIN_ONLY") == "1",
                        help="behave exactly like the proven join test: fake devices, no pages, no sound, no webhooks")
    parser.add_argument("--camera-url", default=env("CAMERA_URL"),
                        help="the camera page: its sound is the bot's voice, and it hears the call")
    parser.add_argument("--audio-url", default=env("AUDIO_WS_URL"),
                        help="websocket for the call's sound (audio_separate_raw.data), without the manager")
    parser.add_argument("--events-url", default=env("EVENTS_URL"),
                        help="webhook for participant join/leave, without the manager")
    parser.add_argument("--meet-url", default=env("MEET_URL_OVERRIDE"),
                        help="TEST ONLY: open this page instead of the Meet link (the fake meeting)")
    parser.add_argument("--control-port", type=int, default=int(env("CONTROL_PORT", "8080")),
                        help="port of the control server (leave, screen share); 0 = none")
    parser.add_argument("--share-title", default=env("SHARE_TITLE", "xbot screen share"),
                        help="the title our screen-share window carries; Chrome picks the window by it")
    parser.add_argument("--no-camera-pictures", action="store_true", default=env("CAMERA_PICTURES") == "0",
                        help="don't send each person's camera picture (video_separate_png.data)")
    parser.add_argument("--per-person-audio", action="store_true", default=env("PER_PERSON_AUDIO") == "1",
                        help="EXPERIMENTAL: one audio stream per person instead of one for the whole call")
    args = parser.parse_args(argv)
    args.manager = None
    args.config = None
    if not args.url:
        link = ManagerLink.from_env() if environ is None else None
        if link:
            args.manager = link
            args.config = link.fetch_config()
            args.url = args.config.meeting_url
            args.name = args.config.bot_name
            args.meet_url = args.meet_url or args.config.meet_url_override
            options = args.config.options
            if options.get("per_person_audio"):
                args.per_person_audio = True
            if options.get("camera_pictures") is False:
                args.no_camera_pictures = True
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
    if args.config is None:
        endpoints = []
        if args.audio_url:
            endpoints.append({"type": "websocket", "url": args.audio_url, "events": ["audio_separate_raw.data"]})
        if args.events_url:
            endpoints.append({"type": "webhook", "url": args.events_url,
                              "events": ["participant_events.join", "participant_events.leave"]})
        args.config = BotConfig.from_dict({"meeting_url": args.url, "bot_name": args.name, "camera_url": args.camera_url,
                                           "endpoints": endpoints, "bot_id": env("BOT_ID") or "xbot_local"})
    elif args.camera_url:
        args.config.camera_url = args.camera_url
    return args


def run_folder(args: argparse.Namespace) -> Path:
    meeting = urlparse(args.url).path.strip("/").replace("/", "_") or "meet"
    return args.output / f"{datetime.now():%Y%m%d-%H%M%S}-{meeting}"


def options_for_file(args: argparse.Namespace) -> dict:
    """options.json, with no tokens: xebit URLs are cut to host and path."""
    skip = {"vnc_client", "manager", "config"}
    out = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items() if k not in skip}
    for key in ("camera_url", "audio_url", "events_url"):
        if out.get(key):
            out[key] = safe_url(out[key])
    cfg = args.config
    out["service"] = {"bot_id": cfg.bot_id, "camera_page": safe_url(cfg.camera_url) if cfg.camera_url else None,
                      "endpoints": [{"type": e.type, "url": safe_url(e.url), "events": e.events} for e in cfg.endpoints],
                      "manager": bool(args.manager)}
    return out


def main() -> int:
    global mouse, before_click
    args = parse_args()

    def send(body: dict) -> bool:
        """Report to the manager. Its answer also says whether a leave was asked for (a leave that came
        while the bot's control server was not up yet is not lost)."""
        global stop_requested
        reply = args.manager.report(body)
        if reply and reply.get("leave") and not stop_requested:
            print("leave requested (the manager's answer)", flush=True)
            stop_requested = True
        return reply is not None

    rep = Reporter(run_folder(args), send=send if args.manager else None, tee=True)
    (rep.run_dir / "options.json").write_text(json.dumps(options_for_file(args), indent=1))
    if args.manager:
        send({"run_dir": rep.run_dir.name})
    if args.meet_url:
        rep.log(f"TEST MODE: opening {args.meet_url} instead of the Meet link")
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

    def leave_requested() -> None:
        global stop_requested
        rep.log("leave requested by the manager")
        stop_requested = True

    control = None
    if args.control_port:
        token = args.manager.token if args.manager else None
        control = ControlServer(args.control_port, token, leave_requested).start()
        rep.log(f"control server on port {control.port}")

    admitted = False
    call = None
    with sync_playwright() as p:
        browser = launch(p, args)
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
        if control:
            control.status = "joining_call"
        try:
            outcome = join(page, args, rep)
            if outcome is None:
                admitted = True
                if args.join_only:
                    outcome = attend_join_only(page, args, rep)
                else:
                    outcome, call = attend(p, page, args, rep, args.config, control)
        except Exception as exc:
            outcome = ("fatal", "bot_errored", redact(f"{type(exc).__name__}: {exc}".splitlines()[0]))
            if admitted:
                safe_leave(page)
        if control:
            control.in_call.clear()
            control.fail_pending()
            control.status = outcome[0]
        # call_ended FIRST, then close the camera page (docs/SPEC.md 4.3): wait until the manager has it.
        rep.status(*outcome, page=page)
        if not rep.flush(wait=20):
            rep.log("warning: the manager has not confirmed the end status; closing the pages anyway")
        if call:
            call.close()
        before_click = None
        if args.trace:
            context.tracing.stop(path=rep.run_dir / "trace.zip")
        try:
            (rep.run_dir / "final-page.html").write_text(page.content())
        except PlaywrightError:
            pass
        rep.status("done", message=f"artifacts in {rep.run_dir}")
        rep.flush(wait=10)
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
    if control:
        control.stop()
    return 0 if admitted or outcome[1] == "dry_run" else 1


if __name__ == "__main__":
    sys.exit(main())
