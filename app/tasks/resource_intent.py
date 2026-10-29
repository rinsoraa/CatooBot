"""R3 shared, deterministic collection intent. This module never observes or acts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.tasks.intent import (
    _GENERIC_WOOD_NOUNS,
    WOOD_SPECIES,
    WOOD_UNSUPPORTED,
    _negation_hits,
    is_wood_request,
)
from app.tasks.resource_contract import RESOURCE_SPECS

RESOURCE_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    *WOOD_SPECIES,
    (("圆石", "cobblestone"), "minecraft:cobblestone"),
    (("石头", "石块", "stone"), "minecraft:stone"),
    (("煤矿石", "煤矿", "煤炭", "煤", "coal ore", "coal_ore"), "minecraft:coal_ore"),
    (("铁矿石", "铁矿", "iron ore", "iron_ore"), "minecraft:iron_ore"),
    (("沙子", "沙块", "sand"), "minecraft:sand"),
    (("泥土", "土块", "土", "dirt"), "minecraft:dirt"),
)
_GENERIC_WOOD = _GENERIC_WOOD_NOUNS
_UNSUPPORTED = (
    *(alias for aliases, _reason in WOOD_UNSUPPORTED for alias in aliases),
    "樱花",
    "红树",
    "苍白橡木",
    "cherry",
    "mangrove",
    "pale_oak",
    "pale oak",
    "深板岩",
    "deepslate",
    "基岩",
    "bedrock",
    "钻石",
    "diamond",
    "金矿",
    "gold_ore",
    "铜矿",
    "copper_ore",
    "红石矿",
    "redstone_ore",
    "青金石",
    "lapis",
    "绿宝石",
    "emerald",
    "沙砾",
    "gravel",
    "红沙",
    "red_sand",
    "砂岩",
    "sandstone",
    "草方块",
    "grass_block",
    "水",
    "岩浆",
    "熔岩",
    "lava",
    "water",
    "黑曜石",
    "obsidian",
)
_ACTIONS = (
    "砍伐",
    "采集",
    "收集",
    "拾取",
    "挖",
    "砍",
    "采",
    "撸",
    "捡",
    "找",
    "拿",
    "mine",
    "collect",
    "gather",
    "get",
    "harvest",
    "chop",
)
_QUERIES = re.compile(
    r"只是问|只问|问问|如果|假如|假设|会先检查|需要哪些条件|怎么挖|如何挖|"
    r"在哪里|在哪儿|在哪|什么位置|坐标|有什么|有哪些|有没有|看见|看看|找得到|在哪找|"
    r"背包里|身上有|手里拿|拿着什么|how to|where is|where are"
)
_COUNT = re.compile(r"(?P<n>-?\d+|[零〇一二两三四五六七八九十百千万]+)\s*(?:根|个|块|条|截)")
_TREE_COUNT = re.compile(r"(?P<n>\d+|[零〇一二两三四五六七八九十百千万]+)\s*棵")
_FILLERS = (
    "minecraft",
    "罐头",
    "catoobot",
    "帮我",
    "帮忙",
    "麻烦",
    "能不能",
    "可以",
    "请你",
    "请",
    "给我",
    "我想要",
    "我想",
    "想要",
    "附近",
    "那边",
    "这里",
    "那里的",
    "的",
    "一",
    "点",
    "些",
    "几",
    "你",
    "我",
    "去",
    "来",
    "要",
    "想",
    "并",
    "然后",
    "接着",
    "再",
    "顺便",
    "带回来",
    "拿回来",
    "捡回来",
    "采回来",
    "取回来",
    "弄回来",
    "捡起来",
    "挖下来",
    "挖掉",
    "砍掉",
    "回来",
    "回去",
    "走到",
    "过去",
    "到",
    "把",
    "它",
    "一下",
    "吧",
    "了",
    "哦",
    "啊",
    "呀",
    "嘛",
    "呢",
    "先",
    "就",
    "and then",
    "and",
    "please",
    "bring back",
    "a",
    "one",
)
_IGNORED = frozenset("，,、；;。.！!？?：: \t\r\n～~")
_ALIASES = tuple(
    sorted(
        {
            (alias.lower(), block.removeprefix("minecraft:"))
            for aliases, block in RESOURCE_ALIASES
            for alias in aliases
        }
        | {(name, name) for name in RESOURCE_SPECS}
        | {("minecraft:" + name, name) for name in RESOURCE_SPECS},
        key=lambda row: (-len(row[0]), row[0]),
    )
)


@dataclass(frozen=True)
class CollectionRequest:
    candidates: tuple[str, ...] = ()
    count: int = 1
    requested: int = 1
    family: str = ""
    problem: str = ""
    detail: str = ""


def _number(token: str) -> int:
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    digits = {
        c: n
        for n, chars in enumerate(("零〇", "一", "二两", "三", "四", "五", "六", "七", "八", "九"))
        for c in chars
    }
    if all(char in digits for char in token):
        return int("".join(str(digits[char]) for char in token))
    total = current = 0
    for char in token:
        if char in digits:
            current = digits[char]
        else:
            unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}[char]
            total += (current or 1) * unit
            current = 0
    return total + current


def parse_collection_request(text: str) -> CollectionRequest | None:
    """Resolve the whole request, never pick a default species or silently cap a count.

    Unexplained words cannot amend an otherwise recognized collection target. In
    particular an unknown species before a generic log noun cannot become oak.
    """
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    if not any(action in raw for action in _ACTIONS) and not _QUERIES.search(raw):
        return None
    if not any(word in raw for word, _block in _ALIASES) and not any(
        word in raw for word in (*_GENERIC_WOOD, *_UNSUPPORTED)
    ):
        return None
    if len(raw) > 200:
        return CollectionRequest(problem="text_too_long")
    if _QUERIES.search(raw):
        return CollectionRequest(problem="query_only")
    if _negation_hits(raw):
        # Any unconsumed negation in a collection description needs clarification;
        # a model's positive label cannot select a different clause to execute.
        return CollectionRequest(problem="negated_or_ambiguous")
    # A deictic request to remove one specified block keeps the existing P3
    # atomic confirmation path. It makes no collection/inventory promise.
    if (
        "挖掉" in raw
        and any(word in raw for word in ("这个", "那个", "面前", "前面", "这块", "那块"))
        and not any(
            word in raw
            for word in ("捡", "拾取", "带回来", "收集", "采集", "采回来", "拿回来", "取回来")
        )
    ):
        if any(_number(match.group("n")) != 1 for match in _COUNT.finditer(raw)):
            return CollectionRequest(problem="quantity_out_of_budget")
        return None
    if not is_wood_request(raw) and not any(
        raw.startswith(action)
        for action in ("mine", "collect", "gather", "get", "harvest", "chop", "please")
    ):
        return CollectionRequest(problem="not_a_request")
    if any(word in raw for word in _UNSUPPORTED):
        return CollectionRequest(problem="unsupported_target")
    ids = re.findall(
        r"(?:minecraft:)?[a-z][a-z0-9_]*(?:_log|_wood|_ore|_stem)\b|minecraft:[a-z0-9_]+", raw
    )
    if any(name.removeprefix("minecraft:") not in RESOURCE_SPECS for name in ids):
        return CollectionRequest(problem="unsupported_target")

    targets: list[str] = []
    harvesting: list[str] = []
    generic = False
    unknown: list[str] = []
    cursor = 0
    vocabulary = sorted((*_GENERIC_WOOD, *_ACTIONS, *_FILLERS), key=lambda word: (-len(word), word))
    while cursor < len(raw):
        match = _COUNT.match(raw, cursor) or _TREE_COUNT.match(raw, cursor)
        if match:
            cursor = match.end()
            continue
        for alias, block in _ALIASES:
            if raw.startswith(alias, cursor):
                if block not in targets:
                    targets.append(block)
                cursor += len(alias)
                break
        else:
            for word in vocabulary:
                if raw.startswith(word, cursor):
                    generic = generic or word in _GENERIC_WOOD
                    if word in _ACTIONS and word not in ("找", "捡", "拾取", "拿"):
                        harvesting.append(word)
                    cursor += len(word)
                    break
            else:
                if raw[cursor] not in _IGNORED:
                    unknown.append(raw[cursor])
                cursor += 1
    if unknown:
        return CollectionRequest(problem="target_or_instruction_unresolved")
    descriptive_tree = harvesting == ["砍", "挖"] and any(
        _number(match.group("n")) == 1 for match in _TREE_COUNT.finditer(raw)
    )
    if len(harvesting) > 1 and not descriptive_tree:
        return CollectionRequest(problem="ambiguous_collection_sequence")
    if len(targets) > 1 or (
        generic and any(RESOURCE_SPECS[name].family != "log" for name in targets)
    ):
        return CollectionRequest(problem="ambiguous_target")
    candidates = (
        tuple(targets)
        if targets
        else tuple(
            name for name, spec in RESOURCE_SPECS.items() if generic and spec.family == "log"
        )
    )
    if not candidates:
        return CollectionRequest(problem="target_unresolved")
    counts = [_number(match.group("n")) for match in _COUNT.finditer(raw)]
    if any(_number(match.group("n")) != 1 for match in _TREE_COUNT.finditer(raw)):
        return CollectionRequest(problem="tree_group_unresolved")
    if len(counts) > 1:
        return CollectionRequest(problem="ambiguous_quantity")
    count = counts[0] if counts else 1
    family = RESOURCE_SPECS[candidates[0]].family
    if not 1 <= count <= RESOURCE_SPECS[candidates[0]].max_count:
        return CollectionRequest(
            candidates=candidates, requested=count, family=family, problem="quantity_out_of_budget"
        )
    return CollectionRequest(candidates=candidates, count=count, requested=count, family=family)


def collection_block_for(text: str) -> str:
    request = parse_collection_request(text)
    if request is None or request.problem or len(request.candidates) != 1:
        return ""
    return "minecraft:" + request.candidates[0]


def is_non_action_collection_turn(text: str) -> bool:
    request = parse_collection_request(text)
    if request is not None and request.problem == "query_only":
        # Ordinary factual queries may still use the audited SAFE tools.
        return bool(
            re.search(
                r"只是问|只问|问问|如果|假如|假设|会先检查|需要哪些条件|怎么挖|如何挖|how to",
                str(text or ""),
            )
        )
    return request is not None and bool(request.problem)


def explicit_collection_count(text: str) -> int | None:
    match = _COUNT.search(str(text or ""))
    return None if match is None else _number(match.group("n"))
