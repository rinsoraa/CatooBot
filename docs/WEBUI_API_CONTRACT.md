# CatooBot WebUI v1.0 · API 契约（W1 定稿，W2 实施）

> 本文件定义 WebUI v1.0 前后端之间的**唯一契约**。W2（后端 API 基座）、W3（前端外壳）、
> W4（AI 与配置）、W5（领域页）、W6（切换）均以实现本契约为准；契约本身如需变更，先在本文档修订。
> 约束来源：本任务 §0-§62（不允许 WebUI 改变 Phase 16 Core 行为；Key 不进 config.yaml、不回显明文；
> 旧 UI 保留为 `/legacy` 回滚面）。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 前缀 | `/api/v1`（新 JSON API 的唯一前缀；旧路由原样保留，仅供 `/legacy`） |
| 传输 | JSON（`application/json; charset=utf-8`）；无表单 POST、无 302 |
| 认证 | 复用现有会话 Cookie `catoobot_session`（`app/web/auth.py`，httpOnly，12h TTL）；未登录 → `401` |
| CSRF | 写操作（POST/PUT/PATCH/DELETE）必须带 `X-CSRF-Token` 头；token 由 `GET /api/v1/session` 下发（见 §2） |
| 幂等 | 所有写操作支持可选 `Idempotency-Key` 头（同 key 重复提交返回首次结果，用于防双击/断线重试） |
| 时间 | 一律 Unix 秒（int）；前端本地化显示 |
| 分页 | `?limit=&cursor=`；响应含 `{"items": [...], "next_cursor": str|null, "total": int|null}` |
| 语言 | 错误消息支持 `?lang=zh|en`（默认跟随 Cookie `catoobot_lang`） |
| 版本 | `GET /api/v1/meta` 返回 `{"api": "v1", "app_version": ..., "core_behavior_phase": "P16"}` |

### 1.1 响应信封

成功：

```json
{ "ok": true, "data": { ... }, "meta": { "request_id": "..." } }
```

失败：

```json
{ "ok": false, "error": { "code": "config.invalid_value", "message": "人话错误", "field": "runtime.tick_interval_seconds", "detail": null }, "meta": { "request_id": "..." } }
```

- `code` 为稳定机器码（下表按域给前缀）；`message` 面向用户，**不得包含密钥/堆栈**；
- 未知异常 → `500 {"code": "internal.error"}`，细节只进服务端日志。

### 1.2 错误码前缀

| 前缀 | 域 |
|---|---|
| `auth.*` | 会话/登录/CSRF（`auth.unauthorized`、`auth.csrf`、`auth.throttled`、`auth.bad_credentials`） |
| `config.*` | 配置中心（`config.invalid_value`、`config.restart_required`、`config.readonly_key`、`config.stale`） |
| `credential.*` | 凭据（`credential.missing`、`credential.invalid_name`、`credential.write_failed`） |
| `ai.*` | Provider/模型（`ai.provider_unknown`、`ai.model_unknown`、`ai.test_failed`、`ai.no_key`） |
| `world.*` | 沙盒/角色（`world.not_running`、`world.confirm_required`、`world.busy`） |
| `memory.*` / `social.*` / `media.*` / `tools.*` / `agent.*` | 各领域动作冲突/不存在 |
| `internal.*` | 兜底 |

### 1.3 关键设计决策（与今日现状的差异，必须实现）

1. **CSRF 对 SPA 可见**：今天 token 由 httpOnly 会话 Cookie 派生、只注入服务端表单（`app/web/security.py:49-75`），
   JS 拿不到。v1 通过 `GET /api/v1/session` 返回 token（同源、需已登录会话）。
2. **配置快照携带来源**：每个键返回 `value / source / level / usage_status / restart_required / hot_reload`，
   直接消费 `WEBUI_CONFIG_MATRIX.md` 的分类；`DEFINED_BUT_UNUSED`/`LEGACY` 默认 **不出现在普通视图**，
   仅在「高级/专家」标记 `hidden: true` 或需要 `?include=unused` 才可见。
3. **密钥永远只回 masked 状态**：任何响应不得包含 Key 明文；写后只回 `{"env_name": ..., "masked": "sk-***abcd", "saved": true}`。
4. **重启类配置显式标注**：响应带 `restart_required: true` 的键，保存成功后返回 `config.restart_required` 作为
   `data.warnings[]`（不是错误），前端展示「需重启生效」。
