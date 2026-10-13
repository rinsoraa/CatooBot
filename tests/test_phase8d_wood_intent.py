"""Phase 8D：木材采集意图解析的确定性测试（不依赖模型、不用 sleep）。"""

from __future__ import annotations

import pytest

from app.tasks.intent import WOOD_MAX_TARGETS, WoodRequest, parse_wood_request

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
