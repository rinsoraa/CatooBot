"""Cognitive Decision & Intent layer (v2.1 Phase 6 §5-§20).

    trigger → DecisionGate → [DecisionRequest] → candidates → LLM
            → IntentProposal → DecisionValidator → Action/Mutation → Event

The contract is one line long: **the LLM may propose; only the Sandbox
decides.** Concretely:

* candidates are generated from what the world actually owns (§5) — an action
  the seed never granted cannot appear, so the model cannot invent one;
* the model only ever sees a numbered candidate list and must answer with one
  ``candidate_id`` (§8/§10): strict JSON, validated against the request;
* nothing here mutates the world (§18) — execution happens only after the
  validator re-reads live runtime state and accepts (§19/§20);
* a failed / invalid / stale / low-confidence proposal falls back to a
  deterministic pick (§11) and the sandbox keeps running (§28);
* no LLM is called per tick or per message (§12): only a DecisionRequest the
  gate marked as genuinely ambiguous reaches the model.

All five decision events ride the existing :class:`SandboxEventType` spine
with causation/correlation links (§16/§17).
"""

from __future__ import annotations

import json
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.ai.models import AIRequest, ChatMessage
from app.sandbox.events import SandboxEventType as ET


class DecisionTrigger(str, Enum):  # noqa: UP042 - pydantic-friendly str enum
    """Why a decision is needed at all (§6)."""

    external_invitation = "external_invitation"
    pet_interaction = "pet_interaction"
    resource_shortage = "resource_shortage"
    conflicting_goals = "conflicting_goals"
    action_interrupt = "action_interrupt"
    scheduled_event = "scheduled_event"
    # Phase 7: goal-driven steps
    goal_step = "goal_step"
    goal_conflict = "goal_conflict"
    goal_blocked = "goal_blocked"


class CandidateKind(str, Enum):  # noqa: UP042
    action = "action"  # start a concrete, owned action
    continue_current = "continue_current"  # keep doing what she is doing
    postpone = "postpone"  # do nothing about the trigger for now


class DecisionCandidate(BaseModel):
    """One *legal* option, built from the world, never from the model (§5)."""

    candidate_id: str
    kind: CandidateKind
    action_id: str = ""
    label: str = ""
    reason: str = ""
    #: unmet requirements (empty list = executable right now)
    requirements: list[str] = Field(default_factory=list)
    priority: float = 0.5

    @property
    def executable(self) -> bool:
        return not self.requirements


class DecisionRequest(BaseModel):
    """The complete request handed to the decision layer (§6/§7)."""

    request_id: str
    character_id: str = ""
    trigger: DecisionTrigger = DecisionTrigger.scheduled_event
    #: compact cognitive context (world line / needs / memories — Phase 5 shape)
    context: dict[str, Any] = Field(default_factory=dict)
    candidates: list[DecisionCandidate] = Field(default_factory=list)
    #: human-readable hard constraints the proposal must respect
    constraints: list[str] = Field(default_factory=list)
    deadline: float = 0.0
    #: world revision captured when the request was built (§20 staleness)
    world_revision: int = 0
    #: cognitive revision captured at the same moment (Phase 8.1 §15): what the
    #: character knows — relationships/social context — can move without the
    #: world moving, and a proposal must not outlive either
    cognitive_revision: int = 0
    correlation_id: str = ""
    causation_id: str = ""


class IntentProposal(BaseModel):
    """The model's structured answer (§9/§10) — a proposal, not a change."""

    request_id: str = ""
    candidate_id: str = ""
    confidence: float = 0.0
    #: debug-only excerpt; never written to memory, never a persona text (§21)
    reason: str = ""


class DecisionOutcome(BaseModel):
    """What the decision layer concluded, for the caller to execute (§18)."""

    request_id: str = ""
    accepted: bool = False
    candidate_id: str = ""
    action_id: str = ""
    kind: str = ""
    source: str = ""  # deterministic / llm / fallback / none
    reason: str = ""
    correlation_id: str = ""


