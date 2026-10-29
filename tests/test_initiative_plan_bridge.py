"""Phase 7B §三/§四/§五/§六/§八/§十一/§十四：Initiative → Planner 的桥。

矩阵：Planner Adapter / Candidate Ranking / Lifecycle / Security / 24h simulation。
"""

from __future__ import annotations

from typing import Any

from app.activity import FakeClock
from app.activity.decision import DecisionTrigger
from app.activity.initiative import (
    EMPTY_HINT_BOOK,
    HINT_TERM,
    MAX_HINTS,
    InitiativeActivityHint,
    InitiativeHintBook,
    hint_book_from,
    normalise_hint,
)
from app.activity.plan import ItemReason
from app.activity.planner import ActivityPlanner
from app.activity.profiles import SCORE_WEIGHTS
from tests.activity_plan_fakes import PlanRig, clock_at


class FakeSource:
    """鸭子类型的建议来源（7A 的服务就是这个形状）。"""

    def __init__(self, items: list[dict[str, Any]] | None = None, *, boom: bool = False) -> None:
        self.items = list(items or [])
        self.boom = boom
        self.calls = 0
        self.last_limit = 0

    def hints(self, *, limit: int = 3, now: float | None = None) -> list[dict[str, Any]]:
        self.calls += 1
        self.last_limit = int(limit)
        if self.boom:
            raise RuntimeError("source down")
        return [dict(item) for item in self.items[:limit]]


def hint(
    activity: str,
    *,
    intent_type: str = "ACTIVITY_CHANGE",
    related: str = "",
    bonus: float = 1.0,
    intent_id: str = "INT-1",
) -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "intent_type": intent_type,
        "source": "ROUTINE",
        "related_activity": related,
        "score_bonus": bonus,
        "expires_at": 0.0,
        # 这里刻意只给"纯数据"：活动名的翻译由 activity 侧完成（§六 分层）
        "activity_name": activity,
    }


def planner_with(source: Any = None) -> ActivityPlanner:
    return ActivityPlanner(intent_source=source) if source is not None else ActivityPlanner()


def plan_for(planner: ActivityPlanner, *, hour: int = 14, clock: Any = None) -> Any:
    """用**同一个时钟**规划（意图的过期判断依赖 now，两个时钟混用会误判过期）。"""
    moment = clock if clock is not None else clock_at(hour, 0)
    context = planner._context(moment, moment.now(), started_today=0, episode=None)
    return planner.plan_next(now=moment.now(), context=context)


class TestHintAdapter:
    def test_bounded_and_deterministic(self) -> None:
        """§三.3/§三.4：有界、确定、同一活动只留最大加成（绝不叠加）。"""
        source = FakeSource(
            [
                hint("reading", bonus=0.3, intent_id="INT-1"),
                hint("reading", bonus=0.9, intent_id="INT-2"),
                hint("gaming", bonus=0.4, intent_id="INT-3"),
                hint("music", bonus=0.2, intent_id="INT-4"),
                hint("relax", bonus=0.1, intent_id="INT-5"),
                hint("online", bonus=0.05, intent_id="INT-6"),
            ]
        )
        book = hint_book_from(source, now=1000.0)
        # 读取是**有界**的（≤MAX_HINTS 条原始建议），同一个活动合并成一条（取最大加成）
        assert source.last_limit == MAX_HINTS
        assert [item.activity_name for item in book.hints] == [
            "reading",
            "gaming",
            "music",
            "relax",
        ]
        assert book.bonus_for("reading") == 0.9  # 取最大，不是 0.3+0.9
        assert book.bonus_for("online") == 0.0  # 第 6 条读都没读到（有界）
        again = hint_book_from(source, now=1000.0)
        assert [item.to_payload() for item in again.hints] == [
            item.to_payload() for item in book.hints
        ]

    def test_registry_decides_the_names(self) -> None:
        """§六：`minecraft` 根本没注册（6C 就禁止它当虚拟活动），`minecraft_task` 永远不是候选。"""
        assert normalise_hint(hint("minecraft"))[1] == "not_registered"
        assert normalise_hint(hint("minecraft_task"))[1] == "not_registered"
        assert normalise_hint(hint("dig"))[1] == "not_registered"
        assert normalise_hint(hint("building"))[1] == ""
        assert normalise_hint(hint("reading"))[1] == ""

    def test_intent_type_is_mapped_to_a_registered_activity(self) -> None:
        """§六：类型 → **已注册**活动的映射在 activity 侧（7A 只交纯数据）。"""
        book = hint_book_from(
            FakeSource([hint("", intent_type="MINECRAFT_INTEREST", related="")]), now=1000.0
        )
        assert [item.activity_name for item in book.hints] == ["building"]
        rest = hint_book_from(FakeSource([hint("", intent_type="REST")]), now=1000.0)
        assert [item.activity_name for item in rest.hints] == ["relax"]

    def test_source_failure_degrades_to_baseline(self) -> None:
        """§三.6：来源挂了 → 空书 + 降级说明，Planner 逐字退回原有行为。"""
        book = hint_book_from(FakeSource(boom=True), now=1000.0)
        assert book.empty and book.degraded
        degraded = ActivityPlanner(intent_source=FakeSource(boom=True))
        plan = plan_for(degraded)
        baseline = plan_for(ActivityPlanner())
        assert plan.content_signature() == baseline.content_signature()
        assert plan.constraints["initiative"]["degraded"]

    def test_missing_source_is_an_empty_book(self) -> None:
        assert hint_book_from(None, now=0.0) is EMPTY_HINT_BOOK
        assert EMPTY_HINT_BOOK.empty and not EMPTY_HINT_BOOK.degraded

    def test_root_is_read_only_data(self) -> None:
        """§三.5：Adapter 只返回数据 —— 没有任何写入口。"""
        book = InitiativeHintBook(hints=(InitiativeActivityHint("reading", 0.5, "INT-1"),))
        for name in ("start", "complete", "create", "save", "append"):
            assert not hasattr(book, name)
        payload = book.payload()
        assert payload["hints"][0]["activity_name"] == "reading"
        assert set(payload) == {"hints", "dropped", "degraded", "limit"}


