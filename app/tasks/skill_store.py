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
from dataclasses import dataclass, replace
from typing import Any, Protocol

from app.tasks.skill import (
    SKILL_TRIGGER_AMBIGUOUS,
    SKILL_TRIGGER_COUNTEREXAMPLE,
    SKILL_TRIGGER_POSITIVE,
    SKILL_TRIGGER_REJECTED,
    ProceduralSkill,
    SkillCounts,
    SkillEvidence,
    SkillStatus,
    SkillThresholds,
    day_text,
    derive_skill_state,
    skill_id_for,
)

#: 审计里属于本阶段的类型前缀
SKILL_EVENT_PREFIX = "skill."
#: 审计里的作用域键
SKILL_SCOPE = "procedural_skill"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


#: 证据 verdict → 派生状态用的触发词（7E.1：状态只由"触发 + 派生计数 + 阈值"决定）
_TRIGGER_BY_VERDICT = {
    "POSITIVE": SKILL_TRIGGER_POSITIVE,
    "COUNTEREXAMPLE": SKILL_TRIGGER_COUNTEREXAMPLE,
    "AMBIGUOUS": SKILL_TRIGGER_AMBIGUOUS,
    "REJECTED": SKILL_TRIGGER_REJECTED,
}


def _binding_id(task_id: str) -> str:
    return f"SKB-{str(task_id)}"


