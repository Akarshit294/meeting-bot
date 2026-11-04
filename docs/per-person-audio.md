# Per-person audio (experimental) and the speaker timeline

Built overnight on 2026-11-04 (docs/PLAN.md task 12). Tested only against the fake Meet page.

## 1. Who spoke when: `speakers.jsonl` (always on)

- About 5 times a second the bot asks Meet, with one locator query, which tiles or People-panel entries
  say "this person is speaking". Each change becomes one line in the run folder's `speakers.jsonl`:
  `{"t": "...", "id": "<same id as the join webhook>", "name": "Asha", "event": "started" | "stopped"}`.
- The signals it looks for (from `bot/speakers.py`): `data-speaking="true"`, an `aria-label` that says
  "speaking", or a class name containing one of `SPEAKING_CLASSES` (an environment variable of the bot,
  default `speaking`).
- **Not known yet: what Meet's real speaking indicator looks like.** None of the real snapshots in
  `evidence/` caught someone talking. So every 10 s the bot also writes the class names of each tile's
  direct children to `timeline.jsonl` (`tile_classes`), whenever they change. In the morning, talk for a
  few seconds, stop, talk again, then compare those lines with when you talked: the class that comes and
  goes with your voice is the indicator. Give it to the bots (section 4) and the timeline works.

## 2. One stream per person (`--per-person-audio`, off by default)

### Why it is hard

Meet does not send one stream per person. Every real in-call snapshot shows exactly 3 `<audio>` elements:
Meet sends the loudest speakers in 3 "slots", and which person is in which slot changes. Naming a slot
needs either code in Meet's page (which can get the guest join blocked) or matching the slot to the person
Meet shows as speaking.

### What the bot does (option 1 of the plan: an isolated world)

After admission only, and only with the flag:

1. Through the DevTools protocol (`Page.createIsolatedWorld`) the bot makes an **isolated world** in Meet's
   tab. Code there shares the page's elements but not its JavaScript: Meet's scripts cannot see it, and
   nothing runs in Meet's page world. A DevTools binding (`Runtime.addBinding`, given only to that world)
   carries data back to the bot.
2. In that world, each `<audio>` element's stream (`srcObject`) goes into the bot's own 16 kHz AudioContext.
   Every 200 ms, each slot's sound (16-bit PCM) and its level go to the bot. New elements and new streams
   are picked up every second.
3. The bot names each slot with the speaker timeline (`SlotNamer` in `bot/per_person_audio.py`):
   - a slot that is the only one with sound while exactly one person is shown speaking belongs to them;
   - it stays theirs while it has sound and they are still shown speaking;
   - a new slot with sound goes to the one speaking person that no other slot has;
   - when it is not clear (two new slots, two people), nothing is guessed.
4. Up to 1 s of a slot's sound waits for its name (the indicator comes a little after the sound), then
   goes to that person. Sound that can't be named is not sent.
5. Frames are exactly like the mixed stream's (`docs/SPEC.md` 3.2), with the person's id and name: the
   same ids as the join/leave webhooks. No `meeting-mix` stream is sent while this works.
6. **Safety net:** if 10 s of sound arrive and nobody can be named (the indicator isn't read), or nothing
   arrives from Meet's `<audio>` elements for 15 s, the bot goes back to the mixed stream for the rest of
   the call and says so in `bot.log` and `timeline.jsonl` (`fallback_to_mix`). The agent always hears the call.

### What works (fake meeting)

`tests/test_docker_per_person.py`: the fake meeting plays Asha (440 Hz), Ravi (660 Hz) and Meera (880 Hz)
through 3 fixed `<audio>` slots, taking 3-second turns, with a speaking indicator. The frames for each
person carry that person's tone (checked with xebit's decoder and an FFT), with the same ids as the join
webhooks. In one run: 127 frames named (Asha 40, Ravi 57, Meera 30), 33 with sound not named (mostly
when the fake page redraws its tiles and the indicator blinks off). The mixed default still passes all
its tests.

### Option 2 (not built): code in Meet's page world

The plan says: describe, don't build. It would be:

- after admission, inject a script into Meet's page world (for example `Page.addScriptToEvaluateOnNewDocument`
  is out, since it runs before admission; it would be `Runtime.evaluate` in the main world) that wraps
  `RTCPeerConnection` events (`track`) or reads Meet's own participant-to-stream map, to know which remote
  track (and so which slot) carries which participant;
- then record each track directly, with no guessing from the speaking indicator.

Risks: Meet can see changed built-ins and any timing or object it doesn't expect; that is exactly what
can get a guest refused or the call degraded, and Google changes Meet's internals often, so it would
break without warning. Before admission it would be far too risky; after admission it is still a risk
for a live demo. Option 1 does not need it.

## 3. What to check in a real call

1. Run `./try.sh --per-person-audio <meet link>` and join from two devices (you and a phone).
2. Talk from one device, then the other. `try.sh` should show `sound sent: N frames (You 40, Phone 25)`:
   one count per person, growing for whoever talks.
3. In the run folder:
   - `bot.log`: `per-person audio ... hooked`, later `frames sent ...; N frames with sound could not be named`.
     If it says `switching to the mixed stream`, the speaking indicator wasn't read (see section 1).
   - `speakers.jsonl`: `started`/`stopped` lines with your names when you talk.
   - `timeline.jsonl`: `tile_classes` lines, to find the real indicator if `speakers.jsonl` stays empty.
4. Without the flag, the mixed stream (`meeting-mix`) must work as before.

## 4. Setting the indicator's class name

If the morning test finds the class (say `abc123`), add this line to `.env`, then restart the service
(new bots get it):

```bash
echo 'BOT_ENV_SPEAKING_CLASSES=abc123' >> .env
```

```bash
docker compose up -d
```

Any `BOT_ENV_<NAME>=value` line in `.env` reaches every bot as `<NAME>=value`. Several class names can be
given, separated by commas.
