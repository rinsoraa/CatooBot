# CatooBot v2.1 · WebUI v1.0
# W6 Final Integration / Cutover
# 全量整合 · 安全验收 · 回归验证 · 默认切换 · v0.8 收口

---

# 一、当前基线

当前 WebUI v1.0 已完成：

```text
W1 Audit
✅

W2 Backend API
✅

W3 Frontend Shell
✅

W4 AI & Configuration
✅

W5 Domain Pages
✅
```

当前最终 W5 baseline：

```text
e6f49f01de901fdb29ac7c5fba1d8d3154a53e0f
```

W4 baseline：

```text
51cfaeef1e7b9284fa7b0a6606b6b80911767dd1
```

当前 HEAD：

```text
e6f49f01de901fdb29ac7c5fba1d8d3154a53e0f
```

当前分支：

```text
main
```

W5 已完成：

```text
Character
World / Sandbox
Social
Memory
Tools
Media / Sticker
Agent
Logs
Runtime
Dashboard
```

以及：

```text
/api/v1
WebSocket
Session / CSRF
Config
AI
Credentials
Legacy migration
Route guards
Realtime
```

---

# 二、W6 定位

本阶段名称：

> **WebUI v1.0 · W6 Final Integration / Cutover**

核心目标：

> **不再增加新的 WebUI 领域功能，而是对 W1～W5 进行一次完整的系统级整合、验证、修复、性能与安全收口，并将 WebUI v1 正式设为默认入口。**

W6 完成后：

```text
WebUI v1
=
正式版本
```

而：

```text
v0.8 legacy
=
兼容 / 回退版本
```

---

# 三、W6 不是什么

W6 不是：

```text
继续增加新页面
```

不是：

```text
继续扩展 Core
```

不是：

```text
重新设计 WebUI
```

不是：

```text
重写 Runtime
```

不是：

```text
增加新的 Agent / Memory / Social 能力
```

禁止把 W6 变成：

```text
W7
W8
新的功能开发阶段
```

---

# 四、W6 核心原则

严格保持：

```text
Existing Core
        ↓
Existing Admin / Domain Services
        ↓
/api/v1
        ↓
WebUI v1
```

WebUI：

```text
展示
编辑
诊断
控制
```

Core：

```text
真实状态
真实配置
真实业务逻辑
真实权限
真实 Runtime
```

禁止：

```text
前端造假状态
前端计算业务真相
前端实现第二套 Runtime
前端实现第二套 Router
前端实现第二套配置系统
```

---

# 五、第一步：完整审计当前仓库

编码前必须首先读取：

```text
docs/WEBUI_V1_AUDIT.md
docs/WEBUI_CONFIG_MATRIX.md
docs/WEBUI_API_CONTRACT.md
docs/WEBUI_V1_DOMAIN_MAP.md
docs/WEBUI_V1_MIGRATION_MAP.md
```

然后检查：

```text
webui/
app/web/
app/runtime/
app/ai/
app/memory/
app/social/
app/tools/
app/agent/
app/onebot/
```

如果对应模块存在，则阅读其真实 implementation。

同时检查：

```text
pyproject.toml
package.json
vite.config.*
tsconfig.*
.github/workflows/
```

---

# 六、W6 Audit Report

在真正修改代码前：

创建：

```text
docs/WEBUI_V1_W6_AUDIT.md
```

至少记录：

```text
1. W5 baseline
2. 当前 v1 route 数
3. 当前 API endpoint 数
4. 当前 WebSocket topic
5. 当前测试数量
6. 当前 build size
7. 当前 legacy 路由
8. 当前 security boundary
9. 当前 known limitations
10. W6 必须修复项
11. W6 可接受遗留项
12. 明确不处理项
```

---

# 七、W6 Audit 分类

所有发现的问题必须分成：

```text
P0
阻止发布

P1
正式发布前必须修复

P2
可以带着发布

P3
未来优化
```

---

# 八、P0 示例

以下任何问题都属于 P0：

```text
WebUI v1 无法启动

登录绕过

CSRF bypass

Secret 泄露

Character Bible 泄露

普通用户可调用管理员接口

危险操作无需确认

错误操作直接修改 Runtime 真相

Legacy fallback 彻底失效

AI mutation 产生错误配置

Session 隔离失败
```

---

# 九、P1 示例

例如：

```text
关键页面导航错误

某个 mutation 后 UI 与 server truth 不一致

关键空状态异常

关键 loading 状态异常

核心 API contract mismatch

重要页面无法在刷新后恢复

WS 重连后状态错误

移动端关键页面严重不可用

关键 accessibility 问题
```

---

# 十、禁止为了完成 W6 隐藏问题

禁止：

```text
删除测试
降低测试要求
关闭 lint
跳过 typecheck
忽略 CI
注释掉失败功能
```

也禁止：

```text
把真实错误改成假的 success
```

---

# 十一、Cutover 目标

W6 完成以后：

默认：

```text
web.version = v1
```

v0.8：

```text
仍然可用
```

并作为：

```text
rollback
legacy fallback
```

---

# 十二、Cutover 原则

不能直接：

```text
删除 v0.8
```

必须：

```text
v1 default
+
v0.8 preserved
+
legacy routes preserved
+
web.version rollback
```

---

# 十三、先验证 web.version

确认：

```text
v1
v0.8
```

