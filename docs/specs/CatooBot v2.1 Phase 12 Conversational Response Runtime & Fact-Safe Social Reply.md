# CatooBot v2.1 Phase 12 · Conversational Response Runtime & Fact-Safe Social Reply

## 0. 基线

当前最终基线：

```text id="b2tq3m"
HEAD: 16dee3235646d40a85bb72250491e6712b5c740d
Phase: 11
Tests: 1291 passed
CI: success
```

正式进入：

# Phase 12 · Conversational Response Runtime & Fact-Safe Social Reply

本阶段第一次把：

```text
External conversation
    ↓
Person
    ↓
CognitiveContext
    ↓
Conversation response
```

连接成一条真正可运行的“社交回复链”。

但本阶段仍然不是：

```text
Conversation History System
Agent System
Planner System
Personality Evolution System
Emotion System
```

---

# 1. Phase 12 的核心目标

到 Phase 11，系统已经知道：

```text
谁在和角色说话
↓
当前关系是什么
↓
有哪些未完成的社会义务
↓
最近共同经历过什么
↓
有哪些长期共同记忆
↓
当前世界正在发生什么
```

现在必须解决：

> **角色收到一条消息之后，如何利用这些认知产生一条安全、连续、可验证边界的自然语言回复。**

最终形成：

```text id="0o3yfq"
External Message
      ↓
PersonIdentity
      ↓
verified external interaction
      ↓
CognitiveContext
      ↓
Conversation Response Decision
      ↓
LLM response proposal
      ↓
Response Validator
      ↓
ConversationResponse
      ↓
调用方发送消息
```

注意：

**发送出去的文字本身不是 Sandbox 世界事实。**

---

# 2. 本阶段最重要的架构边界

必须明确区分：

```text id="g4vr73"
Message
= 外部世界传入的事实

Response
= 角色准备输出的语言

Sandbox Fact
= 已验证的世界状态

Memory
= 已发生且值得长期保存的过去事实
```

因此：

```text id="gb7qdm"
LLM 输出一句话
```

绝不能直接造成：

```text id="3c8b3x"
new Memory
new Commitment
new Goal
new Experience
Relationship change
World mutation
```

---

# 3. 不创建第二套认知系统

禁止：

```text id="0bx9cd"
ConversationContextManager
ChatContextManager
SocialResponseContext
ConversationMemoryManager
```

等与 `CognitiveContext` 平行的新系统。

本阶段：

```text id="v8tq5d"
CognitiveContext
```

仍然是唯一的认知投影来源。

---

# 4. 不创建聊天数据库

严格禁止：

```text id="f9m40c"
conversation_messages
chat_history
chat_messages
conversation_sessions
message_embeddings
chat_memory
```

不要将普通聊天消息持久化成新数据库对象。

Phase 12 的 turn state 只存在当前调用生命周期。

---

# 5. 新增：ConversationTurn

允许新增一个轻量 Pydantic 数据模型：

```python id="0wlb5x"
ConversationTurn
```

建议至少：

```text id="ax3i2r"
turn_id
character_id
source
external_actor_id
person_id
social_space_id
group_id
message_text
received_at
event_id
world_revision
cognitive_revision
```

其中：

```text id="6l2n1y"
person_id
```

必须来自：

```text external identity
    ↓
PersonIdentity
```

不能由：

```text display_name
message text
memory
relationship
```

猜测。

---

# 6. ConversationTurn 是当前调用对象，不是 Memory

ConversationTurn：

```text id="g90l1v"
不落库
不进入 Memory
不进入 Experience
不改变 Relationship
```

除非后续已经有合法的：

```text id="d8n4re"
SocialInteractionFact
Experience
Memory
```

流程。

---

# 7. ConversationResponse

新增：

```python id="x6dk81"
ConversationResponse
```

建议至少：

```text id="f0sw7l"
turn_id
character_id
person_id
text
mode
memory_refs
experience_refs
action_candidate_id
source
world_revision
cognitive_revision
```

