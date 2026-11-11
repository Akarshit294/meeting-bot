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
8. Bring in the fix for Meet's refusals of 2026-11-11. Read `docs/why-meet-refused-the-bot.md` first. The fix
   is commit `2b21d85` in the personal repo (`~/Documents/personal/meeting-bot`, `Akarshit294/meeting-bot`):
   the bot image installs the newest Chrome (`CHROME_REFRESH`) and Linux desktop fonts; Meet's Chrome gets
   its time zone from `BROWSER_TZ` (default `Asia/Kolkata`); `test_browser_time_zone`;
   `scripts/browser_check.py`, `scripts/browser_check.sh`, `scripts/mac_bot_browser.py`; the "If Meet
   refuses the bot" paragraph of `MORNING.md`; the write-up itself. Take the changes as a patch, not the
   commit: `git -C ~/Documents/personal/meeting-bot show 2b21d85 | git apply -3`, then commit as
   `Akarshit Gupta <akarshit@xebit.ai>`. Don't `git fetch` the personal repo: that would bring its whole
   history, `evidence/` included, into this one. With task 7's build check, a new `CHROME_REFRESH`
   value must start the full build (about 7 minutes): that is its purpose. Done when the browser check on
   the new image (command in the write-up) shows the current Chrome, `Asia/Calcutta` and 300+ fonts, and
   the fast tests pass. Never test it by opening real Meet yourself: the owner does that.

### Phase 1 — prove the bot on real Meet (owner's Mac)
9. The owner reruns the Round 3 test (`MORNING.md` section 0) with a second person. The run on 2026-11-05
   (`output/20261105-131624-aoj-tgmx-zns`) was never admitted. First real call with a second person, on the
   fixed image (task 8): 2026-11-11, `output/20261111-100138-*` on the owner's Mac. Read it in task 10, and
   repeat the `MORNING.md` checks it did not cover.
10. Fix what that test finds. Read the run folder: `bot.log` lines `usage:`, `slow:`, `voices:`,
   `screen share stopped in`; `speakers.jsonl` (`source: indicator` means Meet's speaking light was read).

### Phase 2 — many customers
11. Postgres (its own container) instead of `state/bots.json`: customers, API keys (stored hashed, shown
   once), bots, each bot's status history, a log of every event delivery.
12. API keys per customer, header `Authorization: Token <key>` as now. Every call is scoped to the key's
    customer: another customer's bot is `404`.
13. Limits: bots at once per customer, and for the whole server (`429` above either).
14. Count each customer's bot minutes in calls (shown in the dashboard; billing reads it later).
15. Bot IDs keep the `xbot_` prefix (xebit-api will route on it, task 31).

### Phase 3 — events
16. Status webhooks: each customer sets one webhook address. Every status change is sent there in Recall's
    shape (`bot.joining_call`, `bot.in_waiting_room`, `bot.in_call_recording`, `bot.call_ended`,
    `bot.done`, `bot.fatal` ..., with `code` and `sub_code`), so xebit-api's existing
    `/api/webhooks/recall/status` works unchanged. Read that handler first: it accepts two payload shapes.
17. Sign every webhook with the customer's own secret (headers in the Svix style, as Recall does), retry
    failures with growing waits, and log each delivery (shown in the dashboard).
18. The live feeds stay as now, sent from the bot to the customer's URLs: `participant_events.join/leave`
    (webhook, now signed too), `audio_separate_raw.data` and `video_separate_png.data` (websockets).
19. On the server: no recordings, no saved pages, no screenshots, no per-person sound files. Only `bot.log`
    (text), deleted after 7 days.

### Phase 4 — safety (before sharing Xebit's server)
20. Bot containers cannot reach private addresses: the server's other containers, `10.0.0.0/8`,
    `172.16.0.0/12`, `192.168.0.0/16`, `169.254.169.254` (AWS instance information). Customers' pages run in
    our browser, so this matters.
21. The part that starts bots (it holds the Docker socket) is never reachable from outside. Only the API and
    the dashboard are public, over HTTPS.
22. CPU and memory caps for all bots together, so a bot can never slow down a live Xebit demo or push
    Xebit's database out of memory.
23. The live view of a bot's screen opens only through the dashboard, after login, and only for the
    customer's own bots (admins: all bots).

### Phase 5 — dashboard and admin page
24. Login with the Clerk application.
25. Customer dashboard: their bots (status history, delivered events, the text log), API keys (create,
    revoke), webhook address and secret, minutes used this month.
26. Admin page: customers (add, disable, set limits), every bot running now (with a stop button), server
    load (from the `usage:` numbers), recent failures.

### Phase 6 — run on Xebit's server
27. GitHub Actions builds the images and pushes them to GitHub's registry (GHCR); the server only pulls.
    A full bot image build takes about 7 minutes of CPU (Chrome): not on the prod server during demos.
    Also a weekly scheduled build with a new `CHROME_REFRESH`, so the bot's Chrome never falls behind real
    people's (task 8, `docs/why-meet-refused-the-bot.md`).
28. A deploy workflow like xebit-api's `ci.yml`: SSH, pull the images, `docker compose up -d`. Set the
    bot's `BROWSER_TZ` to the server's region (`BOT_ENV_BROWSER_TZ`; Mumbai is the default, `Asia/Kolkata`).
29. An address such as `bots.xebit.ai`: a DNS record, plus a site in the server's Caddy (the Caddyfile is in
    xebit-api, so that is a small xebit-api change).
