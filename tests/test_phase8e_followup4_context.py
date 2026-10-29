"""Phase 8E Follow-up 4：决策上下文接线与可见性（任务书 §四 / §六）。

证明两件事：
  1. 上下文**真的进入**决策请求（不是「接收后忽略」）；
  2. 每个来源的状态可区分（存在/缺失/失败/过期/不可用），绝不伪装成确定的空结果。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.tasks.turn_context import (
    RENDER_BUDGET,
    WORLD_FRESH_SECONDS,
    ContextState,
    build_turn_context,
)
from tests.test_minecraft_chat_bridge import stack
from tests.turn_decision_stub import TurnDecisionStub


def _fact_states(context: Any) -> dict[str, str]:
    return {fact.source: str(fact.state) for fact in context.facts}


def _fact(context: Any, source: str) -> Any:
    for fact in context.facts:
        if fact.source == source:
            return fact
    raise AssertionError(f"上下文中缺少来源：{source}")


def _handler_stub() -> Any:
    """最小任务入口替身：不接管、没有活动任务（决策路径照常走）。"""

    async def handle(**_: Any) -> Any:
        return SimpleNamespace(handled=False, reply="", task_id="", action="", state="")

    return SimpleNamespace(handle=handle, detector=None, current_task=None)


def _service(**view: Any) -> Any:
    """最小 service 替身：只提供只读 world_view()。"""

    payload = {"available": True, "online": True, "observed_at": time.time(), **view}
    return SimpleNamespace(world_view=lambda: payload)


def _character(*, memory: Any = None, resolver: Any = None) -> Any:
    return SimpleNamespace(minecraft_memory=memory, identity_resolver=resolver, tools=None)


# ------------------------------------------------------------ §六 上下文可见性


@pytest.mark.asyncio
async def test_memory_present_absent_and_failed_are_distinguishable() -> None:
    """有相关记忆 / 没有相关记忆 / 读取失败 必须是三种不同状态（§3.2）。"""

    class Memory:
        def __init__(self, block: str = "", error: Exception | None = None) -> None:
            self.block = block
            self.error = error

        async def context_block(self, *, text: str = "", **_: Any) -> str:
            if self.error is not None:
                raise self.error
            return self.block

    present = await build_turn_context(
        service=_service(),
        character=_character(memory=Memory("罐头记得昨天在这里砍过树")),
        handler=None,
        session_id="s",
        username="IdWood8D",
        message="砍树",
        recent=(),
    )
    assert _fact(present, "memory").state == ContextState.OK
    assert "砍过树" in present.render(), "相关记忆必须真的进入上下文"

    absent = await build_turn_context(
        service=_service(),
        character=_character(memory=Memory("")),
        handler=None,
        session_id="s",
        username="u",
        message="砍树",
        recent=(),
    )
    assert _fact(absent, "memory").state == ContextState.MISSING
    assert _fact(absent, "memory").reason == "no_relevant_memory"

    failed = await build_turn_context(
        service=_service(),
        character=_character(memory=Memory(error=RuntimeError("db down"))),
        handler=None,
        session_id="s",
        username="u",
        message="砍树",
        recent=(),
    )
    assert _fact(failed, "memory").state == ContextState.FAILED
    assert _fact(failed, "memory").reason == "RuntimeError"
    assert _fact(failed, "memory").state != _fact(absent, "memory").state


@pytest.mark.asyncio
async def test_identity_only_counts_when_verified() -> None:
    class Resolver:
        def __init__(self, resolution: str) -> None:
            self.resolution = resolution

        async def resolve(self, **_: Any) -> Any:
            return SimpleNamespace(
                resolution=SimpleNamespace(value=self.resolution),
                channel=SimpleNamespace(value="minecraft_chat"),
                relationship_key="qq:10001" if self.resolution == "VERIFIED" else "",
            )

    verified = await build_turn_context(
        service=_service(),
        character=_character(resolver=Resolver("VERIFIED")),
        handler=None,
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    assert _fact(verified, "identity").state == ContextState.OK
    assert "relationship_key=present" in _fact(verified, "identity").summary

    unbound = await build_turn_context(
        service=_service(),
        character=_character(resolver=Resolver("UNBOUND")),
        handler=None,
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    assert _fact(unbound, "identity").state == ContextState.MISSING
    assert _fact(unbound, "identity").reason == "not_verified"


@pytest.mark.asyncio
async def test_world_freshness_is_visible_and_stale_is_not_current_fact() -> None:
    fresh = await build_turn_context(
        service=_service(dimension="overworld", position={"x": 1, "y": 64, "z": 2}),
        character=_character(),
        handler=None,
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    assert _fact(fresh, "world").state == ContextState.OK
    assert "overworld" in _fact(fresh, "world").summary

    old = time.time() - (WORLD_FRESH_SECONDS + 5)
    stale = await build_turn_context(
        service=_service(
            observed_at=old, dimension="overworld", position={"x": 1, "y": 64, "z": 2}
        ),
        character=_character(),
        handler=None,
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    assert _fact(stale, "world").state == ContextState.STALE
    assert _fact(stale, "world").reason == "observation_expired"

    offline = await build_turn_context(
        service=_service(available=False, reason="perception_off"),
        character=_character(),
        handler=None,
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    assert _fact(offline, "world").state == ContextState.UNAVAILABLE
    assert _fact(offline, "world").reason == "perception_off"


@pytest.mark.asyncio
async def test_inventory_failure_is_never_reported_as_empty_backpack() -> None:
    """§3.2：拿不到背包快照必须是 MISSING/no_inventory_source，不得伪装成「空背包」。"""

    missing = await build_turn_context(
        service=_service(),
        character=_character(),
        handler=None,
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    fact = _fact(missing, "inventory")
    assert fact.state == ContextState.MISSING
    assert fact.reason == "no_inventory_source"
    assert "0" not in fact.summary, "缺失不得渲染成 0 个物品"

    present = await build_turn_context(
        service=_service(inventory={"items": [{"name": "oak_log"}], "held_item": {"name": "axe"}}),
        character=_character(),
        handler=None,
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    good = _fact(present, "inventory")
    assert good.state == ContextState.OK
    assert "slots=1" in good.summary and "axe" in good.summary


@pytest.mark.asyncio
async def test_active_task_and_capability_directories_are_visible() -> None:
    class Handler:
        async def current_task(self, _session: str) -> Any:
            return SimpleNamespace(
                state=SimpleNamespace(value="RUNNING", terminal=False),
                current_step=1,
                plan=SimpleNamespace(steps=[1, 2, 3]),
            )

    class Registry:
        def maybe_get(self, name: str) -> Any:
            if name in ("minecraft_world", "minecraft_look_at"):
                return SimpleNamespace(enabled=True)
            if name == "minecraft_stop":
                return SimpleNamespace(enabled=False)
            return None

    character = _character()
    character.tools = SimpleNamespace(registry=Registry(), enabled=True)
    context = await build_turn_context(
        service=_service(),
        character=character,
        handler=Handler(),
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    task = _fact(context, "active_task")
    assert str(task.state) == str(ContextState.OK), f"active_task 状态异常：{task.state!r}"
    assert "step=2/3" in task.summary, f"步骤进度缺失：{task.summary!r}"
    capabilities = _fact(context, "capabilities")
    assert capabilities.state == ContextState.OK
    assert "minecraft_world" in capabilities.summary
    assert "unsupported" in capabilities.summary, "未开放能力必须如实列出"


@pytest.mark.asyncio
async def test_missing_task_is_not_guessed() -> None:
    class Handler:
        async def current_task(self, _session: str) -> Any:
            return None

    context = await build_turn_context(
        service=_service(),
        character=_character(),
        handler=Handler(),
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    task = _fact(context, "active_task")
    assert task.state == ContextState.OK
    assert task.summary == "no_active_task", "没有活动任务时不得猜任务进度"
    absent = _fact(
        await build_turn_context(
            service=_service(),
            character=_character(),
            handler=None,
            session_id="s",
            username="u",
            message="m",
            recent=(),
        ),
        "active_task",
    )
    assert absent.state == ContextState.MISSING
    assert absent.reason == "task_entry_absent"


@pytest.mark.asyncio
async def test_render_is_bounded_and_has_no_secrets() -> None:
    long_memory = "秘密记忆" * 400

    class Memory:
        async def context_block(self, *, text: str = "", **_: Any) -> str:
            return long_memory

    context = await build_turn_context(
        service=_service(dimension="overworld", position={"x": 1, "y": 64, "z": 2}),
        character=_character(memory=Memory()),
        handler=None,
        session_id="s",
        username="u",
        message="m",
        recent=(),
    )
    assert len(context.render()) <= RENDER_BUDGET
    fields = str(context.log_fields())
    assert "秘密记忆" not in fields, "诊断字段不得包含记忆正文"
    assert fields and "memory" in fields


# ------------------------------------------------------------ §四 上下文真的进入请求


@pytest.mark.asyncio
async def test_context_is_actually_passed_into_the_decision_request(tmp_path: Any) -> None:
    """§4.2/§四：上下文必须进入 decide(task_context=...)，不是接收后忽略。"""

    seen: list[str] = []

    class Spy(TurnDecisionStub):
        async def decide(self, *, message: str, recent_context: Any = (), task_context: str = ""):
            seen.append(task_context)
            return await super().decide(
                message=message, recent_context=recent_context, task_context=task_context
            )

    async with stack(tmp_path, "好的") as env:
        env.chat.turn_decisions = Spy()
        env.chat.bot.task_turns = _handler_stub()
        await env.say("IdWood8D", "今天天气不错呀")
    assert seen, "决策器必须被调用"
    assert "world:" in seen[0] or "capabilities:" in seen[0], f"上下文没进请求：{seen[0]!r}"


@pytest.mark.asyncio
async def test_context_failure_never_breaks_the_decision(tmp_path: Any, monkeypatch: Any) -> None:
    """§四.3：上下文组装失败只减少上下文，不能让回合失败或产生副作用。"""

    import app.integrations.minecraft.chat_bridge as bridge_module

    async def boom(**_: Any) -> Any:
        raise RuntimeError("context build failed")

    monkeypatch.setattr(bridge_module, "build_turn_context", boom)
    async with stack(tmp_path, "我在") as env:
        env.chat.bot.task_turns = _handler_stub()
        await env.say("IdWood8D", "今天天气不错呀")
        assert env.tools.executor.traces == [], "上下文失败不得引发工具执行"
