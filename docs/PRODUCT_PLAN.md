# PRODUCT PLAN — the meeting bot as its own product

Agreed with the owner on 2026-11-11. Today this repo is a Recall.ai-compatible bot for one user on one Mac.
The goal: a separate product that any customer can use, in its own repo in Xebit's GitHub organisation.
Xebit is the first customer.

## What the first version is (and is not)

**Is:** a simple working model that Xebit can switch to from Recall.
- Google Meet only, guest join only.
- The Recall-compatible API that xebit-api already calls (create a bot, read a bot, leave, start and stop
  the screen share, list bots for the pre-launch check).
- Many customers: each with its own API keys, bots, limits and webhook address.
- Events: status changes as webhooks in Recall's shape, plus the live feeds we have today (people joining
  and leaving, the call's sound, camera pictures), sent straight to the customer's addresses.
- A customer dashboard and an admin page.
- Runs on Xebit's server for testing, one bot at a time.

**Is not (later):** Zoom, Teams, signed-in bots, transcripts, storing recordings or any other files,
billing, self-signup, legal pages, a dedicated server. "Why us and not Recall" is discussed later.

## Decisions (2026-11-11)

| Question | Decision |
|---|---|
| Where the code lives | `xebitlabs/xebit-bots` (made 2026-11-11), cloned at `~/Documents/work/xebit/xebit-bots`, remote `git@github.com-work:xebitlabs/xebit-bots.git` (the work SSH alias) |
| Commit author | `Akarshit Gupta <akarshit@xebit.ai>` (the work email), also for the owner's old commits; Claude's commits stay as they are |
| Local test runs | `./try.sh` does not build images each time: code comes from the folder; a build happens only when the Dockerfile or the requirements change (task 7) |
| API | Recall-compatible; our extras go on top, never in place of Recall's fields |
| Files | None stored. Relay everything. Only the bot's text log is kept, for 7 days (no screenshots: they show people's faces) |
| Dashboard login | A separate Clerk application (Xebit already uses Clerk; self-signup and password reset come later for free) |
| Dashboard build | Simple pages served by the same Python service (one thing to deploy), not a separate Next.js app |
| Server | Xebit's prod server (about 4 cores, 16 GB, already runs Chrome for demos): one bot at a time, tests at quiet hours. Its own server before any outside customer |

## Tasks

