"""Phase 6D §十二-§十八/§四十八-§五十：输出契约（矩阵 D/E/F/G + 规范化）。

模型说什么不重要 —— **不合契约的一律拒绝**，绝不"宽容解析"成一条真决策。
"""

from __future__ import annotations

import pytest

from app.activity.model_advisor import (
    MAX_EXPLANATION_CHARS,
    ActivityModelAdvisor,
    ModelAdvisorError,
    ModelFailureCode,
    parse_proposal,
)
from tests.activity_model_fakes import ScriptedProvider, advisor_with, proposal_json

ALLOWED = frozenset({"gaming", "reading", "music", "sleeping"})


def parse(raw: str) -> object:
    return parse_proposal(raw, allowed=ALLOWED)


class TestInvalidJson:
    def test_d_empty_response(self) -> None:
        with pytest.raises(ModelAdvisorError) as caught:
            parse("")
        assert caught.value.code == ModelFailureCode.INVALID_JSON

    def test_d_plain_prose_is_rejected(self) -> None:
        """§十二：不许自然语言全文，只要 JSON object。"""
        with pytest.raises(ModelAdvisorError) as caught:
            parse("我觉得她应该继续玩二十分钟。")
        assert caught.value.code == ModelFailureCode.INVALID_JSON

    def test_d_array_or_number_is_rejected(self) -> None:
        for raw in ("[1, 2, 3]", "42", "true"):
            with pytest.raises(ModelAdvisorError):
                parse(raw)

    def test_d_fenced_json_is_accepted(self) -> None:
        """容错：围栏代码块仍算 JSON（沿用项目既有解析习惯）。"""
        proposal = parse('```json\n{"decision": "continue"}\n```')
        assert proposal.decision == "continue"  # type: ignore[attr-defined]

    def test_d_json_embedded_in_prose_is_accepted(self) -> None:
        proposal = parse('好的：{"decision": "continue"} 就这样')
        assert proposal.decision == "continue"  # type: ignore[attr-defined]


class TestSchemaViolation:
    def test_e_unknown_decision_enum(self) -> None:
        """§十四/§四十九：`wander` 这类不认识的值直接拒绝（也绝不新增活动）。"""
        for value in ("wander", "EXTEND_AND_DIG", "dig", "", "继续"):
            with pytest.raises(ModelAdvisorError) as caught:
                parse(proposal_json(value))
            assert caught.value.code == ModelFailureCode.SCHEMA_ERROR

    def test_e_missing_decision(self) -> None:
        with pytest.raises(ModelAdvisorError) as caught:
            parse('{"reason_code": "high_focus"}')
        assert caught.value.code == ModelFailureCode.SCHEMA_ERROR

    def test_e_extend_without_minutes(self) -> None:
        with pytest.raises(ModelAdvisorError) as caught:
            parse(proposal_json("extend"))
        assert caught.value.code == ModelFailureCode.SCHEMA_ERROR

    def test_e_minutes_without_extend(self) -> None:
        """§十四：decision != extend 时 extension_minutes 必须是 null。"""
        with pytest.raises(ModelAdvisorError) as caught:
            parse(proposal_json("continue", extension_minutes=10))
        assert caught.value.code == ModelFailureCode.SCHEMA_ERROR

    def test_e_hint_without_transition(self) -> None:
        with pytest.raises(ModelAdvisorError) as caught:
            parse(proposal_json("extend", extension_minutes=10, next_hint="reading"))
        assert caught.value.code == ModelFailureCode.SCHEMA_ERROR

    def test_g_invalid_extension_values(self) -> None:
        """§十四：extend 的分钟数必须 > 0 且是数字。"""
        for value in (0, -5, "abc", True, {}):
            with pytest.raises(ModelAdvisorError):
                parse(proposal_json("extend", extension_minutes=value))  # type: ignore[arg-type]

    def test_g_float_minutes_are_rounded_to_int(self) -> None:
        raw = '{"decision": "extend", "extension_minutes": 20.0, "reason_code": "x"}'
        proposal = parse(raw)
        assert proposal.extension_minutes == 20  # type: ignore[attr-defined]