#: two or more executable candidates with a real trade-off need the model;
#: one candidate (or none) never does (§13)
LLM_MIN_CANDIDATES = 2

#: a proposal whose request no longer matches the live revisions is not
#: "answered badly" — the request itself is out of date (§16/§22), so it is
#: rejected outright instead of being replaced by a deterministic pick
STALE_REASONS = frozenset({"world_changed", "cognitive_changed"})


class DecisionGate:
    """Answers "does this trigger need an LLM decision at all?" (§13)."""

    def __init__(self, *, allow_llm: bool = True) -> None:
        self.allow_llm = allow_llm

    def executable(self, candidates: list[DecisionCandidate]) -> list[DecisionCandidate]:
        return [candidate for candidate in candidates if candidate.executable]

    def needs_llm(self, candidates: list[DecisionCandidate]) -> bool:
        if not self.allow_llm:
            return False
        return len(self.executable(candidates)) >= LLM_MIN_CANDIDATES


class DecisionValidator:
    """Re-reads live state before anything executes (§19/§20). Never trusts
    the snapshot the model saw."""

    def __init__(self, runtime: Any) -> None:
        self._rt = runtime

    def validate(self, request: DecisionRequest, proposal: IntentProposal) -> tuple[bool, str]:
        rt = self._rt
        if proposal.request_id != request.request_id:
            return False, "request_mismatch"
        if request.character_id and request.character_id != rt.character_id:
            return False, "character_mismatch"
        candidate = next(
            (c for c in request.candidates if c.candidate_id == proposal.candidate_id),
            None,
        )
        if candidate is None:
            return False, "unknown_candidate"
        if request.deadline and float(self._rt._clock()) > request.deadline:  # noqa: SLF001
            return False, "expired"
        if request.world_revision != rt.world_revision:
            return False, "world_changed"  # stale: state moved under the model
        if request.cognitive_revision != rt.cognitive_revision:
            # §16: the relationship/social picture moved — an answer that was
            # written against the old picture is not executed
            return False, "cognitive_changed"
        if candidate.kind is CandidateKind.action:
            definition = rt.actions.definitions.get(candidate.action_id)
            if definition is None:
                return False, "action_unavailable"
            allowed, reason = rt.rules.allows_action(definition.id)
            if not allowed:
                return False, reason or "rule_blocked"
            if not rt.engine._objects_available(definition):  # noqa: SLF001
                return False, "objects_unavailable"
            if not rt.inventories.can_consume(definition.consumes):
                return False, "requirements_unmet"
            # Phase B: the same restock floor the candidate gate applied — a
            # proposal whose procurement premise went stale is refused by name
            restock_ok, restock_reason = rt.engine.restock_gate(definition)
            if not restock_ok:
                return False, restock_reason
            if not rt.engine._requirements_met(definition):  # noqa: SLF001
                return False, "requirements_unmet"
        return True, ""


