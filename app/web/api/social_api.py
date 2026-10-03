"""`/api/v1` 社交域：用户 / 群组 / 会话 / 关系 / 承诺 / 空间（WebUI v1.0 · W5 §7.2）。

路由只做「校验 → 调既有 Service」：

* 读走 :class:`~app.web.services.social_read.SocialReadService`（纯投影）；
* 用户画像写 ``AdminService.save_user_profile``；群参与开关写
  ``AdminService.set_group_participation``（与旧 ``POST /groups/toggle``
  共用同一条写入路径）；
* 概览薄薄地转发 ``SocialAdminService.dashboard()``。

所有写操作由会话中间件强制 CSRF（v1 路径不在豁免表里）。
"""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from app.sandbox.commitments import CommitmentStatus
from app.web.api.common import (
    API_PREFIX,
    bad_request,
    json_endpoint,
    not_found,
    ok,
    read_json,
    read_query_int,
    unprocessable,
)
from app.web.routes.base import WebContext
from app.web.services.social_read import SocialReadService

#: PATCH /social/users/{person_id} 允许的字段
USER_PROFILE_FIELDS = frozenset({"nickname_override", "notes", "tags", "initiative_enabled"})

#: 承诺状态过滤的合法值（CommitmentStatus）
COMMITMENT_STATUSES = frozenset(status.value for status in CommitmentStatus)


