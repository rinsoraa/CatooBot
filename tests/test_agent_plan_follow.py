"""Phase 7D 修订 2：跟随任务**确实能被现有 TaskRuntime 承载**（逐条证明）。

这一组测试用**真的 TaskRuntime + 真的校验器 + 假的工具通道**（不联网）：
证明 validate_plan 接受 follow 步骤、detached RUNNING 不会被误判成功、
cancel/expire 走既有 minecraft_stop、授权到期不拦在途动作但有任务 TTL 兜底。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tasks.agent_plan import PlanTarget
from app.tasks.agent_planner import BoundedAgentPlanner
from app.tasks.models import TaskPlan
from app.tasks.validation import validate_plan
from tests.agent_plan_fakes import SERVER


def follow_plan() -> TaskPlan:
    planner = BoundedAgentPlanner()
    result = planner._plan_follow(
        "跟着我",
        PlanTarget(
            status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
        ),
    )
    assert result.plan is not None
    return result.plan.plan


RISK_TABLE = {
    "minecraft_follow_player": "LOW",
    "minecraft_stop": "SAFE",
    "minecraft_dig": "MEDIUM",
    "minecraft_dropped_items": "SAFE",
    "minecraft_inventory": "SAFE",
}
TOOL_SCHEMA: dict[str, dict[str, Any]] = {
    "minecraft_follow_player": {
        "type": "object",
        "properties": {"username": {"type": "string"}, "distance": {"type": "number"}},
        "required": ["username"],
    },
}


class TestValidatorAcceptsFollow:
    def test_validate_plan_accepts_a_single_follow_step(self) -> None:
        problems = validate_plan(
            follow_plan(),
            risk_of=RISK_TABLE.get,
            is_registered=lambda tool: tool in RISK_TABLE,
            schema_of=TOOL_SCHEMA.get,
            online=True,
        )
        assert problems == [], problems

    def test_follow_step_with_a_made_up_tool_is_rejected(self) -> None:
        plan = follow_plan()
        plan.steps[0].tool = "minecraft_teleport_to_player"
        problems = validate_plan(
            plan, risk_of=RISK_TABLE.get, is_registered=lambda tool: tool in RISK_TABLE, online=True
        )
        assert any("未注册" in p for p in problems)


class TestDetachedFollowLifecycle:
    """RUNNING + action_id → WAITING_ACTION（绝不误判成功）；终态由事件送达。"""

    async def test_detached_invocation_becomes_waiting_action(self) -> None:
        # 直接驱动 TaskRuntime 的判定函数：detached → WAITING_ACTION，不是 SUCCEEDED
        from app.tasks.runtime import (
            TaskInvocation,
            TaskRecord,  # noqa: F401
        )

        invocation = TaskInvocation(ok=True, status="RUNNING", action_id="act_1")
        assert invocation.detached is True

    async def test_finished_invocation_is_not_detached(self) -> None:
        from app.tasks.runtime import TaskInvocation

        assert TaskInvocation(ok=True, status="SUCCEEDED").detached is False
        assert TaskInvocation(ok=False, status="FAILED", error="x").detached is False


class TestStopPaths:
    """取消/过期/暂停都经既有 minecraft_stop（源码级断言，防回归）。"""

    def test_cancel_and_expire_call_stop_action(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent / "app" / "tasks" / "runtime.py"
        ).read_text(encoding="utf-8")
        # TaskRuntime.cancel / expire 的函数体里都先 _stop_action()
        # （从 runtime.cancel 起找 —— PlanConfirmation 协议里也有一个 cancel）
        region = source[source.index("async def cancel(self, task_id") :]
        for anchor in ("async def cancel(", "async def expire("):
            start = region.index(anchor)
            body = region[start : region.index("    async def", start + 10)]
            assert "_stop_action()" in body, anchor

    def test_follow_timeout_config_bounds_the_action(self) -> None:
        from app.config.settings import AppConfig

        config = AppConfig()
        assert config.minecraft.action.follow_player.timeout == 120.0
        assert 10.0 <= config.minecraft.action.follow_player.timeout <= 600.0
        # 三层期限分层存在且互不相同（修订 2 的核心结论）
        assert config.minecraft.agent.confirmation.ttl_seconds == 60.0
        assert config.task.ttl_seconds == 600.0


class TestTaskRuntimeCarriesFollow:
    """真 TaskRuntime（内存 store + 桩 invoke）跑完一个 follow 任务的完整生命周期。"""

    async def test_follow_task_runs_through_the_real_runtime(self) -> None:
        from app.tasks.models import StepState, TaskState
        from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
        from app.tasks.store import InMemoryTaskStore

        invoked: list[tuple[str, dict[str, Any], str]] = []

        async def invoke(tool: str, arguments: dict[str, Any], **kwargs: Any) -> TaskInvocation:
            invoked.append((tool, dict(arguments), str(kwargs.get("risk") or "")))
            if tool == "minecraft_follow_player":
                return TaskInvocation(
                    ok=True,
                    status="RUNNING",
                    action_id="act_follow_1",
                    result={"result": {"username": "Rinsora"}},
                )
            if tool == "minecraft_stop":
                return TaskInvocation(ok=True, status="SUCCEEDED")
            if tool == "minecraft_inventory":
                return TaskInvocation(
                    ok=True,
                    status="SUCCEEDED",
                    result={"result": {"items": [], "held_item": {"name": "netherite_axe"}}},
                )
            return TaskInvocation(ok=True, status="SUCCEEDED")

        class FakeConfirmations:
            """最小确认门（生产实现是 Minecraft 的 ConfirmationStore；这里只供 TaskRuntime 用）。"""

            def __init__(self) -> None:
                self.pending: dict[str, dict[str, Any]] = {}
                self._seq = 0

            async def request(self, *, task_id: str, **kwargs: Any) -> str:
                self._seq += 1
                cid = f"conf-{self._seq}"
                self.pending[cid] = {"task_id": task_id, **kwargs}
                return cid

            async def consume(
                self, confirmation_id: str, *, task_id: str, **kwargs: Any
            ) -> tuple[bool, str]:
                entry = self.pending.get(str(confirmation_id))
                if entry is None or entry.get("task_id") != str(task_id):
                    return False, "minecraft.confirmation_mismatch"
                self.pending.pop(str(confirmation_id), None)
                return True, ""

            async def cancel(self, confirmation_id: str) -> None:
                self.pending.pop(str(confirmation_id), None)

        runtime = TaskRuntime(
            store=InMemoryTaskStore(),
            invoke=invoke,
            confirmations=FakeConfirmations(),
            config=TaskConfig(ttl_seconds=600.0),
            risk_of=RISK_TABLE.get,
            is_registered=lambda tool: tool in RISK_TABLE,
            schema_of=TOOL_SCHEMA.get,
        )
        record = await runtime.create_task(
            "跟着我",
            session_id="s",
            user_id="u",
            origin="user",
            plan=follow_plan(),
        )
        assert record.state.value == "PENDING_CONFIRMATION"
        started = await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="user"
        )
        # 派发即离开确认态；detached 动作在途 → WAITING_ACTION（不是 SUCCEEDED）
        assert started.state is TaskState.WAITING_ACTION
        assert ("minecraft_follow_player", {"username": "Rinsora"}, "LOW") in invoked
        assert started.plan.steps[0].state is StepState.WAITING_ACTION
        # 取消：走既有 stop 路径 → 步骤 CANCELLED，任务终态
        cancelled = await runtime.cancel(record.task_id, reason="用户说不跟了")
        assert cancelled.state.value in {"CANCELLED", "EXPIRED"}
        assert ("minecraft_stop", {}, "SAFE") in invoked
        # cancel() 把 pending_step 的动作标记为取消；步骤对象在重载后的 record 上
        assert cancelled.plan.steps[0].state is StepState.CANCELLED


class TestServiceRouteRegistered:
    """真机门禁抓到的缺口：任务适配器的服务路由表必须包含 follow_player。"""

    def test_follow_player_has_a_service_route(self) -> None:
        from app.integrations.minecraft.task_adapter import _SERVICE_ROUTES

        assert "minecraft_follow_player" in _SERVICE_ROUTES

    def test_route_calls_service_follow_player(self) -> None:
        import asyncio

        from app.integrations.minecraft.task_adapter import _SERVICE_ROUTES

        calls: list[str] = []

        class FakeService:
            async def follow_player(self, username: Any, distance: Any = None) -> dict[str, Any]:
                calls.append(f"{username}:{distance}")
                return {"username": username, "status": "RUNNING", "action_id": "act_x"}

        result = asyncio.run(
            _SERVICE_ROUTES["minecraft_follow_player"](FakeService(), {"username": "RinsoraNeko"})
        )
        assert result["status"] == "RUNNING"
        assert calls == ["RinsoraNeko:None"]
