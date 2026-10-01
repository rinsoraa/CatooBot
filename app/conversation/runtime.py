"""ConversationTurnRuntime: messages become turns, turns become one response.

Core structure (v1.2 §7-§22, §164-§167):

* per-session buffer + dynamic debounce (a burst is one turn, never N replies)
* rule-based classification at flush time
* per-session worker — at most ONE generation in flight per session
* generation ids + staleness: a correction/interruption arriving mid-flight
  marks the in-flight reply stale; it is never sent
* response callback is injected (adapter pattern): this package knows nothing
  about the character runtime, the AI engine or QQ.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.conversation.classifier import classify
from app.conversation.models import (
    ConversationTurn,
    TurnClassification,
    TurnMessage,
    TurnStatus,
)
from app.utils.narrator import narrate

Logger = logging.Logger

# Messages that classify as a mid-flight cancel get flushed almost instantly.
_CANCEL_FLUSH_SECONDS = 0.15


@dataclass
class Generation:
    """One response generation attempt (v1.2 §16)."""

    id: int
    turn: ConversationTurn
    stale: bool = False
    delivered: bool = False

    def is_current(self) -> bool:
        return not self.stale and not self.delivered


@dataclass
class _Session:
    session_id: str
    is_group: bool = False
    open_turn: ConversationTurn | None = None
    flush_task: asyncio.Task | None = None
    worker_task: asyncio.Task | None = None
    queue: asyncio.Queue[ConversationTurn | None] = field(default_factory=asyncio.Queue)
    active_generation: Generation | None = None
    processing: bool = False  # worker picked a turn, decision in flight
    last_turn_text: str = ""
    last_generation_id: int = 0
    consecutive_questions: int = 0  # bot asked; user replied without new question
    momentum: float = 0.5  # structured input, never a dice (v1.2 §66/§67)
    last_activity: float = 0.0


SubmitPayload = dict[str, Any]
RespondCallback = Callable[[ConversationTurn, Any, Generation], Awaitable[Any]]
DeliverCallback = Callable[[Any, ConversationTurn, Callable[[], bool]], Awaitable[Any]]
PostReplyCallback = Callable[[ConversationTurn, Any, str], Awaitable[None]]
TurnFinishedCallback = Callable[[ConversationTurn], Awaitable[None]]
SocialProvider = Callable[[ConversationTurn], Awaitable[Any]]


class ConversationTurnRuntime:
    def __init__(
        self,
        config: Any,
        decision_engine: Any,
        *,
        logger: Logger | None = None,
        clock: Any = time.time,
        respond_callback: RespondCallback | None = None,
        deliver_callback: DeliverCallback | None = None,
        post_reply_callback: PostReplyCallback | None = None,
        turn_finished_callback: TurnFinishedCallback | None = None,
        social_provider: SocialProvider | None = None,
    ) -> None:
        self.config = config
        self.decision_engine = decision_engine
        self._log = logger or logging.getLogger("CatooBot.Conversation")
        self._clock = clock
        self._respond_callback = respond_callback
        self._deliver_callback = deliver_callback
        self._post_reply_callback = post_reply_callback
        self._turn_finished = turn_finished_callback
        self._social_provider = social_provider
        self._sessions: dict[str, _Session] = {}
        self._counter = 0

    # ------------------------------------------------------------------ api

    def bind(
        self,
        *,
        respond: RespondCallback | None = None,
        deliver: DeliverCallback | None = None,
        post_reply: PostReplyCallback | None = None,
        turn_finished: TurnFinishedCallback | None = None,
        social_provider: SocialProvider | None = None,
    ) -> None:
        """Attach the adapter callbacks (chat plugin wires the real pipeline)."""
        if respond is not None:
            self._respond_callback = respond
        if deliver is not None:
            self._deliver_callback = deliver
        if post_reply is not None:
            self._post_reply_callback = post_reply
        if turn_finished is not None:
            self._turn_finished = turn_finished
        if social_provider is not None:
            self._social_provider = social_provider

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True))

    async def wait_idle(self, timeout: float = 10.0) -> None:
        """Wait until no session is buffering, queued or generating (tests/WebUI)."""

        async def _idle() -> None:
            while True:
                if self._busy():
                    await asyncio.sleep(0.02)
                    continue
                pending_flush = [
                    session.flush_task
                    for session in self._sessions.values()
                    if session.flush_task is not None and not session.flush_task.done()
                ]
                if not pending_flush:
                    return
                await asyncio.sleep(0.02)

        await asyncio.wait_for(_idle(), timeout)

    def _busy(self) -> bool:
        return any(
            session.open_turn is not None
            or session.active_generation is not None
            or session.processing
            or not session.queue.empty()
            for session in self._sessions.values()
        )

    def session_snapshot(self) -> list[dict[str, Any]]:
        """WebUI dashboard facts (v1.2 §132) — structured, no internals leaked."""
        out = []
        for session in self._sessions.values():
            gen = session.active_generation
            out.append(
                {
                    "session_id": session.session_id,
                    "is_group": session.is_group,
                    "buffered_messages": (
                        len(session.open_turn.messages) if session.open_turn else 0
                    ),
                    "queued_turns": session.queue.qsize(),
                    "active_generation": gen.id if gen else None,
                    "generation_stale": gen.stale if gen else False,
                    "momentum": round(session.momentum, 3),
                    "consecutive_questions": session.consecutive_questions,
                }
            )
        return out

    # --------------------------------------------------------------- submit

    async def submit(self, payload: SubmitPayload) -> None:
        """Buffer one incoming message; a flush produces at most one response."""
        session = self._session(payload["session_id"], is_group=payload.get("is_group", False))
        message = TurnMessage(
            message_id=str(payload.get("message_id", "") or ""),
            user_id=str(payload["user_id"]),
            nickname=str(payload.get("nickname", "") or ""),
            text=str(payload.get("text", "") or ""),
            mentioned=bool(payload.get("mentioned", False)),
            reply_to_bot=bool(payload.get("reply_to_bot", False)),
            media_count=int(payload.get("media_count", 0) or 0),
            created_at=float(self._clock()),
        )

        direct = message.mentioned or message.reply_to_bot
        cancels = self._cancels_in_flight(session, message)
        if cancels and session.active_generation is not None:
            stale = session.active_generation
            stale.stale = True
            stale.turn.status = TurnStatus.stale
            narrate().quiet(
                "旧回复作废",
                detail=f"generation #{stale.id} · 用户改口/打断了",
            )
            self._log.info(
                "[Conversation] generation #%d marked stale by user correction (session=%s)",
                stale.id,
                session.session_id,
            )

        if session.open_turn is None:
            session.open_turn = ConversationTurn(
                turn_id=self._next_turn_id(),
                session_id=session.session_id,
                user_id=message.user_id,
                nickname=message.nickname,
                group_id=payload.get("group_id"),
                status=TurnStatus.open,
            )
        session.open_turn.messages.append(message)
        session.open_turn.media_count += message.media_count
        extras = payload.get("meta")
        if isinstance(extras, dict):
            session.open_turn.meta.update(extras)
        session.last_activity = float(self._clock())

        # Direct address and mid-flight corrections flush almost immediately —
        # making someone wait a debounce window after "@" feels broken.
        if direct or cancels:
            await self._flush(session)
        else:
            self._schedule_flush(session)

    # -------------------------------------------------------------- buffer

    def _debounce_seconds(self, session: _Session) -> float:
        """Dynamic window (v1.2 §12): never a fixed wait for its own sake."""
        cfg = self.config.debounce
        base_ms = cfg.group_message_ms if session.is_group else cfg.direct_message_ms
        delay = base_ms / 1000.0
        turn = session.open_turn
        if turn and turn.messages:
            last = turn.messages[-1].text
            if len(last) >= 30:
                delay *= 0.6  # long message: user is probably done typing
            elif len(last) <= 4:
                delay *= 1.4  # "在吗" — give them a beat to continue
        return max(0.05, delay)

    def _schedule_flush(self, session: _Session) -> None:
        if session.flush_task is not None:
            session.flush_task.cancel()
        delay = self._debounce_seconds(session)
        loop = asyncio.get_running_loop()
        session.flush_task = loop.create_task(self._flush_later(session, delay))

    async def _flush_later(self, session: _Session, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self._flush(session)

    async def _flush(self, session: _Session) -> None:
        turn = session.open_turn
        if turn is None or not turn.messages:
            return
        session.open_turn = None
        if session.flush_task is not None:
            session.flush_task.cancel()
            session.flush_task = None

        turn.classification = classify(turn, previous_turn_text=session.last_turn_text)
        turn.status = TurnStatus.pending
        turn.ended(self._clock)
        session.queue.put_nowait(turn)
        if session.worker_task is None or session.worker_task.done():
            session.worker_task = asyncio.create_task(self._worker(session))

    # -------------------------------------------------------------- worker

    async def _worker(self, session: _Session) -> None:
        try:
            while True:
                turn = await session.queue.get()
                if turn is None:
                    return
                session.processing = True
                try:
                    await self._process_turn(session, turn)
                except asyncio.CancelledError:
                    session.processing = False
                    raise
                except Exception:  # noqa: BLE001 - runtime must never kill the bus
                    self._log.exception("[Conversation] turn %s failed", turn.turn_id)
                    turn.status = TurnStatus.error
                    session.processing = False
                    await self._finish_turn(turn)
                else:
                    session.processing = False
        except asyncio.CancelledError:
            raise

    async def _process_turn(self, session: _Session, turn: ConversationTurn) -> None:
        # Group participation is decided once per turn, on the merged burst
        # (v1.2 §82/§83) — by the v0.9 Social Cognition via the injected provider.
        social_decision = None
        if self._social_provider is not None:
            try:
                social_decision = await self._social_provider(turn)
            except Exception:  # noqa: BLE001
                self._log.exception("[Conversation] social provider failed")

        decision = await self.decision_engine.decide(
            turn,
            previous_turn_text=session.last_turn_text,
            momentum=session.momentum,
            consecutive_questions=session.consecutive_questions,
            social_decision=social_decision,
            hard_block=turn.meta.get("hard_block"),
        )
        turn.topic = decision.intent
        if not decision.respond:
            turn.status = TurnStatus.silent
            turn.silence_reason = decision.silence_reason or "low_conversational_value"
            narrate().quiet(
                "这轮先不接",
                detail=str(turn.meta.get("silence_text") or turn.silence_reason),
            )
            self._log.info(
                "[Conversation] turn silent (%s) session=%s",
                turn.silence_reason,
                session.session_id,
            )
            await self._finish_turn(turn)
            return
        narrate().judge(
            "这一轮要回",
            detail=f"{turn.classification.value} · {len(turn.messages)} 条"
            + (f" · {decision.intent}" if decision.intent != "chat" else ""),
        )

        session.last_generation_id += 1
        generation = Generation(id=session.last_generation_id, turn=turn)
        session.active_generation = generation
        turn.status = TurnStatus.generating
        turn.generation_id = generation.id

        plan = None
        try:
            if self._respond_callback is not None:
                plan = await self._respond_callback(turn, decision, generation)
        except Exception:  # noqa: BLE001 - a failed turn must not kill the worker
            self._log.exception("[Conversation] response generation failed")
            turn.status = TurnStatus.error
            session.active_generation = None
            await self._finish_turn(turn)
            return

        if generation.stale or plan is None:
            turn.status = TurnStatus.stale if generation.stale else TurnStatus.cancelled
            narrate().quiet("这轮的回复已过期，不发", detail=f"generation #{generation.id}")
            self._log.info(
                "[Conversation] dropped stale generation #%d (session=%s)",
                generation.id,
                session.session_id,
            )
            session.active_generation = None
            await self._finish_turn(turn)
            return

        turn.status = TurnStatus.delivered
        sent_texts: list[str] = []
        if self._deliver_callback is not None:
            result = await self._deliver_callback(plan, turn, generation.is_current)
            if isinstance(result, list):
                sent_texts = [str(item) for item in result]
        generation.delivered = True
        session.active_generation = None

        # Momentum is a structured signal (v1.2 §66): bursts and questions raise it,
        # low-info replies drain it. It feeds decisions — it never rolls dice.
        burst = len(turn.messages)
        low_info = len(turn.text) <= 4
        session.momentum = min(1.0, session.momentum + 0.1 * burst - (0.25 if low_info else 0.0))
        session.momentum = max(0.0, session.momentum)
        if decision.should_ask:
            session.consecutive_questions += 1
        else:
            session.consecutive_questions = 0
        session.last_turn_text = turn.text

        if self._post_reply_callback is not None:
            try:
                await self._post_reply_callback(turn, decision, "\n".join(sent_texts))
            except Exception:  # noqa: BLE001
                self._log.exception("[Conversation] post-reply update failed")
        await self._finish_turn(turn)

    async def _finish_turn(self, turn: ConversationTurn) -> None:
        if turn.ended_at is None:
            turn.ended(self._clock)
        if self._turn_finished is not None:
            try:
                await self._turn_finished(turn)
            except Exception:  # noqa: BLE001
                self._log.exception("[Conversation] turn persistence failed")

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _cancels_in_flight(session: _Session, message: TurnMessage) -> bool:
        """True when this message alone is a correction / interruption (v1.2 §15)."""
        if session.active_generation is None:
            return False
        probe = ConversationTurn(
            turn_id="probe",
            session_id=session.session_id,
            user_id=message.user_id,
            messages=[message],
        )
        kind = classify(probe)
        return kind in (TurnClassification.correction, TurnClassification.interruption)

    def _next_turn_id(self) -> str:
        self._counter += 1
        return f"turn_{int(self._clock())}_{self._counter:04d}"

    def _session(self, session_id: str, *, is_group: bool) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            session = _Session(session_id=session_id, is_group=is_group)
            self._sessions[session_id] = session
        return session

    async def shutdown(self) -> None:
        workers = []
        for session in self._sessions.values():
            if session.flush_task is not None:
                session.flush_task.cancel()
            if session.worker_task is not None and not session.worker_task.done():
                session.queue.put_nowait(None)
                workers.append(session.worker_task)
        if workers:
            # Drain in-flight turns so no background DB write survives shutdown.
            await asyncio.gather(*workers, return_exceptions=True)
