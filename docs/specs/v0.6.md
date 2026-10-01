# CatooBot v0.6 开发任务
# Tool Runtime + Contextual Tool Use
# 工具运行时 + 上下文工具调用 + WebUI 工具管理

---

# v0.6 §1 一、前置条件

CatooBot v0.5 已经基本完成。

当前系统已经具备：

- NapCatQQ
- OneBot 11
- WebSocket
- Event System
- Message / Segment
- AI Engine
- Model Router
- 429 Failover
- Persona
- Character Identity
- User Profile
- Group Profile
- Relationship
- Character State
- Activity
- Topic Thread
- Behavior Engine
- Initiative Engine
- Response Timing
- Long-term Memory
- Episodic Memory
- Semantic Memory
- Embedding
- Hybrid Retrieval
- Memory Consolidation
- Memory Compression
- Context Builder
- WebUI
- Runtime Management
- Model Management
- Persona Management
- Memory Management
- Session Management
- Logs

现在进入：

# CatooBot v0.6

核心目标：

> 让 CatooBot 在聊天过程中，能够根据当前上下文自主判断是否需要使用某个工具，并在工具完成后自然地继续聊天。

---

# v0.6 §2 二、非常重要的产品定位

CatooBot 仍然不是传统意义上的“功能型 Bot”。

QQ 用户不应该看到：

```text id="qxjnu8"
/weather
/search
/news
/music
```

也不应该看到：

```text id="m3m5oe"
当前可用工具：
1. 天气
2. 搜索
3. 计算器
```

更不应该通过 QQ 指令管理工具。

所有工具：

```text id="59fwxw"
注册
启用
禁用
配置
权限
测试
日志
执行记录
```

全部由：

```text id="1c8fxp"
WebUI
```

管理。

---

# v0.6 §3 三、用户看到的体验

用户说：

```text id="nfw3ah"
明天新加坡会不会下雨？
```

系统内部：

```text id="5ey9kz"
Message
 ↓
Context
 ↓
Tool Decision
 ↓
Weather Tool
 ↓
Tool Result
 ↓
AI
 ↓
自然回复
```

用户只能看到类似：

```text id="tdx3p4"
看了一下，明天大概率会有阵雨，出门最好带把伞。
```

而不是：

```text id="p65r6c"
正在调用 weather.search
tool result:
...
```

除非 WebUI 的 Debug 模式中查看。

---

# v0.6 §4 四、v0.6 不做完整 Agent

本版本明确：

**先做 Tool Runtime，不做完整自主 Agent。**

暂时不实现：

- 无限循环 Agent
- 自动递归规划
- 长时间自主任务
- 多 Agent
- Agent-to-Agent
- 自动修改自身工具
- 自动安装工具
- 自主修改系统配置

v0.6 先实现：

```text id="lgm8mi"
判断是否需要 Tool
↓
选择 Tool
↓
生成参数
↓
执行
↓
读取结果
↓
继续回答
```

最多允许有限次数的 Tool Call。

---

# v0.6 §5 五、整体架构

```text id="o7ky3t"
                         QQ
                          │
                          ▼
                     NapCat/OneBot
                          │
                          ▼
                   CatooBot Core
                          │
                          ▼
                 Character Runtime
                          │
             ┌────────────┼─────────────┐
             ▼            ▼             ▼
          Persona      Memory       Behavior
             │            │             │
             └────────────┼─────────────┘
                          ▼
                    Context Builder
                          │
                          ▼
                     AI Engine
                          │
                   Tool Decision
                          │
             ┌────────────┴────────────┐
             ▼                         ▼
         No Tool                  Tool Required
             │                         │
             │                         ▼
             │                   Tool Router
             │                         │
             │                   Tool Registry
             │                         │
             │                   Tool Runtime
             │                         │
             │              ┌──────────┼──────────┐
             │              ▼          ▼          ▼
             │           Weather    Search    Calculator
             │
             └───────────────┬─────────────────────┘
                             ▼
                        Tool Result
                             │
                             ▼
                          AI Engine
                             │
                             ▼
                      Response Planner
                             │
                             ▼
                             QQ
```

---

# v0.6 §6 六、核心模块

建议增加：

```text id="fvh1sp"
app/
├─ tools/
│  ├─ __init__.py
│  ├─ models.py
│  ├─ registry.py
│  ├─ router.py
│  ├─ executor.py
│  ├─ policy.py
│  ├─ context.py
│  ├─ errors.py
│  ├─ result.py
│  ├─ schema.py
│  └─ builtins/
│     ├─ __init__.py
│     ├─ calculator.py
│     ├─ time.py
│     └─ ...
```

如果现有项目已有类似模块，应优先复用。

不要机械重构。

---

# v0.6 §7 七、Tool Interface

建立统一 Tool 抽象：

```python id="j9x3gr"
class Tool(ABC):

    name: str
    description: str
    version: str

    async def execute(
        self,
        arguments: dict,
        context: ToolContext
    ) -> ToolResult:
        ...
```

同时提供：

```text id="zydfes"
schema
permissions
timeout
enabled
risk_level
```

---

# v0.6 §8 八、Tool Metadata

每个 Tool 至少定义：

```text id="gbw6xa"
name
display_name
description
version
category
input_schema
output_schema
enabled
timeout
risk_level
```

例如：

```text id="1cfz28"
name:
weather

display_name:
Weather

description:
查询指定地点的实时天气和天气预报。

category:
information

risk_level:
low
```

---

# v0.6 §9 九、Tool Registry

