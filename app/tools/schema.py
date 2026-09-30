"""JSON-Schema argument validation without extra dependencies (spec §14/§15).

Only the subset our tools actually use is implemented — object/array/string/
number/integer/boolean, ``required``, ``enum``, ``minLength``/``maxLength``,
``minimum``/``maximum``, ``pattern`` and ``additionalProperties``. Unknown
keywords are ignored rather than rejected, so schemas stay forward-compatible.
"""

from __future__ import annotations

import re
from typing import Any


def _matches_type(expected: str, value: Any) -> bool:
    """True when ``value`` satisfies a JSON-schema type name."""
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True


def validate_arguments(schema: dict[str, Any], arguments: Any) -> list[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    if not schema:
        return [] if isinstance(arguments, dict) else ["arguments must be an object"]
    if not isinstance(arguments, dict):
        return ["arguments must be an object"]
    return _validate_object(schema, arguments, path="")


def _validate_object(schema: dict[str, Any], value: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    properties: dict[str, Any] = schema.get("properties") or {}
    required = schema.get("required") or []

    for name in required:
        if name not in value or value[name] in (None, ""):
            errors.append(f"{_join(path, name)} is required")

    additional = schema.get("additionalProperties", True)
    for name, raw in value.items():
        if name not in properties:
            if additional is False:
                errors.append(f"{_join(path, name)} is not an allowed parameter")
            continue
        errors.extend(_validate_value(properties[name], raw, _join(path, name)))
    return errors


def _validate_value(spec: dict[str, Any], value: Any, path: str) -> list[str]:
    errors: list[str] = []
    expected = spec.get("type")
    if expected and not _matches_type(str(expected), value):
        errors.append(f"{path} must be a {expected}")
        return errors

    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{path} must be one of {spec['enum']}")

    if isinstance(value, str):
        if (min_length := spec.get("minLength")) is not None and len(value) < min_length:
            errors.append(f"{path} is shorter than {min_length} characters")
        if (max_length := spec.get("maxLength")) is not None and len(value) > max_length:
            errors.append(f"{path} is longer than {max_length} characters")
        if pattern := spec.get("pattern"):
            try:
                if not re.search(pattern, value):
                    errors.append(f"{path} does not match the expected format")
            except re.error:
                pass

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if (minimum := spec.get("minimum")) is not None and value < minimum:
            errors.append(f"{path} must be >= {minimum}")
        if (maximum := spec.get("maximum")) is not None and value > maximum:
            errors.append(f"{path} must be <= {maximum}")

    if isinstance(value, list):
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_value(item_spec, item, f"{path}[{index}]"))
        if (max_items := spec.get("maxItems")) is not None and len(value) > max_items:
            errors.append(f"{path} has more than {max_items} items")

    if isinstance(value, dict) and spec.get("properties"):
        errors.extend(_validate_object(spec, value, path))

    return errors


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def apply_defaults(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Fill in schema defaults for parameters the model omitted."""
    result = dict(arguments)
    for name, spec in (schema.get("properties") or {}).items():
        if name not in result and isinstance(spec, dict) and "default" in spec:
            result[name] = spec["default"]
    return result


def tool_json_schema(metadata: Any) -> dict[str, Any]:
    """OpenAI-style function schema for one tool (native tool calling)."""
    return {
        "type": "function",
        "function": {
            "name": metadata.name,
            "description": metadata.description,
            "parameters": metadata.input_schema or {"type": "object", "properties": {}},
        },
    }
