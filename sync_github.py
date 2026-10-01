"""Sync the CatooBot working tree to the GitHub mirror and (optionally) push.

Workflow (single source of truth):

    1. All modifications happen in THIS folder (E:\\WorkSpace ZCode\\CatooBot).
    2. Tests / ruff / mypy are run and must pass here first.
    3. ``python sync_github.py``          → mirror code into ..\\CatooBot_github
       ``python sync_github.py --push``   → mirror + git commit + git push

The mirror (``E:\\WorkSpace ZCode\\CatooBot_github``) is a git repo pointing at
the private GitHub project. It never receives secrets or runtime data:

    synced:     app/  plugins/  tests/  docs/  .github/  pyproject.toml  run.py
                README.md  .env.example  .gitignore  .gitattributes
                sync_github.py  config/config.example.yaml
                config/character_bible.md
    never:      .env  config/config.yaml  config/overrides.yaml
                data/  logs/  .venv/  caches  *.egg-info
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
TARGET = SOURCE.parent / "CatooBot_github"

MIRRORED_DIRS = ("app", "plugins", "tests", "docs", ".github")
ROOT_FILES = (
    "pyproject.toml",
    "run.py",
    "README.md",
    ".env.example",
    ".gitignore",
    ".gitattributes",
    "sync_github.py",
)
CONFIG_DIR_FILES = ("config.example.yaml", "character_bible.md")

EXCLUDED_DIR_PATTERNS = ("__pycache__", "*.egg-info", ".mypy_cache", ".pytest_cache", ".ruff_cache")


def _excluded(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_DIR_PATTERNS)


def _file_hash(path: Path) -> bytes:
    return path.read_bytes()


def mirror_dir(src: Path, dst: Path, changes: list[str]) -> None:
    """Mirror ``src`` into ``dst``: copy changed files, delete removed ones."""
    dst.mkdir(parents=True, exist_ok=True)
    src_files: dict[str, Path] = {}
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if any(_excluded(part) for part in rel.parts):
            continue
        if item.is_file():
            src_files[rel.as_posix()] = item
    dst_files = {
        item.relative_to(dst).as_posix(): item
        for item in sorted(dst.rglob("*"))
        if item.is_file() and not any(_excluded(p) for p in item.relative_to(dst).parts)
    }

    for rel, src_path in src_files.items():
        dst_path = dst / rel
        if not dst_path.exists() or _file_hash(src_path) != _file_hash(dst_path):
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            changes.append(f"+ {rel}")
    for rel, dst_path in dst_files.items():
        if rel not in src_files:
            dst_path.unlink()
            changes.append(f"- {rel}")


def mirror(changes: list[str]) -> None:
    if not TARGET.exists():
        sys.exit(f"[sync] target folder not found: {TARGET}")
    if not (TARGET / ".git").exists():
        sys.exit(f"[sync] {TARGET} is not a git repository — refusing to sync")

    for name in MIRRORED_DIRS:
        mirror_dir(SOURCE / name, TARGET / name, changes)
    for name in ROOT_FILES:
        src, dst = SOURCE / name, TARGET / name
        if not src.exists():
            changes.append(f"! missing in source, skipped: {name}")
        elif not dst.exists() or _file_hash(src) != _file_hash(dst):
            shutil.copy2(src, dst)
            changes.append(f"+ {name}")
    for name in CONFIG_DIR_FILES:
        src, dst = SOURCE / "config" / name, TARGET / "config" / name
        if not dst.exists() or _file_hash(src) != _file_hash(dst):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            changes.append(f"+ config/{name}")


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

    git("add", "-A")
    staged = subprocess.run(
        ["git", "status", "--porcelain"], cwd=TARGET, capture_output=True, text=True
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
    parser.add_argument("-m", "--message", default="Sync from working tree", help="commit message")
    args = parser.parse_args()

    changes: list[str] = []
    mirror(changes)
    if not changes:
        print("[sync] mirror already up to date")
    else:
        print(f"[sync] {len(changes)} change(s):")
        for line in changes:
            print(f"  {line}")

    if args.push:
        push(args.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
