"""The manager: the Recall-compatible REST API (docs/SPEC.md 2) and the bots' internal endpoints."""
from __future__ import annotations

import hmac
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .runner import Runner, RunnerError, bot_token
from .store import FINAL, GONE, IN_CALL, Bot, Store, new_bot_id

log = logging.getLogger("manager")

TEST_PAGES = Path(__file__).with_name("testpages")
WATCH_INTERVAL = 3.0
# Our own per-bot switches (`xbot_options` in the create body; ./try.sh sends them, xebit-api never does).
OPTION_KEYS = ("per_person_audio", "camera_pictures", "save_dom", "save_media", "record_screen", "captions")


def safe_url(url: Any) -> str:
    """A URL from xebit, for logs: host and path only (the query carries tokens; CLAUDE.md rule 6)."""
    try:
        parts = urlparse(str(url))
        return f"{parts.scheme}://{parts.hostname or ''}{f':{parts.port}' if parts.port else ''}{parts.path}"
    except ValueError:
        return "<bad url>"


def is_meet_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.hostname == "meet.google.com" and parts.path.strip("/") != ""


def is_web_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


async def json_body(request: Request, required: bool = True) -> dict:
    raw = await request.body()
    if not raw.strip():
        if required:
            raise bad_request("a JSON body is required")
        return {}
    try:
        import json

        body = json.loads(raw)
    except ValueError:
        raise bad_request("the body is not valid JSON") from None
    if not isinstance(body, dict):
        raise bad_request("the body must be a JSON object")
    return body


def parse_endpoints(recording_config: Any) -> list[dict]:
    if recording_config is None:
        return []
    if not isinstance(recording_config, dict):
        raise bad_request("recording_config must be an object")
    endpoints = recording_config.get("realtime_endpoints") or []
    if not isinstance(endpoints, list):
        raise bad_request("recording_config.realtime_endpoints must be a list")
    parsed = []
    for item in endpoints:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise bad_request("each realtime endpoint needs a url")
        if item.get("type") not in ("webhook", "websocket"):
            raise bad_request("realtime endpoint type must be webhook or websocket")
        events = item.get("events") or []
        if not isinstance(events, list):
            raise bad_request("realtime endpoint events must be a list")
        parsed.append({"type": item["type"], "url": item["url"], "events": [str(e) for e in events]})
    return parsed


