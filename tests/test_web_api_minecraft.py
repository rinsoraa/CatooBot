"""`/api/v1/minecraft` 集成测试（契约 §7.5）：信封、错误码、Bearer 回调门。"""

from __future__ import annotations

from typing import Any

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.service import MinecraftService
from tests.api_harness import api_server, error_code


async def test_disabled_snapshot_and_action_errors(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.get("/api/v1/minecraft")
        assert status == 200
        assert payload["data"]["enabled"] is False
        assert payload["data"]["connection"]["status"] == "DISCONNECTED"

        status, payload = await client.post("/api/v1/minecraft/join", body={"host": "x", "port": 1})
        assert status == 503
        assert error_code(payload) == "minecraft.disabled"

        status, payload = await client.post("/api/v1/minecraft/leave", body={})
        assert status == 503
        assert error_code(payload) == "minecraft.disabled"


async def test_event_webhook_requires_bearer_token(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        service = MinecraftService(
            bot,
            MinecraftConfig(
                enabled=True,
                auto_start_runtime=False,
                external_callback_token="test-token",
            ),
        )
        bot.minecraft = service
        try:
            # 无 token / 错 token → 401
            status, payload = await client.post(
                "/api/v1/minecraft/events", body={"event": "minecraft.chat"}
            )
            assert status == 401

            status, payload = await client.post(
                "/api/v1/minecraft/events",
                body={"event": "minecraft.chat", "session_id": "s", "timestamp": 1.0},
                headers={"Authorization": "Bearer test-token"},
            )
            # 事件名合法即接受（上下文字段不做形状假设）
            assert status == 200

            status, payload = await client.post(
                "/api/v1/minecraft/events",
                body={"event": "minecraft.spawned", "session_id": "s", "timestamp": 2.0},
                headers={"Authorization": "Bearer test-token"},
            )
            assert status == 200
            assert payload["data"]["accepted"] is True

            # 未知事件名 → 400 minecraft.bad_event
            status, payload = await client.post(
                "/api/v1/minecraft/events",
                body={"event": "minecraft.boom", "timestamp": 3.0},
                headers={"Authorization": "Bearer test-token"},
            )
            assert status == 400
            assert error_code(payload) == "minecraft.bad_event"
        finally:
            await service._cleanup()


async def test_event_webhook_dispatches_to_plugin_listener(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        service = MinecraftService(
            bot,
            MinecraftConfig(
                enabled=True,
                auto_start_runtime=False,
                external_callback_token="test-token",
            ),
        )
        bot.minecraft = service
        received: list[Any] = []
        service.add_listener(received.append)
        try:
            status, payload = await client.post(
                "/api/v1/minecraft/events",
                body={
                    "event": "minecraft.chat",
                    "session_id": "s1",
                    "timestamp": 1.0,
                    "username": "空凛",
                    "message": "罐头过来",
                },
                headers={"Authorization": "Bearer test-token"},
            )
            assert status == 200
            assert len(received) == 1
            assert received[0].data["username"] == "空凛"
            assert received[0].data["message"] == "罐头过来"
        finally:
            await service._cleanup()


async def test_join_reaches_runtime_and_translates_errors(tmp_path):
    """join → 503 runtime_down（auto_start=false 且无人监听）。"""
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        service = MinecraftService(
            bot,
            MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=65530),
        )
        bot.minecraft = service
        try:
            status, payload = await client.post(
                "/api/v1/minecraft/join",
                body={"host": "127.0.0.1", "port": 25565},
            )
            assert status == 503
            assert error_code(payload) == "minecraft.runtime_down"

            # 校验错误：端口越界（未触网就拒绝）
            status, payload = await client.post(
                "/api/v1/minecraft/join",
                body={"host": "127.0.0.1", "port": 99999},
            )
            assert status == 422
            assert error_code(payload) == "minecraft.invalid_target"
        finally:
            await service._cleanup()


async def test_events_webhook_without_login_and_wrong_token(tmp_path):
    """回调端点不走会话认证：未登录 + 带 Bearer 也能到 token 复检。"""
    async with api_server(tmp_path) as (client, bot, server):
        service = MinecraftService(
            bot,
            MinecraftConfig(
                enabled=True,
                auto_start_runtime=False,
                external_callback_token="test-token",
            ),
        )
        bot.minecraft = service
        try:
            # 未登录会话（没 login），带正确 Bearer：中间件放行 → 复检通过
            status, payload = await client.post(
                "/api/v1/minecraft/events",
                body={"event": "minecraft.disconnected", "session_id": None, "timestamp": 9.0},
                headers={"Authorization": "Bearer test-token"},
                csrf=False,
            )
            assert status == 200
            # 未登录 + 无 Bearer：仍然 401（不会 302）
            status, payload = await client.post(
                "/api/v1/minecraft/events",
                body={"event": "minecraft.disconnected", "timestamp": 9.0},
                csrf=False,
            )
            assert status == 401
        finally:
            await service._cleanup()


# ------------------------------------------------------- Phase 2：World Debug 端点


async def test_world_endpoint_disabled_is_available_false(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.get("/api/v1/minecraft/world")
        assert status == 200  # 读端点恒 200（功能状态不是错误）
        assert payload["data"]["available"] is False


async def test_world_endpoint_reports_primed_semantic_model(tmp_path):
    """集成：感知缓存就绪后，World Debug 端点返回语义模型 + raw 原文。"""
    from tests.test_minecraft_world import make_clock, make_perception

    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        clock, advance = make_clock()
        perception, _fake_client, _events = make_perception(clock, advance)
        await perception.poll({"near", "local"})
        assert perception.cache.online is True

        service = MinecraftService(bot, MinecraftConfig(enabled=True, auto_start_runtime=False))
        service.perception = perception
        bot.minecraft = service
        try:
            status, payload = await client.get("/api/v1/minecraft/world")
            assert status == 200
            data = payload["data"]
            assert data["available"] is True
            assert data["online"] is True
            assert data["semantic"]["self"]["location"] == "plains"
            assert data["semantic"]["points_of_interest"][0]["type"] == "crafting_table"
            assert data["raw"]["self"]["dimension"] == "overworld"
            assert data["age_seconds"] is not None
        finally:
            await service._cleanup()