30. A test from outside: a test customer and key, a real Meet call, every event arriving. The server's
    data-centre internet address is one more oddity for Meet's check (`docs/why-meet-refused-the-bot.md`):
    make several tries, 10 minutes apart, and count how many get in.

### Phase 7 — Xebit becomes customer #1 (changes in xebit-api)
31. A setting to choose Recall or our service, plus our base URL and key. `api/services/recall.py` builds
    `RECALL_BASE` from the region today; `api/services/preflight.py` also uses `RECALL_BASE`.
    Bots whose ID starts with `xbot_` go to us; a Recall bot still running keeps going to Recall.
32. Check what Xebit loses: it asks Recall for `transcript.data` / `transcript.partial_data`, which we do
    not send. It looks observational only (`tests/test_recall_observational_only.py`); confirm before the
    switch. It also sends `variant: web_4_core` (we accept and ignore it).
33. Stage first, then a real demo on prod. Compare with Recall: joins that worked, sound, delay.

## Rules that carry over
- Never open a real Google Meet link from a cloud VM. Real-Meet tests run on the owner's Mac (`./try.sh`).
- Never log a full URL from a customer: camera-page, screen-page and socket URLs carry tokens. Log the host
  and path only.
- Never commit secrets: no `.env`, no saved Google sign-ins, no bot-account passwords.
- Never force-push.
- Keep the bot's Chrome current and its time zone set to the server's region. When Meet refuses the bot,
  check the browser (`scripts/browser_check.py`) before the join code, and don't retry many times in a row
  (`docs/why-meet-refused-the-bot.md`).
- `docs/xebit-consumer/` is copied from xebit-api and is the contract: never edit it; copy its logic into
  tests. When the spec and that code disagree, the code wins.

## Facts worth knowing
- The Python base image is pinned by digest in all three Dockerfiles (2026-11-05): an unpinned Docker Hub
  update once forced a 7-minute full rebuild. `docker buildx history ls` shows every build's time.
- The owner's Mac slows down when hot (`pmset -g therm`, `CPU_Speed_Limit`); a slow Mac makes the bot late
  (clicks, pictures). `try.sh` warns below 80%.
- Real Meet (2 tests, 2026-11-05): each person stays in one `<audio>` slot for the whole call; the speaking
  light is class `wEsLMd` (silent: `gjg47c`); captions start about 30 s late and lag 1-3 s.
- Meet scores what the browser looks like before any click (2026-11-11): the container with an out-of-date
  Chrome, UTC and 5 fonts was refused nearly every time; with the newest Chrome, the Indian time zone and
  desktop fonts it got in 3 of 3. One try proves nothing: compare several tries, 10 minutes apart.
- Each bot may use up to 2.5 cores and 5 GB (`docs/SPEC.md` 4.4). Its real use in a call is in `bot.log`
  (`usage:` lines) once the Round 3 test runs.
