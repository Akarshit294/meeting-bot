"""The test kit (docs/PLAN.md task 5), one small server on port 9000:

- the FAKE MEETING page at /meet/<code> (meet.html), and what it measures (POST/GET /api/state);
- commands for the fake meeting (POST /api/command, polled by the page at GET /api/commands);
- the TEST RECEIVER: webhooks at /hooks/<anything>, websockets at /ws/audio and /ws/video;
- the test camera page (/camera.html, reports to /api/camera-state) and screen page (/screen.html).
"""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).parent
app = FastAPI(title="meeting-bot test kit")


class Store:
    def __init__(self):
        self.reset()

    def reset(self):
        self.state: dict = {}
        self.camera: dict = {}
        self.commands: list[dict] = []
        self.hooks: list[dict] = []
        self.audio: deque = deque(maxlen=1500)
        self.audio_count = 0
        self.audio_connections = 0
        self.video: dict = defaultdict(lambda: deque(maxlen=4))
        self.video_count: dict = defaultdict(int)
        self.video_connections = 0


S = Store()


@app.get("/meet/{code}")
def meet(code: str):
    return FileResponse(HERE / "meet.html", headers={"Cache-Control": "no-store"})


@app.get("/camera.html")
def camera_page():
    return FileResponse(HERE / "camera.html", headers={"Cache-Control": "no-store"})


@app.get("/screen.html")
def screen_page():
    return FileResponse(HERE / "screen.html", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/state")
async def post_state(request: Request):
    S.state = await request.json()
    S.state["received"] = time.time()
    return {"ok": True}


@app.get("/api/state")
def get_state():
    return S.state


@app.post("/api/camera-state")
async def post_camera(request: Request):
    S.camera = await request.json()
    S.camera["received"] = time.time()
    return {"ok": True}


@app.get("/api/camera-state")
def get_camera():
    return S.camera


@app.post("/api/command")
async def post_command(request: Request):
    S.commands.append(await request.json())
    return {"ok": True}


@app.get("/api/commands")
def get_commands():
    out, S.commands = S.commands, []
    return out


@app.post("/api/reset")
def reset():
    S.reset()
    return {"ok": True}


@app.post("/hooks/{path:path}")
async def hook(path: str, request: Request):
    S.hooks.append({"path": path, "t": time.time(), "body": await request.json()})
    return {"status": "ok"}


@app.get("/api/hooks")
def get_hooks():
    return S.hooks


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket):
    await ws.accept()
    S.audio_connections += 1
    try:
        while True:
            S.audio.append((time.time(), await ws.receive_text()))
            S.audio_count += 1
    except WebSocketDisconnect:
        pass


@app.get("/api/audio")
def get_audio(last: int = 0):
    frames = list(S.audio)[-last:] if last else []
    return {"count": S.audio_count, "connections": S.audio_connections, "frames": [f for _, f in frames],
            "received": [t for t, _ in frames]}


@app.websocket("/ws/video")
async def ws_video(ws: WebSocket):
    await ws.accept()
    S.video_connections += 1
    try:
        while True:
            raw = await ws.receive_text()
            try:
                pid = json.loads(raw)["data"]["data"]["participant"]["id"]
            except (ValueError, KeyError, TypeError):
                pid = "?"
            S.video[pid].append(raw)
            S.video_count[pid] += 1
    except WebSocketDisconnect:
        pass


@app.get("/api/video")
def get_video(frames: int = 0):
    return {"connections": S.video_connections, "count": dict(S.video_count),
            "frames": {pid: list(q)[-frames:] for pid, q in S.video.items()} if frames else {}}


@app.exception_handler(Exception)
async def oops(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=500)
