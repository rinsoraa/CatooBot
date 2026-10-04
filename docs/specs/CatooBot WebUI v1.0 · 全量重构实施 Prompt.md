# CatooBot v2.1 · WebUI v1.0 全量重构实施任务

## 0. 任务定位

当前 CatooBot Core 已经完成 Phase 16，并以：

```text
004fe0a7ef82e77b5c10dd0f6fabd6334519c8f6
```

作为当前稳定基线。

当前 WebUI 仍是 v0.8，存在明显的信息架构和配置管理问题：

- 页面越来越多，但导航越来越乱；
- 相同或相关配置分散在多个入口；
- 有些配置项无法判断是否真正生效；
- 一部分配置必须修改 YAML；
- API Provider / Model / API Key 管理不完整；
- WebUI 与真实 Runtime 配置存在认知断层；
- 普通用户需要理解内部配置结构才能知道该修改什么；
- 当前 SSR / Python 字符串 HTML / 巨型 `ui.py` 已经开始阻碍继续迭代；
- 页面功能逐渐超过“管理面板”，但还没有形成统一的 Control Center。

本任务不是给 v0.8 换 CSS。

本任务是：

> **将现有 CatooBot WebUI 重构为真正可长期使用的 WebUI v1.0。**

目标：

```text
漂亮
+
可读
+
低门槛
+
功能完整
+
配置真正可管理
+
配置状态可解释
+
AI Provider / Model / API Key 全流程可管理
+
实时状态
+
可维护
```

最终用户不需要打开：

```text
config.yaml
overrides.yaml
.env
```

才能完成日常配置。

但高级用户仍然必须拥有：

```text
Expert / Advanced
```

级别的底层配置能力。

---

# 1. 第一原则：先审计，再重构

不要一上来删除旧 WebUI。

第一阶段必须先对当前仓库做完整审计。

重点检查：

```text
app/web/
app/config/
app/ai/
app/runtime/
app/core/
```

以及所有与 WebUI 管理有关的：

```text
routes
pages
services
schemas
settings
runtime state
WebSocket
authentication
CSRF
config persistence
model registry
provider registry
credentials
```

必须真实读取源码，不允许根据文件名猜行为。

---

# 2. 必须生成 WebUI 审计结果

第一阶段生成：

```text
docs/WEBUI_V1_AUDIT.md
```

至少记录：

## 2.1 当前页面

完整列出：

```text
URL
页面名称
后端文件
使用 Service
主要功能
是否仍在使用
是否有重复功能
是否建议迁移
```

---

## 2.2 当前配置

完整扫描 `AppConfig` 及其子配置。

建立：

```text
docs/WEBUI_CONFIG_MATRIX.md
```

每个配置必须至少记录：

```text
config key
label
类型
默认值
当前值
来源
是否真正被读取
读取位置
是否支持运行时修改
是否需要重启
是否已经有 WebUI
当前 WebUI 入口
建议 v1.0 入口
高级程度
依赖条件
```

例如：

```text
runtime.tick_interval_seconds
```

需要明确：

```text
名称：世界调度频率
当前值：1
来源：config / override / env / default
运行时：RuntimeScheduler
是否热更新：...
是否需要重启：...
普通用户是否需要看到：否 / 高级
解释：Scheduler 唤醒频率
```

---

# 3. 配置有效性审计是本任务最重要的要求之一

对于每一个配置项，不允许只因为它存在于 Settings Model 就认为“它有效”。

必须追踪真实引用。

至少区分：

```text
ACTIVE
```

实际被 Runtime / Service 使用。

```text
ACTIVE_WITH_RESTART
```

实际使用，但修改后需要重启。

```text
DEFINED_BUT_UNUSED
```

定义存在，但当前 Runtime 没有真实引用。

```text
CONDITIONALLY_USED
```

只有特定功能开启时才使用。

```text
LEGACY
```

历史字段，不建议继续使用。

```text
UNKNOWN
```

静态分析无法确定，必须人工审计。

禁止把：

```text
DEFINED_BUT_UNUSED
LEGACY
```

