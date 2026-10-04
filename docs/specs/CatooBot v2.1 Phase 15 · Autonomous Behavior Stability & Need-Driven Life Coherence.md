# CatooBot v2.1 Phase 15 · Autonomous Behavior Stability & Need-Driven Life Coherence

## 0. 基线

当前最终稳定基线：

```text
HEAD: bb7996042c9abae43cbf796db3786acd6845bddd
Phase: 14.1
Tests: 1393 passed
CI: success
```

正式进入：

# Phase 15 · Autonomous Behavior Stability & Need-Driven Life Coherence

本阶段不是：

```text
Phase 14.2
```

也不是重新设计 Autonomous Life。

---

# 1. 核心目标

Phase 14 已经解决：

```text
真实时间
↓
唯一 World Tick
↓
Need 漂移
↓
Goal / Action / Commitment 持续推进
```

现在必须解决：

> **角色长期无人聊天时，会不会真的形成稳定、连续、合理的自主行为，而不是在 Need / Goal / Action 之间抖动、重复、饥饿、卡死或过度调用 LLM。**

目标闭环：

```text
Need
 ↓
pressure
 ↓
Goal / existing obligation
 ↓
Action candidate
 ↓
Decision
 ↓
ActionInstance
 ↓
Action completion
 ↓
Need relief / world effect
 ↓
new state
 ↓
next behavior
```

这一闭环必须能够稳定运行数小时甚至更长。

---

# 2. 当前实现必须先审计，不要预设重写

当前代码已经有：

```text
NeedSystem
GoalDetector
GoalManager
ActionSystem
DecisionCoordinator
SandboxRuntime.tick()
```

而且当前 tick 已经：

```text
Need advance
→ current action progress
→ commitment sweep
→ commitment goal bridge
→ goal.advance()
→ _decide_and_apply()
```

不要假设这些能力不存在。

第一步必须先完整审计现有实际行为，再决定最小修复。

---

# 3. 不创建第二套 Autonomous Life System

禁止：

```text
AutonomousBehaviorManager
LifePlanner
RoutineManager
BehaviorAgent
DailyLifeManager
CharacterBrain
```

已有：

```text
GoalManager
ActionSystem
NeedSystem
DecisionCoordinator
SandboxRuntime
```

继续作为唯一行为链。

---

# 4. 本阶段不创建 Planner

严格：

```text
Planner = No
```

不要从：

```text
Need
```

直接生成一个长期 planner。

Phase 15 只负责：

```text
现有 deterministic goal/action pipeline
```

的长期稳定性。

---

# 5. 不创建 Agent

严格：

```text
Agent = No
```

不能：

```text tick
→ agent loop
→ LLM 思考
→ 自主规划
```

---

# 6. LLM 仍然只解决 ambiguity

保持 Phase 6 原则：

```text
deterministic candidate generation
+
deterministic validation
+
LLM only when multiple / ambiguous legal choices
```

不能：

```text every autonomous decision
→ LLM
```

---

# 7. 本阶段第一目标：防止 Decision Thrashing

当前 runtime：

```text id="q7n4m2"
current_action is None
or
needs.critical()
```

就可能进入：

```text goals.advance()
or
_decide_and_apply()
```

必须确认：

### 正常场景

```text
tick
tick
tick
tick
```

如果没有新的 decision opportunity：

```text LLM calls = 0
```

不能因为：

```text every second
```

一直重新咨询模型。

---

# 8. Decision Opportunity Identity

需要定义一个 deterministic：

```text decision opportunity identity
```

不要建立数据库。

可以基于：

```text world_revision
current_action_id
critical_need_band
active_goal_id
goal_revision / updated_at
```

或经过实际代码审计后使用更合适的现有状态。

目标：

> 同一个未改变的决策机会不能重复启动同一次 LLM decision。

---

# 9. Opportunity 消失后可以重新决策

例如：

```text Need becomes critical
↓
decision
↓
action starts
```

这次机会已经结束。

之后：

```text action completes
need changes
goal changes
world changes
```

才允许产生新的 decision opportunity。

---

# 10. 不要把 Decision Cache 当永久事实

如果实现 opportunity guard：

它只能是：

```text runtime control state
```

不能成为：

```text world truth
Memory
Experience
Relationship
Goal
```

不需要数据库持久化一个“上次问过 LLM”对象。

