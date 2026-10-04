# CatooBot v2.1 · WebUI v1.0 — W4 AI & Configuration

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

当前状态：

```text
W1 Audit
✅

W2 Backend API
✅

W3 Frontend Shell
✅

W4 AI & Configuration
← 本阶段

W5 Domain Pages
NOT STARTED
```

当前 WebUI：

```text
Vue 3
TypeScript
Vite
Vue Router
Pinia
Naive UI
```

当前后端：

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
Realtime / WebSocket
```

当前质量基线：

```text
pytest: 1533 passed
frontend tests: 36 passed
ruff: PASS
mypy: PASS
frontend typecheck: PASS
frontend build: PASS
CI: success
working tree: clean
```

---

# 1. W4 的任务定位

本阶段名称：

> **WebUI v1.0 · W4 AI & Configuration**

目标：

> **让普通用户无需打开 `config.yaml`、`overrides.yaml` 或 `.env`，即可通过 WebUI 完成 CatooBot 的 AI Provider、API Key、Model、Model Role、Fallback 与常用系统配置。**

最终用户应该可以完成：

```text
添加 Provider
↓
填写 Base URL
↓
填写 API Key
↓
测试 Provider
↓
添加 Model
↓
测试 Model
↓
设为默认聊天模型
↓
设置其他模型用途
↓
设置 fallback 顺序
↓
查看 cooldown / 429 / 5xx
↓
修改常用配置
↓
知道哪些已经生效
↓
知道哪些需要重启
```

---

# 2. W4 的核心原则

必须坚持：

```text
WebUI
    ↓
/api/v1
    ↓
现有 Admin Services / Config System / AI Router
    ↓
CatooBot Core
```

不要在 Vue 中创建：

```text
第二套 Model Router
第二套 Config Store
第二套 Credential Store
第二套 Failover Engine
第二套 Cooldown Engine
```

WebUI：

```text
展示
编辑
测试
解释
```

Core：

```text
真实配置
真实状态
真实模型选择
真实 cooldown
真实 failover
```

---

# 3. W4 前先处理 API Contract discrepancy

W3 已确认：

```text
POST /api/v1/ai/models/{name}/test
```

当前实际返回：

```text
{
    ok,
    latency_ms,
    model,
    error_type,
    message
}
```

而 `WEBUI_API_CONTRACT.md` 某处示例使用：

```text
attempts
final_model
```

W4 开始编码前：

1. 重新读取 `docs/WEBUI_API_CONTRACT.md`；
2. 阅读 W2 实际 endpoint implementation；
3. 统一 API contract；
4. 必须保证 Backend implementation、contract、Frontend types 三者一致。

不要：

```text
前端同时兼容两个完全不同的 schema
```

除非为了短期 migration 确有必要。

优先修正 contract 文档或最小后端适配，使最终 v1 API 只有一个明确格式。

---

# 4. W4 不做的事情

本阶段不要迁移：

```text
Memory
Social
Character
Sandbox
Tools
Agent
Sticker
Logs
Runtime
```

这些属于 W5。

但可以在 Dashboard/AI 页面使用已有：

```text
AI status
Runtime status
```

作为导航反馈。

---

# 5. 最终 AI 页面信息架构

不要继续保留：

```text
模型
配置 → AI 模型
凭据
```

这种分散入口。

统一：

```text
AI 与模型
├── 概览
├── Provider
├── 模型
├── 模型用途
├── 故障转移
├── 用量
└── 测试台
```

系统配置：

```text
系统
├── 基础
├── AI
├── QQ / OneBot
├── 行为
├── Runtime
├── 日志
├── WebUI
└── 高级
```

AI 页面与 Config Center 必须互相知道对方存在。

---

# 6. AI Overview

页面：

```text
/ai
```

目标：

> 打开后 5 秒内知道 AI 系统是否正常。

顶部状态：

```text
AI
● Ready
```

核心卡片：

```text
Provider
Models
Current Chat Model
Fallback
Cooldown
Errors
```

例如：

```text
Provider
3 configured
2 online

Models
8 enabled
2 disabled

Chat
DeepSeek V4.1 Flash

Fallback
3 models

429
2

5xx
0

Cooldown
1
```

全部来自真实 API。

禁止 mock 数据。

---

# 7. AI Overview 增加配置缺失提醒

如果：

```text
没有 Provider
```

显示：

```text
⚠ 尚未配置 AI Provider

[添加 Provider]
```

如果：

```text
Provider 有了
但没有 API Key
```

显示：

```text
⚠ Provider 缺少 API Key

[配置凭据]
```

如果：

```text
Provider 有 API Key
但没有 Model
```

显示：

```text
⚠ 尚未配置模型

[添加模型]
```

如果：

```text
Model 有了
但未绑定 chat role
```

显示：

```text
⚠ 尚未设置默认聊天模型

[配置模型用途]
```

这就是“低门槛”的核心之一：

> UI 主动告诉用户下一步该做什么。

---

# 8. Provider 页面

页面：

```text
/ai/providers
```

展示 Provider Card / Table。

每个 Provider：

```text
名称
类型
Base URL
状态
API Key 状态
Models 数量
Last error
```

示例：

```text
WorkBuddy

● Ready

OpenAI Compatible

http://127.0.0.1:7864/v1

API Key
● 已配置

Models
6

