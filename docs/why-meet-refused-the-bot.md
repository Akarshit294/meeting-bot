# Why Google Meet refused the bot (2026-11-11), and the fix

## In short

On 2026-11-11 Google Meet refused the bot almost every time: "You can't join this video call". The same
bot image had got in 9 times out of 10 on 2026-11-04 and 2026-11-05. The bot's code was not the cause, and
neither was Playwright, the meeting or the Wi-Fi. The cause was **what the bot's container looks like to
Google**: an out-of-date Chrome, the UTC time zone behind an Indian internet address, and only 5 fonts.
After we fixed those three things, the bot got in 3 times out of 3, on the same Wi-Fi and the same meeting.

## What Meet checks

Meet checks every guest (a person who joins without signing in) twice:

1. **When the meeting page opens.** It looks at what the browser *is*: Chrome version, operating system,
   time zone, fonts, graphics card, and more. If too much looks odd, the page goes straight to "You can't
   join this video call". The bot never sees the "What's your name?" screen. The saved page has the title
   `Meet` without the meeting code, and no reCAPTCHA script.
2. **When the guest clicks "Ask to join".** The pre-join screen says: "System info will be sent to confirm
   you're not a bot." A similar check runs again, now also with how the page was used. If it fails, the
   same "You can't join" page appears right after the knock. That saved page has the meeting code in its
   title and reCAPTCHA scripts.

Both checks give a score, not a yes or no. Each odd detail adds to it. Above some level Meet refuses, and
there is chance in it: the same browser can pass once and fail the next minute.

The bot reports both cases as `fatal [google_meet_bot_blocked]`. To tell them apart, look at the run
folder: a `prejoin.png` and an `in_waiting_room` status mean check 2; neither means check 1.

## What happened

All times are UTC. Run folders are in `output/` on the owner's Mac (not in git).

| When | Browser | Network | Tries | Got the name screen | Got into the waiting room |
|---|---|---|---|---|---|
| 2026-11-04 | bot container (proven join test) | Wi-Fi | 5 | 5 | 5 |
| 2026-11-05 | bot container, same image as 11-11 for the last run | Wi-Fi | 5 | 5 | 4 (one refused at check 2) |
| 2026-11-11 09:08-09:38 | bot container, old image | Wi-Fi and hotspot | 9 | 3 | 1 |
| 2026-11-11 | the owner's Chrome, incognito | Wi-Fi | 1 | 1 | 1 |
| 2026-11-11 | the bot's Playwright setup on the Mac, no Docker | Wi-Fi | 1 | 1 | 1 |
| 2026-11-11 09:56-10:01 | bot container, **fixed image** | Wi-Fi | 3 | 3 | 3 (admitted in seconds; one call with a second person) |

## How we found the cause

Each test removed one suspect:

| Test | Result | What it ruled out |
|---|---|---|
| The owner opened the same link in an incognito Chrome window | got in | The meeting's settings |
| The owner typed the link by hand into the bot's browser (live view, `--record-session --hold`) | refused | How the bot clicks and types |
| A plain Chrome in the same container, started without Playwright, driven by hand | refused | Playwright and the bot's Chrome switches |
| One try on a phone hotspot | got the name screen, refused at check 2 | Nothing: Wi-Fi tries also got the name screen sometimes. One try proves nothing |
| The bot's exact Playwright setup (same switches, same private window) on the Mac, without Docker (`scripts/mac_bot_browser.py`) | got in | **So the container is the problem** |
| The container with the newest Chrome, fonts and the Indian time zone | got in 3 of 3 | - |

## What the browser looked like

Measured with `scripts/browser_check.py` (inside the container, on a local page; it never opens Meet).

| | Container before | Container after | Mac (got in) |
|---|---|---|---|
| Chrome | 157.0.8103.38 (installed by the 2026-11-05 build) | 157.0.8103.50 | 157.0.8103.50 |
| Time zone the browser shows | `UTC` | `Asia/Calcutta` (India) | India |
| Fonts | 5 (Liberation only) | 328 installed | hundreds |
| Operating system | Linux | Linux | macOS |
| Graphics | SwiftShader (software, no graphics card) | same | a real graphics card |
| `navigator.webdriver` (the "I'm automated" flag) | `false` | `false` | `false` |
| Notifications on a first visit | `denied` | `denied` | `denied` |
| `Accept-Language` header | `en-US` | `en-US` | `en-US` |
| Speech voices | 0 | 0 | many |

