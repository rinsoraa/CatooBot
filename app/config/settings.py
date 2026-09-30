"""CatooBot configuration system.

Precedence (highest wins):
    1. Environment variables (``CATOOBOT_*``, loaded from ``.env`` if present)
    2. ``config/config.yaml``
    3. Built-in defaults (defined by the pydantic models below)

Secrets such as the OneBot access token should live in ``.env`` (which is
git-ignored), never in YAML committed to the repository.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("CatooBot.Config")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Environment variable -> (section, field) overrides for sensitive/deploy values.
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "CATOOBOT_ONEBOT_ACCESS_TOKEN": ("onebot", "access_token"),
    "CATOOBOT_ONEBOT_HOST": ("onebot", "host"),
    "CATOOBOT_ONEBOT_PORT": ("onebot", "port"),
    "CATOOBOT_ONEBOT_PATH": ("onebot", "path"),
    "CATOOBOT_BOT_NAME": ("bot", "name"),
    "CATOOBOT_BOT_DEBUG": ("bot", "debug"),
    "CATOOBOT_LOG_LEVEL": ("logging", "level"),
    "CATOOBOT_LOG_COLOR": ("logging", "color"),
    "CATOOBOT_LOG_NARRATE": ("logging", "narrate"),
    "CATOOBOT_DATABASE_URL": ("database", "url"),
}

_TRUTHY = {"1", "true", "yes", "on"}


class BotConfig(BaseModel):
    name: str = "CatooBot"
    debug: bool = True
    command_prefix: str = "/"


class OneBotConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    path: str = "/onebot/v11/ws"
    access_token: str = ""
    api_timeout: float = 10.0

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}{self.path}"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_dir: str = "logs"
    # Terminal presentation (v0.8 polish). ``color`` means "colour when the
    # terminal supports it" — piping to a file stays plain, NO_COLOR always wins.
    color: bool = True
    # Narration: print the character's inner life (state / thinking / flow /
    # what she says / what her world is doing) to the console. File keeps it too.
    narrate: bool = True
    # Also narrate every world tick (noisy; off by default — changes are always shown)
    narrate_world_ticks: bool = False

    @field_validator("level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        """Reject typos at the source (WebUI form included)."""
        normalized = (value or "INFO").strip().upper()
        if normalized not in ("DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"):
            raise ValueError(
                f"log level must be DEBUG/INFO/WARNING/ERROR/CRITICAL, got {value!r}"
            )
        return normalized


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///data/catoobot.db"

    @property
    def sqlite_path(self) -> Path:
        """Convert ``sqlite:///relative/path`` into a filesystem path."""
        raw = self.url
        if raw.startswith("sqlite:///"):
            raw = raw[len("sqlite:///") :]
        elif raw.startswith("sqlite://"):
            raw = raw[len("sqlite://") :]
        return PROJECT_ROOT / raw


class PermissionsConfig(BaseModel):
    """QQ ids accept int or str in YAML/ENV and are normalized to strings."""

    superusers: list[str] = Field(default_factory=list)
    admins: list[str] = Field(default_factory=list)

    @field_validator("superusers", "admins", mode="before")
    @classmethod
    def _normalize_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, int)):
            value = [value]
        return [str(item).strip() for item in value]


class AIContextConfig(BaseModel):
    """Short-term conversation context (recent turns only, no long-term memory)."""

    enabled: bool = True
    max_messages: int = Field(default=20, ge=1)


class AICooldownConfig(BaseModel):
    rate_limit_seconds: float = Field(default=30.0, ge=0)
    server_error_seconds: float = Field(default=10.0, ge=0)


class AIProviderConfig(BaseModel):
    """One AI platform endpoint. The API key itself never lives in YAML —
    ``api_key_env`` names the environment variable holding it."""

    type: str = "openai_compatible"
    base_url: str
    api_key_env: str = ""


class AIModelConfig(BaseModel):
    """One callable model: a (provider, model-id) pair with a friendly name."""

    name: str
    provider: str
    model: str
    enabled: bool = True


