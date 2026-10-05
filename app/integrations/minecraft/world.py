"""World Perception & Semantic World Model（Phase 2 / 2.1 / 3A · 只读「眼睛」）。

数据流（与任务书一致，Mineflayer 原始对象绝不进 LLM）::

    Minecraft Runtime  GET /minecraft/world/snapshot
      ↓ Raw World Snapshot（事实数据，本模块的 Raw* 模型）
    WorldPerception（分层缓存 + 语义映射 + movement-aware 差异事件，本模块）
      ↓ Semantic World Model（面向对话/Agent 的世界理解数据）
    只读工具 / WebUI World Debug / 未来的 Phase 3B 行动层

硬约束：

* **语义层不从无中发明对象**——每个语义条目都能追溯到一个 raw 条目（测试保证）；
* **空气不进上下文**——柱面表层扫描在 runtime 侧完成，raw 里本来就没有空气柱；
* **relative_direction 由系统计算**——runtime 侧算好，Python 不再从坐标推断；
* **断开即失效**——bot 离线时缓存整体作废，绝不保留假在线状态；
* **窗口位移 ≠ 世界变化**（Phase 3A）——near 差异以观测锚点对齐后只在重叠区域比对，
  移动造成的方块进入/离开窗口永远不计入 world.changed。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ------------------------------------------------------------------- raw models


class WorldPos(BaseModel):
    x: float
    y: float
    z: float


class Rel3(BaseModel):
    dx: float
    dy: float
    dz: float


class SelfState(BaseModel):
    position: WorldPos | None = None
    yaw: float = 0.0
    pitch: float = 0.0
    dimension: str | None = None
    health: float | None = None
    food: float | None = None
    game_mode: str | None = None
    held_item: str | None = None


class RelativeObject(BaseModel):
    """玩家/实体/方块的公共空间形态：世界坐标 + 相对坐标 + 系统计算的方向。"""

    model_config = ConfigDict(extra="ignore")

    pos: WorldPos | None = None
    rel: Rel3 | None = None
    distance: float = 0.0
    bearing: float = 0.0
    relative_direction: str = "front"
    compass: str = "north"


class RelativePlayer(RelativeObject):
    username: str = ""


class RelativeEntity(RelativeObject):
    type: str = ""
    kind: str | None = None


class BlockEntry(RelativeObject):
    name: str = ""
    biome: str | None = None


class BlockLayer(BaseModel):
    radius: int = 0
    step: int = 1
    columns: list[BlockEntry] = Field(default_factory=list)


class ExtendedLayer(BaseModel):
    radius: int = 0
    points: list[BlockEntry] = Field(default_factory=list)


class BlocksSection(BaseModel):
    near: BlockLayer | None = None
    local: BlockLayer | None = None
    extended: ExtendedLayer | None = None
    interesting: list[BlockEntry] = Field(default_factory=list)


class Environment(BaseModel):
    biome: str | None = None
    time_of_day_ticks: float | None = None
    time_phase: str | None = None
    weather: str | None = None
    light: float | None = None
    dimension: str | None = None


class RawSnapshot(BaseModel):
    """runtime 返回的事实数据。``self`` 字段名与 JSON 对齐（alias）。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    online: bool = False
    fetched_at: float = 0.0
    self_state: SelfState | None = Field(default=None, alias="self")
    players: list[RelativePlayer] = Field(default_factory=list)
    entities: list[RelativeEntity] = Field(default_factory=list)
    environment: Environment | None = None
    blocks: BlocksSection = Field(default_factory=BlocksSection)


def parse_raw_snapshot(payload: dict[str, Any]) -> RawSnapshot:
    """校验 runtime 的 snapshot 载荷；坏载荷按 ValueError 处理（调用方记录日志）。"""
    if not isinstance(payload, dict):
        raise ValueError("world snapshot 载荷必须是 JSON 对象")
    if payload.get("online") is False:
        return RawSnapshot(online=False, fetched_at=float(payload.get("fetched_at") or 0.0))
    try:
        return RawSnapshot.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - 统一转 ValueError
        raise ValueError(f"world snapshot 校验失败：{exc}") from exc


# ---------------------------------------------------------------- semantic model

