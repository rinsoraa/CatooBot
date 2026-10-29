"""Phase 8A — 自然语言行动路由与决策可观测性（契约 + 诊断可区分性）。

任务书 §8：测试必须**确定性、隔离、可重复**，不依赖真实 LLM、不用 sleep 伪造并发。
这里复用 tests/test_minecraft_chat_bridge.py 的 ChatStack —— 角色运行时 / 工具运行时 /
策略门 / 工具链**全是真的**，只有两处替身：脚本化模型（MockAIProvider）与假 MinecraftService。

断言对象是 **RouteTrace**（Phase 8A 新增的结构化路由证据）：
route_target 有限枚举 + reason_code 稳定原因码，并且必须能区分
「没有候选」「模型没选」「配置关闭」「策略拒绝」「确认待办」「动作运行中」与「真实成功」。
"""

from __future__ import annotations

from typing import Any

from app.character.turn import TurnOrigin
from app.integrations.minecraft.chat_bridge import MAX_INFLIGHT
from app.tasks.turn import TaskTurnOutcome
from app.tasks.turn_decision import TurnDecisionKind
from app.tools.models import RouteTarget
from tests.test_minecraft_agent_confirm_gate import decide
from tests.test_minecraft_chat_bridge import stack
from tests.turn_decision_stub import TurnDecisionStub


def last_route(env: Any) -> Any:
    route = env.runtime.routes.last
    assert route is not None, "每个被测试的请求都必须留下一条路由记录"
    return route


class FakeTurns:
    """任务侧替身：只回答"这句话归任务管"和它创建的任务 ID。"""

    def __init__(self, *, task_id: str = "task_8a", reply: str = "好，我去看看。") -> None:
        self.task_id = task_id
        self.reply = reply

    async def handle(self, *, session_id: str, user_id: str, text: str, **_: Any) -> Any:
        return TaskTurnOutcome(True, action="create", task_id=self.task_id, reply=self.reply)


class FakeAgentClassifier:
    def __init__(self, kind: str = "multi_step") -> None:
        self.kind = kind

    def classify(self, text: str, *, tool_candidates: int = 0) -> Any:
        return type("C", (), {"kind": self.kind, "reason": "stub"})()

    def may_handle(self, kind: str) -> bool:
        return True

    def detect_control(self, text: str) -> str:
        return ""


class FakeAgentResult:
    def __init__(self, *, status: str = "ok", error_type: str = "", usable: bool = True) -> None:
        self.status = status
        self.error_type = error_type
        self.is_usable = usable
        self.task_id = "task_agent_8a"

    def to_prompt_block(self) -> str:
        return "（替身结果）"


class FakeAgent:
    """AgentHarness 的最小面：控制命令 / 分类 / 是否可处理 / 运行。"""

    def __init__(self, *, kind: str = "multi_step", result: FakeAgentResult | None = None) -> None:
        self.enabled = True
        self.classifier = FakeAgentClassifier(kind)
        self.result = result or FakeAgentResult()

    async def handle_control(self, text: str, session_id: str) -> str:
        return ""

    def can_handle(self, kind: str) -> bool:
        return True

    async def run(
        self, text: str, *, session_id: str, user_id: str, context: Any, classification: Any
    ) -> Any:
        return self.result


# ============================================================ §8.1 正向路由


async def test_plain_chat_calls_no_tool_and_records_no_candidates(tmp_path: Any) -> None:
    """普通闲聊：不调用工具、不产生世界动作，并留下"没有候选"这个**真实原因**。"""
    async with stack(tmp_path, "今天挺好的呀～") as env:
        await env.say("示例好友", "嗯嗯")
        route = last_route(env)
        assert route.route_target == RouteTarget.CHAT_ONLY.value
        assert route.reason_code == "no_tool_candidates"
        assert route.candidates == []
        assert route.tool_called is False
        assert env.tools.executor.traces == []
        assert env.chat_replies == ["今天挺好的呀～"]


async def test_model_not_calling_a_tool_is_distinct_from_no_candidates(tmp_path: Any) -> None:
    """有候选但模型没选：reason_code 必须与"没有候选"区分开。"""
    async with stack(tmp_path, "我想想哦……") as env:
        await env.say("示例好友", "看看我的背包")
        route = last_route(env)
        assert route.candidates, "背包请求必须有候选工具"
        assert route.reason_code == "model_no_tool_call"
        # Phase 8A FIX：模型未选择工具时，路由目标是 CHAT_ONLY（与「无候选」相同），
        # 仅靠 reason_code 区分两者；不能残留候选阶段的 MINECRAFT_TOOL。
        assert route.route_target == RouteTarget.CHAT_ONLY.value
        assert route.tool_called is False
        assert env.tools.executor.traces == []


