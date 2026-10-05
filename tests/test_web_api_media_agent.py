"""`/api/v1/stickers*`、`/api/v1/expressions*`、`/api/v1/agent*`（W5 §7.4）。

媒体/口癖/Agent 三个域共用一个真实 WebServer：HTTP + 会话 Cookie + CSRF，
动作全部落到既有 ``StickerAdminService`` / ``ExpressionAdminService`` /
``AgentAdminService``，测试只证明契约形状、确认门与状态推导。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from app.config.settings import AppConfig
from app.core.bot import Bot
from app.media.models import StickerAsset
from app.web.api.agent_api import AgentApiRoutes
from app.web.api.media_api import MediaApiRoutes
from app.web.server import WebServer
from tests.api_harness import ApiClient, error_code, free_port
from tests.conftest import BIBLE_PATH
from tests.test_web_realtime import DummyAdapter


class V1Server(WebServer, MediaApiRoutes, AgentApiRoutes):
    """W5 路由组合面（父任务合入 ApiRoutes 后本子类仍兼容）。"""


@asynccontextmanager
async def v1_server(tmp_path, *, config_overrides: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    port = free_port()
    sections: dict[str, Any] = {
        "bot": {"name": "TestBot"},
        "database": {"url": "sqlite:///" + str(tmp_path / "api.db")},
        "logging": {"log_dir": str(tmp_path / "logs"), "level": "WARNING"},
        "web": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": port,
            "username": "admin",
            "password": "pw123",
        },
        "sandbox": {"enabled": True, "bible_path": str(BIBLE_PATH)},
        # 贴纸目录隔离到 tmp：reindex 绝不扫描仓库里的真实 data/stickers
        "media": {"sticker_dir": str(tmp_path / "stickers")},
    }
    sections.update(config_overrides or {})
    config = AppConfig(**sections)
    bot = Bot(config, DummyAdapter())
    await bot.database.connect()
    await bot.character.start()
    server = V1Server(config.web, bot)
    server._config_admin.overrides_path = tmp_path / "overrides.yaml"
    await server.start()
    client = ApiClient(f"http://127.0.0.1:{port}")
    try:
        yield client, bot, server
    finally:
        await client.close()
        await server.stop()
        await bot.shutdown()


async def _login(client: ApiClient) -> None:
    status, payload = await client.login()
    assert status == 200, payload


async def _add_sticker(bot: Bot, tmp_path: Any, **overrides: Any) -> str:
    target = tmp_path / f"sticker_{overrides.get('file_name', 'a.gif')}"
    target.write_bytes(b"GIF89a")
    asset = StickerAsset(
        file_path=str(target),
        file_name=overrides.get("file_name", "a.gif"),
        mime_type="image/gif",
        origin=overrides.get("origin", "manual_import"),
        origin_user_id=overrides.get("origin_user_id", ""),
        emotion_tags=overrides.get("emotion_tags", ["开心"]),
        intent_tags=overrides.get("intent_tags", ["回应"]),
        visual_summary=overrides.get("visual_summary", "一只在笑的猫"),
        safety_status=overrides.get("safety_status", "ok"),
        status=overrides.get("status", "active"),
    )
    stored = await bot.media.library.insert(asset)
    return str(stored.id)


async def _add_expression(bot: Bot, **overrides: Any) -> int:
    import time as _time

    now = int(_time.time())
    await bot.database.execute(
        """INSERT INTO expression_patterns
               (scope_key, pattern, kind, sample_count, speaker_count,
                first_seen_at, last_seen_at, use_count, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            overrides.get("scope_key", "group:100"),
            overrides.get("pattern", "好耶"),
            overrides.get("kind", "word"),
            overrides.get("sample_count", 5),
            overrides.get("speaker_count", 3),
            now - 100,
            now,
            0,
            overrides.get("status", "active"),
            now - 200,
            now,
        ),
    )
    row = await bot.database.fetchone("SELECT id FROM expression_patterns ORDER BY id DESC LIMIT 1")
    return int(row["id"])


