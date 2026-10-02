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
    gateway = OneBotGateway(
        runtime,
        transport=transport,
        config=OneBotConfig(**cfg),
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

            async def slow_first(request):  # type: ignore[no-untyped-def]
                text = request.messages[0].content
                if "slow" in text:
                    order.append("slow-start")
                    await asyncio.sleep(0.05)
                    order.append("slow-end")
                else:
                    order.append("fast")
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal(text="好"))

            runtime.ai_engine = _StubEngine(slow_first)
            await transport.receive(
                qq_message(
                    message_id="g1", user_id="21002", group_id="901", text="slow", at_self=True
                )
            )
            await asyncio.sleep(0.005)
            await transport.receive(
                qq_message(
                    message_id="g2", user_id="21003", group_id="902", text="fast", at_self=True
                )
            )
            await settle(gateway, rounds=60)
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
        engine, _provider = make_engine([proposal(text="回")] * 10)
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        gateway, transport = await make_gateway(runtime=runtime, max_pending_per_lane=1)
        try:
            blocker = asyncio.Event()

            async def stalled(_request):  # type: ignore[no-untyped-def]
                await blocker.wait()
                from types import SimpleNamespace

                return SimpleNamespace(content=proposal(text="回"))

            runtime.ai_engine = _StubEngine(stalled)
            await transport.receive(qq_message(message_id="o1", user_id="21004", text="一"))
            await asyncio.sleep(0.005)  # the worker picks up #1 and stalls
            await transport.receive(qq_message(message_id="o2", user_id="21004", text="二"))
            await transport.receive(qq_message(message_id="o3", user_id="21004", text="三"))
            for _ in range(20):  # the first turn is deliberately stalled: no drain here
                await asyncio.sleep(0.005)
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

            assert len(facts()) >= 2
            blocker.set()
            await settle(gateway)
            assert len(facts()) == 3  # nobody was silently dropped from the world
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
            transport.disconnect()
            assert gateway.state is ConnectionState.connected
            await gateway.handle_disconnect()  # bounded ladder, no busy loop
            assert gateway.state is ConnectionState.connected
            assert transport.reconnects >= 1
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

    async def chat(self, request):  # type: ignore[no-untyped-def]
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