@dataclass(frozen=True)
class ClaimOutcome:
    """一次证据认领的结果（7E.1 §1：认领、计数、状态、绑定消费是**同一个**原子操作）。"""

    evidence: SkillEvidence
    created: bool
    skill: ProceduralSkill | None = None
    binding_consumed: bool = False


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
    async def claim_evidence(
        self,
        evidence: SkillEvidence,
        *,
        thresholds: SkillThresholds,
        new_skill: ProceduralSkill | None = None,
        attach_subject: bool = False,
        feedback: tuple[str, str] | None = None,
    ) -> ClaimOutcome: ...
    async def recent_evidence(
        self, limit: int = 20, *, skill_id: str = "", subject_key: str = ""
    ) -> list[SkillEvidence]: ...
    async def evidence_for_task(self, task_id: str) -> list[SkillEvidence]: ...
    async def skill_for_subject(
        self, *, character_id: str, server_id: str, subject_key: str
    ) -> ProceduralSkill | None: ...
    async def bind_task(
        self,
        *,
        task_id: str,
        skill_id: str,
        subject_key: str,
        plan_version: int,
        plan_hash: str,
        at: float,
        expires_at: float,
    ) -> bool: ...
    async def binding_for(self, task_id: str) -> dict[str, Any] | None: ...
    async def prune_bindings(self, *, now: float, keep_seconds: float = 86400.0) -> int: ...
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
        #: task_id -> skill binding (single consumption, expires with the task TTL)
        self._bindings: dict[str, dict[str, Any]] = {}
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

    async def claim_evidence(
        self,
        evidence: SkillEvidence,
        *,
        thresholds: SkillThresholds,
        new_skill: ProceduralSkill | None = None,
        attach_subject: bool = False,
        feedback: tuple[str, str] | None = None,
    ) -> ClaimOutcome:
        """Atomic claim. Every awaited call inside is itself await-free, so no other
        coroutine can slip between "claimed" and "counters written". Cross-process
        atomicity is the SQLite implementation's job (see the Sqlite store)."""
        key = (
            evidence.subject_key,
            evidence.task_id,
            int(evidence.plan_version),
            evidence.plan_hash,
        )
        if key in self._evidence_keys:
            existing = next(
                item
                for item in self._evidence
                if (
                    item.subject_key,
                    item.task_id,
                    int(item.plan_version),
                    item.plan_hash,
                )
                == key
            )
            current = self._rows.get(existing.skill_id) if existing.skill_id else None
            return ClaimOutcome(evidence=existing, created=False, skill=current)
        self._evidence_keys.add(key)

        binding_consumed = False
        skill_id = ""
        if feedback is not None:
            task_id, _bound = feedback
            row = self._bindings.get(str(task_id))
            if row is not None and float(row.get("consumed_at") or 0) == 0:
                row["consumed_at"] = float(evidence.at or 0)
                row["outcome"] = str(evidence.outcome)
                skill_id = str(row.get("skill_id") or "")
                binding_consumed = True
        elif evidence.skill_id:
            skill_id = str(evidence.skill_id)
        elif new_skill is not None:
            stored, _ = await self.create_skill(new_skill)
            skill_id = stored.skill_id
        elif attach_subject and evidence.subject_key:
            found = self._by_subject(evidence.subject_key)
            skill_id = found.skill_id if found is not None else ""

        if skill_id:
            stored_evidence = replace(evidence, skill_id=skill_id)
            self._evidence.append(stored_evidence)
            updated = self._apply_derived_state(
                skill_id,
                thresholds=thresholds,
                evidence=stored_evidence,
                reuse=feedback is not None,
            )
            return ClaimOutcome(
                evidence=stored_evidence,
                created=True,
                skill=updated,
                binding_consumed=binding_consumed,
            )
        self._evidence.append(evidence)
        return ClaimOutcome(
            evidence=evidence, created=True, skill=None, binding_consumed=binding_consumed
        )

    def _by_subject(self, subject_key: str) -> ProceduralSkill | None:
        for item in self._rows.values():
            if item.subject_key and item.subject_key == str(subject_key):
                return item
        return None

    def _counts_for(self, skill_id: str) -> SkillCounts:
        totals = {"POSITIVE": 0, "COUNTEREXAMPLE": 0, "AMBIGUOUS": 0, "REJECTED": 0}
        for item in self._evidence:
            if item.skill_id != str(skill_id):
                continue
            bucket = str(item.verdict)
            if bucket in totals:
                totals[bucket] += 1
        return SkillCounts(
            positive=totals["POSITIVE"],
            counterexample=totals["COUNTEREXAMPLE"],
            ambiguous=totals["AMBIGUOUS"],
            rejected=totals["REJECTED"],
        )

    def _apply_derived_state(
        self,
        skill_id: str,
        *,
        thresholds: SkillThresholds,
        evidence: SkillEvidence,
        reuse: bool,
    ) -> ProceduralSkill | None:
        """Derive counters/status from the evidence rows, then write the row once."""
        current = self._rows.get(str(skill_id))
        if current is None:
            return None
        counts = self._counts_for(skill_id)
        status, reason = derive_skill_state(
            current=SkillStatus(current.status),
            counts=counts,
            thresholds=thresholds,
            trigger=_TRIGGER_BY_VERDICT.get(str(evidence.verdict), SKILL_TRIGGER_REJECTED),
        )
        moment = float(evidence.at or 0)
        payload = current.to_payload()
        payload.update(
            {
                "success_count": counts.positive,
                "failure_count": counts.counterexample,
                "ambiguous_count": counts.ambiguous,
                "status": status.value,
                "reason": reason,
                "updated_at": moment,
                "last_used_at": moment if reuse else float(current.last_used_at or 0),
                "last_learned_at": (float(current.last_learned_at or 0) if reuse else moment),
                "last_verified_at": (
                    moment
                    if str(evidence.verdict) == "POSITIVE"
                    else float(current.last_verified_at or 0)
                ),
            }
        )
        updated = ProceduralSkill.from_payload(payload)
        self._rows[str(skill_id)] = updated
        return updated

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

    async def skill_for_subject(
        self, *, character_id: str, server_id: str, subject_key: str
    ) -> ProceduralSkill | None:
        for item in self._rows.values():
            if (
                item.character_id == str(character_id)
                and item.server_id == str(server_id)
                and item.subject_key
                and item.subject_key == str(subject_key)
            ):
                return item
        return None

    async def bind_task(
        self,
        *,
        task_id: str,
        skill_id: str,
        subject_key: str,
        plan_version: int,
        plan_hash: str,
        at: float,
        expires_at: float,
    ) -> bool:
        """Bind one *really created* task to a skill (task_id unique; no-op if present)."""
        key = str(task_id)
        if key in self._bindings:
            return False
        self._bindings[key] = {
            "binding_id": _binding_id(key),
            "task_id": key,
            "skill_id": str(skill_id),
            "subject_key": str(subject_key),
            "plan_version": int(plan_version),
            "plan_hash": str(plan_hash),
            "created_at": float(at),
            "expires_at": float(expires_at),
            "consumed_at": 0.0,
            "outcome": "",
        }
        return True

    async def binding_for(self, task_id: str) -> dict[str, Any] | None:
        row = self._bindings.get(str(task_id))
        return dict(row) if row is not None else None

    async def prune_bindings(self, *, now: float, keep_seconds: float = 86400.0) -> int:
        stale = [
            key
            for key, row in self._bindings.items()
            if float(row.get("expires_at") or 0) > 0
            and float(row["expires_at"]) + float(keep_seconds) < float(now)
        ]
        for key in stale:
            self._bindings.pop(key, None)
        return len(stale)

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
    " summary, status, character_id, server_id, environment, tools_signature, subject_key,"
    " max_risk, version, supersedes_skill_id, superseded_by, success_count, failure_count,"
    " ambiguous_count,"
    " fingerprint, reason, created_at, updated_at, last_learned_at, last_verified_at, last_used_at,"
    " payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

