"""WorldReadService：世界页只读投影（WebUI v1.0 · W5 契约 §7.1）。

在 :class:`~app.web.services.read_model.RuntimeReadService.world()` 的既有键
之上**只追加** W5 需要的细节块（``needs_full`` / ``spaces`` / ``objects`` /
``inventories`` / ``pet`` / ``social_spaces`` / ``action_defs`` /
``modes_defs`` / ``goals_full``），并把 ``world.action`` 补全为带
``definition_id``/``detail``/``reason_code``/``space_id``/``goal_id``/
``goal_step`` 的块。所有值都来自沙盒的公开访问器；没有就返回空值，绝不编造。

对既有键的一处显式变更（W5 契约要求）：``interrupted`` 由 bool 变为
``{"active", "definition_id", "remaining_minutes", "reason", "progress"}|null``
（数据源仍是 ``_interrupted``，没有打断时返回 ``null``）。``/api/v1/overview``
的 ``world.interrupted`` 仍是 bool，不受影响。

纯读：不触碰 ``world_revision``/``cognitive_revision``（W2 测试继续验证）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.web.services.read_model import RuntimeReadService

if TYPE_CHECKING:
    from app.core.bot import Bot
    from app.web.realtime import RealtimeHub
    from app.web.services.admin import AdminService


def _stamp(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number or None


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class WorldReadService:
    """把沙盒状态投影成世界页 JSON；纯读、无副作用。"""

    def __init__(
        self,
        bot: Bot,
        *,
        admin: AdminService | None = None,
        hub: RealtimeHub | None = None,
    ) -> None:
        self._bot = bot
        self._base = RuntimeReadService(bot, hub=hub, admin=admin)

    # ----------------------------------------------------------------- world

    async def world(self) -> dict[str, Any]:
        """``GET /api/v1/world``：W2 既有键 + W5 追加块。"""
        data = await self._base.world()
        additions: dict[str, Any] = {
            "needs_full": [],
            "interrupted": None,
            "spaces": [],
            "objects": [],
            "inventories": {},
            "pet": None,
            "social_spaces": [],
            "action_defs": [],
            "modes_defs": [],
            "goals_full": [],
        }
        sandbox = self._sandbox()
        if sandbox is None:
            return {**data, **additions}

        additions["needs_full"] = self._needs_full(sandbox)
        additions["interrupted"] = self._interrupted_block(sandbox)
        additions["spaces"] = [space.model_dump(mode="json") for space in sandbox.spaces.all()]
        additions["objects"] = [obj.model_dump(mode="json") for obj in sandbox.objects.all()]
        additions["inventories"] = {
            key: inventory.model_dump(mode="json")
            for key, inventory in sandbox.inventories.all().items()
        }
        additions["pet"] = self._pet_block(sandbox)
        additions["social_spaces"] = [
            space.model_dump(mode="json") for space in sandbox.social_spaces.values()
        ]
        additions["action_defs"] = self._action_defs(sandbox)
        additions["modes_defs"] = self._modes_defs(sandbox)
        additions["goals_full"] = self._goals_full(sandbox)
        if isinstance(data.get("action"), dict):
            data["action"] = self._action_detail(sandbox, data["action"])
        return {**data, **additions}

    # -------------------------------------------------------------- timeline

    async def timeline(self, limit: int = 120) -> dict[str, Any]:
        """``GET /api/v1/world/timeline``：变更驱动的事件轨迹。

        复用 ``sandbox.store.timeline``（与 ``/world/trace`` 同一张表、同一
        粒度：只有世界事件，不是每 tick 的噪声），补上 UI 需要的字段。
        """
        sandbox = self._sandbox()
        if sandbox is None:
            return {"enabled": False, "items": [], "count": 0}
        rows = await sandbox.store.timeline(limit=limit)
        items = [self._timeline_row(row) for row in rows]
        items.reverse()  # store returns newest-first; a timeline reads oldest→newest
        return {"enabled": True, "items": items, "count": len(items)}

    @staticmethod
    def _timeline_row(row: dict[str, Any]) -> dict[str, Any]:
        payload = _json_object(row.get("data"))
        action = payload.get("action") or payload.get("action_id") or payload.get("definition_id")
        return {
            "ts": _stamp(row.get("created_at")),
            "event_type": str(row.get("kind") or ""),
            "summary": str(row.get("summary") or ""),
            "location": payload.get("location") or None,
            "action": action or None,
            "revisions": payload.get("revisions") or None,
        }

    # ----------------------------------------------------------------- blocks

    @staticmethod
    def _needs_full(sandbox: Any) -> list[dict[str, Any]]:
        seed = getattr(sandbox, "seed", None)
        labels = dict(getattr(seed, "need_labels", None) or {})
        rows: list[dict[str, Any]] = []
        for need in sandbox.needs.all().values():
            band = need.band()
            rows.append(
                {
                    "key": need.key,
                    "label": labels.get(need.key, need.key),
                    "level": round(float(need.level), 4),
                    "band": band,
                    "growth": round(float(need.growth_per_hour), 4),
                    "critical": band == "critical",
                    "pressing": band in ("strong", "critical"),
                }
            )
        return rows

    @staticmethod
    def _interrupted_block(sandbox: Any) -> dict[str, Any] | None:
        context = getattr(sandbox, "_interrupted", None)
        if context is None:
            return None
        return {
            "active": True,
            "definition_id": context.definition_id,
            "remaining_minutes": round(float(context.remaining_minutes), 3),
            "reason": context.interrupt_reason or "",
            "progress": round(float(context.progress), 4),
        }

    @staticmethod
    def _pet_block(sandbox: Any) -> dict[str, Any] | None:
        pet = getattr(sandbox, "pet", None)
        if pet is None:
            return None
        block = pet.model_dump(mode="json")
        system = getattr(sandbox, "pet_system", None)
        if system is not None:
            try:
                block["line"] = system.prompt_line()
            except Exception:  # noqa: BLE001 - 一行文案失败不拖垮整页
                block["line"] = None
        return block

    @staticmethod
    def _action_defs(sandbox: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for definition in sandbox.actions.definitions.values():
            spaces = list(definition.spaces)
            single = spaces[0] if len(spaces) == 1 and spaces[0] != "*" else None
            rows.append(
                {
                    "definition_id": definition.id,
                    "name": definition.name,
                    "space_id": single,
                    "needs": dict(definition.need_relief),
                }
            )
        return rows

    @staticmethod
    def _modes_defs(sandbox: Any) -> list[dict[str, Any]]:
        seed = getattr(sandbox, "seed", None)
        rows: list[dict[str, Any]] = []
        for definition in getattr(seed, "modes", None) or []:
            key = str(definition.get("id") or "")
            rows.append(
                {
                    "key": key,
                    "label": str(definition.get("name") or key),
                    "description": str(definition.get("style") or definition.get("trigger") or ""),
                }
            )
        return rows

    @staticmethod
    def _goals_full(sandbox: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for goal in sandbox.goals.all():
            rows.append(
                {
                    "goal_id": goal.goal_id,
                    "title": goal.reason or goal.kind.value,
                    "status": goal.status.value,
                    "priority": round(float(goal.priority), 4),
                    "progress": round(float(goal.progress), 4),
                    "current_step": (
                        goal.current_step.model_dump(mode="json")
                        if goal.current_step is not None
                        else None
                    ),
                    "target_commitment": goal.target_commitment or None,
                    "dedupe_key": goal.dedupe_key,
                }
            )
        return rows

    @staticmethod
    def _goal_for_action(sandbox: Any, action: Any) -> Any | None:
        for goal in sandbox.goals.all():
            step = goal.current_step
            if step is not None and step.action_instance_id == action.id:
                return goal
        return None

    @classmethod
    def _action_detail(cls, sandbox: Any, base: dict[str, Any]) -> dict[str, Any]:
        action = sandbox.current_action
        if action is None:
            return base
        goal = cls._goal_for_action(sandbox, action)
        return {
            **base,
            "definition_id": action.definition_id,
            "detail": action.detail or None,
            "reason_code": action.reason_code or None,
            "space_id": action.space_id or None,
            "goal_id": goal.goal_id if goal is not None else None,
            "goal_step": (
                goal.current_step.model_dump(mode="json")
                if goal is not None and goal.current_step is not None
                else None
            ),
        }

    # ---------------------------------------------------------------- helpers

    def _sandbox(self) -> Any | None:
        return getattr(self._bot, "sandbox", None)
