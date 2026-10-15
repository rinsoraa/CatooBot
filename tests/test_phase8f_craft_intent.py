"""Phase 8F：确定性合成请求解析（§9.1 用例 1–3/12 的解析层；不调用模型）。"""

from __future__ import annotations

import pytest

from app.tasks.craft_intent import (
    display_name_of,
    is_craft_request,
    mentions_craft,
    parse_count,
    parse_craft_request,
)


@pytest.mark.parametrize(
    "text,item,count,material",
    [
        ("帮我用橡木原木做一根木棍", "stick", 1, "oak_log"),
        ("把橡木原木做成橡木木板", "oak_planks", 0, "oak_log"),
        ("做两把木棍", "stick", 2, ""),
        ("合成 8 个橡木木板", "oak_planks", 8, ""),
        ("帮我做点木棍", "stick", 0, ""),
        ("做 100 个橡木木板", "oak_planks", 100, ""),
        # 点名材料（材料名本身也是可合成物品）时，目标必须取**动词之后**那一个
        ("用橡木木板做一根木棍", "stick", 1, "oak_planks"),
    ],
)
def test_positive_requests_are_parsed_deterministically(
    text: str, item: str, count: int, material: str
) -> None:
    """明确请求：目标物品、数量、点名的材料全部来自确定性解析（缺数量 = 0，不猜）。"""
    request = parse_craft_request(text)
    assert request is not None and not request.problem, request
    assert request.item == item
    assert request.count == count
    assert request.material == material


def test_long_alias_shadows_the_generic_planks_alias() -> None:
    """「橡木木板」必须解析成橡木木板，而不是被泛称「木板」抢先判成歧义。"""
    request = parse_craft_request("把橡木原木做成橡木木板")
    assert request is not None and not request.problem
    assert request.item == "oak_planks"


def test_material_named_before_the_verb_is_not_treated_as_the_target() -> None:
    """真实门禁发现的缺陷：「用橡木木板做木棍」里的橡木木板是材料，不是目标。"""
    request = parse_craft_request("用橡木木板做一根木棍")
    assert request is not None and not request.problem
    assert request.item == "stick"
    assert request.material == "oak_planks"
    # 反过来（用木棍做木板）同样取动词之后的目标
    flipped = parse_craft_request("把木棍做成橡木木板")
    assert flipped is not None and not flipped.problem
    assert flipped.item == "oak_planks"


def test_woody_words_without_a_craft_verb_are_not_craft_requests() -> None:
    """「砍点木板」是采集语义（交回木材入口），合成入口绝不接走它。"""
    assert parse_craft_request("去砍点木板回来") is None
    assert parse_craft_request("帮我收集几块木板") is None


def test_generic_planks_without_a_species_clarifies() -> None:
    """只说「木板」= 树种歧义：澄清，绝不替用户挑树种（§9.1 用例 2）。"""
    request = parse_craft_request("做一点木板")
    assert request is not None and request.problem == "ambiguous_item"
    assert request.detail == "木板"


def test_negation_is_never_upgraded_into_a_task() -> None:
    """否定语气 → 明确不做（绝不产生可执行计划）。"""
    request = parse_craft_request("别做木棍了")
    assert request is not None and request.problem == "negated"
    assert is_craft_request("别做木棍了") is False


def test_zero_count_clarifies_instead_of_defaulting() -> None:
    """「0 个」是明确的数量表达，但不是合法请求 → 澄清（不得静默当成默认值）。"""
    request = parse_craft_request("做 0 个橡木木板")
    assert request is not None and request.problem == "zero_count"


def test_multiple_targets_are_ambiguous() -> None:
    """一次只冻结一个目标：多目标必须澄清。"""
    request = parse_craft_request("帮我做点木棍和橡木木板")
    assert request is not None and request.problem == "ambiguous_item"


def test_unknown_item_with_an_item_word_clarifies() -> None:
    """「做点东西」= 目标物品未知 → 澄清（§9.1 用例 3）。"""
    request = parse_craft_request("帮我做点东西")
    assert request is not None and request.problem == "unknown_item"


def test_non_item_goals_are_left_to_the_other_paths() -> None:
    """建房子这类目标不归合成入口（没有点名任何可合成物品）。"""
    assert parse_craft_request("帮我做个房子") is None
    assert parse_craft_request("我们在做什么任务") is None


def test_count_parsing_accepts_full_digits_and_chinese_numerals() -> None:
    """完整数字（不被截断）+ 中文数字 + 单位。"""
    assert parse_count("做 100 个木板") == 100
    assert parse_count("做三根木棍") == 3
    assert parse_count("做点木棍") is None


def test_helpers_and_display_names() -> None:
    assert mentions_craft("做点木棍") is True
    assert mentions_craft("今天天气不错") is False
    assert is_craft_request("帮我做一根木棍") is True
    assert display_name_of("stick") == "木棍"
    assert display_name_of("oak_log") == "橡木原木"
    # 拿不到映射就原样返回，绝不编造用户语言名
    assert display_name_of("mystery_item") == "mystery_item"
