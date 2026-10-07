"""Phase 5B §三十一：QQ 任务入口与统一任务控制（A–L + 身份/去重/认领）。

要钉死的四件事：

* **QQ 只是入口**：消息 → 任务入口 → Planner/TaskRuntime，绝不绕过 TaskRuntime 动世界；
* **普通聊天不受影响**：判断失败就不认领，仍然走人格回复（§二十二/§二十三）；
* **归属**：只有任务发起人能确认/暂停/继续/停止，别人一律被拒且**不改任务状态**（§5.2/§十六）；
* **事件幂等**：``(task_id, event_seq)`` 去重，同一件事只发一次（§十五）。

这里用的是**真 TaskRuntime**（假工具通道 + 假时钟）+ **真 OneBot 解析出来的消息事件**，
只有"发 QQ 消息"那一步是替身（记录发出去的文字）——不是 mock 掉任务系统。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.onebot_v11.parser import parse_event
from app.core.event_bus import EventBus
from app.tasks.models import TASK_AUTHORIZATION_EXPIRED, TASK_CONFIRMATION_REQUIRED
from app.tasks.qq_entry import QQIdentity, QQTaskEntry, session_target
from tests.test_task_recovery import Clock, FakeInvoke, make_runtime

SELF_ID = 10001
GROUP = 987654
USER_A = "2731431246"
USER_B = "1234567"


# ------------------------------------------------------------------ 替身与工具


class FakeDelivery:
    """记录发出去的 QQ 消息（唯一替身：发送通道）。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, int, str]] = []

    async def send_private_msg(self, user_id: int, message: str) -> int:
        self.sent.append(("private", int(user_id), str(message)))
        return len(self.sent)

    async def send_group_msg(self, group_id: int, message: str) -> int:
        self.sent.append(("group", int(group_id), str(message)))
        return len(self.sent)

    def texts(self, kind: str | None = None) -> list[str]:
        return [text for k, _id, text in self.sent if kind is None or k == kind]

    def last(self) -> str:
        return self.sent[-1][2] if self.sent else ""


class FakeBot:
    def __init__(self) -> None:
        self.api = FakeDelivery()
        self.social = None
        self.response_delivery = None
        self.behavior = None
        self.log = _QuietLog()


class _QuietLog:
    def info(self, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, *args: Any, **kwargs: Any) -> None: ...
    def exception(self, *args: Any, **kwargs: Any) -> None: ...


def private_event(text: str, *, user_id: str = USER_A, self_id: int = SELF_ID) -> Any:
    return parse_event(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 1,
            "user_id": int(user_id),
            "self_id": self_id,
            "time": 1700000000,
            "raw_message": text,
            "sender": {"user_id": int(user_id), "nickname": "Rinsora"},
            "message": [{"type": "text", "data": {"text": text}}],
        }
    )


def group_event(
    text: str, *, user_id: str = USER_A, group_id: int = GROUP, mention: bool = True
) -> Any:
    segments: list[dict[str, Any]] = []
    if mention:
        segments.append({"type": "at", "data": {"qq": str(SELF_ID)}})
    segments.append({"type": "text", "data": {"text": text}})
    return parse_event(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": 2,
            "user_id": int(user_id),
            "group_id": group_id,
            "self_id": SELF_ID,
            "time": 1700000000,
            "raw_message": text,
            "sender": {"user_id": int(user_id), "nickname": "Rinsora"},
            "message": segments,
        }
    )


