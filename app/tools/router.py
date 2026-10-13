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
from app.tools.models import (
    RouteTarget,
    RouteTrace,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolTrace,
)
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
        #: Phase 8A：本轮的结构化路由证据（与 last_traces 同生命周期）。
        self.last_route: RouteTrace | None = None

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
        route: RouteTrace | None = None,
    ) -> tuple[str, list[ToolResult]]:
        """Return ``(final_text, results)``; the text is always safe to send."""
        decision = decision if decision is not None else {}
        # Phase 8A 修复：**先**清空上一轮的执行 trace。旧顺序把清空放在候选查询之后，
        # 于是「这一轮没有任何工具候选」的早退会把**上一轮**的 ToolTrace 留在 last_traces
        # 里 —— 诊断时看起来像这一轮真的调过工具（stale trace，见 Phase 8A 审计）。
        self.last_traces = []
        self.last_route = route
        safe_messages = list(messages) or [ChatMessage.user(query)]
        candidates = self.router.candidates(query, allowed=allowed_tools)
        if not candidates:
            if route is not None:
                route.route_target = RouteTarget.CHAT_ONLY.value
                route.reason_code = "no_tool_candidates"
                route.candidates = []
            response = await engine.chat(AIRequest(messages=safe_messages, temperature=temperature))
            return response.content, []

        names = [tool.metadata.name for tool, _ in candidates]
        decision["candidates"] = [
            {"name": tool.metadata.name, "score": score} for tool, score in candidates
        ]
        if route is not None:
            route.route_target = RouteTarget.MINECRAFT_TOOL.value
            route.candidates = [dict(item) for item in decision["candidates"]]
        self._log.info("[Tool] candidates=%s", ",".join(names))

        working = list(safe_messages)
        if self.config.decision_mode == "json":
            working.insert(0, ChatMessage.system(self.router.build_instruction(candidates)))

        budget = TurnBudget(
            max_calls=self.config.max_calls_per_turn,
            max_execution_time=self.config.max_execution_time,
        )
        results: list[ToolResult] = []

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
                if route is not None and not route.selected:
                    # 只有「这一轮模型**从未**给出可解析的工具调用」才是 model_no_tool_call。
                    # 一旦本轮提取到过调用（无论成功、被拒还是未知工具），收官回合都不能把
                    # 已经确定的结果码覆盖掉。
                    route.reason_code = "model_no_tool_call"
                return decision_response.content, results

            if budget.remaining() <= 0 or budget.timed_out():
                self._log.warning("[Tool] budget exhausted before '%s'", call.name)
                decision["selected"] = call.name
                decision["reason"] = "budget_exceeded"
                if route is not None:
                    route.reason_code = "budget_exceeded"
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
            if route is not None:
                route.selected = call.name
                route.reason_code = "tool_called"
                if self.last_traces:
                    route.tool_trace_ids.append(str(self.last_traces[-1].trace_id))
                    del route.tool_trace_ids[:-8]
                _fill_outcome(route, result)
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


#: Phase 8A §5.2：把既有的稳定 error_type 映射成**可区分**的路由/执行原因码。
#: 绝不把"没调用""被拒绝""动作进行中""验证成功"混为一谈。
_OUTCOME_REASON: dict[str, str] = {
    "permission_denied": "policy_denied",
    "rate_limit": "policy_denied",
    "budget_exceeded": "policy_denied",
    "loop_detected": "policy_denied",
    "invalid_arguments": "invalid_tool_decision",
    "unknown_tool": "invalid_tool_decision",
    "minecraft.confirmation_required": "confirmation_required",
    "minecraft.user_not_trusted": "policy_denied",
    "minecraft.action_not_allowed": "policy_denied",
    "minecraft.action_busy": "policy_denied",
    "minecraft.confirmation_expired": "policy_denied",
    "minecraft.confirmation_mismatch": "policy_denied",
    "minecraft.confirmation_invalid": "policy_denied",
    "minecraft.confirmation_not_user_turn": "policy_denied",
    "minecraft.task_authorization_missing": "task_authorization_denied",
    "minecraft.player_not_found": "clarify_needed",
    "task.authorization_denied": "task_authorization_denied",
    "minecraft.offline": "minecraft_offline",
    "minecraft.not_connected": "minecraft_offline",
    "runtime.unreachable": "minecraft_offline",
    "minecraft.disabled": "minecraft_disabled",
    "unavailable": "minecraft_disabled",
    "timeout": "tool_failed",
}


def _fill_outcome(route: RouteTrace, result: ToolResult) -> None:
    """把一次工具执行映射成稳定的策略/执行状态码（Phase 8A §5.2）。

    只写路由证据，不改变任何执行语义：这里没有重试、没有放宽、没有伪造成功 ——
    「工具调用成功」与「最终游戏目标达成」仍然是两件事（action_state 只描述这一次调用）。
    """
    data = result.data if isinstance(getattr(result, "data", None), dict) else {}
    action_id = str(data.get("action_id") or "")
    status = str(data.get("status") or "").upper()
    if action_id:
        route.action_id = action_id
    # 「工具调用是否实际发生」：未知工具 / 参数不合法 / 被策略拦下都在**执行之前**就返回了，
    # 工具函数根本没跑 —— 不能记成发生过调用（§5.2 要求区分「未调用」与「失败」）。
    if result.success:
        route.tool_called = True
        route.policy_outcome = "allowed"
        if action_id and status in {"RUNNING", "PENDING", "ACCEPTED", "STARTED"}:
            route.reason_code = "action_running"
            route.action_state = "running"
        elif status in {"PENDING_CONFIRMATION", "CONFIRMATION_REQUIRED"}:
            route.reason_code = "confirmation_required"
            route.action_state = "pending_confirmation"
        else:
            route.reason_code = "action_succeeded"
            route.action_state = "succeeded"
        return
    error = str(result.error_type or "")
    route.policy_outcome = error or "failed"
    route.action_state = "failed"
    route.reason_code = _OUTCOME_REASON.get(error, "tool_failed")
