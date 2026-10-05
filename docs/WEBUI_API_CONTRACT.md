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
| `minecraft.*` | Minecraft 连接层（`minecraft.disabled`、`minecraft.runtime_down`、`minecraft.invalid_target`、`minecraft.session_active`、`minecraft.not_connected`、`minecraft.bad_event`、`minecraft.chat_empty`、`minecraft.action_busy`、`minecraft.action_invalid`、`minecraft.action_failed`、`minecraft.path_not_found`、`minecraft.player_not_found`、`minecraft.player_lost`、`minecraft.follow_target_too_far`） |
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
| `GET /api/v1/overview` | **总览仪表盘一屏数据**（聚合，单请求） | `{"qq": {"online", "self_id", "messages_received", "users", "groups", "sessions", "last_event_at"}, "ai": {"enabled", "current_model", "models_ok", "models_total", "requests", "errors", "rate_limited", "status"}, "world": {"phase", "location", "action": {"name", "progress", "started_at", "planned_end_at"}, "modes": [], "needs": {"critical": [], "pressing": []}, "world_revision", "cognitive_revision", "session": {"active", "person_id"}, "interrupted": bool}, "runtime": {"scheduler": {"running", "interval_seconds", "ticks", "catchups", "last_tick_at", "last_report"}, "watchdog": {"last_lag_ms", "max_lag_ms", "lag_events"}, "database": {"connected", "size_bytes"}, "hub": {"subscribers", "published", "dropped", "queue_size"}, "process": {"uptime_seconds", "started_at", "version", "python"}, "onebot": {"state", "connected", "self_id", "last_event_at", "received", "accepted", "deduped", "dropped", "self_ignored", "responses", "sent", "failed", "pending_outbound", "busy", "lanes": []}}, "counts": {"memories", "experiences", "goals_open", "commitments_open"}}`（W5 扩展见 §7.4 末） |
| `GET /api/v1/runtime/status` | 运行时细项（`overview` 的 runtime 段独立化，供轮询） | 同上 `runtime` 段；W5 起另含 `process` / `onebot`，见 §7.4 末 |
| `GET /api/v1/runtime/scheduler` | 世界调度器 | `{"running", "interval_seconds", "ticks", "catchups", "last_tick_at", "last_report"}` |
| `POST /api/v1/runtime/tick` | 手动跑一次世界 tick（**新增 admin 动作**，限流 1 次/秒） | `{"ran": true, "minutes": 1.0, "report": {...}}`；冲突 `409 world.busy` |
| `POST /api/v1/runtime/actions/{name}` | 现有运行时动作 JSON 化：`reload_persona / reload_plugins / reload_models / restore_model_overrides` | `{"done": true, "action": name, "detail": "…"}` |
| `GET /api/v1/logs/tail?limit=400&level=&q=&channel=` | 日志尾读（服务端过滤；`limit` ≤500，W5 增 `channel`） | `{"items": [{"ts", "time", "level", "logger", "channel", "message"}], "file": "logs/catoobot.log", "truncated": bool, "parsed": int, "total": int}`；`channel` 从叙述前缀解析，非叙述行为 `null`（详见 §7.4） |
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
| `POST /api/v1/credentials/test` | 用给定 provider 现测一次（不落盘）；若 `value` 缺省则用已存 Key | `{"provider": "openai", "model"?: "gpt-4o-mini", "value"?: "…"}` | `{"ok": true, "latency_ms": 412, "model": "…", "reply": "pong"}` / `{"ok": true, "error_type": "empty_finish_length", "message": "连通正常…", "reply": "", "http_status": 200}`（推理占满探测预算，见 §6 要点）/ `{"ok": false, "code": "ai.test_failed", "message": "…"}` |

`domain` ∈ `ai / onebot / tool / embedding / web`；`ref` 受限字符集 `[A-Z0-9_]+`（tool 例外，见既有命名）。
> 今日缺口：AI Key 无任何写入路径（`app/ai/engine.py:106` 只读环境变量），W2 必须补齐；
> `.env` 写入非原子（`config_admin.py:171`），W2 一并改为原子。

---

## 6. AI 与模型

