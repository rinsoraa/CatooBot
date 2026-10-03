"""Config-editor tests (v0.9 UI work).

Spec coverage: the WebUI writes ``config/overrides.yaml`` (never the operator's
commented ``config.yaml``), the loader merges it on top of the base, invalid
input is rejected *before* touching disk, and AI provider/model edits hot-reload
the router without restarting the process.
"""

from __future__ import annotations

import pytest

from app.config.settings import deep_merge, load_config, read_overrides, write_overrides


class TestOverridesFile:
    def test_deep_merge_merges_dicts_and_replaces_lists(self) -> None:
        base = {"ai": {"enabled": True, "models": [{"name": "a"}]}, "bot": {"name": "x"}}
        extra = {"ai": {"enabled": False, "models": [{"name": "b"}]}}
        merged = deep_merge(base, extra)
        assert merged["ai"]["enabled"] is False
        assert merged["ai"]["models"] == [{"name": "b"}]
        assert merged["bot"]["name"] == "x"

    def test_write_and_read_roundtrip(self, tmp_path) -> None:
        target = tmp_path / "ov.yaml"
        write_overrides({"logging": {"level": "DEBUG"}}, target)
        assert target.exists()
        assert "logging" in target.read_text(encoding="utf-8")
        assert read_overrides(target) == {"logging": {"level": "DEBUG"}}

    def test_read_missing_is_empty(self, tmp_path) -> None:
        assert read_overrides(tmp_path / "nope.yaml") == {}

    def test_load_config_merges_overrides(self, tmp_path, monkeypatch) -> None:
        # the real .env must never leak into the test process (W6 §126)
        monkeypatch.setattr("app.config.settings.load_dotenv", lambda *a, **k: None)

        from app.config.settings import PROJECT_ROOT

        overrides = tmp_path / "ov.yaml"
        write_overrides({"bot": {"name": "覆盖名"}}, overrides)
        config = load_config(overrides_path=overrides)
        assert config.bot.name == "覆盖名"
        # untouched sections keep the real config values
        assert config.database.url.startswith("sqlite:///")
        assert PROJECT_ROOT is not None


class TestConfigAdminService:
    async def test_snapshot_hides_secrets(self, tmp_path, monkeypatch) -> None:
        from app.web.services.config_admin import ConfigAdminService
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        monkeypatch.setenv("TESTKEY", "super-secret-value")
        service = ConfigAdminService(bot, overrides_path=tmp_path / "ov.yaml")
        snap = service.snapshot()
        # no provider is configured by default, but the shape must not leak keys
        assert "ai" in snap and "providers" in snap["ai"]
        dumped = str(snap)
        assert "super-secret-value" not in dumped
        await bot.database.close()

    async def test_ai_form_adds_provider_and_model(self, tmp_path) -> None:
        from app.web.services.config_admin import ConfigAdminService
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        service = ConfigAdminService(bot, overrides_path=tmp_path / "ov.yaml")
        form = {
            "ai_enabled": "1",
            "ai_temperature": "0.7",
            "p_new_name": "workbuddy",
            "p_new_base": "http://127.0.0.1:7864/v1",
            "p_new_env": "WORKBUDDY_API_KEY",
            "m_new_name": "primary",
            "m_new_provider": "workbuddy",
            "m_new_model": "cn:deepseek-v4.1-flash",
        }
        section = service.ai_form(form)
        assert section["enabled"] is True
        assert section["providers"]["workbuddy"]["base_url"].endswith("/v1")
        assert section["models"][0]["provider"] == "workbuddy"

    async def test_save_validates_before_writing(self, tmp_path) -> None:
        from app.web.services.config_admin import ConfigAdminService
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        service = ConfigAdminService(bot, overrides_path=tmp_path / "ov.yaml")
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            await service.save({"logging": {"level": "NOT_A_LEVEL"}})

    async def test_basic_form_parses_ids_and_booleans(self, tmp_path) -> None:
        from app.web.services.config_admin import ConfigAdminService
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        service = ConfigAdminService(bot, overrides_path=tmp_path / "ov.yaml")
        payload = service.basic_form(
            {"superusers": "123, 456 789", "admins": "999", "log_narrate": "1"}
        )
        assert payload["permissions"]["superusers"] == ["123", "456", "789"]
        assert payload["logging"]["narrate"] is True

    async def test_web_form_requires_password_length(self, tmp_path) -> None:
        from app.web.services.config_admin import ConfigAdminService
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        service = ConfigAdminService(bot, overrides_path=tmp_path / "ov.yaml")
        with pytest.raises(ValueError):
            await service.web_form({"web_password": "abc"})
        await bot.database.close()

    async def test_memory_consolidation_schedule_hot_applies(self, tmp_path) -> None:
        """The consolidator and its scheduler captured ``config.memory`` at
        construction; a WebUI edit to 巩固 cadence used to do nothing until a
        restart. Changing daily → hourly must move the live interval."""
        from app.web.services.config_admin import ConfigAdminService
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        try:
            service = ConfigAdminService(bot, overrides_path=tmp_path / "ov.yaml")
            assert bot.consolidation_scheduler.interval_seconds == 86400.0  # daily
            after = bot.config.model_copy(deep=True)
            after.memory.consolidation.schedule = "hourly"
            notes = await service.apply(after)
            assert bot.consolidator._config.consolidation.schedule == "hourly"  # noqa: SLF001
            assert bot.consolidation_scheduler.interval_seconds == 3600.0
            assert not any("重启" in note for note in notes)
        finally:
            await bot.shutdown()


