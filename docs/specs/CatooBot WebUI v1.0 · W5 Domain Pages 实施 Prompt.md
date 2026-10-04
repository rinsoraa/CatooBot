# CatooBot v2.1 · WebUI v1.0 — W5 Domain Pages

## 0. 当前基线

W1：

```text
3478b0a2c676ac446236a9acdd762bdf365fd3aa
```

W2：

```text
1ba6f79684df93d3e9613d130d04394fe594cb08
```

W3：

```text
2bebea992d60cd1935d49eaf840a6c6c1838c53b
```

W4：

```text
51cfaeef1e7b9284fa7b0a6606b6b80911767dd1
```

当前状态：

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
← 本阶段

W6 Final Integration / Cutover
NOT STARTED
```

当前质量基线：

```text
pytest: 1544 passed
frontend tests: 145 passed
ruff: PASS
mypy: PASS
vue-tsc: PASS
vite build: PASS
CI: success
working tree: clean
```

---

# 1. W5 任务定位

本阶段名称：

> **WebUI v1.0 · W5 Domain Pages**

目标：

> 将现有 v0.8 中已经存在的角色、世界、社交、记忆、工具、Agent、媒体、日志、Runtime 管理能力，迁移到 WebUI v1.0 的新信息架构中。

重点不是：

```text
继续增加后端能力
```

而是：

```text
整理已有能力
↓
补齐必要的 WebUI API read/action contract
↓
建立新的 v1 页面
↓
让用户能够真正理解 CatooBot 当前发生了什么
```

---

# 2. W5 页面范围

本阶段优先完成：

```text
🏠 总览
   └─ Dashboard enhancement

👤 角色
   ├─ 角色
   ├─ 当前状态
   └─ 世界 / Sandbox

💬 社交
   ├─ 用户
   ├─ 群组
   ├─ 社交空间
   ├─ Relationship
   ├─ Commitment
   └─ 当前会话

🧠 记忆
   ├─ 浏览
   ├─ 搜索
   ├─ 时间线
   ├─ Memory Detail
   └─ 健康度

🎨 媒体与能力
   ├─ 表情
   ├─ 工具
   └─ Agent
     
⚙ 系统
   ├─ 日志
   └─ Runtime
```

具体页面根据 W1 审计和当前真实 Service/API 能力决定。

---

# 3. 最重要的边界

W5 **不是扩展 Core 功能阶段**。

绝对不要借机新增：

```text
❌ 新 Memory Engine
❌ 新 Social Engine
❌ 新 Relationship System
❌ 新 Sandbox
❌ 新 Agent
❌ 新 Tool Runtime
❌ 新 EventBus
❌ 新 Scheduler
❌ 新 Conversation DB
```

如果发现当前 WebUI 无法展示某个已有领域信息：

优先：

```text
已有 accessor
↓
Read Model
↓
/api/v1
↓
Frontend
```

而不是重新创建业务系统。

---

# 4. 第一阶段：W5 Domain Audit

开始编码前重新读取：

```text
docs/WEBUI_V1_AUDIT.md
docs/WEBUI_CONFIG_MATRIX.md
docs/WEBUI_API_CONTRACT.md
```

并重新审计：

```text
app/web/routes/
app/web/pages/
app/web/services/
webui/src/
```

建立：

```text
docs/WEBUI_V1_DOMAIN_MAP.md
```

内容至少：

```text
旧页面
↓
旧 Route
↓
旧 Service
↓
当前真实能力
↓
v1 新页面
↓
现有 /api/v1 endpoint
↓
缺失 endpoint
↓
迁移处理
```

不要根据旧页面名字猜其真正用途。

---

# 5. Domain API 缺失处理原则

如果某个 v1 页面需要的数据当前 `/api/v1` 没有：

允许增加：

```text
最小 read API
```

如果某个管理动作已经有 Service：

允许增加：

```text
最小 action API
```

但禁止：

```text
Frontend → DB
Frontend → private runtime state
Frontend → domain internals
```

统一：

```text
Vue
 ↓
/api/v1
 ↓
Existing Admin / Domain Service
 ↓
