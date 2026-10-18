"""Sync the CatooBot working tree to the GitHub mirror and (optionally) push.

Workflow (single source of truth):

    1. All modifications happen in THIS folder.
    2. Tests / ruff / mypy are run and must pass here first.
    3. ``python sync_github.py``          → mirror code into the sibling folder
       ``python sync_github.py --push``   → mirror + git commit + git push

The mirror (this folder's sibling ``CatooBot_github``, derived from this
script's own location — no machine-specific path is hard-coded here) is a git
repo pointing at the private GitHub project. It never receives secrets or
runtime data:

    synced:     app/  plugins/  tests/  docs/  .github/  webui/  minecraft_runtime/
                server_mod/src/ and allowlisted mod build/test/docs/sample files
                pyproject.toml  run.py  README.md  .env.example  .gitignore
                .gitattributes  .dockerignore  Dockerfile  docker-compose.yml
                uv.lock  sync_github.py  config/config.example.yaml
                config/character_bible.example.md
    never:      .env  config/config.yaml  config/overrides.yaml
                config/character_bible.md (private character data)
                data/  logs/  .venv/  caches  *.egg-info
                minecraft_runtime/auth.json (Minecraft account secrets)
                minecraft_runtime/auth_cache/ (Microsoft token cache)
                minecraft_runtime/node_modules/
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
TARGET = SOURCE.parent / "CatooBot_github"

MIRRORED_DIRS = ("app", "plugins", "tests", "docs", ".github", "webui", "minecraft_runtime")
ROOT_FILES = (
    "pyproject.toml",
    "uv.lock",
    "run.py",
    "README.md",
    "LICENSE",
    "NOTICE",
    "EULA.md",
    "CHANGELOG.md",
    ".env.example",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "sync_github.py",
)
CONFIG_DIR_FILES = ("config.example.yaml", "character_bible.example.md")
# Use a separate allowlist: never mirror server_mod/build or live properties.
MOD_FILES = ("README.md", "build.ps1", "test.ps1", "fabric.mod.json", "config.sample.properties")

EXCLUDED_DIR_PATTERNS = (
    "__pycache__",
    "*.egg-info",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".vite",
    ".git",
    # Minecraft Bridge runtime 的本地认证数据（Phase 1）：账号凭据与 token 缓存
    # 只留在操作者机器上，绝不进镜像/仓库。
    "auth.json",
    "auth_cache",
)


def _excluded(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_DIR_PATTERNS)


def _file_hash(path: Path) -> bytes:
    return path.read_bytes()


def public_files(directory: Path) -> list[Path]:
    """Prune excluded directories before walking dependency trees and caches."""
    files: list[Path] = []
    for parent, directories, names in os.walk(directory):
        directories[:] = sorted(name for name in directories if not _excluded(name))
        files.extend(Path(parent) / name for name in sorted(names) if not _excluded(name))
    return files


def mirror_dir(src: Path, dst: Path, changes: list[str], *, dry_run: bool = False) -> None:
    """Mirror ``src`` into ``dst``: copy changed files, delete removed ones."""
    if not src.is_dir():
        raise FileNotFoundError(f"required source directory not found: {src}")
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    src_files: dict[str, Path] = {}
    for item in public_files(src):
        rel = item.relative_to(src)
        if any(_excluded(part) for part in rel.parts):
            continue
        if item.is_file():
            src_files[rel.as_posix()] = item
    dst_files = {
        item.relative_to(dst).as_posix(): item
        for item in public_files(dst)
        if item.is_file() and not any(_excluded(p) for p in item.relative_to(dst).parts)
    }

    for rel, src_path in src_files.items():
        dst_path = dst / rel
        if not dst_path.exists() or _file_hash(src_path) != _file_hash(dst_path):
            if not dry_run:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
            changes.append(f"+ {rel}")
    for rel, dst_path in dst_files.items():
        if rel not in src_files:
            if not dry_run:
                dst_path.unlink()
            changes.append(f"- {rel}")


def release_files(root: Path) -> list[Path]:
    """Exact public-file manifest shared by sync validation and pre-commit backup."""
    files = [root / name for name in ROOT_FILES]
    files += [root / "config" / name for name in CONFIG_DIR_FILES]
    files += [root / "server_mod" / name for name in MOD_FILES]
    for name in (*MIRRORED_DIRS, "server_mod/src"):
        directory = root / name
        if not directory.is_dir():
            raise FileNotFoundError(f"required source directory not found: {directory}")
        files += public_files(directory)
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(f"required release file not found: {path}")
    return sorted(files)


def mirror(changes: list[str], *, dry_run: bool = False) -> None:
    if not TARGET.exists():
        sys.exit(f"[sync] target folder not found: {TARGET}")
    if not (TARGET / ".git").exists():
        sys.exit(f"[sync] {TARGET} is not a git repository — refusing to sync")

    # Validate the entire source before the first write or deletion.
    release_files(SOURCE)
    for name in (*MIRRORED_DIRS, "server_mod/src"):
        mirror_dir(SOURCE / name, TARGET / name, changes, dry_run=dry_run)
    names = [*ROOT_FILES]
    names += [f"config/{name}" for name in CONFIG_DIR_FILES]
    names += [f"server_mod/{name}" for name in MOD_FILES]
    for name in names:
        src, dst = SOURCE / name, TARGET / name
        if not dst.exists() or _file_hash(src) != _file_hash(dst):
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            changes.append(f"+ {name}")


def backup_release() -> Path:
    """Save the exact publishable candidate in the user's mirror before staging."""
    directory = TARGET / ".sync-backups"
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archive = directory / f"release-{stamp}.zip"
    manifest: dict[str, str] = {}
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
        for path in release_files(TARGET):
            name = path.relative_to(TARGET).as_posix()
            content = path.read_bytes()
            manifest[name] = hashlib.sha256(content).hexdigest()
            output.writestr(name, content)
        output.writestr("MANIFEST.sha256.json", json.dumps(manifest, indent=2, sort_keys=True))
    with zipfile.ZipFile(archive) as saved:
        if saved.testzip() is not None:
            raise OSError("release backup failed ZIP integrity verification")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"[sync] pre-commit backup: {archive} (sha256={digest})")
    return archive


