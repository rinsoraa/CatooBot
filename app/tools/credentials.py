"""Minimal credential manager (spec §81/§82).

Design goals: secrets must never end up in the SQLite database, in logs, in
tool results or on a WebUI page in full.

Resolution order:

1. ``get_secret(name)`` → environment variable (``.env`` already loads into it)
2. ``data/secrets.json`` — a local, git-ignored file outside the database
3. nothing → callers treat the tool as "not configured"

The WebUI only ever sees ``masked()`` output. A future version can swap the
storage backend (OS keychain, vault) without touching tool code.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.config.settings import PROJECT_ROOT

SECRETS_FILE = PROJECT_ROOT / "data" / "secrets.json"
MASK = "********"


class CredentialManager:
    def __init__(
        self,
        path: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._path = path or SECRETS_FILE
        self._log = logger or logging.getLogger("CatooBot.Credentials")
        self._cache: dict[str, str] | None = None

    # ----------------------------------------------------------------- read

    def get_secret(self, name: str) -> str:
        """Return the secret value (env first), or "" when unset."""
        if not name:
            return ""
        from_env = os.environ.get(name, "")
        if from_env:
            return from_env
        return self._load().get(name, "")

    def has_secret(self, name: str) -> bool:
        return bool(self.get_secret(name))

    def source_of(self, name: str) -> str:
        if not name:
            return "unset"
        if os.environ.get(name):
            return "env"
        return "file" if name in self._load() else "unset"

    @staticmethod
    def mask(value: str) -> str:
        """Never render a secret in full (spec §40/§82)."""
        if not value:
            return ""
        if len(value) <= 8:
            return MASK
        return f"{value[:3]}{MASK}{value[-4:]}"

    def masked(self, name: str) -> str:
        return self.mask(self.get_secret(name))

    def names(self) -> list[str]:
        from_env = {key for key in os.environ if key.endswith(("_KEY", "_TOKEN", "_SECRET"))}
        merged = set(self._load()) | from_env
        return sorted(merged)

    # ---------------------------------------------------------------- write

    def set_secret(self, name: str, value: str) -> None:
        if not name:
            raise ValueError("secret name is required")
        data = dict(self._load())
        if value:
            data[name] = value
        else:
            data.pop(name, None)
        self._save(data)
        self._log.info("Credential '%s' updated (masked=%s)", name, self.mask(value))

    def delete_secret(self, name: str) -> bool:
        data = self._load()
        if name not in data:
            return False
        data.pop(name)
        self._save(data)
        self._log.info("Credential '%s' deleted", name)
        return True

    # ------------------------------------------------------------ internals

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            return self._cache
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._log.warning("secrets.json is unreadable or invalid; ignoring it")
            self._cache = {}
            return self._cache
        self._cache = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        return self._cache

    def _save(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:  # best effort on POSIX; Windows ACLs are inherited from the folder
            self._path.chmod(0o600)
        except OSError:
            pass
        self._cache = data
