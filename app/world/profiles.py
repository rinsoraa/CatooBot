"""Activity duration profiles (v1.0 §12/§13/§24/§42/§55).

Each activity *type* (keyed by the routine catalogue ``key``) gets a duration
profile: min/typical/max, momentum (continuation inertia, not a probability),
whether it is interruptible, and how many ambient events one episode may carry.

These are defaults — the WebUI may override any of them through
``world.activity.profiles``.
"""

from __future__ import annotations

from app.world.episode import ActivityProfile

#: key -> profile. Durations are minutes; momentum is 0..1.
DEFAULT_PROFILES: dict[str, ActivityProfile] = {
    "sleeping": ActivityProfile(
        key="sleeping", label="睡觉", location="卧室", social="quiet", tags=["sleep"],
        min_minutes=360, typical_minutes=420, max_minutes=600, momentum=0.95,
        interruptible=False, ambient_eligible=False, energy_delta_per_hour=0.15,
        flexibility=0.1,
    ),
    "lazing": ActivityProfile(
        key="lazing", label="赖床", location="卧室", social="quiet", tags=["sleep", "slow"],
        min_minutes=20, typical_minutes=40, max_minutes=90, momentum=0.6,
        ambient_eligible=False, energy_delta_per_hour=0.03, flexibility=0.5,
    ),
    "breakfast": ActivityProfile(
        key="breakfast", label="吃早饭", location="厨房", social="alone", tags=["meal"],
        min_minutes=15, typical_minutes=30, max_minutes=45, momentum=0.25,
        ambient_max_per_episode=1, energy_delta_per_hour=0.04, flexibility=0.2,
    ),
    "lunch": ActivityProfile(
        key="lunch", label="吃午饭", location="厨房", social="alone", tags=["meal"],
        min_minutes=20, typical_minutes=40, max_minutes=60, momentum=0.25,
        ambient_max_per_episode=1, energy_delta_per_hour=0.04, flexibility=0.2,
    ),
    "dinner": ActivityProfile(
        key="dinner", label="吃晚饭", location="厨房", social="alone", tags=["meal"],
        min_minutes=20, typical_minutes=40, max_minutes=60, momentum=0.25,
        ambient_max_per_episode=1, energy_delta_per_hour=0.04, flexibility=0.2,
    ),
    "errand": ActivityProfile(
        key="errand", label="出门买东西", location="便利店", social="alone", tags=["outside"],
        min_minutes=15, typical_minutes=30, max_minutes=60, momentum=0.4,
        ambient_max_per_episode=1, energy_delta_per_hour=-0.05, flexibility=0.5,
    ),
    "gaming": ActivityProfile(
        key="gaming", label="打游戏", location="房间", social="alone", tags=["game", "focus"],
        min_minutes=30, typical_minutes=90, max_minutes=240, momentum=0.85,
        ambient_max_per_episode=2, energy_delta_per_hour=-0.08, flexibility=0.8,
    ),
    "building": ActivityProfile(
        key="building", label="在 Minecraft 里建房子", location="房间", social="alone",
        tags=["game", "focus", "creative"],
        min_minutes=30, typical_minutes=90, max_minutes=180, momentum=0.85,
        ambient_max_per_episode=2, energy_delta_per_hour=-0.06, flexibility=0.8,
    ),
    "watching": ActivityProfile(
        key="watching", label="看剧", location="客厅", social="alone", tags=["media", "slow"],
        min_minutes=20, typical_minutes=60, max_minutes=180, momentum=0.8,
        ambient_max_per_episode=1, energy_delta_per_hour=-0.03, flexibility=0.7,
    ),
    "movie": ActivityProfile(
        key="movie", label="看电影", location="客厅", social="alone", tags=["media", "slow"],
        min_minutes=60, typical_minutes=120, max_minutes=180, momentum=0.85,
        ambient_eligible=False, energy_delta_per_hour=-0.02, flexibility=0.4,
    ),
    "music": ActivityProfile(
        key="music", label="听歌", location="房间", social="alone", tags=["music", "slow"],
        min_minutes=10, typical_minutes=30, max_minutes=120, momentum=0.5,
        ambient_max_per_episode=1, energy_delta_per_hour=0.02, flexibility=0.8,
    ),
    "scrolling": ActivityProfile(
        key="scrolling", label="刷手机", location="沙发", social="quiet", tags=["phone", "slow"],
        min_minutes=5, typical_minutes=20, max_minutes=60, momentum=0.3,
        ambient_max_per_episode=1, energy_delta_per_hour=-0.02, flexibility=0.7,
    ),
    "voice_chat": ActivityProfile(
        key="voice_chat", label="和朋友连麦", location="房间", social="with_friends",
        tags=["social", "game"],
        min_minutes=30, typical_minutes=60, max_minutes=120, momentum=0.7,
        ambient_max_per_episode=1, energy_delta_per_hour=-0.03, flexibility=0.6,
    ),
    "group_chat": ActivityProfile(
        key="group_chat", label="在群里聊天", location="房间", social="chatting",
        tags=["social", "phone"],
        min_minutes=10, typical_minutes=30, max_minutes=90, momentum=0.5,
        ambient_max_per_episode=1, energy_delta_per_hour=-0.02, flexibility=0.7,
    ),
    "idle": ActivityProfile(
        key="idle", label="发呆", location="房间", social="quiet", tags=["slow", "ambient"],
        min_minutes=5, typical_minutes=15, max_minutes=60, momentum=0.25,
        ambient_max_per_episode=1, energy_delta_per_hour=0.01, flexibility=0.9,
    ),
}

_DEFAULT_CUSTOM = ActivityProfile(
    key="custom", label="待着", location="", social="alone", tags=["custom"],
    min_minutes=15, typical_minutes=30, max_minutes=90, momentum=0.5,
    ambient_max_per_episode=1, flexibility=0.6,
)


def profile_for(key: str, *, overrides: dict[str, dict] | None = None) -> ActivityProfile:
    """Resolve the profile for an activity key (built-in + WebUI overrides)."""
    profile = DEFAULT_PROFILES.get(key)
    if profile is None:
        profile = _DEFAULT_CUSTOM.model_copy(update={"key": key or "custom"})
    if overrides:
        extra = overrides.get(key)
        if extra:
            profile = profile.model_copy(update=extra)
    return profile
