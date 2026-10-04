# CatooBot v2.1 Phase 13 · Real External Runtime Integration & OneBot Event Gateway

## 0. 基线

当前最终基线：

```text id="f1x8c4"
HEAD: 07fb39c5ece0e3a1f100175995add73297c85fb4
Phase: 12.1
Tests: 1324 passed
CI: success
```

正式进入：

# Phase 13 · Real External Runtime Integration & OneBot Event Gateway

本阶段第一次让 CatooBot 真正连接外部 QQ / NapCat / OneBot 11 运行环境。

目标不是做“更多人格功能”。

目标是建立一条可靠、可测试、不会污染 Sandbox 的真实运行链：

```text
id="r7f3k2"
NapCat / OneBot 11
        ↓
Transport Event
        ↓
Event Normalization
        ↓
ExternalWorldEvent
        ↓
CatooBot Runtime
        ↓
CognitiveContext
        ↓
ConversationRuntime
        ↓
Response Commit Guard
        ↓
Outbound Response
        ↓
NapCat / OneBot 11
```

---

# 1. Phase 13 的核心目标

完成后，系统应该能够：

```text
QQ 用户发消息
↓
NapCat 收到 OneBot 事件
↓
CatooBot 正确识别 actor / person / group
↓
去重
↓
进入现有 ExternalWorldEvent
↓
执行现有 Sandbox / Relationship / Cognitive / Conversation 流程
↓
生成 ConversationResponse
↓
通过 commit guard
↓
转换为 OneBot outbound action
↓
发送 QQ 回复
```

并且：

```text
重复事件
→ 不重复处理

机器人自己的消息
→ 不重新触发自己

网络断线
→ 不污染 Sandbox

网络重连
→ 恢复事件接收

并发消息
→ 不破坏同一社会上下文的顺序

Response stale
→ 不发送
```

---

# 2. 最重要的架构边界

必须明确：

```text
Transport
= NapCat / OneBot 连接

Adapter
= 协议转换

ExternalWorldEvent
= 外部事实

Sandbox
= 世界事实权威

ConversationRuntime
= 当前回复生成

ConversationResponse
= 语言输出提案

Outbound Adapter
= 把合法 Response 发送回 QQ
```

禁止：

```text
NapCat Adapter
→ 直接修改 Relationship

NapCat Adapter
→ 直接创建 Memory

NapCat Adapter
→ 直接创建 Commitment

NapCat Adapter
→ 直接执行 Action

LLM
→ 直接调用 OneBot API
```

---

# 3. 不允许把 OneBot 逻辑塞进 Sandbox Core

禁止在：

```text
app/sandbox/
```

里出现：

```text
WebSocket client
OneBot HTTP client
NapCat API request
QQ API token
```

Sandbox 必须保持：

```text
platform-agnostic
```

---

# 4. 推荐目录结构

优先审计现有结构后采用：

```text id="5czc3e"
app/
├── sandbox/
│   ├── runtime.py
│   ├── conversation.py
│   ├── external.py
│   └── ...
│
└── integrations/
    └── onebot/
        ├── client.py
        ├── models.py
        ├── inbound.py
        ├── outbound.py
        └── runtime.py
```

但如果当前项目已有更合适的：

```text
external_adapters.py
```

体系，应优先扩展已有体系。

禁止：

```text
app/qq2/
app/napcat2/
app/bot_runtime2/
```

重复创建第二套运行架构。

---

# 5. OneBot 只负责协议适配

OneBot adapter 负责：

```text
id="2r7j9c"
JSON event
↓
typed transport event
```

以及：

```text
ConversationResponse
↓
OneBot API payload
```

不能负责：

```text
person matching
relationship logic
memory logic
commitment detection
goal generation
```

这些继续由 Sandbox 处理。

---

# 6. Inbound Event 类型

Phase 13 第一阶段重点支持：

```text id="0n6c1v"
message
```

以及必要的生命周期事件：

```text id="8v9f2a"
meta_event / heartbeat
notice
request
```

但：

**不要一次性实现 OneBot 全部 Action API。**

当前真正需要的是：

```text
receive message
send message
connection lifecycle
```

其他 API 留给后续阶段。

---

# 7. Message Event Normalization

OneBot message event 必须先转换成项目自己的：

