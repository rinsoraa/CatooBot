"""`/api/v1` 角色 / 世界 / 记忆（WebUI v1.0 · W2 契约 §7.1、§7.3、§10）。

HTTP 面与运行时测试共用 ``tests.test_web_api_runtime.v1_server``：真实
WebServer（挂载 ``DomainApiRoutes``）+ 真实会话中间件，不改任何 Core 文件。
"""

from __future__ import annotations

from tests.api_harness import error_code
from tests.test_web_api_runtime import v1_server


class TestCharacter:
    async def test_character_returns_persona_state_and_source(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/character")
            assert status == 200
            data = payload["data"]
            assert {"persona", "state", "source"} <= set(data)
            assert data["source"] in ("config", "database")
            assert "identity" in data["persona"]
            assert "mood" in data["state"] and "energy" in data["state"]

    async def test_character_patch_round_trips(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/character", body={"system_prompt": "W2 测试提示词"}
            )
            assert status == 200
            assert payload["data"]["persona"]["system_prompt"] == "W2 测试提示词"
            assert payload["data"]["source"] == "database"  # 保存后 persona 来自 DB
            _status, again = await client.get("/api/v1/character")
            assert again["data"]["persona"]["system_prompt"] == "W2 测试提示词"

    async def test_character_state_endpoint(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/character/state")
            assert status == 200
            assert isinstance(payload["data"], dict)
            assert "mood" in payload["data"]

    async def test_character_write_anonymous_is_401(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.patch(
                "/api/v1/character", body={"system_prompt": "x"}, csrf=False
            )
            assert status == 401
            assert error_code(payload) == "auth.unauthorized"


class TestWorld:
    async def test_world_projection_has_every_section(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
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
            assert data["enabled"] is True
            assert data["phase"] == bot.sandbox.phase.value
            assert isinstance(data["needs"]["bands"], dict)
            assert isinstance(data["goals"], list)
            assert isinstance(data["commitments"], list)
            assert isinstance(data["relationships"], list)
            assert len(data["relationships"]) <= 3
            assert data["world_revision"] == bot.sandbox.world_revision

    async def test_world_trace_returns_structured_items(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/world/trace?limit=5")
            assert status == 200
            data = payload["data"]
            assert data["enabled"] is True
            assert isinstance(data["items"], list)
            assert data["count"] == len(data["items"])
            for item in data["items"]:
                assert {"ts", "kind", "summary"} <= set(item)

    async def test_world_control_pause_and_resume(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/world/control/pause")
            assert status == 200
            assert payload["data"]["done"] is True
            assert payload["data"]["phase"] == "paused"
            _status, world = await client.get("/api/v1/world")
            assert world["data"]["phase"] == "paused"
            status, payload = await client.post("/api/v1/world/control/resume")
            assert status == 200
            assert payload["data"]["phase"] == "running"
            _status, world = await client.get("/api/v1/world")
            assert world["data"]["phase"] == "running"

    async def test_destructive_world_controls_need_confirm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            for action in ("reset", "reinitialize"):
                status, payload = await client.post(f"/api/v1/world/control/{action}", body={})
                assert status == 409, action
                assert error_code(payload) == "world.confirm_required"

    async def test_world_control_unknown_action_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/world/control/explode")
            assert status == 404
            assert error_code(payload) == "world.action_unknown"

    async def test_sandbox_disabled_is_graceful_and_control_503(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        overrides = {"sandbox": {"enabled": False}}
        async with v1_server(tmp_path, config_overrides=overrides) as (client, bot, _server):
            await client.login()
            assert bot.sandbox is None and bot.runtime_scheduler is None
            status, payload = await client.get("/api/v1/world")
            assert status == 200
            assert payload["data"]["enabled"] is False
            assert payload["data"]["phase"] is None
            assert payload["data"]["goals"] == []
            status, payload = await client.get("/api/v1/overview")
            assert status == 200
            assert payload["data"]["world"]["phase"] is None
            assert payload["data"]["runtime"]["scheduler"]["running"] is False
            assert payload["data"]["counts"]["goals_open"] is None
            assert payload["data"]["counts"]["experiences"] is None
            for path in ("/api/v1/world/control/pause", "/api/v1/runtime/tick"):
                status, payload = await client.post(path)
                assert status == 503, path
                assert error_code(payload) == "world.not_running"


class TestMemories:
    async def test_memories_list_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/memories?q=你好&mode=keyword&limit=5")
            assert status == 200
            data = payload["data"]
            assert {"items", "next_cursor", "total", "mode"} <= set(data)
            assert data["mode"] == "keyword"
            assert isinstance(data["items"], list)

    async def test_memory_health_is_registered_before_the_id_route(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/memories/health")
            assert status == 200  # 不是 422：health 没有被当成 memory_id
            assert isinstance(payload["data"], dict)

    async def test_memory_lifecycle_list_detail_and_archive(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await client.login()
            memory = await bot.memory.remember(
                "user",
                "10001",
                "她记得我喜欢喝冰可乐，尤其是夏天。",
                user_id="10001",
                importance=0.7,
            )
            assert memory is not None and memory.id
            status, payload = await client.get("/api/v1/memories?q=冰可乐&mode=keyword&limit=5")
            assert status == 200
            assert memory.id in [item["id"] for item in payload["data"]["items"]]
            status, payload = await client.get(f"/api/v1/memories/{memory.id}")
            assert status == 200
            assert payload["data"]["memory"]["id"] == memory.id
            assert "relations" in payload["data"]
            status, payload = await client.post(f"/api/v1/memories/{memory.id}/archive")
            assert status == 200
            assert payload["data"]["done"] is True
            _status, detail = await client.get(f"/api/v1/memories/{memory.id}")
            assert detail["data"]["memory"]["status"] == "archived"

    async def test_memory_detail_missing_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/memories/987654")
            assert status == 404
            assert error_code(payload) == "memory.not_found"

    async def test_memory_action_unknown_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/memories/1/nope")
            assert status == 400
            assert error_code(payload) == "memory.action_unknown"

    async def test_memory_action_on_missing_memory_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/memories/987654/activate")
            assert status == 404
            assert error_code(payload) == "memory.not_found"


class TestBehaviorDebugPromptsAndCharacterIO:
    """v0.8 → v1 迁移：行为调试、提示词、角色导入导出必须在新版同样可达。

    旧版分别是 /behavior/test-response、/behavior/preview、/behavior/trigger/*、
    /prompts、/character/export 与 /character/import（两步式）。危险的一方保留
    确认门：导入只有带 confirm=import 才真正写入。
    """

    async def test_test_response_is_a_dry_run(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post(
                "/api/v1/behavior/test-response", body={"text": "你好呀"}
            )
            assert status == 200
            assert isinstance(payload["data"], dict)

            status, payload = await client.post("/api/v1/behavior/test-response", body={})
            assert status == 400
            assert error_code(payload) == "behavior.text_required"

    async def test_preview_and_triggers(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post(
                "/api/v1/behavior/preview", body={"mood": "happy", "topic": "项目"}
            )
            assert status == 200 and isinstance(payload["data"], dict)

            status, payload = await client.post("/api/v1/behavior/triggers/mood_up")
            assert status == 200
            assert payload["data"]["action"] == "mood_up"
            assert payload["data"]["result"]["ok"] is True

            status, payload = await client.post("/api/v1/behavior/triggers/nope")
            assert status == 400
            assert error_code(payload) == "behavior.trigger_unknown"

    async def test_prompts_round_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/prompts")
            assert status == 200
            assert {"persona_system_prompt", "memory_extraction_prompt"} <= set(payload["data"])

            status, payload = await client.patch(
                "/api/v1/prompts",
                body={"memory_extraction_prompt": "只记住用户明确说过的偏好。"},
            )
            assert status == 200
            assert payload["data"]["memory_extraction_prompt"] == "只记住用户明确说过的偏好。"

            status, payload = await client.patch("/api/v1/prompts", body={})
            assert status == 400
            assert error_code(payload) == "prompts.empty"

    async def test_character_export_and_import_two_step(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from pathlib import Path

        from app.web.services.admin import AdminService

        apply_calls: list[tuple[bool, bool]] = []

        async def fake_confirm(_self: AdminService, path: str) -> dict:
            # 记录调用时临时文件是否存在（类方法签名带 self，因此这里多一个参数）
            apply_calls.append((True, Path(path).exists()))
            return {"ok": True, "rows": 1}

        monkeypatch.setattr(AdminService, "import_character_confirm", fake_confirm)

        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/character/export")
            assert status == 200
            document = payload["data"]
            assert {"tables", "counts", "format"} <= set(document)

            # 第一步：只预览，不写入
            status, payload = await client.post(
                "/api/v1/character/import", body={"document": document}
            )
            assert status == 200
            assert payload["data"]["applied"] is False
            assert "preview" in payload["data"]
            assert apply_calls == []

            # 第二步：确认导入
            status, payload = await client.post(
                "/api/v1/character/import", body={"document": document, "confirm": "import"}
            )
            assert status == 200
            assert payload["data"]["applied"] is True
            assert apply_calls == [(True, True)]

            status, payload = await client.post("/api/v1/character/import", body={})
            assert status == 400
            assert error_code(payload) == "character.document_required"
