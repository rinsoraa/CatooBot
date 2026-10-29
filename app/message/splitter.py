"""Split long replies into QQ-sized messages.

Rules: never cut inside a UTF-8 character (Python str slicing is codepoint-
safe by construction), prefer paragraph (\n\n) and line (\n) boundaries, and
keep newlines intact. v0.2 uses a simple safe length cut; platform-specific
limits can refine ``max_length`` per adapter later.
"""

from __future__ import annotations

DEFAULT_MAX_LENGTH = 2000

_BREAK_POINTS = ("\n\n", "\n", " ", "，", "。", "！", "？", "；")


def split_message(text: str, max_length: int = DEFAULT_MAX_LENGTH) -> list[str]:
    """Split ``text`` into chunks of at most ``max_length`` characters."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remainder = text
    while len(remainder) > max_length:
        cut = _find_cut(remainder, max_length)
        chunks.append(remainder[:cut].strip("\n"))
        remainder = remainder[cut:]
    if remainder.strip():
        chunks.append(remainder.strip("\n"))
    return [c for c in chunks if c]


def _find_cut(text: str, max_length: int) -> int:
    """Best split point <= max_length: paragraph > line > punctuation > hard cut."""
    window = text[: max_length + 1]
    for pattern in _BREAK_POINTS:
        index = window.rfind(pattern)
        if index > 0:
            return index + len(pattern)
    return max_length  # hard cut at a codepoint boundary (never mid-character)
