"""核心好友（core friend）：独立的关系阶段与独立的主动聊天规则。

契约：
* :func:`app.config.settings.explicit_core_friends` 是"谁是核心好友"的唯一权威
  —— 沙盒 persons 映射、关系表阶段、主动聊天门禁读的是同一份显式配置；
* 配置里的核心好友在关系表里固定 ``core`` 档（最高档，不随互动次数退化），
  启动时把既有行纠正过来，WebUI 读表看到的也是 ``core``；
* 「主动聊天（核心好友）」是一套**独立**规则：自己的开关、间隔、日/时额度、
  闲置窗与概率。普通主动聊天关着时这一路仍可生效；普通的最低关系门槛与它无关；
* 额度按人（scope）独立计数，一个核心好友的主动不会占用别人的名额。
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.behavior.initiative import InitiativeCandidate, InitiativeEngine
from app.behavior.presence import PresenceResolver
from app.character.relationship import CORE_STAGE, STAGES, RelationshipManager
from app.config.settings import (
    BehaviorInitiativeConfig,
    BehaviorInitiativeCoreConfig,
    BehaviorScheduleConfig,
    explicit_core_friends,
)
from app.database.database import Database
from tests.conftest import make_bot

TZ = ZoneInfo("Asia/Singapore")
DAY = datetime(2026, 10, 5, 15, 0, tzinfo=TZ)
QQ_CORE = "2731431246"
QQ_OTHER = "1132577137"


class FrozenPresence(PresenceResolver):
    """Presence frozen at a calm daytime moment (no sleep/DND windows)."""

    def __init__(self, moment: datetime = DAY) -> None:
        super().__init__("Asia/Singapore", BehaviorScheduleConfig())
        self._moment = moment

    def now(self) -> datetime:  # type: ignore[override]
        return self._moment


class Clock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def candidate(qq: str) -> InitiativeCandidate:
    return InitiativeCandidate(
        scope_key=f"private:{qq}", user_id=qq, reason="life_event", topic="今天出门买了东西"
    )


def engine(*, config: BehaviorInitiativeConfig, db: Database, clock: Clock) -> InitiativeEngine:
    return InitiativeEngine(
        config,
        FrozenPresence(),
        database=db,
        clock=clock,
        rng=random.Random(7),
    )


async def make_db(tmp_path) -> Database:  # type: ignore[no-untyped-def]
    from app.config.settings import DatabaseConfig

    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'core.db'}"))
    await db.connect()
    return db


class TestExplicitCoreFriends:
    def test_identity_mapping_forms(self) -> None:
        from app.config.settings import SandboxConfig

        # explicit QQ → name mapping wins
        cfg = SandboxConfig(core_friend_identities={QQ_CORE: "空凛"})
        assert explicit_core_friends(cfg) == {QQ_CORE: "空凛"}

        # dict form of core_friend_ids
        cfg = SandboxConfig(core_friend_ids={QQ_CORE: "空凛"})
        assert explicit_core_friends(cfg) == {QQ_CORE: "空凛"}

        # legacy single-id list: only unambiguous with exactly one bible name
        cfg = SandboxConfig(core_friend_ids=[QQ_CORE])
        assert explicit_core_friends(cfg, core_names=["空凛"]) == {QQ_CORE: "空凛"}
        assert explicit_core_friends(cfg, core_names=["空凛", "阿澈"]) == {}
        assert explicit_core_friends(SandboxConfig(), core_names=["空凛"]) == {}

    def test_explicit_mapping_beats_the_legacy_list(self) -> None:
        from app.config.settings import SandboxConfig

        cfg = SandboxConfig(core_friend_ids=[QQ_OTHER], core_friend_identities={QQ_CORE: "空凛"})
        mapping = explicit_core_friends(cfg, core_names=["空凛", "阿澈"])
        assert mapping[QQ_CORE] == "空凛"


class TestCoreFriendStage:
    async def test_core_stage_sits_at_the_top(self) -> None:
        assert STAGES[-1] == CORE_STAGE

    async def test_configured_core_friend_reads_as_core(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        try:
            await db.execute(
                """INSERT INTO relationships
                       (user_id, stage, preferred_tone, notes, interaction_count,
                        first_seen, last_seen, updated_at)
                   VALUES (?, 'familiar', '', '', 27, 0, 0, 0)""",
                (QQ_CORE,),
            )
            manager = RelationshipManager(db, core_friend_ids={QQ_CORE})
            rel = await manager.get(QQ_CORE)
            assert rel.stage == CORE_STAGE and rel.interaction_count == 27
            listed = {r.user_id: r.stage for r in await manager.all()}
            assert listed[QQ_CORE] == CORE_STAGE
        finally:
            await db.close()

    async def test_sync_corrects_the_table_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        try:
            await db.execute(
                """INSERT INTO relationships
                       (user_id, stage, preferred_tone, notes, interaction_count,
                        first_seen, last_seen, updated_at)
                   VALUES (?, 'familiar', '', '', 27, 0, 0, 0)""",
                (QQ_CORE,),
            )
            manager = RelationshipManager(db, core_friend_ids={QQ_CORE})
            assert await manager.sync_core_stages() == 1
            row = await db.fetchone("SELECT stage FROM relationships WHERE user_id = ?", (QQ_CORE,))
            assert row["stage"] == CORE_STAGE
            assert await manager.sync_core_stages() == 0  # idempotent
        finally:
            await db.close()

    async def test_interactions_never_downgrade_a_core_friend(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        try:
            manager = RelationshipManager(db, core_friend_ids={QQ_CORE})
            rel = await manager.record_interaction(QQ_CORE)
            assert rel.stage == CORE_STAGE  # not "familiar" despite count 1
            rel = await manager.record_interaction(QQ_CORE)
            assert rel.stage == CORE_STAGE and rel.interaction_count == 2

            # non-core users still advance by count
            other = await manager.record_interaction(QQ_OTHER)
            assert other.stage == "new"
            for _ in range(5):
                other = await manager.record_interaction(QQ_OTHER)
            assert other.stage == "familiar"
        finally:
            await db.close()


class TestCoreFriendGate:
    async def test_the_core_path_has_its_own_switch(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        try:
            config = BehaviorInitiativeConfig.model_validate(
                {
                    "enabled": False,  # general switch off
                    "base_probability": 1.0,
                    "core_friend": {"enabled": True, "base_probability": 1.0},
                }
            )
            gate = engine(config=config, db=db, clock=clock)
            blocked = await gate.evaluate(candidate(QQ_OTHER), relationship_stage="familiar")
            assert blocked.allowed is False and blocked.reason == "disabled"

            core = await gate.evaluate(
                candidate(QQ_CORE), relationship_stage=CORE_STAGE, is_core=True
            )
            assert core.allowed is True, core.reason
        finally:
            await db.close()

    async def test_core_limits_are_independent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        try:
            config = BehaviorInitiativeConfig.model_validate(
                {
                    "enabled": True,
                    "base_probability": 1.0,
                    "min_interval_minutes": 120,
                    "daily_limit": 1,
                    "hourly_limit": 0,
                    "core_friend": {
                        "enabled": True,
                        "base_probability": 1.0,
                        "min_interval_minutes": 1,
                        "daily_limit": 5,
                        "hourly_limit": 0,
                    },
                }
            )
            gate = engine(config=config, db=db, clock=clock)

            # a send one minute ago: too soon for the normal rules, fine for core
            state = await gate.load_state(f"private:{QQ_CORE}")
            state.last_sent_at = int(clock()) - 60
            state.daily_count = 2
            await gate.save_state(state)
            normal = await gate.evaluate(candidate(QQ_CORE), relationship_stage="familiar")
            assert normal.reason == "cooldown"
            core = await gate.evaluate(
                candidate(QQ_CORE), relationship_stage=CORE_STAGE, is_core=True
            )
            assert core.allowed is True, core.reason

            # the daily budget of the normal path does not apply to core either
            state = await gate.load_state(f"private:{QQ_OTHER}")
            state.daily_count = 1
            state.daily_date = time.strftime("%Y-%m-%d", time.localtime(clock()))
            await gate.save_state(state)
            normal = await gate.evaluate(candidate(QQ_OTHER), relationship_stage="familiar")
            assert normal.reason == "daily_limit"
        finally:
            await db.close()

    async def test_core_friends_skip_the_stage_gate(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """门槛是普通规则的一部分；核心好友按身份就是最高档。"""
        db = await make_db(tmp_path)
        clock = Clock()
        try:
            config = BehaviorInitiativeConfig.model_validate(
                {
                    "enabled": True,
                    "min_relationship_stage": "very_close",
                    "base_probability": 1.0,
                    "core_friend": {"enabled": True, "base_probability": 1.0},
                }
            )
            gate = engine(config=config, db=db, clock=clock)
            normal = await gate.evaluate(candidate(QQ_OTHER), relationship_stage="familiar")
            assert normal.reason == "relationship_too_new"
            core = await gate.evaluate(
                candidate(QQ_CORE), relationship_stage=CORE_STAGE, is_core=True
            )
            assert core.allowed is True, core.reason
        finally:
            await db.close()

    async def test_the_idle_window_comes_from_the_core_config(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        try:
            config = BehaviorInitiativeConfig.model_validate(
                {"idle_hours": 6.0, "core_friend": {"idle_hours": 1.0}}
            )
            gate = engine(config=config, db=db, clock=clock)
            last_seen = int(clock()) - 2 * 3600  # two hours of silence

            normal = await gate.build_candidates(
                scope_key=f"private:{QQ_OTHER}",
                user_id=QQ_OTHER,
                last_seen=last_seen,
                relationship_stage="familiar",
            )
            assert [c.reason for c in normal] == []

            core = await gate.build_candidates(
                scope_key=f"private:{QQ_CORE}",
                user_id=QQ_CORE,
                last_seen=last_seen,
                relationship_stage=CORE_STAGE,
                is_core=True,
            )
            assert [c.reason for c in core] == ["long_absence"]
        finally:
            await db.close()


class TestCoreFriendWiring:
    async def test_bot_knows_the_configured_core_friend(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot = make_bot(
            tmp_path,
            sandbox={"enabled": False, "core_friend_identities": {QQ_CORE: "空凛"}},
        )
        await bot.database.connect()
        try:
            assert bot.is_core_friend(QQ_CORE) is True
            assert bot.is_core_friend(int(QQ_CORE)) is True
            assert bot.is_core_friend(QQ_OTHER) is False
            assert bot.character.relationships.is_core(QQ_CORE) is True
        finally:
            await bot.shutdown()

    async def test_startup_sync_brings_the_table_in_line(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot = make_bot(
            tmp_path,
            sandbox={"enabled": False, "core_friend_identities": {QQ_CORE: "空凛"}},
        )
        await bot.database.connect()
        try:
            await bot.database.execute(
                """INSERT INTO relationships
                       (user_id, stage, preferred_tone, notes, interaction_count,
                        first_seen, last_seen, updated_at)
                   VALUES (?, 'familiar', '', '', 27, 0, 0, 0)""",
                (QQ_CORE,),
            )
            changed = await bot.character.relationships.sync_core_stages()
            assert changed == 1
            row = await bot.database.fetchone(
                "SELECT stage FROM relationships WHERE user_id = ?", (QQ_CORE,)
            )
            assert row["stage"] == CORE_STAGE
        finally:
            await bot.shutdown()

    async def test_the_scheduler_flags_core_users(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from app.behavior.scheduler import BehaviorScheduler

        bot = make_bot(
            tmp_path,
            sandbox={"enabled": False, "core_friend_identities": {QQ_CORE: "空凛"}},
        )
        await bot.database.connect()
        try:
            scheduler = BehaviorScheduler(bot, bot.behavior, tick_seconds=1)
            assert scheduler._is_core_friend(QQ_CORE) is True  # noqa: SLF001
            assert scheduler._is_core_friend(QQ_OTHER) is False  # noqa: SLF001
        finally:
            await bot.shutdown()

    async def test_relationship_line_uses_a_human_label(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from app.character.relationship import stage_label

        assert stage_label(CORE_STAGE) == "核心好友"
        assert stage_label("familiar") == "熟悉"
        assert stage_label("unknown") == "unknown"


class TestCoreFriendSettings:
    async def test_save_writes_and_hot_applies_the_core_block(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from app.web.services.behavior import BehaviorService

        bot = make_bot(tmp_path)
        await bot.database.connect()
        try:
            service = BehaviorService(bot)
            await service.save_settings(
                {
                    "initiative_enabled": "1",
                    "min_interval_minutes": "120",
                    "daily_limit": "3",
                    "hourly_limit": "1",
                    "idle_hours": "6",
                    "min_relationship_stage": "familiar",
                    "max_unanswered": "1",
                    "core_initiative_enabled": "1",
                    "core_min_interval_minutes": "45",
                    "core_daily_limit": "8",
                    "core_hourly_limit": "3",
                    "core_idle_hours": "2",
                    "core_max_unanswered": "4",
                    "core_base_probability": "0.7",
                }
            )
            core = bot.behavior.initiative.config.core_friend
            assert core.enabled is True
            assert core.min_interval_minutes == 45
            assert core.daily_limit == 8 and core.hourly_limit == 3
            assert core.idle_hours == 2.0 and core.max_unanswered == 4
            assert abs(core.base_probability - 0.7) < 1e-9

            overrides = await service.load_overrides()
            assert overrides["initiative"]["core_friend"]["daily_limit"] == 8

            # a save that does not carry the core fields keeps them (merge,
            # never replace — the v1 settings page writes the same map)
            await service.save_settings({"initiative_enabled": "1"})
            assert bot.behavior.initiative.config.core_friend.daily_limit == 8
        finally:
            await bot.shutdown()

    async def test_the_core_card_renders_on_the_chat_page(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        import socket

        import aiohttp

        from app.config.settings import AppConfig
        from app.core.bot import Bot
        from app.web.server import WebServer
        from tests.conftest import FakeAdapter

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = AppConfig(
            database={"url": f"sqlite:///{tmp_path / 'page.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
            web={
                "enabled": True,
                "host": "127.0.0.1",
                "port": port,
                "username": "admin",
                "password": "pw123",
            },
        )
        bot = Bot(config, FakeAdapter())
        await bot.database.connect()
        server = WebServer(bot.config.web, bot)
        await server.start()
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await session.post(
                    f"http://127.0.0.1:{port}/login",
                    data={"username": "admin", "password": "pw123"},
                )
                resp = await session.get(f"http://127.0.0.1:{port}/sandbox/chat")
                body = await resp.text()
        finally:
            await server.stop()
            await bot.shutdown()

        assert "主动聊天（核心好友）" in body
        for name in (
            "core_initiative_enabled",
            "core_min_interval_minutes",
            "core_daily_limit",
            "core_hourly_limit",
            "core_idle_hours",
            "core_max_unanswered",
            "core_base_probability",
        ):
            assert f"name='{name}'" in body, name
        assert "value='core'" in body  # the stage select offers the core stage

    async def test_defaults_are_sane(self) -> None:
        cfg = BehaviorInitiativeCoreConfig()
        assert cfg.enabled is True  # core friends are opted in by being configured
        assert cfg.daily_limit >= 1 and cfg.hourly_limit >= 1
        assert 0.0 < cfg.base_probability <= 1.0
        assert cfg.min_interval_minutes >= 1