Core
```

---

# 6. Character 页面

页面：

```text
/character
```

目标：

> 让管理员理解“当前运行的角色是谁、现在是什么状态”，而不是修改 Character Bible 真相。

---

# 7. Character 页面内容

根据实际可用 API 展示：

```text
角色名称
显示头像
当前模式
当前地点
当前动作
当前重要 Need
当前 Goal
当前 Action
当前 Social Context
当前运行状态
```

状态区域：

```text
Online
Runtime
AI
QQ
```

---

# 8. Character Bible 安全

真实：

```text
config/character_bible.md
```

不得作为普通 API 全文返回。

不得在：

```text
Character Page
Frontend bundle
Browser source
Config export
```

出现真实私密内容。

如果已有公开 Character Definition API：

只展示：

```text
公开/允许展示字段
```

否则使用：

```text
当前 Runtime Projection
```

---

# 9. Character 与 Config 分离

Character 页面：

```text
角色当前状态
```

Settings：

```text
运行配置
```

不得出现：

```text
角色页面直接修改 Sandbox
```

或者：

```text
角色页面直接覆盖 Character Bible
```

---

# 10. World / Sandbox 页面

页面：

```text
/character/world
```

或者：

```text
/character/sandbox
```

具体根据新 IA 选择一个名称。

目标：

> 让管理员看到当前角色的“世界真相”。

---

# 11. World Overview

至少显示：

```text
当前地点
当前活动
当前 ActionInstance
当前 Goal
当前 Mode
Needs
World Revision
Cognitive Revision
```

例如：

```text
当前活动
吃蛋糕

地点
厨房

模式
home

Need
饥饿 · 很强烈

Goal
...

