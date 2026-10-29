"""`/api/v1/config/*`：注册表驱动的配置中心（WebUI v1.0 · W2 §11-§21）。

三条读取路径 + 三条写入路径，全部落在既有配置系统上：

* ``GET  /config/schema``     字段元数据（供渲染表单）
* ``GET  /config/effective``  当前真正生效的值 + 来源层
* ``GET  /config/restart-pending`` 已保存但还没重启的键
* ``POST /config/validate``   只校验、不落盘（预览）
* ``PATCH /config``           校验 → 原子写 overrides → 热应用 → 返回逐键结果
* ``POST /config/reset`` / ``GET|PUT /config/raw``  高级通道

“假热更新”在这里是被禁止的：``restart_required`` 的键保存成功也只报告
``effective=false``，绝不假装运行时已经改变。
"""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

from app.web import config_registry as registry
from app.web.api.common import (
    bad_request,
    conflict,
    nested,
    ok,
    read_json,
    unprocessable,
)
from app.web.routes.base import WebContext


class ConfigApiRoutes(WebContext):
    """Registry-driven configuration API."""

    #: restart-required keys saved this process lifetime (cleared by a real restart)
    _pending_restart: dict[str, float] | None = None

    def _pending(self) -> dict[str, float]:
        if self._pending_restart is None:
            self._pending_restart = {}
        return self._pending_restart

    # ------------------------------------------------------------------ reads

    async def _v1_config_schema(self, request: web.Request) -> web.Response:
        include_unused = request.query.get("include", "") == "unused" or request.query.get(
            "include_unused", ""
        ).lower() in ("1", "true", "yes")
        rows = registry.schema(
            area=request.query.get("area", ""),
            level=request.query.get("level", ""),
            include_unused=include_unused,
        )
        areas = sorted({row["area"] for row in registry.schema(include_unused=True)})
        return ok(
            {
                "items": rows,
                "areas": areas,
                "levels": [registry.BASIC, registry.ADVANCED, registry.EXPERT],
                "usage_status": [
                    registry.ACTIVE,
                    registry.ACTIVE_WITH_RESTART,
                    registry.CONDITIONALLY_USED,
                    registry.DEFINED_BUT_UNUSED,
                    registry.LEGACY,
                ],
                "legends": {
                    "source": {
                        "env": "环境变量 / .env",
                        "overrides": "WebUI 覆盖（overrides.yaml）",
                        "models": "config.yaml 的 models 段展开",
                        "yaml": "config.yaml",
                        "default": "内置默认值",
                    },
                    "usage_status": {
                        registry.ACTIVE: "运行期会读取",
                        registry.ACTIVE_WITH_RESTART: "仅启动时读取",
                        registry.CONDITIONALLY_USED: "仅在开关打开时读取",
                        registry.DEFINED_BUT_UNUSED: "当前版本没有读取方",
                        registry.LEGACY: "旧语义，已废弃",
                    },
                },
            },
            request=request,
        )

    async def _v1_config_effective(self, request: web.Request) -> web.Response:
        include_unused = request.query.get("include", "") == "unused" or request.query.get(
            "include_unused", ""
        ).lower() in ("1", "true", "yes")
        keys = [part for part in request.query.get("keys", "").split(",") if part]
        rows = registry.effective(
            self._bot.config,
            area=request.query.get("area", ""),
            level=request.query.get("level", ""),
            include_unused=include_unused,
            keys=keys or None,
            overrides_path=self._config_admin.overrides_path,
        )
        return ok({"items": rows, "count": len(rows)}, request=request)

    async def _v1_config_restart_pending(self, request: web.Request) -> web.Response:
        pending = self._pending()
        return ok(
            {
                "pending": sorted(pending),
                "since": min(pending.values()) if pending else None,
            },
            request=request,
        )

    # ----------------------------------------------------------------- writes

    def _check_keys(self, values: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        wildcards = [
            field for field in registry.fields() if "<n>" in field.key or "[i]" in field.key
        ]
        for key in values:
            if "<n>" in key or "[i]" in key:
                raise bad_request("写入时请使用具体的键（不要通配符）", field=key)
            item = registry.field_for(key)
            if item is None:  # a concrete instance of a wildcard field (ai.models[0].name …)
                item = next(
                    (field for field in wildcards if registry.wildcard_match(key, field.key)), None
                )
            if item is None:
                raise bad_request(f"未知配置键：{key}", code="config.unknown_key", field=key)
            if item.sensitive:
                raise bad_request(
                    f"{key} 是敏感项，请通过凭据接口写入（不会进入 config.yaml）",
                    code="config.readonly_key",
                    field=key,
                )
            keys.append(key)
        return keys

    async def _v1_config_validate(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        values = body.get("values")
        if not isinstance(values, dict) or not values:
            raise bad_request("values 必须是非空的键值映射", field="values")
        keys = self._check_keys(values)
        payload = nested(values)
        try:
            self._config_admin._validate(payload)  # noqa: SLF001 - the service's own gate
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            raise unprocessable(f"校验失败：{exc}", detail=str(exc)) from exc
        hot = registry.hot_keys(keys)
        restart = registry.restart_keys(keys)
        return ok(
            {
                "valid": True,
                "changes": values,
                "hot_reload": hot,
                "restart_required": restart,
                "notes": [registry.usage_note(key) for key in keys if registry.usage_note(key)],
            },
            request=request,
        )

    async def _v1_config_patch(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        values = body.get("values")
        if not isinstance(values, dict) or not values:
            raise bad_request("values 必须是非空的键值映射", field="values")
        keys = self._check_keys(values)
        payload = nested(values)
        try:
            result = await self._config_admin.save(payload)
        except ValueError as exc:
            raise unprocessable(str(exc)) from exc
        hot = registry.hot_keys(keys)
        restart = registry.restart_keys(keys)
        now = time.time()
        for key in restart:
            self._pending()[key] = now
        effective_rows = registry.effective(
            self._bot.config, keys=keys, overrides_path=self._config_admin.overrides_path
        )
        applied = set(hot)  # a restart-required key is saved, never "live"
        warnings: list[dict[str, Any]] = []
        if restart:
            warnings.append(
                {
                    "code": "config.restart_required",
                    "message": "以下配置已保存，需要重启 CatooBot 才会生效",
                    "keys": restart,
                }
            )
        return ok(
            {
                "saved": keys,
                "hot_reload": hot,
                "restart_required": restart,
                "effective": [key in applied for key in keys],
                "values": effective_rows,
                "notes": result.get("notes", []),
                "warnings": warnings,
            },
            request=request,
        )

    async def _v1_config_reset(self, request: web.Request) -> web.Response:
        body = await read_json(request, required=False)
        if body.get("confirm") != "reset":
            raise conflict(
                "重置会清空所有 WebUI 覆盖，请带 confirm=reset", code="config.confirm_required"
            )
        await self._config_admin.reset()
        self._pending().clear()
        return ok({"reset": True}, request=request)

    async def _v1_config_raw_get(self, request: web.Request) -> web.Response:
        return ok(
            {
                "yaml": self._config_admin.overrides_yaml(),
                "path": str(self._config_admin.overrides_path),
            },
            request=request,
        )

    async def _v1_config_raw_put(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        text = body.get("yaml")
        if not isinstance(text, str):
            raise bad_request("yaml 必须是字符串", field="yaml")
        if body.get("confirm") != "raw":
            raise conflict("保存原始 YAML 需要 confirm=raw", code="config.confirm_required")
        result = await self._config_admin.save_raw(text)
        return ok({"saved": True, "notes": result.get("notes", [])}, request=request)
