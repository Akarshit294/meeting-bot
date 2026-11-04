from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import statistics
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from Xlib import X
from Xlib import display as xdisplay
from Xlib.ext import xtest

DEFAULT_RECORDINGS = Path("/mouse/recordings.jsonl")
SEQUENCES_FILE = "meet-sequences.jsonl"
LAYOUT_FILE = "meet-layout.json"
RECORDER_PAGE = Path(__file__).with_name("recorder.html")
MEET_PAGE = Path(__file__).with_name("meet-recorder.html")
MEET_STEPS = ["mic", "camera", "join"]

Event = tuple[float, str, float, float]

VIEWPORT_ORIGIN_JS = """() => {
    const side = (outerWidth - innerWidth) / 2;
    return [screenX + side, screenY + outerHeight - innerHeight - side];
}"""


def fitts_index(distance: float, w: float, h: float, angle: float) -> float:
    width = abs(math.cos(angle)) * w + abs(math.sin(angle)) * h
    return math.log2(distance / width + 1)


class Recording:
    def __init__(self, trial: dict):
        self.samples = [s for s in trial["samples"] if s[0] >= 0]
        self.during = trial.get("during", [])
        self.after = trial.get("after", [])
        self.down, self.up = trial["down"], trial["up"]
        start = trial.get("start") or {"x": self.samples[0][1], "y": self.samples[0][2]}
        self.x0, self.y0 = start["x"], start["y"]
        self.target = trial["target"]
        self.w, self.h = self.target["w"], self.target["h"]
        self.u = (self.down["x"] - self.target["x"]) / self.w
        self.v = (self.down["y"] - self.target["y"]) / self.h
        dx, dy = self.down["x"] - self.x0, self.down["y"] - self.y0
        self.distance = math.hypot(dx, dy)
        self.angle = math.atan2(dy, dx)
        self.index = fitts_index(self.distance, self.w, self.h, self.angle)
        self.onset = next((t for t, x, y in self.samples if math.hypot(x - self.x0, y - self.y0) > 3), None)
        press = (self.down["x"], self.down["y"])
        self.arrive = max((t for t, x, y in self.samples if math.dist((x, y), press) > 2), default=self.onset)


def read_trials(path: Path) -> list[dict]:
    trials = []
    for line in path.read_text().splitlines():
        try:
            trial = json.loads(line)
        except ValueError:
            continue
        if isinstance(trial, dict) and trial.get("hit") and trial.get("pointerType") != "touch":
            trials.append(trial)
    return trials


def load_recordings(path: Path) -> list[Recording]:
    recordings = []
    for trial in read_trials(path):
        try:
            recording = Recording(trial)
        except (KeyError, TypeError, IndexError, ZeroDivisionError):
            continue
        if recording.distance >= 20 and recording.onset is not None and recording.onset < recording.down["t"]:
            recordings.append(recording)
    return recordings


def fit_fitts(recordings: list[Recording]) -> tuple[float, float]:
    if len(recordings) >= 5:
        try:
            b, a = statistics.linear_regression([r.index for r in recordings],
                                                [r.arrive - r.onset for r in recordings])
        except statistics.StatisticsError:
            b = 0
        if b > 0:
            return a, b
    return 0.0, 1.0


def presses_miss(layout: dict, seq: dict, margin: float = 2) -> str | None:
    if layout.get("viewport") != seq["layout"].get("viewport"):
        return f"the page is {layout.get('viewport')}, the recording was made on {seq['layout'].get('viewport')}"
    for click in seq["clicks"]:
        box = layout.get(click["action"])
        if not box:
            return f"no {click['action']} button to click (already off?)"
        x, y, w, h = box
        for edge, what in (("down", "press"), ("up", "release")):
            px, py = click[edge]["x"], click[edge]["y"]
            if not (x + margin <= px <= x + w - margin and y + margin <= py <= y + h - margin):
                return f"your {click['action']} {what} at ({px:.0f}, {py:.0f}) would miss the button, which is at {box}"
    return None


