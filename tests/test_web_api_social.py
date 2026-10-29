"""`/api/v1/social/*`（WebUI v1.0 · W5 契约 §7.2）。

走 ``tests.api_harness.api_server``：真实 WebServer + 真实会话中间件
（会话 Cookie + CSRF），既验证形状也验证契约里的状态码。
"""

from __future__ import annotations

import time
from typing import Any

from app.sandbox.commitments import CommitmentKind, TimeWindow
from app.web.services.admin import AdminService
from tests.api_harness import api_server, error_code


async def _insert_user(bot: Any, qq: str, nickname: str = "Alice") -> None:
    now = int(time.time())
    await bot.database.execute(
        "INSERT OR REPLACE INTO users (user_id, nickname, last_seen, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (qq, nickname, now, now),
    )


async def _insert_group(bot: Any, group_id: str, name: str = "测试群") -> None:
    now = int(time.time())
    await bot.database.execute(
        "INSERT OR REPLACE INTO groups (group_id, name, last_seen, updated_at) VALUES (?, ?, ?, ?)",
        (group_id, name, now, now),
    )


def _person_id(bot: Any, qq: str) -> str:
    return bot.sandbox.persons.for_qq(qq).person_id


class TestSocialAuthAndCsrf:
    async def test_users_anonymous_is_401(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.get("/api/v1/social/users")
            assert status == 401
            assert error_code(payload) == "auth.unauthorized"

    async def test_user_patch_anonymous_is_401(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.patch(
                "/api/v1/social/users/person_qq_x", body={"notes": "x"}, csrf=False
            )
            assert status == 401
            assert error_code(payload) == "auth.unauthorized"

    async def test_user_patch_without_csrf_is_403(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001")
            await client.login()
            person = _person_id(bot, "10001")
            status, payload = await client.patch(
                f"/api/v1/social/users/{person}", body={"notes": "x"}, csrf=False
            )
            assert status == 403
            assert error_code(payload) == "auth.csrf"

    async def test_group_patch_without_csrf_is_403(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/social/groups/456", body={"participation_enabled": False}, csrf=False
            )
            assert status == 403
            assert error_code(payload) == "auth.csrf"


class TestSocialUsers:
    async def test_users_list_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001", "Alice")
            await client.login()
            status, payload = await client.get("/api/v1/social/users")
            assert status == 200
            data = payload["data"]
            assert {"items", "total", "limit", "offset", "next_cursor"} <= set(data)
            assert data["total"] == 1
            row = data["items"][0]
            assert {
                "person_id",
                "display_name",
                "qq",
                "nickname",
                "interaction_count",
                "stage",
                "initiative_enabled",
                "notes",
                "tags",
                "last_seen",
                "relationship",
                "open_commitments",
                "recent_experience",
                "spaces",
            } <= set(row)
            assert row["qq"] == "10001"
            assert row["person_id"] == _person_id(bot, "10001")

    async def test_users_pagination_fields(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            for index, qq in enumerate(("10001", "10002", "10003")):
                await _insert_user(bot, qq, f"User{index}")
            await client.login()
            status, payload = await client.get("/api/v1/social/users?limit=2&offset=0")
            assert status == 200
            data = payload["data"]
            assert data["total"] == 3
            assert len(data["items"]) == 2
            assert data["next_cursor"] == "2"
            status, payload = await client.get("/api/v1/social/users?limit=2&offset=2")
            assert status == 200
            assert len(payload["data"]["items"]) == 1
            assert payload["data"]["next_cursor"] is None

    async def test_users_filter_matches_nickname(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001", "Mimi")
            await _insert_user(bot, "10002", "Bob")
            await client.login()
            status, payload = await client.get("/api/v1/social/users?q=mimi")
            assert status == 200
            assert [row["qq"] for row in payload["data"]["items"]] == ["10001"]

    async def test_user_detail_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001")
            me = _person_id(bot, "10001")
            await client.login()
            status, payload = await client.get(f"/api/v1/social/users/{me}")
            assert status == 200
            data = payload["data"]
            assert set(data) == {
                "person",
                "relationship",
                "commitments",
                "experiences",
                "memories",
                "spaces",
            }
            assert data["person"]["person_id"] == me

    async def test_user_detail_unknown_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/social/users/person_qq_missing")
            assert status == 404
            assert error_code(payload) == "social.user_not_found"

    async def test_user_patch_round_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001", "Alice")
            person = _person_id(bot, "10001")
            await client.login()
            status, payload = await client.patch(
                f"/api/v1/social/users/{person}",
                body={
                    "nickname_override": "小可乐",
                    "notes": "喜欢冰可乐",
                    "tags": ["朋友", "可乐"],
                    "initiative_enabled": False,
                },
            )
            assert status == 200
            updated = payload["data"]["person"]
            assert updated["nickname_override"] == "小可乐"
            assert updated["display_name"] == "小可乐"
            assert updated["notes"] == "喜欢冰可乐"
            assert updated["tags"] == ["朋友", "可乐"]
            assert updated["initiative_enabled"] is False
            _status, again = await client.get(f"/api/v1/social/users/{person}")
            assert again["data"]["person"]["notes"] == "喜欢冰可乐"
            assert again["data"]["person"]["initiative_enabled"] is False

    async def test_user_patch_merges_partial_fields(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001", "Alice")
            person = _person_id(bot, "10001")
            await client.login()
            await client.patch(
                f"/api/v1/social/users/{person}",
                body={"nickname_override": "保留我", "notes": "旧备注"},
            )
            status, payload = await client.patch(
                f"/api/v1/social/users/{person}", body={"notes": "新备注"}
            )
            assert status == 200
            person_data = payload["data"]["person"]
            assert person_data["nickname_override"] == "保留我"
            assert person_data["notes"] == "新备注"

    async def test_user_patch_bad_field_is_422(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001")
            person = _person_id(bot, "10001")
            await client.login()
            status, payload = await client.patch(
                f"/api/v1/social/users/{person}", body={"nickname": "x"}
            )
            assert status == 422
            assert error_code(payload) == "validation.failed"

    async def test_user_patch_bad_tags_is_422(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001")
            person = _person_id(bot, "10001")
            await client.login()
            status, payload = await client.patch(
                f"/api/v1/social/users/{person}", body={"tags": "not-a-list"}
            )
            assert status == 422
            assert error_code(payload) == "validation.failed"

    async def test_user_patch_unknown_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/social/users/person_qq_missing", body={"notes": "x"}
            )
            assert status == 404
            assert error_code(payload) == "social.user_not_found"


class TestSocialGroups:
    async def test_groups_list_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_group(bot, "456")
            await client.login()
            status, payload = await client.get("/api/v1/social/groups")
            assert status == 200
            assert payload["data"]["count"] == 1
            assert payload["data"]["items"][0]["group_id"] == "456"

    async def test_group_patch_goes_through_admin_service(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        calls: list[tuple[str, bool]] = []
        original = AdminService.set_group_participation

        async def spy(self, group_id: str, enabled: bool) -> None:
            calls.append((group_id, enabled))
            await original(self, group_id, enabled)

        monkeypatch.setattr(AdminService, "set_group_participation", spy)
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_group(bot, "456")
            await client.login()
            status, payload = await client.patch(
                "/api/v1/social/groups/456", body={"participation_enabled": False}
            )
            assert status == 200
            assert calls == [("456", False)]
            assert payload["data"]["participation_enabled"] in (0, False)
            _status, groups = await client.get("/api/v1/social/groups")
            row = groups["data"]["items"][0]
            assert int(row["participation_enabled"]) == 0

    async def test_legacy_group_toggle_uses_the_same_write_path(
        self, tmp_path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        calls: list[tuple[str, bool]] = []
        original = AdminService.set_group_participation

        async def spy(self, group_id: str, enabled: bool) -> None:
            calls.append((group_id, enabled))
            await original(self, group_id, enabled)

        monkeypatch.setattr(AdminService, "set_group_participation", spy)
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            async with client._session.post(
                client.base + "/groups/toggle",
                data={"group_id": "456", "enabled": "1"},
                headers={"X-CSRF-Token": client.csrf},
                allow_redirects=False,
            ) as response:
                assert response.status == 302
            assert calls == [("456", True)]

    async def test_group_patch_requires_boolean(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/social/groups/456", body={"participation_enabled": "yes"}
            )
            assert status == 422
            assert error_code(payload) == "validation.failed"


class TestSocialSessionsRelationships:
    async def test_sessions_inactive_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/social/sessions")
            assert status == 200
            assert payload["data"] == {"active": False, "items": []}

    async def test_sessions_active_after_touch(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001")
            person = _person_id(bot, "10001")
            bot.sandbox.touch_social_session(person_id=person, social_space_id="qq:456")
            await client.login()
            status, payload = await client.get("/api/v1/social/sessions")
            assert status == 200
            data = payload["data"]
            assert data["active"] is True
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert set(item) == {
                "person_id",
                "person_name",
                "social_space_id",
                "started_at",
                "last_activity_at",
                "turns",
                "interrupted",
            }
            assert item["person_id"] == person
            assert item["social_space_id"] == "qq:456"
            assert item["turns"] == 1

    async def test_session_snapshot_is_a_copy(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await client.login()
            sandbox = bot.sandbox
            sandbox.touch_social_session(person_id="person_qq_x", social_space_id="qq:1")
            snapshot = sandbox.social_session_snapshot()
            assert snapshot is not None
            assert snapshot is not sandbox._social_session
            snapshot["turns"] = 999
            snapshot["person_name"] = "被篡改"
            assert sandbox._social_session is not None
            assert sandbox._social_session["turns"] == 1
            again = sandbox.social_session_snapshot()
            assert again is not None and again["turns"] == 1

    async def test_relationships_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/social/relationships?limit=5")
            assert status == 200
            data = payload["data"]
            assert {"items", "count"} <= set(data)
            assert isinstance(data["items"], list)
            for row in data["items"]:
                assert {
                    "person_id",
                    "display_name",
                    "relation_type",
                    "trust",
                    "familiarity",
                    "closeness",
                    "social_comfort",
                    "interaction_count",
                    "last_interaction_at",
                } <= set(row)


class TestSocialCommitments:
    @staticmethod
    async def _create_commitment(bot: Any, person: str) -> Any:
        now = time.time()
        return bot.sandbox.commitments.create(
            person_id=person,
            kind=CommitmentKind.shared_activity,
            window=TimeWindow(
                earliest_at=now + 600,
                latest_at=now + 3600,
                due_at=now + 1800,
                hint="半小时后一起玩",
            ),
            target_activity="打游戏",
        )

    async def test_commitments_empty_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/social/commitments")
            assert status == 200
            assert payload["data"]["items"] == []
            assert payload["data"]["count"] == 0

    async def test_commitment_row_and_detail(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _insert_user(bot, "10001")
            person = _person_id(bot, "10001")
            created = await self._create_commitment(bot, person)
            assert created is not None
            await client.login()
            status, payload = await client.get("/api/v1/social/commitments")
            assert status == 200
            row = payload["data"]["items"][0]
            assert row["commitment_id"] == created.commitment_id
            assert set(row) == {
                "commitment_id",
                "person_id",
                "person_name",
                "kind",
                "status",
                "strength",
                "priority",
                "summary",
                "target_activity",
                "time_hint",
                "earliest_at",
                "due_at",
                "created_at",
                "goal_id",
                "goal_status",
            }
            status, payload = await client.get(
                f"/api/v1/social/commitments/{created.commitment_id}"
            )
            assert status == 200
            detail = payload["data"]
            assert {"goal", "action", "outcome"} <= set(detail)
            assert detail["goal"] is None
            assert detail["outcome"] is None

    async def test_commitment_detail_closed_outcome(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            created = await self._create_commitment(bot, "person_qq_x")
            assert created is not None
            bot.sandbox.commitments.cancel(created, by="tester", reason="测试取消")
            await client.login()
            status, payload = await client.get(
                f"/api/v1/social/commitments/{created.commitment_id}"
            )
            assert status == 200
            outcome = payload["data"]["outcome"]
            assert outcome is not None
            assert outcome["status"] == created.status.value
            assert outcome["revision"] >= 0

    async def test_commitments_status_filter_and_invalid(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            created = await self._create_commitment(bot, "person_qq_x")
            assert created is not None
            await client.login()
            status, payload = await client.get(
                f"/api/v1/social/commitments?person={created.person_id}"
            )
            assert status == 200 and payload["data"]["count"] == 1
            status, payload = await client.get("/api/v1/social/commitments?status=pending")
            assert status == 200 and payload["data"]["count"] == 1
            status, payload = await client.get("/api/v1/social/commitments?status=completed")
            assert status == 200 and payload["data"]["count"] == 0
            status, payload = await client.get("/api/v1/social/commitments?status=nope")
            assert status == 400
            assert error_code(payload) == "social.status_unknown"

    async def test_commitment_detail_unknown_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/social/commitments/cm_missing")
            assert status == 404
            assert error_code(payload) == "social.commitment_not_found"


class TestSocialSpacesAndOverview:
    async def test_spaces_shape_and_map(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            bot.config.sandbox.social_space_map = {"456": "qq:456_group"}
            await client.login()
            status, payload = await client.get("/api/v1/social/spaces")
            assert status == 200
            data = payload["data"]
            assert {"items", "map"} <= set(data)
            assert data["map"] == {"456": "qq:456_group"}
            for row in data["items"]:
                assert {
                    "space_id",
                    "kind",
                    "qq_group_id",
                    "name",
                    "participants",
                    "character_presence",
                    "interest",
                } <= set(row)

    async def test_overview_endpoint(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/social/overview")
            assert status == 200
            assert "enabled" in payload["data"]
