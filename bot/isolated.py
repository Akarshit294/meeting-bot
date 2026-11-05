"""An ISOLATED world in Meet's tab, after admission only (docs/PLAN.md task 12 and R2).

Through the DevTools protocol (`Page.createIsolatedWorld`) the bot makes its own world in Meet's tab. Code
there shares the page's elements (it can read `<audio>` and `<video>` elements and their streams) but not
its JavaScript: Meet's scripts can't see it, and nothing runs in Meet's page world. A DevTools binding
(`Runtime.addBinding`, given only to this world) carries data back to the bot.

Binding calls arrive on the bot's main thread while it waits on any Playwright call; the handlers only
queue them.
"""
from __future__ import annotations

import json
from typing import Callable

WORLD = "xbot-media"


class IsolatedWorld:
    def __init__(self, page, bindings: dict[str, Callable[[str], None]], name: str = WORLD):
        self.page = page
        self.name = name
        self.handlers = dict(bindings)
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Runtime.bindingCalled", self._on_binding)
        self.cdp.send("Runtime.enable")
        # The bindings first, so the world gets them when it is made; only that world (not Meet's) has them.
        for binding in self.handlers:
            self.cdp.send("Runtime.addBinding", {"name": binding, "executionContextName": name})
        frame_id = self.cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
        self.context = self.cdp.send("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": name})["executionContextId"]

    def _on_binding(self, event: dict) -> None:
        handler = self.handlers.get(event.get("name"))
        if handler:  # only our world has the bindings
            handler(event.get("payload", ""))

    def evaluate(self, expression: str):
        """Run code in OUR world (never Meet's) and return its JSON value."""
        result = self.cdp.send("Runtime.evaluate", {"expression": expression, "contextId": self.context,
                                                     "returnByValue": True})
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            raise RuntimeError(f"isolated world: {details.get('text')} {json.dumps(details.get('exception', {}))[:200]}")
        return result.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.cdp.detach()
        except Exception:  # noqa: BLE001
            pass