async def test_explicit_world_query_routes_through_the_tool_path(tmp_path: Any) -> None:
    """明确查询世界：走既有工具路径，并记录候选、选中与调用事实。"""
    async with stack(tmp_path, decide("minecraft_world"), "我在平原上呢～") as env:
        await env.say("示例好友", "看看附近有什么方块")
        route = last_route(env)
        assert route.route_target == RouteTarget.MINECRAFT_TOOL.value
        assert route.selected == "minecraft_world"
        assert route.tool_called is True
        assert route.reason_code == "action_succeeded"
        assert "minecraft_world" in [item["name"] for item in route.candidates]
        assert [t.tool_name for t in env.tools.executor.traces] == ["minecraft_world"]


async def test_inventory_query_selects_the_inventory_tool(tmp_path: Any) -> None:
    async with stack(tmp_path, decide("minecraft_inventory"), "背包里有木头。") as env:
        await env.say("示例好友", "背包里有什么？")
        route = last_route(env)
        assert route.selected == "minecraft_inventory"
        assert route.tool_called is True


async def test_stop_is_not_swallowed_by_the_chat_path(tmp_path: Any) -> None:
    """§4.5：游戏内"停下来"必须真的走控制工具，不能被聊天确认分支吞掉。"""
    async with stack(tmp_path, decide("minecraft_stop"), "好，停了。") as env:
        await env.say("示例好友", "停下来")
        route = last_route(env)
        assert route.reason_code != "control_reply", "stop 不能被当成普通对话控制意图"
        assert route.selected == "minecraft_stop"
        assert route.tool_called is True


async def test_multi_step_goal_routes_to_the_task_side(tmp_path: Any) -> None:
    """§4.3：需要编排的请求交给任务侧，并且不产生一次"裸工具调用"。"""
    # §4.1：多步目标先经统一决策入口 → TASK_PROPOSAL → 既有任务入口接管。
    async with stack(
        tmp_path,
        "好，我去砍一棵橡树。",
        decision=TurnDecisionStub(
            TurnDecisionKind.TASK_PROPOSAL, task_goal="去砍一棵橡树，挖一块原木并捡回来"
        ),
    ) as env:
        env.chat.bot.task_turns = FakeTurns()  # type: ignore[attr-defined]
        await env.say("示例好友", "去砍一棵橡树，挖一块原木并捡回来")
        route = last_route(env)
        assert route.route_target == RouteTarget.TASK.value
        assert route.reason_code == "task_handled"
        assert route.task_id == "task_8a"
        assert env.tools.executor.traces == []


async def test_agent_multi_step_is_recorded_as_task(tmp_path: Any) -> None:
    async with stack(tmp_path, "我分几步来做。") as env:
        env.runtime.agent = FakeAgent()  # type: ignore[assignment]
        await env.say("示例好友", "帮我看看箱子里的东西，再拿两块木头出来")
        route = last_route(env)
        assert route.route_target == RouteTarget.TASK.value
        assert route.reason_code == "agent_multi_step"
        assert route.task_id == "task_agent_8a"


# ========================================================== §8.2 负向/配置


async def test_tools_disabled_is_recorded_as_configuration_not_confusion(tmp_path: Any) -> None:
    """工具总开关关闭：诊断必须指出**配置**，而不是笼统归为"模型没听懂"。"""
    async with stack(tmp_path, "我看看哦。") as env:
        env.runtime.tools = None
        await env.say("示例好友", "看看我的背包")
        route = last_route(env)
        assert route.reason_code == "tool_runtime_disabled"
        assert route.route_target == RouteTarget.CHAT_ONLY.value
        assert route.tool_called is False


async def test_invalid_tool_decision_is_recorded(tmp_path: Any) -> None:
    """模型给出的工具名不存在：记录可区分的 invalid_tool_decision，且不执行动作。"""
    async with stack(tmp_path, decide("minecraft_teleport"), "我做不到这个。") as env:
        await env.say("示例好友", "把我传送到出生点")
        route = last_route(env)
        assert route.tool_called is False
        assert route.reason_code == "invalid_tool_decision"
        assert route.policy_outcome in {"unknown_tool", ""}


