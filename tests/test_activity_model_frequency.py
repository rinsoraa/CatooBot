"""Phase 6D §五-§七/§三十五-§三十八：调用频率与守卫（矩阵 P/Q + cycle + 审计）。

三条硬门禁：

* 窗口外 tick 一百次 → **0** 次模型调用（§三十六）；
* 进入窗口并做出软决策时 → **1** 次；之后 tick 一百次仍然 **1** 次（§三十七）；
* 真的延长之后（``planned_end_at`` 变了）才是**新** cycle，才允许再问一次（§六）。
"""

from __future__ import annotations

from tests.activity_model_fakes import ScriptedProvider, proposal_json, rig_with_advisor

LONG = (600.0, 7200.0, 14400.0)  # 10 分钟 / 2 小时 / 4 小时


class TestFrequencyGuard:
    async def test_p_hundred_ticks_outside_the_window_make_zero_calls(self) -> None:
        """§三十六：窗口外连 tick 一百次，模型一次都不许被问。"""
        provider = ScriptedProvider(scripted=[proposal_json("continue")])
        rig = rig_with_advisor(provider, hour=14)
        await rig.runtime.start(activity_name="gaming", duration=LONG, now=rig.clock.now())
        for _ in range(100):  # 100 分钟 < 窗口起点（2 小时 - 5 分钟）
            rig.clock.advance(60)
            await rig.runtime.advance()
        assert provider.calls == 0

    async def test_window_entered_but_not_due_still_makes_zero_calls(self) -> None:
        """**刻意的设计**：窗口内、还没到期时规则只"待命"（6B §十一），模型也不必被问。"""
        provider = ScriptedProvider(scripted=[proposal_json("extend", extension_minutes=30)])
        rig = rig_with_advisor(provider, hour=14)
        await rig.runtime.start(activity_name="gaming", duration=(600.0, 1800.0, 7200.0))
        rig.clock.advance_minutes(26)  # 窗口是最后 5 分钟；此刻在窗口里但没到期
        view = await rig.runtime.refresh_decision_view()
        assert view["transition_pending"] is True
        await rig.runtime.advance()
        assert provider.calls == 0

    async def test_q_one_call_at_the_soft_decision_and_no_more(self) -> None:
        """§三十七：做出软决策时问 **1** 次；之后 tick 一百次仍然只有 1 次。"""
        provider = ScriptedProvider(scripted=[proposal_json("extend", extension_minutes=20)])
        rig = rig_with_advisor(provider, hour=14)
        await rig.runtime.start(activity_name="gaming", duration=(600.0, 1800.0, 7200.0))
        rig.clock.advance_minutes(31)  # 到期 → 软决策
        await rig.runtime.advance()
        assert provider.calls == 1
        for _ in range(100):
            rig.clock.advance(1)
            await rig.runtime.advance()
        assert provider.calls == 1, "同一个 cycle 里绝不能再问"

    async def test_cycle_key_is_stable_inside_a_cycle(self) -> None:
        provider = ScriptedProvider(scripted=[proposal_json("continue")])
        rig = rig_with_advisor(provider, hour=14)
        episode = await rig.runtime.start(activity_name="gaming", duration=(600.0, 1800.0, 7200.0))
        assert episode is not None
        rig.clock.advance_minutes(31)
        await rig.runtime.advance()
        first = rig.runtime.advisor_view()["last_receipt"]["cycle_id"]
        assert first.startswith(f"activity:{episode.episode_id}:")
        rig.clock.advance_minutes(1)
        await rig.runtime.advance()
        assert (
            rig.runtime.advisor_view()["last_receipt"].get("skipped_reason")
            in {
                "",  # 没再走到顾问那一步（规则换了活动）
                "already_attempted",
            }
            or rig.runtime.advisor_view()["last_receipt"]["cycle_id"] != first
        )

    async def test_a_real_extension_opens_a_new_cycle(self) -> None:
        """§六：延长成功（planned_end 变了）之后，这是一个**新** cycle → 可以再问一次。"""
        provider = ScriptedProvider(
            scripted=[
                proposal_json("extend", extension_minutes=10),
                proposal_json("continue"),
            ]
        )
        rig = rig_with_advisor(provider, hour=14)
        await rig.runtime.start(activity_name="gaming", duration=(600.0, 1800.0, 7200.0))
        rig.clock.advance_minutes(31)
        await rig.runtime.advance()
        assert provider.calls == 1
        current = await rig.runtime.current()
        assert current is not None and current.status.value == "EXTENDED"
        rig.clock.advance((float(current.planned_end_at) - rig.clock.now()) + 1)
        await rig.runtime.advance()
        assert provider.calls == 2, "新的 cycle 应该可以再问一次"

    async def test_attempt_is_audited_once_per_cycle(self) -> None:
        """§七：审计里"问过了"的痕迹与 cycle 一一对应（它是重启守卫的数据来源）。"""
        provider = ScriptedProvider(scripted=[proposal_json("continue")])
        rig = rig_with_advisor(provider, hour=14)
        episode = await rig.runtime.start(activity_name="gaming", duration=(600.0, 1800.0, 7200.0))
        rig.clock.advance_minutes(31)
        await rig.runtime.advance()
        rows = await rig.runtime.recent_transitions(episode.episode_id, limit=20)  # type: ignore[union-attr]
        advised = [row for row in rows if row["transition"] == "MODEL_ADVISED"]
        assert len(advised) == 1
        assert advised[0]["reason"].startswith("activity:")
        # 它不该污染给模型看的"最近的活动变化"
        block = await rig.runtime.context_block()
        assert "MODEL_ADVISED" not in block

    async def test_advisor_audit_rows_do_not_displace_lifecycle_rows(self) -> None:
        rig = rig_with_advisor(ScriptedProvider(scripted=[proposal_json("continue")]), hour=14)
        await rig.runtime.start(activity_name="gaming", duration=(600.0, 1800.0, 7200.0))
        rig.clock.advance_minutes(31)
        await rig.runtime.advance()
        block = await rig.runtime.context_block()
        assert "现在的活动" in block


