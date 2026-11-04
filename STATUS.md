# STATUS

Where the overnight build (2026-11-04) stands.

**In short:** all 12 tasks are done and merged into `main`. Every part works against a fake Meet page in
this VM:
- the join (the proven code, unchanged);
- the bot's voice and its hearing of the call;
- the sound stream to xebit, joins and leaves, each person's camera picture;
- the screen share, and leaving.

The experimental one-stream-per-person sound (`--per-person-audio`) also works there. Nothing was tried on
real Meet: that is not allowed from this VM. Start with `MORNING.md`.

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
| 10 Clean finish | done | whole suite green (at the end of the night: `pytest`: 103, `pytest -m docker`: 24, also from a fresh clone of `main`); CPU and memory measured; spec ticked below; one fresh-eyes review, its findings fixed or listed below; merged into `main` |
| 11 Camera pictures | done | one JPEG screenshot of Meet's page per tick, each roster person's tile cut out (at most 320 px wide, PNG), about 2 a second while the picture changes and every 5 s when it doesn't; never the bot; `camera/<name>.png` in the run folder; `--no-camera-pictures` turns it off; docker test: each person's frames carry their id, name and tile colour, Asha (camera on) about 2 a second, Ravi (camera off) still sent, none for the bot; `tests/test_pictures.py` |
| 12 Speaker timeline, per-person audio | done (per-person is EXPERIMENTAL, off by default) | step 1: `speakers.jsonl` in every run (docker-tested in both modes); step 2: `--per-person-audio` hooks Meet's 3 `<audio>` slots from an isolated world and names them by the speaking indicator; docker test: each person's frames carry their own tone; falls back to the mixed stream if nobody can be named. See `docs/per-person-audio.md` |

## What was tested here, and how

All in this VM, with the fake Meet page (`tests/testkit/meet.html`, built from the real snapshots in
`evidence/`). The bot ran in its real container, through the manager's real API.

1. **The join:** the bot opens the fake page and goes through the unchanged join code: typing the name over
   VNC, the recorded mouse, Ask to join. The fake page records that every click was real input.
