# CatooBot WebUI v1.0 · W1 现状审计

> 本文件是 WebUI v1.0 全量重构的 **W1 审计产物**。所有结论均来自真实源码阅读（file:line 为证），
> 不根据文件名猜测行为。审计期间 **未修改任何 Core 行为、未删除旧 UI**。
> 审计基线 commit：`004fe0a`（Console World Log 变化驱动）。

审计覆盖：`app/web/**`（39 个 py 文件 / 8,877 行）、`app/config/settings.py`（配置矩阵见
`WEBUI_CONFIG_MATRIX.md`）、`app/ai/**`、`app/runtime/**`、`app/core/**`、`app/sandbox/**` 的只读投影面、
`app/integrations/onebot/**`、`app/utils/{logger,narrator}.py`。

---

## 1. 技术形态事实（v1.0 的起点）

| 事实 | 证据 |
|---|---|
| 后端：aiohttp，`WebServer` 多继承 13 个 route mixin | `app/web/server.py:90-129` |
| 前端：**无框架、无构建步骤**；CSS/JS 是 Python 内联字符串 | `app/web/ui.py:433-723`（CSS+JS）、`ui.py:773-838`（shell 内联注入） |
| 无 `package.json` / `vite` / `node_modules` / 任何 `.js/.css` 静态文件 | 全仓只在 `.venv` 内找到 JS 产物 |
| 无 `add_static`：不存在静态目录服务 | 全仓 grep `add_static` = 0 |
| 模板：纯 f-string 拼接，无 Jinja2（无依赖） | `pyproject.toml` 无 jinja；`app/web/**` 无 `render_template` |
| 路由规模：13 组注册函数 + `/login`/`/logout`，快照测试覆盖 103 个 method/93 个 path | `app/web/server.py:112-129`；`tests/test_web_routes.py:14-118` |
| 会话：内存 dict，token `secrets.token_urlsafe(32)`，TTL 12h；重启即失效 | `app/web/auth.py:22,60,139-142` |
| 密码：PBKDF2-HMAC-SHA256 / 120k 迭代 | `app/web/auth.py:23-31` |
| Cookie：`catoobot_session`，httponly，samesite=Lax，无 Secure | `app/web/server.py:205` |
| CSRF：`sha256("catoobot-csrf:"+session_token)[:32]`，无状态；表单自动注入，POST 校验 | `app/web/security.py:28-34,49-54,67-95`；`server.py:158-170` |
| 登录限速：5 次失败 / 300s / (remote|username) | `app/web/security.py:98-158`；`server.py:194-198` |
| 密钥脱敏：日志 regex filter；叙述行经 `redact()` 后才进 WS；凭据 `mask()` | `app/utils/logger.py:56-80,182-197`；`app/web/realtime.py:127`；`app/tools/credentials.py:61-71` |
| 版本串三处不一致：`app/main.py:15 "v2.0"` / `app/web/ui.py:43 "v0.8"` / `pyproject.toml 2.0.0` | 同左 |
| 主题/语言：Cookie 驱动（mode/theme/nav/lang），i18n 字典仅覆盖部分词条 | `app/web/ui.py:27-30`；`app/web/i18n.py:15-73` |

| 文件规模 Top（迁移成本集中处） | 行数 |
|---|---|
| `app/web/ui.py` | 838 |
| `app/web/routes/memory.py` | 542 |
| `app/web/routes/sandbox.py` | 523 |
| `app/web/services/config_admin.py` | 518 |
| `app/web/pages/config_page.py` | 464 |
| `app/web/services/admin.py` | 372 |
| `app/web/routes/tools.py` | 356 |
| `app/web/routes/identity.py` | 340 |
| `app/web/pages/sandbox_page.py` | 316 |
| `app/web/services/behavior.py` | 313 |
| `app/web/pages/social_page.py` | 309 |
| `app/web/services/memory_correction.py` | 303 |

---

## 2. 页面清单（URL → 后端 → 服务 → 迁移建议）

图例：**仍用** = 有导航/页内链接/测试驱动的真实使用；**v1.0 去向** 使用八区：
`总览 / 角色 / AI与模型 / 社交 / 记忆 / 媒体与能力 / 系统 / 日志·Runtime`。
所有路由都要求会话 Cookie；`CSRF` 列标 `Y` 表示该写操作要求 `csrf_token`（表单字段或 `X-CSRF-Token` 头，
`app/web/security.py:28-34`）。

### 2.1 认证与框架级

| URL (方法) | 名称 | 后端实现 | 主要功能 | 仍用 | 重复 | v1.0 去向 |
|---|---|---|---|---|---|---|
| `GET /login` | 登录页 | `server.py:113` + `_login_page` | 登录表单 | 是（重定向目标） | — | 系统（重设计） |
| `POST /login` | 登录提交 | `server.py:114` + `_login_submit` | 校验密码、发会话 | 是 | — | 系统（重设计） |
| `POST /logout` | 登出 | `server.py:115` + `_logout` | 销毁会话 | 是（顶栏表单 `ui.py:829`） | — | 系统 |
| `GET /ws/events` | 实时推送 | `routes/ops.py:128-162`（注册 `:205`） | WS：narration + status | 是（`/logs` 页 JS） | — | 日志·Runtime（**复用**） |