class DecisionCoordinator:
    """Owns the gate → request → model → validate → events pipeline."""

    #: the model's answer may not be longer than this in the debug event
    REASON_EXCERPT = 120

    def __init__(self, runtime: Any) -> None:
        self._rt = runtime
        self.gate = DecisionGate(
            allow_llm=bool(getattr(runtime.config, "allow_ai_decisions", True))
        )
        self.validator = DecisionValidator(runtime)
        self.llm_calls = 0
        self.deterministic_calls = 0
        self.fallbacks = 0
        self.rejections = 0

    # ------------------------------------------------------------- entry

    async def decide(
        self,
        *,
        trigger: DecisionTrigger,
        candidates: list[DecisionCandidate],
        correlation_id: str = "",
        causation_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> DecisionOutcome:
        """Run the pipeline; **never** mutates the world (§18)."""
        request = self._request(
            trigger=trigger,
            candidates=candidates,
            correlation_id=correlation_id,
            causation_id=causation_id,
            context=context,
        )
        executable = self.gate.executable(candidates)
        if not executable:
            self.rejections += 1
            self._publish(ET.DECISION_REJECTED, request, reason="no_legal_candidate")
            return DecisionOutcome(
                request_id=request.request_id,
                accepted=False,
                reason="no_legal_candidate",
                correlation_id=correlation_id,
            )
        requested = self._publish(
            ET.DECISION_REQUESTED,
            request,
            reason=trigger.value,
            extra={"candidates": [c.candidate_id for c in executable]},
        )
        if requested is not None:
            # §17: the whole decision hangs off its own request event
            request.causation_id = requested.event_id

        if not self.gate.needs_llm(candidates):
            self.deterministic_calls += 1
            pick = self.deterministic_pick(executable)
            return self._accept(request, pick, source="deterministic")

        proposal = await self._ask_model(request, executable)
        if proposal is None:
            self.fallbacks += 1
            pick = self.deterministic_pick(executable)
            self._publish(
                ET.DECISION_FALLBACK,
                request,
                reason="llm_unavailable_or_invalid",
                extra={"picked": pick.candidate_id if pick else ""},
            )
            return self._accept(request, pick, source="fallback")

        self._publish(
            ET.DECISION_PROPOSED,
            request,
            reason="model_proposal",
            extra={
                "candidate_id": proposal.candidate_id,
                "confidence": round(proposal.confidence, 3),
                "reason": proposal.reason[: self.REASON_EXCERPT],
            },
        )
        ok, why = self.validator.validate(request, proposal)
        if ok:
            candidate = next(
                c for c in request.candidates if c.candidate_id == proposal.candidate_id
            )
            return self._accept(request, candidate, source="llm")
        if why in STALE_REASONS:
            # §16: the world *or* what the character knows moved while the model
            # was thinking — nothing from this request may execute
            self.rejections += 1
            self._publish(ET.DECISION_REJECTED, request, reason=why)
            return DecisionOutcome(
                request_id=request.request_id,
                accepted=False,
                reason=why,
                correlation_id=correlation_id,
            )
        # §11: any other rejected proposal falls back deterministically — never a crash
        self.fallbacks += 1
        pick = self.deterministic_pick(self.gate.executable(request.candidates))
        self._publish(
            ET.DECISION_FALLBACK,
            request,
            reason=why,
            extra={"picked": pick.candidate_id if pick else ""},
        )
        if pick is None:
            self.rejections += 1
            self._publish(ET.DECISION_REJECTED, request, reason=why)
            return DecisionOutcome(
                request_id=request.request_id,
                accepted=False,
                reason=why,
                correlation_id=correlation_id,
            )
        return self._accept(request, pick, source="fallback")

    # ---------------------------------------------------------- internals

    def _request(
        self,
        *,
        trigger: DecisionTrigger,
        candidates: list[DecisionCandidate],
        correlation_id: str = "",
        causation_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> DecisionRequest:
        """Build a request stamped with the live world and cognitive revisions (§20)."""
        rt = self._rt
        return DecisionRequest(
            request_id=f"dec_{uuid.uuid4().hex[:12]}",
            character_id=rt.character_id,
            trigger=trigger,
            context=dict(context or self._context_summary()),
            candidates=list(candidates),
            constraints=self._constraints(),
            deadline=float(rt._clock())  # noqa: SLF001
            + float(getattr(rt.config, "decision_timeout", 20.0)),
            world_revision=rt.world_revision,
            cognitive_revision=rt.cognitive_revision,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    def deterministic_pick(self, candidates: list[DecisionCandidate]) -> DecisionCandidate | None:
        """§11 priority: critical need → candidate priority → stability.

        Candidate priorities are set where the candidates are *built* (a
        core-friend invitation outranks continuing; a routine trigger does
        not), so the sandbox — not the model — decides what is urgent.
        """
        legal = self.gate.executable(candidates)
        if not legal:
            return None
        rt = self._rt
        critical = {need.key for need in rt.needs.critical()}
        if critical:
            for candidate in legal:
                if candidate.kind is not CandidateKind.action:
                    continue
                definition = rt.actions.definitions.get(candidate.action_id)
                if definition is not None and critical & set(definition.need_relief):
                    return candidate
        # highest priority wins; ties prefer keeping the running action, then id
        return sorted(
            legal,
            key=lambda c: (
                -c.priority,
                0 if c.kind is CandidateKind.continue_current else 1,
                c.candidate_id,
            ),
        )[0]

    def _accept(
        self,
        request: DecisionRequest,
        candidate: DecisionCandidate | None,
        *,
        source: str,
    ) -> DecisionOutcome:
        if candidate is None:
            return DecisionOutcome(
                request_id=request.request_id,
                accepted=False,
                reason="no_legal_candidate",
                correlation_id=request.correlation_id,
            )
        self._publish(
            ET.DECISION_ACCEPTED,
            request,
            reason=f"accepted:{source}",
            extra={"candidate_id": candidate.candidate_id, "source": source},
        )
        return DecisionOutcome(
            request_id=request.request_id,
            accepted=True,
            candidate_id=candidate.candidate_id,
            action_id=candidate.action_id,
            kind=candidate.kind.value,
            source=source,
            reason=candidate.reason,
            correlation_id=request.correlation_id,
        )

    def _publish(
        self,
        event_type: ET,
        request: DecisionRequest,
        *,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "request_id": request.request_id,
            "trigger": request.trigger.value,
            "reason": reason,
        }
        payload.update(extra or {})
        return self._rt.events.publish(
            event_type,
            source="character",
            target=request.trigger.value,
            payload=payload,
            causation_id=request.causation_id,
            correlation_id=request.correlation_id,
        )

    def _context_summary(self) -> dict[str, Any]:
        """Small, budgeted context (§24) — Phase 5 budgets are reused."""
        rt = self._rt
        world = rt.context()
        return {
            "world": str(world.get("state_line", "")),
            "mode": str(world.get("mode_line", "")),
            "needs": str(world.get("need_line", "")),
            "goal": str(world.get("goal", "")),
        }

    def _constraints(self) -> list[str]:
        rt = self._rt
        lines = ["只能在给出的候选里选一个，不得发明新选项"]
        boundaries = list(getattr(rt.definition, "boundaries", []) or [])[:3]
        lines.extend(str(boundary)[:40] for boundary in boundaries)
        return lines

    # ----------------------------------------------------------- the model

    async def _ask_model(
        self, request: DecisionRequest, executable: list[DecisionCandidate]
    ) -> IntentProposal | None:
        """One bounded call; any trouble returns None → deterministic fallback."""
        rt = self._rt
        engine = getattr(rt, "ai_engine", None)
        if engine is None or not getattr(engine, "enabled", False):
            return None
        memories = await self._memory_lines()
        prompt = self._build_prompt(request, executable, memories)
        chat_request = AIRequest(
            messages=[ChatMessage.user(prompt)],
            temperature=0.1,
            metadata={"purpose": "sandbox_decision"},
            # 300 会被推理打满 → 空 JSON → 静默退化为规则（2026-10-05 复盘：给推理留位）
            max_tokens=800,
        )
        model = str(getattr(rt.config, "decision_model", "") or "")
        if model:
            chat_request = chat_request.with_model(model)
        self.llm_calls += 1
        try:
            response = await engine.chat(chat_request)
        except Exception:  # noqa: BLE001 - the sandbox must keep running (§28)
            rt._log.info("[Decision] model unavailable, using fallback", exc_info=True)  # noqa: SLF001
            return None
        proposal = self._parse(response.content)
        if proposal is None:
            return None
        # stamp the answered request (one call answers exactly this request);
        # the validator's request_id check then guards against mixups
        proposal.request_id = request.request_id
        if proposal.confidence < float(getattr(rt.config, "decision_min_confidence", 0.35)):
            return None  # §11: low confidence is a failure, not a decision
        return proposal

    async def _memory_lines(self) -> list[str]:
        try:
            payload = (
                await self._rt.cognitive_context(query=" ".join(self._context_summary().values()))
            ).as_prompt_payload()
        except Exception:  # noqa: BLE001 - context is an aid (§33)
            return []
        return [str(item.get("text", "")) for item in payload.get("memories", [])][:3]

    def _build_prompt(
        self,
        request: DecisionRequest,
        executable: list[DecisionCandidate],
        memories: list[str],
    ) -> str:
        context = request.context
        label = getattr(self._rt.definition.identity, "name", "") or "角色"
        lines = [
            f"你是「{label}」的生活决策助手。只有一个任务：",
            "从下面给出的候选里选择一个此刻最合适的，不得发明候选之外的选项，"
            "也不要输出任何解释或多余文字。",
            "",
            f"当前世界：{context.get('world', '')}",
            f"当前状态：{context.get('mode', '')}",
            f"需求：{context.get('needs', '') or '都还好'}",
        ]
        goal = str(context.get("goal", "") or "")
        if goal:
            lines.append(f"手上在做的事：{goal}")
        if memories:
            lines.append("相关记忆（仅供参考，与当前世界冲突时以当前世界为准）：")
            lines.extend(f"- {text}" for text in memories)
        lines.append("")
        lines.append("候选（只能选其中一个 candidate_id）：")
        for index, candidate in enumerate(executable, 1):
            lines.append(f"{index}. candidate_id={candidate.candidate_id} — {candidate.label}")
        if request.constraints:
            lines.append("")
            lines.extend(f"约束：{line}" for line in request.constraints[:3])
        lines.append("")
        lines.append('只输出 JSON：{"candidate_id": "...", "confidence": 0.0}')
        return "\n".join(lines)

    @staticmethod
    def _parse(content: str) -> IntentProposal | None:
        """Strict JSON only (§10): no free-text interpretation, ever."""
        text = (content or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        candidate_id = str(data.get("candidate_id", "") or "")
        if not candidate_id:
            return None
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return IntentProposal(
            candidate_id=candidate_id,
            confidence=max(0.0, min(1.0, confidence)),
        )

    # ------------------------------------------------------ tie-break bridge

    def wrap_tie_breaker(self, raw: Any) -> Any:
        """Route the engine's ambiguity tie-break through this pipeline (§16).

        The wrapped callable publishes the decision events and validates the
        model's pick against live state; ``None`` means "no usable proposal",
        and the engine then falls back to its own deterministic choice.
        """
        if raw is None:
            return None

        async def wrapped(payload: dict[str, Any]) -> Any:
            options = [str(option) for option in payload.get("options", [])]
            candidates = [
                DecisionCandidate(
                    candidate_id=f"action:{action_id}",
                    kind=CandidateKind.action,
                    action_id=action_id,
                    label=action_id,
                    reason="ambiguity_tiebreak",
                )
                for action_id in options
                if action_id in self._rt.actions.definitions
            ]
            if not candidates:
                return None
            request = self._request(
                trigger=DecisionTrigger.conflicting_goals, candidates=candidates
            )
            self._publish(ET.DECISION_REQUESTED, request, reason="ambiguity")
            self.llm_calls += 1
            try:
                proposed = raw(payload)
                if hasattr(proposed, "__await__"):
                    proposed = await proposed
            except Exception:  # noqa: BLE001 - a broken decider never blocks the world
                proposed = None
            action_id = str(getattr(proposed, "action_id", "") or "")
            proposal = IntentProposal(
                request_id=request.request_id, candidate_id=f"action:{action_id}", confidence=1.0
            )
            self._publish(
                ET.DECISION_PROPOSED,
                request,
                reason="tiebreak_proposal",
                extra={"candidate_id": proposal.candidate_id},
            )
            ok, why = self.validator.validate(request, proposal)
            if not ok:
                self.fallbacks += 1
                self._publish(ET.DECISION_FALLBACK, request, reason=why)
                return None
            self._publish(
                ET.DECISION_ACCEPTED,
                request,
                reason="accepted:llm_tiebreak",
                extra={"candidate_id": proposal.candidate_id, "source": "llm"},
            )
            return proposed

        return wrapped
