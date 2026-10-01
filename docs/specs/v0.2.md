# CatooBot v0.2 开发任务：AI Engine + OpenAI Compatible Provider + 多模型自动故障转移

## 一、前置条件

CatooBot v0.1 已经完成并可以正常运行。

当前项目已经具备：

- NapCatQQ 接入
- OneBot 11
- WebSocket
- Message / Segment
- Event System
- Command System
- Plugin System
- Permission System
- SQLite
- Logger
- 基础测试

现在不要重新设计 v0.1。

**必须先阅读现有项目源码，理解当前架构，然后在现有代码基础上增量开发。**

禁止：

- 重写整个项目
- 删除已经实现的模块
- 把现有架构改成 NoneBot / MaiBot / NcatBot
- 将 AI 逻辑直接塞进 OneBot Adapter
- 将 AI 逻辑直接写死在 Message Handler 中

---

# 二、v0.2 的目标

这一版本只解决一件事：

> 让 CatooBot 成为一个真正可以通过 QQ 与大语言模型聊天的 Bot。

完整链路：

```text
QQ
 ↓
NapCat
 ↓
OneBot 11
 ↓
CatooBot Event
 ↓
Message Router
 ↓
AI Trigger
 ↓
Conversation Context
 ↓
AI Engine
 ↓
Model Router
 ↓
Provider
 ↓
OpenAI Compatible API
 ↓
模型
 ↓
AI Response
 ↓
CatooBot
 ↓
OneBot API
 ↓
NapCat
 ↓
QQ
```

---

# 三、核心设计原则

这一版本必须坚持：

```text
QQ 接入层
        ↓
CatooBot Core
        ↓
AI Engine
        ↓
Model Router
        ↓
Provider
        ↓
具体 AI API
```

任何具体模型供应商都不能污染 Core。

例如：

错误：

```python
if model == "glm":
    ...
```

大量出现在业务代码中。

正确：

```text
AIEngine
   ↓
ModelRouter
   ↓
Provider
```

让上层只知道：

```python
await ai.chat(...)
```

而不需要知道底层到底是：

- DeepSeek
- GLM
- Kimi
- MiniMax
- OpenAI
- SiliconFlow

---

# 四、增加 AI 模块

在现有：

```text
app/
```

下面增加：

```text
app/
├─ ai/
│  ├─ __init__.py
│  ├─ engine.py
│  ├─ models.py
│  ├─ context.py
│  ├─ provider.py
│  ├─ router.py
│  ├─ errors.py
│  └─ providers/
│     ├─ __init__.py
│     └─ openai_compatible.py
```

如果现有项目已有更合适的目录结构，可以沿用现有风格，不要机械复制目录。

---

# 五、AI 数据结构

设计统一的数据模型。

## 1. ChatMessage

至少支持：

```text
system
user
assistant
```

例如：

```python
ChatMessage(
    role="user",
    content="你好"
)
```

---

## 2. AIRequest

应该至少包含：

```text
messages
model
temperature
max_tokens
stream
metadata
```

其中 model 可以为空，由 ModelRouter 决定。

---

## 3. AIResponse

至少包含：

```text
content
model
provider
usage
finish_reason
raw_response
```

usage 如果 API 没有提供，可以为空。

---

# 六、Provider 抽象

建立统一接口：

```python
class AIProvider(ABC):

    async def chat(
        self,
        request: AIRequest
    ) -> AIResponse:
        ...
```

未来任何模型都必须通过这个接口进入 CatooBot。

---

# 七、OpenAI Compatible Provider

第一版先只实现一个：

```text
OpenAICompatibleProvider
```

目标是兼容所有提供 OpenAI 风格 API 的服务。

配置：

```yaml
providers:
  siliconflow:
    type: openai_compatible
    base_url: "https://api.siliconflow.cn/v1"
    api_key_env: "SILICONFLOW_API_KEY"
```

不要把 API Key 写在 YAML。

必须从环境变量读取：

```env
SILICONFLOW_API_KEY=
```

Provider 要支持：

```text
POST /chat/completions
```