| 方法与路径 | 说明 | 请求 | 响应 `data` |
|---|---|---|---|
| `GET /api/v1/ai/providers` | Provider 列表（含 Key 状态） | — | `{"items": [{"name", "type", "base_url", "api_key_env", "has_key", "restart_required"}]}` |
| `PUT /api/v1/ai/providers/{name}` | 新增/修改 Provider（upsert） | `{"type", "base_url", "api_key_env"}` | 同单项；`type` 目前仅 `openai_compatible`，未知类型 → `400 ai.provider_unknown` |
| `DELETE /api/v1/ai/providers/{name}` | 删除（须带 `confirm` 防误删；其下模型必须为空或 `?force=true` 连带删除） | `{"confirm": "<name>"}`；`?force=true` 时为 `{"confirm": "force"}` | `{"deleted": true, "models_removed": 2}`；`confirm` 缺失/不符 → `409 ai.confirm_required`（不可恢复，级联时连同模型一起删除）；未知 → `404 ai.provider_unknown`；被模型引用（未 force）→ `409 ai.provider_in_use`（带 `models`）；来自 config.yaml → `409 config.base_defined` |
| `GET /api/v1/ai/models` | 模型列表（含路由器实时状态 + 7 天用量） | — | `{"items": [{"name", "provider", "model", "enabled", "order", "roles": ["chat"], "in_cooldown", "cooldown_until", "cooldown_remaining_seconds", "failure_count", "usage": {"calls", "failures", "avg_latency_ms", "tokens"}}]}` |
| `PUT /api/v1/ai/models/{name}` | 新增/修改模型 | `{"provider", "model", "enabled"}` | 同单项 |
| `DELETE /api/v1/ai/models/{name}` | 删除（须带 `confirm` 防误删；若被角色引用 → `409 ai.model_in_use`，带 `roles`） | `{"confirm": "<name>"}`；`?force=true` 时为 `{"confirm": "force"}` | `{"deleted": true, "roles_cleared": [...]}`；`confirm` 缺失/不符 → `409 ai.confirm_required`（不可恢复，force 时解除用途绑定）；未知 → `404 ai.model_unknown` |
| `PUT /api/v1/ai/models/order` | 失败转移顺序（**持久化**，修复今日 priority 不重放的问题） | `{"order": ["fast", "smart", "vision"]}` | `{"order": [...]}` |
| `POST /api/v1/ai/models/{name}/test` | 单模型测试（走完整路由，含失败转移） | `{"prompt"?: "ping"}` | `{"ok", "model", "requested_model", "provider", "provider_model", "latency_ms", "http_status", "http_status_source", "error_type", "message", "response"}` |
| `GET /api/v1/ai/status` | AI 概览的一屏数据（W4）：健康状态 + 计数 + fallback 链 + 429/5xx + cooldown | — | `{"status", "enabled", "configured", "checks{has_provider,has_credential,has_model,chat_bound}", "providers{total,with_key,missing_key}", "models{total,enabled,disabled,usable,cooldown}", "chat_model", "fallback_chain", "errors{rate_limited,server_errors}", "cooldown_models"}` |
| `GET /api/v1/ai/roles` | 角色绑定现状（chat/vision/decision/conversation/extraction/planner/evaluator/social/embedding） | — | `{"items": [{"role", "key", "model", "source"}]}` |
| `PUT /api/v1/ai/roles/{role}` | 绑定角色模型（写 `sandbox.decision_model` 等对应键；embedding 走专属字段并标注需重启） | `{"model": "smart" \| ""}` | 同 `roles` 单项 + `restart_required` 标记 |
| `GET /api/v1/ai/usage?days=7&group_by=model\|provider\|purpose` | 用量聚合（补齐今日只按 model 聚合的缺口） | — | `{"items": [{"key", "calls", "failures", "rate_limited", "server_errors", "tokens", "avg_latency_ms", "max_latency_ms"}]}` |
| `POST /api/v1/ai/router/reset` | 清空冷却/失败计数（只影响内存态） | `{"confirm": "reset"}` | `{"reset": true}` |