继续作为普通用户配置暴露。

---

# 4. 配置来源必须透明

完整审计并实现现有配置优先级。

当前体系如果保持：

```text
Environment
    ↓
overrides.yaml
    ↓
config.yaml
    ↓
default
```

则 WebUI 必须把它显式展示出来。

每个配置项至少显示：

```text
当前值

来源：
● WebUI override
● Environment
● overrides.yaml
● config.yaml
● Default
```

以及：

```text
生效状态：
✓ 已生效
⚠ 修改后需要重启
⚠ 当前功能未启用
⚠ 未被 Runtime 使用
```

用户不应该再猜：

> “我改了这个值，到底有没有生效？”

---

# 5. 不要破坏现有配置语义

绝对禁止为了 WebUI 简化而擅自改变：

```text
AppConfig
配置优先级
Runtime 行为
Sandbox
Goal
Action
Need
Relationship
Commitment
Memory
Conversation
External Influence
OneBot
```

如果当前配置系统存在历史兼容行为：

> 迁移 WebUI，而不是修改 Core 语义。

---

# 6. WebUI v1.0 技术方向

优先采用：

```text
Vue 3
TypeScript
Vite
Naive UI
```

但开始前先检查仓库当前前端环境。

如果仓库已有其他成熟前端体系：

> 优先复用现有基础设施，而不是为了追求技术栈而重复建设。

禁止使用 CDN 作为正式生产依赖。

正式 WebUI 必须：

```text
npm/pnpm build
        ↓
静态文件
        ↓
aiohttp 提供
```

最终架构：

```text
Browser
   ↓
Vue WebUI
   ↓
REST / JSON API
   ↓
aiohttp
   ↓
Admin Services
   ↓
CatooBot Core
```

实时信息：

```text
Runtime
   ↓
RealtimeHub / existing event mechanism
   ↓
WebSocket
   ↓
Vue
```

---

# 7. Core 与 WebUI 必须彻底分层

最终结构应该接近：

```text
CatooBot Core
        ↑
Existing Admin Services
        ↑
WebUI API Layer
        ↑
Vue WebUI
```

不要让：

```text
Vue
```

直接操作：

```text
Sandbox
Database
Config files
Runtime internals
```

所有写操作必须经过后端 Service。

---

# 8. 旧 WebUI 不要第一时间删除

必须建立可回滚方案。

推荐：

```text
/
    → WebUI v1

/legacy
    → WebUI v0.8
```

或者：

```text
webui.version = v1
```

允许切换。

只有 WebUI v1 达到最终验收后，才考虑删除旧 UI。

如果旧 WebUI 无法完整保留，也至少要保留：

```text
legacy branch / tagged commit
```

不要直接删除现有 UI 然后让项目进入不可回退状态。

---

# 9. 新 WebUI 一级信息架构

一级导航控制在 7~8 个区域。

推荐：

```text
🏠 总览

👤 角色
   ├─ 角色信息
   ├─ 当前状态
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
   ├─ 社交策略
   └─ 会话

🧠 记忆
   ├─ 浏览
   ├─ 搜索
   ├─ 时间线
   └─ 健康度

🎨 媒体与能力
   ├─ 表情
   ├─ 工具
   └─ Agent

⚙ 系统
   ├─ 基础
   ├─ QQ / OneBot
   ├─ 行为
   ├─ 日志
   ├─ Runtime
   ├─ WebUI
   ├─ 凭据
   └─ 高级
```

不要继续把每一个 Python module 做成一个一级菜单。

---

# 10. 总览 Dashboard 必须重新设计

首页目标：

> 用户打开 WebUI 5 秒内知道 Bot 当前是否正常、当前在做什么、AI 是否可用、QQ 是否连接。

顶部状态：

```text
CatooBot
● Online

QQ
● Connected

AI
● Ready

Runtime
● Running
```

核心状态卡：

```text
QQ
AI
世界
Runtime
```

世界卡至少展示：

```text
正在做什么
地点
模式
重要 Need
当前 Goal
当前 Action
```