5. **危险动作要 confirm**：`reset / reinitialize / 清空记忆 / 停沙盒` 必须带 `{"confirm": "<动作名>"}`，
   否则 `409 world.confirm_required`（沿用 `routes/sandbox.py:451-474` 的既有约定）。

---

## 2. 会话与安全

| 方法与路径 | 说明 | 请求 | 响应 `data` |
|---|---|---|---|
| `GET /api/v1/session` | 会话引导：当前用户 + CSRF token + 权限位 | — | `{"user": {"name": "admin"}, "csrf_token": "…", "permissions": {"admin": true}, "lang": "zh", "theme": "dark"}` |
| `POST /api/v1/session` | 登录（替代 `POST /login`，JSON 版） | `{"username", "password"}` | 同 `GET /api/v1/session`；失败 `401 auth.bad_credentials`；限速 `429 auth.throttled`（沿用 `LoginThrottle`） |
| `DELETE /api/v1/session` | 登出 | — | `{"logged_out": true}` |
| `PATCH /api/v1/session` | 偏好（语言/主题/导航折叠） | `{"lang"?, "theme"?, "nav_collapsed"?}` | 回显偏好 |
| `POST /api/v1/session/password` | 改密码（PBKDF2，沿用 `AuthService.set_password`） | `{"old_password", "new_password"}` | `{"changed": true}`（改密后所有会话失效，含当前） |

- 首页 cookie 之外不引入新的会话机制；不引入 token 存储到 localStorage。
- `app/web/security.py` 的 `CSRF_EXEMPT_PATHS` 随旧路由保留；v1 路径全部要求 CSRF（登录除外）。

---

## 3. 系统与运行时（只读投影 + 少量动作）

| 方法与路径 | 说明 | 响应 `data`（键为契约） |
|---|---|---|
| `GET /api/v1/meta` | 版本/构建信息 | `{"api", "app_version", "webui_version", "core_behavior_phase", "python", "started_at", "uptime_seconds"}` |
| `GET /api/v1/overview` | **总览仪表盘一屏数据**（聚合，单请求） | `{"qq": {"online", "self_id", "messages_received", "users", "groups", "sessions", "last_event_at"}, "ai": {"enabled", "current_model", "models_ok", "models_total", "requests", "errors", "rate_limited"}, "world": {"phase", "location", "action": {"name", "progress", "started_at", "planned_end_at"}, "modes": [], "needs": {"critical": [], "pressing": []}, "world_revision", "cognitive_revision", "session": {"active", "person_id"}, "interrupted": bool}, "runtime": {"scheduler": {"running", "interval_seconds", "ticks", "catchups", "last_tick_at"}, "watchdog": {"last_lag_ms", "max_lag_ms", "lag_events"}, "database": {"connected"}, "hub": {"subscribers", "published", "dropped"}}, "counts": {"memories", "experiences", "goals_open", "commitments_open"}}` |
| `GET /api/v1/runtime/status` | 运行时细项（`overview` 的 runtime 段独立化，供轮询） | 同上 `runtime` 段 |
| `GET /api/v1/runtime/scheduler` | 世界调度器 | `{"running", "interval_seconds", "ticks", "catchups", "last_tick_at", "last_report"}` |
| `POST /api/v1/runtime/tick` | 手动跑一次世界 tick（**新增 admin 动作**，限流 1 次/秒） | `{"ran": true, "minutes": 1.0, "report": {...}}`；冲突 `409 world.busy` |
| `POST /api/v1/runtime/actions/{name}` | 现有运行时动作 JSON 化：`reload_persona / reload_plugins / reload_models / restore_model_overrides` | `{"done": true, "action": name, "detail": "…"}` |
| `GET /api/v1/logs/tail?limit=400&level=&q=` | 日志尾读（服务端过滤，替代页面内过滤） | `{"items": [{"ts", "level", "logger", "message"}], "file": "logs/catoobot.log", "truncated": bool}` |
| `GET /api/v1/logs/channels` | 叙述频道字典（图标/标签，前端图例） | `{"channels": [{"key": "world", "icon": "🌍", "label": "世界"}]}` |

说明：`last_event_at`、`database.connected` 今日无公开访问器（见 `WEBUI_V1_AUDIT.md` §5），
W2 需在 `AdminService` 增只读方法；`runtime/tick` 复用 `RuntimeScheduler.tick_once()`（无副作用外溢，受 `_world_lock` 保护）。

