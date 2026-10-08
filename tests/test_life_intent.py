"""Phase 7A §三-§八/§二十/§二十二/§二十六/§五十三/§六十一：LifeIntent 本体与候选生成。

矩阵：A（创建）/ B（去重）/ I（Goal）/ J（Memory）/ K（Social）/ V（确定性）。
"""

from __future__ import annotations

import pytest

from app.initiative import (
    ALLOWED_INTENT_TRANSITIONS,
    INTENT_TTL_SECONDS,
    InitiativeSource,
    LifeIntent,
    LifeIntentExecutionClass,
    LifeIntentStatus,
    LifeIntentType,
    execution_class_for,
    intent_fingerprint,
    intent_transition_allowed,
    propose_intents,
)
from app.initiative.candidates import IDLE_ACTIVITIES, LONG_IDLE_SECONDS, MemorySignal
from tests.initiative_fakes import T0, Rig, commitment, goal


class TestLifeIntentModel:
    def test_a_intent_creation_has_every_auditable_field(self) -> None:
        """A：一条意图必须能回答"是什么 / 为什么 / 优先级 / 何时 / 何时过期 / 允不允许"。"""
        intent = LifeIntent(
            intent_id="INT-1",
            character_id="罐头",
            intent_type=LifeIntentType.MINECRAFT_INTEREST,
            title="有点想回 Minecraft 看看",
            description="想起那个还没做完的项目",
            source=InitiativeSource.GOAL,
            priority=0.8,
            created_at=T0,
            expires_at=T0 + 3600,
            related_goal="G-1",
        )
        payload = intent.to_payload()
        for key in (
            "intent_type",
            "title",
            "description",
            "source",
            "priority",
            "created_at",
            "expires_at",
            "status",
            "suppression_reason",
            "resolution_reason",
            "confidence",
        ):
            assert key in payload
        assert intent.status is LifeIntentStatus.PROPOSED
        assert intent.open and not intent.terminal
        assert LifeIntent.from_payload(payload) == intent

    def test_no_executing_or_running_status_exists(self) -> None:
        """§四：``LifeIntent != Task`` —— 状态里不许有 EXECUTING / RUNNING。"""
        values = {item.value for item in LifeIntentStatus}
        assert values == {"PROPOSED", "SUPPRESSED", "EXPIRED", "CANCELLED", "RESOLVED"}
        assert "EXECUTING" not in values and "RUNNING" not in values

    def test_status_machine_is_append_only(self) -> None:
        """§二十四/§二十六：状态机没有"回到 PROPOSED"的路 —— 历史不可改写。"""
        assert intent_transition_allowed(LifeIntentStatus.PROPOSED, LifeIntentStatus.SUPPRESSED)
        assert intent_transition_allowed(LifeIntentStatus.PROPOSED, LifeIntentStatus.RESOLVED)
        assert intent_transition_allowed(LifeIntentStatus.SUPPRESSED, LifeIntentStatus.EXPIRED)
        assert not intent_transition_allowed(LifeIntentStatus.SUPPRESSED, LifeIntentStatus.PROPOSED)
        assert not intent_transition_allowed(LifeIntentStatus.RESOLVED, LifeIntentStatus.PROPOSED)
        # 终态没有出口（历史不可改写）
        terminals = (
            LifeIntentStatus.EXPIRED,
            LifeIntentStatus.CANCELLED,
            LifeIntentStatus.RESOLVED,
        )
        for terminal in terminals:
            assert ALLOWED_INTENT_TRANSITIONS[terminal] == frozenset()
        with pytest.raises(ValueError):
            LifeIntent(
                intent_id="x",
                character_id="c",
                intent_type=LifeIntentType.REST,
                title="t",
                status=LifeIntentStatus.EXPIRED,
            ).with_status(LifeIntentStatus.PROPOSED)

    def test_expiry_uses_expires_at(self) -> None:
        """§二十二：过了 ``expires_at`` 就是过期（绝不无限挂着 PROPOSED）。"""
        intent = LifeIntent(
            intent_id="INT-1",
            character_id="c",
            intent_type=LifeIntentType.SOCIAL,
            title="t",
            created_at=T0,
            expires_at=T0 + 60,
        )
        assert not intent.expired_at(T0 + 30)
        assert intent.expired_at(T0 + 60)
        assert INTENT_TTL_SECONDS[LifeIntentType.SOCIAL.value] == 4 * 3600.0

    def test_execution_class_is_virtual_only(self) -> None:
        """§四十二：AUTONOMOUS_TASK_CANDIDATE 只是枚举值 —— 7A 永远不给它赋值。"""
        assert LifeIntentExecutionClass.AUTONOMOUS_TASK_CANDIDATE.value
        for kind in LifeIntentType:
            assert execution_class_for(kind) is LifeIntentExecutionClass.VIRTUAL_ONLY

    def test_fingerprint_is_deterministic_and_bucketed(self) -> None:
        """§二十：指纹 = character|type|goal|activity|semantic key|time bucket。"""
        first = intent_fingerprint(
            character_id="c",
            intent_type=LifeIntentType.SOCIAL,
            semantic_key="social:x",
            bucket=1,
        )
        same = intent_fingerprint(
            character_id="c",
            intent_type=LifeIntentType.SOCIAL,
            semantic_key="social:x",
            bucket=1,
        )
        other_bucket = intent_fingerprint(
            character_id="c",
            intent_type=LifeIntentType.SOCIAL,
            semantic_key="social:x",
            bucket=2,
        )
        assert first == same and first != other_bucket


