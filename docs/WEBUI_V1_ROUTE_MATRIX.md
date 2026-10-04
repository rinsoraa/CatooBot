# CatooBot WebUI v1.0 · 路由矩阵（Route Matrix）

> 最终状态（W6 cutover 后）。Auth = 会话 Cookie 要求；CSRF = 写操作要求 `X-CSRF-Token`；
> Lazy = 前端按需加载（Vite chunk）；WS = 该页面消费的实时主题。
> 数据来源：`app/web/server.py` 的真实路由表（210 条）与 `webui/src/router/index.ts`（45 条）。

---

## 1. v1 SPA 路由（`web.version = "v1"`，默认）

| Route | Name | Auth | Lazy | Page title | Primary API | WS | Status |
|---|---|---|---|---|---|---|---|
| `/` | dashboard | 是 | 是 | 总览 | `/api/v1/overview`, `/runtime`, `/world` | status, world | ACTIVE |
| `/login` | login | 否（guest） | 是 | 登录 | `/api/v1/session`（POST） | — | ACTIVE |
| `/character` | character | 是 | 是 | 角色 | `/api/v1/character`, `/character/state` | status | ACTIVE |
| `/character/world` | character-world | 是 | 是 | 角色 · 世界 | `/api/v1/world`, `/world/control/*` | world | ACTIVE |
| `/character/world/timeline` | character-world-timeline | 是 | 是 | 角色 · 世界时间线 | `/api/v1/world/timeline`, `/world/topics` | world | ACTIVE |
| `/ai` | ai-overview | 是 | 是 | AI 与模型 | `/api/v1/ai/status` | status | ACTIVE |
| `/ai/providers` | ai-providers | 是 | 是 | AI · 服务商 | `/api/v1/ai/providers*`, `/credentials/test` | status | ACTIVE |
| `/ai/models` | ai-models | 是 | 是 | AI · 模型 | `/api/v1/ai/models*` | status | ACTIVE |
| `/ai/roles` | ai-roles | 是 | 是 | AI · 模型用途 | `/api/v1/ai/roles*` | — | ACTIVE |
| `/ai/failover` | ai-failover | 是 | 是 | AI · 故障转移 | `/api/v1/ai/models/order`, `/ai/router/reset` | status | ACTIVE |
| `/ai/usage` | ai-usage | 是 | 是 | AI · 用量 | `/api/v1/ai/usage` | — | ACTIVE |
| `/ai/test` | ai-test | 是 | 是 | AI · 测试台 | `/api/v1/ai/models/{name}/test` | — | ACTIVE |
| `/ai/setup` | ai-setup | 是 | 是 | AI · 配置向导 | 复用上述 AI 端点 | status | ACTIVE |
| `/social` | social-users | 是 | 是 | 社交 · 用户 | `/api/v1/social/users` | — | ACTIVE |
| `/social/users/:personId` | social-user | 是 | 是 | 社交 · 人物 | `/api/v1/social/users/{id}` | — | ACTIVE |
| `/social/groups` | social-groups | 是 | 是 | 社交 · 群组 | `/api/v1/social/groups` | — | ACTIVE |
| `/social/relationships` | social-relationships | 是 | 是 | 社交 · 关系 | `/api/v1/social/relationships` | — | ACTIVE |
| `/social/commitments` | social-commitments | 是 | 是 | 社交 · 承诺 | `/api/v1/social/commitments` | — | ACTIVE |
| `/social/commitments/:commitmentId` | social-commitment | 是 | 是 | 社交 · 承诺详情 | `/api/v1/social/commitments/{id}` | — | ACTIVE |
| `/social/sessions` | social-sessions | 是 | 是 | 社交 · 会话 | `/api/v1/social/sessions` | status | ACTIVE |
| `/memory` | memory | 是 | 是 | 记忆 | `/api/v1/memories` | — | ACTIVE |
| `/memory/timeline` | memory-timeline | 是 | 是 | 记忆 · 时间线 | `/api/v1/memories/timeline` | — | ACTIVE |
| `/memory/health` | memory-health | 是 | 是 | 记忆 · 健康度 | `/api/v1/memories/health` | — | ACTIVE |
| `/memory/:memoryId(\d+)` | memory-detail | 是 | 是 | 记忆 · 详情 | `/api/v1/memories/{id}` | — | ACTIVE |
| `/abilities` | —（重定向） | 是 | — | — | — | — | ACTIVE |
| `/abilities/tools` | abilities-tools | 是 | 是 | 能力 · 工具 | `/api/v1/tools*` | — | ACTIVE |
| `/abilities/tools/:name` | abilities-tool | 是 | 是 | 能力 · 工具详情 | `/api/v1/tools/{name}*` | — | ACTIVE |
| `/abilities/media` | abilities-media | 是 | 是 | 能力 · 媒体 | `/api/v1/stickers*`, `/expressions*` | — | ACTIVE |
| `/abilities/agent` | abilities-agent | 是 | 是 | 能力 · Agent | `/api/v1/agent*` | — | ACTIVE |
| `/abilities/agent/tasks/:taskId` | abilities-agent-task | 是 | 是 | 能力 · Agent 任务 | `/api/v1/agent/tasks/{id}*` | — | ACTIVE |
| `/system` | —（重定向） | 是 | — | — | — | — | ACTIVE |
| `/system/settings` | system-settings | 是 | 是 | 系统 · 设置 | `/api/v1/config/*` | — | ACTIVE |
| `/system/settings/restart-pending` | system-restart-pending | 是 | 是 | 系统 · 等待重启 | `/api/v1/config/restart-pending` | — | ACTIVE |
| `/system/settings/advanced` | system-advanced | 是 | 是 | 系统 · 高级（YAML） | `/api/v1/config/raw`, `/validate` | — | ACTIVE |
| `/system/credentials` | system-credentials | 是 | 是 | 系统 · 凭据 | `/api/v1/credentials*` | — | ACTIVE |
| `/system/logs` | system-logs | 是 | 是 | 系统 · 日志 | `/api/v1/logs/tail`, `/logs/channels` | log, narration, status | ACTIVE |
| `/system/runtime` | system-runtime | 是 | 是 | 系统 · Runtime | `/api/v1/runtime*`, `/overview` | status, scheduler | ACTIVE |
| `/minecraft` | minecraft | 是 | 是 | Minecraft | `/api/v1/minecraft`, `/minecraft/world`, `/minecraft/join`, `/minecraft/leave` | — | ACTIVE |
| `/:pathMatch(.*)*` | not-found | 是 | 是 | 未找到 | — | — | ACTIVE |

