#!/usr/bin/env bash
# The owner's one command: test the bot in a real Google Meet call, without xebit.
#
#   ./try.sh <meet link> [bot name]
#   ./try.sh --per-person-audio <meet link> [bot name]     (experimental: one sound stream per person)
#
# It starts the service if needed, sends a bot to the call with the test pages (a chime as its voice,
# a big clock as its screen share), prints the live-view link and every status, and waits for keys:
#   s = start the screen share   x = stop it   l = leave   q = quit (leaves first)
set -u
cd "$(dirname "$0")"

usage() {
  echo "Usage: ./try.sh [--per-person-audio] [--no-camera-pictures] <https://meet.google.com/abc-defg-hij> [bot name]"
  exit 2
}

PER_PERSON=false
PICTURES=true
while [ $# -gt 0 ]; do
  case "$1" in
    --per-person-audio) PER_PERSON=true; shift ;;
    --no-camera-pictures) PICTURES=false; shift ;;
    -h|--help) usage ;;
    -*) echo "Unknown option: $1"; usage ;;
    *) break ;;
  esac
done
LINK="${1:-}"
NAME="${2:-Maya}"
[ -n "$LINK" ] || usage
case "$LINK" in
  https://meet.google.com/*) ;;
  *) echo "That is not a Google Meet link (it must start with https://meet.google.com/)."; exit 2 ;;
esac
NAME=$(printf '%s' "$NAME" | tr -d '"\\')

# 1. The API key, in .env (made on the first run).
if [ ! -f .env ] || ! grep -q '^BOT_API_KEY=.' .env; then
  NEWKEY=$(openssl rand -hex 24 2>/dev/null || LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 48)
  echo "BOT_API_KEY=$NEWKEY" >> .env
  echo "Made .env with a new API key (BOT_API_KEY). Give the same key to xebit-api later."
