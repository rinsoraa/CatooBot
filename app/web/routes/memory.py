"""Memory pages: browse, search, health, retrieval debug, embeddings, consolidation, correction (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from aiohttp import web

from app.web import ui
from app.web.pages import correction_page
from app.web.routes.base import WebContext, esc, format_ts, layout


class MemoryRoutes(WebContext):
    """Handlers for this domain."""

    async def _memory_page(self, request: web.Request) -> web.Response:
        keyword = request.query.get("q", "")
        category = request.query.get("category", "")
        scope_key = request.query.get("scope", "")
        source = request.query.get("source", "")
        memories = await self._admin.list_memories(
            keyword=keyword, category=category, scope_key=scope_key, source=source, limit=200
        )
        options = [
            ("", "全部类型"),
            ("fact", "fact · 客观事实"),
            ("preference", "preference · 喜好"),
            ("profile", "profile · 资料"),
            ("project", "project · 项目"),
            ("interest", "interest · 兴趣"),
            ("habit", "habit · 习惯"),
            ("event", "event · 发生过的事"),
            ("relationship", "relationship · 关系"),
            ("instruction", "instruction · 用户要求"),
        ]
        rows = "".join(
            f"<tr><td>{m['id']}</td>"
            f"<td><a href='/memory?scope={quote(str(m['scope_key']))}'>"
            f"{esc(m['scope_key'])}</a></td>"
            f"<td>{ui.badge(m['category'], 'info')}"
            f"{' ' + ui.badge('vision', 'muted') if m.get('source') == 'vision' else ''}</td>"
            f"<td>{esc(m['content'])}</td>"
            f"<td>{m['importance']:.2f}</td><td>{m['confidence']:.2f}</td>"
            f"<td>{m['use_count']}</td>"
            f"<td class='muted'>{esc(format_ts(m['created_at']))}</td>"
            f"<td><a class='btn btn-ghost btn-sm' href='/memory/detail/{m['id']}'"
            f"{ui.attr_tip('查看这条记忆的来源、关系与演化历史')}>详情</a>"
            f"<form class='inline' method='post' action='/api/memory/delete/{m['id']}'"
            f" data-confirm='确定删除这条记忆吗？（会同时删除它的向量）'>"
            f"<button class='btn btn-danger btn-sm' type='submit'"
            f"{ui.attr_tip('物理删除：不推荐，通常用「记忆修正」或归档更安全')}>删除</button>"
            f"</form></td></tr>"
            for m in memories
        )
        filters = (
            "<form method='get' action='/memory'><div class='grid'>"
            + ui.field(
                "搜索内容",
                "q",
                keyword,
                tip_text="按关键词过滤（不做语义检索，语义搜索请用「搜索」页）",
            )
            + ui.select(
                "类型", "category", options, category, tip_text="记忆的语义分类，影响检索时的加权"
            )
            + ui.select(
                "来源",
                "source",
                [
                    ("", "全部来源"),
                    ("conversation", "conversation · 从对话里记下的"),
                    ("vision", "vision · 她看过的图片"),
                    ("system", "system · 系统整理/压缩"),
                    ("explicit", "explicit · 用户明确要求"),
                ],
                source,
                tip_text="她是怎么知道这件事的：对话、看图、还是系统整理",
            )
            + ui.field(
                "Scope",
                "scope",
                scope_key,
                tip_text="按用户/群过滤：user:123456 或 group:654321；留空查看全部",
            )
            + "</div><p><button class='btn btn-secondary' type='submit'"
            + ui.attr_tip("应用过滤条件")
            + ">筛选</button>"
            + ui.link_button(
                "/memory/correction",
                "用一句话修正记忆",
                tip_text="不用删改数据库：写清「改成什么」，她就会当作一直如此",
            )
            + "</p></form>"
        )
        table = ui.table(
            ["ID", "Scope", "类型", "内容", "重要度", "置信度", "用过", "创建时间", ""],
            rows,
            tips=[
                "记忆主键，修正时用它定位",
                "属于哪位用户/哪个群",
                "记忆分类",
                "记忆正文（会进入提示词的就是这行）",
                "越高越优先被想起",
                "模型对这条事实的把握",
                "被检索命中的次数",
                "写入时间",
                "详情 / 删除",
            ],
            empty="没有符合条件的记忆",
            table_id="memories",
        )
        body = (
            self._memory_tabs("/memory")
            + ui.card("筛选", filters, tip_text="这里按元数据过滤；语义相似度搜索在「搜索」标签页")
            + ui.card(
                f"记忆({len(memories)})",
                table,
                tip_text="只列出状态为 active 的记忆；被取代/归档的旧事实在时间线里可查",
            )
        )
        return web.Response(
            text=layout("记忆", "/memory", body, subtitle="她长期记住的事，以及为什么"),
            content_type="text/html",
        )

    async def _memory_search_page(self, request: web.Request) -> web.Response:
        form = await request.post() if request.method == "POST" else {}
        query = str(form.get("q") or request.query.get("q", "")).strip()
        mode = str(form.get("mode") or request.query.get("mode", "hybrid"))
        scope_key = str(form.get("scope") or request.query.get("scope", "")).strip()

        results_html = ""
        if query:
            data = await self._memory_admin.search(query, mode=mode, scope_key=scope_key, limit=15)
            rows = "".join(
                f"<tr><td>{esc(r.get('final') if r.get('final') is not None else '-')}</td>"
                f"<td>{esc(r.get('semantic'))}</td><td>{esc(r.get('keyword'))}</td>"
                f"<td>{esc(r.get('layer'))}</td><td>{esc(r.get('category'))}</td>"
                f"<td>{esc(r.get('scope_key'))}</td><td>{esc(r.get('origin'))}</td>"
                f"<td><a href='/memory/detail/{r['id']}'>{r['id']}</a></td>"
                f"<td>{esc(r.get('content'))}</td></tr>"
                for r in data.get("results", [])
            )
            note = (
                ""
                if data.get("semantic_available")
                else (
                    '<p class="muted">语义检索不可用（Embedding 未配置或失败）— 已降级为关键词+元数据排序</p>'
                )
            )
            results_html = (
                f'<div class="card"><h3>结果 ({len(data.get("results", []))}) — mode={esc(mode)}</h3>{note}'
                "<table><tr><th>final</th><th>semantic</th><th>keyword</th><th>layer</th>"
                "<th>type</th><th>scope</th><th>来源</th><th>ID</th><th>内容</th></tr>"
                + (rows or '<tr><td colspan="9" class="muted">无结果</td></tr>')
                + "</table></div>"
            )

        body = (
            self._memory_tabs("/memory/search")
            + f"""