```text id="e43d9a"
NormalizedMessageEvent
```

至少包含：

```text
event_id
self_id
post_type
message_type
message_id
user_id
group_id
message
raw_time
ingest_time
```

以及：

```text id="m71z6s"
social_space_id
```

必要时：

```text actor_id
```

---

# 8. 不要让 OneBot JSON 直接进入 Sandbox

禁止：

```python id="k45gq1"
sandbox.process(onebot_json)
```

必须：

```text id="nv5i8a"
OneBot JSON
↓
Normalizer
↓
NormalizedMessageEvent
↓
ExternalWorldEvent
↓
Sandbox
```

这样未来换：

```text Discord
Telegram
Web
Minecraft
```

不需要修改 Sandbox Core。

---

# 9. ExternalWorldEvent Mapping

Message 必须最终形成现有：

```text id="m94x8s"
ExternalWorldEvent
```

至少表达：

```text actor
character
social_space
content
source
event identity
received timestamp
```

且：

```text id="w3ap7x"
ExternalWorldEvent
```

才是 Sandbox 看到的外部事实。

---

# 10. PersonIdentity Resolution

严格保持 Phase 11 / 12：

```text id="g8n2ca"
OneBot user_id
    ↓
persons.for_qq()
    ↓
PersonIdentity
    ↓
person_id
```

禁止：

```text display_name
message content
nickname
memory
relationship
```

作为身份猜测依据。

解析失败：

```text id="y2x0cf"
创建/返回独立外部 identity
```

或者沿用当前 resolver 的既有 fallback。

不要因为 QQ nickname 看起来像某个核心人物就自动绑定。

---

# 11. Private Message

私聊：

```text id="0khw29"
message_type = private
```

至少映射：

```text user_id
person_id
social_space_id
```

建议：

```text social_space_id = private:<person_id>
```

但如果现有项目已有 social-space identity，请复用现有实现。

---

# 12. Group Message

群聊：

```text id="df3m1z"
message_type = group
```

至少保留：

```text group_id
user_id
person_id
social_space_id
message_id
```

注意：

```text group_id
```

是社会空间。

```text person_id
```

仍然是发言者的人物身份。

两者绝不能混为一谈。

---

# 13. Group Isolation

必须确保：

```text id="x7z8qa"
Group A
Person X

Group B
Person X
```

不会因为 person_id 一样就错误地把：

```text group context
```

混在一起。

也不能因为 group_id 一样，就错误地把：

```text person identity
```

合并成群组人格。

---

# 14. Event Identity

Phase 13 必须定义：

```text id="9fj3c2"
transport_event_id
```

Message event 优先：

```text message:<self_id>:<message_id>
```

如果当前协议事件没有 message_id：

使用：

```text canonical event fields
+
deterministic digest
```

不要使用随机 UUID 作为 dedupe identity。

---

# 15. Deduplication

Inbound event 必须：

```text id="7s4p0n"
before Sandbox processing
```

进行去重。

例如：

```text id="z8g4m2"
同一个 OneBot message event
→ 收到两次
→ ExternalWorldEvent 只进入 Sandbox 一次
```

---

# 16. Dedupe Scope

至少：

```text id="j3p4q8"
self_id
+
transport_event_id
```

共同构成事件 identity。

不能单独使用：

```text message_id
```

因为不同 bot account / runtime 可能存在相同编号。

---

# 17. Dedupe Storage

Phase 13 暂时不新增数据库。

使用：

```text id="t5k1u6"
bounded in-memory dedupe cache
```

至少有：

```text max size
TTL
LRU / ordered eviction
```

不能：

```text infinite set
```

导致长时间运行内存增长。

---

# 18. Restart Semantics

当前阶段：

```text id="q2v8m5"
dedupe cache
```

可以不跨进程持久化。

但必须明确：

```text restart
→ dedupe cache empty
```

因此：

> Phase 13 不保证 restart 后旧 transport event 的永久幂等。

禁止假装支持。

如果外部 transport 本身会 replay 历史事件，必须在 adapter 层明确处理，不要静默假设不会发生。

---

# 19. Self Message Protection

必须先处理：

```text id="x6n1r4"
self_id
```

如果 inbound message 的：

```text user_id == current self_id
```

或 OneBot event 明确表示：

