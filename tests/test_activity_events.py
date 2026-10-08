"""Phase 6A §三十二/§三十三/§三十四：Activity 事件（矩阵 L + LLM 隔离 + P）。

* 事件名固定八个、载荷固定六个字段（§三十二）；
* **幂等**：同一个 Episode 的一次性转移只发一次 —— 包括"重启之后又试了一次"（§三十三）；
* **事件不产生 AI Turn**（§三十四）：活动层没有任何通向模型/角色运行时的路径。
"""

from __future__ import annotations

import pathlib
from typing import Any

from app.activity import (
    ACTIVITY_CANCELLED,
    ACTIVITY_COMPLETED,
    ACTIVITY_EVENT_NAMES,
    ACTIVITY_EXPIRED,
    ACTIVITY_EXTENDED,
    ACTIVITY_INTERRUPTED,
    ACTIVITY_RECOVERED,
    ACTIVITY_SCHEDULED,
    ACTIVITY_STARTED,
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityRuntime,
    FakeClock,
    InMemoryActivityStore,
    SqliteActivityStore,
    TransitionReason,
)
from app.config.settings import DatabaseConfig
from app.database.database import Database

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTIVITY_PACKAGE = REPO_ROOT / "app" / "activity"


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, dict(payload)))

    @property
    def names(self) -> list[str]:
        return [name for name, _payload in self.events]


def build(store: Any, clock: FakeClock, recorder: Recorder) -> ActivityRuntime:
    return ActivityRuntime(
        store=store,
        clock=clock,
        character_id="罐头@deadbeef",
        planner=ActivityPlanner(),
        publisher=ActivityEventPublisher(sink=recorder),
    )


class TestPayloadShape:
    def test_eight_event_names(self) -> None:
        assert ACTIVITY_EVENT_NAMES == (
            ACTIVITY_SCHEDULED,
            ACTIVITY_STARTED,
            ACTIVITY_EXTENDED,
            ACTIVITY_COMPLETED,
            ACTIVITY_INTERRUPTED,
            ACTIVITY_CANCELLED,
            ACTIVITY_EXPIRED,
            ACTIVITY_RECOVERED,
        )

    async def test_payload_carries_the_six_required_fields(self) -> None:
        recorder = Recorder()
        clock = FakeClock()
        runtime = build(InMemoryActivityStore(), clock, recorder)
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        name, payload = recorder.events[-1]
        assert name == ACTIVITY_STARTED
        assert set(payload) == {
            "episode_id",
            "character_id",
            "activity",
            "status",
            "timestamp",
            "reason",
            "source",
        }
        assert payload["episode_id"] == episode.episode_id
        assert payload["character_id"] == "罐头@deadbeef"
        assert payload["activity"] == "reading"
        assert payload["reason"] == TransitionReason.SCHEDULED.value
        assert payload["source"] == "ROUTINE"
        assert payload["timestamp"] == clock.now()


class TestIdempotency:
    async def test_duplicate_terminal_transition_is_not_published_twice(self) -> None:
        """L：同一个 Episode 的终结事件只发一次。"""
        recorder = Recorder()
        clock = FakeClock()
        runtime = build(InMemoryActivityStore(), clock, recorder)
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        await runtime.complete(now=clock.now())
        assert recorder.names.count(ACTIVITY_COMPLETED) == 1
        # 再直接发一次（模拟调度重试/重复投递）→ 幂等拦住
        published = await runtime.publisher.publish(
            ACTIVITY_COMPLETED,
            episode,
            store=runtime.store,
            timestamp=clock.now(),
            reason=TransitionReason.MANUAL.value,
        )
        assert published is False
        assert recorder.names.count(ACTIVITY_COMPLETED) == 1

    async def test_restart_retry_does_not_republish(self, tmp_path: Any) -> None:
        """§三十三：重启之后（新 runtime / 新 publisher）也**不允许**再发一次终结事件。"""
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'events.db'}"))
        await database.connect()
        clock = FakeClock()
        first = Recorder()
        runtime = build(SqliteActivityStore(database), clock, first)
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        await runtime.complete(now=clock.now())
        assert first.names.count(ACTIVITY_COMPLETED) == 1

        # 模拟重启：同一个库、全新的 runtime 与 publisher，然后重放同一条终结事件
        second = Recorder()
        after_restart = build(SqliteActivityStore(database), clock, second)
        stored = await after_restart.store.get(episode.episode_id)
        assert stored is not None
        published = await after_restart.publisher.publish(
            ACTIVITY_COMPLETED,
            stored,
            store=after_restart.store,
            timestamp=clock.now(),
            reason=TransitionReason.MANUAL.value,
        )
        assert published is False
        assert second.names == []
        await database.close()

    async def test_extended_may_repeat(self) -> None:
        """EXTENDED 不是一次性转移（延长可以发生多次，只是有上限）。"""
        recorder = Recorder()
        clock = FakeClock()
        runtime = build(InMemoryActivityStore(), clock, recorder)
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        await runtime.extend(now=clock.now())
        await runtime.extend(now=clock.now())
        assert recorder.names.count(ACTIVITY_EXTENDED) == 2

    async def test_sink_failure_never_breaks_the_lifecycle(self) -> None:
        clock = FakeClock()

        def boom(_name: str, _payload: dict[str, Any]) -> None:
            raise RuntimeError("subscriber exploded")

        runtime = ActivityRuntime(
            store=InMemoryActivityStore(),
            clock=clock,
            character_id="c",
            planner=ActivityPlanner(),
            publisher=ActivityEventPublisher(sink=boom),
        )
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        assert (await runtime.complete(now=clock.now())) is not None


class TestNoLlmIsolation:
    def test_activity_package_never_reaches_the_model_or_chat(self) -> None:
        """§三十四：活动事件是纯状态事件 —— 这个包连 AI/角色/聊天都不 import。"""
        forbidden = (
            "app.ai",
            "app.character",
            "app.plugins",
            "app.adapters",
            "app.message",
            "app.ai.engine",
            "CharacterRuntime",
            "AIEngine",
            "respond(",
            "send_private_msg",
            "send_group_msg",
        )
        for path in sorted(ACTIVITY_PACKAGE.glob("*.py")):
            if path.name == "model_advisor.py":
                # Phase 6D §四十四/§七十九：唯一被批准的模型缝（只引用 provider 抽象）
                continue
            source = path.read_text(encoding="utf-8")
            for needle in forbidden:
                assert needle not in source, f"{path.name} 不应出现 {needle}"

    def test_no_activity_event_consumer_in_the_bot(self) -> None:
        """Bot 装配时**没有**给事件挂任何会说话/会思考的 sink（只有日志）。"""
        source = (REPO_ROOT / "app" / "core" / "bot.py").read_text(encoding="utf-8")
        assert "ActivityEventPublisher(logger=self.log)" in source
        assert "ActivityEventPublisher(sink=" not in source

    def test_character_runtime_only_reads_activity(self) -> None:
        """角色侧只能**读**活动（context_block）；它拿不到任何改活动的入口。"""
        source = (REPO_ROOT / "app" / "character" / "runtime.py").read_text(encoding="utf-8")
        assert "context_block()" in source  # 只读它现在的活动
        for forbidden in ("activity.start(", "activity.switch_to(", "activity.complete("):
            assert forbidden not in source
