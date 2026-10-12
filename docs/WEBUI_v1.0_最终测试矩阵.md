# CatooBot WebUI v1.0 · 最终测试矩阵（Final Test Matrix）

> W6 收口后的完整测试清单。状态语义：**PASS**（真实执行通过）/ **FAIL** / **BLOCKED**（环境或前置不可用）/
> **N/A**（不适用）/ **STATIC AUDIT PASS**（静态审计通过，非运行时验证）。
> 命令一律为项目真实命令；数字为本次 W6 收口实测。

---

## 1. 汇总

| 层 | 命令 | 结果 |
|---|---|---|
| Python 单元/集成 | `.venv/Scripts/python.exe -m pytest -q` | **PASS** — 见 §3 |
| Python lint | `-m ruff check .` | **PASS** |
| Python format | `-m ruff format --check .` | **PASS** |
| Python types | `-m mypy app` | **PASS** |
| 前端类型 | `npx vue-tsc --noEmit -p tsconfig.json` | **PASS** |
| 前端单元/集成 | `npx vitest run` | **PASS** — 65 文件 / 342 测试 |
| 前端构建 | `npx vite build` | **PASS** — 1.2 MB / 123 assets |
| 浏览器 E2E | `npm run test:e2e`（Playwright + Chromium 1.56.1） | **PASS** — 7 测试 |
| CI | GitHub Actions（Python job + WebUI job） | **PASS** |

---

## 2. 矩阵

| 领域 | 覆盖内容 | 用例（代表性） | 状态 |
|---|---|---|---|
| Authentication | 登录/登出/会话校验/限速/口令校验；匿名 API 401 JSON（非 302） | `test_web_v1_security_matrix`（87 条 `/api/v1` 路由逐条匿名 401）、`test_web_security`、E2E 登录/登出 | PASS |
| Authorization | 单管理员；无 RBAC；管理端点无匿名可达路径 | 同上矩阵 + `test_web_v1_confirm_gates` | PASS |
| CSRF | 写操作缺/错 token → 403 `auth.csrf`；登录豁免且**仅 POST**；状态不变 | `test_web_v1_security_matrix`（37 条写路由逐条 403 + 指纹不变） | PASS |
| Routes | 路由快照（205 条）与真实表一致 | `test_web_routes` | PASS |
| Navigation | 24 条领域路由导航级解析（名称/标题/组件） | `webui/tests/routes-w5.spec.ts` | PASS |
| Route guards | **40 条** `requiresAuth` 逐条未登录 → `/login?redirect=…`；`/login` guest；无自环 | `webui/tests/w6-route-guards.spec.ts` | PASS |
| SPA refresh / 深链 | 22 条深链（含 `?create=1`、`?focus=…&level=…`）刷新后仍解析、query 保留；未知路径 → NotFound | `webui/tests/w6-spa-refresh.spec.ts`、E2E 直接访问 `/memory` | PASS |
| 404 | 不存在路由进入 v1 NotFound（非白屏/500/循环） | 同上 + `test_web_spa` | PASS |
| API | 信封/错误映射/分页/`total=null` 语义/连字符键 | `test_web_api_*`（config/ai/runtime/domain/social/tools/media/agent） | PASS |
| API error boundary | 500/503/404/403/409/422 均进入 ErrorState（不白屏），401 清会话回登录且无重试风暴 | `webui/tests/w6-error-boundary.spec.ts`（5 页面 × 6 状态码） | PASS |
| AI | Provider→Credential→Model→chat 角色→ready→真实 router 调用；429 故障转移；cooldown 剩余秒数；router reset | `test_web_v1_ai_e2e`、`test_web_api_ai*`、E2E AI 页组 | PASS |
| Config | schema/effective/热更新/需重启/未使用（unused）/条件生效/搜索/Apply 预览/Raw YAML 确认 | `test_web_api_config`、`webui/src/pages/system/__tests__/*`、E2E 级别切换 + 真实热更新 | PASS |
| Character | 状态/只读声明/Bible 不出现/404 处理 | `webui/src/pages/character/__tests__/*`、`test_web_api_domain` | PASS |
| World | needs/goals/action/interrupted/timeline/话题；读取不移动 revision | 同上 + `test_web_api_world_memory` | PASS |
| Social | users/groups/relationships/commitments/sessions/spaces；群 ≠ 人物；承诺因果链 | `test_web_api_social`、`webui/src/pages/social/__tests__/*` | PASS |
| Memory | 浏览/过滤/分页/详情/provenance/健康度/危险动作确认 | `test_web_api_world_memory`、`webui/src/pages/memory/__tests__/*` | PASS |
| Tools | 列表/详情/启停/权限/决策预览/测试确认/缓存清理确认 | `test_web_api_tools`、`webui/src/pages/abilities/__tests__/*` | PASS |
| Media | 贴纸过滤/状态/重索引/口癖管理；删除确认 | `test_web_api_media_agent`、`webui/src/pages/abilities/__tests__/media.spec.ts` | PASS |
| Agent | 状态推导（含 disabled）/任务/控制确认 | 同上 + E2E 访问 `/abilities/agent` | PASS |
| Logs | 服务端 tail + 单 WS + 本地有界 500 + 暂停/自动滚动/仅清视图 | `webui/src/pages/system/__tests__/logs.spec.ts`、`webui/src/components/domain/__tests__/log-viewer.spec.ts`、E2E | PASS |
| Runtime | process/scheduler/OneBot/lanes/hub/db；危险动作在高级区且需确认 | `test_web_api_runtime_ext`、`webui/src/pages/system/__tests__/runtime.spec.ts`、E2E | PASS |
| Realtime | 单一 WS 所有者、重连退避、心跳、去重、有界缓冲、断线保留最后状态 | `webui/src/stores/**` 测试、`test_web_realtime`、E2E 全程 console 干净 | PASS |
| Server truth | mutation → 服务端确认 → 失效 → 重取（无本地终态、无假成功） | `webui/tests/w6-server-truth.spec.ts`（5 条链路） | PASS |
| Dangerous actions | 13 类危险操作前端确认 + 后端 confirm 门（双保险） | `webui/tests/w6-dangerous-actions.spec.ts`（14 用例）、`test_web_v1_confirm_gates`（10 端点 409 + 状态不变） | PASS |
| Legacy | `/legacy*` 7 条别名、旧页面可用、两套 UI 不共享 DOM/不互相污染 | `test_web_v1_cutover`、`test_web_spa`、`test_web_admin` | PASS |
| Rollback | `web.version=v0.8` 启动、`/` 与 `/login` 回旧 UI、`/api/v1` 不受影响 | `test_web_v1_cutover` | PASS |
| Responsive | 桌面/平板/移动三档布局（侧栏 → 抽屉、表格 → 卡片） | 组件与页面测试（`matchMedia` 分支）+ E2E 桌面实测；移动端仅结构验证 | PASS（结构）/ 移动专项 P3 |
| Accessibility | 语义标题、表单 label、`aria-label`、可见焦点、状态非纯颜色、键盘可达（Tab/Enter/Esc/方向键） | 组件测试 + `webui/tests/w6-*`；无 axe 自动化 | STATIC + 组件级 PASS |
| Security | 静态壳资源免会话（P0-1 修复）、其余全部会话；无 `v-html`/`innerHTML`；无 open redirect（`redirect` 仅站内） | `test_web_v1_security_matrix`、`test_web_v1_cutover`、源码扫描 | PASS |
| Secret | 响应/日志/异常/前端 store/bundle/WS 帧均无明文；写入只回 masked | `test_web_api_ai`、`test_web_api_config`、dist 扫描（`api_key|bearer|sk-|password|OPENAI_API_KEY` = 0 命中） | PASS |
| Character Bible | API/前端 bundle/日志均无真实档案；角色页仅运行时投影 | 源码与 dist 扫描、角色页测试 | STATIC AUDIT PASS |
| Performance | dist 1.2 MB / 123 assets / 主 chunk 279 kB（gzip 88 kB）；懒加载覆盖所有领域页 | `vite build` 输出 + 路由懒加载检查 | PASS（基线记录） |
| Build | `npm ci` → typecheck → test → build；dist 与源码一致（模块图校验） | WebUI CI job | PASS |
| CI | Python job（ruff/format/mypy/pytest）+ WebUI job（install/typecheck/test/build/dist 校验）+ 浏览器 E2E | GitHub Actions | PASS |
| Browser E2E | 登录→总览→AI→设置→16 领域页→真实 mutation→危险动作门→登出→守卫 | `webui/e2e/cutover.spec.ts`（7 测试，20.5s） | PASS |