AI 卡展示：

```text
当前模型
Provider
最近调用
成功率
429
5xx
Cooldown
```

Runtime 卡展示：

```text
运行时长
scheduler
当前 tick interval
最近 tick
队列情况
```

下方增加：

```text
最近事件
```

例如：

```text
世界变化
QQ 消息
决策
打断
恢复
AI 调用
错误
```

使用已有实时事件体系。

禁止每秒 HTTP polling。

---

# 11. AI 与模型页面是 v1.0 的重点

这一部分必须做到真正可用。

---

# 12. Provider 管理

页面：

```text
AI 与模型 → Provider
```

展示：

```text
Provider Name
状态
Base URL
API Key 状态
模型数量
最近错误
```

例如：

```text
WorkBuddy

● Connected

Base URL
http://127.0.0.1:7864/v1

API Key
● 已配置

Models
6

[测试连接]
[编辑]
[删除]
```

支持：

```text
+ 添加 Provider
```

---

# 13. 添加 Provider

不要让用户理解：

```text
api_key_env
```

界面直接：

```text
Provider 名称
Provider 类型
Base URL
API Key
```

Provider 类型默认：

```text
OpenAI Compatible
```

允许未来扩展其他类型。

保存时：

```text
config
+
secret
```

分开处理。

---

# 14. API Key 安全要求

API Key 绝对不要存进：

```text
config.yaml
```

默认继续采用：

```text
.env
```

或者现有项目已经使用的 Secret 存储机制。

WebUI 负责帮用户维护，而不是要求用户自己编辑 `.env`。

例如：

```text
API Key
•••••••••••••••

● 已配置

[更换]
[删除]
```

安全要求：

- API Key 不能通过普通 GET API 返回；
- 保存后只能返回 masked 状态；
- 前端刷新后也不能拿到明文；
- 日志绝不能打印 Secret；
- 错误信息不能泄露完整 Key；
- 修改使用后端安全写入；
- 写文件使用安全/原子替换；
- 保留已有 Secret 行；
- 不要破坏用户其他 `.env` 配置。

如果当前系统已经存在凭据管理机制，优先复用。

---

# 15. Provider 测试

不要只做简单：

```text
ping
```

应提供：

```text
连接测试
```

结果至少包括：

```text
Provider
HTTP 状态
连接耗时
模型可访问性
测试模型
响应耗时
错误类型
```

成功：

```text
✓ API 可用
✓ 模型可调用
Latency: 1.24s
```

失败：

```text
✕ HTTP 429
模型进入 cooldown
```

不要泄露 API Key。

---

# 16. 模型管理

页面：

```text
AI 与模型 → 模型
```

提供：

```text
+ 添加模型
```

表单：

```text
显示名称
Provider
Model ID
模型类型
启用
优先级
```

以后可以继续扩展：

```text
Temperature override
Context length
Timeout
Max tokens
```

但只有确认 Core 真正支持的字段才加入。

---

# 17. 模型列表

每个模型卡片显示：

```text
DeepSeek V4 Flash

● Ready

Provider
WorkBuddy

Model ID
deepseek-v4-flash

Priority
2

429
0

5xx
0

Cooldown
None

Last error
None

[测试]
[编辑]
[停用]
[设为默认]
```

---

# 18. 模型用途必须独立管理

页面：

```text
AI 与模型 → 模型用途
```

例如：

```text
聊天回复
[ DeepSeek V4.1 Flash ]

视觉理解
[ Qwen-VL ]

记忆抽取
[ GLM-5.3 Flash ]

Embedding
[ BGE-M3 ]

决策
[ GLM-5.3 Flash ]

Agent Planner
[ MiniMax M3 ]

Agent Evaluator
[ MiniMax M3 ]
```

但只能显示当前 Core 真正存在的模型用途。

绝对不要凭空新增不存在的能力。

---

# 19. 故障转移

提供一个可视化的 fallback chain。

例如：

```text
Primary

6 Astra
GLM-5.3

↓ 429 / hard failure

6 Sol
DeepSeek-V4-Pro

↓ 429 / hard failure

6 Luna
DeepSeek-V4.1-Flash

↓
...
```

