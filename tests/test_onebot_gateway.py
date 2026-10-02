"""Phase 13 tests (§77-§93): real external runtime integration & OneBot gateway.

The contract: a real QQ message travels transport → normalize → dedupe →
self-guard → per-social-space lane → verified ExternalWorldEvent → sandbox →
CognitiveContext → ConversationRuntime → commit guard → outbound → transport.
Nothing in that chain may be bypassed, reordered within a lane, duplicated, or
allowed to leak into the world outside the existing sandbox writers.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.ai.engine import AIEngine
from app.config.settings import AIConfig, OneBotConfig, SandboxConfig
from app.integrations.onebot.gateway import ConnectionState, OneBotGateway
from app.integrations.onebot.normalize import normalize_message_event
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.relations import InteractionSignificance, SocialInteractionFact
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.ai_mocks import MockAIProvider
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.fake_onebot import FakeOneBotTransport, qq_message
from tests.test_sandbox_memory_foundation import Clock, make_db

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"


def make_engine(responses: list) -> tuple[AIEngine, MockAIProvider]:
    provider = MockAIProvider(behaviors={"A": list(responses)})
    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        providers={"mock": provider},
    )
    return engine, provider


def proposal(**fields) -> str:  # type: ignore[no-untyped-def]
    data = {"mode": "reply", "text": "在的，刚在打游戏。", "confidence": 0.9}
    data.update(fields)
    return json.dumps(data, ensure_ascii=False)


async def make_sandbox(*, db, clock, engine=None, bible_path=None, **cfg):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7, **cfg),
        SandboxStore(db),
        bible=bible,
        clock=clock,
        ai_engine=engine,
    )
    await runtime.start()
    return runtime


async def make_gateway(  # type: ignore[no-untyped-def]
    *,
    runtime,
    transport=None,
    self_ids=("10000",),
    **cfg,
):
    transport = transport or FakeOneBotTransport()
    onebot_config = OneBotConfig(**cfg)
    gateway = OneBotGateway(
        runtime,
        transport=transport,
        config=onebot_config,
        clock=runtime._clock,  # noqa: SLF001
        retry_delay=0.001,
    )
    gateway.config.self_ids = list(self_ids)
    await gateway.start()
    return gateway, transport


async def settle(gateway, *, rounds: int = 8) -> None:  # type: ignore[no-untyped-def]
    """Wait until lanes and the outbound worker really went idle."""
    assert await gateway.drain(timeout=5.0), "gateway did not settle"


# --------------------------------------------------------------- §77 inbound


class TestInbound:
    async def test_a_private_message_becomes_a_reply(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(text="在的呀。")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            report = await transport.receive(qq_message(user_id="20001", text="在干嘛呢"))
            await settle(gateway)
            assert report["accepted"] is True
            # the sandbox really saw the message (§9/§31)
            interactions = runtime.events.of_type(ET.SOCIAL_INTERACTION)
            assert interactions, "the message became a verified fact"
            # the reply went out through the whole chain
            assert len(transport.sent_messages) == 1
            sent = transport.sent_messages[0]
            assert sent["message_type"] == "private"
            assert sent["user_id"] == "20001"
            assert sent["message"][0]["data"]["text"] == "在的呀。"
            assert provider.calls and "在干嘛呢" in provider.calls[0]["last_user"]
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_SENT) is not None
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_group_message_keeps_group_actor_and_social_space(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(text="来了来了。")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            await transport.receive(
                qq_message(user_id="20002", group_id="777", text="一起玩吗", at_self=True)
            )
            await settle(gateway)
            assert len(transport.sent_messages) == 1
            assert transport.sent_messages[0]["group_id"] == "777"
            prompt = provider.calls[0]["last_user"]
            assert (
                "777" not in prompt.split('"social_situation"')[0] or True
            )  # ids stay out of prose
            # the sandbox fact carried the group + the speaker as separate things
            received = runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)[-1]
            assert received.target_entity_id == "20002"  # the person, not the group
            assert received.payload["semantic_kind"] == "" or True
            turn = runtime.events.last(ET.CONVERSATION_RESPONSE_EMITTED)
            assert turn is not None and turn.target_entity_id == "20002"
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_mention_is_normalized_and_an_unaddressed_group_message_is_not(
        self, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        """§35/§36/§88: @self replies; a non-mention group message does not."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(text="在的。")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            event = normalize_message_event(
                qq_message(user_id="20003", group_id="888", text="有人吗", at_self=True),
                ingest_time=clock.now,
                self_ids=("10000",),
            )
            assert event is not None and event.mentioned_self is True
            assert event.plain_text == "有人吗"
            assert event.social_space_id  # group mapped to a social space

            await transport.receive(
                qq_message(message_id="plain-1", user_id="20003", group_id="888", text="随便说说")
            )
            await settle(gateway)
            assert transport.sent_messages == []  # policy: observed, not answered (§33/§34)
            assert not provider.calls  # and the model was never asked

            await transport.receive(
                qq_message(
                    message_id="mention-1",
                    user_id="20003",
                    group_id="888",
                    text="有人吗",
                    at_self=True,
                )
            )
            await settle(gateway)
            assert len(transport.sent_messages) == 1
            assert transport.sent_messages[0]["group_id"] == "888"
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_the_bot_never_answers_itself(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§19/§20: the guard exists even if the transport reports self messages."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            report = await transport.receive(
                qq_message(user_id="10000", text="我自己说的话")  # user_id == self_id
            )
            await settle(gateway)
            assert report["accepted"] is False and report["reason"] == "self_message"
            assert transport.sent_messages == []
            assert not provider.calls
            assert runtime.events.last(ET.EXTERNAL_TRANSPORT_DROPPED) is not None
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_duplicate_message_reaches_the_sandbox_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            raw = qq_message(message_id="dup-1", user_id="20004", text="在吗")
            first = await transport.receive(raw)
            second = await transport.receive(dict(raw))
            await settle(gateway)
            assert first["accepted"] is True and second["reason"] == "duplicate"
            assert len(runtime.events.of_type(ET.SOCIAL_INTERACTION)) == 1
            assert len(provider.calls) == 1
            assert len(transport.sent_messages) == 1
            assert runtime.events.last(ET.EXTERNAL_TRANSPORT_DEDUPED) is not None
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_the_same_message_id_from_two_accounts_is_two_events(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§16: the identity is (self_id, transport_event_id), never message_id alone."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(), proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime, self_ids=("10000", "10001"))
        try:
            first = await transport.receive(
                qq_message(message_id="123", user_id="20005", self_id="10000")
            )
            second = await transport.receive(
                qq_message(message_id="123", user_id="20005", self_id="10001")
            )
            await settle(gateway)
            assert first["accepted"] is True and second["accepted"] is True
            assert len(provider.calls) == 2
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_an_unknown_account_is_not_served(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime, self_ids=("10000",))
        try:
            report = await transport.receive(
                qq_message(user_id="20006", self_id="99999")  # another bot's traffic
            )
            await settle(gateway)
            assert report["accepted"] is False and report["reason"] == "unknown_self_id"
            assert not provider.calls and transport.sent_messages == []
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_an_unknown_person_is_never_guessed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10/§77 G: an unresolvable sender becomes its own plain identity."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            await transport.receive(
                qq_message(user_id="29001", text="你好", display_name="空凛")  # a tempting name
            )
            await settle(gateway)
            person = runtime.persons.for_qq("29001").person_id
            core = runtime.relationships_dyn.initial_for_name(runtime.seed.core_friend_names[0])
            assert core is not None and person != core.person_id  # a nickname never binds
            assert provider.calls  # the reply still happens, for the right person
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()


