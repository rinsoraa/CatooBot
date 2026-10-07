"""Phase 6A §二十五/§二十六/§三十三/§四十四：Episode 的持久化与并发守卫。

两个实现，语义完全一致：

* :class:`InMemoryActivityStore` —— 单元测试用；每次写入**JSON 往返**一遍
  （沿用 TaskRuntime 的纪律：这样"忘了落盘"这类 bug 在单测里就会暴露）。
* :class:`SqliteActivityStore` —— 生产用；**每个转移都是一次事务 + compare-and-set**，
  并靠 partial unique index 在数据库层保证"每个角色最多一个 live primary Episode"
  与"同一个 Episode 的同一个一次性转移只落一次"（§二十六/§三十三/§四十四）。
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


# ---------------------------------------------------------------- InMemory


class InMemoryActivityStore:
    """进程内实现（测试用；每次读写都做一次 JSON 往返，模拟真的落盘）。"""

    def __init__(self) -> None:
        self._episodes: dict[str, ActivityEpisode] = {}
        self._order: list[str] = []
        self._transitions: dict[str, list[dict[str, Any]]] = {}
        self._sequence: dict[str, int] = {}

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
