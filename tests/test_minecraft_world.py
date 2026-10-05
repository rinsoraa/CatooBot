"""World Perception 单元测试（Phase 2 + 2.1）：语义映射、分层缓存 merge、
环境签名差异、方块增删检测、缓存失效、事件去抖。"""

from __future__ import annotations

import pytest

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.service import MinecraftService
from app.integrations.minecraft.world import (
    NO_MOVEMENT,
    TELEPORT_REBASE,
    WINDOW_SHIFT,
    WORLD_CHANGE,
    WorldPerception,
    WorldStateCache,
    build_semantic_model,
    environment_signature,
    parse_raw_snapshot,
    terrain_category,
)


def make_clock(start: float = 1000.0):
    state = {"now": start}

    def clock() -> float:
        return state["now"]

    def advance(seconds: float) -> None:
        state["now"] += seconds

    return clock, advance


def raw_payload(**overrides) -> dict:
    """一份最小但合法的 raw snapshot（flying-squid 形状）。"""
    payload: dict = {
        "ok": True,
        "online": True,
        "fetched_at": 1000.0,
        "self": {
            "position": {"x": 10.0, "y": 64.0, "z": -5.0},
            "yaw": 90.0,
            "pitch": 0.0,
            "dimension": "overworld",
            "health": 20,
            "food": 20,
            "game_mode": "survival",
            "held_item": None,
        },
        "players": [],
        "entities": [],
        "environment": {
            "biome": "plains",
            "time_of_day_ticks": 1000,
            "time_phase": "day",
            "weather": "clear",
            "light": 15,
            "dimension": "overworld",
        },
        "blocks": {
            "near": {
                "radius": 6,
                "step": 1,
                "columns": [
                    {
                        "name": "grass_block",
                        "rel": {"dx": 0, "dy": -1, "dz": -2},
                        "pos": {"x": 10, "y": 63, "z": -7},
                        "distance": 2.2,
                        "bearing": 0.0,
                        "relative_direction": "front",
                        "compass": "north",
                    },
                    {
                        "name": "oak_log",
                        "rel": {"dx": 3, "dy": 0, "dz": 0},
                        "pos": {"x": 13, "y": 64, "z": -5},
                        "distance": 3.0,
                        "bearing": 90.0,
                        "relative_direction": "right",
                        "compass": "east",
                    },
                ],
            },
            "local": {
                "radius": 32,
                "step": 8,
                "columns": [
                    {
                        "name": "water",
                        "rel": {"dx": -16, "dy": -2, "dz": 0},
                        "pos": {"x": -6, "y": 62, "z": -5},
                        "distance": 16.1,
                        "bearing": -90.0,
                        "relative_direction": "left",
                        "compass": "west",
                    }
                ],
            },
            "extended": {
                "radius": 96,
                "points": [
                    {
                        "name": "sand",
                        "rel": {"dx": 48, "dy": 0, "dz": 0},
                        "pos": {"x": 58, "y": 64, "z": -5},
                        "distance": 48.0,
                        "bearing": 90.0,
                        "relative_direction": "right",
                        "compass": "east",
                        "biome": "desert",
                    }
                ],
            },
            "interesting": [
                {
                    "name": "crafting_table",
                    "rel": {"dx": 2, "dy": 0, "dz": 0},
                    "pos": {"x": 12, "y": 64, "z": -5},
                    "distance": 2.0,
                    "bearing": 90.0,
                    "relative_direction": "right",
                    "compass": "east",
                }
            ],
        },
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------- parsing


def test_parse_raw_snapshot_offline_and_invalid():
    offline = parse_raw_snapshot({"ok": True, "online": False, "fetched_at": 1.0})
    assert offline.online is False
    with pytest.raises(ValueError):
        parse_raw_snapshot("not-a-dict")
    with pytest.raises(ValueError):
        parse_raw_snapshot({"ok": True, "online": True, "self": {"position": "bad"}})


def test_terrain_category_mapping():
    assert terrain_category("grass_block") == "grassland"
    assert terrain_category("oak_log") == "forest"
    assert terrain_category("birch_leaves") == "forest"
    assert terrain_category("water") == "water"
    assert terrain_category("crafting_table") is None  # 功能方块不是地形


def test_semantic_model_aggregates_without_fabrication():
    raw = parse_raw_snapshot(raw_payload())
    semantic = build_semantic_model(raw)

    assert semantic["self"]["location"] == "plains"
    assert semantic["self"]["dimension"] == "overworld"
    # terrain 聚合自 raw 柱面：grassland(1) / forest(1) / water(1) 全部可追溯
    terrain_types = {item["type"] for item in semantic["terrain"]}
    assert {"grassland", "forest", "water"} <= terrain_types
    assert all(item["direction"] for item in semantic["terrain"])
    # players 原样投影
    assert semantic["players"] == []
    # entities 为空
    assert semantic["entities"] == []
    # POI：crafting_table 来自 raw.interesting
    assert semantic["points_of_interest"][0]["type"] == "crafting_table"
    assert semantic["points_of_interest"][0]["direction"] == "right"


def test_semantic_model_groups_entities_by_type():
    payload = raw_payload()
    for i in range(3):
        payload["entities"].append(
            {
                "type": "cow",
                "kind": "mob",
                "pos": {"x": 5 + i, "y": 64, "z": -10},
                "rel": {"dx": -5 - i, "dy": 0, "dz": -5},
                "distance": 7.0 + i,
                "bearing": 30.0,
                "relative_direction": "front_right",
                "compass": "west",
            }
        )
    semantic = build_semantic_model(parse_raw_snapshot(payload))
    cows = next(item for item in semantic["entities"] if item["type"] == "cow")
    assert cows["count"] == 3
    assert cows["direction"] == "west"
    assert cows["distance"] == 7.0  # 最近的一头


# ------------------------------------------------------------------ cache / invalidation


def test_cache_invalidate_on_disconnect():
    clock, _ = make_clock()
    cache_raw = parse_raw_snapshot(raw_payload())
    state_cache = WorldStateCache(clock)
    state_cache.update(cache_raw, {"near", "local"})
    assert state_cache.online is True
    merged = state_cache.raw
    assert merged is not None and merged.online is True
    assert merged.blocks.near is not None and merged.blocks.local is not None

    # bot 断开：整体作废，不留假在线状态（Test 10）
    state_cache.invalidate()
    assert state_cache.online is False
    assert state_cache.raw is None
    assert state_cache.layer_age("near") is None


async def test_service_receives_disconnect_invalidates_perception():
    """service.receive_event(disconnected) 必须令感知缓存失效（Test 10）。"""
    clock, advance = make_clock()
    perception, _client, _events = make_perception(clock, advance)
    await perception.poll({"near"})
    assert perception.cache.online is True

    service = MinecraftService(None, MinecraftConfig(enabled=True, auto_start_runtime=False))
    service.perception = perception
    await service.receive_event(
        {"event": "minecraft.disconnected", "session_id": "s", "timestamp": 1.0}
    )
    assert perception.cache.online is False
    assert perception.view()["available"] is False


# ------------------------------------------------------------------ diff events


def make_perception(clock, advance, **overrides):
    events_out: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self) -> None:
            self.payload: dict = raw_payload()

        async def world_snapshot(self, layers: str) -> dict:
            return self.payload

    client = FakeClient()
    kwargs = dict(
        client=client,
        clock=clock,
        near_interval=1.0,
        local_interval=4.0,
        extended_interval=20.0,
        event_cooldown=5.0,
        change_block_threshold=10,
        dispatch=lambda name, data: events_out.append((name, data)),
    )
    kwargs.update(overrides)
    perception = WorldPerception(**kwargs)
    return perception, client, events_out


