# CatooBot Minecraft Phase 1 · 连接层实施记录

> Phase 目标（任务书）：罐头能通过 QQ / WebUI 接收 Minecraft Java 服务器地址，
> 使用配置好的账号加入服务器，并把连接状态、进世界、聊天、被踢、断开等基础事件
> 反馈给 CatooBot。**只有连接层**；感知/寻路/Agent 属于 Phase 2+，一律未实现。

---

## 1. 修改文件清单

**新增（Node.js Runtime，独立进程）**

| 文件 | 说明 |
|---|---|
| `minecraft_runtime/runtime.js` | Bridge runtime：单 bot 状态机 + Bridge HTTP API + 事件回调推送 |
| `minecraft_runtime/package.json` | 依赖：`mineflayer`；devDep：`flying-squid`（仅 E2E） |
| `minecraft_runtime/auth.example.json` | 认证配置模板（真实 `auth.json` 已 gitignore） |
| `minecraft_runtime/test/fake_server.js` | E2E 用 flying-squid 本地服务器 + 观察者玩家 |
| `minecraft_runtime/test/e2e.js` | Phase 1 验收自动化（Test 3-10 可机器执行子集） |

**新增（Python 侧）**

| 文件 | 说明 |
|---|---|
| `app/integrations/minecraft/__init__.py` | 包导出 |
| `app/integrations/minecraft/events.py` | 九种 Bridge 事件模型与校验 |
| `app/integrations/minecraft/runtime_client.py` | Bridge HTTP 客户端（aiohttp） |
| `app/integrations/minecraft/service.py` | `MinecraftService`：进程托管/状态镜像/事件分发/对账轮询 |
| `app/web/api/minecraft.py` | `/api/v1/minecraft*` 路由（契约 §7.5） |
| `plugins/minecraft/plugin.json` `plugin.py` | QQ 触发 join/leave + Minecraft 聊天入沙盒链 |
| `tests/test_minecraft_service.py` `test_web_api_minecraft.py` `test_minecraft_plugin.py` | 27 个 pytest 用例 |
| `docs/MINECRAFT_PHASE1.md` | 本文档 |

**修改**

| 文件 | 变更 |
|---|---|
| `app/config/settings.py` | 新增 `MinecraftConfig` + `AppConfig.minecraft` |
| `app/core/bot.py` | 装配：构造（try/except 降级）、`start()` 启动、`shutdown()` 回收 |
| `app/web/server.py` | `_auth_middleware` 为 `POST /api/v1/minecraft/events` 增加 Bearer token 豁免 |
| `app/web/api/__init__.py` | 注册 `MinecraftApiRoutes` 与 `_register_v1_minecraft` |
| `config/config.example.yaml` | 新增 `minecraft:` 配置段（含注释） |
| `.gitignore` | `minecraft_runtime/auth.json`、`minecraft_runtime/auth_cache/` |
| `tests/test_web_routes.py` | 路由快照 +4 条 |
| `docs/WEBUI_API_CONTRACT.md` | §1.2 错误码前缀 + §7.5 |
| `docs/WEBUI_V1_ROUTE_MATRIX.md` | SPA 路由 + 端点矩阵 |
| `webui/src/*`（api/types/page/icon/router/sidebar/spec） | Minecraft 页面与单测 |
| `webui/dist/*` | 重新构建（提交入库） |
| `webui/e2e/cutover.spec.ts` | 新增 Minecraft 页巡游段 |

## 2. 架构说明

```
CatooBot Core (Python)
  └─ MinecraftService                    app/integrations/minecraft/service.py
       ├─ 进程托管：spawn node runtime.js、健康等待、崩溃检测、有界重启（默认 3 次）
       ├─ Bridge 调用：join / leave / chat / status（错误翻译成稳定错误码）
       ├─ 事件通道：WebUI 回调端点（Bearer token）→ 校验 → 状态镜像 → 订阅者分发
       └─ 对账轮询：回调丢失/runtime 暴毙时兜底，状态永不卡死
        ↓ HTTP（127.0.0.1:25580，任务书 §Bridge API 的四个端点）
Minecraft Runtime（独立 Node.js 进程，永不因 bot 故障崩溃）
  └─ runtime.js：显式状态机 + mineflayer 单 bot 管理 + 事件回调推送（重试+有界队列）
        ↓ Mineflayer
Minecraft Server
```

