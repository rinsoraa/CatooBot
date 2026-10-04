# CatooBot v2.1 · WebUI v1.0 — W2 Backend API Foundation

## 0. 当前基线

W1 审计已经完成并通过 CI。

当前基线：

```text
commit:
3478b0a2c676ac446236a9acdd762bdf365fd3aa

branch:
main

tests:
1430 passed

ruff:
PASS

ruff format:
PASS

mypy:
PASS

CI:
success

working tree:
clean
```

W1 已生成：

```text
docs/WEBUI_V1_AUDIT.md
docs/WEBUI_CONFIG_MATRIX.md
docs/WEBUI_API_CONTRACT.md
```

其中：

> `docs/WEBUI_API_CONTRACT.md` 是 W2 的唯一 API 契约依据。

**开始 W2 前必须重新读取这三个文档，尤其完整读取 `docs/WEBUI_API_CONTRACT.md`，并严格以实际源码与契约为准。**

---

# 1. W2 任务边界

本阶段名称：

> **WebUI v1.0 · W2 Backend API Foundation**

本阶段只实现：

```text
/api/v1
    ↓
JSON API
    ↓
现有 Admin Services / Config System / AI Router
```

核心目标：

```text
Browser / future Vue
        ↓
    /api/v1
        ↓
WebUI API Layer
        ↓
Existing Services
        ↓
CatooBot Core
```

---

# 2. 本阶段明确禁止做的事情

W2 **不要开始 Vue / React / 前端 UI 重写**。

禁止：

```text
❌ package.json
❌ Vue 页面
❌ Vite 页面
❌ 删除旧 WebUI
❌ 重写 ui.py
❌ 重写现有 routes
❌ 修改 Sidebar
❌ 迁移旧页面
❌ 新建前端组件库
```

也禁止借机修改：

```text
❌ Sandbox 核心语义
❌ Goal
❌ ActionInstance
❌ NeedSystem
❌ Relationship
❌ Commitment
❌ Memory
❌ ConversationRuntime
❌ ExternalInfluenceEvaluator
❌ OneBot Gateway
❌ RuntimeScheduler
```

除非某个 `/api/v1` 所需的**最小只读访问器/管理接口确实缺失**，才允许增加最小兼容能力。

不得因为 WebUI API 而改变运行时语义。

---

# 3. W2 的核心原则

必须保持：

```text
WebUI API
    = 管理层 / 适配层

CatooBot Core
    = 真正的业务事实

Existing Admin Services
    = 业务操作入口

Frontend
    = 后续 W3/W4
```

WebUI API 不允许直接：

```python
database.execute(...)
sandbox._private_field = ...
runtime._internal_state = ...
```

除非现有 Admin Service 本身就是该操作的正式入口。

优先：

```text
Route
 ↓
Schema / validation
 ↓
Admin Service
 ↓
Core
```

---

# 4. 首先审计 W1 契约与现有 Service

开始编码前必须先检查：

```text
docs/WEBUI_API_CONTRACT.md
docs/WEBUI_V1_AUDIT.md
docs/WEBUI_CONFIG_MATRIX.md
```

然后检查现有：

```text
app/web/server.py
app/web/services/
app/config/
app/ai/
app/runtime/
```

重点确认：

```text
ConfigAdminService
现有 Model / Provider 管理能力
Credential 处理
CSRF 处理
Session/Auth
RealtimeHub / WebSocket
现有 domain service
```

不要重复实现已有业务逻辑。

---

# 5. API 根路径

统一使用：

```text
/api/v1
```

所有新的 WebUI JSON API 必须进入这个命名空间。

禁止新增：

```text
/api/foo
/api/config2
/api/new-model
```

这种没有版本层级的接口。

---

# 6. 统一 JSON Response Envelope

严格按照：

```text
docs/WEBUI_API_CONTRACT.md
```

定义的 response envelope 实现。

如果契约定义的是：

```json
{
  "ok": true,
  "data": {}
}
```

就统一遵守。

错误同理：

```json
{
  "ok": false,
  "error": {
    "code": "...",
    "message": "...",
    "details": {}
  }
}
```

不要不同 endpoint 各自设计格式。

---

# 7. HTTP 状态码必须统一

按照契约落实：

