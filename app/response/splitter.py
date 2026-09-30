"""Natural message chunking: split one reply into chat-sized bubbles.

Unlike :mod:`app.message.splitter` (a hard length safety net for the OneBot
transport), this is a *behavioural* splitter: it prefers paragraph and sentence
boundaries, never cuts mid-word, and only splits sometimes (spec §9/§10) —
the number of bubbles varies with the content and configuration.
"""

from __future__ import annotations

import random
import re

from app.config.settings import BehaviorChunkingConfig
from app.message.splitter import split_message as hard_split

# Sentence enders, keeping Chinese punctuation ahead of ASCII.
_SENTENCE_END = re.compile(r"(?<=[。！？!?…~～])(?![。！？!?…~～])")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")

# "……我看看时间……哦对确实是八点" — a *sequence* (act, then report) reads like
# two bubbles from a real person. When the text before an ellipsis contains a
# self-action cue, split there deterministically (never by dice): the ellipsis
# IS the pause between the two messages.
_ACTION_CUE = re.compile(
    r"(?:让我|等我|我看看|我想想|让我看看|让我想想|我翻翻|我查查|我瞅瞅|"
    r"稍等|等等|看了一下|想了一下|翻了一下|查了一下|打开看了看|掐指一算)"
)
_ELLIPSIS = "……"


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p and p.strip()]
    return parts


def _split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p and p.strip()]
    return parts


def _merge_short(chunks: list[str], min_length: int, max_chunks: int) -> list[str]:
    """Merge tiny fragments so bubbles read naturally, then cap the count."""
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) < min_length:
            merged[-1] = f"{merged[-1]}\n{chunk}"
        else:
            merged.append(chunk)
    while len(merged) > max_chunks:
        tail = merged.pop()
        merged[-1] = f"{merged[-1]}\n{tail}"
    return merged


class MessageChunker:
    def __init__(
        self,
        config: BehaviorChunkingConfig,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        self._rng = rng or random.Random()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def plan(self, text: str, *, force_single: bool = False) -> list[str]:
        """Return the message bubbles for one AI reply."""
        text = text.strip()
        if not text:
            return []
        cfg = self._config
        if force_single or not cfg.enabled:
            return hard_split(text)

        paragraphs = _split_paragraphs(text)
        if cfg.paragraph_always_split and len(paragraphs) > 1:
            return [c for c in _merge_short(paragraphs, cfg.min_chunk_length, cfg.max_chunks)]

        # Action-then-report sequences always split: "等等我看看时间……哦对…" is
        # two bubbles with a real pause between them, not one.
        sequenced = self._action_sequence_split(text)
        if sequenced is not None:
            return [
                c for c in _merge_short(sequenced, cfg.min_chunk_length, cfg.max_chunks)
            ]

        sentences = _split_sentences(text)
        if len(sentences) < 2:
            return hard_split(text)
        if self._rng.random() >= cfg.chunk_probability:
            return hard_split(text)  # one bubble — most replies stay whole

        candidates: list[str] = []
        for sentence in sentences:
            if candidates and len(candidates[-1]) < cfg.min_chunk_length:
                candidates[-1] = f"{candidates[-1]}{sentence}"
            else:
                candidates.append(sentence)
        if len(candidates) < 2:
            return hard_split(text)
        return [c for c in _merge_short(candidates, cfg.min_chunk_length, cfg.max_chunks)]

    @staticmethod
    def _action_sequence_split(text: str) -> list[str] | None:
        """Split at the first ellipsis that follows a self-action cue.

        Returns ``[before(incl. ……), after]`` or None when the text is not an
        action→report sequence (e.g. plain hesitation "好像……算了吧" has no cue
        before the ellipsis and stays one bubble).
        """
        start = 0
        while True:
            idx = text.find(_ELLIPSIS, start)
            if idx == -1:
                return None
            before = text[: idx + len(_ELLIPSIS)]
            after = text[idx + len(_ELLIPSIS) :].strip()
            if _ACTION_CUE.search(before) and after:
                return [before.strip(), after]
            start = idx + len(_ELLIPSIS)
