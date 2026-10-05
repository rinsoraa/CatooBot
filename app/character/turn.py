"""这一轮是谁发起的 —— 回合来源（Phase 3E.1）。

LOW Minecraft 动作只允许在**用户发起的回合**里执行，所以"回合来源"必须是一份
**结构化事实**，由调用方显式声明：

* 绝不从 ``user_text`` 的内容推断（禁止 ``if user_text == "（主动发起）"`` 这类判断）；
* 绝不提供一个"猜不出来就当用户"的默认值——不知道来源时按最保守的
  :attr:`TurnOrigin.BACKGROUND` 处理，宁可不让 LOW 动作执行；
* ToolContext 里只落一个**派生**布尔（``minecraft_explicit_intent``），
  策略层读它，读不到就是不允许。

四个来源（任务书 §三/§四）：

===============  =========================================================
USER             真实用户发来的消息（QQ / WebUI 里用户打的字）
INITIATIVE       她自己的主动发言（``compose_initiative``）
BACKGROUND       后台/自主生成（定时任务、行为预览、重放……没有人当场说话）
SYSTEM           系统内部生成（管理台干跑、诊断）
===============  =========================================================
"""

from __future__ import annotations

from enum import Enum


class TurnOrigin(str, Enum):  # noqa: UP042 - 与项目其它面向 pydantic/JSON 的枚举一致
    """回合来源（唯一事实来源；不可从消息内容推断）。"""

    #: 真实用户发来的消息（QQ / WebUI 用户输入 / Minecraft 玩家说的话）
    USER = "user"
    #: 她自己的主动发言（主动找一个理由开口）
    INITIATIVE = "initiative"
    #: 后台/自主生成（预览、重放、定时——没有人当场提出请求）
    BACKGROUND = "background"
    #: 系统内部生成（管理台干跑、诊断工具）
    SYSTEM = "system"

    @property
    def is_user(self) -> bool:
        """只有真正的用户回合才算"用户明确要求"（LOW 动作的唯一放行来源）。"""
        return self is TurnOrigin.USER

    def __str__(self) -> str:  # pragma: no cover - 调试友好
        return self.value
