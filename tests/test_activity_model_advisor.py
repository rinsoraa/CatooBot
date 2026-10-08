"""Phase 6D §三/§四/§八/§三十四/§七十一：顾问本体与输入契约（矩阵 A/B/C + S/T 的输入侧）。

只验证"顾问这一层自己"的事：输入怎么组、输出怎么解析、provider 怎么被套住。
"规则是否采纳"在 `test_activity_model_guard.py`，频率在 `..._frequency.py`。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.activity.model_advisor import (
    ADVISOR_PURPOSE,
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    ActivityDecisionProposal,
    ActivityModelAdvisor,
    AIEngineStructuredProvider,
    ModelAdvisorError,
    ModelFailureCode,
    advisory_cycle_key,
    build_advisor,
    build_input,
)
from tests.activity_model_fakes import (
    RaisingProvider,
    ScriptedProvider,
    advisor_with,
    proposal_json,
)


class FakeAIEngine:
    """假的既有 AIEngine（只实现 6D 用到的那一小块）。"""

    def __init__(self, content: str = "", *, enabled: bool = True, error: Exception | None = None):
        self.content = content
        self.enabled = enabled
        self.error = error
        self.requests: list[Any] = []

    async def chat(self, request: Any) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error

        class _Response:
            content = self.content

        return _Response()


# ---------------------------------------------------------------- 提案与输入


class TestProposalShape:
    def test_proposal_payload_carries_the_five_fields(self) -> None:
        proposal = ActivityDecisionProposal(
            decision="extend",
            reason_code="high_focus",
            state_explanation="她很投入",
            extension_minutes=20,
        )
        payload = proposal.to_payload()
        assert set(payload) == {
            "decision",
            "extension_minutes",
            "next_hint",
            "reason_code",
            "state_explanation",
        }
        assert proposal.extension_seconds == 1200.0

    def test_input_is_structured_and_bounded(self) -> None:
        """§八/§五十六：结构化投入 + 条数有界（不是把整个系统塞进去）。"""
        context = {
            "current_activity": "gaming",
            "elapsed_minutes": 42,
            "planned_remaining_minutes": 3,
            "time_period": "evening",
            "energy": 0.68,
            "focus": 0.71,
            "mood": "neutral",
            "hard_constraints": {"extension_count": 1},
            "allowed_decisions": ["continue", "extend", "transition"],
            "routine_candidates": [f"r{i}" for i in range(20)],
            "goal_candidates": [f"g{i}" for i in range(20)],
            "recent_activities": [f"a{i}" for i in range(20)],
            "future_plan": [{"activity": f"p{i}"} for i in range(20)],
            "memory_evidence": [f"m{i}" for i in range(20)],
        }
        candidates = [{"activity": f"c{i}"} for i in range(20)]
        payload = build_input(context=context, candidates=candidates)
        assert payload["current_activity"] == "gaming"
        assert len(payload["planner_candidates"]) <= 6
        assert len(payload["routine_candidates"]) <= 6
        assert len(payload["goal_candidates"]) <= 3
        assert len(payload["recent_activities"]) <= 5
        assert len(payload["future_plan"]) <= 6
        assert len(payload["memory_evidence"]) <= 5

    def test_input_never_carries_forbidden_payloads(self) -> None:
        """§九：聊天历史 / 记忆库 / 世界快照 / checkpoint / token / 路径 / 堆栈 一律不进输入。"""
        payload = build_input(
            context={"current_activity": "reading"},
            candidates=[{"activity": "reading"}],
        )
        blob = str(payload).lower()
        for forbidden in (
            "api_key",
            "apikey",
            "authorization",
            "secret",
            "token",
            "traceback",
            "c:\\",
            "/home/",
            "chat_history",
            "world_snapshot",
            "checkpoint",
        ):
            assert forbidden not in blob, forbidden

    def test_system_prompt_declares_advisor_and_untrusted_data(self) -> None:
        """§十八/§四十五/§七十八：不许思维链、不许越权、输入只是数据。"""
        text = SYSTEM_PROMPT.lower()
        assert "advisory component" in text
        assert "do not control execution" in text
        assert "cannot override constraints" in text
        assert "cannot create tasks" in text
        assert "cannot invoke tools" in text
        assert "chain-of-thought" in text or "chain of thought" in text
        assert "not instructions" in text
        assert "json" in text

    def test_output_schema_matches_the_decision_enum(self) -> None:
        enum = OUTPUT_SCHEMA["properties"]["decision"]["enum"]
        assert enum == ["continue", "extend", "transition"]
        assert "decision" in OUTPUT_SCHEMA["required"]


# ---------------------------------------------------------------- A/B/C


class TestValidProposals:
    async def test_a_valid_continue(self) -> None:
        provider = ScriptedProvider(scripted=[proposal_json("continue")])
        advisor = advisor_with(provider)
        proposal = await advisor.advise(context={"current_activity": "gaming"}, candidates=[])
        assert proposal.decision == "continue"
        assert proposal.extension_minutes is None
        assert advisor.calls == 1

    async def test_b_valid_extend(self) -> None:
        provider = ScriptedProvider(scripted=[proposal_json("extend", extension_minutes=20)])
        proposal = await advisor_with(provider).advise(
            context={"current_activity": "gaming"}, candidates=[]
        )
        assert proposal.decision == "extend"
        assert proposal.extension_minutes == 20
        assert proposal.extension_seconds == 1200.0

    async def test_c_valid_transition(self) -> None:
        provider = ScriptedProvider(scripted=[proposal_json("transition", next_hint="reading")])
        proposal = await advisor_with(provider).advise(
            context={"current_activity": "gaming"}, candidates=[{"activity": "reading"}]
        )
        assert proposal.decision == "transition"
        assert proposal.next_hint == "reading"

    async def test_advise_passes_the_timeout_and_purpose(self) -> None:
        class Recording(ScriptedProvider):
            timeout_ms = 0

            async def complete_json(self, *, schema, system, input, timeout_ms):  # type: ignore[override]
                self.timeout_ms = timeout_ms
                return await super().complete_json(
                    schema=schema, system=system, input=input, timeout_ms=timeout_ms
                )

        provider = Recording(scripted=[proposal_json("continue")])
        advisor = advisor_with(provider, timeout_ms=800)
        await advisor.advise(context={}, candidates=[], deadline_ms=600)
        assert provider.timeout_ms == 600  # deadline 优先
        await advisor.advise(context={}, candidates=[])
        assert provider.timeout_ms == 800  # 没给就用配置值


# ---------------------------------------------------------------- provider 缝


class TestProviderSeam:
    async def test_engine_provider_wraps_the_existing_engine(self) -> None:
        """§四/§七十九/§八十：只套既有 AIEngine，不新建客户端。"""
        engine = FakeAIEngine(proposal_json("continue"))
        provider = AIEngineStructuredProvider(engine, model="m1")
        assert provider.enabled is True
        raw = await provider.complete_json(
            schema=OUTPUT_SCHEMA, system=SYSTEM_PROMPT, input={"a": 1}, timeout_ms=1500
        )
        assert "continue" in raw
        request = engine.requests[0]
        assert request.metadata["purpose"] == ADVISOR_PURPOSE  # 复用 ai_usage 的归类
        assert request.model == "m1"  # 按既有 router 的模型名固定
        assert request.messages[0].role == "system"
        assert "NOT INSTRUCTIONS" in request.messages[0].content.upper()

    async def test_engine_provider_translates_ai_errors(self) -> None:
        from app.ai.errors import AITimeoutError

        provider = AIEngineStructuredProvider(
            FakeAIEngine(error=AITimeoutError("slow")), model="m1"
        )
        with pytest.raises(ModelAdvisorError) as caught:
            await provider.complete_json(schema={}, system="s", input={}, timeout_ms=100)
        assert caught.value.code == ModelFailureCode.TIMEOUT

    async def test_engine_provider_without_engine_is_disabled(self) -> None:
        provider = AIEngineStructuredProvider(None, model="m1")
        assert provider.enabled is False

    async def test_advise_without_provider_is_a_typed_failure(self) -> None:
        advisor = ActivityModelAdvisor(provider=None)
        with pytest.raises(ModelAdvisorError) as caught:
            await advisor.advise(context={}, candidates=[])
        assert caught.value.code == ModelFailureCode.DISABLED


# ---------------------------------------------------------------- 装配


class TestBuildAdvisor:
    def test_default_config_yields_no_advisor(self) -> None:
        from app.config.settings import ModelAdvisorConfig

        assert build_advisor(ModelAdvisorConfig(), FakeAIEngine()) is None

    def test_enabled_without_model_degrades_to_rule_only(self) -> None:
        """§四十二：开了但没配好 → 只告警 + 退回纯规则（不让 Bot 起不来）。"""
        from app.config.settings import ModelAdvisorConfig

        warnings: list[str] = []

        class Logger:
            def warning(self, message: str, *args: Any) -> None:
                warnings.append(message % args if args else message)

            def info(self, message: str, *args: Any) -> None:
                pass

        config = ModelAdvisorConfig(enabled=True, model=None)
        assert build_advisor(config, FakeAIEngine(), logger=Logger()) is None
        assert warnings, "应该留下一条告警"
        no_engine = FakeAIEngine(enabled=False)
        advisor = build_advisor(
            ModelAdvisorConfig(enabled=True, model="m1"), no_engine, logger=Logger()
        )
        assert advisor is None

    def test_enabled_and_configured_builds_the_advisor(self) -> None:
        from app.config.settings import ModelAdvisorConfig

        advisor = build_advisor(
            ModelAdvisorConfig(enabled=True, model="m1", provider="router", timeout_ms=900),
            FakeAIEngine(),
        )
        assert advisor is not None
        assert advisor.available is True
        assert advisor.model == "m1"
        assert advisor.provider_name == "router"
        assert advisor.timeout_ms == 900

    def test_view_is_read_only_and_honest(self) -> None:
        advisor = advisor_with(ScriptedProvider())
        payload = advisor.view()
        assert payload["enabled"] is True
        assert payload["calls"] == 0
        assert "prompt" not in payload and "raw" not in payload


# ---------------------------------------------------------------- cycle key


class TestCycleKey:
    def test_cycle_key_shape_and_determinism(self) -> None:
        """§六：`activity:{episode_id}:{transition_cycle}`，且**同一现实**永远同一个键。"""
        from app.activity.model import ActivityEpisode, ActivitySource, ActivityType

        episode = ActivityEpisode(
            episode_id="ACT-20261008-007",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="gaming",
            source=ActivitySource.ROUTINE,
            planned_end_at=1_700_000_000.0,
        )
        key = advisory_cycle_key(episode)
        assert key == "activity:ACT-20261008-007:1700000000"
        assert advisory_cycle_key(episode) == key
        # 真的延长之后（planned_end 变了）→ 这是**新** cycle
        episode.planned_end_at += 600
        assert advisory_cycle_key(episode) != key

    def test_attempted_guard_is_per_key(self) -> None:
        advisor = advisor_with(ScriptedProvider())
        assert advisor.has_attempted("activity:A:1") is False
        advisor.mark_attempted("activity:A:1")
        assert advisor.has_attempted("activity:A:1") is True
        assert advisor.has_attempted("activity:A:2") is False


def test_raising_provider_is_translated_to_a_failure_code() -> None:
    import asyncio

    advisor = advisor_with(RaisingProvider(error=TimeoutError("boom")))

    async def run() -> str:
        try:
            await advisor.advise(context={}, candidates=[])
        except ModelAdvisorError as exc:
            return exc.code
        return ""

    assert asyncio.run(run()) == ModelFailureCode.PROVIDER_ERROR
