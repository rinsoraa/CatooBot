"""CatooBot configuration system.

Precedence (highest wins):
    1. Environment variables (``CATOOBOT_*``, loaded from ``.env`` if present)
    2. ``config/config.yaml``
    3. Built-in defaults (defined by the pydantic models below)

Secrets such as the OneBot access token should live in ``.env`` (which is
git-ignored), never in YAML committed to the repository.

本文件按配置段引用多个版本的 §n（v0.4 缺失 / v0.5 / v0.6 / v0.7 / v0.8 / v0.9 /
v1.1 / v1.2 / v2.0）。"""

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


def project_path(value: str | Path) -> Path:
    """Resolve a config path against PROJECT_ROOT instead of the current dir.

    ``data/stickers`` in the config means "inside the project", so a bot
    started from another working directory (a service manager, an IDE task, a
    shell opened elsewhere) must see the same sticker library, media cache,
    log file and plugin folder as one started from the repo root.
    """
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


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
    #: Show the model's thinking excerpt in the terminal (console only — never
    #: written to the log file, and never saved to the database).
    narrate_thinking: bool = True
    # Event-loop watchdog: single process, single loop — one blocking call in
    # the WebUI or a tool freezes QQ chat too, so stalls are measured and logged.
    watchdog_enabled: bool = True
    watchdog_interval_seconds: float = Field(default=1.0, gt=0)
    watchdog_threshold_ms: int = Field(default=500, ge=50)
    # Config file watching (Task 23): hand-edits to config.yaml are re-loaded
    # and hot-applied instead of waiting for a restart. Polling, no dependency;
    # a broken edit is reported and the previous config stays live.
    watch_config_enabled: bool = True
    watch_config_interval_seconds: float = Field(default=2.0, gt=0)

    @field_validator("level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        """Reject typos at the source (WebUI form included)."""
        normalized = (value or "INFO").strip().upper()
        if normalized not in ("DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"):
            raise ValueError(f"log level must be DEBUG/INFO/WARNING/ERROR/CRITICAL, got {value!r}")
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
    #: a transient failure retries with exponential backoff + jitter (seconds)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)
    retry_backoff_max_seconds: float = Field(default=4.0, ge=0)


class AIConcurrencyConfig(BaseModel):
    """One in-flight limit per provider endpoint, shared by every model on it."""

    max_parallel_per_provider: int = Field(default=4, ge=1)


class AIUsageConfig(BaseModel):
    """Per-call usage rows (tokens, latency, outcome) and their retention."""

    enabled: bool = True
    retention_days: int = Field(default=30, ge=1)


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
    #: Output budget for calls that do not pin one themselves (0 = let the
    #: provider decide). Reasoning models can spend their whole completion on
    #: reasoning_content and return empty content (finish=length); a bounded
    #: budget plus the router's empty-response failover caps that damage.
    max_tokens: int = Field(default=0, ge=0)
    context: AIContextConfig = Field(default_factory=AIContextConfig)
    cooldown: AICooldownConfig = Field(default_factory=AICooldownConfig)
    concurrency: AIConcurrencyConfig = Field(default_factory=AIConcurrencyConfig)
    usage: AIUsageConfig = Field(default_factory=AIUsageConfig)
    providers: dict[str, AIProviderConfig] = Field(default_factory=dict)
    models: list[AIModelConfig] = Field(default_factory=list)


class CharacterConfig(BaseModel):
    """Character identity/persona. Empty by default — defined via WebUI."""

    timezone: str = "Asia/Shanghai"
    identity: dict[str, Any] = Field(default_factory=dict)
    personality: dict[str, Any] = Field(default_factory=dict)
    speaking_style: dict[str, Any] = Field(default_factory=dict)
    behavior_rules: list[str] = Field(default_factory=list)
    system_prompt: str = ""


class MemoryWeightsConfig(BaseModel):
    """Hybrid ranking weights (spec v0.5 §21) — always configurable, never hardcoded."""

    semantic: float = Field(default=0.40, ge=0.0)
    keyword: float = Field(default=0.20, ge=0.0)
    importance: float = Field(default=0.15, ge=0.0)
    confidence: float = Field(default=0.10, ge=0.0)
    recency: float = Field(default=0.10, ge=0.0)
    relationship: float = Field(default=0.05, ge=0.0)


class MemoryRetrievalConfig(BaseModel):
    top_k: int = Field(default=8, ge=1, le=50)
    weights: MemoryWeightsConfig = Field(default_factory=MemoryWeightsConfig)
    # Relevance guard (spec v0.5 §98): nothing below this final score is injected...
    min_final_score: float = Field(default=0.18, ge=0.0, le=1.0)
    # ...and a memory must also show *some* relevance evidence (semantic or
    # keyword), so importance/recency alone never pulls an unrelated fact in
    # (v0.5 §22). Kept deliberately low: Chinese function words dilute lexical
    # overlap, and min_final_score already does the strict filtering.
    min_relevance: float = Field(default=0.05, ge=0.0, le=1.0)
    keyword_candidates: int = Field(default=20, ge=1, le=200)
    semantic_candidates: int = Field(default=20, ge=1, le=200)
    topic_bonus: float = Field(default=0.12, ge=0.0, le=1.0)
    cache_ttl_seconds: float = Field(default=60.0, ge=0.0)


class MemoryEmbeddingConfig(BaseModel):
    """Embedding endpoint. Empty model/provider means semantic search is off."""

    provider: str = ""  # ai.providers key to reuse base_url + credential
    model: str = ""
    dimensions: int | None = None
    timeout: float = Field(default=10.0, gt=0)
    base_url: str = ""  # optional standalone endpoint
    api_key_env: str = ""  # optional standalone credential


class MemorySemanticConfig(BaseModel):
    enabled: bool = False  # opt-in: requires a configured embedding model
    embedding: MemoryEmbeddingConfig = Field(default_factory=MemoryEmbeddingConfig)
    batch_size: int = Field(default=32, ge=1, le=256)


class MemoryConsolidationConfig(BaseModel):
    enabled: bool = True
    duplicate_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    conflict_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    schedule: str = "daily"  # daily | hourly | manual
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
    #: measured extraction latency is ~5-19s depending on the model; 30s only
    #: hides a slow model. A tighter bound surfaces a lagging extraction sooner.
    timeout: float = Field(default=12.0, gt=0)
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
    """Reply delay model (v0.4 规格，原文缺失（§7）): probabilistic band, never a fixed sleep."""

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
    """Natural message splitting (v0.4 规格，原文缺失（§9/§10）) — not every reply is split."""

    enabled: bool = True
    chunk_probability: float = Field(default=0.20, ge=0.0, le=1.0)
    max_chunks: int = Field(default=3, ge=1, le=6)
    min_chunk_length: int = Field(default=6, ge=1)
    paragraph_always_split: bool = True
    inter_chunk_delay_min: float = Field(default=0.6, ge=0.0)
    inter_chunk_delay_max: float = Field(default=2.0, ge=0.0)


class BehaviorScheduleConfig(BaseModel):
    """Sleep / DND / night windows in the character's timezone (v0.4 规格，原文缺失（§20/§43）)."""

    sleep_enabled: bool = True
    sleep_start: str = "00:30"
    sleep_end: str = "08:00"
    dnd_enabled: bool = False
    dnd_start: str = "23:00"
    dnd_end: str = "08:00"
    dnd_blocks_replies: bool = False  # passive replies stay on by default
    night_start: str = "23:00"
    night_end: str = "06:00"


class BehaviorGroupConfig(BaseModel):
    """Non-@ group participation — the *live* knobs (v0.9 social path).

    ``participation_probability`` is a deterministic **rate**: each eligible
    message earns that much participation credit; one credit = one chime-in.
    1.0 therefore means "every eligible message"（仍受社交认知的冷却/每日额度约束）.
    """

    participation_enabled: bool = True
    participation_probability: float = Field(default=0.04, ge=0.0, le=1.0)
    min_message_length: int = Field(default=3, ge=1)


class BehaviorInitiativeConfig(BaseModel):
    """Proactive chat (spec v0.8 §21-§33). Disabled by default — opt in via WebUI."""

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
    group: BehaviorGroupConfig = Field(default_factory=BehaviorGroupConfig)
    initiative: BehaviorInitiativeConfig = Field(default_factory=BehaviorInitiativeConfig)


class SocialContinuationConfig(BaseModel):
    """Active-conversation follow-up window (spec v0.9 §13/§48)."""

    enabled: bool = True
    window_minutes: int = Field(default=10, ge=1)
    max_messages: int = Field(default=8, ge=1)


class SocialObserverConfig(BaseModel):
    """5-message observation trigger (spec v0.9 §24/§25)."""

    batch_size: int = Field(default=5, ge=1)
    min_context_messages: int = Field(default=20, ge=1)
    max_staleness_messages: int = Field(default=5, ge=1)


class SocialParticipationConfig(BaseModel):
    """Hard frequency limits on autonomous group speech (spec v0.9 §91)."""

    daily_limit: int = Field(default=30, ge=0)
    cooldown_seconds: int = Field(default=90, ge=0)
    #: poor_timing (defer) only delays, never vetoes. After this many *consecutive*
    #: defers in one group, the next defer is ignored and the accumulated
    #: participation credit is allowed to trigger normally.
    max_consecutive_defer: int = Field(default=3, ge=1)


class SocialGroupContextConfig(BaseModel):
    """Per-group short-term message buffer (spec v0.9 §10)."""

    max_messages: int = Field(default=30, ge=1)


class SocialThresholdsConfig(BaseModel):
    """Structured-decision thresholds (spec v0.9 §96). Scores are rules, not dice."""

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
    a low-weight tie-breaker only (spec v0.9 §92), never the decision itself.
    """

    enabled: bool = True
    decision_model: str = ""  # empty -> router's default (fast) model
    group_context: SocialGroupContextConfig = Field(default_factory=SocialGroupContextConfig)
    continuation: SocialContinuationConfig = Field(default_factory=SocialContinuationConfig)
    observer: SocialObserverConfig = Field(default_factory=SocialObserverConfig)
    participation: SocialParticipationConfig = Field(default_factory=SocialParticipationConfig)
    thresholds: SocialThresholdsConfig = Field(default_factory=SocialThresholdsConfig)
    attention: SocialFeatureConfig = Field(default_factory=SocialFeatureConfig)
    fatigue: SocialFeatureConfig = Field(default_factory=SocialFeatureConfig)
    topic: SocialFeatureConfig = Field(default_factory=SocialFeatureConfig)
    observation_retention_days: int = Field(default=30, ge=1)
    #: Task 20: settled reply-outcome rows older than this are pruned
    feedback_retention_days: int = Field(default=30, ge=1)


class ToolRateLimitConfig(BaseModel):
    """Per-scope call limits for a tool (spec v0.6 §33/§78)."""

    per_user_per_minute: int = Field(default=10, ge=0)
    per_group_per_minute: int = Field(default=20, ge=0)
    global_per_minute: int = Field(default=60, ge=0)


class ToolPermissionsConfig(BaseModel):
    """Which risk levels may run at all (v0.6: low-risk only, spec v0.6 §22/§106)."""

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
    """Tool runtime settings (spec v0.6 §19/§34/§35/§52/§71)."""

    enabled: bool = False  # opt-in: no tool calls until configured
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
    """Hard caps for one agent task (spec v0.7 §29 — never unbounded)."""

    max_steps: int = Field(default=8, ge=1, le=30)
    max_tool_calls: int = Field(default=6, ge=0, le=30)
    max_replans: int = Field(default=2, ge=0, le=5)
    max_execution_seconds: float = Field(default=60.0, gt=0)
    max_parallel_tools: int = Field(default=3, ge=1, le=8)


class AgentModeConfig(BaseModel):
    """Which task classes the agent may handle (spec v0.7 §58)."""

    simple: bool = True
    tool_assisted: bool = True
    multi_step: bool = True
    long_running: bool = False  # framework only in v0.7 (spec v0.7 §15/§50)


class AgentModelConfig(BaseModel):
    """Optional model *name* override; empty means "use the model router"."""

    model: str = ""
    timeout: float = Field(default=30.0, gt=0)


class AgentEvaluatorConfig(AgentModelConfig):
    timeout: float = Field(default=20.0, gt=0)
    use_llm: bool = False  # rule-based completion check by default


class AgentConfig(BaseModel):
    """Agent Runtime settings (v0.7)."""

    enabled: bool = True
    autonomy: str = "normal"  # manual | assisted | normal (spec v0.7 §59)
    mode: AgentModeConfig = Field(default_factory=AgentModeConfig)
    budget: AgentBudgetConfig = Field(default_factory=AgentBudgetConfig)
    planner: AgentModelConfig = Field(default_factory=AgentModelConfig)
    evaluator: AgentEvaluatorConfig = Field(default_factory=AgentEvaluatorConfig)
    background: dict[str, Any] = Field(default_factory=lambda: {"enabled": False})
    # how many observations are fed back into the model at once (spec v0.7 §143)
    max_observations_in_context: int = Field(default=6, ge=1, le=20)
    # cancel / pause / resume phrases recognized in normal chat (spec v0.7 §36/§37)
    cancel_phrases: list[str] = Field(
        default_factory=lambda: [
            "算了",
            "不用查了",
            "别查了",
            "不用继续了",
            "不用找了",
            "不查了",
            "取消",
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
            "然后",
            "再帮",
            "并且",
            "顺便",
            "比较",
            "对比",
            "哪个更",
            "哪个适合",
            "分别",
            "同时",
            "一次性",
            "都查",
            "和周日",
            "和明天",
            "之后再",
            "接着",
        ]
    )


class MediaConfig(BaseModel):
    """Multimodal + sticker runtime (v1.1 §5-§58)."""

    enabled: bool = True
    vision_model: str = ""  # ai.models 里的视觉模型别名；留空走路由默认
    sticker_dir: str = "data/stickers"  # 手动导入目录（library/imported/archived 下）
    media_dir: str = "data/media"
    # Expression decision / cooldown
    expression_enabled: bool = True
    sticker_cooldown_seconds: int = Field(default=60, ge=0)
    max_stickers_per_turn: int = Field(default=1, ge=0, le=3)
    # Acquisition (background, never blocks chat)
    auto_collect: bool = True
    max_library_size: int = Field(default=5000, ge=0)
    acquisition_model: str = ""  # 收藏判断用的模型；留空走视觉模型/默认
    # Startup indexer
    indexer_enabled: bool = True
    analysis_version: str = "v1"
    # Background media understanding for *sticker-like* images even when the
    # character does not reply (never affects whether she replies).
    background_vision_enabled: bool = True
    background_vision_max_per_hour: int = Field(default=20, ge=0)
    #: how long a turn waits for sticker recognition before deciding anyway
    recognition_timeout_seconds: float = Field(default=12.0, gt=0)
    # Strict boundary: plain images are never stickers (v1.1 §2.2/§6)
    import_as_sticker: bool = True  # files dropped into sticker_dir ARE stickers


class ConversationDebounceConfig(BaseModel):
    """Dynamic turn-closing window (v1.2 §11/§12) — never a fixed wait."""

    enabled: bool = True
    direct_message_ms: int = Field(default=1200, ge=0)
    group_message_ms: int = Field(default=1800, ge=0)


class ConversationConfig(BaseModel):
    """Conversation Turn Runtime (v1.2): bursts become one turn, one reply."""

    debounce: ConversationDebounceConfig = Field(default_factory=ConversationDebounceConfig)


class ContinuityConfig(BaseModel):
    """Character Continuity (v1.2 §23-§31): short-timescale "same person" state.

    Every field decays by its own TTL — nothing here is permanent (v1.2 §105/§106).
    """

    enabled: bool = True
    recent_emotion_ttl_minutes: int = Field(default=90, ge=5)
    current_interest_ttl_hours: int = Field(default=12, ge=1)
    unfinished_thought_ttl_hours: int = Field(default=6, ge=1)
    open_loop_ttl_days: int = Field(default=14, ge=1)
    micro_event_ttl_minutes: int = Field(default=90, ge=5)
    max_recent_events: int = Field(default=8, ge=1, le=30)
    max_open_loops: int = Field(default=12, ge=1, le=50)
    shared_experience_min_confidence: float = Field(default=0.60, ge=0.0, le=1.0)


class SandboxConfig(BaseModel):
    """Character Life Sandbox (v2.0 §16/§67/§202): the character *lives* here.

    ``enabled`` makes this the world core; the legacy WorldRuntime stays off.
    """

    enabled: bool = True
    tick_seconds: int = Field(default=600, ge=30)
    simulation_seed: int = 0
    bible_path: str = "config/character_bible.md"
    allow_ai_decisions: bool = True  # LLM only for ambiguous choices (v2.0 §194)
    reset_on_bible_change: bool = True  # re-initialize when the bible changes
    max_events_keep: int = Field(default=500, ge=50)
    snapshot_keep: int = Field(default=48, ge=1, le=500)
    #: QQ 用户号 → 核心朋友（空凛），影响打断优先级与回复速度
    #: 主动消息（后台消息 ≠ 后台生活）的每日额度
    max_background_messages_per_day: int = Field(default=3, ge=0)
    #: 启动时把人物档案同步成 WebUI 的 /character 角色设定（唯一权威来源）
    sync_persona_from_bible: bool = True
    #: QQ 用户号 → 核心朋友。两种写法：
    #:   - 旧列表 ["123456"]：仅当 Bible 只有一个核心好友时可映射（否则警告并退化为普通身份）
    #:   - 映射 {"123456": "空凛", "234567": "阿澈"}：一一对应，推荐
    core_friend_ids: list[str] | dict[str, str] = Field(default_factory=list)
    #: 显式 QQ → Bible 核心好友名映射（优先于上面两种写法；名字不匹配会被忽略并警告）
    core_friend_identities: dict[str, str] = Field(default_factory=dict)
    #: QQ 群号 → SocialSpace id（游戏群/猫图群…）；未映射的群自动成为 qq:<gid>
    social_space_map: dict[str, str] = Field(default_factory=dict)

    # ---------- Cognitive Context Bridge（Phase 5）：注入聊天上下文的预算 ----------
    #: 相关 Sandbox 记忆最多注入几条（0 = 关闭该层）
    memory_context_limit: int = Field(default=3, ge=0, le=20)
    #: 全部注入记忆的字符数上限
    memory_context_max_chars: int = Field(default=600, ge=0, le=4000)
    #: 相关性评分下限（低于此分不注入）
    memory_context_min_score: float = Field(default=0.12, ge=0.0, le=1.0)
    #: 必须有话题证据（关键词或实体命中）才允许注入——重要度+新近度
    #: 不能单独把一条无关记忆拉进上下文
    memory_context_require_evidence: bool = True
    #: 最近经历注入条数上限
    experience_context_limit: int = Field(default=3, ge=0, le=20)
    #: Phase 11 §23: “最近一起做过什么”的唯一时间窗（分钟，默认 48 小时；
    #: 0 = 只用条数上限）——社交语境不另设第二份 recency 阈值
    social_context_recent_window_minutes: float = Field(default=2880.0, ge=0)

    # ---------- 对话回复运行时（Phase 12）：只产出语言，不写世界 ----------
    #: 单条回复的字符上限（超长直接拒绝并回退，不截断）；唯一来源
    conversation_max_response_chars: int = Field(default=400, ge=20, le=4000)
    #: 回复可选钉住的模型 name（留空 = 路由默认）
    conversation_model: str = ""

    # ---------- 认知决策层（Phase 6） ----------
    #: 决策调用可选钉住的模型 name（ai.models 里已注册；留空 = 路由默认）
    decision_model: str = ""
    #: 决策 LLM 的调用超时（秒）与最低置信度（低于则走确定性回退）
    decision_timeout: float = Field(default=20.0, gt=0)
    decision_min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)


class ExpressionConfig(BaseModel):
    """Task 22: expression / 口癖 learning — patterns from group speech.

    Disabled by default (opt-in); when on, group messages are mined for short
    reusable phrases that get injected back into her prompt *for that group
    only*, inside the existing style limits. See docs/V3_EXPRESSION_LEARNING.md.
    """

    enabled: bool = False
    learn_max_per_hour: int = Field(default=60, ge=1)
    min_speakers: int = Field(default=2, ge=1)  # ≥N distinct speakers
    min_occurrences: int = Field(default=3, ge=1)  # or ≥M times
    max_patterns_per_group: int = Field(default=80, ge=1)
    inject_max_items: int = Field(default=3, ge=1)
    inject_max_chars: int = Field(default=24, ge=1)
    groups: list[str] = Field(default_factory=list)  # empty = all groups


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
    conversation: ConversationConfig = Field(default_factory=ConversationConfig)
    continuity: ContinuityConfig = Field(default_factory=ContinuityConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    expression: ExpressionConfig = Field(default_factory=ExpressionConfig)


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

    def set_if_blank(target: dict[str, Any], key: str, value: Any) -> None:
        """Like ``setdefault``, but an empty override also yields.

        The WebUI writes ``""`` for an unused slot; a blank is "not set", so the
        ``models:`` table's derivation must still win — otherwise an empty
        override silently masks it.
        """
        if not target.get(key):
            target[key] = value

    ai = ensure("ai")
    ai.setdefault("providers", providers)
    ai.setdefault("models", chat + extra)

    sem = ensure("memory").setdefault("semantic", {})
    emb = sem.setdefault("embedding", {})
    set_if_blank(emb, "provider", embedding.get("provider", ""))
    set_if_blank(emb, "model", embedding.get("model", ""))
    if embedding.get("dimensions") is not None:
        emb.setdefault("dimensions", embedding["dimensions"])
    emb.setdefault("timeout", embedding.get("timeout", 10))

    set_if_blank(ensure("memory").setdefault("extraction", {}), "model", extraction)

    media = ensure("media")
    set_if_blank(media, "vision_model", vision)
    set_if_blank(media, "acquisition_model", acquisition)

    set_if_blank(ensure("social"), "decision_model", decision)
    #: the sandbox decision layer uses the same "internal fast model" knob
    set_if_blank(ensure("sandbox"), "decision_model", decision)
    set_if_blank(ensure("agent").setdefault("planner", {}), "model", planner)
    set_if_blank(ensure("agent").setdefault("evaluator", {}), "model", evaluator)

    return data


def _friendly_yaml_error(path: Path, exc: yaml.YAMLError) -> SystemExit:
    """Turn a PyYAML parse failure into an operator-actionable message."""
    mark = getattr(exc, "problem_mark", None)
    where = "（位置未知）"
    line_text = ""
    if mark is not None:
        where = f"{mark.line + 1} 行第 {mark.column + 1} 列"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if 0 <= mark.line < len(lines):
                line_text = lines[mark.line].rstrip()
            elif lines:
                # "unexpected end of stream" (e.g. an unclosed quote) points
                # past the last line — show the tail, where the construct began.
                line_text = lines[-1].rstrip()
        except OSError:
            pass
    problem = getattr(exc, "problem", None) or str(exc)
    return SystemExit(
        f"\n[ERROR] [Config] 配置文件语法错误：{path} {where}\n"
        f"    出错行原文：{line_text or '（无法读取）'}\n"
        f"    PyYAML 报告：{problem}\n"
        f"    常见原因：引号未闭合、缩进不一致（空格/制表符混用）、"
        f"冒号后缺空格、列表项 '- ' 后的内容没对齐。\n"
        f"    请修正后重启；也可删除该文件从 config.example.yaml 重建。"
    )


def _validate_model_refs(config: AppConfig) -> None:
    """Startup check: every model-name / provider-name reference must resolve.

    The router raises ``ModelNotFoundError`` only at call time, so a typo in
    ``models.extraction`` surfaces as "every extraction fails" — far too late.
    Log it here with the list of valid names instead.
    """
    model_names = [m.name for m in config.ai.models]
    provider_names = sorted(config.ai.providers)
    problems: list[str] = []

    def check_model(label: str, value: str) -> None:
        if value and value not in model_names:
            problems.append(
                f"{label}={value!r} 不是已注册的模型 name（可用：{', '.join(model_names) or '无'}）"
            )

    check_model("memory.extraction.model", config.memory.extraction.model)
    check_model("media.vision_model", config.media.vision_model)
    check_model("media.acquisition_model", config.media.acquisition_model)
    check_model("social.decision_model", config.social.decision_model)
    check_model("sandbox.decision_model", config.sandbox.decision_model)
    check_model("agent.planner.model", config.agent.planner.model)
    check_model("agent.evaluator.model", config.agent.evaluator.model)

    emb_provider = config.memory.semantic.embedding.provider
    if emb_provider and emb_provider not in provider_names:
        problems.append(
            f"memory.semantic.embedding.provider={emb_provider!r} 不是已注册的 provider"
            f"（可用：{', '.join(provider_names) or '无'}）"
        )

    for model in config.ai.models:
        if model.provider not in provider_names:
            problems.append(
                f"ai.models[{model.name}].provider={model.provider!r} 不是已注册的 provider"
                f"（可用：{', '.join(provider_names) or '无'}）"
            )

    if problems:
        for problem in problems:
            logger.error("[Config] 配置引用了未注册的模型/provider name：%s", problem)
        logger.error(
            "[Config] 已注册的模型 name：%s；已注册的 provider：%s —— 请在 models: 表里修正",
            ", ".join(model_names) or "无",
            ", ".join(provider_names) or "无",
        )


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
        try:
            with path.open("r", encoding="utf-8") as fp:
                loaded = yaml.safe_load(fp)
        except yaml.YAMLError as exc:
            raise _friendly_yaml_error(path, exc) from exc
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
    config = AppConfig.model_validate(data)
    _validate_model_refs(config)
    return config