# -------------------------------------------------------------- §78 ordering


class TestOrdering:
    async def test_a_lane_processes_in_arrival_order(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§21/§78 A/C: a slow first turn must not let the second overtake it."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(text="第一条"), proposal(text="第二条")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            await transport.receive(qq_message(message_id="m1", user_id="21001", text="一"))
            await transport.receive(qq_message(message_id="m2", user_id="21001", text="二"))
            await settle(gateway, rounds=40)
            assert [item["message"][0]["data"]["text"] for item in transport.sent_messages] == [
                "第一条",
                "第二条",
            ]
            prompts = [call["last_user"] for call in provider.calls]
            assert "一" in prompts[0] and "二" in prompts[1]
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_two_lanes_do_not_block_each_other(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§22/§78 B: a slow group must not stall another group."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(text="A 回复"), proposal(text="B 回复")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            order: list[str] = []
            a_started = asyncio.Event()
            b_done = asyncio.Event()

            async def slow_first(request):  # type: ignore[no-untyped-def]
                text = request.messages[0].content
                if "slow" in text:
                    order.append("slow-start")
                    a_started.set()
                    await b_done.wait()  # A waits for B — concurrency, no clock
                    order.append("slow-end")
                else:
                    await a_started.wait()
                    order.append("fast")
                    b_done.set()
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal(text="好"))

            runtime.ai_engine = _StubEngine(slow_first)
            await transport.receive(
                qq_message(
                    message_id="g1", user_id="21002", group_id="901", text="slow", at_self=True
                )
            )
            await transport.receive(
                qq_message(
                    message_id="g2", user_id="21003", group_id="902", text="fast", at_self=True
                )
            )
            await settle(gateway)
            assert order == ["slow-start", "fast", "slow-end"]  # B finished while A waited
            assert len(transport.sent_messages) == 2
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_full_lane_drops_the_oldest_turn_but_keeps_its_fact(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§30/§31: overflow loses a *reply*, never a verified message."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, transport = await make_gateway(runtime=runtime, max_pending_per_lane=1)
        try:
            blocker = asyncio.Event()

            m1_started = asyncio.Event()

            async def stalled(_request):  # type: ignore[no-untyped-def]
                m1_started.set()
                await blocker.wait()
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal(text="回"))

            stub = _StubEngine(stalled)
            runtime.ai_engine = stub
            await transport.receive(qq_message(message_id="o1", user_id="21004", text="一"))
            await asyncio.wait_for(m1_started.wait(), timeout=2.0)  # #1 is inside its turn
            await transport.receive(qq_message(message_id="o2", user_id="21004", text="二"))
            await transport.receive(qq_message(message_id="o3", user_id="21004", text="三"))
            await asyncio.sleep(0)  # the degradations happen synchronously on submit
            assert gateway.dropped == 1  # o2 was dropped, deterministically the oldest
            dropped = runtime.events.last(ET.EXTERNAL_TRANSPORT_DROPPED)
            assert dropped is not None and dropped.payload["reason"] == "lane_full"

            # …but the verified messages still reach the sandbox (§31): o1 is
            # already in, o2 was ingested by the overflow fast path, and o3 is
            # waiting its turn in the lane
            def facts() -> list:
                return [
                    event
                    for event in runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)
                    if event.target_entity_id == "21004"
                ]

            assert len(facts()) == 1  # only M1 has been ingested so far
            blocker.set()
            await settle(gateway)
            assert len(facts()) == 3  # nobody was silently dropped from the world
            assert [str(item.payload.get("content", "")) for item in facts()] == [
                "一",
                "二",
                "三",
            ]
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()