def push(message: str) -> None:
    def git(*args: str) -> None:
        result = subprocess.run(
            ["git", *args],
            cwd=TARGET,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            sys.exit(f"[sync] git {' '.join(args)} failed:\n{result.stderr.strip()}")

    # Do not include operator-local/unrelated files via a blanket git add -A.
    allowed = [*MIRRORED_DIRS, *ROOT_FILES]
    allowed += [f"config/{name}" for name in CONFIG_DIR_FILES]
    allowed += ["server_mod/src", *(f"server_mod/{name}" for name in MOD_FILES)]
    dirty = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=TARGET, capture_output=True, check=True
    ).stdout
    if dirty.strip():
        sys.exit("[sync] index already has staged changes; review them before pushing")
    backup_release()  # Any backup error stops execution before staging or committing.
    git("add", "-A", "--", *allowed)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=TARGET, capture_output=True, text=True
    ).stdout.strip()
    if not staged:
        print("[sync] nothing to commit — mirror already up to date")
        return
    git("commit", "-m", message)
    git("push")
    print(f"[sync] pushed: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--push", action="store_true", help="commit and push after mirroring")
    parser.add_argument(
        "--check", action="store_true", help="check drift without writing; exit 1 if different"
    )
    parser.add_argument("-m", "--message", default="Sync from working tree", help="commit message")
    args = parser.parse_args()
    if args.check and args.push:
        parser.error("--check and --push cannot be combined")

    changes: list[str] = []
    mirror(changes, dry_run=args.check)
    if not changes:
        print("[sync] mirror already up to date")
    else:
        print(f"[sync] {len(changes)} change(s):")
        for line in changes:
            print(f"  {line}")

    if args.push:
        push(args.message)
    return int(args.check and bool(changes))


if __name__ == "__main__":
    sys.exit(main())