_SKILL_UPDATE = (
    "UPDATE procedural_skills SET schema_version = ?, name = ?, objective_pattern = ?, summary = ?,"
    " status = ?, character_id = ?, server_id = ?, environment = ?, tools_signature = ?,"
    " subject_key = ?, max_risk = ?, version = ?, supersedes_skill_id = ?, superseded_by = ?,"
    " success_count = ?,"
    " failure_count = ?, ambiguous_count = ?, fingerprint = ?, reason = ?, updated_at = ?,"
    " last_learned_at = ?, last_verified_at = ?, last_used_at = ?, payload = ?"
    " WHERE skill_id = ?"
)


def _evidence_params(evidence: SkillEvidence, payload: dict[str, Any]) -> tuple[Any, ...]:
    """列值顺序与 ``_EVIDENCE_INSERT`` 一致（认领与审计两处共用）。"""

    return (
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
    )


_EVIDENCE_INSERT = (
    "INSERT OR IGNORE INTO procedural_skill_evidence (evidence_id, skill_id, subject_key, task_id,"
    " plan_version, plan_hash, outcome, verdict, reason_code, step_ids, postcondition_kind,"
    " postcondition_ok, detail, created_at, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _skill_insert_params(
    skill_id: str, payload: dict[str, Any], fingerprint: str
) -> tuple[Any, ...]:
    """列值顺序与 ``_SKILL_INSERT`` 一致（单一真相：三处写入共用，避免占位符错位）。"""

    return (
        str(skill_id),
        int(payload["schema_version"]),
        payload["name"],
        payload["objective_pattern"],
        payload["summary"],
        payload["status"],
        payload["character_id"],
        payload["server_id"],
        payload["environment"],
        payload["tools_signature"],
        str(payload.get("subject_key") or ""),
        payload["max_risk"],
        int(payload["version"]),
        payload["supersedes_skill_id"],
        payload["superseded_by"],
        int(payload["success_count"]),
        int(payload["failure_count"]),
        int(payload["ambiguous_count"]),
        str(fingerprint),
        payload["reason"],
        float(payload["created_at"]),
        float(payload["updated_at"]),
        float(payload["last_learned_at"]),
        float(payload["last_verified_at"]),
        float(payload["last_used_at"]),
        _dump(payload),
    )


def _skill_update_params(
    payload: dict[str, Any], fingerprint: str, skill_id: str
) -> tuple[Any, ...]:
    """列值顺序与 ``_SKILL_UPDATE`` 一致（末尾的 skill_id 是 WHERE 条件）。"""

    return (
        int(payload["schema_version"]),
        payload["name"],
        payload["objective_pattern"],
        payload["summary"],
        payload["status"],
        payload["character_id"],
        payload["server_id"],
        payload["environment"],
        payload["tools_signature"],
        str(payload.get("subject_key") or ""),
        payload["max_risk"],
        int(payload["version"]),
        payload["supersedes_skill_id"],
        payload["superseded_by"],
        int(payload["success_count"]),
        int(payload["failure_count"]),
        int(payload["ambiguous_count"]),
        str(fingerprint),
        payload["reason"],
        float(payload["updated_at"]),
        float(payload["last_learned_at"]),
        float(payload["last_verified_at"]),
        float(payload["last_used_at"]),
        _dump(payload),
        str(skill_id),
    )


def _next_skill_id(conn: Any, day: str) -> str:
    prefix = f"{skill_id_for(day, 0).rsplit('-', 1)[0]}-"
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(skill_id, ?) AS INTEGER)) AS seq FROM procedural_skills"
        " WHERE skill_id LIKE ?",
        (len(prefix) + 1, f"{prefix}%"),
    ).fetchone()
    sequence = int((row[0] if row else 0) or 0) + 1
    return skill_id_for(day, sequence)


def _insert_skill_in_tx(conn: Any, skill: ProceduralSkill) -> str:
    """事务内插入技能（指纹唯一索引去重）→ 返回实际落库的 skill_id（空 = 已存在）。"""

    payload = skill.to_payload()
    skill_id = _next_skill_id(conn, day_text(skill.created_at or time.time()))
    payload["skill_id"] = skill_id
    cursor = conn.execute(_SKILL_INSERT, _skill_insert_params(skill_id, payload, skill.fingerprint))
    return skill_id if cursor.rowcount else ""


