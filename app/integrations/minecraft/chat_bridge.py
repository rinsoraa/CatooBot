"""Minecraft 玩家聊天 → 角色对话（Phase 4A §十六-§二十三）。

游戏里玩家说话（``minecraft.chat``）此前只进沙盒外部事件链（世界认知/行为活动）。
本模块是**第二个消费者**：把它当成一次真正的 **USER 回合**交给
:class:`~app.character.runtime.CharacterRuntime` —— 于是游戏内玩家可以用自然语言
触发已有的 Minecraft Tools（SAFE 不限，LOW 需要可信玩家 + USER 回合）。

两条消费者**并存、互不替代**（§十六/§二十九）：

    minecraft.chat
      ├── sandbox.submit_external（世界认知 / 行为活动，插件里做）
      └── MinecraftChatBridge → CharacterRuntime.respond(turn_origin=USER) → 游戏内回复

硬门禁（§三十）：**罐头自己的发言绝不进自己的 LLM 回合**，否则「她说话 → 收到自己的
聊天 → 模型 → 再说话」会无限循环。判定用 runtime 上报的 username 与镜像里的
``connection.username`` 双比对（拿不到自己的名字时，宁可不回）。

本模块不碰 Mineflayer / Bridge HTTP：发言走 ``MinecraftService.send_chat``（Action Runtime）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.character.runtime import CharacterTurnMode
from app.character.turn import TurnOrigin
from app.integrations.minecraft.agent import PLAYER_KEY
from app.integrations.minecraft.identity_context import IdentityResolution
from app.tasks.agent_plan import PlanTarget
from app.tasks.agent_planner import detect_shape
from app.tasks.craft_intent import (
    CRAFT_MAX_BATCHES,
    CraftRequest,
    display_name_of,
    parse_craft_request,
)
from app.tasks.intent import (
    TaskIntentDetector,
    WoodRequest,
    mentions_wood,
    parse_wood_request,
)
from app.tasks.turn_context import build_turn_context
from app.tasks.turn_decision import (
    REASON_ENGINE_MISSING,
    TurnDecisionInterpreter,
    TurnDecisionKind,
    TurnDecisionOutcome,
)
from app.tasks.wood_intent import Polarity, WoodIntent, WoodIntentInterpreter
from app.tools.models import RouteTarget, RouteTrace, ToolCall, new_route_id
from app.tools.policy import TurnBudget

if TYPE_CHECKING:  # pragma: no cover
    from app.core.bot import Bot
    from app.integrations.minecraft.events import MinecraftBridgeEvent
    from app.integrations.minecraft.service import MinecraftService

log = logging.getLogger("CatooBot.Minecraft.Chat")

#: Phase 8D：明确拒绝（不支持的树种/歧义）也必须有请求语气才算「在让罐头动手」，
#: 否则会把「我昨天砍了两根竹子」这类闲聊当成指令。

#: 会话命名（§十八）：Minecraft 世界有自己的会话，绝不塞进 QQ 的 private:/group:
SESSION_PREFIX = "minecraft"
#: 排队上限：短时间内大量聊天不会无限堆积（每条都在独立任务里跑）
MAX_INFLIGHT = 4
#: 一次回复最多发几行（任务确认摘要按行发，但不能无限刷屏）
MAX_CHAT_LINES = 10
#: Phase 8C：与任务入口**同一套**确定性识别器（不新增第二套意图判断，也不额外调模型）
_DETECTOR = TaskIntentDetector()


def chat_session_id(host: str | None, port: int | None, username: str) -> str:
    """会话名：``minecraft:{host}:{port}:{username}``（缺服务器信息时只用用户名）。"""
    if host and port:
        return f"{SESSION_PREFIX}:{host}:{port}:{username}"
    return f"{SESSION_PREFIX}:{username}"


#: 明确的任务控制说法（§4.3）：以这些词开头的消息一律**交回**任务入口，Phase 8D
#: Follow-up 2 的否定分支绝不截获它们。这里只用于「让路」，不会启动任何动作。
_WOOD_CONTROL_PREFIXES = (
    "取消",
    "算了",
    "确认",
    "暂停",
    "继续",
    "恢复",
    "停止",
    "停下",
    "不查了",
    "别查了",
    "不用查",
    "不用继续",
    "不用找",
)


class _WoodRoute(StrEnum):
    """木材消息的**显式**路由结果（Phase 8E Follow-up §4.1）。

    修复前 ``None`` 同时表示「交回任务入口」和「由普通聊天处理」：模型把木材消息判成
    QUERY / NARRATION / OTHER 后仍会落回旧 ``TaskTurnHandler._create()`` →
    ``plan_resource_task()``。这里把语义分开，任何一种都不再靠 ``None`` 猜。
    """

    NOT_WOOD = "not_wood"
    HANDLED = "handled"
    NORMAL_CHAT = "normal_chat"
    DELEGATE_CONTROL = "delegate_control"
    WOOD_POSITIVE_REQUEST = "wood_positive_request"
    SAFE_CLARIFY = "safe_clarify"


def _item_counts(raw: Any) -> dict[str, int]:
    """把冻结计划里的增量/消耗映射整理成「裸物品名 → 正整数」（只读、绝不猜）。"""
    counts: dict[str, int] = {}
    if not isinstance(raw, Mapping):
        return counts
    for key, value in raw.items():
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            continue
        if count > 0:
            counts[str(key)] = count
    return counts


@dataclass(frozen=True)
class _TurnReply:
    """桥接层的回合结论（Phase 8E Follow-up 2 §4.1）。

    ``reply`` 非空 = 本回合已由桥接层回答完毕（任务/木材/澄清），角色层不参与；
    ``reply`` 为空 = 交给角色层生成，但必须按 ``mode`` 的**单回合能力模式**执行：
    ``CHAT_ONLY`` 时角色层不得启动 Agent、不得进入 ToolOrchestrator。
    """

    reply: str | None = None
    mode: CharacterTurnMode = CharacterTurnMode.NORMAL


@dataclass(frozen=True)
class _WoodRouteDecision:
    """一次木材路由的结论：``route`` 决定后续控制流，``reply`` 是已生成的回复。"""

    route: _WoodRoute
    reply: str | None = None


class MinecraftChatBridge:
    """``minecraft.chat`` 事件 → USER 回合 → 游戏内回复。"""

    def __init__(self, bot: Bot, service: MinecraftService) -> None:
        self.bot = bot
        self.service = service
        self._inflight = 0
        #: Phase 8E：木材意图解释器（**唯一决策源**）。可由装配/测试注入；
        #: 未注入时用既有 AIEngine 惰性构造（不新建 provider 或凭据）。
        self.wood_intent: Any = None
        #: Phase 8E Follow-up 3：**统一**回合决策解释器（所有 MC 自然语言消息先经它）。
        #: 同样支持注入；未注入时用同一个既有 AIEngine 惰性构造。
        self.turn_decisions: Any = None

    # ------------------------------------------------------------ 配置/门禁

    @property
    def enabled(self) -> bool:
        return bool(self.service.enabled and self.service.config.agent.chat.enabled)

    def self_username(self) -> str:
        """罐头自己的 MC 名字（镜像里的 connection.username）。"""
        try:
            return str(self.service.snapshot()["connection"].get("username") or "").strip()
        except Exception:  # noqa: BLE001 - 镜像坏了也不该让聊天崩
            return ""

    def is_self_speech(self, username: str) -> bool:
        """§三十：她自己发的聊天绝不能回到她自己的 LLM 回合。"""
        mine = self.self_username()
        if not username:
            return True
        if not mine:
            # 拿不到自己的名字时保守处理：宁可不回，也不冒无限循环的风险
            log.info("[MC Chat] 跳过（镜像里还没有自己的 username）：%s", username)
            return True
        return username == mine

    # ------------------------------------------------------------- 事件入口

    def apply_event(self, event: MinecraftBridgeEvent) -> asyncio.Task[None] | None:
        """MinecraftService 的监听器（只处理 ``minecraft.chat``）。

        **不阻塞事件通道**：真正的回合在后台任务里跑（返回该任务，测试可以 await；
        事件分发方不会等它 —— WorldPerception 绝不能被 LLM 拖住）。
        """
        if event.type_name != "minecraft.chat":
            return None
        if not self.enabled:
            # Phase 8A：丢包也要有**可诊断的原因码**（以前这里什么都不留）。
            self._route(reason_code="mc_chat_disabled", route_target=RouteTarget.BLOCKED.value)
            return None
        username = str(event.data.get("username") or "").strip()
        message = str(event.data.get("message") or "").strip()
        if not username or not message:
            self._route(reason_code="mc_chat_unusable", route_target=RouteTarget.BLOCKED.value)
            return None
        if self.is_self_speech(username):
            self._route(
                reason_code="mc_chat_self_speech",
                route_target=RouteTarget.BLOCKED.value,
                session_id=self._session_id(username),
                text_chars=len(message),
            )
            return None
        if event.data.get("private"):
            # 悄悄话不公开回：本阶段只有公共聊天发送能力（见文档「已知限制」）
            log.info("[MC Chat] 跳过一条悄悄话（%s）", username)
            self._route(
                reason_code="mc_chat_private",
                route_target=RouteTarget.BLOCKED.value,
                session_id=self._session_id(username),
                text_chars=len(message),
            )
            return None
        if self._inflight >= MAX_INFLIGHT:
            log.info("[MC Chat] 忙不过来，跳过一条（%s）：%.40s", username, message)
            self._route(
                reason_code="mc_chat_busy",
                route_target=RouteTarget.BLOCKED.value,
                session_id=self._session_id(username),
                text_chars=len(message),
            )
            return None
        self._inflight += 1
        task = asyncio.create_task(self._reply(username, message))
        task.add_done_callback(lambda _task: self._done())
        return task

    def _done(self) -> None:
        self._inflight = max(0, self._inflight - 1)

    # --------------------------------------------------------------- 回合

    async def _reply(self, username: str, message: str) -> None:
        """一次完整的 USER 回合：历史 → 角色回复 → 回游戏内 + 记入会话。"""
        bot = self.bot
        try:
            session_id = self._session_id(username)
            history = await bot.ai.conversations.get_context(session_id)
            log.info(
                "[MC Chat] %s: %.80s（session=%s, history=%d）",
                username,
                message,
                session_id,
                len(history),
            )
            # Phase 5A：这句话是不是在指挥一个多步骤任务（确认/暂停/继续/取消，
            # 或者"去砍棵树把木头带回来"这类需要编排的请求）。命中就由任务侧回答，
            # 不再丢给模型自由发挥；没命中（绝大多数聊天）照常走角色回合。
            handled = await self._task_reply(username, session_id, message)
            if handled.reply is not None:
                await bot.ai.conversations.append_user_message(session_id, message)
                await bot.ai.conversations.append_assistant_message(session_id, handled.reply)
                await self._say(handled.reply)
                return
            reply = await bot.character.respond(
                session_id,
                username,
                message,
                # §十九：游戏内玩家说话就是 USER 回合（可信门在策略层，不在这里）
                turn_origin=TurnOrigin.USER,
                # §二十/§二十一：把「谁在说话」作为运输层事实带进工具上下文
                origin_metadata={PLAYER_KEY: username},
                history=history,
                user_name=username,
                is_group=False,
                time_context=self._time_context(),
                extra_instruction=(
                    "（这条消息来自 Minecraft 游戏内聊天，是服务器里的人在跟你说话；"
                    "回复要短，像游戏里随口说的话。）"
                ),
                # Phase 8E Follow-up 2：单回合能力模式（CHAT_ONLY = 无 Agent/无工具）
                mode=handled.mode,
            )
            if not reply:
                return
            await bot.ai.conversations.append_user_message(session_id, message)
            await bot.ai.conversations.append_assistant_message(session_id, reply)
            await self._say(reply)
        except Exception as exc:  # noqa: BLE001 - 游戏内聊天故障绝不拖垮事件通道/世界感知
            log.warning("[MC Chat] 处理 %s 的聊天失败：error_type=%s", username, type(exc).__name__)

    def _session_id(self, username: str) -> str:
        connection = self.service.snapshot().get("connection") or {}
        return chat_session_id(connection.get("host"), connection.get("port"), username)

    async def _task_reply(self, username: str, session_id: str, message: str) -> _TurnReply:
        """统一回合决策入口（Phase 8E Follow-up 3，见 docs/MINECRAFT_PHASE8E.md §16）。

        顺序（任务书 §4.1 / §7.1）：

        1. **确定性控制优先**：停止/取消/确认/状态查询/活动任务 busy 由既有确定性检测器
           判定并交回既有任务入口 —— **绝不等待模型**（停止不依赖 LLM 的可用性/速度）；
        2. 其余自然语言消息**一律**先经 ``TurnDecisionInterpreter``（统一 LLM 决策入口）；
        3. 按 9 类 kind 路由到**既有**审计过的路径；任何失败都安全澄清。

        模型只决定「用户想表达什么」：权限、参数校验、Policy、确认门、步骤授权、冻结
        计划、TaskRuntime 与最终库存验证全部仍由既有确定性链路负责。
        """
        handler = getattr(self.bot, "task_turns", None)
        if handler is None:
            return _TurnReply()
        # 1) 确定性控制（不等待模型）。
        control = await self._control_turn(handler, username, session_id, message)
        if control is not None:
            return control
        # 2) 统一 LLM 决策入口 —— **带本回合真实上下文**（FU4 §4.1/§4.2）。
        decision_outcome = await self._decide_turn(
            session_id, message, handler=handler, username=username
        )
        if not decision_outcome.ok:
            # §3.3：无效输出/超时/未知能力等一律**安全澄清**，绝不降级进执行路径。
            return _TurnReply(
                self._decision_clarify(session_id, message, decision_outcome.reason_code)
            )
        # §5.1：这里**没有**关键词主分流闸。木材解析只在 TASK_PROPOSAL 分支里参与
        # （目标解析 + 额外安全否决，见 _route_turn）；含木材关键词的 CHAT/QUERY/
        # NARRATION 一律遵从已经校验的决策类别。
        kind = (
            decision_outcome.decision.kind
            if decision_outcome.decision is not None
            else TurnDecisionKind.CHAT
        )
        routed = await self._route_turn(
            handler, username, session_id, message, kind, decision_outcome
        )
        if routed is not None:
            return routed
        # 3) CANCEL/STOP（模型报告）与木材 DELEGATE_CONTROL：交回既有任务入口。
        try:
            outcome = await handler.handle(session_id=session_id, user_id=username, text=message)
        except Exception as exc:  # noqa: BLE001 - 任务入口坏了也不能让游戏内聊天没反应
            log.warning(
                "[MC Chat] 任务入口处理失败（%s）：error_type=%s", username, type(exc).__name__
            )
            return _TurnReply()
        if not outcome.handled or not outcome.reply:
            return _TurnReply()
        # Phase 8A：任务侧接走这句话也是一条**路由结论**（TASK + 任务 ID 可关联）。
        self._route(
            reason_code="task_handled",
            route_target=RouteTarget.TASK.value,
            session_id=session_id,
            task_id=str(outcome.task_id or ""),
            text_chars=len(message),
        )
        log.info(
            "[MC Task] action=%s task=%s state=%s（%s）",
            outcome.action,
            outcome.task_id or "-",
            outcome.state or "-",
            username,
        )
        return _TurnReply(outcome.reply)

    # ------------------------------------------ Phase 8E Follow-up 3：统一决策入口

    async def _control_turn(
        self, handler: Any, username: str, session_id: str, message: str
    ) -> _TurnReply | None:
        """确定性控制优先（``None`` = 不是控制/状态/忙碌，继续走统一决策）。

        控制权在**确定性检测器**手里，绝不因为要等模型而延迟停止（§7.1）。
        """
        # 极简替身（测试里的 handler 只有 handle()）：回退到同一个模块级确定性检测器，
        # 而不是把控制权让给模型 —— §7.1 要求停止/取消永远不依赖模型。
        detector = getattr(handler, "detector", None) or _DETECTOR
        reader = getattr(handler, "current_task", None)
        if callable(reader):
            try:
                current = await reader(session_id)
            except Exception:  # noqa: BLE001 - 取当前任务失败就按「没有活动任务」处理
                current = None
        else:
            current = None
        active = current is not None and not bool(getattr(current.state, "terminal", True))
        # 控制词出现在句首（如「取消，别砍树了」）也算控制命令：停止/取消**绝不等模型**。
        stripped = str(message or "").strip()
        prefix_control = any(stripped.startswith(item) for item in _WOOD_CONTROL_PREFIXES)
        try:
            if prefix_control or detector.control_command(message):
                return await self._delegate_task(handler, username, session_id, message)
            if active and detector.status_query(message):
                return await self._delegate_task(handler, username, session_id, message)
        except Exception as exc:  # noqa: BLE001
            log.warning("[MC Chat] 控制判定失败：error_type=%s", type(exc).__name__)
            return None
        if active:
            # 活动任务期间保持既有 busy 语义（不因等模型而延迟或叠加新工作）。
            return await self._delegate_task(handler, username, session_id, message)
        return None

    async def _delegate_task(
        self, handler: Any, username: str, session_id: str, message: str
    ) -> _TurnReply:
        """交回既有任务入口（控制/状态/忙碌的唯一权威）。"""
        try:
            outcome = await handler.handle(session_id=session_id, user_id=username, text=message)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[MC Chat] 任务入口处理失败（%s）：error_type=%s", username, type(exc).__name__
            )
            return _TurnReply()
        if not outcome.handled or not outcome.reply:
            return _TurnReply()
        self._turn_route(
            session_id,
            message,
            RouteTarget.TASK.value,
            "task_handled",
            task_id=str(outcome.task_id or ""),
        )
        log.info(
            "[MC Task] action=%s task=%s state=%s（%s）",
            outcome.action,
            outcome.task_id or "-",
            outcome.state or "-",
            username,
        )
        return _TurnReply(outcome.reply)

    def _decision_interpreter(self) -> Any:
        """统一决策解释器（可注入；否则用同一个既有 AIEngine 惰性构造）。"""
        injected = getattr(self, "turn_decisions", None)
        if injected is not None:
            return injected
        engine = getattr(self.bot, "ai", None)
        if engine is None or not callable(getattr(engine, "chat", None)):
            return None
        self.turn_decisions = TurnDecisionInterpreter(engine)
        return self.turn_decisions

    async def _decide_turn(
        self,
        session_id: str,
        message: str,
        *,
        handler: Any = None,
        username: str = "",
    ) -> TurnDecisionOutcome:
        """一次有界的统一决策（解释器契约是永不抛，这里再兜一层）。

        FU4 §4.2：决策前先构造**本回合真实上下文**（世界观察/背包/记忆/身份/活动任务/
        能力目录），交给解释器作为 ``task_context``；上下文组装失败只减少上下文，
        绝不影响安全路径（控制优先已在 ``_control_turn`` 返回，早于这里）。
        """
        interpreter = self._decision_interpreter()
        if interpreter is None:
            return TurnDecisionOutcome(None, REASON_ENGINE_MISSING, 0, 0.0, {})
        recent: list[str] = []
        try:
            context = await self.bot.ai.conversations.get_context(session_id)
            recent = [str(item)[:240] for item in list(context)[-4:]]
        except Exception:  # noqa: BLE001 - 拿不到上下文就少给一点，不影响安全
            recent = []
        task_context = ""
        try:
            turn_context = await build_turn_context(
                service=self.service,
                character=getattr(self.bot, "character", None),
                handler=handler,
                session_id=session_id,
                username=username,
                message=message,
                recent=tuple(recent),
            )
            task_context = turn_context.render()
            # §7/§8：只记状态、来源类别与稳定原因码（不含记忆正文/上下文原文）。
            log.info("[MC Turn] ctx=%s", turn_context.log_fields())
        except Exception as exc:  # noqa: BLE001 - 上下文不是决策前提，缺了也要能决策
            log.warning("[MC Turn] 上下文构造失败：error_type=%s", type(exc).__name__)
        try:
            return await interpreter.decide(
                message=message,
                recent_context=tuple(recent),
                task_context=task_context,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[MC Turn] 决策异常：error_type=%s", type(exc).__name__)
            return TurnDecisionOutcome(None, REASON_ENGINE_MISSING, 0, 0.0, {})

    async def _route_turn(
        self,
        handler: Any,
        username: str,
        session_id: str,
        message: str,
        kind: Any,
        outcome: TurnDecisionOutcome,
    ) -> _TurnReply | None:
        """九类决策 → 既有路径（``None`` = 交回既有任务入口）。"""
        decision = outcome.decision
        if kind in (TurnDecisionKind.CHAT, TurnDecisionKind.NARRATION):
            self._turn_route(
                session_id, message, RouteTarget.CHAT_ONLY.value, f"decision_{kind.value}"
            )
            return _TurnReply(None, CharacterTurnMode.CHAT_ONLY)
        if kind is TurnDecisionKind.CLARIFY:
            question = str(getattr(decision, "clarification_question", "") or "")
            self._turn_route(session_id, message, RouteTarget.CLARIFY.value, "decision_clarify")
            return _TurnReply(question or "我不太确定你想让我做什么，可以说得再具体一点吗？")
        if kind is TurnDecisionKind.UNSUPPORTED:
            self._turn_route(session_id, message, RouteTarget.BLOCKED.value, "decision_unsupported")
            return _TurnReply(self._decision_text(decision, "这件事我现在还做不到。"))
        if kind in (TurnDecisionKind.QUERY, TurnDecisionKind.ATOMIC_ACTION):
            return await self._run_capability(username, session_id, message, kind, decision)
        if kind is TurnDecisionKind.TASK_PROPOSAL:
            # Phase 8F §3.1：**合成解析先于木材解析** —— 「做成木板」含木材名词，木材入口
            # 会把它当成「不支持的木材种类」直接拒掉（那是采集语义，不是合成语义）。
            craft = parse_craft_request(message)
            if craft is not None:
                return await self._craft_turn(username, session_id, message, craft)
            # FU4 §5.2：木材解析**只在这里**参与 —— 作为目标解析 + 额外安全否决：
            #   HANDLED           → 否定请求，明确「不做」（绝不升级成行动）
            #   SAFE_CLARIFY      → 歧义/非法参数/解释器不可用 → 澄清
            #   NORMAL_CHAT       → 与木材有关的普通提及 → 聊天（CHAT_ONLY）
            #   DELEGATE_CONTROL  → 控制/状态/忙碌 → 既有任务入口
            #   WOOD_POSITIVE_REQUEST → 既有木材校验 + 计划链（授权/后置验证不变）
            #   NOT_WOOD          → 继续既有入口与 follow/explore 计划层
            wood = _WoodRouteDecision(_WoodRoute.NOT_WOOD)
            if mentions_wood(message) or parse_wood_request(message) is not None:
                wood = await self._wood_route(handler, username, session_id, message)
            if wood.route is _WoodRoute.HANDLED and wood.reply is not None:
                return _TurnReply(wood.reply)
            if wood.route is _WoodRoute.SAFE_CLARIFY:
                return _TurnReply(
                    wood.reply or self._decision_text(decision, "这件事我需要你说得更具体一点。")
                )
            if wood.route is _WoodRoute.NORMAL_CHAT:
                return _TurnReply(None, CharacterTurnMode.CHAT_ONLY)
            if wood.route is _WoodRoute.DELEGATE_CONTROL:
                return await self._delegate_task(handler, username, session_id, message)
            if wood.route is _WoodRoute.WOOD_POSITIVE_REQUEST:
                if wood.reply is not None:
                    return _TurnReply(wood.reply)
                return _TurnReply(self._decision_text(decision, "这件事我还没有能安全执行的做法。"))
            # 先给**既有任务入口**一次机会：资源任务/控制/状态的既有权威路径就是它
            # （8D 的单目标资源模板），跳过它会直接砍掉既有能力。
            delegated = await self._delegate_task(handler, username, session_id, message)
            if delegated.reply is not None:
                return delegated
            # 再走 follow/explore 计划层；两者都不认 → 安全澄清（绝不回落工具执行）。
            planned = await self._plan_reply(username, session_id, message)
            if planned is not None:
                return _TurnReply(planned)
            # 8C Follow-up 语义：形状已识别但计划层没装配 → plan_service_unavailable；
            # 其余情况才是「这个目标我还没有安全做法」。
            layer_missing = getattr(self.bot, "agent_plans", None) is None
            self._turn_route(
                session_id,
                message,
                RouteTarget.BLOCKED.value,
                "plan_service_unavailable" if layer_missing else "decision_unsupported",
            )
            return _TurnReply(self._decision_text(decision, "这件事我还没有能安全执行的做法。"))
        if kind in (TurnDecisionKind.CANCEL, TurnDecisionKind.STOP):
            return None  # 交回既有控制路径（唯一权威）
        return _TurnReply(None, CharacterTurnMode.CHAT_ONLY)

    async def _run_capability(
        self, username: str, session_id: str, message: str, kind: Any, decision: Any
    ) -> _TurnReply:
        """执行**单个**已审计能力：schema 校验 → Policy → 工具 → AgentBridge → Service。

        走的是既有 ``ToolExecutor.execute``（它自己会做参数校验与 ``policy.authorize``），
        不新增任何执行旁路；能力白名单由 ``turn_decision`` 的可信常量界定。
        """
        capability = str(getattr(decision, "capability_id", "") or "")
        arguments = dict(getattr(decision, "arguments", {}) or {})
        spoken = self._decision_text(decision, "")
        executor = getattr(
            getattr(getattr(self.bot, "character", None), "tools", None), "executor", None
        )
        context = await self._turn_context(username, session_id)
        if executor is None or not capability or context is None:
            self._turn_route(session_id, message, RouteTarget.BLOCKED.value, "capability_disabled")
            return _TurnReply(spoken or "我现在没法去做这件事。")
        label = str(getattr(kind, "value", kind) or "action")
        try:
            result = await executor.execute(
                ToolCall(name=capability, arguments=arguments, reason=f"decision_{label}"),
                context,
                TurnBudget(max_calls=1),
            )
        except Exception as exc:  # noqa: BLE001 - 工具层契约是不抛，这里再兜一层
            log.warning("[MC Turn] 能力执行异常：error_type=%s", type(exc).__name__)
            self._turn_route(session_id, message, RouteTarget.MINECRAFT_TOOL.value, "action_failed")
            return _TurnReply(spoken or "我试了一下，没成功。")
        success = bool(getattr(result, "success", False))
        self._turn_route(
            session_id,
            message,
            RouteTarget.MINECRAFT_TOOL.value,
            "action_succeeded"
            if success
            else str(getattr(result, "error_type", "") or "action_failed"),
        )
        if not success:
            return _TurnReply(spoken or "我试了一下，没成功。")
        return _TurnReply(spoken or self._observed_text(capability, result))

    async def _turn_context(self, username: str, session_id: str) -> Any:
        """既有工具上下文（``INTENT_KEY`` 由 ``TurnOrigin`` 派生，调用方无法手写）。"""
        character = getattr(self.bot, "character", None)
        builder = getattr(character, "_tool_context", None)
        if builder is None:
            return None
        try:
            built = builder(
                session_id=session_id,
                user_id=username,
                time_context=self._time_context(),
                is_group=False,
                turn_origin=TurnOrigin.USER,
                origin_metadata={PLAYER_KEY: username},
            )
            if hasattr(built, "__await__"):
                built = await built
            return built
        except Exception as exc:  # noqa: BLE001
            log.warning("[MC Turn] 工具上下文构造失败：error_type=%s", type(exc).__name__)
            return None

    @staticmethod
    def _decision_text(decision: Any, fallback: str) -> str:
        text = str(getattr(decision, "reply_text", "") or "").strip()
        return text or fallback

    @staticmethod
    def _observed_text(capability: str, result: Any) -> str:
        """把**真实**工具结果说成人话（抽不出来就说不确定，绝不编造）。"""
        if capability == "minecraft_stop":
            return "停下来了。"
        if capability == "minecraft_look_at":
            return "我看过去了。"
        data = getattr(result, "data", None)
        if capability == "minecraft_find_blocks" and isinstance(data, dict):
            matches = data.get("matches") or []
            if not matches:
                return "附近没看到这类方块。"
            names = sorted(
                {
                    str((item.get("block") or {}).get("name") or "")
                    for item in matches
                    if isinstance(item, dict)
                }
                - {""}
            )
            label = "、".join(names) or "方块"
            return f"附近看到 {len(matches)} 个{label}。"
        if capability == "minecraft_inventory" and isinstance(data, dict):
            items = data.get("items") or []
            held = data.get("held_item")
            held_name = str(held.get("name") or "") if isinstance(held, dict) else ""
            if not items:
                return "我身上现在是空的。"
            return f"我身上有 {len(items)} 样东西" + (
                f"，手里拿着 {held_name}。" if held_name else "。"
            )
        if capability == "minecraft_world" and isinstance(data, dict):
            position = data.get("position")
            if isinstance(position, dict) and position:
                where = f"我在 ({position.get('x')}, {position.get('y')}, {position.get('z')})"
                dimension = str(data.get("dimension") or "")
                return where + (f"，{dimension}。" if dimension else "。")
        summary = str(getattr(result, "summary", "") or "").strip()
        return summary or "我看过了。"

    def _decision_clarify(self, session_id: str, message: str, reason_code: str) -> str:
        """决策失败/歧义 → 安全澄清（原因码由**可信代码**生成）。"""
        raw = str(reason_code or "unknown")
        code = raw if raw.startswith("decision_") else f"decision_{raw}"
        self._turn_route(session_id, message, RouteTarget.CLARIFY.value, code)
        if raw in ("decision_timeout", "decision_model_error", "decision_engine_unavailable"):
            return "我刚才没想清楚，你再说一遍？"
        return "这句话我没太理解，你是想让我做什么？"

    def _turn_route(
        self,
        session_id: str,
        message: str,
        route_target: str,
        reason_code: str,
        *,
        task_id: str = "",
    ) -> None:
        """结构化路由证据（记账失败绝不影响执行）。"""
        try:
            self._route(
                reason_code=reason_code,
                route_target=route_target,
                session_id=session_id,
                task_id=task_id,
                text_chars=len(message),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[MC Turn] 路由记账失败：error_type=%s", type(exc).__name__)

    # ---------------------------------------------------------- Phase 8D：木材采集

    async def _wood_intent_preempts(self, handler: Any, session_id: str, message: str) -> bool:
        """木材意图解释是否在入口之前接管（§5 控制优先级 / §5 活动任务边界）。

        控制命令、有活动任务时的状态查询、以及会话里已有未终态任务（busy）都保持
        既有顺序与文案，交回任务入口；其余木材相关消息交由解释器判定（唯一决策源）。
        """
        detector = getattr(handler, "detector", None)
        current_task = getattr(handler, "current_task", None)
        if detector is None or not callable(current_task):
            return False
        if detector.control_command(message):
            return False
        stripped = str(message or "").strip()
        if any(stripped.startswith(word) for word in _WOOD_CONTROL_PREFIXES):
            return False
        current = await current_task(str(session_id))
        if current is not None and detector.status_query(message):
            return False
        return current is None or bool(current.state.terminal)

    def _wood_negated_reply(self, session_id: str, message: str) -> str:
        """否定结论：零计划、零任务、零工具，只留稳定原因码。"""
        self._route(
            reason_code="wood_negated_intent",
            route_target=RouteTarget.CHAT_ONLY.value,
            session_id=session_id,
            text_chars=len(message),
        )
        return "好，那我不砍了 —— 这次什么都没做。"

    def _wood_interpreter(self) -> Any:
        """解释器实例：优先用注入的（测试/装配），否则用既有 AIEngine 惰性构造。"""
        injected = getattr(self, "wood_intent", None)
        if injected is not None:
            return injected
        engine = getattr(self.bot, "ai", None)
        if engine is None or not callable(getattr(engine, "chat", None)):
            return None
        self.wood_intent = WoodIntentInterpreter(engine)
        return self.wood_intent

    async def _wood_route(
        self, handler: Any, username: str, session_id: str, message: str
    ) -> _WoodRouteDecision:
        """按解释器结论给出**显式**路由结果（§4.1/§4.2 行为矩阵）。

        返回对象而不是 ``None``：调用方据此决定「终止任务路由走普通对话」还是
        「交回确定性控制入口」，两者不再共用一种返回值。
        """
        if not await self._wood_intent_preempts(handler, session_id, message):
            # 控制命令 / 状态查询 / 活动任务 busy —— 一律交回既有入口（§4.3）。
            return _WoodRouteDecision(_WoodRoute.DELEGATE_CONTROL)
        interpreter = self._wood_interpreter()
        if interpreter is None:
            return _WoodRouteDecision(
                _WoodRoute.SAFE_CLARIFY,
                self._wood_intent_clarify(session_id, message, "wood_interpreter_unavailable"),
            )
        try:
            result = await interpreter.interpret(message=message)
        except Exception as exc:  # noqa: BLE001 - 解释器损坏也绝不让聊天没反应
            # §5.3：只记异常**类型名**（异常正文可能夹带 provider 响应/请求上下文）。
            log.warning(
                "[MC Chat] 木材意图解释器失败：error_type=%s（%s）",
                type(exc).__name__,
                username,
            )
            return _WoodRouteDecision(
                _WoodRoute.SAFE_CLARIFY,
                self._wood_intent_clarify(session_id, message, "wood_intent_error"),
            )
        if result is None:
            return _WoodRouteDecision(
                _WoodRoute.SAFE_CLARIFY,
                self._wood_intent_clarify(session_id, message, "wood_intent_invalid_result"),
            )
        reason = str(getattr(result, "reason_code", "") or "")
        intent = str(getattr(result, "intent", "") or "")
        polarity = str(getattr(result, "polarity", "") or "")
        if intent == WoodIntent.AMBIGUOUS_INTENT.value:
            # 沿用 Phase 8D Follow-up 2 已确立的稳定原因码（审计连续性）。
            return _WoodRouteDecision(
                _WoodRoute.SAFE_CLARIFY,
                self._wood_intent_clarify(session_id, message, "wood_negation_ambiguous"),
            )
        if bool(getattr(result, "needs_clarification", False)):
            return _WoodRouteDecision(
                _WoodRoute.SAFE_CLARIFY,
                self._wood_intent_clarify(session_id, message, reason or "clarify"),
            )
        if intent == WoodIntent.NEGATED_REQUEST.value:
            return _WoodRouteDecision(
                _WoodRoute.HANDLED, self._wood_negated_reply(session_id, message)
            )
        if intent == WoodIntent.WOOD_COLLECTION_REQUEST.value:
            if polarity != Polarity.POSITIVE.value:
                return _WoodRouteDecision(
                    _WoodRoute.SAFE_CLARIFY,
                    self._wood_intent_clarify(session_id, message, "conflict"),
                )
            # 只有 schema + 矛盾校验都通过的肯定结果，且下一步仍过确定性参数校验。
            return _WoodRouteDecision(
                _WoodRoute.WOOD_POSITIVE_REQUEST,
                await self._wood_positive_reply(username, session_id, message, result),
            )
        if intent in (WoodIntent.QUERY.value, WoodIntent.NARRATION.value):
            # §4.2：查询/叙述 → 普通对话语义，**绝不**经过可创建任务的旧入口。
            return _WoodRouteDecision(_WoodRoute.NORMAL_CHAT)
        if intent == WoodIntent.TASK_CONTROL.value:
            # §4.3：模型标签不构成控制权限；确定性检测器没认出来就必须澄清。
            return _WoodRouteDecision(
                _WoodRoute.SAFE_CLARIFY,
                self._wood_intent_clarify(session_id, message, "wood_intent_task_control"),
            )
        # OTHER 或未知枚举：明确策略 = 安全澄清并终止任务创建路由（不猜任务、不回旧模板）。
        return _WoodRouteDecision(
            _WoodRoute.SAFE_CLARIFY,
            self._wood_intent_clarify(session_id, message, reason or "wood_intent_other"),
        )

    async def _wood_positive_reply(
        self, username: str, session_id: str, message: str, result: Any
    ) -> str:
        """肯定请求：确定性解析 + 支持树种矩阵**校验参数**（冲突 → 澄清）。"""
        wood = parse_wood_request(message)
        if wood is None:
            return self._wood_intent_clarify(session_id, message, "parameters_unresolved")
        conflict = self._wood_parameter_conflict(wood, result)
        if conflict:
            return self._wood_intent_clarify(session_id, message, conflict)
        return await self._wood_reply(username, session_id, message, wood)

    def _wood_parameter_conflict(self, wood: WoodRequest, result: Any) -> str:
        """§6.1：模型提取值与确定性解析**冲突时澄清**，不任选其一。"""
        model_count = getattr(result, "requested_count", None)
        if model_count is not None and int(model_count) != int(wood.requested):
            return "count_conflict"
        raw_species = str(getattr(result, "target_species", None) or "").strip()
        if not raw_species or not wood.species:
            return ""
        probe = parse_wood_request("帮我砍一根" + raw_species)
        if probe is None or not probe.species:
            return "target_unverified"
        if tuple(probe.candidates) != tuple(wood.candidates):
            return "target_conflict"
        return ""

    def _wood_intent_clarify(self, session_id: str, message: str, reason_code: str) -> str:
        """歧义/失败关闭的澄清回复（零计划、零任务、零工具；原因码稳定可审计）。"""
        code = reason_code if reason_code.startswith("wood_") else "wood_intent_" + reason_code
        self._route(
            reason_code=code,
            route_target=RouteTarget.CLARIFY.value,
            session_id=session_id,
            text_chars=len(message),
        )
        return (
            "我先不动手：这件事我还判断不准（是不砍、砍什么、还是砍几根）。"
            "你直接说「砍一根橡木原木」或者「先不砍」就行。"
        )

    async def _wood_reply(
        self, username: str, session_id: str, message: str, wood: WoodRequest
    ) -> str:
        """把木材请求交给既有有界计划层（shape_hint=wood）—— 失败**绝不**回落工具链。"""
        if wood.problem == "unsupported_species":
            self._route(
                reason_code="wood_unsupported_species",
                route_target=RouteTarget.BLOCKED.value,
                session_id=session_id,
                text_chars=len(message),
            )
            return (
                f"「{wood.detail}」这类木头我现在采不了（只做已验证的树干原木），所以我什么都没动。"
            )
        if wood.problem == "zero_count":
            self._route(
                reason_code="wood_zero_count",
                route_target=RouteTarget.CLARIFY.value,
                session_id=session_id,
                text_chars=len(message),
            )
            return "「0 根」我没法当成一次采集请求 —— 你想要几根？我一次最多采 2 根。"
        if wood.problem:
            self._route(
                reason_code="wood_ambiguous_species",
                route_target=RouteTarget.CLARIFY.value,
                session_id=session_id,
                text_chars=len(message),
            )
            return f"要采哪一种？你提到的树不止一种（{wood.detail}），我不替你挑。"
        plans = getattr(self.bot, "agent_plans", None)
        plan_goal = getattr(plans, "plan_goal_from_user", None)
        if not callable(plan_goal):
            return self._plan_failure(
                session_id,
                message,
                reason_code="plan_service_unavailable",
                text="我这条采木头计划现在排不了（计划服务没装配），所以我什么都没做。",
            )
        try:
            out = await plan_goal(
                objective=str(message),
                user_id=str(username),
                session_id=str(session_id),
                target=PlanTarget(),
                source="minecraft",
                shape_hint="wood",
            )
        except Exception as exc:  # noqa: BLE001 - 计划层故障只影响这一条消息
            log.warning(
                "[MC Plan] 木材计划接线失败（%s）：error_type=%s", username, type(exc).__name__
            )
            return self._plan_failure(
                session_id,
                message,
                reason_code="plan_service_error",
                text="这条采木头计划没排成功（计划层出错），我没有执行任何动作。",
            )
        return self._plan_reply_text(username, session_id, message, "wood", out)

    async def _plan_reply(self, username: str, session_id: str, message: str) -> str | None:
        """Phase 8C：游戏内提出的**多步目标**交给既有计划层（QQ 路径零影响）。

        ``None`` = 这句话不归计划层管，照常走角色回合。只接管两种**当前已支持**的形状：
        跟随（目标只能来自已验证身份，绝不从文本猜玩家名）与有界探索；纯查询、闲聊、
        控制命令与状态查询一律不接管（§五 路由矩阵）。
        """
        bot = self.bot
        intent = _DETECTOR.detect(message)
        if "query_only" in intent.reasons:
            return None
        if _DETECTOR.control_command(message) or _DETECTOR.status_query(message):
            return None
        if _DETECTOR.detect_follow(message):
            shape = "follow"
        elif detect_shape(message) == "explore":
            shape = "explore"
        else:
            return None
        # 到这里已经确认「这是她支持的受支持多步目标」。此后**任何**失败都必须如实回话并记
        # BLOCKED 路由，绝不再返回 None —— 返回 None 会回落到普通角色回合，进而可能进入
        # ToolOrchestrator 的单步工具链，等于把多步目标降级成一次无约束的原子调用。
        plans = getattr(bot, "agent_plans", None)
        plan_goal = getattr(plans, "plan_goal_from_user", None)
        if not callable(plan_goal):
            return self._plan_failure(
                session_id,
                message,
                reason_code="plan_service_unavailable",
                text="我这条多步计划现在排不了（计划服务没装配），所以我什么都没做。",
            )
        target: Any = PlanTarget()
        if shape == "follow":
            resolved = await self._follow_target(username, session_id)
            if resolved is None:
                # fail-closed：解析不出可信目标就说清原因，绝不跟着昵称猜的人走
                self._route(
                    reason_code="plan_target_unverified",
                    route_target=RouteTarget.CLARIFY.value,
                    session_id=session_id,
                    text_chars=len(message),
                )
                return "我不知道你说的「跟着」是跟谁 —— 先绑定一下身份，我才能确认是你。"
            target = resolved
        try:
            out = await plan_goal(
                objective=str(message),
                user_id=str(username),
                session_id=str(session_id),
                target=target,
                source="minecraft",
            )
        except Exception as exc:  # noqa: BLE001 - 计划层故障只影响这一条消息
            log.warning(
                "[MC Plan] 计划层接线失败（%s）：error_type=%s", username, type(exc).__name__
            )
            return self._plan_failure(
                session_id,
                message,
                reason_code="plan_service_error",
                text="这条计划没排成功（计划层出错），我没有执行任何动作。",
            )
        return self._plan_reply_text(username, session_id, message, shape, out)

    def _plan_failure(self, session_id: str, message: str, *, reason_code: str, text: str) -> str:
        """已识别的多步目标在计划层不可用/出错时的**明确失败答复**（BLOCKED 路由）。

        关键：这里必须返回字符串而不是 ``None`` —— ``None`` 会让这句话回落到普通角色回合，
        进而可能进入 ``ToolOrchestrator`` 的单步工具链（把多步目标降级成原子调用）。
        """
        self._route(
            reason_code=reason_code,
            route_target=RouteTarget.BLOCKED.value,
            session_id=session_id,
            text_chars=len(message),
        )
        return text

    async def _follow_target(self, username: str, session_id: str) -> Any | None:
        """游戏内说话者 → **已验证**的跟随目标（8B 身份解析 + 5C 目标解析）。

        只有 VERIFIED 且能解析出对手平台（QQ）用户 ID 时才继续；其余一律 ``None``
        （降级/撤销/冲突/跨服/缺权威 UUID 都算解析不出来，不猜）。
        """
        bot = self.bot
        character = getattr(bot, "character", None)
        resolver = getattr(character, "identity_resolver", None)
        proposals = getattr(bot, "proposals", None)
        resolve_target = getattr(proposals, "resolve_target", None)
        if resolver is None or not callable(resolve_target):
            return None
        try:
            context = await resolver.resolve(
                session_id=str(session_id),
                user_id=str(username),
                user_name=str(username),
                origin_metadata={PLAYER_KEY: username},
            )
        except Exception as exc:  # noqa: BLE001 - 身份解析故障只降级
            log.warning("[MC Plan] 身份解析失败（%s）：error_type=%s", username, type(exc).__name__)
            return None
        if context.resolution is not IdentityResolution.VERIFIED:
            return None
        if str(context.platform) != "qq" or not str(context.platform_user_id):
            return None
        try:
            return await resolve_target(
                platform="qq",
                user_id=str(context.platform_user_id),
                server_id=str(context.server_id),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[MC Plan] 目标解析失败（%s）：error_type=%s", username, type(exc).__name__)
            return None

    def _plan_reply_text(
        self, username: str, session_id: str, message: str, shape: str, out: Any
    ) -> str:
        """计划层结果 → 游戏内回话，并留下一条可审计的路由结论（§六）。"""
        action = str((out or {}).get("action") or "")
        record = (out or {}).get("record")
        plan = (out or {}).get("plan")
        task_id = str(getattr(record, "task_id", "") or "")
        if action == "created":
            self._route(
                reason_code="plan_created",
                route_target=RouteTarget.TASK.value,
                session_id=session_id,
                task_id=task_id,
                text_chars=len(message),
            )
            log.info(
                "[MC Plan] shape=%s action=created task=%s（%s）", shape, task_id or "-", username
            )
            if shape == "wood":
                # 数量一律取自**冻结的计划**（用户说的数不一定等于计划里的数）。
                return (
                    self._wood_summary(record, plan)
                    + "\n需要你确认后我才会动手。\n回复「确认」开始。"
                )
            if shape == "craft":
                return (
                    self._craft_summary(record, plan)
                    + "\n需要你确认后我才会动手。\n回复「确认」开始。"
                )
            target_name = str(getattr(getattr(plan, "target", None), "player_name", "") or "")
            if shape == "follow" and target_name:
                duration = dict(getattr(plan, "risk_summary", {}) or {}).get("duration_note")
                text = f"罐头准备这样做：跟随 {target_name}（来自已验证的身份绑定）。"
                if duration:
                    text += f"\n期限：{duration}"
                return text + "\n需要你确认后我才会动。\n回复「确认」开始。"
            return str(out.get("reply") or "计划已经排好，回复「确认」我就开始。")
        route_target = (
            RouteTarget.BLOCKED.value
            if action in {"busy", "create_failed"}
            else RouteTarget.CLARIFY.value
        )
        self._route(
            reason_code=f"plan_{action or 'rejected'}",
            route_target=route_target,
            session_id=session_id,
            task_id=task_id,
            text_chars=len(message),
        )
        log.info(
            "[MC Plan] shape=%s action=%s outcome=%s（%s）",
            shape,
            action or "rejected",
            (out or {}).get("outcome") or "-",
            username,
        )
        return str(out.get("reply") or "这件事现在做不了。")

    def _wood_summary(self, record: Any, plan: Any) -> str:
        """如实说明这一份木材计划采几根、什么木头（数字来自冻结计划，不是用户说的数）。"""
        task_plan = getattr(record, "plan", None)
        state = getattr(task_plan, "expected_final_state", None)
        expected = getattr(state, "inventory_delta", None)
        items: dict[str, int] = {}
        if isinstance(expected, dict):
            for key, value in expected.items():
                try:
                    count = int(value or 0)
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    items[str(key)] = count
        total = sum(items.values())
        names = "、".join(f"{name} x{count}" for name, count in sorted(items.items()))
        detail: dict[str, Any] = {}
        checks = getattr(plan, "checks", None) or ()
        if checks and isinstance(checks[-1], dict):
            raw = checks[-1].get("detail")
            if isinstance(raw, dict):
                detail = dict(raw)
        text = f"罐头准备这样做：采集 {names or '原木'}（共 {total} 个，来自真实观察确认的目标）。"
        if detail.get("capped"):
            text += "你说的比这个多 —— 本期一次最多采 2 根，这一份计划只采上面这些。"
        return text

    async def _craft_turn(
        self, username: str, session_id: str, message: str, request: CraftRequest
    ) -> _TurnReply:
        """Phase 8F §9.1：合成请求 → 澄清 / 安全拒绝 / 既有计划链（shape_hint=craft）。

        澄清与拒绝分支**不建任务、不调工具**；肯定请求走既有计划链 + 既有两道确认门。
        """
        if request.problem == "negated":
            self._turn_route(session_id, message, RouteTarget.CHAT_ONLY.value, "craft_negated")
            return _TurnReply("好，那我不做这个了。")
        if request.problem == "zero_count":
            self._turn_route(session_id, message, RouteTarget.CLARIFY.value, "craft_zero_count")
            return _TurnReply("「0 个」我没法当成一次合成请求 —— 你想要几个？")
        if request.problem == "unknown_item":
            self._turn_route(session_id, message, RouteTarget.CLARIFY.value, "craft_unknown_item")
            return _TurnReply("你想让我做什么？我现在只会做木棍、木板这类基础物品。")
        if request.problem:
            names = request.detail or "这个"
            self._turn_route(session_id, message, RouteTarget.CLARIFY.value, "craft_ambiguous_item")
            return _TurnReply(f"「{names}」指的是哪一种？说清树种我才能算配方（我不替你挑）。")
        plans = getattr(self.bot, "agent_plans", None)
        plan_goal = getattr(plans, "plan_goal_from_user", None)
        if not callable(plan_goal):
            return _TurnReply(
                self._plan_failure(
                    session_id,
                    message,
                    reason_code="plan_service_unavailable",
                    text="我这条合成计划现在排不了（计划服务没装配），所以我什么都没做。",
                )
            )
        try:
            out = await plan_goal(
                objective=str(message),
                user_id=str(username),
                session_id=str(session_id),
                target=PlanTarget(),
                source="minecraft",
                shape_hint="craft",
            )
        except Exception as exc:  # noqa: BLE001 - 计划层故障只影响这一条消息
            log.warning(
                "[MC Plan] 合成计划接线失败（%s）：error_type=%s", username, type(exc).__name__
            )
            return _TurnReply(
                self._plan_failure(
                    session_id,
                    message,
                    reason_code="plan_service_error",
                    text="这条合成计划没排成功（计划层出错），我没有执行任何动作。",
                )
            )
        return _TurnReply(self._plan_reply_text(username, session_id, message, "craft", out))

    def _craft_summary(self, record: Any, plan: Any) -> str:
        """如实说明这一份合成计划做什么、用多少材料（数字全部取自**冻结计划**）。"""
        task_plan = getattr(record, "plan", None)
        state = getattr(task_plan, "expected_final_state", None)
        produced = _item_counts(getattr(state, "inventory_delta", None))
        consumed = _item_counts(getattr(state, "inventory_consumed", None))
        steps = list(getattr(task_plan, "steps", None) or [])
        batches = sum(1 for step in steps if getattr(step, "tool", "") == "minecraft_craft")
        detail: dict[str, Any] = {}
        checks = getattr(plan, "checks", None) or ()
        if checks and isinstance(checks[-1], dict):
            raw_detail = checks[-1].get("detail")
            if isinstance(raw_detail, dict):
                detail = dict(raw_detail)
        made = "、".join(
            f"{display_name_of(name)} x{count}" for name, count in sorted(produced.items())
        )
        used = "、".join(
            f"{display_name_of(name)} x{count}" for name, count in sorted(consumed.items())
        )
        target_name = made or "目标物品"
        text = (
            f"罐头准备这样做：用 {used or '现有材料'} 合成 {target_name}"
            f"（一共 {batches} 刀，材料以服务器实际配方为准）。"
        )
        requested = int(detail.get("requested") or 0)
        total = sum(produced.values())
        if detail.get("default_batch"):
            text += "\n你没说数量，我按**一次合成**算（就是上面的产出量）。"
        if requested and total > requested:
            extra = total - requested
            text += (
                f"\n合成是按整刀做的，所以这一份会产出 {total} 个，"
                f"比你说的 {requested} 个多 {extra} 个（不会额外多做）。"
            )
        if detail.get("capped"):
            text += (
                f"\n你说的比这个多：本期一次最多 {CRAFT_MAX_BATCHES} 刀，这一份计划只做上面这些。"
            )
        return text

    def _route(
        self,
        *,
        reason_code: str,
        route_target: str = RouteTarget.CHAT_ONLY.value,
        session_id: str = "",
        task_id: str = "",
        text_chars: int = 0,
    ) -> None:
        """记一条结构化路由证据（Phase 8A §五）。

        桥接层的**丢包**（自身发言 / 悄悄话 / 忙碌 / 未启用）与**任务路由**也必须可诊断 ——
        以前这些路径一行记录都不留，用户只能看到"她没反应/只聊天"。
        记账失败绝不影响聊天本身（fail-open 只针对诊断，不针对权限）。
        """
        character = getattr(self.bot, "character", None)
        recorder = getattr(character, "record_route", None) if character is not None else None
        if recorder is None:
            return
        try:
            recorder(
                RouteTrace(
                    route_id=new_route_id(),
                    session_id=session_id,
                    source="minecraft_chat",
                    turn_origin=TurnOrigin.USER.value,
                    route_target=route_target,
                    reason_code=reason_code,
                    task_id=task_id,
                    text_chars=text_chars,
                )
            )
        except Exception:  # noqa: BLE001 - 诊断记账失败绝不能拖垮聊天
            log.debug("[MC Chat] 路由记账失败（忽略）", exc_info=True)

    def _time_context(self) -> Any:
        behavior = getattr(self.bot, "behavior", None)
        if behavior is None:
            return None
        try:
            return behavior.time_context()
        except Exception:  # noqa: BLE001 - 时间只是礼貌
            return None

    async def _say(self, reply: str) -> None:
        """把回复发回游戏（走 Service → Action Runtime，绝不直接碰 runtime）。

        多行回复**按行发**：Minecraft 的聊天一行就是一条消息，任务确认摘要这种
        "每一步都要让用户看见"的内容不能被压成一行截断（§十一）。
        """
        limit = self.service.config.agent.chat.max_reply_chars
        lines = [line.strip() for line in str(reply).splitlines() if line.strip()]
        for line in lines[:MAX_CHAT_LINES]:
            text = line
            if len(text) > limit:
                text = text[: limit - 1].rstrip() + "…"
            try:
                await self.service.send_chat(text)
            except Exception as exc:  # noqa: BLE001 - 发不出去只记一笔（她还在世界里）
                log.warning("[MC Chat] 回复发送失败：error_type=%s", type(exc).__name__)
                return
