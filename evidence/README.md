# Evidence: what was tried on real Google Meet, and what happened

The owner ran all of these by hand, on an Intel Mac on a home network, in real Meet calls the owner
hosted. People's names and the bot account's details are replaced by labels such as `<the host>`.

## 1. Guest join: WORKS. This is what v1 uses.

- Code: `bot/`, copied as-is from the owner's test folder.
- How it was run, and the results table: `bot/JOIN_TEST_RUN.md`.
- 5 runs on 2026-11-04 between 09:45 and 09:50 UTC, in Docker (Google Chrome, headful, on Xvfb).
  Name "Notetaker", typed key by key through VNC, 15 seconds on the pre-join screen before asking to
  join. **5 of 5 were let in.** About 42 seconds from start to the join request.
- Files per run, in `evidence/guest-join/<run>/`:
  - `events.jsonl`: the status list, in Recall's words;
  - `options.json`: the exact options;
  - `prejoin.png`, `02-in_waiting_room.png`, `03-in_call_not_recording.png`, `04-call_ended.png`;
  - `04-call_ended.html`: the real Meet page at the end.

## 2. Signed-in join: WORKS. Not used in v1; a later fallback.

- Code: `spikes/signed-in-join/` (TypeScript), copied as-is.
- How it was run, and the results table: `spikes/signed-in-join/RUN.md`.
- The bot used Google Chrome on the Mac with the bot account's saved profile. The owner signed that
  profile in once by hand, in a Chrome started outside Playwright.
- 3 runs on 2026-11-04 between 11:34 and 11:38 UTC: **3 of 3 got in.** The first went through
  "Ask to join" and the host's Admit (5 s). The other two got "Join now" and went straight in.
- The saved sign-in, the account's name and its address are NOT in this repo, on purpose. Never
  try to sign in to Google from the cloud VM.

## 3. What did NOT work. Don't repeat these.

| Runs (`evidence/signed-in-join/`) | What was tried | What happened |
| --- | --- | --- |
| `20261026T1233*-ask-*-anonymous` (4 runs) | A plain Playwright Chrome or Chromium, signed out | Blocked before the pre-join page ("You can't join this video call") |
| `20261028T074452Z-let-in-chromium-guest-profile`, `20261028T074514Z-let-in-chromium-anonymous` | A Playwright Chromium guest, with a once-signed-in profile and with an empty one | Blocked the same way |
| `20261027T0648*` to `0653*-signin-*` | Signing in to Google inside a browser that Playwright started, by hand and by script | Google refused the sign-in (see each `run.log`) |

So the guest join works only the way `bot/` does it: its Chrome flags, clicks and typing through
VNC, recorded human mouse movement and typing rhythm, and no JavaScript in Meet's page before the
bot is let in.

## 4. Real page snapshots of every Meet state

The signed-in runs of 2026-10-26 and 2026-10-27 also went through every way a call can end for the
bot: let in, refused, removed by the host, left by itself, withdrew its request. Each run folder
holds, for every state it saw:

- `NN-<state>.json`: a snapshot of the page. `flags` is what the state reader found (join button,
  waiting text, "Leave call", "Share screen", captions, people button, tiles, end text). `items` is
  every visible text, button label, input, live region and participant tile at that moment.
- `NN-<state>.png`: a screenshot.
- `run.log`: the timeline.

The reader that made them: `spikes/signed-in-join/src/page.ts` (`readPage()` and `meetState()`).

**Use these to build the fake Meet page and the state checks** (`docs/PLAN.md`, task 5): copy the
real labels and texts from the snapshots, not from memory.
