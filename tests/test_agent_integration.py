"""v0.7 end-to-end: QQ message → classifier → agent → character reply.

Verifies the promises that matter to the user:

* multi-step questions go through the agent and come back as *one* natural reply
  (no plan, no tool names, no task ids in the message);
* simple chat and single-tool questions never spin up the agent;
* cancel / pause / resume work through natural language;
* agent facts are reference data for the character, not a new persona;
* a failed agent task still produces an in-character, honest answer.
"""

from __future__ import annotations

import json

from app.agent.runtime import AgentRuntime
from app.ai.engine import AIEngine
from app.config.settings import AIConfig, AppConfig
from app.tools.builtins import WeatherTool
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.conftest import group_event, make_bot, private_event
from tests.test_agent import LocationAwareWeather, plan_payload, tool_step


async def make_agent_bot(tmp_path, replies: list[str], **agent_overrides):
    """A bot whose chat path can reach the agent runtime."""
    provider = MockAIProvider(behaviors={"A": replies})
    bot = make_bot(tmp_path)
    config = AppConfig(
        bot={"name": "TestBot"},
        database={"url": f"sqlite:///{tmp_path / 'chat.db'}"},
        logging={"log_dir": str(tmp_path / "logs")},
        behavior={"reply": {"enabled": False}},
        tools={"enabled": True, "decision_mode": "json"},
        agent=agent_overrides or {},
    )
    bot.config = config
    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        bot.database,
        providers={"mock": provider},
    )
    bot.ai = engine
    bot.character.engine = engine
    if bot.character.extractor is not None:
        bot.character.extractor.engine = engine
        bot.character.extractor.config.extraction.enabled = False

    await bot.database.connect()
    if bot.continuity is not None:
        await bot.continuity.start()
    bot.config.conversation.debounce.direct_message_ms = 20
    bot.config.conversation.debounce.group_message_ms = 30
    bot.tools = ToolRuntime(config.tools, bot.database)
    await bot.tools.start()
    bot.tools.registry.unregister("weather")
    bot.tools.registry.register(WeatherTool(providers=[LocationAwareWeather()]))
    bot.character.tools = bot.tools

    bot.agent = AgentRuntime(config.agent, engine, bot.tools, bot.database)
    bot.character.agent = bot.agent
    bot.event_bus.on("message", bot.core_router.on_message)
    await bot.plugins.load_all()
    return bot, provider


MULTI_STEP_QUESTION = "帮我查一下周六和周日天气，然后告诉我哪天适合出去"


class TestMultiStepFlow:
    async def test_plan_runs_and_reply_is_natural(self, tmp_path) -> None:
        plan = plan_payload(
            [
                tool_step("s1", "weather", location="Singapore", days=3),
                {"id": "s2", "description": "比较两天并给建议", "tool": None,
                 "depends_on": ["s1"]},
            ]
        )
        replies = [plan, "周六雨挺大的，周日好一些", "看下来周日更适合出门，天气会稳一点。"]

        bot, provider = await make_agent_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(private_event(MULTI_STEP_QUESTION, user_id=7))
            await bot.conversation.wait_idle()
            text = bot.adapter.sent_texts()[-1]  # type: ignore[attr-defined]

            assert "周日更适合" in text
            # nothing internal leaks into the chat
            for leak in ("Plan", "Step", "step_", "tool", "Task", "agent", "{"):
                assert leak not in text

            tasks = await bot.agent.list_tasks()
            assert len(tasks) == 1 and tasks[0]["status"] == "completed"
            detail = await bot.agent.task_detail(tasks[0]["task_id"])
            assert [plan_row["version"] for plan_row in detail["plans"]] == [1]
            assert len(detail["steps"]) == 2
            assert all(step["status"] == "completed" for step in detail["steps"])
        finally:
            await bot.shutdown()

    async def test_facts_reach_the_character_prompt(self, tmp_path) -> None:
        plan = plan_payload([tool_step("s1", "weather", location="Singapore")])
        replies = [plan, "周六降雨概率很高", "周日比较稳，我建议周日出门。"]

        bot, provider = await make_agent_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(private_event(MULTI_STEP_QUESTION, user_id=7))
            await bot.conversation.wait_idle()
            final_prompt = provider.calls[-1]["messages"][-1].content
            assert "任务结果" in final_prompt
            assert "外部参考数据" in final_prompt
            assert "不要提及工具、计划、步骤或任务" in final_prompt
        finally:
            await bot.shutdown()

    async def test_task_is_persisted_with_steps_and_observations(self, tmp_path) -> None:
        plan = plan_payload([tool_step("s1", "time")])
        replies = [plan, "时间拿到了"]

        bot, _ = await make_agent_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(private_event(MULTI_STEP_QUESTION, user_id=7))
            await bot.conversation.wait_idle()
            tasks = await bot.agent.list_tasks()
            detail = await bot.agent.task_detail(tasks[0]["task_id"])
            assert detail["goal"]["description"] == MULTI_STEP_QUESTION
            assert detail["observations"], "observations must be recorded"
            assert detail["traces"], "trace events must be recorded"
            # traces are structured events — never a hidden chain of thought
            assert all(trace["event"] for trace in detail["traces"])
        finally:
            await bot.shutdown()