async def test_first_poll_primes_without_events():
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    got = await perception.poll({"near"})
    assert got == [] and events == []  # 首帧只建基线
    assert perception.cache.online is True


async def test_player_events_with_cooldown():
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    await perception.poll({"near"})  # prime

    advance(2.0)
    client.payload = raw_payload(
        players=[
            {
                "username": "RinsoraNeko",
                "pos": {"x": 12, "y": 64, "z": -5},
                "rel": {"dx": 2, "dy": 0, "dz": 0},
                "distance": 2.0,
                "bearing": 90.0,
                "relative_direction": "right",
                "compass": "east",
            }
        ]
    )
    events = await perception.poll({"near"})
    names = [name for name, _ in events]
    assert "minecraft.player.nearby" in names
    payload = dict(events)["minecraft.player.nearby"]
    assert payload["usernames"] == ["RinsoraNeko"]

    # 冷却期内同样的事件不再发（去抖）
    advance(1.0)
    client.payload = raw_payload(
        players=[
            {
                "username": "空凛",
                "pos": {"x": 12, "y": 64, "z": -5},
                "rel": {"dx": 2, "dy": 0, "dz": 0},
                "distance": 2.0,
                "bearing": 90.0,
                "relative_direction": "right",
                "compass": "east",
            }
        ]
    )
    events = await perception.poll({"near"})
    assert all(name != "minecraft.player.nearby" for name, _ in events)