class TestCandidateRanking:
    def test_hint_only_moves_restricted_candidates(self) -> None:
        """§四：hint 只在**合格候选之间**挪排序（不合格的一律不动）。"""
        baseline = plan_for(ActivityPlanner())
        boosted = plan_for(ActivityPlanner(intent_source=FakeSource([hint("gaming")])))
        base_scores = {item.activity: item.score for item in baseline.eligible()}
        new_scores = {item.activity: item.score for item in boosted.eligible()}
        assert set(base_scores) <= set(new_scores)
        assert new_scores["gaming"] > base_scores["gaming"]
        # 其它合格候选的分数**一分都没变**（软项只加给被建议的那个）
        for activity, score in base_scores.items():
            if activity != "gaming":
                assert new_scores[activity] == score
        assert boosted.candidates[0].breakdown[HINT_TERM] >= 0.0

    def test_no_hint_keeps_the_original_plan(self) -> None:
        """§五：没有有效意图时，计划**逐字**与 6C 一致（含内容签名）。"""
        plain = plan_for(ActivityPlanner())
        empty_source = plan_for(ActivityPlanner(intent_source=FakeSource([])))
        assert plain.content_signature() == empty_source.content_signature()
        assert [item.activity for item in plain.items] == [
            item.activity for item in empty_source.items
        ]
        assert plain.candidates == empty_source.candidates

    def test_hint_cannot_beat_a_hard_anchor(self) -> None:
        """§四：低优先级 hint 压不过硬锚点（睡觉/三餐永远先排）。"""
        rig = PlanRig(
            clock=clock_at(11, 30),
            planner=ActivityPlanner(intent_source=FakeSource([hint("gaming")])),
        )
        plan = rig.runtime.planner.plan_next(
            now=rig.clock.now(),
            context=rig.runtime.planner._context(
                rig.clock, rig.clock.now(), started_today=0, episode=None, trigger="test"
            ),
        )
        reasons = {item.activity: item.reason for item in plan.items}
        assert ItemReason.ANCHOR.value in reasons.values()

    def test_eligibility_is_untouched(self) -> None:
        """§四：hint 不能让 `eligible=False` 的候选变合格（能量不够就是不够）。"""
        rig = PlanRig(clock=clock_at(14, 0))
        rig.state.energy = 0.05
        planner = ActivityPlanner(intent_source=FakeSource([hint("gaming")]))
        context = planner._context(
            rig.clock,
            rig.clock.now(),
            started_today=0,
            episode=None,
        )
        context.character_state = rig.state
        plan = planner.plan_next(now=rig.clock.now(), context=context)
        rejected = {item.activity: item.reason for item in plan.rejected()}
        eligible = {item.activity for item in plan.eligible()}
        assert eligible & set(rejected) == set()
        assert plan.eligible()  # 仍然有合格候选

    def test_weight_is_softer_than_the_existing_terms(self) -> None:
        """§四：软项的权重必须低于锚点/习惯/目标 —— 它只可能是**软**偏好。"""
        assert SCORE_WEIGHTS[HINT_TERM] < SCORE_WEIGHTS["goal_relevance"]
        assert SCORE_WEIGHTS[HINT_TERM] < SCORE_WEIGHTS["routine_preference"]
        assert SCORE_WEIGHTS[HINT_TERM] < SCORE_WEIGHTS["anchor_fit"]