都能够正常启动。

测试：

```text
web.version = v1
```

访问：

```text
/
```

必须进入：

```text
WebUI v1
```

---

# 十四、Legacy 验证

测试：

```text
web.version = v0.8
```

访问：

```text
/
```

必须仍然进入：

```text
legacy WebUI
```

---

# 十五、Legacy Direct Alias

逐项验证：

```text
/legacy/character
/legacy/social
/legacy/memory
/legacy/memory/health
```

能够正常访问。

---

# 十六、v1 / legacy 不允许互相污染

例如：

```text
v1 session
```

不能改变：

```text
legacy auth
```

反之亦然。

如果底层 Session 是共享的：

必须验证其行为符合既有安全模型。

---

# 十七、完整 Route Matrix

建立：

```text
docs/WEBUI_V1_ROUTE_MATRIX.md
```

至少包含：

```text
Route
Name
Auth Required
Lazy Loaded
Page Title
Primary API
WS Topic
v1
legacy
Status
```

覆盖：

```text
全部 v1 routes
全部 legacy routes
```

---

# 十八、Router Guard 全量覆盖

W5 已通过代表性未登录测试。

W6 必须进一步改成：

> **所有 `requiresAuth` 路由都进行数据驱动的未登录守卫测试。**

不要只测试：

```text
/memory
```

---

# 十九、未登录 Route 测试

对所有：

```text
meta.requiresAuth === true
```

的 route：

测试：

```text
未登录
↓
访问 route
↓
redirect → /login
↓
redirect query 保留
```

---

# 二十、登录后回跳

测试：

```text
访问：

/social
```

未登录：

```text
/login?redirect=/social
```

登录后：

```text
→ /social
```

必须正常。

---

# 二十一、登录页自身

确认：

```text
/login
```

不产生：

```text
redirect loop
```

---

# 二十二、404

测试不存在 route：

```text
/not-found
```

必须：

```text
进入 v1 正确 NotFound
```

而不是：

```text
白屏
500
无限 redirect
```

---

# 二十三、SPA Refresh

逐类测试浏览器直接刷新：

```text
/ai
/character
/social
/memory
/abilities/tools
/system/logs
/system/runtime
```

必须：

```text
仍能正确进入对应页面
```

不能因为：

```text
history fallback
```

导致 404。

---

# 二十四、Deep Link

测试：

```text
/ai/providers?create=1
/ai/models?create=1
/system/settings?focus=...
```

浏览器直接打开：

必须仍然：

```text
正常解析
```

---

# 二十五、Page Title

所有核心页面：

必须：

```text
拥有明确 title
```

并且：

```text
不同页面不能全部叫 CatooBot WebUI
```

---

# 二十六、Navigation Smoke Test

至少验证：

```text
Dashboard
→ AI
→ Character
→ World
→ Social
→ Memory
→ Tools
→ Media
→ Agent
→ Logs
→ Runtime
→ Settings
```

每个页面：

```text
可进入
可返回
无 JS exception
```

---

# 二十七、浏览器 E2E

W5 状态：

```text
Playwright 未安装
```

W6：

> **必须再次尝试建立真实 Browser E2E。**

优先：

```text
Playwright
```

如果当前环境可以安装：

必须使用真实浏览器测试。

---

# 二十八、Browser E2E 最小范围

至少覆盖：

```text
登录
↓
Dashboard
↓
AI Setup
↓
Provider
↓
Model
↓
Role
↓
Settings
↓
Character
↓
World
↓
Social
↓
Memory
↓
Tools
↓
Logs
↓
Runtime
```

---

# 二十九、Browser E2E 不要求全部 CRUD

重点是：

```text
页面能打开
核心数据能加载
核心 mutation 能成功
危险操作有确认
登出正常
```

---

# 三十、如果 Playwright 仍然无法安装

不要伪造成功。

在最终报告明确：

```text
Browser E2E:
NOT AVAILABLE
```

并记录：

```text
安装失败原因
```

同时：

```text
route navigation tests
API integration tests
jsdom tests
```

必须完整通过。

---

# 三十一、JavaScript Runtime Error

Browser E2E 如果可用：

禁止存在：

```text
console.error
Unhandled Promise Rejection
Chunk load error
```

除非是明确允许的测试场景。

---

# 三十二、API Error Boundary

所有核心页面必须验证：

后端：

```text
500
401
403
404
409
422
```

不会导致：

```text
白屏
```

而应该进入：

```text
ErrorState
```

---

# 三十三、Global Error Handling

检查：

```text
API client
router
realtime
store
page component
```

是否存在统一错误处理。

禁止：

```text
catch(error) {}
```

吞掉错误。

---

# 三十四、Session 过期

模拟：

```text
WebUI 已打开
↓
session 失效
↓
API 返回 401
```

必须：

```text
清理前端 auth state
↓
跳转 login
```

不能：

```text
无限请求
```

---

# 三十五、CSRF Regression

所有写操作：

```text
POST
PUT
PATCH
DELETE
```

必须确保：

```text
CSRF 正常
```

测试：

```text
无 CSRF
→ 403
```

---

# 三十六、权限边界

确认：

```text
GET
```

和：

```text
mutation
```

都不能绕过 Session。

---

# 三十七、Dangerous Action Audit

逐类检查：

