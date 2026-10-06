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
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config.settings import PROJECT_ROOT, MinecraftConfig
from app.integrations.minecraft.events import MinecraftBridgeEvent, parse_bridge_event
from app.integrations.minecraft.runtime_client import (
    MinecraftRuntimeClient,
    MinecraftRuntimeError,
)
from app.integrations.minecraft.world import TELEPORT_REBASE, WINDOW_SHIFT, WorldPerception
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.core.bot import Bot

log = get_logger("CatooBot.Minecraft")

Listener = Callable[[MinecraftBridgeEvent], Any]


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
        self._client = MinecraftRuntimeClient(
            f"http://127.0.0.1:{config.runtime_port}", timeout=config.request_timeout_seconds
        )
        self._process: _RuntimeProcess | None = None
        self._listeners: list[Listener] = []
        self._poll_task: asyncio.Task[None] | None = None
        self._perception_task: asyncio.Task[None] | None = None
        self.perception: WorldPerception | None = None
        self._started = False
        self._restart_count = 0
        self._runtime_down = False
        # 状态镜像：来自事件回调 + 对账轮询，绝不自行发明状态
        self._mirror: dict[str, Any] = {"status": "DISCONNECTED"}
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
        if not self.callback_url:
            self.callback_url = self._default_callback_url()
        try:
            if self.config.auto_start_runtime:
                await self._start_runtime()
            else:
                log.info("[Minecraft] runtime 由外部托管（auto_start_runtime=false）")
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

    async def stop(self) -> None:
        """Bot.shutdown 时调用：断开 bot（尽力）、停轮询、回收 runtime 进程。"""
        await self._cleanup()

    async def _cleanup(self) -> None:
        self._started = False
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
        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.disconnect(), timeout=3.0)
            except Exception:  # noqa: BLE001 - 停机路径尽力而为
                pass
            await self._client.close()
        if self._process is not None:
            await self._process.stop()
            self._process = None

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
        # Phase 4B：dig 的安全门（超时/距离）随进程环境注入 runtime
        env["MC_DIG_TIMEOUT_MS"] = str(int(self.config.action.dig.timeout * 1000))
        env["MC_DIG_MAX_DISTANCE"] = str(self.config.action.dig.max_distance)
        # Phase 4C：place 的安全门
        env["MC_PLACE_TIMEOUT_MS"] = str(int(self.config.action.place.timeout * 1000))
        env["MC_PLACE_MAX_DISTANCE"] = str(self.config.action.place.max_distance)
        # Phase 4F：crafting（玩家 2×2）的超时
        env["MC_CRAFT_TIMEOUT_MS"] = str(int(self.config.action.craft.timeout * 1000))
        env["MC_RECIPE_LOOKUP_TIMEOUT_MS"] = str(
            int(self.config.action.recipe_lookup.timeout * 1000)
        )
        # Phase 4E：container 的安全门（超时/距离）
        env["MC_CONTAINER_TIMEOUT_MS"] = str(int(self.config.action.container.timeout * 1000))
        env["MC_CONTAINER_MAX_DISTANCE"] = str(self.config.action.container.max_distance)
        # Phase 4D：背包写操作的超时
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
                await self._client.health()
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
            await self._client.health()
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
            raise _translate(exc) from exc

    @staticmethod
    def validate_dig(x: Any, y: Any, z: Any, expected_block: Any) -> tuple[dict[str, float], str]:
        """dig 的参数校验（纯函数，抛 :class:`MinecraftActionInvalid`）。

        调用方（WebUI 调试端点）先校验再进确认门：垃圾参数不该挂出一条待确认。
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
        return coords, expected_block.strip()

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
    def validate_recipe_lookup(item: Any) -> str:
        """recipe_lookup 的参数校验（纯函数）：物品名非空、长度受限、无控制字符。"""
        if not isinstance(item, str) or not item.strip():
            raise MinecraftActionInvalid("item 不能为空（要查什么物品的配方）")
        if len(item) > PLACE_MAX_ITEM_CHARS:
            raise MinecraftActionInvalid(f"item 最长 {PLACE_MAX_ITEM_CHARS} 个字符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in item):
            raise MinecraftActionInvalid("item 不能包含控制字符")
        return item.strip()

    @staticmethod
    def validate_craft(recipe_id: Any) -> str:
        """craft 的参数校验（纯函数）：只接受 recipe_lookup 给出的稳定 recipe_id。

        recipe_id 是可读的规范签名（``stick*4=oak_planks*2``，需要工作台的带 ``!`` 前缀），
        所以这里只做形状校验；**是否存在 / 现在能不能做**由 runtime 用当前配方表与当前背包判定。
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
        return clean

    async def recipe_lookup(self, item: Any) -> dict[str, Any]:
        """查一个目标物品在**玩家自身 2×2** 里能做的配方（Phase 4F · SAFE 只读）。

        同步动作：直接返回语义投影（`recipe_id` / `result` / `requires_table` /
        `available` / `ingredients`）；只有工作台配方时返回
        ``status="crafting_table_required"``（**绝不自动去找工作台**）。
        """
        self._require_enabled()
        clean = self.validate_recipe_lookup(item)
        try:
            await self._ensure_runtime()
            return await self._client.recipe_lookup(clean)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def craft(self, recipe_id: Any) -> dict[str, Any]:
        """执行**一次** 2×2 配方（Phase 4F · MEDIUM · 需要用户确认）。

        一次一个 recipe：不批量、不做 recipe chain、不自动准备材料、不碰工作台。
        返回启动即 ``RUNNING``；终态经事件送达（成功带 ``result`` 的 before/after）。
        """
        self._require_enabled()
        clean = self.validate_craft(recipe_id)
        try:
            await self._ensure_runtime()
            return await self._client.craft(clean)
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

    async def dig(self, x: Any, y: Any, z: Any, expected_block: Any) -> dict[str, Any]:
        """破坏**一个**明确指定的方块（Phase 4B · MEDIUM · 需要用户确认）。

        只做类型/格式校验与 runtime 调用；世界层面的校验（方块存在 / 与 expected_block 一致 /
        可挖 / 距离上限）由 runtime 在真正执行前用**实时状态**判定——确认是授权，不代替校验。
        返回 ``{action_id, action, status:"RUNNING"}``，终态经 action 事件送达
        （成功带 ``result{position, block_before, block_after}``）。
        """
        self._require_enabled()
        coords, expected = self.validate_dig(x, y, z, expected_block)
        try:
            await self._ensure_runtime()
            return await self._client.dig(coords["x"], coords["y"], coords["z"], expected)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def stop_action(self) -> dict[str, Any]:
        """最高优先级安全停止（幂等）：取消进行中动作，返回被取消的 action_id 列表。

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
                "note": "runtime 不可达，视为已停止",
            }
        try:
            return await self._client.stop()
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
                return {
                    "ok": True,
                    "status": "IDLE",
                    "cancelled": [],
                    "note": "runtime 不可达，视为已停止",
                }
            raise _translate(exc) from exc

    async def status(self) -> dict[str, Any]:
        """合并 runtime 实况与本地镜像；runtime 不可达时返回降级快照。"""
        try:
            live = await self._client.status()
            self._runtime_down = False
            self._mirror = {key: value for key, value in live.items() if key != "ok"}
        except MinecraftRuntimeError:
            pass  # 保留镜像，快照里以 runtime_alive=False 呈现
        return self.snapshot()

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
        key = f"{event.type_name}:{event.session_id}:{event.timestamp}"
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

    def _apply_to_mirror(self, event: MinecraftBridgeEvent) -> None:
        name = event.type_name
        if name == "minecraft.connecting":
            self._mirror["status"] = "CONNECTING"
        elif name == "minecraft.connected":
            self._mirror["status"] = "CONNECTED"
            self._mirror["username"] = event.data.get("username")
        elif name == "minecraft.spawned":
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
