"""MinecraftService：CatooBot 与 Minecraft Bridge runtime 之间的稳定通信层（Phase 1）。

职责（全部是连接层职责，不含任何 Minecraft AI）：

* 托管 runtime 进程：spawn ``node runtime.js``、健康等待、崩溃检测与有界重启；
* Bridge 调用：join / leave / chat / status（校验与错误翻译在这里，路由层只翻译）；
* 事件通道：接收 runtime 回调（WebUI 的 token 门）→ 更新状态镜像 → 分发给订阅者；
* 对账轮询：回调丢失或 runtime 暴毙时靠轮询兜底，状态永不卡死。

状态机镜像 runtime 的显式状态（DISCONNECTED/…/ERROR），不用布尔拼凑。
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config.settings import PROJECT_ROOT, MinecraftConfig
from app.integrations.minecraft.events import MinecraftBridgeEvent, parse_bridge_event
from app.integrations.minecraft.runtime_client import (
    MinecraftRuntimeClient,
    MinecraftRuntimeError,
)
from app.integrations.minecraft.world import TELEPORT_REBASE, WINDOW_SHIFT, WorldPerception
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.core.bot import Bot

log = get_logger("CatooBot.Minecraft")

Listener = Callable[[MinecraftBridgeEvent], Any]


class MinecraftBridgeError(Exception):
    """面向调用方（WebUI/QQ）的稳定错误；``code`` 对应契约 ``minecraft.*``。"""

    status = 400

    def __init__(self, message: str, *, code: str = "minecraft.error") -> None:
        super().__init__(message)
        self.code = code


class MinecraftDisabled(MinecraftBridgeError):
    status = 503

    def __init__(self, message: str = "Minecraft 连接层未启用") -> None:
        super().__init__(message, code="minecraft.disabled")


class MinecraftRuntimeDown(MinecraftBridgeError):
    status = 503

    def __init__(self, message: str) -> None:
        super().__init__(message, code="minecraft.runtime_down")


class MinecraftInvalidTarget(MinecraftBridgeError):
    status = 422

    def __init__(self, message: str = "服务器地址不合法") -> None:
        super().__init__(message, code="minecraft.invalid_target")


class MinecraftNotConnected(MinecraftBridgeError):
    status = 409

    def __init__(self, message: str = "罐头现在不在任何服务器里") -> None:
        super().__init__(message, code="minecraft.not_connected")


class MinecraftBusy(MinecraftBridgeError):
    status = 409

    def __init__(self, message: str = "已经有 bot 会话在运行") -> None:
        super().__init__(message, code="minecraft.session_active")


#: runtime 业务错误码 → 稳定错误
def _translate(exc: MinecraftRuntimeError) -> MinecraftBridgeError:
    if exc.unreachable:
        return MinecraftRuntimeDown(str(exc))
    if exc.status == 409:
        return MinecraftBusy(str(exc))
    if exc.code in {"target.invalid", "chat.empty", "chat.too_long"}:
        return MinecraftInvalidTarget(str(exc))
    if exc.code == "chat.not_online":
        return MinecraftNotConnected(str(exc))
    return MinecraftBridgeError(str(exc), code=f"minecraft.{exc.code}")


class _RuntimeProcess:
    """一个被托管的 ``node runtime.js`` 子进程（含日志泵）。"""

    def __init__(self, runtime_dir: Path, node: str, env: dict[str, str]) -> None:
        self._runtime_dir = runtime_dir
        self._node = node
        self._env = env
        self._process: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task[None] | None = None
        self.log_tail: deque[str] = deque(maxlen=200)

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        if self.running:
            return
        self._process = await asyncio.create_subprocess_exec(
            self._node,
            "runtime.js",
            cwd=str(self._runtime_dir),
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._pump = asyncio.create_task(self._pump_logs())

    async def _pump_logs(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                self.log_tail.append(text)
                level, message = _parse_runtime_log(text)
                log.log(level, "[runtime] %s", message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 日志泵不能杀死服务
            log.exception("[Minecraft] runtime 日志泵异常")

    async def stop(self, timeout: float = 5.0) -> None:
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=timeout)
            except TimeoutError:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
                await self._process.wait()
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except asyncio.CancelledError:
                pass
            self._pump = None
        self._process = None


def _parse_runtime_log(line: str) -> tuple[int, str]:
    """runtime 输出 JSON 行；解析失败按原样 INFO 输出。"""
    import json

    try:
        payload = json.loads(line)
        if isinstance(payload, dict):
            level = {"debug": 10, "info": 20, "warn": 30, "error": 40}.get(
                str(payload.get("level")), 20
            )
            return level, str(payload.get("message") or line)
    except ValueError:
        pass
    return 20, line


class MinecraftService:
    """由 ``Bot`` 装配的长驻服务；WebUI 与 QQ 插件都经由它操作 Minecraft。"""

    def __init__(
        self,
        bot: Bot,
        config: MinecraftConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.config = config
        self._clock = clock
        self._client = MinecraftRuntimeClient(
            f"http://127.0.0.1:{config.runtime_port}", timeout=config.request_timeout_seconds
        )
        self._process: _RuntimeProcess | None = None
        self._listeners: list[Listener] = []
        self._poll_task: asyncio.Task[None] | None = None
        self._perception_task: asyncio.Task[None] | None = None
        self.perception: WorldPerception | None = None
        self._started = False
        self._restart_count = 0
        self._runtime_down = False
        # 状态镜像：来自事件回调 + 对账轮询，绝不自行发明状态
        self._mirror: dict[str, Any] = {"status": "DISCONNECTED"}
        self._last_event: dict[str, Any] | None = None
        self._recent_event_keys: deque[str] = deque(maxlen=256)
        # 回调共享密钥：auto_start 时随进程环境注入；外部托管时用配置值
        self.callback_token: str | None = config.external_callback_token or None
        self.callback_url: str = config.external_callback_url

    # --------------------------------------------------------------- listeners

    def add_listener(self, listener: Listener) -> None:
        """订阅 Bridge 事件（QQ 插件由此把 chat 送进消息处理链）。"""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    # -------------------------------------------------------------- lifecycle

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def runtime_managed(self) -> bool:
        return self._process is not None

    @property
    def auth_configured(self) -> bool:
        """本地 auth.json 是否存在（只看存在性，绝不读内容）。"""
        return (self._runtime_dir() / "auth.json").exists()

    def _runtime_dir(self) -> Path:
        """runtime 目录；相对路径以项目根为基准（与 bible_path 等约定一致）。

        必须解析成绝对路径再传给子进程：Node 进程的 CWD 就在 runtime 目录里，
        相对路径会在它那边被二次拼接（auth.json 曾因此永远读不到，
        静默回退成默认名字）。
        """
        path = Path(self.config.runtime_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    async def start(self) -> None:
        """Bot.start 时调用：拉起 runtime（可选）+ 对账轮询。失败会清理自身。"""
        if not self.config.enabled or self._started:
            return
        if not self.callback_url:
            self.callback_url = self._default_callback_url()
        try:
            if self.config.auto_start_runtime:
                await self._start_runtime()
            else:
                log.info("[Minecraft] runtime 由外部托管（auto_start_runtime=false）")
            if self.config.perception_enabled:
                self.perception = WorldPerception(
                    client=self._client,
                    clock=self._clock,
                    near_interval=self.config.near_interval_seconds,
                    local_interval=self.config.local_interval_seconds,
                    extended_interval=self.config.extended_interval_seconds,
                    event_cooldown=self.config.world_event_cooldown_seconds,
                    change_block_threshold=self.config.world_change_block_threshold,
                    dispatch=self._dispatch_world_event,
                )
                self._perception_task = asyncio.create_task(self._perception_loop())
            self._started = True
            self._poll_task = asyncio.create_task(self._poll_loop())
            log.info(
                "[Minecraft] bridge ready (runtime_port=%d, callback=%s, auth=%s, perception=%s)",
                self.config.runtime_port,
                "configured" if self.callback_url else "disabled",
                "auth.json" if self.auth_configured else "offline-default",
                f"on (near={self.config.near_interval_seconds}s)" if self.perception else "off",
            )
        except Exception:
            await self._cleanup()
            raise

    async def stop(self) -> None:
        """Bot.shutdown 时调用：断开 bot（尽力）、停轮询、回收 runtime 进程。"""
        await self._cleanup()

    async def _cleanup(self) -> None:
        self._started = False
        if self._perception_task is not None:
            self._perception_task.cancel()
            try:
                await self._perception_task
            except asyncio.CancelledError:
                pass
            self._perception_task = None
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.disconnect(), timeout=3.0)
            except Exception:  # noqa: BLE001 - 停机路径尽力而为
                pass
            await self._client.close()
        if self._process is not None:
            await self._process.stop()
            self._process = None

    def _default_callback_url(self) -> str:
        web = self.bot.config.web
        if not bool(getattr(web, "enabled", False)):
            log.warning(
                "[Minecraft] WebUI 未启用，Bridge 事件回调不可用（仅剩状态轮询兜底）；"
                "建议设置 web.enabled: true"
            )
            return ""
        return f"http://127.0.0.1:{web.port}/api/v1/minecraft/events"

    # ------------------------------------------------------------ runtime 进程

    def _runtime_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["MC_RUNTIME_PORT"] = str(self.config.runtime_port)
        env["MC_CONNECT_TIMEOUT"] = str(self.config.connect_timeout_seconds)
        env["MC_AUTH_FILE"] = str(self._runtime_dir() / "auth.json")
        if self.callback_url:
            env["MC_CALLBACK_URL"] = self.callback_url
            env["MC_CALLBACK_TOKEN"] = self.callback_token or ""
        return env

    async def _start_runtime(self) -> None:
        if self._process is not None:
            await self._process.stop()
            self._process = None
        runtime_dir = self._runtime_dir()
        if not (runtime_dir / "runtime.js").exists():
            target = runtime_dir / "runtime.js"
            raise MinecraftRuntimeDown(
                f"找不到 Minecraft runtime：{target}（检查 minecraft.runtime_dir）"
            )
        if not self.callback_token:
            self.callback_token = secrets.token_urlsafe(24)
        process = _RuntimeProcess(runtime_dir, self.config.node_executable, self._runtime_env())
        try:
            await process.start()
        except FileNotFoundError as exc:
            raise MinecraftRuntimeDown(
                f"无法启动 node（{self.config.node_executable}）：请确认已安装 Node.js ≥ 20"
            ) from exc
        self._process = process
        deadline = self._clock() + self.config.startup_timeout_seconds
        while self._clock() < deadline:
            if not process.running:
                tail = "\n".join(list(process.log_tail)[-8:])
                raise MinecraftRuntimeDown(
                    f"Minecraft runtime 进程启动后立即退出（见日志）\n{tail}"
                )
            try:
                await self._client.health()
                self._runtime_down = False
                log.info("[Minecraft] runtime 进程就绪 (pid=%s)", process.pid)
                return
            except MinecraftRuntimeError:
                await asyncio.sleep(0.3)
        raise MinecraftRuntimeDown(
            f"Minecraft runtime 在 {self.config.startup_timeout_seconds}s 内未就绪"
        )

    async def _ensure_runtime(self) -> None:
        try:
            await self._client.health()
            self._runtime_down = False
            return
        except MinecraftRuntimeError:
            pass
        if not self.config.auto_start_runtime:
            raise MinecraftRuntimeDown("Minecraft runtime 未运行（auto_start_runtime=false）")
        if self._restart_count >= self.config.max_runtime_restarts:
            restarts = self.config.max_runtime_restarts
            raise MinecraftRuntimeDown(
                f"runtime 重启次数用尽（{restarts}），请检查日志后重启 CatooBot"
            )
        self._restart_count += 1
        log.warning("[Minecraft] runtime 不可达，尝试重启（第 %d 次）", self._restart_count)
        await self._start_runtime()

    # -------------------------------------------------------------- bridge API

    async def join(self, host: str, port: int) -> dict[str, Any]:
        """加入服务器。返回 ``{"session_id", "status"}``；失败抛稳定错误。"""
        self._require_enabled()
        clean_host = str(host or "").strip()
        if not clean_host or len(clean_host) > 253:
            raise MinecraftInvalidTarget("服务器地址不能为空")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise MinecraftInvalidTarget("端口必须是 1-65535 的整数")
        await self._ensure_runtime()
        try:
            result = await self._client.connect(clean_host, int(port))
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc
        self._mirror = {
            "status": str(result.get("status") or "CONNECTING"),
            "session_id": result.get("session_id"),
            "host": clean_host,
            "port": int(port),
        }
        return result

    async def leave(self) -> dict[str, Any]:
        """主动离开。幂等：不在任何服务器（或 runtime 已死）时也算成功。"""
        self._require_enabled()
        try:
            await self._ensure_runtime()
        except MinecraftRuntimeDown:
            # runtime 进程都没了 → bot 必然不在线；按幂等语义落回 DISCONNECTED
            self._mirror = {"status": "DISCONNECTED"}
            return {"ok": True, "status": "DISCONNECTED", "note": "runtime 不可达，视为已断开"}
        try:
            return await self._client.disconnect()
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
                self._mirror = {"status": "DISCONNECTED"}
                return {"ok": True, "status": "DISCONNECTED", "note": "runtime 不可达，视为已断开"}
            raise _translate(exc) from exc

    async def send_chat(self, message: str) -> dict[str, Any]:
        """在服务器里发言。只做校验与翻译，不判断语义。"""
        self._require_enabled()
        text = str(message or "")
        if not text.strip():
            raise MinecraftBridgeError("消息不能为空", code="minecraft.chat_empty")
        if len(text) > 256:
            raise MinecraftBridgeError("消息超过 256 字符上限", code="minecraft.chat_too_long")
        try:
            await self._ensure_runtime()
            return await self._client.chat(text)
        except MinecraftRuntimeError as exc:
            if exc.unreachable:
                self._mark_runtime_down(str(exc))
            raise _translate(exc) from exc

    async def status(self) -> dict[str, Any]:
        """合并 runtime 实况与本地镜像；runtime 不可达时返回降级快照。"""
        try:
            live = await self._client.status()
            self._runtime_down = False
            self._mirror = {key: value for key, value in live.items() if key != "ok"}
        except MinecraftRuntimeError:
            pass  # 保留镜像，快照里以 runtime_alive=False 呈现
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        """同步只读投影（WebUI/QQ 兜底），不发网络请求。"""
        mirror = dict(self._mirror)
        return {
            "enabled": self.enabled,
            "auth_configured": self.auth_configured,
            "runtime": {
                "running": self._process.running if self._process else False,
                "pid": self._process.pid if self._process else None,
                "managed": self.config.auto_start_runtime,
                "restarts": self._restart_count,
                "down": self._runtime_down,
                "log_tail": list(self._process.log_tail)[-5:] if self._process else [],
            },
            "connection": {
                "status": mirror.get("status", "DISCONNECTED"),
                "session_id": mirror.get("session_id"),
                "host": mirror.get("host"),
                "port": mirror.get("port"),
                "username": mirror.get("username"),
                "auth_mode": mirror.get("auth_mode"),
                "dimension": mirror.get("dimension"),
                "position": mirror.get("position"),
                "health": mirror.get("health"),
                "last_error": mirror.get("last_error"),
                "kicked_reason": mirror.get("kicked_reason"),
                "connected_at": mirror.get("connected_at"),
            },
            "last_event": self._last_event,
        }

    # ----------------------------------------------------------- 事件接收/分发

    async def receive_event(self, raw: dict[str, Any]) -> MinecraftBridgeEvent:
        """WebUI 回调端点的落点：校验 → 镜像 → 分发。未知事件名抛 ValueError。"""
        event = parse_bridge_event(raw)
        key = f"{event.type_name}:{event.session_id}:{event.timestamp}"
        if key in self._recent_event_keys:
            return event  # runtime 的重试可能造成重复，静默去重
        self._recent_event_keys.append(key)
        self._last_event = raw
        self._apply_to_mirror(event)
        context = "".join(
            f" {key}={event.data[key]!r}"
            for key in ("username", "message", "reason", "error")
            if key in event.data
        )
        log.info(
            "[Minecraft] %s (session=%s%s)",
            event.type_name,
            event.session_id or "-",
            context,
        )
        metrics = getattr(self.bot, "metrics", None)
        if metrics is not None:
            metrics.inc(f"minecraft_{event.type_name.split('.', 1)[1]}_events")
        if event.type_name == "minecraft.disconnected" and self.perception is not None:
            # 离开世界：感知缓存整体作废（不留假在线状态）
            self.perception.invalidate()
        await self._notify_listeners(event)
        return event

    async def _notify_listeners(self, event: Any) -> None:
        """把事件分发给所有订阅者；单个订阅者故障不拖垮事件通道。"""
        for listener in list(self._listeners):
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 - 订阅者故障不拖垮事件通道
                log.exception("[Minecraft] 事件订阅者处理 %s 失败", event.type_name)

    def _apply_to_mirror(self, event: MinecraftBridgeEvent) -> None:
        name = event.type_name
        if name == "minecraft.connecting":
            self._mirror["status"] = "CONNECTING"
        elif name == "minecraft.connected":
            self._mirror["status"] = "CONNECTED"
            self._mirror["username"] = event.data.get("username")
        elif name == "minecraft.spawned":
            self._mirror["status"] = "ONLINE"
            self._mirror["username"] = event.data.get("username", self._mirror.get("username"))
            self._mirror["connected_at"] = event.timestamp
        elif name == "minecraft.kicked":
            self._mirror["status"] = "DISCONNECTING"
            self._mirror["kicked_reason"] = event.data.get("reason")
        elif name == "minecraft.disconnected":
            self._mirror["status"] = "DISCONNECTED"
            self._mirror["session_id"] = None
            self._mirror["dimension"] = None
            self._mirror["position"] = None
            self._mirror["health"] = None
        elif name == "minecraft.error":
            self._mirror["last_error"] = event.data.get("error")

    # ----------------------------------------------------------------- 对账轮询

    def _mark_runtime_down(self, reason: str) -> None:
        if self._runtime_down:
            return
        self._runtime_down = True
        log.warning("[Minecraft] runtime 不可达：%s", reason)
        asyncio.create_task(
            self._dispatch_synthetic(
                "minecraft.error", {"error": f"Bridge runtime 不可达：{reason}"}
            )
        )

    async def _dispatch_synthetic(self, event_name: str, data: dict[str, Any]) -> None:
        try:
            await self.receive_event(
                {
                    "event": event_name,
                    "session_id": self._mirror.get("session_id"),
                    "timestamp": self._clock(),
                    **data,
                }
            )
        except Exception:  # noqa: BLE001
            log.exception("[Minecraft] 合成事件分发失败")

    async def _poll_loop(self) -> None:
        failures = 0
        while True:
            await asyncio.sleep(self.config.poll_interval_seconds)
            if not self._started:
                return
            try:
                live = await self._client.status()
            except Exception:  # noqa: BLE001 - 轮询永不抛出
                failures += 1
                if failures == 1:
                    self._mark_runtime_down("状态轮询连续失败")
                if (
                    failures >= 2
                    and self.config.auto_start_runtime
                    and self._restart_count < self.config.max_runtime_restarts
                ):
                    try:
                        await self._start_runtime()
                        failures = 0
                        await self._dispatch_synthetic(
                            "minecraft.disconnected", {"reason": "runtime 进程已重启"}
                        )
                    except Exception:  # noqa: BLE001
                        log.warning("[Minecraft] runtime 重启失败，稍后重试")
                continue
            failures = 0
            previous = self._mirror.get("status")
            self._mirror = {key: value for key, value in live.items() if key != "ok"}
            current = str(live.get("status") or "DISCONNECTED")
            if previous != current:
                log.info("[Minecraft] 状态对账：%s → %s", previous, current)

    # ------------------------------------------------------------- 世界感知(P2)

    async def _perception_loop(self) -> None:
        """按层节拍轮询 Raw World Snapshot；差异事件经去抖后分发。"""
        assert self.perception is not None
        while True:
            await asyncio.sleep(self.perception.next_due_in())
            if not self._started:
                return
            if self._mirror.get("status") != "ONLINE":
                # 不在世界里：感知无事可做，拉长节拍待命（进世界后 ≤1s 内开扫）
                self.perception.defer(1.0)
                continue
            try:
                due = self.perception.due_layers()
                if not due:
                    continue
                events = await self.perception.poll(due)
                for name, data in events:
                    await self._dispatch_world_event(name, data)
                # 窗口位移/大跨度重定位只做内部记录（Phase 3A §七：不进 LLM 上下文）
                diff = self.perception.last_diff
                if diff is not None and diff.kind in (WINDOW_SHIFT, TELEPORT_REBASE):
                    log.info(
                        "[Minecraft] 观察窗口%s（shift=%s，位移柱 %d，重叠 %d）——不计为世界变化",
                        "重定位" if diff.kind == TELEPORT_REBASE else "位移",
                        diff.shift,
                        diff.shifted_blocks,
                        diff.overlap_blocks,
                    )
            except asyncio.CancelledError:
                raise
            except MinecraftRuntimeError:
                # runtime 不可达由状态对账轮询统一处理，这里安静等下一拍
                await asyncio.sleep(1.0)
            except Exception:  # noqa: BLE001 - 感知故障绝不拖垮连接层
                log.exception("[Minecraft] 世界感知轮询失败")
                await asyncio.sleep(1.0)

    async def _dispatch_world_event(self, name: str, data: dict[str, Any]) -> None:
        from app.integrations.minecraft.events import parse_world_event

        try:
            event = parse_world_event(name, data, self._clock())
        except ValueError:
            log.warning("[Minecraft] 忽略未知感知事件：%s", name)
            return
        log.info("[Minecraft] %s %s", event.type_name, data)
        metrics = getattr(self.bot, "metrics", None)
        if metrics is not None:
            metrics.inc(
                f"minecraft_{event.type_name.removeprefix('minecraft.').replace('.', '_')}_events"
            )
        await self._notify_listeners(event)

    def world_view(self) -> dict[str, Any]:
        """API / 只读工具的世界视图（语义模型 + 缓存元信息 + raw）。"""
        if self.perception is None:
            return {
                "available": False,
                "online": False,
                "reason": (
                    "world perception disabled" if self.config.enabled else "minecraft disabled"
                ),
            }
        return self.perception.view()

    # ------------------------------------------------------------------- misc

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise MinecraftDisabled()