```text
Memory delete
Sticker delete
Expression delete
Tool test
Tool cache clear
World reset
World reinitialize
Runtime action
Agent control
Runtime tick
Router reset
Provider delete
Model delete
Raw Config Apply
```

必须：

```text
ConfirmDialog
+
后端 confirmation
```

双保险。

---

# 三十八、不能只靠前端 Confirm

危险接口本身也必须验证：

```text
没有 confirm
→ 409 / confirm_required
```

不能：

```text
前端没点按钮
但 API 直接执行
```

---

# 三十九、Secret Security Audit

完整扫描：

```text
API responses
logs
exceptions
frontend store
frontend bundle
debug payloads
WS payloads
HTML
SSR
```

不得出现：

```text
API Key
Password
Token
Credential Secret
```

明文。

---

# 四十、Character Bible Security Audit

特别搜索：

```text
character_bible
bible_source
真实人物设定文本
```

确保：

```text
不进入 public API
不进入 frontend bundle
不进入 source map
不进入 console
不进入 logs
```

---

# 四十一、Source Map

如果：

```text
source maps
```

被构建：

必须确认不会包含：

```text
Character Bible
Secret
private config
```

如果当前生产不需要：

优先关闭生产 source map。

---

# 四十二、Frontend Bundle Audit

运行：

```text
grep / ripgrep
```

搜索：

```text
OPENAI_API_KEY
API_KEY
TOKEN
PASSWORD
SECRET
character_bible
bible_source
```

检查：

```text
dist/
```

---

# 四十三、Backend Response Audit

对：

```text
/api/v1/config/*
/api/v1/credentials/*
/api/v1/character/*
/api/v1/runtime/*
```

检查：

```text
是否意外暴露内部 Secret
```

---

# 四十四、Debug Endpoint Audit

任何：

```text
debug
trace
raw
expert
```

接口：

必须明确：

```text
能看什么
不能看什么
```

不能因为 debug 模式绕过 secret policy。

---

# 四十五、API Contract Final Audit

重新检查：

```text
docs/WEBUI_API_CONTRACT.md
```

与实际：

```text
Backend
Frontend Types
Frontend API calls
Tests
```

必须一致。

---

# 四十六、禁止双 schema

如果仍存在：

```text
legacy shape
v1 shape
```

必须：

```text
明确区分 endpoint
```

不能：

```text
同一 endpoint 随机返回两种 schema
```

---

# 四十七、Pagination Audit

检查：

```text
Social
Memory
Tools
Stickers
Expressions
Logs
Agent
```

所有列表：

必须：

```text
正确处理 page
page_size
total
has_more
```

---

# 四十八、total = null

继续保持：

```text
total = null
```

时：

不要：

```text
猜 total
```

必须：

```text
还有更多
```

---

# 四十九、URL State

以下页面的主要筛选条件：

必须尽量可通过 URL 恢复：

```text
Memory
Social users
Commitments
Logs
Usage
```

不能所有筛选都在刷新以后丢失。

---

# 五十、Realtime Audit

只有：

```text
ONE websocket owner
```

检查：

```text
reconnect
heartbeat
dedup
backpressure
```

---

# 五十一、WS 重连

模拟：

```text
WS disconnect
↓
reconnect
↓
subscribe
```

必须：

```text
不会出现第二个 socket
```

---

# 五十二、WS Flood Protection

检查：

```text
log
narration
world
scheduler
```

是否：

```text
bounded
```

不能无限增长：

```text
memory
array
queue
```

---

# 五十三、Log Buffer

必须保持：

```text
max 500
```

或当前真实上限。

测试超过上限：

```text
旧数据淘汰
```

不能无限增长。

---

# 五十四、World Refresh

World 页面继续保持：

```text
WS world event
↓
throttle
↓
REST snapshot
```

而不是：

```text
每秒 polling
```

---

# 五十五、No Polling Regression

全局搜索：

```text
setInterval
setTimeout
poll
```

确认没有新增：

```text
每秒请求 API
```

---

# 五十六、Scheduler 与 WebUI

确认：

```text
WebUI
```

没有创建：

```text
第二套 scheduler
```

---

# 五十七、Runtime Tick

WebUI 的：

```text
manual tick
```

必须：

```text
确认
```

并最终走：

```text
Runtime Service
```

不能自己修改 World。

---

# 五十八、Mutation Server Truth Audit

以下所有 mutation：

```text
save
↓
server response
↓
invalidate
↓
refetch
```

不能：

```text
local assignment
```

作为最终状态。

---

# 五十九、重点检查 mutation

至少：

```text
Provider
Model
Role
Failover
Config
Character state
Memory
Social notes
Tool permissions
Sticker
Expression
Agent control
World control
Runtime actions
```

---

# 六十、Optimistic UI

检查是否存在：

```text
假成功
```

例如：

```text
delete
→ frontend immediately removes
→ backend later failed
```

这种逻辑优先移除。

---

# 六十一、Error Recovery

测试：

```text
mutation success
mutation 409
mutation 422
mutation timeout
mutation 500
```

每种情况：

```text
UI 状态正确
```

---

# 六十二、Dirty State

重点检查：

```text
Settings
Raw YAML
Failover
Forms
```

在：

```text
route change
refresh
close
```

时：

不会意外丢失重要未保存数据。

---

# 六十三、Config Center

重点最终验证：

```text
Basic
Advanced
Expert
```