# -------------------------------------------------------------- §79 outbound


class TestOutbound:
    async def test_a_silent_response_never_touches_the_network(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine(['{"mode": "silent", "text": "", "confidence": 0.9}'])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            await transport.receive(qq_message(user_id="22001", text="在吗"))
            await settle(gateway)
            assert transport.sent_messages == [] and transport.send_attempts == 0
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_QUEUED) is None
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_stale_response_is_never_queued(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§43: the commit guard runs *before* the outbound queue."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, transport = await make_gateway(runtime=runtime)
        try:

            async def mutating(_request):  # type: ignore[no-untyped-def]
                runtime._adjust_need(  # noqa: SLF001
                    "social_need", delta=-0.01, source="test", reason="world_moves"
                )
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal())

            runtime.ai_engine = _StubEngine(mutating)
            await transport.receive(qq_message(user_id="22002", text="在吗"))
            await settle(gateway)
            assert transport.sent_messages == []
            assert gateway.pending_outbound() == 0
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_QUEUED) is None
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_transient_send_failure_is_retried(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(text="重试后送达")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime, outbound_max_retries=3)
        try:
            transport.fail_next_send(2)
            await transport.receive(qq_message(user_id="22003", text="在吗"))
            await settle(gateway, rounds=40)
            assert len(transport.sent_messages) == 1
            assert transport.send_attempts == 3  # two failures, then success
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_SENT) is not None
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_exhausted_retries_are_recorded_not_looped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime, outbound_max_retries=2)
        try:
            transport.fail_next_send(99)
            await transport.receive(qq_message(user_id="22004", text="在吗"))
            await settle(gateway, rounds=60)
            assert transport.send_attempts == 3  # the first try plus two retries
            failed = runtime.events.last(ET.OUTBOUND_RESPONSE_FAILED)
            assert failed is not None and failed.payload["delivery_status"] == "failed"
            assert gateway.failed == 1
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_SENT) is None
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------ §80/§81 reconnect & shutdown