其中：

```text mode
```

可以是：

```text id="7hm6u3"
reply
acknowledge
defer
silent
```

不要设计十几个 response mode。

---

# 8. Response 与 Action 必须分开

注意：

```text id="3kb2w9"
“回复什么”
```

和：

```text id="4q42qk"
“要不要实际执行某个 Action”
```

不是同一件事。

Phase 6 已经有：

```text id="7l8u5k"
DecisionCoordinator
DecisionCandidate
DecisionValidator
DecisionOutcome
```

本阶段不得重新创建行动决策系统。

如果当前消息确实触发行动决策：

```text id="1sl5tf"
Conversation Runtime
    ↓
existing DecisionCoordinator
    ↓
DecisionOutcome
```

然后 ConversationResponse 只负责表达结果。

---

# 9. 不要把 DecisionCoordinator 改成 Chatbot

禁止把：

```text id="v2cmka"
DecisionCoordinator
```

改造成：

```text
conversation manager
response generator
chat agent
```

它继续只负责：

```text
candidate selection
```

其现有约束必须保持：

```text id="9eu2io"
LLM may propose
Sandbox decides
```

现有 `DecisionValidator` 继续重新读取：

```text id="jqbp4o"
world_revision
cognitive_revision
```

验证 proposal 是否仍然有效。

---

# 10. Phase 12 新增的职责：Response Runtime

建议新增轻量：

```text id="8x7k3h"
ConversationRuntime
```

或者直接在：

```text app/sandbox/runtime.py
```

增加 thin orchestration。

但：

**不要新增一个巨型 manager。**

职责只有：

```text id="zsy3mx"
receive turn
↓
resolve person
↓
build CognitiveContext
↓
determine response mode
↓
build LLM response request
↓
parse response
↓
validate
↓
return ConversationResponse
```

---

# 11. Response Runtime 不能直接修改 Sandbox

必须保持：

```text id="opv8wd"
ConversationRuntime
    = orchestration/read-only/response
```

不能直接：

```python id="qg0s7f"
runtime.relationships.change(...)
runtime.commitments.create(...)
runtime.memory.save(...)
runtime.goal_manager.create(...)
```

---

# 12. 消息进入后的正确顺序

对于 QQ / ExternalWorldEvent：

```text id="3j9w5b"
ExternalWorldEvent
    ↓
External Influence
    ↓
verified social interaction update
    ↓
same-event social task settled
    ↓
CognitiveContext
    ↓
ConversationResponse
```

如果这一条消息本身造成：

```text id="zj4ylx"
relationship / cognitive_revision
```

则必须让 Response Context 看见最新的 revision。

不能：

```text id="c9o0ra"
旧 cognitive context
↓
LLM
↓
relationship meanwhile changed
```

---

# 13. 外部消息不是命令

继续保持 Phase 3 / 6 的原则：

```text id="s1csc3"
用户说：
“去玩 Minecraft”
```

不能直接：

```text
execute action
```

而必须：

```text id="9bd5xx"
External fact
↓
Decision gate
↓
candidate actions
↓
DecisionCoordinator
↓
Validator
↓
Action
```

如果只是聊天表达：

```text id="y4m6i5"
“你今天干嘛呢”
```

则可以只生成语言回复。

---

# 14. Response Generation 的输入

LLM Response Generator 至少应该看到：

```text id="1ty62w"
当前消息
当前 person
当前 Social Situation
当前 World
当前 Continuity
相关 Memories
相关 Experiences
```

推荐：

```json id="8xwv5t"
{
  "message": "...",
  "person": {...},
  "social_situation": {...},
  "current_world": {...},
  "continuity": {...},
  "relevant_memories": [...],
  "recent_experiences": [...]
}
```

不要把整个 Sandbox dump 给模型。

---

# 15. CognitiveContext 必须是唯一上下文来源

