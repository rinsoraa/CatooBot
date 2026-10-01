"""Persona, people and conversation control (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from aiohttp import web

from app.config.settings import PROJECT_ROOT
from app.web import ui
from app.web.routes.base import WebContext, esc, format_ts, layout


class IdentityRoutes(WebContext):
    """Handlers for this domain."""

    async def _character_page(self, request: web.Request) -> web.Response:
        persona = await self._admin.get_persona()
        state = await self._admin.character_state()
        identity = persona.identity

        def field(label: str, name: str, value: str, tip_text: str = "") -> str:
            return (
                f"<label class='field'><span class='field-label'>{label}"
                f"{ui.tip(tip_text) if tip_text else ''}</span>"
                f"<input name='{name}' value='{esc(value)}'></label>"
            )

        def area(label: str, name: str, value: str, rows: int = 4, tip_text: str = "") -> str:
            return (
                f"<label class='field'><span class='field-label'>{label}"
                f"{ui.tip(tip_text) if tip_text else ''}</span>"
                f"<textarea name='{name}' rows='{rows}'>{esc(value)}</textarea></label>"
            )

        def list_field(label: str, name: str, items: list[str], tip_text: str = "") -> str:
            return area(label, name, "、".join(items), 2, tip_text)

        body = f"""
<div class="card"><h3>Identity</h3><form method="post" action="/character">
{field("名字", "name", identity.name, "她自称的名字，会出现在人设与提示词里")}{field("昵称", "nickname", identity.nickname, "别人可能怎么叫她；留空就用名字")}
{field("年龄", "age", identity.age, "设定年龄；只是人设，不影响功能")}{field("生日", "birthday", identity.birthday)}
{field("性别", "gender", identity.gender)}{field("职业", "occupation", identity.occupation, "她对外的人设身份，例如「家里蹲」")}
{field("所在地", "location", identity.location, "人设里的地点，会参与「她在家/在外面」这类叙事")}
{area("背景故事", "background", identity.background)}
{list_field("性格 traits", "traits", persona.personality.traits)}
{list_field("喜欢", "likes", persona.personality.likes)}
{list_field("不喜欢", "dislikes", persona.personality.dislikes)}
{list_field("习惯", "habits", persona.personality.habits)}
{list_field("兴趣", "interests", persona.personality.interests)}
<label>语气 tone</label><input name="tone" value="{esc(persona.speaking_style.tone)}">
<label>回复长度偏好 short/mixed/long</label><input name="length_preference" value="{esc(persona.speaking_style.length_preference)}">
<label>Emoji</label><select name="emoji"><option value="1" {"selected" if persona.speaking_style.emoji else ""}>允许</option><option value="0" {"selected" if not persona.speaking_style.emoji else ""}>不用</option></select>
<label>颜文字</label><select name="kaomoji"><option value="1" {"selected" if persona.speaking_style.kaomoji else ""}>允许</option><option value="0" {"selected" if not persona.speaking_style.kaomoji else ""}>不用</option></select>
{area("风格备注", "style_notes", persona.speaking_style.notes, 2)}
{area("行为规则（每行一条）", "rules", chr(10).join(persona.behavior_rules.rules), 4)}
{area("System Prompt（角色自由补充）", "system_prompt", persona.system_prompt, 6)}
<p><button class="btn btn-primary">保存并热加载</button></p>
<p class="hint">人设由人物档案 <code>config/character_bible.md</code> 编译；
档案变化（或人设为空）时启动会自动同步到这里，手工修改后以页面为准。</p></form></div>

<div class="card"><h3>当前状态（narrative）</h3><form method="post" action="/character/state">
{field("心情 mood", "mood", state["mood"])}{field("能量 energy (0-1)", "energy", str(state["energy"]))}
{field("正在做 activity", "activity", state["activity"])}{field("关注 current_focus", "current_focus", state["current_focus"])}
<p><button class="btn btn-primary">更新状态</button></p>
<p class="hint">「正在做 / 位置 / 精力」由生活沙盒自动同步（她切换动作时会覆盖手工值）；
想看细节去 <a href="/sandbox">沙盒</a> 页。</p></form></div>

