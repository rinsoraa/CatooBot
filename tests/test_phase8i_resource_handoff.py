"""Phase 8I H1/H2：缺料诊断与资源交接提案（观察返回值用真实 runtime 的形状）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.tasks.intent import WOOD_MAX_TARGETS
from app.tasks.resource_handoff import (
    REASON_MATERIAL,
    craft_gap_from_detail,
    handoff_block_for,
    propose_handoff,
    unsupported_reason_for,
)


def _matches(count: int = 2) -> dict[str, Any]:
    return {
        "ok": True,
        "matches": [
            {
                "block": {"name": "oak_log"},
                "position": {"x": -264 + i, "y": 69, "z": -140},
                "distance": {"goal_near": 2.83 + i, "raw": 2.01 + i},
            }
            for i in range(count)
        ],
    }


class FakeObserve:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, dict(arguments)))
        return type("R", (), {"result": self.payload, "summary": tool})()


def test_material_gap_is_parsed_into_items_and_amounts() -> None:
    gap = craft_gap_from_detail("insufficient_material:oak_log(缺 3)")
    assert gap.reason == REASON_MATERIAL
    assert gap.missing == (("oak_log", 3),)
    assert gap.is_material_gap is True
    assert "oak_log" in gap.explain()


@pytest.mark.parametrize(
    "detail",
    [
        "crafting_table_required:NOT_FOUND:no_workbench:附近没有工作台",
        "inventory_unavailable:背包读不回来",
        "no_recipe:wooden_pickaxe",
        "search_budget_exceeded:300>256",
    ],
)
async def test_non_material_failures_are_never_treated_as_missing_material(detail: str) -> None:
    gap = craft_gap_from_detail(detail)
    assert gap.is_material_gap is False
    observe = FakeObserve(_matches())
    # 关键：这些失败**不会**触发资源交接，也不会去查方块。
    assert await propose_handoff(detail=detail, observe=observe) is None
    assert observe.calls == []


async def test_proposal_requires_a_real_observed_target() -> None:
    for payload in ({"ok": True, "matches": []}, {"ok": False, "matches": []}, None):
        observe = FakeObserve(payload)
        proposal = await propose_handoff(
            detail="insufficient_material:oak_log(缺 2)", observe=observe
        )
        assert proposal is None, "没有真实确认到目标就不许提案"
        assert observe.calls and observe.calls[0][0] == "minecraft_find_blocks"


async def test_proposal_carries_real_evidence_and_respects_the_hard_bound() -> None:
    observe = FakeObserve(_matches(count=3))
    proposal = await propose_handoff(
        detail="insufficient_material:oak_log(缺 7)", observe=observe, craft_goal="做一把木镐"
    )
    assert proposal is not None
    assert proposal.block == "oak_log"
    assert proposal.requested_amount == 7
    assert proposal.amount == WOOD_MAX_TARGETS, "单次交接必须按既有硬上限夹取"
    assert proposal.capped is True
    assert proposal.evidence["count"] == 3
    assert proposal.evidence["nearest_distance"] == 2.83
    assert proposal.evidence["craft_goal"] == "做一把木镐"
    tool, args = observe.calls[0]
    assert tool == "minecraft_find_blocks"
    assert args["block_names"] == ["oak_log"]


async def test_unsupported_items_are_refused_with_a_stable_reason() -> None:
    assert handoff_block_for("planks") is None
    assert handoff_block_for("bamboo") is None
    assert handoff_block_for("oak_log") == "oak_log"
    assert unsupported_reason_for("planks") == "planks"
    observe = FakeObserve(_matches())
    assert (
        await propose_handoff(detail="insufficient_material:planks(缺 3)", observe=observe) is None
    )
    assert observe.calls == [], "不支持的物品连查都不查（不把不支持能力包装成资源能力）"


async def test_proposal_text_lists_everything_the_task_book_requires() -> None:
    observe = FakeObserve(_matches(count=2))
    proposal = await propose_handoff(
        detail="insufficient_material:birch_log(缺 5)", observe=observe, craft_goal="做木棍"
    )
    assert proposal is not None
    text = proposal.to_text()
    assert "birch_log x5" in text
    assert "真实观察" in text
    assert "minecraft_dig" in text
    assert "会改变世界" in text
    assert "前置条件" in text
    assert "提案" in text and "确认" in text
