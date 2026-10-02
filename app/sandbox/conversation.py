"""Conversational Response Runtime (v2.1 Phase 12 §5-§59).

    External message → PersonIdentity → CognitiveContext → proposal
        → validator → ConversationResponse → (the caller's adapter sends it)

Hard boundaries this module keeps (§2/§32/§59):

* **a response is language, not a fact** — ``ConversationResponse.text`` never
  becomes an event, an experience, a memory, a relationship change, a promise
  or a goal.  The only writers of world state stay where they already are;
* the **only** context source is :meth:`SandboxRuntime.cognitive_context` — no
  second context builder, no direct memory/relationship/commitment queries;
* ``person_id`` comes from the platform identity, never from a display name or
  from the text;
* the model's answer is *proposed*: refs must exist in this turn's context,
  an action may only name a world-derived candidate, and a stale answer (the
  world or the cognition moved while it was thinking) is discarded, never sent;
* this module knows nothing about QQ/OneBot — the caller owns transport (§59).
"""

from __future__ import annotations

import json
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.ai.models import AIRequest, ChatMessage
from app.sandbox.events import SandboxEventType as ET

#: §22: four modes, no zoo
CONVERSATION_MODES: tuple[str, ...] = ("reply", "acknowledge", "defer", "silent")
MODE_RANK: dict[str, int] = {"reply": 3, "acknowledge": 2, "defer": 1, "silent": 0}
#: the model's answer is only kept when it is at least this confident (§55 —
#: the threshold is the existing decision one, deliberately not a second knob)
REF_EXCERPT = 80


class ConversationMode(str, Enum):  # noqa: UP042 - pydantic-friendly str enum
    reply = "reply"
    acknowledge = "acknowledge"
    defer = "defer"
    silent = "silent"


class ConversationTurn(BaseModel):
    """One conversational turn *for this call* (§5) — never persisted (§6)."""

    turn_id: str
    character_id: str = ""
    source: str = "qq"
    external_actor_id: str = ""
    #: the Phase 8 stable id, resolved from the platform handle (§5)
    person_id: str = ""
    social_space_id: str = ""
    group_id: str = ""
    message_text: str = ""
    received_at: float = 0.0
    event_id: str = ""
    world_revision: int = 0
    cognitive_revision: int = 0


class ResponseProposal(BaseModel):
    """The model's strict shape (§21) — anything else is not a response."""

    mode: str = "reply"
    text: str = ""
    memory_refs: list[str] = Field(default_factory=list)
    experience_refs: list[str] = Field(default_factory=list)
    #: a *world-derived* candidate id, never a made-up action (§23)
    action_candidate_id: str = ""
    confidence: float = 0.0


class ConversationResponse(BaseModel):
    """What the caller may send (§7) — with the provenance of every claim."""

    turn_id: str
    character_id: str = ""
    person_id: str = ""
    text: str = ""
    mode: str = ConversationMode.silent.value
    memory_refs: list[str] = Field(default_factory=list)
    experience_refs: list[str] = Field(default_factory=list)
    action_candidate_id: str = ""
    source: str = "none"  # llm / fallback / policy / none
    reason: str = ""
    #: refs the model asked for that do not exist in this turn's context (§18)
    dropped_refs: list[str] = Field(default_factory=list)
    world_revision: int = 0
    cognitive_revision: int = 0


