"""Tool runtime pages: catalogue, executions, metrics, permissions (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from app.web.routes.base import WebContext, _as_float, _as_int, esc, format_ts, layout


def _sample_arguments(name: str) -> dict[str, Any]:
    """Prefilled test payloads for the WebUI test box (spec v0.6 §41)."""
    samples: dict[str, dict[str, Any]] = {
        "time": {"timezone": "Asia/Singapore"},
        "calculator": {"operation": "evaluate", "expression": "12893 * 473"},
        "weather": {"location": "Singapore", "days": 3},
        "web_search": {"query": "今天的新闻", "max_results": 3},
    }
    return samples.get(name, {})


class ToolRoutes(WebContext):
    """Handlers for this domain."""

    async def _tools_page(self, request: web.Request) -> web.Response:
        data = await self._tool_admin.dashboard()
        metrics = data.get("metrics", {})
        tools = data.get("tools", [])

        cards = "".join(
            f'<div class="stat"><span class="muted">{esc(label)}</span><b>{esc(value)}</b></div>'
            for label, value in (
                ("工具总数", data.get("total", 0)),
                ("Enabled", data.get("enabled_count", 0)),
                ("已禁用", data.get("disabled_count", 0)),
                ("今日调用", metrics.get("calls", 0)),
                ("成功", metrics.get("success", 0)),
                ("失败", metrics.get("failure", 0)),
                ("Timeouts", metrics.get("timeout", 0)),
                ("Cache Hits", metrics.get("cache_hits", 0)),
                ("Avg Latency (ms)", metrics.get("avg_latency_ms", 0)),
                ("P95 Latency (ms)", metrics.get("p95_latency_ms", 0)),
                ("决策模式", data.get("decision_mode", "-")),
                ("Budget / turn", data.get("max_calls_per_turn", 0)),
            )
        )
        rows = "".join(
            f"<tr><td><a href='/tools/{esc(t['name'])}'>{esc(t['name'])}</a></td>"
            f"<td>{esc(t['version'])}</td><td>{esc(t['description'])}</td>"
            f"<td>{esc(t['category'])}</td><td>{'✓' if t['enabled'] else '✗'}</td>"
            f"<td>{esc(t['risk_level'])}</td>"
            f"<td>{esc(t['timeout'] if t['timeout'] else 'default')}</td>"
            f"<td>{esc(t['cache_ttl_seconds'])}</td>"
            f"<td><form class='inline' method='post' action='/api/tools/toggle'>"
            f"<input type='hidden' name='name' value='{esc(t['name'])}'>"
            f"<input type='hidden' name='enabled' value='{0 if t['enabled'] else 1}'>"
            f"<button class='btn btn-secondary btn-sm'>{'禁用' if t['enabled'] else '启用'}</button></form></td></tr>"
            for t in tools
        )
        recent = data.get("recent", [])
        recent_rows = "".join(
            f"<tr><td class='muted'>{esc(format_ts(r['created_at']))}</td>"
            f"<td>{esc(r['tool_name'])}</td><td>{esc(r['user_id'] or '')}</td>"
            f"<td>{esc(r['status'])}</td><td>{esc(r['error_type'] or '')}</td>"
            f"<td>{esc(r['duration_ms'])}</td><td>{esc((r['result_summary'] or '')[:60])}</td></tr>"
            for r in recent
        )
        policy = data.get("policy", {})
        body = f"""