---

# 11. 防止同一 Tick 重复 Decision

单个：

```text Runtime.tick()
```

最多只能产生：

```text one autonomous decision opportunity
```

除非已有：

```text same-tick chain
```

有明确事件推动且经过验证。

默认情况下：

```text tick
→ decision
```

结束后不能：

```text decision
→ immediate second decision
→ third decision
```

无限串联。

---

# 12. Action Thrashing

重点检查：

```text Action A
→ complete
→ Action B
→ complete
→ Action A
→ complete
```

如果：

```text Need state
World state
Goal state
```

没有实质变化，却不断来回切换：

这是 behavior thrashing。

---

# 13. Action Repetition Guard

需要一个：

```text bounded recent-action history
```

但：

**不要建立新的数据库表。**

可以使用：

```text recent ActionInstance ids
recent action definition ids
completion timestamps
```

作为 deterministic runtime state。

---

# 14. Repetition Guard 的目的

不是：

> “一个行为不能重复。”

而是：

> **没有新的需求变化 / 世界变化时，不能无限高速重复同一个行为。**

例如：

```text drink_cola
↓
drink_cola
↓
drink_cola
↓
drink_cola
```

如果每次都是真实的 Need / inventory / time progression：

允许。

如果状态几乎没变化：

```text deterministic cooldown / suppression
```

---

# 15. 不硬编码角色行为

禁止：

```python
if action_id == "drink_cola":
    cooldown = ...
```

也禁止：

```python
if character == "罐头":
```

所有 guard 必须基于：

```text ActionDefinition
Need
World
Goal
recent execution metadata
```

---

# 16. ActionDefinition 可扩展，但必须最小化

如果现有 ActionDefinition 已经有：

```text duration
need_relief
requirements
cost
tags
modes
```

优先复用。

只有确实需要时才增加：

```text repeat_cooldown
```

或等价字段。

不要增加：

```text preferred_for_character
personality_weight
emotion_weight
```

等第二套人格行为系统。

---

# 17. Need Relief 完整性

对每个能够：

```text Goal / Action
```

驱动自主行为的 Action：

必须确认：

```text Action completes
↓
expected need relief
```

确实发生。

例如：

```text sleep
→ sleepiness ↓
energy ↑

eat
→ hunger ↓

drink
→ thirst ↓
```

这些不是硬编码事件。

应继续来自：

```text ActionDefinition.need_relief
```

以及现有 Action completion pipeline。

---

# 18. Need Relief 不得凭空发生

禁止：

```text decision accepted
→ need relief immediately
```

必须：

```text ActionInstance actually completes
→ world effect
→ need relief
```

否则会出现：

```text 没做事情
但需求已经满足
```

---

# 19. Need Relief Verification

新增测试：

```text action started
→ need unchanged / expected transient
→ action not complete
→ need not magically relieved

action completed
→ need relief applied exactly once
```

---

# 20. No Double Relief

非常重要：

```text Action completion replay
```

不得：

```text need relief ×2
```

应该：

```text one ActionInstance
→ one completion
→ one relief
```

必须利用：

```text ActionInstance identity
```

而不是：

```text action definition id
```

进行幂等。

---

# 21. Goal 与 Need 的职责继续分开

保持：

```text Need
= 当前压力

Goal
= 为什么持续完成某件事

Action
= 当前具体行为
```

不要：

```text Need = Goal
```

也不要：

```text Action = Goal
```

---

# 22. Need 不要直接执行 Action

保持现有流程：

```text Need
→ candidate / Goal
→ Decision
→ Action
```

禁止：

```python
if hunger > 0.8:
    start_action("eat")
```

这样的第二条 bypass。

---

# 23. Goal Priority 不重新设计

当前 GoalManager 已经：

```text priority
↓
created_at
↓
goal_id
```

进行确定性选择。

本阶段：

**不要重新设计 Goal Priority bands。**

尤其不要改变现有：

```text PET_CRITICAL
RESOURCE_SHORTAGE
UNFINISHED_PROJECT
```

数值语义。

如果测试发现 priority starvation：

必须先证明问题，再做最小修复。

---

# 24. Goal Starvation

需要测试：

```text Goal A high priority
Goal B lower priority
```

A 连续推进若干 action。

但 B 不应该：

```text 永久饿死
```