class TestRouting:
    async def test_simple_chat_never_creates_a_task(self, tmp_path) -> None:
        bot, provider = await make_agent_bot(tmp_path, ["哈哈，今天确实挺舒服的"])
        try:
            await bot.event_bus.emit(private_event("今天心情不错", user_id=7))
            await bot.conversation.wait_idle()
            assert bot.adapter.sent_texts() == ["哈哈，今天确实挺舒服的"]  # type: ignore[attr-defined]
            assert await bot.agent.list_tasks() == []
            assert len(provider.calls) == 1
        finally:
            await bot.shutdown()

    async def test_single_tool_question_uses_tool_path_not_agent(self, tmp_path) -> None:
        decision = json.dumps(
            {"tool_call": {"name": "weather", "arguments": {"location": "Singapore"}}}
        )
        bot, _ = await make_agent_bot(tmp_path, [decision, "明天有雨，带伞吧。"])
        try:
            await bot.event_bus.emit(private_event("明天天气怎么样", user_id=7))
            await bot.conversation.wait_idle()
            assert "带伞" in bot.adapter.sent_texts()[-1]  # type: ignore[attr-defined]
            assert await bot.agent.list_tasks() == []  # no plan was made
        finally:
            await bot.shutdown()

    async def test_group_message_also_reaches_the_agent(self, tmp_path) -> None:
        plan = plan_payload([tool_step("s1", "time")])
        replies = [plan, "查完了，时间没问题"]
        bot, _ = await make_agent_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(group_event(MULTI_STEP_QUESTION, user_id=9, at_bot=True))
            await bot.conversation.wait_idle()
            tasks = await bot.agent.list_tasks()
            assert len(tasks) == 1
            assert tasks[0]["session_id"].startswith("group:")
        finally:
            await bot.shutdown()


class TestNaturalControls:
    async def test_cancel_phrase_stops_the_task(self, tmp_path) -> None:
        bot, provider = await make_agent_bot(tmp_path, ["好的，那就不查了。"])
        try:
            # a running task that this process knows about
            from app.agent.models import AgentBudget, Goal

            goal = Goal(goal_id="g", session_id="private:7", description="查天气")
            await bot.agent._store_task(  # noqa: SLF001
                "task-live", goal, status="running", classification="multi_step",
                budget=AgentBudget(),
            )
            bot.agent._active_by_session["private:7"] = "task-live"  # noqa: SLF001

            await bot.event_bus.emit(private_event("不用查了", user_id=7))
            await bot.conversation.wait_idle()
            assert (await bot.agent.get("task-live"))["status"] == "cancelled"
            assert "不查了" in bot.adapter.sent_texts()[-1]  # type: ignore[attr-defined]
            assert await bot.agent.list_tasks(status="completed") == []
        finally:
            await bot.shutdown()

    async def test_pause_and_resume_phrases(self, tmp_path) -> None:
        replies = ["好，先放着。", "行，我接着看看。"]
        bot, _ = await make_agent_bot(tmp_path, replies)
        try:
            from app.agent.models import AgentBudget, Goal

            goal = Goal(goal_id="g", session_id="private:8", description="查天气")
            await bot.agent._store_task(  # noqa: SLF001
                "task-pause", goal, status="running", classification="multi_step",
                budget=AgentBudget(),
            )
            bot.agent._active_by_session["private:8"] = "task-pause"  # noqa: SLF001

            await bot.event_bus.emit(private_event("先停一下", user_id=8))
            await bot.conversation.wait_idle()
            assert (await bot.agent.get("task-pause"))["status"] == "paused"

            await bot.event_bus.emit(private_event("继续吧", user_id=8))
            await bot.conversation.wait_idle()
            assert (await bot.agent.get("task-pause"))["status"] == "running"
        finally:
            await bot.shutdown()