def read_sequences(path: Path) -> list[dict]:
    sequences = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            seq = json.loads(line)
            if [c["action"] for c in seq["clicks"]] == MEET_STEPS and all("up" in c for c in seq["clicks"]) \
                    and seq["start"] and seq["layout"]:
                sequences.append(seq)
        except (ValueError, KeyError, TypeError):
            continue
    return sequences


class HumanMouse:
    def __init__(self, recordings: Path | None = DEFAULT_RECORDINGS):
        self.recordings = load_recordings(recordings) if recordings and recordings.exists() else []
        self.fitts = fit_fitts(self.recordings)
        recorded = read_sequences(recordings.with_name(SEQUENCES_FILE)) if recordings else []
        layout = recordings.with_name(LAYOUT_FILE) if recordings else None
        current = json.loads(layout.read_text()) if layout and layout.exists() else None
        self.sequences = [s for s in recorded if not current or not presses_miss(current, s)]
        self.outdated_sequences = len(recorded) - len(self.sequences)
        self.sequence = random.choice(self.sequences) if self.sequences else None
        self.origin = self.viewport = None
        self.vnc = None
        self._display = None

    def calibrate(self, page) -> None:
        self.origin = tuple(page.evaluate(VIEWPORT_ORIGIN_JS))
        self.viewport = page.evaluate("[innerWidth, innerHeight]")

    def play_sequence(self, seq: dict, origin: tuple[float, float], before: str | None = None) -> None:
        ox, oy = origin
        plan = [(t, "move", ox + x, oy + y) for t, x, y in seq["samples"]]
        for click in seq["clicks"]:
            plan += [(click["down"]["t"], "down", ox + click["down"]["x"], oy + click["down"]["y"]),
                     (click["up"]["t"], "up", ox + click["up"]["x"], oy + click["up"]["y"])]
        if before:
            cut = next(c["down"]["t"] for c in seq["clicks"] if c["action"] == before)
            plan = [event for event in plan if event[0] < cut]
        self._play(plan)

    def click(self, rect: tuple[float, float, float, float], recording: Recording | None = None) -> None:
        start = self.position()
        if recording or self.recordings:
            self._play(self._replay(start, rect, recording))
        else:
            self._play(synthetic_plan(start, rect))

    def move_to(self, x: float, y: float) -> None:
        self._play([(0.0, "move", x, y)])

    def click_element(self, page, locator) -> bool:
        locator.scroll_into_view_if_needed(timeout=10_000)
        box = locator.bounding_box(timeout=10_000)
        if not box:
            return False
        left, top = self.origin or page.evaluate(VIEWPORT_ORIGIN_JS)
        self.click((left + box["x"], top + box["y"], box["width"], box["height"]))
        return True

    def position(self) -> tuple[int, int]:
        pointer = self._connect().screen().root.query_pointer()
        return pointer.root_x, pointer.root_y

    def _connect(self):
        if self._display is None:
            self._display = xdisplay.Display()
        return self._display

    def _replay(self, start: tuple[float, float], rect: tuple[float, float, float, float],
                rec: Recording | None = None) -> list[Event]:
        sx, sy = start
        x, y, w, h = rect
        distance = math.hypot(x + w / 2 - sx, y + h / 2 - sy)
        angle = math.atan2(y + h / 2 - sy, x + w / 2 - sx)

        def unlikeness(r: Recording) -> float:
            turn = abs(math.remainder(angle - r.angle, math.tau)) / math.pi
            return abs(math.log((distance + 1) / (r.distance + 1))) + 0.5 * turn + 0.3 * abs(math.log(w / r.w))

        rec = rec or random.choice(sorted(self.recordings, key=unlikeness)[:3])

        mx, my = min(6, w / 4), min(6, h / 4)
        ex = min(max(x + w * rec.u, x + mx), x + w - mx)
        ey = min(max(y + h * rec.v, y + my), y + h - my)
        travel, heading = math.hypot(ex - sx, ey - sy), math.atan2(ey - sy, ex - sx)
        scale, turn = travel / rec.distance, heading - rec.angle
        cos_t, sin_t = math.cos(turn), math.sin(turn)

        def place(px, py, ox, oy, nx, ny, k):
            dx, dy = (px - ox) * k, (py - oy) * k
            return nx + dx * cos_t - dy * sin_t, ny + dx * sin_t + dy * cos_t

        a, b = self.fitts
        new_time = max(a + b * fitts_index(travel, w, h, heading), 50)
        old_time = max(a + b * rec.index, 50)
        stretch = min(max(new_time / old_time, 0.5), 2.0)

        def retime(t: float) -> float:
            if t <= rec.onset:
                return t
            return rec.onset + (min(t, rec.arrive) - rec.onset) * stretch + max(t - rec.arrive, 0)

        plan = [(retime(t), "move", *place(px, py, rec.x0, rec.y0, sx, sy, scale)) for t, px, py in rec.samples]
        down_t = retime(rec.down["t"])
        plan.append((down_t, "down", ex, ey))

        def held(t, px, py):
            return down_t + t - rec.down["t"], *place(px, py, rec.down["x"], rec.down["y"], ex, ey, 1)

        plan += [(t, "move", px, py) for t, px, py in (held(*s) for s in rec.during)]
        t, px, py = held(rec.up["t"], rec.up["x"], rec.up["y"])
        plan.append((t, "up", px, py))
        plan += [(t, "move", px, py) for t, px, py in (held(*s) for s in rec.after)]
        return plan

    def _play(self, plan: list[Event]) -> None:
        display = self._connect()
        screen = display.screen()
        last, buttons = None, 0
        plan = sorted(plan, key=lambda event: event[0])
        if self.vnc:
            plan = press_like_a_click(plan)
        t0 = time.perf_counter()
        for t, kind, x, y in plan:
            delay = t0 + t / 1000 - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            spot = (min(max(round(x), 0), screen.width_in_pixels - 1), min(max(round(y), 0), screen.height_in_pixels - 1))
            if kind != "move":
                buttons = 1 if kind == "down" else 0
            if self.vnc:
                if spot != last or kind != "move":
                    self.vnc.pointer(*spot, buttons)
            else:
                if spot != last:
                    xtest.fake_input(display, X.MotionNotify, x=spot[0], y=spot[1])
                if kind != "move":
                    xtest.fake_input(display, X.ButtonPress if kind == "down" else X.ButtonRelease, 1)
                display.flush()
            last = spot
        if self.vnc and last and plan and plan[-1][1] != "move":
            for dx, pause in ((1, 0.02), (0, 0.03)):
                time.sleep(pause)
                self.vnc.pointer(last[0] + dx, last[1], 0)
        if not self.vnc:
            display.sync()


