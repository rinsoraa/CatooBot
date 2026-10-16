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

#: Phase 7D §八场景 B：跟随意图（7C 留下的缺口 —— "跟着我"不是资源任务，但确实是
#: 一个需要授权的 Minecraft 操作）。目标**绝不**取自这句话本身，只来自可信身份桥。
FOLLOW_INTENT_KEYWORDS = (
    "跟着",
    "跟住",
    "跟随",
    "跟我",
    "跟过来",
    "来我身边",
)

#: 纯查询类请求：即使命中上面的词也**不**创建任务（§四十三）
_QUERY_PATTERNS = (
    re.compile(r"(在哪里|在哪儿|在哪|什么位置|坐标)"),
    re.compile(r"(有什么|有哪些|有没有|看见|看看|找得到|在哪找)"),
    re.compile(r"(背包里|身上有|手里拿|拿着什么)"),
)

# ---------------------------------------------------------- Phase 8D：木材采集意图

#: 每个任务最多采集的原木数量（任务书 §2.2 的硬上限；**不随请求扩大**）。
WOOD_MAX_TARGETS = 2

#: 支持矩阵：别名组 → 已验证支持的原木方块 ID。
#: 只有这里的 ID 会被规划；**其余一律明确拒绝**（绝不静默映射成橡木）。
WOOD_SPECIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("橡木", "橡树", "橡", "oak"), "minecraft:oak_log"),
    (("云杉", "杉树", "杉", "spruce"), "minecraft:spruce_log"),
    (("白桦", "桦木", "桦树", "birch"), "minecraft:birch_log"),
    (("丛林", "丛林木", "jungle"), "minecraft:jungle_log"),
    (("金合欢", "acacia"), "minecraft:acacia_log"),
    (("深色橡木", "暗橡", "dark_oak", "dark oak"), "minecraft:dark_oak_log"),
)

#: 明确不支持的木材相关词 → 稳定原因码（要么不是树干原木，要么是派生木制品）。
WOOD_UNSUPPORTED: tuple[tuple[tuple[str, ...], str], ...] = (
    (("竹子", "bamboo"), "bamboo"),
    (("菌柄", "绯红", "诡异", "下界木"), "nether_stem"),
    (("去皮", "stripped"), "stripped_wood"),
    (("木板", "plank"), "planks"),
    (("树叶", "叶子", "leaves"), "leaves"),
    (("木头方块", "wood block"), "wood_block"),
)

#: 木材采集动作词：只有「把木头弄到手」这一类才算（比 _ACTION_VERBS 更严）。
_WOOD_ACTIONS = ("砍", "砍伐", "采", "采集", "收集", "挖", "撸")

#: 数量单位 —— **只认原木根数**；"棵" 指树本身，不代表原木根数（见 parse_wood_request）。
_LOG_COUNT_UNITS = ("根", "个", "块", "条", "截")
_CHINESE_NUMERALS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


#: 别名按**长度倒序**展开（最长优先）。必须这样排序，否则「深色橡木」会同时命中
#: 「橡木」/「橡」而被误判成多树种歧义（Phase 8D Follow-up §A 修复的缺陷 1）。
_WOOD_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((alias.lower(), block) for aliases, block in WOOD_SPECIES for alias in aliases if alias),
        key=lambda item: (-len(item[0]), item[0]),
    )
)

#: 每个支持方块对应的**规范展示名**（别名组第一项），用于歧义详情。
_WOOD_SPECIES_NAME: dict[str, str] = {block: aliases[0] for aliases, block in WOOD_SPECIES}


def _match_wood_species(text: str) -> list[tuple[str, str]]:
    """最长优先 + **消费式**扫描：命中的别名会被跳过，越短的别名不会重复命中。

    这样「深色橡木」只会解析成深色橡木，「橡木和云杉」仍然报出两个树种（澄清）。
    """
    lowered = text.lower()
    found: list[str] = []
    cursor = 0
    while cursor < len(lowered):
        for alias, block in _WOOD_ALIASES:
            if lowered.startswith(alias, cursor):
                if block not in found:
                    found.append(block)
                cursor += len(alias)
                break
        else:
            cursor += 1
    return [(block, _WOOD_SPECIES_NAME.get(block, block)) for block in found]