Action
...
```

---

# 12. 不要把内部数据直接堆出来

不要：

```text
巨大 JSON
```

默认显示人类可读信息。

高级/诊断模式才允许：

```text
Raw state
```

而且：

> 只显示已经允许通过 API 输出的结构化数据。

---

# 13. Action Instance 展示

如果当前有：

```text
ActionInstance
```

显示：

```text
动作名称
状态
开始时间
预计完成
当前进度
Goal
Goal Step
```

明确：

> ActionInstance 是真实运行状态，不是前端任务。

---

# 14. Interrupted Action

如果当前处于：

```text
InterruptedActionContext
```

需要显示：

```text
当前动作
暂停原因
原 ActionInstance
当前社交交互
恢复状态
```

但：

> 不允许前端修改 InterruptedActionContext。

只展示。

---

# 15. Need 页面

显示：

```text
Need 名称
当前 Band
趋势
重要性
```

如果 API 没有趋势：

不要自行计算假的趋势。

只显示真实状态。

---

# 16. World Timeline

如果已有：

```text
trace
narration
world events
```

可以建立：

```text
/character/world/timeline
```

显示：

```text
时间
事件
地点
Action
状态变化
```

但不要：

```text
每秒 tick 一条
```

当前已经完成的 Console World Log 去刷屏设计必须延续到 WebUI。

---

# 17. Social 一级页面

页面：

```text
/social
```

不要继续分散成：

```text
/users
/groups
/relationships
/commitments
/sessions
```

作为多个一级导航。

统一：

```text
社交
```

内部：

```text
用户
群组
关系
承诺
会话
```

---

# 18. User Explorer

显示：

```text
人物
QQ handle
所属空间
Relationship
最近互动
开放 Commitment
最近 Shared Experience
```

不要暴露：

```text
数据库内部 id
```

作为主要用户界面。

高级模式可以显示。

---

# 19. User Detail

页面：

```text
/social/users/{id}
```

显示：

```text
Identity
Relationship
Open Commitments
Recent Shared Experiences
Relevant Memories
Social Spaces
```

严格遵循当前 Core 的人物身份模型。

---

# 20. Group Explorer

显示：

```text
群组
Group ID
名称
成员/用户
Social Spaces
最近活动
```

注意：

> Group ≠ Person。

不能把群号当成关系对象。

---

# 21. Social Space

显示：

```text
私聊
群聊
具体 social_space
```

帮助管理员理解：

```text
person
+
social_space
```

是两个独立维度。

---

# 22. Relationship

页面提供：

```text
Relationship stage/type
交互次数
最近互动
Relationship 相关上下文
```

不要允许管理员在普通页面随意修改 Relationship。

如果当前已有合法 Admin action：

可以提供。

否则：

> 只读。

---

# 23. Commitment

显示：

```text
开放 Commitment
人物
内容摘要
状态
窗口
创建时间
到期时间
关联 Goal
```

特别显示：

```text
Future
Active
Fulfilled
Broken / Cancelled
```

但使用当前 Core 的真实状态定义。

---

# 24. Commitment Detail

显示：

```text
Commitment
→ Person
→ Social Space
→ Goal
→ ActionInstance
→ Shared Activity
→ Outcome
```

这能让管理员排查：

> “为什么这个约定没有完成？”

---

# 25. Social Session

显示：

```text
person
social space
start
last activity
turn count
interrupted
```

不要显示：

```text
完整聊天正文
```

除非当前已有合法 conversation history capability。

当前架构明确：

> Runtime-only conversation episode 不等于完整聊天数据库。

---

# 26. Memory 一级页面

页面：

```text
/memory
```

或：

```text
/memories
```

统一入口。

---

# 27. Memory Explorer

支持：

```text
搜索
Person
时间范围
Memory type
Episode
significance
```

使用已有：

```text
GET /api/v1/memories
```

或者必要扩展。

---

# 28. Memory Table

显示：

```text
时间
摘要
类型
Person
来源 Episode
重要性
```

不要默认显示完整 embedding。

---

# 29. Memory Detail

点击进入：

```text
/memory/{id}
```

显示：

```text
Memory 内容
Person
Episode
Experience
Source
Created
Updated
Retrieval metadata
```

如果当前 Memory 模型存在 provenance：

明确显示。

---

# 30. Memory 搜索

支持：

```text
关键词
Person
类型
时间
```

如果当前 API 已支持 semantic search：

可以提供：

```text
语义搜索
```

但不要在前端自行计算 embedding。

---

# 31. Memory Health

页面：

```text
/memory/health
```

显示真实：

```text
memory count
embedding status
retrieval status
DB status
index status
```

不存在的数据：

显示：

```text
—
```

不要估算。

---

# 32. Memory Mutation

如果当前 API 支持：

```text
delete
archive
edit
```

必须：

```text
Danger confirmation
```

并清楚说明：

> 这会修改长期记忆，不是聊天记录。

如果当前 Core 没有明确的 Admin Memory Mutation Service：

> 不要添加数据库直写功能。

---

# 33. Tool 页面

一级位置：

```text
/abilities/tools
```

或者：

```text
/media-tools
```

按照实际 IA 选择。

---

# 34. Tool Explorer

显示：

```text
Tool Name
Description
Enabled
Risk Level
Permission
Budget
Last Used
```

使用已有 Tool Registry / Tool Policy。

---

# 35. Tool Detail

显示：

```text
名称
用途
输入 Schema
输出 Schema
权限
Budget
Cache
Loop Detection
Enabled
```

不要显示：

```text
工具内部代码
```

作为普通页面。

专家模式可以显示 metadata。

---

# 36. Tool Enable / Disable

如果现有 Tool Policy 支持：

提供：

```text
Switch
```

保存后：

```text
刷新真实 Tool Registry
```

如果不支持运行时修改：

显示：

```text
⚠ 重启后生效
```

不能假装热更新。

---

# 37. Tool Test

如果已有 Tool Admin Service：

可以提供：

```text
[测试]
```

但：

> Tool Test 是诊断操作。

必须明确：

```text
可能产生真实外部请求
```

例如：

```text
Web Search
Weather
```

点击测试时二次确认或明确提示。

---

# 38. Tool Policy

显示：

```text
Allowed
Denied
Risk
```

不要让普通页面成为：

```text
任意工具执行器
```

---

# 39. Sticker / Media 页面

如果当前项目已有：

```text
Sticker
Media
Expression
```

统一进入：

```text
/abilities/media
```

---

# 40. Sticker Library

显示：

```text
Sticker
文件名
预览
来源
添加时间
使用次数
是否有效
```

如果当前支持：

```text
import
delete
```

提供真实 API。

---

# 41. Sticker 安全

不要：

```text
默认上传普通图片就标记为 Sticker
```

继续遵守现有 Sticker 判断逻辑。

WebUI 只管理真实 Sticker Library。

---

# 42. Agent 页面

这里有一个硬性边界：

> **W5 不创建新的 Agent Runtime。**

只有当前仓库真实存在：

```text
Agent Service
Agent Runtime
Agent Admin Service
```

才迁移已有 Agent 页面。

如果当前没有：

```text
Agent system
```

则：

> 不要因为导航中有 Agent 就凭空创建它。

---

# 43. 如果存在现有 Agent

统一进入：

```text
/abilities/agent
```

显示：

```text
Status
Active Tasks
Recent Tasks
Policy
Budget
Model
```

具体基于真实后端。

---

# 44. 如果没有 Agent Core

显示：

```text
尚未启用 Agent Runtime
```

而不是：

```text
伪造一个 Agent 页面
```

---

# 45. Logs 一级页面

页面：

```text
/system/logs
```

目标：

> 真正成为 Debug Console。

---

# 46. Logs UI

支持：

```text
Level
Channel
Search
Pause
Resume
Auto Scroll
Clear View
```

不要删除实际日志文件。

“清空”只应该：

```text
清空当前浏览器视图
```

除非另有明确 Admin API。

---

# 47. Log Channels

至少考虑：

```text
WORLD
QQ
AI
DECISION
SOCIAL
MEMORY
RUNTIME
ERROR
```

以真实 narration / logging channels 为准。

---

# 48. Live Log

使用现有：

```text
WebSocket log / narration
```

不要轮询。

继续使用 W3/W4 的 Realtime Store。

---

# 49. Log Filtering

前端过滤：

```text
level
channel
keyword
```

可以本地过滤最近缓存。

历史搜索必须走后端 API。

不要假设浏览器已经拥有完整历史。

---

# 50. Log Security

敏感内容：

```text
API Key
Authorization
Secrets
Character Bible private data
```

不能显示。

后端必须先脱敏。

前端不要依赖：

```text
replace("sk-", "***")
```

这种不可靠处理。

---

# 51. Runtime 页面

页面：

```text
/system/runtime
```

这是：

> 运维观察页面。

---

# 52. Runtime Overview

显示：

```text
Process
Uptime
Runtime State
Scheduler
OneBot
AI
World
Database
WebSocket
```

---

# 53. Scheduler

显示：

```text
scheduler status
tick interval
ticks
last tick
elapsed time
```

特别说明：

```text
tick interval
```

只是 Scheduler 唤醒频率。

不要让 UI 把它描述成：

> “世界每 N 秒更新一次”。

延续 Phase 14.1 的准确语义。

---

# 54. World Runtime

显示：

```text
world revision
cognitive revision
current action
current goal
current commitment
```

如果当前有：

```text
InterruptedActionContext
```

显示其诊断状态。

---

# 55. OneBot

显示：

```text
connected
reconnect
last event
queue
lanes
```

不要让前端自己推断在线。

以 Runtime API 为准。

---

# 56. Runtime Actions

如果 W2 已经提供：

```text
POST /api/v1/runtime/actions/{name}
```

或者：

```text
world/control
```

这些必须单独放在：

```text
高级操作
```

绝不能放普通 Runtime 页面顶部。

---

# 57. Runtime Dangerous Actions

例如：

```text
force tick
reload
restart
reset
clear
```

必须：

```text
危险级别
说明
二次确认
后端权限
```

---

# 58. Sandbox Controls

如果当前提供：

```text
world/control
```

必须明确：

> 这是管理员调试操作，会直接影响运行时世界。

普通用户默认不要看到。

---

# 59. Dashboard Enhancement

W5 完成后回到：

```text
/
```

增强 Dashboard。

最终 Dashboard 至少：

```text
QQ
AI
World
Runtime
```

以及：

```text
当前动作
地点
Goal
Need
Relationship / Social Context
```

---

# 60. Dashboard Quick Actions

只提供高价值入口：

```text
配置 AI
查看世界
查看日志
查看社交
查看记忆
```

不要塞：

```text
20 个快捷按钮
```

---

# 61. Realtime integration

所有 Domain 页面尽可能利用：

```text
WebSocket
```

例如：

```text
World
Runtime
Logs
AI
Social
```

但：

> 如果 topic 不存在，不要在 W5 为每一个页面创建一个独立 WS 连接。

继续：

```text
一个 realtime store
```

---

# 62. Page Data Pattern

统一：

```text
load initial REST snapshot
↓
subscribe to Realtime Store
↓
apply event
↓
periodic manual refresh only when user requests
```

禁止：

```text
setInterval(fetch, 1000)
```

---

# 63. Mutation Pattern

所有领域变更：

```text
UI
↓
API
↓
Service
↓
Core
↓
server response
↓
invalidate relevant store
↓
reload
```

禁止：

```text
UI locally modifies truth
```

---

# 64. Store 扩展

根据实际需要增加：

```text
stores/character.ts
stores/world.ts
stores/social.ts
stores/memory.ts
stores/tools.ts
stores/logs.ts
stores/runtime.ts
```

但：

> 只在 Store 真正需要共享状态时建立。

不要为每个页面机械创建 Store。

---

# 65. 不要复制整个 Backend

Store 只保存：

```text
当前页面需要的 read model
```

不要把整个 Runtime / DB JSON 全部复制到 Pinia。

---

# 66. Domain Read Models

建议统一建立：

```text
CharacterReadModel
WorldReadModel
SocialReadModel
MemoryReadModel
ToolReadModel
RuntimeReadModel
```

来源：

```text
/api/v1
```

---

# 67. Detail 页面

所有 Detail 页面必须支持：

```text
Loading
404
Permission denied
Dependency error
Empty
```

不能默认数据一定存在。

---

# 68. Pagination

Memory / Users / Groups / Logs / Tasks 等可能增长的数据：

必须考虑：

```text
pagination
cursor
limit
```

具体使用后端 API 实际支持的方式。

禁止一次 GET 全部几万条。

---

# 69. Search

搜索必须：

```text
debounce
```

如果是后端查询。

不要每敲一个字发一个请求。

---

# 70. Filters

过滤条件可以保存到 URL：

例如：

```text
/memory?person=...
/logs?channel=AI
/social?space=...
```

这样页面可分享/刷新恢复。

---

# 71. URL State

分页、筛选、搜索、tab：

优先放 URL。

临时 UI 状态：

```text
modal open
drawer open
```

放组件。

---

# 72. Empty States

每个 Domain 必须有明确 Empty State。

例如：

```text
没有记忆
暂时没有可展示的长期记忆。
```

不要：

```text
空白页面
```

---

# 73. Read-only 表明方式

如果页面暂时不能编辑：

显示：

```text
只读
```

不要放：

```text
假的 Save 按钮
```

---

# 74. Admin 操作

如果有真实 Admin action：

Button 文案必须是：

```text
重置
暂停
恢复
删除
强制执行
```

而不是：

```text
Submit
Action
Apply
```

---

# 75. Dangerous Actions

统一使用 W4：

```text
ConfirmDialog
```

必须说明：

```text
影响
是否可恢复
```

如果不可恢复：

明确：

```text
不可撤销
```

---

# 76. Character / Social / Memory 权限

当前项目只有单管理员会话。

W5 不新增 RBAC。

但所有 mutation API 继续遵守：

```text
Auth
CSRF
Authorization
```

---

# 77. Legacy 页面迁移

根据 W1 的：

```text
WEBUI_V1_AUDIT.md
```

建立：

```text
旧页面
→ v1 新页面
```

迁移完成后：

旧页面可以保留：

```text
/legacy
```

以及明确兼容 URL。

---

# 78. 不要一对一复制旧页面

W5 的目标不是：

```text
v0.8 page
↓
换个 Vue template
```

而是：

```text
多个旧页面
↓
重新组织
↓
一个合理 v1 页面
```

例如：

```text
users
groups
relationships
commitments
sessions
```

统一为：

```text
社交
```

---

# 79. Dashboard / Domain Navigation

每个页面顶部：

```text
PageHeader
标题
描述
Breadcrumb / Tab
```

用户进入页面后知道：

```text
我在哪里
这里能做什么
```

---

# 80. Design consistency

必须继续使用：

```text
W3 tokens
W3 theme
W4 components
```

禁止重新建立：

```text
domain-specific color system
```

---

# 81. 图标

继续使用当前：

```text
inline SVG
```

除非确有必要。

不要在 W5 引入新的 icon library。

---

# 82. 性能

Domain 页面：

不要一次加载所有数据。

例如：

```text
Memory
```

先：

```text
count + first page
```

再：

```text
detail
```

---

# 83. World 页面性能

World 每秒 tick：

> 不代表 WebUI 每秒重渲染整个页面。

只处理：

```text
world change
```

或：

```text
relevant runtime event
```

保持 W3 Realtime 思路。

---

# 84. Log 性能

日志流使用：

```text
bounded buffer
```

建议最大：

```text
500
```

或根据当前 frontend 实现合理设置。

不能无限积累。

---

# 85. Memory Detail 性能

Memory detail：

只加载：

```text
点击的 Memory
```

不要列表页面预取全部详情。

---

# 86. Social Detail 性能

Person detail：

只加载：

```text
relationship
commitments
experiences
memories
```

通过 API 按需读取。

不要一次加载整个数据库。

---

# 87. 领域 API 扩展原则

如果 W2 缺少某个 endpoint：

必须：

```text
契约
schema
route
service
test
```

完整加入。

不要直接让前端依赖临时 JSON。

---

# 88. API Contract

W5 如果新增 `/api/v1` endpoint：

必须同步：

```text
docs/WEBUI_API_CONTRACT.md
```

包括：

```text
request
response
errors
auth
CSRF
```

---

# 89. Response 类型

Frontend：

```text
types/
```

必须与 Backend：

```text
schema
```

保持一致。

不能：

```text
any
```

逃避类型。

---

# 90. Real-time 事件类型

如果新增：

```text
world
social
memory
tool
runtime
```

事件必须：

```text
typed
versionable
```

例如：

```json
{
  "topic": "world",
  "type": "world.changed",
  "payload": {}
}
```

具体格式根据当前 WS contract。

---

# 91. 不要让 Realtime 事件成为事实来源

UI 收到：

```text
world.changed
```

只能：

```text
更新 read model
```

必要时仍可以重新 GET：

```text
/api/v1/world
```

最终后端 API 是事实源。

---

# 92. Agent 页面边界再次强调

如果当前项目确实存在 Agent：

迁移。

如果不存在：

只提供：

```text
Coming / Disabled
```

不得启动新的 Agent Runtime 开发。

---

# 93. Tools 页面边界

不要因为 Tool Explorer 页面存在：

```text
直接执行任何工具
```

所有执行必须经过：

```text
existing Tool Runtime
```

---

# 94. Sandbox 操作边界

不要因为有：

```text
Force Tick
World Control
Action Control
```

就让前端直接修改：

```text
Sandbox state
```

所有操作：

```text
Admin API
```

---

# 95. Memory 操作边界

如果当前 Memory mutation Service 不存在：

只读。

不要：

```text
DELETE FROM ...
```

---

# 96. Social 操作边界

Relationship / Commitment：

优先只读。

如果已有管理 Service：

再提供 mutation。

---

# 97. Log 页面边界

不能：

```text
前端删除服务器日志
```

除非已有专门 Admin API。

---

# 98. Runtime 页面边界

不能：

```text
前端自行修改 scheduler
```

只能使用后端已有配置/API。

---

# 99. Mobile

W5 继续保持：

```text
desktop-first
responsive
```

但不进行复杂 mobile 专属设计。

---

# 100. Accessibility

所有新增页面必须：

```text
labels
keyboard navigation
visible focus
semantic headings
aria-label where required
```

状态不能只用颜色表示。

---

# 101. Loading / Error / Empty

所有 Domain 页面必须提供：

```text
Skeleton
ErrorState
EmptyState
Retry
```

---

# 102. Network Errors

如果 API：

```text
503
```

页面显示：

```text
服务暂时不可用
[重试]
```

不要白屏。

---

# 103. Reconnect

WebSocket 断线：

Domain 页面继续显示：

```text
最后一次有效状态
```

同时顶部：

```text
Reconnecting...
```

恢复后：

```text
重新获取必要 snapshot
```

---

# 104. 不要因为 Realtime 断线清空页面

例如：

```text
World data
```

已经存在。

WebSocket 断线：

不要：

```text
World = null
```

保留：

```text
最后已知状态
```

并标记：

```text
数据可能不是最新
```

---

# 105. Dashboard 最终增强

增加：

```text
当前世界
Need
Goal
Action
Social
Runtime
```

但保持信息密度。

---

# 106. 世界状态视觉表达

建议使用：

```text
Current Activity Card
Current Location
Need chips
Goal progress
```

不要：

```text
大 JSON
```

---

# 107. Social 状态视觉表达

例如：

```text
当前对象
空凛

