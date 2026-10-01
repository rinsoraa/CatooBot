"""SandboxRuntime (v2.0 §67-§74/§145-§146): the world that keeps living.

Tick (default 10 min) is CPU/DB only — no LLM unless a genuine ambiguity comes
up (§69/§193). External events wake it immediately (§70). Restart settles
through the gap bounded, never replaying every missed tick (§72). All state
mutations flow through ``_transaction`` and the only writer is this runtime
(§76/§77).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from app.sandbox.actions import ActionSystem
from app.sandbox.bible import CharacterBible
from app.sandbox.decision import (
    InterruptEvaluator,
    ProposalValidator,
    SandboxDecisionEngine,
)
from app.sandbox.entities import PetSystem
from app.sandbox.models import (
    ActionDefinition,
    ActionInstance,
    ActionStatus,
    CharacterEntity,
    Commission,
    DecisionTrace,
    EventLevel,
    EventSource,
    ExternalEvent,
    PetState,
    SandboxDecision,
    SandboxEventRecord,
    SandboxPhase,
    SandboxSnapshot,
    SocialSpace,
    SpaceNode,
    WorldObjectItem,
)
from app.sandbox.modes import ModeRuntime
from app.sandbox.needs import NeedSystem
from app.sandbox.seed import (
    build_action_definitions,
    build_inventories,
    build_needs,
    build_objects,
    build_projects,
    build_social_spaces,
    build_spaces,
    seeded_rng,
)
from app.sandbox.store import SandboxStore
from app.sandbox.world import (
    InventorySystem,
    ObjectSystem,
    SpaceSystem,
    WorldRuleEngine,
)
from app.utils.narrator import narrate

logger = logging.getLogger("CatooBot.Sandbox")

#: action id → the canonical activity written into CharacterState
#: ("gaming/reading/eating/working" are the ids ReplyTiming treats as busy)
ACTIVITY_IDS: dict[str, str] = {
    "play_minecraft": "gaming",
    "play_singleplayer": "gaming",
    "watch_animation": "reading",
    "work_commission": "working",
    "eat_pudding": "eating",
    "eat_cake": "eating",
    "eat_fruit": "eating",
    "drink_cola": "eating",
    "sleep": "sleeping",
    "nap": "napping",
    "browse_social": "online",
    "chat_group": "online",
    "browse_forum": "online",
    "film_cat": "online",
    "go_shopping_cola": "out",
    "go_shopping_sweets": "out",
    "take_out_trash": "out",
    "pick_up_package": "out",
    "walk": "out",
}

TickListener = Callable[[dict[str, Any]], None]


class SandboxRuntime:
    """The character's persistent life. One instance per bot."""

    def __init__(
        self,
        config: Any,
        store: SandboxStore,
        *,
        bible: CharacterBible,
        bot: Any = None,
        clock: Any = time.time,
        ai_decider: Any = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.bible = bible
        self.bot = bot
        self._clock = clock
        self._log = logger_ or logger
        self._rng = seeded_rng(int(getattr(config, "simulation_seed", 0)))

        # ------------------------------------------------------------ state
        self.phase = SandboxPhase.initializing
        self.character = CharacterEntity()
        self.pet = PetState()
        self.projects = build_projects()
        self.social_spaces: dict[str, SocialSpace] = {}
        self.commissions: dict[str, Commission] = {}
        self.knowledge: dict[str, dict[str, Any]] = {}
        self.pending_events: list[ExternalEvent] = []
        self._last_tick = float(clock())
        self._notes: list[str] = []  # micro-continuity feed
        self._restored = False
        self._private_chat_until = 0.0  # online-social window after a chat
        #: narrative-on-every-tick switch (logging.narrate_world_ticks)
        self.narrate_ticks = False
        #: optional ContinuityManager — meaningful events feed recent_events
        self.continuity: Any = None
        #: in-memory ring of recent events (works without a database too)
        self._recent_memory: list[SandboxEventRecord] = []
        #: optional async (activity, location, energy) -> None projection into
        #: CharacterState so WebUI/prompts/timing all read the same life.
        self.state_sync: Any = None

        # ------------------------------------------------------------ systems
        self.spaces = SpaceSystem(build_spaces())
        self.objects = ObjectSystem(build_objects())
        self.inventories = InventorySystem(build_inventories(bible))
        self.needs = NeedSystem(build_needs(), clock=clock)
        self.actions = ActionSystem(build_action_definitions(), rng=self._rng, clock=clock)
        self.rules = WorldRuleEngine(bible)
        self.modes = ModeRuntime(clock=clock)
        self.pet_system = PetSystem(self.pet, rng=self._rng, clock=clock)
        self.interrupt = InterruptEvaluator(bible=bible, clock=clock)
        self.validator = ProposalValidator(self.rules, self.actions)
        self.social_spaces = build_social_spaces(bible)
        self.engine = SandboxDecisionEngine(
            bible=bible,
            actions=self.actions,
            needs=self.needs,
            spaces=self.spaces,
            objects=self.objects,
            inventories=self.inventories,
            rules=self.rules,
            modes=self.modes,
            pet=self.pet_system,
            rng=self._rng,
            clock=clock,
            ai_decider=ai_decider if getattr(config, "allow_ai_decisions", True) else None,
        )
        self.current_action: ActionInstance | None = None

    # ------------------------------------------------------------------ api

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True))

    async def start(self) -> None:
        """Load persisted state; recover through any downtime (§72)."""
        restored = await self._restore()
        now = float(self._clock())
        if restored:
            elapsed_min = max(0.0, (now - self._last_tick) / 60.0)
            if elapsed_min >= 1.0:
                self.phase = SandboxPhase.recovering
                await self._settle_gap(elapsed_min)
        else:
            await self._seed_fresh()
        self.phase = SandboxPhase.running
        await self.store.state_set("phase", self.phase.value)
        self._derive_modes()
        await self._sync_state()

    async def shutdown(self) -> None:
        self.phase = SandboxPhase.stopped
        await self._persist_all()
        await self.store.state_set("phase", self.phase.value)

    # ---------------------------------------------------------------- seed

    async def _seed_fresh(self) -> None:
        """First run of a new character: bible seed → initial world (§120)."""
        self._log.info("[Sandbox] seeding a fresh world from %s", self.bible.version)
        self.character.location = "livingroom"
        await self.store.save_bible(self.bible)
        await self.store.state_set("bible_version", self.bible.version)
        await self._persist_all()
        await self._append_event(
            "sandbox_initialized",
            "世界初始化完成（根据人物档案）",
            source=EventSource.system,
            level=EventLevel.major,
            reason="bible_seed",
        )

    async def reinitialize(self) -> None:
        """Admin/Rebuild path (§155): wipe runtime, seed from the bible again."""
        await self._seed_fresh()
        self.phase = SandboxPhase.running

    # ------------------------------------------------------------ tick loop

    async def tick(self, *, minutes: float | None = None) -> dict[str, Any]:
        """One sandbox tick (§68). Returns a small report for logs/tests."""
        if self.phase not in (SandboxPhase.running, SandboxPhase.degraded):
            return {"skipped": self.phase.value}
        step = minutes if minutes is not None else self._tick_minutes()
        now = float(self._clock())
        self.needs.advance(step, rest=self._rest_coefficient())
        pet_events = self.pet_system.tick(
            minutes=step,
            owner_space=self.character.location,
            owner_action=self.current_action.definition_id if self.current_action else "",
            owner_home=self.spaces.is_home(self.character.location),
            food_available=self.inventories.get("cat_food_bowl").count("猫粮") > 0,
        )
        for line in pet_events:
            await self._append_event(
                "pet",
                line,
                source=EventSource.pet_action,
                level=EventLevel.micro,
                reason="pet_rule",
            )
        # pet complaints feed her need to care for the cat (§177)
        if pet_events and "蹭过来" in pet_events[0]:
            self.needs.add("pet_care", 0.25)

        report: dict[str, Any] = {"minutes": step, "pet": pet_events}
        self._last_tick = now

        # Action lifecycle.
        if self.current_action is not None:
            self.current_action.progress = self.actions.progress(self.current_action, now=now)
            if self.actions.is_due(self.current_action, now=now):
                await self._complete_action()
                report["completed"] = True

        # Critical needs / no action → decision point.
        need_decision = self.current_action is None or self.needs.critical()
        if need_decision:
            await self._decide_and_apply(space_id=self.character.location)
            report["decided"] = True

        self._derive_modes()
        self.character.energy = max(0.0, min(1.0, 1.0 - self.needs.level("energy")))
        await self._process_pending_events()
        if self._should_snapshot():
            await self._snapshot()
        await self._persist_deltas()
        if self.narrate_ticks:
            pressing = self.needs.summary_line() if self.needs.pressing() else ""
            detail = "沙盒心跳" + (f"（{pressing}）" if pressing else "")
            narrate().world(f"{self.status_line()}  ·  {'+'.join(self.modes.ids())}", detail=detail)
        return report

    def _tick_minutes(self) -> float:
        if self._last_tick <= 0:
            return max(1.0, float(self.config.tick_seconds) / 60.0)
        elapsed = max(0.0, (float(self._clock()) - self._last_tick) / 60.0)
        cap = max(1.0, float(self.config.tick_seconds) / 60.0 * 2)
        return max(1.0, min(cap, elapsed))

    # ------------------------------------------------------------ actions

    async def _complete_action(self) -> None:
        action = self.current_action
        if action is None:
            return
        definition = self.actions.definition(action)
        if definition is None:
            action.status = ActionStatus.completed
            self.current_action = None
            return

        def apply() -> None:
            self.actions.finish(action, ActionStatus.completed)
            self.inventories.consume(definition.consumes)
            self.needs.relieve(definition.need_relief)
            for key, amount in definition.need_cost.items():
                self.needs.add(key, amount)
            for effect_key, value in definition.effects.items():
                if effect_key.startswith("object:"):
                    self.objects.apply_effect(effect_key, value)
                elif effect_key.startswith("inventory:"):
                    self.inventories.apply_effect(effect_key, value)
                elif effect_key.startswith("project:"):
                    name = effect_key.split(":", 1)[1]
                    project = self.projects.get(name)
                    if project is not None:
                        project["progress"] = min(1.0, float(project["progress"]) + value)
                elif effect_key.startswith("commission:"):
                    self._advance_commission(float(value))
            if definition.id == "feed_cat":
                self.pet_system.feed()
            if definition.destination:
                # she walked home after the errand (§30: space affects behavior)
                self.character.location = "livingroom"
            if definition.id in ("eat_pudding", "eat_cake", "eat_fruit"):
                self.knowledge["fridge_stock"] = {
                    "known": True,
                    "source": "observation",
                    "learned_at": float(self._clock()),
                    "data": {},
                }
            # groceries consumed empty the fridge → replenishment awareness
            if self.inventories.get("fridge").count("可乐") == 0:
                self.knowledge["fridge_no_cola"] = {
                    "known": True,
                    "source": "observation",
                    "learned_at": float(self._clock()),
                    "data": {},
                }

        await self._transaction(apply, label=f"complete:{action.definition_id}")
        await self._append_event(
            "action_completed",
            f"{definition.name}结束" + (f"（{action.detail}）" if action.detail else ""),
            source=EventSource.character_action,
            level=EventLevel.normal if definition.typical_minutes >= 20 else EventLevel.micro,
            reason="natural_completion",
        )
        self._notes.append(f"{definition.name}做完了")
        self.current_action = None
        await self.store.save_action(action)
        await self._sync_state()

    async def _decide_and_apply(self, *, space_id: str) -> SandboxDecision:
        decision, trace = await self.engine.decide_next(
            space_id=space_id, current=self.current_action, asleep=self._is_asleep()
        )
        if decision.decision in ("switch", "interrupt") and decision.action_id:
            current_definition = self.current_action.definition_id if self.current_action else ""
            # Already doing the right thing (a critical need can re-fire every
            # tick): let it run to completion instead of restarting it.
            if (
                decision.action_id == current_definition
                and self.current_action is not None
                and not self.actions.is_due(self.current_action)
            ):
                return decision
            ok, reason = self.validator.validate(decision.action_id, space_id)
            if not ok:
                trace.factors.append(f"rejected:{reason}")
                decision = SandboxDecision(
                    decision="continue",
                    action_id=decision.action_id,
                    reason_codes=["validator_rejected", reason],
                )
            else:
                await self._start_action(decision.action_id, reason=decision.reason_codes[:1])
        if self.current_action is not None:
            definition = self.actions.definition(self.current_action)
            if (
                decision.decision == "extend"
                and definition is not None
                and self.actions.can_extend(self.current_action, definition)
            ):
                extra = self.actions.extend(self.current_action, definition)
                if extra:
                    trace.factors.append(f"extended:{int(extra)}m")
        await self.store.append_trace(trace)
        return decision

    async def _start_action(
        self, action_id: str, *, reason: list[str] | None = None, space_id: str | None = None
    ) -> ActionInstance | None:
        definition = self.actions.definitions.get(action_id)
        if definition is None:
            return None
        target_space = space_id or self._space_for(definition)
        urgency = max(
            (self.needs.pressure(key) for key in definition.need_relief),
            default=0.0,
        )
        if definition.destination:
            target_space = definition.destination
        instance = self.actions.start(
            definition,
            space_id=target_space,
            reason_code=(reason[0] if reason else "decision"),
            urgency=urgency,
        )
        await self._transaction(
            lambda: setattr(self, "current_action", instance), label=f"start:{action_id}"
        )
        self.character.location = target_space
        self.character.current_action_id = instance.id
        await self.store.save_action(instance)
        await self._sync_state()
        if definition.typical_minutes >= 20:
            await self._append_event(
                "action_started",
                f"开始{definition.name}" + (f"（{instance.detail}）" if instance.detail else ""),
                source=EventSource.character_action,
                level=EventLevel.micro,
                reason=instance.reason_code,
            )
            narrate().world(
                f"{definition.name}" + (f"·{instance.detail}" if instance.detail else ""),
                detail=f"在{self.spaces.name(target_space)}",
            )
        return instance

    def _space_for(self, definition: ActionDefinition) -> str:
        if "*" in definition.spaces:
            return self.character.location
        if self.character.location in definition.spaces:
            return self.character.location
        return definition.spaces[0] if definition.spaces else self.character.location

    # ------------------------------------------------------- external events

    async def handle_external(self, event: ExternalEvent) -> dict[str, Any]:
        """QQ message / package / admin: enters the world (§49-§53)."""
        event.handled = False
        meaning, reason = self.rules.external_influence_action(event)
        event.data["meaning"] = meaning
        definition = self.actions.definition(self.current_action)
        interrupt, why = self.interrupt.evaluate(
            event, current=self.current_action, definition=definition
        )
        result: dict[str, Any] = {
            "meaning": meaning,
            "reason": reason,
            "interrupt": interrupt,
            "why": why,
        }
        if event.kind in ("user_message", "group_mention"):
            # social stimulus: it never *silently* rewrites her activity (§52)
            self._private_chat_until = float(self._clock()) + 1800.0
            friend_like = bool(event.data.get("familiar", True))
            self.needs.add("social_need", -0.08 if friend_like else -0.04)
            self.social_spaces_touch(event)
            if interrupt and self.current_action is not None:
                await self._interrupt_action(why)
                if meaning == "invitation_game":
                    await self._start_action("play_minecraft", reason=["core_friend_invitation"])
                    result["action"] = "play_minecraft"
                    await self._append_event(
                        "interrupt",
                        "空凛喊她联机 → 放下手上的事去开服务器",
                        source=EventSource.external_event,
                        level=EventLevel.normal,
                        reason="core_friend_invitation",
                    )
        elif event.kind == "delivery_arrived":
            obj = self.objects.get("package_box")
            if obj is not None:
                obj.state["present"] = True
            await self._append_event(
                "delivery",
                "快递到了（放在门口）",
                source=EventSource.external_event,
                level=EventLevel.micro,
                reason="delivery_arrived",
            )
            self.needs.add("household_maintenance", 0.05)
        elif event.kind == "admin":
            await self._append_event(
                "admin",
                event.summary or "管理员事件",
                source=EventSource.admin,
                level=EventLevel.normal,
                reason=event.reason_code or "manual",
            )
        event.handled = True
        await self.store.append_trace(
            DecisionTrace(
                ts=float(self._clock()),
                kind="external_event",
                summary=event.summary or event.kind,
                reason_code=why,
                factors=[meaning, f"priority:{event.priority.value}"],
            )
        )
        await self._persist_deltas()
        return result

    async def _interrupt_action(self, why: str) -> None:
        action = self.current_action
        if action is None:
            return
        definition = self.actions.definition(action)
        await self._transaction(
            lambda: self.actions.finish(action, ActionStatus.interrupted),
            label=f"interrupt:{action.definition_id}",
        )
        await self.store.save_action(action)
        await self._append_event(
            "action_interrupted",
            f"{definition.name if definition else action.definition_id}被打断",
            source=EventSource.external_event,
            level=EventLevel.normal,
            reason=why,
        )
        self.current_action = None

    def social_spaces_touch(self, event: ExternalEvent) -> None:
        space_id = event.social_space_id
        if space_id and space_id in self.social_spaces:
            social = self.social_spaces[space_id]
            social.character_presence = "active"
            social.social_temperature = min(1.0, social.social_temperature + 0.05)
        elif event.group_id:
            found = self.social_spaces.get(f"qq:{event.group_id}")
            if found is not None:
                found.character_presence = "active"

    async def notify(self, event: ExternalEvent) -> dict[str, Any]:
        """Event-driven wakeup (§70): handle immediately, don't wait for tick."""
        return await self.handle_external(event)

    # -------------------------------------------------- initiative support

    def _day_key(self) -> str:
        import time as _time

        stamp = _time.localtime(float(self._clock()))
        return f"{stamp.tm_year}-{stamp.tm_mon}-{stamp.tm_mday}"

    async def background_budget_left(self) -> int:
        """Daily cap on *background messages* (separate from her life)."""
        cap = int(getattr(self.config, "max_background_messages_per_day", 3))
        key = await self.store.state_get("bg_msgs_day")
        used_raw = await self.store.state_get("bg_msgs_used")
        used = int(used_raw or 0) if key == self._day_key() else 0
        return max(0, cap - used)

    async def note_background_message(self) -> None:
        key = self._day_key()
        stored = await self.store.state_get("bg_msgs_day")
        used = int(await self.store.state_get("bg_msgs_used") or 0) if stored == key else 0
        await self.store.state_set("bg_msgs_day", key)
        await self.store.state_set("bg_msgs_used", str(used + 1))

    async def life_moment(self) -> tuple[str, str]:
        """A recent shareable life event (finished something / project news).

        Feeds the initiative topic pool — she may mention each moment at most
        once (surfaced ids ride ``sandbox_state``).
        """
        try:
            events = await self.store.recent_events(limit=12)
        except Exception:  # noqa: BLE001 - her life is an optional input
            events = []
        if not events:
            events = [
                {
                    "id": record.id,
                    "kind": record.kind,
                    "summary": record.summary,
                    "created_at": record.created_at,
                }
                for record in reversed(self._recent_memory[-12:])
            ]
        surfaced = await self._surfaced_moments()
        shareable = {"action_completed", "commission", "delivery", "sandbox_initialized"}
        for row in events:
            kind = str(row.get("kind", ""))
            event_id = str(row.get("id", ""))
            if kind in shareable and event_id not in surfaced:
                summary = str(row.get("summary", "")).strip()
                if summary:
                    return summary, event_id
        return "", ""

    async def _surfaced_moments(self) -> set[str]:
        raw = await self.store.state_get("surfaced_moments")
        if not raw:
            return set()
        try:
            import json as _json

            data = _json.loads(raw)
            return {str(item) for item in data} if isinstance(data, list) else set()
        except (TypeError, ValueError):
            return set()

    async def note_moment_surfaced(self, event_id: str) -> None:
        """A life moment became a message — never mention it twice."""
        if not event_id:
            return
        import json as _json

        surfaced = await self._surfaced_moments()
        surfaced.add(str(event_id))
        keep = list(surfaced)[-50:]
        await self.store.state_set("surfaced_moments", _json.dumps(keep))

    # ------------------------------------------------------------- facts

    def facts_for(self, text: str) -> Any:
        """Tag-driven world facts for this text (see app/sandbox/facts.py)."""
        from app.sandbox.facts import FactSelector

        selector = getattr(self, "_fact_selector", None)
        if selector is None:
            selector = FactSelector(self)
            self._fact_selector = selector
        return selector.select(text)

    def facts_block(self, text: str) -> str:
        """Facts + the must-not-invent constraint, narrated for the operator."""
        selection = self.facts_for(text)
        if selection.empty:
            self._log.debug("[Facts] no entity matched for %r", (text or "")[:40])
            return ""
        labels = "、".join(entry_id.split(":")[-1] for entry_id in selection.hits)
        narrate().say(
            "facts",
            f"命中标签 → 注入 {len(selection.lines)} 条世界事实",
            detail=f"{labels} · " + "；".join(selection.lines),
        )
        return selection.block()

    def audit_claims(self, text: str) -> str:
        """Strip sentences claiming something is gone while the sandbox says not."""
        from app.sandbox.facts import FactSelector

        selector = getattr(self, "_fact_selector", None)
        if selector is None:
            selector = FactSelector(self)
            self._fact_selector = selector
        cleaned, _violations = selector.audit_claims(text)
        return cleaned

    async def note_agent_result(
        self,
        *,
        task_type: str,
        status: str,
        summary: str,
        session_id: str = "",
        user_id: str = "",
    ) -> None:
        """An agent task finished — a life event, not a state command (§93-§94)."""
        done = status in ("completed", "partial")
        self.needs.add("social_need", -0.05 if done else 0.0)
        self._notes.append(summary)
        await self._append_event(
            "agent_result",
            summary,
            source=EventSource.user_interaction,
            level=EventLevel.micro,
            reason=f"agent:{task_type}:{status}",
        )

    async def note_user_interaction(self, *, user_id: str, session_id: str) -> None:
        """A QQ user started talking: a social stimulus, not a command (§52)."""
        self.needs.add("social_need", -0.05)
        self._private_chat_until = float(self._clock()) + 1800.0
        space_id = session_id.split(":", 1)[1] if ":" in session_id else ""
        if session_id.startswith("group:") and space_id:
            social = self.social_spaces.get(f"qq:{space_id}")
            if social is not None:
                social.character_presence = "active"

    async def _process_pending_events(self) -> None:
        while self.pending_events:
            event = self.pending_events.pop(0)
            try:
                await self.handle_external(event)
            except Exception:  # noqa: BLE001 - an event must never kill the world
                self._log.exception("[Sandbox] pending event failed")

    # ----------------------------------------------------- recovery / replay

    async def _settle_gap(self, elapsed_minutes: float) -> None:
        """Bounded settle after downtime (§72): no per-tick replay."""
        hours = elapsed_minutes / 60.0
        assume_asleep = self._was_sleeping_through(elapsed_minutes)
        self.needs.advance(min(elapsed_minutes, 24 * 60), rest=1.0 if assume_asleep else 0.0)
        self.pet_system.advance(min(elapsed_minutes, 12 * 60))
        note: list[str] = [f"离线 {hours:.1f} 小时"]

        if self.current_action is not None:
            definition = self.actions.definition(self.current_action)
            leftover = (self.current_action.planned_end_at - self._last_tick) / 60.0
            if leftover <= elapsed_minutes:
                # It finished while offline: settle once, not N times.
                await self._complete_action()
                note.append(f"{definition.name if definition else '某件事'}在离线期间做完了")
                await self._decide_and_apply(space_id=self.character.location)
            else:
                self.current_action.started_at += elapsed_minutes * 60.0
                self.current_action.planned_end_at += elapsed_minutes * 60.0
                note.append("回来后接着做手上的事")

        # Morning recovery: slept long enough → awake, day starts (§73 Recovery / §72 恢复).
        hour = time.localtime(float(self._clock())).tm_hour
        if assume_asleep and not (2 <= hour < 5) and self.current_action is not None:
            definition = self.actions.definition(self.current_action)
            if definition is not None and "rest" in definition.tags:
                await self._complete_action()
                note.append("睡醒了")
                await self._decide_and_apply(space_id=self.character.location)

        self._last_tick = float(self._clock())
        await self._append_event(
            "recovery",
            "；".join(note),
            source=EventSource.system,
            level=EventLevel.normal,
            reason="bounded_recovery",
        )
        await self._snapshot()

    def _was_sleeping_through(self, elapsed_minutes: float) -> bool:
        if self.current_action is None:
            return False
        definition = self.actions.definition(self.current_action)
        return definition is not None and "rest" in definition.tags

    async def replay(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """§190: what happened, in order — from events + traces (no CoT)."""
        events = await self.store.recent_events(limit=limit)
        traces = await self.store.recent_traces(limit=limit)
        merged = [
            *(
                {
                    "ts": row["created_at"],
                    "kind": row["kind"],
                    "summary": row["summary"],
                    "reason": row["reason_code"],
                    "level": row["priority"],
                }
                for row in events
            ),
            *(
                {
                    "ts": row["ts"],
                    "kind": f"trace:{row['kind']}",
                    "summary": row["summary"],
                    "reason": row["reason_code"],
                    "level": "trace",
                }
                for row in traces
            ),
        ]
        merged.sort(key=lambda item: item["ts"])
        return merged

    async def simulate(self, hours: float, *, step_minutes: float = 10.0) -> dict[str, Any]:
        """Dry-run simulation (§169-§171): advances in memory, never QQ, no LLM.

        Used by tests and the WebUI simulator. State changes are real in-memory
        changes for the duration of the run — callers snapshot/restore if needed.
        """
        ticks = max(1, int(hours * 60 / step_minutes))
        completed = 0
        events: list[str] = []
        for _ in range(ticks):
            before = self.current_action.id if self.current_action else ""
            report = await self.tick(minutes=step_minutes)
            after = self.current_action.id if self.current_action else ""
            if report.get("completed"):
                completed += 1
            if before and after and before != after:
                definition = self.actions.definition(self.current_action)
                if definition is not None:
                    events.append(definition.name)
            if len(events) > 200:
                break
        return {
            "hours": hours,
            "ticks": ticks,
            "completed_actions": completed,
            "transitions": events[-40:],
        }

    # ------------------------------------------------------------- snapshot

    async def _snapshot(self, *, persist: bool = True) -> SandboxSnapshot:
        snapshot = SandboxSnapshot(
            created_at=float(self._clock()),
            elapsed_minutes=max(0.0, (float(self._clock()) - self._last_tick) / 60.0),
            phase=self.phase.value,
            location=self.character.location,
            character=self.character.model_dump(mode="json"),
            pet=self.pet_system.snapshot(),
            needs={key: need.model_dump(mode="json") for key, need in self.needs.all().items()},
            action=(self.current_action.model_dump(mode="json") if self.current_action else None),
            modes=self.modes.snapshot(),
            spaces=[space.model_dump(mode="json") for space in self.spaces.all()],
            objects=[obj.model_dump(mode="json") for obj in self.objects.all()],
            inventories={
                key: inv.model_dump(mode="json") for key, inv in self.inventories.all().items()
            },
            social_spaces=[space.model_dump(mode="json") for space in self.social_spaces.values()],
            commissions=[c.model_dump(mode="json") for c in self.commissions.values()],
            knowledge=dict(self.knowledge),
            projects=dict(self.projects),
            bible_version=self.bible.version,
        )
        if persist:
            await self.store.save_snapshot(
                snapshot, keep=int(getattr(self.config, "snapshot_keep", 48))
            )
        return snapshot

    async def _restore(self) -> bool:
        snapshot = await self.store.latest_snapshot()
        if snapshot is None:
            return False
        try:
            self._apply_snapshot(snapshot)
            self._restored = True
            self._log.info(
                "[Sandbox] restored (location=%s, action=%s)",
                self.character.location,
                self.current_action.definition_id if self.current_action else "-",
            )
            return True
        except Exception:  # noqa: BLE001 - bad snapshot → reseed instead of crashing
            self._log.exception("[Sandbox] snapshot restore failed; will reseed")
            return False

    def _apply_snapshot(self, snapshot: SandboxSnapshot) -> None:
        self.character = CharacterEntity.model_validate(snapshot.character)
        self.pet_system.restore(snapshot.pet)
        self.needs.restore(snapshot.needs)
        if snapshot.action:
            self.current_action = ActionInstance.model_validate(snapshot.action)
        else:
            self.current_action = None
        self.modes.restore(snapshot.modes)
        if snapshot.spaces:
            self.spaces = SpaceSystem([SpaceNode.model_validate(s) for s in snapshot.spaces])
        if snapshot.objects:
            self.objects = ObjectSystem(
                [WorldObjectItem.model_validate(o) for o in snapshot.objects]
            )
        if snapshot.inventories:
            from app.sandbox.models import Inventory

            self.inventories = InventorySystem(
                {key: Inventory.model_validate(data) for key, data in snapshot.inventories.items()}
            )
        if snapshot.social_spaces:
            self.social_spaces = {
                item["id"]: SocialSpace.model_validate(item) for item in snapshot.social_spaces
            }
        if snapshot.commissions:
            self.commissions = {
                item["id"]: Commission.model_validate(item) for item in snapshot.commissions
            }
        self.knowledge = dict(snapshot.knowledge)
        self.projects = dict(snapshot.projects or self.projects)
        created = snapshot.created_at or float(self._clock())
        self._last_tick = created

    # ---------------------------------------------------------- persistence

    async def _persist_all(self) -> None:
        await self.store.save_entity(
            "character",
            type="character",
            name=self.character.name,
            space_id=self.character.location,
            data=self.character.model_dump(mode="json"),
        )
        await self.store.save_entity(
            "pet",
            type="pet",
            name=self.pet_system.pet.name,
            space_id=self.pet_system.pet.location,
            data=self.pet_system.snapshot(),
        )
        for space in self.spaces.all():
            await self.store.save_space(
                space.id,
                name=space.name,
                parent_id=space.parent_id,
                kind=space.kind.value,
                data=space.model_dump(mode="json"),
            )
        for obj in self.objects.all():
            await self.store.save_object(
                obj.id,
                name=obj.name,
                space_id=obj.space_id,
                kind=obj.kind,
                data=obj.model_dump(mode="json"),
            )
        for key, inventory in self.inventories.all().items():
            await self.store.save_inventory(key, inventory.model_dump(mode="json"))
        for key, need in self.needs.all().items():
            await self.store.save_inventory(  # needs ride the same upsert
                f"need:{key}", need.model_dump(mode="json")
            )
        for social in self.social_spaces.values():
            await self.store.save_social_space(
                social.id,
                name=social.name,
                kind=social.kind,
                data=social.model_dump(mode="json"),
            )
        for commission in self.commissions.values():
            await self.store.save_commission(
                commission.id,
                kind=commission.kind,
                status=commission.status,
                progress=commission.progress,
                deadline=commission.deadline,
                reward=commission.reward,
                data=commission.model_dump(mode="json"),
            )
        for key, entry in self.knowledge.items():
            await self.store.save_knowledge(
                key,
                known=bool(entry.get("known")),
                source=str(entry.get("source", "")),
                learned_at=float(entry.get("learned_at", 0) or 0),
                data=dict(entry.get("data", {}) or {}),
            )
        await self.store.state_set("last_tick", str(self._last_tick))
        await self.store.state_set("projects", _json(self.projects))

    async def _persist_deltas(self) -> None:
        await self.store.save_entity(
            "character",
            type="character",
            name=self.character.name,
            space_id=self.character.location,
            data=self.character.model_dump(mode="json"),
        )
        await self.store.save_entity(
            "pet",
            type="pet",
            name=self.pet_system.pet.name,
            space_id=self.pet_system.pet.location,
            data=self.pet_system.snapshot(),
        )
        for key in ("fridge", "cat_food_bowl", "character"):
            inventory = self.inventories.get(key)
            await self.store.save_inventory(key, inventory.model_dump(mode="json"))
        for key in self.knowledge:
            entry = self.knowledge[key]
            await self.store.save_knowledge(
                key,
                known=bool(entry.get("known")),
                source=str(entry.get("source", "")),
                learned_at=float(entry.get("learned_at", 0) or 0),
                data=dict(entry.get("data", {}) or {}),
            )

    async def _append_event(
        self,
        kind: str,
        summary: str,
        *,
        source: EventSource,
        level: EventLevel,
        reason: str,
    ) -> None:
        record = SandboxEventRecord(
            id=f"evt_{uuid.uuid4().hex[:10]}",
            kind=kind,
            level=level,
            source=source,
            summary=summary,
            reason_code=reason,
            created_at=float(self._clock()),
        )
        await self.store.append_event(record)
        self._recent_memory.append(record)
        del self._recent_memory[:-30]
        # v1.2 §139: meaningful world events become her recent continuity.
        if (
            self.continuity is not None
            and level in (EventLevel.major, EventLevel.normal)
            and kind not in ("recovery",)
        ):
            try:
                await self.continuity.add_micro_event(
                    summary, kind="world", reason_code=reason or kind
                )
            except Exception:  # noqa: BLE001 - continuity must never break the world
                self._log.debug("[Sandbox] continuity feed failed", exc_info=True)

    # ---------------------------------------------------------- transaction

    async def _transaction(self, mutation: Callable[[], None], *, label: str) -> bool:
        """Atomic-ish mutation (§127/§128): rollback on failure."""
        backup = self._backup_state()
        try:
            mutation()
            return True
        except Exception:  # noqa: BLE001 - restore consistency, keep running
            self._log.exception("[Sandbox] transaction failed: %s — rolling back", label)
            self._restore_state(backup)
            return False

    def _backup_state(self) -> dict[str, Any]:
        return {
            "action": self.current_action.model_copy(deep=True) if self.current_action else None,
            "needs": {k: n.model_copy(deep=True) for k, n in self.needs.all().items()},
            "inventories": {k: v.model_copy(deep=True) for k, v in self.inventories.all().items()},
            "objects": [o.model_copy(deep=True) for o in self.objects.all()],
            "projects": {k: dict(v) for k, v in self.projects.items()},
            "pet": self.pet_system.pet.model_copy(deep=True),
        }

    def _restore_state(self, backup: dict[str, Any]) -> None:
        self.current_action = backup["action"]
        for key, need in backup["needs"].items():
            self.needs.all()[key] = need
        for key, inv in backup["inventories"].items():
            self.inventories.all()[key] = inv
        self.objects = ObjectSystem(backup["objects"])
        self.projects = backup["projects"]
        self.pet_system.pet = backup["pet"]

    # ------------------------------------------------------------- helpers

    def _is_asleep(self) -> bool:
        if self.current_action is None:
            return False
        definition = self.actions.definition(self.current_action)
        return definition is not None and "rest" in definition.tags

    def _rest_coefficient(self) -> float:
        if self.current_action is None:
            return 0.0
        definition = self.actions.definition(self.current_action)
        if definition is None or "rest" not in definition.tags:
            return 0.0
        return 1.0 if "core" in definition.tags else 0.5

    def _should_snapshot(self) -> bool:
        stride = max(1, int(60.0 / max(1.0, float(self.config.tick_seconds) / 60.0)))
        return int(self._rng.random() * stride) == 0

    async def _sync_state(self) -> None:
        """Project her life onto CharacterState (WebUI/prompt/timing agree)."""
        if self.state_sync is None:
            return
        definition = self.actions.definition(self.current_action)
        activity = ACTIVITY_IDS.get(definition.id, definition.id) if definition else "idle"
        location = self.spaces.name(self.character.location)
        energy = max(0.0, min(1.0, 1.0 - self.needs.level("energy")))
        try:
            await self.state_sync(activity, location, energy)
        except Exception:  # noqa: BLE001 - projection must never break the world
            self._log.debug("[Sandbox] state sync failed", exc_info=True)

    def _derive_modes(self) -> list[str]:
        definition = self.actions.definition(self.current_action)
        hour = time.localtime(float(self._clock())).tm_hour
        social_active = (
            any(space.character_presence == "active" for space in self.social_spaces.values())
            or float(self._clock()) < self._private_chat_until
        )
        modes = self.modes.derive(
            space_id=self.character.location,
            hour=hour,
            definition=definition,
            social_active=social_active,
            is_home=self.spaces.is_home(self.character.location),
        )
        entered = self.modes.update(modes)
        self.character.modes = modes
        return entered

    def _advance_commission(self, amount: float) -> None:
        active = next(
            (c for c in self.commissions.values() if c.status in ("open", "active")),
            None,
        )
        if active is None:
            return
        active.status = "active"
        active.progress = min(1.0, active.progress + amount)
        if active.progress >= 1.0:
            active.status = "delivered"
            self.needs.relieve({"work_need": 1.0})

    # -------------------------------------------------------- context output

    def context(self) -> dict[str, Any]:
        """The current-world slice for the character prompt (§85-§87).

        Deliberately small: where she is, what she is doing (with detail),
        the mode, the cat, notable needs and the active project — never the
        whole sandbox (§86).
        """
        definition = self.actions.definition(self.current_action)
        action_line = ""
        if definition is not None:
            action_line = definition.name
            if self.current_action and self.current_action.detail:
                action_line += f"（{self.current_action.detail}）"
        elif self._is_asleep() is False and self.current_action is None:
            action_line = "闲着"
        location = self.spaces.name(self.character.location)
        pieces = [f"现在她在{location}"]
        if action_line:
            pieces.append(f"正在{action_line}")
        pet = self.pet_system.prompt_line()
        project = next(iter(self.projects.values()), None)
        goal = ""
        if project is not None:
            goal = f"{project['name']}：{project.get('next_action', '')}"
        commission = next(
            (c for c in self.commissions.values() if c.status in ("open", "active")),
            None,
        )
        if commission is not None:
            goal = f"{commission.title or commission.kind}（进度 {commission.progress:.0%}）"
        return {
            "state_line": "；".join(pieces) + f"；{pet}",
            "mode_line": self.modes.prompt_line(),
            "need_line": self.needs.summary_line(),
            "goal": goal,
            "events": list(self._notes[-3:]),
            "location": location,
            "action": action_line,
            "modes": self.modes.ids(),
            "pet": pet,
        }

    def status_line(self) -> str:
        definition = self.actions.definition(self.current_action)
        where = self.spaces.name(self.character.location)
        doing = definition.name if definition is not None else "闲着"
        return f"{doing}（{where}）"

    def snapshot_dict(self) -> dict[str, Any]:
        return self.context() | {"phase": self.phase.value}


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
