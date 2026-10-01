"""Character Continuity (v1.2): the short-timescale "same person" state.

Continuity is NOT inner monologue and NOT chain-of-thought (v1.2 §24/§113). It is
structured, expiring state: what she has been into lately, what is unfinished,
what she and this user have been through together, how this user chats.
"""

from app.continuity.models import (
    AffectDimension,
    AffectiveContext,
    CharacterContinuityState,
    InteractionPattern,
    InteractionProfile,
    MicroEvent,
    OpenLoop,
    OpenLoopStatus,
    OpenLoopType,
    SharedExperience,
    SharedExperienceType,
)
from app.continuity.store import ContinuityStore

__all__ = [
    "AffectDimension",
    "AffectiveContext",
    "CharacterContinuityState",
    "ContinuityStore",
    "InteractionPattern",
    "InteractionProfile",
    "MicroEvent",
    "OpenLoop",
    "OpenLoopStatus",
    "OpenLoopType",
    "SharedExperience",
    "SharedExperienceType",
]
