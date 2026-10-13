"""Phase 8B §九：跨平台身份上下文（解析矩阵 / 连续性隔离 / 信任分层）。

覆盖任务书 §九 三组：

* A 身份解析矩阵（VERIFIED / UNBOUND / REVOKED / CONFLICT / SERVER_MISMATCH / DEGRADED）；
* B 跨平台连续性与通道隔离（同一个人共用同一条关系；未绑定绝不合并）；
* C 身份与授权严格分层（核验不产生权限，读取路径不碰 Service/动作）。

测试确定性：真实 SQLite（IdentityStore + MemoryManager）+ 假 Service，不用 sleep、
不依赖真实 LLM、不依赖真实 Minecraft 服务器。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from app.integrations.minecraft.identity import IdentityStore
from app.integrations.minecraft.identity_context import (
    PERMISSION_EFFECT,
    IdentityChannel,
    IdentityResolution,
    IdentityResolver,
    channel_of,
)
from tests.minecraft_memory_fakes import UUID_KONGLING, UUID_OTHER, build_bridge
from tests.test_minecraft_agent_confirm_gate import decide
from tests.test_minecraft_agent_tools import FakeMinecraftService
from tests.test_minecraft_chat_bridge import stack

QQ_ID = "2731431246"
OTHER_QQ = "1234567"
PLAYER = "空凛"
MC_SESSION = "minecraft:127.0.0.1:25565:空凛"


class _StubStore:
    """可控的只读 Store 替身（只为构造 DEGRADED 场景，行为模仿真实 IdentityStore）。"""

    def __init__(self, *, fail_on: str = "", raise_on: str = "") -> None:
        # 真实 IdentityStore 的降级标记是**读取失败时**才写上的「最近一次失败原因」，
        # 所以替身也必须这样：初始为空、读取时才置位（否则测不出「本次读取降级」）。
        self.degraded_reason = ""
        self._fail_on = fail_on
        self._raise_on = raise_on

    def _maybe_fail(self, hook: str) -> None:
        if self._fail_on == hook:
            self.degraded_reason = "OperationalError"

    async def verified(self, **kwargs: Any) -> Any:
        if self._raise_on == "verified":
            raise RuntimeError("store down")
        self._maybe_fail("verified")
        return None

    async def links_for(self, **kwargs: Any) -> list[Any]:
        if self._raise_on == "links_for":
            raise RuntimeError("store down")
        self._maybe_fail("links_for")
        return []

    async def all_links(self, **kwargs: Any) -> list[Any]:
        return []

    async def by_player(self, **kwargs: Any) -> Any:
        return None


class _ServerSwap:
    """把真实桥包一层，只替换 server_id（模拟换到另一台服务器）。"""

    def __init__(self, inner: Any, server_id: str) -> None:
        self._inner = inner
        self._server_id = server_id
        self.identities = inner.identities

    def server_id(self) -> str:
        return self._server_id

    def player_named(self, username: str) -> Any:
        return self._inner.player_named(username)


async def _env(
    tmp_path: Path,
    *,
    players: list[dict[str, Any]] | None = None,
    db_name: str = "mc8b.db",
) -> tuple[Any, IdentityResolver, FakeMinecraftService, Any]:
    """真实 SQLite 记忆桥（含真实 IdentityStore）+ 只读解析器 + 假 Service。"""
    if players is None:
        players = [{"name": PLAYER, "uuid": UUID_KONGLING}]
    service = FakeMinecraftService(players=players)
    bridge, db, _manager, svc = await build_bridge(tmp_path, service=service, db_name=db_name)
    resolver = IdentityResolver(bridge_getter=lambda: bridge)
    return bridge, resolver, svc, db


async def _bind(
    bridge: Any,
    *,
    platform: str = "qq",
    user_id: str = QQ_ID,
    username: str = PLAYER,
    uuid: str = UUID_KONGLING,
) -> Any:
    return await bridge.identities.verify(
        platform=platform,
        user_id=user_id,
        server_id=bridge.server_id(),
        player_uuid=uuid,
        username=username,
    )


def test_channel_detection_only_uses_transport_facts() -> None:
    assert channel_of(session_id=MC_SESSION) is IdentityChannel.MINECRAFT_CHAT
    assert channel_of(session_id="private:1") is IdentityChannel.QQ_PRIVATE
    assert channel_of(session_id="group:1", is_group=True) is IdentityChannel.QQ_GROUP
    assert channel_of(session_id="x", origin_metadata={"source": "webui"}) is IdentityChannel.WEBUI


async def test_qq_verified_resolves_to_the_canonical_person(tmp_path: Path) -> None:
    """A：同服务器 VERIFIED 的 QQ 侧解析到 canonical 主体，且事实里没有敏感 ID。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge)).ok
        ctx = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert ctx.resolution is IdentityResolution.VERIFIED
        assert ctx.channel is IdentityChannel.QQ_PRIVATE
        assert ctx.person_ref == "qq:" + QQ_ID
        assert ctx.relationship_key == QQ_ID
        assert ctx.permission_effect == PERMISSION_EFFECT
        facts = ctx.facts()
        assert facts
        assert QQ_ID not in facts and UUID_KONGLING not in facts
        assert "动作授权" in facts
    finally:
        await db.close()


