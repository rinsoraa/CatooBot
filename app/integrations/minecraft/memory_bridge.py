"""Phase 5C：把身份桥 + Minecraft 记忆接到现有 Bot 上（唯一接线点）。

职责边界**严格**如下：

```
WorldPerception（运行时）  →  这里只读地观察/复核
TaskRuntime（已有）        →  这里只订阅它的事件，把"值得记住的结果"写成记忆
IdentityStore（迁移 28）    →  这里做 QQ ↔ Minecraft 绑定与冲突拒绝
Memory（现有引擎 + minecraft 域）→ 这里写入/检索/对账
```

* 本模块**绝不**执行世界动作：只用 SAFE 只读（`inventory` / `dig_capability` / `find_blocks`）
  复核记忆，绝不 move/dig/place/pickup（§二十三/§五十一）。
* 记忆给的只是**上下文**：权限永远由 Policy / Confirmation / TaskAuthorization 决定（§二/§五十二）。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.integrations.minecraft.identity import (
    CODE_CONFLICT,
    CODE_NOT_FOUND,
    SOURCE_CONFIGURED,
    SOURCE_EXPLICIT,
    IdentityLink,
    IdentityStore,
    MinecraftIdentity,
    MinecraftServerIdentity,
    canonical_uuid,
    server_identity,
)
from app.memory.minecraft import (
    FactSource,
    Freshness,
    MinecraftMemoryFact,
    MinecraftMemoryKind,
    MinecraftMemoryReconciler,
    MinecraftMemoryRetriever,
    MinecraftMemoryStore,
    MinecraftMemoryWriter,
)
from app.tasks.models import StepState
from app.tasks.skill_learning import DIG_TOOL, self_dig_confirmed

#: 复核距离：这么近才敢说"世界说它不在了"（远了只能 unknown，绝不误判 §二十）
VERIFY_NEAR_BLOCKS = 4.5
#: 记忆里一条位置事实的复核范围（find_blocks 的上限）
VERIFY_SCAN_BLOCKS = 32
#: 注入预算（§四十九：一次 turn ≤5 条）
CONTEXT_MAX_CHARS = 420
#: 运行时给出的"读不到"原因（``too_far`` = 4J 的距离门，``unavailable`` = 拿不到世界）
#: —— 这些**都不是**"世界变了"，必须原样当 unknown（§二十二）
_UNKNOWN_REASONS = frozenset({"too_far", "unavailable", "error"})


def _unwrap(payload: Any) -> dict[str, Any]:
    """运行时回执把语义结果包在 ``result`` 里（与工具层投影同口径）。"""
    if not isinstance(payload, Mapping):
        return {}
    inner = payload.get("result")
    return dict(inner) if isinstance(inner, Mapping) else dict(payload)


def _step_state(step: Any) -> str:
    """步骤状态（枚举或字符串；读不到就是空串 → 一律按"没成功"处理）。"""
    raw = getattr(getattr(step, "state", None), "value", getattr(step, "state", ""))
    return str(raw or "")


@dataclass
class MemoryBridgeStatus:
    """给 WebUI/日志看的只读状态（**memory degraded 必须可见**，§四十）。"""

    enabled: bool = False
    server_id: str = ""
    server_label: str = ""
    character_key: str = ""
    facts: int = 0
    stale: int = 0
    invalidated: int = 0
    links: dict[str, int] = field(default_factory=dict)
    #: 缺陷期间留下的 legacy scope 事实数（**只作审计**：不参与检索/对账，也不删）
    legacy: int = 0
    memory_degraded: str = ""
    identity_degraded: str = ""
    last_reconcile: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "server_id": self.server_id,
            "server_label": self.server_label,
            "character_key": self.character_key,
            "facts": self.facts,
            "stale": self.stale,
            "invalidated": self.invalidated,
            "links": dict(self.links),
            "legacy": int(self.legacy),
            "memory_degraded": self.memory_degraded,
            "identity_degraded": self.identity_degraded,
            "last_reconcile": dict(self.last_reconcile),
        }


class MinecraftMemoryBridge:
    """身份桥 + 记忆域的组装与接线（Bot 只跟这一个对象打交道）。"""

    def __init__(
        self,
        service: Any,
        *,
        memory_manager: Any,
        database: Any = None,
        character_key: str = "default",
        character_label: str = "罐头",
        identity_store: IdentityStore | None = None,
        clock: Any = time.time,
        logger: Any = None,
    ) -> None:
        self.service = service
        self.clock = clock
        self._log = logger
        self.character_key = str(character_key or "default")
        self.character_label = str(character_label or "罐头")
        self.store = MinecraftMemoryStore(
            memory_manager, character_key=self.character_key, clock=clock, logger=logger
        )
        self.writer = MinecraftMemoryWriter(
            self.store, clock=clock, character_label=self.character_label, logger=logger
        )
        self.retriever = MinecraftMemoryRetriever(
            self.store, verify=self.verify_fact, clock=clock, logger=logger
        )
        self.reconciler = MinecraftMemoryReconciler(
            self.store, verify=self.verify_fact, clock=clock, logger=logger
        )
        self.identities = identity_store or (
            IdentityStore(database, clock=clock, logger=logger) if database is not None else None
        )
        #: 上次对账结果（只读展示）
        self.last_reconcile: dict[str, Any] = {}
        #: 已经记录过"第一次见面"的玩家（§十五：只在第一次写 EVENT）
        self._greeted: set[str] = set()

    # ------------------------------------------------------------ 服务器身份（§四）

    def server(self) -> MinecraftServerIdentity:
        connection: dict[str, Any] = {}
        try:
            connection = dict(self.service.snapshot().get("connection") or {})
        except Exception:  # noqa: BLE001 - 拿不到连接事实就返回空身份
            connection = {}
        return server_identity(connection.get("host"), connection.get("port"))

    def server_id(self) -> str:
        return self.server().server_id

    def self_username(self) -> str:
        """罐头**自己**的 MC 名字（镜像里的 ``connection.username``）。

        真机踩到过：mineflayer 连自己 spawn 也会推 ``player_joined``，于是她把
        「Catodayo 在这个服务器里活动过」记成了别人的 PLAYER 事实。自己的名字也必须
        从身份与记忆里排除掉（她不该把自己当"一个玩家"）。
        """
        try:
            connection = dict(self.service.snapshot().get("connection") or {})
        except Exception:  # noqa: BLE001 - 拿不到名字就不排除（宁可多记，不要认错人）
            return ""
        return str(connection.get("username") or "").strip()

    def online_players(self) -> list[MinecraftIdentity]:
        """当前在线的玩家（uuid 来自运行时；username 只是显示名；**不含她自己**）。"""
        server_id = self.server_id()
        mine = self.self_username().lower()
        try:
            semantic = (self.service.world_view() or {}).get("semantic") or {}
        except Exception:  # noqa: BLE001
            return []
        out: list[MinecraftIdentity] = []
        for row in semantic.get("players") or []:
            if not isinstance(row, Mapping):
                continue
            found = MinecraftIdentity(
                server_id=server_id,
                player_uuid=canonical_uuid(row.get("uuid")),
                username=str(row.get("name") or ""),
            )
            if not found.player_uuid:
                continue
            if mine and found.username.strip().lower() == mine:
                continue
            out.append(found)
        return out

    def player_named(self, username: str) -> MinecraftIdentity | None:
        """按**精确**玩家名找在线玩家（只用于检索相关性；绝不作为身份绑定依据，§六）。"""
        wanted = str(username or "").strip().lower()
        if not wanted:
            return None
        for player in self.online_players():
            if player.username.lower() == wanted:
                return player
        return None

    def self_position(self) -> dict[str, Any]:
        try:
            semantic = (self.service.world_view() or {}).get("semantic") or {}
            position = ((semantic.get("self") or {}).get("position")) or {}
            return dict(position) if isinstance(position, Mapping) else {}
        except Exception:  # noqa: BLE001
            return {}

    # ------------------------------------------------------------ 身份绑定（§五-§八）

    async def bind(
        self, *, platform: str, user_id: str, username: str, source: str = SOURCE_EXPLICIT
    ) -> Any:
        """把平台用户绑到**在线**的同名玩家：要真的拿到 uuid 才允许（§四十三 2）。"""
        if self.identities is None:
            return None
        player = self.player_named(username)
        if player is None:
            return None
        outcome = await self.identities.verify(
            platform=platform,
            user_id=str(user_id),
            server_id=player.server_id,
            player_uuid=player.player_uuid,
            username=player.username,
            source=source,
        )
        if outcome.ok and outcome.link is not None:
            await self.writer.relationship(
                server_id=player.server_id,
                player_uuid=player.player_uuid,
                username=player.username,
                label="bound_player",
            )
        return outcome

    async def bind_configured(self, *, platform: str, user_id: str, username: str) -> Any:
        """项目既有显式配置（如 core_friend_identities）：优先级次于用户显式验证（§六）。"""
        return await self.bind(
            platform=platform, user_id=user_id, username=username, source=SOURCE_CONFIGURED
        )

    async def unbind(self, *, platform: str, user_id: str) -> Any:
        if self.identities is None:
            return None
        return await self.identities.revoke(platform=platform, user_id=str(user_id))

    async def link_for(self, *, platform: str, user_id: str) -> IdentityLink | None:
        if self.identities is None:
            return None
        return await self.identities.verified(
            platform=platform, user_id=str(user_id), server_id=self.server_id()
        )

    # ------------------------------------------------------------ 观察 → 记忆

    async def on_player_joined(self, data: Mapping[str, Any]) -> None:
        """玩家上线 → 记一条 PLAYER 事实（第一次见面额外记一条 EVENT，§十二/§十五）。

        **她自己不算"一个玩家"**：mineflayer 连自己 spawn 也会推 player_joined，
        真机上曾因此记出「Catodayo 在这个服务器里活动过」。
        """
        server_id = self.server_id()
        player_uuid = canonical_uuid(data.get("uuid"))
        username = str(data.get("username") or "").strip()
        if not server_id or not player_uuid:
            return
        mine = self.self_username().lower()
        if mine and username.lower() == mine:
            return
        existed = await self.store.facts(
            server_id=server_id,
            kinds=[MinecraftMemoryKind.PLAYER],
            player_uuid=player_uuid,
            limit=1,
        )
        await self.writer.player_seen(
            server_id=server_id, player_uuid=player_uuid, username=username
        )
        if not existed and player_uuid not in self._greeted:
            self._greeted.add(player_uuid)
            await self.writer.event(
                server_id=server_id,
                subject=f"first_seen:{player_uuid}",
                content=f"第一次在这个世界里见到 {username or '一个玩家'}。",
            )

    async def on_task_finished(self, record: Any) -> None:
        """TaskRuntime 收尾 → 一条任务经验（§三十一/§三十二）。"""
        server_id = self.server_id()
        state = str(getattr(getattr(record, "state", None), "value", "") or "")
        if not server_id or state not in {"SUCCEEDED", "FAILED", "EXPIRED"}:
            return
        gained: dict[str, Any] = {}
        verification = getattr(record, "verification", None) or {}
        if isinstance(verification, Mapping) and isinstance(
            verification.get("inventory_delta"), Mapping
        ):
            gained = {str(k): v for k, v in verification["inventory_delta"].items()}
        await self.writer.task_finished(
            server_id=server_id,
            objective=str(getattr(record, "objective", "") or ""),
            outcome=state,
            task_id=str(getattr(record, "task_id", "") or ""),
            plan_version=int(getattr(record, "plan_version", 1) or 1),
            initiator=str(getattr(record, "user_id", "") or ""),
            position=self._task_position(record),
            gained=gained,
            reason=str(getattr(record, "message", "") or "")[:60],
        )
        if state == "SUCCEEDED":
            await self._remember_task_target(server_id, record)

    async def _remember_task_target(self, server_id: str, record: Any) -> None:
        """任务成功时，把她**亲手挖掉的**那些格子记成世界事实（§三十：重要的世界事实）。

        Phase 7D Follow-up：**逐挖掘步骤独立判定**，只有同时满足三条才写
        （其余一律不写、也不降级成别的事实）：

          1. 这一步真的执行成功（``SUCCEEDED``）；
          2. 世界效果确认（``world_effect == BLOCK_REMOVED``）；
          3. 执行归属是**罐头自己**（``attribution == SELF_CONFIRMED``）。

        判定只用本地运行时随动作终态回报的归因（`dig_attribution.js` 契约），来源是
        ``TASK_RESULT``（"她亲手挖到过"，不是"她亲眼看见"）。世界随后变了也没关系：
        这条事实带坐标，对账与检索都会用**当前世界**去复核它，该失效就失效（§二十二/§二十七）。

        这样"刚才那棵树在哪里"才有可复核的素材 —— 否则任务只留下一条 TASK 事实，
        而 TASK 事实本来就是"做过什么"，不是"世界里有什么"。
        """
        for step in getattr(record, "steps", None) or []:
            if str(getattr(step, "tool", "") or "") != DIG_TOOL:
                continue
            if _step_state(step) != StepState.SUCCEEDED.value:
                continue
            if not self_dig_confirmed(step):
                continue
            arguments = getattr(step, "effective_arguments", None) or {}
            if not isinstance(arguments, Mapping):
                continue
            block = str(arguments.get("expected_block") or "").strip()
            position = {axis: arguments.get(axis) for axis in ("x", "y", "z")}
            if not block or any(position.get(axis) is None for axis in ("x", "y", "z")):
                continue
            await self.writer.resource_seen(
                server_id=server_id,
                block_name=block,
                position=position,
                source=FactSource.TASK_RESULT,
            )

    @staticmethod
    def _task_position(record: Any) -> dict[str, Any]:
        """任务里最后一次动作的目标位置（只取最终动作的语义结果，不抄内部字段）。"""
        for step in reversed(list(getattr(record, "steps", []) or [])):
            result = getattr(step, "result", None)
            if not isinstance(result, Mapping):
                continue
            position = result.get("position") or result.get("target")
            if isinstance(position, Mapping) and {"x", "y", "z"} <= set(position):
                return {
                    "x": position["x"],
                    "y": position["y"],
                    "z": position["z"],
                }
        return {}

    # ------------------------------------------------------------ 只读复核（SAFE）

    async def verify_fact(self, fact: MinecraftMemoryFact) -> tuple[str, dict[str, Any]]:
        """对一条记忆做**只读**世界复核（§二十二）：present / absent / unknown。"""
        if fact.kind is MinecraftMemoryKind.RESOURCE and fact.position:
            name = fact.subject.split("@", 1)[0].replace("resource:", "").strip()
            return await self._verify_block(fact.position, name)
        if fact.kind is MinecraftMemoryKind.LOCATION and fact.position:
            return await self._verify_area(fact.position, fact.radius)
        return "unknown", {"reason": "not_a_world_fact"}

    async def _verify_block(
        self, position: Mapping[str, Any], name: str
    ) -> tuple[str, dict[str, Any]]:
        self_pos = self.self_position()
        distance = _distance(self_pos, position)
        if distance is not None and distance <= VERIFY_NEAR_BLOCKS:
            try:
                payload = _unwrap(
                    await self.service.dig_capability(
                        int(position["x"]), int(position["y"]), int(position["z"])
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 读不到就是 unknown
                return "unknown", {"error": str(exc)[:80]}
            current = str(((payload.get("block") or {}) or {}).get("name") or "")
            reason = str(payload.get("reason") or "")
            if reason in _UNKNOWN_REASONS:
                return "unknown", {"reason": reason}
            if reason == "air" or not current:
                return "absent", {"reason": "air"}
            if name and current != name:
                return "absent", {"reason": "changed", "actual": current}
            return "present", {"block": current}
        if distance is None or distance > VERIFY_SCAN_BLOCKS:
            # 太远：这一层只做"看见就算数"，**绝不**因为"没看见"宣布它不存在
            return "unknown", {"reason": "too_far_to_verify"}
        try:
            payload = _unwrap(await self.service.find_blocks([name], int(VERIFY_SCAN_BLOCKS), 8))
        except Exception as exc:  # noqa: BLE001
            return "unknown", {"error": str(exc)[:80]}
        for row in payload.get("matches") or []:
            found = row.get("position") if isinstance(row, Mapping) else None
            if not isinstance(found, Mapping):
                continue
            near = _distance(found, position)
            if near is not None and near <= 2.0:
                return "present", {"block": name}
        return "unknown", {"reason": "not_in_scan"}

    async def _verify_area(
        self, position: Mapping[str, Any], radius: float = 0.0
    ) -> tuple[str, dict[str, Any]]:
        """地点类事实只确认"那一格现在是什么"（不做任何推测，§二十二）。

        ``radius > 0`` 时坐标是**聚簇锚点**（一片林子里的某一格），锚点变空说明不了什么
        → ``unknown``；``radius == 0`` 时这一格就是被记住的东西本身，空了就是 ``absent``。
        """
        distance = _distance(self.self_position(), position)
        if distance is not None and distance > VERIFY_SCAN_BLOCKS:
            return "unknown", {"reason": "too_far_to_verify"}
        try:
            payload = _unwrap(
                await self.service.dig_capability(
                    int(position["x"]), int(position["y"]), int(position["z"])
                )
            )
        except Exception as exc:  # noqa: BLE001
            return "unknown", {"error": str(exc)[:80]}
        reason = str(payload.get("reason") or "")
        current = str(((payload.get("block") or {}) or {}).get("name") or "")
        if reason in _UNKNOWN_REASONS:
            return "unknown", {"reason": reason}
        if reason == "air" or (not current and not reason):
            if float(radius or 0.0) <= 0.0:
                return "absent", {"reason": "air"}
            return "unknown", {"reason": "air_at_cluster_anchor"}
        if not current:
            return "unknown", {"reason": "block_unreadable"}
        return "present", {"reason": reason, "block": current}

    # ------------------------------------------------------------ 对账 / 检索 / 上下文

    async def reconcile(self, *, limit: int = 20) -> dict[str, Any]:
        server_id = self.server_id()
        if not server_id:
            return {}
        report = await self.reconciler.reconcile(server_id=server_id, limit=limit)
        self.last_reconcile = report.to_payload()
        if (report.invalidated or report.staled) and self._log is not None:
            self._log.info(
                "[Minecraft.Memory] reconcile: invalidated=%d staled=%d confirmed=%d",
                report.invalidated,
                report.staled,
                report.confirmed,
            )
        return self.last_reconcile

    async def context_block(
        self,
        *,
        text: str = "",
        platform: str = "",
        user_id: str = "",
        limit: int = 5,
    ) -> str:
        """给一次 LLM turn 的"相关 Minecraft 记忆"块（≤5 条、≤420 字；没有就空串）。"""
        server_id = self.server_id()
        if not server_id:
            return ""
        player_uuid = ""
        if platform and user_id:
            link = await self.link_for(platform=platform, user_id=user_id)
            if link is not None:
                player_uuid = link.canonical_uuid
        context = await self.retriever.retrieve(
            server_id=server_id,
            query=str(text or ""),
            player_uuid=player_uuid,
            position=self.self_position(),
            limit=limit,
        )
        block = context.block()
        if context.degraded and self._log is not None:
            # §四十：检索降级要如实说，绝不假装成功
            self._log.warning("[Minecraft.Memory] retrieval degraded (memory unavailable)")
        return block[:CONTEXT_MAX_CHARS]

    # ------------------------------------------------------------ 状态（WebUI/日志）

    async def status(self) -> MemoryBridgeStatus:
        server = self.server()
        server_id = server.server_id
        status = MemoryBridgeStatus(
            enabled=bool(server_id),
            server_id=server_id,
            server_label=server.display(),
            character_key=self.character_key,
            memory_degraded=self.store.degraded_reason,
            identity_degraded=getattr(self.identities, "degraded_reason", "")
            if self.identities
            else "",
            last_reconcile=dict(self.last_reconcile),
        )
        if not server_id:
            return status
        facts = await self.store.all_facts(server_id=server_id, limit=200)
        status.facts = len(facts)
        status.stale = sum(1 for fact in facts if fact.fresh is Freshness.STALE)
        status.invalidated = sum(1 for fact in facts if fact.fresh is Freshness.INVALIDATED)
        # 历史 scope 只数一下、给人看：它不参与检索/对账（§十一 决策记录）
        status.legacy = len(await self.store.legacy_facts(limit=200))
        if self.identities is not None:
            status.links = await self.identities.counts()
        return status

    async def facts_view(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """只读展示用的事实列表（不含原始聊天/世界快照；§十一/§四十一）。"""
        server_id = self.server_id()
        if not server_id:
            return []
        facts = await self.store.all_facts(server_id=server_id, limit=max(1, min(int(limit), 200)))
        return [fact.to_payload() for fact in facts]

    async def legacy_view(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """历史（缺限期）scope 的**只读**审计视图：每条都带 ``legacy_scope: true``。

        2026-10-08 决定：保留这批数据当证据，不删、不盲迁；这里只是让人看得见它。
        """
        facts = await self.store.legacy_facts(limit=max(1, min(int(limit), 200)))
        return [fact.to_payload() for fact in facts]

    async def links_view(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """只读展示用的绑定列表（UUID 只给尾号，§五：绝不外泄完整 UUID）。"""
        if self.identities is None:
            return []
        links = await self.identities.all_links(server_id=self.server_id(), limit=limit)
        return [link.user_facing() for link in links]


def _axis(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _distance(a: Any, b: Any) -> float | None:
    """两个坐标的欧氏距离（缺字段/不是数字 → None，绝不猜）。"""
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return None
    dx = _axis(a.get("x"))
    dy = _axis(a.get("y"))
    dz = _axis(a.get("z"))
    ex = _axis(b.get("x"))
    ey = _axis(b.get("y"))
    ez = _axis(b.get("z"))
    if None in (dx, dy, dz, ex, ey, ez):
        return None
    return ((dx - ex) ** 2 + (dy - ey) ** 2 + (dz - ez) ** 2) ** 0.5  # type: ignore[operator]


__all__ = [
    "CONTEXT_MAX_CHARS",
    "MinecraftMemoryBridge",
    "MemoryBridgeStatus",
    "SOURCE_CONFIGURED",
    "SOURCE_EXPLICIT",
    "CODE_CONFLICT",
    "CODE_NOT_FOUND",
    "FactSource",
    "MinecraftIdentity",
]
