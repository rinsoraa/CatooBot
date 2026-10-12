# CatooBot v2.1 Phase 16 · Live Social Influence & Autonomous Interaction Continuity

## 0. 基线

当前最终稳定基线：

```text
HEAD: ebe26d3a29c7be3a168fad917d6b8ccd3cd0511f
Phase: 15
Tests: 1407 passed
CI: success
```

正式进入：

# Phase 16 · Live Social Influence & Autonomous Interaction Continuity

本阶段不是：

```text
Phase 15.1
```

也不是：

```text
Emotion
Personality Evolution
Proactive Social Agent
Conversation History DB
```

---

# 1. Phase 16 核心目标

目前系统已经能够：

```text
Autonomous Life
    ↓
Need → Goal → Action
```

以及：

```text
QQ Message
    ↓
OneBot
    ↓
ExternalWorldEvent
    ↓
CognitiveContext
    ↓
ConversationResponse
```

现在必须把二者连接起来：

```text
Autonomous Action
       ↕
External Social Event
       ↕
Social Interaction
       ↕
Conversation
       ↕
Relationship / Commitment
       ↕
Autonomous Action Resume
```

最终实现：

> **角色在真实生活的时候，社会互动可以自然地进入她的生活；处理完互动后，角色能够继续原来的生活，而不是被 QQ 系统和 Sandbox 系统分裂成两个世界。**

---

# 2. 核心示例

例如：

```text
20:00
角色正在 gaming
ActionInstance = A1
```

20:15：

```text
QQ:
“在干嘛？”
```

系统：

```text
OneBot
↓
ExternalWorldEvent
↓
ExternalInfluenceEvaluator
↓
OBSERVE / WAKE / INTERRUPT
```

如果：

```text OBSERVE
```

则：

```text A1 继续
ConversationResponse 正常生成
```

如果：

```text WAKE
```

则：

```text A1 不需要取消
Sandbox 立即重新评估一次世界
ConversationResponse 使用最新 Context
```

如果：

```text INTERRUPT
```

则：

```text A1
↓
InterruptedActionContext
↓
处理外部社会事件
↓
Conversation / SocialInteraction
↓
事件结束
↓
恢复 A1
```

最终：

```text A1 仍然是同一个 ActionInstance
```

而不是：

```text A1 cancelled
A2 new gaming
```

---

# 3. 不创建新的 Social System

禁止：

```text
SocialRuntime
SocialInteractionManager
SocialAgent
OnlinePresenceManager
ConversationLifeManager
```

已有：

```text ExternalInfluenceEvaluator
InterruptEvaluator
ConversationRuntime
RelationshipUpdateEngine
CommitmentManager
SandboxRuntime
```

继续作为唯一系统。

---

# 4. 不创建第二个 Action / Interrupt System

Phase 3 / 7 已经拥有：

```text InterruptedActionContext
InterruptEvaluator
resume semantics
```

本阶段必须先审计现有实现。

如果已经满足需求：

```text 复用
```

不要重写。

---

# 5. ExternalInfluenceEvaluator 是唯一社会事件影响入口

所有真实外部消息：

```text QQ
Web
system
```

最终：

```text ExternalWorldEvent
↓
ExternalInfluenceEvaluator
```

只有这里可以决定：

```text NO_EFFECT
OBSERVE
WAKE
INTERRUPT
REJECT
```

ConversationRuntime：

**不能自行决定 interrupt autonomous action。**

---

# 6. ConversationResponse 不能改变 Influence Decision

LLM 回复：

```text “我马上回你”
```

不能导致：

```text INTERRUPT
```

也不能：

```text WAKE
```

Influence 只能由：

```text ExternalWorldEvent
+
ExternalInfluenceEvaluator
```

确定。

---

# 7. 四种 Influence 行为必须严格验证

## NO_EFFECT

外部事件：

```text
```

不值得改变当前行为。

要求：

