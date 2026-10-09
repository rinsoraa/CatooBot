"""Phase 7B §三/§四/§六：Initiative → ActivityPlanner 的**只读结构化建议**（桥）。

分层（§二：不建平行系统，也不引入反向依赖）：

```text
LifeIntentService.hints()      →  纯数据（dict / duck-typed 对象）
        ↓                            ↓
  （7A 的意图包只 import 自己）   InitiativeHintBook（本模块，activity 侧）
        ↓
  ActivityPlanner 候选评分里的一个**软**项：initiative_fit
```

三条硬性质：

* **只改变合格候选之间的软排序**（§四）：本模块不碰资格、不碰硬约束、不碰 6B 护栏；
  一个名字必须先通过 Planner 自己的 ``_rejection`` 才有资格被加分；
* **名称必须来自实际 Registry**（§六）：不存在的名字（例如 ``minecraft``）会被**丢掉**，
  ``minecraft_task`` 更是永远不是虚拟活动（那是 Task→Activity Adapter 的活）；
* **有界、确定、可降级**（§三.3/§三.6）：一次规划最多采纳 ``MAX_HINTS`` 条；
  同一个活动只取**最大**加成（绝不叠加，§三.4）；来源读失败 → 空书 + 降级说明，
  Planner 逐字退回原有行为（§五）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.activity.model import looks_like_minecraft_activity
from app.activity.profiles import plannable, profile_for

#: 一次规划最多采纳几条建议（bounded，§三.3）。
#: ★真机教训：她同时挂着 4 条念头时，上限 3 会把其中一条（当时正好是 MINECRAFT_INTEREST）
#: 整条挤出去。放宽到 5（**仍然有界**，与 7A 的记忆窗口同宽）。
MAX_HINTS = 5
#: 评分里的软项名（§四：权重必须低于既有锚点/习惯/目标项）
HINT_TERM = "initiative_fit"

#: 意图类型 → **已注册的虚拟活动**（§六：以实际 Registry 为准）
#: 空串表示"用它自己的 related_activity"（例如长闲置意图里 Planner 建议的那件事）。
INTENT_ACTIVITY_MAP: dict[str, str] = {
    # 虚拟的"捣鼓建造"——**不是**连服务器、**不是**真实 Minecraft 动作（§六/§三十六）
    "MINECRAFT_INTEREST": "building",
    "REST": "relax",
    "SOCIAL": "online",
    "ACTIVITY_CHANGE": "",
    "PERSONAL_ROUTINE": "",
}

#: 永远不能作为虚拟活动的名字（§六：``minecraft_task`` 只能由 Task 适配器创建）
FORBIDDEN_ACTIVITY_MARKERS = ("task", "dig", "mine", "move", "place", "follow", "pickup")


@dataclass(frozen=True)
class InitiativeActivityHint:
    """一条"角色的主动想法想把某个活动往前排一点"（§三 的建议形状）。

    ``score_bonus`` ∈ [0,1]：**软**偏好，最终由 Planner 乘上自己的权重 ——
    它绝不可能让一个不合格的候选变得合格（§四）。
    """

    activity_name: str
    score_bonus: float
    intent_id: str = ""
    source: str = ""
    intent_type: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "activity_name": str(self.activity_name),
            "score_bonus": round(max(0.0, min(1.0, float(self.score_bonus))), 4),
            "intent_id": str(self.intent_id),
            "source": str(self.source),
            "intent_type": str(self.intent_type),
        }


@dataclass(frozen=True)
class InitiativeHintBook:
    """一次规划里**采纳**的建议集合（只读；同一活动只留最大加成，绝不叠加）。"""

    hints: tuple[InitiativeActivityHint, ...] = ()
    dropped: tuple[dict[str, Any], ...] = ()
    degraded: str = ""
    limit: int = MAX_HINTS

    @property
    def empty(self) -> bool:
        return not self.hints

    def hint_for(self, activity: str) -> InitiativeActivityHint | None:
        wanted = str(activity or "").strip().lower()
        for hint in self.hints:
            if hint.activity_name == wanted:
                return hint
        return None

    def bonus_for(self, activity: str) -> float:
        hint = self.hint_for(activity)
        return float(hint.score_bonus) if hint is not None else 0.0

    def activities(self) -> tuple[str, ...]:
        """被建议的活动名（Planner 用它把候选池扩到"她真正想做的那件事"）。"""
        return tuple(hint.activity_name for hint in self.hints)

    def payload(self) -> dict[str, Any]:
        return {
            "hints": [hint.to_payload() for hint in self.hints],
            "dropped": [dict(item) for item in self.dropped],
            "degraded": str(self.degraded),
            "limit": int(self.limit),
        }


#: 空书（没有建议 / 来源不可用）
EMPTY_HINT_BOOK = InitiativeHintBook()


def _read_field(item: Any, *names: str) -> Any:
    for name in names:
        if isinstance(item, Mapping) and name in item:
            return item[name]
        value = getattr(item, name, None)
        if value is not None:
            return value
    return None


def _registered(name: Any) -> bool:
    """名字是不是**已注册的虚拟活动**（§六：以实际 Registry 为准，不假定存在）。"""
    text = str(name or "").strip().lower()
    if not text:
        return False
    if looks_like_minecraft_activity(text):
        return False  # 连 "minecraft" 本身都不注册（6C 的老规矩）
    if any(marker in text for marker in FORBIDDEN_ACTIVITY_MARKERS):
        return False  # minecraft_task / dig / move_to … 永远不是虚拟活动
    return bool(profile_for(text)) and plannable(text)


def normalise_hint(item: Any) -> tuple[InitiativeActivityHint | None, str]:
    """把来源给的原始建议规范化（返回 ``(hint, 丢弃原因)``）。"""
    intent_type = str(_read_field(item, "intent_type", "type") or "").strip().upper()
    # 显式的 ``activity_name`` 优先（§三 的建议形状）；7A 的服务交的是 ``related_activity``
    related = (
        str(_read_field(item, "activity_name", "related_activity", "activity") or "")
        .strip()
        .lower()
    )
    mapped = INTENT_ACTIVITY_MAP.get(intent_type, "")
    activity = related or mapped
    if not activity:
        return None, "no_activity"
    if not _registered(activity):
        return None, "not_registered"
    try:
        bonus = float(_read_field(item, "score_bonus", "bonus", "priority") or 0.0)
    except (TypeError, ValueError):
        bonus = 0.0
    hint = InitiativeActivityHint(
        activity_name=activity,
        score_bonus=max(0.0, min(1.0, bonus)),
        intent_id=str(_read_field(item, "intent_id") or ""),
        source=str(_read_field(item, "source") or ""),
        intent_type=intent_type,
    )
    return hint, ""


def hint_book_from(source: Any, *, now: float, limit: int = MAX_HINTS) -> InitiativeHintBook:
    """从一个**只读**来源取建议并整理成书（bounded / 确定 / 可降级，§三）。

    ``source`` 是鸭子类型的：只要有一个 ``hints(*, limit, now)`` 或可调用对象即可。
    任何异常都只会得到一本**空书** + 降级说明 —— Planner 逐字退回原有行为（§五）。
    """
    if source is None:
        return EMPTY_HINT_BOOK
    bounded = max(0, min(MAX_HINTS, int(limit)))
    if bounded == 0:
        return InitiativeHintBook(limit=0)
    reader = getattr(source, "hints", None)
    try:
        raw = reader(limit=bounded, now=now) if callable(reader) else None
    except Exception as exc:  # noqa: BLE001 - 建议读失败绝不拖垮规划
        return InitiativeHintBook(degraded=f"{type(exc).__name__}: {exc}"[:120], limit=bounded)
    if raw is None:
        return InitiativeHintBook(limit=bounded)
    hints: list[InitiativeActivityHint] = []
    dropped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(raw):
        hint, reason = normalise_hint(item)
        if hint is None:
            dropped.append(
                {
                    "intent_id": str(_read_field(item, "intent_id") or ""),
                    "reason": reason or "invalid",
                }
            )
            continue
        if hint.activity_name in seen:
            # §三.4：同一个活动只留**最大**加成，绝不因为重复 check 累积
            index = next(
                idx
                for idx, existing in enumerate(hints)
                if existing.activity_name == hint.activity_name
            )
            if hint.score_bonus > hints[index].score_bonus:
                hints[index] = hint
            continue
        seen.add(hint.activity_name)
        hints.append(hint)
        if len(hints) >= bounded:
            break
    hints.sort(key=lambda item: (-item.score_bonus, item.activity_name))
    return InitiativeHintBook(hints=tuple(hints), dropped=tuple(dropped), limit=bounded)


def hint_sources_payload(book: InitiativeHintBook) -> dict[str, Any]:
    """给计划 ``constraints`` 的**审计**段落（§十二：只读展示用，不参与内容哈希）。"""
    return {"initiative": book.payload()}