async def test_minecraft_chat_resolves_via_the_authoritative_uuid(tmp_path: Path) -> None:
    """A/B：游戏内说话的人经运行时权威 UUID 解析到同一个**人物键**。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge)).ok
        ctx = await resolver.resolve(session_id=MC_SESSION, user_id=PLAYER, user_name=PLAYER)
        assert ctx.resolution is IdentityResolution.VERIFIED
        assert ctx.channel is IdentityChannel.MINECRAFT_CHAT
        assert ctx.relationship_key == QQ_ID
        assert ctx.platform == "qq" and ctx.platform_user_id == QQ_ID
        assert ctx.uuid_suffix == UUID_KONGLING[-4:]
        assert ctx.memory_identity(platform="minecraft_chat", user_id=PLAYER) == ("qq", QQ_ID)
    finally:
        await db.close()


async def test_minecraft_without_authoritative_uuid_never_binds(tmp_path: Path) -> None:
    """§五：只有 username、拿不到权威 UUID 时不得按名字合并，也不注入任何事实。"""
    bridge, resolver, _svc, db = await _env(tmp_path, players=[{"name": PLAYER}])
    try:
        assert (await _bind(bridge)).ok
        ctx = await resolver.resolve(session_id=MC_SESSION, user_id=PLAYER, user_name=PLAYER)
        assert ctx.resolution is IdentityResolution.UNBOUND
        assert ctx.reason == "no_authoritative_uuid"
        assert ctx.relationship_key == PLAYER
        assert ctx.facts() == ""
        assert ctx.memory_identity(platform="minecraft_chat", user_id=PLAYER) == (
            "minecraft_chat",
            PLAYER,
        )
    finally:
        await db.close()


async def test_same_username_different_uuid_is_a_different_person(tmp_path: Path) -> None:
    """A：同名不同 UUID 绝不合并（以 UUID 为准）。"""
    players = [{"name": PLAYER, "uuid": UUID_KONGLING}, {"name": PLAYER, "uuid": UUID_OTHER}]
    bridge, resolver, _svc, db = await _env(tmp_path, players=players)
    try:
        assert (await _bind(bridge, user_id=QQ_ID, uuid=UUID_KONGLING)).ok
        assert (await _bind(bridge, user_id=OTHER_QQ, uuid=UUID_OTHER)).ok
        first = await bridge.identities.by_player(
            server_id=bridge.server_id(), player_uuid=UUID_KONGLING
        )
        second = await bridge.identities.by_player(
            server_id=bridge.server_id(), player_uuid=UUID_OTHER
        )
        assert first is not None and first.user_id == QQ_ID
        assert second is not None and second.user_id == OTHER_QQ
    finally:
        await db.close()


async def test_rename_keeps_the_same_person(tmp_path: Path) -> None:
    """A：同 UUID 改名后仍是同一个 canonical 主体。"""
    bridge, resolver, _svc, db = await _env(
        tmp_path, players=[{"name": "空凛2", "uuid": UUID_KONGLING}]
    )
    try:
        assert (await _bind(bridge, username=PLAYER)).ok
        ctx = await resolver.resolve(
            session_id="minecraft:127.0.0.1:25565:空凛2", user_id="空凛2", user_name="空凛2"
        )
        assert ctx.resolution is IdentityResolution.VERIFIED
        assert ctx.relationship_key == QQ_ID
    finally:
        await db.close()


async def test_revoked_link_is_never_reused(tmp_path: Path) -> None:
    """A：解绑之后 QQ 与游戏内两侧都不得沿用旧关联。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge)).ok
        assert (await bridge.identities.revoke(platform="qq", user_id=QQ_ID)).ok
        qq = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        mc = await resolver.resolve(session_id=MC_SESSION, user_id=PLAYER, user_name=PLAYER)
        assert qq.resolution is IdentityResolution.REVOKED
        assert mc.resolution is IdentityResolution.REVOKED
        assert qq.relationship_key == QQ_ID and mc.relationship_key == PLAYER
        assert "解除" in qq.facts() and "解除" in mc.facts()
    finally:
        await db.close()


