"""Minecraft 确认门单测（Phase 4A §二十七）。

确认基础设施必须先于第一个 MEDIUM 动作可用：绑定 user + session + 参数指纹、
只能由 USER 回合消费、一次性、TTL 到期即失效。这里直测
:class:`~app.integrations.minecraft.confirmation.ConfirmationStore`（纯内存、注入时钟），
LLM Tool 侧的确认流程在 `tests/test_minecraft_agent_confirm_gate.py` 里走真实工具链路。
"""

from __future__ import annotations

from typing import Any

from app.character.turn import TurnOrigin
from app.integrations.minecraft.confirmation import (
    CANCELLED,
    CODE_EXPIRED,
    CODE_INVALID,
    CODE_MISMATCH,
    CODE_NOT_USER_TURN,
    CONFIRMED,
    CONSUMED,
    EXPIRED,
    PENDING,
    ConfirmationStore,
    arguments_hash,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_store(**overrides: Any) -> tuple[ConfirmationStore, FakeClock]:
    clock = FakeClock()
    store = ConfirmationStore(
        ttl_seconds=overrides.pop("ttl_seconds", 60.0),
        max_pending=overrides.pop("max_pending", 8),
        clock=clock,
    )
    return store, clock


def create(store: ConfirmationStore, **overrides: Any) -> Any:
    payload = {
        "session_id": "private:10001",
        "user_id": "10001",
        "tool": "minecraft_dig",
        "risk": "MEDIUM",
        "arguments": {"x": 120, "y": 64, "z": -230},
    }
    payload.update(overrides)
    return store.create(**payload)


# ------------------------------------------------------------------ 创建


def test_confirmation_created() -> None:
    store, _clock = make_store()
    item = create(store)
    assert item.status == PENDING
    assert item.confirmation_id.startswith("cfm_")
    assert item.arguments_hash == arguments_hash({"x": 120, "y": 64, "z": -230})
    assert item.expires_at - item.created_at == 60.0
    assert store.pending() == [item]
    # 同一个人同一个动作再要一次：复用同一条 PENDING（不刷屏、不重复授权）
    again = create(store)
    assert again.confirmation_id == item.confirmation_id
    # 投影里只给模型安全字段（id/工具/风险/摘要/有效期）
    payload = item.to_payload()
    assert set(payload) == {"confirmation_id", "tool", "risk", "summary", "expires_at", "status"}


def test_confirmation_requires_user_turn() -> None:
    store, _clock = make_store()
    item = create(store)
    outcome = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10001",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.USER,
    )
    assert outcome.ok and outcome.confirmation is not None
    assert item.status == CONSUMED


def test_confirmation_rejects_initiative() -> None:
    store, _clock = make_store()
    item = create(store)
    outcome = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10001",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.INITIATIVE,
    )
    assert not outcome.ok and outcome.code == CODE_NOT_USER_TURN
    assert item.status == PENDING, "非用户回合不得消费、也不得改动确认状态"


def test_confirmation_rejects_background() -> None:
    store, _clock = make_store()
    item = create(store)
    outcome = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10001",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.BACKGROUND,
    )
    assert not outcome.ok and outcome.code == CODE_NOT_USER_TURN
    assert item.status == PENDING


def test_confirmation_rejects_system() -> None:
    store, _clock = make_store()
    item = create(store)
    outcome = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10001",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.SYSTEM,
    )
    assert not outcome.ok and outcome.code == CODE_NOT_USER_TURN
    assert item.status == PENDING


# ------------------------------------------------------------ 归属与参数


def test_confirmation_matches_user() -> None:
    """§六：别的 QQ 用户拿同一个 id 不能执行（防劫持）。"""
    store, _clock = make_store()
    item = create(store)
    outcome = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10002",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.USER,
    )
    assert not outcome.ok and outcome.code == CODE_MISMATCH
    assert item.status == PENDING


def test_confirmation_matches_session() -> None:
    store, _clock = make_store()
    item = create(store)
    outcome = store.consume(
        item.confirmation_id,
        session_id="group:555",
        user_id="10001",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.USER,
    )
    assert not outcome.ok and outcome.code == CODE_MISMATCH
    assert item.status == PENDING


