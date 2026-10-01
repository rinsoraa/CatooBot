"""Agent runtime pages: tasks, simulator, policy, metrics (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

import json

from aiohttp import web

from app.web.routes.base import WebContext, esc, format_ts, layout


class AgentRoutes(WebContext):
    """Handlers for this domain."""

    async def _agent_page(self, request: web.Request) -> web.Response:
        data = await self._agent_admin.dashboard()
        metrics = data.get("metrics", {})
        policy = data.get("policy", {})
        health = data.get("health", {})
        cards = "".join(
            f'<div class="stat"><span class="muted">{esc(label)}</span><b>{esc(value)}</b></div>'
            for label, value in (
                ("Active", metrics.get("active", 0)),
                ("已完成", metrics.get("completed", 0)),
                ("部分完成", metrics.get("partial_rate", 0)),
                ("Failed", metrics.get("failed", 0)),
                ("已取消", metrics.get("cancelled", 0)),
                ("Paused", metrics.get("paused", 0)),
                ("总计", metrics.get("total", 0)),
                ("成功率", metrics.get("success_rate", 0)),
                ("平均步数", metrics.get("avg_steps", 0)),
                ("平均工具调用", metrics.get("avg_tool_calls", 0)),
                ("Avg Duration (ms)", metrics.get("avg_duration_ms", 0)),
                ("重规划率", metrics.get("replan_rate", 0)),
            )
        )
        rows = "".join(
            f"<tr><td><a href='/agent/tasks/{esc(t['task_id'])}'>{esc(t['task_id'])}</a></td>"
            f"<td>{esc(t['classification'])}</td><td>{esc(t['status'])}</td>"
            f"<td>{esc(t.get('user_id') or '')}</td><td>{esc(t.get('step_count') or 0)}</td>"
            f"<td>{esc(t.get('tool_calls') or 0)}</td><td>{esc(t.get('replans') or 0)}</td>"
            f"<td class='muted'>{esc(format_ts(t.get('updated_at')))}</td>"
            f"<td>{esc((t.get('result_summary') or '')[:60])}</td></tr>"
            for t in data.get("recent", [])
        )
        health_cards = "".join(
            f'<div class="stat"><span class="muted">{esc(name)}</span><b>{esc(value)}</b></div>'
            for name, value in health.items()
        )
        body = f"""
<div class="card"><p class="muted">Agent 只在多步任务时介入；QQ 用户只会看到自然回复。
{"" if policy.get("enabled") else "⚠️ Agent 当前未启用（config.yaml 的 agent.enabled）"}</p></div>
<div class="grid">{cards}</div>
<div class="card"><h3>最近任务</h3>
<table><tr><th>Task</th><th>类型</th><th>状态</th><th>User</th><th>Steps</th><th>Tools</th>
<th>Replans</th><th>更新</th><th>结果</th></tr>
{rows or '<tr><td colspan="9" class="muted">暂无任务</td></tr>'}</table>
<p><a href="/agent/tasks">全部任务</a> · <a href="/agent/simulator">Simulator</a> ·
<a href="/agent/policy">Policy</a> · <a href="/agent/metrics">Metrics</a></p></div>
<div class="card"><h3>组件健康</h3><div class="grid">{health_cards}</div></div>"""
        return web.Response(text=layout("Agent", "/agent", body), content_type="text/html")

    async def _agent_tasks_page(self, request: web.Request) -> web.Response:
        status = request.query.get("status", "")
        tasks = await self._agent_admin.tasks(status=status, limit=200)
        options = "".join(
            f'<option value="{s}" {"selected" if s == status else ""}>{s or "全部"}</option>'
            for s in (
                "",
                "created",
                "planning",
                "ready",
                "running",
                "paused",
                "replanning",
                "completed",
                "failed",
                "cancelled",
                "expired",
            )
        )
        rows = "".join(
            f"<tr><td><a href='/agent/tasks/{esc(t['task_id'])}'>{esc(t['task_id'])}</a></td>"
            f"<td>{esc(t['classification'])}</td><td>{esc(t['status'])}</td>"
            f"<td>{esc(t.get('result_status') or '')}</td>"
            f"<td>{esc(t.get('user_id') or '')}</td><td>{esc(t.get('group_id') or '')}</td>"
            f"<td>{esc(t.get('step_count') or 0)}</td><td>{esc(t.get('tool_calls') or 0)}</td>"
            f"<td>{esc(t.get('replans') or 0)}</td>"
            f"<td class='muted'>{esc(format_ts(t.get('created_at')))}</td>"
            f"<td>{esc(round(t.get('duration_ms') or 0))}</td></tr>"
            for t in tasks
        )
        body = f"""<div class="card"><form method="get" action="/agent/tasks">
