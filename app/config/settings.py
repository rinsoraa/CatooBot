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

    # ---------- Phase 13: real external gateway (transport → sandbox) ----------
    #: 是否启用真实外部网关（默认关，避免影响既有启动路径）
    gateway_enabled: bool = False
    #: 本 runtime 服务的 bot 账号（self_id）；未列出的账号事件一律丢弃（§54/§56）
    self_ids: list[str] = Field(default_factory=list)
    #: 入站去重：TTL（秒）与容量上限（有界内存，§17）
    dedupe_ttl: float = Field(default=600.0, gt=0)
    dedupe_max_size: int = Field(default=2048, ge=16)
    #: 每个 social lane 的待处理上限（§29）
    max_pending_per_lane: int = Field(default=20, ge=1)
    #: 出站发送的最大重试次数（§46）
    outbound_max_retries: int = Field(default=3, ge=0, le=10)
    #: 重连退避上限（秒，§50）；反向 WS 下只作为状态恢复的去抖，不主动拨号
    reconnect_max_seconds: float = Field(default=30.0, ge=1)
    #: 优雅关闭的时间预算（秒，Phase 13.1 §13）：先让已开始的工作收尾，再取消剩余
    shutdown_timeout: float = Field(default=5.0, gt=0)

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
    # Print the 🌍 world line when the *visible* state changes (the world itself
    # still ticks every second; off by default — changes are always shown)
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
    #: WebUI v1.0 rollback switch: "v1" serves the Vue SPA at /, "v0.8" hands
    #: / and /login back to the legacy SSR console (which stays at /legacy).
    version: str = "v1"


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


class BehaviorInitiativeCoreConfig(BaseModel):
    """Proactive chat for configured core friends — independent hard limits.

    A core friend (``sandbox.core_friend_identities`` / ``core_friend_ids``) is
    the person the character actually lives around, so she may reach out to
    them on her own schedule: own switch, interval, budgets, idle window and
    probabilities, none of them shared with the general initiative rules.
    Counters stay per person (``initiative_state`` is per scope), so one core
    friend's messages never consume another person's quota.

    No ``min_relationship_stage`` on purpose: a core friend holds the top
    ``core`` stage by identity, so such a gate could never reject anyone.
    """

    enabled: bool = True
    min_interval_minutes: int = Field(default=60, ge=1)
    daily_limit: int = Field(default=6, ge=0)
    hourly_limit: int = Field(default=2, ge=0)
    idle_hours: float = Field(default=3.0, ge=0.0)
    base_probability: float = Field(default=0.5, ge=0.0, le=1.0)
    relationship_bonus: float = Field(default=0.2, ge=0.0, le=1.0)
    topic_bonus: float = Field(default=0.35, ge=0.0, le=1.0)
    max_unanswered: int = Field(default=2, ge=0)
    duplicate_similarity: float = Field(default=0.6, ge=0.0, le=1.0)


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
    #: separate, independent rules for the configured core friends
    core_friend: BehaviorInitiativeCoreConfig = Field(default_factory=BehaviorInitiativeCoreConfig)


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
    #: Phase 16 §46-§48: 一次连续社交互动的静默上限（秒）。会话进行中，重复消息
    #: 只在同一条社交会话里处理，不再制造嵌套打断；超时视为会话结束、恢复自主生活。
    interaction_episode_timeout_seconds: float = Field(default=900.0, gt=0)
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


class CoreFriendRelationshipConfig(BaseModel):
    """核心好友的初始关系数值（留空 = 档案默认的 core 档：0.7/0.5/0.6/0.8）。

    只影响**起始值**；之后的增减仍由真实互动按既有规则决定。取值 0..1。
    """

    trust: float | None = Field(default=None, ge=0.0, le=1.0)
    familiarity: float | None = Field(default=None, ge=0.0, le=1.0)
    closeness: float | None = Field(default=None, ge=0.0, le=1.0)
    social_comfort: float | None = Field(default=None, ge=0.0, le=1.0)


