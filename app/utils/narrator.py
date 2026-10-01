"""Narrator: the operator-facing story of what the character is doing.

This is *terminal output only*. It exists so the human running CatooBot can
watch the character think (`心理历程`), decide (`判断`), speak (`碎碎念`) and
live her world (`世界`) without opening the WebUI — and without any of it ever
reaching QQ.

Design notes:

* Narration is emitted as a normal log record flagged ``narrate=True``, so the
  rotating log file keeps a plain-text record while the console renders the
  pretty version. One switch (``logging.narrate``) silences all of it.
* The narrator never prints hidden reasoning: it shows *structured* facts the
  runtime already decided — intent class, plan steps, tool calls, verdicts,
  delays, the outgoing text. There is no chain-of-thought to leak because none
  is ever asked for or stored.
* Nothing here may raise: a broken console must not take chat down.
"""

from __future__ import annotations

import logging

from app.utils import console

#: channel -> (icon, label, accent colour)
CHANNELS: dict[str, tuple[str, str, str]] = {
    "boot": ("🚀", "启动", "bright_cyan"),
    "sense": ("📨", "感知", "bright_black"),
    "vision": ("👁", "看见", "bright_cyan"),
    "facts": ("📎", "事实", "bright_blue"),
    "reason": ("💭", "思路", "bright_magenta"),
    "audit": ("🛡", "校验", "yellow"),
    "judge": ("🚪", "判断", "bright_black"),
    "think": ("🧠", "思考", "cyan"),
    "flow": ("🎐", "心流", "blue"),
    "reply": ("💬", "碎碎念", "bright_green"),
    "mind": ("🫧", "心理", "magenta"),
    "world": ("🌍", "世界", "yellow"),
    "tool": ("🔧", "工具", "cyan"),
    "task": ("🧩", "任务", "bright_magenta"),
    "reach": ("📣", "主动", "bright_yellow"),
    "quiet": ("🌙", "静默", "bright_black"),
    "warn": ("⚠️", "异常", "bright_red"),
}

LOGGER_NAME = "CatooBot.Narration"