<div class="card"><h3>记忆搜索</h3><form method="post" action="/memory/search">
<label>查询</label><input name="q" value="{esc(query)}">
<label>模式</label><select name="mode">
<option value="hybrid" {"selected" if mode == "hybrid" else ""}>hybrid（语义+关键词+元数据）</option>
<option value="semantic" {"selected" if mode == "semantic" else ""}>semantic（仅语义）</option>
<option value="keyword" {"selected" if mode == "keyword" else ""}>keyword（仅关键词）</option>
</select>
<label>Scope（留空=全部；user:123 / group:456）</label>
<input name="scope" value="{esc(scope_key)}">
<p><button class="btn btn-primary">搜索</button></p></form></div>
{results_html}"""
        )
        return web.Response(
            text=layout(
                "记忆 · 搜索", "/memory", body, subtitle="语义 + 关键词混合检索，可看每项得分"
            ),
            content_type="text/html",
        )

    async def _memory_timeline_page(self, request: web.Request) -> web.Response:
        scope_key = request.query.get("scope", "")
        memories = await self._memory_admin.timeline(scope_key, limit=300)
        rows = "".join(
            f"<tr><td class='muted'>{esc(format_ts(m.get('event_at') or m.get('created_at')))}</td>"
            f"<td>{esc(m['layer'])}</td><td>{esc(m['category'])}</td>"
            f"<td>{esc(m['status'])}</td><td>{esc(m['scope_key'])}</td>"
            f"<td><a href='/memory/detail/{m['id']}'>{m['id']}</a></td>"
            f"<td>{esc(m.get('summary') or m['content'])}</td></tr>"
            for m in memories
        )
        body = (
            self._memory_tabs("/memory/timeline")
            + f"""