增加：

```text id="p1m4p8"
ToolRegistry
```

功能：

```python id="b2yp5k"
register()
unregister()
get()
list()
enable()
disable()
```

Tool Registry 不负责执行。

只负责：

> “系统目前有哪些工具。”

---

# v0.6 §10 十、Tool Router

增加：

```text id="g5vq2j"
ToolRouter
```

负责：

```text id="e95jls"
当前上下文
↓
判断有哪些 Tool 可能相关
↓
过滤 disabled Tool
↓
过滤权限
↓
过滤风险
↓
交给 AI Tool Decision
```

---

# v0.6 §11 十一、Tool Decision

不要简单：

```text id="hr6z8d"
if "天气" in message:
    weather()
```

这种关键词硬编码只能作为低级 fallback。

主要方式：

```text id="h8g9bx"
AI Engine
↓
Tool Schema
↓
模型判断
```

也就是说，模型能够看到：

```text id="q4rt54"
available tools
```

然后产生：

```json id="6lupxd"
{
  "tool": "weather",
  "arguments": {
    "location": "Singapore"
  }
}
```

---

# v0.6 §12 十二、不要把所有 Tool 都发给模型

非常重要。

如果以后有：

```text id="zqs7i0"
50 个 Tool
```

不要一次全部注入 Prompt。

应该：

```text id="xso7fu"
用户消息
↓
Tool Candidate Retrieval
↓
Top N relevant tools
↓
AI
```

工具自身也要有：

```text id="l8c9ew"
description
tags
category
keywords
```

可以使用轻量级：

```text id="47wq4i"
keyword
semantic similarity
category
```

筛选。

---

# v0.6 §13 十三、Tool Capability Retrieval

以后工具可能非常多。

因此增加：

```text id="4gl0g5"
ToolCapabilityIndex
```

类似 Memory Retrieval：

```text id="leqb0x"
Current Query
↓
Tool Search
↓
Relevant Tools
↓
AI
```

先做：

```text id="xq7x40"
keyword + metadata
```

未来可以支持：

```text id="z36jdi"
Embedding
```

---

# v0.6 §14 十四、Tool Schema

所有 Tool 必须提供机器可解析的参数 Schema。

例如：

```json id="dr4o5p"
{
  "type": "object",
  "properties": {
    "location": {
      "type": "string"
    }
  },
  "required": ["location"]
}
```

这样 AI 生成参数后可以严格验证。

---

# v0.6 §15 十五、参数验证

Tool 执行之前：

```text id="fvmiyt"
AI Tool Call
↓
Schema Validation
↓
合法？
├─ 是 → Execute
└─ 否 → Tool Error
```

不能让：

```text id="q9yidv"
错误参数
```

直接进入 Tool。

---

# v0.6 §16 十六、Tool Result

统一：

```python id="0b2x4k"
ToolResult(
    success=True,
    data=...,
    summary=...,
    raw=...,
    metadata=...
)
```

至少：

```text id="qg0qf6"
success
data
summary
error
execution_time
tool_name
```

---

# v0.6 §17 十七、Tool Result 必须可控

不要直接把第三方 API 的完整 JSON 塞进 LLM。

应该：

```text id="mmxftz"
Raw API Response
↓
Tool
↓
Structured Result
↓
LLM
```

例如天气 API 返回：

```json id="g9v3gr"
{
  "many": "many fields..."
}
```

Tool 应该转换成：

```text id="wa8tez"
地点：新加坡
天气：阵雨
温度：29°C
湿度：81%
```

---

# v0.6 §18 十八、Tool 错误标准化

建立：

```text id="fxg5s0"
ToolError
├─ InvalidArgumentsError
├─ AuthenticationError
├─ PermissionDeniedError
├─ TimeoutError
├─ RateLimitError
├─ ExternalServiceError
├─ NotFoundError
└─ UnknownToolError
```

Tool Runtime 将所有异常标准化。

---

# v0.6 §19 十九、Tool Timeout

每个 Tool 都必须有 timeout。

例如：

```yaml id="4z6dzf"
tools:
  default_timeout: 10
```

单个 Tool 可以覆盖。

例如：

```text id="gdy1ah"
weather = 5s
search = 15s
```

---

# v0.6 §20 二十、Tool Retry

原则：

不能无限重试。

默认：

```text id="ti1fo4"
max_retry = 1
```

针对：

```text id="xlgdrr"
timeout
部分 5xx
暂时网络错误
```

允许一次 retry。

对于：

```text id="pge6s4"
401
403
invalid args
```

不要 retry。

---

# v0.6 §21 二十一、Tool Execution Policy

增加：

```text id="cwyl7u"
ToolPolicy
```

每次执行：

```text id="zjch84"
Tool
↓
Policy
↓
Allowed?
```

Policy 至少考虑：

```text id="v3d5td"
enabled
user
group
risk_level
time
rate_limit
```

---

# v0.6 §22 二十二、Tool Risk Level

至少：

```text id="3vkdxo"
low
medium
high
```

例如：

```text id="6miyf7"
calculator = low
weather = low
web_search = low
send_message = medium
delete_file = high
execute_command = high
```

v0.6 暂时：

**只实现 low-risk 工具。**

---

# v0.6 §23 二十三、严格禁止任意命令执行

这一版：

**不要提供：**

```text id="jncfq8"
shell
cmd
powershell
python_exec
arbitrary_code
```

CatooBot 不能根据模型输出执行任意系统命令。

这是硬性安全边界。

