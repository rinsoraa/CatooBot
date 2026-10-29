"""Terminal styling helpers: ANSI colours, CJK-aware width, boxes.

Everything here is *operator-facing only*: colours never touch QQ messages and
never reach the log file (the file handler strips ANSI). Colour support is
resolved once and honours ``NO_COLOR`` / ``FORCE_COLOR`` plus Windows console
capabilities, so piping the output to a file stays clean.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from typing import Any, TextIO

RESET = "\033[0m"

#: named styles -> SGR codes (bright variants included)
STYLES: dict[str, str] = {
    "reset": RESET,
    "bold": "\033[1m",
    "dim": "\033[2m",
    "italic": "\033[3m",
    "underline": "\033[4m",
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "bright_black": "\033[90m",
    "gray": "\033[90m",
    "bright_red": "\033[91m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m",
    "bright_white": "\033[97m",
}

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_ENABLED: bool | None = None


# ------------------------------------------------------------------ support


def _enable_windows_vt() -> bool:
    """Ask the Windows console for ANSI support (no-op elsewhere)."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        # ``ctypes.windll`` exists only on Windows; typeshed gates it on
        # sys.platform, so a direct attribute access fails mypy on Linux CI.
        # getattr() stays dynamic (Any) and reads the same at runtime.
        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 - windows-only loader
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 - styling must never break the app
        return False


def _detect(stream: TextIO | None = None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    target = stream if stream is not None else sys.stdout
    try:
        if not target.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    return _enable_windows_vt()


def set_color_enabled(value: bool | None) -> None:
    """Pin colour support (``None`` restores auto-detection)."""
    global _ENABLED
    _ENABLED = value


def color_enabled(stream: TextIO | None = None) -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = _detect(stream)
    return _ENABLED


def paint(text: Any, *styles: str) -> str:
    """Wrap text in the given styles (no-op when colour is unavailable)."""
    body = str(text)
    if not styles or not color_enabled():
        return body
    prefix = "".join(STYLES.get(name, "") for name in styles)
    if not prefix:
        return body
    return f"{prefix}{body}{RESET}"


def strip(text: str) -> str:
    """Remove ANSI escapes (used by the log-file handler)."""
    return _ANSI_RE.sub("", text)


# ------------------------------------------------------------------- width


def char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 2
    if char in ("\u200b", "\ufe0f"):  # zero-width space / variation selector
        return 0
    return 1


def visible_width(text: str) -> int:
    """Display width of a string, ignoring ANSI escapes and CJK being double."""
    return sum(char_width(char) for char in strip(text))


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    """Cut to ``width`` display columns (CJK aware)."""
    if visible_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    limit = max(1, width - visible_width(ellipsis))
    for char in strip(text):
        size = char_width(char)
        if used + size > limit:
            break
        out.append(char)
        used += size
    return "".join(out) + ellipsis


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad to a display width, counting CJK characters as two columns."""
    gap = max(0, width - visible_width(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def wrap(text: str, width: int) -> list[str]:
    """Greedy CJK-aware wrap (no word-splitting subtleties needed here)."""
    lines: list[str] = []
    current = ""
    used = 0
    for char in text:
        if char == "\n":
            lines.append(current)
            current, used = "", 0
            continue
        size = char_width(char)
        if used + size > width and current:
            lines.append(current)
            current, used = "", 0
        current += char
        used += size
    lines.append(current)
    return lines


# ------------------------------------------------------------------- boxes


def box(
    title: str,
    lines: list[str],
    *,
    accent: str = "cyan",
    width: int | None = None,
    padding: int = 1,
) -> list[str]:
    """A rounded box with a title, CJK-aligned and colour-aware.

    Returns the raw lines (already painted) so callers can prefix a gutter.
    """
    content = max(
        24,
        width or max([visible_width(line) for line in lines] + [visible_width(title)]),
    )
    inner = content + padding * 2
    header = f"─ {title} "
    dashes = "─" * max(0, inner - visible_width(header))
    out = [paint("╭" + header + dashes + "╮", accent)]
    for line in lines:
        body = " " * padding + pad(line, content) + " " * padding
        out.append(paint("│", accent) + body + paint("│", accent))
    out.append(paint("╰" + "─" * inner + "╯", accent))
    return out


def banner(
    title: str,
    subtitle: str,
    *,
    version: str = "",
    author: str = "",
    width: int = 46,
    accent: str = "bright_cyan",
) -> list[str]:
    """The startup artwork: title, version line and the developer signature."""
    inner = width
    top = "╔" + "═" * inner + "╗"
    bottom = "╚" + "═" * inner + "╝"
    lines = [
        top,
        "║" + pad(paint(title, "bold", accent), inner, "center") + "║",
    ]
    if version:
        lines.append("║" + pad(paint(version, "bright_white"), inner, "center") + "║")
    if subtitle:
        lines.append("║" + pad(paint(subtitle, "gray"), inner, "center") + "║")
    if author:
        signature = paint(f"developer · {author}", "bright_magenta")
        lines.append("║" + pad(signature, inner, "center") + "║")
    lines.append(bottom)
    return lines
