# CatooBot WebUI v1.0 · W6 审计（Final Integration / Cutover Audit）

> 基线：W5 `e6f49f01de901fdb29ac7c5fba1d8d3154a53e0f`。
> 本审计在**修改代码之前**建立问题清单，并把每条按 P0/P1/P2/P3 分类；
> 「W6 只修 P0/P1」，其余按要求记录。所有结论来自真实源码与真实运行（浏览器 E2E 见 §11）。

---

## 1. W5 baseline

`e6f49f01de901fdb29ac7c5fba1d8d3154a53e0f`（W1 `3478b0a` → W2 `1ba6f79` → W3 `2bebea9` → W4 `51cfaeef` → W5 `e6f49f0`）。

## 2. v1 前端路由

`webui/src/router/index.ts`：**44 条路由定义**，其中 `meta.requiresAuth === true` 的 **40 条**（数据驱动守卫测试逐条覆盖），`meta.guest` 仅 `/login`，catch-all → `not-found`。导航级测试遍历 24 条主要路径，38 个互不相同的标题。

## 3. `/api/v1` endpoint

服务端真实路由表 **205 条**：`/api/v1` **87 条**（其中写操作 **38 条**）+ legacy/SPA **118 条**（含 58 个旧页面、`/assets/{tail}`、`/ws/events`、`/legacy*` 与 SPA fallback）。

## 4. WebSocket topic

`hello` / `narration` / `log` / `status` / `world` / `scheduler`（后两者变化驱动，`status` 5s）。前端只有**一个** WS 所有者（`stores/realtime.ts`），页面不得自建连接。

## 5. 测试数量

| 层 | W5 基线 | W6 审计时 |
|---|---|---|
| pytest | 1664 | 1664（W6 新增 20+ 安全/切换/AI E2E 测试后见 §10） |
| vitest | 279 | 279（W6 新增 62 条验证测试后见 §10） |
| Playwright | 0（未安装） | 7（W6 装上并跑通，见 §11） |

## 6. Build size（W5 基线）

`webui/dist` 1.2 MB / 124 assets；主 chunk 279.00 kB（gzip 87.76 kB）；最大页面 chunk：Settings 164.52 kB、Input 74.18 kB。

## 7. Legacy 路由

58 个旧 SSR 页面 + 别名 **7 条**：`/legacy`、`/legacy/character`（GET+POST）、`/legacy/social`、`/legacy/memory`、`/legacy/memory/health`、`/legacy/memory/timeline`（W6 新增）。四个同名路径（`/character`、`/social`、`/memory`、`/memory/health`）由 dispatcher 按 `web.version` 分流；`/memory/timeline` 亦为 dispatcher（W6 修复遮蔽）。

## 8. 安全边界（审计基线）

会话 Cookie `catoobot_session`（httpOnly/Lax，12h，内存态）+ 全站 CSRF 中间件（写操作缺/错 token → 403 `auth.csrf`）+ 单管理员模型（无 RBAC）+ 6 个后端确认门（`memory/world/ai/config/media/tools.confirm_required`）+ Secret 只以 masked 形态出现 + 真实 Character Bible 从不进入 API/前端。

---

## 9. W6 发现（按严重度）

### P0（阻止发布）——1 项，已修复

| # | 问题 | 证据 | 处置 |
|---|---|---|---|
| P0-1 | **未登录时 SPA 自己的静态资源被会话中间件拦截**：`/assets/*`（以及 favicon）要求登录，302 → `/login`；匿名浏览器打开登录页时 HTML 能拿到、`/assets/index-*.js` 拿不到（返回 HTML），**真实浏览器里 v1 登录页根本无法工作**（jsdom 测试不会加载模块脚本，所以 W3–W5 都没暴露） | 浏览器 E2E 首次运行即失败（chunk 加载失败）；`app/web/server.py` 中间件对 `/assets/` 一视同仁 | 静态壳资源（`/assets/*`、`/favicon.ico`）加入中间件豁免；`/api/v1`、`/ws`、页面路径仍全部要求会话 |

### P1（发布前必须修复）——10 项，已全部修复

