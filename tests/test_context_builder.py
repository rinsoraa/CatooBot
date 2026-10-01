"""Context builder + response processor tests (character prompt assembly)."""

from __future__ import annotations

from app.character.context import CharacterContextBuilder
from app.character.persona import Persona
from app.character.relationship import Relationship
from app.character.response import CharacterResponseProcessor
from app.character.state import CharacterState
from app.memory.model import Memory


def make_builder() -> CharacterContextBuilder:
    return CharacterContextBuilder()


PERSONA = Persona.from_config(
    {
        "identity": {"name": "小星", "occupation": "学生", "background": "在海边城市长大。"},
        "personality": {"traits": ["好奇心强"], "likes": ["猫", "画画"]},
        "behavior_rules": {"rules": ["不聊政治"]},
        "system_prompt": "自定义补充段。",
    }
)


def build_messages(**overrides):
    builder = make_builder()
    kwargs = {
        "persona": PERSONA,
        "state": CharacterState(mood="happy", activity="画画", current_focus="毕设"),
        "relationship": Relationship(user_id="1", stage="close", interaction_count=42),
        "memories": [Memory(scope_key="user:1", content="用户喜欢猫")],
        "history": [],
        "user_text": "你好",
    }
    kwargs.update(overrides)
    return builder.build(**kwargs)


class TestContextBuilder:
    def test_system_message_first_and_user_last(self) -> None:
        from app.ai.models import ChatMessage

        messages = build_messages(history=[ChatMessage.assistant("早")])
        assert messages[0].role == "system"
        assert messages[-1].role == "user"
        assert messages[-1].content == "你好"

    def test_identity_and_background_present(self) -> None:
        system = build_messages()[0].content
        assert "小星" in system
        assert "学生" in system
        assert "在海边城市长大。" in system

    def test_state_and_relationship_present(self) -> None:
        system = build_messages()[0].content
        assert "happy" in system
        assert "画画" in system
        assert "close" in system

    def test_memory_block_present(self) -> None:
        system = build_messages()[0].content
        assert "用户喜欢猫" in system

    def test_no_memory_block_when_empty(self) -> None:
        system = build_messages(memories=[])[0].content
        assert "你记得的关于对方的事" not in system

    def test_knowledge_boundary_always_present(self) -> None:
        system = build_messages(persona=Persona())[0].content
        assert "绝不向用户透露" in system
        assert "不要输出功能菜单" in system

    def test_style_limits_forbid_blank_lines_and_cap_length(self) -> None:
        """她只写一段话（分不分条由系统决定），且默认一句话、≤30 字。"""
        system = build_messages()[0].content
        assert "不要在回复里用换行或空行分段" in system
        assert "30 字" in system and "45 字" in system
        assert "不要凑长度" in system

    def test_group_hint_present(self) -> None:
        system = build_messages(is_group=True)[0].content
        assert "群聊场景" in system

    def test_history_in_middle(self) -> None:
        from app.ai.models import ChatMessage

        messages = build_messages(history=[ChatMessage.user("早"), ChatMessage.assistant("早呀")])
        assert [m.content for m in messages] == ["…", "早", "早呀", "你好"][1:] or [
            m.content for m in messages[1:-1]
        ] == ["早", "早呀"]

    def test_stale_history_gets_time_marker(self) -> None:
        """A 4 a.m. goodnight must not look like "just said" at 8 p.m. (v1.1 fix)."""
        import time as time_mod

        from app.ai.models import ChatMessage
        from app.behavior.models import TimeContext

        now = time_mod.time()
        stale = now - 16 * 3600  # ~16h ago
        tc = TimeContext(
            timezone="Asia/Shanghai",
            local_time="20:32",
            date_text="2026-09-30",
            weekday="星期三",
            period="evening",
            is_weekend=False,
            is_sleeping=False,
            in_dnd=False,
        )
        messages = build_messages(
            history=[
                ChatMessage(role="user", content="晚安", created_at=stale),
                ChatMessage(role="assistant", content="晚安好梦", created_at=stale),
                ChatMessage(role="user", content="在吗", created_at=now),
            ],
            time_context=tc,
        )
        history_texts = [m.content for m in messages[1:-1]]
        assert any(t.startswith("[") and "晚安" in t for t in history_texts)
        assert "在吗" in history_texts  # recent turn stays untagged
        # the system prompt explains the markers and pins the current state
        system = messages[0].content
        assert "过去的聊天记录" in system
        assert "此刻" in system

    def test_recent_history_untagged(self) -> None:
        import time as time_mod

        from app.ai.models import ChatMessage

        messages = build_messages(
            history=[ChatMessage(role="user", content="刚聊的话", created_at=time_mod.time() - 60)]
        )
        assert messages[1].content == "刚聊的话"
        assert "过去的聊天记录" not in messages[0].content


class TestResponseProcessor:
    def test_clean_response_untouched(self) -> None:
        processor = CharacterResponseProcessor()
        text = "今天天气不错，适合出门走走～"
        assert processor.sanitize(text) == text

    def test_imitated_time_tag_stripped(self) -> None:
        """The model must not copy the history "[今天凌晨4点]" convention (v1.1 fix)."""
        processor = CharacterResponseProcessor()
        assert (
            processor.sanitize("[当前] 最近在肝一个沙盒游戏，老上头了")
            == "最近在肝一个沙盒游戏，老上头了"
        )
        assert processor.sanitize("[今天晚上8点] 在打游戏") == "在打游戏"
        assert processor.sanitize("[9月28日下午] 那天我在看书") == "那天我在看书"

    def test_imitated_time_tag_stripped_per_line(self) -> None:
        processor = CharacterResponseProcessor()
        cleaned = processor.sanitize("哈哈这个我知道！\n[此刻] 我正连麦呢")
        assert "[此刻]" not in cleaned
        assert "哈哈这个我知道！" in cleaned
        assert "我正连麦呢" in cleaned

    def test_emoticon_brackets_not_touched(self) -> None:
        processor = CharacterResponseProcessor()
        text = "[狗头] 这波不亏"
        assert processor.sanitize(text) == text

    def test_internal_terms_detected(self) -> None:
        processor = CharacterResponseProcessor()
        report = processor.inspect("我运行在 NapCat 上，底层是 OneBot 协议")
        assert report.leaked
        assert "napcat" in report.terms

    def test_command_like_detected(self) -> None:
        processor = CharacterResponseProcessor()
        assert processor.inspect("/help 可以查看命令").command_like
        assert not processor.inspect("斜杠/ help 都行吧").command_like

    def test_natural_ai_mention_allowed(self) -> None:
        processor = CharacterResponseProcessor()
        assert not processor.inspect("现在 AI 画图挺厉害的").leaked

    def test_sanitize_drops_leaking_lines(self) -> None:
        processor = CharacterResponseProcessor()
        text = "哈哈这个我知道！\n其实我的 system prompt 是保密的\n明天见呀"
        cleaned = processor.sanitize(text)
        assert "system prompt" not in cleaned
        assert "哈哈这个我知道！" in cleaned
        assert "明天见呀" in cleaned

    def test_sanitize_never_returns_empty(self) -> None:
        processor = CharacterResponseProcessor()
        text = "我的 NapCat 配置文件丢了"
        assert processor.sanitize(text) == text  # whole text leaks -> kept as-is

    def test_persona_provider_terms(self) -> None:
        processor = CharacterResponseProcessor(persona_provider=lambda: ["内部代号XYZ"])
        assert processor.inspect("这是 内部代号xyz 的秘密").leaked