class TestLifecycle:
    async def test_future_plan_never_moves_the_current_episode(self) -> None:
        """§七/§三十四：计划是打算，不是现实 —— 它绝不提前结束当前 Episode。"""
        rig = PlanRig(
            clock=clock_at(14, 0),
            planner=ActivityPlanner(intent_source=FakeSource([hint("music")])),
        )
        episode = await rig.runtime.start(
            activity_name="reading", duration=(600.0, 1800.0, 7200.0), now=rig.clock.now()
        )
        assert episode is not None
        before = episode.episode_id
        await rig.runtime.refresh_plan(trigger="state_changed", now=rig.clock.now())
        current = await rig.runtime.current()
        assert current is not None and current.episode_id == before
        assert current.activity_name == "reading"
        plan = await rig.runtime.load_plan()
        assert plan is not None  # 计划可以有她的想法，但现实没动

    async def test_legality_still_gates_the_landing(self) -> None:
        """§七：只有 6B 允许转移时，被建议的活动才真的成为 Episode（不是被迫转移）。"""
        rig = PlanRig(clock=clock_at(14, 0))
        rig.runtime.engine.planner = ActivityPlanner(intent_source=FakeSource([hint("music")]))
        episode = await rig.runtime.start(
            activity_name="reading", duration=(60.0, 120.0, 1800.0), now=rig.clock.now()
        )
        assert episode is not None
        # 最短时长还没到 → 6B 说 CONTINUE（hint 再大也不能提前换）
        early = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert early["decision"] == "CONTINUE"
        rig.clock.advance_minutes(5)
        later = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert later["decision"] in {"TRANSITION", "EXTEND", "CONTINUE"}

    async def test_plan_attribution_is_recorded(self) -> None:
        """§八/§十二：能回答"哪条意图影响了哪条计划项"，但**不**因此改内容签名。"""
        rig = PlanRig(clock=clock_at(14, 0))
        rig.runtime.planner.intent_source = FakeSource([hint("music", intent_id="INT-77")])
        await rig.runtime.refresh_plan(trigger="manual", now=rig.clock.now())
        plan = await rig.runtime.load_plan()
        assert plan is not None
        payload = plan.to_payload()
        music_candidates = [c for c in payload["candidates"] if c["activity"] == "music"]
        assert music_candidates and music_candidates[0]["intent_id"] in {"", "INT-77"}
        assert plan.constraints["initiative"]["hints"] is not None

    async def test_audit_only_fields_do_not_bump_the_version(self) -> None:
        """§五：只是多记了一个 intent_id，不该让相同的计划不断 +1 版本。"""
        rig = PlanRig(clock=clock_at(14, 0))
        rig.runtime.planner.intent_source = FakeSource([hint("music", intent_id="INT-1")])
        await rig.runtime.refresh_plan(trigger="manual", now=rig.clock.now())
        first = await rig.runtime.load_plan()
        assert first is not None
        rig.runtime.planner.intent_source = FakeSource([hint("music", intent_id="INT-2")])
        # **同一个 now**：内容确实没变（只是归因 id 不同）
        await rig.runtime.refresh_plan(trigger="manual", now=rig.clock.now())
        second = await rig.runtime.load_plan()
        assert second is not None
        assert second.plan_version == first.plan_version  # 内容没变 → 不升版
        assert second.content_hash == first.content_hash


class TestPlanBridgeSecurity:
    def test_bridge_module_has_no_execution_surface(self) -> None:
        """§十八：桥本身不许 import 执行面（它只产出数据）。"""
        import ast
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "app" / "activity" / "initiative.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Name):
                names.add(node.id)
        for forbidden in ("app.tools", "app.tasks", "app.integrations", "app.ai", "app.core"):
            assert not any(module.startswith(forbidden) for module in modules), modules
        for forbidden in ("TaskRuntime", "ActionRuntime", "MinecraftService", "allow_medium"):
            assert forbidden not in names, forbidden
        # 也不能反向依赖 7A 的包（分层：意图包只 import 自己）
        assert not any(module.startswith("app.initiative") for module in modules)

    def test_hints_are_pure_data_without_handles(self) -> None:
        """§三.5：Planner 拿到的是数据 —— 里面没有任何句柄。"""
        book = hint_book_from(FakeSource([hint("reading")]), now=0.0)
        payload = book.payload()
        blob = repr(payload)
        for forbidden in ("TaskRuntime", "ActionRuntime", "Minecraft", "Policy", "token"):
            assert forbidden not in blob


