"""Config admin service: edit the runtime configuration from the WebUI.

Everything the WebUI writes goes to ``config/overrides.yaml`` (never to the
operator's commented ``config.yaml``), is validated by constructing a real
:class:`AppConfig`, and is then hot-applied where the runtime supports it.
Anything that cannot be applied live is reported back as "needs restart".
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from app.ai.errors import AIError
from app.config.settings import (
    OVERRIDES_PATH,
    AppConfig,
    deep_merge,
    read_overrides,
    write_overrides,
)
from app.permissions.manager import PermissionManager

if TYPE_CHECKING:
    from app.core.bot import Bot

#: sections that need a process restart, with a human explanation
RESTART_FIELDS: dict[str, str] = {
    "onebot.host": "OneBot 监听地址（重启后生效）",
    "onebot.port": "OneBot 监听端口（重启后生效）",
    "onebot.path": "OneBot WebSocket 路径（重启后生效）",
    "database.url": "数据库地址（重启后生效）",
    "web.host": "WebUI 监听地址（重启后生效）",
    "web.port": "WebUI 监听端口（重启后生效）",
    "world.timezone": "世界时区（重启后生效）",
}

ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip() in ("1", "true", "True", "yes", "on")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _split_ids(raw: str) -> list[str]:
    parts = re.split(r"[,，、\s]+", str(raw or ""))
    return [part for part in (p.strip() for p in parts) if part.isdigit()]


def env_path() -> Path:
    return OVERRIDES_PATH.parent.parent / ".env"


class ConfigAdminService:
    def __init__(self, bot: Bot, *, overrides_path: Any = None) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Web.Config")
        #: last AI reload report, shown as a banner on the config page
        self.last_report: dict[str, Any] = {}
        #: override file to write (tests point this at a temp path)
        self.overrides_path = overrides_path or OVERRIDES_PATH

    @property
    def config(self) -> AppConfig:
        return self.bot.config

    # ------------------------------------------------------------------ read

    def overrides(self) -> dict[str, Any]:
        return read_overrides(self.overrides_path)

    def overrides_yaml(self) -> str:
        payload = self.overrides()
        if not payload:
            return ""
        return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)

    def snapshot(self) -> dict[str, Any]:
        """Effective config for the forms (secrets are only ever named, never shown)."""
        cfg = self.config
        return {
            "bot": cfg.bot.model_dump(),
            "onebot": cfg.onebot.model_dump(),
            "logging": cfg.logging.model_dump(),
            "permissions": cfg.permissions.model_dump(),
            "memory": cfg.memory.model_dump(),
            "web": {"username": cfg.web.username, "host": cfg.web.host, "port": cfg.web.port},
            "ai": {
                "enabled": cfg.ai.enabled,
                "default_temperature": cfg.ai.default_temperature,
                "timeout": cfg.ai.timeout,
                "cooldown": cfg.ai.cooldown.model_dump(),
                "providers": {
                    name: {
                        "type": p.type,
                        "base_url": p.base_url,
                        "api_key_env": p.api_key_env,
                        "has_key": bool(p.api_key_env and os.environ.get(p.api_key_env)),
                    }
                    for name, p in cfg.ai.providers.items()
                },
                "models": [
                    {
                        "name": m.name,
                        "provider": m.provider,
                        "model": m.model,
                        "enabled": m.enabled,
                        "live": name_live(cfg, m.name),
                    }
                    for m in cfg.ai.models
                ],
            },
            "restart_required": sorted(RESTART_FIELDS),
        }

    # ---------------------------------------------------------------- secrets

    def read_env_keys(self) -> list[str]:
        """Environment variable names present in ``.env`` (values never returned)."""
        path = env_path()
        if not path.exists():
            return []
        keys: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ENV_LINE.match(line)
            if match:
                keys.append(match.group(1))
        return keys

    def set_env_secret(self, name: str, value: str) -> bool:
        """Write ``NAME=value`` into ``.env`` and the live process environment."""
        name = (name or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("环境变量名不合法（只允许字母数字下划线，不能以数字开头）")
        if not value:
            return False
        path = env_path()
        lines: list[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
        replaced = False
        for index, line in enumerate(lines):
            match = ENV_LINE.match(line)
            if match and match.group(1) == name:
                lines[index] = f"{name}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{name}={value}")
        path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
        os.environ[name] = value
        self._log.info("[Config] .env updated: %s (value hidden)", name)
        return True

    # --------------------------------------------------------------- writing

    def _validate(self, payload: dict[str, Any]) -> AppConfig:
        """Merge onto the current config and validate (raises on bad input)."""
        merged = self.config.model_dump()
        deep_merge(merged, payload)
        return AppConfig.model_validate(merged)

    async def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist ``payload`` into overrides.yaml, then apply it live."""
        candidate = self._validate(payload)          # raises before touching disk
        stored = deep_merge(self.overrides(), payload)
        write_overrides(stored, self.overrides_path)
        notes = await self.apply(candidate)
        return {"notes": notes, "path": str(self.overrides_path)}

    async def reset(self) -> None:
        """Drop every WebUI override and reload from config.yaml."""
        if self.overrides_path.exists():
            self.overrides_path.unlink()
        from app.config.settings import load_config

        fresh = load_config(overrides_path=self.overrides_path)
        await self.apply(fresh)
        self._log.info("[Config] overrides cleared, config.yaml restored")

    async def save_raw(self, text: str) -> dict[str, Any]:
        """Power-user path: replace the whole overrides file (validated first)."""
        data = yaml.safe_load(text or "") or {}
        if not isinstance(data, dict):
            raise ValueError("YAML 顶层必须是键值映射")
        candidate = self._validate(data)
        write_overrides(data, self.overrides_path)
        notes = await self.apply(candidate)
        return {"notes": notes}

    # ----------------------------------------------------------- hot applying

    async def apply(self, config: AppConfig) -> list[str]:
        """Push a validated config into the live runtime; return restart notes."""
        before = self.config
        notes: list[str] = []
        self.last_report = {}
        self.bot.config = config

        await self._apply_ai(before, config, notes)
        self._apply_logging(before, config)
        self._apply_permissions(before, config)
        self._apply_memory(before, config)
        self._apply_behavior_settings(before, config)
        self._apply_character(before, config)
        for path, label in RESTART_FIELDS.items():
            if self._changed(before, config, path):
                notes.append(label)
        return notes

    @staticmethod
    def _changed(before: AppConfig, after: AppConfig, dotted: str) -> bool:
        def read(cfg: AppConfig, path: str) -> Any:
            node: Any = cfg
            for part in path.split("."):
                node = getattr(node, part, None)
            return node

        return read(before, dotted) != read(after, dotted)

    async def _apply_ai(self, before: AppConfig, after: AppConfig, notes: list[str]) -> None:
        if before.ai.model_dump() == after.ai.model_dump():
            return
        try:
            report = await self.bot.ai.reconfigure(after.ai)
            self.last_report = report
            self._log.info("[Config] AI reloaded: %s", report)
            if not report["enabled"] and after.ai.enabled:
                notes.append(
                    "AI 已启用但没有可用模型：请检查 Provider 的 API Key 环境变量是否已设置"
                )
        except Exception as exc:  # noqa: BLE001 - a bad model list must not break chat
            self._log.exception("[Config] AI reconfigure failed")
            notes.append(f"AI 配置应用失败：{exc}")

    def _apply_logging(self, before: AppConfig, after: AppConfig) -> None:
        if before.logging.model_dump() == after.logging.model_dump():
            return
        from app.utils.logger import setup_logging

        setup_logging(
            after.logging.level,
            after.logging.log_dir,
            color=after.logging.color,
            narrate=after.logging.narrate,
        )
        sandbox = getattr(self.bot, "sandbox", None)
        if sandbox is not None:
            sandbox.narrate_ticks = after.logging.narrate_world_ticks

    def _apply_permissions(self, before: AppConfig, after: AppConfig) -> None:
        if before.permissions.model_dump() == after.permissions.model_dump():
            return
        self.bot.permissions = PermissionManager(after.permissions)

    def _apply_memory(self, before: AppConfig, after: AppConfig) -> None:
        if before.memory.model_dump() == after.memory.model_dump():
            return
        manager = getattr(self.bot, "memory", None)
        if manager is not None:
            manager._config = after.memory  # noqa: SLF001 - documented hot swap
            retriever = getattr(manager, "retriever", None)
            if retriever is not None:
                retriever._config = after.memory.retrieval  # noqa: SLF001
                retriever.invalidate_cache()
        extractor = getattr(self.bot, "extractor", None)
        if extractor is not None:
            extractor.config = after.memory

    def _apply_behavior_settings(self, before: AppConfig, after: AppConfig) -> None:
        try:
            if before.behavior.model_dump() != after.behavior.model_dump():
                from app.web.services.behavior import BehaviorService

                BehaviorService(self.bot)._apply(after.behavior)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            self._log.exception("[Config] behaviour hot-apply failed")

    def _apply_character(self, before: AppConfig, after: AppConfig) -> None:
        if before.character.timezone == after.character.timezone:
            return
        from app.behavior.presence import PresenceResolver

        self.bot.presence = PresenceResolver(after.character.timezone, after.behavior.schedule)
        components = (
            getattr(self.bot, "behavior", None),
            getattr(self.bot, "reply_timing", None),
            getattr(self.bot, "character", None),
        )
        for component in components:
            if component is not None and hasattr(component, "presence"):
                component.presence = self.bot.presence

    # -------------------------------------------------------------- AI forms

    def ai_form(self, form: dict[str, Any]) -> dict[str, Any]:
        """Build the ``ai`` section from the provider/model editor form."""
        providers: dict[str, Any] = {}
        existing = self.config.ai.providers
        for index in _indexes(form, "p_name_"):
            name = str(form.get(f"p_name_{index}", "")).strip()
            if not name or _as_bool(form.get(f"p_del_{index}")):
                continue
            providers[name] = {
                "type": str(form.get(f"p_type_{index}", "openai_compatible")).strip()
                or "openai_compatible",
                "base_url": str(form.get(f"p_base_{index}", "")).strip(),
                "api_key_env": str(form.get(f"p_env_{index}", "")).strip(),
            }
        new_name = str(form.get("p_new_name", "")).strip()
        if new_name:
            providers[new_name] = {
                "type": str(form.get("p_new_type", "openai_compatible")).strip()
                or "openai_compatible",
                "base_url": str(form.get("p_new_base", "")).strip(),
                "api_key_env": str(form.get("p_new_env", "")).strip(),
            }
        if not providers:
            providers = {name: p.model_dump() for name, p in existing.items()}

        models: list[dict[str, Any]] = []
        for index in _indexes(form, "m_name_"):
            name = str(form.get(f"m_name_{index}", "")).strip()
            if not name or _as_bool(form.get(f"m_del_{index}")):
                continue
            models.append(
                {
                    "name": name,
                    "provider": str(form.get(f"m_provider_{index}", "")).strip(),
                    "model": str(form.get(f"m_model_{index}", "")).strip(),
                    "enabled": not _as_bool(form.get(f"m_off_{index}")),
                }
            )
        new_model = str(form.get("m_new_name", "")).strip()
        if new_model:
            models.append(
                {
                    "name": new_model,
                    "provider": str(form.get("m_new_provider", "")).strip(),
                    "model": str(form.get("m_new_model", "")).strip()
                    or new_model,
                    "enabled": True,
                }
            )
        if not models:
            models = [m.model_dump() for m in self.config.ai.models]

        return {
            "enabled": _as_bool(form.get("ai_enabled"), self.config.ai.enabled),
            "default_temperature": _as_float(
                form.get("ai_temperature"), self.config.ai.default_temperature
            ),
            "timeout": _as_float(form.get("ai_timeout"), self.config.ai.timeout),
            "cooldown": {
                "rate_limit_seconds": _as_float(
                    form.get("ai_rate_cooldown"), self.config.ai.cooldown.rate_limit_seconds
                ),
                "server_error_seconds": _as_float(
                    form.get("ai_server_cooldown"), self.config.ai.cooldown.server_error_seconds
                ),
            },
            "providers": providers,
            "models": models,
        }

    # ---------------------------------------------------------- other forms

    def basic_form(self, form: dict[str, Any]) -> dict[str, Any]:
        return {
            "bot": {
                "name": str(form.get("bot_name", self.config.bot.name)).strip()
                or self.config.bot.name,
                "debug": _as_bool(form.get("bot_debug"), self.config.bot.debug),
                "command_prefix": str(
                    form.get("command_prefix", self.config.bot.command_prefix)
                ).strip()
                or self.config.bot.command_prefix,
            },
            "logging": {
                "level": str(form.get("log_level", self.config.logging.level)).upper(),
                "log_dir": self.config.logging.log_dir,
                "color": _as_bool(form.get("log_color"), self.config.logging.color),
                "narrate": _as_bool(form.get("log_narrate"), self.config.logging.narrate),
                "narrate_world_ticks": _as_bool(
                    form.get("log_narrate_ticks"), self.config.logging.narrate_world_ticks
                ),
            },
            "permissions": {
                "superusers": _split_ids(str(form.get("superusers", "")))
                or list(self.config.permissions.superusers),
                "admins": _split_ids(str(form.get("admins", ""))),
            },
            "memory": {
                "enabled": _as_bool(form.get("memory_enabled"), self.config.memory.enabled),
                "extraction": {
                    "enabled": _as_bool(
                        form.get("memory_extraction"), self.config.memory.extraction.enabled
                    ),
                    "model": str(form.get("memory_extract_model", "") or "").strip(),
                    "timeout": _as_int(
                        form.get("memory_extract_timeout"),
                        int(self.config.memory.extraction.timeout),
                    ),
                },
            },
        }

    def onebot_form(self, form: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "onebot": {
                "host": str(form.get("onebot_host", self.config.onebot.host)).strip(),
                "port": _as_int(form.get("onebot_port"), self.config.onebot.port),
                "path": str(form.get("onebot_path", self.config.onebot.path)).strip(),
                "api_timeout": _as_float(
                    form.get("onebot_api_timeout"), self.config.onebot.api_timeout
                ),
            }
        }
        token = str(form.get("onebot_token", "")).strip()
        if token:
            env_name = "CATOOBOT_ONEBOT_ACCESS_TOKEN"
            self.set_env_secret(env_name, token)
            payload["onebot"]["access_token"] = ""
        return payload

    async def web_form(self, form: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "web": {
                "host": str(form.get("web_host", self.config.web.host)).strip(),
                "port": _as_int(form.get("web_port"), self.config.web.port),
            }
        }
        new_password = str(form.get("web_password", "")).strip()
        username = str(form.get("web_username", self.config.web.username)).strip()
        if new_password:
            if len(new_password) < 6:
                raise ValueError("新密码至少 6 位")
            await self.bot.web_auth.set_password(username or self.config.web.username, new_password)
            payload["web"]["username"] = username or self.config.web.username
        elif username and username != self.config.web.username:
            await self.bot.web_auth.rename_user(self.config.web.username, username)
            payload["web"]["username"] = username
        return payload

    # ----------------------------------------------------------- diagnostics

    async def test_provider(self, name: str) -> str:
        """Ping one provider through the router (uses the first model of it)."""
        router = self.bot.ai.router
        target = next(
            (state for state in router.states.values() if state.spec.provider == name), None
        )
        if target is None:
            return f"没有使用 provider「{name}」的模型"
        from app.ai.models import AIRequest, ChatMessage

        request = AIRequest(
            messages=[ChatMessage.user("ping")],
            model=target.spec.name,
            temperature=0.0,
            max_tokens=8,
        )
        started = time.perf_counter()
        try:
            response = await self.bot.ai.chat(request)
        except AIError as exc:
            return f"❌ {name} 测试失败：{exc}"
        elapsed = (time.perf_counter() - started) * 1000
        return f"✅ {name} 正常（{elapsed:.0f}ms，模型 {response.model or target.spec.model}）"


def name_live(config: AppConfig, name: str) -> bool:
    """Whether a configured model made it into the live router (provider had a key)."""
    provider = next((m.provider for m in config.ai.models if m.name == name), "")
    entry = config.ai.providers.get(provider)
    if entry is None:
        return False
    return bool(entry.api_key_env and os.environ.get(entry.api_key_env))


def _indexes(form: dict[str, Any], prefix: str) -> list[int]:
    found: list[int] = []
    for key in form:
        if key.startswith(prefix):
            suffix = key[len(prefix):]
            if suffix.isdigit():
                found.append(int(suffix))
    return sorted(found)
