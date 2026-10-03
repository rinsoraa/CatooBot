"""`/api/v1` JSON 层公共设施（WebUI v1.0 · W2）。

这一层只做四件事：统一信封、统一错误映射、请求体校验、点号键的读写。
业务语义一律留在既有 Admin Service / Config System / AI Router 里。

契约见 ``docs/WEBUI_API_CONTRACT.md``；本模块是它的可执行版本。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from app.web.api_errors import (  # noqa: F401 - re-exported for route modules
    ApiError,
    bad_request,
    conflict,
    forbidden,
    not_found,
    rate_limited,
    redact_message,
    unauthorized,
    unavailable,
    unprocessable,
)

log = logging.getLogger("CatooBot.Web.API")

API_PREFIX = "/api/v1"

#: per-request id, stored on the request so every response can echo it
REQUEST_ID_KEY: web.RequestKey[str] = web.RequestKey("api_request_id", str)

#: 单个 JSON 请求体的上限（配置/YAML 编辑也远小于此）
MAX_BODY_BYTES = 1 << 20


# --------------------------------------------------------------------- errors


# ------------------------------------------------------------------- envelope


def _request_id(request: web.Request | None) -> str:
    if request is not None:
        existing = request.get(REQUEST_ID_KEY)
        if isinstance(existing, str) and existing:
            return existing
    return secrets.token_hex(6)


def ok(data: Any = None, *, request: web.Request | None = None, status: int = 200) -> web.Response:
    """The one success shape: ``{"ok": true, "data": ..., "meta": {...}}``."""
    payload: dict[str, Any] = {
        "ok": True,
        "data": data,
        "meta": {"request_id": _request_id(request)},
    }
    return web.json_response(payload, status=status, dumps=_dumps)


def fail(error: ApiError, *, request: web.Request | None = None) -> web.Response:
    body: dict[str, Any] = {"code": error.code, "message": error.message, "detail": error.detail}
    if error.field:
        body["field"] = error.field
    payload = {"ok": False, "error": body, "meta": {"request_id": _request_id(request)}}
    return web.json_response(payload, status=error.status, dumps=_dumps)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


Handler = Callable[[web.Request], Awaitable[web.Response]]


def json_endpoint(handler: Handler) -> Handler:
    """Translate ordinary exceptions into the contract envelope (once, here).

    Routes stay free of ``try/except``; a raised :class:`ApiError` is the
    normal way to reject a request.  Unexpected exceptions are logged with a
    traceback and answered as an opaque 500 — details never travel.
    """

    async def wrapper(request: web.Request) -> web.Response:
        request[REQUEST_ID_KEY] = secrets.token_hex(6)
        try:
            return await handler(request)
        except ApiError as exc:
            return fail(exc, request=request)
        except ValueError as exc:
            return fail(unprocessable(redact_message(str(exc)) or "输入不合法"), request=request)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - the boundary of the API layer
            from pydantic import ValidationError

            if isinstance(exc, ValidationError):
                errors = exc.errors()
                field = (
                    ".".join(str(part) for part in errors[0].get("loc", ())) or None
                    if errors
                    else None
                )
                return fail(
                    unprocessable("字段校验失败", field=field, detail=redact_message(str(exc))),
                    request=request,
                )
            log.exception("[API] %s %s failed", request.method, request.path)
            return fail(
                ApiError(500, "internal.error", f"服务器内部错误：{redact_message(str(exc))}"),
                request=request,
            )

    wrapper.__name__ = getattr(handler, "__name__", "handler")
    return wrapper


# ---------------------------------------------------------------- request body


async def read_json(request: web.Request, *, required: bool = True) -> dict[str, Any]:
    """Parse a JSON object body with a hard size cap."""
    if request.content_length and request.content_length > MAX_BODY_BYTES:
        raise bad_request("请求体过大", code="request.too_large")
    raw = await request.read()
    if not raw:
        if required:
            raise bad_request("缺少 JSON 请求体", code="request.empty")
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise bad_request(f"JSON 解析失败：{exc}", code="request.malformed") from exc
    if not isinstance(data, dict):
        raise bad_request("请求体必须是 JSON 对象", code="request.malformed")
    return data


def read_query_int(
    request: web.Request, name: str, *, default: int, minimum: int, maximum: int
) -> int:
    raw = request.query.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise bad_request(f"{name} 必须是整数", field=name) from exc
    if not minimum <= value <= maximum:
        raise bad_request(f"{name} 必须在 {minimum}~{maximum} 之间", field=name)
    return value


# ------------------------------------------------------------------ dotted keys

#: ``ai.models[0].name`` / ``ai.providers.<n>.base_url`` / ``runtime.tick_seconds``
_KEY_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_-]*)(?:\[(\d+)\]|\.<[a-z_]+>|\.([A-Za-z_][A-Za-z0-9_-]*))*"
)
_SEGMENT_RE = re.compile(r"\.?([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]|\.<[a-z_]+>")


def split_key(key: str) -> list[str | int]:
    """``ai.models[0].name`` → ``["ai", "models", 0, "name"]``.

    ``<n>``-style wildcard placeholders are dropped (they address "all of"),
    so ``ai.providers.<n>.type`` → ``["ai", "providers", "type"]``.
    """
    if not _KEY_RE.fullmatch(key):
        raise bad_request(f"非法的配置键：{key}", code="config.unknown_key", field=key)
    parts: list[str | int] = []
    for match in _SEGMENT_RE.finditer(key):
        name, index = match.group(1), match.group(2)
        if index is not None:
            parts.append(int(index))
        elif name is not None:
            parts.append(name)
        # a ``<n>`` placeholder contributes nothing
    if not parts:
        raise bad_request(f"非法的配置键：{key}", code="config.unknown_key", field=key)
    return parts


def dot_get(data: Any, key: str) -> Any:
    node = data
    for part in split_key(key):
        if isinstance(part, int):
            if not isinstance(node, list) or part >= len(node):
                return None
            node = node[part]
        else:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
    return node


def nested(pairs: dict[str, Any]) -> dict[str, Any]:
    """``{"ai.models[0].name": "x"}`` → nested dict ready for ``deep_merge``."""
    root: dict[str, Any] = {}
    for key, value in pairs.items():
        parts = split_key(key)
        node: Any = root
        for position, part in enumerate(parts):
            last = position == len(parts) - 1
            if isinstance(part, int):
                if not isinstance(node, list):
                    raise bad_request(f"配置键的列表下标位置不对：{key}", field=key)
                while len(node) <= part:
                    node.append(None)
                if last:
                    node[part] = value
                else:
                    if node[part] is None:
                        node[part] = [] if isinstance(parts[position + 1], int) else {}
                    node = node[part]
                continue
            if not isinstance(node, dict):
                raise bad_request(f"配置键的层级位置不对：{key}", field=key)
            if last:
                node[part] = value
            else:
                nxt = parts[position + 1]
                if not isinstance(node.get(part), (dict, list)):
                    node[part] = [] if isinstance(nxt, int) else {}
                node = node[part]
    return root


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """Inverse of :func:`nested` — used to diff effective values."""
    flat: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            flat.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            flat.update(flatten(value, f"{prefix}[{index}]"))
    else:
        flat[prefix] = data
    return flat