<div class="card"><p class="muted">工具调用对 QQ 用户完全不可见；这里的管理项保存后立即生效。
{"" if data.get("enabled") else "⚠️ 工具运行时当前未启用（config.yaml 的 tools.enabled）"}</p></div>
<div class="grid">{cards}</div>
<div class="card"><h3>Tool Registry</h3>
<table><tr><th>Tool</th><th>Version</th><th>Description</th><th>Category</th>
<th>Enabled</th><th>Risk</th><th>Timeout</th><th>Cache TTL</th><th></th></tr>
{rows or '<tr><td colspan="9" class="muted">没有注册任何工具</td></tr>'}</table></div>
<div class="card"><h3>Policy</h3><pre>{esc(json.dumps(policy, ensure_ascii=False, indent=2))}</pre></div>
<div class="card"><h3>最近执行</h3>
<table><tr><th>时间</th><th>Tool</th><th>User</th><th>状态</th><th>错误</th><th>耗时(ms)</th><th>结果摘要</th></tr>
{recent_rows or '<tr><td colspan="7" class="muted">暂无执行记录</td></tr>'}</table>
<p><a href="/tools/executions">全部执行日志</a> · <a href="/tools/metrics">指标</a> ·
<a href="/tools/decision-debug">决策调试</a> · <a href="/tools/permissions">权限</a> ·
<a href="/credentials">凭据</a></p></div>"""
        return web.Response(text=layout("工具", "/tools", body), content_type="text/html")

    async def _tool_detail_page(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        detail = await self._tool_admin.tool_detail(name)
        if not detail:
            return web.Response(
                text=layout("工具 · 详情", "/tools", '<div class="card">工具不存在</div>'),
                content_type="text/html",
            )
        meta = detail["metadata"]
        settings = detail.get("settings") or {}
        metrics = detail.get("metrics") or {}
        executions = detail.get("executions") or []
        credential: dict[str, Any] = next(iter((detail.get("credentials") or {}).values()), {})

        exec_rows = "".join(
            f"<tr><td class='muted'>{esc(format_ts(r['created_at']))}</td>"
            f"<td>{esc(r['status'])}</td><td>{esc(r['error_type'] or '')}</td>"
            f"<td>{esc(r['duration_ms'])}</td><td>{'✓' if r['cache_hit'] else ''}</td>"
            f"<td>{esc((r['result_summary'] or '')[:60])}</td></tr>"
            for r in executions
        )
        provider_options = "".join(
            f'<option value="{esc(p)}" {"selected" if settings.get("provider") == p else ""}>{esc(p)}</option>'
            for p in (["open_meteo", "weatherapi"] if name == "weather" else ["tavily", "brave"])
        )
        locator_block = ""
        if name == "weather":
            locator_block = (
                "<label>默认地点（用户未指定时使用）</label>"
                f'<input name="default_location" value="{esc(settings.get("default_location", ""))}">'
            )
        elif name == "web_search":
            locator_block = (
                "<label>每次最多返回条数</label>"
                f'<input name="max_results" value="{esc(settings.get("max_results", 5))}">'
            )
        body = f"""
<div class="card"><h3>{esc(meta["display_name"])}（{esc(name)}）</h3>
<table>
<tr><th>Description</th><td>{esc(meta["description"])}</td></tr>
<tr><th>When to use</th><td>{esc(meta["when_to_use"])}</td></tr>
<tr><th>When NOT to use</th><td>{esc(meta["when_not_to_use"])}</td></tr>
<tr><th>Limitations</th><td>{esc(meta["limitations"])}</td></tr>
<tr><th>Risk / Category</th><td>{esc(meta["risk_level"])} / {esc(meta["category"])}</td></tr>
<tr><th>Enabled</th><td>{"✓" if meta["enabled"] else "✗"}</td></tr>
</table>
<form class="inline" method="post" action="/api/tools/toggle">
<input type="hidden" name="name" value="{esc(name)}">
<input type="hidden" name="enabled" value="{0 if meta["enabled"] else 1}">
<button class="btn btn-secondary btn-sm">{"禁用" if meta["enabled"] else "启用"}</button></form></div>

