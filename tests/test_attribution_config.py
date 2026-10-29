from types import SimpleNamespace

from app.config.settings import AppConfig, MinecraftConfig
from app.integrations.minecraft.runtime_client import MinecraftRuntimeError
from app.integrations.minecraft.service import MinecraftService, _translate


def test_strict_config_defaults_disabled_and_has_no_secret_field():
    config = MinecraftConfig()
    assert config.attribution_strict is False
    assert config.attribution_url == "http://127.0.0.1:19132"
    assert "attribution_token" not in config.model_dump()


def test_service_passes_explicit_mode_and_env_secret_without_serializing_it(monkeypatch):
    monkeypatch.setenv("CATOO_ATTRIBUTION_TOKEN", "test-secret-0123456789")
    config = MinecraftConfig(attribution_strict=True)
    bot = SimpleNamespace(config=AppConfig())
    service = MinecraftService(bot, config)
    environment = service._runtime_env()
    assert environment["MC_ATTRIBUTION_STRICT"] == "1"
    assert environment["MC_ATTRIBUTION_URL"] == config.attribution_url
    assert environment["MC_ATTRIBUTION_TOKEN"] == "test-secret-0123456789"
    assert "test-secret" not in config.model_dump_json()


def test_service_disabled_mode_does_not_inherit_stale_runtime_flag(monkeypatch):
    monkeypatch.setenv("MC_ATTRIBUTION_STRICT", "1")
    service = MinecraftService(SimpleNamespace(config=AppConfig()), MinecraftConfig())
    assert service._runtime_env()["MC_ATTRIBUTION_STRICT"] == "0"


def test_missing_proof_is_not_misreported_as_action_busy():
    failure = _translate(
        MinecraftRuntimeError("unconfirmed", code="block.attribution_unconfirmed", status=409)
    )
    assert failure.code == "minecraft.attribution_unconfirmed" and failure.status == 409
    unavailable = _translate(
        MinecraftRuntimeError("unavailable", code="block.attribution_unavailable", status=503)
    )
    assert unavailable.code == "minecraft.attribution_unavailable" and unavailable.status == 503