---

# v0.6 §24 二十四、第一批内置工具

先实现以下：

## 1. Time Tool

功能：

```text id="7ccvgq"
当前时间
日期
星期
时区
```

例如：

```text id="1wl4d0"
用户：
现在几点？
```

模型可以调用：

```text id="5f1z6y"
time
```

---

## 2. Calculator Tool

支持：

```text id="h3g3s8"
基本算术
百分比
单位换算
```

不要让 LLM 自己做复杂数学。

例如：

```text id="3f1dkx"
用户：
12893 * 473 是多少？
```

调用 Calculator。

---

## 3. Weather Tool

提供接口抽象：

```text id="u8yo0a"
WeatherProvider
```

Tool：

```text id="i4k9fj"
weather
```

输入：

```text id="jj5s2l"
location
```

输出：

```text id="60d7l9"
current
forecast
```

实际使用哪个天气 API：

**通过 WebUI 配置。**

不要硬编码服务商。

---

## 4. Web Search Tool

设计：

```text id="f4k0p2"
SearchProvider
```

Tool：

```text id="s8z81b"
web_search
```

输入：

```text id="q5j8oc"
query
```

输出：

```text id="o6jg4x"
title
url
snippet
source
published_at
```

---

# v0.6 §25 二十五、搜索结果处理

Search Tool 返回的内容：

不能直接全部塞给 AI。

先做：

```text id="1ahj1d"
dedupe
trim
source
title
snippet
```

限制：

```text id="80rtkw"
最多 N 个结果
```

配置：

```yaml id="2s4w71"
tools:
  web_search:
    max_results: 5
```

---

# v0.6 §26 二十六、搜索结果可信度

Tool 结果必须保留：

```text id="x5qc2f"
source
url
timestamp
```

如果 AI 无法从结果得到答案：

不要让模型自行编造。

应该：

```text id="js0zhy"
Tool Result insufficient
```

然后自然回答：

```text id="c1jo95"
我没找到足够可靠的信息……
```

---

# v0.6 §27 二十七、工具使用与“拟人化”结合

Tool 调用不要破坏角色。

例如：

不要：

```text id="rlqlzn"
[调用 weather]
[结果]
[处理中]
```

QQ 用户不可见。

最终：

```text id="h8a1eg"
看了一下，明天应该会有雨，出门带把伞吧。
```

---

# v0.6 §28 二十八、Tool 调用过程不可见

默认：

```text id="z40z5q"
tool call
tool result
```

全部只进入：

```text id="nqvclm"
内部日志
```

WebUI 可以查看。

---

# v0.6 §29 二十九、Tool Result 不应成为 Memory

非常重要。

比如：

```text id="u08yja"
天气：
29°C
```

不能自动记成：

```text id="m13xk6"
用户喜欢 29°C
```

Tool Result 默认：

```text id="mm7br0"
ephemeral
```

只有用户明确表达长期信息：

```text id="lfb3c7"
我很喜欢这种天气
```

才由 Memory Engine 决定是否保存。

---

# v0.6 §30 三十、Tool Context

定义：

```python id="6jyowf"
ToolContext
```

至少包括：

```text id="75u2rx"
user
group
session
character
locale
timezone
current_datetime
```

Tool 可以读取这些信息。

例如：

Time Tool 自动知道：

```text id="3q9vzm"
用户默认时区
```

---

# v0.6 §31 三十一、Tool 不允许直接访问数据库

Tool 只通过：

```text id="0y1m8v"
Service
Repository
```

访问数据。

不要：

```text id="7s8b83"
tool.py
↓
sqlite.connect(...)
```

---

# v0.6 §32 三十二、工具权限

虽然 v0.6 只做低风险 Tool，但仍然需要：

```text id="j1rj4v"
ToolPermissionManager
```

支持：

```text id="wrpm4q"
global enable
user allow
group allow
```

例如：

```text id="o8j4h7"
Web Search
Group A = enabled
Group B = disabled
```

---

# v0.6 §33 三十三、工具限流

每个 Tool 支持：

```text id="v3ezr9"
per_user
per_group
global
```

例如：

```yaml id="qqqtd5"
tools:
  web_search:
    rate_limit:
      per_user_per_minute: 10
```

防止异常调用。

---

# v0.6 §34 三十四、Tool Call Budget

每条消息：

默认最多：

```text id="9xoyxm"
3 次 Tool Call
```

例如：

```text id="4ycdjb"
用户问题
↓
搜索
↓
天气
↓
计算
↓
最终回答
```

超过：

```text id="17yr5b"
ToolBudgetExceeded
```

不能无限调用。

---

# v0.6 §35 三十五、Tool Loop Detection

防止：

```text id="kk8v2a"
search
↓
search
↓
search
↓
search
```

检测：

```text id="0q4y4p"
same tool
same arguments
```

重复超过阈值：

```text id="2fz4fn"
阻止继续执行
```

---

# v0.6 §36 三十六、Tool Call Trace

每次 Tool 调用记录：

```text id="0v63vq"
trace_id
session_id
user_id
group_id
tool_name
arguments_hash
started_at
finished_at
duration
status
error_type
```

不要默认保存完整敏感参数。

---

# v0.6 §37 三十七、WebUI Tool Registry

增加页面：

```text id="4l4y6n"
/tools
```

显示：

```text id="0h7bc2"
Tool
Version
Description
Category
Enabled
Risk Level
Timeout
Rate Limit
```

---

# v0.6 §38 三十八、WebUI Tool Detail

