"""Phase 7A §二/§六十三：把各层的**只读**事实拼成一个 bounded 的 :class:`InitiativeContext`。

这一层刻意只做两件事：**读**和**降级**。

* 全部依赖都是**鸭子类型注入**（``activity`` / ``goals`` / ``commitments`` / ``memory`` /
  ``topics`` / ``social`` / ``presence`` / ``minecraft`` / ``task_states_provider``）——
  整个 ``app.initiative`` 包**只 import 它自己与标准库**，所以"能不能执行"在源码级就是不可能的
  （见 ``tests/test_initiative_security.py``）；
* 每一处读取都 catch 异常并退化成安全默认值（读不到就当作"没有这个信号"），
  绝不让读失败拖垮 tick 或对话；
* 每次 collect 都是 **bounded** 的（候选 ≤6、目标 ≤3、记忆 ≤5、话题 ≤3、承诺 ≤2），
  绝不整表扫描（§六十三）。
"""

from __future__ import annotations

import json
from typing import Any

from app.initiative.candidates import (
    MAX_COMMITMENT_SIGNALS,
    MAX_GOAL_SIGNALS,
    MAX_MEMORY_SIGNALS,
    MAX_SOCIAL_SIGNALS,
    CommitmentSignal,
    GoalSignal,
    InitiativeContext,
    MemorySignal,
)

#: 读回来的 episode 条数（算 routine_due 用；bounded）
RECENT_ACTIVITY_LIMIT = 6
#: 记忆只读这么几条（bounded；§六十三 不许全量扫描）
MEMORY_SCAN_LIMIT = 5
#: 群里最多看几个话题
SOCIAL_GROUP_LIMIT = 3


def _memory_domain(item: Any, scope: str) -> str:
    """记忆的域：先看 provenance 里的 ``domain``，再退回作用域后缀（``:minecraft``）。"""
    raw = getattr(item, "provenance", None)
    if isinstance(raw, str) and raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                domain = str(payload.get("domain") or "")
                if domain:
                    return domain
        except (TypeError, ValueError):
            pass
    elif isinstance(raw, dict):
        domain = str(raw.get("domain") or "")
        if domain:
            return domain
    tail = str(scope or "").rsplit(":", 1)[-1].strip().lower()
    return tail if tail in {"minecraft", "mc"} else ""


