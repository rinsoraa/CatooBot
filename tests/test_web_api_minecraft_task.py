"""Phase 5A §七十八/§七十九：任务 API（只读投影 + 暂停/继续/取消）。

**硬门禁**：这套端点永远不能确认计划（没有 confirm 路径），"继续"也只能沿用一份
仍然有效的授权 —— WebUI 不能替用户授权（§八十七 case 6）。
"""

from __future__ import annotations

from typing import Any

from app.config.settings import MinecraftConfig, ToolsConfig
from app.integrations.minecraft.agent import MinecraftAgentBridge
from app.tasks.models import ExpectedFinalState, TaskPlan, TaskState, TaskStep
from app.tasks.runtime import TaskConfig
from tests.api_harness import api_server, error_code


class StubRuntime:
    """最小任务运行时替身：只暴露 API 用到的那几个面。"""

    def __init__(self, **kwargs: Any) -> None:
        from app.tasks.runtime import TaskRuntime
        from app.tasks.store import InMemoryTaskStore
        from tests.test_task_runtime import FakeConfirmation

        self.record: Any = None
        self.actions: list[tuple[str, str]] = []
        self._inner = TaskRuntime(
            store=InMemoryTaskStore(),
            invoke=self._invoke,
            confirmations=FakeConfirmation(),
            config=None,
            # 注册表/风险表只是"计划校验"的输入（这里不需要真的工具栈）
            risk_of=lambda tool: "LOW" if tool == "minecraft_move_to" else "SAFE",
            is_registered=lambda tool: True,
            schema_of=lambda tool: None,
            **kwargs,
        )

    async def _invoke(self, tool: str, arguments: Any, **kwargs: Any) -> Any:
        from app.tasks.runtime import TaskInvocation

        return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})

    # --- 代理面
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def current(self, session_id: str | None = None) -> Any:
        return self.record

    async def get(self, task_id: str) -> Any:
        if self.record is None or self.record.task_id != task_id:
            return None
        return self.record

    async def pause(self, task_id: str, *, reason: str = "") -> Any:
        self.actions.append(("pause", reason))
        self.record.state = TaskState.PAUSED
        return self.record

    async def cancel(self, task_id: str, *, reason: str = "") -> Any:
        self.actions.append(("cancel", reason))
        self.record.state = TaskState.CANCELLED
        return self.record

    async def resume(self, task_id: str, **kwargs: Any) -> Any:
        self.actions.append(("resume", str(kwargs)))
        assert kwargs.get("non_user_ok") is True, "WebUI 的继续必须显式声明它不是用户回合"
        assert kwargs.get("origin") != "user", "WebUI 绝不能伪装成用户回合"
        self.record.state = TaskState.RUNNING
        return self.record

    def snapshot_payload(self, record: Any) -> dict[str, Any]:
        if record is None:
            return {}
        return self._inner.snapshot_payload(record)


async def _stub(bot: Any, **kwargs: Any) -> StubRuntime:
    stub = StubRuntime(**kwargs)
    # 任务端点只需要 bot.tasks（不碰 bot.minecraft，免得影响停机路径）
    bot.tasks = stub
    record = await stub._inner.create_task(  # noqa: SLF001 - 造一条真实的 PENDING 记录
        "去附近找一棵橡木，挖一块原木并捡回来",
        session_id="minecraft:127.0.0.1:25565:空凛",
        user_id="空凛",
        origin="user",
        plan=TaskPlan(
            objective="去附近找一棵橡木，挖一块原木并捡回来",
            steps=[
                TaskStep(
                    step_id="step_1",
                    tool="minecraft_move_to",
                    arguments={"x": 12, "y": 64, "z": 9},
                    risk="LOW",
                )
            ],
            expected_final_state=ExpectedFinalState(),
        ),
    )
    stub.record = record
    return stub


async def test_task_reads_are_always_200_and_controls_refuse_when_absent(tmp_path) -> None:
    """读端点恒 200（没有任务不是故障）；控制端点才 503（不做假动作）。"""
    async with api_server(tmp_path) as (client, bot, _server):
        await client.login()
        bot.tasks = None
        status, payload = await client.get("/api/v1/minecraft/task")
        assert status == 200
        assert payload["data"]["task"] is None

        status, payload = await client.get("/api/v1/minecraft/task/task_x")
        assert status == 404
        assert error_code(payload) == "task.not_found"

        status, payload = await client.post("/api/v1/minecraft/task/task_x/pause", body={})
        assert status == 503
        assert error_code(payload) == "task.disabled"


