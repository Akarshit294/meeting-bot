"""What the bot's browser shows a website: Chrome version, time zone, fonts, permissions, graphics, every
Chrome switch, the request headers. Runs inside the bot image with the bot's own launch() and the same
private window as the Meet join. Opens only a local page (127.0.0.1) and chrome://version: never Meet or any
Google page, so it can run as often as needed. Why it exists: docs/why-meet-refused-the-bot.md.

    docker run --rm --shm-size 2g -v "$PWD/scripts:/scripts:ro" --entrypoint bash meeting-bot:latest \
      /scripts/browser_check.sh

Prints JSON: "summary" first (the values that mattered on 2026-11-11), then everything."""
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading

sys.path.insert(0, "/app")
import meet_bot  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HEADERS = []
PAGE = b"<!doctype html><html><head><title>probe</title></head><body>probe</body></html>"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        HEADERS.append({"path": self.path, **dict(self.headers)})
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Accept-CH", "Sec-CH-UA-Platform-Version, Sec-CH-UA-Full-Version-List, Sec-CH-UA-Arch, "
                                      "Sec-CH-UA-Bitness, Sec-CH-UA-Model")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


JS = r"""async () => {
 const o = {}, n = navigator;
 o.webdriver = n.webdriver; o.userAgent = n.userAgent; o.platform = n.platform; o.vendor = n.vendor;
 o.languages = n.languages; o.hardwareConcurrency = n.hardwareConcurrency; o.deviceMemory = n.deviceMemory;
 o.plugins = Array.from(n.plugins).map(p => p.name); o.pdfViewerEnabled = n.pdfViewerEnabled; o.maxTouchPoints = n.maxTouchPoints;
 try { o.uaBrands = n.userAgentData.brands.map(b => b.brand + ' ' + b.version);
       o.uaHigh = await n.userAgentData.getHighEntropyValues(['platform','platformVersion','architecture','bitness','model','fullVersionList','formFactors']); }
 catch (e) { o.uaHigh = String(e) }
 const ro = Intl.DateTimeFormat().resolvedOptions(); o.timeZone = ro.timeZone; o.tzOffsetMin = new Date().getTimezoneOffset(); o.intlLocale = ro.locale;
 o.screen = {w: screen.width, h: screen.height, availW: screen.availWidth, availH: screen.availHeight, colorDepth: screen.colorDepth};
 o.window = {outerW: outerWidth, outerH: outerHeight, innerW: innerWidth, innerH: innerHeight, x: screenX, y: screenY, dpr: devicePixelRatio};
 o.windowChrome = typeof window.chrome === 'object' ? Object.keys(window.chrome) : null;
 try { const gl = document.createElement('canvas').getContext('webgl'); const d = gl.getExtension('WEBGL_debug_renderer_info');
       o.webgl = {vendor: gl.getParameter(d.UNMASKED_VENDOR_WEBGL), renderer: gl.getParameter(d.UNMASKED_RENDERER_WEBGL)} } catch (e) { o.webgl = String(e) }
 try { o.webgpuAdapter = navigator.gpu ? !!(await navigator.gpu.requestAdapter()) : 'no navigator.gpu' } catch (e) { o.webgpuAdapter = String(e) }
 try { o.mediaDevices = (await n.mediaDevices.enumerateDevices()).map(d => d.kind) } catch (e) { o.mediaDevices = String(e) }
 o.voices = speechSynthesis.getVoices().length; await new Promise(r => setTimeout(r, 1000)); o.voicesAfter1s = speechSynthesis.getVoices().length;
 o.notification = typeof Notification !== 'undefined' ? Notification.permission : null;
 for (const name of ['notifications', 'camera', 'microphone']) { try { o['perm_' + name] = (await n.permissions.query({name})).state } catch (e) { o['perm_' + name] = String(e) } }
 try { o.storageQuotaMB = Math.round((await n.storage.estimate()).quota / 1048576) } catch (e) {}
 o.connection = n.connection ? {type: n.connection.effectiveType, rtt: n.connection.rtt, downlink: n.connection.downlink} : null;
 const fonts = ['Arial','Helvetica','Times New Roman','Courier New','Verdana','Georgia','Roboto','Google Sans','Noto Sans','DejaVu Sans','Liberation Sans','Ubuntu','Cantarell','Segoe UI'];
 const s = document.createElement('span'); s.textContent = 'mmmmmmmmmmlliWW'; s.style.fontSize = '72px'; document.body.appendChild(s);
 const base = {}; for (const b of ['monospace', 'serif', 'sans-serif']) { s.style.fontFamily = b; base[b] = s.offsetWidth }
 o.fontsFound = fonts.filter(f => ['monospace', 'serif', 'sans-serif'].some(b => { s.style.fontFamily = `"${f}",${b}`; return s.offsetWidth !== base[b] }));
 s.remove();
 let cdp = false; const e = new Error(); Object.defineProperty(e, 'stack', {get() { cdp = true; return '' }}); console.debug(e);
 await new Promise(r => setTimeout(r, 200)); o.devtoolsConsoleProbeFired = cdp;
 o.historyLength = history.length; o.doNotTrack = n.doNotTrack;
 o.darkMode = matchMedia('(prefers-color-scheme: dark)').matches;
 return o;
}"""


def chrome_cmdlines():
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            parts = [x.decode() for x in open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0") if x]
        except OSError:
            continue
        if parts and "chrome" in parts[0] and not any(x.startswith("--type=") for x in parts):
            out.append(parts)
    return out


def main():
    srv = socketserver.TCPServer(("127.0.0.1", 8765), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    args = meet_bot.parse_args(["https://meet.google.com/abc-defg-hij", "--name", "Maya"])
    with sync_playwright() as p:
        browser = meet_bot.launch(p, args)
        # The same private window as the Meet join (bot/meet_bot.py, main).
        context = browser.new_context(no_viewport=True, locale="en-US", permissions=["camera", "microphone"])
        page = context.new_page()
        page.goto("http://127.0.0.1:8765/first")
        page.goto("http://127.0.0.1:8765/second")
        fp = page.evaluate(JS)
        version = context.new_page()
        try:
            version.goto("chrome://version")
            ver = version.inner_text("body")
        except Exception as exc:  # noqa: BLE001
            ver = f"could not open: {exc}"
        try:
            fonts = len(subprocess.run(["fc-list"], capture_output=True, text=True, check=True).stdout.splitlines())
        except (OSError, subprocess.CalledProcessError):
            fonts = None
        high = fp.get("uaHigh") if isinstance(fp.get("uaHigh"), dict) else {}
        summary = {
            "chrome": next((b["version"] for b in high.get("fullVersionList", []) if b["brand"] == "Google Chrome"), None),
            "time_zone": fp.get("timeZone"),
            "fonts_installed": fonts,
            "common_fonts_found": fp.get("fontsFound"),
            "graphics": (fp.get("webgl") or {}).get("renderer") if isinstance(fp.get("webgl"), dict) else fp.get("webgl"),
            "webdriver": fp.get("webdriver"),
            "notifications": fp.get("notification"),
            "accept_language": next((h.get("Accept-Language") for h in reversed(HEADERS)), None),
        }
        print(json.dumps({"summary": summary, "launch_flags_from_meet_flags": meet_bot.meet_flags(args),
                          "chrome_main_process": chrome_cmdlines(), "request_headers": HEADERS, "page_sees": fp,
                          "chrome_version_page": ver}, indent=1))
        browser.close()


main()
