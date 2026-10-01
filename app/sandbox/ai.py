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

PROMPT = """你在扮演角色「罐头」的生活决策助手。
根据她当前的状态，从候选动作里挑一个最符合她性格的选择。
只输出 JSON，不要解释：
{"decision":"switch|continue","action":"候选id","reason_codes":["简短原因"]}

候选动作只能用下面列表里的 id。她懒散、爱打游戏（Minecraft/单机）、爱甜食和可乐、
是个夜猫子、有只猫叫小喵。"""


class SandboxAIDecider:
    """Callable(payload) -> SandboxDecision | None, built on the shared engine."""

    def __init__(self, engine: Any, *, timeout: float = 20.0) -> None:
        self._engine = engine
        self._timeout = timeout

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
        request = AIRequest(
            messages=[ChatMessage.user(f"{PROMPT}\n\n{prompt}")],
            temperature=0.2,
            metadata={"purpose": "sandbox"},
            max_tokens=500,
        )
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