| # | 问题 | 证据（修复前） | 处置 |
|---|---|---|---|
| P1-1 | **四个 v1 分流点是死代码**：`/character`、`/social`、`/memory`、`/memory/health` 的 dispatcher 注册在旧路由模块之后，aiohttp 先注册者优先 → v1 模式下这四个入口仍返回 v0.8 SSR | W6 验证代理实测 `GET /character` → `行为规则` 旧页面；W5 的 dispatcher 测试只覆盖了 `/`、`/login` | 新增 `SpaRoutes.register_page_dispatch()`，在 `server.py` 中**先于**旧模块调用；`register_spa` 不再重复注册 |
| P1-2 | **CSRF 豁免按路径而非按方法**：`/api/v1/session` 整条路径豁免 → `DELETE /api/v1/session`（登出）与 `PATCH`（偏好）无需 token 即可执行 | 安全矩阵测试实测无 token 的 `DELETE /api/v1/session` → 200 且会话被销毁 | 豁免改为 `POST /api/v1/session` 单一方法；登出/偏好现在同样要求 CSRF |
| P1-3 | **钉住模型无法故障转移**：`ModelRouter._candidates()` 对 `AIRequest.model` 只返回该模型，`POST /ai/models/{name}/test`（契约写明「含失败转移」）在主模型 429 时直接 `AllModelsFailedError` | W6 AI E2E：m1(429)+m2(ok) → 备份模型从未被调用 | 钉住模型作为**首选**，其后跟随启用链（未知/禁用仍快速失败）；244 条 AI/工具测试保持通过 |
| P1-4 | `/memory/timeline` 被旧 SSR 页面遮蔽（不在分流清单里） | 浏览器 E2E 访问 `/memory/timeline` 得到旧页面 | 加入 dispatcher，并提供 `/legacy/memory/timeline` 别名 |
| P1-5 | `RestartBanner` 在会话就绪前请求 `restart-pending` → 匿名 401 噪声 | 浏览器 console 出现 401 | 组件仅在已认证时加载 |
| P1-6 | **Dashboard/角色页 uptime 永远显示 `—`**：页面读 `runtime.uptime_seconds`，实际在 `process.uptime_seconds` | 浏览器 E2E 断言 uptime ≠ — 失败 | 前端同时兼容两种形状 |
| P1-7 | **带连字符的 Provider/模型名无法通过 `/config/effective` 读取**：点号键解析正则只允许 `[A-Za-z0-9_]`，而 W2 的命名校验明确允许 `-` | 实测 `split_key('ai.providers.my-provider.base_url')` 抛 `config.unknown_key` | 键解析允许 `-`（含通配匹配），并加回归测试 |
| P1-8 | **Provider/模型删除没有二次确认**（前端无对话框、后端无 confirm 门），与 W6 §37 的「危险操作」清单冲突 | W6 前端验证：点击删除立刻发出 `DELETE` | 双保险：前端 ConfirmDialog + 后端 `409 ai.confirm_required`（`confirm=<name>`；级联删除需 `confirm="force"`）；契约与测试同步更新 |
| P1-9 | **测试环境污染**：`tests/test_webui_config.py` 通过 `load_config()` 触发真实 `load_dotenv()`，把真实 `.env` 的 `CATOOBOT_WEB_PASSWORD` 泄漏进测试进程，导致特定组合下 3 个 WS 测试失败 | W6 后端验证代理实测 | 该文件对 `load_dotenv` 加隔离（与其他测试同一做法） |
| P1-10 | **7 个既有测试编码了 W5 的错误映射**：断言 v1 模式下 `/character`、`/memory`、`/memory/health` 返回旧页面 | 修复 P1-1 后集中失败 | 改为断言 v1 返回 SPA、旧页面在 `/legacy/*`；`POST /legacy/character` 随别名一并注册 |

### P2（可带发布）——记录不改

| # | 项 | 说明 |
|---|---|---|
| P2-1 | `preview_url` 恒 `null`（贴纸无静态文件路由） | 不伪造 URL；W5 已记录 |
| P2-2 | 记忆纠正 / embeddings / consolidation 重操作仍在旧控制台 | 重型运维，风险高 |
| P2-3 | 角色导出/导入不迁移（§99 语义） | 防 Bible/Secret 泄露面 |
| P2-4 | 关系/承诺只读 | Core 无 Admin 写路径 |
| P2-5 | 旧 `/tools/{name}` 通配在旧 SSR 里仍遮蔽其子页 | legacy-only 既有行为，不影响 v1 |
| P2-6 | `spaces[]` 行字段名与类型标注不同（页面两种都读） | 兼容展示 |
| P2-7 | 契约里 `/ai/models/{name}/test` 与 `/ai/status` 的 `degraded` 定义偏保守（仅当无可用模型/缺 Key） | 语义不变，仅记录 |
| P2-8 | Windows 下 E2E 服务器 stdout 偶发 `ConnectionResetError 10054` | 无害噪声 |

### P3（未来优化）——记录不改

深色/浅色对比度的逐项复核、移动端专项手势、Playwright 在 Linux 分支的验证、旧页面的进一步收敛（等 W6 之后单独评估）。

## 10. W6 必须修复项（对应 §9）

P0-1 与 P1-1…P1-10 —— **全部已修复**，各自带测试（见 `WEBUI_V1_FINAL_TEST_MATRIX.md` 与最终报告）。

## 11. W6 可接受遗留项

见 §9 的 P2/P3；其中 P2-1/2/3/4 是**刻意的产品边界**（不导出、不伪造、只读），P2-5/6 是 legacy 与展示兼容，P2-7/8 是语义/环境噪声。

## 12. 明确不处理项

- 不重写 Router / API Client / Store / UI Shell / aiohttp server（§110）。
- 不新增任何领域功能、Agent/Memory/Social 能力或第二套 Runtime（§140）。
- 不改 Git 历史（§108）。
- 不为「更高级」加入动画/图表/新主题（§142）。
- 旧 SSR 的 `/tools/{name}` 遮蔽问题只在报告中记录（§112），不为它动 v1。