async def test_world_changed_requires_block_threshold():
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    await perception.poll({"near"})

    advance(2.0)
    # 只变 1 个方块：低于阈值 → 无事件
    client.payload = raw_payload()
    client.payload["blocks"]["near"]["columns"][0]["name"] = "stone"
    events = await perception.poll({"near"})
    assert all(name != "minecraft.world.changed" for name, _ in events)

    advance(6.0)  # 越过冷却
    # 整片变化（>10）：聚合成一条 world.changed（绝无逐方块风暴）。
    # 坐标必须落在 near 窗口内（anchor=(10,-5)、radius=6 → x∈[4,16]、z∈[-11,1]），
    # 与 runtime 的真实输出一致；窗口外的柱属于位移噪声（Phase 3A）。
    columns = client.payload["blocks"]["near"]["columns"]
    for i in range(12):
        columns.append(
            {
                "name": "cobblestone",
                "rel": {"dx": i, "dy": -1, "dz": 3},
                "pos": {"x": 4 + i, "y": 63, "z": 1},
                "distance": 3.0 + i,
                "bearing": 0.0,
                "relative_direction": "front",
                "compass": "north",
            }
        )
    events = await perception.poll({"near"})
    changed = [data for name, data in events if name == "minecraft.world.changed"]
    assert len(changed) == 1
    assert changed[0]["changed_blocks"] >= 10


async def test_offline_snapshot_invalidates_cache():
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    await perception.poll({"near"})
    assert perception.cache.online is True

    advance(2.0)
    client.payload = {"ok": True, "online": False, "fetched_at": 1002.0}
    await perception.poll({"near"})
    assert perception.cache.online is False
    assert perception.view()["available"] is False


# =========================================== Phase 2.1：partial layer merge（修复 1）


def _payload_with_layers(*layers: str) -> dict:
    """runtime 的 partial 载荷形状：只带请求的层（其余 blocks 键缺失）。"""
    payload = raw_payload()
    blocks = payload["blocks"]
    kept: dict = {}
    if "near" in layers:
        kept["near"] = blocks["near"]
    if "local" in layers:
        kept["local"] = blocks["local"]
        kept["interesting"] = blocks["interesting"]
    if "extended" in layers:
        kept["extended"] = blocks["extended"]
    payload["blocks"] = kept
    return payload