禁止 Response Runtime 自己：

```text id="qbgh4x"
query memories
query relationships
query commitments
query experiences
```

再拼第二套 context。

必须：

```text id="gd5h2p"
ConversationRuntime
    ↓
SandboxRuntime.cognitive_context()
```

所有上下文统一从：

```text id="4pj0l3"
CognitiveContext
```

得到。

---

# 16. 当前世界必须优先

Prompt 必须明确：

```text id="jlc4kn"
current_world = authoritative current fact
memory = historical reference
experience = historical episode
```

例如：

```text id="7yz4qj"
旧 memory:
和 A 一起玩 Minecraft

当前 world:
角色正在睡觉
```

回复模型不能因为 Memory 存在而认为：

```text
现在正在 Minecraft
```

---

# 17. Memory 的使用必须是“可引用事实”

LLM 可以看到：

```text id="4l7ffk"
memory_id
text
episode_key
activity
```

但不得获得一个“自由生成 Memory”的工具。

Response 只能：

```text id="7h4o1c"
引用已有 memory
```

不能：

```text id="7j9j87"
声明新记忆
```

---

# 18. Memory Reference

建议 ResponseProposal 使用：

```python id="b3v5kr"
memory_refs: list[str]
experience_refs: list[str]
```

要求：

```text id="tlh4hc"
每个 ref 必须存在于本轮 CognitiveContext
```

否则：

```text id="o5tnn1"
reject / strip
```

不能让模型引用：

```text id="7j9ypx"
context 外的 memory_id
```

---

# 19. Experience Reference

同样：

```text id="9b3j74"
experience_refs
```

只能引用：

```text
CognitiveContext.recent_shared_experiences
```

中的已有：

```text
experience_id
episode_key
```

不能自己构造：

```text experience_id
```

---

# 20. 为什么需要 References

不是为了让用户看到 ID。

而是为了建立：

```text id="uh2h8n"
Response
    ↓
它使用了哪些已知事实
```

以后可以审计：

```text
“这句话中的过去经历来自哪里？”
```

而不是：

```text
不知道模型为什么这么说。
```

---

# 21. Strict ResponseProposal

LLM 必须只输出 JSON。

建议：

```json id="ce7s9e"
{
  "mode": "reply",
  "text": "...",
  "memory_refs": [],
  "experience_refs": [],
  "action_candidate_id": "",
  "confidence": 0.0
}
```

不要允许：

```text id="8kmc2e"
自由格式
```

---

# 22. Response mode 规则

只允许：

```text id="rqj2j5"
reply
acknowledge
defer
silent
```

含义：

```text reply
= 正常回复

acknowledge
= 简短回应

defer
= 当前不适合深入回复

silent
= 不发送消息
```

不要在 Phase 12 添加：

```text
typing
emotion_display
sticker_mode
voice_mode
roleplay_mode
```

这些以后再做。

---

# 23. Action Candidate Reference

如果模型需要表达：

```text
“我要去做某件事”
```

绝不能返回：

```json id="uy4y0k"
{"action_id":"make_random_action"}
```

必须：

```text id="1z6fvi"
action_candidate_id
```

而这个 candidate：

```text id="xq2f0w"
必须由现有 DecisionCoordinator / Sandbox world-derived candidates 提供
```

不能由 LLM 创建。

---

# 24. Action 不允许从自然语言自动执行

模型输出：

```text
“我去玩 Minecraft 了”
```

不代表：

```text
action started
```

只有：

```text id="4t1pga"
DecisionCoordinator
→ DecisionValidator
→ ActionSystem
```

真正执行之后：

```text
ACTION_REQUESTED
ACTION_STARTED
```

才成立。

---

# 25. ResponseValidator

新增：

```text id="c9h7j2"
ConversationResponseValidator
```

或者作为现有 runtime 的内部验证方法。

职责：

