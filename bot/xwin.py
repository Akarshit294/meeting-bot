"""Small X11 helpers (python-xlib). They act on OUR page browser's windows only, never on Meet's page.

- Chrome lists a window as a screen-share source only if it has WM_STATE = NormalState, which a window
  manager normally sets. The bot's display has no window manager, so we set it on our own window.
- Our windows are kept below Meet's window, so the bot's clicks land on Meet.
"""
from __future__ import annotations

import struct
import zlib
from typing import Iterator

from Xlib import X
from Xlib import display as xdisplay

_display = None


def _d():
    global _display
    if _display is None:
        _display = xdisplay.Display()
    return _display


def top_windows() -> Iterator:
    root = _d().screen().root
    yield from root.query_tree().children


def title(window) -> str:
    d = _d()
    try:
        prop = window.get_full_property(d.intern_atom("_NET_WM_NAME"), 0)
        if prop and prop.value:
            value = prop.value
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        name = window.get_wm_name()
        return name.decode("latin-1") if isinstance(name, bytes) else (name or "")
    except Exception:  # noqa: BLE001 - windows come and go
        return ""


def wm_class(window) -> tuple[str, str]:
    try:
        return window.get_wm_class() or ("", "")
    except Exception:  # noqa: BLE001
        return ("", "")


def describe() -> list[dict]:
    """Every top-level window (for logs)."""
    out = []
    for w in top_windows():
        try:
            geo = w.get_geometry()
            attrs = w.get_attributes()
        except Exception:  # noqa: BLE001
            continue
        out.append({"id": hex(w.id), "title": title(w), "class": list(wm_class(w)),
                    "geometry": [geo.x, geo.y, geo.width, geo.height], "mapped": attrs.map_state == X.IsViewable})
    return out


def mark_shareable(title_part: str) -> bool:
    """Set WM_STATE = NormalState on the window whose title contains `title_part`."""
    d = _d()
    atom = d.intern_atom("WM_STATE")
    found = False
    for w in top_windows():
        if title_part in title(w):
            w.change_property(atom, atom, 32, [1, 0])
            found = True
    d.sync()
    return found


def lower(class_name: str, title_parts: tuple[str, ...] = ()) -> int:
    """Put at the bottom of the stack every top-level window whose WM_CLASS names `class_name`, or whose
    title contains one of `title_parts` (Chrome's own "... is sharing a window." bar)."""
    d = _d()
    lowered = 0
    for w in top_windows():
        if class_name in wm_class(w) or (title_parts and any(part in title(w) for part in title_parts)):
            w.configure(stack_mode=X.Below)
            lowered += 1
    d.sync()
    return lowered


def png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode raw RGB rows as a PNG (no extra libraries)."""
    raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def grab_screen() -> bytes:
    """The whole display as a PNG (what the live view shows: Meet's window on top)."""
    d = _d()
    root = d.screen().root
    geo = root.get_geometry()
    image = root.get_image(0, 0, geo.width, geo.height, X.ZPixmap, 0xFFFFFFFF)
    data = image.data if isinstance(image.data, bytes) else bytes(image.data)
    # 32-bit BGRX -> RGB
    rgb = bytearray(geo.width * geo.height * 3)
    rgb[0::3] = data[2::4]
    rgb[1::3] = data[1::4]
    rgb[2::3] = data[0::4]
    return png(geo.width, geo.height, bytes(rgb))