@dataclass(frozen=True)
class WoodRequest:
    """一条**确定性**的木材采集请求（Phase 8D）。

    ``problem`` 非空 = 明确拒绝/需要澄清（此时 ``block``/``count`` 无意义）；
    否则这是一条可以进入既有有界规划链的请求。
    """

    candidates: tuple[str, ...] = ()
    species: str = ""
    count: int = 0
    requested: int = 0
    capped: bool = False
    problem: str = ""
    detail: str = ""


def parse_wood_request(text: str) -> WoodRequest | None:
    """把一句自然语言解析成木材采集请求（``None`` = 这句话不归木材管）。

    规则（确定性、可测试，不调用模型）：

    * 必须同时有**木材动作词**与**木材名词** —— 少了任何一半都不是采集请求（纯查询不建任务）；
    * 数量只认原木根数（"两根/1 个/三块"）；"一棵树"不指定根数 → 取默认 1 根；
    * 用户明确要求超过一任务上限时**不静默截断**：``capped=True`` 交给回复层如实说明；
    * 未指定树种 → ``candidates`` = 支持矩阵里的全部原木 ID（由观察决定真实目标，不猜树种）；
    * 多个不同树种同时出现 → ``problem=ambiguous_species``（澄清，不替用户选）；
    * 命中明确不支持的木材（竹子/菌柄/去皮/木板/树叶/木头方块）→ ``problem=unsupported_species``。
    """
    raw = str(text or "").strip()
    if not raw or len(raw) > 200:
        return None
    if not any(action in raw for action in _WOOD_ACTIONS):
        return None

    hits = _match_wood_species(raw)
    unsupported = ""
    for aliases, code in WOOD_UNSUPPORTED:
        if any(alias in raw.lower() for alias in aliases):
            unsupported = code
            break
    explicit = _parse_log_count(raw)
    requested = 1 if explicit is None else explicit
    if explicit == 0:
        # 「0 根」是明确的数量表达，但 0 不是合法请求 → 澄清；**绝不**静默当成 1 根。
        return WoodRequest(requested=0, problem="zero_count", detail="0")
    if unsupported:
        # 明确不支持的木材（竹子/菌柄/去皮/木板/树叶/木头方块）→ **先**如实拒绝：
        # 不能因为它没写"原木/树"就当成与木材无关的话放过（§5.1：要么验证，要么拒绝）。
        return WoodRequest(requested=requested, problem="unsupported_species", detail=unsupported)
    has_generic_wood = any(word in raw for word in ("原木", "木头", "树", "log", "wood"))
    if not hits and not has_generic_wood:
        return None  # 有动作词但没有木材名词：不是木材请求（交给既有路径）
    if len(hits) > 1:
        names = "、".join(name for _block, name in hits)
        return WoodRequest(requested=requested, problem="ambiguous_species", detail=names)

    candidates: tuple[str, ...]
    if hits:
        candidates = (hits[0][0],)
        species = hits[0][1]
    else:
        candidates = tuple(block for _aliases, block in WOOD_SPECIES)
        species = ""
    capped = requested > WOOD_MAX_TARGETS
    count = min(requested, WOOD_MAX_TARGETS)
    return WoodRequest(
        candidates=candidates,
        species=species,
        count=count,
        requested=requested,
        capped=capped,
    )


