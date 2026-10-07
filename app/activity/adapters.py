"""Phase 6A 适配器：把**已经存在**的事实源接到 ActivityRuntime 上（不新造第二套系统）。

四个来源，各管一段：

* :class:`SandboxActivityAdapter` —— 沙盒的虚拟生活（她的日常模拟器）：
  虚拟活动变化 → 换 Episode（``source=ROUTINE``）；
* :class:`TaskActivityAdapter` —— ``task.*`` 事件（TaskRuntime 已经发布）：
  任务生命周期 → Episode 生命周期（``source=TASK``，只引用 ``task_id``）；
* :class:`MinecraftObservationAdapter` —— MinecraftService 的**只读**镜像：
  只记录观察，绝不产生动作、绝不改 Episode 状态；
* :class:`UserInteractionAdapter` —— QQ / 游戏内聊天回合：
  只记录"有人跟她说话了"，**不**改活动（§三十八/§三十九）。

任何一个适配器都不许调用世界动作 API —— 真实的 Minecraft 动作永远只能由
``TaskRuntime → Policy → Confirmation → Agent Bridge → MinecraftService → ActionRuntime`` 产生。
"""

from __future__ import annotations

from typing import Any

from app.activity.model import (
    ActivitySource,
    ActivityType,
    TransitionReason,
)

# ---------------------------------------------------------------- 沙盒（虚拟生活）


class SandboxActivityAdapter:
    """沙盒是她的生活模拟器：它的"当前动作"就是她的**虚拟**活动（§十五/§四十八）。

    沙盒自己的 ``_sync_state`` 每个 tick 都会报一次"我现在在做什么"，所以这里必须
    **幂等**：活动名没变就不做任何转移（否则会出现 §十三 那种"eating → phone → eating"
    的抖动，把 primary Episode 切碎）。
    """

    def __init__(self, runtime: Any, *, logger: Any = None) -> None:
        self._runtime = runtime
        self._log = logger
        self.degraded_reason = ""

    async def observe_virtual_life(
        self,
        activity: str,
        *,
        location: str = "",
        duration: tuple[float, float, float] | None = None,
        tags: list[str] | None = None,
    ) -> Any:
        """沙盒报来的虚拟活动 → Episode（同一件事就什么都不做）。"""
        name = str(activity or "").strip()
        if not name:
            return None
        try:
            current = await self._runtime.current()
            if current is not None and str(current.activity_name) == name:
                return current  # 同一件事：不切（§十三）
            return await self._runtime.switch_to(
                activity_name=name,
                activity_type=ActivityType.VIRTUAL_LIFE,
                source=ActivitySource.ROUTINE,
                reason=TransitionReason.WORLD_EVENT,
                location=location,
                tags=list(tags or []),
                duration=duration,
            )
        except Exception as exc:  # noqa: BLE001 - 沙盒状态同步失败只降级（§五十一）
            self.degraded_reason = f"{type(exc).__name__}"
            if self._log is not None:
                self._log.debug("[World.Activity] 虚拟活动同步失败（降级）：%s", exc)
            return None


# ---------------------------------------------------------------- Task（真实任务）


#: Task 终态 → (调 ActivityRuntime 的哪个公开方法, 转移原因)。
#: **每个终态都映射到不同的 Episode 状态**，审计能一眼看出"这个活动是被取消、超时，
#: 还是任务失败"（§十九/§三十一）。用公开方法（而不是内部转移助手）是为了让任务层
#: 只能走"正常生命周期入口"，绕不过状态机。
TASK_TERMINAL_MAP: dict[str, tuple[str, TransitionReason]] = {
    "task.succeeded": ("complete", TransitionReason.TASK_COMPLETED),
    "task.failed": ("interrupt", TransitionReason.TASK_FAILED),
    "task.cancelled": ("cancel", TransitionReason.MANUAL),
    "task.expired": ("expire", TransitionReason.TIME_EXPIRED),
}

#: 任务开始/恢复 → 开一个新的任务型 Episode
TASK_START_EVENTS = frozenset({"task.started", "task.resumed"})


