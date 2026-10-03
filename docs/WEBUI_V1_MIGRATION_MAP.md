# CatooBot WebUI v1.0 · W5 迁移表（Migration Map）

> 旧 URL → v1 URL 的最终去向。`web.version = "v1"`（默认）时旧 SSR 页面仍在
> `/legacy` 与各自未冲突的旧路径上可用；`web.version = "v0.8"` 时 `/`、`/login`
> 与四个冲突路径交还给旧 SSR。基线：W4 `51cfaeef`。

图例：**migrated** = v1 已有对应页面；**kept** = 旧入口保留（自身就是 v1 入口或未迁移）；**legacy** = 仅旧版提供；**dispatched** = 同一路径按 `web.version` 分流。

| Old URL | New URL | Status | Replacement | Notes |
|---|---|---|---|---|
| `GET /` | `GET /` | dispatched | v1 `总览`（`pages/Dashboard.vue`） | `web.version=v0.8` 时回旧 dashboard；旧 dashboard 常驻 `/legacy` |
| `GET/POST /login` | `GET /login` | dispatched | v1 登录页 | `POST /login`（旧表单）保留，两套前端共用会话 |
| `GET /character` | `GET /character` | dispatched | `/character`（角色） | 旧 SSR 页面移到 `/legacy/character`（同 handler） |
| `POST /character` | — | kept | 旧表单（`/legacy/character` 提交回 `/character`） | persona 修改在 v1 由配置中心承担；角色页只读 |
| `POST /character/state` | `PATCH /api/v1/character/state` | migrated | 角色状态卡（v1 API） | v1 用 JSON API；旧表单保留 |
| `GET /character/export` | — | legacy | 仅旧版 | W5 不做数据导出（§99） |
| `POST /character/import[/confirm]` | — | legacy | 仅旧版 | 同上 |
| `GET /social` | `GET /social` | dispatched | `/social`（社交 · 用户） | 旧 dashboard → `/legacy/social` |
| `GET /social/observations` | `GET /social`（观察区块） | kept | 旧页仍可访问 | v1 的社交观察并入社交面板（后续可加） |
| `GET /social/group` | `GET /social`（群组 tab） | migrated | `/social/groups` | v1 用 `/social/groups` |
| `GET /social/simulator` | `GET /ai/test` | migrated | AI 测试台 | 社交模拟的替代是「决策预览」（`/abilities/tools` 内）与社交面板 |
| `POST /social/analyze` | — | legacy | 仅旧版 | v1 不自动跑分析（避免隐式写入） |
| `POST /social/replay` | — | dropped (legacy) | — | W1 已判定与 analyze 重复且无 UI 调用 |
| `GET/POST /social/policy` | `GET/PUT /api/v1/config` | migrated | 系统 · 设置（社交区） | 阈值属配置中心 |
| `GET /users` | `GET /social`（用户 tab） | migrated | `/social` | 旧 `/users` 仍可用 |
| `GET /users/{id}`（无旧页） | `GET /social/users/{person_id}` | migrated | 人物详情 | 新增能力 |
| `GET /groups` | `GET /social/groups` | migrated | 群组 | 旧 `/groups` 仍可用 |
| `POST /groups/toggle` | `PATCH /api/v1/social/groups/{id}` | migrated | 群组页开关 | **同一 service 写路径**（`AdminService.set_group_participation`） |
| `GET /sessions` | `GET /social/sessions` | migrated | 会话 | 旧 `/sessions` 仍可用 |
| `POST /api/sessions/clear` | — | kept | 旧入口 | 清理会话仍走旧 API（v1 暂未暴露） |
| `GET /memory` | `GET /memory` | dispatched | `/memory`（浏览） | 旧页面 → `/legacy/memory` |
| `GET /memory/health` | `GET /memory/health` | dispatched | `/memory/health` | 旧页面 → `/legacy/memory/health` |
| `GET /memory/detail/{id}` | `GET /memory/{id}` | migrated | 记忆详情 | 旧路径仍可用 |
| `GET /memory/timeline` | `GET /memory/timeline` | migrated | 时间线 | 旧 `/memory/timeline` 未被遮蔽（无冲突） |
| `GET/POST /memory/search` | `/memory?q=`（URL 状态） | migrated | 浏览页过滤器 | v1 用 query 参数 |
| `GET /memory/retrieval-debug` | — | legacy | 仅旧版 | 诊断能力保留在旧控制台 |
| `POST /memory/embeddings/{action}`、`/memory/consolidation/run` | — | legacy | 仅旧版 | 重型运维操作不迁移 |
| `GET/POST /memory/correction[/apply]` | `POST /api/v1/memories/{id}/edit`（单条） | partially migrated | 详情页编辑 | 计划型纠错仍是 known limitation |
| `GET /tools` | `GET /abilities/tools` | migrated | 工具 | 旧 `/tools` 仍可用（注意旧 `/tools/{name}` 曾遮蔽子页） |
| `GET /tools/{name}` | `GET /abilities/tools/{name}` | migrated | 工具详情 | 含 [测试]（真实外部请求 + 确认） |
| `GET /tools/permissions` | `GET /abilities/tools`（权限区） | migrated | 权限规则 | 增删走 `PUT/DELETE /api/v1/tools/permissions` |
| `GET /tools/decision-debug` | `GET /abilities/tools`（决策预览） | migrated | 只读预览 | 绝不执行工具 |
| `GET /stickers` | `GET /abilities/media` | migrated | 贴纸库 | 旧 `/stickers` 仍可用 |
| `GET /expressions` | `GET /abilities/media`（口癖区） | migrated | 口癖 | 旧 `/expressions` 仍可用 |
| `GET /agent`、`/agent/tasks*` | `GET /abilities/agent[/tasks/{id}]` | migrated | Agent | 真实 Runtime；`disabled` 时显示「尚未启用」 |
| `GET /logs` | `GET /system/logs` | migrated | 日志（Debug Console） | 旧的 `/logs` 仍可用 |
| `GET /runtime` | `GET /system/runtime` | migrated | Runtime | 旧的 `/runtime` 仍可用 |
| `/config`、`/prompts`、`/models`、`/credentials` | `/system/settings`、`/ai/*` | migrated (W4) | 配置中心 / AI | W4 已完成 |
| `/sandbox*`、`/behavior`、`/conversation*`、`/topics`、`/expressions`（页面） | `/character/world`、`/character/world/timeline`、`/abilities/media` | partially migrated | 见上表 | 未迁移部分留在 `/legacy` |
| `/legacy` | `GET /legacy` | kept | v0.8 控制台首页 | W3 起常驻；新增 `/legacy/character`、`/legacy/social`、`/legacy/memory`、`/legacy/memory/health` 直达别名 |

