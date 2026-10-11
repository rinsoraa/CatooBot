"""Phase 7F.2：有界探索结果的结构化投影（**只读**，不碰权限、不碰执行）。

本模块只回答一个问题：一次探索任务的**真实终态**是什么，以及它带来了哪些
**有证据支持**的新事实。它绝不产生动作、绝不改 Policy，也不建立第二套记忆系统
—— 输出直接复用既有 :mod:`app.memory.minecraft` 模型。

证据分级（§4.1）：

* ``ARRIVED_VERIFIED`` / ``ARRIVED_NO_NEW_FACT``：运行时的 SAFE 后置校验
  （``record.verification.position_within.ok``，由 TaskRuntime 重新读世界坐标得出）
  为真 —— 不是动作自述、不是计划批准。
* ``NEW_FACTS_VERIFIED``：在上述到达基础上，**执行期**的 SAFE ``minecraft_world``
  观察给出了带坐标的新兴趣点，且我们把它当成新事实写回记忆。
* ``BLOCKED``：运行时后置校验明确失败（目标不可达 / 前置不满足）。绝不写正向事实。
* ``CANCELLED_OR_INTERRUPTED``：任务被取消 / 中断 / 未完成。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.memory.minecraft.model import (
    FactSource,
    Freshness,
    MinecraftMemoryFact,
    MinecraftMemoryKind,
    cluster_position,
    number_or,
)

#: 有界探索一次最多落几条新兴趣点事实（bounded，避免整片世界被写进记忆）。
MAX_NEW_FACTS = 5

_POSITION_KEYS = ("x", "y", "z")


class ExplorationOutcome(str, Enum):  # noqa: UP042 - 面向 JSON 的枚举
    ARRIVED_VERIFIED = "ARRIVED_VERIFIED"
    ARRIVED_NO_NEW_FACT = "ARRIVED_NO_NEW_FACT"
    NEW_FACTS_VERIFIED = "NEW_FACTS_VERIFIED"
    BLOCKED = "BLOCKED"
    CANCELLED_OR_INTERRUPTED = "CANCELLED_OR_INTERRUPTED"


@dataclass(frozen=True)
class ExplorationResult:
    """一次探索任务的结构化结果（只读投影；不携带任何执行句柄）。"""

    task_id: str
    title: str
    outcome: ExplorationOutcome
    objective: str = ""
    arrived: bool = False
    arrival: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    new_facts: tuple[MinecraftMemoryFact, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "outcome": self.outcome.value,
            "objective": self.objective,
            "arrived": self.arrived,
            "arrival": dict(self.arrival),
            "message": self.message,
            "new_facts": len(self.new_facts),
        }


def _state_of(record: Any) -> str:
    raw = getattr(getattr(record, "state", None), "value", getattr(record, "state", ""))
    return str(raw or "").upper()


def _plan_of(record: Any) -> Any:
    return getattr(record, "plan", None)


def _expected_position(record: Any) -> dict[str, Any]:
    expected = getattr(_plan_of(record), "expected_final_state", None)
    target = getattr(expected, "position_within", None)
    return dict(target) if isinstance(target, Mapping) else {}


def is_exploration_task(record: Any) -> bool:
    """记录是不是一次**有界探索**任务。

    结构判据只用 7F.1 的到达后置条件（``expected_final_state.position_within``）——
    这正是"到达由运行时独立复核"的契约。仅仅"有 move_to 步骤"（例如用户说"过来"）
    不算探索：那种任务没有到达复核，拿它当探索会把动作自述当完成（§4.1）。
    """

    return bool(_expected_position(record))


def _finite_axis(value: Any) -> float | None:
    """单个坐标轴：拒绝 ``bool``、不可转换值、转换溢出与非有限实数
    （``NaN`` / ``±Infinity``）。

    ``True`` / ``False`` 是 Python 的 ``int`` 子类，绝不能被当成 ``1`` / ``0``；
    ``float("nan")`` / ``float("inf")`` 转换成功但**不是**有效世界坐标，一律拒绝。
    超出 ``float`` 值域的巨大整数（``float(10 ** 400)``）会抛 ``OverflowError``，
    同样**不是**有效世界坐标：这里一并 fail-closed 返回 ``None``，绝不向上传播异常
    （Phase 7F.2.2.1 数值转换溢出加固）。
    """

    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _clean_position(value: Any) -> dict[str, float] | None:
    """把一个位置映射规范成 ``{x, y, z}`` **有限**浮点；任何无效一维 → ``None``。

    缺字段、类型异常、不可解析、``bool`` 或非有限值（``NaN`` / ``±Infinity``）一律
    fail-closed。合法负坐标与小数坐标（含有限数字字符串）照常通过
    （Phase 7F.2.2 坐标域加固）。
    """

    if not isinstance(value, Mapping):
        return None
    cleaned: dict[str, float] = {}
    for key in _POSITION_KEYS:
        if key not in value:
            return None
        number = _finite_axis(value[key])
        if number is None:
            return None
        cleaned[key] = number
    return cleaned


def verified_arrival(record: Any) -> dict[str, float] | None:
    """**唯一**的已验证到达判定（fail-closed）；通过则返回**实际到达坐标** x/y/z。

    只有同时满足以下全部条件才算"已由运行时独立复核的到达"：

    * ``record.state`` 规范化后**严格**等于 ``SUCCEEDED``；
    * ``record.verification`` 是可读取的映射，且 ``checked is True``、``ok is True``；
    * ``verification.position_within`` 是含**实际到达坐标** x/y/z 的有效映射
      —— 绝不用计划里的目标坐标冒充实际到达。

    缺失 / 类型异常 / 字段冲突 / 状态与证据互相矛盾 → ``None``。所有会把探索结果
    提升为正向记忆（到达经历 / 新事实）的路径都必须经过这里，**不允许任何调用点
    自行做更弱的判定**（Phase 7F.2.1 终态不变量）。
    """

    if _state_of(record) != "SUCCEEDED":
        return None
    verification = getattr(record, "verification", None)
    if not isinstance(verification, Mapping):
        return None
    if verification.get("checked") is not True or verification.get("ok") is not True:
        return None
    return _clean_position(verification.get("position_within"))


def _arrived(record: Any) -> bool:
    """运行时是否**独立复核**了到达（严格判定见 :func:`verified_arrival`）。"""

    return verified_arrival(record) is not None


def _arrival_payload(record: Any) -> dict[str, Any]:
    """从运行时验证结果里取出**实际到达坐标**（不是计划里的目标；未验证 → 空）。"""

    return dict(verified_arrival(record) or {})


def _world_observation(record: Any) -> dict[str, Any] | None:
    """取**执行期**最后一次成功的 SAFE ``minecraft_world`` 观察（带语义模型）。

    只认运行时真实调用过的那一步（步骤状态 ``SUCCEEDED``）；伪造的、缺失的、
    非本任务的观察一律返回 ``None`` —— 没有证据就不产生新事实。
    """

    for step in reversed(list(getattr(record, "steps", None) or [])):
        if str(getattr(step, "tool", "") or "") != "minecraft_world":
            continue
        state = str(getattr(getattr(step, "state", None), "value", getattr(step, "state", "")))
        if state.upper() != "SUCCEEDED":
            continue
        result = getattr(step, "result", None)
        view = dict(result) if isinstance(result, Mapping) else {}
        if view.get("online") is False or view.get("available") is False:
            return None
        has_semantic = isinstance(view.get("semantic"), Mapping)
        has_poi = isinstance(view.get("points_of_interest"), list)
        if has_semantic or has_poi:
            return view
    return None


def _poi_entries(view: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    semantic = view.get("semantic")
    if isinstance(semantic, Mapping) and isinstance(semantic.get("points_of_interest"), list):
        return [item for item in semantic["points_of_interest"] if isinstance(item, Mapping)]
    if isinstance(view.get("points_of_interest"), list):
        return [item for item in view["points_of_interest"] if isinstance(item, Mapping)]
    return []


def exploration_facts(
    record: Any,
    *,
    server_id: str,
    limit: int = MAX_NEW_FACTS,
) -> tuple[MinecraftMemoryFact, ...]:
    """从**执行期** SAFE 观察抽取带坐标的新兴趣点事实（有界、可证据追溯）。

    只从 ``minecraft_world`` 的 ``points_of_interest`` 里取有真实坐标的条目；
    没坐标 / 离线 / 非执行期观察 → 不产生任何事实（§4.2）。
    """

    view = _world_observation(record)
    if view is None or not server_id or not _arrived(record):
        # 没有独立到达复核 → 观察不能晋升为"在探索中确认的新事实"（fail-closed）。
        return ()
    task_id = str(getattr(record, "task_id", "") or "")
    title = str(getattr(record, "objective", "") or "")[:80]
    out: list[MinecraftMemoryFact] = []
    seen: set[str] = set()
    for entry in _poi_entries(view):
        position = _clean_position(entry.get("pos") or entry.get("position"))
        if position is None:
            continue
        name = str(entry.get("type") or "").strip()
        if not name:
            continue
        cell = cluster_position(position)
        subject = f"poi:{name}@{cell}" if cell else f"poi:{name}"
        if subject in seen:
            continue
        seen.add(subject)
        out.append(
            MinecraftMemoryFact(
                kind=MinecraftMemoryKind.LOCATION,
                server_id=str(server_id),
                subject=subject,
                content=f"在探索“{title}”时，{_place(position)} 发现了 {name}。",
                source=FactSource.OBSERVED,
                position=position,
                task_id=task_id,
                outcome=ExplorationOutcome.NEW_FACTS_VERIFIED.value,
                title=title,
                extra={"exploration_fact": True, "actionable": True, "poi": True},
            )
        )
        if len(out) >= max(1, int(limit)):
            break
    return tuple(out)


def exploration_outcome(record: Any, *, new_facts: Sequence[Any] = ()) -> ExplorationResult:
    """把持久化 TaskRecord 投影成最小的探索结果（纯函数，不写任何东西）。"""

    task_id = str(getattr(record, "task_id", "") or "")
    objective = " ".join(str(getattr(record, "objective", "") or "").split())[:200]
    state = _state_of(record)
    # Phase 7F.2.1：到达判定统一走 verified_arrival —— 只有 SUCCEEDED 且验证完整
    # 才算是真实到达；状态与证据矛盾时 arrived=False（fail-closed）。
    arrived = verified_arrival(record) is not None
    arrival = _arrival_payload(record)
    message = " ".join(str(getattr(record, "message", "") or "").split())[:160]
    facts = tuple(new_facts)

    if state == "SUCCEEDED" and arrived:
        outcome = (
            ExplorationOutcome.NEW_FACTS_VERIFIED
            if facts
            else ExplorationOutcome.ARRIVED_NO_NEW_FACT
        )
    elif state == "SUCCEEDED":
        # 没有独立到达证据的成功**不算**真实到达（绝不把动作自述当完成）。
        # 有界探索在运行时里 SUCCEEDED ⟹ 到达复核已过；到达字段缺失只可能是坏数据，
        # 按"未确认完成"处理。
        outcome = ExplorationOutcome.CANCELLED_OR_INTERRUPTED
    elif state == "FAILED":
        outcome = ExplorationOutcome.BLOCKED
    elif state in {"CANCELLED", "EXPIRED"}:
        outcome = ExplorationOutcome.CANCELLED_OR_INTERRUPTED
    else:
        outcome = ExplorationOutcome.CANCELLED_OR_INTERRUPTED

    return ExplorationResult(
        task_id=task_id,
        title=objective,
        outcome=outcome,
        objective=objective,
        arrived=arrived,
        arrival=arrival,
        message=message,
        new_facts=facts,
    )


def arrival_fact(record: Any, *, server_id: str) -> MinecraftMemoryFact | None:
    """到达事实（事件，一条，去重键带终态）：给生活系统一个可追溯的“去过”经历。

    这是**经历**不是新位置知识；带坐标但只作事件，``actionable=False`` —— 普通到达
    不应反复生成“继续探索”意图（§4.3）。
    """

    if not server_id:
        return None
    # Phase 7F.2.1：到达经历必须过**共享的严格判定**（终态 SUCCEEDED + 完整验证 +
    # 实际到达坐标），不允许比 verified_arrival 更弱的重复检查。
    position = verified_arrival(record)
    if position is None:
        return None
    task_id = str(getattr(record, "task_id", "") or "")
    title = " ".join(str(getattr(record, "objective", "") or "").split())[:80]
    state = _state_of(record)
    return MinecraftMemoryFact(
        kind=MinecraftMemoryKind.EVENT,
        server_id=str(server_id),
        subject=f"explore_arrived:{task_id or title}",
        content=f"罐头探索“{title}”时真的走到了 {_place(position)} 附近。",
        source=FactSource.OBSERVED,
        position=position,
        task_id=task_id,
        outcome=ExplorationOutcome.ARRIVED_VERIFIED.value,
        title=title,
        fresh=Freshness.ACTIVE,
        extra={
            "exploration_fact": True,
            "actionable": False,
            "arrival": True,
            "terminal_state": state,
        },
    )


def _place(position: Mapping[str, Any]) -> str:
    x = int(number_or(position.get("x")))
    y = int(number_or(position.get("y")))
    z = int(number_or(position.get("z")))
    return f"({x},{y},{z}) 附近"


__all__ = [
    "MAX_NEW_FACTS",
    "ExplorationOutcome",
    "ExplorationResult",
    "arrival_fact",
    "exploration_facts",
    "exploration_outcome",
    "is_exploration_task",
    "verified_arrival",
]