<div class="card"><h3>配置（保存立即生效）</h3>
<form method="post" action="/api/tools/config">
<input type="hidden" name="name" value="{esc(name)}">
<label>Provider</label><select name="provider">{provider_options}</select>
{locator_block}
<label>超时（秒）</label><input name="timeout" value="{esc(meta["timeout"] or "")}">
<label>缓存 TTL（秒，0=不缓存）</label><input name="cache_ttl_seconds" value="{esc(meta["cache_ttl_seconds"])}">
<label>凭据环境变量名（如 TAVILY_API_KEY，可留空）</label>
<input name="api_key_env" value="{esc(settings.get("api_key_env", ""))}">
<label>凭据值（留空表示不修改；只写入本地凭据库，永不回显）</label>
<input name="api_key_value" type="password" value="">
<p class="muted">当前凭据：{esc(credential.get("masked") or "未设置")}（来源：{esc(credential.get("source") or "unset")}）</p>
<p><button class="btn btn-primary">保存</button></p></form></div>

<div class="card"><h3>手动测试（不会发送到 QQ）</h3>
<form method="post" action="/api/tools/test">
<input type="hidden" name="name" value="{esc(name)}">
<label>参数（JSON）</label>
<textarea name="arguments" rows="4">{esc(json.dumps(_sample_arguments(name), ensure_ascii=False))}</textarea>
<p><button class="btn btn-primary">测试</button></p></form></div>

<div class="card"><h3>Schema</h3>
<pre>{esc(json.dumps(meta["input_schema"], ensure_ascii=False, indent=2))}</pre></div>

<div class="card"><h3>指标</h3><pre>{esc(json.dumps(metrics, ensure_ascii=False, indent=2))}</pre>
<form class="inline" method="post" action="/api/tools/cache/clear">
<input type="hidden" name="name" value="{esc(name)}"><button class="btn btn-danger btn-sm">清空缓存</button></form></div>

<div class="card"><h3>最近执行</h3>
<table><tr><th>时间</th><th>状态</th><th>错误</th><th>耗时(ms)</th><th>缓存</th><th>摘要</th></tr>
{exec_rows or '<tr><td colspan="6" class="muted">暂无</td></tr>'}</table></div>"""
        return web.Response(text=layout("工具 · 详情", "/tools", body), content_type="text/html")

    async def _tools_executions_page(self, request: web.Request) -> web.Response:
        tool_name = request.query.get("tool", "")
        rows = await self._tool_admin.executions(limit=200, tool_name=tool_name)
        body_rows = "".join(
            f"<tr><td class='muted'>{esc(format_ts(r['created_at']))}</td>"
            f"<td>{esc(r['tool_name'])}</td><td>{esc(r['user_id'] or '')}</td>"
            f"<td>{esc(r['group_id'] or '')}</td><td>{esc(r['status'])}</td>"
            f"<td>{esc(r['error_type'] or '')}</td><td>{esc(r['duration_ms'])}</td>"
            f"<td>{esc(r['reason'])}</td><td>{esc((r['result_summary'] or '')[:60])}</td></tr>"
            for r in rows
        )
        body = f"""<div class="card"><form method="get" action="/tools/executions">
<label>按工具过滤</label><input name="tool" value="{esc(tool_name)}">
<p><button class="btn btn-primary">筛选</button></p></form>
<p class="muted">参数只记录哈希与前 80 字符预览，凭据永不入库。</p></div>
<div class="card"><h3>执行日志 ({len(rows)})</h3>
<table><tr><th>时间</th><th>Tool</th><th>User</th><th>Group</th><th>状态</th>
<th>错误</th><th>耗时(ms)</th><th>原因</th><th>摘要</th></tr>
{body_rows or '<tr><td colspan="9" class="muted">暂无记录</td></tr>'}</table></div>"""
        return web.Response(
            text=layout("工具 · 执行记录", "/tools", body), content_type="text/html"
        )

    async def _tools_metrics_page(self, request: web.Request) -> web.Response:
        metrics = await self._tool_admin.metrics()
        body = (
            '<div class="card"><h3>Tool Metrics</h3>'
            f"<pre>{esc(json.dumps(metrics, ensure_ascii=False, indent=2))}</pre></div>"
        )
        return web.Response(text=layout("工具 · 指标", "/tools", body), content_type="text/html")

    async def _tools_decision_page(self, request: web.Request) -> web.Response:
        form = await request.post() if request.method == "POST" else {}
        query = str(form.get("q") or request.query.get("q", "")).strip()
        result_html = ""
        if query:
            data = await self._tool_admin.decision_debug(query)
            rows = "".join(
                f"<tr><td>{esc(c['name'])}</td><td>{c['score']}</td>"
                f"<td>{'✓' if c['enabled'] else '✗'}</td><td>{esc(c['risk_level'])}</td></tr>"
                for c in data["candidates"]
            )
            rejected = "".join(
                f"<tr><td>{esc(r['name'])}</td><td>{'启用' if r['enabled'] else '禁用'}</td>"
                f"<td>{esc(r['reason'])}</td></tr>"
                for r in data["rejected"]
            )
            result_html = f"""
