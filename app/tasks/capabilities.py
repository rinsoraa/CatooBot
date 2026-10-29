"""Phase 7C §六：**能力目录**（capability catalog）—— 从既有工具注册表派生，不建第二套。

任务书要求能力至少要能表达：

```text
capability_id / description / input_schema / expected_effect / risk_class / available / limitations
```

这里的唯一事实来源是**既有**的 Minecraft 原子工具表（`ACTION_RISK`，19 个）与工具运行时的注册
元数据 —— 本模块只做**只读整理**：

* 没有新工具、没有新 ActionRuntime action（§十五）；
* `available` 看的是"这个能力此刻能不能用"（服务器是否连接、工具是否启用），
  它**不代表**目标一定能完成（§六：不得因为工具存在就声称目标必然可完成）；
* `limitations` 是**静态的、写死的**限制说明（例如"一次最多一个方块""需要某距离内"），
  它们来自各阶段的既有语义，不在这里重新解释世界。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 既有 19 个 Minecraft 原子工具的风险分类（唯一事实来源）
from app.integrations.minecraft.agent import ACTION_RISK

#: 能力的静态说明与限制（人读的审计文本；**不**参与任何判定，判定看 risk_class/available）
CAPABILITY_NOTES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "minecraft_world": ("看一眼周围的世界（只读）", "读回世界快照，不改变任何东西", ()),
    "minecraft_chat": ("在游戏里说一句话", "只在聊天频道发言，不动世界", ()),
    "minecraft_look_at": ("看向某个坐标（只读朝向）", "只改朝向", ()),
    "minecraft_stop": ("停下正在做的事", "取消当前的持续动作", ()),
    "minecraft_move_to": (
        "走到附近某个点",
        "改变位置（不破坏/不放置方块）",
        ("有最大距离限制", "需要可通行的路径"),
    ),
    "minecraft_follow_player": (
        "跟着某个玩家",
        "持续跟随某个**已解析**的玩家实体",
        ("只在对方在线且可见时有效", "有最大追逐距离"),
    ),
    "minecraft_dig": (
        "挖掉单个方块",
        "破坏一个方块（会掉落物品）",
        ("一次一个方块", "需要主手工具才高效", "MEDIUM：必须用户确认"),
    ),
    "minecraft_inventory": ("看一眼自己的背包（只读）", "读回背包切片", ()),
    "minecraft_place": (
        "放置单个方块",
        "在世界里放一个方块（消耗主手物品）",
        ("一次一个方块", "需要主手有对应物品", "MEDIUM：必须用户确认"),
    ),
    "minecraft_equip": (
        "把某个物品拿到主手",
        "改自身的装备状态",
        ("一次一个槽位", "不会隐式交换", "MEDIUM"),
    ),
    "minecraft_inventory_move": (
        "在背包内移动一组物品",
        "改背包布局",
        ("单物品单槽位", "绝不隐式交换", "MEDIUM"),
    ),
    "minecraft_container_inspect": (
        "看一眼箱子/桶（只读）",
        "读回容器内容",
        ("需要站在容器旁",),
    ),
    "minecraft_container_transfer": (
        "从容器取/向容器存",
        "改容器与背包",
        ("只做单向搬运，绝不交换", "MEDIUM"),
    ),
    "minecraft_recipe_lookup": ("查一个配方（只读）", "读回配方与材料", ()),
    "minecraft_craft": (
        "按配方合成一次",
        "消耗材料、产出物品",
        ("一次一个配方", "3×3 需要工作台", "MEDIUM"),
    ),
    "minecraft_dropped_items": ("看一眼地上的掉落物（只读）", "读回掉落物列表", ()),
    "minecraft_pickup_item": (
        "捡起单个掉落物",
        "改背包（可能顺带移动）",
        ("一次一个实体", "MEDIUM"),
    ),
    "minecraft_dig_capability": (
        "问一句「这块好不好挖」（只读）",
        "读回挖掘可行性与时耗",
        (),
    ),
    "minecraft_find_blocks": (
        "找附近的某类方块（只读）",
        "读回坐标列表",
        ("有最大搜索距离与结果数上限",),
    ),
}

#: 目标文本 → 需要的能力（确定性关键词表；§六 的例子就落在这里）
OBJECTIVE_CAPABILITY_HINTS: dict[str, tuple[str, ...]] = {
    "橡木": (
        "minecraft_find_blocks",
        "minecraft_move_to",
        "minecraft_dig",
        "minecraft_pickup_item",
    ),
    "原木": (
        "minecraft_find_blocks",
        "minecraft_move_to",
        "minecraft_dig",
        "minecraft_pickup_item",
    ),
    "橡树": (
        "minecraft_find_blocks",
        "minecraft_move_to",
        "minecraft_dig",
        "minecraft_pickup_item",
    ),
    "木头": (
        "minecraft_find_blocks",
        "minecraft_move_to",
        "minecraft_dig",
        "minecraft_pickup_item",
    ),
    "挖": ("minecraft_find_blocks", "minecraft_move_to", "minecraft_dig"),
    "采集": (
        "minecraft_find_blocks",
        "minecraft_move_to",
        "minecraft_dig",
        "minecraft_pickup_item",
    ),
    "收集": (
        "minecraft_find_blocks",
        "minecraft_move_to",
        "minecraft_dig",
        "minecraft_pickup_item",
    ),
    "捡": ("minecraft_dropped_items", "minecraft_pickup_item", "minecraft_move_to"),
    "跟着": ("minecraft_follow_player",),
    "跟随": ("minecraft_follow_player",),
    "看看": ("minecraft_world",),
    "观察": ("minecraft_world",),
    "合成": ("minecraft_recipe_lookup", "minecraft_craft"),
    "制作": ("minecraft_recipe_lookup", "minecraft_craft"),
    "放": ("minecraft_place",),
    "箱子": ("minecraft_container_inspect",),
    "拿出来": ("minecraft_container_inspect", "minecraft_container_transfer"),
    "背包": ("minecraft_inventory",),
    "查看背包": ("minecraft_inventory",),
}

#: 目标里出现这些词 = 需要**设计方案/材料/机制**层面的信息（§六 的"刷铁机"就是这一类）：
#: 有工具≠目标可完成，所以它们一律升级为"缺信息"，绝不假装可执行。
DESIGN_REQUIRED_KEYWORDS: tuple[str, ...] = (
    "刷铁机",
    "农场",
    "机制",
    "设计",
    "规划",
    "村庄",
    "村民",
    "僵尸",
    "红石",
    "自动化",
)


@dataclass(frozen=True)
class Capability:
    """一个能力（§六）。**只读**：它是"她能做什么"的说明，不是权限。"""

    capability_id: str
    description: str = ""
    expected_effect: str = ""
    risk_class: str = "SAFE"
    available: bool = True
    input_schema: dict[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "capability_id": str(self.capability_id),
            "description": str(self.description),
            "expected_effect": str(self.expected_effect),
            "risk_class": str(self.risk_class),
            "available": bool(self.available),
            "input_schema": dict(self.input_schema),
            "limitations": [str(item) for item in self.limitations],
        }


def capability_catalog(
    *,
    registry: Any = None,
    minecraft_online: bool = False,
    tool_risk_table: dict[str, str] | None = None,
) -> dict[str, Capability]:
    """把既有工具表整理成能力目录（**只读**，确定性）。

    ``registry`` 是可选的工具注册表（``ToolRuntime.registry`` 之类，鸭子类型：只要
    ``all()`` / ``metadata(name)`` 能拿到 ``description`` 与 ``parameters`` 就够）；
    拿不到就用静态说明 —— 目录内容不受它影响，只有 ``input_schema``/描述会更精确。
    """
    risk_table = dict(tool_risk_table or ACTION_RISK)
    catalog: dict[str, Capability] = {}
    for capability_id, risk in sorted(risk_table.items()):
        note = CAPABILITY_NOTES.get(capability_id)
        description = str(note[0]) if note is not None else capability_id
        effect = str(note[1]) if note is not None else ""
        limitations = tuple(note[2]) if note is not None else ()
        schema = _tool_schema(registry, capability_id)
        catalog[capability_id] = Capability(
            capability_id=capability_id,
            description=description,
            expected_effect=effect,
            risk_class=str(risk),
            # §六：能力"此刻能不能用" —— Minecraft 没连上时，世界类能力一律 unavailable
            available=bool(minecraft_online),
            input_schema=schema,
            limitations=limitations,
        )
    return catalog


def _tool_schema(registry: Any, name: str) -> dict[str, Any]:
    """从既有工具注册表取 ``input_schema``（拿不到就空表，绝不让目录建设失败）。"""
    if registry is None:
        return {}
    try:
        metadata = None
        getter = getattr(registry, "metadata", None)
        if callable(getter):
            metadata = getter(name)
        if metadata is None:
            getter = getattr(registry, "get", None)
            if callable(getter):
                metadata = getter(name)
        if metadata is None:
            return {}
        schema = getattr(metadata, "parameters", None) or getattr(metadata, "input_schema", None)
        return dict(schema or {}) if isinstance(schema, dict) else {}
    except Exception:  # noqa: BLE001 - 目录只是说明，拿不到就算了
        return {}


def required_capabilities(objective: str) -> tuple[str, ...]:
    """从目标文本推出**需要哪些能力**（确定性关键词表；顺序 = 首次命中的顺序）。"""
    text = str(objective or "").lower()
    found: list[str] = []
    for keyword, capabilities in OBJECTIVE_CAPABILITY_HINTS.items():
        if keyword.lower() not in text:
            continue
        for capability in capabilities:
            if capability not in found:
                found.append(capability)
    return tuple(found)


def needs_design_information(objective: str) -> bool:
    """目标是不是属于"有工具也做不了、得先有方案"的那一类（§六）。"""
    text = str(objective or "").lower()
    return any(keyword.lower() in text for keyword in DESIGN_REQUIRED_KEYWORDS)