全部可用。

---

# 六十四、Config Effective Truth

测试：

```text
修改 hot field
```

确认：

```text
effective state changed
```

---

# 六十五、Restart Required

测试：

```text
restart-required field
```

确认：

```text
saved
+
pending restart
+
runtime old value remains
```

---

# 六十六、Unused Config

Expert：

必须：

```text
visible
+
clearly marked
```

而不是：

```text
silently treated as working
```

---

# 六十七、AI End-to-End

必须至少完成一次：

```text
Provider
↓
Credential
↓
Model
↓
Chat Role
↓
AI Ready
↓
real router call
```

使用：

```text
fake provider
或测试 Provider
```

绝对不要提交真实 API key。

---

# 六十八、AI Failover

必须重新验证：

```text
Model A
↓
429
↓
Model B
```

确认 fallback：

```text
由 Model Router 执行
```

而不是：

```text
WebUI
```

---

# 六十九、AI Cooldown

必须确认：

```text
429
↓
cooldown
↓
UI 显示真实剩余时间
```

不能由 frontend 自己构建第二套 cooldown state。

---

# 七十、Memory Integrity

WebUI Memory mutation：

必须不会：

```text
破坏 Memory identity
```

不会：

```text
制造重复 episode
```

不会：

```text
绕过 provenance
```

---

# 七十一、Social Integrity

Social 页面中的：

```text
nickname
notes
tags
initiative
```

修改必须走：

```text
Social Admin Service
```

禁止：

```text
直接写数据库
```

---

# 七十二、Relationship

当前 W5：

```text
read-only
```

W6 不要擅自开放：

```text
relationship write
```

---

# 七十三、Commitment

当前：

```text
read-only
```

W6 不要增加：

```text
人工修改承诺
```

---

# 七十四、Character

Character 页面继续：

```text
只展示 Runtime state
```

不得泄漏：

```text
Character Bible
```

---

# 七十五、World

World 页面：

必须继续：

```text
真实 Runtime state
```

不得生成：

```text
fake progress
fake trend
fake timeline
```

---

# 七十六、Timeline

确认：

```text
每秒 tick
```

不会变成：

```text
每秒一条 timeline record
```

---

# 七十七、Tool Test

确认：

```text
tool test
```

仍然明确：

```text
真的发起外部请求
```

同时：

```text
不会发送 QQ 消息
```

---

# 七十八、Agent

当前 W5：

```text
real Agent Runtime capability
```

W6：

只做：

```text
integration verification
```

不要新增：

```text
新的 Agent planner
```

---

# 七十九、Logs

Logs：

必须继续：

```text
server tail
+
single WS
+
local bounded buffer
```

清空：

```text
只清 local view
```

---

# 八十、Runtime

Runtime 页面：

必须明确：

```text
scheduler interval
≠
world update frequency
```

---

# 八十一、Dashboard

Dashboard 最终应当：

```text
不包含假数字
```

所有：

```text
AI
Social
World
Memory
Runtime
```

必须来自真实 API。

---

# 八十二、断线状态

WS 断开：

Dashboard：

应该明确：

```text
数据可能不是最新
```

但不能：

```text
清空全部状态
```

---

# 八十三、Loading / Empty / Error 三态

对每个主要数据组件：

必须至少存在：

```text
Loading
Empty
Error
Success
```

不能：

```text
null
→
白屏
```

---

# 八十四、Accessibility

W6 做一次全局审查：

```text
button label
form label
aria
keyboard
focus
contrast
semantic heading
```

---

# 八十五、Keyboard

至少：

```text
Tab
Enter
Escape
Arrow
```

在：

```text
Modal
Drawer
Navigation
Forms
ConfirmDialog
```

中行为正常。

---

# 八十六、Responsive

测试至少：

```text
Desktop
1280+
```

```text
Tablet
768~1024
```

```text
Mobile
<= 767
```

---

# 八十七、关键移动页面

必须重点检查：

```text
Dashboard
AI
Model
Character
Social
Memory
Logs
Runtime
Settings
```

不能出现：

```text
横向溢出
按钮不可点击
表格无法操作
drawer 出屏
modal 出屏
```

---

# 八十八、Performance Baseline

记录：

```text
dist size
gzip size
main chunk
largest chunks
```

与 W5：

```text
1.2M
124 assets
main 279kB
gzip 88kB
```

比较。

---

# 八十九、性能原则

W6 不需要重构整个 frontend。

只有发现明确问题才处理：

```text
重复加载
巨大公共 chunk
不必要 eager import
重复 API request
无限 reactive update
```

---

# 九十、Lazy Loading Audit

确认核心 domain page：

```text
lazy loaded
```

避免：

```text
所有页面进入 main bundle
```

---

# 九十一、API Request Audit

进入页面：

不得出现：

```text
同一个 endpoint
瞬间请求 2~5 次
```

除非确有必要。

---

# 九十二、Mutation 重复提交

测试：

```text
连续双击 Save
```

应该：

```text
只产生一次 mutation
```

或至少：

```text
第二次被 loading state 阻止
```

---

# 九十三、WebSocket 重复订阅

切换：

```text
Dashboard
→ Logs
→ Dashboard
```

不会：

```text
重复订阅同一 topic
```

---

# 九十四、Store 审查

当前原则：

