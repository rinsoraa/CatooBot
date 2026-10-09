"""Phase 7E §9：程序性技能的持久化（**两个最小表**，与 AgentPlan/Proposal 同一房型）。

存储选择（任务书 §9 要求对比后写明理由，也见 ``docs/MINECRAFT_PHASE7E.md`` §2.1）：

* 复用通用记忆引擎（``memories`` + provenance）**不采用**：记忆引擎把它当自然语言事实处理 ——
  合并/压缩会改写正文、保留策略会过期删除、配额会裁剪、语义检索会把它当聊天记忆注入；
  技能需要的是结构化、带版本、带条件、终态不可复活，语义与记忆引擎相反。
* 新增两个最小表**采用**：仍属同一套技能/记忆体系 —— 同一个 ``Database``（同一 SQLite/WAL/
  迁移链/事务），审计历史**复用** ``behavior_events``（``skill.*`` 前缀），不建第二套引擎。

幂等（§7.2 第 4/6 条）：``procedural_skill_evidence`` 上 ``UNIQUE(subject_key, task_id,
plan_version, plan_hash)`` —— 同一终态事件重放、同一 task 多次回调、进程重启恢复都只会
落一条证据，成功/失败计数绝不会重复累加。
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from app.tasks.skill import ProceduralSkill, SkillEvidence, day_text, skill_id_for

#: 审计里属于本阶段的类型前缀
SKILL_EVENT_PREFIX = "skill."
#: 审计里的作用域键
SKILL_SCOPE = "procedural_skill"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _usage_key(objective: str, plan_hash: str) -> str:
    """使用链的键：物化期的 (objective, plan_hash) —— 任务还不存在也能记账（§7.6）。"""

    return " ".join(str(objective or "").split())[:160] + "|" + str(plan_hash or "")


def _from_skill_row(row: Any) -> ProceduralSkill | None:
    if row is None:
        return None
    payload = row.get("payload") if isinstance(row, dict) else None
    if not isinstance(payload, str) or not payload:
        return None
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return ProceduralSkill.from_payload(data)


def _from_evidence_row(row: Any) -> SkillEvidence | None:
    if row is None:
        return None
    payload = row.get("payload") if isinstance(row, dict) else None
    if not isinstance(payload, str) or not payload:
        return None
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return SkillEvidence.from_payload(data)


class SkillStore(Protocol):
    async def create_skill(self, skill: ProceduralSkill) -> tuple[ProceduralSkill, bool]: ...
    async def get(self, skill_id: str) -> ProceduralSkill | None: ...
    async def by_fingerprint(self, fingerprint: str) -> ProceduralSkill | None: ...
    async def replace_skill(self, skill: ProceduralSkill) -> ProceduralSkill | None: ...
    async def recent(self, limit: int = 20, *, character_id: str = "") -> list[ProceduralSkill]: ...
    async def active_for(
        self, *, character_id: str, server_id: str, method_class: str, limit: int = 5
    ) -> list[ProceduralSkill]: ...
    async def latest_for_method(
        self, *, character_id: str, server_id: str, method_class: str
    ) -> ProceduralSkill | None: ...
    async def status_counts(self, *, character_id: str = "") -> dict[str, int]: ...
    async def add_evidence(self, evidence: SkillEvidence) -> tuple[SkillEvidence, bool]: ...
    async def recent_evidence(
        self, limit: int = 20, *, skill_id: str = "", subject_key: str = ""
    ) -> list[SkillEvidence]: ...
    async def evidence_for_task(self, task_id: str) -> list[SkillEvidence]: ...
    async def note_usage(
        self, skill_id: str, *, objective: str, plan_hash: str, at: float
    ) -> bool: ...
    async def usage_for(self, *, objective: str, plan_hash: str) -> str: ...
    async def log_event(
        self,
        *,
        skill_id: str,
        event: str,
        reason: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool: ...
    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]: ...


class InMemorySkillStore:
    """测试用（行为与 SQLite 实现一致：同样的幂等与只读投影）。"""

    def __init__(self) -> None:
        self._rows: dict[str, ProceduralSkill] = {}
        self._order: list[str] = []
        self._evidence: list[SkillEvidence] = []
        self._evidence_keys: set[tuple[str, str, int, str]] = set()
        self._usage: dict[str, str] = {}
        self._events: list[dict[str, Any]] = []
        self._seq: dict[str, int] = {}

    async def create_skill(self, skill: ProceduralSkill) -> tuple[ProceduralSkill, bool]:
        existing = await self.by_fingerprint(skill.fingerprint)
        if existing is not None:
            return existing, False
        day = day_text(skill.created_at or 0.0)
        self._seq[day] = self._seq.get(day, 0) + 1
        stored = ProceduralSkill.from_payload(
            {**skill.to_payload(), "skill_id": skill_id_for(day, self._seq[day])}
        )
        self._rows[stored.skill_id] = stored
        self._order.append(stored.skill_id)
        return stored, True

    async def get(self, skill_id: str) -> ProceduralSkill | None:
        return self._rows.get(str(skill_id))

    async def by_fingerprint(self, fingerprint: str) -> ProceduralSkill | None:
        for skill in self._rows.values():
            if skill.fingerprint and skill.fingerprint == str(fingerprint):
                return skill
        return None

    async def replace_skill(self, skill: ProceduralSkill) -> ProceduralSkill | None:
        if str(skill.skill_id) not in self._rows:
            return None
        self._rows[str(skill.skill_id)] = skill
        return skill

    async def recent(self, limit: int = 20, *, character_id: str = "") -> list[ProceduralSkill]:
        rows = [self._rows[key] for key in reversed(self._order)]
        if character_id:
            rows = [item for item in rows if item.character_id == str(character_id)]
        return rows[: max(1, int(limit))]

    async def active_for(
        self, *, character_id: str, server_id: str, method_class: str, limit: int = 5
    ) -> list[ProceduralSkill]:
        rows = [
            item
            for item in await self.recent(500, character_id=character_id)
            if item.status == "ACTIVE"
            and item.server_id == str(server_id)
            and item.objective_pattern == str(method_class)
        ]
        return rows[: max(1, int(limit))]

    async def latest_for_method(
        self, *, character_id: str, server_id: str, method_class: str
    ) -> ProceduralSkill | None:
        for item in await self.recent(500, character_id=character_id):
            if item.server_id == str(server_id) and item.objective_pattern == str(method_class):
                return item
        return None

    async def status_counts(self, *, character_id: str = "") -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in await self.recent(1000, character_id=character_id):
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    async def add_evidence(self, evidence: SkillEvidence) -> tuple[SkillEvidence, bool]:
        key = (
            evidence.subject_key,
            evidence.task_id,
            int(evidence.plan_version),
            evidence.plan_hash,
        )
        if key in self._evidence_keys:
            for item in self._evidence:
                if (
                    item.subject_key,
                    item.task_id,
                    int(item.plan_version),
                    item.plan_hash,
                ) == key:
                    return item, False
        self._evidence_keys.add(key)
        self._evidence.append(evidence)
        return evidence, True

    async def recent_evidence(
        self, limit: int = 20, *, skill_id: str = "", subject_key: str = ""
    ) -> list[SkillEvidence]:
        rows = list(reversed(self._evidence))
        if skill_id:
            rows = [item for item in rows if item.skill_id == str(skill_id)]
        if subject_key:
            rows = [item for item in rows if item.subject_key == str(subject_key)]
        return rows[: max(1, int(limit))]

    async def evidence_for_task(self, task_id: str) -> list[SkillEvidence]:
        return [item for item in self._evidence if item.task_id == str(task_id)]

    async def note_usage(self, skill_id: str, *, objective: str, plan_hash: str, at: float) -> bool:
        key = _usage_key(objective, plan_hash)
        if key in self._usage:
            return False
        self._usage[key] = str(skill_id)
        return True

    async def usage_for(self, *, objective: str, plan_hash: str) -> str:
        return self._usage.get(_usage_key(objective, plan_hash), "")

    async def log_event(
        self,
        *,
        skill_id: str,
        event: str,
        reason: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        self._events.append(
            {
                "type": str(event),
                "scope_key": SKILL_SCOPE,
                "reason": str(reason),
                "detail": _dump({"skill_id": str(skill_id), **(detail or {})}),
                "created_at": int(at if at is not None else time.time()),
            }
        )
        return True

    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = [
            item for item in reversed(self._events) if item["type"].startswith(SKILL_EVENT_PREFIX)
        ]
        return rows[: max(1, int(limit))]


_SKILL_INSERT = (
    "INSERT OR IGNORE INTO procedural_skills (skill_id, schema_version, name, objective_pattern,"
    " summary, status, character_id, server_id, environment, tools_signature, max_risk, version,"
    " supersedes_skill_id, superseded_by, success_count, failure_count, ambiguous_count,"
    " fingerprint, reason, created_at, updated_at, last_learned_at, last_verified_at, last_used_at,"
    " payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

_SKILL_UPDATE = (
    "UPDATE procedural_skills SET schema_version = ?, name = ?, objective_pattern = ?, summary = ?,"
    " status = ?, character_id = ?, server_id = ?, environment = ?, tools_signature = ?,"
    " max_risk = ?, version = ?, supersedes_skill_id = ?, superseded_by = ?, success_count = ?,"
    " failure_count = ?, ambiguous_count = ?, fingerprint = ?, reason = ?, updated_at = ?,"
    " last_learned_at = ?, last_verified_at = ?, last_used_at = ?, payload = ?"
    " WHERE skill_id = ?"
)

_EVIDENCE_INSERT = (
    "INSERT OR IGNORE INTO procedural_skill_evidence (evidence_id, skill_id, subject_key, task_id,"
    " plan_version, plan_hash, outcome, verdict, reason_code, step_ids, postcondition_kind,"
    " postcondition_ok, detail, created_at, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


class SqliteSkillStore:
    """生产实现：``procedural_skills`` + ``procedural_skill_evidence``（迁移 34）。"""

    def __init__(self, database: Any, *, logger: Any = None) -> None:
        self._db = database
        self._log = logger

    # ------------------------------------------------------------ 技能行

    async def create_skill(self, skill: ProceduralSkill) -> tuple[ProceduralSkill, bool]:
        fingerprint = str(skill.fingerprint or "")
        if not fingerprint:
            raise ValueError("skill.fingerprint 不能为空")
        payload = skill.to_payload()

        def _run(conn: Any) -> str:
            day = day_text(skill.created_at or time.time())
            prefix = f"{skill_id_for(day, 0).rsplit('-', 1)[0]}-"
            row = conn.execute(
                "SELECT MAX(CAST(SUBSTR(skill_id, ?) AS INTEGER)) AS seq FROM procedural_skills"
                " WHERE skill_id LIKE ?",
                (len(prefix) + 1, f"{prefix}%"),
            ).fetchone()
            sequence = int((row[0] if row else 0) or 0) + 1
            skill_id = skill_id_for(day, sequence)
            payload["skill_id"] = skill_id
            cursor = conn.execute(
                _SKILL_INSERT,
                (
                    skill_id,
                    int(payload["schema_version"]),
                    payload["name"],
                    payload["objective_pattern"],
                    payload["summary"],
                    payload["status"],
                    payload["character_id"],
                    payload["server_id"],
                    payload["environment"],
                    payload["tools_signature"],
                    payload["max_risk"],
                    int(payload["version"]),
                    payload["supersedes_skill_id"],
                    payload["superseded_by"],
                    int(payload["success_count"]),
                    int(payload["failure_count"]),
                    int(payload["ambiguous_count"]),
                    fingerprint,
                    payload["reason"],
                    float(payload["created_at"]),
                    float(payload["updated_at"]),
                    float(payload["last_learned_at"]),
                    float(payload["last_verified_at"]),
                    float(payload["last_used_at"]),
                    _dump(payload),
                ),
            )
            return skill_id if cursor.rowcount else ""

        created_id = str(await self._db.run_in_transaction(_run) or "")
        if created_id:
            stored = await self.get(created_id)
            if stored is not None:
                return stored, True
        existing = await self.by_fingerprint(fingerprint)
        if existing is not None:
            return existing, False
        raise RuntimeError("skill create failed")

    async def get(self, skill_id: str) -> ProceduralSkill | None:
        row = await self._db.fetchone(
            "SELECT payload FROM procedural_skills WHERE skill_id = ?", (str(skill_id),)
        )
        return _from_skill_row(row)

    async def by_fingerprint(self, fingerprint: str) -> ProceduralSkill | None:
        row = await self._db.fetchone(
            "SELECT payload FROM procedural_skills WHERE fingerprint = ? LIMIT 1",
            (str(fingerprint),),
        )
        return _from_skill_row(row)

    async def replace_skill(self, skill: ProceduralSkill) -> ProceduralSkill | None:
        current = await self.get(skill.skill_id)
        if current is None:
            return None
        payload = skill.to_payload()

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                _SKILL_UPDATE,
                (
                    int(payload["schema_version"]),
                    payload["name"],
                    payload["objective_pattern"],
                    payload["summary"],
                    payload["status"],
                    payload["character_id"],
                    payload["server_id"],
                    payload["environment"],
                    payload["tools_signature"],
                    payload["max_risk"],
                    int(payload["version"]),
                    payload["supersedes_skill_id"],
                    payload["superseded_by"],
                    int(payload["success_count"]),
                    int(payload["failure_count"]),
                    int(payload["ambiguous_count"]),
                    payload["fingerprint"],
                    payload["reason"],
                    float(payload["updated_at"]),
                    float(payload["last_learned_at"]),
                    float(payload["last_verified_at"]),
                    float(payload["last_used_at"]),
                    _dump(payload),
                    str(skill.skill_id),
                ),
            )
            return bool(cursor.rowcount)

        if not await self._db.run_in_transaction(_run):
            return None
        return await self.get(skill.skill_id)

    async def recent(self, limit: int = 20, *, character_id: str = "") -> list[ProceduralSkill]:
        if character_id:
            rows = await self._db.fetchall(
                "SELECT payload FROM procedural_skills WHERE character_id = ?"
                " ORDER BY updated_at DESC, id DESC LIMIT ?",
                (str(character_id), max(1, int(limit))),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT payload FROM procedural_skills ORDER BY updated_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
        return [item for item in (_from_skill_row(row) for row in rows) if item is not None]

    async def active_for(
        self, *, character_id: str, server_id: str, method_class: str, limit: int = 5
    ) -> list[ProceduralSkill]:
        rows = await self._db.fetchall(
            "SELECT payload FROM procedural_skills WHERE character_id = ? AND server_id = ?"
            " AND objective_pattern = ? AND status = 'ACTIVE'"
            " ORDER BY updated_at DESC, id DESC LIMIT ?",
            (str(character_id), str(server_id), str(method_class), max(1, int(limit))),
        )
        return [item for item in (_from_skill_row(row) for row in rows) if item is not None]

    async def latest_for_method(
        self, *, character_id: str, server_id: str, method_class: str
    ) -> ProceduralSkill | None:
        row = await self._db.fetchone(
            "SELECT payload FROM procedural_skills WHERE character_id = ? AND server_id = ?"
            " AND objective_pattern = ? ORDER BY version DESC, updated_at DESC, id DESC LIMIT 1",
            (str(character_id), str(server_id), str(method_class)),
        )
        return _from_skill_row(row)

    async def status_counts(self, *, character_id: str = "") -> dict[str, int]:
        if character_id:
            rows = await self._db.fetchall(
                "SELECT status, COUNT(*) AS n FROM procedural_skills WHERE character_id = ?"
                " GROUP BY status",
                (str(character_id),),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT status, COUNT(*) AS n FROM procedural_skills GROUP BY status"
            )
        return {str(row.get("status") or ""): int(row.get("n") or 0) for row in rows}

    # ------------------------------------------------------------ 学习账本

    async def add_evidence(self, evidence: SkillEvidence) -> tuple[SkillEvidence, bool]:
        payload = evidence.to_payload()

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                _EVIDENCE_INSERT,
                (
                    evidence.evidence_id,
                    evidence.skill_id,
                    evidence.subject_key,
                    evidence.task_id,
                    int(evidence.plan_version),
                    evidence.plan_hash,
                    evidence.outcome,
                    str(evidence.verdict),
                    evidence.reason_code,
                    _dump(list(evidence.step_ids)),
                    evidence.postcondition_kind,
                    1 if evidence.postcondition_ok else 0,
                    _dump(evidence.detail),
                    float(evidence.at),
                    _dump(payload),
                ),
            )
            return bool(cursor.rowcount)

        if await self._db.run_in_transaction(_run):
            return evidence, True
        rows = await self.evidence_for_task(evidence.task_id)
        for item in rows:
            if (
                item.subject_key == evidence.subject_key
                and int(item.plan_version) == int(evidence.plan_version)
                and item.plan_hash == evidence.plan_hash
            ):
                return item, False
        return evidence, False

    async def recent_evidence(
        self, limit: int = 20, *, skill_id: str = "", subject_key: str = ""
    ) -> list[SkillEvidence]:
        where: list[str] = []
        params: list[Any] = []
        if skill_id:
            where.append("skill_id = ?")
            params.append(str(skill_id))
        if subject_key:
            where.append("subject_key = ?")
            params.append(str(subject_key))
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        params.append(max(1, int(limit)))
        sql = (
            "SELECT payload FROM procedural_skill_evidence"
            f"{clause} ORDER BY created_at DESC, id DESC LIMIT ?"
        )
        rows = await self._db.fetchall(sql, tuple(params))
        return [item for item in (_from_evidence_row(row) for row in rows) if item is not None]

    async def evidence_for_task(self, task_id: str) -> list[SkillEvidence]:
        rows = await self._db.fetchall(
            "SELECT payload FROM procedural_skill_evidence WHERE task_id = ? ORDER BY id",
            (str(task_id),),
        )
        return [item for item in (_from_evidence_row(row) for row in rows) if item is not None]

    async def note_usage(self, skill_id: str, *, objective: str, plan_hash: str, at: float) -> bool:
        key = _usage_key(objective, plan_hash)

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO procedural_skill_usage"
                " (skill_id, usage_key, objective, plan_hash, created_at, payload)"
                " VALUES (?,?,?,?,?,?)",
                (
                    str(skill_id),
                    key,
                    str(objective)[:200],
                    str(plan_hash),
                    float(at),
                    _dump({"skill_id": str(skill_id), "objective": str(objective)[:200]}),
                ),
            )
            return bool(cursor.rowcount)

        return bool(await self._db.run_in_transaction(_run))

    async def usage_for(self, *, objective: str, plan_hash: str) -> str:
        row = await self._db.fetchone(
            "SELECT skill_id FROM procedural_skill_usage WHERE usage_key = ?",
            (_usage_key(objective, plan_hash),),
        )
        return str((row or {}).get("skill_id") or "")

    # ------------------------------------------------------------ 审计（复用 behavior_events）

    async def log_event(
        self,
        *,
        skill_id: str,
        event: str,
        reason: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        moment = int(at if at is not None else time.time())
        payload = _dump({"skill_id": str(skill_id), **(detail or {})})

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "INSERT INTO behavior_events (type, scope_key, reason, detail, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(event)
                    if str(event).startswith(SKILL_EVENT_PREFIX)
                    else f"{SKILL_EVENT_PREFIX}{event}",
                    SKILL_SCOPE,
                    str(reason),
                    payload,
                    "procedural_skill",
                    moment,
                ),
            )
            return bool(cursor.rowcount)

        return bool(await self._db.run_in_transaction(_run))

    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT type, reason, detail, created_at FROM behavior_events"
            " WHERE scope_key = ? AND type LIKE ? ORDER BY id DESC LIMIT ?",
            (SKILL_SCOPE, f"{SKILL_EVENT_PREFIX}%", max(1, int(limit))),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw = item.get("detail")
            if isinstance(raw, str) and raw:
                try:
                    item["detail"] = json.loads(raw)
                except (TypeError, ValueError):
                    item["detail"] = {}
            out.append(item)
        return out


__all__ = [
    "InMemorySkillStore",
    "SKILL_EVENT_PREFIX",
    "SKILL_SCOPE",
    "SkillStore",
    "SqliteSkillStore",
]
