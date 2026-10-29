"""WebUI authentication: PBKDF2-hashed credentials + cookie sessions.

Bootstrap: on first run the admin user is created from ``web.username`` and
``CATOOBOT_WEB_PASSWORD`` (or ``web.password``) via environment — the
plaintext password is never stored, only a salted PBKDF2 hash in SQLite.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config.settings import WebConfig
    from app.database.database import Database

SESSION_TTL_SECONDS = 12 * 3600
_ITERATIONS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), _ITERATIONS
    )
    return f"pbkdf2${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


class AuthService:
    def __init__(
        self,
        config: WebConfig,
        database: Database | None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._config = config
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Web")
        self._clock = clock
        # token -> expiry (in-memory sessions; server restart = re-login)
        self._sessions: dict[str, float] = {}

    # ---------------------------------------------------------------- users

    async def ensure_bootstrap_user(self) -> None:
        """Create the admin account on first run (idempotent)."""
        if self._db is None:
            self._log.warning("No database: WebUI auth falls back to config bootstrap each start")
        else:
            row = await self._db.fetchone(
                "SELECT username FROM web_users WHERE username = ?",
                (self._config.username,),
            )
            if row is not None:
                return
        password = (
            os.environ.get(self._config.password_env, "").strip() or self._config.password.strip()
        )
        if not password:
            self._log.warning(
                "WebUI admin '%s' has no password set (%s empty) — set it before use",
                self._config.username,
                self._config.password_env,
            )
            password = secrets.token_urlsafe(12)
            self._log.warning("Generated a temporary password for this run only")
        await self.create_user(self._config.username, password)

    async def create_user(self, username: str, password: str) -> None:
        if self._db is None:
            return
        await self._db.execute(
            """INSERT INTO web_users (username, password_hash, created_at) VALUES (?, ?, ?)
               ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash""",
            (username, hash_password(password), int(self._clock())),
        )
        self._log.info("WebUI account ensured: %s", username)

    async def set_password(self, username: str, password: str) -> bool:
        """Change a WebUI password from the admin UI (hash only, never plaintext)."""
        if self._db is None:
            return False
        await self._db.execute(
            "UPDATE web_users SET password_hash = ? WHERE username = ?",
            (hash_password(password), username),
        )
        row = await self._db.fetchone(
            "SELECT password_hash FROM web_users WHERE username = ?", (username,)
        )
        if row is None:
            return False
        # every existing session is invalid after a password change
        self._sessions.clear()
        self._log.info("WebUI password changed for '%s' (sessions invalidated)", username)
        return True

    async def rename_user(self, old: str, new: str) -> bool:
        if self._db is None or not new or old == new:
            return False
        exists = await self._db.fetchone(
            "SELECT username FROM web_users WHERE username = ?", (new,)
        )
        if exists is not None:
            raise ValueError(f"用户名 '{new}' 已存在")
        await self._db.execute("UPDATE web_users SET username = ? WHERE username = ?", (new, old))
        self._log.info("WebUI account renamed: %s -> %s", old, new)
        return True

    async def login(self, username: str, password: str) -> str | None:
        """Verify credentials; returns a session token or None."""
        stored: str | None = None
        if self._db is not None:
            row = await self._db.fetchone(
                "SELECT password_hash FROM web_users WHERE username = ?", (username,)
            )
            stored = row["password_hash"] if row else None
        if stored is None or not verify_password(password, stored):
            self._log.warning("WebUI login failed for user '%s'", username)
            return None
        token = secrets.token_urlsafe(32)
        self._sessions[token] = self._clock() + SESSION_TTL_SECONDS
        self._log.info("WebUI login ok: %s", username)
        return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        expiry = self._sessions.get(token)
        if expiry is None:
            return False
        if expiry < self._clock():
            del self._sessions[token]
            return False
        return True

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)
