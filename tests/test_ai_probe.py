"""探针预算与「截断但连通」判定（2026-10-05 WARN 复盘）。

后台持续出现 ``Empty content from model=cn:deepseek-v4.1-flash finish=length``，
两个根因：

1. 探测请求只给 8 tokens：推理模型把 completion 全花在 reasoning 上，正文必空。
   端点其实是活的（上游 200 + completion tokens），旧实现把它报成失败，
   模型页 / 凭据页会误报；
2. 内部决策（沙箱裁决 / 意图 / 对话 / 记忆整理）的 completion 预算过紧：
   推理吃掉预算 → 空正文 → EmptyResponseError → 路由器白白级联降级，
   日志里全是 WARN。

本文件锁住修复后的契约：探针预算 256、finish=length 的空正文 = 「连通正常（degraded）」；
决策类调用预算充裕，且沙箱裁决会钉到配置指定的轻模型。
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.engine import AIEngine
from app.ai.errors import AIError, AllModelsFailedError, EmptyResponseError
from app.ai.models import AIRequest
from app.ai.probe import (
    PROBE_MAX_TOKENS,
    PROBE_PROMPT,
    probe_request,
    truncated_by_budget,
)
from app.config.settings import AIConfig
from app.sandbox.ai import SandboxAIDecider
from tests.ai_mocks import MockAIProvider


def make_engine(
    models: list[str],
    *,
    behaviors: dict[str, list[Any]] | None = None,
    max_tokens: int = 0,
) -> tuple[AIEngine, MockAIProvider]:
    provider = MockAIProvider(name="mock", behaviors=behaviors or {})
    config = AIConfig(
        enabled=True,
        max_tokens=max_tokens,
        models=[{"name": name, "provider": "mock", "model": name} for name in models],
    )
    return AIEngine(config, providers={"mock": provider}), provider


def decision_json(action: str) -> str:
    return json.dumps({"decision": "switch", "action": action, "reason_codes": ["hungry"]})


class TestProbeRequest:
    def test_shape_is_pinned_low_temperature_and_bounded(self) -> None:
        request = probe_request("cn:some-reasoner")
        assert isinstance(request, AIRequest)
        assert request.model == "cn:some-reasoner"
        assert request.temperature == 0.0
        assert request.max_tokens == PROBE_MAX_TOKENS == 256
        assert len(request.messages) == 1
        assert request.messages[0].content == PROBE_PROMPT

    def test_custom_prompt_and_blank_fallback(self) -> None:
        assert probe_request("m", prompt="你好").messages[0].content == "你好"
        # 空 prompt 不允许产生空 message（上游会 400）
        assert probe_request("m", prompt="").messages[0].content == PROBE_PROMPT


class TestTruncatedByBudget:
    def test_empty_response_with_length_is_connected(self) -> None:
        exc = EmptyResponseError("p", "m", finish_reason="length", reasoning_chars=900)
        assert truncated_by_budget(exc) is True

    def test_empty_response_with_stop_is_a_real_failure(self) -> None:
        # 模型自己选择不说 —— 不是预算问题，算异常（沿用旧语义）
        assert truncated_by_budget(EmptyResponseError("p", "m", finish_reason="stop")) is False
        assert truncated_by_budget(EmptyResponseError("p", "m")) is False

    def test_router_text_form(self) -> None:
        # 整条链都因预算截断失败时，终点是 AllModelsFailedError（无 finish_reason 属性）
        text = AllModelsFailedError(
            [
                "primary: Provider 'w' returned an empty response (model=x) finish=length",
                "secondary: Provider 'w' returned an empty response (model=y) finish=length",
            ]
        )
        assert truncated_by_budget(text) is True

    def test_other_errors_stay_failures(self) -> None:
        assert truncated_by_budget(AIError("boom")) is False
        assert truncated_by_budget(AllModelsFailedError(["primary: HTTP 500"])) is False
        assert truncated_by_budget(RuntimeError("finish=length is a coincidence here")) is True


class TestSandboxDeciderBudget:
    async def test_pinned_model_and_ample_budget(self) -> None:
        engine, provider = make_engine(
            ["primary", "small"], behaviors={"small": [decision_json("eat")]}
        )
        decider = SandboxAIDecider(engine, model="small")
        decider.set_character_context(name="角色", traits=["温和"])
        decision = await decider({"current": "发呆", "space": "客厅", "options": ["eat", "sleep"]})
        assert decision is not None and decision.action_id == "eat" and decision.via_ai is True
        call = provider.calls[0]
        assert call["model"] == "small"
        assert call["max_tokens"] == 1200

    async def test_no_pin_uses_router_default(self) -> None:
        engine, provider = make_engine(
            ["primary", "small"], behaviors={"primary": [decision_json("sleep")]}
        )
        decider = SandboxAIDecider(engine)
        decision = await decider({"space": "卧室", "options": ["eat", "sleep"]})
        assert decision is not None and decision.action_id == "sleep"
        assert provider.calls[0]["model"] == "primary"

    async def test_call_site_budget_wins_over_global_default(self) -> None:
        # ai.max_tokens=64 也不该把裁决预算压回去（_with_default_max_tokens 只在未指定时生效）
        engine, provider = make_engine(
            ["small"], behaviors={"small": [decision_json("eat")]}, max_tokens=64
        )
        decider = SandboxAIDecider(engine, model="small")
        await decider({"space": "客厅", "options": ["eat"]})
        assert provider.calls[0]["max_tokens"] == 1200

    async def test_bad_pin_degrades_to_deterministic_choice(self) -> None:
        # 钉了不存在的模型名：路由器直接拒绝，但裁决层必须吞掉、交回确定性选择
        engine, _provider = make_engine(["primary"])
        decider = SandboxAIDecider(engine, model="ghost")
        assert await decider({"space": "客厅", "options": ["eat"]}) is None

    async def test_budget_exhaustion_degrades_instead_of_raising(self) -> None:
        engine, _provider = make_engine(
            ["small"],
            behaviors={"small": [EmptyResponseError("mock", "small", finish_reason="length")]},
        )
        decider = SandboxAIDecider(engine, model="small")
        assert await decider({"space": "客厅", "options": ["eat"]}) is None

    async def test_disabled_engine_never_calls_the_model(self) -> None:
        engine, provider = make_engine(["small"])
        engine.enabled = False  # 运行时开关：关掉 AI 后决策层不再请求模型
        decider = SandboxAIDecider(engine, model="small")
        assert await decider({"space": "客厅", "options": ["eat"]}) is None
        assert provider.calls == []
