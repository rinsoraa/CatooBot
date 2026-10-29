"""Message / Segment model tests."""

from __future__ import annotations

import pytest

from app.message.message import Message, parse_cq_string
from app.message.segment import (
    AtSegment,
    FileSegment,
    ImageSegment,
    ReplySegment,
    Segment,
    TextSegment,
    VideoSegment,
)


def make_message() -> Message:
    return Message(
        [
            TextSegment(type="text", data={"text": "你好 "}),
            AtSegment(type="at", data={"qq": 123456}),
            TextSegment(type="text", data={"text": " 今天怎么样？"}),
        ]
    )


class TestSegments:
    def test_text_segment(self) -> None:
        seg = TextSegment(type="text", data={"text": "hello"})
        assert seg.text == "hello"
        assert seg.to_onebot() == {"type": "text", "data": {"text": "hello"}}

    def test_at_segment(self) -> None:
        seg = AtSegment(type="at", data={"qq": "10086"})
        assert seg.user_id == 10086
        assert not seg.is_all

    def test_at_segment_all(self) -> None:
        seg = AtSegment(type="at", data={"qq": "all"})
        assert seg.user_id == "all"
        assert seg.is_all

    def test_image_segment(self) -> None:
        seg = ImageSegment(type="image", data={"file": "a.jpg", "url": "https://x/a.jpg"})
        assert seg.file == "a.jpg"
        assert seg.url == "https://x/a.jpg"

    def test_reply_segment(self) -> None:
        seg = ReplySegment(type="reply", data={"id": "777"})
        assert seg.message_id == 777

    def test_unknown_segment_preserved(self) -> None:
        seg = Segment.from_onebot({"type": "dice", "data": {"result": "6"}})
        assert isinstance(seg, Segment)
        assert not isinstance(seg, TextSegment)
        assert seg.to_onebot() == {"type": "dice", "data": {"result": "6"}}

    def test_from_onebot_returns_typed_subclass(self) -> None:
        seg = Segment.from_onebot({"type": "at", "data": {"qq": 1}})
        assert isinstance(seg, AtSegment)

    def test_video_segment_is_typed(self) -> None:
        seg = Segment.from_onebot(
            {"type": "video", "data": {"url": "https://x/a.mp4", "file": "a.mp4"}}
        )
        assert isinstance(seg, VideoSegment)
        assert seg.url == "https://x/a.mp4"
        assert seg.file == "a.mp4"

    def test_file_segment_is_typed(self) -> None:
        seg = Segment.from_onebot({"type": "file", "data": {"file": "doc.pdf"}})
        assert isinstance(seg, FileSegment)
        assert seg.file == "doc.pdf"
        assert seg.name == "doc.pdf"


class TestMessage:
    def test_text_property(self) -> None:
        assert make_message().text == "你好  今天怎么样？"

    def test_segments_property(self) -> None:
        message = make_message()
        assert [s.type for s in message.segments] == ["text", "at", "text"]

    def test_is_mentioned(self) -> None:
        message = make_message()
        assert message.is_mentioned(123456)
        assert not message.is_mentioned(999)

    def test_is_mentioned_all_does_not_count(self) -> None:
        message = Message([AtSegment(type="at", data={"qq": "all"})])
        assert not message.is_mentioned(10001)

    def test_from_onebot_array(self) -> None:
        raw = [
            {"type": "text", "data": {"text": "hi "}},
            {"type": "at", "data": {"qq": 5}},
        ]
        message = Message.from_onebot(raw)
        assert message.text == "hi "
        assert message.is_mentioned(5)

    def test_from_onebot_cq_string(self) -> None:
        message = Message.from_onebot("[CQ:at,qq=88] hello[CQ:image,file=x.png]")
        assert message.is_mentioned(88)
        assert message.text == " hello"
        assert len(message.get("image")) == 1

    def test_from_onebot_cq_escaping(self) -> None:
        message = Message.from_onebot("[CQ:text,data=a&#91;b&#93;,text=x]")
        assert message.text == "x"

    def test_strip_prefix_at(self) -> None:
        message = Message.from_onebot(
            [{"type": "at", "data": {"qq": 10001}}, {"type": "text", "data": {"text": " /ping"}}]
        )
        assert message.strip_prefix_at(10001)
        # router does .strip() on the text afterwards; leading space is fine
        assert message.text.strip() == "/ping"

    def test_strip_prefix_at_with_blank_text(self) -> None:
        message = Message.from_onebot(
            [
                {"type": "text", "data": {"text": "  "}},
                {"type": "at", "data": {"qq": 10001}},
                {"type": "text", "data": {"text": " hi"}},
            ]
        )
        assert message.strip_prefix_at(10001)
        assert message.text == " hi"

    def test_strip_prefix_at_only_leading(self) -> None:
        message = Message.from_onebot(
            [{"type": "text", "data": {"text": "hi "}}, {"type": "at", "data": {"qq": 10001}}]
        )
        assert not message.strip_prefix_at(10001)
        assert message.is_mentioned(10001)

    def test_to_onebot_roundtrip(self) -> None:
        message = make_message()
        rebuilt = Message.from_onebot(message.to_onebot())
        assert rebuilt.text == message.text
        assert [s.type for s in rebuilt.segments] == [s.type for s in message.segments]

    def test_to_cq_string_roundtrip(self) -> None:
        original = "[CQ:at,qq=88] hello"
        message = Message.from_onebot(original)
        assert parse_cq_string(message.to_cq_string())[0].type == "at"

    def test_add_operator(self) -> None:
        message = Message.text_message("a") + Message.text_message("b")
        assert message.text == "ab"
        assert (Message() + "c").text == "c"

    def test_get_and_first(self) -> None:
        message = Message.from_onebot("[CQ:image,file=a][CQ:image,file=b]")
        assert len(message.get("image")) == 2
        assert message.first("image") is not None
        assert message.first("reply") is None

    def test_from_none_is_empty(self) -> None:
        assert len(Message.from_onebot(None)) == 0

    def test_from_invalid_type_raises(self) -> None:
        with pytest.raises(TypeError):
            Message.from_onebot(123)  # type: ignore[arg-type]