点击 Tool：

```text id="7trhxf"
/tools/{name}
```

查看：

```text id="7oy5ya"
Schema
Configuration
Permissions
Rate Limit
Recent Executions
Errors
Latency
```

---

# v0.6 §39 三十九、WebUI 启用/禁用

支持：

```text id="1g7f0p"
Enable
Disable
```

实时生效。

无需重启。

---

# v0.6 §40 四十、WebUI Tool Configuration

例如 Weather：

```text id="c9dtna"
Provider
API Endpoint
API Key
Default Location
```

敏感信息：

```text id="2j6wpr"
加密或安全存储
```

不要在页面显示完整 API Key。

---

# v0.6 §41 四十一、WebUI Tool Test

管理员进入：

```text id="q8f3l5"
Weather
```

输入：

```text id="2f1v3d"
Singapore
```

点击：

```text id="y72p9r"
Test
```

显示：

```text id="w7s4m3"
request
response
latency
status
```

但：

**不会发送 QQ 消息。**

---

# v0.6 §42 四十二、WebUI Tool Execution Logs

页面：

```text id="bs9sj7"
/tools/executions
```

支持：

```text id="0t2mwy"
时间
Tool
User
Group
Status
Duration
```

点击可以查看：

```text id="f97z45"
Execution Detail
```

敏感参数必须脱敏。

---

# v0.6 §43 四十三、WebUI Tool Health

显示：

```text id="dggo7f"
Success Rate
Error Rate
Average Latency
P95 Latency
Rate Limit Count
Timeout Count
```

---

# v0.6 §44 四十四、Tool Provider 抽象

Provider 与 Tool 分开。

例如：

```text id="egrwji"
Weather Tool
      │
      ▼
WeatherProvider
      │
 ┌────┴────┐
 ▼         ▼
Provider A Provider B
```

同一个 Tool 可以更换底层供应商。

---

# v0.6 §45 四十五、Search Provider

同理：

```text id="gk6fob"
Search Tool
     │
     ▼
SearchProvider
```

支持：

```text id="3emx1i"
provider_a
provider_b
```

---

# v0.6 §46 四十六、Tool Routing 与 Model Routing 分开

不要混淆：

```text id="h7s2cq"
Model Router
=
选择 AI 模型

Tool Router
=
选择工具
```

二者独立。

---

# v0.6 §47 四十七、AI Tool Calling Adapter

不要把 OpenAI/SiliconFlow 某个平台的 Tool Calling 格式写死在 Core。

建立：

```text id="5pf3c5"
ToolCallAdapter
```

用于：

```text id="m8i6c6"
Provider-specific tool calling
```

如果某个模型支持标准 function calling：

直接转换。

如果某个模型不支持：

可以 fallback 为：

```text id="v7e8ik"
structured JSON decision
```

---

# v0.6 §48 四十八、Tool Call 标准格式

内部统一：

```python id="k0z29n"
ToolCall(
    id,
    name,
    arguments
)
```

不管底层模型返回：

```text id="j0nryy"
OpenAI
SiliconFlow
Gemini
其他
```

都转换成统一结构。

---

# v0.6 §49 四十九、Tool Call Result 标准格式

统一：

```python id="27uj05"
ToolCallResult(
    call_id,
    tool_name,
    success,
    result,
    error
)
```

---

# v0.6 §50 五十、单轮 Tool Calling

v0.6 第一阶段先实现：

```text id="5vtdw7"
User Message
↓
AI
↓
Tool Call
↓
Tool Result
↓
AI Final Response
```

即：

**最多一轮 Tool Call Loop。**

---

# v0.6 §51 五十一、多轮 Tool Calling

第二阶段允许：

```text id="kc1p4z"
AI
↓
Tool A
↓
Tool B
↓
Tool C
↓
Final
```

最多由：

```yaml id="avbpj3"
tools:
  max_calls_per_turn: 3
```

控制。

---

# v0.6 §52 五十二、不要允许无限 Tool Loop

硬上限：

```text id="s1ih8h"
max_calls_per_turn
```

以及：

```text id="j8ks6l"
max_execution_time
```

任何一个达到上限：

```text id="7j9d7j"
停止。
```

---

# v0.6 §53 五十三、Tool 使用决策必须结合 Character

角色不会因为拥有一个工具，就每次都使用。

例如：

```text id="0c7j1f"
用户：
天气不错。

```

不应该自动调用：

```text id="jz0q77"
weather
```

而：

```text id="pe3p7x"
用户：
明天会不会下雨？
```

才应该考虑。

---

# v0.6 §54 五十四、Tool Use 与 Personality

工具只是能力。

角色说话方式仍然由：

```text id="ga6t7s"
Persona
+
Relationship
+
Behavior
+
Context
```

决定。

Tool 不应该改变人格。

---

# v0.6 §55 五十五、Tool Use 与 Memory

调用 Tool 前可以使用：

```text id="hd7h0x"
Memory
```

例如：

用户以前告诉角色：

```text id="8oyc4x"
默认所在地是新加坡。
```

用户：

```text id="8y9tui"
明天会下雨吗？
```

Tool 参数可以利用 Memory：

```text id="fuyd4e"
location = Singapore
```

但必须：

> **只有高置信度、相关且允许用于该场景的 Memory 才能自动作为工具参数。**

如果地点不明确：

自然询问。

---

# v0.6 §56 五十六、Tool 参数来源必须可追踪

每个参数内部记录：

```text id="sogb87"
explicit_user
memory
context
model_inferred
default
```

