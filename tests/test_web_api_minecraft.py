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
    """§三十/§四十八：GET /minecraft 带 LLM Tool Debug（每个工具的风险/开关/是否允许）。"""
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
            "minecraft_container_inspect",
            "minecraft_container_transfer",
            "minecraft_craft",
            "minecraft_dig",
            "minecraft_dropped_items",
            "minecraft_equip",
            "minecraft_inventory",
            "minecraft_inventory_move",
            "minecraft_pickup_item",
            "minecraft_place",
            "minecraft_recipe_lookup",
        }
        assert all(row["allowed"] is False for row in agent["tools"])
        assert all(row["reason"] == "minecraft.disabled" for row in agent["tools"])

        # 启用 Tool Runtime + 装配 bridge：SAFE 允许、LOW 因「不在世界」被拒（reason 如实）
        await bot.tools.start()  # 注册全部 minecraft_* 工具（api_server 不跑 Bot.start）
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


# ------------------------------------------------ Phase 4A：确认门端点


async def test_confirmation_endpoint_only_shrinks_authority(tmp_path):
    """§九/§三十二：Debug 端点只能造测试条 / 取消 / 置过期，**不能**代替用户确认。"""
    from app.integrations.minecraft.agent import MinecraftAgentBridge

    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        service = MinecraftService(bot, MinecraftConfig(enabled=True, auto_start_runtime=False))
        bot.minecraft = service
        try:
            bridge = MinecraftAgentBridge(service)
            service.agent = bridge

            # §一/§三十八：create_test 绝不能为正式动作（minecraft_dig）造确认
            status, payload = await client.post(
                "/api/v1/minecraft/agent/confirm",
                body={"action": "create_test", "tool": "minecraft_dig", "risk": "MEDIUM"},
            )
            assert status == 400 and error_code(payload) == "minecraft.confirmation_invalid"

            # 测试专用名字可以造（生产注册表里没有这个动作，永远无法消费）
            status, payload = await client.post(
                "/api/v1/minecraft/agent/confirm",
                body={"action": "create_test", "tool": "minecraft_test_medium"},
            )
            assert status == 200
            created = payload["data"]["confirmation"]
            assert created["status"] == "PENDING" and created["tool"] == "minecraft_test_medium"

            # 只读投影里能看到它（WebUI 的 Pending Confirmation 面板）
            status, payload = await client.get("/api/v1/minecraft")
            store = payload["data"]["agent"]["confirmations"]
            assert store["ttl_seconds"] == 60.0
            assert created["confirmation_id"] in {
                row["confirmation_id"] for row in store["pending"]
            }
            assert payload["data"]["agent"]["trusted_players"] == []

            # 没有「确认」动作：非法 action 直接 400（不能代替用户确认）
            for action in ("confirm", "consume", "approve"):
                status, payload = await client.post(
                    "/api/v1/minecraft/agent/confirm",
                    body={"action": action, "confirmation_id": created["confirmation_id"]},
                )
                assert status == 400 and error_code(payload) == "minecraft.confirmation_invalid"

            # CANCEL / EXPIRE 只缩小授权（各用一条自己的确认）
            for action, expected in (("cancel", "CANCELLED"), ("expire", "EXPIRED")):
                _status, payload = await client.post(
                    "/api/v1/minecraft/agent/confirm",
                    body={"action": "create_test", "tool": f"minecraft_test_{action}"},
                )
                target = payload["data"]["confirmation"]["confirmation_id"]
                status, payload = await client.post(
                    "/api/v1/minecraft/agent/confirm",
                    body={"action": action, "confirmation_id": target},
                )
                assert status == 200 and payload["data"]["status"] == expected
                # 同一条再操作 → 404（已经不是 PENDING：一次性）
                status, payload = await client.post(
                    "/api/v1/minecraft/agent/confirm",
                    body={"action": action, "confirmation_id": target},
                )
                assert status == 404 and error_code(payload) == "minecraft.confirmation_invalid"
        finally:
            await service._cleanup()


