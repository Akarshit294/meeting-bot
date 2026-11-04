# COPIED FROM xebit-api api/services/demo_v2/transport/lifecycle.py (47f5189), lines 51-60 and 965-985: how bot status codes are read.
# Reference only: this is the code that READS what our service sends. Do not edit; do not import.

# Recall bot status codes meaning "in the call right now". On an unexpected WS drop this
# distinguishes a ZOMBIE bot (page dead, bot still squatting in the meeting) from a normal
# external end (host kicked the bot / call ended). Lives here (not bot_watch) so the
# disconnect-settler and the launch-side watcher share one definition; bot_watch re-imports it.
_JOINED_CODES = {"in_call_not_recording", "in_call_recording"}
# Recall bot status codes meaning "the bot is genuinely GONE" — the call ended or the bot died.
# On an unexpected WS drop these are a normal external end (-> `ended`, no leave). Anything that
# is NEITHER joined NOR gone (joining_call / in_waiting_room / ready / unknown) is indeterminate:
# the bot could still enter the call as a ghost, so we best-effort leave it before marking `ended`.
_GONE_CODES = {"call_ended", "done", "fatal"}

def _latest_recall_status_code(bot_payload: dict) -> str:
    """Latest Recall bot status code from a `get_bot` payload. Empty string if unknown.
    (Moved here from bot_watch — which re-imports it — so it and `settle_unexpected_disconnect`
    share one definition.)"""
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

