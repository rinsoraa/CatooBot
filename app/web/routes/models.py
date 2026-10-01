"""Model router page + per-model override (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from app.web.routes.base import WebContext, esc, layout


class ModelRoutes(WebContext):
    """Handlers for this domain."""

    async def _models_page(self, request: web.Request) -> web.Response:
        models = self._bot.ai.router.snapshot()
        providers = list(self._bot.ai._providers)
        rows = "".join(
            f"""<tr><td>{esc(m["name"])}</td>
<td><details><summary class="muted">修改</summary>
<form method="post" action="/api/models/override">
<input type="hidden" name="name" value="{esc(m["name"])}">
<label>Provider</label><select name="provider">{"".join(f"<option>{esc(p)}</option>" for p in providers)}</select>
<label>Model ID</label><input name="model" value="{esc(m["model"])}">
<label>Priority</label><input name="priority" value="{i}">
<label>Enabled</label><select name="enabled"><option value="1">启用</option><option value="0">禁用</option></select>
<p><button class="btn btn-secondary btn-sm">应用（热更新）</button></p></form></details></td>
<td>{esc(m["provider"])}</td><td>{esc(m["model"])}</td><td>{"✓" if m["enabled"] else "✗"}</td>
<td>{"❄" if m["in_cooldown"] else ""}</td><td>{m["failure_count"]}</td><td class="muted">{esc(m["last_error"] or "")}</td></tr>"""
            for i, m in enumerate(models)
        )
        usage_rows = ""
        usage_recorder = getattr(self._bot, "ai_usage", None)
        if usage_recorder is not None:
            days = int(self._bot.config.ai.usage.retention_days)
            usage = await usage_recorder.summary(days=min(days, 7))
            usage_rows = (
                "".join(
                    f"""<tr><td>{esc(u["model"])}</td><td>{u["calls"]}</td>
<td>{u["failures"]}</td><td>{u["prompt_tokens"]:,}</td>
<td>{u["completion_tokens"]:,}</td><td>{u["avg_latency_ms"]:.0f} ms</td>
<td>{u["max_latency_ms"]:.0f} ms</td></tr>"""
                    for u in usage
                )
                or '<tr><td colspan="7" class="muted">还没有调用记录。</td></tr>'
            )
        body = f"""<div class="card"><h3>Model Router（修改立即生效，无需重启）</h3>
<table><tr><th>Name</th><th>Edit</th><th>Provider</th><th>Model</th><th>Enabled</th><th>Cooldown</th><th>Fails</th><th>Last Error</th></tr>
{rows or '<tr><td colspan="8" class="muted">No models.</td></tr>'}</table></div>
<div class="card"><h3>用量（近 7 天，按模型）</h3>
<table><tr><th>模型</th><th>调用</th><th>失败</th><th>Prompt tokens</th><th>Completion tokens</th><th>平均耗时</th><th>最慢</th></tr>
{usage_rows}</table>
<p class="muted">每次模型调用一行（含失败与重试）；保留 {esc(self._bot.config.ai.usage.retention_days)} 天后自动清理。</p></div>"""
        return web.Response(
            text=layout("模型", "/models", body, subtitle="模型优先级、启用状态与故障转移情况"),
            content_type="text/html",
        )

    async def _api_model_override(self, request: web.Request) -> web.Response:
        form = await request.post()
        changes: dict[str, Any] = {}
        if form.get("model"):
            changes["model"] = str(form["model"]).strip()
        if form.get("provider"):
            changes["provider"] = str(form["provider"]).strip()
        if form.get("enabled") is not None:
            changes["enabled"] = str(form["enabled"]) == "1"
        if form.get("priority"):
            try:
                changes["priority"] = int(str(form["priority"]))
            except ValueError:
                pass
        await self._admin.apply_model_override(str(form.get("name", "")), **changes)
        raise web.HTTPFound("/models")

    def register_models(self, app: web.Application) -> None:
        app.router.add_get("/models", self._models_page)
        app.router.add_post("/api/models/override", self._api_model_override)