```text id="8v0lnt"
turn_id match
character_id match
person_id match
memory refs valid
experience refs valid
action candidate valid
world_revision unchanged
cognitive_revision unchanged
```

如果 revision 改变：

```text id="gq2pvi"
response proposal stale
→ 不直接发送
```

---

# 26. Stale Response

这是 Phase 12 最重要的并发保护之一。

场景：

```text id="j2q2l8"
T0:
build CognitiveContext
world_revision = 10
cognitive_revision = 20

T1:
LLM thinking

T2:
another event arrives
cognitive_revision = 21

T3:
LLM returns
```

则：

```text id="y8y4m4"
response = stale
```

不能直接发送。

建议：

```text id="j5pb8r"
discard
```

不要自动重新调用 LLM 无限 retry。

最多由上层重新开始一次新的 conversation turn。

---

# 27. World Revision 与 Cognitive Revision 都必须检查

不能只检查：

```text id="j89vkm"
world_revision
```

也必须：

```text id="w5b0db"
cognitive_revision
```

因为：

```text Relationship
Commitment
Social Situation
```

都可能在：

```text
world_revision 不变
```

时改变。

---

# 28. No automatic memory creation

一次普通聊天：

```text id="11kz4e"
message
→ response
```

不会自动：

```text id="hjfn6u"
Memory
```

即：

```text id="jgv1sq"
message text ≠ memory fact
```

只有已经存在的：

```text
Experience → MemoryCandidate
```

系统才能创建 Memory。

---

# 29. No automatic relationship mutation

Response generation 不修改：

```text id="z4k15h"
trust
familiarity
closeness
social comfort
```

Relationship 仍然：

```text id="0c1dqo"
verified SocialInteractionFact
→ RelationshipUpdateEngine
```

唯一写入口。

---

# 30. No automatic Commitment creation

LLM 生成：

```text id="jdj5z8"
“下次一起玩啊”
```

不能自动创建：

```text
Commitment
```

只有：

```text id="82m9f5"
deterministic CommitmentDetector
```

检测到明确社会承诺事实时才能创建。

---

# 31. No automatic Goal creation

同样：

```text id="4x6k9a"
聊天中出现一个“要不要一起玩”
```

不会直接：

```text
Goal
```

Goal 仍只能：

```text id="x7q3cz"
CommitmentGoalBridge
```

创建。

---

# 32. Generated text is not a World Fact

这是本阶段必须写入代码注释 / contract 的规则：

```text
id="6a1jdr"
ConversationResponse.text
= language output only
```

永远不能当作：

```text
Sandbox Event
Experience
Memory
Relationship fact
Commitment fact
```

除非未来存在专门的 verified post-processing。

Phase 12 不实现这个 post-processing。

---

# 33. Response Event

允许发布：

```text id="5gr7cd"
CONVERSATION_RESPONSE_PROPOSED
CONVERSATION_RESPONSE_EMITTED
```

但如果现有 `SandboxEventType` 已经有更合适的名称，优先复用。

这些事件：

```text id="89kmc7"
是 runtime trace
```

而不是：

```text world mutation
```

不能增加：

```text world_revision
cognitive_revision
```

---

# 34. Response Event 的内容

可以记录：

```text id="7dc5v2"
turn_id
person_id
mode
source
memory_refs
experience_refs
action_candidate_id
```

但不要保存：

```text id="f7tq2r"
完整聊天历史数据库
```

如果已有 EventBus 记录 message content，请遵守现有隐私/长度限制，不新增长期聊天存储。

---

# 35. Group Chat

本阶段不要重新设计 Group Social System。

复用现有：

```text id="m6edb1"
ExternalWorldEvent
social_space_id
group_id
actor_id
```

如果系统已有：

```text id="8a9yp6"
group non-mention influence
```

继续使用。

Phase 12 只负责：

```text id="j4jhf5"
当上层已经决定这条 turn 需要 conversational response
```

再生成 Response。

不要让 Response Runtime 自己覆盖 Phase 3 的 influence policy。