关系
...

空间
私聊

开放承诺
1

最近共享经历
...
```

实际内容必须来自 backend。

---

# 108. Memory Timeline

可以使用：

```text
Timeline
```

展示：

```text
日期
Experience
Memory
Person
```

帮助管理员理解：

> 角色记住了什么。

---

# 109. Debug / Expert

普通页面保持简单。

Expert 可以：

```text
Raw JSON
Trace
IDs
revision
provenance
```

但仍然不能显示 Secret。

---

# 110. 页面权限

如果 API 返回：

```text
403
```

显示：

```text
无权限
```

不要隐藏页面导致用户不知道功能存在。

---

# 111. Domain Search

支持搜索的页面：

```text
User
Group
Memory
Logs
Tools
```

必须 debounce。

---

# 112. Pagination UI

统一组件：

```text
Pagination
```

或 Infinite Scroll。

优先使用现有 Naive UI / 自制组件。

不要再引入大型 UI 库。

---

# 113. Domain Components

根据实际复用建立：

```text
WorldStateCard
NeedList
GoalCard
ActionCard
RelationshipCard
CommitmentCard
MemoryCard
ToolCard
RuntimeStatus
LogViewer
```

不要为了组件数量而组件化。

---

# 114. Domain Store Invalidation

例如：

```text
Relationship mutation
→ social store invalidate
→ character overview refresh
```

例如：

```text
Memory mutation
→ memory list invalidate
→ memory health refresh
```

具体按真实关联设计。

---

# 115. API cache

普通 GET 可以短时间缓存。

但：

```text
Runtime
World
AI status
```

不能使用过期过久的缓存。

Realtime 更新优先。

---

# 116. Browser URL Deep Links

必须支持：

```text
/social/users/{id}
/memory/{id}
/character/world
/tools/{name}
```

具体路径按最终 IA。

---

# 117. Back / Forward

页面 Tab / Filter 改变：

浏览器后退应该能正确恢复。

不要所有状态都：

```text
pushState
```

混乱处理。

优先 Router query 参数。

---

# 118. Old URL Compatibility

W1 已经保留很多旧 URL。

W5 不得无故破坏：

```text
/character
/memory
/social
/tools
/agent
...
```

如果迁移：

可以：

```text
301 / compatibility / redirect
```

但登录/session行为必须保持。

---

# 119. Legacy Strategy

v0.8 保留：

```text
/legacy
```

作为最终 fallback。

如果旧 URL 在 v1 已有对应页面：

可以进入 v1。

没有对应页面：

回退到 legacy。

---

# 120. W5 迁移表

必须新增：

```text
docs/WEBUI_V1_MIGRATION_MAP.md
```

至少：

```text
Old URL
New URL
Status
Replacement
Notes
```

示例：

```text
/memory
→ /memory
migrated