def test_partial_near_update_preserves_local_and_extended():
    """near 更新只能覆盖 near：local/interesting/extended 必须原样保留。"""
    clock, advance = make_clock()
    cache = WorldStateCache(clock)
    cache.update(parse_raw_snapshot(raw_payload()), {"near", "local", "extended"})
    advance(1.0)

    cache.update(parse_raw_snapshot(_payload_with_layers("near")), {"near"})

    merged = cache.raw
    assert merged is not None
    assert merged.blocks.near is not None
    assert merged.blocks.local is not None, "near 更新不得清空 local"
    assert merged.blocks.extended is not None, "near 更新不得清空 extended"
    assert merged.blocks.interesting, "interesting 属于 local 层，near 更新不得清空"

    # 语义模型 / raw view 里其它层的内容仍然存在（不是只看对象存在）
    semantic = build_semantic_model(merged)
    terrain_types = {item["type"] for item in semantic["terrain"]}
    assert "water" in terrain_types, "water 只在 local 层，必须保留"
    assert "sand" in terrain_types, "sand 只在 extended 层，必须保留"
    raw_view = merged.model_dump(by_alias=True)
    assert raw_view["blocks"]["local"]["columns"], "raw view 必须保留 local 层数据"
    assert raw_view["blocks"]["extended"]["points"], "raw view 必须保留 extended 层数据"


def test_partial_local_update_preserves_near_and_extended():
    """local 更新覆盖 local + interesting；near/extended 必须保留。"""
    furnace = {
        "name": "furnace",
        "rel": {"dx": 1, "dy": 0, "dz": 1},
        "pos": {"x": 11, "y": 64, "z": -4},
        "distance": 1.4,
        "bearing": 45.0,
        "relative_direction": "front",
        "compass": "north",
    }
    clock, advance = make_clock()
    cache = WorldStateCache(clock)
    cache.update(parse_raw_snapshot(raw_payload()), {"near", "local", "extended"})
    advance(2.0)

    local_payload = _payload_with_layers("local")
    local_payload["blocks"]["interesting"] = [furnace]  # 证明 local 层确实被本次更新替换
    cache.update(parse_raw_snapshot(local_payload), {"local"})

    merged = cache.raw
    assert merged is not None
    assert merged.blocks.local is not None
    assert merged.blocks.near is not None, "local 更新不得清空 near"
    assert merged.blocks.extended is not None, "local 更新不得清空 extended"
    assert merged.blocks.near.columns, "near 层数据必须仍在"
    assert merged.blocks.extended.points, "extended 层数据必须仍在"
    assert [entry.name for entry in merged.blocks.interesting] == ["furnace"]

    semantic = build_semantic_model(merged)
    terrain_types = {item["type"] for item in semantic["terrain"]}
    assert "forest" in terrain_types, "forest（oak_log）只在 near 层，必须保留"
    assert "sand" in terrain_types, "sand 只在 extended 层，必须保留"


def test_partial_extended_update_preserves_near_and_local():
    """extended 更新只能覆盖 extended：near/local 必须保留。"""
    snow_point = {
        "name": "snow",
        "rel": {"dx": -48, "dy": 0, "dz": 0},
        "pos": {"x": -38, "y": 64, "z": -5},
        "distance": 48.0,
        "bearing": -90.0,
        "relative_direction": "left",
        "compass": "west",
        "biome": "snowy_plains",
    }
    clock, advance = make_clock()
    cache = WorldStateCache(clock)
    cache.update(parse_raw_snapshot(raw_payload()), {"near", "local", "extended"})
    advance(3.0)

    extended_payload = _payload_with_layers("extended")
    extended_payload["blocks"]["extended"] = {"radius": 96, "points": [snow_point]}
    cache.update(parse_raw_snapshot(extended_payload), {"extended"})

    merged = cache.raw
    assert merged is not None
    assert merged.blocks.extended is not None
    assert [point.name for point in merged.blocks.extended.points] == ["snow"]
    assert merged.blocks.near is not None and merged.blocks.near.columns, "near 必须保留"
    assert merged.blocks.local is not None and merged.blocks.local.columns, "local 必须保留"

    semantic = build_semantic_model(merged)
    terrain_types = {item["type"] for item in semantic["terrain"]}
    assert "grassland" in terrain_types, "grassland（grass_block）只在 near 层，必须保留"
    assert "water" in terrain_types, "water 只在 local 层，必须保留"
    assert "snow" in terrain_types, "snow 来自本次 extended 更新"


