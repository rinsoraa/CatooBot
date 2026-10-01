"""Task 25 ⑥a: relative config paths mean "inside the project", not "inside cwd".

A bot started from a service manager or an IDE task has a different working
directory than one started from the repo root; the sticker library, the media
cache and the log file must not move with it. These tests run the resolution
from a foreign cwd so a future edit cannot quietly reintroduce ``Path(some_relative)``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.config.settings import PROJECT_ROOT


class TestProjectPath:
    def test_relative_paths_hang_off_the_project_root(self) -> None:
        from app.config.settings import project_path

        assert project_path("data/stickers") == PROJECT_ROOT / "data" / "stickers"
        assert project_path(Path("logs")) == PROJECT_ROOT / "logs"

    def test_absolute_paths_are_left_alone(self, tmp_path) -> None:
        from app.config.settings import project_path

        absolute = tmp_path / "elsewhere" / "stickers"
        assert project_path(str(absolute)) == absolute
        assert project_path(absolute) == absolute

    def test_user_home_is_expanded(self) -> None:
        from app.config.settings import project_path

        resolved = project_path("~/catoobot-test")
        assert resolved.is_absolute()
        assert "~" not in str(resolved)


class TestStickerLibraryDir:
    def test_creates_the_library_under_the_project_root_not_the_cwd(
        self, tmp_path, monkeypatch
    ) -> None:
        from app.config import settings
        from app.media.sticker import StickerLibrary

        fake_root = tmp_path / "proj"
        foreign_cwd = tmp_path / "somewhere-else"
        foreign_cwd.mkdir()
        monkeypatch.setattr(settings, "PROJECT_ROOT", fake_root)
        monkeypatch.chdir(foreign_cwd)

        library = StickerLibrary(database=None, sticker_dir="data/stickers")

        assert library.dir == fake_root / "data" / "stickers"
        assert library.dir.is_dir(), "the library dir is created where the bot will read it"
        assert not (foreign_cwd / "data").exists(), "nothing may land in the working directory"

    def test_an_absolute_sticker_dir_is_respected(self, tmp_path, monkeypatch) -> None:
        from app.config import settings
        from app.media.sticker import StickerLibrary

        monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path / "proj")
        target = tmp_path / "custom-stickers"
        library = StickerLibrary(database=None, sticker_dir=str(target))
        assert library.dir == target

    def test_indexer_import_dir_uses_the_same_rule(self, tmp_path, monkeypatch) -> None:
        from app.config import settings
        from app.media.indexer import StickerLibraryIndexer

        fake_root = tmp_path / "proj"
        monkeypatch.setattr(settings, "PROJECT_ROOT", fake_root)
        monkeypatch.chdir(tmp_path)

        indexer = StickerLibraryIndexer(library=None, analyzer=None, import_dir="data/stickers")
        assert indexer._dir == fake_root / "data" / "stickers"  # noqa: SLF001 - the point of the test


class TestFromAForeignWorkingDirectory:
    """In-process monkeypatching could hide an import-time constant; a child
    process with a different cwd is the honest check."""

    def test_resolution_is_cwd_independent_in_a_child_process(self, tmp_path) -> None:
        code = (
            "import pathlib, sys;"
            "from app.config.settings import PROJECT_ROOT, project_path;"
            "assert pathlib.Path.cwd() != PROJECT_ROOT, 'the test must run from elsewhere';"
            "assert project_path('data/stickers') == PROJECT_ROOT / 'data' / 'stickers';"
            "print(PROJECT_ROOT)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()) == PROJECT_ROOT