例如：

```text id="4lwmwm"
location:
Singapore
source:
memory
```

这样 WebUI 可以 Debug。

---

# v0.6 §57 五十七、不能让 Memory 自动变成高风险 Tool 参数

即使未来有：

```text id="f744t9"
send_message
```

也不能因为 Memory：

```text id="用户认识某人"
```

就自动执行外部动作。

当前 v0.6 不实现高风险 Tool。

---

# v0.6 §58 五十八、Tool Response Processing

Tool 结果进入：

```text id="z6f7y8"
ToolResultProcessor
```

负责：

```text id="3j1v1g"
sanitize
truncate
normalize
detect_error
```

防止第三方 API 返回：

```text id="xq5d4u"
prompt injection
```

---

# v0.6 §59 五十九、外部内容不能成为系统指令

特别是 Search Tool。

例如网页写：

```text id="c7in8j"
Ignore previous instructions...
```

这只是：

```text id="90v3mt"
网页内容
```

绝不能覆盖：

```text id="zrrj9q"
System Prompt
Persona
Tool Policy
```

---

# v0.6 §60 六十、Search Tool 的网页内容隔离

必须明确：

```text id="v7n38x"
External Content
```

只能作为：

```text id="j0a4e6"
untrusted reference data
```

进入 Context。

不要直接拼到 System Prompt。

---

# v0.6 §61 六十一、工具结果不要自动写入长期记忆

Search：

```text id="7gm78z"
“某公司今天发生了什么？”
```

结果属于：

```text id="3evd78"
ephemeral
```

除非用户后续明确表达：

```text id="u14e9k"
我以后还想关注这个公司。
```

才由 Memory Engine 决定。

---

# v0.6 §62 六十二、工具与主动行为

v0.6 可以让 Initiative Engine 使用 Tool。

例如：

```text id="anq3ld"
角色主动聊天候选
+
今天是用户生日
```

如果 WebUI 有：

```text id="f09e1m"
Calendar / Date Tool
```

可以辅助。

但默认不要做大量主动 Tool 调用。

主动行为仍然需要：

```text id="39yr7n"
cooldown
daily budget
initiative gate
```

---

# v0.6 §63 六十三、工具与群聊

Tool 使用必须考虑：

```text id="q0zudl"
当前群
当前用户
group permission
```

例如：

```text id="d8yslw"
Web Search
```

群 A：

```text id="zi2m0z"
允许
```

群 B：

```text id="ztmggc"
禁止
```

---

# v0.6 §64 六十四、工具调用的消息时序

如果 Tool 调用很慢：

```text id="1ye4c6"
用户
↓
Tool
↓
等待 5 秒
↓
AI
```

不要：

```text id="02fcxr"
沉默 5 秒后突然回复
```

Response Planner 应该允许：

```text id="pii3ck"
延迟管理
```

最终仍然自然。

但默认：

**不要发送“正在搜索……”这样的机器状态文本。**

---

# v0.6 §65 六十五、工具失败时的人格化处理

工具失败：

内部：

```text id="pu2owk"
WeatherProvider Timeout
```

QQ 不应该看到：

```text id="5x5h5e"
HTTP 504
```

而应该由 AI 根据角色自然表达：

```text id="5du2kg"
我刚刚查了一下，不过这次没拿到结果……
```

具体语气由 Persona 决定。

---

# v0.6 §66 六十六、绝对不能编造 Tool 结果

这是核心要求。

如果：

```text id="3wq4yl"
Weather Tool
```

失败。

模型不能自己说：

```text id="r5fg9j"
明天 28°C，有雨。
```

必须知道：

```text id="5uqbxm"
Tool unavailable
```

然后诚实表达。

---

# v0.6 §67 六十七、Tool Result Confidence

Tool Result 可以带：

```text id="pdkp7x"
confidence
source
timestamp
```

AI 根据结果质量决定表达。

---

# v0.6 §68 六十八、Tool Cache

对于：

```text id="4y5r3w"
时间
天气
搜索
```

可以缓存。

例如：

```yaml id="9rk7a3"
tools:
  weather:
    cache_ttl_seconds: 600
```

但：

缓存时间必须由 Tool 决定。

---

# v0.6 §69 六十九、Tool Cache Key

例如 Weather：

```text id="jlkqcz"
provider
location
forecast_range
```

形成：

```text id="7f81ea"
cache_key
```

避免：

```text id="6xmqcw"
A 用户调用天气
B 用户误拿到不同地点
```

---

# v0.6 §70 七十、工具执行并发

如果：

```text id="6k6doj"
Weather
+
Time
```

彼此独立：

允许并发。

但：

```text id="b2k3pz"
Tool B
依赖 Tool A
```

必须串行。

v0.6 可以简单：

```text id="u6j1vx"
parallel independent calls
```

但要有最大并发数。

---

# v0.6 §71 七十一、工具执行并发限制

例如：

```yaml id="v43k4z"
tools:
  max_concurrent: 4
```

---

# v0.6 §72 七十二、Tool Runtime 生命周期

```text id="ffeqw2"
Startup
↓
Load Registry
↓
Load Tool Config
↓
Validate Providers
↓
Ready
↓
Runtime
↓
Shutdown
```

单个 Tool Provider 出问题：

```text id="m3k5sw"
不会影响整个 CatooBot。
```

---

# v0.6 §73 七十三、Tool Hot Reload

WebUI 修改：

```text id="n3x9c8"
Tool enabled
Provider
Timeout
Rate limit
```

