"""Phase 5C §二十-§二十三/§四十：世界变化 → 记忆对账（只读、可降级、绝不反向写）。

方向只有一条：``WorldPerception → Memory``。这里要钉死：

* 世界说"不在了" → 那条事实 ``INVALIDATED``，**行还在**（历史不删）；
* 世界说"还在" → 刷新 ``last_verified_at``，不当新事实；
* 世界说"读不到"（太远 / 桥挂了）→ **什么都不做**（读不到 ≠ 世界变了）；
* 非世界事实（玩家/任务）永远不会被世界"证伪"，只按时间变旧；
* 记忆层读不到 → 报告如实标 ``degraded``，绝不假装对过账；
* 复核全程只用 SAFE 只读（``dig_capability`` / ``find_blocks``），绝不动世界。
"""

from __future__ import annotations

from typing import Any

from app.memory.minecraft.model import Freshness
from app.memory.minecraft.store import MinecraftMemoryStore
from tests.minecraft_memory_fakes import ANCHOR, UUID_KONGLING, build_bridge


class Clock:
    """可推进的假时钟（只用于"多久没复核算旧"这种时间判断）。"""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


async def seeded(tmp_path: Any, **kwargs: Any):
    """一条 RESOURCE 事实 + 一条 LOCATION 事实 + 一条 PLAYER 事实。"""
    bridge, database, _manager, service = await build_bridge(tmp_path, **kwargs)
    server_id = bridge.server_id()
    service.set_block(ANCHOR, "minecraft:oak_log")
    await bridge.writer.resource_seen(
        server_id=server_id, block_name="minecraft:oak_log", position=ANCHOR
    )
    await bridge.writer.location_seen(
        server_id=server_id,
        kind_label="oak_grove",
        position=dict(ANCHOR),
        content="(100,64,100) 附近有一片橡树。",
        radius=16.0,
    )
    await bridge.writer.player_seen(server_id=server_id, player_uuid=UUID_KONGLING, username="空凛")
    return bridge, database, service, server_id


async def run(bridge: Any) -> Any:
    """跑一次对账并返回**报告对象**（``bridge.reconcile()`` 给的是它的 JSON 投影）。"""
    report = await bridge.reconciler.reconcile(server_id=bridge.server_id())
    bridge.last_reconcile = report.to_payload()
    return report


def by_subject(facts: list[Any], prefix: str) -> Any:
    for fact in facts:
        if fact.subject.startswith(prefix):
            return fact
    raise AssertionError(f"no fact with subject prefix {prefix!r}")


class TestWorldSaysAbsent:
    async def test_block_gone_invalidates_and_keeps_history(self, tmp_path) -> None:
        bridge, database, service, server_id = await seeded(tmp_path)
        service.set_block(ANCHOR, None)  # 世界变了：那块木头没了

        report = await run(bridge)
        assert report.invalidated == 1
        assert report.degraded is False

        resource = by_subject(await bridge.store.all_facts(server_id=server_id), "resource:")
        assert resource.fresh is Freshness.INVALIDATED
        # §二十一：历史不删 —— 行还在，只是状态变了
        assert await bridge.store.facts(server_id=server_id, kinds=None)
        active = await bridge.store.facts(server_id=server_id)
        assert all(fact.fresh is not Freshness.INVALIDATED for fact in active)
        await database.close()

    async def test_block_replaced_by_another_kind_invalidates(self, tmp_path) -> None:
        bridge, database, service, server_id = await seeded(tmp_path)
        service.set_block(ANCHOR, "minecraft:stone")  # 换成了别的方块
        report = await run(bridge)
        assert report.invalidated == 1
        resource = by_subject(await bridge.store.all_facts(server_id=server_id), "resource:")
        assert resource.fresh is Freshness.INVALIDATED
        await database.close()

    async def test_single_cell_location_can_be_invalidated(self, tmp_path) -> None:
        """radius=0 的地点就是那一格本身 → 空了就是不在了（不是"读不到"）。"""
        bridge, database, _manager, service = await build_bridge(tmp_path)
        server_id = bridge.server_id()
        service.set_block(ANCHOR, "minecraft:chest")
        await bridge.writer.location_seen(
            server_id=server_id,
            kind_label="my_chest",
            position=dict(ANCHOR),
            content="(100,64,100) 放着我的箱子。",
            radius=0.0,
        )
        service.set_block(ANCHOR, None)
        report = await run(bridge)
        assert report.invalidated == 1
        fact = by_subject(await bridge.store.all_facts(server_id=server_id), "location:")
        assert fact.fresh is Freshness.INVALIDATED
        await database.close()

    async def test_clustered_location_anchor_is_not_conclusive(self, tmp_path) -> None:
        """radius>0 的坐标只是聚簇锚点：锚点空了说明不了什么 → 不许乱判 INVALIDATED。"""
        bridge, database, _manager, service = await build_bridge(tmp_path)
        server_id = bridge.server_id()
        service.set_block(ANCHOR, "minecraft:oak_log")
        await bridge.writer.location_seen(
            server_id=server_id,
            kind_label="oak_grove",
            position=dict(ANCHOR),
            content="(100,64,100) 附近有一片橡树。",
            radius=16.0,
        )
        service.set_block(ANCHOR, None)
        report = await run(bridge)
        assert report.invalidated == 0
        fact = by_subject(await bridge.store.all_facts(server_id=server_id), "location:")
        assert fact.fresh is not Freshness.INVALIDATED
        await database.close()


