"""Stage or update an explicitly allowlisted public CatooBot mirror. NO Git commits or network.

Usage:
    python scripts/build_public_mirror.py --audit
    python scripts/build_public_mirror.py --stage
    python scripts/build_public_mirror.py --make-snapshot --snapshot-dir DIR
    python scripts/build_public_mirror.py --plan --snapshot-dir DIR
    python scripts/build_public_mirror.py --verify --snapshot-dir DIR
    python scripts/build_public_mirror.py --update-existing --snapshot-dir DIR \
        --confirm UPDATE_LOCAL_MIRROR_WORKTREE

Privacy is default-deny: docs, local configuration, evidence, runtime files and
machine paths are never copied. The scan is a defense-in-depth check, NOT a
guarantee of absence of all personal content: a human must review the snapshot.

Two local delivery paths exist and both are local-only (no git subprocess, no
network, no commit and no push):

* --stage fills a completely empty mirror and keeps refusing to overwrite an
  existing release snapshot. That default-deny behaviour is unchanged.
* --update-existing is the reviewed, explicit opt-in replacement path for a
  mirror that already holds a previous snapshot. It requires an exact
  confirmation token, rejects links/junctions/unknown or sensitive files and any
  path that resolves outside the mirror root, writes a verified rollback copy of
  the current worktree into the private tree first, then replaces managed files
  one at a time with a SHA-256 check per file (no recursive directory copy, no
  .git write) and finally re-verifies the whole worktree against the reviewed
  snapshot.

--make-snapshot materialises the allowlisted candidate into an independent
directory (a temporary directory, never the mirror or docs), --plan prints
added/modified/removed against the mirror, and --verify re-checks a snapshot
against the mirror. A snapshot is only ever applied when it still matches the
live allowlist byte for byte, so a stale snapshot cannot be updated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent
TARGET = SOURCE.parent / "Catoobot_github"

GIT_DIR = ".git"
MIRROR_IGNORE = ".gitignore"
SNAPSHOT_TREE = "tree"
SNAPSHOT_MANIFEST = "candidate-manifest.json"
BACKUP_DIRNAME = ".sync-backups"
ROLLBACK_MANIFEST = "rollback-manifest.json"
UPDATE_CONFIRMATION = "UPDATE_LOCAL_MIRROR_WORKTREE"

PUBLIC_TREES = (
    "app",
    "plugins",
    "tests",
    "webui/src",
    "webui/tests",
    "webui/e2e",
    "minecraft_runtime/test",
    "server_mod/src",
    ".github/workflows",
)
PUBLIC_FILES = (
    "run.py",
    "sync_github.py",
    "scripts/build_public_mirror.py",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "NOTICE",
    "EULA.md",
    ".env.example",
    ".gitattributes",
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "webui/index.html",
    "webui/package.json",
    "webui/package-lock.json",
    "webui/tsconfig.json",
    "webui/vite.config.ts",
    "webui/playwright.config.ts",
    "minecraft_runtime/action_runtime.js",
    "minecraft_runtime/dig_attribution.js",
    "minecraft_runtime/resource_facts.js",
    "minecraft_runtime/runtime.js",
    "minecraft_runtime/strict_attribution.js",
    "minecraft_runtime/package.json",
    "minecraft_runtime/package-lock.json",
    "minecraft_runtime/auth.example.json",
    "server_mod/README.md",
    "server_mod/build.ps1",
    "server_mod/test.ps1",
    "server_mod/fabric.mod.json",
    "server_mod/config.sample.properties",
    "config/config.example.yaml",
    "config/character_bible.example.md",
)
BANNED_COMPONENTS = frozenset(
    {
        ".git",
        "docs",
        "data",
        "logs",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".gradle",
        ".zcode",
        ".sync-backups",
        "test-results",
        "playwright-report",
        "auth_cache",
        ".test-tmp",
        "META-INF",
        "backup",
        "backups",
    }
)
BANNED_NAMES = frozenset(
    {
        ".env",
        "config.yaml",
        "overrides.yaml",
        "auth.json",
        "character_bible.md",
        "bible_source_罐头.txt",
        "agent.md",
        "AGENTS.md",
    }
)
BANNED_EXTS = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".wal",
    ".shm",
    ".log",
    ".pyc",
    ".class",
    ".jar",
    ".exe",
    ".dll",
    ".pdb",
    ".zip",
    ".bak",
}
PUBLIC_IGNORES = """# PUBLIC REPOSITORY — release-snapshot allowlist; all docs private
/docs/
/data/
/logs/
/config/config.yaml
/config/overrides.yaml
/config/character_bible.md
/.env
/.env.*
!/.env.example
/**/auth.json
/**/auth_cache/
/.venv/
**/node_modules/
**/__pycache__/
**/*.py[cod]
**/*.db*
**/*.sqlite*
**/*.log*
**/*.jar
**/*.class
**/.pytest_cache/
**/.mypy_cache/
**/.ruff_cache/
/webui/dist/
/webui/test-results/
/webui/playwright-report/
/minecraft_runtime/node_modules/
/server_mod/build/
/.sync-backups/
/ci-artifacts/
/AGENTS.md
/agent.md
"""
DANGER = {
    "personal_contact": re.compile(
        r"(?i)[a-z0-9_.+\-]+@(?!(?:example\.(?:com|org|net)|localhost)\b)"
        r"[a-z0-9.\-]+\.[a-z]{2,}"
    ),
    "numeric_account_assignment": re.compile(r"(?i)\b(?:qq|uid|user_id)\s*[:=]\s*\d{7,12}\b"),
    "private_windows_workspace": re.compile(
        r"(?i)(?:[A-Z]:[/\\](?:WorkSpace(?: ZCode| DSH)?|Catoobot|UserData|"
        r"ComfyUI|RinsoraLoRA)(?:[/\\]|\b))"
    ),
    "private_user_profile": re.compile(
        r"(?i)[A-Z]:[/\\]Users[/\\](?!Example|Public|Default|User\\|User/)[^/\\\s\x22\x27]+"
    ),
    "private_unix_home": re.compile(r"(?i)(?:/Users/|/home/)(?!example/|user/)[A-Za-z0-9_.-]+/"),
    "raw_private_key": re.compile(r"-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----"),
    "known_token_patterns": re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9_]{25,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{22,})"
    ),
}

BANNED_COMPONENT_KEYS = {name.lower() for name in BANNED_COMPONENTS}
BANNED_NAME_KEYS = {name.lower() for name in BANNED_NAMES}


def acceptable(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    rel = path.relative_to(SOURCE)
    ancestors = (path, *path.parents)
    if any(p.is_symlink() or p.is_junction() for p in ancestors):
        return False
    if not path.resolve().is_relative_to(SOURCE.resolve()):
        return False
    if any(
        part.lower() in BANNED_COMPONENT_KEYS or part.lower().endswith(".egg-info")
        for part in rel.parts
    ):
        return False
    if path.name.lower() in BANNED_NAME_KEYS or (
        path.name.lower().startswith(".env.") and path.name != ".env.example"
    ):
        return False
    return not (path.suffix.lower() in BANNED_EXTS or path.stat().st_size > 2_000_000)


def candidate_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for d in PUBLIC_TREES:
        folder = SOURCE / d
        if folder.is_dir():
            for p in folder.rglob("*"):
                if acceptable(p):
                    files[p.relative_to(SOURCE).as_posix()] = p
    for name in PUBLIC_FILES:
        p = SOURCE / name
        if p.is_file() and acceptable(p):
            files[name] = p
    return dict(sorted(files.items()))


def audit(files: dict[str, Path]) -> list[dict[str, str]]:
    findings = []
    for rel, p in files.items():
        data = p.read_bytes()
        if b"\x00" in data:
            findings.append({"file": rel, "kind": "binary_content_unreviewed"})
            continue
        content = data.decode("utf-8", "replace")
        for code, regex in DANGER.items():
            if regex.search(content):
                findings.append({"file": rel, "kind": code})
        # Sensitive filenames or private doc paths are not allowed even if placeholders.
        if any(part in BANNED_COMPONENTS for part in Path(rel).parts):
            findings.append({"file": rel, "kind": "banned_component"})
    # A public fixture must not silently reproduce long, non-template lines
    # from the owner's private character bible. No private text is logged or
    # copied; only a file-level finding leaves this private workspace.
    private_bible = SOURCE / "config" / "character_bible.md"
    public_template = SOURCE / "config" / "character_bible.example.md"
    if private_bible.is_file():
        known_template_lines = (
            {line.strip() for line in public_template.read_text(encoding="utf-8").splitlines()}
            if public_template.is_file()
            else set()
        )
        protected_lines = {
            line.strip()
            for line in private_bible.read_text(encoding="utf-8").splitlines()
            if len(line.strip()) >= 32 and line.strip() not in known_template_lines
        }
        for rel, path in files.items():
            if rel == "config/character_bible.example.md" or path.suffix.lower() != ".md":
                continue
            candidate = path.read_text(encoding="utf-8", errors="replace")
            if any(line in candidate for line in protected_lines):
                findings.append({"file": rel, "kind": "private_bible_overlap_unapproved"})
    return findings


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def inside(child: Path, parent: Path) -> bool:
    """True only when the fully resolved child stays inside the resolved parent."""
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except OSError:  # pragma: no cover - unreadable path component
        return False


def safe_relative(rel: str) -> bool:
    """Reject absolute, drive-qualified, traversal and non-normalised path keys."""
    if not rel or rel != rel.strip() or "\\" in rel or "//" in rel:
        return False
    pure = Path(rel)
    if pure.drive or pure.root or pure.is_absolute():
        return False
    if any(part in ("", ".", "..") for part in rel.split("/")):
        return False
    return pure.as_posix() == rel


def worktree_files(root: Path) -> dict[str, Path]:
    """Map every regular file below root (Git metadata excluded); refuse any link."""
    if is_linked(root):
        raise SystemExit(f"Refusing a linked directory: {root}")
    if not root.is_dir():
        raise SystemExit(f"Directory is missing: {root}")
    files: dict[str, Path] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError as exc:
            raise SystemExit(f"Cannot list directory {current}: {exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            if current == root and entry.name == GIT_DIR:
                continue
            if entry.is_symlink() or is_linked(path):
                raise SystemExit(f"Refusing a symbolic link or junction: {path}")
            if entry.is_dir(follow_symlinks=False):
                stack.append(path)
            elif entry.is_file(follow_symlinks=False):
                files[path.relative_to(root).as_posix()] = path
            else:
                raise SystemExit(f"Refusing a non-regular file: {path}")
    return dict(sorted(files.items()))


def hash_map(files: dict[str, Path]) -> dict[str, str]:
    return {rel: sha256_file(path) for rel, path in files.items()}


def write_verified(source: Path, destination: Path) -> str:
    """Replace one file atomically and prove the bytes landed unchanged."""
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if is_linked(destination) or is_linked(destination.parent):
        raise SystemExit(f"Refusing to write through a link: {destination}")
    temporary = destination.with_name(destination.name + ".fu4-write.tmp")
    if temporary.exists():
        temporary.unlink()
    temporary.write_bytes(data)
    try:
        if sha256_file(temporary) != digest:
            raise SystemExit(f"Write verification failed for {destination.name}.")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    if sha256_file(destination) != digest:
        raise SystemExit(f"Post-replace verification failed for {destination.name}.")
    return digest


def ensure_directory(path: Path) -> None:
    """Create missing mirror directories one level at a time; refuse links/escape."""
    missing: list[Path] = []
    current = path
    while True:
        if current.exists():
            if is_linked(current) or not current.is_dir():
                raise SystemExit(f"Refusing a linked or non-directory mirror path: {current}")
            break
        missing.append(current)
        if current.parent == current:
            raise SystemExit(f"Refusing to create a mirror directory: {path}")
        current = current.parent
    for directory in reversed(missing):
        if not inside(directory, TARGET):
            raise SystemExit(f"Refusing to create a directory outside the mirror: {directory}")
        directory.mkdir()


def write_public_ignore(path: Path) -> str:
    """Write the managed ignore file exactly as stage always has; return its hash."""
    path.write_text(PUBLIC_IGNORES, encoding="utf-8")
    return sha256_file(path)


def write_report(path: Path, report: dict[str, object]) -> None:
    if inside(path, TARGET):
        raise SystemExit("Refusing to write a machine report inside the public mirror.")
    if is_linked(path) or is_linked(path.parent):
        raise SystemExit(f"Refusing a linked report path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compare(candidate: dict[str, Path], mirror: dict[str, Path]) -> dict[str, object]:
    """Added/modified/removed relative to the current worktree of the mirror."""
    wanted = hash_map(candidate)
    current = hash_map(mirror)
    shared = set(wanted) & set(current)
    return {
        "added": sorted(set(wanted) - set(current)),
        "modified": sorted(rel for rel in shared if wanted[rel] != current[rel]),
        "removed": sorted(set(current) - set(wanted)),
        "unchanged_count": len([rel for rel in shared if wanted[rel] == current[rel]]),
    }


def build_snapshot(files: dict[str, Path], snapshot_dir: Path) -> dict[str, object]:
    """Materialise the reviewed candidate into an independent, verifiable directory."""
    for boundary in (SOURCE, TARGET):
        if inside(snapshot_dir, boundary) or inside(boundary, snapshot_dir):
            raise SystemExit(
                "Snapshot directory must be independent of the private source tree "
                f"and the public mirror: {snapshot_dir}"
            )
    if snapshot_dir.exists():
        if is_linked(snapshot_dir) or not snapshot_dir.is_dir():
            raise SystemExit(f"Refusing a linked or non-directory snapshot path: {snapshot_dir}")
        if any(snapshot_dir.iterdir()):
            raise SystemExit(
                f"Refusing to write into a non-empty snapshot directory: {snapshot_dir}"
            )
    tree = snapshot_dir / SNAPSHOT_TREE
    tree.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for rel, source in files.items():
        destination = tree / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        hashes[rel] = write_verified(source, destination)
    hashes[MIRROR_IGNORE] = write_public_ignore(tree / MIRROR_IGNORE)
    record = {
        "snapshot": "LOCAL_CANDIDATE_SNAPSHOT_UNAPPROVED",
        "generated_utc": utc_stamp(),
        "file_count": len(hashes),
        "candidate_count": len(files),
        "sha256": dict(sorted(hashes.items())),
        "note": "Local candidate only; not reviewed, not committed, not published.",
    }
    (snapshot_dir / SNAPSHOT_MANIFEST).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return record


def read_snapshot(snapshot_dir: Path) -> tuple[dict[str, Path], dict[str, object]]:
    """Load a snapshot and prove every file still matches its recorded hash."""
    if is_linked(snapshot_dir) or not snapshot_dir.is_dir():
        raise SystemExit(f"Snapshot directory is missing or linked: {snapshot_dir}")
    manifest_path = snapshot_dir / SNAPSHOT_MANIFEST
    if is_linked(manifest_path) or not manifest_path.is_file():
        raise SystemExit(f"Snapshot manifest is missing: {manifest_path}")
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = worktree_files(snapshot_dir / SNAPSHOT_TREE)
    declared = record.get("sha256")
    if not isinstance(declared, dict) or set(declared) != set(files):
        raise SystemExit("Snapshot manifest does not match the snapshot tree; rebuild it.")
    for rel, path in files.items():
        if sha256_file(path) != declared[rel]:
            raise SystemExit(f"Snapshot file changed after review: {rel}")
    return files, record


def assert_snapshot_fresh(files: dict[str, Path]) -> None:
    """Refuse a stale snapshot: reviewed bytes must equal the live allowlist bytes."""
    if MIRROR_IGNORE not in files:
        raise SystemExit("Snapshot is missing the managed ignore file.")
    if files[MIRROR_IGNORE].read_text(encoding="utf-8") != PUBLIC_IGNORES:
        raise SystemExit("Snapshot ignore file does not match the reviewed ignore policy.")
    live = hash_map(candidate_files())
    snapshot = {rel: sha256_file(path) for rel, path in files.items() if rel != MIRROR_IGNORE}
    if snapshot == live:
        return
    missing = sorted(set(live) - set(snapshot))[:5]
    extra = sorted(set(snapshot) - set(live))[:5]
    changed = sorted(rel for rel in set(live) & set(snapshot) if live[rel] != snapshot[rel])[:5]
    raise SystemExit(
        "Snapshot is stale relative to the reviewed allowlist; rebuild it. "
        f"missing={missing} unexpected={extra} changed={changed}"
    )


def assert_no_sensitive_names(files: dict[str, Path]) -> None:
    for rel in files:
        parts = Path(rel).parts
        if any(part.lower() in BANNED_COMPONENT_KEYS for part in parts):
            raise SystemExit(f"Refusing a snapshot entry under a banned component: {rel}")
        name = parts[-1].lower()
        if name in BANNED_NAME_KEYS and name != MIRROR_IGNORE:
            raise SystemExit(f"Refusing a sensitive snapshot entry: {rel}")
        if name.startswith(".env.") and name != ".env.example":
            raise SystemExit(f"Refusing a sensitive snapshot entry: {rel}")


def mirror_gates() -> None:
    if is_linked(TARGET):
        raise SystemExit("Refusing a linked mirror directory.")
    if not TARGET.is_dir():
        raise SystemExit(f"Mirror directory is missing: {TARGET}")
    git_dir = TARGET / GIT_DIR
    if not git_dir.is_dir():
        raise SystemExit("Mirror must be a Git repo (created separately with no remote).")
    if is_linked(git_dir):
        raise SystemExit("Refusing linked Git metadata.")


def git_metadata_state() -> dict[str, object]:
    """Read-only view of the mirror Git metadata. Never runs git and never writes."""
    git_dir = TARGET / GIT_DIR
    state: dict[str, object] = {
        "git_dir_present": git_dir.is_dir() and not is_linked(git_dir),
        "head": None,
        "refs": [],
        "packed_refs_entries": 0,
        "remotes": [],
        "remote_url_configured": False,
        "file_count": 0,
        "fingerprint": None,
    }
    if not state["git_dir_present"]:
        return state
    head = git_dir / "HEAD"
    if head.is_file() and not is_linked(head):
        state["head"] = head.read_text(encoding="utf-8", errors="replace").strip()
    refs: list[str] = []
    refs_root = git_dir / "refs"
    if refs_root.is_dir():
        for path in sorted(refs_root.rglob("*")):
            if is_linked(path):
                raise SystemExit(f"Refusing a linked Git reference: {path}")
            if path.is_file():
                refs.append(path.relative_to(git_dir).as_posix())
    state["refs"] = refs
    packed = git_dir / "packed-refs"
    if packed.is_file() and not is_linked(packed):
        state["packed_refs_entries"] = len(
            [
                line
                for line in packed.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip() and not line.startswith("#")
            ]
        )
    config = git_dir / "config"
    if config.is_file() and not is_linked(config):
        text = config.read_text(encoding="utf-8", errors="replace")
        state["remotes"] = sorted(
            {match.group(1) for match in re.finditer(r'^\s*\[remote\s+"([^"]*)"\]', text, re.M)}
        )
        state["remote_url_configured"] = bool(re.search(r"^\s*url\s*=", text, re.M))
    fingerprint = hashlib.sha256()
    entries: list[str] = []
    for path in sorted(git_dir.rglob("*")):
        if is_linked(path):
            raise SystemExit(f"Refusing a linked Git metadata entry: {path}")
        if not path.is_file():
            continue
        size = path.stat().st_size
        rel = path.relative_to(git_dir).as_posix()
        if size <= 8_000_000:
            entries.append(f"{rel}:{size}:{sha256_file(path)}")
        else:  # pragma: no cover - no large Git metadata is expected in a local mirror
            entries.append(f"{rel}:{size}")
    fingerprint.update("\n".join(entries).encode("utf-8"))
    state["file_count"] = len(entries)
    state["fingerprint"] = fingerprint.hexdigest()
    return state


def previous_manifest_entries() -> set[str]:
    """Files recorded by the last local stage/update manifest (private, untrusted)."""
    path = SOURCE / "docs" / "PUBLIC_CANDIDATE_MANIFEST.local.json"
    if not path.is_file():
        return set()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    hashes = record.get("sha256")
    return set(hashes) if isinstance(hashes, dict) else set()


def write_local_manifest(
    files: dict[str, Path], *, label: str, extra: dict[str, object] | None = None
) -> str:
    manifest_path = SOURCE / "docs" / "PUBLIC_CANDIDATE_MANIFEST.local.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "snapshot": label,
        "file_count": len(files),
        "sha256": hash_map(files),
        "note": "Local candidate only; not reviewed, not committed, not published.",
    }
    if extra:
        record.update(extra)
    manifest_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return str(manifest_path)


def mirror_target_path(rel: str) -> Path:
    if not safe_relative(rel):
        raise SystemExit(f"Refusing an unsafe mirror path: {rel!r}")
    destination = TARGET / rel
    if not inside(destination, TARGET):
        raise SystemExit(f"Refusing a mirror path outside the mirror root: {rel}")
    return destination


def empty_directories() -> list[str]:
    found: list[str] = []
    for current, dirnames, _ in os.walk(TARGET, followlinks=False):
        here = Path(current)
        rel_dir = here.relative_to(TARGET)
        if rel_dir.parts and rel_dir.parts[0] == GIT_DIR:
            continue
        for name in sorted(dirnames):
            if not rel_dir.parts and name == GIT_DIR:
                continue
            path = here / name
            try:
                if not any(path.iterdir()):
                    found.append(path.relative_to(TARGET).as_posix())
            except OSError:
                continue
    return sorted(found)


def prune_empty_directories() -> list[str]:
    """Remove leftover empty directories below the mirror (never the Git metadata)."""
    pruned: list[str] = []
    for current, dirnames, _ in os.walk(TARGET, topdown=False, followlinks=False):
        here = Path(current)
        rel_dir = here.relative_to(TARGET)
        if rel_dir.parts and rel_dir.parts[0] == GIT_DIR:
            continue
        for name in sorted(dirnames):
            if not rel_dir.parts and name == GIT_DIR:
                continue
            path = here / name
            if not path.exists():
                continue
            if is_linked(path):
                raise SystemExit(f"Refusing a symbolic link or junction in the mirror: {path}")
            if any(path.iterdir()):
                continue
            path.rmdir()
            pruned.append(path.relative_to(TARGET).as_posix())
    return sorted(pruned)


def create_rollback_backup(
    mirror: dict[str, Path], *, root: Path | None = None
) -> dict[str, object]:
    """Verified, private copy of the current mirror worktree before any replacement."""
    base = (SOURCE / BACKUP_DIRNAME if root is None else root).resolve()
    if inside(base, TARGET):
        raise SystemExit("Refusing to write a rollback copy inside the public mirror.")
    if not inside(base, SOURCE):
        raise SystemExit("Refusing to write a rollback copy outside the private source tree.")
    if base.exists() and (is_linked(base) or not base.is_dir()):
        raise SystemExit(f"Refusing a linked or non-directory rollback root: {base}")
    base.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    destination = base / f"mirror-{stamp}"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite an existing rollback directory: {destination}")
    tree = destination / SNAPSHOT_TREE
    tree.mkdir(parents=True)
    hashes: dict[str, str] = {}
    for rel, source in mirror.items():
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        hashes[rel] = write_verified(source, target)
    if hash_map(worktree_files(tree)) != hashes:
        raise SystemExit("Rollback copy failed verification; nothing was replaced.")
    record = {
        "kind": "mirror-worktree-rollback",
        "created_utc": stamp,
        "mirror": str(TARGET),
        "file_count": len(hashes),
        "files": dict(sorted(hashes.items())),
        "empty_dirs": empty_directories(),
        "git_metadata": git_metadata_state(),
        "restore": (
            "Copy tree/<relative path> back over the mirror one file at a time; "
            "never copy .git and never delete the mirror root."
        ),
    }
    manifest = destination / ROLLBACK_MANIFEST
    manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "path": str(destination),
        "tree": str(tree),
        "manifest": str(manifest),
        "file_count": len(hashes),
        "created_utc": stamp,
    }


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def update_existing(
    snapshot_dir: Path,
    *,
    confirm: str | None,
    prune_empty: bool = True,
    backup_root: Path | None = None,
) -> dict[str, object]:
    """Apply a reviewed snapshot to an existing mirror worktree, file by file."""
    if confirm != UPDATE_CONFIRMATION:
        raise SystemExit(
            "Refusing to replace an existing release snapshot without an explicit "
            f"--confirm {UPDATE_CONFIRMATION}."
        )
    files, record = read_snapshot(snapshot_dir)
    assert_snapshot_fresh(files)
    assert_no_sensitive_names(files)
    problems = audit({rel: path for rel, path in files.items() if rel != MIRROR_IGNORE})
    if problems:
        raise SystemExit("Publication blocked by the snapshot privacy audit.")
    mirror_gates()
    before = git_metadata_state()
    if before["remotes"] or before["remote_url_configured"]:
        raise SystemExit("Refusing to update a mirror that has a Git remote configured.")
    mirror = worktree_files(TARGET)
    diff = compare(files, mirror)
    previous = previous_manifest_entries()
    unrecorded = [rel for rel in diff["removed"] if rel not in previous]
    if unrecorded:
        raise SystemExit(
            "Refusing: the mirror holds files that are neither reviewed candidates nor "
            f"recorded in the previous snapshot manifest: {unrecorded[:5]}"
        )
    rollback = create_rollback_backup(mirror, root=backup_root)
    written: list[str] = []
    for rel in sorted({*diff["modified"], *diff["added"]}):
        destination = mirror_target_path(rel)
        ensure_directory(destination.parent)
        write_verified(files[rel], destination)
        written.append(rel)
    deleted: list[str] = []
    for rel in diff["removed"]:
        destination = mirror_target_path(rel)
        if is_linked(destination):
            raise SystemExit(f"Refusing to delete a linked mirror path: {rel}")
        if destination.is_dir():
            raise SystemExit(f"Refusing to delete a mirror directory as a file: {rel}")
        destination.unlink()
        deleted.append(rel)
    pruned = prune_empty_directories() if prune_empty else []
    after = git_metadata_state()
    if after != before:
        raise SystemExit("Refusing: mirror Git metadata changed during the update.")
    verified = compare(files, worktree_files(TARGET))
    differences = len(verified["added"]) + len(verified["modified"]) + len(verified["removed"])
    if differences:
        raise SystemExit(
            f"Post-update hash verification failed; restore the rollback copy: {rollback['path']}"
        )
    git_commits = len(after["refs"]) + int(after["packed_refs_entries"])
    write_local_manifest(
        files,
        label="LOCAL_UPDATED_UNAPPROVED",
        extra={
            "updated_utc": utc_stamp(),
            "added": diff["added"],
            "modified": diff["modified"],
            "removed": diff["removed"],
            "rollback": rollback["path"],
        },
    )
    return {
        "mode": "update-existing",
        "generated_utc": utc_stamp(),
        "candidate_count": len(files) - 1,
        "managed_entries": len(files),
        "snapshot": {
            "path": str(snapshot_dir),
            "files": len(files),
            "snapshot_label": record.get("snapshot"),
        },
        "added": diff["added"],
        "modified": diff["modified"],
        "removed": diff["removed"],
        "unchanged_count": diff["unchanged_count"],
        "written_files": written,
        "deleted_files": deleted,
        "pruned_empty_dirs": pruned,
        "hash_verify": {
            "candidate_files": len(files),
            "mirror_files": len(files),
            "differences": differences,
            "verdict": "PASS" if differences == 0 else "FAIL",
            "added": verified["added"],
            "modified": verified["modified"],
            "removed": verified["removed"],
        },
        "rollback_snapshot_path": rollback["path"],
        "rollback_manifest": rollback["manifest"],
        "git_commits": git_commits,
        "git_head": after["head"],
        "git_refs": after["refs"],
        "git_metadata_fingerprint": after["fingerprint"],
        "git_metadata_unchanged": after == before,
        "remotes": after["remotes"],
        "privacy_findings": 0,
    }


def stage(files: dict[str, Path]) -> None:
    # Programmatic callers receive the same gates as the CLI, before any writes.
    if not files or files != candidate_files():
        raise SystemExit("Refusing a missing, stale, or non-allowlisted source snapshot.")
    if audit(files):
        raise SystemExit("Publication blocked by source privacy audit.")
    mirror_gates()
    other = [p for p in TARGET.iterdir() if p.name != GIT_DIR]
    if other:
        raise SystemExit(
            "Refusing to overwrite an existing release snapshot; clean/review separately."
        )
    for rel, p in files.items():
        dst = TARGET / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    (TARGET / MIRROR_IGNORE).write_text(PUBLIC_IGNORES, encoding="utf-8")
    write_local_manifest(files, label="LOCAL_UNAPPROVED")
    print(f"STAGED_LOCAL_CANDIDATE={len(files)} files; git remote unchanged; no commit.")


def snapshot_report(files: dict[str, Path], snapshot_dir: Path, record: dict) -> dict[str, object]:
    return {
        "mode": "make-snapshot",
        "generated_utc": utc_stamp(),
        "candidate_count": len(files),
        "managed_entries": int(record.get("file_count", 0)),
        "snapshot": {
            "path": str(snapshot_dir),
            "files": record.get("file_count"),
            "snapshot_label": record.get("snapshot"),
        },
        "privacy_findings": 0,
    }


def plan_report(
    files: dict[str, Path], snapshot_dir: Path, mirror: dict[str, Path], diff: dict
) -> dict[str, object]:
    git = git_metadata_state()
    return {
        "mode": "plan",
        "generated_utc": utc_stamp(),
        "candidate_count": len(files) - 1,
        "managed_entries": len(files),
        "snapshot": {"path": str(snapshot_dir), "files": len(files)},
        "added": diff["added"],
        "modified": diff["modified"],
        "removed": diff["removed"],
        "unchanged_count": diff["unchanged_count"],
        "mirror_files": len(mirror),
        "previous_manifest_gaps": sorted(
            set(mirror) - previous_manifest_entries() - {MIRROR_IGNORE}
        ),
        "git": {
            "commits": len(git["refs"]) + int(git["packed_refs_entries"]),
            "head": git["head"],
            "remotes": git["remotes"],
            "metadata_fingerprint": git["fingerprint"],
        },
    }


def verify_report(
    files: dict[str, Path], snapshot_dir: Path, mirror: dict[str, Path], diff: dict
) -> dict[str, object]:
    differences = len(diff["added"]) + len(diff["modified"]) + len(diff["removed"])
    git = git_metadata_state()
    return {
        "mode": "verify",
        "generated_utc": utc_stamp(),
        "candidate_count": len(files) - 1,
        "managed_entries": len(files),
        "snapshot": {"path": str(snapshot_dir), "files": len(files)},
        "snapshot_files": len(files),
        "mirror_files": len(mirror),
        "added": diff["added"],
        "modified": diff["modified"],
        "removed": diff["removed"],
        "hash_verify": {
            "candidate_files": len(files),
            "mirror_files": len(mirror),
            "differences": differences,
            "verdict": "PASS" if differences == 0 else "FAIL",
        },
        "git_commits": len(git["refs"]) + int(git["packed_refs_entries"]),
        "git_head": git["head"],
        "git_metadata_fingerprint": git["fingerprint"],
        "remotes": git["remotes"],
    }


def require_snapshot_dir(snapshot_dir: Path | None) -> Path:
    if snapshot_dir is None:
        raise SystemExit("--snapshot-dir is required for this mode.")
    return snapshot_dir.resolve()


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__)
    mode = a.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit", action="store_true", help="read-only allowlist/privacy scan")
    mode.add_argument(
        "--stage", action="store_true", help="stage into an empty local git mirror only"
    )
    mode.add_argument(
        "--make-snapshot",
        action="store_true",
        help="write the reviewed candidate into an independent snapshot directory",
    )
    mode.add_argument(
        "--plan", action="store_true", help="compare a snapshot with the mirror (read-only)"
    )
    mode.add_argument("--verify", action="store_true", help="re-verify a snapshot (read-only)")
    mode.add_argument(
        "--update-existing",
        action="store_true",
        help="explicit opt-in: replace managed files of the existing mirror worktree",
    )
    a.add_argument("--snapshot-dir", type=Path, help="independent snapshot directory")
    a.add_argument(
        "--confirm",
        help=f"exact token required by --update-existing ({UPDATE_CONFIRMATION})",
    )
    a.add_argument(
        "--keep-empty-dirs",
        action="store_true",
        help="do not remove leftover empty directories after --update-existing",
    )
    a.add_argument("--json-out", type=Path, help="write the machine report outside the mirror")
    args = a.parse_args()
    files = candidate_files()
    problems = audit(files)
    print(f"PUBLIC_CANDIDATE_FILES={len(files)}")
    print(f"PUBLIC_CANDIDATE_PRIVACY_FINDINGS={len(problems)}")
    for f in problems[:45]:
        print(f"BLOCKED_PATH={f['file']} REASON={f['kind']}")
    report: dict[str, object]
    if args.make_snapshot or args.plan or args.verify or args.update_existing:
        snapshot_dir = require_snapshot_dir(args.snapshot_dir)
        if args.make_snapshot:
            if problems:
                print("PUBLICATION_BLOCKED: correct in private workspace before snapshotting.")
                return 2
            record = build_snapshot(files, snapshot_dir)
            report = snapshot_report(files, snapshot_dir, record)
            print(f"SNAPSHOT_DIR={snapshot_dir}")
            print(f"SNAPSHOT_FILES={record['file_count']}")
            print("SNAPSHOT_SEALED: per-file SHA-256 recorded; review before applying.")
        elif args.plan:
            snapshot = read_snapshot(snapshot_dir)[0]
            assert_snapshot_fresh(snapshot)
            mirror = worktree_files(TARGET)
            diff = compare(snapshot, mirror)
            report = plan_report(snapshot, snapshot_dir, mirror, diff)
            print(f"PLAN_ADDED={len(diff['added'])}")
            print(f"PLAN_MODIFIED={len(diff['modified'])}")
            print(f"PLAN_REMOVED={len(diff['removed'])}")
            print(f"PLAN_UNCHANGED={diff['unchanged_count']}")
            for rel in diff["added"]:
                print(f"ADDED={rel}")
            for rel in diff["modified"]:
                print(f"MODIFIED={rel}")
            for rel in diff["removed"]:
                print(f"REMOVED={rel}")
        elif args.verify:
            snapshot = read_snapshot(snapshot_dir)[0]
            assert_snapshot_fresh(snapshot)
            mirror = worktree_files(TARGET)
            diff = compare(snapshot, mirror)
            report = verify_report(snapshot, snapshot_dir, mirror, diff)
            print(f"VERIFY_DIFFERENCES={report['hash_verify']['differences']}")
            print(f"GIT_COMMITS={report['git_commits']}")
            print(f"GIT_REMOTES={len(report['remotes'])}")
        else:
            report = update_existing(
                snapshot_dir,
                confirm=args.confirm,
                prune_empty=not args.keep_empty_dirs,
            )
            print(f"UPDATED_ADDED={len(report['added'])}")
            print(f"UPDATED_MODIFIED={len(report['modified'])}")
            print(f"UPDATED_REMOVED={len(report['removed'])}")
            for rel in list(report["modified"]) + list(report["added"]):
                print(f"WRITTEN={rel}")
            for rel in report["removed"]:
                print(f"DELETED={rel}")
            for rel in report["pruned_empty_dirs"]:
                print(f"PRUNED_EMPTY_DIR={rel}")
            print(f"ROLLBACK_SNAPSHOT={report['rollback_snapshot_path']}")
            print(f"HASH_VERIFY={report['hash_verify']['verdict']}")
            print(f"VERIFY_DIFFERENCES={report['hash_verify']['differences']}")
            print(f"GIT_COMMITS={report['git_commits']}")
            print(f"GIT_REMOTES={len(report['remotes'])}")
            print(f"GIT_METADATA_UNCHANGED={report['git_metadata_unchanged']}")
            print("LOCAL_WORKTREE_ONLY: worktree files only; no commit, push or remote.")
    else:
        if problems:
            print("PUBLICATION_BLOCKED: correct in private workspace before staging.")
            return 2
        if args.stage:
            stage(files)
        else:
            print("AUDIT_ONLY_PASS: still requires human review before commit/push.")
        return 0
    if args.json_out:
        write_report(args.json_out, report)
        print(f"MACHINE_REPORT={args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
