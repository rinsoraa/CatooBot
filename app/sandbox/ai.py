"""AI tie-break adapter (v2.0 §46/§124/§194): only for ambiguous choices.

The model receives the candidate list and returns a **structured decision**;
the validator still has the final word. Any failure returns None and the
deterministic choice stands. AI never writes state directly (§125).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ai.errors import AIError
from app.ai.models import AIRequest, ChatMessage

logger = logging.getLogger("CatooBot.Sandbox.AI")

#: generic decision-prompt scaffold — the character line is injected from the
#: CharacterDefinition (never hardcoded here)
PROMPT_TEMPLATE = """你正在扮演角色「{name}」的生活决策助手。
根据她当前的状态，从候选动作里挑一个最符合她性格的选择。
只输出 JSON，不要解释：
{{"decision":"switch|continue","action":"候选id","reason_codes":["简短原因"]}}

候选动作只能用下面列表里的 id。{character_line}"""


class SandboxAIDecider:
    """Callable(payload) -> SandboxDecision | None, built on the shared engine."""

    def __init__(self, engine: Any, *, timeout: float = 20.0, model: str = "") -> None:
        self._engine = engine
        self._timeout = timeout
        #: 可选钉住的裁决模型（配置 sandbox.decision_model / models.decision）。
        #: 微决策是高频简单选择，钉到轻模型能让「思考」的占用可预期（2026-10-05 复盘：
        #: 该旋钮此前被忽略，裁决每次都从主力模型开始、被预算截断后白白级联降级）。
        self._model = model
        #: injected by the runtime from the CharacterDefinition
        self._character_line = ""
        self._name = ""

    def set_character_context(self, *, name: str, traits: list[str], pet_name: str = "") -> None:
        """One line of who she is — built from the seed, not from literals."""
        parts = [trait for trait in traits[:4] if trait]
        line = "她" + "、".join(parts) if parts else ""
        if pet_name:
            line += f"，有只{pet_name}"
        self._character_line = line + "。"

    async def __call__(self, payload: dict[str, Any]) -> Any:
        return await self._decide(payload)

    async def _decide(self, payload: dict[str, Any]) -> Any:
        if self._engine is None or not getattr(self._engine, "enabled", False):
            return None
        options = payload.get("options", [])
        if not options:
            return None
        prompt = (
            f"当前正在做的：{payload.get('current') or '没有'}\n"
            f"所在位置：{payload.get('space', '')}\n"
            f"候选动作：{', '.join(options)}\n请输出 JSON："
        )
        base = PROMPT_TEMPLATE.format(
            name=self._name or "角色", character_line=self._character_line
        )
        request = AIRequest(
            messages=[ChatMessage.user(f"{base}\n\n{prompt}")],
            temperature=0.2,
            metadata={"purpose": "sandbox"},
            # 推理模型先花 completion 预算思考：500 会被推理打满 → 空正文 → 级联降级。
            # 给足「推理 + 一个 JSON」的余量（上限不是目标值，模型该短还是短）。
            max_tokens=1200,
        )
        if self._model:
            request = request.with_model(self._model)
        try:
            response = await self._engine.chat(request)
        except AIError as exc:
            logger.info("[Sandbox.AI] tie-break unavailable: %s", exc)
            return None
        except Exception:  # noqa: BLE001 - never stall the world
            logger.exception("[Sandbox.AI] tie-break failed")
            return None
        data = _parse_json(response.content)
        if not data:
            return None
        from app.sandbox.models import SandboxDecision

        action = str(data.get("action", "")).strip()
        if action not in options:
            return None
        reasons = data.get("reason_codes")
        return SandboxDecision(
            decision=str(data.get("decision", "switch")),
            action_id=action,
            reason_codes=[str(r) for r in reasons] if isinstance(reasons, list) else [],
            via_ai=True,
        )


def _parse_json(content: str) -> dict[str, Any] | None:
    text = (content or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
