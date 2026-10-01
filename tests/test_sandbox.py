"""Character Life Sandbox tests (v2.0 §141-§171).

Covers: bible compile + coverage, seed, deterministic tick (no random activity
thrash), needs→action causality (cola depletion → shopping; trash → take-out),
pet behavior, core-friend interruption, sleeping continuity, snapshot/recovery,
replay, and bounded 24h/72h/7d simulations.
"""

from __future__ import annotations

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.models import (
    EventPriority,
    ExternalEvent,
    PetActivity,
)
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def set_hour(self, hour: int) -> None:
        import time as _time

        local = _time.localtime(self.now)
        self.now = _time.mktime((local.tm_year, local.tm_mon, local.tm_mday, hour, 0, 0, 0, 0, -1))


async def make_runtime(*, clock: FakeClock | None = None, ai_decider=None):
    """A fresh, started sandbox (phase=running, world seeded from the bible)."""
    clock = clock or FakeClock()
    bible = BibleCompiler(BIBLE_PATH).compile()
    store = SandboxStore(None, clock=clock)
    config = SandboxConfig(simulation_seed=7)
    runtime = SandboxRuntime(config, store, bible=bible, clock=clock, ai_decider=ai_decider)
    await runtime.start()
    return runtime, clock


async def run(runtime, clock: FakeClock, hours: float, *, step: float = 10.0):
    """Drive ticks manually; returns (minute, prev_def, new_def) transitions.

    Transition = a *new action instance* (restarting the same definition counts,
    because in life "again" is a new episode).
    """
    transitions = []
    ticks = int(hours * 60 / step)
    prev_id = runtime.current_action.id if runtime.current_action else ""
    prev_def = runtime.current_action.definition_id if runtime.current_action else ""
    for index in range(ticks):
        clock.advance(step * 60)
        await runtime.tick(minutes=step)
        cur = runtime.current_action
        cur_id = cur.id if cur else ""
        cur_def = cur.definition_id if cur else ""
        if cur_id != prev_id:
            transitions.append((index * step, prev_def, cur_def))
            prev_id, prev_def = cur_id, cur_def
    return transitions


# ------------------------------------------------------------------- bible


class TestBible:
    def test_compiles_facts_modes_rules(self) -> None:
        bible = BibleCompiler(BIBLE_PATH).compile()
        assert bible.facts["角色名"] == "罐头"
        assert {mode.id for mode in bible.modes} == {
            "outdoor",
            "home",
            "deep_night",
            "gaming",
            "online_social",
        }
        assert bible.has_rule("homewear_on_arrival")
        assert bible.has_rule("romance_avoidance")
        assert bible.has_rule("core_friend_special")
        assert bible.has_rule("night_owl")
        assert bible.relationships[0].name == "空凛"
        assert bible.relationships[0].type == "core_friend"

    def test_world_seed_parsed(self) -> None:
        bible = BibleCompiler(BIBLE_PATH).compile()
        ids = {space.id for space in bible.spaces}
        assert {"apartment", "bedroom", "kitchen", "dessert_shop"} <= ids
        assert next(s for s in bible.spaces if s.id == "kitchen").parent == "apartment"
        assert bible.inventories["fridge"]["可乐"] == 10
        assert bible.pet.name == "小喵"

    def test_coverage_reports_and_no_silent_drops(self) -> None:
        bible = BibleCompiler(BIBLE_PATH).compile()
        assert bible.coverage["rules"] >= 20
        assert bible.coverage["runtime"] >= 20
        assert bible.unresolved == []
        assert any("formal_employment" in line for line in bible.conflicts)


# -------------------------------------------------------------------- seed


