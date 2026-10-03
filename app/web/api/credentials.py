"""`/api/v1/credentials/*`：凭据总览与写入（WebUI v1.0 · W2 契约 §5）。

五类凭据各有真实归宿，互不混用：

* ``ai`` / ``embedding``：``.env``（原子写，保留既有行）+ 进程环境变量 + 立即重建 AIEngine；
* ``onebot``：同一个 ``.env``，但适配器启动时已捕获，返回 ``restart_required: true``；
* ``web``：PBKDF2 哈希进 SQLite（``AuthService.set_password``），不落盘明文；
* ``tool``：``data/secrets.json``（``CredentialManager``）。

任何响应只给 masked 形态；明文不进日志、不进错误消息、不进 overrides.yaml。
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from aiohttp import web

from app.ai.models import AIRequest, ChatMessage
from app.ai.provider import create_provider
from app.config import env_store
from app.tools.credentials import MASK, CredentialManager
from app.utils.logger import redact
from app.web.api.common import (
    API_PREFIX,
    bad_request,
    conflict,
    json_endpoint,
    not_found,
    ok,
    read_json,
)
from app.web.routes.base import WebContext

log = logging.getLogger("CatooBot.Web.Credentials")

DOMAINS: tuple[str, ...] = ("ai", "embedding", "onebot", "web", "tool")
_ENV_DOMAINS = ("ai", "embedding")
ONEBOT_TOKEN_ENV = "CATOOBOT_ONEBOT_ACCESS_TOKEN"
ENV_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
TOOL_REF_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class CredentialApiRoutes(WebContext):
    """凭据区的 v1 端点。"""

    def _register_v1_credentials(self, app: web.Application) -> None:
        wrap = json_endpoint
        prefix = f"{API_PREFIX}/credentials"
        app.router.add_get(prefix, wrap(self._v1_credentials_list))
        app.router.add_put(f"{prefix}/{{domain}}/{{ref}}", wrap(self._v1_credential_put))
        app.router.add_delete(f"{prefix}/{{domain}}/{{ref}}", wrap(self._v1_credential_delete))
        app.router.add_post(f"{prefix}/test", wrap(self._v1_credential_test))

    # ------------------------------------------------------------------- reads

    async def _v1_credentials_list(self, request: web.Request) -> web.Response:
        file_values = self._env_file()
        items = self._ai_items(file_values)
        items += self._embedding_items(file_values)
        items += self._onebot_items(file_values)
        items += await self._web_items()
        known = {str(item["ref"]) for item in items}
        items += self._tool_items(known)
        return ok({"items": items, "domains": list(DOMAINS)}, request=request)

    def _env_file(self) -> dict[str, str]:
        try:
            return env_store.read_env()
        except Exception:  # noqa: BLE001 - a broken .env must not break the overview
            log.warning("[Credentials] .env unreadable; reporting environment only")
            return {}

    def _env_item(self, domain: str, ref: str, file_values: dict[str, str]) -> dict[str, Any]:
        live = os.environ.get(ref, "")
        value = live or file_values.get(ref, "")
        source = "env" if live else ("dotenv" if ref in file_values else "unset")
        return {
            "domain": domain,
            "ref": ref,
            "masked": CredentialManager.mask(value),
            "configured": bool(value),
            "source": source,
        }

    def _ai_items(self, file_values: dict[str, str]) -> list[dict[str, Any]]:
        seen: list[str] = []
        for provider in self._bot.config.ai.providers.values():
            env_name = str(getattr(provider, "api_key_env", "") or "")
            if env_name and env_name not in seen:
                seen.append(env_name)
        return [self._env_item("ai", name, file_values) for name in seen]

    def _embedding_items(self, file_values: dict[str, str]) -> list[dict[str, Any]]:
        embedding = getattr(self._bot.config.memory.semantic, "embedding", None)
        env_name = str(getattr(embedding, "api_key_env", "") or "")
        return [self._env_item("embedding", env_name, file_values)] if env_name else []

    def _onebot_items(self, file_values: dict[str, str]) -> list[dict[str, Any]]:
        return [self._env_item("onebot", ONEBOT_TOKEN_ENV, file_values)]

    async def _web_items(self) -> list[dict[str, Any]]:
        username = str(getattr(self._config, "username", "") or "admin")
        configured = False
        database = getattr(self._bot, "database", None)
        if database is not None:
            try:
                row = await database.fetchone(
                    "SELECT username FROM web_users WHERE username = ?", (username,)
                )
                configured = row is not None
            except Exception:  # noqa: BLE001 - table may not exist yet
                configured = False
        return [
            {
                "domain": "web",
                "ref": username,
                "masked": MASK if configured else "",
                "configured": configured,
                "source": "database" if configured else "unset",
            }
        ]

    def _tool_items(self, known: set[str]) -> list[dict[str, Any]]:
        manager = self._credential_manager()
        items: list[dict[str, Any]] = []
        for name in manager.names():
            if name in known:  # already listed under ai/embedding/onebot
                continue
            value = manager.get_secret(name)
            items.append(
                {
                    "domain": "tool",
                    "ref": name,
                    "masked": manager.mask(value),
                    "configured": bool(value),
                    "source": manager.source_of(name),
                }
            )
        return items

    def _credential_manager(self) -> CredentialManager:
        tools = getattr(self._bot, "tools", None)
        manager = getattr(tools, "credentials", None)
        if isinstance(manager, CredentialManager):
            return manager
        return CredentialManager()

    # ------------------------------------------------------------------ writes

    async def _v1_credential_put(self, request: web.Request) -> web.Response:
        domain = request.match_info["domain"]
        ref = request.match_info["ref"]
        self._check_target(domain, ref)
        body = await read_json(request)
        value = body.get("value")
        if not isinstance(value, str):
            raise bad_request("value 必须是字符串（空字符串表示删除）", field="value")
        result = await self._write(domain, ref, value)
        return ok(result, request=request)

    async def _write(self, domain: str, ref: str, value: str) -> dict[str, Any]:
        if domain in _ENV_DOMAINS:
            return await self._write_env(domain, ref, value, reconfigure=True)
        if domain == "onebot":
            if ref != ONEBOT_TOKEN_ENV:
                raise bad_request(
                    f"OneBot 令牌的环境变量名固定为 {ONEBOT_TOKEN_ENV}",
                    code="credential.invalid_name",
                )
            return await self._write_env(domain, ref, value, reconfigure=False)
        if domain == "web":
            if not value:
                raise bad_request(
                    "Web 登录密码不能为空；要轮换请直接填写新密码", code="credential.write_failed"
                )
            if len(value) < 6:
                raise bad_request("Web 登录密码至少 6 位", code="credential.write_failed")
            if not await self._auth.set_password(ref, value):
                raise not_found(f"Web 用户「{ref}」不存在", code="credential.missing")
            log.info("[Credentials] web password updated for '%s' (hash only)", ref)
            return {
                "saved": True,
                "domain": domain,
                "ref": ref,
                "masked": CredentialManager.mask(value),
                "configured": True,
                "deleted": False,
                "restart_required": False,
                "source": "database",
            }
        manager = self._credential_manager()
        if value:
            manager.set_secret(ref, value)
        elif not manager.delete_secret(ref):
            raise not_found(f"工具凭据「{ref}」不存在", code="credential.missing")
        return {
            "saved": True,
            "domain": domain,
            "ref": ref,
            "masked": manager.mask(value),
            "configured": bool(value),
            "deleted": not value,
            "restart_required": False,
            "source": manager.source_of(ref),
        }

    async def _write_env(
        self, domain: str, ref: str, value: str, *, reconfigure: bool
    ) -> dict[str, Any]:
        if value:
            env_store.write_env_secret(ref, value)
            os.environ[ref] = value
        else:
            env_store.remove_env_secret(ref)
            os.environ.pop(ref, None)
        if reconfigure:
            await self._bot.ai.reconfigure(self._bot.config.ai)
        log.info("[Credentials] %s/%s updated (value hidden)", domain, ref)
        return {
            "saved": True,
            "domain": domain,
            "ref": ref,
            "masked": CredentialManager.mask(value),
            "configured": bool(value),
            "deleted": not value,
            "restart_required": domain == "onebot",
            "source": "env" if value else "unset",
        }

    async def _v1_credential_delete(self, request: web.Request) -> web.Response:
        domain = request.match_info["domain"]
        ref = request.match_info["ref"]
        self._check_target(domain, ref)
        force = request.query.get("force", "").lower() in ("1", "true", "yes")
        if domain in _ENV_DOMAINS:
            owners = [
                name
                for name, provider in self._bot.config.ai.providers.items()
                if provider.api_key_env == ref
            ]
            if owners and not force:
                raise conflict(
                    f"环境变量 {ref} 仍被 Provider 使用：{'、'.join(owners)}；"
                    "确认不再使用后带 force=1 删除",
                    code="credential.in_use",
                    detail={"providers": owners},
                )
            existed = os.environ.pop(ref, None) is not None
            removed = env_store.remove_env_secret(ref)
            if not existed and not removed:
                raise not_found(f"凭据「{ref}」未配置", code="credential.missing")
            await self._bot.ai.reconfigure(self._bot.config.ai)
            log.info("[Credentials] %s/%s deleted", domain, ref)
            return ok(
                {"deleted": True, "domain": domain, "ref": ref, "restart_required": False},
                request=request,
            )
        if domain == "onebot":
            if ref != ONEBOT_TOKEN_ENV:
                raise bad_request(
                    f"OneBot 令牌的环境变量名固定为 {ONEBOT_TOKEN_ENV}",
                    code="credential.invalid_name",
                )
            existed = os.environ.pop(ref, None) is not None
            removed = env_store.remove_env_secret(ref)
            if not existed and not removed:
                raise not_found(f"凭据「{ref}」未配置", code="credential.missing")
            return ok(
                {"deleted": True, "domain": domain, "ref": ref, "restart_required": True},
                request=request,
            )
        if domain == "web":
            raise conflict(
                "Web 登录密码不能删除（否则将无法登录）；请直接设置新密码",
                code="credential.delete_forbidden",
            )
        if not self._credential_manager().delete_secret(ref):
            raise not_found(f"工具凭据「{ref}」不存在", code="credential.missing")
        return ok(
            {"deleted": True, "domain": domain, "ref": ref, "restart_required": False},
            request=request,
        )

    # --------------------------------------------------------------------- test

    async def _v1_credential_test(self, request: web.Request) -> web.Response:
        """用给定 Provider 现测一次；``value`` 缺省用已存 Key，且绝不落盘。"""
        body = await read_json(request)
        provider_name = str(body.get("provider", "")).strip()
        if not provider_name:
            raise bad_request("必须指定要测试的 provider", field="provider")
        config = self._bot.config.ai
        entry = config.providers.get(provider_name)
        if entry is None:
            raise not_found(f"Provider「{provider_name}」不存在", code="ai.provider_unknown")
        model_ref = str(body.get("model") or "").strip()
        if model_ref:
            configured = next((m for m in config.models if m.name == model_ref), None)
            model_id = configured.model if configured is not None else model_ref
        else:
            first = next((m for m in config.models if m.provider == provider_name), None)
            model_id = first.model if first is not None else ""
        if not model_id:
            raise bad_request(
                "没有可用的模型：请先配置模型或显式传入 model",
                code="ai.model_unknown",
                field="model",
            )
        raw_value = body.get("value")
        api_key = raw_value if isinstance(raw_value, str) and raw_value else ""
        if not api_key and entry.api_key_env:
            api_key = os.environ.get(entry.api_key_env, "")
        if not api_key:
            raise bad_request(
                "没有可用的 API Key：请填写 value，或先保存 Provider 的 Key",
                code="ai.no_key",
                field="value",
            )
        try:
            provider = create_provider(
                entry.type,
                name=provider_name,
                base_url=entry.base_url,
                api_key=api_key,
                timeout=config.timeout,
            )
        except ValueError as exc:
            raise bad_request(redact(str(exc)), code="ai.provider_unknown") from exc
        request_obj = AIRequest(
            messages=[ChatMessage.user("ping")],
            model=model_id,
            temperature=0.0,
            max_tokens=8,
        )
        started = time.perf_counter()
        try:
            response = await provider.chat(request_obj)
        except Exception as exc:  # noqa: BLE001 - the endpoint's job is to report the failure
            result: dict[str, Any] = {
                "ok": False,
                "provider": provider_name,
                "model": model_id,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "error_type": type(exc).__name__,
                "message": redact(str(exc)),
                "reply": "",
                "http_status": _status_for(exc),
                "http_status_source": _status_source(exc),
            }
        else:
            result = {
                "ok": True,
                "provider": provider_name,
                "model": model_id,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "error_type": "",
                "message": "",
                "reply": redact(response.content)[:200],
                "http_status": 200,
                "http_status_source": "upstream",
            }
        finally:
            await provider.close()
        return ok(result, request=request)

    # ---------------------------------------------------------------- internals

    def _check_target(self, domain: str, ref: str) -> None:
        if domain not in DOMAINS:
            raise bad_request(
                f"未知凭据域「{domain}」（可用：{'、'.join(DOMAINS)}）",
                code="credential.invalid_domain",
                field="domain",
            )
        pattern = TOOL_REF_RE if domain == "tool" else ENV_REF_RE
        if not pattern.fullmatch(ref or ""):
            raise bad_request(
                f"非法的凭据名「{ref}」：只允许字母、数字、下划线等受限字符集",
                code="credential.invalid_name",
                field="ref",
            )


def _status_for(exc: BaseException) -> int | None:
    from app.web.services.ai_admin import http_status_for

    return http_status_for(exc)[0]


def _status_source(exc: BaseException) -> str:
    from app.web.services.ai_admin import http_status_for

    return http_status_for(exc)[1]
