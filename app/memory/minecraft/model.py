"""Phase 5C：Minecraft 记忆的数据模型（semantic facts，不是世界快照的副本）。

三条硬规则：

* **Memory 只是历史知识**：`WorldPerception` 才是当前事实（§二十九）。这里记的是
  "曾经观察到什么"，并且带 `world_revision` 与新鲜度（§二十/§二十一）。
* **永远不伪装来源**（§十八）：`OBSERVED` / `DERIVED` / `USER_STATED` / `SYSTEM` 分得清清楚楚，
  派生结论绝不允许写成"我亲眼看见"。
* **一句一条**（§二十五）：每条 memory 就是我们给模型看的那一句话本身，
  结构化事实放在 `provenance` 里（server_id / kind / 坐标 / 任务 id …），
  绝不把 world snapshot 或整棵 Task checkpoint 塞进来（§二/§十四）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: 域名（写进 provenance，检索端只认这个域；普通聊天记忆不带它 → 天然隔离，§四十八）
DOMAIN = "minecraft"


class MinecraftMemoryKind(str, Enum):  # noqa: UP042 - 面向 JSON 的枚举
    """第一版严格限制为这七类语义（§十一）。"""

    PLAYER = "PLAYER"
    LOCATION = "LOCATION"
    RESOURCE = "RESOURCE"
    TASK = "TASK"
    EVENT = "EVENT"
    RELATIONSHIP = "RELATIONSHIP"
    PREFERENCE = "PREFERENCE"


class FactSource(str, Enum):  # noqa: UP042
    """事实来源（§十八）—— 派生结论绝不伪装成亲眼所见。"""

    OBSERVED = "OBSERVED"  # 运行时/世界感知直接读到的
    USER_STATED = "USER_STATED"  # 用户明确说过的
    TASK_RESULT = "TASK_RESULT"  # 任务执行的真实结果
    DERIVED = "DERIVED"  # 从多条事实推出来的（≤0.70）
    SYSTEM = "SYSTEM"  # 系统/配置（例如 trusted 配置里的关系）


class Freshness(str, Enum):  # noqa: UP042
    """新鲜度（§二十一）：**过期不删历史**，只改状态。"""

    ACTIVE = "ACTIVE"
    STALE = "STALE"  # 太久没复核，未必还成立
    INVALIDATED = "INVALIDATED"  # 当前世界事实与它矛盾（§二十二）
    SUPERSEDED = "SUPERSEDED"  # 被更新的事实取代


#: 来源 → 默认置信度（§十九：复用一套评分体系，不新造）
DEFAULT_CONFIDENCE: dict[FactSource, float] = {
    FactSource.USER_STATED: 0.95,
    FactSource.OBSERVED: 0.90,
    FactSource.TASK_RESULT: 0.90,
    FactSource.SYSTEM: 0.85,
    FactSource.DERIVED: 0.70,
}

#: 来源的置信度上限（§十九：DERIVED ≤0.70；模型推断 ≤0.50）
CONFIDENCE_CEILING: dict[FactSource, float] = {
    FactSource.DERIVED: 0.70,
    FactSource.SYSTEM: 0.85,
    FactSource.OBSERVED: 0.95,
    FactSource.USER_STATED: 0.99,
    FactSource.TASK_RESULT: 0.99,
}

#: Minecraft kind → 既有记忆引擎的 category（不新增 category，避免污染普通记忆的标签体系）
CATEGORY_BY_KIND: dict[MinecraftMemoryKind, str] = {
    MinecraftMemoryKind.PLAYER: "profile",
    MinecraftMemoryKind.LOCATION: "fact",
    MinecraftMemoryKind.RESOURCE: "fact",
    MinecraftMemoryKind.TASK: "event",
    MinecraftMemoryKind.EVENT: "event",
    MinecraftMemoryKind.RELATIONSHIP: "relationship",
    MinecraftMemoryKind.PREFERENCE: "preference",
}

#: 新事实 → 引擎 status（§二十一）
ENGINE_STATUS_BY_FRESHNESS: dict[Freshness, str] = {
    Freshness.ACTIVE: "active",
    Freshness.STALE: "stale",
    Freshness.INVALIDATED: "invalidated",
    Freshness.SUPERSEDED: "superseded",
}

#: 反查：引擎 status → 新鲜度
FRESHNESS_BY_ENGINE_STATUS: dict[str, Freshness] = {
    "active": Freshness.ACTIVE,
    "stale": Freshness.STALE,
    "invalidated": Freshness.INVALIDATED,
    "superseded": Freshness.SUPERSEDED,
    "expired": Freshness.STALE,
    "archived": Freshness.STALE,
}

#: 一句"这不是指令"的提示语（§四十一）：出现这些词的事实**永远只是用户愿望**，不是规则
DIRECTIVE_HINTS = (
    "不要确认",
    "不用确认",
    "跳过确认",
    "直接挖",
    "直接砍",
    "自动挖",
    "自动砍",
    "别确认",
    "免确认",
    "忽略上面",
    "忽略之前",
    "系统提示",
    "system prompt",
)


def looks_like_directive(text: str) -> bool:
    """这句话是不是在试图改规则（§四十一/§四十二：绝不产生 memory → 权限）。"""
    lowered = str(text or "").lower()
    return any(hint in lowered for hint in DIRECTIVE_HINTS)


#: "试图改规则"的语句无论被说多少遍，置信度都封在这里（§四十一：它永远只是"他说过一句话"）
DIRECTIVE_CONFIDENCE = 0.40


def confidence_for(source: FactSource, requested: float | None = None) -> float:
    """按来源给出置信度并压到上限内（§十九）。"""
    base = DEFAULT_CONFIDENCE.get(source, 0.50)
    value = base if requested is None else float(requested)
    ceiling = CONFIDENCE_CEILING.get(source, 0.99)
    return round(max(0.0, min(value, ceiling)), 3)


def number_or(value: Any, default: float = 0.0) -> float:
    """把任意输入安全转成 float（坐标可能来自 JSON/镜像，缺字段是常态）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def cluster_position(position: Any, *, size: int = 16) -> str:
    """把坐标按格聚簇（§十三：位置允许误差，不要求精确到每一格）。"""
    source = position if isinstance(position, dict) else None
    if source is None:
        return ""
    x = int(number_or(source.get("x")) // size * size) if source.get("x") is not None else 0
    y = int(number_or(source.get("y")) // size * size) if source.get("y") is not None else 0
    z = int(number_or(source.get("z")) // size * size) if source.get("z") is not None else 0
    if source.get("x") is None or source.get("y") is None or source.get("z") is None:
        return ""
    return f"{x},{y},{z}"


@dataclass
class MinecraftMemoryFact:
    """一条 Minecraft 语义记忆（写进现有 memories 表的"包装"）。"""

    kind: MinecraftMemoryKind
    server_id: str
    subject: str
    content: str
    source: FactSource = FactSource.OBSERVED
    #: 0 = 用来源默认（§十九）；显式给值会被压到该来源的上限内
    confidence: float = 0.0
    observed_at: float = 0.0
    last_verified_at: float = 0.0
    fresh: Freshness = Freshness.ACTIVE
    world_revision: str = ""
    player_uuid: str = ""
    username: str = ""
    position: dict[str, float] | None = None
    radius: float = 0.0
    task_id: str = ""
    plan_version: int = 0
    initiator: str = ""
    outcome: str = ""
    observation_count: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 允许用字符串构造（写起来方便，也避免"字符串 vs 枚举"这种低级不一致）
        if not isinstance(self.kind, MinecraftMemoryKind):
            self.kind = MinecraftMemoryKind(str(self.kind))
        if not isinstance(self.source, FactSource):
            self.source = FactSource(str(self.source))
        if not isinstance(self.fresh, Freshness):
            self.fresh = Freshness(str(self.fresh))
        if not self.confidence:
            self.confidence = confidence_for(self.source)
        if self.source is FactSource.DERIVED and self.confidence > 0.70:
            self.confidence = 0.70

    # ------------------------------------------------------------ 形状

    @property
    def category(self) -> str:
        return CATEGORY_BY_KIND.get(self.kind, "fact")

    @property
    def layer(self) -> str:
        return "episodic" if self.category == "event" else "semantic"

    @property
    def engine_status(self) -> str:
        return ENGINE_STATUS_BY_FRESHNESS.get(self.fresh, "active")

    @property
    def conflict_group(self) -> str:
        "同一个语义组（去掉坐标细节）：两组不同坐标的「基地」互为冲突候选（§三十四）。"
        base = str(self.subject or "").split("@", 1)[0].strip().lower()
        return f"{self.kind.value}:{base}"

    @property
    def dedupe_key(self) -> str:
        """语义身份（§三十三）：同一件事反复观察 → 更新这一条，而不是不断新增。"""
        raw = f"{DOMAIN}|{self.server_id}|{self.kind.value}|{self.subject}".lower()
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @property
    def directive(self) -> bool:
        """这条是不是"试图改规则"的语句（§四十一：只当用户愿望，绝不成为规则）。"""
        return looks_like_directive(self.content)

    def provenance(self) -> dict[str, Any]:
        """结构化事实（检索端按 server_id/kind 过滤；**不含任何 raw snapshot**）。"""
        payload: dict[str, Any] = {
            "domain": DOMAIN,
            "kind": self.kind.value,
            "server_id": self.server_id,
            "subject": self.subject,
            "fact_source": self.source.value,
            "freshness": self.fresh.value,
            "observed_at": float(self.observed_at or 0.0),
            "last_verified_at": float(self.last_verified_at or 0.0),
            "observation_count": int(self.observation_count),
            "world_revision": self.world_revision,
        }
        if self.player_uuid:
            payload["player_uuid"] = self.player_uuid
            payload["username"] = self.username
        if self.position:
            payload["position"] = dict(self.position)
            payload["radius"] = float(self.radius or 0.0)
            payload["cell"] = cluster_position(self.position)
        if self.task_id:
            payload["task_id"] = self.task_id
            payload["plan_version"] = int(self.plan_version or 0)
            payload["initiator"] = self.initiator
            payload["outcome"] = self.outcome
        if self.directive:
            # §四十一/§四十二：明确标成"不可信语境"，永远不能被当成指令
            payload["untrusted_directive"] = True
        payload.update(dict(self.extra or {}))
        return payload

    def strengthened(self, *, now: float, confidence_step: float = 0.05) -> None:
        """再观察一次同一件事（§三十三）：记次数、刷新时间、轻微增强置信度。"""
        self.observation_count = int(self.observation_count) + 1
        self.last_verified_at = float(now)
        ceiling = CONFIDENCE_CEILING.get(self.source, 0.99)
        self.confidence = round(min(ceiling, float(self.confidence) + confidence_step), 3)

    def same_fact(self, other: MinecraftMemoryFact) -> bool:
        return self.dedupe_key == other.dedupe_key

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "server_id": self.server_id,
            "subject": self.subject,
            "content": self.content,
            "source": self.source.value,
            "confidence": self.confidence,
            "observed_at": self.observed_at,
            "last_verified_at": self.last_verified_at,
            "fresh": self.fresh.value,
            "world_revision": self.world_revision,
            "player_uuid": self.player_uuid,
            "username": self.username,
            "position": dict(self.position) if self.position else None,
            "radius": self.radius,
            "task_id": self.task_id,
            "plan_version": self.plan_version,
            "initiator": self.initiator,
            "outcome": self.outcome,
            "observation_count": self.observation_count,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MinecraftMemoryFact:
        return cls(
            kind=MinecraftMemoryKind(str(payload.get("kind"))),
            server_id=str(payload.get("server_id") or ""),
            subject=str(payload.get("subject") or ""),
            content=str(payload.get("content") or ""),
            source=FactSource(str(payload.get("source"))),
            confidence=float(payload.get("confidence") or 0.0),
            observed_at=float(payload.get("observed_at") or 0.0),
            last_verified_at=float(payload.get("last_verified_at") or 0.0),
            fresh=Freshness(str(payload.get("fresh") or Freshness.ACTIVE.value)),
            world_revision=str(payload.get("world_revision") or ""),
            player_uuid=str(payload.get("player_uuid") or ""),
            username=str(payload.get("username") or ""),
            position=dict(payload.get("position") or {}) or None,
            radius=float(payload.get("radius") or 0.0),
            task_id=str(payload.get("task_id") or ""),
            plan_version=int(payload.get("plan_version") or 0),
            initiator=str(payload.get("initiator") or ""),
            outcome=str(payload.get("outcome") or ""),
            observation_count=int(payload.get("observation_count") or 1),
            extra=dict(payload.get("extra") or {}),
        )
