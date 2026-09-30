"""Configuration loading tests."""

from __future__ import annotations

from pathlib import Path

from app.config.settings import (
    AppConfig,
    PermissionsConfig,
    load_config,
)


class TestPermissionsNormalization:
    def test_int_ids_become_strings(self) -> None:
        config = PermissionsConfig(superusers=[123456, "654321"], admins=999)
        assert config.superusers == ["123456", "654321"]
        assert config.admins == ["999"]

    def test_none_becomes_empty(self) -> None:
        config = PermissionsConfig(superusers=None, admins=None)
        assert config.superusers == []
        assert config.admins == []


class TestLoadConfig:
    def _write(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def _load(config_path, tmp_path, **kwargs):  # type: ignore[no-untyped-def]
        """load_config isolated from the developer machine's overrides.yaml."""
        return load_config(
            config_path,
            env_file=tmp_path / "no.env",
            overrides_path=tmp_path / "no-overrides.yaml",
            **kwargs,
        )

    def test_yaml_int_superuser_accepted(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("CATOOBOT_ONEBOT_ACCESS_TOKEN", raising=False)
        config_path = self._write(
            tmp_path / "config.yaml",
            "permissions:\n  superusers:\n    - 123456789\n",
        )
        config = self._load(config_path, tmp_path)
        assert config.permissions.superusers == ["123456789"]

    def test_defaults_when_no_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("CATOOBOT_ONEBOT_ACCESS_TOKEN", raising=False)
        config = self._load(tmp_path / "missing.yaml", tmp_path)
        assert config.onebot.port == 8080
        assert config.onebot.path == "/onebot/v11/ws"
        assert config.bot.command_prefix == "/"

    def test_env_overrides_yaml(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CATOOBOT_ONEBOT_PORT", "9999")
        monkeypatch.setenv("CATOOBOT_ONEBOT_ACCESS_TOKEN", "secret")
        self._write(tmp_path / "config.yaml", "onebot:\n  port: 8080\n")
        config = self._load(tmp_path / "config.yaml", tmp_path)
        assert config.onebot.port == 9999
        assert config.onebot.access_token == "secret"

    def test_full_model_defaults(self) -> None:
        config = AppConfig()
        assert config.onebot.url == "ws://127.0.0.1:8080/onebot/v11/ws"
        assert config.permissions.superusers == []