#: 方块名 → 地形类别（语义层的 terrain 聚合用；小而克制，覆盖自然地表即可）
_TERRAIN_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("water",), "water"),
    (("_log", "_leaves"), "forest"),
    (("grass_block", "tall_grass", "fern", "moss_block", "moss_carpet"), "grassland"),
    (("dirt", "podzol", "coarse_dirt", "rooted_dirt", "mud"), "dirt"),
    (("sand", "sandstone", "red_sand", "gravel"), "sand"),
    (("stone", "andesite", "granite", "diorite", "deepslate", "tuff", "cobblestone"), "stone"),
    (("snow", "ice", "packed_ice", "blue_ice", "frosted_ice"), "snow"),
    (("farmland", "_wheat", "_carrots", "_potatoes", "beetroots", "_path"), "farmland"),
    (("_planks", "bricks", "stone_bricks", "deepslate_tiles", "concrete"), "built"),
)


def terrain_category(name: str) -> str | None:
    lowered = name.lower()
    for keywords, category in _TERRAIN_RULES:
        if any(lowered == keyword or lowered.endswith(keyword) for keyword in keywords):
            return category
    return None


def build_semantic_model(raw: RawSnapshot) -> dict[str, Any]:
    """Raw Snapshot → Semantic World Model。纯函数、无 IO、无发明。

    terrain 来自 local/extended 柱面的类别×方位聚合；players/entities/POI
    直接来自 raw 列表；一切方向字段沿用 runtime 计算的 relative_direction/compass。
    """
    model: dict[str, Any] = {"captured_at": raw.fetched_at}

    if raw.self_state is not None:
        model["self"] = {
            "location": raw.environment.biome if raw.environment else None,
            "dimension": raw.self_state.dimension,
            "position": raw.self_state.position.model_dump() if raw.self_state.position else None,
            "health": raw.self_state.health,
            "food": raw.self_state.food,
            "game_mode": raw.self_state.game_mode,
            "held_item": raw.self_state.held_item,
            "yaw": raw.self_state.yaw,
        }
    if raw.environment is not None:
        model["environment"] = raw.environment.model_dump()

    # ---- terrain：类别 × 罗盘方位 聚合（local 优先，extended 补充远处）
    buckets: dict[tuple[str, str], list[float]] = {}
    for layer in (raw.blocks.local, raw.blocks.extended, raw.blocks.near):
        columns: list[BlockEntry] = []
        if isinstance(layer, BlockLayer):
            columns = layer.columns
        elif isinstance(layer, ExtendedLayer):
            columns = layer.points
        for entry in columns:
            category = terrain_category(entry.name)
            if category is None:
                continue
            bucket = buckets.setdefault((category, entry.compass), [])
            bucket.append(entry.distance)
    terrain = [
        {
            "type": category,
            "direction": compass,
            "distance": round(sum(distances) / len(distances), 1),
            "samples": len(distances),
        }
        for (category, compass), distances in sorted(
            buckets.items(), key=lambda item: -len(item[1])
        )[:8]
    ]
    model["terrain"] = terrain

    # ---- players（原样投影，方向沿用 runtime 计算）
    # Phase 3E：带上世界坐标——「罐头你过来」要靠它算出移动目标（§四十一），
    # 方向/距离仍由 runtime 计算，绝不让模型自己从坐标推断方位。
    model["players"] = [
        {
            "name": player.username,
            "direction": player.relative_direction,
            "distance": round(player.distance, 1),
            "compass": player.compass,
            "position": (
                {
                    "x": round(player.pos.x, 1),
                    "y": round(player.pos.y, 1),
                    "z": round(player.pos.z, 1),
                }
                if player.pos is not None
                else None
            ),
        }
        for player in raw.players[:20]
    ]

    # ---- entities：按类型聚合计数，方向取该类型出现最多的罗盘方位
    entity_groups: dict[str, dict[str, Any]] = {}
    for entity in raw.entities:
        group = entity_groups.setdefault(entity.type, {"count": 0, "compass": {}, "nearest": 1e9})
        group["count"] += 1
        group["compass"][entity.compass] = group["compass"].get(entity.compass, 0) + 1
        group["nearest"] = min(group["nearest"], entity.distance)
    model["entities"] = [
        {
            "type": entity_type,
            "count": group["count"],
            "direction": max(group["compass"], key=group["compass"].get)
            if group["compass"]
            else None,
            "distance": round(group["nearest"], 1),
        }
        for entity_type, group in sorted(
            entity_groups.items(), key=lambda item: item[1]["nearest"]
        )[:12]
    ]

    # ---- points of interest：功能方块/矿石等，就近排序
    pois: dict[str, BlockEntry] = {}
    for entry in list(raw.blocks.interesting) + [
        column
        for column in (raw.blocks.near.columns if raw.blocks.near else [])
        if terrain_category(column.name) is None
    ]:
        key = f"{entry.name}@{entry.pos.x},{entry.pos.y},{entry.pos.z}" if entry.pos else entry.name
        pois.setdefault(key, entry)
    model["points_of_interest"] = [
        {
            "type": entry.name,
            "direction": entry.relative_direction,
            "distance": round(entry.distance, 1),
            "compass": entry.compass,
            "pos": entry.pos.model_dump() if entry.pos else None,
        }
        for entry in sorted(pois.values(), key=lambda item: item.distance)[:10]
    ]

    return model


