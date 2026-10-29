"""The suite must never touch the operator's live data (Task 3).

Tests once reached production data through default paths: junk files landed in
the real sticker library, and any future default-path slip would silently hit
`data/catoobot.db`. `tests/conftest.py` installs a guard that turns such an
access into a loud failure; this file pins the path classifier. It never opens
a database itself — the matching end-to-end behaviour is covered by the guard
being active for every other test in the suite.
"""

from __future__ import annotations

from tests.conftest import REPO_ROOT, is_live_db_path


class TestLiveDbGuard:
    def test_live_database_paths_are_recognised(self) -> None:
        assert is_live_db_path("data/catoobot.db") is True
        assert is_live_db_path(REPO_ROOT / "data" / "catoobot.db") is True
        assert is_live_db_path(f"file:{REPO_ROOT.as_posix()}/data/catoobot.db?mode=ro") is True

    def test_temporary_and_memory_databases_are_allowed(self, tmp_path) -> None:
        assert is_live_db_path(":memory:") is False
        assert is_live_db_path(tmp_path / "test.db") is False
        assert is_live_db_path("file::memory:?cache=shared") is False