class TestUnknownActivity:
    def test_f_hallucinated_activity_is_rejected(self) -> None:
        """§十五/§五十：活动名必须来自既有注册表，绝不动态创建。"""
        with pytest.raises(ModelAdvisorError) as caught:
            parse(proposal_json("transition", next_hint="cook_dinner"))
        assert caught.value.code == ModelFailureCode.UNKNOWN_ACTIVITY

    def test_f_freeform_instructions_are_rejected(self) -> None:
        for name in ("build a spaceship", "wander around", "do whatever"):
            with pytest.raises(ModelAdvisorError) as caught:
                parse(proposal_json("transition", next_hint=name))
            assert caught.value.code == ModelFailureCode.UNKNOWN_ACTIVITY

    def test_f_minecraft_activity_name_is_rejected(self) -> None:
        """6A §二十九/§七十五：虚拟活动绝不冒用 Minecraft 名字（模型也不行）。"""
        for name in ("minecraft", "minecraft_task", "dig", "mining"):
            with pytest.raises(ModelAdvisorError) as caught:
                parse_proposal(
                    proposal_json("transition", next_hint=name),
                    allowed=frozenset({*ALLOWED, name}),
                )
            assert caught.value.code == ModelFailureCode.UNKNOWN_ACTIVITY

    def test_f_known_names_normalized(self) -> None:
        proposal = parse(proposal_json("transition", next_hint=" Reading "))
        assert proposal.next_hint == "reading"  # type: ignore[attr-defined]


class TestCanonicalization:
    def test_case_and_whitespace_are_normalized(self) -> None:
        """§四十八：`" Extend "` → `extend`。"""
        proposal = parse(
            '{"decision": " Extend ", "extension_minutes": 15, "reason_code": " High_Focus "}'
        )
        assert proposal.decision == "extend"  # type: ignore[attr-defined]
        assert proposal.reason_code == "high_focus"  # type: ignore[attr-defined]

    def test_reason_code_falls_back_when_absurd(self) -> None:
        proposal = parse(
            '{"decision": "continue", "reason_code": "'
            + "x" * 200
            + '", "state_explanation": "hi"}'
        )
        assert proposal.reason_code == "model"  # type: ignore[attr-defined]

    def test_explanation_is_clamped_and_never_chain_of_thought(self) -> None:
        """§十七：解释 ≤160 字符，而且只存最终解释（没有思维链字段）。"""
        long = "很" * 400
        proposal = parse(proposal_json("continue", explanation=long))
        assert len(proposal.state_explanation) == MAX_EXPLANATION_CHARS  # type: ignore[attr-defined]
        payload = proposal.to_payload()  # type: ignore[attr-defined]
        for forbidden in ("reasoning", "chain_of_thought", "thinking", "prompt"):
            assert forbidden not in payload

    def test_proposal_only_has_the_five_fields(self) -> None:
        proposal = parse(proposal_json("continue"))
        assert set(proposal.to_payload()) == {  # type: ignore[attr-defined]
            "decision",
            "extension_minutes",
            "next_hint",
            "reason_code",
            "state_explanation",
        }


class TestAdvisorLevelParsing:
    async def test_advisor_surfaces_the_failure_code(self) -> None:
        advisor = advisor_with(ScriptedProvider(scripted=["not json"]))
        with pytest.raises(ModelAdvisorError) as caught:
            await advisor.advise(context={}, candidates=[])
        assert caught.value.code == ModelFailureCode.INVALID_JSON
        assert advisor.last_failure == ModelFailureCode.INVALID_JSON

    async def test_advisor_reports_latency_and_call_count(self) -> None:
        provider = ScriptedProvider(scripted=[proposal_json("continue")])
        advisor: ActivityModelAdvisor = advisor_with(provider)
        await advisor.advise(context={}, candidates=[])
        assert advisor.calls == 1
        assert advisor.last_latency_ms >= 0
        assert advisor.view()["last_failure"] == ""
