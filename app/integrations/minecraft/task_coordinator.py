"""Phase 5A：把 Minecraft 的 action 事件接进 TaskRuntime（生产与 smoke 共用）。

TaskRuntime 只认"某个 action_id 进终态了"这件事实，绝不认识 Mineflayer；
这里就是那层胶水：订阅 MinecraftService 的事件流 → 过滤出 action 终态 → 交给 TaskRuntime，
同时按 Task TTL 定期收尾（``tick``）。

**不新增事件源**：订阅的仍然是现有 `minecraft.action.*`（§七十六）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from app.tasks.models import TASK_EVENTS
from app.tasks.runtime import TaskRuntime

#: 动作终态 → 交给 TaskRuntime 的事件名（只消费这些）
TERMINAL_SUFFIXES = ("completed", "failed", "cancelled", "timeout")


def _terminal_detail(data: dict[str, Any]) -> dict[str, Any]:
    """Map top-level Node cancel reason into detail, fail closed on conflict."""
    detail = dict(data.get("detail") or {}) if isinstance(data.get("detail"), dict) else {}
    top_reason = data.get("reason")
    top_cleanup = data.get("cleanup")
    if isinstance(top_cleanup, Mapping):
        detail["cleanup"] = dict(top_cleanup)
    top_stop_scope = data.get("stop_scope")
    if isinstance(top_stop_scope, str) and top_stop_scope:
        detail["stop_scope"] = top_stop_scope
    top_stop_request = data.get("stop_request_id")
    if isinstance(top_stop_request, str) and top_stop_request:
        existing_stop_request = str(detail.get("stop_request_id") or "")
        if existing_stop_request and existing_stop_request != top_stop_request:
            detail["stop_request_id_conflict"] = True
            detail["stop_request_id"] = ""
        else:
            detail["stop_request_id"] = top_stop_request
    if isinstance(top_reason, str) and top_reason:
        existing = detail.get("reason")
        if existing and existing != top_reason:
            detail["reason_conflict"] = True
            detail["reason"] = ""
        else:
            detail["reason"] = top_reason
    return detail


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
        self._terminal_tasks: set[asyncio.Task[Any]] = set()
        self._shutdown_timeout = 5.0

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """挂上事件订阅 + 起一个只做"过期收尾"的定时器（不新增事件源）。"""
        self._service.add_listener(self._on_service_event)
        if self._task is None and not self._stopping:
            self._task = asyncio.create_task(self._tick_forever())

    def freeze(self) -> None:
        """First-line shutdown freeze: no new world writes from this coordinator."""
        self._stopping = True
        try:
            freeze_service = getattr(self._service, "begin_shutdown", None)
            if freeze_service is not None:
                freeze_service()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.runtime.freeze_dispatch()
        except Exception:  # noqa: BLE001
            pass

    async def begin_shutdown(self, *, timeout: float = 5.0) -> list[str]:
        """Stop accepting new work, then ask the task runtime to stop/drain in-flight actions."""
        self.freeze()
        self._shutdown_timeout = max(0.1, float(timeout))
        try:
            await self.runtime.begin_shutdown()
        except Exception:  # noqa: BLE001
            pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        try:
            return await self.runtime.shutdown_pending(timeout=self._shutdown_timeout)
        except Exception:  # noqa: BLE001 - shutdown must keep going
            if self._log is not None:
                self._log.exception("[Task] shutdown_pending failed")
            return []

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 收尾不该抛
                pass
            self._task = None
        # Wait for terminal events already handed to this coordinator; never silently
        # drop a received action result during shutdown.
        if self._terminal_tasks:
            pending = list(self._terminal_tasks)
            done = asyncio.gather(*pending, return_exceptions=True)
            try:
                await asyncio.wait_for(done, timeout=self._shutdown_timeout)
            except TimeoutError:
                if self._log is not None:
                    self._log.warning("[Task] terminal drain timeout during shutdown")

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
        task = asyncio.create_task(self._handle_terminal(name, action_id, data))
        self._terminal_tasks.add(task)
        task.add_done_callback(self._terminal_tasks.discard)

    async def _handle_terminal(self, name: str, action_id: str, data: dict[str, Any]) -> None:
        try:
            await self.runtime.on_action_event(
                action_id=action_id,
                event=name,
                status=str(data.get("status") or ""),
                result=data.get("result") if isinstance(data.get("result"), dict) else {},
                error=str(data.get("error") or ""),
                code=str(data.get("code") or ""),
                # R0 §4.2/§5.1：运行时结构化失败 detail（目标/实际位置/距离/半径/reason）
                # 必须一起带进任务证据 —— 只靠一句 message 无法区分 no_path、
                # path_search_timeout、goal_reached_without_arrival 等失败状态。
                detail=_terminal_detail(data),
                # Phase 7F.5：事件必须说明是**哪个 Runtime 实例**发出的。
                runtime_instance=str(data.get("runtime_instance") or ""),
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
