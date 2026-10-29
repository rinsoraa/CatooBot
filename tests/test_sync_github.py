"""Fail-closed public delivery; all writes stay in isolated temporary trees."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = load("safe_sync_test", ROOT / "sync_github.py")
public = load("safe_public_test", ROOT / "scripts" / "build_public_mirror.py")


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture()
def trees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source, target = tmp_path / "source", tmp_path / "mirror"
    source.mkdir()
    (source / "docs").mkdir()
    (target / ".git").mkdir(parents=True)
    (source / "app").mkdir()
    (source / "app" / "main.py").write_text("value = 1\n", encoding="utf-8")
    (source / "README.md").write_text("Public example\n", encoding="utf-8")
    monkeypatch.setattr(public, "SOURCE", source)
    monkeypatch.setattr(public, "TARGET", target)
    return source, target


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--push"],
        ["--commit"],
        ["--force"],
        ["--check", "--push"],
        ["--unknown"],
        ["--stage", "--check"],
        ["--check", "message"],
    ],
)
def test_wrapper_rejects_legacy_and_ambiguous_modes(args, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sync_github.py", *args])

    def forbidden(*args, **kwargs):
        pytest.fail("Rejected options must not execute any child process")

    monkeypatch.setattr(sync.subprocess, "call", forbidden)
    assert sync.main() == 2


@pytest.mark.parametrize(("option", "flag"), [("--check", "--audit"), ("--stage", "--stage")])
def test_wrapper_invokes_only_local_builder(option, flag, monkeypatch):
    commands = []
    monkeypatch.setattr(sys, "argv", ["sync_github.py", option])

    def child(args):
        commands.append(args)
        return 17

    monkeypatch.setattr(sync.subprocess, "call", child)
    assert sync.main() == 17
    assert commands == [[sys.executable, str(ROOT / "scripts" / "build_public_mirror.py"), flag]]
    assert not hasattr(sync, "push") and not hasattr(sync, "mirror")


def test_audit_is_read_only(trees, monkeypatch):
    source, target = trees
    before = snapshot(source), snapshot(target)
    monkeypatch.setattr(sys, "argv", ["build_public_mirror.py", "--audit"])
    assert public.main() == 0
    assert (snapshot(source), snapshot(target)) == before


@pytest.mark.parametrize("args", [[], ["--audit", "--stage"], ["--push"]])
def test_builder_requires_unambiguous_explicit_mode(trees, monkeypatch, args):
    source, target = trees
    before = snapshot(source), snapshot(target)
    monkeypatch.setattr(sys, "argv", ["build_public_mirror.py", *args])
    with pytest.raises(SystemExit):
        public.main()
    assert (snapshot(source), snapshot(target)) == before


def test_default_deny_keeps_private_and_generated_files_out(trees):
    source, target = trees
    names = (
        ".env",
        "config/config.yaml",
        "config/overrides.yaml",
        "config/character_bible.md",
        "docs/specs/private.md",
        "data/private.db",
        "logs/private.log",
        "app/auth.json",
        "app/__pycache__/private.pyc",
        "app/.git/config",
        "app/node_modules/private.js",
        "app/.ENV",
        "app/CONFIG.YAML",
        "app/cache/private.jar",
    )
    for name in names:
        p = source / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("private", encoding="utf-8")
    files = public.candidate_files()
    assert not set(names) & set(files)
    public.stage(files)
    assert not set(names) & set(snapshot(target))


def test_stage_copies_exact_bytes_and_manifest_without_git_operations(trees, monkeypatch):
    source, target = trees
    git_before = snapshot(target / ".git")
    files = public.candidate_files()
    public.stage(files)
    for name, path in files.items():
        assert (target / name).read_bytes() == path.read_bytes()
    assert snapshot(target / ".git") == git_before
    assert (target / ".gitignore").read_text(encoding="utf-8") == public.PUBLIC_IGNORES
    manifest = json.loads((source / "docs/PUBLIC_CANDIDATE_MANIFEST.local.json").read_text())
    assert manifest["sha256"] == {
        name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()
    }
    assert manifest["snapshot"] == "LOCAL_UNAPPROVED"


def test_existing_snapshot_refused_without_overwrite_or_deletion(trees):
    _, target = trees
    (target / "operator.txt").write_text("keep", encoding="utf-8")
    before = snapshot(target)
    with pytest.raises(SystemExit):
        public.stage(public.candidate_files())
    assert snapshot(target) == before


@pytest.mark.parametrize(
    "pollution",
    [
        "contact = '" + "private.person" + "@" + "personal.invalid'\n",
        "user_id = " + "98765" + "43210\n",
        "root = " + repr("E:" + "/" + "Catoobot" + "/private") + "\n",
        "-----BEGIN " + "PRIVATE KEY-----\n",
        "value = " + repr("ghp_" + "A" * 30) + "\n",
    ],
)
def test_stage_reaudits_pollution_before_any_writes(trees, pollution):
    source, target = trees
    (source / "app/main.py").write_text(pollution, encoding="utf-8")
    before = snapshot(source), snapshot(target)
    with pytest.raises(SystemExit):
        public.stage(public.candidate_files())
    assert (snapshot(source), snapshot(target)) == before


def test_private_character_bible_prose_cannot_enter_public_fixture(trees):
    source, target = trees
    (source / "config").mkdir()
    (source / "tests/fixtures").mkdir(parents=True)
    private_line = "Private fictional character backstory kept in local configuration only."
    (source / "config/character_bible.md").write_text(private_line + "\n", encoding="utf-8")
    (source / "tests/fixtures/public_character_bible.md").write_text(
        "# Synthetic public fixture\n" + private_line + "\n", encoding="utf-8"
    )
    before = snapshot(target)
    files = public.candidate_files()
    assert any(item["kind"] == "private_bible_overlap_unapproved" for item in public.audit(files))
    with pytest.raises(SystemExit):
        public.stage(files)
    assert snapshot(target) == before


@pytest.mark.parametrize("invalid", ["../escaped.py", "ABSOLUTE", "docs/private.md"])
def test_programmatic_stage_rejects_paths_outside_reviewed_allowlist(trees, invalid):
    source, target = trees
    files = public.candidate_files()
    key = str(source.parent / "outside.py") if invalid == "ABSOLUTE" else invalid
    files[key] = source / "README.md"
    before = snapshot(target)
    with pytest.raises(SystemExit):
        public.stage(files)
    assert snapshot(target) == before


def test_missing_source_aborts_before_copy(trees):
    source, target = trees
    files = public.candidate_files()
    (source / "app/main.py").unlink()
    with pytest.raises((SystemExit, FileNotFoundError)):
        public.stage(files)
    assert snapshot(target) == {}


def test_binary_like_source_blocks_publication(trees, monkeypatch):
    source, target = trees
    (source / "app/blob.dat").write_bytes(b"public\x00private")
    monkeypatch.setattr(sys, "argv", ["builder", "--stage"])
    assert public.main() == 2
    assert snapshot(target) == {}


def test_source_symlink_and_linked_parent_are_excluded(trees, tmp_path):
    source, _ = trees
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret.py").write_text("private", encoding="utf-8")
    try:
        (source / "app/link.py").symlink_to(private / "secret.py")
        (source / "app/linked").symlink_to(private, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable for this OS account")
    assert not public.acceptable(source / "app/link.py")
    assert not public.acceptable(source / "app/linked/secret.py")


def test_aliases_and_internal_history_are_not_public(trees):
    assert "CHANGELOG.md" not in public.PUBLIC_FILES
    assert "sync_github.py" in public.PUBLIC_FILES
    assert "scripts/build_public_mirror.py" in public.PUBLIC_FILES
    assert public.audit({"app/main.py": trees[0] / "app/main.py"}) == []