[测试]
[编辑]
[删除]
```

---

# 9. 添加 Provider

点击：

```text
+ 添加 Provider
```

打开：

```text
Drawer / Modal
```

表单：

```text
Provider 名称
Provider 类型
Base URL
API Key
```

高级字段：

```text
启用
```

默认：

```text
enabled = true
```

如果 Core 不支持 Provider Type 的自由扩展：

按现有真实 enum 渲染。

---

# 10. Provider Type

普通用户最常见：

```text
OpenAI Compatible
```

如果当前 Core 支持多种 Provider 类型：

动态读取后端 schema。

不要前端硬编码一套与 Core 不一致的类型。

---

# 11. Base URL

输入时提供说明：

```text
例如：

https://api.openai.com/v1
http://127.0.0.1:7864/v1
```

允许：

```text
localhost
127.0.0.1
内网地址
https
http
```

只进行真正必要的 URL 校验。

---

# 12. API Key

使用：

```text
SecretField
```

默认：

```text
••••••••••••
```

旁边：

```text
● 已配置
```

编辑时：

```text
保持现有 Key
```

不是把 masked value 当成新 Key 保存。

必须区分：

```text
unchanged
replace
clear
```

---

# 13. API Key 更换

点击：

```text
更换
```

才允许输入新 Key。

保存：

```text
旧 Key
↓
替换
↓
热加载
↓
Provider Test
```

成功以后：

```text
● 已配置
```

不要把新 Key 返回前端。

---

# 14. Provider 测试

点击：

```text
测试连接
```

显示 modal：

```text
正在测试...
```

然后：

```text
✓ Provider 可连接

HTTP
200

Latency
1.21s

Credential
✓

Provider
✓
```

失败：

```text
✕ Provider 测试失败

HTTP 401

API Key 无效
```

错误使用后端 `message`。

不要在前端重新解释错误。

---

# 15. Provider Test 不得修改 Runtime

Provider Test 只是：

```text
diagnostic request
```

不得：

```text
改变 Sandbox
创建 Conversation
写 Memory
创建 Experience
改变 Relationship
```

---

# 16. Provider 编辑

Provider Edit 必须支持：

```text
名称
Base URL
enabled
Credential
```

如果修改名称涉及引用：

必须由后端处理真实迁移。

前端不能：

```text
delete old
create new
```

模拟重命名。

---

# 17. Provider 删除

如果 Provider 正被模型使用：

API 返回：

```text
409 ai.provider_in_use
```

UI 显示：

```text
该 Provider 仍被 4 个模型使用。

[查看模型]
[取消]
```

如果后端支持：

```text
force delete
```

必须二次确认：

```text
将同时删除/解绑相关模型
继续？
```

危险操作不能默认 force。

---

# 18. Model 页面

页面：

```text
/ai/models
```

使用：

```text
Table + Card responsive
```

字段：

```text
显示名称
Model ID
Provider
启用状态
Priority
Role
Cooldown
429
5xx
Last error
```

---

# 19. 添加 Model

表单：

```text
显示名称
Provider
Model ID
启用
```

如果当前 Core 支持：

```text
Priority
```

也提供。

不要增加 Core 尚不存在的参数。

---

# 20. Provider 下拉列表

只能出现：

```text
真实存在
enabled
且可用于模型
```

的 Provider。

如果没有 Provider：

直接显示：

```text
请先创建 Provider

[添加 Provider]
```

---

# 21. Model ID

清晰区分：

```text
显示名称
```

和：

```text
Model ID
```

例如：

```text
名称：
DeepSeek V4 Flash

Model ID：
deepseek-v4-flash
```

---

# 22. Model Test

模型创建后：

```text
[测试模型]
```

真实调用：

```text
Provider
↓
Credential
↓
Model Router / router.chat
↓
真实模型
```

返回：

```text
状态
Model
Latency
Error Type
Message
```

按统一后的 API Contract。

---

# 23. Test Prompt

模型测试 UI 提供：

```text
测试内容
```

默认：

```text
你好，请简单回复一句“测试成功”。
```

允许用户修改。

不要硬编码角色 Persona。

这是：

```text
raw model connectivity test
```

不是正式角色对话。

---

# 24. Model Status

模型状态：

```text
● Ready
● Cooldown
● Disabled
⚠ Error
```

不要仅仅使用颜色。

必须有文本。

---

# 25. Cooldown

显示：

```text
Cooldown
12s remaining
```

这个状态来自真实 AI Router。

WebUI 不实现倒计时逻辑作为第二真相。

可以本地视觉倒计时：

但最终状态必须以后端为准。

---

# 26. Model Usage

显示：

```text
请求次数
成功
失败
429
5xx
```

如果后端提供：

```text
Purpose
Provider
Error Type
```

提供过滤。

不要过度制作分析图表。

---

# 27. Model Role 页面

页面：

```text
/ai/roles
```

目标：

> 告诉普通用户“哪个模型负责什么”。

不要显示：

```text
chat
vision
decision
```

而直接显示：

```text
聊天回复
图片理解
决策
会话判断
记忆抽取
社交判断
Planner
Evaluator
Embedding
```

内部 key 可放：

```text
高级信息：
chat
vision
...
```

---

# 28. Role Card

例如：

```text
聊天回复

当前：
DeepSeek V4.1 Flash

说明：
负责普通 QQ 对话的主要回复生成。

