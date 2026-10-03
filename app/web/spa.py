"""WebUI v1.0 的 SPA 托管（W3 §40-§43）。

三件事，都在管理层、不碰任何 Core 语义：

* **静态资产**：`webui/dist`（Vite 产物，随仓库发布）由 aiohttp 直接提供；
* **SPA fallback**：未命中的前端路由（`/ai`、`/settings` …）刷新时返回
  `index.html`，而 `/api/*`、`/ws/*` 永远不被吃掉；
* **回滚开关**：`web.version = "v1" | "v0.8"` —— `v0.8` 时 `/` 与 `/login`
  交还给旧 SSR 控制台，旧页面本身的路径（`/character` 等）始终不变，
  所以 `/legacy` 一直是可用入口。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from app.config.settings import PROJECT_ROOT

log = logging.getLogger("CatooBot.Web.SPA")

SPA_DIR = PROJECT_ROOT / "webui" / "dist"
INDEX_NAME = "index.html"
V1 = "v1"

#: 精确命中的静态文件（Vite 产物都在 /assets 下，favicon 是历史习惯）
_ASSET_PREFIXES = ("/assets/",)


def spa_dir() -> Path:
    return SPA_DIR


def spa_ready() -> bool:
    return (spa_dir() / INDEX_NAME).is_file()


def index_file() -> Path | None:
    path = spa_dir() / INDEX_NAME
    return path if path.is_file() else None


def safe_asset(relative: str) -> Path | None:
    """Resolve a request path inside ``dist``; nothing outside it is served."""
    base = spa_dir().resolve()
    try:
        candidate = (base / relative).resolve()
    except OSError:
        return None
    if candidate != base and base not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def _no_cache(path: Path) -> web.StreamResponse:
    response = web.FileResponse(path)
    response.headers["Cache-Control"] = "no-cache"
    return response


def _immutable(path: Path) -> web.StreamResponse:
    response = web.FileResponse(path)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


async def spa_index() -> web.StreamResponse:
    """`index.html`（缺失时给开发者的构建提示，而不是 500）。"""
    path = index_file()
    if path is not None:
        return _no_cache(path)
    return web.Response(
        status=503,
        text=not_built_page(),
        content_type="text/html",
    )


async def spa_asset(request: web.Request) -> web.StreamResponse:
    # the route is `/assets/{tail}` while the files live in `dist/assets/`
    tail = str(request.match_info.get("tail", ""))
    resolved = safe_asset(f"assets/{tail}")
    if resolved is None:
        raise web.HTTPNotFound()
    return _immutable(resolved)


async def spa_fallback(request: web.Request) -> web.StreamResponse:
    """前端路由的刷新兜底；API 与 WebSocket 命名空间永不落到这里。"""
    path = request.path
    if path.startswith("/api/"):
        from app.web.api.common import fail, not_found

        return fail(not_found("接口不存在", code="request.unknown_endpoint"), request=request)
    if path.startswith("/ws/"):
        raise web.HTTPNotFound()
    return await spa_index()


def not_built_page() -> str:
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>CatooBot · 前端未构建</title>"
        "<style>body{font-family:system-ui,'Microsoft YaHei',sans-serif;background:#14151a;"
        "color:#e8e9ee;display:grid;place-items:center;min-height:100vh;margin:0}"
        "code{background:#24262f;padding:2px 6px;border-radius:6px}</style></head><body>"
        "<div><h1>前端资源未构建</h1>"
        "<p>没有找到 <code>webui/dist/index.html</code>。请先构建前端：</p>"
        "<pre><code>cd webui &amp;&amp; npm install &amp;&amp; npm run build</code></pre>"
        "<p>构建完成后刷新本页即可；旧版控制台仍然可以在 "
        "<a style='color:#a3aaf0' href='/legacy'>/legacy</a> 使用。</p>"
        "</div></body></html>"
    )


class SpaRoutes:
    """`/`、`/login`、`/legacy` 的分发与静态资产（作为 WebServer 的 mixin）。"""

    _config: Any
    _dashboard: Any
    _login_page: Any

    def _v1_frontend(self) -> bool:
        version = str(getattr(self._config, "version", V1) or V1)
        return version != "v0.8"

    async def _spa_root(self, request: web.Request) -> web.StreamResponse:
        if not self._v1_frontend():
            return await self._dashboard(request)
        return await spa_index()

    async def _spa_login_page(self, request: web.Request) -> web.StreamResponse:
        if not self._v1_frontend():
            return await self._login_page(request)
        return await spa_index()

    async def _spa_legacy(self, request: web.Request) -> web.StreamResponse:
        """The v0.8 console keeps its original entry point here."""
        return await self._dashboard(request)

    async def _spa_favicon(self, request: web.Request) -> web.StreamResponse:
        resolved = safe_asset("favicon.ico")
        if resolved is None:
            return web.Response(status=204)
        return _immutable(resolved)

    def register_spa(self, app: web.Application) -> None:
        # Registered last: the catch-all only sees paths no domain claimed.
        app.router.add_get("/", self._spa_root)
        app.router.add_get("/login", self._spa_login_page)
        app.router.add_get("/legacy", self._spa_legacy)
        app.router.add_get("/assets/{tail:.*}", spa_asset)
        app.router.add_get("/favicon.ico", self._spa_favicon)
        app.router.add_get("/{tail:.*}", spa_fallback)
