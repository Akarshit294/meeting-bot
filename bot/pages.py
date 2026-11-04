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
import time
from typing import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Playwright

import xwin
from botconfig import needs_secure_flag, origin, safe_url

PAGE_CLASS = "xbot-pages"
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
        self.browser = p.chromium.launch(headless=False, args=flags, env=env)
        self.log("page browser started (camera page plays into agent_out, hears meet_out.monitor)")

    def _new_window(self):
        context = self.browser.new_context(no_viewport=True, permissions=["microphone"], locale="en-US")
        page = context.new_page()
        self.keep_below(force=True)
        return page

    def open_camera(self, url: str, attempts: int = 3) -> bool:
        for attempt in range(1, attempts + 1):
            try:
                self.camera = self._new_window()
                self.camera.on("console", lambda m: m.type == "error" and self.log(f"camera page console: {m.text[:200]}"))
                self.camera.goto(url, wait_until="domcontentloaded", timeout=30_000)
                self.keep_below(force=True)
                self.log(f"camera page open: {safe_url(url)}")
                return True
            except PlaywrightError as exc:
                self.log(f"camera page {safe_url(url)} did not open (try {attempt}): {str(exc).splitlines()[0]}")
                self._close_page(self.camera)
                self.camera = None
                time.sleep(2 * attempt)
        return False

    def open_screen(self, url: str) -> bool:
        """Open (or re-point) the screen-share window. The window keeps the same title, so a running
        share keeps showing it."""
        try:
            if self.screen is None:
                self.screen = self._new_window()
                cdp = self.screen.context.new_cdp_session(self.screen)
                window = cdp.send("Browser.getWindowForTarget")["windowId"]
                cdp.send("Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": "fullscreen"}})
            self.screen.goto(url, wait_until="domcontentloaded", timeout=30_000)
            self.screen.evaluate(KEEP_TITLE_JS, self.share_title)
            self.screen.wait_for_timeout(500)
            found = xwin.mark_shareable(self.share_title)
            self.keep_below(force=True)
            self.log(f"screen page open: {safe_url(url)} (window found by title: {found})")
            return found
        except PlaywrightError as exc:
            self.log(f"screen page {safe_url(url)} did not open: {str(exc).splitlines()[0]}")
            self.close_screen()
            return False

    def close_screen(self) -> None:
        if self.screen is not None:
            self._close_page(self.screen)
            self.screen = None
            self.log("screen page closed")

    def keep_below(self, force: bool = False) -> None:
        """Put every window of this browser under Meet's window (at most every 2 s unless forced)."""
        now = time.monotonic()
        if not force and now - self._last_keep < 2:
            return
        self._last_keep = now
        try:
            xwin.lower_class(PAGE_CLASS)
        except Exception as exc:  # noqa: BLE001 - X trouble must not break a click
            self.log(f"could not lower our windows: {exc}")

    def tick(self) -> None:
        self.keep_below()

    def close(self) -> None:
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
