"""Phase 8E Follow-up 4：回合决策上下文（真实来源 + 诚实时效/缺失状态）。

设计原则（任务书 §3）：

* 每个来源都必须能区分：真实存在 / 缺失 / 读取失败 / 已过期 / 服务器不匹配 / 服务不可用；
* **绝不**把「未取得数据」伪装成确定的空结果（背包读取失败 != 背包为空）；
* 只给模型只读、最小、可追溯的事实；不传完整 UUID、QQ 号、凭据、原始记忆全文；
* 这一层**只读**：不产生任何世界动作（只用缓存观察 + 既有只读查询结果）。

本模块只做「读取 + 规范化 + 标注状态」，不新建第二套记忆/身份/感知存储。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.tasks.turn_decision import AUDITED_CAPABILITIES

#: 本阶段明确**未开放为自由能力调用**的世界修改能力（只作为事实告知模型，绝不可直接执行）。
#:
#: Phase 8F：``minecraft_craft`` **不在这张表里了** —— 基础合成已作为**受管任务**开放
#: （计划 → 授权/确认 → 既有执行链 → 真实库存复核）。但它**没有**进入
#: ``READ_ONLY_CAPABILITIES`` / ``ACTION_CAPABILITIES`` 自由调用白名单（§5.3：不得靠把工具
#: 加进通用白名单来打通），所以模型依然不能把它当成原子能力直接点名执行。
UNSUPPORTED_CAPABILITIES: tuple[str, ...] = (
    "minecraft_dig",
    "minecraft_place_block",
    "minecraft_attack",
    "minecraft_container",
    "minecraft_inventory_move",
)

#: 世界观察新鲜度上限（秒）。超过即标 STALE，让模型知道「这不是当前事实」。
WORLD_FRESH_SECONDS = 15.0

#: 渲染预算：整块上下文不得超过这个字符数（任务书 §10 有界预算）。
RENDER_BUDGET = 900


class ContextState(StrEnum):
    """一个上下文来源的状态（§3.2）。"""

    OK = "ok"
    MISSING = "missing"
    FAILED = "failed"
    STALE = "stale"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ContextFact:
    """单个来源的只读事实 + 状态 + 时效。"""

    source: str
    state: str
    summary: str = ""
    reason: str = ""
    observed_at: float | None = None

    def render(self, *, now: float) -> str:
        head = f"{self.source}: {self.state}"
        detail: list[str] = []
        if self.summary:
            detail.append(self.summary)
        if self.reason:
            detail.append(f"reason={self.reason}")
        if self.observed_at is not None:
            detail.append(f"age={max(0.0, now - self.observed_at):.1f}s")
        joined = ", ".join(detail)
        return head + (f" ({joined})" if detail else "")

    def log_fields(self) -> dict[str, Any]:
        """结构化日志字段（只含状态与稳定原因码，不含内容）。"""
        return {
            "source": self.source,
            "state": self.state,
            "reason": self.reason,
            "chars": len(self.summary),
            "has_time": self.observed_at is not None,
        }


@dataclass(frozen=True)
class TurnDecisionContext:
    """一次决策的完整上下文（所有来源都带状态）。"""

    recent: tuple[str, ...] = ()
    facts: tuple[ContextFact, ...] = ()
    memory_block: str = ""
    built_at: float = field(default_factory=time.time)

    def fact(self, source: str) -> ContextFact | None:
        for item in self.facts:
            if item.source == source:
                return item
        return None

    def render(self, *, now: float | None = None) -> str:
        moment = time.time() if now is None else now
        lines = [item.render(now=moment) for item in self.facts]
        if self.memory_block:
            lines.append("memory_facts: " + self.memory_block)
        head = "[decision context]"
        body = NL_JOIN.join(lines)
        text = head + NL_JOIN + body if body else head
        return text[:RENDER_BUDGET]

    def log_fields(self) -> dict[str, Any]:
        return {
            "recent": len(self.recent),
            "facts": [item.log_fields() for item in self.facts],
            "memory_chars": len(self.memory_block),
        }


NL_JOIN = "\n"


def _fact(source: str, state: ContextState, **kw: Any) -> ContextFact:
    return ContextFact(source=source, state=state.value, **kw)


def _time_of(payload: Any) -> float | None:
    """从视图里尽量取出**真实**时间戳；取不到就返回 None（不编造）。"""
    if not isinstance(payload, dict):
        return None
    for key in ("observed_at", "updated_at", "timestamp", "at", "fetched_at"):
        raw = payload.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw)
    nested = payload.get("meta")
    if isinstance(nested, dict):
        return _time_of(nested)
    return None


def _num(value: Any) -> float | None:
    """只要真正是数字才返回 float；bool/字符串/缺失一律 None（不猜、不强转）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _position_summary(payload: dict[str, Any]) -> str:
    semantic = payload.get("semantic") if isinstance(payload.get("semantic"), dict) else payload
    position = semantic.get("position") if isinstance(semantic, dict) else None
    if not isinstance(position, dict):
        position = payload.get("position") if isinstance(payload.get("position"), dict) else None
    dimension = ""
    if isinstance(semantic, dict):
        dimension = str(semantic.get("dimension") or "")
    if not isinstance(position, dict):
        return f"dimension={dimension}" if dimension else ""
    x, y, z = _num(position.get("x")), _num(position.get("y")), _num(position.get("z"))
    if x is None or y is None or z is None:
        return f"dimension={dimension}" if dimension else ""
    where = f"({round(x, 1)}, {round(y, 1)}, {round(z, 1)})"
    return f"{dimension} @ {where}" if dimension else f"@ {where}"


