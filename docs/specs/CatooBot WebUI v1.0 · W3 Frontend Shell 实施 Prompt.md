# CatooBot v2.1 · WebUI v1.0 — W3 Frontend Shell

## 0. 当前基线

W1：

```text
3478b0a2c676ac446236a9acdd762bdf365fd3aa
```

W2：

```text
1ba6f79684df93d3e9613d130d04394fe594cb08
```

当前状态：

```text
W1 Audit                 ✅
W2 Backend API           ✅
W3 Frontend Shell        ← 本阶段
W4 AI / Configuration    NOT STARTED
W5 Domain Pages          NOT STARTED
```

当前质量状态：

```text
pytest: 1522 passed
ruff: PASS
ruff format: PASS
mypy: PASS
CI: success
working tree: clean
```

W2 已经建立：

```text
/api/v1
Config Registry
Effective Config
Credential API
Provider API
Model API
Model Role API
Failover configuration
Runtime Read API
Realtime / WebSocket contract
Auth / CSRF
```

旧 SSR WebUI 继续保留。

---

# 1. W3 的任务定位

本阶段名称：

> **WebUI v1.0 · W3 Frontend Shell**

本阶段只建立：

```text
Vue 3
+
TypeScript
+
Vite
+
Design System
+
Application Shell
+
Routing
+
API Client
+
Auth / CSRF
+
Realtime Store
+
Theme
+
基础 Dashboard 骨架
```

目标：

> **让 CatooBot 拥有一个可以持续开发的现代 WebUI 前端骨架。**

---

# 2. W3 明确不做的事情

本阶段禁止完成完整的：

```text
❌ Provider 管理页面
❌ Model 管理页面
❌ API Key 管理页面
❌ Model Role 页面
❌ Failover 页面
❌ Config Center 完整页面
❌ Memory 页面迁移
❌ Social 页面迁移
❌ Sandbox 完整页面
❌ Tools 页面迁移
❌ Agent 页面迁移
❌ Sticker 页面迁移
```

这些属于：

```text
W4 AI / Configuration
W5 Domain Pages
```

---

# 3. W3 可以做的页面

只允许先完成：

```text
/                       → WebUI v1 Shell + Dashboard skeleton
/login                  → v1 Login
```

Dashboard 只需要验证：

```text
WebUI 已连接
API 已连接
Session 正常
CSRF 正常
Runtime 可以读取
Realtime 可以连接
```

可以放少量真实数据：

```text
Bot Online
AI status
QQ status
Runtime status
Current world
```

但不要在 W3 把 Dashboard 做成最终产品。

---

# 4. 前端技术栈

优先：

```text
Vue 3
TypeScript
Vite
Naive UI
```

如果仓库实际环境发现已有其他更合适的前端依赖：

> 优先使用成熟现有基础设施，而不是为了“标准答案”强行增加重复依赖。

建议：

```text
Vue Router
Pinia
Axios / fetch wrapper
Lucide icons
```

但是：

> 每引入一个第三方依赖都必须说明用途，并检查 license / maintenance / bundle cost。

不要为了一个小功能引入大型库。

---

# 5. 前端目录结构

建议：

```text
webui/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.ts
│   ├── App.vue
│   │
│   ├── api/
│   │   ├── client.ts
│   │   ├── auth.ts
│   │   ├── config.ts
│   │   ├── runtime.ts
│   │   └── realtime.ts
│   │
│   ├── stores/
│   │   ├── auth.ts
│   │   ├── app.ts
│   │   ├── runtime.ts
│   │   └── realtime.ts
│   │
│   ├── layouts/
│   │   ├── AppLayout.vue
│   │   └── AuthLayout.vue
│   │
│   ├── pages/
│   │   ├── Login.vue
│   │   └── Dashboard.vue
│   │
│   ├── components/
│   │   ├── AppSidebar.vue
│   │   ├── AppTopbar.vue
│   │   ├── StatusBadge.vue
│   │   ├── MetricCard.vue
│   │   ├── LoadingState.vue
│   │   ├── ErrorState.vue
│   │   └── EmptyState.vue
│   │
│   ├── router/
│   │   └── index.ts
│   │
│   ├── styles/
│   │   ├── tokens.css
│   │   ├── base.css
│   │   └── theme.css
│   │
│   └── types/
│       ├── api.ts
│       ├── auth.ts
│       └── runtime.ts
│
└── public/
```

