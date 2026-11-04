"""Copies of the xebit-api code that reads what we send (docs/xebit-consumer/). Tests check our output
with these, so a mismatch with xebit shows up as a failing test. Keep them identical to the originals.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, NamedTuple

# ---- docs/xebit-consumer/status_excerpt.py ----

_JOINED_CODES = {"in_call_not_recording", "in_call_recording"}
_GONE_CODES = {"call_ended", "done", "fatal"}


def _latest_recall_status_code(bot_payload: dict) -> str:
    status_changes = bot_payload.get("status_changes")
    if isinstance(status_changes, list) and status_changes:
        latest = status_changes[-1]
        if isinstance(latest, dict):
            code = latest.get("code")
            if isinstance(code, str):
                return code
    status = bot_payload.get("status")
    if isinstance(status, dict):
        code = status.get("code")
        if isinstance(code, str):
            return code
    code = bot_payload.get("status_code")
    if isinstance(code, str):
        return code
    return ""


# ---- docs/xebit-consumer/preflight_excerpt.py ----

def _result_count(resp) -> int | None:
    try:
        results = resp.json().get("results")
    except Exception:
        return None
    return len(results) if isinstance(results, list) else None


# ---- docs/xebit-consumer/recall_client.py: create_bot()'s request body, built the same way ----

def create_bot_body(
    meeting_url: str,
    bot_name: str = "Xebit AI",
    join_at: str | None = None,
    webhook_url: str | None = None,
    demo_script_id: str | None = None,
    output_media_url: str | None = None,
    session_id: str | None = None,
    video_token: str = "v" * 32,
    audio_token: str = "a" * 32,
    demo_v2_separate_audio: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "recording_config": {
            "video_mixed_mp4": None,
            "include_bot_in_recording": {"audio": True},
            "transcript": {
                "provider": {"recallai_streaming": {"mode": "prioritize_low_latency", "language_code": "en"}},
                "diarization": {"use_separate_streams_when_available": True},
            },
        },
    }
    if join_at:
        body["join_at"] = join_at
    if webhook_url and not webhook_url.startswith("http://localhost"):
        endpoints: list[dict[str, Any]] = [
            {"type": "webhook", "url": f"{webhook_url}/api/webhooks/recall/transcript",
             "events": ["transcript.data", "transcript.partial_data"]},
            {"type": "webhook", "url": f"{webhook_url}/api/webhooks/recall/call-events",
             "events": ["participant_events.join", "participant_events.leave"]},
        ]
        if session_id:
            ws_base = webhook_url.replace("https://", "wss://").replace("http://", "ws://")
            endpoints.append({"type": "websocket", "url": f"{ws_base}/api/ws/recall/video/{session_id}?token={video_token}",
                              "events": ["video_separate_png.data"]})
            body["recording_config"]["video_separate_png"] = {}
            body["recording_config"]["video_mixed_layout"] = "gallery_view_v2"
            if demo_v2_separate_audio:
                endpoints.append({"type": "websocket", "url": f"{ws_base}/api/ws/recall/audio/{session_id}?token={audio_token}",
                                  "events": ["audio_separate_raw.data"]})
                body["recording_config"]["audio_separate_raw"] = {}
        body["recording_config"]["realtime_endpoints"] = endpoints
    if demo_script_id:
        body["metadata"] = {"demo_script_id": demo_script_id}
    if output_media_url and not output_media_url.startswith("http://localhost"):
        body["output_media"] = {"camera": {"kind": "webpage", "config": {"url": output_media_url}}}
        body["variant"] = {"google_meet": "web_4_core", "zoom": "web_4_core", "microsoft_teams": "web_4_core"}
    return body


def start_screenshare_body(url: str) -> dict:
    return {"screenshare": {"kind": "webpage", "config": {"url": url}}}


STOP_SCREENSHARE_BODY = {"screenshare": True}


# ---- docs/xebit-consumer/webhooks_excerpt.py: the bot self-filter and the join/leave reader ----

_NOTETAKER_NAME_MARKERS = ("read.ai", "otter.ai", "fireflies.ai", "fathom", "tl;dv", "fellow.app", "avoma", "sembly",
                           "notetaker", "meeting notes", "meeting bot", "notes by ")


def _is_bot_participant(participant: dict[str, Any], configured_bot_name: str = "") -> bool:
    participant_id = participant.get("id")
    if participant_id == 2147483647 or str(participant_id) == "2147483647":
        return True
    if participant.get("is_bot") is True:
        return True
    name = str(participant.get("name") or "").strip().lower()
    configured = configured_bot_name.strip().lower()
    if configured and name == configured:
        return True
    if name and any(marker in name for marker in _NOTETAKER_NAME_MARKERS):
        return True
    return False


class HeadCount:
    """What xebit-api keeps per session from the call-events webhook (webhooks_excerpt.py)."""

    def __init__(self, configured_bot_name: str = ""):
        self.count = 0
        self.roster: dict[Any, str] = {}
        self.configured = configured_bot_name

    def feed(self, body: dict) -> None:
        event_type = body.get("event", "")
        data = body.get("data", {})
        bot_data = data.get("bot", {})
        bot_id = bot_data.get("id", "") or data.get("bot_id", "")
        assert bot_id, "no bot id: xebit ignores the event"
        participant = data.get("data", {}).get("participant", {}) if "data" in data else data.get("participant", {})
        if event_type == "participant_events.join":
            if not _is_bot_participant(participant, self.configured):
                self.count += 1
                self.roster[participant.get("id")] = str(participant.get("name") or "").strip()
        elif event_type == "participant_events.leave":
            if not _is_bot_participant(participant, self.configured):
                self.count = max(0, self.count - 1)
                self.roster.pop(participant.get("id"), None)


# ---- docs/xebit-consumer/audio_ingest_excerpt.py: the audio frame decoder ----

class _DecodedFrame(NamedTuple):
    participant_id: Any
    participant_name: str
    pcm: bytes
    is_bot: bool
    captured_at: float | None = None


def _epoch_from_iso(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _decode_recall(raw: str, session_id: str) -> "_DecodedFrame | None":
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if payload.get("event") != "audio_separate_raw.data":
        return None
    inner = payload.get("data", {}).get("data", {})
    participant = inner.get("participant") or {}
    participant_id = participant.get("id")
    participant_name = (participant.get("name") or "").strip()
    if not participant_id:
        return None
    buffer_b64 = inner.get("buffer")
    if not buffer_b64:
        return None
    try:
        pcm_bytes = base64.b64decode(buffer_b64)
    except Exception:
        return None
    stamp = inner.get("timestamp")
    return _DecodedFrame(
        participant_id, participant_name, pcm_bytes, _is_bot_participant(participant),
        _epoch_from_iso(stamp.get("absolute")) if isinstance(stamp, dict) else None,
    )


# ---- docs/xebit-consumer/video_ingest_excerpt.py: what the video socket keeps from a frame ----

def decode_video(raw: str) -> dict | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if payload.get("event") != "video_separate_png.data":
        return None
    inner = payload.get("data", {}).get("data", {})
    participant = inner.get("participant") or {}
    name = (participant.get("name") or "").strip()
    if _is_bot_participant(participant):
        return None
    buffer_b64 = inner.get("buffer")
    if not buffer_b64:
        return None
    return {"participant_id": participant.get("id"), "participant_name": name or None,
            "type": inner.get("type") or "webcam", "buffer": buffer_b64}