async def test_confirmation_endpoint_requires_minecraft(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post(
            "/api/v1/minecraft/agent/confirm", body={"action": "create_test"}
        )
        assert status == 503 and error_code(payload) == "minecraft.disabled"


# ------------------------------------------------ Phase 4B：dig 端点


async def test_dig_endpoint_requires_confirmation(tmp_path):
    """§三十七：WebUI 的 DIG 也必须过 MEDIUM 确认门——第一次只会得到 409。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": True}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge

                service.agent = MinecraftAgentBridge(service)

                # 参数不合法 → 422（且不挂确认）
                status, payload = await client.post(
                    "/api/v1/minecraft/dig", body={"x": 1, "y": 64, "z": 2}
                )
                assert status == 422 and error_code(payload) == "minecraft.action_invalid"
                assert service.agent.confirmations.pending() == []

                # 先让 Agent 上下文进入在线态（dig 需要在线；否则先被在线门拦下）
                fake.online = True
                await service.status()
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )
                # 第一次请求 → 需要确认（不执行）
                status, payload = await client.post(
                    "/api/v1/minecraft/dig",
                    body={"x": 1, "y": 64, "z": 2, "expected_block": "minecraft:stone"},
                )
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_required"
                detail = payload["error"]["detail"] if "error" in payload else payload.get("detail")
                assert detail["confirmation"]["tool"] == "minecraft_dig"
                assert fake.dig_calls == [], "确认前绝不挖"

                # 管理台也不能替用户确认：没有 confirm/consume
                status, payload = await client.post(
                    "/api/v1/minecraft/agent/confirm",
                    body={
                        "action": "confirm",
                        "confirmation_id": detail["confirmation"]["confirmation_id"],
                    },
                )
                assert status == 400 and error_code(payload) == "minecraft.confirmation_invalid"
                assert fake.dig_calls == []
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_dig_endpoint_disabled_without_minecraft(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post(
            "/api/v1/minecraft/dig", body={"x": 1, "y": 64, "z": 2, "expected_block": "stone"}
        )
        assert status == 503 and error_code(payload) == "minecraft.disabled"


# ------------------------------------------------ Phase 4C：inventory / place


async def test_inventory_endpoint_is_read_only_and_always_200(tmp_path):
    """读端点恒 200：未启用 → online:false；启用后给只读切片（无任何动作）。"""
    from tests.test_minecraft_service import FakeRuntime

    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.get("/api/v1/minecraft/inventory")
        assert status == 200
        assert payload["data"] == {
            "ok": True,
            "online": False,
            "selected_hotbar_slot": None,
            "held_item": None,
            "items": [],
        }

        fake = FakeRuntime()
        await fake.start()
        try:
            service = MinecraftService(
                bot, MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=fake.port)
            )
            bot.minecraft = service
            try:
                status, payload = await client.get("/api/v1/minecraft/inventory")
                assert status == 200
                data = payload["data"]
                assert data["online"] is True and data["held_item"]["name"] == "dirt"
                assert data["items"] == [{"name": "dirt", "count": 12}]
                assert set(data) == {"ok", "online", "selected_hotbar_slot", "held_item", "items"}
                assert fake.place_calls == [], "只读端点绝不触发任何动作"
            finally:
                await service._cleanup()
        finally:
            await fake.stop()


async def test_place_endpoint_requires_confirmation(tmp_path):
    """§二十六：WebUI 的 PLACE 也必须过 MEDIUM 确认门 —— 第一次只会得到 409。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": True}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)

                body = {"x": 2, "y": 64, "z": 2, "face": "up", "expected_item": "dirt"}
                # 参数不合法 → 422（且不挂确认）
                bad_bodies = [
                    {"x": 2.5, "y": 64, "z": 2, "face": "up", "expected_item": "dirt"},
                    {"x": 2, "y": 64, "z": 2, "face": "north_east", "expected_item": "dirt"},
                    {"x": 2, "y": 64, "z": 2, "face": "up", "expected_item": ""},
                    {"x": 2, "y": 64, "z": 2, "face": "up"},  # 缺 expected_item
                    {"y": 64, "z": 2, "face": "up", "expected_item": "dirt"},  # 缺 x
                ]
                for bad in bad_bodies:
                    status, payload = await client.post("/api/v1/minecraft/place", body=bad)
                    assert status == 422 and error_code(payload) == "minecraft.action_invalid", bad
                assert service.agent.confirmations.pending() == []

                # 让 Agent 上下文在线（place 需要在线）
                fake.online = True
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                status, payload = await client.post("/api/v1/minecraft/place", body=body)
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_required"
                detail = payload.get("detail") or payload["error"]["detail"]
                assert detail["confirmation"]["tool"] == "minecraft_place"
                assert fake.place_calls == [], "确认前绝不放置"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_place_endpoint_disabled_without_minecraft(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post(
            "/api/v1/minecraft/place",
            body={"x": 2, "y": 64, "z": 2, "face": "up", "expected_item": "dirt"},
        )
        assert status == 503 and error_code(payload) == "minecraft.disabled"


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


# ------------------------------------------------ Phase 4D：背包控制端点


async def test_inventory_slots_endpoint_is_debug_read_only(tmp_path):
    """§二十：`GET /minecraft/inventory/slots` 是**调试**用原始槽位视图（恒 200、只读）。

    它不进 LLM 工具链（`minecraft_inventory` 的聚合切片才是模型看到的）。
    """
    from tests.test_minecraft_service import FakeRuntime

    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        # 未启用 → 200 + 空槽位表（读端点不因功能关闭而报错）
        status, payload = await client.get("/api/v1/minecraft/inventory/slots")
        assert status == 200
        assert payload["data"] == {
            "ok": True,
            "online": False,
            "hotbar_start": None,
            "inventory_start": None,
            "slots": [],
        }

        fake = FakeRuntime()
        await fake.start()
        try:
            service = MinecraftService(
                bot, MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=fake.port)
            )
            bot.minecraft = service
            try:
                status, payload = await client.get("/api/v1/minecraft/inventory/slots")
                assert status == 200
                assert payload["data"]["slots"] == fake.inventory_slots_payload["slots"]
                assert fake.inventory_slots_calls == 1
                # 只读端点绝不触发任何动作
                assert fake.equip_calls == [] and fake.inventory_move_calls == []
            finally:
                await service._cleanup()
        finally:
            await fake.stop()


async def test_equip_endpoint_validates_and_requires_confirmation(tmp_path):
    """§二十六：WebUI 的 EQUIP 也必须过 MEDIUM 确认门 —— 第一次只会得到 409。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": True}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)

                # 参数不合法 → 422（且不挂确认）
                for bad in ({}, {"item": ""}, {"item": 7}, {"item": "x" * 200}):
                    status, payload = await client.post("/api/v1/minecraft/equip", body=bad)
                    assert status == 422 and error_code(payload) == "minecraft.action_invalid", bad
                assert service.agent.confirmations.pending() == []

                # 在线后才谈得上"拿东西"
                fake.online = True
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                status, payload = await client.post(
                    "/api/v1/minecraft/equip", body={"item": "dirt"}
                )
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_required"
                detail = payload.get("detail") or payload["error"]["detail"]
                assert detail["confirmation"]["tool"] == "minecraft_equip"
                assert detail["confirmation"]["summary"] == "把 dirt 拿到手里"
                assert fake.equip_calls == [], "确认前绝不装备"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_inventory_move_endpoint_validates_slots_before_confirmation(tmp_path):
    """§十八/§二十二：槽位在进确认门**之前**就校验（垃圾参数不挂待确认）。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": True}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge

                service.agent = MinecraftAgentBridge(service)
                good = {"source_slot": 37, "destination_slot": 9, "item": "dirt", "count": 1}
                bad_cases = [
                    {"destination_slot": 9, "item": "dirt", "count": 1},  # 缺 source
                    {**good, "source_slot": 8},  # 主背包从 9 开始
                    {**good, "source_slot": 45},  # 快捷栏到 44 结束
                    {**good, "destination_slot": 37},  # 同一个槽位
                    {**good, "source_slot": 2.5},
                    {**good, "count": 0},
                    {**good, "count": 1.5},
                    {**good, "item": ""},
                    {**good, "item": "x" * 200},
                ]
                for bad in bad_cases:
                    status, payload = await client.post(
                        "/api/v1/minecraft/inventory_move", body=bad
                    )
                    assert status == 422 and error_code(payload) == "minecraft.action_invalid", bad
                assert service.agent.confirmations.pending() == [], "垃圾参数绝不挂待确认"
                assert fake.inventory_move_calls == []

                # 确认门之前还有「必须在线」这一关（离线时根本谈不到确认）
                fake.online = True
                await service.status()
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                # 合法参数 → 409（确认门）；这时才允许出现一条待确认
                status, payload = await client.post("/api/v1/minecraft/inventory_move", body=good)
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_required"
                detail = payload.get("detail") or payload["error"]["detail"]
                assert detail["confirmation"]["summary"] == "把 37 格的 dirt ×1 移到 9 格"
                assert fake.inventory_move_calls == [], "确认前绝不搬"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_medium_endpoints_cannot_self_authorise(tmp_path):
    """§二十六/§一：WebUI 与开发者入口拿不到 MEDIUM 的执行权。

    即使确认真的存在（这里由测试直接造出来），消费确认也要求**用户回合**：
    开发者入口的 turn_origin 是 SYSTEM → 409 ``confirmation_not_user_turn``，
    runtime 一个请求都收不到。确认只能由用户在对话里说「确认」后、由那个回合的
    工具调用消费。
    """
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": True}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import (
                    MinecraftAgentBridge,
                )
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                # 第一次：如实请求确认（挂 PENDING，不执行）
                status, payload = await client.post(
                    "/api/v1/minecraft/equip", body={"item": "dirt"}
                )
                assert status == 409 and error_code(payload) == "minecraft.confirmation_required"
                pending = service.agent.confirmations.pending()
                assert len(pending) == 1 and pending[0].tool == "minecraft_equip"

                # 之后：确认在，但开发者入口消费不了（来源门先于一切）
                for item in ("dirt", "sand"):
                    status, payload = await client.post(
                        "/api/v1/minecraft/equip", body={"item": item}
                    )
                    assert status == 409, item
                    assert error_code(payload) == "minecraft.confirmation_not_user_turn", item
                assert fake.equip_calls == [], "非用户回合绝不装备"
                assert len(service.agent.confirmations.pending()) == 1, "来源门不消费确认"

                # 搬运同理
                body = {"source_slot": 37, "destination_slot": 9, "item": "dirt", "count": 1}
                status, payload = await client.post("/api/v1/minecraft/inventory_move", body=body)
                assert status == 409 and error_code(payload) == "minecraft.confirmation_required"
                status, payload = await client.post("/api/v1/minecraft/inventory_move", body=body)
                assert (
                    status == 409 and error_code(payload) == "minecraft.confirmation_not_user_turn"
                )
                assert fake.inventory_move_calls == [], "非用户回合绝不搬东西"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


