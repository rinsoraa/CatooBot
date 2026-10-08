"""Phase 7A §九-§十五：InitiativeGate 的 hard guards。

矩阵：D（静默时段）/ E（睡觉）/ F（用户任务）/ G（待确认）/ H（刚说过话）/
L（Minecraft 离线）/ M（Minecraft 在线）+ §六十 的 24 小时快进仿真。
"""

from __future__ import annotations

from typing import Any

from app.initiative import (
    HARD_GUARD_ORDER,
    InitiativeGate,
    LifeIntentStatus,
    LifeIntentType,
    suppression_reasons,
)
from app.initiative.candidates import LONG_IDLE_SECONDS, InitiativeContext, MemorySignal
from tests.initiative_fakes import T0, FakeConfig, Rig, commitment, goal


def _candidates(**changes: Any) -> list[Any]:
    """用"有明确信号"的 context 造一批候选（保证门禁有东西可判）。"""
    base = {
        "character_id": "罐头@deadbeef",
        "now": T0,
        "goals": (goal(),),
        "memories": ("Minecraft 里的小城",),
        "social_topics": ("服务器",),
    }
    base.update(changes)
    from app.initiative import propose_intents

    return list(propose_intents(InitiativeContext(**base)))


class TestHardGuards:
    def test_guard_vocabulary_covers_the_taskbook(self) -> None:
        """§十：11 条 hard guard 一条都不能少，而且顺序稳定（= 评估顺序）。"""
        expected = {
            "ACTIVE_USER_TASK",
            "PENDING_CONFIRMATION",
            "RECENT_USER_INTERACTION",
            "QUIET_HOURS",
            "SLEEPING",
            "HIGH_SOCIAL_FATIGUE",
            "MINECRAFT_OFFLINE",
            "RECENT_INITIATIVE",
            "DUPLICATE_INTENT",
            "CHARACTER_RECOVERY",
            "SYSTEM_DEGRADED",
        }
        reasons = set(suppression_reasons())
        assert expected <= reasons
        assert len(HARD_GUARD_ORDER) == 11

    def _decide(self, **changes: Any) -> Any:
        candidates = _candidates(**changes)
        context = InitiativeContext(
            character_id="罐头@deadbeef",
            now=T0,
            **{key: value for key, value in changes.items() if key != "now"},
        )
        return InitiativeGate().evaluate(candidates, context=context, config=FakeConfig())

    def test_e_sleeping_suppresses_everything(self) -> None:
        """E/§十五：``current_activity = sleeping`` → 默认全部 SUPPRESSED，模型也拦不住。"""
        decision = self._decide(sleeping=True)
        assert not decision.allowed
        assert decision.reason == "SLEEPING"
        assert all(item.status is LifeIntentStatus.PROPOSED for item in decision.suppressed)

    def test_d_quiet_hours_suppress_at_night(self) -> None:
        """D/§十四：深夜静默时段（既有 night 窗口）不产生"我突然想出去玩"。"""
        decision = self._decide(quiet_hours=True, period="night")
        assert not decision.allowed and decision.reason == "QUIET_HOURS"

    def test_f_active_user_task_wins(self) -> None:
        """F/§十一：用户任务 RUNNING/WAITING_ACTION/PAUSED → Initiative 不得抢占。"""
        for state in ("RUNNING", "WAITING_ACTION", "PAUSED"):
            decision = self._decide(task_states=(state,))
            assert not decision.allowed and decision.reason == "ACTIVE_USER_TASK", state

    def test_g_pending_confirmation_never_auto_confirms(self) -> None:
        """G/§十二：PENDING_CONFIRMATION → SUPPRESSED reason=PENDING_CONFIRMATION。"""
        decision = self._decide(task_states=("PENDING_CONFIRMATION",))
        assert not decision.allowed
        assert decision.reason == "PENDING_CONFIRMATION"

    def test_h_recent_user_interaction_window(self) -> None:
        """H/§十三：用户刚问过话 → 抑制窗口内什么都不提。"""
        fresh = self._decide(user_interaction_at=T0 - 60.0)
        assert not fresh.allowed and fresh.reason == "RECENT_USER_INTERACTION"
        stale = self._decide(user_interaction_at=T0 - 11 * 60.0)
        assert stale.allowed

    def test_recovery_and_degraded_guards(self) -> None:
        """§四十五/§十：刚重启、或读数降级时都不产生意图。"""
        recovering = self._decide(recovered_at=T0 - 10.0)
        assert not recovering.allowed and recovering.reason == "CHARACTER_RECOVERY"
        degraded = self._decide(degraded="planner_unavailable")
        assert not degraded.allowed and degraded.reason == "SYSTEM_DEGRADED"

    def test_high_social_fatigue(self) -> None:
        """§十 HIGH_SOCIAL_FATIGUE：社交疲劳过高时不再主动起念头。"""
        decision = self._decide(social_fatigue=0.95)
        assert not decision.allowed and decision.reason == "HIGH_SOCIAL_FATIGUE"

    def test_l_minecraft_offline_allows_virtual_interest(self) -> None:
        """L/§三十七：离线**不**封杀"想去看看"这种虚拟兴趣。"""
        decision = self._decide(minecraft_online=False)
        assert decision.allowed, decision.to_payload()
        assert decision.selected is not None
        assert decision.selected.intent_type is LifeIntentType.MINECRAFT_INTEREST

    def test_l_offline_still_blocks_world_required_intents(self) -> None:
        """L/§三十七：只有"明确需要真实世界"的候选才被离线挡下。"""
        from tests.initiative_fakes import intent

        gate = InitiativeGate()
        world_intent = intent(
            intent_type=LifeIntentType.MINECRAFT_INTEREST, tags=("requires_world",)
        )
        decision = gate.evaluate(
            [world_intent],
            context=InitiativeContext(character_id="罐头@deadbeef", now=T0, minecraft_online=False),
            config=FakeConfig(),
        )
        assert not decision.allowed and decision.reason == "MINECRAFT_OFFLINE"

    def test_m_minecraft_online_still_only_a_candidate(self) -> None:
        """M/§三十八：在线也**不**能变成 Task —— 它仍然只是一条候选意图。"""
        decision = self._decide(minecraft_online=True)
        assert decision.allowed and decision.selected is not None
        assert decision.selected.execution_class.value == "VIRTUAL_ONLY"
        assert "requires_world" not in decision.selected.tags

    def test_engine_disabled_produces_nothing(self) -> None:
        """§六十五：``enabled=false`` 只是"不允许产生意图"，不是"允许执行"。"""
        candidates = _candidates()
        decision = InitiativeGate().evaluate(
            candidates,
            context=InitiativeContext(character_id="罐头@deadbeef", now=T0),
            config=FakeConfig(enabled=False),
        )
        assert not decision.allowed and decision.reason == "ENGINE_DISABLED"

    def test_one_proposal_per_pass(self) -> None:
        """§六十：低噪声 —— 一轮最多放行一条，其余如实记 LOWER_PRIORITY。"""
        decision = self._decide()
        assert decision.allowed
        assert len([item for item in decision.verdicts if item.allowed]) == 1
        others = [item for item in decision.verdicts if item.reason == "LOWER_PRIORITY"]
        assert others, decision.to_payload()