实际结构可以调整，但必须保持：

```text
API
Store
View
Component
Style
```

分层明确。

---

# 6. 不允许前端直接操作后端内部结构

前端只能通过：

```text
/api/v1
```

访问后端。

禁止：

```text
import Python backend logic
```

或者依赖旧 SSR 页面 HTML 解析数据。

禁止：

```text
document.querySelector(...)
```

去解析旧 WebUI 内容。

---

# 7. API Client

建立统一：

```text
api/client.ts
```

负责：

```text
base URL
JSON parsing
response envelope
HTTP errors
401
403
409
422
500
503
```

所有 API 调用统一经过它。

不要：

```text
fetch()
fetch()
axios()
fetch()
```

在每个页面各写一遍。

---

# 8. Response Envelope

严格匹配 W2 当前 API 实际协议。

不要根据 W1 Prompt 猜。

先读取：

```text
docs/WEBUI_API_CONTRACT.md
```

再根据真实 W2 endpoint 测试确认。

前端类型统一：

```ts
ApiSuccess<T>
ApiError
ApiResponse<T>
```

如果某个 endpoint 的当前响应结构与文档存在差异：

> **先兼容当前已实现结构，不在 W3 自己修改后端契约。**

并在最终报告列出。

---

# 9. Auth

建立：

```text
stores/auth.ts
api/auth.ts
```

支持：

```text
login
logout
session bootstrap
session refresh / reload
```

流程：

```text
打开 WebUI
↓
GET /api/v1/session
↓
判断登录状态
↓
未登录 → Login
已登录 → App
```

---

# 10. CSRF

前端不能假设 CSRF token 永远存在。

初始化流程：

```text
GET /api/v1/session
        ↓
GET /api/v1/csrf
        ↓
保存 token
        ↓
所有写请求自动注入
```

如果 W2 当前 session endpoint 已经同时提供 CSRF 信息：

优先复用，不要重复调用。

---

# 11. CSRF 必须由 API Client 自动处理

业务页面不能写：

```ts
headers["X-CSRF-Token"] = ...
```

必须统一在：

```text
api/client.ts
```

完成。

以后 W4/W5 的所有写页面自动获得保护。

---

# 12. 401 / 403 行为

如果 API 返回：

```text
401
```

前端：

```text
清理 session 状态
→ 跳转 Login
```

如果：

```text
403
```

显示：

```text
没有权限
```

不要把 403 自动当成“登录失效”。

CSRF 失败尤其不要自动退出用户。

---

# 13. API 错误显示

建立统一错误处理：

```text
ApiErrorHandler
```

例如：

```text
409 ai.provider_in_use
```

前端应该能显示：

```text
无法删除 Provider
该 Provider 仍被以下模型使用。
```

如果后台返回可读 message：

优先使用后端 message。

不要前端自己猜错误原因。

---

# 14. Realtime

建立：

```text
api/realtime.ts
stores/realtime.ts
```

统一处理：

```text
WebSocket connect
disconnect
reconnect
heartbeat
topic subscription
event dispatch
```

---

# 15. WebSocket Topic

W2 当前已经有：

```text
status
world
scheduler
log
qq
runtime
```

实际 topic 以 W2 contract 为准。

前端只建立：

```text
RealtimeEvent
```

类型。

---

# 16. 禁止每秒 polling

W3 Dashboard：

禁止：

```text
setInterval(fetch, 1000)
```

使用：

```text
REST initial snapshot
+
WebSocket realtime update
```

