"""Phase 6A §十四/§四十/§四十一：CharacterState 投影与上下文（矩阵 P 的上下文一侧）。

两条硬规则：

* **§十四**：``CharacterState.activity*`` 只是当前 Episode 的派生快照 —— 只由投影写；
  连 WebUI 的"直接改 activity"也必须翻译成一次显式换活动，不许绕过 Episode。
* **§四十/§四十一**：给模型的上下文 1 条当前活动 + ≤3 条最近变化；优先级是
  「当前世界 > 当前任务 > 当前 Episode > 最近活动 > 相关记忆」，那只是**上下文**顺序，
  **不是**授权顺序（§五十）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.activity import (
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    ActivitySource,
    ActivityStatus,
    FakeClock,
    InMemoryActivityStore,
    TransitionReason,
)
from app.activity.projection import RECENT_TRANSITION_BUDGET, activity_context_block
from app.character.context import CharacterContextBuilder
from app.character.persona import Persona
from app.character.relationship import Relationship
from app.character.state import CharacterState
from app.web.api.domain import CHARACTER_STATE_FIELDS, DomainApiRoutes
from app.web.api_errors import ApiError


class RecordingStates:
    """替换 StateManager：记录每次投影写了什么。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {}

    async def update(self, **changes: Any) -> dict[str, Any]:
        self.calls.append(dict(changes))
        self.data.update({k: v for k, v in changes.items() if k != "reason"})
        return dict(self.data)

    async def load(self) -> CharacterState:
        known = {k: v for k, v in self.data.items() if k in CharacterState.model_fields}
        return CharacterState(**known)


class FailingStates(RecordingStates):
    async def update(self, **changes: Any) -> dict[str, Any]:
        raise RuntimeError("state store down")


def build(states: Any, *, character_id: str = "罐头@deadbeef") -> tuple[ActivityRuntime, FakeClock]:
    clock = FakeClock(1_700_000_000.0)
    runtime = ActivityRuntime(
        store=InMemoryActivityStore(),
        clock=clock,
        character_id=character_id,
        planner=ActivityPlanner(),
        publisher=ActivityEventPublisher(),
        projection=ActivityProjection(states),
    )
    return runtime, clock


class TestProjection:
    async def test_episode_fields_land_on_character_state(self) -> None:
        states = RecordingStates()
        runtime, clock = build(states)
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        assert states.data["activity"] == "reading"
        assert states.data["current_activity_episode_id"] == episode.episode_id
        assert states.data["activity_status"] == ActivityStatus.ACTIVE.value
        assert states.data["activity_source"] == ActivitySource.ROUTINE.value
        assert states.data["activity_started_at"] == int(episode.started_at)
        assert states.data["activity_planned_end_at"] == int(episode.planned_end_at)
        assert states.data["activity_since"] == int(episode.started_at)

    async def test_status_change_is_projected_too(self) -> None:
        states = RecordingStates()
        runtime, clock = build(states)
        await runtime.start(activity_name="reading", now=clock.now())
        clock.advance_minutes(70)
        updated = await runtime.advance()
        assert updated is not None
        assert states.data["activity_status"] == updated.status.value
        assert states.calls[-1]["reason"] == TransitionReason.TIME_EXPIRED.value

    async def test_finished_episode_leaves_no_current_activity(self) -> None:
        """Episode 一终结，快照就清空（"她刚结束一件事"），而且**不**编一个假活动出来：

        * ``CharacterState`` 是不含终结 Episode 的（activity 只反映**当前** Episode）；
        * 给模型的上下文也为空（她此刻没有进行中的活动）。
        """
        states = RecordingStates()
        runtime, clock = build(states)
        await runtime.start(activity_name="reading", now=clock.now())
        await runtime.complete(now=clock.now())
        assert states.data["activity"] == ""
        assert states.data["current_activity_episode_id"] == ""
        assert states.data["activity_status"] == ""
        assert await runtime.context_block() == ""
        # 历史仍然可查（审计不受影响）
        assert await runtime.recent(5)

    async def test_projection_failure_only_degrades(self) -> None:
        runtime, _clock = build(FailingStates())
        episode = await runtime.start(activity_name="reading")
        assert episode is not None  # Episode 照样成立
        assert runtime.projection is not None and runtime.projection.degraded_reason


class FakeRequest:
    """最小的 aiohttp 请求替身（只用到 read_json/ok 需要的那几个面）。"""

    def __init__(self, body: dict[str, Any]) -> None:
        self._raw = json.dumps(body).encode("utf-8")
        self.content_length = len(self._raw)
        self.headers: dict[str, str] = {}
        self.query: dict[str, str] = {}
        self.match_info: dict[str, str] = {}

    def get(self, key: str, default: Any = None) -> Any:
        """aiohttp 的请求对象支持 ``request.get(key)``（中间件写进去的 request id）。"""
        return self.headers.get(key, default)

    async def read(self) -> bytes:
        return self._raw

    async def json(self) -> Any:
        return json.loads(self._raw.decode("utf-8"))


class StubAdmin:
    """替换 AdminService：记录直接写进 CharacterState 的字段。"""

    def __init__(self, states: CharacterState | None = None) -> None:
        self.writes: list[dict[str, Any]] = []
        self.state = states or CharacterState()

    async def set_state(self, **changes: Any) -> CharacterState:
        self.writes.append(dict(changes))
        self.state = self.state.model_copy(update=changes)
        return self.state