[更换]
```

下面：

```text
ⓘ 这个模型负责什么？
```

点击显示解释。

---

# 29. Role 与默认模型

如果：

```text
chat
```

当前真实语义是：

```text
order[0]
```

那么 UI 必须明确：

```text
默认聊天模型 = 当前 Fallback Chain 第一个模型
```

不要让用户修改：

```text
chat
```

后又发现 Router priority 没变化。

前端显示必须与实际 Router 语义一致。

---

# 30. Role Restart Warning

例如：

```text
vision
embedding
```

如果修改需要重启：

显示：

```text
已保存

⚠ 当前 Runtime 尚未加载

重启后生效
```

不要假装已经使用新模型。

---

# 31. Failover 页面

页面：

```text
/ai/failover
```

目标：

> 让用户看到模型真正的 fallback 顺序。

例如：

```text
主模型

1. DeepSeek V4.1 Flash
   ● Enabled

↓

2. GLM-5.3 Flash
   ● Enabled

↓

3. MiniMax M3
   ● Enabled
```

---

# 32. Failover 排序

允许：

```text
拖拽
```

或：

```text
上移
下移
```

具体 UX 根据依赖选择。

保存：

```text
PUT /api/v1/ai/models/order
```

立即刷新真实 Router 状态。

---

# 33. Failover 的解释

页面必须明确：

```text
故障转移由 Model Router 执行。
```

WebUI 只是：

```text
配置顺序
启停模型
```

不要让用户误以为：

```text
网页在执行 retry
```

---

# 34. 429 / 5xx 说明

提供 tooltip：

```text
429：
请求过多，当前模型进入 cooldown。

5xx：
Provider / Model 服务端错误。
```

最终具体策略仍由 Model Router 决定。

---

# 35. AI Router Reset

如果 API：

```text
POST /api/v1/ai/router/reset
```

存在：

UI 可以放在：

```text
AI 与模型 → 高级操作
```

而不是显眼主按钮。

显示：

```text
重置 Router 内存状态

说明：
清除当前运行时 cooldown / transient state。
不会删除模型配置。

[重置]
```

必须二次确认。

---

# 36. AI Test Bench

页面：

```text
/ai/test
```

布局：

```text
Provider
[ WorkBuddy ▼ ]

Model
[ DeepSeek V4 Flash ▼ ]

Prompt
[                                  ]

[发送测试]
```

结果：

```text
HTTP
Latency
Model
Error
Response
```

---

# 37. Test Bench 安全

禁止：

```text
测试结果自动写 Memory
测试结果自动进入 Conversation
测试结果修改 Relationship
```

这是：

> 独立的管理诊断功能。

---

# 38. Config Center

页面：

```text
/system/settings
```

目标：

> **普通用户不需要理解 YAML。**

页面结构：

```text
设置

[基础]
[AI]
[QQ / OneBot]
[行为]
[Runtime]
[日志]
[WebUI]
[高级]
```

具体分类根据 Config Registry。

---

# 39. Basic / Advanced / Expert

顶部：

```text
基础设置
○ 基础
○ 高级
○ 专家
```

默认：

```text
基础
```

普通用户不要一打开就看到 272 个字段。

---

# 40. Config 搜索

必须有：

```text
搜索设置
```

支持：

```text
中文 label
description
内部 key
```

例如：

```text
搜索：
温度

→ 默认温度
→ Temperature
→ ai.temperature
```

---

# 41. Config Category

每组配置：

```text
标题
说明
配置项
```

不要把所有字段堆成一张巨大表。

---

# 42. Config Field

每个 field 应显示：

```text
名称
当前值
说明
来源
生效状态
```

例如：

```text
默认温度

0.8

控制未单独覆盖的模型请求温度。

来源：
WebUI Override

● 已生效

热更新：
支持
```

---

# 43. Config Source Badge

来源：

```text
Default
Config File
Model Config
Override
Environment
```

使用统一 Badge。

点击：

```text
查看来源
```

显示：

```text
来源：
overrides.yaml

内部键：
ai.temperature
```

---

# 44. Config Effective Badge

状态：

```text
✓ 已生效
⚠ 重启后生效
⚠ 当前未使用
ⓘ 条件生效
```

必须同时有：

```text
icon + text
```

不能只依靠颜色。

---

# 45. Config 使用状态

对于：

```text
DEFINED_BUT_UNUSED
LEGACY
```

默认：

不显示。

Expert 模式：

显示：

```text
⚠ 当前未使用
```

说明：

```text
该配置项当前没有被 Runtime 使用。
修改它不会改变 Bot 行为。
```

这正是本项目想解决的问题。

---

# 46. Config Conditional

例如：

```text
某个功能关闭
```

对应配置：

```text
⚠ 当前功能未启用
```

但配置本身可以修改。

这样用户知道：

> “我改了，但是现在不会产生效果。”

---

# 47. Config 修改方式

根据类型自动渲染：

```text
boolean
→ Switch

enum
→ Select

string
→ Input

secret
→ SecretField

integer
→ NumberInput

float
→ NumberInput / Slider

duration
→ NumberInput + 单位

list
→ Editable list

mapping
→ Structured editor
```

不要让所有东西都变成文本框。

---

# 48. Constraints

从 Config Registry 使用：

```text
min
max
enum
pattern
```

前端展示：

```text
0 ~ 2
```

并且：

后端依旧重新验证。

前端验证不能代替后端验证。

---

# 49. 配置修改状态栏

页面底部：

```text
有未保存修改

