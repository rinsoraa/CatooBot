"""配置注册表：把真实 Config schema 描述给 WebUI（WebUI v1.0 · W2 §11-§17）。

它不是第二套配置——值永远来自 `AppConfig`；这里只有"如何理解这个字段"的
元数据（标签/分区/级别/是否热重载/是否需要重启/使用状态），并且：

* 结构从 pydantic 模型推导（``AppConfig.model_fields``），不会与 settings.py 漂移；
* 只有 UI 元数据是显式的（``_BASIC``/``_ADVANCED``/``_UNUSED`` …），
  它们与 W1 的 ``docs/WEBUI_CONFIG_MATRIX.md`` 一一对应；
* ``DEFINED_BUT_UNUSED`` / ``LEGACY`` 默认不出现在普通查询里，但从不隐藏事实。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from app.config.settings import (
    OVERRIDES_PATH,
    PROJECT_ROOT,
    AppConfig,
    read_overrides,
    resolve_models_block,
)

BASIC = "basic"
ADVANCED = "advanced"
EXPERT = "expert"

ACTIVE = "ACTIVE"
ACTIVE_WITH_RESTART = "ACTIVE_WITH_RESTART"
CONDITIONALLY_USED = "CONDITIONALLY_USED"
DEFINED_BUT_UNUSED = "DEFINED_BUT_UNUSED"
LEGACY = "LEGACY"

#: 区域名与 WebUI v1.0 的八个分区一致
AREAS = (
    "总览",
    "角色",
    "AI与模型",
    "社交",
    "记忆",
    "媒体与能力",
    "系统",
    "运行",
)

_SECTION_AREA = {
    "bot": "系统",
    "onebot": "系统",
    "logging": "运行",
    "database": "系统",
    "permissions": "系统",
    "ai": "AI与模型",
    "character": "角色",
    "memory": "记忆",
    "web": "系统",
    "behavior": "角色",
    "social": "社交",
    "media": "媒体与能力",
    "conversation": "角色",
    "continuity": "记忆",
    "runtime": "运行",
    "sandbox": "角色",
    "tools": "媒体与能力",
    "agent": "媒体与能力",
    "expression": "媒体与能力",
}

#: 普通用户第一屏就会用到的键（W1 矩阵 §3.4 的顺序即优先级）
_BASIC = (
    "ai.enabled",
    "ai.providers.",
    "ai.models[",
    "ai.default_temperature",
    "ai.timeout",
    "bot.name",
    "bot.debug",
    "onebot.",
    "logging.level",
    "logging.color",
    "logging.narrate",
    "logging.narrate_world_ticks",
    "permissions.",
    "memory.enabled",
    "memory.extraction.",
    "memory.consolidation.schedule",
    "character.timezone",
    "web.host",
    "web.port",
    "web.username",
    "behavior.reply.enabled",
    "behavior.reply.min_delay",
    "behavior.reply.max_delay",
    "behavior.chunking.enabled",
    "behavior.chunking.chunk_probability",
    "behavior.chunking.max_chunks",
    "behavior.schedule.sleep_",
    "behavior.schedule.dnd_enabled",
    "behavior.schedule.dnd_start",
    "behavior.schedule.dnd_end",
    "behavior.group.participation_enabled",
    "behavior.initiative.enabled",
    "behavior.initiative.min_interval_minutes",
    "behavior.initiative.daily_limit",
    "behavior.initiative.hourly_limit",
    "social.enabled",
    "social.continuation.window_minutes",
    "social.participation.",
    "media.enabled",
    "tools.enabled",
    "agent.enabled",
    "runtime.enabled",
    "continuity.enabled",
    "sandbox.conversation_max_response_chars",
    "sandbox.max_background_messages_per_day",
    "conversation.debounce.",
    "ai.context.max_messages",
)

#: 需要理解后果才改的键（其余默认 expert）
_ADVANCED = (
    "ai.providers.<n>.type",
    "ai.context.enabled",
    "ai.max_tokens",
    "ai.cooldown.rate_limit_seconds",
    "ai.cooldown.server_error_seconds",
    "ai.concurrency.",
    "ai.usage.",
    "onebot.api_timeout",
    "database.url",
    "logging.log_dir",
    "logging.narrate_thinking",
    "logging.watch_config_",
    "memory.retrieval.top_k",
    "memory.retrieval.min_relevance",
    "memory.semantic.embedding.model",
    "memory.consolidation.duplicate_threshold",
    "character.identity",
    "character.personality",
    "character.speaking_style",
    "character.behavior_rules",
    "character.system_prompt",
    "sandbox.bible_path",
    "sandbox.allow_ai_decisions",
    "sandbox.reset_on_bible_change",
    "sandbox.sync_persona_from_bible",
    "sandbox.core_friend",
    "sandbox.conversation_model",
    "sandbox.decision_model",
    "sandbox.memory_context_limit",
    "sandbox.memory_context_max_chars",
    "sandbox.experience_context_limit",
    "sandbox.snapshot_keep",
    "social.decision_model",
    "social.group_context.",
    "social.feedback_retention_days",
    "continuity.",
    "media.sticker_",
    "media.max_stickers_per_turn",
    "media.auto_collect",
    "media.indexer_enabled",
    "media.background_vision_enabled",
    "media.expression_enabled",
    "tools.rate_limit.",
    "tools.candidate_tools",
    "tools.decision_mode",
    "tools.default_timeout",
    "tools.max_calls_per_turn",
    "tools.max_execution_time",
    "tools.max_retries",
    "tools.cache_enabled",
    "agent.autonomy",
    "agent.mode.",
    "agent.budget.",
    "agent.planner.",
    "agent.evaluator.",
    "agent.max_observations_in_context",
    "expression.min_speakers",
    "expression.min_occurrences",
    "expression.max_patterns_per_group",
    "expression.inject_",
    "behavior.initiative.min_relationship_stage",
    "behavior.initiative.idle_hours",
    "behavior.initiative.max_unanswered",
    "behavior.schedule.dnd_blocks_replies",
    "behavior.reply.busy_factor",
    "behavior.reply.sleeping_factor",
    "behavior.reply.night_factor",
    "behavior.reply.close_relationship_factor",
    "behavior.reply.jitter",
    "runtime.tick_interval_seconds",
    "web.enabled",
)

#: 无真实读取方（W1 矩阵 §3.1）：默认隐藏，事实仍可查询
_UNUSED = frozenset(
    {
        "memory.semantic.batch_size",
        "memory.consolidation.enabled",
        "social.continuation.enabled",
        "social.continuation.max_messages",
        "social.observer.max_staleness_messages",
        "social.attention.enabled",
        "social.fatigue.enabled",
        "social.topic.enabled",
        "social.observation_retention_days",
        "behavior.initiative.active_activity_factor",
        "tools.permissions.default_enabled",
        "media.media_dir",
        "media.max_library_size",
        "media.acquisition_model",
        "media.import_as_sticker",
        "conversation.debounce.enabled",
        "sandbox.max_events_keep",
        "expression.learn_max_per_hour",
        "agent.background",
    }
)

#: 语义已废弃（仍被读作步进上限）
_LEGACY = frozenset({"sandbox.tick_seconds"})

#: 仅在某个开关打开时才被读取 → (flag key, 人话说明)
_CONDITIONAL = {
    "onebot.enabled": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.self_ids": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.dedupe_ttl": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.dedupe_max_size": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.max_pending_per_lane": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.outbound_max_retries": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.reconnect_max_seconds": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.shutdown_timeout": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "sandbox.social_space_map": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "media.expression_enabled": ("", "聊天插件加载后生效"),
    "media.recognition_timeout_seconds": ("", "聊天插件加载后生效"),
}

#: 保存即生效的键（ConfigAdminService.apply 的真实覆盖面，见 W1 矩阵 §3.3）
_HOT = (
    "ai.",
    "logging.level",
    "logging.color",
    "logging.narrate",
    "logging.narrate_thinking",
    "logging.narrate_world_ticks",
    "logging.log_dir",
    "permissions.",
    "memory.",
    "behavior.",
    "character.timezone",
    "bot.name",
    "bot.debug",
)
_HOT_EXCEPT = ("memory.semantic.", "logging.watch", "logging.watchdog_")

#: 需要重启才生效（组件在启动时捕获了配置）
_RESTART_NOTE = "保存后需要重启 CatooBot 才会生效"

_SENSITIVE_LEAVES = frozenset(
    {"access_token", "password", "token", "secret", "api_key", "webhook_secret"}
)

#: 常见叶子名 → 中文标签
_LABELS = {
    "enabled": "启用",
    "name": "名称",
    "host": "监听地址",
    "port": "端口",
    "path": "路径",
    "url": "地址",
    "base_url": "接口地址",
    "api_key_env": "API Key 环境变量名",
    "model": "模型",
    "models": "模型列表",
    "providers": "服务商",
    "provider": "服务商",
    "type": "接口类型",
    "level": "日志级别",
    "timeout": "超时（秒）",
    "debug": "调试模式",
    "temperature": "默认温度",
    "default_temperature": "默认温度",
    "max_tokens": "最大输出 tokens",
    "context": "上下文",
    "max_messages": "上下文条数",
    "top_k": "召回条数",
    "schedule": "周期",
    "timezone": "时区",
    "username": "登录用户名",
    "password": "登录密码",
    "superusers": "超级管理员",
    "admins": "管理员",
    "interaction_count": "互动次数",
    "cooldown": "冷却",
    "seed": "随机种子",
    "dir": "目录",
    "days": "保留天数",
    "limit": "上限",
    "probability": "概率",
    "respect": "尊重",
}


def _leaf_label(leaf: str) -> str:
    return _LABELS.get(leaf, leaf.replace("_", " "))


@dataclass(frozen=True)
class ConfigField:
    key: str
    label: str
    description: str
    type: str
    default: Any
    constraints: dict[str, Any] = field(default_factory=dict)
    area: str = "系统"
    level: str = ADVANCED
    hot_reload: bool = False
    restart_required: bool = True
    usage_status: str = ACTIVE_WITH_RESTART
    sensitive: bool = False
    choices: tuple[str, ...] = ()
    hidden: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "type": self.type,
            "default": self.default,
            "constraints": self.constraints,
            "area": self.area,
            "level": self.level,
            "hot_reload": self.hot_reload,
            "restart_required": self.restart_required,
            "usage_status": self.usage_status,
            "sensitive": self.sensitive,
            "choices": list(self.choices),
            "hidden": self.hidden,
        }


# ------------------------------------------------------------------ derivation


def _type_name(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin in (list, tuple, set, frozenset):
        return "list"
    if origin is dict:
        return "dict"
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "str"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "obj"
    return "str"


def _constraints(field_info: Any) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for rule in field_info.metadata:
        kind = type(rule).__name__
        if kind == "Ge":
            found["min"] = rule.ge
        elif kind == "Gt":
            found["exclusive_min"] = rule.gt
        elif kind == "Le":
            found["max"] = rule.le
        elif kind == "Lt":
            found["exclusive_max"] = rule.lt
        elif kind in ("MinLen", "MinLength"):
            found["min_length"] = rule.min_length
        elif kind in ("MaxLen", "MaxLength"):
            found["max_length"] = rule.max_length
        elif kind == "Pattern":
            found["pattern"] = rule.pattern
    return found


def _matches(key: str, prefix: str) -> bool:
    """Prefix match that understands the ``<n>`` / ``[i]`` wildcard forms."""
    if prefix.endswith("."):
        return key.startswith(prefix)
    return key == prefix or key.startswith(prefix + ".") or key.startswith(prefix + "[")


def _usage_status(key: str) -> str:
    if key in _UNUSED:
        return DEFINED_BUT_UNUSED
    if key in _LEGACY:
        return LEGACY
    for prefix, (_flag, _why) in _CONDITIONAL.items():
        if _matches(key, prefix):
            return CONDITIONALLY_USED
    return ACTIVE if _hot(key) else ACTIVE_WITH_RESTART


def _hot(key: str) -> bool:
    if any(_matches(key, exception) for exception in _HOT_EXCEPT):
        return False
    return any(_matches(key, prefix) for prefix in _HOT)


def _level(key: str) -> str:
    if any(_matches(key, prefix) for prefix in _BASIC):
        return BASIC
    if any(_matches(key, prefix) for prefix in _ADVANCED):
        return ADVANCED
    return EXPERT


def _area(key: str) -> str:
    section = key.split(".", 1)[0]
    return _SECTION_AREA.get(section, "系统")


def _sensitive(key: str) -> bool:
    leaf = key.rsplit(".", 1)[-1]
    if leaf == "api_key_env":  # a variable *name* is not itself a secret
        return False
    return leaf in _SENSITIVE_LEAVES


def _describe(key: str, leaf: str) -> str:
    hot = _hot(key)
    base = f"{_leaf_label(leaf)}（{key}）"
    if key in _UNUSED:
        return base + "：当前版本没有任何读取方，保留仅为了兼容旧配置。"
    if key in _LEGACY:
        return base + "：旧语义（每 10 分钟世界刷新）已废弃，现在只作为手动步进的上限。"
    if hot:
        return base + "：保存后立即生效。"
    return base + "：" + _RESTART_NOTE + "。"


def _walk(model: type[BaseModel], prefix: str, out: list[ConfigField]) -> None:
    for name, info in model.model_fields.items():
        key = f"{prefix}.{name}" if prefix else name
        annotation = info.annotation
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is dict and args and isinstance(args[1], type) and issubclass(args[1], BaseModel):
            _walk(args[1], f"{key}.<n>", out)
            continue
        if (
            origin in (list, tuple, set, frozenset)
            and args
            and isinstance(args[0], type)
            and issubclass(args[0], BaseModel)
        ):
            _walk(args[0], f"{key}[i]", out)
            continue
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            _walk(annotation, key, out)
            continue
        try:
            default = info.get_default(call_default_factory=True)
        except Exception:  # noqa: BLE001 - a factory that needs context
            default = None
        if isinstance(default, BaseModel):
            continue
        leaf = name
        out.append(
            ConfigField(
                key=key,
                label=_leaf_label(leaf),
                description=_describe(key, leaf),
                type=_type_name(annotation),
                default=_jsonable(default),
                constraints=_constraints(info),
                area=_area(key),
                level=_level(key),
                hot_reload=_hot(key),
                restart_required=not _hot(key),
                usage_status=_usage_status(key),
                sensitive=_sensitive(key),
                hidden=key in _UNUSED or key in _LEGACY,
            )
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


_CACHE: list[ConfigField] | None = None


def fields() -> list[ConfigField]:
    """Every config field, derived from the real ``AppConfig`` schema."""
    global _CACHE
    if _CACHE is None:
        out: list[ConfigField] = []
        _walk(AppConfig, "", out)
        _CACHE = out
    return _CACHE


def field_for(key: str) -> ConfigField | None:
    for item in fields():
        if item.key == key:
            return item
    return None


def schema(
    *,
    area: str = "",
    level: str = "",
    include_unused: bool = False,
    include_sensitive: bool = True,
) -> list[dict[str, Any]]:
    """Field metadata for form rendering (values come from ``effective``)."""
    rows: list[dict[str, Any]] = []
    for item in fields():
        if item.hidden and not include_unused:
            continue
        if not include_sensitive and item.sensitive:
            continue
        if area and item.area != area:
            continue
        if level and item.level != level:
            continue
        rows.append(item.as_dict())
    return rows


# -------------------------------------------------------------------- layers

_ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env_layer() -> dict[str, Any]:
    from app.config.settings import _ENV_OVERRIDES  # noqa: PLC0415 - internal map, read-only

    layer: dict[str, Any] = {}
    for env_name, (section, key) in _ENV_OVERRIDES.items():
        if env_name in os.environ:
            layer[f"{section}.{key}"] = os.environ[env_name]
    return layer


def _yaml_layers() -> tuple[dict[str, Any], dict[str, Any]]:
    """``(raw, expanded)`` — expanded is what ``models:`` produced."""
    import yaml

    path = PROJECT_ROOT / "config" / "config.yaml"
    if not path.exists():
        return {}, {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {}
    import copy

    expanded = resolve_models_block(copy.deepcopy(raw))
    return raw, expanded


def source_map(overrides_path: Path | None = None) -> dict[str, str]:
    """Per-key provenance: env / overrides / models / yaml."""
    from app.web.api.common import flatten

    env_flat = _env_layer()
    overrides_flat = flatten(read_overrides(overrides_path or OVERRIDES_PATH))
    raw, expanded = _yaml_layers()
    raw_flat = flatten(raw)
    expanded_flat = flatten(expanded)
    result: dict[str, str] = {}
    for key in set(env_flat) | set(overrides_flat) | set(expanded_flat) | set(raw_flat):
        if key in env_flat:
            result[key] = "env"
        elif key in overrides_flat:
            result[key] = "overrides"
        elif key in expanded_flat and key not in raw_flat:
            result[key] = "models"
        elif key in raw_flat:
            result[key] = "yaml"
    return result


def _mask(value: Any) -> dict[str, Any]:
    from app.tools.credentials import CredentialManager

    text = "" if value is None else str(value)
    return {"configured": bool(text), "masked": CredentialManager.mask(text) if text else ""}


def _concrete(field_: ConfigField, config: AppConfig) -> list[tuple[str, Any]]:
    """Expand ``<n>`` / ``[i]`` wildcards into the instances that exist now."""
    if "<n>" not in field_.key and "[i]" not in field_.key:
        return [(field_.key, _read(config, field_.key))]
    rows: list[tuple[str, Any]] = []
    for concrete in _instances(field_.key, config):
        rows.append((concrete, _read(config, concrete)))
    return rows


def _instances(key: str, config: AppConfig) -> list[str]:
    from app.web.api.common import split_key

    head = key.split(".<n>", 1)[0].split("[i]", 1)[0]
    parts = split_key(head)
    node: Any = config
    for part in parts:
        if isinstance(part, int):
            node = node[part] if isinstance(node, list) and part < len(node) else None
        else:
            node = getattr(node, str(part), None)
    if isinstance(node, dict):
        names = list(node.keys())
        return [key.replace("<n>", str(name)) for name in names]
    if isinstance(node, list):
        return [key.replace("[i]", f"[{index}]") for index in range(len(node))]
    return []


def _read(config: Any, key: str) -> Any:
    from app.web.api.common import split_key

    node: Any = config
    for part in split_key(key):
        if isinstance(part, int):
            if not isinstance(node, list) or part >= len(node):
                return None
            node = node[part]
        else:
            model_dump = getattr(node, "model_dump", None)
            if model_dump is not None:
                node = getattr(node, str(part), None)
            elif isinstance(node, dict):
                node = node.get(str(part))
            else:
                return None
    return node


def effective(
    config: AppConfig,
    *,
    area: str = "",
    level: str = "",
    include_unused: bool = False,
    keys: list[str] | None = None,
    overrides_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Effective values with their provenance — the "what is it *now*" view."""
    from app.web.api.common import split_key

    _ = split_key  # keys are parsed by _read/_instances; kept for symmetry
    wanted = set(keys) if keys else None
    wanted_wild = [key for key in (keys or []) if "<n>" in key or "[i]" in key]
    sources = source_map(overrides_path)
    env_layer = _env_layer()
    rows: list[dict[str, Any]] = []
    for item in fields():
        if item.hidden and not include_unused:
            continue
        if area and item.area != area:
            continue
        if level and item.level != level:
            continue
        for concrete, value in _concrete(item, config):
            if (
                wanted is not None
                and concrete not in wanted
                and not any(wildcard_match(concrete, pattern) for pattern in wanted_wild)
            ):
                continue
            row = {
                "key": concrete,
                "label": item.label,
                "area": item.area,
                "level": item.level,
                "type": item.type,
                "usage_status": item.usage_status,
                "hot_reload": item.hot_reload,
                "restart_required": item.restart_required,
                "hidden": item.hidden,
            }
            if item.sensitive:
                row["sensitive"] = True
                # the live environment wins over the boot-time config object:
                # a key written through the credentials API must show up here.
                live = env_layer.get(concrete, value)
                row.update(_mask(live))
            else:
                row["value"] = _jsonable(value)
                row["source"] = sources.get(concrete, "default")
            rows.append(row)
    return rows


def wildcard_match(concrete: str, pattern: str) -> bool:
    """``ai.models[0].name`` matches ``ai.models[i].name``; dict keys match ``<n>``."""
    if "<n>" not in pattern and "[i]" not in pattern:
        return concrete == pattern
    regex = re.escape(pattern)
    regex = regex.replace(re.escape("<n>"), r"[^.]+").replace(re.escape("[i]"), r"\[\d+\]")
    return bool(re.fullmatch(regex, concrete))


def usage_note(key: str) -> str:
    for prefix, (_flag, why) in _CONDITIONAL.items():
        if _matches(key, prefix):
            return why
    return ""


def hot_keys(keys: list[str]) -> list[str]:
    return [key for key in keys if (item := field_for(key)) is not None and item.hot_reload]


def restart_keys(keys: list[str]) -> list[str]:
    return [key for key in keys if (item := field_for(key)) is None or item.restart_required]
