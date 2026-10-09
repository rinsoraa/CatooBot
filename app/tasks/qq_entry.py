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
from app.tasks.proposal_service import should_record_user_proposal
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


async def send_qq(
    bot: Any,
    identity: QQIdentity,
    text: str,
    *,
    delays: tuple[float, ...],
    logger: Any = None,
) -> bool:
    """把一句话发到 QQ；``delays`` = 每次失败之后等多久再试（有界，绝不无限重试）。

    真机踩到过：重启 CatooBot 时，内存里的恢复通知会在 **NapCat 还没重连**的时候就发出去
    （启动日志：`Connection closed → 发送失败 → NapCat connected`），于是
    "我重启过…"这条就永久丢了。发消息的路径因此都带一段有界重试。
    """
    message = str(text or "").strip()
    if not message:
        return False
    api = getattr(bot, "api", None)
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
        except Exception as exc:  # noqa: BLE001 - 发不出去只记账
            if logger is not None:
                logger.warning(
                    "[QQ] 发送失败 session=%s attempt=%d/%d error=%s",
                    identity.session_id,
                    index + 1,
                    len(attempts),
                    exc,
                )
    if logger is not None:
        logger.error("[QQ] 发送放弃 session=%s", identity.session_id)
    return False


def qq_text_of(event: Any) -> str:
    """事件里的纯文本正文（去空白）。"""
    message = getattr(event, "message", None)
    if message is None:
        return ""
    return str(getattr(message, "text", "") or "").strip()


def qq_addressed(bot: Any, event: Any) -> bool:
    """群里只有 @她 或"回复她那条消息"才算在跟她说话（§二十二：普通群聊照旧走人格）。"""
    message = getattr(event, "message", None)
    if message is None:
        return False
    self_id = str(getattr(event, "self_id", "") or "")
    if self_id and message.is_mentioned(self_id):
        return True
    return qq_replies_to_bot(bot, event)