---

## 4. 配置中心（本契约的核心）

### 4.1 读取

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/config/schema?area=&level=` | 返回**可渲染的表单模型**：每个键 `{"key", "label", "type", "default", "level", "usage_status", "restart_required", "hot_reload", "choices"?, "min"?, "max"?, "unit"?, "help", "hidden"}`。`area` ∈ `basic/onebot/behavior/social/memory/media/tools/agent/sandbox/runtime/logging/webui/ai/credentials/advanced`；`hidden=true` 的键（unused/legacy）默认不返回 |
| `GET /api/v1/config/effective?area=` | 生效值：`{"items": [{"key", "value", "source", "restart_required", "hot_reload", "pending": bool}]}`，`source` ∈ `env / override / yaml / models_block / default`（对应 `WEBUI_CONFIG_MATRIX.md` §1.1 的优先级链） |
| `GET /api/v1/config/restart-pending` | 是否有「已保存但需重启」的键：`{"pending": ["onebot.port"], "since": ts}` |

### 4.2 写入

| 方法与路径 | 说明 |
|---|---|
| `PATCH /api/v1/config` | 批量保存：`{"values": {"runtime.tick_interval_seconds": 1.0}, "confirm"?: "…"}`。逐键校验（pydantic 子模型）→ 原子写 `config/overrides.yaml`（沿用 `settings.py:844-857`）→ 调用 `ConfigAdminService.apply()` 热应用；返回 `{"saved": [...], "warnings": [{"code": "config.restart_required", "keys": [...]}]}` |
| `POST /api/v1/config/reset` | 清空覆盖（`{"confirm": "reset"}`） |
| `GET /api/v1/config/raw` | 读取 overrides.yaml 原文（高级/专家） |
| `PUT /api/v1/config/raw` | 保存原文（校验 → 原子写；`{"confirm": "raw"}`） |
| `GET /api/v1/config/export` | 导出**脱敏**配置全量（不含任何 secret；`api_key_env` 只给变量名） |
| `POST /api/v1/config/import` | 导入配置（`{"confirm": "import"}`；同样不接收 secret，secret 必须走凭据接口） |

规则：
- 未知键 → `400 config.unknown_key`；类型/范围错误 → `400 config.invalid_value`（带 `field`）。
- `level=expert` 键的 PATCH 需要当前会话已通过「专家模式」解锁（`PATCH /api/v1/session` 设 `expert=true`），
  防止普通用户误改；解锁只影响展示与写入校验，不改变 Core。
- **禁止**通过任何配置 API 写入 `character_bible.md`；Bible 只读（`GET /api/v1/character/bible` 返回覆盖统计，
  不含全文——全文仅在本地文件系统）。

---

## 5. 凭据（AI Key / OneBot Token / 工具凭据）

统一规则（任务 §凭据条款）：
- Key **绝不进 `config.yaml`/`overrides.yaml`**；AI Key 写 `.env`（`NAME=value`），工具凭据写 `data/secrets.json`；
- 写入必须**原子替换**（tmp + `Path.replace`，与 `settings.py:854-856` 同法）+ **保留既有行** + 不触碰无关行；
- 读取只返回 masked：`{"env_name", "masked", "configured": bool, "source": "env|dotenv"}`；
- 保存后刷新页面/GET 也只拿到 masked；日志与错误消息不得出现明文（沿用 `redact()` 与 `CredentialManager.mask()`）。

| 方法与路径 | 说明 | 请求 | 响应 `data` |
|---|---|---|---|
| `GET /api/v1/credentials` | 凭据总览（按域分组） | — | `{"items": [{"domain": "ai", "ref": "OPENAI_API_KEY", "masked": "sk-***abcd", "configured": true}, {"domain": "onebot", "ref": "CATOOBOT_ONEBOT_ACCESS_TOKEN", ...}, {"domain": "tool", "ref": "weather_api_key", ...}]}` |
| `PUT /api/v1/credentials/{domain}/{ref}` | 写入/轮换 | `{"value": "…", "confirm"?: "…"}` | `{"saved": true, "ref", "masked", "restart_required": bool}` |
| `DELETE /api/v1/credentials/{domain}/{ref}` | 删除（AJ Key 场景建议同时把对应 provider 的 `api_key_env` 置空，需 `confirm`） | — | `{"deleted": true}` |
| `POST /api/v1/credentials/test` | 用给定 provider 现测一次（不落盘）；若 `value` 缺省则用已存 Key | `{"provider": "openai", "model"?: "gpt-4o-mini", "value"?: "…"}` | `{"ok": true, "latency_ms": 412, "model": "…", "reply": "pong"}` / `{"ok": false, "code": "ai.test_failed", "message": "…"}` |

`domain` ∈ `ai / onebot / tool / embedding / web`；`ref` 受限字符集 `[A-Z0-9_]+`（tool 例外，见既有命名）。
> 今日缺口：AI Key 无任何写入路径（`app/ai/engine.py:106` 只读环境变量），W2 必须补齐；
> `.env` 写入非原子（`config_admin.py:171`），W2 一并改为原子。

---

## 6. AI 与模型

| 方法与路径 | 说明 | 请求 | 响应 `data` |
|---|---|---|---|
| `GET /api/v1/ai/providers` | Provider 列表（含 Key 状态） | — | `{"items": [{"name", "type", "base_url", "api_key_env", "has_key", "restart_required"}]}` |
| `PUT /api/v1/ai/providers/{name}` | 新增/修改 Provider（upsert） | `{"type", "base_url", "api_key_env"}` | 同单项；`type` 目前仅 `openai_compatible`，未知类型 → `400 ai.provider_unknown` |
| `DELETE /api/v1/ai/providers/{name}` | 删除（其下模型必须为空或 `?force=true` 连带删除） | — | `{"deleted": true, "models_removed": 2}` |
| `GET /api/v1/ai/models` | 模型列表（含路由器实时状态 + 7 天用量） | — | `{"items": [{"name", "provider", "model", "enabled", "order", "roles": ["chat"], "in_cooldown", "cooldown_until", "failure_count", "usage": {"calls", "failures", "avg_latency_ms", "tokens"}}]}` |
| `PUT /api/v1/ai/models/{name}` | 新增/修改模型 | `{"provider", "model", "enabled"}` | 同单项 |
| `DELETE /api/v1/ai/models/{name}` | 删除（若被角色引用 → `409 ai.model_in_use`，带 `roles`） | — | `{"deleted": true}` |
| `PUT /api/v1/ai/models/order` | 失败转移顺序（**持久化**，修复今日 priority 不重放的问题） | `{"order": ["fast", "smart", "vision"]}` | `{"order": [...]}` |
| `POST /api/v1/ai/models/{name}/test` | 单模型测试（走完整路由，含失败转移） | `{"prompt"?: "ping"}` | `{"ok", "latency_ms", "attempts": [...], "final_model"}` |
| `GET /api/v1/ai/roles` | 角色绑定现状（chat/vision/decision/conversation/extraction/planner/evaluator/social/embedding） | — | `{"items": [{"role", "key", "model", "source"}]}` |
| `PUT /api/v1/ai/roles/{role}` | 绑定角色模型（写 `sandbox.decision_model` 等对应键；embedding 走专属字段并标注需重启） | `{"model": "smart" \| ""}` | 同 `roles` 单项 + `restart_required` 标记 |
| `GET /api/v1/ai/usage?days=7&group_by=model\|provider\|purpose` | 用量聚合（补齐今日只按 model 聚合的缺口） | — | `{"items": [{"key", "calls", "failures", "rate_limited", "server_errors", "tokens", "avg_latency_ms", "max_latency_ms"}]}` |
| `POST /api/v1/ai/router/reset` | 清空冷却/失败计数（只影响内存态） | `{"confirm": "reset"}` | `{"reset": true}` |

要点：
- 「默认聊天模型」= `order[0]`（显式化，不再靠隐式插入序）；`chat` 角色可单独钉选。
- 一次新装配置闭环（W4 验收）：**加 Provider（接口地址+Key）→ 加模型 → 设默认 → 测试**，全程不碰 YAML/.env。
- 保存 AI 配置会重建 `AIEngine`（`ai/engine.py:128`），冷却态被重置 —— v1 前端在保存后清空冷却列并提示。

---

## 7. 领域只读/动作（W5 页面直接消费）

### 7.1 角色与世界

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/character` | persona（identity/personality/speaking_style/behavior_rules/system_prompt）+ 来源（DB persona vs config） |
| `PATCH /api/v1/character` | 更新 persona（热生效，沿用 `AdminService.persona`） |
| `GET /api/v1/character/state` | mood/energy/activity/阶段 |
| `POST /api/v1/character/export` / `POST /api/v1/character/import` (`confirm`) | 导出/导入（在今日 export/import 基础上 JSON 化；导入返回 diff 预览，二次确认才应用） |
| `GET /api/v1/world` | 沙盒总览：phase、位置、当前动作+进度、modes、needs（含 bands）、goals、commitments（含 due）、relationships（top）、world/cognitive revision、session/interrupt |
| `GET /api/v1/world/trace?limit=` | 结构化事件轨迹（`sandbox.store.recent_events/timeline`） |
| `POST /api/v1/world/control/{action}` | `pause / resume / reset / reinitialize`（沿用 `routes/sandbox.py:451-474`；`reset`/`reinitialize` 需 `confirm`） |
| `POST /api/v1/world/simulate` | 干跑 N 小时（沿用 `routes/sandbox.py:430-449` 的备份/恢复；需 `confirm`，单次限时） |