async def test_conflict_is_reported_not_merged(tmp_path: Path) -> None:
    """A：同一 UUID 的第二个人得到 CONFLICT；原绑定不受影响。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge, user_id=QQ_ID, uuid=UUID_KONGLING)).ok
        second = await _bind(bridge, user_id=OTHER_QQ, uuid=UUID_KONGLING)
        assert not second.ok
        ctx = await resolver.resolve(session_id="private:2", user_id=OTHER_QQ, user_name="另一个人")
        assert ctx.resolution is IdentityResolution.CONFLICT
        assert ctx.reason == "conflict_record"
        first = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert first.resolution is IdentityResolution.VERIFIED
    finally:
        await db.close()


async def test_server_mismatch_never_reuses_a_link_across_servers(tmp_path: Path) -> None:
    """A：绑定绝不跨服务器复用（server_id 必须匹配当前连接）。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge)).ok
        elsewhere = IdentityResolver(bridge_getter=lambda: _ServerSwap(bridge, "mc-somewhereelse"))
        ctx = await elsewhere.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert ctx.resolution is IdentityResolution.SERVER_MISMATCH
        assert ctx.reason == "server_identity_mismatch"
        assert ctx.relationship_key == QQ_ID and ctx.facts()
    finally:
        await db.close()


async def test_store_error_during_read_is_reported_as_degraded(tmp_path: Path) -> None:
    """A：读取期间存储报错 → 明确 DEGRADED，绝不假装没有绑定。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        bridge.identities = _StubStore(fail_on="verified")
        ctx = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert ctx.resolution is IdentityResolution.DEGRADED
        assert ctx.reason == "identity_store_error"
        assert ctx.degraded_reason == "OperationalError"
        assert ctx.permission_effect == PERMISSION_EFFECT
    finally:
        await db.close()


async def test_store_exception_is_degraded_not_raised(tmp_path: Path) -> None:
    """A：解析器永不抛异常（抛错也降级），普通聊天不受影响。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        bridge.identities = _StubStore(raise_on="verified")
        ctx = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert ctx.resolution is IdentityResolution.DEGRADED
        assert ctx.reason == "resolve_error"
        assert ctx.facts() and ctx.permission_effect == PERMISSION_EFFECT
    finally:
        await db.close()


async def test_identity_layer_absent_is_degraded_and_grants_nothing() -> None:
    """A：没有身份层 → 显式 DEGRADED（不猜人、不产生任何权限）。"""
    resolver = IdentityResolver(bridge_getter=lambda: None)
    ctx = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
    assert ctx.resolution is IdentityResolution.DEGRADED
    assert ctx.reason == "identity_layer_absent"
    assert ctx.relationship_key == QQ_ID
    assert ctx.memory_identity(platform="qq", user_id=QQ_ID) == ("qq", QQ_ID)
    assert ctx.permission_effect == PERMISSION_EFFECT
    assert "降级" in ctx.facts()