# ------------------------------------------------------------------- state cache


class WorldStateCache:
    """分层世界状态缓存：每一层独立维护，partial snapshot 绝不覆盖其它层。

    关键不变量（Phase 2.1 审计修复）：

    * ``near`` 更新只替换 near；``local`` 更新替换 local + interesting；
      ``extended`` 更新只替换 extended；
    * 动态状态（self / players / entities / environment）取最新成功 snapshot；
    * 每层有独立的 fetched_at：拉 near 不刷新 local / extended 的 age；
    * 断开整体作废，不留假在线状态。
    """

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._online = False
        self._self_state: SelfState | None = None
        self._players: list[RelativePlayer] = []
        self._entities: list[RelativeEntity] = []
        self._environment: Environment | None = None
        self._near: BlockLayer | None = None
        self._local: BlockLayer | None = None
        self._extended: ExtendedLayer | None = None
        self._interesting: list[BlockEntry] = []
        #: near 层拍摄时的观测锚点（Phase 3A）：与 near 数据同帧存储，
        #: 因为窗口对齐必须用「那一帧扫描时的中心」，不能被其它层的帧刷新。
        self._near_anchor: tuple[int, int] | None = None
        self._layer_at: dict[str, float] = {}
        self._fetched_at: float = 0.0

    @property
    def online(self) -> bool:
        return self._online

    @property
    def near_anchor(self) -> tuple[int, int] | None:
        """最近一次 near 快照的窗口锚点（floor(x), floor(z)）；无 near 数据为 None。"""
        return self._near_anchor

    def update(self, raw: RawSnapshot, layers: set[str]) -> None:
        """合并一次 snapshot：只覆盖本次请求的层，其余层原样保留。"""
        self._online = bool(raw.online)
        if not raw.online:
            self.invalidate()
            return
        self._fetched_at = raw.fetched_at or self._clock()
        # 动态状态：最新成功 snapshot 覆盖（runtime 每帧都带这些段）
        if raw.self_state is not None:
            self._self_state = raw.self_state
        self._players = list(raw.players)
        self._entities = list(raw.entities)
        if raw.environment is not None:
            self._environment = raw.environment
        now = self._clock()
        blocks = raw.blocks
        if "near" in layers and blocks.near is not None:
            self._near = blocks.near
            self._near_anchor = observation_anchor(raw.self_state)
            self._layer_at["near"] = now
        if "local" in layers:
            if blocks.local is not None:
                self._local = blocks.local
            self._interesting = list(blocks.interesting)
            self._layer_at["local"] = now
        if "extended" in layers and blocks.extended is not None:
            self._extended = blocks.extended
            self._layer_at["extended"] = now

    def invalidate(self) -> None:
        """bot 离开世界：缓存整体作废，绝不保留假在线状态。"""
        self._online = False
        self._self_state = None
        self._players = []
        self._entities = []
        self._environment = None
        self._near = None
        self._local = None
        self._extended = None
        self._interesting = []
        self._near_anchor = None
        self._layer_at.clear()
        self._fetched_at = 0.0

    def layer_age(self, layer: str) -> float | None:
        fetched = self._layer_at.get(layer)
        return None if fetched is None else max(0.0, self._clock() - fetched)

    @property
    def raw(self) -> RawSnapshot | None:
        """合并视图：各层 + 最新动态状态拼回一份 RawSnapshot（语义模型/调试用）。

        分层事实仍保存在各自字段里；这里只是只读投影，不负责任何写入。
        """
        if not self._online:
            return None
        return RawSnapshot(
            online=True,
            fetched_at=self._fetched_at,
            self=self._self_state,  # 字段名 self_state，alias 是 self（mypy 按 alias 校验）
            players=list(self._players),
            entities=list(self._entities),
            environment=self._environment,
            blocks=BlocksSection(
                near=self._near,
                local=self._local,
                extended=self._extended,
                interesting=list(self._interesting),
            ),
        )

    def view(self) -> dict[str, Any]:
        """API/工具用的缓存元信息。"""
        return {
            "online": self._online,
            "captured_at": self._fetched_at if self._online else None,
            "age_seconds": self.layer_age("near"),
            "layers": {
                layer: {"age_seconds": self.layer_age(layer)}
                for layer in ("near", "local", "extended")
            },
        }