### 2.2 总览 / Runtime / 日志

| URL (方法) | 名称 | 后端实现 | 服务 | 主要功能 | 仍用 | 重复 | v1.0 去向 |
|---|---|---|---|---|---|---|---|
| `GET /` | 仪表盘 | `routes/dashboard.py:20-153` | `AdminService.dashboard` | 状态瓦片、角色状态、模型表、指标 | 是（nav `ui.py:361`） | — | **总览**（重设计） |
| `GET /runtime` | 运行 | `routes/ops.py:202` | `AdminService.runtime_action` | 运行按钮、watchdog 统计 | 是（nav `ui.py:385`；dashboard 链 `:131`） | 与 `/logs` | 日志·Runtime |
| `POST /api/runtime/{action}` (CSRF Y) | 运行操作 | `routes/ops.py:203` | 同上 | reload_persona / restore_model_overrides / reload_plugins（HTML+meta refresh） | 是 | — | Runtime（JSON 化） |
| `GET /logs` | 日志 | `routes/ops.py:204` | `AdminService.logs` + WS | 等级/关键词过滤 + 实时流 | 是（nav `ui.py:379`） | 与 `/runtime` | 日志（重设计） |
| `GET /models` | 模型 | `routes/models.py:81` | `bot.ai.router.snapshot` + `UsageRecorder` | 路由状态 + 7 天用量表 | 是（nav `ui.py:376`） | 与 `/config?tab=ai` | **AI与模型**（合并） |
| `POST /api/models/override` (CSRF Y) | 模型热改 | `routes/models.py:82` | `AdminService.apply_model_override` | 启停/换 provider/换模型/优先级（302） | 是 | 与 `/config?tab=ai` | AI与模型（JSON 化） |

### 2.3 角色（Character / Sandbox / Conversation）

| URL (方法) | 名称 | 后端实现 | 服务 | 主要功能 | 仍用 | 重复 | v1.0 去向 |
|---|---|---|---|---|---|---|---|
| `GET/POST /character` (CSRF Y) | 角色 | `routes/identity.py:329-330` | `AdminService.persona` | 身份/性格/说话风格热改 | 是（nav `ui.py:362`） | 与 `/prompts` | 角色（重设计） |
| `POST /character/state` (CSRF Y) | 状态编辑 | `routes/identity.py:331` | `AdminService` | mood/energy/activity | 是（页内 `:68`） | 与 `/sandbox` | 角色（合并） |
| `GET /character/export` | 导出 | `routes/identity.py:332` | `AdminService.export_character` | 下载角色 JSON | 是 | — | 角色（JSON 化） |
| `POST /character/import` (CSRF Y) | 导入预览 | `routes/identity.py:333` | 同上 | 上传 JSON，仅预览 | 是 | — | 角色 |
| `POST /character/import/confirm` (CSRF Y) | 导入确认 | `routes/identity.py:334` | 同上 | 覆盖应用 | 是 | — | 角色（合并） |
| `GET/POST /sandbox/chat` (CSRF Y) | 私聊/群聊行为 | `routes/sandbox.py:510-513` | `BehaviorService` | 回复/分段/作息/主动卡片 | 是（tab `pages/sandbox_page.py:23`） | 与 `/social/policy`、`/groups` | 角色（合并入口） |
| `GET /topics` | 话题 | `routes/sandbox.py:515` | `BehaviorService` | 未闭环话题 | 是（nav `ui.py:370`） | 与 `/conversation/continuity` | 社交（保留） |
| `POST /api/topics/{action}/{id}` (CSRF Y) | 话题操作 | `routes/sandbox.py:516` | `BehaviorService.topic_action` | resolve/forget/delete（302） | 是 | — | 社交（JSON 化） |
| `GET /sandbox` | 沙盒总览 | `routes/sandbox.py:517` | `_sandbox_data` | 生活总览 | 是（nav `ui.py:365`） | — | 角色（重设计） |
| `GET /sandbox/inspectors` | 巡检器 | `routes/sandbox.py:518` | `_sandbox_data` | 宠物/空间/物件/库存 | 是（tab `:19`） | — | 角色（合并） |
| `GET /sandbox/needs` | 需求 | `routes/sandbox.py:519` | `_sandbox_data` | 需求/动作定义 | 是（tab `:20`） | — | 角色（合并） |
| `GET /sandbox/bible` | 档案 | `routes/sandbox.py:520` | `_sandbox_data` | 正典来源与覆盖 | 是（tab `:22`） | 与 `/character` | 角色（合并） |
| `GET /sandbox/trace` | 轨迹 | `routes/sandbox.py:521` | `sandbox.store` | 结构化回放轨迹 | 是（tab `:25`） | — | 角色（保留） |
| `POST /sandbox/simulate` (CSRF Y) | 试跑 | `routes/sandbox.py:522` | `SandboxRuntime` | 备份→干跑 N 小时→恢复 | **无 UI 调用**（仅测试） | — | 角色（接线或下线，见 §9 UNKNOWN） |
| `POST /api/sandbox/control` (CSRF Y) | 沙盒控制 | `routes/sandbox.py:523` | 直接调 `bot.sandbox`/`LifecycleManager` | pause/resume/reset/reinitialize（JSON） | **无 UI 调用**（仅测试） | — | 角色（作为 v1 JSON 起点） |
| `GET /conversation` | 对话 | `routes/prompts.py:167` | `ConversationRuntime` 只读 | 轮次运行期 + 过期轮次 | 是（nav `ui.py:363`） | 与 `/sessions` | 角色（保留） |
| `GET /conversation/continuity` | 延续 | `routes/prompts.py:168` | `ContinuityManager` | 未闭环/画像/共同经历 | 是（tab `conversation_page.py:31`） | 与 `/topics` | 角色（合并） |
| `GET /behavior` | 旧别名 | `routes/sandbox.py:509` | — | 302 → `/sandbox/chat` | 是（`config_page.py:198` 链接） | 纯别名 | 角色（删除别名） |

