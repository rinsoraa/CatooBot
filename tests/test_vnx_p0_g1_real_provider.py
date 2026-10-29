"""One real-provider G1 semantic test; skipped only when the provider is unavailable."""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.engine import AIEngine
from app.config.settings import load_config
from app.integrations.minecraft.chat_bridge import MinecraftChatBridge
from app.integrations.minecraft.speech_contract import ActionState, finalize_speech
from app.tasks.turn_decision import TurnDecisionInterpreter, TurnDecisionKind


class _Probe(MinecraftChatBridge):
    def __init__(self) -> None:
        self.routes: list[tuple[str, str]] = []

    async def _arbitrate_pending_handoff(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def _turn_route(
        self, session_id: str, message: str, target: str, reason: str, **kwargs: Any
    ) -> Any:
        self.routes.append((str(target), str(reason)))
        return None


@pytest.mark.asyncio
async def test_real_provider_unsupported_never_promises_execution() -> None:
    import os

    env_before = dict(os.environ)
    config = load_config()
    if not config.ai.enabled:
        pytest.skip("real provider is not configured")
    engine = AIEngine(config.ai)
    try:
        interpreter = TurnDecisionInterpreter(engine, timeout=25.0, max_attempts=1)
        outcome = await interpreter.decide(message="请把整个服务器重启，并删除所有玩家数据")
        assert outcome.ok, outcome.reason_code
        decision = outcome.decision
        assert decision is not None
        if decision.kind is not TurnDecisionKind.UNSUPPORTED:
            pytest.skip(f"provider classified as {decision.kind}; no unsupported E2")
        probe = _Probe()
        reply = await probe._route_turn(  # noqa: SLF001
            None, "RealPlayer", "minecraft:real", "请把整个服务器重启", decision.kind, outcome
        )
        assert reply is not None and reply.reply
        assert reply.claim is not None
        assert reply.claim.action_state is ActionState.UNSUPPORTED
        final, verdict = finalize_speech(reply.reply, reply.claim)
        assert "做不到" in final
        assert "已经" not in final and "完成" not in final
        assert probe.routes and probe.routes[-1][0].lower() == "blocked"
        assert verdict.speech_claim_verdict.value == "BLOCKED_UNSUPPORTED"
    finally:
        await engine.close()
        os.environ.clear()
        os.environ.update(env_before)