def test_layer_age_advances_independently():
    """更新 near 不得刷新 local/extended 的 age（任务书示例逐值断言）。"""
    clock, advance = make_clock()
    cache = WorldStateCache(clock)
    cache.update(parse_raw_snapshot(raw_payload()), {"near", "local", "extended"})
    assert cache.layer_age("near") == 0.0
    assert cache.layer_age("local") == 0.0
    assert cache.layer_age("extended") == 0.0

    advance(1.0)
    cache.update(parse_raw_snapshot(_payload_with_layers("near")), {"near"})
    assert cache.layer_age("near") == 0.0
    assert cache.layer_age("local") == 1.0
    assert cache.layer_age("extended") == 1.0


# =========================================== Phase 2.1：environment diff（修复 2）


def test_environment_signature_ignores_ticks_and_light():
    from app.integrations.minecraft.world import Environment

    base = Environment(
        biome="plains",
        time_of_day_ticks=1000,
        time_phase="day",
        weather="clear",
        light=15,
        dimension="overworld",
    )
    moved = Environment(
        biome="plains",
        time_of_day_ticks=9999,
        time_phase="day",
        weather="clear",
        light=3,
        dimension="overworld",
    )
    assert environment_signature(base) == environment_signature(moved)


async def test_environment_tick_change_alone_does_not_emit_world_changed():
    """Test A：只改 time_of_day_ticks（和精确 light）不得产生 world.changed。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    await perception.poll({"near"})  # prime（含环境签名基线）

    advance(2.0)
    payload = raw_payload()
    payload["environment"]["time_of_day_ticks"] = 1400  # 时间刻持续变化
    payload["environment"]["light"] = 12  # 精确光照也不参与签名
    client.payload = payload
    events = await perception.poll({"near"})
    assert all(name != "minecraft.world.changed" for name, _ in events)


async def test_environment_time_phase_change_emits_world_changed():
    """Test B：time_phase 变化必须产生事件。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    await perception.poll({"near"})

    advance(2.0)
    payload = raw_payload()
    payload["environment"]["time_phase"] = "night"
    client.payload = payload
    events = await perception.poll({"near"})
    changed = [data for name, data in events if name == "minecraft.world.changed"]
    assert len(changed) == 1
    assert changed[0]["environment_changed"] is True
    assert changed[0]["environment"]["time_phase"] == "night"


async def test_environment_weather_change_emits_world_changed():
    """Test C：weather 变化必须产生事件。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    await perception.poll({"near"})

    advance(2.0)
    payload = raw_payload()
    payload["environment"]["weather"] = "rain"
    client.payload = payload
    events = await perception.poll({"near"})
    changed = [data for name, data in events if name == "minecraft.world.changed"]
    assert len(changed) == 1
    assert changed[0]["environment_changed"] is True
    assert changed[0]["environment"]["weather"] == "rain"


async def test_environment_biome_change_emits_world_changed():
    """Test D：biome 变化必须产生事件。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    await perception.poll({"near"})

    advance(2.0)
    payload = raw_payload()
    payload["environment"]["biome"] = "desert"
    client.payload = payload
    events = await perception.poll({"near"})
    changed = [data for name, data in events if name == "minecraft.world.changed"]
    assert len(changed) == 1
    assert changed[0]["environment_changed"] is True
    assert changed[0]["environment"]["biome"] == "desert"


# =========================================== Phase 2.1：block removal diff（修复 3）


def _stone_column(x: int, z: int) -> dict:
    return {
        "name": "stone",
        "rel": {"dx": x - 10, "dy": -1, "dz": z + 5},
        "pos": {"x": x, "y": 63, "z": z},
        "distance": 1.0,
        "bearing": 0.0,
        "relative_direction": "front",
        "compass": "north",
    }


def _near_payload_with(columns: list[dict]) -> dict:
    payload = raw_payload()
    payload["blocks"] = {"near": {"radius": 6, "step": 1, "columns": columns}}
    return payload