要点：
- **测试结果只有一个形状**（W4 统一，替换了本节早前示例中的 `attempts/final_model`）：
  `ok / model / requested_model / provider / provider_model / latency_ms / http_status / http_status_source / error_type / message / response`，
  其中 `model` 是配置里的别名、`provider_model` 是真正作答的服务商模型 id（故障转移后两者可能不同）。
  `http_status_source` ∈ `upstream`（Provider 真的回了状态）/ `error_class`（由错误分类推导的规范状态）/ `""`（无 HTTP 语义，如超时/连接失败）。
  成功时 `response` 是模型回复文本且 `message` 为空，失败时相反。
  **第三种形状（2026-10-05 复盘）**：`ok=true` 且 `error_type="empty_finish_length"`、`response=""`，
  由 `message` 说明——上游确实回应了（200 + completion tokens），只是这次 256-token 探测预算
  被推理过程（`reasoning_content`）占满、没产出正文；端点与凭据本身是通的。
  `http_status` 仍是 200 且 `http_status_source="upstream"`，前端按**成功 + 说明**展示
  （结果面板标签为「说明」而非「错误信息」），不要按失败渲染。
  若整条故障转移链都被尝试过（例如唯一的模型返回 5xx），路由器抛出 `AllModelsFailedError`：
  此时 `http_status` 为 `null`、`http_status_source` 为 `""`，上游细节（含真实状态码文本）保留在 `message` 里。
测试是**诊断请求**：不写 Memory / Conversation / Experience，也不改 Relationship。
- AI 健康状态由后端推导（`ready / degraded / unavailable / not_configured`），同时出现在本端点与 `GET /api/v1/overview` 的 `ai.status`；前端不得自行猜测状态。
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
| `PATCH /api/v1/character/state` | 更新状态字段（Body 允许 `mood/energy/activity/current_focus/location/social_state/schedule_state/current_goal/current_project/reason`）；未知字段/类型错误 → `422 validation.failed`（带 `field`），成功返回新 state |
| `POST /api/v1/character/export` / `POST /api/v1/character/import` (`confirm`) | 导出/导入（在今日 export/import 基础上 JSON 化；导入返回 diff 预览，二次确认才应用） |
| `GET /api/v1/world` | 沙盒总览：phase、位置、当前动作+进度、modes、needs（含 bands）、goals、commitments（含 due）、relationships（top）、world/cognitive revision、session/interrupt；**W5 追加块**：`needs_full[]`、`spaces[]`、`objects[]`、`inventories{}`、`pet{}`、`social_spaces[]`、`action_defs[]`、`modes_defs[]`、`goals_full[]`；`action` 增加 `definition_id/detail/reason_code/space_id/goal_id/goal_step`；`interrupted` 由 bool 改为 `{active, definition_id, remaining_minutes, reason, progress}` 或 `null`（无打断时；`/overview` 的 `world.interrupted` 仍是 bool） |
| `GET /api/v1/world/trace?limit=` | 结构化事件轨迹（`sandbox.store.recent_events/timeline`） |
| `GET /api/v1/world/timeline?limit=` | 变更驱动事件时间线（同 `store.timeline` 粒度，升序）：`{enabled, items: [{ts, event_type, summary, location, action, revisions}], count}` |
| `GET /api/v1/world/topics?scope_key=&status=` | 未闭环话题列表：`{items: [TopicThread…], count}` |
| `POST /api/v1/world/topics/{topic_id}/{action}` | 话题动作 `resolve/forget/delete`；未知动作 → `400 topic.action_unknown`，话题不存在 → `404 topic.not_found`，动作未生效 → `409 topic.action_failed`；成功 `{done, action, topic_id}` |
| `POST /api/v1/world/control/{action}` | `pause / resume / reset / reinitialize`（沿用 `routes/sandbox.py:451-474`；`reset`/`reinitialize` 需 `confirm`） |
| `POST /api/v1/world/simulate` | 干跑 N 小时（沿用 `routes/sandbox.py:430-449` 的备份/恢复；需 `confirm`，单次限时） |

只读保证：`/world`、`/world/timeline`、`/world/topics`、`/social/*` 的 GET 均不移动
`sandbox.world_revision` / `cognitive_revision`（W5 测试断言）。

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