```text
stores/
    ai
    config
    credentials
    world
    social
    memory
    realtime
```

除此之外：

页面局部状态即可。

不要在 W6：

```text
机械新增 Store
```

---

# 九十五、禁止 Store 成为业务层

Store 只负责：

```text
fetch
cache
invalidate
```

不要把：

```text
Core business logic
```

搬进 Store。

---

# 九十六、API Client

确认：

```text
所有 API request
```

都通过：

```text
single API client
```

禁止：

```text
page.vue
直接 fetch()
```

---

# 九十七、Raw fetch Audit

全局搜索：

```text
fetch(
axios
XMLHttpRequest
WebSocket
```

确认只有：

```text
API client
Realtime store
```

拥有真正网络逻辑。

---

# 九十八、WebSocket Audit

确认：

不存在：

```text
new WebSocket(...)
```

分散在页面中。

---

# 九十九、Frontend Security

检查：

```text
v-html
innerHTML
dangerouslySetInnerHTML
```

默认禁止。

如果确实存在：

必须证明：

```text
内容经过安全处理
```

---

# 一百、URL 参数安全

检查：

```text
redirect
query
search
id
name
```

不能：

```text
注入脚本
```

不能产生：

```text
open redirect
```

---

# 一百零一、API Response Rendering

后端返回的：

```text
description
message
name
notes
```

默认作为：

```text
text
```

而不是：

```text
HTML
```

---

# 一百零二、Build Audit

执行：

```bash
cd webui
npm ci
npm run typecheck
npm run test
npm run build
```

---

# 一百零三、Python Audit

执行：

```bash
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe app
.venv\Scripts\python.exe -m pytest -q
```

具体命令以实际项目为准。

---

# 一百零四、Full Test Requirement

W6 不接受：

```text
只跑 webui test
```

必须：

```text
Frontend
+
Backend
+
Integration
```

全量通过。

---

# 一百零五、回归基线

W5 报告：

```text
frontend:
279 passed

pytest:
1664 passed
```

W6：

> 如果新增测试，数字可以增加。

但：

```text
不得无理由减少
```

如果减少：

必须在报告解释：

```text
删除了什么
为什么
由什么测试替代
```

---

# 一百零六、CI

必须最终获得：

```text
CI = success
```

至少包含：

```text
Python lint
Python format
Python mypy
Python pytest
WebUI typecheck
WebUI test
WebUI build
```

---

# 一百零七、CI Warnings

如果 CI 存在：

```text
Node version warning
Ubuntu migration warning
bundle mismatch warning
```

W6 不要求为了完成 W6 全部解决。

但必须：

```text
记录
```

并判断：

```text
blocking / non-blocking
```

---

# 一百零八、Git 安全

绝对禁止：

```text
force push
rebase main
history rewrite
filter-repo
BFG
reset --hard remote
```

不要破坏历史。

---

# 一百零九、W6 Commit Strategy

建议：

```text
webui-v1/w6-audit-hardening
webui-v1/w6-security
webui-v1/w6-integration
webui-v1/w6-e2e
webui-v1/w6-cutover
```

也可以根据实际修改量合并。

要求：

```text
每个 commit 都尽量可回滚
```

---

# 一百一十、禁止大规模重构

除非：

```text
发现阻止发布的架构问题
```

否则不要：

```text
重写 router
重写 API client
重写所有 Store
重写 UI shell
重写 aiohttp server
```

---

# 一百一十一、已知限制处理

W5 已知限制：

```text
Playwright 未安装
preview_url = null
记忆纠正仍为旧控制台
embeddings/consolidation 重操作仍为旧控制台
角色导入导出不迁移
relationship/commitment read-only
旧 /tools/{name} 通配行为
spaces 字段兼容
```

W6：

除非是：

```text
P0/P1
```

否则不要强行解决。

---

# 一百一十二、Legacy 工具路由问题

W5 已知：

```text
旧 /tools/{name}
```

可能遮蔽其子页。

W6：

不要为了修旧 SSR 行为而破坏：

```text
v1
```

如果不影响 v1：

保持现状。

最终报告记录：

```text
known legacy limitation
```

---

# 一百一十三、Migration Map 最终更新

W6 完成前：

更新：

```text
docs/WEBUI_V1_MIGRATION_MAP.md
```

状态应清晰：

```text
migrated
kept
legacy
partially migrated
dispatched
dropped
```

并注明：

```text
W6 final status
```

---

# 一百一十四、W6 Final Architecture Document

创建：

```text
docs/WEBUI_V1_FINAL_ARCHITECTURE.md
```

至少描述：

```text
Browser
↓
SPA
↓
API Client
↓
/api/v1
↓
Admin / Domain Services
↓
Core
```

以及：

```text
Realtime
Session
CSRF
Config
AI
Domain
Legacy
```

---

# 一百一十五、正式发布原则

W6 完成后：

```text
v1 = default
```

而：

```text
legacy = fallback
```

---

# 一百一十六、Rollback

必须保留：

```text
web.version = v0.8
```

作为 rollback。

测试：

```text
切换 v1
↓
运行
↓
切换 v0.8
↓
运行
```

不能因为 v1 的新 API 而导致 v0.8 无法启动。

---

# 一百一十七、Rollback 不需要自动化

W6 不强制增加：

```text
自动 rollback
```

只需要保证：

```text
配置切换
```

真实可行。

