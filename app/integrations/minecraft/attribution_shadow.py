"""P2 影子归因接收端（独立模块，默认关闭，影子数据不参与任何任务终态）。"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SHADOW_STATES = (
    "SELF_CONFIRMED",
    "UNKNOWN",
    "REJECTED",
    "AMBIGUOUS",
    "UNREGISTERED_BREAK",
    "EXTERNAL_CONFIRMED",
)


def sign(body: bytes, token: str) -> str:
    return hmac.new(token.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(body: bytes, token: str, signature: str | None) -> bool:
    if not token or not signature:
        return False
    return hmac.compare_digest(sign(body, token), signature.lower())


class ShadowLedger:
    """只做记录与统计：不返回任何可被任务消费的授权/成功信号。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.seen_events: set[str] = set()
        self.rejected_signature = 0
        self.duplicate_events = 0
        self.late_events = 0
        self.states: Counter[str] = Counter()
        self.reasons: Counter[str] = Counter()
        self.samples: list[dict[str, Any]] = []

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "")
        with self._lock:
            if event_id and event_id in self.seen_events:
                self.duplicate_events += 1
                return {"ok": True, "consumed": False, "reason": "duplicate_event"}
            if event_id:
                self.seen_events.add(event_id)
            state = str(payload.get("shadow") or "UNKNOWN").upper()
            if state not in SHADOW_STATES:
                state = "UNKNOWN"
            reason = str(payload.get("reason") or "")
            self.states[state] += 1
            if reason:
                self.reasons[reason] += 1
            self.samples.append({**payload, "shadow": state})
            return {"ok": True, "consumed": True, "state": state}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total = sum(self.states.values())
            confirmed = self.states.get("SELF_CONFIRMED", 0)
            unknown = self.states.get("UNKNOWN", 0)
            return {
                "total_events": total,
                "shadow_states": dict(self.states),
                "reasons": dict(self.reasons),
                "match_rate": round(confirmed / total, 4) if total else 0.0,
                "unknown_ratio": round(unknown / total, 4) if total else 0.0,
                "duplicate_events": self.duplicate_events,
                "rejected_signature": self.rejected_signature,
                "late_events": self.late_events,
            }


def build_handler(ledger: ShadowLedger, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # 安静：影子通道不污染日志
            return

        def _respond(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/attribution/stats":
                self._respond(200, ledger.snapshot())
            else:
                self._respond(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/attribution/event":
                self._respond(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            signature = self.headers.get("X-Signature")
            if not verify(body, token, signature):
                ledger.rejected_signature += 1
                self._respond(401, {"ok": False, "reason": "hmac"})
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                self._respond(400, {"ok": False, "reason": "bad_json"})
                return
            self._respond(200, ledger.record(payload))

    return Handler


class ShadowServer:
    """仅 127.0.0.1 监听的影子接收端；显式启用才启动。"""

    def __init__(self, token: str, port: int = 19133, ledger: ShadowLedger | None = None) -> None:
        self.token = token
        self.port = port
        self.ledger = ledger or ShadowLedger()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        handler = build_handler(self.ledger, self.token)
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


def reserve(
    client_url: str, token: str, payload: dict[str, Any], timeout: float = 2.0
) -> dict[str, Any]:
    """P2 客户端登记调用（仅影子用途：结果不参与任务终态）。"""
    import urllib.request

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        client_url.rstrip("/") + "/reserve",
        data=body,
        headers={"Content-Type": "application/json", "X-Signature": sign(body, token)},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    import os
    import sys
    import time

    token = os.environ.get("CATOO_ATTRIBUTION_TOKEN", "")
    if not token:
        print(
            "[shadow] 缺少 CATOO_ATTRIBUTION_TOKEN，拒绝启动（不开放无认证通道）", file=sys.stderr
        )
        return 2
    port = int(os.environ.get("CATOO_ATTRIBUTION_PORT", "19133"))
    server = ShadowServer(token=token, port=port)
    url = server.start()
    print(f"[shadow] listening {url}（影子模式，不参与任务终态）")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