async def test_world_changed_detects_removed_block():
    """previous A,B,C stone；current A,B → changed_blocks == 1（C 的移除必须被计到）。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance, change_block_threshold=1)
    a, b, c = _stone_column(8, 0), _stone_column(9, 0), _stone_column(10, 0)
    client.payload = _near_payload_with([a, b, c])
    await perception.poll({"near"})  # prime：A/B/C 基线

    advance(2.0)
    client.payload = _near_payload_with([a, b])  # 只有 C 消失
    events = await perception.poll({"near"})
    changed = [data for name, data in events if name == "minecraft.world.changed"]
    assert len(changed) == 1
    assert changed[0]["changed_blocks"] == 1


async def test_world_changed_counts_multiple_removed_blocks():
    """移除数量 ≥ 阈值必须产生事件，计数只算真实移除。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance, change_block_threshold=3)
    client.payload = _near_payload_with([_stone_column(i, 0) for i in (8, 9, 10)])
    await perception.poll({"near"})

    advance(2.0)
    client.payload = _near_payload_with([])  # 三块全部移除
    events = await perception.poll({"near"})
    changed = [data for name, data in events if name == "minecraft.world.changed"]
    assert len(changed) == 1
    assert changed[0]["changed_blocks"] == 3
    assert changed[0]["changed_blocks"] >= 3


# ================================ Phase 3A：movement-aware world diff


def _grid_payload(
    anchor: tuple[int, int],
    *,
    y: int = 64,
    radius: int = 6,
    overrides: dict[tuple[int, int], str] | None = None,
) -> dict:
    """以 anchor 为中心生成整窗柱面（每柱一个 grass_block，可局部覆盖方块名）。

    与 runtime 的真实输出同形：柱坐标 = floor(self.x) + dx（dx 为整数），
    所以窗口恰好是 ``anchor ± radius`` 的方块矩形。
    """
    payload = raw_payload()
    ax, az = anchor
    payload["self"]["position"] = {"x": float(ax), "y": float(y), "z": float(az)}
    names = overrides or {}
    columns = []
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            x, z = ax + dx, az + dz
            columns.append(
                {
                    "name": names.get((x, z), "grass_block"),
                    "rel": {"dx": dx, "dy": -1, "dz": dz},
                    "pos": {"x": x, "y": y - 1, "z": z},
                    "distance": float(max(abs(dx), abs(dz))),
                    "bearing": 0.0,
                    "relative_direction": "front",
                    "compass": "north",
                }
            )
    payload["blocks"] = {"near": {"radius": radius, "step": 1, "columns": columns}}
    return payload


def _world_changed_events(events: list[tuple[str, dict]]) -> list[dict]:
    return [data for name, data in events if name == "minecraft.world.changed"]


async def test_yaw_change_does_not_emit_world_changed():
    """§九：原地只转视角（yaw 0 → 90）不得触发 world.changed（世界没变）。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    client.payload = _grid_payload((100, 200))
    await perception.poll({"near"})  # prime

    advance(2.0)
    rotated = _grid_payload((100, 200))
    rotated["self"]["yaw"] = 90.0
    for column in rotated["blocks"]["near"]["columns"]:
        column["bearing"] = 90.0
        column["relative_direction"] = "right"  # 方向字段变化不是世界变化
    client.payload = rotated
    events = await perception.poll({"near"})
    assert _world_changed_events(events) == []
    assert perception.last_diff is not None
    assert perception.last_diff.kind == NO_MOVEMENT
    assert perception.last_diff.changed_blocks == 0


async def test_move_one_block_without_world_change_emits_nothing():
    """Test 1：移动 1 格、世界没变 → 无 world.changed；位移柱只记 shifted。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    client.payload = _grid_payload((100, 200))
    await perception.poll({"near"})

    advance(2.0)
    client.payload = _grid_payload((101, 200))  # 向东 1 格：A 离开 / F 进入
    events = await perception.poll({"near"})
    assert _world_changed_events(events) == []
    diff = perception.last_diff
    assert diff is not None
    assert diff.kind == WINDOW_SHIFT
    assert diff.changed_blocks == 0
    assert diff.shifted_blocks == 26  # 每侧一整列 13 柱
    assert diff.shift == (1, 0)