允许：

```text
拖拽排序
启用/禁用
修改 retry/cooldown
```

但最终必须写入当前 Core 实际使用的配置结构。

不要在 WebUI 自己实现第二套 Router。

---

# 20. AI 测试台

增加：

```text
AI 与模型 → 测试台
```

允许选择：

```text
Provider
Model
```

输入：

```text
Prompt
```

点击：

```text
发送
```

结果展示：

```text
模型
延迟
Token
HTTP 状态
返回内容
```

这只是管理工具。

不能偷偷创建 Conversation / Memory / Sandbox Experience。

---

# 21. 配置中心必须成为新的核心能力

增加：

```text
系统 → 设置
```

不是一个简单 YAML 表单，而是：

```text
Configuration Center
```

---

# 22. Configuration Registry

后端建立统一 Registry。

例如：

```python
ConfigField(
    key="ai.temperature",
    label="默认温度",
    category="ai.generation",
    type="float",
    description="...",
    default=0.8,
    min_value=0,
    max_value=2,
    hot_reload=True,
    restart_required=False,
    advanced=False,
)
```

Registry 不是新的配置存储。

它只是：

> **告诉 WebUI 某个配置是什么、在哪里、如何编辑、是否生效。**

最终真实值仍然由现有 Config 系统管理。

---

# 23. 每个配置必须有“可解释状态”

配置 UI：

```text
默认温度

0.8

来源
WebUI Override

状态
✓ 已生效

热更新
✓ 支持

说明
控制未单独指定温度的模型请求。
```

如果需要重启：

```text
⚠ 修改将在重启后生效
```

如果功能未启用：

```text
⚠ 当前功能未启用
```

如果配置未被实际代码引用：

```text
⚠ 当前配置未使用

这是遗留/未启用字段，不建议修改。
```

不要假装所有字段都有效。

---

# 24. 配置 UI 分三层

默认：

```text
Basic
```

展示最常用的配置。

用户主动点：

```text
高级设置
```

显示：

```text
Advanced
```

再点：

```text
专家模式
```

显示：

```text
Expert
```

Expert 中可以提供：

```text
YAML
```

---

# 25. YAML 编辑器继续保留

但是重新定位为：

```text
系统 → 高级 → YAML
```

明确标记：

```text
Expert Mode
直接修改底层配置。
不熟悉配置结构的用户不建议使用。
```

保存前必须：

```text
parse
validate
preview changes
confirm
```

不要允许无校验覆盖。

---

# 26. 配置变更预览

用户修改多个设置后：

```text
待应用修改

ai.default_model
旧：glm-5.3
新：deepseek-v4-flash

runtime.tick_interval
旧：1
新：2

状态：
2 项可热更新
1 项需要重启
```

按钮：

```text
[取消]
[应用]
```

应用后：

```text
✓ 已应用

需要重启：
runtime.tick_interval

[现在重启]
```

---

# 27. “配置是否真的生效”必须可视化

增加配置状态 API。

例如：

```text
/api/v1/config/schema
/api/v1/config/effective
/api/v1/config/changes
```

每项至少返回：

```text
key
current_value
default_value
source
effective
hot_reload
restart_required
usage_status
description
```

其中：

```text
usage_status
```

来自真实代码审计/运行时信息，而不是前端猜测。

---

# 28. 不要为了显示“已生效”而造假

例如：

用户修改：

```text
foo.bar
```

如果后端并没有真正应用到 Runtime：

不能显示：

```text
✓ 已生效
```

必须显示：

```text
⚠ 已保存，但当前 Runtime 尚未加载
```

或者：

```text
⚠ 此配置当前没有运行时引用
```

---

# 29. Settings 分类

推荐：

```text
系统
├── 基础
├── QQ / OneBot
├── AI
├── 行为
├── 社交
├── 记忆
├── 媒体
├── Runtime
├── 日志
├── WebUI
├── 凭据
└── 高级
```

但不要让用户看到 Python class / YAML section 名字。