class Stack:
    """一次测试用到的全套：真 TaskRuntime + 真入口 + 假发送通道。"""

    def __init__(self, **kwargs: Any) -> None:
        self.clock = Clock(1000.0)
        self.invoke = FakeInvoke(**kwargs)
        self.runtime, self.invoke, self.confirmations, self.events = make_runtime(
            invoke=self.invoke, clock=self.clock
        )
        self.bot = FakeBot()
        self.entry = QQTaskEntry(self.bot, runtime=self.runtime, observe=self._observe())
        self.runtime._publish_fn = lambda event, payload: self._record(event, payload)  # noqa: SLF001

    # 观察通道：入口/规划阶段只用 SAFE 读（与生产同一条路）
    def _observe(self) -> Any:
        from app.tasks.runtime import TaskInvocation

        async def observe(tool: str, arguments: Any) -> TaskInvocation:
            if tool == "minecraft_world":
                return TaskInvocation(
                    ok=True, result={"ok": True, "position": {"x": 1, "y": 64, "z": 1}}
                )
            if tool == "minecraft_find_blocks":
                return TaskInvocation(
                    ok=True,
                    result={
                        "ok": True,
                        "matches": [
                            {
                                "block": {"name": "oak_log"},
                                "position": {"x": 12, "y": 64, "z": 9},
                                "distance": {"goal_near": 3.0, "raw": 3.2},
                            }
                        ],
                    },
                )
            if tool == "minecraft_inventory":
                return TaskInvocation(
                    ok=True,
                    result={"ok": True, "held_item": {"name": "stone_pickaxe"}, "items": []},
                )
            if tool == "minecraft_dig_capability":
                return TaskInvocation(
                    ok=True, result={"ok": True, "can_dig": False, "reason": "too_far"}
                )
            raise AssertionError(f"不该观察到这里：{tool}")

        return observe

    def _record(self, event: str, payload: dict[str, Any]) -> None:
        """与生产同形：记录下来，并像 Bot 那样把通知调度成后台任务。"""
        import asyncio

        self.events.rows.append((event, dict(payload)))
        entry = getattr(self, "entry", None)
        if entry is None:
            return
        asyncio.create_task(entry.on_task_event(event, dict(payload)))

    async def say(self, event: Any) -> bool:
        claimed = await self.entry.on_message(event)
        await self.flush()
        return claimed

    async def flush(self) -> None:
        """把 on_task_event 的后台通知跑完（生产里由 Bot 的 create_task 调度）。"""
        import asyncio

        for _ in range(5):
            await asyncio.sleep(0)

    async def notify(self, event: str, payload: dict[str, Any]) -> None:
        await self.entry.on_task_event(event, dict(payload))
        await self.flush()

    @property
    def task(self) -> Any:
        return self.runtime._store  # noqa: SLF001


async def current(stack: Stack, session_id: str = f"private:{USER_A}") -> Any:
    return await stack.runtime.current(session_id)


async def current_group(stack: Stack, group_id: int = GROUP) -> Any:
    return await stack.runtime.current(f"group:{group_id}")


# ------------------------------------------------------------------ A/B/C


async def test_a_qq_private_request_creates_a_task_and_shows_the_plan() -> None:
    stack = Stack()
    claimed = await stack.say(private_event("帮我找附近的一块橡木"))
    assert claimed is True, "任务请求必须被任务入口认领（不再走人格回复）"
    record = await current(stack)
    assert record is not None
    assert record.state.value == "PENDING_CONFIRMATION"
    assert record.user_id == USER_A, "身份必须用稳定的 QQ 号"
    assert record.session_id == f"private:{USER_A}"
    assert record.source == "qq", "§二十九/§三十：来源要记成 qq"
    assert len(stack.invoke.world_actions) == 0, "计划阶段只能 SAFE 观察，绝不改世界"
    reply = stack.bot.api.last()
    assert "罐头准备这样做" in reply and "回复「确认」开始" in reply
    assert record.task_id not in reply and "plan_hash" not in reply, "内部信息绝不发给用户"
    assert any(name == "task.created" for name, _ in stack.events.rows)


@pytest.mark.parametrize(
    "text",
    [
        "我今天想砍树",
        "木头真的好难找",
        "你觉得橡木好看吗",
        "我想吃蛋糕",
        "你在干嘛",
        "看看附近有没有铁矿",
    ],
)
async def test_b_normal_qq_chat_never_creates_a_task(text: str) -> None:
    stack = Stack()
    claimed = await stack.say(private_event(text))
    assert claimed is False, "普通聊天不能被任务入口吞掉（§二十二）"
    assert await current(stack) is None
    assert stack.bot.api.sent == [], "不认领就不该由任务侧回话"