[取消]
[应用修改]
```

避免每改一个字段就提交一次。

如果当前某些字段必须即时应用：

按照后端实际语义处理。

默认：

> 以批量 Apply 为主。

---

# 50. Apply Preview

点击：

```text
应用修改
```

先显示：

```text
确认修改

ai.temperature
0.8 → 0.9

runtime.xxx
1 → 2

2 项修改

✓ 1 项立即生效
⚠ 1 项需要重启
```

然后：

```text
[取消]
[确认应用]
```

---

# 51. Apply Result

成功：

```text
✓ 设置已保存

立即生效：1
需要重启：1
```

并刷新：

```text
Effective Config
```

---

# 52. Restart Banner

有 pending restart：

顶部显示：

```text
⚠ 有配置等待重启
```

点击：

```text
查看修改
```

进入：

```text
/system/settings/restart-pending
```

---

# 53. 不要自动重启

W4 默认：

```text
不自动 restart
```

除非已有明确安全 API 且用户主动点击。

用户修改配置后：

```text
保存成功
↓
告诉他是否需要重启
```

---

# 54. Expert YAML

保留：

```text
/system/settings/advanced
```

提供：

```text
Raw YAML
```

但：

> 这是最后的 Expert fallback，不是普通配置入口。

---

# 55. Raw YAML UI

需要：

```text
Editor
Validate
Preview
Apply
```

流程：

```text
编辑
↓
Validate
↓
发现错误 / 成功
↓
Preview
↓
Apply
```

不要直接：

```text
Save
```

覆盖。

---

# 56. Raw YAML Warning

顶部明确：

```text
专家模式

直接修改底层配置。
错误配置可能导致 Bot 无法启动。

普通用户建议使用标准设置页面。
```

---

# 57. Raw YAML 不返回 Secret

如果配置文件中有：

```text
secret
password
token
api key
```

后端既然已经禁止明文泄露：

前端也必须继续保持 masked。

不要因为是 Expert 模式就绕过安全限制。

---

# 58. Credentials 页面

如果 `/credentials` 仍需要独立入口：

重新定位为：

```text
/system/credentials
```

但：

普通 Provider API Key 的配置应该在：

```text
Provider 编辑
```

直接完成。

Credentials 页面主要用于：

```text
查看已配置凭据
管理其他 domain credentials
```

---

# 59. Credential UI

显示：

```text
Provider
Credential
Status
Source
```

不能显示：

```text
真实 Key
```

操作：

```text
更换
删除
测试
```

---

# 60. AI 与 Config 的联动

例如：

用户没有模型。

点击：

```text
聊天模型
```

应该直接给：

```text
暂无模型

[添加模型]
```

点击以后打开：

```text
Add Model
```

添加完成：

自动返回。

这是低门槛的重要交互。

---

# 61. Deep-link

支持：

```text
/ai/providers?create=1
/ai/models?create=1
/system/settings?focus=ai.temperature
```

未来可以让 Dashboard 的告警直接跳到对应配置。

---

# 62. Dashboard AI Warning → 配置页

例如：

```text
AI not ready
```

点击：

```text
配置 AI
```

跳：

```text
/ai
```

并自动聚焦问题。

---

# 63. AI 状态详情

AI Overview 显示：

```text
Ready
Degraded
Not configured
Unavailable
```

定义来自真实 API。

不要由前端自己用：

```text
provider_count > 0
```

猜测。

后端应成为状态真相。

---

# 64. AI Usage

页面：

```text
/ai/usage
```

提供：

```text
时间段
Provider
Model
Purpose
Error Type
```

最基础显示：

```text
请求数
成功数
失败数
429
5xx
```

如果 API 支持：

```text
Latency
```

再显示。

---

# 65. Usage 不必过度做 BI

不要在 W4 制作：

```text
复杂 analytics dashboard
几十张图
趋势预测
```

这里只需要让用户知道：

> 哪个模型在用、是否频繁报错、哪个 Provider 有问题。

---

# 66. Model Role Explanation

每个 Role 必须有中文说明。

示例：

```text
聊天回复
负责普通对话的主要回答生成。

视觉理解
负责图片内容分析。

社交判断
负责需要社交上下文的判断。

记忆抽取
负责从高价值互动中提取可保存信息。
```

说明必须来自实际 Core 能力。

不得凭空创造职责。

---

# 67. AI 配置向导

增加：

```text
/ai/setup
```

或者 Modal Wizard。

首次没有配置 AI 时：

```text
欢迎使用 CatooBot

第 1 步
添加 Provider

第 2 步
填写 API Key

第 3 步
添加 Model

第 4 步
设为聊天模型

第 5 步
测试