class SocialApiRoutes(WebContext):
    """`/api/v1/social/*`（W5）。"""

    _social_read_service: SocialReadService | None = None

    def _social_read(self) -> SocialReadService:
        if self._social_read_service is None:
            self._social_read_service = SocialReadService(
                self._bot,
                admin=getattr(self, "_admin", None),
                social_admin=getattr(self, "_social_admin", None),
            )
        return self._social_read_service

    # ------------------------------------------------------------------ users

    async def _v1_social_users(self, request: web.Request) -> web.Response:
        query = request.query.get("q", "") or ""
        limit = read_query_int(request, "limit", default=50, minimum=1, maximum=200)
        offset = read_query_int(request, "offset", default=0, minimum=0, maximum=100_000)
        page = await self._social_read().users_page(query=query, limit=limit, offset=offset)
        return ok(page, request=request)

    async def _v1_social_user_detail(self, request: web.Request) -> web.Response:
        person_id = str(request.match_info["person_id"])
        data = await self._social_read().user_detail(person_id)
        if data is None:
            raise not_found(f"用户不存在：{person_id}", code="social.user_not_found")
        return ok(data, request=request)

    async def _v1_social_user_patch(self, request: web.Request) -> web.Response:
        person_id = str(request.match_info["person_id"])
        service = self._social_read()
        current = await service.user_detail(person_id)
        if current is None:
            raise not_found(f"用户不存在：{person_id}", code="social.user_not_found")
        body = await read_json(request)
        unknown = sorted(set(body) - USER_PROFILE_FIELDS)
        if unknown:
            raise unprocessable(f"不支持的字段：{unknown[0]}", field=unknown[0])
        person = current["person"]
        qq = str(person.get("qq") or "")
        if not qq:
            raise bad_request("该用户没有可写入的 QQ 标识", code="social.user_qq_missing")
        merged = self._merge_profile(person, body)
        await self._admin.save_user_profile(qq, merged)
        updated = await service.user_detail(person_id)
        assert updated is not None  # 刚更新过，用户必然存在
        return ok({"person": updated["person"]}, request=request)

    @staticmethod
    def _merge_profile(person: dict[str, Any], body: dict[str, Any]) -> dict[str, str]:
        """PATCH 语义：缺省字段沿用现值，绝不用空值覆盖已有资料。"""
        if "nickname_override" in body and not isinstance(body["nickname_override"], str):
            raise unprocessable("nickname_override 必须是字符串", field="nickname_override")
        if "notes" in body and not isinstance(body["notes"], str):
            raise unprocessable("notes 必须是字符串", field="notes")
        if "tags" in body and not isinstance(body["tags"], list):
            raise unprocessable("tags 必须是字符串数组", field="tags")
        if "initiative_enabled" in body and not isinstance(body["initiative_enabled"], bool):
            raise unprocessable("initiative_enabled 必须是布尔值", field="initiative_enabled")
        override = body.get("nickname_override", person.get("nickname_override") or "")
        notes = body.get("notes", person.get("notes") or "")
        tags = body.get("tags", person.get("tags") or [])
        if isinstance(person.get("initiative_enabled"), bool) and "initiative_enabled" not in body:
            enabled = person["initiative_enabled"]
        else:
            enabled = body.get("initiative_enabled", True)
        return {
            "nickname_override": str(override).strip(),
            "notes": str(notes),
            "tags": json.dumps([str(tag) for tag in tags], ensure_ascii=False),
            "initiative_enabled": "1" if enabled else "0",
        }

    # ----------------------------------------------------------------- groups

    async def _v1_social_groups(self, request: web.Request) -> web.Response:
        items = await self._admin.list_groups()
        return ok({"items": items, "count": len(items)}, request=request)

    async def _v1_social_group_patch(self, request: web.Request) -> web.Response:
        group_id = str(request.match_info["group_id"])
        body = await read_json(request)
        if "participation_enabled" not in body:
            raise unprocessable("缺少 participation_enabled", field="participation_enabled")
        enabled = body["participation_enabled"]
        if not isinstance(enabled, bool):
            raise unprocessable("participation_enabled 必须是布尔值", field="participation_enabled")
        await self._admin.set_group_participation(group_id, enabled)
        rows = await self._admin.list_groups()
        updated = next((row for row in rows if str(row.get("group_id")) == group_id), None)
        return ok(
            updated or {"group_id": group_id, "participation_enabled": enabled},
            request=request,
        )

    # --------------------------------------------------------------- sessions

    async def _v1_social_sessions(self, request: web.Request) -> web.Response:
        return ok(self._social_read().sessions(), request=request)

    # ------------------------------------------------------------ relations

    async def _v1_social_relationships(self, request: web.Request) -> web.Response:
        limit = read_query_int(request, "limit", default=50, minimum=1, maximum=200)
        items = await self._social_read().relationships(limit=limit)
        return ok({"items": items, "count": len(items)}, request=request)

    # ---------------------------------------------------------- commitments

    async def _v1_social_commitments(self, request: web.Request) -> web.Response:
        status = request.query.get("status", "") or ""
        person = request.query.get("person", "") or ""
        if status and status not in COMMITMENT_STATUSES:
            raise bad_request(f"未知的承诺状态：{status}", code="social.status_unknown")
        items = await self._social_read().commitments(status=status, person=person)
        return ok(
            {
                "items": items,
                "count": len(items),
                "status": status or None,
                "person": person or None,
            },
            request=request,
        )

    async def _v1_social_commitment_detail(self, request: web.Request) -> web.Response:
        commitment_id = str(request.match_info["commitment_id"])
        data = await self._social_read().commitment_detail(commitment_id)
        if data is None:
            raise not_found(f"承诺不存在：{commitment_id}", code="social.commitment_not_found")
        return ok(data, request=request)

    # --------------------------------------------------------------- spaces

    async def _v1_social_spaces(self, request: web.Request) -> web.Response:
        return ok(self._social_read().spaces(), request=request)

    # ------------------------------------------------------------- overview

    async def _v1_social_overview(self, request: web.Request) -> web.Response:
        social_admin = getattr(self, "_social_admin", None)
        data = await social_admin.dashboard() if social_admin is not None else {"enabled": False}
        return ok(data, request=request)

    # ----------------------------------------------------------- registration

    def _register_v1_social(self, app: web.Application) -> None:
        wrap = json_endpoint
        app.router.add_get(f"{API_PREFIX}/social/users", wrap(self._v1_social_users))
        app.router.add_get(
            f"{API_PREFIX}/social/users/{{person_id}}", wrap(self._v1_social_user_detail)
        )
        app.router.add_patch(
            f"{API_PREFIX}/social/users/{{person_id}}", wrap(self._v1_social_user_patch)
        )
        app.router.add_get(f"{API_PREFIX}/social/groups", wrap(self._v1_social_groups))
        app.router.add_patch(
            f"{API_PREFIX}/social/groups/{{group_id}}", wrap(self._v1_social_group_patch)
        )
        app.router.add_get(f"{API_PREFIX}/social/sessions", wrap(self._v1_social_sessions))
        app.router.add_get(
            f"{API_PREFIX}/social/relationships", wrap(self._v1_social_relationships)
        )
        app.router.add_get(f"{API_PREFIX}/social/commitments", wrap(self._v1_social_commitments))
        app.router.add_get(
            f"{API_PREFIX}/social/commitments/{{commitment_id}}",
            wrap(self._v1_social_commitment_detail),
        )
        app.router.add_get(f"{API_PREFIX}/social/spaces", wrap(self._v1_social_spaces))
        app.router.add_get(f"{API_PREFIX}/social/overview", wrap(self._v1_social_overview))