class TestAIReconfigure:
    async def test_reconfigure_swaps_the_router(self, tmp_path) -> None:
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig
        from tests.ai_mocks import MockAIProvider
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        provider = MockAIProvider()
        engine = AIEngine(
            AIConfig(
                enabled=True,
                models=[{"name": "a", "provider": "mock", "model": "a"}],
            ),
            bot.database,
            providers={"mock": provider},
        )
        assert engine.router.model_count == 1

        report = await engine.reconfigure(
            AIConfig(
                enabled=True,
                models=[
                    {"name": "a", "provider": "mock", "model": "a"},
                    {"name": "b", "provider": "mock", "model": "b"},
                ],
            )
        )
        assert report["enabled"] is True
        assert "b" in report["models_added"]
        assert engine.router.model_count == 2
        await bot.database.close()

    async def test_reconfigure_pushes_context_config(self, tmp_path) -> None:
        """ai.context was captured at construction and never re-pushed — a WebUI
        edit to the context window silently did nothing until a restart."""
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig, AIContextConfig
        from tests.ai_mocks import MockAIProvider
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "a", "provider": "mock", "model": "a"}]),
            bot.database,
            providers={"mock": MockAIProvider()},
        )
        assert engine.conversations._config.max_messages == 20  # noqa: SLF001

        await engine.reconfigure(
            AIConfig(
                enabled=True,
                models=[{"name": "a", "provider": "mock", "model": "a"}],
                context=AIContextConfig(enabled=True, max_messages=5),
            )
        )
        assert engine.conversations._config.max_messages == 5  # noqa: SLF001
        await bot.database.close()

    async def test_reconfigure_dropping_model(self, tmp_path) -> None:
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig
        from tests.ai_mocks import MockAIProvider
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        engine = AIEngine(
            AIConfig(
                enabled=True,
                models=[
                    {"name": "a", "provider": "mock", "model": "a"},
                    {"name": "b", "provider": "mock", "model": "b"},
                ],
            ),
            bot.database,
            providers={"mock": MockAIProvider()},
        )
        report = await engine.reconfigure(
            AIConfig(
                enabled=True,
                models=[{"name": "a", "provider": "mock", "model": "a"}],
            )
        )
        assert report["models_removed"] == ["b"]
        await bot.database.close()