```text current_action unchanged
no interrupt
no wake
no duplicate decision
```

---

## OBSERVE

角色“知道发生了这件事”，但继续当前生活。

要求：

```text current_action unchanged
Conversation may happen
No forced autonomous replan
```

---

## WAKE

当前事件值得立即重新检查世界。

要求：

```text current_action remains unless existing decision says otherwise
runtime performs one ordered wakeup
```

不能：

```text every message → immediate action restart
```

---

## INTERRUPT

事件具有足够高的外部影响。

要求：

```text current ActionInstance
↓
pause/interruption
↓
preserve exact identity
↓
process external social event
↓
resume same ActionInstance if legal
```

---

# 8. InterruptedActionContext 必须是“暂停”，不是复制

必须保持：

```text original_action_instance_id
goal_id
goal_step_id
progress
started_at
remaining_duration
interrupt_reason
interrupt_event_id
```

至少其中已有语义必须完整保留。

禁止：

```text original action
↓
cancel
↓
new action
```

伪装成 resume。

---

# 9. ActionInstance Identity

假设：

```text A1 = action_inst_123
```

被打断。

恢复后必须：

```text current_action.id == action_inst_123
```

不能：

```text action_inst_456
```

---

# 10. Interrupted Action 状态

优先复用现有 `ActionStatus` / interruption semantics。

不要新建：

```text INTERRUPTED_V2
PAUSED_V2
SOCIAL_PAUSED
```

除非当前状态模型确实无法表示。

---

# 11. Resume 条件

Action interrupt 后：

```text resume
```

不能无条件。

必须再次经过：

```text WorldRuleEngine
requirements
inventory
objects
location
ActionDefinition
```

验证。

如果原 action 已经：

```text no longer legal
```

则：

```text do not resume
```

进入已有合法决策流程。

---

# 12. Resume 不能偷偷执行新 Decision

如果：

```text A1 interrupted
A1 still legal
```

则：

```text resume A1
```

不应调用：

```text LLM
```

也不应该创建：

```text new Goal
```

---

# 13. Resume 与 Goal

如果：

```text A1 belongs to Goal G1
```

恢复后：

```text same G1
same GoalStep
same ActionInstance
```

不能创建：

```text G2
```

除非原有 Goal 已经确实 terminal。

---

# 14. Interrupt 对 Goal 的语义

普通：

```text temporary social interrupt
```

不能：

```text Goal cancelled
```

也不能：

```text Goal failed
```

除非现有规则明确将该 Action 判定为不可恢复。

默认：

```text interrupt ≠ failure
```

---

# 15. Interrupt 对 Commitment 的语义

如果 Action 是：

```text fulfillment of Commitment C1
```

被社交事件暂时打断：

```text C1 remains open / in_progress
```

不能：

```text auto-cancel
```

也不能：

```text auto-reschedule
```

除非存在真实 verified reschedule fact。

---

# 16. Social Event Processing 顺序

对于：

```text INTERRUPT
```

必须：

```text external event
↓
Influence
↓
interrupt current action
↓
persist interrupted state if needed
↓
process SocialInteractionFact
↓
Relationship / Commitment updates
↓
ConversationContext
↓
ConversationResponse
↓
resume / re-evaluate
```

不要：

```text LLM response
↓
interrupt action
```

---

# 17. World Lock

继续复用：

```text SandboxRuntime._world_lock
```

所有：

```text Tick
Wakeup
External event
Interrupt
Resume
```

必须在同一世界序列化边界中执行。

---

# 18. No half-interrupted state

绝对不允许：

```text current_action = None
```

但：

```text _interrupted = None
```

同时成立。

也不允许：

```text _interrupted exists
```

但：

```text current_action still active
```

导致两个活动实例同时存在。

---

# 19. Critical invariant

任何时间：

```text active current_action <= 1
```

且：

```text current_action
+
_interrupted
```

最多代表：

```text 1 个真实行为实例
```

而不是两个 ActionInstance。