async def test_move_three_blocks_without_world_change_emits_nothing():
    """Test 2：移动 3 格、世界没变 → 无 world.changed。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)
    client.payload = _grid_payload((100, 200))
    await perception.poll({"near"})

    advance(2.0)
    client.payload = _grid_payload((103, 200))
    events = await perception.poll({"near"})
    assert _world_changed_events(events) == []
    diff = perception.last_diff
    assert diff is not None and diff.kind == WINDOW_SHIFT
    assert diff.changed_blocks == 0
    assert diff.shifted_blocks == 3 * 13 * 2  # 每侧 3 列


async def test_move_with_overlap_change_is_counted():
    """Test 3：移动后重叠区域 1 个方块变化，threshold=1 → 事件且计数为 1。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance, change_block_threshold=1)
    client.payload = _grid_payload((100, 200))
    await perception.poll({"near"})

    advance(2.0)
    # 世界方块 (102,200)：上一帧是 grass_block，现在变成 stone（世界真实变化）
    client.payload = _grid_payload((101, 200), overrides={(102, 200): "stone"})
    events = await perception.poll({"near"})
    changed = _world_changed_events(events)
    assert len(changed) == 1
    assert changed[0]["changed_blocks"] == 1
    assert changed[0]["near_diff"]["kind"] == WORLD_CHANGE


async def test_move_with_ten_overlap_changes_emits_world_changed():
    """Test 4：移动后重叠区域 10 个方块变化，threshold=10 → 正常触发。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance)  # 默认 threshold=10
    client.payload = _grid_payload((100, 200))
    await perception.poll({"near"})

    advance(2.0)
    overrides = {(96 + i, 200): "stone" for i in range(10)}
    client.payload = _grid_payload((101, 200), overrides=overrides)
    events = await perception.poll({"near"})
    changed = _world_changed_events(events)
    assert len(changed) == 1
    assert changed[0]["changed_blocks"] == 10
    assert changed[0]["near_diff"]["kind"] == WORLD_CHANGE


async def test_entering_window_blocks_are_not_counted_as_world_change():
    """Test 5：移动 5 格产生的大量新区域不得计成 changed_blocks。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance, change_block_threshold=1)
    client.payload = _grid_payload((100, 200))
    await perception.poll({"near"})

    advance(2.0)
    client.payload = _grid_payload((105, 200))  # 5 格：65 柱进入 + 65 柱离开
    events = await perception.poll({"near"})
    assert _world_changed_events(events) == []
    diff = perception.last_diff
    assert diff is not None and diff.kind == WINDOW_SHIFT
    assert diff.changed_blocks == 0
    assert diff.shifted_blocks == 5 * 13 * 2
    assert diff.overlap_blocks == 8 * 13  # 重叠区双侧都存在的柱


async def test_teleport_rebases_baseline_without_event():
    """Test 6：远距 teleport → 不触发事件、旧基线作废、新位置立即可作基线。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance, change_block_threshold=1)
    client.payload = _grid_payload((100, 200))
    await perception.poll({"near"})

    advance(2.0)
    client.payload = _grid_payload((1000, 200))  # 没有有效重叠
    events = await perception.poll({"near"})
    assert _world_changed_events(events) == []
    diff = perception.last_diff
    assert diff is not None and diff.kind == TELEPORT_REBASE
    assert diff.changed_blocks == 0

    # 新窗口已建立基线：原地不动 → 无事件
    advance(2.0)
    events = await perception.poll({"near"})
    assert _world_changed_events(events) == []
    assert perception.last_diff is not None
    assert perception.last_diff.kind == NO_MOVEMENT

    # 新基线上检测到真实变化（threshold=1）→ 事件
    advance(2.0)
    client.payload = _grid_payload((1000, 200), overrides={(1000, 200): "stone"})
    events = await perception.poll({"near"})
    changed = _world_changed_events(events)
    assert len(changed) == 1
    assert changed[0]["changed_blocks"] == 1


async def test_vertical_movement_does_not_count_as_world_change():
    """Test 8：垂直移动/跳跃不得把世界判定为大量方块变化。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance, change_block_threshold=1)
    client.payload = _grid_payload((100, 200), y=64)
    await perception.poll({"near"})

    advance(2.0)
    payload = _grid_payload((100, 200), y=70)  # 跳到 6 格高：锚点不变（X/Z 对齐）
    for column in payload["blocks"]["near"]["columns"]:
        if column["pos"]["x"] == 100 and column["pos"]["z"] == 200:
            # 同一根柱子：最高方块因垂直窗口裁切换了 y 坐标，名字没变 → 不是世界变化
            column["pos"]["y"] = 65
    client.payload = payload
    events = await perception.poll({"near"})
    assert _world_changed_events(events) == []
    diff = perception.last_diff
    assert diff is not None
    assert diff.kind == NO_MOVEMENT
    assert diff.changed_blocks == 0


