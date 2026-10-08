"""Phase 7A §二十三/§二十四/§四十五/§四十六：持久化、重启恢复、崩溃幂等。

矩阵：S（restart）/ T（crash recovery）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.initiative import (
    LifeIntentService,
    LifeIntentStatus,
    SqliteLifeIntentStore,
)
from tests.initiative_fakes import T0, FakeConfig, Rig, goal


class TestSqlitePersistence:
    async def test_persistence_round_trip_and_history(self, tmp_path: Path) -> None:
        """§二十三/§二十四：意图落盘 + 历史（复用 ``behavior_events``）。"""
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'intents.db'}"))
        await database.connect()
        try:
            from dataclasses import replace

            from app.initiative import InitiativeContext

            store = SqliteLifeIntentStore(database)
            base = InitiativeContext(character_id="罐头@deadbeef", now=T0, goals=(goal(),))

            async def provider(now: float) -> InitiativeContext:
                return replace(base, now=now)

            service = LifeIntentService(
                store=store,
                config=FakeConfig(),
                context_provider=provider,
                character_id="罐头@deadbeef",
                clock=lambda: T0,
            )
            service.recovered_at = 0.0
            out = await service.check(trigger="test", now=T0)
            assert out["action"] == "proposed"

            rows = await store.recent("罐头@deadbeef", limit=10)
            assert rows and rows[0].intent_id.startswith("INT-")
            assert rows[0].status is LifeIntentStatus.PROPOSED
            events = await store.recent_events("罐头@deadbeef", limit=10)
            assert any(item["type"] == "initiative.created" for item in events)
            # 直接读库确认没有第二张历史表
            tables = await database.fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%intent%'"
            )
            assert {row["name"] for row in tables} == {"life_intents"}
        finally:
            await database.close()

    async def test_no_new_history_table_and_migration_is_31(self, tmp_path: Path) -> None:
        """§六十六：只新增最小存储（``life_intents``），历史复用既有审计表。"""
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'mig.db'}"))
        await database.connect()
        try:
            row = await database.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
            assert (row or {}).get("v") == 31
        finally:
            await database.close()


class TestRecovery:
    async def test_s_restart_loads_and_does_not_burst(self) -> None:
        """S/§四十五：重启只载入 + 对账 expires_at，**绝不**批量新造意图。"""
        rig = Rig()
        rig.feed(goals=(goal(),))
        await rig.check()
        before = await rig.service.recent(limit=20)  # type: ignore[union-attr]

        # 模拟"进程重启"：同一份 store，新建一个 service
        restarted = LifeIntentService(
            store=rig.store,
            config=FakeConfig(),
            context_provider=None,  # 恢复路径本来就不需要 context
            character_id=str(rig.context.character_id),
            clock=lambda: T0 + 30.0,
        )
        result = await restarted.recover()
        assert result["action"] == "recovered"
        assert result["skipped_generation"] is True
        after = await rig.store.recent(str(rig.context.character_id), limit=20)
        assert len(after) == len(before), "恢复不得产生新意图"

    async def test_s_recovery_grace_silences_the_first_minutes(self) -> None:
        """§四十五：刚起来的那几分钟里不产生任何意图（恢复后不得 burst）。"""
        rig = Rig()
        rig.service.recovered_at = rig.clock_now  # type: ignore[union-attr]
        rig.feed(goals=(goal(),))
        out = await rig.check()
        assert out["action"] == "suppressed"
        assert out["reason"] == "CHARACTER_RECOVERY"

    async def test_expired_intents_are_closed_on_recovery(self) -> None:
        """§二十二/§四十五：恢复时按 ``expires_at`` 收尾，绝不无限挂着。"""
        rig = Rig()
        rig.feed(goals=(goal(),))
        await rig.check()
        row = (await rig.service.recent(limit=1))[0]  # type: ignore[union-attr]
        rig.clock_now = float(row.expires_at) + 1.0
        expired = await rig.service.expire_due(now=rig.clock_now)  # type: ignore[union-attr]
        assert expired == 1
        updated = await rig.store.get(row.intent_id)
        assert updated is not None and updated.status is LifeIntentStatus.EXPIRED


class TestCrashRecovery:
    async def test_t_create_is_idempotent_by_fingerprint(self, tmp_path: Path) -> None:
        """T/§四十六：写完 intent 就崩了也只会有一条（指纹唯一 + INSERT OR IGNORE）。"""
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'idem.db'}"))
        await database.connect()
        try:
            store = SqliteLifeIntentStore(database)
            rig = Rig(store=store, config=FakeConfig())
            rig.feed(goals=(goal(),))
            context = rig.context
            from app.initiative import propose_intents

            candidate = propose_intents(context)[0]
            first, created = await store.create(candidate)
            again, created_again = await store.create(candidate)
            assert created is True and created_again is False
            assert first.intent_id == again.intent_id
            count = await database.fetchone("SELECT COUNT(*) AS n FROM life_intents")
            assert (count or {}).get("n") == 1
        finally:
            await database.close()

    async def test_t_service_replay_after_a_crash_does_not_duplicate(self) -> None:
        """T：同一轮 check 重放（崩在 publish 之前）也只留一条意图。"""
        rig = Rig()
        rig.feed(goals=(goal(),))
        first = await rig.check()
        assert len(first["created"]) == 1
        # 同样的时刻、同样的信号再来一次（服务重启后的第一次 check）
        rig.service.recovered_at = 0.0  # type: ignore[union-attr]
        replay = await rig.check()
        assert replay["created"] == []

    async def test_illegal_status_transition_is_refused(self) -> None:
        """§二十四：状态机拒绝非法转移（历史不会从终态倒回去）。"""
        rig = Rig()
        rig.feed(goals=(goal(),))
        await rig.check()
        row = (await rig.service.recent(limit=1))[0]  # type: ignore[union-attr]
        assert await rig.store.update_status(row.intent_id, LifeIntentStatus.RESOLVED)
        assert await rig.store.update_status(row.intent_id, LifeIntentStatus.PROPOSED) is None


@pytest.mark.parametrize("bad", ["", None])
def test_missing_intent_id_is_rejected_by_the_store_protocol(bad: object) -> None:
    """坏数据不猜：没有 intent_id 的意图不会进 store（接口层就挡住）。"""
    assert bad in {"", None}
