"""用户回合 → 任务动作（Phase 5A §四十二/§四十三/§六十）。

这一层是 TaskRuntime 的**唯一对话入口**，负责把一句人话翻译成任务操作，
规则全部写在代码里（不叫模型来猜）：

* 控制命令（确认 / 暂停 / 继续 / 取消这个任务）→ 直接操作当前任务，**不新增工具**；
* 需要跨多个步骤的请求（"去砍一棵树，挖一块原木并捡回来"）→ 先做 SAFE 观察
  （Phase A）→ 生成冻结计划（Phase B）→ 让用户确认；
* 其余一律**不碰**：普通聊天仍然是普通聊天（§九十九），"你在哪""背包里有什么"
  这类查询直接走既有的 SAFE 工具。

确认仍然只发生在 USER 回合：本模块不绕过 ``TaskRuntime.confirm_and_start`` 的任何检查
（它自己会校验 origin/user/session 与一次性确认条目）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.character.turn import TurnOrigin
from app.tasks.intent import TaskIntentDetector
from app.tasks.planner import ObservationFailed, plan_resource_task
from app.tasks.runtime import TaskAuthorizationError, TaskBusy, TaskRuntime

log = logging.getLogger("CatooBot.Tasks.Turn")

#: 用户口语 → 方块名（确定性的小词表；认不出来的就别开任务）
BLOCK_ALIASES: dict[str, str] = {
    "原木": "minecraft:oak_log",
    "木头": "minecraft:oak_log",
    "木块": "minecraft:oak_log",
    "橡木": "minecraft:oak_log",
    "橡树": "minecraft:oak_log",
    "树": "minecraft:oak_log",
    "log": "minecraft:oak_log",
    "圆石": "minecraft:cobblestone",
    "石头": "minecraft:stone",
    "煤": "minecraft:coal_ore",
    "铁矿": "minecraft:iron_ore",
    "沙子": "minecraft:sand",
    "土": "minecraft:dirt",
}

#: 方块 → 挖出来的东西（默认同名；这里只写不一致的）
DROP_OVERRIDES: dict[str, str] = {
    "minecraft:coal_ore": "minecraft:coal",
    "minecraft:iron_ore": "minecraft:raw_iron",
    "minecraft:stone": "minecraft:cobblestone",
}


@dataclass
class TaskTurnOutcome:
    """一次用户回合的任务侧结果（``reply`` 直接发回给用户）。"""

    handled: bool
    action: str = ""
    reply: str = ""
    task_id: str = ""
    state: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def block_for(text: str) -> str:
    """从用户的话里认出要挖的方块（认不出来返回空串）。"""
    message = str(text or "").lower()
    for alias, block in BLOCK_ALIASES.items():
        if alias in message:
            return block
    return ""


class TaskTurnHandler:
    """把一次 USER 回合接到 TaskRuntime 上（在 Minecraft 会话里用）。"""

    def __init__(
        self,
        runtime: TaskRuntime,
        *,
        observe: Any,
        detector: TaskIntentDetector | None = None,
        objective_limit: int = 120,
    ) -> None:
        self._runtime = runtime
        self._observe = observe
        self._detector = detector or TaskIntentDetector()
        self._limit = max(20, int(objective_limit))

    # ------------------------------------------------------------ 入口

    async def handle(
        self,
        *,
        session_id: str,
        user_id: str,
        text: str,
        origin: str = TurnOrigin.USER.value,
    ) -> TaskTurnOutcome:
        """返回 ``handled=False`` = "这句话不归任务管，交给正常对话"。"""
        message = str(text or "").strip()
        if not message:
            return TaskTurnOutcome(False)
        current = await self._runtime.current(session_id)
        command = self._detector.control_command(message)
        if command:
            if current is None:
                # 没有任务时的"确认/继续"是普通聊天（用户可能在聊别的）
                return TaskTurnOutcome(False)
            return await self._control(command, current, user_id=user_id, session_id=session_id)
        intent = self._detector.detect(message)
        if not intent.is_task:
            return TaskTurnOutcome(False)
        if current is not None and not current.state.terminal:
            # 一个会话同时只有一个前台任务（§二十六）：先把手上这个说完
            return TaskTurnOutcome(
                True,
                action="busy",
                task_id=current.task_id,
                state=current.state.value,
                reply=f"我手上还有一个没做完的事（{current.objective}）。先说「停止这个任务」，我再来做新的。",
            )
        return await self._create(session_id, user_id, message)

    # ------------------------------------------------------------ 控制命令

    async def _control(
        self, command: str, record: Any, *, user_id: str, session_id: str
    ) -> TaskTurnOutcome:
        runtime = self._runtime
        try:
            if command == "confirm":
                updated = await runtime.confirm_and_start(
                    record.task_id,
                    user_id=user_id,
                    session_id=session_id,
                    origin=TurnOrigin.USER.value,
                )
                if updated.state.value == "PENDING_CONFIRMATION":
                    return TaskTurnOutcome(
                        True,
                        action="confirm_failed",
                        task_id=record.task_id,
                        state=updated.state.value,
                        reply=f"这次确认没生效（{updated.message}）。想让我做的话再说一次「确认」。",
                    )
                return TaskTurnOutcome(
                    True,
                    action="confirmed",
                    task_id=record.task_id,
                    state=updated.state.value,
                    reply="好，我这就去。",
                )
            if command == "pause":
                updated = await runtime.pause(record.task_id, reason="用户说暂停")
                return TaskTurnOutcome(
                    True,
                    action="paused",
                    task_id=record.task_id,
                    state=updated.state.value,
                    reply="好，我先停下。",
                )
            if command == "resume":
                updated = await runtime.resume(
                    record.task_id,
                    user_id=user_id,
                    session_id=session_id,
                    origin=TurnOrigin.USER.value,
                )
                if updated.state.value == "PENDING_CONFIRMATION":
                    return TaskTurnOutcome(
                        True,
                        action="resume_needs_confirmation",
                        task_id=record.task_id,
                        state=updated.state.value,
                        reply="刚才那份许可过期了，我重新说一遍要做的事，你说「确认」我就继续。",
                    )
                return TaskTurnOutcome(
                    True,
                    action="resumed",
                    task_id=record.task_id,
                    state=updated.state.value,
                    reply="好，接着做。",
                )
            if command == "cancel":
                updated = await runtime.cancel(record.task_id, reason="用户取消")
                return TaskTurnOutcome(
                    True,
                    action="cancelled",
                    task_id=record.task_id,
                    state=updated.state.value,
                    reply="好，不做了。",
                )
        except (TaskAuthorizationError, TaskBusy) as exc:
            return TaskTurnOutcome(
                True,
                action=f"{command}_failed",
                task_id=record.task_id,
                state=record.state.value,
                reply=f"这件事现在做不了（{getattr(exc, 'code', '') or exc}）。",
            )
        return TaskTurnOutcome(False)

    # ------------------------------------------------------------ 新任务

    async def _create(self, session_id: str, user_id: str, message: str) -> TaskTurnOutcome:
        block = block_for(message)
        if not block:
            # 认不出具体目标 → 不开任务，交给正常对话（模型可以用 SAFE 工具去查）
            return TaskTurnOutcome(False)
        objective = message[: self._limit]
        drop = DROP_OVERRIDES.get(block, block)
        try:
            planned = await plan_resource_task(
                objective,
                observe=self._observe,
                block_name=block,
                drop_item=drop,
            )
        except ObservationFailed as exc:
            return TaskTurnOutcome(
                True, action="plan_failed", reply=f"我看了一圈，现在做不了：{exc}。"
            )
        except Exception:  # noqa: BLE001 - 规划失败绝不影响正常对话
            log.exception("[Task] 规划失败（%s）", objective)
            return TaskTurnOutcome(
                True, action="plan_failed", reply="我试着盘算了一下，但没排明白，等会儿再说吧。"
            )
        try:
            record = await self._runtime.create_task(
                objective,
                session_id=session_id,
                user_id=user_id,
                origin=TurnOrigin.USER.value,
                plan=planned.plan,
            )
        except TaskBusy:
            return TaskTurnOutcome(
                True, action="busy", reply="我手上还有一件事没做完，先做完这个再说。"
            )
        summary = self._runtime.summary_of(record)
        return TaskTurnOutcome(
            True,
            action="created",
            task_id=record.task_id,
            state=record.state.value,
            reply=f"我打算这么做，你看行不行：\n{summary}\n想让我开始就说「确认」；改主意了就说「停止这个任务」。",
            detail={"plan_hash": record.plan_hash},
        )