class Narrator:
    """Formats and emits narration lines (console-pretty, file-plain)."""

    def __init__(self, logger: logging.Logger | None = None, enabled: bool = True) -> None:
        self._log = logger or logging.getLogger(LOGGER_NAME)
        self.enabled = enabled
        #: console-only model thinking switch (never persisted)
        self.thinking_enabled = True

    # ------------------------------------------------------------- primitives

    def say(self, channel: str, text: str, *, detail: str = "", world: bool = True) -> str:
        """One narrated line: ``icon 标签 │ text  · detail``."""
        if not self.enabled:
            return ""
        icon, label, accent = CHANNELS.get(channel, ("•", channel, "white"))
        head = f"{icon} {console.paint(label, accent, 'bold')}"
        body = f" {console.paint('│', 'bright_black')} {text}"
        trailer = f" {console.paint('· ' + detail, 'gray')}" if detail else ""
        line = f"{head}{body}{trailer}"
        self._emit(channel, line)
        return line

    def note(self, channel: str, text: str, *, detail: str = "") -> None:
        """Narration without an icon gutter (for continuation lines)."""
        if not self.enabled:
            return
        _icon, _label, accent = CHANNELS.get(channel, ("•", channel, "white"))
        body = f"   {console.paint('│', accent)} {text}"
        if detail:
            body += f" {console.paint('· ' + detail, 'gray')}"
        self._emit(channel, body)

    def panel(self, title: str, lines: list[str], *, accent: str = "cyan") -> None:
        """A titled box, used for startup and the ready summary."""
        if not self.enabled or not lines:
            return
        for line in console.box(title, lines, accent=accent):
            self._emit("panel", line)

    def blank(self) -> None:
        if self.enabled:
            self._emit("blank", "")

    def _emit(self, channel: str, line: str, *, console_only: bool = False) -> None:
        try:
            self._log.info(
                "%s",
                line,
                extra={"narrate": True, "channel": channel, "console_only": console_only},
            )
        except Exception:  # noqa: BLE001 - narration is cosmetic
            pass

    def thinking(self, text: str, *, detail: str = "") -> None:
        """The model's own thinking excerpt — console only.

        Shown so the operator can watch what the model considered, but never
        written to the log file: hidden chain-of-thought is not persisted
        (project policy §113/§99). Dropped by the file handler's filter.
        """
        if not self.enabled or not getattr(self, "thinking_enabled", True):
            return
        icon, label, accent = CHANNELS["reason"]
        head = f"{icon} {console.paint(label, accent, 'bold')}"
        body = f" {console.paint('│', 'bright_black')} {text}"
        trailer = f" {console.paint('· ' + detail, 'gray')}" if detail else ""
        self._emit("reason", f"{head}{body}{trailer}", console_only=True)

    # ------------------------------------------------------------- story beats

    def boot_step(self, text: str, *, detail: str = "", ok: bool = True) -> None:
        mark = console.paint("✔", "bright_green") if ok else console.paint("✖", "bright_red")
        self.note("boot", f"{mark} {text}", detail=detail)

    def mind(
        self,
        *,
        mood: str = "",
        energy: float | None = None,
        schedule: str = "",
        activity: str = "",
        location: str = "",
        mood_source: str = "",
        detail: str = "",
    ) -> None:
        """The character's present condition — 心理历程 in one line."""
        bits: list[str] = []
        if mood:
            bits.append(f"心情 {console.paint(mood, _mood_color(mood))}")
        if energy is not None:
            bits.append(f"精力 {console.paint(_energy_bar(energy), _energy_color(energy))}")
        if schedule:
            bits.append(f"状态 {console.paint(schedule, 'bright_white')}")
        doing = activity or "发呆"
        where = f"（{location}）" if location else ""
        bits.append(f"正在 {console.paint(doing + where, 'bright_cyan')}")
        note = detail or (f"来源 {mood_source}" if mood_source else "")
        self.say("mind", " ".join(bits), detail=note)

    def thought(self, text: str, *, detail: str = "") -> None:
        self.say("think", console.paint(text, "italic"), detail=detail)

    def flow(self, text: str, *, detail: str = "") -> None:
        self.say("flow", text, detail=detail)

    def reply(self, text: str, *, index: int = 0, total: int = 1) -> None:
        marker = f"[{index}/{total}] " if total > 1 else ""
        self.say("reply", console.paint(marker + text, "bright_green"))

    def world(self, text: str, *, detail: str = "") -> None:
        self.say("world", text, detail=detail)

    def tool(self, text: str, *, detail: str = "") -> None:
        self.say("tool", text, detail=detail)

    def task(self, text: str, *, detail: str = "") -> None:
        self.say("task", text, detail=detail)

    def reach(self, text: str, *, detail: str = "") -> None:
        self.say("reach", text, detail=detail)

    def quiet(self, text: str, *, detail: str = "") -> None:
        self.say("quiet", console.paint(text, "gray"), detail=detail)

    def sense(self, text: str, *, detail: str = "") -> None:
        self.say("sense", text, detail=detail)

    def judge(self, text: str, *, detail: str = "") -> None:
        self.say("judge", text, detail=detail)

    def warn(self, text: str, *, detail: str = "") -> None:
        self.say("warn", console.paint(text, "bright_red"), detail=detail)


# ------------------------------------------------------------------ renderers


def _mood_color(mood: str) -> str:
    return {
        "cheerful": "bright_green",
        "happy": "green",
        "neutral": "bright_white",
        "quiet": "bright_black",
        "down": "bright_blue",
    }.get(mood, "bright_white")


def _energy_color(energy: float) -> str:
    if energy >= 0.7:
        return "green"
    if energy >= 0.4:
        return "yellow"
    return "bright_red"


def _energy_bar(energy: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(max(0.0, min(1.0, energy)) * width))))
    return "▰" * filled + "▱" * (width - filled)


#: Module-level narrator every subsystem may use (``bot.start`` sets enabled).
narrator = Narrator()


def narrate() -> Narrator:
    return narrator


def configure(
    enabled: bool,
    logger: logging.Logger | None = None,
    *,
    thinking: bool = True,
) -> Narrator:
    """Apply the ``logging.narrate`` / ``narrate_thinking`` switches."""
    narrator.enabled = enabled
    narrator.thinking_enabled = bool(thinking)
    if logger is not None:
        narrator._log = logger  # noqa: SLF001 - single owner of this handle
    return narrator


def plain(text: str) -> str:
    """Strip styling — used when a narration line goes into a plain channel."""
    return console.strip(text)
