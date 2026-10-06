"""Phase 5A：把 Minecraft 的 action 事件接进 TaskRuntime（生产与 smoke 共用）。

TaskRuntime 只认"某个 action_id 进终态了"这件事实，绝不认识 Mineflayer；
这里就是那层胶水：订阅 MinecraftService 的事件流 → 过滤出 action 终态 → 交给 TaskRuntime，
同时按 Task TTL 定期收尾（``tick``）。

**不新增事件源**：订阅的仍然是现有 `minecraft.action.*`（§七十六）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.tasks.models import TASK_EVENTS
from app.tasks.runtime import TaskRuntime

#: 动作终态 → 交给 TaskRuntime 的事件名（只消费这些）
TERMINAL_SUFFIXES = ("completed", "failed", "cancelled", "timeout")


class MinecraftTaskCoordinator:
    """订阅 action 事件 → 唤醒对应 Task；并定期让过期的 Task 收尾。"""

    def __init__(
        self,
        runtime: TaskRuntime,
        service: Any,
        *,
        publish: Any = None,
        poll_seconds: float = 5.0,
        logger: Any = None,
    ) -> None:
        self.runtime = runtime
        self._service = service
        self._publish = publish
        self._poll_seconds = max(1.0, float(poll_seconds))
        self._log = logger
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """挂上事件订阅 + 起一个只做"过期收尾"的定时器（不新增事件源）。"""
        self._service.add_listener(self._on_service_event)
        if self._task is None and not self._stopping:
            self._task = asyncio.create_task(self._tick_forever())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 收尾不该抛
                pass
            self._task = None

    # ------------------------------------------------------------ 事件

    def _on_service_event(self, event: Any) -> None:
        # 事件名必须取 ``type_name``（= MinecraftEventType.value）。直接 str(event.event)
        # 得到的是 "MinecraftEventType.ACTION_COMPLETED"，永远匹配不上前缀。
        name = str(getattr(event, "type_name", "") or "")
        if not name.startswith("minecraft.action."):
            return
        suffix = name.rsplit(".", 1)[-1]
        if suffix not in TERMINAL_SUFFIXES:
            return
        data = dict(getattr(event, "data", None) or {})
        action_id = str(data.get("action_id") or "")
        if not action_id:
            return
        asyncio.create_task(self._handle_terminal(name, action_id, data))

    async def _handle_terminal(self, name: str, action_id: str, data: dict[str, Any]) -> None:
        try:
            await self.runtime.on_action_event(
                action_id=action_id,
                event=name,
                status=str(data.get("status") or ""),
                result=data.get("result") if isinstance(data.get("result"), dict) else {},
                error=str(data.get("error") or ""),
                code=str(data.get("code") or ""),
            )
        except Exception:  # noqa: BLE001 - 事件回调里绝不炸
            if self._log is not None:
                self._log.exception("[Task] action event handling failed action_id=%s", action_id)

    async def _tick_forever(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._poll_seconds)
            try:
                await self.runtime.tick()
            except Exception:  # noqa: BLE001
                if self._log is not None:
                    self._log.exception("[Task] tick failed")


def task_event_names() -> tuple[str, ...]:
    return TASK_EVENTS
