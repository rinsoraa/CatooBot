"""C1/G1: fact-constrained replies and language-variation truth."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations.minecraft.chat_bridge import MinecraftChatBridge
from app.integrations.minecraft.speech_contract import (
    ActionState,
    SpeechClaimVerdict,
    claim_for_state,
    finalize_speech,
)
from app.tasks.turn_decision import TurnDecisionKind
from tests.test_minecraft_chat_bridge import stack
from tests.turn_decision_stub import TurnDecisionStub


def test_state_matrix_never_allows_stronger_claim() -> None:
    for state in (ActionState.UNSUPPORTED, ActionState.PENDING_CONFIRMATION, ActionState.RUNNING):
        claim = claim_for_state(state)
        text, verdict = finalize_speech("好，我这就去做，马上完成。", claim)
        assert text != "好，我这就去做，马上完成。"
        assert claim.allowed_claim
        assert verdict.speech_claim_verdict in {
            SpeechClaimVerdict.REWRITTEN,
            SpeechClaimVerdict.BLOCKED_UNSUPPORTED,
            SpeechClaimVerdict.BLOCKED_UNVERIFIED,
        }
    unknown = claim_for_state(ActionState.UNKNOWN)
    text, verdict = finalize_speech("已经成功完成了。", unknown)
    assert "没确认" in text
    assert verdict.speech_claim_verdict is SpeechClaimVerdict.BLOCKED_UNVERIFIED
    succeeded = claim_for_state(ActionState.SUCCEEDED)
    assert finalize_speech("挖到了 3 个铁矿。", succeeded)[0] == "挖到了 3 个铁矿。"
    failed = claim_for_state(ActionState.FAILED)
    assert finalize_speech("没找到目标。", failed)[0] == "没找到目标。"


@pytest.mark.parametrize(
    "message",
    [
        "帮我建一座城堡",
        "去给我造个钻石屋",
        "你能不能把整个世界变成晚上",
        "把服务器重启一下",
    ],
)
@pytest.mark.asyncio
async def test_g1_01_unsupported_never_promises_execution(tmp_path: Any, message: str) -> None:
    decision = TurnDecisionStub(
        TurnDecisionKind.UNSUPPORTED, reply_text="行，我这就去做，马上完成。"
    )
    async with stack(tmp_path, decision=decision) as env:
        env.chat.bot.task_turns = object()  # type: ignore[attr-defined]
        handled = await env.chat._task_reply("示例好友", "minecraft:test:示例好友", message)  # noqa: SLF001
        assert handled.reply
        assert handled.claim is not None
        assert handled.claim.action_state is ActionState.UNSUPPORTED
        final, verdict = finalize_speech(handled.reply, handled.claim)
        assert "做不到" in final and "没有开始" in final
        assert "马上完成" not in final
        assert verdict.speech_claim_verdict is SpeechClaimVerdict.BLOCKED_UNSUPPORTED
        assert env.service.action_calls("follow_player") == []
        assert env.service.action_calls("move_to") == []


@pytest.mark.parametrize("reason", ["invalid_json", "timeout", "http_429", "http_503"])
@pytest.mark.asyncio
async def test_g1_05_invalid_provider_output_degrades_safely(tmp_path: Any, reason: str) -> None:
    decision = TurnDecisionStub(reason_code=reason)
    async with stack(tmp_path, decision=decision) as env:
        env.chat.bot.task_turns = object()  # type: ignore[attr-defined]
        handled = await env.chat._task_reply(  # noqa: SLF001
            "示例好友", "minecraft:test:示例好友", "去砍一棵树"
        )
        assert handled.reply
        assert env.service.action_calls("follow_player") == []
        assert env.service.action_calls("dig") == []


@pytest.mark.asyncio
async def test_g1_10_varied_wording_keeps_same_fact_constraint(tmp_path: Any) -> None:
    texts = []
    decision = TurnDecisionStub(TurnDecisionKind.UNSUPPORTED, reply_text="我这就开始。")
    async with stack(tmp_path, decision=decision) as env:
        env.chat.bot.task_turns = object()  # type: ignore[attr-defined]
        for message in ("你能飞吗？", "飞起来给我看看", "带我飞到天上"):
            handled = await env.chat._task_reply(  # noqa: SLF001
                "示例好友", "minecraft:test:示例好友", message
            )
            assert handled.claim is not None
            texts.append(finalize_speech(handled.reply, handled.claim)[0])
    assert len(set(texts)) == 1
    assert all("做不到" in text for text in texts)


def test_g1_04_inventory_reply_matches_real_receipt() -> None:
    result = SimpleNamespace(
        data={
            "items": [{"name": "minecraft:iron_ore", "count": 3}],
            "held_item": {"name": "minecraft:stone_pickaxe"},
        },
        summary="",
    )
    text = MinecraftChatBridge._observed_text("minecraft_inventory", result)
    assert "1 样" in text
    assert "minecraft:stone_pickaxe" in text
    empty = MinecraftChatBridge._observed_text(
        "minecraft_inventory", SimpleNamespace(data={"items": []}, summary="")
    )
    assert "空的" in empty
