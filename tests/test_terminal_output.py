"""Terminal presentation tests: colours, CJK width, boxes, narration.

Spec coverage: the operator console shows the character's inner life (state,
thinking, flow, what she said, what her world is doing) and the log file stays
plain text with ANSI stripped. Startup artwork carries the developer signature.
"""

from __future__ import annotations

import logging

import pytest

from app.utils import console
from app.utils.narrator import Narrator, configure, narrate


@pytest.fixture(autouse=True)
def _restore_console() -> None:
    console.set_color_enabled(None)
    configure(True)
    yield
    console.set_color_enabled(None)
    configure(True)


class TestWidth:
    def test_cjk_counts_as_two_columns(self) -> None:
        assert console.visible_width("正在打游戏") == 10
        assert console.visible_width("abc") == 3
        assert console.visible_width("打a") == 3

    def test_ansi_escapes_are_ignored(self) -> None:
        console.set_color_enabled(True)
        painted = console.paint("打游戏", "red")
        assert console.visible_width(painted) == 6
        assert console.strip(painted) == "打游戏"

    def test_truncate_is_cjk_aware(self) -> None:
        assert console.truncate("正在打游戏", 5) == "正在…"
        assert console.truncate("短", 10) == "短"

    def test_pad_aligns_columns(self) -> None:
        assert console.visible_width(console.pad("打游戏", 10)) == 10
        assert console.pad("ab", 5, "right") == "   ab"