async def test_untrusted_player_low_action_is_policy_denied(tmp_path: Any) -> None:
    """§8.2：不可信玩家提出的 LOW 动作必须继续被策略拒绝，且不产生真实动作。"""
    async with stack(
        tmp_path, decide("minecraft_follow_player", username="罐头"), "好。", trusted=[]
    ) as env:
        await env.say("陌生人", "跟着我")
        route = last_route(env)
        assert route.tool_called is False
        assert route.reason_code in {"policy_denied", "clarify_needed"}
        assert env.service.action_calls("follow_player") == []


async def test_background_turn_cannot_use_a_low_action(tmp_path: Any) -> None:
    """§8.2：BACKGROUND 回合不能伪装成 USER 意图去驱动 LOW 动作。"""
    async with stack(
        tmp_path, decide("minecraft_follow_player", username="示例好友"), "好。"
    ) as env:
        reply = await env.runtime.respond(
            "sess-bg",
            "示例好友",
            "跟着我",
            turn_origin=TurnOrigin.BACKGROUND,
        )
        assert isinstance(reply, str)
        route = last_route(env)
        assert route.turn_origin == TurnOrigin.BACKGROUND.value
        assert route.tool_called is False
        assert env.service.action_calls("follow_player") == []


# ========================================================== §8.3 追踪与回退


async def test_bridge_drops_are_recorded_with_reasons(tmp_path: Any) -> None:
    """桥接层丢包（自身发言 / 悄悄话 / 忙碌）以前零记录，现在必须有稳定原因码。"""
    async with stack(tmp_path, "在呢～") as env:
        mine = env.chat.self_username() or "罐头"
        await env.say(mine, "我自己说的话")
        assert last_route(env).reason_code == "mc_chat_self_speech"

        await env.say("示例好友", "偷偷跟你说", private=True)
        assert last_route(env).reason_code == "mc_chat_private"

        env.chat._inflight = MAX_INFLIGHT
        await env.say("示例好友", "忙的时候来一句")
        assert last_route(env).reason_code == "mc_chat_busy"


async def test_stale_tool_traces_are_cleared_when_candidates_are_absent(tmp_path: Any) -> None:
    """回归：没有候选的回合**不得**留下上一轮的 ToolTrace（诊断里的 stale trace）。"""
    async with stack(tmp_path, decide("minecraft_world"), "看过了。", "没有这类工具哦。") as env:
        await env.say("示例好友", "看看附近有什么方块")
        assert len(env.tools.orchestrator.last_traces) == 1
        await env.say("示例好友", "嗯嗯")
        route = last_route(env)
        assert route.reason_code == "no_tool_candidates"
        assert env.tools.orchestrator.last_traces == []


async def test_route_record_never_contains_the_raw_chat_text(tmp_path: Any) -> None:
    """§5.1：路由证据不得保存聊天原文，只能留长度与结构化事实。"""
    secret = "这是一句不该被记录的原文-9F3A"
    async with stack(tmp_path, "嗯嗯。") as env:
        await env.say("示例好友", secret)
        snapshot = env.runtime.routes.snapshot()
        assert snapshot, "必须有路由记录"
        assert secret not in str(snapshot)
        assert snapshot[-1]["text_chars"] == len(secret)
        assert snapshot[-1]["route_id"].startswith("rt_")


async def test_route_journal_is_bounded(tmp_path: Any) -> None:
    """记录环进程内有界：不会因为长期运行无限增长。"""
    async with stack(tmp_path, "嗯。") as env:
        for index in range(80):
            await env.say("示例好友", f"随便聊聊 {index}")
        assert len(env.runtime.routes) <= 64
        assert len(env.runtime.routes.snapshot(limit=100)) <= 64


async def test_clarify_is_recorded_when_the_agent_says_the_goal_is_unclear(tmp_path: Any) -> None:
    """§4.4：目标不清楚时留下 CLARIFY，而不是被记成"普通聊天"或"成功"。"""
    async with stack(tmp_path, "你是想让我做什么呢？") as env:
        env.runtime.agent = FakeAgent(  # type: ignore[assignment]
            result=FakeAgentResult(status="failed", error_type="planning_failed", usable=False)
        )
        await env.say("示例好友", "把那个东西弄一下")
        route = last_route(env)
        assert route.route_target == RouteTarget.CLARIFY.value
        assert route.reason_code == "agent_goal_unclear"
        assert route.tool_called is False
