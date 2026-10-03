"""Operations & observability routes (Task 18).

Runtime switches, the log page and the live WebSocket. First domain moved out
of the single-file server; the route-table snapshot keeps the served surface
identical.
"""

from __future__ import annotations

import asyncio
import json
import time

from aiohttp import web

from app.web.realtime import STATUS_INTERVAL
from app.web.routes.base import WebContext, esc, layout

#: Live feed for /logs (Task 17). Vanilla JS, no build step: connect to the
#: authenticated WebSocket, append narration lines, show the status snapshot,
#: reconnect with backoff when the socket drops (page navigation closes it).
_LIVE_FEED_JS = """
<div class="card"><h3>实时播报 <span class="muted" id="live-state">连接中…</span></h3>
<div class="muted" id="live-status">—</div>
<pre id="live-feed" style="max-height:320px;overflow:auto"></pre></div>
<script>
(function () {
  var feed = document.getElementById('live-feed');
  var state = document.getElementById('live-state');
  var status = document.getElementById('live-status');
  var attempts = 0;
  function append(line) {
    feed.textContent += line + "\\n";
    var lines = feed.textContent.split("\\n");
    if (lines.length > 120) { feed.textContent = lines.slice(-100).join("\\n") + "\\n"; }
    feed.scrollTop = feed.scrollHeight;
  }
  function connect() {
    var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    var ws = new WebSocket(proto + location.host + '/ws/events');
    ws.onopen = function () { attempts = 0; state.textContent = '已连接'; };
    ws.onmessage = function (event) {
      var msg = JSON.parse(event.data);
      if (msg.topic === 'narration') {
        append((msg.data.channel ? '[' + msg.data.channel + '] ' : '') + msg.data.message);
      } else if (msg.topic === 'status') {
        var d = msg.data;
        status.textContent = (d.online ? '在线' : '离线') + ' · ' + (d.sandbox || '沙盒未启用')
          + ' · AI 请求 ' + (d.metrics && d.metrics.ai_requests || 0)
          + ' · 失败 ' + (d.metrics && d.metrics.ai_errors || 0);
      }
    };
    ws.onclose = function () {
      attempts += 1;
      var wait = Math.min(30000, 1000 * Math.pow(2, attempts));
      state.textContent = '已断开，' + Math.round(wait / 1000) + 's 后重连…';
      setTimeout(connect, wait);
    };
  }
  connect();
})();
</script>
"""