class TestWorldSaysPresent:
    async def test_present_refreshes_instead_of_duplicating(self, tmp_path) -> None:
        bridge, database, _service, server_id = await seeded(tmp_path)
        before = by_subject(await bridge.store.facts(server_id=server_id), "resource:")
        report = await run(bridge)
        assert report.confirmed >= 1
        facts = await bridge.store.facts(server_id=server_id)
        resource = by_subject(facts, "resource:")
        assert len([f for f in facts if f.subject.startswith("resource:")]) == 1
        assert resource.observation_count > before.observation_count
        assert resource.last_verified_at >= before.last_verified_at
        assert resource.fresh is Freshness.ACTIVE
        await database.close()


class TestUnknownIsNotAChange:
    async def test_too_far_to_verify_changes_nothing(self, tmp_path) -> None:
        bridge, database, service, server_id = await seeded(tmp_path)
        # 记忆里的位置离她很近，但"她"其实在很远的地方 → 超出扫描范围 → unknown
        service._self_position = {"x": 5000.0, "y": 64.0, "z": 5000.0}
        report = await run(bridge)
        assert report.invalidated == 0
        assert report.confirmed == 0
        assert report.skipped >= 1
        facts = await bridge.store.all_facts(server_id=server_id)
        assert all(fact.fresh is not Freshness.INVALIDATED for fact in facts)
        await database.close()

    async def test_bridge_failure_is_unknown(self, tmp_path) -> None:
        bridge, database, service, server_id = await seeded(tmp_path)
        service.fail = {"dig_capability", "find_blocks"}
        report = await run(bridge)
        assert report.invalidated == 0
        assert report.confirmed == 0
        facts = await bridge.store.all_facts(server_id=server_id)
        assert all(fact.fresh is not Freshness.INVALIDATED for fact in facts)
        await database.close()

    async def test_block_query_returns_air_marker_for_unknown(self, tmp_path) -> None:
        """`reason: unavailable` 是"读不到"，不是"那格空了" —— 一样不动记忆。"""
        bridge, database, service, server_id = await seeded(tmp_path)

        async def unavailable(x: int, y: int, z: int) -> dict[str, Any]:
            return {"ok": True, "result": {"ok": True, "block": None, "reason": "unavailable"}}

        service.dig_capability = unavailable  # type: ignore[method-assign]
        report = await run(bridge)
        assert report.invalidated == 0
        await database.close()