def _skill_in_tx(conn: Any, skill_id: str) -> ProceduralSkill | None:
    row = conn.execute(
        "SELECT payload FROM procedural_skills WHERE skill_id = ?", (str(skill_id),)
    ).fetchone()
    if row is None:
        return None
    return _from_skill_row({"payload": row["payload"]})


def _counts_in_tx(conn: Any, skill_id: str) -> SkillCounts:
    """证据计数由证据行**聚合派生**（并发认领时天然收敛，不会 lost update）。"""

    rows = conn.execute(
        "SELECT verdict, COUNT(*) AS n FROM procedural_skill_evidence WHERE skill_id = ?"
        " GROUP BY verdict",
        (str(skill_id),),
    ).fetchall()
    totals = {"POSITIVE": 0, "COUNTEREXAMPLE": 0, "AMBIGUOUS": 0, "REJECTED": 0}
    for row in rows:
        bucket = str(row["verdict"])
        if bucket in totals:
            totals[bucket] = int(row["n"] or 0)
    return SkillCounts(
        positive=totals["POSITIVE"],
        counterexample=totals["COUNTEREXAMPLE"],
        ambiguous=totals["AMBIGUOUS"],
        rejected=totals["REJECTED"],
    )


def _apply_derived_state_in_tx(
    conn: Any,
    skill_id: str,
    *,
    thresholds: SkillThresholds,
    evidence: SkillEvidence,
    reuse: bool,
) -> ProceduralSkill | None:
    current = _skill_in_tx(conn, skill_id)
    if current is None:
        return None
    counts = _counts_in_tx(conn, skill_id)
    status, reason = derive_skill_state(
        current=SkillStatus(current.status),
        counts=counts,
        thresholds=thresholds,
        trigger=_TRIGGER_BY_VERDICT.get(str(evidence.verdict), SKILL_TRIGGER_REJECTED),
    )
    moment = float(evidence.at or 0)
    payload = current.to_payload()
    payload.update(
        {
            "success_count": counts.positive,
            "failure_count": counts.counterexample,
            "ambiguous_count": counts.ambiguous,
            "status": status.value,
            "reason": reason,
            "updated_at": moment,
            "last_used_at": moment if reuse else float(current.last_used_at or 0),
            "last_learned_at": float(current.last_learned_at or 0) if reuse else moment,
            "last_verified_at": (
                moment
                if str(evidence.verdict) == "POSITIVE"
                else float(current.last_verified_at or 0)
            ),
        }
    )
    conn.execute(_SKILL_UPDATE, _skill_update_params(payload, current.fingerprint, skill_id))
    return ProceduralSkill.from_payload(payload)


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

        def _run(conn: Any) -> str:
            return _insert_skill_in_tx(conn, skill)

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
                _skill_update_params(payload, str(skill.fingerprint or ""), skill.skill_id),
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

    async def claim_evidence(
        self,
        evidence: SkillEvidence,
        *,
        thresholds: SkillThresholds,
        new_skill: ProceduralSkill | None = None,
        attach_subject: bool = False,
        feedback: tuple[str, str] | None = None,
    ) -> ClaimOutcome:
        """Atomic claim inside **one** transaction (7E.1 §1).

        Order matters: the UNIQUE index decides who owns this task's evidence; only the
        owner recomputes counters (aggregated from the evidence rows, so concurrent
        claims converge instead of losing updates) and derives the status. Reuse
        feedback additionally consumes the durable task binding in the same transaction,
        so a second terminal callback finds nothing to consume.
        """

        payload = evidence.to_payload()
        fingerprint = str(new_skill.fingerprint or "") if new_skill is not None else ""

        def _run(conn: Any) -> tuple[str, bool, str, bool]:
            cursor = conn.execute(_EVIDENCE_INSERT, _evidence_params(evidence, payload))
            if not cursor.rowcount:
                row = conn.execute(
                    "SELECT payload FROM procedural_skill_evidence WHERE subject_key = ?"
                    " AND task_id = ? AND plan_version = ? AND plan_hash = ?",
                    (
                        evidence.subject_key,
                        evidence.task_id,
                        int(evidence.plan_version),
                        evidence.plan_hash,
                    ),
                ).fetchone()
                return (str(row["payload"]) if row is not None else "", False, "", False)

            skill_id = ""
            consumed = False
            if feedback is not None:
                task_id, bound_skill = feedback
                cur = conn.execute(
                    "UPDATE procedural_skill_bindings SET consumed_at = ?, outcome = ?"
                    " WHERE task_id = ? AND consumed_at <= 0",
                    (float(evidence.at or 0), str(evidence.outcome), str(task_id)),
                )
                consumed = bool(cur.rowcount)
                if consumed:
                    skill_id = str(bound_skill or "")
            elif evidence.skill_id:
                skill_id = str(evidence.skill_id)
            elif new_skill is not None:
                created_id = _insert_skill_in_tx(conn, new_skill)
                if created_id:
                    skill_id = created_id
                else:
                    row = conn.execute(
                        "SELECT skill_id FROM procedural_skills WHERE fingerprint = ? LIMIT 1",
                        (fingerprint,),
                    ).fetchone()
                    skill_id = str(row["skill_id"] or "") if row is not None else ""
            elif attach_subject and evidence.subject_key:
                row = conn.execute(
                    "SELECT skill_id FROM procedural_skills WHERE subject_key = ?"
                    " ORDER BY updated_at DESC, id DESC LIMIT 1",
                    (str(evidence.subject_key),),
                ).fetchone()
                skill_id = str(row["skill_id"] or "") if row is not None else ""

            stored_payload = payload
            updated_payload = ""
            if skill_id:
                stored_payload = {**payload, "skill_id": skill_id}
                conn.execute(
                    "UPDATE procedural_skill_evidence SET skill_id = ? WHERE evidence_id = ?",
                    (skill_id, evidence.evidence_id),
                )
                applied = _apply_derived_state_in_tx(
                    conn,
                    skill_id,
                    thresholds=thresholds,
                    evidence=replace(evidence, skill_id=skill_id),
                    reuse=feedback is not None,
                )
                updated_payload = _dump(applied.to_payload()) if applied is not None else ""
            return (_dump(stored_payload), True, updated_payload, consumed)

        raw, created, skill_raw, consumed = await self._db.run_in_transaction(_run)
        stored = evidence
        if isinstance(raw, str) and raw:
            try:
                stored = SkillEvidence.from_payload(json.loads(raw))
            except (TypeError, ValueError):
                stored = evidence
        skill = None
        if isinstance(skill_raw, str) and skill_raw:
            try:
                skill = ProceduralSkill.from_payload(json.loads(skill_raw))
            except (TypeError, ValueError):
                skill = None
        return ClaimOutcome(
            evidence=stored, created=bool(created), skill=skill, binding_consumed=bool(consumed)
        )

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

    async def skill_for_subject(
        self, *, character_id: str, server_id: str, subject_key: str
    ) -> ProceduralSkill | None:
        row = await self._db.fetchone(
            "SELECT payload FROM procedural_skills WHERE character_id = ? AND server_id = ?"
            " AND subject_key = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
            (str(character_id), str(server_id), str(subject_key)),
        )
        return _from_skill_row(row)

    async def bind_task(
        self,
        *,
        task_id: str,
        skill_id: str,
        subject_key: str,
        plan_version: int,
        plan_hash: str,
        at: float,
        expires_at: float,
    ) -> bool:
        """Persist one task -> skill binding (task_id is UNIQUE; duplicate = no-op)."""

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO procedural_skill_bindings (binding_id, task_id, skill_id,"
                " subject_key, plan_version, plan_hash, created_at, expires_at, consumed_at,"
                " outcome, reason, payload) VALUES (?,?,?,?,?,?,?,?,0,'','',?)",
                (
                    _binding_id(str(task_id)),
                    str(task_id),
                    str(skill_id),
                    str(subject_key),
                    int(plan_version),
                    str(plan_hash),
                    float(at),
                    float(expires_at),
                    _dump(
                        {
                            "task_id": str(task_id),
                            "skill_id": str(skill_id),
                            "subject_key": str(subject_key),
                        }
                    ),
                ),
            )
            return bool(cursor.rowcount)

        return bool(await self._db.run_in_transaction(_run))

    async def binding_for(self, task_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT binding_id, task_id, skill_id, subject_key, plan_version, plan_hash,"
            " created_at, expires_at, consumed_at, outcome FROM procedural_skill_bindings"
            " WHERE task_id = ?",
            (str(task_id),),
        )
        return dict(row) if row is not None else None

    async def prune_bindings(self, *, now: float, keep_seconds: float = 86400.0) -> int:
        """Housekeeping: drop bindings whose task TTL expired long ago (never affects live ones)."""

        def _run(conn: Any) -> int:
            cursor = conn.execute(
                "DELETE FROM procedural_skill_bindings WHERE expires_at > 0 AND expires_at < ?",
                (float(now) - float(keep_seconds),),
            )
            return int(cursor.rowcount or 0)

        return int(await self._db.run_in_transaction(_run) or 0)

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