def _parse_log_count(text: str) -> int | None:
    """解析「要几根原木」：**完整**数字 / 中文数字 + 原木单位。

    * 必须解析完整整数 token（``\\d+``，不是 ``\\d{1,2}``）：否则「100 根」会被截成
      10、「102 根」会被截成 10，且不会触发超限说明（Phase 8D Follow-up §C 缺陷 3）。
    * 返回 ``None`` = 这句话没给数量（调用方取默认 1 根）；返回 ``0`` = 明确说了 0 根
      （调用方必须澄清，不得静默当成 1 根）。
    """
    units = "|".join(_LOG_COUNT_UNITS)
    match = re.search(rf"(\d+)\s*(?:{units})", text)
    if match:
        return int(match.group(1))
    match = re.search(rf"([零一二两三四五六七八九十])\s*(?:{units})", text)
    if match:
        return _CHINESE_NUMERALS.get(match.group(1), 0)
    return None


def explicit_log_count(text: str) -> int | None:
    """Phase 8I Follow-up 5：这句话**明确**写了要几根原木（``None`` = 没写数量）。

    必须与 ``parse_wood_request`` 的默认值区分开：用户没写数量时这里是 ``None``，
    调用方才能判断「同树种且未给出不同数量」，而不是把默认 1 根误当成明确的新数量。
    """
    return _parse_log_count(text)


#: 明确**请求**标记（命令式语气）。Phase 8D Follow-up §B：不能只认礼貌词，否则
#: 「去砍两根竹子带回来」这种命令句会绕过木材入口、静默落回旧单目标资源路径。
_WOOD_REQUEST_MARKERS = (
    "帮我",
    "帮忙",
    "请",
    "给我",
    "能不能",
    "可以",
    "去",
    "来",
    "带回来",
    "要",
    "想",
)

#: 叙述/回忆标记（过去时）：出现任意一个就不当作**新的采集指令**。
_WOOD_NARRATION_MARKERS = (
    "昨天",
    "前天",
    "上次",
    "上回",
    "刚才",
    "刚刚",
    "以前",
    "之前",
    "曾经",
    "已经",
    "那天",
)


#: Phase 8D Follow-up 2：否定/暂缓标记。多字标记本身无歧义，直接命中。
_WOOD_NEGATION_MARKERS = (
    "不要",
    "不用",
    "不必",
    "不想",
    "不打算",
    "不需要",
    "先别",
    "暂时别",
)

#: 单字否定「别」需要位置校验：避免把「特别/分别/个别/级别/类别/区别」里的字当否定。
_WOOD_SINGLE_NEGATIONS = ("别",)

#: 「别」前面允许出现的字（句首/主语/时间/标点）；其余一律不算否定。
_NEGATION_LEAD_OK = (
    "",
    "我",
    "你",
    "他",
    "她",
    "它",
    "咱",
    "先",
    "请",
    "那",
    "就",
    "，",
    "。",
    "！",
    "？",
    "、",
    "；",
    "：",
    " ",
)

#: 否定之后出现「改口/另选目标」→ 作用范围不明，必须澄清（保守 CLARIFY）。
_WOOD_REDIRECT_MARKERS = ("改", "换", "而是", "另", "重新")

#: 双重否定/纠正句 → 同样无法可靠判定，必须澄清。
_WOOD_DOUBLE_NEGATION_MARKERS = ("不是不", "并不是不", "不是不想", "不是不能")

#: 与木材相关的名词提示（否定裁定用；比树种表更宽，含裸「树/木」）。
_WOOD_NOUN_HINTS: tuple[str, ...] = (
    (
        "原木",
        "木头",
        "木块",
        "树干",
        "树",
        "木",
    )
    + tuple(alias for aliases, _code in WOOD_SPECIES for alias in aliases)
    + tuple(alias for aliases, _code in WOOD_UNSUPPORTED for alias in aliases)
)


class WoodNegation:
    """木材采集动作的否定裁定结果（§4.1：三种取值，字面量即可，不引入 NLP 框架）。"""

    NOT_NEGATED = "not_negated"
    NEGATED = "negated"
    AMBIGUOUS = "ambiguous"


def mentions_wood(text: str) -> bool:
    """这句话是否与木材有关（否定裁定的前置门槛，比树种表更宽）。"""
    raw = str(text or "")
    return any(hint in raw for hint in _WOOD_NOUN_HINTS)