---

# 一百一十八、Default Cutover

最终生产默认：

```yaml
web:
  version: v1
```

如果配置文件实际结构不同：

以项目真实 Config Registry 为准。

---

# 一百一十九、切换后的人工验收

必须实际走：

```text
启动
↓
登录
↓
Dashboard
↓
AI
↓
Character
↓
World
↓
Social
↓
Memory
↓
Tools
↓
Media
↓
Agent
↓
Logs
↓
Runtime
↓
Settings
↓
Logout
```

---

# 一百二十、真实 Core Smoke

至少验证：

```text
Character state
World snapshot
Memory read
Social read
Tool list
Agent status
Runtime status
AI status
OneBot status
```

这些都必须来自真实 Backend。

---

# 一百二十一、QQ / OneBot 回归

W6 不需要重新开发 QQ 层。

但至少做：

```text
QQ message
↓
OneBot
↓
CatooBot
↓
正常回复
```

确认：

```text
WebUI v1
```

没有破坏：

```text
OneBot Gateway
Conversation Runtime
Response Runtime
```

---

# 一百二十二、WebUI Admin 操作不能污染 QQ

执行：

```text
AI Test
Tool Test
Agent Simulation
World Admin Action
```

不能意外：

```text
发送 QQ 消息
```

除非操作本身明确设计为会影响 QQ。

---

# 一百二十三、数据一致性

W6 必须确认：

```text
WebUI mutation
=
Backend actual state
=
Runtime actual state
```

不能出现：

```text
WebUI says success
but Runtime still old
```

---

# 一百二十四、Restart-required

确认：

```text
页面显示 restart required
```

不是：

```text
页面显示成功
但 Runtime 已偷偷修改
```

---

# 一百二十五、Real-time Consistency

对于：

```text
World
Logs
Runtime
AI
```

确认：

```text
WS
```

只是：

```text
notification / refresh trigger
```

最终仍：

```text
REST/API = authoritative snapshot
```

---

# 一百二十六、测试数据隔离

所有测试：

禁止污染：

```text
production Character
production Memory
production Relationship
production QQ
```

优先使用：

```text
fake services
temporary DB
test fixtures
```

---

# 一百二十七、测试 Secret

绝对不能：

```text
commit real API key
```

不能：

```text
echo secret
```

进入：

```text
CI logs
```

---

# 一百二十八、Fixture Audit

测试 fixture 中：

不能存在：

```text
真实 token
真实 password
真实 private URL
```

---

# 一百二十九、文件泄露扫描

检查：

```text
git ls-files
```

以及：

```text
git grep
```

确认：

```text
真实 Character Bible
.env
secrets.json
API key
```

没有错误进入 public repo。

---

# 一百三十、Dist Leak Audit

检查：

```text
webui/dist
```

不得包含：

```text
secret
character bible
private file path
internal credentials
```

---

# 一百三十一、日志 Leak Audit

执行：

```text
grep / ripgrep
```

检查：

```text
API Key
password
Authorization
Bearer
Cookie
```

不能进入：

```text
normal logs
```

---

# 一百三十二、WebUI Console Audit

正式运行：

```text
浏览器 DevTools Console
```

不得出现：

```text
Unhandled error
Vue warning
router error
chunk error
```

---

# 一百三十三、Network Audit

正式运行：

检查：

```text
重复 request
失败 retry storm
401 loop
WS reconnect storm
```

---

# 一百三十四、Memory / CPU Audit

运行 WebUI：

至少观察：

```text
5 分钟
```

检查：

```text
CPU
Memory
WS messages
DOM node growth
log buffer
```

不得：

```text
持续线性增长
```

---

# 一百三十五、长时间运行

如果条件允许：

进行：

```text
30~60 分钟
```

的 WebUI 开放运行测试。

重点观察：

```text
Realtime
Logs
World
Memory
DOM
```

是否持续增长。

如果无法进行长时间运行：

最终报告说明。

---

# 一百三十六、最终代码清理

删除：

```text
dead code
unused imports
temporary debug
console.log
TODO test hacks
```

但不要删除：

```text
有意义的 TODO
未来明确保留的 extension points
```

---

# 一百三十七、文档清理

检查：

```text
W1
W2
W3
W4
W5
W6
```

文档之间不能存在：

```text
明显互相矛盾
```

---

# 一百三十八、版本语义

最终所有文档统一：

```text
WebUI v1.0
```

不要：

```text
v0.9
v1 beta
v1 preview
```

混用。

---

# 一百三十九、最终信息架构

最终应该稳定为：

```text
Dashboard

AI 与模型
├─ 概览
├─ Provider
├─ 模型
├─ 模型用途
├─ 故障转移
├─ 用量
└─ 测试

角色与世界
├─ Character
├─ World
└─ Timeline

社交
├─ Users
├─ Groups
├─ Relationships
├─ Commitments
└─ Sessions

Memory
├─ Explorer
├─ Timeline
└─ Health

Abilities
├─ Tools
├─ Media
└─ Agent

System
├─ Logs
├─ Runtime
├─ Settings
└─ Credentials
```

具体菜单名称以现有 v1 implementation 为准，不为了文档强行改 UI。

---

# 一百四十、W6 绝不新增的功能

禁止在 W6 新增：

