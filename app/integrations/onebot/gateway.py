"""Real external runtime gateway (Phase 13 §1-§66).

    transport event → normalize → dedupe → self-guard → lane (FIFO per social space)
        → Sandbox ingest (ExternalWorldEvent) → ConversationRuntime
        → Response Commit Guard → outbound queue → transport send

Boundaries this module keeps (§2/§3/§59):

* it is *outside* the sandbox: no sandbox module imports transport code, and
  this module never touches relationship/memory/commitment/goal state — it only
  feeds the existing sandbox entries and speaks what the sandbox produced;
* the sandbox is the world's authority: every message becomes an
  ``ExternalWorldEvent`` and flows through the existing influence pipeline;
* the only text that may leave is a **fresh, committed** ``ConversationResponse``
  (Phase 12.1) — retries resend that same response, never a new turn;
* delivery is honestly best-effort (§66): at-least-once inbound with a bounded
  dedupe cache, best-effort outbound with bounded retries and duplicate
  suppression. No exactly-once claim, no message database.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.integrations.onebot.normalize import (
    NormalizedMessageEvent,
    normalize_message_event,
    normalize_typed_event,
)
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.external_adapters import adapt_qq_message


class ConnectionState(str, Enum):  # noqa: UP042 - §49, deliberately small
    connecting = "connecting"
    connected = "connected"
    disconnected = "disconnected"
    reconnecting = "reconnecting"
    stopped = "stopped"


class _DedupeCache:
    """Bounded TTL cache for transport event identities (§15-§18).

    Restart empties it — Phase 13 does not pretend to be permanently
    idempotent across processes; a transport that replays history must be
    handled in the transport configuration, not silently assumed away.
    """

    def __init__(self, *, ttl: float, max_size: int) -> None:
        self.ttl = float(ttl)
        self.max_size = int(max_size)
        self._entries: deque[tuple[str, float]] = deque()
        self._seen: set[str] = set()
        self.hits = 0

    def remember(self, key: str, *, now: float) -> bool:
        """True when ``key`` was already seen (duplicate); records otherwise."""
        self._evict(now)
        if key in self._seen:
            self.hits += 1
            return True
        self._seen.add(key)
        self._entries.append((key, float(now)))
        while len(self._entries) > self.max_size:
            old_key, _stamp = self._entries.popleft()
            self._seen.discard(old_key)
        return False

    def _evict(self, now: float) -> None:
        while self._entries and now - self._entries[0][1] > self.ttl:
            old_key, _stamp = self._entries.popleft()
            self._seen.discard(old_key)

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class _LaneItem:
    """One inbound message waiting for its turn in a lane."""

    event: NormalizedMessageEvent
    #: False = the overflow degraded this turn to a fact-only ingest (§3)
    respond: bool = True
    started: bool = False
    degraded: bool = False


class _Lane:
    """FIFO lane for one social space (§21-§24): ordered in, ordered out."""

    def __init__(self, key: str, *, max_pending: int, gateway: Any) -> None:
        self.key = key
        self.max_pending = int(max_pending)
        self._gateway = gateway
        self._pending: deque[_LaneItem] = deque()
        self._wakeup = asyncio.Event()
        self._worker: asyncio.Task[Any] | None = None
        self.processed = 0
        self.dropped = 0
        self.degraded = 0
        self.busy = False

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
            pass
        self._worker = None

    @property
    def pending(self) -> int:
        return len(self._pending)

    def submit(self, item: _LaneItem) -> _LaneItem | None:
        """Queue one item; an overflow degrades the oldest turn, in place (§4).

        The degraded item is *not* removed from the lane: it keeps its
        position and is still ingested in order — only its response is
        cancelled. Dropping it out of the lane would let a later message
        reach the sandbox first, which §1/§10 forbid.
        """
        degraded: _LaneItem | None = None
        while len(self._pending) >= self.max_pending:
            candidate = next(
                (entry for entry in self._pending if not entry.started and entry.respond),
                None,
            )
            if candidate is None:
                # nothing conversational left to trade away: the remaining
                # items are cheap fact-only ingests and stay ordered
                break
            candidate.respond = False
            candidate.degraded = True
            degraded = candidate
            self.dropped += 1
        self._pending.append(item)
        self._wakeup.set()
        return degraded

    async def _run(self) -> None:
        while True:
            if not self._pending:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            item = self._pending.popleft()
            item.started = True
            self.busy = True
            try:
                await self._gateway._handle_lane_item(item)  # noqa: SLF001 - same package
                if not item.respond:
                    self.degraded += 1
                self.processed += 1
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad turn never stops the lane (§62)
                self._gateway._log.exception(  # noqa: SLF001
                    "[OneBot] lane item failed (lane=%s)", self.key
                )
            finally:
                self.busy = False


@dataclass
class OutboundItem:
    """A committed, fresh response plus where it must go (§44)."""

    turn_id: str
    text: str
    mode: str
    message_type: str
    user_id: str = ""
    group_id: str = ""
    attempts: int = 0
    delivered: bool = False
    trace: dict[str, Any] = field(default_factory=dict)


class OneBotGateway:
    """Transport → sandbox → response → transport, with honest delivery semantics."""

    #: §50: bounded exponential backoff for reconnects (deterministic sequence)
    BACKOFF_SEQUENCE = (1.0, 2.0, 4.0, 8.0, 16.0)

    def __init__(  # noqa: PLR0913 - one gateway, explicit wiring
        self,
        runtime: Any,
        *,
        transport: Any,
        config: Any,
        clock: Any = time.time,
        logger: Any = None,
        retry_delay: float = 0.05,
    ) -> None:
        import logging

        self.runtime = runtime
        self.transport = transport
        self.config = config
        self._clock = clock
        self._log = logger or getattr(runtime, "_log", None) or logging.getLogger("CatooBot.OneBot")
        self.state = ConnectionState.stopped
        self.dedupe = _DedupeCache(
            ttl=float(getattr(config, "dedupe_ttl", 600.0)),
            max_size=int(getattr(config, "dedupe_max_size", 2048)),
        )
        self._lanes: dict[str, _Lane] = {}
        self._outbound: deque[OutboundItem] = deque()
        self._outbound_wakeup = asyncio.Event()
        self._outbound_worker: asyncio.Task[Any] | None = None
        self._in_flight: set[asyncio.Task[Any]] = set()
        self._sent_turns: deque[str] = deque(maxlen=512)  # §48: bounded memory
        self._retry_delay = float(retry_delay)
        self.received = 0
        self.accepted = 0
        self.deduped = 0
        self.dropped = 0
        self.self_ignored = 0
        self.responses = 0
        self.sent = 0
        self.failed = 0

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Connect the transport and start the outbound worker (§49)."""
        self.state = ConnectionState.connecting
        self._outbound_worker = asyncio.create_task(self._outbound_loop())
        set_lifecycle = getattr(self.transport, "set_lifecycle", None)
        if callable(set_lifecycle):
            # §22: the transport reports the socket's fate; the gateway owns state
            set_lifecycle(
                on_connected=self.on_transport_connected,
                on_disconnected=self.on_transport_disconnected,
            )
        await self.transport.start(self.handle_transport_event)
        if getattr(self.transport, "connected", True):
            self.state = ConnectionState.connected
        self._log.info(
            "[OneBot] gateway started (self_ids=%s)",
            list(getattr(self.config, "self_ids", []) or []) or ["<unset>"],
        )

    async def stop(self, *, timeout: float | None = None) -> None:
        """Graceful stop with a bounded budget (Phase 13.1 §11-§19).

        Stop accepting new inbound work, let already-started turns and already
        committed deliveries finish (bounded by ``shutdown_timeout``), then
        cancel whatever is left — never hang, never leave orphans.
        """
        was = self.state
        self.state = ConnectionState.stopped
        budget = float(
            timeout if timeout is not None else getattr(self.config, "shutdown_timeout", 5.0)
        )
        drained = await self.drain(timeout=budget)
        for lane in list(self._lanes.values()):
            await lane.stop()
        self._lanes.clear()
        if self._outbound_worker is not None:
            self._outbound_worker.cancel()
            try:
                await self._outbound_worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
                pass
            self._outbound_worker = None
        for task in list(self._in_flight):
            task.cancel()
        self._in_flight.clear()
        await self.transport.stop()
        self._log.info("[OneBot] gateway stopped (was %s, drained=%s)", was.value, drained)

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)
        return task

    def backoff_delays(self) -> list[float]:
        """§50: the reconnect ladder, capped by configuration."""
        cap = float(getattr(self.config, "reconnect_max_seconds", 30.0))
        return [min(delay, cap) for delay in self.BACKOFF_SEQUENCE]

    async def handle_disconnect(self) -> None:
        """Report a dropped socket (kept for callers that poll transport state)."""
        self.on_transport_disconnected()

    # ------------------------------------------------- connection lifecycle

    def on_transport_connected(self) -> None:
        """A client (NapCat) connected again — resume serving (§23)."""
        if self.state is ConnectionState.stopped:
            return
        self.state = ConnectionState.connected
        self._log.info("[OneBot] transport connected")

    def on_transport_disconnected(self) -> None:
        """The socket dropped: transport state only, never a world fact (§27)."""
        if self.state is ConnectionState.stopped:
            return
        self.state = ConnectionState.disconnected
        self._log.warning("[OneBot] transport disconnected (waiting for the client)")
        task = asyncio.create_task(self._recover_connection())
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)

    async def _recover_connection(self) -> None:
        """§26: bounded recovery *debounce* — never a client dial.

        In reverse-WebSocket mode NapCat reconnects to us; the ladder only
        keeps the gateway from flapping while the socket comes back.
        """
        for delay in self.backoff_delays():
            if self.state in (ConnectionState.connected, ConnectionState.stopped):
                return
            if getattr(self.transport, "can_dial", False):
                self.state = ConnectionState.reconnecting
                try:
                    await self.transport.reconnect(self.handle_transport_event)
                except Exception:  # noqa: BLE001 - keep waiting, bounded by the ladder
                    self._log.warning("[OneBot] reconnect attempt failed", exc_info=True)
                    continue
                if getattr(self.transport, "connected", False):
                    self.on_transport_connected()
                    return
                continue
            await asyncio.sleep(delay)  # passive: the client dials us back

    # --------------------------------------------------------------- inbound

    async def handle_transport_event(self, payload: Any) -> dict[str, Any]:
        """Entry point from the transport: raw dict or normalized event."""
        if self.state is ConnectionState.stopped:
            return {"accepted": False, "reason": "stopped"}
        self.received += 1
        event = self._normalize(payload)
        if event is None:
            self.dropped += 1
            self._trace(ET.EXTERNAL_TRANSPORT_DROPPED, {}, reason="malformed")  # §92
            return {"accepted": False, "reason": "malformed"}
        self._trace(
            ET.EXTERNAL_TRANSPORT_RECEIVED,
            {
                "transport_event_id": event.transport_event_id,
                "ingest_seq": event.ingest_seq,
                "message_type": event.message_type,
                "group_id": event.group_id,
            },
            target=event.user_id,
        )
        self_ids = tuple(str(item) for item in (getattr(self.config, "self_ids", []) or []))
        if event.user_id == event.self_id or (
            self_ids and event.self_id and event.self_id not in self_ids
        ):
            # §19/§20/§54: never answer ourselves, never serve an unknown account
            self.self_ignored += 1
            self.dropped += 1
            reason = "self_message" if event.user_id == event.self_id else "unknown_self_id"
            self._trace(ET.EXTERNAL_TRANSPORT_DROPPED, self._ids(event), reason=reason)
            return {"accepted": False, "reason": reason}
        key = f"{event.self_id}:{event.transport_event_id}"
        if self.dedupe.remember(key, now=float(self._clock())):  # §16 dedupe scope
            self.deduped += 1
            self._trace(ET.EXTERNAL_TRANSPORT_DEDUPED, self._ids(event), reason="duplicate")
            return {"accepted": False, "reason": "duplicate"}

        lane = self._lane_for(event.lane_id)
        degraded = lane.submit(_LaneItem(event=event))
        if degraded is not None:
            # §30/§31 (Phase 13.1): the overflow loses its *reply*, never its
            # fact and never its lane position — it stays ordered and is
            # ingested right where it arrived
            self.dropped += 1
            self._trace(
                ET.EXTERNAL_TRANSPORT_DROPPED, self._ids(degraded.event), reason="lane_full"
            )
        self.accepted += 1
        return {"accepted": True, "lane": lane.key, "transport_event_id": event.transport_event_id}

    def _normalize(self, payload: Any) -> NormalizedMessageEvent | None:
        self_ids = tuple(str(item) for item in (getattr(self.config, "self_ids", []) or []))
        space_map = dict(
            getattr(getattr(self.runtime, "config", None), "social_space_map", {}) or {}
        )
        if isinstance(payload, NormalizedMessageEvent):
            return payload
        if isinstance(payload, dict):
            self._seq = getattr(self, "_seq", 0) + 1
            return normalize_message_event(
                payload,
                ingest_time=float(self._clock()),
                ingest_seq=self._seq,
                self_ids=self_ids,
                social_space_map=space_map,
            )
        self._seq = getattr(self, "_seq", 0) + 1
        return normalize_typed_event(
            payload,
            ingest_time=float(self._clock()),
            ingest_seq=self._seq,
            self_ids=self_ids,
            social_space_map=space_map,
        )

    def _lane_for(self, key: str) -> _Lane:
        lane = self._lanes.get(key)
        if lane is None:
            lane = _Lane(
                key,
                max_pending=int(getattr(self.config, "max_pending_per_lane", 20)),
                gateway=self,
            )
            lane.start()
            self._lanes[key] = lane
        return lane

    # ------------------------------------------------------------ the turn

    async def _handle_lane_item(self, item: _LaneItem) -> None:
        """Ingest first, then (maybe) reply — the two phases stay separate (§31).

        A degraded item (lane overflow) still ingests *in its lane position*;
        it simply never reaches the conversation runtime.
        """
        event = item.event
        world_event = self._world_event(event)
        await self.runtime.submit_external(world_event)
        results = await self.runtime.wakeup()
        if not item.respond:
            return  # the fact happened; the reply was traded away (§3)
        influence = next(
            (
                str(result.get("influence", ""))
                for result in results
                if result.get("event_id") == world_event.event_id
            ),
            "",
        )
        ceiling = self._mode_ceiling(event, influence)
        if ceiling == "silent":
            return
        await self._respond(event, world_event, ceiling)

    def _world_event(self, event: NormalizedMessageEvent) -> Any:
        """§9/§10: the only shape the sandbox sees; identity stays a handle here."""
        core_ids = {}
        try:
            core_ids = self.runtime.persons.core_map()
        except Exception:  # noqa: BLE001 - an unknown account is a plain person
            core_ids = {}
        return adapt_qq_message(
            message_id=event.message_id or event.transport_event_id,
            actor_id=event.user_id,
            text=event.plain_text,
            display_name=event.display_name,
            is_group=event.is_group,
            group_id=event.group_id,
            mentioned=event.mentioned_self,
            reply_to_bot=event.reply_to_bot,
            is_core_actor=str(event.user_id) in core_ids,
            social_space_id=event.social_space_id,
            timestamp=event.raw_time or event.ingest_time,
        )

    def _mode_ceiling(self, event: NormalizedMessageEvent, influence: str) -> str:
        """§32/§36: the adapter-side *upper bound*, on top of the sandbox gate.

        The existing external-influence decision stays authoritative: a fact
        the sandbox rejected or decided to take no effect from never speaks,
        and this method can only lower the ceiling — it never raises it.
        Private chat and being addressed allow a full reply; an unaddressed
        group message is observed, not answered.
        """
        if influence in ("reject", "no_effect"):
            return "silent"
        if not event.is_group:
            return "reply"
        core_friend = False
        try:
            core_friend = str(event.user_id) in self.runtime.persons.core_map()
        except Exception:  # noqa: BLE001 - policy falls back to the plain gate
            core_friend = False
        if event.mentioned_self or event.reply_to_bot or core_friend:
            return "reply"
        return "silent"

    async def _respond(self, event: NormalizedMessageEvent, world_event: Any, ceiling: str) -> None:
        response = await self.runtime.conversation_turn(
            message=event.plain_text,
            actor_id=event.user_id,
            source="qq",
            social_space_id=event.social_space_id,
            group_id=event.group_id,
            event_id=world_event.event_id,
            correlation_id=world_event.correlation_id,
            mode_ceiling=ceiling,
        )
        committed = self.runtime.commit_conversation_response(response)  # §43: before queueing
        if committed.mode == "silent" or not committed.text:
            return  # §41: silence never touches the network
        self.responses += 1
        item = OutboundItem(
            turn_id=committed.turn_id,
            text=committed.text,
            mode=committed.mode,
            message_type="group" if event.is_group else "private",
            user_id=event.user_id,
            group_id=event.group_id,
        )
        self._outbound.append(item)
        self._outbound_wakeup.set()
        self._trace(
            ET.OUTBOUND_RESPONSE_QUEUED,
            {
                "turn_id": item.turn_id,
                "response_mode": item.mode,
                "message_type": item.message_type,
                "transport_event_id": event.transport_event_id,
            },
            target=item.user_id,
        )

    # -------------------------------------------------------------- outbound

    def pending_outbound(self) -> int:
        return len(self._outbound)

    def busy(self) -> bool:
        return any(lane.busy or lane.pending for lane in self._lanes.values()) or bool(
            self._in_flight
        )

    async def drain(self, *, timeout: float = 5.0, idle_rounds: int = 3) -> bool:
        """Wait until every lane item and outbound send has finished (§60).

        Returns True when the gateway went idle inside the timeout; a *slow*
        turn (a real model call) is waited for, never cancelled — the work that
        already started is allowed to complete.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        idle = 0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.002)
            if self.busy() or self.pending_outbound():
                idle = 0
                continue
            idle += 1
            if idle >= idle_rounds:
                return True
        return not (self.busy() or self.pending_outbound())

    async def _outbound_loop(self) -> None:
        while True:
            if not self._outbound:
                self._outbound_wakeup.clear()
                await self._outbound_wakeup.wait()
                continue
            item = self._outbound.popleft()
            await self._deliver(item)

    async def _deliver(self, item: OutboundItem) -> None:
        """§45-§48: resend *this* response only, bounded, once per turn."""
        if item.turn_id in self._sent_turns:
            return  # §48: a delivered turn is never sent twice
        retries = int(getattr(self.config, "outbound_max_retries", 3))
        payload: dict[str, Any] = {
            "message_type": item.message_type,
            "message": [{"type": "text", "data": {"text": item.text}}],
        }
        if item.message_type == "group":
            payload["group_id"] = item.group_id
        else:
            payload["user_id"] = item.user_id
        for attempt in range(1, retries + 2):
            item.attempts = attempt
            try:
                await self.transport.send(payload)
            except Exception as exc:  # noqa: BLE001 - one item failing is not fatal (§62)
                self._log.warning("[OneBot] send failed (attempt %d): %s", attempt, exc)
                self._trace(
                    ET.OUTBOUND_RESPONSE_FAILED,
                    {
                        "turn_id": item.turn_id,
                        "delivery_status": "retry" if attempt <= retries else "failed",
                        "attempts": attempt,
                    },
                    target=item.user_id,
                )
                if attempt <= retries:
                    await asyncio.sleep(self._retry_delay)
                    continue
                self.failed += 1
                item.trace = {"delivery_status": "failed", "attempts": attempt}
                return
            self.sent += 1
            item.delivered = True
            self._sent_turns.append(item.turn_id)
            self._trace(
                ET.OUTBOUND_RESPONSE_SENT,
                {
                    "turn_id": item.turn_id,
                    "delivery_status": "sent",
                    "attempts": attempt,
                    "response_mode": item.mode,
                },
                target=item.user_id,
            )
            return

    # ------------------------------------------------------------- plumbing

    @staticmethod
    def _ids(event: NormalizedMessageEvent) -> dict[str, Any]:
        return {
            "transport_event_id": event.transport_event_id,
            "ingest_seq": event.ingest_seq,
            "self_id": event.self_id,
            "social_space_id": event.social_space_id,
            "group_id": event.group_id,
        }

    def _trace(
        self, event_type: ET, payload: dict[str, Any], *, reason: str = "", target: str = ""
    ) -> Any:
        """§64/§65: transport traces — never a world fact, never a revision."""
        return self.runtime.events.publish(
            event_type,
            source="onebot",
            target=target,
            payload={**payload, "reason": reason} if reason else dict(payload),
        )