class AIConfig(BaseModel):
    enabled: bool = False
    system_prompt: str = "你是 CatooBot，一个运行在 QQ 上的 AI 助手。使用自然、友好的中文回答。"
    default_temperature: float = Field(default=0.8, ge=0, le=2)
    timeout: float = Field(default=60.0, gt=0)
    context: AIContextConfig = Field(default_factory=AIContextConfig)
    cooldown: AICooldownConfig = Field(default_factory=AICooldownConfig)
    providers: dict[str, AIProviderConfig] = Field(default_factory=dict)
    models: list[AIModelConfig] = Field(default_factory=list)


class CharacterConfig(BaseModel):
    """Character identity/persona. Empty by default — defined via WebUI."""

    timezone: str = "Asia/Singapore"
    identity: dict[str, Any] = Field(default_factory=dict)
    personality: dict[str, Any] = Field(default_factory=dict)
    speaking_style: dict[str, Any] = Field(default_factory=dict)
    behavior_rules: list[str] = Field(default_factory=list)
    system_prompt: str = ""


class MemoryWeightsConfig(BaseModel):
    """Hybrid ranking weights (spec §21) — always configurable, never hardcoded."""

    semantic: float = Field(default=0.40, ge=0.0)
    keyword: float = Field(default=0.20, ge=0.0)
    importance: float = Field(default=0.15, ge=0.0)
    confidence: float = Field(default=0.10, ge=0.0)
    recency: float = Field(default=0.10, ge=0.0)
    relationship: float = Field(default=0.05, ge=0.0)


class MemoryRetrievalConfig(BaseModel):
    top_k: int = Field(default=8, ge=1, le=50)
    weights: MemoryWeightsConfig = Field(default_factory=MemoryWeightsConfig)
    # Relevance guard (spec §98): nothing below this final score is injected...
    min_final_score: float = Field(default=0.18, ge=0.0, le=1.0)
    # ...and a memory must also show *some* relevance evidence (semantic or
    # keyword), so importance/recency alone never pulls an unrelated fact in
    # (§22). Kept deliberately low: Chinese function words dilute lexical
    # overlap, and min_final_score already does the strict filtering.
    min_relevance: float = Field(default=0.05, ge=0.0, le=1.0)
    keyword_candidates: int = Field(default=20, ge=1, le=200)
    semantic_candidates: int = Field(default=20, ge=1, le=200)
    topic_bonus: float = Field(default=0.12, ge=0.0, le=1.0)
    cache_ttl_seconds: float = Field(default=60.0, ge=0.0)


class MemoryEmbeddingConfig(BaseModel):
    """Embedding endpoint. Empty model/provider means semantic search is off."""

    provider: str = ""      # ai.providers key to reuse base_url + credential
    model: str = ""
    dimensions: int | None = None
    timeout: float = Field(default=10.0, gt=0)
    base_url: str = ""      # optional standalone endpoint
    api_key_env: str = ""   # optional standalone credential


class MemorySemanticConfig(BaseModel):
    enabled: bool = False   # opt-in: requires a configured embedding model
    embedding: MemoryEmbeddingConfig = Field(default_factory=MemoryEmbeddingConfig)
    batch_size: int = Field(default=32, ge=1, le=256)


class MemoryConsolidationConfig(BaseModel):
    enabled: bool = True
    duplicate_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    conflict_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    schedule: str = "daily"          # daily | hourly | manual
    compression_min_cluster: int = Field(default=5, ge=2)
    compression_use_llm: bool = False
    max_scan: int = Field(default=500, ge=10)


class MemoryRetentionConfig(BaseModel):
    episodic_days: int = Field(default=180, ge=1)


class MemoryPolicyConfig(BaseModel):
    enabled: bool = True
    max_active_per_user: int = Field(default=500, ge=1)
    max_active_per_group: int = Field(default=300, ge=1)


