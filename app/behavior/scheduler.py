"""BehaviorScheduler: one long-running background tick for all behaviour.

A single task wakes every ``tick_seconds`` and checks activity, state decay and
initiative candidates — never one task per user (spec v0.8 §48). Counters live in
SQLite, so limits survive a restart (spec v0.8 §49). Every step is isolated: one
failing behaviour must not stop the scheduler (spec v0.8 §74).

Since v0.8 this is also the **only** scheduler in the project (spec v0.8 §34): the
persistent world, memory maintenance and agent housekeeping register
:class:`ScheduledJob` entries here instead of spawning loops of their own.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.behavior.models import BehaviorEvent, ScheduledJob

if TYPE_CHECKING:
    from app.behavior.engine import CharacterBehaviorEngine
    from app.core.bot import Bot

DEFAULT_TICK_SECONDS = 30.0


class BehaviorScheduler:
    def __init__(
        self,
        bot: Bot,
        behavior: CharacterBehaviorEngine,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._bot = bot
        self._behavior = behavior
        self._tick_seconds = max(1.0, tick_seconds)
        self._log = logger or logging.getLogger("CatooBot.Behavior")
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._jobs: dict[str, ScheduledJob] = {}
        self.ticks = 0
        self.errors = 0
        #: scope -> the rejection reason already reported at INFO; a pass that
        #: keeps being turned down for the same reason is DEBUG noise.
        self._gate_reason_seen: dict[str, str] = {}

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="behavior-scheduler")
        self._log.info(
            "[Behavior] Scheduler started (tick=%ss, initiative=%s)",
            int(self._tick_seconds),
            "on" if self._behavior.initiative.enabled else "off",
        )

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
            self._log.info("[Behavior] Scheduler stopped")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------ loop

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive anything
                self.errors += 1
                self._log.exception("[Behavior] Scheduler tick failed, continuing")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._tick_seconds)
            except TimeoutError:
                continue

    async def tick(self) -> None:
        """One behaviour pass. Never raises to the caller."""
        self.ticks += 1
        await self._safe("activity", self._behavior.tick())
        if self._behavior.initiative.enabled:
            await self._safe("initiative", self._initiative_pass())
        # Agent housekeeping rides the existing scheduler (spec v0.8 §103/§104):
        # timeouts and stale tasks only — no personality work here.
        agent = getattr(self._bot, "agent", None)
        if agent is not None and agent.enabled:
            await self._safe("agent_housekeeping", agent.housekeeping())
        await self._run_jobs()

    # ------------------------------------------------------------ job registry

    def register_job(self, job: ScheduledJob) -> None:
        """Register (or replace) a named background job on this scheduler."""
        self._jobs[job.name] = job
        self._log.info(
            "[Scheduler] Job registered: %s (every %ss, misfire=%s)",
            job.name,
            int(job.interval_seconds),
            job.misfire_policy,
        )

    def unregister_job(self, name: str) -> None:
        self._jobs.pop(name, None)

    def jobs(self) -> list[ScheduledJob]:
        return list(self._jobs.values())

    async def _run_jobs(self) -> None:
        now = time.time()
        day_key = self._day_key()
        for job in list(self._jobs.values()):
            if not job.due(now, day_key):
                continue
            overdue = (now - job.last_run) if job.last_run else 0.0
            if job.misfire_policy == "skip" and overdue > job.interval_seconds * 2:
                # Missed windows are dropped, never stacked (spec v0.8 §33).
                job.last_run = now
                job.detail = "skipped missed window"
                self._log.info("[Scheduler] %s: missed window skipped", job.name)
                continue
            await self._safe(f"job:{job.name}", self._run_job(job, now))

    async def _run_job(self, job: ScheduledJob, now: float) -> None:
        job.last_run = now
        job.runs += 1
        job.runs_today += 1
        try:
            await job.handler()
            job.detail = "ok"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - isolated per job (spec v0.8 §74)
            job.failures += 1
            job.detail = f"{type(exc).__name__}: {exc}"[:200]
            self.errors += 1
            self._log.exception("[Scheduler] Job %s failed", job.name)

    def _day_key(self) -> str:
        return time.strftime("%Y-%m-%d")

    async def _background_budget_ok(self) -> bool:
        """Global daily cap on *background* messages (spec v0.8 §42/§105).

        Background life may happen all day; messaging someone about it is a
        separate, much smaller budget.
        """
        sandbox = getattr(self._bot, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            return True
        left = await sandbox.background_budget_left()
        if left <= 0:
            self._log.info(
                "[Initiative] Daily background-message budget spent (%s)",
                self._bot.config.sandbox.max_background_messages_per_day,
            )
            return False
        return True

    async def _world_moment(self) -> tuple[str, str]:
        """A recent shareable life event — surfaced at most once (v2.0)."""
        sandbox = getattr(self._bot, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            return "", ""
        try:
            return await sandbox.life_moment()
        except Exception:  # noqa: BLE001 - her life is an optional input
            self._log.debug("[Initiative] Life moment lookup failed", exc_info=True)
            return "", ""

    async def _safe(self, name: str, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self.errors += 1
            self._log.exception("[Behavior] %s step failed", name)

    # ------------------------------------------------------------ initiative

    async def _initiative_pass(self) -> None:
        if self._bot.character is None:
            return
        if not await self._background_budget_ok():
            return
        moment_text, moment_event = await self._world_moment()
        self._last_moment_event = moment_event
        relationships = await self._bot.relationships.all()
        for relationship in relationships:
            scope_key = f"private:{relationship.user_id}"
            try:
                user_enabled = await self._user_initiative_enabled(relationship.user_id)
                candidates = await self._behavior.initiative.build_candidates(
                    scope_key=scope_key,
                    user_id=relationship.user_id,
                    last_seen=relationship.last_seen,
                    relationship_stage=relationship.stage,
                    world_moment=moment_text,
                )
                if not candidates:
                    continue
                for candidate in candidates:
                    await self._behavior.initiative.mark_candidate(scope_key, candidate.reason)
                    gate = await self._behavior.initiative.evaluate(
                        candidate,
                        relationship_stage=relationship.stage,
                        user_enabled=user_enabled,
                        last_seen=relationship.last_seen,
                    )
                    if not gate.allowed:
                        changed = self._gate_reason_seen.get(scope_key) != gate.reason
                        self._gate_reason_seen[scope_key] = gate.reason
                        self._log.log(
                            logging.INFO if changed else logging.DEBUG,
                            "[Initiative] Gate rejected: %s (scope=%s)%s",
                            gate.reason,
                            scope_key,
                            "" if changed else " — unchanged, see the first line",
                        )
                        await self._behavior.initiative.mark_skipped(
                            scope_key, gate.reason, gate.detail
                        )
                        continue
                    self._gate_reason_seen.pop(scope_key, None)
                    if await self._send_initiative(scope_key, relationship.user_id, candidate):
                        break  # one proactive message per user per pass
            except Exception:  # noqa: BLE001 - one user must not stop the pass
                self._log.exception("[Initiative] Failed for scope %s", scope_key)

    async def _send_initiative(self, scope_key: str, user_id: str, candidate) -> bool:
        bot = self._bot
        message = await bot.character.compose_initiative(
            session_id=scope_key,
            user_id=user_id,
            reason=candidate.reason,
            topic=candidate.topic,
        )
        if not message:
            self._log.info("[Initiative] Empty generation, skipped (scope=%s)", scope_key)
            await self._behavior.initiative.mark_skipped(scope_key, "empty_generation")
            return False

        plan = bot.response_planner.plan_reply(
            message,
            state=await bot.character.states.load(),
            relationship=await bot.relationships.get(user_id),
            time_context=self._behavior.time_context(),
        )
        target = bot.response_delivery.target_for_user(int(user_id))
        sent = await bot.response_delivery.deliver(target, plan)
        if not sent:
            self._log.info(
                "[Initiative] Delivery failed (QQ offline?) — dropped, scope=%s", scope_key
            )
            return False

        await self._behavior.initiative.record_sent(
            scope_key, message, reason=candidate.reason, user_id=user_id, topic=candidate.topic
        )
        await self._mark_moment_surfaced(candidate)
        # The character remembers what she said, so the topic can continue.
        await bot.ai.conversations.append_assistant_message(scope_key, message)
        self._log.info(
            "[Initiative] Sent to %s (reason=%s, topic=%.30s)",
            scope_key,
            candidate.reason,
            candidate.topic or "-",
        )
        return True

    async def _mark_moment_surfaced(self, candidate) -> None:
        """A life moment that became a message is never mentioned twice."""
        if getattr(candidate, "reason", "") != "life_event":
            return
        event_id = getattr(self, "_last_moment_event", "")
        if not event_id:
            return
        sandbox = getattr(self._bot, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            return
        try:
            await sandbox.note_moment_surfaced(event_id)
        except Exception:  # noqa: BLE001
            self._log.debug("[Initiative] Marking moment surfaced failed", exc_info=True)

    async def _user_initiative_enabled(self, user_id: str) -> bool:
        try:
            row = await self._bot.database.fetchone(
                "SELECT initiative_enabled FROM user_profiles WHERE user_id = ?", (str(user_id),)
            )
        except Exception:  # noqa: BLE001
            return True
        if row is None:
            return True
        return bool(row["initiative_enabled"])

    # ---------------------------------------------------------------- audit

    async def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "ticks": self.ticks,
            "errors": self.errors,
            "tick_seconds": self._tick_seconds,
            "started_at": getattr(self, "_started_at", None),
        }

    async def record_manual(self, action: str, detail: str = "") -> None:
        await self._behavior.log_event(
            BehaviorEvent(
                type=f"manual_{action}",
                reason="webui",
                detail=detail,
                created_at=int(time.time()),
                status="done",
            )
        )