例如：

```text
页面加载
↓
GET /api/v1/overview
↓
GET /api/v1/runtime
↓
WebSocket connect
↓
Realtime updates
```

---

# 17. Realtime Store

建议：

```text
stores/realtime.ts
```

负责：

```text
connected
last_message_at
topics
events
connection_error
reconnect_count
```

其他 Store 不应该各自创建 WebSocket。

---

# 18. Runtime Store

建立：

```text
stores/runtime.ts
```

只保存：

```text
overview
runtime
world
scheduler
qq
ai status
```

不要复制整个 Sandbox。

它只是：

> WebUI read model cache。

---

# 19. Frontend State 不是真实世界

必须严格区分：

```text
Vue Store
    = UI cache

Backend
    = Runtime truth
```

刷新页面后：

```text
GET API
```

重新获得真实状态。

不能把浏览器中的旧状态认为是真相。

---

# 20. Design System

建立统一 Design Tokens。

例如：

```css
:root {
  --cb-bg: ...;
  --cb-surface: ...;
  --cb-surface-raised: ...;
  --cb-border: ...;
  --cb-text: ...;
  --cb-text-muted: ...;
  --cb-primary: ...;
  --cb-success: ...;
  --cb-warning: ...;
  --cb-danger: ...;
  --cb-radius-sm: ...;
  --cb-radius-md: ...;
  --cb-radius-lg: ...;
  --cb-shadow-sm: ...;
  --cb-shadow-md: ...;
}
```

不要在组件中到处写：

```css
#123456
```

---

# 21. 色彩方向

整体：

> Soft Dark / Soft Light

不要做：

```text
❌ 传统蓝色后台
❌ 高饱和霓虹
❌ 全屏渐变
❌ 大面积玻璃拟态
❌ 大量 Emoji
```

推荐：

```text
背景柔和
表面层次明显
边框低对比
Primary 柔和紫蓝
Success 柔和薄荷
Warning 柔和暖黄
Danger 柔和珊瑚
```

重点：

> **可读性优先于装饰性。**

---

# 22. Typography

统一：

```text
标题
页面标题
Section title
Body
Caption
Code
```

中文优先使用系统字体栈。

例如：

```css
font-family:
Inter,
ui-sans-serif,
system-ui,
-apple-system,
"Segoe UI",
"PingFang SC",
"Hiragino Sans GB",
"Microsoft YaHei",
sans-serif;
```

不要把代码字体作为正文。

---

# 23. Layout

桌面：

```text
┌──────────────┬───────────────────────────┐
│              │ Topbar                    │
│   Sidebar    ├───────────────────────────┤
│              │                           │
│              │ Content                   │
│              │                           │
└──────────────┴───────────────────────────┘
```

Sidebar：

```text
expanded
collapsed
```

都必须支持。

---

# 24. Sidebar 第一版

W3 只实现最终结构，不要求所有页面可用。

导航：

```text
🏠 总览

👤 角色

🤖 AI 与模型

💬 社交

🧠 记忆

🎨 媒体与能力

⚙ 系统
```

未完成页面可以显示：

```text
即将开放
```

或者暂时 disabled。

不要链接到不存在的页面。

---

# 25. Sidebar 不要继续复制 v0.8

不要把：

```text
v0.8 的十几个菜单
```

原样搬过来。

W3 开始就建立：

> **v1.0 信息架构。**

---

# 26. Topbar

至少包括：

```text
Sidebar toggle

当前页面名称

Realtime status

Bot status

Theme switch

用户菜单
```

例如：

```text
● Live
```

表示 WebSocket 正常。

如果断线：

```text
○ Reconnecting...
```

---

# 27. Dashboard Skeleton

页面：

```text
/
```

只做基础骨架：

顶部：

```text
CatooBot
Online
```

状态卡：

```text
QQ
AI
World
Runtime
```

当前世界：

```text
正在做什么
地点
Mode
```

最近事件：