### 2.4 社交 / 用户 / 群组 / 会话 / 口癖

| URL (方法) | 名称 | 后端实现 | 服务 | 主要功能 | 仍用 | 重复 | v1.0 去向 |
|---|---|---|---|---|---|---|---|
| `GET /social` | 社交总览 | `routes/social.py:126` | `SocialAdminService` | 社交认知 + 回复反馈 | 是（nav `ui.py:382`） | — | 社交（重设计） |
| `GET /social/observations` | 观察 | `routes/social.py:127` | 同上 | 结构化决策日志 | 是（tab） | — | 社交（合并） |
| `GET /social/group` | 群参与 | `routes/social.py:128` | 同上 | 实时群参与快照 | 是（tab+表单） | 与 `/groups` | 社交（合并） |
| `GET /social/simulator` | 模拟器 | `routes/social.py:129` | 同上 | 不发送的 analyze | 是（tab） | 与 `/behavior/preview` | 社交（合并） |
| `POST /social/analyze` (CSRF Y) | 立即分析 | `routes/social.py:130` | 同上 | 分析后重定向 | 是 | — | 社交（JSON 化） |
| `POST /social/replay` (CSRF Y) | 回放 | `routes/social.py:131` | 同上 | 与分析同实现 | **无 UI 调用** | **与 analyze 重复** | 社交（删除或并入 analyze） |
| `GET/POST /social/policy` (CSRF Y) | 社交策略 | `routes/social.py:132-133` | `SocialAdminService.save_settings` | 阈值/频率上限 | 是（tab；`sandbox.py:124` 链接） | 与 `/sandbox/chat` | 社交（合并） |
| `GET/POST /users` (CSRF Y) | 用户 | `routes/identity.py:335-336` | `AdminService` | 用户画像/阶段/主动开关 | 是（nav `ui.py:373`） | 与 `/sandbox/chat` 主动卡 | 社交（重设计） |
| `GET /groups` | 群组 | `routes/identity.py:337` | `AdminService` | 群列表 + 参与开关 | 是（nav `ui.py:374`） | 与 `/sandbox/chat` | 社交（合并） |
| `POST /groups/toggle` (CSRF Y) | 群开关 | `routes/identity.py:338` | `AdminService` | 切换参与 | 是 | — | 社交（JSON 化） |
| `GET /sessions` | 会话 | `routes/identity.py:339` | `AdminService` | 逐会话上下文 | 是（nav `ui.py:375`） | 与 `/conversation` | 角色（合并） |
| `POST /api/sessions/clear` (CSRF Y) | 清会话 | `routes/identity.py:340` | `AdminService.clear_session` | 清单个会话上下文 | 是 | — | 角色（JSON 化） |
| `GET /expressions` | 口癖 | `routes/expressions.py:84` | `ExpressionAdminService` | 已学口癖 + 出处 | 是（nav `ui.py:372`） | 与 `/memory` | 社交（保留） |
| `POST /expressions/toggle` (CSRF Y) | 口癖开关 | `routes/expressions.py:85` | 同上 | 启停 | 是 | — | 社交（JSON 化） |
| `POST /expressions/delete` (CSRF Y) | 口癖删除 | `routes/expressions.py:86` | 同上 | 删除 + 样本 | 是 | — | 社交（JSON 化） |

### 2.5 记忆

