# PLAN — the overnight build (2026-11-04 night)

Goal for the morning: the owner pulls `main` on a Mac (Intel, Docker Desktop), runs one command,
and tests the bot in a real Google Meet call, first on its own (task 9's test pages), then with
xebit-api. Work through the tasks in order. Each one ends with tests passing, `STATUS.md` updated,
and a commit pushed to `main`.

You cannot reach Google Meet from this VM. So everything is tested against a **fake meeting page**
(task 5) that has the same buttons and labels as Meet. The real-Meet checks wait for the morning;
list them in `MORNING.md`.

---

## Task 1 — Layout and tooling

- `manager/` (FastAPI service), `bot/` (the bot image, keeps today's files), `tests/`,
  `docker-compose.yml`, `MORNING.md`, `STATUS.md`.
- `pyproject.toml` or `requirements*.txt` with pinned versions; `pytest` configured, with a
  `docker` marker for tests that need Docker.
- `.gitignore`: `.env`, `output/`, `__pycache__/`, `.venv/`, run artifacts.

**Done when** `pytest` runs (even with zero tests) and the layout is committed.

## Task 2 — The API, without Docker

- Everything in `docs/SPEC.md` section 2, with the bot runner behind an interface so tests can use
  a fake runner.
- Status list, `xbot_` ids, auth, `404` / `409` / `429` cases, list filter, the JSON state file.

**Done when** tests cover every endpoint and check our responses with the logic copied from
`docs/xebit-consumer/` (`_latest_recall_status_code`, `_result_count`, and that `create_bot()`'s
exact request body is accepted).

## Task 3 — The bot takes orders

- The bot reads its config from the manager (meeting URL, name, camera page URL, endpoints, bot
  id, manager URL + secret) and still runs from the command line as today.
- It reports every status to the manager (and still to `events.jsonl`).
- A small control server inside the bot (port 8080): leave, start screen share (url), stop screen
  share. Leave works at any time (before admission it just stops).
- After `fatal`, report nothing more. After `call_ended`, report `done` when finished.
- `--join-only` keeps today's behaviour exactly (fake devices, no pages, no audio).

**Done when** unit tests cover config parsing, the status sequence rules and the control server.

## Task 4 — Manager runs real containers

- Start, watch and stop bot containers with the Docker SDK (`docs/SPEC.md` section 6).
- Live view port, limits, shared network, a container that dies without `done` / `fatal` gets
  `fatal` / `bot_errored`.
- `docker-compose.yml`: the manager, with the Docker socket mounted, on port 8090, building both
  images. On the Mac, xebit-api (its own compose) will call `http://host.docker.internal:8090`.

**Done when** in this VM: `docker compose up --build`, then create a bot through the API (pointing
at the fake meeting, task 5), see it reach `joining_call`, then leave it, and see `call_ended`,
then `done`, and the container gone. Put it in a `docker`-marked test.

## Task 5 — The fake meeting page (test harness, no Google)

A local page, served inside the test setup, that looks to the bot's code like Meet:

- Pre-join: a name input with the same label Meet uses ("Your name"), "Turn off microphone" /
  "Turn off camera" buttons that toggle to "Turn on ...", an "Ask to join" button.
- After "Ask to join": the text "Asking to be let in…", then after 2 s the in-call view: a
  "Leave call" button, "Turn on microphone", "Share screen", "People", and participant tiles with
  `data-participant-id` and names (including one marked as the bot itself, "(You)"). A People panel
  that lists everyone. A timer adds a participant at 20 s and removes one at 40 s.
- Its speaker plays a steady 440 Hz tone (the "meeting sound").
- It measures its own microphone level and the strongest frequency (expects the camera page's
  tone, task 6) and shows them in the page and on a local test endpoint.
- "Share screen" calls `getDisplayMedia` and checks the frames it receives are not black; "Stop
  presenting" stops it.
- "Leave call" shows "You left the meeting".

The bot gets a `--meet-url` override (or a test flag) that points browser A at this page. Nothing
else in the bot changes for the test: the same clicks, typing and state reading run against it.

Also a small **test receiver**: an HTTP + websocket server that records webhooks and audio frames,
and a **test camera page** that plays a 1000 Hz tone and reports its own `getUserMedia` level.

**Done when** the bot, in its container, joins the fake meeting through the real join code, and
the receiver gets `participant_events.join` for the fake people (not for the bot).

## Task 6 — Sound, both ways

- PulseAudio setup in `bot/entrypoint.sh` (`docs/SPEC.md` 5.1); browser A on PulseAudio (no fake
  devices) unless `--join-only`; browser B for our pages.
- The audio stream to xebit (`docs/SPEC.md` 3.2).
- Turn the microphone on after admission.

**Done when** a `docker`-marked test proves, all at once:
1. the fake meeting hears the camera page: its microphone's strongest frequency is ~1000 Hz;
2. the camera page hears the meeting: its `getUserMedia` level is above silence;
3. the receiver gets audio frames that `_decode_recall` (copied from
   `docs/xebit-consumer/audio_ingest_excerpt.py`) decodes, 16 kHz, about 200 ms each, and their
   strongest frequency is ~440 Hz;
4. the 1000 Hz tone is NOT in the frames sent to xebit (the bot doesn't hear itself).

## Task 7 — Screen share

`docs/SPEC.md` 5.5: screen page window, the click, Chrome picking the window by title, confirm,
stop. Find out exactly how `--auto-select-desktop-capture-source` matches titles and write it in
`STATUS.md`.

**Done when** a `docker`-marked test calls `POST output_media`, the fake meeting receives frames
that are not black, then `DELETE output_media` stops them.

## Task 8 — Participants and leaving

- The People panel first, tiles as fallback, 10-second rule for leaves, skip the bot itself, log
  each entry's attributes once (`docs/SPEC.md` 5.4).
- Leave and end handling with the right order: status first, then close pages (`docs/SPEC.md` 4.3
  and 5.6).

**Done when** against the fake meeting: joins for the 2 people already there, a join at ~20 s, a
leave ~10 s after the removal at 40 s, never one for the bot; `leave_call` gives `call_ended` /
`bot_received_leave_call`, then `done`, and the camera page closes after `call_ended`.

## Task 9 — The morning kit

- **Test pages served by the manager** for a real-Meet test without xebit: `/test/voice.html`
  (speaks a short sentence or plays a tone every few seconds, and shows the level it hears from the
  call) and `/test/screen.html` (a big moving clock, page title fixed).
- **`MORNING.md`**: exact steps for the Mac.
  1. `git pull`, `docker compose up --build` (expected build time), how to check it is up.
  2. Create a bot with `curl` for a real Meet link, with the test pages as camera and screen pages.
     What to do in Meet (admit it), what you should hear and see, how to open the live view, the
     `curl` for screen share on and off, and for leave.
  3. A table: each thing to check, how to see it, and what to send back if it fails (which log,
     which file).
  4. With xebit: the values to give xebit-api (base URL `http://host.docker.internal:8090/api/v1`,
     the key), and what to watch.
- **`STATUS.md`** final: what is done, what was tested here and how, what only the real Meet can
  prove, known gaps, decisions you made.

**Done when** every command in `MORNING.md` except the real Meet join has been run in this VM.

## Task 10 — Clean finish

- Whole test suite green (`pytest` and `pytest -m docker`).
- Measure one bot's CPU and memory during the fake meeting (`docker stats`), put the numbers in
  `STATUS.md`.
- Re-read `docs/SPEC.md` and tick every point in `STATUS.md` as done / not done / done differently.

## If you finish early

In this order, each behind a flag that is off by default:

1. A static picture as the bot's camera (a fake video device showing the agent's name), only if it
   can be done without bringing back the fake audio device.
2. One audio stream per person (`docs/SPEC.md` section 7). Research only: write how it could be
   done on Meet in `docs/per-person-audio.md`. Don't build it.