async def test_c_qq_confirmation_starts_the_task() -> None:
    stack = Stack()
    await stack.say(private_event("帮我找附近的一块橡木"))
    claimed = await stack.say(private_event("确认"))
    assert claimed is True
    record = await current(stack)
    assert record is not None
    assert record.state.value == "WAITING_ACTION", "确认后真的开始执行第一步"
    assert record.authorization is not None
    assert "开始处理" in stack.bot.api.last()


# ------------------------------------------------------------------ D/E：归属与来源


async def test_d_other_users_cannot_control_someone_elses_group_task() -> None:
    stack = Stack()
    await stack.say(group_event("帮我找附近的一块橡木", user_id=USER_A))
    record = await current_group(stack)
    assert record is not None and record.user_id == USER_A
    before = (record.state.value, record.authorization, record.plan_hash)

    for text in ("确认", "暂停这个任务", "继续", "停止"):
        claimed = await stack.say(group_event(text, user_id=USER_B))
        assert claimed is True, "拒绝也要说话（否则用户以为没听见）"
        assert "你不是这个任务的发起人" in stack.bot.api.last()
        still = await current_group(stack)
        assert still is not None
        assert (still.state.value, still.authorization, still.plan_hash) == before, (
            "拒绝绝不能改变任务状态"
        )
    assert stack.invoke.world_actions == [], "没人能靠拒绝消息让世界动作发生"


@pytest.mark.parametrize("origin", ["system", "webui", "background", "initiative", "task"])
async def test_e_system_origins_cannot_confirm_a_qq_task(origin: str) -> None:
    stack = Stack()
    await stack.say(private_event("帮我找附近的一块橡木"))
    record = await current(stack)
    assert record is not None
    with pytest.raises(Exception) as excinfo:
        await stack.runtime.confirm_and_start(
            record.task_id, user_id=USER_A, session_id=f"private:{USER_A}", origin=origin
        )
    assert "not_user_turn" in str(getattr(excinfo.value, "code", ""))
    still = await current(stack)
    assert still is not None and still.authorization is None


async def test_identity_uses_the_stable_id_not_the_nickname() -> None:
    first = QQIdentity.from_event(private_event("你好", user_id=USER_A))
    second = QQIdentity.from_event(private_event("你好", user_id=USER_A))
    assert first.user_id == second.user_id == USER_A
    assert first.session_id == f"private:{USER_A}"
    group = QQIdentity.from_event(group_event("你好"))
    assert group.session_id == f"group:{GROUP}" and group.conversation_id == str(GROUP)
    assert session_target("private:42") == ("private", 42)
    assert session_target("group:7") == ("group", 7)
    assert session_target("weird") is None


# ------------------------------------------------------------------ F：授权到期


async def test_f_authorization_expiry_notifies_and_old_confirmation_is_rejected() -> None:
    stack = Stack()
    stack.runtime.config.authorization_ttl_seconds = 5.0
    await stack.say(private_event("帮我找附近的一块橡木"))
    await stack.say(private_event("确认"))
    record = await current(stack)
    assert record is not None
    old_confirmation = record.confirmation_id

    stack.clock.advance(6.0)
    # 安全边界（动作自然结束）后推进 → 授权到期
    await stack.runtime.on_action_event(
        action_id=record.pending_action_id, event="minecraft.action.completed", status="SUCCEEDED"
    )
    waiting = await current(stack)
    assert waiting is not None
    if waiting.state.value != "PENDING_CONFIRMATION":
        stack.clock.advance(1.0)
        fresh = await stack.runtime.get(record.task_id)
        assert fresh is not None
        fresh.pending_action_id = ""
        fresh.pending_step_id = ""
        fresh.state = fresh.state.RUNNING
        for step in fresh.steps:
            step.state = step.state.PENDING
        await stack.runtime._save(fresh)  # noqa: SLF001
        await stack.runtime.drive(record.task_id)
    expired = await stack.runtime.get(record.task_id)
    assert expired is not None
    assert expired.state.value == "PENDING_CONFIRMATION"
    assert expired.authorization is None
    names = [name for name, _ in stack.events.rows]
    assert TASK_AUTHORIZATION_EXPIRED in names
    assert expired.plan_hash == record.plan_hash, "授权到期不是重规划：计划不变"

    # 旧确认不能复用；用新确认重新走一遍用户回合
    ok, code = await stack.confirmations.consume(
        old_confirmation,
        task_id=expired.task_id,
        session_id=f"private:{USER_A}",
        user_id=USER_A,
        plan_hash=expired.plan_hash,
        arguments={"plan_hash": expired.plan_hash, "plan": expired.plan.hash_payload()},
        origin="user",
    )
    assert ok is False and code
    claimed = await stack.say(private_event("确认"))
    assert claimed is True
    renewed = await current(stack)
    assert renewed is not None and renewed.authorization is not None