用：

```text
用户语言
```

而不是：

```text
内部实现语言
```

例如：

错误：

```text
sandbox.tick_seconds
```

界面：

```text
世界步进上限
```

下面再显示：

```text
内部键：
sandbox.tick_seconds
```

---

# 30. Character 页面

角色页面负责真正的角色运行信息。

但必须遵守：

> Character Bible 是 canonical source。

真实 Character Bible 如果仍然是本地私密文件：

- 不得上传到 Git；
- WebUI 不得意外暴露其完整原文；
- 普通配置不能直接覆盖 Character Bible 的 canonical semantics。

WebUI 可以管理：

```text
角色运行开关
显示名称
头像
Runtime overrides
```

但不能偷偷建立另一套“角色人格真相”。

---

# 31. Sandbox 页面

重新设计为：

```text
当前世界
当前行动
地点
模式
Needs
Goal
Action
Relationship
Commitment
```

只读状态为主。

如果当前 Core 某些字段允许管理：

明确区分：

```text
状态查看
```

和：

```text
配置修改
```

不要让用户误以为可以直接手动修改 Sandbox truth。

---

# 32. Social 页面

至少包括：

```text
Users
Groups
Social Spaces
Relationships
Commitments
```

清楚解释：

```text
Person
Group
Social Space
Relationship
Commitment
```

不要把它们混成一个“社交设置”。

---

# 33. Memory 页面

提供：

```text
搜索
过滤
时间
Person
Episode
Memory type
```

但是不要增加第二套 Memory 引擎。

现有 Memory Service 能做什么就展示什么。

---

# 34. Media / Tools / Agent

保持独立，但视觉与交互风格统一。

每页都遵守：

```text
状态
说明
配置
操作
实时结果
```

而不是简单堆表格。

---

# 35. Logs 页面

日志页面必须支持：

```text
Level
Category
Search
时间范围
自动滚动
暂停
清空视图
```

分类：

```text
WORLD
QQ
AI
DECISION
SOCIAL
MEMORY
ERROR
RUNTIME
```

与现有 narration / logging 体系对接。

不要为了 WebUI 重新创建第二套日志系统。

---

# 36. Runtime 页面

显示：

```text
Runtime status
Uptime
Scheduler
Tick interval
World revision
Cognitive revision
Current Action
Current Goal
Current Commitment
Queue
OneBot
WebSocket
```

但是：

> Runtime 页面是观察页面，不是让用户随便改 Core 内部状态。

---

# 37. 实时更新

优先使用现有：

```text
RealtimeHub
NarrationFeed
WebSocket
```

Vue 端建立统一：

```text
useRealtime()
```

事件进入后更新 Store。

不要让每个页面各自：

```text
setInterval(fetch...)
```

禁止使用“每秒请求一次 API”来模拟实时状态。

---

# 38. UI 设计要求

整体风格：

```text
Soft Dark / Soft Light
现代
克制
高可读性
高信息密度但不拥挤
```

不要：

```text
Bootstrap Admin 默认蓝
```

不要：

```text
过度霓虹
```

不要：

```text
满屏 Emoji
```

推荐：

```text
Lucide icons
12~16px border radius
低强度 shadow
柔和边框
清晰 Typography hierarchy
```

支持：

```text
Light
Dark
System
```

并记住用户主题。

---

# 39. Layout

桌面：

```text
Sidebar
+
Topbar
+
Content
```

Sidebar 支持折叠。

Mobile 至少保证：

```text
导航可以收起
表单不会横向溢出
表格支持响应式
```

---

# 40. 统一组件库

必须抽象：

```text
StatusBadge
MetricCard
ConfigField
ConfigGroup
ConfigSourceBadge
SaveBar
ConfirmDialog
DangerDialog
DataTable
EmptyState
ErrorState
LoadingState
Drawer
Modal
Form
SecretField
ProviderCard
ModelCard
ActivityFeed
```

这样以后添加页面不用复制 HTML。

---

# 41. API 设计

建立：

```text
/api/v1/
```

至少需要：

