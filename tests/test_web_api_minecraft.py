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


# ------------------------------------------------ Phase 3B：Action Runtime 端点


async def test_action_endpoints_require_enabled_service(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post(
            "/api/v1/minecraft/look_at", body={"x": 1, "y": 2, "z": 3}
        )
        assert status == 503 and error_code(payload) == "minecraft.disabled"
        status, payload = await client.post("/api/v1/minecraft/stop", body={})
        assert status == 503 and error_code(payload) == "minecraft.disabled"


async def test_look_at_validates_locally_and_translates_runtime_errors(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        service = MinecraftService(
            bot,
            MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=65530),
        )
        bot.minecraft = service
        try:
            # 非法坐标 → 422（本地校验，无需 runtime）
            status, payload = await client.post(
                "/api/v1/minecraft/look_at", body={"x": "abc", "y": 1, "z": 2}
            )
            assert status == 422 and error_code(payload) == "minecraft.action_invalid"
            # 合法坐标但 runtime 不可达 → 503 runtime_down
            status, payload = await client.post(
                "/api/v1/minecraft/look_at", body={"x": 1, "y": 2, "z": 3}
            )
            assert status == 503 and error_code(payload) == "minecraft.runtime_down"
        finally:
            await service._cleanup()


async def test_look_at_and_stop_reach_runtime(tmp_path):
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.stop_cancelled = ["act_running"]
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=fake.port),
            )
            bot.minecraft = service
            try:
                status, payload = await client.post(
                    "/api/v1/minecraft/look_at", body={"x": 10, "y": 64, "z": -5}
                )
                assert status == 200
                data = payload["data"]
                assert data["status"] == "SUCCEEDED" and data["action"] == "look_at"
                assert str(data["action_id"]).startswith("act_")
                assert fake.look_at_calls == [{"x": 10.0, "y": 64.0, "z": -5.0}]

                status, payload = await client.post("/api/v1/minecraft/stop", body={})
                assert status == 200
                assert payload["data"]["cancelled"] == ["act_running"]
                assert payload["data"]["status"] == "IDLE"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_minecraft_projection_includes_action_block(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        # 未启用：读端点恒 200，动作视图是 IDLE
        status, payload = await client.get("/api/v1/minecraft")
        assert status == 200
        assert payload["data"]["action"]["status"] == "IDLE"

        # 启用后：动作事件驱动镜像（started → RUNNING）。
        # 必须指向一个死端口：GET /minecraft 的 status() 会去问 runtime 并用实况
        # 覆盖镜像——若撞上操作者机器上真在跑的 runtime（默认端口 25580），
        # 事件镜像会被无关 session 的 IDLE 冲掉（测试隔离事故，实测踩过）。
        service = MinecraftService(
            bot,
            MinecraftConfig(
                enabled=True,
                auto_start_runtime=False,
                runtime_port=65530,
                external_callback_token="act-test-token",
            ),
        )
        bot.minecraft = service
        try:
            await client.post(
                "/api/v1/minecraft/events",
                body={
                    "event": "minecraft.action.started",
                    "session_id": "s1",
                    "timestamp": 1.0,
                    "action": "look_at",
                    "action_id": "act_mirror",
                },
                headers={"Authorization": "Bearer act-test-token"},
                csrf=False,
            )
            status, payload = await client.get("/api/v1/minecraft")
            action = payload["data"]["action"]
            assert action["status"] == "RUNNING" and action["action_id"] == "act_mirror"
        finally:
            await service._cleanup()


# ------------------------------------------------ Phase 3C：move_to 端点


async def test_move_to_endpoint_disabled(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post(
            "/api/v1/minecraft/move_to", body={"x": 1, "y": 2, "z": 3}
        )
        assert status == 503 and error_code(payload) == "minecraft.disabled"


async def test_move_to_endpoint_validates_and_translates(tmp_path):
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=fake.port),
            )
            bot.minecraft = service
            try:
                # 非法坐标 → 422（本地校验）
                status, payload = await client.post(
                    "/api/v1/minecraft/move_to", body={"x": "abc", "y": 1, "z": 2}
                )
                assert status == 422 and error_code(payload) == "minecraft.action_invalid"
                # 正常移动 → 200 + 启动即 RUNNING（Phase 3E：终点经事件送达）
                status, payload = await client.post(
                    "/api/v1/minecraft/move_to", body={"x": 10, "y": 64, "z": -5}
                )
                assert status == 200
                data = payload["data"]
                assert data["action"] == "move_to" and data["status"] == "RUNNING"
                assert str(data["action_id"]).startswith("act_")
                assert fake.move_to_calls == [{"x": 10.0, "y": 64.0, "z": -5.0}]
                # 启动阶段同步拒绝（runtime 词表照旧翻译）→ 500 minecraft.path_not_found
                fake.move_to_plan.append({"error": ("path.not_found", 500)})
                status, payload = await client.post(
                    "/api/v1/minecraft/move_to", body={"x": 12, "y": 64, "z": -5}
                )
                assert status == 500 and error_code(payload) == "minecraft.path_not_found"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