```text
Realtime event feed
```

---

# 28. Dashboard 不允许自己轮询

数据：

```text
GET /api/v1/overview
```

然后通过：

```text
WebSocket
```

更新。

---

# 29. Dashboard 状态必须使用真实 API

不要写：

```ts
const botOnline = true;
```

不要 mock：

```text
AI Ready
QQ Connected
```

除非是在：

```text
unit test / Storybook-like local component preview
```

正式页面必须使用真实 API。

---

# 30. Loading / Error / Empty 状态

每一个基础组件都必须考虑：

```text
Loading
Success
Empty
Error
Disconnected
```

不要让 API 请求失败后整页白屏。

---

# 31. 统一基础组件

至少建立：

```text
StatusBadge
MetricCard
SectionHeader
PageHeader
LoadingState
ErrorState
EmptyState
ConfirmDialog
Toast
```

未来 W4/W5 必须复用。

---

# 32. Toast / Notification

统一：

```text
success
info
warning
error
```

不要自己：

```text
alert()
```

---

# 33. 页面级错误边界

如果：

```text
某一个 Dashboard card API 失败
```

不能让：

```text
整个页面崩溃
```

组件自己显示：

```text
暂时无法获取
重试
```

---

# 34. Dark / Light / System

实现：

```text
Light
Dark
System
```

用户选择持久化到：

```text
localStorage
```

或者项目统一 preference API。

优先 localStorage，避免 W3 新增后端设置。

---

# 35. Theme 初始值

首次进入：

```text
System
```

后续：

```text
用户主动选择
```

保存选择。

避免闪烁：

> 尽量在 app 初始化前应用 theme。

---

# 36. Responsive

至少适配：

```text
1440px
1280px
1024px
768px
```

桌面优先。

移动端：

```text
Sidebar → Drawer
```

不要要求现在完成手机端高级交互。

---

# 37. Accessibility

至少保证：

```text
button
input
dialog
navigation
```

具有：

```text
aria-label
keyboard focus
visible focus
```

颜色不能成为唯一状态提示。

例如：

```text
● Connected
```

同时有文本：

```text
已连接
```

---

# 38. Frontend API Types

不要把：

```ts
any
```

作为 API 类型逃生舱。

优先：

```ts
type
interface
```

定义：

```text
ApiResponse<T>
Session
Overview
Runtime
World
Scheduler
RealtimeEvent
ApiError
```

---

# 39. API Client 必须支持真实 Cookie

因为当前 WebUI 使用 Session。

前端请求必须正确发送：

```text
credentials: include
```

或者等价配置。

不要把认证改成：

```text
localStorage JWT
```

除非 Core 已经有这套架构。

不允许为了前端方便更换认证模型。

---

# 40. Vite Dev / Production

开发环境：

```text
Vite dev server
```

允许 proxy：

```text
/api
```

到 aiohttp。

生产：

```text
npm/pnpm build
```

得到静态资产。

最终由 aiohttp 提供。

---

# 41. aiohttp 集成

W3 必须完成：

```text
development
+
production serving
```

至少做到：

```text
GET /
→ v1 index.html
```

并正确 fallback：

```text
/dashboard
/ai
/settings
```

这些未来 SPA route 刷新时不能得到 404。

可以使用：

```text
SPA fallback
```

但必须保证：

```text
/api/v1/*
```

仍由 API handler 接管。

---

# 42. `/legacy` 保留

当前旧 WebUI 必须继续可访问。

设计：

```text
/
→ v1

/legacy
→ v0.8
```

如果 v0.8 有现有登录入口：

必须保持可访问方式。

不要删除。

---

# 43. Legacy 与 v1 不共享 DOM

两套 UI：

```text
legacy SSR
v1 SPA
```

可以共享：

```text
backend services
API
auth
```

但不应该互相操作 DOM。

---

# 44. Frontend Build CI

加入：

```text
npm/pnpm install
npm/pnpm run typecheck
npm/pnpm run build
```

