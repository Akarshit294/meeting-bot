"""Browser B: our own pages (docs/SPEC.md 4 and 5.2).

- The camera page plays the agent's voice into `agent_out` (Meet's microphone is a copy of it) and hears
  the call through `meet_out.monitor`.
- The screen page is shown in the call as the bot's screen share: its own window, fullscreen (so no
  address bar or token shows), with a fixed title that Meet's browser picks by
  `--auto-select-desktop-capture-source`.

Not Google's site, so nothing here needs care about looking automated. All windows of this browser are
kept UNDER Meet's window: clicks go to whatever window is on top, and Chrome still captures a covered
window (X Composite).
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Playwright

import xwin
from botconfig import needs_secure_flag, origin, redact, safe_url

PAGE_CLASS = "xbot-pages"
# Chrome's own bar while sharing ("<site> is sharing a window. Stop sharing / Hide"): it sits at the
# bottom centre of the screen, on top, right over Meet's control bar.
SHARING_BAR = (" is sharing a window", " is sharing your screen", " is sharing a tab", " is sharing this tab")
KEEP_EVERY = 0.5  # the keeper thread lowers our windows this often
KEEP_TITLE_JS = """title => {
    const pin = () => { if (document.title !== title) document.title = title; };
    pin();
    if (!window.__xbotTitle) {
        window.__xbotTitle = new MutationObserver(pin);
        window.__xbotTitle.observe(document, {subtree: true, childList: true, characterData: true});
    }
}"""


class PageBrowser:
    def __init__(self, p: Playwright, log: Callable[[str], None], insecure: list[str | None] = (),
                 share_title: str = "xbot screen share"):
        self.log = log
        self.share_title = share_title
        self.camera = None
        self.screen = None
        self._last_keep = 0.0
        flags = [
            "--autoplay-policy=no-user-gesture-required",
            "--use-fake-ui-for-media-stream",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-dev-shm-usage",
            f"--class={PAGE_CLASS}",
            "--window-position=0,0",
            "--window-size=" + os.environ.get("SCREEN_SIZE", "1280x720").replace("x", ","),
        ]
        origins = sorted({origin(u) for u in insecure if u and needs_secure_flag(u)} - {None})
        if origins:
            # xebit's pages may be plain http (e.g. http://host.docker.internal:8000 on the Mac): the
            # camera page needs getUserMedia, which needs a secure origin.
            flags.append("--unsafely-treat-insecure-origin-as-secure=" + ",".join(origins))
        self.insecure = origins
        env = {**os.environ, "PULSE_SINK": "agent_out", "PULSE_SOURCE": "meet_out.monitor"}
        # Google Chrome, like Recall's bot (it has the H.264/AAC codecs a page may record with); Playwright's
        # Chromium only where Chrome isn't available (arm64).
        channel = "chrome" if shutil.which("google-chrome") else None
        self.browser = p.chromium.launch(channel=channel, headless=False, args=flags, env=env)
        self.camera_lost: str | None = None
        self._closing = False
        self._stop_keeper = threading.Event()
        threading.Thread(target=self._keep_below_always, name="keep-below", daemon=True).start()
        self.log(f"page browser started ({channel or 'chromium'}; the camera page plays into agent_out and hears "
                 "meet_out.monitor)")

    def _new_window(self):
        context = self.browser.new_context(no_viewport=True, permissions=["microphone"], locale="en-US")
        page = context.new_page()
        self.keep_below(force=True)
        return page

    def open_camera(self, url: str, attempts: int = 3, should_stop: Callable[[], bool] = lambda: False) -> bool:
        if self.camera is not None:
            self._close_page(self.camera)
            self.camera = None
        for attempt in range(1, attempts + 1):
            if should_stop():
                return False
            try:
                page = self._new_window()
                self.camera = page
                page.on("console", lambda m: m.type == "error" and self.log(f"camera page console: {redact(m.text)[:200]}"))
                page.on("crash", lambda _p: self._lost("crashed"))
                page.on("close", lambda _p: None if self._closing or self.camera is not page else self._lost("closed"))
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                self.keep_below(force=True)
                self.camera_lost = None
                self.log(f"camera page open: {safe_url(url)}")
                return True
            except PlaywrightError as exc:
                self.log(f"camera page {safe_url(url)} did not open (try {attempt}): {redact(str(exc).splitlines()[0])}")
                self._closing = True
                self._close_page(self.camera)
                self._closing = False
                self.camera = None
                time.sleep(2 * attempt)
        return False

    def _lost(self, how: str) -> None:
        self.camera_lost = how
        self.log(f"the camera page {how}")

    def open_screen(self, url: str) -> bool:
        """Open (or re-point) the screen-share window. The window keeps the same title, so a running
        share keeps showing it."""
        try:
            if self.screen is None:
                self.screen = self._new_window()
                cdp = self.screen.context.new_cdp_session(self.screen)
                window = cdp.send("Browser.getWindowForTarget")["windowId"]
                cdp.send("Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": "fullscreen"}})
            self.screen.goto(url, wait_until="domcontentloaded", timeout=15_000)
            self.screen.evaluate(KEEP_TITLE_JS, self.share_title)
            self.screen.wait_for_timeout(500)
            found = xwin.mark_shareable(self.share_title)
            self.keep_below(force=True)
            self.log(f"screen page open: {safe_url(url)} (window found by title: {found})")
            return found
        except PlaywrightError as exc:
            self.log(f"screen page {safe_url(url)} did not open: {redact(str(exc).splitlines()[0])}")
            self.close_screen()
            return False

    def close_screen(self) -> None:
        if self.screen is not None:
            self._close_page(self.screen)
            self.screen = None
            self.log("screen page closed")

    def keep_below(self, force: bool = False) -> None:
        """Put every window of this browser, and Chrome's sharing bar, under Meet's window (at most every
        2 s unless forced). Needed again after a share starts: Chrome raises the captured window."""
        now = time.monotonic()
        if not force and now - self._last_keep < 2:
            return
        self._last_keep = now
        try:
            xwin.lower(PAGE_CLASS, SHARING_BAR)
        except Exception as exc:  # noqa: BLE001 - X trouble must not break a click
            self.log(f"could not lower our windows: {exc}")

    def _keep_below_always(self) -> None:
        """Our windows (and Chrome's sharing bar) under Meet's every KEEP_EVERY seconds, on a thread and X
        connection of their own, so it happens even while the bot's main loop is busy. In the second real test
        (2026-11-05) the main loop was busy for 17 s just after a share started, and the shared page covered
        Meet all that time (Chrome raises the window it starts capturing): the bot's clicks and camera
        pictures would have hit the shared page."""
        display = None
        while not self._stop_keeper.wait(KEEP_EVERY):
            try:
                display = display or xwin.connect()
                xwin.lower(PAGE_CLASS, SHARING_BAR, d=display)
            except Exception:  # noqa: BLE001 - a window that went away while we looked; try again next time
                display = None

    def before_click(self) -> None:
        self.keep_below(force=True)

    def tick(self) -> None:
        self.keep_below()

    def close(self) -> None:
        self._closing = True
        self._stop_keeper.set()
        self.close_screen()
        if self.camera is not None:
            self._close_page(self.camera)
            self.camera = None
            self.log("camera page closed")
        try:
            self.browser.close()
        except PlaywrightError:
            pass

    @staticmethod
    def _close_page(page) -> None:
        if page is None:
            return
        try:
            page.context.close()
        except PlaywrightError:
            pass