- **不耦合**：Mineflayer 只存在于 Node 进程；Python 侧只见 HTTP + JSON 事件。
  Phase 2+ 的 Perception / Navigation / Agent 只需在 runtime 之上加端点，不动 CatooBot 核心。
- **状态机**（runtime 权威，Python 镜像）：
  `DISCONNECTED → CONNECTING → AUTHENTICATING → CONNECTED → SPAWNING → ONLINE`；
  任意活动态 → `DISCONNECTING → DISCONNECTED`；失败落 `ERROR`（可再次 connect）。
  不用布尔拼凑；连接看门狗保证限时未进世界必落 `ERROR`。
- **并发防护**：runtime 同时只允许一个 bot 会话（重复 connect → 409）；
  disconnect 幂等；事件推送互斥（防重发/漏发）；Python 侧事件去重（同键 5 分钟窗口内静默）。
- **崩溃隔离**：runtime `uncaughtException` 只记录并转事件，进程不退；
  Python 侧 Minecraft 任何故障均不影响 QQ 聊天主链路（bot.py try/except 降级）。

## 3. API / Event Schema

**CatooBot → Runtime（Node 进程，127.0.0.1，与任务书一致）**

| 端点 | 请求 | 响应 |
|---|---|---|
| `POST /minecraft/connect` | `{"host", "port"}` | `{"session_id", "status"}`；重复会话 409 |
| `GET /minecraft/status` | — | `{status, session_id, host, port, username, auth_mode, dimension, position{x,y,z}, health, last_error, ...}` |
| `POST /minecraft/chat` | `{"message"}` | `{"sent": true}`；不在线 400 `chat.not_online` |
| `POST /minecraft/disconnect` | `{}` | `{"status"}`（幂等） |
| `GET /minecraft/health` | — | `{"ok": true}`（进程存活探针） |

**Runtime → CatooBot 事件**（HTTP POST 回调 + Bearer token；`event` ∈ 九种，见
`app/integrations/minecraft/events.py`；`session_id` / `timestamp` 必带，其余上下文
如 `username` / `message` / `reason` / `error` 随事件而异）：

```
minecraft.connecting / connected / spawned / chat / player_joined /
player_left / kicked / disconnected / error
```

**CatooBot WebUI（`/api/v1`，契约 §7.5）**：`GET /minecraft`（读，恒 200）、
`POST /minecraft/join|leave`、`POST /minecraft/events`（Bridge 回调，免会话/免 CSRF，
Bearer token 门）。错误码：`minecraft.disabled / runtime_down / invalid_target /
session_active / not_connected / bad_event / chat_empty / chat_too_long`。

## 4. 启动方式

1. **依赖**：Python 侧无新依赖；Node.js ≥ 20（`node -v` 确认）。
2. **安装 runtime 依赖（一次性）**：`cd minecraft_runtime && npm install`
3. **配置**：`config/config.yaml`（或从 example 拷贝后）设置：
   ```yaml
   minecraft:
     enabled: true
   ```
   其余字段有默认值（runtime_port 25580 / 自动重启 3 次 / 连接看门狗 75s 等）。
4. **启动 CatooBot**：`python run.py`。日志出现 `Minecraft 桥已启动` 即 Bridge 就绪。
5. **使用**：WebUI 侧栏「Minecraft」页输入 host/port 加入；QQ 对罐头说
   「加入Minecraft 127.0.0.1:25565」。
6. **关闭**：`minecraft.enabled: false`（或 WebUI 页面提示未启用）——运行中的
   bot 会话随 `disconnect` / 停机回收，无僵尸进程。

## 5. Minecraft 登录配置方法

认证信息**只存在本地** `minecraft_runtime/auth.json`（已 gitignore；模板见
`auth.example.json`）。绝不写进 Git / config / .env，也**不通过 QQ / WebUI 收取**。

```jsonc
// minecraft_runtime/auth.json —— Offline 服务器
{ "mode": "offline", "username": "GuanTou" }
```
```jsonc
// Microsoft 正版账号（正版服务器）
{
  "mode": "microsoft",
  "email": "you@example.com",
  "password": "********"
}
```

- `mode: "microsoft"` 时凭据由 minecraft-protocol 处理，token 缓存在
  `minecraft_runtime/auth_cache/`（已 gitignore）；密码只在首次登录需要，
  之后靠缓存的 refresh token。