但：

> 使用项目实际包管理器。

如果仓库已有 lockfile：

必须使用对应工具。

例如：

```text
pnpm-lock.yaml
→ pnpm
```

---

# 45. Dependency Lock

必须提交：

```text
package.json
lockfile
```

但不要提交：

```text
node_modules
dist
```

除非仓库已有特殊规定。

---

# 46. Bundle 控制

W3 不应该引入过重依赖。

构建完成后检查：

```text
bundle size
chunk count
```

避免：

```text
整个 UI framework + huge utility library
```

只为了基础 Shell。

---

# 47. API Contract 兼容要求

W3 不应该修改 `/api/v1` 后端协议。

如果前端发现：

```text
actual response
≠
documented contract
```

只在最终报告记录。

不要为了前端方便偷偷修改 Backend。

特别注意：

```text
POST /api/v1/ai/models/{name}/test
```

当前 W2 已明确存在任务书示例与 Contract 示例的 response shape 差异。

W3：

> 先封装当前实际 API response。

W4 前解决契约统一问题。

---

# 48. Frontend Logging

开发环境允许：

```text
console.debug
```

生产构建不得留下：

```text
API Key
Cookie
CSRF token
完整 response
```

尤其：

```text
console.log(response)
```

不能用于 Secret endpoint。

---

# 49. Security

检查：

```text
XSS
unsafe HTML
v-html
URL injection
```

默认禁止：

```vue
v-html
```

除非经过明确 sanitize。

不要将 API 返回内容直接作为 HTML 注入。

---

# 50. 不要实现假数据

允许测试：

```text
mock fixtures
```

但正式页面：

```text
Dashboard
```

必须来自真实：

```text
/api/v1
```

---

# 51. Testing

W3 至少增加：

## API Client tests

覆盖：

```text
200
401
403
409
422
500
```

## Auth

```text
logged in
logged out
CSRF bootstrap
401 redirect
```

## Realtime

```text
connect
disconnect
reconnect
event dispatch
```

## Store

```text
runtime update
world update
status update
```

## Components

至少测试：

```text
StatusBadge
MetricCard
LoadingState
ErrorState
```

## E2E

至少：

```text
打开 /
未登录 → Login

登录
↓
Dashboard
↓
API success

WebSocket connected

改变主题
↓
刷新
↓
主题保持
```

---

# 52. 必须验证旧 WebUI

运行完整测试：

```text
GET /
```

在新的 v1 root 接管后：

确认：

```text
/legacy
```

仍正常。

以及：

```text
POST /login
```

旧机制不被破坏。

---

# 53. 不要修改 Core

整个 W3：

```text
Sandbox
Goal
Action
Need
Social
Memory
Conversation
OneBot
RuntimeScheduler
```

禁止功能性修改。

如果必须修改 backend：

只允许：

```text
SPA serving
API compatibility
static assets
```

性质的管理层改动。

---

# 54. Git

正常提交。

禁止：

```text
force push
history rewrite
rebase shared main
filter-repo
BFG
```

---

# 55. Character Bible

继续检查：

```text
config/character_bible.md
docs/bible_source_罐头.txt
```

不得进入：

```text
frontend bundle
API debug
config export
```

任何真实 Character Bible 内容都不能被打包进前端。

---

# 56. W3 实施顺序

严格：

```text
Step 1
审计当前 Node / frontend 环境

Step 2
初始化 Vue / TypeScript / Vite

Step 3
建立 Design Tokens

Step 4
建立 API Client

Step 5
建立 Auth / CSRF

Step 6
建立 Router

Step 7
建立 Layout

Step 8
建立 Sidebar

Step 9
建立 Topbar

Step 10
建立 Theme

Step 11
建立 Realtime Store

Step 12
建立 Runtime Store

Step 13
Dashboard skeleton

Step 14
aiohttp production serving

Step 15
Legacy route preservation

Step 16
Frontend tests

Step 17
Full regression

Step 18
CI
```

