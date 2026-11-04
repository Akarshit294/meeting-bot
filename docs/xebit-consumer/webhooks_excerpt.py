# COPIED FROM xebit-api api/routes/recall_webhooks.py (47f5189), lines 85-122 (bot self-filter) and 897-1021 (participant join/leave webhook).
# Reference only: this is the code that READS what our service sends. Do not edit; do not import.

_NOTETAKER_NAME_MARKERS: tuple[str, ...] = (
    "read.ai",
    "otter.ai",
    "fireflies.ai",
    "fathom",
    "tl;dv",
    "fellow.app",
    "avoma",
    "sembly",
    "notetaker",
    "meeting notes",
    "meeting bot",
    "notes by ",
)


def _is_bot_participant(participant: dict[str, Any]) -> bool:
    """Filter for Recall bot/bridge participants AND third-party notetaker bots.

    Uses stable identifiers + a curated list of specific notetaker product markers — never a
    broad name prefix, as real users may have names starting with the same prefix.
    """
    participant_id = participant.get("id")
    if participant_id == 2147483647 or str(participant_id) == "2147483647":
        return True
    if participant.get("is_bot") is True:
        return True

    name = str(participant.get("name") or "").strip().lower()
    # Only match the exact configured bot name (e.g. "Xebit AI")
    configured_bot_name = settings.recall_bot_name.strip().lower()
    if configured_bot_name and name == configured_bot_name:
        return True
    # Known third-party notetaker products (specific identifiers, substring match).
    if name and any(marker in name for marker in _NOTETAKER_NAME_MARKERS):
        return True
    return False


# ... (lines 123-896 omitted: the transcript and status webhooks; v1 sends neither) ...

@router.post("/call-events", dependencies=[Depends(rate_limit("webhook"))])
async def recall_call_events_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Receives real-time call events (participant join/leave) from Recall.ai."""
    body: dict[str, Any] = await request.json()
    logger.info("Recall call-event webhook received: %s", body)
    event_type = body.get("event", "")
    data = body.get("data", {})

    bot_data = data.get("bot", {})
    bot_id = bot_data.get("id", "") or data.get("bot_id", "")
    if not bot_id:
        return {"status": "ignored"}

    # Look up meeting + session for both join and leave events
    result = await db.execute(
        select(Meeting).where(Meeting.recall_bot_id == bot_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        return {"status": "ignored"}

    session_result = await db.execute(
        select(MeetingSession)
        .where(MeetingSession.meeting_id == meeting.id)
        .order_by(MeetingSession.created_at.desc())
        .limit(1)
    )
    session = session_result.scalar_one_or_none()
    if not session:
        return {"status": "ignored"}

    participant = data.get("data", {}).get("participant", {}) if "data" in data else data.get("participant", {})

    sid = str(session.id)
    # demo_v2 owns its own recording start/stop (relay control + teardown finalize); the old
    # bridge-command publishes below must NOT fire for it (dead no-op on the demo_v2 bridge +
    # misleading logs). Computed once, used by both the join and leave branches.
    from api.services.meeting_launcher import is_demo_v2_path
    is_v2 = is_demo_v2_path(getattr(meeting, "transport", None), session.meeting_type)

    if event_type == "participant_events.join":
        logger.info(
            "Recall participant join: bot_id=%s participant=%s",
            bot_id, participant,
        )
        if _is_bot_participant(participant):
            logger.info("Recall participant join ignored for bot participant: session=%s", session.id)
        else:
            prior_count = _human_participant_counts.get(sid, 0)
            _human_participant_counts[sid] = prior_count + 1
            logger.info("Human participant count: session=%s count=%d", sid, _human_participant_counts[sid])
            # OLD-runtime readiness waiter — demo_v2 has its own gate (mark_human_joined below),
            # so skip this for it (the last ungated old-runtime call on the demo_v2 path).
            if not is_v2:
                notify_participant_joined(sid)
            demo_v2_readiness.mark_human_joined(sid)  # demo_v2 readiness gate (self-contained)
            # demo_v2: feed the live router the human roster (headcount + names) so it can tell a
            # 1:1 from a group call. Observational context only — never speech-state/FSM.
            if is_v2:
                roster = _human_participant_names.setdefault(sid, {})
                roster[participant.get("id")] = str(participant.get("name") or "").strip()
                await demo_v2_participants.update_roster(sid, list(roster.values()))
            # demo_v2 recording: push the new headcount to the overlay (no-op unless recording on)
            await _publish_participant_count(sid, _human_participant_counts[sid])
            # Cancel any pending leave-exit timer (human came back)
            _cancel_leave_exit(sid)
            # Same for the recording-stop debounce — rejoin within the 30s
            # grace keeps the same recording running.
            _cancel_stop_recording(sid)
            # 0 → 1 transition: kick recording on (OLD runtime only). demo_v2 starts recording from
            # its relay at this same join edge, so skip the old bridge-command publish for it.
            # start_recording is idempotent on the bridge, so re-emits across reconnects are safe;
            # here we only fire it once per "meeting actually active" transition to avoid log noise.
            if prior_count == 0 and not is_v2:
                try:
                    await publish_start_recording(
                        sid, command_reason="first_participant_joined"
                    )
                except Exception as exc:
                    logger.warning(
                        "start_recording publish failed: session=%s error=%s", sid, exc,
                    )

    elif event_type == "participant_events.leave":
        participant_name = participant.get("name", "")
        logger.info(
            "Recall participant leave: bot_id=%s participant=%s session=%s",
            bot_id, participant_name, session.id,
        )
        if not _is_bot_participant(participant):
            _human_participant_counts[sid] = max(0, _human_participant_counts.get(sid, 1) - 1)
            logger.info("Human participant count: session=%s count=%d", sid, _human_participant_counts[sid])
            # demo_v2 recording: push the new headcount to the overlay (no-op unless recording on)
            await _publish_participant_count(sid, _human_participant_counts[sid])
            # demo_v2: drop the leaver from the router's roster (observational context only).
            if is_v2:
                roster = _human_participant_names.get(sid)
                if roster is not None:
                    roster.pop(participant.get("id"), None)
                    await demo_v2_participants.update_roster(sid, list(roster.values()))

            # Only start leave timer when ALL human participants have left
            non_terminal = (SessionStatus.launching, SessionStatus.joining, SessionStatus.waiting, SessionStatus.active)
            if (
                _human_participant_counts[sid] == 0
                and session.status in non_terminal
            ):
                # demo_v2 sessions must settle via demo_v2 lifecycle, NOT the old runtime's
                # graceful-exit cleanup (which the old `_delayed_graceful_exit` would run).
                _schedule_leave_exit(
                    session_id=sid,
                    meeting_id=str(meeting.id),
                    recall_bot_id=meeting.recall_bot_id,
                    is_demo_v2=is_v2,
                )
                # 1 → 0 transition: arm the OLD recording-stop debounce (OLD runtime only). For
                # demo_v2, all-humans-left funnels into the leave-exit → settle → relay teardown,
                # whose finalize net seals the recording, so the old debounce isn't needed.
                if not is_v2:
                    _schedule_stop_recording(sid)

    return {"status": "ok"}
