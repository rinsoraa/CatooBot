"""Phase 7E 接线回归：包装规划器 / 资源任务入口 / Bot 装配与终态钩子 / 迁移 / 只读 API。

这一组证明"技能只产出**计划候选**、且既有链路行为在技能缺席或故障时逐字不变"。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.config.settings import AppConfig, DatabaseConfig
from app.core.bot import Bot
from app.database.database import Database
from app.tasks.agent_planner import BoundedAgentPlanner, PlanningOutcome
from app.tasks.models import TaskState
from app.tasks.skill_planner import SKILL_REUSE_REASON, SkillAwarePlanner
from app.tasks.turn import TaskTurnHandler
from tests.skill_fakes import (
    FakeObserve,
    build_task_runtime,
    make_service,
    resource_record,
)
from tests.test_web_realtime import DummyAdapter


async def promoted_service(**kwargs: Any) -> Any:
    kwargs.setdefault("observe", FakeObserve())
    service = make_service(**kwargs)
    await service.on_task_finished(resource_record(task_id="t1"))
    await service.on_task_finished(resource_record(task_id="t2"))
    return service


# ---------------------------------------------------------------- 包装规划器


class TestSkillAwarePlanner:
    async def test_resource_objective_uses_the_skill_plan(self) -> None:
        service = await promoted_service()
        # allow_medium=True 与生产配置一致（config.tools.allow_medium: true）
        planner = SkillAwarePlanner(BoundedAgentPlanner(allow_medium=True), skills=service)
        result = await planner.plan("去挖一块橡木并捡回来", observe=FakeObserve())
        assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL
        assert result.reason == SKILL_REUSE_REASON
        assert result.plan is not None
        assert result.checks and result.checks[0]["check"] == "skill_reuse"
        assert [step.tool for step in result.plan.plan.steps][0] == "minecraft_equip"

    async def test_follow_objective_never_uses_skills(self) -> None:
        service = await promoted_service()
        planner = SkillAwarePlanner(BoundedAgentPlanner(allow_medium=True), skills=service)
        before = service.retrieval_count
        result = await planner.plan("跟着我", observe=FakeObserve())
        assert service.retrieval_count == before, "跟随目标不该去问技能"
        assert result.outcome is not PlanningOutcome.READY_FOR_APPROVAL or (
            result.reason != SKILL_REUSE_REASON
        )

    async def test_unknown_objective_falls_back_to_base_planner(self) -> None:
        service = await promoted_service()
        planner = SkillAwarePlanner(BoundedAgentPlanner(allow_medium=True), skills=service)
        result = await planner.plan("建造一台刷铁机", observe=FakeObserve())
        assert result.reason != SKILL_REUSE_REASON

    async def test_without_skills_behaviour_is_unchanged(self) -> None:
        base = BoundedAgentPlanner(allow_medium=True)
        planner = SkillAwarePlanner(base, skills=None)
        # 背包里有斧头 → 模板同样会插一步 equip（与技能模板同形，便于比对）
        observe = FakeObserve(inventory=[{"name": "netherite_axe", "count": 1}])
        result = await planner.plan("去挖一块橡木并捡回来", observe=observe)
        assert result.reason == "resource_plan_ready"
        assert [step.tool for step in result.plan.plan.steps][0] == "minecraft_equip"

    async def test_broken_skill_layer_falls_back(self) -> None:
        class Broken:
            async def suggest(self, objective: str) -> Any:
                raise RuntimeError("skills down")

        planner = SkillAwarePlanner(BoundedAgentPlanner(allow_medium=True), skills=Broken())
        result = await planner.plan("去挖一块橡木并捡回来", observe=FakeObserve())
        assert result.reason == "resource_plan_ready", "异常必须原样回退"

    async def test_medium_skill_is_blocked_when_medium_is_disabled(self) -> None:
        """``allow_medium=false`` 时技能**不能**绕开基规划器的风险闸门。"""

        service = await promoted_service()
        strict = SkillAwarePlanner(BoundedAgentPlanner(allow_medium=False), skills=service)
        result = await strict.plan("去挖一块橡木并捡回来", observe=FakeObserve())
        assert result.reason != SKILL_REUSE_REASON
        assert result.outcome.value == "BLOCKED_BY_POLICY"


# ---------------------------------------------------------------- 资源任务入口（turn）


class TestTurnSeam:
    async def test_skill_plan_is_used_and_carries_the_reference(self) -> None:
        service = await promoted_service()
        runtime = build_task_runtime()
        handler = TaskTurnHandler(runtime, observe=FakeObserve(), skills=service)
        outcome = await handler.handle(
            session_id="s", user_id="u", text="去挖一块橡木并捡回来", origin="user"
        )
        assert outcome.handled and outcome.action == "created"
        record = await runtime.get(outcome.task_id)
        assert record is not None
        assert record.state is TaskState.PENDING_CONFIRMATION
        # 计划 observations 不随任务持久化（5A 既有语义）→ 引用只在进程内可读；
        # **持久**的关联是技能使用链（§7.6 的回流据此反查，重启也不丢）。
        usage = await service.store.usage_for(
            objective=record.objective, plan_hash=record.plan_hash
        )
        assert usage, "必须留下持久的使用链"

    async def test_without_a_skill_it_uses_the_existing_template(self) -> None:
        runtime = build_task_runtime()
        handler = TaskTurnHandler(runtime, observe=FakeObserve(), skills=None)
        outcome = await handler.handle(
            session_id="s", user_id="u", text="去挖一块橡木并捡回来", origin="user"
        )
        assert outcome.action == "created"
        record = await runtime.get(outcome.task_id)
        assert record is not None
        assert not any(
            str(item.get("tool")) == "skill_reference" for item in (record.plan.observations or [])
        )

    async def test_broken_skill_layer_does_not_break_task_creation(self) -> None:
        class Broken:
            async def suggest(self, objective: str) -> Any:
                raise RuntimeError("skills down")

        runtime = build_task_runtime()
        handler = TaskTurnHandler(runtime, observe=FakeObserve(), skills=Broken())
        outcome = await handler.handle(
            session_id="s", user_id="u", text="去挖一块橡木并捡回来", origin="user"
        )
        assert outcome.action == "created", "技能故障不影响建任务"


# ---------------------------------------------------------------- Bot 装配 + 终态钩子


def wire_bot(*, skills: Any) -> Bot:
    bot = Bot.__new__(Bot)
    bot.log = logging.getLogger("test.skill.bot")
    bot.skills = skills
    bot.skill_planner = None
    bot.minecraft_memory = None
    bot._memory_tasks = set()

    class Tasks:
        async def get(self, task_id: str) -> Any:
            return SimpleNamespace(
                task_id=task_id,
                state=SimpleNamespace(value="SUCCEEDED"),
                objective="去挖一块橡木并捡回来",
                verification={"inventory_delta": {"oak_log": 1}},
                steps=[],
                plan=None,
                plan_version=1,
                plan_hash="h",
                user_id="u",
                message="",
            )

    bot.tasks = Tasks()
    return bot


class TestBotHook:
    async def test_terminal_event_reaches_the_skill_layer(self) -> None:
        seen: list[str] = []

        class Spy:
            async def on_task_finished(self, record: Any) -> None:
                seen.append(str(getattr(record, "task_id", "")))

        bot = wire_bot(skills=Spy())
        bot._remember_task_outcome("task.succeeded", {"task_id": "task_1"})
        await asyncio.gather(*bot._memory_tasks)
        assert seen == ["task_1"]

    async def test_cancelled_tasks_reach_the_skill_layer_too(self) -> None:
        """学习资格门自己拒绝"取消算学会"，但**复用过的技能**必须能看到这个反例。"""

        seen: list[str] = []

        class Spy:
            async def on_task_finished(self, record: Any) -> None:
                seen.append(str(getattr(record, "task_id", "")))

        bot = wire_bot(skills=Spy())
        bot._remember_task_outcome("task.cancelled", {"task_id": "task_c"})
        await asyncio.gather(*bot._memory_tasks)
        assert seen == ["task_c"]

    async def test_non_terminal_events_are_ignored(self) -> None:
        seen: list[str] = []

        class Spy:
            async def on_task_finished(self, record: Any) -> None:
                seen.append("x")

        bot = wire_bot(skills=Spy())
        for event in ("task.started", "task.step_succeeded", "task.confirmation_required"):
            bot._remember_task_outcome(event, {"task_id": "task_1"})
        await asyncio.gather(*bot._memory_tasks) if bot._memory_tasks else None
        assert seen == []

    async def test_broken_skill_layer_does_not_break_the_hook(self) -> None:
        class Broken:
            async def on_task_finished(self, record: Any) -> None:
                raise RuntimeError("skills down")

        bot = wire_bot(skills=Broken())
        bot._remember_task_outcome("task.succeeded", {"task_id": "task_1"})
        await asyncio.gather(*bot._memory_tasks, return_exceptions=True)


class TestSetupSkills:
    async def test_disabled_config_skips_assembly(self, tmp_path: Path) -> None:
        bot = Bot(
            AppConfig(
                bot={"name": "TestBot"},
                database={"url": f"sqlite:///{tmp_path / 'skills_off.db'}"},
                logging={"log_dir": str(tmp_path / "logs")},
                skills={"enabled": False},
            ),
            DummyAdapter(),
        )
        await bot.database.connect()
        await bot._setup_skills()
        assert bot.skills is None
        await bot.database.close()

    async def test_assembly_without_minecraft_is_graceful(self, tmp_path: Path) -> None:
        bot = Bot(
            AppConfig(
                bot={"name": "TestBot"},
                database={"url": f"sqlite:///{tmp_path / 'skills_on.db'}"},
                logging={"log_dir": str(tmp_path / "logs")},
            ),
            DummyAdapter(),
        )
        await bot.database.connect()
        await bot._setup_skills()
        assert bot.skills is not None
        view = await bot.skills.view()
        assert view["enabled"] is True and view["degraded"] is False
        # 没有 Minecraft 观察通道 → 给不出计划候选（回退既有模板）
        assert await bot.skills.suggest("去挖一块橡木并捡回来") is None
        await bot.database.close()


# ---------------------------------------------------------------- 迁移 34


class TestMigration34:
    async def test_tables_exist_and_migration_is_idempotent(self, tmp_path: Path) -> None:
        url = f"sqlite:///{tmp_path / 'mig.db'}"
        database = Database(DatabaseConfig(url=url))
        await database.connect()
        rows = await database.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'procedural%'"
        )
        names = {str(row["name"]) for row in rows}
        assert {
            "procedural_skills",
            "procedural_skill_evidence",
            "procedural_skill_usage",
        } <= names
        version = await database.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        assert int((version or {}).get("v") or 0) >= 34
        await database.close()
        # 再连一次（同一文件）：迁移必须幂等
        again = Database(DatabaseConfig(url=url))
        await again.connect()
        version = await again.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        assert int((version or {}).get("v") or 0) >= 34
        await again.close()

    async def test_upgrade_from_a_v33_database_applies_34(self, tmp_path: Path) -> None:
        """旧库升级兼容：已有 33 版的库（含业务数据）连上来会补上 34，且不动既有表。"""

        url = f"sqlite:///{tmp_path / 'upgrade.db'}"
        database = Database(DatabaseConfig(url=url))
        await database.connect()
        # 模拟"升级前"：删掉 34 的三张表与迁移记录（既有表与数据保留）
        await database.execute("DROP TABLE IF EXISTS procedural_skill_usage")
        await database.execute("DROP TABLE IF EXISTS procedural_skill_evidence")
        await database.execute("DROP TABLE IF EXISTS procedural_skills")
        await database.execute("DELETE FROM schema_migrations WHERE version >= 34")
        await database.execute(
            "INSERT INTO memories (scope_key, category, content, content_hash, created_at,"
            " updated_at) VALUES ('character:x', 'fact', '既有数据', 'h1', 1, 1)"
        )
        await database.close()
        # 再连一次 = 真实升级路径
        upgraded = Database(DatabaseConfig(url=url))
        await upgraded.connect()
        rows = await upgraded.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'procedural%'"
        )
        assert len(rows) == 3, "升级后三张表都在"
        version = await upgraded.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        assert int((version or {}).get("v") or 0) == 34
        kept = await upgraded.fetchall("SELECT COUNT(*) AS n FROM memories")
        assert int(kept[0]["n"]) == 1, "既有业务数据不受影响"
        await upgraded.close()

    async def test_evidence_uniqueness_is_enforced_by_the_database(self, tmp_path: Path) -> None:
        from app.tasks.skill import EvidenceVerdict, SkillEvidence
        from app.tasks.skill_store import SqliteSkillStore

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'uniq.db'}"))
        await database.connect()
        store = SqliteSkillStore(database)
        evidence = SkillEvidence(
            evidence_id="EV-1",
            subject_key="subject",
            task_id="task_1",
            plan_version=1,
            plan_hash="h1",
            verdict=EvidenceVerdict.POSITIVE.value,
            reason_code="QUALIFIED",
        )
        first, created = await store.add_evidence(evidence)
        assert created is True
        second, created_again = await store.add_evidence(
            SkillEvidence.from_payload({**evidence.to_payload(), "evidence_id": "EV-2"})
        )
        assert created_again is False and second.evidence_id == "EV-1"
        await database.close()


# ---------------------------------------------------------------- E15 只读 API


class TestSkillsApi:
    async def test_read_only_view_is_served_and_has_no_actions(self, tmp_path: Path) -> None:
        from tests.test_web_api_runtime import v1_server

        async with v1_server(tmp_path) as (client, bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/world/skills")
            assert status == 200
            data = payload["data"]
            assert set(data) >= {"enabled", "degraded", "counts", "skills", "evidence", "stats"}
            assert data["enabled"] is False  # 这个测试 Bot 没装技能层 → 恒 200 + enabled:false

            # 装上真的技能服务后：视图变成"有技能"，但**任何**执行入口都不存在
            service = await promoted_service()
            bot.skills = service
            status, payload = await client.get("/api/v1/world/skills")
            assert status == 200
            data = payload["data"]
            assert data["enabled"] is True and data["counts"].get("ACTIVE") == 1
            assert data["skills"][0]["server_id"]
            blob = str(data)
            for verb in ("confirm", "execute", "run", "approve"):
                assert verb not in blob
