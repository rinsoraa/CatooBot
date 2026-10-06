"""Builtin tool: minecraft_craft —— 执行一次玩家 2×2 背包配方（Phase 4F）。

边界（任务书 §二/§十一/§十二/§十六/§十七/§十八/§四十二/§四十三）：
* 只接受 `minecraft_recipe_lookup` 给出的 **recipe_id**（一次**一个**配方、一次**一次**执行）：
  没有 count / times / batch_size —— 彻底避免"执行几次"和"产出几个"的语义混淆；
* 只走**玩家自身 2×2**（`bot.craft(recipe, 1, null)`）：不碰工作台、不自动放工作台；
* 材料不够 → 直接失败（`material_insufficient`）：**绝不**自动开箱取料、自动移动背包、
  自动挖矿、也**绝不**先做中间材料（recipe chain）；
* MEDIUM → 必须用户确认（USER 回合 + 可信玩家 + allow_medium + 确认门）；
* 成功后 `inventory` 真的变了（产物增加 + 材料减少），结果以**重读 inventory** 为准。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import CRAFT_MAX_RECIPE_ID_CHARS, MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

CRAFT_METADATA = ToolMetadata(
    name="minecraft_craft",
    display_name="Minecraft Craft",
    description=(
        "执行**一次**某个配方（用 recipe_id 指定，来自 minecraft_recipe_lookup）："
        "只支持玩家自己 2×2 背包合成，一次只做一个配方、只执行一次。"
        "材料不够会直接失败（不会自动去找材料）。MEDIUM 风险，需要用户确认。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "craft", "inventory"],
    keywords=[
        "做一个",
        "做几个",
        "合成",
        "制作",
        "造一个",
        "做点",
        "craft",
        "minecraft",
    ],
    when_to_use=(
        "用户明确要求合成某个东西，并且已经用 minecraft_recipe_lookup 拿到 recipe_id、"
        "确认材料够的时候。"
    ),
    when_not_to_use=(
        "还没查过配方 / 不确定材料够不够时先查（minecraft_recipe_lookup）；"
        "工作台配方、熔炉冶炼、酿造、村民交易本阶段都不支持；"
        "需要材料就去自己想办法（本工具不会自动开箱、挖矿、或先做中间材料）。"
    ),
    limitations=(
        "只支持 2×2 玩家背包合成（不需要工作台的配方）；一次调用只执行一次配方，"
        "不做批量、不做 recipe chain、不自动准备材料、不全自动生产；"
        f"recipe_id 最长 {CRAFT_MAX_RECIPE_ID_CHARS} 个字符。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "recipe_id": {
                "type": "string",
                "minLength": 3,
                "maxLength": CRAFT_MAX_RECIPE_ID_CHARS,
                # 形状：`[!]结果名*每刀产出=材料*数量[+材料*数量…]`（recipe_lookup 给的 id）
                "pattern": r"^!?[a-z0-9_]+\*[0-9]+=",
                "description": "minecraft_recipe_lookup 返回的 recipe_id（见该工具的输出）",
            }
        },
        "required": ["recipe_id"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftCraftTool(ActionTool):
    """执行一次配方：判定（含 MEDIUM 确认门）→ Service → 结构化结果。"""

    metadata = CRAFT_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.craft(arguments.get("recipe_id"))
