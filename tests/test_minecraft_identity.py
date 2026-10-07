"""Phase 5C §三-§九/§三十五：Minecraft 身份桥（canonical identity + QQ 绑定流程）。

要点：

* ``player_uuid`` 是 canonical identity，``username`` 只是显示名（改名不换人）；
* ``server_id`` 是确定性的（同一台服务器重启不变，换服务器必变）；
* QQ 侧绑定**必须**用户显式二次确认，且只能在玩家在线（拿得到真实 uuid）时成立；
* 已经有别人的 VERIFIED 绑定 → **冲突拒绝**，绝不覆盖；
* 解绑 = REVOKED，历史保留（不删行）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.onebot_v11.parser import parse_event
from app.integrations.minecraft.identity import (
    CODE_CONFLICT,
    CODE_INVALID,
    CODE_NOT_FOUND,
    CODE_UNAVAILABLE,
    SOURCE_CONFIGURED,
    canonical_uuid,
    server_identity,
)
from app.integrations.minecraft.identity_commands import MinecraftIdentityCommands
from tests.minecraft_memory_fakes import (
    UUID_KONGLING,
    UUID_OTHER,
    FakeMinecraftService,
    build_bridge,
)

SELF_ID = 10001
GROUP = 987654
USER_A = "2731431246"
USER_B = "1234567"


class FakeDelivery:
    """记录发出去的 QQ 消息（唯一替身：发送通道）。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, int, str]] = []

    async def send_private_msg(self, user_id: int, message: str) -> int:
        self.sent.append(("private", int(user_id), str(message)))
        return len(self.sent)

    async def send_group_msg(self, group_id: int, message: str) -> int:
        self.sent.append(("group", int(group_id), str(message)))
        return len(self.sent)

    def last(self) -> str:
        return self.sent[-1][2] if self.sent else ""


class FakeBot:
    def __init__(self) -> None:
        self.api = FakeDelivery()
        self.social = None
        self.response_delivery = None


def private_event(text: str, user_id: str = USER_A) -> Any:
    return parse_event(
        {
            "post_type": "message",
            "message_type": "private",
            "self_id": SELF_ID,
            "user_id": int(user_id),
            "message_id": 1,
            "raw_message": text,
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"nickname": "昵称不可作身份"},
        }
    )


def group_event(text: str, *, mention: bool = True, user_id: str = USER_A) -> Any:
    segments: list[dict[str, Any]] = [{"type": "text", "data": {"text": text}}]
    if mention:
        segments.insert(0, {"type": "at", "data": {"qq": str(SELF_ID)}})
    return parse_event(
        {
            "post_type": "message",
            "message_type": "group",
            "self_id": SELF_ID,
            "user_id": int(user_id),
            "group_id": GROUP,
            "message_id": 2,
            "raw_message": text,
            "message": segments,
            "sender": {"nickname": "昵称不可作身份"},
        }
    )


# ------------------------------------------------------------------ canonical identity


class TestCanonicalIdentity:
    def test_uuid_normalisation(self) -> None:
        assert (
            canonical_uuid("11111111-2222-3333-4444-555555555f2c")
            == "11111111222233334444555555555f2c"
        )
        assert canonical_uuid("11111111-2222-3333-4444-55555555 5f2c") == ""
        assert canonical_uuid("not-a-uuid") == ""
        assert canonical_uuid(None) == ""
        assert canonical_uuid(12345) == ""

    def test_server_id_is_deterministic_and_scoped(self) -> None:
        first = server_identity("127.0.0.1", 25565)
        again = server_identity("127.0.0.1", 25565)
        assert first.server_id == again.server_id  # 服务器重启 → 同一个 id
        assert first.server_id.startswith("mc-")
        assert server_identity("127.0.0.1", 25566).server_id != first.server_id
        assert server_identity("example.com", 25565).server_id != first.server_id
        assert server_identity("127.0.0.1", 25565, world_key="w2").server_id != first.server_id
        assert server_identity("127.0.0.1", 25565, edition="bedrock").server_id != first.server_id

    def test_no_host_means_no_identity(self) -> None:
        empty = server_identity(None, None)
        assert empty.server_id == ""
        assert server_identity("", 25565).server_id == ""

    def test_username_is_not_identity(self) -> None:
        with_host = server_identity("127.0.0.1", 25565)
        # 同一个玩家改名 → uuid 不变；不同玩家同名 → uuid 不同（身份只看 uuid）
        a = canonical_uuid(UUID_KONGLING)
        b = canonical_uuid(UUID_OTHER)
        assert a != b
        assert with_host.server_id  # 服务器身份与玩家名无关


