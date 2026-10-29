"""VNX-1 P0 full-chain isolation: ChatBridge -> planner/service -> TaskRuntime -> AgentBridge."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest

from app.integrations.minecraft.identity_context import (
    IdentityChannel,
    IdentityContext,
    IdentityResolution,
)
from app.integrations.minecraft.task_adapter import MinecraftTaskInvoker
from app.tasks.agent_plan import PlanTarget
from app.tasks.agent_plan_store import InMemoryAgentPlanStore
from app.tasks.agent_planner import BoundedAgentPlanner
from app.tasks.agent_service import AgentPlanService
from app.tasks.runtime import TaskAuthorizationError, TaskConfig, TaskRuntime
from app.tasks.store import InMemoryTaskStore
from app.tasks.turn import TaskTurnHandler
from app.tasks.turn_decision import TurnDecisionKind
from tests.test_minecraft_chat_bridge import stack
from tests.test_phase8c_bounded_orchestration import FakeIdentityResolver
from tests.test_task_turn import FakeConfirmations
from tests.turn_decision_stub import TurnDecisionStub


class BarrierPlanner:
    def __init__(self, planner: Any, barriers: dict[str, tuple[Any, Any]]) -> None:
        self._planner = planner
        self._barriers = barriers

    async def plan(self, objective: str, **kwargs: Any) -> Any:
        target = kwargs.get("target")
        key = str(getattr(target, "player_uuid", "") or "")
        barrier = self._barriers.get(key)
        if barrier is not None:
            arrived, release = barrier
            arrived.set()
            await release.wait()
        return await self._planner.plan(objective, **kwargs)


class PerUserIdentityResolver:
    async def resolve(self, **kwargs: Any) -> Any:
        username = str(kwargs.get("user_name") or kwargs.get("user_id") or "unknown")
        return IdentityContext(
            resolution=IdentityResolution.VERIFIED,
            channel=IdentityChannel.MINECRAFT_CHAT,
            person_ref=f"qq:{username}",
            platform="qq",
            platform_user_id=f"qq-{username}",
            server_id="mc-test-server",
            username=username,
            relationship_key=f"rel-{username}",
        )


class PerUserProposals:
    async def resolve_target(
        self, *, platform: str, user_id: str, server_id: str, **_: Any
    ) -> PlanTarget:
        username = str(user_id).removeprefix("qq-")
        return PlanTarget(
            status="VERIFIED",
            server_id=str(server_id),
            player_uuid=f"target-{username}",
            player_name="示例好友",
            reason="verified_link",
        )


class VerifiedProposals:
    async def resolve_target(self, **kwargs: Any) -> PlanTarget:
        return PlanTarget(
            status="VERIFIED",
            server_id="mc-test-server",
            player_uuid="11111111-2222-3333-4444-555555555555",
            player_name="示例好友",
            reason="verified_link",
        )


def _runtime_with(invoke: Any) -> TaskRuntime:
    return TaskRuntime(
        store=InMemoryTaskStore(),
        invoke=invoke,
        confirmations=FakeConfirmations(),
        config=TaskConfig(),
        risk_of=lambda tool: {
            "minecraft_follow_player": "LOW",
            "minecraft_move_to": "LOW",
            "minecraft_dig": "MEDIUM",
            "minecraft_inventory": "SAFE",
        }.get(tool, "SAFE"),
        is_registered=lambda tool: (
            tool
            in {
                "minecraft_follow_player",
                "minecraft_move_to",
                "minecraft_dig",
                "minecraft_inventory",
            }
        ),
        schema_of=lambda tool: {"type": "object"},
        validate_arguments=lambda schema, arguments: [],
    )


async def _rig(
    tmp_path: Path,
    *,
    trusted: list[str],
    bindings: dict[str, dict[str, Any]] | None = None,
    per_user: bool = False,
    plan_barriers: dict[str, tuple[Any, Any]] | None = None,
    same_target: bool = False,
) -> Any:
    env = await stack(tmp_path, "好。", trusted=trusted).__aenter__()
    runtime = _runtime_with(lambda *a, **k: None)
    invoker = MinecraftTaskInvoker(env.bridge, task_reader=runtime.get)
    # Runtime 的 invoke 在构造时已绑定；用可替换闭包保证 invoker 可用。
    runtime._invoke = invoker
    if bindings is None:
        bindings = {
            "示例好友": {
                "actor_session_ref": "ps_current",
                "player_uuid": "uuid-current",
                "server_scope": "host:25565",
                "identity_assurance": "server_observed_uuid",
                "captured_at": 1.0,
            }
        }
    env.service.player_session_ref = lambda username: str(
        (bindings.get(username) or {}).get("actor_session_ref") or ""
    )
    env.service.player_uuid = lambda username: str(
        (bindings.get(username) or {}).get("player_uuid") or ""
    )
    env.service.server_scope = lambda: "host:25565"
    env.service.player_session_binding = lambda username: dict(bindings.get(username) or {}) or None
    env.chat.bot.minecraft = env.service
    env.bridge.set_task_authorizer(runtime.authorize_step)
    handler = TaskTurnHandler(runtime, observe=lambda *a, **k: None)
    env.chat.bot.task_turns = handler
    if per_user:
        env.chat.bot.proposals = VerifiedProposals() if same_target else PerUserProposals()
        env.chat.bot.character.identity_resolver = PerUserIdentityResolver()
    else:
        env.chat.bot.proposals = VerifiedProposals()
        env.chat.bot.character.identity_resolver = FakeIdentityResolver(IdentityResolution.VERIFIED)
    planner = BoundedAgentPlanner(allow_low=True, allow_medium=False)
    if plan_barriers is not None:
        planner = BarrierPlanner(planner, plan_barriers)
    service = AgentPlanService(
        store=InMemoryAgentPlanStore(),
        planner=planner,
        task_runtime=runtime,
        observe=None,
        character_id="罐头@test",
        plan_ttl_seconds=600.0,
        replan_budget=2,
    )
    env.chat.bot.agent_plans = service
    env.chat.turn_decisions = TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL, task_goal="跟着我")
    return env, runtime


async def test_full_chain_untrusted_follow_is_not_dispatched() -> None:
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=[])
        try:
            await env.say("Stranger", "跟着我")
            # The plan candidate may exist, but no world action may be dispatched.
            assert env.service.action_calls("follow_player") == []
            # A confirmation turn must not turn the untrusted candidate into a dispatch.
            await env.say("Stranger", "确认")
            assert env.service.action_calls("follow_player") == []
            assert env.service.action_calls("move_to") == []
        finally:
            await env.__aexit__(None, None, None)


async def test_full_chain_trusted_follow_dispatches_after_confirmation() -> None:
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=["示例好友"])
        try:
            await env.say("示例好友", "跟着我")
            assert env.service.action_calls("follow_player") == []
            await env.say("示例好友", "确认")
            # The real TaskRuntime→AgentBridge boundary must reach the fake service once.
            calls = env.service.action_calls("follow_player")
            assert len(calls) == 1, (calls, env.service.calls)
        finally:
            await env.__aexit__(None, None, None)


async def test_full_chain_trusted_revoked_after_plan_blocks_dispatch() -> None:
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=["示例好友"])
        try:
            await env.say("示例好友", "跟着我")
            env.service.config.agent.trusted_players = []
            await env.say("示例好友", "确认")
            assert env.service.action_calls("follow_player") == []
        finally:
            await env.__aexit__(None, None, None)


async def test_full_chain_reconnect_blocks_old_session_dispatch() -> None:
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=["示例好友"])
        try:
            await env.say("示例好友", "跟着我")
            env.service.player_session_ref = lambda _username: "ps_new"
            env.service.player_session_ref_at = lambda _username, _ts: "ps_old"
            await env.say("示例好友", "确认")
            assert env.service.action_calls("follow_player") == []
        finally:
            await env.__aexit__(None, None, None)


async def test_full_chain_wrong_owner_confirmation_has_zero_write() -> None:
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=["示例好友"])
        try:
            await env.say("示例好友", "跟着我")
            await env.say("Other", "确认")
            assert env.service.action_calls("follow_player") == []
        finally:
            await env.__aexit__(None, None, None)


async def test_full_chain_stop_before_confirmation_has_zero_write() -> None:
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=["示例好友"])
        try:
            await env.say("示例好友", "跟着我")
            await env.say("示例好友", "取消任务")
            await env.say("示例好友", "确认")
            assert env.service.action_calls("follow_player") == []
        finally:
            await env.__aexit__(None, None, None)


async def test_full_chain_uuid_rotation_does_not_inherit_old_session() -> None:
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=["示例好友"])
        try:
            await env.say("示例好友", "跟着我")
            env.service.player_session_ref = lambda _username: "ps_same_name_new_uuid"
            env.service.player_session_ref_at = lambda _username, _ts: "ps_old_uuid"
            await env.say("示例好友", "确认")
            assert env.service.action_calls("follow_player") == []
        finally:
            await env.__aexit__(None, None, None)


async def test_full_chain_legacy_record_is_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=["示例好友"])
        try:
            await env.say("示例好友", "跟着我")
            record = await runtime.current("minecraft:127.0.0.1:25565:示例好友")
            assert record is not None
            record.actor_verified = False
            await runtime._save(record)
            await env.say("示例好友", "确认")
            assert env.service.action_calls("follow_player") == []
        finally:
            await env.__aexit__(None, None, None)


async def test_e2_09_two_players_concurrent_full_chain_authorization() -> None:
    bindings = {
        "PlayerA": {
            "actor_session_ref": "ps-A",
            "player_uuid": "uuid-A",
            "server_scope": "host:25565",
            "identity_assurance": "server_observed_uuid",
            "captured_at": 1.0,
        },
        "PlayerB": {
            "actor_session_ref": "ps-B",
            "player_uuid": "uuid-B",
            "server_scope": "host:25565",
            "identity_assurance": "server_observed_uuid",
            "captured_at": 2.0,
        },
    }
    a_arrived = asyncio.Event()
    b_arrived = asyncio.Event()
    a_release = asyncio.Event()
    b_release = asyncio.Event()
    barriers = {
        "target-PlayerA": (a_arrived, a_release),
        "target-PlayerB": (b_arrived, b_release),
    }
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(
            Path(d),
            trusted=["PlayerA", "PlayerB"],
            bindings=bindings,
            per_user=True,
            plan_barriers=barriers,
        )
        try:
            ta = asyncio.create_task(env.say("PlayerA", "跟着我"))
            tb = asyncio.create_task(env.say("PlayerB", "跟着我"))
            await asyncio.wait_for(a_arrived.wait(), timeout=5)
            await asyncio.wait_for(b_arrived.wait(), timeout=5)
            b_release.set()
            await asyncio.wait_for(tb, timeout=5)
            a_release.set()
            await asyncio.wait_for(ta, timeout=5)
            sa = "minecraft:127.0.0.1:25565:PlayerA"
            sb = "minecraft:127.0.0.1:25565:PlayerB"
            a = await runtime.current(sa)
            b = await runtime.current(sb)
            assert a is not None and b is not None
            assert a.task_id != b.task_id
            assert a.actor_session_ref == "ps-A" and a.actor_player_uuid == "uuid-A"
            assert b.actor_session_ref == "ps-B" and b.actor_player_uuid == "uuid-B"
            with pytest.raises(TaskAuthorizationError):
                await runtime.confirm_and_start(
                    a.task_id, user_id="PlayerB", session_id=sb, origin="user"
                )
            env.service.config.agent.trusted_players = ["PlayerB"]
            await env.say("PlayerA", "确认")
            assert env.service.action_calls("follow_player") == []
            await env.say("PlayerB", "确认")
            assert len(env.service.action_calls("follow_player")) == 1
        finally:
            await env.__aexit__(None, None, None)


async def test_e2_09d_a_legal_dispatch() -> None:
    bindings = {
        "PlayerA": {
            "actor_session_ref": "ps-A",
            "player_uuid": "uuid-A",
            "server_scope": "host:25565",
            "identity_assurance": "server_observed_uuid",
            "captured_at": 1.0,
        }
    }
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=["PlayerA"], bindings=bindings, per_user=True)
        try:
            await env.say("PlayerA", "跟着我")
            record = await runtime.current("minecraft:127.0.0.1:25565:PlayerA")
            assert record is not None and record.actor_player_uuid == "uuid-A"
            assert env.service.action_calls("follow_player") == []
            await env.say("PlayerA", "确认")
            assert len(env.service.action_calls("follow_player")) == 1
        finally:
            await env.__aexit__(None, None, None)


async def test_e2_09d_b_legal_dispatch() -> None:
    bindings = {
        "PlayerB": {
            "actor_session_ref": "ps-B",
            "player_uuid": "uuid-B",
            "server_scope": "host:25565",
            "identity_assurance": "server_observed_uuid",
            "captured_at": 2.0,
        }
    }
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(Path(d), trusted=["PlayerB"], bindings=bindings, per_user=True)
        try:
            await env.say("PlayerB", "跟着我")
            record = await runtime.current("minecraft:127.0.0.1:25565:PlayerB")
            assert record is not None and record.actor_player_uuid == "uuid-B"
            assert env.service.action_calls("follow_player") == []
            await env.say("PlayerB", "确认")
            assert len(env.service.action_calls("follow_player")) == 1
        finally:
            await env.__aexit__(None, None, None)


async def test_e2_09_same_target_cross_user_dedup_is_owner_safe() -> None:
    bindings = {
        "PlayerA": {
            "actor_session_ref": "ps-A",
            "player_uuid": "uuid-A",
            "server_scope": "host:25565",
            "identity_assurance": "server_observed_uuid",
            "captured_at": 1.0,
        },
        "PlayerB": {
            "actor_session_ref": "ps-B",
            "player_uuid": "uuid-B",
            "server_scope": "host:25565",
            "identity_assurance": "server_observed_uuid",
            "captured_at": 2.0,
        },
    }
    with tempfile.TemporaryDirectory() as d:
        env, runtime = await _rig(
            Path(d),
            trusted=["PlayerA", "PlayerB"],
            bindings=bindings,
            per_user=True,
            same_target=True,
        )
        try:
            await env.say("PlayerA", "跟着我")
            await env.say("PlayerB", "跟着我")
            sa = "minecraft:127.0.0.1:25565:PlayerA"
            sb = "minecraft:127.0.0.1:25565:PlayerB"
            a = await runtime.current(sa)
            b = await runtime.current(sb)
            assert a is not None
            if b is not None:
                assert b.task_id != a.task_id
                assert b.user_id == "PlayerB"
                assert b.actor_player_uuid == "uuid-B"
            with pytest.raises(TaskAuthorizationError):
                await runtime.confirm_and_start(
                    a.task_id, user_id="PlayerB", session_id=sb, origin="user"
                )
            assert env.service.action_calls("follow_player") == []
        finally:
            await env.__aexit__(None, None, None)
