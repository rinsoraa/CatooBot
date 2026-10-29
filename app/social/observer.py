"""Group Observer (v0.9 §24-§28/§45/§77-§80).

Every ``batch_size`` external messages, the observer takes one *structured*
pass over the group: it decides whether the character has a natural reason to
join — ``reply`` / ``observe`` / ``ignore`` / ``defer``. It never generates the
final reply (that is the main chat model's job, v0.9 §40/§80) and it never emits a
hidden chain of thought (v0.9 §39).

When the AI engine is unavailable the observer falls back to the rule-based
:class:`~app.social.relevance.RelevanceEvaluator` + thresholds, so the feature
degrades gracefully instead of vanishing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ai.errors import AIError
from app.ai.models import AIRequest, ChatMessage
from app.config.settings import SocialConfig
from app.social.models import (
    PARTICIPATION_REASONS,
    GroupMessage,
    ParticipationDecision,
)
from app.utils.logger import redact

OBSERVER_PROMPT = """你正在帮助一个虚拟角色判断"是否应该自然加入当前群聊"。
你判断的是"这个角色此刻有没有自然参与这段对话的理由"，而不是"模型要不要生成回复"。

综合以下信息判断：
- 最近群聊内容与发言者
- 当前话题
- 角色当前活动与关注点
- 角色兴趣
- 角色与参与者的关系
- 相关长期记忆
- 是否已经有人在充分讨论、插入是否会突兀

只输出 JSON，不要输出思维过程，格式：
{"decision":"reply|observe|ignore|defer","reason_code":"...","confidence":0.0,"topic":"...","response_goal":"简短说明打算自然说什么","target_message_ids":[]}

reason_code 只能取：
topic_interest / current_activity_relevance / memory_relevance / helpful_contribution /
shared_interest / humor_opportunity / user_relationship /
no_relevance / already_discussed / conversation_too_fast / low_contribution_value / poor_timing
"""


class GroupObserver:
    def __init__(
        self,
        *,
        config: SocialConfig,
        engine: Any = None,
        relevance: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._engine = engine
        self._relevance = relevance
        self._log = logger or logging.getLogger("CatooBot.Social")

    # ---------------------------------------------------------------- decide

    async def observe(
        self,
        *,
        group_id: str,
        batch: list[GroupMessage],
        recent_context: list[GroupMessage],
        character_block: str,
        topic: str,
    ) -> ParticipationDecision:
        """Decide whether to join, given the accumulated external messages."""
        if self._engine is not None and getattr(self._engine, "enabled", False):
            decision = await self._model_decision(
                group_id, batch, recent_context, character_block, topic
            )
            if decision is not None:
                return decision
        return await self._rule_decision(batch, topic)

    async def _model_decision(
        self,
        group_id: str,
        batch: list[GroupMessage],
        recent_context: list[GroupMessage],
        character_block: str,
        topic: str,
    ) -> ParticipationDecision | None:
        prompt = "\n\n".join(
            [
                OBSERVER_PROMPT,
                f"当前话题：{topic or '（未确定）'}",
                character_block,
                self._transcript(batch, title="本批新消息"),
                self._transcript(recent_context, title="最近上下文"),
                "请输出 JSON：",
            ]
        )
        request = AIRequest(
            messages=[ChatMessage.user(prompt)],
            temperature=0.1,
            # reasoning models burn output tokens on thinking before the JSON;
            # a 300 cap truncated every response (finish=length) and the
            # observer silently degraded to rules on every call.
            max_tokens=900,
            # Tag the call so the model page can break out observer latency /
            # tokens instead of lumping it under an empty purpose.
            metadata={"purpose": "social_observer"},
        )
        if self._config.decision_model:
            request = request.with_model(self._config.decision_model)
        try:
            response = await self._engine.chat(request)
        except AIError as exc:
            self._log.warning("[Social] observer model failed, using rules: %s", exc)
            return None
        status, parsed = self._parse(response.content)
        if status != "ok" or parsed is None:
            # Same three-state contract as memory extraction: a *parse* failure
            # logs the model's raw output (first 200 chars) so a prompt drift
            # is visible immediately instead of silently degrading to rules.
            excerpt = redact(" ".join((response.content or "").split())[:200])
            self._log.warning("[Social] observer returned no valid JSON（前 200 字）：%s", excerpt)
            return None
        return self._from_json(parsed, [m.message_id for m in batch])

    async def _rule_decision(self, batch: list[GroupMessage], topic: str) -> ParticipationDecision:
        if self._relevance is None:
            return ParticipationDecision(decision="ignore", reason_code="no_relevance")
        # Minimal character signals when the engine is offline (tests / fallback).
        scores = await self._relevance.evaluate(
            messages=batch,
            topic=topic,
            interests=[],
            activity_text="",
            focus="",
        )
        t = self._config.thresholds
        if (
            scores.topic_relevance >= t.topic_relevance
            and scores.contribution_value >= t.contribution_value
            and scores.social_fit >= t.social_fit
        ):
            return ParticipationDecision(
                decision="reply",
                reason_code="topic_interest",
                confidence=max(scores.topic_relevance, scores.contribution_value),
                topic=topic,
                target_message_ids=[m.message_id for m in batch],
                scores=scores,
            )
        if scores.topic_relevance >= 0.4:
            # Mid-range relevance with too little contribution/social-fit to
            # reply *now*: defer rather than ignore. This is the *offline*
            # fallback only — with the AI engine on, poor_timing comes from the
            # model's JSON, whose surface is governed by the prompt, not here.
            return ParticipationDecision(
                decision="defer",
                reason_code="poor_timing",
                confidence=scores.topic_relevance,
                topic=topic,
                scores=scores,
            )
        return ParticipationDecision(
            decision="ignore",
            reason_code="no_relevance",
            confidence=0.0,
            topic=topic,
            scores=scores,
        )

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _transcript(messages: list[GroupMessage], *, title: str, limit: int = 20) -> str:
        lines = [f"{title}："]
        for message in list(messages)[-limit:]:
            speaker = (
                "（我）"
                if message.is_bot_message
                else (message.nickname or f"用户{message.user_id}")
            )
            lines.append(f"{speaker}：{message.content}")
        return "\n".join(lines)

    @staticmethod
    def _parse(content: str) -> tuple[str, dict[str, Any] | None]:
        """Tolerant JSON extraction from the model reply.

        Returns ``(status, data)``:

        * ``parse_failed`` — no JSON / invalid JSON / top-level not a dict;
        * ``ok`` — a usable decision object.
        """
        text = (content or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return "parse_failed", None
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return "parse_failed", None
        if not isinstance(data, dict):
            return "parse_failed", None
        return "ok", data

    @staticmethod
    def _from_json(data: dict[str, Any], target_ids: list[str]) -> ParticipationDecision:
        decision = str(data.get("decision", "ignore"))
        if decision not in ("reply", "observe", "ignore", "defer"):
            decision = "ignore"
        reason = str(data.get("reason_code", "no_relevance"))
        if reason not in PARTICIPATION_REASONS:
            reason = "no_relevance"
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return ParticipationDecision(
            decision=decision,  # type: ignore[arg-type]
            reason_code=reason,
            confidence=max(0.0, min(1.0, confidence)),
            topic=str(data.get("topic", "")),
            response_goal=str(data.get("response_goal", ""))[:120],
            target_message_ids=[str(i) for i in (data.get("target_message_ids") or target_ids)],
        )