### Phase 0 — the new repo
1. Make a fresh clone of this repo. In that clone only, remove `evidence/` from the whole history (it holds
   screenshots of the owner's real test calls; the product does not need them). Never rewrite or
   force-push this personal repo.
2. Scan the whole history for secrets (gitleaks or similar) and show the result to the owner before the
   first push. A quick scan on 2026-11-11 found no keys or passwords; commits carry the owner's personal
   email as author (the owner decides: keep it, or change it in the same rewrite).
3. Push to the new, empty repo `xebitlabs/xebit-bots`, after the owner says OK.
4. Rewrite `CLAUDE.md` and `README.md` for the new repo: it is no longer an overnight build VM. Keep the
   rules that still hold (below, "Rules that carry over").
5. GitHub Actions: run the fast tests (`pytest`, without `-m docker`) on every pull request.
6. Keep `bot/mouse/` (the owner's recorded mouse movements and typing rhythm): the proven join replays them.
   Keep `spikes/signed-in-join/` (the start of signed-in bots, a later feature).

7. Local test runs without a build each time. The code comes from the folder (the manager's code mounted
   into its container; the bot's code mounted into each bot container by the manager, only when a local
   setting names the folder). `./try.sh` builds only when an image is missing or its Dockerfile or
   requirements changed (for example a hash of those files kept as an image label), or with
   `./try.sh --build`. Servers (Phase 6) never mount code: they run the built images.
   Watch out: the manager starts bot containers through the Mac's Docker, so mount paths must be the Mac's
   paths (as `HOST_OUTPUT_DIR` already is); `bot/entrypoint.sh` is not executable on the Mac (the image
   sets 755); `/mouse` comes from `bot/mouse/`. The Docker tests (`pytest -m docker`) must still pass.

### Phase 1 — prove the bot on real Meet (owner's Mac)
8. The owner reruns the Round 3 test (`MORNING.md` section 0) with a second person. The run on 2026-11-05
   (`output/20261105-131624-aoj-tgmx-zns`) was never admitted, so the Round 3 code is untested on real Meet.
9. Fix what that test finds. Read the run folder: `bot.log` lines `usage:`, `slow:`, `voices:`,
   `screen share stopped in`; `speakers.jsonl` (`source: indicator` means Meet's speaking light was read).

### Phase 2 — many customers
10. Postgres (its own container) instead of `state/bots.json`: customers, API keys (stored hashed, shown
   once), bots, each bot's status history, a log of every event delivery.
11. API keys per customer, header `Authorization: Token <key>` as now. Every call is scoped to the key's
    customer: another customer's bot is `404`.
12. Limits: bots at once per customer, and for the whole server (`429` above either).
13. Count each customer's bot minutes in calls (shown in the dashboard; billing reads it later).
14. Bot IDs keep the `xbot_` prefix (xebit-api will route on it, task 30).

### Phase 3 — events
15. Status webhooks: each customer sets one webhook address. Every status change is sent there in Recall's
    shape (`bot.joining_call`, `bot.in_waiting_room`, `bot.in_call_recording`, `bot.call_ended`,
    `bot.done`, `bot.fatal` ..., with `code` and `sub_code`), so xebit-api's existing
    `/api/webhooks/recall/status` works unchanged. Read that handler first: it accepts two payload shapes.
16. Sign every webhook with the customer's own secret (headers in the Svix style, as Recall does), retry
    failures with growing waits, and log each delivery (shown in the dashboard).
17. The live feeds stay as now, sent from the bot to the customer's URLs: `participant_events.join/leave`
    (webhook, now signed too), `audio_separate_raw.data` and `video_separate_png.data` (websockets).
18. On the server: no recordings, no saved pages, no screenshots, no per-person sound files. Only `bot.log`
    (text), deleted after 7 days.

### Phase 4 — safety (before sharing Xebit's server)
19. Bot containers cannot reach private addresses: the server's other containers, `10.0.0.0/8`,
    `172.16.0.0/12`, `192.168.0.0/16`, `169.254.169.254` (AWS instance information). Customers' pages run in
    our browser, so this matters.
20. The part that starts bots (it holds the Docker socket) is never reachable from outside. Only the API and
    the dashboard are public, over HTTPS.
21. CPU and memory caps for all bots together, so a bot can never slow down a live Xebit demo or push
    Xebit's database out of memory.
22. The live view of a bot's screen opens only through the dashboard, after login, and only for the
    customer's own bots (admins: all bots).

### Phase 5 — dashboard and admin page
23. Login with the Clerk application.
24. Customer dashboard: their bots (status history, delivered events, the text log), API keys (create,
    revoke), webhook address and secret, minutes used this month.
25. Admin page: customers (add, disable, set limits), every bot running now (with a stop button), server
    load (from the `usage:` numbers), recent failures.

### Phase 6 — run on Xebit's server
26. GitHub Actions builds the images and pushes them to GitHub's registry (GHCR); the server only pulls.
    A full bot image build takes about 7 minutes of CPU (Chrome): not on the prod server during demos.
27. A deploy workflow like xebit-api's `ci.yml`: SSH, pull the images, `docker compose up -d`.
28. An address such as `bots.xebit.ai`: a DNS record, plus a site in the server's Caddy (the Caddyfile is in
    xebit-api, so that is a small xebit-api change).
29. A test from outside: a test customer and key, a real Meet call, every event arriving.

### Phase 7 — Xebit becomes customer #1 (changes in xebit-api)
30. A setting to choose Recall or our service, plus our base URL and key. `api/services/recall.py` builds
    `RECALL_BASE` from the region today; `api/services/preflight.py` also uses `RECALL_BASE`.
    Bots whose ID starts with `xbot_` go to us; a Recall bot still running keeps going to Recall.
31. Check what Xebit loses: it asks Recall for `transcript.data` / `transcript.partial_data`, which we do
    not send. It looks observational only (`tests/test_recall_observational_only.py`); confirm before the
    switch. It also sends `variant: web_4_core` (we accept and ignore it).
32. Stage first, then a real demo on prod. Compare with Recall: joins that worked, sound, delay.

## Rules that carry over
- Never open a real Google Meet link from a cloud VM. Real-Meet tests run on the owner's Mac (`./try.sh`).
- Never log a full URL from a customer: camera-page, screen-page and socket URLs carry tokens. Log the host
  and path only.
- Never commit secrets: no `.env`, no saved Google sign-ins, no bot-account passwords.
- Never force-push.
- `docs/xebit-consumer/` is copied from xebit-api and is the contract: never edit it; copy its logic into
  tests. When the spec and that code disagree, the code wins.

## Facts worth knowing
- The Python base image is pinned by digest in all three Dockerfiles (2026-11-05): an unpinned Docker Hub
  update once forced a 7-minute full rebuild. `docker buildx history ls` shows every build's time.
- The owner's Mac slows down when hot (`pmset -g therm`, `CPU_Speed_Limit`); a slow Mac makes the bot late
  (clicks, pictures). `try.sh` warns below 80%.
- Real Meet (2 tests, 2026-11-05): each person stays in one `<audio>` slot for the whole call; the speaking
  light is class `wEsLMd` (silent: `gjg47c`); captions start about 30 s late and lag 1-3 s.
- Each bot may use up to 2.5 cores and 5 GB (`docs/SPEC.md` 4.4). Its real use in a call is in `bot.log`
  (`usage:` lines) once the Round 3 test runs.