---

# 20. External Message During Action

必须测试：

```text A1 active
↓
message M1
```

分别验证：

```text NO_EFFECT
OBSERVE
WAKE
INTERRUPT
```

四种结果。

---

# 21. Private Message

测试：

```text private message from known person
```

应正确：

```text person_id
relationship
social_space_id
```

并进入 Influence。

---

# 22. Group Message

测试：

```text group message
```

必须：

```text actor person_id
group social_space_id
```

独立。

不能：

```text group_id -> person_id
```

---

# 23. Unknown Person

未知 person：

```text independent PersonIdentity
```

仍可产生：

```text ExternalInfluence
```

但不得：

```text core friend assumptions
```

---

# 24. Mention Policy

`@self`：

只能使：

```text existing influence policy
```

有机会产生：

```text WAKE / INTERRUPT / response
```

不能：

```text @self
→ always interrupt
```

---

# 25. Non-mention Group

继续：

```text existing Phase 13/13.1 policy
```

不要因为 Phase 16：

```text every non-mention
→ interrupt autonomous life
```

---

# 26. Message Deduplication

同一个：

```text self_id + transport_event_id
```

收到两次：

必须：

```text one InfluenceDecision
one SocialInteraction processing
one Conversation turn
```

不能：

```text interrupt twice
```

---

# 27. Repeated Messages

同一个人快速发送：

```text M1
M2
M3
```

不能：

```text M1 interrupts A1
M2 interrupts interruption
M3 interrupts interruption
```

必须定义：

```text interrupt already active
```

时的行为。

推荐：

```text M1
→ interrupt A1

M2/M3
→ enter same social interaction lane
→ do not create nested interrupted actions
```

---

# 28. No Nested Interruptions

禁止：

```text A1 interrupted
↓
A2
↓
A2 interrupted
↓
A3
```

在同一个 character 上无限套娃。

单层：

```text A1
↓
InterruptedActionContext
```

即可。

---

# 29. Nested External Event Policy

如果 interrupt session 中又来了消息：

继续：

```text same social interaction lane
```

不能：

```text new interruption context
```

---

# 30. Conversation Response During Interrupt

ConversationRuntime：

```text uses fresh CognitiveContext
```

因此应该看到：

```text current world
current action/interrupted context
relationship
commitment
shared memories
```

但：

**不允许 ConversationRuntime 自己修改 interrupted state。**

---

# 31. Response Commit Guard

继续使用 Phase 12.1：

```text commit_conversation_response()
```

必须检查：

```text world_revision
cognitive_revision
character_id
```

如果在：

```text LLM thinking
```

期间发生：

```text resume/interruption/world change
```

则 response：

```text stale
→ silent
```

---

# 32. Stale Response after Interrupt

场景：

```text M1
↓
Conversation LLM starts
↓
another external event arrives
↓
current action/interruption state changes
↓
LLM returns
```

要求：

```text response rejected
```

不能发送：

```text based on old world
```

---

# 33. Relationship Update

一次真实外部消息：

```text SocialInteractionFact
```

必须：

```text RelationshipUpdateEngine
```

处理。

不能：

```text ConversationRuntime
→ direct trust write
```

---

# 34. Relationship Revision

外部互动导致：

```text relationship mutation
```

则：

```text cognitive_revision++
world_revision unchanged
```

保持 Phase 8.1。

---

# 35. No Duplicate Relationship Mutation

同一个 transport event replay：

```text relationship delta only once
```

必须测试。

---

# 36. Commitment Detection

如果消息包含：

```text explicit invitation_accepted
promise_made
appointment_confirmed
```

继续由：

```text CommitmentDetector
```

决定。

Conversation LLM：

不能创建 Commitment。

---

# 37. Commitment during Interrupt

场景：

```text current action A1
↓
friend message
↓
invitation accepted
```

必须：

```text relationship
+
commitment
```

正确写入。

同时：

```text A1
```