class InitiativeContextAdapter:
    """只读 context 组装器（没有写权限、没有执行句柄）。"""

    def __init__(
        self,
        *,
        character_id: str = "",
        activity: Any = None,
        goals: Any = None,
        commitments: Any = None,
        memory: Any = None,
        topics: Any = None,
        social: Any = None,
        presence: Any = None,
        minecraft: Any = None,
        state_provider: Any = None,
        task_states_provider: Any = None,
        routine_table: Any = None,
        logger: Any = None,
    ) -> None:
        self.character_id = str(character_id or "")
        self.activity = activity
        self.goals = goals
        self.commitments = commitments
        self.memory = memory
        self.topics = topics
        self.social = social
        self.presence = presence
        self.minecraft = minecraft
        self.state_provider = state_provider
        self.task_states_provider = task_states_provider
        self.routine_table = dict(routine_table or {})
        self._log = logger

    # ------------------------------------------------------------ 主入口

    async def collect(self, now: float) -> InitiativeContext:
        moment = float(now)
        state = self._state()
        recent = await self._recent_activities()
        period = self._period(moment)
        states = await self._task_states()
        return InitiativeContext(
            character_id=self.character_id,
            now=moment,
            period=period,
            current_activity=await self._current_activity(),
            current_activity_elapsed=await self._current_elapsed(moment),
            current_activity_status=await self._current_status(),
            recent_activities=recent,
            energy=float(state.get("energy", 1.0) or 0.0),
            focus=float(state.get("focus", 0.5) or 0.0),
            mood=str(state.get("mood") or ""),
            sleeping=self._sleeping(),
            quiet_hours=self._quiet_hours(),
            social_fatigue=await self._social_fatigue(),
            goals=await self._goal_signals(),
            commitments=await self._commitment_signals(),
            memories=await self._memories(),
            social_topics=await self._social_topics(),
            routine_due=self._routine_due(period, recent),
            planner_candidates=self._planner_candidates(),
            task_states=states,
            pending_confirmation="PENDING_CONFIRMATION" in states,
            user_interaction_at=self._user_interaction_at(),
            minecraft_online=self._minecraft_online(),
            degraded=self._degraded(),
        )

    # ------------------------------------------------------------ 各层读取（全部降级安全）

    async def _current_activity(self) -> str:
        episode = await self._call(self.activity, "current")
        return str(getattr(episode, "activity_name", "") or "")

    async def _current_status(self) -> str:
        episode = await self._call(self.activity, "current")
        status = getattr(episode, "status", "")
        return str(getattr(status, "value", status) or "")

    async def _current_elapsed(self, now: float) -> float:
        episode = await self._call(self.activity, "current")
        if episode is None:
            return 0.0
        try:
            return float(episode.elapsed(now))
        except Exception:  # noqa: BLE001 - 拿不到就算了
            return 0.0

    async def _recent_activities(self) -> tuple[str, ...]:
        rows = await self._call(self.activity, "recent", RECENT_ACTIVITY_LIMIT)
        try:
            return tuple(str(getattr(item, "activity_name", "") or "") for item in (rows or ()))
        except Exception:  # noqa: BLE001
            return ()

    async def _call(self, target: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        """安全地调用一个可能不存在 / 可能是 async 的只读方法。"""
        if target is None:
            return None
        method = getattr(target, name, None)
        if method is None:
            return None
        try:
            result = method(*args, **kwargs)
        except Exception:  # noqa: BLE001 - 读失败 = 没有这个信号
            self._debug(f"{name} failed")
            return None
        if hasattr(result, "__await__"):
            try:
                return await result
            except Exception:  # noqa: BLE001
                self._debug(f"{name} failed")
                return None
        return result

    def _debug(self, message: str) -> None:
        if self._log is not None:
            self._log.debug("[Initiative] context read degraded: %s", message, exc_info=True)

    def _state(self) -> dict[str, Any]:
        if self.state_provider is None:
            return {}
        try:
            value = self.state_provider()
        except Exception:  # noqa: BLE001
            return {}
        if isinstance(value, dict):
            return dict(value)
        energy = getattr(value, "energy", None)
        if energy is None:
            return {}
        return {
            "energy": float(energy),
            "focus": float(getattr(value, "focus", 0.5) or 0.5),
            "mood": str(getattr(value, "mood", "") or ""),
        }

    def _period(self, now: float) -> str:
        clock = getattr(self.activity, "clock", None)
        if clock is None:
            return ""
        try:
            return str(clock.period(now))
        except Exception:  # noqa: BLE001
            return ""

    def _routine_due(self, period: str, recent: tuple[str, ...]) -> tuple[str, ...]:
        table = self.routine_table.get(str(period)) or ()
        done = {str(name) for name in recent}
        return tuple(str(name) for name in table if str(name) not in done)

    async def _goal_signals(self) -> tuple[GoalSignal, ...]:
        rows = await self._call(self.goals, "open_goals")
        out: list[GoalSignal] = []
        for goal in list(rows or ())[:MAX_GOAL_SIGNALS]:
            status = getattr(goal, "status", "")
            kind = getattr(goal, "kind", "")
            out.append(
                GoalSignal(
                    goal_id=str(getattr(goal, "goal_id", "") or ""),
                    kind=str(getattr(kind, "value", kind) or ""),
                    label=str(
                        getattr(goal, "reason", "")
                        or getattr(goal, "target_project", "")
                        or getattr(goal, "target_item", "")
                        or ""
                    ),
                    progress=float(getattr(goal, "progress", 0.0) or 0.0),
                    priority=float(getattr(goal, "priority", 0.5) or 0.5),
                    blocked=str(getattr(status, "value", status) or "") == "blocked",
                )
            )
        return tuple(item for item in out if item.goal_id)

    async def _commitment_signals(self) -> tuple[CommitmentSignal, ...]:
        rows = await self._call(self.commitments, "all")
        out: list[CommitmentSignal] = []
        for item in list(rows or ()):
            status = getattr(item, "status", "")
            if bool(getattr(status, "terminal", False)):
                continue
            out.append(
                CommitmentSignal(
                    commitment_id=str(getattr(item, "commitment_id", "") or ""),
                    kind=str(getattr(getattr(item, "kind", ""), "value", "") or ""),
                    label=str(
                        getattr(item, "description", "")
                        or getattr(item, "target_activity", "")
                        or ""
                    ),
                    player=str(getattr(item, "person_id", "") or ""),
                    due_at=float(getattr(item, "due_at", 0.0) or 0.0),
                )
            )
            if len(out) >= MAX_COMMITMENT_SIGNALS:
                break
        return tuple(item for item in out if item.commitment_id)

    async def _memories(self) -> tuple[MemorySignal, ...]:
        """最近几条记忆 + 它们的**结构化域**（§十七：只作支持性上下文）。

        ★真机教训：``MemoryManager.list_memories`` 是 **async**（返回协程），
        所以这里必须走 async-aware 的读取器 —— 用同步版本会拿到一个协程对象。
        """
        rows = await self._call(self.memory, "list_memories", limit=MEMORY_SCAN_LIMIT)
        out: list[MemorySignal] = []
        for item in list(rows or ())[:MAX_MEMORY_SIGNALS]:
            text = str(getattr(item, "content", "") or getattr(item, "display_text", "") or "")
            if not text:
                continue
            scope = str(getattr(item, "scope_key", "") or "")
            out.append(
                MemorySignal(
                    text=text,
                    domain=_memory_domain(item, scope),
                    scope=scope,
                    importance=float(getattr(item, "importance", 0.5) or 0.5),
                )
            )
        return tuple(out)

    async def _social_topics(self) -> tuple[str, ...]:
        groups = await self._call(self.social, "groups") or ()
        out: list[str] = []
        for group_id in list(groups)[:SOCIAL_GROUP_LIMIT]:
            topics = await self._call(
                self.topics, "get_active_topics", f"group:{group_id}", limit=1
            )
            for item in list(topics or ())[:1]:
                title = str(getattr(item, "title", "") or "")
                if title:
                    out.append(title)
            if len(out) >= MAX_SOCIAL_SIGNALS:
                break
        return tuple(out)

    async def _social_fatigue(self) -> float:
        groups = await self._call(self.social, "groups") or ()
        for group_id in list(groups)[:SOCIAL_GROUP_LIMIT]:
            snapshot = await self._call(self.social, "group_snapshot", group_id)
            if not isinstance(snapshot, dict):
                continue
            attention = snapshot.get("attention")
            if isinstance(attention, dict):
                try:
                    return float(attention.get("fatigue") or 0.0)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _planner_candidates(self) -> tuple[str, ...]:
        plan = getattr(self.activity, "active_plan", None)
        if plan is None:
            return ()
        try:
            eligible = plan.eligible()
        except Exception:  # noqa: BLE001
            return ()
        return tuple(str(getattr(item, "activity", "") or "") for item in list(eligible)[:6])

    async def _task_states(self) -> tuple[str, ...]:
        if self.task_states_provider is None:
            return ()
        try:
            value = await self._await(self.task_states_provider())
        except Exception:  # noqa: BLE001
            return ()
        try:
            return tuple(str(getattr(item, "value", item) or "").upper() for item in value)
        except TypeError:
            return ()

    def _user_interaction_at(self) -> float:
        observation = getattr(self.activity, "last_observation", None)
        if not isinstance(observation, dict):
            return 0.0
        try:
            return float(observation.get("user_interaction_at") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _minecraft_online(self) -> bool:
        service = self.minecraft
        if service is None or not bool(getattr(service, "enabled", False)):
            return False
        try:
            snapshot = service.snapshot()
        except Exception:  # noqa: BLE001 - 读不到就当离线（保守）
            return False
        connection = (snapshot or {}).get("connection") or {}
        return str(connection.get("status") or "") == "ONLINE"

    def _sleeping(self) -> bool:
        if self.presence is None:
            return False
        for name in ("is_sleeping",):
            method = getattr(self.presence, name, None)
            if method is None:
                continue
            try:
                return bool(method())
            except Exception:  # noqa: BLE001
                return False
        return False

    def _quiet_hours(self) -> bool:
        """§十四：DND 或深夜静默时段（都用既有 presence 配置，不加新配置）。"""
        if self.presence is None:
            return False
        for name in ("in_dnd", "is_night"):
            method = getattr(self.presence, name, None)
            if method is None:
                continue
            try:
                if bool(method()):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _degraded(self) -> str:
        reason = str(getattr(self.activity, "degraded_reason", "") or "")
        return reason

    async def _await(self, value: Any) -> Any:
        """把一个"可能是协程"的只读结果变成值（真机上的 provider 多半是 async）。"""
        if hasattr(value, "__await__"):
            try:
                return await value
            except Exception:  # noqa: BLE001
                self._debug("await failed")
                return None
        return value

    def _sync(self, target: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        if target is None:
            return None
        method = getattr(target, name, None)
        if method is None:
            return None
        try:
            return method(*args, **kwargs)
        except Exception:  # noqa: BLE001 - 读失败 = 没有这个信号
            self._debug(f"{name} failed")
            return None
