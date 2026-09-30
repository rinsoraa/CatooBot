"""Social Cognition Engine + group participation (v0.9).

Turns "group participation = random probability" into structured social
judgment: observe → understand → evaluate relevance & social context → decide.
Reuses the existing Behavior / Topic / Memory / State / World subsystems.
"""

from __future__ import annotations

from app.social.cognition import SocialCognitionEngine

__all__ = ["SocialCognitionEngine"]
