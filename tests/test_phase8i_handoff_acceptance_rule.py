"""Phase 8I H3：资源交接的**接受**判定（真机门禁发现的真实缺陷回归）。

决策层会把「给我做 64 块橡木木板」和「好，去砍橡木原木」都判成 task_proposal；
前者是**新请求**，绝不能消费上一轮的资源提案 —— 否则重复/无关的合成请求会在没人
接受的情况下把提案静默变成采集任务。
"""

from __future__ import annotations

from app.integrations.minecraft.chat_bridge import _is_new_craft_request


class TestNewCraftRequest:
    def test_valid_craft_request_is_a_new_request(self) -> None:
        assert _is_new_craft_request("给我做 4 块橡木木板") is True
        assert _is_new_craft_request("在工作台上做一把木镐") is True

    def test_repeat_craft_request_is_not_an_acceptance(self) -> None:
        assert _is_new_craft_request("给我做 64 块橡木木板") is True
        assert _is_new_craft_request("帮我做 64 块橡木木板") is True

    def test_plain_acceptance_is_not_a_craft_request(self) -> None:
        assert _is_new_craft_request("好，去砍橡木原木") is False
        assert _is_new_craft_request("可以") is False
        assert _is_new_craft_request("砍两根橡木原木") is False

    def test_unactionable_craft_talk_is_not_a_new_request(self) -> None:
        assert _is_new_craft_request("别做木板") is False
        assert _is_new_craft_request("做一下") is False