---

# 36. “是否回复”不是 LLM 自由决定世界行为

Response Runtime 应先获得：

```text id="n7jn3h"
response_allowed / response_mode
```

来源必须是：

```text deterministic runtime policy
```

或者现有 external influence。

不要让 LLM 自己发明：

```text “我觉得应该回复”
```

作为一个新的世界权限系统。

---

# 37. Private Chat

私聊可以默认：

```text id="x5h0tp"
response candidate available
```

但仍然允许：

```text silent
```

前提是：

```text response policy
```

允许。

不要强制所有输入都必须回复。

---

# 38. Response LLM Prompt

Prompt 必须明确：

```text id="mlnqib"
你正在代表角色生成当前这一轮的聊天回复。

你只能使用提供的：
- 当前世界
- 当前社会上下文
- 已存在记忆
- 已存在共同经历

不要创造过去发生过的事件。
不要创造不存在的约定。
不要创造不存在的人。
不要把 Memory 当成当前 World。
不要宣称任何尚未通过 Sandbox 验证的行动已经发生。

如果不知道，就不要编造。
```

---

# 39. Response Prompt 中的角色身份

角色名称可以来自：

```text id="a8o2x4"
CharacterDefinition.identity.name
```

不得硬编码：

```text
罐头
空凛
小喵
```

等具体角色内容。

---

# 40. Response Prompt 中不放完整 Character Bible

不要把完整：

```text id="zkq2ga"
character_bible.md
```

每一轮重新塞给 LLM。

角色的行为边界应继续来自：

```text CharacterDefinition
CognitiveContext
```

---

# 41. Response Prompt 不得包含未知信息

所有：

```text person
relationship
commitment
memory
experience
world
```

必须来自当前 runtime。

不要从：

```text
聊天历史
```

自行重新解析长期事实。

---

# 42. No Conversation Summary Agent

禁止：

```text id="qg5d19"
chat summarizer agent
conversation memory agent
social narrator
```

本阶段只处理：

```text current turn
```

---

# 43. No Emotion Engine

继续禁止：

```text id="q0k1g3"
mood engine
emotion model
attachment model
jealousy
longing
```

---

# 44. No Personality Evolution

继续禁止：

```text id="5yv7o3"
人格学习
人格参数修改
trait drift
```

---

# 45. No Planner / Agent

继续：

```text id="1l7i3y"
Planner: No
Agent: No
```

Action 仍由现有：

```text DecisionCoordinator
GoalManager
ActionSystem
```

处理。

---

# 46. No new Memory system

继续复用：

```text id="uxn2zk"
MemoryCandidateBuilder
SandboxMemoryStore
MemoryRepository
```

---

# 47. No new Relationship system

继续：

```text id="2w0pbh"
SocialInteractionFact
→ RelationshipUpdateEngine
```

---

# 48. No new Experience system

继续：

```text id="9c1o2d"
ExperienceBuilder
canonical episode_key
```

---

# 49. Character Isolation

测试：

```text id="c8oq0p"
Character A + Person X
Character B + Person X
```

分别 conversation turn：

```text id="x3nq0y"
memory
relationship
commitment
experience
```

不能串。

---

# 50. Person Isolation

当前聊天对象：

```text id="h20v1l"
Person A
```

Response Context：

```text id="5l5a9n"
A relationship
A commitments
A shared memory
A experiences
```

不能错误带入：

```text B commitments
B memory
B relationship
```

---

# 51. Response Reference Isolation

尤其测试：

```text id="0o7cfd"
A turn
```

如果模型返回：

```json
{
  "memory_refs":["memory_B"]
}
```

必须 reject/strip。

不能发送一个无法证明来源的 reference。

---

# 52. Invalid Action Candidate

如果模型返回：

```json
{
  "action_candidate_id":"action:not-in-request"
}
```

必须：

```text id="p1hk4x"
reject action portion
```

不能执行。

