# COPIED FROM xebit-api api/routes/recall_audio_ws.py (47f5189), lines 55-142: the per-person audio socket token + frame decoder.
# Reference only: this is the code that READS what our service sends. Do not edit; do not import.

def make_recall_audio_ws_token(session_id: str) -> str:
    """Stable per-session token Recall embeds in the ingest URL.

    Same as make_recall_video_ws_token; reused for both audio and video endpoints.
    """
    secret = (settings.token_encryption_key or "").encode()
    if not secret:
        raise RuntimeError(
            "XEBIT_TOKEN_ENCRYPTION_KEY is required to sign Recall audio WS URLs"
        )
    return hmac.new(secret, session_id.encode(), hashlib.sha256).hexdigest()[:32]


class _DecodedFrame(NamedTuple):
    """One participant audio frame, after the transport-specific parsing (Task L2).

    Carries `is_bot` rather than having the decoder simply drop the agent's own frames, so the
    filtered-frame counter and the once-per-pid "Filtered bot track" log stay in the shared body
    — the decoder stays PURE and every piece of per-connection state keeps living in one place.
    """

    participant_id: Any
    participant_name: str
    pcm: bytes
    is_bot: bool
    # When the sender stamped this audio, as epoch seconds — Recall's `timestamp.absolute`, the
    # LiveKit bridge's `t`. Timing only: `now - captured_at` is how late the audio reached us
    # (including any backlog in this socket). None when the sender sent no stamp.
    captured_at: float | None = None


def _epoch_from_iso(value: Any) -> float | None:
    """An ISO 8601 time (Recall's `timestamp.absolute`) as epoch seconds, or None. A time with no
    zone is read as UTC, which is what Recall sends."""
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
    """Recall's `audio_separate_raw.data` envelope -> a frame, or None to skip this message.

    Task L2: this is the ONLY Recall-specific step in the ingest path. Everything after it —
    VAD pipelines, the turn coordinator, the pause-judge, the noise ladder — is transport-blind,
    which is what lets LiveKit reuse the whole body behind its own decoder.
    """
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

    # Decode PCM (base64 → bytes)
    try:
        pcm_bytes = base64.b64decode(buffer_b64)
    except Exception as exc:
        logger.warning(
            "Audio frame decode failed: session=%s participant_id=%s err=%s",
            session_id, participant_id, exc
        )
        return None

    stamp = inner.get("timestamp")
    return _DecodedFrame(
        participant_id, participant_name, pcm_bytes, _is_bot_participant(participant),
        _epoch_from_iso(stamp.get("absolute")) if isinstance(stamp, dict) else None,
    )

