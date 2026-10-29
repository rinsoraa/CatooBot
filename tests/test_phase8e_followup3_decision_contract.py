"""Phase 8E Follow-up 3 §9.1：统一回合决策契约的单元测试（确定性、不依赖真实 LLM）。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.tasks.turn_decision import (
    ACTION_CAPABILITIES,
    AUDITED_CAPABILITIES,
    READ_ONLY_CAPABILITIES,
    REASON_EMPTY,
    REASON_ENGINE_MISSING,
    REASON_INVALID_JSON,
    REASON_MODEL_ERROR,
    REASON_SCHEMA_INVALID,
    REASON_TIMEOUT,
    TurnDecision,
    TurnDecisionInterpreter,
    TurnDecisionKind,
    allowed_capabilities,
)


class ScriptedEngine:
    """按顺序吐出预设内容；用于确定性驱动解析路径。"""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.requests: list[Any] = []

    async def chat(self, request: Any) -> Any:
        self.calls += 1
        self.requests.append(request)
        item = self.replies.pop(0) if self.replies else ""
        if isinstance(item, Exception):
            raise item
        if item == "__timeout__":
            await asyncio.sleep(5)
        return SimpleNamespace(content=item)


def payload(**overrides: Any) -> str:
    data: dict[str, Any] = {"kind": "chat", "reply_text": "嗯嗯。"}
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


async def decide(replies: Any, *, message: str = "你好呀", **kwargs: Any) -> Any:
    engine = ScriptedEngine(*replies)
    interpreter = TurnDecisionInterpreter(engine, timeout=0.2, **kwargs)
    out = await interpreter.decide(message=message)
    return out, engine


# ------------------------------------------------------------ 合法类别矩阵


@pytest.mark.parametrize(
    ("overrides", "kind"),
    [
        ({"kind": "chat", "reply_text": "今天挺好的。"}, TurnDecisionKind.CHAT),
        ({"kind": "narration", "reply_text": "那棵树确实很高。"}, TurnDecisionKind.NARRATION),
        ({"kind": "query", "capability_id": "minecraft_world"}, TurnDecisionKind.QUERY),
        ({"kind": "query", "capability_id": "minecraft_inventory"}, TurnDecisionKind.QUERY),
        (
            {"kind": "atomic_action", "capability_id": "minecraft_look_at", "arguments": {"x": 1}},
            TurnDecisionKind.ATOMIC_ACTION,
        ),
        (
            {"kind": "atomic_action", "capability_id": "minecraft_stop"},
            TurnDecisionKind.ATOMIC_ACTION,
        ),
        ({"kind": "task_proposal", "task_goal": "砍一根橡木原木"}, TurnDecisionKind.TASK_PROPOSAL),
        ({"kind": "clarify", "clarification_question": "你想砍哪种树？"}, TurnDecisionKind.CLARIFY),
        ({"kind": "unsupported", "reply_text": "我还不会建城堡。"}, TurnDecisionKind.UNSUPPORTED),
        ({"kind": "cancel", "reply_text": "好，取消。"}, TurnDecisionKind.CANCEL),
        ({"kind": "stop", "reply_text": "停下。"}, TurnDecisionKind.STOP),
    ],
)
async def test_every_legal_kind_parses(overrides: dict[str, Any], kind: TurnDecisionKind) -> None:
    out, engine = await decide([payload(**overrides)])
    assert out.ok, out.reason_code
    assert out.decision.kind is kind
    assert out.attempts == 1
    assert engine.calls == 1
    assert out.latency_ms >= 0


# ------------------------------------------------------------ 严格失败路径


async def test_invalid_json_is_rejected() -> None:
    out, _ = await decide(["这不是 JSON"])
    assert not out.ok
    assert out.reason_code == REASON_INVALID_JSON


async def test_empty_response_is_rejected() -> None:
    out, _ = await decide([""])
    assert not out.ok
    assert out.reason_code == REASON_INVALID_JSON


async def test_fenced_json_is_accepted() -> None:
    body = "```json" + chr(10) + payload(reply_text="围栏里的回复。") + chr(10) + "```"
    out, _ = await decide([body])
    assert out.ok, out.reason_code
    assert out.decision.reply_text == "围栏里的回复。"


async def test_unknown_kind_is_schema_invalid() -> None:
    out, _ = await decide([payload(kind="destroy_everything")])
    assert not out.ok
    assert out.reason_code == REASON_SCHEMA_INVALID
    assert "kind" in out.detail.get("fields", [])


async def test_unknown_capability_is_rejected() -> None:
    out, _ = await decide([payload(kind="atomic_action", capability_id="minecraft_place_block")])
    assert not out.ok
    assert out.reason_code == REASON_SCHEMA_INVALID


async def test_read_only_capability_cannot_be_used_as_atomic_action() -> None:
    out, _ = await decide([payload(kind="atomic_action", capability_id="minecraft_find_blocks")])
    assert not out.ok
    assert out.reason_code == REASON_SCHEMA_INVALID


async def test_action_capability_cannot_be_used_as_query() -> None:
    out, _ = await decide([payload(kind="query", capability_id="minecraft_stop")])
    assert not out.ok
    assert out.reason_code == REASON_SCHEMA_INVALID


async def test_missing_required_field_per_kind() -> None:
    for overrides in (
        {"kind": "atomic_action"},
        {"kind": "query"},
        {"kind": "task_proposal"},
        {"kind": "clarify"},
    ):
        out, _ = await decide([payload(**overrides)])
        assert not out.ok, overrides
        assert out.reason_code == REASON_SCHEMA_INVALID


async def test_wrong_argument_types_are_rejected() -> None:
    for arguments in ([1, 2], "not-an-object", {"x": {"nested": 1}}, {"x": [{"a": 1}]}):
        raw = json.dumps(
            {
                "kind": "atomic_action",
                "capability_id": "minecraft_look_at",
                "arguments": arguments,
            },
            ensure_ascii=False,
        )
        out, _ = await decide([raw])
        assert not out.ok, arguments
        assert out.reason_code == REASON_SCHEMA_INVALID


async def test_bad_argument_key_is_rejected() -> None:
    out, _ = await decide(
        [
            payload(
                kind="atomic_action", capability_id="minecraft_look_at", arguments={"危险 key": 1}
            )
        ]
    )
    assert not out.ok
    assert out.reason_code == REASON_SCHEMA_INVALID


async def test_model_cannot_grant_itself_permissions() -> None:
    for field in ("authorized", "action_id", "risk", "allow_medium", "trusted", "success"):
        out, _ = await decide([payload(**{field: True})])
        assert not out.ok, field
        assert out.reason_code == REASON_SCHEMA_INVALID


async def test_tool_call_style_json_is_not_executed() -> None:
    raw = json.dumps({"tool": "minecraft_dig", "arguments": {"x": 1, "y": 2, "z": 3}})
    out, _ = await decide([raw])
    assert not out.ok
    assert out.reason_code == REASON_SCHEMA_INVALID


async def test_timeout_is_reported_and_bounded() -> None:
    out, engine = await decide(["__timeout__"], max_attempts=2)
    assert not out.ok
    assert out.reason_code == REASON_TIMEOUT
    assert out.attempts == 2
    assert engine.calls == 2


async def test_model_error_does_not_leak_exception_text() -> None:
    out, _ = await decide([RuntimeError("SENSITIVE_PROVIDER_BODY_8EFU3")])
    assert not out.ok
    assert out.reason_code == REASON_MODEL_ERROR
    assert out.detail.get("error_type") == "RuntimeError"
    assert "SENSITIVE_PROVIDER_BODY_8EFU3" not in json.dumps(out.detail)


async def test_engine_missing_and_empty_message() -> None:
    interpreter = TurnDecisionInterpreter(None)
    out = await interpreter.decide(message="你好")
    assert not out.ok and out.reason_code == REASON_ENGINE_MISSING
    interpreter2 = TurnDecisionInterpreter(ScriptedEngine(payload()))
    out2 = await interpreter2.decide(message="   ")
    assert not out2.ok and out2.reason_code == REASON_EMPTY


async def test_bounded_retry_then_success() -> None:
    out, engine = await decide(["不是 JSON", payload(reply_text="重试成功。")])
    assert out.ok, out.reason_code
    assert out.attempts == 2
    assert engine.calls == 2
    assert out.decision.reply_text == "重试成功。"


async def test_bounded_retry_gives_up_safely() -> None:
    out, engine = await decide(["坏", "也坏", "还是坏"], max_attempts=2)
    assert not out.ok
    assert out.attempts == 2
    assert engine.calls == 2
    assert out.reason_code == REASON_INVALID_JSON


async def test_schema_failure_records_field_paths_only() -> None:
    out, _ = await decide([payload(kind="clarify")])
    assert not out.ok
    assert out.detail.get("fields")
    assert all("SENSITIVE" not in item for item in out.detail.get("fields", []))


# ------------------------------------------------------------ 清洗与上下文边界


def test_text_cleaning_truncates_and_strips_controls() -> None:
    decision = TurnDecision(
        kind=TurnDecisionKind.CHAT,
        reply_text="第一行" + chr(10) + "第二行" + chr(7) + "x" * 900,
    )
    assert chr(10) not in decision.reply_text
    assert chr(7) not in decision.reply_text
    assert len(decision.reply_text) <= 400


async def test_context_is_bounded_and_message_truncated() -> None:
    engine = ScriptedEngine(payload())
    interpreter = TurnDecisionInterpreter(engine, max_context_messages=2, max_chars=20)
    await interpreter.decide(
        message="很长" * 50,
        recent_context=["一", "二", "三", "四"],
        task_context="当前没有活动任务",
    )
    sent = engine.requests[0].messages[-1].content
    assert "一" not in sent and "二" not in sent
    assert "三" in sent and "四" in sent
    assert "当前没有活动任务" in sent
    assert len(sent) < 400


def test_capability_sets_are_audited_and_disjoint() -> None:
    assert READ_ONLY_CAPABILITIES.isdisjoint(ACTION_CAPABILITIES - {"minecraft_look_at"})
    assert AUDITED_CAPABILITIES == READ_ONLY_CAPABILITIES | ACTION_CAPABILITIES
    assert "minecraft_dig" not in AUDITED_CAPABILITIES
    assert "minecraft_place_block" not in AUDITED_CAPABILITIES
    assert "minecraft_attack" not in AUDITED_CAPABILITIES
    assert allowed_capabilities() == AUDITED_CAPABILITIES


def test_decision_model_has_no_permission_fields() -> None:
    fields = set(TurnDecision.model_fields)
    assert fields == {
        "kind",
        "reply_text",
        "capability_id",
        "arguments",
        "task_goal",
        "clarification_question",
    }
