"""Phase 6D 测试夹具：脚本化模型 provider + 带顾问的运行时装配。

刻意**不**联网：所有"模型输出"都是脚本化的确定字符串（§六十九：CI 不依赖真实 LLM）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.activity import ActivityPlanner
from app.activity.model_advisor import ActivityModelAdvisor
from tests.activity_plan_fakes import PlanRig, State  # noqa: F401  (复用时钟/状态替身)


@dataclass
class ScriptedProvider:
    """按脚本依次返回"模型输出"（字符串原样返回，好测各种畸形输出）。"""

    scripted: list[str] = field(default_factory=list)
    calls: int = 0
    last_input: dict[str, Any] = field(default_factory=dict)
    last_system: str = ""

    async def complete_json(
        self, *, schema: dict[str, Any], system: str, input: dict[str, Any], timeout_ms: int
    ) -> str:
        self.last_input = dict(input)
        self.last_system = str(system)
        index = min(self.calls, len(self.scripted) - 1) if self.scripted else 0
        self.calls += 1
        if not self.scripted:
            return "{}"
        return self.scripted[index]


@dataclass
class RaisingProvider:
    """总是抛异常的 provider（测 timeout / provider error 的翻译与回退）。"""

    error: Exception = field(default_factory=lambda: TimeoutError("deadline exceeded"))
    calls: int = 0

    async def complete_json(
        self, *, schema: dict[str, Any], system: str, input: dict[str, Any], timeout_ms: int
    ) -> str:
        self.calls += 1
        raise self.error


def proposal_json(
    decision: str,
    *,
    extension_minutes: int | None = None,
    next_hint: str | None = None,
    reason_code: str = "high_focus",
    explanation: str = "她在兴头上，再来一会儿更自然",
) -> str:
    return json.dumps(
        {
            "decision": decision,
            "extension_minutes": extension_minutes,
            "next_hint": next_hint,
            "reason_code": reason_code,
            "state_explanation": explanation,
        },
        ensure_ascii=False,
    )


def advisor_with(provider: Any, **kwargs: Any) -> ActivityModelAdvisor:
    return ActivityModelAdvisor(
        provider=provider,
        model=kwargs.pop("model", "test-model"),
        provider_name=kwargs.pop("provider_name", "scripted"),
        **kwargs,
    )


def rig_with_advisor(
    provider: Any, *, hour: int = 14, minute: int = 0, **advisor_kwargs: Any
) -> PlanRig:
    """一套真部件 + 装了脚本化顾问的决策引擎。"""
    from tests.activity_plan_fakes import clock_at

    advisor = advisor_with(provider, **advisor_kwargs)
    rig = PlanRig(clock=clock_at(hour, minute), planner=ActivityPlanner())
    rig.runtime.engine.advisor = advisor  # type: ignore[union-attr]
    return rig
