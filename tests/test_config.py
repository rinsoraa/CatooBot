"""Configuration loading tests."""

from __future__ import annotations

from pathlib import Path

import pytest

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

    def test_empty_override_does_not_mask_models_table(self, tmp_path, monkeypatch) -> None:
        """WebUI 把空串写进 overrides 时，models: 表的推导不能被静默屏蔽。"""
        monkeypatch.delenv("CATOOBOT_ONEBOT_ACCESS_TOKEN", raising=False)
        config_path = self._write(
            tmp_path / "config.yaml",
            "models:\n"
            "  providers:\n"
            "    mock:\n"
            "      type: openai_compatible\n"
            "      base_url: http://127.0.0.1:1/v1\n"
            "  chat:\n"
            "    - { name: small, provider: mock, model: cn:glm-5.3-flash }\n"
            "  extraction: small\n",
        )
        overrides_path = self._write(
            tmp_path / "overrides.yaml",
            "memory:\n  extraction:\n    model: ''\n",
        )
        config = load_config(
            config_path,
            env_file=tmp_path / "no.env",
            overrides_path=overrides_path,
        )
        assert config.memory.extraction.model == "small"

    def test_model_ref_to_unknown_name_logs_error(self, tmp_path, monkeypatch, caplog) -> None:
        import logging

        monkeypatch.delenv("CATOOBOT_ONEBOT_ACCESS_TOKEN", raising=False)
        config_path = self._write(
            tmp_path / "config.yaml",
            "models:\n"
            "  providers:\n"
            "    mock:\n"
            "      type: openai_compatible\n"
            "      base_url: http://127.0.0.1:1/v1\n"
            "  chat:\n"
            "    - { name: primary, provider: mock, model: m }\n"
            "  extraction: not-a-name\n",
        )
        with caplog.at_level(logging.ERROR, logger="CatooBot.Config"):
            self._load(config_path, tmp_path)
        messages = [r.getMessage() for r in caplog.records]
        assert any("memory.extraction.model='not-a-name'" in m for m in messages)
        assert any("primary" in m for m in messages), "必须列出可用 name"

    def test_malformed_yaml_gives_a_friendly_error(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("CATOOBOT_ONEBOT_ACCESS_TOKEN", raising=False)
        config_path = self._write(
            tmp_path / "config.yaml",
            'onebot:\n  port: 8080\n  path: "/onebot/v11/ws\n',
        )
        with pytest.raises(SystemExit) as exc_info:
            self._load(config_path, tmp_path)
        message = str(exc_info.value)
        assert "配置文件语法错误" in message
        assert "config.yaml" in message
        assert "path:" in message  # 出错行原文
        assert "引号" in message  # 常见原因提示
