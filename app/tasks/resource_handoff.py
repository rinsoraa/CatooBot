"""Phase 8I H1/H2：缺料诊断与经**真实观察**校验的资源获取提案（纯逻辑 + 注入式观察）。

任务书 §3/§4 的硬要求：

* 缺料信息只能来自真实库存与真实配方规划：查询失败、背包读失败、工作台状态未知都不许伪装成材料不足
  （所以先看稳定原因码，只有 insufficient_material 才谈得上资源交接）；
* 资源目标必须映射到**既有资源任务能力**（Phase 8D 已审计的木材支持矩阵），
  并**由真实世界观察确认存在
  可达目标**；没确认到目标就明确说建立不了可执行的资源计划，绝不假设附近一定有；
* 提案只是提案：不等于建立了任务，也不等于用户已批准 —— 执行仍走既有 AgentPlan 批准
  + 执行确认两道门；
* 单次交接有硬边界：数量按既有 WOOD_MAX_TARGETS 夹取并如实披露，绝不无限采集。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.tasks.intent import WOOD_MAX_TARGETS, WOOD_SPECIES, WOOD_UNSUPPORTED

#: 规划层给出的稳定原因码前缀（Phase 8H 的 craft_plan 原因码）。
REASON_MATERIAL = "insufficient_material"
REASON_NO_RECIPE = "no_recipe"
REASON_TABLE = "crafting_table_required"
REASON_INVENTORY = "inventory_unavailable"
REASON_SEARCH = "search_budget_exceeded"

#: 规划层的缺料 detail 形如 oak_log(缺 1)（由 craft_plan.py 生成，本模块只读它）。
_MISSING_PATTERN = re.compile(r"([a-z0-9_]+)\s*\(缺\s*(\d+)\)")

#: 缺失物品 -> 既有木材能力支持的方块（**只**来自 WOOD_SPECIES 这张已审计支持矩阵）。
HANDOFF_BLOCKS: dict[str, str] = {
    block.split(":", 1)[1]: block.split(":", 1)[1] for _aliases, block in WOOD_SPECIES
}

#: 明确不支持的物品 -> 稳定原因码（来自 WOOD_UNSUPPORTED；用来给出准确拒绝理由）。
UNSUPPORTED_ITEMS: dict[str, str] = {
    alias: code for aliases, code in WOOD_UNSUPPORTED for alias in (aliases[0], code)
}

ObserveFn = Callable[[str, Mapping[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class CraftGap:
    """一次合成规划失败的**结构化诊断**（原因码 + 真实缺失材料；绝不合并原因）。"""

    reason: str
    detail: str
    missing: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    @property
    def is_material_gap(self) -> bool:
        return self.reason == REASON_MATERIAL and bool(self.missing)

    def explain(self) -> str:
        if self.reason == REASON_MATERIAL and self.missing:
            parts = "、".join(f"{item} x{amount}" for item, amount in self.missing)
            return f"材料不够：还缺 {parts}（配方真实查询得到，缺什么缺多少都来自真实库存）"
        if self.reason == REASON_TABLE:
            return "这个配方要工作台，但工作台状态不支持执行（不会拿材料不足顶替）"
        if self.reason == REASON_NO_RECIPE:
            return "真实配方查询没有给出可用配方（不是材料不足）"
        if self.reason == REASON_INVENTORY:
            return "背包读不回来，所以罐头无法判断材料够不够（不会假装是缺料）"
        if self.reason == REASON_SEARCH:
            return "配方候选搜索超出既有预算，罐头没有继续试（不是材料不足）"
        return f"这次做不了：{self.detail or self.reason}"


@dataclass(frozen=True)
class ResourceHandoff:
    """一个**待用户决定**的资源获取提案（含真实观察证据与既有步骤/风险/前置条件）。"""

    item: str
    amount: int
    requested_amount: int
    block: str
    evidence: Mapping[str, Any]
    steps: tuple[str, ...]
    risks: tuple[str, ...]
    preconditions: tuple[str, ...]
    reason: str = REASON_MATERIAL

    @property
    def capped(self) -> bool:
        return self.amount < self.requested_amount

    def to_text(self) -> str:
        """任务书 §4.1 要求的提案内容（可解释、不承诺执行）。"""
        lines = [
            f"诊断：这次合成做不了 —— 缺少 {self.item} x{self.requested_amount}"
            "（来自真实库存与真实配方规划）。",
            "资源目标来自真实观察：附近确认到可处理的 "
            f"{self.block}（真实查询到 {self.evidence.get('count', 0)} 个候选，最近 "
            f"{self.evidence.get('nearest_distance', '未知')} 格）。",
        ]
        if self.capped:
            lines.append(
                f"注意：本阶段一次最多采 {WOOD_MAX_TARGETS} 个原木，所以方案只覆盖 "
                f"{self.item} x{self.amount}"
                f"（要的 {self.requested_amount} 个不够的部分得再跑一轮）。"
            )
        lines.append("预计步骤（全部走既有任务链与既有工具）：" + " → ".join(self.steps))
        lines.append("会改变世界：" + "；".join(self.risks))
        lines.append("仍未满足的前置条件：" + "；".join(self.preconditions))
        lines.append(
            "这只是一个提案：你明确同意后，罐头才会按既有批准与执行确认流程建立资源任务；"
            "在确认之前不会有任何采集动作。"
        )
        return "\n".join(lines)


def craft_gap_from_detail(detail: str) -> CraftGap:
    """把规划层的失败 detail 解析成结构化诊断（**不**改变原因码、不合并不同失败）。"""
    text = str(detail or "").strip()
    reason = text.split(":", 1)[0] if text else ""
    missing: list[tuple[str, int]] = []
    if reason == REASON_MATERIAL:
        for item, amount in _MISSING_PATTERN.findall(text):
            try:
                qty = int(amount)
            except ValueError:
                continue
            if qty > 0:
                missing.append((item, qty))
    return CraftGap(reason=reason, detail=text, missing=tuple(missing))


def handoff_block_for(item: str) -> str | None:
    """缺失物品 -> 既有资源能力支持的方块；不支持就返回 None（绝不静默映射成别的物品）。"""
    name = str(item or "").strip().lower()
    return HANDOFF_BLOCKS.get(name)


def unsupported_reason_for(item: str) -> str | None:
    name = str(item or "").strip().lower()
    return UNSUPPORTED_ITEMS.get(name)


def _result_of(invocation: Any) -> dict[str, Any]:
    payload = getattr(invocation, "result", None)
    return dict(payload) if isinstance(payload, Mapping) else {}


async def propose_handoff(
    *,
    detail: str,
    observe: ObserveFn,
    max_distance: int = 16,
    craft_goal: str = "",
) -> ResourceHandoff | None:
    """在**真实观察确认存在目标**的前提下，给出一个有界的资源获取提案；否则返回 None。"""
    gap = craft_gap_from_detail(detail)
    if not gap.is_material_gap:
        return None
    item, requested = gap.missing[0]
    block = handoff_block_for(item)
    if block is None:
        return None
    invocation = await observe(
        "minecraft_find_blocks",
        {"block_names": [block], "max_distance": max_distance, "max_results": 8},
    )
    payload = _result_of(invocation)
    if payload.get("ok") is False:
        return None
    matches: Sequence[Any] = payload.get("matches") or []
    clean = [row for row in matches if isinstance(row, Mapping)]
    if not clean:
        return None
    nearest = clean[0]
    raw_distance = nearest.get("distance")
    distance = raw_distance if isinstance(raw_distance, Mapping) else {}
    evidence = {
        "count": len(clean),
        "nearest_position": dict(nearest.get("position") or {}),
        "nearest_distance": distance.get("goal_near"),
        "craft_goal": craft_goal,
    }
    amount = max(1, min(int(requested), WOOD_MAX_TARGETS))
    return ResourceHandoff(
        item=item,
        amount=amount,
        requested_amount=int(requested),
        block=block,
        evidence=evidence,
        steps=(
            "真实观察附近目标方块",
            "需要时换上手头的合适工具",
            "走到目标（既有导航）",
            "挖掘目标方块（既有 minecraft_dig）",
            "捡起掉落物",
            "复核背包净增",
        ),
        risks=(
            "会破坏方块并改变世界（挖掘）；风险和授权由既有风险映射与确认门决定，本提案不绕过它们",
        ),
        preconditions=(
            "用户明确同意这个资源方案",
            "既有 AgentPlan 批准",
            "既有执行确认",
            "执行时目标仍可达且工具条件满足",
        ),
    )
