"""Phase 6C §四十三-§四十五/§六十七：计划的持久化、版本与作废（矩阵 O/P/Q）。

两条硬要求：

* **版本只在内容真的变了时才 +1**（§四十三），且**绝不回退**；
* **旧计划永不删除**（§四十四）—— 只标 ``SUPERSEDED``，历史与审计都在。

两种 store 都要过同一套断言（InMemory 与 SQLite 语义必须一致）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.activity import (
    ActivityPlan,
    InMemoryActivityStore,
    ItemReason,
    PlanItem,
    PlanStatus,
    SqliteActivityStore,
    plan_id_for,
)
from app.activity.plan import parse_plan_id
from app.config.settings import DatabaseConfig
from app.database.database import Database


def make_plan(
    plan_id: str, version: int, activity: str, at: float, **overrides: Any
) -> ActivityPlan:
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "character_id": "罐头@deadbeef",
        "plan_version": version,
        "generated_at": at,
        "horizon_start": at,
        "horizon_end": at + 4 * 3600,
        "items": (
            PlanItem(
                activity=activity,
                planned_start=at,
                planned_end=at + 3600,
                reason=ItemReason.FREE.value,
            ),
        ),
        "source": "FREE",
        "trigger": "episode_ended",
    }
    payload.update(overrides)
    return ActivityPlan(**payload)


# ---------------------------------------------------------------- 计划号与形状


class TestPlanIdentity:
    def test_plan_id_shape_matches_episode_style(self) -> None:
        """``PLAN-YYYYMMDD-NNN``（与 Episode 同一套可读、可审计的形状）。"""
        assert plan_id_for("20261015", 7) == "PLAN-20261015-007"
        assert parse_plan_id("PLAN-20261015-007") == ("20261015", 7)
        for bad in ("ACT-20261015-007", "PLAN-20261015", "PLAN-2026101-007", "PLAN-20261015-x"):
            assert parse_plan_id(bad) is None

    def test_items_are_ordered_and_bounded(self) -> None:
        plan = make_plan("PLAN-20261015-001", 1, "reading", 100.0)
        assert plan.first_item() is not None
        assert plan.horizon_seconds == pytest.approx(4 * 3600)

    def test_payload_roundtrip_is_lossless(self) -> None:
        """JSON 往返不丢字段（InMemory store 就是靠这个模拟落盘的）。"""
        original = make_plan("PLAN-20261015-001", 3, "gaming", 1_700_000_000.0)
        restored = ActivityPlan.from_payload(original.to_payload())
        assert restored.to_payload() == original.to_payload()
        assert restored.content_hash == original.content_hash
        assert restored.items[0].activity == "gaming"


# ---------------------------------------------------------------- O：版本


class TestPlanVersion:
    def test_o_same_content_gives_the_same_signature(self) -> None:
        """§四十三：内容一样 → 签名一样（版本才不会被无意义地刷）。"""
        first = make_plan("PLAN-20261015-001", 1, "reading", 100.0)
        second = make_plan("PLAN-20261015-002", 9, "reading", 100.0)
        assert first.content_signature() == second.content_signature()
        assert first.content_hash == second.content_hash

    def test_o_signature_ignores_the_generation_moment(self) -> None:
        """同一份安排晚一分钟生成，**还是**同一份安排（用相对偏移而不是绝对时间）。"""
        early = make_plan("PLAN-20261015-001", 1, "reading", 100.0)
        later = make_plan("PLAN-20261015-002", 1, "reading", 160.0)
        assert early.content_signature() == later.content_signature()

    def test_o_different_content_changes_the_signature(self) -> None:
        base = make_plan("PLAN-20261015-001", 1, "reading", 100.0)
        other_activity = make_plan("PLAN-20261015-002", 1, "gaming", 100.0)
        other_source = make_plan("PLAN-20261015-003", 1, "reading", 100.0, source="MIXED")
        assert base.content_signature() != other_activity.content_signature()
        assert base.content_signature() != other_source.content_signature()

    def test_o_version_is_not_part_of_the_signature(self) -> None:
        assert (
            make_plan("PLAN-20261015-001", 1, "reading", 100.0).content_signature()
            == make_plan("PLAN-20261015-002", 42, "reading", 100.0).content_signature()
        )


# ---------------------------------------------------------------- P/Q：作废与幂等


class TestPlanStoreSemantics:
    async def _store(self) -> InMemoryActivityStore:
        return InMemoryActivityStore()

    async def test_p_creating_a_new_plan_supersedes_the_old_one(self) -> None:
        """§四十四：旧计划标 SUPERSEDED，历史保留（**绝不删除**）。"""
        store = await self._store()
        first = await store.create_plan(
            make_plan(await store.next_plan_id("20261015"), 1, "reading", 100.0)
        )
        second = await store.create_plan(
            make_plan(await store.next_plan_id("20261015"), 2, "gaming", 200.0)
        )
        active = await store.active_plan("罐头@deadbeef")
        assert active is not None and active.plan_id == second.plan_id
        old = await store.get_plan(first.plan_id)
        assert old is not None
        assert old.status is PlanStatus.SUPERSEDED
        assert old.superseded_by == second.plan_id
        recent = await store.recent_plans("罐头@deadbeef", 5)
        assert [plan.plan_id for plan in recent] == [second.plan_id, first.plan_id]

    async def test_p_history_keeps_every_generation(self) -> None:
        store = await self._store()
        for index in range(4):
            await store.create_plan(
                make_plan(
                    await store.next_plan_id("20261015"),
                    index + 1,
                    "reading",
                    100.0 + index,
                )
            )
        recent = await store.recent_plans("罐头@deadbeef", 10)
        assert len(recent) == 4
        assert sum(1 for plan in recent if plan.active) == 1

    async def test_q_active_plan_is_unique_per_character(self) -> None:
        store = await self._store()
        mine = make_plan(await store.next_plan_id("20261015"), 1, "reading", 100.0)
        await store.create_plan(mine)
        other = make_plan("PLAN-20261015-099", 1, "gaming", 100.0, character_id="另一个人")
        await store.create_plan(other)
        assert (await store.active_plan("罐头@deadbeef")).plan_id == mine.plan_id  # type: ignore[union-attr]
        assert (await store.active_plan("另一个人")).plan_id == other.plan_id  # type: ignore[union-attr]

    async def test_q_plan_ids_are_sequential_per_day(self) -> None:
        store = await self._store()
        first = await store.next_plan_id("20261015")
        await store.create_plan(make_plan(first, 1, "reading", 100.0))
        second = await store.next_plan_id("20261015")
        assert first == "PLAN-20261015-001"
        assert second == "PLAN-20261015-002"

    async def test_q_missing_plan_is_none_not_an_error(self) -> None:
        store = await self._store()
        assert await store.get_plan("PLAN-19700101-001") is None
        assert await store.active_plan("没有人") is None


# ---------------------------------------------------------------- SQLite 版


class TestSqlitePlanStore:
    async def test_sqlite_matches_the_in_memory_semantics(self, tmp_path: Path) -> None:
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'plans.db'}"))
        await database.connect()
        try:
            store = SqliteActivityStore(database)
            first_id = await store.next_plan_id("20261015")
            assert first_id == "PLAN-20261015-001"
            await store.create_plan(make_plan(first_id, 1, "reading", 100.0))
            active = await store.active_plan("罐头@deadbeef")
            assert active is not None
            assert active.plan_id == first_id
            assert active.items[0].activity == "reading"
            assert active.constraints == {} or isinstance(active.constraints, dict)

            second_id = await store.next_plan_id("20261015")
            assert second_id == "PLAN-20261015-002"
            await store.create_plan(make_plan(second_id, 2, "gaming", 200.0))
            assert (await store.active_plan("罐头@deadbeef")).plan_id == second_id  # type: ignore[union-attr]
            old = await store.get_plan(first_id)
            assert old is not None and old.status is PlanStatus.SUPERSEDED
            assert old.superseded_by == second_id
            recent = await store.recent_plans("罐头@deadbeef", 5)
            assert [plan.plan_id for plan in recent] == [second_id, first_id]
            # 候选与 constraints 也能往返（JSON 列）
            assert all(isinstance(candidate.to_payload(), dict) for candidate in active.candidates)
        finally:
            await database.close()

    async def test_sqlite_partial_unique_index_keeps_one_active_plan(self, tmp_path: Path) -> None:
        """§四十四：数据库层也兜住"一个角色只能有一份 ACTIVE_PLAN"。"""
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'plans.db'}"))
        await database.connect()
        try:
            row = await database.fetchone(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
                " AND name = 'idx_activity_plans_active'"
            )
            assert row is not None, "迁移 30 应该建了这个 partial unique index"
            count = await database.fetchone(
                "SELECT COUNT(*) AS n FROM activity_plans WHERE status = 'ACTIVE_PLAN'"
            )
            assert (count or {}).get("n") == 0
        finally:
            await database.close()

    async def test_latest_migration_is_idempotent(self, tmp_path: Path) -> None:
        """§六十七：迁移可重复执行（再连一次不会炸、也不会重复建表）。

        Phase 7D 把上限推到 **33**（``task_agent_plans``）；Phase 7E 推到 **34**
        （``procedural_skills`` + ``procedural_skill_evidence``）；Phase 7E.1 推到 **35**
        （技能任务绑定 + ``subject_key`` 列，并删掉 34 的派生键使用链表）；Phase 7E.1.1 推到
        **36**（按列回填历史证据 payload 的 ``skill_id``）—— 这条断言就是"冻结面"的守卫：
        任何一次新的迁移都必须同时改这里，逼作者想清楚"真的需要新表吗"。
        """
        path = tmp_path / "again.db"
        for _ in range(2):
            database = Database(DatabaseConfig(url=f"sqlite:///{path}"))
            await database.connect()
            version = await database.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
            assert (version or {}).get("v") == 36
            await database.close()

    async def test_plan_write_is_transactional(self, tmp_path: Path) -> None:
        """§六十七：作废旧计划 + 插入新计划在**一个事务**里（不会出现"两份都 active"）。"""
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'tx.db'}"))
        await database.connect()
        try:
            store = SqliteActivityStore(database)
            first = await store.next_plan_id("20261015")
            await store.create_plan(make_plan(first, 1, "reading", 100.0))
            second = await store.next_plan_id("20261015")
            await store.create_plan(make_plan(second, 2, "gaming", 200.0))
            active = await database.fetchall(
                "SELECT plan_id FROM activity_plans WHERE status = 'ACTIVE_PLAN'"
            )
            assert [row["plan_id"] for row in active] == [second]
        finally:
            await database.close()


def test_plan_lifecycle_never_touches_episodes() -> None:
    """§四十六：计划表与 Episode 表是两回事 —— 计划**不会**创建 Episode。"""
    import ast

    source = (Path(__file__).resolve().parents[1] / "app" / "activity" / "plan.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "build_episode" not in imported
    assert "ActivityEpisode" not in imported


def test_no_legacy_schedule_tables_are_used() -> None:
    """§四十五：不许出现 ``future_schedule`` / ``routine_schedule`` 这类重叠存储。"""
    root = Path(__file__).resolve().parents[1] / "app"
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("future_schedule", "routine_schedule", "activity_plan_v2"):
            assert forbidden not in source, (path, forbidden)


def test_asyncio_is_not_needed_for_pure_planning() -> None:
    """规划是纯计算：``ActivityPlanner.plan_next`` 不是协程（§六十五：不 await 世界）。"""
    from app.activity import ActivityPlanner

    assert not asyncio.iscoroutinefunction(ActivityPlanner.plan_next)