---

# 53. LLM unavailable

AI engine unavailable：

```text id="blj9r2"
ConversationResponse 不得抛异常
```

建议：

```text id="vpsg3p"
返回 mode = silent
source = fallback
reason = llm_unavailable
```

除非已有 deterministic reply contract 可以安全复用。

不要硬编码：

```text “我现在有点忙”
“哈哈”
“嗯嗯”
```

作为 fallback 人格。

---

# 54. Invalid JSON

模型输出非法 JSON：

```text id="2s72jy"
→ no response
→ source=fallback
→ no mutation
```

与 DecisionCoordinator 的 strict JSON 原则一致。

---

# 55. Low confidence

模型：

```json id="n8glb7"
confidence < configured threshold
```

则：

```text id="7p4mve"
不要直接发送
```

采用 silent / fallback。

不要降低现有 threshold。

---

# 56. Empty Response

如果：

```text id="a4t0xm"
mode=reply
text=""
```

必须：

```text id="x4jwvt"
invalid
```

不能发送空消息。

---

# 57. Length Boundary

必须设置一个：

```text id="r2ak6p"
max_response_chars
```

优先复用现有 AI config。

如果没有：

```text id="k6t1a0"
新增一个明确配置
```

不要在多个模块硬编码长度。

超长模型输出：

```text id="f8r6z3"
reject / deterministic truncate
```

推荐 reject + fallback，而不是直接截断造成语义残缺。

---

# 58. Current Revision Stamp

ConversationResponse 必须保存：

```text id="s31gqy"
world_revision
cognitive_revision
```

发送前再次验证。

---

# 59. Emit 与 Send 的边界

本阶段：

```text id="hbc5z8"
ConversationRuntime
```

只负责：

```text produce ConversationResponse
```

不直接依赖：

```text NapCat API
QQ HTTP API
OneBot sender
```

不要把通讯平台代码塞进 Sandbox。

上层 adapter 才负责：

```text ConversationResponse
→ QQ message
```

---

# 60. External Adapter 保持独立

现有：

```text id="o7f4t3"
app/sandbox/external.py
app/sandbox/external_adapters.py
```

只负责：

```text external input
→ ExternalWorldEvent
```

Response：

```text ConversationResponse
→ adapter
```

反向方向必须清晰。

---

# 61. 推荐目录

优先：

```text
app/sandbox/conversation.py
```

但如果审计后发现现有结构更适合：

```text app/sandbox/runtime.py
```

允许把 orchestration 留在那里。

不要创建：

```text app/conversation2/
app/social_runtime/
```

等重复体系。

---

# 62. Tests

新增：

```text id="9g5v01"
tests/test_sandbox_conversation.py
```

至少覆盖：

### A. Basic private conversation

```text
message
→ resolve person
→ CognitiveContext
→ valid response
```

---

### B. Person-aware response context

```text
Person A
→ only A social situation
```

---

### C. Memory reference

模型引用已有：

```text memory_id
```

→ accepted。

---

### D. Invalid memory reference

模型引用不存在：

```text memory_B
```

→ rejected / stripped。

---

### E. Experience reference

已有 experience：

```text id="z8p0l5"
episode_key
```

→ accepted。

---

### F. Invalid experience reference

不存在：

```text id="x5q1k3"
```

→ rejected。

---

### G. Action candidate

模型返回合法 candidate：

```text id="w0h8r4"
```

→ 进入现有 Decision / Action validation。

---

### H. Invalid action candidate

模型返回：

```text id="9i0x8p"
```

→ 不执行。

---

### I. World revision stale

LLM thinking期间 world changed：

```text id="n3f5m7"
→ response rejected
```

---

### J. Cognitive revision stale

LLM thinking期间 Relationship/Commitment changed：

```text id="h0y4r8"
→ response rejected
```

---

### K. No memory mutation

普通 conversation turn：

```text id="3f9k1h"
memory count unchanged
```