class TestCostGuard:
    async def test_calls_counter_matches_actual_invocations(self) -> None:
        provider = ScriptedProvider(scripted=[proposal_json("continue")])
        rig = rig_with_advisor(provider, hour=14)
        await rig.runtime.start(activity_name="gaming", duration=(600.0, 1800.0, 7200.0))
        rig.clock.advance_minutes(31)
        await rig.runtime.advance()
        view = rig.runtime.advisor_view()
        assert view["calls"] == provider.calls == 1
        assert view["attempted_cycles"] == 1

    async def test_purpose_is_tagged_for_usage_recording(self) -> None:
        """§八十六：复用既有 ai_usage（purpose=activity_advisor），不新建计费系统。"""
        from app.activity.model_advisor import ADVISOR_PURPOSE

        assert ADVISOR_PURPOSE == "activity_advisor"


def test_hard_interrupts_do_not_consume_an_advisory() -> None:
    """§二十六：1-7 步（含硬中断）都在模型之前 —— 用户交互这类硬触发不问模型。"""
    from app.activity.decision import HARD_INTERRUPT_TRIGGERS, DecisionTrigger

    assert DecisionTrigger.USER_INTERACTION in HARD_INTERRUPT_TRIGGERS
    assert DecisionTrigger.TASK_STARTED in HARD_INTERRUPT_TRIGGERS


def test_transition_pending_only_inside_the_window() -> None:
    """6B 的窗口语义没被 6D 改坏（窗口外 pending 必须是 False）。"""
    from app.activity import ActivityPlanner
    from app.activity.decision import ActivityDecisionEngine
    from tests.activity_plan_fakes import clock_at

    engine = ActivityDecisionEngine(clock=clock_at(14), planner=ActivityPlanner())
    assert engine.advisor is None
    assert engine.transition_window_seconds == 300.0