class MemoryExtractionConfig(BaseModel):
    enabled: bool = True
    model: str = ""  # model *name* from ai.models used for extraction; empty = primary
    timeout: float = Field(default=30.0, gt=0)
    min_content_length: int = Field(default=4, ge=1)


class MemoryConfig(BaseModel):
    enabled: bool = True
    retrieval: MemoryRetrievalConfig = Field(default_factory=MemoryRetrievalConfig)
    extraction: MemoryExtractionConfig = Field(default_factory=MemoryExtractionConfig)
    semantic: MemorySemanticConfig = Field(default_factory=MemorySemanticConfig)
    consolidation: MemoryConsolidationConfig = Field(default_factory=MemoryConsolidationConfig)
    retention: MemoryRetentionConfig = Field(default_factory=MemoryRetentionConfig)
    policy: MemoryPolicyConfig = Field(default_factory=MemoryPolicyConfig)


class WebConfig(BaseModel):
    """Admin WebUI. The password is stored only as a PBKDF2 hash (in DB);
    bootstrap credentials come from env/config on first run."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8500
    username: str = "admin"
    password: str = ""  # initial password only; bootstrap via .env recommended
    password_env: str = "CATOOBOT_WEB_PASSWORD"


class BehaviorReplyTimingConfig(BaseModel):
    """Reply delay model (spec §7): probabilistic band, never a fixed sleep."""

    enabled: bool = True
    min_delay: float = Field(default=0.8, ge=0.0)
    max_delay: float = Field(default=8.0, ge=0.0)
    per_char_delay: float = Field(default=0.045, ge=0.0)
    reading_floor: float = Field(default=0.5, ge=0.0)
    fast_exchange_window: float = Field(default=90.0, ge=0.0)
    fast_exchange_factor: float = Field(default=0.65, gt=0.0)
    busy_factor: float = Field(default=1.6, gt=0.0)
    sleeping_factor: float = Field(default=2.2, gt=0.0)
    night_factor: float = Field(default=1.15, gt=0.0)
    close_relationship_factor: float = Field(default=0.85, gt=0.0)
    jitter: float = Field(default=0.2, ge=0.0, le=0.9)


class BehaviorChunkingConfig(BaseModel):
    """Natural message splitting (spec §9/§10) — not every reply is split."""

    enabled: bool = True
    chunk_probability: float = Field(default=0.20, ge=0.0, le=1.0)
    max_chunks: int = Field(default=3, ge=1, le=6)
    min_chunk_length: int = Field(default=6, ge=1)
    paragraph_always_split: bool = True
    inter_chunk_delay_min: float = Field(default=0.6, ge=0.0)
    inter_chunk_delay_max: float = Field(default=2.0, ge=0.0)


class BehaviorScheduleConfig(BaseModel):
    """Sleep / DND / night windows in the character's timezone (spec §20/§43)."""

    sleep_enabled: bool = True
    sleep_start: str = "00:30"
    sleep_end: str = "08:00"
    dnd_enabled: bool = False
    dnd_start: str = "23:00"
    dnd_end: str = "08:00"
    dnd_blocks_replies: bool = False  # passive replies stay on by default
    night_start: str = "23:00"
    night_end: str = "06:00"


class BehaviorActivityConfig(BaseModel):
    """Fictional character activity (spec §16/§17). Never real-world claims."""

    enabled: bool = True
    roll_interval_minutes: int = Field(default=45, ge=1)
    idle_activity: str = "idle"
    # Character-specific pool; empty means "use the period defaults below".
    pool: list[str] = Field(default_factory=list)
    # Optional per-period override, e.g. {night: [resting, gaming]}
    period_preferences: dict[str, list[str]] = Field(default_factory=dict)