```text
status
dashboard
config
config/schema
config/effective
config/apply
config/validate

providers
providers/test

models
models/test
models/roles
models/failover

credentials

runtime
social
memory
logs

realtime
```

实际 Endpoint 根据现有 Service 调整。

不要重复实现已有后端业务。

---

# 42. API Schema

使用明确的数据模型。

禁止：

```python
return {"whatever": ...}
```

然后前端猜字段含义。

优先使用项目已有：

```text
Pydantic
dataclasses
typed schemas
```

统一 JSON contract。

---

# 43. 安全要求

必须保留并检查：

```text
Session auth
CSRF
权限检查
Secret redaction
```

所有 WebUI 写接口必须：

```text
authenticated
authorized
validated
```

危险操作：

```text
删除 Provider
删除 Credential
覆盖 YAML
重启
清理 Memory
```

必须二次确认。

---

# 44. 配置写入要求

配置修改必须：

```text
validate
→ preview
→ apply
→ report result
```

必要时：

```text
backup
→ atomic write
```

不能因为 WebUI 一次写入把整个配置文件破坏。

---

# 45. Migration 要求

现有 v0.8 用户升级到 v1.0：

必须：

```text
已有配置不丢
已有 Provider 不丢
已有模型不丢
已有 Memory 不丢
已有 Social 数据不丢
已有 WebUI 登录配置不丢
```

如果配置结构发生迁移：

建立：

```text
migration
```

而不是要求用户重新配置。

---

# 46. 兼容旧 Endpoint

在迁移期间，如果其他组件依赖旧 WebUI API：

不要直接删除。

先做：

```text
adapter
```

或者：

```text
deprecated endpoint
```

确认没有消费者后再删除。

---

# 47. 测试策略

必须建立三层测试。

## Backend

测试：

```text
Config Schema
Config Effective
Config Apply
Provider CRUD
Model CRUD
Credential
Failover
Auth
CSRF
Realtime
```

## Frontend

至少测试核心：

```text
Provider 添加
API Key 保存
Model 添加
Model 编辑
Failover 排序
Config 修改
Config Source
Restart warning
```

## E2E

至少覆盖：

```text
登录
→ Provider
→ 填 API Key
→ 测试
→ 添加 Model
→ 设为默认
→ 进入 Dashboard
→ 查看实时状态
```

---

# 48. 必须保证“真实可用配置”优先

这是整个项目最重要的验收标准：

用户能够只通过 WebUI 完成：

```text
首次配置 AI Provider
↓
填写 API Base URL
↓
填写 API Key
↓
测试 Provider
↓
添加模型
↓
测试模型
↓
设置默认模型
↓
设置 fallback
↓
启动/重启 Bot
↓
看到 AI Ready
```

无需手动编辑：

```text
config.yaml
overrides.yaml
.env
```

---

# 49. 但是不要把所有内部字段强行暴露出来

WebUI v1.0 不等于：

```text
把 AppConfig 每个字段做成 input
```

必须根据：

```text
真实使用
用户价值
风险
高级程度
```

筛选。

普通用户看：

```text
常用配置
```

高级用户看：

```text
Advanced
```

专家看：

```text
YAML
```

---

# 50. 运行时可编辑 vs 重启

所有 Configuration Registry 项必须明确：

```text
hot_reload = true / false
restart_required = true / false
```

如果后端支持热更新：

直接应用。

如果不支持：

明确提示：

```text
已保存
将在下一次启动时生效
```

不能假装实时生效。

---

# 51. 不允许 WebUI 自己创造第二套配置缓存真相

前端 Store 可以缓存 UI 状态。

但是：

```text
Backend
```

永远是真实配置来源。

不能出现：

```text
Vue says temperature = 0.8
Backend says temperature = 0.7
```

刷新后必须以后端 Effective Config 为准。

---

# 52. 不允许 WebUI 自己维护第二套 Model Router

模型优先级、fallback、cooldown 等必须最终交给现有 AI Router。

WebUI 只负责：

```text
编辑
展示
测试
```

不是自己执行：