守卫：`meta.requiresAuth` 41 条 + guest 1 条 + 重定向 2 条 + catch-all 1 条 = 45 条定义。未登录访问任一 `requiresAuth` 路由 → `/login?redirect=<fullPath>`；登录后回跳原路径。

---

## 2. 分流点（同一 URL，两种 UI）

| Route | v1 行为 | v0.8 行为（`web.version=v0.8`） | Auth | 旧页面别名 |
|---|---|---|---|---|
| `GET /` | SPA 总览 | 旧 dashboard | 是 | `/legacy` |
| `GET /login` | SPA 登录 | 旧登录表单页 | 否 | —（`POST /login` 两者共用） |
| `GET /character` | SPA 角色 | 旧角色页 | 是 | `/legacy/character`（GET+POST） |
| `GET /social` | SPA 社交 | 旧社交面板 | 是 | `/legacy/social` |
| `GET /memory` | SPA 记忆 | 旧记忆浏览 | 是 | `/legacy/memory` |
| `GET /memory/health` | SPA 健康度 | 旧健康页 | 是 | `/legacy/memory/health` |
| `GET /memory/timeline` | SPA 时间线 | 旧时间线页 | 是 | `/legacy/memory/timeline` |

分流实现：`SpaRoutes.register_page_dispatch()` 在旧模块之前注册（aiohttp 先注册者优先），`web.version` 决定交给 SPA index 还是旧 handler。

---

## 3. Legacy SSR 路由（始终保持可用，v1 模式下亦然）

