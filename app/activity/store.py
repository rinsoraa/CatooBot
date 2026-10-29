"""Phase 6A §二十五/§二十六/§三十三/§四十四 + Phase 6C §四十三-§四十七：持久化与并发守卫。

两个实现，语义完全一致：

* :class:`InMemoryActivityStore` —— 单元测试用；每次写入**JSON 往返**一遍
  （沿用 TaskRuntime 的纪律：这样"忘了落盘"这类 bug 在单测里就会暴露）。
* :class:`SqliteActivityStore` —— 生产用；**每个转移都是一次事务 + compare-and-set**，
  并靠 partial unique index 在数据库层保证"每个角色最多一个 live primary Episode"
  与"同一个 Episode 的同一个一次性转移只落一次"（§二十六/§三十三/§四十四）。

Phase 6C 在同一层加**计划存储**（§四十五：``activity_plans`` + ``activity_plan_items``）：

* 每个角色同一时间**最多一份** ``ACTIVE_PLAN``（partial unique index，和 Episode 同一套思路）；
* 写新计划 = **一个事务**里"把旧计划标 SUPERSEDED + 插入新计划与它的条目"（§六十七：事务性）；
* 旧计划**永不删除**（§四十四：历史保留），因此同一份内容重复写也不会产生新行
  （§六十七：幂等由 ``content_hash`` + 调用方判重保证）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Protocol

from app.activity.model import (
    LIVE_ACTIVITY_STATUSES,
    ONE_SHOT_TRANSITIONS,
    ActivityEpisode,
    ActivitySource,
    ActivityStatus,
    TransitionReason,
    episode_id_for,
    parse_episode_id,
)
from app.activity.plan import (
    ActivityPlan,
    Candidate,
    PlanItem,
    PlanStatus,
    plan_id_for,
)


class ActivityError(RuntimeError):
    """Activity 域的错误基类（上层据此降级，绝不把异常冒泡到聊天/任务）。"""


class ActivityConflict(ActivityError):
    """违反不变量（例如同一个角色已经有一个 live Episode）。"""


class InvalidActivityTransition(ActivityError):
    """状态机拒绝的转移（§六）。"""


class ActivityStore(Protocol):
    """存储契约（InMemory / Sqlite 都要满足）。"""

    async def next_episode_id(self, day: str) -> str: ...

    async def create(self, episode: ActivityEpisode) -> ActivityEpisode: ...

    async def get(self, episode_id: str) -> ActivityEpisode | None: ...

    async def live(self, character_id: str) -> ActivityEpisode | None: ...

    async def recent(self, character_id: str, limit: int = 10) -> list[ActivityEpisode]: ...

    async def transition(
        self,
        episode_id: str,
        *,
        expect: Iterable[ActivityStatus],
        to: ActivityStatus,
        reason: str,
        at: float,
        fields: dict[str, Any] | None = None,
    ) -> ActivityEpisode | None: ...

    async def log_transition(
        self, *, episode_id: str, transition: str, reason: str, source: str, at: float
    ) -> bool: ...

    async def recent_transitions(self, episode_id: str, limit: int = 3) -> list[dict[str, Any]]: ...

    # ---- Phase 6C：计划（§四十三-§四十七） ----

    async def next_plan_id(self, day: str) -> str: ...

    async def create_plan(self, plan: ActivityPlan) -> ActivityPlan: ...

    async def active_plan(self, character_id: str) -> ActivityPlan | None: ...

    async def get_plan(self, plan_id: str) -> ActivityPlan | None: ...

    async def recent_plans(self, character_id: str, limit: int = 5) -> list[ActivityPlan]: ...


# ---------------------------------------------------------------- InMemory


class InMemoryActivityStore:
    """进程内实现（测试用；每次读写都做一次 JSON 往返，模拟真的落盘）。"""

    def __init__(self) -> None:
        self._episodes: dict[str, ActivityEpisode] = {}
        self._order: list[str] = []
        self._transitions: dict[str, list[dict[str, Any]]] = {}
        self._sequence: dict[str, int] = {}
        #: Phase 6C：计划（内容 + 顺序 + 每天的计划号序列）
        self._plans: dict[str, ActivityPlan] = {}
        self._plan_order: list[str] = []
        self._plan_sequence: dict[str, int] = {}

    # ------------------------------------------------------------ 读

    async def next_episode_id(self, day: str) -> str:
        seq = int(self._sequence.get(str(day), 0)) + 1
        self._sequence[str(day)] = seq
        return episode_id_for(day, seq)

    async def get(self, episode_id: str) -> ActivityEpisode | None:
        raw = self._episodes.get(str(episode_id))
        return _copy(raw) if raw is not None else None

    async def live(self, character_id: str) -> ActivityEpisode | None:
        found = [
            episode
            for episode in self._ordered()
            if episode.character_id == str(character_id)
            and episode.status in LIVE_ACTIVITY_STATUSES
        ]
        return _copy(found[-1]) if found else None

    async def recent(self, character_id: str, limit: int = 10) -> list[ActivityEpisode]:
        found = [
            episode
            for episode in reversed(self._ordered())
            if episode.character_id == str(character_id)
        ]
        return [_copy(episode) for episode in found[: max(1, int(limit))]]

    async def recent_transitions(self, episode_id: str, limit: int = 3) -> list[dict[str, Any]]:
        rows = list(self._transitions.get(str(episode_id), []))
        return [dict(row) for row in rows[-max(1, int(limit)) :]]

    # ------------------------------------------------------------ 写

    async def create(self, episode: ActivityEpisode) -> ActivityEpisode:
        existing = await self.live(episode.character_id)
        if existing is not None:
            raise ActivityConflict(
                f"角色 {episode.character_id} 已经有一个未结束的 Episode（{existing.episode_id}）"
            )
        if episode.episode_id in self._episodes:
            raise ActivityConflict(f"Episode ID 重复：{episode.episode_id}")
        self._episodes[episode.episode_id] = _copy(episode)
        self._order.append(episode.episode_id)
        day_seq = parse_episode_id(episode.episode_id)
        if day_seq is not None:
            day, seq = day_seq
            self._sequence[day] = max(int(self._sequence.get(day, 0)), seq)
        return _copy(episode)

    async def transition(
        self,
        episode_id: str,
        *,
        expect: Iterable[ActivityStatus],
        to: ActivityStatus,
        reason: str,
        at: float,
        fields: dict[str, Any] | None = None,
    ) -> ActivityEpisode | None:
        current = self._episodes.get(str(episode_id))
        if current is None:
            return None
        allowed = {
            ActivityStatus(status) if not isinstance(status, ActivityStatus) else status
            for status in expect
        }
        if current.status not in allowed:
            return None  # CAS 失败：别人已经先动过了（§四十四 只能有一次最终转移）
        if not current.can_transition_to(to):
            raise InvalidActivityTransition(f"{current.status.value} → {to.value} 不是合法转移")
        for key, value in dict(fields or {}).items():
            setattr(current, key, value)
        current.status = to
        current.transition_reason = str(reason)
        current.updated_at = float(at)
        if to.terminal and not current.ended_at:
            current.ended_at = float(at)
        self._episodes[str(episode_id)] = _copy(current)
        return _copy(current)

    async def log_transition(
        self, *, episode_id: str, transition: str, reason: str, source: str, at: float
    ) -> bool:
        rows = self._transitions.setdefault(str(episode_id), [])
        if str(transition) in ONE_SHOT_TRANSITIONS and any(
            str(row.get("transition")) == str(transition) for row in rows
        ):
            return False  # 一次性转移已经记过 → 不再发布（§三十三）
        rows.append(
            {
                "episode_id": str(episode_id),
                "transition": str(transition),
                "reason": str(reason),
                "source": str(source),
                "at": float(at),
            }
        )
        return True

    # ------------------------------------------------------------ 计划（Phase 6C）

    async def next_plan_id(self, day: str) -> str:
        seq = int(self._plan_sequence.get(str(day), 0)) + 1
        self._plan_sequence[str(day)] = seq
        return plan_id_for(day, seq)

    async def create_plan(self, plan: ActivityPlan) -> ActivityPlan:
        """落一份新计划（旧 ACTIVE_PLAN 标 SUPERSEDED，历史保留，§四十四）。

        同一个 ``plan_id`` **不许**二次写入：那会静默覆盖掉一条历史计划，
        而 §九 明确要求"旧 Plan 不得删除"。撞上就抛 ``ActivityConflict``（和 Episode 一样）。
        """
        if str(plan.plan_id) in self._plans:
            raise ActivityConflict(f"计划号 {plan.plan_id} 已经存在（历史计划不允许被覆盖）")
        stored = ActivityPlan.from_payload(plan.to_payload())  # JSON 往返：模拟真落盘
        for existing in self._plans.values():
            if existing.character_id == stored.character_id and existing.active:
                existing.status = PlanStatus.SUPERSEDED
                existing.superseded_by = stored.plan_id
        self._plans[stored.plan_id] = ActivityPlan.from_payload(stored.to_payload())
        self._plan_order.append(stored.plan_id)
        return ActivityPlan.from_payload(stored.to_payload())

    async def get_plan(self, plan_id: str) -> ActivityPlan | None:
        raw = self._plans.get(str(plan_id))
        return ActivityPlan.from_payload(raw.to_payload()) if raw is not None else None

    async def active_plan(self, character_id: str) -> ActivityPlan | None:
        found = [
            plan
            for plan in self._plans.values()
            if plan.character_id == str(character_id) and plan.active
        ]
        if not found:
            return None
        # 版本最高的那份（同一角色理论上只有一份 active，这里仍然取稳定的那一份）
        chosen = max(found, key=lambda item: (int(item.plan_version), str(item.plan_id)))
        return ActivityPlan.from_payload(chosen.to_payload())

    async def recent_plans(self, character_id: str, limit: int = 5) -> list[ActivityPlan]:
        found = [plan for plan in self._plans.values() if plan.character_id == str(character_id)]
        found.sort(key=lambda item: (float(item.generated_at), str(item.plan_id)), reverse=True)
        return [
            ActivityPlan.from_payload(plan.to_payload()) for plan in found[: max(1, int(limit))]
        ]

    # ------------------------------------------------------------ 内部

    def _ordered(self) -> list[ActivityEpisode]:
        return [self._episodes[key] for key in self._order if key in self._episodes]


def _copy(episode: ActivityEpisode) -> ActivityEpisode:
    """JSON 往返（保证"没落盘"的东西不会在测试里凭空活着）。"""
    return ActivityEpisode.from_payload(json.loads(json.dumps(episode.to_payload())))


# ---------------------------------------------------------------- Sqlite


class SqliteActivityStore:
    """SQLite 实现：事务 + CAS + partial unique index（§二十六/§三十三/§四十四）。"""

    def __init__(self, database: Any, *, logger: Any = None) -> None:
        self._db = database
        self._log = logger

    # ------------------------------------------------------------ 读

    async def next_episode_id(self, day: str) -> str:
        prefix = f"ACT-{str(day)}-"
        row = await self._db.fetchone(
            "SELECT MAX(CAST(SUBSTR(episode_id, ?) AS INTEGER)) AS seq"
            " FROM activity_episodes WHERE episode_id LIKE ?",
            (len(prefix) + 1, f"{prefix}%"),
        )
        current = int((row or {}).get("seq") or 0)
        return episode_id_for(day, current + 1)

    async def get(self, episode_id: str) -> ActivityEpisode | None:
        row = await self._db.fetchone(
            "SELECT * FROM activity_episodes WHERE episode_id = ?", (str(episode_id),)
        )
        return _from_row(row) if row is not None else None

    async def live(self, character_id: str) -> ActivityEpisode | None:
        placeholders = ",".join("?" for _ in LIVE_ACTIVITY_STATUSES)
        rows = await self._db.fetchall(
            "SELECT * FROM activity_episodes WHERE character_id = ? AND status IN"
            f" ({placeholders}) ORDER BY created_at ASC",
            (str(character_id), *sorted(status.value for status in LIVE_ACTIVITY_STATUSES)),
        )
        return _from_row(rows[-1]) if rows else None

    async def recent(self, character_id: str, limit: int = 10) -> list[ActivityEpisode]:
        rows = await self._db.fetchall(
            "SELECT * FROM activity_episodes WHERE character_id = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (str(character_id), max(1, int(limit))),
        )
        return [_from_row(row) for row in rows]

    async def recent_transitions(self, episode_id: str, limit: int = 3) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT transition, reason, source, at FROM activity_transitions"
            " WHERE episode_id = ? ORDER BY seq DESC LIMIT ?",
            (str(episode_id), max(1, int(limit))),
        )
        return list(reversed([dict(row) for row in rows]))

    # ------------------------------------------------------------ 计划（Phase 6C）

    async def next_plan_id(self, day: str) -> str:
        prefix = f"PLAN-{str(day)}-"
        row = await self._db.fetchone(
            "SELECT MAX(CAST(SUBSTR(plan_id, ?) AS INTEGER)) AS seq"
            " FROM activity_plans WHERE plan_id LIKE ?",
            (len(prefix) + 1, f"{prefix}%"),
        )
        current = int((row or {}).get("seq") or 0)
        return plan_id_for(day, current + 1)

    async def active_plan(self, character_id: str) -> ActivityPlan | None:
        row = await self._db.fetchone(
            "SELECT * FROM activity_plans WHERE character_id = ? AND status = ?"
            " ORDER BY plan_version DESC, created_at DESC LIMIT 1",
            (str(character_id), PlanStatus.ACTIVE_PLAN.value),
        )
        if row is None:
            return None
        return await self._load_plan(row)

    async def get_plan(self, plan_id: str) -> ActivityPlan | None:
        row = await self._db.fetchone(
            "SELECT * FROM activity_plans WHERE plan_id = ?", (str(plan_id),)
        )
        if row is None:
            return None
        return await self._load_plan(row)

    async def recent_plans(self, character_id: str, limit: int = 5) -> list[ActivityPlan]:
        rows = await self._db.fetchall(
            "SELECT * FROM activity_plans WHERE character_id = ?"
            " ORDER BY created_at DESC, plan_id DESC LIMIT ?",
            (str(character_id), max(1, int(limit))),
        )
        return [await self._load_plan(row) for row in rows]

    async def _load_plan(self, row: dict[str, Any]) -> ActivityPlan:
        items = await self._db.fetchall(
            "SELECT * FROM activity_plan_items WHERE plan_id = ? ORDER BY sequence ASC",
            (str(row["plan_id"]),),
        )
        return _plan_from_row(row, items)

    async def create_plan(self, plan: ActivityPlan) -> ActivityPlan:
        """一个事务里"作废旧计划 + 插入新计划与条目"（§六十七：事务性、可重放）。

        §四十四：旧计划只标 SUPERSEDED，**绝不删**。
        """

        def _run(conn: Any) -> None:
            conn.execute(
                "UPDATE activity_plans SET status = ?, superseded_by = ?, updated_at = ?"
                " WHERE character_id = ? AND status = ?",
                (
                    PlanStatus.SUPERSEDED.value,
                    str(plan.plan_id),
                    float(plan.generated_at),
                    str(plan.character_id),
                    PlanStatus.ACTIVE_PLAN.value,
                ),
            )
            conn.execute(
                "INSERT INTO activity_plans (plan_id, character_id, plan_version,"
                " status, generated_at, horizon_start, horizon_end, source, trigger,"
                " content_hash, superseded_by, constraints, candidates, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(plan.plan_id),
                    str(plan.character_id),
                    int(plan.plan_version),
                    plan.status.value,
                    float(plan.generated_at),
                    float(plan.horizon_start),
                    float(plan.horizon_end),
                    str(plan.source),
                    str(plan.trigger),
                    str(plan.content_hash),
                    str(plan.superseded_by),
                    json.dumps(plan.constraints, ensure_ascii=False),
                    json.dumps([item.to_payload() for item in plan.candidates], ensure_ascii=False),
                    float(plan.generated_at),
                    float(plan.generated_at),
                ),
            )
            for sequence, item in enumerate(plan.items):
                payload = item.to_payload()
                conn.execute(
                    "INSERT INTO activity_plan_items (plan_id, sequence, activity,"
                    " planned_start, planned_end, reason, priority, anchor_id, goal_id, score)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(plan.plan_id),
                        int(sequence),
                        payload["activity"],
                        payload["planned_start"],
                        payload["planned_end"],
                        payload["reason"],
                        payload["priority"],
                        payload["anchor_id"],
                        payload["goal_id"],
                        payload["score"],
                    ),
                )

        await self._db.run_in_transaction(_run)
        return plan

    # ------------------------------------------------------------ 写

    async def create(self, episode: ActivityEpisode) -> ActivityEpisode:
        def _run(conn: Any) -> None:
            live = conn.execute(
                "SELECT episode_id FROM activity_episodes WHERE character_id = ?"
                f" AND status IN ({','.join('?' for _ in LIVE_ACTIVITY_STATUSES)}) LIMIT 1",
                (episode.character_id, *sorted(status.value for status in LIVE_ACTIVITY_STATUSES)),
            ).fetchone()
            if live is not None:
                raise ActivityConflict(
                    f"角色 {episode.character_id} 已经有一个未结束的 Episode"
                    f"（{live['episode_id']}）"
                )
            payload = episode.to_payload()
            conn.execute(
                "INSERT INTO activity_episodes (episode_id, character_id, activity_type,"
                " activity_name, location, social_state, tags, started_at, planned_end_at,"
                " ended_at, min_duration, typical_duration, max_duration, status,"
                " transition_reason, source, parent_episode_id, related_task_id,"
                " extension_count, observation, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["episode_id"],
                    payload["character_id"],
                    payload["activity_type"],
                    payload["activity_name"],
                    payload["location"],
                    payload["social_state"],
                    json.dumps(payload["tags"], ensure_ascii=False),
                    payload["started_at"],
                    payload["planned_end_at"],
                    payload["ended_at"],
                    payload["min_duration"],
                    payload["typical_duration"],
                    payload["max_duration"],
                    payload["status"],
                    payload["transition_reason"],
                    payload["source"],
                    payload["parent_episode_id"],
                    payload["related_task_id"],
                    payload["extension_count"],
                    json.dumps(payload["observation"], ensure_ascii=False),
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )

        await self._db.run_in_transaction(_run)
        return episode

    async def transition(
        self,
        episode_id: str,
        *,
        expect: Iterable[ActivityStatus],
        to: ActivityStatus,
        reason: str,
        at: float,
        fields: dict[str, Any] | None = None,
    ) -> ActivityEpisode | None:
        allowed = sorted(
            (status.value if isinstance(status, ActivityStatus) else str(status))
            for status in expect
        )

        def _run(conn: Any) -> bool:
            row = conn.execute(
                "SELECT status FROM activity_episodes WHERE episode_id = ?", (str(episode_id),)
            ).fetchone()
            if row is None or str(row["status"]) not in allowed:
                return False  # CAS 失败：别人已经先动过了
            updates = dict(fields or {})
            updates["status"] = to.value
            updates["transition_reason"] = str(reason)
            updates["updated_at"] = float(at)
            if to.terminal:
                updates.setdefault("ended_at", float(at))
            columns = ", ".join(f"{key} = ?" for key in updates)
            params: list[Any] = []
            for key, value in updates.items():
                if key == "observation":
                    params.append(json.dumps(value or {}, ensure_ascii=False))
                elif key == "tags":
                    params.append(json.dumps(value or [], ensure_ascii=False))
                else:
                    params.append(value)
            cursor = conn.execute(
                f"UPDATE activity_episodes SET {columns} WHERE episode_id = ? AND status IN"
                f" ({','.join('?' for _ in allowed)})",
                (*params, str(episode_id), *allowed),
            )
            return bool(cursor.rowcount)

        changed = await self._db.run_in_transaction(_run)
        if not changed:
            return None
        return await self.get(episode_id)

    async def log_transition(
        self, *, episode_id: str, transition: str, reason: str, source: str, at: float
    ) -> bool:
        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO activity_transitions"
                " (episode_id, transition, reason, source, at) VALUES (?, ?, ?, ?, ?)",
                (str(episode_id), str(transition), str(reason), str(source), float(at)),
            )
            return bool(cursor.rowcount)

        return bool(await self._db.run_in_transaction(_run))


def _from_row(row: dict[str, Any]) -> ActivityEpisode:
    """数据库行 → Episode（JSON 列就地解析；坏数据不猜）。"""
    payload = dict(row)
    for key in ("tags", "observation"):
        raw = payload.get(key)
        if isinstance(raw, str):
            try:
                payload[key] = json.loads(raw)
            except (TypeError, ValueError):
                payload[key] = [] if key == "tags" else {}
    payload.setdefault("character_id", "")
    payload.setdefault("transition_reason", "")
    payload.setdefault("source", ActivitySource.SYSTEM.value)
    payload.setdefault("activity_type", "system")
    payload.setdefault("status", ActivityStatus.SCHEDULED.value)
    return ActivityEpisode.from_payload(payload)


#: 便捷：把 TransitionReason 枚举转成字符串（调用方常写 reason=TransitionReason.X）
def reason_value(reason: TransitionReason | str) -> str:
    return reason.value if isinstance(reason, TransitionReason) else str(reason)


def _plan_from_row(row: dict[str, Any], items: list[dict[str, Any]]) -> ActivityPlan:
    """数据库行 → 计划（条目来自 ``activity_plan_items``，候选来自 JSON 列；坏数据不猜）。"""
    payload = dict(row)
    for key in ("constraints", "candidates"):
        raw = payload.get(key)
        if isinstance(raw, str):
            try:
                payload[key] = json.loads(raw)
            except (TypeError, ValueError):
                payload[key] = {} if key == "constraints" else []
    # Phase 7B §八：计划项**没有**单独的 intent_id 列（不新增迁移），
    # 归因从同一份计划的候选 JSON 里补回来 —— 于是重载之后界面与 API 都还答得出"哪条意图影响了它"。
    intent_by_activity = {
        str(item.get("activity") or ""): str(item.get("intent_id") or "")
        for item in (payload.get("candidates") or [])
        if isinstance(item, dict)
    }
    return ActivityPlan(
        plan_id=str(payload.get("plan_id") or ""),
        character_id=str(payload.get("character_id") or ""),
        plan_version=int(payload.get("plan_version") or 0),
        generated_at=float(payload.get("generated_at") or 0.0),
        horizon_start=float(payload.get("horizon_start") or 0.0),
        horizon_end=float(payload.get("horizon_end") or 0.0),
        status=PlanStatus(str(payload.get("status") or PlanStatus.ACTIVE_PLAN.value)),
        items=tuple(
            PlanItem(
                activity=str(item.get("activity") or ""),
                planned_start=float(item.get("planned_start") or 0.0),
                planned_end=float(item.get("planned_end") or 0.0),
                reason=str(item.get("reason") or ""),
                priority=float(item.get("priority") or 0.0),
                anchor_id=str(item.get("anchor_id") or ""),
                goal_id=str(item.get("goal_id") or ""),
                score=float(item.get("score") or 0.0),
                intent_id=intent_by_activity.get(str(item.get("activity") or ""), ""),
            )
            for item in items
        ),
        candidates=tuple(
            Candidate(
                activity=str(item.get("activity") or ""),
                eligible=bool(item.get("eligible")),
                reason=str(item.get("reason") or ""),
                score=float(item.get("score") or 0.0),
                breakdown={
                    str(key): float(value) for key, value in (item.get("breakdown") or {}).items()
                },
                anchor_id=str(item.get("anchor_id") or ""),
                goal_id=str(item.get("goal_id") or ""),
                order=int(item.get("order") or 0),
            )
            for item in (payload.get("candidates") or [])
        ),
        constraints=dict(payload.get("constraints") or {}),
        source=str(payload.get("source") or ""),
        trigger=str(payload.get("trigger") or ""),
        content_hash=str(payload.get("content_hash") or ""),
        superseded_by=str(payload.get("superseded_by") or ""),
    )