<div class="card"><h3>候选工具</h3>
<table><tr><th>Tool</th><th>score</th><th>Enabled</th><th>Risk</th></tr>
{rows or '<tr><td colspan="4" class="muted">没有候选（会直接聊天）</td></tr>'}</table>
<p>Selected: <b>{esc(data["selected"] or "none")}</b>（decision_mode={esc(data["decision_mode"])}）</p></div>
<div class="card"><h3>被排除的候选</h3>
<table><tr><th>Tool</th><th>状态</th><th>原因</th></tr>
{rejected or '<tr><td colspan="3" class="muted">无</td></tr>'}</table></div>
<div class="card"><h3>注入模型的工具说明（预览）</h3><pre>{esc(data["instruction_preview"] or "（无）")}</pre></div>"""
        body = f"""<div class="card"><h3>Tool Decision Debug（仅后台）</h3>
<form method="post" action="/tools/decision-debug">
<label>模拟用户消息</label><input name="q" value="{esc(query)}">
<p><button class="btn btn-primary">分析</button></p></form>
<p class="muted">这里展示的是"会选哪些工具"，不会真的调用或发送任何消息。</p></div>
{result_html}"""
        return web.Response(
            text=layout("工具 · 决策调试", "/tools", body), content_type="text/html"
        )

    async def _tools_permissions_page(self, request: web.Request) -> web.Response:
        rules = await self._tool_admin.permissions()
        tools = [t.metadata.name for t in self._bot.tools.registry.all()]
        tool_options = "".join(f"<option>{esc(name)}</option>" for name in tools)
        rows = "".join(
            f"<tr><td>{esc(r['scope'])}</td><td>{esc(r['ref'])}</td><td>{esc(r['tool_name'])}</td>"
            f"<td>{'允许' if r['allowed'] else '拒绝'}</td>"
            f"<td><form class='inline' method='post' action='/api/tools/permission/clear'>"
            f"<input type='hidden' name='scope' value='{esc(r['scope'])}'>"
            f"<input type='hidden' name='ref' value='{esc(r['ref'])}'>"
            f"<input type='hidden' name='tool' value='{esc(r['tool_name'])}'>"
            f"<button class='btn btn-danger btn-sm'>删除</button></form></td></tr>"
            for r in rules
        )
        body = f"""<div class="card"><h3>新增 / 更新规则</h3>
