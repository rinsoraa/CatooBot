"""Phase 7C §十三：TaskProposal 测试矩阵（1-9、14-16 + 接线）。

1 结构化提案 / 2 过期或抑制的意图不提案 / 3 去重 / 4 来源隔离 /
5 VERIFIED 身份映射 / 6 REVOKED·CONFLICT 解析不了目标 / 7 能力缺口 /
8 未知能力不假定可用 / 9 SAFE·LOW·MEDIUM 风险映射 /
14 重启不重复 / 15 过期不复活 / 16 allow_medium 保持 false。

10-13（不建 Task / 不调工具 / 不改 Episode / 不发主动 QQ）在
``tests/test_proposal_security.py``（源码级 + 运行时句柄级）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config.settings import AppConfig, DatabaseConfig
from app.database.database import Database
from app.initiative import InMemoryLifeIntentStore, LifeIntentStatus
from app.tasks.capabilities import capability_catalog, required_capabilities
from app.tasks.proposal import (
    ALLOWED_PROPOSAL_TRANSITIONS,
    TERMINAL_PROPOSAL_STATUSES,
    CapabilityGap,
    ProposalSource,
    ProposalStatus,
    TargetResolution,
    assess_proposal,
    build_proposal,
    proposal_fingerprint,
    proposal_time_bucket,
    proposal_transition_allowed,
)
from app.tasks.proposal_service import (
    TASK_PROPOSAL_EXECUTION_LAYER,
    TaskProposalService,
    intent_objective,
    objective_needs_player,
    should_record_user_proposal,
)
from app.tasks.proposal_store import (
    InMemoryTaskProposalStore,
    SqliteTaskProposalStore,
    day_text,
    proposal_id_for,
)
from tests.proposal_fakes import SERVER, T0, FakeConfig, FakeIdentity, ProposalRig, intent, link

# --------------------------------------------------------------------- 1


class TestStructuredProposal:
    """1：LifeIntent 产生一份结构化、可审计的提案。"""

    async def test_a_life_intent_becomes_a_structured_proposal(self) -> None:
        rig = ProposalRig()
        out = await rig.propose()
        assert out["action"] == "created"
        proposal = out["proposal"]
        assert proposal.source == ProposalSource.LIFE.value
        assert proposal.intent_id == "INT-0001"
        assert proposal.initiator == "罐头@deadbeef"
        assert proposal.objective == "想去 Minecraft 收集一点橡木 想起那片树林了"
        assert proposal.required_capabilities == (
            "minecraft_find_blocks",
            "minecraft_move_to",
            "minecraft_dig",
            "minecraft_pickup_item",
        )
        assert proposal.expected_effects, "预期影响必须写清楚（§三）"
        assert proposal.risk_summary["max_risk"] == "MEDIUM"
        assert proposal.risk_summary["source"] == ProposalSource.LIFE.value
        assert proposal.status == ProposalStatus.NEEDS_USER_APPROVAL.value
        assert proposal.feasibility == CapabilityGap.SUPPORTED.value
        assert proposal.created_at == T0
        assert proposal.expires_at == T0 + 6 * 3600
        assert proposal.fingerprint
        assert proposal.proposal_id.startswith("TP-")

    async def test_objective_uses_only_the_intents_own_text(self) -> None:
        """目标只由意图自己的文本组成（标题 + 描述 + 关联记忆），不编造、不调模型。"""
        item = intent(related_memory="那天在树林边上")
        assert intent_objective(item) == "想去 Minecraft 收集一点橡木 想起那片树林了 那天在树林边上"
        assert intent_objective(intent(description="", related_memory="")) == (
            "想去 Minecraft 收集一点橡木"
        )

    async def test_the_proposal_is_not_a_task(self) -> None:
        """§五：提案不是任务 —— 它没有步骤、没有确认、也没有任何"可执行"字段。"""
        rig = ProposalRig()
        proposal = (await rig.propose())["proposal"]
        assert not hasattr(proposal, "steps")
        assert not hasattr(proposal, "plan_hash")
        assert not hasattr(proposal, "confirm")
        assert proposal.status not in {"RUNNING", "EXECUTING", "CONFIRMED"}


# --------------------------------------------------------------------- 2


class TestIntentGating:
    """2：过期或被抑制的意图不产生有效提案。"""

    @pytest.mark.parametrize(
        "status",
        [
            LifeIntentStatus.SUPPRESSED,
            LifeIntentStatus.EXPIRED,
            LifeIntentStatus.CANCELLED,
            LifeIntentStatus.RESOLVED,
        ],
    )
    async def test_non_proposed_intents_produce_nothing(self, status: LifeIntentStatus) -> None:
        rig = ProposalRig()
        out = await rig.propose(status=status)
        assert out["action"] == "skipped"
        assert out["reason"] == f"intent_{status.value.lower()}"
        assert await rig.store.recent() == []

    async def test_expired_intent_produces_nothing(self) -> None:
        rig = ProposalRig()
        out = await rig.propose(expires_at=T0 - 1)
        assert out["action"] == "skipped"
        assert out["reason"] == "intent_expired"
        assert await rig.store.recent() == []

    async def test_empty_objective_produces_nothing(self) -> None:
        rig = ProposalRig()
        out = await rig.propose(title="", description="", related_memory="")
        assert out == {"action": "skipped", "reason": "empty_objective"}


# --------------------------------------------------------------------- 3 / 14


class TestDeduplication:
    """3/14：同一个时间桶里同一份提案只有一行（重启也不会多出第二条）。"""

    async def test_same_intent_twice_is_merged(self) -> None:
        rig = ProposalRig()
        first = await rig.propose()
        second = await rig.propose()
        assert (first["action"], second["action"]) == ("created", "merged")
        assert first["proposal"].proposal_id == second["proposal"].proposal_id
        assert len(await rig.store.recent()) == 1
        assert rig.service.merged_count == 1

    async def test_restart_does_not_create_a_duplicate(self) -> None:
        rig = ProposalRig()
        first = await rig.propose()
        # "重启"：同一份存储上换一个新的 service
        restarted = TaskProposalService(
            store=rig.store,
            config=FakeConfig(),
            identity=FakeIdentity(),
            minecraft_online=lambda: True,
            clock=lambda: T0,
        )
        again = await restarted.propose_from_intent(intent())
        assert again["action"] == "merged"
        assert again["proposal"].proposal_id == first["proposal"].proposal_id
        assert len(await rig.store.recent()) == 1

    async def test_different_objective_is_a_new_proposal(self) -> None:
        rig = ProposalRig()
        await rig.propose()
        other = await rig.propose(title="想去 Minecraft 看看海", intent_id="INT-0002")
        assert other["action"] == "created"
        assert len(await rig.store.recent()) == 2

    async def test_next_bucket_allows_a_new_proposal(self) -> None:
        rig = ProposalRig()
        await rig.propose(expires_at=T0 + 10 * 3600)
        rig.advance(3600)  # 下一个时间桶（§十：按桶去重，不是永久压制）
        again = await rig.propose(expires_at=T0 + 10 * 3600)
        assert again["action"] == "created"
        assert len(await rig.store.recent()) == 2

    def test_fingerprint_is_deterministic_and_bucketed(self) -> None:
        kwargs = {"source": ProposalSource.LIFE, "objective": "去收点橡木", "intent_id": "INT-1"}
        assert proposal_fingerprint(**kwargs, bucket=1) == proposal_fingerprint(**kwargs, bucket=1)
        assert proposal_fingerprint(**kwargs, bucket=1) != proposal_fingerprint(**kwargs, bucket=2)
        assert proposal_time_bucket(T0) == proposal_time_bucket(T0 + 10)
        # 目标**状态**不同 → 不同指纹（解析不到 / 被撤销 不该被当成同一份）
        unresolved = build_proposal(
            source=ProposalSource.LIFE,
            objective="跟着我",
            require_target=True,
            target=TargetResolution(),
            now=T0,
        )
        revoked = build_proposal(
            source=ProposalSource.LIFE,
            objective="跟着我",
            require_target=True,
            target=TargetResolution(status="REVOKED", reason="revoked"),
            now=T0,
        )
        assert unresolved.fingerprint != revoked.fingerprint


# --------------------------------------------------------------------- 4


class TestSourceIsolation:
    """4：用户请求与自主意图保持不同来源。"""

    async def test_life_and_user_proposals_stay_distinct(self) -> None:
        rig = ProposalRig(identity=FakeIdentity([link()]))
        life = (await rig.propose())["proposal"]
        user = (await rig.request("跟着我"))["proposal"]
        assert life.source == ProposalSource.LIFE.value
        assert user.source == ProposalSource.USER.value
        assert life.intent_id == "INT-0001" and user.intent_id == ""
        assert user.initiator == "2731431246"
        assert life.initiator == "罐头@deadbeef"
        assert {item.source for item in await rig.store.recent()} == {"LIFE", "USER"}

    async def test_life_proposal_gets_no_more_permission_than_user(self) -> None:
        """§九：即使来源是 LIFE，也不能获得比 USER 更高的权限。"""
        rig = ProposalRig(identity=FakeIdentity([link()]))
        life = (await rig.propose(title="跟着我", description="", related_memory=""))["proposal"]
        user = (await rig.request("跟着我"))["proposal"]
        assert life.status == user.status
        assert life.risk_summary["max_risk"] == user.risk_summary["max_risk"]
        assert (
            life.risk_summary["would_require_confirmation"]
            is (user.risk_summary["would_require_confirmation"])
        )

    async def test_intent_from_another_character_is_still_life_source(self) -> None:
        rig = ProposalRig()
        out = await rig.service.propose_from_intent(intent(intent_id="INT-X"))
        assert out["proposal"].source == ProposalSource.LIFE.value


# --------------------------------------------------------------------- 5 / 6


class TestIdentityResolution:
    """5/6：只认 VERIFIED 绑定；撤销 / 冲突 / 缺失一律不猜目标。"""

    async def test_verified_link_maps_the_player(self) -> None:
        rig = ProposalRig(identity=FakeIdentity([link("f" * 32, username="Rinsora")]))
        proposal = (await rig.request("跟着我"))["proposal"]
        assert proposal.target["status"] == "VERIFIED"
        assert proposal.target["player_name"] == "Rinsora"
        assert proposal.target["server_id"] == SERVER
        assert proposal.status == ProposalStatus.READY_FOR_FUTURE_EXECUTION.value
        assert proposal.feasibility == CapabilityGap.SUPPORTED.value

    async def test_view_never_exposes_the_full_uuid(self) -> None:
        """§十二：只给后四位（和 QQ / WebUI 其它地方一致）。"""
        rig = ProposalRig(identity=FakeIdentity([link("abcdef" + "0" * 26)]))
        proposal = (await rig.request("跟着我"))["proposal"]
        view = rig.service.to_view(proposal)
        assert view["target"]["uuid_suffix"] == "0000"
        assert "abcdef" not in str(view["target"])

    @pytest.mark.parametrize("status", ["REVOKED", "CONFLICT"])
    async def test_revoked_or_conflicting_identity_is_rejected(self, status: str) -> None:
        rig = ProposalRig(identity=FakeIdentity([link("a" * 32, status=status)]))
        proposal = (await rig.request("跟着我"))["proposal"]
        assert proposal.status == ProposalStatus.REJECTED.value
        assert proposal.reason == f"target_{status.lower()}"
        assert proposal.feasibility == CapabilityGap.UNSUPPORTED.value
        assert "NEEDS_USER_INPUT" in proposal.suggestions

    async def test_missing_identity_asks_for_more_information(self) -> None:
        rig = ProposalRig(identity=FakeIdentity([]))
        proposal = (await rig.request("跟着我"))["proposal"]
        assert proposal.status == ProposalStatus.NEEDS_MORE_INFORMATION.value
        assert proposal.reason == "target_unresolved"
        assert proposal.target["status"] == "MISSING"

    async def test_two_verified_players_are_never_merged(self) -> None:
        """§八：绝不因为同名/多人而把两名真实用户合并成一个目标。"""
        rig = ProposalRig(
            identity=FakeIdentity(
                [link("a" * 32, username="Rinsora"), link("b" * 32, username="Rinsora")]
            )
        )
        proposal = (await rig.request("跟着我"))["proposal"]
        assert proposal.target["status"] == "CONFLICT"
        assert proposal.status == ProposalStatus.REJECTED.value

    async def test_server_mismatch_does_not_guess(self) -> None:
        rig = ProposalRig(identity=FakeIdentity([link("a" * 32, server_id="mc-其它")]))
        proposal = (await rig.request("跟着我", server_id=SERVER))["proposal"]
        assert proposal.target["status"] == "MISSING"
        assert proposal.target["reason"] == "server_mismatch"

    async def test_identity_layer_failure_degrades_without_guessing(self) -> None:
        rig = ProposalRig(identity=FakeIdentity([link()], fails=True))
        proposal = (await rig.request("跟着我"))["proposal"]
        assert proposal.target["status"] == "MISSING"
        assert proposal.target["reason"] == "identity_unavailable"
        assert rig.service.degraded_reason == "RuntimeError"

    def test_player_target_keywords_are_deterministic(self) -> None:
        assert objective_needs_player("跟着我") is True
        assert objective_needs_player("去收点橡木") is False
        # 不需要指定玩家的目标不会被身份层拦下
        assert objective_needs_player("去看看周围有什么") is False


# --------------------------------------------------------------------- 7 / 8


class TestCapabilityGaps:
    """7/8：缺失能力必须如实标记；未知的能力绝不假定可用。"""

    async def test_design_heavy_goal_is_unknown_not_supported(self) -> None:
        """§六：有工具 ≠ 目标可完成（刷铁机要先有方案）。"""
        rig = ProposalRig()
        out = await rig.propose(
            title="建造一台刷铁机", description="想让它自动出铁", related_memory=""
        )
        proposal = out["proposal"]
        assert proposal.status == ProposalStatus.NEEDS_MORE_INFORMATION.value
        assert proposal.feasibility == CapabilityGap.UNKNOWN.value
        assert proposal.suggestions == ("NEEDS_USER_INPUT",)
        assert "design_plan" in proposal.required_capabilities

    def test_unregistered_capability_is_unsupported(self) -> None:
        """目录里没有的能力 → UNSUPPORTED（**不是**"大概可以"），并建议补工具（§七）。"""
        book = capability_catalog(minecraft_online=True)
        book.pop("minecraft_dig")
        assessment = assess_proposal(
            objective="去收点橡木", source=ProposalSource.LIFE, catalog=book
        )
        gaps = {item.capability_id: item.gap for item in assessment.requirements}
        assert gaps["minecraft_dig"] == CapabilityGap.UNSUPPORTED.value
        assert assessment.status == ProposalStatus.NEEDS_MORE_INFORMATION.value
        assert "NEEDS_NEW_TOOL" in assessment.suggestions

    async def test_offline_world_capabilities_are_partial(self) -> None:
        """离线时世界类能力不可用 → PARTIALLY_SUPPORTED + 需要世界观测（§六/§七）。"""
        rig = ProposalRig(online=False)
        proposal = (await rig.propose())["proposal"]
        assert proposal.status == ProposalStatus.NEEDS_MORE_INFORMATION.value
        assert proposal.feasibility == CapabilityGap.PARTIALLY_SUPPORTED.value
        gaps = {item.capability_id: item.gap for item in proposal.requirements}
        assert gaps["minecraft_dig"] == CapabilityGap.PARTIALLY_SUPPORTED.value
        assert "NEEDS_WORLD_OBSERVATION" in proposal.suggestions

    async def test_vague_objective_is_unknown(self) -> None:
        rig = ProposalRig()
        proposal = (await rig.propose(title="随便做点什么吧", description="", related_memory=""))[
            "proposal"
        ]
        assert proposal.feasibility == CapabilityGap.UNKNOWN.value
        assert proposal.reason == "capability_gap"
        assert proposal.status == ProposalStatus.NEEDS_MORE_INFORMATION.value

    async def test_catalog_keeps_the_existing_risk_table(self) -> None:
        """§九：能力目录从既有 19 个原子工具提取，风险分类**一个字都没改**。"""
        from app.integrations.minecraft.agent import ACTION_RISK

        catalog = capability_catalog(minecraft_online=True)
        assert len(catalog) == len(ACTION_RISK) == 19
        for name, risk in ACTION_RISK.items():
            assert catalog[name].risk_class == risk
        assert all(item.available for item in catalog.values())
        offline = capability_catalog(minecraft_online=False)
        assert not any(item.available for item in offline.values())

    async def test_unknown_never_upgrades_itself(self) -> None:
        """§七：UNKNOWN 不会因为"跑过一遍"就变成 SUPPORTED。"""
        rig = ProposalRig()
        first = (await rig.propose(title="建造一台刷怪塔", description="", related_memory=""))[
            "proposal"
        ]
        assert first.feasibility == CapabilityGap.UNKNOWN.value
        again = (await rig.propose(title="建造一台刷怪塔", description="", related_memory=""))[
            "proposal"
        ]
        assert again.feasibility == CapabilityGap.UNKNOWN.value
        assert again.status == first.status


# --------------------------------------------------------------------- 9


class TestRiskMapping:
    """9：SAFE / LOW / MEDIUM 风险正确映射到状态（§九）。"""

    @pytest.mark.parametrize(
        ("objective", "risk", "status"),
        [
            ("去看看周围有什么", "SAFE", ProposalStatus.READY_FOR_FUTURE_EXECUTION.value),
            ("跟着我", "LOW", ProposalStatus.READY_FOR_FUTURE_EXECUTION.value),
            ("去收点橡木", "MEDIUM", ProposalStatus.NEEDS_USER_APPROVAL.value),
        ],
    )
    def test_risk_class_maps_to_status(self, objective: str, risk: str, status: str) -> None:
        assessment = assess_proposal(
            objective=objective,
            source=ProposalSource.LIFE,
            catalog=capability_catalog(minecraft_online=True),
        )
        assert assessment.risk_summary["max_risk"] == risk
        assert assessment.status == status
        assert assessment.risk_summary["would_require_confirmation"] is (risk == "MEDIUM")

    def test_required_capabilities_mapping_is_deterministic(self) -> None:
        assert required_capabilities("去收点橡木") == (
            "minecraft_find_blocks",
            "minecraft_move_to",
            "minecraft_dig",
            "minecraft_pickup_item",
        )
        assert required_capabilities("跟着我") == ("minecraft_follow_player",)

    async def test_medium_proposal_says_it_would_need_confirmation(self) -> None:
        """§九：只是**描述** —— 真执行时仍然要走既有确认链（这里不确认、不代签）。"""
        rig = ProposalRig()
        proposal = (await rig.propose())["proposal"]
        assert proposal.risk_summary["would_require_confirmation"] is True
        assert proposal.status == ProposalStatus.NEEDS_USER_APPROVAL.value
        assert not hasattr(rig.service, "confirm")


# --------------------------------------------------------------------- 15 / 16


class TestExpiryAndTerminalStates:
    """15/16：过期提案是终态，重启也不会复活；allow_medium 一个字没改。"""

    async def test_expired_proposal_is_terminal(self) -> None:
        rig = ProposalRig()
        created = (await rig.propose())["proposal"]
        rig.advance(6 * 3600 + 1)
        expired = await rig.service.expire_due()
        assert [item.proposal_id for item in expired] == [created.proposal_id]
        assert expired[0].status == ProposalStatus.EXPIRED.value
        assert expired[0].reason == "ttl_expired"

    async def test_recover_never_revives_a_terminal_proposal(self) -> None:
        rig = ProposalRig()
        created = (await rig.propose())["proposal"]
        rig.advance(6 * 3600 + 1)
        await rig.service.expire_due()
        summary = await rig.service.recover()
        assert summary["action"] == "ok"
        assert summary["open"] == 0
        still = await rig.store.get(created.proposal_id)
        assert still is not None and still.status == ProposalStatus.EXPIRED.value

    async def test_terminal_proposal_is_not_reopened(self) -> None:
        """§十：同一个时间桶里，终态提案不会被"再提一次"复活成开着的新提案。"""
        rig = ProposalRig()
        created = (await rig.propose())["proposal"]
        await rig.service.cancel(created.proposal_id, reason="改主意了")
        again = await rig.propose()
        assert again["action"] == "merged"
        assert again["proposal"].proposal_id == created.proposal_id
        assert again["proposal"].status == ProposalStatus.CANCELLED.value
        assert again["proposal"].open is False

    async def test_expired_row_is_never_flipped_back(self) -> None:
        """§十：过期是终态 —— 晚一点再提只能是**新的一行**，旧的那行永远不动。"""
        rig = ProposalRig()
        alive = T0 + 24 * 3600  # 意图比提案活得久：排除"意图过期"这条无关路径
        created = (await rig.propose(expires_at=alive))["proposal"]
        rig.advance(6 * 3600 + 1)
        await rig.service.expire_due()
        later = await rig.propose(expires_at=alive)  # 下一个时间桶 → 新的一行
        assert later["action"] == "created"
        assert later["proposal"].proposal_id != created.proposal_id
        old = await rig.store.get(created.proposal_id)
        assert old is not None
        assert old.status == ProposalStatus.EXPIRED.value
        assert old.open is False

    async def test_rejected_and_cancelled_are_terminal(self) -> None:
        rig = ProposalRig()
        created = (await rig.propose())["proposal"]
        rejected = await rig.service.reject(created.proposal_id, reason="不行")
        assert rejected is not None and rejected.status == ProposalStatus.REJECTED.value
        cancelled = await rig.service.cancel(created.proposal_id)
        assert cancelled is None, "终态没有出口"
        assert ProposalStatus.REJECTED in TERMINAL_PROPOSAL_STATUSES
        assert proposal_transition_allowed(ProposalStatus.REJECTED, ProposalStatus.CANCELLED) is (
            False
        )
        assert ALLOWED_PROPOSAL_TRANSITIONS[ProposalStatus.EXPIRED] == frozenset()

    async def test_allow_medium_is_still_false(self) -> None:
        """§九/§十五：本阶段不修改 allow_medium。"""
        config = AppConfig()
        assert config.minecraft.agent.tools.allow_medium is False
        assert config.minecraft.agent.tools.allow_high is False
        assert config.minecraft.agent.tools.allow_destructive is False


# --------------------------------------------------------------------- 只读视图 / 审计


class TestReadOnlyView:
    async def test_view_carries_every_field_the_spec_lists(self) -> None:
        """§十二：Proposal ID / Source / Objective / Related Intent / Target /
        Required Capabilities / Feasibility / Capability Gaps / Risk Summary / Status / Expiry。"""
        rig = ProposalRig(online=False)
        await rig.propose()
        view = await rig.service.view()
        row = view["proposals"][0]
        for key in (
            "proposal_id",
            "source",
            "objective",
            "intent_id",
            "target",
            "required_capabilities",
            "capability_gaps",
            "feasibility",
            "risk_summary",
            "status",
            "created_at",
            "expires_at",
        ):
            assert key in row, key
        assert row["capability_gaps"][0]["gap"] == CapabilityGap.PARTIALLY_SUPPORTED.value
        assert view["minecraft_online"] is False
        assert view["open"] == 1

    async def test_view_has_no_action_verbs(self) -> None:
        rig = ProposalRig()
        await rig.propose()
        view = await rig.service.view()
        blob = str(view).lower()
        # 注意：``would_require_confirmation`` 是**描述**（§九 要求如实写出来），
        # 所以这里禁的是"执行类动词"，不是"confirmation"这个词本身。
        for verb in ("execute", "start_task", "run_task", "approve", "delete_task"):
            assert verb not in blob, verb

    async def test_events_are_append_only_and_named_proposal_star(self) -> None:
        rig = ProposalRig()
        await rig.propose()
        await rig.propose()  # 去重
        await rig.service.cancel("TP-不存在")  # 不存在 → 不产生事件
        events = await rig.store.recent_events(limit=10)
        names = [item["type"] for item in events]
        assert names[-1] == "proposal.created"
        assert "proposal.deduplicated" in names
        assert all(name.startswith("proposal.") for name in names)

    async def test_proposal_event_has_no_hidden_reasoning(self) -> None:
        rig = ProposalRig()
        await rig.propose()
        event = (await rig.store.recent_events(limit=1))[0]
        detail = str(event["detail"])
        for forbidden in ("prompt", "thinking", "reasoning", "chain_of_thought"):
            assert forbidden not in detail


class TestExecutionLayer:
    """§一/§十二：这一层的执行面在只读视图与模块常量上都说"没有"。"""

    def test_execution_layer_constant_is_none(self) -> None:
        assert TASK_PROPOSAL_EXECUTION_LAYER == "NONE"


# --------------------------------------------------------------------- 存储实现


class TestSqliteProposalStore:
    """生产实现的两条硬性质：指纹唯一 + 终态不可覆盖（真库，临时目录）。"""

    async def test_fingerprint_unique_and_terminal_state_frozen(self, tmp_path: Path) -> None:
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'proposals.db'}"))
        await database.connect()
        try:
            store = SqliteTaskProposalStore(database)
            service = TaskProposalService(
                store=store,
                config=FakeConfig(),
                identity=FakeIdentity(),
                minecraft_online=lambda: True,
                clock=lambda: T0,
            )
            first = (await service.propose_from_intent(intent()))["proposal"]
            assert first.proposal_id == proposal_id_for(day_text(T0), 1)
            again = (await service.propose_from_intent(intent()))["proposal"]
            assert again.proposal_id == first.proposal_id
            rows = await database.fetchall("SELECT proposal_id FROM task_proposals")
            assert len(rows) == 1

            cancelled = await service.cancel(first.proposal_id, reason="不做了")
            assert cancelled is not None
            assert await store.update_status(first.proposal_id, ProposalStatus.EXPIRED) is None
            assert (await store.get(first.proposal_id)).status == ProposalStatus.CANCELLED.value

            events = await database.fetchall(
                "SELECT type, scope_key FROM behavior_events WHERE scope_key = 'proposal'"
            )
            assert {row["type"] for row in events} == {
                "proposal.created",
                "proposal.deduplicated",
                "proposal.cancelled",
            }
        finally:
            await database.close()

    async def test_broken_payload_is_not_guessed(self, tmp_path: Path) -> None:
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'broken.db'}"))
        await database.connect()
        try:
            await database.execute(
                "INSERT INTO task_proposals (proposal_id, source, fingerprint, payload)"
                " VALUES ('TP-x', 'LIFE', 'f', '{not json')"
            )
            store = SqliteTaskProposalStore(database)
            assert await store.get("TP-x") is None
            assert await store.recent() == []
        finally:
            await database.close()


# --------------------------------------------------------------------- 接线


class TestSchedulerPass:
    """§四：7A 的 ``check()`` 交出来的是 **intent_id 列表** —— 提案层必须先换成本体。"""

    def _scheduler(self, bot: Any) -> Any:
        from app.behavior.scheduler import BehaviorScheduler

        class FakeScheduler(BehaviorScheduler):
            def __init__(self) -> None:  # 绕开真实调度器的装配
                self._bot = bot
                self._log = _NullLog()

        return FakeScheduler()

    async def test_ids_are_resolved_through_the_store(self) -> None:
        """真契约：给 id，先回查成 LifeIntent，再交给提案层。"""
        life_store = InMemoryLifeIntentStore()
        stored, _ = await life_store.create(intent())
        service = TaskProposalService(
            store=InMemoryTaskProposalStore(),
            config=FakeConfig(),
            identity=FakeIdentity(),
            minecraft_online=lambda: True,
            clock=lambda: T0,
        )
        seen: list[Any] = []

        async def spy(intents: Any, **kwargs: Any) -> dict[str, Any]:
            seen.append([getattr(item, "intent_id", "") for item in intents])
            return {"action": "created", "created": [], "merged": [], "skipped": []}

        service.propose_open_intents = spy  # type: ignore[method-assign]
        bot = type(
            "B",
            (),
            {
                "proposals": service,
                "initiative": type("I", (), {"store": life_store, "last_created": ()})(),
            },
        )()
        await self._scheduler(bot)._proposal_pass([stored.intent_id])
        assert seen == [[stored.intent_id]]

    async def test_last_created_is_preferred_over_a_store_round_trip(self) -> None:
        """7A 已经把本体留在 ``last_created`` 上时，不必再查库。"""
        item = intent()
        assert item.intent_id == "INT-0001"
        seen: list[Any] = []

        class Service:
            enabled = True

            @staticmethod
            async def propose_open_intents(intents: Any, **kwargs: Any) -> dict[str, Any]:
                seen.extend(list(intents))
                return {"action": "created", "created": [], "merged": [], "skipped": []}

        bot = type(
            "B",
            (),
            {
                "proposals": Service(),
                "initiative": type(
                    "I",
                    (),
                    {
                        "store": None,  # 没有 store，只靠 last_created
                        "last_created": (item,),
                    },
                )(),
            },
        )()
        await self._scheduler(bot)._proposal_pass(["INT-0001"])
        assert [getattr(row, "intent_id", "") for row in seen] == ["INT-0001"]

    async def test_empty_input_and_unknown_ids_are_noops(self) -> None:
        rig = ProposalRig()
        life_store = InMemoryLifeIntentStore()
        service = TaskProposalService(
            store=rig.store, config=FakeConfig(), identity=FakeIdentity(), clock=lambda: T0
        )
        bot = type(
            "B",
            (),
            {
                "proposals": service,
                "initiative": type("I", (), {"store": life_store, "last_created": ()})(),
            },
        )()
        scheduler = self._scheduler(bot)
        await scheduler._proposal_pass([])
        await scheduler._proposal_pass(["INT-不存在"])
        assert await rig.store.recent() == [], "查不到的 id 绝不猜、也不写提案"

    async def test_proposal_pass_swallows_failures(self) -> None:
        class FakeBot:
            class proposals:
                enabled = True

                @staticmethod
                async def propose_open_intents(intents: Any, **kwargs: Any) -> Any:
                    raise RuntimeError("db down")

            class initiative:
                store = None
                last_created = ()

        await self._scheduler(FakeBot())._proposal_pass([intent()])  # 不抛就是通过


class _NullLog:
    def debug(self, *args: Any, **kwargs: Any) -> None: ...
    def info(self, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, *args: Any, **kwargs: Any) -> None: ...
    def exception(self, *args: Any, **kwargs: Any) -> None: ...


class TestQQEntryRecording:
    """§四/§八：USER 提案只是旁路记账（窄规则），且失败绝不冒泡。"""

    def _entry(self, service: Any) -> Any:
        from app.tasks.qq_entry import QQTaskEntry

        entry = QQTaskEntry.__new__(QQTaskEntry)
        entry.bot = type("B", (), {"proposals": service})()
        entry._log = _NullLog()
        return entry

    class _Identity:
        user_id = "2731431246"
        session_id = "private:2731431246"

    async def test_records_a_created_task_and_a_player_directed_chat(self) -> None:
        """两条命中：真的建了任务，或这句话明确在指某个玩家（§八 的「跟着我」）。"""
        recorded: list[dict[str, Any]] = []

        class Service:
            enabled = True

            @staticmethod
            async def record_user_request(**kwargs: Any) -> Any:
                recorded.append(kwargs)
                return {}

        entry = self._entry(Service())
        entry._server_id = lambda: SERVER  # type: ignore[method-assign]
        await entry._record_proposal(
            self._Identity(), "去收点橡木", type("O", (), {"action": "created", "task_id": "T-1"})()
        )
        await entry._record_proposal(
            self._Identity(), "跟着我", type("O", (), {"action": "chat", "task_id": ""})()
        )
        assert [item["objective"] for item in recorded] == ["去收点橡木", "跟着我"]
        assert recorded[0]["server_id"] == SERVER
        assert recorded[0]["task_id"] == "T-1"
        assert recorded[1]["task_id"] == ""

    async def test_ordinary_chat_and_questions_leave_no_proposal(self) -> None:
        """§十四 Real QQ：普通闲聊与提问**不会**被记成提案（更不会建任务）。"""
        recorded: list[dict[str, Any]] = []

        class Service:
            enabled = True

            @staticmethod
            async def record_user_request(**kwargs: Any) -> Any:
                recorded.append(kwargs)
                return {}

        entry = self._entry(Service())
        entry._server_id = lambda: SERVER  # type: ignore[method-assign]
        for text in ("你好呀", "你在干嘛", "你想收集一些橡木吗？", "今天天气不错"):
            await entry._record_proposal(
                self._Identity(), text, type("O", (), {"action": "chat", "task_id": ""})()
            )
        assert recorded == []

    def test_the_recording_rule_is_narrow_and_deterministic(self) -> None:
        assert should_record_user_proposal("去砍两块橡木回来", "created") is True
        assert should_record_user_proposal("跟着我", "chat") is True
        assert should_record_user_proposal("跟随过来", "") is True
        assert should_record_user_proposal("你好呀", "chat") is False
        assert should_record_user_proposal("你想收集一些橡木吗？", "chat") is False
        assert should_record_user_proposal("", "") is False

    async def test_recording_failure_never_breaks_the_chat_or_task_flow(self) -> None:
        class Service:
            enabled = True

            @staticmethod
            async def record_user_request(**kwargs: Any) -> Any:
                raise RuntimeError("identity down")

        entry = self._entry(Service())
        entry._server_id = lambda: ""
        await entry._record_proposal(
            self._Identity(), "跟着我", type("O", (), {"action": "created", "task_id": "T-2"})()
        )  # 不抛就是通过

    async def test_no_service_means_no_recording(self) -> None:
        entry = self._entry(None)
        await entry._record_proposal(
            self._Identity(), "跟着我", type("O", (), {"action": "created", "task_id": "T-3"})()
        )


class TestInMemoryStoreParity:
    async def test_in_memory_store_matches_sqlite_semantics(self) -> None:
        store = InMemoryTaskProposalStore()
        first, created = await store.create(
            build_proposal(source=ProposalSource.LIFE, objective="去收点橡木", now=T0)
        )
        assert created is True and first.proposal_id
        second, created_again = await store.create(
            build_proposal(source=ProposalSource.LIFE, objective="去收点橡木", now=T0)
        )
        assert created_again is False and second.proposal_id == first.proposal_id
        assert (
            await store.update_status(first.proposal_id, ProposalStatus.EXPIRED, reason="ttl")
        ) is not None
        assert await store.update_status(first.proposal_id, ProposalStatus.CANCELLED) is None
        assert [item.proposal_id for item in await store.open_proposals()] == []


# --------------------------------------------------------------------- HTTP 只读端点


class TestIdentityWiring:
    """§八：装配必须把**真正的** IdentityStore 传给提案服务。"""

    def test_bot_wiring_reads_the_identity_store(self) -> None:
        """真机门禁抓到的坑：``bot.minecraft_identity`` 是 QQ 绑定**命令处理器**（没有
        ``links_for``），真正的 store 在记忆桥的 ``identities`` 上。这条断言钉死装配点。"""
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "app" / "core" / "bot.py").read_text(
            encoding="utf-8"
        )
        assert 'getattr(self, "minecraft_memory", None), "identities"' in source
        # 不能再直接把命令处理器当 store 传
        assert 'identity = getattr(self, "minecraft_identity", None)' not in source

    async def test_a_command_handler_shaped_identity_degrades_loudly(self) -> None:
        """就算接错（给了没有 links_for 的对象），也必须 MISSING + 如实的 reason。"""
        rig = ProposalRig()
        rig.service.identity = type("Cmd", (), {})()  # 没有 links_for
        proposal = (await rig.request("跟着我"))["proposal"]
        assert proposal.target["status"] == "MISSING"
        assert proposal.target["reason"] == "identity_unavailable"


class TestProposalsEndpoint:
    """§十二 的 HTTP 侧：只读、带 execution_layer=NONE、失败也不变 5xx。"""

    def _api(self, proposals: Any) -> Any:
        from app.web.api.domain import DomainApiRoutes

        api = DomainApiRoutes.__new__(DomainApiRoutes)
        api._bot = type("B", (), {"proposals": proposals})()
        return api

    async def _payload(self, api: Any) -> dict[str, Any]:
        import json

        response = await api._v1_world_proposals(None)
        return json.loads(response.text)["data"]

    async def test_disabled_shape_when_service_is_absent(self) -> None:
        data = await self._payload(self._api(None))
        assert data["enabled"] is False
        assert data["execution_layer"] == "NONE"
        assert data["proposals"] == []

    async def test_view_shape_when_service_exists(self) -> None:
        rig = ProposalRig()
        await rig.propose()
        data = await self._payload(self._api(rig.service))
        assert data["execution_layer"] == "NONE"
        assert data["enabled"] is True
        assert data["proposals"][0]["objective"]
        assert data["proposals"][0]["intent_id"] == "INT-0001"

    async def test_broken_view_becomes_a_clean_api_error(self) -> None:
        class Broken:
            enabled = True

            @staticmethod
            async def view() -> Any:
                raise RuntimeError("db down")

        from app.web.api_errors import ApiError

        api = self._api(Broken())
        with pytest.raises(ApiError) as caught:
            await api._v1_world_proposals(None)
        assert caught.value.code == "proposal.view"
        assert "RuntimeError" in caught.value.message
