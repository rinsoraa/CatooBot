"""dig 的自然语言闭环与感知联动（Phase 4B §二十四/§二十五/§五十二/§六十四-§六十六）。

全链路是真的：真实 `CharacterRuntime` → 真实 `ToolRuntime` → `MinecraftActionPolicy`
→ 确认门 → 假 `MinecraftService`（记录每一次动作调用）；模型用脚本化的 MockAIProvider。

`minecraft_dig` 是**第一个真正修改世界**的动作，所以这里重点验证四件事：
① 用户第一次请求绝不执行、只产生「需要确认」；② 用户确认后才执行；
③ 参数/时效/来源不对一律不执行；④ 世界被改了以后，感知层真的看得见。
"""

from __future__ import annotations

from typing import Any

from app.character.turn import TurnOrigin
from app.integrations.minecraft.agent import (
    BRIDGE_KEY,
    INTENT_KEY,
    PLAYER_KEY,
    TURN_ORIGIN_KEY,
)
from tests.test_minecraft_agent_confirm_gate import DIG_ARGS, decide
from tests.test_minecraft_agent_tools import FakeMinecraftService
from tests.test_minecraft_chat_bridge import stack as chat_stack
from tests.test_minecraft_intent_propagation import stack as turn_stack
from tests.test_minecraft_world import make_clock, make_perception, raw_payload


def dig_calls(env: Any) -> list[dict[str, Any]]:
    return env.service.action_calls("dig")


# ------------------------------------------------ §六十四 Test 1 / Test 2


async def test_dig_requires_confirmation_on_the_first_user_turn(tmp_path: Any) -> None:
    """Test 1：用户说「把我面前这个石头挖掉」→ world → dig → 需要确认（不执行）。"""
    async with turn_stack(
        tmp_path,
        decide("minecraft_world"),
        decide("minecraft_dig", **DIG_ARGS),
        "挖掉那块石头会真的改变世界，要我挖吗？",
    ) as env:
        reply = await env.runtime.respond(
            "private:10001", 10001, "把我面前这个石头挖掉", turn_origin=TurnOrigin.USER
        )
    assert reply == "挖掉那块石头会真的改变世界，要我挖吗？"
    assert env.tool_verdicts == ["", "minecraft.confirmation_required"]
    assert dig_calls(env) == [], "第一次请求绝不执行 dig"
    pending = env.bridge.confirmations.pending()
    assert len(pending) == 1 and pending[0].tool == "minecraft_dig"
    assert pending[0].arguments_hash  # 参数指纹已记录（含 expected_block）


async def test_user_confirmation_then_dig_executes(tmp_path: Any) -> None:
    """Test 2：下一轮用户说「确认」→ 模型用相同参数再调用 → 执行。"""
    service = FakeMinecraftService()
    async with turn_stack(
        tmp_path,
        decide("minecraft_dig", **DIG_ARGS),
        "要我挖吗？",
        decide("minecraft_dig", **DIG_ARGS),
        "好，我去挖。",
        service=service,
    ) as env:
        first = await env.runtime.respond(
            "private:10001", 10001, "把面前这个石头挖掉", turn_origin=TurnOrigin.USER
        )
        assert first == "要我挖吗？"
        assert dig_calls(env) == []
        second = await env.runtime.respond(
            "private:10001", 10001, "确认", turn_origin=TurnOrigin.USER
        )
    assert second == "好，我去挖。"
    assert env.tool_verdicts == [
        "minecraft.confirmation_required",
        "",
    ]
    assert dig_calls(env) == [dict(DIG_ARGS)], "确认后才真的挖"


# ------------------------------------------------ §六十四 Test 3 / Test 4


async def test_expired_confirmation_cannot_dig(tmp_path: Any) -> None:
    """Test 3：用户确认，但上一条确认已过期 → confirmation_expired，不执行。"""
    async with turn_stack(
        tmp_path,
        decide("minecraft_dig", **DIG_ARGS),
        "要我挖吗？",
        decide("minecraft_dig", **DIG_ARGS),
        "刚才那个确认超时了，还要挖吗？",
    ) as env:
        # 先接管时钟，再让第一次请求建立 PENDING（这样 expires_at 走假时钟）
        clock = {"now": 1000.0}
        env.bridge.confirmations._clock = lambda: clock["now"]  # noqa: SLF001
        await env.runtime.respond(
            "private:10001", 10001, "挖掉那个石头", turn_origin=TurnOrigin.USER
        )
        assert env.bridge.confirmations.pending(), "先有一条待确认"
        clock["now"] += 61.0  # 让它过期
        reply = await env.runtime.respond(
            "private:10001", 10001, "确认", turn_origin=TurnOrigin.USER
        )
    assert reply == "刚才那个确认超时了，还要挖吗？"
    assert env.tool_verdicts[-1] == "minecraft.confirmation_expired"
    assert dig_calls(env) == []


