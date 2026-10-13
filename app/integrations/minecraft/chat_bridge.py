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
from typing import TYPE_CHECKING, Any

from app.character.turn import TurnOrigin
from app.integrations.minecraft.agent import PLAYER_KEY
from app.integrations.minecraft.identity_context import IdentityResolution
from app.tasks.agent_plan import PlanTarget
from app.tasks.agent_planner import detect_shape
from app.tasks.intent import TaskIntentDetector
from app.tools.models import RouteTarget, RouteTrace, new_route_id

if TYPE_CHECKING:  # pragma: no cover
    from app.core.bot import Bot
    from app.integrations.minecraft.events import MinecraftBridgeEvent
    from app.integrations.minecraft.service import MinecraftService

log = logging.getLogger("CatooBot.Minecraft.Chat")

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


class MinecraftChatBridge:
    """``minecraft.chat`` 事件 → USER 回合 → 游戏内回复。"""

    def __init__(self, bot: Bot, service: MinecraftService) -> None:
        self.bot = bot
        self.service = service
        self._inflight = 0

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
            if handled is not None:
                await bot.ai.conversations.append_user_message(session_id, message)
                await bot.ai.conversations.append_assistant_message(session_id, handled)
                await self._say(handled)
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
            )
            if not reply:
                return
            await bot.ai.conversations.append_user_message(session_id, message)
            await bot.ai.conversations.append_assistant_message(session_id, reply)
            await self._say(reply)
        except Exception:  # noqa: BLE001 - 游戏内聊天故障绝不拖垮事件通道/世界感知
            log.exception("[MC Chat] 处理 %s 的聊天失败", username)

    def _session_id(self, username: str) -> str:
        connection = self.service.snapshot().get("connection") or {}
        return chat_session_id(connection.get("host"), connection.get("port"), username)

    async def _task_reply(self, username: str, session_id: str, message: str) -> str | None:
        """任务侧的回答（``None`` = 这句话不归任务管，走正常对话）。

        任务运行时没装配、或者识别不出任务意图时一律返回 ``None`` —— 普通对话
        仍然是普通对话（§九十九）。
        """
        handler = getattr(self.bot, "task_turns", None)
        if handler is None:
            return None
        try:
            outcome = await handler.handle(session_id=session_id, user_id=username, text=message)
        except Exception:  # noqa: BLE001 - 任务入口坏了也不能让游戏内聊天没反应
            log.exception("[MC Chat] 任务入口处理失败（%s）", username)
            return None
        if not outcome.handled or not outcome.reply:
            # Phase 8C：任务入口没接管的，再看是不是一条**多步目标**（跟随/有界探索）；
            # 不是就照样还回角色回合（返回 None）。QQ 路径完全不经这里。
            return await self._plan_reply(username, session_id, message)
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
        return outcome.reply

    async def _plan_reply(self, username: str, session_id: str, message: str) -> str | None:
        """Phase 8C：游戏内提出的**多步目标**交给既有计划层（QQ 路径零影响）。

        ``None`` = 这句话不归计划层管，照常走角色回合。只接管两种**当前已支持**的形状：
        跟随（目标只能来自已验证身份，绝不从文本猜玩家名）与有界探索；纯查询、闲聊、
        控制命令与状态查询一律不接管（§五 路由矩阵）。
        """
        bot = self.bot
        plans = getattr(bot, "agent_plans", None)
        plan_goal = getattr(plans, "plan_goal_from_user", None)
        if not callable(plan_goal):
            return None
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
        except Exception:  # noqa: BLE001 - 计划层故障只影响这一条消息
            log.exception("[MC Plan] 计划层接线失败（%s）", username)
            return None
        return self._plan_reply_text(username, session_id, message, shape, out)

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
        except Exception:  # noqa: BLE001 - 身份解析故障只降级
            log.exception("[MC Plan] 身份解析失败（%s）", username)
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
        except Exception:  # noqa: BLE001
            log.exception("[MC Plan] 目标解析失败（%s）", username)
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
                log.warning("[MC Chat] 回复发送失败：%s", exc)
                return
