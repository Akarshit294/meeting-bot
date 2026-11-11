"""The bot's browser setup, on the Mac instead of in Docker: Playwright starts the Mac's Google Chrome with
the bot's service-mode switches and the bot's private window (camera + microphone pre-allowed). Opens a blank
page; a person types the Meet link and joins by hand. Ends when the last tab is closed (or after 30 minutes).

It tells "the container" apart from "Playwright's setup": on 2026-11-11 this got in on the same Wi-Fi and
meeting that refused the container (docs/why-meet-refused-the-bot.md). Needs a Python with Playwright
(`pip install playwright`; no browser download: it uses the installed Google Chrome). Keep FLAGS in step with
meet_flags() in bot/meet_bot.py.

    python3 scripts/mac_bot_browser.py"""
import time

from playwright.sync_api import sync_playwright

FLAGS = [  # bot/meet_bot.py meet_flags(), service mode
    "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--lang=en-US",
    "--window-position=0,0", "--window-size=1280,720",
    "--auto-select-desktop-capture-source=xbot screen share",
    "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=False, args=FLAGS,
                                ignore_default_args=["--enable-automation"])
    context = browser.new_context(no_viewport=True, locale="en-US", permissions=["camera", "microphone"])
    page = context.new_page()
    page.goto("about:blank")
    print(f"ready: Google Chrome {browser.version}", flush=True)
    deadline = time.time() + 30 * 60
    while browser.is_connected() and time.time() < deadline:
        pages = [pg for pg in context.pages if not pg.is_closed()]
        if not pages:
            break
        try:
            pages[0].wait_for_timeout(1000)
        except Exception:  # noqa: BLE001 - the tab closed while waiting
            pass
    print("closed", flush=True)
    browser.close()