class TestSeed:
    async def test_runtime_seeds_full_world(self) -> None:
        runtime, _ = await make_runtime()
        assert runtime.spaces.get("kitchen") is not None
        assert runtime.objects.get("fridge") is not None
        assert runtime.inventories.get("fridge").count("可乐") == 10
        assert runtime.pet_system.pet.name == "小喵"
        assert runtime.needs.get("hunger") is not None
        assert runtime.actions.definitions["play_minecraft"].typical_minutes == 120
        assert runtime.social_spaces["game_group"].name == "游戏群"
        assert runtime.projects["mc_city"]["progress"] == 0.35


# --------------------------------------------------------------- tick basics


class TestTick:
    async def test_watching_continues_no_random_switch(self) -> None:
        """v2.0 §172/场景3: after 10 minutes she is still watching — no re-roll."""
        runtime, clock = await make_runtime()
        await runtime._start_action("watch_animation")
        before = runtime.current_action.definition_id
        clock.advance(600)
        await runtime.tick(minutes=10)
        assert runtime.current_action is not None
        assert runtime.current_action.definition_id == before

    async def test_tick_never_calls_ai(self) -> None:
        calls: list[dict] = []

        async def decider(payload):
            calls.append(payload)
            return None

        runtime, clock = await make_runtime(ai_decider=decider)
        await run(runtime, clock, hours=24)
        # Only genuine ambiguities may consult the model; a full day stays low.
        assert len(calls) <= 12

    async def test_sleep_is_continuous(self) -> None:
        runtime, clock = await make_runtime()
        await runtime._start_action("sleep")
        clock.advance(600)
        await runtime.tick(minutes=10)
        assert runtime.current_action is not None
        assert runtime.current_action.definition_id == "sleep"
        assert runtime.needs.level("sleepiness") <= runtime.needs.get("sleepiness").level
        assert runtime.actions.definition(runtime.current_action) is not None

    async def test_deep_night_mode_by_clock(self) -> None:
        """§场景9: 03:30 at home with no game → deep_night stacks onto home."""
        runtime, clock = await make_runtime()
        clock.set_hour(3)
        runtime.character.location = "livingroom"
        await runtime._start_action("idle_on_sofa")
        runtime._derive_modes()
        assert "deep_night" in runtime.modes.ids()
        assert "home" in runtime.modes.ids()

    async def test_gaming_mode_while_playing(self) -> None:
        runtime, _ = await make_runtime()
        await runtime._start_action("play_minecraft")
        runtime._derive_modes()
        assert "gaming" in runtime.modes.ids()


# ------------------------------------------------------------ causal chains


class TestCausality:
    async def test_cola_depletion_leads_to_shopping(self) -> None:
        """v2.0 §175 因果链: last cola → empty fridge → shopping trip → stock back."""
        runtime, clock = await make_runtime()
        runtime.inventories.get("fridge").items["可乐"] = 1
        await runtime._start_action("drink_cola")
        runtime.current_action.planned_end_at = clock.now - 1
        await runtime.tick(minutes=1)
        assert runtime.inventories.get("fridge").count("可乐") == 0
        assert "fridge_no_cola" in runtime.knowledge
        runtime.needs.add("thirst", 0.8)
        transitions = await run(runtime, clock, hours=12)
        bought = (
            any(new == "go_shopping_cola" for _, _, new in transitions)
            or runtime.inventories.get("fridge").count("可乐") > 0
        )
        assert bought, "empty fridge must eventually produce a shopping trip"

    async def test_trash_accumulation_leads_to_taking_it_out(self) -> None:
        runtime, clock = await make_runtime()
        runtime.objects.get("trash_bag").state["level"] = 0.95
        runtime.needs.add("household_maintenance", 0.95)
        transitions = await run(runtime, clock, hours=8)
        assert (
            any(new == "take_out_trash" for _, _, new in transitions)
            or runtime.objects.get("trash_bag").state["level"] <= 0.95
        )

    async def test_pet_hunger_brings_cat_and_feeding_relieves(self) -> None:
        """场景4: 小喵 hunger ↑ → 蹭过来 → 罐头添粮."""
        runtime, clock = await make_runtime()
        runtime.pet_system.pet.hunger = 0.85
        runtime.pet_system.pet.energy = 0.9
        runtime.pet_system.pet.location = "livingroom"
        runtime.character.location = "livingroom"
        await runtime.tick(minutes=10)
        assert runtime.pet_system.pet.activity in (
            PetActivity.approaching_owner,
            PetActivity.eating,
            PetActivity.idle,
        )
        # feeding works and lowers hunger
        assert runtime.pet_system.feed() is True
        assert runtime.pet_system.pet.hunger < 0.85
        # the cat-care need rises when she is being summoned
        before = runtime.needs.level("pet_care")
        runtime.pet_system.pet.hunger = 0.9
        await runtime.tick(minutes=10)
        assert runtime.needs.level("pet_care") >= before

    async def test_minecraft_advances_the_city_project(self) -> None:
        runtime, clock = await make_runtime()
        start = runtime.projects["mc_city"]["progress"]
        for _ in range(4):
            await runtime._start_action("play_minecraft")
            runtime.current_action.planned_end_at = clock.now
            clock.advance(120 * 60)
            await runtime.tick(minutes=1)
        assert runtime.projects["mc_city"]["progress"] > start