前提是：

```text A continuously remains eligible
```

如果现有语义就是允许 A 完成前持续占用，则不要擅自改变。

只有真实证明：

```text unrelated Goal never gets a chance
```

才处理。

---

# 25. Commitment 优先级继续保持

Phase 9 的：

```text commitment
→ CommitmentGoalBridge
→ Goal
```

优先级不修改。

特别不能因为：

```text entertainment need
```

直接抢走：

```text valid social commitment goal
```

除非现有 Goal Priority 明确允许。

---

# 26. Action Candidate 合法性

每个自主行为必须继续经过：

```text WorldRuleEngine
objects
inventory
requirements
```

验证。

不能因为：

```text Need strong
```

绕过：

```text requirements
```

---

# 27. Impossible Need

测试：

```text hunger critical
```

但是：

```text no legal food action
```

必须：

```text no invalid action
```

而应该：

```text decision blocked / alternative candidate
```

或者现有系统的合法 fallback。

---

# 28. No Candidate

当：

```text no legal candidate
```

不能：

```text LLM invent action
```

必须：

```text existing block / cooldown / safe no-op
```

---

# 29. Blocked Goal Cooldown

已有：

```text RETRY_COOLDOWN_SECONDS
```

必须验证：

```text blocked
↓
no candidate
↓
cooldown
↓
not retry every second
```

同时：

```text world changes
```

可以让它提前恢复的情况必须沿用现有规则。

---

# 30. Blocked Decision LLM Budget

如果一个 Goal：

```text blocked
```

不要：

```text each second
→ LLM asks same impossible choice
```

测试：

```text blocked period = 60 seconds
LLM calls = bounded
```

最好：

```text 0
```

如果没有新的 decision opportunity。

---

# 31. Action Completion Boundary

Action 完成必须：

```text ACTION_COMPLETED
→ GoalStep update
→ Goal evaluate
→ Need relief
→ Experience
```

顺序不能破坏 Phase 7.1 / 10 identity semantics。

---

# 32. Same-tick Chain

允许：

```text Action completed
↓
GoalStep completed
↓
Goal completed
```

但不默认：

```text Goal completed
↓
immediate second Action
```

同一个 tick 最多：

```text one new action start
```

除非现有 commitment/interrupt semantics 明确要求。

---

# 33. Autonomous Behavior Report

Phase 15 可以让 `tick()` report 增加：

```text decision_opportunity
decision_reason
action_selected
action_suppressed
suppression_reason
```

但：

**这些只是 trace/report，不是新的 world state。**

---

# 34. Behavior Trace

可以增加：

```text AUTONOMOUS_DECISION
AUTONOMOUS_ACTION_SUPPRESSED
AUTONOMOUS_ACTION_REPEATED
AUTONOMOUS_GOAL_BLOCKED
```

但必须：

```text trace-only
```

不能修改：

```text world_revision
```

除非真正发生 mutation。

---

# 35. Need Band Crossings

继续复用现有：

```text calm
soft
strong
critical
```

不要新增：

```text emergency
urgent2
very-critical
```

等第二套等级。

---

# 36. Hysteresis

如果 Need 在：

```text strong ↔ critical
```

边界附近抖动：

不要导致：

```text repeated action interruption
```

必须利用已有：

```text band thresholds
current_action
interrupt evaluator
```

保持稳定。

不要增加复杂情绪系统。

---

# 37. Autonomous Interruptions

如果当前 Action 正在进行：

```text Need becomes critical
```

是否允许 interrupt：

继续由已有：

```text InterruptEvaluator
```

决定。

Phase 15 不直接：

```text if critical: cancel current_action
```

---

# 38. Interrupt Identity

中断：

```text ActionInstance A
```

必须能够：

```text pause
resume
rebind
complete
```

且：

```text same ActionInstance
```

不创建：

```text duplicate action instance
```

---

# 39. Resume Regression

场景：

```text Action A active
↓
critical need
↓
interrupt
↓
Action B
↓
B complete
↓
resume A
```

要求：

```text A original action_instance_id
```

保持。

Phase 7.1 rebind integrity 不得破坏。

---

# 40. No Autonomous Social Proactivity

Phase 15：

```text autonomous life
```

仍然：

```text no proactive QQ
```

不得因为：

```text social_need high
```

自动 DM 人。

