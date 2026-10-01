"""Builtin tool: arithmetic, percentages and unit conversion (spec §24.2).

Deliberately **not** an eval: expressions are parsed with :mod:`ast` and only
arithmetic node types are allowed, so a model cannot smuggle code in through
the calculator (安全边界, spec §23/§106).
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Any

from app.tools.errors import InvalidArgumentsError
from app.tools.errors import Tool as ToolBase
from app.tools.models import ToolContext, ToolMetadata, ToolResult

_BINARY_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# unit -> (dimension, factor to the dimension's base unit)
_UNITS: dict[str, tuple[str, float]] = {
    "mm": ("length", 0.001),
    "cm": ("length", 0.01),
    "m": ("length", 1.0),
    "km": ("length", 1000.0),
    "inch": ("length", 0.0254),
    "ft": ("length", 0.3048),
    "mile": ("length", 1609.344),
    "mg": ("mass", 1e-6),
    "g": ("mass", 0.001),
    "kg": ("mass", 1.0),
    "t": ("mass", 1000.0),
    "lb": ("mass", 0.45359237),
    "ml": ("volume", 0.001),
    "l": ("volume", 1.0),
    "m3": ("volume", 1000.0),
    "c": ("temperature", 1.0),
    "f": ("temperature", 1.0),
    "k": ("temperature", 1.0),
}

METADATA = ToolMetadata(
    name="calculator",
    display_name="Calculator",
    description="计算数学表达式、百分比以及常见单位换算，结果精确可靠。",
    version="0.1.0",
    category="utility",
    tags=["math", "calculate", "convert"],
    keywords=["计算", "算一下", "等于多少", "是多少", "换算", "多少度", "百分之"],
    when_to_use="需要进行算术、百分比或单位换算时；涉及数字精确结果时优先使用。",
    when_not_to_use="纯概念解释、无需精确数字时不要调用。",
    limitations="支持四则运算、幂、取模、百分比和长度/质量/体积/温度换算；不支持符号运算。",
    input_schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "要计算的表达式，例如 12893 * 473"},
            "operation": {
                "type": "string",
                "description": "percent = 计算 x 的百分之 y；convert = 单位换算",
                "enum": ["evaluate", "percent", "convert"],
                "default": "evaluate",
            },
            "value": {"type": "number", "description": "percent 的基数"},
            "percent": {"type": "number", "description": "percent 的百分比，例如 15 表示 15%"},
            "amount": {"type": "number", "description": "convert 的数值"},
            "from_unit": {"type": "string", "description": "convert 的原单位，如 km"},
            "to_unit": {"type": "string", "description": "convert 的目标单位，如 mile"},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    cache_ttl_seconds=300.0,
    timeout=3.0,
)


class CalculatorTool(ToolBase):
    metadata = METADATA

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        operation = str(arguments.get("operation") or "evaluate")
        if operation == "percent":
            result = self._percent(arguments)
            expression = f"{arguments.get('value')} 的 {arguments.get('percent')}%"
        elif operation == "convert":
            result, expression = self._convert(arguments)
        else:
            expression = str(arguments.get("expression") or "").strip()
            if not expression:
                raise InvalidArgumentsError("expression is required for evaluate")
            result = self.evaluate(expression)

        number = _format_number(result)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"expression": expression, "result": result, "formatted": number},
            summary=f"{expression} = {number}",
            metadata={
                "source_type": "internal",
                "confidence": 1.0,
                "source": "calculator",
            },
        )

    # ------------------------------------------------------------- helpers

    @staticmethod
    def evaluate(expression: str) -> float:
        """Evaluate a arithmetic-only expression. Raises InvalidArgumentsError."""
        cleaned = expression.replace("×", "*").replace("÷", "/").replace("−", "-").replace("，", "")
        try:
            tree = ast.parse(cleaned, mode="eval")
        except SyntaxError as exc:
            raise InvalidArgumentsError(f"cannot parse expression: {expression}") from exc
        if len(cleaned) > 200:
            raise InvalidArgumentsError("expression is too long")
        try:
            return float(_eval_node(tree.body))
        except ZeroDivisionError as exc:
            raise InvalidArgumentsError("division by zero") from exc
        except OverflowError as exc:
            raise InvalidArgumentsError("number is too large") from exc

    @staticmethod
    def _percent(arguments: dict[str, Any]) -> float:
        try:
            value = float(arguments["value"])
            percent = float(arguments["percent"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidArgumentsError("percent requires numeric value and percent") from exc
        return value * percent / 100.0

    @staticmethod
    def _convert(arguments: dict[str, Any]) -> tuple[float, str]:
        try:
            amount = float(arguments["amount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidArgumentsError("convert requires a numeric amount") from exc
        from_unit = str(arguments.get("from_unit", "")).lower()
        to_unit = str(arguments.get("to_unit", "")).lower()
        if from_unit not in _UNITS or to_unit not in _UNITS:
            raise InvalidArgumentsError(f"unsupported unit: {from_unit} -> {to_unit}")
        dimension, factor = _UNITS[from_unit]
        target_dimension, target_factor = _UNITS[to_unit]
        if dimension != target_dimension:
            raise InvalidArgumentsError(f"cannot convert {dimension} to {target_dimension}")
        if dimension == "temperature":
            value = _convert_temperature(amount, from_unit, to_unit)
        else:
            value = amount * factor / target_factor
        return value, f"{_format_number(amount)}{from_unit} -> {to_unit}"


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise InvalidArgumentsError("only numbers are allowed")
        return float(node.value)
    if isinstance(node, ast.BinOp):
        binary_op = _BINARY_OPS.get(type(node.op))
        if binary_op is None:
            raise InvalidArgumentsError("unsupported operator")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 64:
            raise InvalidArgumentsError("exponent is too large")
        return binary_op(left, right)
    if isinstance(node, ast.UnaryOp):
        unary_op = _UNARY_OPS.get(type(node.op))
        if unary_op is None:
            raise InvalidArgumentsError("unsupported unary operator")
        return unary_op(_eval_node(node.operand))
    raise InvalidArgumentsError("only arithmetic expressions are supported")


def _convert_temperature(amount: float, from_unit: str, to_unit: str) -> float:
    """Celsius is the pivot unit; explicit branches keep this obvious."""
    if from_unit == "c":
        celsius = amount
    elif from_unit == "f":
        celsius = (amount - 32) * 5 / 9
    else:  # kelvin
        celsius = amount - 273.15

    if to_unit == "c":
        return celsius
    if to_unit == "f":
        return celsius * 9 / 5 + 32
    return celsius + 273.15


def _format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"