---

## 冲突与分流（`web.version` 语义）

| 路径 | v1 行为（默认） | v0.8 行为 |
|---|---|---|
| `/` | SPA 总览 | 旧 dashboard |
| `/login` | SPA 登录 | 旧登录页（`POST /login` 两种模式都保留） |
| `/character` | SPA 角色 | 旧角色页 |
| `/social` | SPA 社交 | 旧社交面板 |
| `/memory` | SPA 记忆 | 旧记忆浏览 |
| `/memory/health` | SPA 记忆健康度 | 旧记忆健康页 |
| `/legacy`、`/legacy/character`、`/legacy/social`、`/legacy/memory`、`/legacy/memory/health` | 始终旧 SSR | 同左 |

未列出的旧路径（`/users`、`/groups`、`/sessions`、`/tools`、`/stickers`、`/expressions`、`/agent`、`/logs`、`/runtime`、`/sandbox*`、`/behavior`、`/config*`、`/prompts`、`/conversation*`、`/topics`、`/credentials`、`/models` 等）在任何版本下都保持旧 SSR 行为，未被 W5 改动。

## 未删除任何旧路由

W5 只**新增**四个分流点与四个 `/legacy/*` 别名，`GET /`、`GET /login` 的分流沿用 W3。
旧 SSR 全部路由（含 `/tools/{name}` 这类既有通配）保持注册顺序与行为不变，`/api/v1`
与 `/ws` 命名空间从未被 SPA fallback 影响。
