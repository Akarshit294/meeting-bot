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
- **A run folder per bot, like the owner's test folders:** `events.jsonl`, a screenshot at every
  status (and at every screen-share and participant change), the page HTML at the end, and the
  bot's full log. On the Mac it lands in `./output/<date>-<time>-<meeting code>/`.

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

A local page, served inside the test setup, that looks to the bot's code like Meet. Copy its labels,
texts and tile attributes from the real page snapshots in `evidence/` (see `evidence/README.md`,
section 4), not from memory:

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

- **`./try.sh <meet link> [bot name]`: the owner's one command.** It starts the service if it is not
  running (`docker compose up -d --build`), creates a bot for that link with the test pages below as
  camera and screen pages, and prints the live-view link. Then it waits for keys: `s` = start the
  screen share, `x` = stop it, `l` = leave, `q` = quit (leaves first). It prints every status change
  as it happens. It reads the API key from `.env`, and creates `.env` with a random key on the first
  run. Plain bash + curl (macOS has both).
- **Test pages served by the manager** for a real-Meet test without xebit: `/test/voice.html`
  (speaks a short sentence or plays a tone every few seconds, and shows the level it hears from the
  call) and `/test/screen.html` (a big moving clock, page title fixed).
- **`MORNING.md`**: exact steps for the Mac. Lead with `./try.sh`.
  1. `git pull`, then `./try.sh <meet link>` (first build time), how to check it is up.
  2. The same by hand with `curl`, for a real Meet link, with the test pages as camera and screen
     pages.
     What to do in Meet (admit it), what you should hear and see, how to open the live view, the
     `curl` for screen share on and off, and for leave.
  3. A table: each thing to check, how to see it, and what to send back if it fails (which log,
     which file).
  4. With xebit: the values to give xebit-api (base URL `http://host.docker.internal:8090/api/v1`,
     the key), and what to watch.
  5. A **Results** table for the owner to fill in, one row per run, like `bot/JOIN_TEST_RUN.md`.
- **`STATUS.md`** final: what is done, what was tested here and how, what only the real Meet can
  prove, known gaps, decisions you made.

**Done when** every command in `MORNING.md` except the real Meet join has been run in this VM.

## Task 10 — Clean finish

- Whole test suite green (`pytest` and `pytest -m docker`).
- Measure one bot's CPU and memory during the fake meeting (`docker stats`), put the numbers in
  `STATUS.md`.
- Re-read `docs/SPEC.md` and tick every point in `STATUS.md` as done / not done / done differently.
- **Fresh-eyes review.** Start ONE review subagent. Give it only `docs/SPEC.md`,
  `docs/xebit-consumer/` and the code, not your notes. Ask it for: (1) every place where what we
  send or answer differs from what the xebit code reads, and (2) every way a live demo could end
  by mistake: a false leave, a missing or wrong status, the bot counted as a person, the camera page
  closed before `call_ended`. Fix what it finds; write the rest in `STATUS.md`.
- Merge into `main` (`CLAUDE.md` rule 2).

## Task 11 — Each person's camera picture (only after tasks 1–10)

`docs/SPEC.md` 3.3: about 2 pictures per second per person, cut from one screenshot of Meet's
window per tick, sent on the `video_separate_png.data` websocket. On by default, with a flag to turn
it off (`--no-camera-pictures`). It runs on its own: any failure in it is logged and must never
touch the call, the audio or the screen share.

- Never the bot's own tile. During the bot's own screen share, Meet moves the tiles to a side strip:
  keep sending from wherever the tiles are.
- Measure what it costs (`docs/SPEC.md` 4.4 budget) and write the numbers in `STATUS.md`.
- Also keep each person's latest picture in the run folder (`camera/<name>.png`), and print in
  `./try.sh` how many pictures were sent per person, so the owner can check this without xebit.
  Add these checks, and the speaker timeline of task 12, to `MORNING.md`.
- Test with the fake meeting page: give its tiles different solid colours, and check each person's
  pictures arrive with that person's id and colour, and none for the bot.

**Done when** that test passes and every earlier test still passes. Then merge into `main` again.

## Task 12 — Diarized audio: who spoke, and one stream per person (experimental, after task 11)

The owner wants this. It is the hardest part, so it comes last. Do it in two steps.