def test_phase4d_error_codes_agree_between_service_and_api():
    """两个事实源必须一致：Service 异常自带的 HTTP 语义 vs API 的错误码映射表。"""
    from app.integrations.minecraft.service import (
        MinecraftDestinationOccupied,
        MinecraftEquipUnconfirmed,
        MinecraftItemChanged,
        MinecraftItemCountInsufficient,
        MinecraftItemNotFound,
        MinecraftMoveUnconfirmed,
        MinecraftSlotInvalid,
    )
    from app.web.api.minecraft import _TOOL_STATUS

    pairs = [
        (MinecraftItemNotFound(), 404),
        (MinecraftItemChanged(), 409),
        (MinecraftItemCountInsufficient(), 409),
        (MinecraftDestinationOccupied(), 409),
        (MinecraftSlotInvalid(), 422),
        (MinecraftEquipUnconfirmed(), 500),
        (MinecraftMoveUnconfirmed(), 500),
    ]
    for exc, expected in pairs:
        assert exc.status == expected, exc.code
        assert _TOOL_STATUS[exc.code] == expected, exc.code


async def test_equip_and_move_endpoints_disabled_without_minecraft(tmp_path):
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post("/api/v1/minecraft/equip", body={"item": "dirt"})
        assert status == 503 and error_code(payload) == "minecraft.disabled"
        status, payload = await client.post(
            "/api/v1/minecraft/inventory_move",
            body={"source_slot": 37, "destination_slot": 9, "item": "dirt", "count": 1},
        )
        assert status == 503 and error_code(payload) == "minecraft.disabled"


