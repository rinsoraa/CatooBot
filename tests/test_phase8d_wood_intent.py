"""Phase 8D：木材采集意图解析的确定性测试（不依赖模型、不用 sleep）。"""

from __future__ import annotations

import pytest

from app.tasks.intent import (
    WOOD_MAX_TARGETS,
    WoodNegation,
    WoodRequest,
    classify_wood_negation,
    is_wood_request,
    mentions_wood,
    parse_wood_request,
)

ALL_LOGS = (
    "minecraft:oak_log",
    "minecraft:spruce_log",
    "minecraft:birch_log",
    "minecraft:jungle_log",
    "minecraft:acacia_log",
    "minecraft:dark_oak_log",
)


def _ok(text: str) -> WoodRequest:
    request = parse_wood_request(text)
    assert request is not None, "应当被识别为木材请求：" + text
    assert request.problem == "", "不应被拒绝：" + text + " -> " + request.problem
    return request


@pytest.mark.parametrize(
    "text",
    [
        "帮我砍一棵树，把木头带回来。",
        "帮我收集两根附近的橡木原木，然后捡起来。",
        "帮我采两根云杉原木并捡回来。",
        "帮我砍一根橡木原木",
        "去砍两棵桦木，带回来",
    ],
)
def test_supported_phrases_are_requests(text: str) -> None:
    """任务书 §2.1 的三类语义示例必须都被识别（含树种与数量）。"""
    request = _ok(text)
    assert 1 <= request.count <= WOOD_MAX_TARGETS
    assert request.candidates


def test_species_is_mapped_to_a_supported_log_id() -> None:
    assert _ok("帮我收集两根附近的橡木原木，然后捡起来。").candidates == ("minecraft:oak_log",)
    assert _ok("帮我采两根云杉原木并捡回来。").candidates == ("minecraft:spruce_log",)
    assert _ok("去砍两棵桦木，带回来").candidates == ("minecraft:birch_log",)


def test_unspecified_species_observes_all_supported_logs() -> None:
    """未指定树种 → 由真实观察决定目标，不猜树种（任务书 §5.1）。"""
    request = _ok("帮我砍两棵原木带回来")
    assert request.species == ""
    assert request.candidates == ALL_LOGS


def test_counts_are_parsed_from_log_units_only() -> None:
    assert _ok("帮我砍一根橡木原木").count == 1
    assert _ok("帮我收集两块橡木原木然后捡起来").count == 2
    assert _ok("帮我砍3根橡木原木并捡回来").count == 2
    assert _ok("帮我砍一棵树，把木头带回来。").count == 1


def test_request_over_limit_is_flagged_not_silently_truncated() -> None:
    """任务书 §2.2/§5.3：超上限必须显式告知，不得静默截断后声称完成。"""
    request = _ok("帮我砍五根橡木原木带回来")
    assert request.requested == 5
    assert request.count == WOOD_MAX_TARGETS
    assert request.capped is True


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("帮我砍两根竹子带回来", "bamboo"),
        ("帮我挖三块去皮原木", "stripped_wood"),
        ("帮我收集两块木板", "planks"),
        ("帮我砍点树叶回来", "leaves"),
        ("帮我砍两根绯红菌柄", "nether_stem"),
    ],
)
def test_unsupported_wood_is_refused_explicitly(text: str, code: str) -> None:
    """未验证的树种/派生木制品必须明确拒绝，绝不静默映射成橡木（任务书 §5.1）。"""
    request = parse_wood_request(text)
    assert request is not None, "应当给出明确拒绝而不是无声忽略：" + text
    assert request.problem == "unsupported_species"
    assert request.detail == code


def test_mixed_species_is_ambiguous_not_guessed() -> None:
    request = parse_wood_request("帮我砍两根橡木和云杉原木带回来")
    assert request is not None
    assert request.problem == "ambiguous_species"
    assert "橡木" in request.detail
    assert "云杉" in request.detail


@pytest.mark.parametrize(
    "text",
    [
        "附近有什么木头？",
        "橡木原木在哪里？",
        "我背包里有木头吗？",
        "看看附近有没有树",
        "木头在这儿呢",
        "你好呀，今天天气不错",
        "帮我挖一块钻石",
        "",
        "砍" * 120,
    ],
)
def test_queries_and_chat_are_not_wood_requests(text: str) -> None:
    """纯查询/闲聊/非木材目标/超长文本都不归木材管（任务书 §5.1）。"""
    assert parse_wood_request(text) is None


# ============================================================ Phase 8D Follow-up 回归

DARK_OAK_ALIASES = ("深色橡木", "暗橡", "dark_oak", "dark oak")


@pytest.mark.parametrize("alias", DARK_OAK_ALIASES)
def test_dark_oak_aliases_never_collide_with_oak(alias: str) -> None:
    """§A 缺陷 1：深色橡木的专属别名绝不能被「橡木/橡」吃掉而误报歧义。"""
    spacer = " " if " " in alias else ""
    req = parse_wood_request(f"砍两根{spacer}{alias}{spacer}原木")
    assert req is not None
    assert req.problem == ""
    assert req.species == "深色橡木"
    assert req.candidates == ("minecraft:dark_oak_log",)
    assert req.count == 2


@pytest.mark.parametrize(
    ("alias", "block"),
    (
        ("橡木", "minecraft:oak_log"),
        ("云杉", "minecraft:spruce_log"),
        ("白桦", "minecraft:birch_log"),
        ("丛林", "minecraft:jungle_log"),
        ("金合欢", "minecraft:acacia_log"),
        ("深色橡木", "minecraft:dark_oak_log"),
    ),
)
def test_each_supported_species_resolves_to_its_own_log(alias: str, block: str) -> None:
    """§A：六种支持原木各自确定性解析，不串味、不猜。"""
    req = parse_wood_request(f"砍一根{alias}原木")
    assert req is not None and req.problem == ""
    assert req.candidates == (block,)


