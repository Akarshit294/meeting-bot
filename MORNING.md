# MORNING — test the bot in a real Google Meet call

Everything below runs on your Mac (Docker Desktop running). Nothing here was run against real Meet
overnight: the build VM is not allowed to open Meet. Every other command was run in the VM against a
fake Meet page.

Read `STATUS.md` for what was built and what is still unknown.

## 1. The one command: `./try.sh`

1. Get the code:

   ```bash
   cd meeting-bot
   ```

   ```bash
   git checkout main && git pull
   ```

2. In your browser, start a Google Meet call with your own account. Copy its link.
3. Run (replace the link):

   ```bash
   ./try.sh https://meet.google.com/abc-defg-hij
   ```

   - The first run builds the images: about 5 to 10 minutes. Later runs start in seconds.
   - The first run also makes `.env` with a new API key (`BOT_API_KEY`).
   - It prints a **live view** link (`http://localhost:<port>`): open it to watch the bot's screen.
   - Optional: a second word is the bot's name: `./try.sh <link> "Maya"` (default `Maya`).
4. About 45 seconds later the bot asks to join. `try.sh` prints `ADMIT "Maya"`. Click **Admit** in Meet.
5. Then `try.sh` prints `in_call_recording`. Check, in this order:
   - **The bot's voice:** you hear a short three-note chime every 4 seconds.
   - **The bot hears you:** every 10 s `try.sh` prints a line like
     `people: Akarshit | sound sent: 120 frames, last -45 dB | voice page hears the call: -38 dB | pictures sent: Akarshit 20`.
     Talk: both dB numbers should go up (towards -30 or -20). Be quiet: they go down.
   - **People:** your name is listed; the bot's own name never is.
6. Press **`s`**: within about 10 seconds the call shows the bot presenting a big clock with a moving
   ball. Press **`x`**: the presentation stops.
7. Press **`q`**: the bot leaves. `try.sh` prints `call_ended [bot_received_leave_call]`, then `done`,
   and the **run folder** (`output/<date>-<time>-<meeting code>/`).

To check the service is up at any time:

```bash
curl -s http://localhost:8090/health
```

It prints `{"ok":true}`.

Experimental, after the main test: one sound stream per person (see `docs/per-person-audio.md`):

```bash
./try.sh --per-person-audio https://meet.google.com/abc-defg-hij
```

`try.sh` then shows `sound sent: ... (Akarshit 120, Ravi 80)`: one count per person.

## 2. The same by hand, with `curl`

Run these from the `meeting-bot` folder. The service must be up (step 1 starts it, or
`docker compose up -d --build`).

1. Read your key:

   ```bash
   KEY=$(grep '^BOT_API_KEY=' .env | cut -d= -f2-)
   ```

2. Send a bot (replace the link). The test pages are served by the service itself; `manager` is its name
   inside Docker:

   ```bash
   curl -s -X POST http://localhost:8090/api/v1/bot/ -H "Authorization: Token $KEY" -H "Content-Type: application/json" -d '{"meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Maya", "output_media": {"camera": {"kind": "webpage", "config": {"url": "http://manager:8090/test/voice.html"}}}, "recording_config": {"realtime_endpoints": [{"type": "webhook", "url": "http://manager:8090/testrx/hooks", "events": ["participant_events.join", "participant_events.leave"]}, {"type": "websocket", "url": "ws://manager:8090/testrx/audio", "events": ["audio_separate_raw.data"]}, {"type": "websocket", "url": "ws://manager:8090/testrx/video", "events": ["video_separate_png.data"]}]}}'
   ```

   It answers with the bot, starting `{"id":"xbot_...`. Copy that id:

   ```bash
   BOT=xbot_paste_the_id_here
   ```

3. Watch its status (run it again and again; the last `code` is the current one). Admit the bot in Meet
   when it reaches `in_waiting_room`:

   ```bash
   curl -s http://localhost:8090/api/v1/bot/$BOT/ -H "Authorization: Token $KEY"
   ```

4. What it has sent so far (people, sound frames, pictures; "voice page hears" is only filled by `try.sh`):

   ```bash
   curl -s "http://localhost:8090/testrx/summary?bot=$BOT" -H "Authorization: Token $KEY"
   ```

5. Screen share on (the call shows a big clock):

   ```bash
   curl -s -X POST http://localhost:8090/api/v1/bot/$BOT/output_media/ -H "Authorization: Token $KEY" -H "Content-Type: application/json" -d '{"screenshare": {"kind": "webpage", "config": {"url": "http://manager:8090/test/screen.html"}}}'
   ```

6. Screen share off:

   ```bash
   curl -s -X DELETE http://localhost:8090/api/v1/bot/$BOT/output_media/ -H "Authorization: Token $KEY" -H "Content-Type: application/json" -d '{"screenshare": true}'
   ```

7. Leave:

   ```bash
   curl -s -X POST http://localhost:8090/api/v1/bot/$BOT/leave_call/ -H "Authorization: Token $KEY"
   ```