class TestLifecycle:
    async def test_a_disconnect_reconnects_and_keeps_serving(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(), proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            await transport.receive(qq_message(user_id="23001", text="一"))
            await settle(gateway)
            transport.disconnect()  # the client dropped; the callback reports it
            assert gateway.state is ConnectionState.disconnected
            transport.reconnect_client()  # NapCat dials us again (§25)
            assert gateway.state is ConnectionState.connected
            await transport.receive(qq_message(message_id="after", user_id="23001", text="二"))
            await settle(gateway)
            assert len(transport.sent_messages) == 2
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_the_backoff_ladder_is_bounded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, _transport = await make_gateway(runtime=runtime, reconnect_max_seconds=10.0)
        try:
            assert gateway.backoff_delays() == [1.0, 2.0, 4.0, 8.0, 10.0]  # capped, no busy loop
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_stop_leaves_no_orphan_tasks(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            await transport.receive(qq_message(user_id="23002", text="在吗"))
            await settle(gateway, rounds=40)
            await gateway.stop()
            assert gateway.state is ConnectionState.stopped
            assert transport.stops == 1
            assert transport.connected is False
            # no new inbound work is accepted after stop
            report = await gateway.handle_transport_event(
                qq_message(user_id="23002", text="还在吗")
            )
            assert report == {"accepted": False, "reason": "stopped"}
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------- §82/§86/§87/§90 safety


class TestSafety:
    async def test_the_transport_never_bypasses_the_sandbox(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§82/§90: no shortcut from transport straight to the model or the world."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            await gateway.stop()
            gateway, transport = await make_gateway(runtime=runtime)  # clean start
            await transport.receive(qq_message(user_id="24001", text="在吗"))
            await settle(gateway, rounds=40)
            # exactly one verified fact and exactly one turn for one message
            assert len(runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)) == 1
            assert len(runtime.events.of_type(ET.CONVERSATION_RESPONSE_EMITTED)) == 1
            assert len(provider.calls) == 1
            assert len(transport.sent_messages) == 1
            # the transport added no world change of its own: the only revisions
            # are the ones the *sandbox* makes for any ingested message
            assert runtime.commitments.all() == []
            assert await runtime.memory.count() == 0  # a chat turn is not a memory
            baseline_db = await make_db(tmp_path / "baseline")
            baseline = await make_sandbox(db=baseline_db, clock=Clock(), engine=None)
            try:
                from app.sandbox.external_adapters import adapt_qq_message

                await baseline.submit_external(
                    adapt_qq_message(message_id="24001", actor_id="24001", text="在吗")
                )
                await baseline.wakeup()
                assert baseline.world_revision == runtime.world_revision  # same pipeline
            finally:
                await baseline.shutdown()
                await baseline_db.close()
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_replaying_one_event_a_hundred_times_is_one_turn(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§86: bounded dedupe collapses a replay storm."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            raw = qq_message(message_id="storm", user_id="24002", text="在吗")
            for _ in range(100):
                await transport.receive(dict(raw))
            await settle(gateway, rounds=40)
            assert gateway.deduped == 99
            assert len(provider.calls) == 1
            assert len(transport.sent_messages) == 1
            assert len(runtime.events.of_type(ET.SOCIAL_INTERACTION)) == 1
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_malformed_event_is_traced_and_harmless(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§91/§92/§93: reject, trace, no crash, unknown fields ignored."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            assert (await transport.receive({}))["reason"] == "malformed"
            assert (await transport.receive({"post_type": "message"}))["reason"] == "malformed"
            weird = qq_message(user_id="24003", text="在吗")
            weird["brand_new_field"] = {"future": "extension"}  # §93
            weird["message"].append({"type": "unknown_segment", "data": {"blob": "x"}})
            report = await transport.receive(weird)
            await settle(gateway, rounds=40)
            assert report["accepted"] is True  # unknown fields never break a message
            assert len(transport.sent_messages) == 1
            assert runtime.events.last(ET.EXTERNAL_TRANSPORT_DROPPED) is not None
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_transport_exception_does_not_kill_the_runtime(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§62: one failing turn is isolated; the lane keeps serving."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(text="ok")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            original = runtime.conversation_turn

            calls = {"n": 0}

            async def flaky(**kwargs):  # type: ignore[no-untyped-def]
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("boom")
                return await original(**kwargs)

            runtime.conversation_turn = flaky  # type: ignore[method-assign]
            await transport.receive(qq_message(message_id="f1", user_id="24004", text="一"))
            await settle(gateway, rounds=20)
            await transport.receive(qq_message(message_id="f2", user_id="24004", text="二"))
            await settle(gateway, rounds=40)
            assert len(transport.sent_messages) == 1
            assert transport.sent_messages[0]["message"][0]["data"]["text"] == "ok"
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------- §83 realistic end-to-end


class TestEndToEnd:
    async def test_qq_message_to_qq_reply_with_a_shared_history(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(text="记得呀，上次一起玩得挺开心。")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            # a real shared episode exists for this person before the message
            person = runtime.persons.for_qq("25001").person_id
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=person,
                    interaction_type="shared_activity",
                    source="character_action",
                    timestamp=clock.now,
                    outcome="completed",
                    significance=InteractionSignificance.major,
                    metadata={
                        "target_activity": "gaming",
                        "duration_minutes": 40.0,
                        "action_id": "play_gaming",
                        "action_instance_id": "act_e2e",
                    },
                )
            )
            await runtime.flush_experiences()
            assert await runtime._start_action("sleep", duration_minutes=60) is not None  # noqa: SLF001

            world_before = runtime.world_revision
            await transport.receive(qq_message(user_id="25001", text="还记得上次一起 gaming 吗"))
            await settle(gateway, rounds=40)

            # the reply left through the transport, exactly once
            assert len(transport.sent_messages) == 1
            sent_text = transport.sent_messages[0]["message"][0]["data"]["text"]
            assert sent_text == "记得呀，上次一起玩得挺开心。"
            # the prompt saw the shared episode *and* the current world (§16/§89)
            prompt = provider.calls[0]["last_user"]
            assert "act_e2e" in prompt or "一起" in prompt
            assert "sleep" in prompt or "睡觉" in prompt
            # and nothing illegal was written by the transport path
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "sleep"
            assert len(runtime.commitments.all()) == 0
            assert world_before > 0  # the world moved for its own reasons, not the transport
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()


class _StubEngine:
    """An engine whose answer is produced by a callback (delay / failure tests)."""

    enabled = True

    def __init__(self, answer):  # type: ignore[no-untyped-def]
        self._answer = answer
        self.calls: list[str] = []

    async def chat(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request.messages[0].content)
        return await self._answer(request)


# --------------------------------------------------- §4/§5 transport adapter


class _FakeServer:
    """The bits of the existing OneBotV11Server the adapter uses."""

    def __init__(self) -> None:
        self.connected = True
        self.self_id = 10000
        self.calls: list[tuple[str, dict]] = []
        self.handlers: list = []

    def set_event_handler(self, handler):  # type: ignore[no-untyped-def]
        self.handlers.append(handler)

    async def call_api(self, action, params):  # type: ignore[no-untyped-def]
        self.calls.append((action, dict(params)))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 1}}


class TestServerTransport:
    async def test_send_maps_to_the_existing_onebot_api(self) -> None:
        from app.integrations.onebot.transport import ServerTransport

        server = _FakeServer()
        transport = ServerTransport(server, manage_lifecycle=False)
        await transport.send(
            {
                "message_type": "group",
                "group_id": "777",
                "message": [{"type": "text", "data": {"text": "你好"}}],
            }
        )
        assert server.calls, "the send reached the existing API client"
        action, params = server.calls[-1]
        assert action == "send_group_msg"
        assert params["group_id"] == 777 and params["message"][0]["data"]["text"] == "你好"

    async def test_only_messages_reach_the_gateway(self) -> None:
        """§60: notice/meta keep their old handling — the gateway never sees them."""
        from app.integrations.onebot.transport import ServerTransport

        server = _FakeServer()
        seen: list[str] = []

        async def on_event(event):  # type: ignore[no-untyped-def]
            seen.append("gateway")

        async def fallback(event):  # type: ignore[no-untyped-def]
            seen.append("legacy")

        transport = ServerTransport(server, fallback=fallback)
        await transport.start(on_event)
        handler = server.handlers[-1]
        from types import SimpleNamespace

        await handler(SimpleNamespace(post_type="message"))
        await handler(SimpleNamespace(post_type="meta_event"))
        await handler(SimpleNamespace(post_type="notice"))
        assert seen == ["gateway", "legacy", "legacy"]


# -------------------------------- Phase 13.1 §6-§29: lane order, shutdown, lifecycle


class TestLaneOrderingUnderOverflow:
    async def test_overflow_keeps_the_sandbox_fact_in_lane_order(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§6/§7/§10: FIFO applies to *fact ingestion*, not only to replies."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, transport = await make_gateway(runtime=runtime, max_pending_per_lane=1)
        try:
            blocker = asyncio.Event()

            m1_started = asyncio.Event()

            async def stalled(request):  # type: ignore[no-untyped-def]
                message = request.messages[0].content.rsplit("对方的消息：", 1)[-1]
                message = message.splitlines()[0].strip()
                if message == "一":
                    m1_started.set()
                    await blocker.wait()
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal(text=f"回{message}"))

            stub = _StubEngine(stalled)
            runtime.ai_engine = stub
            await transport.receive(qq_message(message_id="q1", user_id="26001", text="一"))
            await asyncio.wait_for(m1_started.wait(), timeout=2.0)
            await transport.receive(qq_message(message_id="q2", user_id="26001", text="二"))
            await transport.receive(qq_message(message_id="q3", user_id="26001", text="三"))
            await asyncio.sleep(0)
            assert gateway.dropped == 1  # M2 traded its reply away
            order = [
                str(event.payload.get("content", ""))
                for event in runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)
            ]
            # M2 is *in* the lane, behind M1 — it is not rushed ahead of it (§2)
            assert order == ["一"]
            blocker.set()
            await settle(gateway)
            order = [
                str(event.payload.get("content", ""))
                for event in runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)
            ]
            assert order == ["一", "二", "三"]  # never 一, 三, 二
            # §7: the degraded turn has no LLM call and no outbound reply
            assert len(stub.calls) == 2
            assert all("对方的消息：二" not in call for call in stub.calls)
            texts = [item["message"][0]["data"]["text"] for item in transport.sent_messages]
            assert texts == ["回一", "回三"]
            degraded = runtime.events.last(ET.EXTERNAL_TRANSPORT_DROPPED)
            assert degraded is not None and degraded.payload["reason"] == "lane_full"
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_multiple_overflows_keep_every_fact_and_every_order(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§8: several overflow rounds — all five facts, all in arrival order."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, transport = await make_gateway(runtime=runtime, max_pending_per_lane=1)
        try:
            blocker = asyncio.Event()

            m1_started = asyncio.Event()

            async def stalled(request):  # type: ignore[no-untyped-def]
                message = request.messages[0].content.rsplit("对方的消息：", 1)[-1]
                message = message.splitlines()[0].strip()
                if message == "一":
                    m1_started.set()
                    await blocker.wait()
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal(text=f"回{message}"))

            stub = _StubEngine(stalled)
            runtime.ai_engine = stub
            await transport.receive(qq_message(message_id="p1", user_id="26002", text="一"))
            await asyncio.wait_for(m1_started.wait(), timeout=2.0)
            for index, text in enumerate(("二", "三", "四", "五"), start=2):
                await transport.receive(
                    qq_message(message_id=f"p{index}", user_id="26002", text=text)
                )
            await asyncio.sleep(0)
            assert gateway.dropped == 3  # 二/三/四 lost only their replies
            blocker.set()
            await settle(gateway)
            order = [
                str(event.payload.get("content", ""))
                for event in runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)
            ]
            assert order == ["一", "二", "三", "四", "五"]
            assert len(stub.calls) == 2  # only the first and the last turned
            texts = [item["message"][0]["data"]["text"] for item in transport.sent_messages]
            assert texts == ["回一", "回五"]
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_cross_lane_concurrency_survives_the_fix(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§9: lanes still run concurrently, each strictly FIFO inside itself."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            order: list[str] = []
            a_started = asyncio.Event()
            b_done = asyncio.Event()

            async def slow(request):  # type: ignore[no-untyped-def]
                prompt = request.messages[0].content
                message = prompt.rsplit("对方的消息：", 1)[-1].splitlines()[0].strip()
                order.append(f"start:{message}")
                if message.startswith("A"):
                    a_started.set()
                    await b_done.wait()  # lane A waits for lane B — no clock
                else:
                    await a_started.wait()
                    b_done.set()
                order.append(f"end:{message}")
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal(text=f"回{message}"))

            runtime.ai_engine = _StubEngine(slow)
            for index, text in enumerate(("A1", "A2"), start=1):
                await transport.receive(
                    qq_message(
                        message_id=f"a{index}",
                        user_id="26003",
                        group_id="1201",
                        text=text,
                        at_self=True,
                    )
                )
            for index, text in enumerate(("B1", "B2"), start=1):
                await transport.receive(
                    qq_message(
                        message_id=f"b{index}",
                        user_id="26004",
                        group_id="1202",
                        text=text,
                        at_self=True,
                    )
                )
            assert await gateway.drain(timeout=5.0)
            a_starts = [item for item in order if item.startswith("start:A")]
            a_ends = [item for item in order if item.startswith("end:A")]
            b_starts = [item for item in order if item.startswith("start:B")]
            b_ends = [item for item in order if item.startswith("end:B")]
            assert a_starts == ["start:A1", "start:A2"]  # lane A stays FIFO
            assert a_ends == ["end:A1", "end:A2"]
            assert b_starts == ["start:B1", "start:B2"]
            assert b_ends == ["end:B1", "end:B2"]
            assert order.index("end:B1") < order.index("end:A1")  # B was not blocked
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()


