# RUN

Test: the bot joins a Google Meet call as a signed-out guest. Five runs, one row per run in the table below.

## Before you start

1. Docker is running.
2. Open a terminal in this folder.
3. Start a Meet call from your own account in a browser tab, and keep that tab open. Copy its link (`https://meet.google.com/abc-defg-hij`).

## Build (once)

```
docker build -t meet-bot .
```

## Run (one test)

```
mkdir -p output
docker run --rm -p 127.0.0.1:6080:6080 -v "$PWD/output:/output" -v "$PWD/mouse:/mouse:ro" \
  meet-bot https://meet.google.com/meet/gyz-vvrw-dsv --name "Notetaker" --type-name --knock-delay 15
```

- Live view of the bot's screen: http://localhost:6080
- The bot reaches the join request about 45 seconds after it starts.
- If the call asks for it, admit the bot from your Meet tab. The bot waits up to 10 minutes.
- When the bot is in the call, wait about 30 seconds, then press Ctrl+C in the terminal. The bot leaves and the run ends.
- Exit code: 0 if the bot got in, 1 if not.

Optional: add `--trace` to also save a Playwright `trace.zip` for the run.

## Read the result

Each run writes a folder `output/<date>-<time>-<meeting>/`. Open `events.jsonl` in it. The last status before `done` is the result:

| Status (`code` / `sub_code`) | Meaning |
| --- | --- |
| `in_call_not_recording` | the bot got into the call |
| `call_ended` / `bot_received_leave_call` | you stopped the bot with Ctrl+C after it was in |
| `call_ended` / `bot_kicked_from_waiting_room` | the host denied the bot |
| `call_ended` / `call_ended_by_platform_waiting_room_timeout` | nobody answered the request |
| `fatal` / `google_meet_bot_blocked` | Meet showed "You can't join this video call" |
| `fatal` / `bot_errored` | the bot failed; the message says why |

The folder also holds screenshots (`.png`) and page captures (`.html`) from the run.

## Results

| Run | Date and time (UTC) | Meet link | Got in (yes / no) | Final status (`code` / `sub_code`) | Run folder | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-11-04 09:45:47 | https://meet.google.com/gyz-vvrw-dsv | yes | `call_ended` / `bot_received_leave_call` | `20261104-094544-gyz-vvrw-dsv` | Ctrl+C came 1 s after `in_waiting_room`, so `in_call_not_recording` was not logged. The final screenshot shows the bot in the call with 2 participants. |
| 2 | 2026-11-04 09:46:37 | https://meet.google.com/gyz-vvrw-dsv | yes | `call_ended` / `bot_received_leave_call` | `20261104-094634-gyz-vvrw-dsv` | Waiting room 09:47:19, in call the same second. |
| 3 | 2026-11-04 09:47:40 | https://meet.google.com/gyz-vvrw-dsv | yes | `call_ended` / `bot_received_leave_call` | `20261104-094736-gyz-vvrw-dsv` | Waiting room 09:48:21, in call the same second. |
| 4 | 2026-11-04 09:48:33 | https://meet.google.com/gyz-vvrw-dsv | yes | `call_ended` / `bot_received_leave_call` | `20261104-094830-gyz-vvrw-dsv` | Waiting room 09:49:15, in call 09:49:16. |
| 5 | 2026-11-04 09:49:32 | https://meet.google.com/gyz-vvrw-dsv | yes | `call_ended` / `bot_received_leave_call` | `20261104-094930-gyz-vvrw-dsv` | Waiting room 09:50:15, in call 09:50:42 (27 s until admitted). |

Total: 5 / 5 got in. Runs 2 to 5 are confirmed by the status log; run 1 is confirmed by its screenshot.

All 5 runs used the same options (from each run's `options.json`): name `Notetaker`, `--type-name`, `--knock-delay 15`, input `vnc`, not a dry run. Each run took about 42 s from start to the join request.