<label>状态</label><select name="status">{options}</select>
<p><button class="btn btn-primary">筛选</button></p></form></div>
<div class="card"><h3>Tasks ({len(tasks)})</h3>
<table><tr><th>Task</th><th>类型</th><th>状态</th><th>结果</th><th>User</th><th>Group</th>
<th>Steps</th><th>Tools</th><th>Replans</th><th>创建</th><th>耗时(ms)</th></tr>
{rows or '<tr><td colspan="11" class="muted">暂无任务</td></tr>'}</table></div>"""
        return web.Response(text=layout("Agent · 任务", "/agent", body), content_type="text/html")

    async def _agent_task_detail_page(self, request: web.Request) -> web.Response:
        task_id = request.match_info["task_id"]
        detail = await self._agent_admin.task_detail(task_id)
        task = detail.get("task")
        if not task:
            return web.Response(
                text=layout("Agent", "/agent", '<div class="card">任务不存在</div>'),
                content_type="text/html",
            )
        goal = detail.get("goal") or {}
        plans = detail.get("plans") or []
        steps = detail.get("steps") or []
        observations = {obs["step_id"]: obs for obs in detail.get("observations") or []}
        traces = detail.get("traces") or []

        def control_button(action: str, label: str, danger: bool = False) -> str:
            css = "btn btn-danger btn-sm" if danger else "btn btn-secondary btn-sm"
            return (
                f"<form class='inline' method='post' action='/api/agent/control'>"
                f"<input type='hidden' name='task_id' value='{esc(task_id)}'>"
                f"<input type='hidden' name='action' value='{action}'>"
                f"<button class='{css}'>{label}</button></form>"
            )

        plan_blocks = ""
        for plan in plans:
            step_rows = "".join(
                f"<tr><td>{esc(step.get('id'))}</td><td>{esc(step.get('description'))}</td>"
                f"<td>{esc(step.get('tool') or '（分析）')}</td>"
                f"<td>{esc(', '.join(step.get('depends_on') or []) or '-')}</td>"
                f"<td>{esc(json.dumps(step.get('arguments') or {}, ensure_ascii=False)[:80])}</td></tr>"
                for step in plan.get("steps") or []
            )
            plan_blocks += (
                f"<div class='card'><h3>Plan v{plan['version']}（{esc(plan['status'])}）</h3>"
                f"<p class='muted'>{esc(plan.get('reason') or '')}</p>"
                f"<p>{esc(plan.get('summary') or '')}</p>"
                f"<p class='muted'>完成条件：{esc('；'.join(plan.get('criteria') or []) or '-')}</p>"
                "<table><tr><th>Step</th><th>说明</th><th>工具</th><th>依赖</th><th>参数</th></tr>"
                + (step_rows or '<tr><td colspan="5" class="muted">无步骤</td></tr>')
                + "</table></div>"
            )

        step_rows = "".join(
            f"<tr><td>{esc(step['step_id'])}</td><td>{esc(step.get('description') or '')}</td>"
            f"<td>{esc(step.get('tool_name') or '（分析）')}</td><td>{esc(step['status'])}</td>"
            f"<td>{esc(round(step.get('duration_ms') or 0))}</td>"
            f"<td>{esc(step.get('error_type') or '')}</td>"
            f"<td>{esc((step.get('observation_summary') or '')[:80])}</td></tr>"
            for step in steps
        )
        trace_rows = "".join(
            f"<tr><td class='muted'>{esc(format_ts(trace.get('created_at')))}</td>"
            f"<td>{esc(trace.get('level'))}</td><td>{esc(trace.get('event'))}</td>"
            f"<td>{esc((trace.get('detail') or '')[:100])}</td></tr>"
            for trace in traces
        )
        obs_rows = "".join(
            f"<tr><td>{esc(obs.get('step_id'))}</td>"
            f"<td>{'✓' if obs.get('success') else '✗'}</td>"
            f"<td>{esc(obs.get('source') or '')}</td>"
            f"<td>{esc((obs.get('summary') or '')[:120])}</td></tr>"
            for obs in observations.values()
        )
        facts = "".join(f"<li>{esc(fact)}</li>" for fact in task.get("facts") or [])
        unresolved = "".join(f"<li>{esc(item)}</li>" for item in task.get("unresolved") or [])
        sources = "".join(f"<li>{esc(item)}</li>" for item in task.get("sources") or [])

        body = f"""
<div class="card"><h3>Task {esc(task_id)}</h3>
<table>
<tr><th>目标</th><td>{esc(goal.get("description") or "")}</td></tr>
<tr><th>状态</th><td>{esc(task["status"])}（结果：{esc(task.get("result_status") or "-")}）</td></tr>
<tr><th>类型 / 用户</th><td>{esc(task["classification"])} / {esc(task.get("user_id") or "")}</td></tr>
<tr><th>步骤 / 工具调用 / 重规划</th><td>{esc(task.get("step_count") or 0)} / {esc(task.get("tool_calls") or 0)} / {esc(task.get("replans") or 0)}</td></tr>
<tr><th>耗时</th><td>{esc(round(task.get("duration_ms") or 0))} ms</td></tr>
<tr><th>错误</th><td>{esc(task.get("error_type") or "-")}</td></tr>
</table>
<p>{control_button("pause", "暂停")}{control_button("resume", "继续")}
{control_button("cancel", "取消", danger=True)}{control_button("retry", "重试（重新执行）")}
{control_button("replay", "回放（仅重放规划，不发送）")}</p></div>

