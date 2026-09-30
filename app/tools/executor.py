"""Tool executor: the only place a tool actually runs (spec §15/§19/§20/§68).

    ToolCall → schema validation → policy → cache → execute (timeout,
    single retry for transient failures) → result processing → audit

Failures are normalized into :class:`ToolResult` with ``success=False`` so the
chat pipeline can always continue and the model is told "the tool failed"
instead of inventing an answer (§66).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.config.settings import ToolsConfig
from app.tools.errors import (
    ExternalServiceError,
    InvalidArgumentsError,
    ToolError,
    ToolTimeoutError,
)
from app.tools.models import ToolCall, ToolContext, ToolResult, ToolTrace
from app.tools.policy import ToolPolicy, TurnBudget
from app.tools.registry import ToolRegistry
from app.tools.result import ToolResultProcessor
from app.tools.schema import apply_defaults, validate_arguments

if TYPE_CHECKING:  # pragma: no cover
    from app.database.database import Database


def _narrate_tool_call(trace: Any, result: Any) -> None:
    """Console narration for one tool call (§ '工具' channel)."""
    try:
        from app.utils.narrator import narrate

        args = trace.arguments_preview.strip()
        if len(args) > 60:
            args = args[:60] + "…"
        outcome = "ok" if getattr(result, "success", False) else (
            getattr(result, "error_type", "") or "error"
        )
        detail = f"{trace.duration_ms:.0f}ms · {outcome}"
        if getattr(result, "cache_hit", False) or getattr(trace, "cache_hit", False):
            detail += " · 缓存"
        narrate().tool(f"{trace.tool_name}({args})", detail=detail)
    except Exception:  # noqa: BLE001 - narration must never break execution
        return


class ToolExecutor:
    def __init__(
        self,
        config: ToolsConfig,
        registry: ToolRegistry,
        policy: ToolPolicy,
        database: Database | None = None,
        processor: ToolResultProcessor | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self.config = config
        self.registry = registry
        self.policy = policy
        self._db = database
        self.processor = processor or ToolResultProcessor(logger)
        self._log = logger or logging.getLogger("CatooBot.Tools")
        self._clock = clock
        self._semaphore = asyncio.Semaphore(max(1, config.max_concurrent))
        self.traces: list[ToolTrace] = []          # bounded, in-memory audit tail
        self._max_traces = 200

    # ------------------------------------------------------------ execution

    async def execute(
        self,
        call: ToolCall,
        context: ToolContext,
        budget: TurnBudget,
    ) -> ToolResult:
        """Authorize and run one tool call; never raises."""
        started = time.perf_counter()
        trace = ToolTrace(
            trace_id=uuid.uuid4().hex[:12],
            tool_name=call.name,
            reason=call.reason,
            session_id=context.session_id,
            user_id=context.user_id,
            group_id=context.group_id,
            argument_sources=dict(call.argument_sources),
        )

        tool = self.registry.maybe_get(call.name)
        if tool is None:
            return await self._finish(
                trace, started, ToolResult(
                    tool_name=call.name,
                    success=False,
                    error=f"Unknown tool '{call.name}'",
                    error_type="unknown_tool",
                    metadata={"source_type": "internal", "confidence": 0.0},
                )
            )

        metadata = tool.metadata

        # 1) argument validation — bad arguments never reach the tool (§15)
        arguments = apply_defaults(metadata.input_schema or {}, dict(call.arguments))
        problems = validate_arguments(metadata.input_schema or {}, arguments)
        if problems:
            self._log.warning("[Tool] %s rejected arguments: %s", call.name, problems)
            return await self._finish(
                trace, started, ToolResult(
                    tool_name=call.name,
                    success=False,
                    error="; ".join(problems),
                    error_type="invalid_arguments",
                    metadata={"source_type": "internal", "confidence": 0.0},
                )
            )

        # 2) hard rules: permissions, budget, loops, rate limits (§21/§32/§34/§35)
        try:
            self.policy.authorize(
                metadata,
                call,
                context,
                budget,
                enabled=self.registry.is_enabled(call.name),
            )
        except ToolError as exc:
            self._log.info("[Tool] %s blocked: %s", call.name, exc)
            await self._record(
                trace, status="blocked", error_type=exc.error_type, started=started
            )
            return ToolResult(
                tool_name=call.name,
                success=False,
                error=str(exc),
                error_type=exc.error_type,
                metadata={"source_type": "internal", "confidence": 0.0},
            )

        # 3) cache (§68/§69) — TTL is declared by the tool itself
        cache_key = self._cache_key(call, arguments)
        if self.config.cache_enabled and metadata.cache_ttl_seconds > 0:
            cached = await self._cache_get(cache_key)
            if cached is not None:
                budget.register(call)
                self.policy.record_call(metadata, context)
                return await self._finish(
                    trace, started, cached.model_copy(update={"cache_hit": True}), cache_hit=True
                )

        # 4) run with timeout + limited retry
        budget.register(call)
        self.policy.record_call(metadata, context)
        timeout = metadata.timeout or self.config.default_timeout
        attempts = 1 + max(0, self.config.max_retries)
        result: ToolResult | None = None

        for attempt in range(1, attempts + 1):
            try:
                async with self._semaphore:
                    raw = await asyncio.wait_for(
                        tool.execute(arguments, context), timeout=timeout
                    )
                result = self.processor.process(raw)
                break
            except TimeoutError:
                result = self._error_result(
                    call.name, f"Tool '{call.name}' timed out after {timeout:.1f}s", "timeout"
                )
                retryable = True
            except ToolTimeoutError as exc:
                result = self._error_result(call.name, str(exc), "timeout")
                retryable = True
            except ExternalServiceError as exc:
                result = self._error_result(call.name, str(exc), "external_service")
                retryable = True
            except InvalidArgumentsError as exc:
                result = self._error_result(call.name, str(exc), "invalid_arguments")
                retryable = False
            except ToolError as exc:
                result = self._error_result(call.name, str(exc), exc.error_type)
                retryable = exc.retryable
            except Exception:  # noqa: BLE001 - a broken tool must not break chat
                self._log.exception("[Tool] %s crashed", call.name)
                result = self._error_result(
                    call.name, "tool raised an unexpected error", "internal"
                )
                retryable = False

            if attempt < attempts and retryable:
                self._log.info(
                    "[Tool] %s attempt %d failed (%s) — retrying once",
                    call.name,
                    attempt,
                    result.error_type,
                )
                continue
            if not retryable and attempt < attempts:
                self._log.debug(
                    "[Tool] %s failure is not retryable (%s)", call.name, result.error_type
                )
            break

        assert result is not None
        if result.success and self.config.cache_enabled and metadata.cache_ttl_seconds > 0:
            await self._cache_put(cache_key, result, metadata.cache_ttl_seconds)
        return await self._finish(trace, started, result)

    async def drain(self) -> None:
        """Deprecated no-op kept for shutdown symmetry (writes are awaited)."""
        return None

    async def execute_all(
        self,
        calls: list[ToolCall],
        context: ToolContext,
        budget: TurnBudget,
    ) -> list[ToolResult]:
        """Run independent calls concurrently (order preserved, §70)."""
        if len(calls) <= 1:
            return [await self.execute(call, context, budget) for call in calls]
        return list(
            await asyncio.gather(*(self.execute(call, context, budget) for call in calls))
        )

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _error_result(name: str, error: str, error_type: str) -> ToolResult:
        return ToolResult(
            tool_name=name,
            success=False,
            error=error,
            error_type=error_type,
            metadata={"source_type": "internal", "confidence": 0.0},
        )

    async def _finish(
        self,
        trace: ToolTrace,
        started: float,
        result: ToolResult,
        *,
        cache_hit: bool = False,
    ) -> ToolResult:
        result = result.model_copy(
            update={"execution_time": round(time.perf_counter() - started, 4)}
        )
        trace.duration_ms = round(result.execution_time * 1000, 2)
        trace.status = "ok" if result.success else "error"
        trace.error_type = result.error_type
        trace.cache_hit = cache_hit or result.cache_hit
        trace.result_summary = (result.summary or result.error)[:200]
        trace.arguments_hash = trace.arguments_hash or ""
        self._remember_trace(trace)
        _narrate_tool_call(trace, result)
        # Awaited, not fire-and-forget: a queued write could otherwise race with
        # database shutdown and crash sqlite (Windows access violation).
        await self._record(
            trace, status=trace.status, error_type=trace.error_type, started=started
        )
        return result

    def _remember_trace(self, trace: ToolTrace) -> None:
        self.traces.append(trace)
        if len(self.traces) > self._max_traces:
            del self.traces[: len(self.traces) - self._max_traces]

    async def _record(
        self,
        trace: ToolTrace,
        *,
        status: str,
        error_type: str,
        started: float,
    ) -> None:
        """Audit log (§36/§101) — never the full arguments, never secrets."""
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO tool_executions
                       (trace_id, tool_name, session_id, user_id, group_id, reason,
                        arguments_hash, arguments_preview, status, error_type,
                        cache_hit, duration_ms, result_summary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace.trace_id,
                    trace.tool_name,
                    trace.session_id,
                    trace.user_id,
                    trace.group_id,
                    trace.reason,
                    trace.arguments_hash,
                    trace.arguments_preview,
                    status,
                    error_type,
                    int(trace.cache_hit),
                    trace.duration_ms,
                    trace.result_summary,
                    int(self._clock()),
                ),
            )
        except Exception:  # noqa: BLE001 - auditing must never break execution
            self._log.debug("Failed to persist tool execution record", exc_info=True)

    def _cache_key(self, call: ToolCall, arguments: dict[str, Any]) -> str:
        import hashlib

        blob = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(f"{call.name}|{blob}".encode()).hexdigest()[:32]

    async def _cache_get(self, cache_key: str) -> ToolResult | None:
        if self._db is None:
            return None
        row = await self._db.fetchone(
            "SELECT payload, expires_at FROM tool_cache WHERE cache_key = ?", (cache_key,)
        )
        if row is None:
            return None
        if int(row["expires_at"]) < int(self._clock()):
            await self._db.execute("DELETE FROM tool_cache WHERE cache_key = ?", (cache_key,))
            return None
        try:
            return ToolResult.model_validate(json.loads(row["payload"]))
        except (TypeError, ValueError):
            return None

    async def _cache_put(self, cache_key: str, result: ToolResult, ttl: float) -> None:
        if self._db is None:
            return
        now = int(self._clock())
        try:
            await self._db.execute(
                """INSERT INTO tool_cache (cache_key, tool_name, payload, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                       payload=excluded.payload, created_at=excluded.created_at,
                       expires_at=excluded.expires_at""",
                (
                    cache_key,
                    result.tool_name,
                    json.dumps(result.model_dump(), ensure_ascii=False),
                    now,
                    now + int(ttl),
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to cache tool result", exc_info=True)

    async def clear_cache(self, tool_name: str = "") -> int:
        if self._db is None:
            return 0
        if tool_name:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM tool_cache WHERE tool_name = ?", (tool_name,)
            )
            await self._db.execute("DELETE FROM tool_cache WHERE tool_name = ?", (tool_name,))
        else:
            row = await self._db.fetchone("SELECT COUNT(*) AS n FROM tool_cache")
            await self._db.execute("DELETE FROM tool_cache")
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------- metrics

    async def metrics(self) -> dict[str, Any]:
        """Calls / success / failure / timeout / latency percentiles (§43/§79)."""
        empty = {
            "calls": 0, "success": 0, "failure": 0, "timeout": 0,
            "rate_limit": 0, "cache_hits": 0, "avg_latency_ms": 0.0,
            "p50_latency_ms": 0.0, "p95_latency_ms": 0.0, "by_tool": {},
        }
        if self._db is None:
            return empty

        rows = await self._db.fetchall(
            "SELECT tool_name, status, error_type, cache_hit, duration_ms"
            " FROM tool_executions ORDER BY id DESC LIMIT 2000"  # newest first
        )
        if not rows:
            return empty

        latencies: list[float] = []
        by_tool: dict[str, dict[str, Any]] = {}
        for row in rows:
            stats = by_tool.setdefault(
                row["tool_name"],
                {
                    "calls": 0, "success": 0, "failure": 0, "timeout": 0,
                    "rate_limit": 0, "cache_hits": 0, "latencies": [],
                },
            )
            stats["calls"] += 1
            stats["latencies"].append(float(row["duration_ms"]))
            if row["cache_hit"]:
                stats["cache_hits"] += 1
            if row["status"] == "ok":
                stats["success"] += 1
            else:
                stats["failure"] += 1
                if row["error_type"] == "timeout":
                    stats["timeout"] += 1
                if row["error_type"] == "rate_limit":
                    stats["rate_limit"] += 1
            latencies.append(float(row["duration_ms"]))

        for stats in by_tool.values():
            values = sorted(stats.pop("latencies") or [0.0])
            stats["avg_latency_ms"] = round(sum(values) / len(values), 2)
            stats["p95_latency_ms"] = round(_percentile(values, 95), 2)

        ordered = sorted(latencies)
        return {
            "calls": len(rows),
            "success": sum(1 for row in rows if row["status"] == "ok"),
            "failure": sum(1 for row in rows if row["status"] != "ok"),
            "timeout": sum(1 for row in rows if row["error_type"] == "timeout"),
            "rate_limit": sum(1 for row in rows if row["error_type"] == "rate_limit"),
            "cache_hits": sum(1 for row in rows if row["cache_hit"]),
            "avg_latency_ms": round(sum(ordered) / len(ordered), 2),
            "p50_latency_ms": round(_percentile(ordered, 50), 2),
            "p95_latency_ms": round(_percentile(ordered, 95), 2),
            "by_tool": by_tool,
        }

    async def recent_executions(self, limit: int = 50, tool_name: str = "") -> list[dict[str, Any]]:
        if self._db is None:
            return []
        if tool_name:
            rows = await self._db.fetchall(
                "SELECT * FROM tool_executions WHERE tool_name = ? ORDER BY id DESC LIMIT ?",
                (tool_name, limit),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT * FROM tool_executions ORDER BY id DESC LIMIT ?", (limit,)
            )
        return [dict(row) for row in rows]


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * (percentile / 100.0)
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