async def _task_fact(handler: Any, session_id: str) -> ContextFact:
    """活动任务上下文：来自既有 TaskRuntime（不新建第二套状态机）。"""
    reader = getattr(handler, "current_task", None) if handler is not None else None
    if not callable(reader):
        return _fact("active_task", ContextState.MISSING, reason="task_entry_absent")
    try:
        record = await reader(str(session_id))
    except Exception as exc:  # noqa: BLE001
        return _fact("active_task", ContextState.FAILED, reason=type(exc).__name__)
    if record is None:
        return _fact("active_task", ContextState.OK, summary="no_active_task")
    raw_state = getattr(record, "state", None)
    state = getattr(raw_state, "value", None) or str(raw_state or "")
    steps = getattr(getattr(record, "plan", None), "steps", None) or []
    index = int(getattr(record, "current_step", 0) or 0)
    pending = str(getattr(record, "pending_action_id", "") or "")
    return _fact(
        "active_task",
        ContextState.OK,
        summary=f"state={state} step={index + 1}/{len(steps)} awaiting_action={bool(pending)}",
    )


def _capability_fact(character: Any) -> ContextFact:
    """能力目录：**由可信代码**按注册/配置/白名单构造（模型不得自行决定拥有哪些工具）。"""
    tools = getattr(character, "tools", None) if character is not None else None
    if tools is None or not getattr(tools, "enabled", False):
        return _fact("capabilities", ContextState.UNAVAILABLE, reason="tool_runtime_disabled")
    registry = getattr(tools, "registry", None)
    if registry is None:
        return _fact("capabilities", ContextState.FAILED, reason="no_registry")
    available: list[str] = []
    disabled: list[str] = []
    for name in sorted(AUDITED_CAPABILITIES):
        try:
            tool = registry.maybe_get(name)
        except Exception:  # noqa: BLE001
            tool = None
        if tool is None:
            continue
        if getattr(tool, "enabled", True):
            available.append(name)
        else:
            disabled.append(name)
    inside = ",".join(available) or "none"
    outside = ",".join(disabled) or "none"
    unsupported = ",".join(sorted(UNSUPPORTED_CAPABILITIES))
    return _fact(
        "capabilities",
        ContextState.OK,
        summary=(
            f"available=[{inside}] disabled=[{outside}] unsupported=[{unsupported}] "
            # Phase 8G：合成**已实现**（2×2 与 3×3 工作台），但工作台必须**已经存在且可达** ——
            # 这是能力事实，不是世界断言（有没有工作台由真实观察在计划期判定）。
            "crafting=task(2x2_and_workbench_3x3,table_must_exist_and_be_reachable)"
        ),
    )


async def build_turn_context(
    *,
    service: Any = None,
    character: Any = None,
    handler: Any = None,
    session_id: str = "",
    username: str = "",
    message: str = "",
    recent: tuple[str, ...] = (),
    now: float | None = None,
) -> TurnDecisionContext:
    """组装一次决策的上下文（**永不抛**；每个来源都带状态）。"""
    moment = time.time() if now is None else now
    facts: list[ContextFact] = [
        _fact("conversation", ContextState.OK, summary=f"recent={len(recent)} messages"),
    ]
    memory_block = ""
    try:
        memory_fact, memory_block = await _memory_fact(character, message)
    except Exception as exc:  # noqa: BLE001
        memory_fact = _fact("memory", ContextState.FAILED, reason=type(exc).__name__)
    facts.append(memory_fact)
    try:
        facts.append(await _identity_fact(character, session_id, username))
    except Exception as exc:  # noqa: BLE001
        facts.append(_fact("identity", ContextState.FAILED, reason=type(exc).__name__))
    try:
        facts.append(_world_fact(service, now=moment))
    except Exception as exc:  # noqa: BLE001
        facts.append(_fact("world", ContextState.FAILED, reason=type(exc).__name__))
    try:
        facts.append(_inventory_fact(service))
    except Exception as exc:  # noqa: BLE001
        facts.append(_fact("inventory", ContextState.FAILED, reason=type(exc).__name__))
    try:
        facts.append(await _task_fact(handler, session_id))
    except Exception as exc:  # noqa: BLE001
        facts.append(_fact("active_task", ContextState.FAILED, reason=type(exc).__name__))
    try:
        facts.append(_capability_fact(character))
    except Exception as exc:  # noqa: BLE001
        facts.append(_fact("capabilities", ContextState.FAILED, reason=type(exc).__name__))
    return TurnDecisionContext(
        recent=tuple(recent), facts=tuple(facts), memory_block=memory_block, built_at=moment
    )


