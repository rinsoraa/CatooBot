"""SAFE WRAPPER: stage a reviewed public allowlist; never commit or push.

The former sync script copied docs/specs and could publish private files.
It was archived privately under docs/archive, not in the public mirror.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if any(arg in ("--push", "--commit", "--force") for arg in sys.argv[1:]):
        print("BLOCKED: no automatic commit, push, or history rewrite.", file=sys.stderr)
        return 2
    if sys.argv[1:] not in (["--check"], ["--stage"]):
        print("Use: python sync_github.py --check | --stage (local-only)")
        return 2
    flag = "--audit" if sys.argv[1] == "--check" else "--stage"
    script = Path(__file__).resolve().parent / "scripts" / "build_public_mirror.py"
    return subprocess.call([sys.executable, str(script), flag])


if __name__ == "__main__":
    sys.exit(main())