是否继续：

由 existing influence / action rules 决定。

不要因为“产生 Commitment”就硬编码：

```text A1 cancel
```

---

# 38. Conversation 与 Autonomous Resume

例如：

```text A1 = gaming
QQ friend says:
“晚上一起玩？”
```

如果：

```text Commitment accepted
```

可能得到：

```text C1 = future social commitment
```

但当前 A1：

```text 不因未来 commitment 自动被取消
```

因为：

```text future obligation ≠ current interruption
```

---

# 39. Immediate Social Activity

如果 message semantics 明确要求：

```text immediate shared activity
```

并已有：

```text InfluenceDecision.INTERRUPT
```

才允许影响当前 Action。

不要用：

```text target_activity != empty
```

直接 interrupt。

---

# 40. No LLM for Influence Classification

ExternalInfluenceEvaluator：

```text deterministic
```

继续保持：

```text no LLM
```

---

# 41. LLM 只负责 Conversation / Decision Ambiguity

外部事件影响路径：

```text External Event
→ deterministic Influence
```

如果需要：

```text Action choice ambiguity
```

才进入：

```text DecisionCoordinator
```

---

# 42. No Social Agent

禁止：

```text SocialAgent
ConversationAgent
FriendAgent
OnlinePresenceAgent
```

---

# 43. No Emotion System

本阶段仍然禁止：

```text mood
emotion
attachment
jealousy
romance
```

后续单独阶段。

---

# 44. No Proactive Message

即使：

```text social_need
```

被激活：

也不能：

```text autonomous QQ outbound
```

---

# 45. No Conversation History DB

继续禁止：

```text chat_history
conversation_messages
message_embeddings
```

Phase 16 不建立完整聊天记录数据库。

---

# 46. Conversation Episode

可以增加一个：

```text runtime-only Conversation Episode
```

但只能作为：

```text 当前连续社交交互的控制状态
```

不能变成：

```text permanent chat transcript
```

---

# 47. Episode Boundary

建议使用：

```text same person
same social_space
activity continuation
inactivity timeout
```

确定：

```text conversation session
```

但不要把：

```text 每条消息
```

都变成一个 Experience。

---

# 48. Episode Timeout

允许配置：

```text social.interaction_episode_timeout_seconds
```

推荐：

```text 900s
```

即：

```text 15 min inactivity
```

后视为当前互动结束。

如果已有相关配置，复用。

---

# 49. No Persistence of Raw Transcript

Conversation Episode：

```text turn_ids
person_id
social_space_id
started_at
last_activity_at
message_count
```

可以存在 runtime memory。

不保存完整：

```text message bodies
```

作为 episode database。

---

# 50. Conversation Episode 与 Experience

本阶段可以让：

```text meaningful social episode
```

最终形成：

```text SocialInteractionFact
```

但：

**只有已有 deterministic significance 规则允许时，才进入 Experience。**

---

# 51. 不改变 Experience Identity

如果 Episode 确实形成 Experience：

仍必须：

```text canonical episode_key
```

遵守：

```text ActionInstance > Commitment > Interaction
```

---

# 52. Conversation-only Experience

如果聊天没有：

```text shared activity
ActionInstance
Commitment
```

也允许成为一种：

```text interaction-based social episode
```

前提是：

```text interaction_id
```

能够唯一标识它。

不能用：

```text person_id alone
```

作为 episode identity。

---

# 53. Meaningfulness

本阶段不要引入 LLM importance scoring。

允许 deterministic：

```text explicit invitation
explicit promise
appointment
meaningful social interaction significance
long enough continuous exchange
```

等已有条件。

---

# 54. Short Chat

普通：

```text “在吗”
“在”
```

不应该自动：

```text Experience
Memory
```

---

# 55. Meaningful Chat

如果：

```text explicit social fact
```

或者已有：

```text InteractionSignificance.major
```

允许：

```text Experience
```

但必须沿现有 ExperienceBuilder。

