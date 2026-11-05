"""Measure one bot's CPU and memory in the FAKE meeting with given xbot_options (this VM only; needs the test
stack: docker compose -f docker-compose.yml -f tests/docker-compose.test.yml up -d --build).

Usage: python3 scripts/measure-cpu.py '{"save_media": true}' [seconds]
Prints the average and highest CPU (100% = one core) and memory while the bot is in the call.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time

sys.path.insert(0, "tests")
from dockerstack import api_key, request, wait_status  # noqa: E402

options = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 30
key = api_key()
request("POST", "/api/reset", {}, base="http://localhost:9000")
status, bot = request("POST", "/api/v1/bot/", {
    "meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Measure",
    "output_media": {"camera": {"kind": "webpage", "config": {"url": "http://testkit:9000/camera.html"}}},
    "recording_config": {"realtime_endpoints": [
        {"type": "webhook", "url": "http://testkit:9000/hooks/call-events", "events": ["participant_events.join", "participant_events.leave"]},
        {"type": "websocket", "url": "ws://testkit:9000/ws/audio", "events": ["audio_separate_raw.data"]},
        {"type": "websocket", "url": "ws://testkit:9000/ws/video", "events": ["video_separate_png.data"]}]},
    "xbot_options": options}, key=key)
assert status == 201, bot
name = "xbot-" + bot["id"].removeprefix("xbot_")
try:
    wait_status(key, bot["id"], "in_call_recording", timeout=180)
    time.sleep(25)  # Meera has joined, the layout is settled
    cpu, mem = [], []
    end = time.time() + seconds
    while time.time() < end:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}} {{.MemUsage}}", name],
                             capture_output=True, text=True).stdout.split()
        if out:
            cpu.append(float(out[0].rstrip("%")))
            value, unit = re.match(r"([\d.]+)(\w+)", out[1]).groups()
            mem.append(float(value) * (1024 if unit.startswith("G") else 1))
    print(json.dumps({"options": options, "samples": len(cpu), "cpu_avg": round(sum(cpu) / len(cpu)),
                      "cpu_max": round(max(cpu)), "mem_mib": round(sum(mem) / len(mem))}))
finally:
    request("POST", f"/api/v1/bot/{bot['id']}/leave_call/", key=key)
    wait_status(key, bot["id"], "done", timeout=120)
