"""Phase A tests: 最近行为软惩罚（repetition / recency penalty）。

契约：
* 刚做完的同一动作明显失分（favorite 的 +0.3 不再永远霸榜），但不是硬冷却；
* 真正 critical 的需求可以把惩罚压到 1/4，紧急行为仍会发生；
* 惩罚随时间衰减，且只有跨过离散 band 边界才重新开放决策机会
  —— 时间正常流逝不会导致每 tick 决策；
* 重启后惩罚历史从数据库重建，不会「一重启就立刻重买」。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.models import ActionStatus
from app.sandbox.runtime import (
    RECENCY_CRITICAL_FACTOR,
    RECENCY_PENALTY_FRESH,
    RECENCY_SAME_SPACE_BONUS,
    SandboxRuntime,
)
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db


async def make_sandbox(*, db, clock, **cfg):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7, **cfg),
        SandboxStore(db),
        bible=bible,
        clock=clock,
    )
    await runtime.start()
    return runtime


def definition(runtime, action_id: str):  # type: ignore[no-untyped-def]
    definition = runtime.actions.definitions.get(action_id)
    assert definition is not None, f"fixture bible should own {action_id}"
    return definition


class TestPenalty:
    async def test_a_just_finished_action_loses_its_crown(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Case A：刚完成 buy_sweets → 分数被压掉，favorite 不再霸榜。"""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            buy = definition(runtime, "buy_sweets")
            assert runtime._recency_penalty(buy) == 0.0  # noqa: SLF001

            runtime._note_action_finished(  # noqa: SLF001
                SimpleNamespace(definition_id="buy_sweets", space_id="dessert_shop")
            )
            penalty = runtime._recency_penalty(buy)  # noqa: SLF001
            # 同地点重复：fresh 基值 + 同地点加成
            assert penalty == RECENCY_PENALTY_FRESH + RECENCY_SAME_SPACE_BONUS

            engine = runtime.engine
            scored = {d.id: engine._score(d, hour=15) for d in engine.actions.definitions.values()}  # noqa: SLF001
            # 刚做过 → 明显失分（preference 的 +0.3 被压过）
            assert engine._penalty(buy) == penalty  # noqa: SLF001
            assert "recent" in engine._score_reasons(buy, hour=15)  # noqa: SLF001
            # 至少有一个别的动作排在它前面（不是永远最高分）
            assert any(score > scored["buy_sweets"] for score in scored.values())
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_critical_need_shrinks_the_penalty(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Case B：critical 需求覆盖普通惩罚（紧急时允许重复）。

        Phase B：buy_sweets 已变成纯采购（不再缓解 hunger），这里改用一个
        真正消费食物的动作来验证同一条契约。
        """
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            eat = definition(runtime, "eat_pudding")
            runtime._note_action_finished(  # noqa: SLF001
                SimpleNamespace(definition_id="eat_pudding", space_id="kitchen")
            )
            normal = runtime._recency_penalty(eat)  # noqa: SLF001

            hunger = runtime.needs.get("hunger")
            assert hunger is not None
            hunger.level = 0.99
            assert "hunger" in {need.key for need in runtime.needs.critical()}
            critical = runtime._recency_penalty(eat)  # noqa: SLF001
            assert critical < normal
            assert abs(critical - normal * RECENCY_CRITICAL_FACTOR) < 1e-6
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_penalty_decays_across_discrete_bands(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Case C：惩罚随 band 衰减；只有跨边界才改变决策机会签名。"""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            buy = definition(runtime, "buy_sweets")
            runtime._note_action_finished(  # noqa: SLF001
                SimpleNamespace(definition_id="buy_sweets", space_id="dessert_shop")
            )
            fresh_signature = runtime.decision_opportunity()
            assert fresh_signature.endswith("|fresh")
            # 没做过的动作没有惩罚；做过的有
            assert runtime._recency_penalty(buy) > runtime._recency_penalty(
                definition(runtime, "eat_cake")
            )  # noqa: SLF001

            # 同一个瞬间取两次签名必须一致（时间流逝本身不产生新机会）
            clock.advance(60.0)
            assert runtime.decision_opportunity() == fresh_signature  # still fresh band

            clock.advance(30 * 60.0)  # 跨过 fresh → recent
            recent_signature = runtime.decision_opportunity()
            assert recent_signature.endswith("|recent")
            assert recent_signature != fresh_signature
            assert 0.0 < runtime._recency_penalty(buy) < RECENCY_PENALTY_FRESH  # noqa: SLF001

            clock.advance(5 * 60 * 60.0)  # 超过 normal 上限
            assert runtime.decision_opportunity().endswith("|stale")
            assert runtime._recency_penalty(buy) == 0.0  # noqa: SLF001
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_history_survives_a_restart(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """重启后从数据库重建惩罚历史，不会立刻重做同一件事。"""

        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            buy = definition(runtime, "buy_sweets")
            instance = runtime.actions.start(buy, space_id="dessert_shop", reason_code="test")
            runtime.actions.finish(instance, ActionStatus.completed)
            runtime._note_action_finished(instance)  # noqa: SLF001
            await runtime.store.save_action(instance)
        finally:
            await runtime.shutdown()

        revived = await make_sandbox(db=db, clock=clock)
        try:
            assert revived._recent_actions, "history should be rebuilt from sandbox_actions"  # noqa: SLF001
            assert revived._recency_penalty(definition(revived, "buy_sweets")) > 0.0  # noqa: SLF001
        finally:
            await revived.shutdown()
            await db.close()