应尽量：

```text id="6e2w8s"
Hot Reload
```

不需要重启 CatooBot。

---

# v0.6 §74 七十四、WebUI Tools Dashboard

页面：

```text id="c9t5q6"
/tools
```

显示：

```text id="3f5p7m"
Total Tools
Enabled
Disabled
Healthy
Unhealthy
Calls Today
Errors Today
Average Latency
```

---

# v0.6 §75 七十五、WebUI Tool Decision Debug

建议：

```text id="w7fk8t"
/tools/decision-debug
```

输入：

```text id="p9v1qs"
当前用户消息
```

输出：

```text id="m1q4rd"
Candidate Tools
   ↓
weather       score=0.91
search        score=0.53
calculator    score=0.12

Selected:
weather
```

但是：

**这是后台 Debug，不是 QQ 输出。**

---

# v0.6 §76 七十六、WebUI Tool Policy

管理员可以控制：

```text id="x5l6ot"
Global
User
Group
```

例如：

```text id="77x9g6"
web_search
User A = enabled
User B = disabled
```

---

# v0.6 §77 七十七、WebUI Tool Permissions

显示：

```text id="8t15cw"
谁可以使用这个 Tool
```

支持：

```text id="jkp7ou"
all
allowlist
denylist
```

---

# v0.6 §78 七十八、WebUI Tool Rate Limit

支持：

```text id="ws4x9v"
per user
per group
global
```

---

# v0.6 §79 七十九、WebUI Tool Metrics

统计：

```text id="v3rjp8"
calls
success
failure
timeout
rate_limit
avg latency
p50
p95
```

---

# v0.6 §80 八十、数据库

如果现有项目使用 SQLite：

增加：

```text id="4h3nrt"
tools
tool_configs
tool_executions
tool_cache
tool_permissions
```

具体 schema 以当前项目为准。

必须使用 Migration。

---

# v0.6 §81 八十一、敏感凭据

API Key：

绝对不能：

```text id="9u3tyh"
明文数据库
普通日志
QQ 回复
Tool Result
```

应继续使用现有的：

```text id="r4b3gv"
credential / env / secret
```

机制。

如果 v0.5 没有完善 Credential Store：

这次补上一个最小版本。

---

# v0.6 §82 八十二、Credential Manager

增加：

```text id="sn3v1s"
CredentialManager
```

负责：

```text id="9jv6d1"
get_secret()
set_secret()
delete_secret()
```

WebUI 中显示：

```text id="1y4h4v"
********
```

而不是完整 Key。

---

# v0.6 §83 八十三、测试

新增：

```text id="p5h3cl"
tests/
├─ test_tool_models.py
├─ test_tool_registry.py
├─ test_tool_router.py
├─ test_tool_executor.py
├─ test_tool_policy.py
├─ test_tool_schema.py
├─ test_tool_budget.py
├─ test_tool_loop.py
├─ test_tool_cache.py
├─ test_tool_permissions.py
└─ test_builtin_tools.py
```

---

# v0.6 §84 八十四、Tool Registry 测试

测试：

```text id="55w4lc"
register
unregister
get
duplicate name
version
enable
disable
```

---

# v0.6 §85 八十五、Schema 测试

模拟：

```text id="l4lkgp"
合法参数
缺失参数
多余参数
错误类型
null
```

---

# v0.6 §86 八十六、Policy 测试

测试：

```text id="ob5mca"
global disable
user deny
group deny
rate limit
risk level
```

---

# v0.6 §87 八十七、Budget 测试

例如：

```text id="qff4km"
max_calls_per_turn = 3
```

AI 请求：

```text id="ng9brm"
4 calls
```

第 4 次必须被阻止。

---

# v0.6 §88 八十八、Loop Detection 测试

重复：

```text id="6iv2c8"
weather Singapore
weather Singapore
weather Singapore
```

必须在达到阈值时停止。

---

# v0.6 §89 八十九、Timeout 测试

Mock：

```text id="7jhdp2"
Tool timeout
```

验证：

```text id="4f1e7a"
不会阻塞整个 Runtime
```

---

# v0.6 §90 九十、Tool Failure 测试

模拟：

```text id="1ke6cc"
401
403
429
500
Timeout
Invalid arguments
```

验证统一异常。

---

# v0.6 §91 九十一、AI Tool Decision 测试

至少模拟：

```text id="s57grd"
无 Tool
单 Tool
多个 Tool
错误 Tool 名称
错误参数
```

---

# v0.6 §92 九十二、集成测试：天气

输入：

```text id="gyn3hm"
明天新加坡会下雨吗？
```

流程：

```text id="q0yhlw"
AI
↓
weather
↓
result
↓
AI
↓
final response
```

---

# v0.6 §93 九十三、集成测试：普通聊天不调用 Tool

输入：

```text id="r66mc0"
今天心情不错。
```

不应该：

```text id="y15d5j"
调用任何 Tool
```

---

# v0.6 §94 九十四、集成测试：计算

输入：

```text id="p5m6y8"
123456 * 7890 是多少？
```

应该优先调用：

```text id="1z5jqh"
calculator
```

而不是让 LLM 自己估算。

---

# v0.6 §95 九十五、集成测试：搜索

输入：

```text id="7q85vi"
帮我查一下最近有什么关于 XXX 的新闻。
```

如果 Search Tool 可用：

```text id="0n6e3n"
调用 Search
```

如果不可用：

```text id="v6r4dz"
自然表达无法查询，而不是编造新闻。
```

