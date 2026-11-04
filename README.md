# meeting-bot

A self-hosted meeting bot service. It joins a Google Meet call as a guest, speaks with the voice of
a web page we give it, hears the call, shows a second web page as its screen share, and reports who
joins and leaves. Its API copies the small part of Recall.ai's API that xebit-api uses, so xebit-api
can switch to it by changing a base URL and a key.

- What it must do: `docs/SPEC.md`
- How it is being built: `docs/PLAN.md`
- Where the work stands: `STATUS.md`
- How to test it on a Mac: `MORNING.md` (written by the build)
- The proven guest join it starts from: `bot/` (`bot/JOIN_TEST_RUN.md`)
- The proven signed-in join, for later: `spikes/signed-in-join/`
- Every real-Meet test run and its result: `evidence/README.md`