| URL (方法) | 名称 | 后端实现 | 服务 | 主要功能 | 仍用 | 重复 | v1.0 去向 |
|---|---|---|---|---|---|---|---|
| `GET /memory` | 记忆浏览 | `routes/memory.py:527` | `MemoryAdminService` | 过滤浏览 | 是（nav `ui.py:371`） | — | 记忆（重设计） |
| `GET/POST /memory/search` | 检索 | `routes/memory.py:528-529` | 同上 | 混合/语义/关键词 | 是 | 与 `/memory/retrieval-debug` | 记忆（合并） |
| `GET /memory/timeline` | 时间线 | `routes/memory.py:530` | 同上 | 成长时间线（含被取代） | 是 | — | 记忆（合并） |
| `GET /memory/health` | 健康 | `routes/memory.py:531` | 同上 | 计数/向量覆盖/保留 | 是 | 与 `/memory/embeddings` | 记忆（保留） |
| `GET/POST /memory/retrieval-debug` | 检索调试 | `routes/memory.py:532-533` | 同上 | 逐条打分拆解 | 是 | 与 `/memory/search` | 记忆（合并·专家） |
| `GET /memory/embeddings` | 向量 | `routes/memory.py:534` | 同上 | 状态 + 操作 | 是 | 与 `/memory/health` | 记忆（合并） |
| `POST /memory/embeddings/{action}` (CSRF Y) | 向量操作 | `routes/memory.py:535` | 同上 | rebuild/retry/clear-cache | 是 | — | 记忆（JSON 化） |
| `GET /memory/consolidation` | 巩固 | `routes/memory.py:536` | 同上 | 状态 + 手动跑 | 是 | — | 记忆（合并） |
| `POST /memory/consolidation/run` (CSRF Y) | 巩固执行 | `routes/memory.py:537` | 同上 | 按范围跑巩固 | 是 | — | 记忆（JSON 化） |
| `GET /memory/detail/{id}` | 记忆详情 | `routes/memory.py:538` | 同上 | 出处/关联/操作 | 是 | — | 记忆（保留） |
| `POST /api/memory/{action}/{id}` (CSRF Y) | 记忆操作 | `routes/memory.py:539` | `MemoryAdminService.action` | 激活/归档/重嵌入/删除（JSON） | 是 | — | 记忆（保留，**v1 JSON 起点**） |
| `GET/POST /memory/correction` (CSRF Y) | 自然语言纠错 | `routes/memory.py:540-541` | `MemoryCorrectionService` | 生成纠错计划 | 是（tab `memory.py:517`；GET 生成计划） | — | 记忆（合并；POST 路径未用） |
| `POST /memory/correction/apply` (CSRF Y) | 纠错应用 | `routes/memory.py:542` | 同上 | 应用确认的计划 | 是 | — | 记忆（JSON 化） |

### 2.6 媒体与能力（贴纸 / 工具 / Agent / 凭据）

| URL (方法) | 名称 | 后端实现 | 服务 | 主要功能 | 仍用 | 重复 | v1.0 去向 |
|---|---|---|---|---|---|---|---|
| `GET /stickers` | 表情 | `routes/media.py:168` | `StickerAdminService` | 表情库 + 过滤 | 是（nav `ui.py:383`） | — | 媒体与能力（保留） |
| `POST /api/stickers/{action}/{id}` (CSRF Y) | 表情操作 | `routes/media.py:169` | 同上 | 启停/删除（302） | 是 | — | 媒体与能力（JSON 化） |
| `POST /api/stickers/reindex` (CSRF Y) | 重扫 | `routes/media.py:170` | 同上 | 重扫目录 | 是 | — | 媒体与能力（JSON 化） |
| `GET /credentials` | 凭据 | `routes/media.py:171` | `ToolAdminService` | 脱敏凭据列表 + 新增 | 是（nav `ui.py:384`） | 与 `/config?tab=onebot`、AI env | 系统·凭据（重设计） |
| `POST /api/credentials` (CSRF Y) | 写凭据 | `routes/media.py:172` | 同上 | 写 secret（永不回显） | 是 | — | 系统·凭据（JSON 化） |
| `POST /api/credentials/delete` (CSRF Y) | 删凭据 | `routes/media.py:173` | 同上 | 删除 secret | 是 | — | 系统·凭据（JSON 化） |
| `GET /tools` | 工具 | `routes/tools.py:344` | `ToolAdminService` | 注册表/策略/近期执行 | 是（nav `ui.py:380`） | — | 媒体与能力（保留） |
| `GET /tools/{name}` | 工具详情 | `routes/tools.py:345` | 同上 | 配置/测试/schema/指标 | 是 | — | 媒体与能力 |
| `GET /tools/executions` | 执行日志 | `routes/tools.py:346` | 同上 | 全量执行记录 | 是 | — | 媒体与能力（合并） |
| `GET /tools/metrics` | 工具指标 | `routes/tools.py:347` | 同上 | 原始指标 JSON 倾倒 | 是 | 与 `/tools` | 媒体与能力（合并） |
| `GET/POST /tools/decision-debug` (CSRF Y) | 选择调试 | `routes/tools.py:348-349` | 同上 | 工具选择打分预览 | 是 | 与 `/agent/simulator` | 媒体与能力（保留·专家） |
| `GET /tools/permissions` | 工具权限 | `routes/tools.py:350` | 同上 | 用户/群 allow-deny | 是 | — | 媒体与能力 |
| `POST /api/tools/toggle` 等 6 个 (CSRF Y) | 工具操作 | `routes/tools.py:351-356` | 同上 | 启停/配置/测试/清缓存/权限（302 或 HTML） | 是 | `/api/tools/config` 的凭据与 `/credentials` | 媒体与能力（JSON 化） |
| `GET /agent` | Agent | `routes/agent.py:267` | `AgentAdminService` | 指标 + 近期任务 | 是（nav `ui.py:381`） | — | 媒体与能力（保留） |
| `GET /agent/tasks` | 任务列表 | `routes/agent.py:268` | 同上 | 状态过滤 | 是 | — | 媒体与能力（合并） |
| `GET /agent/tasks/{id}` | 任务详情 | `routes/agent.py:269` | 同上 | 计划/步骤/观察/轨迹 | 是 | — | 媒体与能力（保留） |
| `GET/POST /agent/simulator` (CSRF Y) | 模拟器 | `routes/agent.py:270-271` | 同上 | 分类/计划（+dry run） | 是 | 与 `/tools/decision-debug` | 媒体与能力（保留） |
| `GET /agent/policy` | 策略 | `routes/agent.py:272` | 同上 | 只读策略倾倒 | 是 | 与 `/config agent` | 媒体与能力（合并） |
| `GET /agent/metrics` | 指标 | `routes/agent.py:273` | 同上 | 原始指标倾倒 | 是 | 与 `/agent` | 媒体与能力（合并） |
| `POST /api/agent/control` (CSRF Y) | 任务控制 | `routes/agent.py:274` | `AgentAdminService.control` | pause/resume/cancel/retry/replay（HTML） | 是 | — | 媒体与能力（JSON 化） |

