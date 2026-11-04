# RUN

Test: the bot joins a Google Meet call signed in, with its own Google account. It asks to join, you let it in, and it leaves 20 seconds later. Three runs, one new call per run, one row per run in the table below.

## Before you start (once)

1. Open a terminal in `spikes/T0.2-join`.
2. Check Node: `node --version` prints v24 or newer. If `node_modules` is missing, run `npm ci`.
3. `spikes/T0.2-join/.env` has `BOT_ACCOUNT_NAME` and `BOT_ACCOUNT_EMAIL` filled in. The run refuses to start without them.
4. Check the bot's saved login still loads (no call is joined):

   ```
   npm run probe -- --browser chrome --profile chrome --check-login
   ```

   It prints `login {"landed":"myaccount.google.com/","signedIn":true}` when it works. If `signedIn` is false, the login has ended: sign in again by hand before the runs.

## One run

1. Start a new Meet call from your own account, on another device or in another browser. Never in the bot's browser. Do not invite the bot's account.
2. Mute your microphone. The bot's browser plays the call's sound on this machine.
3. Make sure no Chrome window is open on the bot's profile (`.auth/chrome`).
4. In the terminal, in `spikes/T0.2-join`:

   ```
   URL='https://meet.google.com/meet/eej-qvdp-tht'
   npm run join -- --run let-in --browser chrome --profile chrome --network home --url "$URL"
   ```

5. A Chrome window opens. The bot turns off its microphone and camera and clicks the join button. Leave its window alone.
6. The terminal prints a `>>>` line. When the bot's request shows in your call, click Admit, and press Enter in the terminal at the moment you click.
7. The terminal asks: "Is the bot shown in your call under the account's name?" Answer `y`, or type the name you see.
8. The bot stays about 20 seconds, leaves, and the run ends. `q` then Enter, or Ctrl+C, ends a run early.

Use a new call for every run. Once the bot's account has been let in to a call, Meet shows it "Join now" instead of "Ask to join" for the rest of that call.

## Read the result

Each run writes a folder `captures/T0.2/<UTC time>-let-in-chrome-signed-in/` in the repo root. Git ignores it. It holds `run.log` and, for each state, a screenshot (`NN-<state>.png`) and what the page showed (`NN-<state>.json`).

Look at the latest run, from the repo root:

```
D=$(ls -d captures/T0.2/*-let-in-chrome-signed-in | tail -1)
grep -a "login  \|measure\|run.end" "$D/run.log"
```

A good run:

- the `login` line shows `"seen":"signed in"`
- the `run.end` line shows `"result":"let in, then left (left)"` and `"states":"unknown > lobby > unknown > waiting > in-call > left"`

Other `run.end` results:

| Result | Meaning |
| --- | --- |
| `the saved login did not load` | the bot opened Meet signed out: the login has ended |
| `no pre-join page (<state>)` | Meet never showed the pre-join page |
| `refused`, `no-answer`, `blocked` | the page state at the end: Meet refused the bot, nobody answered, or Meet showed it could not join |
| `error: <message>` | the script failed; the message says why |
| `stopped by the owner` | you pressed `q` or Ctrl+C |

## After the three runs

From `spikes/T0.2-join`:

1. `npm run table` prints each run's states with their times.
2. `npm run leaks` checks that no log, page dump or screenshot gives the account's name or address. Run it before you share anything from `captures/`.

## Results

| Run | Date and time (UTC) | Meet link | Let in (yes / no) | `login` seen | `run.end` result | Run folder | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-11-04 11:34:49 | new call (the code is not saved in the log) | yes | `signed in` | `let in, then left (left)`; states `unknown > lobby > unknown > waiting > in-call > left` | `20261104T113449Z-let-in-chrome-signed-in` | Asked to join at 11:34:57, in the call at 11:35:03 (5 s). No Enter came within 15 s of that, so `measure owner.enter` shows `ms: null`. The join result is not affected. You answered `y` to the name question. |
| 2 | 2026-11-04 11:37:53 | new call (the code is not saved in the log) | yes, without being admitted | `signed in` | `let in, then left (left)`; states `lobby > unknown > in-call > left` | `20261104T113753Z-let-in-chrome-signed-in` | The bot clicked `Join now`, not `Ask to join`. No waiting room, and no admit prompt. In the call 1.2 s after the click. You answered `y`. |
| 3 | 2026-11-04 11:38:41 | new call (the code is not saved in the log) | yes, without being admitted | `signed in` | `let in, then left (left)`; states `unknown > lobby > unknown > in-call > left` | `20261104T113841Z-let-in-chrome-signed-in` | Same as run 2: `Join now`, no waiting room, in the call 1.5 s after the click. You answered `y`. |

Total: 3 / 3 runs got in. Only run 1 went through `Ask to join` and your Admit. Runs 2 and 3 joined straight in with `Join now`, so they did not test the admit step.
