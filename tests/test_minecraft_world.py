"""World Perception 单元测试（Phase 2）：语义映射、缓存失效、差异事件去抖。"""

from __future__ import annotations

import pytest

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.service import MinecraftService
from app.integrations.minecraft.world import (
    WorldPerception,
    build_semantic_model,
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
            "extended": {"radius": 96, "points": []},
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
    from app.integrations.minecraft.world import WorldStateCache

    clock, _ = make_clock()
    cache_raw = parse_raw_snapshot(raw_payload())
    state_cache = WorldStateCache(clock)
    state_cache.update(cache_raw, {"near", "local"})
    assert state_cache.online is True
    assert state_cache.raw is cache_raw

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
    # 整片变化（>10）：聚合成一条 world.changed（绝无逐方块风暴）
    columns = client.payload["blocks"]["near"]["columns"]
    for i in range(12):
        columns.append(
            {
                "name": "cobblestone",
                "rel": {"dx": i, "dy": -1, "dz": 3},
                "pos": {"x": 10 + i, "y": 63, "z": -2},
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