class TaskActivityAdapter:
    """TaskRuntime 的任务事件 → ActivityEpisode（§十八/§十九/§四十二/§四十三）。

    * **只引用** ``related_task_id``，绝不复制任务状态机（§十八）；
    * 暂停 → ``INTERRUPTED``（§四十二 的统一口径：任务 PAUSED 时活动不能还 ACTIVE）；
    * 恢复 → 新的 Episode（parent = 被中断的那条）——
      状态机**不允许** INTERRUPTED 回到 ACTIVE（§六），所以只能开新的；
    * 重启后的任务恢复**不会**再建第二个 Episode：先看有没有已经绑着这个 task 的 live Episode，
      有就重新绑定/复用（§四十三）。
    """

    #: 任务侧的活动名（§十九：activity_name = minecraft_task）
    ACTIVITY_NAME = "minecraft_task"

    def __init__(self, runtime: Any, *, logger: Any = None) -> None:
        self._runtime = runtime
        self._log = logger
        self.degraded_reason = ""

    async def on_task_event(self, event: str, payload: dict[str, Any]) -> Any:
        """订阅 ``Bot._publish_task_event`` 的事件（同步钩子 → 这里只入队/直接 await）。"""
        name = str(event or "")
        task_id = str((payload or {}).get("task_id") or "")
        if not task_id:
            return None
        try:
            if name in TASK_START_EVENTS:
                return await self._on_started(name, task_id, payload)
            mapper = TASK_TERMINAL_MAP.get(name)
            if mapper is not None:
                return await self._on_terminal(name, task_id, payload, mapper)
            if name == "task.paused":
                return await self._on_paused(task_id, payload)
            # 其余任务事件（plan_ready / confirmation_required / step_* / replanning…）
            # **不**改 Episode：它们是同一个活动内部的阶段，不是新的活动（§十九）。
            return None
        except Exception as exc:  # noqa: BLE001 - 只降级：任务绝不能因为活动层失败而受影响
            self.degraded_reason = f"{type(exc).__name__}"
            if self._log is not None:
                self._log.warning("[World.Activity] 任务事件 → Episode 失败（降级）：%s", exc)
            return None

    async def _on_started(self, event: str, task_id: str, payload: dict[str, Any]) -> Any:
        current = await self._runtime.current()
        if current is not None and str(current.related_task_id) == task_id:
            return current  # 已经绑着这个任务（重启恢复 / resumed）：绝不重建第二个
        session = str((payload or {}).get("session_id") or "")
        reason = TransitionReason.TASK_STARTED
        # 暂停之后再恢复：新 Episode 接在**同一个任务**那条已收尾的 Episode 后面（可审计）。
        parent = await self._previous_for_task(task_id)
        return await self._runtime.switch_to(
            activity_name=self.ACTIVITY_NAME,
            activity_type=ActivityType.TASK_EXECUTION,
            source=ActivitySource.TASK,
            reason=reason,
            location="minecraft_world",
            social_state="with_friends" if session.startswith("group:") else "alone",
            tags=["minecraft", "task"],
            related_task_id=task_id,
            duration=(60.0, 600.0, 3600.0),
            parent_episode_id=parent,
        )

    async def _previous_for_task(self, task_id: str) -> str:
        """这个任务上一条已经收尾的 Episode（暂停→恢复时用它当 parent）。"""
        try:
            for episode in await self._runtime.recent(10):
                if str(episode.related_task_id) == str(task_id) and episode.status.terminal:
                    return episode.episode_id
        except Exception:  # noqa: BLE001 - 拿不到就不设 parent（不是关键路径）
            return ""
        return ""

    async def _on_terminal(
        self,
        event: str,
        task_id: str,
        payload: dict[str, Any],
        mapper: tuple[str, TransitionReason],
    ) -> Any:
        method_name, reason = mapper
        current = await self._runtime.current()
        if current is None or str(current.related_task_id) != task_id:
            if self._log is not None:
                self._log.debug(
                    "[World.Activity] %s 没有对应的 live Episode（task=%s）——不补造",
                    event,
                    task_id,
                )
            return None
        method = getattr(self._runtime, method_name, None)
        if method is None:
            return None
        return await method(episode_id=current.episode_id, reason=reason)

    async def _on_paused(self, task_id: str, payload: dict[str, Any]) -> Any:
        from app.activity.model import ActivityStatus

        current = await self._runtime.current()
        if current is None or str(current.related_task_id) != task_id:
            return None
        if current.status is not ActivityStatus.ACTIVE:
            return None
        # §四十二：任务 PAUSED 时活动**不能**还 ACTIVE。统一口径 = INTERRUPTED
        # （不是 EXTENDED/WAITING —— 本项目的状态机里没有 WAITING，且 INTERRUPTED 语义最准）。
        return await self._runtime.interrupt(
            episode_id=current.episode_id, reason=TransitionReason.USER_INTERACTION
        )