class BehaviorGroupConfig(BaseModel):
    """Group participation (spec §34-§37). Off by default: @ only."""

    participation_enabled: bool = False
    participation_probability: float = Field(default=0.04, ge=0.0, le=1.0)
    cooldown_seconds: int = Field(default=300, ge=0)
    hourly_limit: int = Field(default=3, ge=0)
    min_message_length: int = Field(default=3, ge=1)
    topic_bonus: float = Field(default=0.15, ge=0.0)
    ignore_when_other_mentioned: bool = True
    mention_always_replies: bool = True


class BehaviorInitiativeConfig(BaseModel):
    """Proactive chat (spec §21-§33). Disabled by default — opt in via WebUI."""

    enabled: bool = False
    min_interval_minutes: int = Field(default=120, ge=1)
    daily_limit: int = Field(default=3, ge=0)
    hourly_limit: int = Field(default=1, ge=0)
    min_relationship_stage: str = "familiar"
    idle_hours: float = Field(default=6.0, ge=0.0)
    base_probability: float = Field(default=0.35, ge=0.0, le=1.0)
    relationship_bonus: float = Field(default=0.2, ge=0.0, le=1.0)
    topic_bonus: float = Field(default=0.35, ge=0.0, le=1.0)
    active_activity_factor: float = Field(default=1.0, ge=0.0)
    max_unanswered: int = Field(default=1, ge=0)
    duplicate_similarity: float = Field(default=0.6, ge=0.0, le=1.0)


class BehaviorConfig(BaseModel):
    """Character behaviour engine settings (v0.4)."""

    enabled: bool = True
    reply: BehaviorReplyTimingConfig = Field(default_factory=BehaviorReplyTimingConfig)
    chunking: BehaviorChunkingConfig = Field(default_factory=BehaviorChunkingConfig)
    schedule: BehaviorScheduleConfig = Field(default_factory=BehaviorScheduleConfig)
    activity: BehaviorActivityConfig = Field(default_factory=BehaviorActivityConfig)
    group: BehaviorGroupConfig = Field(default_factory=BehaviorGroupConfig)
    initiative: BehaviorInitiativeConfig = Field(default_factory=BehaviorInitiativeConfig)


class SocialContinuationConfig(BaseModel):
    """Active-conversation follow-up window (spec §13/§48)."""

    enabled: bool = True
    window_minutes: int = Field(default=10, ge=1)
    max_messages: int = Field(default=8, ge=1)


class SocialObserverConfig(BaseModel):
    """5-message observation trigger (spec §24/§25)."""

    batch_size: int = Field(default=5, ge=1)
    min_context_messages: int = Field(default=20, ge=1)
    max_staleness_messages: int = Field(default=5, ge=1)


class SocialParticipationConfig(BaseModel):
    """Hard frequency limits on autonomous group speech (spec §91)."""

    daily_limit: int = Field(default=30, ge=0)
    cooldown_seconds: int = Field(default=90, ge=0)


class SocialGroupContextConfig(BaseModel):
    """Per-group short-term message buffer (spec §10)."""

    max_messages: int = Field(default=30, ge=1)


class SocialThresholdsConfig(BaseModel):
    """Structured-decision thresholds (spec §96). Scores are rules, not dice."""

    follow_up: float = Field(default=0.75, ge=0.0, le=1.0)
    topic_relevance: float = Field(default=0.70, ge=0.0, le=1.0)
    social_fit: float = Field(default=0.65, ge=0.0, le=1.0)
    contribution_value: float = Field(default=0.65, ge=0.0, le=1.0)


class SocialFeatureConfig(BaseModel):
    enabled: bool = True