# ------------------------------------------------------------------ G：重规划


async def test_g_replanning_notifies_the_new_plan_and_waits_for_confirmation() -> None:
    stack = Stack(dig_reason="air", dig_block=None)
    await stack.say(private_event("帮我找附近的一块橡木"))
    await stack.say(private_event("确认"))
    record = await current(stack)
    assert record is not None
    v1 = record.plan_hash

    # 世界变了：这一步的动作失败（TARGET_ALREADY_DONE 一类）
    await stack.runtime.on_action_event(
        action_id=record.pending_action_id,
        event="minecraft.action.failed",
        status="FAILED",
        error="目标方块已经不是原来那个了",
        code="minecraft.block_changed",
    )
    await stack.flush()
    handed_off = await stack.runtime.get(record.task_id)
    assert handed_off is not None
    assert handed_off.state.value == "REPLANNING"
    assert handed_off.replan_required is True
    assert stack.bot.api.last() and "原计划已经作废" in stack.bot.api.last()

    # 未确认前推进 → 一步世界动作都没有
    actions_before = list(stack.invoke.world_actions)
    await stack.runtime.drive(record.task_id)
    assert stack.invoke.world_actions == actions_before

    # Planner 出新计划 → v2 → 用户确认
    from tests.test_task_recovery import dig_plan

    replanned = await stack.runtime.replan(
        record.task_id, dig_plan(x=90, z=90), reason="WORLD_CHANGED"
    )
    assert replanned.plan_version == 2 and replanned.plan_hash != v1
    await stack.notify(
        TASK_CONFIRMATION_REQUIRED,
        {
            "task_id": replanned.task_id,
            "event_seq": replanned.event_seq + 1,
            "session_id": replanned.session_id,
            "state": replanned.state.value,
        },
    )
    text = stack.bot.api.last()
    assert "新的计划（第 2 版）" in text and "回复「确认」继续" in text
    claimed = await stack.say(private_event("确认"))
    assert claimed is True
    started = await current(stack)
    assert started is not None and started.state.value == "WAITING_ACTION"


# ------------------------------------------------------------------ H/I/J：暂停/继续/停止


async def test_h_i_j_pause_resume_cancel_through_qq() -> None:
    stack = Stack()
    await stack.say(private_event("帮我找附近的一块橡木"))
    await stack.say(private_event("确认"))
    record = await current(stack)
    assert record is not None

    assert await stack.say(private_event("暂停这个任务")) is True
    paused = await current(stack)
    assert paused is not None and paused.state.value in {"PAUSED", "WAITING_ACTION"}
    if paused.state.value != "PAUSED":  # 等动作自然结束
        await stack.runtime.on_action_event(
            action_id=paused.pending_action_id,
            event="minecraft.action.completed",
            status="SUCCEEDED",
        )
        paused = await stack.runtime.get(record.task_id)
        assert paused is not None
    assert paused.state.value == "PAUSED"
    assert "先停这里了" in stack.bot.api.last()

    assert await stack.say(private_event("继续")) is True
    resumed = await current(stack)
    assert resumed is not None and resumed.state.value != "PAUSED"

    assert await stack.say(private_event("停止")) is True
    cancelled = await stack.runtime.get(record.task_id)
    assert cancelled is not None and cancelled.state.value == "CANCELLED"
    assert "minecraft_stop" in stack.invoke.calls, "取消必须经 minecraft_stop 真停（不自己杀动作）"
    assert stack.invoke.calls[-1] == "minecraft_stop"