---

# 56. No Memory Explosion

例如：

```text 一个人每天 300 条消息
```

不能：

```text 300 memories
```

必须：

```text 一个或有限数量的 social episodes
```

且：

```text promotion threshold
```

继续由 Phase 10 existing rules 决定。

---

# 57. Memory Identity

继续：

```text Experience.episode_key
→ shared/<person>/<episode>
```

Conversation episode 如果最终成为 Experience：

必须：

```text Experience
→ Memory
```

自动沿用 Phase 10.2 同源 identity。

---

# 58. Conversation Experience 与 Shared Activity

不能重复记录：

```text 同一次社交互动
```

例如：

```text QQ chat
↓
invitation accepted
↓
shared activity
```

最终不能得到：

```text chat memory
shared activity memory
commitment memory
```

三条高度相似的 Memory。

优先：

```text one coherent social episode
```

---

# 59. Same Episode Aggregation

必须利用：

```text correlation_id
causation_id
commitment_id
action_instance_id
interaction_id
```

聚合。

继续：

```text ActionInstance > Commitment > Interaction
```

---

# 60. Restart

如果：

```text conversation episode active
↓
Runtime restart
```

本阶段必须决定：

### 方案 A

episode 不恢复，只恢复已经持久化的 social facts。

### 方案 B

恢复有限的 episode metadata。

两者都可以。

但：

**不要恢复完整聊天正文。**

优先方案 A，除非现有架构已经自然支持 B。

---

# 61. InterruptedAction Persistence

必须审计：

```text InterruptedActionContext
```

在 Runtime restart 后是否存在。

如果：

```text active interruption
```

在 shutdown 时丢失：

必须：

```text either explicitly settle/resume before shutdown
```

或：

```text persist minimal interruption identity
```

但：

**不要新增 checkpoint 系统。**

复用现有 Sandbox snapshot/persistence。

---

# 62. Restart Safety

场景：

```text A1 active
↓
interrupt
↓
shutdown
↓
restart
```

要求：

```text 不产生第二个 A1
```

也不能：

```text 自动把 A1 complete
```

必须按照已有 lifecycle 语义恢复。

---

# 63. External Event Replay after Restart

同一个：

```text transport_event_id
```

如果已被 Sandbox consumption state 记录：

restart 后 replay：

```text no duplicate influence
no duplicate relationship update
no duplicate commitment
no duplicate response
```

---

# 64. Tick vs Social Event

测试：

```text TICK
MESSAGE
TICK
```

要求：

```text deterministic world ordering
```

已有 Phase 14 world lock 必须继续有效。

---

# 65. Interrupt vs Tick

尤其测试：

```text autonomous action in progress
+
tick
+
interrupt event
```

两个不能：

```text simultaneously mutate current_action
```

---

# 66. OneBot Realistic Integration

继续使用：

```text FakeOneBotTransport
```

不能让 CI 依赖 live QQ。

---

# 67. End-to-end scenario

至少一个完整测试：

```text OneBot inbound message
↓
normalize
↓
ExternalWorldEvent
↓
InfluenceDecision = INTERRUPT
↓
current ActionInstance paused
↓
SocialInteractionFact
↓
Relationship / Commitment
↓
ConversationRuntime
↓
ResponseCommit
↓
FakeOneBot outbound
↓
current ActionInstance resumes
```

---

# 68. OBSERVE scenario

完整测试：

```text active action
↓
message
↓
OBSERVE
↓
conversation response
↓
action continues
```

---

# 69. WAKE scenario

完整测试：

```text active action
↓
message
↓
WAKE
↓
one immediate world reevaluation
↓
no duplicate action
```

---

# 70. NO_EFFECT scenario

```text message
↓
NO_EFFECT
↓
world unchanged except verified interaction facts
```

---

# 71. INTERRUPT scenario

```text message
↓
INTERRUPT
↓
same ActionInstance preserved
```

---

