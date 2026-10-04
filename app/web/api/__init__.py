"""`/api/v1` JSON API（WebUI v1.0 · W2）。

一个命名空间、一个信封、一个错误映射。路由只做「校验 → 调既有 Service」，
业务事实永远留在 Core；旧 SSR 页面不受影响（`/api/v1` 与它们并存）。

每个处理器都经 :func:`~app.web.api.common.json_endpoint` 包装：异常 →
契约规定的 JSON 错误，不再有 per-endpoint 的 ``try/except``。
"""

from __future__ import annotations

from aiohttp import web

from app.web.api.agent_api import AgentApiRoutes
from app.web.api.ai import AiApiRoutes
from app.web.api.common import API_PREFIX, json_endpoint
from app.web.api.config_api import ConfigApiRoutes
from app.web.api.credentials import CredentialApiRoutes
from app.web.api.domain import DomainApiRoutes
from app.web.api.media_api import MediaApiRoutes
from app.web.api.minecraft import MinecraftApiRoutes
from app.web.api.runtime import RuntimeApiRoutes
from app.web.api.session import SessionRoutes
from app.web.api.tools_api import ToolsApiRoutes
from app.web.routes.base import WebContext


class ApiRoutes(
    SessionRoutes,
    ConfigApiRoutes,
    CredentialApiRoutes,
    AiApiRoutes,
    RuntimeApiRoutes,
    DomainApiRoutes,
    ToolsApiRoutes,
    MediaApiRoutes,
    AgentApiRoutes,
    MinecraftApiRoutes,
    WebContext,
):
    """Composes every `/api/v1` domain module (W2)."""

    def register_v1(self, app: web.Application) -> None:
        wrap = json_endpoint
        app.router.add_get(f"{API_PREFIX}/meta", wrap(self._v1_meta))
        app.router.add_get(f"{API_PREFIX}/session", wrap(self._v1_session_get))
        app.router.add_post(f"{API_PREFIX}/session", wrap(self._v1_session_post))
        app.router.add_delete(f"{API_PREFIX}/session", wrap(self._v1_session_delete))
        app.router.add_patch(f"{API_PREFIX}/session", wrap(self._v1_session_patch))
        app.router.add_post(f"{API_PREFIX}/session/password", wrap(self._v1_session_password))
        app.router.add_get(f"{API_PREFIX}/csrf", wrap(self._v1_csrf))

        app.router.add_get(f"{API_PREFIX}/config/schema", wrap(self._v1_config_schema))
        app.router.add_get(f"{API_PREFIX}/config/effective", wrap(self._v1_config_effective))
        app.router.add_get(
            f"{API_PREFIX}/config/restart-pending", wrap(self._v1_config_restart_pending)
        )
        app.router.add_post(f"{API_PREFIX}/config/validate", wrap(self._v1_config_validate))
        app.router.add_patch(f"{API_PREFIX}/config", wrap(self._v1_config_patch))
        app.router.add_post(f"{API_PREFIX}/config/reset", wrap(self._v1_config_reset))
        app.router.add_get(f"{API_PREFIX}/config/raw", wrap(self._v1_config_raw_get))
        app.router.add_put(f"{API_PREFIX}/config/raw", wrap(self._v1_config_raw_put))

        # Domain modules keep their own registration; each one is independently
        # testable and the whole namespace is one revertible unit.
        for name in (
            "_register_v1_credentials",
            "_register_v1_ai",
            "_register_v1_runtime",
            "_register_v1_domain",
            "_register_v1_social",
            "_register_v1_tools",
            "_register_v1_media",
            "_register_v1_agent",
            "_register_v1_minecraft",
        ):
            register = getattr(self, name, None)
            if register is not None:
                register(app)