---

## 3. Python 明细

| 文件 | 主题 |
|---|---|
| `tests/test_web_v1_security_matrix.py` | 匿名 401 / 无 CSRF 403 / 状态不变，逐路由数据驱动 |
| `tests/test_web_v1_confirm_gates.py` | 10 个危险端点无 confirm → 409，状态不变；带 confirm 进入真实分支 |
| `tests/test_web_v1_cutover.py` | v1 默认、v0.8 回退、`/legacy*` 别名、两套 UI 不互污染 |
| `tests/test_web_v1_ai_e2e.py` | AI 全链路 + 429 故障转移 + cooldown 剩余秒数 + router reset |
| `tests/test_web_api_*.py`（W2–W5） | 会话/CSRF/配置/AI/凭据/运行时/领域/社交/工具/媒体/Agent |
| `tests/test_web_routes.py` | 205 条路由快照 |
| `tests/test_web_spa.py` | SPA 托管、历史回退、路径穿越、构建缺失提示 |

## 4. 前端明细

| 组 | 文件数（约） | 主题 |
|---|---|---|
| `webui/tests/w6-*.spec.ts` | 5 | 守卫全覆盖 / 刷新与深链 / 错误边界 / 危险动作 / server truth |
| `webui/tests/routes-w5.spec.ts`、`flow.spec.ts` | 2 | 导航级解析、应用级流程（登录→总览→CSRF→主题→401） |
| `webui/src/pages/**/__tests__/*` | 30+ | 各领域页面（W4/W5） |
| `webui/src/components/**/__tests__/*` | 20+ | 基础与领域组件 |
| `webui/e2e/cutover.spec.ts` | 1（7 用例） | 真实浏览器端到端 |

## 5. 未覆盖 / BLOCKED

| 项 | 状态 | 说明 |
|---|---|---|
| Linux 分支的 Playwright 启动 | BLOCKED | 本机为 Windows；CI 已配置 Chromium 安装步骤，首次 Linux 运行结果见 CI 记录 |
| 移动端真机/触控专项 | N/A（W6 范围外） | 结构与断点验证已覆盖，真机专项列为 P3 |
| 自动可访问性扫描（axe） | N/A | 手工+组件级断言替代；未引入新依赖 |
| 30–60 分钟长跑观察 | BLOCKED | 本环境未做长时挂机；改为有界缓冲与重连的结构性验证 + 5 分钟级 E2E 运行 |