Social Need 可以：

```text influence candidate selection
```

但：

```text no outbound network action
```

---

# 41. Social Need

如果：

```text social_need
```

进入 critical：

不能直接：

```text send_message
```

而只能：

```text existing legal action candidates
```

例如：

```text existing social activity
```

如果世界里没有合法 action：

```text safe no-op
```

---

# 42. Entertainment Need

同理：

```text entertainment
```

只能影响：

```text existing action definitions
```

不能生成：

```text invented entertainment action
```

---

# 43. Preferences

当前 runtime 已经从 Character Bible / seed 派生：

```text preference_bonus
```

本阶段可以验证它真实参与自主行为选择。

如果发现：

```text favorite actions
```

只是定义了 bonus 却根本没有生效：

可以修复。

但：

**不要重新设计 preference system。**

---

# 44. Mode Awareness

当前已经存在：

```text ModeRuntime
```

以及 seed-derived modes。

自主行为选择必须继续尊重：

```text current modes
```

例如：

```text gaming mode
sleep mode
home mode
```

只允许：

```text existing rules
```

过滤/影响候选。

---

# 45. Time-of-day Awareness

如果 Character Bible / world seed 已经有：

```text routine windows
time hints
```

本阶段可以利用它们。

如果没有：

**不要凭空创建一整套日程系统。**

---

# 46. 不创建 Daily Routine System

禁止：

```text MorningRoutine
NightRoutine
DailySchedule
CircadianPlanner
```

本阶段只利用已有：

```text world time
modes
needs
existing action definitions
```

---

# 47. 行为多样性

长时间运行：

```text 10h
```

检查：

```text action distribution
```

不能：

```text same single low-cost action 100%
```

但：

**不要硬编码随机轮换。**

应该由：

```text needs
preferences
requirements
modes
goal priority
recent action suppression
```

自然产生。

---

# 48. Randomness

如果当前 ActionSystem 已经使用：

```text seeded RNG
```

继续使用同一个：

```text simulation_seed
```

不要：

```text random.seed()
```

在 behavior layer 再开一个随机源。

---

# 49. Determinism

同样：

```text character
simulation_seed
world
Need
Goal
Clock
```

应该得到：

```text same autonomous decision sequence
```

除非：

```text external event
```

不同。

---

# 50. Replay

相同：

```text FakeClock
+
same initial seed
```

运行 1 小时两次：

```text action sequence
goal transitions
need trajectories
```

应该 deterministic。

---

# 51. No Random Behavior Drift

不要：

```text every tick random action
```

也不要：

```text every idle tick pick random hobby
```

随机只能作为：

```text existing decision tie-break
```

在合法 candidate 中使用。

---

# 52. Long-run Scenarios

必须建立至少三个长期 deterministic scenario：

### Scenario A · Normal idle life

```text 6 hours
no QQ
```

检查：

```text no crash
no duplicate actions
no infinite LLM
needs remain bounded
```

### Scenario B · Critical need

```text need intentionally high
```

检查：

```text appropriate legal action
need relief
no thrashing
```

### Scenario C · Multiple simultaneous pressures

```text hunger strong
thirst strong
entertainment strong
unfinished project
```

检查：

```text deterministic selection
goal priority respected
action candidate legal
no starvation bug
```

---

# 53. Long-run Bounds

至少统计：

```text ticks
action starts
action completions
goal creates
goal completes
goal blocks
LLM calls
decision rejections
suppressed repetitions
```

不能：

```text LLM calls ≈ ticks
```

不能：

```text action starts ≈ ticks
```

不能：

```text goal count → unbounded
```

---

# 54. Goal Count Stability

6 小时模拟：

```text open goals
terminal goals
```

检查：

```text duplicate goals = 0
```

如果同一 deterministic goal source repeated：

继续依赖：

```text dedupe_key
```

不要新增 Goal dedupe system。

---

# 55. ActionInstance Count Stability

例如：

```text 6 hours
```

必须：

```text active ActionInstance <= 1
```

因为当前角色世界只允许一个 current_action。

并且：

```text total instances
```

合理增长。

不能：

```text 1000 instances
```

但世界只经过几小时。

---

# 56. Need Stability

Need level：

```text 0..1
```

持续运行：

不能：

```text >1
<0
NaN
Infinity
```

---