```text
200
201
204
400
401
403
404
409
422
429
500
503
```

实际只使用需要的状态码。

特别区分：

```text
401
未登录 / session 无效

403
已登录但没有权限 / CSRF / action 不允许

404
资源不存在

409
状态冲突 / 唯一键冲突 / stale config

422
字段校验失败

503
依赖服务不可用
```

不要全部返回 200 再靠：

```json
{"success": false}
```

判断。

---

# 8. CSRF

W1 已经明确：

> SPA / JSON API 需要独立 CSRF bootstrap。

实现：

```text
GET /api/v1/csrf
```

具体返回格式以：

```text
docs/WEBUI_API_CONTRACT.md
```

为准。

要求：

- 不暴露 Session secret；
- token 必须绑定当前 session；
- 写操作必须验证；
- GET / HEAD / OPTIONS 不应要求写操作 CSRF；
- 兼容现有 WebUI 登录系统；
- 不破坏旧 SSR 页面。

新增 API 必须统一经过 CSRF middleware / decorator / helper。

不要每个 route 自己写：

```python
if request...:
```

---

# 9. Auth / Permission

所有：

```text
POST
PUT
PATCH
DELETE
```

以及任何状态变更 API：

必须：

```text
authenticated
+
authorized
+
CSRF validated
+
schema validated
```

只读 API 也必须遵循现有 Session 访问控制。

不要为了“方便未来 Vue”而把 `/api/v1` 做成匿名 API。

---

# 10. 建议的代码结构

根据当前项目实际结构选择最兼容的方案。

目标结构可以接近：

```text
app/web/
├── api/
│   ├── __init__.py
│   ├── common.py
│   ├── csrf.py
│   ├── status.py
│   ├── config.py
│   ├── providers.py
│   ├── models.py
│   ├── credentials.py
│   ├── runtime.py
│   └── domain.py
│
├── schemas/
│   ├── common.py
│   ├── config.py
│   ├── ai.py
│   ├── runtime.py
│   └── domain.py
```

但：

> 不要求机械创建上述所有文件。

以当前代码组织和 W1 契约为准。

重点是：

```text
API layer
≠
business service
```

---

# 11. Config Registry

这是 W2 的核心。

必须建立统一的：

> **Configuration Registry**

它不是新的 config storage。

它只负责描述：

```text
key
label
description
type
default
constraints
category
level
hot_reload
restart_required
usage_status
sensitive
```

以及 WebUI 如何理解这个字段。

---

# 12. Registry 的真实数据来源

不能：

```text
手工复制一份 settings.py
```

然后长期漂移。

Registry 应尽可能从：

```text
现有 Config schema
+
明确的 UI metadata
```

构成。

允许增加 metadata，例如：

```python
ConfigField(
    key="ai.temperature",
    label="默认温度",
    description="...",
    category="ai",
    level="basic",
    hot_reload=True,
    restart_required=False,
)
```

但绝不能让 Registry 自己成为第二套实际配置。

---

# 13. Registry 必须区分状态

至少支持：

```text
ACTIVE
ACTIVE_WITH_RESTART
CONDITIONALLY_USED
DEFINED_BUT_UNUSED
LEGACY
```

对于：

```text
DEFINED_BUT_UNUSED
LEGACY
```

默认不要出现在 Basic 配置 API。

但允许在：

```text
Expert / diagnostic
```

查询。

不能隐藏事实。

---

# 14. Config Schema API

实现契约规定的：

```text
GET /api/v1/config/schema
```

返回前端渲染配置表单所需的信息。

每个字段至少具备：

```text
key
label
description
type
current/effective value
default
category
level
hot_reload
restart_required
usage_status
sensitive
constraints
```

注意：

> Secret 字段绝对不能返回实际值。

---

# 15. Effective Config API

实现：

```text
GET /api/v1/config/effective
```

它回答：

> CatooBot **现在实际使用的配置是什么？**

必须考虑：

```text
environment
overrides.yaml
config.yaml
models section
defaults
```

实际优先级以 W1 `WEBUI_CONFIG_MATRIX.md` 为准。

不能简单：

```python
settings.model_dump()
```

就宣称这是 Effective Config。

---

# 16. Sensitive Config

例如：

