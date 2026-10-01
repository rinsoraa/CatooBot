"""Configuration editing pages (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from aiohttp import web

from app.web.pages import config_page
from app.web.routes.base import WebContext, layout


class ConfigRoutes(WebContext):
    """Handlers for this domain."""

    async def _config_page(self, request: web.Request) -> web.Response:
        result = request.query.get("result", "")
        kind = "warn" if result.startswith("!") else "ok"
        message = result.lstrip("!")
        report = self._config_admin.last_report or None
        body = config_page.render(
            self._config_admin,
            tab=request.query.get("tab", "basic"),
            msg=message,
            kind=kind,
            engine_report=report,
        )
        return web.Response(
            text=layout("配置", "/config", body, subtitle="在浏览器里改配置，保存即生效"),
            content_type="text/html",
        )

    async def _config_save(
        self, request: web.Request, payload: dict[str, Any], tab: str
    ) -> web.Response:
        try:
            outcome = await self._config_admin.save(payload)
        except Exception as exc:  # noqa: BLE001 - validation errors are user-facing
            raise web.HTTPFound(f"/config?tab={tab}&result={quote('!' + str(exc)[:300])}") from exc
        notes = outcome.get("notes") or []
        message = "已保存并生效" if not notes else "已保存 · " + "；".join(notes)
        raise web.HTTPFound(f"/config?tab={tab}&result={quote(message)}")

    async def _config_basic_save(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        return await self._config_save(request, self._config_admin.basic_form(form), "basic")

    async def _config_ai_save(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        return await self._config_save(request, {"ai": self._config_admin.ai_form(form)}, "ai")

    async def _config_onebot_save(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        return await self._config_save(request, self._config_admin.onebot_form(form), "onebot")

    async def _config_web_save(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        try:
            payload = await self._config_admin.web_form(form)
        except Exception as exc:  # noqa: BLE001
            raise web.HTTPFound(f"/config?tab=web&result={quote('!' + str(exc)[:300])}") from exc
        return await self._config_save(request, payload, "web")

    async def _config_raw_save(self, request: web.Request) -> web.Response:
        form = await request.post()
        try:
            await self._config_admin.save_raw(str(form.get("raw", "")))
        except Exception as exc:  # noqa: BLE001
            raise web.HTTPFound(f"/config?tab=raw&result={quote('!' + str(exc)[:300])}") from exc
        raise web.HTTPFound(f"/config?tab=raw&result={quote('覆盖配置已保存')}")

    async def _config_reset(self, request: web.Request) -> web.Response:
        await self._config_admin.reset()
        raise web.HTTPFound(f"/config?result={quote('已清空覆盖，恢复 config.yaml 原样')}")

    async def _config_test_provider(self, request: web.Request) -> web.Response:
        form = await request.post()
        name = str(form.get("name", ""))
        message = await self._config_admin.test_provider(name)
        prefix = "" if message.startswith("✅") else "!"
        raise web.HTTPFound(f"/config?tab=ai&result={quote(prefix + message)}")

    def register_config(self, app: web.Application) -> None:
        app.router.add_get("/config", self._config_page)
        app.router.add_post("/config/basic", self._config_basic_save)
        app.router.add_post("/config/ai", self._config_ai_save)
        app.router.add_post("/config/onebot", self._config_onebot_save)
        app.router.add_post("/config/web", self._config_web_save)
        app.router.add_post("/config/raw", self._config_raw_save)
        app.router.add_post("/config/reset", self._config_reset)
        app.router.add_post("/config/test-provider", self._config_test_provider)