# 57. Need Relief Bounds

Action completion：

```text need relief
```

不能：

```text negative weird
>1
```

所有结果必须 clamp 到：

```text 0..1
```

继续复用现有 NeedSystem 语义。

---

# 58. No Need Persistence Rewrite

继续复用：

```text existing Sandbox snapshot
```

不要：

```text needs_history table
```

---

# 59. Persistence

长时间运行后：

```text shutdown
↓
restart
```

必须保持：

```text Need
Goal
Current Action
ActionInstance
Commitment
World
```

的一致性。

尤其：

```text current_action
```

不能 restart 后重新创建一个新的：

```text action_instance
```

---

# 60. Crash Safety

模拟：

```text Action active
```

突然：

```text process stop
```

restart：

```text same ActionInstance
```

不得：

```text duplicate Action
```

---

# 61. External Event Interruption

真实 QQ 事件：

```text incoming message
```

继续使用：

```text ExternalInfluenceEvaluator
InterruptEvaluator
```

不得让：

```text ConversationRuntime
```

直接 cancel autonomous action。

---

# 62. Conversation does not own autonomous state

聊天：

```text “你在干嘛？”
```

只能读取：

```text current world
current action
```

不能：

```text reply
→ start another autonomous action
```

除非现有 external influence / decision pipeline 正式决定。

---

# 63. Social Context after autonomous action

Action 完成：

```text shared / social action
```

如果产生 verified:

```text SocialInteractionFact
```

继续进入：

```text Relationship
Experience
Memory
CognitiveContext
```

本阶段不得建立另外一套“生活记忆”。

---

# 64. Autonomous Experience

只有：

```text actual ActionInstance completion
```

才产生 Experience。

不要：

```text tick
→ Experience
```

---

# 65. Autonomous Memory

只有既有：

```text Experience → Memory promotion
```

才进入 Memory。

不要：

```text every action
→ Memory
```

---

# 66. No Personality Evolution

禁止：

```text action preference learning
trait adjustment
personality drift
```

Phase 15 只是：

```text use existing character preferences
```

不能：

```text modify them
```

---

# 67. Character Bible remains canonical

继续：

```text Character Bible
→ CharacterDefinition
→ WorldSeed
```

下游 behavior system：

```text character-agnostic
```

---

# 68. No hardcoded character

禁止：

```python
if character.name == "罐头":
```

也禁止：

```text Minecraft
cola
pudding
```

进入 core behavior conditions。

这些必须来自：

```text seed
ActionDefinition
NeedDefinition
ModeDefinition
CharacterDefinition
```

---

# 69. Phase 15 不修改 Goal Priority Bands

再次明确：

```text PRIORITY_PET_CRITICAL
PRIORITY_RESOURCE_SHORTAGE
PRIORITY_UNFINISHED_PROJECT
```

本阶段默认不改。

如测试证明 starvation：

先报告：

```text evidence
```

再允许最小改动。

---

# 70. Phase 15 不修改 Commitment Semantics

保持：

```text Commitment
→ Goal
→ Action
→ Fulfillment
```

全部不变。

---

# 71. Phase 15 不修改 Relationship Semantics

保持：

```text SocialInteractionFact
→ RelationshipUpdateEngine
```

唯一关系 mutation path。

---

# 72. Phase 15 不修改 Memory Identity

保持：

```text Experience.episode_key
→ Memory dedupe
```

完全不动。

---

# 73. Phase 15 不修改 Conversation

保持：

```text ConversationRuntime
ResponseCommitGuard
OneBot Gateway
```

本阶段最多做 regression。

不要把 autonomous behavior 和 chat behavior 合并成一个 manager。

---

# 74. Phase 15 不修改 Runtime Clock

Phase 14.1 已经完成：

```text RuntimeScheduler
= 唯一 world tick owner
```

严禁重新引入：

```text sandbox_tick
```

或改变：

```text scheduler frequency
vs world elapsed
```

语义。

---

# 75. Behavior Decision Report

建议每次自主行为输出：

```text
reason
goal_id
need_pressure
candidate_count
selected_action
decision_source
suppressed_reason
```

用于测试和 debug。

不能：

```text save every tick forever
```

必须 bounded trace。

---

# 76. Decision Source

必须明确区分：

```text deterministic
llm
resume
commitment
interrupt
```