```text
API Key
Secret
Password
Token
Cookie
```

Effective Config API：

不能返回明文。

例如：

```json
{
  "key": "ai.providers.workbuddy.api_key",
  "configured": true,
  "masked": "••••••••7a2f"
}
```

具体格式按照契约。

---

# 17. Config 来源

Config API 必须能告诉 UI：

```text
effective value
source
```

来源至少覆盖当前项目真实存在的：

```text
environment
overrides
config
models
default
```

例如：

```json
{
  "key": "ai.default_model",
  "value": "glm-5.3",
  "source": "overrides"
}
```

不能让前端自己猜来源。

---

# 18. Config 修改 API

实现契约规定的：

```text
POST/PATCH /api/v1/config/...
```

具体 endpoint 以：

```text
WEBUI_API_CONTRACT.md
```

为准。

原则：

```text
request
 ↓
Pydantic/schema validation
 ↓
ConfigAdminService
 ↓
existing config persistence
 ↓
hot reload or restart warning
 ↓
effective state response
```

---

# 19. Config 修改必须支持 Preview / Apply

如果契约已经定义：

```text
validate
preview
apply
```

严格实现。

用户修改：

```text
ai.default_model
runtime.xxx
```

后端应能明确告诉：

```text
哪些变化
哪些立即生效
哪些下次启动生效
哪些非法
```

---

# 20. 不要实现“假热更新”

这是强制要求。

例如某配置：

```text
hot_reload=False
```

那么 API 返回：

```text
saved=true
effective=false
restart_required=true
```

而不是：

```text
success=true
effective=true
```

只有真正 Runtime 已经应用的配置才能标记：

```text
effective=true
```

---

# 21. Config 写盘安全

任何：

```text
config.yaml
overrides.yaml
.env
```

写入操作都必须考虑：

```text
validation
atomic write
backup / recovery where appropriate
```

尤其 W1 已发现 `.env` 存在**非原子写入问题**。

W2 必须修复与 WebUI Credential 写入相关的这一部分。

要求：

```text
write temp
flush
replace atomically
```

并根据 Windows/Linux 两侧实际环境验证。

不要破坏现有 `.env` 中其他键。

---

# 22. Provider API

实现 W1 契约规定的 Provider API。

至少支持：

```text
GET    /api/v1/providers
POST   /api/v1/providers
GET    /api/v1/providers/{id}
PATCH  /api/v1/providers/{id}
DELETE /api/v1/providers/{id}
```

如果契约使用不同路径，以契约为准。

---

# 23. Provider 数据模型

Provider 至少涉及：

```text
id
name
type
base_url
credential reference
enabled
models
status
```

具体字段只使用当前 Core 真正支持的内容。

禁止为了 UI 好看而增加：

```text
不存在于 Core 的 provider flags
```

---

# 24. Provider 写入

新增 Provider 必须最终进入：

```text
真实配置系统 / Provider Registry
```

不能只存：

```text
webui_providers.db
```

然后 AI Router 完全不知道。

必须验证：

```text
WebUI created provider
        ↓
AI Router can discover/use provider
```

---

# 25. Credential API

实现：

```text
GET    /api/v1/credentials
POST   /api/v1/credentials
PATCH  /api/v1/credentials/{id}
DELETE /api/v1/credentials/{id}
```

具体 endpoint 按契约。

---

# 26. Credential 核心要求

Credential：

```text
不进入 config.yaml
不进入 overrides.yaml
不进入普通 JSON response
不进入日志
不进入 exception message
```

默认写入项目现有：

```text
.env
```

或者项目已存在的合法 Secret storage。

---

# 27. API Key 写入

WebUI：

```text
POST credential
```

后端：

```text
validate
↓
safe write
↓
reload environment / provider secret path
↓
return masked status
```

禁止：

```json
{
  "value": "sk-..."
}
```

作为 response 返回。

---

# 28. Credential 删除

删除前必须：

```text
authenticated
authorized
CSRF
```

并通过 Service 更新真实配置。

如果 Credential 被 Provider 引用：

返回：

```text
409 conflict
```

或者根据当前契约执行 detach。

不要静默删除造成 Provider 变成半配置状态。

---

# 29. Model API

实现真实模型管理：