并使用标准：

```text
Authorization: Bearer xxx
Content-Type: application/json
```

---

# 八、不要把 SiliconFlow 写死

虽然第一阶段主要使用 SiliconFlow，但是架构必须支持：

```yaml
providers:

  siliconflow:
    type: openai_compatible
    base_url: ...
    api_key_env: SILICONFLOW_API_KEY

  provider2:
    type: openai_compatible
    base_url: ...
    api_key_env: PROVIDER2_API_KEY
```

以后增加新的 API 平台时：

**只需要增加配置，不应该修改 AI Engine。**

---

# 九、Model Router

这是 v0.2 最重要的部分。

建立：

```text
AIEngine
   ↓
ModelRouter
   ↓
ModelCandidate[]
```

配置示例：

```yaml
ai:

  default_temperature: 0.8

  models:

    - name: primary
      provider: siliconflow
      model: "MODEL_A"

    - name: secondary
      provider: siliconflow
      model: "MODEL_B"

    - name: fallback_1
      provider: siliconflow
      model: "MODEL_C"

    - name: fallback_2
      provider: siliconflow
      model: "MODEL_D"
```

不要把模型名称硬编码进 Python。

---

# 十、模型故障转移

实现自动 Failover。

例如：

```text
Primary
   ↓
429
   ↓
Secondary
   ↓
429
   ↓
Fallback 1
   ↓
成功
```

最终 API 调用应该类似：

```python
response = await ai.chat(...)
```

内部自动：

```text
Model A
 ↓
失败
 ↓
Model B
 ↓
失败
 ↓
Model C
 ↓
成功
```

上层无需知道发生了切换。

---

# 十一、错误类型必须标准化

建立统一 AI 异常：

```text
AIError
├─ AuthenticationError
├─ RateLimitError
├─ InvalidRequestError
├─ ModelNotFoundError
├─ TimeoutError
├─ ConnectionError
├─ ServerError
└─ UnknownAIError
```

Provider 的各种 HTTP 错误必须转换为这些统一错误。

尤其是：

```text
HTTP 429
```

必须转换成：

```python
RateLimitError
```

---

# 十二、429 自动降级策略

重点实现：

```text
429
↓
记录当前模型失败
↓
切换到下一个模型
↓
继续请求
```

日志应该显示：

```text
[AI] Requesting model=MODEL_A
[AI] Rate limited: model=MODEL_A
[AI] Falling back to model=MODEL_B
[AI] Requesting model=MODEL_B
[AI] Success model=MODEL_B
```

但是：

**不要把 API Key 或 Authorization Header 打进日志。**

---

# 十三、不是所有错误都应该切模型

非常重要。

不要简单做成：

```python
except Exception:
    next_model()
```

应该区分错误。

### 应切换模型

```text
429
Model unavailable
Temporary server error
部分 5xx
```

### 不应该盲目切换模型

```text
401
403
Invalid API Key
Invalid request
Prompt 参数错误
请求格式错误
```

如果整个 Provider 的 API Key 都错误，切换同一 Provider 的其他模型没有意义。

例如：

```text
SiliconFlow
├── Model A ← 401
├── Model B ← 不应该继续重试
└── Model C ← 不应该继续重试
```

应直接报 Provider AuthenticationError。

---

# 十四、模型冷却机制

不要在一个模型刚收到 429 后，下一条消息马上再次请求它。

为模型设计临时 cooldown：

```yaml
ai:
  cooldown:
    rate_limit_seconds: 30
    server_error_seconds: 10
```

例如：

```text
Model A
   ↓
429
   ↓
cooldown 30 秒
```

接下来 30 秒：

```text
Model A 不参与路由
```

30 秒后恢复。

---

# 十五、并发安全

注意多个 QQ 用户可能同时请求 AI。

例如：

```text
用户 A → Model A → 429
用户 B → Model A → 429
用户 C → Model A
```

不能产生大量重复失败请求。

Model Router 至少需要保证：

- cooldown 状态线程/协程安全
- 模型状态不会被并发请求破坏
- 不要因为一个请求失败导致整个 Router 崩溃