#: 环境语义签名的字段：只包含真正的环境变化。
#: 刻意排除 time_of_day_ticks（每 tick 都在变）与精确 light（会闪烁）——
#: 拿它们做比较会让 world.changed 变成常驻噪声（Phase 2.1 修复 2）。
ENVIRONMENT_SIGNATURE_FIELDS = ("biome", "time_phase", "weather", "dimension")


def environment_signature(environment: Environment | None) -> dict[str, Any] | None:
    """环境签名：biome / time_phase / weather / dimension（不含 ticks / light）。"""
    if environment is None:
        return None
    return {field: getattr(environment, field) for field in ENVIRONMENT_SIGNATURE_FIELDS}


# ----------------------------------------------------- movement-aware near diff（Phase 3A）

#: near 差异分类（任务书 §六）：至少区分这四种。
NO_MOVEMENT = "NO_MOVEMENT"
WINDOW_SHIFT = "WINDOW_SHIFT"
WORLD_CHANGE = "WORLD_CHANGE"
TELEPORT_REBASE = "TELEPORT_REBASE"

#: 两个观察窗口的有效重叠低于这个比例即视为「没有有效重叠」→ rebase。
#: 半径 6（13×13 柱）时约等于位移 ≥8 格：旧窗口几乎全部离开视野。
REBASE_MIN_OVERLAP_RATIO = 0.2


@dataclass(frozen=True)
class WorldDiff:
    """一次 near 差异的分类结果（Phase 3A）。

    * ``shifted_blocks``：窗口进入/离开的柱数——位移噪声，**永远不计**为世界变化；
    * ``changed_blocks``：重叠区域内的增/改/删；
    * ``overlap_blocks``：重叠区域内两侧都存在的柱数（可比对量）；
    * ``shift``：本帧相对上一帧的锚点位移（方块坐标，整数格）。
    """

    kind: str
    shifted_blocks: int = 0
    changed_blocks: int = 0
    overlap_blocks: int = 0
    shift: tuple[int, int] = (0, 0)


def observation_anchor(self_state: SelfState | None) -> tuple[int, int] | None:
    """观测锚点：罐头所在方块坐标 ``(floor(x), floor(z))``。

    Near 扫描是以自身位置为中心的水平柱面窗口（runtime 里柱坐标 =
    floor(self.x) + dx，dx 为整数），所以窗口对齐只要 X/Z 方块坐标；
    不使用连续浮点坐标作为位移量（任务书 §三）。
    """
    if self_state is None or self_state.position is None:
        return None
    return (math.floor(self_state.position.x), math.floor(self_state.position.z))


def _window_rect(anchor: tuple[int, int], radius: int) -> set[tuple[int, int]]:
    """锚点 + 半径对应的窗口柱坐标集合（13×13 上限，小常数）。"""
    ax, az = anchor
    return {
        (ax + dx, az + dz) for dx in range(-radius, radius + 1) for dz in range(-radius, radius + 1)
    }


def _effective_radius(radius: int | None, keys: Any, anchor: tuple[int, int]) -> int:
    """窗口半径：优先用 runtime 上报值；缺失时退化为密钥包围盒半径。"""
    if radius and radius > 0:
        return int(radius)
    if not keys:
        return 0
    ax, az = anchor
    return max(max(abs(x - ax), abs(z - az)) for x, z in keys)


