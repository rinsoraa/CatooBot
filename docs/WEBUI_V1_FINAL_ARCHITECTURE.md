# CatooBot WebUI v1.0 · 最终架构（Final Architecture）

> W6 收口后的架构说明。原则：**WebUI 是管理层，Core 是唯一真相**；
> 前端只做展示 / 编辑 / 诊断 / 受控控制，绝不计算业务事实。

---

## 1. 端到端分层

```text
Browser
  │  （会话 Cookie：catoobot_session；写操作带 X-CSRF-Token）
  ▼
SPA（Vue 3 + TS + Vite，产物 webui/dist，由 aiohttp 直接托管）
  │  api/client.ts —— 唯一的 HTTP 出口（信封解析 / CSRF 注入 / 401 反应）
  │  stores/realtime.ts —— 唯一的 WebSocket 所有者（hello/status/world/scheduler/log/narration）
  ▼
/api/v1（JSON，统一信封 {ok,data,meta} / {ok:false,error,meta}）
  │  会话 · CSRF · 配置注册表 · 凭据（masked）· AI · 角色/世界 · 社交 · 记忆 · 能力 · Agent · 运行
  ▼
Admin / Domain Services（app/web/services/**）
  │  只读投影（read_model / world_read / social_read / runtime_snapshot / config_registry）
  │  写入通道（ConfigAdminService / AIAdminService / ToolAdminService / …）
  ▼
CatooBot Core
   Sandbox · RuntimeScheduler · Goal/Action/Need · Relationship/Commitment
   Memory · Conversation · OneBot Gateway · AI Router · EventBus
```

## 2. 双向边界

| 允许 | 禁止 |
|---|---|
| 前端调用 `/api/v1`（含确认门） | 前端直连数据库 / 私有运行时字段 |
| 服务层读写真实配置与 Core 状态 | 前端计算 AI 状态、冷却、故障转移 |
| 只读投影把 Core 状态整理成人话 | 第二套 Config/Credential/AI Router/Scheduler/EventBus |
| 最小只读访问器（两处：`social_session_snapshot()`、`OneBotGateway.stats()`） | 为 WebUI 新增业务系统 |
| `localStorage` 存 UI 偏好（主题/侧栏） | 用 `localStorage` 存真实配置 |

## 3. 会话与安全

- **会话**：单管理员，PBKDF2 口令存 DB，`catoobot_session`（httpOnly、SameSite=Lax、12h、内存态）。v1 与 v0.8 共用同一会话，互不污染（同一中间件、同一校验）。
- **CSRF**：写操作（POST/PUT/PATCH/DELETE）必须带 token；豁免仅 `POST /login`（旧表单）与 `POST /api/v1/session`（登录本身）。`GET /api/v1/csrf` 与登录响应都会引导 token。
- **静态资源**：`/assets/*`、`/favicon.ico` 免会话（匿名登录页必须能加载自己的 JS）；其余页面、`/api/v1`、`/ws` 一律要求会话。
- **确认门（双保险）**：前端 ConfirmDialog + 后端 `409 *_confirm_required`（memory/world/ai/config/media/tools）。
- **凭据**：只以 masked 形态出现在任何响应/日志/前端；写完只回 masked；`.env` 原子写入且保留既有行。
- **Character Bible**：真实档案从不进入 API、前端 bundle 或日志；角色页只展示运行时投影。

## 4. 实时（Realtime）

- 一个 WS 端点 `/ws/events`，一个前端所有者；服务端 Hub 有界（丢最旧）。
- 主题：`hello`（连接即发）、`status`（5s）、`world`/`scheduler`（变化驱动）、`log`/`narration`（日志流）。
- **WS 只是通知与刷新触发器**：权威快照永远来自 REST；断线保留最后已知状态并标记「可能不是最新」。

## 5. 配置（Config）

- 注册表由 `AppConfig` 的 pydantic schema 推导（272 键），带区域/级别/来源/生效状态/敏感性。
- 读取：`/config/schema`（渲染元数据）+ `/config/effective`（真实值 + 来源层 + 需重启标记）。
- 写入：`PATCH /config`（校验 → 原子写 overrides → 热应用 → 逐键结果与 warnings）；重启类字段**绝不假装热更新**；`/config/raw` 是专家兜底（确认后替换）。

## 6. AI

- 配置真相在 `AppConfig.ai`（providers/models），凭据在环境变量（masked 呈现）。
- 路由/重试/冷却/429/5xx/故障转移全部在 `ModelRouter`；WebUI 只配置顺序与启停、展示真实状态（含 `cooldown_remaining_seconds`）。
- 健康状态由后端推导（`ready/degraded/unavailable/not_configured`），前端不猜。

## 7. 领域只读投影

`read_model`（overview/runtime/character/world/logs）、`world_read`（needs_full/action 细节/interrupted/spaces/objects/pet/action_defs/modes_defs/goals_full）、`social_read`（users/relationship/commitments/sessions/spaces）、`runtime_snapshot`（process/onebot/lanes/db）。缺数据一律 `null`，由 UI 显示 `—`/空态，不伪造。

## 8. Legacy 与切换

- `web.version = "v1"`（默认）：`/`、`/login`、`/character`、`/social`、`/memory`、`/memory/health`、`/memory/timeline` 交给 SPA；其余旧路径原样保留，`/legacy*` 始终可直达旧控制台。
- `web.version = "v0.8"`：上述路径交还旧 UI；**`/api/v1` 与 `/ws` 不受影响**（可随时切回 v1 而不重启业务）。
- 回退不需要自动化：一个配置项即可，两种模式都有测试覆盖。

## 9. 构建与交付

- `webui/dist` 随仓库发布（运行时不需要 Node）；`node_modules` 永不入库。
- CI 两个 job：Python（ruff/format/mypy/pytest）与 WebUI（npm ci → typecheck → vitest → build → dist 结构校验）；浏览器 E2E 作为 WebUI job 的最后一环（Chromium + 真实 Bot）。
- 版本语义统一为 **WebUI v1.0**；v0.8 只作为 legacy/rollback 名称。
