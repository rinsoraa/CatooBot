"""Social cognition pages (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

from urllib.parse import quote

from aiohttp import web

from app.web.pages import social_page
from app.web.routes.base import WebContext, esc, layout


class SocialRoutes(WebContext):
    """Handlers for this domain."""

    async def _social_page(self, request: web.Request) -> web.Response:
        data = await self._social_admin.dashboard()
        feedback = await self._social_admin.reply_feedback(days=7)
        body = (
            social_page._tabs("/social")
            + self._reply_feedback_card(feedback)
            + social_page.dashboard(data)
        )
        return web.Response(
            text=layout("社交认知", "/social", body, subtitle="她会先听，再判断自己有没有必要说话"),
            content_type="text/html",
        )

    @staticmethod
    def _reply_feedback_card(feedback: dict) -> str:
        """“她最近说得怎么样” — what happened after she spoke (Task 20 §7)."""
        if not feedback.get("available"):
            return '<div class="card"><h3>她最近说得怎么样</h3><p class="muted">暂无</p></div>'
        if not feedback.get("total"):
            return (
                '<div class="card"><h3>她最近说得怎么样</h3>'
                '<p class="muted">近 7 天还没有可结算的回合。</p></div>'
            )
        rate = feedback.get("engaged_rate")
        engagement = feedback.get("engagement") or {}
        rows = "".join(
            f"<tr><td>{esc(reason)}</td><td>{stats['total']}</td><td>{stats['engaged']}</td></tr>"
            for reason, stats in (feedback.get("by_reason") or {}).items()
        )
        median = feedback.get("median_first_reply")
        return (
            '<div class="card"><h3>她最近说得怎么样 <span class="muted">近 7 天</span></h3>'
            f'<div class="grid">'
            f'<div class="stat"><span class="muted">开口次数</span><b>{feedback["total"]}</b></div>'
            f'<div class="stat"><span class="muted">有人接话率</span>'
            f"<b>{'—' if rate is None else f'{rate:.0%}'}</b></div>"
            f'<div class="stat"><span class="muted">中位接话延迟</span>'
            f"<b>{'—' if median is None else f'{median:.0f}s'}</b></div>"
            f'<div class="stat"><span class="muted">群本来就安静</span>'
            f"<b>{feedback['quiet_group']}/{feedback['silence']}</b></div>"
            f'<div class="stat"><span class="muted">被嫌 / 无人理</span>'
            f"<b>{feedback['negative']} / {feedback['ambient']}</b></div>"
            f'<div class="stat"><span class="muted">无法判定</span><b>{feedback["unknown"]}</b></div>'
            f"</div>"
            f"<table><tr><th>为什么开口</th><th>次数</th><th>有人接</th></tr>{rows}</table>"
            f'<p class="muted">被点名（@/回复/续话）的回合只记录、不参与参与度系数；'
            f"参与度样本 {engagement.get('per_group') and len(engagement.get('per_group') or {}) or 0} 个群，"
            f"死区 {engagement.get('min_samples', '-')} 条样本以内不调整。</p></div>"
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