class TestCandidateGeneration:
    async def test_i_goal_produces_goal_progress_or_minecraft_interest(self) -> None:
        """I/§十六/§三十六：目标 → GOAL_PROGRESS（日常目标）或 MINECRAFT_INTEREST（MC 项目）。"""
        rig = Rig()
        minecraft = await rig.propose(goals=(goal(kind="complete_project", label="小城"),))
        assert [item.intent_type for item in minecraft] == [LifeIntentType.MINECRAFT_INTEREST]
        assert minecraft[0].source is InitiativeSource.GOAL
        assert minecraft[0].related_goal == "G-1"

        daily = await rig.propose(goals=(goal(kind="pet_care", label="小喵"),))
        assert LifeIntentType.GOAL_PROGRESS in [item.intent_type for item in daily]

    async def test_j_memory_is_only_supporting_context(self) -> None:
        """J/§十七：Memory 可以提高 Minecraft 兴趣，但 ``Memory != permission``。"""
        rig = Rig()
        out = await rig.propose(memories=(MemorySignal("上次挖橡木挖到半夜", domain="minecraft"),))
        assert [item.intent_type for item in out] == [LifeIntentType.MINECRAFT_INTEREST]
        assert out[0].source is InitiativeSource.MEMORY
        assert out[0].execution_class is LifeIntentExecutionClass.VIRTUAL_ONLY
        assert "virtual_interest" in out[0].tags
        # 没有任何"权限"标记：不该出现 requires_world（7A 的候选全是虚拟的）
        assert "requires_world" not in out[0].tags

    async def test_j_structured_domain_beats_keywords(self) -> None:
        """★真机发现：她的记忆写的是"橡树/原木"，关键词表对不上 —— 结构化 domain 才可靠。"""
        rig = Rig()
        out = await rig.propose(
            memories=(MemorySignal("罐头成功完成过找一棵橡树挖原木的任务", domain="minecraft"),)
        )
        assert [item.intent_type for item in out] == [LifeIntentType.MINECRAFT_INTEREST]
        assert out[0].origin == "memory:minecraft"
        # 没有任何结构化/关键词信号时，绝不自作多情
        quiet = await rig.propose(memories=(MemorySignal("今天买到了布丁"),))
        assert quiet == []

    async def test_k_social_topic_becomes_a_candidate_only(self) -> None:
        """K/§十八：社交话题只能形成候选意图（这里连消息发送器都不存在）。"""
        rig = Rig()
        out = await rig.propose(social_topics=("服务器新版本更新了",))
        assert [item.intent_type for item in out] == [LifeIntentType.SOCIAL]
        assert out[0].source is InitiativeSource.SOCIAL

    async def test_user_context_commitment_is_a_future_context(self) -> None:
        """§十九：用户说过的话只留下**未来上下文**，不是立即行动。"""
        rig = Rig()
        out = await rig.propose(commitments=(commitment(label="晚上一起玩 Minecraft"),))
        assert [item.source for item in out] == [InitiativeSource.USER_CONTEXT]
        assert out[0].related_player == ""
        assert "commitment" in out[0].tags

    async def test_long_idle_needs_two_hours(self) -> None:
        """§三十二：``free_time`` 一分钟不产生任何东西；两小时才问"要不要做点别的"。"""
        rig = Rig()
        assert await rig.propose(current_activity="idle", current_activity_elapsed=60.0) == []
        out = await rig.propose(
            current_activity="free_time",
            current_activity_elapsed=LONG_IDLE_SECONDS + 1,
            planner_candidates=("reading",),
        )
        assert [item.intent_type for item in out] == [LifeIntentType.ACTIVITY_CHANGE]
        assert out[0].related_activity == "reading"

    async def test_no_activity_continuation_intents_from_continuity_alone(self) -> None:
        """§五十三：连续性属于 6B/6C —— Initiative 不该每次 Episode 结束都说"继续 gaming"。"""
        rig = Rig()
        for name in ("gaming", "reading", "music"):
            out = await rig.propose(
                current_activity=name,
                current_activity_elapsed=600.0,
                recent_activities=(name, name, name),
            )
            assert LifeIntentType.ACTIVITY_CONTINUATION not in [item.intent_type for item in out]

    async def test_rest_needs_low_energy(self) -> None:
        """§二十八：低能量才有 REST 念头；有精神就不打扰。"""
        rig = Rig()
        assert await rig.propose(energy=0.9, current_activity="gaming") == []
        out = await rig.propose(energy=0.1, current_activity="gaming")
        assert [item.intent_type for item in out] == [LifeIntentType.REST]

    async def test_routine_due_becomes_a_personal_routine_intent(self) -> None:
        """§五：时段日常里"今天还没做过"的那一件。"""
        rig = Rig()
        out = await rig.propose(period="morning", routine_due=("reading",))
        assert [item.intent_type for item in out] == [LifeIntentType.PERSONAL_ROUTINE]

    async def test_v_same_context_gives_the_same_candidates(self) -> None:
        """V/§六十一：同样的世界 → 同样的候选、同样的优先级、同样的顺序。"""
        rig = Rig()
        first = [
            (item.intent_type.value, item.priority, item.fingerprint)
            for item in await rig.propose(
                goals=(goal(), goal("G-2", "pet_care", "小喵")),
                memories=(MemorySignal("小城还差一半"),),
                social_topics=("服务器",),
                current_activity="idle",
                current_activity_elapsed=LONG_IDLE_SECONDS + 60,
                planner_candidates=("reading",),
            )
        ]
        second = [
            (item.intent_type.value, item.priority, item.fingerprint)
            for item in await rig.propose()
        ]
        assert first == second and first

    def test_candidates_are_bounded(self) -> None:
        """§六十三：候选数量有上限（绝不全量扫描后无界生成）。"""
        from app.initiative.candidates import MAX_GOAL_SIGNALS, MAX_MEMORY_SIGNALS

        context = Rig().context
        empty = propose_intents(context)
        assert len(empty) == 0
        assert MAX_GOAL_SIGNALS <= 3 and MAX_MEMORY_SIGNALS <= 3