2. **Voice and hearing:** the fake meeting's microphone hears the camera page's 1000 Hz tone; the camera
   page hears the meeting's 440 Hz tone; the frames sent to xebit (decoded with xebit's own
   `_decode_recall`) are 200 ms, 16 kHz, peak at 440 Hz and hold no 1000 Hz (the bot doesn't hear itself).
3. **Screen share:** `POST output_media` makes the fake meeting receive bright frames of our page as a
   window share; a second POST changes the page in the same share; `DELETE` stops it.
4. **People:** joins for the 2 people already there, a join ~20 s later, a leave ~10 s after a person
   left, never one for the bot; xebit's own head-count logic ends at the right number.
5. **Ending:** `leave_call` and removal by the host both give `call_ended` (right sub code), then `done`;
   the camera page is still open when `call_ended` is reported and closed before `done`; the container goes.
6. **The manager:** every endpoint, the status rules, 401/404/409/429, the state file, a crashed container.
7. **`./try.sh` and every command in `MORNING.md`** (except joining real Meet).
8. **`--join-only`** (the morning fallback): the bot joins with today's fake devices and leaves cleanly.
9. **A fresh clone of `main`** builds and passes every test (nothing depends on files left in this VM).

## Only a real Meet call can prove

1. That Meet still lets the guest in with the service-mode Chrome settings (no fake microphone/camera,
   no fake-UI flag, PulseAudio devices). `MORNING.md` has a way back to the exact proven join (`--join-only`).
2. That Meet's real "Share screen" button starts presenting our window with no other dialog, and how
   Meet shows "you are presenting" (the bot looks for a "Stop presenting" button or "You are presenting").
3. Where Chrome's "is sharing a window" bar sits over real Meet, and that the bot's clicks still land
   (the bot pushes that bar under Meet's window).
4. The real People panel: the button (`People 3` in the real page), the list's role and labels, whether
   entries carry `data-participant-id`, and how the bot's own entry is marked. The bot logs every entry's
   attributes once (`timeline.jsonl`, `entry_seen`) so this can be checked.
5. What Meet's real speaking indicator looks like (no snapshot caught anyone talking). Until it is known,
   `speakers.jsonl` may stay empty in a real call, and `--per-person-audio` falls back to the mixed stream.
   `timeline.jsonl` logs each tile's class names (`tile_classes`) to find it; see `docs/per-person-audio.md`.
6. The sound level and quality of the voice in a real call, and xebit's real camera page in browser B.
7. CPU and memory with real video (see Measurements).

## Known gaps

1. **The mouse pointer shows in the screen share** when it is over the shared area (Chrome draws it into a
   window capture). Cosmetic.
2. **The shared window is 1279x719** (the screen is 1280x720 and Chrome keeps a 1 px edge), not exactly 1280x720.
3. **The click into the name field is released only when the mouse next moves** (about 7 s later, after
   typing). Seen in the fake meeting; it is the proven join's own behaviour, so it was left unchanged.
   x11vnc holds a click's release until the next pointer event. In the call, every click now ends with a
   1-pixel move so it can't happen there.
4. **No heartbeat:** if the bot's main loop hangs (not crashes), its status stays as it was. The manager
   only notices a container that stops.
5. **`MAX_BOTS` counts every bot that has not finished** (joining, waiting, in the call, just ended).
   xebit's pre-launch check counts only `in_call_recording`, so it can say "healthy" while a create gets `429`.
6. **The bot's own "alone" timer** (leave after 60 s alone) uses the same people list; if Meet's list can
   never be read completely, the bot stays until `MAX_DURATION` (2 h). xebit ends the session on its own.
7. **`is_host`** in the join webhook is set only when the first read shows "Meeting host".
8. **No transcript webhooks** (not in v1).

## The spec, point by point (`docs/SPEC.md`)

| Point | State |
| --- | --- |
| 1 manager (FastAPI) + one bot container per meeting (Xvfb, PulseAudio, x11vnc + noVNC, Meet browser, page browser) | done |
| 2 base path, `Authorization: Token`, 401, `{"detail"}` errors, unknown fields ignored, both slash forms | done |
| 2.1 create: Meet link check (400), `bot_name` default, camera page, endpoints, metadata, `xbot_` ids, 201 | done |
| 2.2 get: shape, 404, status codes and order, nothing after `fatal`, dead container gets `fatal` | done; differently: a container that stops after `call_ended` gets `done`, and one asked to leave before joining gets `call_ended` / `bot_received_leave_call` |
| 2.3 leave: 200, safe to repeat, on a gone bot too | done; also `DELETE /bot/{id}/` acts as a leave and `PATCH` is accepted and ignored |
| 2.4 start share: 200 once the click is made, log confirms, second call replaces, 409, camera body 400 | done |
| 2.5 stop share: 200 even with no share | done |
| 2.6 list: `count/next/previous/results`, `status` filter | done |
| 2.7 `/health`; internal endpoints with a secret | done (each bot has its own token) |
| 3 URLs used as given; failures logged host and path only; webhook retries | done; differently: retried only when the connection never happened (a retry after a timeout or a 5xx could make xebit count twice) |
| 3.1 join/leave webhooks, joins for people already there, 10 s rule, never the bot, stable ids | done |
| 3.2 audio: 200 ms 16 kHz frames, one `meeting-mix` participant, start at admission, reconnect 1/2/4/8 s, stop at leave, no bot voice | done |
| 3.3 camera pictures | done (unchanged pictures are re-sent every 5 s rather than never, so xebit's picture never goes stale) |
| 3.4 no transcript, no status webhook | done |
| 4.1 camera page: after admission until leaving; its sound is the mic; it hears the call; camera off; autoplay; not throttled | done; the microphone is turned on just BEFORE the page loads, so the agent's first words are not lost |
| 4.2 screen page 1280x720 until DELETE or leaving | done (1279x719, see Known gaps) |
| 4.3 `call_ended` first, then the camera page closes | done (the bot waits until the manager has `call_ended`) |
| 4.4 `--cpus 2.5 --memory 5g --shm-size 2g` | done |
| 5.1 PulseAudio sinks and sources | done |
| 5.2 two browsers, flags, auto-select by title | done; differently: browser A also drops `--use-fake-ui-for-media-stream`, and browser B is Google Chrome |
| 5.3 the order after admission | done (with the microphone before the camera page, as above) |
| 5.4 People panel first, tiles as fallback, 10 s rule, skip the bot, log entries once, locators only | done |
| 5.5 screen share steps | done, plus: our windows and Chrome's sharing bar are kept under Meet's window |
| 5.6 leave | done |
| 6 manager: image, `xbot-<id>` containers, network, config, live view on 127.0.0.1, control on 8080, JSON state, watcher, config variables | done; the container name is `xbot-<32 hex>` (the id without its `xbot_`) |

## Measurements (one bot, fake meeting, this VM: 4 CPUs)

Measured with `docker stats` every 3 s (100% = one CPU core).

| Moment | CPU | Memory |
| --- | --- | --- |
| Joining (pre-join page) | 20-48% | about 380 MiB |
| In the call (voice, hearing, sound stream, people) | 37% on average, 46% at most | about 610 MiB |
| While sharing the screen | 71% on average, 74% at most | about 960 MiB |

With camera pictures on (task 11; measured again with the same setup):

| Moment | CPU, pictures off | CPU, pictures on | Memory |
| --- | --- | --- | --- |
| In the call | 38% on average, 47% at most | 54% on average, 57% at most | about 600 MiB |
| While sharing the screen | 73% on average, 80% at most | 87% on average, 92% at most | about 850 MiB |

One pictures tick (screenshot, cut, encode, send for 3 people) takes about 105 ms on the bot's main thread.

The budget is 2.5 cores and 5 GB. The fake meeting sends no video; a real call decodes the others'
video and encodes the shared window, so expect more CPU there (check with `docker stats` in the morning).

## The fresh-eyes review (task 10)

One review agent read only the spec, the xebit code and our code. Fixed:

1. The screen-share request could take longer than xebit's 30 s client timeout: now the bot answers right
   after its click, and a request whose caller gave up is not run later.
2. Any unexpected error in the in-call loop ended the call as `fatal`: now each tick is guarded (40 failed
   ticks in a row are needed to give up), and in-call reads have short timeouts.
3. A leave sent while the bot's container was still starting could be lost: the manager now keeps it and
   tells the bot in its answer to the next status report, and a bot that stops after such a leave gets
   `call_ended` / `bot_received_leave_call`.
4. Statuses were never re-sent: now the bot keeps the ones the manager didn't get and sends them in order,
   and waits (up to 20 s) for `call_ended` to be confirmed before closing the camera page.
5. Webhook retries could make xebit count a person twice (it counts before steps that can fail).
6. A person first seen by name only could get a different id in the leave than in the join.
7. A People-panel read with only the bot in it counted as complete (it could cause false leaves).
8. The agent's first words could go into a still-muted microphone.
9. A blocked audio socket could hang the end of the call; a crashed camera page went unnoticed.
10. The screen-share watcher could stop a working share; a "removed" text in the chat could end the call.
11. `DELETE` / `PATCH /bot/{id}/` answered 405.

Left as known gaps: no heartbeat, `MAX_BOTS` vs. the pre-launch check (see Known gaps).

## "If you finish early": a static picture as the bot's camera — not done

Chrome can show a picture as a camera only with `--use-fake-device-for-media-stream` (plus
`--use-file-for-fake-video-capture`), and that flag also replaces the microphone with a fake one, which
would cut the agent's voice. The other way, a virtual camera device (v4l2loopback), needs a kernel module
on the host, which Docker Desktop doesn't offer. So the bot's camera stays off, as the spec says for v1.

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
- **x11vnc can hold a click's release** until the next pointer event. In the call, each click ends with a
  1-pixel move. Before admission the proven mouse path is unchanged, except that in service mode a click
  on "Ask to join" that didn't register gets a 1-pixel move and, if still needed, one more click (the proven
  code then gave up with `fatal`). `--join-only` keeps today's single click.
- **The microphone is turned on just before the camera page loads** (the spec has it after), because
  xebit can start the demo as soon as it gets the first join, and the page may speak at once.
- **Browser B (our pages) is Google Chrome**, like browser A and like Recall's bot (Playwright's Chromium only
  on arm64, where there is no Chrome).
- **The screen-share request answers right after the click**; whether Meet then shows the bot presenting is
  checked over the next 10 s (one more click if not) and written in the bot's log.
- **Webhooks are retried only when the connection never happened** (refused, unknown host), never after
  a timeout or an error answer, and a leave is not sent for a person whose join failed. Reason: xebit
  counts before steps that can fail, so a retried leave could end a live demo.
- **Camera pictures are cut from a screenshot of Meet's page** (Playwright, through the DevTools protocol;
  `caret="initial"` so Playwright adds nothing to the page), not from video elements, so no code runs in
  Meet. Only people in the roster are sent, so the bot's own tile never is. A picture that hasn't changed
  is sent again every 5 s (not never), so xebit's picture of a camera-off person stays fresh. A picture is
  sent only when the person's tile is where it was at the previous tick (0.5 s before): a picture cut while
  the layout moves showed part of another tile (seen once in the tests from a fresh clone).
- **Per-person audio taps Meet's `<audio>` elements from an isolated world** (DevTools
  `Page.createIsolatedWorld` + a binding only that world has), after admission only and only with the flag.
  Nothing runs in Meet's page world. If nobody can be named for 10 s of sound, or no sound comes for 15 s,
  it goes back to the mixed stream: the agent must always hear the call.
- **`BOT_ENV_<NAME>` in `.env` reaches every bot as `<NAME>`** (for example `BOT_ENV_SPEAKING_CLASSES`),
  so a setting found in the morning needs no code change.
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

