# SPEC — what the service must do

Version 1 (built 2026-11-04 night). Google Meet only. Guest join only.

## 1. The big picture

```
xebit-api ──REST──▶ manager (this repo, port 8090) ──starts──▶ one bot container per meeting
    ▲                                                            │
    │  webhooks: participant join/leave                          │  Google Meet (guest)
    └── websocket: the call's audio, 16 kHz ◀────────────────────┤  camera page = agent's voice
                                                                  │  screen page = screen share
```

- **manager**: a FastAPI service. It serves the API in section 2, starts and stops bot containers
  through the Docker socket, and keeps each bot's status list.
- **bot container**: one per meeting. It runs Xvfb, PulseAudio, x11vnc + noVNC (live view), the Meet
  browser (the proven guest join in `bot/`), and a second browser for our pages. It pushes events
  and audio straight to the URLs xebit-api gave in the create request.

What xebit-api reads from us is in `docs/xebit-consumer/`. When this spec and that code disagree,
the code wins; note it in `STATUS.md`.

## 2. REST API (the Recall-compatible part)

- Base path: `/api/v1`. xebit-api will be configured with a base URL like
  `http://host.docker.internal:8090/api/v1`.
- Auth: header `Authorization: Token <BOT_API_KEY>` on every call. Wrong or missing key: `401`.
- JSON in, JSON out. Errors: a 4xx/5xx with `{"detail": "..."}` (xebit-api logs the body).
- Unknown request fields are accepted and ignored.
- Trailing slashes as written below are what xebit-api sends. Accept both with and without.

### 2.1 `POST /api/v1/bot/` — create a bot

The full request is built by `create_bot()` in `docs/xebit-consumer/recall_client.py`. Fields we use:

