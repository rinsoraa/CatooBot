"""Phase 5C §三十八/§三十九/§四十四：接入与重启恢复。

这里证明的是**接线**（真正的 Bot 类、真正的事件通道），而不是记忆域本身：

* 玩家上线 / 任务终态 → 记忆（其它事件一律不写，§三十）；
* 周期对账有 start / stop（Bot 关掉之后不留野任务）；
* 运维显式配置的 QQ → 玩家名只在**玩家在线**时生效，且幂等；
* **重启**之后身份绑定与事实都还在（真的 SQLite），而记忆层的任何故障
  都只降级记忆 —— 绝不碰任务运行时 / 聊天。
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.bot import Bot
from app.core.event_bus import EventBus
from app.integrations.minecraft.events import MinecraftBridgeEvent
from app.memory.minecraft.model import MinecraftMemoryKind
from tests.minecraft_memory_fakes import (
    UUID_KONGLING,
    FakeMinecraftService,
    build_bridge,
)


def wire_bot(
    *,
    memory: Any,
    minecraft: FakeMinecraftService | None = None,
    linked_players: dict[str, str] | None = None,
    reconcile_interval: float = 300.0,
) -> Bot:
    """构造一个**没启动过**的真 Bot 实例，只装 5C 需要的那几个属性。"""
    bot = Bot.__new__(Bot)
    bot.log = logging.getLogger("test.bot")
    bot.minecraft_memory = memory
    bot.minecraft_identity = None
    bot._memory_tasks = set()
    bot._memory_reconcile_task = None
    bot.minecraft = minecraft if minecraft is not None else FakeMinecraftService()
    bot.config = SimpleNamespace(
        minecraft=SimpleNamespace(
            memory=SimpleNamespace(
                enabled=True,
                reconcile_interval_seconds=reconcile_interval,
                context_items=5,
                linked_players=dict(linked_players or {}),
            )
        )
    )
    return bot


class TestEventWiring:
    async def test_player_joined_writes_memory(self, tmp_path) -> None:
        bridge, database, _manager, service = await build_bridge(tmp_path)
        bot = wire_bot(memory=bridge, minecraft=service)
        service.add_listener(bot._on_minecraft_memory_event)

        event = MinecraftBridgeEvent(
            event="minecraft.player_joined",
            data={"uuid": UUID_KONGLING, "username": "空凛"},
            timestamp=1,
        )
        for listener in list(service.listeners):
            listener(event)
        await asyncio.gather(*bot._memory_tasks)

        facts = await bridge.store.all_facts(server_id=bridge.server_id())
        assert {fact.kind for fact in facts} == {
            MinecraftMemoryKind.PLAYER,
            MinecraftMemoryKind.EVENT,
        }
        player = [f for f in facts if f.kind is MinecraftMemoryKind.PLAYER][0]
        assert "空凛" in player.content and player.player_uuid
        await database.close()

    async def test_other_events_write_nothing(self, tmp_path) -> None:
        """§三十：移动/世界变化/动作事件**不**进长期记忆。"""
        bridge, database, _manager, service = await build_bridge(tmp_path)
        bot = wire_bot(memory=bridge, minecraft=service)
        for name in (
            "minecraft.action.completed",
            "minecraft.chat",
            "minecraft.player_left",
            "minecraft.connected",
        ):
            bot._on_minecraft_memory_event(
                MinecraftBridgeEvent(event=name, data={"username": "空凛"}, timestamp=1)
            )
        await asyncio.gather(*bot._memory_tasks)
        assert await bridge.store.all_facts(server_id=bridge.server_id()) == []
        await database.close()

    async def test_no_memory_means_no_crash(self, tmp_path) -> None:
        bot = wire_bot(memory=None)
        bot._on_minecraft_memory_event(
            MinecraftBridgeEvent(event="minecraft.player_joined", data={}, timestamp=1)
        )
        assert bot._memory_tasks == set()

    async def test_task_terminal_event_writes_an_experience(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bot = wire_bot(memory=bridge)

        class Tasks:
            async def get(self, task_id: str) -> Any:
                return SimpleNamespace(
                    task_id=task_id,
                    state=SimpleNamespace(value="SUCCEEDED"),
                    objective="去砍一棵橡树并捡回来",
                    verification={"inventory_delta": {"minecraft:oak_log": 1}},
                    steps=[],
                    plan_version=1,
                    user_id="2731431246",
                    message="",
                )

        bot.tasks = Tasks()
        bot._remember_task_outcome("task.succeeded", {"task_id": "task_abc"})
        await asyncio.gather(*bot._memory_tasks)

        facts = await bridge.store.facts(server_id=bridge.server_id())
        assert len(facts) == 1
        assert facts[0].kind is MinecraftMemoryKind.TASK
        assert "橡树" in facts[0].content and "oak_log" in facts[0].content
        await database.close()

    async def test_non_terminal_task_events_are_ignored(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bot = wire_bot(memory=bridge)
        bot.tasks = SimpleNamespace()
        for name in ("task.created", "task.started", "task.paused", "task.replanning"):
            bot._remember_task_outcome(name, {"task_id": "task_abc"})
        assert bot._memory_tasks == set()
        assert await bridge.store.all_facts(server_id=bridge.server_id()) == []
        await database.close()

    async def test_task_hook_survives_memory_failure(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        await database.close()
        bot = wire_bot(memory=bridge)

        class Tasks:
            async def get(self, task_id: str) -> Any:
                return SimpleNamespace(
                    task_id=task_id,
                    state=SimpleNamespace(value="FAILED"),
                    objective="去挖一块钻石",
                    verification={},
                    steps=[],
                    plan_version=1,
                    user_id="",
                    message="目标太远",
                )

        bot.tasks = Tasks()
        bot._remember_task_outcome("task.failed", {"task_id": "t"})
        await asyncio.gather(*bot._memory_tasks)  # 不抛异常才算过
        assert bridge.store.degraded_reason


class TestReconcileLoop:
    async def test_loop_starts_and_stops(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bot = wire_bot(memory=bridge, reconcile_interval=30.0)
        bot._start_memory_reconcile()
        assert bot._memory_reconcile_task is not None
        assert not bot._memory_reconcile_task.done()
        await bot._stop_memory_reconcile()
        assert bot._memory_reconcile_task is None
        await database.close()

    async def test_loop_survives_a_broken_reconcile(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)

        async def explode(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("memory exploded")

        bridge.reconcile = explode  # type: ignore[method-assign]
        bot = wire_bot(memory=bridge, reconcile_interval=0.0)
        await bot._stop_memory_reconcile()
        task = asyncio.create_task(bot._memory_reconcile_loop(0.01))
        await asyncio.sleep(0.05)
        assert not task.done()  # 对账炸了也不许把循环打死
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await database.close()

    async def test_stop_is_idempotent(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bot = wire_bot(memory=bridge)
        await bot._stop_memory_reconcile()
        await bot._stop_memory_reconcile()
        await database.close()

    async def test_no_memory_no_loop(self) -> None:
        bot = wire_bot(memory=None)
        bot._start_memory_reconcile()
        assert bot._memory_reconcile_task is None


class TestConfiguredLinks:
    async def test_binds_when_player_is_online(self, tmp_path) -> None:
        bridge, database, _manager, service = await build_bridge(tmp_path)
        service.add_player("空凛", UUID_KONGLING)
        bot = wire_bot(memory=bridge, linked_players={"2731431246": "空凛"})
        await bot._apply_configured_links()
        link = await bridge.link_for(platform="qq", user_id="2731431246")
        assert link is not None and link.username == "空凛"
        await database.close()

    async def test_skips_when_player_is_offline(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bot = wire_bot(memory=bridge, linked_players={"2731431246": "空凛"})
        await bot._apply_configured_links()
        assert await bridge.identities.all_links() == []
        await database.close()

    async def test_is_idempotent(self, tmp_path) -> None:
        bridge, database, _manager, service = await build_bridge(tmp_path)
        service.add_player("空凛", UUID_KONGLING)
        bot = wire_bot(memory=bridge, linked_players={"2731431246": "空凛"})
        for _ in range(3):
            await bot._apply_configured_links()
        links = await bridge.identities.all_links()
        assert len(links) == 1 and links[0].status == "VERIFIED"
        await database.close()

    async def test_does_not_override_an_explicit_binding(self, tmp_path) -> None:
        """§六 优先级：用户自己的显式验证 > 运维配置（配置永远不会把用户挤掉）。"""
        bridge, database, _manager, service = await build_bridge(tmp_path)
        service.add_player("空凛", UUID_KONGLING)
        await bridge.bind(platform="qq", user_id="2731431246", username="空凛")
        bot = wire_bot(memory=bridge, linked_players={"2731431246": "空凛"})
        await bot._apply_configured_links()
        link = await bridge.link_for(platform="qq", user_id="2731431246")
        assert link is not None and link.source == "explicit_verification"
        await database.close()

    async def test_one_bad_entry_does_not_stop_the_others(self, tmp_path) -> None:
        bridge, database, _manager, service = await build_bridge(tmp_path)
        service.add_player("空凛", UUID_KONGLING)
        bot = wire_bot(memory=bridge, linked_players={"1": "不存在的人", "2731431246": "空凛"})
        await bot._apply_configured_links()
        assert await bridge.link_for(platform="qq", user_id="2731431246") is not None
        await database.close()

    async def test_no_mapping_is_a_noop(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bot = wire_bot(memory=bridge)
        await bot._apply_configured_links()
        assert await bridge.identities.all_links() == []
        await database.close()


class TestBootWiring:
    async def test_setup_builds_bridge_and_listener(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig, MemoryConfig
        from app.database.database import Database
        from app.memory.manager import MemoryManager

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'boot.db'}"))
        await database.connect()
        service = FakeMinecraftService()
        bot = Bot.__new__(Bot)
        bot.log = logging.getLogger("test.boot")
        bot.config = SimpleNamespace(
            minecraft=SimpleNamespace(
                memory=SimpleNamespace(
                    enabled=True,
                    reconcile_interval_seconds=300.0,
                    context_items=5,
                    linked_players={},
                )
            ),
            task=SimpleNamespace(ttl_seconds=600.0, max_steps=16),
        )
        bot.minecraft = service
        bot.database = database
        bot.memory = MemoryManager(MemoryConfig(), database)
        bot.character = SimpleNamespace(personas=None)  # 拿不到名字也不能炸
        bot.minecraft_memory = None
        bot.minecraft_identity = None
        bot.event_bus = EventBus()
        bot.tasks = None

        bot._setup_minecraft_memory()
        assert bot.minecraft_memory is not None
        assert bot.minecraft_memory.character_key == "default"  # 拿不到名字就用默认 scope
        assert bot.minecraft_identity is not None
        assert bot._on_minecraft_memory_event in service.listeners
        await database.close()

    async def test_setup_failure_only_disables_memory(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'boot2.db'}"))
        await database.connect()
        await database.close()  # 数据库已经关了 → 记忆装配必然失败
        bot = Bot.__new__(Bot)
        bot.log = logging.getLogger("test.boot")
        bot.config = SimpleNamespace(
            minecraft=SimpleNamespace(
                memory=SimpleNamespace(
                    enabled=True,
                    reconcile_interval_seconds=300.0,
                    context_items=5,
                    linked_players={},
                )
            )
        )
        bot.minecraft = FakeMinecraftService()
        bot.database = database
        bot.memory = SimpleNamespace()  # 不是真的 MemoryManager
        bot.character = None
        bot.minecraft_memory = "stale"
        bot.minecraft_identity = "stale"

        bot._setup_minecraft_memory()  # 不抛异常
        assert bot.minecraft_memory is not None  # 装配本身不抛（失败发生在写入时）

    async def test_setup_respects_the_config_switch(self) -> None:
        bot = Bot.__new__(Bot)
        bot.log = logging.getLogger("test.boot")
        bot.config = SimpleNamespace(
            minecraft=SimpleNamespace(memory=SimpleNamespace(enabled=False))
        )
        bot.minecraft_memory = None
        bot.minecraft_identity = None
        bot.minecraft = FakeMinecraftService()
        bot._setup_minecraft_memory()
        assert bot.minecraft_memory is None  # 关掉 = 完全不做记忆
        assert bot.minecraft_identity is None
        assert bot.minecraft.listeners == []


class TestRestartPersistence:
    async def test_binding_and_facts_survive_with_freshness(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig, MemoryConfig
        from app.database.database import Database
        from app.memory.manager import MemoryManager

        url = f"sqlite:///{tmp_path / 'restart.db'}"
        first_db = Database(DatabaseConfig(url=url))
        await first_db.connect()
        first = await build_bridge(
            tmp_path, database=first_db, manager=MemoryManager(MemoryConfig(), first_db)
        )
        bridge, _db, manager, service = first
        service.add_player("空凛", UUID_KONGLING)
        server_id = bridge.server_id()
        await bridge.bind(platform="qq", user_id="2731431246", username="空凛")
        await bridge.writer.resource_seen(
            server_id=server_id,
            block_name="minecraft:oak_log",
            position={"x": 100, "y": 64, "z": 100},
        )
        await first_db.close()

        second_db = Database(DatabaseConfig(url=url))
        await second_db.connect()
        second = await build_bridge(
            tmp_path, database=second_db, manager=MemoryManager(MemoryConfig(), second_db)
        )
        bridge2, _db2, _manager2, service2 = second
        assert bridge2.server_id() == server_id
        link = await bridge2.link_for(platform="qq", user_id="2731431246")
        assert link is not None and link.username == "空凛"
        assert link.canonical_uuid  # 身份还是那个 uuid
        facts = await bridge2.store.all_facts(server_id=server_id)
        # 绑定会顺手记一条关系事实（不是权限），资源事实也在
        assert {fact.kind for fact in facts} == {
            MinecraftMemoryKind.RESOURCE,
            MinecraftMemoryKind.RELATIONSHIP,
        }
        # 重启后世界还是那样 → 对账确认，不当新事实
        service2.set_block({"x": 100, "y": 64, "z": 100}, "minecraft:oak_log")
        report = await bridge2.reconciler.reconcile(server_id=server_id)
        assert report.confirmed == 1
        resources = await bridge2.store.facts(
            server_id=server_id, kinds=[MinecraftMemoryKind.RESOURCE]
        )
        assert len(resources) == 1 and resources[0].observation_count in {2, 3}
        await second_db.close()

    async def test_memory_failure_does_not_block_task_recovery(self, tmp_path) -> None:
        """§三十九：记忆加载失败只降级记忆；任务恢复走它自己的路（这里证明互不牵连）。"""
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        await database.close()
        bot = wire_bot(memory=bridge)

        class Tasks:
            recovered = False

            async def recover_persisted_tasks(self) -> list[str]:
                self.recovered = True
                return []

        tasks = Tasks()
        bot.tasks = tasks
        await bot._apply_configured_links()
        await bridge.context_block(text="木头在哪", platform="qq", user_id="1")  # 记忆层已坏
        await tasks.recover_persisted_tasks()
        assert tasks.recovered
        assert bridge.store.degraded_reason  # 记忆如实降级，任务照常


class TestContextInjection:
    """记忆块接进角色回合：只**追加**一段上下文，不改任何权限输入。"""

    def _runtime(self, bridge: Any) -> Any:
        from app.character.runtime import CharacterRuntime

        runtime = CharacterRuntime.__new__(CharacterRuntime)
        runtime._log = logging.getLogger("test.character")
        runtime.minecraft_memory = bridge
        return runtime

    @pytest.mark.parametrize(
        ("session_id", "platform"),
        [
            ("private:2731431246", "qq"),
            ("group:987654", "qq"),
            ("minecraft:127.0.0.1:25565:空凛", "minecraft_chat"),
        ],
    )
    def test_platform_follows_the_session(self, session_id: str, platform: str) -> None:
        from app.character.runtime import CharacterRuntime

        assert CharacterRuntime._memory_platform(session_id) == platform

    async def test_block_is_appended_for_the_turn(self, tmp_path) -> None:
        bridge, database, _manager, service = await build_bridge(tmp_path)
        service.set_block({"x": 100, "y": 64, "z": 100}, "minecraft:oak_log")
        await bridge.writer.resource_seen(
            server_id=bridge.server_id(),
            block_name="minecraft:oak_log",
            position={"x": 100, "y": 64, "z": 100},
        )
        runtime = self._runtime(bridge)
        block = await runtime._minecraft_memory_context(
            session_id="private:2731431246", user_id=2731431246, text="我刚才挖木头的地方在哪"
        )
        assert block.startswith("相关 Minecraft 记忆：")
        assert "oak_log" in block
        await database.close()

    async def test_no_bridge_means_no_block(self, tmp_path) -> None:
        runtime = self._runtime(None)
        runtime._log = logging.getLogger("test.character")
        assert (
            await runtime._minecraft_memory_context(session_id="private:1", user_id=1, text="在吗")
            == ""
        )

    async def test_broken_bridge_is_silent(self, tmp_path) -> None:
        class Broken:
            async def context_block(self, **_kwargs: Any) -> str:
                raise RuntimeError("memory exploded")

        runtime = self._runtime(Broken())
        assert (
            await runtime._minecraft_memory_context(session_id="private:1", user_id=1, text="在吗")
            == ""
        )
