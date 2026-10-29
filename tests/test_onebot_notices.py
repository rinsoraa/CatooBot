"""OneBot notice semantics + the poke reaction (Task 19).

Notices used to arrive as one generic ``NoticeEvent``; pokes, recalls, mutes and
card changes now have typed models (so handlers read attributes instead of
digging into ``raw_event``), and a poke *at her* enters the normal turn pipeline
— restrained on purpose: no new behaviour rule, the existing decision still
chooses whether she says anything.
"""

from __future__ import annotations

from app.adapters.onebot_v11.api import BotApi
from app.adapters.onebot_v11.parser import parse_event
from app.message.event import (
    BanNotice,
    CardChangeNotice,
    NoticeEvent,
    PokeNotice,
    RecallNotice,
)


def notice(**fields: object) -> dict:  # type: ignore[type-arg]
    raw = {"post_type": "notice", "self_id": 10001, "time": 1700000000}
    raw.update(fields)
    return raw


class TestTypedNotices:
    def test_poke(self) -> None:
        event = parse_event(notice(notice_type="poke", user_id=7, target_id=10001, group_id=9))
        assert isinstance(event, PokeNotice)
        assert (event.user_id, event.target_id) == (7, 10001)

    def test_recalls_share_one_model(self) -> None:
        for notice_type in ("group_recall", "friend_recall"):
            event = parse_event(notice(notice_type=notice_type, user_id=7, message_id=42))
            assert isinstance(event, RecallNotice)
            assert event.message_id == 42

    def test_mute_carries_duration_and_sub_type(self) -> None:
        event = parse_event(
            notice(notice_type="group_ban", sub_type="ban", user_id=7, operator_id=1, duration=600)
        )
        assert isinstance(event, BanNotice)
        assert (event.sub_type, event.duration) == ("ban", 600)

    def test_card_change(self) -> None:
        event = parse_event(
            notice(
                notice_type="group_card",
                user_id=7,
                card_new="新名片",
                card_old="旧名片",
            )
        )
        assert isinstance(event, CardChangeNotice)
        assert (event.card_new, event.card_old) == ("新名片", "旧名片")

    def test_unknown_notice_stays_generic(self) -> None:
        event = parse_event(notice(notice_type="group_upload", user_id=7))
        assert isinstance(event, NoticeEvent) and not isinstance(event, PokeNotice)
        assert event.notice_type == "group_upload"

    def test_event_names_allow_fine_grained_subscriptions(self) -> None:
        from app.core.event_bus import event_names

        assert "notice.poke" in event_names(parse_event(notice(notice_type="poke", user_id=7)))


class FakeCaller:
    """Records OneBot calls so the typed helpers can be asserted."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []  # type: ignore[type-arg]

    async def call_api(self, action: str, params: dict | None = None, timeout: float | None = None):  # type: ignore[type-arg,no-untyped-def]
        self.calls.append((action, dict(params or {})))
        return {"message_id": 555, "message": [{"type": "text", "data": {"text": "hello"}}]}


class TestApiActions:
    async def test_get_msg(self) -> None:
        caller = FakeCaller()
        result = await BotApi(caller).get_msg(42)  # type: ignore[arg-type]
        assert caller.calls == [("get_msg", {"message_id": 42})]
        assert result["message_id"] == 555

    async def test_send_forward_msg_normalizes_nodes(self) -> None:
        caller = FakeCaller()
        message_id = await BotApi(caller).send_forward_msg(  # type: ignore[arg-type]
            [{"name": "罐头", "uin": "10001", "content": "第一段"}],
            group_id=9,
        )
        action, params = caller.calls[0]
        assert action == "send_forward_msg" and message_id == 555
        assert params["group_id"] == 9
        assert params["messages"] == [
            {
                "type": "node",
                "data": {
                    "name": "罐头",
                    "uin": "10001",
                    "content": [{"type": "text", "data": {"text": "第一段"}}],
                },
            }
        ]

    async def test_send_forward_msg_needs_a_target(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            await BotApi(FakeCaller()).send_forward_msg([])  # type: ignore[arg-type]


class TestPokeReaction:
    async def _bot(self, tmp_path):  # type: ignore[no-untyped-def]
        from tests.ai_mocks import MockAIProvider
        from tests.test_chat_integration import make_character_bot

        provider = MockAIProvider(behaviors={"A": ["干嘛戳我呀"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        return bot, provider

    async def test_poke_at_her_becomes_a_turn(self, tmp_path) -> None:
        bot, provider = await self._bot(tmp_path)
        try:
            await bot.event_bus.emit(
                parse_event(notice(notice_type="poke", user_id=777, target_id=10001))
            )
            await bot.conversation.wait_idle()
            assert provider.calls, "戳一戳应当进入回合运行时"
            assert "（戳了戳你）" in provider.calls[0]["last_user"]
            assert bot.adapter.sent_texts() == ["干嘛戳我呀"]  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_poke_at_someone_else_is_ignored(self, tmp_path) -> None:
        bot, provider = await self._bot(tmp_path)
        try:
            await bot.event_bus.emit(
                parse_event(notice(notice_type="poke", user_id=777, target_id=888))
            )
            await bot.conversation.wait_idle()
            assert provider.calls == []
            assert bot.adapter.sent_texts() == []  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_recall_and_mute_do_not_trigger_turns(self, tmp_path) -> None:
        bot, provider = await self._bot(tmp_path)
        try:
            await bot.event_bus.emit(
                parse_event(notice(notice_type="group_recall", user_id=777, message_id=1))
            )
            await bot.event_bus.emit(
                parse_event(
                    notice(notice_type="group_ban", sub_type="ban", user_id=777, duration=60)
                )
            )
            await bot.conversation.wait_idle()
            assert provider.calls == []
        finally:
            await bot.shutdown()