**Step 1 — who spoke when (cheap, always on).** Meet shows who is talking (a speaking indicator on
the tiles and in the People panel). Read it about 5 times a second, through locators only, and
write a speaker timeline to the run folder (`speakers.jsonl`: time, person id, name, started /
stopped). This works even if step 2 fails. Log what the real indicator looks like so the morning
test can check it.

**Step 2 — one stream per person.** Behind a flag (`--per-person-audio`, off by default). The
mixed stream from task 6 stays the default, so the morning test has a working baseline either way.

- Goal: instead of one `meeting-mix` stream, send one stream per person, in the same frame format
  (`docs/SPEC.md` 3.2), with that person's participant `id` and `name` (the same ids as the
  join/leave webhooks).
- Why it is hard: Meet does not send one stream per person. Every real in-call snapshot in
  `evidence/` shows exactly 3 `<audio>` elements: Meet sends the loudest speakers in 3 slots, and
  which person is in which slot changes. Naming a slot needs either code inside Meet's page (which
  can get the guest join blocked) or matching it to the person Meet shows as speaking.
- Rule: nothing runs in Meet's page world before admission (`CLAUDE.md` rule 4). Try, in order:
  1. After admission, capture each of Meet's `<audio>` elements from an ISOLATED world (not the
     page world), and name each slot by matching its sound to the speaking indicator on the tiles
     or in the People panel.
  2. Only if (1) can't work: describe (don't build) what page-world code after admission would
     need, and its risk.
- Test it with the fake meeting page: give it 3 `<audio>` elements playing different tones and a
  speaking indicator per person, and check each person's frames carry that person's tone.
- Write what you learned, what works, and what to check in a real call in
  `docs/per-person-audio.md`. Add a `./try.sh --per-person-audio <meet link>` line to `MORNING.md`.

**Done when** the speaker timeline is written in the fake-meeting test, the per-person test passes
with the flag on, and the mixed default still passes. Then merge into `main` again.

## If you finish early

A static picture as the bot's camera (a fake video device showing the agent's name), only if it can
be done without bringing back the fake audio device. Behind a flag, off by default.

---

# ROUND 2 (2026-11-05): files per person, per run

Tasks 1–12 are done. Round 2 adds **recordings in every run folder**, so the owner can play back
what the bot got from each person, and fixes what the first real test found. Same rules as before
(`CLAUDE.md`), in particular: nothing new before admission, nothing ever runs in Meet's page world
(isolated worlds and locators only), the join stays exactly as it is.

## What the first real test showed (2026-11-05, owner's Mac)

The run folders are in `evidence/real-meet-2026-11-05/` (logs, status lists, timelines, every
screenshot; the big page dumps are left out). Read them first.

1. Run 1: Google blocked the bot right after "Ask to join" (`fatal` / `google_meet_bot_blocked`).
   Runs 2 and 3: admitted in 1–3 s. One block in three; keep watching, change nothing for it now.
2. **Voice works:** the owner heard the chime in the call.
3. **Screen share works:** the clock showed in the owner's Meet. But the bot's "stop" click missed
   (`warning: Meet still shows the bot presenting`); the share ended only when the screen page
   closed. The top bar shows `Maya (You, presenting)` + `Stop presenting`; the self-view also shows
   a `Stop presenting` button (see `share-started-*.png`).
