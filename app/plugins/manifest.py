"""Plugin manifests: the contract every plugin signs (spec §88-§91).

A plugin ships ``plugin.json`` next to its code. The manifest is required —
the loader refuses a plugin without a valid one — and it declares *what the
plugin may touch*: the capability guard (:mod:`app.plugins.api`) refuses every
surface the manifest does not list, with an audit line.

Deliberately coarse capabilities: one entry per surface (not per OneBot
action), so the list stays short and a new API method rarely needs a new
capability.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

#: The plugin API this build implements. A plugin whose ``api_version`` is not
#: in :data:`SUPPORTED_API_VERSIONS` is refused at load time.
PLUGIN_API_VERSION = "1.0"
SUPPORTED_API_VERSIONS: frozenset[str] = frozenset({"1.0"})

#: Every capability a plugin may declare.
CAPABILITIES: frozenset[str] = frozenset(
    {
        "send.group",  # send messages into groups
        "send.private",  # send private messages
        "message.read",  # subscribe to incoming messages
        "api.call",  # raw OneBot actions (adapter access included)
        "schedule",  # register background jobs on the shared scheduler
        "tools",  # the tool runtime (call tools, register tools)
        "services",  # internal subsystems: character, memory, sandbox, delivery…
    }
)

_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")  # reverse-DNS style
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

MANIFEST_NAME = "plugin.json"


class PluginManifestError(Exception):
    """A plugin manifest is missing, malformed or unsupported."""


class PluginManifest(BaseModel):
    """Parsed ``plugin.json`` (unknown fields are rejected — typos must not pass)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    api_version: str = PLUGIN_API_VERSION
    capabilities: frozenset[str] = frozenset({"message.read"})
    author: str = ""
    description: str = ""

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"id {value!r} must be reverse-DNS style (e.g. 'bot.catoo.chat')")
        return value

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if not _VERSION_RE.match(value):
            raise ValueError(f"version {value!r} must be MAJOR.MINOR.PATCH")
        return value

    @field_validator("api_version")
    @classmethod
    def _check_api_version(cls, value: str) -> str:
        if value not in SUPPORTED_API_VERSIONS:
            raise ValueError(
                f"api_version {value!r} is not supported by this build "
                f"(supported: {', '.join(sorted(SUPPORTED_API_VERSIONS))}; "
                f"current: {PLUGIN_API_VERSION})"
            )
        return value

    @field_validator("capabilities")
    @classmethod
    def _check_capabilities(cls, value: frozenset[str]) -> frozenset[str]:
        unknown = sorted(set(value) - CAPABILITIES)
        if unknown:
            raise ValueError(
                f"unknown capability/capabilities {unknown}; valid: "
                f"{', '.join(sorted(CAPABILITIES))}"
            )
        return frozenset(value)


def load_manifest(path: str | Path) -> PluginManifest:
    """Read and validate one ``plugin.json`` (raises :class:`PluginManifestError`)."""
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise PluginManifestError(f"missing {MANIFEST_NAME}: {manifest_path}")
    try:
        data: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PluginManifestError(f"{manifest_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginManifestError(f"{manifest_path} must contain a JSON object")
    try:
        return PluginManifest.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ())) or "manifest"
        raise PluginManifestError(
            f"{manifest_path} is invalid at {field}: {first.get('msg', 'unknown error')}"
        ) from exc


def manifest_path_for(plugin_cls: type[Any]) -> Path:
    """Locate a plugin's manifest.

    A plugin *package* (``plugins/chat/plugin.py`` or ``plugins/chat/__init__.py``)
    uses ``plugin.json`` inside the package; a single-file plugin
    (``plugins/greet.py``) uses ``greet.json`` next to it.
    """
    module = sys.modules.get(plugin_cls.__module__)
    file = getattr(module, "__file__", None)
    if not file:
        raise PluginManifestError(
            f"cannot locate the module file of {plugin_cls.__module__} to find {MANIFEST_NAME}"
        )
    path = Path(file).resolve()
    if path.name == "__init__.py" or path.stem == "plugin":
        return path.parent / MANIFEST_NAME
    return path.parent / f"{path.stem}.json"