### 2.7 系统（配置 / 提示词）

| URL (方法) | 名称 | 后端实现 | 服务 | 主要功能 | 仍用 | 重复 | v1.0 去向 |
|---|---|---|---|---|---|---|---|
| `GET /config` | 配置中心 | `routes/config.py:89` | `ConfigAdminService` | 5 个 tab（basic/ai/onebot/web/raw） | 是（nav `ui.py:377`） | — | 系统（重设计） |
| `POST /config/basic` (CSRF Y) | 基础保存 | `routes/config.py:90` | 同上 | 名称/日志/权限/记忆 | 是 | — | 系统（JSON 化） |
| `POST /config/ai` (CSRF Y) | AI 保存 | `routes/config.py:91` | 同上 | Provider/模型/冷却 | 是 | 与 `/models` | **AI与模型**（JSON 化） |
| `POST /config/onebot` (CSRF Y) | QQ 保存 | `routes/config.py:92` | 同上 | host/port/token（写 .env） | 是 | 与 `/credentials` | 系统·QQ（JSON 化） |
| `POST /config/web` (CSRF Y) | WebUI 保存 | `routes/config.py:93` | 同上 | 用户名/密码（PBKDF2 存 DB） | 是 | — | 系统·WebUI（JSON 化） |
| `POST /config/raw` (CSRF Y) | 高级 YAML | `routes/config.py:94` | 同上 | 编辑 overrides.yaml（唯一途径触碰角色键/背压/并发等） | 是 | — | 系统·高级（保留） |
| `POST /config/reset` (CSRF Y) | 清覆盖 | `routes/config.py:95` | 同上 | 清空 overrides | 是 | — | 系统（JSON 化） |
| `POST /config/test-provider` (CSRF Y) | 连通性 | `routes/config.py:96` | 同上 | 探测 provider | **无按钮**（仅路由存在） | — | AI与模型（接线） |
| `GET/POST /prompts` (CSRF Y) | 提示词 | `routes/prompts.py:165-166` | `AdminService.prompt_overrides` | persona/记忆提取 prompt 覆盖 | 是（nav `ui.py:378`） | 与 `/character` | AI与模型（合并） |

### 2.8 导航结构现状

- 定义：`app/web/ui.py:360-386`（`NAV`）+ `:389-406`（`NAV_GROUPS`）+ `:409-427`（`nav_html`）+ `:791-838`（shell）。
- 分组：**概览**（仪表盘/运行/日志）、**角色**（角色/沙盒/对话/话题）、**数据**（记忆/口癖/用户/群组/会话）、
  **能力**（模型/配置/提示词/凭据/工具/Agent/社交/表情）——共 4 组 20 项。
- 页内 tab：记忆 8 个（`routes/memory.py:512-524`）、沙盒 6 个（`pages/sandbox_page.py:17-30`）、
  社交 5 个（`pages/social_page.py:22-33`）、对话 2 个（`pages/conversation_page.py:27-35`）、
  配置 5 个（`pages/config_page.py:14-20`）。
- 已发现的现状瑕疵：`/credentials` 渲染时高亮的是「工具」（`routes/media.py:153` 传 `active="/tools"`）。

---

## 3. JSON / API 现状（v1.0 后端 API 的起点）

真正返回 JSON 的只有 3 处：

| 路径 | 返回（真实键） | 证据 |
|---|---|---|
| `POST /api/memory/{action}/{memory_id}` | `{"ok": bool}` | `routes/memory.py:451` |
| `POST /api/sandbox/control` | `{"ok", "reason"?, "phase"?, "report"?}`，接受 JSON body `{action, confirm}` | `routes/sandbox.py:458-474` |
| `GET /ws/events` | WS 帧 `{"topic","ts","data"}`（hello/narration/status） | `realtime.py:99-129` |

