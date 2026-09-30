"""CatooBot v2.0 Character Life Sandbox.

The character does not "have a world state" — she lives in one: entities,
spaces, objects, needs, actions, rules and events, driven by a bounded tick
and by external events, with an AI only consulted for genuinely ambiguous
choices.
"""

from app.sandbox.bible import BibleCompiler, CharacterBible
from app.sandbox.lifecycle import CharacterLifecycleManager
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore

__all__ = [
    "BibleCompiler",
    "CharacterBible",
    "CharacterLifecycleManager",
    "SandboxRuntime",
    "SandboxStore",
]