class StubBot:
    def __init__(self, activity: Any) -> None:
        self.activity = activity


class TestReverseWriteGuard:
    """§十四：WebUI 直接改 activity 也必须走 Episode（否则状态会分裂）。"""

    async def test_patch_activity_goes_through_the_episode(self) -> None:
        states = RecordingStates()
        runtime, _clock = build(states)
        api = DomainApiRoutes()
        api._bot = StubBot(runtime)  # type: ignore[attr-defined]
        api._admin = StubAdmin()  # type: ignore[attr-defined]

        body = {"activity": "gaming", "reason": "webui"}
        response = await api._v1_character_state_patch(FakeRequest(body))  # noqa: SLF001
        assert response.status == 200
        current = await runtime.current()
        assert current is not None
        assert current.activity_name == "gaming"
        assert current.source is ActivitySource.USER
        assert current.transition_reason == TransitionReason.MANUAL.value
        # CharacterState 里那条 activity 是**投影**写的，不是 admin 直接写的
        assert states.data["activity"] == "gaming"
        written = api._admin.writes[-1]  # type: ignore[attr-defined]
        assert "activity" not in written  # 没绕过 Episode 直接写
        assert written.get("reason") == "webui"  # 其它字段照旧直接写
        assert "gaming" not in json.dumps(written, ensure_ascii=False)

    async def test_patch_without_activity_runtime_keeps_the_old_path(self) -> None:
        """没有活动能力（关掉/装配失败）时才退回直接写 —— 明确的降级路径。"""
        api = DomainApiRoutes()
        api._bot = StubBot(None)  # type: ignore[attr-defined]
        api._admin = StubAdmin()  # type: ignore[attr-defined]
        response = await api._v1_character_state_patch(FakeRequest({"activity": "reading"}))  # noqa: SLF001
        assert response.status == 200
        assert api._admin.writes[-1]["activity"] == "reading"  # type: ignore[attr-defined]

    async def test_projection_fields_are_not_writable_via_the_api(self) -> None:
        for key in (
            "current_activity_episode_id",
            "activity_started_at",
            "activity_planned_end_at",
            "activity_status",
            "activity_source",
        ):
            assert key not in CHARACTER_STATE_FIELDS
        # activity 本身在字段表里（降级路径要用），但它会被翻译成换活动（上一个用例）
        assert "activity" in CHARACTER_STATE_FIELDS

    async def test_unknown_field_is_still_rejected(self) -> None:
        api = DomainApiRoutes()
        api._bot = StubBot(None)  # type: ignore[attr-defined]
        api._admin = StubAdmin()  # type: ignore[attr-defined]
        # 处理器直接调用时抛的是契约错误（HTTP 状态由 json_endpoint 包装层负责）
        with pytest.raises(ApiError) as caught:
            await api._v1_character_state_patch(FakeRequest({"activity_status": "ACTIVE"}))  # noqa: SLF001
        assert caught.value.status == 422
        assert caught.value.code


class TestContextBudget:
    async def test_empty_without_any_episode(self) -> None:
        states = RecordingStates()
        runtime, _clock = build(states)
        assert await runtime.context_block() == ""
        assert activity_context_block(None) == ""

    async def test_block_has_label_token_and_plan(self) -> None:
        states = RecordingStates()
        runtime, clock = build(states)
        await runtime.start(activity_name="reading", now=clock.now())
        block = await runtime.context_block()
        assert block.startswith("现在的活动：")
        assert "看书" in block and "reading" in block
        assert "ROUTINE" in block and "ACTIVE" in block

    async def test_recent_transitions_are_capped(self) -> None:
        states = RecordingStates()
        runtime, clock = build(states)
        await runtime.start(activity_name="reading", now=clock.now())
        await runtime.extend(now=clock.now())
        await runtime.extend(now=clock.now())
        block = await runtime.context_block()
        line = [row for row in block.splitlines() if row.startswith("最近的活动变化")][0]
        assert line.count("、") + 1 <= RECENT_TRANSITION_BUDGET

    async def test_related_task_is_shown_but_not_authoritative(self) -> None:
        states = RecordingStates()
        runtime, clock = build(states)
        await runtime.start(
            activity_name="minecraft_task",
            activity_type=runtime.planner.initial(now=clock.now(), clock=clock).activity_type,
            source=ActivitySource.TASK,
            related_task_id="task_abc",
            now=clock.now(),
        )
        block = await runtime.context_block()
        assert "task_abc" in block

    def test_prompt_order_follows_the_context_priority(self) -> None:
        """§四十一：世界 > 当前任务 > 当前 Episode > 最近活动 > 相关记忆（顺序，不是权限）。"""
        builder = CharacterContextBuilder()
        messages = builder.build(
            Persona(),
            CharacterState(),
            Relationship(user_id="1"),
            [],
            [],
            "在吗",
            world=None,
            minecraft="MINECRAFT_MARK",
            activity="ACTIVITY_MARK",
        )
        system = "\n".join(str(message.content) for message in messages)
        assert "ACTIVITY_MARK" in system
        assert system.index("MINECRAFT_MARK") < system.index("ACTIVITY_MARK")