例如：

```text single legal candidate
→ deterministic

multiple legal candidates
→ LLM decision
```

这应该成为长期行为审计的一部分。

---

# 77. Decision Trace Must Be Reproducible

同样：

```text seed
world
need
goal
clock
```

重复运行：

```text same candidate list
same deterministic selection
same decision trigger
```

LLM tie-break 则：

```text fake provider
```

测试中必须完全确定。

---

# 78. LLM Call Upper Bound

对任何：

```text N ticks
```

必须保证：

```text LLM calls <= decision opportunities
```

并且：

```text no duplicate same opportunity
```

例如：

```text 3600 ticks
```

不能：

```text 3600 LLM calls
```

---

# 79. LLM Unavailable

自主生活遇到：

```text AI unavailable
```

必须：

```text no world corruption
no fake action
no fake completion
```

保持：

```text existing action state
```

如果存在：

```text one deterministic legal candidate
```

可以直接执行，而不是必须依赖 LLM。

---

# 80. No Fake Fallback Action

不能：

```text LLM failed
→ invent rest action
```

除非该 action 本身已存在于：

```text ActionDefinition
```

且 deterministic rule 明确允许。

---

# 81. Need / Goal / Action Audit

Phase 15 最终必须能够回答：

```text 为什么角色现在正在做这个？
```

至少可以追溯：

```text ActionInstance
→ Goal / Decision
→ Need / Commitment / Project source
```

不能只有：

```text action_id
```

没有 reason。

---

# 82. Causation / Correlation

继续：

```text Goal
→ Decision
→ ActionInstance
→ ACTION_STARTED
→ ACTION_COMPLETED
→ Experience
```

每条链：

```text causation_id
correlation_id
```

必须保持。

---

# 83. Autonomous Action Audit

新增测试：

```text ActionInstance
```

可以追溯：

```text source
reason
goal_id
decision trace
```

---

# 84. No New Event Spine

继续复用：

```text EventBus
MutationLog
DecisionCoordinator
```

不要创建：

```text BehaviorEventBus
LifeEventBus
AutonomyEventBus
```

---

# 85. World Mutation Authority

行为层所有真实变化：

```text location
inventory
need
project
action
goal
```

仍然只能通过：

```text existing mutation/action spine
```

---

# 86. Resource Safety

6 小时 / 24 小时 simulated runtime：

必须：

```text bounded memory
bounded tasks
bounded trace
bounded goal list
```

不要：

```text append every tick into Python list
```

如果需要 history：

```text bounded ring
```

---

# 87. Performance

由于现在：

```text RuntimeScheduler = 1s
```

Phase 15 必须确保：

```text ordinary tick
```

不产生：

```text database write storm
LLM storm
large CPU work
```

重点确认：

```text tick with no transition
```

尽量：

```text cheap
```

---

# 88. No Per-tick Heavy Retrieval

不要：

```text every tick
→ full Memory retrieval
→ full CognitiveContext build
```

除非当前行为决策真的需要。

---

# 89. CognitiveContext 不参与普通 Tick

自主生活：

```text Tick
```

不应该每秒构建完整：

```text CognitiveContext
```

CognitiveContext 主要在：

```text external conversation
decision ambiguity
```

需要时构建。

---

# 90. No Conversation Cost during Idle Life

6 小时无人聊天：

```text ConversationRuntime calls = 0
```

除非：

```text explicit autonomous decision ambiguity
```

但该 decision 仍然不是 conversation response。

---

# 91. Long-run scenario: 6 hours

必须：

```text FakeClock
advance 6h
```

统计：

```text actions
goals
needs
LLM
Experience
Memory
```

并确保系统没有：

```text runaway
```

---

# 92. Long-run scenario: 24 hours

至少一个：

```text deterministic 24h simulation
```

要求：

```text no crash
no duplicate ActionInstance
no infinite decision loop
no memory explosion
no task explosion
```

不要求一定产生某个固定故事结果。

只要求：

```text architecture remains coherent
```

---

# 93. Behavior Quality ≠ Fixed Story

不要把测试写成：

```text 24h 后一定先喝可乐
然后一定睡觉
然后一定 Minecraft
```

这是错误的。

测试应该验证：

```text rules
invariants
causality
bounds
determinism
```

而不是硬编码一条剧情。

---

# 94. Tests