class SandboxConfig(BaseModel):
    """Character Life Sandbox (v2.0 §16/§67/§202): the character *lives* here.

    ``enabled`` makes this the world core; the legacy WorldRuntime stays off.
    """

    enabled: bool = True
    #: 已废弃的“十分钟世界刷新”语义（Phase 14.1 移除）。现在只作为
    #: 手动 tick / 恢复时单次世界步进的**上限兜底**；世界时间由
    #: RuntimeScheduler 按真实经过时间推进（见 runtime.tick_interval_seconds）。
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
    #: 核心好友的初始关系数值（留空 = 档案默认）；例如全部拉满：{trust: 1.0, ...}
    core_friend_relationship: CoreFriendRelationshipConfig = Field(
        default_factory=CoreFriendRelationshipConfig
    )

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


def explicit_core_friends(
    config: SandboxConfig, *, core_names: list[str] | tuple[str, ...] = ()
) -> dict[str, str]:
    """QQ id → core-friend name, from *explicit* configuration only.

    The single source of truth for "who is a core friend": the sandbox persons
    map, the relationship stage table and the proactive-chat gate all read it,
    so a configured core friend cannot be a core friend in one subsystem and a
    stranger in another. Nothing is guessed — only the two config forms count,
    plus the legacy single-id list when the bible has exactly one core friend
    (``core_names`` comes from the compiled bible).
    """
    mapping: dict[str, str] = {}
    raw = getattr(config, "core_friend_ids", []) or []
    if isinstance(raw, dict):
        mapping.update({str(k): str(v) for k, v in raw.items() if v})
    explicit = getattr(config, "core_friend_identities", None) or {}
    if isinstance(explicit, dict):
        mapping.update({str(k): str(v) for k, v in explicit.items() if v})
    names = [str(name) for name in core_names if name]
    if isinstance(raw, (list, tuple)) and len(raw) == 1 and len(names) == 1:
        mapping.setdefault(str(raw[0]), names[0])
    return mapping


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


class RuntimeConfig(BaseModel):
    """Long-lived runtime scheduling (Phase 14 §63/§64).

    The *world* step is the real elapsed time; this is only how often the
    scheduler asks the world to advance (Phase 14.1: the scheduler is the sole
    owner of ``SandboxRuntime.tick``).
    """

    enabled: bool = True
    #: 调度间隔（秒）：多久问一次世界；不是世界步长，也不是 LLM 频率
    tick_interval_seconds: float = Field(default=1.0, ge=0.05, le=60.0)
    #: 追帧上限（秒）：停机后再久也只走一次有界步进（默认 5 分钟）
    max_catchup_seconds: float = Field(default=300.0, ge=0.0)
    #: 停机时等待调度器收尾的预算（秒）
    shutdown_timeout_seconds: float = Field(default=5.0, gt=0)


class MoveToConfig(BaseModel):
    """Phase 3C：move_to（非破坏性导航）的安全门参数。"""

    #: 单次移动的最大距离（格，相对当前位置）；第一版 64，不允许数千米长距离
    max_distance: float = Field(default=64.0, gt=0, le=1024)


class FollowPlayerConfig(BaseModel):
    """Phase 3D：follow_player（动态跟随）的参数与安全门。"""

    #: 跟随 Action 的最长运行时长（秒）；不许无限运行（10~600，第一版不支持 0）
    timeout: float = Field(default=120.0, ge=10.0, le=600.0)
    #: 最大追逐距离（格）：与目标直线距离超过它就失败，不追到世界尽头
    max_chase_distance: float = Field(default=64.0, gt=0, le=1024)


class DigConfig(BaseModel):
    """Phase 4B：dig（破坏单个方块）的安全门。

    第一版只允许近距离、当前手持工具可挖的单块；不导航、不换工具、不捡掉落物。
    """

    #: 单次挖掘的超时（秒）；obsidian 之类慢方块留有余量
    timeout: float = Field(default=30.0, ge=5.0, le=120.0)
    #: 最大挖掘距离（格，眼睛 → 方块中心，与 mineflayer canDigBlock 同口径）
    max_distance: float = Field(default=5.0, gt=0, le=6.0)