def press_like_a_click(plan: list[Event]) -> list[Event]:
    out, shift, pressed_at = [], 0.0, None
    for t, kind, x, y in plan:
        if kind == "down":
            pressed_at = t + shift
        elif kind == "up" and pressed_at is not None:
            shift += max(random.uniform(45, 95) - (t + shift - pressed_at), 0)
        out.append((t + shift, kind, x, y))
    return out


def synthetic_plan(start: tuple[float, float], rect: tuple[float, float, float, float]) -> list[Event]:
    sx, sy = start
    x, y, w, h = rect
    ex, ey = x + w * random.uniform(0.35, 0.65), y + h * random.uniform(0.35, 0.65)
    travel, heading = math.hypot(ex - sx, ey - sy), math.atan2(ey - sy, ex - sx)
    duration = (230 + 120 * fitts_index(travel, w, h, heading)) * random.uniform(0.85, 1.15)
    bow = travel * random.uniform(-0.06, 0.06)
    steps = max(2, round(duration / 8))
    plan = []
    for i in range(1, steps + 1):
        s = i / steps
        along = 10 * s**3 - 15 * s**4 + 6 * s**5
        side = bow * math.sin(math.pi * s)
        plan.append((s * duration, "move", sx + (ex - sx) * along - side * math.sin(heading),
                     sy + (ey - sy) * along + side * math.cos(heading)))
    down_t = duration + random.uniform(80, 200)
    plan.append((down_t, "down", ex, ey))
    plan.append((down_t + random.uniform(60, 140), "up", ex, ey))
    return plan