建议新增：

```text tests/test_autonomous_behavior_stability.py
```

至少覆盖：

### A. Decision dedupe

同一 opportunity：

```text one LLM decision max
```

### B. Action completion relief

```text exactly once
```

### C. Action repetition guard

没有 state change 时：

```text no rapid same-action loop
```

### D. Blocked goal cooldown

```text no per-second retry
```

### E. Impossible need

```text no illegal action
```

### F. Multiple pressures

```text deterministic
```

### G. Goal starvation

```text detect / preserve existing semantics
```

### H. Interrupt / resume

```text same ActionInstance
```

### I. Restart

```text state continuity
```

### J. Six-hour simulation

```text bounded
```

### K. 24-hour simulation

```text bounded
```

### L. Seed determinism

same seed:

```text same sequence
```

### M. Different seeds

如果允许 tie-break randomness：

```text deterministic per seed
```

不要求不同 seed 必须不同结果。

---

# 95. Regression

全部必须保持：

```text Phase 8
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
```

---

# 96. Existing Test Baseline

当前：

```text 1393 passed
```

必须：

```text 1393 全部保留
```

---

# 97. No Test Weakening

禁止：

```text skip
xfail
delete
weaken assertion
fake clock bypass that avoids actual behavior
```

---

# 98. Quality Gate

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

# 99. Git Safety

继续：

```text E:\WorkSpace ZCode\CatooBot
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

真实 Character Bible：

```text never public
```

---

# 100. 最终报告

```text
# CatooBot v2.1 Phase 15 Report · Autonomous Behavior Stability & Need-Driven Life Coherence

Phase 15 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## Autonomous Loop

Need:
Goal:
Action:
ActionInstance:
Completion:
Need relief:

## Decision Stability

decision opportunities:
LLM calls:
duplicate opportunities:
same-tick repeated decision:

## Behavior Stability

repeated action prevention:
blocked goal cooldown:
interrupt / resume:
impossible need:
multiple simultaneous pressures:
goal starvation:

## Determinism

simulation seed:
same-seed replay:
different-seed behavior:

## Long Run

6h:
24h:
actions:
goals:
LLM:
Experience:
Memory:
bounded tasks:
bounded trace:

## Persistence

Need:
Goal:
Action:
ActionInstance:
Commitment:
World:

## Read-only Boundaries

Conversation changed autonomous state unexpectedly: No
Memory changed autonomous state unexpectedly: No
Relationship bypass: No
Commitment bypass: No

## QQ

proactive message: No
autonomous DM: No
autonomous group message: No

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

## Architecture

new Autonomous Life system: No
new Planner: No
new Agent: No
new Emotion system: No
new Personality Evolution: No
new Routine system: No
new Memory system: No
new Relationship system: No
new Experience system: No
new Conversation system: No
new World system: No
new DB: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Final

Phase 15 complete: Yes / No
Phase 16 entered: No
```

---

# 101. Phase 15 的真正完成定义

最终不是：

```text
角色每天按照固定脚本生活
```

也不是：

```text
LLM 每秒替角色思考
```

而是：

```text
真实时间
↓
Need 持续变化
↓
现有 Goal / Commitment / Project 状态影响行为
↓
合法 Action 候选出现
↓
确定性规则先筛选
↓
只有真正存在歧义时才调用 LLM
↓
ActionInstance 执行
↓
世界发生真实变化
↓
Need 被真实缓解
↓
Experience / Memory 按既有规则产生
↓
下一次行为基于新的世界状态
```

最终应该能够出现：

```text
“她为什么现在去做这个？”
```

系统能够沿链回答：

```text
Need / Goal / Commitment / Project
        ↓
Decision
        ↓
ActionInstance
        ↓
World effect
```

而不是：

```text
“因为 LLM 觉得她应该这么做。”
```

---

# 102. 明确停止边界

Phase 15 完成后：

**不要进入 Phase 16。**

不要在本阶段实现：

```text
Emotion
Mood
Personality Evolution
Proactive Social Messaging
Autonomous QQ
Romance
Self-awareness
Dreams
Diary Agent
Social Graph
Planner
Agent
```

Phase 15 的唯一目标：

> **让已经拥有真实时间的 CatooBot，在长期无人干预时，能够稳定、连续、可追溯地自己生活。**

完成后停止。