<div class="card"><h3>数据（导出 / 导入）</h3>
<p>把她的全部记忆、关系、会话、沙盒状态导成一个 JSON 文件，或从一个文件恢复。
<strong>导入会覆盖当前角色数据</strong>；平台数据、QQ 用户/群、模型与工具配置不受影响，写前会自动备份。</p>
<p><a class="btn btn-secondary btn-sm" href="/character/export">导出数据（下载 JSON）</a></p>
<form method="post" action="/character/import" enctype="multipart/form-data">
<label>从文件导入</label><input type="file" name="file" accept=".json,application/json">
<p><button class="btn btn-secondary btn-sm">上传并预览（不写入）</button></p>
</form></div>"""
        return web.Response(
            text=layout(
                "角色",
                "/character",
                body,
                subtitle="人设、身份、状态与说话风格（QQ 端看不到这些设置）",
            ),
            content_type="text/html",
        )

    async def _character_save(self, request: web.Request) -> web.Response:
        form = await request.post()

        def split_list(value: str) -> list[str]:
            return [item.strip() for item in re_split(value) if item.strip()]

        def re_split(value: str) -> list[str]:
            import re as _re

            return _re.split(r"[、,;\n]+", value)

        data = {
            "identity": {
                k: str(form.get(k, "")).strip()
                for k in (
                    "name",
                    "nickname",
                    "age",
                    "birthday",
                    "gender",
                    "occupation",
                    "location",
                    "background",
                )
            },
            "personality": {
                "traits": split_list(str(form.get("traits", ""))),
                "likes": split_list(str(form.get("likes", ""))),
                "dislikes": split_list(str(form.get("dislikes", ""))),
                "habits": split_list(str(form.get("habits", ""))),
                "interests": split_list(str(form.get("interests", ""))),
            },
            "speaking_style": {
                "tone": str(form.get("tone", "")).strip(),
                "length_preference": str(form.get("length_preference", "")).strip() or "mixed",
                "emoji": str(form.get("emoji")) == "1",
                "kaomoji": str(form.get("kaomoji")) == "1",
                "notes": str(form.get("style_notes", "")).strip(),
            },
            "behavior_rules": {
                "rules": [r.strip() for r in str(form.get("rules", "")).splitlines() if r.strip()]
            },
            "system_prompt": str(form.get("system_prompt", "")).strip(),
        }
        try:
            await self._admin.save_persona(data)
            note = "已保存并热加载"
        except ValueError as exc:
            note = f"保存失败：{exc}"  # old persona stays active (spec v0.3 §53)
        return web.Response(
            text=layout(
                "角色",
                "/character",
                f'<div class="card">{esc(note)}</div>'
                '<meta http-equiv="refresh" content="1;url=/character">',
            ),
            content_type="text/html",
        )

    async def _character_state_save(self, request: web.Request) -> web.Response:
        form = await request.post()
        try:
            energy = float(str(form.get("energy", "0.8")))
        except ValueError:
            energy = 0.8
        await self._admin.set_state(
            mood=str(form.get("mood", "")).strip() or None,
            energy=max(0.0, min(1.0, energy)),
            activity=str(form.get("activity", "")).strip(),
            current_focus=str(form.get("current_focus", "")).strip(),
        )
        raise web.HTTPFound("/character")

    # ------------------------------------------------------- data transfer

    def _pending_import_path(self) -> Path:
        return PROJECT_ROOT / "data" / "exports" / "_pending_import.json"

    async def _character_export(self, request: web.Request) -> web.Response:
        document = await self._admin.export_character()
        payload = json.dumps(document, ensure_ascii=False, indent=2)
        return web.Response(
            text=payload,
            content_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="character-{int(time.time())}.json"'
            },
        )

    async def _character_import(self, request: web.Request) -> web.Response:
        form = await request.post()
        field = form.get("file")
        if field is None or not hasattr(field, "file"):
            raise web.HTTPBadRequest(text="缺少上传文件")
        pending = self._pending_import_path()
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_bytes(field.file.read())
        report = await self._admin.import_character_preview(str(pending))
        if not report.get("ok"):
            reason = report.get("reason", "unknown")
            body = f'<div class="card">导入预览失败：{esc(reason)}</div><p><a href="/character">返回</a></p>'
            return web.Response(text=layout("角色", "/character", body), content_type="text/html")
        counts = report.get("counts", {})
        rows = "".join(
            f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in sorted(counts.items()) if v
        )
        body = f"""<div class="card"><h3>导入预览（尚未写入）</h3>
