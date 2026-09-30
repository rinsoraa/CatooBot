"""Character plugin: the only QQ-facing surface.

Since v0.4 the plugin is deliberately thin — it contains no behaviour rules:

    Behaviour engine decides *whether* to speak (and why)
    Character runtime decides *what* to say (persona + memory + state)
    Response planner/delivery decide *how* to say it (delay, bubbles)

Triggers: private messages always, group messages when @-ed or when the
participation gate allows it. Management stays in the WebUI.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING, Any

from app.ai.errors import AIError
from app.behavior.models import BehaviorEvent
from app.message.message import Message
from app.message.segment import FaceSegment, ImageSegment, MfaceSegment
from app.plugins.base import Plugin
from app.response.delivery import DeliveryTarget
from app.utils.narrator import narrate

if TYPE_CHECKING:
    from app.core.bot import Bot
    from app.message.event import MessageEvent

#: why the behaviour engine decided what it decided (console narration)
REASON_TEXT = {
    "private_direct": "私聊消息",
    "mentioned": "@ 了她",
    "participation": "群里聊得起劲，她想说一句",
    "low_probability": "群里在聊别的，这次没到她的概率",
    "too_short": "消息太短，没什么可接的",
    "cooldown": "刚在群里说过话，歇一会儿",
    "hourly_limit": "这一小时在群里说够了",
    "dnd": "她在勿扰时段",
    "sleeping": "她睡着了",
    "addressed_to_someone_else": "这句是在叫别人",
    "group_disabled": "这个群被关了参与",
    "participation_disabled": "群聊参与关着",
    "mention_replies_disabled": "被叫到也不回（配置）",
    "behavior_disabled": "行为引擎关着",
    # v0.9 social cognition reasons
    "direct_mention": "@ 了她，高优先级",
    "reply_to_bot": "有人直接回复她的消息",
    "direct_follow_up": "接住上一句的追问（无 @ 也识别为在跟她说话）",
    "topic_continuation": "话题还在延续",
    "topic_interest": "这个话题和她有关，值得说一句",
    "current_activity_relevance": "正聊到她此刻在做的事",
    "memory_relevance": "和她的记忆有关",
    "helpful_contribution": "她能补充点有用信息",
    "shared_interest": "共同兴趣",
    "no_relevance": "和她没什么关系",
    "already_discussed": "已经有人讨论过了",
    "low_contribution_value": "没有真正可补充的内容",
    "poor_timing": "现在插话有点突兀，先听",
    "social_disabled": "社交认知关闭着",
    "daily_limit": "今天的群聊参与额度用完了",
}

# Natural, in-character fallbacks — never客服文案.
BUSY_REPLIES = (
    "……（好像走神了）诶，你刚说什么？",
    "嗯……这会儿有点反应不过来，等下再聊？",
    "啊，刚才没听清，再说一遍嘛。",
)


class CharacterPlugin(Plugin):
    name = "character"
    version = "0.4.0"
    description = "Natural-language character chat with behaviour engine"

    async def on_load(self, bot: Bot) -> None:
        bot.event_bus.on("message.private", self._on_private)
        bot.event_bus.on("message.group", self._on_group)

    # ------------------------------------------------------------- triggers

    async def _on_private(self, event: MessageEvent) -> None:
        if not self._ready():
            self.bot.log.warning("[Behavior] private reply skipped (character/AI not ready)")
            narrate().quiet("这条先不回", detail="角色或 AI 还没就绪")
            return
        text = event.message.text.strip()
        if not text and not self._has_media(event):
            return
        decision = await self.bot.behavior.consider_private(event, text)
        story = narrate()
        if not decision.respond:
            self.bot.log.info("[Behavior] private reply skipped (%s)", decision.reason)
            story.quiet("这条先不回", detail=REASON_TEXT.get(decision.reason, decision.reason))
            return
        story.judge("私聊 → 要回", detail=REASON_TEXT.get(decision.reason, decision.reason))
        await self._respond(event, text, is_group=False)

    async def _on_group(self, event: MessageEvent) -> None:
        if not self._ready():
            self.bot.log.warning("[Behavior] group reply skipped (character/AI not ready)")
            narrate().quiet("群里这条先不回", detail="角色或 AI 还没就绪")
            return
        mentioned = event.message.is_mentioned(self.bot.self_id or 0)
        prompt_view = Message(event.message.segments)
        if mentioned:
            prompt_view.strip_prefix_at(self.bot.self_id or 0)
        text = prompt_view.text.strip()
        if not text and not self._has_media(event):
            return

        group_enabled = await self._group_enabled(event.group_id)
        group_id = str(event.group_id)
        message_id = str(getattr(event, "message_id", "") or "")
        reply_to_bot = self._replies_to_bot(event, group_id)

        social = getattr(self.bot, "social", None)
        if social is not None and getattr(social, "enabled", False):
            decision = await social.decide(
                group_id=group_id,
                message_id=message_id,
                user_id=str(event.user_id),
                nickname=event.sender.display_name,
                text=text,
                mentioned=mentioned,
                reply_to_bot=reply_to_bot,
                group_enabled=group_enabled,
            )
            if not decision.should_reply:
                narrate().quiet(
                    "群里这条先不回",
                    detail=REASON_TEXT.get(decision.reason_code, decision.reason_code),
                )
                return
            narrate().judge(
                "群聊 → 回一句",
                detail=REASON_TEXT.get(decision.reason_code, decision.reason_code),
            )
            await self._respond(event, text, is_group=True, social=decision)
            return

        # Legacy path (social disabled): the old probability gate stays intact.
        decision = await self.bot.behavior.consider_group(
            event, text, mentioned=mentioned, group_enabled=group_enabled
        )
        if not decision.respond:
            self.bot.log.debug(
                "[Behavior] group reply skipped (%s) group=%s", decision.reason, event.group_id
            )
            narrate().quiet(
                "群里这条不插话",
                detail=REASON_TEXT.get(decision.reason, decision.reason),
            )
            return
        narrate().judge(
            "群聊 → 接一句" if decision.reason == "participation" else "群聊 → 被叫到了，回",
            detail=(
                f"概率 {decision.probability:.0%}"
                f"（roll {decision.detail.get('roll', '-')}）"
                if decision.reason == "participation"
                else REASON_TEXT.get(decision.reason, decision.reason)
            ),
        )
        await self._respond(
            event, text, is_group=True, participation=decision.reason == "participation"
        )

    def _replies_to_bot(self, event: MessageEvent, group_id: str) -> bool:
        """True when the message is a OneBot reply quoting a bot message."""
        replies = event.message.get("reply")
        if not replies:
            return False
        social = getattr(self.bot, "social", None)
        for segment in replies:
            message_id = getattr(segment, "message_id", None)
            if message_id is None:
                continue
            if social is not None and social.monitor.is_bot_message_id(group_id, str(message_id)):
                return True
            last_sent = self.bot.response_delivery.last_sent_ids.get(f"group:{group_id}")
            if last_sent is not None and int(message_id) == int(last_sent):
                return True
        return False

    # --------------------------------------------------------------- helpers

    def _ready(self) -> bool:
        character = getattr(self.bot, "character", None)
        return character is not None and self.bot.ai.enabled

    @staticmethod
    def _session_id(event: MessageEvent) -> str:
        if event.is_group and event.group_id is not None:
            return f"group:{event.group_id}"
        return f"private:{event.user_id}"

    async def _group_enabled(self, group_id: int | None) -> bool:
        if group_id is None:
            return False
        try:
            row = await self.bot.database.fetchone(
                "SELECT participation_enabled FROM group_profiles WHERE group_id = ?",
                (str(group_id),),
            )
        except Exception:  # noqa: BLE001
            return True
        return bool(row["participation_enabled"]) if row is not None else True

    # --------------------------------------------------------- v1.1 media

    @staticmethod
    def _has_media(event: MessageEvent) -> bool:
        """True when the message carries image / face / mface segments."""
        return any(
            isinstance(seg, (ImageSegment, FaceSegment, MfaceSegment))
            for seg in event.message
        )

    async def _incoming_media(self, event: MessageEvent) -> tuple[str, list[Any]]:
        """Normalize incoming media and build a character-visible context line.

        A plain image becomes an understanding summary (never a sticker); a
        sticker/face becomes a short semantic note. Never blocks for a sticker.
        """
        media = getattr(self.bot, "media", None)
        if media is None or not media.enabled:
            return "", []
        items = media.normalize(
            event.message,
            source_message_id=str(getattr(event, "message_id", "") or ""),
            source_user_id=str(event.user_id),
            source_group_id=str(event.group_id) if event.is_group else "",
        )
        if not items:
            return "", []
        parts: list[str] = []
        for item in items:
            if item.media_type == "image":
                vision = await media.understand_image(item)
                text = vision.as_text()
                if text:
                    parts.append(f"图片：{text}")
                    # operator-facing: what she actually saw in the picture
                    narrate().say("vision", f"看懂了：{text}")
                else:
                    narrate().say(
                        "vision",
                        "这张图没看懂",
                        detail="视觉模型不可用 / 分析失败（详情见日志）",
                    )
            elif item.media_type == "native_face":
                face = media.faces.get(item.face_id)
                if face is not None:
                    parts.append(f"表情：{face.display_name or 'QQ 表情'}")
                    narrate().say("vision", f"收到表情：{face.display_name or 'QQ 表情'}")
            elif item.media_type == "sticker":
                summary = item.emoji_summary or item.emoji_key or "表情包"
                parts.append(f"表情包：{summary}")
                narrate().say("vision", f"收到表情包：{summary}")
        return "；".join(parts), items

    def _schedule_collection(self, event: MessageEvent, items: list[Any]) -> None:
        """Background sticker acquisition — never blocks the reply (§18)."""
        media = getattr(self.bot, "media", None)
        if media is None or not items:
            return
        stickers = [item for item in items if item.is_sticker_source]
        if not stickers:
            return

        async def collect() -> None:
            for item in stickers:
                try:
                    await media.consider_collect(item)
                except Exception:  # noqa: BLE001 - collection must never break chat
                    self.bot.log.exception("[Media] sticker collection failed")

        asyncio.create_task(collect())

    async def _attach_expression(
        self, plan: Any, event: MessageEvent, text: str, media_items: list[Any], is_group: bool
    ) -> None:
        """Decide whether to attach a sticker to the outgoing reply (§23-§29)."""
        media = getattr(self.bot, "media", None)
        if media is None or not media.enabled or not media.config.expression_enabled:
            return
        from app.media.models import ExpressionContext

        incoming_type = ""
        semantics = ""
        if media_items:
            first = media_items[0]
            incoming_type = first.media_type
            if first.media_type == "sticker":
                semantics = first.emoji_summary or first.emoji_key
            elif first.media_type == "native_face":
                face = media.faces.get(first.face_id)
                semantics = face.display_name if face else ""
        context = ExpressionContext(
            incoming_media_type=incoming_type,
            incoming_sticker_semantics=semantics,
        )
        scope_key = f"group:{event.group_id}" if is_group else f"private:{event.user_id}"
        decision = media.decide_expression(context, user_text=text, scope_key=scope_key)
        if not decision.selection_required:
            return
        sticker = await media.select(decision, scope_key=scope_key)
        if sticker is not None:
            plan.attachment = sticker
            media.note_sent(scope_key, sticker_id=getattr(sticker, "id", ""))
            summary = getattr(sticker, "visual_summary", "") or getattr(sticker, "display_name", "")
            narrate().say("mind", f"顺手发了个表情：{summary}")

    async def _respond(
        self,
        event: MessageEvent,
        text: str,
        *,
        is_group: bool,
        participation: bool = False,
        social: Any = None,
    ) -> None:
        bot = self.bot
        session_id = self._session_id(event)
        bot.metrics.inc("ai_requests")

        # The user spoke: clear any pending proactive invitation.
        await bot.behavior.note_user_activity(session_id)
        if is_group:
            await bot.behavior.note_user_activity(f"group:{event.group_id}")

        history = await bot.ai.conversations.get_context(session_id)
        last_at = history[-1].created_at if history and hasattr(history[-1], "created_at") else None
        elapsed = time.time() - (last_at or 0) if last_at else None

        response_goal = getattr(social, "response_goal", "") if social is not None else ""
        extra = f"（你此刻的回应目标：{response_goal}）" if response_goal else None

        # v1.1: incoming media (image -> understanding; sticker/face -> context).
        media_context, media_items = await self._incoming_media(event)
        self._schedule_collection(event, media_items)

        # Media-only message (sticker/face/image with no caption): the triggers
        # let it through, but the AI still needs a non-empty user turn to react.
        if not text and not media_items:
            return
        if not text:
            media_kind = {
                "image": "一张图片",
                "native_face": "一个表情",
                "sticker": "一个表情包",
            }.get(media_items[0].media_type, "一条媒体消息")
            text = f"（发来{media_kind}）"

        try:
            reply = await bot.character.respond(
                session_id,
                event.user_id,
                text,
                history=history,
                user_name=event.sender.display_name,
                is_group=is_group,
                time_context=bot.behavior.time_context(),
                extra_instruction=extra,
                media_context=media_context,
            )
        except AIError as exc:
            bot.metrics.inc("ai_errors")
            bot.log.warning("Character request failed for session=%s: %s", session_id, exc)
            narrate().warn("没想出来，先随便应一句", detail=str(exc)[:80])
            await bot.response_delivery.deliver(
                DeliveryTarget.from_event(event),
                bot.response_planner.plan_reply(
                    random.choice(BUSY_REPLIES),
                    state=await bot.character.states.load(),
                    force_single_message=True,
                ),
            )
            return
        except Exception:  # noqa: BLE001 - never let the plugin kill the bus
            bot.log.exception("Unexpected error in character plugin")
            bot.metrics.inc("ai_errors")
            return

        await bot.behavior.observe_conversation(text, reply)
        await bot.ai.conversations.append_user_message(session_id, text)
        await bot.ai.conversations.append_assistant_message(session_id, reply)

        plan = bot.response_planner.plan_reply(
            reply,
            state=await bot.character.states.load(),
            relationship=await bot.relationships.get(event.user_id),
            time_context=bot.behavior.time_context(),
            seconds_since_last_exchange=elapsed,
        )
        await self._attach_expression(plan, event, text, media_items, is_group)
        await bot.response_delivery.deliver(DeliveryTarget.from_event(event), plan)

        if is_group and social is not None:
            group_id = str(event.group_id)
            sent_id = str(bot.response_delivery.last_sent_ids.get(f"group:{group_id}", "") or "")
            await bot.social.after_reply(
                group_id=group_id,
                message_id=sent_id,
                content=reply,
                topic=getattr(social, "topic", "") or "",
                participants=[str(event.user_id)],
            )
        elif is_group and participation:
            await bot.behavior.note_group_participation(event.group_id)

        if bot.character.extractor is not None:
            extract_group_id = str(event.group_id) if event.is_group else None
            await bot.character.extractor.schedule(
                session_id, str(event.user_id), extract_group_id, text, reply
            )
            await bot.behavior.log_event(
                BehaviorEvent(
                    type="reply_sent",
                    scope_key=session_id,
                    user_id=str(event.user_id),
                    group_id=extract_group_id,
                    reason="participation" if participation else "direct",
                    created_at=int(time.time()),
                    status="done",
                )
            )
