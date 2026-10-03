"""W5 运行时只读快照助手：``process`` / ``onebot`` / ``database.size_bytes``。

从 :mod:`app.web.services.read_model` 拆出的纯投影函数（文件行数约束），
它们只读既有访问器（``Bot.started_at`` / ``OneBotGateway.stats()`` / 配置里的
sqlite 路径），不缓存、不写任何状态；拿不到就返回 ``None``，绝不编造。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from app.config.settings import project_path


def process_block(bot: Any) -> dict[str, Any]:
    """进程事实：版本/Python/启动时间（未启动时 uptime/started_at 为 null）。"""
    from app.main import VERSION

    started = float(getattr(bot, "started_at", 0.0) or 0.0)
    return {
        "uptime_seconds": round(time.time() - started, 3) if started else None,
        "started_at": started or None,
        "version": VERSION,
        "python": sys.version.split()[0],
    }


def onebot_block(bot: Any) -> dict[str, Any]:
    """QQ 网关的只读计数与 lane 深度（``OneBotGateway.stats()``）。

    网关未启用时 state=``disabled``、计数为 null、lanes 为空 —— 不编造 0。
    """
    gateway = getattr(bot, "onebot_gateway", None)
    if gateway is None:
        return {
            "state": "disabled",
            "connected": False,
            "self_id": None,
            "last_event_at": None,
            "received": None,
            "accepted": None,
            "deduped": None,
            "dropped": None,
            "self_ignored": None,
            "responses": None,
            "sent": None,
            "failed": None,
            "pending_outbound": None,
            "busy": None,
            "lanes": [],
        }
    return dict(gateway.stats())


def database_size_bytes(bot: Any, *, connected: bool) -> int | None:
    """sqlite 文件大小；内存库 / 不可读 / 未连接时为 null（不猜）。"""
    if not connected or getattr(bot, "database", None) is None:
        return None
    config = getattr(bot, "config", None)
    db_config = getattr(config, "database", None)
    url = str(getattr(db_config, "url", "") or "")
    if not url.startswith("sqlite:") or ":memory:" in url:
        return None
    raw = url[len("sqlite:///") :] if url.startswith("sqlite:///") else url[len("sqlite://") :]
    if not raw:
        return None
    try:
        path = Path(raw)
        if not path.is_absolute():
            path = project_path(raw)
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None