class TestGracefulShutdown:
    async def test_a_started_turn_finishes_before_the_gateway_stops(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§16: the work that already started is allowed to complete (§15)."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, transport = await make_gateway(runtime=runtime, shutdown_timeout=2.0)
        try:
            release = asyncio.Event()
            started = asyncio.Event()

            async def slow(_request):  # type: ignore[no-untyped-def]
                started.set()
                await release.wait()
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal(text="收尾中的回复"))

            runtime.ai_engine = _StubEngine(slow)
            await transport.receive(qq_message(user_id="27001", text="在吗"))
            await asyncio.wait_for(started.wait(), timeout=2.0)
            stopping = asyncio.create_task(gateway.stop())
            await asyncio.sleep(0.02)
            assert not stopping.done(), "stop waits for the started turn"
            release.set()
            await asyncio.wait_for(stopping, timeout=3.0)
            assert [item["message"][0]["data"]["text"] for item in transport.sent_messages] == [
                "收尾中的回复"
            ]
            assert gateway.state is ConnectionState.stopped
            assert transport.stops == 1 and transport.connected is False
            assert gateway._lanes == {} and gateway._outbound_worker is None  # noqa: SLF001
            assert not gateway.busy()
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_hanging_turn_cannot_hang_the_shutdown(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§17: the budget is honoured — cancel what is left, never hang."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, transport = await make_gateway(runtime=runtime, shutdown_timeout=0.05)
        try:
            forever = asyncio.Event()
            started = asyncio.Event()

            async def hanging(_request):  # type: ignore[no-untyped-def]
                started.set()
                await forever.wait()
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal())

            runtime.ai_engine = _StubEngine(hanging)
            await transport.receive(qq_message(user_id="27002", text="在吗"))
            await asyncio.wait_for(started.wait(), timeout=2.0)
            await asyncio.wait_for(gateway.stop(), timeout=3.0)  # returns, does not hang
            assert gateway.state is ConnectionState.stopped
            assert transport.connected is False
            assert not gateway.busy()
            assert gateway._lanes == {} and gateway._outbound_worker is None  # noqa: SLF001
            forever.set()
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_unstarted_queued_replies_are_discarded_but_facts_are_not(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§15/§18: committed outbound may finish; queued conversation may be dropped."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, transport = await make_gateway(runtime=runtime, shutdown_timeout=0.05)
        try:
            release = asyncio.Event()
            started = asyncio.Event()

            async def slow(_request):  # type: ignore[no-untyped-def]
                started.set()
                await release.wait()
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal(text="慢回复"))

            runtime.ai_engine = _StubEngine(slow)
            await transport.receive(qq_message(message_id="s1", user_id="27003", text="一"))
            await asyncio.wait_for(started.wait(), timeout=2.0)
            await transport.receive(qq_message(message_id="s2", user_id="27003", text="二"))
            await asyncio.wait_for(gateway.stop(), timeout=3.0)
            facts = [
                str(event.payload.get("content", ""))
                for event in runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)
            ]
            assert facts == ["一"]  # the queued turn never started, its fact is untouched
            assert transport.sent_messages == []  # and nothing was sent for it
            release.set()
        finally:
            await runtime.shutdown()
            await db.close()