其余所有 `/api/*` 名义接口实际是 **表单 POST → 302/HTML**（`/api/runtime/*`、`/api/models/override`、
`/api/tools/*`、`/api/credentials*`、`/api/stickers/*`、`/api/sessions/clear`、`/api/topics/*`、`/api/agent/control`），
见 §2 各行证据。→ v1.0 必须系统性 JSON 化，而不是新增平行接口。

服务层（`app/web/services/**`，全部在 `WebServer.__init__` 构造 `server.py:90-99`；路由从不直接碰 Core）：

| 服务 | 域 | 读写 |
|---|---|---|
| `AdminService`（`services/admin.py:25`，372 行） | 仪表盘/live_status、角色状态、用户/群/会话、记忆动作、模型覆盖、提示词、日志尾、运行时动作、角色导出导入 | 读写 |
| `BehaviorService`（`services/behavior.py:25`，313 行） | 行为面板（含 `scheduler.snapshot()`）、行为覆盖、回复预览/测试、心情触发、话题 | 读写 |
| `ConfigAdminService`（`services/config_admin.py:75`，518 行） | 配置快照/编辑/热应用、.env secret、provider 测试 | 写 |
| `MemoryAdminService`（`services/memory.py:19`） | 记忆浏览/检索/时间线/详情/动作、检索调试、健康、向量、巩固 | 读写 |
| `MemoryCorrectionService`（`services/memory_correction.py:56`） | 提示词驱动的记忆纠错 | 写 |
| `AgentAdminService`（`services/agent.py:18`） | Agent 面板/健康/任务/轨迹/模拟/控制 | 读写 |
| `SocialAdminService`（`services/social.py:21`） | 社交面板/观察/群上下文/回放/策略 | 读写 |
| `ExpressionAdminService`（`services/expressions.py:20`） | 口癖模式/样本/状态/删除 | 读写 |
| `StickerAdminService`（`services/media.py:16`） | 贴纸资产/统计/状态/重扫、凭据 | 读写 |
| `ToolAdminService`（`services/tools.py:25`） | 工具面板/执行/指标/开关/配置/测试/权限/凭据/缓存 | 读写 |

---

## 4. 实时机制现状（可复用资产）

| 项 | 事实 | 证据 |
|---|---|---|
| Hub | `RealtimeHub`，每订阅者一个 `asyncio.Queue`，容量 200，慢消费者丢**最旧** | `realtime.py:34,42-109` |
| 信封 | `{"topic": str, "ts": int, "data": payload}` | `realtime.py:82` |
| topic：`hello` | 连接即发 `hub.stats()` = subscribers/published/dropped/queue_size | `routes/ops.py:143-145` |
| topic：`narration` | `{channel, level, message}`，message 已 `redact()` | `realtime.py:112-138` |
| topic：`status` | `{online, sandbox, models[], metrics, watchdog, plugins}`（`AdminService.live_status`），每 5s | `realtime.py:37`；`routes/ops.py:173-187`；`services/admin.py:32-63` |
| 客户端 | 仅 `/logs` 页的原生 JS（narration 追加、status 展示、断线退避重连） | `routes/ops.py:22-63` |
| 认证 | 会话 Cookie；未认证升级返回 401 | `server.py:155-157` |

**今天不承载**（v1.0 需新增 topic 或新端点）：世界内部状态（仅一行 `sandbox` 字符串）、
needs/goals/commitments/relationships/memories/experiences、RuntimeScheduler 的 ticks/last tick、
OneBot 队列与最后事件时间、会话/打断状态、日志历史（只有 400 行轮询尾读 `services/admin.py:301-326`）、
客户端→服务端命令通道。

日志与叙述分类（v1.0 前端图例需要）：`boot 🚀 / sense 📨 / vision 👁 / facts 📎 / reason 💭 / audit 🛡 /
judge 🚪 / think 🧠 / flow 🎐 / reply 💬 / mind 🫧 / world 🌍 / tool 🔧 / task 🧩 / reach 📣 / quiet 🌙 / warn ⚠️`
（`app/utils/narrator.py:27-45`；`panel`/`blank` 为旁路伪频道 `:83-92`）。

---

## 5. v1.0 需要补齐的运行时只读数据（Dashboard/角色页用）

