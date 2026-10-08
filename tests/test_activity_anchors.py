"""Phase 6C §十二-§十五/§四十：日程锚点（ScheduleAnchor / AnchorBook）。

锚点是 **Planner 的输入**（§一：只负责 planning/ranking/scheduling），所以这里断言的
全是"只读计算"：窗口、阶段、契合度、优先级、遵守度统计。
"锚点到了也不许强行切活动"那一条（§十四）在 6B 集成用例里验（见 plan_recovery）。
"""

from __future__ import annotations

import pytest

from app.activity import (
    ANCHOR_PRIORITY_ORDER,
    DEFAULT_ANCHORS,
    AnchorBook,
    AnchorPhase,
    AnchorPriority,
    ScheduleAnchor,
    anchor_adherence,
)
from tests.activity_plan_fakes import clock_at

# ---------------------------------------------------------------- 模型


class TestAnchorModel:
    def test_anchor_fields_and_defaults(self) -> None:
        """§十二：id / activity / target_time / 窗口 / 优先级 / hard。"""
        anchor = ScheduleAnchor(anchor_id="lunch", activity="eating", target_time="12:00")
        assert anchor.target_minute == 12 * 60
        assert anchor.window_before == 45 * 60.0
        assert anchor.window_after == 45 * 60.0
        assert anchor.priority is AnchorPriority.MEAL
        assert anchor.days is None  # 每天

    def test_bad_time_or_missing_id_raises(self) -> None:
        """构造即校验（早失败好过半夜三点才发现锚点写错了）。"""
        for bad in ("12", "25:00", "12:70", "noon", ""):
            with pytest.raises(ValueError):
                ScheduleAnchor(anchor_id="x", activity="eating", target_time=bad)
        with pytest.raises(ValueError):
            ScheduleAnchor(anchor_id="", activity="eating", target_time="12:00")
        with pytest.raises(ValueError):
            ScheduleAnchor(anchor_id="x", activity="", target_time="12:00")

    def test_default_day_has_sleep_and_three_meals(self) -> None:
        ids = [anchor.anchor_id for anchor in DEFAULT_ANCHORS]
        assert ids == ["sleep", "breakfast", "lunch", "dinner"]
        assert all(anchor.hard for anchor in DEFAULT_ANCHORS)  # §十四：睡觉 + 三餐是硬的

    def test_priority_order_is_the_book_order(self) -> None:
        """§十三：sleep > meal > fixed_event > routine_activity > free_activity（从高到低）。"""
        assert ANCHOR_PRIORITY_ORDER == (
            "sleep",
            "meal",
            "fixed_event",
            "routine_activity",
            "free_activity",
        )
        weights = [AnchorPriority(name).weight for name in ANCHOR_PRIORITY_ORDER]
        assert weights == sorted(weights, reverse=True)
        assert AnchorPriority.SLEEP.rank == 0
        assert AnchorPriority.FREE_ACTIVITY.rank == len(ANCHOR_PRIORITY_ORDER) - 1


# ---------------------------------------------------------------- 窗口与阶段


class TestAnchorWindow:
    def test_window_is_local_clock_time(self) -> None:
        """窗口按**本地钟面**（配置时区）算：12:00 的锚点在 +08 时区就是本地 12:00。"""
        clock = clock_at(12, 0)
        anchor = ScheduleAnchor(anchor_id="lunch", activity="eating", target_time="12:00")
        start, end = anchor.window(clock, clock.now())
        assert clock.local(start).strftime("%H:%M") == "11:15"
        assert clock.local(end).strftime("%H:%M") == "12:45"
        # 目标时刻就是本地正午整点
        assert anchor.target_at(clock, clock.now()) == pytest.approx(clock.now())

    def test_phase_transitions(self) -> None:
        anchor = ScheduleAnchor(anchor_id="lunch", activity="eating", target_time="12:00")
        assert anchor.phase(clock_at(10, 0), clock_at(10, 0).now()) is AnchorPhase.BEFORE
        assert anchor.phase(clock_at(12, 10), clock_at(12, 10).now()) is AnchorPhase.DUE
        assert anchor.phase(clock_at(13, 10), clock_at(13, 10).now()) is AnchorPhase.MISSED
        assert anchor.phase(clock_at(20, 0), clock_at(20, 0).now()) is AnchorPhase.DONE

    def test_fit_is_one_inside_the_window_and_decays_outside(self) -> None:
        """§十五 的"弹性窗口"：窗口内给满，窗口外线性衰减到 0。"""
        anchor = ScheduleAnchor(anchor_id="lunch", activity="eating", target_time="12:00")
        inside = clock_at(12, 0)
        assert anchor.fit(inside, inside.now()) == pytest.approx(1.0)
        near = clock_at(11, 0)  # 窗口起点 11:15 之前 15 分钟
        assert 0.0 < anchor.fit(near, near.now()) < 1.0
        far = clock_at(9, 0)
        assert anchor.fit(far, far.now()) == 0.0
        missed = clock_at(13, 10)
        assert anchor.fit(missed, missed.now()) == pytest.approx(0.35)

    def test_days_filter_restricts_the_anchor(self) -> None:
        weekday_only = ScheduleAnchor(
            anchor_id="work",
            activity="working",
            target_time="10:00",
            days=frozenset({0, 1, 2, 3, 4}),
        )
        monday = clock_at(10, 0, day=19)  # 2026-10-19 是周一
        assert monday.local_weekday() == 0
        assert weekday_only.active_on(monday, monday.now()) is True
        weekend = clock_at(10, 0, day=17)  # 周六
        assert weekend.local_weekday() == 5
        assert weekday_only.active_on(weekend, weekend.now()) is False
        assert weekday_only.phase(weekend, weekend.now()) is AnchorPhase.DONE