def peak_speed(points: list) -> float | None:
    best, i = None, 0
    for j in range(1, len(points)):
        dt = points[j][0] - points[i][0]
        if dt >= 8:
            speed = math.dist(points[i][1:], points[j][1:]) / dt
            best = speed if best is None else max(best, speed)
            i = j
    return best


def measure(trial: dict) -> dict | None:
    try:
        r = Recording(trial)
    except (KeyError, TypeError, IndexError, ZeroDivisionError):
        return None
    if r.onset is None or r.distance < 1:
        return None
    press_point = (r.down["x"], r.down["y"])
    moving = [s for s in r.samples if s[0] >= r.onset] + [[r.down["t"], *press_point]]
    movement = r.down["t"] - r.onset
    path = [(r.x0, r.y0)] + [(x, y) for _, x, y in r.samples] + [press_point]
    length = sum(math.dist(p, q) for p, q in zip(path, path[1:]))
    ux, uy = (press_point[0] - r.x0) / r.distance, (press_point[1] - r.y0) / r.distance
    reach = max((x - r.x0) * ux + (y - r.y0) * uy for _, x, y in r.samples)
    return {
        "movement": movement,
        "peak_speed": peak_speed(moving),
        "efficiency": r.distance / length if length else None,
        "overshoot": max(reach - r.distance, 0),
        "dwell": r.down["t"] - r.arrive,
        "press": r.up["t"] - r.down["t"],
        "rate": 1000 * (len(moving) - 1) / movement if movement >= 20 else None,
    }


METRICS = [
    ("movement time (ms)", "movement", "{:.0f}"),
    ("peak speed (px/ms)", "peak_speed", "{:.2f}"),
    ("path efficiency (1 = straight)", "efficiency", "{:.2f}"),
    ("overshoot past the press (px)", "overshoot", "{:.1f}"),
    ("pause before pressing (ms)", "dwell", "{:.0f}"),
    ("press duration (ms)", "press", "{:.0f}"),
    ("pointer events per second", "rate", "{:.0f}"),
]


def summarise(values: list, fmt: str) -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    if len(values) == 1:
        return fmt.format(values[0])
    q1, median, q3 = statistics.quantiles(values, n=4)
    return f"{fmt.format(median)} [{fmt.format(q1)}-{fmt.format(q3)}]"


