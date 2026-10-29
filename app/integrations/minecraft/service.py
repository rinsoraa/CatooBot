"""MinecraftService：CatooBot 与 Minecraft Bridge runtime 之间的稳定通信层（Phase 1）。

职责（全部是连接层职责，不含任何 Minecraft AI）：

* 托管 runtime 进程：spawn ``node runtime.js``、健康等待、崩溃检测与有界重启；
* Bridge 调用：join / leave / chat / status（校验与错误翻译在这里，路由层只翻译）；
* 事件通道：接收 runtime 回调（WebUI 的 token 门）→ 更新状态镜像 → 分发给订阅者；
* 对账轮询：回调丢失或 runtime 暴毙时靠轮询兜底，状态永不卡死。

状态机镜像 runtime 的显式状态（DISCONNECTED/…/ERROR），不用布尔拼凑。
"""

from __future__ import annotations

import asyncio
import math
import os
import secrets
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.config.settings import PROJECT_ROOT, MinecraftConfig
from app.integrations.minecraft.events import MinecraftBridgeEvent, parse_bridge_event
from app.integrations.minecraft.runtime_client import (
    RELEASE_NOT_DELIVERED,
    RELEASE_NOT_RELEASED,
    RELEASE_REJECTED,
    RELEASE_RELEASED,
    RELEASE_SKIPPED,
    RELEASE_TIMEOUT,
    RELEASE_UNKNOWN,
    MinecraftRuntimeClient,
    MinecraftRuntimeError,
    classify_release_failure,
)
from app.integrations.minecraft.world import TELEPORT_REBASE, WINDOW_SHIFT, WorldPerception
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.core.bot import Bot

log = get_logger("CatooBot.Minecraft")

Listener = Callable[[MinecraftBridgeEvent], Any]

#: 这些 4xx 拒绝码意味着"所有权其实还在我们手里，只是现在不能释放"
#: （runtime 用 409 controller.actions_pending 拦住"还有动作没结束"）。
#: 除它们以外的 4xx 都表示 runtime 已经不把我们当有效控制者 → 本地按 fenced 处理。
_STILL_OWNER_REJECTION_CODES = frozenset({"controller.actions_pending"})

#: 释放结果的合法取值。分类器若返回意料之外的东西，必须退化成 UNKNOWN 而不是成功：
#: 除 RELEASED（这条路径只有"runtime 明确回执"才能走）以外的任何值都不算已释放。
_RELEASE_OUTCOMES = frozenset(
    {
        RELEASE_RELEASED,
        RELEASE_REJECTED,
        RELEASE_TIMEOUT,
        RELEASE_UNKNOWN,
        RELEASE_NOT_DELIVERED,
        RELEASE_NOT_RELEASED,
        RELEASE_SKIPPED,
    }
)