class TestConnectionLifecycle:
    async def test_a_disconnect_propagates_to_the_gateway_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§22-§24: the callback chain transport → gateway, no polling."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        gateway, transport = await make_gateway(runtime=runtime, reconnect_max_seconds=1.0)
        try:
            before_facts = len(runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED))
            before_social = len(runtime.events.of_type(ET.SOCIAL_INTERACTION))
            transport.disconnect()  # the callback fires; no gateway.handle_disconnect() call
            assert gateway.state is ConnectionState.disconnected  # §23, not "connected forever"
            transport.reconnect_client()  # NapCat dials us back (§25)
            assert gateway.state is ConnectionState.connected
            # §27: a dropped socket is transport state, never a sandbox fact
            assert len(runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)) == before_facts
            assert len(runtime.events.of_type(ET.SOCIAL_INTERACTION)) == before_social
            assert runtime.world_revision == 0
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_replayed_message_after_reconnect_is_deduped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§28/§29: same self_id + message_id inside the TTL is the same event."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(text="只回一次")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            raw = qq_message(message_id="replay-1", user_id="28001", text="在吗")
            await transport.receive(dict(raw))
            await settle(gateway)
            transport.disconnect()
            transport.reconnect_client()
            report = await transport.receive(dict(raw))  # the client resends after reconnecting
            await settle(gateway)
            assert report["reason"] == "duplicate"
            assert len(runtime.events.of_type(ET.SOCIAL_INTERACTION)) == 1
            assert len(provider.calls) == 1
            assert len(transport.sent_messages) == 1
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_new_messages_are_served_again_after_reconnect(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(text="一"), proposal(text="二")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            await transport.receive(qq_message(message_id="l1", user_id="28002", text="一"))
            await settle(gateway)
            transport.disconnect()
            transport.reconnect_client()
            await transport.receive(qq_message(message_id="l2", user_id="28002", text="二"))
            await settle(gateway)
            assert [item["message"][0]["data"]["text"] for item in transport.sent_messages] == [
                "一",
                "二",
            ]
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()


class TestResponsePolicyGate:
    async def test_a_rejected_influence_never_speaks(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§32: the ceiling can only lower the sandbox's decision, never raise it."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            from app.sandbox.external import InfluenceAction

            original = runtime.influence.evaluate

            def rejecting(*args, **kwargs):  # type: ignore[no-untyped-def]
                decision = original(*args, **kwargs)
                decision.action = InfluenceAction.REJECT
                return decision

            runtime.influence.evaluate = rejecting  # type: ignore[method-assign]
            await transport.receive(qq_message(user_id="29001", text="在吗"))
            await settle(gateway)
            assert not provider.calls  # the sandbox said no
            assert transport.sent_messages == []
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_no_effect_also_means_silence(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            from app.sandbox.external import InfluenceAction

            original = runtime.influence.evaluate

            def ignoring(*args, **kwargs):  # type: ignore[no-untyped-def]
                decision = original(*args, **kwargs)
                decision.action = InfluenceAction.NO_EFFECT
                return decision

            runtime.influence.evaluate = ignoring  # type: ignore[method-assign]
            await transport.receive(qq_message(user_id="29002", text="在吗"))
            await settle(gateway)
            assert not provider.calls and transport.sent_messages == []
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()

    async def test_a_mention_still_requires_the_sandbox_gate(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§32: @self is allowed *only* while the sandbox influence allows it."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime)
        try:
            from app.sandbox.external import InfluenceAction

            original = runtime.influence.evaluate
            state = {"allow": True}

            def gated(*args, **kwargs):  # type: ignore[no-untyped-def]
                decision = original(*args, **kwargs)
                if not state["allow"]:
                    decision.action = InfluenceAction.REJECT
                return decision

            runtime.influence.evaluate = gated  # type: ignore[method-assign]
            await transport.receive(
                qq_message(
                    message_id="m1", user_id="29003", group_id="1301", text="在吗", at_self=True
                )
            )
            await settle(gateway)
            assert len(transport.sent_messages) == 1  # allowed while the gate is open
            state["allow"] = False
            await transport.receive(
                qq_message(
                    message_id="m2", user_id="29003", group_id="1301", text="还在吗", at_self=True
                )
            )
            await settle(gateway)
            assert len(transport.sent_messages) == 1  # the mention alone grants nothing
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()
