"""Public mirror delivery: mod coverage, check-only mode and pre-commit backup gates."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "sync_github", Path(__file__).resolve().parents[1] / "sync_github.py"
)
assert _spec is not None and _spec.loader is not None
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


@pytest.fixture()
def trees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source, target = tmp_path / "source", tmp_path / "mirror"
    source.mkdir()
    (target / ".git").mkdir(parents=True)
    monkeypatch.setattr(sync, "SOURCE", source)
    monkeypatch.setattr(sync, "TARGET", target)
    monkeypatch.setattr(sync, "MIRRORED_DIRS", ("app",))
    monkeypatch.setattr(sync, "ROOT_FILES", ("README.md", ".gitignore"))
    for directory in ("app", "server_mod/src", "config"):
        (source / directory).mkdir(parents=True)
    files = ["README.md", ".gitignore", "app/main.py", "server_mod/src/Booking.java"]
    files += [f"config/{name}" for name in sync.CONFIG_DIR_FILES]
    files += [f"server_mod/{name}" for name in sync.MOD_FILES]
    for name in files:
        (source / name).write_text(f"public {name}\n", encoding="utf-8")
    return source, target


def test_mod_is_updated_and_deleted_sources_are_removed(trees: tuple[Path, Path]) -> None:
    source, target = trees
    sync.mirror([])
    (source / "server_mod/src/Booking.java").write_text("updated", encoding="utf-8")
    (target / "server_mod/src/removed.java").write_text("old", encoding="utf-8")
    changes: list[str] = []
    sync.mirror(changes)
    assert (target / "server_mod/src/Booking.java").read_text() == "updated"
    assert not (target / "server_mod/src/removed.java").exists()
    assert "+ Booking.java" in changes and "- removed.java" in changes
    assert (target / "server_mod/test.ps1").is_file()


def test_private_and_generated_files_never_reach_mirror(trees: tuple[Path, Path]) -> None:
    source, target = trees
    for name in (
        ".env",
        "config/config.yaml",
        "config/overrides.yaml",
        "server_mod/catoo-attribution.properties",
        "server_mod/build/private.jar",
        "app/__pycache__/private.pyc",
        "app/.git/config",
    ):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private", encoding="utf-8")
    sync.mirror([])
    assert not (target / ".env").exists()
    assert not (target / "config/config.yaml").exists()
    assert not (target / "server_mod/catoo-attribution.properties").exists()
    assert not (target / "server_mod/build").exists()
    assert not (target / "app/__pycache__").exists()
    assert not (target / "app/.git").exists()


def test_check_reports_drift_without_any_writes(trees: tuple[Path, Path]) -> None:
    _, target = trees
    before = sorted(path.relative_to(target) for path in target.rglob("*"))
    changes: list[str] = []
    sync.mirror(changes, dry_run=True)
    assert changes
    assert sorted(path.relative_to(target) for path in target.rglob("*")) == before
    sync.mirror([])
    changes = []
    sync.mirror(changes, dry_run=True)
    assert changes == []


def test_missing_source_aborts_before_overwriting_or_deleting(trees: tuple[Path, Path]) -> None:
    source, target = trees
    sync.mirror([])
    (source / "server_mod/test.ps1").unlink()
    (target / "app/keep.py").write_text("operator", encoding="utf-8")
    (source / "README.md").write_text("new", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        sync.mirror([])
    assert (target / "README.md").read_text() != "new"
    assert (target / "app/keep.py").read_text() == "operator"


def test_backup_contains_exact_public_bytes_and_sha256(trees: tuple[Path, Path]) -> None:
    _, target = trees
    sync.mirror([])
    (target / ".env").write_text("never include", encoding="utf-8")
    archive = sync.backup_release()
    with zipfile.ZipFile(archive) as saved:
        assert ".env" not in saved.namelist()
        assert not any(name.startswith(".git/") for name in saved.namelist())
        manifest = json.loads(saved.read("MANIFEST.sha256.json"))
        assert "server_mod/test.ps1" in manifest
        for name, digest in manifest.items():
            content = saved.read(name)
            assert content == (target / name).read_bytes()
            assert hashlib.sha256(content).hexdigest() == digest
    assert (
        archive.with_suffix(".zip.sha256").read_text().split()[0]
        == hashlib.sha256(archive.read_bytes()).hexdigest()
    )


def test_failed_backup_stops_before_git_add(
    trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b"")

    def fail() -> None:
        raise OSError("backup volume unavailable")

    monkeypatch.setattr(sync.subprocess, "run", run)
    monkeypatch.setattr(sync, "backup_release", fail)
    with pytest.raises(OSError, match="backup volume"):
        sync.push("test")
    assert not any(command[1] in ("add", "commit", "push") for command in commands)


def test_preexisting_index_is_never_committed(
    trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert args[1] == "diff"
        return subprocess.CompletedProcess(args, 0, stdout=b"operator-change.py\n")

    monkeypatch.setattr(sync.subprocess, "run", run)
    with pytest.raises(SystemExit, match="index already"):
        sync.push("test")


def test_push_backs_up_before_scoped_staging(
    trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    sync.mirror([])
    operations: list[str] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        operations.append(args[1])
        if args[1] == "add":
            assert operations[0:2] == ["diff", "backup"]
            assert args[1:4] == ["add", "-A", "--"]
            assert "server_mod/src" in args and "server_mod/build" not in args
            assert ".env" not in args and "config/config.yaml" not in args
        # First diff is the preexisting-index check; the second represents our staged change.
        output = b"" if operations.count("diff") == 1 else "server_mod/test.ps1\n"
        return subprocess.CompletedProcess(args, 0, stdout=output)

    monkeypatch.setattr(sync.subprocess, "run", run)
    monkeypatch.setattr(sync, "backup_release", lambda: operations.append("backup"))
    sync.push("test")
    assert operations == ["diff", "backup", "add", "diff", "commit", "push"]
