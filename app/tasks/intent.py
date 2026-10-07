"""什么时候该开一个 Task（Phase 5A §四十二/§四十三/§六十/§九十九）。

**普通对话仍然是普通对话** —— 只有「跨多个行动步骤、需要异步等待与状态管理」的长期意图
才创建 Task（§九十九）。这里是唯一允许做这个判断的地方，规则必须**确定性、可审计、可测试**：

* 明确的多步骤请求（"去…挖…并捡回来"）→ 创建；
* **普通对话仍然是普通对话** —— 只有「跨多个行动步骤、需要异步等待与状态管理」的
* 识别不出多步骤 → 不创建（宁可不开 Task，也不要把它变成一个笨重的工作流引擎）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 明确要求"去做一件事"的动词（中文为主，附带常见英文）
_ACTION_VERBS = (
    "去",
    "挖",
    "砍",
    "采",
    "采集",
    "捡",
    "拾取",
    "拿",
    "收集",
    "走到",
    "过去",
    # Phase 5B：QQ 上"帮我找附近的橡木"这种请求里动词就是"找"
    # （纯查询仍会先在 _QUERY_PATTERNS 被挡掉，不会变成任务）
    "找",
)

#: 表示"多个阶段"的连接词/收尾要求
_MULTI_STEP_MARKERS = (
    "并",
    "然后",
    "再",
    "接着",
    "顺便",
    "回来",
    "带回来",
    "拿回来",
    "捡起来",
    "挖下来",
    "挖一块",
    "挖一个",
    # Phase 5B：QQ 上常见的说法（"帮我把那边的木头挖掉"）
    "挖掉",
    "砍掉",
    "捡回来",
    "采回来",
    "取回来",
    "弄回来",
    # Phase 5B：QQ 上"帮我找附近的一块橡木"这类请求 —— "帮我"本身就是
    # "请你去动手"的请求语气（没有它就只是在聊天）
    "帮我",
    "帮忙",
    "get",
    "and then",
)

#: 明确的资源/方块词（有这些词才认为"目标是 Minecraft 世界里的东西"）
_RESOURCE_WORDS = (
    "原木",
    "木头",
    "木块",
    "橡木",
    "橡树",
    "树",
    "石头",
    "圆石",
    "铁矿",
    "煤",
    "钻石",
    "矿石",
    "方块",
    "木头",
    "木板",
    "log",
    "wood",
    "ore",
    "stone",
    "block",
)

#: 纯查询类请求：即使命中上面的词也**不**创建任务（§四十三）
_QUERY_PATTERNS = (
    re.compile(r"(在哪里|在哪儿|在哪|什么位置|坐标)"),
    re.compile(r"(有什么|有哪些|有没有|看见|看看|找得到|在哪找)"),
    re.compile(r"(背包里|身上有|手里拿|拿着什么)"),
)

#: Task 控制命令（§六十：复用用户回合识别，不新增工具）
CONFIRM_COMMANDS = ("确认", "好的", "可以", "开始吧", "执行", "同意", "yes", "ok")
PAUSE_COMMANDS = ("暂停", "先停一下", "停一下", "等一下")
RESUME_COMMANDS = ("继续", "接着做", "恢复", "resume")
CANCEL_COMMANDS = (
    "停止这个任务",
    "取消任务",
    "别做了",
    "不做了",
    "停止任务",
    # Phase 5B §九：QQ 上"停止"单独说也算（只在当前会话确实有任务时才生效）
    "停止",
    "停下",
    "不用做了",
    "cancel",
)

#: §二十七：任务状态查询（只有当前会话确实有任务时才会被当成查询）
_STATUS_PATTERNS = (
    re.compile(r"(怎么样|咋样|怎么样啦|如何了)"),
    re.compile(r"(到哪了|到哪儿了|做到哪|进行到|第几步)"),
    re.compile(r"(还在做|还在忙|在做了吗|做完了吗|好了没|好了吗|完成了吗)"),
    re.compile(r"(任务|进度|状态)(呢|啦|如何)?$"),
)


@dataclass
class TaskIntent:
    """识别结果：是否需要创建任务 + 命中了什么。"""

    is_task: bool
    reasons: tuple[str, ...] = ()
    objective: str = ""

    def to_payload(self) -> dict[str, object]:
        return {"is_task": self.is_task, "reasons": list(self.reasons), "objective": self.objective}


class TaskIntentDetector:
    """确定性的多步骤任务识别（规则写死在代码里，不调用模型——可测试、可审计）。"""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def detect(self, text: str) -> TaskIntent:
        message = str(text or "").strip()
        if not self.enabled or not message:
            return TaskIntent(False)
        if len(message) > 200:  # 长文多半是在聊天/解释，不是一条行动指令
            return TaskIntent(False)
        reasons: list[str] = []
        lowered = message.lower()
        if any(pattern.search(message) for pattern in _QUERY_PATTERNS):
            return TaskIntent(False, ("query_only",))
        verbs = [verb for verb in _ACTION_VERBS if verb in message]
        markers = [
            marker for marker in _MULTI_STEP_MARKERS if marker in message or marker in lowered
        ]
        resources = [word for word in _RESOURCE_WORDS if word in message or word in lowered]
        if verbs:
            reasons.append(f"verb:{verbs[0]}")
        if markers:
            reasons.append(f"multi:{markers[0]}")
        if resources:
            reasons.append(f"resource:{resources[0]}")
        # 必须同时有"动作"+"多阶段"+"具体资源"，才算一个需要编排的任务
        if len(verbs) >= 1 and markers and resources:
            return TaskIntent(True, tuple(reasons), message)
        return TaskIntent(False, tuple(reasons))

    def status_query(self, text: str) -> bool:
        """这句话是不是在问"任务怎么样了"（§二十七）。"""
        message = str(text or "").strip()
        if not message or len(message) > 60:
            return False
        return any(pattern.search(message) for pattern in _STATUS_PATTERNS)

    def control_command(self, text: str) -> str:
        """用户对当前任务的控制命令（§六十）：confirm / pause / resume / cancel / ''。"""
        message = str(text or "").strip()
        if not message:
            return ""
        for keyword in CANCEL_COMMANDS:
            if keyword in message:
                return "cancel"
        for keyword in PAUSE_COMMANDS:
            if keyword in message:
                return "pause"
        for keyword in RESUME_COMMANDS:
            if keyword in message:
                return "resume"
        for keyword in CONFIRM_COMMANDS:
            if message == keyword or message.startswith(keyword):
                return "confirm"
        return ""