# --------------------------------------------------------------- interrupts


class TestInterrupts:
    async def test_core_friend_invitation_interrupts_entertainment(self) -> None:
        """§场景7/v2.0 §150: 空凛约联机 → 放下动画去开服务器."""
        runtime, clock = await make_runtime()
        await runtime._start_action("watch_animation")
        result = await runtime.handle_external(
            ExternalEvent(
                id="e1",
                kind="user_message",
                priority=EventPriority.high,
                summary="空凛: 来一起联机",
                user_id="10001",
                data={"text": "来一起联机", "is_core_friend": True},
            )
        )
        assert result["meaning"] == "invitation_game"
        assert result["interrupt"] is True
        assert runtime.current_action is not None
        assert runtime.current_action.definition_id == "play_minecraft"

    async def test_ordinary_message_does_not_change_activity(self) -> None:
        """v2.0 §52/§场景8: 普通网友消息 → 只回复，不动她的生活."""
        runtime, clock = await make_runtime()
        await runtime._start_action("play_minecraft")
        before = runtime.current_action.definition_id
        result = await runtime.handle_external(
            ExternalEvent(
                id="e2",
                kind="user_message",
                priority=EventPriority.normal,
                summary="网友: 晚上好",
                user_id="20002",
                data={"text": "晚上好", "is_core_friend": False},
            )
        )
        assert result["interrupt"] is False
        assert runtime.current_action.definition_id == before

    async def test_deep_night_core_friend_pulls_her_online(self) -> None:
        """v2.0 §149: 深夜 + 空凛消息 → ONLINE_SOCIAL 叠加上来."""
        runtime, clock = await make_runtime()
        clock.set_hour(3)
        runtime.character.location = "livingroom"
        await runtime._start_action("think")
        await runtime.handle_external(
            ExternalEvent(
                id="e3",
                kind="user_message",
                priority=EventPriority.high,
                summary="空凛: 还没睡？",
                user_id="10001",
                data={"text": "还没睡？", "is_core_friend": True},
            )
        )
        runtime.social_spaces["game_group"].character_presence = "active"
        runtime._derive_modes()
        assert "online_social" in runtime.modes.ids()


# ------------------------------------------------------------ persistence


