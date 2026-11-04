# COPIED FROM xebit-api api/services/launch_glue.py (47f5189), build_output_media_url: the camera page URL we receive in output_media.camera.
# Reference only: this is the code that READS what our service sends. Do not edit; do not import.

def build_output_media_url(
    session: MeetingSession,
    user_id: str | None,
    tenant_id: str | None,
    realtime_provider: str = "openai",
    is_unidirectional: bool = False,
    listen_mode: str = "off",
    auto_start: bool = True,
    conversational: bool = False,
    recording_quality: str = "low",
    agent_name: str = "",
) -> str | None:
    # Recall output-media bridge page by meeting type. Demo uses the demo_v2 runtime bridge.
    # Recall training can opt into demo_v2 behind demo_v2_training; otherwise it stays on the
    # old realtime bridge picked by provider. LiveKit does not use Recall output media.
    if session.meeting_type == "demo" or (session.meeting_type == "training" and settings.demo_v2_training):
        bridge_page = "demo_v2-bridge.html"
    elif realtime_provider == "gemini":
        bridge_page = "realtime-bridge-vertex.html"
    else:
        bridge_page = "realtime-bridge.html"
    query = {
        "session_id": str(session.id),
        "api_url": settings.platform_url,
        "meeting_type": session.meeting_type,
        # The bridge's hidden recording canvas connects to the screencast WS,
        # which now requires a signed viewer token. NOTE: this URL carries a
        # credential — never log it in full.
        "token": issue_viewer_token(str(session.id)),
    }
    if user_id:
        query["user_id"] = user_id
    if tenant_id:
        query["tenant_id"] = tenant_id
    if is_unidirectional:
        query["force_mute"] = "1"
    # WW-C.3 Surface D: the demo_v2 bridge picks its holding copy from these (wake vs normal, + the
    # wake-gated START line). Only the demo_v2 bridge reads them — and the recording quality.
    if bridge_page == "demo_v2-bridge.html":
        query.update(RECORDING_QUALITY_PARAMS.get(recording_quality, {}))
        query["listen_mode"] = listen_mode
        query["auto_start"] = "1" if auto_start else "0"
        # Conversational (KB-only Q&A) has no demo to "start", so the bridge shows chat-oriented
        # holding copy instead of demo copy.
        if conversational:
            query["conversational"] = "1"
        # The holding screen and the recording's agent tile draw its initial; absent = "X".
        if agent_name:
            query["agent_name"] = agent_name
    return f"{settings.platform_url}/static/{bridge_page}?{urlencode(query)}"



# --- api/services/demo_v2/transport/screenshare.py: the screen page URL we receive in POST output_media ---
def build_viewer_url(session_id: str) -> str:
    """The screencast-viewer page URL Recall renders as the screen share. Carries a signed,
    short-lived token (the URL is a credential — never log it in full)."""
    query = {
        "session_id": session_id,
        "api_url": settings.platform_url,
        "token": issue_viewer_token(session_id),
    }
    return f"{settings.platform_url}/static/screencast-viewer.html?{urlencode(query)}"


