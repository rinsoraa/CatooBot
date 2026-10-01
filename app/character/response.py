"""CharacterResponseProcessor: screen AI replies before they reach QQ.

Looks for internal-implementation leaks (project codename, protocol names,
model names, API keys, prompt dumps). On detection, logs and asks the engine
for one regeneration; persistent leaks are stripped conservatively rather
than sent. Natural-language words like "AI" / "机器人" in ordinary sentences
are NOT touched (spec v0.3 §39).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# Internal identifiers that must never appear in character speech.
_INTERNAL_TERMS = (
    "catoobot",
    "napcat",
    "onebot",
    "siliconflow",
    "workbuddy",
    "system prompt",
    "系统提示词",
    "系统提示",
    "api key",
    "apikey",
    "openai-compatible",
)

# Very narrow "operator instruction" patterns; context-aware by design.
_COMMAND_PATTERN = re.compile(
    r"^\s*/(ping|help|about|chat|clear|remember|memory|forget)\b",
    re.IGNORECASE,
)

# The history context tags stale turns with "[今天凌晨4点]"-style markers.
# Models occasionally imitate the convention and tag their *own* reply
# ("[当前] 最近在肝一个沙盒游戏…"), leaking the marker into QQ. Strip exactly
# that time-marker vocabulary at reply start; ordinary bracketed text
# ("[狗头]" and friends) does not match and is left alone.
_TAG_PREFIX = re.compile(
    r"^\s*\[(?:今天|昨天|前天|当前|此刻|刚刚|现在|\d{1,2}月\d{1,2}日)"
    r"(?:清晨|早上|上午|中午|下午|晚上|凌晨|深夜)?"
    r"(?:\d{1,2}[点时](?:\d{1,2}分)?)?\]\s*"
)


class LeakReport:
    def __init__(self, terms: list[str], command_like: bool) -> None:
        self.terms = terms
        self.command_like = command_like

    @property
    def leaked(self) -> bool:
        return bool(self.terms or self.command_like)


class CharacterResponseProcessor:
    def __init__(
        self,
        persona_provider: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """``persona_provider`` supplies extra forbidden terms (e.g. model ids)."""
        self._persona_provider = persona_provider
        self._log = logger or logging.getLogger("CatooBot.Character")

    def inspect(self, content: str) -> LeakReport:
        lowered = content.lower()
        terms = [term for term in self._forbidden_terms() if term in lowered]
        command_like = bool(_COMMAND_PATTERN.match(content))
        if terms or command_like:
            self._log.warning(
                "Response leak detected: terms=%s command_like=%s", terms, command_like
            )
        return LeakReport(terms, command_like)

    def sanitize(self, content: str) -> str:
        """Conservative last-resort cleanup: drop lines that hard-leak internals."""
        content = "\n".join(self._strip_imitated_tag(line) for line in content.splitlines())
        report = self.inspect(content)
        if not report.leaked:
            return content
        kept_lines = []
        for line in content.splitlines():
            line_report = self.inspect(line)
            if line_report.leaked and line_report.terms:
                continue
            kept_lines.append(line)
        cleaned = "\n".join(kept_lines).strip()
        return cleaned if cleaned else content  # never return empty to the user

    @staticmethod
    def _strip_imitated_tag(content: str) -> str:
        """Remove a "[当前] "-style history-marker imitation from the reply."""
        cleaned = _TAG_PREFIX.sub("", content, count=1)
        if cleaned != content:
            logging.getLogger("CatooBot.Character").warning(
                "Stripped an imitated [time] tag from the reply start"
            )
        return cleaned

    def _forbidden_terms(self) -> list[str]:
        terms = list(_INTERNAL_TERMS)
        if self._persona_provider is not None:
            try:
                terms.extend(str(t).lower() for t in self._persona_provider())
            except Exception:  # noqa: BLE001
                self._log.exception("persona_provider failed; using defaults")
        return terms