class TestFailureHandling:
    async def test_agent_failure_still_replies_in_character(self, tmp_path) -> None:
        """Tool down + no replans → honest answer, no crash, no fabrication (§73)."""
        plan = plan_payload([tool_step("s1", "weather", location="Nowhere")])
        replies = [plan, "唉，我这边没查到，等下再看看？"]

        bot, provider = await make_agent_bot(tmp_path, replies, budget={"max_replans": 0})
        try:
            await bot.event_bus.emit(private_event(MULTI_STEP_QUESTION, user_id=7))
            await bot.conversation.wait_idle()
            text = bot.adapter.sent_texts()[-1]  # type: ignore[attr-defined]
            assert "没查到" in text
            assert "℃" not in text and "80%" not in text  # nothing invented
            # the model was explicitly told the task failed and not to fabricate
            final_prompt = provider.calls[-1]["messages"][-1].content
            assert "未能完成" in final_prompt
            assert "不要编造" in final_prompt
            tasks = await bot.agent.list_tasks()
            assert tasks[0]["status"] == "failed"
        finally:
            await bot.shutdown()

    async def test_planner_garbage_falls_back_to_chat(self, tmp_path) -> None:
        """A planner that returns nonsense must not break the turn (§20)."""
        bot, _ = await make_agent_bot(tmp_path, ["这个我暂时答不上来呢"])
        try:
            await bot.event_bus.emit(private_event(MULTI_STEP_QUESTION, user_id=7))
            await bot.conversation.wait_idle()
            assert bot.adapter.sent_texts()  # something was said
            assert "{" not in bot.adapter.sent_texts()[-1]  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_unclear_goal_asks_instead_of_reporting_failure(self, tmp_path) -> None:
        """No location given → the character asks naturally (spec §111)."""
        unclear = json.dumps({"unclear": True, "reason": "没有说哪个城市"})
        replies = [unclear, "哪个城市的天气呀？我这就去看～"]

        bot, provider = await make_agent_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(
                private_event("帮我查一下周六和周日天气，然后告诉我哪天适合出去玩", user_id=7)
            )
            await bot.conversation.wait_idle()
            # the reply may be delivered as several bubbles — judge the whole answer
            text = "".join(bot.adapter.sent_texts())  # type: ignore[attr-defined]
            assert "哪个城市" in text
            assert "任务" not in text and "失败" not in text
            # the persona prompt still frames the answer (it may sit after the
            # tool-decision block, which the orchestrator inserts first)
            joined = chr(10).join(
                message.content for message in provider.calls[-1]["messages"]
            )
            assert "像真人朋友一样自然聊天" in joined
        finally:
            await bot.shutdown()