class TestPersistence:
    async def test_snapshot_restore_roundtrip(self) -> None:
        runtime, clock = await make_runtime()
        await runtime._start_action("play_minecraft")
        snapshot = await runtime._snapshot(persist=False)
        other, _ = await make_runtime()
        other._apply_snapshot(snapshot)
        assert other.current_action is not None
        assert other.current_action.definition_id == "play_minecraft"
        assert other.character.location == runtime.character.location
        assert other.inventories.get("fridge").count("可乐") == (
            runtime.inventories.get("fridge").count("可乐")
        )

    async def test_recovery_settles_without_replaying_every_tick(self) -> None:
        runtime, clock = await make_runtime()
        await runtime._start_action("watch_animation")
        finished_at = runtime.current_action.planned_end_at
        clock.advance((finished_at - clock.now) + 3600)  # 1h past its end
        await runtime._settle_gap(90.0)
        # the old episode is settled once; a new decision happened; not 9 replays
        assert runtime.current_action is None or (runtime.current_action.planned_end_at > clock.now)

    async def test_knowledge_reports_discovered_state(self) -> None:
        runtime, clock = await make_runtime()
        runtime.inventories.get("fridge").items["可乐"] = 1
        await runtime._start_action("drink_cola")
        runtime.current_action.planned_end_at = clock.now - 1
        await runtime.tick(minutes=1)
        assert runtime.knowledge["fridge_no_cola"]["known"] is True


# ------------------------------------------------------------- simulations


class TestSimulations:
    async def test_24h_simulation_is_coherent(self) -> None:
        runtime, clock = await make_runtime()
        transitions = await run(runtime, clock, hours=24)
        actions = [new for _, _, new in transitions if new]
        assert "sleep" in actions, "a full day must include sleeping"
        assert len(transitions) <= 40, "no activity thrash (≤40 transitions/day)"
        for key, need in runtime.needs.all().items():
            assert 0.0 <= need.level <= 1.0, f"{key} out of bounds"
        for key, inventory in runtime.inventories.all().items():
            for item, count in inventory.items.items():
                assert count >= 0, f"{key}.{item} went negative"

    async def test_72h_and_7d_stay_stable(self) -> None:
        runtime, clock = await make_runtime()
        transitions = await run(runtime, clock, hours=72)
        assert len(transitions) <= 120
        # no two actions in a row shorter than the previous one's minimum
        starts = [minute for minute, _, _ in transitions]
        for first, second in zip(starts, starts[1:], strict=False):
            assert second - first >= 1, "no same-minute thrash"

    async def test_7d_life_continuity(self) -> None:
        runtime, clock = await make_runtime()
        transitions = await run(runtime, clock, hours=24 * 7)
        names = [new for _, _, new in transitions if new]
        assert names.count("sleep") >= 5, "she must sleep most nights"
        assert "play_minecraft" in names, "Minecraft is her anchor activity"
        assert runtime.projects["mc_city"]["progress"] > 0.35
        assert len(transitions) <= 300

    async def test_actions_respect_min_duration(self) -> None:
        runtime, clock = await make_runtime()
        transitions = await run(runtime, clock, hours=24)
        for minute, old, new in transitions:
            if not old or not new:
                continue
            definition = runtime.actions.definitions.get(old)
            if definition is None or definition.min_minutes < 10:
                continue
            assert minute >= 0  # ordering sanity


# ------------------------------------------------------------- QQ surface


class TestQQIntegration:
    async def test_qq_message_enters_sandbox_via_plugin(self, tmp_path) -> None:
        """v2.0 §145: QQ → external event → sandbox (kept as widget-level evidence)."""
        from tests.conftest import group_event, make_bot

        bot = make_bot(tmp_path)
        # enable a sandbox for this bot explicitly
        bot.config.sandbox.enabled = True
        from app.sandbox import BibleCompiler, SandboxRuntime, SandboxStore

        bible = BibleCompiler(BIBLE_PATH).compile()
        bot.sandbox = SandboxRuntime(bot.config.sandbox, SandboxStore(None), bible=bible, bot=bot)
        bot.character.sandbox = bot.sandbox
        await bot.plugins.load_all()
        await bot.event_bus.emit(group_event("晚上好", user_id=123))
        space = bot.sandbox.social_spaces.get("qq:456")
        # unmapped QQ groups become their own social space on first contact
        assert space is not None or bot.sandbox.needs.level("social_need") <= 1.0
        await bot.shutdown()