# 72. Conversation Stale scenario

```text LLM starts
↓
another social event changes cognitive_revision
↓
LLM returns
↓
ResponseCommit suppresses
```

---

# 73. Repeated Interrupt scenario

```text M1 → interrupt
M2 → while interrupt active
M3 → while interrupt active
```

要求：

```text one InterruptedActionContext
```

不是：

```text nested stack
```

---

# 74. Person Isolation

A 的消息：

```text A
```

不能：

```text interrupt B's social context
```

Character world shared within one character，但 person identity must stay distinct.

---

# 75. Character Isolation

Character A：

```text Person X
Action A1
```

Character B：

```text Person X
Action B1
```

Message for A：

```text only A interrupted
```

B 不受影响。

---

# 76. Group Isolation

Group A：

```text Person X
```

Group B：

```text Person X
```

不同 social_space：

不能共享：

```text conversation episode
interrupt context
group context
```

---

# 77. Relationship Isolation

外部互动：

```text RelationshipUpdateEngine
```

只修改：

```text character_id + person_id
```

不能：

```text global person state
```

---

# 78. Commitment Isolation

同样：

```text character_id
+ commitment_id
```

不同 character：

```text independent
```

---

# 79. Response Isolation

Response refs：

```text memory_refs
experience_refs
```

仍然只能来自当前 character/person CognitiveContext。

---

# 80. No New Message Bus

继续复用：

```text existing EventBus
ExternalEventQueue
OneBot lane
```

不要：

```text SocialEventBus
ConversationBus
InterruptBus
```

---

# 81. No New Memory Store

继续：

```text SandboxMemoryStore
```

---

# 82. No New Relationship Store

继续：

```text RelationshipStore
RelationshipUpdateEngine
```

---

# 83. No New Experience Store

继续：

```text ExperienceBuilder
```

---

# 84. No New Planner

继续：

```text Planner = No
```

---

# 85. No Emotion

继续：

```text Emotion = No
```

---

# 86. No proactive outbound

继续：

```text Autonomous QQ send = No
```

---

# 87. Performance

高频消息：

```text 100 messages
```

不能：

```text 100 LLM interrupt decisions
```

Influence classification deterministic。

如果多个消息进入同一 social episode：

```text bounded response/conversation behavior
```

---

# 88. Interrupt Cooldown

不允许：

```text message
→ interrupt
→ resume
→ next message
→ interrupt
→ next message
```

无限抖动。

允许一个 bounded：

```text social interruption window
```

但：

**不要硬编码人物行为。**

优先根据：

```text event urgency
current action
existing InterruptEvaluator
active social interaction state
```

决定。

---

# 89. No Global Chat Freeze

一个人私聊：

```text A
```

不能阻塞：

```text Group B
```

OneBot lane semantics 继续保持。

---

# 90. Long Conversation

一个持续：

```text 30 minutes
```

的聊天：

不能：

```text every message → interrupt/resume
```

应该：

```text first interaction establishes social wake/interruption
remaining messages stay within same social session
```

---

# 91. Social Session Close

经过 inactivity timeout：

```text session ends
```

恢复：

```text autonomous action
```

如果已经在运行。

---

# 92. Conversation During Idle

如果：

```text current_action = None
```

消息进入：

```text no interruption needed
```

只是：

```text Social interaction
Conversation
```

---

# 93. Conversation During Goal

如果：

```text current ActionInstance belongs to Goal
```

social interrupt：

不应自动：

```text Goal cancelled
```

---

# 94. Conversation During Commitment

如果：

```text commitment-related action
```

interrupt：

不应自动：

```text commitment broken
```

除非真正超过 lifecycle timing.

---

# 95. Interaction Outcome

外部社交事实必须保留：

```text event_id
person_id
social_space_id
semantic_kind
target_activity
urgency
correlation_id
causation_id
```

不要因为 conversation layer 而丢失事实 provenance。

---

# 96. Fact-safe rule