# ------------------------------------------------ Phase 3E：Agent 只读投影


async def test_minecraft_projection_includes_agent_block(tmp_path):
    """§三十/§四十八：GET /minecraft 带 LLM Tool Debug（六个工具的风险/开关/是否允许）。"""
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        # 未启用：仍然 200，agent 块如实说「都不可用」
        status, payload = await client.get("/api/v1/minecraft")
        assert status == 200
        agent = payload["data"]["agent"]
        assert agent["enabled"] is False
        assert {row["name"] for row in agent["tools"]} == {
            "minecraft_world",
            "minecraft_chat",
            "minecraft_look_at",
            "minecraft_move_to",
            "minecraft_follow_player",
            "minecraft_stop",
        }
        assert all(row["allowed"] is False for row in agent["tools"])
        assert all(row["reason"] == "minecraft.disabled" for row in agent["tools"])

        # 启用 Tool Runtime + 装配 bridge：SAFE 允许、LOW 因「不在世界」被拒（reason 如实）
        await bot.tools.start()  # 注册六个 minecraft_* 工具（api_server 不跑 Bot.start）
        service = MinecraftService(bot, MinecraftConfig(enabled=True, auto_start_runtime=False))
        bot.minecraft = service
        try:
            from app.integrations.minecraft.agent import MinecraftAgentBridge

            service.agent = MinecraftAgentBridge(service)
            status, payload = await client.get("/api/v1/minecraft")
            assert status == 200
            agent = payload["data"]["agent"]
            assert agent["enabled"] is True
            rows = {row["name"]: row for row in agent["tools"]}
            assert rows["minecraft_world"]["allowed"] is True
            assert rows["minecraft_world"]["risk"] == "SAFE"
            assert rows["minecraft_move_to"]["risk"] == "LOW"
            # 不在世界里：需要世界的动作被拒，理由就是稳定错误码
            assert rows["minecraft_move_to"]["allowed"] is False
            assert rows["minecraft_move_to"]["reason"] == "minecraft.offline"
            assert rows["minecraft_stop"]["allowed"] is True  # 停止永远可用
            assert agent["context"]["online"] is False
            assert agent["policy"]["risk_flags"]["MEDIUM"] is False
        finally:
            await service._cleanup()


# ------------------------------------------------ Phase 3D：follow_player 端点


async def test_follow_player_endpoint_disabled(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post(
            "/api/v1/minecraft/follow_player", body={"username": "空凛"}
        )
        assert status == 503 and error_code(payload) == "minecraft.disabled"


async def test_follow_player_endpoint_validates(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        service = MinecraftService(
            bot,
            MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=65530),
        )
        bot.minecraft = service
        try:
            status, payload = await client.post(
                "/api/v1/minecraft/follow_player", body={"username": "  "}
            )
            assert status == 422 and error_code(payload) == "minecraft.action_invalid"
            status, payload = await client.post(
                "/api/v1/minecraft/follow_player", body={"username": "空凛", "distance": 9}
            )
            assert status == 422 and error_code(payload) == "minecraft.action_invalid"
            # 镜像不是 ONLINE → 409（Service 层在线校验）
            status, payload = await client.post(
                "/api/v1/minecraft/follow_player", body={"username": "空凛"}
            )
            assert status == 409 and error_code(payload) == "minecraft.not_connected"
        finally:
            await service._cleanup()


async def test_follow_player_endpoint_reaches_runtime(tmp_path):
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=fake.port),
            )
            bot.minecraft = service
            try:
                await service.status()  # 镜像 → ONLINE
                status, payload = await client.post(
                    "/api/v1/minecraft/follow_player", body={"username": "空凛", "distance": 3}
                )
                assert status == 200
                assert payload["data"]["status"] == "RUNNING"
                assert payload["data"]["action"] == "follow_player"
                assert fake.follow_player_calls == [{"username": "空凛", "distance": 3.0}]

                # 目标不存在 → 404 minecraft.player_not_found
                fake.follow_player_plan.append({"error": ("player.not_found", 404)})
                status, payload = await client.post(
                    "/api/v1/minecraft/follow_player", body={"username": "路人"}
                )
                assert status == 404 and error_code(payload) == "minecraft.player_not_found"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()