class TestNonWorldFacts:
    async def test_player_fact_is_never_world_invalidated(self, tmp_path) -> None:
        bridge, database, service, server_id = await seeded(tmp_path)
        service.set_block(ANCHOR, None)
        await run(bridge)
        player = by_subject(await bridge.store.all_facts(server_id=server_id), "player:")
        assert player.fresh is not Freshness.INVALIDATED  # 玩家不是"那一格的方块"
        await database.close()

    async def test_stale_by_age_only_via_reconcile(self, tmp_path) -> None:
        clock = Clock()
        bridge, database, _manager, _service = await build_bridge(tmp_path, clock=clock)
        server_id = bridge.server_id()
        await bridge.writer.event(server_id=server_id, subject="met", content="第一次见到空凛。")
        clock.advance(48 * 3600)
        report = await run(bridge)
        assert report.staled == 1
        fact = (await bridge.store.all_facts(server_id=server_id))[0]
        assert fact.fresh is Freshness.STALE
        await database.close()

    async def test_fresh_fact_is_not_aged(self, tmp_path) -> None:
        clock = Clock()
        bridge, database, _manager, _service = await build_bridge(tmp_path, clock=clock)
        server_id = bridge.server_id()
        await bridge.writer.event(server_id=server_id, subject="met", content="刚刚见到空凛。")
        report = await run(bridge)
        assert report.checked == 1
        assert report.staled == 0
        await database.close()


class TestDegradation:
    async def test_unreadable_memory_reports_degraded(self, tmp_path) -> None:
        bridge, database, _service, server_id = await seeded(tmp_path)
        await database.close()  # 记忆 DB 挂了
        report = await run(bridge)
        assert report.degraded is True
        assert report.checked == 0  # 读不到就不假装对过账
        await database.close()

    async def test_reconcile_without_server_is_a_noop(self, tmp_path) -> None:
        bridge, database, _service, _sid = await seeded(tmp_path)
        report = await bridge.reconciler.reconcile(server_id="")
        assert report.checked == 0
        await database.close()


class TestReadOnly:
    async def test_only_safe_reads_are_used(self, tmp_path) -> None:
        """§二十三/§五十一：记忆层只允许 SAFE 只读，连方法都不存在。"""
        bridge, database, service, _sid = await seeded(tmp_path)
        await run(bridge)
        assert {name for name, _payload in service.calls} <= {"dig_capability", "find_blocks"}
        # 记忆桥/复核器上没有任何世界修改能力（不是"没调用"，而是"没有"）
        for forbidden in ("move_to", "dig", "place", "pickup_item", "equip", "container_transfer"):
            assert not hasattr(service, forbidden)
            assert not hasattr(bridge, forbidden)
            assert not hasattr(bridge.reconciler, forbidden)
        await database.close()

    async def test_memory_never_writes_back_to_the_world(self, tmp_path) -> None:
        bridge, database, service, _sid = await seeded(tmp_path)
        before = list(service.blocks.items())
        await run(bridge)  # 世界说"不在了"也只是记下来，绝不动世界
        assert list(service.blocks.items()) == before
        await database.close()


def test_store_is_the_domain_store(tmp_path) -> None:
    """域存储就是现有引擎的适配层（没有第二套存储引擎）。"""
    store = MinecraftMemoryStore(_FakeManager(), character_key="空凛")
    assert store.scope_key == "character:空凛:minecraft"


class _FakeManager:
    """只需要一个小小的替身来验证 scope 计算，不碰数据库。"""


class TestBridgePayload:
    async def test_payload_is_json_shaped_for_webui(self, tmp_path) -> None:
        """``bridge.reconcile()`` 给的是 JSON 投影（Bot 周期任务与 WebUI 都读它）。"""
        bridge, database, _service, _server_id = await seeded(tmp_path)
        payload = await bridge.reconcile()
        assert set(payload) == {
            "checked",
            "confirmed",
            "invalidated",
            "staled",
            "skipped",
            "degraded",
        }
        assert bridge.last_reconcile == payload
        await database.close()

    async def test_no_server_means_nothing_to_reconcile(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        service = bridge.service
        service._host = ""  # 还没连过服务器 → 没有 server_id
        assert bridge.server_id() == ""
        assert await bridge.reconcile() == {}
        await database.close()
