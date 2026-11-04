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
| 7 Screen share | done | docker test: `POST output_media` makes the fake meeting receive bright, non-black frames of our page (a window share); a second POST replaces the page in the same share; `DELETE` stops it |
| 8 Participants and leaving | done | People panel first (opened once with the human mouse), visible tiles as fallback, 10-second rule, the bot skipped; docker tests: joins for the 2 people there, a join at ~20 s, a leave ~10 s after the removal at 40 s, none for the bot, xebit's head count ends at 2; `leave_call` and removal by the host give `call_ended` then `done` with the camera page closed after `call_ended`; `tests/test_participants.py` |
| 9 Morning kit | done | `./try.sh` (bash 3.2 + curl), test pages `/test/voice.html` and `/test/screen.html`, a small test receiver in the manager (`/testrx/...`) so `try.sh` shows people, sound frames and pictures without xebit; `MORNING.md`. Every command in `MORNING.md` was run in this VM against the fake meeting |
| 10 Clean finish | not started | |
| 11 Camera pictures | not started | |
| 12 Speaker timeline, per-person audio | not started | |

## This VM's build setup (not needed on the Mac)

The VM reaches the internet only through an HTTPS proxy on 127.0.0.1. To build here:
`python3 scripts/vm-proxy-forward.py 172.17.0.1 44337 &`, the lines of `scripts/vm.env.example` in `.env`,
and the proxy CA bundle copied to `/tmp/proxyca/agent-proxy-ca.crt`. The Dockerfiles have a `net-proxy`
stage that only these settings select; on the Mac the default stage is used and none of this is baked in.
The VM's `.env` also sets `TEST_MEET_URL` so no bot here can ever open real Meet.

## How Chrome picks the shared window (`--auto-select-desktop-capture-source`)

Read in Chromium's source (tags 120 to 145) and checked in the bot image:

1. The flag is read in `chrome/browser/ui/views/desktop_capture/desktop_media_list_controller.cc`
   (`ShouldAutoAccept`): `source.name.find(<flag value>) != npos`. So it is a **case-sensitive substring**
   match, the first matching source wins, and the picker opens and accepts by itself.
2. A window's name is its X title. A Chrome window's title is `<page title> - Google Chrome` (or
   `- Chromium`, `- Google Chrome for Testing`; our page browser shows `xbot screen share - Google Chrome
   for Testing`). An `--app=` window has no suffix.
3. It also applies to screens ("Entire screen") and tabs (plain page title), so the value must not
   appear in any tab title of the Meet browser. Ours is `xbot screen share`.
4. `--use-fake-ui-for-media-stream` skips the picker altogether (it captures screen 0, or a test pattern
   with the fake-device flag), so service mode drops it.
5. On Linux X11 the picker lists only windows with `WM_STATE = NormalState`, which a window manager sets.
   The bot sets it on its own window. A covered window still captures (X Composite); a window that is
   partly off-screen does not, so ours stays on the screen, under Meet.
6. When a share starts, Chrome raises the captured window and shows its own "<site> is sharing a window"
   bar at the bottom centre, right over Meet's control bar. The bot pushes both under Meet's window again
   (right away, every 2 s, and before every click). See `screen-share-started-*.png` in a run folder.

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
- **When is a read of the people "reliable" (allowed to cause a leave)?** A People-panel read with at least
  one entry; or a tile read whose number of distinct ids equals the number on Meet's People button; or
  Meet saying the bot is alone. Any other read can add people but never removes anyone.
- **A person seen without an id** (no `data-participant-id`) is keyed by name, and takes over the real id
  the first time it is seen, without a second join. Two people with the same name and different ids are
  two people.
- **The bot is skipped** when an entry has "(You)", the own-tile buttons ("Backgrounds and effects",
  "Reframe"), `data-self-name`, or exactly the bot's typed name (so a guest with exactly the bot's name is
  not counted either; xebit's own filter does the same). Once an id is known as the bot it stays skipped.
- **Leaves are not sent for everyone when the bot itself leaves**; xebit learns the end from the status.
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
