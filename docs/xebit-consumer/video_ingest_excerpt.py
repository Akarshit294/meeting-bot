# COPIED FROM xebit-api api/routes/recall_video_ws.py (47f5189), lines 1-14 and 81-168: how each person's camera
# pictures are received. Reference only: do not edit; do not import.

"""Real-time video relay for Recall participant webcams/screenshares.

Recall opens a WebSocket to ``/api/ws/recall/video/{session_id}?token=…`` and
pushes ``video_separate_png.data`` events at 2fps per participant. The token
is HMAC(session_id, settings.token_encryption_key); see
``make_recall_video_ws_token`` below.

The bridge page subscribes at ``/ws/sessions/{session_id}/participant-video``
(user_id + tenant_id query params, validated against meeting ownership) and
draws corner PIPs on its **hidden** recording canvas — the MediaRecorder
captures from that canvas, not from the visible bot-camera canvas, so PIPs
never appear on the bot's outbound camera feed. The bridge only opens this
WS while a recording is active.


# ... (lines 15-80 omitted: imports, the token helper, the Redis channel name) ...

@router.websocket("/api/ws/recall/video/{session_id}")
async def recall_video_ingest_ws(
    ws: WebSocket,
    session_id: str,
    token: str = Query(""),
):
    """Recall pushes ``video_separate_png.data`` here. We publish to Redis.

    Auth: HMAC token in the URL. Recall has no way to send headers, so the
    URL is the credential. Anyone with the (UUID session_id, signed token)
    pair can push frames — equivalent to compromising the API secret.
    """
    try:
        expected = make_recall_video_ws_token(session_id)
    except RuntimeError:
        # No signing key configured — reject unconditionally.
        await ws.close(code=4001, reason="auth_unavailable")
        return
    if not hmac.compare_digest(token, expected):
        await ws.close(code=4001, reason="invalid_token")
        return

    # Fail-fast on Redis: better to reject Recall's connection up front than
    # accept it and then drop every frame in a noisy log spam.
    redis = get_runtime_redis_client()
    try:
        await redis.ping()
    except Exception as exc:
        logger.warning(
            "Recall video ingest rejected — redis unavailable: session=%s err=%s",
            session_id, exc,
        )
        await ws.close(code=1011, reason="redis_unavailable")
        return

    await ws.accept()
    # Lazy: recall_webhooks imports this module at its top, so a top-level import is a cycle.
    from api.routes.recall_webhooks import _is_bot_participant

    channel = _channel(session_id)
    frames_received = 0
    frames_forwarded = 0
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if payload.get("event") != "video_separate_png.data":
                continue
            inner = payload.get("data", {}).get("data", {})
            participant = inner.get("participant") or {}
            name = (participant.get("name") or "").strip()
            # Drop the bot's own video — the canvas already shows the screencast. By Recall's bot
            # id, not by name: the bot joins under its agent's name, which a guest could share.
            if _is_bot_participant(participant):
                continue
            buffer_b64 = inner.get("buffer")
            if not buffer_b64:
                continue
            frames_received += 1
            try:
                await redis.publish(
                    channel,
                    json.dumps({
                        "participant_id": participant.get("id"),
                        "participant_name": name or None,
                        "type": inner.get("type") or "webcam",
                        "buffer": buffer_b64,
                    }),
                )
                frames_forwarded += 1
            except Exception as exc:
                logger.warning("Recall video publish failed session=%s err=%s", session_id, exc)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning(
            "Recall video ingest closed: session=%s err=%s received=%d forwarded=%d",
            session_id, exc, frames_received, frames_forwarded,
        )
    finally:
        try:
            await ws.close()
        except Exception:
            pass