```text
retry
fallback
selection
cooldown
```

---

# 53. 不允许 WebUI 改变 Phase 16 Core 行为

明确禁止因为 WebUI 重构而修改：

```text
Sandbox truth
RuntimeScheduler
ExternalInfluenceEvaluator
ConversationRuntime
ResponseCommitGuard
Goal
ActionInstance
Relationship
Commitment
Memory
OneBot Gateway
```

如果发现某个 WebUI 功能无法操作当前 Core：

先报告：

```text
缺少 Admin capability
```

再增加最小管理层接口。

不要直接绕过 Service 修改数据库或 Runtime 私有变量。

---

# 54. 实施阶段

严格按以下阶段执行。

## Phase W1 — Audit

输出：

```text
WEBUI_V1_AUDIT.md
WEBUI_CONFIG_MATRIX.md
WEBUI_API_CONTRACT.md
```

并 commit。

此阶段不删除旧 UI。

---

## Phase W2 — Backend API Foundation

建立：

```text
WebUI API
Config Registry
Effective Config
Provider API
Model API
Credential API
```

完成 backend tests。

此阶段旧 UI 继续正常运行。

---

## Phase W3 — Frontend Shell

建立：

```text
Vue
TypeScript
Vite
Design system
Layout
Navigation
Theme
Realtime
```

先完成 Dashboard / Settings 基础框架。

---

## Phase W4 — AI & Config

优先实现：

```text
Provider
API Key
Models
Roles
Failover
Testing
Config Center
Effective Config
```

这一阶段完成后必须能够脱离 YAML / `.env` 完成一次全新的 AI 配置。

---

## Phase W5 — Domain Pages

迁移：

```text
Character
Sandbox
Social
Memory
Media
Tools
Agent
Logs
Runtime
```

全部采用统一 UI。

---

## Phase W6 — Cutover

：

```text
/
    → v1

/legacy
    → v0.8
```

进行完整 E2E。

稳定后再决定是否删除旧 UI。

---

# 55. 每个阶段都必须独立提交

不要最后一次提交几万行。

建议 commit：

```text
webui-v1/w1-audit
webui-v1/w2-api
webui-v1/w3-shell
webui-v1/w4-ai-config
webui-v1/w5-domain
webui-v1/w6-cutover
```

每阶段必须：

```text
tests
lint
typecheck
build
```

通过后才能进入下一阶段。

---

# 56. Git 安全

严格禁止：

```text
force push
rebase shared main
filter-repo
BFG
rewrite history
```

正常 commit 即可。

---

# 57. Character Bible

继续遵守：

```text
config/character_bible.md
```

如果它是本地真实文件：

> 不得上传 GitHub。

WebUI 重构期间不得让真实 Character Bible 进入 public repo。

只允许：

```text
character_bible.example.*
```

---

# 58. 最终验收标准

WebUI v1.0 必须同时满足：

### UI

```text
✓ 清晰
✓ 美观
✓ Light / Dark
✓ Sidebar
✓ Responsive
✓ 统一组件
```

### Configuration

```text
✓ 不需要打开 YAML 才能完成常见配置
✓ 配置来源清楚
✓ 是否生效清楚
✓ 是否需要重启清楚
✓ 未使用配置不会伪装成有效配置
✓ Basic / Advanced / Expert
```

### AI

```text
✓ Provider 添加
✓ API Base URL
✓ API Key
✓ Provider Test
✓ Model 添加
✓ Model Test
✓ Model Role
✓ Failover
✓ Cooldown
✓ Usage
```

### Runtime

```text
✓ 实时状态
✓ 世界状态
✓ QQ 状态
✓ AI 状态
✓ Runtime 状态
✓ Realtime WebSocket
```

### 安全

```text
✓ Session
✓ CSRF
✓ Secret 不泄露
✓ Dangerous action confirmation
✓ Config validation
```

### Compatibility

```text
✓ 旧配置可迁移
✓ Core 行为不变
✓ OneBot 不受影响
✓ Sandbox 不受影响
✓ Memory 不受影响
✓ Social 不受影响
✓ Conversation 不受影响
```