4. **People:** right. The People panel lists `Contributors 3`: `Maya (You)`, `Maya — Your
   presentation` (the bot's own share, listed as its own entry: never a person), `Akarshit —
   Meeting host`.
5. **Pictures:** too few (5 in about 45 s in run 2, none in run 3), and `camera/` stayed EMPTY in
   both runs although pictures were sent. Find out why and fix it.
6. **Who spoke when:** `speakers.jsonl` stayed empty: Meet's speaking indicator wasn't recognised.
   Meet's class names are scrambled (`tile_classes` shows things like `oZRSLe > FKJK2b ZrmAYe`), so
   don't rely on class names alone. In the People panel each person has a round blue "dots" icon
   next to their name that moves when they talk (see the screenshots).
7. Sound to xebit worked: 281 and 207 frames of 200 ms, none dropped.

## Task R1 — See the real page (debug)

Save what Meet's page looks like during the call, so the next real run shows the real structure
of tiles, video elements, captions and the speaking indicator:

- In `dom/` in the run folder: `page.content()` and Playwright's `aria_snapshot()` of the body, at
  `in_call_recording`, after captions turn on (task R3), at every people change, at the start and
  stop of a screen share, and every 30 s. Both are read in Playwright's isolated world.
- On in `./try.sh`; a flag (`--save-dom`) in service mode, off by default.

**Done when** a fake-meeting run writes them and they are listed in `MORNING.md`.

## Task R2 — Recordings, in every run folder

A flag `--save-media`: on in `./try.sh`, off by default in service mode (xebit gets the streams;
files are for testing). All files land in the run folder:

| File | What | How |
| --- | --- | --- |
| `audio/mixed.wav` | The whole call's sound, exactly what was streamed to xebit (16 kHz mono) | Write the frames you already send |
| `audio/slot-1.wav` … `slot-3.wav` | Meet's 3 sound slots, raw, unnamed | The isolated-world tap that `--per-person-audio` already has |
| `audio/<name>.wav` | Each person's sound | Task R3 |
| `video/<name>.webm` | Each person's camera | Record the `<video>` element inside that person's tile from an ISOLATED world (MediaRecorder; chunks leave through a binding only that world has, the same way `bot/per_person_audio.py` does it). The tile's participant id names it. A video element that goes away and comes back starts `<name>-2.webm`. A person with the camera off gets no video file (their tile pictures stay). |
| `screen.mp4` | The bot's whole screen, 5 frames a second | ffmpeg `x11grab` of the display. Its own flag `--record-screen`, on in `./try.sh` |

- Every file must be playable on a Mac (QuickTime or a browser) when the run ends, including after
  `q`, a removal, or a crash. Close them properly on every way out.
- Measure the CPU of each piece with `docker stats` and write it in `STATUS.md`. Anything that
  doesn't fit the budget (`docs/SPEC.md` 4.4) stays test-only.
- At the end of a run, `./try.sh` lists every file saved, with its length in seconds.

**Done when** a fake-meeting run with 3 people (one camera off) gives `mixed.wav`, 3 slot files, a
video file per camera-on person with that person's colour, no video for the camera-off person,
and `screen.mp4`, all the right length.

## Task R3 — Who spoke when, from Meet's captions; each person's sound

1. After admission (after the microphone and the camera page), turn on Meet's captions in English
   with the human mouse ("Turn on captions" / the CC button). Captions show only on the bot's
   screen; nobody else in the call sees them. Confirm they are on; if the button can't be found, log
   it and carry on (the call must not suffer).
2. Read the captions area through locators only: who (the name Meet shows) and what, as it
   updates. Write `captions.jsonl` (time, name, text, and whether the line is still changing).
3. `speakers.jsonl`: from the captions (a person starts speaking when their caption line starts or
   grows; stops ~1 s after it stops changing), plus the People-panel indicator if you can read it
   reliably. Match names to the roster ids.
4. `audio/<name>.wav`: give each slot's sound, moment by moment, to the person speaking at that
   moment (step 3). When two people speak at once, give each slot to the speaker whose caption is
   growing and whose slot has the most sound. Sound nobody can be given goes into
   `audio/unknown.wav`. Write in `docs/per-person-audio.md` how well this works and where it fails.
5. `--per-person-audio` (the live stream to xebit) uses the same naming.

**Done when** the fake meeting (now with a captions area that names whoever is talking, and 3
slots with different tones) gives one file per person carrying that person's tone, and
`captions.jsonl` and `speakers.jsonl` match the script of who talked when.

## Task R4 — Fixes from the real test

1. Pictures: why so few, and why `camera/` stayed empty. Fix both; pictures keep coming about twice a
   second while a person's tile is on screen, and `camera/<name>.png` is always there.
2. Stop presenting: click the right button (top bar `Stop presenting`, or the self-view one) and
   confirm Meet no longer shows the bot presenting before closing the screen page.
3. `./try.sh`: print the run folder at the start, and the file list at the end (R2).

## Task R5 — Finish

Fake-meeting tests for R1–R4; the whole suite green; `STATUS.md` updated (a "Round 2" section);
`MORNING.md`: a short "Round 2 test" (one `./try.sh` run where 2 people talk in turns, with captions;
what to listen to and look at in the run folder, and what to send back). Merge into `main`.
