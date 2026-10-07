"""Phase 5C 测试用的假 Minecraft Service + 真实记忆引擎装配。

这里**不碰 Mineflayer**：只用一个与 :class:`MinecraftService` 同形的假对象提供
``snapshot()`` / ``world_view()`` / ``dig_capability()`` / ``find_blocks()``。
记忆域本身用的是**真的** SQLite + 真的 MemoryManager（不是内存桩），
这样"重启后还在"这类断言才有意义。
"""

from __future__ import annotations

from typing import Any

from app.config.settings import DatabaseConfig, MemoryConfig
from app.database.database import Database
from app.integrations.minecraft.memory_bridge import MinecraftMemoryBridge
from app.memory.manager import MemoryManager

#: 固定的一块"锚点"方块（复核路径会读它）
ANCHOR = {"x": 100, "y": 64, "z": 100}
#: 一个可用的 uuid（canonical = 32 位小写）
UUID_KONGLING = "1111111122223333444455555555" + "9f2c"
UUID_OTHER = "aaaabbbbccccddddeeeeffff0000" + "1234"
HOST = "127.0.0.1"
PORT = 25565


class FakeMinecraftService:
    """与 MinecraftService 同形的假 Service（只提供记忆桥用到的只读面）。"""

    def __init__(
        self,
        *,
        host: str = HOST,
        port: int = PORT,
        players: list[dict[str, Any]] | None = None,
        self_position: dict[str, Any] | None = None,
    ) -> None:
        self.enabled = True
        self._host = host
        self._port = port
        self._players = list(players if players is not None else [])
        self._self_position = dict(self_position or {"x": 100.0, "y": 64.0, "z": 100.0})
        #: 复核用的世界事实：坐标 → {"name": ..., "reason": ...}
        self.blocks: dict[tuple[int, int, int], dict[str, Any]] = {}
        #: find_blocks 的返回（不设置 = 空结果）
        self.found: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: 事件订阅者（与 MinecraftService 同形；Bot 装配时挂上来）
        self.listeners: list[Any] = []

    def add_listener(self, listener: Any) -> None:
        if listener not in self.listeners:
            self.listeners.append(listener)

    def remove_listener(self, listener: Any) -> None:
        if listener in self.listeners:
            self.listeners.remove(listener)

    # --- 编排面

    def set_block(self, position: dict[str, Any], name: str | None) -> None:
        """``name=None`` = 空气（挖掉了 / setblock air）。"""
        key = (int(position["x"]), int(position["y"]), int(position["z"]))
        self.blocks[key] = {"name": name or "", "reason": "" if name else "air"}

    def add_player(self, username: str, uuid: str) -> None:
        self._players.append({"name": username, "uuid": uuid, "distance": 3.0})

    # --- 只读面（记忆桥唯一会用到的东西）

    def snapshot(self) -> dict[str, Any]:
        return {
            "connection": {"host": self._host, "port": self._port, "status": "ONLINE"},
            "runtime": {"status": "READY"},
        }

    def world_view(self) -> dict[str, Any]:
        return {
            "semantic": {
                "self": {"position": dict(self._self_position)},
                "players": [dict(row) for row in self._players],
            }
        }

    async def dig_capability(self, x: int, y: int, z: int) -> dict[str, Any]:
        self.calls.append(("dig_capability", {"x": x, "y": y, "z": z}))
        if "dig_capability" in self.fail:
            raise RuntimeError("bridge down")
        block = self.blocks.get((int(x), int(y), int(z)))
        if block is None:
            return {"ok": True, "result": {"ok": True, "block": {"name": "minecraft:stone"}}}
        if not block["name"]:
            return {"ok": True, "result": {"ok": True, "block": {"name": ""}, "reason": "air"}}
        return {"ok": True, "result": {"ok": True, "block": {"name": block["name"]}}}

    async def find_blocks(
        self, block_names: list[str], max_distance: int, max_results: int
    ) -> dict[str, Any]:
        self.calls.append(("find_blocks", {"names": list(block_names), "distance": max_distance}))
        if "find_blocks" in self.fail:
            raise RuntimeError("bridge down")
        return {"ok": True, "result": {"ok": True, "matches": list(self.found)}}


async def build_bridge(
    tmp_path: Any,
    *,
    service: FakeMinecraftService | None = None,
    database: Database | None = None,
    manager: MemoryManager | None = None,
    character_key: str = "空凛",
    clock: Any = None,
    db_name: str = "mc_memory.db",
) -> tuple[MinecraftMemoryBridge, Database, MemoryManager, FakeMinecraftService]:
    """装配一个**真实持久化**的记忆桥（SQLite + MemoryManager，两样都是真的）。"""
    own_db = database is None
    if database is None:
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / db_name}"))
        await database.connect()
    if manager is None:
        manager = MemoryManager(MemoryConfig(), database)
    if service is None:
        service = FakeMinecraftService()
    kwargs: dict[str, Any] = {}
    if clock is not None:
        kwargs["clock"] = clock
    bridge = MinecraftMemoryBridge(
        service,
        memory_manager=manager,
        database=database,
        character_key=character_key,
        character_label=character_key,
        **kwargs,
    )
    del own_db
    return bridge, database, manager, service
