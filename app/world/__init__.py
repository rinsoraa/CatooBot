"""Persistent world / background life runtime (v0.8).

The character keeps living between messages: a clock, a routine, a state that
only moves for a reason, a timeline of life events, slow-moving goals and a
bounded amount of ambient colour. Everything here is **fiction that belongs to
the character's own world** — it never claims real, verifiable acts, and it
never sends a message on its own: proactive speech still goes through the v0.4
Initiative Gate first.

Entry point: :func:`app.world.runtime.build_world`.
"""

from __future__ import annotations

from app.world.runtime import WorldRuntime, build_world

__all__ = ["WorldRuntime", "build_world"]