#### 7.2.1 W5 社交域具体接口（`app/web/api/social_api.py`）

| 方法与路径 | 请求 | 响应 `data` / 错误 |
|---|---|---|
| `GET /api/v1/social/users?q=&limit=≤200&offset=` | — | `{items: [{person_id, display_name, qq, nickname, nickname_override, interaction_count, stage, initiative_enabled, notes, tags, last_seen, relationship{…}\|null, open_commitments, recent_experience\|null, spaces[]}], total, limit, offset, next_cursor}`；`person_id` 来自 `PersonIdentityResolver.for_qq`（无沙盒时退化为 QQ id），绝不暴露 DB 主键 |
| `GET /api/v1/social/users/{person_id}` | — | `{person, relationship\|null, commitments[], experiences[]≤20, memories[]≤20\|null, spaces[]}`；未知人物 → `404 social.user_not_found` |
| `PATCH /api/v1/social/users/{person_id}` | `{nickname_override?, notes?, tags?: [str], initiative_enabled?: bool}` | 缺省字段保留现值；写 `AdminService.save_user_profile`；未知字段/类型错误 → `422`，未知人物 → `404 social.user_not_found`；返回 `{person}` |
| `GET /api/v1/social/groups` | — | `{items: [list_groups 行], count}` |
| `PATCH /api/v1/social/groups/{group_id}` | `{participation_enabled: bool}` | 写 `AdminService.set_group_participation`（与旧 `POST /groups/toggle` 同一写路径，upsert 语义）；非布尔 → `422`；返回更新后的群行（无群行时回显 `{group_id, participation_enabled}`） |
| `GET /api/v1/social/sessions` | — | `{active: bool, items: [{person_id, person_name, social_space_id, started_at, last_activity_at, turns, interrupted}]}`（运行期最多一条；`social_session_snapshot()` 返回副本） |
| `GET /api/v1/social/relationships?limit=≤200` | — | `{items: [{person_id, display_name, relation_type, trust, familiarity, closeness, social_comfort, interaction_count, last_interaction_at}], count}`（只读 `RelationshipStore.important`） |
| `GET /api/v1/social/commitments?status=&person=` | — | `{items: [{commitment_id, person_id, person_name, kind, status, strength, priority, summary, target_activity, time_hint, earliest_at, due_at, created_at, goal_id, goal_status}], count}`；open 在前、再按 `due_at`；未知 status → `400 social.status_unknown` |
| `GET /api/v1/social/commitments/{commitment_id}` | — | 同上行 + `{goal{…}\|null, action{…}\|null, outcome{status, resolved_at, updated_at, revision, result}\|null}`；未知 id → `404 social.commitment_not_found` |
| `GET /api/v1/social/spaces` | — | `{items: [{space_id, kind, qq_group_id, name, participants, character_presence, interest}], map: {qq_group_id: space_id}}`（map 来自 `sandbox.social_space_map`） |
| `GET /api/v1/social/overview` | — | `SocialAdminService.dashboard()` 薄转发（社交未启用时 `{enabled: false}`） |

社交域全部写操作需 CSRF；未登录一律 401。