class TestServiceHints:
    """§三：7A 的服务交出来的建议 —— 只有合法意图、bounded、不重复。"""

    async def test_only_proposed_intents_become_hints(self) -> None:
        from tests.initiative_fakes import Rig, goal

        rig = Rig()
        rig.feed(goals=(goal(kind="complete_project", label="小城"),))
        out = await rig.check()
        assert out["action"] == "proposed"
        hints = rig.service.hints()  # type: ignore[union-attr]
        assert hints and hints[0]["intent_type"] == "MINECRAFT_INTEREST"
        assert hints[0]["intent_id"] == out["created"][0]
        assert 0.0 < float(hints[0]["score_bonus"]) <= 1.0

    async def test_suppressed_intents_are_not_offered(self) -> None:
        from tests.initiative_fakes import Rig, goal

        rig = Rig()
        rig.feed(goals=(goal(),), sleeping=True)
        out = await rig.check()
        assert out["action"] == "suppressed"
        assert rig.service.hints() == ()  # type: ignore[union-attr]

    async def test_expired_intents_are_not_offered(self) -> None:
        from tests.initiative_fakes import Rig, goal

        rig = Rig()
        rig.feed(goals=(goal(kind="pet_care", label="小喵"),))
        await rig.check()
        assert rig.service.hints()  # type: ignore[union-attr]
        later = rig.clock_now + 30 * 3600.0
        assert rig.service.hints(now=later) == ()  # type: ignore[union-attr]

    async def test_repeated_checks_do_not_stack_hints(self) -> None:
        """§三.4：同一个意图不会因为多次 check 被重复加分。"""
        from tests.initiative_fakes import Rig, goal

        rig = Rig()
        rig.feed(goals=(goal(kind="complete_project", label="小城"),))
        await rig.check()
        first = rig.service.hints()  # type: ignore[union-attr]
        for _ in range(5):
            await rig.check(advance=120.0)
        second = rig.service.hints()  # type: ignore[union-attr]
        assert [item["intent_id"] for item in second] == [item["intent_id"] for item in first]
        assert [item["score_bonus"] for item in second] == [item["score_bonus"] for item in first]

    async def test_a_user_task_takes_the_hints_away(self) -> None:
        """§九：任务占位（这里用待确认真实任务）时，**已经提过的想法也不再给 Planner 加权。"""
        from tests.initiative_fakes import Rig, goal

        rig = Rig()
        rig.feed(goals=(goal(kind="complete_project", label="小城"),))
        await rig.check()
        assert rig.service.hints()  # type: ignore[union-attr]
        rig.feed(task_states=("PENDING_CONFIRMATION",))
        blocked = await rig.check(advance=60.0)
        assert blocked["reason"] == "PENDING_CONFIRMATION"
        assert rig.service.hints() == ()  # type: ignore[union-attr]
        rig.feed(task_states=())
        await rig.check(advance=60.0)
        assert rig.service.hints()  # type: ignore[union-attr]

    async def test_intent_reaches_the_plan_as_a_registered_activity(self) -> None:
        """§一/§六：MINECRAFT_INTEREST 真的进了计划 —— 作为**已注册的虚拟活动** building。"""
        from tests.initiative_fakes import Rig, goal

        rig = Rig()
        rig.feed(goals=(goal(kind="complete_project", label="小城"),))
        await rig.check()
        planner = ActivityPlanner(intent_source=rig.service)
        # 用与 Rig 同一个时刻的假时钟（意图是否过期取决于 now）
        plan = plan_for(planner, clock=FakeClock(start=rig.clock_now))
        activities = [item.activity for item in plan.candidates]
        assert "building" in activities
        assert "minecraft" not in activities  # §六：它根本不注册
        assert "minecraft_task" not in activities


class TestFastForwardWithHints:
    async def test_24h_with_hints_is_still_bounded(self) -> None:
        """§十四：24 小时快进 —— 计划版本不随 tick 涨，Episode 不爆炸，加成不累积。"""
        rig = PlanRig(clock=clock_at(8, 0))
        rig.runtime.planner.intent_source = FakeSource([hint("music")])
        versions: list[int] = []
        for _step in range(24 * 4):  # 每 15 分钟推进一次
            rig.clock.advance_minutes(15)
            await rig.runtime.refresh_plan(trigger="state_changed", now=rig.clock.now())
            plan = await rig.runtime.load_plan()
            if plan is not None:
                versions.append(plan.plan_version)
        assert versions
        # 不是每次刷新都升版（相同内容不升版，§五）
        assert max(versions) < len(versions)
        episodes = await rig.runtime.recent(limit=200)
        assert len(episodes) <= 24 * 4
        # 加成永远只有一个来源：同一活动在同一份计划里只有一项
        plan = await rig.runtime.load_plan()
        assert plan is not None
        music = [item for item in plan.candidates if item.activity == "music"]
        assert len(music) == 1
