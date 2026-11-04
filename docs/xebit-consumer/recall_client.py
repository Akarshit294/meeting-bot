# COPIED FROM xebit-api api/services/recall.py (47f5189), the whole Recall client: every request xebit sends us.
# Reference only: this is the code that READS what our service sends. Do not edit; do not import.

"""Recall.ai integration — bot management, calendar, and transcription."""

import logging
from typing import Any

import httpx

from api.config import settings
from api.services.log_redact import safe_log_body

logger = logging.getLogger(__name__)

RECALL_BASE = f"https://{settings.recall_region}.recall.ai/api/v1"
RECALL_HEADERS = {
    "Authorization": f"Token {settings.recall_api_key}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    url = f"{RECALL_BASE}{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(method, url, headers=RECALL_HEADERS, **kwargs)
        if resp.status_code >= 400:
            logger.error("Recall API error %d: %s %s -> %s", resp.status_code, method, path, safe_log_body(resp.text))
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()


# ─── Bot Management ───

async def create_bot(
    meeting_url: str,
    bot_name: str = "Xebit AI",
    join_at: str | None = None,
    webhook_url: str | None = None,
    demo_script_id: str | None = None,
    output_media_url: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Create a Recall.ai bot to join a meeting.

    If join_at is >10min in the future, it's a scheduled bot (guaranteed on-time).
    Otherwise it's an ad-hoc bot (joins from warm pool).
    """
    body: dict[str, Any] = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "recording_config": {
            # Real-time transcription is gated by `recording_config.transcript`
            # + `realtime_endpoints`, so we MUST keep those. But Recall enables
            # video_mixed_mp4 by default for every bot with a recording_config,
            # and we don't use the recording — explicitly disable it with null.
            # audio_mixed_mp3 / audio_mixed_raw are off by default, so omitting
            # them is enough to suppress audio artifacts.
            "video_mixed_mp4": None,
            "include_bot_in_recording": {
                "audio": True
            },
            "transcript": {
                "provider": {
                    "recallai_streaming": {
                        "mode": "prioritize_low_latency",
                        "language_code": "en"
                    }
                },
                "diarization": {"use_separate_streams_when_available": True},
            },
        },
    }

    if join_at:
        body["join_at"] = join_at

    if webhook_url and not webhook_url.startswith("http://localhost"):
        endpoints: list[dict[str, Any]] = [
            {
                "type": "webhook",
                "url": f"{webhook_url}/api/webhooks/recall/transcript",
                "events": ["transcript.data", "transcript.partial_data"],
            },
            {
                "type": "webhook",
                "url": f"{webhook_url}/api/webhooks/recall/call-events",
                "events": ["participant_events.join", "participant_events.leave"],
            },
        ]
        if session_id:
            ws_base = webhook_url.replace("https://", "wss://").replace("http://", "ws://")
            try:
                from api.routes.recall_video_ws import make_recall_video_ws_token
                token = make_recall_video_ws_token(session_id)
                endpoints.append({
                    "type": "websocket",
                    "url": f"{ws_base}/api/ws/recall/video/{session_id}?token={token}",
                    "events": ["video_separate_png.data"],
                })
                # Recall rejects video_separate_png.data subscriptions unless the
                # artifact is enabled AND video_mixed_layout is gallery_view_v2.
                body["recording_config"]["video_separate_png"] = {}
                body["recording_config"]["video_mixed_layout"] = "gallery_view_v2"
            except RuntimeError as exc:
                logger.warning(
                    "Skipping Recall video WS endpoint: %s (set XEBIT_TOKEN_ENCRYPTION_KEY)",
                    exc,
                )
            # Per-participant audio (noise epic): one raw PCM stream per speaker, which the
            # demo_v2 per-track route turns into the router's ears (identity + per-speaker VAD)
            # instead of the single mixed feed. Its OWN try/except, so an audio-token failure
            # can't drop the video endpoint (or vice versa).
            if settings.demo_v2_separate_audio:
                try:
                    from api.routes.recall_audio_ws import make_recall_audio_ws_token
                    audio_token = make_recall_audio_ws_token(session_id)
                    endpoints.append({
                        "type": "websocket",
                        "url": f"{ws_base}/api/ws/recall/audio/{session_id}?token={audio_token}",
                        "events": ["audio_separate_raw.data"],
                    })
                    # Recall only streams audio_separate_raw.data if the artifact is enabled.
                    body["recording_config"]["audio_separate_raw"] = {}
                except RuntimeError as exc:
                    logger.warning(
                        "Skipping Recall audio WS endpoint: %s (set XEBIT_TOKEN_ENCRYPTION_KEY)",
                        exc,
                    )
        body["recording_config"]["realtime_endpoints"] = endpoints
        # Note: status_changes_webhook_url is NOT supported per-bot by Recall.
        # Bot status (call_ended, fatal) is detected by polling GET /bot/{id}/
        # during the leave-exit grace period instead.
    elif webhook_url:
        logger.info("Skipping webhook URL (localhost not supported by Recall.ai)")

    if demo_script_id:
        body["metadata"] = {"demo_script_id": demo_script_id}

    if output_media_url and not output_media_url.startswith("http://localhost"):
        body["output_media"] = {
            "camera": {
                "kind": "webpage", 
                "config": {"url": output_media_url}
            }
        }
        # Use web_4_core variant for better audio/video processing (prevents crackling)
        body["variant"] = {
            "google_meet": "web_4_core",
            "zoom": "web_4_core",
            "microsoft_teams": "web_4_core",
        }
        # output_media_url carries a signed viewer token — never log it in full.
        logger.info("Configured output_media bridge (web_4_core)")
    elif output_media_url:
        logger.info("Skipping output_media (localhost not supported by Recall.ai)")

    return await _request("POST", "/bot/", json=body)


async def get_bot(bot_id: str) -> dict[str, Any]:
    return await _request("GET", f"/bot/{bot_id}/")


async def list_bots(
    join_at_after: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if join_at_after:
        params["join_at_after"] = join_at_after
    return await _request("GET", "/bot/", params=params)


async def update_bot(bot_id: str, **updates: Any) -> dict[str, Any]:
    return await _request("PATCH", f"/bot/{bot_id}/", json=updates)


async def remove_bot_from_call(bot_id: str) -> dict[str, Any]:
    return await _request("POST", f"/bot/{bot_id}/leave_call/")


async def delete_bot(bot_id: str) -> dict[str, Any]:
    return await _request("DELETE", f"/bot/{bot_id}/")


async def start_screenshare(bot_id: str, url: str) -> dict[str, Any]:
    """Start a live webpage screen share in the meeting."""
    body = {"screenshare": {"kind": "webpage", "config": {"url": url}}}
    logger.info("Starting screenshare for bot %s: %s", bot_id, url)
    return await _request("POST", f"/bot/{bot_id}/output_media/", json=body)


async def stop_screenshare(bot_id: str) -> dict[str, Any]:
    """Stop the live webpage screen share."""
    return await _request(
        "DELETE",
        f"/bot/{bot_id}/output_media/",
        json={"screenshare": True},
    )


# ─── Calendar (V2) ───
#
# Recall's calendar integration was never wired up: calendar sync goes through
# Google directly (services/calendar_sync.py). Task 0 deleted `create_calendar_user`
# along with `settings.recall_calendar_user_id`, the only thing that referenced this
# corner — and that setting was itself written only by the legacy calendar
# disconnect and read nowhere. `list_calendar_events` below has no callers either;
# it is left in place because Task 0's scope is the credential wipe, not a sweep of
# unused Recall helpers.

async def list_calendar_events(
    calendar_user_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"calendar_user_id": calendar_user_id}
    if start_time:
        params["start_time__gte"] = start_time
    if end_time:
        params["start_time__lte"] = end_time
    return await _request("GET", "/calendar/event/", params=params)


async def get_calendar_event(event_id: str) -> dict[str, Any]:
    return await _request("GET", f"/calendar/event/{event_id}/")


# ─── Transcript Retrieval (post-meeting) ───

async def get_bot_transcript(bot_id: str) -> list[dict[str, Any]]:
    """Get the full transcript from a completed bot recording."""
    bot = await get_bot(bot_id)
    recordings = bot.get("recordings", [])
    if not recordings:
        return []

    media = recordings[0].get("media_shortcuts", {})
    transcript_data = media.get("transcript", {}).get("data", {})
    download_url = transcript_data.get("download_url")

    if not download_url:
        return []

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(download_url)
        resp.raise_for_status()
        return resp.json()