### 7.3 记忆

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/memories?q=&mode=&scope_key=&category=&layer=&status=&person=&limit=≤200&offset=` | 有 `q` 时沿用检索语义（`mode=keyword/semantic/hybrid`；keyword 是 SQL LIKE，semantic/hybrid 走召回管线，`total` 为 `null`）；无 `q` 时为浏览（`mode="browse"`）。`person` 在未给 `scope_key` 时映射为 `scope_key="user:<person>"`；`status` 缺省 `active`，显式空串 = 全部。响应保持 W2 键并补分页：`{items, next_cursor, total(int\|null), count, mode, semantic_available, error}`（`next_cursor` 是下一页 `offset` 的字符串，最后一页 `null`） |
| `GET /api/v1/memories/timeline?limit=≤500&scope_key=` | 记忆时间线：`{items, count, scope_key}` |
| `GET /api/v1/memories/{id}` | 详情（出处/关联/时间线） |
| `POST /api/v1/memories/{id}/{action}` | `activate` / `archive` / `reembed` / `edit`（Body `{content, category?, importance?, status?, summary?}`，缺 `content` → `400 memory.content_required`）/ **`delete` 必须带 `{"confirm": "delete"}`**，否则 `409 memory.confirm_required`（W5 变更，防误删）；目标不存在 → `404 memory.not_found` |
| `GET /api/v1/memories/health` | 计数/向量覆盖/保留 |
| `POST /api/v1/memories/consolidation/run` | 手动巩固 |
| `POST /api/v1/memories/embeddings/{action}` | rebuild/retry/clear-cache |
| `POST /api/v1/memories/correction/plan` / `POST /api/v1/memories/correction/apply` | 自然语言纠错（两步：计划 → `confirm` 应用） |

### 7.4 媒体与能力（W5 实施）

错误码：`tools.*`（`tools.unknown` / `tools.confirm_required` / `tools.unknown_field` /
`tools.nothing_to_update` / `tools.scope_unknown` / `tools.store_unavailable`）、
`media.*`（`media.action_unknown` / `media.confirm_required` / `media.sticker_unknown` /
`media.pattern_unknown` / `media.unavailable`）、`agent.*`（`agent.task_unknown` /
`agent.action_unknown` / `agent.action_failed`）。未登录一律 401；写操作缺 CSRF → 403 `auth.csrf`。

#### 工具（`app/web/api/tools_api.py`，委托 `ToolAdminService`）

| 方法与路径 | 说明 | 请求 / 查询 | 响应 `data` |
|---|---|---|---|
| `GET /api/v1/tools` | 注册表 + 策略 + 统计一屏 | — | `{items: [{name, display_name, description, category, risk_level, enabled, requires_credentials, has_credential, timeout, cache_ttl_seconds, calls, failures, last_used_at}], policy: {allowed_risk_levels, rate_limit, max_calls_per_turn, ...}, stats: {enabled, decision_mode, total, enabled_count, disabled_count, calls, success, failure, cache_hits}}`。`has_credential`：无凭据要求时为 `null`（不适用），否则「全部就绪」才 `true`；缺失数据一律 `null` |
| `GET /api/v1/tools/{name}` | 详情：schema/文档/设置/指标/最近执行/权限摘要 | — | 单项 + `input_schema` / `output_schema` / `when_to_use` / `when_not_to_use` / `limitations` / `settings`（凭据只回 masked）/ `metrics` / `recent_executions` / `permissions_summary: {count, rules}`；未知工具 404 `tools.unknown` |
| `PATCH /api/v1/tools/{name}` | 热更新启用/设置/超时/缓存 TTL | `{enabled?, settings?, timeout?, cache_ttl_seconds?}` | 详情 + `applied: [...]` + `restart_required`。ToolRuntime 对以上字段全部热应用（v0.6 §73），故事实值为 `false`；未知字段 400 `tools.unknown_field`，空 body 400 `tools.nothing_to_update`，类型错误 400（带 `field`） |
| `POST /api/v1/tools/{name}/test` | 管理端测试（可能真的发起外部请求；不会发 QQ 消息）。干跑按 **SYSTEM 回合**处理：LOW Minecraft 动作会被意图门拒绝，`note` 里说明原因 | `{"arguments"?: {...}, "confirm": "<name>"}` | `{ok, result\|error, duration_ms, may_have_called_external: true, note}`。缺/错 `confirm` → 409 `tools.confirm_required`（**确认门 1**） |
| `GET /api/v1/tools/executions?limit=&name=` | 执行记录（永不回显原始参数） | `limit` ≤500 | `{items, total}` |
| `GET /api/v1/tools/metrics` | 执行器指标 | — | `ToolExecutor.metrics()` 原样 |
| `GET /api/v1/tools/permissions` | 权限规则 | — | `{items: [{scope, ref, tool_name, allowed, created_at}], total}` |
| `PUT /api/v1/tools/permissions` | 新增/更新规则（拒绝优先） | `{scope: "user"\|"group", scope_id, tool, allowed}` | `{saved: true, scope, scope_id, tool, allowed}`；`scope` 非法 400 `tools.scope_unknown`，未知工具 404 |
| `DELETE /api/v1/tools/permissions?scope=&scope_id=&tool=` | 删除规则 | query 三参必填 | `{cleared: true, ...}` |
| `POST /api/v1/tools/cache/clear` | 清空缓存 | `{"confirm": "clear", "name"?: "..."}` | `{cleared: <int>, tool}`；缺 `confirm` → 409 `tools.confirm_required`（**确认门 2**） |
| `GET /api/v1/tools/decision-debug?text=&mode=` | 选择打分预览，**只读、绝不执行** | `text` 必填 | `{query, candidates, rejected, selected, decision_mode, instruction_preview}`；缺 `text` 400 |

#### 贴纸与口癖（`app/web/api/media_api.py`）

| 方法与路径 | 说明 | 请求 / 查询 | 响应 `data` |
|---|---|---|---|
| `GET /api/v1/stickers?q=&emotion=&intent=&status=&limit=&offset=` | 贴纸库（服务端过滤 + 分页） | `limit` ≤500 | `{items: [{sticker_id, file, file_name, preview_url, emotion, emotion_tags, intent, intent_tags, status, origin, origin_user, usage_count, last_used_at, created_at, safety_status, valid}], stats, total}`。`preview_url` 无真实静态路由 → 恒 `null`；`valid` = 文件引用可解析 |
| `POST /api/v1/stickers/{sticker_id}/{action}` | `enable`→active / `disable`→disabled / `delete`→archived | delete 需 `{"confirm": "delete"}` | `{sticker_id, action, status, applied: true}`；未知 id 404 `media.sticker_unknown`，未知动作 400，缺确认 409 `media.confirm_required` |
| `POST /api/v1/stickers/reindex` | 全量重扫贴纸目录 | — | `{scanned, added, updated, removed, state}`；`scan()` 无法区分新增/重扫 → `added`/`updated` 为 `null`，`removed` 恒 0（扫描从不删除记录） |
| `GET /api/v1/expressions?status=&group_id=&limit=` | 口癖列表 | `limit` ≤500 | `{items: [{pattern_id, pattern, kind, status, group_id, scope_key, occurrences, speakers, use_count, first_seen, last_seen}], stats}` |
| `POST /api/v1/expressions/{pattern_id}/{action}` | `enable`→active / `disable`→disabled / `delete`（删来源+向量） | delete 需 `{"confirm": "delete"}` | `{pattern_id, action, status}` / `{..., deleted: true}`；未知 id 404 `media.pattern_unknown`，缺确认 409 `media.confirm_required` |

#### Agent（`app/web/api/agent_api.py`，委托 `AgentAdminService`）

| 方法与路径 | 说明 | 请求 / 查询 | 响应 `data` |
|---|---|---|---|
| `GET /api/v1/agent` | 面板一屏 | — | `{status, health, active_tasks, recent_tasks, policy, budget, planner_model, evaluator_model}`。`status` 由真实运行时推导：无 `bot.agent` → `unavailable`；`agent.enabled=false` → `disabled`；否则 `ready`（不猜） |
| `GET /api/v1/agent/tasks?status=&limit=&offset=` | 任务列表 | `limit` ≤500 | `{items, total}`；`total` 只在取完整个窗口时给出，否则 `null` |
| `GET /api/v1/agent/tasks/{task_id}` | 任务详情（计划/步骤/观察/trace，JSON 字段已解码） | — | `{task, goal, plans, steps, observations, traces}`；未知 404 `agent.task_unknown` |
| `POST /api/v1/agent/tasks/{task_id}/{action}` | `pause / resume / cancel / retry / replay` | — | `{task_id, action, ok, detail, ...}`；未知动作 400 `agent.action_unknown`，未知任务 404 `agent.task_unknown`，状态机拒绝 409 `agent.action_failed` |
| `POST /api/v1/agent/simulate` | 干跑：只分类 + 规划，不执行工具、不发 QQ 消息 | `{"text": "..."}` | `runtime.simulate()` 原样 + `dry_run: true`；缺 `text` 400 |

#### 运行时与日志扩展（`runtime.py` + `services/read_model.py`，契约 §3 加强）

- `GET /api/v1/runtime`（及 `/runtime/status`、`/overview.runtime`）新增：
  - `process: {uptime_seconds, started_at, version, python}`（未启动时前两项 `null`）；
  - `onebot: {state, connected, self_id, last_event_at, received, accepted, deduped, dropped, self_ignored, responses, sent, failed, pending_outbound, busy, lanes: [{lane, pending, busy}]}`，
    来自 `OneBotGateway.stats()`（纯只读投影）；网关未启用时 `state="disabled"`、计数 `null`、`lanes=[]`；
  - `scheduler.last_report`、`hub.queue_size` 两个既有访问器的键补齐；
  - `database: {connected, size_bytes}`（`size_bytes` 仅在配置指向真实存在的 sqlite 文件时给出，否则 `null`）。
- `GET /api/v1/logs/tail?limit=&level=&q=&channel=`：`limit` 默认 400、上限 500；叙述行消息开头的
  `[channel] ` 前缀（`_PlainFilter` 注入）解析成显式 `channel` 字段并从消息中移除；`channel` 过滤在服务端
  完成（多取行再筛）。响应保持 `{items, file, truncated, parsed, total}`，`items[]` 每项含
  `{ts, level, logger, channel, message}`（非叙述行 `channel=null`）。

### 7.5 Minecraft 连接（Phase 1，`app/web/api/minecraft.py`）

Minecraft 连接层（Bridge runtime 为独立 Node.js 进程，见 `docs/MINECRAFT_PHASE1.md`）。
本节端点只调用 `MinecraftService`，不复制状态机逻辑。`minecraft.enabled=false`（或 `bot.minecraft` 缺失）时：
`GET /minecraft` 恒为 200（返回下方投影、`enabled: false`——「未启用」是功能状态不是错误）；
`POST /join`、`POST /leave` 返回 503 `minecraft.disabled`。

| 方法与路径 | 说明 | 请求 / 查询 | 响应 `data` |
|---|---|---|---|
| `GET /api/v1/minecraft` | 连接层一屏投影 | — | `{enabled, auth_configured, runtime: {running, pid, managed, restarts, down, log_tail: []}, connection: {status, session_id, host, port, username, auth_mode, dimension, position: {x,y,z}, health, last_error, kicked_reason, connected_at}, action: {action, action_id, status, started_at, finished_at, elapsed_ms, result, error, code}, pathfinder: {goal, target:{username?|x,y,z}|null, distance, moving}, agent, last_event}`；runtime 不可达时以本地镜像降级呈现 |
| `POST /api/v1/minecraft/join` | 加入服务器（进世界由事件异步确认） | `{"host": "...", "port": 25565}`（port 省略=25565） | `{"session_id", "status"}`（`CONNECTING`/`AUTHENTICATING`）；校验失败 422 `minecraft.invalid_target`，已有会话 409 `minecraft.session_active`，runtime 不可用 503 `minecraft.runtime_down` |
| `POST /api/v1/minecraft/leave` | 主动离开（幂等：不在任何服务器也成功） | — | `{"ok", "status"}`；状态最终由事件流确认 |
| `POST /api/v1/minecraft/look_at` | **Phase 3B SAFE 动作**：让罐头看向世界坐标（不改世界、不移动；yaw/pitch 数学在 runtime） | `{"x": 120, "y": 65, "z": -230}` | `{action_id, action:"look_at", status}`；status ∈ `SUCCEEDED`/`TIMEOUT`/`CANCELLED`；非法坐标 422 `minecraft.action_invalid`，不在世界 409 `minecraft.not_connected`，前台忙 409 `minecraft.action_busy`，执行失败 500 `minecraft.action_failed` |
| `POST /api/v1/minecraft/stop` | **Phase 3B 安全停止**（幂等、最高优先级）：取消进行中动作并清空移动控制位 | — | `{status:"IDLE", cancelled:[action_id…]}`；runtime 不可达也返回成功 |
| `POST /api/v1/minecraft/move_to` | **Phase 3C 非破坏性导航**（LOW；禁止挖/放/搭桥；Stop 可取消）。**Phase 3E 起为持续型动作**——启动即返回 `RUNNING`，终点/失败经事件送达（与 follow_player 同语义） | `{"x": 120, "y": 64, "z": -230}` | `{action_id, action:"move_to", status:"RUNNING"}`；坐标/距离非法 422 `minecraft.action_invalid`（距当前位置 > `minecraft.action.move_to.max_distance`，默认 64），启动阶段不可达 500 `minecraft.path_not_found`，前台忙 409 `minecraft.action_busy`；到达后见 `action.result{distance_to_target…}`，无路径转为 `minecraft.action.failed`（`code=path.not_found`），超时/取消为 `TIMEOUT`/`CANCELLED` 事件 |
| `POST /api/v1/minecraft/follow_player` | **Phase 3D 动态跟随**（LOW；GoalFollow + dynamic；持续型动作——**启动即返回 RUNNING**，终态经事件/状态呈现；STOP/timeout/disconnect 都会真停） | `{"username": "空凛", "distance": 2.5}`（distance 1.5~6，缺省 2.5） | `{action_id, action:"follow_player", status:"RUNNING"}`；启动失败：找不到玩家 404 `minecraft.player_not_found`，离线 409 `minecraft.not_connected`，参数非法 422 `minecraft.action_invalid`，前台忙 409 `minecraft.action_busy`；运行中失败经 `minecraft.action.failed` 事件带 `player_lost` / `follow.target_too_far` |
| `GET /api/v1/minecraft/world` | World Debug 只读视图（Phase 2）：Semantic World Model + raw snapshot + 分层缓存元信息 | — | `{available, online, captured_at, age_seconds, layers: {near/local/extended: {age_seconds}}, semantic, raw}`；未启用/未在线恒 200 且 `available:false`（读端点不做 503） |
| `POST /api/v1/minecraft/events` | **Bridge runtime 事件回调**（服务间通道，不是给浏览器的） | 事件载荷（`minecraft.connecting|connected|spawned|chat|player_joined|player_left|kicked|disconnected|error` + `session_id` + `timestamp` + 上下文） | `{"accepted": true}` |

`GET /minecraft` 的 `agent` 块（Phase 3E，只读，供连接页的 LLM Tool Debug 面板）：

```json
{
  "enabled": true,
  "context": {
    "online": true, "username": "Catodayo", "dimension": "minecraft:overworld",
    "position": {"x": 120.5, "y": 64.0, "z": -230.5}, "biome": "plains",
    "players": [{"name": "空凛", "distance": 6.4, "direction": "front_right"}],
    "current_action": {"action": "follow_player", "action_id": "act_…", "status": "RUNNING"},
    "last_action": {"action": "move_to", "action_id": "act_…", "status": "FAILED",
                    "code": "minecraft.path_not_found", "error": "…", "result": null, "at": 1730000000.0},
    "activity": "刚走到 (126, 64, -228)", "updated_at": 1730000000.0
  },
  "policy": {"enabled": true, "risk_flags": {"SAFE": true, "LOW": true, "MEDIUM": false, …},
             "registered": {"minecraft_move_to": "LOW", …}},
  "tools": [{"name": "minecraft_move_to", "risk": "LOW", "enabled": true,
             "allowed": false, "reason": "minecraft.action_busy"}]
}
```

`allowed` 表示「用户此刻明确要求时会不会被放行」（`reason` 是被拒的稳定错误码：
`minecraft.disabled` / `minecraft.offline` / `minecraft.action_busy` /
`tool.disabled` / `tool.unregistered`）。只读投影，不含任何执行入口。

`/minecraft/events` 安全模型：**免会话 Cookie、免 CSRF**（本机 runtime 进程没有浏览器会话），
改用启动时生成的共享密钥做 Bearer 认证（`Authorization: Bearer <token>`；豁免与校验点在
`WebServer._auth_middleware` + 处理器权威复检）。token 只存在于 CatooBot 进程内存与
runtime 子进程环境变量中，永不落盘、永不下发浏览器。未知事件名/坏载荷 → 400 `minecraft.bad_event`。

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
