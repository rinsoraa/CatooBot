"""World Perception & Semantic World Model（Phase 2 · 只读「眼睛」）。

数据流（与任务书一致，Mineflayer 原始对象绝不进 LLM）::

    Minecraft Runtime  GET /minecraft/world/snapshot
      ↓ Raw World Snapshot（事实数据，本模块的 Raw* 模型）
    WorldPerception（分层缓存 + 语义映射 + 差异事件，本模块）
      ↓ Semantic World Model（面向对话/Agent 的世界理解数据）
    只读工具 / WebUI World Debug / 未来的 Phase 3 行动层

硬约束：

* **语义层不从无中发明对象**——每个语义条目都能追溯到一个 raw 条目（测试保证）；
* **空气不进上下文**——柱面表层扫描在 runtime 侧完成，raw 里本来就没有空气柱；
* **relative_direction 由系统计算**——runtime 侧算好，Python 不再从坐标推断；
* **断开即失效**——bot 离线时缓存整体作废，绝不保留假在线状态。
"""

from __future__ import annotations

from collections.abc import Callable
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
    model["players"] = [
        {
            "name": player.username,
            "direction": player.relative_direction,
            "distance": round(player.distance, 1),
            "compass": player.compass,
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
    """分层世界状态缓存。不同层允许不同刷新周期；断开整体作废。"""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._online = False
        self._raw: RawSnapshot | None = None
        self._layer_at: dict[str, float] = {}

    @property
    def online(self) -> bool:
        return self._online

    def update(self, raw: RawSnapshot, layers: set[str]) -> None:
        self._online = bool(raw.online)
        if not raw.online:
            self.invalidate()
            return
        self._raw = raw
        now = self._clock()
        for layer in layers:
            self._layer_at[layer] = now

    def invalidate(self) -> None:
        """bot 离开世界：缓存整体作废，绝不保留假在线状态。"""
        self._online = False
        self._raw = None
        self._layer_at.clear()

    def layer_age(self, layer: str) -> float | None:
        fetched = self._layer_at.get(layer)
        return None if fetched is None else max(0.0, self._clock() - fetched)

    @property
    def raw(self) -> RawSnapshot | None:
        return self._raw

    def view(self) -> dict[str, Any]:
        """API/工具用的缓存元信息。"""
        raw = self._raw
        return {
            "online": self._online,
            "captured_at": raw.fetched_at if raw else None,
            "age_seconds": self.layer_age("near"),
            "layers": {
                layer: {"age_seconds": self.layer_age(layer)}
                for layer in ("near", "local", "extended")
            },
        }


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
        self._prev_near_signature: dict[str, str] | None = None
        self._prev_environment: dict[str, Any] | None = None
        self._primed = False

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
        events = self._diff(raw)
        return events

    def invalidate(self) -> None:
        self.cache.invalidate()
        self._reset_baseline()

    def _reset_baseline(self) -> None:
        self._primed = False
        self._prev_players.clear()
        self._prev_entity_types.clear()
        self._prev_poi_names.clear()
        self._prev_near_signature = None
        self._prev_environment = None

    # ------------------------------------------------------------------- events

    def _allow(self, event_type: str) -> bool:
        now = self._clock()
        last = self._last_event_at.get(event_type)
        if last is not None and now - last < self._cooldown:
            return False
        self._last_event_at[event_type] = now
        return True

    def _diff(self, raw: RawSnapshot) -> list[tuple[str, dict[str, Any]]]:
        events: list[tuple[str, dict[str, Any]]] = []
        usernames = {player.username for player in raw.players}
        entity_types = {entity.type for entity in raw.entities}
        poi_names = {entry.name for entry in raw.blocks.interesting}

        if not self._primed:
            # 首帧只建立基线，避免上线瞬间的感知风暴。
            self._primed = True
            self._prev_players = usernames
            self._prev_entity_types = entity_types
            self._prev_poi_names = poi_names
            self._prev_near_signature = self._near_signature(raw)
            self._prev_environment = raw.environment.model_dump() if raw.environment else None
            return events

        new_players = usernames - self._prev_players
        gone_players = self._prev_players - usernames
        if new_players and self._allow("minecraft.player.nearby"):
            events.append(
                (
                    "minecraft.player.nearby",
                    {
                        "usernames": sorted(new_players),
                        "players": raw.players[:5]
                        and [
                            {
                                "name": p.username,
                                "direction": p.relative_direction,
                                "distance": round(p.distance, 1),
                            }
                            for p in raw.players
                            if p.username in new_players
                        ],
                    },
                )
            )
        if gone_players and self._allow("minecraft.player.left_area"):
            events.append(("minecraft.player.left_area", {"usernames": sorted(gone_players)}))

        new_entities = entity_types - self._prev_entity_types
        if new_entities and self._allow("minecraft.entity.discovered"):
            counts = {t: sum(1 for e in raw.entities if e.type == t) for t in new_entities}
            events.append(("minecraft.entity.discovered", {"types": counts}))
        gone_entities = self._prev_entity_types - entity_types
        if gone_entities and self._allow("minecraft.entity.left_area"):
            events.append(("minecraft.entity.left_area", {"types": sorted(gone_entities)}))

        new_pois = poi_names - self._prev_poi_names
        if new_pois and self._allow("minecraft.poi.discovered"):
            events.append(("minecraft.poi.discovered", {"types": sorted(new_pois)[:10]}))

        # world.changed：方块变化数量达标 或 环境相位变化，聚合成一条带摘要的事件
        signature = self._near_signature(raw)
        changed = 0
        if self._prev_near_signature is not None:
            changed = sum(
                1 for key, name in signature.items() if self._prev_near_signature.get(key) != name
            )
        environment = raw.environment.model_dump() if raw.environment else None
        env_changed = self._prev_environment is not None and environment != self._prev_environment
        if (changed >= self._change_threshold or env_changed) and self._allow(
            "minecraft.world.changed"
        ):
            events.append(
                (
                    "minecraft.world.changed",
                    {
                        "changed_blocks": changed,
                        "environment_changed": env_changed,
                        "environment": environment,
                    },
                )
            )

        self._prev_players = usernames
        self._prev_entity_types = entity_types
        self._prev_poi_names = poi_names
        self._prev_near_signature = signature
        self._prev_environment = environment
        return events

    def _near_signature(self, raw: RawSnapshot) -> dict[str, str]:
        """近层地表签名：世界坐标 → 方块名，用于统计真实变化量。"""
        if raw.blocks.near is None:
            return {}
        return {
            f"{entry.pos.x},{entry.pos.y},{entry.pos.z}": entry.name
            for entry in raw.blocks.near.columns
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
        if raw is not None and self.cache.online:
            view["semantic"] = build_semantic_model(raw)
            view["raw"] = raw.model_dump(by_alias=True)
        else:
            view["semantic"] = None
            view["raw"] = None
        return view