class TestDedupe:
    async def test_b_duplicate_suppressed_within_the_hour(self) -> None:
        """B/§二十：同一个小时里同一条想法只提一次（合并 / 忽略），不是十条。"""
        rig = Rig()
        rig.feed(goals=(goal(),))
        first = await rig.check()
        assert first["action"] == "proposed" and len(first["created"]) == 1
        second = await rig.check(advance=120.0)
        assert second["action"] in {"ignored", "idle"}
        assert second["created"] == []
        rows = await rig.service.recent(limit=10)  # type: ignore[union-attr]
        assert len([item for item in rows if item.status is LifeIntentStatus.PROPOSED]) == 1

    async def test_suppressed_intent_may_reappear_later(self) -> None:
        """§二十六：抑制不封死未来 —— 条件重新满足时是一条**新**意图（新 id）。"""
        rig = Rig()
        rig.feed(goals=(goal(),), sleeping=True)
        blocked = await rig.check()
        assert blocked["action"] == "suppressed"
        assert blocked["suppressed"], "抑制也必须留痕"

        rig.feed(sleeping=False)
        later = await rig.check(advance=3600.0 * 2)
        assert later["action"] == "proposed"
        assert later["created"] and later["created"][0] != blocked["suppressed"][0]

    async def test_suppressed_is_not_a_failure(self) -> None:
        """§二十五：SUPPRESSED 是状态、不是错误 —— 意图本身没有失败。"""
        rig = Rig()
        rig.feed(goals=(goal(),), sleeping=True)
        out = await rig.check()
        rows = await rig.service.recent(limit=10)  # type: ignore[union-attr]
        sleeping = [item for item in rows if item.suppression_reason == "SLEEPING"]
        assert sleeping, [(item.intent_type.value, item.suppression_reason) for item in rows]
        assert all(item.status is LifeIntentStatus.SUPPRESSED for item in rows)
        # 裁决层：候选还没落盘，所以对象状态仍是 PROPOSED —— 真正说话的是裁决与原因
        assert all(verdict["allowed"] is False for verdict in out["decision"]["verdicts"])
        assert all(
            item["suppression_reason"] == "SLEEPING" for item in out["decision"]["suppressed"]
        )


class TestIdleReference:
    def test_idle_activities_match_the_documented_set(self) -> None:
        assert set(IDLE_ACTIVITIES) == {"idle", "free_time", "resting"}
