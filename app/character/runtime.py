"""CharacterRuntime: the face users talk to.

One entry point for the chat surface::

    reply = await runtime.respond(event, text, history_provider=...)

It composes persona + narrative state + relationship + relevant memories
into a prompt (ContextBuilder), gets a reply from the AIEngine, screens it
(ResponseProcessor), then hands back text. Memory extraction is scheduled by
the caller after the reply is delivered.

Degrade rules (spec v0.3 §53): memory unavailable → chat without memories;
state unavailable → defaults; persona reload failure → keep last persona.

本文件同时引用 v0.3 / v0.6 / v0.7 / v0.8 §n（角色+工具+Agent 混版）。"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from app.ai.errors import AIError
from app.ai.models import AIRequest, ChatMessage
from app.character.context import CharacterContextBuilder
from app.character.relationship import RelationshipManager
from app.character.response import CharacterResponseProcessor
from app.character.state import StateManager
from app.character.turn import TurnOrigin
from app.memory.manager import MemoryManager
from app.utils.narrator import narrate

if TYPE_CHECKING:
    from app.ai.engine import AIEngine
    from app.character.persona import Persona
    from app.character.persona_manager import PersonaManager
    from app.memory.extraction import MemoryExtractor

__all__ = ["CharacterRuntime"]

#: intent class -> console phrasing (structured decision, never raw thoughts)
THOUGHT_TEXT = {
    "simple": "这句就是聊天，自己回",
    "tool_assisted": "这句要用一下工具",
    "multi_step": "这句得拆成几步来做",
    "long_running": "这句像是长期的事",
}


class CharacterRuntime:
    def __init__(
        self,
        persona_manager: PersonaManager,
        engine: AIEngine,
        memory_manager: MemoryManager | None = None,
        extractor: MemoryExtractor | None = None,
        database: Any = None,
        topics: Any = None,
        tools: Any = None,
        agent: Any = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._log = logger or logging.getLogger("CatooBot.Character")
        self.personas = persona_manager
        self.engine = engine
        self.memory = memory_manager
        self.extractor = extractor
        self.states = StateManager(database, logger=self._log, clock=clock)
        self.relationships = RelationshipManager(database, logger=self._log, clock=clock)
        self.topics = topics  # optional TopicManager: boosts related memories
        self.tools = tools  # optional ToolRuntime: enables contextual tool use
        self.agent = agent  # optional AgentRuntime: enables multi-step goals
        self.sandbox: Any = None  # optional SandboxRuntime (v2.0): her life
        # Phase 3E：Minecraft Agent Bridge（LLM Tool 的唯一入口；Bot 装配）
        self.minecraft_agent: Any = None
        # Phase 5C：Minecraft 身份桥 + 持久世界记忆（Bot 装配；只提供上下文）
        self.minecraft_memory: Any = None
        # Phase 6A：世界活动（只读：她"现在在做什么"由 Bot 装配的 ActivityRuntime 提供）
        self.activity: Any = None
        self.builder = CharacterContextBuilder()
        self.processor = CharacterResponseProcessor(logger=self._log)
        self.expression_store: Any = None  # optional ExpressionStore (Task 22)
        self.expression_config: Any = None  # optional ExpressionConfig (Task 22)
        self.metrics: Any = None  # optional Metrics (Task 22 counters)
        self._clock = clock

    # ------------------------------------------------------------- bootstrap

    async def start(self) -> None:
        await self.personas.load()
        await self.states.load()
        persona = self.personas.persona
        if persona.is_configured():
            self._log.info("Character active: %s", persona.identity.name or persona.name)
        else:
            self._log.info("Character persona is empty — configure it in the WebUI (/character)")

    # ------------------------------------------------------------ main chat

    async def respond(
        self,
        session_id: str,
        user_id: int | str,
        user_text: str,
        *,
        turn_origin: TurnOrigin,
        origin_metadata: dict[str, str] | None = None,
        history: list[ChatMessage] | None = None,
        user_name: str | None = None,
        is_group: bool = False,
        temperature: float | None = None,
        time_context=None,
        extra_instruction: str | None = None,
        record_interaction: bool = True,
        media_context: str = "",
        continuity: dict | None = None,
        interaction_profile: Any = None,
        shared_experiences: list | None = None,
        context_trace: dict | None = None,
        facts_query: str | None = None,
    ) -> str:
        """Generate one character reply (already screened). Raises AIError.

        ``turn_origin``（Phase 3E.1）是**必填**的回合来源：工具层的 LOW Minecraft 动作
        只允许在 :attr:`TurnOrigin.USER` 回合里执行，而来源绝不能从 ``user_text`` 猜
        （主动发言的文本长得跟用户消息一样）。调用方必须如实声明；不确定就传
        :attr:`TurnOrigin.BACKGROUND`（fail-closed）。

        ``origin_metadata``（Phase 4A）是**运输层事实**（例如「这条消息来自游戏内玩家
        RinsoraNeko」）：它会进工具上下文，供信任门等策略使用。它**不能**覆盖由
        ``turn_origin`` 派生的意图键（派生值最后写入，见 :meth:`_tool_context`）。
        """
        persona = self.personas.persona
        state = await self.states.load()
        if record_interaction:
            relationship = await self.relationships.record_interaction(user_id)
            await self._note_world_interaction(session_id, user_id)
            # Phase 6A §八：把"有人在跟她说话"记成一次观察（**只记录**，不改活动 ——
            # QQ / 游戏内聊天都不能控制 Activity，§三十八/§三十九）。
            await self._note_activity_interaction(session_id)
        else:
            relationship = await self.relationships.get(user_id)
        memories = await self._safe_memories(session_id, user_text, relationship.stage)

        facts = ""
        sandbox = getattr(self, "sandbox", None)
        if sandbox is not None and getattr(sandbox, "enabled", False):
            query = facts_query if facts_query is not None else user_text
            try:
                facts = sandbox.facts_block(query)
            except Exception:  # noqa: BLE001 - facts are an aid, never a blocker
                self._log.debug("Sandbox facts unavailable", exc_info=True)

        expressions = await self._expression_context(session_id, is_group)

        # Phase 5: the sandbox half of cognition (read-only; sandbox off → None).
        # The retrieval query follows the same hint as the facts lookup, so a
        # proactive opener (topic/motivation) retrieves from her own life too.
        sandbox_context = None
        if sandbox is not None and getattr(sandbox, "enabled", False):
            bridge_query = facts_query if facts_query is not None else user_text
            try:
                cognitive = await sandbox.cognitive_context(
                    query=bridge_query, relationship_target=str(user_id)
                )
                sandbox_context = cognitive.as_prompt_payload()
            except Exception:  # noqa: BLE001 - the bridge must never break chat
                self._log.debug("Cognitive context unavailable", exc_info=True)

        # Phase 5C：把她「记得的 Minecraft 事情」接在世界处境后面（≤5 条、检索时
        # 用只读工具复核过）。**只是上下文** —— 它不参与任何权限判断（§二/§五十二）。
        minecraft_context = self._minecraft_context()
        memory_block = await self._minecraft_memory_context(
            session_id=session_id, user_id=user_id, text=user_text
        )
        # Phase 6A §四十：她"现在在做什么"（活动上下文；预算 1 条 + ≤3 条变化）
        activity_context = await self._activity_context()
        # Phase 6C §五十九：她"接下来打算做什么"（计划上下文；≤3 条，措辞上明确"不是现状"）
        plan_context = await self._plan_context()
        if memory_block:
            minecraft_context = (
                f"{minecraft_context}\n{memory_block}" if minecraft_context else memory_block
            )

        messages = self.builder.build(
            persona,
            state,
            relationship,
            memories,
            history or [],
            user_text,
            user_name=user_name,
            is_group=is_group,
            time_context=time_context,
            extra_instruction=extra_instruction,
            world=await self._world_context(),
            minecraft=minecraft_context,
            activity=activity_context,
            plan=plan_context,
            media_context=media_context,
            facts=facts,
            expressions=expressions,
            continuity=continuity,
            interaction_profile=interaction_profile,
            shared_experiences=shared_experiences,
            context_trace=context_trace,
            sandbox_context=sandbox_context,
        )
        temp = temperature if temperature is not None else self.engine.config.default_temperature
        started = time.perf_counter()
        where = f"（{state.location}）" if state.location else ""
        narrate().thought(
            "在想着怎么回",
            detail=(
                f"历史 {len(history or [])} 条 · 记忆 {len(memories)} 条"
                f" · 她正在{state.activity or '发呆'}{where}"
                f" · 心情 {state.mood} · 精力 {state.energy:.0%}"
            ),
        )
        content_text = await self._generate(
            messages,
            temp,
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            is_group=is_group,
            time_context=time_context,
            turn_origin=turn_origin,
            origin_metadata=origin_metadata,
        )
        content = self.processor.sanitize(content_text.strip())
        if content and sandbox is not None and getattr(sandbox, "enabled", False):
            try:
                content = sandbox.audit_claims(content)
            except Exception:  # noqa: BLE001 - audit must never break a reply
                self._log.debug("Sandbox claim audit failed", exc_info=True)
        if not content:
            self._log.warning("[AI] Empty response for session=%s", session_id)
            narrate().warn("模型返回了空内容", detail=f"session={session_id}")
            raise AIError("Empty AI response")
        narrate().flow(
            "想法成形",
            detail=f"{time.perf_counter() - started:.1f}s · {len(content)} 字"
            + (
                f" · 心情 {await self._current_mood()}"
                if getattr(self, "sandbox", None) is not None
                else ""
            ),
        )
        return content

    async def _current_mood(self) -> str:
        try:
            return self.states.state.mood
        except Exception:  # noqa: BLE001 - cosmetic only
            return ""

    # ---------------------------------------------------------- generation

    async def _generate(
        self,
        messages: list[ChatMessage],
        temperature: float,
        *,
        session_id: str,
        user_id: int | str,
        user_text: str,
        is_group: bool,
        turn_origin: TurnOrigin,
        origin_metadata: dict[str, str] | None = None,
        time_context: Any = None,
    ) -> str:
        """One AI turn; when tools are on, the bounded tool loop runs here.

        Tool failures never propagate: the model is told the tool failed and
        answers honestly (spec v0.6 §65/§66). Tool results are never written to
        memory (v0.6 §29/§61) — only the reply text returns.
        """
        tool_context = self._tool_context(
            session_id=session_id,
            user_id=user_id,
            time_context=time_context,
            is_group=is_group,
            turn_origin=turn_origin,
            origin_metadata=origin_metadata,
        )
        if self.agent is not None and getattr(self.agent, "enabled", False):
            agent_text = await self._try_agent(
                messages,
                temperature,
                session_id=session_id,
                user_id=user_id,
                user_text=user_text,
                context=tool_context,
            )
            if agent_text is not None:
                return agent_text

        if self.tools is None or not getattr(self.tools, "enabled", False):
            response = await self.engine.chat(AIRequest(messages=messages, temperature=temperature))
            self._narrate_thinking(response)
            return response.content

        try:
            text, results = await self.tools.orchestrator.run(
                self.engine,
                messages,
                context=tool_context,
                query=user_text,
                temperature=temperature,
            )
        except AIError:
            raise
        except Exception:  # noqa: BLE001 - a tool path bug must not break chat
            self._log.exception("Tool orchestration failed; falling back to plain chat")
            response = await self.engine.chat(AIRequest(messages=messages, temperature=temperature))
            self._narrate_thinking(response)
            return response.content
        if results:
            self._log.info(
                "[Tool] turn used %d tool call(s): %s",
                len(results),
                ", ".join(f"{r.tool_name}={'ok' if r.success else 'err'}" for r in results),
            )
        return text

    @staticmethod
    def _narrate_thinking(response: Any) -> None:
        """Show the model's thinking excerpt in the terminal (console only).

        Never written to the log file and never persisted — hidden
        chain-of-thought stays out of storage (project policy).
        """
        excerpt = str(getattr(response, "reasoning", "") or "").strip()
        if not excerpt:
            return
        one_line = " ".join(excerpt.split())
        if len(one_line) > 220:
            one_line = one_line[:220] + "…"
        narrate().thinking(one_line, detail="模型自述（仅控制台，不落盘）")

    # --------------------------------------------------------------- agent

    def _tool_context(
        self,
        *,
        session_id: str,
        user_id: int | str,
        time_context: Any,
        is_group: bool,
        turn_origin: TurnOrigin,
        origin_metadata: dict[str, str] | None = None,
        group_id: int | None = None,
    ) -> Any:
        """Tool context carries only what a tool legitimately needs (spec v0.6 §30).

        ``turn_origin``（Phase 3E.1）在这里落成两个键：``turn_origin``（日志/排查）
        与派生的 ``minecraft_explicit_intent``（意图门唯一读的那个布尔）。

        ``origin_metadata``（Phase 4A）是运输层事实（如 ``minecraft_player``）。它在
        **派生键之前**合并：调用方塞进来的任何意图标记都会被派生值覆盖 —— 授权不可能
        靠参数伪造。
        """
        from app.tools.models import ToolContext

        metadata: dict[str, Any] = dict(self._life_metadata())
        metadata.update(origin_metadata or {})
        if self.memory is not None:
            # read-only retrieval handle for query_image_memory — the tool goes
            # through the manager, never the raw database.
            metadata["memory"] = self.memory
        bridge = getattr(self, "minecraft_agent", None)
        if bridge is not None:
            # 延迟导入：Minecraft 模块（aiohttp/桥接）只在真的接了游戏时才进这条路径
            from app.integrations.minecraft.agent import INTENT_KEY, TURN_ORIGIN_KEY

            # Phase 3E：六个 Minecraft Tool 的唯一入口（判定 → Service → 结构化结果）
            metadata["minecraft"] = bridge
            # 意图门（§十五）：**只有用户回合**算"用户明确要求"（Phase 3E.1）。
            # 这个布尔由 turn_origin 派生，调用方无法手写；主动发言/后台/系统回合
            # 一律为 False → LOW 动作被拒（fail-closed）。
            metadata[TURN_ORIGIN_KEY] = turn_origin.value  # 仅日志/排查，不进 LLM
            metadata[INTENT_KEY] = turn_origin.is_user
        return ToolContext(
            user_id=str(user_id),
            group_id=str(group_id) if group_id is not None else None,
            session_id=session_id,
            character_name=self.personas.persona.identity.name,
            locale="zh-CN",
            timezone=getattr(time_context, "timezone", "") or "Asia/Shanghai",
            current_datetime=getattr(time_context, "local_time", ""),
            is_group=is_group,
            # Read-only view of her life for tools/agent (v0.6 §18): they may know
            # what she is doing, but only the sandbox ever writes it.
            metadata=metadata,
        )

    def _life_metadata(self) -> dict[str, str]:
        sandbox = getattr(self, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            return {}
        try:
            line = sandbox.context().get("state_line", "")
        except Exception:  # noqa: BLE001 - her life is an optional input
            self._log.debug("Sandbox metadata unavailable", exc_info=True)
            return {}
        return {"world": line} if line else {}

    async def _try_agent(
        self,
        messages: list[ChatMessage],
        temperature: float,
        *,
        session_id: str,
        user_id: int | str,
        user_text: str,
        context: Any,
    ) -> str | None:
        """Run the agent when the message is genuinely multi-step.

        Returns the user-facing text, or None when the agent should not handle
        this turn (simple chat / tool-assisted / not eligible).
        """
        agent = self.agent
        try:
            control = await agent.handle_control(user_text, session_id)
            if control:
                return await self._control_reply(messages, temperature, control)

            candidates = self.tools.registry.candidates(user_text) if self.tools is not None else []
            classification = agent.classifier.classify(user_text, tool_candidates=len(candidates))
            narrate().thought(
                THOUGHT_TEXT.get(classification.kind, classification.kind),
                detail=(
                    f"相关工具 {len(candidates)} 个 · {classification.reason}"
                    if candidates
                    else classification.reason
                ),
            )
            if classification.kind != "multi_step" or not agent.can_handle("multi_step"):
                return None

            self._log.info(
                "[Agent] routing to agent runtime (%s): %.60s",
                classification.reason,
                user_text,
            )
            result = await agent.run(
                user_text,
                session_id=session_id,
                user_id=str(user_id),
                context=context,
                classification=classification,
            )
            await self._note_agent_result(result, session_id=session_id, user_id=user_id)
            if result.status == "failed" and result.error_type in (
                "planning_failed",
                "plan_invalid",
            ):
                # The goal was unclear (e.g. no location): let the character ask
                # the clarifying question instead of announcing a failure (v0.7 §111).
                self._log.info(
                    "[Agent] goal unclear (%s); answering as normal chat",
                    result.error_type,
                )
                narrate().task("目标不够清楚，改成直接问她", detail=result.error_type)
                return None
            if not result.is_usable:
                narrate().task("这次没查出东西，按普通聊天回", detail=result.status)
                return None  # nothing to say beyond the normal reply
            prompt = (  # facts travel as untrusted reference data (spec v0.7 §109)
                result.to_prompt_block() + "\n\n请基于以上结果，用你自己的语气自然地回答用户。"
                "不要提及工具、计划、步骤或任务。"
            )
            response = await self.engine.chat(
                AIRequest(
                    messages=[*messages, ChatMessage.user(prompt)],
                    temperature=temperature,
                )
            )
            self._narrate_thinking(response)
            return response.content
        except AIError:
            raise
        except Exception:  # noqa: BLE001 - agent trouble must never break chat
            self._log.exception("Agent turn failed; falling back to plain chat")
            narrate().warn("规划这条路走不通，退回普通聊天")
            return None

    async def _control_reply(
        self, messages: list[ChatMessage], temperature: float, control: str
    ) -> str:
        """Natural acknowledgement of cancel/pause/resume (spec v0.7 §97/§98/§99)."""
        hints = {
            "cancel": (
                "用户刚打断了你正在做的事（不用继续了）。用一句话自然回应，不要再执行任何任务。"
            ),
            "pause": "用户让你先停一下。用一句话自然回应，表示可以先放一放。",
        }
        key = control.split(":")[0]
        hint = hints.get(key, "用户让你继续刚才的事。用一句话自然回应，表示你接着去看。")
        response = await self.engine.chat(
            AIRequest(
                messages=[*messages, ChatMessage.user(f"（系统提示：{hint}）")],
                temperature=temperature,
            )
        )
        self._narrate_thinking(response)
        return response.content

    # ---------------------------------------------------- proactive speech

    async def compose_initiative(
        self,
        *,
        session_id: str,
        user_id: int | str,
        reason: str,
        topic: str = "",
    ) -> str:
        """Generate a proactive opener the character has a *reason* to send.

        Returns "" when nothing sensible could be produced — the caller then
        skips sending (spec v0.8 §60: no reason, no message).
        """
        reason_hint = {
            "unfinished_topic": "你想接着上次没聊完的话题说一句话",
            "long_absence": "你们有段时间没聊了，你想随口打个招呼",
            "life_event": "你自己生活里刚发生了点小事，想随口跟对方提一句",
        }.get(reason, "你想主动找对方说句话")
        instruction_parts = [
            "[主动发言] 这是你自己主动找对方说话，不是回复。",
            f"动机：{reason_hint}。",
        ]
        if topic:
            instruction_parts.append(f"话题：{topic}。自然地提起来，别像客服回访。")
        instruction_parts.append(
            "只说这件事本身；不要补充库存、数量或其他世界状态（除非上面的世界事实里有）。"
        )
        instruction_parts.append(
            "只发一句话，非常短（20 字以内），像随手发的一条消息，不要提问式结尾堆叠。"
        )
        history = await self.engine.conversations.get_context(session_id)
        try:
            text = await self.respond(
                session_id,
                user_id,
                "（主动发起）",
                # §八：她是自己开的口，不是用户请求 —— 哪怕正文里出现"过来"也不放行 LOW
                turn_origin=TurnOrigin.INITIATIVE,
                history=history,
                extra_instruction="".join(instruction_parts),
                record_interaction=False,
                facts_query=f"{topic} {reason_hint}",
            )
        except AIError as exc:
            self._log.warning("[Initiative] Generation failed (%s): %s", reason, exc)
            return ""
        if len(text) > 60:  # proactive messages stay short
            text = text[:60].rstrip()
        return text

    # ---------------------------------------------------------------- admin

    async def reload_persona(self) -> Persona:
        return await self.personas.load()

    async def set_activity(
        self, activity: str | None = None, current_focus: str | None = None
    ) -> None:
        await self.states.update(activity=activity, current_focus=current_focus)

    # ---------------------------------------------------------------- world

    @staticmethod
    def _memory_platform(session_id: str) -> str:
        """会话 → 身份平台名（和 :data:`PLATFORMS` 同口径）。"""
        return "minecraft_chat" if str(session_id).startswith("minecraft:") else "qq"

    async def _minecraft_memory_context(
        self, *, session_id: str, user_id: int | str, text: str
    ) -> str:
        """一次 turn 的「相关 Minecraft 记忆」块（没有记忆/不在世界里 → 空串）。"""
        bridge = getattr(self, "minecraft_memory", None)
        if bridge is None:
            return ""
        try:
            return str(
                await bridge.context_block(
                    text=text,
                    platform=self._memory_platform(session_id),
                    user_id=str(user_id or ""),
                )
            )
        except Exception:  # noqa: BLE001 - 记忆只是上下文，绝不拖垮对话
            self._log.debug("Minecraft memory context unavailable", exc_info=True)
            return ""

    async def _activity_context(self) -> str:
        """她此刻的活动（Phase 6A §四十）。拿不到就什么都不加，绝不拖垮对话。"""
        runtime = getattr(self, "activity", None)
        if runtime is None:
            return ""
        try:
            return str(await runtime.context_block())
        except Exception:  # noqa: BLE001 - 活动上下文只是上下文
            self._log.debug("Activity context unavailable", exc_info=True)
            return ""

    async def _plan_context(self) -> str:
        """她"接下来打算做什么"（Phase 6C §五十九）——**计划**，不是现状。

        与 :meth:`_activity_context` 分开取：§七十二 要求"我现在还在处理 Minecraft 任务"
        和"计划完成后休息一下"是两个答案，所以两块上下文各自成型、绝不合写。
        拿不到（没有计划/活动层关了）就什么都不加，绝不拖垮对话。
        """
        runtime = getattr(self, "activity", None)
        if runtime is None:
            return ""
        try:
            return str(await runtime.plan_context_block())
        except Exception:  # noqa: BLE001 - 计划上下文只是上下文
            self._log.debug("Plan context unavailable", exc_info=True)
            return ""

    def _minecraft_context(self) -> str:
        """她此刻在 Minecraft 里的一行处境（Phase 3E §十九；没有就完全静默）。"""
        bridge = getattr(self, "minecraft_agent", None)
        if bridge is None or not getattr(bridge, "enabled", False):
            return ""
        try:
            return bridge.context_line()
        except Exception:  # noqa: BLE001 - 游戏状态绝不拖垮聊天
            self._log.debug("Minecraft context unavailable", exc_info=True)
            return ""

    async def _world_context(self) -> dict | None:
        """Everything the prompt legitimately needs about her own life.

        The v2.0 sandbox is the source of truth (``sandbox.context()``).
        """
        sandbox = getattr(self, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            return None
        try:
            return sandbox.context()
        except Exception:  # noqa: BLE001 - sandbox trouble must not affect chat
            self._log.debug("Sandbox context unavailable", exc_info=True)
            return None

    async def _expression_context(self, session_id: str, is_group: bool) -> str:
        """Task 22: this group's learned phrases, injected as an advisory note."""
        if not is_group or self.expression_store is None or self.expression_config is None:
            return ""
        if not session_id.startswith("group:"):
            return ""
        try:
            from app.expression import expression_context

            block, _ = await expression_context(
                self.expression_store,
                self.expression_config,
                session_id.split(":", 1)[-1],
                now=int(self._clock()),
                metrics=self.metrics,
            )
            return block
        except Exception:  # noqa: BLE001 - learning is advisory, never a blocker
            self._log.debug("Expression context unavailable", exc_info=True)
            return ""

    async def _note_world_interaction(self, session_id: str, user_id: int | str) -> None:
        sandbox = getattr(self, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            return
        try:
            await sandbox.note_user_interaction(user_id=str(user_id), session_id=session_id)
        except Exception:  # noqa: BLE001
            self._log.debug("Sandbox interaction note failed", exc_info=True)

    async def _note_activity_interaction(self, session_id: str) -> None:
        """把一次用户交互交给 Activity 层（只写观察；失败绝不影响聊天，§三十八）。"""
        runtime = getattr(self, "activity", None)
        if runtime is None:
            return
        try:
            await runtime.observe(
                {
                    "user_interaction_at": time.time(),
                    "last_interaction_source": "qq",
                    "last_interaction_session": session_id,
                }
            )
        except Exception:  # noqa: BLE001 - 交互记录失败绝不能影响回复
            self._log.debug("Activity interaction note failed", exc_info=True)

    async def _note_agent_result(self, result: Any, *, session_id: str, user_id: int | str) -> None:
        """Task finished → her life notes it; the agent never writes state."""
        sandbox = getattr(self, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            return
        status = getattr(result, "status", "")
        done = status in ("completed", "partial")
        text = "帮人把一件事办完了" if done else "有件事折腾半天没办成"
        try:
            await sandbox.note_agent_result(
                task_type=getattr(result, "goal_id", "") or "agent_task",
                status=status,
                summary=text,
                session_id=session_id,
                user_id=str(user_id),
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Sandbox agent-result note failed", exc_info=True)

    # ------------------------------------------------------------ internals

    async def _safe_memories(
        self, session_id: str, query: str, relationship_stage: str = ""
    ) -> list:
        """Hybrid retrieval with topic + relationship signals; never raises."""
        if self.memory is None:
            return []
        topic_titles: list[str] = []
        if self.topics is not None:
            try:
                key = session_id.replace("private:", "user:")
                topics = await self.topics.get_active_topics(key, limit=5)
                topic_titles = [topic.title for topic in topics]
            except Exception:  # noqa: BLE001 - topics are an optional signal
                self._log.debug("Topic lookup for retrieval failed", exc_info=True)
        try:
            return await self.memory.retrieve_for_session(
                session_id,
                query,
                relationship_stage=relationship_stage,
                topic_titles=topic_titles,
            )
        except Exception:  # noqa: BLE001 - memory trouble must not break chat
            self._log.exception("Memory retrieval failed; continuing without")
            return []