def compute_near_diff(
    *,
    previous: dict[tuple[int, int], str],
    previous_anchor: tuple[int, int],
    previous_radius: int | None,
    current: dict[tuple[int, int], str],
    current_anchor: tuple[int, int],
    current_radius: int | None,
    change_threshold: int,
) -> WorldDiff:
    """对齐两个观察窗口，只把**重叠区域**的真实变化计为世界变化。

    算法（O(N+M)，只有 key 对齐与集合差，无 O(N²)、无全世界扫描）：

    1. 签名键**本来就是绝对世界坐标**，所以「映射到世界坐标系」是恒等的：
       对齐 = 取两个窗口矩形（锚点 ± 半径）的交集，交集外的柱才是窗口噪声；
       （不要平移上一帧的键——那会拿相邻方块互相比较，真实变化会被邻位抵消。）
    2. 有效重叠比例低于 ``REBASE_MIN_OVERLAP_RATIO`` → ``TELEPORT_REBASE``
       （旧基线作废，由调用方用当前帧重建）；
    3. 逐个重叠柱比较名字：增（缺→有）、删（有→缺）、改（名不同）都计入
       ``changed_blocks``——这就是 Phase 2.1 的增删改语义，只是被限制在重叠区内；
    4. 只在单侧出现且落在重叠区之外的柱 = 窗口进入/离开 → 计入 ``shifted_blocks``，
       **绝不**计入世界变化（情况 C / Test 5）。

    分类 k：rebase → TELEPORT_REBASE；``changed_blocks >= threshold`` → WORLD_CHANGE；
    有位移 → WINDOW_SHIFT；否则 NO_MOVEMENT。
    """
    dx = current_anchor[0] - previous_anchor[0]
    dz = current_anchor[1] - previous_anchor[1]
    # 1) 窗口矩形与有效重叠（世界坐标）
    prev_rect = _window_rect(
        previous_anchor, _effective_radius(previous_radius, previous, previous_anchor)
    )
    curr_rect = _window_rect(
        current_anchor, _effective_radius(current_radius, current, current_anchor)
    )
    common = prev_rect & curr_rect
    overlap_ratio = len(common) / max(1, min(len(prev_rect), len(curr_rect)))
    if overlap_ratio < REBASE_MIN_OVERLAP_RATIO:
        return WorldDiff(
            kind=TELEPORT_REBASE,
            shifted_blocks=len(previous) + len(current),
            changed_blocks=0,
            overlap_blocks=0,
            shift=(dx, dz),
        )
    # 2) 重叠区内的增/改/删
    previous_keys = set(previous)
    current_keys = set(current)
    changed = 0
    overlap = 0
    for key in previous_keys | current_keys:
        if key not in common:
            continue
        before = previous.get(key)
        after = current.get(key)
        if before is not None and after is not None:
            overlap += 1
        if before != after:
            changed += 1
    # 3) 窗口进入 / 离开（重叠区之外的单侧密钥）
    shifted = sum(1 for key in current_keys if key not in previous_keys and key not in common)
    shifted += sum(1 for key in previous_keys if key not in current_keys and key not in common)
    if changed >= change_threshold:
        kind = WORLD_CHANGE
    elif dx != 0 or dz != 0:
        kind = WINDOW_SHIFT
    else:
        kind = NO_MOVEMENT
    return WorldDiff(
        kind=kind,
        shifted_blocks=shifted,
        changed_blocks=changed,
        overlap_blocks=overlap,
        shift=(dx, dz),
    )


# --------------------------------------------------------------- perception diff