class SocialConfig(BaseModel):
    """Social Cognition Engine (v0.9): understanding *when* to speak in a group.

    Replaces "participation = random probability" with structured social
    judgment. The legacy ``behavior.group.participation_probability`` stays as
    a low-weight tie-breaker only (spec §92), never the decision itself.
    """

    enabled: bool = True
    decision_model: str = ""       # empty -> router's default (fast) model
    group_context: SocialGroupContextConfig = Field(default_factory=SocialGroupContextConfig)
    continuation: SocialContinuationConfig = Field(default_factory=SocialContinuationConfig)
    observer: SocialObserverConfig = Field(default_factory=SocialObserverConfig)
    participation: SocialParticipationConfig = Field(default_factory=SocialParticipationConfig)
    thresholds: SocialThresholdsConfig = Field(default_factory=SocialThresholdsConfig)
    attention: SocialFeatureConfig = Field(default_factory=SocialFeatureConfig)
    fatigue: SocialFeatureConfig = Field(default_factory=SocialFeatureConfig)
    topic: SocialFeatureConfig = Field(default_factory=SocialFeatureConfig)
    observation_retention_days: int = Field(default=30, ge=1)


class ToolRateLimitConfig(BaseModel):
    """Per-scope call limits for a tool (spec §33/§78)."""

    per_user_per_minute: int = Field(default=10, ge=0)
    per_group_per_minute: int = Field(default=20, ge=0)
    global_per_minute: int = Field(default=60, ge=0)


class ToolPermissionsConfig(BaseModel):
    """Which risk levels may run at all (v0.6: low-risk only, spec §22/§106)."""

    allowed_risk_levels: list[str] = Field(default_factory=lambda: ["low"])
    default_enabled: bool = True


class ToolOverrideConfig(BaseModel):
    """Per-tool admin overrides (WebUI edits these; they hot-reload)."""

    enabled: bool | None = None
    timeout: float | None = None
    cache_ttl_seconds: float | None = None
    rate_limit: ToolRateLimitConfig | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class ToolsConfig(BaseModel):
    """Tool runtime settings (spec §19/§34/§35/§52/§71)."""

    enabled: bool = False           # opt-in: no tool calls until configured
    # "json" = model answers with a structured decision (works everywhere);
    # "native" = provider function calling (used when the model supports it).
    decision_mode: str = "json"
    candidate_tools: int = Field(default=5, ge=1, le=20)
    default_timeout: float = Field(default=10.0, gt=0)
    max_calls_per_turn: int = Field(default=3, ge=0, le=10)
    max_execution_time: float = Field(default=30.0, gt=0)
    max_concurrent: int = Field(default=4, ge=1, le=16)
    max_retries: int = Field(default=1, ge=0, le=3)
    cache_enabled: bool = True
    rate_limit: ToolRateLimitConfig = Field(default_factory=ToolRateLimitConfig)
    permissions: ToolPermissionsConfig = Field(default_factory=ToolPermissionsConfig)
    configs: dict[str, ToolOverrideConfig] = Field(default_factory=dict)


class AgentBudgetConfig(BaseModel):
    """Hard caps for one agent task (spec §29 — never unbounded)."""

    max_steps: int = Field(default=8, ge=1, le=30)
    max_tool_calls: int = Field(default=6, ge=0, le=30)
    max_replans: int = Field(default=2, ge=0, le=5)
    max_execution_seconds: float = Field(default=60.0, gt=0)
    max_parallel_tools: int = Field(default=3, ge=1, le=8)


class AgentModeConfig(BaseModel):
    """Which task classes the agent may handle (spec §58)."""

    simple: bool = True
    tool_assisted: bool = True
    multi_step: bool = True
    long_running: bool = False   # framework only in v0.7 (spec §15/§50)


class AgentModelConfig(BaseModel):
    """Optional model *name* override; empty means "use the model router"."""

    model: str = ""
    timeout: float = Field(default=30.0, gt=0)


class AgentEvaluatorConfig(AgentModelConfig):
    timeout: float = Field(default=20.0, gt=0)
    use_llm: bool = False        # rule-based completion check by default