/users
→ /social/users
migrated

/groups
→ /social/groups
migrated
```

---

# 121. 不要删除旧代码过早

W5 的前半阶段：

```text
旧 SSR
继续存在
```

直到：

```text
v1 page
真实可用
API 完整
测试通过
```

再删除冗余 route。

---

# 122. Old Route Cleanup

最终可以：

```text
dead route
```

清理。

但必须：

```text
搜索所有引用
测试旧链接
确认没有插件依赖
```

再删除。

---

# 123. 不得修改 Git History

禁止：

```text
force push
rebase main
filter-repo
BFG
```

---

# 124. Character Bible

W5 特别检查：

```text
Character page
World page
Debug
Raw JSON
Memory provenance
Social identity
Logs
```

任何地方都不能泄露真实 Character Bible。

---

# 125. Browser E2E

如果 Playwright 当前已经可用：

优先使用。

如果仍然不可用：

继续使用 jsdom application tests，并在最终报告明确说明。

W5 至少对核心流程增加：

```text
Dashboard
→ Character
→ World
→ Social
→ Memory
→ Tools
→ Logs
→ Runtime
```

路由导航级测试。

---

# 126. 测试：Character

```text
load
render current state
missing data
error
```

---

# 127. 测试：World

```text
world initial snapshot
world realtime change
no polling
```

---

# 128. 测试：Social

```text
users
groups
relationship
commitment
session
detail
```

---

# 129. 测试：Memory

```text
list
search
detail
filters
pagination
health
```

---

# 130. 测试：Tools

```text
list
detail
status
permission
safe test if supported
```

---

# 131. 测试：Logs

```text
realtime
filter
pause
resume
bounded events
reconnect
```

---

# 132. 测试：Runtime

```text
overview
scheduler
world
OneBot
AI
danger actions guarded
```

---

# 133. API Tests

如果新增 backend endpoint：

必须新增 Python tests。

不能只靠 frontend test。

---

# 134. Security Tests

继续测试：

```text
401
403
CSRF
secret masking
```

并增加：

```text
domain API
```

不能绕过已有 middleware。

---

# 135. Performance Tests

至少人工验证：

```text
Logs 500 entries
Memory large list
Users/Groups pagination
World realtime
```

不会造成：

```text
浏览器明显卡顿
```

---

# 136. Full Regression

必须保持：

```text
pytest
ruff
mypy
vue-tsc
vitest
vite build
```

全部通过。

---

# 137. CI

CI 必须：

```text
Python job
+
WebUI job
```

全部成功。

---

# 138. W5 Commit Strategy

建议：

```text
webui-v1/w5-character-world
webui-v1/w5-social
webui-v1/w5-memory
webui-v1/w5-tools-media
webui-v1/w5-runtime-logs
webui-v1/w5-integration
```

实际可以合并为合理的 3~6 个逻辑提交。

---

# 139. 不要把所有 Domain 页面一次提交

每个主要 Domain 完成：

```text
API
Frontend
Tests
```

之后再进入下一个 Domain。

---

# 140. W5 推荐开发顺序

严格优先：

```text
1. Domain Audit
2. Migration Map
3. Character
4. World / Sandbox
5. Social
6. Memory
7. Tools
8. Media / Sticker
9. Agent（仅迁移已有能力）
10. Logs
11. Runtime
12. Dashboard enhancement
13. Legacy route migration
14. Full tests
15. CI
```

---

# 141. 不要在 W5 引入新的 Core Phase

如果发现：

```text
某个页面需要 Core 能力
```

先记录：

```text
known limitation
```

只有“已经存在但 WebUI 没有暴露”的能力才能通过最小 Admin API 补齐。

不要因为页面需求创造新的 Runtime 系统。

---

# 142. W5 最终信息架构

最终 Sidebar 应接近：

```text
🏠 总览