---

# 59. 最终人工验收场景

必须实际执行以下流程：

## 场景 1：第一次配置

新建一个测试 Provider：

```text
Provider
Base URL
API Key
```

测试成功。

---

## 场景 2：添加模型

：

```text
添加 Model
测试
启用
设为默认
```

然后回 Dashboard：

```text
AI = Ready
```

---

## 场景 3：修改模型

修改：

```text
Priority
Enabled
Role
```

确认真实 AI Router 使用新配置。

---

## 场景 4：故障转移

让主模型模拟：

```text
429
```

确认 UI 可以看到：

```text
Cooldown
Fallback
```

且实际 Router 使用备用模型。

---

## 场景 5：配置热更新

修改一个明确支持热更新的配置：

```text
WebUI
Behavior
AI
```

确认无需重启即可看到 Runtime 使用新值。

---

## 场景 6：需要重启的配置

修改明确需要重启的字段。

UI 必须准确显示：

```text
保存成功
但当前 Runtime 未加载
需要重启
```

---

## 场景 7：实时世界

启动 Bot。

让 Sandbox 状态发生变化。

Dashboard 必须实时显示。

不能靠每秒 HTTP polling。

---

## 场景 8：安全

确认：

```text
API Key
```

在：

```text
API response
console
frontend state debug
logs
```

中都不会出现明文。

---

# 60. 最终报告

完成所有阶段后，输出：

```text
# WebUI v1.0 Final Report

1. 当前基线 commit
2. W1~W6 每阶段 commit
3. 删除/迁移了哪些旧页面
4. 新增哪些 API
5. 新增哪些配置 Registry
6. 支持多少配置项通过 WebUI 修改
7. 哪些配置支持热更新
8. 哪些配置要求重启
9. 哪些配置标记为 unused / conditional / legacy
10. Provider / Model / Credential 功能
11. Failover 功能
12. Realtime 功能
13. 安全措施
14. Migration 方案
15. 单元测试数量
16. E2E 测试结果
17. Frontend build
18. ruff
19. mypy
20. pytest
21. CI
22. 最终 commit
23. 当前 Git working tree
24. 是否保留 legacy UI
```

---

# 61. 最重要的最终原则

整个 WebUI v1.0 必须遵守：

```text
不要让用户理解程序内部结构
              ↓
让用户表达“我想让 Bot 做什么”
              ↓
WebUI 找到对应配置
              ↓
Backend 找到真实 Config / Service
              ↓
真实 Runtime 应用
              ↓
UI 明确反馈“是否真的生效”
```

最终用户应该能够：

```text
添加 AI
添加模型
填写 API
选择默认模型
设置备用模型
调整行为
查看世界
查看社交
查看记忆
查看运行状态
查看日志
```

全部通过 WebUI 完成。

而不是：

```text
打开 config.yaml
打开 overrides.yaml
打开 .env
猜参数
重启
再看有没有生效
```

---

# 62. 开始执行前的最后约束

在开始 W1 之前：

1. 读取并审计真实代码；
2. 不根据文件名猜功能；
3. 不删除旧 UI；
4. 不修改 CatooBot Core 行为；
5. 不上传 Character Bible；
6. 不修改 Git history；
7. 不进入新的 Core Phase；
8. 所有无法确认“是否真实生效”的配置必须标记出来，而不是猜测；
9. 每完成一个阶段先运行测试，再提交；
10. 如果发现架构问题，优先形成最小兼容层，而不是直接重写 Core。

**先完成 W1 审计并形成 `WEBUI_V1_AUDIT.md`、`WEBUI_CONFIG_MATRIX.md`、`WEBUI_API_CONTRACT.md`。**

**只有 W1 审计完成且当前 WebUI / Config / Service 的真实关系已经确认后，才进入 W2。**

不要跳过审计直接开始写 UI。

**目标不是“做一个更漂亮的 v0.8”，而是建立一个可以长期维护、普通用户也能直接使用的 CatooBot WebUI v1.0。**