class TestColorSupport:
    def test_forced_off_by_env(self, monkeypatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        console.set_color_enabled(None)
        assert console.color_enabled() is False
        assert console.paint("x", "red") == "x"

    def test_forced_on_by_env(self, monkeypatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        console.set_color_enabled(None)
        assert console.color_enabled() is True
        assert "\033[" in console.paint("x", "red")

    def test_plain_when_not_a_tty(self, monkeypatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        console.set_color_enabled(None)

        class NotATty:
            def isatty(self) -> bool:
                return False

        assert console.color_enabled(NotATty()) is False  # type: ignore[arg-type]


class TestBoxes:
    def test_box_lines_share_one_width(self) -> None:
        lines = console.box("标题", ["短", "很长的中文内容在这里"])
        widths = {console.visible_width(line) for line in lines}
        assert len(widths) == 1

    def test_banner_has_signature_and_aligned_edges(self) -> None:
        lines = console.banner(
            "C a t o o B o t", "Persistent World", version="v0.8", author="Rinsora"
        )
        assert any("Rinsora" in console.strip(line) for line in lines)
        assert len({console.visible_width(line) for line in lines}) == 1
        assert "v0.8" in console.strip(lines[2])


class TestNarrator:
    def _capture(self, caplog) -> list[str]:
        return [record.getMessage() for record in caplog.records]

    def test_channels_render_icon_and_label(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            narrate().world("开始打游戏（房间）", detail="period=evening")
        text = console.strip(self._capture(caplog)[0])
        assert "世界" in text and "开始打游戏（房间）" in text and "period=evening" in text

    def test_reply_channel_labels_multi_part_messages(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            narrate().reply("第一条", index=1, total=2)
            narrate().reply("第二条", index=2, total=2)
        first, second = self._capture(caplog)
        assert "[1/2]" in console.strip(first) and "[2/2]" in console.strip(second)

    def test_mind_line_shows_state(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            narrate().mind(
                mood="happy", energy=0.5, schedule="awake", activity="打游戏", location="房间"
            )
        text = console.strip(self._capture(caplog)[0])
        assert "心情 happy" in text
        assert "▰▰▰▰▰▱▱▱▱▱" in text  # energy bar, half full
        assert "正在 打游戏（房间）" in text

    def test_panel_emits_bordered_block(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            narrate().panel("已就绪", ["QQ 接入  ws://127.0.0.1:8080/x"])
        lines = console.strip("\n".join(self._capture(caplog))).splitlines()
        assert lines[0].startswith("╭") and lines[-1].startswith("╰")
        assert "QQ 接入" in lines[1]

    def test_disabled_narrator_is_silent(self, caplog) -> None:
        configure(False)
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            narrate().world("不该出现")
            narrate().panel("也不该出现", ["x"])
        assert self._capture(caplog) == []

    def test_narration_never_raises(self) -> None:
        story = Narrator(logger=_ExplodingLogger())  # type: ignore[arg-type]
        story.world("boom")  # swallowed, not raised
        story.panel("boom", ["x"])


class _ExplodingLogger:
    def info(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("console exploded")


class TestLogRendering:
    def _record(self, message: str, *, level: int = logging.INFO) -> logging.LogRecord:
        return logging.LogRecord("CatooBot.World", level, __file__, 10, message, None, None)

    def test_console_formatter_shows_badges_and_names(self) -> None:
        from app.utils.logger import ConsoleFormatter

        console.set_color_enabled(False)
        formatter = ConsoleFormatter()
        out = formatter.format(self._record("她开始打游戏了"))
        assert "INFO" in out and "World" in out and "她开始打游戏了" in out
        warning = formatter.format(self._record("出问题了", level=logging.WARNING))
        assert "WARN" in warning

    def test_narration_records_render_as_story(self) -> None:
        from app.utils.logger import ConsoleFormatter

        record = self._record("🌍 世界 │ 打游戏")
        record.narrate = True  # type: ignore[attr-defined]
        out = ConsoleFormatter().format(record)
        assert out == "🌍 世界 │ 打游戏"  # no timestamp/level noise

    def test_file_filter_strips_ansi_and_tags_channels(self) -> None:
        from app.utils.logger import _PlainFilter

        console.set_color_enabled(True)
        record = self._record(console.paint("打游戏", "red"))
        record.narrate = True  # type: ignore[attr-defined]
        record.channel = "world"  # type: ignore[attr-defined]
        assert _PlainFilter().filter(record) is True
        assert record.msg == "[world] 打游戏"
        assert "\033[" not in record.msg

    def test_redaction_still_applies(self) -> None:
        from app.utils.logger import redact

        assert "***" in redact("access_token=abcdef123456")

    def test_setup_logging_respects_switches(self, tmp_path) -> None:
        from app.utils.logger import setup_logging

        setup_logging("INFO", tmp_path, color=False, narrate=False)
        assert console.color_enabled() is False
        assert narrate().enabled is False
        setup_logging("INFO", tmp_path, color=True, narrate=True)
        assert narrate().enabled is True


class TestNarrationInChat:
    async def test_message_flow_is_narrated(self, tmp_path, caplog) -> None:
        """A private message produces sense → judge → reply beats on the console."""
        from tests.ai_mocks import MockAIProvider
        from tests.conftest import private_event
        from tests.test_chat_integration import make_character_bot

        provider = MockAIProvider(behaviors={"A": ["在的呀"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
                await bot.event_bus.emit(private_event("在吗", user_id=777))
                await bot.conversation.wait_idle()
            joined = console.strip("\n".join(r.getMessage() for r in caplog.records))
            assert "感知" in joined
            assert "判断" in joined
            assert "在的呀" in joined  # the outgoing text is echoed back
        finally:
            await bot.shutdown()

    async def test_sandbox_tick_narration_is_opt_in(self, tmp_path, caplog) -> None:
        """v2.0: the sandbox narrates her tick when narrate ticks is on."""
        from app.config.settings import SandboxConfig
        from app.sandbox import BibleCompiler, SandboxRuntime, SandboxStore

        bible = BibleCompiler("config/character_bible.md").compile()
        runtime = SandboxRuntime(SandboxConfig(simulation_seed=3), SandboxStore(None), bible=bible)
        await runtime.start()
        runtime.narrate_ticks = False
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            await runtime.tick(minutes=10)
        assert not any("沙盒心跳" in console.strip(r.getMessage()) for r in caplog.records)
        runtime.narrate_ticks = True
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            await runtime.tick(minutes=10)
        assert any("沙盒心跳" in console.strip(r.getMessage()) for r in caplog.records)