def qq_replies_to_bot(bot: Any, event: Any) -> bool:
    """与人格插件同一套判据：OneBot 的 reply 段指向她刚发过的消息。"""
    message = getattr(event, "message", None)
    replies = message.get("reply") if message is not None else None
    if not replies:
        return False
    group_id = str(getattr(event, "group_id", "") or "")
    social = getattr(bot, "social", None)
    delivery = getattr(bot, "response_delivery", None)
    for segment in replies:
        message_id = getattr(segment, "message_id", None)
        if message_id is None:
            continue
        try:
            if social is not None and social.monitor.is_bot_message_id(group_id, str(message_id)):
                return True
        except Exception:  # noqa: BLE001 - 查不到就当不是
            pass
        last_sent = getattr(delivery, "last_sent_ids", {}).get(f"group:{group_id}")
        if last_sent is not None and int(message_id) == int(last_sent):
            return True
    return False


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
        # Phase 7D §四/§八：LIFE 计划的「批准」（第一道门）—— 必须在任务控制词之前拦截，
        # 否则「批准」会被当成普通聊天漏过去。没在处理控制命令时才看计划层。
        if not command:
            consumed = await self._handle_agent_plan(identity, text)
            if consumed:
                return True
        # Phase 7D §八场景 B：「跟着我」→ 身份桥解析目标 → 跟随计划 → 待确认任务。
        # 它不是资源任务（没有方块目标），所以走计划层而不是 detector.detect。
        if not command and not status_query and self.detector.detect_follow(text):
            handled = await self._handle_follow_request(identity, text)
            if handled:
                return True
        wants_task = self.detector.detect(text).is_task
        if wants_task and not command and not status_query:
            blocked = await self._other_session_task(identity)
            if blocked is not None:
                await self._say(
                    identity,
                    self.replies.busy.format(objective=blocked.objective, state=""),
                )
                return True
        # Phase 7D 修订 2：跟随任务的「确认」在执行前**复核身份仍然有效**
        # （计划到确认之间绑定可能被撤销/冲突 —— 复核失败就取消任务，绝不带着旧目标跑）。
        if command == "confirm":
            veto = await self._verify_follow_identity(identity)
            if veto is not None:
                await self._say(identity, veto)
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
        # Phase 7C §四/§八：**旁路**记一份 USER 提案（窄规则，见 should_record_user_proposal）。
        # 放在这里而不是"建了任务之后"：既有的任务检测器不把「跟着我」认成任务，
        # 而 §八 恰恰要求那句话也要走可信身份桥解析目标。
        await self._record_proposal(identity, text, outcome)
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

    async def _record_proposal(self, identity: QQIdentity, text: str, outcome: Any) -> None:
        """Phase 7C §四：把这次**用户请求**旁路记成一份提案（source=USER）。

        **它不参与**任务链的决策：任务照旧由既有 5A/5B 链创建与执行，这里只是把
        "用户想要什么 / 需要哪些能力 / 目标玩家能不能可信解析"留成可审计的一条。
        因此任何失败都只吞掉 —— 绝不回头影响回复或任务（§四/§十三）。

        命中的条件很窄（``should_record_user_proposal``）：真的建了任务，或这句话
        明确在指某个玩家。普通闲聊与提问不会留下提案。
        """
        if not should_record_user_proposal(text, str(getattr(outcome, "action", ""))):
            return
        service = getattr(self.bot, "proposals", None)
        if service is None or not getattr(service, "enabled", False):
            return
        try:
            await service.record_user_request(
                objective=str(text),
                user_id=str(identity.user_id),
                session_id=str(identity.session_id),
                server_id=self._server_id(),
                task_id=str(getattr(outcome, "task_id", "") or ""),
            )
        except Exception:  # noqa: BLE001 - 记账失败绝不影响用户任务
            self._log.debug("[Task/QQ] 提案记账失败（忽略）", exc_info=True)

    # --------------------------------------------- Phase 7D：计划层入口（全部可失败）

    async def _handle_agent_plan(self, identity: QQIdentity, text: str) -> bool:
        """LIFE 计划的「批准」（修订 1 第一道门）。返回 True = 这条消息归计划层管。"""
        plans = getattr(self.bot, "agent_plans", None)
        if plans is None or not callable(getattr(plans, "handle_qq", None)):
            return False
        try:
            out = await plans.handle_qq(
                text=str(text),
                user_id=str(identity.user_id),
                session_id=str(identity.session_id),
            )
        except Exception:  # noqa: BLE001 - 计划层失败不拖垮任务/聊天
            self._log.debug("[AgentPlan] 批准处理失败（忽略）", exc_info=True)
            return False
        if out is None:
            return False
        await self._say(identity, str(out.get("reply") or "好的。"))
        return True

    async def _handle_follow_request(self, identity: QQIdentity, text: str) -> bool:
        """「跟着我」（§八场景 B）：身份桥解析 → 跟随计划 → 待确认任务（不执行）。

        目标**只**来自当前说话者的 VERIFIED 绑定（绝不从文本抠玩家名）；
        解析不成立就如实拒绝 —— 任何失败都只影响这一条消息。
        """
        plans = getattr(self.bot, "agent_plans", None)
        if plans is None or not callable(getattr(plans, "plan_follow_from_user", None)):
            return False
        proposals = getattr(self.bot, "proposals", None)
        resolver = getattr(proposals, "resolve_target", None)
        if not callable(resolver):
            await self._say(identity, "我现在解析不了你是谁，先绑定一下身份再说？")
            return True
        try:
            target = await resolver(user_id=str(identity.user_id), server_id=self._server_id())
            out = await plans.plan_follow_from_user(
                objective=str(text),
                user_id=str(identity.user_id),
                session_id=str(identity.session_id),
                target=target,
            )
        except Exception:  # noqa: BLE001 - 跟随入口绝不把异常抛给事件总线
            self._log.exception("[Task/QQ] 跟随计划失败（忽略）")
            await self._say(identity, "这条请求我处理不了，等会儿再试。")
            return True
        action = str(out.get("action") or "")
        if action == "created":
            record = out.get("record")
            summary = str(out.get("reply") or "")
            risk = dict(getattr(out.get("plan"), "risk_summary", {}) or {})
            duration = risk.get("duration_note")
            self._log.info(
                "[Task/QQ] follow plan created task=%s plan=%s target=%s",
                getattr(record, "task_id", "-"),
                getattr(out.get("plan"), "plan_id", "-"),
                (out.get("plan").target.player_name if out.get("plan") is not None else "-"),
            )
            # §八场景 A：明确展示目标玩家、操作内容与持续时间限制（修订 2）
            target_name = out.get("plan").target.player_name
            reply = f"罐头准备这样做：跟随 {target_name}（来自已验证的身份绑定）。"
            if duration:
                reply += f"\n期限：{duration}"
            reply += "\n需要你确认后我才会动。\n回复「确认」开始。"
            await self._say(identity, reply if not summary else f"{reply}")
            return True
        await self._say(identity, str(out.get("reply") or "这件事现在做不了。"))
        return True

    async def _verify_follow_identity(self, identity: QQIdentity) -> str | None:
        """确认执行前复核跟随目标身份（7D.1 P1-2 + 7D.2 P1-3，**fail-closed + 取消**）。

        返回 ``None`` = 放行（只有两种情况：当前没有待确认任务；或能**证明**它是
        普通非跟随任务）。其余一切"读不到 / 缺失 / 不一致 / 异常"都否决**并走既有
        安全路径取消待确认任务**（7D.2 §3.2.2：只拒绝不取消会留下一个永远无法确认、
        也无法通过身份复核的任务挂着）。

        * 取消成功 → 文案说明已取消；取消失败 → 如实说"取消未能确认"，**绝不**谎称已取消；
        * 无论取消是否成功，本次都不进入 confirm_and_start() / 不派发 follow；
        * 读取 Task 本身失败时记录"任务无法读取，无法执行取消"，不把拒绝误报成取消成功；
        * 复核依据是**当前 Task 的冻结步骤**（不依赖 AgentPlan 是否存在来判定任务类型），
          至少核对 VERIFIED 状态、player_uuid、server_id 与冻结的 username。
        """
        runtime = self.runtime
        current_getter = getattr(runtime, "current", None)
        if not callable(current_getter):
            self._log.warning("[Task/QQ] follow re-check: runtime 不可读，确认被拒绝")
            return await self._follow_veto_message("任务状态读不出来", task_id="")
        try:
            record = await current_getter(identity.session_id)
        except Exception:
            self._log.exception("[Task/QQ] follow re-check: current() 失败，确认被拒绝")
            return await self._follow_veto_message("任务状态读不出来", task_id="")
        if record is None:
            return None  # 当前会话没有任务 → 「确认」是普通聊天（既有链处理）
        state = str(getattr(getattr(record, "state", None), "value", "") or "")
        if state != "PENDING_CONFIRMATION":
            return None  # 不是待确认 → 既有链自己处理（busy 等）
        task_id = str(getattr(record, "task_id", "") or "")
        # ---- 从**冻结步骤**判定任务类型（不依赖 AgentPlan 是否存在）
        plan_obj = getattr(record, "plan", None)
        steps = list(getattr(plan_obj, "steps", None) or [])
        if not steps:
            self._log.exception("[Task/QQ] follow re-check: 冻结步骤读不到 task=%s", task_id)
            return await self._follow_veto_message(
                "计划步骤读不出来", task_id=task_id, runtime=runtime
            )
        follow_steps = [
            s for s in steps if str(getattr(s, "tool", "")) == "minecraft_follow_player"
        ]
        if not follow_steps:
            return None  # 能证明是普通非跟随任务 → 既有链处理（不触发 follow 专用取消）
        frozen = follow_steps[0]
        frozen_username = str((getattr(frozen, "arguments", None) or {}).get("username", "") or "")
        if not frozen_username:
            self._log.warning("[Task/QQ] follow re-check: 冻结 username 缺失 task=%s", task_id)
            return await self._follow_veto_message(
                "计划的目标玩家读不出来", task_id=task_id, runtime=runtime
            )
        # ---- 跟随任务：缺任何复核依据都拒绝 + 取消（7D.2 §3.2.2）
        plans = getattr(self.bot, "agent_plans", None)
        plan_getter = getattr(plans, "plan_for_task", None)
        if plans is None or not callable(plan_getter):
            self._log.warning("[Task/QQ] follow re-check: AgentPlan 层不可用 task=%s", task_id)
            return await self._follow_veto_message(
                "计划关联读不出来", task_id=task_id, runtime=runtime
            )
        try:
            agent_plan = await plan_getter(task_id)
        except Exception:
            self._log.exception("[Task/QQ] follow re-check: plan_for_task 失败 task=%s", task_id)
            return await self._follow_veto_message(
                "计划关联读不出来", task_id=task_id, runtime=runtime
            )
        if agent_plan is None:
            self._log.warning("[Task/QQ] follow re-check: 无计划关联 task=%s", task_id)
            return await self._follow_veto_message(
                "计划关联读不出来", task_id=task_id, runtime=runtime
            )
        target = getattr(agent_plan, "target", None)
        plan_uuid = str(getattr(target, "player_uuid", "") or "")
        plan_server = str(getattr(target, "server_id", "") or "")
        if not plan_uuid:
            self._log.warning("[Task/QQ] follow re-check: 计划目标 UUID 缺失 task=%s", task_id)
            return await self._follow_veto_message(
                "计划的目标身份不完整", task_id=task_id, runtime=runtime
            )
        # ---- 当前服务器身份必须可靠（跨服务器同名玩家绝不当成当前目标）
        server_now = self._server_id()
        if not server_now:
            self._log.warning("[Task/QQ] follow re-check: 当前服务器身份不可确认 task=%s", task_id)
            return await self._follow_veto_message(
                "当前服务器身份确认不了", task_id=task_id, runtime=runtime
            )
        # ---- 可信身份桥解析
        proposals = getattr(self.bot, "proposals", None)
        resolver = getattr(proposals, "resolve_target", None)
        if not callable(resolver):
            self._log.warning("[Task/QQ] follow re-check: 身份解析不可用 task=%s", task_id)
            return await self._follow_veto_message(
                "身份解析读不出来", task_id=task_id, runtime=runtime
            )
        try:
            resolved = await resolver(user_id=str(identity.user_id), server_id=server_now)
        except Exception:
            self._log.exception("[Task/QQ] follow re-check: 身份解析失败 task=%s", task_id)
            resolved = None
        uuid_now = str(getattr(resolved, "player_uuid", "") or "")
        status_now = str(getattr(resolved, "status", "") or "")
        server_resolved = str(getattr(resolved, "server_id", "") or "")
        name_now = str(getattr(resolved, "player_name", "") or "")
        # ---- 全部一致才放行：VERIFIED + uuid 相同 + server 一致 + username 与冻结参数一致
        consistent = (
            status_now == "VERIFIED"
            and uuid_now == plan_uuid
            and server_resolved == plan_server
            and server_now == plan_server
            and name_now == frozen_username
        )
        if consistent:
            return None
        reason = (
            f"identity={status_now or 'unavailable'} uuid_match={uuid_now == plan_uuid}"
            f" server_match={server_resolved == plan_server}"
            f" name_match={name_now == frozen_username}"
        )
        self._log.warning(
            "[Task/QQ] follow re-check FAILED task=%s %s expected_uuid=…%s server=%s",
            task_id,
            reason,
            plan_uuid[-4:],
            plan_server,
        )
        return await self._follow_veto_message(
            "你的 Minecraft 身份绑定和计划对不上了（可能已解除、换号或换了服务器）",
            task_id=task_id,
            runtime=runtime,
        )

    async def _follow_veto_message(
        self, reason: str, *, task_id: str = "", runtime: Any = None
    ) -> str:
        """fail-closed 的统一否决 + **取消**（7D.2 P1-3）。

        * 有 task_id（跟随任务已确认身份）→ 走既有 ``TaskRuntime.cancel()`` 取消待确认任务；
          取消失败 → 如实说"取消未能确认"，绝不伪称已取消；
        * 没有 task_id（连任务都读不到）→ 明确记录"任务无法读取，无法执行取消"；
        * 无论取消结果如何，都不放行 —— 调用方收到非 None 即拦截「确认」。
        """
        self._log.warning("[Task/QQ] follow re-check vetoed: %s", reason)
        if not task_id:
            self._log.warning("[Task/QQ] follow re-check: 任务无法读取，无法执行取消（只拒绝确认）")
            return (
                f"开始之前我需要再核对一次你的 Minecraft 身份，但{reason}，"
                "这次「确认」我先不执行。请稍后再试或重新绑定。"
            )
        cancelled = False
        cancel = getattr(runtime, "cancel", None)
        if callable(cancel):
            try:
                await_cancel = cancel(task_id, reason="follow identity re-check failed")
                # cancel 可能是同步的也可能是异步的（测试桩/真 runtime 都有）
                if hasattr(await_cancel, "__await__"):
                    await await_cancel
                cancelled = True
            except Exception:
                self._log.exception(
                    "[Task/QQ] follow re-check: 取消失败 task=%s（确认仍被拒绝）", task_id
                )
        if cancelled:
            self._log.info("[Task/QQ] follow re-check: 已取消待确认跟随任务 task=%s", task_id)
            return (
                f"开始之前我需要再核对一次你的 Minecraft 身份，但{reason}，"
                "这个跟随任务我取消了。请稍后再试或重新绑定。"
            )
        return (
            f"开始之前我需要再核对一次你的 Minecraft 身份，但{reason}，"
            "这次「确认」我先不执行，跟随任务暂时停不下来（取消未能确认）。"
            "请稍后再试一次「停止」。"
        )

    def _server_id(self) -> str:
        """当前连接到的服务器 id（拿不到就空 —— 身份解析会如实记为"没有绑定"）。

        复用 5C 的身份桥口径（``edition|host|port|world_key`` 的稳定摘要），
        绝不把 ``127.0.0.1:25565`` 当成永久身份。
        """
        memory = getattr(self.bot, "minecraft_memory", None)
        probe = getattr(memory, "server_id", None)
        if callable(probe):
            try:
                found = str(probe() or "")
                if found:
                    return found
            except Exception:  # noqa: BLE001 - 只读探针，失败就退回本地推导
                pass
        service = getattr(self.bot, "minecraft", None)
        try:
            snapshot = service.snapshot() if service is not None else None
        except Exception:  # noqa: BLE001
            return ""
        connection = dict((snapshot or {}).get("connection") or {})
        from app.integrations.minecraft.identity import server_identity

        return server_identity(connection.get("host"), connection.get("port")).server_id

    def _text_of(self, event: Any) -> str:
        return qq_text_of(event)

    def _addressed(self, event: Any) -> bool:
        return qq_addressed(self.bot, event)

    def _replies_to_bot(self, event: Any) -> bool:
        return qq_replies_to_bot(self.bot, event)

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
        """本入口的发消息路径（薄封装；重试语义见 :func:`send_qq`）。"""
        return await send_qq(self.bot, identity, text, delays=delays, logger=self._log)

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
