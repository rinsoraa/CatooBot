"""`/api/v1/stickers*` 与 `/api/v1/expressions*`（WebUI v1.0 · W5 契约 §7.4）。

动作一律委托既有 Service（``StickerAdminService`` / ``ExpressionAdminService``）：
贴纸只改状态（active/disabled/archived），口癖沿用 set_status/delete。
删除动作必须带 ``{"confirm": "delete"}``，否则 409 ``media.confirm_required``。

诚实说明：

* ``preview_url`` 恒为 ``null`` —— 项目里没有可服务贴纸文件的静态路由，
  这里不伪造一个（前端用 file/file_name 自行映射或后续加路由）；
* 贴纸状态里的 "delete" 落成既有库的 ``archived``（可回溯），不是物理删除；
* ``reindex`` 的 ``added``/``updated`` 无法从 ``scan()`` 的计数中诚实拆分，
  返回 ``null``；``removed`` 恒为 0（扫描从不移除记录）。
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from app.web.api.common import (
    API_PREFIX,
    bad_request,
    conflict,
    json_endpoint,
    not_found,
    ok,
    read_json,
    read_query_int,
    unavailable,
)
from app.web.routes.base import WebContext

#: 贴纸动作 -> 落库状态（沿用 routes/media.py 约定，删除=归档）
STICKER_ACTIONS = {"enable": "active", "disable": "disabled", "delete": "archived"}

#: 口癖动作（delete 走 store.delete）
EXPRESSION_ACTIONS = frozenset({"enable", "disable", "delete"})

DELETE_CONFIRM = "delete"


class MediaApiRoutes(WebContext):
    """`/api/v1/stickers*`、`/api/v1/expressions*`。"""

    # ---------------------------------------------------------------- stickers

    async def _v1_stickers(self, request: web.Request) -> web.Response:
        service = self._sticker_admin
        query = request.query.get("q", "")
        emotion = request.query.get("emotion", "")
        intent = request.query.get("intent", "")
        status = request.query.get("status", "")
        limit = read_query_int(request, "limit", default=100, minimum=1, maximum=500)
        offset = read_query_int(request, "offset", default=0, minimum=0, maximum=1_000_000)
        rows = await service.list_stickers(
            query=query,
            emotion=emotion,
            intent=intent,
            status=status,
            limit=limit,
            offset=offset,
        )
        if query or emotion or intent:
            # search() 没有 count 访问器：只有取完整个匹配窗口时 total 才诚实
            total = len(rows) if offset == 0 and len(rows) < limit else None
        else:
            total = await service.count(status or "active")
        items = [_sticker_item(row) for row in rows]
        return ok({"items": items, "stats": await service.stats(), "total": total}, request=request)

    async def _v1_sticker_action(self, request: web.Request) -> web.Response:
        sticker_id = str(request.match_info["sticker_id"])
        action = str(request.match_info["action"])
        if action not in STICKER_ACTIONS:
            raise bad_request(f"未知的贴纸动作：{action}", code="media.action_unknown")
        if action == "delete":
            body = await read_json(request, required=False)
            if str(body.get("confirm", "")) != DELETE_CONFIRM:
                raise conflict(
                    "删除贴纸需要 confirm=delete（落成归档，可回溯）",
                    code="media.confirm_required",
                )
        service = self._sticker_admin
        if service.media is None or not service.available:
            raise unavailable("表情库未启用", code="media.unavailable")
        if await service.sticker(sticker_id) is None:
            raise not_found(f"贴纸不存在：{sticker_id}", code="media.sticker_unknown")
        status = STICKER_ACTIONS[action]
        applied = await service.set_status(sticker_id, status)
        if not applied:
            raise unavailable("表情库写入失败", code="media.unavailable")
        return ok(
            {"sticker_id": sticker_id, "action": action, "status": status, "applied": True},
            request=request,
        )

    async def _v1_stickers_reindex(self, request: web.Request) -> web.Response:
        service = self._sticker_admin
        if service.media is None or not service.available:
            raise unavailable("表情库未启用", code="media.unavailable")
        state = await service.reindex()
        return ok(
            {
                # scan() 只回 {pending/analyzing/done/failed/duplicate}
                "scanned": sum(int(value) for value in state.values()) if state else 0,
                "added": None,  # scan() 不区分新增与重扫
                "updated": None,
                "removed": 0,  # 扫描从不移除记录
                "state": state,
            },
            request=request,
        )

    # ------------------------------------------------------------- expressions

    async def _v1_expressions(self, request: web.Request) -> web.Response:
        service = self._expression_admin
        status = request.query.get("status", "")
        scope_key = request.query.get("group_id", "") or request.query.get("scope_key", "")
        limit = read_query_int(request, "limit", default=200, minimum=1, maximum=500)
        rows = await service.list_patterns(scope_key)
        if status:
            rows = [row for row in rows if str(row.get("status", "")) == status]
        items = [_expression_item(row) for row in rows[:limit]]
        return ok({"items": items, "stats": service.stats()}, request=request)

    async def _v1_expression_action(self, request: web.Request) -> web.Response:
        action = str(request.match_info["action"])
        if action not in EXPRESSION_ACTIONS:
            raise bad_request(f"未知的口癖动作：{action}", code="media.action_unknown")
        try:
            pattern_id = int(request.match_info["pattern_id"])
        except ValueError as exc:
            raise not_found(
                f"口癖不存在：{request.match_info['pattern_id']}", code="media.pattern_unknown"
            ) from exc
        if action == "delete":
            body = await read_json(request, required=False)
            if str(body.get("confirm", "")) != DELETE_CONFIRM:
                raise conflict(
                    "删除口癖需要 confirm=delete（连同来源与向量一起删除）",
                    code="media.confirm_required",
                )
        service = self._expression_admin
        rows = await service.list_patterns()
        if not any(int(row.get("id", -1)) == pattern_id for row in rows):
            raise not_found(f"口癖不存在：{pattern_id}", code="media.pattern_unknown")
        if action == "delete":
            await service.delete(pattern_id)
            return ok(
                {"pattern_id": pattern_id, "action": action, "deleted": True}, request=request
            )
        status = "active" if action == "enable" else "disabled"
        await service.set_status(pattern_id, status)
        return ok({"pattern_id": pattern_id, "action": action, "status": status}, request=request)

    # ----------------------------------------------------------- registration

    def _register_v1_media(self, app: web.Application) -> None:
        wrap = json_endpoint
        app.router.add_get(f"{API_PREFIX}/stickers", wrap(self._v1_stickers))
        app.router.add_post(f"{API_PREFIX}/stickers/reindex", wrap(self._v1_stickers_reindex))
        app.router.add_post(
            f"{API_PREFIX}/stickers/{{sticker_id}}/{{action}}", wrap(self._v1_sticker_action)
        )
        app.router.add_get(f"{API_PREFIX}/expressions", wrap(self._v1_expressions))
        app.router.add_post(
            f"{API_PREFIX}/expressions/{{pattern_id}}/{{action}}",
            wrap(self._v1_expression_action),
        )


def _sticker_item(row: dict[str, Any]) -> dict[str, Any]:
    """Service row → contract item（preview_url 无真实路由，恒为 null）。"""
    emotion = list(row.get("emotion_tags") or [])
    intent = list(row.get("intent_tags") or [])
    return {
        "sticker_id": row.get("id"),
        "file": row.get("file") or None,
        "file_name": row.get("file_name") or "",
        "preview_url": None,
        "emotion": emotion[0] if emotion else "",
        "emotion_tags": emotion,
        "intent": intent[0] if intent else "",
        "intent_tags": intent,
        "status": row.get("status"),
        "origin": row.get("origin"),
        "origin_user": row.get("origin_user_id") or "",
        "usage_count": int(row.get("usage_count") or 0),
        "last_used_at": row.get("last_used_at"),
        "created_at": row.get("created_at"),
        "safety_status": row.get("safety_status"),
        "valid": bool(row.get("valid")),
    }


def _expression_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pattern_id": row.get("id"),
        "pattern": row.get("pattern") or "",
        "kind": row.get("kind") or "",
        "status": row.get("status") or "",
        "group_id": row.get("scope_key") or "",
        "scope_key": row.get("scope_key") or "",
        "occurrences": int(row.get("sample_count") or 0),
        "speakers": int(row.get("speaker_count") or 0),
        "use_count": int(row.get("use_count") or 0),
        "first_seen": row.get("first_seen_at") or None,
        "last_seen": row.get("last_seen_at") or None,
    }
