"""External event adapters (v2.1 Phase 3 §5/§18): protocol → ExternalWorldEvent.

One adapter per outside channel; each takes *primitives* (ids, text, flags) so
it is fully testable without any OneBot/NapCat/QQ object, and returns the
protocol-free :class:`~app.sandbox.external.ExternalWorldEvent`.

The adapter is the sanctioned place for *semantic parsing* (§18): mapping
"来一起联机" onto ``semantic_kind=game_invitation`` / ``target_activity=gaming``
uses a system-level cue vocabulary, so any future phrasing or any other
character's world flows through the same pipeline. The sandbox core never sees
raw message text for behavior routing, and no character name is involved (§17).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.sandbox.external import (
    ExternalSource,
    ExternalUrgency,
    ExternalWorldEvent,
)

#: system-level cue vocabulary (§18): cue words → (semantic_kind, activity).
#: Generic phrasing families, never a character-specific sentence.
ACTIVITY_CUES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("联机", "一起玩", "来打", "开服", "上号", "组队", "带我"), "game_invitation", "gaming"),
    (("一起看", "看番", "看剧", "追番"), "activity_invitation", "entertainment"),
    (("出门", "一起去", "逛街", "散步"), "activity_invitation", "outdoors"),
    (("语音", "开麦", "连麦"), "activity_invitation", "gaming"),
)

#: legacy meaning strings kept for the Phase 2 API surface
_LEGACY_MEANINGS = {
    "game_invitation": "invitation_game",
    "activity_invitation": "invitation_activity",
}


def classify_semantics(text: str) -> tuple[str, str]:
    """(semantic_kind, target_activity) from the cue vocabulary — or ("", "")."""
    body = text or ""
    for cues, semantic, activity in ACTIVITY_CUES:
        if any(cue in body for cue in cues):
            return semantic, activity
    return "", ""


def adapt_qq_message(
    *,
    message_id: str,
    actor_id: str,
    text: str,
    display_name: str = "",
    is_group: bool = False,
    group_id: str = "",
    mentioned: bool = False,
    reply_to_bot: bool = False,
    is_core_actor: bool = False,
    social_space_id: str = "",
    timestamp: float | None = None,
) -> ExternalWorldEvent:
    """QQ primitives → ExternalWorldEvent (§5). No OneBot object required."""
    semantic, activity = classify_semantics(text)
    urgency = (
        ExternalUrgency.high
        if (is_core_actor or mentioned or reply_to_bot)
        else ExternalUrgency.normal
    )
    relationship = "core_friend" if is_core_actor else "known"
    return ExternalWorldEvent(
        event_id=f"qq_{message_id or uuid.uuid4().hex[:12]}",
        timestamp=float(timestamp if timestamp is not None else time.time()),
        source=ExternalSource.qq,
        actor_id=str(actor_id),
        event_type="group_mention" if (mentioned and is_group) else "message",
        content=text,
        urgency=urgency,
        semantic_kind=semantic,
        target_activity=activity,
        actor_relationship=relationship,
        metadata={
            "display_name": display_name,
            "is_group": is_group,
            "group_id": str(group_id) if group_id else "",
            "mentioned": mentioned,
            "reply_to_bot": reply_to_bot,
            "social_space_id": social_space_id,
            "familiar": reply_to_bot or is_core_actor or mentioned,
        },
    )


def adapt_delivery(
    *, event_id: str = "", summary: str = "", urgency: ExternalUrgency = ExternalUrgency.normal
) -> ExternalWorldEvent:
    """A parcel arriving at the door (§13 example of a non-QQ source)."""
    return ExternalWorldEvent(
        event_id=event_id or f"delivery_{uuid.uuid4().hex[:10]}",
        source=ExternalSource.delivery,
        actor_id="courier",
        event_type="delivery_arrived",
        content=summary or "快递到了",
        urgency=urgency,
        actor_relationship="known",
    )


def adapt_admin(
    *, event_id: str = "", summary: str = "", actor_id: str = "admin"
) -> ExternalWorldEvent:
    """An operator/system note — recorded, never acted on by itself."""
    return ExternalWorldEvent(
        event_id=event_id or f"admin_{uuid.uuid4().hex[:10]}",
        source=ExternalSource.system,
        actor_id=actor_id,
        event_type="admin",
        content=summary,
        urgency=ExternalUrgency.normal,
        actor_relationship="known",
    )


def from_legacy_event(event: Any) -> ExternalWorldEvent:
    """Phase 2 ``ExternalEvent`` → ExternalWorldEvent (compatibility bridge).

    Lets the pre-Phase-3 callers keep working while every effect still flows
    through the new influence pipeline. ``data`` carries the adapter-level
    primitives; raw protocol details are copied to ``metadata`` only.
    """
    data: dict[str, Any] = dict(getattr(event, "data", {}) or {})
    kind = str(getattr(event, "kind", "message"))
    text = str(data.get("text", "") or getattr(event, "summary", ""))
    semantic, activity = classify_semantics(text)
    priority = getattr(event, "priority", None)
    urgency = {
        "critical": ExternalUrgency.critical,
        "high": ExternalUrgency.high,
        "low": ExternalUrgency.low,
    }.get(getattr(priority, "value", "normal"), ExternalUrgency.normal)
    if kind == "delivery_arrived":
        return adapt_delivery(
            event_id=str(getattr(event, "id", "") or ""),
            summary=str(getattr(event, "summary", "") or ""),
            urgency=urgency,
        )
    if kind == "admin":
        return adapt_admin(
            event_id=str(getattr(event, "id", "") or ""),
            summary=str(getattr(event, "summary", "") or ""),
        )
    return ExternalWorldEvent(
        event_id=str(getattr(event, "id", "") or uuid.uuid4().hex[:12]),
        timestamp=float(getattr(event, "created_at", 0) or time.time()),
        source=ExternalSource.qq,
        actor_id=str(getattr(event, "user_id", "") or ""),
        event_type=kind,
        content=text,
        urgency=urgency,
        semantic_kind=semantic,
        target_activity=activity,
        actor_relationship="core_friend" if data.get("is_core_friend") else "known",
        metadata={
            **data,
            "group_id": str(getattr(event, "group_id", "") or ""),
            "social_space_id": str(getattr(event, "social_space_id", "") or ""),
            "legacy_summary": str(getattr(event, "summary", "") or ""),
        },
    )


def legacy_meaning(event: ExternalWorldEvent) -> str:
    """Old API's ``meaning`` string for a given semantic kind."""
    return _LEGACY_MEANINGS.get(event.semantic_kind, "message")