def _negation_hits(text: str) -> list[tuple[int, str]]:
    """否定标记及其**合法位置**（「别」必须处于句首/主语/标点之后）。"""
    hits: list[tuple[int, str]] = []
    for marker in _WOOD_NEGATION_MARKERS:
        start = text.find(marker)
        while start >= 0:
            hits.append((start, marker))
            start = text.find(marker, start + 1)
    for marker in _WOOD_SINGLE_NEGATIONS:
        start = text.find(marker)
        while start >= 0:
            previous = text[start - 1] if start > 0 else ""
            if previous in _NEGATION_LEAD_OK:
                hits.append((start, marker))
            start = text.find(marker, start + 1)
    return sorted(hits)


def _wood_action_after(text: str, start: int, window: int = 8) -> int:
    """否定标记之后 ``window`` 字内最近的木材动作词位置（没有则 -1）。"""
    tail = text[start : start + window]
    found = [tail.find(action) for action in _WOOD_ACTIONS if action in tail]
    return -1 if not found else start + min(found)


def classify_wood_negation(text: str) -> str:
    """裁定一条消息对木材采集动作的否定语义（确定性、纯函数、无副作用）。

    只考虑**否定词与采集动作的局部关系**（不能 ``'不' in text`` 一刀切，也不能让
    「要/想/去/带回来」这类肯定标记覆盖前面的否定）：

    * 双重否定/纠正句 → ``AMBIGUOUS``；
    * 否定标记之后近处有采集动作 → ``NEGATED``；
    * 否定之后又出现改口/另选目标、或**另一个**采集动作 → ``AMBIGUOUS``；
    * 其余 → ``NOT_NEGATED``（交回既有肯定请求判断）。
    """
    raw = str(text or "").strip()
    if not raw:
        return WoodNegation.NOT_NEGATED
    if any(marker in raw for marker in _WOOD_DOUBLE_NEGATION_MARKERS):
        return WoodNegation.AMBIGUOUS
    if not mentions_wood(raw):
        return WoodNegation.NOT_NEGATED
    for start, _marker in _negation_hits(raw):
        action_at = _wood_action_after(raw, start)
        if action_at < 0:
            continue
        after = raw[action_at:]
        if any(marker in after for marker in _WOOD_REDIRECT_MARKERS):
            return WoodNegation.AMBIGUOUS
        rest = raw[action_at + 1 :]
        if any(action in rest for action in _WOOD_ACTIONS):
            return WoodNegation.AMBIGUOUS
        return WoodNegation.NEGATED
    return WoodNegation.NOT_NEGATED


def is_wood_request(text: str) -> bool:
    """区分「明确的采集指令」与「叙述/回忆」（确定性，不调用模型）。

    只在 :func:`parse_wood_request` 已判定这句话**与木材有关**之后使用：

    * 有叙述/过去时标记 → 不是新指令（"我昨天砍了两根竹子" 保持普通叙述语义）；
    * 有请求标记 → 是指令；
    * 两者都没有 → 只有**命令式语序**才算（动作词在句首附近、后面不接 "了/过"）。
    """
    raw = str(text or "").strip()
    if not raw:
        return False
    if any(marker in raw for marker in _WOOD_NARRATION_MARKERS):
        return False
    if any(marker in raw for marker in _WOOD_REQUEST_MARKERS):
        return True
    index = min((raw.find(action) for action in _WOOD_ACTIONS if action in raw), default=-1)
    if index < 0:
        return False
    if len(raw[:index]) > 2:
        return False
    return raw[index + 1 : index + 2] not in ("了", "过")


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

    def detect_follow(self, text: str) -> bool:
        """这句话是不是在要她**跟随某个玩家**（Phase 7D §八场景 B）。

        只认意图、不认目标 —— 目标只能来自可信身份桥（谁说的话就解析谁，
        绝不从文本里抠玩家名，那等于按昵称猜人）。
        """
        message = str(text or "").strip()
        if not message or len(message) > 60:
            return False
        return any(keyword in message for keyword in FOLLOW_INTENT_KEYWORDS)

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