# ------------------------------------------------------------------ store


class TestIdentityStore:
    async def test_verify_and_display_only_suffix(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        service: FakeMinecraftService = bridge.service
        service.add_player("空凛", UUID_KONGLING)

        outcome = await bridge.bind(platform="qq", user_id=USER_A, username="空凛")
        assert outcome.ok and outcome.link is not None
        assert outcome.link.status == "VERIFIED"
        link = outcome.link
        assert link.canonical_uuid == canonical_uuid(UUID_KONGLING)
        assert link.identity().short_uuid() == "9f2c"
        # §五：给用户看的投影绝不包含完整 UUID
        assert "uuid" not in link.user_facing()
        assert link.user_facing()["uuid_suffix"] == "9f2c"
        await database.close()

    async def test_rename_keeps_the_same_person(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        service: FakeMinecraftService = bridge.service
        service.add_player("OldName", UUID_KONGLING)
        await bridge.bind(platform="qq", user_id=USER_A, username="OldName")

        # 改名之后重新验证：uuid 一样 → 只刷新显示名，不新增绑定
        service._players = [{"name": "NewName", "uuid": UUID_KONGLING, "distance": 3.0}]
        again = await bridge.bind(platform="qq", user_id=USER_A, username="NewName")
        assert again.ok
        links = await bridge.identities.all_links()
        assert len(links) == 1
        assert links[0].username == "NewName"
        await database.close()

    async def test_conflict_rejected_without_side_effect(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        service: FakeMinecraftService = bridge.service
        service.add_player("空凛", UUID_KONGLING)
        first = await bridge.bind(platform="qq", user_id=USER_A, username="空凛")
        assert first.ok

        second = await bridge.bind(platform="qq", user_id=USER_B, username="空凛")
        assert not second.ok
        assert second.code == CODE_CONFLICT
        assert "别人" in second.message

        links = await bridge.identities.all_links()
        verified = [link for link in links if link.status == "VERIFIED"]
        assert len(verified) == 1 and verified[0].user_id == USER_A
        # 冲突被如实记下来（历史保留，不删不覆盖）
        assert any(link.status == "CONFLICT" for link in links)
        await database.close()

    async def test_revoke_keeps_history(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        service: FakeMinecraftService = bridge.service
        service.add_player("空凛", UUID_KONGLING)
        await bridge.bind(platform="qq", user_id=USER_A, username="空凛")

        gone = await bridge.unbind(platform="qq", user_id=USER_A)
        assert gone.ok
        assert await bridge.link_for(platform="qq", user_id=USER_A) is None  # 不再认这个人
        links = await bridge.identities.all_links()
        assert [link.status for link in links] == ["REVOKED"]  # 行还在

        assert (await bridge.unbind(platform="qq", user_id=USER_A)).code == CODE_NOT_FOUND
        await database.close()

    async def test_rebind_after_revoke(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        service: FakeMinecraftService = bridge.service
        service.add_player("空凛", UUID_KONGLING)
        await bridge.bind(platform="qq", user_id=USER_A, username="空凛")
        await bridge.unbind(platform="qq", user_id=USER_A)

        again = await bridge.bind(platform="qq", user_id=USER_A, username="空凛")
        assert again.ok
        assert len([link for link in await bridge.identities.all_links() if link.active]) == 1
        await database.close()

    async def test_offline_player_cannot_be_bound(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        assert await bridge.bind(platform="qq", user_id=USER_A, username="空凛") is None
        assert await bridge.identities.all_links() == []
        await database.close()

    async def test_platform_and_user_validated(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        store = bridge.identities
        bad_platform = await store.verify(
            platform="telegram",
            user_id=USER_A,
            server_id="mc-x",
            player_uuid=UUID_KONGLING,
        )
        assert bad_platform.code == CODE_INVALID
        no_user = await store.verify(
            platform="qq", user_id="", server_id="mc-x", player_uuid=UUID_KONGLING
        )
        assert no_user.code == CODE_INVALID
        bad_uuid = await store.verify(
            platform="qq", user_id=USER_A, server_id="mc-x", player_uuid="nope"
        )
        assert bad_uuid.code == CODE_INVALID
        await database.close()

    async def test_unavailable_database_degrades(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        await database.close()
        outcome = await bridge.identities.verify(
            platform="qq", user_id=USER_A, server_id="mc-x", player_uuid=UUID_KONGLING
        )
        assert outcome.code == CODE_UNAVAILABLE
        assert bridge.identities.degraded_reason  # 如实降级，绝不假装成功

    async def test_configured_source_is_recorded(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        service: FakeMinecraftService = bridge.service
        service.add_player("空凛", UUID_KONGLING)
        outcome = await bridge.bind_configured(platform="qq", user_id=USER_A, username="空凛")
        assert outcome.ok and outcome.link is not None
        assert outcome.link.source == SOURCE_CONFIGURED
        await database.close()


# ------------------------------------------------------------------ QQ 命令流程


class TestQQBindingFlow:
    async def test_two_step_explicit_verification(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bridge.service.add_player("空凛", UUID_KONGLING)
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, bridge)

        assert await commands.on_message(private_event("把我和 Minecraft 里的 空凛 绑定")) is True
        ask = bot.api.last()
        assert "127.0.0.1:25565" in ask  # 服务器
        assert "空凛" in ask  # 玩家名
        assert "9f2c" in ask  # UUID 后四位
        assert "确认绑定" in ask
        # 还没有确认 → 一条绑定都不许产生
        assert await bridge.identities.all_links() == []

        assert await commands.on_message(private_event("确认绑定")) is True
        assert "记住了" in bot.api.last()
        link = await bridge.link_for(platform="qq", user_id=USER_A)
        assert link is not None and link.username == "空凛"
        await database.close()

    async def test_confirm_without_pending_is_not_claimed(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, bridge)
        assert await commands.on_message(private_event("确认绑定")) is False
        assert bot.api.sent == []
        await database.close()

    async def test_player_left_between_ask_and_confirm(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bridge.service.add_player("空凛", UUID_KONGLING)
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, bridge)
        await commands.on_message(private_event("把我和 Minecraft 里的 空凛 绑定"))
        # 确认期间人走了 → 什么都没改（绝不"绑到当时在线的人身上"）
        bridge.service._players = []
        assert await commands.on_message(private_event("确认绑定")) is True
        assert "不在线" in bot.api.last() or "什么都没改" in bot.api.last()
        assert await bridge.identities.all_links() == []
        await database.close()

    async def test_uuid_change_between_ask_and_confirm_is_refused(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bridge.service.add_player("空凛", UUID_KONGLING)
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, bridge)
        await commands.on_message(private_event("把我和 Minecraft 里的 空凛 绑定"))
        # 同名但换了 uuid（不同账号 / 伪造）→ 拒绝
        bridge.service._players = [{"name": "空凛", "uuid": UUID_OTHER, "distance": 3.0}]
        assert await commands.on_message(private_event("确认绑定")) is True
        assert "什么都没改" in bot.api.last() or "不是刚才那个人" in bot.api.last()
        assert await bridge.identities.all_links() == []
        await database.close()

    async def test_unknown_player_is_reported(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, bridge)
        await commands.on_message(private_event("把我和 Minecraft 里的 不存在 绑定"))
        assert "没看到" in bot.api.last()
        assert await bridge.identities.all_links() == []
        await database.close()

    async def test_conflict_told_to_the_user(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bridge.service.add_player("空凛", UUID_KONGLING)
        await bridge.bind(platform="qq", user_id=USER_A, username="空凛")
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, bridge)
        await commands.on_message(private_event("把我和 Minecraft 里的 空凛 绑定", user_id=USER_B))
        await commands.on_message(private_event("确认绑定", user_id=USER_B))
        assert "别人" in bot.api.last()
        assert len([link for link in await bridge.identities.all_links() if link.active]) == 1
        await database.close()

    async def test_unbind_command(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bridge.service.add_player("空凛", UUID_KONGLING)
        await bridge.bind(platform="qq", user_id=USER_A, username="空凛")
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, bridge)
        assert await commands.on_message(private_event("解除绑定")) is True
        assert await bridge.link_for(platform="qq", user_id=USER_A) is None
        # 再解一次：目标状态已经达成，如实说明而不是报错
        assert await commands.on_message(private_event("解除绑定")) is True
        assert "还没" in bot.api.last() or "没有记录" in bot.api.last()
        await database.close()

    async def test_group_requires_being_addressed(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bridge.service.add_player("空凛", UUID_KONGLING)
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, bridge)
        # 群里没 @她 → 不认领（普通群聊照旧）
        assert (
            await commands.on_message(group_event("把我和 Minecraft 里的 空凛 绑定", mention=False))
            is False
        )
        assert await commands.on_message(group_event("把我和 Minecraft 里的 空凛 绑定")) is True
        await database.close()

    async def test_ordinary_chat_is_never_claimed(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, bridge)
        for text in ("今天天气不错", "我想你了", "把木头挖掉", "确认"):
            assert await commands.on_message(private_event(text)) is False
        assert bot.api.sent == []
        await database.close()

    async def test_without_identity_store_commands_are_inert(self, tmp_path) -> None:
        bot = FakeBot()
        commands = MinecraftIdentityCommands(bot, None)
        assert await commands.on_message(private_event("把我和 Minecraft 里的 空凛 绑定")) is False
        assert bot.api.sent == []

    async def test_binding_grants_no_permission(self, tmp_path) -> None:
        """§十/§五十二：绑定只建立"谁是谁"，绝不写任何 trusted/权限字段。"""
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        bridge.service.add_player("空凛", UUID_KONGLING)
        outcome = await bridge.bind(platform="qq", user_id=USER_A, username="空凛")
        assert outcome.ok
        facts = await bridge.store.all_facts(server_id=bridge.server_id())
        relationship = [f for f in facts if f.kind.value == "RELATIONSHIP"]
        assert relationship
        for fact in relationship:
            assert fact.extra.get("grants_permission") is False
        await database.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("把我和 Minecraft 里的 A 绑定", "A"),
        ("绑定 Steve_01", "Steve_01"),
        ("把我和 Minecraft 里的 空凛 绑定", "空凛"),  # 离线模式/代理服允许中文名
        ("请把我和 mc 里的 空凛 绑定", "空凛"),
        ("bind Steve", "Steve"),
    ],
)
def test_bind_pattern_accepts_common_phrasings(text: str, expected: str) -> None:
    from app.integrations.minecraft.identity_commands import _BIND, _name_of

    match = _BIND.match(text)
    assert match is not None
    assert _name_of(match) == expected


@pytest.mark.parametrize("text", ["今天天气不错", "我和你说过的", "绑定", "确认"])
def test_bind_pattern_rejects_ordinary_chat(text: str) -> None:
    from app.integrations.minecraft.identity_commands import _BIND

    assert _BIND.match(text) is None


async def test_server_word_is_not_a_player_name(tmp_path) -> None:
    """「把我和 Minecraft 绑定」没说玩家名 → 追问，绝不拿 "Minecraft" 当玩家名去绑。"""
    bridge, database, _manager, _service = await build_bridge(tmp_path)
    bot = FakeBot()
    commands = MinecraftIdentityCommands(bot, bridge)
    assert await commands.on_message(private_event("把我和 Minecraft 绑定")) is True
    assert "哪个玩家名" in bot.api.last()
    assert await bridge.identities.all_links() == []
    await database.close()