class AgentConfig(BaseModel):
    """Agent Runtime settings (v0.7)."""

    enabled: bool = True
    autonomy: str = "normal"     # manual | assisted | normal (spec §59)
    mode: AgentModeConfig = Field(default_factory=AgentModeConfig)
    budget: AgentBudgetConfig = Field(default_factory=AgentBudgetConfig)
    planner: AgentModelConfig = Field(default_factory=AgentModelConfig)
    evaluator: AgentEvaluatorConfig = Field(default_factory=AgentEvaluatorConfig)
    background: dict[str, Any] = Field(default_factory=lambda: {"enabled": False})
    # how many observations are fed back into the model at once (spec §143)
    max_observations_in_context: int = Field(default=6, ge=1, le=20)
    # cancel / pause / resume phrases recognized in normal chat (spec §36/§37)
    cancel_phrases: list[str] = Field(
        default_factory=lambda: [
            "算了", "不用查了", "别查了", "不用继续了", "不用找了", "不查了", "取消",
        ]
    )
    pause_phrases: list[str] = Field(
        default_factory=lambda: ["先停一下", "停一下", "暂停", "先别查了", "等会儿再"]
    )
    resume_phrases: list[str] = Field(
        default_factory=lambda: ["继续吧", "你继续", "接着查", "继续看看", "接着弄"]
    )
    multi_step_markers: list[str] = Field(
        default_factory=lambda: [
            "然后", "再帮", "并且", "顺便", "比较", "对比", "哪个更", "哪个适合",
            "分别", "同时", "一次性", "都查", "和周日", "和明天", "之后再", "接着",
        ]
    )


class WorldRoutineConfig(BaseModel):
    """The character's fictional daily rhythm (spec §17 — never hardcoded)."""

    enabled: bool = True
    # period -> candidate activities; empty means "use the built-in period map"
    periods: dict[str, list[str]] = Field(default_factory=dict)
    transition_minutes: int = Field(default=20, ge=0)  # smoothing window


class WorldGoalsConfig(BaseModel):
    enabled: bool = True
    max_active: int = Field(default=5, ge=1, le=20)
    max_progress_events_per_day: int = Field(default=3, ge=0)
    auto_advance: bool = False        # background advancement is opt-in (spec §77)
    advance_interval_hours: float = Field(default=12.0, gt=0)


class WorldAmbientConfig(BaseModel):
    enabled: bool = True
    min_interval_minutes: int = Field(default=60, ge=5)
    max_per_day: int = Field(default=4, ge=0)


class WorldEventsConfig(BaseModel):
    max_per_hour: int = Field(default=10, ge=1)
    max_per_day: int = Field(default=40, ge=1)
    # Per-type protection (spec §49): "今天这个剧情已经出现三次了，别再刷"
    type_max_per_day: dict[str, int] = Field(default_factory=dict)
    type_cooldown_minutes: dict[str, int] = Field(default_factory=dict)


class WorldSnapshotConfig(BaseModel):
    interval_minutes: int = Field(default=30, ge=1)
    keep: int = Field(default=30, ge=1, le=200)


class WorldMessagingConfig(BaseModel):
    """Background *messaging* limits — separate from background *life* (spec §42/§105)."""

    enabled: bool = True
    max_background_messages_per_day: int = Field(default=3, ge=0)
    pending_ttl_minutes: int = Field(default=120, ge=1)


class WorldActivityConfig(BaseModel):
    """Activity Episode model (v1.0 §16/§32/§73).

    Activity is now a *lifecycle*, not a per-tick label. The planner only runs
    inside the transition window, never every tick.
    """

    transition_window_minutes: int = Field(default=5, ge=0)
    planning_horizon_minutes: int = Field(default=240, ge=10)
    max_transitions_per_hour: int = Field(default=6, ge=1)
    #: per-activity profile overrides (min/typical/max/momentum/…), WebUI-managed
    profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)