```text sender == self
```

必须：

```text id="u4p3yx"
ignore
```

避免：

```text bot
→ send response
→ receive own response
→ generate response
→ infinite loop
```

---

# 20. Self Message 与 report_self_message

不能依赖：

```text id="7c2v9m"
report_self_message=false
```

作为唯一安全机制。

即使 transport 配置没有回报自己消息：

```text id="p1k0x8"
runtime
```

仍然必须拥有 self-message guard。

---

# 21. Message Ordering

同一：

```text id="4b6x9s"
character_id
+
social_space_id
```

的消息必须保持 FIFO processing。

例如：

```text id="9zv4a1"
M1
M2
M3
```

即使：

```text M2 processing slow
M3 arrives
```

也不能让：

```text M3
```

在：

```text M2
```

之前改变同一个社会上下文。

---

# 22. 不做全局串行

不要：

```text id="a83nwp"
所有 QQ 消息
→ 一个全局 lock
```

这会导致：

```text Group A 卡顿
→ Group B 也被阻塞
```

推荐：

```text id="q1j2nm"
per-social-space serialization
```

即：

```text Group A = lane A
Group B = lane B
Private X = lane X
```

---

# 23. 同一 Social Space 的 Lane

建议：

```text id="6yg1e4"
lane_key =
character_id + ":" + social_space_id
```

同一 lane：

```text FIFO
```

不同 lane：

```text concurrent
```

---

# 24. Person vs Group Ordering

不要使用：

```text person_id
```

作为 group message 的唯一 lane。

Group 中：

```text id="3u5s9j"
Person A
Person B
Person C
```

所有人共享：

```text group/social_space lane
```

因为他们共同影响同一个社会空间。

---

# 25. External event timestamp

保留：

```text id="k9p2b7"
source_timestamp
```

和：

```text id="m0w4s6"
ingest_timestamp
```

两者不能混淆。

对于世界事实：

```text id="z0x5s7"
source_timestamp
```

表示外部事件发生时间。

`ingest_timestamp` 仅表示本地收到时间。

---

# 26. 不根据客户端时间重新排序

不要看到：

```text M1 timestamp = 100
M2 timestamp = 99
```

就自动：

```text reorder
```

Phase 13 默认：

```text arrival order
```

作为 processing order。

否则可能引入跨设备 / 时钟漂移错误。

---

# 27. External Event Sequence

建议为每个 inbound transport event 分配：

```text id="h7r8v1"
ingest_seq
```

单调递增。

用途：

```text trace
debug
ordering audit
```

不要把：

```text ingest_seq
```

当作 world event identity。

---

# 28. Backpressure

LLM 可能比 QQ 消息进入速度慢。

不能：

```text every incoming message
→ spawn unlimited task
```

必须有：

```text id="s8g3k0"
bounded queue
```

或现有 scheduler / lane executor。

---

# 29. Queue Limit

每个 social lane：

```text id="6p2tq9"
max_pending
```

必须有限。

默认建议：

```text 20
```

如果已有合理配置则复用。

---

# 30. Queue Overflow

当：

```text id="d4m9r1"
lane queue full
```

不要：

```text OOM
无限等待
```

而应该：

```text id="u5k8c3"
drop / collapse according to deterministic policy
```

对于 Phase 13：

优先：

```text oldest non-started conversational turn
```

可被丢弃。

但：

```text verified non-chat external facts
```

不能因为对话队列满而静默丢失。

如果一个事件同时：

```text affects Sandbox
+
may trigger response
```

必须先完成：

```text Sandbox event ingestion
```

再决定 response 是否排队。

---

# 31. 重要：不要丢 Sandbox Fact

例如：

```text id="p1f7a0"
message
```

触发：

```text SocialInteractionFact
```

即使：

```text response queue full
```

也不能意味着：

```text Sandbox never saw the message.
```

正确：

```text external fact ingestion
    ↓
Sandbox
    ↓
response scheduling
```

两个阶段分开。

---

# 32. Message Processing Pipeline

标准流程：

```text id="r6s2u1"
Inbound OneBot Event
        ↓
Normalize
        ↓
Deduplicate
        ↓
Self-message guard
        ↓
Resolve character
        ↓
Resolve person
        ↓
Create ExternalWorldEvent
        ↓
Sandbox ingest
        ↓
Existing influence / relationship logic
        ↓
If response allowed
        ↓
ConversationRuntime
        ↓
Response Commit
        ↓
Outbound Queue
```

