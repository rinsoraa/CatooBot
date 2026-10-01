"""Tool routing and the AI tool-decision loop (spec v0.6 §10/§11/§46/§47/§50-§52).

Two independent routers, never to be confused (v0.6 §46):
``ModelRouter`` picks *which model answers*, ``ToolRouter`` picks *which tools
are relevant*. This module owns the latter plus the decision loop:

    messages → candidate tools → AI (decision) → ToolCall? → execute
             → result as untrusted reference → AI (final answer)

Every loop is bounded: ``max_calls_per_turn`` and ``max_execution_time`` come
from config and are enforced in code, never by the model.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.ai.models import AIRequest, AIResponse, ChatMessage
from app.config.settings import ToolsConfig
from app.tools.base import Tool as ToolBase
from app.tools.executor import ToolExecutor
from app.tools.models import ToolCall, ToolContext, ToolResult, ToolTrace
from app.tools.policy import ToolPolicy, TurnBudget
from app.tools.registry import ToolRegistry
from app.tools.result import guard_external

DECISION_SYSTEM = """你可以在需要时调用工具来获取准确信息。

可用工具：
{tools}

规则：
- 只在确实需要外部事实/计算时才调用；日常闲聊、情绪回应、常识问题直接正常回复。
- 一次只调用一个工具；拿到结果后再决定是否继续。
- 参数不明确时，不要猜测；改为自然地向用户提问。
- 工具不可用或失败时，**绝不能编造结果**，要如实说明。

回复格式（严格遵守，二选一）：
1) 需要工具时，只输出 JSON，不要其他文字：
{{"tool_call": {{"name": "工具名", "arguments": {{...}}, "reason": "为什么需要"}}}}
2) 不需要工具时，直接输出你要说的话（正常聊天，不要输出 JSON）。
"""

_FENCE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL)


class ToolRouter:
    """Selects candidate tools and extracts the model's tool decision."""

    def __init__(
        self,
        config: ToolsConfig,
        registry: ToolRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self._log = logger or logging.getLogger("CatooBot.Tools")

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled and len(self.registry))

    def candidates(
        self, query: str, *, allowed: list[str] | None = None
    ) -> list[tuple[ToolBase, float]]:
        return self.registry.candidates(query, limit=self.config.candidate_tools, allowed=allowed)

    def build_instruction(self, candidates: list[tuple[ToolBase, float]]) -> str:
        """Render only the candidate schemas — never the whole registry (v0.6 §12)."""
        blocks = []
        for tool, score in candidates:
            payload = tool.metadata.to_prompt_dict()
            blocks.append(
                "- {name}：{description}\n  何时用：{when}\n  何时不用：{not_when}\n"
                "  参数 schema：{schema}".format(
                    name=payload["name"],
                    description=payload["description"] or "(无说明)",
                    when=payload["when_to_use"] or "-",
                    not_when=payload["when_not_to_use"] or "-",
                    schema=json.dumps(payload["parameters"], ensure_ascii=False),
                )
            )
            self._log.debug("[Tool] candidate %s score=%.3f", tool.metadata.name, score)
        return DECISION_SYSTEM.format(tools="\n".join(blocks))

    def parse_decision(self, content: str) -> ToolCall | None:
        """Extract a tool call from a JSON decision (native calls come earlier)."""
        text = (content or "").strip()
        if not text:
            return None
        for candidate in self._json_candidates(text):
            payload: Any
            try:
                payload = json.loads(candidate)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                inner = payload.get("tool_call") or payload.get("tool_use") or payload
                call = ToolCall.from_model_output(inner, reason="model_decision")
                if call is not None:
                    return call
        return None

    @staticmethod
    def _json_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        for match in _FENCE.finditer(text):
            candidates.append(match.group("body").strip())
        if text.startswith("{"):
            candidates.append(text)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
        return candidates


class ToolOrchestrator:
    """Runs the (bounded) decision loop for one chat turn."""

    def __init__(
        self,
        config: ToolsConfig,
        registry: ToolRegistry,
        router: ToolRouter,
        executor: ToolExecutor,
        policy: ToolPolicy,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.router = router
        self.executor = executor
        self.policy = policy
        self._log = logger or logging.getLogger("CatooBot.Tools")
        self.last_traces: list[ToolTrace] = []

    async def run(
        self,
        engine: Any,
        messages: list[ChatMessage],
        *,
        context: ToolContext,
        query: str,
        temperature: float | None = None,
        allowed_tools: list[str] | None = None,
        decision: dict[str, Any] | None = None,
    ) -> tuple[str, list[ToolResult]]:
        """Return ``(final_text, results)``; the text is always safe to send."""
        decision = decision if decision is not None else {}
        safe_messages = list(messages) or [ChatMessage.user(query)]
        candidates = self.router.candidates(query, allowed=allowed_tools)
        if not candidates:
            response = await engine.chat(AIRequest(messages=safe_messages, temperature=temperature))
            return response.content, []

        names = [tool.metadata.name for tool, _ in candidates]
        decision["candidates"] = [
            {"name": tool.metadata.name, "score": score} for tool, score in candidates
        ]
        self._log.info("[Tool] candidates=%s", ",".join(names))

        working = list(safe_messages)
        if self.config.decision_mode == "json":
            working.insert(0, ChatMessage.system(self.router.build_instruction(candidates)))

        budget = TurnBudget(
            max_calls=self.config.max_calls_per_turn,
            max_execution_time=self.config.max_execution_time,
        )
        results: list[ToolResult] = []
        self.last_traces = []

        while True:
            requests_tools = self.config.decision_mode == "native"
            request = AIRequest(
                messages=working,
                temperature=temperature,
                tools=[tool for tool, _ in candidates] if requests_tools else None,
            )
            decision_response: AIResponse = await engine.chat(request)

            call = self._extract_call(decision_response)
            if call is None:
                decision["selected"] = None
                decision["reason"] = "no_tool_needed"
                return decision_response.content, results

            if budget.remaining() <= 0 or budget.timed_out():
                self._log.warning("[Tool] budget exhausted before '%s'", call.name)
                decision["selected"] = call.name
                decision["reason"] = "budget_exceeded"
                # Ask once more with an explicit "no more tools" instruction.
                working.append(
                    ChatMessage.system(
                        "工具调用次数已达上限，请直接用现有信息回答，不要再调用工具。"
                    )
                )
                final = await engine.chat(AIRequest(messages=working, temperature=temperature))
                return final.content, results

            decision["selected"] = call.name
            decision["arguments"] = call.arguments
            result = await self.executor.execute(call, context, budget)
            results.append(result)
            self.last_traces.extend(self.executor.traces[-1:])
            self._log.info(
                "[Tool] %s -> %s (%s, %.0fms)",
                call.name,
                "ok" if result.success else "error",
                result.error_type or "-",
                result.execution_time * 1000,
            )

            working.append(
                ChatMessage.assistant(decision_response.content or f"(调用 {call.name})")
            )
            working.append(
                ChatMessage.user(
                    guard_external(result.to_prompt_block())
                    + "\n\n请基于以上参考数据，用你自己的语气自然回复用户。"
                )
            )

    def _extract_call(self, response: AIResponse) -> ToolCall | None:
        """Native function calls first, then the JSON decision fallback (v0.6 §47)."""
        for native in getattr(response, "tool_calls", None) or []:
            call = ToolCall.from_model_output(native, reason="native_tool_call")
            if call is not None:
                return call
        return self.router.parse_decision(response.content)
