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
        """Generate one character reply (already screened). Raises AIError."""
        persona = self.personas.persona
        state = await self.states.load()
        if record_interaction:
            relationship = await self.relationships.record_interaction(user_id)
            await self._note_world_interaction(session_id, user_id)
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
            media_context=media_context,
            facts=facts,
            expressions=expressions,
            continuity=continuity,
            interaction_profile=interaction_profile,
            shared_experiences=shared_experiences,
            context_trace=context_trace,
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
        time_context: Any = None,
    ) -> str:
        """One AI turn; when tools are on, the bounded tool loop runs here.

        Tool failures never propagate: the model is told the tool failed and
        answers honestly (spec v0.6 §65/§66). Tool results are never written to
        memory (v0.6 §29/§61) — only the reply text returns.
        """
        tool_context = self._tool_context(
            session_id=session_id, user_id=user_id, time_context=time_context, is_group=is_group
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
        group_id: int | None = None,
    ) -> Any:
        """Tool context carries only what a tool legitimately needs (spec v0.6 §30)."""
        from app.tools.models import ToolContext

        metadata: dict[str, Any] = dict(self._life_metadata())
        if self.memory is not None:
            # read-only retrieval handle for query_image_memory — the tool goes
            # through the manager, never the raw database.
            metadata["memory"] = self.memory
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