---

# 33. Response Policy

Phase 13 不重写：

```text external influence policy
```

现有：

```text mode_ceiling
mention rules
non-mention participation
existing group cognition
```

必须继续由已有系统决定。

Adapter 不能自己：

```text if private: always reply
if group: always reply
```

---

# 34. Group Message Non-mention

如果现有 Phase 9/10/11 已经存在：

```text non-mentioned group cognition
```

Phase 13 只负责把真实 OneBot message 接进去。

不要重新实现：

```text every 5 messages
random wakeup
participation probability
```

等策略。

---

# 35. @ Mention

OneBot message segment 可能是：

```text at
text
image
reply
```

Phase 13 首阶段至少必须：

```text text
at
```

正确 normalize。

---

# 36. Text Extraction

Message normalization 必须区分：

```text raw_message
plain_text
segments
```

不要丢失原始结构。

例如：

```text at bot + text
```

要保留：

```text mentioned_self = true
plain_text = ...
```

---

# 37. Message Content

Response Runtime 可以使用：

```text plain_text
```

但 Sandbox external fact 可以保留：

```text message segments metadata
```

以后支持：

```text image
reply
emoji
sticker
```

不会重新改 adapter。

---

# 38. Phase 13 第一阶段不做视觉

暂时不实现：

```text image understanding
video understanding
audio understanding
OCR
```

但 normalized event 结构应允许：

```text message segments
```

保留未知 segment metadata，而不是直接丢弃整个消息。

---

# 39. Outbound Response

只允许：

```text id="z5p2s4"
ConversationResponse
```

进入 outbound adapter。

禁止：

```text LLM
→ OneBot API
```

直接调用。

---

# 40. Outbound Mapping

例如：

```text mode=reply
text="..."
```

转换为：

```text id="j8k2m5"
send_msg
message_type
user_id / group_id
message
```

具体字段按照 OneBot 11 当前协议模型实现。

不要让 Sandbox 知道 `send_msg`。

---

# 41. Silent Response

如果：

```text id="p8z1k6"
mode=silent
```

必须：

```text no network send
```

不要：

```text send empty message
```

---

# 42. Acknowledge / Defer

Phase 12 已定义：

```text reply
acknowledge
defer
silent
```

Phase 13 初期：

```text acknowledge
defer
```

仍使用：

```text text
```

正常 outbound message。

除非未来有专门 transport policy。

---

# 43. Response Commit 必须在 Outbound Send 前

标准顺序：

```text id="l6u2s5"
ConversationResponse
↓
runtime.commit_conversation_response()
↓
fresh?
├─ no → suppress
└─ yes
    ↓
enqueue outbound
    ↓
send
```

不得：

```text response
→ enqueue
→ commit
```

否则 queue 中可能保存 stale response。

---

# 44. Outbound Queue 只接受已 commit response

队列对象：

```text id="q3m9a6"
OutboundMessage
```

必须来自：

```text fresh committed ConversationResponse
```

不能接受：

```text raw LLM proposal
raw ConversationResponse before commit
```

---

# 45. Outbound Retry

网络失败：

```text id="n7q2z4"
retry
```

必须是：

```text same already-committed response
```

不要重新：

```text run LLM
build CognitiveContext
```

否则可能：

```text duplicate response
```

---

# 46. Retry Limit

配置：

```text id="e5h1x9"
outbound_max_retries
```

建议默认：

```text 3
```

但如果已有 transport retry config，优先复用。

---

# 47. 不允许无限 retry

失败：

```text id="t8m2b4"
3 times
```

之后：

```text mark delivery failure
```

记录 trace。

不要进入：

```text infinite loop
```

---

# 48. Duplicate Outbound Protection

同一个：

```text id="c4s9n2"
response.turn_id
```

不能被发送两次，除非：

```text retry of same network attempt
```

网络成功后再次调用 send：

```text should not duplicate
```

Phase 13 至少在 outbound worker 中做：

```text id="y3v7q1"
per-turn sent/attempt state
```

使用 bounded runtime memory。

不要新增 database。

---