```text
新的 Memory Engine
新的 Social Engine
新的 Agent Engine
新的 Tool Engine
新的 AI Router
新的 Runtime Scheduler
新的 Credential Store
新的 Config Store
```

---

# 一百四十一、W6 允许修复

可以修复：

```text
P0
P1
明显 regression
security issue
contract mismatch
UI broken state
route bug
realtime bug
明显 performance bug
```

---

# 一百四十二、W6 不主动优化

不要为了“看起来更高级”而：

```text
加入新动画
加入复杂 dashboard
加入复杂图表
加入新主题
加入新的 AI workflow
加入额外 analytics
```

---

# 一百四十三、W6 Final Test Matrix

创建：

```text
docs/WEBUI_V1_FINAL_TEST_MATRIX.md
```

至少覆盖：

```text
Authentication
Authorization
CSRF
Routes
Navigation
API
AI
Config
Character
World
Social
Memory
Tools
Media
Agent
Logs
Runtime
Realtime
Legacy
Responsive
Accessibility
Security
Performance
Build
CI
```

---

# 一百四十四、测试结果状态

每个项目：

使用：

```text
PASS
FAIL
BLOCKED
N/A
```

不能只写：

```text
done
```

---

# 一百四十五、BLOCKED 必须有说明

例如：

```text
Browser E2E
BLOCKED

原因：
Playwright 安装环境失败
```

而不是：

```text
PASS
```

---

# 一百四十六、Final Security Checklist

最终必须：

```text
✅ Session protected
✅ CSRF protected
✅ Secret masked
✅ No secret in bundle
✅ No Bible in bundle
✅ No Bible in API
✅ Dangerous actions confirmed
✅ Backend confirmation enforced
✅ No open redirect
✅ No XSS path
✅ No debug leak
```

---

# 一百四十七、Final Architecture Checklist

必须：

```text
✅ single API client
✅ single realtime owner
✅ no second AI Router
✅ no second Config Store
✅ no second Credential Store
✅ no second Scheduler
✅ no direct DB writes from pages
✅ Server truth after mutation
✅ Domain Services remain authoritative
```

---

# 一百四十八、Final Product Checklist

必须：

```text
✅ v1 default
✅ legacy preserved
✅ rollback possible
✅ all major routes work
✅ all major APIs work
✅ all dangerous actions protected
✅ AI configurable
✅ Config observable
✅ World realtime
✅ Logs realtime
✅ Memory browsable
✅ Social browsable
✅ Tools browsable
✅ Agent observable
✅ Runtime observable
```

---

# 一百四十九、W6 最终完成定义

只有同时满足以下条件，才能宣布：

```text
W6 = COMPLETE
```

必须：

```text
✅ W5 baseline verified
✅ W6 audit completed
✅ P0 = 0
✅ P1 = 0
✅ API contract final
✅ route matrix final
✅ migration map final
✅ security audit pass
✅ secret audit pass
✅ Character Bible audit pass
✅ all auth guards tested
✅ SPA refresh tested
✅ deep links tested
✅ 404 tested
✅ realtime tested
✅ dangerous actions tested
✅ server truth tested
✅ AI E2E tested
✅ Config hot reload tested
✅ restart-required tested
✅ legacy v0.8 tested
✅ v1 default verified
✅ rollback verified
✅ responsive checked
✅ accessibility checked
✅ performance baseline recorded
✅ frontend tests pass
✅ pytest pass
✅ ruff pass
✅ mypy pass
✅ typecheck pass
✅ build pass
✅ CI success
✅ working tree clean
```

---

# 一百五十、关于 Browser E2E 的完成条件

如果 Playwright 成功安装：

必须：

```text
Browser E2E = PASS
```

如果 Playwright 仍然因为环境原因无法安装：

W6 可以完成，但必须：

```text
Browser E2E = BLOCKED
```

同时：

```text
route tests
API tests
integration tests
jsdom tests
```

全部通过。

不能把：

```text
BLOCKED
```

写成：

```text
PASS
```

---

# 一百五十一、最终 Cutover

完成所有测试后：

确认：

```text
web.version = v1
```

重新启动。

实际打开：

```text
/
```

确认进入：

```text
WebUI v1
```

---

# 一百五十二、Cutover 后必须再次测试

至少：

```text
Login
Dashboard
AI
Character
World
Social
Memory
Tools
Media
Agent
Logs
Runtime
Settings
Logout
```

---

# 一百五十三、不要在 Cutover 后继续开发

当：

```text
v1 default
```

并且：

```text
全部完成测试
```

之后：

**立即停止。**

不要继续：

```text
“顺便再优化一下”
“顺便加一个功能”
“顺便重构”
```

---

# 一百五十四、最终 Git

检查：

```bash
git status
git branch --show-current
git log -5 --oneline
git rev-parse HEAD
git rev-parse origin/main
```

最终必须：

```text
working tree clean
HEAD == origin/main
```

如果项目还有：

```text
mirror repository
```

也检查 mirror。

---

# 一百五十五、最终 Commit

最终 commit message 建议：

```text
webui-v1: finalize v1 integration and cutover
```

或：

```text
webui-v1: complete final integration and default cutover
```

不要使用：

```text
fix stuff
final final
test
temp
```

---

# 一百五十六、最终报告

完成后输出：

```text
# WebUI v1.0 · W6 Final Integration / Cutover Report
```

必须包含：