# ------------------------------------------------ Phase 4E：容器端点


async def test_container_inspect_endpoint_reads_real_content(tmp_path):
    """§三十七：WebUI 的 INSPECT 走 `POST /minecraft/container_inspect`（SAFE 只读、同步返回）。

    它不需要确认门，但仍然受「在线 + 独占」约束。
    """
    from tests.test_minecraft_service import FakeRuntime

    # 未装配连接层 → 503
    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post(
            "/api/v1/minecraft/container_inspect", body={"x": 100, "y": 64, "z": 100}
        )
        assert status == 503 and error_code(payload) == "minecraft.disabled"

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot, MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=fake.port)
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                # 坐标必须整数 → 422（垃圾参数不碰 runtime）
                for bad in ({"x": 100.5, "y": 64, "z": 100}, {"y": 64, "z": 100}, {"x": 1}):
                    status, payload = await client.post(
                        "/api/v1/minecraft/container_inspect", body=bad
                    )
                    assert status == 422 and error_code(payload) == "minecraft.action_invalid", bad
                assert fake.container_inspect_calls == []

                status, payload = await client.post(
                    "/api/v1/minecraft/container_inspect", body={"x": 100, "y": 64, "z": 100}
                )
                assert status == 200, payload
                data = payload["data"]
                assert data["action"] == "container_inspect" and data["status"] == "SUCCEEDED"
                snapshot = data["result"]
                assert snapshot["container"]["type"] == "minecraft:chest"
                assert snapshot["container"]["size"] == 27
                assert snapshot["slots"] == [
                    {"slot": 0, "name": "dirt", "count": 12},
                    {"slot": 7, "name": "sand", "count": 32},
                ]
                assert fake.container_inspect_calls == [{"x": 100, "y": 64, "z": 100}]
                # 只读：绝不出现待确认
                assert service.agent.confirmations.pending() == []

                # runtime 侧的类型拒绝如实透传
                fake.container_inspect_plan.append(
                    {
                        "error": ("container.unsupported", 422),
                        "detail": {"block": "minecraft:furnace"},
                    }
                )
                status, payload = await client.post(
                    "/api/v1/minecraft/container_inspect", body={"x": 100, "y": 64, "z": 100}
                )
                assert status == 422 and error_code(payload) == "minecraft.container_unsupported"
                fake.container_inspect_plan.append({"error": ("container.too_far", 422)})
                status, payload = await client.post(
                    "/api/v1/minecraft/container_inspect", body={"x": 100, "y": 64, "z": 100}
                )
                assert status == 422 and error_code(payload) == "minecraft.container_too_far"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_container_transfer_endpoint_validates_then_requires_confirmation(tmp_path):
    """§三十七：WebUI 的 Transfer 先校验参数，再过确认门；它**拿不到**执行权。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": True}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                good = {
                    "x": 100,
                    "y": 64,
                    "z": 100,
                    "direction": "withdraw",
                    "container_slot": 0,
                    "inventory_slot": 9,
                    "item": "dirt",
                    "count": 1,
                }
                bad_cases = [
                    {**good, "direction": "take"},
                    {**good, "direction": None},
                    {**good, "container_slot": -1},
                    {**good, "container_slot": 1.5},
                    {**good, "inventory_slot": 8},
                    {**good, "inventory_slot": 45},
                    {**good, "count": 0},
                    {**good, "item": ""},
                    {**good, "x": 100.5},
                    {k: v for k, v in good.items() if k != "direction"},
                ]
                for bad in bad_cases:
                    status, payload = await client.post(
                        "/api/v1/minecraft/container_transfer", body=bad
                    )
                    assert status == 422 and error_code(payload) == "minecraft.action_invalid", bad
                assert service.agent.confirmations.pending() == [], "垃圾参数绝不挂待确认"
                assert fake.container_transfer_calls == []

                # 合法参数 → 409（确认门），摘要写清箱子位置 / 第几格 / 物品 / 数量
                status, payload = await client.post(
                    "/api/v1/minecraft/container_transfer", body=good
                )
                assert status == 409 and error_code(payload) == "minecraft.confirmation_required"
                detail = payload.get("detail") or payload["error"]["detail"]
                assert (
                    detail["confirmation"]["summary"]
                    == "从 (100, 64, 100) 的容器第 0 格取 dirt ×1 到背包第 9 格"
                )
                assert fake.container_transfer_calls == [], "确认前绝不搬东西"

                # §三十五：开发者入口（SYSTEM 回合）消费不了确认 —— WebUI 不能自授权
                status, payload = await client.post(
                    "/api/v1/minecraft/container_transfer", body=good
                )
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_not_user_turn"
                assert fake.container_transfer_calls == []
                assert len(service.agent.confirmations.pending()) == 1, "来源门不消费确认"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_container_endpoints_translate_policy_denials(tmp_path):
    """未启用 MEDIUM 时如实 403；不在世界里时如实 409（都绝不碰 runtime）。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": False}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge

                service.agent = MinecraftAgentBridge(service)
                body = {
                    "x": 100,
                    "y": 64,
                    "z": 100,
                    "direction": "withdraw",
                    "container_slot": 0,
                    "inventory_slot": 9,
                    "item": "dirt",
                    "count": 1,
                }
                # 不在世界里：inspect（SAFE）也如实 409（离线没有读箱子这回事）
                status, payload = await client.post(
                    "/api/v1/minecraft/container_inspect", body={"x": 100, "y": 64, "z": 100}
                )
                assert status == 409 and error_code(payload) == "minecraft.offline"
                assert fake.container_inspect_calls == []

                # 上线后：allow_medium=false → transfer 403（在确认门之前就被风险开关拦下）
                fake.online = True
                await service.status()
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )
                status, payload = await client.post(
                    "/api/v1/minecraft/container_transfer", body=body
                )
                assert status == 403 and error_code(payload) == "minecraft.action_not_allowed"
                assert fake.container_transfer_calls == []
                assert service.agent.confirmations.pending() == [], "风险开关先于确认门"

                # 而 inspect 是 SAFE：在线即可读（只读，不需要确认）
                status, payload = await client.post(
                    "/api/v1/minecraft/container_inspect", body={"x": 100, "y": 64, "z": 100}
                )
                assert status == 200, payload
                assert payload["data"]["result"]["container"]["size"] == 27
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


