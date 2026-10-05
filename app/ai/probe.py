"""连通性探针（WebUI 模型测试 / 凭据检查 / provider 快检）。

推理模型会先花 completion 预算思考：探测请求若只给 8 tokens，`content` 必然为空
（finish=length）。端点其实是**活的**（上游回 200 且有 completion tokens），旧实现
把它当失败——模型页/凭据页会误报（2026-10-05 WARN 复盘）。这里统一三件事：

* 探测预算 256：够非推理模型回一句 "pong"，也不为探测浪费大预算；
* 「被预算截断的空正文」（finish=length）→ 视为**连通正常（degraded）**，如实说明；
* 其它异常照旧失败（finish=stop 的空正文仍是异常——模型自己选择不说，属于真异常）。
"""

from __future__ import annotations

from app.ai.errors import EmptyResponseError
from app.ai.models import AIRequest, ChatMessage

#: 探测请求的 completion 预算（不是模型能力上限，只影响这次探针）
PROBE_MAX_TOKENS = 256
PROBE_PROMPT = "ping"

PROBE_DEGRADED_NOTE = (
    f"连通正常；本次探测的 {PROBE_MAX_TOKENS}-token 预算被推理过程占满（未产出正文）。"
    "真实调用使用各自的完整预算，不受影响。"
)


def probe_request(model: str, *, prompt: str = PROBE_PROMPT) -> AIRequest:
    """统一探针请求：模型钉死、温度 0、预算 256。"""
    return AIRequest(
        messages=[ChatMessage.user(prompt or PROBE_PROMPT)],
        model=model,
        temperature=0.0,
        max_tokens=PROBE_MAX_TOKENS,
    )


def truncated_by_budget(exc: BaseException) -> bool:
    """True = 上游已回应，只是预算被推理占满（finish=length）——连通性正常。

    两种形状：单个模型直接抛 ``EmptyResponseError``（钉住模型的直连探测），
    或整条链都因同样原因失败后的 ``AllModelsFailedError``（其文本里带
    ``finish=length``——路由器的错误文本是本项目自己拼的，读回它是既有惯例）。
    """
    if isinstance(exc, EmptyResponseError):
        return getattr(exc, "finish_reason", "") == "length"
    return "finish=length" in str(exc)
