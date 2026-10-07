"""Phase 5B：QQ 任务入口 + 统一任务控制 + 任务事件 → QQ 通知。

**QQ 只是入口，不是执行器**（§一）：这里做的全部事情就是

    QQ 事件 → 身份规范化 → 意图判断 → 现有 Planner / TaskRuntime
            → 展示现有 Plan summary / 翻译现有 Task 事件

任何真实世界动作仍然走 TaskRuntime → Policy → Agent Bridge → MinecraftService，
本模块**不碰** Minecraft 工具、不自己维护第二套任务状态机、不自己实现确认令牌。

三条硬规则：

* **身份只用稳定平台标识**（§四）：``user_id`` 是 QQ 号，``session_id`` 复用项目既有
  约定 ``private:<uid>`` / ``group:<gid>``；昵称/群名片只用来措辞，绝不作为身份。
* **归属**（§5.2/§十六）：只有任务发起人能确认/暂停/继续/停止；别人（哪怕同群）一律
  拒绝，而且拒绝**不改任务状态**。
* **事件幂等**（§十五）：按 ``(task_id, event_seq)`` 去重；同一件事绝不发第二遍。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.tasks.intent import TaskIntentDetector
from app.tasks.models import (
    TASK_AUTHORIZATION_EXPIRED,
    TASK_CANCELLED,
    TASK_CONFIRMATION_REQUIRED,
    TASK_CREATED,
    TASK_EXPIRED,
    TASK_FAILED,
    TASK_PAUSED,
    TASK_PLAN_READY,
    TASK_RECOVERED,
    TASK_REPLANNING,
    TASK_RESUMED,
    TASK_STARTED,
    TASK_STEP_FAILED,
    TASK_STEP_STARTED,
    TASK_STEP_SUCCEEDED,
    TASK_STEP_WAITING,
    TASK_SUCCEEDED,
    TaskState,
)
from app.tasks.turn import QQ_REPLIES, TaskReplies, TaskTurnHandler

log = logging.getLogger("CatooBot.Tasks.QQ")

#: 入口标签（进 TaskRecord.source，§二十九/§三十）
PLATFORM = "qq"

#: 事件 → 一句人话（§十四：简短、不带内部信息）
STEP_TEXTS: dict[str, str] = {
    "minecraft_move_to": "🚶 正在过去……",
    "minecraft_dig": "⛏️ 到地方了，开始挖。",
    "minecraft_equip": "🖐️ 先换一下手里的东西。",
    "minecraft_place": "🧱 开始放方块。",
    "minecraft_pickup_item": "📦 正在捡东西……",
    "minecraft_inventory": "🔎 最后检查一下背包。",
    "minecraft_dropped_items": "👀 看看地上有什么。",
}

#: 失败分类 → 人话（§二十六；不认识的一律说"没做成"，绝不漏 traceback）
FAILURE_TEXTS: dict[str, str] = {
    "AUTHORIZATION": "这件事现在的许可不够，需要重新确认。",
    "VALIDATION": "这个计划我这边校验没过。",
    "TARGET_LOST": "目标不见了，得重新找。",
    "WORLD_CHANGED": "世界里的东西变了，原计划作废。",
    "ACTION_FAILED": "动手的时候没成功。",
    "TIMEOUT": "等得太久，我先把动作停了。",
    "CANCELLED": "动作被取消了。",
    "OFFLINE": "罐头现在不在线，暂时做不了。",
    "BUSY": "我手上还有别的事，先停一下。",
    "RUNTIME_RESTART": "我重启过，之前那一步不再有效。",
    "STALLED": "一直没进展，我先停下了。",
    "VERIFICATION": "最后检查背包时没对上，我不敢说做完了。",
    "INTERNAL": "我这边出了点问题。",
}

#: 直接回复的重试退避（用户正等着，只补一次短的）
REPLY_RETRY_DELAYS: tuple[float, ...] = (1.5,)
#: 通知的重试退避（后台发，NapCat 重连通常几秒；有界，绝不无限重试）
NOTIFY_RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0, 8.0)

#: 状态 → QQ 用的一句短话（事件映射里的补充）
_NOTIFY_SKIP = frozenset({TASK_CREATED, TASK_PLAN_READY, TASK_STEP_WAITING, TASK_STEP_SUCCEEDED})

#: 入口的**同步回复**已经说清楚的事件（其余事件照发）—— 既不去重也不漏发：
#: 只有"回复本身就是这条事件"的才跳过（§十四/§十五）。
ACTION_COVERS: dict[str, frozenset[str]] = {
    "created": frozenset({TASK_CREATED, TASK_PLAN_READY, TASK_CONFIRMATION_REQUIRED}),
    "replanned": frozenset(
        {TASK_CREATED, TASK_PLAN_READY, TASK_REPLANNING, TASK_CONFIRMATION_REQUIRED}
    ),
    "replan_failed": frozenset({TASK_CREATED, TASK_PLAN_READY, TASK_CONFIRMATION_REQUIRED}),
    "confirmed": frozenset({TASK_STARTED}),
    "paused": frozenset({TASK_PAUSED}),
    "resumed": frozenset({TASK_RESUMED}),
    "cancelled": frozenset({TASK_CANCELLED}),
}


@dataclass(frozen=True)
class QQIdentity:
    """规范化的 QQ 身份（§四/§二十一）：稳定 id + 既有会话身份，昵称只用于措辞。"""

    user_id: str
    session_id: str
    conversation_id: str | None = None
    display_name: str = ""
    platform: str = PLATFORM

    @property
    def group_id(self) -> int | None:
        return int(self.conversation_id) if self.conversation_id else None

    @property
    def is_group(self) -> bool:
        return self.conversation_id is not None

    @classmethod
    def from_event(cls, event: Any) -> QQIdentity:
        """从 QQ 事件里取**稳定**身份（绝不用昵称/群名片当 id）。"""
        user_id = str(getattr(event, "user_id", "") or "").strip()
        group_id = getattr(event, "group_id", None) if getattr(event, "is_group", False) else None
        display_name = ""
        sender = getattr(event, "sender", None)
        if sender is not None:
            display_name = str(getattr(sender, "display_name", "") or "")
        if group_id is not None:
            return cls(
                user_id=user_id,
                session_id=f"group:{group_id}",
                conversation_id=str(group_id),
                display_name=display_name,
            )
        return cls(user_id=user_id, session_id=f"private:{user_id}", display_name=display_name)


#: 开头连续的 @提及（OneBot 的 text 里可能是 "@123456" 或 "@昵称"）
_LEADING_MENTION = re.compile(r"^(\s*@[^\s]+\s*)+")


def _strip_mention(text: str, self_id: str = "") -> str:
    """去掉开头的 @她（身份判定已经用稳定 id 做完了，这里只为拿到正文）。"""
    cleaned = str(text or "")
    if self_id and f"@{self_id}" in cleaned:
        cleaned = cleaned.replace(f"@{self_id}", " ", 1)
    return _LEADING_MENTION.sub("", cleaned).strip()


def session_target(session_id: str) -> tuple[str, int] | None:
    """``private:<uid>`` / ``group:<gid>`` → ("private"|"group", id)；认不出来返回 None。"""
    kind, _, raw = str(session_id or "").partition(":")
    if not raw:
        return None
    try:
        return (kind, int(raw)) if kind in {"private", "group"} else None
    except ValueError:
        return None


class QQTaskEntry:
    """QQ 侧的任务入口（订阅消息事件 + 任务事件）。"""

    def __init__(
        self,
        bot: Any,
        *,
        runtime: Any,
        observe: Any,
        detector: TaskIntentDetector | None = None,
        replies: TaskReplies | None = None,
        source: str = PLATFORM,
        logger: Any = None,
    ) -> None:
        self.bot = bot
        self.runtime = runtime
        self.detector = detector or TaskIntentDetector()
        self.replies = replies or QQ_REPLIES
        self.source = str(source or PLATFORM)
        self._log = logger or log
        self.handler = TaskTurnHandler(
            runtime,
            observe=observe,
            detector=self.detector,
            replies=self.replies,
            source=self.source,
        )
        #: §十五：task_id → 已经发过的最大 event_seq（单调，重复的一律丢掉）
        self._reported: OrderedDict[str, int] = OrderedDict()
        #: 事件重放/竞态兜底：同一个 (task_id, seq) 绝不发两次
        self._seen: set[tuple[str, int]] = set()
        #: 正在同步处理用户消息（这期间产生的事件先排队，回复发出去之后再处理：
        #: 既不与入口回复重复，也不会漏掉"确认后立刻失败"这种坏消息）
        self._handling = 0
        self._queued: list[tuple[str, dict[str, Any]]] = []
        #: 后台通知任务（asyncio 只持弱引用，必须自己留一份）
        self._tasks: set[Any] = set()
        self._max_memory = 512

    # ------------------------------------------------------------ 消息入口

    async def on_message(self, event: Any) -> bool:
        """事件总线订阅入口；返回 True = 这条消息归任务管（认领）。"""
        try:
            return await self._handle(event)
        except Exception:  # noqa: BLE001 - 入口绝不把异常抛给事件总线/聊天管线
            self._log.exception("[Task/QQ] 处理消息失败（忽略）")
            return False

    async def _handle(self, event: Any) -> bool:
        if self.runtime is None:
            return False
        text = self._text_of(event)
        if not text:
            return False
        identity = QQIdentity.from_event(event)
        if not identity.user_id:
            return False
        if identity.is_group and not self._addressed(event):
            # 群里没被 @/没接她的话 → 不进任务入口（§二十二：普通群聊照旧）
            return False
        if identity.is_group:
            text = _strip_mention(text, str(getattr(event, "self_id", "") or ""))

        command = self.detector.control_command(text)
        status_query = self.detector.status_query(text)
        wants_task = self.detector.detect(text).is_task
        if wants_task and not command and not status_query:
            blocked = await self._other_session_task(identity)
            if blocked is not None:
                await self._say(
                    identity,
                    self.replies.busy.format(objective=blocked.objective, state=""),
                )
                return True

        self._handling += 1
        try:
            outcome = await self.handler.handle(
                session_id=identity.session_id,
                user_id=identity.user_id,
                text=text,
            )
        finally:
            self._handling -= 1
            self._handling = max(0, self._handling)
        if not outcome.handled or not outcome.reply:
            await self._drain(outcome=None)
            return False
        if outcome.task_id:
            record = await self.runtime.get(outcome.task_id)
            if record is not None:
                self._mark_reported(record.task_id, int(getattr(record, "event_seq", 0) or 0))
        await self._say(identity, outcome.reply + await self._plan_hint(outcome))
        await self._drain(outcome=outcome)
        self._log.info(
            "[Task/QQ] action=%s task=%s state=%s user=%s session=%s",
            outcome.action,
            outcome.task_id or "-",
            outcome.state or "-",
            identity.user_id,
            identity.session_id,
        )
        return True

    def _text_of(self, event: Any) -> str:
        message = getattr(event, "message", None)
        if message is None:
            return ""
        return str(getattr(message, "text", "") or "").strip()

    def _addressed(self, event: Any) -> bool:
        """群里只有 @她 或"回复她那条消息"才进任务入口（§二十二：普通群聊照旧走人格）。"""
        message = getattr(event, "message", None)
        if message is None:
            return False
        self_id = str(getattr(event, "self_id", "") or "")
        if self_id and message.is_mentioned(self_id):
            return True
        return self._replies_to_bot(event)

    def _replies_to_bot(self, event: Any) -> bool:
        """与人格插件同一套判据：OneBot 的 reply 段指向她刚发过的消息。"""
        message = getattr(event, "message", None)
        replies = message.get("reply") if message is not None else None
        if not replies:
            return False
        group_id = str(getattr(event, "group_id", "") or "")
        social = getattr(self.bot, "social", None)
        delivery = getattr(self.bot, "response_delivery", None)
        for segment in replies:
            message_id = getattr(segment, "message_id", None)
            if message_id is None:
                continue
            try:
                if social is not None and social.monitor.is_bot_message_id(
                    group_id, str(message_id)
                ):
                    return True
            except Exception:  # noqa: BLE001 - 查不到就当不是
                pass
            last_sent = getattr(delivery, "last_sent_ids", {}).get(f"group:{group_id}")
            if last_sent is not None and int(message_id) == int(last_sent):
                return True
        return False

    async def _other_session_task(self, identity: QQIdentity) -> Any:
        """这个用户在别的会话里还有没做完的任务吗（§十七）。"""
        try:
            record = await self.runtime.active_for_user(identity.user_id)
        except Exception:  # noqa: BLE001
            return None
        if record is None or record.session_id == identity.session_id:
            return None
        return record

    def _failed_step_message(self, record: Any) -> str:
        if record is None:
            return ""
        step_id = str(getattr(record, "failed_step", "") or "")
        step = record.step(step_id) if step_id else None
        return str(getattr(step, "message", "") or "") if step is not None else ""

    def _allow_medium_enabled(self) -> bool:
        """MEDIUM 总闸（默认关；QQ 绝不绕过它，只是提前把话说清楚）。"""
        try:
            return bool(self.bot.minecraft.config.agent.tools.allow_medium)
        except Exception:  # noqa: BLE001 - 拿不到就按"关着"处理（保守）
            return False

    async def _plan_hint(self, outcome: Any) -> str:
        """计划里有 MEDIUM 步骤、而 MEDIUM 开关是关的 → 建任务时就提醒（§十九/§三十二）。"""
        if str(getattr(outcome, "action", "")) != "created" or not outcome.task_id:
            return ""
        if self._allow_medium_enabled():
            return ""
        try:
            record = await self.runtime.get(outcome.task_id)
        except Exception:  # noqa: BLE001
            return ""
        if record is None:
            return ""
        risky = [step for step in record.steps if str(getattr(step, "risk", "")) == "MEDIUM"]
        if not risky:
            return ""
        return (
            f"\n\n（提醒：里面 {len(risky)} 步需要「允许 MEDIUM 动作」，这个开关现在是关着的 —— "
            "确认后我会在动手那一步停下，一步也不会动世界。要真挖得先在设置里打开它。）"
        )

    # ------------------------------------------------------------ 发送

    async def _say(self, identity: QQIdentity, text: str) -> None:
        """用户消息的直接回复：失败就再试一次（短退避），仍失败只记账。"""
        await self._send(identity, text, delays=REPLY_RETRY_DELAYS)

    async def _send(self, identity: QQIdentity, text: str, *, delays: tuple[float, ...]) -> bool:
        """把一句话发到 QQ。``delays`` = 每次失败之后等多久再试（有界，绝不无限重试）。

        真机踩到过：重启 CatooBot 时，内存里的恢复通知会在 **NapCat 还没重连**的时候就发出去
        （启动日志：`Connection closed → 发送失败 → NapCat connected`），于是"我重启过…"这条
        就永久丢了。通知路径因此带一段有界重试（QQ 通道通常几秒内就回来）。
        """
        message = str(text or "").strip()
        if not message:
            return False
        api = getattr(self.bot, "api", None)
        if api is None:
            return False
        attempts = (0.0, *tuple(delays))
        for index, delay in enumerate(attempts):
            if delay:
                await asyncio.sleep(delay)
            try:
                if identity.is_group and identity.group_id is not None:
                    await api.send_group_msg(identity.group_id, message)
                else:
                    await api.send_private_msg(int(identity.user_id or 0), message)
                return True
            except Exception as exc:  # noqa: BLE001 - 发不出去只记账（她还在跑）
                self._log.warning(
                    "[Task/QQ] 发送失败 session=%s attempt=%d/%d error=%s",
                    identity.session_id,
                    index + 1,
                    len(attempts),
                    exc,
                )
        self._log.error("[Task/QQ] 发送放弃 session=%s", identity.session_id)
        return False

    async def notify(self, session_id: str, text: str) -> None:
        """按会话身份把通知发回去（任务事件用）。"""
        target = session_target(session_id)
        if target is None:
            return
        kind, raw = target
        identity = (
            QQIdentity(user_id="", session_id=session_id, conversation_id=str(raw))
            if kind == "group"
            else QQIdentity(user_id=str(raw), session_id=session_id)
        )
        await self._send(identity, text, delays=NOTIFY_RETRY_DELAYS)

    # ------------------------------------------------------------ 任务事件 → QQ

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        """**同步**事件钩子（TaskRuntime/Bot 直接调它）。

        判定必须在同步时刻做：用户消息正在被处理时把事件排进队列（既不会与入口回复
        重复，也不会漏掉"确认后立刻失败"这种坏消息）；否则丢给后台任务去发。
        """
        name, data = str(event), dict(payload or {})
        if self._handling > 0:
            self._queued.append((name, data))
            return
        self._spawn(self.on_task_event(name, data))

    def _spawn(self, coro: Any) -> None:
        import asyncio

        try:
            task = asyncio.create_task(coro)
        except RuntimeError:  # pragma: no cover - 没有事件循环时安静放弃
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def on_task_event(self, event: str, payload: dict[str, Any]) -> None:
        """异步发送一条任务事件通知（§十三：订阅现有事件，不自己轮询）。"""
        try:
            await self._notify(event, dict(payload or {}))
        except Exception:  # noqa: BLE001 - 通知失败绝不影响任务
            self._log.exception("[Task/QQ] 事件通知失败 event=%s", event)

    async def _drain(self, *, outcome: Any) -> None:
        """把同步处理期间排队的事件发出去（跳过入口回复已经说明的那几条）。"""
        queued, self._queued = self._queued, []
        covered = ACTION_COVERS.get(str(getattr(outcome, "action", "") or ""), frozenset())
        for event, payload in queued:
            if event in covered:
                self._mark_reported(
                    str(payload.get("task_id") or ""), int(payload.get("event_seq") or 0)
                )
                continue
            try:
                await self._notify(event, payload)
            except Exception:  # noqa: BLE001
                self._log.exception("[Task/QQ] 事件通知失败 event=%s", event)

    async def _notify(self, event: str, payload: dict[str, Any]) -> None:
        name = str(event or "")
        if name in _NOTIFY_SKIP:
            return
        task_id = str(payload.get("task_id") or "")
        seq = int(payload.get("event_seq") or 0)
        if not task_id:
            return
        if not self._claim(task_id, seq):
            return
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            return
        record = None
        try:
            record = await self.runtime.get(task_id)
        except Exception:  # noqa: BLE001
            record = None
        text = await self._text_for_event(name, payload, record)
        if not text:
            return
        await self.notify(session_id, text)

    async def _text_for_event(self, event: str, payload: dict[str, Any], record: Any) -> str:
        state = str(payload.get("state") or (record.state.value if record is not None else ""))
        if event == TASK_STARTED:
            return self.replies.confirmed
        if event == TASK_STEP_STARTED:
            tool = str(payload.get("tool") or "")
            return STEP_TEXTS.get(tool, "")
        if event == TASK_CONFIRMATION_REQUIRED:
            if record is None:
                return "需要你确认一下计划。回复「确认」开始。"
            reason = str(getattr(record, "replan_reason", "") or "")
            if int(getattr(record, "plan_version", 1) or 1) > 1 or reason:
                body = self.runtime.replan_summary(record, reason=reason)
                return f"{body}\n回复「确认」继续。"
            summary = self.runtime.summary_of(record)
            headline = self.replies.created.format(summary=summary, version=1, hint="")
            return f"{headline}\n回复「确认」开始。".strip()
        if event == TASK_REPLANNING:
            return (
                "刚才目标发生了变化，原计划已经作废。\n我重新观察了一次，需要你重新确认新的计划。"
            )
        if event == TASK_AUTHORIZATION_EXPIRED:
            return "刚才的操作授权已经过期了。\n计划没有改变，但需要重新确认。"
        if event == TASK_RECOVERED:
            needs_replan = state == TaskState.REPLANNING.value
            tail = "\n我需要重新观察一下，再请你确认新的计划。" if needs_replan else ""
            return f"我重启过，之前那一步的动作已经作废了，不会重做。{tail}"
        if event == TASK_PAUSED:
            return self.replies.paused
        if event == TASK_RESUMED:
            return self.replies.resumed
        if event == TASK_CANCELLED:
            return self.replies.cancelled
        if event == TASK_EXPIRED:
            return "这个任务放太久，已经过期了。"
        if event == TASK_SUCCEEDED:
            return self._success_text(record)
        if event == TASK_FAILED:
            failure = str(payload.get("failure") or (record.failure if record is not None else ""))
            step_message = self._failed_step_message(record)
            if "未获允许" in step_message:
                # §十九：QQ 不是 MEDIUM 白名单 —— 这里要说清"是那个开关挡的"，
                # 而不是含糊地说"许可不够、再确认一次"（再确认也不会放行）。
                return (
                    "这个任务里有需要「允许 MEDIUM 动作」的步骤，而那个开关现在是关的，"
                    "所以我一步都没动手。要真做的话，先在设置里打开它。"
                )
            reason = FAILURE_TEXTS.get(failure, "这次没做成。")
            return f"这件事没成：{reason}"
        if event == TASK_STEP_FAILED:
            failure = str(payload.get("failure") or "")
            if failure in {"WORLD_CHANGED", "TARGET_LOST"}:
                return ""  # 紧接着的 task.replanning 会说明白
            return ""
        return ""

    def _success_text(self, record: Any) -> str:
        gained = ""
        verification = getattr(record, "verification", None) or {}
        delta = verification.get("inventory_delta") if isinstance(verification, dict) else None
        if isinstance(delta, dict):
            parts = [f"{count} 个 {name}" for name, count in delta.items() if int(count or 0) > 0]
            if parts:
                gained = "，已经拿到了 " + "、".join(parts)
        objective = str(getattr(record, "objective", "") or "")
        if gained:
            return f"完成啦{gained}。"
        return f"做完了：{objective}" if objective else "做完了。"

    # ------------------------------------------------------------ 去重（§十五）

    def _claim(self, task_id: str, seq: int) -> bool:
        """这个事件该不该发？（同一个 (task_id, seq) 只发一次；更旧的序号直接丢）"""
        key = (task_id, int(seq))
        if key in self._seen:
            return False
        last = int(self._reported.get(task_id, 0) or 0)
        if seq and seq <= last:
            return False
        self._seen.add(key)
        if len(self._seen) > self._max_memory:
            self._seen = set(list(self._seen)[-self._max_memory :])
        if seq:
            self._mark_reported(task_id, seq)
        return True

    def _mark_reported(self, task_id: str, seq: int) -> None:
        if not task_id:
            return
        current = int(self._reported.get(task_id, 0) or 0)
        if seq >= current:
            self._reported[task_id] = int(seq)
        self._reported.move_to_end(task_id)
        while len(self._reported) > self._max_memory:
            self._reported.popitem(last=False)
