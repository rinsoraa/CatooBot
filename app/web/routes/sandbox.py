"""Sandbox pages + the three conversation-behaviour cards (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from aiohttp import web

from app.web import ui
from app.web.pages import sandbox_page
from app.web.routes.base import WebContext, esc, format_ts, layout


class SandboxRoutes(WebContext):
    """Handlers for this domain."""

    async def _behavior_legacy_redirect(self, request: web.Request) -> web.Response:
        raise web.HTTPFound("/sandbox/chat")

    async def _sandbox_chat_page(self, request: web.Request) -> web.Response:
        """Chat-surface behaviour, merged into the sandbox area (three parts).

        Her *life* belongs to the sandbox; these settings govern the three
        conversation surfaces: private replies, group participation and
        proactive messaging.
        """
        data = await self._behavior.dashboard()
        cfg = self._bot.config.behavior
        initiative = data["initiative"]
        scheduler = data["scheduler"]

        private_card = ui.card(
            "私聊回复",
            "<p class='hint'>有人私聊她时的节奏：延迟、分段与作息门控。"
            "她“正在做什么”由生活沙盒决定，这里只管回话方式。</p>"
            "<div class='grid'>"
            + ui.switch(
                "reply_enabled",
                cfg.reply.enabled,
                "启用延迟回复",
                tip_text="关掉后总是秒回；开着则按下方区间随机延迟",
            )
            + ui.field(
                "最小延迟（秒）", "min_delay", cfg.reply.min_delay, tip_text="最短等多久再回"
            )
            + ui.field(
                "最大延迟（秒）", "max_delay", cfg.reply.max_delay, tip_text="最长等多久再回"
            )
            + ui.switch(
                "chunking_enabled",
                cfg.chunking.enabled,
                "启用自然分段",
                tip_text="偶尔把一段回复拆成几条，更像真人",
            )
            + ui.field(
                "分段概率",
                "chunk_probability",
                cfg.chunking.chunk_probability,
                tip_text="0~1，越大越常拆",
            )
            + ui.field(
                "最多段数", "max_chunks", cfg.chunking.max_chunks, tip_text="一条回复最多拆成几段"
            )
            + "</div>"
            "<div class='grid'>"
            + ui.switch(
                "sleep_enabled",
                cfg.schedule.sleep_enabled,
                "启用睡眠时段",
                tip_text="这个时段回复更慢更困，也不主动说话",
            )
            + ui.field("入睡", "sleep_start", cfg.schedule.sleep_start, tip_text="HH:MM")
            + ui.field("起床", "sleep_end", cfg.schedule.sleep_end, tip_text="HH:MM")
            + ui.switch(
                "dnd_enabled",
                cfg.schedule.dnd_enabled,
                "启用免打扰",
                tip_text="这个时段不主动、也可以不回应",
            )
            + ui.field("免打扰开始", "dnd_start", cfg.schedule.dnd_start, tip_text="HH:MM")
            + ui.field("免打扰结束", "dnd_end", cfg.schedule.dnd_end, tip_text="HH:MM")
            + ui.switch(
                "dnd_blocks_replies",
                cfg.schedule.dnd_blocks_replies,
                "免打扰也阻止被动回复",
                tip_text="关闭时只是不主动，别人叫她还是回",
            )
            + "</div>",
            tip_text="私聊永远会回（除非免打扰配置成不回）；这里调的是“怎么回”",
        )

        group_card = ui.card(
            "群聊参与",
            "<p class='hint'>@ 她 / 回复她 → 必回；下面只管“没 @ 她”的自主插话。"
            "先由社交认知做结构化判断（话题相关、能补充、时机合适就参与）。</p>"
            "<div class='grid'>"
            + ui.switch(
                "participation_enabled",
                cfg.group.participation_enabled,
                "允许非 @ 插话",
                tip_text="关掉后群里只有 @ / 回复她才会说话",
            )
            + ui.field(
                "参与频率",
                "participation_probability",
                cfg.group.participation_probability,
                tip_text="确定性的额度累积：每条合格消息攒这么多额度，攒满 1 参与一次。"
                "1.0 ≈ 每条都参与（仍受冷却/每日额度约束）；0 = 完全交给社交认知",
            )
            + ui.field(
                "最短消息长度",
                "min_message_length",
                cfg.group.min_message_length,
                tip_text="短于此长度的群消息不参与判断（“哈哈哈”这种）",
            )
            + "</div>"
            "<p class='hint'>冷却、每日上限、观察批次与阈值在 "
            "<a href='/social/policy'>社交策略</a> 页；单个群的参与开关在 "
            "<a href='/groups'>群组</a> 页。</p>",
            tip_text="群聊参与 = 社交认知判断 + 这里的频率/长度控制",
        )

        initiative_card = ui.card(
            "主动回复",
            "<p class='hint'>没人找她时，她会不会主动发消息（会被多重硬限制拦住）。"
            "她在生活里发生的事（做完的事、零工、快递）会作为话题来源。</p>"
            "<div class='grid'>"
            + ui.switch(
                "initiative_enabled",
                cfg.initiative.enabled,
                "启用主动聊天",
                tip_text="总开关；下面所有硬性上限仍然会拦",
            )
            + ui.field(
                "最小间隔（分）",
                "min_interval_minutes",
                cfg.initiative.min_interval_minutes,
                tip_text="两次主动之间至少隔多久",
            )
            + ui.field("每日上限", "daily_limit", cfg.initiative.daily_limit, tip_text="")
            + ui.field("每小时上限", "hourly_limit", cfg.initiative.hourly_limit, tip_text="")
            + ui.field(
                "闲置（小时）",
                "idle_hours",
                cfg.initiative.idle_hours,
                tip_text="对方多久没说话才主动",
            )
            + ui.select(
                "最低关系",
                "min_relationship_stage",
                [
                    ("new", "new"),
                    ("familiar", "familiar"),
                    ("close", "close"),
                    ("very_close", "very_close"),
                ],
                cfg.initiative.min_relationship_stage,
                tip_text="关系熟到这个程度才主动",
            )
            + ui.field(
                "未回复上限",
                "max_unanswered",
                cfg.initiative.max_unanswered,
                tip_text="主动发了没回，达到上限就不再追问",
            )
            + "</div>"
            "<p class='hint'>后台消息额度在沙盒配置 <code>sandbox."
            "max_background_messages_per_day</code>。</p>",
            tip_text="主动消息 ≠ 后台生活：她在生活，但主动找人要过这道门",
        )

        settings_body = (
            "<form method='post' action='/behavior/settings'>"
            + private_card
            + group_card
            + initiative_card
            + "<p><button class='btn btn-primary' type='submit' "
            "data-tip='保存并热加载，无需重启'>保存设置</button></p></form>"
        )

        manual_html = (
            '<form class="inline" method="post" action="/behavior/test-response">'
            '<input name="text" placeholder="测试一句用户消息" style="width:340px;display:inline-block">'
            '<button class="btn btn-secondary btn-sm" type="submit">测试回复（生成不发送）</button></form>'
            '<div style="margin-top:12px" class="toolbar">'
            '<form class="inline" method="post" action="/behavior/trigger/mood_up">'
            '<button class="btn btn-secondary btn-sm" type="submit">心情 +1</button></form>'
            '<form class="inline" method="post" action="/behavior/trigger/mood_down">'
            '<button class="btn btn-secondary btn-sm" type="submit">心情 -1</button></form>'
            '<form class="inline" method="post" action="/behavior/trigger/reset_state">'
            '<button class="btn btn-danger btn-sm" type="submit">重置状态</button></form>'
            '<form class="inline" method="post" action="/behavior/trigger/test_initiative">'
            '<button class="btn btn-secondary btn-sm" type="submit">测试主动性（评估不发送）</button></form>'
            "</div>"
        )

        simulator_html = (
            "<form method='post' action='/behavior/preview'>"
            + "<div class='grid'>"
            + ui.field("时间", "sim_time", "23:50")
            + ui.field("心情", "mood", "relaxed")
            + ui.field("参考活动", "activity", "reading")
            + ui.select(
                "关系",
                "relationship",
                [
                    ("new", "new"),
                    ("familiar", "familiar"),
                    ("close", "close"),
                    ("very_close", "very_close"),
                ],
                "familiar",
            )
            + ui.field("话题", "topic", "未完成的项目")
            + "</div>"
            + ui.textarea("示例回复文本", "sample_reply", "好呀。\n\n等我看一下再说。", rows=3)
            + "<p><button class='btn btn-primary' type='submit' "
            "data-tip='只预览，不会发送到 QQ'>预览</button></p></form>"
        )

        events = data["recent_events"]
        event_rows = "".join(
            f"<tr><td class='muted'>{esc(format_ts(e['created_at']))}</td><td>{esc(e['type'])}</td>"
            f"<td>{esc(e['scope_key'] or '')}</td><td>{esc(e['reason'])}</td>"
            f"<td>{esc((e['detail'] or '')[:80])}</td></tr>"
            for e in events
        )
        events_card = ui.table(
            ["时间", "类型", "Scope", "原因", "详情"],
            event_rows or '<tr><td colspan="5" class="muted">暂无行为记录</td></tr>',
            tips=["发生时间", "事件类型", "所属会话", "触发原因", "详情"],
            empty="暂无行为记录",
            table_id="behavior-events",
            filterable=False,
        )

        stats = ui.stats_grid(
            [
                ("心情", data["state"].get("mood") or "-", "当前心情阶梯"),
                ("时段", data["time"]["period"], "角色时区的当前时段"),
                ("睡觉中", "是" if data["time"]["is_sleeping"] else "否", "由作息门控"),
                ("勿扰", "是" if data["time"]["in_dnd"] else "否", ""),
                ("调度 tick", scheduler["ticks"], "共享调度器执行次数"),
                ("今日主动", initiative.get("sent_today_total", 0), "主动消息今日已发"),
            ]
        )

        body = (
            sandbox_page._tabs("/sandbox/chat")
            + stats
            + settings_body
            + ui.card("手动触发", manual_html, tip_text="只在这里后台执行，不会发到 QQ")
            + ui.card("回复预演", simulator_html, tip_text="预览延迟与分段，不会发送")
            + ui.card("最近行为记录", events_card, tip_text="行为引擎做了什么、为什么")
        )
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                body,
                subtitle="私聊回复 / 群聊参与 / 主动回复（她怎么做人，由沙盒决定）",
            ),
            content_type="text/html",
        )

    async def _behavior_settings_save(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._behavior.save_settings(dict(form))
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                '<div class="card">设置已保存并热加载</div>'
                '<meta http-equiv="refresh" content="1;url=/sandbox/chat">',
            ),
            content_type="text/html",
        )

    async def _behavior_preview(self, request: web.Request) -> web.Response:
        form = await request.post()
        result = await self._behavior.preview(dict(form))
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                f'<div class="card"><h3>Preview 结果（未发送任何消息）</h3><pre>{esc(rendered)}</pre>'
                '<p><a href="/sandbox/chat">返回</a></p></div>',
            ),
            content_type="text/html",
        )

    async def _behavior_test_response(self, request: web.Request) -> web.Response:
        form = await request.post()
        result = await self._behavior.test_response(str(form.get("text", "你好")))
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                f'<div class="card"><h3>测试回复（未发送）</h3>'
                f"<pre>{esc(rendered)}</pre><p><a href='/sandbox/chat'>返回</a></p></div>",
            ),
            content_type="text/html",
        )

    async def _behavior_trigger(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        await request.post()
        if action == "mood_up":
            result = await self._behavior.test_state_transition(+1)
        elif action == "mood_down":
            result = await self._behavior.test_state_transition(-1)
        elif action == "reset_state":
            result = await self._behavior.reset_state()
        elif action == "test_initiative":
            preview = await self._behavior.preview(
                {"topic": "未完成的项目", "relationship": "familiar"}
            )
            result = preview.get("initiative", preview)
        else:
            result = {"ok": False, "detail": f"unknown action: {action}"}
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                f'<div class="card"><h3>{esc(action)}</h3>'
                f"<pre>{esc(rendered)}</pre><p><a href='/sandbox/chat'>返回</a></p></div>",
            ),
            content_type="text/html",
        )

    async def _topics_page(self, request: web.Request) -> web.Response:
        scope_key = request.query.get("scope", "")
        status = request.query.get("status", "")
        topics = await self._behavior.list_topics(scope_key=scope_key, status=status)
        status_options = "".join(
            f'<option value="{s}" {"selected" if s == status else ""}>{s or "全部"}</option>'
            for s in ("", "active", "waiting", "resolved", "forgotten")
        )
        rows = "".join(
            f"<tr><td>{t['id']}</td><td>{esc(t['scope_key'])}</td><td>{esc(t['title'])}</td>"
            f"<td>{esc(t['status'])}</td><td>{t['importance']:.2f}</td>"
            f"<td class='muted'>{esc(format_ts(t['last_discussed_at']))}</td><td>"
            f"<form class='inline' method='post' action='/api/topics/resolve/{t['id']}'>"
            f"<button class='btn btn-secondary btn-sm'>解决</button></form>"
            f"<form class='inline' method='post' action='/api/topics/forget/{t['id']}'>"
            f"<button class='btn btn-secondary btn-sm'>遗忘</button></form>"
            f"<form class='inline' method='post' action='/api/topics/delete/{t['id']}'>"
            f"<button class='btn btn-danger btn-sm'>删除</button></form></td></tr>"
            for t in topics
        )
        body = (
            '<div class="card"><form method="get" action="/topics">'
            f'<label>Scope (user:123 / group:456)</label><input name="scope" value="{esc(scope_key)}">'
            f'<label>状态</label><select name="status">{status_options}</select>'
            "<p><button class='btn btn-primary'>筛选</button></p></form></div>"
            f'<div class="card"><h3>Topics ({len(topics)})</h3>'
            "<table><tr><th>ID</th><th>Scope</th><th>Title</th><th>Status</th>"
            "<th>Imp</th><th>Last</th><th></th></tr>"
            + (rows or '<tr><td colspan="7" class="muted">暂无话题</td></tr>')
            + "</table></div>"
        )
        return web.Response(
            text=layout("话题", "/topics", body, subtitle="还没聊完的事，以及她的主动话题来源"),
            content_type="text/html",
        )

    async def _api_topic_action(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        topic_id = int(request.match_info["topic_id"])
        ok = await self._behavior.topic_action(action, topic_id)
        raise web.HTTPFound(f"/topics?updated={1 if ok else 0}")

    async def _sandbox_page(self, request: web.Request) -> web.Response:
        data = self._sandbox_data()
        sandbox = self._bot.sandbox
        if sandbox is not None:
            data["recent_events"] = await sandbox.store.recent_events(limit=12)
        body = sandbox_page._tabs("/sandbox") + sandbox_page.dashboard(data)
        return web.Response(
            text=layout("生活沙盒", "/sandbox", body, subtitle="她本来就在过自己的日子（v2.0）"),
            content_type="text/html",
        )

    async def _sandbox_inspectors_page(self, request: web.Request) -> web.Response:
        body = sandbox_page._tabs("/sandbox/inspectors") + sandbox_page.inspectors(
            self._sandbox_data()
        )
        return web.Response(
            text=layout("沙盒 · 实体与空间", "/sandbox", body, subtitle="宠物、空间、物件、库存"),
            content_type="text/html",
        )

    async def _sandbox_needs_page(self, request: web.Request) -> web.Response:
        body = sandbox_page._tabs("/sandbox/needs") + sandbox_page.needs_page(self._sandbox_data())
        return web.Response(
            text=layout("沙盒 · 需求与动作", "/sandbox", body, subtitle="她为什么做这件事"),
            content_type="text/html",
        )

    async def _sandbox_bible_page(self, request: web.Request) -> web.Response:
        msg = request.query.get("msg", "")
        body = sandbox_page._tabs("/sandbox/bible") + sandbox_page.bible_page(self._sandbox_data())
        if msg:
            body = f"<div class='card'><p class='muted'>{ui.esc(msg)}</p></div>" + body
        return web.Response(
            text=layout("沙盒 · 人物档案", "/sandbox", body, subtitle="Canonical Source 与覆盖率"),
            content_type="text/html",
        )

    async def _sandbox_trace_page(self, request: web.Request) -> web.Response:
        data = self._sandbox_data()
        sandbox = self._bot.sandbox
        if sandbox is not None:
            data["replay"] = await sandbox.replay(limit=120)
        body = sandbox_page._tabs("/sandbox/trace") + sandbox_page.trace_page(data)
        return web.Response(
            text=layout("沙盒 · 回放", "/sandbox", body, subtitle="结构化轨迹（不含思维链）"),
            content_type="text/html",
        )

    async def _sandbox_simulate(self, request: web.Request) -> web.Response:
        form = await request.post()
        try:
            hours = min(168.0, max(1.0, float(str(form.get("hours", "24")))))
        except ValueError:
            hours = 24.0
        sandbox = getattr(self._bot, "sandbox", None)
        if sandbox is None:
            raise web.HTTPFound("/sandbox/trace?msg=沙盒未启用")
        backup = sandbox._backup_state()  # noqa: SLF001 - simulator is admin-gated
        before = sandbox.current_action
        try:
            sim = await sandbox.simulate(hours=hours)
        finally:
            sandbox._restore_state(backup)  # noqa: SLF001 - dry run never sticks
            sandbox.current_action = before
        self._bot.log.info("[Web] sandbox dry-run %.0fh: %s", hours, sim)
        raise web.HTTPFound(
            "/sandbox/trace?msg=" + quote(f"干跑 {hours:.0f}h 完成（未发送任何 QQ）")
        )

    async def _api_sandbox_control(self, request: web.Request) -> web.Response:
        import json as _json

        payload = _json.loads(await request.text() or "{}")
        action = str(payload.get("action", ""))
        sandbox = getattr(self._bot, "sandbox", None)
        lifecycle = getattr(self._bot, "lifecycle_manager", None)
        result: dict[str, Any] = {"ok": False}
        if sandbox is None:
            result["reason"] = "sandbox_disabled"
        elif action == "pause":
            sandbox.phase = sandbox.phase.__class__.paused
            result = {"ok": True, "phase": sandbox.phase.value}
        elif action == "resume":
            sandbox.phase = sandbox.phase.__class__.running
            result = {"ok": True, "phase": sandbox.phase.value}
        elif action == "reset" and lifecycle is not None:
            result = await lifecycle.reset_character(confirm=bool(payload.get("confirm")))
        elif action == "reinitialize":
            await sandbox.reinitialize()
            result = {"ok": True}
        elif action == "reset_report" and lifecycle is not None:
            result = {"ok": True, "report": await lifecycle.reset_report()}
        return web.json_response(result)

    def _sandbox_data(self) -> dict[str, Any]:
        sandbox = getattr(self._bot, "sandbox", None)
        data: dict[str, Any] = {"enabled": sandbox is not None}
        if sandbox is None:
            return data
        data.update(
            {
                "phase": sandbox.phase.value,
                "context": sandbox.context(),
                "spaces": [s.model_dump(mode="json") for s in sandbox.spaces.all()],
                "objects": [o.model_dump(mode="json") for o in sandbox.objects.all()],
                "inventories": {key: inv.items for key, inv in sandbox.inventories.all().items()},
                "needs": {
                    key: need.model_dump(mode="json") for key, need in sandbox.needs.all().items()
                },
                "action": (
                    sandbox.current_action.model_dump(mode="json")
                    if sandbox.current_action
                    else None
                ),
                "action_defs": [
                    d.model_dump(mode="json") for d in sandbox.actions.definitions.values()
                ],
                "pet": sandbox.pet_system.snapshot(),
                "social_spaces": [
                    s.model_dump(mode="json") for s in sandbox.social_spaces.values()
                ],
                "bible": sandbox.bible.model_dump(mode="json"),
            }
        )
        return data

    def register_sandbox(self, app: web.Application) -> None:
        app.router.add_get("/behavior", self._behavior_legacy_redirect)
        app.router.add_get("/sandbox/chat", self._sandbox_chat_page)
        app.router.add_post("/behavior/settings", self._behavior_settings_save)
        app.router.add_post("/behavior/preview", self._behavior_preview)
        app.router.add_post("/behavior/test-response", self._behavior_test_response)
        app.router.add_post("/behavior/trigger/{action}", self._behavior_trigger)
        app.router.add_get("/topics", self._topics_page)
        app.router.add_post("/api/topics/{action}/{topic_id}", self._api_topic_action)
        app.router.add_get("/sandbox", self._sandbox_page)
        app.router.add_get("/sandbox/inspectors", self._sandbox_inspectors_page)
        app.router.add_get("/sandbox/needs", self._sandbox_needs_page)
        app.router.add_get("/sandbox/bible", self._sandbox_bible_page)
        app.router.add_get("/sandbox/trace", self._sandbox_trace_page)
        app.router.add_post("/sandbox/simulate", self._sandbox_simulate)
        app.router.add_post("/api/sandbox/control", self._api_sandbox_control)
