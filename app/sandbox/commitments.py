"""Social Commitment & Obligation (v2.1 Phase 9 §5-§46).

    SocialInteractionFact → CommitmentDetector → SocialCommitment
        → CommitmentGoalBridge → Goal → Decision → Action
        → shared activity / miss → fulfilled | broken | rescheduled
        → SocialInteractionFact → RelationshipUpdateEngine → Experience → Memory

Four things this module keeps strictly apart (§3):

* a **relationship** is who someone is to her (Phase 8, untouched here);
* a **commitment** is what she told that person she would do — its own state,
  never a field inside relationship metadata, never a memory by existing;
* a **goal** is what she is working on *now* (the commitment only *feeds* it);
* a **memory** is what already happened — reachable only from an outcome.

Detection is deterministic (§9/§35): only verified facts that explicitly carry
a future arrangement create a commitment. Vague intent ("有空一起玩") creates
nothing at all; unparsable time is not guessed at (§13/§36).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.sandbox.events import SandboxEventType as ET
from app.sandbox.mutations import StateMutation
from app.sandbox.relations import InteractionSignificance, SocialInteractionFact

#: how long after ``due_at`` a missed promise is still recoverable (§24)
DEFAULT_GRACE_MINUTES = 120.0
#: a shared activity counts as *really happened* after this much (§20)
MIN_FULFILL_DURATION_MINUTES = 10.0
#: goal priority bands by commitment kind (§17) — character-defined ordering
PRIORITY_BY_KIND: dict[str, float] = {
    "appointment": 0.78,
    "shared_activity": 0.66,
    "help": 0.6,
    "deliverable": 0.52,
    "follow_up": 0.44,
}
#: strength nudges the band (a soft intention must never outrank an explicit one)
PRIORITY_BY_STRENGTH: dict[str, float] = {"explicit": 0.0, "soft": -0.1}


class CommitmentKind(str, Enum):  # noqa: UP042 - §11, deliberately small
    shared_activity = "shared_activity"
    appointment = "appointment"
    help = "help"
    follow_up = "follow_up"
    deliverable = "deliverable"


class CommitmentStatus(str, Enum):  # noqa: UP042 - §5, no zoo of states
    pending = "pending"
    scheduled = "scheduled"
    active = "active"
    in_progress = "in_progress"
    rescheduled = "rescheduled"
    completed = "completed"
    cancelled = "cancelled"
    declined = "declined"
    expired = "expired"
    broken = "broken"

    @property
    def terminal(self) -> bool:
        return self in (
            CommitmentStatus.completed,
            CommitmentStatus.cancelled,
            CommitmentStatus.declined,
            CommitmentStatus.expired,
            CommitmentStatus.broken,
        )


class CommitmentStrength(str, Enum):  # noqa: UP042 - §12
    explicit = "explicit"
    soft = "soft"


#: statuses that still owe something to someone
OPEN_STATUSES: frozenset[CommitmentStatus] = frozenset(
    {
        CommitmentStatus.pending,
        CommitmentStatus.scheduled,
        CommitmentStatus.active,
        CommitmentStatus.in_progress,
        CommitmentStatus.rescheduled,
    }
)

#: verified fact types that may create a commitment, and what they mean (§9/§35)
DETECTABLE_FACTS: dict[str, CommitmentKind] = {
    "invitation_accepted": CommitmentKind.shared_activity,
    "appointment_confirmed": CommitmentKind.appointment,
    "promise_made": CommitmentKind.follow_up,
}
#: an explicit reschedule fact moves an existing commitment (§25)
RESCHEDULE_FACT = "appointment_rescheduled"
#: the "it really happened" fact for a shared activity (§20)
SHARED_ACTIVITY_FACT = "shared_activity"
#: the outcome facts this layer emits into the Phase 8 relationship rules (§22)
OUTCOME_FACT_FULFILLED = "commitment_fulfilled"
OUTCOME_FACT_BROKEN = "commitment_broken"
OUTCOME_FACT_RESCHEDULED = "commitment_rescheduled"

#: vague intent never becomes an active commitment (§12/§35)
_VAGUE_RE = re.compile(r"下次|有空|有空再说|改天|以后|回头|再说吧|找时间|有机会|哪天|随时|看情况")
_CN_NUMERALS = {
    "零": 0,
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


class TimeWindow(BaseModel):
    """A parsed arrangement window (§13) — never a guessed single instant."""

    earliest_at: float = 0.0
    latest_at: float = 0.0
    due_at: float = 0.0
    hint: str = ""


def _local_tz() -> Any:
    try:
        return ZoneInfo("Asia/Shanghai")
    except (ZoneInfoNotFoundError, KeyError, ValueError):  # pragma: no cover - env fallback
        return datetime.now().astimezone().tzinfo


def _to_local(now: float) -> datetime:
    return datetime.fromtimestamp(now, tz=_local_tz())


def _hour_value(text: str) -> int | None:
    """'8' / '八' / '二十' → hour value (bounded 0-23), None when unreadable."""
    if text.isdigit():
        return int(text)
    total = 0
    for char in text:
        value = _CN_NUMERALS.get(char)
        if value is None:
            return None
        if char == "十":
            total = (total or 1) * 10  # 十二 → 12, 二十 → 20
        else:
            total += value
    return total


def parse_time_hint(text: str, *, now: float) -> TimeWindow | None:
    """Extract an explicit *future* arrangement from a message (§13).

    Handles the small, closed vocabulary the character actually meets —
    今晚/明晚/明天/周六/N点/N分钟后/HH:MM — and returns None for anything else.
    No guessing: an unreadable hint means "do not create a commitment" (§36).
    """
    hint = (text or "").strip()
    if not hint:
        return None
    local = _to_local(now)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)

    minute_match = re.search(r"(\d+)\s*(分钟|个小时|小时)后", hint)
    if minute_match:
        amount = int(minute_match.group(1))
        delta = (
            timedelta(minutes=amount)
            if minute_match.group(2) == "分钟"
            else timedelta(hours=amount)
        )
        target = local + delta
        return TimeWindow(
            earliest_at=target.timestamp() - 600.0,
            latest_at=target.timestamp() + 1800.0,
            due_at=target.timestamp(),
            hint=hint,
        )

    if "今晚" in hint or "今天晚上" in hint or "夜里" in hint:
        day = midnight
        if local.hour >= 23:
            day = day + timedelta(days=1)  # "tonight" after 23:00 means tomorrow
        return TimeWindow(
            earliest_at=(day + timedelta(hours=19)).timestamp(),
            latest_at=(day + timedelta(hours=23, minutes=30)).timestamp(),
            due_at=(day + timedelta(hours=23, minutes=30)).timestamp(),
            hint=hint,
        )
    if "明晚" in hint or "明天晚上" in hint:
        day = midnight + timedelta(days=1)
        return TimeWindow(
            earliest_at=(day + timedelta(hours=19)).timestamp(),
            latest_at=(day + timedelta(hours=23, minutes=30)).timestamp(),
            due_at=(day + timedelta(hours=23, minutes=30)).timestamp(),
            hint=hint,
        )
    if "明天" in hint or "明早" in hint:
        day = midnight + timedelta(days=1)
        return TimeWindow(
            earliest_at=(day + timedelta(hours=9)).timestamp(),
            latest_at=(day + timedelta(hours=22)).timestamp(),
            due_at=(day + timedelta(hours=22)).timestamp(),
            hint=hint,
        )

    weekday = re.search(r"(?:周|星期|礼拜)([一二三四五六日天])", hint)
    if weekday:
        target_index = _WEEKDAYS[weekday.group(1)]
        ahead = (target_index - local.weekday()) % 7 or 7  # "周X" always means ahead
        day = midnight + timedelta(days=ahead)
        return TimeWindow(
            earliest_at=(day + timedelta(hours=10)).timestamp(),
            latest_at=(day + timedelta(hours=22)).timestamp(),
            due_at=(day + timedelta(hours=22)).timestamp(),
            hint=hint,
        )

    clock_match = re.search(r"(\d{1,2}|[零一两二三四五六七八九十]+)\s*(?:点|:|：)(\d{1,2})?", hint)
    if clock_match:
        hour = _hour_value(clock_match.group(1))
        if hour is None or hour > 23:
            return None
        minute = int(clock_match.group(2) or 0)
        if minute > 59:
            return None
        if any(word in hint for word in ("下午", "晚上", "傍晚")) and hour < 12:
            hour += 12
        elif "中午" in hint and hour < 11:
            hour = 12
        elif "凌晨" in hint and hour >= 12:
            hour -= 12
        day = midnight + timedelta(days=1) if "明天" in hint else midnight
        target = day + timedelta(hours=hour, minutes=minute)
        if target <= local:
            target = target + timedelta(days=1)  # a clock time already past is tomorrow
        return TimeWindow(
            earliest_at=(target - timedelta(minutes=30)).timestamp(),
            latest_at=(target + timedelta(minutes=30)).timestamp(),
            due_at=target.timestamp(),
            hint=hint,
        )
    return None


def is_vague_intent(text: str) -> bool:
    """True for "下次吧 / 有空一起" — intent without an arrangement (§12)."""
    hint = (text or "").strip()
    if not hint:
        return False
    return bool(_VAGUE_RE.search(hint)) and parse_time_hint(hint, now=0.0) is None


class SocialCommitment(BaseModel):
    """What she told someone she would do (§5) — current social state."""

    commitment_id: str
    character_id: str = ""
    #: the Phase 8 person, never a raw QQ id (§7)
    person_id: str = ""
    kind: CommitmentKind = CommitmentKind.shared_activity
    status: CommitmentStatus = CommitmentStatus.pending
    strength: CommitmentStrength = CommitmentStrength.explicit
    description: str = ""
    #: bumped on every reschedule / target change / cancellation (§37)
    revision: int = 0
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    target_action: str = ""
    target_activity: str = ""
    #: the raw wording the arrangement came from — never a guessed timestamp
    time_hint: str = ""
    earliest_at: float = 0.0
    latest_at: float = 0.0
    due_at: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    activated_at: float = 0.0
    resolved_at: float = 0.0
    #: provenance (§8): commitment ← fact ← external event
    source_interaction_id: str = ""
    source_event_id: str = ""
    correlation_id: str = ""
    causation_id: str = ""
    #: schedule history, goal id, last outcome — never prompt text
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def open(self) -> bool:
        return self.status in OPEN_STATUSES

    def in_window(self, now: float) -> bool:
        return self.earliest_at <= now <= self.latest_at

    def past_grace(self, now: float, *, grace_minutes: float = DEFAULT_GRACE_MINUTES) -> bool:
        return bool(self.due_at) and now > self.due_at + grace_minutes * 60.0


class CommitmentDetector:
    """Deterministic bus observer: facts in, commitments out (§9/§34-§36)."""

    def __init__(self, manager: CommitmentManager, *, clock: Any) -> None:
        self._manager = manager
        self._clock = clock
        self.created = 0
        self.fulfilled = 0
        self.vague = 0
        self.skipped = 0

    def observe(self, event: Any) -> None:
        if event.event_type is not ET.SOCIAL_INTERACTION:
            return
        payload = event.payload
        interaction_type = str(payload.get("interaction_type", ""))
        if interaction_type == SHARED_ACTIVITY_FACT:
            self._on_shared_activity(event, payload)
            return
        if interaction_type == RESCHEDULE_FACT:
            self._on_reschedule(event, payload)
            return
        if interaction_type not in DETECTABLE_FACTS:
            return
        self._on_arrangement(event, payload, interaction_type)

    # ------------------------------------------------------------ detection

    def _on_arrangement(self, event: Any, payload: dict[str, Any], interaction_type: str) -> None:
        """An accepted invitation / confirmed appointment / promise (§10/§12)."""
        hint = str(payload.get("time_hint", "") or "")
        if is_vague_intent(hint):
            self.vague += 1  # "下次吧" is intent, not an arrangement (§12)
            return
        window = parse_time_hint(hint, now=float(self._clock()))
        if window is None or window.due_at <= float(self._clock()):
            # no explicit *future* arrangement → no commitment (§10/§36)
            self.skipped += 1
            return
        kind = DETECTABLE_FACTS[interaction_type]
        declared = str(payload.get("commitment_kind", "") or "")
        if declared:
            try:
                kind = CommitmentKind(declared)
            except ValueError:
                self.skipped += 1
                return
        created = self._manager.create(
            person_id=str(event.target_entity_id),
            kind=kind,
            window=window,
            source_interaction_id=str(payload.get("interaction_id", "")),
            source_event_id=event.event_id,
            target_activity=str(payload.get("target_activity", "") or ""),
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            metadata={"description_source": interaction_type},
        )
        if created is not None:
            self.created += 1

    def _on_shared_activity(self, event: Any, payload: dict[str, Any]) -> None:
        """A shared activity really ran → any matching promise is fulfilled (§20)."""
        duration = float(payload.get("duration_minutes", 0.0) or 0.0)
        if duration < MIN_FULFILL_DURATION_MINUTES:
            self.skipped += 1  # a two-minute dabble is not a kept promise
            return
        person_id = str(event.target_entity_id)
        activity = str(payload.get("target_activity", "") or "")
        for commitment in self._manager.for_person(person_id):
            if commitment.kind is not CommitmentKind.shared_activity or not commitment.open:
                continue
            if commitment.target_activity and activity and commitment.target_activity != activity:
                continue
            if self._manager.fulfill(
                commitment,
                source_interaction_id=str(payload.get("interaction_id", "")),
                duration_minutes=duration,
            ):
                self.fulfilled += 1

    def _on_reschedule(self, event: Any, payload: dict[str, Any]) -> None:
        """A verified reschedule fact moves the existing commitment (§25)."""
        person_id = str(event.target_entity_id)
        hint = str(payload.get("time_hint", "") or "")
        window = parse_time_hint(hint, now=float(self._clock()))
        if window is None:
            self.skipped += 1
            return
        for commitment in self._manager.for_person(person_id):
            if not commitment.open:
                continue
            self._manager.reschedule(
                commitment,
                window=window,
                reason="external_reschedule",
                source_interaction_id=str(payload.get("interaction_id", "")),
            )
            return


class CommitmentGoalBridge:
    """Commitment → Goal (§15/§16/§17/§37/§38). Never starts an action itself."""

    def __init__(self, manager: CommitmentManager, *, clock: Any) -> None:
        self._manager = manager
        self._clock = clock

    # ---------------------------------------------------------------- window

    def due(self) -> list[SocialCommitment]:
        """Open commitments whose execution window is open *now*."""
        now = float(self._clock())
        return [
            commitment
            for commitment in self._manager.open()
            if commitment.earliest_at and commitment.earliest_at <= now
        ]

    async def priority_for(self, commitment: SocialCommitment) -> float:
        """§17: kind band + strength; relationship context only nudges ordering."""
        base = PRIORITY_BY_KIND.get(commitment.kind.value, 0.5)
        base += PRIORITY_BY_STRENGTH.get(commitment.strength.value, 0.0)
        base += await self._manager.relationship_hint(commitment.person_id)
        return max(0.1, min(1.0, base))

    # -------------------------------------------------------------- creation

    async def evaluate(self) -> list[str]:
        """Create/refresh one goal per due commitment (§16: never per tick)."""
        from app.sandbox.goals import GoalKind, GoalSource

        created: list[str] = []
        runtime = self._manager.runtime
        for commitment in self.due():
            goal_id = runtime.goals.create(
                kind=GoalKind.fulfill_commitment,
                source=GoalSource.social_commitment,
                source_event_id=commitment.source_event_id,
                reason=f"commitment:{commitment.kind.value}",
                priority=await self.priority_for(commitment),
                correlation_id=commitment.correlation_id,
                target_commitment=commitment.commitment_id,
                target_entity=commitment.person_id,
                metadata={
                    "commitment_id": commitment.commitment_id,
                    # §37: the revision the goal was born under — checked again
                    # right before the action starts
                    "commitment_revision": commitment.revision,
                    "target_activity": commitment.target_activity,
                },
            )
            if goal_id:
                created.append(goal_id)
                self._manager.activate(commitment, reason="execution_window_open")
        return created

    # -------------------------------------------------------------- staleness

    def validate(self, goal: Any) -> tuple[bool, str]:
        """§37/§38: a goal may only run against the commitment it was born on."""
        if getattr(getattr(goal, "kind", None), "value", "") != "fulfill_commitment":
            return True, ""
        commitment_id = str(goal.metadata.get("commitment_id", "") or goal.target_commitment or "")
        commitment = self._manager.get(commitment_id)
        if commitment is None:
            return False, "commitment_unknown"
        if not commitment.open:
            return False, f"commitment_{commitment.status.value}"
        born_under = int(goal.metadata.get("commitment_revision", commitment.revision))
        if born_under != commitment.revision:
            return False, "commitment_rescheduled"  # the plan moved under the goal
        return True, ""

    def cancel_stale(self) -> list[str]:
        """Cancel goals whose commitment closed or moved (checked every tick)."""
        from app.sandbox.goals import GoalKind

        cancelled: list[str] = []
        goals = self._manager.runtime.goals
        for goal in goals.all():
            if goal.kind is not GoalKind.fulfill_commitment or goal.status.terminal:
                continue
            ok, reason = self.validate(goal)
            if ok:
                continue
            goals.cancel(goal, reason=reason)
            cancelled.append(goal.goal_id)
        return cancelled


class CommitmentManager:
    """Owns commitment state, transitions, persistence and the event trail.

    Every transition is a canonical mutation (``affects_world=False``): the
    world stays as it was, the *cognitive* revision moves (Phase 8.1 §13), and
    the commitment's own ``revision`` moves for its lifecycle (§37).
    """

    def __init__(self, runtime: Any, *, clock: Any) -> None:
        self.runtime = runtime
        self._clock = clock
        self._items: dict[str, SocialCommitment] = {}
        #: commitments whose last write failed — retried by the tick's flush
        self._dirty: set[str] = set()
        self.created = 0
        self.fulfilled = 0
        self.broken = 0
        self.rescheduled = 0
        self.cancelled = 0
        self.persist_failures = 0

    # ------------------------------------------------------------- lifecycle

    async def restore(self) -> None:
        """§29: reload commitments; the bridge re-evaluates them afterwards."""
        rows = await self.runtime.store.list_commitments(character_id=self.runtime.character_id)
        for payload in rows:
            try:
                commitment = SocialCommitment.model_validate(payload)
            except Exception:  # noqa: BLE001 - an unreadable row must not break startup
                self.runtime._log.warning(  # noqa: SLF001
                    "[Sandbox] unreadable commitment row skipped", exc_info=True
                )
                continue
            if commitment.status.terminal:
                continue
            self._items[commitment.commitment_id] = commitment

    # ----------------------------------------------------------------- reads

    def all(self, *, statuses: set[CommitmentStatus] | None = None) -> list[SocialCommitment]:
        items = list(self._items.values())
        if statuses is not None:
            items = [item for item in items if item.status in statuses]
        return items

    def open(self) -> list[SocialCommitment]:
        return [item for item in self._items.values() if item.open]

    def get(self, commitment_id: str) -> SocialCommitment | None:
        return self._items.get(commitment_id)

    def for_person(self, person_id: str) -> list[SocialCommitment]:
        """Most urgent first — the same person's promises, no global scan."""
        return sorted(
            (item for item in self._items.values() if item.person_id == person_id),
            key=lambda item: (item.open is False, -item.priority, item.due_at or 1e18),
        )

    def important(self, *, limit: int = 3) -> list[SocialCommitment]:
        """Open, closest-to-due commitments for continuity/prompt surfaces (§30)."""
        items = [item for item in self.open() if item.due_at]
        items.sort(key=lambda item: (item.due_at, -item.priority, item.commitment_id))
        return items[:limit]

    async def relationship_hint(self, person_id: str) -> float:
        """§17: closeness may nudge ordering — it never creates or executes."""
        try:
            state = await self.runtime.relationships_dyn.get(person_id)
        except Exception:  # noqa: BLE001 - the hint is optional
            return 0.0
        if state is None:
            return 0.0
        return min(0.06, float(state.closeness) * 0.06)

    def describe(self, commitment: SocialCommitment) -> str:
        """Neutral one-liner (never a persona text, never a raw timestamp)."""
        activity = commitment.target_activity or commitment.kind.value
        return f"与{self.person_label(commitment.person_id)}的约定：{activity}"

    def person_label(self, person_id: str) -> str:
        """A person's canonical name when the bible gives one, else the id."""
        state = self.runtime.relationships_dyn.initial_for_person(person_id)
        if state is not None:
            return str(state.metadata.get("name") or person_id)
        return person_id or "对方"

    # ------------------------------------------------------------ transitions

    def create(
        self,
        *,
        person_id: str,
        kind: CommitmentKind,
        window: TimeWindow,
        source_interaction_id: str = "",
        source_event_id: str = "",
        target_activity: str = "",
        strength: CommitmentStrength = CommitmentStrength.explicit,
        correlation_id: str = "",
        causation_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SocialCommitment | None:
        """Create — or refresh the identical open promise (§16)."""
        existing = next(
            (
                item
                for item in self._items.values()
                if item.open
                and item.person_id == person_id
                and item.kind is kind
                and item.target_activity == target_activity
                and abs(item.due_at - window.due_at) < 60.0
            ),
            None,
        )
        if existing is not None:
            existing.updated_at = float(self._clock())
            if source_interaction_id:
                existing.source_interaction_id = source_interaction_id
            return existing
        now = float(self._clock())
        commitment = SocialCommitment(
            commitment_id=f"cm_{uuid.uuid4().hex[:10]}",
            character_id=self.runtime.character_id,
            person_id=person_id,
            kind=kind,
            status=CommitmentStatus.pending,
            strength=strength,
            description="",
            target_activity=target_activity,
            time_hint=window.hint[:120],
            earliest_at=window.earliest_at,
            latest_at=window.latest_at,
            due_at=window.due_at,
            created_at=now,
            updated_at=now,
            source_interaction_id=source_interaction_id,
            source_event_id=source_event_id,
            correlation_id=correlation_id,
            causation_id=causation_id or source_event_id,
            metadata=dict(metadata or {}),
        )
        commitment.description = self.describe(commitment)
        commitment.priority = PRIORITY_BY_KIND.get(kind.value, 0.5)
        self._items[commitment.commitment_id] = commitment
        self.created += 1
        self._record(commitment, reason="created", field="status", before="")
        self._publish(
            ET.COMMITMENT_CREATED, commitment, reason="detected", extra={"kind": kind.value}
        )
        return commitment

    def activate(self, commitment: SocialCommitment, *, reason: str) -> bool:
        """pending/scheduled/rescheduled → active, once (§16: no re-announcing)."""
        if not commitment.open or commitment.status in (
            CommitmentStatus.active,
            CommitmentStatus.in_progress,
        ):
            return False
        before = commitment.status.value
        stamp = float(self._clock())
        commitment.status = CommitmentStatus.active
        commitment.activated_at = commitment.activated_at or stamp
        commitment.updated_at = stamp
        self._record(commitment, reason=f"activated:{reason}", field="status", before=before)
        self._publish(ET.COMMITMENT_ACTIVATED, commitment, reason=reason)
        return True

    def mark_in_progress(self, commitment: SocialCommitment, *, reason: str) -> bool:
        if not commitment.open:
            return False
        before = commitment.status.value
        commitment.status = CommitmentStatus.in_progress
        commitment.updated_at = float(self._clock())
        self._record(commitment, reason=f"in_progress:{reason}", field="status", before=before)
        self._publish(ET.COMMITMENT_ACTIVATED, commitment, reason=reason)
        return True

    def fulfill(
        self,
        commitment: SocialCommitment,
        *,
        source_interaction_id: str = "",
        duration_minutes: float = 0.0,
        at: float | None = None,
    ) -> bool:
        """§20/§21: only a *really happened* shared activity fulfils a promise."""
        if not commitment.open:
            return False
        stamp = float(at if at is not None else self._clock())
        before = commitment.status.value
        commitment.status = CommitmentStatus.completed
        commitment.resolved_at = stamp
        commitment.updated_at = stamp
        commitment.revision += 1
        commitment.metadata["outcome"] = "fulfilled"
        commitment.metadata["duration_minutes"] = round(duration_minutes, 2)
        self.fulfilled += 1
        self._record(commitment, reason="fulfilled", field="status", before=before)
        self._publish(
            ET.COMMITMENT_FULFILLED,
            commitment,
            reason="shared_activity",
            extra={
                "interaction_id": source_interaction_id,
                "duration_minutes": round(duration_minutes, 2),
                "significance": "meaningful",
            },
        )
        self._emit_outcome_fact(commitment, OUTCOME_FACT_FULFILLED, outcome="fulfilled")
        return True

    def mark_broken(self, commitment: SocialCommitment, *, reason: str) -> bool:
        """§24: due passed, grace passed, still nothing — a broken promise."""
        if not commitment.open:
            return False
        stamp = float(self._clock())
        before = commitment.status.value
        commitment.status = CommitmentStatus.broken
        commitment.resolved_at = stamp
        commitment.updated_at = stamp
        commitment.revision += 1
        commitment.metadata["outcome"] = "broken"
        commitment.metadata["broken_reason"] = reason
        self.broken += 1
        self._record(commitment, reason=f"broken:{reason}", field="status", before=before)
        self._publish(
            ET.COMMITMENT_BROKEN,
            commitment,
            reason=reason,
            extra={"significance": "major"},
        )
        self._emit_outcome_fact(commitment, OUTCOME_FACT_BROKEN, outcome="broken")
        return True

    def reschedule(
        self,
        commitment: SocialCommitment,
        *,
        window: TimeWindow,
        reason: str,
        source_interaction_id: str = "",
    ) -> bool:
        """§25: same commitment id, revision + 1, old schedule archived."""
        if not commitment.open:
            return False
        stamp = float(self._clock())
        before_due = commitment.due_at
        history = list(commitment.metadata.get("schedule_history", []) or [])
        history.append(
            {
                "revision": commitment.revision,
                "earliest_at": commitment.earliest_at,
                "latest_at": commitment.latest_at,
                "due_at": commitment.due_at,
                "time_hint": commitment.time_hint,
                "at": stamp,
                "reason": reason,
            }
        )
        commitment.metadata["schedule_history"] = history[-8:]
        commitment.earliest_at = window.earliest_at
        commitment.latest_at = window.latest_at
        commitment.due_at = window.due_at
        commitment.time_hint = window.hint[:120]
        commitment.status = CommitmentStatus.rescheduled
        commitment.revision += 1
        commitment.updated_at = stamp
        if source_interaction_id:
            commitment.source_interaction_id = source_interaction_id
        self.rescheduled += 1
        self._record(
            commitment,
            reason=f"rescheduled:{reason}",
            field="due_at",
            before=before_due,
            after=commitment.due_at,
        )
        self._publish(
            ET.COMMITMENT_RESCHEDULED,
            commitment,
            reason=reason,
            extra={"revision": commitment.revision, "significance": "normal"},
        )
        self._emit_outcome_fact(commitment, OUTCOME_FACT_RESCHEDULED, outcome="rescheduled")
        return True

    def cancel(self, commitment: SocialCommitment, *, by: str, reason: str) -> bool:
        """§23: who cancelled, why, when — a polite early notice ≠ a silent miss."""
        if not commitment.open:
            return False
        status = {
            "character_cancelled": CommitmentStatus.cancelled,
            "other_party_cancelled": CommitmentStatus.cancelled,
            "declined": CommitmentStatus.declined,
            "system_expired": CommitmentStatus.expired,
        }.get(by, CommitmentStatus.cancelled)
        stamp = float(self._clock())
        before = commitment.status.value
        commitment.status = status
        commitment.resolved_at = stamp
        commitment.updated_at = stamp
        commitment.revision += 1
        commitment.metadata["outcome"] = by
        commitment.metadata["cancel_reason"] = reason
        self.cancelled += 1
        self._record(commitment, reason=f"cancelled:{by}", field="status", before=before)
        self._publish(
            ET.COMMITMENT_CANCELLED,
            commitment,
            reason=reason,
            extra={"by": by, "status": status.value},
        )
        # a polite early cancellation is a *small* social act, not a betrayal:
        # only a silent miss becomes "broken" (§23/§24)
        self._emit_outcome_fact(
            commitment,
            OUTCOME_FACT_RESCHEDULED if by == "character_cancelled" else OUTCOME_FACT_BROKEN,
            outcome=by,
            significance="normal" if by == "character_cancelled" else "meaningful",
        )
        return True

    def expire(self, commitment: SocialCommitment, *, reason: str) -> bool:
        return self.cancel(commitment, by="system_expired", reason=reason)

    # ---------------------------------------------------------------- sweep

    def sweep(self, *, now: float | None = None) -> dict[str, int]:
        """§24/§29: window activation, expiry and grace-based breakage."""
        stamp = float(now if now is not None else self._clock())
        report = {"activated": 0, "expired": 0, "broken": 0}
        for commitment in list(self._items.values()):
            if not commitment.open:
                continue
            if commitment.due_at and commitment.past_grace(stamp):
                # §24: due + grace passed with no outcome — a broken promise.
                # A one-minute delay is inside the grace window, never betrayal.
                self.mark_broken(commitment, reason="due_passed_without_outcome")
                report["broken"] += 1
        return report

    # -------------------------------------------------------------- plumbing

    def _record(
        self,
        commitment: SocialCommitment,
        *,
        reason: str,
        field: str,
        before: Any = "",
        after: Any = None,
    ) -> None:
        """Canonical mutation trail + the cognitive line (Phase 8.1 §13)."""
        self._dirty.add(commitment.commitment_id)
        self.runtime.mutations.record(
            StateMutation(
                target=f"commitment:{commitment.commitment_id}",
                field=field,
                before=before,
                after=commitment.status.value if after is None else after,
                source="character" if reason.startswith(("activated", "in_progress")) else "social",
                reason=reason,
                # a promise is social state, never physical world state (§39)
                affects_world=False,
            )
        )
        self.runtime.note_cognitive_change(reason=f"commitment:{reason}")

    def _publish(
        self,
        event_type: ET,
        commitment: SocialCommitment,
        *,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "commitment_id": commitment.commitment_id,
            "person_id": commitment.person_id,
            "kind": commitment.kind.value,
            "status": commitment.status.value,
            "strength": commitment.strength.value,
            "revision": commitment.revision,
            "reason": reason,
            "description": commitment.description,
            "target_activity": commitment.target_activity,
            "due_at": commitment.due_at,
            "time_hint": commitment.time_hint,
        }
        payload.update(extra or {})
        return self.runtime.events.publish(
            event_type,
            source="character",
            target=commitment.commitment_id,
            payload=payload,
            causation_id=commitment.causation_id or commitment.source_event_id,
            correlation_id=commitment.correlation_id,
        )

    def _emit_outcome_fact(
        self,
        commitment: SocialCommitment,
        interaction_type: str,
        *,
        outcome: str,
        significance: str = "meaningful",
    ) -> None:
        """§22/§46: the outcome reaches relationships only as a Phase 8 fact."""
        fact = SocialInteractionFact.create(
            character_id=self.runtime.character_id,
            person_id=commitment.person_id,
            interaction_type=interaction_type,
            source="commitment",
            timestamp=float(self._clock()),
            outcome=outcome,
            significance=InteractionSignificance(significance),
            metadata={
                "commitment_id": commitment.commitment_id,
                "kind": commitment.kind.value,
                "description": commitment.description,
            },
        )
        task = asyncio.create_task(
            self.runtime.apply_social_interaction(
                fact,
                correlation_id=commitment.correlation_id,
                causation_id=commitment.causation_id or commitment.source_event_id,
            )
        )
        self.runtime._track_background(task)  # noqa: SLF001 - same runtime

    async def flush(self) -> None:
        """Persist dirty commitments through the existing store facade (§28)."""
        for commitment_id in list(self._dirty):
            commitment = self._items.get(commitment_id)
            if commitment is None:
                self._dirty.discard(commitment_id)
                continue
            written = bool(await self.runtime.store.save_commitment(commitment))
            if written:
                self._dirty.discard(commitment_id)
            else:
                self.persist_failures += 1
                self.runtime._log.warning(  # noqa: SLF001
                    "[Sandbox] commitment %s could not be persisted (kept in memory)",
                    commitment_id,
                )