class OpsRoutes(WebContext):
    """Handlers for /runtime, /logs and /ws/events."""

    async def _logs_page(self, request: web.Request) -> web.Response:
        level = request.query.get("level", "")
        keyword = request.query.get("q", "")
        lines = await self._admin.logs(level=level, keyword=keyword)
        options = "".join(
            f'<option value="{lv}" {"selected" if lv == level else ""}>{lv or "ALL"}</option>'
            for lv in ("", "INFO", "WARNING", "ERROR", "DEBUG")
        )
        body = f"""<div class="card"><form method="get" action="/logs">
<label>等级</label><select name="level">{options}</select>
<label>关键词</label><input name="q" value="{esc(keyword)}">
<p><button class="btn btn-primary">过滤</button></p></form></div>
<div class="card"><pre>{esc(chr(10).join(lines)) or "（无日志）"}</pre></div>{_LIVE_FEED_JS}"""
        return web.Response(
            text=layout("日志", "/logs", body, subtitle="最近的运行日志，含她的内心播报"),
            content_type="text/html",
        )

    # --------------------------------------------------------------- runtime

    async def _runtime_page(self, request: web.Request) -> web.Response:
        body = """<div class="card"><h3>Runtime 控制</h3>
<p>以下均为后台操作，QQ 端没有任何对应指令。</p>
<div class="grid">"""
        for action, label in (
            ("reload_persona", "重载人设"),
            ("restore_model_overrides", "重新应用模型覆盖"),
            ("reload_plugins", "重载插件"),
        ):
            body += f"""<form class="inline" method="post" action="/api/runtime/{action}">
<button class="btn btn-primary">{label}</button></form>"""
        body += "</div></div>"
        watchdog = getattr(self._bot, "watchdog", None)
        if watchdog is not None:
            stats = watchdog.stats()
            rows = "".join(
                f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>"
                for label, value in (
                    ("检测间隔", f"{stats['interval_seconds']:.1f} s"),
                    ("告警阈值", f"{stats['threshold_ms']} ms"),
                    ("测量次数", stats["ticks"]),
                    ("卡顿次数", stats["lag_events"]),
                    ("最近一次", f"{stats['last_lag_ms']:.0f} ms"),
                    ("最严重", f"{stats['max_lag_ms']:.0f} ms"),
                )
            )
            body += (
                '<div class="card"><h3>事件循环看门狗</h3>'
                '<p class="muted">单进程单循环：任何一次阻塞都会同时冻结 QQ 与 WebUI，'
                "超过阈值会打 WARNING（日志里能看到卡了多久）。</p>"
                f"<table>{rows}</table></div>"
            )
        return web.Response(
            text=layout("运行", "/runtime", body, subtitle="运行时开关、插件重载与健康检查"),
            content_type="text/html",
        )

    # ------------------------------------------------------- live websocket

    async def _ws_events(self, request: web.Request) -> web.WebSocketResponse:
        """Live narration + status for one logged-in browser (Task 17).

        The session cookie is checked by the auth middleware (401, never a
        redirect, because an upgrade cannot follow one). A subscriber that
        cannot keep up loses the oldest messages — publishing never blocks.
        """
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        queue = self._hub.subscribe()
        # A second task reads (and ignores) client frames: it is how a clean
        # close is noticed at all, since sending is the only other signal.
        reader = asyncio.create_task(self._ws_drain(ws))
        status_task = asyncio.create_task(self._ws_status_loop(ws))
        try:
            await ws.send_json(
                {"topic": "hello", "ts": int(time.time()), "data": self._hub.stats()}
            )
            while not ws.closed:
                getter = asyncio.create_task(queue.get())
                done, _pending = await asyncio.wait(
                    {getter, reader}, return_when=asyncio.FIRST_COMPLETED
                )
                if reader in done:  # the browser went away
                    getter.cancel()
                    break
                await ws.send_json(getter.result())
        except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
            pass  # the browser went away (or the server is shutting down)
        finally:
            for task in (reader, status_task):
                task.cancel()
            await asyncio.gather(reader, status_task, return_exceptions=True)
            self._hub.unsubscribe(queue)
        return ws

    @staticmethod
    async def _ws_drain(ws: web.WebSocketResponse) -> None:
        """Consume client frames until the peer closes (returning means closed)."""
        try:
            async for _message in ws:
                pass
        except Exception:  # noqa: BLE001 - a close is not an error
            return

    async def _ws_status_loop(self, ws: web.WebSocketResponse) -> None:
        """Push contract §8 topics additively (Task 17 kept intact).

        ``status`` every :data:`STATUS_INTERVAL` (unchanged), ``world`` only
        when the phase/location/action/revision snapshot changes, and
        ``scheduler`` every second tick (= 10s at the default 5s cadence).
        Unknown topics are ignored by the legacy page JavaScript.
        """
        tick = 0
        last_world = ""
        while not ws.closed:
            await asyncio.sleep(STATUS_INTERVAL)
            if ws.closed:
                return
            try:
                data = await self._admin.live_status()
                stamp = int(time.time())
                await ws.send_json({"topic": "status", "ts": stamp, "data": data})
                world = data.get("world") if isinstance(data, dict) else None
                if isinstance(world, dict) and world.get("phase") is not None:
                    signature = "|".join(
                        str(world.get(key))
                        for key in ("phase", "location", "action", "world_revision")
                    )
                    if signature != last_world:  # 变化才推（ConsoleWorld 语义）
                        last_world = signature
                        await ws.send_json({"topic": "world", "ts": stamp, "data": world})
                tick += 1
                if tick % 2:
                    continue  # scheduler 每 10s（默认 5s 心跳的每两拍）
                runtime = data.get("runtime") if isinstance(data, dict) else None
                scheduler = runtime.get("scheduler") if isinstance(runtime, dict) else None
                if scheduler is not None:
                    await ws.send_json({"topic": "scheduler", "ts": stamp, "data": scheduler})
            except (ConnectionResetError, RuntimeError):
                return

    async def _api_runtime_action(self, request: web.Request) -> web.Response:
        result = await self._admin.runtime_action(request.match_info["action"])
        return web.Response(
            text=layout(
                "运行",
                "/runtime",
                f'<div class="card">{esc(json.dumps(result, ensure_ascii=False))}</div>'
                '<meta http-equiv="refresh" content="2;url=/runtime">',
            ),
            content_type="text/html",
        )

    def register_ops(self, app: web.Application) -> None:
        app.router.add_get("/runtime", self._runtime_page)
        app.router.add_post("/api/runtime/{action}", self._api_runtime_action)
        app.router.add_get("/logs", self._logs_page)
        app.router.add_get("/ws/events", self._ws_events)
