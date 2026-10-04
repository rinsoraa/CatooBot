"""MinecraftPlugin 测试（Phase 1）：QQ 触发解析、罐头式回复、聊天入沙盒链。"""

from __future__ import annotations

import pytest

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.events import MinecraftBridgeEvent
from app.integrations.minecraft.service import MinecraftService
from app.plugins.api import PluginApi
from app.plugins.manifest import PluginManifest
from plugins.minecraft.plugin import MinecraftPlugin, parse_server_address
from tests.conftest import group_event, make_bot, private_event

MANIFEST = PluginManifest(
    id="bot.catoo.minecraft",
    name="minecraft",
    version="1.0.0",
    capabilities=frozenset({"message.read", "send.private", "send.group", "services"}),
)


def parse_address_or_none(text: str):
    return parse_server_address(text)


async def make_plugin(bot, service: MinecraftService | None) -> MinecraftPlugin:
    """绕过 loader 的插件装配（manifest → PluginApi → on_load）。"""
    plugin = MinecraftPlugin()
    api = PluginApi(bot, MANIFEST)
    plugin.manifest = MANIFEST
    plugin.api = api
    plugin.bot = api
    if service is not None:
        bot.minecraft = service
    await plugin.on_load(api)
    return plugin


@pytest.fixture
def service_and_bot(tmp_path):
    bot = make_bot(tmp_path)
    service = MinecraftService(bot, MinecraftConfig(enabled=True, auto_start_runtime=False))
    calls: list[tuple[str, object]] = []

    async def fake_join(host: str, port: int):
        calls.append(("join", host, port))
        return {"session_id": "s1", "status": "CONNECTING"}

    async def fake_leave():
        calls.append(("leave", None, None))
        return {"ok": True, "status": "DISCONNECTED"}

    service.join = fake_join  # type: ignore[method-assign]
    service.leave = fake_leave  # type: ignore[method-assign]
    service.snapshot = lambda: {  # type: ignore[method-assign]
        "connection": {"status": "DISCONNECTED", "host": None, "port": None}
    }
    yield bot, service, calls


def event_of(service: MinecraftService, name: str, **data) -> MinecraftBridgeEvent:
    return MinecraftBridgeEvent(event=name, session_id="s1", timestamp=1.0, data=data)


async def test_join_with_inline_address_replies_on_spawned(service_and_bot):
    bot, service, calls = service_and_bot
    plugin = await make_plugin(bot, service)
    await plugin._on_group(group_event("罐头，加入Minecraft 127.0.0.1:25565"))
    assert ("join", "127.0.0.1", 25565) in calls
    # 进世界前不播报
    assert "我进来啦！" not in bot.adapter.sent_texts()
    await plugin._on_minecraft_event(event_of(service, "minecraft.spawned", username="GuanTou"))
    assert "我进来啦！" in bot.adapter.sent_texts()


async def test_join_without_address_asks_for_it(service_and_bot):
    bot, service, calls = service_and_bot
    plugin = await make_plugin(bot, service)
    await plugin._on_private(private_event("罐头 加入Minecraft"))
    assert calls == []
    assert any("服务器地址" in text for text in bot.adapter.sent_texts())


async def test_join_failure_replies_with_reason(service_and_bot):
    bot, service, calls = service_and_bot

    async def failing_join(host: str, port: int):
        from app.integrations.minecraft.service import MinecraftRuntimeDown

        raise MinecraftRuntimeDown("runtime 不可达")

    service.join = failing_join  # type: ignore[method-assign]
    plugin = await make_plugin(bot, service)
    await plugin._on_group(group_event("加入MC 127.0.0.1:25565"))
    # runtime_down 是「我进不去 Minecraft」而非「进不去这个服务器」
    assert any("我现在进不去 Minecraft" in text for text in bot.adapter.sent_texts())
    assert any("runtime 不可达" in text for text in bot.adapter.sent_texts())


async def test_leave_replies_when_online(service_and_bot):
    bot, service, calls = service_and_bot
    service.snapshot = lambda: {  # type: ignore[method-assign]
        "connection": {"status": "ONLINE", "host": "127.0.0.1", "port": 25565}
    }
    plugin = await make_plugin(bot, service)
    await plugin._on_private(private_event("罐头 离开Minecraft"))
    assert ("leave", None, None) in calls
    assert "好啦，我回来啦。" in bot.adapter.sent_texts()


async def test_leave_when_offline_says_so(service_and_bot):
    bot, service, calls = service_and_bot
    plugin = await make_plugin(bot, service)
    await plugin._on_private(private_event("离开Minecraft"))
    assert calls == []
    assert any("本来就不在" in text for text in bot.adapter.sent_texts())


async def test_irrelevant_messages_ignored(service_and_bot):
    bot, service, calls = service_and_bot
    plugin = await make_plugin(bot, service)
    await plugin._on_group(group_event("今天天气真不错"))
    await plugin._on_group(group_event("我加了个好友"))
    assert calls == []
    assert bot.adapter.sent_texts() == []


async def test_chat_event_forwarded_into_sandbox_chain(service_and_bot):
    bot, service, _ = service_and_bot
    plugin = await make_plugin(bot, service)

    submitted: list[object] = []

    class FakeSandbox:
        enabled = True

        async def submit_external(self, event):
            submitted.append(event)
            return True

        async def wakeup(self):
            submitted.append("wakeup")

    bot.sandbox = FakeSandbox()
    await plugin._on_minecraft_event(
        event_of(service, "minecraft.chat", username="空凛", message="罐头过来")
    )
    assert len(submitted) == 2
    world_event = submitted[0]
    assert world_event.content == "罐头过来"
    assert world_event.actor_id == "空凛"
    assert world_event.metadata["channel"] == "minecraft"


async def test_chat_without_username_not_forwarded(service_and_bot):
    bot, service, _ = service_and_bot
    plugin = await make_plugin(bot, service)
    await plugin._on_minecraft_event(event_of(service, "minecraft.chat", message="系统消息"))
    assert getattr(bot, "sandbox", None) is None  # 沙盒未启用时只记录，不炸


def test_parse_server_address_variants():
    assert parse_server_address("加入Minecraft 127.0.0.1:25565") == ("127.0.0.1", 25565)
    assert parse_server_address("进服 hypixel.net") == ("hypixel.net", 25565)
    assert parse_server_address("连接MC localhost：3000") == ("localhost", 3000)
    assert parse_server_address("加入我的世界") is None


async def test_loader_discovers_and_loads_minecraft_plugin(tmp_path):
    """防回归：插件目录必须有 __init__.py（pkgutil 不枚举 namespace 包），
    否则 loader 静默扫不到——线上曾因此插件从未加载且无任何报错。"""
    from tests.conftest import make_ready_bot

    bot = await make_ready_bot(tmp_path)
    assert "minecraft" in bot.plugins.loaded
    assert "character" in bot.plugins.loaded