async def _add_agent_task(bot: Bot, status: str = "ready") -> str:
    import time as _time

    now = int(_time.time())
    task_id = f"task_{status}"
    await bot.database.execute(
        """INSERT INTO agent_tasks
               (task_id, goal_id, session_id, user_id, classification, status,
                created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, f"goal_{status}", "webui:test", "admin", "multi_step", status, now, now),
    )
    return task_id


# --------------------------------------------------------------------- media


class TestStickers:
    async def test_anonymous_stickers_is_401(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.get("/api/v1/stickers")
            assert status == 401
            assert error_code(payload) == "auth.unauthorized"

    async def test_stickers_empty_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/stickers")
            assert status == 200
            data = payload["data"]
            assert set(data) == {"items", "stats", "total"}
            assert data["items"] == []
            assert data["total"] == 0
            assert data["stats"]["enabled"] is True

    async def test_sticker_item_projects_the_asset_model(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            sticker_id = await _add_sticker(bot, tmp_path, file_name="cat.gif")
            status, payload = await client.get("/api/v1/stickers")
            assert status == 200
            item = payload["data"]["items"][0]
            assert set(item) == {
                "sticker_id",
                "file",
                "file_name",
                "preview_url",
                "emotion",
                "emotion_tags",
                "intent",
                "intent_tags",
                "status",
                "origin",
                "origin_user",
                "usage_count",
                "last_used_at",
                "created_at",
                "safety_status",
                "valid",
            }
            assert item["sticker_id"] == sticker_id
            assert item["file_name"] == "cat.gif"
            assert item["emotion"] == "开心"
            assert item["safety_status"] == "ok"
            assert item["created_at"]
            assert item["valid"] is True
            # 没有可服务贴纸文件的真实路由 → 诚实返回 null，不伪造
            assert item["preview_url"] is None

    async def test_sticker_filters_and_paging(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            await _add_sticker(bot, tmp_path, file_name="a.gif", emotion_tags=["开心"])
            await _add_sticker(bot, tmp_path, file_name="b.gif", emotion_tags=["难过"])
            await _add_sticker(bot, tmp_path, file_name="c.gif", status="disabled")

            status, payload = await client.get("/api/v1/stickers?emotion=难过")
            assert status == 200
            assert [row["file_name"] for row in payload["data"]["items"]] == ["b.gif"]

            _status, payload = await client.get("/api/v1/stickers?status=disabled")
            assert [row["file_name"] for row in payload["data"]["items"]] == ["c.gif"]
            assert payload["data"]["total"] == 1

            _status, payload = await client.get("/api/v1/stickers?limit=1&offset=1")
            assert len(payload["data"]["items"]) == 1
            assert payload["data"]["total"] == 2

    async def test_sticker_disable_and_enable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            sticker_id = await _add_sticker(bot, tmp_path)
            status, payload = await client.post(f"/api/v1/stickers/{sticker_id}/disable", body={})
            assert status == 200
            assert payload["data"]["status"] == "disabled"
            _status, listing = await client.get("/api/v1/stickers?status=disabled")
            assert listing["data"]["total"] == 1
            status, payload = await client.post(f"/api/v1/stickers/{sticker_id}/enable", body={})
            assert status == 200
            assert payload["data"]["status"] == "active"

    async def test_sticker_delete_needs_confirm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            sticker_id = await _add_sticker(bot, tmp_path)
            status, payload = await client.post(f"/api/v1/stickers/{sticker_id}/delete", body={})
            assert status == 409
            assert error_code(payload) == "media.confirm_required"

    async def test_sticker_delete_archives(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            sticker_id = await _add_sticker(bot, tmp_path)
            status, payload = await client.post(
                f"/api/v1/stickers/{sticker_id}/delete", body={"confirm": "delete"}
            )
            assert status == 200
            assert payload["data"]["status"] == "archived"
            _status, detail_asset = await client.get("/api/v1/stickers?status=archived")
            assert detail_asset["data"]["total"] == 1

    async def test_sticker_unknown_and_bad_action(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post("/api/v1/stickers/nope/enable", body={})
            assert status == 404
            assert error_code(payload) == "media.sticker_unknown"
            status, payload = await client.post("/api/v1/stickers/nope/explode", body={})
            assert status == 400
            assert error_code(payload) == "media.action_unknown"

    async def test_sticker_reindex_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post("/api/v1/stickers/reindex", body={})
            assert status == 200
            data = payload["data"]
            assert set(data) == {"scanned", "added", "updated", "removed", "state"}
            assert data["scanned"] == 0
            assert data["removed"] == 0
            # scan() 不区分新增/重扫 → 诚实 null
            assert data["added"] is None
            assert data["updated"] is None


class TestExpressions:
    async def test_expressions_shape_and_mapping(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            pattern_id = await _add_expression(bot)
            status, payload = await client.get("/api/v1/expressions")
            assert status == 200
            data = payload["data"]
            assert set(data) == {"items", "stats"}
            item = data["items"][0]
            assert item["pattern_id"] == pattern_id
            assert item["occurrences"] == 5
            assert item["speakers"] == 3
            assert item["group_id"] == "group:100"
            assert item["first_seen"] and item["last_seen"]

    async def test_expression_filters(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            await _add_expression(bot, pattern="好耶", scope_key="group:100")
            await _add_expression(bot, pattern="坏了", scope_key="group:200", status="disabled")
            _status, payload = await client.get("/api/v1/expressions?group_id=group:200")
            assert [row["pattern"] for row in payload["data"]["items"]] == ["坏了"]
            _status, payload = await client.get("/api/v1/expressions?status=active")
            assert [row["pattern"] for row in payload["data"]["items"]] == ["好耶"]
            _status, payload = await client.get("/api/v1/expressions?limit=1")
            assert len(payload["data"]["items"]) == 1

    async def test_expression_disable_and_enable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            pattern_id = await _add_expression(bot)
            status, payload = await client.post(
                f"/api/v1/expressions/{pattern_id}/disable", body={}
            )
            assert status == 200
            assert payload["data"]["status"] == "disabled"
            status, payload = await client.post(f"/api/v1/expressions/{pattern_id}/enable", body={})
            assert status == 200
            assert payload["data"]["status"] == "active"

    async def test_expression_delete_gate_and_delete(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            pattern_id = await _add_expression(bot)
            status, payload = await client.post(f"/api/v1/expressions/{pattern_id}/delete", body={})
            assert status == 409
            assert error_code(payload) == "media.confirm_required"
            status, payload = await client.post(
                f"/api/v1/expressions/{pattern_id}/delete", body={"confirm": "delete"}
            )
            assert status == 200
            assert payload["data"]["deleted"] is True
            _status, listing = await client.get("/api/v1/expressions")
            assert listing["data"]["items"] == []

    async def test_expression_unknown_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post("/api/v1/expressions/999/enable", body={})
            assert status == 404
            assert error_code(payload) == "media.pattern_unknown"
            status, payload = await client.post("/api/v1/expressions/999/explode", body={})
            assert status == 400
            assert error_code(payload) == "media.action_unknown"


# --------------------------------------------------------------------- agent


class TestAgent:
    async def test_agent_panel_shape_and_ready_status(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/agent")
            assert status == 200
            data = payload["data"]
            assert set(data) == {
                "status",
                "health",
                "active_tasks",
                "recent_tasks",
                "policy",
                "budget",
                "planner_model",
                "evaluator_model",
            }
            assert data["status"] == "ready"
            assert set(data["health"]) >= {"planner", "executor", "evaluator", "task_store"}
            assert isinstance(data["active_tasks"], list)
            assert isinstance(data["recent_tasks"], list)
            assert data["budget"]["max_steps"] >= 1
            assert data["planner_model"]

    async def test_agent_status_is_disabled_when_config_off(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        overrides = {"agent": {"enabled": False}}
        async with v1_server(tmp_path, config_overrides=overrides) as (client, bot, _server):
            await _login(client)
            assert bot.agent.enabled is False
            status, payload = await client.get("/api/v1/agent")
            assert status == 200
            assert payload["data"]["status"] == "disabled"

    async def test_agent_tasks_list_and_filter(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            await _add_agent_task(bot, status="ready")
            await _add_agent_task(bot, status="completed")
            status, payload = await client.get("/api/v1/agent/tasks")
            assert status == 200
            assert set(payload["data"]) == {"items", "total"}
            assert payload["data"]["total"] == 2
            _status, payload = await client.get("/api/v1/agent/tasks?status=completed")
            assert payload["data"]["total"] == 1
            assert payload["data"]["items"][0]["status"] == "completed"

    async def test_agent_task_detail_and_unknown(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            task_id = await _add_agent_task(bot, status="ready")
            status, payload = await client.get(f"/api/v1/agent/tasks/{task_id}")
            assert status == 200
            assert payload["data"]["task"]["task_id"] == task_id
            assert {"task", "goal", "plans", "steps", "observations", "traces"} <= set(
                payload["data"]
            )
            status, payload = await client.get("/api/v1/agent/tasks/nope")
            assert status == 404
            assert error_code(payload) == "agent.task_unknown"

    async def test_agent_task_actions_cancel_conflict_and_replay(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            task_id = await _add_agent_task(bot, status="ready")
            status, payload = await client.post(f"/api/v1/agent/tasks/{task_id}/cancel", body={})
            assert status == 200
            assert payload["data"]["ok"] is True
            # 终态后再 pause：Core 的既有状态机拒绝，API 如实 409
            status, payload = await client.post(f"/api/v1/agent/tasks/{task_id}/pause", body={})
            assert status == 409
            assert error_code(payload) == "agent.action_failed"
            status, payload = await client.post(f"/api/v1/agent/tasks/{task_id}/replay", body={})
            assert status == 200
            assert payload["data"]["ok"] is True
            assert "simulation" in payload["data"]

    async def test_agent_unknown_action_and_task(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            task_id = await _add_agent_task(bot, status="ready")
            status, payload = await client.post(f"/api/v1/agent/tasks/{task_id}/explode", body={})
            assert status == 400
            assert error_code(payload) == "agent.action_unknown"
            status, payload = await client.post("/api/v1/agent/tasks/nope/cancel", body={})
            assert status == 404
            assert error_code(payload) == "agent.task_unknown"

    async def test_agent_simulate_is_dry_run(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post(
                "/api/v1/agent/simulate", body={"text": "帮我查一下明天北京的天气"}
            )
            assert status == 200
            assert payload["data"]["dry_run"] is True

    async def test_agent_simulate_without_text_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post("/api/v1/agent/simulate", body={})
            assert status == 400
            assert payload["error"]["field"] == "text"

    async def test_agent_simulate_execute_flag_round_trips(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """旧版复选框语义：execute=true 时再跑一遍打桩执行的干跑（仍然不发任何消息）。"""
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post(
                "/api/v1/agent/simulate",
                body={"text": "帮我查一下明天北京的天气", "execute": True},
            )
            assert status == 200
            assert payload["data"]["execute"] is True
            assert payload["data"]["dry_run"] is True
