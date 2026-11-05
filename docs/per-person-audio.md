# Who spoke when, and each person's sound

Built on 2026-11-04 (task 12), reworked on 2026-11-05 (round 2, task R3) after the first real Meet test
showed that Meet's speaking indicator was not recognised, and again the same day (round 3) after the second
real test: the files per person are now made once, at the end of the call (`bot/voicefiles.py`, section 3).

## 1. Captions: who says what (`captions.jsonl`)

After admission (after the microphone and the camera page) the bot turns Meet's captions on with the
human mouse. Only the bot sees them; nobody else in the call does. Two versions of the button exist right
now (public bot code, October 2026):

- **A:** a "Turn on captions" button (icon `closed_caption_off`). Once on, it says "Turn off captions".
- **B:** a "Captions and translation" button (icon `translate_cc`, the one in the first real test's
  screenshots). It opens a small menu with a "Turn on captions" switch. The bot clicks the switch, then
  presses Escape to close the menu.

If neither works, the bot presses `c` (Meet's own shortcut). If that fails too, it writes
`warning: captions could not be turned on` in `bot.log` and carries on.

The captions area is a region named "Captions". Each speaker turn is a block: the speaker's picture (an
`img` whose `alt` is the name), the name, then the text, which Meet rewrites while the person talks. The
bot's own voice is shown as "You" and is skipped. The bot reads the area about 3 times a second, through
locators only: one `inner_text` of the area and the pictures' `alt` texts. A line that is a speaker's name
starts a block; the lines after it are the text.

`captions.jsonl`: one line each time a block's text changes,
`{"t", "name", "id", "text", "changing": true}`, and one more when it hasn't changed for 1.5 s,
`{"changing": false}`. `id` is the participant id of the join webhook (null if the name matches nobody).
What was already on screen at the first read is written once as `changing: false`.

## 2. Who spoke when (`speakers.jsonl`)

Two sources, merged:

1. **Captions (the main one):** a person starts speaking when their caption block starts or grows, and
   stops about 1 s after it stops changing.
2. **The speaking indicator** on the tiles or People-panel entries, about 5 times a second, through locators
   only: `data-speaking="true"`, an `aria-label` that says "speaking", or a class name from
   `SPEAKING_CLASSES` (section 5). Real Meet's indicator is still not known for sure, so this source may
   stay silent there. Every 10 s each tile's class names go to `timeline.jsonl` (`tile_classes`).

Lines: `{"t", "id", "name", "event": "started" | "stopped", "source": "captions" | "indicator" | "both"}`.

## 3. Each person's sound (`audio/<name>.wav`, and `--per-person-audio`)

Meet does not send one stream per person: it plays the loudest speakers through 3 `<audio>` elements
("slots"). Every real in-call snapshot shows exactly 3. In the second real test (two people, 2 min) each
person stayed in their own slot from start to end; in a bigger call Meet must move people between slots.

