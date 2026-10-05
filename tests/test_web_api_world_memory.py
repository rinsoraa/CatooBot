"""`/api/v1/world*`、`/api/v1/character/state`、`/api/v1/memories*`（W5 契约 §7.1/§7.3）。

走 ``tests.api_harness.api_server``（真实 WebServer + 会话/CSRF 中间件）。
覆盖：世界读模型的 W5 追加块、时间线、话题动作、角色状态 PATCH、
记忆分页/过滤/edit/delete confirm 门，以及「读不移动 revision」。
"""

from __future__ import annotations

from typing import Any

from app.sandbox.models import InterruptedActionContext
from tests.api_harness import api_server, error_code


async def _remember(bot: Any, content: str, *, ref: str = "10001", scope: str = "user") -> Any:
    return await bot.memory.remember(scope, ref, content, user_id=ref, importance=0.6)


async def _create_topic(bot: Any, title: str) -> int:
    topic = await bot.behavior.topics.create("user:10001", title)
    assert topic is not None
    return int(topic.id)


class TestWorldRichModel:
    async def test_world_keeps_w2_keys_and_adds_w5_blocks(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/world")
            assert status == 200
            data = payload["data"]
            assert {
                "enabled",
                "phase",
                "location",
                "action",
                "modes",
                "needs",
                "world_revision",
                "cognitive_revision",
                "session",
                "interrupted",
                "goals",
                "commitments",
                "relationships",
            } <= set(data)
            assert {
                "needs_full",
                "spaces",
                "objects",
                "inventories",
                "pet",
                "social_spaces",
                "action_defs",
                "modes_defs",
                "goals_full",
            } <= set(data)
            assert data["interrupted"] is None  # 没有打断动作时是 null，不是 bool

    async def test_world_needs_full_matches_sandbox(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/world")
            assert status == 200
            rows = payload["data"]["needs_full"]
            assert len(rows) == len(bot.sandbox.needs.all())
            for row in rows:
                assert set(row) == {
                    "key",
                    "label",
                    "level",
                    "band",
                    "growth",
                    "critical",
                    "pressing",
                }
                assert row["band"] == bot.sandbox.needs.get(row["key"]).band()
                assert isinstance(row["label"], str) and row["label"]
                assert row["critical"] is (row["band"] == "critical")

    async def test_world_extra_blocks_come_from_sandbox_accessors(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/world")
            assert status == 200
            data = payload["data"]
            sandbox = bot.sandbox
            assert len(data["spaces"]) == len(sandbox.spaces.all())
            assert len(data["objects"]) == len(sandbox.objects.all())
            assert set(data["inventories"]) == set(sandbox.inventories.all())
            assert len(data["social_spaces"]) == len(sandbox.social_spaces)
            assert len(data["action_defs"]) == len(sandbox.actions.definitions)
            assert len(data["modes_defs"]) == len(sandbox.seed.modes)
            assert len(data["goals_full"]) == len(sandbox.goals.all())
            assert (data["pet"] is None) == (sandbox.pet is None)

    async def test_world_action_detail_when_running(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            sandbox = bot.sandbox
            definition = next(iter(sandbox.actions.definitions.values()))
            instance = sandbox.actions.start(
                definition,
                space_id=sandbox.character.location,
                reason_code="w5_test",
                detail="W5 测试动作",
            )
            sandbox.current_action = instance
            await client.login()
            status, payload = await client.get("/api/v1/world")
            assert status == 200
            action = payload["data"]["action"]
            assert action is not None
            assert action["definition_id"] == definition.id
            assert action["detail"] == "W5 测试动作"
            assert action["reason_code"] == "w5_test"
            assert action["space_id"] == sandbox.character.location
            assert "goal_id" in action and "goal_step" in action

    async def test_world_interrupted_block(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            bot.sandbox._interrupted = InterruptedActionContext(
                action_id="act_w5",
                definition_id="gaming",
                progress=0.4,
                remaining_minutes=12.5,
                interrupt_reason="有人找她聊天",
            )
            try:
                await client.login()
                status, payload = await client.get("/api/v1/world")
                assert status == 200
                block = payload["data"]["interrupted"]
                assert block == {
                    "active": True,
                    "definition_id": "gaming",
                    "remaining_minutes": 12.5,
                    "reason": "有人找她聊天",
                    "progress": 0.4,
                }
            finally:
                bot.sandbox._interrupted = None

    async def test_world_reads_do_not_move_revisions(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await client.login()
            sandbox = bot.sandbox
            before = (sandbox.world_revision, sandbox.cognitive_revision)
            for path in (
                "/api/v1/world",
                "/api/v1/world/timeline?limit=10",
                "/api/v1/world/topics",
                "/api/v1/social/relationships",
                "/api/v1/social/commitments",
                "/api/v1/social/sessions",
                "/api/v1/social/spaces",
            ):
                status, _payload = await client.get(path)
                assert status == 200, path
            assert (sandbox.world_revision, sandbox.cognitive_revision) == before

    async def test_world_anonymous_is_401(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.get("/api/v1/world/timeline")
            assert status == 401
            assert error_code(payload) == "auth.unauthorized"


class TestTimelineAndTopics:
    async def test_timeline_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/world/timeline?limit=5")
            assert status == 200
            data = payload["data"]
            assert data["enabled"] is True
            assert data["count"] == len(data["items"])
            for item in data["items"]:
                assert set(item) == {
                    "ts",
                    "event_type",
                    "summary",
                    "location",
                    "action",
                    "revisions",
                }

    async def test_topics_list_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await _create_topic(bot, "周末去爬山W5")
            await client.login()
            status, payload = await client.get("/api/v1/world/topics")
            assert status == 200
            data = payload["data"]
            assert data["count"] == len(data["items"]) >= 1
            assert {"title", "status", "scope_key"} <= set(data["items"][0])

    async def test_topic_action_unknown_action_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/world/topics/1/explode")
            assert status == 400
            assert error_code(payload) == "topic.action_unknown"

    async def test_topic_action_unknown_topic_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/world/topics/999999/resolve")
            assert status == 404
            assert error_code(payload) == "topic.not_found"

    async def test_topic_action_resolve(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            topic_id = await _create_topic(bot, "未闭环话题W5")
            await client.login()
            status, payload = await client.post(f"/api/v1/world/topics/{topic_id}/resolve")
            assert status == 200
            assert payload["data"]["done"] is True
            _status, topics = await client.get("/api/v1/world/topics?status=resolved")
            assert topic_id in [item["id"] for item in topics["data"]["items"]]

    async def test_topic_action_without_csrf_is_403(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/world/topics/1/resolve", csrf=False)
            assert status == 403
            assert error_code(payload) == "auth.csrf"


class TestCharacterStatePatch:
    async def test_state_patch_updates_fields(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/character/state",
                body={"mood": "被W5测试改过的好心情", "energy": 0.42},
            )
            assert status == 200
            assert payload["data"]["mood"] == "被W5测试改过的好心情"
            assert payload["data"]["energy"] == 0.42
            _status, again = await client.get("/api/v1/character/state")
            assert again["data"]["mood"] == "被W5测试改过的好心情"

    async def test_state_patch_unknown_field_is_422(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/character/state", body={"telepathy": "on"}
            )
            assert status == 422
            assert error_code(payload) == "validation.failed"

    async def test_state_patch_bad_energy_is_422(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch("/api/v1/character/state", body={"energy": "很多"})
            assert status == 422
            assert error_code(payload) == "validation.failed"

    async def test_state_patch_without_csrf_is_403(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/character/state", body={"mood": "x"}, csrf=False
            )
            assert status == 403
            assert error_code(payload) == "auth.csrf"


class TestMemoriesV5:
    async def test_browse_pagination_fields(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            contents = (
                "W5分页甲：她记得我喜欢喝冰可乐。",
                "W5分页乙：她记得我周末去爬山。",
                "W5分页丙：她记得我最讨厌香菜。",
            )
            for content in contents:
                memory = await _remember(bot, content)
                assert memory is not None
            await client.login()
            status, payload = await client.get(
                "/api/v1/memories?scope_key=user:10001&limit=2&offset=0"
            )
            assert status == 200
            data = payload["data"]
            assert data["mode"] == "browse"
            assert data["total"] == 3
            assert len(data["items"]) == 2
            assert data["next_cursor"] == "2"
            status, payload = await client.get(
                "/api/v1/memories?scope_key=user:10001&limit=2&offset=2"
            )
            assert status == 200
            assert len(payload["data"]["items"]) == 1
            assert payload["data"]["next_cursor"] is None

    async def test_keyword_search_keeps_w2_keys(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            memory = await _remember(bot, "她记得我最爱吃W5独特口味的冰可乐。")
            assert memory is not None
            await client.login()
            status, payload = await client.get("/api/v1/memories?q=W5独特口味&mode=keyword&limit=5")
            assert status == 200
            data = payload["data"]
            assert {"items", "next_cursor", "total", "mode"} <= set(data)
            assert data["mode"] == "keyword"
            assert data["total"] == 1
            assert memory.id in [item["id"] for item in data["items"]]
            assert data["semantic_available"] is False

    async def test_person_filter_maps_to_scope_key(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            first = await _remember(bot, "只属于10001的W5记忆。", ref="10001")
            second = await _remember(bot, "只属于10002的W5记忆。", ref="10002")
            assert first is not None and second is not None
            await client.login()
            status, payload = await client.get("/api/v1/memories?person=10001&limit=10")
            assert status == 200
            ids = [item["id"] for item in payload["data"]["items"]]
            assert first.id in ids and second.id not in ids
            assert all(item["scope_key"] == "user:10001" for item in payload["data"]["items"])

    async def test_timeline_endpoint(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            memory = await _remember(bot, "W5时间线里应该出现的一条记忆。")
            assert memory is not None
            await client.login()
            status, payload = await client.get(
                "/api/v1/memories/timeline?scope_key=user:10001&limit=10"
            )
            assert status == 200
            data = payload["data"]
            assert data["count"] == len(data["items"]) >= 1
            assert memory.id in [item["id"] for item in data["items"]]

    async def test_edit_updates_content(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            memory = await _remember(bot, "W5编辑前的原始内容。")
            assert memory is not None
            await client.login()
            status, payload = await client.post(
                f"/api/v1/memories/{memory.id}/edit",
                body={"content": "W5编辑后的新内容。", "importance": 0.8},
            )
            assert status == 200
            assert payload["data"]["action"] == "edit"
            _status, detail = await client.get(f"/api/v1/memories/{memory.id}")
            assert detail["data"]["memory"]["content"] == "W5编辑后的新内容。"

    async def test_edit_requires_content(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/memories/1/edit", body={})
            assert status == 400
            assert error_code(payload) == "memory.content_required"

    async def test_delete_requires_confirm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            memory = await _remember(bot, "W5删除确认门测试：没有确认不能删。")
            assert memory is not None
            await client.login()
            status, payload = await client.post(f"/api/v1/memories/{memory.id}/delete", body={})
            assert status == 409
            assert error_code(payload) == "memory.confirm_required"
            _status, detail = await client.get(f"/api/v1/memories/{memory.id}")
            assert detail["data"]["memory"]["id"] == memory.id

    async def test_delete_with_confirm_removes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            memory = await _remember(bot, "W5确认后应当被删除的记忆。")
            assert memory is not None
            await client.login()
            status, payload = await client.post(
                f"/api/v1/memories/{memory.id}/delete", body={"confirm": "delete"}
            )
            assert status == 200 and payload["data"]["done"] is True
            status, payload = await client.get(f"/api/v1/memories/{memory.id}")
            assert status == 404
            assert error_code(payload) == "memory.not_found"

    async def test_memory_mutation_without_csrf_is_403(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            memory = await _remember(bot, "W5 CSRF 门测试用记忆。")
            assert memory is not None
            await client.login()
            status, payload = await client.post(
                f"/api/v1/memories/{memory.id}/delete",
                body={"confirm": "delete"},
                csrf=False,
            )
            assert status == 403
            assert error_code(payload) == "auth.csrf"

    async def test_memories_anonymous_is_401(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.get("/api/v1/memories")
            assert status == 401
            assert error_code(payload) == "auth.unauthorized"


class TestMemoryOpsMigratedFromV08:
    """旧版「记忆运维」在新版的可达性：向量、整理、检索调试、会话清空。

    这些能力在 v0.8 的 /memory/embeddings、/memory/consolidation、
    /memory/retrieval-debug、/api/sessions/clear 里；v1 必须同样可达，
    并且沿用 v1 的危险动作确认约定。
    """

    async def test_embedding_status_is_readable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/memories/embeddings")
            assert status == 200
            assert "available" in payload["data"]

    async def test_embedding_actions_run_and_echo(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            for action in ("rebuild", "retry"):
                status, payload = await client.post(f"/api/v1/memories/embeddings/{action}")
                assert status == 200, payload
                assert payload["data"]["action"] == action
                assert "result" in payload["data"]

    async def test_clear_cache_needs_confirm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/memories/embeddings/clear-cache")
            assert status == 409
            assert error_code(payload) == "memory.confirm_required"
            status, payload = await client.post(
                "/api/v1/memories/embeddings/clear-cache", body={"confirm": "clear-cache"}
            )
            assert status == 200 and payload["data"]["action"] == "clear-cache"

    async def test_unknown_embedding_action_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/memories/embeddings/nope")
            assert status == 400
            assert error_code(payload) == "memory.embedding_action_unknown"

    async def test_static_paths_do_not_shadow_the_memory_id_route(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            memory = await _remember(bot, "路由顺序回归：按 ID 取详情仍然可用。")
            assert memory is not None
            await client.login()
            status, payload = await client.get(f"/api/v1/memories/{memory.id}")
            assert status == 200
            assert payload["data"]["memory"]["content"].startswith("路由顺序回归")

    async def test_consolidation_status_and_run(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/memories/consolidation")
            assert status == 200
            assert "enabled" in payload["data"]
            status, payload = await client.post(
                "/api/v1/memories/consolidation/run", body={"scope": ""}
            )
            assert status == 200
            assert payload["data"]["scope"] == ""
            assert "result" in payload["data"]

    async def test_retrieval_debug_needs_a_query(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            memory = await _remember(bot, "检索调试：她记得用户喜欢在雨天听歌。")
            assert memory is not None
            await client.login()
            status, payload = await client.get("/api/v1/memories/retrieval-debug?q=雨天")
            assert status == 200
            assert payload["data"]["query"] == "雨天"
            status, payload = await client.get("/api/v1/memories/retrieval-debug")
            assert status == 400
            assert error_code(payload) == "memory.query_required"

    async def test_session_clear_needs_confirm_and_clears(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):
            await bot.ai.conversations.append_user_message("private:10001", "以前聊过的话")
            await client.login()
            status, payload = await client.post("/api/v1/sessions/private:10001/clear")
            assert status == 409
            assert error_code(payload) == "session.confirm_required"

            status, payload = await client.post(
                "/api/v1/sessions/private:10001/clear", body={"confirm": "clear"}
            )
            assert status == 200 and payload["data"]["cleared"] is True
            rows = await bot.database.fetchall(
                "SELECT id FROM conversations WHERE session_id = ?", ("private:10001",)
            )
            assert rows == []
