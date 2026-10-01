"""Builtin tool: current time / date in the character's timezone (spec §24.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.tools.errors import Tool as ToolBase
from app.tools.models import ToolContext, ToolMetadata, ToolResult

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_PERIODS = (
    (5, 8, "清晨"),
    (8, 12, "上午"),
    (12, 14, "中午"),
    (14, 18, "下午"),
    (18, 23, "晚上"),
    (23, 24, "深夜"),
    (0, 5, "深夜"),
)

METADATA = ToolMetadata(
    name="time",
    display_name="Time",
    description="查询当前日期、时间、星期和时区。",
    version="0.1.0",
    category="utility",
    tags=["time", "date", "clock"],
    keywords=["几点", "现在几点", "今天几号", "日期", "星期几", "什么时候", "现在时间"],
    when_to_use="用户询问当前时间、日期、星期，或需要知道“今天/现在”的具体值时。",
    when_not_to_use="用户问的是历史或未来某个具体日期时（那不是“现在”）。",
    limitations="只返回当前时刻，不做日期计算；时区取自角色配置。",
    input_schema={
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA 时区名，例如 Asia/Singapore；留空使用角色时区",
            }
        },
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    cache_ttl_seconds=15.0,
    timeout=3.0,
)


class TimeTool(ToolBase):
    metadata = METADATA

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        tz_name = str(arguments.get("timezone") or context.timezone or "Asia/Singapore")
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            tz_name = context.timezone or "Asia/Singapore"
            tz = ZoneInfo(tz_name)

        now = datetime.now(tz)
        period = next((label for start, end, label in _PERIODS if start <= now.hour < end), "现在")
        weekday = _WEEKDAYS[now.weekday()]
        summary = (
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M')}（{weekday}，{period}），时区 {tz_name}。"
        )
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "datetime": now.strftime("%Y-%m-%d %H:%M"),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M"),
                "weekday": weekday,
                "period": period,
                "timezone": tz_name,
            },
            summary=summary,
            metadata={
                "source_type": "internal",
                "confidence": 1.0,
                "source": "system clock",
                "timestamp": now.isoformat(),
            },
        )
