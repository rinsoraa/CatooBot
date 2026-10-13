"""Phase 8C — 有界目标规划与原子工具编排（游戏内多步目标 → 既有计划层）。

任务书 §8：测试必须确定性、隔离、可重复，不依赖真实 LLM，也不用 sleep。
这里复用 tests/test_minecraft_chat_bridge.py 的 ChatStack：角色运行时 / 工具运行时 /
策略门 / 工具链**全是真的**，只有脚本化模型与假 MinecraftService；任务入口、计划层与
身份解析用最小替身（只实现被测路径真正用到的方法）。

断言对象是「谁接走了这句话」与「留下了什么可审计证据」：
  * 唯一接管：任务入口接了计划层就绝不接手；计划层接了角色聊天就绝不接手。
  * 纯查询/闲聊/控制命令/状态查询**绝不**建任务。
  * 跟随目标只能来自已验证身份；解析不出来就 fail-closed 说清原因。
  * 创建成功才出现 task_id；失败/忙碌如实记录为 CLARIFY / BLOCKED。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.integrations.minecraft.identity_context import (
    IdentityChannel,
    IdentityContext,
    IdentityResolution,
)
from app.tasks.turn import TaskTurnOutcome
from app.tools.models import RouteTarget
from tests.test_minecraft_chat_bridge import stack

MC_SESSION_PREFIX = "minecraft:"


class FakeTurns:
    """任务入口替身：默认「没接管」，也可以演一遍接管/忙碌。"""

    def __init__(
        self, *, handled: bool = False, action: str = "create", task_id: str = "t_task"
    ) -> None:
        self.handled = handled
        self.action = action
        self.task_id = task_id
        self.calls = 0

    async def handle(self, *, session_id: str, user_id: str, text: str, **_: Any) -> Any:
        self.calls += 1
        if not self.handled:
            return TaskTurnOutcome(False)
        return TaskTurnOutcome(True, action=self.action, task_id=self.task_id, reply="任务侧回答。")


class FakePlans:
    """计划层替身：记录调用参数，返回可编排的结果。"""

    def __init__(
        self, result: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def plan_goal_from_user(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return created_result()


def created_result(task_id: str = "task_8c", player_name: str = "IdSmoke8C") -> dict[str, Any]:
    return {
        "action": "created",
        "reply": "跟随摘要。",
        "outcome": "READY_FOR_APPROVAL",
        "plan": SimpleNamespace(
            target=SimpleNamespace(player_name=player_name),
            risk_summary={"duration_note": "3 分钟"},
        ),
        "record": SimpleNamespace(task_id=task_id),
    }


class FakeIdentityResolver:
    """身份解析替身：只回答「是否已验证」与对手平台标识。"""

    def __init__(
        self, resolution: IdentityResolution, *, platform: str = "qq", user_id: str = "10001"
    ) -> None:
        self.resolution = resolution
        self.platform = platform
        self.user_id = user_id
        self.calls: list[dict[str, Any]] = []

    async def resolve(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        # 返回**真实的** IdentityContext：角色运行时随后要读 relationship_key 等字段，
        # 用替身会把"接线对不对"测成"替身字段全不全"。
        verified = self.resolution is IdentityResolution.VERIFIED
        user_id = str(kwargs.get("user_id") or self.user_id)
        return IdentityContext(
            resolution=self.resolution,
            channel=IdentityChannel.MINECRAFT_CHAT,
            person_ref=f"qq:{self.user_id}" if verified else f"minecraft_chat:{user_id}",
            platform=self.platform if verified else "",
            platform_user_id=self.user_id if verified else "",
            server_id="mc-test-server",
            username=str(kwargs.get("user_name") or user_id),
            relationship_key=str(self.user_id) if verified else user_id,
        )


class FakeProposals:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[dict[str, Any]] = []

    async def resolve_target(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.ok:
            return SimpleNamespace(status="MISSING", reason="no_verified_link")
        return SimpleNamespace(
            status="VERIFIED",
            server_id="mc-test-server",
            player_uuid="11111111-2222-3333-4444-555555555555",
            player_name="IdSmoke8C",
            reason="verified_link",
        )


def wire(
    env: Any,
    *,
    turns: FakeTurns | None = None,
    plans: FakePlans | None = None,
    identity: FakeIdentityResolver | None = None,
    proposals: FakeProposals | None = None,
) -> tuple[FakeTurns, FakePlans]:
    """把三个替身装到真实的 bot 上（bridge 通过 getattr 取它们）。"""
    turns = turns or FakeTurns()
    plans = plans or FakePlans()
    bot = env.chat.bot  # bridge 持有的最小 Bot 面（FakeBot：ai / character / behavior）
    bot.task_turns = turns
    bot.agent_plans = plans
    bot.proposals = proposals or FakeProposals()
    bot.character.identity_resolver = identity or FakeIdentityResolver(IdentityResolution.VERIFIED)
    return turns, plans


def last_route(env: Any) -> Any:
    route = env.runtime.routes.last
    assert route is not None, "每个被测试的请求都必须留下一条路由记录"
    return route


# ============================================================ A 组：路由与唯一接管


async def test_verified_follow_request_plans_a_bounded_task(tmp_path: Any) -> None:
    """游戏内「跟着我」+ 已验证身份 → 进入既有计划层，目标来自身份绑定。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = wire(env)
        await env.say("空凛", "跟着我")
        assert turns.calls == 1, "任务入口先看一次（没接管）"
        assert len(plans.calls) == 1, "计划层必须恰好接一次"
        call = plans.calls[0]
        assert call["objective"] == "跟着我"
        assert call["user_id"] == "空凛" and call["session_id"].startswith(MC_SESSION_PREFIX)
        assert call["source"] == "minecraft"
        assert getattr(call["target"], "player_name", "") == "IdSmoke8C", "目标只能来自身份绑定"
        route = last_route(env)
        assert route.route_target == RouteTarget.TASK.value
        assert route.reason_code == "plan_created"
        assert route.task_id == "task_8c"
        assert "确认" in env.chat_replies[-1]