class ConversationRuntime:
    """Thin orchestration (§10): receive → resolve → context → propose → validate."""

    #: the prompt never grows past this many characters of context (§14)
    PROMPT_CONTEXT_CHARS = 2400

    def __init__(self, runtime: Any, *, clock: Any) -> None:
        self._rt = runtime
        self._clock = clock
        self.turns = 0
        self.proposals = 0
        self.rejections = 0
        self.fallbacks = 0
        self.llm_calls = 0

    # ---------------------------------------------------------------- entry

    async def respond(
        self,
        *,
        message: str,
        actor_id: str,
        source: str = "qq",
        social_space_id: str = "",
        group_id: str = "",
        event_id: str = "",
        correlation_id: str = "",
        mode_ceiling: str = "reply",
        action_candidates: list[str] | None = None,
        event: Any = None,
    ) -> ConversationResponse:
        """Produce this turn's response. Read-only: no world state is touched."""
        rt = self._rt
        person = rt.persons.for_qq(str(actor_id))  # §5: identity, never a guess
        turn = ConversationTurn(
            turn_id=f"turn_{uuid.uuid4().hex[:12]}",
            character_id=rt.character_id,
            source=source,
            external_actor_id=str(actor_id),
            person_id=person.person_id,
            social_space_id=str(social_space_id or ""),
            group_id=str(group_id or ""),
            message_text=str(message or ""),
            received_at=float(self._clock()),
            event_id=str(event_id or ""),
            world_revision=int(rt.world_revision),
            cognitive_revision=int(rt.cognitive_revision),
        )
        self.turns += 1
        ceiling = MODE_RANK.get(str(mode_ceiling), 0)
        if ceiling <= 0:
            # §36: whether this turn may speak at all is a *runtime policy* the
            # caller already decided (influence phase) — never the model's idea
            return self._silent(turn, source="policy", reason="response_not_allowed")

        context = await rt.cognitive_context(
            query=turn.message_text, relationship_target=str(actor_id)
        )
        allowed_candidates = [str(item) for item in (action_candidates or []) if item]
        proposal = await self._ask_model(turn, context, ceiling, allowed_candidates)
        if proposal is None:
            self.fallbacks += 1
            return self._silent(turn, source="fallback", reason="llm_unavailable_or_invalid")

        self.proposals += 1
        self._publish(
            ET.CONVERSATION_RESPONSE_PROPOSED,
            turn,
            payload={
                "mode": str(proposal.mode),
                "source": "llm",
                "memory_refs": list(proposal.memory_refs)[:8],
                "experience_refs": list(proposal.experience_refs)[:8],
                "action_candidate_id": str(proposal.action_candidate_id),
                "confidence": round(float(proposal.confidence), 3),
                "text_excerpt": str(proposal.text)[:REF_EXCERPT],
            },
            correlation_id=correlation_id,
        )
        response = self._validate(turn, proposal, context, ceiling, allowed_candidates)
        self._publish(
            ET.CONVERSATION_RESPONSE_EMITTED,
            turn,
            payload={
                "mode": response.mode,
                "source": response.source,
                "reason": response.reason,
                "memory_refs": list(response.memory_refs),
                "experience_refs": list(response.experience_refs),
                "action_candidate_id": response.action_candidate_id,
                "dropped_refs": list(response.dropped_refs),
                "text_excerpt": str(response.text)[:REF_EXCERPT],
            },
            correlation_id=correlation_id,
        )
        return response

    # ------------------------------------------------------------- the model

    async def _ask_model(
        self,
        turn: ConversationTurn,
        context: Any,
        ceiling: int,
        action_candidates: list[str],
    ) -> ResponseProposal | None:
        """One bounded call; any trouble returns None → the caller stays silent."""
        rt = self._rt
        engine = getattr(rt, "ai_engine", None)
        if engine is None or not getattr(engine, "enabled", False):
            return None
        prompt = self._build_prompt(turn, context, ceiling, action_candidates)
        request = AIRequest(
            messages=[ChatMessage.user(prompt)],
            temperature=0.6,
            metadata={"purpose": "conversation_response", "turn_id": turn.turn_id},
            max_tokens=int(getattr(rt.config, "conversation_max_response_chars", 400)) + 80,
        )
        model = str(getattr(rt.config, "conversation_model", "") or "")
        if model:
            request = request.with_model(model)
        self.llm_calls += 1
        try:
            answer = await engine.chat(request)
        except Exception:  # noqa: BLE001 - a chat failure must never break the world (§53)
            rt._log.info("[Conversation] model unavailable", exc_info=True)  # noqa: SLF001
            return None
        return self._parse(getattr(answer, "content", ""))

    def _build_prompt(
        self,
        turn: ConversationTurn,
        context: Any,
        ceiling: int,
        action_candidates: list[str],
    ) -> str:
        """§14/§38: the current world, the social situation, and the message."""
        payload = context.as_prompt_payload()
        situation = payload.get("social_situation") or {}
        context_blob = {
            "current_world": {
                "line": payload.get("world_line", ""),
                "action": context.current_action,
                "location": context.current_location,
            },
            "person": payload.get("person", {}),
            "social_situation": {
                "relationship": situation.get("relationship", {}),
                "open_commitments": situation.get("open_commitments", []),
                "recent_shared_experiences": situation.get("recent_shared_experiences", []),
                "relevant_shared_memories": situation.get("relevant_shared_memories", []),
            },
            "memories": [
                {"memory_id": row.get("memory_id"), "text": row.get("text", "")}
                for row in payload.get("memories", [])
            ],
            "experiences": [
                {"id": row.get("id"), "text": row.get("text", "")}
                for row in payload.get("experiences", [])
            ],
            "allowed_modes": [mode for mode in CONVERSATION_MODES if MODE_RANK[mode] <= ceiling],
            "action_candidates": action_candidates,
        }
        blob = json.dumps(context_blob, ensure_ascii=False)[: self.PROMPT_CONTEXT_CHARS]
        name = getattr(getattr(rt_definition(self._rt), "identity", None), "name", "") or "角色"
        return (
            f"你正在代表「{name}」生成当前这一轮的聊天回复。\n"
            "你只能使用下面提供的：当前世界、当前社会上下文、已存在记忆、已存在共同经历。\n"
            "不要创造过去发生过的事件；不要创造不存在的约定；不要创造不存在的人；\n"
            "不要把记忆当成当前世界；不要宣称任何尚未验证的行动已经发生；\n"
            "如果不知道，就不要编造。\n"
            "只输出一个 JSON 对象，不要任何解释或多余文字，格式：\n"
            '{"mode": "reply|acknowledge|defer|silent", "text": "...", '
            '"memory_refs": [], "experience_refs": [], '
            '"action_candidate_id": "", "confidence": 0.0}\n'
            "memory_refs / experience_refs 只能引用上面出现过的 id；"
            "action_candidate_id 只能取 action_candidates 里的值，否则留空。\n\n"
            f"对方的消息：{turn.message_text}\n"
            f"当前上下文：{blob}"
        )

    @staticmethod
    def _parse(raw: str) -> ResponseProposal | None:
        """Strict JSON only (§21/§54): a broken answer is not a response."""
        text = str(raw or "")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return ResponseProposal.model_validate(data)
        except Exception:  # noqa: BLE001 - an unshaped dict is simply not a proposal
            return None

    # ------------------------------------------------------------ validation

    def _validate(
        self,
        turn: ConversationTurn,
        proposal: ResponseProposal,
        context: Any,
        ceiling: int,
        action_candidates: list[str],
    ) -> ConversationResponse:
        """§25-§27/§51-§57: nothing leaves this method unverified."""
        rt = self._rt
        if rt.world_revision != turn.world_revision:
            return self._reject(turn, "world_changed")  # §26: the world moved
        if rt.cognitive_revision != turn.cognitive_revision:
            return self._reject(turn, "cognitive_changed")  # §27: what she knows moved
        mode = str(proposal.mode or "").strip().lower()
        if mode not in CONVERSATION_MODES or MODE_RANK[mode] > ceiling:
            return self._reject(turn, "mode_not_allowed")
        text = str(proposal.text or "").strip()
        limit = int(getattr(rt.config, "conversation_max_response_chars", 400))
        if mode != ConversationMode.silent.value:
            if not text:  # §56: never send an empty message
                return self._reject(turn, "empty_text")
            if len(text) > limit:  # §57: too long is rejected, never truncated
                return self._reject(turn, "too_long")
        known_memories = {
            str(row.get("memory_id"))
            for row in list(getattr(context, "relevant_memories", []) or [])
        } | {
            str(item.get("memory_id"))
            for item in (context.social_situation.get("relevant_shared_memories") or [])
        }
        known_experiences = (
            {str(row.get("id")) for row in list(getattr(context, "recent_experiences", []) or [])}
            | {
                str(item.get("experience_id"))
                for item in (context.social_situation.get("recent_shared_experiences") or [])
            }
            | {
                str(item.get("episode_key"))
                for item in (context.social_situation.get("recent_shared_experiences") or [])
            }
        )
        memory_refs, dropped = _keep_known(proposal.memory_refs, known_memories)
        experience_refs, dropped_experiences = _keep_known(
            proposal.experience_refs, known_experiences
        )
        dropped.extend(dropped_experiences)
        candidate = str(proposal.action_candidate_id or "")
        if candidate and candidate not in set(action_candidates):
            dropped.append(f"action:{candidate}")  # §52: never invent an action
            candidate = ""
        confidence = float(proposal.confidence or 0.0)
        floor = float(getattr(rt.config, "decision_min_confidence", 0.35))
        if mode != ConversationMode.silent.value and confidence < floor:
            return self._reject(turn, "low_confidence")  # §55
        return ConversationResponse(
            turn_id=turn.turn_id,
            character_id=turn.character_id,
            person_id=turn.person_id,
            text=text,
            mode=mode,
            memory_refs=memory_refs,
            experience_refs=experience_refs,
            action_candidate_id=candidate,
            source="llm",
            reason="refs_stripped" if dropped else "",
            dropped_refs=dropped,
            world_revision=turn.world_revision,
            cognitive_revision=turn.cognitive_revision,
        )

    # -------------------------------------------------------------- plumbing

    def _silent(self, turn: ConversationTurn, *, source: str, reason: str) -> ConversationResponse:
        return ConversationResponse(
            turn_id=turn.turn_id,
            character_id=turn.character_id,
            person_id=turn.person_id,
            mode=ConversationMode.silent.value,
            source=source,
            reason=reason,
            world_revision=turn.world_revision,
            cognitive_revision=turn.cognitive_revision,
        )

    def _reject(self, turn: ConversationTurn, reason: str) -> ConversationResponse:
        self.rejections += 1
        self.fallbacks += 1
        return self._silent(turn, source="fallback", reason=reason)

    def _publish(
        self,
        event_type: ET,
        turn: ConversationTurn,
        *,
        payload: dict[str, Any],
        correlation_id: str = "",
    ) -> Any:
        """Trace-only events (§33/§34): no revisions move, no text is stored."""
        data = {"turn_id": turn.turn_id, "person_id": turn.person_id, **payload}
        return self._rt.events.publish(
            event_type,
            source=turn.source,
            target=turn.external_actor_id,
            payload=data,
            correlation_id=correlation_id or turn.event_id,
        )


def rt_definition(runtime: Any) -> Any:
    """The character definition behind the prompt label (§39, never hardcoded)."""
    return getattr(runtime, "definition", None)


def _keep_known(refs: list[str], known: set[str]) -> tuple[list[str], list[str]]:
    """§18/§19: only references that exist in *this* turn's context survive."""
    kept: list[str] = []
    dropped: list[str] = []
    for ref in refs or []:
        value = str(ref)
        if value in known:
            kept.append(value)
        elif value:
            dropped.append(value)
    return kept, dropped