async def test_resolution_is_read_only_and_stable(tmp_path: Path) -> None:
    """A/C：解析是只读的 —— 不写库、不调用 Service、重复解析结果一致。"""
    bridge, resolver, svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge)).ok
        before = await bridge.identities.counts()
        ctxs = [
            await resolver.resolve(session_id=MC_SESSION, user_id=PLAYER, user_name=PLAYER)
            for _ in range(3)
        ]
        assert {str(c.resolution) for c in ctxs} == {"VERIFIED"}
        assert {c.relationship_key for c in ctxs} == {QQ_ID}
        assert await bridge.identities.counts() == before
        assert svc.action_calls("move_to") == [] and svc.action_calls("chat") == []
    finally:
        await db.close()


async def test_uuid_case_and_hyphens_are_normalized(tmp_path: Path) -> None:
    """A：canonical UUID 大小写与连字符规范化后仍命中同一个人。"""
    messy = "-".join(
        [
            UUID_KONGLING[0:8],
            UUID_KONGLING[8:12],
            UUID_KONGLING[12:16],
            UUID_KONGLING[16:20],
            UUID_KONGLING[20:],
        ]
    ).upper()
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge, uuid=messy)).ok
        ctx = await resolver.resolve(session_id=MC_SESSION, user_id=PLAYER, user_name=PLAYER)
        assert ctx.resolution is IdentityResolution.VERIFIED
        assert ctx.relationship_key == QQ_ID
    finally:
        await db.close()


async def _attach_identity(
    env: Any, tmp_path: Path, *, db_name: str = "mc8b_chat.db"
) -> tuple[Any, Any]:
    """给真实聊天栈装上真实记忆桥与只读解析器（复用同一个 Service 的世界视图）。"""
    bridge, db, _manager, _svc = await build_bridge(tmp_path, service=env.service, db_name=db_name)
    env.runtime.minecraft_memory = bridge
    env.runtime.identity_resolver = IdentityResolver(bridge_getter=lambda: bridge)
    return bridge, db


async def test_verified_player_shares_the_qq_relationship_row(tmp_path: Path) -> None:
    """B：游戏内说话的同一个人 → 关系落在同一条（QQ 键）上；一条消息只计一次。"""
    service = FakeMinecraftService(players=[{"name": PLAYER, "uuid": UUID_KONGLING}])
    async with stack(tmp_path, "嗯嗯，我在呢", service=service) as env:
        bridge, db = await _attach_identity(env, tmp_path)
        try:
            assert (await _bind(bridge)).ok
            await env.say(PLAYER, "你好呀")
            linked = await env.runtime.relationships.get(QQ_ID)
            assert linked.interaction_count == 1
            assert env.turns == 1
            display = await env.runtime.relationships.get(PLAYER)
            assert display.interaction_count == 0
        finally:
            await db.close()


async def test_unverified_player_keeps_a_local_identity(tmp_path: Path) -> None:
    """B：没有绑定 → 游戏内身份保持本地键，绝不与 QQ 侧合并。"""
    service = FakeMinecraftService(players=[{"name": PLAYER, "uuid": UUID_KONGLING}])
    async with stack(tmp_path, "嗯嗯", service=service) as env:
        bridge, db = await _attach_identity(env, tmp_path, db_name="mc8b_chat2.db")
        try:
            await env.say(PLAYER, "你好呀")
            local = await env.runtime.relationships.get(PLAYER)
            assert local.interaction_count == 1
            untouched = await env.runtime.relationships.get(QQ_ID)
            assert untouched.interaction_count == 0
        finally:
            await db.close()


async def test_verified_identity_grants_no_low_action(tmp_path: Path) -> None:
    """C：核验过身份但玩家不在 trusted_players → LOW 动作仍被原策略拒绝且无动作。"""
    service = FakeMinecraftService(players=[{"name": PLAYER, "uuid": UUID_KONGLING}])
    low = decide("minecraft_move_to", x=120, y=64, z=-230)
    async with stack(tmp_path, low, service=service, trusted=[]) as env:
        bridge, db = await _attach_identity(env, tmp_path, db_name="mc8b_c1.db")
        try:
            assert (await _bind(bridge)).ok
            await env.say(PLAYER, "走到 (120,64,-230)")
            assert env.service.action_calls("move_to") == []
            assert env.verdicts, "策略拒绝必须留下可诊断的结论"
            assert env.service.action_calls("chat") != []
        finally:
            await db.close()


