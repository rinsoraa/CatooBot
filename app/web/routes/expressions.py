"""Expression / 口癖 page (Task 22): list, disable/enable, delete, provenance."""

from __future__ import annotations

from aiohttp import web

from app.web import ui
from app.web.routes.base import WebContext, esc, format_ts, layout


class ExpressionsRoutes(WebContext):
    async def _expressions_page(self, request: web.Request) -> web.Response:
        pattern_id = request.query.get("id", "")
        detail_html = ""
        if pattern_id:
            samples = await self._expression_admin.samples(int(pattern_id))
            rows = "".join(
                f"<tr><td>{esc(s['user_id'])}</td><td>{esc(s['text'])}</td>"
                f"<td class='muted'>{esc(format_ts(s['seen_at']))}</td>"
                f"<td class='muted'>{esc(s['message_id'])}</td></tr>"
                for s in samples
            )
            detail_html = f"""<div class="card"><h3>来源样本（{len(samples)}）</h3>
<table><tr><th>发言人</th><th>原文（已脱敏）</th><th>时间</th><th>message_id</th></tr>
{rows or '<tr><td colspan="4" class="muted">无样本</td></tr>'}</table>
<p><a href="/expressions">返回列表</a></p></div>"""

        patterns = await self._expression_admin.list_patterns()
        rows = "".join(
            f"""<tr><td>{esc(p["pattern"])}</td><td>{p["kind"]}</td><td class="muted">{esc(p["scope_key"])}</td>
<td>{p["sample_count"]}（{p["speaker_count"]} 人）</td>
<td class="muted">{esc(format_ts(p["last_used_at"])) if p["last_used_at"] else "—"}</td>
<td>{ui.badge(p["status"], "info" if p["status"] == "active" else "warn")}</td>
<td><a class="btn btn-ghost btn-sm" href="/expressions?id={p["id"]}">来源</a></td>
<td><form class="inline" method="post" action="/expressions/toggle">
<input type="hidden" name="id" value="{p["id"]}">
<input type="hidden" name="status" value="{"active" if p["status"] == "disabled" else "disabled"}">
<button class="btn btn-secondary btn-sm">{"启用" if p["status"] == "disabled" else "停用"}</button></form></td>
<td><form class="inline" method="post" action="/expressions/delete" data-confirm="删除这条表达及其来源/向量？">
<input type="hidden" name="id" value="{p["id"]}">
<button class="btn btn-danger btn-sm">删除</button></form></td></tr>"""
            for p in patterns
        )

        enabled = self._expression_admin.config.enabled
        body = f"""<div class="card"><h3>口癖 / 表达学习（{len(patterns)}）</h3>
<p class="hint">这些表达是从群友的话里学来的，随时可停用或删除；总开关在 <code>config.expression.enabled</code>
（当前 {"开启" if enabled else "关闭"}）。学到的表达只按群注入，且不改变她的说话风格。</p>
<table><tr><th>表达</th><th>类型</th><th>来源群</th><th>样本</th><th>最近用到</th><th>状态</th><th colspan="3"></th></tr>
{rows or '<tr><td colspan="9" class="muted">还没有学到任何表达（开启后，群里不同人重复用的短句式会被记下来）</td></tr>'}</table></div>{detail_html}"""
        return web.Response(
            text=layout(
                "口癖",
                "/expressions",
                body,
                subtitle="从群消息学的口头禅：可停用、可删除、可追溯来源",
            ),
            content_type="text/html",
        )

    async def _expressions_toggle(self, request: web.Request) -> web.Response:
        form = await request.post()
        pattern_id = int(str(form.get("id", "0") or "0"))
        status = str(form.get("status", "disabled"))
        await self._expression_admin.set_status(pattern_id, status)
        raise web.HTTPFound("/expressions")

    async def _expressions_delete(self, request: web.Request) -> web.Response:
        form = await request.post()
        pattern_id = int(str(form.get("id", "0") or "0"))
        await self._expression_admin.delete(pattern_id)
        raise web.HTTPFound("/expressions")

    def register_expressions(self, app: web.Application) -> None:
        app.router.add_get("/expressions", self._expressions_page)
        app.router.add_post("/expressions/toggle", self._expressions_toggle)
        app.router.add_post("/expressions/delete", self._expressions_delete)
