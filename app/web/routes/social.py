"""Social cognition pages (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

from urllib.parse import quote

from aiohttp import web

from app.web.pages import social_page
from app.web.routes.base import WebContext, layout


class SocialRoutes(WebContext):
    """Handlers for this domain."""

    async def _social_page(self, request: web.Request) -> web.Response:
        data = await self._social_admin.dashboard()
        body = social_page._tabs("/social") + social_page.dashboard(data)
        return web.Response(
            text=layout("社交认知", "/social", body, subtitle="她会先听，再判断自己有没有必要说话"),
            content_type="text/html",
        )

    async def _social_observations_page(self, request: web.Request) -> web.Response:
        group_id = request.query.get("group", "")
        rows = await self._social_admin.observations(group_id=group_id)
        body = social_page._tabs("/social/observations") + social_page.observations_page(
            rows, group_id
        )
        return web.Response(
            text=layout(
                "社交 · 观察记录", "/social", body, subtitle="结构化决策日志（调试用，不保存隐私）"
            ),
            content_type="text/html",
        )

    async def _social_group_page(self, request: web.Request) -> web.Response:
        group_id = request.query.get("group", "")
        snapshot = self._social_admin.group_context(group_id) if group_id else None
        body = social_page._tabs("/social/group") + social_page.group_page(snapshot, group_id)
        return web.Response(
            text=layout("社交 · 群上下文", "/social", body, subtitle="某个群的实时参与状态"),
            content_type="text/html",
        )

    async def _social_simulator_page(self, request: web.Request) -> web.Response:
        group_id = request.query.get("group", "")
        result = await self._social_admin.analyze_now(group_id) if group_id else None
        body = social_page._tabs("/social/simulator") + social_page.simulator_page(result, group_id)
        return web.Response(
            text=layout("社交 · 手动分析", "/social", body, subtitle="不发送 QQ，只看她会怎么判断"),
            content_type="text/html",
        )

    async def _social_analyze(self, request: web.Request) -> web.Response:
        form = await request.post()
        group_id = str(form.get("group", ""))
        raise web.HTTPFound(f"/social/simulator?group={quote(group_id)}")

    async def _social_replay(self, request: web.Request) -> web.Response:
        form = await request.post()
        group_id = str(form.get("group", ""))
        raise web.HTTPFound(f"/social/simulator?group={quote(group_id)}")

    async def _social_policy_page(self, request: web.Request) -> web.Response:
        msg = request.query.get("msg", "")
        config = self._bot.config.social.model_dump()
        body = social_page._tabs("/social/policy") + social_page.policy_page(config, msg)
        return web.Response(
            text=layout("社交 · 策略", "/social", body, subtitle="阈值与频率上限（保存即热加载）"),
            content_type="text/html",
        )

    async def _social_policy_save(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._social_admin.save_settings(dict(form))
        raise web.HTTPFound(f"/social/policy?msg={quote('设置已保存并热加载')}")

    def register_social(self, app: web.Application) -> None:
        app.router.add_get("/social", self._social_page)
        app.router.add_get("/social/observations", self._social_observations_page)
        app.router.add_get("/social/group", self._social_group_page)
        app.router.add_get("/social/simulator", self._social_simulator_page)
        app.router.add_post("/social/analyze", self._social_analyze)
        app.router.add_post("/social/replay", self._social_replay)
        app.router.add_get("/social/policy", self._social_policy_page)
        app.router.add_post("/social/policy", self._social_policy_save)