@pytest.mark.parametrize(
    ("text", "requested", "capped", "count"),
    (
        ("砍1根橡木原木", 1, False, 1),
        ("砍2根橡木原木", 2, False, 2),
        ("砍3根橡木原木", 3, True, 2),
        ("砍10根橡木原木", 10, True, 2),
        ("砍99根橡木原木", 99, True, 2),
        ("砍100根橡木原木", 100, True, 2),
        ("砍102根橡木原木", 102, True, 2),
    ),
)
def test_count_boundaries_are_parsed_as_whole_tokens(
    text: str, requested: int, capped: bool, count: int
) -> None:
    """§C 缺陷 3：三位数必须整体解析（100 不能变 10），超限必须打 capped 标记。"""
    req = parse_wood_request(text)
    assert req is not None and req.problem == ""
    assert req.requested == requested
    assert req.capped is capped
    assert req.count == count, "实际规划绝不超过 WOOD_MAX_TARGETS"
    assert req.count <= WOOD_MAX_TARGETS


@pytest.mark.parametrize("text", ("砍0根橡木原木", "砍零根橡木原木"))
def test_zero_count_is_clarified_not_silently_promoted(text: str) -> None:
    """§C：「0 根」必须澄清，绝不静默变成 1 根任务。"""
    req = parse_wood_request(text)
    assert req is not None
    assert req.problem == "zero_count"
    assert req.requested == 0
    assert req.count == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("砍两根原木", 2),
        ("砍两根橡木原木", 2),
        ("砍三个橡木原木", 3),
        ("砍十根橡木原木", 10),
        ("砍一条橡木原木", 1),
        ("砍两截橡木原木", 2),
        ("砍一块橡木原木", 1),
        ("砍橡木原木", 1),
    ),
)
def test_count_units_and_chinese_numerals(text: str, expected: int) -> None:
    """§C：单位与中文数字矩阵（说不清时默认 1 根，是明确的最小可验证默认）。"""
    req = parse_wood_request(text)
    assert req is not None and req.problem == ""
    assert req.requested == expected


@pytest.mark.parametrize(
    ("text", "is_request"),
    (
        ("去砍两根竹子带回来", True),
        ("帮我砍两根竹子带回来", True),
        ("砍两根竹子", True),
        ("我昨天砍了两根竹子", False),
        ("我刚刚砍了两根竹子", False),
        ("我上次砍了两根竹子", False),
        ("昨天砍了两根竹子", False),
    ),
)
def test_request_versus_narration_is_deterministic(text: str, is_request: bool) -> None:
    """§B：命令句（哪怕没有礼貌词）算指令；过去时叙述不算。"""
    assert is_wood_request(text) is is_request


# ============================================================ Phase 8D Follow-up 2：否定裁定

NEGATED_TEXTS = (
    "不要砍两根橡木原木带回来",
    "我不想砍两根橡木原木带回来",
    "别砍两根橡木原木带回来",
    "先别砍两根橡木原木带回来",
    "暂时别砍两根橡木原木带回来",
    "我不打算砍两根橡木原木带回来",
    "不用帮我砍两根橡木原木带回来",
    "我不需要砍两根橡木原木带回来",
    "不必砍橡木原木了",
    "不要砍两根竹子带回来",
    "不要砍树",
    "我不想砍木头，我只是问附近有什么树",
)

AMBIGUOUS_TEXTS = (
    "不要砍橡木，改砍云杉",
    "不要砍橡木原木，砍云杉原木",
    "不是不想砍两根橡木原木带回来",
    "并不是不想砍橡木",
)

POSITIVE_TEXTS = (
    "去砍两根橡木原木带回来",
    "帮我砍两根橡木原木带回来",
    "我想砍一根橡木",
    "砍0根橡木原木",
    "去砍两根竹子带回来",
    "帮我砍五根橡木原木带回来",
    "今天天气不错呀",
)

FALSE_POSITIVE_TEXTS = (
    "我特别想砍两根橡木原木带回来",
    "我分别砍了两根橡木和云杉原木",
    "这个级别可以砍橡木原木吗",
    "我觉得不错",
)


@pytest.mark.parametrize("text", NEGATED_TEXTS)
def test_explicit_negation_is_classified_as_negated(text: str) -> None:
    assert classify_wood_negation(text) is WoodNegation.NEGATED


@pytest.mark.parametrize("text", AMBIGUOUS_TEXTS)
def test_conflicting_or_double_negation_is_ambiguous(text: str) -> None:
    assert classify_wood_negation(text) is WoodNegation.AMBIGUOUS


@pytest.mark.parametrize("text", POSITIVE_TEXTS)
def test_positive_or_unrelated_lines_are_not_negated(text: str) -> None:
    assert classify_wood_negation(text) is WoodNegation.NOT_NEGATED


@pytest.mark.parametrize("text", FALSE_POSITIVE_TEXTS)
def test_negation_lookalikes_inside_words_are_not_negation(text: str) -> None:
    """「特别/分别/级别」里的「别」、以及没有木材名词的句子都不算否定。"""
    assert classify_wood_negation(text) is WoodNegation.NOT_NEGATED


def test_mentions_wood_is_a_wide_but_bounded_gate() -> None:
    for text in ("砍树", "橡木", "两根原木", "云杉原木", "砍竹子"):
        assert mentions_wood(text) is True
    for text in ("今天天气不错", "跟着我", "看看背包"):
        assert mentions_wood(text) is False
