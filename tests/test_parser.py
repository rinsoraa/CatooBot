"""OneBot JSON -> typed event parser tests."""

from __future__ import annotations

import pytest

from app.adapters.onebot_v11.models import ApiResult
from app.adapters.onebot_v11.parser import parse_event, parse_payload
from app.core.exceptions import ParseError
from app.message.event import (
    Event,
    GroupMessageEvent,
    MetaEvent,
    NoticeEvent,
    PrivateMessageEvent,
    RequestEvent,
)

GROUP_MESSAGE_JSON = {
    "post_type": "message",
    "self_id": 10001,
    "time": 1700000000,
    "message_type": "group",
    "sub_type": "normal",
    "message_id": 1234,
    "user_id": 222,
    "group_id": 555,
    "message": [
        {"type": "at", "data": {"qq": "10001"}},
        {"type": "text", "data": {"text": " /ping"}},
    ],
    "raw_message": "[CQ:at,qq=10001] /ping",
    "sender": {"user_id": 222, "nickname": "Bob", "card": "Bobby", "role": "member"},
}


class TestMessageEvents:
    def test_group_message(self) -> None:
        event = parse_event(GROUP_MESSAGE_JSON)
        assert isinstance(event, GroupMessageEvent)
        assert event.user_id == 222
        assert event.group_id == 555
        assert event.message_id == 1234
        assert event.self_id == 10001
        assert event.timestamp == 1700000000
        assert event.message.is_mentioned(10001)
        assert event.sender.card == "Bobby"
        assert event.sender.role == "member"

    def test_private_message(self) -> None:
        event = parse_event(
            {
                "post_type": "message",
                "self_id": 10001,
                "message_type": "private",
                "message_id": 1,
                "user_id": 7,
                "message": [{"type": "text", "data": {"text": "/about"}}],
                "sender": {"user_id": 7, "nickname": "Eve"},
            }
        )
        assert isinstance(event, PrivateMessageEvent)
        assert event.is_private
        assert event.is_to_me
        assert event.message.text == "/about"

    def test_cq_string_message_format(self) -> None:
        event = parse_event(
            {
                "post_type": "message",
                "self_id": 10001,
                "message_type": "group",
                "group_id": 555,
                "message_id": 2,
                "user_id": 9,
                "message": "[CQ:at,qq=10001] hi",
                "sender": {"user_id": 9, "nickname": "X"},
            }
        )
        assert event.message.text == " hi"
        assert event.message.is_mentioned(10001)

    def test_raw_event_preserved(self) -> None:
        event = parse_event(GROUP_MESSAGE_JSON)
        assert event.raw_event["message_id"] == 1234

    def test_is_to_me(self) -> None:
        event = parse_event(GROUP_MESSAGE_JSON)
        assert event.is_to_me
        plain = {**GROUP_MESSAGE_JSON, "message": [{"type": "text", "data": {"text": "hi"}}]}
        assert not parse_event(plain).is_to_me


class TestOtherEvents:
    def test_notice_event(self) -> None:
        event = parse_event(
            {
                "post_type": "notice",
                "self_id": 10001,
                "notice_type": "group_recall",
                "group_id": 555,
                "user_id": 222,
                "operator_id": 333,
                "message_id": 1234,
            }
        )
        assert isinstance(event, NoticeEvent)
        assert event.notice_type == "group_recall"
        assert event.operator_id == 333

    def test_request_event(self) -> None:
        event = parse_event(
            {
                "post_type": "request",
                "self_id": 10001,
                "request_type": "friend",
                "user_id": 222,
                "comment": "add me",
                "flag": "flag-1",
            }
        )
        assert isinstance(event, RequestEvent)
        assert event.comment == "add me"

    def test_meta_heartbeat(self) -> None:
        event = parse_event(
            {
                "post_type": "meta_event",
                "self_id": 10001,
                "meta_event_type": "heartbeat",
                "status": {"online": True},
                "interval": 5000,
            }
        )
        assert isinstance(event, MetaEvent)
        assert event.interval == 5000

    def test_meta_lifecycle(self) -> None:
        event = parse_event(
            {
                "post_type": "meta_event",
                "self_id": 10001,
                "meta_event_type": "lifecycle",
                "sub_type": "connect",
            }
        )
        assert isinstance(event, MetaEvent)
        assert event.sub_type == "connect"

    def test_unknown_post_type_becomes_generic_event(self) -> None:
        event = parse_event({"post_type": "something_new", "self_id": 10001})
        assert type(event) is Event


class TestApiResults:
    def test_api_response_detected(self) -> None:
        result = parse_payload(
            {"status": "ok", "retcode": 0, "data": {"message_id": 5}, "echo": "1"}
        )
        assert isinstance(result, ApiResult)
        assert result.ok

    def test_failed_api_response(self) -> None:
        result = parse_payload({"status": "failed", "retcode": 1200, "data": None, "echo": "2"})
        assert isinstance(result, ApiResult)
        assert not result.ok


class TestErrors:
    def test_invalid_payload_raises(self) -> None:
        with pytest.raises(ParseError):
            parse_payload({"totally": "unknown"})

    def test_missing_required_fields_raises(self) -> None:
        with pytest.raises(ParseError):
            parse_event({"post_type": "message"})  # no self_id/message_type keys of value

    def test_parse_payload_routes_events(self) -> None:
        assert isinstance(parse_payload(GROUP_MESSAGE_JSON), GroupMessageEvent)