# 49. OneBot Connection Lifecycle

必须支持：

```text id="a8s4k1"
CONNECTING
CONNECTED
DISCONNECTED
RECONNECTING
STOPPED
```

不需要复杂状态机。

---

# 50. Reconnect

连接中断：

```text id="d6p2x9"
→ stop receive loop
→ reconnect with bounded backoff
```

建议：

```text 1s
2s
4s
8s
16s
max 30s
```

具体数值可配置。

不要 busy-loop。

---

# 51. Reconnect Jitter

允许：

```text id="g2m7w5"
small deterministic/random jitter
```

但：

```text reconnect behavior
```

不要成为核心 world logic。

---

# 52. Heartbeat

如果 OneBot transport 已提供 heartbeat：

```text id="q8n4b6"
复用 transport。
```

不要在 Sandbox 自己实现 QQ heartbeat。

---

# 53. Connection Failure 不进入 Sandbox

例如：

```text id="z4j1t8"
WebSocket disconnected
```

不要生成：

```text SOCIAL_INTERACTION
```

也不要：

```text Relationship changed
```

Connection state 属于 transport layer。

---

# 54. Login / Self ID

连接成功后：

```text id="k3m7p1"
必须确认 self_id
```

并建立：

```text current character
↔ OneBot self_id
```

映射。

不能：

```text first received user_id
```

自动当成 bot identity。

---

# 55. Multi-account Isolation

如果未来：

```text bot self_id A
bot self_id B
```

同一 process 内存在：

必须：

```text id="u2c8f5"
character_id
+
self_id
```

隔离。

同一个：

```text user_id
```

不能在两个 bot account 中自动合并为相同 world actor。

PersonIdentity 的 external-id namespace 必须包含 platform/account scope。

---

# 56. Character Mapping

当前角色由：

```text id="r7k1x6"
self_id
```

映射到：

```text CharacterDefinition
```

不能：

```text if self_id == xxx:
    CharacterA
```

硬编码。

使用配置。

---

# 57. Configuration

允许新增：

```text id="j4n8v3"
onebot.enabled
onebot.ws_url
onebot.token
onebot.self_id
onebot.reconnect
onebot.dedupe_ttl
onebot.dedupe_max_size
onebot.max_pending_per_lane
onebot.outbound_max_retries
```

但：

**先检查现有 config 是否已经存在等价字段。**

不要重复定义。

---

# 58. Secrets

OneBot token：

```text id="w5g2q8"
不得提交到 Git
```

public example：

```text token: ""
```

真实 config：

```text local only
```

继续遵守：

```text real Character Bible
real secrets
```

不进入 public mirror。

---

# 59. No WebSocket inside Sandbox

禁止 import：

```python
websockets
aiohttp
httpx
```

到：

```text app/sandbox/*
```

除非项目当前 sandbox already requires an existing network abstraction。

Sandbox 必须保持纯逻辑。

---

# 60. Runtime Shutdown

Phase 13 必须支持：

```text id="q9r4v6"
stop()
```

按顺序：

```text
stop accepting new inbound events
↓
finish current sandbox-critical event
↓
stop conversation queue
↓
stop outbound retry worker
↓
close WebSocket
```

不得：

```text abruptly kill running mutation
```

---

# 61. Graceful shutdown

已经开始发送的 outbound：

```text id="m8w2z3"
允许完成当前 attempt
```

但：

```text unstarted queued responses
```

可以丢弃。

不要为了保存聊天队列建立数据库。

---

# 62. Exception Boundary

Transport exception：

```text id="y4p8q2"
不能杀死 Sandbox runtime
```

Sandbox exception：

```text id="x7c3n9"
不能让 WebSocket receive loop 永久退出
```

LLM exception：

```text id="n5m1z7"
只影响当前 turn
```

OneBot send failure：

```text id="k6q4p3"
只影响当前 outbound item
```

---

# 63. Logging

必须提供结构化 trace：

```text id="h3m8v1"
transport_event_id
ingest_seq
self_id
character_id
social_space_id
person_id
event_type
turn_id
response_mode
delivery_status
```

禁止长期记录：

```text id="s4d7x2"
完整 Character Bible
API key
OneBot token
```

消息正文遵守现有日志策略，不建立永久 chat history。

---

# 64. Event Trace