<div class="card"><form method="get" action="/memory/timeline">
<label>Scope（留空=全部）</label><input name="scope" value="{esc(scope_key)}">
<p><button class="btn btn-primary">筛选</button></p></form></div>
<div class="card"><h3>时间线 ({len(memories)})</h3>
<table><tr><th>时间</th><th>层</th><th>类型</th><th>状态</th><th>Scope</th><th>ID</th><th>内容</th></tr>
{rows or '<tr><td colspan="7" class="muted">暂无记忆</td></tr>'}</table></div>"""
        )
        return web.Response(
            text=layout(
                "记忆 · 时间线",
                "/memory",
                body,
                subtitle="她的记忆是怎么长出来的（含被取代的旧事实）",
            ),
            content_type="text/html",
        )

    async def _memory_health_page(self, request: web.Request) -> web.Response:
        health = await self._memory_admin.health()
        embedding = health.get("embedding", {})
        scheduler = health.get("scheduler", {})
        last = health.get("last_consolidation") or {}
        extraction = health.get("extraction") or {}

        def stat(label: str, value: object) -> str:
            return f'<div class="stat"><span class="muted">{esc(label)}</span><b>{esc(value)}</b></div>'

        cards = "".join(
            stat(label, value)
            for label, value in (
                ("总计", health.get("total", 0)),
                ("Active", health.get("active", 0)),
                ("事件层", health.get("episodic", 0)),
                ("语义层", health.get("semantic", 0)),
                ("已被取代", health.get("superseded", 0)),
                ("已归档", health.get("archived", 0)),
                ("已过期", health.get("expired", 0)),
                ("Avg Importance", health.get("average_importance", 0)),
                ("Avg Confidence", health.get("average_confidence", 0)),
                ("向量化覆盖率", health.get("embedding_coverage", 0)),
                ("已向量化", health.get("embedded", 0)),
                (
                    "Semantic Search",
                    "on" if health.get("retrieval", {}).get("semantic_available") else "off",
                ),
                ("未落盘（outbox）", health.get("outbox", {}).get("pending", 0)),
                ("抽取模型", extraction.get("model") or "（默认）"),
            )
        )
        banner = ui.flash("error", str(extraction.get("note") or ""))
        body = (
            self._memory_tabs("/memory/health")
            + banner
            + f"""
<div class="grid">{cards}</div>
<div class="card"><h3>Embedding</h3>
<table>
<tr><th>Provider / Model</th><td>{esc(embedding.get("provider") or "-")} / {esc(embedding.get("model") or "-")}</td></tr>
<tr><th>Dimensions</th><td>{esc(embedding.get("dimensions") or "-")}</td></tr>
<tr><th>Cache hits / misses</th><td>{esc(embedding.get("hits", 0))} / {esc(embedding.get("misses", 0))}</td></tr>
<tr><th>Failures</th><td>{esc(embedding.get("failures", 0))}</td></tr>
<tr><th>Last error</th><td class="muted">{esc(embedding.get("last_error") or "-")}</td></tr>
</table></div>
<div class="card"><h3>Consolidation</h3>
<table>
<tr><th>Schedule</th><td>{esc(scheduler.get("schedule") or "-")}（running={esc(scheduler.get("running"))}, runs={esc(scheduler.get("runs", 0))}）</td></tr>
<tr><th>Next run in</th><td>{esc(scheduler.get("next_run_in"))}</td></tr>
<tr><th>Last report</th><td>{esc(last.get("summary") or "-")}</td></tr>
</table></div>
<div class="card"><h3>Policy / Retention</h3>
<pre>{esc(json.dumps({"policy": health.get("policy"), "retention": health.get("retention"), "retrieval": health.get("retrieval")}, ensure_ascii=False, indent=2))}</pre></div>"""
        )
        return web.Response(
            text=layout("记忆 · 健康度", "/memory", body, subtitle="向量化覆盖、配额与降级情况"),
            content_type="text/html",
        )

    async def _memory_debug_page(self, request: web.Request) -> web.Response:
        form = await request.post() if request.method == "POST" else {}
        query = str(form.get("q") or request.query.get("q", "")).strip()
        scope_key = str(form.get("scope") or request.query.get("scope", "")).strip()
        result_html = ""
        if query:
            data = await self._memory_admin.retrieval_debug(query, scope_key)
            rows = "".join(
                f"<tr><td>{r['id']}</td><td>{r['final']}</td><td>{r['semantic']}</td>"
                f"<td>{r['keyword']}</td><td>{r['importance']}</td><td>{r['confidence']}</td>"
                f"<td>{r['recency']}</td><td>{r['relationship']}</td><td>{r['topic_bonus']}</td>"
                f"<td>{r['temporal']}</td><td>{esc(r['origin'])}</td>"
                f"<td>{esc(r['content'])}</td></tr>"
                for r in data.get("results", [])
            )
            result_html = f"""