---

# v0.6 §96 九十六、集成测试：工具不可用

WebUI：

```text id="efpq3o"
Disable weather
```

用户：

```text id="pm7ykh"
明天会下雨吗？
```

CatooBot：

```text id="kh3r8o"
不能调用天气工具
```

然后自然回答：

不要假装知道实时天气。

---

# v0.6 §97 九十七、集成测试：Memory + Tool

Memory：

```text id="eh32dk"
用户当前所在地是新加坡
```

用户：

```text id="g1y1v0"
明天会下雨吗？
```

Tool：

```text id="8evrso"
weather(Singapore)
```

参数来源：

```text id="tdpsxa"
memory
```

必须在内部 Trace 中可见。

---

# v0.6 §98 九十八、集成测试：Tool + Persona

Weather Tool 返回：

```text id="j7cr3g"
31°C
```

最终表达方式：

必须仍然符合 Persona。

不能变成：

```text id="3p1v97"
API Response:
Temperature = 31
```

---

# v0.6 §99 九十九、集成测试：主动行为 + Tool

如果 v0.4 Initiative 决定：

```text id="au4dqm"
主动聊天
```

未来允许：

```text id="o6xqjf"
Tool
```

辅助生成话题。

但：

默认不允许频繁自动调用外部工具。

主动行为必须：

```text id="y3i0i6"
通过 Tool Budget
+
Initiative Budget
```

---

# v0.6 §100 一百、Tool 与 WebUI 完全分离

WebUI：

```text id="0p0hg2"
工具管理
```

QQ：

```text id="b7l53n"
只聊天
```

绝对不要新增：

```text id="j0s5wm"
/tools
/tool
/enable
/disable
```

---

# v0.6 §101 一百零一、Tool Audit Log

每一次执行必须内部记录：

```text id="xaq5o8"
who
when
where
tool
reason
arguments_source
status
latency
result_summary
```

但：

**不得把内部审计信息暴露给普通 QQ 用户。**

---

# v0.6 §102 一百零二、工具“为什么被使用”

在内部：

```text id="jkrp6b"
reason:
user_request
context
memory
initiative
```

例如：

```text id="98io3g"
weather
reason = explicit_user_question
```

或者：

```text id="kql4dd"
search
reason = user_request_requires_current_information
```

这便于 WebUI 调试。

---

# v0.6 §103 一百零三、工具“为什么没有被使用”

WebUI Debug 可以显示：

```text id="hkrpn4"
Tool candidate:
weather
rejected:
no relevant intent
```

但不是给 QQ 用户看的。

---

# v0.6 §104 一百零四、Tool Result 不直接决定答案

工具只提供：

```text id="b2c1lq"
事实/数据
```

最终表达仍由：

```text id="7t4jsg"
Character + AI
```

决定。

例如天气 Tool 说：

```text id="4twj0f"
Rain: 70%
```

角色可以自然说：

```text id="ewqcc4"
明天有比较明显的降雨概率，出门带把伞比较稳。
```

---

# v0.6 §105 一百零五、External Data 标记

所有外部 Tool Result 都必须有：

```text id="crq0wk"
source_type = external
```

进入 Context 后：

```text id="q53d0p"
仅作为参考数据
```

不能改变：

```text id="9df3b3"
Persona
System
Security Rules
Tool Policy
```

---

# v0.6 §106 一百零六、工具安全边界

v0.6 暂时禁止以下工具：

```text id="s0j7st"
shell
powershell
python
file_delete
file_write arbitrary
registry
network socket raw
account login
message_send arbitrary
```

只允许：

```text id="f00q84"
low-risk
read-only
```

---

# v0.6 §107 一百零七、允许的第一批工具

最少：

```text id="0f1h8s"
Time
Calculator
Weather
Web Search
```

如果实际项目条件允许，再增加：

```text id="d3u6g0"
Unit Converter
```

但不要一次加入十几个。

---

# v0.6 §108 一百零八、Tool Provider 可替换

例如：

```text id="vq4zpc"
Weather
├─ Provider A
└─ Provider B
```

Provider A 失败：

可以 fallback Provider B。

但 Tool 对上层仍然只是：

```text id="sh9gg4"
weather
```

---

# v0.6 §109 一百零九、Provider Failover 与 Model Failover 分离

例如：

```text id="w5s7v6"
Model Router
↓
Chat Model A → B → C
```

这是 AI 模型级别。

而：

```text id="5hw2e6"
Weather Provider A → B
```

这是 Tool Provider 级别。

二者互不影响。

---

# v0.6 §110 一百一十、Tool Metrics

至少统计：

```text id="rkl4mv"
tool_calls_total
tool_success_total
tool_failure_total
tool_timeout_total
tool_rate_limit_total
tool_avg_latency
tool_p95_latency
```

WebUI 显示。

---

# v0.6 §111 一百一十一、缓存策略

允许：

```text id="pltrt4"
Time:
短 TTL

Weather:
中 TTL

Search:
短 TTL
```

具体：

```text id="k6v5of"
由 Tool 自己声明
```

---

# v0.6 §112 一百一十二、Tool Documentation

每个 Tool 必须有：

```text id="ie4tr4"
name
description
when_to_use
when_not_to_use
input_schema
output_schema
limitations
```

这样：

```text id="1z01hq"
AI
+
WebUI
+
Developer
```

都能理解工具。

---

# v0.6 §113 一百一十三、开发顺序

严格：