fi
KEY=$(grep '^BOT_API_KEY=' .env | tail -1 | cut -d= -f2-)
PORT=$(grep '^MANAGER_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2-)
API="http://localhost:${PORT:-8090}"
AUTH="Authorization: Token $KEY"

# 2. The service.
if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop, wait until it says it is running, then run this again."
  exit 1
fi
echo "Starting the service (docker compose up -d --build). The first time takes about 5-10 minutes."
if ! docker compose up -d --build; then
  echo "The service did not build or start. Send the output above."
  exit 1
fi
for _ in $(seq 60); do curl -fsS "$API/health" >/dev/null 2>&1 && break; sleep 1; done
if ! curl -fsS "$API/health" >/dev/null 2>&1; then
  echo "The service is not answering on $API. Send the output of: docker compose logs manager"
  exit 1
fi

# 3. The bot. The bot containers reach the manager as http://manager:8090.
TAG=$(LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 12)
RX="http://manager:8090/testrx"
WS="ws://manager:8090/testrx"
CAMERA="http://manager:8090/test/voice.html?report=http%3A%2F%2Fmanager%3A8090%2Ftestrx%2Fvoice%3Ftag%3D$TAG"
SCREEN="http://manager:8090/test/screen.html"
BODY=$(cat <<JSON
{"meeting_url": "$LINK", "bot_name": "$NAME",
 "output_media": {"camera": {"kind": "webpage", "config": {"url": "$CAMERA"}}},
 "recording_config": {"realtime_endpoints": [
   {"type": "webhook", "url": "$RX/hooks", "events": ["participant_events.join", "participant_events.leave"]},
   {"type": "websocket", "url": "$WS/audio", "events": ["audio_separate_raw.data"]},
   {"type": "websocket", "url": "$WS/video", "events": ["video_separate_png.data"]}]},
 "xbot_options": {"per_person_audio": $PER_PERSON, "camera_pictures": $PICTURES, "save_dom": true}}
JSON
)
RESP=$(curl -sS -X POST "$API/api/v1/bot/" -H "$AUTH" -H "Content-Type: application/json" -d "$BODY")
ID=$(printf '%s' "$RESP" | sed -n 's/.*"id":"\(xbot_[0-9a-f]*\)".*/\1/p')
if [ -z "$ID" ]; then
  echo "Could not create the bot. The service said: $RESP"
  exit 1
fi
LIVE=$(printf '%s' "$RESP" | sed -n 's/.*"live_view_url":"\([^"]*\)".*/\1/p')
echo
echo "Bot $ID is on its way to $LINK as \"$NAME\"."
echo "Live view (watch the bot's screen): ${LIVE:-not available}"
echo "Keys: s = share screen, x = stop sharing, l = leave, q = quit (leaves first)"
echo

leave() {
  curl -sS -o /dev/null -X POST "$API/api/v1/bot/$ID/leave_call/" -H "$AUTH"
}
trap 'echo; echo "Leaving..."; leave; exit 130' INT

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

SEEN=0
LAST=""
QUIT=0
TICK=0
while true; do
  BOT=$(curl -sS "$API/api/v1/bot/$ID/" -H "$AUTH")
  CHANGES=$(printf '%s' "$BOT" | grep -o '"code":"[a-z_]*","sub_code":[^,]*')
  COUNT=$(printf '%s\n' "$CHANGES" | grep -c '"code"')
  if [ "$COUNT" -gt "$SEEN" ]; then
    printf '%s\n' "$CHANGES" | tail -n +"$((SEEN + 1))" | while read -r line; do
      code=$(printf '%s' "$line" | sed 's/"code":"\([a-z_]*\)".*/\1/')
      sub=$(printf '%s' "$line" | sed 's/.*"sub_code":"\{0,1\}\([a-z_]*\)"\{0,1\}.*/\1/')
      [ "$sub" = "null" ] && sub=""
      case "$code" in
        joining_call) hint="the bot is opening Meet (about 40 s to the join request)" ;;
        in_waiting_room) hint="ADMIT \"$NAME\" in your Meet tab now" ;;
        in_call_not_recording) hint="the bot is in the call" ;;
        in_call_recording) hint="sound and voice are on: you should hear a chime every 4 s. Press s to share the screen." ;;
        call_ended) hint="the bot left the call" ;;
        done) hint="finished" ;;
        fatal) hint="the bot failed" ;;
        *) hint="" ;;
      esac
      say "STATUS $code${sub:+ [$sub]}${hint:+ - $hint}"
    done
    SEEN=$COUNT
    LAST=$(printf '%s\n' "$CHANGES" | tail -1 | sed 's/"code":"\([a-z_]*\)".*/\1/')
  fi
  case "$LAST" in
    in_call_*)
      TICK=$((TICK + 1))
      if [ $((TICK % 5)) -eq 1 ]; then
        say "$(curl -sS "$API/testrx/summary?bot=$ID&tag=$TAG" -H "$AUTH")"
      fi ;;
    done|fatal)
      echo
      END=$(printf '%s' "$BOT" | grep -o '"code":"[a-z_]*","sub_code":[^,]*,"message":"[^"]*"' | grep -v '"code":"done"' | tail -1 \
            | sed 's/"code":"\([a-z_]*\)","sub_code":"\{0,1\}\([a-z_]*\)"\{0,1\},"message":"\(.*\)"/\1 [\2] \3/; s/ \[null\]//')
      say "Result: $END"
      say "$(curl -sS "$API/testrx/summary?bot=$ID&tag=$TAG" -H "$AUTH")"
      DIR=$(curl -sS "$API/testrx/run-dir?bot=$ID" -H "$AUTH")
      [ -n "$DIR" ] && say "Run folder (screenshots, bot.log, events.jsonl, camera pictures): $DIR"
      exit 0 ;;
  esac
  [ "$QUIT" = 1 ] && { sleep 2; continue; }
  KEYPRESS=""
  read -rsn1 -t 2 KEYPRESS
  [ $? -eq 1 ] && sleep 2  # no keyboard (input closed): just keep watching
  case "$KEYPRESS" in
    s|S)
      say "Starting the screen share..."
      out=$(curl -sS -w ' (HTTP %{http_code})' -X POST "$API/api/v1/bot/$ID/output_media/" -H "$AUTH" \
            -H "Content-Type: application/json" -d "{\"screenshare\": {\"kind\": \"webpage\", \"config\": {\"url\": \"$SCREEN\"}}}")
      case "$out" in *"HTTP 200"*) say "Screen share is on: the call should show a big clock." ;; *) say "Screen share failed: $out" ;; esac ;;
    x|X)
      curl -sS -o /dev/null -X DELETE "$API/api/v1/bot/$ID/output_media/" -H "$AUTH" -H "Content-Type: application/json" \
        -d '{"screenshare": true}'
      say "Screen share stopped." ;;
    l|L)
      say "Asking the bot to leave..."; leave ;;
    q|Q)
      say "Leaving, then quitting..."; leave; QUIT=1 ;;
  esac
done
