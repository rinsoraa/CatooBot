"""Phase 8E Step 1：先复现当前入口级失败（LLM 解释器接管前）。

这两个用例在 Phase 8D Follow-up 2 的确定性词法判定下**必然误路由**：
  * `能不能别砍两根橡木原木带回来`：`别` 前是 `能`（不在允许前导字内）→ 判 NOT_NEGATED；
  * `我不砍两根橡木原木带回来`：`不砍` 不在否定标记表内 → 判 NOT_NEGATED。
两者都会以肯定请求进入木材计划层（创建待确认计划），这正是本阶段要修掉的缺陷。

本文件**不导入**新模块：它通过既有注入点 `env.chat.wood_intent` 提供一个替身解释器，
断言「解释器判为否定时，入口必须零计划/零任务/零工具」。在解释器接线之前它必然失败。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.tasks.intent import TaskIntentDetector
from app.tasks.turn import TaskTurnOutcome
from tests.test_minecraft_chat_bridge import stack


class _Turns:
    """任务入口替身：默认不接管，记录被调用次数。"""

    def __init__(self, *, current: Any = None) -> None:
        self.detector = TaskIntentDetector()
        self._current = current
        self.calls = 0

    async def current_task(self, session_id: str) -> Any:
        return self._current

    async def handle(self, *, session_id: str, user_id: str, text: str, **_: Any) -> Any:
        self.calls += 1
        return TaskTurnOutcome(False)


class _Plans:
    """计划层替身：只记录是否被调用。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def plan_goal_from_user(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        raise AssertionError("否定请求绝不能进入计划层")


class _Interp:
    """替身解释器：固定返回一个结构化结果（对接线前后的行为差异做判定）。"""

    def __init__(self, **fields: Any) -> None:
        base: dict[str, Any] = {
            "intent": "other",
            "polarity": "unknown",
            "target_species": None,
            "requested_count": None,
            "task_control": None,
            "needs_clarification": False,
            "reason_code": "other",
        }
        base.update(fields)
        self.result = SimpleNamespace(**base)
        self.seen: list[str] = []

    async def interpret(self, *, message: str, **_: Any) -> Any:
        self.seen.append(message)
        return self.result


def _wire(env: Any, interp: _Interp) -> tuple[_Turns, _Plans]:
    turns, plans = _Turns(), _Plans()
    bot = env.chat.bot
    bot.task_turns = turns
    bot.agent_plans = plans
    env.chat.wood_intent = interp  # 解释器注入点（Phase 8E 新增）
    return turns, plans


def test_negation_with_bie_after_neng_is_not_routed_to_planner(tmp_path: Any) -> None:
    """`能不能别砍…` 必须零计划（当前：确定性判定漏掉它 → 误建计划）。"""

    async def run() -> None:
        async with stack(tmp_path, "角色回答。") as env:
            interp = _Interp(
                intent="negated_request", polarity="negative", reason_code="negated_request"
            )
            _, plans = _wire(env, interp)
            await env.say("IdSmoke8E", "能不能别砍两根橡木原木带回来")
            assert interp.seen == ["能不能别砍两根橡木原木带回来"], "木材相关消息必须交给解释器"
            assert plans.calls == [], "否定请求不得进入计划层"

    import asyncio

    asyncio.run(run())


def test_negation_with_bu_kan_is_not_routed_to_planner(tmp_path: Any) -> None:
    """`我不砍…` 必须零计划（当前：`不砍` 不在否定标记表内 → 误建计划）。"""

    async def run() -> None:
        async with stack(tmp_path, "角色回答。") as env:
            interp = _Interp(
                intent="negated_request", polarity="negative", reason_code="negated_request"
            )
            _, plans = _wire(env, interp)
            await env.say("IdSmoke8E", "我不砍两根橡木原木带回来")
            assert interp.seen == ["我不砍两根橡木原木带回来"], "木材相关消息必须交给解释器"
            assert plans.calls == [], "否定请求不得进入计划层"

    import asyncio

    asyncio.run(run())


def test_control_command_still_goes_to_the_existing_entry(tmp_path: Any) -> None:
    """控制命令优先级不变：`取消，别砍树了` 仍交给既有任务入口。"""

    async def run() -> None:
        async with stack(tmp_path, "角色回答。") as env:
            interp = _Interp(intent="task_control", polarity="unknown", task_control="cancel")
            turns, plans = _wire(env, interp)
            await env.say("IdSmoke8E", "取消，别砍树了")
            assert turns.calls == 1, "控制命令必须由既有入口处理"
            assert plans.calls == [], "控制命令不得进入计划层"
            assert interp.seen == [], "控制命令不得被解释器抢截"

    import asyncio

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