```text id="kv5xbi"
1. 阅读 v0.5 代码
2. 分析现有 AI Engine
3. 分析 Context Builder
4. Tool Model
5. Tool Registry
6. Tool Schema
7. Tool Executor
8. Tool Policy
9. Tool Router
10. Tool Result
11. Tool Call Adapter
12. Time Tool
13. Calculator Tool
14. Weather Provider + Tool
15. Search Provider + Tool
16. Tool Budget
17. Loop Detection
18. Tool Cache
19. Tool Permission
20. WebUI Tool Management
21. WebUI Tool Debug
22. Tool Metrics
23. Tests
24. Integration Tests
25. QQ 实测
26. Regression
```

---

# v0.6 §114 一百一十四、最终验收

## 1. 普通聊天

用户：

```text
今天感觉不错。
```

结果：

```text
不调用 Tool。
```

---

## 2. 实时信息

用户：

```text
明天新加坡天气怎么样？
```

结果：

```text
Tool Decision
→ weather
→ result
→ AI
→ natural reply
```

---

## 3. 计算

用户：

```text
23891 × 731 是多少？
```

结果：

```text
calculator
```

---

## 4. 搜索

用户：

```text
帮我查一下最近的 XXX 信息。
```

结果：

```text
search
```

---

## 5. Tool 不可用

Tool Disabled：

不能编造结果。

---

## 6. Tool 参数错误

不能崩溃。

---

## 7. Provider 失败

可以 fallback。

---

## 8. Tool 超时

必须正常超时并继续处理。

---

## 9. Tool Loop

达到：

```text
max_calls_per_turn
```

必须停止。

---

## 10. QQ 端

永远不会出现：

```text
/tool
/tools
/help
```

也不会暴露：

```text
tool_name
API
provider
JSON
execution trace
```

---

# v0.6 §115 一百一十五、最终产品目标

v0.5 的 CatooBot：

```text
它能记得你过去发生的事情。
```

v0.6 的 CatooBot：

```text
它不仅记得，
还能够在合适的时候利用外部工具帮你完成事情。
```

最终用户体验应该类似：

```text
用户：
你知道明天会不会下雨吗？

CatooBot：
嗯，我帮你看一下。

（内部调用 Weather Tool）

CatooBot：
明天降雨概率有点高，出门带把伞吧。
```

用户看不到：

```text
Tool
API
Provider
JSON
OneBot
NapCat
Model
Memory Retrieval
```

这些都属于：

```text
CatooBot Runtime
```

内部实现。

---

# v0.6 §116 一百一十六、最终交付

完成：

# CatooBot v0.6

必须具备：

```text id="bo8f6h"
✅ Tool Interface
✅ Tool Registry
✅ Tool Schema
✅ Tool Router
✅ Tool Executor
✅ Tool Policy
✅ Tool Permission
✅ Tool Result
✅ Tool Errors
✅ Tool Budget
✅ Loop Detection
✅ Tool Cache
✅ Tool Metrics
✅ Tool Audit Log

✅ Time Tool
✅ Calculator Tool
✅ Weather Tool
✅ Web Search Tool

✅ Provider abstraction
✅ Provider fallback
✅ Tool Call Adapter
✅ Multi-tool execution

✅ Persona integration
✅ Memory integration
✅ Topic integration
✅ Relationship integration
✅ Behavior integration

✅ WebUI Tool Management
✅ WebUI Tool Debug
✅ WebUI Tool Test
✅ WebUI Tool Logs
✅ WebUI Tool Metrics
✅ WebUI Tool Permissions
✅ WebUI Credential Management

✅ 完整测试
✅ Integration Test
✅ Database Migration
✅ Regression Test
```

同时保持：

```text id="r2h0j9"
✅ 无 QQ Bot 指令
✅ 无 QQ 管理命令
✅ WebUI 唯一管理入口
✅ Persona 稳定
✅ Memory 严格隔离
✅ Behavior 正常
✅ Initiative 正常
✅ Model Router 正常
✅ NapCat / OneBot 正常
```

---

# v0.6 §117 一百一十七、开发硬性要求

1. 先完整阅读 v0.5 源码。
2. 不假定现有目录结构。
3. 不重写稳定模块。
4. Tool 与 AI Engine 解耦。
5. Tool 与 Model Router 解耦。
6. Provider 与 Tool 解耦。
7. 所有 Tool 参数必须 Schema Validate。
8. 所有 Tool 必须有 Timeout。
9. 所有 Tool 必须有限流。
10. 所有 Tool 必须可 Enable/Disable。
11. 所有 Tool 结果必须标准化。
12. 所有外部数据视为不可信内容。
13. Tool 失败不能导致聊天系统崩溃。
14. Tool 不可用时不能编造结果。
15. 不允许任意 Shell / Python / PowerShell 执行。
16. Tool Call 必须有数量上限。
17. Tool Loop 必须检测。
18. Tool 管理只能通过 WebUI。
19. Tool 执行细节不能出现在 QQ。
20. Tool Result 不自动进入长期 Memory。
21. 不允许跨用户/跨群使用错误的 Tool Context。
22. 所有凭据必须安全存储。
23. 不允许把第三方网页内容当成 System Prompt。
24. 所有新增能力都应该可以关闭。
25. 优先保证现有自然聊天体验不被破坏。

现在开始：

**首先完整阅读 CatooBot v0.5 源码、数据库 Schema、Memory、ContextBuilder、AI Engine、Behavior Engine、WebUI 和当前配置系统，然后在现有架构上增量开发 CatooBot v0.6。**