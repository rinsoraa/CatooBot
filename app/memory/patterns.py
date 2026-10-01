"""Shared regexes for judging whether text should be kept as a memory/pattern.

Lives in its own module so ``app.expression`` can reuse the judgement gate
without importing the whole :mod:`app.memory.manager` (which drags in the
retrieval/embedding graph and, with it, a type-resolution cycle for mypy).
"""

from __future__ import annotations

import re

# Sensitive "judgement about the character" phrasing — never stored as a fact.
_FORBIDDEN_PATTERNS = re.compile(
    r"(很蠢|很笨|智力低|智商低|性格缺陷|心理有问题|心理疾病|人格缺陷|人格评分|好感度\s*\d+|恋爱值\s*\d+)"
)

# Prompt-rewrite attempts are kept only as a plain user wish, never an order.
_INJECTION_PATTERNS = re.compile(
    r"(系统提示词|system\s*prompt|ignore previous|忽略之前|你现在是|你的设定改成)"
)
