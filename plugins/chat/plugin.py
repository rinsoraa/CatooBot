"""Character plugin: the only QQ-facing surface.

Since v0.4 the plugin is deliberately thin — it contains no behaviour rules:

    Conversation runtime decides *how messages become turns* (v1.2)
    Behaviour engine decides *whether* to speak (and why)
    Character runtime decides *what* to say (persona + memory + state)
    Response planner/delivery decide *how* to say it (delay, sequence)

The plugin only *submits* messages to the ConversationTurnRuntime and binds
the respond/deliver/post-reply callbacks; every message goes through the turn
runtime — there is no direct path.

本文件同时引用 v1.1 / v1.2 / v2.0 §n（媒体/回合/沙盒）；裸 §N 才指 v2.0。"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.ai.errors import AIError
from app.character.turn import TurnOrigin
from app.message.message import Message
from app.message.segment import FileSegment, VideoSegment
from app.plugins.api import PluginApi
from app.plugins.base import Plugin
from app.response.delivery import DeliveryTarget
from app.utils.narrator import narrate

if TYPE_CHECKING:
    from app.message.event import MessageEvent

#: why the behaviour engine decided what it decided (console narration)
REASON_TEXT = {
    "participation_rate": "按你设的参与频率，轮到她接话了",
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
    "participation_disabled": "群聊参与关着（非 @ 不插话）",
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

_MEDIA_PLACEHOLDER = {
    "image": "一张图片",
    "native_face": "一个表情",
    "sticker": "一个表情包",
    "video": "一个视频",
    "file": "一个文件",
}


@dataclass
class _Recognition:
    """A background recognition in flight for one incoming media item."""

    item: Any
    task: asyncio.Task


class CharacterPlugin(Plugin):
    name = "character"
    version = "1.2.0"
    description = "Natural-language character chat with conversation turns + continuity"

    async def on_load(self, bot: PluginApi) -> None:
        self._background_tasks: set[asyncio.Task] = set()
        bot.event_bus.on("message.private", self._on_private)
        bot.event_bus.on("message.group", self._on_group)
        bot.event_bus.on("notice.poke", self._on_poke)
        runtime = getattr(bot, "conversation", None)
        if runtime is not None:
            runtime.bind(
                respond=self._respond_for_turn,
                deliver=self._deliver_for_turn,
                post_reply=self._post_reply,
                turn_finished=self._turn_finished,
                social_provider=self._social_for_turn,
            )

    # ------------------------------------------------------------- triggers

    async def _on_private(self, event: MessageEvent) -> None:
        if not self._ready():
            self.bot.log.warning("[Behavior] private reply skipped (character/AI not ready)")
            narrate().quiet("这条先不回", detail="角色或 AI 还没就绪")
            return
        text = event.message.text.strip()
        if not text and not self._has_media(event):
            return
        await self._sandbox_external(event, text, mentioned=False, reply_to_bot=False)
        decision = await self.bot.behavior.consider_private(event, text)
        if not decision.respond:
            self.bot.log.info("[Behavior] private reply skipped (%s)", decision.reason)
            narrate().quiet("这条先不回", detail=REASON_TEXT.get(decision.reason, decision.reason))
            return

        narrate().judge("私聊 → 要回", detail=REASON_TEXT.get(decision.reason, decision.reason))
        await self._submit(event, text, mentioned=False, reply_to_bot=False)

    async def _on_group(self, event: MessageEvent) -> None:
        if not self._ready():
            self.bot.log.warning("[Behavior] group reply skipped (character/AI not ready)")
            narrate().quiet("群里这条先不回", detail="角色或 AI 还没就绪")
            return
        mentioned = event.message.is_mentioned(event.self_id)
        prompt_view = Message(event.message.segments)
        if mentioned:
            prompt_view.strip_prefix_at(event.self_id)
        text = prompt_view.text.strip()
        reply_to_bot = self._replies_to_bot(event, str(event.group_id))
        # One line per group message — a silent mention-drop must never hide again.
        self.bot.log.info(
            "[Group] mentioned=%s reply_to_bot=%s self_id=%s user=%s group=%s text=%r",
            mentioned,
            reply_to_bot,
            event.self_id,
            event.user_id,
            event.group_id,
            text[:40],
        )
        if not text and not self._has_media(event):
            return
        # Task 22: learn short phrases from how this group talks (opt-in).
        learner = getattr(self.bot, "expression_learner", None)
        if learner is not None and text:
            try:
                await learner.learn_message(
                    group_id=str(event.group_id),
                    user_id=str(event.user_id),
                    message_id=str(getattr(event, "message_id", "") or ""),
                    text=text,
                    sender_is_bot=str(event.user_id) == str(event.self_id),
                )
            except Exception:  # noqa: BLE001 - learning must never break chat
                self.bot.log.exception("[Expression] learn failed (ignored)")
        await self._sandbox_external(event, text, mentioned=mentioned, reply_to_bot=reply_to_bot)

        # every raw message is observed; the burst is decided later (v1.2).
        social = getattr(self.bot, "social", None)
        if social is not None and getattr(social, "enabled", False):
            await social.observe_only(
                group_id=str(event.group_id),
                message_id=str(getattr(event, "message_id", "") or ""),
                user_id=str(event.user_id),
                nickname=event.sender.display_name,
                text=text,
                reply_to_bot=reply_to_bot,
            )
        await self.bot.behavior.note_user_activity(f"group:{event.group_id}")
        await self._submit(event, text, mentioned=mentioned, reply_to_bot=reply_to_bot)

    # ------------------------------------------------------ v2.0 sandbox

    async def _sandbox_external(
        self, event: MessageEvent, text: str, *, mentioned: bool, reply_to_bot: bool
    ) -> None:
        """QQ is the outside world: adapter → ExternalWorldEvent → sandbox (§5).

        The plugin supplies *primitives* only; protocol-free translation and
        semantic cue parsing live in :mod:`app.sandbox.external_adapters`, and
        every effect flows through the sandbox's own influence pipeline —
        the plugin never touches sandbox state (v2.1 Phase 3).
        """
        sandbox = getattr(self.bot, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            return
        from app.sandbox.external_adapters import adapt_qq_message

        config = self.bot.config.sandbox
        user_id = str(event.user_id)
        is_core = user_id in {str(uid) for uid in config.core_friend_ids}
        space_id = ""
        if event.is_group and event.group_id is not None:
            mapped = config.social_space_map.get(str(event.group_id), "")
            space_id = mapped or f"qq:{event.group_id}"
        try:
            world_event = adapt_qq_message(
                message_id=str(getattr(event, "message_id", "") or ""),
                actor_id=user_id,
                text=text,
                display_name=event.sender.display_name,
                is_group=event.is_group,
                group_id=str(event.group_id) if event.is_group else "",
                mentioned=mentioned,
                reply_to_bot=reply_to_bot,
                is_core_actor=is_core,
                social_space_id=space_id,
            )
            if await sandbox.submit_external(world_event):
                await sandbox.wakeup()
        except Exception:  # noqa: BLE001 - sandbox trouble must not break chat
            self.bot.log.exception("[Sandbox] external event failed")

    async def _on_poke(self, event: Any) -> None:
        """A poke *at her* enters the normal turn pipeline (Task 19).

        Deliberately restrained: no new behaviour rule, no forced reply — the
        poke becomes an addressed input like any other, so the existing
        behaviour/social decision still decides whether she says anything.
        """
        if not self._ready() or event.target_id != self.bot.self_id:
            return
        if event.user_id is None:
            return
        is_group = event.group_id is not None
        session_id = f"group:{event.group_id}" if is_group else f"private:{event.user_id}"
        narrate().sense(
            f"👉 {event.user_id} 戳了戳她",
            detail=f"{'群 ' + str(event.group_id) if is_group else '私聊'} · user {event.user_id}",
        )
        continuity = getattr(self.bot, "continuity", None)
        if continuity is not None:
            await continuity.observe_message(
                str(event.user_id), "（戳了戳你）", session_id=session_id
            )
        await self.bot.conversation.submit(
            {
                "session_id": session_id,
                "is_group": is_group,
                "group_id": str(event.group_id) if is_group else None,
                "user_id": str(event.user_id),
                "nickname": str(event.user_id),
                "message_id": "",
                "text": "（戳了戳你）",
                "mentioned": True,
                "reply_to_bot": False,
                "media_count": 0,
                "meta": {
                    "media_items": [],
                    "deferred_images": [],
                    "media_note": "",
                    "recognition": None,
                },
            }
        )

    # ---------------------------------------------------------- v1.2 submit

    async def _submit(
        self,
        event: MessageEvent,
        text: str,
        *,
        mentioned: bool,
        reply_to_bot: bool,
    ) -> None:
        bot = self.bot
        runtime = bot.conversation
        session_id = self._session_id(event)
        media_note, items, deferred, recognition = self._submit_media(event)
        if not text and items:
            first = items[0].media_type
            text = f"（发来{_MEDIA_PLACEHOLDER.get(first, '一条媒体消息')}）"
        if not text:
            # video / file / unknown media the pipeline does not handle — give
            # them a readable placeholder so the message is never invisible.
            text = self._unrecognized_media_placeholder(event.message)
        if bot.continuity is not None:
            try:
                await bot.continuity.observe_message(
                    str(event.user_id), text, session_id=session_id
                )
            except Exception:  # noqa: BLE001
                bot.log.exception("[Continuity] observe_message failed")
        await runtime.submit(
            {
                "session_id": session_id,
                "is_group": event.is_group,
                "group_id": str(event.group_id) if event.is_group else None,
                "user_id": str(event.user_id),
                "nickname": event.sender.display_name,
                "message_id": str(getattr(event, "message_id", "") or ""),
                "text": text,
                "mentioned": mentioned,
                "reply_to_bot": reply_to_bot,
                "media_count": len(items),
                "meta": {
                    "media_items": items,
                    "deferred_images": deferred,
                    "media_note": media_note,
                    "recognition": recognition,
                },
            }
        )

    def _submit_media(
        self, event: MessageEvent
    ) -> tuple[str, list[Any], list[Any], _Recognition | None]:
        """Cheap classification now; sticker/image recognition starts as a task.

        The turn's *decision* waits for that task (bounded), so the order is
        识别 → 判断 → 收藏 instead of deciding first and recognizing later.
        """
        media = getattr(self.bot, "media", None)
        if media is None or not media.enabled:
            return "", [], [], None
        items = media.normalize(
            event.message,
            source_message_id=str(getattr(event, "message_id", "") or ""),
            source_user_id=str(event.user_id),
            source_group_id=str(event.group_id) if event.is_group else "",
        )
        if not items:
            return "", [], [], None
        notes: list[str] = []
        deferred: list[Any] = []
        for item in items:
            if item.media_type == "image":
                deferred.append(item)
                narrate().say(
                    "vision",
                    "收到图片",
                    detail="识别中（结果用于判断是否接话）" if item.url else "",
                )
            elif item.media_type == "native_face":
                face = media.faces.get(item.face_id)
                note = f"表情：{face.display_name or 'QQ 表情'}" if face else "表情：QQ 表情"
                notes.append(note)
                narrate().say("vision", note)
            elif item.media_type == "sticker":
                summary = item.emoji_summary or item.emoji_key or "表情包"
                notes.append(f"表情包：{summary}")
                narrate().say(
                    "vision",
                    f"收到表情包：{summary}",
                    detail="识别中（结果用于判断是否接话）" if item.url else "",
                )
                # The reply path also wants the real summary — the background
                # pass caches the vision result by sha256, so it costs nothing.
                deferred.append(item)
        recognition: _Recognition | None = None
        first = next(
            (item for item in items if item.media_type in ("sticker", "image") and item.url),
            None,
        )
        if first is not None:
            scope = (
                f"group:{first.source_group_id}"
                if first.source_group_id
                else f"private:{first.source_user_id}"
            )
            task = asyncio.create_task(media.recognize(first, scope_key=scope))
            self._track(task)
            recognition = _Recognition(item=first, task=task)
        return "；".join(notes), items, deferred, recognition

    # --------------------------------------------------- v1.2 turn callbacks

    async def _recognize_turn(self, turn: Any) -> str:
        """Await the media recognition, narrate it, use it (识别 → 判断 → 收藏)."""
        pending = turn.meta.pop("recognition", None)
        if pending is None:
            return str(turn.meta.get("recognized_text", "") or "")
        media = getattr(self.bot, "media", None)
        if media is None:
            return ""
        timeout = float(getattr(self.bot.config.media, "recognition_timeout_seconds", 12.0))
        try:
            outcome = await asyncio.wait_for(asyncio.shield(pending.task), timeout=timeout)
        except TimeoutError:
            narrate().quiet(
                "还在识别",
                detail=f"超过 {timeout:.0f}s，先继续判断；结果出来再收藏",
            )
            # shield: the real task keeps running, so a late result can still
            # be narrated and collected (the turn is no longer reachable).
            pending.task.add_done_callback(
                lambda done, t=turn, it=pending.item: self._track(
                    asyncio.create_task(self._finish_late_recognition(done, t, it))
                )
            )
            return ""
        except Exception:  # noqa: BLE001 - recognition never breaks the turn
            self.bot.log.exception("[Media] recognition failed")
            return ""
        return self._apply_recognition(turn, outcome, pending.item)

    def _apply_recognition(self, turn: Any, outcome: Any, item: Any) -> str:
        if outcome.status == "throttled":
            narrate().quiet("这张先不认了", detail="本会话每小时后台识别上限到了（可调配置）")
            return ""
        text = str(outcome.vision_text or "")
        if text:
            narrate().say("vision", f"看懂了：{text}")
            turn.meta["recognized_text"] = text
        turn.meta["recognized_item"] = item
        turn.meta["recognition_outcome"] = outcome
        return text

    def _collect_recognized(self, turn: Any) -> None:
        """Schedule acquisition after the turn's decision — narrates 识别→判断→收藏.

        Idempotent via a flag (not a pop): the reply callback still needs the
        outcome's vision (e.g. the caption) after collection was scheduled.
        """
        if turn is None:
            return
        meta = getattr(turn, "meta", {}) or {}
        if meta.get("recognition_collected"):
            return
        outcome = meta.get("recognition_outcome")
        if outcome is None:
            return
        meta["recognition_collected"] = True
        self._schedule_collect_recognized(turn, outcome)

    async def _finish_late_recognition(self, task: asyncio.Task, turn: Any, item: Any) -> None:
        """A recognition that finished after the timeout: narrate + collect."""
        try:
            outcome = task.result()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            return
        if outcome.status != "done" or turn is None:
            return
        if outcome.vision_text:
            narrate().say("vision", f"看懂了：{outcome.vision_text}", detail="迟到的识别结果")
            turn.meta["recognized_text"] = outcome.vision_text
        turn.meta["recognized_item"] = item
        turn.meta["recognition_outcome"] = outcome
        self._collect_recognized(turn)

    def _schedule_collect_recognized(self, turn: Any, outcome: Any) -> None:
        """Acquisition after recognition — background, never blocks the reply."""
        media = getattr(self.bot, "media", None)
        meta = getattr(turn, "meta", {}) or {}
        item = meta.get("recognized_item")
        if item is None:
            items = list(meta.get("media_items", []) or [])
            item = next((i for i in items if i.media_type == "sticker"), None)
        if media is None or item is None or outcome.vision is None:
            return

        async def keep() -> None:
            try:
                decision = await media.collect_recognized(item, outcome.vision)
            except Exception:  # noqa: BLE001 - collection must never break chat
                self.bot.log.exception("[Media] sticker collection failed")
                return
            if decision is not None and decision.decision == "save":
                summary = item.emoji_summary or outcome.vision_text or "表情包"
                narrate().say("mind", f"这张收进表情库了：{summary}")

        self._track(asyncio.create_task(keep()))

    def _track(self, task: asyncio.Task) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _social_for_turn(self, turn: Any) -> Any:
        """Group participation decided once per merged burst (v1.2 §82/§83).

        Sticker recognition runs *before* the decision so 静默/要回 is based on
        what the sticker actually says (识别 → 判断 → 收藏).
        """
        recognized = await self._recognize_turn(turn)
        if not turn.group_id:
            self._collect_recognized(turn)
            return None
        social = getattr(self.bot, "social", None)
        if social is None or not getattr(social, "enabled", False):
            self._collect_recognized(turn)
            if not (turn.mentioned or turn.reply_to_bot):
                from app.social.models import ParticipationDecision

                return ParticipationDecision(decision="ignore", reason_code="social_disabled")
            return None
        group_enabled = await self._group_enabled(int(turn.group_id))
        decision_text = turn.text
        if recognized:
            decision_text = f"{decision_text} ／ 表情包内容：{recognized}".strip()
        decision = await social.decide_for_turn(
            group_id=str(turn.group_id),
            user_id=turn.user_id,
            nickname=turn.nickname,
            text=decision_text,
            mentioned=turn.mentioned,
            reply_to_bot=turn.reply_to_bot,
            group_enabled=group_enabled,
        )
        if not decision.should_reply:
            turn.meta["silence_text"] = REASON_TEXT.get(decision.reason_code, decision.reason_code)
        elif decision.reason_code == "participation_rate":
            narrate().judge(
                "群聊 → 按参与频率接一句",
                detail=REASON_TEXT.get("participation_rate", "participation_rate"),
            )
        turn.meta["social_reason_code"] = decision.reason_code
        self._collect_recognized(turn)
        return decision

    async def _respond_for_turn(self, turn: Any, decision: Any, generation: Any) -> Any:
        """One turn → one ResponsePlan (the injected respond callback)."""
        bot = self.bot
        if not generation.is_current():
            return None
        session_id = turn.session_id
        is_group = turn.group_id is not None
        # ai_requests / ai_errors are counted by the model router itself
        # (Bot._on_router_event) — a turn here is not a model call.

        meta = turn.meta
        recognized = await self._recognize_turn(turn)
        outcome = meta.get("recognition_outcome")
        self._collect_recognized(turn)
        media_context = str(meta.get("media_note", "") or "")
        recognized_item = meta.get("recognized_item")
        label = (
            "表情包"
            if recognized_item is not None and recognized_item.media_type == "sticker"
            else "图片"
        )
        if recognized and f"{label}：" not in media_context:
            media_context = f"{media_context}；{label}：{recognized}".lstrip("；")
            caption = "；".join(
                getattr(getattr(outcome, "vision", None), "ocr_text", []) or []
            ).strip()
            if caption:
                media_context += (
                    f"；图上的配字「{caption}」就是对方想表达的话——"
                    "直接回应配字内容就好，不要描述或评价表情包本身"
                )
        media_items = list(meta.get("media_items", []) or [])
        # The recognized item's summary is already in the context — don't run
        # (and narrate) the reply-path vision for the very same image again.
        deferred = [
            item
            for item in (meta.get("deferred_images", []) or [])
            if not (recognized and item is recognized_item)
        ]
        image_vision: list[tuple[Any, Any]] = []
        if deferred:
            media = getattr(bot, "media", None)
            if media is not None:
                for item in deferred:
                    vision = await media.understand_image(item)
                    seen = vision.as_text()
                    image_vision.append((item, vision))
                    if seen:
                        label = "表情包" if item.media_type == "sticker" else "图片"
                        media_context = f"{media_context}；{label}：{seen}".lstrip("；")
                        narrate().say("vision", f"看懂了：{seen}")
                        caption = "；".join(vision.ocr_text).strip()
                        if caption:
                            media_context += (
                                f"；图上的配字「{caption}」就是对方想表达的话——"
                                "直接回应配字内容就好，不要描述或评价表情包本身"
                            )
                        # v1.2: a vision-confirmed meme becomes a real sticker
                        # candidate — saved silently in the background.
                        if media.looks_like_sticker(vision):
                            self._schedule_collection(
                                None, [media.as_sticker_candidate(item)], vision=vision
                            )
                    else:
                        narrate().say(
                            "vision",
                            "这张图没看懂",
                            detail="视觉模型不可用 / 分析失败（详情见日志）",
                        )
        text = turn.text

        history = await bot.ai.conversations.get_context(session_id)
        last_at = history[-1].created_at if history and hasattr(history[-1], "created_at") else None
        elapsed = time.time() - (last_at or 0) if last_at else None

        response_goal = str(meta.get("response_goal", "") or "")
        extra = f"（你此刻的回应目标：{response_goal}）" if response_goal else None
        if image_vision:
            turn.meta["image_vision"] = image_vision
        if decision.response_style == "brief":
            extra = f"{extra or ''}（这条简短回应就好）".strip()

        # v1.2 continuity layers (all optional, all relevance-gated).
        continuity_block: dict | None = None
        profile = None
        shared: list[str] | None = None
        if bot.continuity is not None:
            state = bot.continuity.state
            loops = await bot.continuity.open_loop_context(turn.user_id)
            if any(
                (
                    state.current_interest,
                    state.unfinished_thought,
                    state.recent_events,
                    state.last_response_context,
                    loops,
                )
            ):
                continuity_block = {
                    "current_interest": state.current_interest,
                    "unfinished_thought": state.unfinished_thought,
                    "recent_events": state.recent_events[-3:],
                    "last_response_context": state.last_response_context,
                    "open_loops": [loop.summary for loop in loops[:3]],
                }
            profile = await bot.continuity.profile(turn.user_id)
            shared_experiences = await bot.continuity.recall_shared(turn.user_id, text)
            shared = [exp.summary for exp in shared_experiences] or None

        trace: dict = {}
        try:
            reply = await bot.character.respond(
                session_id,
                int(turn.user_id) if str(turn.user_id).isdigit() else turn.user_id,
                text,
                # Phase 3E.1：真实用户发来的消息 —— 只有这个来源才允许 LOW Minecraft 动作
                turn_origin=TurnOrigin.USER,
                history=history,
                user_name=turn.nickname or None,
                is_group=is_group,
                time_context=bot.behavior.time_context(),
                extra_instruction=extra,
                media_context=media_context,
                continuity=continuity_block,
                interaction_profile=profile,
                shared_experiences=shared,
                context_trace=trace,
            )
        except AIError as exc:
            bot.log.warning("Character request failed for session=%s: %s", session_id, exc)
            narrate().warn("没想出来，先随便应一句", detail=str(exc)[:80])
            return bot.response_planner.plan_reply(
                random.choice(BUSY_REPLIES),
                state=await bot.character.states.load(),
                force_single_message=True,
            )
        # v1.2 §17: the answer may be obsolete by the time the model returns.
        if not generation.is_current():
            return None

        await bot.behavior.observe_conversation(text, reply)
        await bot.ai.conversations.append_user_message(session_id, text)
        await bot.ai.conversations.append_assistant_message(session_id, reply)

        plan = bot.response_planner.plan_reply(
            reply,
            state=await bot.character.states.load(),
            relationship=await bot.relationships.get(
                int(turn.user_id) if str(turn.user_id).isdigit() else turn.user_id
            ),
            time_context=bot.behavior.time_context(),
            seconds_since_last_exchange=elapsed,
        )
        await self._attach_expression(plan, turn, text, media_items, is_group)
        turn.meta["context_trace"] = trace
        return plan

    async def _deliver_for_turn(self, plan: Any, turn: Any, is_current: Any) -> list[str]:
        if turn.group_id:
            target = DeliveryTarget(group_id=int(turn.group_id))
        else:
            target = DeliveryTarget(user_id=int(turn.user_id))
        return await self.bot.response_delivery.deliver(target, plan, is_current=is_current)

    async def _post_reply(self, turn: Any, decision: Any, reply: str) -> None:
        bot = self.bot
        session_id = turn.session_id
        is_group = turn.group_id is not None
        # Task 21: what she saw this turn can become a memory worth recalling.
        await self._remember_images(turn)
        # Task 20: one observation row per answered turn (settled later).
        feedback = getattr(bot, "reply_feedback", None)
        if feedback is not None:
            try:
                await feedback.record_turn(
                    turn_id=str(turn.turn_id),
                    scope_key=session_id,
                    reason_code=str(turn.meta.get("social_reason_code", "") or ""),
                    is_group=is_group,
                )
            except Exception:  # noqa: BLE001 - feedback never breaks a reply
                bot.log.exception("[Social.Feedback] record_turn failed")
        if is_group:
            group_id = str(turn.group_id)
            sent_id = str(bot.response_delivery.last_sent_ids.get(f"group:{group_id}", "") or "")
            social = getattr(bot, "social", None)
            if social is not None and getattr(social, "enabled", False):
                await social.after_reply(
                    group_id=group_id,
                    message_id=sent_id,
                    content=reply,
                    topic=getattr(social, "topic", "") or "",
                    participants=[turn.user_id],
                )
        if bot.character.extractor is not None:
            extract_group_id = str(turn.group_id) if is_group else None
            await bot.character.extractor.schedule(
                session_id, turn.user_id, extract_group_id, turn.text, reply
            )
            from app.behavior.models import BehaviorEvent as _Event

            await bot.behavior.log_event(
                _Event(
                    type="reply_sent",
                    scope_key=session_id,
                    user_id=turn.user_id,
                    group_id=extract_group_id,
                    reason=decision.intent or "direct",
                    created_at=int(time.time()),
                    status="done",
                )
            )
        if bot.continuity is not None:
            bot.continuity.schedule_update_after_reply(turn, decision, reply)

    async def _turn_finished(self, turn: Any) -> None:
        continuity = getattr(self.bot, "continuity", None)
        if continuity is not None:
            await continuity.store.save_turn(turn)

    async def _remember_images(self, turn: Any) -> None:
        """One episodic memory per turn for the photos worth remembering."""
        bot = self.bot
        manager = getattr(bot, "memory", None)
        if manager is None:
            return
        entries: list[tuple[Any, Any]] = list(turn.meta.pop("image_vision", []) or [])
        outcome = turn.meta.get("recognition_outcome")
        item = turn.meta.get("recognized_item")
        if outcome is not None and item is not None:
            entries.append((item, outcome.vision))
        if not entries:
            return
        text = str(turn.text or "").strip()
        from app.media.memory_bridge import record_image_memories

        await record_image_memories(
            manager,
            scope="group" if turn.group_id else "user",
            ref=str(turn.group_id if turn.group_id else turn.user_id),
            entries=entries,
            user_id=str(turn.user_id),
            group_id=str(turn.group_id) if turn.group_id else None,
        )
        if text:
            bot.log.debug("[Media.Memory] image memory considered for %s", turn.session_id)

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
        """True when the message carries media, not just text/at/reply.

        image/face/mface feed the media pipeline; video/file (and any other
        non-text, non-@, non-reply segment) are recognised here so a bare media
        message is never dropped as an invisible blank.
        """
        return any(seg.type not in ("text", "at", "reply") for seg in event.message)

    @staticmethod
    def _unrecognized_media_placeholder(message: Message) -> str:
        """Readable placeholder for media the media pipeline does not handle.

        image/face/mface already flow through ``media.normalize`` (and get their
        own placeholder); this covers video / file / anything else, so a
        caption-less media message reaches the character instead of becoming an
        empty string that also trips the ``too_short`` silence gate.
        """
        for segment in message:
            if isinstance(segment, VideoSegment):
                return "（发来一个视频）"
            if isinstance(segment, FileSegment):
                return "（发来一个文件）"
            if segment.type not in (
                "text",
                "at",
                "reply",
                "image",
                "face",
                "mface",
                "video",
                "file",
            ):
                return "（发来一条媒体消息）"
        return ""

    def _schedule_collection(
        self, event: MessageEvent | None, items: list[Any], *, vision: Any = None
    ) -> None:
        """Background sticker recognition + acquisition — never blocks (v1.1 §18).

        Runs whether or not the character replies to this message (option A):
        QQ-marked stickers get recognized and possibly kept, with a per-scope
        hourly budget so a spammy group can't burn vision calls.
        """
        media = getattr(self.bot, "media", None)
        if media is None or not items:
            return

        def scope_of(item: Any) -> str:
            if item.source_group_id:
                return f"group:{item.source_group_id}"
            if item.source_user_id:
                return f"private:{item.source_user_id}"
            return "global"

        async def collect() -> None:
            for item in items:
                try:
                    outcome = await media.background_recognize_and_collect(
                        item, scope_key=scope_of(item), vision=vision
                    )
                except Exception:  # noqa: BLE001 - collection must never break chat
                    self.bot.log.exception("[Media] sticker collection failed")
                    continue
                if outcome.status == "throttled":
                    narrate().quiet(
                        "这张先不认了", detail="本会话每小时后台识别上限到了（可调配置）"
                    )
                    continue
                if outcome.status != "done":
                    continue
                if outcome.vision_text:
                    narrate().say("vision", f"看懂了：{outcome.vision_text}")
                decision = outcome.decision
                if decision is not None and decision.decision == "save":
                    summary = item.emoji_summary or outcome.vision_text or "表情包"
                    narrate().say("mind", f"这张收进表情库了：{summary}")
                elif decision is not None:
                    self.bot.log.debug("[Media] sticker not kept: %s", decision.reason_codes)

        task = asyncio.create_task(collect())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def drain_background(self, timeout: float = 5.0) -> None:
        """Test helper: wait for the background media jobs to finish."""
        while self._background_tasks:
            pending = list(self._background_tasks)
            await asyncio.wait(pending, timeout=timeout)

    async def _attach_expression(
        self, plan: Any, turn: Any, text: str, media_items: list[Any], is_group: bool
    ) -> None:
        """Decide whether to attach a sticker to the outgoing reply (v1.1 §23-§29)."""
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
        # v1.2 §90: turn-level affect hints the expression choice.
        emotion_hint = ""
        continuity = getattr(self.bot, "continuity", None)
        if continuity is not None:
            affect = continuity.state.affect
            if affect.level("amusement") >= 0.5:
                emotion_hint = "好笑"
            elif affect.level("warmth") >= 0.5:
                emotion_hint = "开心"
        context = ExpressionContext(
            incoming_media_type=incoming_type,
            incoming_sticker_semantics=semantics,
            emotion_hint=emotion_hint,
        )
        scope_key = turn.session_id
        decision = media.decide_expression(context, user_text=text, scope_key=scope_key)
        if not decision.selection_required:
            return
        sticker = await media.select(decision, scope_key=scope_key)
        if sticker is not None:
            plan.attachment = sticker
            media.note_sent(scope_key, sticker_id=getattr(sticker, "id", ""))
            summary = getattr(sticker, "visual_summary", "") or getattr(sticker, "display_name", "")
            narrate().say("mind", f"顺手发了个表情：{summary}")
