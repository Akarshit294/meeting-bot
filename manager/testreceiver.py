"""A tiny receiver for real-Meet tests WITHOUT xebit (`./try.sh`). Not used by xebit-api.

The bot sends here what it would send to xebit (joins/leaves, the call's sound, camera pictures), and the
test voice page reports what it hears. `./try.sh` prints the counts from `GET /testrx/stats` (API key
needed). The receiving endpoints only count; they keep at most a few small things per bot in memory.
"""
from __future__ import annotations

import array
import base64
import json
import math
import time
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

MAX_KEYS = 50


class Bounded(OrderedDict):
    def get_or_new(self, key: str, factory):
        if key not in self:
            self[key] = factory()
            while len(self) > MAX_KEYS:
                self.popitem(last=False)
        return self[key]


def new_bot() -> dict[str, Any]:
    return {"joins": [], "leaves": [], "audio_frames": 0, "audio_db": None, "audio_participants": {},
            "pictures": {}, "first_audio": None, "last_audio": None}


def level_db(pcm: bytes) -> float | None:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if not samples:
        return None
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768
    return round(20 * math.log10(rms + 1e-9), 1)


def router(require_key, run_dir_of, media_files_of=lambda bot_id: None) -> APIRouter:
    bots: Bounded = Bounded()
    voices: Bounded = Bounded()
    r = APIRouter(prefix="/testrx", include_in_schema=False)

    def bot_of(payload: dict) -> dict:
        bot_id = str(payload.get("data", {}).get("bot", {}).get("id") or "?")
        return bots.get_or_new(bot_id, new_bot)

    @r.post("/hooks")
    async def hooks(request: Request):
        body = await request.json()
        participant = body.get("data", {}).get("data", {}).get("participant", {})
        entry = {"id": participant.get("id"), "name": participant.get("name"), "t": time.time()}
        stats = bot_of(body)
        if body.get("event") == "participant_events.join":
            stats["joins"].append(entry)
        elif body.get("event") == "participant_events.leave":
            stats["leaves"].append(entry)
        return {"status": "ok"}

    @r.websocket("/audio")
    async def audio(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                payload = json.loads(await ws.receive_text())
                stats = bot_of(payload)
                inner = payload.get("data", {}).get("data", {})
                stats["audio_frames"] += 1
                who = (inner.get("participant") or {}).get("name") or "?"
                stats["audio_participants"][who] = stats["audio_participants"].get(who, 0) + 1
                now = time.time()
                stats["first_audio"] = stats["first_audio"] or now
                stats["last_audio"] = now
                if stats["audio_frames"] % 5 == 0:
                    stats["audio_db"] = level_db(base64.b64decode(inner.get("buffer") or ""))
        except (WebSocketDisconnect, ValueError):
            pass

    @r.websocket("/video")
    async def video(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                payload = json.loads(await ws.receive_text())
                participant = payload.get("data", {}).get("data", {}).get("participant") or {}
                name = participant.get("name") or str(participant.get("id"))
                pictures = bot_of(payload)["pictures"]
                pictures[name] = pictures.get(name, 0) + 1
        except (WebSocketDisconnect, ValueError):
            pass

    @r.post("/voice")
    async def voice(request: Request, tag: str = "-"):
        voices.get_or_new(tag, dict).update(await request.json(), t=time.time())
        return {"ok": True}

    @r.get("/stats")
    def stats(bot: str, request: Request, tag: str = "-"):
        require_key(request.headers.get("authorization"))
        data = dict(bots.get(bot) or new_bot())
        data["run_dir"] = run_dir_of(bot)
        present: dict = {}
        for event in sorted([("join", e) for e in data["joins"]] + [("leave", e) for e in data["leaves"]], key=lambda x: x[1]["t"]):
            if event[0] == "join":
                present[event[1]["id"]] = event[1]["name"]
            else:
                present.pop(event[1]["id"], None)
        data["present"] = list(present.values())
        data["voice"] = voices.get(tag)
        return data

    @r.get("/summary", response_class=PlainTextResponse)
    def summary(bot: str, request: Request, tag: str = "-"):
        """One line for ./try.sh (plain bash can't read JSON)."""
        d = stats(bot, request, tag)
        people = ", ".join(d["present"]) or "nobody yet"
        level = d["audio_db"]
        sound = f"{d['audio_frames']} frames" + ("" if level is None else ", silent" if level < -90 else f", last {level} dB")
        if len(d["audio_participants"]) > 1 or (d["audio_participants"] and "Meeting" not in d["audio_participants"]):
            sound += " (" + ", ".join(f"{k} {v}" for k, v in sorted(d["audio_participants"].items())) + ")"
        voice = d["voice"]
        heard = f"{voice.get('heard_db')} dB" if voice and voice.get("mic") else "nothing yet"
        pictures = ", ".join(f"{k} {v}" for k, v in sorted(d["pictures"].items())) or "none"
        return (f"people: {people} | sound sent: {sound} | voice page hears the call: {heard} | "
                f"pictures sent: {pictures}")

    @r.get("/files", response_class=PlainTextResponse)
    def files(bot: str, request: Request):
        """The recordings the bot saved, with their lengths (for ./try.sh at the end of a run)."""
        require_key(request.headers.get("authorization"))
        return media_files_of(bot) or ""

    @r.get("/run-dir", response_class=PlainTextResponse)
    def run_dir(bot: str, request: Request):
        require_key(request.headers.get("authorization"))
        return run_dir_of(bot) or ""

    return r