<div class="card"><h3>管线</h3>
<pre>{esc(json.dumps({k: v for k, v in data.items() if k != "results"}, ensure_ascii=False, indent=2))}</pre></div>
<div class="card"><h3>打分明细</h3>
<table><tr><th>ID</th><th>final</th><th>semantic</th><th>keyword</th><th>importance</th>
<th>confidence</th><th>recency</th><th>relationship</th><th>topic</th><th>temporal</th>
<th>来源</th><th>内容</th></tr>
{rows or '<tr><td colspan="12" class="muted">无结果</td></tr>'}</table></div>"""
        body = (
            self._memory_tabs("/memory/retrieval-debug")
            + f"""
<div class="card"><h3>检索调试器（仅后台）</h3><form method="post" action="/memory/retrieval-debug">
<label>查询</label><input name="q" value="{esc(query)}">
<label>Scope（留空=全部）</label><input name="scope" value="{esc(scope_key)}">
<p><button class="btn btn-primary">运行</button></p></form></div>
{result_html}"""
        )
        return web.Response(
            text=layout(
                "记忆 · 检索调试", "/memory", body, subtitle="为什么这条记忆会被想起（打分明细）"
            ),
            content_type="text/html",
        )

    async def _memory_embeddings_page(self, request: web.Request) -> web.Response:
        status = await self._memory_admin.embedding_status()
        body = (
            self._memory_tabs("/memory/embeddings")
            + f"""
<div class="card"><h3>向量状态</h3>
<pre>{esc(json.dumps(status, ensure_ascii=False, indent=2))}</pre>
<form class="inline" method="post" action="/memory/embeddings/rebuild"><button class="btn btn-secondary btn-sm">重建向量</button></form>
<form class="inline" method="post" action="/memory/embeddings/retry"><button class="btn btn-secondary btn-sm">重试失败项</button></form>
<form class="inline" method="post" action="/memory/embeddings/clear-cache"><button class="btn btn-danger btn-sm">清空缓存</button></form>
<p class="muted">重建/补齐在后台执行，不会阻塞 QQ 聊天。</p></div>"""
        )
        return web.Response(
            text=layout("记忆 · 向量", "/memory", body, subtitle="Embedding 服务状态、缓存与重建"),
            content_type="text/html",
        )

    async def _memory_embedding_action(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        if action == "rebuild":
            result = await self._memory_admin.rebuild_embeddings(limit=500)
        elif action == "retry":
            result = await self._memory_admin.retry_failed()
        elif action == "clear-cache":
            result = await self._memory_admin.clear_embedding_cache()
        else:
            result = {"error": f"unknown action: {action}"}
        return web.Response(
            text=layout(
                "记忆 · 向量",
                "/memory",
                self._memory_tabs("/memory/embeddings")
                + f'<div class="card"><pre>{esc(json.dumps(result, ensure_ascii=False))}</pre>'
                '<p><a href="/memory/embeddings">返回</a></p></div>',
            ),
            content_type="text/html",
        )

    async def _memory_consolidation_page(self, request: web.Request) -> web.Response:
        status = await self._memory_admin.consolidation_status()
        last = status.get("last_report") or {}
        body = (
            self._memory_tabs("/memory/consolidation")
            + f"""