async def test_model_changing_coordinates_is_a_mismatch(tmp_path: Any) -> None:
    """Test 4：用户说「挖掉刚才那个」，但模型给了不同坐标 → mismatch，不执行。"""
    async with turn_stack(
        tmp_path,
        decide("minecraft_dig", **DIG_ARGS),
        "要我挖吗？",
        decide("minecraft_dig", x=121, y=64, z=-230, expected_block="minecraft:stone"),
        "那块石头跟我确认的位置不一样，要重新确认一下。",
    ) as env:
        await env.runtime.respond(
            "private:10001", 10001, "挖掉那个石头", turn_origin=TurnOrigin.USER
        )
        reply = await env.runtime.respond(
            "private:10001", 10001, "确认", turn_origin=TurnOrigin.USER
        )
    assert reply == "那块石头跟我确认的位置不一样，要重新确认一下。"
    assert env.tool_verdicts[-1] == "minecraft.confirmation_mismatch"
    assert dig_calls(env) == []


# ------------------------------------------------ §六十四 Test 5（游戏内玩家）


async def test_untrusted_minecraft_player_cannot_dig(tmp_path: Any) -> None:
    """Test 5：游戏里的 Steve 让罐头挖 → user_not_trusted，零动作调用（但能聊天）。"""
    async with chat_stack(
        tmp_path,
        decide("minecraft_dig", **DIG_ARGS),
        "这个我不能随便挖哦。",
    ) as env:
        await env.say("Steve", "罐头，把这个挖掉")
    assert env.verdicts == ["minecraft.user_not_trusted"]
    assert dig_calls(env) == []
    assert env.chat_replies == ["这个我不能随便挖哦。"]


async def test_trusted_minecraft_player_can_start_the_dig_flow(tmp_path: Any) -> None:
    """可信玩家（空凛）：可以进入确认流程（先要确认，不直接执行）。"""
    async with chat_stack(
        tmp_path,
        decide("minecraft_world"),
        decide("minecraft_dig", **DIG_ARGS),
        "要我挖吗？",
    ) as env:
        await env.say("空凛", "把前面这个石头挖掉")
    assert env.verdicts == ["", "minecraft.confirmation_required"]
    assert dig_calls(env) == []
    assert env.chat_replies == ["要我挖吗？"]


# ------------------------------------------------ §六十五/§六十六 防护


async def test_single_turn_cannot_chain_two_blocks(tmp_path: Any) -> None:
    """§六十五：一个回合里想挖第二块 → 只会得到 mismatch/需要确认，绝不连着挖。"""
    async with turn_stack(
        tmp_path,
        decide("minecraft_dig", **DIG_ARGS),
        decide("minecraft_dig", x=127, y=64, z=-230, expected_block="minecraft:stone"),
        "一次只能挖一块，要我挖哪块？",
    ) as env:
        reply = await env.runtime.respond(
            "private:10001", 10001, "把这两个都挖了", turn_origin=TurnOrigin.USER
        )
    assert reply == "一次只能挖一块，要我挖哪块？"
    assert env.tool_verdicts == [
        "minecraft.confirmation_required",
        "minecraft.confirmation_mismatch",
    ]
    assert dig_calls(env) == []


async def test_initiative_turn_cannot_dig(tmp_path: Any) -> None:
    """§六十六：她自己想挖也不行——action_not_allowed 且不产生确认。"""
    async with turn_stack(
        tmp_path,
        decide("minecraft_dig", **DIG_ARGS),
        "（自己嘀咕了一句）",
    ) as env:
        await env.runtime.compose_initiative(
            session_id="private:10001", user_id=10001, reason="life_event"
        )
    assert env.tool_verdicts == ["minecraft.action_not_allowed"]
    assert dig_calls(env) == []
    assert env.bridge.confirmations.pending() == []


async def test_dig_tool_arguments_must_include_expected_block(tmp_path: Any) -> None:
    """§九：不许拿 unknown/瞎猜的方块名，也不许省略 expected_block（schema 直接拒）。"""
    async with turn_stack(
        tmp_path,
        decide("minecraft_dig", x=120, y=64, z=-230),  # 没有 expected_block
        "我得先看清那是块什么。",
    ) as env:
        await env.runtime.respond("private:10001", 10001, "挖掉那个", turn_origin=TurnOrigin.USER)
    assert env.tool_verdicts == ["invalid_arguments"]
    assert dig_calls(env) == []