async def test_medium_action_still_needs_confirmation_with_verified_identity(
    tmp_path: Path,
) -> None:
    """C：身份核验不绕过 MEDIUM 确认门（不得静默确认）。"""
    service = FakeMinecraftService(players=[{"name": PLAYER, "uuid": UUID_KONGLING}])
    query = decide("minecraft_find_blocks", block_names=["minecraft:oak_log"], max_distance=8)
    async with stack(tmp_path, query, service=service) as env:
        bridge, db = await _attach_identity(env, tmp_path, db_name="mc8b_c2.db")
        try:
            assert (await _bind(bridge)).ok
            await env.say(PLAYER, "附近有橡木吗")
            # 只读查询照常执行（SAFE），身份上下文不改变任何风险判定。
            assert env.service.action_calls("find_blocks") != []
        finally:
            await db.close()


def test_identity_module_never_reaches_the_action_layers() -> None:
    """C：身份模块源码里不得出现策略/动作/桥接入口（结构性证明它无法授权）。"""
    source = (Path("app") / "integrations" / "minecraft" / "identity_context.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "minecraft_agent",
        "authorize(",
        "MinecraftService",
        "confirm",
        "origin_metadata[",
    ):
        assert forbidden not in source
    assert 'PERMISSION_EFFECT = "none"' in source


# --------------------------------------------------------- §四 降级契约回归（8B Follow-up）


class _FlakyDB:
    """把真实 Database 包一层，按需让**读取**抛同一个异常类型（确定性故障注入）。

    只包 ``fetchall``（IdentityStore 的读取路径）；写入（绑定/解绑）不受影响。
    ``fail_user`` 只让携带该 user_id 的查询失败 —— 用于并发隔离用例。
    """

    def __init__(self, inner: Any, *, fail_user: str = "", gate: Any = None) -> None:
        self._inner = inner
        self.fail_user = fail_user
        self.gate = gate
        self.fail = True
        self.failures = 0

    async def fetchall(self, sql: str, params: tuple[Any, ...]) -> Any:
        targeted = (not self.fail_user) or (self.fail_user in str(params))
        if self.fail and targeted:
            if self.gate is not None and not self.gate.is_set():
                await self.gate.wait()
            self.failures += 1
            raise sqlite3.OperationalError("identity store unavailable")
        return await self._inner.fetchall(sql, params)


async def _flaky_env(tmp_path: Path, *, fail_user: str = "", gate: Any = None) -> Any:
    """真实 Store（换成故障 DB 的同一个 Store 类）+ 解析器；仍然只有一套身份存储。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    flaky = _FlakyDB(db, fail_user=fail_user, gate=gate)
    bridge.identities = IdentityStore(flaky)
    return bridge, resolver, flaky, db


async def test_first_read_failure_is_degraded(tmp_path: Path) -> None:
    """§四：首次读取失败 → DEGRADED（不是 UNBOUND）。"""
    bridge, resolver, flaky, db = await _flaky_env(tmp_path)
    try:
        ctx = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert ctx.resolution is IdentityResolution.DEGRADED
        assert ctx.reason == "identity_store_error"
        assert ctx.degraded_reason == "OperationalError"
        assert flaky.failures >= 1
        assert ctx.permission_effect == PERMISSION_EFFECT
    finally:
        await db.close()


async def test_same_exception_twice_is_degraded_both_times(tmp_path: Path) -> None:
    """§四 2：同类异常连续发生两次 → 两次都必须 DEGRADED（旧判据会漏掉第二次）。"""
    bridge, resolver, flaky, db = await _flaky_env(tmp_path)
    try:
        first = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        second = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert first.resolution is IdentityResolution.DEGRADED
        assert second.resolution is IdentityResolution.DEGRADED
        assert str(bridge.identities.degraded_reason) == "OperationalError"
        assert flaky.failures >= 2
    finally:
        await db.close()


async def test_historical_marker_does_not_hide_a_new_failure(tmp_path: Path) -> None:
    """§四 3：已有历史错误标记时再次发生同类错误，当前解析仍必须是 DEGRADED。"""
    bridge, resolver, _flaky, db = await _flaky_env(tmp_path)
    try:
        bridge.identities.degraded_reason = "OperationalError"  # 历史遗留标记
        ctx = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert ctx.resolution is IdentityResolution.DEGRADED
        assert ctx.reason == "identity_store_error"
    finally:
        await db.close()


async def test_recovery_restores_accurate_resolution(tmp_path: Path) -> None:
    """§四 4：存储恢复后按真实数据恢复（VERIFIED / UNBOUND），不是永久降级。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge)).ok
        flaky = _FlakyDB(db)
        bridge.identities = IdentityStore(flaky)
        broken = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert broken.resolution is IdentityResolution.DEGRADED
        flaky.fail = False
        healed = await resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        assert healed.resolution is IdentityResolution.VERIFIED
        assert healed.relationship_key == QQ_ID
        other = await resolver.resolve(
            session_id="private:2", user_id=OTHER_QQ, user_name="另一个人"
        )
        assert other.resolution is IdentityResolution.UNBOUND
    finally:
        await db.close()