```text
GET
POST
PATCH
DELETE
```

以及契约要求的：

```text
reorder
enable/disable
```

等操作。

---

# 30. Model 创建验证

新增模型时验证：

```text
provider exists
model id non-empty
display name valid
priority valid
role bindings valid
```

不能添加：

```text
provider = nonexistent
```

的模型。

---

# 31. Model Role API

实现：

```text
GET /api/v1/models/roles
PATCH /api/v1/models/roles
```

具体路径遵循契约。

角色绑定必须最终修改：

> **真实 AI Router 使用的配置。**

不能让 WebUI 自己维护独立 role mapping。

---

# 32. Model Reorder / Failover

实现模型排序 / fallback chain 时：

```text
WebUI
 ↓
Existing AI Router configuration
```

不要重新实现：

```text
retry
cooldown
429 handling
5xx handling
```

这些仍然属于 AI Router。

WebUI 只负责：

```text
配置顺序
启用
禁用
显示状态
```

---

# 33. Model Test

实现契约规定的 Model Test。

测试链路：

```text
Provider
 ↓
Credential
 ↓
Model
 ↓
Real request
```

返回：

```text
HTTP status
latency
model
success/failure
error category
```

禁止返回：

```text
API Key
Authorization header
完整秘密 URL
```

---

# 34. Model Usage

当前项目已经有 AI usage / failure / cooldown 等运行状态。

W2 应通过 API 暴露：

```text
usage
success count
failure count
429
5xx
cooldown
latency if available
last error
```

但：

> 只读取真实 Router / Metrics 数据。

不要复制建立第二套 model statistics store。

---

# 35. Runtime Read API

W1 已确认一些 Runtime 数据底层有访问器，但旧 WebUI 未充分利用。

实现契约规定的：

```text
GET /api/v1/runtime
```

至少考虑：

```text
runtime status
uptime
scheduler ticks
tick interval
world revision
cognitive revision
current action
current goal
current commitment
OneBot status
queue status
```

只读。

---

# 36. Runtime Read Model

可以创建：

```text
RuntimeReadModel
```

但它必须是：

> **瞬时 read projection**

不是持久化状态。

禁止：

```text
runtime_state.db
runtime_cache table
```

之类的第二真相。

---

# 37. Goal / Commitment / Relationship Read Projection

如果 W1 契约定义了：

```text
goals
commitments
relationships
```

读取它们已有 accessor。

如果没有统一 accessor：

只添加最小 read-only service。

禁止修改：

```text
Goal semantics
Commitment semantics
Relationship semantics
```

---

# 38. Domain API

W2 只实现契约要求的领域只读接口和必要 action endpoint。

例如：

```text
character
sandbox
social
memory
logs
tools
agent
```

具体以 `WEBUI_API_CONTRACT.md` 为准。

不要为了“接口看起来很全”而一次性新增所有可能 endpoint。

---

# 39. Action API 的原则

对于：

```text
restart
clear
reload
delete
rebuild
force action
```

必须明确这是：

```text
admin action
```

而不是普通：

```text
PATCH state
```

所有危险动作：

```text
audit log
confirmation
authorization
```

具体范围按契约。

---

# 40. Realtime / WebSocket

W2 不需要开发新前端，但必须为未来 SPA 提供稳定 WS contract。

现有：

```text
RealtimeHub
NarrationFeed
```

优先复用。

只扩展必要 topic，例如：

```text
status
world
runtime
ai
logs
config
```

具体按照契约。

---

# 41. WebSocket 不应成为第二套业务系统

WebSocket 只负责：

```text
publish
subscribe
```

不负责：

```text
修改 Sandbox
修改 Goal
模型 routing
配置持久化
```

---

# 42. API 和旧 SSR 必须共存

W2 完成后：

```text
旧 WebUI
    ↓
继续正常工作

/api/v1
    ↓
新后端 API 正常工作
```

旧页面不要求迁移。

这样 W2 才能安全独立提交。

---

# 43. API 统一错误处理

实现一个统一异常映射，例如：

```text
ValidationError
AuthenticationError
AuthorizationError
NotFound
Conflict
RateLimited
DependencyUnavailable
InternalError
```

映射为契约规定的 JSON 错误格式。