# ---------------------------------------------------------------- Minecraft（只读观察）


class MinecraftObservationAdapter:
    """把 Minecraft 的**当前真实状态**读成一个只读观察（§十六）。

    **观察不是命令**（§十七）：这里绝不产生动作、绝不改 Episode 状态；它只把事实交给
    ``runtime.observe()``，用于审计与"这个 Episode 还合理吗"的判断。
    """

    def __init__(self, runtime: Any, service: Any = None, *, logger: Any = None) -> None:
        self._runtime = runtime
        self._service = service
        self._log = logger
        self.degraded_reason = ""

    async def observe(self, *, task: Any = None, player_uuid: str = "") -> dict[str, Any]:
        observation = self._collect(task=task, player_uuid=player_uuid)
        try:
            await self._runtime.observe(observation)
        except Exception as exc:  # noqa: BLE001 - 观察失败只降级
            self.degraded_reason = f"{type(exc).__name__}"
            if self._log is not None:
                self._log.debug("[World.Activity] Minecraft 观察失败（降级）：%s", exc)
        return observation

    def _collect(self, *, task: Any, player_uuid: str) -> dict[str, Any]:
        """§十六 的观察形状：全是**事实**，没有一个是命令。"""
        import time as _time

        service = self._service
        observation: dict[str, Any] = {
            "online": False,
            "server_id": "",
            "player_uuid": str(player_uuid or ""),
            "position": None,
            "nearby_players": [],
            "current_action": None,
            "current_task_id": str(getattr(task, "task_id", "") or ""),
            "task_state": str(getattr(getattr(task, "state", None), "value", "") or ""),
            "observed_at": float(_time.time()),
        }
        if service is None:
            return observation
        try:
            snapshot = service.snapshot()
            connection = dict(snapshot.get("connection") or {})
            observation["online"] = str(connection.get("status") or "") == "ONLINE"
            observation["current_action"] = dict(snapshot.get("action") or {})
        except Exception:  # noqa: BLE001 - 读不到就当离线（如实，而不是猜在线）
            return observation
        try:
            semantic = (service.world_view() or {}).get("semantic") or {}
        except Exception:  # noqa: BLE001
            return observation
        self_state = (semantic.get("self") or {}) if isinstance(semantic, dict) else {}
        observation["position"] = dict(self_state.get("position") or {}) or None
        observation["nearby_players"] = [
            str(row.get("name") or "")
            for row in (semantic.get("players") or [])
            if isinstance(row, dict)
        ][:8]
        try:
            server = getattr(service, "snapshot", None)
            if callable(server):
                connection = dict(service.snapshot().get("connection") or {})
                observation["server_id"] = str(connection.get("host") or "")
        except Exception:  # noqa: BLE001
            pass
        return observation


# ---------------------------------------------------------------- 用户交互

#: 用户交互**不会**改变她的活动（§三十八/§三十九）：QQ / 游戏内聊天只能读取。
#: 这里只把"有人在跟她说话"记成观察，让审计能回答"是否被打断"。
INTERACTION_MARKER = "user_interaction_at"


class UserInteractionAdapter:
    """用户交互 → 只写观察，不改状态（QQ/Minecraft 都**不能**控制 Activity）。"""

    def __init__(self, runtime: Any, *, logger: Any = None) -> None:
        self._runtime = runtime
        self._log = logger

    async def note_interaction(
        self, *, session_id: str = "", source: str = "qq", at: float | None = None
    ) -> dict[str, Any]:
        import time as _time

        moment = float(at if at is not None else _time.time())
        observation = dict(getattr(self._runtime, "last_observation", {}) or {})
        observation[INTERACTION_MARKER] = moment
        observation["last_interaction_source"] = str(source)
        observation["last_interaction_session"] = str(session_id)
        try:
            await self._runtime.observe(observation)
        except Exception:  # noqa: BLE001 - 交互记录失败绝不能影响聊天
            if self._log is not None:
                self._log.debug("[World.Activity] 交互记录失败（忽略）", exc_info=True)
        return observation
