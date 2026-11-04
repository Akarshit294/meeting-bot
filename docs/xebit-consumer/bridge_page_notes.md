# COPIED FROM xebit-api static/demo_v2-bridge.html (47f5189): every line that names Recall or its media.
# This page is the "camera page". Read these to see what it expects from the bot.

```
14:  /* ── Holding screen = the bot's camera / Output Media in the meeting ────────
40:<!-- LiveKit JS client — ONLY used in transport=livekit mode (the bridge joins the
43:     Vendored locally; untouched/ignored on the Recall Output-Media path. -->
50:       The bare demo screen on both transports; the script sizes it (1280x720 on Recall, the
76:// ─── LiveKit transport (L2b) ──────────────────────────────────────────────────
77:// transport=livekit makes this bridge join a LiveKit WebRTC room as the agent:
79://     track (instead of ctx.destination = Recall Output Media);
82://     relay as an `audio` message TAGGED with that participant (replacing getUserMedia);
85:// skipped. Absent these params everything below stays on the Recall path untouched.
88:const lkTransport = _lkParams.get("transport") || "recall";
92:const isLiveKit = lkTransport === "livekit" && !!lkUrl && !!lkToken;
105://               nothing is shrunk on the way. The shrink to 1280x720 was the blur: on a 4K
155:// LOCAL_CAPTURE_KEY for Recall's single getUserMedia mic. This replaces the module-level
163:// agent's voice (mixer -> recDest) without disturbing the live air (mixer -> ctx.destination).
194:// ─── Holding screen (the bot camera / Output Media) ───────────────────────────
197:// Recall's SEPARATE screenshare (screencast-viewer.html), not this page; so the
248://    rings — the Recall camera has no client mic). Non-wake-gated sessions keep `_wakeGated` false so
389:// On Recall this canvas IS the bot's camera, so it stays on the page, and the cost above applies
393:// stepped at the most Recall's camera sends, and redrawn only while something in it moves.
394:const HOLD_RECALL_FPS = 15;
395:function stepHoldingOnRecall() {
400:else setInterval(stepHoldingOnRecall, 1000 / HOLD_RECALL_FPS);
404:// sources — the getUserMedia mic (Recall) or a SUBSCRIBED remote human track (LiveKit) — each
406:// straight to ctx.destination / the published agent track and is NEVER wired into a capture
410:// leak path — Recall feeding the bot's OWN output media back into getUserMedia — does
411:// not happen: the old vertex Recall bridge has no echo suppression either and runs
416:// In the Recall bot's browser, ctx.destination is captured as the bot's Output Media,
448:  // Through the mixer (-> ctx.destination = the bot's output media) so the recorder can tap it.
449:  // Falls back to ctx.destination if the mixer isn't up (manual dev before start()).
450:  src.connect(mixer || ctx.destination);
539:// place with it — never a cut. Recall's
610:  // screen — and nearly transparent, because on Recall this page is the bot's camera.
790:  // `recall_audio_ws` router (`/api/ws/demo_v2/audio/{session_id}`, alongside
791:  // `/api/ws/recall/audio/…`), NOT by the demo_v2 relay router, whose socket is `/ws/demo_v2/{sid}`
844:// Called eagerly on Recall (the getUserMedia mic, UNTAGGED — the server path is unchanged), and
856:  src.connect(proc); proc.connect(zeroGain); zeroGain.connect(ctx.destination);
878:    // Untagged on Recall — the mixed server path keys off the ABSENCE of `participant`.
938:      // is where the humans read it. No-op on Recall (publishData guards on isLiveKit). NOT drawn
962:  // Bring the medium up: LiveKit joins the room (so the agent track can publish); Recall grabs
968:    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
973:  // Recall: mixer -> ctx.destination is the live air (= the bot's Output Media). LiveKit: mixer ->
991:    // recorder is Recall-only, and stop() skips remote tracks by design).
997:    mixer.connect(ctx.destination);
1031:  // Recall: `stream` is our getUserMedia mic — stop its tracks. LiveKit: `stream` is never set
1091:// The bare demo screen on both transports. LiveKit used to bake a 360px "Transcript & Chat"
1097:// SIZE. Recall: 1280x720 — there this canvas only feeds the recording. LiveKit: this canvas IS
1107:// (renderHolding lays itself out from the canvas size). Recall's is its camera: left at 1280x720.
1112:// On Recall the composite exists only to be recorded, and the recording keeps REC_OUT_FPS frames
1128:// frame cap alone would take 0.8s and heartbeats alone 8s. Recall has no live encoder on this
1139:// file coming on or going off (mediaMoving): at 10 a second its ease would read as steps. Recall
1218:  // the encoder would show up as a stuttering LIVE demo, not just a worse file. Recall's recorder
1229:// Gain applied to a human in the recording mix. Same 1.5 the Recall mic clone uses, so a
1230:// LiveKit recording sits at the same agent-vs-human balance as a Recall one.
1244:  // LiveKit + bridge-side recording ONLY. On Recall the single rig IS the getUserMedia mic, which
1260://     Recall: ONE getUserMedia mic carrying the whole mixed meeting, cloned (gain 1.5) so it
1261://     reaches ONLY recDest — never ctx.destination (the live air).
1326:  // getUserMedia before creating ctx/mixer), and the common flow — invitee already present,
1502:      // The holding screen is what shows. Recall steps it on its own timer (it is the bot's
1548:// On Recall the pipeline exists solely for the recording, so it still stops with the recorder.
1637:      // native agent tile + wake ripple. Publish on change only; internally a no-op on Recall.
1714:  // conveys agent state), just not the static placeholder avatar. Recall is untouched (its holding
1734:    // canvases are the same size on either transport) rather than re-rendered, so the recording
1737:    // that differs: on Recall it is the bot's camera, on its own timer; on LiveKit nobody sees it
1799://  LIVEKIT TRANSPORT (L2b) — ported from realtime-bridge-vertex.html's `LK` IIFE.
2037:// LiveKit data-channel wiring (no-op on Recall). The dashboard's Start-Demo button (control)
2082:// Best-effort clean stop on page teardown (Recall navigating the tab away). The server-side
2087:// Freeze watch. On Recall this page went quiet for ~3 s every ~33 s — no audio frames, no
2123:// Recall gives us no CPU figure for a bot, but a starved machine shows in two clocks this page can
2161:  // BOT MODE — the Recall bot loaded this page as Output Media. Hide the test
```