| 数据点 | 现有访问器 | 状态 |
|---|---|---|
| QQ 在线 / self_id / 收消息计数 | `bot.is_connected`（`core/bot.py:324`）；`bot.self_id`（`:319`）；`metrics.snapshot()["messages_received"]`（`core/metrics.py:24`） | 有 |
| 用户/群/会话计数 | `AdminService.dashboard()["users"/"groups"/"sessions"]`（`services/admin.py:80-84,87-103`） | 有 |
| 最后事件时间 | 无属性；最近似为 `users.last_seen`（`core/router.py:49`） | **缺**（需新只读方法） |
| AI 启用 / 模型健康 / 429 | `bot.ai.enabled`（`ai/engine.py:84`）；`bot.ai.router.snapshot()`（`ai/router.py:155-157`）；`metrics.snapshot()`（`metrics.py:25-28`） | 有（`live_status` 只透出子集） |
| token/延迟 7 天用量 | `UsageRecorder.summary()`（`ai/usage.py:93`） | 有，但无 service 方法（路由直查 `routes/models.py:36-40`） |
| 世界 phase / 位置 / 当前动作 / 进度 | `sandbox.phase`；`runtime.context()`（`sandbox/runtime.py:3162-3203`）；`current_action.progress`（`:765`） | 有（页面已用） |
| needs 带 / goals / commitments / 关系 / 记忆·经历计数 / 双 revision | `needs.bands()`（`sandbox/needs.py:74`）；`goals.open_goals()`（`goals.py:331`）；`commitments.open()`（`commitments.py:618`）+ `commitment_bridge.due()`（`:491`）；`relationships_dyn.important()`（`relations.py:539`）；`memory.count()`（`memory_foundation.py:669`）；`store.count_experiences()`（`store.py:511`）；`runtime.world_revision` / `cognitive_revision`（`:225,229`） | 访问器存在，**WebUI 服务层未读**（**缺**） |
| RuntimeScheduler：running/interval/ticks/last_tick | `bot.runtime_scheduler`（`runtime/scheduler.py:43-56,116`） | 无快照方法、无 WebUI 读（**缺**） |
| 行为调度器 ticks/errors | `scheduler.snapshot()`（`behavior/scheduler.py:316-323`）经 `BehaviorService.dashboard`（`services/behavior.py:36-38`） | 有 |
| 事件循环 watchdog | `watchdog.stats()`（`core/watchdog.py:160-173`） | 有（已在 `live_status`） |
| Hub 自身统计 | `hub.stats()`（`realtime.py:99-105`） | 有（仅 hello 帧） |
| DB 连接状态 / world lock / 社交会话状态 | `bot.database._conn`（`database/database.py:1226`，私有）；`runtime._world_lock`（`runtime.py:217`）；`runtime.social_session_active()`（`:850-865`，仅布尔公开） | **缺**（私有或无访问器） |

结论：v1.0 的最短路径是**扩展 `AdminService.live_status()` 并新增只读 JSON 端点**；上面标「有」的访问器均为
无副作用读取（`goals.open_goals()`/`commitments.open()` 只读内存管理器）。

---

## 6. 现有能力 vs v1.0 目标（差距清单）

| 目标能力 | 现状 | 证据 |
|---|---|---|
| 全新安装后不碰 YAML/.env 完成 AI 配置 | **不可**：WebUI 只能写 `api_key_env`（变量名），无任何 WebUI 路径写 AI Key；引擎只读 `os.environ` | `services/config_admin.py:339,347`；`ai/engine.py:106`；对照 `set_env_secret` 仅 OneBot 用（`config_admin.py:452`） |
| 设置/切换默认聊天模型 | **不可**：模型条目无 default/role 字段，纯靠列表顺序 | `config/settings.py:198-204`；`ai/router.py:251-257` |
| 角色模型绑定（vision/decision/planner/evaluator/embedding） | **不可**（无表单字段；仅 raw YAML）：`media.vision_model`、`sandbox.decision_model|conversation_model`、`agent.planner|evaluator.model`、`social.decision_model`、`memory.semantic.embedding.*` | `config/settings.py:618,628,724,728,470,550-559,263-272`；`config_admin.py:378-394` |
| 失败转移顺序持久化 | **部分**：`/models` 改 priority 即时生效但 `restore_model_overrides` 不重放 priority | `services/admin.py:245-253,259-266` |
| 从 UI 测试模型 | **部分**：`POST /config/test-provider` 存在但无按钮；仅测 provider 下第一个已配模型 | `routes/config.py:81-86`；`config_admin.py:477-499` |
| Provider 类型选择 | 硬编码单一选项 `openai_compatible`（注册表支持扩展但无发现机制） | `pages/config_page.py:228-231`；`ai/provider.py:35-56` |
| 重启/停止机器人 | **缺** | `app/web` 无 restart/exit |
| 手动跑世界 tick | **缺** | `sandbox/runtime.py:701`、`runtime/scheduler.py:101` 无 web 调用方 |
| 启动/停止沙盒 | **部分**：pause/resume/reset/reinitialize 有；start/stop 无 | `routes/sandbox.py:451-474`；`runtime.py:414,447-454` |
| 改 Runtime 调度间隔并热应用 | **缺**：字段存在但无 UI；调度器在构造时捕获 config | `config/settings.py:762`；`core/bot.py:293-295`；`config_admin.py:219` |
| 看世界调度 ticks | **部分**：行为调度可见，世界调度不可见 | `services/behavior.py:36-38` vs `runtime/scheduler.py:43-45` |
| 看打断/会话状态 | **缺**（`context()["interrupted"]` 存在但页面不渲染；`_social_session` 无读取方） | `sandbox/runtime.py:3202`；`pages/sandbox_page.py:39-73` |
| 批量清理记忆 | **缺**（仅按 id 删除） | `routes/memory.py:539` |
| 日志历史 | **部分**：400 行尾读 + 实时 narration，无分页/下载/过滤服务端化 | `services/admin.py:301-326` |
| 前端形态 | 服务端 HTML；v1.0 需 Vue 3 + TS + Vite（**不允许 CDN**） | §1 |

---

## 7. 安全与合规发现