不要在每个 endpoint 复制：

```python
try:
    ...
except:
```

---

# 44. API 输入验证

所有 POST/PATCH body 必须：

```text
typed
validated
bounded
```

例如：

```text
temperature
timeout
priority
cooldown
url
provider name
model id
```

都需要真实约束。

不要信任前端。

---

# 45. Provider URL 校验

至少防止：

```text
空 URL
非法 scheme
malformed URL
```

具体允许范围以当前项目 Provider 类型为准。

不要因为校验方便而限制用户使用：

```text
http://localhost
http://127.0.0.1
```

这些是合法本地 Provider 场景。

---

# 46. Credential Secret Redaction

统一提供：

```text
redact_secret()
```

或者复用项目已有机制。

必须检查：

```text
logs
exceptions
debug repr
response serialization
trace
```

确保 Secret 不泄露。

---

# 47. CSRF 测试必须真实验证

至少测试：

```text
没有 CSRF → 403
错误 CSRF → 403
正确 CSRF → success
```

以及：

```text
GET
```

不被错误拦截。

---

# 48. Auth 测试

至少覆盖：

```text
anonymous GET
anonymous POST
authenticated GET
authenticated POST
invalid session
```

具体权限规则根据现有 WebUI。

---

# 49. Config 测试

必须至少覆盖：

### Schema

```text
returns schema
returns categories
returns metadata
```

### Effective

```text
returns current value
returns source
doesn't leak secrets
```

### Apply

```text
valid change
invalid change
hot reload
restart required
conflict
```

---

# 50. Provider 测试

至少：

```text
create
read
update
delete
duplicate
invalid
test
```

并验证最终真实 Provider 配置。

---

# 51. Model 测试

至少：

```text
create
read
update
delete
enable
disable
reorder
role binding
test
```

并验证 AI Router 最终使用的配置发生相应变化。

---

# 52. Credential 测试

至少：

```text
add
replace
delete
masked response
secret not leaked
atomic write
preserve unrelated env vars
```

测试文件：

```text
.env
```

中的其他变量不会因为 WebUI 更新一个 API Key 而消失。

---

# 53. Runtime 测试

至少：

```text
runtime status
scheduler data
world revision
cognitive revision
goal
action
commitment
```

并确认：

> 只读，不改变 Sandbox。

---

# 54. Regression 测试

所有既有测试必须保持通过：

```text
pytest -q
```

最终不能出现：

```text
1430 tests → 旧功能丢失
```

---

# 55. 静态质量

完成后必须：

```bash
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe app
.venv\Scripts\python.exe -m pytest -q
```

不要使用系统 Python 的旧工具链替代项目 `.venv`。

W1 已经确认：

> 项目必须优先使用 `.venv` 工具链。

---

# 56. API Contract 验收

完成后必须逐条检查：

```text
docs/WEBUI_API_CONTRACT.md §10
```

中的 W2 10 项验收要求。

不得自行缩减范围。

如果发现契约与当前源码真实能力存在冲突：

1. 先定位原因；
2. 优先通过最小兼容层满足契约；
3. 不得悄悄修改契约来降低验收标准；
4. 如果确实需要改变契约，必须在最终报告中明确说明。

---

# 57. 不要提前做前端

即使 API 做完后很容易顺手写页面：

> **不要这样做。**

W2 完成标准是：

```text
API 可被未来 Vue 使用
+
curl / pytest 可以验证
+
旧 UI 仍工作
```

而不是：

```text
API + Vue 混合提交
```

这样 W3 才能独立验证前端。

---

# 58. 推荐实现顺序

严格按以下顺序：

```text
Step 1
读取 W1 三份文档

Step 2
API common / response / error

Step 3
Auth / CSRF integration

Step 4
Config Registry

Step 5
Config Schema / Effective / Apply

Step 6
Credential API

Step 7
Provider API

Step 8
Model API

Step 9
Model Role / Failover

Step 10
Model Test / Usage

Step 11
Runtime read API

Step 12
必要 Domain read APIs

Step 13
Realtime contract

Step 14
Backend tests

Step 15
Full regression

Step 16
CI
```

---

# 59. Commit 策略

W2 不要最后一次提交所有代码。

建议至少拆：