<div class="card"><h3>巩固状态</h3>
<pre>{esc(json.dumps(status, ensure_ascii=False, indent=2))}</pre></div>
<div class="card"><h3>手动执行</h3><form method="post" action="/memory/consolidation/run">
<label>Scope（留空=全部；user:123 / group:456）</label><input name="scope" value="">
<p><button class="btn btn-primary">运行巩固</button></p></form>
<p class="muted">去重合并、事件压缩、过期归档、配额清理；后台执行，不阻塞聊天。</p></div>
<div class="card"><h3>上次结果</h3><pre>{esc(last.get("summary") or "尚未运行")}</pre></div>"""
        )
        return web.Response(
            text=layout("记忆 · 巩固", "/memory", body, subtitle="后台整理记忆的时间与结果"),
            content_type="text/html",
        )

    async def _memory_consolidation_run(self, request: web.Request) -> web.Response:
        form = await request.post()
        result = await self._memory_admin.run_consolidation(str(form.get("scope", "")).strip())
        return web.Response(
            text=layout(
                "记忆 · 巩固",
                "/memory",
                self._memory_tabs("/memory/consolidation")
                + f'<div class="card"><pre>{esc(json.dumps(result, ensure_ascii=False, indent=2))}</pre>'
                '<p><a href="/memory/consolidation">返回</a></p></div>',
            ),
            content_type="text/html",
        )

    async def _memory_detail_page(self, request: web.Request) -> web.Response:
        memory_id = int(request.match_info["memory_id"])
        data = await self._memory_admin.detail(memory_id)
        if not data:
            return web.Response(
                text=layout(
                    "记忆",
                    "/memory",
                    self._memory_tabs("/memory") + '<div class="card">记忆不存在</div>',
                ),
                content_type="text/html",
            )
        memory = data["memory"]
        relations = data["relations"]
        previous = data["supersedes"]

        rel_rows = (
            "".join(
                f"<tr><td>{esc(r['relation'])}</td><td>{r['to_id']}</td>"
                f"<td>{esc(r.get('related_status') or '')}</td>"
                f"<td>{esc((r.get('related_content') or '')[:80])}</td></tr>"
                for r in relations
            )
            or '<tr><td colspan="4" class="muted">无关联</td></tr>'
        )
        prev_html = "".join(
            f"<li>#{p['id']}（{esc(p['status'])}）{esc(p['content'])}</li>" for p in previous
        )
        body = (
            self._memory_tabs("/memory")
            + f"""