class MinecraftBridgeError(Exception):
    """面向调用方（WebUI/QQ）的稳定错误；``code`` 对应契约 ``minecraft.*``。"""

    status = 400

    def __init__(
        self,
        message: str,
        *,
        code: str = "minecraft.error",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        #: 结构化补充（当前用于 dig 的 block.changed：expected/actual）
        self.detail: dict[str, Any] = dict(detail or {})


class MinecraftDisabled(MinecraftBridgeError):
    status = 503

    def __init__(self, message: str = "Minecraft 连接层未启用") -> None:
        super().__init__(message, code="minecraft.disabled")


class MinecraftRuntimeDown(MinecraftBridgeError):
    status = 503

    def __init__(self, message: str) -> None:
        super().__init__(message, code="minecraft.runtime_down")


class MinecraftRuntimeUnreachable(MinecraftRuntimeDown):
    """Runtime 连接在请求发出后断开：动作可能已接受，不能记为明确失败。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "minecraft.runtime_unreachable"


class MinecraftInvalidTarget(MinecraftBridgeError):
    status = 422

    def __init__(self, message: str = "服务器地址不合法") -> None:
        super().__init__(message, code="minecraft.invalid_target")


class MinecraftNotConnected(MinecraftBridgeError):
    status = 409

    def __init__(self, message: str = "罐头现在不在任何服务器里") -> None:
        super().__init__(message, code="minecraft.not_connected")


class MinecraftBusy(MinecraftBridgeError):
    status = 409

    def __init__(self, message: str = "已经有 bot 会话在运行") -> None:
        super().__init__(message, code="minecraft.session_active")


# ---- Phase 3B：Action Runtime 的稳定错误 ----------------


class MinecraftActionBusy(MinecraftBridgeError):
    """同一时间只允许一个前台动作；再提交 → 拒绝（不排队、不覆盖）。"""

    status = 409

    def __init__(self, message: str = "已有动作在执行") -> None:
        super().__init__(message, code="minecraft.action_busy")


class MinecraftActionInvalid(MinecraftBridgeError):
    """动作参数不合法 / 未知动作（本地校验或 runtime 校验）。"""

    status = 422

    def __init__(self, message: str = "动作参数不合法") -> None:
        super().__init__(message, code="minecraft.action_invalid")


class MinecraftActionFailed(MinecraftBridgeError):
    """动作已被受理但执行失败（失败原因在 message 里，不含内部堆栈）。"""

    status = 500

    def __init__(self, message: str = "动作执行失败") -> None:
        super().__init__(message, code="minecraft.action_failed")


class MinecraftPathNotFound(MinecraftBridgeError):
    """Phase 3C：非破坏性导航找不到路径（绝不自动挖/搭/绕圈）。"""

    status = 500

    def __init__(self, message: str = "无法找到到达目标的非破坏性路径") -> None:
        super().__init__(message, code="minecraft.path_not_found")


class MinecraftPathNotReached(MinecraftBridgeError):
    """Phase 4H.1：Pathfinder 结束了但罐头实际位置不在到达半径内（绝不假报成功）。"""

    status = 500

    def __init__(self, message: str = "导航结束了，但罐头不在目标附近") -> None:
        super().__init__(message, code="minecraft.path_not_reached")


class MinecraftPlayerNotFound(MinecraftBridgeError):
    """Phase 3D：跟随目标玩家不存在（不在线/不在视野内）——不启动 Pathfinder。"""

    status = 404

    def __init__(self, message: str = "找不到该玩家") -> None:
        super().__init__(message, code="minecraft.player_not_found")


class MinecraftPlayerLost(MinecraftBridgeError):
    """Phase 3D：跟随目标消失超过宽限期（持续动作失败，通常经事件上报）。"""

    status = 500

    def __init__(self, message: str = "跟随目标已消失") -> None:
        super().__init__(message, code="minecraft.player_lost")


class MinecraftFollowTargetTooFar(MinecraftBridgeError):
    """Phase 3D：目标超过最大追逐距离（不追到世界尽头）。"""

    status = 500

    def __init__(self, message: str = "跟随目标距离超过上限") -> None:
        super().__init__(message, code="minecraft.follow_target_too_far")


#: runtime 业务错误码 → 稳定错误（action.* 必须先于通用 409 判断）
class MinecraftBlockNotFound(MinecraftBridgeError):
    """目标位置没有可破坏的方块（空气也算没有）。"""

    status = 404

    def __init__(self, message: str = "目标位置没有方块") -> None:
        super().__init__(message, code="minecraft.block_not_found")


class MinecraftBlockChanged(MinecraftBridgeError):
    """方块与用户确认时不一致：确认的是「这个位置的这个方块」，不是「这里现在的东西」。"""

    status = 409

    def __init__(
        self,
        message: str = "方块已经变了",
        *,
        expected: str = "",
        actual: str = "",
    ) -> None:
        super().__init__(
            message,
            code="minecraft.block_changed",
            detail={"expected": expected, "actual": actual},
        )


class MinecraftBlockNotDiggable(MinecraftBridgeError):
    """当前状态下挖不动（工具不对/被保护）——不换工具、不走近、不找角度。"""

    status = 400

    def __init__(self, message: str = "当前状态下挖不动这个方块") -> None:
        super().__init__(message, code="minecraft.block_not_diggable")


class MinecraftBlockTooFar(MinecraftBridgeError):
    """目标超出挖掘距离——本阶段不会自己走过去。"""

    status = 400

    def __init__(self, message: str = "目标方块太远") -> None:
        super().__init__(message, code="minecraft.block_too_far")


class MinecraftHeldItemMissing(MinecraftBridgeError):
    """主手没有拿任何（或数量为 0）物品——本阶段不会自动装备/切 hotbar。"""

    status = 400

    def __init__(self, message: str = "主手没有拿着东西") -> None:
        super().__init__(message, code="minecraft.held_item_missing")


class MinecraftHeldItemChanged(MinecraftBridgeError):
    """主手拿的不是用户确认的那个物品（expected_item 是硬约束）。"""

    status = 409

    def __init__(
        self, message: str = "主手物品与确认的不一致", *, expected: str = "", actual: str = ""
    ) -> None:
        super().__init__(
            message,
            code="minecraft.held_item_changed",
            detail={"expected": expected, "actual": actual},
        )


class MinecraftTargetOccupied(MinecraftBridgeError):
    """目标位置已经有方块——第一版只往空气里放（不碰 replaceable 语义）。"""

    status = 409

    def __init__(self, message: str = "目标位置已经有方块", *, actual: str = "") -> None:
        super().__init__(message, code="minecraft.target_occupied", detail={"actual": actual})


class MinecraftReferenceBlockMissing(MinecraftBridgeError):
    """参考方块（target − face 方向那一格）不存在/是空气：没有可依附的面。"""

    status = 404

    def __init__(self, message: str = "参考方块位置没有方块") -> None:
        super().__init__(message, code="minecraft.reference_block_missing")


class MinecraftBlockNameUnknown(MinecraftBridgeError):
    """Phase 4K：方块名在当前 Minecraft registry 里解析不出来（422，不是"附近没有"）。"""

    status = 422

    def __init__(
        self, message: str = "不认识的方块名", *, unknown: list[str] | None = None
    ) -> None:
        super().__init__(
            message,
            code="minecraft.block_name_unknown",
            detail={"unknown": unknown or []},
        )


class MinecraftBlockQueryUnavailable(MinecraftBridgeError):
    """Phase 4K：运行时给不出方块查询能力（绝不假装"附近没有"）。"""

    status = 500

    def __init__(self, message: str = "当前运行时无法查询方块位置") -> None:
        super().__init__(message, code="minecraft.block_query_unavailable")


class MinecraftBlockUnavailable(MinecraftBridgeError):
    """目标区域没有加载（blockAt 返回 null）——不猜、不放置。"""

    status = 404

    def __init__(self, message: str = "目标区域没有加载") -> None:
        super().__init__(message, code="minecraft.block_unavailable")


class MinecraftItemNotFound(MinecraftBridgeError):
    """背包里没有这个物品（或者 source 槽位是空的）。"""

    status = 404

    def __init__(self, message: str = "背包里没有这个物品") -> None:
        super().__init__(message, code="minecraft.item_not_found")


class MinecraftItemChanged(MinecraftBridgeError):
    """槽位上的物品与请求的不一致（带 expected/actual）。"""

    status = 409

    def __init__(
        self, message: str = "槽位上的物品与请求的不一致", *, expected: str = "", actual: str = ""
    ) -> None:
        super().__init__(
            message, code="minecraft.item_changed", detail={"expected": expected, "actual": actual}
        )


class MinecraftItemCountInsufficient(MinecraftBridgeError):
    """source 槽位上的数量不够。"""

    status = 409

    def __init__(
        self,
        message: str = "数量不够",
        *,
        available: int | None = None,
        requested: int | None = None,
    ) -> None:
        detail: dict[str, Any] = {}
        if available is not None:
            detail["available"] = available
        if requested is not None:
            detail["requested"] = requested
        super().__init__(message, code="minecraft.item_count_insufficient", detail=detail)


class MinecraftDestinationOccupied(MinecraftBridgeError):
    """目标槽位被别的物品占用（绝不隐式交换），或同名堆已满。"""

    status = 409

    def __init__(self, message: str = "目标槽位被占用", *, actual: str = "") -> None:
        super().__init__(
            message,
            code="minecraft.destination_occupied",
            detail={"actual": actual} if actual else {},
        )


class MinecraftSlotInvalid(MinecraftBridgeError):
    """槽位编号非法（必须是主背包 + 快捷栏 9..44 的整数）。"""

    status = 422

    def __init__(self, message: str = "槽位编号非法") -> None:
        super().__init__(message, code="minecraft.slot_invalid")


class MinecraftEquipUnconfirmed(MinecraftBridgeError):
    """equip resolve 了但主手不是预期物品：不能报成功。"""

    status = 500

    def __init__(
        self, message: str = "未能确认装备结果", *, expected: str = "", actual: str = ""
    ) -> None:
        super().__init__(
            message,
            code="minecraft.equip_unconfirmed",
            detail={"expected": expected, "actual": actual},
        )


class MinecraftMoveUnconfirmed(MinecraftBridgeError):
    """transfer resolve 了但 source/destination 的真实状态不符合预期。"""

    status = 500

    def __init__(
        self, message: str = "未能确认移动结果", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.move_unconfirmed", detail=detail or {})


class MinecraftContainerUnsupported(MinecraftBridgeError):
    """不是本阶段支持的单方块容器（非 chest/barrel 方块、双箱、窗口结构不符）。"""

    status = 422

    def __init__(self, message: str = "只支持单方块 Chest / Barrel", *, block: str = "") -> None:
        super().__init__(
            message,
            code="minecraft.container_unsupported",
            detail={"block": block} if block else None,
        )


class MinecraftContainerTooFar(MinecraftBridgeError):
    """容器离得太远（本阶段绝不自动走过去）。"""

    status = 422

    def __init__(self, message: str = "容器太远", *, distance: float | None = None) -> None:
        super().__init__(
            message,
            code="minecraft.container_too_far",
            detail={"distance": distance} if distance is not None else None,
        )


class MinecraftContainerOpenFailed(MinecraftBridgeError):
    """打开容器失败（服务器没给窗口 / 方块其实不是容器）。"""

    status = 500

    def __init__(self, message: str = "打开容器失败") -> None:
        super().__init__(message, code="minecraft.container_open_failed")


class MinecraftContainerClosed(MinecraftBridgeError):
    """容器窗口在动作过程中被关掉（玩家/服务器关掉，或方块被破坏）。"""

    status = 409

    def __init__(self, message: str = "容器窗口已经关闭") -> None:
        super().__init__(message, code="minecraft.container_closed")


class MinecraftContainerCloseFailed(MinecraftBridgeError):
    """动作做完了但窗口关不上：世界状态可能已经变了，必须如实上报（绝不吞掉）。"""

    status = 500

    def __init__(
        self, message: str = "关闭容器失败", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.container_close_failed", detail=detail or {})


class MinecraftContainerTransferUnconfirmed(MinecraftBridgeError):
    """transfer resolve 了但重读的真实状态不符（不硬编码 ±count）。"""

    status = 500

    def __init__(
        self, message: str = "未能确认搬运结果", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            message, code="minecraft.container_transfer_unconfirmed", detail=detail or {}
        )


class MinecraftRecipeNotFound(MinecraftBridgeError):
    """找不到这个配方（物品没有配方 / recipe_id 对不上任何配方）。"""

    status = 404

    def __init__(self, message: str = "找不到这个配方") -> None:
        super().__init__(message, code="minecraft.recipe_not_found")


class MinecraftRecipeUnavailable(MinecraftBridgeError):
    """配方存在但现在做不了（例如需要工作台——本阶段只支持玩家 2×2）。"""

    status = 409

    def __init__(self, message: str = "这个配方现在做不了") -> None:
        super().__init__(message, code="minecraft.recipe_unavailable")


class MinecraftMaterialInsufficient(MinecraftBridgeError):
    """材料不够（本阶段绝不自动准备材料）。"""

    status = 409

    def __init__(self, message: str = "材料不够", *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="minecraft.material_insufficient", detail=detail or {})


class MinecraftRecipeChanged(MinecraftBridgeError):
    """确认时的配方与现在的对不上（材料/形状变了）。"""

    status = 409

    def __init__(self, message: str = "这个配方已经变了") -> None:
        super().__init__(message, code="minecraft.recipe_changed")


class MinecraftItemEntityNotFound(MinecraftBridgeError):
    """附近找不到这个掉落物实体（已经被捡走、消失，或区块没加载）。"""

    status = 404

    def __init__(self, message: str = "附近找不到这个掉落物实体") -> None:
        super().__init__(message, code="minecraft.item_entity_not_found")


class MinecraftItemEntityInvalid(MinecraftBridgeError):
    """那个实体不是掉落物（玩家/怪物/投射物…），或者读不到它身上的物品。"""

    status = 422

    def __init__(
        self, message: str = "那个实体不是掉落物", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.item_entity_invalid", detail=detail or {})


class MinecraftItemEntityChanged(MinecraftBridgeError):
    """实体身上的物品与 expected_item 不一致（第二层身份校验失败）。"""

    status = 409

    def __init__(
        self, message: str = "实体身上的物品与预期不一致", *, expected: str = "", actual: str = ""
    ) -> None:
        super().__init__(
            message,
            code="minecraft.item_entity_changed",
            detail={"expected": expected, "actual": actual},
        )


class MinecraftPickupTargetReplaced(MinecraftBridgeError):
    """entity_id 还在，但已经指向了**另一个**实体对象（绝不自动改绑）。"""

    status = 409

    def __init__(
        self, message: str = "目标实体已经被替换", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.pickup_target_replaced", detail=detail or {})


class MinecraftPickupTargetLost(MinecraftBridgeError):
    """目标被别的玩家捡走 / 消失，追不回来了。"""

    status = 409

    def __init__(
        self, message: str = "目标掉落物已经丢了", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.pickup_target_lost", detail=detail or {})


class MinecraftPickupTargetTooFar(MinecraftBridgeError):
    """掉落物离得太远（本阶段不会追太远）。"""

    status = 422

    def __init__(
        self, message: str = "掉落物太远", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.pickup_target_too_far", detail=detail or {})


class MinecraftPickupFailed(MinecraftBridgeError):
    """拾取动作本身失败（启动导航失败等）。"""

    status = 500

    def __init__(self, message: str = "拾取失败") -> None:
        super().__init__(message, code="minecraft.pickup_failed")


class MinecraftPickupUnconfirmed(MinecraftBridgeError):
    """实体消失了，但背包里的物品没有增加 —— 不能算成功。"""

    status = 500

    def __init__(
        self, message: str = "未能确认拾取结果", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.pickup_unconfirmed", detail=detail or {})


class MinecraftCraftingTableMissing(MinecraftBridgeError):
    """指定的那个位置没有工作台（区块没加载，或者被挖掉了）。"""

    status = 404

    def __init__(self, message: str = "那里没有工作台") -> None:
        super().__init__(message, code="minecraft.crafting_table_missing")


class MinecraftCraftingTableInvalid(MinecraftBridgeError):
    """那个位置不是 crafting_table（本阶段只认这一种工作台）。"""

    status = 422

    def __init__(
        self, message: str = "那个位置不是工作台", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.crafting_table_invalid", detail=detail or {})


class MinecraftCraftingTableTooFar(MinecraftBridgeError):
    """工作台离得太远（本阶段绝不自动走过去）。"""

    status = 422

    def __init__(
        self, message: str = "工作台太远", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.crafting_table_too_far", detail=detail or {})


class MinecraftCraftFailed(MinecraftBridgeError):
    """底层 bot.craft 失败。"""

    status = 500

    def __init__(self, message: str = "合成失败") -> None:
        super().__init__(message, code="minecraft.craft_failed")


class MinecraftCraftUnconfirmed(MinecraftBridgeError):
    """craft resolve 了但重读 inventory 对不上（产物没增加 / 材料没减少）。"""

    status = 500

    def __init__(
        self, message: str = "未能确认合成结果", *, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, code="minecraft.craft_unconfirmed", detail=detail or {})


class MinecraftBlockPlaceUnconfirmed(MinecraftBridgeError):
    """placeBlock resolve 了但世界里没出现预期方块：客户端/服务器不同步，不能报成功。"""

    status = 500

    def __init__(
        self,
        message: str = "未能确认方块已被放置",
        *,
        expected: str = "",
        actual: str = "",
    ) -> None:
        super().__init__(
            message,
            code="minecraft.block_place_unconfirmed",
            detail={"expected": expected, "actual": actual},
        )


class MinecraftBlockBreakUnconfirmed(MinecraftBridgeError):
    """dig 结束后方块仍在原位：客户端状态与服务器不同步，不能报成功。"""

    status = 500

    def __init__(self, message: str = "未能确认方块已被破坏") -> None:
        super().__init__(message, code="minecraft.block_break_unconfirmed")


class MinecraftAttributionError(MinecraftBridgeError):
    """Strict evidence unavailable or unconfirmed; never described as action busy."""

    def __init__(self, message: str, *, unavailable: bool = False) -> None:
        self.status = 503 if unavailable else 409
        super().__init__(
            message,
            code="minecraft.attribution_unavailable"
            if unavailable
            else "minecraft.attribution_unconfirmed",
        )


def canonical_block_name(name: str) -> str:
    """把方块名规范成裸名（Phase 4K §四）。

    ``iron_ore`` / ``minecraft:iron_ore`` / ``IRON_ORE`` 是同一个方块；runtime 的语义投影
    一贯用裸名（``block_before: "stone"``），所以这里也统一成裸名（去命名空间 + 小写）。
    只做规范化，不猜、不做近似匹配。
    """
    return str(name or "").strip().lower().replace("minecraft:", "", 1)


def canonical_item_name(name: str) -> str:
    """把物品名规范成 ``minecraft:xxx``（Phase 4I §六）。

    ``stone_pickaxe`` 与 ``minecraft:stone_pickaxe`` 是**同一把工具**：确认指纹绑的是参数本身，
    所以必须在**进确认门之前**就统一，否则模型换个写法就会把已挂起的确认变成 mismatch。
    只做命名空间规范化 —— 不猜、不做近似匹配。
    """
    # Minecraft 的物品 id 永远是小写：整体小写化让 "STONE_PICKAXE" 也是同一把工具
    clean = str(name or "").strip().lower()
    if not clean:
        return ""
    if ":" not in clean:
        return f"minecraft:{clean}"
    namespace, _, path = clean.partition(":")
    if not namespace.strip() or not path.strip():
        return clean
    return f"{namespace.strip().lower()}:{path.strip()}"


def _translate(exc: MinecraftRuntimeError) -> MinecraftBridgeError:
    if exc.unreachable:
        return MinecraftRuntimeDown(str(exc))
    if exc.code == "action.busy":
        return MinecraftActionBusy(str(exc))
    if exc.code in {"action.invalid", "action.unknown"}:
        return MinecraftActionInvalid(str(exc))
    if exc.code == "action.not_online":
        return MinecraftNotConnected(str(exc))
    if exc.code == "action.failed":
        return MinecraftActionFailed(str(exc))
    if exc.code == "path.not_found":
        return MinecraftPathNotFound(str(exc))
    if exc.code == "path.not_reached":
        return MinecraftPathNotReached(str(exc))
    if exc.code == "block.not_found":
        return MinecraftBlockNotFound(str(exc))
    if exc.code == "block.changed":
        return MinecraftBlockChanged(
            str(exc),
            expected=str(exc.detail.get("expected") or ""),
            actual=str(exc.detail.get("actual") or ""),
        )
    if exc.code == "block.not_diggable":
        return MinecraftBlockNotDiggable(str(exc))
    if exc.code == "block.too_far":
        return MinecraftBlockTooFar(str(exc))
    if exc.code == "block.break_unconfirmed":
        return MinecraftBlockBreakUnconfirmed(str(exc))
    if exc.code in {"block.attribution_unavailable", "block.attribution_unconfirmed"}:
        return MinecraftAttributionError(str(exc), unavailable=exc.code.endswith("unavailable"))
    # Phase 4C：place 的目标/手持物品校验（§九-§十二）
    if exc.code == "held.item_missing":
        return MinecraftHeldItemMissing(str(exc))
    if exc.code == "held.item_changed":
        return MinecraftHeldItemChanged(
            str(exc),
            expected=str(exc.detail.get("expected") or ""),
            actual=str(exc.detail.get("actual") or ""),
        )
    if exc.code == "target.occupied":
        return MinecraftTargetOccupied(str(exc), actual=str(exc.detail.get("actual") or ""))
    if exc.code == "reference.missing":
        return MinecraftReferenceBlockMissing(str(exc))
    if exc.code == "block.unavailable":
        return MinecraftBlockUnavailable(str(exc))
    if exc.code == "block.name_unknown":
        raw_unknown = exc.detail.get("unknown")
        unknown = [str(item) for item in raw_unknown] if isinstance(raw_unknown, list) else []
        return MinecraftBlockNameUnknown(str(exc), unknown=unknown)
    if exc.code == "block.query_unavailable":
        return MinecraftBlockQueryUnavailable(str(exc))
    if exc.code == "block.place_unconfirmed":
        return MinecraftBlockPlaceUnconfirmed(
            str(exc),
            expected=str(exc.detail.get("expected") or ""),
            actual=str(exc.detail.get("actual") or ""),
        )
    if exc.code == "face.invalid":
        return MinecraftActionInvalid(str(exc))
    if exc.code == "item.invalid":
        return MinecraftActionInvalid(str(exc))
    # Phase 4D：背包写操作（equip / inventory_move）
    if exc.code == "item.not_found":
        return MinecraftItemNotFound(str(exc))
    if exc.code == "item.changed":
        return MinecraftItemChanged(
            str(exc),
            expected=str(exc.detail.get("expected") or ""),
            actual=str(exc.detail.get("actual") or ""),
        )
    if exc.code == "item.count_insufficient":
        return MinecraftItemCountInsufficient(
            str(exc),
            available=exc.detail.get("available"),
            requested=exc.detail.get("requested"),
        )
    if exc.code == "destination.occupied":
        return MinecraftDestinationOccupied(str(exc), actual=str(exc.detail.get("actual") or ""))
    if exc.code == "slot.invalid":
        return MinecraftSlotInvalid(str(exc))
    if exc.code == "equip.unconfirmed":
        return MinecraftEquipUnconfirmed(
            str(exc),
            expected=str(exc.detail.get("expected") or ""),
            actual=str(exc.detail.get("actual") or ""),
        )
    if exc.code == "move.unconfirmed":
        return MinecraftMoveUnconfirmed(str(exc), detail=dict(exc.detail))
    # Phase 4E：container（Chest / Barrel 单方块读取与单项存取）
    if exc.code == "container.unsupported":
        return MinecraftContainerUnsupported(str(exc), block=str(exc.detail.get("block") or ""))
    if exc.code == "container.too_far":
        return MinecraftContainerTooFar(str(exc), distance=exc.detail.get("distance"))
    if exc.code == "container.open_failed":
        return MinecraftContainerOpenFailed(str(exc))
    if exc.code == "container.closed":
        return MinecraftContainerClosed(str(exc))
    if exc.code == "container.close_failed":
        return MinecraftContainerCloseFailed(str(exc), detail=dict(exc.detail))
    if exc.code == "container.transfer_unconfirmed":
        return MinecraftContainerTransferUnconfirmed(str(exc), detail=dict(exc.detail))
    # Phase 4F：crafting（玩家 2×2）
    if exc.code == "recipe.not_found":
        return MinecraftRecipeNotFound(str(exc))
    # Phase 4H：掉落物 / 拾取
    if exc.code == "item_entity.not_found":
        return MinecraftItemEntityNotFound(str(exc))
    if exc.code == "item_entity.invalid":
        return MinecraftItemEntityInvalid(str(exc), detail=dict(exc.detail))
    if exc.code == "item_entity.changed":
        return MinecraftItemEntityChanged(
            str(exc),
            expected=str(exc.detail.get("expected") or ""),
            actual=str(exc.detail.get("actual") or ""),
        )
    if exc.code == "target.replaced":
        return MinecraftPickupTargetReplaced(str(exc), detail=dict(exc.detail))
    if exc.code == "pickup.target_lost":
        return MinecraftPickupTargetLost(str(exc), detail=dict(exc.detail))
    if exc.code == "pickup.target_too_far":
        return MinecraftPickupTargetTooFar(str(exc), detail=dict(exc.detail))
    if exc.code == "pickup.failed":
        return MinecraftPickupFailed(str(exc))
    if exc.code == "pickup.unconfirmed":
        return MinecraftPickupUnconfirmed(str(exc), detail=dict(exc.detail))
    if exc.code == "table.missing":
        return MinecraftCraftingTableMissing(str(exc))
    if exc.code == "table.invalid":
        return MinecraftCraftingTableInvalid(str(exc), detail=dict(exc.detail))
    if exc.code == "table.too_far":
        return MinecraftCraftingTableTooFar(str(exc), detail=dict(exc.detail))
    if exc.code == "recipe.invalid":
        return MinecraftActionInvalid(str(exc))
    if exc.code == "recipe.unavailable":
        return MinecraftRecipeUnavailable(str(exc))
    if exc.code == "recipe.changed":
        return MinecraftRecipeChanged(str(exc))
    if exc.code == "material.insufficient":
        return MinecraftMaterialInsufficient(str(exc), detail=dict(exc.detail))
    if exc.code == "craft.failed":
        return MinecraftCraftFailed(str(exc))
    if exc.code == "craft.unconfirmed":
        return MinecraftCraftUnconfirmed(str(exc), detail=dict(exc.detail))
    if exc.code == "player.not_found":
        return MinecraftPlayerNotFound(str(exc))
    # player.lost / follow.target_too_far 发生在持续动作的后台阶段，正常经事件上报；
    # 这里保留映射，任何同步路径出现它们时也能得到稳定错误码。
    if exc.code == "player.lost":
        return MinecraftPlayerLost(str(exc))
    if exc.code == "follow.target_too_far":
        return MinecraftFollowTargetTooFar(str(exc))
    if exc.status == 409:
        return MinecraftBusy(str(exc))
    if exc.code in {"target.invalid", "chat.empty", "chat.too_long"}:
        return MinecraftInvalidTarget(str(exc))
    if exc.code == "chat.not_online":
        return MinecraftNotConnected(str(exc))
    return MinecraftBridgeError(str(exc), code=f"minecraft.{exc.code}")


#: action 事件 → 镜像里的动作状态
_ACTION_EVENT_STATUSES = {
    "minecraft.action.started": "RUNNING",
    "minecraft.action.completed": "SUCCEEDED",
    "minecraft.action.failed": "FAILED",
    "minecraft.action.cancelled": "CANCELLED",
    "minecraft.action.timeout": "TIMEOUT",
}

#: 空闲动作视图（WebUI「Current Action」用）
ACTION_IDLE: dict[str, Any] = {
    "action": None,
    "action_id": None,
    "status": "IDLE",
    "started_at": None,
    "finished_at": None,
    "elapsed_ms": None,
}

#: 空闲 Pathfinder 诊断（Phase 3C/3D；未启用/无 bot 时）
PATHFINDER_IDLE: dict[str, Any] = {
    "goal": None,
    "target": None,
    "distance": None,
    "moving": False,
}

#: follow_player 的默认与边界（与 runtime 同规则）
FOLLOW_DEFAULT_DISTANCE = 2.5
FOLLOW_MIN_DISTANCE = 1.5
FOLLOW_MAX_DISTANCE = 6.0
FOLLOW_MAX_USERNAME_CHARS = 16

#: Phase 4K：find_blocks 的硬上限（与 runtime 常量一致；LLM 传得再大也越不过它）
FIND_BLOCKS_MAX_NAMES = 8
FIND_BLOCKS_HARD_MAX_DISTANCE = 32
FIND_BLOCKS_HARD_MAX_RESULTS = 16

#: dig 的 expected_block 长度上限（minecraft:xxx 之类）
DIG_MAX_BLOCK_CHARS = 64

#: Phase 4D：mineflayer 玩家窗口可操作的槽位范围（主背包 9-35 + 快捷栏 36-44）
PLAYER_SLOT_MIN = 9
PLAYER_SLOT_MAX = 44

#: Phase 4F：recipe_id 的长度上限（可读规范签名）
CRAFT_MAX_RECIPE_ID_CHARS = 200

#: Phase 4E：容器动作的槽位 / 方向词表（与 runtime 同一份语义）
CONTAINER_DIRECTIONS: tuple[str, ...] = ("withdraw", "deposit")
#: 单方块容器的容器侧槽位数（runtime 从真实窗口结构推导后必须等于它；双箱是 54）
CONTAINER_SLOT_COUNT = 27

#: Phase 4C：place 只允许这六个方向（与 runtime 同一张表，绝不接受任意向量）
PLACE_FACES: tuple[str, ...] = ("up", "down", "north", "south", "east", "west")
#: place 的 expected_item 长度上限
PLACE_MAX_ITEM_CHARS = 64


class _RuntimeProcess:
    """一个被托管的 ``node runtime.js`` 子进程（含日志泵）。"""

    def __init__(self, runtime_dir: Path, node: str, env: dict[str, str]) -> None:
        self._runtime_dir = runtime_dir
        self._node = node
        self._env = env
        self._process: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task[None] | None = None
        self.log_tail: deque[str] = deque(maxlen=200)

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        if self.running:
            return
        self._process = await asyncio.create_subprocess_exec(
            self._node,
            "runtime.js",
            cwd=str(self._runtime_dir),
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._pump = asyncio.create_task(self._pump_logs())

    async def _pump_logs(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                self.log_tail.append(text)
                level, message = _parse_runtime_log(text)
                log.log(level, "[runtime] %s", message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 日志泵不能杀死服务
            log.exception("[Minecraft] runtime 日志泵异常")

    async def stop(self, timeout: float = 5.0) -> None:
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=timeout)
            except TimeoutError:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
                await self._process.wait()
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except asyncio.CancelledError:
                pass
            self._pump = None
        self._process = None


def _parse_runtime_log(line: str) -> tuple[int, str]:
    """runtime 输出 JSON 行；解析失败按原样 INFO 输出。"""
    import json

    try:
        payload = json.loads(line)
        if isinstance(payload, dict):
            level = {"debug": 10, "info": 20, "warn": 30, "error": 40}.get(
                str(payload.get("level")), 20
            )
            return level, str(payload.get("message") or line)
    except ValueError:
        pass
    return 20, line


class MinecraftService:
    """由 ``Bot`` 装配的长驻服务；WebUI 与 QQ 插件都经由它操作 Minecraft。"""

    def __init__(
        self,
        bot: Bot,
        config: MinecraftConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.config = config
        self._clock = clock
        # Phase 7F.5：**正向**调用凭据（Python → Node）。每进程生成、经进程环境注入 Node；
        # 它只做「调用者身份」校验，**绝不**替代 TaskAuthorization / TaskStepAuthorization /
        # 用户确认门，也不是幂等键。
        self.bridge_token: str | None = config.external_bridge_token or None
        #: 当前 Runtime 实例标识（只来自**成功的**身份响应，绝不从失败里推断）。
        self._runtime_instance: str = ""
        #: 实例变化回调（由 Bot 装配到 TaskRuntime.on_runtime_instance_changed）。
        self.on_runtime_instance_changed: Any = None
        self._client = MinecraftRuntimeClient(
            f"http://127.0.0.1:{config.runtime_port}",
            timeout=config.request_timeout_seconds,
            token_getter=lambda: self.bridge_token or "",
        )
        self._process: _RuntimeProcess | None = None
        #: Node 单控制者所有权：本 Service 即当前控制者；凭据只在客户端内存中。
        self._controller_id = f"ctrl_{uuid.uuid4().hex[:16]}"
        self._controller_task: asyncio.Task[None] | None = None
        self._controller_state = "UNKNOWN"
        self._controller_initial_delay = 0.0
        self._shutting_down = False
        #: P1-F1：控制者释放的结算结果（四态 + 证据）。默认 SKIPPED＝从未释放过。
        self.controller_release: dict[str, Any] = {
            "outcome": RELEASE_SKIPPED,
            "released": False,
            "attempted": False,
            "code": "",
            "status": 0,
            "budget": 0.0,
            "elapsed": 0.0,
            "owner_state": "",
        }
        #: 一次生命周期里释放只结算一次：重复 stop 不会重复发释放请求。
        self._release_settled = False
        #: 首个释放结算完成后置位：并发第二调用者等它，而不是读到占位 SKIPPED。
        self._release_done = asyncio.Event()
        #: 收尾只跑一次（并发/重复 stop 都收敛到同一份结果）。
        self._cleanup_lock = asyncio.Lock()
        self._cleanup_done = False
        #: 收尾进度：idle / in_progress / complete / cancelled / failed。
        #: 取消或失败时保持非 complete，重试 stop() 才能补做被中断的尾部清理。
        self.cleanup_state = "idle"
        self._listeners: list[Listener] = []
        self._poll_task: asyncio.Task[None] | None = None
        self._perception_task: asyncio.Task[None] | None = None
        self.perception: WorldPerception | None = None
        self._started = False
        self._restart_count = 0
        self._runtime_down = False
        # 状态镜像：来自事件回调 + 对账轮询，绝不自行发明状态
        self._mirror: dict[str, Any] = {"status": "DISCONNECTED"}
        # P0-FU1：玩家连接代次（由服务端观察到的 player_joined/player_left 事件生成；
        # 不是 Bot 自己的 connection.connected_at）。
        self._player_session_history: dict[tuple[str, str, str, str], list[tuple[float, str]]] = {}
        self._player_session_current: dict[tuple[str, str, str, str], str] = {}
        self._player_session_uuid: dict[tuple[str, str, str, str], str] = {}
        self._player_session_runtime: dict[tuple[str, str, str, str], str] = {}
        self._player_session_seen: dict[tuple[str, str, str, str], float] = {}
        self._player_session_clock = time.monotonic
        self.session_max_age_seconds = 300.0
        self.attestation_client: Any = None
        self.attestation_required = False
        self._attested: dict[str, dict[str, Any]] = {}
        self._attest_generation: dict[str, int] = {}
        self._player_session_names: dict[tuple[str, str, str, str], str] = {}
        self._attest_refresh_task: Any = None
        self._attest_refresh_interval = 120.0
        self.on_attested_identity_revoked: Any = None
        self.auto_attest_on_join = False
        self._attest_bootstrap_tasks: dict[str, Any] = {}
        self._attest_bootstrap_timeout = 5.0
        self._attest_proof_max_age = 300.0
        #: Phase 3E：Agent Bridge（由 Bot 装配；持有它 = 六个 LLM Tool 的判定与上下文）
        self.agent: Any = None
        #: Phase 4A：游戏内聊天桥（由 Bot 装配；把 minecraft.chat 变成 USER 回合）
        self.chat_bridge: Any = None
        self._last_event: dict[str, Any] | None = None
        self._recent_event_keys: deque[str] = deque(maxlen=256)
        # 回调共享密钥：auto_start 时随进程环境注入；外部托管时用配置值
        self.callback_token: str | None = config.external_callback_token or None
        self.callback_url: str = config.external_callback_url

    # --------------------------------------------------------------- listeners

    def add_listener(self, listener: Listener) -> None:
        """订阅 Bridge 事件（QQ 插件由此把 chat 送进消息处理链）。"""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    # -------------------------------------------------------------- lifecycle

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def runtime_managed(self) -> bool:
        return self._process is not None

    @property
    def auth_configured(self) -> bool:
        """本地 auth.json 是否存在（只看存在性，绝不读内容）。"""
        return (self._runtime_dir() / "auth.json").exists()

    def _runtime_dir(self) -> Path:
        """runtime 目录；相对路径以项目根为基准（与 bible_path 等约定一致）。

        必须解析成绝对路径再传给子进程：Node 进程的 CWD 就在 runtime 目录里，
        相对路径会在它那边被二次拼接（auth.json 曾因此永远读不到，
        静默回退成默认名字）。
        """
        path = Path(self.config.runtime_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    async def start(self) -> None:
        """Bot.start 时调用：拉起 runtime（可选）+ 对账轮询。失败会清理自身。"""
        if not self.config.enabled or self._started:
            return
        # 重新进入一个生命周期：收尾与释放结算都允许再发生一次。
        self._cleanup_done = False
        self._release_settled = False
        self._release_done = asyncio.Event()
        self.cleanup_state = "idle"
        if not self.callback_url:
            self.callback_url = self._default_callback_url()
        try:
            if self.config.auto_start_runtime:
                await self._start_runtime()
            else:
                log.info("[Minecraft] runtime 由外部托管（auto_start_runtime=false）")
            if not self.config.auto_start_runtime:
                # External runtime: give the process owner a short grace window to claim
                # first (Gate harness/admin), then retry our claim if it was released.
                self._controller_state = "UNCLAIMED"
                self._controller_initial_delay = 1.5
            else:
                await self._claim_controller_with_retry()
            self._controller_task = asyncio.create_task(self._controller_renew_loop())
            if self.config.perception_enabled:
                self.perception = WorldPerception(
                    client=self._client,
                    clock=self._clock,
                    near_interval=self.config.near_interval_seconds,
                    local_interval=self.config.local_interval_seconds,
                    extended_interval=self.config.extended_interval_seconds,
                    event_cooldown=self.config.world_event_cooldown_seconds,
                    change_block_threshold=self.config.world_change_block_threshold,
                    dispatch=self._dispatch_world_event,
                )
                self._perception_task = asyncio.create_task(self._perception_loop())
            self._started = True
            if self.agent is not None:
                # Phase 3E：Agent 上下文跟着 action 事件走（绝不触发新的 Agent Turn）
                self.add_listener(self.agent.apply_event)
            if self.chat_bridge is not None:
                # Phase 4A：游戏内聊天 → USER 回合（与沙盒外部事件链并存，互不替代）
                self.add_listener(self.chat_bridge.apply_event)
            self._poll_task = asyncio.create_task(self._poll_loop())
            log.info(
                "[Minecraft] bridge ready (runtime_port=%d, callback=%s, auth=%s, perception=%s)",
                self.config.runtime_port,
                "configured" if self.callback_url else "disabled",
                "auth.json" if self.auth_configured else "offline-default",
                f"on (near={self.config.near_interval_seconds}s)" if self.perception else "off",
            )
        except Exception:
            await self._cleanup()
            raise

    async def _claim_controller_with_retry(self) -> None:
        try:
            await self._client.claim_controller(self._controller_id)
            self._controller_state = "ACTIVE"
        except MinecraftRuntimeError as exc:
            if exc.status == 404:
                self._controller_state = "UNSUPPORTED"
                log.warning("[Minecraft] runtime 不支持控制者所有权接口")
            elif exc.unreachable or exc.status >= 500 or exc.status == 409:
                # 409 = another (test harness/admin) owner holds it; keep retrying.
                self._controller_state = "UNCLAIMED"
                log.warning("[Minecraft] 控制者暂不可用，稍后重试：%s", exc)
            else:
                self._controller_state = "FAILED"
                log.warning("[Minecraft] 控制者所有权不可用：%s", exc)

    async def _controller_renew_loop(self) -> None:
        waited = 0.0
        if self._controller_initial_delay > 0:
            await asyncio.sleep(self._controller_initial_delay)
            self._controller_initial_delay = 0.0
        while self._started:
            await asyncio.sleep(0.5 if self._controller_state in {"UNCLAIMED", "UNKNOWN"} else 30.0)
            try:
                if self._controller_state in {"UNCLAIMED", "UNKNOWN"}:
                    await self._client.claim_controller(self._controller_id)
                    self._controller_state = "ACTIVE"
                    continue
                if not self._client.controller_ready():
                    raise MinecraftRuntimeError("控制者凭据已失效", code="controller.expired")
                await self._client.renew_controller()
            except asyncio.CancelledError:
                raise
            except MinecraftRuntimeError as exc:
                if exc.status == 404:
                    self._controller_state = "UNSUPPORTED"
                    continue
                if exc.status == 409:
                    self._controller_state = "UNCLAIMED"
                    continue
                if (exc.unreachable or exc.status >= 500) and waited < 60.0:
                    waited += 0.5
                    continue
                self._controller_state = "UNKNOWN"
                self._client.owner_state = "UNKNOWN"
                log.warning("[Minecraft] 控制者续租失败；世界写将 fail-closed")
            except Exception:  # noqa: BLE001
                self._controller_state = "UNKNOWN"
                self._client.owner_state = "UNKNOWN"

    def begin_shutdown(self) -> None:
        """Freeze new dispatches; STOP/read-only remain available until cleanup."""
        self._shutting_down = True

    def controller_ready(self) -> bool:
        """TaskRuntime 派发前的控制者准入（只读、无副作用）。"""
        if self._shutting_down or self._controller_state != "ACTIVE":
            return False
        try:
            return bool(self._client.controller_ready())
        except Exception:  # noqa: BLE001
            return False

    async def stop(self) -> None:
        """Bot.shutdown 时调用：断开 bot（尽力）、停轮询、回收 runtime 进程。"""
        self.begin_shutdown()
        await self._cleanup()

    #: 控制者释放的**有界**预算（秒）。与相邻 disconnect 清理（3.0s）同量级：
    #: 关停路径绝不允许被一次静默的 runtime 拖成分钟级（A3-FU1 实测 63.157s）。
    CONTROLLER_RELEASE_TIMEOUT_SECONDS = 3.0

    def _release_budget(self) -> float:
        """释放预算 = min(固定上限, 配置的单请求预算)，且不小于 0.1s。

        绝不能直接沿用 ``request_timeout_seconds``：那是给正常业务调用用的，
        60s 的预算会把一次静默等待原样带进关停（P1-F1 的成因）。
        """
        budget = float(self.CONTROLLER_RELEASE_TIMEOUT_SECONDS)
        configured = float(getattr(self.config, "request_timeout_seconds", 0.0) or 0.0)
        if configured > 0:
            budget = min(budget, configured)
        return max(0.1, budget)

    def controller_released(self) -> bool:
        """只有 runtime **明确回执** released 时才为 True。

        TIMEOUT / UNKNOWN / NOT_DELIVERED / REJECTED / NOT_RELEASED / SKIPPED 一律
        False —— 超时绝不等于释放成功，下游不得把不确定性当成已释放。
        """
        return bool(self.controller_release.get("released"))

    async def _release_controller_bounded(self) -> dict[str, Any]:
        """释放结算的并发入口：同一生命周期只真正结算一次。

        第二个进入者**必须**等首个结算完成（有界），否则它会读到 ``controller_release``
        的初始占位（outcome=SKIPPED＝"从未持有"），把"正在释放"误报成"没有所有权"。
        等不到就返回 UNKNOWN + ``controller.release_in_progress``，仍然不伪造回执。
        """
        settled = self._release_settled
        self._release_settled = True
        if settled:
            try:
                await asyncio.wait_for(self._release_done.wait(), timeout=self._release_budget())
            except (TimeoutError, asyncio.CancelledError):
                pass
            if self._release_done.is_set():
                return dict(self.controller_release)
            report = dict(self.controller_release)
            report.update(
                outcome=RELEASE_UNKNOWN,
                released=False,
                attempted=True,
                code="controller.release_in_progress",
            )
            log.warning("[Minecraft] 并发释放仍在进行，返回 UNKNOWN（不伪造回执）")
            return report
        try:
            return await self._release_controller_once()
        finally:
            self._release_done.set()

    async def _release_controller_once(self) -> dict[str, Any]:
        """有界释放控制者所有权，并把结果留成可区分的四态证据（P1-F1）。

        结果分类（前四个就是必须可区分的四态）：

        * ``RELEASED``      —— runtime 明确回执已释放（唯一 released=True 的结果）；
        * ``REJECTED``      —— runtime 明确 4xx 业务拒绝（如 controller.release_forbidden）；
        * ``TIMEOUT``       —— 等响应等到预算用完（传输超时）；
        * ``UNKNOWN``       —— 请求可能已送达但结果不可知（断连 / 响应丢失 / 5xx / 非 JSON）；
        * ``NOT_DELIVERED`` —— 连接根本没建立（连接被拒），确定"这一次没有释放"；
        * ``NOT_RELEASED``  —— runtime 回 2xx 但 released=false（它那边本来没有所有权）；
        * ``SKIPPED``       —— 本服务从未持有所有权，不发请求。

        不变量：

        1. 超时 ≠ 释放成功：除 RELEASED 外绝不回写 released 回执；
        2. 不确定时 ``owner_state`` 置 UNKNOWN 且**保留** owner_token ——
           不确定性本身就是下游必须看见的证据，因此 controller_ready() 继续 fail-closed；
        3. 从未持有（无 controller_id 且无 token）绝不发释放请求，
           否则可能在别的控制者持有时造成串扰（多 controller 所有权隔离）。
        """
        report: dict[str, Any] = {
            "outcome": RELEASE_SKIPPED,
            "released": False,
            "attempted": False,
            "code": "",
            "status": 0,
            "budget": 0.0,
            "elapsed": 0.0,
            "owner_state": "",
        }
        client = self._client
        if client is None:
            report["code"] = "controller.no_client"
            self.controller_release = report
            return dict(report)
        ownership = bool(str(getattr(client, "controller_id", "") or "")) or bool(
            str(getattr(client, "owner_token", "") or "")
        )
        if not ownership:
            report["code"] = "controller.no_ownership"
            report["owner_state"] = str(getattr(client, "owner_state", "") or "")
            self.controller_release = report
            log.info("[Minecraft] 未持有控制者所有权，跳过释放请求")
            return dict(report)
        budget = self._release_budget()
        report["budget"] = budget
        report["attempted"] = True
        started = time.monotonic()
        try:
            await asyncio.wait_for(client.release_controller(), timeout=budget)
        except asyncio.CancelledError:
            # 关停本身被取消：同样不许伪造回执，但必须留下证据再上抛。
            client.owner_state = "UNKNOWN"
            report.update(
                outcome=RELEASE_UNKNOWN,
                code="controller.release_cancelled",
                elapsed=time.monotonic() - started,
                owner_state="UNKNOWN",
            )
            self.controller_release = report
            log.warning("[Minecraft] 控制者释放被取消，所有权状态保持 UNKNOWN")
            raise
        except Exception as exc:  # noqa: BLE001 - 释放失败不得挂住关停，但必须留证据
            outcome = classify_release_failure(exc)
            if outcome not in _RELEASE_OUTCOMES or outcome == RELEASE_RELEASED:
                # 兜底：无法归类的一律 UNKNOWN，绝不允许从失败路径产出"已释放"。
                outcome = RELEASE_UNKNOWN
            code = str(getattr(exc, "code", "") or "") or type(exc).__name__
            status = int(getattr(exc, "status", 0) or 0)
            if outcome == RELEASE_REJECTED and code not in _STILL_OWNER_REJECTION_CODES:
                # runtime 明确说"你不是有效控制者"：本地所有权按 fenced 处理（写权归零）。
                client.owner_state = "REVOKED"
            elif outcome == RELEASE_REJECTED:
                # 例如 controller.actions_pending：所有权还在，但释放没有发生。
                pass
            else:
                # 超时 / 响应丢失 / 5xx：保留不确定性，owner_token 不清。
                client.owner_state = "UNKNOWN"
            report.update(
                outcome=outcome,
                code=code,
                status=status,
                elapsed=time.monotonic() - started,
                owner_state=str(getattr(client, "owner_state", "") or ""),
            )
            self.controller_release = report
            log.warning(
                "[Minecraft] 控制者释放未确认（%s/%s，%.3fs/%.1fs）：%s",
                outcome,
                code,
                report["elapsed"],
                budget,
                exc,
            )
            return dict(report)
        confirmed = bool(getattr(client, "release_confirmed", False))
        report["ack"] = str(getattr(client, "release_ack", "") or "")
        report["ack_reason"] = str(getattr(client, "release_ack_reason", "") or "")
        report["owner_token_present"] = bool(str(getattr(client, "owner_token", "") or ""))
        if confirmed:
            report.update(outcome=RELEASE_RELEASED, released=True)
        elif report["ack_reason"] == "ack.explicit" or report["ack"] == "explicit":
            # runtime 明确说这次没有释放（released=false / 状态仍 ACTIVE）。
            report.update(outcome=RELEASE_NOT_RELEASED, code="controller.not_released")
        else:
            # 矛盾回执 / 畸形值 / 归属不符：不可判定，按 UNKNOWN 保留不确定性。
            report.update(
                outcome=RELEASE_UNKNOWN,
                code=report["ack_reason"] or "controller.release_ack_unknown",
            )
        report["elapsed"] = time.monotonic() - started
        report["owner_state"] = str(getattr(client, "owner_state", "") or "")
        self.controller_release = report
        log.info(
            "[Minecraft] 控制者释放 %s（%.3fs/%.1fs，owner_state=%s）",
            report["outcome"],
            report["elapsed"],
            budget,
            report["owner_state"],
        )
        return dict(report)

    async def _cleanup(self) -> None:
        """一次性收尾：并发/重复 stop 都收敛到同一份结果。

        ``_cleanup_done`` **只**在真正跑完尾部清理后置位：一次被取消（CancelledError）
        或抛错的收尾不得记成"已完成"，否则 aiohttp 会话与托管的 Node 子进程会泄漏，
        而之后重试 stop() 只会直接 return，永远补不上。
        """
        async with self._cleanup_lock:
            if self._cleanup_done:
                return
            self.cleanup_state = "in_progress"
            try:
                await self._cleanup_once()
            except asyncio.CancelledError:
                self.cleanup_state = "cancelled"
                raise
            except BaseException:
                self.cleanup_state = "failed"
                raise
            self._cleanup_done = True
            self.cleanup_state = "complete"

    async def _cleanup_once(self) -> None:
        self._started = False
        bootstrap_tasks = list(self._attest_bootstrap_tasks.values())
        self._attest_bootstrap_tasks.clear()
        for task in bootstrap_tasks:
            if not task.done():
                task.cancel()
        if bootstrap_tasks:
            await asyncio.gather(*bootstrap_tasks, return_exceptions=True)
        refresh = self._stop_attestation_refresh()
        if refresh is not None:
            try:
                await asyncio.wait_for(asyncio.shield(refresh), timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception:  # noqa: BLE001 - cleanup is best effort
                pass
        self._revoke_all_identity("service_stop")
        if self._perception_task is not None:
            self._perception_task.cancel()
            try:
                await self._perception_task
            except asyncio.CancelledError:
                pass
            self._perception_task = None
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._controller_task is not None:
            self._controller_task.cancel()
            try:
                await self._controller_task
            except asyncio.CancelledError:
                pass
            self._controller_task = None
        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.disconnect(), timeout=3.0)
            except Exception:  # noqa: BLE001 - 停机路径尽力而为
                pass
            try:
                # P1-F1：有界释放 + 四态证据；超时绝不伪造 released 回执。
                await self._release_controller_bounded()
            finally:
                # 释放被取消也必须关会话、回收 runtime 子进程：尾部清理用 shield
                # 保住，否则进程/端口泄漏，下一次 stop() 又因为 done=False 才能重试。
                await asyncio.shield(self._cleanup_tail())
        else:
            await self._cleanup_tail()

    async def _cleanup_tail(self) -> None:
        """释放之后的资源收尾（幂等，可被重试）。"""
        # 注意：保留 self._client 引用（已 close 的客户端仍可读状态），
        # 只回收进程；关闭后的 client 由 _closed 守卫拒绝复活 I/O。
        client = self._client
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001 - 停机路径尽力而为
                pass
        process, self._process = self._process, None
        if process is not None:
            try:
                await process.stop()
            except Exception:  # noqa: BLE001 - 停机路径尽力而为
                pass

    def _default_callback_url(self) -> str:
        web = self.bot.config.web
        if not bool(getattr(web, "enabled", False)):
            log.warning(
                "[Minecraft] WebUI 未启用，Bridge 事件回调不可用（仅剩状态轮询兜底）；"
                "建议设置 web.enabled: true"
            )
            return ""
        return f"http://127.0.0.1:{web.port}/api/v1/minecraft/events"

    # ------------------------------------------------------------ runtime 进程

    def _runtime_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["MC_RUNTIME_PORT"] = str(self.config.runtime_port)
        env["MC_CONNECT_TIMEOUT"] = str(self.config.connect_timeout_seconds)
        env["MC_ATTRIBUTION_STRICT"] = "1" if self.config.attribution_strict else "0"
        env["MC_ATTRIBUTION_URL"] = self.config.attribution_url
        env["MC_ATTRIBUTION_TOKEN"] = os.environ.get("CATOO_ATTRIBUTION_TOKEN", "")
        # Phase 4B：dig 的安全门（超时/距离）随进程环境注入 runtime
        env["MC_DIG_TIMEOUT_MS"] = str(int(self.config.action.dig.timeout * 1000))
        env["MC_DIG_MAX_DISTANCE"] = str(self.config.action.dig.max_distance)
        # Phase 4C：place 的安全门
        env["MC_PLACE_TIMEOUT_MS"] = str(int(self.config.action.place.timeout * 1000))
        env["MC_PLACE_MAX_DISTANCE"] = str(self.config.action.place.max_distance)
        # Phase 4H：拾取（有限导航 + 收集确认）
        env["MC_PICKUP_TIMEOUT_MS"] = str(int(self.config.action.pickup.timeout * 1000))
        env["MC_PICKUP_MAX_DISTANCE"] = str(self.config.action.pickup.max_distance)
        # Phase 4G：工作台的最大交互距离（眼睛 → 方块中心）
        env["MC_CRAFTING_TABLE_MAX_DISTANCE"] = str(
            self.config.action.craft.crafting_table.max_distance
        )
        # Phase 4F：crafting（玩家 2×2）的超时
        env["MC_CRAFT_TIMEOUT_MS"] = str(int(self.config.action.craft.timeout * 1000))
        env["MC_RECIPE_LOOKUP_TIMEOUT_MS"] = str(
            int(self.config.action.recipe_lookup.timeout * 1000)
        )
        # Phase 4E：container 的安全门（超时/距离）
        env["MC_CONTAINER_TIMEOUT_MS"] = str(int(self.config.action.container.timeout * 1000))
        env["MC_CONTAINER_MAX_DISTANCE"] = str(self.config.action.container.max_distance)
        # Phase 4K：找方块的默认范围/条数（硬上限仍是 runtime 常量）
        env["MC_FIND_BLOCKS_MAX_DISTANCE"] = str(self.config.action.find_blocks.max_distance)
        env["MC_FIND_BLOCKS_MAX_RESULTS"] = str(self.config.action.find_blocks.max_results)
        # Phase 4D：背包写操作的写超时
        env["MC_EQUIP_TIMEOUT_MS"] = str(int(self.config.action.equip.timeout * 1000))
        env["MC_INVENTORY_MOVE_TIMEOUT_MS"] = str(
            int(self.config.action.inventory_move.timeout * 1000)
        )
        env["MC_AUTH_FILE"] = str(self._runtime_dir() / "auth.json")
        # Phase 3C：move_to 的最大距离（runtime 侧与 Service 侧同规则）
        env["MC_MOVE_MAX_DISTANCE"] = str(self.config.action.move_to.max_distance)
        # Phase 3D：follow_player 的超时与最大追逐距离
        env["MC_FOLLOW_TIMEOUT_MS"] = str(int(self.config.action.follow_player.timeout * 1000))
        env["MC_FOLLOW_MAX_CHASE_DISTANCE"] = str(
            self.config.action.follow_player.max_chase_distance
        )
        # Phase 7F.5：**正向**凭据随进程环境注入 —— Node 侧在所有路由之前强制校验它。
        env["MC_BRIDGE_TOKEN"] = self.bridge_token or ""
        if self.callback_url:
            env["MC_CALLBACK_URL"] = self.callback_url
            env["MC_CALLBACK_TOKEN"] = self.callback_token or ""
        return env

    async def _start_runtime(self) -> None:
        if self._process is not None:
            await self._process.stop()
            self._process = None
        runtime_dir = self._runtime_dir()
        if not (runtime_dir / "runtime.js").exists():
            target = runtime_dir / "runtime.js"
            raise MinecraftRuntimeDown(
                f"找不到 Minecraft runtime：{target}（检查 minecraft.runtime_dir）"
            )
        if not self.callback_token:
            self.callback_token = secrets.token_urlsafe(24)
        self._ensure_bridge_token()
        process = _RuntimeProcess(runtime_dir, self.config.node_executable, self._runtime_env())
        try:
            await process.start()
        except FileNotFoundError as exc:
            raise MinecraftRuntimeDown(
                f"无法启动 node（{self.config.node_executable}）：请确认已安装 Node.js ≥ 20"
            ) from exc
        self._process = process
        deadline = self._clock() + self.config.startup_timeout_seconds
        while self._clock() < deadline:
            if not process.running:
                tail = "\n".join(list(process.log_tail)[-8:])
                raise MinecraftRuntimeDown(
                    f"Minecraft runtime 进程启动后立即退出（见日志）\n{tail}"
                )
            try:
                payload = await self._client.health()
                self._note_runtime_instance(payload)
                self._runtime_down = False
                log.info("[Minecraft] runtime 进程就绪 (pid=%s)", process.pid)
                return
            except MinecraftRuntimeError:
                await asyncio.sleep(0.3)
        raise MinecraftRuntimeDown(
            f"Minecraft runtime 在 {self.config.startup_timeout_seconds}s 内未就绪"
        )

    async def _ensure_runtime(self) -> None:
        try:
            # Phase 7F.5.1 §二：**每一次成功的健康探测同时是身份探测**。以前这里只调
            # health() 而不处理身份字段，于是「外部托管的 Runtime（auto_start_runtime=false）
            # 在 Python 不重启的情况下换了实例」这件事永远观测不到，只能等旧动作 TTL 过期。
            # 注意：失败、超时、401、无效响应绝不更新身份（_note_runtime_instance 内部保证）。
            payload = await self._client.health()
            self._note_runtime_instance(payload)
            self._runtime_down = False
            return
        except MinecraftRuntimeError:
            pass
        if not self.config.auto_start_runtime:
            raise MinecraftRuntimeDown("Minecraft runtime 未运行（auto_start_runtime=false）")
        if self._restart_count >= self.config.max_runtime_restarts:
            restarts = self.config.max_runtime_restarts
            raise MinecraftRuntimeDown(
                f"runtime 重启次数用尽（{restarts}），请检查日志后重启 CatooBot"
            )
        self._restart_count += 1
        log.warning("[Minecraft] runtime 不可达，尝试重启（第 %d 次）", self._restart_count)
        await self._start_runtime()

    async def probe_runtime_instance(self) -> str:
        """主动做一次身份探测（Phase 7F.5.1 §三.1）：成功响应 -> 返回当前实例标识。

        与 _ensure_runtime 的唯一区别是**失败语义**：这里把失败如实抛给调用方，
        让「Runtime 不可达」（按既有传输层语义处理）与「Runtime 可达但不报告身份」
        （必须在**真正发出外部动作之前** fail-closed）能被分开判定。
        """
        payload = await self._client.health()
        self._note_runtime_instance(payload)
        self._runtime_down = False
        return self.runtime_instance()

    def _ensure_bridge_token(self) -> str:
        """确保本进程有一份**正向**调用凭据（Phase 7F.5 §六）。

        每进程生成一次、之后保持不变（重启 Python ⇒ 换新凭据；重启 Node ⇒ 由 Python
        重新注入）。凭据本身绝不写日志、绝不进错误响应或事件。
        """
        if not self.bridge_token:
            self.bridge_token = secrets.token_urlsafe(32)
        return self.bridge_token

    # ------------------------------------------------- Phase 7F.5：运行时身份

    def runtime_instance(self) -> str:
        """当前 Runtime 实例标识（Phase 7F.5）。

        只来自**成功的**身份响应；从没见过就返回空串（此时关联仍按 action_id 工作）。
        """
        return self._runtime_instance

    def _note_runtime_instance(self, payload: Any) -> None:
        """记录成功身份响应里的实例标识，并在**真的换了**时通知任务层对账。

        两条纪律（§五.1）：

        * 只有**成功**的身份响应才更新/判定实例 —— 单次请求失败或单次健康检查失败
          不足以推断 Runtime 已经重启；
        * 首次见到实例只记录、不通知（还没有任何旧实例的动作需要分类）。
        """
        if not isinstance(payload, dict):
            return
        instance = str(payload.get("runtime_instance") or "")
        if not instance:
            return
        previous = self._runtime_instance
        self._runtime_instance = instance
        if not previous or previous == instance:
            return
        revoke = getattr(self, "_revoke_all_identity", None)
        if callable(revoke):
            revoke("runtime_instance_changed")
        # A confirmed new instance means the previous runtime session is dead: clear
        # its id so the new runtime's spawned/connected events are accepted, while
        # any late event still naming the old session is ignored.
        mirror = getattr(self, "_mirror", None)
        if isinstance(mirror, dict):
            mirror["session_id"] = None
        callback = self.on_runtime_instance_changed
        if callback is None:
            return
        log.warning("[Minecraft] runtime 实例已更换：%s -> %s", previous, instance)
        try:
            result = callback(instance)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception:  # noqa: BLE001 - 对账通知失败不该影响服务本身
            log.exception("[Minecraft] runtime instance change notification failed")

    # -------------------------------------------------------------- bridge API

    async def join(self, host: str, port: int) -> dict[str, Any]:
        """加入服务器。返回 ``{"session_id", "status"}``；失败抛稳定错误。"""
        self._require_enabled()
        clean_host = str(host or "").strip()
        if not clean_host or len(clean_host) > 253:
            raise MinecraftInvalidTarget("服务器地址不能为空")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise MinecraftInvalidTarget("端口必须是 1-65535 的整数")
        await self._ensure_runtime()
        try:
            result = await self._client.connect(clean_host, int(port))
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc
        self._mirror = {
            "status": str(result.get("status") or "CONNECTING"),
            "session_id": result.get("session_id"),
            "host": clean_host,
            "port": int(port),
        }
        return result

    async def leave(self) -> dict[str, Any]:
        """主动离开。幂等：不在任何服务器（或 runtime 已死）时也算成功。"""
        self._require_enabled()
        try:
            await self._ensure_runtime()
        except MinecraftRuntimeDown:
            # runtime 进程都没了 → bot 必然不在线；按幂等语义落回 DISCONNECTED
            self._mirror = {"status": "DISCONNECTED"}
            return {"ok": True, "status": "DISCONNECTED", "note": "runtime 不可达，视为已断开"}
        try:
            return await self._client.disconnect()
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
                self._mirror = {"status": "DISCONNECTED"}
                return {"ok": True, "status": "DISCONNECTED", "note": "runtime 不可达，视为已断开"}
            raise _translate(exc) from exc

    async def send_chat(self, message: str) -> dict[str, Any]:
        """在服务器里发言。只做校验与翻译，不判断语义。"""
        self._require_enabled()
        text = str(message or "")
        if not text.strip():
            raise MinecraftBridgeError("消息不能为空", code="minecraft.chat_empty")
        if len(text) > 256:
            raise MinecraftBridgeError("消息超过 256 字符上限", code="minecraft.chat_too_long")
        try:
            await self._ensure_runtime()
            return await self._client.chat(text)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    # ------------------------------------------- Action Runtime（Phase 3B）

    async def look_at(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
        """让罐头看向指定世界坐标（SAFE 动作：不改世界、不移动）。

        参数校验在 Service 层先做一遍（与 runtime 同规则）——上层永远不接触
        yaw/pitch 数学；返回 ``{action_id, action, status}``，终态含 TIMEOUT/CANCELLED。
        """
        self._require_enabled()
        coords: dict[str, float] = {}
        for name, value in (("x", x), ("y", y), ("z", z)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MinecraftActionInvalid(f"坐标 {name} 必须是数字")
            if not math.isfinite(float(value)):
                raise MinecraftActionInvalid(f"坐标 {name} 必须是有限数字")
            coords[name] = float(value)
        if abs(coords["x"]) > 3.0e7 or abs(coords["z"]) > 3.0e7 or not -512 <= coords["y"] <= 2048:
            raise MinecraftActionInvalid("坐标超出 Minecraft 世界边界")
        try:
            await self._ensure_runtime()
            return await self._client.look_at(coords["x"], coords["y"], coords["z"])
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def move_to(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
        """非破坏性导航到世界坐标（Phase 3C · LOW）。

        校验（与 runtime 同规则）：坐标有限且在世界边界内 + 距当前位置不超过
        ``minecraft.action.move_to.max_distance``（镜像位置；运行时还会用实时位置复检）。
        不可达 → :class:`MinecraftPathNotFound`（绝不自动挖/搭/绕圈）。
        """
        self._require_enabled()
        coords: dict[str, float] = {}
        for name, value in (("x", x), ("y", y), ("z", z)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MinecraftActionInvalid(f"坐标 {name} 必须是数字")
            if not math.isfinite(float(value)):
                raise MinecraftActionInvalid(f"坐标 {name} 必须是有限数字")
            coords[name] = float(value)
        if abs(coords["x"]) > 3.0e7 or abs(coords["z"]) > 3.0e7 or not -512 <= coords["y"] <= 2048:
            raise MinecraftActionInvalid("坐标超出 Minecraft 世界边界")
        position = self._mirror.get("position")
        max_distance = float(self.config.action.move_to.max_distance)
        if isinstance(position, dict) and {"x", "y", "z"} <= set(position):
            distance = math.dist(
                (coords["x"], coords["y"], coords["z"]),
                (float(position["x"]), float(position["y"]), float(position["z"])),
            )
            if distance > max_distance:
                raise MinecraftActionInvalid(
                    f"目标距当前位置 {distance:.1f} 格，超过 move_to 上限 {max_distance:g} 格"
                )
        try:
            await self._ensure_runtime()
            return await self._client.move_to(coords["x"], coords["y"], coords["z"])
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def follow_player(self, username: Any, distance: Any = None) -> dict[str, Any]:
        """动态跟随玩家（Phase 3D · LOW · 持续型动作）。

        验证（§二十）：username 非空 / ≤16 字符 / 无控制字符；distance 1.5~6
        （默认 2.5）；Minecraft 必须 ONLINE（镜像判定）。启动成功即返回 ``RUNNING``
        ——终态（STOP / 超时 / 目标丢失 / 超距离）经 action 事件异步送达。
        """
        self._require_enabled()
        if not isinstance(username, str) or not username.strip():
            raise MinecraftActionInvalid("username 不能为空")
        if len(username) > FOLLOW_MAX_USERNAME_CHARS:
            raise MinecraftActionInvalid(f"username 最长 {FOLLOW_MAX_USERNAME_CHARS} 个字符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in username):
            raise MinecraftActionInvalid("username 不能包含控制字符")
        raw_distance = FOLLOW_DEFAULT_DISTANCE if distance is None or distance == "" else distance
        if isinstance(raw_distance, bool) or not isinstance(raw_distance, (int, float)):
            raise MinecraftActionInvalid("distance 必须是数字")
        target_distance = float(raw_distance)
        if not math.isfinite(target_distance) or not (
            FOLLOW_MIN_DISTANCE <= target_distance <= FOLLOW_MAX_DISTANCE
        ):
            raise MinecraftActionInvalid(
                f"distance 必须在 {FOLLOW_MIN_DISTANCE:g}~{FOLLOW_MAX_DISTANCE:g} 格之间"
            )
        if str(self._mirror.get("status", "DISCONNECTED")) != "ONLINE":
            raise MinecraftNotConnected("罐头不在世界里，无法跟随")
        try:
            await self._ensure_runtime()
            return await self._client.follow_player(username, target_distance)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
                raise MinecraftRuntimeUnreachable(str(exc)) from exc
            raise _translate(exc) from exc

    @staticmethod
    def validate_dig(
        x: Any,
        y: Any,
        z: Any,
        expected_block: Any,
        expected_tool: Any = None,
        *,
        require_collection: bool = False,
    ) -> tuple[dict[str, float], str, str | None, bool]:
        """dig 的参数校验（纯函数，抛 :class:`MinecraftActionInvalid`）。

        调用方（WebUI 调试端点）先校验再进确认门：垃圾参数不该挂出一条待确认。
        Phase 4I：``expected_tool`` 可选 —— 给了就是"执行瞬间主手必须拿着它"的身份硬约束
        （**不是**"帮我去找一把"），并在这里统一规范化成 canonical item name。
        """
        coords: dict[str, float] = {}
        for name, value in (("x", x), ("y", y), ("z", z)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MinecraftActionInvalid(f"坐标 {name} 必须是数字")
            if not math.isfinite(float(value)):
                raise MinecraftActionInvalid(f"坐标 {name} 必须是有限数字")
            coords[name] = float(value)
        if abs(coords["x"]) > 3.0e7 or abs(coords["z"]) > 3.0e7 or not -512 <= coords["y"] <= 2048:
            raise MinecraftActionInvalid("坐标超出 Minecraft 世界边界")
        if not isinstance(expected_block, str) or not expected_block.strip():
            raise MinecraftActionInvalid("expected_block 必须是非空字符串（先看清目标方块再动手）")
        if len(expected_block) > DIG_MAX_BLOCK_CHARS:
            raise MinecraftActionInvalid(f"expected_block 最长 {DIG_MAX_BLOCK_CHARS} 个字符")
        # 控制字符检查看**原始**值（与 follow_player 的 username 同规则）：
        # 带换行/制表的方块名一定是模型拼错了，宁可让它重来
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in expected_block):
            raise MinecraftActionInvalid("expected_block 不能包含控制字符")
        if not isinstance(require_collection, bool):
            raise MinecraftActionInvalid("require_collection 必须是布尔值")
        tool = MinecraftService.validate_expected_tool(expected_tool)
        return coords, expected_block.strip(), tool, require_collection

    @staticmethod
    def validate_expected_tool(expected_tool: Any) -> str | None:
        """Phase 4I（§六/§十二）：``expected_tool`` 的可选校验 + canonical 化。

        * 缺省（None/空字符串）→ ``None``（Phase 4B 原行为：不对主手加额外约束）；
        * 给了 → 必须是非空字符串、长度受限、无控制字符，并规范成 ``minecraft:xxx``
          （``stone_pickaxe`` 与 ``minecraft:stone_pickaxe`` 是同一把工具）。

        只做**身份**校验：哪种工具"更适合"挖什么不由 CatooBot 判断（那是
        runtime 侧 ``bot.canDigBlock`` 的事，§十三/§十四）。
        """
        if expected_tool is None:
            return None
        if not isinstance(expected_tool, str):
            raise MinecraftActionInvalid("expected_tool 必须是字符串")
        if not expected_tool.strip():
            return None  # 空字符串等同"没要求"（旧调用方传空值不该变成硬约束）
        if len(expected_tool) > PLACE_MAX_ITEM_CHARS:
            raise MinecraftActionInvalid(f"expected_tool 最长 {PLACE_MAX_ITEM_CHARS} 个字符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in expected_tool):
            raise MinecraftActionInvalid("expected_tool 不能包含控制字符")
        return canonical_item_name(expected_tool)

    async def inventory(self) -> dict[str, Any]:
        """只读背包切片（Phase 4C）：选中的 hotbar 槽 / 手持物品 / 按名字聚合的物品。

        只做投影与错误翻译：没有 slot、没有 NBT、没有 window/容器状态（§四）。
        """
        self._require_enabled()
        try:
            await self._ensure_runtime()
            return await self._client.inventory()
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def inventory_slots(self) -> dict[str, Any]:
        """**调试**用的原始槽位视图（WebUI Move Test / smoke 用；LLM 工具不读它）。

        只读投影：槽位号 + 物品名 + 数量 + 是否快捷栏；没有 NBT、没有 window 对象。
        """
        self._require_enabled()
        try:
            await self._ensure_runtime()
            return await self._client.inventory_slots()
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    @staticmethod
    def validate_equip(item: Any) -> str:
        """equip 的参数校验（纯函数）：item 非空字符串、长度受限、无控制字符。"""
        if not isinstance(item, str) or not item.strip():
            raise MinecraftActionInvalid("item 不能为空（先看清背包里有什么）")
        if len(item) > PLACE_MAX_ITEM_CHARS:
            raise MinecraftActionInvalid(f"item 最长 {PLACE_MAX_ITEM_CHARS} 个字符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in item):
            raise MinecraftActionInvalid("item 不能包含控制字符")
        return item.strip()

    @staticmethod
    def validate_inventory_move(
        source_slot: Any, destination_slot: Any, item: Any, count: Any
    ) -> tuple[int, int, str, int]:
        """inventory_move 的参数校验（纯函数）。

        槽位必须是 :data:`PLAYER_SLOT_MIN` ~ :data:`PLAYER_SLOT_MAX`（主背包 + 快捷栏）的整数；
        count 是 >= 1 的整数；item 是非空字符串。**不是**聚合视图，槽位只在这里出现。
        """
        slots: dict[str, int] = {}
        for name, value in (("source_slot", source_slot), ("destination_slot", destination_slot)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MinecraftActionInvalid(f"{name} 必须是整数")
            if not PLAYER_SLOT_MIN <= value <= PLAYER_SLOT_MAX:
                raise MinecraftActionInvalid(
                    f"{name} 必须在 {PLAYER_SLOT_MIN}~{PLAYER_SLOT_MAX} 之间"
                    "（主背包 9-35 + 快捷栏 36-44）"
                )
            slots[name] = int(value)
        if slots["source_slot"] == slots["destination_slot"]:
            raise MinecraftActionInvalid("source_slot 与 destination_slot 不能相同")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise MinecraftActionInvalid("count 必须是 >= 1 的整数")
        if not isinstance(item, str) or not item.strip():
            raise MinecraftActionInvalid("item 不能为空")
        if len(item) > PLACE_MAX_ITEM_CHARS:
            raise MinecraftActionInvalid(f"item 最长 {PLACE_MAX_ITEM_CHARS} 个字符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in item):
            raise MinecraftActionInvalid("item 不能包含控制字符")
        return slots["source_slot"], slots["destination_slot"], item.strip(), int(count)

    async def equip(self, item: Any) -> dict[str, Any]:
        """把背包里**明确指定**的物品拿到主手（Phase 4D · MEDIUM · 需要用户确认）。

        只支持 destination=hand；不自动装备、不切 hotbar、不挑"更方便"的 stack
        （runtime 按槽位稳定顺序取第一个匹配的物品）。返回启动即 ``RUNNING``，
        终态经事件送达（成功带 ``item/destination/held_item/already_equipped``）。
        """
        self._require_enabled()
        clean = self.validate_equip(item)
        try:
            await self._ensure_runtime()
            return await self._client.equip(clean)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def inventory_move(
        self, source_slot: Any, destination_slot: Any, item: Any, count: Any
    ) -> dict[str, Any]:
        """把一个明确槽位上的物品移动指定数量到另一个明确槽位（Phase 4D · MEDIUM）。

        一个物品、一个 source、一个 destination、一个 count；目标被别的物品占用时
        **拒绝**（绝不隐式交换）；不批量整理、不自动找"最优"槽位。
        """
        self._require_enabled()
        source, destination, clean_item, clean_count = self.validate_inventory_move(
            source_slot, destination_slot, item, count
        )
        try:
            await self._ensure_runtime()
            return await self._client.inventory_move(source, destination, clean_item, clean_count)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    @staticmethod
    def validate_pickup(entity_id: Any, expected_item: Any) -> tuple[int, str]:
        """pickup_item 的参数校验（纯函数）。

        entity_id 是 >= 0 的整数；expected_item 是**第二层身份校验**（非空物品名）——
        光有 entity id 不是足够的人类可读安全约束（§十四）。真正的"实体还在不在 /
        还是不是掉落物 / 物品对不对 / 距离够不够"由 runtime 用实时状态判定。
        """
        if isinstance(entity_id, bool) or not isinstance(entity_id, int):
            raise MinecraftActionInvalid("entity_id 必须是整数（用 minecraft_dropped_items 拿）")
        if entity_id < 0:
            raise MinecraftActionInvalid("entity_id 不能是负数")
        if not isinstance(expected_item, str) or not expected_item.strip():
            raise MinecraftActionInvalid("expected_item 不能为空（第二层身份校验）")
        clean = expected_item.strip()
        if len(clean) > PLACE_MAX_ITEM_CHARS:
            raise MinecraftActionInvalid(f"expected_item 最长 {PLACE_MAX_ITEM_CHARS} 个字符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in clean):
            raise MinecraftActionInvalid("expected_item 不能包含控制字符")
        return int(entity_id), clean

    @staticmethod
    def validate_crafting_table(crafting_table: Any) -> dict[str, int] | None:
        """crafting_table 参数校验（纯函数，§五）。

        要么没有（= 玩家自身 2×2），要么是**明确的整数方块坐标**；绝不接受
        ``"nearest"`` / ``"auto"`` / ``"any"`` 这类执行时才决定的隐式目标。
        是否存在工作台、是不是工作台、距离够不够，由 runtime 用**实时**方块状态判定。
        """
        if crafting_table is None:
            return None
        if not isinstance(crafting_table, Mapping):
            raise MinecraftActionInvalid(
                "crafting_table 必须是 {x, y, z} 这样的方块坐标（不接受 nearest / auto）"
            )
        coords: dict[str, int] = {}
        for name in ("x", "y", "z"):
            value = crafting_table.get(name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise MinecraftActionInvalid(f"crafting_table.{name} 必须是整数")
            coords[name] = int(value)
        if abs(coords["x"]) > 3.0e7 or abs(coords["z"]) > 3.0e7 or not -512 <= coords["y"] <= 2048:
            raise MinecraftActionInvalid("crafting_table 坐标超出 Minecraft 世界边界")
        return coords

    @staticmethod
    def validate_recipe_lookup(
        item: Any, crafting_table: Any = None
    ) -> tuple[str, dict[str, int] | None]:
        """recipe_lookup 的参数校验（纯函数）：物品名 + 可选的工作台坐标。"""
        if not isinstance(item, str) or not item.strip():
            raise MinecraftActionInvalid("item 不能为空（要查什么物品的配方）")
        if len(item) > PLACE_MAX_ITEM_CHARS:
            raise MinecraftActionInvalid(f"item 最长 {PLACE_MAX_ITEM_CHARS} 个字符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in item):
            raise MinecraftActionInvalid("item 不能包含控制字符")
        return item.strip(), MinecraftService.validate_crafting_table(crafting_table)

    @staticmethod
    def validate_craft(
        recipe_id: Any, crafting_table: Any = None
    ) -> tuple[str, dict[str, int] | None]:
        """craft 的参数校验（纯函数）：recipe_id + 可选的工作台坐标。

        recipe_id 是可读的规范签名（``stick*4=oak_planks*2``，需要工作台的带 ``!`` 前缀），
        所以这里只做形状校验；**是否存在 / 现在能不能做 / 工作台还在不在**由 runtime
        用当前配方表、当前方块状态与当前背包判定。
        """
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            raise MinecraftActionInvalid(
                "recipe_id 不能为空（先用 minecraft_recipe_lookup 拿到它）"
            )
        clean = recipe_id.strip()
        if len(clean) > CRAFT_MAX_RECIPE_ID_CHARS:
            raise MinecraftActionInvalid(f"recipe_id 最长 {CRAFT_MAX_RECIPE_ID_CHARS} 个字符")
        if "*" not in clean or "=" not in clean:
            raise MinecraftActionInvalid(
                "recipe_id 形状不对（应当是 minecraft_recipe_lookup 返回的那个 id）"
            )
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in clean):
            raise MinecraftActionInvalid("recipe_id 不能包含控制字符")
        return clean, MinecraftService.validate_crafting_table(crafting_table)

    @staticmethod
    def validate_dig_capability(x: Any, y: Any, z: Any) -> dict[str, int]:
        """dig_capability 的参数校验（纯函数，抛 :class:`MinecraftActionInvalid`）。

        §四：**方块坐标必须是整数**（查的是「真实位置的方块」，不接受浮点想象坐标），
        也**不接受** block name / expected_tool —— 那两样都会把「事实」变成「调用者声称」。
        """
        coords: dict[str, int] = {}
        for name, value in (("x", x), ("y", y), ("z", z)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MinecraftActionInvalid(f"坐标 {name} 必须是整数（方块坐标没有小数）")
            coords[name] = int(value)
        if abs(coords["x"]) > 3.0e7 or abs(coords["z"]) > 3.0e7 or not -512 <= coords["y"] <= 2048:
            raise MinecraftActionInvalid("坐标超出 Minecraft 世界边界")
        return coords

    async def dig_capability(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
        """只读查「当前状态下这个方块能不能挖、大概多久」（Phase 4J · SAFE · 非独占）。

        事实全部来自 runtime 的实时读取（blockAt / heldItem / canDigBlock / digTime）；
        本方法绝不改世界、不改背包、不装备、不移动。返回同步语义投影。
        """
        self._require_enabled()
        coords = self.validate_dig_capability(x, y, z)
        try:
            await self._ensure_runtime()
            return await self._client.dig_capability(coords["x"], coords["y"], coords["z"])
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    @staticmethod
    def validate_find_blocks(
        block_names: Any, max_distance: Any = None, max_results: Any = None
    ) -> tuple[list[str], int, int]:
        """find_blocks 的参数校验 + 规范化（纯函数，抛 :class:`MinecraftActionInvalid`）。

        §四：``block_names`` 是 1~8 个方块名（逐个规范化成裸名，去重，保持顺序）；
        ``max_distance`` / ``max_results`` 可选，越界直接拒绝（不允许"扫全世界"）。
        返回 ``(names, max_distance_or_0, max_results_or_0)``，0 表示"用配置默认值"。
        """
        if isinstance(block_names, str) or not isinstance(block_names, (list, tuple)):
            raise MinecraftActionInvalid("block_names 必须是方块名数组（不是单个字符串）")
        if not block_names:
            raise MinecraftActionInvalid("block_names 不能为空（至少要一个方块名）")
        if len(block_names) > FIND_BLOCKS_MAX_NAMES:
            raise MinecraftActionInvalid(
                f"block_names 最多 {FIND_BLOCKS_MAX_NAMES} 个（本阶段不做批量扫描）"
            )
        names: list[str] = []
        for raw in block_names:
            if not isinstance(raw, str) or not raw.strip():
                raise MinecraftActionInvalid("block_names 里每一项都必须是非空字符串")
            if len(raw) > 64:
                raise MinecraftActionInvalid("方块名最长 64 个字符")
            if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
                raise MinecraftActionInvalid("方块名不能包含控制字符")
            clean = canonical_block_name(raw)
            if clean and clean not in names:
                names.append(clean)
        if not names:
            raise MinecraftActionInvalid("block_names 不能为空（至少要一个方块名）")

        distance = 0
        if max_distance is not None and max_distance != "":
            if isinstance(max_distance, bool) or not isinstance(max_distance, int):
                raise MinecraftActionInvalid("max_distance 必须是整数")
            if not 1 <= max_distance <= FIND_BLOCKS_HARD_MAX_DISTANCE:
                raise MinecraftActionInvalid(
                    f"max_distance 必须在 1~{FIND_BLOCKS_HARD_MAX_DISTANCE} 之间"
                    "（不允许大范围全局扫描）"
                )
            distance = int(max_distance)

        results = 0
        if max_results is not None and max_results != "":
            if isinstance(max_results, bool) or not isinstance(max_results, int):
                raise MinecraftActionInvalid("max_results 必须是整数")
            if not 1 <= max_results <= FIND_BLOCKS_HARD_MAX_RESULTS:
                raise MinecraftActionInvalid(
                    f"max_results 必须在 1~{FIND_BLOCKS_HARD_MAX_RESULTS} 之间"
                )
            results = int(max_results)
        return names, distance, results

    async def find_blocks(
        self, block_names: Any, max_distance: Any = None, max_results: Any = None
    ) -> dict[str, Any]:
        """在当前**已加载**的世界里找指定方块的位置（Phase 4K · SAFE 只读 · 非独占）。

        只回答"在哪里"：不移动、不装备、不挖、不拾取，也不给任何"推荐/最佳"。
        找不到就是正常空结果；方块名不认识 → ``minecraft.block_name_unknown``（§二十二）。
        """
        self._require_enabled()
        names, distance, results = self.validate_find_blocks(block_names, max_distance, max_results)
        if distance == 0:
            distance = int(self.config.action.find_blocks.max_distance)
        if results == 0:
            results = int(self.config.action.find_blocks.max_results)
        try:
            await self._ensure_runtime()
            return await self._client.find_blocks(names, distance, results)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def dropped_items(self) -> dict[str, Any]:
        """读附近的**掉落物实体**（Phase 4H · SAFE 只读）。

        同步动作：直接返回语义投影（`entity_id` / `item{name,count}` / `position` /
        `distance`，按距离升序、最多 32 条 + `truncated`）；只列 Item Entity，
        玩家/怪物/投射物等一律不进列表，也不泄露 raw entity/metadata。
        """
        self._require_enabled()
        try:
            await self._ensure_runtime()
            return await self._client.dropped_items()
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def entities(self) -> dict[str, Any]:
        """R5: nearby living entities (SAFE read-only projection)."""
        self._require_enabled()
        try:
            await self._ensure_runtime()
            return await self._client.entities()
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def combat_verify(
        self,
        entity_id: Any,
        expected_type: Any,
        *,
        expected_uuid: Any = None,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """R5: verify the frozen entity is really gone after bounded attacks.

        This is a read-only server observation, not an action result claim.
        """
        self._require_enabled()
        if not isinstance(entity_id, int) or isinstance(entity_id, bool) or entity_id < 0:
            raise MinecraftActionInvalid("entity_id 必须是非负整数")
        if not isinstance(expected_type, str) or not expected_type.strip():
            raise MinecraftActionInvalid("expected_type 不能为空")
        uuid = None
        if expected_uuid is not None:
            if not isinstance(expected_uuid, str) or not expected_uuid.strip():
                raise MinecraftActionInvalid("expected_uuid 必须是非空字符串")
            uuid = expected_uuid.strip()
        deadline = time.monotonic() + max(0.2, min(float(timeout), 10.0))
        while True:
            try:
                await self._ensure_runtime()
                view = await self._client.entities()
            except MinecraftRuntimeError as exc:
                if exc.unreachable:
                    self._mark_runtime_down(str(exc))
                raise _translate(exc) from exc
            payload = (
                view.get("result")
                if isinstance(view, dict) and isinstance(view.get("result"), dict)
                else view
            )
            rows = payload.get("entities") if isinstance(payload, dict) else None
            match = None
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict) or row.get("entity_id") != entity_id:
                        continue
                    match = row
                    break
            if match is None:
                return {"ok": True, "absent": True, "entity_id": entity_id, "checked": True}
            if uuid and str(match.get("entity_uuid") or "") != uuid:
                return {"ok": True, "absent": True, "entity_id": entity_id, "checked": True}
            if time.monotonic() >= deadline:
                raise MinecraftActionFailed("目标实体仍然存在，不能宣告击杀")
            await asyncio.sleep(0.25)

    async def drop_item(self, item: Any, count: Any = 1) -> dict[str, Any]:
        """R6: one explicit main-hand drop (MEDIUM; no auto-equip)."""
        self._require_enabled()
        if not isinstance(item, str) or not item.strip():
            raise MinecraftActionInvalid("item 不能为空")
        name = item.strip().lower().removeprefix("minecraft:")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 64:
            raise MinecraftActionInvalid("count 必须是 1 到 64 之间的整数")
        try:
            await self._ensure_runtime()
            return await self._client.drop_item(name, count)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def delivery_verify(
        self,
        item: Any,
        count: Any,
        entity_id: Any = None,
        expected_uuid: Any = None,
    ) -> dict[str, Any]:
        """R6: confirm the dropped item entity still exists; state = DROPPED."""
        self._require_enabled()
        if not isinstance(item, str) or not item.strip():
            raise MinecraftActionInvalid("item 不能为空")
        name = item.strip().lower().removeprefix("minecraft:")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 64:
            raise MinecraftActionInvalid("count 必须是 1 到 64 之间的整数")
        try:
            await self._ensure_runtime()
            view = await self._client.dropped_items()
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc
        payload = (
            view.get("result")
            if isinstance(view, dict) and isinstance(view.get("result"), dict)
            else view
        )
        rows = payload.get("items") if isinstance(payload, dict) else None
        match = None
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if entity_id is not None and row.get("entity_id") != entity_id:
                    continue
                if expected_uuid and str(row.get("entity_uuid") or "") != str(expected_uuid):
                    continue
                stack_raw = row.get("item")
                stack = stack_raw if isinstance(stack_raw, dict) else {}
                if stack.get("name") == name and int(stack.get("count") or 0) == count:
                    match = row
                    break
        if match is None:
            raise MinecraftActionFailed("掉落物实体已不存在，无法确认 DROPPED")
        return {
            "ok": True,
            "state": "DROPPED",
            "item": name,
            "count": count,
            "entity_id": match.get("entity_id"),
            "entity_uuid": match.get("entity_uuid"),
            "position": match.get("position"),
        }

    async def attack(
        self,
        entity_id: Any,
        expected_type: Any,
        *,
        expected_uuid: Any = None,
        max_distance: Any = None,
    ) -> dict[str, Any]:
        """R5: exactly one hit on one frozen entity (MEDIUM; no navigation)."""
        self._require_enabled()
        if not isinstance(entity_id, int) or isinstance(entity_id, bool) or entity_id < 0:
            raise MinecraftActionInvalid("entity_id 必须是非负整数")
        if not isinstance(expected_type, str) or not expected_type.strip():
            raise MinecraftActionInvalid("expected_type 不能为空")
        uuid = None
        if expected_uuid is not None:
            if not isinstance(expected_uuid, str) or not expected_uuid.strip():
                raise MinecraftActionInvalid("expected_uuid 必须是非空字符串")
            uuid = expected_uuid.strip()
        distance = None
        if max_distance is not None:
            if (
                not isinstance(max_distance, int | float)
                or isinstance(max_distance, bool)
                or not math.isfinite(float(max_distance))
                or not 0 < float(max_distance) <= 4
            ):
                raise MinecraftActionInvalid("max_distance 必须在 0 到 4 之间")
            distance = float(max_distance)
        try:
            await self._ensure_runtime()
            return await self._client.attack(
                entity_id,
                expected_type.strip(),
                expected_uuid=uuid,
                max_distance=distance,
            )
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def pickup_item(
        self,
        entity_id: Any,
        expected_item: Any,
        *,
        entity_uuid: Any = None,
        expected_break_action: Any = None,
    ) -> dict[str, Any]:
        """走过去捡起**一个明确指定**的掉落物实体（Phase 4H · MEDIUM · 需要用户确认）。

        一次只捡一个实体：不捡附近所有掉落物、不自动挖、不自动 loot、不追超过上限的距离。
        返回启动即 ``RUNNING``；终态经事件送达（成功带 ``result`` 的 before/after）。
        """
        self._require_enabled()
        clean_id, clean_item = self.validate_pickup(entity_id, expected_item)
        clean_uuid: str | None = None
        if entity_uuid is not None:
            if not isinstance(entity_uuid, str):
                raise MinecraftActionInvalid("entity_uuid 必须是字符串")
            try:
                clean_uuid = str(UUID(entity_uuid))
            except (ValueError, AttributeError) as exc:
                raise MinecraftActionInvalid("entity_uuid 必须是实体 UUID") from exc
        extra: dict[str, Any] = {}
        if expected_break_action is not None:
            if (
                not isinstance(expected_break_action, str)
                or not 1 <= len(expected_break_action) <= 128
                or any(not 33 <= ord(ch) <= 126 for ch in expected_break_action)
                or clean_uuid is None
            ):
                raise MinecraftActionInvalid("expected_break_action 必须绑定有效动作与实体 UUID")
            extra["expected_break_action"] = expected_break_action
        try:
            await self._ensure_runtime()
            return await self._client.pickup_item(
                clean_id, clean_item, entity_uuid=clean_uuid, **extra
            )
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def recipe_lookup(self, item: Any, crafting_table: Any = None) -> dict[str, Any]:
        """查一个目标物品能做的配方（Phase 4F/4G · SAFE 只读）。

        * 不给 ``crafting_table`` → 只查**玩家自身 2×2**（4F 行为不变）；
        * 给了明确的 ``{x,y,z}`` → 查**那张工作台**的 3×3（坐标不合规 → 422；工作台不在 /
          不是工作台 / 太远 → 404/422，**绝不自动去找工作台**）。

        同步动作：直接返回语义投影（`recipe_id` / `result` / `requires_table` /
        `available` / `ingredients`，带工作台时额外带回坐标）。
        """
        self._require_enabled()
        clean, table = self.validate_recipe_lookup(item, crafting_table)
        try:
            await self._ensure_runtime()
            return await self._client.recipe_lookup(clean, table)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def craft(self, recipe_id: Any, crafting_table: Any = None) -> dict[str, Any]:
        """执行**一次**配方（Phase 4F/4G · MEDIUM · 需要用户确认）。

        * 不给 ``crafting_table`` → 玩家自身 2×2（4F 行为不变）；
        * 给了明确坐标 → 用**那张工作台**的 3×3（执行前会重新验证方块还在、还是工作台、
          距离够；被挖掉 → ``crafting_table_missing``，被换掉 → ``crafting_table_invalid``）。

        一次一个 recipe：不批量、不做 recipe chain、不自动准备材料、不自动放置工作台。
        返回启动即 ``RUNNING``；终态经事件送达（成功带 ``result`` 的 before/after）。
        """
        self._require_enabled()
        clean, table = self.validate_craft(recipe_id, crafting_table)
        try:
            await self._ensure_runtime()
            return await self._client.craft(clean, table)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    @staticmethod
    def validate_container_inspect(x: Any, y: Any, z: Any) -> dict[str, int]:
        """container_inspect 的参数校验（纯函数）：整数坐标 + 世界边界。"""
        coords: dict[str, int] = {}
        for name, value in (("x", x), ("y", y), ("z", z)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MinecraftActionInvalid(f"坐标 {name} 必须是整数（方块坐标没有小数）")
            coords[name] = int(value)
        if abs(coords["x"]) > 3.0e7 or abs(coords["z"]) > 3.0e7 or not -512 <= coords["y"] <= 2048:
            raise MinecraftActionInvalid("坐标超出 Minecraft 世界边界")
        return coords

    @staticmethod
    def validate_container_transfer(
        x: Any,
        y: Any,
        z: Any,
        direction: Any,
        container_slot: Any,
        inventory_slot: Any,
        item: Any,
        count: Any,
    ) -> tuple[dict[str, int], str, int, int, str, int]:
        """container_transfer 的参数校验（纯函数）。

        坐标是整数；direction ∈ withdraw/deposit；container_slot >= 0（真正上界由真实
        窗口大小决定，在 runtime 里校验）；inventory_slot 沿用 Phase 4D 的 9~44；
        count >= 1；item 非空。**先校验再进确认门**：垃圾参数不该挂出一条待确认。
        """
        coords = MinecraftService.validate_container_inspect(x, y, z)
        name = str(direction or "").strip().lower()
        if name not in CONTAINER_DIRECTIONS:
            raise MinecraftActionInvalid(f"direction 必须是 {'/'.join(CONTAINER_DIRECTIONS)} 之一")
        if isinstance(container_slot, bool) or not isinstance(container_slot, int):
            raise MinecraftActionInvalid("container_slot 必须是整数")
        if container_slot < 0:
            raise MinecraftActionInvalid("container_slot 不能是负数")
        if isinstance(inventory_slot, bool) or not isinstance(inventory_slot, int):
            raise MinecraftActionInvalid("inventory_slot 必须是整数")
        if not PLAYER_SLOT_MIN <= inventory_slot <= PLAYER_SLOT_MAX:
            raise MinecraftActionInvalid(
                f"inventory_slot 必须在 {PLAYER_SLOT_MIN}~{PLAYER_SLOT_MAX} 之间"
                "（主背包 9-35 + 快捷栏 36-44）"
            )
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise MinecraftActionInvalid("count 必须是 >= 1 的整数")
        if not isinstance(item, str) or not item.strip():
            raise MinecraftActionInvalid("item 不能为空")
        if len(item) > PLACE_MAX_ITEM_CHARS:
            raise MinecraftActionInvalid(f"item 最长 {PLACE_MAX_ITEM_CHARS} 个字符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in item):
            raise MinecraftActionInvalid("item 不能包含控制字符")
        return (
            coords,
            name,
            int(container_slot),
            int(inventory_slot),
            item.strip(),
            int(count),
        )

    async def container_inspect(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
        """读取一个 Chest / Barrel 的**真实内容**（Phase 4E · SAFE 只读）。

        同步动作：runtime 内部 open → read → close 后直接返回快照
        （``{ok, container:{type,label,position,size}, slots:[{slot,name,count}]}``）。
        SAFE 只代表"对支持的 normal chest/barrel 做只读 inspection"——
        打开窗口仍是独占的客户端状态，不与其他前台动作并发。
        """
        self._require_enabled()
        coords = self.validate_container_inspect(x, y, z)
        try:
            await self._ensure_runtime()
            return await self._client.container_inspect(coords["x"], coords["y"], coords["z"])
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def container_transfer(
        self,
        x: Any,
        y: Any,
        z: Any,
        direction: Any,
        container_slot: Any,
        inventory_slot: Any,
        item: Any,
        count: Any,
    ) -> dict[str, Any]:
        """把一个明确物品在「容器槽 ↔ 自己背包槽」之间移动一次（Phase 4E · MEDIUM）。

        一个方向、一个 container_slot、一个 inventory_slot、一个 item、一个 count；
        目标被别的物品占用时**拒绝**（绝不交换、绝不换槽）；只支持单方块 Chest / Barrel。
        返回启动即 ``RUNNING``，终态经事件送达（成功带 container/inventory 的 before/after）。
        """
        self._require_enabled()
        coords, direction_name, cslot, islot, clean_item, clean_count = (
            self.validate_container_transfer(
                x, y, z, direction, container_slot, inventory_slot, item, count
            )
        )
        try:
            await self._ensure_runtime()
            return await self._client.container_transfer(
                coords["x"],
                coords["y"],
                coords["z"],
                direction_name,
                cslot,
                islot,
                clean_item,
                clean_count,
            )
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    @staticmethod
    def validate_place(
        x: Any, y: Any, z: Any, face: Any, expected_item: Any
    ) -> tuple[dict[str, int], str, str]:
        """place 的参数校验（纯函数，抛 :class:`MinecraftActionInvalid`）。

        §十三：方块坐标必须是**整数**（100.5 直接拒绝）；§八：face 只允许六个值；
        §九：expected_item 是"主手必须拿着这个物品"的硬约束（非空字符串）。
        调用方先校验再进确认门：垃圾参数不该挂出一条待确认。
        """
        coords: dict[str, int] = {}
        for name, value in (("x", x), ("y", y), ("z", z)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MinecraftActionInvalid(f"坐标 {name} 必须是整数（方块坐标没有小数）")
            coords[name] = int(value)
        if abs(coords["x"]) > 3.0e7 or abs(coords["z"]) > 3.0e7 or not -512 <= coords["y"] <= 2048:
            raise MinecraftActionInvalid("坐标超出 Minecraft 世界边界")
        face_name = str(face or "").strip().lower()
        if face_name not in PLACE_FACES:
            raise MinecraftActionInvalid(f"face 必须是 {'/'.join(PLACE_FACES)} 之一")
        if not isinstance(expected_item, str) or not expected_item.strip():
            raise MinecraftActionInvalid("expected_item 不能为空（先看清主手拿着什么）")
        if len(expected_item) > PLACE_MAX_ITEM_CHARS:
            raise MinecraftActionInvalid(f"expected_item 最长 {PLACE_MAX_ITEM_CHARS} 个字符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in expected_item):
            raise MinecraftActionInvalid("expected_item 不能包含控制字符")
        return coords, face_name, expected_item.strip()

    async def place(self, x: Any, y: Any, z: Any, face: Any, expected_item: Any) -> dict[str, Any]:
        """放置**一个**明确指定的方块（Phase 4C · MEDIUM · 需要用户确认）。

        只做类型/格式校验与 runtime 调用；世界层面的校验（主手物品 / 目标是否空气 /
        参考方块 / 距离）由 runtime 在真正执行前用**实时状态**判定——确认是授权，不代替校验。
        返回 ``{action_id, action, status:"RUNNING"}``，终态经 action 事件送达。
        """
        self._require_enabled()
        coords, face_name, item = self.validate_place(x, y, z, face, expected_item)
        try:
            await self._ensure_runtime()
            return await self._client.place(coords["x"], coords["y"], coords["z"], face_name, item)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def dig(
        self,
        x: Any,
        y: Any,
        z: Any,
        expected_block: Any,
        expected_tool: Any = None,
        *,
        require_collection: bool = False,
        expected_empty_hand: bool = False,
    ) -> dict[str, Any]:
        """破坏**一个**明确指定的方块（Phase 4B · MEDIUM · 需要用户确认）。

        只做类型/格式校验与 runtime 调用；世界层面的校验（方块存在 / 与 expected_block 一致 /
        可挖 / 距离上限）由 runtime 在真正执行前用**实时状态**判定——确认是授权，不代替校验。
        Phase 4I：``expected_tool`` 可选，给了就要求**执行瞬间**主手拿着它
        （runtime 重新读 heldItem；不匹配 → ``minecraft.held_item_changed`` /
        ``minecraft.held_item_missing``；
        **绝不自动换工具**）。返回 ``{action_id, action, status:"RUNNING"}``，终态经 action
        事件送达（成功额外带 ``result.tool_expected`` / ``tool_actual``）。
        """
        self._require_enabled()
        coords, expected, tool, require_collection = self.validate_dig(
            x, y, z, expected_block, expected_tool, require_collection=require_collection
        )
        if not isinstance(expected_empty_hand, bool) or (expected_empty_hand and tool):
            raise MinecraftActionInvalid("expected_empty_hand 与 expected_tool 不得冲突")
        extra = {"expected_empty_hand": True} if expected_empty_hand else {}
        try:
            await self._ensure_runtime()
            return await self._client.dig(
                coords["x"],
                coords["y"],
                coords["z"],
                expected,
                tool,
                require_collection=require_collection,
                **extra,
            )
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def stop_action(
        self, action_id: str | None = None, stop_request_id: str | None = None
    ) -> dict[str, Any]:
        """安全停止（幂等）；传 action_id 时只取消该动作。

        runtime 不可达也按成功处理——硬停止入口永远不该失败（此时本来也没有
        可执行的动作）。
        """
        self._require_enabled()
        try:
            await self._ensure_runtime()
        except MinecraftRuntimeDown:
            # runtime 进程都没了 → 本来就没有可执行的动作；硬停止入口永远成功
            return {
                "ok": True,
                "status": "IDLE",
                "cancelled": [],
                "stop_confirmed": False,
                "stop_unknown": True,
                "stop_request_id": stop_request_id,
                "note": "runtime 不可达；停止未被确认",
            }
        try:
            return await self._client.stop(action_id, stop_request_id)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
                return {
                    "ok": True,
                    "status": "IDLE",
                    "cancelled": [],
                    "stop_confirmed": False,
                    "stop_unknown": True,
                    "stop_request_id": stop_request_id,
                    "note": "runtime 不可达；停止未被确认",
                }
            raise _translate(exc) from exc

    async def status(self) -> dict[str, Any]:
        """合并 runtime 实况与本地镜像；runtime 不可达时返回降级快照。"""
        try:
            live = await self._client.status()
            # Phase 7F.5.1 §二：显式状态查询的成功响应同样按统一协议处理身份字段。
            self._note_runtime_instance(live)
            self._runtime_down = False
            self._mirror = {key: value for key, value in live.items() if key != "ok"}
        except MinecraftRuntimeError:
            pass  # 保留镜像，快照里以 runtime_alive=False 呈现
        return self.snapshot()

    async def live_connection_status(self) -> dict[str, Any] | None:
        """只读直查 runtime；失败时不把可能过时的镜像当作当前在线证明。"""
        try:
            return await self._client.status()
        except MinecraftRuntimeError:
            return None

    def snapshot(self) -> dict[str, Any]:
        """同步只读投影（WebUI/QQ 兜底），不发网络请求。"""
        mirror = dict(self._mirror)
        return {
            "enabled": self.enabled,
            "auth_configured": self.auth_configured,
            "runtime": {
                "running": self._process.running if self._process else False,
                "pid": self._process.pid if self._process else None,
                "managed": self.config.auto_start_runtime,
                "restarts": self._restart_count,
                "down": self._runtime_down,
                "log_tail": list(self._process.log_tail)[-5:] if self._process else [],
            },
            "connection": {
                "status": mirror.get("status", "DISCONNECTED"),
                "session_id": mirror.get("session_id"),
                "host": mirror.get("host"),
                "port": mirror.get("port"),
                "username": mirror.get("username"),
                "auth_mode": mirror.get("auth_mode"),
                "dimension": mirror.get("dimension"),
                "position": mirror.get("position"),
                "health": mirror.get("health"),
                "last_error": mirror.get("last_error"),
                "kicked_reason": mirror.get("kicked_reason"),
                "connected_at": mirror.get("connected_at"),
            },
            # Phase 3B：当前/最近一次动作（IDLE 表示从未有动作或 runtime 未上报）
            "action": dict(mirror.get("action") or ACTION_IDLE),
            # Phase 3C：Pathfinder 诊断（goal 类型 / 目标坐标 / 是否在移动）
            "pathfinder": dict(mirror.get("pathfinder") or PATHFINDER_IDLE),
            "last_event": self._last_event,
        }

    # ----------------------------------------------------------- 事件接收/分发

    async def receive_event(self, raw: dict[str, Any]) -> MinecraftBridgeEvent:
        """WebUI 回调端点的落点：校验 → 镜像 → 分发。未知事件名抛 ValueError。"""
        event = parse_bridge_event(raw)
        identity = f"{event.data.get('username') or ''}:{event.data.get('uuid') or ''}"
        key = f"{event.type_name}:{event.session_id}:{event.timestamp}:{identity}"
        if key in self._recent_event_keys:
            return event  # runtime 的重试可能造成重复，静默去重
        self._recent_event_keys.append(key)
        self._last_event = raw
        self._apply_to_mirror(event)
        context = "".join(
            f" {key}={event.data[key]!r}"
            for key in ("username", "message", "reason", "error")
            if key in event.data
        )
        log.info(
            "[Minecraft] %s (session=%s%s)",
            event.type_name,
            event.session_id or "-",
            context,
        )
        metrics = getattr(self.bot, "metrics", None)
        if metrics is not None:
            metrics.inc(f"minecraft_{event.type_name.split('.', 1)[1]}_events")
        if event.type_name == "minecraft.disconnected" and self.perception is not None:
            # 离开世界：感知缓存整体作废（不留假在线状态）
            self.perception.invalidate()
        await self._notify_listeners(event)
        return event

    async def _notify_listeners(self, event: Any) -> None:
        """把事件分发给所有订阅者；单个订阅者故障不拖垮事件通道。"""
        for listener in list(self._listeners):
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 - 订阅者故障不拖垮事件通道
                log.exception("[Minecraft] 事件订阅者处理 %s 失败", event.type_name)

    def _player_session_key(self, username: str, uuid: str = "") -> tuple[str, str, str, str]:
        """服务器作用域 + 玩家名 + 可信 UUID；UUID 缺失也保留空槽（写任务会拒绝）。"""
        return (
            str(self._mirror.get("host") or ""),
            str(self._mirror.get("port") or ""),
            str(username or "").casefold(),
            str(uuid or ""),
        )

    def _session_keys_for_all(self) -> list[tuple[str, str, str, str]]:
        return list(self._player_session_current)

    def _session_keys_for(self, username: str) -> list[tuple[str, str, str, str]]:
        scope = (str(self._mirror.get("host") or ""), str(self._mirror.get("port") or ""))
        wanted = str(username or "").casefold()
        return [
            key
            for key in self._player_session_current
            if (str(key[0]), str(key[1])) == scope and key[2] == wanted
        ]

    def server_scope(self) -> str:
        return f"{self._mirror.get('host') or ''}:{self._mirror.get('port') or ''}"

    def player_uuid(self, username: str) -> str:
        keys = self._session_keys_for(username)
        if len(keys) != 1:
            return ""
        return str(self._player_session_uuid.get(keys[0]) or "")

    def player_session_binding(self, username: str) -> dict[str, Any]:
        """可信入口在任务准入时捕获的绑定；缺失/离线/未证明返回空。"""
        if self.attestation_required and not self._attestation_is_current(username):
            return {}
        ref = self.player_session_ref(username)
        if not ref:
            return {}
        keys = self._session_keys_for(username)
        if len(keys) != 1:
            return {}
        seen = float(self._player_session_seen.get(keys[0]) or 0.0)
        if seen <= 0 or (self._player_session_clock() - seen) > self.session_max_age_seconds:
            return {}
        uuid = self.player_uuid(username)
        if not uuid:
            return {}
        return {
            "actor_session_ref": ref,
            "player_uuid": uuid,
            "server_scope": self.server_scope(),
            "identity_assurance": (
                "server_attested"
                if self.attestation_required and username in self._attested
                else ("server_observed_uuid" if uuid else "server_observed_no_uuid")
            ),
            "captured_at": time.time(),
        }

    def player_session_ref(self, username: str) -> str:
        """当前玩家连接代次；离线/未观察到/同名多候选/Attested 未验证返回空。"""
        if self.attestation_required and not self._attestation_is_current(username):
            return ""
        keys = self._session_keys_for(username)
        if len(keys) != 1:
            return ""
        return str(self._player_session_current.get(keys[0]) or "")

    def player_session_is_fresh(self, username: str) -> bool:
        """统一的新鲜度判定；AgentBridge 和准入必须共用。"""
        if self.attestation_required and not self._attestation_is_current(username):
            return False
        keys = self._session_keys_for(username)
        if len(keys) != 1:
            return False
        seen = float(self._player_session_seen.get(keys[0]) or 0.0)
        if seen <= 0:
            return False
        return (self._player_session_clock() - seen) <= self.session_max_age_seconds

    def _revoke_all_identity(self, reason: str) -> None:
        """Drop every local actor session and attested proof after an identity reset."""
        snapshots = []
        for name in list(self._attested):
            snapshot = dict(self._attested[name])
            snapshot["username"] = name
            snapshot["reason"] = reason
            snapshots.append(snapshot)
        self._stop_attestation_refresh()
        self._attested.clear()
        self._player_session_current.clear()
        self._player_session_uuid.clear()
        self._player_session_runtime.clear()
        self._player_session_seen.clear()
        self._player_session_names.clear()
        for username in list(self._attest_generation):
            self._attest_generation[username] = self._attest_generation[username] + 1
        for snapshot in snapshots:
            self._notify_attested_revoked(snapshot)
        log.warning("[Minecraft] 身份证明已整体撤销：%s", reason)

    def _attestation_is_current(self, username: str) -> bool:
        """Single internal validity contract for every attested-facing accessor.

        Cache presence is never sufficient: the live local ref, UUID, runtime
        session and server scope must still equal the values captured when the
        HMAC proof was committed, and the proof must still be within TTL.
        """
        attested = self._attested.get(username)
        if attested is None:
            return False
        if self._runtime_down or str(self._mirror.get("status") or "") != "ONLINE":
            return False
        keys = self._session_keys_for(username)
        if len(keys) != 1:
            return False
        key = keys[0]
        if str(self._player_session_current.get(key) or "") != attested["actor_session_ref"]:
            return False
        if str(self._player_session_uuid.get(key) or "") != attested["player_uuid"]:
            return False
        if str(self._mirror.get("session_id") or "") != attested["runtime_session_id"]:
            return False
        if self.server_scope() != attested["server_scope"]:
            return False
        seen = float(self._player_session_seen.get(key) or 0.0)
        if seen <= 0:
            return False
        return (self._player_session_clock() - seen) <= self.session_max_age_seconds

    def _spawn_initial_attestation(self, username: str) -> None:
        """Fire one bounded first proof for a newly joined player (managed task)."""
        if not self.attestation_required or self.attestation_client is None:
            return
        existing = self._attest_bootstrap_tasks.get(username)
        if existing is not None and not existing.done():
            return
        if not username or username in self._attest_bootstrap_tasks:
            return
        try:
            task = asyncio.create_task(self._bootstrap_attestation(username))
        except RuntimeError:
            return
        self._attest_bootstrap_tasks[username] = task
        task.add_done_callback(lambda done: self._finish_bootstrap(username, done))

    def _finish_bootstrap(self, username: str, task: Any) -> None:
        if self._attest_bootstrap_tasks.get(username) is task:
            self._attest_bootstrap_tasks.pop(username, None)
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - bootstrap failure is fail-closed
            log.info("[Minecraft] 首次证明失败：%s", username)

    async def _bootstrap_attestation(self, username: str) -> None:
        try:
            await asyncio.wait_for(
                self.attest_player_session(username), timeout=self._attest_bootstrap_timeout
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - proof failure keeps writes denied
            return

    def _start_attestation_refresh(self) -> None:
        """Start the bounded, service-owned re-attestation loop (idempotent)."""
        if not self.attestation_required or self.attestation_client is None:
            return
        task = self._attest_refresh_task
        if task is not None and not task.done():
            return
        try:
            self._attest_refresh_task = asyncio.create_task(self._attestation_refresh_loop())
        except RuntimeError:
            # No running loop yet (sync assembly); the next join starts it.
            self._attest_refresh_task = None

    def _stop_attestation_refresh(self) -> Any:
        task = self._attest_refresh_task
        self._attest_refresh_task = None
        if task is not None and not task.done():
            task.cancel()
        return task

    async def _refresh_current_attested_players(self) -> None:
        if not self.attestation_required or self.attestation_client is None:
            return
        if self._runtime_down or str(self._mirror.get("status") or "") != "ONLINE":
            return
        names = sorted(
            {self._player_session_names.get(key, key[2]) for key in self._session_keys_for_all()}
        )
        for name in names:
            try:
                await self.attest_player_session(name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one player's proof failure is isolated
                log.info("[Minecraft] 续验跳过 %s: %s", name, type(exc).__name__)

    async def _attestation_refresh_loop(self) -> None:
        try:
            while self.attestation_required:
                await asyncio.sleep(self._attest_refresh_interval)
                await self._refresh_current_attested_players()
        except asyncio.CancelledError:
            return

    def configure_attestation(
        self,
        client: Any,
        *,
        required: bool = True,
        refresh_interval_seconds: float = 120.0,
        proof_max_age_seconds: float = 300.0,
    ) -> None:
        self.attestation_client = client
        self.attestation_required = bool(required)
        self._attest_refresh_interval = max(0.05, float(refresh_interval_seconds))
        self._attest_proof_max_age = max(1.0, float(proof_max_age_seconds))
        if proof_max_age_seconds < self.session_max_age_seconds:
            self.session_max_age_seconds = float(proof_max_age_seconds)
        # Mode switches revoke every existing attested binding; stale proofs must not
        # survive an upgrade or downgrade between EVENT_ONLY and SERVER_ATTESTED.
        self._revoke_all_identity("attestation_mode_changed")
        if self.attestation_required and client is not None:
            self._start_attestation_refresh()

    def current_attested_binding(self, username: str) -> dict[str, Any]:
        if self.attestation_required and not self._attestation_is_current(username):
            return {}
        attested = self._attested.get(username)
        return dict(attested) if attested else {}

    def _notify_attested_revoked(self, snapshot: dict[str, Any]) -> None:
        callback = getattr(self, "on_attested_identity_revoked", None)
        if callback is None:
            return
        try:
            result = callback(dict(snapshot))
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception:  # noqa: BLE001 - revocation notification is best effort
            log.exception("[Minecraft] attested revocation callback failed")

    def _revoke_attested_for(self, username: str, reason: str) -> bool:
        attested = self._attested.pop(username, None)
        self._attest_generation[username] = self._attest_generation.get(username, 0) + 1
        if attested is None:
            return False
        snapshot = dict(attested)
        snapshot["username"] = username
        snapshot["reason"] = reason
        self._notify_attested_revoked(snapshot)
        return True

    def invalidate_attested_session(self, username: str, reason: str = "invalid") -> None:
        self._revoke_attested_for(username, reason)

    async def attest_player_session(self, username: str) -> dict[str, Any]:
        """Verify a server session and bind it to the current local event session."""
        client = self.attestation_client
        if client is None:
            raise RuntimeError("attestation client is not configured")
        keys = self._session_keys_for(username)
        if len(keys) != 1:
            raise RuntimeError("attestation ambiguous session")
        local_key = keys[0]
        local_ref = str(self._player_session_current.get(local_key) or "")
        uuid = self.player_uuid(username)
        runtime_session = str(self._mirror.get("session_id") or "")
        scope = self.server_scope()
        if not local_ref or not uuid or not runtime_session or not scope:
            raise RuntimeError("attestation prerequisites missing")
        generation = self._attest_generation.get(username, 0) + 1
        self._attest_generation[username] = generation
        attested = await client.fetch_async(name=username, player_uuid=uuid)
        # Re-check all captured facts before committing; stale results are dropped.
        current_key = local_key
        current_ref = str(self._player_session_current.get(current_key) or "")
        if (
            self._attest_generation.get(username) != generation
            or current_ref != local_ref
            or str(self._mirror.get("session_id") or "") != runtime_session
            or self.server_scope() != scope
            or self.player_uuid(username) != uuid
        ):
            raise RuntimeError("attestation superseded")
        if attested.server_scope != scope:
            self.invalidate_attested_session(username, "scope_mismatch")
            raise RuntimeError("attestation scope mismatch")
        previous = self._attested.get(username)
        if previous is not None and (
            previous["server_session_ref"] != attested.session_ref
            or previous["boot_id"] != attested.boot_id
            or previous["server_scope"] != attested.server_scope
            or previous["runtime_session_id"] != runtime_session
        ):
            self._player_session_current.pop(current_key, None)
            self._player_session_uuid.pop(current_key, None)
            self._player_session_runtime.pop(current_key, None)
            self._player_session_seen.pop(current_key, None)
            self._revoke_attested_for(username, "server_session_changed")
            raise RuntimeError("server session changed")
        binding = {
            "username": username,
            "actor_session_ref": local_ref,
            "player_uuid": uuid,
            "server_scope": scope,
            "identity_assurance": "server_attested",
            "captured_at": time.time(),
            "server_session_ref": attested.session_ref,
            "server_session_seq": attested.session_seq,
            "boot_id": attested.boot_id,
            "runtime_session_id": runtime_session,
            "verified_at_monotonic": float(self._player_session_clock()),
            "generation": generation,
        }
        self._attested[username] = binding
        self._player_session_seen[local_key] = float(self._player_session_clock())
        return dict(binding)

    def player_session_ref_at(self, username: str, timestamp: float) -> str:
        """仅审计兼容：历史反查不再作为授权依据。"""
        chosen = ""
        keys = self._session_keys_for(username)
        if len(keys) != 1:
            return ""
        for key in keys:
            for at, ref in self._player_session_history.get(key) or []:
                if float(at) <= float(timestamp):
                    chosen = ref
        return chosen

    def _record_player_session_event(self, event: MinecraftBridgeEvent) -> None:
        if event.type_name not in {"minecraft.player_joined", "minecraft.player_left"}:
            return
        if str(self._mirror.get("status") or "") != "ONLINE" or self._runtime_down:
            return
        username = str(event.data.get("username") or "")
        if not username:
            return
        uuid = str(event.data.get("uuid") or "")
        event_runtime = str(getattr(event, "session_id", "") or "")
        current_runtime = str(self._mirror.get("session_id") or "")
        # 旧 runtime/session 的迟到 join/leave 不参与当前连接映射。
        if event_runtime and current_runtime and event_runtime != current_runtime:
            return
        if event.type_name == "minecraft.player_joined":
            key = self._player_session_key(username, uuid)
            existing = [
                item
                for item in self._session_keys_for(username)
                if self._player_session_uuid.get(item) == uuid
            ]
            if existing:
                old = existing[0]
                if self._player_session_runtime.get(old) == event_runtime:
                    return  # 重复 join
                self._player_session_current.pop(old, None)
                self._player_session_uuid.pop(old, None)
                self._player_session_runtime.pop(old, None)
            ref = f"ps_{secrets.token_hex(10)}"
            self._player_session_history.setdefault(key, []).append((float(event.timestamp), ref))
            self._player_session_current[key] = ref
            self._player_session_seen[key] = float(self._player_session_clock())
            self._player_session_uuid[key] = uuid
            self._player_session_runtime[key] = event_runtime
            self._player_session_names[key] = username
            if self.attestation_required:
                self._start_attestation_refresh()
                if self.auto_attest_on_join:
                    self._spawn_initial_attestation(username)
        else:
            key = self._player_session_key(username, uuid)
            target = key if key in self._player_session_current else None
            if target is None:
                for item in self._session_keys_for(username):
                    if self._player_session_uuid.get(item) == uuid:
                        target = item
                        break
            if target is None:
                return  # 迟到/重复 leave：找不到匹配连接，不能误删新连接
            if event_runtime and self._player_session_runtime.get(target) not in {
                "",
                event_runtime,
            }:
                return
            self._player_session_current.pop(target, None)
            self._player_session_uuid.pop(target, None)
            self._player_session_runtime.pop(target, None)
            self._player_session_seen.pop(target, None)
            self._player_session_names.pop(target, None)
            self._revoke_attested_for(username, "player_left")

    def _apply_to_mirror(self, event: MinecraftBridgeEvent) -> None:
        name = event.type_name
        # A different runtime session is a different connection generation: its
        # lifecycle callbacks must not mutate the current mirror.  The previous
        # generation is only reopened after disconnected/spawned has cleared the
        # mirror session id.
        event_session = str(getattr(event, "session_id", "") or "")
        current_session = str(self._mirror.get("session_id") or "")
        if (
            event_session
            and current_session
            and event_session != current_session
            and name
            in {
                "minecraft.connecting",
                "minecraft.connected",
                "minecraft.spawned",
                "minecraft.kicked",
                "minecraft.disconnected",
                "minecraft.player_joined",
                "minecraft.player_left",
            }
        ):
            log.info("[Minecraft] 忽略旧 Runtime 事件：%s session=%s", name, event_session)
            return
        self._record_player_session_event(event)
        if name == "minecraft.connecting":
            self._mirror["status"] = "CONNECTING"
        elif name == "minecraft.connected":
            self._mirror["status"] = "CONNECTED"
            self._mirror["username"] = event.data.get("username")
        elif name == "minecraft.spawned":
            self._runtime_down = False
            if event.session_id:
                self._mirror["session_id"] = event.session_id
            self._mirror["status"] = "ONLINE"
            self._mirror["username"] = event.data.get("username", self._mirror.get("username"))
            self._mirror["connected_at"] = event.timestamp
        elif name == "minecraft.kicked":
            self._mirror["status"] = "DISCONNECTING"
            self._mirror["kicked_reason"] = event.data.get("reason")
        elif name == "minecraft.disconnected":
            self._mirror["status"] = "DISCONNECTED"
            self._mirror["session_id"] = None
            self._mirror["dimension"] = None
            self._mirror["position"] = None
            self._mirror["health"] = None
            self._player_session_current.clear()
            self._player_session_uuid.clear()
            self._player_session_runtime.clear()
            self._player_session_seen.clear()
            self._attested.clear()
        elif name == "minecraft.error":
            self._mirror["last_error"] = event.data.get("error")
        elif name.startswith("minecraft.action."):
            self._apply_action_event(event)

    def _apply_action_event(self, event: MinecraftBridgeEvent) -> None:
        """Phase 3B：动作生命周期镜像（started/终态 → WebUI 的 Current Action）。

        Phase 3E：终态额外保留 ``result`` 与 ``error``/``code``——move_to 等持续型
        动作的终点位置与失败原因只在这里出现（HTTP 早已返回 RUNNING）。
        """
        data = event.data
        self._mirror["action"] = {
            "action": data.get("action"),
            "action_id": data.get("action_id"),
            "status": _ACTION_EVENT_STATUSES.get(event.type_name, "IDLE"),
            "started_at": data.get("started_at"),
            "finished_at": (
                None if event.type_name == "minecraft.action.started" else event.timestamp
            ),
            "elapsed_ms": data.get("elapsed_ms"),
            "result": data.get("result") if isinstance(data.get("result"), dict) else None,
            "error": data.get("error"),
            "code": data.get("code"),
        }

    # ----------------------------------------------------------------- 对账轮询

    def _mark_runtime_down(self, reason: str) -> None:
        if self._runtime_down:
            return
        self._runtime_down = True
        self._revoke_all_identity("runtime_down")
        self._mirror["session_id"] = None
        log.warning("[Minecraft] runtime 不可达：%s", reason)
        asyncio.create_task(
            self._dispatch_synthetic(
                "minecraft.error", {"error": f"Bridge runtime 不可达：{reason}"}
            )
        )

    async def _dispatch_synthetic(self, event_name: str, data: dict[str, Any]) -> None:
        try:
            await self.receive_event(
                {
                    "event": event_name,
                    "session_id": self._mirror.get("session_id"),
                    "timestamp": self._clock(),
                    **data,
                }
            )
        except Exception:  # noqa: BLE001
            log.exception("[Minecraft] 合成事件分发失败")

    async def _poll_loop(self) -> None:
        failures = 0
        while True:
            await asyncio.sleep(self.config.poll_interval_seconds)
            if not self._started:
                return
            try:
                live = await self._client.status()
                # Phase 7F.5.1 §二：周期轮询的成功响应也是身份响应 —— 外部托管 Runtime
                # 换实例时，这里是**主动**发现变化的正常路径（不必等用户操作或 TTL）。
                self._note_runtime_instance(live)
            except Exception:  # noqa: BLE001 - 轮询永不抛出
                failures += 1
                if failures == 1:
                    self._mark_runtime_down("状态轮询连续失败")
                if (
                    failures >= 2
                    and self.config.auto_start_runtime
                    and self._restart_count < self.config.max_runtime_restarts
                ):
                    try:
                        await self._start_runtime()
                        failures = 0
                        await self._dispatch_synthetic(
                            "minecraft.disconnected", {"reason": "runtime 进程已重启"}
                        )
                    except Exception:  # noqa: BLE001
                        log.warning("[Minecraft] runtime 重启失败，稍后重试")
                continue
            failures = 0
            previous = self._mirror.get("status")
            self._mirror = {key: value for key, value in live.items() if key != "ok"}
            current = str(live.get("status") or "DISCONNECTED")
            if previous != current:
                log.info("[Minecraft] 状态对账：%s → %s", previous, current)
                # R1：状态对账同样是感知失效的入口。此前只有 connected/disconnected 事
                # 件会让缓存作废；一旦事件丢失（回调失败、WebUI 未启用），缓存会一直
                # available=True 且 age 无限增长，旧位置/旧玩家就会被当成'此刻的事实'。
                if self.perception is not None and current != "ONLINE":
                    self.perception.invalidate()

    # ------------------------------------------------------------- 世界感知(P2)

    async def _perception_loop(self) -> None:
        """按层节拍轮询 Raw World Snapshot；差异事件经去抖后分发。"""
        assert self.perception is not None
        while True:
            await asyncio.sleep(self.perception.next_due_in())
            if not self._started:
                return
            if self._mirror.get("status") != "ONLINE":
                # 不在世界里：感知无事可做，拉长节拍待命（进世界后 ≤1s 内开扫）
                self.perception.defer(1.0)
                continue
            try:
                due = self.perception.due_layers()
                if not due:
                    continue
                events = await self.perception.poll(due)
                for name, data in events:
                    await self._dispatch_world_event(name, data)
                # 窗口位移/大跨度重定位只做内部记录（Phase 3A §七：不进 LLM 上下文）
                diff = self.perception.last_diff
                if diff is not None and diff.kind in (WINDOW_SHIFT, TELEPORT_REBASE):
                    log.info(
                        "[Minecraft] 观察窗口%s（shift=%s，位移柱 %d，重叠 %d）——不计为世界变化",
                        "重定位" if diff.kind == TELEPORT_REBASE else "位移",
                        diff.shift,
                        diff.shifted_blocks,
                        diff.overlap_blocks,
                    )
            except asyncio.CancelledError:
                raise
            except MinecraftRuntimeError:
                # runtime 不可达由状态对账轮询统一处理，这里安静等下一拍
                await asyncio.sleep(1.0)
            except Exception:  # noqa: BLE001 - 感知故障绝不拖垮连接层
                log.exception("[Minecraft] 世界感知轮询失败")
                await asyncio.sleep(1.0)

    async def _dispatch_world_event(self, name: str, data: dict[str, Any]) -> None:
        from app.integrations.minecraft.events import parse_world_event

        try:
            event = parse_world_event(name, data, self._clock())
        except ValueError:
            log.warning("[Minecraft] 忽略未知感知事件：%s", name)
            return
        log.info("[Minecraft] %s %s", event.type_name, data)
        metrics = getattr(self.bot, "metrics", None)
        if metrics is not None:
            metrics.inc(
                f"minecraft_{event.type_name.removeprefix('minecraft.').replace('.', '_')}_events"
            )
        await self._notify_listeners(event)

    def world_view(self) -> dict[str, Any]:
        """API / 只读工具的世界视图（语义模型 + 缓存元信息 + raw）。"""
        if self.perception is None:
            return {
                "available": False,
                "online": False,
                "reason": (
                    "world perception disabled" if self.config.enabled else "minecraft disabled"
                ),
            }
        return self.perception.view()

    # ------------------------------------------------------------------- misc

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise MinecraftDisabled()