# ---------------------------------------------------------------- AnchorBook


class TestAnchorBook:
    def test_book_is_sorted_by_priority_then_time(self) -> None:
        clock = clock_at(12, 0)
        book = AnchorBook()
        ids = [anchor.anchor_id for anchor in book.all()]
        assert ids[0] == "sleep"  # 最高优先级排最前
        assert book.to_payload(clock, clock.now())[0]["anchor_id"] == "sleep"

    def test_due_and_upcoming(self) -> None:
        clock = clock_at(12, 10)
        book = AnchorBook()
        due = [anchor.anchor_id for anchor in book.due(clock, clock.now())]
        assert due == ["lunch"]
        upcoming = [anchor.anchor_id for anchor in book.upcoming(clock, clock.now())]
        assert "dinner" in upcoming and "lunch" not in upcoming

    def test_next_after_picks_the_nearest_pending_anchor(self) -> None:
        clock = clock_at(9, 0)
        book = AnchorBook()
        nxt = book.next_after(clock, clock.now())
        assert nxt is not None and nxt.anchor_id == "lunch"

    def test_best_fit_returns_the_sponsoring_anchor(self) -> None:
        """哪个锚点在等这个活动（Planner 的 ``anchor_fit`` 来源）。"""
        clock = clock_at(12, 5)
        book = AnchorBook()
        anchor, fit = book.best_fit("eating", clock, clock.now())
        assert anchor is not None and anchor.anchor_id == "lunch"
        assert fit == pytest.approx(1.0)
        anchor_none, fit_none = book.best_fit("gaming", clock, clock.now())
        assert anchor_none is None and fit_none == 0.0

    def test_duplicate_ids_are_refused(self) -> None:
        with pytest.raises(ValueError):
            AnchorBook(
                anchors=(
                    ScheduleAnchor(anchor_id="x", activity="eating", target_time="12:00"),
                    ScheduleAnchor(anchor_id="x", activity="eating", target_time="18:00"),
                )
            )

    def test_custom_book_is_used_instead_of_the_default(self) -> None:
        """锚点是可注入的（测试/运维给不同的一天），默认那套只是默认。"""
        book = AnchorBook(
            anchors=(ScheduleAnchor(anchor_id="tea", activity="eating", target_time="15:30"),)
        )
        clock = clock_at(15, 30)
        assert [anchor.anchor_id for anchor in book.due(clock, clock.now())] == ["tea"]


# ---------------------------------------------------------------- 遵守度


class TestAnchorAdherence:
    def test_adherence_counts_only_elapsed_anchors(self) -> None:
        """§六十三：还没到的锚点不算失约（否则 3 天模拟的比率没有意义）。"""
        clock = clock_at(14, 0)
        spans = [
            ("eating", clock.day_start(clock.now()) + 11 * 3600, clock.day_start() + 13 * 3600)
        ]
        report = anchor_adherence(AnchorBook(), spans, clock=clock, now=clock.now())
        # 早饭（08:00，窗口 07:15–08:45 已过）与午饭（12:00，窗口 11:15–12:45 已过）都该计入
        checked = {item["anchor_id"] for item in report["anchors"]}
        assert "breakfast" in checked and "lunch" in checked
        assert "dinner" not in checked and "sleep" not in checked
        lunch = next(item for item in report["anchors"] if item["anchor_id"] == "lunch")
        assert lunch["matched"] is True  # 11:00–13:00 覆盖了 12:00

    def test_adherence_reports_misses(self) -> None:
        clock = clock_at(14, 0)
        spans = [("gaming", clock.day_start() + 9 * 3600, clock.day_start() + 13 * 3600)]
        report = anchor_adherence(AnchorBook(), spans, clock=clock, now=clock.now())
        assert 0.0 <= report["ratio"] < 1.0
        assert all(item["matched"] is False for item in report["anchors"])

    def test_adherence_of_empty_history_is_neutral(self) -> None:
        clock = clock_at(14, 0)
        report = anchor_adherence(AnchorBook(), [], clock=clock, now=clock.now())
        assert report["checked"] == 0
        assert report["ratio"] == 1.0
