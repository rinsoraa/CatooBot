"""Minecraft Agent Bridge（Phase 3E）：LLM Tool → Policy → Service → Action Runtime。

边界（任务书 §三）：LLM 永远不直接碰 runtime / HTTP / Mineflayer。六个 Minecraft Tool
只能经过这里，而这里只回答三件事：

* **能不能做** —— :class:`MinecraftActionPolicy`：风险分级（SAFE/LOW/…）+ 显式意图门
  （LOW 只在用户明确要求时放行）+ 在线/忙碌门 + 目标在附近的上下文门（§十二~§十六）；
* **做完了是什么** —— :class:`MinecraftAgentContext`：当前动作 / 最近动作 / 活动，
  完全由 action 事件驱动（§二十/§二十二/§二十四），**绝不**自己伪造终态；
* **结果怎么给模型** —— :meth:`MinecraftAgentBridge.invoke`：结构化 ok/error（§二十七/§二十八），
  绝不把 traceback / HTTP 栈交给模型。

本模块不新增任何 Minecraft 能力，也不改变既有动作语义：动作执行永远在
:class:`~app.integrations.minecraft.service.MinecraftService` → Action Runtime 里。

意图门（§十五）只认**结构性事实**：这一轮是不是用户发起的对话（Tool Layer 不做 NLP，
「用户是不是要罐头过去」由 LLM 判断 —— §十六）。模型在自主/后台回合里推理出「我应该跟过去」
时没有用户请求，LOW 一律被拒。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.integrations.minecraft.service import MinecraftBridgeError
from app.tools.models import ToolContext, ToolResult

if TYPE_CHECKING:  # pragma: no cover
    from app.config.settings import MinecraftAgentToolsConfig
    from app.integrations.minecraft.events import MinecraftBridgeEvent
    from app.integrations.minecraft.service import MinecraftService

log = logging.getLogger("CatooBot.Minecraft.Agent")

#: ToolContext.metadata 里的桥接对象键（tool 通过它拿到 service/policy/context）
BRIDGE_KEY = "minecraft"
#: ToolContext.metadata 里的「本轮由用户明确发起」标记（意图门用；缺省 = 不允许 LOW）。
#: 它**只能**由 :class:`~app.character.turn.TurnOrigin` 派生（Phase 3E.1），
#: 任何调用方都不许手写这个键。
INTENT_KEY = "minecraft_explicit_intent"
#: 本轮来源（``TurnOrigin`` 的值）；只用于日志与排查，绝不进 LLM（Phase 3E.1 §六/§二十）
TURN_ORIGIN_KEY = "turn_origin"

#: 正式风险分级（§十三）。本阶段只落地 SAFE / LOW；MEDIUM/HIGH/DESTRUCTIVE 等 Phase 4。
RISK_LEVELS = ("SAFE", "LOW", "MEDIUM", "HIGH", "DESTRUCTIVE")

#: 六个已批准 Tool 的风险等级（§二）——注册表之外的任何名字都不放行
ACTION_RISK: dict[str, str] = {
    "minecraft_world": "SAFE",
    "minecraft_chat": "SAFE",
    "minecraft_look_at": "SAFE",
    "minecraft_stop": "SAFE",
    "minecraft_move_to": "LOW",
    "minecraft_follow_player": "LOW",
}

#: Tool → Action Runtime 动作名（chat 也走统一生命周期）
TOOL_ACTION: dict[str, str] = {
    "minecraft_chat": "chat",
    "minecraft_look_at": "look_at",
    "minecraft_move_to": "move_to",
    "minecraft_follow_player": "follow_player",
    "minecraft_stop": "stop",
}

#: 需要世界在线才能执行（minecraft_world 是离线也能答的只读工具；
#: minecraft_stop 是最高优先级安全停止，离线/不可达也必须成功）
NEEDS_ONLINE: frozenset[str] = frozenset(
    {"minecraft_chat", "minecraft_look_at", "minecraft_move_to", "minecraft_follow_player"}
)

#: 独占前台动作（runtime 同一时间只允许一个）：忙 → minecraft.action_busy（§二十六）
EXCLUSIVE_TOOLS: frozenset[str] = frozenset(
    {"minecraft_look_at", "minecraft_move_to", "minecraft_follow_player"}
)

#: 需要「用户明确要求」才能执行的风险等级（§十四/§十五）
EXPLICIT_INTENT_RISKS: frozenset[str] = frozenset({"LOW", "MEDIUM", "HIGH", "DESTRUCTIVE"})

#: 风险等级 → 配置开关字段（§三十一；未实现的等级默认关闭，且没有对应 Tool）
RISK_FLAGS: dict[str, str] = {
    "SAFE": "allow_safe",
    "LOW": "allow_low",
    "MEDIUM": "allow_medium",
    "HIGH": "allow_high",
    "DESTRUCTIVE": "allow_destructive",
}

#: runtime 动作错误码 → 交给模型的稳定错误码（§二十七）。运行时词表不外泄。
RUNTIME_ERROR_CODES: dict[str, str] = {
    "action.not_online": "minecraft.offline",
    "chat.not_online": "minecraft.offline",
    "action.busy": "minecraft.action_busy",
    "action.invalid": "minecraft.action_invalid",
    "action.unknown": "minecraft.action_invalid",
    "action.failed": "minecraft.action_failed",
    "path.not_found": "minecraft.path_not_found",
    "player.not_found": "minecraft.player_not_found",
    "player.lost": "minecraft.player_lost",
    "follow.target_too_far": "minecraft.follow_target_too_far",
    "session.active": "minecraft.action_busy",
}

#: 一句话活动（§二十二：SUCCEEDED → minecraft.activity）。只写事实，不写情绪。
_ACTIVITY_TEMPLATES: dict[str, str] = {
    "move_to": "刚走到 {where}",
    "follow_player": "刚结束跟随 {who}",
    "look_at": "刚看向 {where}",
    "chat": "刚在服务器里说过话",
    "stop": "刚把 Minecraft 行动停下来了",
}

_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT"})


# ------------------------------------------------------------------ 判定


@dataclass(frozen=True)
class PolicyDecision:
    """一次 Tool 调用的放行结论（``code``/``message`` 只在拒绝时非空）。"""

    allowed: bool
    tool: str
    risk: str
    code: str = ""
    message: str = ""


@dataclass(frozen=True)
class GateFacts:
    """判定所需的事实快照（由 bridge 采集；policy 自己不碰 Service/网络）。"""

    minecraft_enabled: bool = False
    online: bool = False
    #: 正在运行的独占动作（runtime 动作名）；空 = 空闲
    busy: str = ""
    explicit_intent: bool = False
    #: 当前语义世界模型里的附近玩家名（空元组 = 感知不可用，不做该检查）
    nearby_players: tuple[str, ...] = ()
    #: 本轮来源（仅供日志/审计；判定只读 ``explicit_intent``，绝不读它）
    turn_origin: str = ""


class MinecraftActionPolicy:
    """Minecraft Tool 的唯一硬门（任务书 §十二/§十四/§十五）。"""

    def __init__(
        self, tools: MinecraftAgentToolsConfig, logger: logging.Logger | None = None
    ) -> None:
        self.tools = tools
        self._log = logger or log

    def risk_of(self, tool: str) -> str:
        return ACTION_RISK.get(tool, "")

    def allowed_by_config(self, tool: str) -> bool:
        """纯配置视角的放行（不含在线/忙碌/意图）——WebUI 只读展示也用它。"""
        risk = self.risk_of(tool)
        if not risk:
            return False
        return bool(self.tools.enabled and getattr(self.tools, RISK_FLAGS[risk], False))

    def check(
        self,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        facts: GateFacts | None = None,
    ) -> PolicyDecision:
        """按固定顺序判定（§十二）：Tool → Enabled → Online → Risk → Intent → Busy → Context。"""
        facts = facts or GateFacts()
        origin = facts.turn_origin
        risk = self.risk_of(tool)
        if not risk:
            return self._reject(
                tool,
                risk,
                "minecraft.action_invalid",
                f"未注册的 Minecraft 工具：{tool}",
                turn_origin=origin,
            )
        if not facts.minecraft_enabled:
            return self._reject(
                tool, risk, "minecraft.disabled", "Minecraft 连接层未启用", turn_origin=origin
            )
        if not self.tools.enabled:
            return self._reject(
                tool, risk, "minecraft.disabled", "Minecraft 工具未启用", turn_origin=origin
            )
        if tool in NEEDS_ONLINE and not facts.online:
            return self._reject(
                tool,
                risk,
                "minecraft.offline",
                "罐头现在不在 Minecraft 世界里",
                turn_origin=origin,
            )
        if not getattr(self.tools, RISK_FLAGS[risk], False):
            return self._reject(
                tool,
                risk,
                "minecraft.action_not_allowed",
                f"{risk} 级动作未获允许",
                turn_origin=origin,
            )
        if risk in EXPLICIT_INTENT_RISKS and not facts.explicit_intent:
            return self._reject(
                tool,
                risk,
                "minecraft.action_not_allowed",
                "这需要用户明确要求；不要自己决定移动罐头（可以先用 minecraft_world 看看情况）",
                turn_origin=origin,
            )
        if tool in EXCLUSIVE_TOOLS and facts.busy:
            return self._reject(
                tool,
                risk,
                "minecraft.action_busy",
                f"当前正在执行 Minecraft 行动（{facts.busy}）；"
                "如需打断，先用 minecraft_stop 停止它",
                turn_origin=origin,
            )
        target = str((arguments or {}).get("username", "")).strip()
        if (
            tool == "minecraft_follow_player"
            and target
            and facts.nearby_players
            and target not in facts.nearby_players
        ):
            # §四十二：目标必须真的在附近——不猜、不拼、不跟随看不见的玩家
            known = "、".join(facts.nearby_players[:5]) or "（没有其他人）"
            return self._reject(
                tool,
                risk,
                "minecraft.player_not_found",
                f"附近没有叫「{target}」的玩家；现在能看到的只有：{known}",
                turn_origin=origin,
            )
        self._log.info(
            "[MC Policy] allowed tool=%s risk=%s turn_origin=%s",
            tool,
            risk,
            facts.turn_origin or "unknown",
        )
        return PolicyDecision(allowed=True, tool=tool, risk=risk)

    def _reject(
        self, tool: str, risk: str, code: str, message: str, *, turn_origin: str = ""
    ) -> PolicyDecision:
        self._log.info(
            "[MC Policy] rejected tool=%s risk=%s turn_origin=%s code=%s",
            tool,
            risk,
            turn_origin or "unknown",
            code,
        )
        return PolicyDecision(allowed=False, tool=tool, risk=risk, code=code, message=message)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.tools.enabled),
            "risk_flags": {
                level: bool(getattr(self.tools, flag, False)) for level, flag in RISK_FLAGS.items()
            },
            "registered": dict(ACTION_RISK),
        }


# ------------------------------------------------------------------ 上下文


class MinecraftAgentContext:
    """当前/最近动作与在线状态（§十九/§二十二/§二十四）。

    只由事件驱动：``RUNNING`` 不会被自己「模拟完成」，终态只来自 runtime 的 action 事件。
    世界事实（坐标/附近玩家）由 bridge 在需要时向感知层现取，这里不快照、不进历史。
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.online: bool = False
        self.username: str = ""
        self.current_action: dict[str, Any] | None = None
        self.last_action: dict[str, Any] | None = None
        self.activity: str = ""
        self.updated_at: float = 0.0

    # ------------------------------------------------------------- 事件入口

    def apply_event(self, event: MinecraftBridgeEvent) -> None:
        name = event.type_name
        self.updated_at = self._clock()
        if name == "minecraft.spawned":
            self.online = True
            self.username = str(event.data.get("username") or self.username)
            return
        if name in ("minecraft.connecting", "minecraft.connected"):
            self.online = False
            if name == "minecraft.connecting":
                self.current_action = None
            return
        if name in ("minecraft.disconnected", "minecraft.kicked"):
            # 断线/踢出：runtime 会取消所有动作，本地也必须清掉「正在做」
            self.online = False
            self.current_action = None
            return
        if not name.startswith("minecraft.action."):
            return
        data = dict(event.data)
        status = str(data.get("status") or "")
        if name == "minecraft.action.started":
            self.current_action = {
                "action": data.get("action"),
                "action_id": data.get("action_id"),
                "status": status or "RUNNING",
                "started_at": data.get("started_at"),
            }
            return
        record = self._terminal_record(data, status)
        self.current_action = None
        self.last_action = record
        if status == "SUCCEEDED":
            self.activity = self._describe(record)

    def _terminal_record(self, data: dict[str, Any], status: str) -> dict[str, Any]:
        return {
            "action": data.get("action"),
            "action_id": data.get("action_id"),
            "status": status or "FAILED",
            "code": RUNTIME_ERROR_CODES.get(str(data.get("code") or ""), ""),
            "error": str(data.get("error") or ""),
            "result": data.get("result") if isinstance(data.get("result"), dict) else None,
            "at": self._clock(),
        }

    @staticmethod
    def _describe(record: Mapping[str, Any]) -> str:
        template = _ACTIVITY_TEMPLATES.get(
            str(record.get("action") or ""), "刚做完一个 Minecraft 动作"
        )
        result = record.get("result") or {}
        where = _format_position(result.get("final_position") or result.get("target"))
        who = str(result.get("username") or "").strip()
        try:
            return template.format(where=where or "目标位置", who=who or "对方")
        except (KeyError, IndexError):  # pragma: no cover - 模板是常量，坏不了
            return "刚做完一个 Minecraft 动作"

    # ------------------------------------------------------------- 只读投影

    def snapshot(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "username": self.username,
            "current_action": dict(self.current_action) if self.current_action else None,
            "last_action": dict(self.last_action) if self.last_action else None,
            "activity": self.activity,
            "updated_at": self.updated_at,
        }