✓ AI Ready
```

---

# 68. Setup Wizard 不创建第二套配置逻辑

Wizard 只是调用：

```text
现有 /api/v1
```

不要写：

```text
wizard-specific storage
```

---

# 69. First Run Detection

如果：

```text
AI 未配置
```

Dashboard 可以：

```text
Setup required
```

但不能阻止用户查看其他页面。

---

# 70. AI Ready 之后

Wizard 完成：

```text
Provider
Credential
Model
Role
```

确认：

```text
AI Router
```

真实可调用。

---

# 71. Router Reset UI

只放在：

```text
高级操作
```

点击后二次确认：

```text
这不会删除模型配置。
只清除当前运行中的临时 Router 状态。
```

---

# 72. Danger Confirmation

统一使用：

```text
ConfirmDialog
```

危险操作包括：

```text
删除 Provider
删除 Credential
删除 Model
Force Delete
Reset Router
Raw Config Apply
```

---

# 73. 未保存修改保护

如果页面有：

```text
dirty state
```

用户离开：

```text
是否放弃未保存修改？
```

浏览器关闭可以使用：

```text
beforeunload
```

但不要影响正常路由。

---

# 74. Store 架构

建议：

```text
stores/ai.ts
stores/config.ts
stores/credentials.ts
```

负责：

```text
fetch
cache
mutation
invalidate
```

但不要让 Store 成为第二套业务逻辑。

---

# 75. Server Truth 优先

Mutation 完成后：

不要只：

```text
store.foo = localValue
```

必须优先：

```text
save
↓
reload effective state
↓
update store
```

这样可以避免：

```text
前端显示成功
但 Backend 实际拒绝
```

导致 UI 假状态。

---

# 76. Optimistic UI 谨慎使用

Provider 删除 / Model reorder 可以使用：

```text
loading state
```

而不是强制 optimistic update。

尤其：

```text
Config Apply
Credentials
Provider
Model
```

以 Server confirmation 为准。

---

# 77. API error mapping

统一：

```text
ai.provider_in_use
ai.provider_invalid
ai.model_invalid
ai.credential_missing
config.restart_required
config.readonly_key
config.base_defined
```

如果后端 code 存在：

UI 必须：

```text
code → 用户友好信息
```

但保留：

```text
专家详情
```

用于调试。

---

# 78. UI 文案原则

不要：

```text
PATCH /api/v1/...
```

不要：

```text
ai.provider_in_use
```

普通用户只看到自然语言。

高级模式可以显示：

```text
错误代码：ai.provider_in_use
```

---

# 79. 主题

继续使用 W3 的：

```text
Light
Dark
System
```

W4 不得重新创建主题系统。

---

# 80. 组件复用

必须使用 W3：

```text
PageHeader
MetricCard
StatusBadge
LoadingState
ErrorState
EmptyState
ConfirmDialog
Toast
```

同时新增：

```text
ConfigField
ConfigSection
ConfigSourceBadge
ConfigStatusBadge
SecretField
ProviderCard
ModelTable
RoleCard
FailoverList
TestResult
SetupWizard
DirtyBar
RestartBanner
```

---

# 81. 不要让 AI 页面全部使用 DataTable

适合：

```text
Provider
Model
Usage
```

使用 Table。

适合：

```text
Provider Overview
Role
Setup
```

使用 Card。

---

# 82. 响应式

桌面：

```text
2~4 columns
```

平板：

```text
2 columns
```

移动：

```text
1 column
```

Model table 移动端可以：

```text
Card
```

不要强迫用户横向滚动整个页面。

---

# 83. Loading

Provider 列表：

```text
Skeleton
```

Model 列表：

```text
Skeleton
```

Config：

```text
Skeleton
```

不要统一显示：

```text
Loading...
```

---

# 84. Empty state

没有 Provider：

```text
还没有 AI Provider

添加你的第一个 Provider。
```

没有 Model：

```text
Provider 已配置，但还没有模型。

添加模型。
```

没有 Role：

```text
模型已存在，但还没有设置用途。
```

---

# 85. Secret input UX

API Key：

```text
••••••••••••
```

支持：

```text
显示 / 隐藏
```

但：

> 一旦保存以后，GET API 永远不能返回完整值。

---

# 86. Config Editor UX

对于 boolean：

```text
Switch
```

对于数值：

```text
InputNumber
```

如果 Registry 提供 min/max：

显示：

```text
0 ~ 2
```

对于 enum：

```text
Select
```

对于 text：

```text
Input
```

对于长文本：

```text
Textarea
```

---

# 87. Advanced / Expert 控制

默认：

```text
Basic
```

切换：

```text
Advanced
Expert
```

不要在页面切换时丢掉用户未保存修改。

---

# 88. Config Search

搜索结果支持：

```text
关键词
```

匹配：

```text
label
description
key
```

每个结果显示：

```text
名称
所在分类
级别
状态
```

---

# 89. Config dependency

如果配置 A 控制配置 B：

例如：

```text
功能开关 = false
```

B：

```text
disabled
```

并提示：

```text
当前功能未启用。
```

不要隐藏配置 B 到用户完全不知道它存在。

---

# 90. Restart Pending

全局 Banner：

```text
⚠ 2 项设置将在重启后生效