```text
1. W5 baseline
2. W6 final commit
3. Audit summary
4. P0 / P1 findings
5. Fixed issues
6. API contract verification
7. Route matrix result
8. Auth guard result
9. SPA refresh result
10. Deep-link result
11. Browser E2E
12. API integration
13. AI integration
14. Config integration
15. Security audit
16. Secret audit
17. Character Bible audit
18. Realtime audit
19. Server-truth mutation audit
20. Legacy verification
21. Rollback verification
22. Responsive verification
23. Accessibility verification
24. Performance baseline
25. frontend tests
26. pytest
27. ruff
28. mypy
29. typecheck
30. build
31. CI
32. known limitations
33. blocked items
34. final route status
35. final web.version
36. final Git status
37. HEAD / origin/main
38. mirror status
```

---

# 一百五十七、最终报告不能夸大

例如：

如果没有真实浏览器：

不要写：

```text
Browser E2E PASS
```

如果只测了一个代表性 route：

不要写：

```text
all routes tested
```

如果某项只是静态检查：

写：

```text
static audit PASS
```

不要写：

```text
runtime verified
```

---

# 一百五十八、最终输出中的质量等级

最后增加：

```text
Release Status:

READY
或
READY WITH KNOWN LIMITATIONS
或
BLOCKED
```

如果：

```text
P0 > 0
```

必须：

```text
BLOCKED
```

如果：

```text
P0 = 0
P1 > 0
```

也必须：

```text
BLOCKED
```

如果：

```text
P0 = 0
P1 = 0
```

即使存在：

```text
P2 / P3 / Browser E2E BLOCKED
```

也可以：

```text
READY WITH KNOWN LIMITATIONS
```

但必须明确：

```text
哪些限制不会影响正式使用
```

---

# 一百五十九、最终产品状态

W6 成功后：

```text
CatooBot WebUI v1.0
```

正式形成：

```text
                    CatooBot WebUI v1.0
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
         AI / Config      Domain        Runtime
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                        /api/v1
                             │
                ┌────────────┼────────────┐
                ▼            ▼            ▼
             Admin         Domain       Realtime
            Services      Services       WS
                │            │            │
                └────────────┼────────────┘
                             ▼
                       CatooBot Core
```

旧版：

```text
WebUI v0.8
```

则成为：

```text
Legacy / Rollback
```

---

# 一百六十、最终硬性要求

1. 不重写稳定 Core。
2. 不新增第二套业务真相。
3. 不新增第二个 WebSocket。
4. 不新增第二个 Scheduler。
5. 不新增第二个 AI Router。
6. 不新增第二个 Config Store。
7. 不新增第二个 Credential Store。
8. 不直接从 Vue 页面访问数据库。
9. 不让前端伪造 Runtime 状态。
10. 不让前端伪造 AI 状态。
11. 不隐藏后端错误。
12. 不吞异常。
13. 不降低测试覆盖。
14. 不删除失败测试来获得 PASS。
15. 不绕过 Session。
16. 不绕过 CSRF。
17. 不绕过 Dangerous Action Confirmation。
18. 不暴露 Secret。
19. 不暴露 Character Bible。
20. 不增加 QQ 管理命令。
21. 不改变用户正常聊天体验。
22. 不让 WebUI 管理操作自动发送 QQ 消息。
23. 不删除 v0.8。
24. 不删除 legacy route。
25. 不修改 Git history。
26. 不 force push。
27. 不 rebase main。
28. 不进行 history rewrite。
29. 不在 W6 引入新的大功能。
30. 完成 Cutover 后立即停止。

---

# 一百六十一、W6 最终目标

W5 解决的是：

```text
“WebUI 页面有没有？”
```

W6 要解决的是：

```text
“这个 WebUI 能不能正式作为 CatooBot 的默认管理入口？”
```

最终必须达到：

```text
能启动
+
能登录
+
能导航
+
能配置
+
能查看真实状态
+
能执行受控管理操作
+
能实时更新
+
能处理错误
+
能安全处理 Secret
+
能安全处理 Character Bible
+
能回退到 legacy
+
不破坏 Runtime
+
不破坏 QQ
+
测试通过
+
CI 通过
```

---

# 一百六十二、现在开始

首先：

```text
读取 W1/W2/W3/W4/W5 文档
↓
读取 W5 当前源码
↓
生成 W6 Audit
↓
分类 P0/P1/P2/P3
```

然后：

```text
只修复 P0/P1
↓
补全最终测试
↓
做 Security Audit
↓
做 Browser E2E
↓
做 Realtime / API / Server Truth Audit
↓
验证 Legacy
↓
切换 v1 default
↓
完整回归
↓
生成最终报告
```

不要提前进入：

```text
W7
W8
新的 Domain 功能
新的 Agent 能力
新的 Core 架构
```

---

# 一百六十三、最终停止条件

当最终状态达到：

```text
W1 Audit
✅

W2 Backend API
✅

W3 Frontend Shell
✅

W4 AI & Configuration
✅

W5 Domain Pages
✅

W6 Final Integration / Cutover
✅
```

立即停止。

不要继续开发下一阶段。

# 最核心的一句话

> **W6 不是继续把 WebUI 做大，而是证明它已经足够稳定、安全、真实、一致，可以从“开发中的新 WebUI”正式变成 CatooBot v2.1 的默认 WebUI。**