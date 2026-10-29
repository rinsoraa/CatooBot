"""Sticker library + tool credentials (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

from urllib.parse import quote

from aiohttp import web

from app.web import ui
from app.web.routes.base import WebContext, esc, layout


class MediaRoutes(WebContext):
    """Handlers for this domain."""

    async def _stickers_page(self, request: web.Request) -> web.Response:
        query = request.query.get("q", "")
        emotion = request.query.get("emotion", "")
        intent = request.query.get("intent", "")
        msg = request.query.get("msg", "")
        stickers = await self._sticker_admin.list_stickers(
            query=query, emotion=emotion, intent=intent
        )
        stats = await self._sticker_admin.stats()

        if not stats.get("enabled"):
            body = ui.card(
                "表情包库未启用",
                "<p class='muted'>在 config 打开 <code>media.enabled</code>。</p>",
            )
            return web.Response(
                text=layout("表情", "/stickers", body, subtitle="角色的表情资产库"),
                content_type="text/html",
            )

        def tag_badges(tags: list[str]) -> str:
            return (
                " ".join(ui.badge(tag, "info") for tag in (tags or [])[:6])
                or '<span class="muted">-</span>'
            )

        rows = "".join(
            f"<tr><td>{esc(s['file_name'] or s['id'])}</td>"
            f"<td>{tag_badges(s['emotion_tags'])}</td>"
            f"<td>{tag_badges(s['intent_tags'])}</td>"
            f"<td>{esc((s['visual_summary'] or '')[:40])}</td>"
            f"<td>{s['usage_count']}</td>"
            f"<td>{ui.badge('正常', 'success') if s['status'] == 'active' else ui.badge(s['status'], 'default')}</td>"
            f"<td>"
            f"<form class='inline' method='post' action='/api/stickers/disable/{s['id']}'>"
            f"<button class='btn btn-secondary btn-sm'>禁用</button></form> "
            f"<form class='inline' method='post' action='/api/stickers/enable/{s['id']}'>"
            f"<button class='btn btn-secondary btn-sm'>启用</button></form> "
            f"<form class='inline' method='post' action='/api/stickers/delete/{s['id']}' data-confirm='确定删除这个表情吗？'>"
            f"<button class='btn btn-danger btn-sm'>删除</button></form>"
            f"</td></tr>"
            for s in stickers
        )
        tiles = ui.stats_grid(
            [
                ("表情总数", stats["total"], "已收藏、可用的表情"),
                ("已禁用", stats["disabled"], ""),
                ("原生表情", stats["native_faces"], "QQ 原生 face，可低成本复用"),
                (
                    "视觉识别",
                    "可用" if stats["vision_enabled"] else "未配置",
                    "是否配置了视觉模型（未配置时表情只靠文字摘要打标签）",
                ),
            ]
        )
        filters = (
            "<form method='get' action='/stickers'><div class='grid'>"
            + ui.field("搜索", "q", query, tip_text="按描述/标签模糊搜索")
            + ui.field("情绪", "emotion", emotion, tip_text="如 开心 / 难过 / 生气 / 无语")
            + ui.field("意图", "intent", intent, tip_text="如 吐槽 / 安慰 / 回应")
            + "</div><p><button class='btn btn-secondary' type='submit'>筛选</button></p></form>"
        )
        table = ui.table(
            ["名称", "情绪", "意图", "描述", "使用", "状态", ""],
            rows
            or '<tr><td colspan="7" class="muted">还没有表情包，把文件放进 data/stickers 或等她自动收藏</td></tr>',
            tips=[
                "文件名/emoji",
                "情绪标签",
                "意图标签",
                "视觉摘要",
                "被使用的次数",
                "状态",
                "禁用/启用/删除",
            ],
            empty="还没有表情包",
            table_id="stickers",
        )
        controls = (
            "<form class='inline' method='post' action='/api/stickers/reindex'>"
            "<button class='btn btn-secondary btn-sm' type='submit' data-tip='重新扫描并分析 data/stickers 下的所有文件'>重建索引</button></form>"
        )
        body = (
            ui.flash("ok", msg)
            + tiles
            + ui.card(
                "表情包库",
                controls + filters + table,
                tip_text="表情是角色的表达资产（≠ 记忆）；普通图片永远不会进入这里",
            )
            + '<p class="hint">手动导入：把文件放进 <code>data/stickers/</code> 后点「重建索引」。'
            "收到用户 mface/face 时她会后台自行判断是否收藏，不需要任何命令。</p>"
        )
        return web.Response(
            text=layout("表情", "/stickers", body, subtitle="角色的表情资产库"),
            content_type="text/html",
        )

    async def _api_sticker_action(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        sticker_id = request.match_info["sticker_id"]
        if action == "disable":
            await self._sticker_admin.set_status(sticker_id, "disabled")
        elif action == "enable":
            await self._sticker_admin.set_status(sticker_id, "active")
        elif action == "delete":
            await self._sticker_admin.set_status(sticker_id, "archived")
        raise web.HTTPFound("/stickers")

    async def _api_sticker_reindex(self, request: web.Request) -> web.Response:
        await self._sticker_admin.reindex()
        raise web.HTTPFound("/stickers?msg=" + quote("已重新建立索引（后台完成）"))

    async def _credentials_page(self, request: web.Request) -> web.Response:
        credentials = await self._tool_admin.credentials()
        rows = "".join(
            f"<tr><td>{esc(c['name'])}</td><td>{esc(c['masked'] or '—')}</td>"
            f"<td>{esc(c['source'])}</td>"
            f"<td><form class='inline' method='post' action='/api/credentials/delete'>"
            f"<input type='hidden' name='name' value='{esc(c['name'])}'>"
            f"<button class='btn btn-danger btn-sm'>删除</button></form></td></tr>"
            for c in credentials
        )
        body = f"""<div class="card"><h3>凭据（只写不读）</h3>
<form method="post" action="/api/credentials">
<label>变量名（与工具配置中的 api_key_env 一致）</label><input name="name" placeholder="TAVILY_API_KEY">
<label>值</label><input name="value" type="password">
<p><button class="btn btn-primary">保存</button></p></form>
<p class="muted">值写入 data/secrets.json（不在数据库中，也不会回显）；环境变量优先级更高。
删除仅影响本地凭据库，不影响 .env。</p></div>
<div class="card"><h3>已知凭据 ({len(credentials)})</h3>
<table><tr><th>名称</th><th>值</th><th>来源</th><th></th></tr>
{rows or '<tr><td colspan="4" class="muted">暂无</td></tr>'}</table></div>"""
        return web.Response(text=layout("凭据", "/tools", body), content_type="text/html")

    async def _api_credential_set(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._tool_admin.set_credential(
            str(form.get("name", "")).strip(), str(form.get("value", ""))
        )
        raise web.HTTPFound("/credentials")

    async def _api_credential_delete(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._tool_admin.delete_credential(str(form.get("name", "")))
        raise web.HTTPFound("/credentials")

    def register_media(self, app: web.Application) -> None:
        app.router.add_get("/stickers", self._stickers_page)
        app.router.add_post("/api/stickers/{action}/{sticker_id}", self._api_sticker_action)
        app.router.add_post("/api/stickers/reindex", self._api_sticker_reindex)
        app.router.add_get("/credentials", self._credentials_page)
        app.router.add_post("/api/credentials", self._api_credential_set)
        app.router.add_post("/api/credentials/delete", self._api_credential_delete)