```text
webui-v1/w2-api-common
webui-v1/w2-config
webui-v1/w2-credentials
webui-v1/w2-ai
webui-v1/w2-runtime
webui-v1/w2-tests
```

如果项目规模允许，也可以合并为 1~3 个逻辑提交。

原则是：

> 每个 commit 都必须是可回退的。

---

# 60. Git 安全

严格禁止：

```text
force push
rebase main
filter-repo
BFG
history rewrite
```

正常 commit + push。

---

# 61. Character Bible 安全

继续遵守：

```text
真实 Character Bible
≠
public Git repository
```

任何新文件都不得把真实 Bible 内容复制进去。

特别检查：

```text
API response
debug endpoint
diagnostic endpoint
config export
snapshot
logs
```

都不能意外返回真实 Bible 私密内容。

---

# 62. W2 最重要的人工验收

完成后必须用真实运行中的 Bot 验证。

## A. Config

通过 API：

```text
读取 schema
读取 effective config
修改一个支持热更新的配置
```

确认：

```text
API 返回 success
Runtime 使用新值
```

---

## B. Restart-required

修改一个需要重启的字段。

确认：

```text
API 返回保存成功
effective_runtime 不假装改变
restart_required = true
```

---

## C. Provider

通过 API：

```text
创建 Provider
设置 Base URL
设置 API Key
```

然后：

```text
Test Provider
```

确认成功。

---

## D. Model

通过 API：

```text
创建模型
```

然后：

```text
Test Model
```

确认真实 AI Router 可以使用它。

---

## E. Role

修改：

```text
默认 Chat Model
```

确认真实 AI Router 使用新的模型。

---

## F. Secret

检查：

```text
GET API
logs
errors
test result
```

确认 API Key 没有明文出现。

---

## G. Runtime

读取：

```text
/api/v1/runtime
```

确认：

```text
scheduler
world
goal
action
commitment
```

都是真实当前状态。

并确认请求本身不会改变世界。

---

# 63. 最终报告

完成后必须输出：

# WebUI v1.0 · W2 Backend API Foundation Report

至少包括：

```text
1. W1 baseline commit
2. W2 final commit
3. API endpoints implemented
4. Config Registry implementation
5. Config fields exposed
6. Effective Config source support
7. Hot reload support
8. Restart-required support
9. Credential implementation
10. Secret security
11. Provider CRUD
12. Model CRUD
13. Model role binding
14. Failover
15. Model test
16. Usage/status
17. Runtime read model
18. WebSocket changes
19. Auth / CSRF
20. Tests added
21. pytest
22. ruff
23. mypy
24. frontend status = not started
25. old WebUI status = preserved
26. CI result
27. working tree status
28. known limitations
```

---

# 64. W2 完成定义

只有同时满足以下条件才能宣布：

```text
W2 = COMPLETE
```

```text
✅ /api/v1 已建立
✅ JSON contract 统一
✅ Auth 正常
✅ CSRF 正常
✅ Config Registry
✅ Config Schema
✅ Effective Config
✅ Config Apply
✅ Credential API
✅ Provider API
✅ Model API
✅ Model Role
✅ Failover configuration
✅ Model Test
✅ Usage/status
✅ Runtime read API
✅ 必要实时 API/WS contract
✅ Secret 不泄露
✅ 旧 WebUI 正常
✅ Core 行为未改变
✅ 全量 pytest 通过
✅ ruff 通过
✅ mypy 通过
✅ CI success
✅ working tree clean
```

---

# 65. 停止条件

W2 完成后：

> **立即停止。**

不要进入 W3。

不要开始：

```text
Vue
Vite
Frontend
Dashboard redesign
Sidebar
CSS
```

最终状态应为：

```text
W1 Audit
✅

W2 Backend API Foundation
✅

W3 Frontend
NOT STARTED
```

并等待下一阶段指令。

---

# 最核心的一句话

本阶段不是“把旧页面改成 API”。

而是：

> **给 CatooBot WebUI v1.0 建立一套可靠、统一、可验证、可解释、不会与 Core 产生第二份真相的 Backend Management API。**

未来 Vue 只是这个 API 的一个消费者。

**先完成 W1 文档复核，再开始 W2；完成 W2 后停止，不进入 W3。**