def create_app(settings: Settings, runner: Runner, store: Store | None = None, watch: bool = True) -> FastAPI:
    store = store or Store(settings.state_file)
    runner.token_for = lambda bot_id: bot_token(store.secret, bot_id)
    stop_watch = threading.Event()

    def watch_once() -> None:
        """Containers that stopped: `call_ended` becomes `done`; anything not finished becomes `fatal`."""
        for bot in store.all():
            if not bot.container:
                continue
            state = runner.state(bot)
            if state == "running":
                continue
            if bot.latest == "call_ended":
                store.add_status(bot.id, "done", message="the bot's container stopped")
            elif bot.leave_requested and bot.latest not in FINAL and bot.latest not in IN_CALL:
                # Asked to leave before it was let in, and it stopped: that is the leave, not a failure.
                store.add_status(bot.id, "call_ended", "bot_received_leave_call", "stopped before joining")
                store.add_status(bot.id, "done", message="the bot's container stopped")
            elif bot.latest not in FINAL:
                store.add_status(bot.id, "fatal", "bot_errored", "the bot's container stopped without reporting")
                log.warning("bot %s: container stopped without reporting -> fatal/bot_errored", bot.id)
            if state == "exited":
                runner.remove(bot)
            store.update(bot.id, container=None)

    def watch_loop() -> None:
        while not stop_watch.wait(WATCH_INTERVAL):
            try:
                watch_once()
            except Exception:  # never let the watcher die
                log.exception("watcher")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        thread = None
        if watch:
            thread = threading.Thread(target=watch_loop, name="watcher", daemon=True)
            thread.start()
        if settings.test_meet_url:
            log.warning("TEST MODE: every bot opens %s instead of its Meet link", safe_url(settings.test_meet_url))
        yield
        stop_watch.set()

    app = FastAPI(title="meeting-bot manager", lifespan=lifespan)
    app.state.store = store
    app.state.runner = runner
    app.state.settings = settings
    app.state.watch_once = watch_once

    def require_key(authorization: str | None = Header(default=None)) -> None:
        scheme, _, key = (authorization or "").partition(" ")
        if scheme.lower() != "token" or not hmac.compare_digest(key.strip().encode(), settings.api_key.encode()):
            raise HTTPException(status_code=401, detail="Invalid or missing API key (header: Authorization: Token <key>)")

    def find(bot_id: str) -> Bot:
        bot = store.get(bot_id)
        if bot is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return bot

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_key)])

    def route(method: str, path: str, **kwargs):
        """Register a path with and without its trailing slash (xebit-api sends the slash)."""
        def wrap(fn):
            api.add_api_route(path, fn, methods=[method], **kwargs)
            api.add_api_route(path.rstrip("/"), fn, methods=[method], include_in_schema=False, **kwargs)
            return fn
        return wrap

    @route("POST", "/bot/", status_code=201)
    async def create_bot(request: Request):
        body = await json_body(request)
        meeting_url = body.get("meeting_url")
        if not is_meet_url(meeting_url):
            raise bad_request("meeting_url must be a Google Meet link like https://meet.google.com/abc-defg-hij")
        bot_name = body.get("bot_name") or "Meeting Bot"
        if not isinstance(bot_name, str) or not bot_name.strip():
            raise bad_request("bot_name must be a non-empty string")
        metadata = body.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise bad_request("metadata must be an object")
        output_media = body.get("output_media") or {}
        if not isinstance(output_media, dict):
            raise bad_request("output_media must be an object")
        camera_url = None
        if output_media.get("camera") is not None:
            camera = output_media["camera"]
            camera_url = camera.get("config", {}).get("url") if isinstance(camera, dict) else None
            if not is_web_url(camera_url):
                raise bad_request("output_media.camera.config.url must be an http(s) URL")
        endpoints = parse_endpoints(body.get("recording_config"))
        # Our own extension (./try.sh): per-bot switches. xebit-api never sends it.
        raw_options = body.get("xbot_options") if isinstance(body.get("xbot_options"), dict) else {}
        options = {k: bool(raw_options[k]) for k in OPTION_KEYS if k in raw_options}
        if store.active_count() >= settings.max_bots:
            raise HTTPException(status_code=429, detail=f"Too many bots: {settings.max_bots} are already running (MAX_BOTS)")

        bot = store.add(Bot(id=new_bot_id(), meeting_url=meeting_url, bot_name=bot_name.strip(), metadata=metadata,
                            camera_url=camera_url, endpoints=endpoints, options=options))
        log.info("bot %s: create for %s as %r, camera page %s, %d endpoints", bot.id, safe_url(meeting_url), bot.bot_name,
                 safe_url(camera_url) if camera_url else "none", len(endpoints))
        try:
            started = await run_blocking(runner.start, bot, bot_token(store.secret, bot.id))
        except RunnerError as err:
            store.add_status(bot.id, "fatal", "bot_errored", str(err)[:300])
            log.error("bot %s: %s", bot.id, err)
            raise HTTPException(status_code=503, detail=f"Could not start the bot: {err}") from None
        store.update(bot.id, container=started.container, live_view_url=started.live_view_url)
        store.add_status(bot.id, "joining_call")
        return find(bot.id).public()

    @route("GET", "/bot/")
    def list_bots(status: str | None = None):
        bots = [b for b in store.all() if status is None or b.latest == status]
        return {"count": len(bots), "next": None, "previous": None, "results": [b.public() for b in bots]}

    @route("GET", "/bot/{bot_id}/")
    def get_bot(bot_id: str):
        return find(bot_id).public()

    def request_leave(bot: Bot) -> None:
        if bot.latest in GONE:
            return
        # Kept, and given to the bot in the answer to its next status report: a leave is never lost.
        store.update(bot.id, leave_requested=True)
        if not bot.container:
            return
        for attempt in (1, 2):
            try:
                runner.command(bot, "leave", timeout=5)
                return
            except RunnerError as err:
                if attempt == 2:
                    # The control server isn't up (the container is still starting): stop the container.
                    # The bot treats SIGTERM as a leave request, like Ctrl+C in the proven runs.
                    log.info("bot %s: leave by SIGTERM (%s)", bot.id, err)
                    runner.stop(bot)
                else:
                    time.sleep(1)

    @route("POST", "/bot/{bot_id}/leave_call/")
    def leave_call(bot_id: str):
        request_leave(find(bot_id))
        return find(bot_id).public()

    @route("DELETE", "/bot/{bot_id}/")
    def delete_bot(bot_id: str):
        """Recall deletes a bot that hasn't joined yet. Here: the same as a leave (the record stays)."""
        request_leave(find(bot_id))
        return find(bot_id).public()

    @route("PATCH", "/bot/{bot_id}/")
    async def update_bot(bot_id: str, request: Request):
        """Accepted and ignored in v1 (xebit-api's client has update_bot, but v1 has nothing to change)."""
        bot = find(bot_id)
        await json_body(request, required=False)
        return bot.public()

    @route("POST", "/bot/{bot_id}/output_media/")
    async def start_output_media(bot_id: str, request: Request):
        bot = find(bot_id)
        body = await json_body(request)
        if "screenshare" not in body:
            if "camera" in body:
                raise bad_request("Only output_media.screenshare is supported (the camera page is set when the bot is created)")
            raise bad_request("output_media needs a screenshare")
        share = body["screenshare"]
        url = share.get("config", {}).get("url") if isinstance(share, dict) and isinstance(share.get("config"), dict) else None
        if not isinstance(share, dict) or share.get("kind", "webpage") != "webpage" or not is_web_url(url):
            raise bad_request('screenshare must be {"kind": "webpage", "config": {"url": "<http(s) URL>"}}')
        if bot.latest not in IN_CALL or not bot.container:
            raise HTTPException(status_code=409, detail=f"The bot is not in the call (status: {bot.latest or 'none'})")
        log.info("bot %s: start screen share of %s", bot.id, safe_url(url))
        try:
            # The bot answers right after its click (xebit's client gives up after 30 s).
            status, reply = await run_blocking(runner.command, bot, "screenshare", {"url": url}, 28)
        except RunnerError as err:
            raise HTTPException(status_code=502, detail=f"The bot did not answer: {err}") from None
        if status == 409:
            raise HTTPException(status_code=409, detail=reply.get("detail") or "The bot is not in the call")
        if status >= 400:
            raise HTTPException(status_code=502, detail=reply.get("detail") or f"The bot answered {status}")
        store.update(bot.id, screenshare_url=url)
        return find(bot_id).public()

    @route("DELETE", "/bot/{bot_id}/output_media/")
    async def stop_output_media(bot_id: str, request: Request):
        bot = find(bot_id)
        await json_body(request, required=False)
        if bot.container and bot.latest not in GONE:
            try:
                await run_blocking(runner.command, bot, "screenshare_stop", {}, 28)
            except RunnerError as err:
                log.info("bot %s: stop screen share: %s", bot.id, err)
        store.update(bot.id, screenshare_url=None)
        return find(bot_id).public()

    app.include_router(api)

    # ---- internal: the bot containers report here (each with its own token) ----

    def bot_from_token(bot_id: str, x_bot_token: str | None) -> Bot:
        bot = store.get(bot_id)
        if bot is None or not x_bot_token or not hmac.compare_digest(x_bot_token, bot_token(store.secret, bot_id)):
            raise HTTPException(status_code=401, detail="bad bot token")
        return bot

    @app.get("/internal/bots/{bot_id}/config", include_in_schema=False)
    def bot_config(bot_id: str, x_bot_token: str | None = Header(default=None)):
        bot = bot_from_token(bot_id, x_bot_token)
        return {
            "bot_id": bot.id,
            "meeting_url": bot.meeting_url,
            "bot_name": bot.bot_name,
            "camera_url": bot.camera_url,
            "endpoints": bot.endpoints,
            "meet_url_override": settings.test_meet_url,
            "options": bot.options,
        }

    @app.post("/internal/bots/{bot_id}/status", include_in_schema=False)
    async def bot_status(bot_id: str, request: Request, x_bot_token: str | None = Header(default=None)):
        bot = bot_from_token(bot_id, x_bot_token)
        body = await json_body(request)
        if body.get("run_dir"):
            store.update(bot.id, run_dir=str(body["run_dir"]))
        code = body.get("code")
        if not code:
            return {"accepted": False, "leave": bot.leave_requested}
        accepted = store.add_status(bot.id, str(code), body.get("sub_code"), str(body.get("message") or "")[:500])
        log.info("bot %s: status %s%s%s", bot.id, code, f" [{body.get('sub_code')}]" if body.get("sub_code") else "",
                 "" if accepted else " (ignored: not allowed after %s)" % bot.latest)
        return {"accepted": accepted, "leave": bot.leave_requested}

    # ---- no auth ----

    @app.get("/health")
    def health():
        return {"ok": True}

    from . import testreceiver

    def run_dir_of(bot_id: str) -> str | None:
        bot = store.get(bot_id)
        return f"output/{bot.run_dir}" if bot and bot.run_dir else None

    def media_files_of(bot_id: str) -> str | None:
        """The bot's list of recordings with their lengths (media-files.txt, written before `done`)."""
        bot = store.get(bot_id)
        if not bot or not bot.run_dir:
            return None
        try:
            return (settings.output_dir / Path(bot.run_dir).name / "media-files.txt").read_text()
        except OSError:
            return None

    app.include_router(testreceiver.router(require_key, run_dir_of, media_files_of))

    if TEST_PAGES.is_dir():
        app.mount("/test", StaticFiles(directory=TEST_PAGES, html=True), name="test")

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    return app


async def run_blocking(fn, *args):
    import anyio.to_thread

    return await anyio.to_thread.run_sync(lambda: fn(*args))


def main() -> None:
    import uvicorn

    from .runner import DockerRunner

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if settings.host_output_dir is None:
        log.warning("HOST_OUTPUT_DIR is not set: bot run folders stay inside the bot containers")
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        settings.output_dir.chmod(0o777)  # bot containers run as a non-root user
    except OSError:
        pass
    app = create_app(settings, DockerRunner(settings))
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
