# STATUS

Where the overnight build (2026-11-04) stands. Newest first inside each section.

## Tasks

| Task | State | Notes |
| --- | --- | --- |
| 1 Layout and tooling | done | `pytest` runs; Docker tests are marked `docker` |
| 2 API without Docker | done | `tests/test_api.py`: every endpoint, checked with xebit's `_latest_recall_status_code`, `_result_count` and `create_bot()`'s exact body (`tests/xebit_logic.py`) |
| 3 Bot takes orders | not started | |
| 4 Manager runs real containers | not started | |
| 5 Fake meeting page | not started | |
| 6 Sound, both ways | not started | |
| 7 Screen share | not started | |
| 8 Participants and leaving | not started | |
| 9 Morning kit | not started | |
| 10 Clean finish | not started | |
| 11 Camera pictures | not started | |
| 12 Speaker timeline, per-person audio | not started | |

## How to run the tests

1. `python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt`
2. Fast tests: `pytest`
3. Docker tests (slow, need Docker): `pytest -m docker`

## Decisions

(Each one: what was unclear, what was chosen, why.)

- **A container that stops after `call_ended` gets `done`, not `fatal`.** The spec says a container that
  dies without `done` or `fatal` gets `fatal` / `bot_errored`. But after `call_ended` only `done` may follow
  (xebit already treats the bot as gone), so the manager adds `done` there instead.
- **Status rules live in the manager** (`manager/store.py`): the same code twice in a row is dropped;
  nothing after `fatal`; only `done` after `call_ended`; nothing after `done`.
- **If Docker can't start a bot, `POST /bot/` answers `503`** (and the bot record gets `fatal` /
  `bot_errored`), so xebit-api fails the launch at once instead of polling.
- **`leave_call` before the bot's control server is up** stops the container with SIGTERM. The bot treats
  SIGTERM as a leave request, as Ctrl+C did in the proven runs.
- **The bot reads its config from the manager** (`GET /internal/bots/<id>/config` with its own token),
  so the camera page URL and the endpoint URLs (they carry tokens) are not in the container's environment.
- **Each bot gets its own internal token** (HMAC of a manager secret and the bot id). The secret is kept in
  the state file, so a manager restart keeps live bots working.

## Known gaps

## Only a real Meet call can prove