async def test_unverified_follow_request_fails_closed(tmp_path: Any) -> None:
    """身份没核验 → 绝不调计划层、绝不用昵称猜人，并如实说明。"""
    async with stack(tmp_path, "角色回答。") as env:
        _, plans = wire(env, identity=FakeIdentityResolver(IdentityResolution.UNBOUND))
        await env.say("空凛", "跟着我")
        assert plans.calls == [], "未核验身份不得进入计划层"
        route = last_route(env)
        assert route.route_target == RouteTarget.CLARIFY.value
        assert route.reason_code == "plan_target_unverified"
        assert "绑定" in env.chat_replies[-1]


async def test_explore_request_enters_the_plan_layer_without_a_target(tmp_path: Any) -> None:
    """有界探索请求：进既有计划层，且不伪造目标玩家。"""
    async with stack(tmp_path, "角色回答。") as env:
        _, plans = wire(env)
        await env.say("空凛", "去附近转转探索一下")
        assert len(plans.calls) == 1
        target = plans.calls[0]["target"]
        assert getattr(target, "player_uuid", "") == "", "探索不需要玩家目标"
        assert getattr(target, "player_name", "") == ""


async def test_pure_query_never_creates_a_task(tmp_path: Any) -> None:
    """纯查询（§五）：既不进任务入口也不进计划层，照常走角色回合。"""
    async with stack(tmp_path, "附近有橡树。") as env:
        _, plans = wire(env)
        await env.say("空凛", "附近有什么方块？")
        assert plans.calls == [], "查询绝不建任务"
        assert env.chat_replies == ["附近有橡树。"]
        assert last_route(env).route_target == RouteTarget.CHAT_ONLY.value


async def test_chitchat_never_enters_the_plan_layer(tmp_path: Any) -> None:
    async with stack(tmp_path, "是呀。") as env:
        _, plans = wire(env)
        await env.say("空凛", "今天玩得开心吗")
        assert plans.calls == []