查看
```

页面：

```text
列出 pending keys
旧值
新值
原因
```

---

# 91. WebUI Config

可以管理：

```text
Theme
narration
WebUI behavior
```

但必须以 Config Registry 实际暴露字段为准。

不能因为 WebUI 自己需要某个 setting，就创建第二份。

---

# 92. AI Settings

只显示真实 active config。

例如：

```text
AI enabled
Default temperature
Timeout
Cooldown
```

具体字段来自：

```text
Config Registry
```

而不是人工重新列一遍。

---

# 93. Config Registry 与 Frontend Field Mapping

Frontend 不要：

```ts
switch (key) {
  case "ai.temperature":
...
}
```

到处硬编码。

建立：

```text
ConfigFieldRenderer
```

根据 Registry：

```text
type
constraints
level
sensitive
```

动态选择组件。

允许少量特殊字段使用 custom renderer。

---

# 94. Custom Renderer

如果某字段过于复杂：

```text
AI model
Failover
Credential
```

可以：

```text
custom component
```

但仍然使用统一 Config API。

---

# 95. Configuration Categories

至少：

```text
基础
AI
QQ / OneBot
行为
社交
记忆
媒体
Runtime
日志
WebUI
高级
```

实际根据 Registry。

---

# 96. Expert Config

Expert 页面可以查看：

```text
全部字段
```

包括：

```text
unused
legacy
```

必须明确：

```text
⚠ 当前字段未使用
```

---

# 97. Raw YAML Editor

如果 W2 已支持：

```text
GET /api/v1/config/raw
PUT /api/v1/config/raw
POST /api/v1/config/validate
```

W4 只需要接 UI。

流程：

```text
打开
↓
加载
↓
编辑
↓
Validate
↓
Preview
↓
Apply
```

---

# 98. 不要让 Raw YAML 变成主入口

Basic：

```text
Settings
```

Expert：

```text
Raw YAML
```

它必须明显低优先级。

---

# 99. 配置导入 / 导出

W4 暂时：

**不要增加完整配置导出功能。**

原因：

```text
Secret
Character Bible
环境相关配置
```

容易造成泄露。

如果现有 API 已有 raw export：

前端默认隐藏。

---

# 100. API Key Export

绝对禁止：

```text
导出所有 API Key
```

---

# 101. Provider / Model Duplicate

新增同名：

后端返回：

```text
409
```

前端显示：

```text
已有同名 Provider。
```

不要静默覆盖。

---

# 102. Model Delete

删除前：

显示：

```text
该模型当前绑定：
聊天回复
社交判断

删除后这些用途将失效。
```

如果后端允许删除：

二次确认。

更安全：

如果正在被 Role 引用：

```text
409
```

并要求先解绑。

---

# 103. Role Delete / Disable

如果一个重要 Role 没有模型：

不能让 UI 假装正常。

显示：

```text
⚠ 未配置
```

---

# 104. Model Reorder Dirty State

拖动顺序以后：

```text
未保存
```

点击：

```text
保存顺序
```

调用：

```text
PUT /api/v1/ai/models/order
```

成功后：

重新获取：

```text
models
roles
```

---

# 105. Router Reset

Reset 成功：

刷新：

```text
AI status
Models
Cooldown
```

确保：

```text
in_cooldown
```

回到真实状态。

---

# 106. Setup Wizard 完成条件

只有当：

```text
Provider exists
Credential configured
Model exists
Chat role configured
Model Test success
```

才显示：

```text
✓ AI Ready
```

不要仅仅：

```text
Provider count > 0
```

就显示 Ready。

---

# 107. Test Result 不要持久化到 Memory

Provider / Model Test：

```text
runtime diagnostic
```

不要：

```text
Memory
Conversation
Experience
```

---

# 108. Config Apply 与 Runtime

Apply 后：

如果热更新：

```text
reload effective config
```

如果需要重启：

```text
pending restart
```

不要：

```text
fake reload
```

---

# 109. WebSocket AI events

如果 W2 realtime topic 已支持：

```text
ai
```

W4 可以用于：

```text
Model status
429
Cooldown
AI request
```

但：

> WebSocket 事件只能更新显示，最终状态仍以 API effective state 为准。

---

# 110. AI Event Feed

Overview 可以显示：

```text
AI request
Model changed
429
Cooldown
Provider error
Router reset
```

但不显示：

```text
API Key
Prompt 内容
完整模型输出
```

除非用户明确在 Test Bench 查看。

---

# 111. Prompt Privacy

不要把正式用户对话内容加入：

```text
AI usage feed
```

默认只显示：

```text
purpose
model
latency
status
```

---

# 112. Token Usage

只有后端真正有：

```text
token usage
```

才显示。

没有：

不要估算。

---

# 113. Cost

W4 不做复杂费用估算。

除非 Provider 返回明确成本。

不要凭空计算。

---

# 114. Model Health

Model health 可以基于后端：

```text
ready
cooldown
failure_count
last_error
```

不要自己在前端统计一个不同的 health。

---

# 115. Provider Health

同理：

```text
configured
credential
last test
last error
```

尽量来自 backend。

---

# 116. AI Dashboard 的最终目标

用户打开：

```text
/ai
```

应该一眼看懂：

```text
AI 当前是否正常
谁在工作
有没有备用
有没有 Key 问题
有没有模型问题
下一步该怎么做
```

而不是：

```text
一堆配置字段
```

---

# 117. Configuration Dashboard 的最终目标

用户打开：

```text
/system/settings
```

应该一眼知道：

```text
哪些设置最常用
哪些已经生效
哪些需要重启
哪些没在使用
```

---

# 118. W4 测试

必须增加：

## Provider UI

```text
list
create
edit
delete
test
credential replace
```

## Model UI

```text
list
create
edit
delete
test
enable
disable
reorder
```

## Role UI

```text
list
change
restart warning
```

## Config UI

```text
schema
render
search
edit
validate
preview
apply
restart pending
unused
conditional
```

## Secret

```text
masked
never exposed
```

---

# 119. AI Integration Test

必须至少有一次完整链路：

```text
WebUI
↓
Create Provider
↓
Set Credential
↓
Create Model
↓
Test Model
↓
Set Chat Role
↓
Read AI Status
↓
Router uses Model
```

使用测试环境的真实 Provider 或 fake provider。

不能使用生产 API Key。

---

# 120. Config Integration Test

至少：

```text
读取 Config Schema
↓
修改支持热更新配置
↓
Apply
↓
Backend Effective Config
↓
Runtime value changed
```

再测试：

```text
修改 restart-required
↓
Apply
↓
effective Runtime unchanged
↓
restart pending visible
```

---

# 121. Failover Integration Test

必须模拟：

```text
Model A
↓
429
```

然后确认：

```text
Model Router
↓
Fallback B
```

WebUI 本身不执行 fallback。

WebUI 只配置：

```text
A
B
```

顺序。

---

# 122. API Contract Regression

W4 完成后：

```text
docs/WEBUI_API_CONTRACT.md
```

必须与：

```text
Backend
Frontend Type
Frontend implementation
```

一致。

---

# 123. W3 回归

必须保持：

```text
36 frontend tests
```

全部通过。

---

# 124. 全量测试

必须运行：

```bash
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe app
.venv\Scripts\python.exe -m pytest -q