<form method="post" action="/api/tools/permission">
<label>范围</label><select name="scope">
<option value="user">user</option><option value="group">group</option></select>
<label>对象（用户 QQ / 群号）</label><input name="ref">
<label>工具</label><select name="tool">{tool_options}</select>
<label>策略</label><select name="allowed">
<option value="1">允许</option><option value="0">拒绝</option></select>
<p><button class="btn btn-primary">保存</button></p></form>
<p class="muted">拒绝优先于允许；用户规则优先于群规则。风险等级限制在 config.yaml 的 tools.permissions。</p></div>
<div class="card"><h3>现有规则 ({len(rules)})</h3>
<table><tr><th>Scope</th><th>对象</th><th>Tool</th><th>策略</th><th></th></tr>
{rows or '<tr><td colspan="5" class="muted">暂无规则（默认允许）</td></tr>'}</table></div>"""
        return web.Response(text=layout("工具 · 权限", "/tools", body), content_type="text/html")

    async def _api_tool_toggle(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._tool_admin.set_enabled(
            str(form.get("name", "")), str(form.get("enabled")) == "1"
        )
        raise web.HTTPFound(f"/tools/{form.get('name', '')}")

    async def _api_tool_config(self, request: web.Request) -> web.Response:
        form = await request.post()
        name = str(form.get("name", ""))
        timeout = _as_float(form.get("timeout"))
        cache_ttl = _as_float(form.get("cache_ttl_seconds"))
        max_results = _as_int(form.get("max_results"))
        await self._tool_admin.save_settings(
            name,
            timeout=timeout,
            cache_ttl_seconds=cache_ttl,
            provider=str(form.get("provider", "")),
            default_location=str(form.get("default_location", "")),
            max_results=max_results,
            api_key_env=str(form.get("api_key_env", "")),
            api_key_value=str(form.get("api_key_value", "")),
        )
        raise web.HTTPFound(f"/tools/{name}")

    async def _api_tool_test(self, request: web.Request) -> web.Response:
        form = await request.post()
        name = str(form.get("name", ""))
        raw = str(form.get("arguments", "") or "{}")
        try:
            arguments = json.loads(raw)
        except ValueError as exc:
            result = {"ok": False, "error": f"参数不是合法 JSON: {exc}"}
        else:
            result = await self._tool_admin.test_tool(name, arguments)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        return web.Response(
            text=layout(
                "工具 · 测试",
                "/tools",
                f'<div class="card"><h3>{esc(name)} 测试结果</h3>'
                f"<pre>{esc(rendered)}</pre>"
                f'<p><a href="/tools/{esc(name)}">返回</a></p></div>',
            ),
            content_type="text/html",
        )

    async def _api_tool_cache_clear(self, request: web.Request) -> web.Response:
        form = await request.post()
        name = str(form.get("name", ""))
        await self._tool_admin.clear_cache(name)
        raise web.HTTPFound(f"/tools/{name}")

    async def _api_tool_permission(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._tool_admin.set_permission(
            str(form.get("scope", "user")),
            str(form.get("ref", "")),
            str(form.get("tool", "")),
            str(form.get("allowed", "1")) == "1",
        )
        raise web.HTTPFound("/tools/permissions")

    async def _api_tool_permission_clear(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._tool_admin.clear_permission(
            str(form.get("scope", "user")),
            str(form.get("ref", "")),
            str(form.get("tool", "")),
        )
        raise web.HTTPFound("/tools/permissions")

    def register_tools(self, app: web.Application) -> None:
        app.router.add_get("/tools", self._tools_page)
        app.router.add_get("/tools/{name}", self._tool_detail_page)
        app.router.add_get("/tools/executions", self._tools_executions_page)
        app.router.add_get("/tools/metrics", self._tools_metrics_page)
        app.router.add_get("/tools/decision-debug", self._tools_decision_page)
        app.router.add_post("/tools/decision-debug", self._tools_decision_page)
        app.router.add_get("/tools/permissions", self._tools_permissions_page)
        app.router.add_post("/api/tools/toggle", self._api_tool_toggle)
        app.router.add_post("/api/tools/config", self._api_tool_config)
        app.router.add_post("/api/tools/test", self._api_tool_test)
        app.router.add_post("/api/tools/cache/clear", self._api_tool_cache_clear)
        app.router.add_post("/api/tools/permission", self._api_tool_permission)
        app.router.add_post("/api/tools/permission/clear", self._api_tool_permission_clear)