建议新增：

```text id="p7k2w9"
EXTERNAL_TRANSPORT_RECEIVED
EXTERNAL_TRANSPORT_DEDUPED
EXTERNAL_TRANSPORT_DROPPED
OUTBOUND_RESPONSE_QUEUED
OUTBOUND_RESPONSE_SENT
OUTBOUND_RESPONSE_FAILED
```

这些是：

```text trace-only
```

不能改变：

```text world_revision
cognitive_revision
```

---

# 65. 不要把 transport event 当 World Event

尤其：

```text id="m5n8q3"
EXTERNAL_TRANSPORT_RECEIVED
```

只是：

```text transport trace
```

而：

```text id="f9r4v2"
ExternalWorldEvent
```

才进入 Sandbox。

---

# 66. Exactly-once 不要假装存在

Phase 13 的现实语义应该明确是：

```text id="y8k1w4"
Inbound:
at-least-once transport
+
bounded dedupe
+
single-lane processing

Outbound:
best-effort delivery
+
bounded retry
+
duplicate suppression
```

不要声称：

```text exactly once
```

除非真正具备持久化事务基础。

---

# 67. 不做可靠消息队列

禁止本阶段增加：

```text id="s2x7m1"
Redis queue
RabbitMQ
Kafka
Celery
database outbox
```

先用：

```text runtime bounded queue
```

验证架构。

---

# 68. 不做主动消息系统

Phase 13 不实现：

```text scheduled QQ message
autonomous outbound
heartbeat social behavior
主动找人聊天
随机发消息
```

只有：

```text inbound event
→ possible response
```

---

# 69. 不做多媒体

本阶段：

```text text + at
```

足够。

图片 / voice / video：

```text normalized but not interpreted
```

---

# 70. 不做聊天历史数据库

继续禁止：

```text conversation_messages
message_embeddings
conversation_sessions
```

---

# 71. 不修改 Conversation semantics

保持 Phase 12 / 12.1：

```text reply
acknowledge
defer
silent
```

保持：

```text memory_refs
experience_refs
action_candidate_id
world_revision
cognitive_revision
```

---

# 72. 不修改 Response Commit Guard

Phase 12.1 的：

```text id="n6j2c4"
commit_conversation_response()
```

必须继续作为唯一 response freshness gate。

---

# 73. 不修改 Relationship / Commitment / Experience / Memory

保持：

```text id="v7k5s1"
Phase 8
Phase 9
Phase 10
Phase 11
```

所有语义不变。

Phase 13 只负责把真实 transport event 接入已有系统。

---

# 74. 不修改 Character Bible

继续：

```text id="h8p3v5"
character-agnostic core
```

任何角色内容从：

```text CharacterDefinition / CharacterWorldSeed
```

读取。

---

# 75. 推荐实现顺序

严格按：

## Step 1

审计现有：

```text external.py
external_adapters.py
runtime.py
conversation.py
config
```

确认哪些 OneBot / adapter 能力已经存在。

禁止重复实现。

## Step 2

建立：

```text NormalizedMessageEvent
```

和 OneBot inbound decoder。

## Step 3

实现：

```text event dedupe
self guard
person resolution
social_space mapping
```

## Step 4

接入现有：

```text ExternalWorldEvent
```

## Step 5

实现：

```text per-social-space lanes
bounded queues
```

## Step 6

接入：

```text ConversationRuntime
```

## Step 7

接入：

```text Response Commit Guard
```

## Step 8

实现：

```text OneBot outbound adapter
```

## Step 9

实现：

```text reconnect
retry
shutdown
```

## Step 10

做完整 fake transport integration tests。

---

# 76. 测试：不要依赖真实 QQ

CI 不允许依赖：

```text live QQ
live NapCat
real API
real network
```

必须提供：

```text FakeOneBotTransport
```

用于测试：

```text inbound event
outbound send
disconnect
reconnect
duplicate
delay
failure
```

---

# 77. Inbound Tests

至少：

### A. Private message

```text QQ event
→ person_id
→ ExternalWorldEvent
→ ConversationResponse
```

### B. Group message

```text group_id
social_space_id
person_id
```

全部正确。

### C. Mention

```text @self + text
```

正确 normalize。

### D. Self message

```text user_id == self_id
→ dropped
```

