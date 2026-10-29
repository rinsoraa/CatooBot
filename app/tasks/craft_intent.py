"""Phase 8F §3.1/§4.2：确定性合成请求解析（**不调用模型**）。

设计口径与 Phase 8D 木材解析一致：

* 只有同时出现「合成类动词」与「可识别的目标物品」才算合成请求 —— 少了任何一半
  都不是合成请求（「砍点木板」是木材动作，交回木材入口；纯查询/闲聊不建任务）；
* 数量缺省**不猜**：``count == 0`` = 用户没说数量 → 由计划层按**一次真实合成**的
  产出量定义默认值，并在回话里如实说明用了什么默认；
* 只说「木板」而不说树种 → 歧义澄清（绝不替用户挑树种）；
* 命中否定语气（「别做木棍了」）→ 明确不做，**绝不**升级成行动；
* 本模块只解析：不查配方、不读背包、不产生任何世界动作（真实配方与背包由计划层读）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.tasks.models import canonical_item_name

#: 单次任务最多几个合成批次（与 ``validation.MAX_ACTION_STEPS`` 相容：一个批次一步）。
CRAFT_MAX_BATCHES = 4

#: 合成类动词（必须出现其中之一，且必须搭配可识别目标物品）。
CRAFT_VERBS: tuple[str, ...] = (
    "合成",
    "做成",
    "制成",
    "制作",
    "拼成",
    "做",
    "造",
    "craft",
    "make",
)

#: 明确的否定前缀（出现在动词前 4 个字内即视为否定）。
_NEGATION_PREFIXES: tuple[str, ...] = ("别", "不要", "不用", "不需要", "甭", "勿", "先别")

#: 整句否定（出现在任何位置都算）。
_NEGATION_PHRASES: tuple[str, ...] = ("算了别做", "不用做了", "不做了", "别做了", "先不做")

#: 数量单位（与木材的「根」并存；合成结果常按「个/支/组」说）。
CRAFT_COUNT_UNITS: tuple[str, ...] = ("个", "根", "块", "条", "支", "把", "组", "份")

_CHINESE_NUMERALS: dict[str, int] = {
    "零": 0,
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

#: 木材**动作**词（与合成动词互斥：有这些词的句子归木材入口，不归这里）。
_WOOD_ACTIONS: tuple[str, ...] = ("砍", "砍伐", "采", "采集", "收集", "挖", "撸", "伐")

#: 支持的**目标物品**（别名 → 裸物品名 → 用户语言名）。
#: 木板按树种分开：只说「木板」是歧义，必须澄清。
CRAFT_TARGETS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("木棍", "棍子", "stick"), "stick", "木棍"),
    (("橡木木板", "橡木板", "oak_planks"), "oak_planks", "橡木木板"),
    (("云杉木板", "云杉板", "spruce_planks"), "spruce_planks", "云杉木板"),
    (("白桦木板", "白桦板", "birch_planks"), "birch_planks", "白桦木板"),
    (("丛林木板", "丛林板", "jungle_planks"), "jungle_planks", "丛林木板"),
    (("金合欢木板", "相思木板", "acacia_planks"), "acacia_planks", "金合欢木板"),
    (("深色橡木木板", "深色橡木板", "dark_oak_planks"), "dark_oak_planks", "深色橡木木板"),
    # Phase 8G：基础 3×3 / 工作台配方的**路由**识别（能不能做仍由真实配方 + 真实背包决定）。
    (("工作台", "crafting_table", "crafting table"), "crafting_table", "工作台"),
    (("木镐", "wooden_pickaxe"), "wooden_pickaxe", "木镐"),
    (("木斧", "wooden_axe"), "wooden_axe", "木斧"),
    (("木锹", "木铲", "wooden_shovel"), "wooden_shovel", "木锹"),
    (("木锄", "wooden_hoe"), "wooden_hoe", "木锄"),
    (("木剑", "wooden_sword"), "wooden_sword", "木剑"),
    (("箱子", "chest"), "chest", "箱子"),
    (("梯子", "ladder"), "ladder", "梯子"),
    (("木板", "planks"), "", "木板"),
)

#: **工位**（不是材料）：出现这些词只表示「在哪儿做」，绝不当作材料（Phase 8G）。
_STATION_ITEMS: frozenset[str] = frozenset({"crafting_table", "furnace", "anvil"})


#: 材料别名（**只用于审计与回话**：真实材料由服务器配方 + 真实背包决定）。
CRAFT_MATERIALS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("橡木原木", "oak_log"), "oak_log", "橡木原木"),
    (("云杉原木", "spruce_log"), "spruce_log", "云杉原木"),
    (("白桦原木", "birch_log"), "birch_log", "白桦原木"),
    (("丛林原木", "jungle_log"), "jungle_log", "丛林原木"),
    (("金合欢原木", "acacia_log"), "acacia_log", "金合欢原木"),
    (("深色橡木原木", "dark_oak_log"), "dark_oak_log", "深色橡木原木"),
    (("原木", "木头", "木料", "log", "wood"), "", "原木"),
)

#: 出现这些词但没有可识别目标物品时**仍然**算「合成意图」（用于给未知物品澄清）。
_ITEM_ISH: tuple[str, ...] = ("木板", "木棍", "planks", "stick", "东西", "物品")


@dataclass(frozen=True)
class CraftRequest:
    """一条**确定性**的合成请求（Phase 8F）。

    ``problem`` 非空 = 明确需要澄清或安全拒绝（此时 ``item``/``count`` 无意义）；
    否则这是一条可以进入既有有界规划链的请求。
    """

    item: str = ""
    display: str = ""
    count: int = 0
    requested: int = 0
    material: str = ""
    problem: str = ""
    detail: str = ""


def _has_craft_verb(text: str) -> bool:
    return any(verb in text for verb in CRAFT_VERBS)


def _has_wood_action(text: str) -> bool:
    return any(action in text for action in _WOOD_ACTIONS)


def _is_negated(text: str) -> bool:
    """否定语气：整句否定，或否定词紧贴在合成动词之前。"""
    if any(phrase in text for phrase in _NEGATION_PHRASES):
        return True
    for verb in CRAFT_VERBS:
        for match in re.finditer(re.escape(verb), text):
            window = text[max(0, match.start() - 4) : match.start()]
            if any(prefix in window for prefix in _NEGATION_PREFIXES):
                return True
    return False


def parse_count(text: str, *, units: tuple[str, ...] = CRAFT_COUNT_UNITS) -> int | None:
    """解析「要几个」：完整数字或中文数字 + 单位。

    返回 ``None`` = 没说数量；``0`` = 明确说了 0（调用方必须澄清，不得当成默认值）。
    完整 ``\\d+`` 而不是 ``\\d{1,2}``：避免「100 个」被截成 10（Phase 8D 的既有教训）。
    """
    unit = "|".join(units)
    match = re.search(rf"(\d+)\s*(?:{unit})", text)
    if match:
        return int(match.group(1))
    match = re.search(rf"([零一二两三四五六七八九十])\s*(?:{unit})", text)
    if match:
        return _CHINESE_NUMERALS.get(match.group(1), 0)
    return None


def _match_target_spans(text: str) -> list[tuple[int, int, str, str]]:
    """匹配物品名的**出现位置**：同一起点上长别名遮蔽短别名。

    返回 ``[(start, end, 裸物品名, 用户语言名), ...]``（按出现顺序）；泛称命中时裸物品名为空串。
    """
    lowered = str(text or "").lower()
    spans: list[tuple[int, int, str, str]] = []
    for aliases, item, display in CRAFT_TARGETS:
        for alias in aliases:
            start = lowered.find(alias.lower())
            if start >= 0:
                spans.append((start, start + len(alias), item, display))
                break
    spans.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    kept: list[tuple[int, int, str, str]] = []
    for start, end, item, display in spans:
        if any(
            not (end <= other_start or start >= other_end)
            for other_start, other_end, _i, _d in kept
        ):
            continue
        kept.append((start, end, item, display))
    return kept


def _split_by_verb(
    text: str, spans: list[tuple[int, int, str, str]]
) -> tuple[list[tuple[int, int, str, str]], list[tuple[int, int, str, str]]]:
    """按合成动词把候选分成 ``(动词之后的候选, 动词之前的候选)``。

    「用橡木木板做一根木棍」里，动词**之前**的橡木木板是**材料**，动词之后的木棍才是目标 ——
    只看"提到了哪些物品"会把材料误当成目标（真实门禁发现的缺陷）。没有动词时全算「之后」。
    """
    positions = _verb_positions(text)
    if not positions:
        return list(spans), []
    verb_at = max(positions)
    return [span for span in spans if span[0] >= verb_at], [
        span for span in spans if span[0] < verb_at
    ]


def _verb_positions(text: str) -> list[int]:
    """所有合成动词的出现位置（取**最后**一个作为分界：把「做成」这类复合动词也算上）。"""
    lowered = str(text or "").lower()
    return [
        match.start() for verb in CRAFT_VERBS for match in re.finditer(re.escape(verb), lowered)
    ]


def _match_material(text: str) -> tuple[str, str]:
    lowered = text.lower()
    for aliases, item, display in CRAFT_MATERIALS:
        for alias in aliases:
            if alias.lower() in lowered:
                return item, display
    return "", ""


def parse_craft_request(text: str) -> CraftRequest | None:
    """把一句自然语言解析成合成请求（``None`` = 这句话不归合成入口管）。

    规则（确定性、可测试、不调用模型）：

    1. 必须有合成动词；只有木材动作词（砍/采/收集…）的句子**不归这里**；
    2. 必须有可识别目标物品；有合成动词但目标不可识别 → ``problem=unknown_item``；
    3. 多目标（「木板和木棍」）→ ``problem=ambiguous_item``（一次只冻结一个目标）；
    4. 只说「木板」没说树种 → ``problem=ambiguous_item``（澄清，不替用户选）；
    5. 否定语气 → ``problem=negated``（明确不做）；
    6. 明确说 0 个 → ``problem=zero_count``。
    """
    raw = str(text or "").strip()
    if not raw or len(raw) > 200:
        return None
    if not _has_craft_verb(raw):
        return None
    material_item, material_display = _match_material(raw)
    spans = _match_target_spans(raw)
    if not spans and not any(word in raw.lower() for word in _ITEM_ISH):
        # 有合成动词但没有点名任何东西（「帮我做个房子」）：不归合成入口，
        # 交给既有决策/计划层（它们会走 unsupported / needs-design 路径）。
        return None
    if _is_negated(raw):
        return CraftRequest(problem="negated", detail=raw[:60])
    if _has_wood_action(raw) and not spans:
        # 「砍点木板」：木材动作 + 物品词，但不是合成请求 → 交回木材入口。
        return None
    if not spans:
        return CraftRequest(problem="unknown_item", detail=raw[:60])
    after, before = _split_by_verb(raw, spans)
    candidates = after or before
    distinct = {(item, display) for _start, _end, item, display in candidates}
    if len(distinct) > 1:
        names = "、".join(sorted(display for _item, display in distinct))
        return CraftRequest(problem="ambiguous_item", detail=names)
    item, display = candidates[0][2], candidates[0][3]
    if not item:
        # 泛称「木板」：树种歧义，必须澄清（不替用户挑树种）。
        return CraftRequest(problem="ambiguous_item", detail=display)
    if not material_item and before and before[0][2] not in _STATION_ITEMS:
        # 动词之前点名的物品 = 用户说的材料（真实材料仍由服务器配方与真实背包决定）。
        material_item, material_display = before[0][2], before[0][3]
    explicit = parse_count(raw)
    if explicit == 0:
        return CraftRequest(item=item, display=display, requested=0, problem="zero_count")
    count = 0 if explicit is None else int(explicit)
    return CraftRequest(
        item=item,
        display=display,
        count=count,
        requested=count,
        material=material_item,
        detail=material_display,
    )


def display_name_of(item: str) -> str:
    """裸物品名 → 用户语言名（拿不到映射就如实原样返回，**绝不编造**）。"""
    target = canonical_item_name(item)
    for aliases, name, display in CRAFT_TARGETS:
        if name and name == target:
            return display
        if any(alias.lower() == target for alias in aliases):
            return display
    for aliases, name, display in CRAFT_MATERIALS:
        if name and name == target:
            return display
        if any(alias.lower() == target for alias in aliases):
            return display
    return target


def mentions_craft(text: str) -> bool:
    """这句话是否**可能**与合成有关（只用于决定要不要进合成路由）。"""
    raw = str(text or "")
    return _has_craft_verb(raw) and any(
        alias.lower() in raw.lower()
        for aliases, _item, _display in CRAFT_TARGETS
        for alias in aliases
    )


def is_craft_request(text: str) -> bool:
    """这句话是不是可进入合成计划链的肯定请求（解析后无 problem 且有目标）。"""
    request = parse_craft_request(text)
    return bool(request is not None and not request.problem and request.item)