### 7.2 对话与社交

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/conversation` | 对话运行期：活动轮次、过期轮次、模式上限 |
| `GET /api/v1/conversation/continuity` | 未闭环、画像、共同经历（`ContinuityManager`） |
| `GET /api/v1/social` | 社交面板（观察/阈值/频率现状） |
| `GET /api/v1/social/observations?limit=` | 结构化决策日志 |
| `POST /api/v1/social/analyze` | 立即分析（不发送；`POST /social/replay` 与其重复，v1 不提供 replay） |
| `GET /api/v1/users?limit=&cursor=` / `PATCH /api/v1/users/{user_id}` | 用户画像/阶段/主动开关 |
| `GET /api/v1/groups` / `PATCH /api/v1/groups/{group_id}` | 群列表/参与开关 |
| `GET /api/v1/sessions` / `DELETE /api/v1/sessions/{key}` | 会话上下文查看/清除 |
| `GET /api/v1/topics` / `POST /api/v1/topics/{id}/{action}` | 未闭环话题（resolve/forget/delete） |
| `GET /api/v1/expressions` / `PATCH /api/v1/expressions/{id}` / `DELETE` | 口癖学习管理 |

### 7.3 记忆

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/memories?q=&kind=&limit=&cursor=` | 浏览/检索（含检索调试参数 `debug=true` 时返回逐条打分） |
| `GET /api/v1/memories/{id}` | 详情（出处/关联/时间线） |
| `POST /api/v1/memories/{id}/{action}` | activate/archive/reembed/delete（沿用 `MemoryAdminService.action`） |
| `GET /api/v1/memories/health` | 计数/向量覆盖/保留 |
| `POST /api/v1/memories/consolidation/run` | 手动巩固 |
| `POST /api/v1/memories/embeddings/{action}` | rebuild/retry/clear-cache |
| `POST /api/v1/memories/correction/plan` / `POST /api/v1/memories/correction/apply` | 自然语言纠错（两步：计划 → `confirm` 应用） |