cd webui
npm ci
npm run typecheck
npm run test
npm run build
```

具体命令以 package.json 实际 scripts 为准。

---

# 125. 不得修改 Runtime Core

W4 虽然会调用真实 AI Router，但：

禁止修改：

```text
Sandbox
RuntimeScheduler
Goal
ActionInstance
NeedSystem
Relationship
Commitment
Memory
ConversationRuntime
OneBot
ExternalInfluence
```

除非发现 API contract 确实缺失最小管理接口。

---

# 126. 不得新增第二套 AI Router

这是硬性要求。

前端绝不自己实现：

```text
retry
429 handling
cooldown
provider selection
fallback execution
```

---

# 127. 不得新增第二套 Config Store

前端绝不持久化：

```text
settings.json
ai-config.json
webui-config.json
```

作为真实 AI Config。

可以：

```text
localStorage
```

存 UI preference：

```text
theme
sidebar collapsed
last tab
```

但不能存真实 Runtime Config。

---

# 128. 不得新增第二套 Credential Store

API Key：

```text
后端 Secret storage
```

前端：

```text
masked state
```

仅此而已。

---

# 129. W4 Git Commit

建议：

```text
webui-v1/w4-ai-overview
webui-v1/w4-provider
webui-v1/w4-models
webui-v1/w4-config
webui-v1/w4-integration
```

也可以根据实际工作量合并为 2~4 个逻辑提交。

每个 commit：

```text
tests
typecheck
build
```

应尽量保持可回退。

---

# 130. Git 安全

禁止：

```text
force push
rebase main
filter-repo
BFG
history rewrite
```

---

# 131. Character Bible

继续遵守：

```text
真实 Character Bible
≠
public Git
```

W4 特别检查：

```text
Config API
AI Wizard
Frontend bundle
Expert Config
Debug response
```

不得把真实 Bible 内容暴露出来。

---

# 132. 浏览器级 E2E

W3 当前尚未安装 Playwright。

W4 建议：

> 如果项目环境允许，增加 Playwright。

至少做：

```text
登录
→ AI Setup
→ Provider
→ Credential
→ Model
→ Role
→ Config
→ Apply
```

真实浏览器 E2E。

如果安装 Playwright 会带来过重环境成本：

至少保留当前 jsdom application tests，并在最终报告明确说明没有 browser E2E。

但是：

> AI Setup 这种核心用户流程，优先建议真实浏览器测试。

---

# 133. 可访问性

AI 表单必须：

```text
label
description
error message
focus
keyboard navigation
```

所有 Secret input：

必须有：

```text
显示/隐藏
```

按钮。

---

# 134. 表单错误

错误必须直接显示在字段附近。

例如：

```text
Base URL

https://...

✕ URL 格式无效
```

而不是只显示：

```text
Error
```

---

# 135. 保存中的状态

按钮：

```text
保存
```

提交后：

```text
保存中...
```

防止重复点击。

---

# 136. 保存成功

Toast：

```text
✓ 已保存
```

如果：

```text
restart required
```

同时显示：

```text
⚠ 重启后生效
```

---

# 137. 数据刷新

Mutation 后必须：

```text
invalidate
```

相关 Store。

例如：

```text
Provider mutation
→ providers
→ models
→ AI overview
```

Model mutation：

```text
models
→ roles
→ failover
→ overview
```

Config mutation：

```text
effective
→ restart pending
→ runtime status
```

---

# 138. 不要滥用全局刷新

避免：

```text
save
→ window.location.reload()
```

必须通过 Store / API 更新。

只有 session/login 这类边界状态可以重新导航。

---

# 139. URL 与状态

切换：

```text
/ai/models
/ai/roles
/ai/failover
```

页面刷新仍然可以恢复。

不要所有状态只存在：

```text
component local state
```

---

# 140. 最终人工验收场景

## 场景 A：全新 AI 配置

从没有 Provider 的状态开始：

```text
Dashboard
↓
AI Setup
↓
添加 Provider
↓
API Key
↓
测试
↓
添加 Model
↓
模型测试
↓
设置 Chat Role
↓
AI Ready
```

全程只使用 WebUI。

不能碰：

```text
config.yaml
overrides.yaml
.env
```

---

## 场景 B：更换 API Key

```text
Provider
↓
更换 Key
↓
保存
↓
测试
```

确认：

```text
原 Key 不再使用
新 Key 生效
UI 仍只显示 masked
```

---

## 场景 C：模型故障转移

```text
A
B
C
```

调整顺序：

```text
C
A
B
```

保存。

确认：

```text
AI Router
```

看到新的真实顺序。

---

## 场景 D：429

模拟 A 返回：

```text
429
```

确认：

```text
A cooldown
B fallback
```

WebUI 显示真实状态。

---

## 场景 E：配置热更新

修改：

```text
一个已知 hot reload field
```

确认：

```text
不重启
Runtime 使用新值
```

---

## 场景 F：配置需要重启

修改：

```text
一个已知 restart-required field
```

确认：

```text
保存成功
Runtime 旧值继续运行
Pending Restart visible
```

---

## 场景 G：Unused

切换 Expert：

看到：

```text
DEFINED_BUT_UNUSED
```

并明确：

```text
修改不会影响当前行为
```

---

## 场景 H：Secret

打开：

```text
DevTools Network
Console
AI API responses
```

确认：

```text
API Key
```

没有明文。

---

# 141. 最终 W4 交付物

完成后必须具备：

```text
/ai
/ai/providers
/ai/models
/ai/roles
/ai/failover
/ai/usage
/ai/test
/ai/setup