- offline 模式的用户名只能是 `[A-Za-z0-9_]` ≤16 字符（协议限制），中文「罐头」
  请用拼音（如 `GuanTou`）。
- 修改 auth.json 后重启 CatooBot（或重启 runtime 进程）生效。

## 6. 测试记录（2026-10-05，Windows 11 / Node v22.20 / Java 21）

| 门禁 | 结果 |
|---|---|
| `ruff check .` / `ruff format --check .` | 通过 |
| `mypy app`（256 文件） | 通过 |
| `pytest tests`（全量） | 通过（见本次运行输出，全绿） |
| `vue-tsc --noEmit` + vitest | 通过（Minecraft 页 5 用例） |
| `vite build` + dist 模块图校验 | 通过（dist 已重新提交） |
| Playwright 浏览器 E2E（真实后端） | 7/7 通过（含 Minecraft 页巡游） |
| `node minecraft_runtime/test/e2e.js` | **全部通过**（详见下） |

**Node E2E 覆盖的任务书验收项**（flying-squid 真协议本地服务器 + 观察者玩家）：

- Test 3 加入：connect 200，返回 session_id 与 CONNECTING ✓
- Test 4 服务器可见：flying-squid 玩家表出现 bot；观察者客户端在服务器内 ✓
- Test 5 状态：username / dimension(overworld) / position / health=20 ✓
- Test 6 收聊天：观察者发「罐头」→ `minecraft.chat`（username=Tester）✓
- Test 7 发聊天：Bridge chat API → 观察者客户端看到「我在这里！」✓
- Test 8 被踢：服务器踢出 → `minecraft.kicked` + 回到 DISCONNECTED ✓
- Test 9 主动离开：disconnect → 离开事件 + DISCONNECTED；重复 disconnect 幂等 ✓
- Test 10 重复性：完整流程 ×3，connected/spawned 事件计数精确=3，无残留会话；
  连死目标 → error 事件且 runtime 存活；SIGTERM 干净退出 ✓

（人工验收 Test 1/2 由真实服务器完成：WebUI 输入地址 → 罐头进服；Test 4 的
「Minecraft 客户端看到实体」在 flying-squid E2E 中以服务器玩家表 + 观察者客户端等价验证。）

## 7. 已知限制

1. **非原版聊天格式**：runtime 以原版 `<用户名> 消息` 模式解析聊天兜底；自定义
   聊天格式的服务器可能解析不出用户名（事件仍会上报，`username` 为空）。
2. **加入/离开系统行**被过滤，不算 chat 事件（player_joined/player_left 单独上报）。
3. **Microsoft 认证**未经真实正版服务器实测（本环境无正版账号）；流程走
   minecraft-protocol 内置 Microsoft auth，需要用户自行配置 auth.json 首登。
4. WebUI 未启用时事件回调不可用，只剩轮询兜底（事件可能丢失，状态不卡死）。
5. 同一句话 1 秒内的重复（chat+message 双事件去重）会被吞掉——极端刷屏场景。
6. E2E 的「另一客户端看到罐头」由 flying-squid 上的观察者玩家等价验证，未在
   真实 Minecraft 客户端做像素级验证。

## 8. Phase 2 建议

1. **World Perception**：runtime 增加 `GET /minecraft/world`（周围方块/实体扫描，
   prismarine-viewer / mineflayer 的 blockAt）与 `minecraft.world_snapshot` 事件。
2. **语义化**：Python 侧新增 World Semanticizer（把方块/实体快照变成叙述性世界模型），
   挂在现有沙盒世界旁，而不是替换它。
3. **动作工具**：Bridge 加 `move / dig / place / look` 端点 + 确认门，接入 ToolRuntime，
   让 Agent 以「工具」方式驱动罐头。
4. **导航**：mineflayer-navimap / Pathfinder 插件进 runtime，暴露 `goto` API。
5. **对话 Agent**：minecraft.chat 从「入沙盒链」升级为带世界上下文的回复决策
   （复用现有 conversation/sandbox 决策层，不在 runtime 里写业务）。
6. **可靠性**：runtime 重连策略（掉线自动重进）、多服务器配置档案、
   被踢/断线的 QQ 主动播报（需用户同意的额度控制）。