优先使用 asyncio 原语。

---

# 十六、AI Trigger

这一版本不要让所有 QQ 消息都调用 AI。

默认规则：

### 私聊

可以直接触发 AI：

```text
用户：
你好
```

机器人：

```text
你好呀！
```

### 群聊

默认只有以下情况触发：

```text
@CatooBot 你好
```

或者：

```text
/chat 你好
```

不要默认读取所有群聊消息并调用 AI。

这样可以避免：

- API 浪费
- 群聊刷屏
- 上下文混乱
- 不必要的模型调用

---

# 十七、创建 Chat Plugin

新增：

```text
plugins/
└─ chat/
   ├─ __init__.py
   └─ plugin.py
```

聊天 Plugin 只负责：

```text
判断是否触发
↓
构造 Context
↓
调用 AIEngine
↓
发送回复
```

不要让它自己实现：

- API 请求
- 模型切换
- 429 判断
- WebSocket
- 数据库

这些全部属于底层模块。

---

# 十八、机器人 System Prompt

支持配置：

```yaml
ai:
  system_prompt: |
    你是 CatooBot，一个运行在 QQ 上的 AI 助手。
    请使用自然、简洁的中文回答。
```

要求：

System Prompt 不要硬编码。

以后我要加入：

```text
人格
角色
记忆
用户信息
群信息
时间
```

都应该可以在 Context 层组合。

---

# 十九、Conversation Context

v0.2 暂时只做：

**短期上下文。**

例如最近：

```text
user
assistant
user
assistant
```

保留最近 N 条消息。

配置：

```yaml
ai:
  context:
    enabled: true
    max_messages: 20
```

暂时：

**不要实现长期 Memory。**

数据库可以暂时保存 conversation，但不要做复杂向量数据库。

---

# 二十、按会话隔离上下文

至少区分：

```text
private:user_id
group:group_id
```

例如：

```text
private:123456
group:789012
```

不能让：

```text
A群
```

的对话泄漏到：

```text
B群
```

也不能让一个私聊用户的上下文进入另一个用户。

---

# 二十一、上下文结构

建议：

```text
ConversationManager
│
├─ get_context(session_id)
├─ append_user_message()
├─ append_assistant_message()
├─ clear_context()
└─ trim_context()
```

未来长期 Memory 可以在这里继续扩展。

---

# 二十二、支持清空上下文

增加：

```text
/clear
```

行为：

```text
当前会话
↓
删除短期上下文
```

回复：

```text
当前对话上下文已清空。
```

只清空当前 Session。

---

# 二十三、AI 命令

增加：

```text
/chat <message>
```

例如：

```text
/chat 介绍一下 Minecraft
```

同时：

```text
/clear
```

清空当前对话。

---

# 二十四、@机器人聊天

群聊：

```text
@CatooBot 你好
```

提取：

```text
你好
```

然后送入 AI。

例如：

```text
原消息：

[At(CatooBot), Text("你好")]
```

转换：

```text
你好
```

不要把：

```text
[CQ:at,qq=xxxx]
```

这种底层字符串直接丢给 AI。

---

# 二十五、回复策略

AI 响应之后：

```text
AIResponse
 ↓
提取 content
 ↓
Message
 ↓
Bot.send_message()
```

如果模型返回空内容：

不要发送空消息。

记录：

```text
[AI] Empty response
```

然后向用户返回一个友好的错误信息。

---

# 二十六、AI 回复长度

QQ 不适合无限长文本。

在 Message Sender 层增加一个：

```python
split_message()
```

未来可以根据平台限制拆分长消息。

例如：

```text
AI:
一段很长的内容……
```

自动：

```text
消息 1
消息 2
消息 3
```

拆分时不要：

- 在 UTF-8 字符中间截断
- 随意截断 Markdown
- 丢失换行

第一版可以采用简单安全的按长度切分。

---

# 二十七、超时

AI API 必须有 timeout。

例如：

```yaml
ai:
  timeout: 60
```

超过：

```text
TimeoutError
```

