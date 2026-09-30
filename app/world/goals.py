"""PersistentGoalService: goals that live longer than one conversation.

A goal has progress, milestones and a *next action*, and it advances in small
bounded steps (spec §29-§31):

* at most ``max_active`` goals exist at once (a character with 30 goals is a
  task list, not a life);
* progress events are capped per day — the character does not finish a novel
  before lunch;
* automatic advancement is **opt-in** and interval-based, and it only ever
  moves progress a little and emits an idempotent life event;
* goals are never deleted in a way that loses history: they end as
  ``done``/``abandoned`` (spec §20 deletion-free rule applies here too).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.config.settings import WorldGoalsConfig
from app.world.clock import WorldClock
from app.world.errors import GoalError, GoalLimitError
from app.world.models import CharacterProject, Milestone, PersistentGoal
from app.world.state import WorldStateService

PROGRESS_STEP = 0.08          # one background nudge
MAX_STEP = 0.4                # one manual advance can move at most this much




class PersistentGoalService:
    def __init__(
        self,
        *,
        database: Any,
        clock: WorldClock,
        config: WorldGoalsConfig | None = None,
        state: WorldStateService | None = None,
        events: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._db = database
        self._clock = clock
        self._config = config or WorldGoalsConfig()
        self._state = state
        self._events = events
        self._log = logger or logging.getLogger("CatooBot.World")

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def _stamp(self) -> int:
        """Wall-clock seconds from the injected world clock (test-friendly)."""
        return int(self._clock.now().timestamp())

    # --------------------------------------------------------------- create

    async def create(
        self,
        name: str,
        *,
        description: str = "",
        milestones: list[str] | None = None,
        project_id: str = "",
        priority: int = 3,
        source: str = "system",
        next_action: str = "",
        goal_id: str = "",
    ) -> PersistentGoal:
        if not self._config.enabled:
            raise GoalError("goals are disabled")
        name = (name or "").strip()
        if not name:
            raise GoalError("goal name is required")
        active = await self.all(status="active")
        if len(active) >= self._config.max_active:
            raise GoalLimitError(
                f"active goal limit reached ({self._config.max_active})"
            )
        stamp = self._stamp()
        goal = PersistentGoal(
            goal_id=goal_id or f"goal_{uuid.uuid4().hex[:10]}",
            name=name[:120],
            description=description[:500],
            status="active",
            priority=max(1, min(5, int(priority))),
            next_action=next_action[:200],
            milestones=[Milestone(name=item[:120]) for item in (milestones or [])],
            project_id=project_id,
            source=source,
            created_at=stamp,
            updated_at=stamp,
        )
        await self._save(goal)
        self._log.info("[World] Goal created: %s (%s)", goal.name, goal.goal_id)
        if self._events is not None:
            await self._events.emit(
                type="goal",
                summary=f"给自己定了个目标：{goal.name}",
                key=f"create:{goal.goal_id}",
                importance=0.5,
                source=source,
                related_goal=goal.goal_id,
                detail={"kind": "create"},
                allow_duplicate=True,
            )
        return goal

    async def _save(self, goal: PersistentGoal) -> None:
        import json

        goal.updated_at = self._stamp()
        payload = json.dumps(goal.model_dump(), ensure_ascii=False)
        await self._db.execute(
            """INSERT INTO persistent_goals
               (goal_id, name, description, status, priority, progress, next_action,
                milestones, project_id, enabled, created_at, updated_at, last_progress_at,
                completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(goal_id) DO UPDATE SET
                   name=excluded.name, description=excluded.description,
                   status=excluded.status, priority=excluded.priority,
                   progress=excluded.progress, next_action=excluded.next_action,
                   milestones=excluded.milestones, project_id=excluded.project_id,
                   enabled=excluded.enabled, updated_at=excluded.updated_at,
                   last_progress_at=excluded.last_progress_at,
                   completed_at=excluded.completed_at""",
            (
                goal.goal_id,
                goal.name,
                goal.description,
                goal.status,
                int(goal.priority),
                float(goal.progress),
                goal.next_action,
                json.dumps([m.model_dump() for m in goal.milestones], ensure_ascii=False),
                goal.project_id,
                1 if goal.enabled else 0,
                int(goal.created_at),
                int(goal.updated_at),
                int(goal.last_progress_at),
                goal.completed_at,
            ),
        )
        del payload  # kept the row explicit on purpose; JSON is for the API layer

    # ----------------------------------------------------------------- read

    async def get(self, goal_id: str) -> PersistentGoal | None:
        row = await self._db.fetchone(
            "SELECT * FROM persistent_goals WHERE goal_id = ?", (goal_id,)
        )
        return self._from_row(row) if row else None

    async def all(self, status: str = "", limit: int = 50) -> list[PersistentGoal]:
        sql = "SELECT * FROM persistent_goals"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY priority ASC, created_at ASC LIMIT ?"
        rows = await self._db.fetchall(sql, (*params, int(limit)))
        return [self._from_row(row) for row in rows]

    async def current_goal(self) -> PersistentGoal | None:
        """The goal the character is 'on' right now (highest-priority active)."""
        state = self._state.state if self._state is not None else None
        if state is not None and state.current_goal:
            goal = await self.get(state.current_goal)
            if goal is not None and goal.status == "active":
                return goal
        active = await self.all(status="active", limit=1)
        return active[0] if active else None

    async def prompt_line(self) -> str:
        goal = await self.current_goal()
        if goal is None:
            return ""
        parts = [f"你最近在做的事：{goal.name}（进度 {goal.progress_percent}%）"]
        upcoming = goal.next_open_milestone()
        if upcoming is not None:
            parts.append(f"下一小步：{upcoming.name}")
        elif goal.next_action:
            parts.append(f"下一小步：{goal.next_action}")
        return "；".join(parts)

    # --------------------------------------------------------------- advance

    async def advance(
        self,
        goal_id: str,
        *,
        amount: float = PROGRESS_STEP,
        note: str = "",
        milestone: str = "",
        next_action: str | None = None,
        reason: str = "task_completion",
        emit: bool = True,
    ) -> PersistentGoal:
        goal = await self.get(goal_id)
        if goal is None:
            raise GoalError(f"unknown goal: {goal_id}")
        if goal.status != "active":
            raise GoalError(f"goal is {goal.status}")

        step = max(0.0, min(MAX_STEP, float(amount)))
        if emit and self._events is not None:
            used = await self._progress_events_today(goal_id)
            if used >= self._config.max_progress_events_per_day:
                self._log.info(
                    "[World] Goal progress limit reached for %s (%s/day)",
                    goal.name,
                    self._config.max_progress_events_per_day,
                )
                return goal

        goal.progress = max(0.0, min(1.0, goal.progress + step))
        goal.last_progress_at = self._stamp()
        if next_action is not None:
            goal.next_action = next_action[:200]
        if milestone:
            for item in goal.milestones:
                if item.name == milestone and not item.done:
                    item.done = True
                    item.done_at = goal.last_progress_at
                    item.note = note[:200]
                    break
        summary = note or self._progress_summary(goal)
        completed = goal.progress >= 1.0
        if completed:
            goal.status = "done"
            goal.completed_at = goal.last_progress_at
        await self._save(goal)

        if await self._should_become_current(goal) and self._state is not None:
            await self._state.change(
                "task_completion" if completed else "topic_progress",
                current_goal=goal.goal_id,
                emit=False,
            )

        if emit and self._events is not None:
            if completed:
                await self._events.emit(
                    type="milestone",
                    summary=f"把「{goal.name}」做完了",
                    key=f"done:{goal.goal_id}",
                    importance=0.8,
                    source="scheduled",
                    related_goal=goal.goal_id,
                    detail={"kind": "completion"},
                    allow_duplicate=True,
                )
            else:
                await self._events.emit(
                    type="goal",
                    summary=summary,
                    key=f"progress:{goal.goal_id}:{goal.progress_percent}",
                    importance=0.5,
                    related_goal=goal.goal_id,
                    bucket=self._clock.today_key(),
                    detail={"kind": "progress"},
                )
        self._log.info(
            "[World] Goal advanced: %s -> %s%% (%s)",
            goal.name,
            goal.progress_percent,
            reason,
        )
        return goal

    async def _should_become_current(self, goal: PersistentGoal) -> bool:
        if self._state is None or goal.status != "active":
            return False
        return not self._state.state.current_goal

    async def _progress_events_today(self, goal_id: str) -> int:
        """Progress events only — creating a goal is not progress (§30)."""
        if self._events is None:
            return 0
        rows = await self._events.recent(
            limit=50,
            types=["goal", "milestone"],
            since=self._clock.start_of_day(),
        )
        return sum(
            1
            for event in rows
            if event.related_goal == goal_id
            and (event.detail or {}).get("kind") in ("progress", "completion")
        )

    def _progress_summary(self, goal: PersistentGoal) -> str:
        upcoming = goal.next_open_milestone()
        if upcoming is not None:
            return f"「{goal.name}」推进到{goal.progress_percent}%，下一步是{upcoming.name}"
        return f"「{goal.name}」推进到{goal.progress_percent}%"

    # ------------------------------------------------------------- lifecycle

    async def set_status(
        self, goal_id: str, status: str, *, reason: str = "manual"
    ) -> PersistentGoal:
        goal = await self.get(goal_id)
        if goal is None:
            raise GoalError(f"unknown goal: {goal_id}")
        goal.status = status
        if status == "done":
            goal.progress = 1.0
            goal.completed_at = self._stamp()
        await self._save(goal)
        if self._state is not None and self._state.state.current_goal == goal_id:
            await self._state.change(
                reason,
                current_goal="",
                emit=False,
            )
        self._log.info("[World] Goal %s -> %s (%s)", goal.name, status, reason)
        return goal

    async def add_milestone(self, goal_id: str, name: str) -> PersistentGoal:
        goal = await self.get(goal_id)
        if goal is None:
            raise GoalError(f"unknown goal: {goal_id}")
        if not any(item.name == name for item in goal.milestones):
            goal.milestones.append(Milestone(name=name[:120]))
            await self._save(goal)
        return goal

    async def set_current(self, goal_id: str) -> None:
        if self._state is None:
            return
        await self._state.change("manual", current_goal=goal_id, emit=False)

    # ----------------------------------------------------------- background

    async def auto_advance_due(self) -> PersistentGoal | None:
        """Bounded, opt-in background advancement (spec §77)."""
        if not self._config.enabled or not self._config.auto_advance:
            return None
        goal = await self.current_goal()
        if goal is None:
            return None
        hours = self._clock.hours_since(goal.last_progress_at or goal.created_at)
        if hours < self._config.advance_interval_hours:
            return None
        return await self.advance(goal.goal_id, reason="time_passage")

    # -------------------------------------------------------------- projects

    async def create_project(
        self, name: str, *, description: str = "", project_id: str = ""
    ) -> CharacterProject:
        if not (name or "").strip():
            raise GoalError("project name is required")
        stamp = self._stamp()
        project = CharacterProject(
            project_id=project_id or f"proj_{uuid.uuid4().hex[:8]}",
            name=name[:120],
            description=description[:500],
            created_at=stamp,
            updated_at=stamp,
        )
        await self._db.execute(
            """INSERT INTO character_projects
               (project_id, name, description, status, progress, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                   name=excluded.name, description=excluded.description,
                   status=excluded.status, progress=excluded.progress,
                   updated_at=excluded.updated_at""",
            (
                project.project_id,
                project.name,
                project.description,
                project.status,
                project.progress,
                project.created_at,
                project.updated_at,
            ),
        )
        self._log.info("[World] Project created: %s", project.name)
        return project

    async def projects(self) -> list[CharacterProject]:
        rows = await self._db.fetchall(
            "SELECT * FROM character_projects ORDER BY created_at ASC LIMIT 50"
        )
        return [
            CharacterProject(
                project_id=row["project_id"],
                name=row["name"] or "",
                description=row.get("description") or "",
                status=row.get("status") or "active",
                progress=float(row.get("progress") or 0.0),
                created_at=int(row.get("created_at") or 0),
                updated_at=int(row.get("updated_at") or 0),
            )
            for row in rows
        ]

    # ----------------------------------------------------------------- view

    async def view(self) -> dict[str, Any]:
        goals = await self.all(limit=50)
        return {
            "enabled": self._config.enabled,
            "max_active": self._config.max_active,
            "auto_advance": self._config.auto_advance,
            "advance_interval_hours": self._config.advance_interval_hours,
            "max_progress_events_per_day": self._config.max_progress_events_per_day,
            "active": [g for g in goals if g.status == "active"],
            "goals": goals,
            "projects": await self.projects(),
        }

    # --------------------------------------------------------------- helper

    def _from_row(self, row: dict[str, Any]) -> PersistentGoal:
        import json

        milestones: list[Milestone] = []
        raw = row.get("milestones")
        if raw:
            try:
                milestones = [Milestone.model_validate(item) for item in json.loads(raw)]
            except Exception:  # noqa: BLE001 - corrupt milestone list must not break reads
                milestones = []
        return PersistentGoal(
            goal_id=row["goal_id"],
            name=row.get("name") or "",
            description=row.get("description") or "",
            status=row.get("status") or "active",
            priority=int(row.get("priority") or 3),
            progress=float(row.get("progress") or 0.0),
            next_action=row.get("next_action") or "",
            milestones=milestones,
            project_id=row.get("project_id") or "",
            enabled=bool(row.get("enabled", 1)),
            created_at=int(row.get("created_at") or 0),
            updated_at=int(row.get("updated_at") or 0),
            last_progress_at=int(row.get("last_progress_at") or 0),
            completed_at=row.get("completed_at"),
        )