### 7.4 媒体与能力

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/stickers` / `POST /api/v1/stickers/{id}/{action}` / `POST /api/v1/stickers/reindex` | 贴纸库 |
| `GET /api/v1/tools` / `GET /api/v1/tools/{name}` / `PATCH /api/v1/tools/{name}` / `POST /api/v1/tools/{name}/test` | 工具注册表/配置/测试 |
| `GET /api/v1/tools/executions` / `GET /api/v1/tools/decision-debug` | 执行记录/选择打分 |
| `GET /api/v1/tools/permissions` / `PUT/DELETE /api/v1/tools/permissions` | 权限规则 |
| `GET /api/v1/agent` / `GET /api/v1/agent/tasks` / `GET /api/v1/agent/tasks/{id}` / `POST /api/v1/agent/tasks/{id}/{action}` | Agent 面板/任务/控制（pause/resume/cancel/retry） |
| `POST /api/v1/agent/simulate` | 模拟（dry run） |

---

## 8. WebSocket 契约（复用 `/ws/events`）

保留现有端点与信封 `{"topic", "ts", "data"}`（`app/web/realtime.py:82`），v1 **新增 topic**（服务端发布方在
`RealtimeHub` 侧统一裁剪，慢消费者丢最旧不变）：

| topic | 载荷 | 频率 |
|---|---|---|
| `hello`（保留） | `hub.stats()` | 连接时 |
| `narration`（保留） | `{channel, level, message}`（已 redact） | 事件驱动 |
| `status`（保留） | 现有 `live_status`；**v1 扩展为 `overview` 的轻量版**（qq/ai/world/runtime/scheduler） | 5s（可订阅 `?interval=`） |
| `world`（新增） | `{phase, location, action, needs, revision}` 变化驱动（仅在变化时发，沿用 ConsoleWorld 快照的「变化才推」语义） | 变化时 |
| `scheduler`（新增） | `{running, ticks, last_tick_at, last_report}` | 每 10s 或变化时 |
| `log`（新增） | 与 `narration` 同源，但可带 `level >= X` 过滤参数 | 事件驱动 |

客户端 → 服务端：仅一条最小订阅帧 `{"subscribe": ["narration", "status", ...], "interval": 5}`
（服务端忽略未知字段；不引入 RPC，所有命令走 HTTP API）。

---

## 9. 旧路由 → v1 映射（W6 切换用）

| 现状（保留在 `/legacy`） | v1 目标 | 处置 |
|---|---|---|
| `GET /`, `/runtime`, `/logs`, `/ws/events` | `/api/v1/overview`, `/runtime/*`, `/logs/*`, WS 同址 | 重设计后替换 |
| `/models` + `POST /api/models/override` | `/api/v1/ai/*` | 合并进 AI 区 |
| `/character*`, `/sandbox*`, `/conversation*`, `/topics` | `/api/v1/character/*`, `/api/v1/world/*`, `/api/v1/conversation/*`, `/api/v1/topics` | 重设计 |
| `/users`, `/groups`, `/sessions`, `/expressions`, `/social*` | `/api/v1/users|groups|sessions|expressions|social*` | 合并 |
| `/memory*` | `/api/v1/memories*` | 合并（8 tab → 2 视图 + 高级抽屉） |
| `/tools*`, `/agent*`, `/stickers`, `/credentials` | `/api/v1/tools|agent|stickers|credentials` | 保留能力，JSON 化 |
| `/config*`, `/prompts` | `/api/v1/config*`, `/api/v1/prompts` | 重设计为「配置中心 + 提示词」 |
| `POST /social/replay` | — | **不迁移**（与 analyze 重复且无 UI 调用） |
| `GET /behavior` | — | **不迁移**（纯别名 302） |
| `POST /sandbox/simulate`, `POST /api/sandbox/control` | `/api/v1/world/simulate`, `/api/v1/world/control/*` | 接线为正式入口（今日仅测试调用） |

切换（W6）：`/` 由 v1 前端服务；`/legacy` 继续服务旧 UI（保留直至回滚窗口关闭）；提供 `webui.version`
配置（`v1` / `v0.8`）作为一键回滚开关；v1 前端静态产物由新增的 `/assets` 静态路由本地服务（**禁止 CDN**）。

---

## 10. W2 验收清单（契约落地的最小可测面）

1. `GET /api/v1/session` 返回 CSRF token；未登录一律 401；登录限速沿用。
2. 所有 v1 写操作无 CSRF 头 → 403 `auth.csrf`；带错 token → 403。
3. `GET /api/v1/config/effective` 的 `source` 与 `WEBUI_CONFIG_MATRIX.md` §1.1 优先级组合一致（env > override > yaml > models 段 > default）。
4. `GET /api/v1/config/schema` 默认不返回 `DEFINED_BUT_UNUSED`/`LEGACY` 键；`?include=unused` 才可见。
5. `PUT /api/v1/credentials/ai/…` 写入后：`overrides.yaml` 无明文、`.env` 原有行保留、GET 只回 masked、
   日志中无明文（用现有 `redact()` 断言）。
6. `PATCH /api/v1/config` 对 restart 类键返回 `warnings[].code == "config.restart_required"` 且不报错。
7. `PUT /api/v1/ai/models/order` 后重启（新 `ConfigAdminService` 实例）顺序仍生效（修复 priority 不重放）。
8. `GET /api/v1/overview` 单请求可渲染总览四卡；`runtime.scheduler.ticks` 随 `POST /api/v1/runtime/tick` 增长。
9. `POST /api/v1/world/control/reset` 缺 `confirm` → 409；带对值 → 成功。
10. Core 行为零变化：全量 pytest 通过；`app/sandbox/**`、`app/runtime/**`、`app/integrations/**` 无行为改动
    （仅可能新增只读访问器）。
