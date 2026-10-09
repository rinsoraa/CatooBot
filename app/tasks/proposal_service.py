"""Phase 7C §四/§五/§八/§十/§十一：TaskProposal 的编排层。

```text
LifeIntent ──┐
             ├──→ TaskProposal  （**到此为止**：本阶段执行层恒为 NONE）
QQ 用户请求 ─┘
```

这一层是 7C 唯一"有副作用"的地方，而它的副作用只有两种：

1. 写 ``task_proposals`` / ``behavior_events``（提案与审计）；
2. 发 ``proposal.*`` 事件（state update，绝不允许因此调模型 / 发 QQ / 碰世界，§十二）。

**没有执行入口**（§一/§五/§十五）：这里既没有 TaskRuntime / ActionRuntime /
MinecraftService 的动作面 / Policy / ConfirmationStore / QQ 发送器 —— 也没有
``create_task`` / ``confirm_and_start`` 可调。见 ``tests/test_proposal_security.py``。

两条来源共享同一套 schema、能力目录与可行性检查，但授权规则不同（§四）：
``LIFE`` 只能停在提案；``USER`` 仍然走既有 5A/5B 任务链（本模块只是**旁路记账**，
不参与那条链的决策）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.tasks.capabilities import Capability, capability_catalog
from app.tasks.proposal import (
    DEFAULT_PROPOSAL_TTL_SECONDS,
    IDENTITY_CONFLICT,
    IDENTITY_MISSING,
    IDENTITY_REVOKED,
    IDENTITY_VERIFIED,
    NO_TARGET,
    CapabilityGap,
    ProposalSource,
    ProposalStatus,
    TargetResolution,
    TaskProposal,
    build_proposal,
)
from app.tasks.proposal_events import (
    PROPOSAL_CANCELLED,
    PROPOSAL_CREATED,
    PROPOSAL_DEDUPLICATED,
    PROPOSAL_EXPIRED,
    PROPOSAL_REJECTED,
    ProposalEventPublisher,
)

#: §四：USER 路径上"这句话是在指一个玩家"的关键词（确定性；绝不靠模型猜目标）
PLAYER_TARGET_KEYWORDS: tuple[str, ...] = (
    "跟着",
    "跟住",
    "跟随",
    "跟我",
    "跟过来",
    "来我身边",
    "带我去",
    "带过来",
    "带过来找我",
    "来找我",
    "和我一起",
)

#: 一次 pass 最多处理多少条新意图（bounded，§十；绝不因为"想得多"就不停写盘）
MAX_PROPOSALS_PER_PASS = 3
#: 重启恢复读回多少条开着的提案（bounded）
RECOVER_LIMIT = 50
#: WebUI / 上下文只读展示条数
VIEW_RECENT_LIMIT = 10

#: §一/§五：这一层的执行面 —— 恒为 ``NONE``（提案永远不会自己获得执行权）。
#: 它与 7A 的 "执行层 = NONE" 是同一个承诺，WebUI 也只读地展示这个事实。
TASK_PROPOSAL_EXECUTION_LAYER = "NONE"


def objective_needs_player(objective: str) -> bool:
    """这句话是不是在指某个具体玩家（§八：需要可信身份桥才能解析目标）。"""
    text = str(objective or "").lower()
    return any(keyword.lower() in text for keyword in PLAYER_TARGET_KEYWORDS)


def should_record_user_proposal(text: str, action: str) -> bool:
    """这次 QQ 消息该不该记成一份 USER 提案（§四；**窄**且确定性）。

    两条命中之一：

    1. 既有任务链**真的建了任务**（``action == "created"``）—— 用户的行动请求；
    2. 这句话明确在指某个玩家（§八 的「跟着我」这类）。

    为什么要有第 2 条：7C 要求"对需要指定玩家的请求必须走可信身份桥解析目标"（§八），
    而**既有的**任务检测器并不把「跟着我」认成任务 —— 只看第 1 条的话，
    §八 那个例子永远不会有证据。第 2 条的判定是纯关键词、且**只**认"指玩家"这一类，
    所以普通闲聊与提问（"你在干嘛" / "你想收集一些橡木吗？"）都不会被记成提案。
    """
    if str(action or "") == "created":
        return True
    return objective_needs_player(text)


class TaskProposalService:
    """提案编排器（确定性；只读输入 + 一个状态存储 + 一个终态时钟）。"""

    def __init__(
        self,
        *,
        store: Any,
        config: Any = None,
        character_id: str = "",
        identity: Any = None,
        registry: Any = None,
        minecraft: Any = None,
        minecraft_online: Callable[[], bool] | None = None,
        publisher: Any = None,
        clock: Callable[[], float] = time.time,
        logger: Any = None,
    ) -> None:
        self.store = store
        self.config = config
        self.character_id = str(character_id or "")
        self.identity = identity
        self.registry = registry
        self.minecraft = minecraft
        self.publisher = publisher or ProposalEventPublisher(logger=logger)
        self._clock = clock
        self._log = logger
        self._online_provider = minecraft_online
        self.started_at = float(self._clock())
        self.last_check_at = 0.0
        self.created_count = 0
        self.merged_count = 0
        self.degraded_reason = ""

    # ------------------------------------------------------------ 只读

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True))

    def _now(self, now: float | None = None) -> float:
        return float(now if now is not None else self._clock())

    def _minecraft_online(self) -> bool:
        """Minecraft 是否在线（**只读**探针；读不到一律当离线，保守）。"""
        if self._online_provider is not None:
            try:
                return bool(self._online_provider())
            except Exception:  # noqa: BLE001
                return False
        service = self.minecraft
        if service is None or not bool(getattr(service, "enabled", False)):
            return False
        try:
            snapshot = service.snapshot()
        except Exception:  # noqa: BLE001 - 读不到就当离线（保守）
            return False
        connection = (snapshot or {}).get("connection") or {}
        return str(connection.get("status") or "") == "ONLINE"

    def catalog(self, *, minecraft_online: bool | None = None) -> dict[str, Capability]:
        """能力目录（§六）：从既有工具表 + 注册表提取；在线状态决定 ``available``。"""
        online = self._minecraft_online() if minecraft_online is None else bool(minecraft_online)
        return capability_catalog(registry=self.registry, minecraft_online=online)

    #: 配置旋钮（拿不到就用模块级默认；绝不因为配置读不到就不记账）
    @property
    def _max_per_pass(self) -> int:
        return max(1, int(getattr(self.config, "max_per_pass", MAX_PROPOSALS_PER_PASS)))

    @property
    def _view_limit(self) -> int:
        return max(1, int(getattr(self.config, "recent_limit", VIEW_RECENT_LIMIT)))

    @property
    def _ttl_seconds(self) -> float:
        return (
            float(getattr(self.config, "ttl_hours", DEFAULT_PROPOSAL_TTL_SECONDS / 3600.0)) * 3600.0
        )

    async def recent(self, limit: int | None = None) -> list[TaskProposal]:
        return await self.store.recent(
            max(1, int(limit if limit is not None else self._view_limit))
        )

    async def open_proposals(self) -> list[TaskProposal]:
        return await self.store.open_proposals()

    async def current(self) -> TaskProposal | None:
        """最近一份**还开着**的提案（"她最近在盘算什么"）。"""
        for proposal in await self.recent(RECOVER_LIMIT):
            if proposal.open:
                return proposal
        return None

    async def view(self) -> dict[str, Any]:
        """只读投影（WebUI §十二）—— **没有**任何执行按钮需要的数据。"""
        rows = await self.recent(self._view_limit)
        return {
            "enabled": self.enabled,
            "execution_layer": TASK_PROPOSAL_EXECUTION_LAYER,
            "proposals": [self.to_view(item) for item in rows],
            "open": sum(1 for item in rows if item.open),
            "created_total": int(self.created_count),
            "merged_total": int(self.merged_count),
            "minecraft_online": self._minecraft_online(),
            "degraded_reason": str(self.degraded_reason or ""),
        }

    def to_view(self, proposal: TaskProposal) -> dict[str, Any]:
        """单份提案的只读展示（§十二 要求的每一项都在；不暴露凭据/原始事件）。"""
        target = dict(proposal.target or {})
        return {
            "proposal_id": proposal.proposal_id,
            "source": str(proposal.source),
            "objective": proposal.objective,
            "intent_id": proposal.intent_id,
            "initiator": proposal.initiator,
            "target": {
                "status": str(target.get("status") or ""),
                "player_name": str(target.get("player_name") or ""),
                "server_id": str(target.get("server_id") or ""),
                # §十二：只给后四位，和 QQ/WebUI 其它地方一致，绝不回显完整 UUID
                "uuid_suffix": str(target.get("player_uuid") or "")[-4:],
                "reason": str(target.get("reason") or ""),
            },
            "required_capabilities": list(proposal.required_capabilities),
            "capability_gaps": [
                {"capability_id": item.capability_id, "gap": str(item.gap), "reason": item.reason}
                for item in proposal.requirements
                if item.capability_id and str(item.gap) != CapabilityGap.SUPPORTED.value
            ],
            "feasibility": str(proposal.feasibility),
            "risk_summary": dict(proposal.risk_summary or {}),
            "status": str(proposal.status),
            "reason": str(proposal.reason),
            "created_at": float(proposal.created_at),
            "expires_at": float(proposal.expires_at),
            "updated_at": float(proposal.updated_at),
            "terminal": proposal.terminal,
        }

    # ------------------------------------------------------------ 目标解析（§八）

    async def resolve_target(
        self, *, platform: str = "qq", user_id: str, server_id: str = ""
    ) -> TargetResolution:
        """QQ 用户 → 这台服务器上的 MC 玩家（**只认 VERIFIED 绑定**，绝不猜名字）。

        读不到身份层时返回 ``MISSING``（reason=``identity_unavailable``）—— 不猜、不放行。
        """
        identity = self.identity
        links_for = getattr(identity, "links_for", None)
        if not callable(links_for):
            # identity 为 None 或**形状不对**（真机踩过：接成了 QQ 绑定命令处理器，没有
            # ``links_for``）都算"身份层不可用" —— 如实记下原因，绝不猜目标。
            return TargetResolution(status=IDENTITY_MISSING, reason="identity_unavailable")
        if not str(user_id):
            return TargetResolution(status=IDENTITY_MISSING, reason="empty_user")
        try:
            links = list(await links_for(platform=str(platform), user_id=str(user_id)))
        except Exception as exc:  # noqa: BLE001 - 身份层故障只降级（§八：不猜目标）
            self.degraded_reason = f"{type(exc).__name__}"
            if self._log is not None:
                self._log.warning("[TaskProposal] 身份解析失败（降级）：%s", exc)
            return TargetResolution(status=IDENTITY_MISSING, reason="identity_unavailable")
        wanted = str(server_id or "")
        verified = [
            link
            for link in links
            if str(getattr(link, "status", "")) == IDENTITY_VERIFIED
            and (not wanted or str(getattr(link, "server_id", "")) == wanted)
        ]
        uuids = {str(getattr(link, "canonical_uuid", "") or "") for link in verified}
        if len(uuids) == 1 and verified:
            chosen = verified[0]
            return TargetResolution(
                status=IDENTITY_VERIFIED,
                server_id=str(getattr(chosen, "server_id", "") or ""),
                player_uuid=str(getattr(chosen, "canonical_uuid", "") or ""),
                player_name=str(getattr(chosen, "username", "") or ""),
                source=str(getattr(chosen, "source", "") or ""),
            )
        if len(uuids) > 1:
            # §八：同名/多人绑成同一个目标时**绝不合并**两名真实用户
            return TargetResolution(
                status=IDENTITY_CONFLICT,
                server_id=wanted,
                reason="multiple_verified_targets",
            )
        if any(str(getattr(link, "status", "")) == IDENTITY_CONFLICT for link in links):
            return TargetResolution(status=IDENTITY_CONFLICT, server_id=wanted, reason="conflict")
        if any(str(getattr(link, "status", "")) == IDENTITY_REVOKED for link in links):
            return TargetResolution(status=IDENTITY_REVOKED, server_id=wanted, reason="revoked")
        return TargetResolution(
            status=IDENTITY_MISSING,
            server_id=wanted,
            reason="server_mismatch" if (wanted and links) else "no_link",
        )

    # ------------------------------------------------------------ 建立提案

    async def propose_from_intent(self, intent: Any, *, now: float | None = None) -> dict[str, Any]:
        """一条 LifeIntent → 一份 TaskProposal（source=LIFE，**仅此而已**）。

        §十三-2：**过期或被抑制**的意图不产生有效提案 —— 直接跳过，不写任何行。
        """
        moment = self._now(now)
        status = str(getattr(getattr(intent, "status", None), "value", "") or "")
        if status != "PROPOSED":
            return {"action": "skipped", "reason": f"intent_{status.lower() or 'unknown'}"}
        expired_at = getattr(intent, "expired_at", None)
        if callable(expired_at) and bool(expired_at(moment)):
            return {"action": "skipped", "reason": "intent_expired"}
        objective = intent_objective(intent)
        if not objective:
            return {"action": "skipped", "reason": "empty_objective"}
        player = str(getattr(intent, "related_player", "") or "")
        target = NO_TARGET
        require_target = bool(player)
        if require_target:
            target = await self.resolve_target(user_id=player)
        return await self._create(
            source=ProposalSource.LIFE,
            objective=objective,
            intent_id=str(getattr(intent, "intent_id", "") or ""),
            initiator=str(getattr(intent, "character_id", "") or self.character_id),
            target=target,
            require_target=require_target,
            now=moment,
        )

    async def record_user_request(
        self,
        *,
        objective: str,
        user_id: str,
        session_id: str = "",
        platform: str = "qq",
        server_id: str = "",
        task_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """一条**用户**请求的旁路记账（source=USER，§四）。

        **它不参与** 5A/5B 任务链的决策：任务照旧由既有入口创建与执行，
        这里只是把"用户想要什么、需要哪些能力、目标能不能解析"留成可审计的提案。
        """
        moment = self._now(now)
        require_target = objective_needs_player(objective)
        target = (
            await self.resolve_target(platform=platform, user_id=user_id, server_id=server_id)
            if require_target
            else NO_TARGET
        )
        return await self._create(
            source=ProposalSource.USER,
            objective=objective,
            intent_id="",
            initiator=str(user_id),
            target=target,
            require_target=require_target,
            now=moment,
            detail={"session_id": str(session_id), "task_id": str(task_id)},
        )

    async def _create(
        self,
        *,
        source: ProposalSource,
        objective: str,
        intent_id: str,
        initiator: str,
        target: TargetResolution,
        require_target: bool,
        now: float,
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposal = build_proposal(
            source=source,
            objective=objective,
            intent_id=intent_id,
            initiator=initiator,
            target=target,
            require_target=require_target,
            catalog=self.catalog(),
            now=now,
            ttl_seconds=self._ttl_seconds,
        )
        stored, created = await self.store.create(proposal)
        event = PROPOSAL_CREATED if created else PROPOSAL_DEDUPLICATED
        if created:
            self.created_count += 1
        else:
            self.merged_count += 1
        await self.publisher.publish(
            event,
            stored,
            store=self.store,
            timestamp=now,
            reason="created" if created else "duplicate_fingerprint",
        )
        if self._log is not None:
            self._log.info(
                "[TaskProposal] action=%s id=%s source=%s status=%s feasibility=%s max_risk=%s"
                " target=%s intent=%s",
                "created" if created else "merged",
                stored.proposal_id,
                stored.source,
                stored.status,
                stored.feasibility,
                (stored.risk_summary or {}).get("max_risk", ""),
                (stored.target or {}).get("status", ""),
                stored.intent_id or "-",
            )
        result: dict[str, Any] = {
            "action": "created" if created else "merged",
            "event": event,
            "proposal": stored,
            "reason": str(stored.reason),
        }
        if detail:
            result["detail"] = dict(detail)
        return result

    # ------------------------------------------------------------ 过期 / 恢复

    async def expire_due(self, *, now: float | None = None) -> list[TaskProposal]:
        """把过了 ``expires_at`` 的**开着**的提案置为 EXPIRED（终态，不可复活，§十）。"""
        moment = self._now(now)
        expired: list[TaskProposal] = []
        for proposal in await self.store.open_proposals():
            if not proposal.expired_at(moment):
                continue
            updated = await self.store.update_status(
                proposal.proposal_id, ProposalStatus.EXPIRED, reason="ttl_expired", now=moment
            )
            if updated is None:
                continue  # 并发下已经被改成别的终态：尊重先到的那个
            expired.append(updated)
            await self.publisher.publish(
                PROPOSAL_EXPIRED, updated, store=self.store, timestamp=moment, reason="ttl_expired"
            )
        return expired

    async def cancel(
        self, proposal_id: str, *, reason: str = "cancelled", now: float | None = None
    ) -> TaskProposal | None:
        """取消一份提案（终态；重启也不会复活）。"""
        moment = self._now(now)
        updated = await self.store.update_status(
            str(proposal_id), ProposalStatus.CANCELLED, reason=str(reason), now=moment
        )
        if updated is not None:
            await self.publisher.publish(
                PROPOSAL_CANCELLED,
                updated,
                store=self.store,
                timestamp=moment,
                reason=str(reason),
            )
        return updated

    async def reject(
        self, proposal_id: str, *, reason: str = "rejected", now: float | None = None
    ) -> TaskProposal | None:
        """明确否决一份提案（终态；例如目标身份被撤销）。"""
        moment = self._now(now)
        updated = await self.store.update_status(
            str(proposal_id), ProposalStatus.REJECTED, reason=str(reason), now=moment
        )
        if updated is not None:
            await self.publisher.publish(
                PROPOSAL_REJECTED, updated, store=self.store, timestamp=moment, reason=str(reason)
            )
        return updated

    async def recover(self, *, now: float | None = None) -> dict[str, Any]:
        """重启恢复（§十）：**只**处理过期与只读视图，绝不复活任何终态。

        终态提案（REJECTED/EXPIRED/CANCELLED）在这里连碰都不会被碰 ——
        ``expire_due`` 只遍历开着的行。
        """
        moment = self._now(now)
        self.last_check_at = moment
        try:
            expired = await self.expire_due(now=moment)
            rows = await self.recent(RECOVER_LIMIT)
            open_rows = [item for item in rows if item.open]
        except Exception as exc:  # noqa: BLE001 - 恢复失败只降级，绝不影响启动
            self.degraded_reason = f"{type(exc).__name__}"
            if self._log is not None:
                self._log.warning("[TaskProposal] 恢复失败（降级）：%s", exc)
            return {"action": "degraded", "expired": 0, "open": 0, "recent": 0, "reason": "error"}
        return {
            "action": "ok",
            "expired": len(expired),
            "open": len(open_rows),
            "recent": len(rows),
            "reason": "",
        }

    async def propose_open_intents(
        self, intents: Sequence[Any], *, now: float | None = None
    ) -> dict[str, Any]:
        """把一批**新产生**的意图变成提案（§十：一次最多 ``MAX_PROPOSALS_PER_PASS`` 条）。"""
        moment = self._now(now)
        self.last_check_at = moment
        created: list[TaskProposal] = []
        merged: list[TaskProposal] = []
        skipped: list[str] = []
        for intent in list(intents)[: self._max_per_pass]:
            out = await self.propose_from_intent(intent, now=moment)
            if out.get("action") == "created":
                created.append(out["proposal"])
            elif out.get("action") == "merged":
                merged.append(out["proposal"])
            else:
                skipped.append(str(out.get("reason") or ""))
        await self.expire_due(now=moment)
        return {
            "action": "created" if created else "noop",
            "created": created,
            "merged": merged,
            "skipped": skipped,
        }


def intent_objective(intent: Any) -> str:
    """意图 → 一句可执行的目标文本（§三：目标要能被检查，不能只有一句"想玩"）。

    只用**意图自己带的文本**（标题 + 描述 + 关联记忆），不调模型、不编造细节。
    """
    parts: list[str] = []
    for name in ("title", "description", "related_memory"):
        value = " ".join(str(getattr(intent, name, "") or "").split())
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)[:200]