def test_phase4e_error_codes_agree_between_service_and_api():
    """两个事实源必须一致：Service 异常自带的 HTTP 语义 vs API 的错误码映射表。"""
    from app.integrations.minecraft.service import (
        MinecraftContainerClosed,
        MinecraftContainerCloseFailed,
        MinecraftContainerOpenFailed,
        MinecraftContainerTooFar,
        MinecraftContainerTransferUnconfirmed,
        MinecraftContainerUnsupported,
    )
    from app.web.api.minecraft import _TOOL_STATUS

    pairs = [
        (MinecraftContainerUnsupported(), 422),
        (MinecraftContainerTooFar(), 422),
        (MinecraftContainerOpenFailed(), 500),
        (MinecraftContainerClosed(), 409),
        (MinecraftContainerCloseFailed(), 500),
        (MinecraftContainerTransferUnconfirmed(), 500),
    ]
    for exc, expected in pairs:
        assert exc.status == expected, exc.code
        assert _TOOL_STATUS[exc.code] == expected, exc.code


# ------------------------------------------------ Phase 4F：crafting 端点


async def test_recipe_lookup_endpoint_projects_semantics(tmp_path):
    """§四十：WebUI 的 Recipe Lookup 走 `POST /minecraft/recipe_lookup`（SAFE 同步返回）。"""
    from tests.test_minecraft_service import FakeRuntime

    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post(
            "/api/v1/minecraft/recipe_lookup", body={"item": "stick"}
        )
        assert status == 503 and error_code(payload) == "minecraft.disabled"

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot, MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=fake.port)
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                for bad in ({}, {"item": ""}, {"item": 7}):
                    status, payload = await client.post("/api/v1/minecraft/recipe_lookup", body=bad)
                    assert status == 422 and error_code(payload) == "minecraft.action_invalid", bad
                assert fake.recipe_lookup_calls == []

                status, payload = await client.post(
                    "/api/v1/minecraft/recipe_lookup", body={"item": "stick"}
                )
                assert status == 200, payload
                data = payload["data"]
                assert data["action"] == "recipe_lookup" and data["status"] == "SUCCEEDED"
                result = data["result"]
                assert result["status"] == "available"
                entry = result["recipes"][0]
                assert entry["recipe_id"] == "stick*4=oak_planks*2"
                assert entry["ingredients"] == [{"name": "oak_planks", "count": 2}]
                assert "delta" not in str(result) and "inShape" not in str(result)
                assert fake.recipe_lookup_calls == [{"item": "stick"}]
                # 只读：不产生待确认
                assert service.agent.confirmations.pending() == []

                # runtime 侧的错误如实透传
                fake.recipe_lookup_plan.append({"error": ("recipe.not_found", 404)})
                status, payload = await client.post(
                    "/api/v1/minecraft/recipe_lookup", body={"item": "unobtainium"}
                )
                assert status == 404 and error_code(payload) == "minecraft.recipe_not_found"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_craft_endpoint_requires_confirmation_and_cannot_self_authorise(tmp_path):
    """§四十：WebUI 的 Craft 先校验参数，再过确认门；它**拿不到**执行权。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": True}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                good = {"recipe_id": "stick*4=oak_planks*2"}
                bad_cases = [
                    {},
                    {"recipe_id": ""},
                    {"recipe_id": "not-a-signature"},
                    {"recipe_id": 7},
                ]
                for bad in bad_cases:
                    status, payload = await client.post("/api/v1/minecraft/craft", body=bad)
                    assert status == 422 and error_code(payload) == "minecraft.action_invalid", bad
                assert service.agent.confirmations.pending() == [], "垃圾参数绝不挂待确认"
                assert fake.craft_calls == []

                # 多余的键会被忽略（调试入口只读 recipe_id）—— 确认指纹仍然只绑 recipe_id
                status, _payload = await client.post(
                    "/api/v1/minecraft/craft", body={**good, "count": 2}
                )
                assert status == 409
                stored = service.agent.confirmations.pending()
                assert len(stored) == 1
                assert stored[0].arguments == {**good, "crafting_table": None}
                # 清掉这条，继续后面的流程
                service.agent.confirmations.cancel(stored[0].confirmation_id)

                status, payload = await client.post("/api/v1/minecraft/craft", body=good)
                assert status == 409 and error_code(payload) == "minecraft.confirmation_required"
                detail = payload.get("detail") or payload["error"]["detail"]
                assert detail["confirmation"]["summary"] == "用 2 个 oak_planks 制作 4 个 stick"
                assert fake.craft_calls == [], "确认前绝不合成"

                # 开发者入口（SYSTEM 回合）消费不了确认
                status, payload = await client.post("/api/v1/minecraft/craft", body=good)
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_not_user_turn"
                assert fake.craft_calls == []
                assert len(service.agent.confirmations.pending()) == 1, "来源门不消费确认"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_craft_endpoint_respects_allow_medium(tmp_path):
    """allow_medium=false → 403（风险开关先于确认门）。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": False}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )
                status, payload = await client.post(
                    "/api/v1/minecraft/craft", body={"recipe_id": "stick*4=oak_planks*2"}
                )
                assert status == 403 and error_code(payload) == "minecraft.action_not_allowed"
                assert fake.craft_calls == []
                assert service.agent.confirmations.pending() == []
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


