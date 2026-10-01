"""Task 22: expression / 口癖 learning — store, learner, injection."""

from __future__ import annotations

from app.config.settings import ExpressionConfig
from app.expression.context import expression_context
from app.expression.learner import ExpressionLearner
from app.expression.store import ExpressionStore


async def make_store(tmp_path, **overrides):  # type: ignore[no-untyped-def]
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'expr.db'}"))
    await database.connect()
    config = ExpressionConfig(**{"enabled": True, **overrides})
    store = ExpressionStore(database, config)
    return store, database, config


class TestExpressionStore:
    async def test_learn_increments_speaker_and_sample_counts(self, tmp_path) -> None:
        store, database, _config = await make_store(tmp_path)
        try:
            await store.learn(
                scope_key="group:1",
                pattern="有一说一",
                kind="word",
                user_id="a",
                message_id="m1",
                text="有一说一",
                now=1,
            )
            await store.learn(
                scope_key="group:1",
                pattern="有一说一",
                kind="word",
                user_id="b",
                message_id="m2",
                text="有一说一",
                now=2,
            )
            patterns = await store.active_patterns("group:1")
            assert len(patterns) == 1
            assert patterns[0].sample_count == 2
            assert patterns[0].speaker_count == 2
            assert patterns[0].threshold_met is True
        finally:
            await database.close()

    async def test_same_message_is_idempotent(self, tmp_path) -> None:
        store, database, _config = await make_store(tmp_path)
        try:
            await store.learn(
                scope_key="group:1",
                pattern="有一说一",
                kind="word",
                user_id="a",
                message_id="m1",
                text="有一说一",
                now=1,
            )
            await store.learn(
                scope_key="group:1",
                pattern="有一说一",
                kind="word",
                user_id="a",
                message_id="m1",
                text="有一说一",
                now=2,
            )
            patterns = await store.list_patterns("group:1")
            assert len(patterns) == 1
            assert patterns[0]["sample_count"] == 1
            assert patterns[0]["speaker_count"] == 1
        finally:
            await database.close()

    async def test_below_threshold_is_not_active(self, tmp_path) -> None:
        store, database, _config = await make_store(tmp_path)
        try:
            # one speaker, two occurrences → below min_speakers=2 AND min_occurrences=3
            await store.learn(
                scope_key="group:1",
                pattern="有一说一",
                kind="word",
                user_id="a",
                message_id="m1",
                text="有一说一",
                now=1,
            )
            await store.learn(
                scope_key="group:1",
                pattern="有一说一",
                kind="word",
                user_id="a",
                message_id="m2",
                text="有一说一",
                now=2,
            )
            assert await store.active_patterns("group:1") == []
        finally:
            await database.close()

    async def test_disable_and_delete(self, tmp_path) -> None:
        store, database, _config = await make_store(tmp_path)
        try:
            await store.learn(
                scope_key="group:1",
                pattern="有一说一",
                kind="word",
                user_id="a",
                message_id="m1",
                text="有一说一",
                now=1,
            )
            await store.learn(
                scope_key="group:1",
                pattern="有一说一",
                kind="word",
                user_id="b",
                message_id="m2",
                text="有一说一",
                now=2,
            )
            patterns = await store.active_patterns("group:1")
            await store.set_status(patterns[0].id, "disabled")
            assert await store.active_patterns("group:1") == []
            await store.delete(patterns[0].id)
            assert await store.list_patterns("group:1") == []
        finally:
            await database.close()

    async def test_evict_archives_overflow(self, tmp_path) -> None:
        store, database, _config = await make_store(tmp_path, max_patterns_per_group=2)
        try:
            for i, word in enumerate(("有一说一", "狠狠地", "绝绝子")):
                for user in ("a", "b"):
                    await store.learn(
                        scope_key="group:1",
                        pattern=word,
                        kind="word",
                        user_id=user,
                        message_id=f"m{i}-{user}",
                        text=word,
                        now=i,
                    )
            evicted = await store.evict("group:1")
            assert evicted >= 1
            active = await store.active_patterns("group:1")
            assert len(active) <= 2
        finally:
            await database.close()


class TestExpressionLearner:
    async def test_learns_short_lexical_phrases(self, tmp_path) -> None:
        store, database, config = await make_store(tmp_path)
        learner = ExpressionLearner(store, config)
        try:
            results = await learner.learn_message(
                group_id="1", user_id="a", message_id="m1", text="有一说一，这个真的绝了"
            )
            assert ("有一说一", "learned") in results
            assert ("这个真的绝了", "learned") in results
        finally:
            await database.close()

    async def test_rejects_her_own_and_bot_messages(self, tmp_path) -> None:
        store, database, config = await make_store(tmp_path)
        learner = ExpressionLearner(store, config)
        try:
            results = await learner.learn_message(
                group_id="1",
                user_id="bot",
                message_id="m1",
                text="有一说一",
                sender_is_bot=True,
            )
            assert results == []
            assert await store.list_patterns("group:1") == []
        finally:
            await database.close()

    async def test_rejects_sensitive_and_entities(self, tmp_path) -> None:
        store, database, config = await make_store(tmp_path)
        learner = ExpressionLearner(store, config)
        try:
            results = await learner.learn_message(
                group_id="1", user_id="a", message_id="m1", text="傻逼"
            )
            assert results == [("傻逼", "命中敏感/攻击词表")]

            results = await learner.learn_message(
                group_id="1", user_id="a", message_id="m2", text="12345"
            )
            assert results == []  # no lexical content
            assert await store.list_patterns("group:1") == []
        finally:
            await database.close()

    async def test_disabled_config_learns_nothing(self, tmp_path) -> None:
        store, database, config = await make_store(tmp_path, enabled=False)
        learner = ExpressionLearner(store, config)
        try:
            results = await learner.learn_message(
                group_id="1", user_id="a", message_id="m1", text="有一说一"
            )
            assert results == []
            assert await store.list_patterns("group:1") == []
        finally:
            await database.close()


class TestExpressionContext:
    async def test_injection_respects_budget_and_format(self, tmp_path) -> None:
        store, database, config = await make_store(
            tmp_path, inject_max_items=2, inject_max_chars=10
        )
        try:
            for i, word in enumerate(("有一说一", "狠狠地", "绝绝子")):
                for user in ("a", "b"):
                    await store.learn(
                        scope_key="group:1",
                        pattern=word,
                        kind="word",
                        user_id=user,
                        message_id=f"m{i}-{user}",
                        text=word,
                        now=i,
                    )
            block, ids = await expression_context(store, config, "1")
            assert len(ids) <= 2
            assert "这个群的人习惯这样说" in block
            assert "不要为了用而用" in block
            # no cross-group leak
            other, _ = await expression_context(store, config, "2")
            assert other == ""
        finally:
            await database.close()

    async def test_no_block_when_nothing_threshold_met(self, tmp_path) -> None:
        store, database, config = await make_store(tmp_path)
        try:
            await store.learn(
                scope_key="group:1",
                pattern="有一说一",
                kind="word",
                user_id="a",
                message_id="m1",
                text="有一说一",
                now=1,
            )
            block, _ids = await expression_context(store, config, "1")
            assert block == ""
        finally:
            await database.close()
