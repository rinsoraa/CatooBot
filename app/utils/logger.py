"""Logging setup for CatooBot.

Two rendering paths, one record stream:

* **console** — colourised, aligned, with narration lines (the character's
  interior life, see :mod:`app.utils.narrator`) printed as a readable story;
* **file** (``logs/catoobot.log``) — plain ``[HH:MM:SS] [LEVEL] [Name] msg``
  with ANSI stripped, so grepping stays easy.

Both handlers use UTF-8 so emoji / Chinese text survive on Windows consoles.
Sensitive values (tokens) must pass through :func:`redact` before formatting.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

from app.config.settings import PROJECT_ROOT
from app.utils import console

_PLAIN_FMT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
_DATEFMT = "%H:%M:%S"

#: level -> (colour, short badge)
_LEVEL_STYLE: dict[int, tuple[str, str]] = {
    logging.DEBUG: ("bright_black", "DEBUG"),
    logging.INFO: ("bright_blue", "INFO "),
    logging.WARNING: ("bright_yellow", "WARN "),
    logging.ERROR: ("bright_red", "ERROR"),
    logging.CRITICAL: ("bright_red", "FATAL"),
}

#: logger name -> accent colour (keeps subsystems visually distinct)
_NAME_STYLE: tuple[tuple[str, str], ...] = (
    ("CatooBot.OneBot", "bright_cyan"),
    ("CatooBot.AI", "bright_magenta"),
    ("CatooBot.Character", "magenta"),
    ("CatooBot.Behavior", "bright_blue"),
    ("CatooBot.World", "yellow"),
    ("CatooBot.Memory", "bright_green"),
    ("CatooBot.Tool", "cyan"),
    ("CatooBot.Agent", "bright_magenta"),
    ("CatooBot.Plugin", "green"),
    ("CatooBot.Web", "bright_white"),
    ("CatooBot.Response", "blue"),
    ("CatooBot.Router", "bright_white"),
    ("CatooBot", "bright_white"),
)

_configured = False

_SECRET_PATTERN = re.compile(r"(access[_-]?token|authorization|bearer)[=: ]+\S+", re.IGNORECASE)

_ANSI_PATTERN = re.compile(r"\033\[[0-9;]*m")


def redact(text: str) -> str:
    """Mask credential-like fragments so they never reach log output."""
    return _SECRET_PATTERN.sub(r"\1=***", text)


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            try:
                record.msg = redact(str(record.msg) % record.args)
                record.args = None
            except (TypeError, ValueError):
                record.msg = redact(str(record.msg))
                record.args = None
        else:
            record.msg = redact(str(record.msg))
        return True


class _PlainFilter(logging.Filter):
    """Keeps the log file free of styling and marks narration lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "console_only", False):
            # Model thinking is shown in the terminal only; hidden
            # chain-of-thought is never persisted (project policy).
            return False
        text = _ANSI_PATTERN.sub("", str(record.msg))
        channel = getattr(record, "channel", "")
        if getattr(record, "narrate", False) and channel not in ("", "panel", "blank"):
            text = f"[{channel}] {text.strip()}"
        record.msg = text
        record.args = None
        return True


def _name_color(name: str) -> str:
    for prefix, color in _NAME_STYLE:
        if name.startswith(prefix):
            return color
    return "bright_white"


def _short_name(name: str) -> str:
    if name == "CatooBot":
        return "CatooBot"
    return name.split(".")[-1]


class ConsoleFormatter(logging.Formatter):
    """Colourised console output; narration lines render as a story."""

    def __init__(self, datefmt: str = _DATEFMT) -> None:
        super().__init__(datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib signature
        stamp = self.formatTime(record, self.datefmt)
        message = record.getMessage()
        if getattr(record, "narrate", False):
            return message
        level_name = "INFO"
        level_color = "bright_blue"
        for level, (color, badge) in _LEVEL_STYLE.items():
            if record.levelno >= level:
                level_name, level_color = badge, color
        name = _short_name(record.name)
        head = (
            console.paint(stamp, "gray")
            + " "
            + console.paint(f"{level_name:5}", level_color)
            + " "
            + console.paint(f"{name:9}", _name_color(record.name))
        )
        body = message
        if record.levelno >= logging.ERROR:
            body = console.paint(message, "bright_red")
        elif record.levelno >= logging.WARNING:
            body = console.paint(message, "yellow")
        elif record.levelno <= logging.DEBUG:
            body = console.paint(message, "gray")
        line = f"{head} {console.paint('│', 'bright_black')} {body}"
        if record.exc_info:
            trace = self.formatException(record.exc_info)
            line += "\n" + console.paint(trace, "bright_red")
        if record.stack_info:
            line += "\n" + console.paint(self.formatStack(record.stack_info), "gray")
        return line


def setup_logging(
    level: str = "INFO",
    log_dir: str | Path = "logs",
    *,
    color: bool = True,
    narrate: bool = True,
    narrate_thinking: bool = True,
) -> None:
    """Initialize root logging once. Subsequent calls adjust level/switches."""
    global _configured

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass

    # ``color: true`` means "use colours when the terminal supports them" —
    # piping to a file or a CI log stays plain (NO_COLOR also wins).
    console.set_color_enabled(None if color else False)
    from app.utils.narrator import configure as configure_narrator

    configure_narrator(narrate, thinking=narrate_thinking)

    if not _configured:
        directory = Path(log_dir)
        if not directory.is_absolute():
            directory = PROJECT_ROOT / directory
        directory.mkdir(parents=True, exist_ok=True)

        redactor = _RedactingFilter()
        plain = _PlainFilter()

        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(ConsoleFormatter(datefmt=_DATEFMT))
        stdout_handler.addFilter(redactor)

        file_handler = logging.handlers.RotatingFileHandler(
            directory / "catoobot.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_PLAIN_FMT, datefmt=_DATEFMT))
        file_handler.addFilter(redactor)
        file_handler.addFilter(plain)

        root = logging.getLogger()
        root.addHandler(stdout_handler)
        root.addHandler(file_handler)
        root.setLevel(logging.INFO)
        # websockets internals are chatty at DEBUG; keep them at INFO unless asked
        logging.getLogger("websockets").setLevel(logging.INFO)
        _configured = True

    resolved = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger().setLevel(resolved)
    if resolved <= logging.DEBUG:
        logging.getLogger("websockets").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, e.g. ``get_logger("OneBot")``."""
    return logging.getLogger("CatooBot" if name == "CatooBot" else f"CatooBot.{name}")