def test_phase4f_error_codes_agree_between_service_and_api():
    """两个事实源必须一致：Service 异常自带的 HTTP 语义 vs API 的错误码映射表。"""
    from app.integrations.minecraft.service import (
        MinecraftCraftFailed,
        MinecraftCraftUnconfirmed,
        MinecraftMaterialInsufficient,
        MinecraftRecipeChanged,
        MinecraftRecipeNotFound,
        MinecraftRecipeUnavailable,
    )
    from app.web.api.minecraft import _TOOL_STATUS

    pairs = [
        (MinecraftRecipeNotFound(), 404),
        (MinecraftRecipeUnavailable(), 409),
        (MinecraftRecipeChanged(), 409),
        (MinecraftMaterialInsufficient(), 409),
        (MinecraftCraftFailed(), 500),
        (MinecraftCraftUnconfirmed(), 500),
    ]
    for exc, expected in pairs:
        assert exc.status == expected, exc.code
        assert _TOOL_STATUS[exc.code] == expected, exc.code


# ------------------------------------------------ Phase 4G：工作台（3×3）端点


async def test_recipe_lookup_endpoint_accepts_an_explicit_crafting_table(tmp_path):
    """§三十八：WebUI 的 3×3 查询要给出**明确的工作台坐标**（nearest/auto 一律拒绝）。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot, MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=fake.port)
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                for bad in ("nearest", "auto", "any", {"x": 100, "y": 64}):
                    status, payload = await client.post(
                        "/api/v1/minecraft/recipe_lookup",
                        body={"item": "chest", "crafting_table": bad},
                    )
                    assert status == 422 and error_code(payload) == "minecraft.action_invalid", bad
                assert fake.recipe_lookup_calls == []

                table = {"x": 100, "y": 64, "z": 100}
                fake.recipe_lookup_result = {
                    "ok": True,
                    "item": "chest",
                    "crafting_table": table,
                    "status": "available",
                    "total": 1,
                    "recipes": [
                        {
                            "recipe_id": "!chest*1=oak_planks*8",
                            "result": {"name": "chest", "count_per_craft": 1},
                            "requires_table": True,
                            "available": True,
                            "ingredients": [{"name": "oak_planks", "count": 8}],
                        }
                    ],
                }
                status, payload = await client.post(
                    "/api/v1/minecraft/recipe_lookup",
                    body={"item": "chest", "crafting_table": table},
                )
                assert status == 200, payload
                result = payload["data"]["result"]
                assert result["crafting_table"] == table
                assert result["recipes"][0]["requires_table"] is True
                assert fake.recipe_lookup_calls == [{"item": "chest", "crafting_table": table}]

                # 工作台侧的三种拒绝如实透传
                fake.recipe_lookup_plan.append({"error": ("table.missing", 404)})
                status, payload = await client.post(
                    "/api/v1/minecraft/recipe_lookup",
                    body={"item": "chest", "crafting_table": table},
                )
                assert status == 404 and error_code(payload) == "minecraft.crafting_table_missing"
                fake.recipe_lookup_plan.append(
                    {"error": ("table.invalid", 422), "detail": {"block": "chest"}}
                )
                status, payload = await client.post(
                    "/api/v1/minecraft/recipe_lookup",
                    body={"item": "chest", "crafting_table": table},
                )
                assert status == 422 and error_code(payload) == "minecraft.crafting_table_invalid"
                fake.recipe_lookup_plan.append({"error": ("table.too_far", 422)})
                status, payload = await client.post(
                    "/api/v1/minecraft/recipe_lookup",
                    body={"item": "chest", "crafting_table": table},
                )
                assert status == 422 and error_code(payload) == "minecraft.crafting_table_too_far"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_craft_endpoint_with_table_requires_confirmation(tmp_path):
    """§三十八/§三十九：3×3 的确认摘要要写出工作台坐标；开发者入口仍然拿不到执行权。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": True}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                table = {"x": 100, "y": 64, "z": 100}
                good = {"recipe_id": "!chest*1=oak_planks*8", "crafting_table": table}
                status, payload = await client.post("/api/v1/minecraft/craft", body=good)
                assert status == 409 and error_code(payload) == "minecraft.confirmation_required"
                detail = payload.get("detail") or payload["error"]["detail"]
                assert detail["confirmation"]["summary"] == (
                    "用 8 个 oak_planks 在 (100, 64, 100) 的 Crafting Table 制作 1 个 chest"
                )
                stored = service.agent.confirmations.pending()
                assert len(stored) == 1 and stored[0].arguments == good, "指纹包含工作台坐标"
                assert fake.craft_calls == []

                # WebUI（SYSTEM 回合）不能消费确认
                status, payload = await client.post("/api/v1/minecraft/craft", body=good)
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_not_user_turn"
                assert fake.craft_calls == []

                # 换个工作台坐标 → 必须是另一条授权（mismatch）
                moved = {
                    "recipe_id": "!chest*1=oak_planks*8",
                    "crafting_table": {"x": 101, "y": 64, "z": 100},
                }
                status, payload = await client.post("/api/v1/minecraft/craft", body=moved)
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_not_user_turn", (
                    "来源门先于参数指纹（不泄露「参数是否匹配」）"
                )
                assert fake.craft_calls == []
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


