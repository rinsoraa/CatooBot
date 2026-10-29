"""Expression / 口癖 learner (Task 22): mine short phrases from group speech.

The admission rules (design ③④) are deterministic and reject *before* anything
is stored, with a reason code — the same discipline as the rest of the codebase
("never silent"). Only group-internal, lexical, short phrases from real people
pass; the result is stored with full provenance by :class:`ExpressionStore`.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.config.settings import ExpressionConfig
from app.expression.store import REJECT, ExpressionStore
from app.memory.patterns import _FORBIDDEN_PATTERNS

logger = logging.getLogger("CatooBot.Expression")

#: a candidate must contain at least one CJK or latin letter to count as lexical
_LEXICAL = re.compile(r"[\u4e00-\u9fffA-Za-z]")
#: candidates with these are dropped: digits, @, URLs (entity / noise)
_ENTITY = re.compile(r"[\d@]")
#: a small attack/discrimination list — never learn these
_SENSITIVE = re.compile(r"(傻逼|傻x|傻叉|妈的|草泥马|滚蛋|去死|废物|智障|弱智|贱人|婊子)")
#: split on anything that is not a CJK char / latin letter (punctuation, spaces)
_SPLIT = re.compile(r"[^\u4e00-\u9fffA-Za-z]+")

_MAX_WORD_LEN = 12


class ExpressionLearner:
    def __init__(
        self,
        store: ExpressionStore,
        config: ExpressionConfig,
        *,
        clock: Any = time.time,
        metrics: Any = None,
    ) -> None:
        self._store = store
        self._config = config
        self._clock = clock
        self._metrics = metrics
        self._seen: set[str] = set()

    def _candidates(self, text: str) -> list[str]:
        """Split a message into 2..12-char lexical candidates (word kind)."""
        out: list[str] = []
        for segment in _SPLIT.split(text):
            segment = segment.strip()
            if not 2 <= len(segment) <= _MAX_WORD_LEN:
                continue
            if not _LEXICAL.search(segment):
                continue
            if segment not in out:
                out.append(segment)
        return out

    def _reject(self, text: str, *, sender_is_bot: bool) -> str | None:
        """Return the reason to drop the whole message, or None to proceed."""
        if sender_is_bot:
            return "sender_is_bot"
        if not _LEXICAL.search(text):
            return "no_lexical_content"
        return None

    def _reject_candidate(self, candidate: str) -> str | None:
        if not _LEXICAL.search(candidate):
            return "no_lexical_content"
        if _ENTITY.search(candidate):
            return "contains_entity"
        if _SENSITIVE.search(candidate):
            return "sensitive"
        if _FORBIDDEN_PATTERNS.search(candidate):
            return "forbidden"
        return None

    async def learn_message(
        self,
        *,
        group_id: str,
        user_id: str,
        message_id: str,
        text: str,
        sender_is_bot: bool = False,
    ) -> list[tuple[str, str]]:
        """Learn from one group message; returns [(pattern, reason|'learned')]."""
        if not self._config.enabled:
            return []
        scope_key = f"group:{group_id}"
        if self._config.groups and group_id not in self._config.groups:
            return []

        whole = self._reject(text, sender_is_bot=sender_is_bot)
        if whole is not None:
            return []

        results: list[tuple[str, str]] = []
        now = int(self._clock())
        for candidate in self._candidates(text):
            reason = self._reject_candidate(candidate)
            if reason is not None:
                results.append((candidate, REJECT[reason]))
                if self._metrics is not None:
                    self._metrics.inc(f"expressions_rejected_{reason}")
                continue
            # same message must not re-learn a phrase twice
            if candidate in self._seen:
                results.append((candidate, "duplicate"))
                continue
            self._seen.add(candidate)
            await self._store.learn(
                scope_key=scope_key,
                pattern=candidate,
                kind="word",
                user_id=user_id,
                message_id=message_id,
                text=text[:80],
                now=now,
            )
            if self._metrics is not None:
                self._metrics.inc("expressions_learned")
            results.append((candidate, "learned"))
        await self._store.evict(scope_key)
        return results