| Field | Use |
| --- | --- |
| `meeting_url` | Required. Must be a `https://meet.google.com/...` link, else `400`. |
| `bot_name` | The guest name the bot types. Default `"Meeting Bot"`. |
| `output_media.camera.config.url` | The **camera page** (xebit's `demo_v2-bridge.html`). See section 4. Optional: no camera page means a bot that only joins and listens. |
| `recording_config.realtime_endpoints` | List of `{type, url, events}`. Where to push events and audio. See section 3. |
| `metadata` | Store it and return it in `GET`. |
| `recording_config.transcript`, `video_mixed_*`, `include_bot_in_recording`, `variant`, `join_at` | Accept and ignore in v1. |

Response `201`: the bot object (section 2.2). xebit-api reads only `id`.

Bot ids look like `xbot_<32 hex chars>`. xebit-api will use the `xbot_` prefix to know which bots
are ours, so keep it.

### 2.2 `GET /api/v1/bot/{id}/` — one bot

```json
{
  "id": "xbot_3f2c...",
  "meeting_url": "https://meet.google.com/abc-defg-hij",
  "bot_name": "Maya",
  "metadata": {"demo_script_id": "..."},
  "status_changes": [
    {"code": "joining_call", "sub_code": null, "message": "", "created_at": "2026-11-05T04:30:00.000000Z"},
    {"code": "in_waiting_room", "sub_code": null, "message": "", "created_at": "..."},
    {"code": "in_call_not_recording", "sub_code": null, "message": "", "created_at": "..."},
    {"code": "in_call_recording", "sub_code": null, "message": "", "created_at": "..."}
  ],
  "live_view_url": "http://localhost:49153"
}
```

`404` for an unknown id. xebit-api reads `status_changes[-1].code`
(`docs/xebit-consumer/status_excerpt.py`) and polls this every 5 seconds while it waits.

**Status codes, in order:**

| Code | When |
| --- | --- |
| `joining_call` | The bot container started. |
| `in_waiting_room` | The bot asked to join and is waiting to be let in. |
| `in_call_not_recording` | The bot is in the call. |
| `in_call_recording` | The camera page is open and the call's audio is streaming to xebit. |
| `call_ended` | The bot left or was removed. `sub_code` says why (the bot's existing sub codes). |
| `done` | After `call_ended`, once the container has stopped. |
| `fatal` | The bot failed (`google_meet_bot_blocked`, `meeting_not_found`, `bot_errored`, ...). **Nothing comes after `fatal`** (xebit-api only notices a failure while `fatal` is the latest code). |

xebit-api treats `in_call_not_recording` and `in_call_recording` as "in the call", and `call_ended`,
`done` and `fatal` as "gone". A bot whose container dies without reporting gets `fatal` /
`bot_errored` from the manager.

### 2.3 `POST /api/v1/bot/{id}/leave_call/` — leave

Body: none. The bot clicks "Leave call", then reports `call_ended` / `bot_received_leave_call`, then
`done`. Return `200` with the bot object. Safe to call again, or on a bot that is already gone
(`200`, no error).

### 2.4 `POST /api/v1/bot/{id}/output_media/` — start the screen share

Body: `{"screenshare": {"kind": "webpage", "config": {"url": "<screen page URL>"}}}`

The **screen page** (xebit's `screencast-viewer.html`, page title `Xebit Screencast`) is shown in
the call as the bot's screen share. See section 4. Return `200` with the bot object once the click
is made; confirm the share started in the bot's log. A second call with a new URL replaces the
page. Bot not in the call: `409`.

A body with `camera` instead of `screenshare`: `400` in v1 (xebit-api never sends it).

### 2.5 `DELETE /api/v1/bot/{id}/output_media/` — stop the screen share

Body: `{"screenshare": true}`. Stop presenting and close the screen page. `200`. No share running:
still `200`.

### 2.6 `GET /api/v1/bot/` — list bots

Query `status=<code>`: only bots whose latest code equals it. Response:
`{"count": <n>, "next": null, "previous": null, "results": [<bot>, ...]}`. xebit-api's pre-launch
check calls `GET /bot/?status=in_call_recording` and reads the length of `results`; it treats `401`
and `403` as a bad key (`docs/xebit-consumer/preflight_excerpt.py`).

### 2.7 Also

- `GET /health` (no auth): `{"ok": true}`.
- Endpoints for the bot containers to report to the manager are internal; protect them with a
  shared secret the manager generates and passes to each container.

## 3. What the bot pushes to xebit-api

The URLs come from `recording_config.realtime_endpoints` in the create request. Each item is
`{"type": "webhook" | "websocket", "url": "...", "events": [...]}`. Use the URL exactly as given.
If a push fails, log it (host and path only) and keep going; retry webhooks up to 3 times.

### 3.1 Participant join and leave — webhook, events `participant_events.join` / `participant_events.leave`

`POST <url>` with:

```json
{
  "event": "participant_events.join",
  "data": {
    "bot": {"id": "xbot_3f2c..."},
    "data": {
      "participant": {"id": "<stable id>", "name": "Asha", "is_host": false, "is_bot": false},
      "timestamp": {"absolute": "2026-11-05T04:31:02.123456Z"}
    }
  }
}
```

What xebit-api does with these (`docs/xebit-consumer/webhooks_excerpt.py`):

- The FIRST human join starts the demo. Send joins for everyone already in the call as soon as the
  bot is admitted.
- It keeps a head count. When it drops to 0, it ends the session 60 seconds later unless someone
  joins again. **So a false "leave" can end a live demo.** Only send a leave when you are sure:
  the person has been missing for 10 seconds or more, in a reliable list (section 5.4).
- It keeps the names for the agent.
- **The bot itself must never count as a person.** Don't send a join for the bot's own tile. If you
  ever send one, it must have `"is_bot": true`.
- `id` must be non-empty and stable for that person for the whole call (Meet's
  `data-participant-id` value is fine). It may be a string.

### 3.2 The call's audio — websocket, event `audio_separate_raw.data`

Open a websocket client to the URL (it carries `?token=...`). Send text frames, one per chunk:

```json
{
  "event": "audio_separate_raw.data",
  "data": {
    "data": {
      "buffer": "<base64 of 16 kHz, mono, signed 16-bit little-endian PCM>",
      "timestamp": {"absolute": "<ISO time of the chunk's first sample, UTC>"},
      "participant": {"id": "meeting-mix", "name": "Meeting", "is_bot": false}
    },
    "bot": {"id": "xbot_3f2c..."}
  }
}
```

- Chunks of about 200 ms (6400 bytes of PCM). Recall sends 200 ms pieces too.
- **v1 sends the whole call's sound as ONE participant** (`meeting-mix`). The agent hears everyone
  but can't tell who spoke. One stream per person is version 2.
- This is the agent's ears. Without it the agent hears nothing. Start it when the bot is admitted;
  reconnect with backoff (1, 2, 4, 8 s, max 8 s) if it drops; stop it when the bot leaves.
- The bot's own voice must not be in it (Meet does not play your own microphone back to you, so the
  Meet browser's speaker output is right).
- Decoder that reads it: `_decode_recall` in `docs/xebit-consumer/audio_ingest_excerpt.py`.

### 3.3 Not in v1

- `transcript.data` / `transcript.partial_data` webhooks: no speech-to-text in v1. Send nothing.
- `video_separate_png.data` websocket: don't connect.
- Recall's status webhook: none. xebit-api polls `GET /bot/{id}/`.

## 4. Inside the bot: the two pages

This is what Recall calls "Output Media", and what xebit's camera page expects
(`docs/xebit-consumer/bridge_page_notes.md`):

1. **Camera page** (`output_media.camera.config.url`). Open it right after the bot is admitted,
   and keep it open until the bot leaves.
   - Whatever it plays (Web Audio) is the bot's **microphone** in the call. This is the agent's
     voice.
   - In it, `navigator.mediaDevices.getUserMedia({audio: true})` must return the **call's sound**
     (everyone except the bot). xebit records the meeting from this.
   - On Recall its picture is also the bot's camera. **v1: the bot's camera stays off** (a live
     camera needs a virtual camera device; that is version 2).
   - It needs autoplay without a click, and must not be slowed down when its window is not in front.
   - It talks to xebit-api over websockets and loads files from the same host.
2. **Screen page** (from `POST output_media`). Shown in the call as the bot's **screen share**,
   1280x720, until `DELETE output_media` or the bot leaves.
3. **Order at the end:** report `call_ended` FIRST, then close the camera page. (When the camera
   page's socket closes, xebit-api asks `GET /bot/{id}/`; if the bot still looks in the call, it
   marks the session failed.)
4. Budget: Recall gives each bot 2.25 CPU cores and 5.25 GB. The camera page was tuned to fit that
   (it crackled when it used more). Give each bot container `--cpus 2.5 --memory 5g --shm-size 2g`.

## 5. Inside the bot: how (suggested design, Linux + Docker, no kernel modules)

### 5.1 Sound (PulseAudio inside the container)

- Null sink `meet_out`: the Meet browser plays the call here.
- Null sink `agent_out`: the page browser plays the camera page (agent's voice) here.
- Virtual source `agent_mic`: a remap of `agent_out.monitor`. The Meet browser's microphone.
- The page browser's microphone: `meet_out.monitor` (the call's sound).
- Audio to xebit: read `meet_out.monitor` at 16 kHz mono s16le (for example `parec` or ffmpeg),
  cut into 200 ms chunks, send as in 3.2.
- Pick devices per browser with `PULSE_SINK` / `PULSE_SOURCE` environment variables on each browser
  process.

### 5.2 Two browsers

- **Browser A — Meet.** Today's bot (Google Chrome through Playwright, headful on Xvfb). To use
  PulseAudio it must drop `--use-fake-device-for-media-stream` and the silent `.wav` flag; with no
  camera device, the camera is simply off. `--join-only` keeps today's fake devices, unchanged.
  Add `--auto-select-desktop-capture-source=<screen page window title>` so "Share screen" picks
  our window without a picker (check how Chrome matches the title; the window title may carry a
  browser suffix). Add the flags that stop background throttling
  (`--disable-background-timer-throttling`, `--disable-backgrounding-occluded-windows`,
  `--disable-renderer-backgrounding`).
- **Browser B — our pages.** A plain Chromium (Playwright's bundled one is fine). Not Google's
  site, so it needs no care about looking automated. `--autoplay-policy=no-user-gesture-required`,
  `--use-fake-ui-for-media-stream` (to allow the microphone without a prompt), the same
  anti-throttling flags. Camera page and screen page in separate windows. The screen page window
  is 1280x720 with a fixed title.

### 5.3 After admission, in this order

1. Report `in_call_not_recording`.
2. Send joins for the people already there (5.4).
3. Start the audio stream (3.2).
4. Open the camera page in browser B.
5. Turn the bot's microphone ON in Meet ("Turn on microphone"), with the same human-mouse click
   the join uses. Check it is on.
6. Report `in_call_recording`.
7. Keep watching: removed, call ended, alone too long (the bot's existing logic), participants,
   leave requests, screen-share requests.

### 5.4 Who is in the call

- Meet's video tiles (`[data-participant-id]`) are a VIEW, not the full list: during a screen
  share, or with many people, tiles move or hide. Don't send a leave just because a tile went away.
- Prefer the **People panel**: open it once after admission (click "People" / "Show everyone" with
  the human mouse) and read the list from it. Fall back to tiles if the panel can't be read.
- Send a leave only after a person has been missing from the list for 10 seconds or more.
- The bot's own entry: skip it. Meet marks it ("(You)", a "You" label, or `data-self-name`); the
  bot also knows its own typed name. Log each entry's attributes once so the morning test shows
  the real page.
- Read Meet only through Playwright locators (isolated world). Never inject JavaScript into Meet.

### 5.5 Screen share

1. Open the screen page in its own browser-B window (1280x720, fixed title).
2. In Meet, click "Share screen" / "Present now" with the human mouse. Chrome picks the window by
   title (5.2), so no picker appears.
3. Confirm Meet shows "You are presenting" / a "Stop presenting" button. If not within 10 s, log
   it and try once more.
4. Stop: click "Stop presenting" (or the equivalent), then close the window.

Labels differ between Meet versions: match several (see `spikes/signed-in-join/src/page.ts`, the
patterns in `bot/meet_bot.py`, and the real page snapshots in `evidence/`).

### 5.6 Leave

On a leave request, or when the call ends: click "Leave call" (if still in), report `call_ended`
with its sub code, close the pages and the browsers, then report `done` and exit.

## 6. Manager details

- Starts the bot image `meeting-bot:latest` with the Docker SDK, one container per bot, named
  `xbot-<id>`, with the limits in 4.4, on a Docker network shared with the manager.
- Passes the bot its config (meeting URL, name, camera page URL, endpoints, bot id, the manager's
  internal URL and secret) through environment variables or a mounted JSON file.
- Publishes the bot's noVNC port (6080) on `127.0.0.1:<free port>` and returns it as
  `live_view_url`.
- Sends leave and screen-share requests to the bot's small control server (port 8080 inside the
  container).
- Keeps state in memory and in a JSON file (so a manager restart doesn't lose live bots).
- Watches its containers: one that exits without `done` or `fatal` gets `fatal` / `bot_errored`.
- Config: `BOT_API_KEY` (required), `BOT_IMAGE` (default `meeting-bot:latest`), `MANAGER_PORT`
  (8090), `MAX_BOTS` (default 4, `429` above it).

## 7. Not in v1

One audio stream per person · a live camera picture · signed-in joins (a Google account) ·
speech-to-text · webcam pictures of people · Zoom and Teams · running on a production server.