class WorldConfig(BaseModel):
    """Persistent world / background life runtime (v0.8)."""

    enabled: bool = True
    timezone: str = ""                # empty -> character.timezone
    tick_seconds: int = Field(default=60, ge=5)
    state_persist_seconds: int = Field(default=60, ge=5)
    routine: WorldRoutineConfig = Field(default_factory=WorldRoutineConfig)
    goals: WorldGoalsConfig = Field(default_factory=WorldGoalsConfig)
    ambient: WorldAmbientConfig = Field(default_factory=WorldAmbientConfig)
    events: WorldEventsConfig = Field(default_factory=WorldEventsConfig)
    snapshot: WorldSnapshotConfig = Field(default_factory=WorldSnapshotConfig)
    messaging: WorldMessagingConfig = Field(default_factory=WorldMessagingConfig)
    activity: WorldActivityConfig = Field(default_factory=WorldActivityConfig)
    missed_event_policy: str = "skip"  # skip | catch_up (spec §33/§34)
    rest_mode: bool = False           # character "on vacation": fewer events (spec §68)

    @field_validator("missed_event_policy")
    @classmethod
    def _validate_missed_policy(cls, value: str) -> str:
        policy = (value or "skip").strip().lower()
        if policy not in ("skip", "catch_up"):
            raise ValueError("missed_event_policy must be 'skip' or 'catch_up'")
        return policy


class MediaConfig(BaseModel):
    """Multimodal + sticker runtime (v1.1 §5-§58)."""

    enabled: bool = True
    vision_model: str = ""             # ai.models 里的视觉模型别名；留空走路由默认
    sticker_dir: str = "data/stickers" # 手动导入目录（library/imported/archived 下）
    media_dir: str = "data/media"
    # Expression decision / cooldown
    expression_enabled: bool = True
    sticker_cooldown_seconds: int = Field(default=60, ge=0)
    max_stickers_per_turn: int = Field(default=1, ge=0, le=3)
    # Acquisition (background, never blocks chat)
    auto_collect: bool = True
    max_library_size: int = Field(default=5000, ge=0)
    acquisition_model: str = ""        # 收藏判断用的模型；留空走视觉模型/默认
    # Startup indexer
    indexer_enabled: bool = True
    analysis_version: str = "v1"
    # Strict boundary: plain images are never stickers (spec §2.2/§6 场景6)
    import_as_sticker: bool = True     # files dropped into sticker_dir ARE stickers