class TestPersonaAndMemoryIsolation:
    async def test_agent_does_not_touch_memory_by_itself(self, tmp_path) -> None:
        plan = plan_payload([tool_step("s1", "weather", location="Singapore")])
        replies = [plan, "周六有雨", "周日更适合出门。"]

        bot, _ = await make_agent_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(private_event(MULTI_STEP_QUESTION, user_id=7))
            await bot.conversation.wait_idle()
            await bot.character.extractor.wait_idle() if bot.character.extractor else None
            if bot.memory is not None:
                memories = await bot.memory.list_memories()
                # weather data must never become a long-term fact (§44/§45)
                assert all("降雨" not in memory.content for memory in memories)
                assert all("概率" not in memory.content for memory in memories)
        finally:
            await bot.shutdown()

    async def test_reply_keeps_the_character_voice(self, tmp_path) -> None:
        plan = plan_payload([tool_step("s1", "time")])
        replies = [plan, "嗯…现在这个点啦"]

        bot, provider = await make_agent_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(private_event(MULTI_STEP_QUESTION, user_id=7))
            await bot.conversation.wait_idle()
            system_prompt = provider.calls[-1]["messages"][0].content
            # persona/style rules are still in force for the agent's final answer
            assert "像真人朋友一样自然聊天" in system_prompt
            assert "不要用反问句收尾" in system_prompt
        finally:
            await bot.shutdown()


class TestQQSurface:
    async def test_no_agent_commands_exist(self, tmp_path) -> None:
        bot, _ = await make_agent_bot(tmp_path, ["随便聊聊呗"])
        try:
            for text in ("/agent", "/plan", "/task", "/tools", "/execute"):
                await bot.event_bus.emit(private_event(text, user_id=7))
                await bot.conversation.wait_idle()
            await bot.conversation.wait_idle()
            texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert len(texts) == 5  # every one was treated as ordinary chat
            assert await bot.agent.list_tasks(status="completed") == []
        finally:
            await bot.shutdown()


class TestAgentConfigOff:
    async def test_agent_disabled_keeps_everything_working(self, tmp_path) -> None:
        plan = plan_payload([tool_step("s1", "time")])
        bot, _ = await make_agent_bot(
            tmp_path, [plan, "普通回复"], **{"enabled": False}
        )
        try:
            await bot.event_bus.emit(private_event(MULTI_STEP_QUESTION, user_id=7))
            await bot.conversation.wait_idle()
            assert bot.adapter.sent_texts()  # chat still works
            assert await bot.agent.list_tasks() == []  # but no task was created
        finally:
            await bot.shutdown()


class TestWebsiteAgentPages:
    async def test_dashboard_and_detail_render(self, tmp_path) -> None:
        from app.web.services.agent import AgentAdminService

        plan = plan_payload([tool_step("s1", "time")])
        replies = [plan, "时间拿到了"]
        bot, _ = await make_agent_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(private_event(MULTI_STEP_QUESTION, user_id=7))
            await bot.conversation.wait_idle()
            service = AgentAdminService(bot)

            dashboard = await service.dashboard()
            assert dashboard["metrics"]["total"] >= 1
            assert dashboard["policy"]["enabled"] is True
            assert dashboard["health"]["planner"] == "ok"

            tasks = await service.tasks()
            detail = await service.task_detail(tasks[0]["task_id"])
            assert detail["task"]["status"] == "completed"
            assert detail["plans"]
            assert isinstance(detail["plans"][0]["steps"], list)

            simulation = await service.simulate("查天气然后比较")
            assert simulation["classification"] in ("simple", "tool_assisted", "multi_step")
        finally:
            await bot.shutdown()

    async def test_control_actions(self, tmp_path) -> None:
        from app.web.services.agent import AgentAdminService

        bot, _ = await make_agent_bot(tmp_path, ["好"])
        try:
            from app.agent.models import AgentBudget, Goal

            goal = Goal(goal_id="g", session_id="private:tr", description="查天气")
            await bot.agent._store_task(  # noqa: SLF001
                "task-web", goal, status="running", classification="multi_step",
                budget=AgentBudget(),
            )
            service = AgentAdminService(bot)
            assert (await service.control("pause", "task-web"))["ok"] is True
            assert (await bot.agent.get("task-web"))["status"] == "paused"
            assert (await service.control("resume", "task-web"))["ok"] is True
            assert (await service.control("cancel", "task-web"))["ok"] is True
            assert (await bot.agent.get("task-web"))["status"] == "cancelled"
        finally:
            await bot.shutdown()