<p>将写入 <strong>{report.get("rows", 0)}</strong> 行、覆盖当前角色数据；平台数据不受影响，写前自动备份。</p>
<table><tr><th>表</th><th>行数</th></tr>{rows or '<tr><td colspan="2" class="muted">（空包：角色域将被清空）</td></tr>'}</table>
<form method="post" action="/character/import/confirm">
<button class="btn btn-danger">确认导入（覆盖当前角色数据）</button></form>
<p><a href="/character">取消</a></p></div>"""
        return web.Response(text=layout("角色", "/character", body), content_type="text/html")

    async def _character_import_confirm(self, request: web.Request) -> web.Response:
        pending = self._pending_import_path()
        if not pending.exists():
            raise web.HTTPBadRequest(text="没有待导入的文件")
        report = await self._admin.import_character_confirm(str(pending))
        pending.unlink(missing_ok=True)
        note = "已导入（备份已写）" if report.get("ok") else f"导入失败：{report.get('reason')}"
        return web.Response(
            text=layout(
                "角色",
                "/character",
                f'<div class="card">{esc(note)}</div>'
                '<meta http-equiv="refresh" content="1;url=/character">',
            ),
            content_type="text/html",
        )

    async def _users_page(self, request: web.Request) -> web.Response:
        users = await self._admin.list_users()

        def toggle(checked: bool) -> str:
            return "checked" if checked else ""

        rows = "".join(
            f"""<tr><td>{esc(u["user_id"])}</td><td>{esc(u["nickname"] or "")}</td>
<td>{esc(u["nickname_override"] or "")}</td><td>{u["interactions"]}</td><td>{esc(u["stage"])}</td>
<td class="muted">{esc(format_ts(u["last_seen"]))}</td><td>{esc(u["notes"] or "")}</td>
<td><details><summary class="muted">编辑</summary><form method="post" action="/users">
<input type="hidden" name="user_id" value="{esc(u["user_id"])}">
<label>Nickname override</label><input name="nickname_override" value="{esc(u["nickname_override"] or "")}">
<label>Notes</label><textarea name="notes" rows="2">{esc(u["notes"] or "")}</textarea>
<label style="display:inline-block"><input type="checkbox" name="initiative_enabled" value="1"
 {toggle(bool(u.get("initiative_enabled", 1)))} style="width:auto"> 允许主动聊天</label>
<p><button class="btn btn-secondary btn-sm">保存</button></p></form></details></td></tr>"""
            for u in users
        )
        body = f"""<div class="card"><h3>Users ({len(users)})</h3>
<table><tr><th>QQ</th><th>Nickname</th><th>Override</th><th>Interactions</th><th>Stage</th><th>Last Seen</th><th>Notes</th><th>Initiative</th><th></th></tr>
{rows or '<tr><td colspan="9" class="muted">暂无用户</td></tr>'}</table></div>"""
        return web.Response(
            text=layout(
                "用户", "/users", body, subtitle="谁在和她聊天、关系到什么程度、是否允许主动找他"
            ),
            content_type="text/html",
        )

    async def _users_save(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._admin.save_user_profile(
            str(form.get("user_id", "")),
            {
                "nickname_override": str(form.get("nickname_override", "")).strip(),
                "notes": str(form.get("notes", "")).strip(),
                "initiative_enabled": "1" if form.get("initiative_enabled") else "0",
            },
        )
        raise web.HTTPFound("/users")

    async def _groups_page(self, request: web.Request) -> web.Response:
        groups = await self._admin.list_groups()
        rows = "".join(
            f"""<tr><td>{esc(g["group_id"])}</td><td>{esc(g["name"] or "")}</td>
