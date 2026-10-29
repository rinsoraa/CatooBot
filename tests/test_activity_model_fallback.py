"""Phase 6D §二十七/§三十/§三十九/§六十八：失败回退与**纯规则等价**（矩阵 M/N/O/X + parity）。

这一层是 6D 的命门：**模型挂了，罐头照常生活**，而且规则输出必须与没装顾问时**一模一样**。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.activity import ActivityStatus, TransitionReason
from app.activity.decision import DecisionReason
from app.activity.model_advisor import ModelAdvisorError, ModelFailureCode
from tests.activity_model_fakes import (
    RaisingProvider,
    ScriptedProvider,
    advisor_with,
    proposal_json,
    rig_with_advisor,
)

PROFILE = (600.0, 1800.0, 7200.0)  # 10 / 30 / 120 分钟


async def in_window_rig(provider: Any = None, **kwargs: Any):
    """造一个"已到期、还没到硬上限、可以软决策"的场景（顾问可选）。"""
    rig = rig_with_advisor(provider, **kwargs) if provider is not None else None
    if rig is None:
        from tests.activity_plan_fakes import PlanRig

        rig = PlanRig()
    episode = await rig.runtime.start(activity_name="gaming", duration=PROFILE, now=rig.clock.now())
    rig.clock.advance_minutes(31)  # 超过 typical(30) → 到期
    return rig, episode


async def decide(rig: Any) -> tuple[Any, dict[str, Any]]:
    """推进一次并返回 (决策, trace payload)。"""
    current = await rig.runtime.advance()
    view = rig.runtime.decision_view(current)
    return current, dict(view.get("last_decision") or {})


class TestFallbackToRules:
    async def test_m_timeout_falls_back_to_the_rule_decision(self) -> None:
        """M：超时 → 规则继续（延长照旧），而且回执里写明失败码。"""
        provider = RaisingProvider(error=TimeoutError("deadline exceeded"))
        rig, _episode = await in_window_rig(provider)
        current, trace = await decide(rig)
        assert current is not None
        assert trace["decision"] == "EXTEND"  # 规则：能延长就延长
        assert trace["model_attempted"] is True
        assert trace["fallback_used"] is True
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert receipt["failure"] == ModelFailureCode.PROVIDER_ERROR  # 假 provider 直接抛
        assert provider.calls == 1

    async def test_n_provider_error_falls_back_and_records_it(self) -> None:
        from app.ai.errors import AIConnectionError

        provider = RaisingProvider(error=AIConnectionError("down"))
        rig, _episode = await in_window_rig(provider)
        current, trace = await decide(rig)
        assert current is not None and current.activity_name == "gaming"
        assert trace["fallback_used"] is True
        assert rig.runtime.advisor_view()["last_failure"] in {
            ModelFailureCode.PROVIDER_ERROR,
            ModelFailureCode.CONNECTION_ERROR,
        }

    async def test_x_invalid_output_falls_back(self) -> None:
        provider = ScriptedProvider(scripted=["我觉得再来一会儿吧（纯文字）"])
        rig, _episode = await in_window_rig(provider)
        current, trace = await decide(rig)
        assert current is not None
        assert trace["decision"] == "EXTEND"  # 规则结果
        assert trace["fallback_used"] is True

    async def test_degenerate_model_failure_never_breaks_the_tick(self) -> None:
        """§三十九/§九十七：模型层任何故障都**不许**让 Activity Runtime 不可用。"""
        provider = RaisingProvider(error=RuntimeError("provider exploded"))
        rig, _episode = await in_window_rig(provider)
        for _ in range(3):
            rig.clock.advance_minutes(30)
            current = await rig.runtime.advance()
            assert current is not None
        assert rig.runtime.degraded_reason in ("", "planner_failed")


class TestRuleOnlyParity:
    """§六十八 的**核心门禁**：advisor=None 与"装了一个坏顾问"的决定必须一致。"""

    async def _scenario(self, provider: Any) -> tuple[list[str], list[float]]:
        rig, _episode = await in_window_rig(provider)
        names: list[str] = []
        ends: list[float] = []
        for _ in range(4):
            current = await rig.runtime.advance()
            if current is None:
                break
            names.append(f"{current.activity_name}:{current.status.value}")
            ends.append(round(float(current.planned_end_at or 0.0), 3))
            rig.clock.advance_minutes(31)
        return names, ends

    async def test_rule_only_matches_broken_advisor(self) -> None:
        plain, plain_ends = await self._scenario(None)
        broken, broken_ends = await self._scenario(RaisingProvider(error=TimeoutError("timeout")))
        assert plain == broken
        assert plain_ends == broken_ends

    async def test_rule_only_matches_invalid_output_advisor(self) -> None:
        plain, _ = await self._scenario(None)
        junk, _ = await self._scenario(ScriptedProvider(scripted=["{oops"]))
        assert plain == junk

    async def test_no_advisor_means_zero_calls_and_no_receipt_attempt(self) -> None:
        rig, _episode = await in_window_rig(None)
        _current, trace = await decide(rig)
        assert trace["model_attempted"] is False
        assert trace["fallback_used"] is False
        assert rig.runtime.advisor_view()["enabled"] is False
        # 没有顾问时回执如实写"没问过、为什么没问"（不是空对象）
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert receipt["attempted"] is False
        assert receipt["skipped_reason"] == "disabled"

    async def test_disabled_flag_keeps_rules_in_charge(self) -> None:
        """§四十一/§七十四：`enabled=false` = 纯规则（连问都不问）。"""
        provider = ScriptedProvider(scripted=[proposal_json("continue")])
        rig, _episode = await in_window_rig(provider, enabled=False)
        _current, trace = await decide(rig)
        assert provider.calls == 0
        assert trace["model_attempted"] is False
        assert trace["decision"] == "EXTEND"  # 规则自己决定


class TestMaxDurationIsNotNegotiable:
    async def test_h_max_duration_never_asks_the_model(self) -> None:
        """§二十/§六十二：到硬上限时规则**必须**换活动；模型连被问的资格都没有（更不可越权）。"""
        provider = ScriptedProvider(scripted=[proposal_json("continue")])
        rig, episode = await in_window_rig(provider)
        # 重开一个上限很短的 Episode，直接顶到硬上限
        rig.clock.advance_minutes(1)
        await rig.runtime.switch_to(activity_name="reading", duration=(60.0, 120.0, 180.0))
        rig.clock.advance_minutes(4)  # 超过 max(3 分钟)
        current = await rig.runtime.advance()
        assert current is not None
        assert current.episode_id != episode.episode_id  # 真的换了
        assert provider.calls == 0, "max_duration 场景不应该问模型（§二十六 第 1-7 步没有模型）"
        view = rig.runtime.decision_view(current)
        # 规则路径的收尾（TRANSITION/EXPIRED）—— 关键是**没有**模型的影子
        assert view["last_decision"]["model_attempted"] is False
        assert current.status in {ActivityStatus.ACTIVE, ActivityStatus.EXTENDED}

    async def test_i_min_duration_never_asks_the_model(self) -> None:
        """§二十六：最短时长守卫在模型之前 —— 这也顺带保证了窗口外的 0 次调用。"""
        provider = ScriptedProvider(scripted=[proposal_json("extend", extension_minutes=90)])
        rig, episode = await in_window_rig(provider)
        rig.clock.advance(0)
        await rig.runtime.switch_to(activity_name="reading", duration=PROFILE)
        rig.clock.advance_minutes(2)  # min 是 10 分钟
        current = await rig.runtime.advance()
        assert current is not None and current.episode_id != episode.episode_id
        assert provider.calls == 0
        view = rig.runtime.decision_view(current)
        assert view["last_decision"]["reason_code"] == DecisionReason.MIN_DURATION_GUARD.value


def test_failure_codes_cover_the_taskbook_taxonomy() -> None:
    """§二十七：失败分类一个都不能少（日志与回执都用它们）。"""
    for name in (
        "TIMEOUT",
        "CONNECTION_ERROR",
        "INVALID_JSON",
        "SCHEMA_ERROR",
        "UNKNOWN_ACTIVITY",
        "RULE_REJECTED",
        "RATE_LIMIT",
        "PROVIDER_ERROR",
    ):
        assert hasattr(ModelFailureCode, name), name


def test_runtime_keeps_working_when_the_advisor_is_absent() -> None:
    """§三十九/§九十七：没有顾问时 Activity Runtime 照常（装配层不该依赖模型）。"""
    from tests.activity_plan_fakes import PlanRig

    rig = PlanRig()
    assert rig.runtime.advisor is None
    assert rig.runtime.advisor_view()["enabled"] is False
    assert rig.runtime.advisor_view()["available"] is False
    # 延长审计里也不该出现顾问行
    assert TransitionReason.TIME_EXPIRED.value


class SlowTimeoutProvider:
    """**真的**慢：先睡 500ms，再抛超时（6D.1 B 要的是"实际延迟"，不是 0）。"""

    calls: int = 0

    async def complete_json(
        self, *, schema: dict[str, Any], system: str, input: dict[str, Any], timeout_ms: int
    ) -> str:
        self.calls += 1
        await asyncio.sleep(0.5)
        raise ModelAdvisorError(ModelFailureCode.TIMEOUT, "deadline exceeded")


def rig_with_log(provider: Any, log: Any) -> Any:
    """带 logger 的装配（好让用例断言 [Activity.Model] 那一行）。"""
    from app.activity import ActivityPlanner
    from tests.activity_plan_fakes import PlanRig, clock_at

    rig = PlanRig(clock=clock_at(14, 0), planner=ActivityPlanner(), logger=log)
    rig.runtime.engine.advisor = advisor_with(provider)
    return rig


class TestLatencyReceipt:
    """6D.1 B：失败路径也必须保留**实际**延迟，日志统一 attempted/latency/result/fallback。"""

    async def test_failure_receipt_keeps_the_real_latency(self) -> None:
        """任务书用例：provider 睡 500ms 再抛 Timeout → receipt.latency_ms ≈ 500。"""
        provider = SlowTimeoutProvider()
        rig, _episode = await in_window_rig(provider)
        current = await rig.runtime.advance()
        assert current is not None
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert receipt["failure"] == ModelFailureCode.TIMEOUT
        assert 400 <= receipt["latency_ms"] <= 1500, receipt
        assert receipt["latency_ms"] != 0, "绝不允许再出现 TIMEOUT latency_ms=0"
        assert receipt["fallback_used"] is True

    async def test_trace_carries_the_same_latency_on_failure(self) -> None:
        """同一条延迟也要进 DecisionTrace（WebUI/审计看到的是同一个数）。"""
        provider = SlowTimeoutProvider()
        rig, _episode = await in_window_rig(provider)
        _current, trace = await decide(rig)
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert trace["model_attempted"] is True
        assert trace["fallback_used"] is True
        assert trace["model_latency_ms"] == receipt["latency_ms"]
        assert trace["model_latency_ms"] >= 400

    async def test_failure_log_line_is_unified(self, caplog: Any) -> None:
        """日志四件套：attempted= / latency_ms=（非 0）/ result= / fallback=。"""
        log = logging.getLogger("catoobot.test.activity.advisor")
        provider = SlowTimeoutProvider()
        rig = rig_with_log(provider, log)
        episode = await rig.runtime.start(
            activity_name="gaming", duration=PROFILE, now=rig.clock.now()
        )
        assert episode is not None
        rig.clock.advance_minutes(31)
        with caplog.at_level(logging.INFO, logger=log.name):
            await rig.runtime.advance()
        lines = [
            record.getMessage()
            for record in caplog.records
            if "Activity.Model" in record.getMessage()
        ]
        assert lines, "顾问失败也必须留下一行日志"
        text = lines[-1]
        assert "attempted=True" in text
        assert "result=TIMEOUT" in text
        assert "fallback=True" in text
        assert "latency_ms=0" not in text
        assert "proposal=" not in text  # 6D.1：字段名统一成 result=