# ------------------------------------------------ §二十四/§五十二 感知联动


async def test_world_perception_sees_the_dug_block_disappear() -> None:
    """挖掉一块之后，感知层（near diff）必须真的看到那次移除。

    这里给感知层喂「挖之前 / 挖之后」两份 raw snapshot：目标柱从 stone 变成 air（该柱消失），
    阈值设为 1（单块移除也算世界变化）。§二十四：changed_blocks >= 1。
    """
    clock, advance = make_clock()
    perception, client, _events = make_perception(clock, advance, change_block_threshold=1)

    # 第一帧：目标位置有一块石头（near 窗口内的柱）
    columns = client.payload["blocks"]["near"]["columns"]
    assert columns, "样例快照里要有近层柱子"
    target = dict(columns[0])
    target["name"] = "stone"
    target["pos"] = {"x": 10, "y": 63, "z": -5}
    columns.append(target)
    assert await perception.poll({"near"}) == []  # 首帧只建基线

    # 第二帧：那块石头没了（挖掉了）——真实链路里这正是 dig 的结果
    advance(6.0)  # 越过事件冷却
    remaining = [
        col for col in client.payload["blocks"]["near"]["columns"] if col["pos"] != target["pos"]
    ]
    client.payload = raw_payload()
    client.payload["blocks"]["near"]["columns"] = remaining
    events = await perception.poll({"near"})

    changed = [data for name, data in events if name == "minecraft.world.changed"]
    assert changed, "方块被移除必须产生 world.changed（不再是「什么都没发生」）"
    assert changed[0]["changed_blocks"] >= 1
    # 合并视图（= LLM/工具看到的 raw）里也不再有那一块
    merged = perception.cache.raw
    assert merged is not None
    assert all(
        col.pos is None or (col.pos.x, col.pos.y, col.pos.z) != (10.0, 63.0, -5.0)
        for col in merged.blocks.near.columns
    )


async def test_perception_merge_drops_the_removed_column() -> None:
    """缓存层：移除的方块不再出现在 raw 合并视图里（Phase 2.1 的移除检测服务于动作层）。"""
    clock, advance = make_clock()
    perception, client, _events = make_perception(clock, advance)
    columns = client.payload["blocks"]["near"]["columns"]
    target = dict(columns[0])
    target["pos"] = {"x": 11, "y": 63, "z": -5}
    columns.append(target)
    await perception.poll({"near"})
    first = perception.cache.raw
    assert first is not None and any(
        col.pos is not None and (col.pos.x, col.pos.y, col.pos.z) == (11.0, 63.0, -5.0)
        for col in first.blocks.near.columns
    )

    advance(2.0)
    client.payload = raw_payload()
    client.payload["blocks"]["near"]["columns"] = [
        col for col in columns if col.get("pos") != {"x": 11, "y": 63, "z": -5}
    ]
    await perception.poll({"near"})
    merged = perception.cache.raw
    assert merged is not None and all(
        col.pos is None or (col.pos.x, col.pos.y, col.pos.z) != (11.0, 63.0, -5.0)
        for col in merged.blocks.near.columns
    )


async def test_dig_result_becomes_activity_in_context(tmp_path: Any) -> None:
    """§五十五：挖成功之后，agent context 的 activity 是「刚挖掉了 <方块>」。"""
    from app.integrations.minecraft.events import parse_bridge_event

    async with turn_stack(tmp_path, "嗯") as env:
        env.bridge.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.completed",
                    "session_id": "s1",
                    "timestamp": 2.0,
                    "action": "dig",
                    "action_id": "act_dig_1",
                    "status": "SUCCEEDED",
                    "result": {
                        "position": {"x": 120, "y": 64, "z": -230},
                        "block_before": "minecraft:stone",
                        "block_after": "air",
                    },
                }
            )
        )
        assert env.bridge.context.activity == "刚挖掉了 minecraft:stone"
        assert "刚挖掉了 minecraft:stone" in env.bridge.context_line()


def test_tool_context_metadata_keys_are_documented() -> None:
    """供后续阶段对齐：运输层/意图键的名字是稳定契约。"""
    assert (BRIDGE_KEY, INTENT_KEY, TURN_ORIGIN_KEY, PLAYER_KEY) == (
        "minecraft",
        "minecraft_explicit_intent",
        "turn_origin",
        "minecraft_player",
    )