8. All bots that are in a call right now (what xebit's pre-launch check asks):

   ```bash
   curl -s "http://localhost:8090/api/v1/bot/?status=in_call_recording" -H "Authorization: Token $KEY"
   ```

## 3. What to check, and what to send back if it fails

Every run writes a folder `output/<date>-<time>-<meeting code>/`. In it:
`bot.log` (everything the bot did), `events.jsonl` (the status list), `timeline.jsonl` (people seen,
with Meet's real attributes, joins, leaves, screen share), a screenshot at every status
(`NN-<status>.png`), at every people change (`people-*.png`) and screen share (`share-*.png`,
`screen-*.png` = the whole bot screen), `final-page.html` (Meet's page at the end), `camera/` (each
person's latest picture) and `speakers.jsonl` (who spoke when).

| # | Check | How to see it | If it fails, send |
| --- | --- | --- | --- |
| 1 | The bot asks to join within about 45 s | `try.sh` prints `in_waiting_room`; Meet shows "Maya wants to join" | `bot.log`, `prejoin.png`, every `.png` in the run folder |
| 2 | It gets in when admitted | `in_call_not_recording`, then `in_call_recording` | `events.jsonl`, `bot.log`, `03-*.png`, `04-*.png` |
| 3 | You hear the bot's voice | A chime every 4 s | `bot.log` (look for `microphone ON`), `04-in_call_recording.png` |
| 4 | The bot hears the call | `voice page hears the call` goes up when you talk | `bot.log` |
| 5 | Sound is sent | `sound sent: N frames` keeps growing; its dB goes up when you talk | `bot.log` (lines starting `audio:`) |
| 6 | You are listed, the bot is not | `people: <your name>` | `timeline.jsonl` (the `entry_seen` lines), `people-*.png` |
| 7 | A leave is noticed, and only a real one | Join from your phone too, then leave on the phone: about 10-15 s later that name disappears from `people:`. Present your screen meanwhile: nobody must disappear | `timeline.jsonl`, `bot.log` |
| 8 | Screen share works | `s`: Meet shows the bot presenting the clock within ~10 s; `x` stops it | `bot.log`, `share-*.png`, `screen-*.png` |
| 9 | The bot's clicks still land on Meet while sharing | After `s`, press `x` and then `q`: both work | `screen-share-started-*.png` (shows what was on top) |
| 10 | Leaving | `q`: the bot leaves; `call_ended [bot_received_leave_call]`, then `done` | `events.jsonl` |
| 11 | Removed by you | Remove the bot from the call in Meet: `call_ended [bot_kicked_from_call]`, then `done` | `events.jsonl`, `bot.log` |
| 12 | Each person's picture | `pictures sent: <your name> N` grows by about 2 a second; `output/<run>/camera/<your name>.png` looks like your tile; no picture of the bot | `bot.log` (lines starting `pictures:`), the `camera/` folder |
| 13 | Who spoke when | `output/<run>/speakers.jsonl` has `started`/`stopped` lines with your name when you talk | `speakers.jsonl`, `bot.log` (lines starting `speakers:`) |
| 14 | Live view | The link shows the bot's screen (Meet) | - |

**If Meet refuses the bot** ("You can't join this video call", status `fatal [google_meet_bot_blocked]`):
the service mode changes a few Chrome settings compared with the proven join (no fake microphone or
camera, see `STATUS.md`). Check whether the proven join still works, unchanged:

1. Add this line to `.env`:

   ```bash
   echo 'BOT_ARGS=--join-only --type-name --knock-delay 15' >> .env
   ```

2. Restart the service and try again (the bot then only joins: no voice, no sound, no screen share):

   ```bash
   docker compose up -d --build && ./try.sh https://meet.google.com/abc-defg-hij
   ```

3. Remove that line from `.env` afterwards.

**Stop everything:**

```bash
docker compose down
```

**Remove bot containers that are still running** (normally there are none):

```bash
docker rm -f $(docker ps -aq --filter label=meeting-bot.id)
```

## 4. With xebit-api

1. In xebit-api's settings, change only two things:
   - the Recall base URL: `http://host.docker.internal:8090/api/v1`
   - the Recall API key: the `BOT_API_KEY` value in `.env`. Show it with:

     ```bash
     grep '^BOT_API_KEY=' .env
     ```

2. The addresses xebit gives the bot (its webhook URL and its page URL) must be reachable from Docker,
   for example `http://host.docker.internal:<xebit port>`. Note: xebit itself skips any that start with
   `http://localhost`.
3. Start the service (`docker compose up -d --build`, or `./try.sh` once), then start a demo in xebit.
4. Watch:
   - the manager: `docker compose logs -f manager` (every request and status change, URLs without tokens);
   - the run folder of the bot (as in section 3).
5. What should happen:
   - xebit's pre-launch check passes (it calls `GET /bot/?status=in_call_recording` with the key);
   - the bot joins (admit it), and its status goes `joining_call`, `in_waiting_room`,
     `in_call_not_recording`, `in_call_recording`;
   - the agent speaks with the camera page's voice, and answers when you talk (it hears the call through
     the `audio_separate_raw.data` stream: one stream named `meeting-mix`);
   - xebit's screen page shows as the bot's screen share when xebit starts it;
   - your join reaches xebit (the demo starts); when everyone leaves, xebit ends the session;
   - at the end the bot reports `call_ended` before its camera page closes, so xebit does not mark the
     session failed.

## 5. Results (fill in one row per run)

| Run | Date and time | Meet link | `try.sh` or xebit | Got in (yes / no) | Voice heard | Bot hears you | Screen share | People right | Pictures | Final status (`code` / `sub_code`) | Run folder | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | | | `try.sh` | | | | | | | | | |
| 2 | | | `try.sh` | | | | | | | | | |
| 3 | | | `try.sh --per-person-audio` | | | | | | | | | |
| 4 | | | xebit | | | | | | | | | |
| 5 | | | xebit | | | | | | | | | |
