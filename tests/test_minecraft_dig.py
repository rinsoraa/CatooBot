"""dig 服务层测试（Phase 4B）：类型/格式校验、错误翻译（含 block_changed 的 detail）、
启动即 RUNNING、离线/忙的稳定错误码。runtime 用进程内假服务器（FakeRuntime），不需要 Node。
"""

from __future__ import annotations

import pytest

from app.integrations.minecraft.service import (
    MinecraftActionBusy,
    MinecraftActionInvalid,
    MinecraftBlockBreakUnconfirmed,
    MinecraftBlockChanged,
    MinecraftBlockNotDiggable,
    MinecraftBlockNotFound,
    MinecraftBlockTooFar,
    MinecraftNotConnected,
    MinecraftService,
)
from tests.test_minecraft_service import FakeRuntime, make_config

ARGS = (120.0, 64.0, -230.0, "minecraft:stone")


@pytest.fixture
async def fake_runtime():
    runtime = FakeRuntime()
    await runtime.start()
    yield runtime
    await runtime.stop()


@pytest.fixture
async def make_service():
    created: list[MinecraftService] = []

    def factory(config):
        service = MinecraftService(None, config)
        created.append(service)
        return service

    yield factory
    for service in created:
        await service._cleanup()


# ------------------------------------------------------------- 正常路径


async def test_dig_starts_and_returns_running(fake_runtime: FakeRuntime, make_service) -> None:
    """启动即 RUNNING（挖掘可能持续数秒~数十秒，绝不阻塞调用方）。"""
    service = make_service(make_config(fake_runtime))
    result = await service.dig(*ARGS)
    assert result["status"] == "RUNNING"
    assert result["action"] == "dig"
    assert str(result["action_id"]).startswith("act_dig")
    assert fake_runtime.dig_calls == [
        {"x": 120.0, "y": 64.0, "z": -230.0, "expected_block": "minecraft:stone"}
    ]


# ------------------------------------------------------------- 参数校验


async def test_dig_validates_coordinates(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    bad_coords = [
        ("abc", 1, 2, "stone"),
        (1, None, 2, "stone"),
        (float("nan"), 1, 2, "stone"),
        (1, 1, float("inf"), "stone"),
        (True, 1, 2, "stone"),  # bool 不是坐标
        (4.0e7, 1, 2, "stone"),  # 超出世界边界
        (1, -600, 2, "stone"),
    ]
    for x, y, z, block in bad_coords:
        with pytest.raises(MinecraftActionInvalid):
            await service.dig(x, y, z, block)
    assert fake_runtime.dig_calls == [], "本地校验先于任何网络往返"


async def test_dig_requires_expected_block(fake_runtime: FakeRuntime, make_service) -> None:
    """expected_block 必填：用户确认的是「这个位置的这个方块」（§六）。"""
    service = make_service(make_config(fake_runtime))
    for bad in ("", "   ", None, 42):
        with pytest.raises(MinecraftActionInvalid, match="expected_block"):
            await service.dig(1, 64, 2, bad)
    with pytest.raises(MinecraftActionInvalid, match="最长"):
        await service.dig(1, 64, 2, "x" * 65)
    with pytest.raises(MinecraftActionInvalid, match="控制字符"):
        await service.dig(1, 64, 2, "stone\n")
    assert fake_runtime.dig_calls == []


# ------------------------------------------------------------- 错误翻译


async def test_dig_translates_block_not_found(fake_runtime: FakeRuntime, make_service) -> None:
    fake_runtime.dig_plan.append({"error": ("block.not_found", 404)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftBlockNotFound) as excinfo:
        await service.dig(*ARGS)
    assert excinfo.value.code == "minecraft.block_not_found"
    assert excinfo.value.status == 404


async def test_dig_translates_block_changed_with_detail(
    fake_runtime: FakeRuntime, make_service
) -> None:
    """§十四：方块变了要带上 expected/actual（结构化，不是原始异常文本）。"""
    fake_runtime.dig_plan.append(
        {
            "error": ("block.changed", 409),
            "detail": {"expected": "minecraft:stone", "actual": "minecraft:dirt"},
        }
    )
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftBlockChanged) as excinfo:
        await service.dig(*ARGS)
    assert excinfo.value.code == "minecraft.block_changed"
    assert excinfo.value.status == 409
    assert excinfo.value.detail == {"expected": "minecraft:stone", "actual": "minecraft:dirt"}


async def test_dig_translates_not_diggable(fake_runtime: FakeRuntime, make_service) -> None:
    fake_runtime.dig_plan.append({"error": ("block.not_diggable", 400)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftBlockNotDiggable) as excinfo:
        await service.dig(*ARGS)
    assert excinfo.value.code == "minecraft.block_not_diggable"


async def test_dig_translates_too_far(fake_runtime: FakeRuntime, make_service) -> None:
    fake_runtime.dig_plan.append({"error": ("block.too_far", 400)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftBlockTooFar) as excinfo:
        await service.dig(*ARGS)
    assert excinfo.value.code == "minecraft.block_too_far"


async def test_dig_translates_break_unconfirmed(fake_runtime: FakeRuntime, make_service) -> None:
    """§二十三：dig 结束了但方块还在 → 不能报成功。"""
    fake_runtime.dig_plan.append({"error": ("block.break_unconfirmed", 500)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftBlockBreakUnconfirmed) as excinfo:
        await service.dig(*ARGS)
    assert excinfo.value.code == "minecraft.block_break_unconfirmed"


async def test_dig_translates_offline_and_busy(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    fake_runtime.dig_plan.append({"error": ("action.not_online", 400)})
    with pytest.raises(MinecraftNotConnected):
        await service.dig(*ARGS)
    fake_runtime.dig_plan.append({"error": ("action.busy", 409)})
    with pytest.raises(MinecraftActionBusy):
        await service.dig(*ARGS)


async def test_dig_reports_runtime_down(make_service) -> None:
    from app.integrations.minecraft.service import MinecraftRuntimeDown

    service = make_service(make_config(None))  # 没人监听的端口
    with pytest.raises(MinecraftRuntimeDown):
        await service.dig(*ARGS)


# ------------------------------------------------------------- 配置注入


async def test_dig_gates_are_passed_to_the_runtime() -> None:
    """§十：dig 的超时与距离上限必须随环境注入 runtime（配置即安全门）。"""
    from app.config.settings import MinecraftConfig

    config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, action={"dig": {"timeout": 45, "max_distance": 4}}
    )
    service = MinecraftService(None, config)  # type: ignore[arg-type]
    env = service._runtime_env()  # noqa: SLF001
    assert env["MC_DIG_TIMEOUT_MS"] == "45000"
    assert env["MC_DIG_MAX_DISTANCE"] == "4.0"


def test_dig_config_bounds() -> None:
    """超时 5~120s、距离 0~6：越界直接 ValidationError（配置层挡住荒谬值）。"""
    from pydantic import ValidationError

    from app.config.settings import MinecraftConfig

    for bad in ({"timeout": 3}, {"timeout": 200}, {"max_distance": 0}, {"max_distance": 9}):
        with pytest.raises(ValidationError):
            MinecraftConfig(enabled=True, action={"dig": bad})