class AppConfig(BaseModel):
    bot: BotConfig = Field(default_factory=BotConfig)
    onebot: OneBotConfig = Field(default_factory=OneBotConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    character: CharacterConfig = Field(default_factory=CharacterConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)
    social: SocialConfig = Field(default_factory=SocialConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    world: WorldConfig = Field(default_factory=WorldConfig)


def _coerce(value: str) -> Any:
    """Best-effort convert env-var strings into YAML-compatible values."""
    low = value.strip().lower()
    if low in _TRUTHY:
        return True
    if low in {"0", "false", "no", "off"}:
        return False
    try:
        return int(value)
    except ValueError:
        return value


def _apply_env_overrides(data: dict[str, Any], environ: dict[str, str]) -> dict[str, Any]:
    for env_name, (section, field) in _ENV_OVERRIDES.items():
        raw = environ.get(env_name)
        if raw is None or raw == "":
            continue
        data.setdefault(section, {})
        if isinstance(data[section], dict):
            data[section][field] = _coerce(raw)
    return data


#: WebUI-managed overrides. Kept OUT of config.yaml on purpose: the operator's
#: commented file is never rewritten by a machine (that is how sections got
#: deleted before), and every WebUI change stays diff-able in one place.
OVERRIDES_PATH = PROJECT_ROOT / "config" / "overrides.yaml"


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``extra`` onto ``base`` (lists replace, dicts merge)."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def read_overrides(path: str | Path | None = None) -> dict[str, Any]:
    """Read the WebUI override file (empty dict when absent/invalid)."""
    target = Path(path) if path is not None else OVERRIDES_PATH
    if not target.exists():
        return {}
    try:
        with target.open("r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def write_overrides(payload: dict[str, Any], path: str | Path | None = None) -> Path:
    """Atomically write the WebUI override file."""
    target = Path(path) if path is not None else OVERRIDES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# CatooBot 覆盖配置（由 WebUI 的「配置」页面自动写入）\n"
        "# 这里的值会叠加在 config.yaml 之上；删除本文件即可恢复 config.yaml 的原样。\n"
        "# 请不要手工维护：改了 config.yaml 里同名项也不会生效，因为这里优先级更高。\n"
    )
    body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False)
    temporary = target.with_suffix(".yaml.tmp")
    temporary.write_text(header + body, encoding="utf-8")
    temporary.replace(target)
    return target


def _resolve_models(data: dict[str, Any]) -> dict[str, Any]:
    """Expand the consolidated ``models:`` section into the runtime sections.

    ``models:`` is the single, documented place to declare every model the bot
    uses (chat / extra / embedding / vision / extraction / decision / planner /
    evaluator / acquisition). This keeps the on-disk config tidy while the
    pydantic schema and runtime stay unchanged. Any value the WebUI already set
    explicitly (overrides) wins over this derivation.
    """
    models = data.get("models")
    if not isinstance(models, dict):
        return data
    providers = models.get("providers") or {}
    chat = [m for m in (models.get("chat") or []) if isinstance(m, dict)]
    extra = [m for m in (models.get("extra") or []) if isinstance(m, dict)]
    embedding = models.get("embedding") or {}
    vision = str(models.get("vision") or "").strip()
    extraction = str(models.get("extraction") or "").strip()
    decision = str(models.get("decision") or "").strip()
    planner = str(models.get("planner") or "").strip()
    evaluator = str(models.get("evaluator") or "").strip()
    acquisition = str(models.get("acquisition") or "").strip()

    def ensure(section: str) -> dict[str, Any]:
        data.setdefault(section, {})
        if not isinstance(data[section], dict):
            data[section] = {}
        return data[section]

    ai = ensure("ai")
    ai.setdefault("providers", providers)
    ai.setdefault("models", chat + extra)

    sem = ensure("memory").setdefault("semantic", {})
    emb = sem.setdefault("embedding", {})
    emb.setdefault("provider", embedding.get("provider", ""))
    emb.setdefault("model", embedding.get("model", ""))
    if embedding.get("dimensions") is not None:
        emb.setdefault("dimensions", embedding["dimensions"])
    emb.setdefault("timeout", embedding.get("timeout", 10))

    ensure("memory").setdefault("extraction", {}).setdefault("model", extraction)

    media = ensure("media")
    media.setdefault("vision_model", vision)
    media.setdefault("acquisition_model", acquisition)

    ensure("social").setdefault("decision_model", decision)
    ensure("agent").setdefault("planner", {}).setdefault("model", planner)
    ensure("agent").setdefault("evaluator", {}).setdefault("model", evaluator)

    return data


def load_config(
    config_path: str | Path | None = None,
    *,
    env_file: str | Path | None = None,
    overrides_path: str | Path | None = None,
) -> AppConfig:
    """Load the application configuration.

    Order: ``.env`` → ``config.yaml`` → WebUI overrides → ``models:`` expansion
    → ``CATOOBOT_*`` env.
    """
    load_dotenv(env_file if env_file is not None else PROJECT_ROOT / ".env")

    path = Path(config_path) if config_path is not None else PROJECT_ROOT / "config" / "config.yaml"
    if not path.exists():
        example = path.with_name("config.example.yaml")
        if example.exists():
            shutil.copyfile(example, path)
            print(f"[INFO] [Config] Created {path} from config.example.yaml")
        else:
            print(f"[INFO] [Config] {path} not found, using built-in defaults")

    data: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as fp:
            loaded = yaml.safe_load(fp)
        if isinstance(loaded, dict):
            data = loaded
        elif loaded is not None:
            print(f"[WARNING] [Config] {path} has invalid structure, using defaults")

    overrides = read_overrides(overrides_path)
    if overrides:
        data = deep_merge(data, overrides)

    import os

    data = _apply_env_overrides(data, dict(os.environ))
    data = _resolve_models(data)
    return AppConfig.model_validate(data)
