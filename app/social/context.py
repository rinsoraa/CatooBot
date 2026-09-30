"""SocialCharacterContext builder (v0.9 §30/§31/§55).

Assembles the *smallest useful* character picture for social judgment: current
state, activity, focus, mood, interests, active topics, and only the memory /
relationship facts relevant to the current talk. It never dumps the whole
Memory store into the observer prompt (§31).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.world.runtime import WorldRuntime


class SocialContextBuilder:
    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("CatooBot.Social")

    def character_block(
        self,
        *,
        persona: Any,
        state: Any,
        world: WorldRuntime | None = None,
        topics: list[str] | None = None,
        memories: list[str] | None = None,
        relationships: dict[str, str] | None = None,
        activity_context: dict | None = None,
    ) -> str:
        """A compact, prompt-safe summary of the character for the observer."""
        identity = getattr(persona, "identity", None)
        personality = getattr(persona, "personality", None)
        name = (getattr(identity, "name", "") or "") or "她"

        lines: list[str] = [f"角色：{name}"]
        occupation = getattr(identity, "occupation", None)
        if identity is not None and occupation:
            lines.append(f"身份：{occupation}")
        if personality is not None:
            interests = list(getattr(personality, "interests", []) or [])
            likes = list(getattr(personality, "likes", []) or [])
            if interests or likes:
                lines.append("兴趣：" + "、".join((interests or []) + (likes or []))[:120])

        state_bits: list[str] = []
        mood = getattr(state, "mood", "") or ""
        if mood:
            state_bits.append(f"心情 {mood}")
        activity = getattr(state, "activity", "") or ""
        where = getattr(state, "location", "") or ""
        if activity:
            state_bits.append(f"正在 {activity}" + (f"（{where}）" if where else ""))
        focus = getattr(state, "current_focus", "") or ""
        if focus:
            state_bits.append(f"当前关注 {focus}")
        if state_bits:
            lines.append("当前状态：" + "；".join(state_bits))

        # v1.0: the world's episode gives the *richest* activity picture — detail,
        # elapsed time and any interaction overlay (spec §47/§86/§92).
        if activity_context:
            detail = str(activity_context.get("activity_detail") or "").strip()
            elapsed = activity_context.get("elapsed_minutes", 0)
            overlay = activity_context.get("interaction_overlay")
            if detail and detail != activity:
                lines.append(f"活动详情：{detail}（已进行约 {elapsed} 分钟）")
            if overlay:
                overlay_text = {
                    "chatting": "正在和用户聊天",
                    "assisting_user": "正在帮用户处理任务",
                }.get(str(overlay), str(overlay))
                lines.append(f"当前交互：{overlay_text}（不影响正在做的事）")
        elif world is not None and getattr(world, "enabled", False):
            try:
                world_line = world.state.describe()
                if world_line:
                    lines.append(f"此刻：{world_line}")
            except Exception:  # noqa: BLE001 - context is best-effort
                pass

        if topics:
            lines.append("活跃话题：" + "、".join(topics[:5]))
        if memories:
            lines.append("相关记忆：" + "；".join(memories[:5]))
        if relationships:
            relation_summary = "；".join(
                f"{uid}={stage}" for uid, stage in list(relationships.items())[:8]
            )
            lines.append(f"与参与者关系：{relation_summary}")

        return "\n".join(lines)

    def message_block(
        self,
        messages: list[Any],
        *,
        title: str = "最近群聊",
        limit: int = 20,
    ) -> str:
        """Grouped, speaker-attributed transcript for the observer (§54/§56)."""
        lines: list[str] = [f"{title}："]
        for message in list(messages)[-limit:]:
            speaker = message.nickname or f"用户{message.user_id}"
            if message.is_bot_message:
                speaker = "（我）"
            lines.append(f"{speaker}：{message.content}")
        return "\n".join(lines)