### E. Duplicate

```text same message event ×2
→ sandbox receives once
```

### F. Same message_id across different self_id

```text bot A + message 123
bot B + message 123
→ both accepted
```

### G. Unknown person

```text unresolvable sender
→ no guess
```

### H. Character mapping

```text self_id
→ correct character
```

### I. Group isolation

```text Group A
Group B
```

contexts independent。

---

# 78. Ordering Tests

至少：

### A. Same social lane FIFO

```text M1
M2
M3
```

必须：

```text M1 → M2 → M3
```

### B. Different lanes concurrent

```text Group A slow
Group B fast
```

B 不得被 A 阻塞。

### C. Slow LLM

```text M1 LLM slow
M2 arrives
```

同 lane 仍保持定义的顺序。

### D. Queue overflow

超过：

```text max_pending
```

发生确定性 drop，不 OOM。

---

# 79. Outbound Tests

至少：

### A. Valid response

```text commit
→ enqueue
→ send
```

### B. Silent

```text no network call
```

### C. Stale before enqueue

```text commit fails
→ no enqueue
```

### D. Send failure

```text retry <= max
```

### E. Retry success

```text eventually sent
```

### F. Retry exhausted

```text delivery_failed
```

### G. Same committed response replay

不产生重复 send attempt beyond defined retry semantics。

---

# 80. Reconnect Tests

至少：

```text disconnect
↓
reconnect
↓
new event
↓
process
```

并测试：

```text repeated disconnect
```

不创建无限任务。

---

# 81. Shutdown Tests

```text stop()
```

必须：

```text no new inbound processing
outbound worker stops
websocket closes
tasks finish/cancel cleanly
```

不能：

```text orphan asyncio task
```

---

# 82. Sandbox Safety Tests

Fake transport 发送：

```text message
```

整个流程前后必须确认：

```text character state valid
relationship rules unchanged
commitment rules unchanged
memory rules unchanged
experience rules unchanged
```

Transport layer 本身不能 bypass Sandbox。

---

# 83. Realistic end-to-end Test

至少做一个：

```text id="q8x2m1"
Fake OneBot inbound
    ↓
NormalizedMessageEvent
    ↓
ExternalWorldEvent
    ↓
Sandbox
    ↓
CognitiveContext
    ↓
Conversation LLM fake
    ↓
ConversationResponse
    ↓
Commit Guard
    ↓
Fake OneBot outbound
```

最后断言：

```text output message == expected
```

并检查：

```text world/relationship/memory/experience
```

没有非法 mutation。

---

# 84. Fake LLM

测试不得依赖真实 LLM。

使用：

```text deterministic fake provider
```

能够控制：

```text valid JSON
invalid JSON
slow response
low confidence
stale response
```

---

# 85. Fake OneBot Transport

至少支持：

```text receive(event)
send(payload)
disconnect()
reconnect()
fail_next_send()
delay_next_receive()
```

并记录：

```text sent_messages
send_attempts
```

---

# 86. Event Idempotency Regression

同一个：

```text transport_event_id
```

重复 100 次。

要求：

```text Sandbox ingestion = 1
Conversation turn = 1
```

不能：

```text 100 LLM calls
```

---

# 87. Response Deduplication Regression

同一个 inbound event：

```text one turn
one committed response
```

不能：

```text two outbound replies
```

---

# 88. Group Mention Regression

测试：

```text Group
User A
@bot
```

以及：

```text Group
User A
non-mention
```

但本阶段只验证：

```text existing policy
```

没有被 adapter 绕过。

---

# 89. Current world precedence

真实 external message：

```text “你在干嘛？”
```

CognitiveContext：

```text current world = sleep
```

LLM fake 可以引用：

```text current world
```

但：

```text Memory = past gaming
```

不能改变 current world。

---

# 90. No direct transport-to-LLM shortcut

禁止：

```text OneBot message
→ prompt
→ LLM
→ QQ
```

绕过：

```text ExternalWorldEvent
CognitiveContext
ConversationRuntime
ResponseCommit
```

Phase 13 最重要的架构测试之一就是验证这条 shortcut 不存在。

---

# 91. No duplicate external processing

事件：

```text message
```

只能：

```text ExternalWorldEvent once
```

如果 normalize 失败：