class PlaceConfig(BaseModel):
    """Phase 4C：place（放置单个方块）的安全门。

    第一版只往**空气格**放、只用当前主手的物品；不导航、不找放置面、不换 hotbar、不补货。
    """

    #: 单次放置的超时（秒）
    timeout: float = Field(default=30.0, ge=5.0, le=120.0)
    #: 最大交互距离（格，眼睛 → 目标方块中心，与 dig 同口径）
    max_distance: float = Field(default=5.0, gt=0, le=6.0)


class EquipConfig(BaseModel):
    """Phase 4D：equip（把物品拿到主手）的超时。背包写操作很快，15s 足够。"""

    timeout: float = Field(default=15.0, ge=5.0, le=120.0)


class InventoryMoveConfig(BaseModel):
    """Phase 4D：inventory_move（单物品单槽位搬运）的超时。"""

    timeout: float = Field(default=15.0, ge=5.0, le=120.0)


class RecipeLookupConfig(BaseModel):
    """Phase 4F：recipe_lookup（只读查配方）的超时。纯本地计算，不需要长等待。"""

    timeout: float = Field(default=10.0, ge=1.0, le=60.0)


class PickupConfig(BaseModel):
    """Phase 4H：单实体拾取（有限导航 + 收集确认）的安全门。

    第一版只有一个最大距离与超时：进入拾取半径（runtime 常量）就停导航、等服务器收集，
    不把半径/轮询周期这类内部旋钮暴露成用户配置。
    """

    #: 目标掉落物离眼睛的最大距离（格）；超出的直接拒绝，绝不追太远
    max_distance: float = Field(default=16.0, gt=0, le=64.0)
    #: 单次拾取的最长秒数（导航 + 等待收集）
    timeout: float = Field(default=30.0, ge=5.0, le=120.0)


class CraftingTableConfig(BaseModel):
    """Phase 4G：指定工作台（3×3）时的安全门。只加一个距离，不做一堆细碎开关。"""

    #: 最大交互距离（格，眼睛 → 工作台方块中心，与 dig/place/container 同口径）
    max_distance: float = Field(default=5.0, gt=0, le=6.0)


class CraftConfig(BaseModel):
    """Phase 4F/4G：craft（一次一个配方；2×2 或指定工作台的 3×3）的超时与安全门。"""

    timeout: float = Field(default=30.0, ge=5.0, le=120.0)
    #: Phase 4G：指定工作台时的距离上限（不指定工作台时用不到）
    crafting_table: CraftingTableConfig = Field(default_factory=CraftingTableConfig)


class ContainerConfig(BaseModel):
    """Phase 4E：container（读 Chest / Barrel + 单物品存取）的安全门。

    第一版只支持**单方块** chest / barrel、只在一个容器槽与一个背包槽之间搬一次；
    不导航、不自动开未知容器、不操作双箱/潜影盒/熔炉。
    """

    #: 单次容器动作的超时（秒）
    timeout: float = Field(default=30.0, ge=5.0, le=120.0)
    #: 最大交互距离（格，眼睛 → 容器方块中心，与 dig/place 同口径）
    max_distance: float = Field(default=5.0, gt=0, le=6.0)


class FindBlocksConfig(BaseModel):
    """Phase 4K：找方块（只读查询）的默认值与安全上限。

    只回答「附近有哪些指定方块」，不移动/不装备/不挖/不拾取：
    范围与条数都有硬上限，绝不允许"扫全世界"（LLM 传 1000 会被拒）。
    """

    #: 默认搜索半径（格）；调用方没给 max_distance 时用它
    max_distance: int = Field(default=16, ge=1, le=32)
    #: 默认最多返回多少条；调用方没给 max_results 时用它
    max_results: int = Field(default=8, ge=1, le=16)


