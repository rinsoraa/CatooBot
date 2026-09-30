"""Persona and character identity models.

The persona is *data*, never code: identity, personality, speaking style and
behavior rules are edited through the WebUI and persisted as JSON. Default
values are deliberately empty — the operator fills them in; CatooBot does not
ship a canned character.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CharacterIdentity(BaseModel):
    """Who the character is. Empty by default; the operator defines it."""

    name: str = ""
    nickname: str = ""
    age: str = ""
    birthday: str = ""
    gender: str = ""
    occupation: str = ""
    location: str = ""
    background: str = ""


class Personality(BaseModel):
    traits: list[str] = Field(default_factory=list)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    habits: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)


class SpeakingStyle(BaseModel):
    language: str = "zh-CN"
    tone: str = "casual"
    emoji: bool = True
    kaomoji: bool = False
    length_preference: str = "mixed"  # short | mixed | long
    notes: str = ""


class BehaviorRules(BaseModel):
    """Hard rules the character must follow (identity boundary etc.)."""

    rules: list[str] = Field(default_factory=list)


class Persona(BaseModel):
    """The full character definition, versioned and hot-reloadable."""

    name: str = "default"
    identity: CharacterIdentity = Field(default_factory=CharacterIdentity)
    personality: Personality = Field(default_factory=Personality)
    speaking_style: SpeakingStyle = Field(default_factory=SpeakingStyle)
    behavior_rules: BehaviorRules = Field(default_factory=BehaviorRules)
    system_prompt: str = ""  # free-form extra prompt on top of the structured parts

    def is_configured(self) -> bool:
        """False while the operator has not defined anything yet."""
        return bool(
            self.identity.name
            or self.identity.background
            or self.personality.traits
            or self.system_prompt
        )

    def to_json(self) -> str:
        import json

        return json.dumps(self.model_dump(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> Persona:
        import json

        return cls.model_validate(json.loads(raw))

    @classmethod
    def from_config(cls, data: dict[str, Any] | None) -> Persona:
        """Build from the ``character:`` section of config.yaml."""
        if not data:
            return cls()
        normalized = dict(data)
        rules = normalized.get("behavior_rules")
        if isinstance(rules, list):
            # config.yaml carries rules as a plain list; the model wraps them
            normalized["behavior_rules"] = {"rules": rules}
        return cls.model_validate(normalized)
