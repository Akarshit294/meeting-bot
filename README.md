# meeting-bot

A self-hosted meeting bot service. It joins a Google Meet call as a guest, speaks with the voice of
a web page we give it, hears the call, shows a second web page as its screen share, and reports who
joins and leaves. Its API copies the small part of Recall.ai's API that xebit-api uses, so xebit-api
can switch to it by changing a base URL and a key.

**Try it:** `./try.sh https://meet.google.com/abc-defg-hij` (Docker Desktop running). Details: `MORNING.md`
(start with its "Round 2 test"). Each run's folder (`output/<date>-<time>-<meeting code>/`) holds the logs,
screenshots, Meet's page (`dom/`), the recordings (`audio/`, `video/`, `screen.mp4`) and who spoke when
(`captions.jsonl`, `speakers.jsonl`).

What is where:

- `manager/`: the API (FastAPI, port 8090). It starts one bot container per meeting.
- `bot/`: the bot image (the proven guest join, plus voice, hearing, people, screen share, pictures,
  captions, recordings).
- `tests/`: `pytest` (fast) and `pytest -m docker` (the bot in the fake meeting, `tests/testkit/`).
- `docker-compose.yml`, `try.sh`: run it.

Documents:

- What it must do: `docs/SPEC.md`
- How it is being built: `docs/PLAN.md`
- Where the work stands: `STATUS.md`
- How to test it on a Mac: `MORNING.md`
- Captions, who spoke when, each person's sound: `docs/per-person-audio.md`
- The proven guest join it starts from: `bot/` (`bot/JOIN_TEST_RUN.md`)
- The proven signed-in join, for later: `spikes/signed-in-join/`
- Every real-Meet test run and its result: `evidence/README.md`
