"""PersonaManager: loads, caches and hot-reloads the active persona.

Precedence: active row in the ``personas`` table (edited via WebUI)
> ``character:`` section of config.yaml > empty default persona. A failed
reload keeps the last valid persona (spec §53).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.character.persona import Persona

if TYPE_CHECKING:
    from app.config.settings import CharacterConfig
    from app.database.database import Database


class PersonaManager:
    def __init__(
        self,
        config: CharacterConfig | None = None,
        database: Database | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Character")
        self._persona = Persona.from_config(dict(config.model_dump()) if config else None)

    @property
    def persona(self) -> Persona:
        return self._persona

    async def load(self) -> Persona:
        """Restore the persona persisted by the WebUI, if any."""
        if self._db is None:
            return self._persona
        try:
            data = await self._db.get_setting_json("active_persona")
        except Exception:  # noqa: BLE001 - DB trouble must not break the character
            self._log.exception("Failed to load persona from database, keeping current")
            return self._persona
        if isinstance(data, dict):
            try:
                self._persona = Persona.model_validate(data)
                self._log.info(
                    "Persona loaded: %s",
                    self._persona.identity.name or self._persona.name,
                )
            except Exception:  # noqa: BLE001
                self._log.exception("Stored persona invalid, keeping current")
        return self._persona

    async def save(self, persona: Persona) -> Persona:
        """Persist a new persona and apply it immediately (WebUI path)."""
        self._persona = persona
        if self._db is not None:
            try:
                await self._db.set_setting_json(
                    "active_persona", persona.model_dump(), int(time.time())
                )
            except Exception:  # noqa: BLE001
                self._log.exception("Failed to persist persona (applied in memory only)")
        self._log.info("Persona updated: %s", persona.name)
        return self._persona