<div class="card"><h3>结果</h3>
<p>{esc(task.get("result_summary") or "（无）")}</p>
<p class="muted">已确认信息</p><ul>{facts or '<li class="muted">无</li>'}</ul>
<p class="muted">未完成</p><ul>{unresolved or '<li class="muted">无</li>'}</ul>
<p class="muted">来源</p><ul>{sources or '<li class="muted">无</li>'}</ul></div>

{plan_blocks}

<div class="card"><h3>步骤执行</h3>
<table><tr><th>Step</th><th>说明</th><th>工具</th><th>状态</th><th>耗时(ms)</th><th>错误</th><th>观察</th></tr>
{step_rows or '<tr><td colspan="7" class="muted">尚无执行记录</td></tr>'}</table></div>

<div class="card"><h3>Observations</h3>
<table><tr><th>Step</th><th>成功</th><th>来源</th><th>摘要</th></tr>
{obs_rows or '<tr><td colspan="4" class="muted">无</td></tr>'}</table></div>

<div class="card"><h3>Trace（结构化事件，不含模型思维链）</h3>
<table><tr><th>时间</th><th>级别</th><th>事件</th><th>详情</th></tr>
{trace_rows or '<tr><td colspan="4" class="muted">无</td></tr>'}</table></div>"""
        return web.Response(
            text=layout("Agent · 任务详情", "/agent", body), content_type="text/html"
        )

    async def _agent_simulator_page(self, request: web.Request) -> web.Response:
        form = await request.post() if request.method == "POST" else {}
        query = str(form.get("q") or request.query.get("q", "")).strip()
        result_html = ""
        if query:
            payload = await self._agent_admin.simulate(
                query, execute=str(form.get("execute")) == "1"
            )
            result_html = (
                '<div class="card"><h3>模拟结果（不会执行外部请求）</h3>'
                f"<pre>{esc(json.dumps(payload, ensure_ascii=False, indent=2))}</pre></div>"
            )
        body = f"""<div class="card"><h3>Agent Simulator</h3>
<form method="post" action="/agent/simulator">
<label>用户消息</label><input name="q" value="{esc(query)}">
<label><input type="checkbox" name="execute" value="1" style="width:auto"> 同时做 Dry Run（Planner 真实，工具 Mock）</label>
<p><button class="btn btn-primary">模拟</button></p></form>
<p class="muted">只做分类与规划，不会调用任何工具，也不会发送 QQ 消息。</p></div>
{result_html}"""
        return web.Response(text=layout("Agent · 模拟器", "/agent", body), content_type="text/html")

    async def _agent_policy_page(self, request: web.Request) -> web.Response:
        policy = self._agent_admin.runtime.policy_snapshot()
        body = (
            '<div class="card"><h3>Agent Policy</h3>'
            f"<pre>{esc(json.dumps(policy, ensure_ascii=False, indent=2))}</pre>"
            "<p class='muted'>修改请在 config/config.yaml 的 agent: 小节进行；"
            "Agent 永远无法绕过 Tool Policy / Permission / Budget。</p></div>"
        )
        return web.Response(text=layout("Agent · 策略", "/agent", body), content_type="text/html")

    async def _agent_metrics_page(self, request: web.Request) -> web.Response:
        metrics = await self._agent_admin.runtime.metrics()
        body = (
            '<div class="card"><h3>Agent Metrics</h3>'
            f"<pre>{esc(json.dumps(metrics, ensure_ascii=False, indent=2))}</pre></div>"
        )
        return web.Response(text=layout("Agent · 指标", "/agent", body), content_type="text/html")

    async def _api_agent_control(self, request: web.Request) -> web.Response:
        form = await request.post()
        task_id = str(form.get("task_id", ""))
        action = str(form.get("action", ""))
        result = await self._agent_admin.control(action, task_id)
        return web.Response(
            text=layout(
                "Agent",
                "/agent",
                f'<div class="card"><h3>{esc(action)}</h3>'
                f"<pre>{esc(json.dumps(result, ensure_ascii=False, indent=2))}</pre>"
                f'<p><a href="/agent/tasks/{esc(task_id)}">返回任务</a></p></div>',
            ),
            content_type="text/html",
        )

    def register_agent(self, app: web.Application) -> None:
        app.router.add_get("/agent", self._agent_page)
        app.router.add_get("/agent/tasks", self._agent_tasks_page)
        app.router.add_get("/agent/tasks/{task_id}", self._agent_task_detail_page)
        app.router.add_get("/agent/simulator", self._agent_simulator_page)
        app.router.add_post("/agent/simulator", self._agent_simulator_page)
        app.router.add_get("/agent/policy", self._agent_policy_page)
        app.router.add_get("/agent/metrics", self._agent_metrics_page)
        app.router.add_post("/api/agent/control", self._api_agent_control)