async def test_control_command_is_not_hijacked_by_the_plan_layer(tmp_path: Any) -> None:
    """确认/暂停这类控制词归任务入口管，绝不被计划层当成新目标。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = wire(env, turns=FakeTurns(handled=True, action="confirm", task_id="t_c"))
        await env.say("空凛", "确认")
        assert plans.calls == [], "控制命令不得触发新计划"
        assert turns.calls == 1
        assert last_route(env).task_id == "t_c"


async def test_status_query_is_not_hijacked(tmp_path: Any) -> None:
    async with stack(tmp_path, "角色回答。") as env:
        _, plans = wire(env)
        await env.say("空凛", "任务怎么样了")
        assert plans.calls == []


async def test_task_entry_takeover_excludes_the_plan_layer(tmp_path: Any) -> None:
    """唯一接管：任务入口接了，计划层与角色侧都不许再动。"""
    async with stack(tmp_path, "角色回答。") as env:
        _, plans = wire(env, turns=FakeTurns(handled=True, action="create", task_id="t_res"))
        await env.say("空凛", "帮我砍棵树把木头带回来")
        assert plans.calls == []
        assert env.chat_replies == ["任务侧回答。"]
        assert last_route(env).reason_code == "task_handled"


async def test_busy_task_entry_still_takes_over(tmp_path: Any) -> None:
    async with stack(tmp_path, "角色回答。") as env:
        _, plans = wire(env, turns=FakeTurns(handled=True, action="busy", task_id="t_busy"))
        await env.say("空凛", "跟着我")
        assert plans.calls == [], "忙着的时候不得叠新计划"
        assert env.chat_replies == ["任务侧回答。"]


# ============================================================ B 组：计划结果如实呈现


async def test_plan_rejected_is_reported_truthfully(tmp_path: Any) -> None:
    async with stack(tmp_path, "角色回答。") as env:
        plans = FakePlans(
            result={
                "action": "plan_rejected",
                "reply": "现在排不了这件事：路上不好走。",
                "outcome": "UNSUPPORTED",
                "plan": None,
                "record": None,
            }
        )
        wire(env, plans=plans)
        await env.say("空凛", "跟着我")
        route = last_route(env)
        assert route.route_target == RouteTarget.CLARIFY.value
        assert route.reason_code == "plan_plan_rejected"
        assert route.task_id == ""
        assert "排不了" in env.chat_replies[-1]


async def test_plan_layer_failure_falls_back_to_chat(tmp_path: Any) -> None:
    """计划层抛异常：这条消息仍要被角色侧正常回答（绝不静默丢掉）。"""
    async with stack(tmp_path, "我在的。") as env:
        wire(env, plans=FakePlans(error=RuntimeError("plan boom")))
        await env.say("空凛", "跟着我")
        assert env.chat_replies == ["我在的。"]
        assert last_route(env).route_target == RouteTarget.CHAT_ONLY.value


async def test_busy_plan_action_is_recorded_as_blocked(tmp_path: Any) -> None:
    async with stack(tmp_path, "角色回答。") as env:
        plans = FakePlans(
            result={
                "action": "busy",
                "reply": "我手上还有一件事没做完，先做完这个再说。",
                "outcome": "READY",
                "plan": None,
                "record": None,
            }
        )
        wire(env, plans=plans)
        await env.say("空凛", "跟着我")
        route = last_route(env)
        assert route.route_target == RouteTarget.BLOCKED.value
        assert route.reason_code == "plan_busy"


# ============================================================ C 组：会话与身份边界


async def test_plan_layer_receives_the_mc_identity_not_someone_else(tmp_path: Any) -> None:
    """游戏内创建的任务只用说话者自己的身份与会话（不冒用他人）。"""
    async with stack(tmp_path, "角色回答。") as env:
        _, plans = wire(env)
        await env.say("空凛", "跟着我")
        call = plans.calls[0]
        assert call["user_id"] == "空凛"
        assert call["session_id"].endswith(":空凛")
        assert "IdSmoke8C" not in call["user_id"]


async def test_missing_plan_layer_keeps_chat_working(tmp_path: Any) -> None:
    """计划层没装配：照常聊天，绝不因为接线缺失就没反应。"""
    async with stack(tmp_path, "我还在。") as env:
        env.chat.bot.task_turns = FakeTurns()
        env.chat.bot.agent_plans = None
        await env.say("空凛", "跟着我")
        assert env.chat_replies == ["我还在。"]


# ============================================================ D 组：可观测与隐私


async def test_plan_route_carries_no_raw_text(tmp_path: Any) -> None:
    """路由证据只有长度与稳定原因码，不存聊天原文（§六）。"""
    async with stack(tmp_path, "角色回答。") as env:
        wire(env)
        await env.say("空凛", "跟着我")
        route = last_route(env)
        assert route.text_chars == len("跟着我")
        blob = str(env.runtime.routes.snapshot())
        assert "跟着我" not in blob


# ============================================================ 计划记账（Phase 8C 修正）


def test_plan_hash_bookkeeping_reads_the_real_attribute() -> None:
    """Phase 8C：记账读的是 ``TaskPlan.plan_hash``；旧代码读不存在的 ``.hash`` → 恒为空串。"""
    from app.tasks.agent_service import AgentPlanService

    service = object.__new__(AgentPlanService)  # 纯函数：不碰 store，直接验取值路径
    task_plan = SimpleNamespace(plan_hash="abc123")
    result = SimpleNamespace(plan=SimpleNamespace(plan=task_plan))
    assert service._hash_of(result) == "abc123"
    assert service._hash_of(SimpleNamespace(plan=None)) == ""
    assert service._plan_payload(
        SimpleNamespace(plan=SimpleNamespace(to_payload=lambda: {"k": 1}))
    ) == {"k": 1}
