"""Terminal helpers: colour detection (NO_COLOR aware, Windows VT mode) and Unicode fallbacks."""

from __future__ import annotations

import os
import sys
from typing import IO, Optional

CODES = {
    "reset": "0", "bold": "1", "dim": "2", "italic": "3", "underline": "4",
    "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "grey": "90",
    "bg_red": "41", "bg_yellow": "43", "bg_blue": "44", "bg_green": "42", "white": "97", "black": "30",
}


def _enable_windows_vt(stream: IO) -> bool:
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def supports_color(stream: Optional[IO] = None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
        return True
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if os.name == "nt":
        return _enable_windows_vt(stream)
    return True


def supports_unicode(stream: Optional[IO] = None) -> bool:
    stream = stream or sys.stdout
    enc = (getattr(stream, "encoding", None) or "ascii").lower()
    try:
        "✖▲●│─·→".encode(enc)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


class Style:
    def __init__(self, color: bool, unicode: bool):
        self.color = color
        self.unicode = unicode

    def __call__(self, text: str, *names: str) -> str:
        if not self.color or not names:
            return text
        seq = ";".join(CODES[n] for n in names if n in CODES)
        return "\x1b[%sm%s\x1b[0m" % (seq, text)

    def sym(self, fancy: str, plain: str) -> str:
        return fancy if self.unicode else plain


def style_for(stream: Optional[IO] = None, color: Optional[bool] = None) -> Style:
    stream = stream or sys.stdout
    return Style(supports_color(stream) if color is None else color, supports_unicode(stream))
