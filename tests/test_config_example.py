"""Config file hygiene: the shipped example must stay valid and unambiguous."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = PROJECT_ROOT / "config" / "config.example.yaml"
LIVE = PROJECT_ROOT / "config" / "config.yaml"
TOP_LEVEL = re.compile(r"^([a-z_]+):")


def top_level_keys(path: Path) -> list[str]:
    return [
        match.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := TOP_LEVEL.match(line))
    ]


class TestConfigExample:
    def test_example_exists(self) -> None:
        assert EXAMPLE.exists()

    def test_no_duplicate_top_level_sections(self) -> None:
        """A duplicated section silently overrides the earlier one — never ship that."""
        keys = top_level_keys(EXAMPLE)
        duplicates = {key for key in keys if keys.count(key) > 1}
        assert duplicates == set(), f"duplicate sections in config.example.yaml: {duplicates}"

    def test_example_parses_as_yaml(self) -> None:
        data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_example_loads_through_the_real_config_loader(self) -> None:
        from app.config.settings import AppConfig

        data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
        config = AppConfig.model_validate(data)
        # the example must be runnable as-is, with safe placeholders
        assert config.bot.name
        assert config.onebot.path.startswith("/")
        assert config.web.enabled is False  # WebUI stays opt-in in the example
        assert config.memory.semantic.enabled is False
        assert config.behavior.initiative.enabled is False

    def test_example_documents_every_section(self) -> None:
        expected = {
            "bot",
            "onebot",
            "logging",
            "database",
            "permissions",
            "ai",
            "character",
            "memory",
            "behavior",
            "web",
        }
        assert expected <= set(top_level_keys(EXAMPLE))

    @pytest.mark.skipif(not LIVE.exists(), reason="no local config.yaml in this checkout")
    def test_live_config_has_no_duplicate_sections(self) -> None:
        """The live file is operator-owned, but duplicates are always a bug."""
        keys = top_level_keys(LIVE)
        duplicates = {key for key in keys if keys.count(key) > 1}
        assert duplicates == set(), f"duplicate sections in config.yaml: {duplicates}"

    @pytest.mark.skipif(not LIVE.exists(), reason="no local config.yaml in this checkout")
    def test_example_and_live_sections_are_aligned(self) -> None:
        """Both files describe the same shape, in the same order (easy to diff)."""
        assert top_level_keys(LIVE) == top_level_keys(EXAMPLE)
