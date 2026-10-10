"""Phase 7E §7.2/§7.4/§7.6：程序性技能服务（学习 → 检索 → 适用性 → 计划候选 → 回流）。

边界（任务书 §7.5/§10 硬约束）：

* **没有执行能力**：本模块不 import Mineflayer / ActionRuntime / MinecraftService，
  不调用任何 Minecraft 工具，也不调 ``TaskRuntime.create_task`` —— 它只产出
  ``PlannedTask``（计划候选），交给既有入口 → ``TaskRuntime.create_task`` → ``validate_plan``
  → 既有批准/确认 → 既有执行链。
* **观察只用既有 SAFE 通道**（构造时注入的 ``observe``，与规划器同一条工具通道）。
* **任何异常都降级**：``degraded_reason`` 置位后学习与检索整体不可用，既有聊天/规划/任务
  行为逐字不变（§7.2 关键负向清单最后两条）。
* 技能的文本/字段一律当**数据**：不改变任何 gate、不降低风险、不绕过确认（§7.5）。

生命周期（§7.1/§7.2 两段式）：

``第一次合格任务`` → CANDIDATE（记录，**不参与复用**）→ ``独立第二条合格任务`` → ACTIVE
→ 复用后成功 = 追加正向证据；复用后失败 = 反例 → STALE（再失败一次 → INVALIDATED）。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.tasks.models import ExpectedFinalState, TaskPlan, TaskState, TaskStep
from app.tasks.planner import Observation, PlannedTask
from app.tasks.skill import (
    SKILL_SCHEMA_VERSION,
    Applicability,
    EvidenceVerdict,
    ProceduralSkill,
    SkillEvidence,
    SkillStatus,
    SkillThresholds,
    subject_key_for,
)
from app.tasks.skill_learning import (
    POSITION_SLOT,
    WORLD_CHANGING_TOOLS,
    LearningInput,
    qualify,
)
from app.tasks.validation import collect_references, validate_plan

#: 计划里携带技能引用的观察条目标识（**不进 plan_hash**：只做审计与回流归因）
SKILL_REFERENCE_TOOL = "skill_reference"
#: 学习账本证据 id 前缀
EVIDENCE_ID_PREFIX = "EV"
#: 检索上限（有界，§7.4）
DEFAULT_RETRIEVE_LIMIT = 5
#: 默认晋升阈值：两条**独立**合格证据（§7.2 的"独立成功证据"）
DEFAULT_PROMOTION_MIN_SUCCESSES = 2
#: 默认失效阈值：复用后连续两条反例 → INVALIDATED
DEFAULT_INVALIDATE_AFTER_FAILURES = 2
#: dig 时长信号阈值（实测 < 预期 × 该比例 → 判定"挖到之前方块就没了"）
DEFAULT_DIG_SHORT_RATIO = 0.5


@dataclass(frozen=True)
class ToolsSnapshot:
    """当前工具契约快照（由装配处用真实工具运行时算好，技能无权改它）。"""

    registered: frozenset[str] = frozenset()
    risks: Mapping[str, str] = field(default_factory=dict)
    signature: str = ""


@dataclass(frozen=True)
class Assessment:
    """适用性评估结论（§7.4）：可审计，``UNKNOWN`` 永不自动升级为适用。"""

    verdict: Applicability
    reason_code: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def applicable(self) -> bool:
        return self.verdict is Applicability.APPLICABLE


def tools_signature_of(
    names: list[str] | tuple[str, ...] | set[str],
    risks: Mapping[str, str],
    schemas: Mapping[str, Any] | Callable[[str], Any] | None = None,
) -> str:
    """工具契约指纹：名字 + 风险 + schema 形状（任一项变化都会让旧技能转 STALE）。"""

    def _schema(tool: str) -> Any:
        if schemas is None:
            return None
        if callable(schemas):
            return schemas(tool)
        return schemas.get(tool)

    parts: list[str] = []
    for name in sorted(str(item) for item in names):
        schema = _schema(name)
        try:
            blob = json.dumps(schema, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            blob = str(schema)
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
        parts.append(f"{name}:{str(risks.get(name, '') or '')}:{digest}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def default_classifier(objective: str) -> str:
    """兜底方法类键（不知道方块词表时）：只做空白归一。生产装配会注入方块感知的分类器。"""

    return "text:" + " ".join(str(objective or "").split()).lower()[:120]


class SkillService:
    """技能学习与复用的唯一门面（学习、检索、适用性、物化、回流、只读视图）。"""

    def __init__(
        self,
        *,
        store: Any,
        config: Any = None,
        character_id: str = "",
        observe: Any = None,
        server_id: Callable[[], str] | None = None,
        tools_snapshot: Callable[[], ToolsSnapshot] | None = None,
        classify: Callable[[str], str] | None = None,
        schema_of: Callable[[str], Mapping[str, Any] | None] | None = None,
        validate_arguments: Any = None,
        allowed_risks: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.time,
        logger: Any = None,
    ) -> None:
        self.store = store
        self.config = config
        self.character_id = str(character_id)
        self.observe = observe
        self._server_id = server_id or (lambda: "")
        self._tools_snapshot = tools_snapshot or (lambda: ToolsSnapshot())
        self._classify = classify or default_classifier
        self._schema_of = schema_of or (lambda _tool: None)
        self._validate_arguments = validate_arguments
        #: 风险是否被当前配置允许（例如 allow_medium=false 时 MEDIUM 技能不可复用）
        self._allowed_risks = allowed_risks or (lambda _risk: True)
        self._clock = clock
        self._log = logger
        #: 降级原因（非空 = 学习/检索整体不可用；既有链路完全不受影响）
        self.degraded_reason = ""
        self.learned_count = 0
        self.promoted_count = 0
        self.rejected_count = 0
        self.ambiguous_count = 0
        self.retrieval_count = 0

    # ------------------------------------------------------------ 配置读法

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True)) if self.config is not None else True

    @property
    def retrieve_limit(self) -> int:
        return int(
            getattr(self.config, "retrieve_limit", DEFAULT_RETRIEVE_LIMIT) or DEFAULT_RETRIEVE_LIMIT
        )

    @property
    def promotion_min_successes(self) -> int:
        return max(
            1,
            int(
                getattr(self.config, "promotion_min_successes", DEFAULT_PROMOTION_MIN_SUCCESSES)
                or DEFAULT_PROMOTION_MIN_SUCCESSES
            ),
        )

    @property
    def invalidate_after_failures(self) -> int:
        return max(
            1,
            int(
                getattr(self.config, "invalidate_after_failures", DEFAULT_INVALIDATE_AFTER_FAILURES)
                or DEFAULT_INVALIDATE_AFTER_FAILURES
            ),
        )

    @property
    def thresholds(self) -> SkillThresholds:
        """晋升/失效阈值（存储层在自己的事务里用它派生状态，保证与计数同一原子操作）。"""

        return SkillThresholds(
            promotion_min_successes=self.promotion_min_successes,
            invalidate_after_failures=self.invalidate_after_failures,
        )

    @property
    def dig_short_ratio(self) -> float:
        return float(
            getattr(self.config, "dig_short_ratio", DEFAULT_DIG_SHORT_RATIO)
            or DEFAULT_DIG_SHORT_RATIO
        )

    # ------------------------------------------------------------ 学习（§7.2）

    async def bind_task_for_plan(self, record: Any, plan: Any = None) -> str:
        """任务**真正建立之后**才登记"这条任务用了哪条技能"（7E.1 §2）。

        * 只认计划里那条 ``skill_reference``（进程内/已持久化的计划 payload 都行）；
        * 绑定按 ``task_id`` 唯一、可消费一次、随任务 TTL 失效；失败只降级（不猜来源）；
        * 没有引用（基规划器计划）→ 什么都不做，返回空串。
        """

        if not self.enabled:
            return ""
        task_id = str(getattr(record, "task_id", "") or "")
        if not task_id:
            return ""
        reference = _reference_from_plan(
            plan if plan is not None else getattr(record, "plan", None)
        )
        skill_id = str(reference.get("skill_id") or "")
        if not skill_id:
            return ""
        try:
            skill = await self.store.get(skill_id)
        except Exception as exc:  # noqa: BLE001 - 读不到就不绑（保守：宁可不反馈）
            self.degraded_reason = type(exc).__name__
            self._warn("[Skill] 绑定前读取失败（不绑定）：%s", type(exc).__name__)
            return ""
        if skill is None:
            self._warn("[Skill] 计划引用的技能不存在（不绑定）skill=%s", skill_id)
            return ""
        expires_at = float(getattr(record, "expires_at", 0.0) or 0.0)
        try:
            bound = await self.store.bind_task(
                task_id=task_id,
                skill_id=skill_id,
                subject_key=str(skill.subject_key or skill.extra.get("subject_key") or ""),
                plan_version=int(getattr(record, "plan_version", 0) or 0),
                plan_hash=str(getattr(record, "plan_hash", "") or ""),
                at=self._clock(),
                expires_at=expires_at,
            )
        except Exception as exc:  # noqa: BLE001 - 绑定失败只降级
            self.degraded_reason = type(exc).__name__
            self._warn("[Skill] 绑定失败（不反馈）：%s", type(exc).__name__)
            return ""
        if bound:
            await self.store.log_event(
                skill_id=skill_id,
                event="skill.bound",
                reason="task_created",
                at=self._clock(),
                detail={
                    "task_id": task_id,
                    "plan_hash": str(getattr(record, "plan_hash", "") or ""),
                },
            )
        return skill_id

    def skill_reference_of(self, record: Any) -> dict[str, Any]:
        """从持久化 TaskRecord 读"这次是不是用了某条技能"（只读观察条目，不进 plan_hash）。"""

        plan = getattr(record, "plan", None)
        observations = getattr(plan, "observations", None) or []
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("tool") or "") != SKILL_REFERENCE_TOOL:
                continue
            result = item.get("result")
            if isinstance(result, Mapping):
                return dict(result)
        return {}

    async def on_task_finished(self, record: Any) -> dict[str, Any]:
        """任务终态 → 学习或回流。**只读记录**；任何异常只降级，不抛给调用方。"""

        if not self.enabled:
            return {"action": "disabled"}
        task_id = str(getattr(record, "task_id", "") or "")
        if not task_id:
            return {"action": "skipped", "reason": "no_task_id"}
        try:
            return await self._on_task_finished(record)
        except Exception as exc:  # noqa: BLE001 - 学习失败绝不波及任务链
            self.degraded_reason = type(exc).__name__
            self._warn("[Skill] 学习降级：%s", type(exc).__name__)
            return {"action": "degraded", "reason": type(exc).__name__}

    async def _on_task_finished(self, record: Any) -> dict[str, Any]:
        state = getattr(getattr(record, "state", None), "value", getattr(record, "state", ""))
        outcome = str(state or "")
        # §7.6 / 7E.1 §2：归因**只**看这条任务自己的持久绑定（task_id 唯一）。
        # 查不到绑定就绝不猜来源 —— 退回普通学习资格门（不反馈到任何技能）。
        task_id = str(getattr(record, "task_id", "") or "")
        binding = None
        if task_id:
            try:
                binding = await self.store.binding_for(task_id)
            except Exception:  # noqa: BLE001 - 读不到就当作没有绑定（保守）
                binding = None
        if binding is not None and float(binding.get("consumed_at") or 0) == 0:
            return await self._record_reuse(binding, record, outcome=outcome)
        return await self._learn(record, outcome=outcome)

    async def _learn(self, record: Any, *, outcome: str) -> dict[str, Any]:
        snapshot = self._tools_snapshot()
        objective = str(getattr(record, "objective", "") or "")
        method_class = self._classify(objective)
        subject_key = subject_key_for(
            character_id=self.character_id,
            server_id=self._server_id(),
            objective_pattern=method_class,
        )
        step_ids = tuple(
            str(getattr(step, "step_id", "") or "")
            for step in (getattr(record.plan, "steps", None) or [])
        )
        # 幂等（§7.2 第 4 条）：同一 (方法主题, task, plan 版本, plan hash) 只算一次。
        # 这里只是"省一次后置条件观察"的**快速路径**；权威判据是 claim_evidence 里
        # 证据表的唯一索引（7E.1 §1：认领、计数、状态、绑定消费是同一个原子操作）。
        existing = await self.store.evidence_for_task(str(record.task_id))
        plan_version = int(getattr(record, "plan_version", 0) or 0)
        plan_hash = str(getattr(record, "plan_hash", "") or "")
        for item in existing:
            if (
                item.subject_key == subject_key
                and int(item.plan_version) == plan_version
                and item.plan_hash == plan_hash
            ):
                # 已经算过（重复终态事件 / 重复回调 / 重启恢复）→ 不再累计
                return {
                    "action": "duplicate",
                    "evidence_id": item.evidence_id,
                    "verdict": item.verdict,
                }

        task_effect_verified, postconditions = await self._postconditions(record)
        dig_expected = self._expected_dig_ms(record)
        result = qualify(
            LearningInput(
                record=record,
                character_id=self.character_id,
                server_id=self._server_id(),
                tools_signature=snapshot.signature,
                registered_tools=snapshot.registered,
                risk_table=dict(snapshot.risks),
                postconditions=postconditions,
                task_effect_verified=task_effect_verified,
                dig_expected_ms=dig_expected,
                dig_short_ratio=self.dig_short_ratio,
                method_class=method_class,
                objective=objective,
            )
        )
        now = self._clock()
        # 指纹已存在时把目标技能直接写进证据（存储层据此附着 → 计数由证据行派生）
        existing = await self.store.by_fingerprint(result.fingerprint)
        evidence = SkillEvidence(
            evidence_id=f"{EVIDENCE_ID_PREFIX}-{str(record.task_id)}-{plan_version}",
            skill_id=str(existing.skill_id) if existing is not None else "",
            subject_key=subject_key,
            task_id=str(record.task_id),
            plan_version=plan_version,
            plan_hash=plan_hash,
            step_ids=step_ids,
            outcome=outcome,
            verdict=result.verdict.value,
            reason_code=result.reason_code,
            postcondition_kind=result.postcondition_kind,
            postcondition_ok=bool(result.postcondition_ok),
            detail=dict(result.detail),
            at=now,
        )
        if not result.ok:
            claim = await self.store.claim_evidence(
                evidence, thresholds=self.thresholds, attach_subject=True
            )
            if not claim.created:
                return {
                    "action": "duplicate",
                    "evidence_id": claim.evidence.evidence_id,
                    "verdict": claim.evidence.verdict,
                }
            if result.verdict is EvidenceVerdict.AMBIGUOUS:
                self.ambiguous_count += 1
            else:
                self.rejected_count += 1
            await self.store.log_event(
                skill_id=claim.evidence.skill_id,
                event="skill.evidence_rejected",
                reason=result.reason_code,
                at=now,
                detail={
                    "task_id": str(record.task_id),
                    "verdict": result.verdict.value,
                    "attached": bool(claim.evidence.skill_id),
                },
            )
            return {
                "action": "rejected",
                "reason": result.reason_code,
                "verdict": result.verdict.value,
                "evidence_id": claim.evidence.evidence_id,
            }

        # 正向：指纹不存在时先备好"新技能"对象（版本 lineage 一起带上），
        # 具体创建发生在 claim_evidence 的事务里（唯一索引保证并发只建一条）。
        new_skill: ProceduralSkill | None = None
        lineage: ProceduralSkill | None = None
        if existing is None:
            lineage = await self._previous_version(result.method_class)
            new_skill = self._new_skill_object(
                result, record, now=now, subject_key=subject_key, lineage=lineage
            )
        claim = await self.store.claim_evidence(
            evidence, thresholds=self.thresholds, new_skill=new_skill
        )
        if not claim.created:
            return {
                "action": "duplicate",
                "evidence_id": claim.evidence.evidence_id,
                "verdict": claim.evidence.verdict,
            }
        skill = claim.skill
        if skill is None:
            # 认领成功但技能行读不回来（存储故障）→ 降级，不假装学会
            self.degraded_reason = "skill_missing_after_claim"
            return {"action": "degraded", "reason": "skill_missing_after_claim"}
        if lineage is not None:
            # 版本 lineage 的"旧版转 STALE"是幂等收尾写（不参与认领原子性；失败只降级）
            await self._supersede_previous(lineage, new_skill_id=skill.skill_id, now=now)
        self.learned_count += 1
        await self.store.log_event(
            skill_id=skill.skill_id,
            event="skill.learned",
            reason=result.reason_code,
            at=now,
            detail={
                "task_id": str(record.task_id),
                "status": skill.status,
                "success_count": int(skill.success_count),
            },
        )
        return {
            "action": "learned",
            "skill_id": skill.skill_id,
            "status": skill.status,
            "success_count": skill.success_count,
            "new": existing is None,
        }

    def _new_skill_object(
        self,
        result: Any,
        record: Any,
        *,
        now: float,
        subject_key: str,
        lineage: ProceduralSkill | None,
    ) -> ProceduralSkill:
        """备好"新技能"对象（真正的创建发生在 claim_evidence 的事务里）。"""

        return ProceduralSkill(
            name=str(getattr(record, "objective", "") or "")[:80],
            objective_pattern=result.method_class,
            summary=f"{len(result.steps)} 步方法（来自真实成功任务）",
            status=SkillStatus.CANDIDATE.value,
            character_id=self.character_id,
            server_id=self._server_id(),
            tools_signature=self._tools_snapshot().signature,
            subject_key=str(subject_key),
            slots=result.slots,
            steps=result.steps,
            preconditions=result.preconditions,
            success_criteria=result.success_criteria,
            required_capabilities=result.required_capabilities,
            risk_summary={"max_risk": result.max_risk},
            max_risk=result.max_risk,
            version=1 if lineage is None else int(lineage.version) + 1,
            supersedes_skill_id="" if lineage is None else lineage.skill_id,
            fingerprint=result.fingerprint,
            success_count=1,
            created_at=now,
            updated_at=now,
            last_learned_at=now,
            last_verified_at=now,
            reason="candidate_from_first_success",
            extra={
                "subject_key": subject_key,
                "sample_objective": str(getattr(record, "objective", "") or "")[:120],
                "source_task_id": str(record.task_id),
                #: 目标方块（方法类键的一部分；物化时用它做新鲜 find_blocks）
                "target_block": _target_block_of(result.steps),
            },
        )

    async def _supersede_previous(
        self, lineage: ProceduralSkill, *, new_skill_id: str, now: float
    ) -> None:
        """版本 lineage 的收尾写：旧版转 STALE 并回链（幂等；失败只降级）。"""

        try:
            superseded = ProceduralSkill.from_payload(lineage.to_payload())
            if superseded.superseded_by == new_skill_id:
                return
            superseded.status = SkillStatus.STALE.value
            superseded.superseded_by = str(new_skill_id)
            superseded.reason = f"superseded_by:{new_skill_id}"
            superseded.updated_at = now
            await self.store.replace_skill(superseded)
            await self.store.log_event(
                skill_id=superseded.skill_id,
                event="skill.superseded",
                reason=f"new_version:{new_skill_id}",
                at=now,
                detail={"version": int(superseded.version)},
            )
        except Exception as exc:  # noqa: BLE001 - lineage 收尾失败不影响新技能
            self.degraded_reason = type(exc).__name__
            self._warn("[Skill] lineage 收尾失败：%s", type(exc).__name__)

    async def _previous_version(self, method_class: str) -> ProceduralSkill | None:
        """同方法类的**上一条**技能（用来接版本 lineage；终态的不算）。"""

        row = await self.store.latest_for_method(
            character_id=self.character_id,
            server_id=self._server_id(),
            method_class=str(method_class),
        )
        if row is None or row.terminal:
            return None
        return row

    async def _record_reuse(
        self, binding: Mapping[str, Any], record: Any, *, outcome: str
    ) -> dict[str, Any]:
        """复用回流（§7.6 + 7E.1 §2）：**只**按这条任务自己的持久绑定归因。

        认领证据、消费绑定、重算计数、派生状态全在存储层的同一个事务里完成；
        绑定已被消费（重复终态回调）→ 直接 duplicate，不再动任何计数。
        """

        now = self._clock()
        task_id = str(getattr(record, "task_id", "") or "")
        skill_id = str(binding.get("skill_id") or "")
        subject_key = str(binding.get("subject_key") or "")
        plan_version = int(getattr(record, "plan_version", 0) or 0)
        plan_hash = str(getattr(record, "plan_hash", "") or "")
        if not skill_id:
            return {"action": "skipped", "reason": "binding_without_skill", "task_id": task_id}
        evidence = SkillEvidence(
            evidence_id=f"{EVIDENCE_ID_PREFIX}-{task_id}-{plan_version}",
            skill_id=skill_id,
            subject_key=subject_key,
            task_id=task_id,
            plan_version=plan_version,
            plan_hash=plan_hash,
            step_ids=tuple(
                str(getattr(step, "step_id", "") or "")
                for step in (getattr(record.plan, "steps", None) or [])
            ),
            outcome=outcome,
            verdict=(
                EvidenceVerdict.POSITIVE.value
                if outcome == TaskState.SUCCEEDED.value
                else EvidenceVerdict.COUNTEREXAMPLE.value
            ),
            reason_code=(
                "reuse_succeeded"
                if outcome == TaskState.SUCCEEDED.value
                else f"reuse_{outcome.lower()}"
            ),
            at=now,
        )
        claim = await self.store.claim_evidence(
            evidence,
            thresholds=self.thresholds,
            feedback=(task_id, skill_id),
        )
        if not claim.created:
            return {"action": "duplicate", "evidence_id": claim.evidence.evidence_id}
        if not claim.binding_consumed:
            # 绑定在别处已经被消费（并发第二次回调）→ 认领到的证据也不再算反馈
            return {"action": "duplicate", "evidence_id": claim.evidence.evidence_id}
        skill = claim.skill
        if skill is None:
            return {"action": "feedback", "skill_id": skill_id, "verdict": "unknown", "status": ""}
        verdict = "reuse_ok"
        if skill.status == SkillStatus.INVALIDATED.value:
            verdict = "invalidated"
        elif skill.status == SkillStatus.STALE.value and outcome != TaskState.SUCCEEDED.value:
            verdict = "stale"
        elif outcome == TaskState.SUCCEEDED.value and skill.status == SkillStatus.ACTIVE.value:
            verdict = "reuse_ok"
        await self.store.log_event(
            skill_id=skill_id,
            event="skill.feedback",
            reason=verdict,
            at=now,
            detail={
                "task_id": task_id,
                "status": skill.status,
                "success_count": int(skill.success_count),
                "failure_count": int(skill.failure_count),
            },
        )
        return {
            "action": "feedback",
            "skill_id": skill_id,
            "verdict": verdict,
            "status": skill.status,
        }

    # ------------------------------------------------------------ 后置条件（学习期，只读）

    async def _postconditions(self, record: Any) -> tuple[bool, dict[str, tuple[bool, str]]]:
        """改动世界的步骤 → 独立后置条件结论（§7.2 第 5 条）。

        主力依据是**运行时自己的**独立验证（``record.verification.inventory_delta``，
        由 ``_finish`` 用新鲜 SAFE 背包读算出）；若此刻能拿到新鲜 SAFE 读，再补一条
        "目标方块确实不在了"的复核（读回原方块 → 判定不可信 → 保守为不满足）。
        """

        plan = getattr(record, "plan", None)
        expected = dict(
            getattr(getattr(plan, "expected_final_state", None), "inventory_delta", None) or {}
        )
        verification = getattr(record, "verification", None)
        delta = dict((verification or {}).get("inventory_delta") or {})
        covered = bool(expected) and all(
            int(delta.get(item, 0) or 0) >= int(count) for item, count in expected.items()
        )
        out: dict[str, tuple[bool, str]] = {}
        for step in getattr(plan, "steps", None) or []:
            tool = str(getattr(step, "tool", "") or "")
            if tool not in WORLD_CHANGING_TOOLS:
                continue
            step_id = str(getattr(step, "step_id", "") or "")
            if tool != "minecraft_dig" or self.observe is None:
                continue
            # 只有 dig 有"位置型"证据：新鲜 SAFE 复核目标方块确实不在了
            fresh = await self._fresh_block_absent(step)
            if fresh is not None:
                out[step_id] = (fresh and covered, "block_absent" if fresh else "")
        return covered, out

    async def _fresh_block_absent(self, step: Any) -> bool | None:
        """新鲜 SAFE 复核：目标位置已经不是原方块。读不到/离线 → None（不参与判定）。"""

        arguments = dict(
            getattr(step, "effective_arguments", None) or getattr(step, "arguments", {}) or {}
        )
        position = {key: _coord(arguments, key) for key in ("x", "y", "z")}
        if any(value is None for value in position.values()):
            return None
        position = {key: int(value) for key, value in position.items() if value is not None}
        try:
            result = await self.observe("minecraft_dig_capability", position)
        except Exception:  # noqa: BLE001 - 读不到就不参与判定（保守）
            return None
        payload = getattr(result, "result", None)
        if not isinstance(payload, Mapping):
            return None
        block = payload.get("block")
        name = str((block or {}).get("name") if isinstance(block, Mapping) else "")
        reason = str(payload.get("reason") or "")
        if reason == "air":
            return True
        if name:
            return False
        return None

    def _expected_dig_ms(self, record: Any) -> dict[str, int]:
        """计划 SAFE 观察里的预期挖掘时长（``dig_capability.dig_time_ms``）→ 按坐标对回步骤。"""

        plan = getattr(record, "plan", None)
        probes: list[tuple[tuple[int, int, int], int]] = []
        for item in getattr(plan, "observations", None) or []:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("tool") or "") != "minecraft_dig_capability":
                continue
            arguments = item.get("arguments") or {}
            result = item.get("result") or {}
            key = _point_of(arguments)
            if key is None:
                continue
            value = result.get("dig_time_ms") if isinstance(result, Mapping) else None
            if isinstance(value, (int, float)) and value > 0:
                probes.append((key, int(value)))
        out: dict[str, int] = {}
        for step in getattr(plan, "steps", None) or []:
            if str(getattr(step, "tool", "")) != "minecraft_dig":
                continue
            arguments = dict(
                getattr(step, "effective_arguments", None) or getattr(step, "arguments", {}) or {}
            )
            key = _point_of(arguments)
            if key is None:
                continue
            for probe_key, value in probes:
                if probe_key == key:
                    out[str(getattr(step, "step_id", "") or "")] = value
                    break
        return out

    # ------------------------------------------------------------ 检索 + 适用性（§7.4）

    async def candidates_for(self, objective: str) -> list[ProceduralSkill]:
        """有界检索：只找 ``ACTIVE``（§7.2：未验证候选绝不参与复用）。异常/降级一律返回空。"""

        if not self.enabled:
            return []
        try:
            method_class = self._classify(objective)
            rows = await self.store.active_for(
                character_id=self.character_id,
                server_id=self._server_id(),
                method_class=method_class,
                limit=self.retrieve_limit,
            )
            self.retrieval_count += 1
            return list(rows)
        except Exception as exc:  # noqa: BLE001 - 检索失败 = 回退既有规划器
            self.degraded_reason = type(exc).__name__
            self._warn("[Skill] 检索降级：%s", type(exc).__name__)
            return []

    async def assess(self, skill: ProceduralSkill, *, objective: str = "") -> Assessment:
        """适用性评估（§7.4）：封闭检查项 + 稳定原因码；观察失败 = ``UNKNOWN``。"""

        snapshot = self._tools_snapshot()
        if skill.status_enum is not SkillStatus.ACTIVE:
            return Assessment(
                Applicability.STALE, f"skill_not_active:{skill.status}", {"status": skill.status}
            )
        if int(skill.schema_version) != int(SKILL_SCHEMA_VERSION):
            return Assessment(Applicability.STALE, "schema_version_mismatch")
        if skill.character_id and skill.character_id != self.character_id:
            return Assessment(Applicability.INAPPLICABLE, "character_mismatch")
        if skill.server_id and skill.server_id != self._server_id():
            return Assessment(Applicability.INAPPLICABLE, "server_mismatch")
        if (
            skill.tools_signature
            and snapshot.signature
            and skill.tools_signature != snapshot.signature
        ):
            return Assessment(
                Applicability.STALE,
                "tools_signature_mismatch",
                {"skill": skill.tools_signature, "now": snapshot.signature},
            )
        for tool in skill.required_capabilities:
            if tool not in snapshot.registered:
                return Assessment(Applicability.INAPPLICABLE, f"tool_missing:{tool}")
        if not self._allowed_risks(str(skill.max_risk)):
            return Assessment(Applicability.INAPPLICABLE, f"risk_not_allowed:{skill.max_risk}")
        if not skill.steps:
            return Assessment(Applicability.INAPPLICABLE, "no_steps")
        # ---- 前置条件：只用既有 SAFE 观察；读不到 = UNKNOWN（绝不当作满足）
        for precondition in skill.preconditions:
            verdict = await self._check_precondition(precondition)
            if not verdict.applicable:
                return verdict
        return Assessment(Applicability.APPLICABLE, "applicable")

    async def _check_precondition(self, precondition: Any) -> Assessment:
        kind = str(getattr(precondition, "kind", "") or "")
        params = dict(getattr(precondition, "params", None) or {})
        if kind == "inventory_has":
            if self.observe is None:
                return Assessment(Applicability.UNKNOWN, "observe_unavailable")
            try:
                result = await self.observe("minecraft_inventory", {})
            except Exception:  # noqa: BLE001
                return Assessment(Applicability.UNKNOWN, "observe_failed")
            payload = getattr(result, "result", None)
            if not isinstance(payload, Mapping):
                return Assessment(Applicability.UNKNOWN, "observe_unreadable")
            item = str(params.get("item") or "")
            need = int(params.get("count") or 1)
            have = 0
            for row in payload.get("items") or []:
                if isinstance(row, Mapping) and str(row.get("name") or "") == item:
                    have = int(row.get("count") or 0)
            held = payload.get("held_item")
            if isinstance(held, Mapping) and str(held.get("name") or "") == item:
                have = max(have, int(held.get("count") or 0))
            if have >= need:
                return Assessment(Applicability.APPLICABLE, "inventory_ok")
            return Assessment(Applicability.INAPPLICABLE, f"inventory_missing:{item}")
        return Assessment(Applicability.INAPPLICABLE, f"precondition_unsupported:{kind}")

    # ------------------------------------------------------------ 计划候选（§7.5）

    async def suggest(self, objective: str) -> PlannedTask | None:
        """给既有入口用的**计划候选**：适用性通过 + 门控通过才返回；否则 None（回退）。"""

        if not self.enabled or self.observe is None:
            return None
        try:
            for skill in await self.candidates_for(objective):
                assessment = await self.assess(skill, objective=objective)
                if not assessment.applicable:
                    await self.store.log_event(
                        skill_id=skill.skill_id,
                        event="skill.applicability",
                        reason=assessment.reason_code,
                        at=self._clock(),
                        detail={"verdict": assessment.verdict.value, "objective": objective[:120]},
                    )
                    continue
                planned = await self.materialize(skill, objective=objective)
                if planned is None:
                    await self.store.log_event(
                        skill_id=skill.skill_id,
                        event="skill.materialize_failed",
                        reason="plan_not_valid",
                        at=self._clock(),
                        detail={"objective": objective[:120]},
                    )
                    continue
                await self.store.log_event(
                    skill_id=skill.skill_id,
                    event="skill.reused",
                    reason="applicable",
                    at=self._clock(),
                    detail={"objective": objective[:120]},
                )
                return planned
            return None
        except Exception as exc:  # noqa: BLE001 - 技能异常 = 回退既有规划器
            self.degraded_reason = type(exc).__name__
            self._warn("[Skill] 复用降级：%s", type(exc).__name__)
            return None

    async def materialize(self, skill: ProceduralSkill, *, objective: str) -> PlannedTask | None:
        """把技能模板物化成**经 schema 校验**的计划候选（坐标槽位用新鲜 SAFE 观察解析）。"""

        sample = str(skill.extra.get("sample_objective") or "")
        block = self._target_block(skill)
        if not block:
            return None
        observations: list[Observation] = []
        found = await self._safe_observe(
            "minecraft_find_blocks",
            {"block_names": [block], "max_distance": 16, "max_results": 8},
        )
        if found is None:
            return None
        result = getattr(found, "result", None)
        matches = list((result or {}).get("matches") or []) if isinstance(result, Mapping) else []
        if not matches:
            return None
        position = dict((matches[0] or {}).get("position") or {})
        if not all(key in position for key in ("x", "y", "z")):
            return None
        observations.append(
            Observation(
                tool="minecraft_find_blocks",
                arguments={"block_names": [block], "max_distance": 16, "max_results": 8},
                result=dict(result) if isinstance(result, Mapping) else {},
                summary=getattr(found, "summary", ""),
            )
        )
        probe = await self._safe_observe("minecraft_dig_capability", position)
        if probe is not None:
            observations.append(
                Observation(
                    tool="minecraft_dig_capability",
                    arguments=dict(position),
                    result=dict(getattr(probe, "result", None) or {}),
                    summary=getattr(probe, "summary", ""),
                )
            )
        steps = []
        for step in skill.steps:
            arguments = _substitute(dict(step.arguments), position)
            steps.append(
                TaskStep(
                    step_id=step.step_id,
                    tool=step.tool,
                    arguments=arguments,
                    risk=step.risk,
                    depends_on=step.depends_on,
                    references=collect_references(arguments),
                )
            )
        criteria = {
            str(item.params.get("item") or ""): int(item.params.get("count") or 0)
            for item in skill.success_criteria
            if str(item.kind) == "inventory_delta"
        }
        plan = TaskPlan(
            objective=str(objective or sample)[:120],
            steps=steps,
            expected_final_state=ExpectedFinalState(inventory_delta=criteria),
            observations=[
                # 注意：只有 **TaskPlan.observations** 会随任务一起持久化（运行时字段），
                # 这里是①新鲜观察 ②技能引用 —— 二者都是审计，不进 plan_hash。
                *[item.to_payload() for item in observations],
                {
                    "tool": SKILL_REFERENCE_TOOL,
                    "arguments": {},
                    "result": {
                        "skill_id": skill.skill_id,
                        "skill_version": int(skill.version),
                        "fingerprint": skill.fingerprint,
                        "status": skill.status,
                        "source": "procedural_skill",
                    },
                    "summary": f"复用技能 {skill.skill_id}（{skill.status}）",
                },
            ],
        )
        snapshot = self._tools_snapshot()
        problems = validate_plan(
            plan,
            risk_of=lambda tool: str(snapshot.risks.get(tool, "") or ""),
            is_registered=lambda tool: tool in snapshot.registered,
            schema_of=self._schema_of,
            validate_arguments=self._validate_arguments,
        )
        if problems:
            self._warn("[Skill] 技能物化未过校验：%s", problems[:2])
            return None
        return PlannedTask(objective=plan.objective, plan=plan, observations=observations)

    def _target_block(self, skill: ProceduralSkill) -> str:
        declared = str(skill.extra.get("target_block") or "")
        if declared:
            return declared
        for step in skill.steps:
            if step.tool == "minecraft_dig":
                return str(step.arguments.get("expected_block") or "")
        return ""

    async def _safe_observe(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        if self.observe is None:
            return None
        try:
            return await self.observe(tool, dict(arguments))
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------ 只读观测（§7.5 / E15）

    async def view(self, *, limit: int = 10) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        try:
            skills = await self.store.recent(max(1, int(limit)), character_id=self.character_id)
            counts = await self.store.status_counts(character_id=self.character_id)
            for skill in skills:
                rows.append(
                    {
                        "skill_id": skill.skill_id,
                        "name": skill.name[:60],
                        "status": skill.status,
                        "objective_pattern": skill.objective_pattern,
                        "server_id": skill.server_id,
                        "schema_version": int(skill.schema_version),
                        "version": int(skill.version),
                        "steps": [step.tool for step in skill.steps],
                        "max_risk": skill.max_risk,
                        "success_count": int(skill.success_count),
                        "failure_count": int(skill.failure_count),
                        "ambiguous_count": int(skill.ambiguous_count),
                        "last_learned_at": float(skill.last_learned_at),
                        "last_verified_at": float(skill.last_verified_at),
                        "last_used_at": float(skill.last_used_at),
                        "reason": skill.reason,
                    }
                )
            for item in await self.store.recent_evidence(max(1, int(limit))):
                evidence.append(
                    {
                        "evidence_id": item.evidence_id,
                        "skill_id": item.skill_id,
                        "task_id": item.task_id,
                        "outcome": item.outcome,
                        "verdict": str(item.verdict),
                        "reason_code": item.reason_code,
                        "postcondition_kind": item.postcondition_kind,
                        "at": float(item.at),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - 读视图失败也只降级
            self.degraded_reason = type(exc).__name__
        return {
            "enabled": self.enabled,
            "degraded": bool(self.degraded_reason),
            "reason": self.degraded_reason,
            "character_id": self.character_id,
            "server_id": self._server_id(),
            "counts": counts,
            "skills": rows,
            "evidence": evidence,
            "stats": {
                "learned": self.learned_count,
                "promoted": self.promoted_count,
                "rejected": self.rejected_count,
                "ambiguous": self.ambiguous_count,
                "retrievals": self.retrieval_count,
            },
        }

    # ------------------------------------------------------------ 内部

    def _warn(self, message: str, *args: Any) -> None:
        if self._log is not None:
            self._log.warning(message, *args)


def _reference_from_plan(plan: Any) -> dict[str, Any]:
    """从计划里读 ``skill_reference``（dict 条目与 Observation 对象都支持）。"""

    # AgentPlan（LIFE 路径）把技能引用放在**持久化**的 checks 里；TaskPlan 放在 observations 里
    for check in getattr(plan, "checks", None) or []:
        if not isinstance(check, Mapping):
            continue
        if str(check.get("check") or "") != "skill_reuse":
            continue
        detail = check.get("detail")
        if isinstance(detail, Mapping) and str(detail.get("skill_id") or ""):
            return dict(detail)
    observations = getattr(plan, "observations", None)
    if observations is None and isinstance(plan, Mapping):
        observations = plan.get("observations")
    for item in observations or []:
        tool = ""
        result: Any = None
        if isinstance(item, Mapping):
            tool = str(item.get("tool") or "")
            result = item.get("result")
        else:
            tool = str(getattr(item, "tool", "") or "")
            result = getattr(item, "result", None)
        if tool != SKILL_REFERENCE_TOOL:
            continue
        if isinstance(result, Mapping):
            return dict(result)
    return {}


def _coord(source: Mapping[str, Any], key: str) -> int | None:
    """从观察结果/参数里读一个整数坐标（读不出来返回 None，绝不猜 0）。"""

    value = source.get(key)
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _point_of(source: Mapping[str, Any]) -> tuple[int, int, int] | None:
    """从参数/观察里读一个整数坐标点（读不出来返回 None）。"""

    values = [_coord(source, key) for key in ("x", "y", "z")]
    if any(value is None for value in values):
        return None
    return (int(values[0]), int(values[1]), int(values[2]))  # type: ignore[arg-type]


def _target_block_of(steps: Any) -> str:
    """从模板步骤里取"要挖的方块"（只读，用于物化期的新鲜 find_blocks）。"""

    for step in steps or ():
        if str(getattr(step, "tool", "")) == "minecraft_dig":
            return str((getattr(step, "arguments", {}) or {}).get("expected_block") or "")
    return ""


def _substitute(value: Any, position: Mapping[str, Any]) -> Any:
    """把位置槽位替换成新鲜观察到的坐标（**其余参数原样保留**，例如 expected_block）。"""

    if isinstance(value, Mapping):
        if "$slot" in value:
            expanded = {
                str(key): _substitute(item, position)
                for key, item in value.items()
                if str(key) != "$slot"
            }
            if str(value.get("$slot")) == POSITION_SLOT:
                expanded.update({key: _coord(position, key) or 0 for key in ("x", "y", "z")})
            return expanded
        return {str(key): _substitute(item, position) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_substitute(item, position) for item in value]
    return value


__all__ = [
    "Assessment",
    "DEFAULT_PROMOTION_MIN_SUCCESSES",
    "SKILL_REFERENCE_TOOL",
    "SkillService",
    "ToolsSnapshot",
    "default_classifier",
    "tools_signature_of",
]
