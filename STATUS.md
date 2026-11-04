# STATUS

Where the overnight build (2026-11-04) stands. Newest first inside each section.

## Tasks

| Task | State | Notes |
| --- | --- | --- |
| 1 Layout and tooling | done | `pytest` runs; Docker tests are marked `docker` |
| 2 API without Docker | done | `tests/test_api.py`: every endpoint, checked with xebit's `_latest_recall_status_code`, `_result_count` and `create_bot()`'s exact body (`tests/xebit_logic.py`) |
| 3 Bot takes orders | done | config from the manager, status reports, control server on 8080, run folder with `bot.log`, `--join-only`; `tests/test_bot_orders.py` |
| 4 Manager runs real containers | done | Docker SDK runner, compose; `tests/test_docker_manager.py` (create, `joining_call`, leave, `call_ended`, `done`, container gone; a killed container gets `fatal`) |
| 5 Fake meeting page | done | `tests/testkit/` (fake Meet from the evidence snapshots, receiver, test camera and screen pages); `tests/test_docker_meeting.py`: the bot joins through the real join code; joins for Asha and Ravi, none for the bot |
| 6 Sound, both ways | done | PulseAudio in the bot; docker test: the fake meeting hears ~1000 Hz from the camera page, the camera page hears 440 Hz, xebit's `_decode_recall` reads 200 ms 16 kHz frames peaking at 440 Hz with no 1000 Hz; `tests/test_audio.py` (format, pings, reconnect, backoff) |
| 7 Screen share | not started | |
| 8 Participants and leaving | not started | |
| 9 Morning kit | not started | |
| 10 Clean finish | not started | |
| 11 Camera pictures | not started | |
| 12 Speaker timeline, per-person audio | not started | |

## This VM's build setup (not needed on the Mac)

The VM reaches the internet only through an HTTPS proxy on 127.0.0.1. To build here:
`python3 scripts/vm-proxy-forward.py 172.17.0.1 44337 &`, the lines of `scripts/vm.env.example` in `.env`,
and the proxy CA bundle copied to `/tmp/proxyca/agent-proxy-ca.crt`. The Dockerfiles have a `net-proxy`
stage that only these settings select; on the Mac the default stage is used and none of this is baked in.
The VM's `.env` also sets `TEST_MEET_URL` so no bot here can ever open real Meet.

## How to run the tests

1. `python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt`
2. Fast tests: `pytest`
3. Docker tests (slow, need Docker): `pytest -m docker`

## Decisions

(Each one: what was unclear, what was chosen, why.)

- **Browser A (Meet) in service mode drops `--use-fake-ui-for-media-stream` too**, not only the fake
  device flags. Reason: with that flag Chrome skips the screen picker and captures the whole screen, so
  `--auto-select-desktop-capture-source` can never pick our window (checked in Chromium's source and in
  a test in the image: `NotReadableError`). Microphone permission still comes from the browser context, as
  before. `--join-only` keeps all of today's flags exactly (a test checks them).
- **Chrome picks the share window by a fixed title we give it**, `xbot screen share` (the page's title is
  pinned to it in browser B). Chrome matches the flag's value as a case-sensitive SUBSTRING of the source
  name, and a window's name is its title plus a suffix (`... - Google Chrome for Testing`).
- **Our screen window gets `WM_STATE` set by the bot.** Chrome lists a window as shareable only if it
  has `WM_STATE = NormalState`, which a window manager sets. The bot's display has no window manager (and
  adding one would change the proven Meet window), so the bot sets it on its own window with python-xlib.
- **The screen window is fullscreen** (through the DevTools protocol in browser B), so the share shows
  only the page: no address bar, no URL with its token.
- **All browser-B windows are kept UNDER Meet's window** (re-lowered every 2 s and before every click).
  The bot's clicks go to whatever window is on top; Chrome still captures a covered window (X Composite).
  The screen stays 1280x720 like the proven runs.
- **The bot's websocket clients read what the server sends**, only to answer its pings. Found in the fake
  meeting test: uvicorn (which xebit-api also runs) closes a socket whose pings go unanswered, so without
  this the audio socket dropped every 40 s.
- **`--join-only` does not start PulseAudio**, so Meet sees the same devices as in the proven runs.
- **After `fatal` the bot writes nothing more, not even `done`** (in `events.jsonl` too), to match the API.
- **`in_call_recording` is reported once the start steps are done** (people read, audio started, camera
  page open, microphone on), whether or not the create request had a camera page or an audio endpoint.
- **If the camera page can't be opened (3 tries), the bot leaves and reports `fatal` / `bot_errored`.**
  A bot in the call with no voice would look fine to xebit but be mute.
- **Meet is read only with locator reads** (`count`, `inner_text`, `get_attribute`, `is_visible`) and
  screenshots/`page.content()`, which Playwright runs in its isolated world. `all_inner_texts()` and
  `evaluate()` run in Meet's page world, so the bot never uses them on Meet.

- **A container that stops after `call_ended` gets `done`, not `fatal`.** The spec says a container that
  dies without `done` or `fatal` gets `fatal` / `bot_errored`. But after `call_ended` only `done` may follow
  (xebit already treats the bot as gone), so the manager adds `done` there instead.
- **Status rules live in the manager** (`manager/store.py`): the same code twice in a row is dropped;
  nothing after `fatal`; only `done` after `call_ended`; nothing after `done`.
- **If Docker can't start a bot, `POST /bot/` answers `503`** (and the bot record gets `fatal` /
  `bot_errored`), so xebit-api fails the launch at once instead of polling.
- **`leave_call` before the bot's control server is up** stops the container with SIGTERM. The bot treats
  SIGTERM as a leave request, as Ctrl+C did in the proven runs.
- **The bot reads its config from the manager** (`GET /internal/bots/<id>/config` with its own token),
  so the camera page URL and the endpoint URLs (they carry tokens) are not in the container's environment.
- **Each bot gets its own internal token** (HMAC of a manager secret and the bot id). The secret is kept in
  the state file, so a manager restart keeps live bots working.

## Known gaps

## Only a real Meet call can prove
