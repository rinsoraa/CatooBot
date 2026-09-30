"""CatooBot launcher: ``python run.py``.

Ensures the project root is importable no matter where it is invoked from.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