def _format_position(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    try:
        return f"({float(value['x']):.0f}, {float(value['y']):.0f}, {float(value['z']):.0f})"
    except (KeyError, TypeError, ValueError):
        return ""


# -------------------------------------------------------------------- 桥


class MinecraftAgentBridge:
    """六个 Minecraft Tool 的唯一入口：判定 → Service → 结构化结果。"""

    def __init__(
        self, service: MinecraftService, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.service = service
        self._clock = clock
        self.policy = MinecraftActionPolicy(service.config.agent.tools)
        self.context = MinecraftAgentContext(clock=clock)

    # ------------------------------------------------------------ 事实采集

    @property
    def enabled(self) -> bool:
        return bool(self.service.enabled and self.service.config.agent.tools.enabled)

    def world_view(self) -> dict[str, Any]:
        """只读世界视图（语义模型；raw snapshot 绝不外泄给 LLM）。"""
        return self.service.world_view()

    def world_facts(self) -> dict[str, Any]:
        """从感知层现取世界事实（在线/维度/坐标/附近玩家）。"""
        view = self.world_view()
        semantic = view.get("semantic") or {}
        self_state = semantic.get("self") or {}
        players = [
            {
                "name": str(player.get("name") or ""),
                "distance": player.get("distance"),
                "direction": player.get("direction"),
                "position": player.get("position"),
            }
            for player in semantic.get("players") or []
            if player.get("name")
        ]
        online = bool(view.get("online")) and str(self_state.get("dimension") or "") != ""
        if online:
            self.context.online = True
        return {
            "available": bool(view.get("available")),
            "online": online,
            "dimension": self_state.get("dimension"),
            "position": self_state.get("position"),
            "biome": self_state.get("location"),
            "players": players,
            "age_seconds": view.get("age_seconds"),
        }

    def gate_facts(self, *, explicit_intent: bool = False, turn_origin: str = "") -> GateFacts:
        world = self.world_facts()
        current = self.context.current_action or {}
        busy = ""
        if str(current.get("status") or "") == "RUNNING":
            busy = str(current.get("action") or "")
        return GateFacts(
            minecraft_enabled=bool(self.service.enabled),
            online=bool(world["online"]) or self.context.online,
            busy=busy,
            explicit_intent=explicit_intent,
            nearby_players=tuple(player["name"] for player in world["players"]),
            turn_origin=turn_origin,
        )

    # ------------------------------------------------------------ 判定/调用

    def check(
        self, tool: str, arguments: Mapping[str, Any] | None = None, *, context: ToolContext
    ) -> PolicyDecision:
        return self.policy.check(
            tool,
            arguments,
            self.gate_facts(
                explicit_intent=explicit_intent(context), turn_origin=turn_origin(context)
            ),
        )

    def denial(self, tool: str, decision: PolicyDecision) -> ToolResult:
        """策略拒绝 → 结构化失败（§二十七）。"""
        return _failure(tool, decision.code, decision.message)

    async def invoke(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        call: Callable[[MinecraftService], Awaitable[dict[str, Any]]],
        *,
        context: ToolContext,
    ) -> ToolResult:
        """判定 → 调 Service → 结构化结果；任何异常都不外泄给模型。"""
        decision = self.check(tool, arguments, context=context)
        if not decision.allowed:
            return self.denial(tool, decision)
        log.info("[MC Tool] requested tool=%s args=%s", tool, _preview(arguments))
        try:
            payload = await call(self.service)
        except MinecraftBridgeError as exc:
            code = _stable_code(getattr(exc, "code", ""))
            log.info("[MC Tool] failed tool=%s code=%s", tool, code)
            return _failure(tool, code, str(exc))
        except Exception as exc:  # noqa: BLE001 - 工具层永不抛给模型
            log.exception("[MC Tool] crashed tool=%s", tool)
            return _failure(
                tool, "minecraft.action_failed", f"Minecraft 调用失败（{type(exc).__name__}）"
            )
        return self._success(tool, payload)

    def _success(self, tool: str, payload: Mapping[str, Any]) -> ToolResult:
        action = str(payload.get("action") or TOOL_ACTION.get(tool, tool))
        status = str(payload.get("status") or "SUCCEEDED")
        data: dict[str, Any] = {"ok": True, "action": action, "status": status}
        action_id = payload.get("action_id")
        if action_id:
            data["action_id"] = str(action_id)
        if isinstance(payload.get("result"), dict):
            data["result"] = payload["result"]
        if tool == "minecraft_stop":
            data["cancelled"] = list(payload.get("cancelled") or [])
            data["status"] = str(payload.get("status") or "IDLE")
        log.info(
            "[MC Action] tool=%s action_id=%s status=%s",
            tool,
            data.get("action_id") or "-",
            data["status"],
        )
        return ToolResult(
            tool_name=tool,
            success=True,
            data=data,
            summary=_summarize(tool, data),
            metadata={"source_type": "minecraft", "confidence": 0.9},
        )

    # ------------------------------------------------------------ 事件入口

    def apply_event(self, event: MinecraftBridgeEvent) -> None:
        """订阅 MinecraftService 的事件流：只更新上下文，绝不触发新的 Agent Turn（§二十一）。"""
        self.context.apply_event(event)
        if event.type_name.startswith("minecraft.action."):
            data = event.data
            log.info(
                "[MC Action] event=%s action=%s action_id=%s status=%s",
                event.type_name.removeprefix("minecraft.action."),
                data.get("action"),
                data.get("action_id") or "-",
                data.get("status") or "-",
            )

    # ------------------------------------------------------------ 只读投影

    def context_line(self, *, limit: int = 240) -> str:
        """每轮注入的紧凑上下文（§十九/§四十五：一行，绝不塞历史、绝不塞 raw snapshot）。"""
        parts: list[str] = []
        world = self.world_facts()
        if world["online"]:
            where = _format_position(world["position"]) or "未知坐标"
            biome = str(world["biome"] or "").strip()
            parts.append(
                f"罐头正在 Minecraft 里（{world['dimension'] or 'overworld'}，{where}"
                + (f"，{biome}" if biome else "")
                + "）"
            )
            players = world["players"]
            if players:
                parts.append(
                    "附近玩家："
                    + "、".join(
                        f"{player['name']}（{player['distance']}格，{player['direction']}）"
                        for player in players[:4]
                    )
                )
            else:
                parts.append("附近没有其他玩家")
            current = self.context.current_action
            if current:
                target = ""
                result = (self.context.last_action or {}).get("result") or {}
                if isinstance(result, dict) and result.get("username"):
                    target = f" {result['username']}"
                parts.append(f"正在做：{current.get('action')}{target}（{current.get('status')}）")
            last = self.context.last_action
            if last and str(last.get("status")) != "SUCCEEDED":
                detail = last.get("code") or last.get("error") or last.get("status")
                parts.append(f"上一次动作没成功：{last.get('action')}（{detail}）")
            elif self.context.activity:
                parts.append(self.context.activity)
        elif self.service.enabled:
            parts.append("罐头现在不在 Minecraft 服务器里")
        if not parts:
            return ""
        line = "；".join(parts) + "。"
        return line[:limit]

    def snapshot(self, *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """WebUI 只读投影：可用工具 + 风险/允许 + 当前上下文（§三十/§四十八）。"""
        world = self.world_facts()
        return {
            "enabled": self.enabled,
            "context": {
                **self.context.snapshot(),
                **{
                    key: world[key]
                    for key in ("available", "dimension", "position", "biome", "players")
                },
            },
            "policy": self.policy.snapshot(),
            "tools": tools or [],
        }


# ------------------------------------------------------------------ 工具侧辅助


def explicit_intent(context: ToolContext) -> bool:
    """本轮是否由用户明确发起（结构性事实；Tool Layer 不做 NLP —— 任务书 §十六）。

    该键只能由 :class:`~app.character.turn.TurnOrigin` 派生（Phase 3E.1）；
    读不到 = 不允许 LOW 动作（fail-closed）。
    """
    return bool(context.metadata.get(INTENT_KEY, False))


def turn_origin(context: ToolContext) -> str:
    """本轮来源（只用于日志/审计；缺失时返回空串，判定不读它）。"""
    value = context.metadata.get(TURN_ORIGIN_KEY, "")
    return str(getattr(value, "value", value) or "")


def bridge_from(context: ToolContext) -> MinecraftAgentBridge | None:
    bridge = context.metadata.get(BRIDGE_KEY)
    return bridge if isinstance(bridge, MinecraftAgentBridge) else None


def _stable_code(code: Any) -> str:
    text = str(code or "")
    if text.startswith("minecraft."):
        return text
    return RUNTIME_ERROR_CODES.get(text, "minecraft.action_failed")


def _failure(tool: str, code: str, message: str) -> ToolResult:
    return ToolResult(
        tool_name=tool,
        success=False,
        error=message,
        error_type=code,
        data={"ok": False, "error": {"code": code, "message": message}},
        metadata={"source_type": "minecraft", "confidence": 0.0},
    )


def _preview(arguments: Mapping[str, Any], limit: int = 120) -> str:
    parts = [f"{key}={value!r}" for key, value in list(arguments.items())[:6]]
    text = " ".join(parts)
    return text[:limit]


def _summarize(tool: str, data: Mapping[str, Any]) -> str:
    action = data.get("action")
    status = data.get("status")
    if tool == "minecraft_stop":
        cancelled = data.get("cancelled") or []
        if cancelled:
            return (
                f"已停止正在进行的 Minecraft 行动（{', '.join(str(item) for item in cancelled)}）。"
            )
        return "当前没有正在进行的 Minecraft 行动（无需停止）。"
    if status == "RUNNING":
        return f"{action} 已开始（action_id={data.get('action_id')}），完成与否会由事件告知。"
    where = _format_position((data.get("result") or {}).get("final_position"))
    if where:
        return f"{action} 已完成，到达 {where}。"
    return f"{action} 已完成（{status}）。"
