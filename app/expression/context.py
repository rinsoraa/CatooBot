"""Expression / 口癖 injection (Task 22): the prompt block for one group.

The block is *advisory* — a list of this group's phrases with an explicit
"still speak your own way" note. It never changes how she decides to reply; the
existing outbound post-processing (chunking, style limits, claim audit) still
applies unchanged, so a learned phrase can't turn her into a repeater.
"""

from __future__ import annotations

from app.config.settings import ExpressionConfig
from app.expression.store import ExpressionStore


async def expression_context(
    store: ExpressionStore,
    config: ExpressionConfig,
    group_id: str,
) -> tuple[str, list[int]]:
    """Return (prompt block, used pattern ids); ("", []) when nothing to inject."""
    if not config.enabled:
        return "", []
    patterns = await store.active_patterns(f"group:{group_id}")
    if not patterns:
        return "", []

    selected: list[int] = []
    lines: list[str] = []
    total = 0
    for pattern in patterns:
        if len(selected) >= config.inject_max_items:
            break
        if total + len(pattern.pattern) > config.inject_max_chars:
            continue
        selected.append(pattern.id)
        lines.append(pattern.pattern)
        total += len(pattern.pattern)
    if not lines:
        return "", []

    block = (
        "这个群的人习惯这样说（只是用词偏好，不是让你复读；仍按你自己的风格说）：\n"
        + "\n".join(f"  · {line}" for line in lines)
        + "\n{以上仅供参考，不要为了用而用；不自然就不用}"
    )
    return block, selected