def test_phase4g_error_codes_agree_between_service_and_api():
    """两个事实源必须一致：Service 异常自带的 HTTP 语义 vs API 的错误码映射表。"""
    from app.integrations.minecraft.service import (
        MinecraftCraftingTableInvalid,
        MinecraftCraftingTableMissing,
        MinecraftCraftingTableTooFar,
    )
    from app.web.api.minecraft import _TOOL_STATUS

    pairs = [
        (MinecraftCraftingTableMissing(), 404),
        (MinecraftCraftingTableInvalid(), 422),
        (MinecraftCraftingTableTooFar(), 422),
    ]
    for exc, expected in pairs:
        assert exc.status == expected, exc.code
        assert _TOOL_STATUS[exc.code] == expected, exc.code


# ------------------------------------------------ Phase 4H：掉落物 / 拾取端点


async def test_dropped_items_endpoint_is_read_only(tmp_path):
    """§五十：WebUI 的 Dropped Items 走 `POST /minecraft/dropped_items`（SAFE 只读）。"""
    from tests.test_minecraft_service import FakeRuntime

    async with api_server(tmp_path) as (client, bot, server):
        await client.login()
        status, payload = await client.post("/api/v1/minecraft/dropped_items", body={})
        assert status == 503 and error_code(payload) == "minecraft.disabled"

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot, MinecraftConfig(enabled=True, auto_start_runtime=False, runtime_port=fake.port)
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                status, payload = await client.post("/api/v1/minecraft/dropped_items", body={})
                assert status == 200, payload
                data = payload["data"]
                assert data["action"] == "dropped_items" and data["status"] == "SUCCEEDED"
                result = data["result"]
                assert result["total"] == 1 and result["truncated"] is False
                row = result["items"][0]
                assert row["entity_id"] == 123
                assert row["item"] == {"name": "dirt", "count": 3}
                assert set(row) == {"entity_id", "item", "position", "distance"}
                assert fake.dropped_items_calls == 1
                # 只读：不产生待确认
                assert service.agent.confirmations.pending() == []

                fake.dropped_items_plan.append({"error": ("action.failed", 500)})
                status, payload = await client.post("/api/v1/minecraft/dropped_items", body={})
                assert status == 500 and error_code(payload) == "minecraft.action_failed"
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