async def test_status_query_answers_with_a_short_summary() -> None:
    stack = Stack()
    await stack.say(private_event("帮我找附近的一块橡木"))
    await stack.say(private_event("确认"))
    for text in ("任务怎么样了", "现在到哪了", "任务还在做吗"):
        assert await stack.say(private_event(text)) is True
        reply = stack.bot.api.last()
        assert "状态：" in reply and "第 " in reply
        assert "plan_hash" not in reply and "action_id" not in reply


# ------------------------------------------------------------------ K：重启


async def test_k_restart_notifies_the_owner_and_never_repeats_world_actions() -> None:
    stack = Stack()
    await stack.say(private_event("帮我找附近的一块橡木"))
    await stack.say(private_event("确认"))
    record = await current(stack)
    assert record is not None
    assert record.state.value == "WAITING_ACTION"
    actions = list(stack.invoke.world_actions)

    # 新进程：同一份持久化存储 + 新的 runtime，QQ 入口照旧订阅
    fresh_runtime, _, _, _ = make_runtime(
        invoke=stack.invoke,
        store=stack.runtime._store,
        clock=stack.clock,  # noqa: SLF001
    )
    entry = QQTaskEntry(stack.bot, runtime=fresh_runtime, observe=stack._observe())  # noqa: SLF001
    records = await fresh_runtime.recover_persisted_tasks()
    assert len(records) == 1
    after = records[0]
    assert after.recovery["reason"] == "RUNTIME_RESTART"
    assert after.authorization is None and after.replan_required is True
    assert stack.invoke.world_actions == actions, "恢复期绝不重复世界动作"
    for event, payload in stack.events.rows[-6:]:
        await entry.on_task_event(event, payload)
    assert entry is not None
    assert after.state.value in {"PAUSED", "REPLANNING"}


# ------------------------------------------------------------------ L：跨会话隔离


async def test_l_cross_session_isolation() -> None:
    stack = Stack()
    await stack.say(group_event("帮我找附近的一块橡木", user_id=USER_A, group_id=GROUP))
    group_task = await current_group(stack)
    assert group_task is not None

    # 同一个用户在**另一个群**里说话：看不到那个任务，也就不该被它影响
    other = group_event("任务怎么样了", user_id=USER_A, group_id=GROUP + 1)
    assert await stack.say(other) is False, "别的会话里没有这个任务 → 不进任务入口"

    # 同一用户再开新任务：先告诉他手上还有一个（§十七）
    claimed = await stack.say(private_event("帮我找附近的一块橡木", user_id=USER_A))
    assert claimed is True
    assert "还有一个任务正在处理中" in stack.bot.api.last()
    assert await stack.runtime.current(f"private:{USER_A}") is None, "绝不开第二个任务"


async def test_dedup_sends_each_event_once() -> None:
    stack = Stack()
    await stack.say(private_event("帮我找附近的一块橡木"))
    await stack.say(private_event("确认"))
    record = await current(stack)
    assert record is not None
    payload = {
        "task_id": record.task_id,
        "event_seq": record.event_seq + 5,
        "session_id": record.session_id,
        "state": record.state.value,
        "tool": "minecraft_dig",
    }
    before = len(stack.bot.api.sent)
    await stack.notify("task.step_started", payload)
    await stack.notify("task.step_started", payload)
    await stack.notify("task.step_started", dict(payload, event_seq=payload["event_seq"] - 1))
    after = stack.bot.api.sent
    assert len(after) == before + 1, "同一个事件只发一次，更旧的序号也丢掉"
    assert "开始挖" in after[-1][2]


async def test_event_bus_claim_stops_later_handlers() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def first(_event: Any) -> bool:
        seen.append("first")
        return True

    async def second(_event: Any) -> None:
        seen.append("second")

    bus.on("message", first)
    bus.on("message", second)
    claimed = await bus.emit(private_event("帮我找附近的一块橡木"))
    assert claimed is True and seen == ["first"], "认领后后面的处理器不再跑"