如果还有备用模型：

```text
Model A timeout
↓
Model B
```

是否 fallback 必须由错误类型决定。

---

# 二十八、重试

不要做无限 retry。

推荐：

```text
单个模型：
最多 retry 1 次
```

如果第一次：

```text
429
```

不要：

```text
retry
retry
retry
retry
```

而是直接进入下一个模型。

对于临时网络错误，可以允许一次短暂 retry。

---

# 二十九、AI 状态

为每个 Model 保存运行状态：

```text
model
provider
enabled
cooldown_until
last_error
last_success
failure_count
```

后续 Web Dashboard 可以显示。

---

# 三十、状态不要写死到配置

配置只描述：

```text
模型是什么
```

Runtime State 保存：

```text
这个模型现在是否 cooldown
上次什么时候失败
```

不要把 runtime cooldown 写回 yaml。

---

# 三十一、配置示例

创建：

```text
config/config.example.yaml
```

例如：

```yaml
bot:
  name: CatooBot
  debug: true

onebot:
  enabled: true
  host: 127.0.0.1
  port: 8080
  path: /onebot/v11/ws
  access_token: ""

ai:

  enabled: true

  system_prompt: |
    你是 CatooBot，一个运行在 QQ 上的 AI 助手。
    使用自然、友好的中文回答。

  timeout: 60

  context:
    enabled: true
    max_messages: 20

  cooldown:
    rate_limit_seconds: 30
    server_error_seconds: 10

  providers:

    siliconflow:
      type: openai_compatible
      base_url: "https://api.siliconflow.cn/v1"
      api_key_env: "SILICONFLOW_API_KEY"

  models:

    - name: primary
      provider: siliconflow
      model: "MODEL_A"

    - name: secondary
      provider: siliconflow
      model: "MODEL_B"

    - name: fallback
      provider: siliconflow
      model: "MODEL_C"
```

不要填写真实 API Key。

---

# 三十二、环境变量

创建：

```text
.env.example
```

内容：

```env
SILICONFLOW_API_KEY=
```

实际项目：

```text
.env
```

必须加入：

```text
.gitignore
```

---

# 三十三、日志

AI 日志至少包含：

```text
[AI]
[AI.Provider]
[AI.Router]
[AI.Context]
```

例如：

```text
[AI.Router] Selected model=primary
[AI.Provider] Sending request provider=siliconflow
[AI.Router] Model primary returned 429
[AI.Router] Cooling down model=primary for 30s
[AI.Router] Fallback -> secondary
[AI.Router] Response success model=secondary
```

绝对不要输出：

```text
API KEY
Authorization
完整敏感请求头
```

---

# 三十四、测试

这一版本必须补充自动化测试。

至少：

## test_ai_models.py

测试：

- ChatMessage
- AIRequest
- AIResponse

## test_ai_router.py

测试：

### 正常

```text
A → success
```

### 429

```text
A → 429
B → success
```

### 连续失败

```text
A → 429
B → 500
C → success
```

### 全部失败

```text
A → fail
B → fail
C → fail
```

应该返回统一异常。

### Cooldown

验证：

```text
A → 429
↓
cooldown
↓
Router 不再选择 A
```

---

# 三十五、Provider Mock

测试不能依赖真实 SiliconFlow API。

请建立 Mock Provider：

```python
MockAIProvider
```

能够模拟：

```text
success
429
401
403
500
timeout
invalid_request
```

所有 Router 测试必须使用 Mock。

---

# 三十六、集成测试

提供一个最小集成测试：

```text
MessageEvent
 ↓
Chat Plugin
 ↓
AI Engine
 ↓
Mock Provider
 ↓
Bot.send_message
```

确保整条链路能够工作。

---

# 三十七、README

更新 README，明确介绍：

```text
CatooBot v0.2
```

架构：

```text
QQ
 ↓
NapCat
 ↓
OneBot
 ↓
CatooBot
 ↓
AI Engine
 ↓
Model Router
 ↓
Provider
```

并提供：

### 安装依赖

### 配置 .env

### 配置 config.yaml

