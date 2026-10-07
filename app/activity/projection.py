"""Phase 6A §十四/§四十/§四十一：Episode → CharacterState 的**唯一**投影。

§十四 的硬规则：``CharacterState.activity`` 必须**由当前 Episode 派生**，
不允许任何人绕过 Episode 直接写它（否则就会出现"Episode 说 A、角色状态说 B"的分裂）。

所以这个模块是**唯一**写 ``CharacterState.activity*`` 的地方：

```
ActivityEpisode → ActivityProjection → CharacterState.activity / activity_status / ...
```

生成给模型的上下文时也守预算（§四十）：当前 Episode **1 条**、最近转移 **≤3 条**，
绝不把整段活动历史塞进 prompt；优先级见 §四十一（当前世界 > 当前任务 > 当前 Episode >
最近活动 > 相关记忆 > 历史记忆 —— 那只是**上下文**优先级，**不是**授权优先级，§五十）。
"""

from __future__ import annotations

from typing import Any

from app.activity.model import ActivityEpisode, activity_label

#: 最近转移最多给模型看几条（§四十）
RECENT_TRANSITION_BUDGET = 3


class ActivityProjection:
    """把 Episode 投射到 CharacterState（失败只降级，绝不打断生命周期）。"""

    def __init__(self, states: Any, *, logger: Any = None) -> None:
        self._states = states
        self._log = logger
        #: 最近一次投影失败的原因（WebUI/日志如实显示"投影降级"）
        self.degraded_reason = ""

    async def apply(self, episode: ActivityEpisode | None, *, reason: str = "") -> None:
        """把当前 Episode（或"没有 Episode"）写进 CharacterState。

        ``episode is None`` 是**合法**状态（§五十二：旧数据没有 Episode 就不该有），
        此时 activity 系列字段清空 —— 但不编一个假的 activity 出来。
        """
        if self._states is None:
            return
        fields: dict[str, Any] = {
            "activity": "",
            "current_activity_episode_id": "",
            "activity_started_at": 0,
            "activity_planned_end_at": 0,
            "activity_status": "",
            "activity_source": "",
        }
        if episode is not None:
            fields.update(
                {
                    "activity": episode.activity_name,
                    "activity_since": int(episode.started_at or 0),
                    "current_activity_episode_id": episode.episode_id,
                    "activity_started_at": int(episode.started_at or 0),
                    "activity_planned_end_at": int(episode.planned_end_at or 0),
                    "activity_status": episode.status.value,
                    "activity_source": episode.source.value,
                }
            )
        try:
            await self._states.update(reason=str(reason or "activity_episode"), **fields)
            self.degraded_reason = ""
        except Exception as exc:  # noqa: BLE001 - 投影失败只降级（§五十一）
            self.degraded_reason = f"{type(exc).__name__}"
            if self._log is not None:
                self._log.warning("[World.Activity] 状态投影失败（降级）：%s", exc)


def activity_context_block(
    episode: ActivityEpisode | None,
    transitions: list[dict[str, Any]] | None = None,
    *,
    related_task: str = "",
) -> str:
    """给一次 LLM turn 的"她现在在做什么"（≤1 条 + 最近 ≤3 条转移）。

    没有 Episode 就返回空串 —— **绝不**用记忆/猜测补一个出来（§二十八/§四十八）。
    """
    if episode is None:
        return ""
    lines = [
        f"现在的活动：{activity_label(episode.activity_name)}"
        f"（{episode.activity_name}，{episode.status.value}，来源 {episode.source.value}"
        f"，计划 {_clock_text(episode.planned_end_at)} 结束）"
    ]
    if related_task:
        lines.append(f"对应的任务：{related_task}")
    rows = list(transitions or [])[-RECENT_TRANSITION_BUDGET:]
    if rows:
        described = "、".join(
            f"{row.get('transition', '?')}（{row.get('reason', '?')}）" for row in rows
        )
        lines.append(f"最近的活动变化：{described}")
    return "\n".join(lines)


def _clock_text(timestamp: float) -> str:
    if not timestamp:
        return "未定"
    import datetime as _dt

    return _dt.datetime.fromtimestamp(float(timestamp)).strftime("%H:%M")
