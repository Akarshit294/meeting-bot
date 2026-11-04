# STATUS

Where the overnight build (2026-11-04) stands. Newest first inside each section.

## Tasks

| Task | State | Notes |
| --- | --- | --- |
| 1 Layout and tooling | done | `pytest` runs; Docker tests are marked `docker` |
| 2 API without Docker | not started | |
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

## Known gaps

## Only a real Meet call can prove