/system/settings
/system/settings/restart-pending
/system/credentials
/system/settings/advanced
```

其中具体页面可以使用：

```text
tabs
drawer
modal
```

而不要求全部成为独立 route。

如果某个页面实际不需要独立 route：

优先使用更简单的 UX。

---

# 142. W4 完成定义

只有同时满足以下条件才能宣布：

```text
W4 = COMPLETE
```

```text
✅ AI Overview
✅ Provider CRUD UI
✅ Provider Test
✅ Credential UI
✅ API Key masked
✅ API Key replace
✅ Model CRUD UI
✅ Model Test
✅ Model enable/disable
✅ Model reorder
✅ Model Role UI
✅ Failover UI
✅ Usage UI
✅ Router Reset
✅ AI Setup Wizard
✅ Config Center
✅ Config search
✅ Basic / Advanced / Expert
✅ Config dynamic renderer
✅ Config source
✅ Effective state
✅ Hot reload state
✅ Restart required state
✅ Unused state
✅ Conditional state
✅ Apply Preview
✅ Restart Pending
✅ Expert YAML
✅ Danger confirmation
✅ Server truth after mutation
✅ No second Config Store
✅ No second AI Router
✅ No second Credential Store
✅ Secret never exposed
✅ W3 regression preserved
✅ frontend typecheck
✅ frontend tests
✅ frontend build
✅ pytest
✅ ruff
✅ mypy
✅ CI success
✅ working tree clean
```

---

# 143. W4 完成后的用户体验目标

普通用户第一次使用：

```text
打开 WebUI
↓
看到：
“AI 尚未配置”
↓
点击：
“开始配置”
↓
添加 Provider
↓
填写 API Key
↓
添加 Model
↓
测试
↓
设为聊天模型
↓
完成
```

之后：

```text
“模型到底有没有生效？”
```

可以直接看到：

```text
● 已生效
```

而不是去猜 YAML。

如果：

```text
“这个设置需要重启吗？”
```

直接显示：

```text
⚠ 重启后生效
```

如果：

```text
“这个配置到底有用吗？”
```

直接显示：

```text
⚠ 当前未使用
```

---

# 144. 最终报告

完成后输出：

```text
# WebUI v1.0 · W4 AI & Configuration Report

1. W3 baseline commit
2. W4 final commit
3. API contract discrepancy resolution
4. AI Overview
5. Provider UI
6. Credential UI
7. API Key handling
8. Model UI
9. Model Test
10. Model Roles
11. Failover
12. Usage
13. Setup Wizard
14. Config Center
15. Config Registry integration
16. Basic / Advanced / Expert
17. Effective Config
18. Restart Pending
19. Expert YAML
20. Secret security
21. Store architecture
22. Integration tests
23. Browser E2E
24. frontend tests
25. typecheck
26. build
27. pytest
28. ruff
29. mypy
30. CI
31. known limitations
32. final Git status
```

---

# 145. 停止条件

完成后立即停止：

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
NOT STARTED
```

不要开始：

```text
Memory
Social
Character
Sandbox
Tools
Agent
Sticker
Logs
```

---

# 最核心的一句话

W4 不是：

> “把几个配置表单做出来。”

而是：

> **把 CatooBot 最复杂、最容易让用户迷惑的 AI 与配置系统，转换成一个普通用户能够理解、能够修改、能够测试，并且能够明确知道“是否真的生效”的管理体验。**

最终应该形成：

```text
                  CatooBot WebUI v1.0

             ┌───────────────────────┐
             │       AI 与模型       │
             ├───────────────────────┤
             │ Provider              │
             │ Credential            │
             │ Model                 │
             │ Role                  │
             │ Failover              │
             │ Usage                 │
             │ Test                  │
             └───────────┬───────────┘
                         │
                    /api/v1
                         │
             ┌───────────▼───────────┐
             │ Existing AI Router    │
             │ Config System         │
             │ Credential Storage    │
             └───────────┬───────────┘
                         │
             ┌───────────▼───────────┐
             │      CatooBot Core    │
             └───────────────────────┘
```

**先读取 W1/W2 文档与当前 W3 前端代码，再开始 W4。完成 W4 后停止，不进入 W5。**