👤 角色
   ├─ 当前角色
   └─ 世界

🤖 AI 与模型
   ├─ 概览
   ├─ Provider
   ├─ 模型
   ├─ 模型用途
   ├─ 故障转移
   ├─ 用量
   └─ 测试台

💬 社交
   ├─ 用户
   ├─ 群组
   ├─ 关系
   ├─ 承诺
   └─ 会话

🧠 记忆
   ├─ 浏览
   ├─ 搜索
   └─ 健康度

🎨 媒体与能力
   ├─ 表情
   ├─ 工具
   └─ Agent

⚙ 系统
   ├─ 日志
   ├─ Runtime
   └─ 设置
```

当前实际功能不足时：

不要显示假的页面。

可以：

```text
disabled
```

或：

```text
Not available
```

---

# 143. W5 完成后的用户体验

用户进入：

```text
总览
```

可以知道：

```text
角色现在在做什么
AI 是否正常
QQ 是否正常
Runtime 是否正常
```

点击：

```text
角色
```

可以看到：

```text
当前世界
Need
Goal
Action
```

点击：

```text
社交
```

可以理解：

```text
角色认识谁
关系怎样
有哪些承诺
```

点击：

```text
记忆
```

可以理解：

```text
角色记住了什么
```

点击：

```text
工具
```

可以知道：

```text
角色能做什么
哪些工具启用
```

点击：

```text
日志
```

可以：

```text
实时观察运行
```

点击：

```text
Runtime
```

可以：

```text
定位运行问题
```

---

# 144. 不要把 WebUI 做成“开发者数据库浏览器”

默认页面应该：

```text
解释
```

而不是：

```text
展示所有内部字段
```

管理员真正需要的：

```text
ID
revision
trace
raw
```

放到：

```text
Expert / Diagnostic
```

---

# 145. Expert Mode

继续复用 W4：

```text
Basic
Advanced
Expert
```

Domain 页面也可以拥有：

```text
Show technical details
```

例如：

```text
ActionInstance ID
GoalStep
world_revision
cognitive_revision
episode_key
```

---

# 146. 不允许泄露内部安全数据

即使 Expert：

不能显示：

```text
API Key
Session Secret
Passwords
Private Character Bible
```

---

# 147. Domain 页面最终原则

每个页面都必须回答：

```text
这里是什么？
它现在是什么状态？
为什么是这个状态？
我可以做什么？
这个操作会影响什么？
```

如果页面无法回答这些问题：

重新设计。

---

# 148. W5 完成定义

只有同时满足：

```text
✅ Character
✅ World / Sandbox
✅ Social
✅ Users
✅ Groups
✅ Relationships
✅ Commitments
✅ Sessions
✅ Memory Explorer
✅ Memory Search
✅ Memory Detail
✅ Memory Health
✅ Tools
✅ Media / Sticker
✅ Agent（仅已有能力）
✅ Logs
✅ Runtime
✅ Dashboard enhancement
✅ Realtime integration
✅ Pagination where needed
✅ Search / filters
✅ Loading / Empty / Error states
✅ Domain API contract
✅ Domain tests
✅ frontend tests
✅ pytest
✅ ruff
✅ mypy
✅ vue-tsc
✅ Vite build
✅ CI success
✅ legacy compatibility
✅ Character Bible security
✅ working tree clean
```

才允许宣布：

```text
W5 = COMPLETE
```

---

# 149. W5 最终报告

完成后必须输出：

```text
# WebUI v1.0 · W5 Domain Pages Report