The rows that are the same on the Mac (webdriver, notifications, language) cannot be the cause: the Mac
got in with them. The rows that differ are the suspects. We fixed three of them: Chrome, time zone, fonts.
Linux and the missing graphics card are still there and still count against the bot.

## The fix

| File | Change |
|---|---|
| `bot/Dockerfile` | `ARG CHROME_REFRESH`: changing its value makes the build install Google's newest stable Chrome (about 7 minutes). Without it, Docker's build cache keeps the first Chrome forever. |
| `bot/Dockerfile` | The fonts of a Linux desktop: `fonts-dejavu-core fonts-noto-core fonts-noto-mono fonts-noto-color-emoji`. |
| `bot/meet_bot.py` | `browser_env()`: Meet's Chrome runs with `TZ` from `BROWSER_TZ`, default `Asia/Kolkata`. Only the browser: the container's clock and all logs stay on UTC. The manager passes `BOT_ENV_BROWSER_TZ=<zone>` from its `.env` to the bot as `BROWSER_TZ`. |
| `tests/test_bot_orders.py` | `test_browser_time_zone`. |
| `scripts/browser_check.py`, `scripts/browser_check.sh` | Shows what the bot's browser shows a website. |
| `scripts/mac_bot_browser.py` | The bot's browser setup on the Mac, for a hand test without Docker. |
| `MORNING.md` | "If Meet refuses the bot" now starts with this. |

## Why the same image worked on 2026-11-05 and not on 2026-11-11

The image did not change. The world around it did. On 2026-11-05 the image had the newest Chrome. By
2026-11-11 Google had shipped a newer build (157.0.8103.50), real people's Chromes had updated themselves,
and the bot's had not. The container already looked odd on 2026-11-05 (Linux, no graphics card, UTC, 5
fonts), but its score stayed just under the line. An out-of-date Chrome on top pushed it over.

This is the best explanation, **not a proven one**: we changed three things at once, so we don't know which
one mattered most. Keep all three. Each makes the bot look more like a person's computer.

## Why the earlier join work didn't help

The recorded mouse movements, the recorded typing rhythm, the 15-second wait before knocking and the
clicks through VNC all make the bot *behave* like a person. That is check 2, and it is why the join worked
on 2026-11-04 and 2026-11-05. The 2026-11-11 refusals mostly came at check 1, before anything was clicked.
That is why even the owner's own typing failed. The behaviour work still matters; it covers the other check.

## Rules from now on

1. **Keep Chrome current.** Build the bot image with the newest Chrome at least every few weeks: change
   `CHROME_REFRESH` in `bot/Dockerfile`. When GitHub Actions builds the images, add a weekly build that does
   this.
2. **The browser's time zone matches the server's region.** Set `BROWSER_TZ` (India is right for Xebit's
   Mumbai server and the owner's Mac).
3. **Keep the fonts.**
4. **Don't retry fast.** Many refused tries in a row can only make Google more suspicious.
5. **One try proves nothing.** To compare two setups, compare rates over several tries, 10 minutes apart.
6. **When Meet refuses, check the browser before the join code.** Run `scripts/browser_check.py` and
   compare its Chrome version with the current stable Chrome (the Mac's `chrome://version` is fine).

## How to check the browser

Inside the bot image (no Meet, safe to run any time):

```bash
docker run --rm --shm-size 2g -v "$PWD/scripts:/scripts:ro" --entrypoint bash meeting-bot:latest \
  /scripts/browser_check.sh
```

The `summary` at the top should show: the current Chrome version, `time_zone` `Asia/Calcutta` (or the
`BROWSER_TZ` you set), and `fonts_installed` above 300.

On the Mac, without Docker, for a hand test on real Meet (needs a Python with Playwright):

```bash
python3 scripts/mac_bot_browser.py
```

## Still open

- Which of the three fixes mattered most is not proven.
- Still odd, not fixed: Linux; software graphics; notifications `denied` on a first visit (Playwright's
  `permissions=["camera", "microphone"]` denies every other permission); `Accept-Language: en-US` without
  `en;q=0.9`; no speech voices. If refusals come back, start here.
- A cloud server adds one more oddity: a data-centre internet address. The test from outside on the server
  (plan, Phase 6) will show whether that matters.
