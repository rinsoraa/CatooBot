"""Builtin tool: query the images she has seen (Task 21 milestone 3).

Read-only: it reuses the memory manager's existing ``list_memories(source=…)``
— the same scope-filtered path the WebUI uses — so it adds no new SQL or
database access surface. It never re-downloads or re-analyses an image: the
visual summary was already computed and cached by :mod:`app.media.vision`, and
this tool only surfaces the *memory* derived from it.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import Tool as ToolBase
from app.tools.models import ToolContext, ToolMetadata, ToolResult

METADATA = ToolMetadata(
    name="query_image_memory",
    display_name="Query Image Memory",
    description="查询她看过、并记进记忆的图片（图片内容摘要、场景、文字）。",
    version="0.1.0",
    category="memory",
    tags=["memory", "image", "vision", "recall"],
    keywords=["我看过", "那张图", "之前发的图", "图片写了什么", "图里是什么", "看过的照片"],
    when_to_use="用户问起她之前看过/收到的某张图的内容、上面的文字或场景，需要主动回看图片记忆时。",
    when_not_to_use="图片从未被视觉识别、或自然回忆已经答得上时（不必多此一举）。",
    limitations="只返回已写入记忆的图片摘要；不会重新调用视觉模型，也不会重新下载原图。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要回想的关键词（图里的文字、物体、场景等）；留空返回最近的图片记忆",
            }
        },
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=5.0,
)


class QueryImageMemoryTool(ToolBase):
    metadata = METADATA

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        manager = context.metadata.get("memory")
        if manager is None:
            return self.failure(
                self.metadata.name, "记忆不可用（memory disabled）", error_type="unavailable"
            )
        query = str(arguments.get("query", "")).strip()
        scope_key = context.session_id.replace("private:", "user:") if context.session_id else ""
        try:
            memories = await manager.list_memories(
                source="vision", keyword=query, scope_key=scope_key, status="active", limit=10
            )
        except Exception as exc:  # noqa: BLE001 - surface as a tool failure, never raise
            return self.failure(
                self.metadata.name, f"检索图片记忆失败：{exc}", error_type="tool_error"
            )

        items = [
            {
                "content": memory.content,
                "summary": memory.summary or "",
                "category": memory.category,
                "created_at": memory.created_at,
            }
            for memory in memories
        ]
        if not items:
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={"memories": []},
                summary="没有找到相关的图片记忆（她可能还没看过或没记住这张图）。",
                metadata={"source_type": "memory", "confidence": 0.0, "count": 0},
            )
        summary = "；".join(item["summary"] or item["content"][:40] for item in items[:3])
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"memories": items},
            summary=f"找到 {len(items)} 条图片记忆：{summary}",
            metadata={"source_type": "memory", "confidence": 0.8, "count": len(items)},
        )
