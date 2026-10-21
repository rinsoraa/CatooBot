"""用户回合 → 任务动作（Phase 5A §四十二/§四十三/§六十）。

这一层是 TaskRuntime 的**唯一对话入口**，负责把一句人话翻译成任务操作，
规则全部写在代码里（不叫模型来猜）：

* 控制命令（确认 / 暂停 / 继续 / 取消这个任务）→ 直接操作当前任务，**不新增工具**；
* 需要跨多个步骤的请求（"去砍一棵树，挖一块原木并捡回来"）→ 先做 SAFE 观察
  （Phase A）→ 生成冻结计划（Phase B）→ 让用户确认；
* 其余一律**不碰**：普通聊天仍然是普通聊天（§九十九），"你在哪""背包里有什么"
  这类查询直接走既有的 SAFE 工具。

确认仍然只发生在 USER 回合：本模块不绕过 ``TaskRuntime.confirm_and_start`` 的任何检查
（它自己会校验 origin/user/session 与一次性确认条目）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.character.turn import TurnOrigin
from app.tasks.build_plan import parse_build_request, plan_build_task
from app.tasks.combat_plan import parse_combat_request, plan_combat_task
from app.tasks.intent import TaskIntentDetector
from app.tasks.models import ReplanReason, TaskState
from app.tasks.planner import ObservationFailed, plan_collection_task
from app.tasks.resource_contract import RESOURCE_SPECS
from app.tasks.resource_intent import collection_block_for, parse_collection_request
from app.tasks.runtime import TaskAuthorizationError, TaskBusy, TaskRuntime
from app.tasks.validation import collection_plan_problems

log = logging.getLogger("CatooBot.Tasks.Turn")


def _collection_input_reply(problem: str) -> str:
    if problem == "negated_or_ambiguous":
        return "收到，这条请求先不执行采集。如果需要采集，请重新明确目标和数量。"
    if problem == "query_only":
        return "这条按询问处理，不会创建采集任务。"
    if problem == "unsupported_target":
        return "这个采集目标暂不支持。请选已支持的原木、石头、圆石、煤矿、铁矿、泥土或沙子。"
    if problem == "quantity_out_of_budget":
        return "这次数量超出范围：原木每次最多两根，其他已支持资源每次一块。请明确数量后再试。"
    if problem == "text_too_long":
        return "采集说明太长，请简短说明一种资源和数量。"
    return "采集目标或数量还不明确，请先选定一种资源并说明数量，我再规划。"


def _build_input_reply(problem: str) -> str:
    if "unsupported_item" in problem:
        return "这个方块暂不支持放置。首期只支持工作台、火把、泥土、圆石、石头和橡木木板。"
    if "too_many_cells" in problem:
        return "一次最多放四块，请缩小范围。"
    if "region_not_axis_aligned" in problem:
        return "首期只支持沿一个轴排成直线的简单放置，请给出同一轴上的起点和终点。"
    if "target_occupied" in problem:
        return "目标位置已经有方块了，不能覆盖。"
    if "reference_missing" in problem:
        return "目标旁边没有可依附的方块，放不上去。"
    if "material_missing_or_insufficient" in problem:
        return "主手没有拿够这种方块；请先把材料拿在主手，再让我放。"
    if "cell_outside_region" in problem or "region_too_large" in problem:
        return "目标超出身边固定的小范围，请把放置位置放在附近三格以内。"
    if "cell_too_far" in problem:
        return "目标超出可放置距离，请靠近一点。"
    if "position_unknown" in problem:
        return "现在读不到位置，暂时不能放置。"
    return "这个放置请求现在不能执行，请检查目标位置、材料和主手物品。"


def _combat_input_reply(problem: str) -> str:
    if "player_forbidden" in problem:
        return "默认不攻击玩家。"
    if "villager_forbidden" in problem:
        return "默认不攻击村民或商人。"
    if "pet_forbidden" in problem:
        return "默认不攻击宠物或驯服动物。"
    if "explosive_not_supported" in problem:
        return "爆炸类目标不在首期战斗范围内。"
    if "target_required" in problem:
        return "要打什么还不明确；首期只支持僵尸、骷髅、蜘蛛和洞穴蜘蛛。"
    if "unsupported_target" in problem:
        return "这个目标不在战斗白名单里。"
    if "no_target" in problem:
        return "附近没有看到符合条件的敌对目标。"
    if "target_identity_unknown" in problem:
        return "目标身份还不完整，不能开始攻击。"
    if "low_health" in problem:
        return "我现在生命太低，不进入战斗。"
    if "low_food" in problem:
        return "我现在太饿，不进入战斗。"
    if "weapon_required" in problem:
        return "主手需要先拿一把剑，我才会开始攻击。"
    return "这次战斗请求现在不能执行，请检查目标和自身状态。"


#: Compatibility export, derived from the single resource registry.
DROP_OVERRIDES = {
    "minecraft:" + name: "minecraft:" + spec.drop
    for name, spec in RESOURCE_SPECS.items()
    if name != spec.drop
}


STATE_LABELS: dict[str, str] = {
    "PLANNING": "正在盘算",
    "PENDING_CONFIRMATION": "等你确认",
    "RUNNING": "进行中",
    "WAITING_ACTION": "进行中",
    "WAITING_USER": "等你说话",
    "PAUSED": "已暂停",
    "REPLANNING": "需要重新确认新计划",
    "SUCCEEDED": "已完成",
    "FAILED": "没做成",
    "CANCELLED": "已取消",
    "EXPIRED": "已过期",
}


@dataclass(frozen=True)
class TaskReplies:
    """同一套语义的两种说法（游戏内 vs QQ）—— 语义只有一处，措辞可以各说各的。"""

    created: str = "我打算这么做，你看行不行：\n{summary}\n{hint}"
    created_hint: str = "想让我开始就说「确认」；改主意了就说「停止这个任务」。"
    confirmed: str = "好，我这就去。"
    paused: str = "好，我先停下。"
    resumed: str = "好，接着做。"
    cancelled: str = "好，不做了。"
    busy: str = "我手上还有一个没做完的事（{objective}）。先说「停止这个任务」，我再来做新的。"
    plan_failed: str = "我看了一圈，现在做不了：{reason}。"
    plan_failed_unknown: str = "我试着盘算了一下，但没排明白，等会儿再说吧。"
    replan_failed_unknown: str = (
        "原来那件事的目标已经不在了，而且我没听出你想改做什么。再说一次要什么吧。"
    )
    replan_failed: str = "我重新找了一圈，现在还是做不了：{reason}。"
    replan_failed_retry: str = "我试着重新盘算了一下，但没排明白，等会儿再说吧。"
    not_owner: str = "这个任务不是你发起的，控制不了。"
    confirm_failed: str = "这次确认没生效（{reason}）。想让我做的话再说一次「确认」。"
    resume_needs_confirmation: str = (
        "刚才那份许可过期了，我重新说一遍要做的事，你说「确认」我就继续。"
    )
    action_failed: str = "这件事现在做不了（{reason}）。"
    status: str = "任务：{objective}\n状态：{state}\n第 {done}/{total} 步\n当前：{current}"
    status_idle: str = "（这一步还没开始）"


#: 游戏内聊天（Phase 5A 的原话，保持兼容）
IN_GAME_REPLIES = TaskReplies()

#: QQ（Phase 5B §八/§十四/§二十八/§三十二）
QQ_REPLIES = TaskReplies(
    created=(
        "罐头准备这样做：\n\n{summary}\n\n这是第 {version} 版计划。\n\n"
        "需要你确认后我才会动手。\n回复「确认」开始。"
    ),
    created_hint="",
    confirmed="🌱 我开始处理了。",
    paused="先停这里了。",
    resumed="好，接着做。",
    cancelled="好，不做了。",
    busy="你还有一个任务正在处理中。\n\n当前：{objective}\n\n请先：暂停 / 停止 / 继续",
    plan_failed="我找了一圈，现在做不了：{reason}。",
    plan_failed_unknown="我试着盘算了一下，但没排明白，等会儿再说吧。",
    replan_failed_unknown="原来那件事的目标已经不在了，而且我没听出你想改做什么。再说一次要什么吧。",
    replan_failed="我重新找了一圈，现在还是做不了：{reason}。",
    replan_failed_retry="我试着重新盘算了一下，但没排明白，等会儿再说吧。",
    not_owner="你不是这个任务的发起人，这个任务由 @{owner} 创建。",
    confirm_failed="这次确认没生效（{reason}）。想让我做的话再说一次「确认」。",
    resume_needs_confirmation="刚才那份许可过期了，计划没有变，需要你重新确认一次。",
    action_failed="这件事现在做不了（{reason}）。",
    status="任务：{objective}\n\n状态：{state}\n第 {done}/{total} 步\n\n当前：\n{current}",
    status_idle="（这一步还没开始）",
)


@dataclass
class TaskIntent:
    """规范化的任务意图（§二十五）：QQ 原始消息绝不进 TaskRuntime。

    ``source`` 是入口标签（``qq`` / ``minecraft_chat`` / ``webui``），
    只用于审计与 UI 展示（§二十九/§三十）；TaskRuntime 收到的是规范化身份与目标。
    """

    objective: str
    source: str
    user_id: str
    session_id: str
    conversation_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "source": self.source,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
        }


@dataclass
class TaskTurnOutcome:
    """一次用户回合的任务侧结果（``reply`` 直接发回给用户）。"""

    handled: bool
    action: str = ""
    reply: str = ""
    task_id: str = ""
    state: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def block_for(text: str) -> str:
    """Only a single resolved target; generic logs must use the candidate planner."""
    return collection_block_for(text)


class TaskTurnHandler:
    """把一次 USER 回合接到 TaskRuntime 上（在 Minecraft 会话里用）。"""

    def __init__(
        self,
        runtime: TaskRuntime,
        *,
        observe: Any,
        detector: TaskIntentDetector | None = None,
        replies: TaskReplies | None = None,
        source: str = "minecraft_chat",
        objective_limit: int = 120,
        plans: Any = None,
        skills: Any = None,
    ) -> None:
        self._runtime = runtime
        self._observe = observe
        self._detector = detector or TaskIntentDetector()
        self._replies = replies or IN_GAME_REPLIES
        self._source = str(source or "minecraft_chat")
        self._limit = max(20, int(objective_limit))
        #: Phase 7D：AgentPlan 服务（鸭子类型注入；None = 不记账，任务链完全不受影响）
        self._plans = plans
        #: Phase 7E：程序性技能服务（鸭子类型注入）。它只能提供**计划候选**：
        #: 适配性通过时用它物化的计划，否则原样回退既有确定性模板；异常一律回退。
        self._skills = skills

    # ------------------------------------------------ 只读访问器（Phase 8D）
    @property
    def detector(self) -> TaskIntentDetector:
        """入口用的确定性检测器 —— MC 桥接在**接管之前**必须用同一套判定（§7.4）。"""
        return self._detector

    async def current_task(self, session_id: str) -> Any | None:
        """这个会话当前的活动任务（只读；与 handle 内部用的是同一个查询）。"""
        return await self._runtime.current(session_id)

    # ------------------------------------------------------------ 入口

    async def handle(
        self,
        *,
        session_id: str,
        user_id: str,
        text: str,
        origin: str = TurnOrigin.USER.value,
    ) -> TaskTurnOutcome:
        """返回 ``handled=False`` = "这句话不归任务管，交给正常对话"。"""
        message = str(text or "").strip()
        if not message:
            return TaskTurnOutcome(False)
        current = await self._runtime.current(session_id)
        command = self._detector.control_command(message)
        if command:
            if current is None:
                # 没有任务时的"确认/继续"是普通聊天（用户可能在聊别的）
                return TaskTurnOutcome(False)
            return await self._control(command, current, user_id=user_id, session_id=session_id)
        if current is not None and self._detector.status_query(message):
            # §二十七：先看当前会话里那个**唯一**的活动任务，不猜、不挑历史任务
            return self._status(current)
        build = parse_build_request(message)
        if build is not None:
            if current is not None and not current.state.terminal:
                return TaskTurnOutcome(
                    True,
                    action="busy",
                    task_id=current.task_id,
                    state=current.state.value,
                    reply=self._replies.busy.format(
                        objective=current.objective, state=STATE_LABELS.get(current.state.value, "")
                    ),
                )
            return await self._create_build(session_id, user_id, message, build, origin=origin)
        combat = parse_combat_request(message)
        if combat is not None:
            if current is not None and not current.state.terminal:
                return TaskTurnOutcome(
                    True,
                    action="busy",
                    task_id=current.task_id,
                    state=current.state.value,
                    reply=self._replies.busy.format(
                        objective=current.objective, state=STATE_LABELS.get(current.state.value, "")
                    ),
                )
            return await self._create_combat(session_id, user_id, message, combat, origin=origin)
        intent = self._detector.detect(message)
        if not intent.is_task:
            return TaskTurnOutcome(False)
        if current is not None and not current.state.terminal:
            # 一个会话同时只有一个前台任务（§二十六）：先把手上这个说完
            return TaskTurnOutcome(
                True,
                action="busy",
                task_id=current.task_id,
                state=current.state.value,
                reply=self._replies.busy.format(
                    objective=current.objective, state=STATE_LABELS.get(current.state.value, "")
                ),
            )
        # Phase 8C：来源必须一路带下去（此前 _create 里被硬编码成 USER，形参形同虚设）。
        return await self._create(session_id, user_id, message, origin=origin)

    async def _create_build(
        self,
        session_id: str,
        user_id: str,
        message: str,
        build: Any,
        *,
        origin: str = TurnOrigin.USER.value,
    ) -> TaskTurnOutcome:
        if build.problem:
            return TaskTurnOutcome(
                True,
                action="plan_failed",
                reply=_build_input_reply(build.problem),
                detail={"reason_code": f"build.{build.problem}"},
            )
        try:
            planned = await plan_build_task(message, build, observe=self._observe)
        except ObservationFailed as exc:
            return TaskTurnOutcome(
                True,
                action="plan_failed",
                reply=_build_input_reply(str(exc)),
                detail={"reason_code": str(exc)},
            )
        except Exception:  # noqa: BLE001 - build planning must not break chat
            log.exception("[Task] build planning failed (%s)", message)
            return TaskTurnOutcome(
                True, action="plan_failed", reply="这个放置请求现在做不了，请稍后再试。"
            )
        try:
            record = await self._runtime.create_task(
                message,
                session_id=session_id,
                user_id=user_id,
                origin=str(origin or TurnOrigin.USER.value),
                plan=planned.plan,
                source=self._source,
            )
        except TaskBusy:
            return TaskTurnOutcome(
                True, action="busy", reply="我手上还有一件事没做完，先做完这个再说。"
            )
        await self._record_agent_plan(message, record, session_id=session_id, user_id=user_id)
        summary = self._runtime.summary_of(record)
        reply = self._replies.created.format(
            summary=summary,
            version=record.plan_version,
            hint=self._replies.created_hint,
        ).strip()
        return TaskTurnOutcome(
            True,
            action="created",
            task_id=record.task_id,
            state=record.state.value,
            reply=reply,
            detail={"plan_hash": record.plan_hash, "plan_version": record.plan_version},
        )

    async def _create_combat(
        self,
        session_id: str,
        user_id: str,
        message: str,
        combat: Any,
        *,
        origin: str = TurnOrigin.USER.value,
    ) -> TaskTurnOutcome:
        if combat.problem:
            return TaskTurnOutcome(
                True,
                action="plan_failed",
                reply=_combat_input_reply(combat.problem),
                detail={"reason_code": f"combat.{combat.problem}"},
            )
        try:
            planned = await plan_combat_task(message, combat, observe=self._observe)
        except ObservationFailed as exc:
            return TaskTurnOutcome(
                True,
                action="plan_failed",
                reply=_combat_input_reply(str(exc)),
                detail={"reason_code": str(exc)},
            )
        except Exception:  # noqa: BLE001 - combat planning must not break chat
            log.exception("[Task] combat planning failed (%s)", message)
            return TaskTurnOutcome(
                True, action="plan_failed", reply="这次战斗请求现在做不了，请稍后再试。"
            )
        try:
            record = await self._runtime.create_task(
                message,
                session_id=session_id,
                user_id=user_id,
                origin=str(origin or TurnOrigin.USER.value),
                plan=planned.plan,
                source=self._source,
            )
        except TaskBusy:
            return TaskTurnOutcome(
                True, action="busy", reply="我手上还有一件事没做完，先做完这个再说。"
            )
        await self._record_agent_plan(message, record, session_id=session_id, user_id=user_id)
        summary = self._runtime.summary_of(record)
        reply = self._replies.created.format(
            summary=summary,
            version=record.plan_version,
            hint=self._replies.created_hint,
        ).strip()
        return TaskTurnOutcome(
            True,
            action="created",
            task_id=record.task_id,
            state=record.state.value,
            reply=reply,
            detail={"plan_hash": record.plan_hash, "plan_version": record.plan_version},
        )

    async def _record_agent_plan(
        self, message: str, record: Any, *, session_id: str, user_id: str
    ) -> None:
        if self._plans is None:
            return
        try:
            planned = type("P", (), {"plan": getattr(record, "plan", None), "risk_summary": {}})()
            await self._plans.record_user_plan(
                objective=str(message),
                user_id=str(user_id),
                session_id=str(session_id),
                task_id=str(record.task_id),
                planned=planned,
            )
        except Exception:  # noqa: BLE001 - 计划记账失败绝不影响任务链
            log.debug("[Task] agent plan 记账失败（忽略）", exc_info=True)

    # ------------------------------------------------------------ 控制命令

    async def _control(
        self, command: str, record: Any, *, user_id: str, session_id: str
    ) -> TaskTurnOutcome:
        runtime = self._runtime
        # §十六：任何控制入口都要重新验证归属 —— 别人（哪怕是同群成员）碰不到这个任务，
        # 而且**拒绝不得改变任务状态**（这里在调用任何 runtime 方法之前就返回）。
        owner = str(record.user_id)
        if str(user_id) != owner:
            return TaskTurnOutcome(
                True,
                action="not_owner",
                task_id=record.task_id,
                state=record.state.value,
                reply=self._replies.not_owner.format(owner=owner, objective=record.objective),
            )
        try:
            if command == "confirm":
                if record.replan_required or record.state is TaskState.REPLANNING:
                    # 5A.1 §三十二：世界变了 → 先重新观察、出新计划，让用户确认**新**计划
                    return await self._replan(record)
                updated = await runtime.confirm_and_start(
                    record.task_id,
                    user_id=user_id,
                    session_id=session_id,
                    origin=TurnOrigin.USER.value,
                )
                if updated.state.value == "PENDING_CONFIRMATION":
                    return TaskTurnOutcome(
                        True,
                        action="confirm_failed",
                        task_id=record.task_id,
                        state=updated.state.value,
                        reply=self._replies.confirm_failed.format(reason=updated.message),
                    )
                return TaskTurnOutcome(
                    True,
                    action="confirmed",
                    task_id=record.task_id,
                    state=updated.state.value,
                    reply=self._replies.confirmed,
                )
            if command == "pause":
                updated = await runtime.pause(record.task_id, reason="用户说暂停")
                return TaskTurnOutcome(
                    True,
                    action="paused",
                    task_id=record.task_id,
                    state=updated.state.value,
                    reply=self._replies.paused,
                )
            if command == "resume":
                if record.replan_required and record.state in {
                    TaskState.PAUSED,
                    TaskState.REPLANNING,
                }:
                    return await self._replan(record)
                updated = await runtime.resume(
                    record.task_id,
                    user_id=user_id,
                    session_id=session_id,
                    origin=TurnOrigin.USER.value,
                )
                if updated.state.value == "PENDING_CONFIRMATION":
                    return TaskTurnOutcome(
                        True,
                        action="resume_needs_confirmation",
                        task_id=record.task_id,
                        state=updated.state.value,
                        reply=self._replies.resume_needs_confirmation,
                    )
                return TaskTurnOutcome(
                    True,
                    action="resumed",
                    task_id=record.task_id,
                    state=updated.state.value,
                    reply=self._replies.resumed,
                )
            if command == "cancel":
                updated = await runtime.cancel(record.task_id, reason="用户取消")
                return TaskTurnOutcome(
                    True,
                    action="cancelled",
                    task_id=record.task_id,
                    state=updated.state.value,
                    reply=self._replies.cancelled,
                )
        except (TaskAuthorizationError, TaskBusy) as exc:
            return TaskTurnOutcome(
                True,
                action=f"{command}_failed",
                task_id=record.task_id,
                state=record.state.value,
                reply=self._replies.action_failed.format(reason=getattr(exc, "code", "") or exc),
            )
        return TaskTurnOutcome(False)

    # ------------------------------------------------------------ 状态查询

    def _status(self, record: Any) -> TaskTurnOutcome:
        """§二十八：给用户看的状态摘要（绝不直接返回完整 TaskRecord）。"""
        snapshot = self._runtime.snapshot_payload(record)
        current = snapshot.get("current_step") or {}
        label = ""
        for row in (snapshot.get("plan") or {}).get("steps") or []:
            if isinstance(row, dict) and row.get("step_id") == current.get("step_id"):
                label = str(row.get("label") or "")
                break
        progress = snapshot.get("progress") or {}
        reply = self._replies.status.format(
            objective=record.objective,
            state=STATE_LABELS.get(record.state.value, record.state.value),
            done=int(progress.get("completed") or 0),
            total=int(progress.get("total") or 0),
            current=label or self._replies.status_idle,
        )
        return TaskTurnOutcome(
            True,
            action="status",
            task_id=record.task_id,
            state=record.state.value,
            reply=reply,
        )

    # ------------------------------------------------------------ 重规划

    async def _replan(self, record: Any) -> TaskTurnOutcome:
        """用户按了「确认/继续」，但旧计划已经作废 → 先 SAFE 观察，再出新计划要他确认。

        仍然**只做 SAFE 查询**（§五）：新计划里任何世界动作都要等这次的新确认。
        """
        request = parse_collection_request(record.objective)
        if request is None:
            return TaskTurnOutcome(
                True,
                action="replan_failed",
                task_id=record.task_id,
                state=record.state.value,
                reply="原来那件事的目标已经不在了，而且我没听出你想改做什么。再说一次要什么吧。",
            )
        try:
            planned = await plan_collection_task(
                record.objective,
                observe=self._observe,
            )
        except ObservationFailed as exc:
            return TaskTurnOutcome(
                True,
                action="replan_failed",
                task_id=record.task_id,
                state=record.state.value,
                reply=f"我重新找了一圈，现在还是做不了：{exc}。",
            )
        except Exception:  # noqa: BLE001 - 规划失败不该让游戏内聊天没反应
            log.exception("[Task] 重规划失败 task=%s", record.task_id)
            return TaskTurnOutcome(
                True,
                action="replan_failed",
                task_id=record.task_id,
                state=record.state.value,
                reply="我试着重新盘算了一下，但没排明白，等会儿再说吧。",
            )
        try:
            updated = await self._runtime.replan(
                record.task_id,
                planned.plan,
                reason=str(record.replan_reason or ReplanReason.USER_REQUEST.value),
                observations=[item.to_payload() for item in planned.observations],
            )
        except (TaskAuthorizationError, TaskBusy) as exc:
            return TaskTurnOutcome(
                True,
                action="replan_failed",
                task_id=record.task_id,
                state=record.state.value,
                reply=f"这件事实在接不下去（{getattr(exc, 'code', '') or exc}）。",
            )
        return TaskTurnOutcome(
            True,
            action="replanned",
            task_id=updated.task_id,
            state=updated.state.value,
            reply=self._runtime.replan_summary(
                updated, reason=str(updated.replan_reason or record.replan_reason)
            ),
        )

    # ------------------------------------------------------------ 新任务

    async def _bind_skill_task(self, record: Any, planned: Any) -> None:
        """技能 → 任务 的持久绑定（只在任务**确实建立**之后调用；失败只降级）。"""

        skills = getattr(self, "_skills", None)
        binder = getattr(skills, "bind_task_for_plan", None)
        if binder is None or planned is None:
            return
        try:
            await binder(record, getattr(planned, "plan", None))
        except Exception:  # noqa: BLE001 - 绑定失败不影响任务本身
            log.exception("[Task] 技能绑定失败（忽略，之后不会对该任务做技能反馈）")

    async def _skill_plan(self, objective: str) -> Any:
        """Phase 7E：技能给出的计划候选（没有/不适用/异常 → None = 用既有模板）。"""

        skills = getattr(self, "_skills", None)
        if skills is None:
            return None
        try:
            planned = await skills.suggest(objective)
            if planned is not None and (
                collection_plan_problems(planned.plan)
                or collection_plan_problems(planned.plan, objective=objective, required=True)
            ):
                return None
            return planned
        except Exception:  # noqa: BLE001 - 技能异常绝不改变既有行为
            log.exception("[Task] 技能复用失败（忽略，按原模板规划）")
            return None

    async def _create(
        self,
        session_id: str,
        user_id: str,
        message: str,
        *,
        origin: str = TurnOrigin.USER.value,
    ) -> TaskTurnOutcome:
        request = parse_collection_request(message)
        if request is None:
            # 认不出具体目标 → 不开任务，交给正常对话（模型可以用 SAFE 工具去查）
            return TaskTurnOutcome(False)
        if request.problem:
            log.info("[Task] collection input rejected reason=%s", request.problem)
            return TaskTurnOutcome(
                True,
                action="plan_failed",
                reply=_collection_input_reply(request.problem),
                detail={"reason_code": f"resource.intent.{request.problem}"},
            )
        # Parse the full original text before any display truncation.
        objective = message
        try:
            # Phase 7E §7.5：先问一次技能（适用 → 用它的计划候选；不适用/异常 → 原样回退）。
            # 技能只是**计划候选**：下面照旧走 create_task → validate_plan → 确认门。
            planned = await self._skill_plan(objective)
            if planned is None:
                planned = await plan_collection_task(
                    objective,
                    observe=self._observe,
                )
        except ObservationFailed as exc:
            return TaskTurnOutcome(
                True, action="plan_failed", reply=f"我看了一圈，现在做不了：{exc}。"
            )
        except Exception:  # noqa: BLE001 - 规划失败绝不影响正常对话
            log.exception("[Task] 规划失败（%s）", objective)
            return TaskTurnOutcome(
                True, action="plan_failed", reply="我试着盘算了一下，但没排明白，等会儿再说吧。"
            )
        try:
            record = await self._runtime.create_task(
                objective,
                session_id=session_id,
                user_id=user_id,
                origin=str(origin or TurnOrigin.USER.value),
                plan=planned.plan,
                source=self._source,
            )
        except TaskBusy:
            return TaskTurnOutcome(
                True, action="busy", reply="我手上还有一件事没做完，先做完这个再说。"
            )
        # Phase 7E.1 §2：任务**真的建立之后**才把"这条任务用了哪条技能"落成持久绑定
        # （失败只降级：绑定不上就永远不反馈，绝不猜来源）。
        await self._bind_skill_task(record, planned)
        # Phase 7D §四（修订 1）：USER 计划与待确认任务**同时**建立（记账失败不影响任务）。
        await self._record_agent_plan(message, record, session_id=session_id, user_id=user_id)
        summary = self._runtime.summary_of(record)
        reply = self._replies.created.format(
            summary=summary,
            version=record.plan_version,
            hint=self._replies.created_hint,
        ).strip()
        return TaskTurnOutcome(
            True,
            action="created",
            task_id=record.task_id,
            state=record.state.value,
            reply=reply,
            detail={"plan_hash": record.plan_hash, "plan_version": record.plan_version},
        )