class MinecraftActionConfig(BaseModel):
    """Action Runtime 的动作级配置（Phase 3C 起）。"""

    move_to: MoveToConfig = Field(default_factory=MoveToConfig)
    follow_player: FollowPlayerConfig = Field(default_factory=FollowPlayerConfig)
    #: Phase 4B：第一个世界修改动作
    dig: DigConfig = Field(default_factory=DigConfig)
    #: Phase 4C：放置单个方块（dig 的对称实现）
    place: PlaceConfig = Field(default_factory=PlaceConfig)
    #: Phase 4D：背包写操作（拿到手上 / 单物品单槽位搬运）
    equip: EquipConfig = Field(default_factory=EquipConfig)
    inventory_move: InventoryMoveConfig = Field(default_factory=InventoryMoveConfig)
    #: Phase 4E：单方块容器（读 Chest / Barrel + 单物品存取）
    container: ContainerConfig = Field(default_factory=ContainerConfig)
    #: Phase 4F：玩家自身 2×2 背包合成（查配方 + 执行一次）
    recipe_lookup: RecipeLookupConfig = Field(default_factory=RecipeLookupConfig)
    craft: CraftConfig = Field(default_factory=CraftConfig)
    #: Phase 4H：捡起一个明确的掉落物实体（有限导航 + 有限距离）
    pickup: PickupConfig = Field(default_factory=PickupConfig)
    #: Phase 4K：找方块（只读；默认半径/条数，硬上限在 runtime 侧）
    find_blocks: FindBlocksConfig = Field(default_factory=FindBlocksConfig)


class MinecraftAgentToolsConfig(BaseModel):
    """Phase 3E：LLM Tool 层能做什么（任务书 §三十一）。

    与风险分级（§十三）一一对应：SAFE / LOW 本阶段有对应 Tool；MEDIUM/HIGH/DESTRUCTIVE
    现在**没有任何动作**，先按 false 把门装好，等 Phase 4 再实现。
    """

    #: Minecraft 工具总开关（关掉 = 模型完全碰不到 Minecraft）
    enabled: bool = True
    #: SAFE：只读查询 / 说话 / 朝向 / 停止
    allow_safe: bool = True
    #: LOW：非破坏性移动与跟随（还必须「用户明确要求」，见 §十五）
    allow_low: bool = True
    allow_medium: bool = False  # 尚未实现任何 MEDIUM 动作
    allow_high: bool = False  # 尚未实现任何 HIGH 动作
    allow_destructive: bool = False  # 尚未实现任何 DESTRUCTIVE 动作


class MinecraftConfirmationConfig(BaseModel):
    """Phase 4A：MEDIUM/HIGH 动作的用户确认门（任务书 §八）。

    确认只活在内存里，重启即失效（绝不让旧授权复活）；TTL 到点即 EXPIRED。
    """

    #: 一次确认的有效期（秒）；10~300
    ttl_seconds: float = Field(default=60.0, ge=10.0, le=300.0)
    #: 同时挂起的确认上限（有界内存；超出时最旧的先失效）
    max_pending: int = Field(default=32, ge=1, le=256)


class MinecraftAgentChatConfig(BaseModel):
    """Phase 4A：Minecraft 玩家聊天 → 角色对话（USER 回合）桥。"""

    #: 玩家在游戏里说话时，让角色按用户回合接话并在游戏里回复
    enabled: bool = True
    #: 游戏内回复的长度上限（Minecraft 聊天本身 256 字符上限；这里更短更自然）
    max_reply_chars: int = Field(default=200, ge=20, le=256)


class MinecraftAgentConfig(BaseModel):
    """Phase 3E：Minecraft Agent Bridge（LLM ↔ 已存在的动作能力）。"""

    tools: MinecraftAgentToolsConfig = Field(default_factory=MinecraftAgentToolsConfig)
    #: Phase 4A：MEDIUM/HIGH 的确认门参数
    confirmation: MinecraftConfirmationConfig = Field(default_factory=MinecraftConfirmationConfig)
    #: Phase 4A：游戏内聊天 → 角色对话
    chat: MinecraftAgentChatConfig = Field(default_factory=MinecraftAgentChatConfig)
    #: Phase 4A：可信 Minecraft 玩家名（LOW 及以上动作只对这些人执行；SAFE 不限）
    trusted_players: list[str] = Field(default_factory=list)