<td class="muted">{esc(format_ts(g["last_seen"]))}</td>
<td>{"✓" if g.get("participation_enabled", 1) else "✗"}</td>
<td><form class="inline" method="post" action="/groups/toggle">
<input type="hidden" name="group_id" value="{esc(g["group_id"])}">
<input type="hidden" name="enabled" value="{0 if g.get("participation_enabled", 1) else 1}">
<button class="btn btn-secondary btn-sm">切换参与</button></form></td></tr>"""
            for g in groups
        )
        body = f"""<div class="card"><h3>Groups ({len(groups)})</h3>
<table><tr><th>Group</th><th>Name</th><th>Last Seen</th><th>参与</th><th></th></tr>
{rows or '<tr><td colspan="5" class="muted">暂无群组</td></tr>'}</table></div>"""
        return web.Response(
            text=layout(
                "群组", "/groups", body, subtitle="群里的资料与参与开关（进群不说话就关掉它）"
            ),
            content_type="text/html",
        )

    async def _groups_toggle(self, request: web.Request) -> web.Response:
        form = await request.post()
        group_id = str(form.get("group_id", ""))
        enabled = str(form.get("enabled", "1")) == "1"
        now = int(time.time())
        await self._bot.database.execute(
            """INSERT INTO group_profiles (group_id, participation_enabled, last_seen)
               VALUES (?, ?, ?)
               ON CONFLICT(group_id) DO UPDATE SET participation_enabled=excluded.participation_enabled""",
            (group_id, 1 if enabled else 0, now),
        )
        raise web.HTTPFound("/groups")

    async def _sessions_page(self, request: web.Request) -> web.Response:
        session_id = request.query.get("id", "")
        sessions = await self._admin.list_sessions()
        rows = "".join(
            f"""<tr><td><a href="/sessions?id={esc(s["session_id"])}">{esc(s["session_id"])}</a></td>
<td>{esc(s["type"])}</td><td>{s["messages"]}</td><td class="muted">{esc(format_ts(s["last_at"]))}</td></tr>"""
            for s in sessions
        )
        context_html = ""
        if session_id:
            context = await self._admin.session_context(session_id)
            lines = "\n".join(f"[{c['role']}] {c['content']}" for c in context)
            context_html = f"""<div class="card"><h3>Context: {esc(session_id)}</h3>
<pre>{esc(lines)}</pre>
<form method="post" action="/api/sessions/clear"><input type="hidden" name="session_id" value="{esc(session_id)}">
<button class="danger">清空该会话上下文</button></form></div>"""
        body = f"""<div class="card"><h3>Sessions ({len(sessions)})</h3>
<table><tr><th>Session</th><th>Type</th><th>Messages</th><th>Last</th></tr>{rows}</table></div>{context_html}"""
        return web.Response(
            text=layout("会话", "/sessions", body, subtitle="按会话隔离的短期上下文，可单独清空"),
            content_type="text/html",
        )

    async def _api_session_clear(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._admin.clear_session(str(form.get("session_id", "")))
        raise web.HTTPFound("/sessions")

    def register_identity(self, app: web.Application) -> None:
        app.router.add_get("/character", self._character_page)
        app.router.add_post("/character", self._character_save)
        app.router.add_post("/character/state", self._character_state_save)
        app.router.add_get("/character/export", self._character_export)
        app.router.add_post("/character/import", self._character_import)
        app.router.add_post("/character/import/confirm", self._character_import_confirm)
        app.router.add_get("/users", self._users_page)
        app.router.add_post("/users", self._users_save)
        app.router.add_get("/groups", self._groups_page)
        app.router.add_post("/groups/toggle", self._groups_toggle)
        app.router.add_get("/sessions", self._sessions_page)
        app.router.add_post("/api/sessions/clear", self._api_session_clear)