What the bot does (all after admission, nothing in Meet's page world):

1. It hears each slot separately, from an **isolated world** in Meet's tab (`bot/taps.py`): DevTools
   `Page.createIsolatedWorld` makes a world that shares the page's elements but not its JavaScript; each
   `<audio>` element's stream goes into that world's own 16 kHz AudioContext; 200 ms pieces come back
   through a DevTools binding only that world has. With `--save-media` they are also saved raw as
   `audio/slot-1.wav` ... `slot-3.wav`.
2. **The files** (`--save-media`): at the end of the call, `bot/voicefiles.py` splits each slot:
   - each slot is cut into stretches of sound (a pause of 0.3 s ends one);
   - a stretch is named after the person `speakers.jsonl` clearly shows talking during it (the speaking
     indicator is on time; a span started or stopped by the captions is moved 2 s and 1 s earlier);
   - a slot where one person has 80% of the named sound is theirs: all of its sound goes to them, also
     from before the captions came on, and while someone else talks in another slot;
   - in a slot shared by several people, each stretch goes to the person it is named after; an unnamed one
     to `audio/unknown.wav`.
   The second real test's sound, split again this way: 15 s in `Akarshit.wav` (was 2), 27 s in
   `Subrat Parida.wav` (was 4), nothing in `unknown.wav` (was 33). Any old run folder can be split again:
   `python3 bot/voicefiles.py output/<run folder>` (the old files go to `audio/old-split/`).
3. **The live stream** (`--per-person-audio`): `bot/voices.py` gives each piece to a person, 2.5 s later
   (captions come after the words):
   - the people talking around that moment are those whose captions changed from 0.2 s before to 1.5 s
     after it (or whom the indicator showed speaking);
   - a slot with sound keeps its person while that person is still talking;
   - a slot that goes quiet for 0.3 s forgets its person (the next voice in it may be someone else);
   - a slot with sound and no person goes to a talking person no other sounding slot has: first one whose
     new caption block starts about then (a new speaker), then the one whose caption grows most. When
     several slots start together, the loudest goes first. When it is a tie, nobody is guessed.
   All files start at the same moment and have silence where that person said nothing, so they line up
   with `audio/mixed.wav` and `screen.mp4`.
4. With `--per-person-audio`: the named pieces go to xebit as one stream per person (same frame format
   as the mixed stream, `docs/SPEC.md` 3.2, with the person's id and name, the same ids as the join/leave
   webhooks), with the time the sound was heard. Sound nobody can be given is not sent. If 10 s of sound
   can't be given to anyone, or no sound comes from the slots for 15 s, the bot goes back to the mixed
   stream for the rest of the call (`bot.log`: `switching to the mixed stream`).

### How well it works (fake meeting)

`tests/test_docker_round2.py`: the fake meeting plays Asha (440 Hz), Ravi (660 Hz) and Meera (880 Hz)
through 3 slots, taking 3-second turns with short pauses, and shows captions about 0.5 s behind the voice
(the "Captions and translation" version, no speaking indicator, so only captions say who talks). In one
run: 176 pieces given to a person, 36 to nobody; of the loud pieces in each person's file, Asha 69 of 70
were her tone, Ravi 51 of 51, Meera 56 of 58. `speakers.jsonl` found every turn's start and end.

### Where it fails, or may fail in a real call

1. **The first moments of each turn often go to `unknown`:** until the new speaker's first caption shows,
   nobody can be named (a few hundred milliseconds per turn).
2. **Meet's caption lag is not known.** The windows above assume captions come within about 1.5 s of the
   words. If Meet is slower, more sound goes to `unknown`; if a speaker's last corrections come long after
   they stop, the next speaker's first words in the same slot may go to them.
3. **Two people at once:** each gets a slot, and the naming works when one of them was already talking. If
   both start at the same moment, the loudest slot goes to the one whose caption grows most; a tie gives
   `unknown`.
4. **Back-to-back speakers with no pause in the same slot:** the slot stays with the first speaker until
   their captions stop changing (about the caption lag).
5. **Captions only name people in the language they are set to** (English by default for the bot). Speech
   Meet can't caption (another language, noise, music) gives no caption, so its sound goes to `unknown`.
6. **Names:** captions show display names. Two people with the same name can't be told apart. A name that
   is not in the People list yet gets `id: null` in `captions.jsonl` and `speakers.jsonl`, and its sound
   goes to a file with that name.
7. **The bot's own long actions** (starting a screen share, opening the People panel) used to pause the
   reading; now the bot keeps reading while it waits and while the mouse moves, but a page that takes long
   to load in the share window can still delay it by a second or two.

### Option 2 (not built): code in Meet's page world

The plan says: describe, don't build. It would be:

- after admission, inject a script into Meet's page world (`Runtime.evaluate` in the main world) that wraps
  `RTCPeerConnection` events (`track`) or reads Meet's own participant-to-stream map (each person's audio
  carries a per-sender id that only page JavaScript can read), to know which slot carries which person;
- then record each track directly, with no guessing.

Risks: Meet can see changed built-ins and any timing or object it doesn't expect; that is exactly what
can get a guest refused or the call degraded, and Google changes Meet's internals often, so it would
break without warning. Before admission it would be far too risky; after admission it is still a risk
for a live demo. Captions do not need it.

## 4. What to check in a real call

Use the Round 2 test in `MORNING.md`. In short:

1. `./try.sh <meet link>`, two people (you and a phone) talking in turns, then a little at the same time.
2. `bot.log`: `captions: turned on (...)`. If it says `could not be turned on`, send `dom/` and the
   screenshots.
3. `captions.jsonl`: your words, under your names. `speakers.jsonl`: `started`/`stopped` at your turns.
4. `audio/<your name>.wav` and `audio/<phone name>.wav`: each should hold only that person's voice;
   `audio/unknown.wav` should be short bits. Listen to them next to `audio/mixed.wav`.
5. `./try.sh --per-person-audio <meet link>`: `try.sh` shows `sound sent: ... (You 40, Phone 25)`.
6. Without the flag, the mixed stream (`meeting-mix`) must work as before.

## 5. Setting the indicator's class name (optional)

The default is `speaking,.wEsLMd`: `wEsLMd` is the class Meet's tiles had while a person spoke in the second
real test (2026-11-05; `gjg47c` while silent). Google renames such classes from time to time. If
`speakers.jsonl` stops showing `"source": "indicator"`, find the new class in `dom/` (a class that only the
speaking person's tile has) and add this line to `.env`, then restart the service (new bots get it):

```bash
echo 'BOT_ENV_SPEAKING_CLASSES=speaking,.abc123' >> .env
```

```bash
docker compose up -d
```

A name starting with a dot means "a tile that contains an element with this class". Several names can be
given, separated by commas. Any `BOT_ENV_<NAME>=value` line in `.env` reaches every bot as `<NAME>=value`.