```text no Sandbox event
no LLM call
no outbound
```

而不是半处理状态。

---

# 92. Malformed transport event

例如：

```text missing user_id
missing post_type
invalid message
```

必须：

```text reject
trace
no crash
```

不能让：

```text whole runtime
```

退出。

---

# 93. Unexpected OneBot fields

允许：

```text future / unknown fields
```

忽略未知字段。

不要：

```text schema too strict
```

导致未来 OneBot minor extension 整体断掉。

---

# 94. Transport Security

OneBot token：

```text config only
```

如果 HTTP API 用到：

```text Authorization / access_token
```

必须：

```text never log token
```

WebSocket endpoint 日志：

```text sanitize secrets
```

---

# 95. Public example config

继续：

```text real token = never publish
```

example：

```text token = ""
```

---

# 96. Phase 13 不进入更高层自动行为

禁止：

```text proactive social
autonomous DM
scheduled conversation
random interruption
emotion
sticker engine
vision
voice
```

这些以后分别进入独立阶段。

---

# 97. 完成标准

必须：

```text id="m8x1v7"
ruff
ruff format
mypy
pytest
```

并保持：

```text id="q7s4p0"
1324 existing tests
全部保留
+
Phase 13 tests
```

不得：

```text skip
xfail
删测试
弱断言
```

CI：

```text success
```

Git：

```text HEAD == origin/main
working tree clean
```

---

# 98. 最终报告格式

```text id="y2m6q8"
# CatooBot v2.1 Phase 13 Report · Real External Runtime Integration & OneBot Event Gateway

Phase 13 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## Transport

OneBot adapter:
connection mode:
self_id:
reconnect:
heartbeat:
shutdown:

## Inbound

private message:
group message:
mention:
person resolution:
character resolution:
external_event mapping:

## Identity

transport_event_id:
dedupe:
self-message guard:
unknown person behavior:

## Ordering

same social lane:
cross-lane concurrency:
queue limit:
overflow behavior:

## Conversation

CognitiveContext source:
ConversationRuntime:
Response Commit Guard:
stale response:
invalid response:

## Outbound

silent:
reply:
retry:
retry limit:
duplicate prevention:

## Isolation

person isolation:
group isolation:
character isolation:
self_id isolation:

## Failure Handling

malformed event:
transport disconnect:
LLM failure:
send failure:
shutdown:

## Persistence

new database: No
new chat history: No
new queue database: No

## Regression

Phase 8:
Phase 8.1:
Phase 9:
Phase 9.1:
Phase 9.1.1:
Phase 10:
Phase 10.1:
Phase 10.2:
Phase 11:
Phase 12:
Phase 12.1:

## Architecture

Sandbox modified for transport: No
OneBot logic inside sandbox: No
Direct OneBot → LLM shortcut: No
LLM fact construction: No
new Memory system: No
new Relationship system: No
new Experience system: No
new Planner: No
new Agent: No
new Emotion system: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Final

Phase 13 complete: Yes / No
Phase 14 entered: No
```

---

# 99. Phase 13 的真正完成定义

最终必须能够真实运行：

```text id="s4k6n1"
NapCat / OneBot
        ↓
QQ message
        ↓
dedupe
        ↓
person / character resolve
        ↓
ExternalWorldEvent
        ↓
Sandbox
        ↓
CognitiveContext
        ↓
ConversationRuntime
        ↓
Response Commit Guard
        ↓
Outbound Queue
        ↓
OneBot send_msg
        ↓
QQ response
```

同时：

```text id="e7m4t2"
重复 QQ event
→ 不重复回复

自己发出的 QQ message
→ 不自言自语

网络断线
→ Sandbox 不被污染

LLM 很慢
→ 同一 social space 不乱序

两个群同时聊天
→ 互不阻塞

Response 过期
→ 不发送

OneBot API 失败
→ 有界 retry

Runtime shutdown
→ 不留下 orphan tasks
```

---

# 100. 最终边界

Phase 13 的任务不是让“罐头”变得更聪明。

而是第一次证明：

> **前面已经完成的 Phase 1～12.1，不只是测试环境中的漂亮架构，而是能够承受真实 QQ 外部事件流，并安全地形成真实回复。**

完成后停止。

不要自动进入 Phase 14。