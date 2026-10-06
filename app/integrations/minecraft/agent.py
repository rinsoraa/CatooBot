"""Minecraft Agent Bridge（Phase 3E）：LLM Tool → Policy → Service → Action Runtime。

边界（任务书 §三）：LLM 永远不直接碰 runtime / HTTP / Mineflayer。Minecraft Tool（7 个）
只能经过这里，而这里只回答三件事：

* **能不能做** —— :class:`MinecraftActionPolicy`：风险分级（SAFE/LOW/…）+ 显式意图门
  （LOW 只在用户明确要求时放行）+ 在线/忙碌门 + 目标在附近的上下文门（§十二~§十六）；
* **做完了是什么** —— :class:`MinecraftAgentContext`：当前动作 / 最近动作 / 活动，
  完全由 action 事件驱动（§二十/§二十二/§二十四），**绝不**自己伪造终态；
* **结果怎么给模型** —— :meth:`MinecraftAgentBridge.invoke`：结构化 ok/error（§二十七/§二十八），
  绝不把 traceback / HTTP 栈交给模型。

本模块不新增任何 Minecraft 能力，也不改变既有动作语义：动作执行永远在
:class:`~app.integrations.minecraft.service.MinecraftService` → Action Runtime 里。

意图门（§十五）只认**结构性事实**：这一轮是不是用户发起的对话（Tool Layer 不做 NLP，
「用户是不是要罐头过去」由 LLM 判断 —— §十六）。模型在自主/后台回合里推理出「我应该跟过去」
时没有用户请求，LOW 一律被拒。
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.character.turn import TurnOrigin
from app.integrations.minecraft.confirmation import (
    CODE_EXPIRED,
    CODE_MISMATCH,
    CODE_NOT_TRUSTED,
    CODE_REQUIRED,
    CODE_TASK_UNAUTHORIZED,
    CONFIRMATION_RISKS,
    ConfirmationOutcome,
    ConfirmationStore,
)
from app.integrations.minecraft.service import MinecraftBridgeError
from app.tools.models import ToolContext, ToolResult

if TYPE_CHECKING:  # pragma: no cover
    from app.config.settings import MinecraftAgentToolsConfig
    from app.integrations.minecraft.events import MinecraftBridgeEvent
    from app.integrations.minecraft.service import MinecraftService

log = logging.getLogger("CatooBot.Minecraft.Agent")

#: ToolContext.metadata 里的桥接对象键（tool 通过它拿到 service/policy/context）
BRIDGE_KEY = "minecraft"
#: 开发者调试入口使用的固定身份（WebUI 动作按钮；不是任何真实用户/会话）
DEVELOPER_USER_ID = "webui-developer"
DEVELOPER_SESSION_ID = "webui:developer"
#: ToolContext.metadata 里的「本轮来自游戏内玩家」的用户名（Phase 4A 信任门用）
PLAYER_KEY = "minecraft_player"
#: ToolContext.metadata 里的「本轮由用户明确发起」标记（意图门用；缺省 = 不允许 LOW）。
#: 它**只能**由 :class:`~app.character.turn.TurnOrigin` 派生（Phase 3E.1），
#: 任何调用方都不许手写这个键。
INTENT_KEY = "minecraft_explicit_intent"
#: 本轮来源（``TurnOrigin`` 的值）；只用于日志与排查，绝不进 LLM（Phase 3E.1 §六/§二十）
TURN_ORIGIN_KEY = "turn_origin"

#: 正式风险分级（§十三）。本阶段只落地 SAFE / LOW；MEDIUM/HIGH/DESTRUCTIVE 等 Phase 4。
RISK_LEVELS = ("SAFE", "LOW", "MEDIUM", "HIGH", "DESTRUCTIVE")

#: 已批准 Tool 的风险等级（§二）——注册表之外的任何名字都不放行
ACTION_RISK: dict[str, str] = {
    "minecraft_world": "SAFE",
    "minecraft_chat": "SAFE",
    "minecraft_look_at": "SAFE",
    "minecraft_stop": "SAFE",
    "minecraft_move_to": "LOW",
    "minecraft_follow_player": "LOW",
    # Phase 4B：第一个世界修改动作（单方块，MEDIUM → 必须用户确认）
    "minecraft_dig": "MEDIUM",
    # Phase 4C：只读背包切片（SAFE）与放置单方块（MEDIUM，对称于 dig）
    "minecraft_inventory": "SAFE",
    "minecraft_place": "MEDIUM",
    # Phase 4D：背包写操作（改真实角色状态，且影响后续 place/dig 的物品语义 → MEDIUM）
    "minecraft_equip": "MEDIUM",
    "minecraft_inventory_move": "MEDIUM",
    # Phase 4E：容器（读=Chest/Barrel 只读 inspection；存取=改容器与背包 → MEDIUM）
    "minecraft_container_inspect": "SAFE",
    "minecraft_container_transfer": "MEDIUM",
    # Phase 4F：玩家自身 2×2 背包合成（查配方=只读；执行一次配方=改背包 → MEDIUM）
    "minecraft_recipe_lookup": "SAFE",
    "minecraft_craft": "MEDIUM",
    # Phase 4H：掉落物感知（只读）/ 拾取单个掉落物实体（会移动 + 改背包 → MEDIUM）
    "minecraft_dropped_items": "SAFE",
    "minecraft_pickup_item": "MEDIUM",
    # Phase 4J：挖掘能力只读查询（不改世界、不改背包、不移动、不装备）
    "minecraft_dig_capability": "SAFE",
    # Phase 4K：找方块只读（只定位，不移动/不装备/不挖/不拾取）
    "minecraft_find_blocks": "SAFE",
}

#: Tool → Action Runtime 动作名（chat 也走统一生命周期）
TOOL_ACTION: dict[str, str] = {
    "minecraft_chat": "chat",
    "minecraft_look_at": "look_at",
    "minecraft_move_to": "move_to",
    "minecraft_follow_player": "follow_player",
    "minecraft_stop": "stop",
    "minecraft_dig": "dig",
    "minecraft_place": "place",
    "minecraft_equip": "equip",
    "minecraft_inventory_move": "inventory_move",
    "minecraft_container_inspect": "container_inspect",
    "minecraft_container_transfer": "container_transfer",
    "minecraft_recipe_lookup": "recipe_lookup",
    "minecraft_craft": "craft",
    "minecraft_dropped_items": "dropped_items",
    "minecraft_dig_capability": "dig_capability",
    "minecraft_find_blocks": "find_blocks",
    "minecraft_pickup_item": "pickup_item",
}

#: 离线也能用的 Tool：minecraft_world（离线也要能回答「我不在游戏里」）
#: 与 minecraft_stop（最高优先级安全停止，永远成功、幂等）。
#: 其余一切工具（含 Phase 4B 的 MEDIUM/HIGH 动作）都要求在线——**默认需要在线**，
#: 这样新增动作不需要记得去改一张「需要在线」的名单。
OFFLINE_TOOLS: frozenset[str] = frozenset({"minecraft_world", "minecraft_stop"})

#: **非**独占的 Tool：只有它们能与前台动作并存（chat 是唯一允许并存的通信动作，
#: world 只读，stop 是控制面）。其余一律独占 —— **默认独占**，Phase 4B/4C 加动作
#: 时不会漏掉「不能边挖边走」这类互斥约束。
NON_EXCLUSIVE_TOOLS: frozenset[str] = frozenset(
    {
        "minecraft_world",
        "minecraft_inventory",
        "minecraft_chat",
        "minecraft_stop",
        # Phase 4F：查配方是纯读取（不碰世界、不碰背包），可与前台动作并行
        "minecraft_recipe_lookup",
        # Phase 4H：看地上的掉落物也是纯读取（实体列表变化频繁不代表它要独占）
        "minecraft_dropped_items",
        # Phase 4J：挖掘能力查询是纯读取（只回答「现在能不能挖、多久」）
        "minecraft_dig_capability",
        # Phase 4K：找方块是纯读取（只回答「目标在哪里」）
        "minecraft_find_blocks",
    }
)

#: 需要「用户明确要求」才能执行的风险等级（§十四/§十五）
EXPLICIT_INTENT_RISKS: frozenset[str] = frozenset({"LOW", "MEDIUM", "HIGH", "DESTRUCTIVE"})

#: 风险等级 → 配置开关字段（§三十一；未实现的等级默认关闭，且没有对应 Tool）
RISK_FLAGS: dict[str, str] = {
    "SAFE": "allow_safe",
    "LOW": "allow_low",
    "MEDIUM": "allow_medium",
    "HIGH": "allow_high",
    "DESTRUCTIVE": "allow_destructive",
}

#: runtime 动作错误码 → 交给模型的稳定错误码（§二十七）。运行时词表不外泄。
RUNTIME_ERROR_CODES: dict[str, str] = {
    "action.not_online": "minecraft.offline",
    "chat.not_online": "minecraft.offline",
    "action.busy": "minecraft.action_busy",
    "action.invalid": "minecraft.action_invalid",
    "action.unknown": "minecraft.action_invalid",
    "action.failed": "minecraft.action_failed",
    "path.not_found": "minecraft.path_not_found",
    # Phase 4H.1：Pathfinder 结束了（或空路径静默 resolve），但实际位置不在到达半径内
    "path.not_reached": "minecraft.path_not_reached",
    "player.not_found": "minecraft.player_not_found",
    "player.lost": "minecraft.player_lost",
    "follow.target_too_far": "minecraft.follow_target_too_far",
    "session.active": "minecraft.action_busy",
    # Phase 4B：dig 的目标校验失败（§三十）
    "block.not_found": "minecraft.block_not_found",
    "block.changed": "minecraft.block_changed",
    "block.not_diggable": "minecraft.block_not_diggable",
    "block.too_far": "minecraft.block_too_far",
    "block.break_unconfirmed": "minecraft.block_break_unconfirmed",
    "block.dig_aborted": "minecraft.action_failed",
    "block.invalid": "minecraft.action_invalid",
    # Phase 4C：place 的校验失败（§九-§十二/§十九）
    "held.item_missing": "minecraft.held_item_missing",
    "held.item_changed": "minecraft.held_item_changed",
    "target.occupied": "minecraft.target_occupied",
    "reference.missing": "minecraft.reference_block_missing",
    "block.unavailable": "minecraft.block_unavailable",
    # Phase 4K：找方块（名字不认识 / 运行时给不出查询能力）
    "block.name_unknown": "minecraft.block_name_unknown",
    "block.query_unavailable": "minecraft.block_query_unavailable",
    "block.place_unconfirmed": "minecraft.block_place_unconfirmed",
    "face.invalid": "minecraft.action_invalid",
    "item.invalid": "minecraft.action_invalid",
    # Phase 4D：背包写操作
    "item.not_found": "minecraft.item_not_found",
    "item.changed": "minecraft.item_changed",
    "item.count_insufficient": "minecraft.item_count_insufficient",
    "destination.occupied": "minecraft.destination_occupied",
    "slot.invalid": "minecraft.slot_invalid",
    "equip.unconfirmed": "minecraft.equip_unconfirmed",
    "move.unconfirmed": "minecraft.move_unconfirmed",
    # Phase 4E：container（读 Chest / Barrel + 单物品存取）
    "container.unsupported": "minecraft.container_unsupported",
    "container.too_far": "minecraft.container_too_far",
    "container.open_failed": "minecraft.container_open_failed",
    "container.closed": "minecraft.container_closed",
    "container.close_failed": "minecraft.container_close_failed",
    "container.transfer_unconfirmed": "minecraft.container_transfer_unconfirmed",
    # Phase 4F：crafting（玩家 2×2）
    "recipe.not_found": "minecraft.recipe_not_found",
    "recipe.invalid": "minecraft.action_invalid",
    "recipe.unavailable": "minecraft.recipe_unavailable",
    "recipe.changed": "minecraft.recipe_changed",
    "material.insufficient": "minecraft.material_insufficient",
    # Phase 4G：指定工作台（3×3）
    "table.missing": "minecraft.crafting_table_missing",
    "table.invalid": "minecraft.crafting_table_invalid",
    "table.too_far": "minecraft.crafting_table_too_far",
    # Phase 4H：掉落物 / 拾取
    "item_entity.not_found": "minecraft.item_entity_not_found",
    "item_entity.invalid": "minecraft.item_entity_invalid",
    "item_entity.changed": "minecraft.item_entity_changed",
    "target.replaced": "minecraft.pickup_target_replaced",
    "pickup.target_lost": "minecraft.pickup_target_lost",
    "pickup.target_too_far": "minecraft.pickup_target_too_far",
    "pickup.failed": "minecraft.pickup_failed",
    "pickup.unconfirmed": "minecraft.pickup_unconfirmed",
    "craft.failed": "minecraft.craft_failed",
    "craft.unconfirmed": "minecraft.craft_unconfirmed",
}

#: 一句话活动（§二十二：SUCCEEDED → minecraft.activity）。只写事实，不写情绪。
#: 值可以是 ``str``，也可以是"按方向取模板"的 ``dict``（Phase 4E 的 container_transfer）。
_ACTIVITY_TEMPLATES: dict[str, Any] = {
    "move_to": "刚走到 {where}",
    "follow_player": "刚结束跟随 {who}",
    "look_at": "刚看向 {where}",
    "chat": "刚在服务器里说过话",
    "stop": "刚把 Minecraft 行动停下来了",
    "dig": "刚挖掉了 {block}",
    "place": "刚放好了 {block}",
    "equip": "刚把 {block} 拿到手里",
    "inventory_move": "刚把 {block} 从 {source} 格移到了 {destination} 格",
    # Phase 4E：容器动作只写事实（绝不写"整理了一下仓库"这种掩盖精确范围的句子）
    "container_inspect": "刚打开了一个 {container} 并查看了里面的东西",
    "container_transfer": {
        "withdraw": "刚从 {where} 的 {container} 取出了 {block} ×{count}",
        "deposit": "刚把 {block} ×{count} 放回 {where} 的 {container}",
    },
    # Phase 4F：只写事实（"刚做好了 4 个 stick"），绝不写"做了很多木棍"这种估摸的话
    "craft": "刚做好了 {count} 个 {block}",
    # Phase 4H：同样是事实（成功才记；没捡到走的是失败路径，不进 activity）
    "pickup_item": "刚拣起了 {block} ×{count}",
}

#: activity 取哪个结果字段当"那个方块/物品"（dig 看挖掉的、place 看放上的、equip/move 看物品名）
_ACTIVITY_BLOCK_FIELDS: dict[str, str] = {
    "dig": "block_before",
    "place": "block_after",
    "equip": "item",
    "inventory_move": "item",
    "container_transfer": "item",
    "craft": "item",
    "pickup_item": "item",
}

#: 容器类型 → 中文/英文显示名（activity 与摘要里都用它，绝不写死"箱子"）
_CONTAINER_LABELS: dict[str, str] = {
    # 归一化后的方块名（runtime 侧已经去掉 minecraft: 前缀）
    "chest": "Chest",
    "barrel": "Barrel",
}


def recipe_summary(
    recipe_id: str, crafting_table: Any = None, *, table_label: str = "Crafting Table"
) -> str:
    """把可读的 recipe_id 展开成确认摘要（§十三/§十六）。

    * 2×2：``stick*4=oak_planks*2`` → ``用 2 个 oak_planks 制作 4 个 stick``；
    * 3×3（给了坐标）→ ``用 8 个 oak_planks 在 (100, 64, 100) 的 Crafting Table 制作 1 个 chest``。

    解析不出来就退化成"执行配方 <id>"。这样确认文本说的是"用哪种材料、在哪张工作台上做"，
    而不是"执行 recipe abc123"——**坐标也进入确认文本**，因为换个工作台就是另一个世界操作。
    """
    text = str(recipe_id or "").strip().lstrip("!")
    where = ""
    if isinstance(crafting_table, Mapping):
        where = _format_position(crafting_table)
    suffix = f"在 {where} 的 {table_label} " if where else ""
    head, separator, tail = text.partition("=")
    if not separator or "*" not in head:
        return f"执行配方 {recipe_id}"
    result_name, _, result_count = head.partition("*")
    parts: list[str] = []
    for chunk in tail.split("+"):
        name, _, count = chunk.partition("*")
        if not name:
            continue
        parts.append(f"{count or '1'} 个 {name}")
    body = f"{result_count or '1'} 个 {result_name}"
    if not parts:
        return f"{suffix}制作 {body}" if suffix else f"制作 {body}"
    return f"用 {' + '.join(parts)} {suffix}制作 {body}"


def _container_label(type_name: Any) -> str:
    """容器类型 → 显示名（未知一律"容器"；绝不猜类型）。"""
    key = str(type_name or "").strip().lower().removeprefix("minecraft:")
    return _CONTAINER_LABELS.get(key, "容器")


_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT"})


# ------------------------------------------------------------------ 判定


@dataclass(frozen=True)
class PolicyDecision:
    """一次 Tool 调用的放行结论（``code``/``message`` 只在拒绝时非空）。"""

    allowed: bool
    tool: str
    risk: str
    code: str = ""
    message: str = ""


@dataclass(frozen=True)
class GateFacts:
    """判定所需的事实快照（由 bridge 采集；policy 自己不碰 Service/网络）。"""

    minecraft_enabled: bool = False
    online: bool = False
    #: 正在运行的独占动作（runtime 动作名）；空 = 空闲
    busy: str = ""
    explicit_intent: bool = False
    #: 当前语义世界模型里的附近玩家名（空元组 = 感知不可用，不做该检查）
    nearby_players: tuple[str, ...] = ()
    #: 本轮来源（仅供日志/审计；判定只读 ``explicit_intent``，绝不读它）
    turn_origin: str = ""
    #: 本轮来自游戏内玩家说话时的 MC 用户名（空 = 不是来自游戏内的回合）
    minecraft_player: str = ""
    #: 可信玩家名单（配置；只有 ``minecraft_player`` 非空时才参与判定）
    trusted_players: tuple[str, ...] = ()
    #: Phase 5A：这一轮是**任务步骤**（origin == task），且拿到了已校验的步骤授权
    task_id: str = ""
    task_step_id: str = ""
    task_authorized: bool = False


class MinecraftActionPolicy:
    """Minecraft Tool 的唯一硬门（任务书 §十二/§十四/§十五）。"""

    def __init__(
        self,
        tools: MinecraftAgentToolsConfig,
        logger: logging.Logger | None = None,
        *,
        risk_table: Mapping[str, str] | None = None,
    ) -> None:
        self.tools = tools
        #: 工具 → 风险等级。默认就是正式表（六个 Tool）；Phase 4B 起扩表时改这里，
        #: 测试用的桩动作通过参数注入——生产代码里绝不出现未实现动作的名字。
        self.risk_table: dict[str, str] = dict(risk_table or ACTION_RISK)
        self._log = logger or log

    def risk_of(self, tool: str) -> str:
        return self.risk_table.get(tool, "")

    def allowed_by_config(self, tool: str) -> bool:
        """纯配置视角的放行（不含在线/忙碌/意图）——WebUI 只读展示也用它。"""
        risk = self.risk_of(tool)
        if not risk:
            return False
        return bool(self.tools.enabled and getattr(self.tools, RISK_FLAGS[risk], False))

    def check(
        self,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        facts: GateFacts | None = None,
    ) -> PolicyDecision:
        """按固定顺序判定（§三十三）：

        注册 → Minecraft 启用 → 工具启用 → 在线 → 风险开关 → USER 回合
        → 可信玩家 → 忙 → 确认。

        SAFE 不需要 USER、不需要 trusted；LOW 需要 USER（来自游戏内时还要 trusted）；
        MEDIUM/HIGH/DESTRUCTIVE 还要**确认**——这里只回答「需要确认」，
        真正消费确认的是 :class:`MinecraftAgentBridge`（它持有确认存储）。
        """
        facts = facts or GateFacts()
        origin = facts.turn_origin
        risk = self.risk_of(tool)
        if not risk:
            return self._reject(
                tool,
                risk,
                "minecraft.action_invalid",
                f"未注册的 Minecraft 工具：{tool}",
                turn_origin=origin,
            )
        if not facts.minecraft_enabled:
            return self._reject(
                tool, risk, "minecraft.disabled", "Minecraft 连接层未启用", turn_origin=origin
            )
        if not self.tools.enabled:
            return self._reject(
                tool, risk, "minecraft.disabled", "Minecraft 工具未启用", turn_origin=origin
            )
        if tool not in OFFLINE_TOOLS and not facts.online:
            return self._reject(
                tool,
                risk,
                "minecraft.offline",
                "罐头现在不在 Minecraft 世界里",
                turn_origin=origin,
            )
        if not getattr(self.tools, RISK_FLAGS[risk], False):
            return self._reject(
                tool,
                risk,
                "minecraft.action_not_allowed",
                f"{risk} 级动作未获允许",
                turn_origin=origin,
            )
        if origin == "task":
            # Phase 5A §十六/§十七：任务步骤**绝不**冒充用户回合 —— 它的放行来自
            # "已确认的冻结计划 + 步骤授权"（bridge 先向 TaskRuntime 校验
            # plan_hash + tool + arguments_hash 三者都对得上）。
            if risk in EXPLICIT_INTENT_RISKS and not facts.task_authorized:
                return self._reject(
                    tool,
                    risk,
                    CODE_TASK_UNAUTHORIZED,
                    "这个任务步骤没有有效授权（计划未确认、授权已过期或参数对不上）",
                    turn_origin=origin,
                )
        elif risk in EXPLICIT_INTENT_RISKS and not facts.explicit_intent:
            return self._reject(
                tool,
                risk,
                "minecraft.action_not_allowed",
                "这需要用户明确要求；不要自己决定移动罐头（可以先用 minecraft_world 看看情况）",
                turn_origin=origin,
            )
        if (
            risk in EXPLICIT_INTENT_RISKS
            and facts.minecraft_player
            and facts.minecraft_player not in facts.trusted_players
        ):
            # §二十一/§二十二：游戏里的陌生人喊「罐头过来」不该让她真的跑过去。
            # 只对**来自游戏内**的回合生效（QQ/WebUI 回合没有 minecraft_player）→ §二十六 不变。
            return self._reject(
                tool,
                risk,
                CODE_NOT_TRUSTED,
                f"玩家「{facts.minecraft_player}」还不在可信名单里，罐头不会执行这个动作",
                turn_origin=origin,
            )
        if tool not in NON_EXCLUSIVE_TOOLS and facts.busy:
            return self._reject(
                tool,
                risk,
                "minecraft.action_busy",
                f"当前正在执行 Minecraft 行动（{facts.busy}）；"
                "如需打断，先用 minecraft_stop 停止它",
                turn_origin=origin,
            )
        if risk in CONFIRMATION_RISKS and origin != "task":
            # §四/§三十三：MEDIUM/HIGH/DESTRUCTIVE 一律要用户确认——这里只标记
            # 「需要确认」，由 bridge 消费一条匹配的 PENDING（消费必须发生在 USER 回合）。
            # Phase 5A：任务步骤不再逐步确认 —— 用户已经**一次性确认了整份冻结计划**，
            # 每一步靠 TaskStepAuthorization 放行（上面刚检查过）。
            self._log.info(
                "[MC Policy] confirmation tool=%s risk=%s turn_origin=%s trusted=%s",
                tool,
                risk,
                origin or "unknown",
                bool(facts.minecraft_player and facts.minecraft_player in facts.trusted_players),
            )
            return PolicyDecision(
                allowed=False,
                tool=tool,
                risk=risk,
                code=CODE_REQUIRED,
                message="这个 Minecraft 动作需要用户确认",
            )
        target = str((arguments or {}).get("username", "")).strip()
        if (
            tool == "minecraft_follow_player"
            and target
            and facts.nearby_players
            and target not in facts.nearby_players
        ):
            # §四十二：目标必须真的在附近——不猜、不拼、不跟随看不见的玩家
            known = "、".join(facts.nearby_players[:5]) or "（没有其他人）"
            return self._reject(
                tool,
                risk,
                "minecraft.player_not_found",
                f"附近没有叫「{target}」的玩家；现在能看到的只有：{known}",
                turn_origin=origin,
            )
        self._log.info(
            "[MC Policy] allowed tool=%s risk=%s turn_origin=%s trusted=%s confirmation=%s",
            tool,
            risk,
            facts.turn_origin or "unknown",
            bool(facts.minecraft_player and facts.minecraft_player in facts.trusted_players),
            "required" if risk in CONFIRMATION_RISKS else "not_required",
        )
        return PolicyDecision(allowed=True, tool=tool, risk=risk)

    def _reject(
        self, tool: str, risk: str, code: str, message: str, *, turn_origin: str = ""
    ) -> PolicyDecision:
        self._log.info(
            "[MC Policy] rejected tool=%s risk=%s turn_origin=%s code=%s",
            tool,
            risk,
            turn_origin or "unknown",
            code,
        )
        return PolicyDecision(allowed=False, tool=tool, risk=risk, code=code, message=message)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.tools.enabled),
            "risk_flags": {
                level: bool(getattr(self.tools, flag, False)) for level, flag in RISK_FLAGS.items()
            },
            "registered": dict(self.risk_table),
            "confirmation_risks": sorted(CONFIRMATION_RISKS),
        }


# ------------------------------------------------------------------ 上下文


class MinecraftAgentContext:
    """当前/最近动作与在线状态（§十九/§二十二/§二十四）。

    只由事件驱动：``RUNNING`` 不会被自己「模拟完成」，终态只来自 runtime 的 action 事件。
    世界事实（坐标/附近玩家）由 bridge 在需要时向感知层现取，这里不快照、不进历史。
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.online: bool = False
        self.username: str = ""
        self.current_action: dict[str, Any] | None = None
        self.last_action: dict[str, Any] | None = None
        self.activity: str = ""
        self.updated_at: float = 0.0

    # ------------------------------------------------------------- 事件入口

    def apply_event(self, event: MinecraftBridgeEvent) -> None:
        name = event.type_name
        self.updated_at = self._clock()
        if name == "minecraft.spawned":
            self.online = True
            self.username = str(event.data.get("username") or self.username)
            return
        if name in ("minecraft.connecting", "minecraft.connected"):
            self.online = False
            if name == "minecraft.connecting":
                self.current_action = None
            return
        if name in ("minecraft.disconnected", "minecraft.kicked"):
            # 断线/踢出：runtime 会取消所有动作，本地也必须清掉「正在做」
            self.online = False
            self.current_action = None
            return
        if not name.startswith("minecraft.action."):
            return
        data = dict(event.data)
        status = str(data.get("status") or "")
        if name == "minecraft.action.started":
            self.current_action = {
                "action": data.get("action"),
                "action_id": data.get("action_id"),
                "status": status or "RUNNING",
                "started_at": data.get("started_at"),
            }
            return
        record = self._terminal_record(data, status)
        self.current_action = None
        self.last_action = record
        if status == "SUCCEEDED":
            self.activity = self._describe(record)

    def _terminal_record(self, data: dict[str, Any], status: str) -> dict[str, Any]:
        return {
            "action": data.get("action"),
            "action_id": data.get("action_id"),
            "status": status or "FAILED",
            "code": RUNTIME_ERROR_CODES.get(str(data.get("code") or ""), ""),
            "error": str(data.get("error") or ""),
            "result": data.get("result") if isinstance(data.get("result"), dict) else None,
            "at": self._clock(),
        }

    @staticmethod
    def _describe(record: Mapping[str, Any]) -> str:
        action = str(record.get("action") or "")
        template: Any = _ACTIVITY_TEMPLATES.get(action, "刚做完一个 Minecraft 动作")
        result = record.get("result") or {}
        if isinstance(template, dict):
            # Phase 4E：container_transfer 的句子取决于 direction（拿出去 / 放进去）
            template = template.get(str(result.get("direction") or ""), "刚做完一个 Minecraft 动作")
        where = _format_position(
            result.get("final_position") or result.get("target") or result.get("position")
        )
        who = str(result.get("username") or "").strip()
        field = _ACTIVITY_BLOCK_FIELDS.get(action, "block_before")
        block_raw: Any = result.get(field)
        if isinstance(block_raw, Mapping):
            # 4H：pickup 的 result.item 是 {name, count_before} 这种语义快照，取名字即可
            block_raw = block_raw.get("name")
        block = str(block_raw or "").strip()
        if action == "dig":
            # §三十四：用了工具就只陈述这个事实
            # （"用 minecraft:stone_pickaxe 挖掉了 minecraft:iron_ore"），
            # 没要求工具就照旧（"挖掉了 minecraft:stone"）。绝不写"高效地""轻松"这种评价。
            tool_used = str(result.get("tool_actual") or "").strip()
            if tool_used:
                return f"刚用 {tool_used} 挖掉了 {block or '一个方块'}"
        nested = result.get("result")
        nested = nested if isinstance(nested, Mapping) else {}
        container_type = result.get("container_type")
        container = result.get("container")
        if not container_type and isinstance(container, Mapping):
            container_type = container.get("type")
        try:
            return template.format(
                where=where or "目标位置",
                who=who or "对方",
                block=block or "一个方块",
                source=result.get("source_slot"),
                destination=result.get("destination_slot"),
                container=_container_label(container_type),
                count=result.get("count")
                or result.get("crafted_count")
                or result.get("collected_count")
                or nested.get("crafted_count")
                or 1,
            )
        except (KeyError, IndexError):  # pragma: no cover - 模板是常量，坏不了
            return "刚做完一个 Minecraft 动作"

    # ------------------------------------------------------------- 只读投影

    def snapshot(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "username": self.username,
            "current_action": dict(self.current_action) if self.current_action else None,
            "last_action": dict(self.last_action) if self.last_action else None,
            "activity": self.activity,
            "updated_at": self.updated_at,
        }