def serve_recorder(recordings: Path, port: int) -> None:
    recordings.parent.mkdir(parents=True, exist_ok=True)
    sequences, layout = recordings.with_name(SEQUENCES_FILE), recordings.with_name(LAYOUT_FILE)
    pages = {"/": RECORDER_PAGE.read_bytes(), "/meet": MEET_PAGE.read_bytes()}
    lock = threading.Lock()

    def describe_click(trial: dict) -> str:
        m = measure(trial)
        return f": moved {m['movement']:.0f} ms, pressed {m['press']:.0f} ms" if m else ""

    def describe_sequence(seq: dict) -> str:
        return ": " + ", ".join(f"{c.get('action')} pressed at {c.get('down', {}).get('t', 0):.0f} ms" for c in seq.get("clicks", []))

    stores = {"/trials": (recordings, "click", describe_click), "/sequences": (sequences, "sequence", describe_sequence)}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in pages:
                return self._send("text/html; charset=utf-8", pages[path])
            if path == "/layout" and layout.exists():
                return self._send("application/json", layout.read_bytes())
            self.send_error(404)

        def do_POST(self):
            if self.path not in stores:
                return self.send_error(404)
            store, noun, describe = stores[self.path]
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 20_000_000:
                return self.send_error(413)
            try:
                entry = json.loads(self.rfile.read(length))
                entry["received_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            except (ValueError, TypeError):
                return self.send_error(400)
            with lock:
                with store.open("a") as f:
                    f.write(json.dumps(entry, separators=(",", ":")) + "\n")
                total = len(store.read_text().splitlines())
            print(f"{noun} {total} saved{describe(entry)}", flush=True)
            self._send("application/json", json.dumps({"saved": total}).encode())

        def _send(self, content_type: str, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    print(f"Mouse recorder: http://localhost:{port} (buttons, saved to {recordings})\n"
          f"                http://localhost:{port}/meet (the Meet sequence, saved to {sequences}"
          + ("" if layout.exists() else "; capture the layout first with meet_bot.py --dry-run --save-layout") + ")",
          flush=True)
    try:
        ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass


def path_gaps(original: list, replayed: list, shift: float) -> list[float]:
    times = [t for t, _, _ in original]
    gaps = []
    for t, x, y in replayed:
        t -= shift
        i = bisect.bisect_left(times, t)
        if 0 < i < len(times):
            (t0, x0, y0), (t1, x1, y1) = original[i - 1], original[i]
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            gaps.append(math.dist((x, y), (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)))
    return gaps


def median_max(values: list[float]) -> str:
    return f"median {statistics.median(values):.1f}, max {max(values):.1f}" if values else "-"


def compare_sequences(original: dict, replayed: dict) -> list[str]:
    shift = replayed["clicks"][0]["down"]["t"] - original["clicks"][0]["down"]["t"]
    sent = []
    for t, x, y in original["samples"]:
        if not sent or sent[-1][1:] != (round(x), round(y)):
            sent.append((t, round(x), round(y)))
    seen = [(t - shift, round(x), round(y)) for t, x, y in replayed["samples"]]
    lags, j = [], 0
    for t, x, y in sent:
        for k in range(j, min(j + 4, len(seen))):
            if seen[k][1:] == (x, y):
                lags.append(abs(seen[k][0] - t))
                j = k + 1
                break
    timing = [abs(r[edge]["t"] - shift - o[edge]["t"])
              for o, r in zip(original["clicks"], replayed["clicks"]) for edge in ("down", "up") if edge in r]
    registered = sum("up" in c for c in replayed["clicks"])
    return [f"{len(lags)}/{len(sent)}",
            f"median {statistics.median(lags):.1f}, max {max(lags):.1f}" if lags else "-",
            f"{registered}/{len(original['clicks'])}, off by max {max(timing):.1f} ms"]


def demo(recordings: Path, clicks: int) -> None:
    import meet_bot
    from playwright.sync_api import sync_playwright

    mouse = HumanMouse(recordings)
    print(f"replaying {len(mouse.recordings)} recorded clicks from {recordings}" if mouse.recordings
          else f"nothing recorded at {recordings}: using synthetic movement", flush=True)
    sequences = []
    with sync_playwright() as p:
        browser = meet_bot.launch(p)
        page = browser.new_context(no_viewport=True, locale="en-US").new_page()

        if mouse.sequences:
            page.goto(MEET_PAGE.as_uri() + "?observe=1")
            left, top = page.evaluate(VIEWPORT_ORIGIN_JS)
            for seq in mouse.sequences[:3]:
                page.evaluate("layout => init(layout)", seq["layout"])
                arena = page.locator("#arena").bounding_box()
                origin = (left + arena["x"], top + arena["y"])
                mouse.move_to(origin[0] + seq["start"]["x"], origin[1] + seq["start"]["y"])
                page.wait_for_timeout(300)
                done = page.evaluate("window.sequences.length")
                page.evaluate("begin()")
                mouse.play_sequence(seq, origin)
                page.wait_for_function(f"window.sequences.length > {done}", timeout=15_000)
                sequences.append((seq, page.evaluate("window.sequences.at(-1)")))

        page.goto(RECORDER_PAGE.as_uri() + "?observe=1")
        target = page.locator("#target")
        left, top = page.evaluate(VIEWPORT_ORIGIN_JS)
        arena = page.locator("#arena").bounding_box()
        ax, ay = left + arena["x"], top + arena["y"]

        def trial(click, preset=None) -> dict:
            done = page.evaluate("window.trials.length")
            page.evaluate("rect => showTarget(rect)", preset)
            click()
            page.wait_for_function(f"window.trials.length > {done}", timeout=15_000)
            return page.evaluate("window.trials.at(-1)")

        copies = []
        for rec in [r for r in mouse.recordings if 0 <= r.x0 < arena["width"] and 0 <= r.y0 < arena["height"]][:6]:
            mouse.move_to(ax + rec.x0, ay + rec.y0)
            page.wait_for_timeout(300)
            t = rec.target
            seen = trial(lambda: mouse.click((ax + t["x"], ay + t["y"], t["w"], t["h"]), recording=rec), t)
            copies.append((rec, Recording(seen)))
        adapted = [trial(lambda: mouse.click_element(page, target)) for _ in range(clicks)]
        contrast = [trial(target.click)]
        shot = recordings.parent / "demo.png"
        page.screenshot(path=str(shot))
        browser.close()

    if sequences:
        print("\nYour Meet sequences replayed exactly, as Chrome saw them (aligned on the first press):")
        print(f"{'':12}{'samples on the same pixel':>27}{'their timing, ms off':>28}{'clicks, timing':>30}")
        for i, (seq, seen) in enumerate(sequences, 1):
            cells = compare_sequences(seq, seen)
            print(f"{'sequence ' + str(i):12}{cells[0]:>27}{cells[1]:>28}{cells[2]:>30}")

    if copies:
        print("\nExact copies (your original start point and button), as Chrome saw them:")
        print(f"{'':10}{'movement ms':>22}{'press ms':>18}{'path difference px':>28}")
        for i, (rec, seen) in enumerate(copies, 1):
            moved = f"{rec.down['t'] - rec.onset:.0f} -> {seen.down['t'] - seen.onset:.0f}"
            pressed = f"{rec.up['t'] - rec.down['t']:.0f} -> {seen.up['t'] - seen.down['t']:.0f}"
            gap = median_max(path_gaps(rec.samples, seen.samples, seen.onset - rec.onset))
            print(f"{'click ' + str(i):10}{moved:>22}{pressed:>18}{gap:>28}")

    columns = [
        (f"your recordings (n={len(mouse.recordings)})", [measure(t) for t in read_trials(recordings)] if recordings.exists() else []),
        (f"replayed in Chrome (n={clicks})", [measure(t) for t in adapted]),
        ("one Playwright click", [measure(t) for t in contrast]),
    ]
    print(f"\nButtons at new spots, median [middle half]:\n{'':32}" + "".join(f"{name:>30}" for name, _ in columns))
    for label, key, fmt in METRICS:
        cells = [summarise([m[key] for m in ms if m], fmt) for _, ms in columns]
        print(f"{label:32}" + "".join(f"{cell:>30}" for cell in cells))
    print(f"\nscreenshot of the replayed paths: {shot}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record human mouse movements and replay them as X input.")
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="serve the recorder page")
    record.add_argument("--port", type=int, default=8080)
    record.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    replay = commands.add_parser("demo", help="replay recorded clicks in Chrome and compare")
    replay.add_argument("--clicks", type=int, default=12)
    replay.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    args = parser.parse_args()
    if args.command == "record":
        serve_recorder(args.recordings, args.port)
    else:
        demo(args.recordings, args.clicks)


if __name__ == "__main__":
    main()