| Route | 说明 | Auth | 迁移状态 |
|---|---|---|---|
| `/legacy` | v0.8 控制台首页（dashboard） | 是 | legacy |
| `/legacy/character`、`/legacy/social`、`/legacy/memory`、`/legacy/memory/health`、`/legacy/memory/timeline` | 分流路径的旧页面直达别名 | 是 | legacy |
| `/users`、`/groups`、`/sessions` | 旧用户/群/会话页 | 是 | kept（v1 的能力已在 `/social/*`） |
| `/character/export`、`/character/import[/confirm]` | 角色数据导出/导入 | 是 | legacy |
| `/sandbox`、`/sandbox/{inspectors,needs,bible,trace,chat}`、`/behavior`、`/behavior/*` | 沙盒与行为页 | 是 | partially migrated（世界/话题已进 v1） |
| `/conversation`、`/conversation/continuity`、`/topics` | 对话运行期/话题 | 是 | partially migrated（话题在 v1 世界时间线） |
| `/memory/{search,retrieval-debug,embeddings,consolidation,correction,detail/{id}}` | 记忆进阶操作 | 是 | partially migrated（浏览/详情/时间线/健康度已进 v1） |
| `/tools`、`/tools/{name}`、`/tools/{executions,metrics,decision-debug,permissions}` | 旧工具页 | 是 | migrated（v1 `/abilities/tools*`） |
| `/stickers`、`/expressions` | 旧表情/口癖页 | 是 | migrated（v1 `/abilities/media`） |
| `/agent`、`/agent/{tasks,tasks/{id},simulator,policy,metrics}` | 旧 Agent 页 | 是 | migrated（v1 `/abilities/agent*`） |
| `/logs`、`/runtime` | 旧日志/Runtime 页 | 是 | migrated（v1 `/system/logs`、`/system/runtime`） |
| `/models`、`/config*`、`/prompts`、`/credentials` | 旧 AI/配置页 | 是 | migrated（W4 起进 v1） |
| `/api/*`（旧 JSON 端点） | 旧表单/重定向式接口 | 是 | kept（v1 用 `/api/v1`） |
| `/ws/events` | 唯一 WebSocket | 是 | kept（两套 UI 共用） |

**未删除任何旧路由**；`/api/v1` 与 `/ws/*` 从未被 SPA fallback 影响；未知 `/api/*` 返回 JSON 404。

---

## 4. 关键端点矩阵（`/api/v1`，写操作需 CSRF）

| 组 | 读 | 写 | 确认门 |
|---|---|---|---|
| 会话 | `GET /session`, `GET /csrf`, `GET /meta` | `POST/DELETE/PATCH /session`（POST 免 CSRF，其余必须） | — |
| 配置 | `/config/schema|effective|restart-pending|raw` | `PATCH /config`, `POST /config/validate|reset`, `PUT /config/raw` | `reset`/`raw` → `config.confirm_required` |
| 凭据 | `GET /credentials` | `PUT/DELETE /credentials/{domain}/{ref}` | 删除被引用 Key（`?force=1`）需显式确认 |
| AI | `/ai/status|providers|models|roles|usage` | `PUT/DELETE /ai/providers|models`, `PUT /ai/models/order`, `PUT /ai/roles/{role}`, `POST /ai/models/{name}/test`, `POST /ai/router/reset` | 删除 Provider/模型 → `ai.confirm_required`；force 需 `confirm="force"`；Router reset → `ai.confirm_required` |
| 角色/世界 | `/character`, `/character/state`, `/world`, `/world/timeline|trace|topics` | `PATCH /character`, `PATCH /character/state`, `POST /world/control/{action}`, `POST /world/topics/{id}/{action}` | `reset`/`reinitialize` → `world.confirm_required` |
| 社交 | `/social/users|users/{id}|groups|relationships|commitments[,/{id}]|sessions|spaces|overview` | `PATCH /social/users/{id}`, `PATCH /social/groups/{id}` | — |
| 记忆 | `/memories`, `/memories/{id}`, `/memories/timeline|health` | `POST /memories/{id}/{activate|archive|reembed|edit}` | `delete` → `memory.confirm_required` |
| 工具 | `/tools`, `/tools/{name}`, `/tools/{executions|metrics|permissions|decision-debug}` | `PATCH /tools/{name}`, `POST /tools/{name}/test`, `PUT/DELETE /tools/permissions`, `POST /tools/cache/clear` | test → `tools.confirm_required`（`confirm=<name>`）；cache clear → `confirm="clear"` |
| 媒体 | `/stickers`, `/expressions` | `POST /stickers/{id}/{...}`, `/stickers/reindex`, `POST /expressions/{id}/{...}` | delete → `media.confirm_required` |
| Agent | `/agent`, `/agent/tasks[,/{id}]` | `POST /agent/tasks/{id}/{pause|resume|cancel|retry|replay}`, `POST /agent/simulate` | 控制操作前端确认；后端校验状态机 |
| 日志/Runtime | `/logs/tail`, `/logs/channels`, `/runtime`, `/runtime/status`, `/runtime/scheduler`, `/overview` | `POST /runtime/tick`, `POST /runtime/actions/{name}` | 前端确认；受 `_world_lock` 与运行时保护 |
| Minecraft | `/minecraft` | `POST /minecraft/join`, `POST /minecraft/leave`；`POST /minecraft/events`（Bridge 回调，**免会话/免 CSRF**，Bearer token 门） | leave 前端确认；join 校验 host/port |
