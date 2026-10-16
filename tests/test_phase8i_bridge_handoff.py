"""Phase 8I §7.1/§7.2：缺料诊断、资源提案与接受路径的桥接层行为（轻量构造）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations.minecraft.chat_bridge import MinecraftChatBridge
from app.tasks.intent import parse_wood_request
from app.tasks.resource_handoff import REASON_MATERIAL

WOOD_OK = {
    "ok": True,
    "matches": [
        {
            "block": {"name": "oak_log"},
            "position": {"x": -265, "y": 69, "z": -140},
            "distance": {"goal_near": 2.83, "raw": 2.01},
        }
    ],
}


class FakePlans:
    """假计划服务：既提供观察（H2），也提供既有计划入口（H3 复用）。"""

    def __init__(self, payload: Any = WOOD_OK, action: str = "created") -> None:
        self.observe = self._observe
        self.payload = payload
        self.action = action
        self.calls: list[dict[str, Any]] = []

    async def _observe(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append({"tool": tool, "arguments": dict(arguments)})
        return SimpleNamespace(result=self.payload, summary=tool)

    async def plan_goal_from_user(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.action == "created":
            return {
                "action": "created",
                "reply": "",
                "record": SimpleNamespace(task_id="task_fake", plan=SimpleNamespace(steps=[])),
                "plan": SimpleNamespace(checks=()),
            }
        return {"action": "blocked", "outcome": "blocked_by_precondition", "reply": "做不了"}


def _bridge(plans: Any = None) -> MinecraftChatBridge:
    bridge = object.__new__(MinecraftChatBridge)
    bridge.bot = SimpleNamespace(agent_plans=plans)
    bridge.service = None
    bridge._pending_handoffs = {}
    bridge._pending_craft_goals = {}
    bridge._route = lambda **kwargs: None  # type: ignore[method-assign]
    return bridge


def test_gap_detail_reads_the_stable_reason_code() -> None:
    out = {"checks": ({"name": "plan", "detail": "insufficient_material:oak_log(缺 2)"},)}
    assert MinecraftChatBridge._gap_detail_of(out) == "insufficient_material:oak_log(缺 2)"
    assert (
        MinecraftChatBridge._gap_detail_of({"checks": ({"detail": {"reason": "no_recipe"}},)})
        == "no_recipe"
    )
    assert MinecraftChatBridge._gap_detail_of({}) == ""


async def test_craft_handoff_proposes_only_with_a_real_observed_target() -> None:
    plans = FakePlans()
    bridge = _bridge(plans)
    extra = await bridge._craft_handoff(
        "IdCraft", "s", "做木镐", {"checks": ({"detail": "insufficient_material:oak_log(缺 2)"},)}
    )
    assert "oak_log" in extra and "真实观察" in extra and "提案" in extra
    assert plans.calls[0]["tool"] == "minecraft_find_blocks"
    assert plans.calls[0]["arguments"]["block_names"] == ["oak_log"]
    assert ("IdCraft", "s") in bridge._pending_handoffs


async def test_craft_handoff_is_silent_without_an_observed_target() -> None:
    plans = FakePlans(payload={"ok": True, "matches": []})
    bridge = _bridge(plans)
    extra = await bridge._craft_handoff(
        "IdCraft", "s", "做木镐", {"checks": ({"detail": "insufficient_material:oak_log(缺 2)"},)}
    )
    assert "建立不了可执行的资源计划" in extra
    assert bridge._pending_handoffs == {}, "没有确认真实目标就不许留下待决交接"


async def test_craft_handoff_refuses_unsupported_items_honestly() -> None:
    plans = FakePlans()
    bridge = _bridge(plans)
    extra = await bridge._craft_handoff(
        "IdCraft", "s", "做木镐", {"checks": ({"detail": "insufficient_material:planks(缺 3)"},)}
    )
    assert "不在罐头现有的资源能力里" in extra and "planks" in extra
    assert plans.calls == [], "不支持的物品连观察都不做"
    assert bridge._pending_handoffs == {}


@pytest.mark.parametrize(
    "detail",
    [
        "no_recipe:wooden_pickaxe",
        "inventory_unavailable:背包读不回来",
        "search_budget_exceeded:300>256",
        "crafting_table_required:NOT_FOUND:x:y",
    ],
)
async def test_non_material_failures_never_propose_resources(detail: str) -> None:
    plans = FakePlans()
    bridge = _bridge(plans)
    extra = await bridge._craft_handoff("IdCraft", "s", "做木镐", {"checks": ({"detail": detail},)})
    assert extra == ""
    assert plans.calls == []
    assert bridge._pending_handoffs == {}


def test_canonical_objective_is_parseable_by_the_existing_wood_entry() -> None:
    bridge = _bridge()
    objective = bridge._canonical_wood_objective("oak_log", 2)
    wood = parse_wood_request(objective)
    assert wood is not None
    assert not wood.problem, "既有木材入口必须能解析规范说法"
    assert wood.species == "橡木" and wood.count == 2
    assert objective.count("2") == 1
    assert bridge._canonical_wood_objective("not_a_block", 1) == ""


async def test_accepting_a_handoff_goes_through_the_existing_plan_and_gates() -> None:
    plans = FakePlans()
    bridge = _bridge(plans)
    await bridge._craft_handoff(
        "IdCraft", "s", "做木镐", {"checks": ({"detail": "insufficient_material:oak_log(缺 2)"},)}
    )
    reply = await bridge._accept_pending_handoff("IdCraft", "s", "好，去砍")
    assert reply is not None
    plan_call = [call for call in plans.calls if "shape_hint" in call]
    assert plan_call and plan_call[0]["shape_hint"] == "wood", "必须复用既有木材/资源计划入口"
    assert "确认" in (reply.reply or ""), "仍然要用户确认，绝不直接执行"
    assert ("IdCraft", "s") not in bridge._pending_handoffs, "单次交接：接受后即消费掉"
    assert await bridge._accept_pending_handoff("IdCraft", "s", "好，去砍") is None


async def test_accepting_without_a_pending_handoff_falls_through() -> None:
    bridge = _bridge(FakePlans())
    assert await bridge._accept_pending_handoff("IdCraft", "s", "好，去砍") is None


async def test_expired_handoff_requires_a_fresh_look() -> None:
    plans = FakePlans()
    bridge = _bridge(plans)
    bridge._pending_handoffs[("IdCraft", "s")] = {
        "item": "oak_log",
        "block": "oak_log",
        "amount": 2,
        "craft_goal": "做木镐",
        "at": -10_000.0,
    }
    reply = await bridge._accept_pending_handoff("IdCraft", "s", "好，去砍")
    assert reply is not None and "过期" in (reply.reply or "")
    assert plans.calls == [], "过期方案不许直接接进计划链"


def test_remember_craft_goal_keeps_the_original_request_for_replanning() -> None:
    bridge = _bridge()
    bridge._remember_craft_goal("IdCraft", "s", "做一把木镐")
    stored = bridge._pending_craft_goals[("IdCraft", "s")]
    assert stored["objective"] == "做一把木镐"
    assert REASON_MATERIAL == "insufficient_material"


#: 真实 plan_rejected 的结果形态（真机门禁实测，**没有 checks 字段**）。
REAL_REJECTED = {
    "action": "plan_rejected",
    "reply": "现在排不了这件事：insufficient_material:oak_log(缺 4)",
    "outcome": "blocked_by_precondition",
    "plan": None,
    "record": None,
}


def test_gap_detail_reads_the_real_plan_rejected_shape() -> None:
    """真机门禁暴露：真实 plan_rejected 没有 checks，只能从 reply 的原因码里取 detail。"""
    assert (
        MinecraftChatBridge._gap_detail_of(REAL_REJECTED) == "insufficient_material:oak_log(缺 4)"
    )
    # 既有 checks 形态保持兼容（不回归）。
    assert (
        MinecraftChatBridge._gap_detail_of({"checks": [{"detail": "no_recipe:x"}]}) == "no_recipe:x"
    )
    assert (
        MinecraftChatBridge._gap_detail_of(
            {"checks": [{"detail": {"reason": "no_recipe", "detail": "y"}}]}
        )
        == "no_recipe:y"
    )
    assert MinecraftChatBridge._gap_detail_of({}) == ""
    assert MinecraftChatBridge._gap_detail_of({"reply": "材料不够"}) == ""


async def test_real_plan_rejected_shape_produces_a_handoff_proposal() -> None:
    """真实形态（无 checks）下，缺料回话也必须接上资源交接提案（H2 在真机可达）。"""
    plans = FakePlans()
    bridge = _bridge(plans)
    text = await bridge._craft_handoff("IdCraft", "s", "给我做 4 块橡木木板", REAL_REJECTED)
    assert text, "真实形态下必须产出提案文本"
    assert "提案" in text
    assert "oak_log" in text
    assert "真实观察" in text
    stored = bridge._pending_handoffs[("IdCraft", "s")]
    assert stored["item"] == "oak_log" and stored["amount"] >= 1
    assert stored["craft_goal"] == "给我做 4 块橡木木板"
    assert [call for call in plans.calls if call.get("tool") == "minecraft_find_blocks"], (
        "必须真的观察过"
    )
