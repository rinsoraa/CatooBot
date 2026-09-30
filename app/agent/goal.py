"""Goal parsing and task classification (spec §7/§14/§15/§36/§37).

The classifier decides *how much machinery* a message deserves:

* ``simple``        → plain character chat (no tools, no plan)
* ``tool_assisted`` → the v0.6 single-tool path (spec §17/§123)
* ``multi_step``    → the agent runtime (spec §18/§124)
* ``long_running``  → recognized but refused in v0.7 (spec §15/§50)

It also detects natural-language control intents (cancel / pause / resume) so
"别查了" stops the running task without inventing any QQ command (spec §36).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agent.models import CLASSIFICATIONS, Goal
from app.config.settings import AgentConfig

CONTROL_CANCEL = "cancel"
CONTROL_PAUSE = "pause"
CONTROL_RESUME = "resume"


@dataclass
class Classification:
    kind: str = "simple"
    reason: str = ""
    control: str = ""                 # "", cancel, pause, resume
    confidence: float = 0.6
    markers: list[str] = field(default_factory=list)

    @property
    def needs_agent(self) -> bool:
        return self.kind == "multi_step"

    @property
    def needs_tools(self) -> bool:
        return self.kind in ("tool_assisted", "multi_step")


class TaskClassifier:
    def __init__(
        self,
        config: AgentConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self._log = logger or logging.getLogger("CatooBot.Agent")

    # ------------------------------------------------------------- controls

    def detect_control(self, text: str) -> str:
        """Natural-language cancel/pause/resume (spec §36/§37/§57/§98)."""
        normalized = text.strip()
        if not normalized or len(normalized) > 20:
            return ""
        # Strip trailing punctuation so "不用查了。" still counts.
        core = normalized.rstrip("。！!？?~～ ")
        for phrases, intent in (
            (self.config.cancel_phrases, CONTROL_CANCEL),
            (self.config.pause_phrases, CONTROL_PAUSE),
            (self.config.resume_phrases, CONTROL_RESUME),
        ):
            for phrase in phrases:
                if not phrase or phrase not in normalized:
                    continue
                # "算了" inside a longer sentence ("我今天算了算账…") is not a command.
                if len(core) <= len(phrase) + 4:
                    return intent
        return ""

    # --------------------------------------------------------- classification

    def classify(
        self,
        text: str,
        *,
        tool_candidates: int = 0,
        has_active_task: bool = False,
    ) -> Classification:
        """Rule-based, cheap and predictable — the LLM is *not* asked here."""
        control = self.detect_control(text)
        if control:
            return Classification(
                kind="simple", reason=f"control:{control}", control=control, confidence=0.9
            )

        stripped = text.strip()
        if not stripped:
            return Classification(kind="simple", reason="empty")

        long_running = any(
            phrase in stripped
            for phrase in ("每天", "定时", "监控一下", "持续关注", "以后每天", "定时提醒")
        )
        if long_running:
            return Classification(
                kind="long_running",
                reason="requires长期后台任务（v0.7 仅预留框架）",
                confidence=0.7,
            )

        markers = [marker for marker in self.config.multi_step_markers if marker in stripped]
        # A second sentence usually means a second thing to do.
        if not markers and stripped.count("，") + stripped.count(",") >= 2 and tool_candidates:
            markers = ["多个诉求"]
        if markers and tool_candidates:
            return Classification(
                kind="multi_step",
                reason="multi_step_markers:" + ",".join(markers[:3]),
                confidence=0.75,
                markers=markers,
            )

        if tool_candidates:
            return Classification(
                kind="tool_assisted", reason="tool_candidates", confidence=0.7
            )

        return Classification(kind="simple", reason="plain_chat")

    def may_handle(self, kind: str) -> bool:
        mode = self.config.mode
        allowed = {
            "simple": mode.simple,
            "tool_assisted": mode.tool_assisted,
            "multi_step": mode.multi_step,
            "long_running": mode.long_running,
        }
        return bool(self.config.enabled and allowed.get(kind, False))


class GoalParser:
    """Turns a user message into a Goal (spec §7/§8)."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("CatooBot.Agent")

    def parse(
        self,
        text: str,
        *,
        session_id: str,
        user_id: str = "",
        group_id: str | None = None,
        classification: Classification | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> Goal:
        kind = classification.kind if classification else "multi_step"
        return Goal(
            goal_id=uuid.uuid4().hex[:12],
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            description=text.strip(),
            type=kind if kind in CLASSIFICATIONS else "multi_step",
            priority=5,
            constraints=dict(constraints or {}),
            status="pending",
            created_at=int(time.time()),
        )
