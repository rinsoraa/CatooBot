"""Plugin manifests + capability guard (Task 11, spec §88-§91).

A plugin must ship a manifest and may only touch the surfaces it declares.
Undeclared use is refused *before* it reaches QQ, written to the audit log and
counted — never silently allowed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.plugins.api import PluginApi, PluginCapabilityError
from app.plugins.manifest import (
    PluginManifest,
    PluginManifestError,
    load_manifest,
)
from tests.conftest import REPO_ROOT, make_bot

_PLUGIN_TEMPLATE = """
from app.plugins.base import Plugin


class DemoPlugin(Plugin):
    name = "{name}"
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__()
        self.denied: Exception | None = None
        self.touched: object = None

    async def on_load(self, bot) -> None:
{body}
"""


def write_plugin(
    root: Path,
    name: str,
    *,
    capabilities: list[str],
    body: str = "        self.touched = bot.log",
    manifest: bool = True,
    manifest_extra: dict[str, Any] | None = None,
) -> Path:
    """One single-file plugin (``<name>.py`` + ``<name>.json``) in *root*."""
    root.mkdir(parents=True, exist_ok=True)
    indented = "\n".join(f"    {line}" if line.strip() else line for line in body.splitlines())
    (root / f"{name}.py").write_text(
        _PLUGIN_TEMPLATE.format(name=name, body=indented), encoding="utf-8"
    )
    if manifest:
        data: dict[str, Any] = {
            "id": f"com.example.{name}",
            "name": name,
            "version": "0.1.0",
            "api_version": "1.0",
            "capabilities": capabilities,
        }
        data.update(manifest_extra or {})
        (root / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root / f"{name}.py"


class TestManifestValidation:
    def _write(self, tmp_path: Path, data: dict[str, Any]) -> Path:
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _valid(self) -> dict[str, Any]:
        return {"id": "com.example.demo", "name": "demo", "version": "1.0.0"}

    def test_minimal_manifest_defaults(self, tmp_path) -> None:
        manifest = load_manifest(self._write(tmp_path, self._valid()))
        assert manifest.id == "com.example.demo"
        assert manifest.api_version == "1.0"
        assert manifest.capabilities == frozenset({"message.read"})

    def test_missing_file_is_refused(self, tmp_path) -> None:
        with pytest.raises(PluginManifestError, match="missing plugin.json"):
            load_manifest(tmp_path / "absent.json")

    def test_invalid_json_is_refused(self, tmp_path) -> None:
        path = tmp_path / "plugin.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(PluginManifestError, match="not valid JSON"):
            load_manifest(path)

    def test_unknown_capability_is_refused(self, tmp_path) -> None:
        data = self._valid() | {"capabilities": ["send.everything"]}
        with pytest.raises(PluginManifestError, match="unknown capability"):
            load_manifest(self._write(tmp_path, data))

    def test_unsupported_api_version_is_refused(self, tmp_path) -> None:
        data = self._valid() | {"api_version": "2.0"}
        with pytest.raises(PluginManifestError, match="api_version"):
            load_manifest(self._write(tmp_path, data))

    def test_unknown_field_is_refused(self, tmp_path) -> None:
        data = self._valid() | {"capabilties": ["message.read"]}  # typo on purpose
        with pytest.raises(PluginManifestError, match="capabilties"):
            load_manifest(self._write(tmp_path, data))

    def test_non_reverse_dns_id_is_refused(self, tmp_path) -> None:
        data = self._valid() | {"id": "Demo Plugin"}
        with pytest.raises(PluginManifestError, match="reverse-DNS"):
            load_manifest(self._write(tmp_path, data))

    def test_manifest_round_trips_through_model(self) -> None:
        manifest = PluginManifest(
            id="com.example.demo",
            name="demo",
            version="1.0.0",
            capabilities={"message.read", "send.private"},
        )
        assert "send.private" in manifest.capabilities


class TestLoaderRequiresManifest:
    async def test_plugin_without_manifest_is_refused_others_load(self, tmp_path) -> None:
        bot = make_bot(tmp_path)
        root = tmp_path / "plugins"
        write_plugin(root, "okplug", capabilities=["message.read"])
        write_plugin(root, "nomanifest", capabilities=[], manifest=False)
        loader = bot.plugins.__class__(bot, user_plugin_dir=root)
        await loader.load_all()
        assert "okplug" in loader.loaded
        assert "nomanifest" not in loader.loaded
        assert loader.failure_counts.get("nomanifest") == 1

    async def test_on_load_failure_is_counted_and_isolated(self, tmp_path) -> None:
        bot = make_bot(tmp_path)
        root = tmp_path / "plugins"
        write_plugin(root, "okplug2", capabilities=["message.read"])
        write_plugin(
            root,
            "boomplug",
            capabilities=["message.read"],
            body='        raise RuntimeError("boom")',
        )
        loader = bot.plugins.__class__(bot, user_plugin_dir=root)
        await loader.load_all()
        assert "okplug2" in loader.loaded
        assert "boomplug" not in loader.loaded
        assert loader.failure_counts.get("boomplug") == 1
        assert bot.metrics.get("plugin_failures") == 1


class TestCapabilityGuard:
    async def _load(self, tmp_path, name: str, capabilities: list[str], body: str):  # type: ignore[no-untyped-def]
        bot = make_bot(tmp_path)
        root = tmp_path / "plugins"
        write_plugin(root, name, capabilities=capabilities, body=body)
        loader = bot.plugins.__class__(bot, user_plugin_dir=root)
        await loader.load_all()
        return bot, loader.loaded[name]

    async def test_undeclared_send_is_refused_before_reaching_qq(self, tmp_path, caplog) -> None:
        body = """        try:
            await bot.send_group(12345, "hi")
        except Exception as exc:  # noqa: BLE001 - captured for the assertion
            self.denied = exc"""
        bot, plugin = await self._load(tmp_path, "capdeny", ["message.read"], body)
        with caplog.at_level(logging.WARNING, logger="CatooBot.Plugins.Audit"):
            pass  # the denial already happened during load
        assert isinstance(plugin.denied, PluginCapabilityError)
        assert "send.group" in str(plugin.denied)
        assert bot.adapter.calls == []  # nothing left the process
        assert bot.metrics.get("plugin_capability_denied") == 1
        assert any(
            "capability denied" in record.getMessage()
            for record in caplog.records
            if record.name == "CatooBot.Plugins.Audit"
        )

    async def test_declared_send_reaches_the_adapter(self, tmp_path) -> None:
        body = """        self.touched = await bot.send_group(12345, "hi")"""
        bot, plugin = await self._load(tmp_path, "capallow", ["message.read", "send.group"], body)
        assert plugin.denied is None
        assert bot.adapter.calls[0][0] == "send_group_msg"
        assert bot.metrics.get("plugin_capability_denied") == 0

    async def test_internal_services_need_the_services_capability(self, tmp_path) -> None:
        body = """        try:
            self.touched = bot.character
        except Exception as exc:  # noqa: BLE001
            self.denied = exc"""
        _, plugin = await self._load(tmp_path, "capsvc", ["message.read"], body)
        assert isinstance(plugin.denied, PluginCapabilityError)
        assert "services" in str(plugin.denied)

    async def test_services_capability_opens_them(self, tmp_path) -> None:
        body = """        self.touched = bot.character"""
        _, plugin = await self._load(tmp_path, "capsvc2", ["services"], body)
        assert plugin.denied is None
        assert plugin.touched is not None

    async def test_observability_attributes_are_free(self, tmp_path) -> None:
        body = (
            """        self.touched = (bot.log is not None, bot.metrics is not None, bot.self_id)"""
        )
        _, plugin = await self._load(tmp_path, "capfree", [], body)
        assert plugin.denied is None
        assert plugin.touched[0] is True and plugin.touched[1] is True

    async def test_unknown_attribute_stays_unknown(self, tmp_path) -> None:
        body = """        try:
            self.touched = bot.write_a_new_service
        except Exception as exc:  # noqa: BLE001
            self.denied = exc"""
        _, plugin = await self._load(tmp_path, "capunknown", ["services"], body)
        assert isinstance(plugin.denied, AttributeError)


class TestShippedChatPlugin:
    def test_chat_manifest_is_valid(self) -> None:
        manifest = load_manifest(REPO_ROOT / "plugins" / "chat" / "plugin.json")
        assert manifest.id == "bot.catoo.chat"
        # the plugin reads messages, replies in both scopes and drives services
        assert {"message.read", "send.private", "send.group", "services"} <= manifest.capabilities

    async def test_loaded_chat_plugin_uses_the_guarded_api(self, tmp_path) -> None:
        from tests.conftest import make_ready_bot

        bot = await make_ready_bot(tmp_path)
        try:
            plugin = bot.plugins.loaded["character"]
            assert isinstance(plugin.bot, PluginApi)
            assert plugin.api is plugin.bot
            assert plugin.manifest is not None
            assert "services" in plugin.manifest.capabilities
            assert bot.plugins.failure_counts == {}
        finally:
            await bot.shutdown()
