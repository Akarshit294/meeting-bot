# CLAUDE.md — meeting-bot

A self-hosted meeting bot service. It joins a Google Meet call as a guest, speaks with the voice of a
web page we give it, hears the call, shows a second web page as its screen share, and reports who
joins and leaves. Its API is a small, exact copy of the parts of Recall.ai's API that xebit-api uses,
so xebit-api can switch from Recall to this service by changing only a base URL and a key.

Read these before you change anything:

1. `docs/SPEC.md` — what the service must do, field by field. This is the contract.
2. `docs/PLAN.md` — the build tasks, in order, each with its "done when".
3. `STATUS.md` — where the work stands. Keep it current.
4. `docs/xebit-consumer/` — the xebit-api code that reads what we send. It is the ground truth for
   every payload shape. Never edit it; copy its logic into tests.
5. `evidence/README.md` — what was tried on real Meet and what happened: the guest join (5 of 5,
   2026-11-04), the signed-in join (3 of 3), what Google blocked, and real page snapshots of every
   Meet state. Build the fake Meet page from those snapshots.
6. `bot/` — the WORKING guest join, copied as-is. `bot/JOIN_TEST_RUN.md` shows how it was tested.
7. `spikes/signed-in-join/` — the WORKING signed-in join (TypeScript), copied as-is. Not used in v1.
   Its `src/page.ts` is the best Meet state reader we have.

## Rules

1. **You work unattended.** The owner is asleep and expects a finished service in the morning. Do not
   stop to ask questions. When something is unclear, choose the simplest option that keeps the
   contract, write the choice in `STATUS.md` under "Decisions", and continue. Stop only when every
   task in `docs/PLAN.md` is done or truly blocked. A blocked task goes in `STATUS.md` with what you
   tried; then move on to the next task.
2. **Commit and push after every finished task**, with a clear message. If this session was given
   its own branch, push there; when every task is done, also merge that branch into `main` and push
   `main`, so the owner only has to `git pull` `main`. Otherwise push to `main`. Never leave work only
   on the VM. Never force-push.
3. **Never open a real Google Meet link from this VM.** Calls can't work here (traffic goes through
   a web proxy, so call audio and video can't get out) and Google would see a data-centre address
   and might mark it. Every Meet test runs on the owner's Mac in the morning. Test here with the fake
   meeting page (see `docs/PLAN.md`, task 5).
4. **Don't weaken the join.** The join steps in `bot/meet_bot.py` are proven by hand-run tests and
   are easy to break: clicks and typing go through VNC (x11vnc), the mouse replays recorded human
   movement, no JavaScript runs in Meet's page world before the bot is admitted (read the page only
   through Playwright locators, which run in an isolated world), the Chrome flags hide nothing
   beyond what is there now. You may restructure the code, but keep these behaviours. Everything
   new happens AFTER admission. Keep a `--join-only` mode that behaves exactly like today's bot.
5. **The API must stay wire-compatible** with `docs/SPEC.md`. xebit-api will not be changed to fit
   us, apart from the base URL and the key.
6. **Never log a full URL from xebit.** Camera-page, screen-page and socket URLs carry tokens. Log
   the host and path only.
7. **No secrets in the repo.** Keys come from environment variables. `.env` files are git-ignored.
8. **Tests:** pytest. Run the files that touch your change after each task, and the whole suite at
   the end. Docker-based tests may be slow; mark them so they can run on their own
   (`pytest -m docker`).
9. **Write docs simply.** The owner reads `STATUS.md` and `MORNING.md` first thing: short plain
   sentences, numbered steps, exact commands. No jargon where a plain word works.
10. Python 3.12 everywhere (the bot already uses it). Keep dependencies few and pinned.

## Network in this VM

All outbound traffic goes through an HTTPS proxy. `docker build` steps may need the proxy settings
(`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` build args) and the proxy's CA certificate; find them in
the VM's environment and `/etc/ssl` and pass them in. Do not bake them into the image for the Mac:
use build args or a separate build stage that is a no-op on the Mac.
