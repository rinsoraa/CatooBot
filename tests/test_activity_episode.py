"""Phase 6A §三-§七/§四十五：Episode 模型、状态机、duration 与 ID（矩阵 A / H + 基础）。

* Episode 是"当前活动"的唯一事实来源（§一/§八）；
* 状态机显式且**拒绝**非法转移（§六）；
* 时长是 Episode 的**属性**（min/typical/max），不是每 tick 现算/随机（§七/§四十五）；
* Episode ID 稳定、可审计、能反解（§四）。
"""

from __future__ import annotations

import pathlib

import pytest

from app.activity.model import (
    ALLOWED_ACTIVITY_TRANSITIONS,
    DEFAULT_VIRTUAL_PROFILE,
    LIVE_ACTIVITY_STATUSES,
    TERMINAL_ACTIVITY_STATUSES,
    VIRTUAL_DURATIONS,
    ActivityEpisode,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    TransitionReason,
    activity_label,
    build_episode,
    duration_profile,
    episode_id_for,
    looks_like_minecraft_activity,
    parse_episode_id,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def episode(**overrides: object) -> ActivityEpisode:
    base = dict(
        episode_id="ACT-20261008-001",
        character_id="罐头@1dae9716",
        activity_type=ActivityType.VIRTUAL_LIFE,
        activity_name="reading",
        source=ActivitySource.ROUTINE,
        now=1_700_000_000.0,
    )
    base.update(overrides)
    return build_episode(**base)  # type: ignore[arg-type]


class TestEpisodeShape:
    def test_creation_has_all_audit_fields(self) -> None:
        """A：SCHEDULED 的 Episode 带着"是什么/什么时候/多久/为什么/谁建的"。"""
        item = episode()
        assert item.status is ActivityStatus.SCHEDULED
        assert item.activity_name == "reading"
        assert item.activity_type is ActivityType.VIRTUAL_LIFE
        assert item.source is ActivitySource.ROUTINE
        assert item.transition_reason == TransitionReason.SCHEDULED.value
        assert item.min_duration < item.typical_duration < item.max_duration
        assert item.created_at == 1_700_000_000.0 and item.updated_at == 1_700_000_000.0
        assert item.started_at == 0.0  # 还没开始
        payload = item.to_payload()
        for key in (
            "episode_id",
            "character_id",
            "activity_type",
            "activity_name",
            "location",
            "social_state",
            "tags",
            "started_at",
            "planned_end_at",
            "min_duration",
            "typical_duration",
            "max_duration",
            "status",
            "transition_reason",
            "source",
            "parent_episode_id",
            "related_task_id",
        ):
            assert key in payload, key

    def test_payload_round_trip(self) -> None:
        item = episode(tags=["a", "b"], related_task_id="task_1", location="客厅")
        again = ActivityEpisode.from_payload(item.to_payload())
        assert again.to_payload() == item.to_payload()

    def test_max_end_is_started_plus_max(self) -> None:
        item = episode(status=ActivityStatus.ACTIVE)
        item.started_at = 1000.0
        assert item.max_end_at == 1000.0 + item.max_duration


class TestStateMachine:
    def test_allowed_transitions_table(self) -> None:
        """H：只有 §六 画出来的那些箭头；终态没有出口。"""
        assert ALLOWED_ACTIVITY_TRANSITIONS[ActivityStatus.SCHEDULED] == frozenset(
            {ActivityStatus.ACTIVE, ActivityStatus.CANCELLED, ActivityStatus.EXPIRED}
        )
        for status in TERMINAL_ACTIVITY_STATUSES:
            assert ALLOWED_ACTIVITY_TRANSITIONS[status] == frozenset()
        assert ActivityStatus.EXTENDED in ALLOWED_ACTIVITY_TRANSITIONS[ActivityStatus.EXTENDED]

    @pytest.mark.parametrize(
        "start",
        [
            ActivityStatus.COMPLETED,
            ActivityStatus.CANCELLED,
            ActivityStatus.EXPIRED,
            ActivityStatus.INTERRUPTED,
        ],
    )
    def test_terminal_never_returns_to_active(self, start: ActivityStatus) -> None:
        """§六 明确点名禁止的三条（COMPLETED/CANCELLED/EXPIRED → ACTIVE）以及 INTERRUPTED。"""
        item = episode(status=start)
        assert not item.can_transition_to(ActivityStatus.ACTIVE)
        for target in ActivityStatus:
            assert not item.can_transition_to(target) or target is start

    def test_scheduled_cannot_jump_to_completed(self) -> None:
        assert not episode().can_transition_to(ActivityStatus.COMPLETED)

    def test_open_and_terminal_are_disjoint(self) -> None:
        assert not (LIVE_ACTIVITY_STATUSES & TERMINAL_ACTIVITY_STATUSES)
        for status in ActivityStatus:
            assert status.open != status.terminal


class TestDurationModel:
    def test_known_virtual_activities_have_three_values(self) -> None:
        for name in ("eating", "reading", "gaming", "sleeping", "idle"):
            minimum, typical, maximum = VIRTUAL_DURATIONS[name]
            assert 0 < minimum <= typical <= maximum

    def test_unknown_activity_falls_back_deterministically(self) -> None:
        unknown = "没听过的活动"
        first = duration_profile(ActivityType.VIRTUAL_LIFE, unknown)
        assert first == DEFAULT_VIRTUAL_PROFILE
        assert duration_profile(ActivityType.VIRTUAL_LIFE, unknown) == first  # 确定性

    def test_task_activity_gets_a_wide_profile(self) -> None:
        minimum, typical, maximum = duration_profile(ActivityType.TASK_EXECUTION, "minecraft_task")
        assert minimum <= typical <= maximum
        assert maximum >= 3600.0

    def test_no_randomness_anywhere_in_the_package(self) -> None:
        """§四十五：活动/时长/转移**绝不**用 random。"""
        package = REPO_ROOT / "app" / "activity"
        for path in sorted(package.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert "import random" not in source, path
            assert "random." not in source, path
            assert "uniform(" not in source, path
            assert "choice(" not in source, path


class TestEpisodeId:
    def test_format_and_parse(self) -> None:
        """§四：``ACT-YYYYMMDD-NNN``，可反解。"""
        assert episode_id_for("20261008", 1) == "ACT-20261008-001"
        assert episode_id_for("20261008", 42) == "ACT-20261008-042"
        assert parse_episode_id("ACT-20261008-007") == ("20261008", 7)

    @pytest.mark.parametrize(
        "bad",
        ["", "ACT", "ACT-20261008", "foo-20261008-001", "ACT-2026-001", "ACT-20261008-xx"],
    )
    def test_bad_ids_are_refused_not_guessed(self, bad: str) -> None:
        assert parse_episode_id(bad) is None

    def test_activity_name_is_not_an_id(self) -> None:
        item = episode()
        assert item.episode_id != item.activity_name
        assert parse_episode_id(item.activity_name) is None


class TestVocabulary:
    def test_minecraft_names_are_recognised(self) -> None:
        for name in ("minecraft_task", "minecraft_digging", "mc_explore", "digging"):
            assert looks_like_minecraft_activity(name)
        for name in ("reading", "eating", "out", "idle"):
            assert not looks_like_minecraft_activity(name)

    def test_labels_are_presentation_only(self) -> None:
        assert activity_label("reading") == "看书"
        assert activity_label("没听过的活动") == "没听过的活动"