class WorldActivityConfig(BaseModel):
    """Phase 6A：世界活动（Activity Episode）—— 只暴露真正需要的四个旋钮（§五十三）。"""

    #: 关掉 = 完全没有 Episode 生命周期（角色状态里的 activity 不再被投影覆盖）
    enabled: bool = True
    #: 运行中的观察/时间推进最多多久落一次盘（§二十三：禁止每秒写数据库）
    persistence_interval_seconds: float = Field(default=60.0, ge=10.0, le=600.0)
    #: 重启恢复的宽限：计划结束时间离现在这么近就不算"已过期"（避免每次重启都强行转移）
    recovery_grace_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)
    #: 最近 Episode 读多少条（LLM 上下文与 WebUI 的默认窗口，§三十五/§三十六）
    recent_episode_limit: int = Field(default=5, ge=1, le=10)
    # ---- Phase 6B：决策引擎（只加这三个，§五十三）
    #: 进入"准备换活动"的窗口（分钟）：窗口内只立 pending，不切活动（§十六）
    transition_window_minutes: float = Field(default=5.0, ge=0.0, le=60.0)
    #: 一条 Episode 最多自动延长几次（防无限续命，§十六）
    max_extensions_per_episode: int = Field(default=2, ge=0, le=5)
    #: 撞车冷却（分钟）：刚做过的活动在这么久内不许立刻回来（防 A→B→A，§十七）
    bounce_cooldown_minutes: float = Field(default=10.0, ge=0.0, le=240.0)
    # ---- Phase 6C：rolling horizon（只加这三个，§六十八：别加几十个 tuning knobs）
    #: 规划视野（分钟）：只保证"未来 1~4 小时"有计划，绝不排满一整天（§六）。
    #: 范围 60~720（1~12 小时）；超出直接是 config validation error（§七）。
    planning_horizon_minutes: float = Field(default=240.0, ge=60.0, le=720.0)
    #: 两次"软触发"重新规划之间的最短间隔（分钟，§九）；重大触发可以突破它
    planner_refresh_min_minutes: float = Field(default=5.0, ge=0.0, le=120.0)
    #: horizon 里最多排几条 future proposal（§六十八：默认 6，不要更多）
    max_future_episodes: int = Field(default=6, ge=1, le=6)


class WorldConfig(BaseModel):
    """Phase 6A：角色世界（目前只有时钟与活动）。"""

    #: 世界时钟的时区（§二十二：默认 Asia/Singapore，可配置）
    timezone: str = "Asia/Singapore"
    activity: WorldActivityConfig = Field(default_factory=WorldActivityConfig)


class TaskRuntimeConfig(BaseModel):
    """Phase 5A：多步骤任务的上限（保守默认；只暴露这四个旋钮，§九十七）。"""

    #: 一个 Task 从创建到过期的总时长（秒）；到点 EXPIRED 并停掉正在跑的动作
    ttl_seconds: float = Field(default=600.0, ge=30.0, le=3600.0)
    #: 计划里最多多少步（防"1000 步"）
    max_steps: int = Field(default=16, ge=1, le=64)
    #: 最多重规划几次
    max_replans: int = Field(default=2, ge=0, le=5)
    #: 连续多少次"同一个工具 + 同一份参数 + 状态没变"就认为卡住（暂停任务）
    no_progress_limit: int = Field(default=3, ge=1, le=10)


class MinecraftMemoryConfig(BaseModel):
    """Phase 5C：身份桥 + 持久世界记忆（只暴露真正需要的旋钮）。

    关掉这里只影响"记得/想得起"，**绝不影响**任务、确认门与动作权限（§二：记忆只给上下文）。
    """

    enabled: bool = True
    #: 周期对账间隔（世界感知 → 记忆；绝不反向写世界）
    reconcile_interval_seconds: float = Field(default=300.0, ge=30.0, le=3600.0)
    #: 一次 turn 注入的记忆条数上限（§四十九：≤5 条）
    context_items: int = Field(default=5, ge=1, le=5)
    #: 运维显式配置的「QQ 号 → Minecraft 玩家名」（§六 优先级 2；
    #: 次于用户自己显式验证，且**只**建立身份关联，绝不授予任何权限）
    linked_players: dict[str, str] = Field(default_factory=dict)