1. W4 baseline
2. W5 final commit
3. Domain Audit
4. Migration Map
5. Character
6. World / Sandbox
7. Social
8. Memory
9. Tools
10. Media / Sticker
11. Agent
12. Logs
13. Runtime
14. Dashboard enhancement
15. New API endpoints
16. Store architecture
17. Realtime changes
18. Legacy migration
19. Security
20. Browser E2E status
21. frontend tests
22. pytest
23. ruff
24. mypy
25. typecheck
26. build
27. CI
28. known limitations
29. final Git status
```

---

# 150. 停止条件

完成后：

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
NOT STARTED
```

立即停止。

不要继续增加：

```text
新的 Core System
新的 Agent
新的 Memory
新的 Social System
新的 Scheduler
新的 EventBus
```

---

# 最核心的一句话

W5 不是：

> “把剩下的旧页面全部搬到 Vue。”

而是：

> **把 CatooBot 已经存在的复杂世界、角色、社交、记忆、工具和运行时能力，重新组织成一个普通管理员真正看得懂、查得到、操作安全、不会产生第二套真相的 WebUI v1.0。**

最终结构：

```text
                         CatooBot WebUI v1.0

 ┌───────────────┬──────────────────────────────────┐
 │               │                                  │
 │    Navigation │             Domain View           │
 │               │                                  │
 │  总览          │   Character / World              │
 │  角色          │   Social / Memory               │
 │  AI            │   Tools / Media                 │
 │  社交          │   Logs / Runtime                 │
 │  记忆          │                                  │
 │  能力          │                                  │
 │  系统          │                                  │
 │               │                                  │
 └───────┬───────┴──────────────────────────────────┘
         │
         ▼
      API Client
         │
         ▼
      /api/v1
         │
         ▼
  Existing Admin / Domain Services
         │
         ▼
      CatooBot Core
```

**先审计 W1/W2 的 Domain 能力与当前 W3/W4 前端，再开始 W5。完成 W5 后停止，不进入 W6。**