def test_confirmation_matches_arguments_hash() -> None:
    """§七：用户确认的是**哪个**动作不可变——参数变了就不是同一个动作。"""
    store, _clock = make_store()
    item = create(store)
    outcome = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10001",
        arguments={"x": 300, "y": 64, "z": -500},
        turn_origin=TurnOrigin.USER,
    )
    assert not outcome.ok and outcome.code == CODE_MISMATCH
    assert item.status == PENDING
    # 哈希本身对键顺序不敏感（同样的动作 = 同样的指纹）
    assert arguments_hash({"x": 1, "z": 3, "y": 2}) == arguments_hash({"z": 3, "x": 1, "y": 2})


# ------------------------------------------------------ 过期/一次性/取消


def test_confirmation_expires() -> None:
    store, clock = make_store(ttl_seconds=60.0)
    item = create(store)
    clock.advance(59.0)
    assert store.get(item.confirmation_id).status == PENDING  # type: ignore[union-attr]
    clock.advance(2.0)
    assert store.get(item.confirmation_id).status == EXPIRED  # type: ignore[union-attr]
    outcome = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10001",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.USER,
    )
    assert not outcome.ok and outcome.code == CODE_EXPIRED
    assert store.pending() == []


def test_confirmation_consumed_once() -> None:
    store, _clock = make_store()
    item = create(store)
    first = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10001",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.USER,
    )
    second = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10001",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.USER,
    )
    assert first.ok and not second.ok
    assert second.code == CODE_INVALID
    assert item.status == CONSUMED


def test_confirmation_cancelled() -> None:
    store, _clock = make_store()
    item = create(store)
    assert store.cancel(item.confirmation_id) is True
    assert item.status == CANCELLED
    assert store.cancel(item.confirmation_id) is False, "已取消的不能再取消"
    outcome = store.consume(
        item.confirmation_id,
        session_id="private:10001",
        user_id="10001",
        arguments={"x": 120, "y": 64, "z": -230},
        turn_origin=TurnOrigin.USER,
    )
    assert not outcome.ok and outcome.code == CODE_INVALID


def test_unknown_confirmation_is_invalid() -> None:
    store, _clock = make_store()
    outcome = store.consume(
        "cfm_does_not_exist",
        session_id="private:10001",
        user_id="10001",
        arguments={},
        turn_origin=TurnOrigin.USER,
    )
    assert not outcome.ok and outcome.code == CODE_INVALID


# ------------------------------------------------------------ 运维语义


def test_store_is_bounded() -> None:
    """有界内存：超出上限时最旧的先失效（绝不留一条仍有效的旧授权）。"""
    store, _clock = make_store(max_pending=3)
    ids = [create(store, arguments={"i": index}).confirmation_id for index in range(5)]
    assert len(store.pending()) <= 3
    for stale in ids[:2]:
        item = store.get(stale)
        assert item is None or item.status != PENDING, stale


def test_confirmation_snapshot_has_no_secrets() -> None:
    store, _clock = make_store()
    item = create(store, summary="挖掉一个方块")
    snapshot = store.snapshot()
    assert snapshot["ttl_seconds"] == 60.0
    assert snapshot["pending"][0]["summary"] == "挖掉一个方块"
    assert item.confirmation_id in {row["confirmation_id"] for row in snapshot["pending"]}
    # §二十七：日志/投影里绝不出现机密字样
    for forbidden in ("token", "password", "api_key"):
        assert forbidden not in str(snapshot)


def test_manual_expire() -> None:
    store, _clock = make_store()
    item = create(store)
    assert store.expire(item.confirmation_id) is True
    assert item.status == EXPIRED
    assert store.expire(item.confirmation_id) is False


def test_status_constants_are_distinct() -> None:
    assert {PENDING, CONFIRMED, EXPIRED, CANCELLED, CONSUMED} == {
        "PENDING",
        "CONFIRMED",
        "EXPIRED",
        "CANCELLED",
        "CONSUMED",
    }
