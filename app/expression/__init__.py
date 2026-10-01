"""Expression / 口癖 learning (Task 22).

Short, group-scoped ways of talking she picked up from how a group chats.
Learning is opt-in and admission-gated; injection is advisory and stays inside
the existing style limits. See docs/V3_EXPRESSION_LEARNING.md.
"""

from app.expression.context import expression_context
from app.expression.learner import ExpressionLearner
from app.expression.store import ExpressionStore

__all__ = ["ExpressionLearner", "ExpressionStore", "expression_context"]