async def test_minecraft_side_satisfies_the_same_contract(tmp_path: Path) -> None:
    """§四：MC 侧同样满足降级契约（连续两次失败都 DEGRADED；恢复后按真实数据）。"""
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge)).ok
        flaky = _FlakyDB(db)
        bridge.identities = IdentityStore(flaky)
        one = await resolver.resolve(session_id=MC_SESSION, user_id=PLAYER, user_name=PLAYER)
        two = await resolver.resolve(session_id=MC_SESSION, user_id=PLAYER, user_name=PLAYER)
        assert one.resolution is IdentityResolution.DEGRADED
        assert two.resolution is IdentityResolution.DEGRADED
        flaky.fail = False
        healed = await resolver.resolve(session_id=MC_SESSION, user_id=PLAYER, user_name=PLAYER)
        assert healed.resolution is IdentityResolution.VERIFIED
        assert healed.relationship_key == QQ_ID
    finally:
        await db.close()


async def test_concurrent_resolutions_do_not_pollute_each_other(tmp_path: Path) -> None:
    """§四：并发解析互不污染 —— 一个请求的失败不得影响另一个的成功。

    用**按请求定向**的故障注入（只有失败那个请求的查询会抛）+ 门闩，不依赖 sleep。
    """
    bridge, resolver, _svc, db = await _env(tmp_path)
    try:
        assert (await _bind(bridge)).ok
        gate = asyncio.Event()
        flaky = _FlakyDB(db, fail_user=QQ_ID, gate=gate)
        bridge.identities = IdentityStore(flaky)
        failing = asyncio.create_task(
            resolver.resolve(session_id="private:1", user_id=QQ_ID, user_name="某人")
        )
        healthy = asyncio.create_task(
            resolver.resolve(session_id=MC_SESSION, user_id=PLAYER, user_name=PLAYER)
        )
        gate.set()  # 放行失败请求；健康请求本就不等门闩
        broken, ok = await asyncio.gather(failing, healthy)
        assert broken.resolution is IdentityResolution.DEGRADED
        assert ok.resolution is IdentityResolution.VERIFIED
        assert ok.relationship_key == QQ_ID
    finally:
        await db.close()


async def test_identity_failure_never_blocks_normal_chat(tmp_path: Path) -> None:
    """§四：身份层异常仍然不影响普通聊天继续执行（真实聊天栈）。"""
    service = FakeMinecraftService(players=[{"name": PLAYER, "uuid": UUID_KONGLING}])
    async with stack(tmp_path, "嗯嗯，在呢", service=service) as env:
        bridge, db = await _attach_identity(env, tmp_path, db_name="mc8b_deg.db")
        try:
            bridge.identities = IdentityStore(_FlakyDB(db))
            await env.say(PLAYER, "你好呀")
            assert env.turns == 1
            assert env.chat_replies and env.chat_replies[-1] == "嗯嗯，在呢"
        finally:
            await db.close()