<div class="card"><h3>记忆 #{memory["id"]}</h3>
<table>
<tr><th>内容</th><td>{esc(memory["content"])}</td></tr>
<tr><th>Summary</th><td>{esc(memory.get("summary") or "-")}</td></tr>
<tr><th>Layer / Type</th><td>{esc(memory["layer"])} / {esc(memory["category"])}</td></tr>
<tr><th>Scope</th><td>{esc(memory["scope_key"])}</td></tr>
<tr><th>Status</th><td>{esc(memory["status"])}</td></tr>
<tr><th>Importance / Confidence</th><td>{memory["importance"]:.2f} / {memory["confidence"]:.2f}</td></tr>
<tr><th>来源</th><td>{esc(memory["source"])}</td></tr>
<tr><th>使用次数</th><td>{memory["use_count"]}（最近 {esc(format_ts(memory.get("last_used_at")))}）</td></tr>
<tr><th>创建 / 更新</th><td>{esc(format_ts(memory["created_at"]))} / {esc(format_ts(memory["updated_at"]))}</td></tr>
<tr><th>有效期</th><td>{esc(format_ts(memory.get("valid_from")))} ~ {esc(format_ts(memory.get("valid_until")))}</td></tr>
</table>
<p><form class="inline" method="post" action="/api/memory/activate/{memory["id"]}"><button class="btn btn-secondary btn-sm">激活</button></form>
<form class="inline" method="post" action="/api/memory/archive/{memory["id"]}"><button class="btn btn-secondary btn-sm">归档</button></form>
<form class="inline" method="post" action="/api/memory/reembed/{memory["id"]}"><button class="btn btn-secondary btn-sm">重新向量化</button></form>
<form class="inline" method="post" action="/api/memory/delete/{memory["id"]}"><button class="btn btn-danger btn-sm">删除</button></form></p>
</div>
<div class="card"><h3>替代的历史记忆</h3><ul>{prev_html or '<li class="muted">无</li>'}</ul></div>
<div class="card"><h3>关联关系</h3>
<table><tr><th>关系</th><th>目标 ID</th><th>状态</th><th>内容</th></tr>{rel_rows}</table></div>"""
        )
        return web.Response(
            text=layout("记忆 · 详情", "/memory", body, subtitle="一条记忆的来源、关系与演化"),
            content_type="text/html",
        )

    async def _api_memory_action(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        memory_id = int(request.match_info["memory_id"])
        ok = await self._admin.memory_action(action, memory_id)
        return web.json_response({"ok": ok})

    async def _memory_correction_page(self, request: web.Request) -> web.Response:
        form = await request.post() if request.method == "POST" else {}
        scope_key = str(form.get("scope") or request.query.get("scope", "")).strip()
        instruction = str(form.get("instruction") or request.query.get("instruction", "")).strip()
        service = self._correction
        targets = await service.targets()
        memories: list[dict[str, Any]] = []
        plan: dict[str, Any] | None = None
        message = str(form.get("result") or request.query.get("result", ""))
        kind = "warn" if message.startswith("!") else "ok"
        message = message.lstrip("!")
        if not message and request.method == "POST":
            message, kind = "把指令写得更具体一些，例如：把用户喜欢吃的东西改成草莓", "error"
        if scope_key:
            memories = await service.memories(scope_key)
        if request.method == "POST" and scope_key and instruction:
            try:
                plan = await service.plan(scope_key, instruction)
                message = "已生成方案，确认后才会写入"
                kind = "info"
            except Exception as exc:  # noqa: BLE001 - shown to the operator
                message, kind = str(exc), "error"
        body = correction_page.render(
            service,
            targets=targets,
            scope_key=scope_key,
            memories=memories,
            instruction=instruction,
            plan=plan,
            msg=message,
            kind=kind,
        )
        return web.Response(
            text=layout(
                "记忆修正",
                "/memory",
                self._memory_tabs("/memory/correction") + body,
                subtitle="用一句人话纠正她记住的事",
            ),
            content_type="text/html",
        )

    async def _memory_correction_apply(self, request: web.Request) -> web.Response:
        form = await request.post()
        raw = str(form.get("plan", "{}"))
        try:
            plan = json.loads(raw)
            result = await self._correction.apply(plan)
        except Exception as exc:  # noqa: BLE001
            raise web.HTTPFound(
                f"/memory/correction?result={quote('!修正失败：' + str(exc)[:200])}"
            ) from exc
        if result.get("changed"):
            message = f"已修正：{str(plan.get('before') or '')[:40]} → {str(result.get('content') or '')[:40]}"
        else:
            message = "没有需要修改的内容"
        scope_key = str(plan.get("scope_key") or "")
        raise web.HTTPFound(f"/memory/correction?scope={quote(scope_key)}&result={quote(message)}")

    @staticmethod
    def _memory_tabs(active: str) -> str:
        tabs = [
            ("/memory", "浏览"),
            ("/memory/search", "搜索"),
            ("/memory/correction", "记忆修正"),
            ("/memory/timeline", "时间线"),
            ("/memory/health", "健康度"),
            ("/memory/retrieval-debug", "检索调试"),
            ("/memory/embeddings", "向量"),
            ("/memory/consolidation", "巩固"),
        ]
        return ui.tabs(tabs, active)

    def register_memory(self, app: web.Application) -> None:
        app.router.add_get("/memory", self._memory_page)
        app.router.add_get("/memory/search", self._memory_search_page)
        app.router.add_post("/memory/search", self._memory_search_page)
        app.router.add_get("/memory/timeline", self._memory_timeline_page)
        app.router.add_get("/memory/health", self._memory_health_page)
        app.router.add_get("/memory/retrieval-debug", self._memory_debug_page)
        app.router.add_post("/memory/retrieval-debug", self._memory_debug_page)
        app.router.add_get("/memory/embeddings", self._memory_embeddings_page)
        app.router.add_post("/memory/embeddings/{action}", self._memory_embedding_action)
        app.router.add_get("/memory/consolidation", self._memory_consolidation_page)
        app.router.add_post("/memory/consolidation/run", self._memory_consolidation_run)
        app.router.add_get("/memory/detail/{memory_id}", self._memory_detail_page)
        app.router.add_post("/api/memory/{action}/{memory_id}", self._api_memory_action)
        app.router.add_get("/memory/correction", self._memory_correction_page)
        app.router.add_post("/memory/correction", self._memory_correction_page)
        app.router.add_post("/memory/correction/apply", self._memory_correction_apply)
