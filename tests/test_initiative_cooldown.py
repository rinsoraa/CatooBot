"""Phase 7A §二十一/§三十三：冷却与防爆。

矩阵：C（冷却）/ U（burst protection）。
"""

from __future__ import annotations

from app.initiative import LifeIntentStatus, LifeIntentType, time_bucket
from app.initiative.gate import BURST_MAX_IN_WINDOW, BURST_WINDOW_SECONDS
from tests.initiative_fakes import T0, FakeConfig, Rig, goal, intent


class TestCooldown:
    async def test_c_same_class_needs_to_wait_for_the_cooldown(self) -> None:
        """C/§二十一：同类 Initiative 在冷却里不再重复提（默认 20 分钟）。"""
        rig = Rig()
        rig.feed(goals=(goal(),))
        first = await rig.check()
        assert first["action"] == "proposed"

        second = await rig.check(advance=5 * 60.0)  # 5 分钟：还在冷却里
        assert second["created"] == []
        assert second["reason"] in {"RECENT_INITIATIVE", "DUPLICATE_INTENT"}

    async def test_cooldown_is_visible_for_the_ui(self) -> None:
        """§四十九：界面上要看得见冷却（还剩多少秒 / 本小时几条）。"""
        rig = Rig()
        rig.feed(goals=(goal(),))
        await rig.check()
        cooldown = await rig.service.cooldown_view(T0 + 60.0)  # type: ignore[union-attr]
        assert cooldown["minutes"] == 20
        assert 0 < cooldown["seconds_remaining"] <= 20 * 60
        assert cooldown["proposals_last_hour"] == 1

    def test_cooldown_only_counts_proposed_intents(self) -> None:
        """§二十五：被抑制不算"产生过" —— 否则睡一觉全被自己挡住。"""
        from app.initiative import InitiativeGate, propose_intents
        from app.initiative.candidates import InitiativeContext

        context = InitiativeContext(character_id="c", now=T0, goals=(goal(),))
        candidates = list(propose_intents(context))
        suppressed_only = [
            intent(intent_type=LifeIntentType.MINECRAFT_INTEREST, created_at=T0 - 60.0),
        ]
        object.__setattr__(suppressed_only[0], "status", LifeIntentStatus.SUPPRESSED)
        decision = InitiativeGate().evaluate(
            candidates, context=context, config=FakeConfig(), recent_intents=suppressed_only
        )
        assert decision.allowed, decision.to_payload()

    def test_zero_cooldown_means_no_cooldown(self) -> None:
        """§二十一：``cooldown_minutes=0`` 是合法配置（明确表示"不要冷却"）。"""
        from app.initiative import InitiativeGate, propose_intents
        from app.initiative.candidates import InitiativeContext

        context = InitiativeContext(character_id="c", now=T0, goals=(goal(),))
        decision = InitiativeGate().evaluate(
            list(propose_intents(context)),
            context=context,
            config=FakeConfig(cooldown_minutes=0),
            recent_intents=[intent(created_at=T0 - 10.0)],
        )
        assert decision.allowed


class TestBurstProtection:
    def _evaluate(self, recent: list, config: FakeConfig) -> object:
        from app.initiative import InitiativeGate, propose_intents
        from app.initiative.candidates import InitiativeContext

        context = InitiativeContext(character_id="c", now=T0, goals=(goal(),))
        return InitiativeGate().evaluate(
            list(propose_intents(context)),
            context=context,
            config=config,
            recent_intents=recent,
        )

    def test_u_five_in_ten_minutes_enters_cooldown(self) -> None:
        """U/§三十三：10 分钟里已经有 5 条 → 进入 INITIATIVE_COOLDOWN，不再产生。"""
        recent = [
            intent(created_at=T0 - 60.0 * (index + 1), semantic_key=f"k{index}")
            for index in range(5)
        ]
        decision = self._evaluate(recent, FakeConfig(max_proposals_per_hour=99))
        assert not decision.allowed
        assert decision.reason == "BURST_PROTECTION"
        assert decision.guards["_BURST"]["in_window"] == 5
        assert BURST_MAX_IN_WINDOW == 5 and BURST_WINDOW_SECONDS == 600.0

    def test_u_hourly_cap(self) -> None:
        """U/§三十三：``max_proposals_per_hour`` 是配置里的硬上限。"""
        recent = [
            intent(created_at=T0 - 60.0 * (index + 1), semantic_key=f"k{index}")
            for index in range(3)
        ]
        decision = self._evaluate(recent, FakeConfig(max_proposals_per_hour=3))
        assert not decision.allowed and decision.reason == "BURST_PROTECTION"

    def test_old_intents_do_not_count(self) -> None:
        """§三十三：窗口外的旧意图不参与计数。"""
        recent = [
            intent(created_at=T0 - 7200.0 - index, semantic_key=f"k{index}") for index in range(9)
        ]
        decision = self._evaluate(recent, FakeConfig())
        assert decision.allowed and decision.to_payload()["proposals_last_hour"] == 0

    def test_bucket_is_hourly(self) -> None:
        """§二十：时间桶就是一小时（同一个小时里同一条想法只有一个指纹）。"""
        assert time_bucket(T0) == time_bucket(T0 + 1.0)
        assert time_bucket(T0) != time_bucket(T0 + 3600.0)