Conversation LLM 输出：

```text language only
```

仍然：

```text ≠ world fact
```

所有真实变化：

```text ExternalWorldEvent
Relationship
Commitment
Action
```

继续由 deterministic systems。

---

# 97. Tests

新增：

```text tests/test_live_social_influence.py
```

至少：

### A. NO_EFFECT

### B. OBSERVE

### C. WAKE

### D. INTERRUPT

### E. same ActionInstance resume

### F. no nested interrupt

### G. repeated message dedupe

### H. relationship update exactly once

### I. commitment detection exactly once

### J. stale response after interrupt

### K. restart during interruption

### L. private/group isolation

### M. character isolation

### N. long conversation bounded behavior

### O. conversation inactivity close

### P. OneBot end-to-end

### Q. tick + message ordering

### R. message + interrupt ordering

### S. no proactive QQ

### T. no extra LLM for Influence

---

# 98. Baseline

当前：

```text
1407 passed
```

必须：

```text
1407 全部保留
+
Phase 16 tests
```

不得：

```text skip
xfail
delete
weaken
```

---

# 99. Quality Gate

必须：

```text ruff
ruff format
mypy
pytest
CI success
```

并：

```text HEAD == origin/main
working tree clean
```

---

# 100. Regression

保持：

```text
Phase 8
Phase 8.1
Phase 9
Phase 9.1
Phase 9.1.1
Phase 10
Phase 10.1
Phase 10.2
Phase 11
Phase 12
Phase 12.1
Phase 13
Phase 13.1
Phase 14
Phase 14.1
Phase 15
```

---

# 101. Final Report

```text
# CatooBot v2.1 Phase 16 Report · Live Social Influence & Autonomous Interaction Continuity

Phase 16 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## Influence

NO_EFFECT:
OBSERVE:
WAKE:
INTERRUPT:

## Autonomous Interaction

active ActionInstance:
interrupt identity:
resume:
nested interruption:
interrupt cooldown:

## Social

person:
relationship:
commitment:
social episode:
group space:

## Conversation

CognitiveContext:
ResponseCommit:
stale response:
LLM calls:

## Persistence

interrupt restart:
goal:
action:
commitment:
relationship:

## Identity / Isolation

person:
group:
character:
social_space:

## Ordering

tick/message:
message/message:
interrupt/message:

## Long Conversation

episode start:
episode close:
message count:
no history DB:

## QQ

proactive message: No
autonomous DM: No
autonomous group message: No

## Architecture

new Social system: No
new Planner: No
new Agent: No
new Emotion: No
new Memory system: No
new Relationship system: No
new Experience system: No
new Conversation DB: No
new OneBot transport system: No
new EventBus: No
Character Bible modified: No
Git history rewritten: No
Force push: No

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
Phase 13:
Phase 13.1:
Phase 14:
Phase 14.1:
Phase 15:

## Final

Phase 16 complete: Yes / No
Phase 17 entered: No
```

---

# 102. Phase 16 真正的完成定义

最终应该做到：

```text
角色正在自己生活
        ↓
真实 QQ 事件到来
        ↓
系统判断：
NO_EFFECT / OBSERVE / WAKE / INTERRUPT
        ↓
如果需要
暂停当前真实 ActionInstance
        ↓
处理社会事实
        ↓
Relationship / Commitment / Conversation
        ↓
生成经过 Commit Guard 的回复
        ↓
社会事件结束
        ↓
恢复原来的生活
```

最终观察到的应该不是：

```text
“机器人收到消息，于是停止模拟生活。”
```

而是：

> **“罐头正在做自己的事情，但现实中的人可以随时进入她的生活；她会回应、会被打断、会记得这次互动，然后继续她原本的生活。”**

这一步完成以后，CatooBot 才真正开始具备“**在线角色**”而不是“**自主模拟器 + QQ 聊天器**”的统一性。

完成后停止，不进入 Phase 17。