class WorldPerception:
    """感知引擎：按层轮询 snapshot → 缓存 → 语义模型 → 差异事件（去抖）。"""

    def __init__(
        self,
        *,
        client: Any,
        clock: Callable[[], float],
        near_interval: float,
        local_interval: float,
        extended_interval: float,
        event_cooldown: float,
        change_block_threshold: int,
        dispatch: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        self._client = client
        self._clock = clock
        self._intervals = {
            "near": near_interval,
            "local": local_interval,
            "extended": extended_interval,
        }
        self._cooldown = event_cooldown
        self._change_threshold = change_block_threshold
        self._dispatch = dispatch
        #: 离线时的退避（秒）：不在世界里的轮询绝不允许 0 间隔空转
        self._offline_retry_seconds = 2.0
        self.cache = WorldStateCache(clock)
        self._due: dict[str, float] = {layer: 0.0 for layer in self._intervals}
        self._last_event_at: dict[str, float] = {}
        self._prev_players: set[str] = set()
        self._prev_entity_types: set[str] = set()
        self._prev_poi_names: set[str] = set()
        self._prev_near_signature: dict[tuple[int, int], str] | None = None
        #: 与 _prev_near_signature 配套的观测窗口锚点/半径（Phase 3A）：
        #: 窗口对齐必须用「上一帧 near 拍摄时的中心」，不能用最新帧的位置。
        self._prev_near_anchor: tuple[int, int] | None = None
        self._prev_near_radius: int | None = None
        self._prev_environment: dict[str, Any] | None = None
        self._primed = False
        #: 最近一次可比对的 near 差异分类（debug / 事件系统用，不进 LLM 上下文）
        self.last_diff: WorldDiff | None = None

    # ------------------------------------------------------------------ polling

    def due_layers(self) -> set[str]:
        now = self._clock()
        due = {layer for layer, at in self._due.items() if now >= at}
        return due

    def next_due_in(self) -> float:
        now = self._clock()
        return max(0.05, min(self._due.values()) - now)

    def defer(self, seconds: float) -> None:
        """把所有层的下次到期时间推后（离线/待命时用，避免空转）。"""
        now = self._clock()
        for layer in self._due:
            self._due[layer] = now + seconds

    async def poll(self, layers: set[str]) -> list[tuple[str, dict[str, Any]]]:
        """拉取一层或多层 snapshot；返回本轮应当分发的事件列表。"""
        if not layers:
            return []
        payload = await self._client.world_snapshot(",".join(sorted(layers)))
        raw = parse_raw_snapshot(payload)
        if not raw.online:
            if self.cache.online:
                self.cache.invalidate()
                self._reset_baseline()
            # 离线：本轮不算数，退避后再试（否则 due 永远到期 → 忙等打 runtime）
            self.defer(self._offline_retry_seconds)
            return []
        self.cache.update(raw, layers)
        for layer in layers:
            self._due[layer] = self._clock() + self._intervals[layer]
        # 差异必须基于合并视图：本次只拉了一层时，其它层仍以缓存里的旧值为准，
        # 否则 partial poll 会看到空的 blocks.near → 幽灵变化（Phase 2.1）
        merged = self.cache.raw
        if merged is None:  # 理论上到不了这里（update 已置 online）
            return []
        return self._diff(merged)

    def invalidate(self) -> None:
        self.cache.invalidate()
        self._reset_baseline()

    def _reset_baseline(self) -> None:
        self._primed = False
        self._prev_players.clear()
        self._prev_entity_types.clear()
        self._prev_poi_names.clear()
        self._prev_near_signature = None
        self._prev_near_anchor = None
        self._prev_near_radius = None
        self._prev_environment = None
        self.last_diff = None

    # ------------------------------------------------------------------- events

    def _allow(self, event_type: str) -> bool:
        now = self._clock()
        last = self._last_event_at.get(event_type)
        if last is not None and now - last < self._cooldown:
            return False
        self._last_event_at[event_type] = now
        return True

    def _diff(self, merged: RawSnapshot) -> list[tuple[str, dict[str, Any]]]:
        """对合并视图做差异（不是对本次请求的 raw）：partial poll 不产生幽灵变化。"""
        events: list[tuple[str, dict[str, Any]]] = []
        self.last_diff = None
        usernames = {player.username for player in merged.players}
        entity_types = {entity.type for entity in merged.entities}
        poi_names = {entry.name for entry in merged.blocks.interesting}
        # 有 near 层数据才谈方块签名；没有就保持 None（不做比较、不更新基线）
        signature = self._near_signature(merged) if merged.blocks.near is not None else None
        anchor = self.cache.near_anchor
        radius = merged.blocks.near.radius if merged.blocks.near is not None else None
        environment = environment_signature(merged.environment)

        if not self._primed:
            # 首帧只建立基线，避免上线瞬间的感知风暴。
            self._primed = True
            self._prev_players = usernames
            self._prev_entity_types = entity_types
            self._prev_poi_names = poi_names
            self._prev_near_signature = signature
            self._prev_near_anchor = anchor
            self._prev_near_radius = radius
            self._prev_environment = environment
            return events

        new_players = usernames - self._prev_players
        gone_players = self._prev_players - usernames
        if new_players and self._allow("minecraft.player.nearby"):
            events.append(
                (
                    "minecraft.player.nearby",
                    {
                        "usernames": sorted(new_players),
                        "players": [
                            {
                                "name": p.username,
                                "direction": p.relative_direction,
                                "distance": round(p.distance, 1),
                            }
                            for p in merged.players
                            if p.username in new_players
                        ][:5],
                    },
                )
            )
        if gone_players and self._allow("minecraft.player.left_area"):
            events.append(("minecraft.player.left_area", {"usernames": sorted(gone_players)}))

        new_entities = entity_types - self._prev_entity_types
        if new_entities and self._allow("minecraft.entity.discovered"):
            counts = {t: sum(1 for e in merged.entities if e.type == t) for t in new_entities}
            events.append(("minecraft.entity.discovered", {"types": counts}))
        gone_entities = self._prev_entity_types - entity_types
        if gone_entities and self._allow("minecraft.entity.left_area"):
            events.append(("minecraft.entity.left_area", {"types": sorted(gone_entities)}))

        new_pois = poi_names - self._prev_poi_names
        if new_pois and self._allow("minecraft.poi.discovered"):
            events.append(("minecraft.poi.discovered", {"types": sorted(new_pois)[:10]}))

        # world.changed：movement-aware near 差异（Phase 3A）＋环境语义签名。
        # 窗口位移（进入/离开的柱）绝不计入 changed_blocks；只有重叠区域内的
        # 增/改/删才可能触发事件（TELEPORT_REBASE 时整体不触发，重建基线）。
        comparable = (
            signature is not None
            and anchor is not None
            and self._prev_near_signature is not None
            and self._prev_near_anchor is not None
        )
        if comparable:
            assert signature is not None and anchor is not None  # for type checkers
            assert self._prev_near_signature is not None
            assert self._prev_near_anchor is not None
            diff = compute_near_diff(
                previous=self._prev_near_signature,
                previous_anchor=self._prev_near_anchor,
                previous_radius=self._prev_near_radius,
                current=signature,
                current_anchor=anchor,
                current_radius=radius,
                change_threshold=self._change_threshold,
            )
            self.last_diff = diff
            env_changed = (
                environment is not None
                and self._prev_environment is not None
                and environment != self._prev_environment
            )
            if (
                diff.kind != TELEPORT_REBASE
                and (diff.kind == WORLD_CHANGE or env_changed)
                and self._allow("minecraft.world.changed")
            ):
                events.append(
                    (
                        "minecraft.world.changed",
                        {
                            "changed_blocks": diff.changed_blocks,
                            "environment_changed": env_changed,
                            "environment": environment,
                            # 差异分类只用于调试/事件系统，不要求进 LLM 上下文
                            "near_diff": {
                                "kind": diff.kind,
                                "shifted_blocks": diff.shifted_blocks,
                                "overlap_blocks": diff.overlap_blocks,
                                "shift": list(diff.shift),
                            },
                        },
                    )
                )

        self._prev_players = usernames
        self._prev_entity_types = entity_types
        self._prev_poi_names = poi_names
        if signature is not None:
            # rebase 与「首次拿到 near」都走这里：当前帧即新基线
            self._prev_near_signature = signature
            self._prev_near_anchor = anchor
            self._prev_near_radius = radius
        if environment is not None:
            self._prev_environment = environment
        return events

    def _near_signature(self, merged: RawSnapshot) -> dict[tuple[int, int], str]:
        """近层柱面签名：方块坐标 ``(x, z)`` → 该柱垂直窗口内最高非空气方块名。

        Phase 3A：键只取水平坐标（near 是水平柱面窗口）——垂直移动/跳跃
        不会因为窗口顶部裁切改变键而制造虚假变化（Test 8）。
        """
        if merged.blocks.near is None:
            return {}
        return {
            (int(entry.pos.x), int(entry.pos.z)): entry.name
            for entry in merged.blocks.near.columns
            if entry.pos
        }

    # --------------------------------------------------------------------- view

    def view(self) -> dict[str, Any]:
        """API/工具用的世界视图：语义模型 + 缓存元信息 + raw 原文。"""
        raw = self.cache.raw
        view: dict[str, Any] = {
            "available": self.cache.online and raw is not None,
            **self.cache.view(),
        }
        if self.last_diff is not None:
            view["last_diff"] = {
                "kind": self.last_diff.kind,
                "shifted_blocks": self.last_diff.shifted_blocks,
                "changed_blocks": self.last_diff.changed_blocks,
                "overlap_blocks": self.last_diff.overlap_blocks,
                "shift": list(self.last_diff.shift),
            }
        if raw is not None and self.cache.online:
            view["semantic"] = build_semantic_model(raw)
            view["raw"] = raw.model_dump(by_alias=True)
        else:
            view["semantic"] = None
            view["raw"] = None
        return view
