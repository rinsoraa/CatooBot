"""AI 管理服务：Provider / 模型 / 角色 / 用量（WebUI v1.0 · W2 契约 §6）。

这个服务只做翻译与校验：读的是真实 ``bot.config.ai`` 与路由器快照，写的每
一笔都经过 :class:`ConfigAdminService`（overrides.yaml + 热应用），因此
WebUI 不会绕开配置系统，也不会出现"页面说改了、运行时没变"的假象。

Key 在这里只有两种形态：环境变量名（``api_key_env``）和"是否存在"；
任何响应、日志、异常消息都不带明文。
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import app.ai.providers  # noqa: F401 - importing registers built-in provider types
from app.ai.models import AIRequest, ChatMessage
from app.ai.provider import _PROVIDER_FACTORIES
from app.config.settings import load_config, write_overrides
from app.utils.logger import redact
from app.web import config_registry as registry
from app.web.api.common import bad_request, conflict, not_found

if TYPE_CHECKING:
    from app.core.bot import Bot
    from app.web.services.config_admin import ConfigAdminService

PROVIDER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: 角色 → 配置键；``chat`` 是合成的（= 模型顺序第一项），单独处理
ROLE_KEYS: dict[str, str] = {
    "vision": "media.vision_model",
    "decision": "sandbox.decision_model",
    "conversation": "sandbox.conversation_model",
    "extraction": "memory.extraction.model",
    "social": "social.decision_model",
    "planner": "agent.planner.model",
    "evaluator": "agent.evaluator.model",
    "embedding": "memory.semantic.embedding.model",
}
ROLE_ORDER: tuple[str, ...] = (
    "chat",
    "vision",
    "decision",
    "conversation",
    "extraction",
    "social",
    "planner",
    "evaluator",
    "embedding",
)
CHAT_KEY = "ai.models[0].name"

GROUP_BY: tuple[str, ...] = ("model", "provider", "purpose", "error_type")

_EMPTY_USAGE: dict[str, Any] = {
    "calls": 0,
    "failures": 0,
    "rate_limited": 0,
    "server_errors": 0,
    "tokens": 0,
    "avg_latency_ms": 0.0,
    "max_latency_ms": 0.0,
}


class AIAdminService:
    """Provider/模型/角色/用量的唯一 WebUI 入口。"""

    def __init__(self, bot: Bot, config_admin: ConfigAdminService) -> None:
        self.bot = bot
        self.config_admin = config_admin
        self._log = logging.getLogger("CatooBot.Web.AIAdmin")

    # ------------------------------------------------------------------ reads

    @property
    def _ai(self) -> Any:
        return self.bot.config.ai

    def providers(self) -> list[dict[str, Any]]:
        """Provider 列表（含 Key 状态；永远不返回 Key 本身）。"""
        using: dict[str, list[str]] = {}
        for model in self._ai.models:
            using.setdefault(model.provider, []).append(model.name)
        return [
            self._provider_item(name, models=using.get(name, [])) for name in self._ai.providers
        ]

    def _provider_item(self, name: str, *, models: list[str] | None = None) -> dict[str, Any]:
        entry = self._ai.providers[name]
        if models is None:
            models = [m.name for m in self._ai.models if m.provider == name]
        return {
            "name": name,
            "type": entry.type,
            "base_url": entry.base_url,
            "api_key_env": entry.api_key_env,
            "has_key": bool(entry.api_key_env and os.environ.get(entry.api_key_env)),
            "models": models,
            "restart_required": False,
        }

    async def models(self) -> list[dict[str, Any]]:
        """配置顺序 + 路由器实时状态 + 近 7 天用量。"""
        states = {row["name"]: row for row in self.bot.ai.router.snapshot()}
        usage = {row["key"]: row for row in await self._usage_rows(group_by="model")}
        pinned = self._pinned_roles()
        items: list[dict[str, Any]] = []
        for index, model in enumerate(self._ai.models):
            state = states.get(model.name, {})
            items.append(
                {
                    "name": model.name,
                    "provider": model.provider,
                    "model": model.model,
                    "enabled": model.enabled,
                    "order": index,
                    "roles": sorted(pinned.get(model.name, []), key=ROLE_ORDER.index),
                    "live": model.name in states,
                    "in_cooldown": bool(state.get("in_cooldown", False)),
                    "cooldown_until": float(state.get("cooldown_until") or 0.0),
                    "failure_count": int(state.get("failure_count") or 0),
                    "last_error": state.get("last_error"),
                    "usage": dict(usage.get(model.model, _EMPTY_USAGE)),
                }
            )
        return items

    def _pinned_roles(self) -> dict[str, list[str]]:
        pinned: dict[str, list[str]] = {}
        if self._ai.models:
            pinned.setdefault(self._ai.models[0].name, []).append("chat")
        for role, key in ROLE_KEYS.items():
            model = _read_key(self.bot.config, key)
            if model:
                pinned.setdefault(str(model), []).append(role)
        return pinned

    def roles(self) -> list[dict[str, Any]]:
        """九个角色的绑定现状（含来源层与重启标记）。"""
        sources = registry.source_map(self.config_admin.overrides_path)
        items: list[dict[str, Any]] = []
        for role in ROLE_ORDER:
            if role == "chat":
                key = CHAT_KEY
                model = self._ai.models[0].name if self._ai.models else ""
                restart = False
            else:
                key = ROLE_KEYS[role]
                model = str(_read_key(self.bot.config, key) or "")
                field = registry.field_for(key)
                restart = field.restart_required if field is not None else True
            items.append(
                {
                    "role": role,
                    "key": key,
                    "model": model,
                    "source": sources.get(key, "default"),
                    "restart_required": restart,
                }
            )
        return items

    # ----------------------------------------------------------------- writes

    async def upsert_provider(
        self, name: str, *, type: str, base_url: str, api_key_env: str = ""
    ) -> dict[str, Any]:
        """新增/修改 Provider（写入 overrides.yaml 并热重建 AIEngine）。"""
        name = (name or "").strip()
        if not PROVIDER_NAME_RE.fullmatch(name):
            raise bad_request(
                "Provider 名称只允许 1~32 位字母、数字、下划线或短横线", code="ai.invalid_name"
            )
        provider_type = (type or "openai_compatible").strip()
        if provider_type not in _PROVIDER_FACTORIES:
            known = "、".join(sorted(_PROVIDER_FACTORIES)) or "无"
            raise bad_request(
                f"未知的 Provider 类型：{provider_type}（当前支持：{known}）",
                code="ai.provider_unknown",
                field="type",
            )
        url = (base_url or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise bad_request(
                "接口地址必须是 http:// 或 https:// 开头的完整地址",
                code="config.invalid_value",
                field="base_url",
            )
        env_name = (api_key_env or "").strip()
        if env_name and not ENV_NAME_RE.fullmatch(env_name):
            raise bad_request(
                "API Key 环境变量名只允许字母、数字、下划线（不能以数字开头）",
                code="credential.invalid_name",
                field="api_key_env",
            )
        result = await self.config_admin.save(
            {
                "ai": {
                    "providers": {
                        name: {"type": provider_type, "base_url": url, "api_key_env": env_name}
                    }
                }
            }
        )
        self._log.info("[AIAdmin] provider '%s' upserted (%s)", name, provider_type)
        return {**self._provider_item(name), "notes": list(result.get("notes", []))}

    async def delete_provider(self, name: str, *, force: bool = False) -> dict[str, Any]:
        """删除 Provider；被模型引用时需 force，连带删除其模型。

        只有 WebUI 自己写入过（overrides.yaml）的 Provider 才能删；来自
        config.yaml / ``models:`` 段的定义返回 ``config.base_defined``。
        """
        if name not in self._ai.providers:
            raise not_found(f"Provider「{name}」不存在", code="ai.provider_unknown")
        used = [m.name for m in self._ai.models if m.provider == name]
        if used and not force:
            raise conflict(
                f"Provider「{name}」仍被 {len(used)} 个模型使用：{'、'.join(used)}；"
                "请先删除这些模型，或带 force=1 连带删除",
                code="ai.provider_in_use",
                detail={"models": used},
            )
        overrides = self.config_admin.overrides()
        section = overrides.get("ai")
        over_providers = section.get("providers") if isinstance(section, dict) else None
        if not isinstance(over_providers, dict) or name not in over_providers:
            raise conflict(
                f"Provider「{name}」来自 config/config.yaml（或 models 总表），"
                "WebUI 只能删除自己写入的覆盖项；请直接编辑 config.yaml 后重启",
                code="config.base_defined",
                detail={"name": name},
            )
        over_providers.pop(name)
        models_removed = 0
        if used and isinstance(section, dict):
            remaining = [m.model_dump() for m in self._ai.models if m.provider != name]
            models_removed = len(self._ai.models) - len(remaining)
            section["models"] = remaining
        write_overrides(overrides, self.config_admin.overrides_path)
        fresh = load_config(overrides_path=self.config_admin.overrides_path)
        await self.config_admin.apply(fresh)
        self._log.info("[AIAdmin] provider '%s' deleted (models removed: %d)", name, models_removed)
        return {"deleted": True, "models_removed": models_removed}

    async def create_model(
        self, name: str, *, provider: str, model: str, enabled: bool = True
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not MODEL_NAME_RE.fullmatch(name):
            raise bad_request(
                "模型别名只允许字母、数字、下划线、点或短横线（不超过 64 位）",
                code="ai.invalid_name",
            )
        if any(m.name == name for m in self._ai.models):
            raise conflict(f"模型别名「{name}」已存在", code="ai.model_conflict")
        provider = (provider or "").strip()
        if provider not in self._ai.providers:
            raise bad_request(
                f"Provider「{provider}」不存在", code="ai.provider_unknown", field="provider"
            )
        model_id = (model or "").strip()
        if not model_id:
            raise bad_request("必须填写服务商提供的模型 ID", field="model")
        rows = [m.model_dump() for m in self._ai.models]
        rows.append(
            {"name": name, "provider": provider, "model": model_id, "enabled": bool(enabled)}
        )
        notes = await self._write_models(rows)
        return {**self._model_item(name), "notes": notes}

    async def update_model(
        self,
        name: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        target = next((m for m in self._ai.models if m.name == name), None)
        if target is None:
            raise not_found(f"模型「{name}」不存在", code="ai.model_unknown")
        if provider is not None and provider.strip() not in self._ai.providers:
            raise bad_request(
                f"Provider「{provider}」不存在", code="ai.provider_unknown", field="provider"
            )
        if model is not None and not model.strip():
            raise bad_request("模型 ID 不能为空", field="model")
        if provider is None and model is None and enabled is None:
            raise bad_request("没有需要修改的字段（provider/model/enabled）")
        rows: list[dict[str, Any]] = []
        for item in self._ai.models:
            row = item.model_dump()
            if item.name == name:
                if provider is not None:
                    row["provider"] = provider.strip()
                if model is not None:
                    row["model"] = model.strip()
                if enabled is not None:
                    row["enabled"] = bool(enabled)
            rows.append(row)
        notes = await self._write_models(rows)
        return {**self._model_item(name), "notes": notes}

    async def delete_model(self, name: str, *, force: bool = False) -> dict[str, Any]:
        if not any(m.name == name for m in self._ai.models):
            raise not_found(f"模型「{name}」不存在", code="ai.model_unknown")
        roles = list(self._pinned_roles().get(name, []))
        if roles and not force:
            raise conflict(
                f"模型「{name}」仍被角色使用：{'、'.join(roles)}；"
                "请先改绑角色，或带 force=1 强制删除",
                code="ai.model_in_use",
                detail={"roles": roles},
            )
        rows = [m.model_dump() for m in self._ai.models if m.name != name]
        await self._write_models(rows)
        return {"deleted": True, "roles_cleared": roles}

    async def reorder(self, names: list[str]) -> list[str]:
        current = [m.name for m in self._ai.models]
        if len(names) != len(current) or set(names) != set(current):
            raise bad_request(
                "order 必须是当前全部模型别名的一个排列（不增不减、不重复）",
                code="ai.invalid_order",
                field="order",
            )
        by_name = {m.name: m.model_dump() for m in self._ai.models}
        await self._write_models([by_name[name] for name in names])
        self._log.info("[AIAdmin] model order updated: %s", "、".join(names))
        return names

    async def set_role(self, role: str, model: str) -> dict[str, Any]:
        """绑定角色模型；``chat`` 通过重排实现（默认模型 = 顺序第一项）。"""
        model = (model or "").strip()
        names = [m.name for m in self._ai.models]
        if model and model not in names:
            raise not_found(f"模型「{model}」不存在", code="ai.model_unknown")
        if role == "chat":
            if not model:
                raise bad_request(
                    "chat 角色不能为空：默认模型必须是一个已有模型",
                    code="ai.invalid_value",
                    field="model",
                )
            index = names.index(model)
            notes: list[str] = []
            if index != 0:
                rows = [m.model_dump() for m in self._ai.models]
                rows.insert(0, rows.pop(index))
                notes = await self._write_models(rows)
            return {
                "role": "chat",
                "key": CHAT_KEY,
                "model": model,
                "source": "overrides" if index != 0 else "model_order",
                "restart_required": False,
                "notes": notes,
            }
        key = ROLE_KEYS.get(role)
        if key is None:
            raise not_found(
                f"未知角色「{role}」（可用：{'、'.join(ROLE_ORDER)}）", code="ai.role_unknown"
            )
        field = registry.field_for(key)
        restart = field.restart_required if field is not None else True
        result = await self.config_admin.save(_nest(key, model))
        self._log.info("[AIAdmin] role '%s' → %r (%s)", role, model or "未设置", key)
        return {
            "role": role,
            "key": key,
            "model": model,
            "source": "overrides",
            "restart_required": restart,
            "notes": list(result.get("notes", [])),
        }

    async def test_model(self, name: str, *, prompt: str = "ping") -> dict[str, Any]:
        """走完整路由器实测一个模型；失败也返回结果（不抛 5xx）。"""
        if not any(m.name == name for m in self._ai.models):
            raise not_found(f"模型「{name}」不存在", code="ai.model_unknown")
        request = AIRequest(
            messages=[ChatMessage.user(prompt or "ping")],
            model=name,
            temperature=0.0,
            max_tokens=8,
        )
        started = time.perf_counter()
        try:
            response = await self.bot.ai.router.chat(request)
        except Exception as exc:  # noqa: BLE001 - the test's whole job is to report failures
            return {
                "ok": False,
                "latency_ms": _elapsed_ms(started),
                "model": name,
                "error_type": type(exc).__name__,
                "message": redact(str(exc)),
            }
        return {
            "ok": True,
            "latency_ms": _elapsed_ms(started),
            "model": name,
            "error_type": "",
            "message": redact(response.content)[:200],
        }

    async def usage(self, *, days: int = 7, group_by: str = "model") -> list[dict[str, Any]]:
        if group_by not in GROUP_BY:
            raise bad_request(
                f"group_by 只支持：{'、'.join(GROUP_BY)}", code="ai.invalid_value", field="group_by"
            )
        return await self._usage_rows(days=days, group_by=group_by)

    async def reset_router(self) -> dict[str, Any]:
        """重建路由器（清空内存中的冷却/失败计数；不改配置）。"""
        await self.bot.ai.reconfigure(self.bot.config.ai)
        return {"reset": True}

    # --------------------------------------------------------------- internals

    async def _write_models(self, rows: list[dict[str, Any]]) -> list[str]:
        result = await self.config_admin.save({"ai": {"models": rows}})
        return list(result.get("notes", []))

    def _model_item(self, name: str) -> dict[str, Any]:
        for item in self.bot.config.ai.models:
            if item.name == name:
                return {
                    "name": item.name,
                    "provider": item.provider,
                    "model": item.model,
                    "enabled": item.enabled,
                }
        raise not_found(f"模型「{name}」不存在", code="ai.model_unknown")

    async def _usage_rows(self, *, days: int = 7, group_by: str = "model") -> list[dict[str, Any]]:
        recorder = getattr(self.bot, "ai_usage", None)
        if recorder is None:
            return []
        return await recorder.summary_by(days=days, group_by=group_by)


def _read_key(config: Any, dotted: str) -> Any:
    node = config
    for part in dotted.split("."):
        if node is None:
            return None
        node = getattr(node, part, None)
    return node


def _nest(dotted: str, value: Any) -> dict[str, Any]:
    payload: Any = value
    for part in reversed(dotted.split(".")):
        payload = {part: payload}
    return payload


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["AIAdminService", "ROLE_KEYS", "ROLE_ORDER", "GROUP_BY"]