def _format_position(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    try:
        return f"({float(value['x']):.0f}, {float(value['y']):.0f}, {float(value['z']):.0f})"
    except (KeyError, TypeError, ValueError):
        return ""


# -------------------------------------------------------------------- 桥


class MinecraftAgentBridge:
    """Minecraft Tool 的唯一入口：判定（含确认门）→ Service → 结构化结果。"""

    def __init__(
        self,
        service: MinecraftService,
        *,
        clock: Callable[[], float] = time.time,
        risk_table: Mapping[str, str] | None = None,
    ) -> None:
        self.service = service
        self._clock = clock
        self.policy = MinecraftActionPolicy(
            service.config.agent.tools, risk_table=risk_table, logger=log
        )
        self.context = MinecraftAgentContext(clock=clock)
        #: Phase 4A：MEDIUM/HIGH 的用户确认（内存态、TTL、绑 user/session/args）
        self.confirmations = ConfirmationStore(
            ttl_seconds=service.config.agent.confirmation.ttl_seconds,
            max_pending=service.config.agent.confirmation.max_pending,
            clock=clock,
        )
        #: Phase 5A：任务步骤授权校验器（None = 任何任务步骤都不放行，fail-closed）
        self._task_authorizer: Any = None
        #: 已校验过的一次性凭据 → (task_id, step_id, 过期时刻)。外部无法伪造（进程内随机串）。
        self._task_tokens: dict[str, tuple[str, str, float]] = {}

    # ------------------------------------------------------------ 事实采集

    @property
    def enabled(self) -> bool:
        return bool(self.service.enabled and self.service.config.agent.tools.enabled)

    def world_view(self) -> dict[str, Any]:
        """只读世界视图（语义模型；raw snapshot 绝不外泄给 LLM）。"""
        return self.service.world_view()

    async def inventory(self) -> dict[str, Any]:
        """只读背包切片（Tool → Bridge/Policy → Service → runtime 只读状态，§三十五）。"""
        return await self.service.inventory()

    def world_facts(self) -> dict[str, Any]:
        """从感知层现取世界事实（在线/维度/坐标/附近玩家）。"""
        view = self.world_view()
        semantic = view.get("semantic") or {}
        self_state = semantic.get("self") or {}
        players = [
            {
                "name": str(player.get("name") or ""),
                "distance": player.get("distance"),
                "direction": player.get("direction"),
                "position": player.get("position"),
            }
            for player in semantic.get("players") or []
            if player.get("name")
        ]
        online = bool(view.get("online")) and str(self_state.get("dimension") or "") != ""
        if online:
            self.context.online = True
        return {
            "available": bool(view.get("available")),
            "online": online,
            "dimension": self_state.get("dimension"),
            "position": self_state.get("position"),
            "biome": self_state.get("location"),
            "players": players,
            "age_seconds": view.get("age_seconds"),
        }

    def gate_facts(
        self,
        *,
        explicit_intent: bool = False,
        turn_origin: str = "",
        minecraft_player: str = "",
        task_facts_values: tuple[str, str, bool] = ("", "", False),
    ) -> GateFacts:
        world = self.world_facts()
        current = self.context.current_action or {}
        busy = ""
        if str(current.get("status") or "") == "RUNNING":
            busy = str(current.get("action") or "")
        return GateFacts(
            minecraft_enabled=bool(self.service.enabled),
            online=bool(world["online"]) or self.context.online,
            busy=busy,
            explicit_intent=explicit_intent,
            nearby_players=tuple(player["name"] for player in world["players"]),
            turn_origin=turn_origin,
            minecraft_player=minecraft_player,
            trusted_players=tuple(self.service.config.agent.trusted_players),
            task_id=task_facts_values[0],
            task_step_id=task_facts_values[1],
            task_authorized=task_facts_values[2],
        )

    # ------------------------------------------------------------ Phase 5A：任务步骤

    def set_task_authorizer(self, authorizer: Any = None) -> None:
        """装上"这一步有没有被授权"的校验器（由 TaskRuntime 提供）。

        bridge **只相信**这个校验器的回答：WebUI/系统调用永远拿不到 ``TASK`` 回合，
        也就永远无法自己造出一条任务授权（§八十七 case 6）。
        """
        self._task_authorizer = authorizer

    async def invoke_task_step(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        task_id: str,
        step_id: str,
        plan_hash: str,
        risk: str,
        authorization: Any,
        call: Callable[[MinecraftService], Awaitable[dict[str, Any]]],
    ) -> ToolResult:
        """执行**一个已授权的任务步骤**（§十五/§十七/§十八）。

        与普通工具调用的唯一区别：回合来源是 ``TASK``（**不是** USER），
        并且必须先通过 TaskRuntime 的步骤授权校验（plan_hash + tool + 参数指纹）。
        """
        authorizer = self._task_authorizer
        authorized = False
        if authorizer is not None:
            try:
                authorized = bool(
                    await authorizer(
                        task_id=task_id,
                        step_id=step_id,
                        tool=tool,
                        arguments=dict(arguments),
                        plan_hash=plan_hash,
                    )
                )
            except Exception:  # noqa: BLE001 - 校验器坏了必须 fail-closed
                log.exception("[MC Task] authorizer crashed task=%s step=%s", task_id, step_id)
                authorized = False
        if not authorized:
            message = "任务步骤没有有效授权（计划未确认、授权已过期或参数对不上）"
            return ToolResult(
                tool_name=tool,
                success=False,
                error=message,
                error_type=CODE_TASK_UNAUTHORIZED,
                data={
                    "ok": False,
                    "error": {"code": CODE_TASK_UNAUTHORIZED, "message": message},
                    "task_id": task_id,
                    "step_id": step_id,
                },
                metadata={"source_type": "minecraft", "confidence": 0.0},
            )
        token = secrets.token_urlsafe(18)
        self._sweep_task_tokens()
        self._task_tokens[token] = (str(task_id), str(step_id), self._clock() + 30.0)
        context = ToolContext(
            user_id=task_id,
            session_id=task_id,
            metadata={
                BRIDGE_KEY: self,
                TURN_ORIGIN_KEY: TurnOrigin.TASK.value,
                INTENT_KEY: False,  # 任务步骤**不是**用户回合（绝不冒充）
                TASK_TOKEN_KEY: token,
            },
        )
        log.info(
            "[MC Task] step tool=%s task=%s step=%s risk=%s",
            tool,
            task_id,
            step_id,
            risk or self.policy.risk_of(tool),
        )
        return await self.invoke(tool, arguments, call, context=context)

    def _sweep_task_tokens(self) -> None:
        now = self._clock()
        for token, (_task, _step, expires_at) in list(self._task_tokens.items()):
            if expires_at <= now:
                self._task_tokens.pop(token, None)

    def _resolve_task_token(self, token: str) -> tuple[str, str, bool]:
        """把一次性凭据解析成任务事实（bridge 内部用；外部拼不出有效凭据）。"""
        entry = self._task_tokens.get(str(token))
        if entry is None:
            return ("", "", False)
        task_id, step_id, expires_at = entry
        if expires_at <= self._clock():
            self._task_tokens.pop(str(token), None)
            return ("", "", False)
        return (task_id, step_id, True)

    # ------------------------------------------------------------ 判定/调用

    def check(
        self, tool: str, arguments: Mapping[str, Any] | None = None, *, context: ToolContext
    ) -> PolicyDecision:
        return self.policy.check(
            tool,
            arguments,
            self.gate_facts(
                explicit_intent=explicit_intent(context),
                turn_origin=turn_origin(context),
                minecraft_player=minecraft_player(context),
                task_facts_values=task_facts(context, self._resolve_task_token),
            ),
        )

    def check_developer(
        self, tool: str, arguments: Mapping[str, Any] | None = None
    ) -> PolicyDecision:
        """开发者调试判定（§三十七/§七十二）：管理员**显式点击**视为「用户要求」。

        只跳过「这一轮是不是用户发起的对话」这一条（WebUI 的动作按钮本来就没有对话），
        其余门照旧：风险开关 / 在线 / 忙碌 / **确认门**。它**不能**代替用户确认 ——
        MEDIUM 动作在这里同样只会得到 `minecraft.confirmation_required`。
        """
        return self.policy.check(
            tool,
            arguments,
            self.gate_facts(explicit_intent=True, turn_origin=TurnOrigin.SYSTEM.value),
        )

    async def invoke_developer(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        call: Callable[[MinecraftService], Awaitable[dict[str, Any]]],
    ) -> ToolResult:
        """开发者调试执行：判定（显式意图）→ 确认门 → 执行面。"""
        decision = self.check_developer(tool, arguments)
        if not decision.allowed:
            if decision.code != CODE_REQUIRED:
                return self.denial(tool, decision)
            dev = ToolContext(
                user_id=DEVELOPER_USER_ID,
                session_id=DEVELOPER_SESSION_ID,
                metadata={
                    BRIDGE_KEY: self,
                    TURN_ORIGIN_KEY: TurnOrigin.SYSTEM.value,
                    INTENT_KEY: True,
                },
            )
            outcome = self._resolve_confirmation(tool, arguments, dev)
            if not outcome.ok:
                return self._confirmation_failure(tool, arguments, dev, outcome)
        log.info("[MC Tool] requested tool=%s args=%s (developer)", tool, _preview(arguments))
        try:
            payload = await call(self.service)
        except MinecraftBridgeError as exc:
            code = _stable_code(getattr(exc, "code", ""))
            log.info("[MC Tool] failed tool=%s code=%s", tool, code)
            return _failure(tool, code, str(exc), detail=getattr(exc, "detail", None))
        except Exception:  # noqa: BLE001 - 调试入口同样不抛给上层
            log.exception("[MC Tool] crashed tool=%s (developer)", tool)
            return _failure(tool, "minecraft.action_failed", "Minecraft 调用失败")
        return self._success(tool, payload)

    def denial(self, tool: str, decision: PolicyDecision) -> ToolResult:
        """策略拒绝 → 结构化失败（§二十七）。"""
        return _failure(tool, decision.code, decision.message)

    async def invoke(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        call: Callable[[MinecraftService], Awaitable[dict[str, Any]]],
        *,
        context: ToolContext,
    ) -> ToolResult:
        """判定 → 调 Service → 结构化结果；任何异常都不外泄给模型。"""
        decision = self.check(tool, arguments, context=context)
        if not decision.allowed:
            if decision.code != CODE_REQUIRED:
                return self.denial(tool, decision)
            # §十/§十一：MEDIUM/HIGH 需要确认——只有「本轮是 USER 回合 + 存在匹配的
            # PENDING 确认」才算用户授权；否则如实报「需要确认」并给出待确认信息。
            outcome = self._resolve_confirmation(tool, arguments, context)
            if not outcome.ok:
                return self._confirmation_failure(tool, arguments, context, outcome)
            log.info(
                "[MC Confirmation] authorised tool=%s id=%s",
                tool,
                outcome.confirmation.confirmation_id if outcome.confirmation else "-",
            )
        log.info("[MC Tool] requested tool=%s args=%s", tool, _preview(arguments))
        try:
            payload = await call(self.service)
        except MinecraftBridgeError as exc:
            code = _stable_code(getattr(exc, "code", ""))
            log.info("[MC Tool] failed tool=%s code=%s", tool, code)
            detail = getattr(exc, "detail", None)
            return _failure(tool, code, str(exc), detail=detail)
        except Exception as exc:  # noqa: BLE001 - 工具层永不抛给模型
            log.exception("[MC Tool] crashed tool=%s", tool)
            return _failure(
                tool, "minecraft.action_failed", f"Minecraft 调用失败（{type(exc).__name__}）"
            )
        return self._success(tool, payload)

    def _success(self, tool: str, payload: Mapping[str, Any]) -> ToolResult:
        action = str(payload.get("action") or TOOL_ACTION.get(tool, tool))
        status = str(payload.get("status") or "SUCCEEDED")
        data: dict[str, Any] = {"ok": True, "action": action, "status": status}
        action_id = payload.get("action_id")
        if action_id:
            data["action_id"] = str(action_id)
        if isinstance(payload.get("result"), dict):
            data["result"] = payload["result"]
        elif not action_id:
            # Phase 5A：**只读视图**（inventory / world / find_blocks / dig_capability …）的
            # 载荷本身就是结果，没有 action_id 也没有 ``result`` 包装。以前这些字段被丢掉了，
            # 于是"读背包"只剩一个空壳（示例：{'ok': True, 'action': …, 'status': …}）——
            # 任务侧的最终校验（重新读背包）与规划阶段的观察都因此拿不到数据。这里把视图
            # 原样并入 data（已有键不覆盖），动作路径（有 action_id）完全不受影响。
            for key, value in payload.items():
                data.setdefault(key, value)
        if tool == "minecraft_stop":
            data["cancelled"] = list(payload.get("cancelled") or [])
            data["status"] = str(payload.get("status") or "IDLE")
        log.info(
            "[MC Action] tool=%s action_id=%s status=%s",
            tool,
            data.get("action_id") or "-",
            data["status"],
        )
        return ToolResult(
            tool_name=tool,
            success=True,
            data=data,
            summary=_summarize(tool, data),
            metadata={"source_type": "minecraft", "confidence": 0.9},
        )

    # ---------------------------------------------------------- 确认门(4A)

    def _resolve_confirmation(
        self, tool: str, arguments: Mapping[str, Any], context: ToolContext
    ) -> ConfirmationOutcome:
        """本轮是否带着一条有效确认？（§十-§十四）

        * 没有 PENDING：创建一条并如实报告「需要用户确认」（动作不执行）；
        * 有 PENDING：交给 :meth:`ConfirmationStore.consume` 做
          来源(USER) → 归属(session/user) → 参数指纹 → 一次性 的完整校验。
        """
        session_id = str(context.session_id)
        user_id = str(context.user_id)
        pending = self.confirmations.latest_for(session_id=session_id, user_id=user_id, tool=tool)
        if pending is None:
            self._request_confirmation(tool, arguments, context)
            return ConfirmationOutcome(
                ok=False, code=CODE_REQUIRED, message="这个 Minecraft 动作需要用户确认"
            )
        outcome = self.confirmations.consume(
            pending.confirmation_id,
            session_id=session_id,
            user_id=user_id,
            arguments=arguments,
            turn_origin=turn_origin_enum(context),
        )
        if outcome.ok:
            return outcome
        if outcome.code == CODE_EXPIRED:
            # §八：过期不能再使用 —— 按当前参数重新挂一条，让用户再确认一次
            self._request_confirmation(tool, arguments, context)
            return outcome
        if outcome.code == CODE_MISMATCH:
            # §七：用户确认的是**哪一个**动作，参数不可变 —— 参数变了就不是同一个动作：
            # 作废旧确认、按新参数重新挂一条（下一次用户确认才是对新动作的授权）。
            self.confirmations.cancel(pending.confirmation_id)
            self._request_confirmation(tool, arguments, context)
            return ConfirmationOutcome(
                ok=False,
                code=CODE_MISMATCH,
                message="动作参数与你确认时的不一样，已按新参数重新发起确认",
            )
        return outcome

    def _request_confirmation(
        self, tool: str, arguments: Mapping[str, Any], context: ToolContext
    ) -> None:
        """挂一条 PENDING（同会话同用户同工具同参数时复用）。"""
        risk = self.policy.risk_of(tool)
        self.confirmations.create(
            session_id=str(context.session_id),
            user_id=str(context.user_id),
            tool=tool,
            risk=risk,
            arguments=arguments,
            summary=_confirmation_summary(tool, risk, arguments),
        )

    def _confirmation_failure(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
        outcome: ConfirmationOutcome,
    ) -> ToolResult:
        """需要确认 / 确认无效 → 结构化失败（把待确认信息一起交给模型）。"""
        extra: dict[str, Any] = {}
        pending = self.confirmations.find_pending(
            session_id=str(context.session_id),
            user_id=str(context.user_id),
            tool=tool,
            arguments=arguments,
        )
        if pending is not None and outcome.code == CODE_REQUIRED:
            extra["confirmation"] = pending.to_payload()
        message = outcome.message
        if outcome.code == CODE_REQUIRED:
            message += (
                "；先把这件事用你自己的话告诉用户，等他明确说「确认」之后"
                "再用完全相同的参数重新调用这个工具"
            )
        log.info("[MC Confirmation] required tool=%s code=%s", tool, outcome.code)
        return _failure(tool, outcome.code, message, extra=extra)

    # ------------------------------------------------------------ 事件入口

    def apply_event(self, event: MinecraftBridgeEvent) -> None:
        """订阅 MinecraftService 的事件流：只更新上下文，绝不触发新的 Agent Turn（§二十一）。"""
        self.context.apply_event(event)
        if event.type_name.startswith("minecraft.action."):
            data = event.data
            log.info(
                "[MC Action] event=%s action=%s action_id=%s status=%s",
                event.type_name.removeprefix("minecraft.action."),
                data.get("action"),
                data.get("action_id") or "-",
                data.get("status") or "-",
            )

    # ------------------------------------------------------------ 只读投影

    def context_line(self, *, limit: int = 240) -> str:
        """每轮注入的紧凑上下文（§十九/§四十五：一行，绝不塞历史、绝不塞 raw snapshot）。"""
        parts: list[str] = []
        world = self.world_facts()
        if world["online"]:
            where = _format_position(world["position"]) or "未知坐标"
            biome = str(world["biome"] or "").strip()
            parts.append(
                f"罐头正在 Minecraft 里（{world['dimension'] or 'overworld'}，{where}"
                + (f"，{biome}" if biome else "")
                + "）"
            )
            players = world["players"]
            if players:
                parts.append(
                    "附近玩家："
                    + "、".join(
                        f"{player['name']}（{player['distance']}格，{player['direction']}）"
                        for player in players[:4]
                    )
                )
            else:
                parts.append("附近没有其他玩家")
            current = self.context.current_action
            if current:
                target = ""
                result = (self.context.last_action or {}).get("result") or {}
                if isinstance(result, dict) and result.get("username"):
                    target = f" {result['username']}"
                parts.append(f"正在做：{current.get('action')}{target}（{current.get('status')}）")
            last = self.context.last_action
            if last and str(last.get("status")) != "SUCCEEDED":
                detail = last.get("code") or last.get("error") or last.get("status")
                parts.append(f"上一次动作没成功：{last.get('action')}（{detail}）")
            elif self.context.activity:
                parts.append(self.context.activity)
        elif self.service.enabled:
            parts.append("罐头现在不在 Minecraft 服务器里")
        if not parts:
            return ""
        line = "；".join(parts) + "。"
        return line[:limit]

    def snapshot(self, *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """WebUI 只读投影：可用工具 + 风险/允许 + 当前上下文（§三十/§四十八）。"""
        world = self.world_facts()
        return {
            "enabled": self.enabled,
            "context": {
                **self.context.snapshot(),
                **{
                    key: world[key]
                    for key in ("available", "dimension", "position", "biome", "players")
                },
            },
            "policy": self.policy.snapshot(),
            "tools": tools or [],
            # Phase 4A：待确认列表 + 可信玩家（只读；Debug 面板与「确认门」都看这里）
            "confirmations": self.confirmations.snapshot(),
            "trusted_players": list(self.service.config.agent.trusted_players),
        }


# ------------------------------------------------------------------ 工具侧辅助


def explicit_intent(context: ToolContext) -> bool:
    """本轮是否由用户明确发起（结构性事实；Tool Layer 不做 NLP —— 任务书 §十六）。

    该键只能由 :class:`~app.character.turn.TurnOrigin` 派生（Phase 3E.1）；
    读不到 = 不允许 LOW 动作（fail-closed）。
    """
    return bool(context.metadata.get(INTENT_KEY, False))


def turn_origin(context: ToolContext) -> str:
    """本轮来源（只用于日志/审计；缺失时返回空串，判定不读它）。"""
    value = context.metadata.get(TURN_ORIGIN_KEY, "")
    return str(getattr(value, "value", value) or "")


def turn_origin_enum(context: ToolContext) -> TurnOrigin:
    """本轮来源的枚举形态；缺失/未知一律按 BACKGROUND（fail-closed）。"""
    raw = context.metadata.get(TURN_ORIGIN_KEY, "")
    try:
        return TurnOrigin(str(getattr(raw, "value", raw)))
    except ValueError:
        return TurnOrigin.BACKGROUND


#: Phase 5A：任务步骤的事实键。**不可伪造**：上下文里只放一个 bridge 自己签发的随机凭据，
#: 真正的事实（task_id / step_id / 已授权）只存在于 bridge 进程内的那张小表里 ——
#: 任何外部调用方自己拼一个 ToolContext 都拿不到有效的凭据。
TASK_TOKEN_KEY = "minecraft_task_token"


def task_facts(context: ToolContext, verifier: Any = None) -> tuple[str, str, bool]:
    """本轮是不是一个**已校验**的任务步骤（凭据无效/过期 → 全空，fail-closed）。"""
    token = str(context.metadata.get(TASK_TOKEN_KEY, "") or "")
    if not token or verifier is None:
        return ("", "", False)
    return verifier(token)


def minecraft_player(context: ToolContext) -> str:
    """本轮来自游戏内玩家时的 MC 用户名（空 = 不是来自游戏内的回合）。"""
    value = context.metadata.get(PLAYER_KEY, "")
    return str(getattr(value, "value", value) or "").strip()


def _confirmation_summary(tool: str, risk: str, arguments: Mapping[str, Any] | None) -> str:
    """给用户/界面看的一句话（不含机密、不含正文）。

    用户确认的是**具体动作**，所以摘要要说清"挖哪个方块"这种关键信息。
    """
    args = dict(arguments or {})
    if tool == "minecraft_dig":
        block = str(args.get("expected_block") or "方块")
        where = _format_position(args)
        tool_name = str(args.get("expected_tool") or "").strip()
        if tool_name:
            # §八：工具 / 方块 / 位置三样都要讲清楚（"用石镐挖铁矿"和"空手挖铁矿"
            # 是两次不同的操作，用户确认的必须是其中一次）
            head = f"使用 {tool_name} 挖掘 {block}"
        else:
            head = f"挖掉 {block}"
        return f"{head}（{where}）" if where else head
    if tool == "minecraft_pickup_item":
        # §十六：说明"捡的是地上的哪一个掉落物"，不写成"在 (x,y,z) 执行拾取"
        # （Item 会滑动/被推走，位置只是启动时的提示，不是授权身份本体）
        item = args.get("expected_item") or "掉落物"
        entity_id = args.get("entity_id")
        return f"拾取附近的 {item}（实体 #{entity_id}）"
    if tool == "minecraft_craft":
        # §十三/§十五：fingerprint 绑 recipe_id（+ 工作台坐标，见 arguments 本身），
        # 摘要必须把人类可读信息展开 —— 坐标也要写进去（换张工作台就是另一个操作）
        table = args.get("crafting_table")
        return recipe_summary(
            str(args.get("recipe_id") or ""), table if isinstance(table, Mapping) else None
        )
    if tool == "minecraft_container_transfer":
        # 摘要就是用户的授权文本：位置 + 第几格 + 什么物品 × 多少 + 另一个槽位，全部写清。
        # 容器类型在**执行时**才由 runtime 验证（本阶段只可能是 Chest / Barrel），
        # 所以这里不写死类型，只说"容器"。
        where = _format_position(args)
        container_slot = args.get("container_slot")
        inventory_slot = args.get("inventory_slot")
        item = args.get("item") or "物品"
        count = args.get("count")
        if str(args.get("direction") or "") == "deposit":
            return (
                f"把背包第 {inventory_slot} 格的 {item} ×{count} "
                f"放入 {where} 的容器第 {container_slot} 格"
            )
        return (
            f"从 {where} 的容器第 {container_slot} 格取 {item} ×{count} "
            f"到背包第 {inventory_slot} 格"
        )
    if tool == "minecraft_equip":
        return f"把 {args.get('item') or '物品'} 拿到手里"
    if tool == "minecraft_inventory_move":
        return (
            f"把 {args.get('source_slot')} 格的 {args.get('item') or '物品'} "
            f"×{args.get('count')} 移到 {args.get('destination_slot')} 格"
        )
    if tool == "minecraft_place":
        item = str(args.get("expected_item") or "方块")
        where = _format_position(args)
        face = str(args.get("face") or "")
        location = f"{where} 的 {face} 面" if where and face else (where or face)
        return f"放置 {item} 到 {location}" if location else f"放置 {item}"
    preview = _preview(args)
    return f"{tool}（{risk}）" + (f"：{preview}" if preview else "")


def bridge_from(context: ToolContext) -> MinecraftAgentBridge | None:
    bridge = context.metadata.get(BRIDGE_KEY)
    return bridge if isinstance(bridge, MinecraftAgentBridge) else None


def _stable_code(code: Any) -> str:
    text = str(code or "")
    if text.startswith("minecraft."):
        return text
    return RUNTIME_ERROR_CODES.get(text, "minecraft.action_failed")


def _failure(
    tool: str,
    code: str,
    message: str,
    *,
    extra: Mapping[str, Any] | None = None,
    detail: Mapping[str, Any] | None = None,
) -> ToolResult:
    error: dict[str, Any] = {"code": code, "message": message}
    if detail:
        # §十四：block.changed 这类失败要带上 expected/actual，模型才知道发生了什么
        error["detail"] = dict(detail)
    data: dict[str, Any] = {"ok": False, "error": error}
    data.update(extra or {})
    return ToolResult(
        tool_name=tool,
        success=False,
        error=message,
        error_type=code,
        data=data,
        metadata={"source_type": "minecraft", "confidence": 0.0},
    )


def _preview(arguments: Mapping[str, Any], limit: int = 120) -> str:
    parts = [f"{key}={value!r}" for key, value in list(arguments.items())[:6]]
    text = " ".join(parts)
    return text[:limit]


def _summarize(tool: str, data: Mapping[str, Any]) -> str:
    action = data.get("action")
    status = data.get("status")
    if tool == "minecraft_stop":
        cancelled = data.get("cancelled") or []
        if cancelled:
            return (
                f"已停止正在进行的 Minecraft 行动（{', '.join(str(item) for item in cancelled)}）。"
            )
        return "当前没有正在进行的 Minecraft 行动（无需停止）。"
    if tool == "minecraft_dropped_items":
        raw_dropped: Any = data.get("result")
        dropped: dict[str, Any] = raw_dropped if isinstance(raw_dropped, dict) else {}
        items = dropped.get("items") or []
        raw_total = dropped.get("total")
        drop_total = raw_total if isinstance(raw_total, int) else len(items)
        if not items:
            return "附近没有掉落物。"
        listed = "、".join(
            f"{row.get('item', {}).get('name')}×{row.get('item', {}).get('count')}"
            f"（实体 #{row.get('entity_id')}，{row.get('distance')} 格）"
            for row in items[:5]
        )
        more = f"，另有 {drop_total - 5} 个未列出" if drop_total > 5 else ""
        truncated = "（超过上限，只列了前 32 个）" if dropped.get("truncated") else ""
        return f"附近有 {drop_total} 个掉落物：{listed}{more}{truncated}。"
    if tool == "minecraft_find_blocks":
        raw_find: Any = data.get("result")
        found: dict[str, Any] = raw_find if isinstance(raw_find, dict) else {}
        raw_matches: Any = found.get("matches")
        matches: list[Any] = raw_matches if isinstance(raw_matches, list) else []
        if not matches:
            return "附近没有找到这些方块。"
        listed = "、".join(
            f"{((row.get('block') or {}) if isinstance(row, Mapping) else {}).get('name')}"
            f"（{_format_position(row.get('position'))}）"
            for row in matches[:5]
        )
        more = "……" if len(matches) > 5 else ""
        truncated = "（还有更多，先列最近的几条）" if found.get("truncated") else ""
        return f"附近找到 {len(matches)} 个：{listed}{more}{truncated}。"
    if tool == "minecraft_dig_capability":
        raw_capability: Any = data.get("result")
        capability: dict[str, Any] = raw_capability if isinstance(raw_capability, dict) else {}
        raw_block: Any = capability.get("block")
        if isinstance(raw_block, Mapping):
            block = str(raw_block.get("name") or "那个方块")
        else:
            block = "那个方块"
        raw_held: Any = capability.get("held_item")
        held_name = str((raw_held or {}).get("name") or "") if isinstance(raw_held, Mapping) else ""
        reason = capability.get("reason")
        if capability.get("can_dig"):
            dig_time = capability.get("dig_time_ms")
            took = f"，大约 {dig_time} 毫秒" if isinstance(dig_time, int) else ""
            who = f"用 {held_name}" if held_name else "空手"
            return f"{who}能挖 {block}{took}。"
        if reason == "air":
            return f"{block} 是空气，没什么可挖的。"
        if reason == "too_far":
            return f"{block} 太远了（超过挖掘距离上限），现在够不到。"
        if reason == "not_diggable":
            who = f"当前主手是 {held_name}" if held_name else "现在是空手"
            return f"{who}，挖不动 {block}。"
        return f"现在挖不了 {block}。"
    if tool == "minecraft_recipe_lookup":
        raw_lookup: Any = data.get("result")
        lookup: dict[str, Any] = raw_lookup if isinstance(raw_lookup, dict) else {}
        item = str(lookup.get("item") or "")
        status = str(lookup.get("status") or "")
        recipes = lookup.get("recipes") or []
        total = lookup.get("total")
        # Phase 4G：指定了工作台就把位置写进摘要（模型要能说清在哪张工作台上查的）
        where = _format_position(lookup.get("crafting_table"))
        at = f"在工作台 {where} 上" if where else ""
        if status == "recipe_not_found":
            return f"查不到 {item} 的配方（这个版本里没有，或者物品名不对）。"
        if status == "crafting_table_required":
            return (
                f"{item} 只有工作台配方（本阶段只支持玩家 2×2，或者给我一张明确的工作台坐标；"
                "不会自己去找工作台）。"
            )
        ready = [row for row in recipes if row.get("available")]
        if ready:
            first = ready[0]
            ingredients = "、".join(
                f"{row.get('name')}×{row.get('count')}" for row in first.get("ingredients") or []
            )
            more = f"，另外还有 {len(ready) - 1} 个也能做" if len(ready) > 1 else ""
            return (
                f"{item} {at}现有材料能做：用 {ingredients} 得到 "
                f"{first.get('result', {}).get('count_per_craft')} 个 {item}"
                f"（recipe_id={first.get('recipe_id')}）{more}。"
            )
        return (
            f"{item} {at}有 {total or len(recipes)} 个配方，但当前材料都不够"
            "（需要先准备材料，本阶段不会自动去做）。"
        )
    if tool == "minecraft_container_inspect":
        raw_result: Any = data.get("result")
        snapshot: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        raw_container: Any = snapshot.get("container")
        container: dict[str, Any] = raw_container if isinstance(raw_container, dict) else {}
        slots: list[Any] = snapshot.get("slots") or []
        label = _container_label(container.get("type"))
        where = _format_position(container.get("position"))
        head = f"看了 {where} 的 {label}" if where else f"看了 {label}"
        if not slots:
            return head + "：里面是空的。"
        items = "、".join(
            f"{row.get('name')}×{row.get('count')}（第 {row.get('slot')} 格）" for row in slots[:6]
        )
        more = f"，另有 {len(slots) - 6} 格" if len(slots) > 6 else ""
        return f"{head}：{items}{more}。"
    if status == "RUNNING":
        return f"{action} 已开始（action_id={data.get('action_id')}），完成与否会由事件告知。"
    where = _format_position((data.get("result") or {}).get("final_position"))
    if where:
        return f"{action} 已完成，到达 {where}。"
    return f"{action} 已完成（{status}）。"