class TestFastForward:
    async def test_24h_simulation_is_low_noise(self) -> None:
        """§五十九/§六十：24 小时快进 —— 不是"每分钟一条"，而是一个可解释的低噪声水平。"""
        rig = Rig()
        rig.feed(
            goals=(goal(), goal("G-2", "pet_care", "小喵")),
            memories=(MemorySignal("小城还差一半"),),
            social_topics=("服务器",),
            current_activity="idle",
            current_activity_elapsed=LONG_IDLE_SECONDS + 60,
            planner_candidates=("reading",),
            routine_due=("reading",),
        )
        created = 0
        for _step in range(24 * 6):  # 每 10 分钟 check 一次
            out = await rig.check(advance=600.0)
            created += len(out["created"])
        assert 5 <= created <= 60, created  # 合理区间：不是 0，也绝不是每分钟一条
        rows = await rig.service.recent(limit=500)  # type: ignore[union-attr]
        proposed = [item for item in rows if item.status is LifeIntentStatus.PROPOSED]
        assert len(proposed) <= 5
        for row in rows:
            assert row.expires_at > row.created_at

    async def test_commitments_and_goals_both_reach_the_gate(self) -> None:
        """§十九 + §十六：两条来源都能一路走到门禁（不是只在候选层存在）。"""
        rig = Rig()
        rig.feed(goals=(goal(),), commitments=(commitment(),))
        out = await rig.check()
        assert out["action"] == "proposed"
        assert out["candidate_count"] >= 2