---

# 57. W3 不要过度设计 Dashboard

当前 Dashboard 只是：

> **验证 Shell + API + Realtime 全链路。**

不要在 W3 里做：

```text
复杂图表
AI Usage 分析
Memory analytics
Social analytics
完整世界可视化
```

这些都留到后续阶段。

---

# 58. W3 验收标准

完成后必须满足：

```text
✅ Vue 3 / TypeScript / Vite 已建立
✅ Frontend build 成功
✅ Typecheck 成功
✅ API Client
✅ Response envelope
✅ Auth
✅ CSRF
✅ Router
✅ Sidebar
✅ Topbar
✅ Theme
✅ Light/Dark/System
✅ WebSocket
✅ Realtime Store
✅ Runtime Store
✅ Dashboard skeleton
✅ Loading/Error states
✅ Responsive desktop/tablet
✅ aiohttp production serving
✅ /api/v1 不受影响
✅ /legacy 可访问
✅ 旧 SSR 功能保持
✅ Core 未发生功能性修改
✅ Secret 未进入前端
✅ pytest 全部通过
✅ frontend test 全部通过
✅ lint/typecheck/build 全部通过
✅ CI success
✅ working tree clean
```

---

# 59. 人工验收

实际启动：

```text
CatooBot
```

打开：

```text
/
```

验证：

### 未登录

```text
→ Login
```

### 登录

```text
→ Dashboard
```

### Dashboard

看到：

```text
Bot
QQ
AI
World
Runtime
```

且数据来自真实 API。

### WebSocket

看到：

```text
● Live
```

关闭后：

```text
○ Reconnecting...
```

恢复后：

```text
● Live
```

### 世界变化

Sandbox 状态发生变化后：

```text
Dashboard World card
```

实时更新。

### 主题

切换：

```text
Light
Dark
System
```

刷新后保持。

### Legacy

打开：

```text
/legacy
```

旧 WebUI 仍然工作。

---

# 60. W3 最终报告

完成后报告：

```text
# WebUI v1.0 · W3 Frontend Shell Report

1. W2 baseline
2. W3 final commit
3. Frontend stack
4. Frontend directory
5. API client
6. Auth
7. CSRF
8. Realtime
9. Theme
10. Design system
11. Layout
12. Dashboard
13. Legacy strategy
14. aiohttp serving
15. npm/pnpm build
16. typecheck
17. frontend tests
18. pytest
19. ruff
20. mypy
21. CI
22. bundle size
23. known limitations
24. contract discrepancies
25. final Git status
```

---

# 61. W3 停止条件

完成后：

```text
W1 Audit
✅

W2 Backend API
✅

W3 Frontend Shell
✅

W4 AI / Configuration
NOT STARTED
```

**立即停止。**

不要进入 W4。

不要开始：

```text
Provider UI
Model UI
API Key UI
Config Center UI
Memory UI
Social UI
```

---

# 最核心原则

W3 的目的不是：

> “把 v0.8 搬到 Vue。”

而是：

> **建立一个从今天开始可以长期迭代的 CatooBot WebUI v1.0 前端基础设施。**

最终架构：

```text
                    CatooBot WebUI v1

┌──────────────────────────────────────────────┐
│                  Vue 3 SPA                   │
│                                              │
│  Layout / Router / Stores / Components       │
└──────────────────────┬───────────────────────┘
                       │
                API Client / WS
                       │
┌──────────────────────▼───────────────────────┐
│                /api/v1                       │
│                                              │
│ Config / AI / Runtime / Domain / Realtime   │
└──────────────────────┬───────────────────────┘
                       │
                 Admin Services
                       │
┌──────────────────────▼───────────────────────┐
│                CatooBot Core                 │
└──────────────────────────────────────────────┘
```

**W3 只建立这一层 Frontend Shell，不提前实现 W4 的 AI 与 Configuration 页面。完成后停止。**