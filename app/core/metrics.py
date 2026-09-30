"""Lightweight runtime counters for the WebUI dashboard."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any


class Metrics:
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self.started_at = time.time()

    def inc(self, key: str, amount: int = 1) -> None:
        self._counters[key] += amount

    def get(self, key: str) -> int:
        return self._counters.get(key, 0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_seconds": int(time.time() - self.started_at),
            "messages_received": self.get("messages_received"),
            "ai_requests": self.get("ai_requests"),
            "ai_errors": self.get("ai_errors"),
            "rate_limited": self.get("rate_limited"),
            "replies_sent": self.get("replies_sent"),
            "memories_extracted": self.get("memories_extracted"),
        }