### 启动

```powershell
python run.py
```

### 配置模型

### 测试 `/chat`

### 测试 `@CatooBot`

### 测试 `/clear`

### 查看 429 fallback 日志

---

# 三十八、不要实现的功能

这一版本明确禁止提前实现：

- 长期记忆
- RAG
- 向量数据库
- Agent
- Tool Calling
- 自动联网搜索
- 图片理解
- 图片生成
- 多模态
- Web Dashboard
- 多平台适配
- 插件市场
- 自动学习人格
- 复杂 Prompt 管理 UI

这些属于未来版本。

---

# 三十九、未来架构预留

最终希望演变成：

```text
CatooBot
│
├─ Core
│
├─ Adapters
│   ├─ OneBot
│   ├─ Web
│   └─ Minecraft
│
├─ AI
│   ├─ Engine
│   ├─ Router
│   ├─ Providers
│   ├─ Context
│   ├─ Memory
│   └─ Agent
│
├─ Plugins
│
├─ Database
│
└─ WebUI
```

因此本阶段代码必须保持模块化。

---

# 四十、最重要的开发流程

请按照以下顺序开发：

```text
1. 读取现有项目
2. 分析 v0.1 当前真实结构
3. 找到 Event / Plugin / Message / Config 等已有模块
4. 在现有结构上增量扩展
5. 创建 AI 数据模型
6. 创建 Provider 接口
7. 创建 OpenAI Compatible Provider
8. 创建 Model Router
9. 实现错误分类
10. 实现 429 cooldown
11. 实现 failover
12. 创建 Conversation Context
13. 创建 Chat Plugin
14. 接入 /chat
15. 接入 @CatooBot
16. 实现 /clear
17. 增加消息长度拆分
18. 编写单元测试
19. 编写集成测试
20. 更新 README
21. 运行全部测试
22. 修复全部问题
```

---

# 四十一、验收标准

完成后必须可以：

### 1

QQ：

```text
/chat 你好
```

CatooBot 调用 AI 并回复。

### 2

群聊：

```text
@CatooBot 你好
```

正常回复。

### 3

连续对话：

```text
用户：你好
Bot：你好呀

用户：你还记得我刚刚说什么吗？
Bot：...
```

上下文有效。

### 4

执行：

```text
/clear
```

当前会话被清空。

### 5

模拟 429：

```text
Model A
↓
429
↓
Model B
↓
成功
```

自动切换。

### 6

模拟 A、B 失败：

```text
A → 429
B → 500
C → success
```

自动切换到 C。

### 7

模拟全部失败：

CatooBot 不崩溃。

用户收到友好的错误消息。

### 8

两个用户同时请求：

```text
User A → AI
User B → AI
```

不会出现：

- Context 串线
- Future 串线
- Session 串线
- 模型状态损坏

### 9

NapCat 断线：

CatooBot 不崩溃。

NapCat 恢复连接后继续工作。

---

# 四十二、最终要求

最终交付：

**CatooBot v0.2**

具备：

```text
✅ QQ AI 聊天
✅ @机器人触发
✅ /chat
✅ /clear
✅ OpenAI Compatible Provider
✅ SiliconFlow 配置支持
✅ Model Router
✅ 429 自动切换
✅ Cooldown
✅ Error Classification
✅ Conversation Context
✅ 多会话隔离
✅ Timeout
✅ 基础 Retry
✅ 长消息拆分
✅ 完整测试
✅ README
```

并保持：

**CatooBot 是自研核心。**

NapCat 只负责：

```text
QQ 接入
```

OneBot 只负责：

```text
QQ Bot 协议
```

AI Provider 只负责：

```text
调用模型
```

AI Engine 负责：

```text
统一调度
```

Model Router 负责：

```text
模型选择与故障转移
```

Chat Plugin 负责：

```text
把 QQ 消息转换成 AI 请求，并把 AI 结果转换成 QQ 消息
```

现在开始时，**先完整阅读现有 CatooBot v0.1 源码，再开始修改。不要假定目录结构一定与本 Prompt 一致。**