---

### L. No relationship mutation

普通 response：

```text id="g7m5q0"
relationship unchanged
cognitive_revision unchanged
```

---

### M. No commitment mutation

普通 response：

```text id="l9v2ax"
commitment state unchanged
```

---

### N. No experience mutation

普通 response：

```text id="r5p4m2"
experience count unchanged
```

---

### O. LLM unavailable

```text id="d2k7q1"
→ silent / fallback
→ no exception
→ no mutation
```

---

### P. Invalid JSON

```text id="a1q6z8"
→ silent / fallback
```

---

### Q. Low confidence

```text id="u7x2c5"
→ no direct send
```

---

### R. Empty text

```text id="m4d8p1"
→ reject
```

---

### S. Character isolation

```text id="v6n3r0"
same person
two characters
→ separate context
```

---

### T. Person isolation

```text id="c5z1q4"
Person A
Person B
→ social context does not cross
```

---

### U. Current World precedence

```text id="y2m7x9"
Memory says gaming
World says sleeping
→ response context sees sleep
```

---

### V. Read-only response generation

生成 response 前后：

```text id="f1w8k3"
world_revision unchanged
cognitive_revision unchanged
relationships unchanged
commitments unchanged
memories unchanged
experiences unchanged
```

---

### W. Determinism of non-LLM layer

相同：

```text id="p9r6m4"
turn
person
world
cognitive state
```

重复 build：

```text id="g3h7t2"
same context
same refs
same candidate set
```

---

# 63. Group chat tests

至少覆盖：

```text id="r4x7v1"
group message
```

验证：

```text social_space_id
group_id
actor_id
person_id
```

能正确向下传递。

但本阶段不要重新实现“是否响应群聊”的完整策略。

验证：

```text id="l0n5m8"
ResponseRuntime
```

不会绕过已有 External Influence policy。

---

# 64. Existing invitation flow regression

Phase 9 当前：

```text id="g9h4r2"
QQ invitation
→ ExternalWorldEvent
→ Relationship
→ DecisionCoordinator
→ Action
```

必须全部保持。

Phase 12 Response Runtime 不得导致：

```text id="6j2p8v"
invitation
→ duplicate decision
```

也不得：

```text
decision already made
→ second LLM call for same action
```

如果邀请流程已经有语言回复所需的信息：

```text id="n8k3q6"
可以把 DecisionOutcome
```

传给 ResponseRuntime。

不要重新跑 Action decision。

---

# 65. Decision + Response 的正确关系

例如：

```text id="1g8r5k"
A:
“晚上一起玩？”
```

流程可能是：

```text
ExternalWorldEvent
        ↓
Influence
        ↓
existing DecisionCoordinator
        ↓
accepted action
        ↓
ConversationRuntime
        ↓
“好呀，晚上一起玩”
```

而不是：

```text id="s5m7q2"
Conversation LLM
→ “好呀，我去玩了”
→ directly mutate Action
```

---

# 66. Conversation response 不制造“已经做了”的假事实

例如模型输出：

```text
“我已经把那个房子建好了。”
```

如果 Sandbox 没有：

```text id="c8v2p4"
对应 verified completion
```

Response 仍然是语言，但：

```text
绝不能因为这句话而写入 Experience / Memory。
```

本阶段甚至不尝试做“文本事实抽取”。

---

# 67. No post-hoc fact extraction

禁止本阶段新增：

```text id="s3g5j7"
ResponseParser
FactExtractor
MemoryFromResponse
PromiseFromResponse
```

Phase 12 只负责输出。

---

# 68. Privacy / Logging

Response proposal 的 debug event：

```text id="m2v8q5"
不要记录完整 prompt
不要持久化完整 message history
```

如果现有日志需要：

```text id="y7n1d4"
仅记录：
turn_id
mode
source
refs
action_candidate_id
reason
```

文本可遵循现有短日志策略。

---

# 69. Character Bible