async def test_task_read_endpoints_return_the_ui_projection(tmp_path) -> None:
    async with api_server(tmp_path) as (client, bot, _server):
        await client.login()
        stub = await _stub(bot)

        status, payload = await client.get("/api/v1/minecraft/task")
        assert status == 200
        task = payload["data"]["task"]
        assert task["state"] == TaskState.PENDING_CONFIRMATION.value
        assert task["objective"].startswith("去附近找一棵橡木")
        assert task["progress"] == {"completed": 0, "total": 1}
        assert task["confirmation_required"] is True
        assert task["plan"]["steps"][0]["tool"] == "minecraft_move_to"
        assert task["plan"]["steps"][0]["label"], "计划里的每一步都要有一句人话"
        assert task["current_step"]["arguments"] == {"x": 12, "y": 64, "z": 9}
        # 只读投影里绝不能出现 raw 世界状态
        assert "raw" not in str(task).lower()
        assert task["rollback_supported"] is False

        status, payload = await client.get(f"/api/v1/minecraft/task/{stub.record.task_id}")
        assert status == 200
        assert payload["data"]["task_id"] == stub.record.task_id

        status, payload = await client.get("/api/v1/minecraft/task/task_missing")
        assert status == 404
        assert error_code(payload) == "task.not_found"


async def test_task_control_endpoints_only_narrow_authority(tmp_path) -> None:
    async with api_server(tmp_path) as (client, bot, _server):
        await client.login()
        stub = await _stub(bot)
        task_id = stub.record.task_id

        status, payload = await client.post(
            f"/api/v1/minecraft/task/{task_id}/pause", body={"reason": "看看"}
        )
        assert status == 200
        assert payload["data"]["state"] == TaskState.PAUSED.value
        assert stub.actions[-1][0] == "pause"

        status, payload = await client.post(f"/api/v1/minecraft/task/{task_id}/resume", body={})
        assert status == 200
        assert payload["data"]["state"] == TaskState.RUNNING.value
        assert stub.actions[-1][0] == "resume"

        status, payload = await client.post(f"/api/v1/minecraft/task/{task_id}/cancel", body={})
        assert status == 200
        assert payload["data"]["state"] == TaskState.CANCELLED.value
        assert stub.actions[-1][0] == "cancel"

        status, payload = await client.post(f"/api/v1/minecraft/task/{task_id}/confirm", body={})
        assert status == 400
        assert error_code(payload) == "task.action_invalid", "没有 confirm 这条路"


async def test_task_control_endpoints_reject_unknown_tasks(tmp_path) -> None:
    async with api_server(tmp_path) as (client, bot, _server):
        await client.login()
        await _stub(bot)
        status, payload = await client.post("/api/v1/minecraft/task/task_nope/pause", body={})
        assert status == 404
        assert error_code(payload) == "task.not_found"


async def test_task_action_errors_map_to_conflicts(tmp_path) -> None:
    from app.tasks.runtime import TaskAuthorizationError

    async with api_server(tmp_path) as (client, bot, _server):
        await client.login()
        stub = await _stub(bot)

        async def refuse(task_id: str, **kwargs: Any) -> Any:
            raise TaskAuthorizationError("授权过期了", code="task.resume_requires_user")

        stub.resume = refuse  # type: ignore[method-assign]
        status, payload = await client.post(
            f"/api/v1/minecraft/task/{stub.record.task_id}/resume", body={}
        )
        assert status == 409
        assert error_code(payload) == "task.resume_requires_user"


async def test_webui_resume_never_confirms_a_plan(tmp_path) -> None:
    """WebUI 的「继续」不能变成"替用户确认计划"：它没有走确认门的路径。"""
    async with api_server(tmp_path) as (client, bot, _server):
        await client.login()
        stub = await _stub(bot)
        assert stub.record.state is TaskState.PENDING_CONFIRMATION
        stub.resume = _refusing_resume  # type: ignore[method-assign]
        status, payload = await client.post(
            f"/api/v1/minecraft/task/{stub.record.task_id}/resume", body={}
        )
        assert status == 409
        assert error_code(payload) == "task.confirmation_not_user_turn"


async def _refusing_resume(task_id: str, **kwargs: Any) -> Any:
    from app.tasks.runtime import TaskAuthorizationError

    raise TaskAuthorizationError("必须用户确认", code="task.confirmation_not_user_turn")


async def test_minecraft_tools_stay_untouched_by_the_task_endpoints(tmp_path) -> None:
    """5A 不新增 Minecraft 原子工具：任务端点只是编排层，不碰单工具行为。"""
    from app.integrations.minecraft.agent import ACTION_RISK

    assert len(ACTION_RISK) == 19
    assert not any(name.startswith("minecraft_task") for name in ACTION_RISK)
    assert ToolsConfig(enabled=True).max_calls_per_turn >= 1
    assert MinecraftConfig(enabled=True).agent.tools.allow_medium is False
    assert MinecraftAgentBridge is not None
    assert TaskConfig().max_steps == 16
