"""Task 25 ⑥b: the initiative gate must not spam INFO once per tick.

A user whose gate keeps saying ``too_soon`` produced one INFO line per pass
forever, burying the lines that matter. Only a *change* of reason is INFO now;
a repeat is DEBUG. The reason is logged again after the scope is allowed
through, because a new rejection there is news.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from app.behavior.scheduler import BehaviorScheduler


class _Initiative:
    def __init__(self) -> None:
        self.gate = SimpleNamespace(allowed=False, reason="too_soon", detail="")
        self.skipped: list[str] = []

    async def build_candidates(self, **kwargs):  # type: ignore[no-untyped-def]
        return [SimpleNamespace(reason="miss_you", topic="今天的月亮")]

    async def mark_candidate(self, scope_key: str, reason: str) -> None:
        return None

    async def evaluate(self, candidate, **kwargs):  # type: ignore[no-untyped-def]
        return self.gate

    async def mark_skipped(self, scope_key: str, reason: str, detail: str) -> None:
        self.skipped.append(reason)


class _Harness(BehaviorScheduler):
    def __init__(self, initiative: _Initiative) -> None:
        bot = SimpleNamespace(
            character=object(),
            relationships=SimpleNamespace(
                all=self._relationships,
            ),
        )
        behavior = SimpleNamespace(initiative=initiative)
        super().__init__(bot, behavior, tick_seconds=3600)
        self.sent: list[str] = []

    @staticmethod
    async def _relationships():  # type: ignore[no-untyped-def]
        return [SimpleNamespace(user_id="5", last_seen=0, stage="friend")]

    async def _background_budget_ok(self) -> bool:
        return True

    async def _world_moment(self) -> tuple[str, str]:
        return "", ""

    async def _user_initiative_enabled(self, user_id: str) -> bool:
        return True

    async def _send_initiative(self, scope_key, user_id, candidate) -> bool:  # type: ignore[no-untyped-def]
        self.sent.append(scope_key)
        return True


def _gate_lines(caplog) -> list[str]:  # type: ignore[no-untyped-def]
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("[Initiative] Gate rejected")
        and record.levelno == logging.INFO
    ]


class TestInitiativeGateLogging:
    async def test_repeat_rejections_log_info_once(self, caplog) -> None:
        initiative = _Initiative()
        scheduler = _Harness(initiative)
        with caplog.at_level(logging.DEBUG, logger="CatooBot.Behavior"):
            await scheduler._initiative_pass()  # noqa: SLF001
            await scheduler._initiative_pass()  # noqa: SLF001
            await scheduler._initiative_pass()  # noqa: SLF001
        assert len(_gate_lines(caplog)) == 1, _gate_lines(caplog)
        assert len(initiative.skipped) == 3, "every rejection is still recorded in the state"
        assert "unchanged" in "\n".join(
            record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG
        )

    async def test_a_new_reason_is_news_again(self, caplog) -> None:
        initiative = _Initiative()
        scheduler = _Harness(initiative)
        with caplog.at_level(logging.INFO, logger="CatooBot.Behavior"):
            await scheduler._initiative_pass()  # noqa: SLF001
            initiative.gate = SimpleNamespace(allowed=False, reason="user_disabled", detail="")
            await scheduler._initiative_pass()  # noqa: SLF001
        lines = _gate_lines(caplog)
        assert len(lines) == 2, lines
        assert "too_soon" in lines[0] and "user_disabled" in lines[1]

    async def test_an_allowed_send_resets_the_memory(self, caplog) -> None:
        initiative = _Initiative()
        scheduler = _Harness(initiative)
        with caplog.at_level(logging.INFO, logger="CatooBot.Behavior"):
            await scheduler._initiative_pass()  # noqa: SLF001
            initiative.gate = SimpleNamespace(allowed=True, reason="", detail="")
            await scheduler._initiative_pass()  # noqa: SLF001
            initiative.gate = SimpleNamespace(allowed=False, reason="too_soon", detail="")
            await scheduler._initiative_pass()  # noqa: SLF001
        assert scheduler.sent == ["private:5"]
        assert len(_gate_lines(caplog)) == 2, _gate_lines(caplog)