async def test_pickup_endpoint_requires_confirmation_and_cannot_self_authorise(tmp_path):
    """§五十：WebUI 的 PICKUP 只能发起确认 —— 拿不到真实执行权。"""
    from tests.test_minecraft_service import FakeRuntime

    fake = FakeRuntime()
    await fake.start()
    try:
        fake.online = True
        async with api_server(tmp_path) as (client, bot, server):
            await client.login()
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    runtime_port=fake.port,
                    agent={"tools": {"allow_medium": True}},
                ),
            )
            bot.minecraft = service
            try:
                from app.integrations.minecraft.agent import MinecraftAgentBridge
                from app.integrations.minecraft.events import parse_bridge_event

                service.agent = MinecraftAgentBridge(service)
                await service.status()
                service.agent.apply_event(
                    parse_bridge_event(
                        {
                            "event": "minecraft.spawned",
                            "session_id": "s1",
                            "timestamp": 1.0,
                            "username": "Catodayo",
                        }
                    )
                )

                for bad in (
                    {},
                    {"entity_id": 123},
                    {"expected_item": "dirt"},
                    {"entity_id": "123", "expected_item": "dirt"},
                    {"entity_id": 1.5, "expected_item": "dirt"},
                    {"entity_id": 123, "expected_item": ""},
                ):
                    status, payload = await client.post("/api/v1/minecraft/pickup_item", body=bad)
                    assert status == 422 and error_code(payload) == "minecraft.action_invalid", bad
                assert service.agent.confirmations.pending() == [], "垃圾参数绝不挂待确认"
                assert fake.pickup_calls == []

                good = {"entity_id": 123, "expected_item": "dirt"}
                status, payload = await client.post("/api/v1/minecraft/pickup_item", body=good)
                assert status == 409 and error_code(payload) == "minecraft.confirmation_required"
                detail = payload.get("detail") or payload["error"]["detail"]
                assert detail["confirmation"]["summary"] == "拾取附近的 dirt（实体 #123）"
                stored = service.agent.confirmations.pending()
                assert len(stored) == 1 and stored[0].arguments == good, "指纹绑 entity_id + 物品"
                assert fake.pickup_calls == [], "确认前绝不移动/拾取"

                # WebUI（SYSTEM 回合）不能消费确认
                status, payload = await client.post("/api/v1/minecraft/pickup_item", body=good)
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_not_user_turn"
                assert fake.pickup_calls == []
                assert len(service.agent.confirmations.pending()) == 1, "来源门不消费确认"

                # 换一个 entity_id：仍然是"来源门"先拦（不泄露参数是否匹配）
                other = {"entity_id": 130, "expected_item": "dirt"}
                status, payload = await client.post("/api/v1/minecraft/pickup_item", body=other)
                assert status == 409
                assert error_code(payload) == "minecraft.confirmation_not_user_turn"
                assert fake.pickup_calls == []
            finally:
                await service._cleanup()
    finally:
        await fake.stop()


def test_phase4h_error_codes_agree_between_service_and_api():
    """两个事实源必须一致：Service 异常自带的 HTTP 语义 vs API 的错误码映射表。"""
    from app.integrations.minecraft.service import (
        MinecraftItemEntityChanged,
        MinecraftItemEntityInvalid,
        MinecraftItemEntityNotFound,
        MinecraftPickupFailed,
        MinecraftPickupTargetLost,
        MinecraftPickupTargetReplaced,
        MinecraftPickupTargetTooFar,
        MinecraftPickupUnconfirmed,
    )
    from app.web.api.minecraft import _TOOL_STATUS

    pairs = [
        (MinecraftItemEntityNotFound(), 404),
        (MinecraftItemEntityInvalid(), 422),
        (MinecraftItemEntityChanged(), 409),
        (MinecraftPickupTargetReplaced(), 409),
        (MinecraftPickupTargetLost(), 409),
        (MinecraftPickupTargetTooFar(), 422),
        (MinecraftPickupFailed(), 500),
        (MinecraftPickupUnconfirmed(), 500),
    ]
    for exc, expected in pairs:
        assert exc.status == expected, exc.code
        assert _TOOL_STATUS[exc.code] == expected, exc.code