async def _memory_fact(character: Any, message: str) -> tuple[ContextFact, str]:
    bridge = getattr(character, "minecraft_memory", None) if character is not None else None
    if bridge is None:
        return _fact("memory", ContextState.MISSING, reason="memory_bridge_absent"), ""
    try:
        block = str(await bridge.context_block(text=str(message or "")) or "")
    except Exception as exc:  # noqa: BLE001 - 读不到记忆不等于没有记忆
        return _fact("memory", ContextState.FAILED, reason=type(exc).__name__), ""
    if block.strip():
        return _fact("memory", ContextState.OK, summary=f"{len(block)} chars"), block
    return _fact("memory", ContextState.MISSING, reason="no_relevant_memory"), ""


async def _identity_fact(character: Any, session_id: str, username: str) -> ContextFact:
    resolver = getattr(character, "identity_resolver", None) if character is not None else None
    if resolver is None:
        return _fact("identity", ContextState.MISSING, reason="identity_layer_absent")
    try:
        context = await resolver.resolve(
            session_id=str(session_id), user_id=str(username), user_name=str(username)
        )
    except Exception as exc:  # noqa: BLE001
        return _fact("identity", ContextState.FAILED, reason=type(exc).__name__)
    resolution = getattr(context, "resolution", "")
    value = str(getattr(resolution, "value", resolution) or "unknown")
    channel = str(getattr(getattr(context, "channel", ""), "value", "") or "")
    key_present = "present" if str(getattr(context, "relationship_key", "") or "") else "absent"
    state = ContextState.OK if value.upper() == "VERIFIED" else ContextState.MISSING
    reason = "" if value.upper() == "VERIFIED" else "not_verified"
    return _fact(
        "identity",
        state,
        summary=f"resolution={value} channel={channel or 'unknown'} relationship_key={key_present}",
        reason=reason,
    )


def _world_fact(service: Any, *, now: float) -> ContextFact:
    if service is None:
        return _fact("world", ContextState.FAILED, reason="no_minecraft_service")
    try:
        view = service.world_view()
    except Exception as exc:  # noqa: BLE001
        return _fact("world", ContextState.FAILED, reason=type(exc).__name__)
    if not isinstance(view, dict):
        return _fact("world", ContextState.FAILED, reason="bad_world_view")
    if not view.get("available"):
        return _fact(
            "world",
            ContextState.UNAVAILABLE,
            reason=str(view.get("reason") or "perception_unavailable"),
        )
    observed = _time_of(view)
    summary = _position_summary(view)
    online = bool(view.get("online"))
    summary = (summary + f", online={online}").lstrip(", ")
    stale = observed is not None and (now - observed) > WORLD_FRESH_SECONDS
    return _fact(
        "world",
        ContextState.STALE if stale else ContextState.OK,
        summary=summary,
        reason="observation_expired" if stale else "",
        observed_at=observed,
    )


def _inventory_fact(service: Any) -> ContextFact:
    """背包：**只用真实读取结果**；读不到就是读不到，绝不说成「背包为空」。"""
    if service is None:
        return _fact("inventory", ContextState.FAILED, reason="no_minecraft_service")
    try:
        view = service.world_view()
    except Exception as exc:  # noqa: BLE001
        return _fact("inventory", ContextState.FAILED, reason=type(exc).__name__)
    if not isinstance(view, dict):
        return _fact("inventory", ContextState.FAILED, reason="bad_world_view")
    semantic = view.get("semantic") if isinstance(view.get("semantic"), dict) else {}
    items = view.get("inventory") if isinstance(view.get("inventory"), dict) else None
    if items is None and isinstance(semantic, dict):
        items = semantic.get("inventory") if isinstance(semantic.get("inventory"), dict) else None
    if not isinstance(items, dict):
        # 感知层当前**不提供**背包快照：如实标注缺失来源，不得伪造空背包。
        return _fact("inventory", ContextState.MISSING, reason="no_inventory_source")
    entries = items.get("items") if isinstance(items.get("items"), list) else None
    if entries is None:
        return _fact("inventory", ContextState.MISSING, reason="no_inventory_source")
    held = items.get("held_item")
    held_name = held.get("name") if isinstance(held, dict) else (held or "")
    return _fact(
        "inventory",
        ContextState.OK,
        summary=f"slots={len(entries)} held={held_name or 'empty'}",
    )
