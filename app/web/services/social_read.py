"""SocialReadService：社交域只读投影（WebUI v1.0 · W5 契约 §7.2）。

只做投影，不新增真相、不写任何状态：用户来自 ``AdminService.list_users`` ＋
``PersonIdentityResolver.for_qq``（``person_id`` 始终返回，绝不拿 DB 主键当 UI 键）；
关系来自 ``RelationshipStore.get/important``（只读副本，绝不 ``ensure``/``save``）；
承诺来自 ``CommitmentManager.all/get/for_person``，目标关联来自
``Goal.target_commitment``；会话来自 ``SandboxRuntime.social_session_snapshot()``
（副本）；经历/记忆只读既有沙盒经历表与 ``bot.memory.repository`` 检索。

没有访问器或数据不存在时返回 ``None``/空列表，绝不编造。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.sandbox.commitments import OPEN_STATUSES, CommitmentStatus

if TYPE_CHECKING:
    from app.core.bot import Bot
    from app.web.services.admin import AdminService
    from app.web.services.social import SocialAdminService

_RELATION_FIELDS = ("relation_type", "trust", "familiarity", "closeness", "social_comfort")


def _stamp(value: Any) -> float | None:
    try:
        return float(value) or None
    except (TypeError, ValueError):
        return None


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


class SocialReadService:
    """把 Core 的社交状态投影成契约 JSON；纯读、无副作用。"""

    def __init__(
        self,
        bot: Bot,
        admin: AdminService | None = None,
        social_admin: SocialAdminService | None = None,
    ) -> None:
        self._bot = bot
        self._admin = admin
        self._social_admin = social_admin

    # ----------------------------------------------------------------- users

    async def users_page(self, query: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """用户页 + 分页字段（total 是过滤后的真实条数）。"""
        matched = await self._user_items(query)
        items = matched[offset : offset + limit]
        return {
            "items": items,
            "total": len(matched),
            "limit": limit,
            "offset": offset,
            "next_cursor": (
                str(offset + limit) if offset >= 0 and offset + limit < len(matched) else None
            ),
        }

    async def users(
        self, query: str = "", limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        page = await self.users_page(query=query, limit=limit, offset=offset)
        return page["items"]

    async def user_detail(self, person_id: str) -> dict[str, Any] | None:
        """``GET /api/v1/social/users/{person_id}``；未知人物返回 None。"""
        sandbox = self._sandbox()
        row: dict[str, Any] | None = None
        identity: tuple[str, str] | None = None
        for candidate in await self._user_rows():
            resolved = self._identity(candidate, sandbox)
            if resolved[0] == person_id or str(candidate.get("user_id") or "") == person_id:
                row, identity = candidate, resolved
                break
        relationship = await self._relationship(person_id, sandbox)
        person_row = await sandbox.relationships_dyn.person_row(person_id) if sandbox else None
        if row is None and relationship is None and person_row is None:
            return None

        session = sandbox.social_session_snapshot() if sandbox is not None else None
        experience_map = await self._experience_map(sandbox)
        if row is not None and identity is not None:
            person = await self._user_item(row, identity, sandbox, experience_map, session)
            qq = person.get("qq")
        else:
            person = self._person_from_row(person_id, person_row, relationship, sandbox)
            qq = person.get("qq")

        goals = self._goal_map(sandbox)
        manager = getattr(sandbox, "commitments", None) if sandbox is not None else None
        commitments = []
        if manager is not None:
            commitments = [
                self._commitment_row(item, goals.get(item.commitment_id), manager)
                for item in manager.for_person(person_id)
            ]
        experiences = await self._experiences_for(person_id, qq, sandbox, limit=20)
        person_view = {key: value for key, value in person.items() if key != "relationship"}
        return {
            "person": person_view,
            "relationship": person.get("relationship"),
            "commitments": commitments,
            "experiences": experiences,
            "memories": await self._memories_for(qq),
            "spaces": person.get("spaces") or [],
        }

    async def _user_items(self, query: str = "") -> list[dict[str, Any]]:
        sandbox = self._sandbox()
        session = sandbox.social_session_snapshot() if sandbox is not None else None
        experience_map = await self._experience_map(sandbox)
        items: list[dict[str, Any]] = []
        for row in await self._user_rows():
            identity = self._identity(row, sandbox)
            item = await self._user_item(row, identity, sandbox, experience_map, session)
            if query and not self._matches(item, query):
                continue
            items.append(item)
        return items

    @staticmethod
    def _matches(item: dict[str, Any], query: str) -> bool:
        needle = query.casefold()
        haystack = [
            str(item.get("display_name") or ""),
            str(item.get("nickname") or ""),
            str(item.get("qq") or ""),
            str(item.get("notes") or ""),
            " ".join(item.get("tags") or []),
        ]
        return any(needle in value.casefold() for value in haystack)

    async def _user_item(
        self,
        row: dict[str, Any],
        identity: tuple[str, str],
        sandbox: Any | None,
        experience_map: dict[str, str],
        session: dict[str, Any] | None,
    ) -> dict[str, Any]:
        person_id, resolved_name = identity
        qq = str(row.get("user_id") or "")
        nickname = str(row.get("nickname") or "")
        state = await self._relationship(person_id, sandbox)
        display = str(row.get("nickname_override") or resolved_name or nickname or person_id)
        open_count = 0
        if sandbox is not None:
            open_count = sum(1 for item in sandbox.commitments.for_person(person_id) if item.open)
        return {
            "person_id": person_id,
            "display_name": display,
            "qq": qq or None,
            "nickname": nickname or None,
            "nickname_override": str(row.get("nickname_override") or "") or None,
            "interaction_count": int(row.get("interactions") or 0),
            "stage": str(row.get("stage") or "new"),
            "initiative_enabled": bool(row.get("initiative_enabled", 1)),
            "notes": str(row.get("notes") or ""),
            "tags": _tags(row.get("tags")),
            "last_seen": _stamp(row.get("last_seen")),
            "relationship": self._relationship_block(state),
            "open_commitments": open_count,
            "recent_experience": experience_map.get(person_id) or experience_map.get(qq) or None,
            "spaces": self._spaces_for(person_id, sandbox, session),
        }

    def _identity(self, row: dict[str, Any], sandbox: Any | None) -> tuple[str, str]:
        qq = str(row.get("user_id") or "")
        nickname = str(row.get("nickname") or "")
        if sandbox is None:
            return qq, nickname
        identity = sandbox.persons.for_qq(qq, display_name=nickname)
        return identity.person_id, identity.display_name or nickname

    def _person_from_row(
        self,
        person_id: str,
        person_row: dict[str, Any] | None,
        relationship: Any | None,
        sandbox: Any | None,
    ) -> dict[str, Any]:
        external = (person_row or {}).get("external_ids") or {}
        qq = str(external.get("qq") or "") or (person_id if person_id.isdigit() else "")
        display = str((person_row or {}).get("display_name") or "")
        initial = sandbox.relationships_dyn.initial_for_person(person_id) if sandbox else None
        if not display and initial is not None:
            display = str(initial.metadata.get("name") or "")
        return {
            "person_id": person_id,
            "display_name": display or person_id,
            "qq": qq or None,
            "nickname": None,
            "nickname_override": None,
            "interaction_count": int(getattr(relationship, "interaction_count", 0) or 0),
            "stage": None,
            "initiative_enabled": None,
            "notes": None,
            "tags": [],
            "last_seen": _stamp(getattr(relationship, "last_interaction_at", 0.0)),
            "relationship": self._relationship_block(relationship),
            "open_commitments": 0,
            "recent_experience": None,
            "spaces": self._spaces_for(person_id, sandbox, None),
        }

    async def _relationship(self, person_id: str, sandbox: Any | None) -> Any | None:
        if sandbox is None:
            return None
        store = sandbox.relationships_dyn
        state = await store.get(person_id)
        if state is None:
            state = store.initial_for_person(person_id)
        return state

    @staticmethod
    def _relationship_block(state: Any | None) -> dict[str, Any] | None:
        if state is None:
            return None
        block: dict[str, Any] = {field: getattr(state, field) for field in _RELATION_FIELDS}
        block["interaction_count"] = int(state.interaction_count)
        block["positive_interactions"] = int(state.positive_interactions)
        block["negative_interactions"] = int(state.negative_interactions)
        block["last_interaction_at"] = _stamp(state.last_interaction_at)
        block["source"] = str(state.source)
        return block

    # ------------------------------------------------------------- relations

    async def relationships(self, limit: int = 50) -> list[dict[str, Any]]:
        sandbox = self._sandbox()
        if sandbox is None:
            return []
        states = await sandbox.relationships_dyn.important(limit=limit)
        rows: list[dict[str, Any]] = []
        for state in states:
            rows.append(
                {
                    "person_id": state.person_id,
                    "display_name": await self._display_name(state.person_id, sandbox),
                    "relation_type": state.relation_type,
                    "trust": round(float(state.trust), 4),
                    "familiarity": round(float(state.familiarity), 4),
                    "closeness": round(float(state.closeness), 4),
                    "social_comfort": round(float(state.social_comfort), 4),
                    "interaction_count": int(state.interaction_count),
                    "last_interaction_at": _stamp(state.last_interaction_at),
                }
            )
        return rows

    async def _display_name(self, person_id: str, sandbox: Any) -> str:
        state = sandbox.relationships_dyn.initial_for_person(person_id)
        if state is not None and state.metadata.get("name"):
            return str(state.metadata["name"])
        row = await sandbox.relationships_dyn.person_row(person_id)
        return str(row["display_name"]) if row and row.get("display_name") else person_id

    # ----------------------------------------------------------- commitments

    async def commitments(self, status: str = "", person: str = "") -> list[dict[str, Any]]:
        sandbox = self._sandbox()
        if sandbox is None:
            return []
        manager = sandbox.commitments
        statuses = {CommitmentStatus(status)} if status else None
        items = manager.all(statuses=statuses)
        if person:
            items = [item for item in items if item.person_id == person]
        goals = self._goal_map(sandbox)
        rows = [
            self._commitment_row(item, goals.get(item.commitment_id), manager) for item in items
        ]
        open_values = {value.value for value in OPEN_STATUSES}
        rows.sort(
            key=lambda row: (
                0 if row["status"] in open_values else 1,
                row["due_at"] if row["due_at"] is not None else float("inf"),
                row["created_at"] if row["created_at"] is not None else 0.0,
            )
        )
        return rows

    @staticmethod
    def _goal_map(sandbox: Any | None) -> dict[str, Any]:
        if sandbox is None:
            return {}
        return {
            goal.target_commitment: goal for goal in sandbox.goals.all() if goal.target_commitment
        }

    @staticmethod
    def _commitment_row(item: Any, goal: Any | None, manager: Any) -> dict[str, Any]:
        return {
            "commitment_id": item.commitment_id,
            "person_id": item.person_id,
            "person_name": manager.person_label(item.person_id),
            "kind": item.kind.value,
            "status": item.status.value,
            "strength": item.strength.value,
            "priority": round(float(item.priority), 4),
            "summary": item.description or None,
            "target_activity": item.target_activity or None,
            "time_hint": item.time_hint or None,
            "earliest_at": _stamp(item.earliest_at),
            "due_at": _stamp(item.due_at),
            "created_at": _stamp(item.created_at),
            "goal_id": goal.goal_id if goal is not None else None,
            "goal_status": goal.status.value if goal is not None else None,
        }

    async def commitment_detail(self, commitment_id: str) -> dict[str, Any] | None:
        """``GET /api/v1/social/commitments/{id}``；未知承诺返回 None。"""
        sandbox = self._sandbox()
        if sandbox is None:
            return None
        manager = sandbox.commitments
        item = manager.get(commitment_id)
        if item is None:
            return None
        goal = self._goal_map(sandbox).get(commitment_id)
        row = self._commitment_row(item, goal, manager)
        return {
            **row,
            "goal": self._goal_block(goal),
            "action": self._action_block(sandbox, item, goal),
            "outcome": self._outcome_block(item),
        }

    @staticmethod
    def _goal_block(goal: Any | None) -> dict[str, Any] | None:
        if goal is None:
            return None
        return {
            "goal_id": goal.goal_id,
            "kind": goal.kind.value,
            "status": goal.status.value,
            "reason": goal.reason or None,
            "priority": round(float(goal.priority), 4),
            "progress": round(float(goal.progress), 4),
            "target_commitment": goal.target_commitment or None,
            "dedupe_key": goal.dedupe_key,
            "current_step": (
                goal.current_step.model_dump(mode="json") if goal.current_step is not None else None
            ),
        }

    @staticmethod
    def _action_block(sandbox: Any, item: Any, goal: Any | None) -> dict[str, Any] | None:
        current = sandbox.current_action
        if current is None:
            return None
        step = goal.current_step if goal is not None else None
        step_match = step is not None and step.action_instance_id == current.id
        target_match = bool(item.target_action) and current.definition_id == item.target_action
        if not (step_match or target_match):
            return None
        definition = sandbox.actions.definition(current)
        return {
            "instance_id": current.id,
            "definition_id": current.definition_id,
            "name": definition.name if definition is not None else None,
            "status": current.status.value,
            "progress": round(float(current.progress), 4),
            "detail": current.detail or None,
            "space_id": current.space_id or None,
            "reason_code": current.reason_code or None,
            "started_at": _stamp(current.started_at),
            "planned_end_at": _stamp(current.planned_end_at),
        }

    @staticmethod
    def _outcome_block(item: Any) -> dict[str, Any] | None:
        if item.open:
            return None
        return {
            "status": item.status.value,
            "resolved_at": _stamp(item.resolved_at),
            "updated_at": _stamp(item.updated_at),
            "revision": int(item.revision),
            "result": item.metadata.get("outcome"),
        }

    # --------------------------------------------------------------- sessions
    def sessions(self) -> dict[str, Any]:
        """``GET /api/v1/social/sessions``：运行期交互会话（最多一条）。"""
        sandbox = self._sandbox()
        if sandbox is None:
            return {"active": False, "items": []}
        active = bool(sandbox.social_session_active())
        snapshot = sandbox.social_session_snapshot() if active else None
        return {"active": active, "items": [snapshot] if snapshot is not None else []}

    # ----------------------------------------------------------------- spaces

    def spaces(self) -> dict[str, Any]:
        """``GET /api/v1/social/spaces``：社交空间 + 配置的 QQ 群映射。"""
        sandbox = self._sandbox()
        items: list[dict[str, Any]] = []
        if sandbox is not None:
            for space in sandbox.social_spaces.values():
                items.append(
                    {
                        "space_id": space.id,
                        "kind": space.kind,
                        "qq_group_id": space.qq_group_id or None,
                        "name": space.name,
                        "participants": list(space.participants),
                        "character_presence": space.character_presence,
                        "interest": round(float(space.interest), 4),
                    }
                )
        config = getattr(self._bot, "config", None)
        sandbox_config = getattr(config, "sandbox", None)
        raw_map = getattr(sandbox_config, "social_space_map", None) or {}
        return {
            "items": items,
            "map": {str(key): str(value) for key, value in dict(raw_map).items()},
        }

    # -------------------------------------------------------------- helpers

    async def _user_rows(self) -> list[dict[str, Any]]:
        return await self._admin.list_users() if self._admin is not None else []

    async def _experience_map(self, sandbox: Any | None) -> dict[str, str]:
        if sandbox is None:
            return {}
        rows = await sandbox.store.recent_experiences(character_id=sandbox.character_id, limit=100)
        result: dict[str, str] = {}
        for row in rows:
            summary = str(row.get("summary") or "")
            if not summary:
                continue
            for actor in row.get("actors") or []:
                key = str(actor)
                result.setdefault(key, summary)
        return result

    async def _experiences_for(
        self, person_id: str, qq: str | None, sandbox: Any | None, limit: int
    ) -> list[dict[str, Any]] | None:
        if sandbox is None:
            return None  # 没有沙盒时经历块返回 null，而不是假装空数据
        rows = await sandbox.store.recent_experiences(character_id=sandbox.character_id, limit=200)
        wanted = {person_id}
        if qq:
            wanted.add(qq)
        found = [
            row
            for row in rows
            if wanted.intersection(str(actor) for actor in (row.get("actors") or []))
        ]
        return found[:limit]

    async def _memories_for(self, qq: str | None) -> list[dict[str, Any]] | None:
        memory = getattr(self._bot, "memory", None)
        if memory is None:
            return None
        if not qq:
            return []
        rows = await memory.repository.search(scope_key=f"user:{qq}", status="active", limit=20)
        return [row.model_dump() for row in rows]

    @staticmethod
    def _spaces_for(
        person_id: str, sandbox: Any | None, session: dict[str, Any] | None
    ) -> list[str]:
        if sandbox is None:
            return []
        found = [
            space_id
            for space_id, space in sandbox.social_spaces.items()
            if person_id in (space.participants or [])
        ]
        if session is not None and session.get("person_id") == person_id:
            space_id = str(session.get("social_space_id") or "")
            if space_id and space_id not in found:
                found.append(space_id)
        return found

    def _sandbox(self) -> Any | None:
        return getattr(self._bot, "sandbox", None)
