"""``.env`` 存取：写临时文件 → flush → 原子替换（WebUI v1.0 · W2 §21）。

WebUI 只借它写/删单个 Secret 变量；既有行（含注释、空行、顺序）原样保留，
写坏不可能发生在半途——要么旧文件，要么完整新文件。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

#: ``NAME=value`` with an optional ``export`` prefix
LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_path() -> Path:
    from app.config.settings import PROJECT_ROOT

    return PROJECT_ROOT / ".env"


def valid_name(name: str) -> bool:
    return bool(NAME_RE.fullmatch(name))


def read_env(path: Path | None = None) -> dict[str, str]:
    """The variables currently stored in the file (no shell expansion)."""
    target = path or env_path()
    if not target.exists():
        return {}
    values: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        match = LINE_RE.match(line)
        if match:
            values[match.group(1)] = line.split("=", 1)[1].strip()
    return values


def _write_lines(target: Path, lines: list[str]) -> None:
    """Atomic replace: a reader never sees a half-written file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + ("\n" if lines else ""))
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            os.chmod(tmp_name, target.stat().st_mode & 0o777)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_env_secret(name: str, value: str, *, path: Path | None = None) -> Path:
    """Set ``NAME=value``, keeping every other line exactly as it was."""
    if not valid_name(name):
        raise ValueError(f"非法的环境变量名：{name!r}")
    target = path or env_path()
    lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    replaced = False
    for index, line in enumerate(lines):
        match = LINE_RE.match(line)
        if match and match.group(1) == name:
            lines[index] = f"{name}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{name}={value}")
    _write_lines(target, lines)
    return target


def remove_env_secret(name: str, *, path: Path | None = None) -> bool:
    """Drop the variable's line; other lines are untouched. Returns ``False`` if absent."""
    if not valid_name(name):
        raise ValueError(f"非法的环境变量名：{name!r}")
    target = path or env_path()
    if not target.exists():
        return False
    lines = target.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    for line in lines:
        match = LINE_RE.match(line)
        if match and match.group(1) == name:
            continue
        kept.append(line)
    if len(kept) == len(lines):
        return False
    _write_lines(target, kept)
    return True