禁止修改真实：

```text id="c6n2x8"
character_bible.md
```

核心代码继续：

```text character-agnostic
```

不能出现：

```python
if name == "罐头":
```

---

# 70. Git 安全

继续：

```text id="q4b7x0"
E:\WorkSpace ZCode\CatooBot
→
E:\WorkSpace ZCode\CatooBot_github
→
commit
→
push
```

禁止：

```text force push
history rewrite
filter-repo
BFG
```

真实 Bible 继续不进入 public mirror。

---

# 71. Phase 12 不修改这些已封板语义

必须保持：

```text id="2k9m6z"
Phase 8:
Relationship mutation path

Phase 9:
Commitment lifecycle

Phase 9.1:
Fulfillment → Goal completion

Phase 9.1.1:
Shared activity matching

Phase 10:
Shared Experience

Phase 10.1:
Persistent episode identity

Phase 10.2:
Memory episode identity

Phase 11:
Social Situation / CognitiveContext
```

---

# 72. No schema migration unless absolutely necessary

本阶段优先：

```text no migration
```

ConversationTurn / ConversationResponse：

```text runtime only
```

如果审计发现现有 event schema 无法表达必要的：

```text response trace
```

优先扩展已有 Event payload。

不要新增 Conversation table。

---

# 73. Completion criteria

必须：

```text id="5d2x9p"
ruff
ruff format
mypy
pytest
```

要求：

```text id="w8q4n7"
1291 existing tests
全部保留并通过

+
Phase 12 tests
```

不得：

```text skip
xfail
删除旧测试
弱化断言
```

CI：

```text success
```

Git：

```text HEAD == origin/main
working tree clean
```

---

# 74. Final report

最终报告必须：

```text id="u0k8m5"
# CatooBot v2.1 Phase 12 Report · Conversational Response Runtime & Fact-Safe Social Reply

Phase 12 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## Conversation Turn

turn_id:
character_id:
external_actor_id:
person_id:
group_id:
social_space_id:
message persistence: No

## Cognitive Context

person:
relationship:
open commitments:
shared experiences:
shared memories:
current world:
continuity:

## Response

response mode:
source:
text validation:
memory refs:
experience refs:
action candidate:

## Validation

world_revision checked:
cognitive_revision checked:
stale response behavior:
invalid memory ref:
invalid experience ref:
invalid action candidate:

## Read-only Guarantee

relationship changed: No
commitment changed: No
goal changed: No
memory changed: No
experience changed: No
world state changed: No
revision changed by response generation: No

## Failure Handling

LLM unavailable:
invalid JSON:
low confidence:
empty response:
stale response:

## Isolation

person isolation:
character isolation:
group context isolation:

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

## Architecture

new Conversation DB: No
new Memory system: No
new Relationship system: No
new Experience system: No
new Planner: No
new Agent: No
new Emotion system: No
new Personality Evolution: No
LLM used for fact construction: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Final

Phase 12 complete: Yes / No
Phase 13 entered: No
```

---

# 75. Phase 12 的真正完成定义

完成后，系统应该第一次具备：

```text id="8w3l1p"
QQ Message
    ↓
ExternalWorldEvent
    ↓
PersonIdentity
    ↓
Verified social interaction
    ↓
CognitiveContext
    ↓
Social Situation
    ↓
Response Proposal
    ↓
Response Validator
    ↓
ConversationResponse
    ↓
QQ adapter
```

而且必须保持：

```text
ConversationResponse ≠ World Fact
ConversationResponse ≠ Memory
ConversationResponse ≠ Relationship Mutation
ConversationResponse ≠ Commitment
ConversationResponse ≠ Goal
```

真正实现的是：

> **角色终于可以利用已经建立起来的“关系、承诺、共同经历、记忆、当前世界”来形成一轮连续的自然语言社交回应；但这条语言输出链不能反过来污染世界模型。**

完成后停止。

不要自动进入 Phase 13。