# =========================================== Phase 3C：move_to 期间的感知（§二十三）


async def test_movement_during_move_to_keeps_perception_quiet():
    """move_to 期间罐头持续移动（窗口平移）不得产生虚假 world.changed；
    重叠区真实变化仍按 Phase 2.1/3A 规则触发。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance, change_block_threshold=1)
    client.payload = _grid_payload((100, 200))
    await perception.poll({"near"})  # prime

    # 连续三帧锚点东移 2 格（模拟移动中的观察窗口平移）：只能 WINDOW_SHIFT，不报世界变化
    for step, anchor in enumerate(((102, 200), (104, 200), (106, 200))):
        advance(2.0)
        client.payload = _grid_payload(anchor)
        events = await perception.poll({"near"})
        assert all(name != "minecraft.world.changed" for name, _ in events), (
            f"第 {step + 1} 帧不得有世界事件"
        )
        assert perception.last_diff is not None
        assert perception.last_diff.kind == WINDOW_SHIFT, f"第 {step + 1} 帧应为 WINDOW_SHIFT"
        assert perception.last_diff.changed_blocks == 0

    # 移动中重叠区发生 1 处真实变化（世界方块 (108,200) 变成 stone）：照常触发
    advance(2.0)
    client.payload = _grid_payload((108, 200), overrides={(108, 200): "stone"})
    events = await perception.poll({"near"})
    changed = [data for name, data in events if name == "minecraft.world.changed"]
    assert len(changed) == 1
    assert changed[0]["changed_blocks"] == 1
    assert changed[0]["near_diff"]["kind"] == WORLD_CHANGE


# =========================================== Phase 3D：follow 期间的感知（§二十八）


async def test_follow_period_window_shifts_stay_quiet():
    """跟随期间目标+罐头持续移动：多帧窗口平移只能 WINDOW_SHIFT / changed_blocks==0；
    重叠区真实变化仍按 Phase 2.1/3A 规则检测。"""
    clock, advance = make_clock()
    perception, client, events = make_perception(clock, advance, change_block_threshold=1)
    client.payload = _grid_payload((200, 100))
    await perception.poll({"near"})  # prime

    # 四帧连续平移（模拟跟随中的位移），每帧都要安静
    for step, anchor in enumerate(((203, 100), (206, 101), (209, 103), (212, 104))):
        advance(2.0)
        client.payload = _grid_payload(anchor)
        events = await perception.poll({"near"})
        world_events = [name for name, _ in events if name == "minecraft.world.changed"]
        assert world_events == [], f"跟随第 {step + 1} 帧不得有 world.changed"
        assert perception.last_diff is not None
        assert perception.last_diff.kind == WINDOW_SHIFT
        assert perception.last_diff.changed_blocks == 0

    # 跟随中重叠区真实变化：照常触发
    advance(2.0)
    client.payload = _grid_payload((214, 104), overrides={(213, 104): "stone"})
    events = await perception.poll({"near"})
    changed = [data for name, data in events if name == "minecraft.world.changed"]
    assert len(changed) == 1 and changed[0]["changed_blocks"] == 1
