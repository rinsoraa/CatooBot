"""Prompt editing + the conversation/continuity inspector (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from app.web.pages import conversation_page
from app.web.routes.base import WebContext, esc, layout


class PromptRoutes(WebContext):
    """Handlers for this domain."""

    async def _prompts_page(self, request: web.Request) -> web.Response:
        prompts = await self._admin.get_prompts()
        body = f"""<div class="card"><h3>Prompts</h3><form method="post" action="/prompts">
<label>Persona System Prompt（角色自由补充段）</label>
<textarea name="persona_system_prompt" rows="8">{esc(prompts["persona_system_prompt"])}</textarea>
<p class="hint">角色的身份/性格/规则来自人物档案（<code>config/character_bible.md</code>）并同步在
<a href="/character">角色</a>页；这里只放自由补充段，长期设定请改档案。</p>
<label>Memory Extraction Prompt（留空使用内置默认）</label>
<textarea name="memory_extraction_prompt" rows="8">{esc(prompts["memory_extraction_prompt"])}</textarea>
<p><button class="btn btn-primary">保存并热加载</button></p></form></div>"""
        return web.Response(
            text=layout(
                "提示词",
                "/prompts",
                body,
                subtitle="系统提示词覆盖项（写在这里的内容会优先于默认值）",
            ),
            content_type="text/html",
        )

    async def _prompts_save(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._admin.save_prompts(
            {
                "persona_system_prompt": str(form.get("persona_system_prompt", "")),
                "memory_extraction_prompt": str(form.get("memory_extraction_prompt", "")),
            }
        )
        return web.Response(
            text=layout(
                "提示词",
                "/prompts",
                '<div class="card">已保存</div>'
                '<meta http-equiv="refresh" content="1;url=/prompts">',
            ),
            content_type="text/html",
        )

    async def _conversation_page(self, request: web.Request) -> web.Response:
        runtime = self._bot.conversation
        sessions = runtime.session_snapshot()
        stale_total = 0
        turns: list[dict[str, Any]] = []
        try:
            row = await self._bot.database.fetchone(
                "SELECT COUNT(*) AS n FROM conversation_turns WHERE status = 'stale'"
            )
            stale_total = int(row["n"]) if row else 0
            rows = await self._bot.database.fetchall(
                "SELECT * FROM conversation_turns ORDER BY started_at DESC LIMIT 30"
            )
            turns = [dict(row) for row in rows]
        except Exception:  # noqa: BLE001 - table may not exist pre-migration
            self._bot.log.debug("[Web] conversation tables unavailable", exc_info=True)
        data = {
            "enabled": runtime.enabled,
            "sessions": sessions,
            "session_count": len(sessions),
            "buffered": sum(s["buffered_messages"] for s in sessions),
            "generating": sum(1 for s in sessions if s["active_generation"]),
            "stale_total": stale_total,
        }
        body = conversation_page._tabs("/conversation") + conversation_page.dashboard(data, turns)
        return web.Response(
            text=layout(
                "对话轮次",
                "/conversation",
                body,
                subtitle="连续消息合并为一个 Turn；改口让旧回复作废（v1.2）",
            ),
            content_type="text/html",
        )

    async def _conversation_continuity_page(self, request: web.Request) -> web.Response:
        continuity = self._bot.continuity
        data: dict[str, Any] = {"enabled": continuity is not None}
        if continuity is not None:
            state = continuity.state.model_dump(mode="json")
            loops = await continuity.store.open_loops(include_closed=True)
            profiles: list[dict[str, Any]] = []
            try:
                rows = await self._bot.database.fetchall(
                    "SELECT * FROM interaction_profiles ORDER BY updated_at DESC LIMIT 20"
                )
                import json as _json

                for row in rows:
                    profiles.append(
                        {
                            "user_id": row["user_id"],
                            "patterns": _json.loads(row["patterns"] or "{}"),
                            "updated_at": row["updated_at"],
                        }
                    )
            except Exception:  # noqa: BLE001
                self._bot.log.debug("[Web] interaction profiles unavailable", exc_info=True)
            shared: list[dict[str, Any]] = []
            try:
                rows = await self._bot.database.fetchall(
                    "SELECT * FROM shared_experiences ORDER BY updated_at DESC LIMIT 20"
                )
                import json as _json

                shared = [
                    {
                        "summary": row["summary"],
                        "type": row["type"],
                        "keywords": _json.loads(row["keywords"] or "[]"),
                        "times_referenced": row["times_referenced"],
                        "confidence": row["confidence"],
                    }
                    for row in rows
                ]
            except Exception:  # noqa: BLE001
                pass
            data.update(
                {
                    "state": state,
                    "open_loops": [
                        {
                            "summary": loop.summary,
                            "type": loop.type.value,
                            "status": loop.status.value,
                            "progress": loop.progress,
                            "source": loop.source,
                            "confidence": loop.confidence,
                        }
                        for loop in loops[:12]
                    ],
                    "profiles": profiles,
                    "shared_experiences": shared,
                }
            )
        body = conversation_page._tabs("/conversation/continuity")
        body += conversation_page.continuity_page(data)
        return web.Response(
            text=layout(
                "对话 · 延续状态",
                "/conversation",
                body,
                subtitle="她最近关注什么、有什么没做完、和谁的共同经历（v1.2）",
            ),
            content_type="text/html",
        )

    def register_prompts(self, app: web.Application) -> None:
        app.router.add_get("/prompts", self._prompts_page)
        app.router.add_post("/prompts", self._prompts_save)
        app.router.add_get("/conversation", self._conversation_page)
        app.router.add_get("/conversation/continuity", self._conversation_continuity_page)