class MinecraftConfig(BaseModel):
    """Minecraft 连接层（Phase 1）：Bridge runtime（mineflayer 子进程）的托管参数。

    账号认证信息不在这里——它们只存在于 ``<runtime_dir>/auth.json``
    （本地安全目录，已 gitignore），绝不经过 Bridge API / 配置 / Git 传输。
    """

    enabled: bool = False
    #: Minecraft Runtime（Node.js）所在目录，含 runtime.js 与 auth.json
    runtime_dir: str = "minecraft_runtime"
    node_executable: str = "node"
    #: Bridge runtime 的本地 HTTP 端口（只绑定 127.0.0.1）
    runtime_port: int = Field(default=25580, ge=1024, le=65535)
    #: 由 CatooBot 启动/重启/回收 runtime 进程；关闭 = 外部自管（进阶用法）
    auto_start_runtime: bool = True
    #: 等待 runtime 进程健康检查通过的最长时间
    startup_timeout_seconds: float = Field(default=30.0, gt=0)
    #: 单次 Bridge HTTP 请求超时
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    #: 状态对账轮询间隔（事件以回调推送为主，轮询只兜底对账）
    poll_interval_seconds: float = Field(default=5.0, ge=1.0)
    #: 传给 runtime 的连接看门狗：多久没进世界算失败
    connect_timeout_seconds: float = Field(default=75.0, gt=0)
    #: runtime 进程意外退出后的自动重启预算（防崩溃循环）
    max_runtime_restarts: int = Field(default=3, ge=0)
    #: 外部自管 runtime 时的回调地址与共享密钥（auto_start=true 时自动生成，留空）
    external_callback_url: str = ""
    external_callback_token: str = ""
    # ---- 世界感知（Phase 2，只读「眼睛」）
    #: 进入世界后持续获取 Raw World Snapshot 并构建语义模型
    perception_enabled: bool = True
    #: 分层刷新周期（Near 高频高细 / Local 中频 / Extended 低频摘要）
    near_interval_seconds: float = Field(default=1.0, ge=0.3, le=120)
    local_interval_seconds: float = Field(default=4.0, ge=1.0, le=600)
    extended_interval_seconds: float = Field(default=20.0, ge=5.0, le=1800)
    #: 语义级感知事件的最小间隔（去抖；方块变化聚合为 world.changed）
    world_event_cooldown_seconds: float = Field(default=5.0, ge=1.0, le=300)
    #: 触发 world.changed 的近层方块变化数量阈值
    world_change_block_threshold: int = Field(default=10, ge=1, le=1000)
    #: Phase 3C：动作级配置（move_to 的安全门）
    action: MinecraftActionConfig = Field(default_factory=MinecraftActionConfig)
    #: Phase 3E：Agent Bridge（六个 LLM Tool 的权限门）
    agent: MinecraftAgentConfig = Field(default_factory=MinecraftAgentConfig)
    #: Phase 5C：身份桥 + 持久 Minecraft 记忆
    memory: MinecraftMemoryConfig = Field(default_factory=MinecraftMemoryConfig)


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
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    minecraft: MinecraftConfig = Field(default_factory=MinecraftConfig)
    #: Phase 5A：多步骤任务运行时（第一期只接入 Minecraft 工具回路）
    task: TaskRuntimeConfig = Field(default_factory=TaskRuntimeConfig)
    world: WorldConfig = Field(default_factory=WorldConfig)
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


def resolve_models_block(data: dict[str, Any]) -> dict[str, Any]:
    """Public, side-effect-free view of the ``models:`` expansion (WebUI W2)."""
    return _resolve_models(data)


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
