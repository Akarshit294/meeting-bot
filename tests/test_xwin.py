"""The PNG encoder the bot uses for screen grabs and camera pictures (no image library in the image)."""
import struct
import zlib

import xwin


def test_png_roundtrip():
    w, h = 3, 2
    rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255] * h)
    data = xwin.png(w, h, rgb)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    length = struct.unpack(">I", data[8:12])[0]
    assert data[12:16] == b"IHDR" and struct.unpack(">II", data[16:24]) == (w, h)
    idat_at = data.index(b"IDAT")
    size = struct.unpack(">I", data[idat_at - 4:idat_at])[0]
    raw = zlib.decompress(data[idat_at + 4:idat_at + 4 + size])
    assert raw == b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    assert length == 13
