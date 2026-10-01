"""Task 25 ⑤: a misconfigured AI must say so loudly at startup.

Two failure modes used to be quiet enough to waste an afternoon:

* a model bound to a provider name that does not exist (or whose API key is
  missing, so the provider was never built) — the router disabled it silently;
* AI enabled with nothing usable left — the bot started and every AI feature
  simply did nothing.

Both now log at ERROR and name what *was* configured, and the dashboard shows
a banner, so "why is she not answering" is answerable at a glance.
"""

from __future__ import annotations

import logging

from app.ai.router import ModelRouter, ModelSpec
from tests.ai_mocks import MockAIProvider


class TestUnknownProvider:
    def test_router_names_the_providers_it_knows(self, caplog) -> None:
        router = ModelRouter(
            [
                ModelSpec(name="main", provider="ghost", model="x"),
                ModelSpec(name="backup", provider="mock", model="y"),
            ],
            {"mock": MockAIProvider()},
        )
        assert list(router.states) == ["backup"]
        line = "\n".join(
            record.getMessage() for record in caplog.records if record.name == "CatooBot.AI.Router"
        )
        assert "unknown provider 'ghost'" in line
        assert "known providers: ['mock']" in line
        assert any(record.levelno == logging.ERROR for record in caplog.records)

    def test_a_missing_api_key_is_named_as_the_reason(self, caplog) -> None:
        """When no provider survives, the message must say so — an empty
        ``known providers:`` list would read like a typo in the config."""
        router = ModelRouter([ModelSpec(name="main", provider="mock", model="x")], {})
        assert router.states == {}
        line = "\n".join(record.getMessage() for record in caplog.records)
        assert "known providers: none — no provider had an API key" in line


class TestNoUsableModel:
    async def test_engine_errors_and_disables_itself(self, tmp_path, caplog) -> None:
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig, DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'ai.db'}"))
        await database.connect()
        try:
            config = AIConfig(
                enabled=True,
                providers={
                    "openai": {
                        "type": "openai_compatible",
                        "base_url": "http://127.0.0.1:1/v1",
                        "api_key_env": "NOPE_KEY",
                    }
                },
                models=[{"name": "main", "provider": "openai", "model": "gpt-x"}],
            )
            with caplog.at_level(logging.ERROR, logger="CatooBot.AI"):
                engine = AIEngine(config, database)
            assert engine.enabled is False
            line = "\n".join(record.getMessage() for record in caplog.records)
            assert "AI is enabled but no usable model is left" in line
            assert "configured providers=['openai']" in line
            assert "configured models=['main']" in line
        finally:
            await database.close()

    async def test_a_healthy_engine_does_not_log_the_error(self, tmp_path, caplog) -> None:
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig, DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'ai2.db'}"))
        await database.connect()
        try:
            config = AIConfig(
                enabled=True,
                providers={
                    "mock": {
                        "type": "openai_compatible",
                        "base_url": "http://127.0.0.1:1/v1",
                        "api_key_env": "NOPE",
                    }
                },
                models=[{"name": "main", "provider": "mock", "model": "m"}],
            )
            with caplog.at_level(logging.ERROR, logger="CatooBot.AI"):
                engine = AIEngine(config, database, providers={"mock": MockAIProvider("mock")})
            assert engine.enabled is True
            assert not any(
                "no usable model is left" in record.getMessage() for record in caplog.records
            )
        finally:
            await database.close()