1. **【已处置】真实角色 Bible 源文件曾被镜像到 GitHub**：`docs/bible_source_罐头.txt`（200 行，含姓名/生日/
   外貌/饮食等真实人设）存在于发布仓库 `CatooBot_github/docs/`（`git ls-files` 证实）。按本任务
   「Character Bible 不得上传 GitHub，只允许 `character_bible.example.*`」的硬约束，W1 已将其**移出镜像范围**：
   `docs/bible_source_罐头.txt` → `config/bible_source_罐头.txt`（`config/` 不在 `MIRRORED_DIRS`，
   `sync_github.py:37`），并同步更新 `docs/README.md` 的索引行与 `docs/V2.0_SANDBOX_PLAN.md` 的引用路径。
   **诚实边界**：该文件仍存在于私有仓库的历史 commit 中；本任务禁止改写历史（force push/rebase/filter-repo/BFG），
   故不做清除，历史仅私有可见。后续如需彻底清除，需要用户单独决策（重建仓库或 GitHub 支持的历史清理）。
2. 密钥卫生：AI Key 今天只有 `api_key_env` 进 YAML；`.env` 写入是**非原子** `path.write_text`
   （`config_admin.py:171`），无备份；`read_env_keys` 返回名字列表但**无调用方**（死代码，`config_admin.py:139-149`）。
   v1.0 凭据写入必须原子化 + 保留既有行 + 只回 masked 状态。
3. CSRF 对 SPA 不可用：token 由 httpOnly 会话 Cookie 派生（`security.py:49-54`），只注入服务端渲染表单，
   无任何端点/meta 暴露给 JS（`app/web` 内无 `fetch(`）。v1.0 必须新增会话引导端点（见 API 契约 §2）。
4. 无害遗留：`CSRF_EXEMPT_PATHS` 含不存在的 `/api/login`（`security.py:31`）；`/social/replay` 与 `/social/analyze`
   实现重复且前者无 UI 调用；`/behavior` 为纯别名 302。
5. 供应链：全部前端资产是内联字符串，无 CDN、无外部字体（`ui.py:433-723`）→ v1.0「不允许 CDN」的现状是达标的，
   Vite 产物必须本地服务（需新增 `add_static`）。

---

## 8. 迁移阻碍（写 UI 前必须解决的工程项）

| # | 阻碍 | 证据 | W2/W3 处置 |
|---|---|---|---|
| 1 | 业务逻辑混在 handler/page 中（直接查库、直接调 core） | `routes/prompts.py:58-90`；`routes/sandbox.py:430-474` | W2 抽 API 层，页面逻辑下沉 service |
| 2 | 只有 2 个 JSON 端点，其余 302/HTML | §3 | W2 统一 `/api/v1` JSON 化 |
| 3 | SPA 拿不到 CSRF token | `security.py:49-75` | W2 新增 `GET /api/v1/session`（含 csrf_token） |
| 4 | 无静态托管路径 | 全仓无 `add_static` | W3 增加 `/assets` 静态服务（本地 dist） |
| 5 | 服务端 HTML 生成耦合（`layout()` 注入 CSRF/主题） | `routes/base.py:60-74`；`ui.py:726-838` | W3 保留旧 shell 供 `/legacy`，v1 独立 |
| 6 | 巨型单文件（`ui.py` 838 行含 CSS+JS+组件+导航） | §1 规模表 | W3 前端组件化；旧文件不动（legacy 复用） |
| 7 | 版本串三处不一致 | §1 | W6 统一（`app/main.py` / `ui.py` / `pyproject.toml`） |
| 8 | 无权限分级（单用户） | `auth.py:88-96` | v1.0 维持单管理员，不做 RBAC（避免 Core 变更） |

---

## 9. UNKNOWN（需人工确认，未在本次审计中下结论）

- `POST /sandbox/simulate`、`POST /api/sandbox/control` 除测试外是否有外部脚本/插件调用：仓库内未发现调用方（grep）。
  处置建议：v1.0 将其接成正式 JSON 动作，或明确下线；在 W2 决策前保持原样。
- `CSRF_EXEMPT_PATHS` 中 `/api/login` 的意图（历史遗留或为旧前端预留）：`security.py:31`，无对应路由。
- `docs/specs/v0.1.md … v2.0.md`（13 个历史开发规格）当前随 `docs/` 镜像发布：属于用户既有决策，W1 未改动；
  如不希望公开，请在 W6 一并决定。

---

## 10. W1 结论

- 现状 WebUI 是「Python 内联渲染 + 表单 POST + 一个可复用的 WS」的单体后台，**没有可复用的前端资产**，
  但服务层（10 个 service，读多写少且与 Core 解耦）与 `/ws/events` 是 v1.0 的最大可复用面。
- v1.0 的规模瓶颈不在页面数量（93 路径），而在：**JSON API 层缺失、CSRF/静态托管缺口、AI 凭据与角色绑定缺口、
  运行时只读投影缺口**。四者都已在 `WEBUI_API_CONTRACT.md` 中给出契约。
- 本阶段未删除旧 UI、未改变任何 Core 行为；旧 WebUI 保持可运行，作为 W6 的回滚面（`/legacy`）。
