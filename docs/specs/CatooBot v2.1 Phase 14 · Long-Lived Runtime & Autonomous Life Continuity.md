# CatooBot v2.1 Phase 14 · Long-Lived Runtime & Autonomous Life Continuity

## 0. 基线

当前最终稳定基线：

```text id="5x0b9f"
HEAD: 046fed7650c6190aff436258ed2a939c4fe174ce
Phase: 13.1 / Phase 13 final
Tests: 1361 passed
CI: success
```

正式进入：

# Phase 14 · Long-Lived Runtime & Autonomous Life Continuity

本阶段不是：

```text id="9gk4l2"
Phase 13.2
```

也不是继续扩展 OneBot transport。

---

# 1. Phase 14 的核心目标

目前已经可以：

```text id="spq3m1"
QQ
↓
ExternalWorldEvent
↓
Sandbox
↓
CognitiveContext
↓
ConversationRuntime
↓
QQ response
```

但如果：

```text id="1n6m2q"
24 小时没人发消息
```

角色基本没有“时间连续性”。

而此前 Phase 7 已经建立：

```text id="yv4f6r"
Goal
Autonomous Life Loop
Action
ActionInstance
```

因此 Phase 14 的核心任务是：

> **把已经存在的 Autonomous Life Loop 从 Sandbox 内部能力，接入真实长期运行时间，让角色在没有聊天消息时也能按照世界时间持续生活。**

最终形成：

```text id="8f9k3a"
Real Clock
    ↓
Runtime Tick
    ↓
World Time Advance
    ↓
Autonomous Life Loop
    ↓
Goal / Action
    ↓
Action Instance
    ↓
Experience / Memory
    ↓
Continuity
    ↺
```

同时继续接受：

```text id="q7m1v2"
QQ external events
```

形成：

```text id="4d9x6p"
Autonomous Life
        ↕
External World
        ↕
Conversation
```

---

# 2. 本阶段必须明确的目标

完成后：

### 没有人聊天

角色仍然：

```text id="3b7x5c"
有当前 Action
有世界时间
有 Goal
有 Need
有 Project
有 ongoing state
```

### 有人突然发消息

角色：

```text id="f1c8m3"
不会因为收到消息而重新生成一套生活状态
```

而是直接从：

```text id="2a9m7x"
当前真实 Sandbox state
```

建立 CognitiveContext。

### 重启 Runtime

角色：

```text id="h8q3k1"
继续从持久化世界状态恢复
```

而不是：

```text id="7c2v4p"
全部重新开始
```

---

# 3. 核心原则

## 3.1 Sandbox 仍然是世界权威

Clock：

```text id="r9h3m5"
只是触发机制
```

不能直接修改：

```text World
Goal
Action
Memory
Relationship
```

所有状态变化继续通过：

```text id="w6c1z8"
Event
→ Mutation
```

或现有 Action lifecycle。

---

# 4. 不创建第二套 Autonomous Life System

禁止：

```text id="v5m8q2"
LifeManager
LifeSimulationManager
CharacterBrain
VirtualLifeEngine
AutonomousAgent
DailyRoutineManager
```

如果 Phase 7 已经存在：

```text id="d8x2m1"
GoalManager
AutonomousLifeLoop
ActionSystem
```

优先直接重新利用。

本阶段核心是：

```text id="n3k5p7"
Runtime integration
```

不是重做 Goal / Action 系统。

---

# 5. 不创建新的 World 模型

不要新增：

```text id="a7f3m9"
SecondWorld
SimWorld
LifeWorld
OfflineWorld
```

继续只有一个：

```text id="q1w5e8"
Sandbox
```

---

# 6. Real Clock 与 Simulation Clock

必须明确区分：

```text id="x3r9p2"
real_now
```

和：

```text id="v7k4m1"
world_time
```

如果现有 Sandbox 已经有统一 clock abstraction：

```text id="h4m8s6"
必须复用。
```

不要在不同模块调用：

```python
datetime.now()
time.time()
asyncio.sleep()
```

然后各自理解时间。

---

# 7. Single Authoritative Clock

整个 Runtime：

```text id="m8k2v5"
必须只有一个 authoritative time source
```

例如：

```python
clock.now()
```

所有：

```text id="u3n7q1"
Goal timing
Commitment timing
Action timing
Experience timestamps
Continuity
Recent memory
```

均使用同一来源。

---

# 8. Wall Clock Mode

Phase 14 首先实现：

```text id="c6m1r9"
real-time mode
```

即：

```text world_time ≈ actual clock
```

但：

**不要让真实 system clock 直接写 Sandbox。**

应该：

```text id="p4x8d2"
clock tick
↓
Runtime tick
↓
Sandbox time advancement
```

---

# 9. Tick Scheduler

新增一个非常薄的：

```text id="z8n2m4"
Runtime Tick Scheduler
```

可以存在于：

```text app/runtime/
```

或扩展现有 runtime。

它只负责：

```text id="f5c9x1"
每隔 Δt
→ 调用 Sandbox autonomous tick
```

不要让 Scheduler 直接执行：

```text Goal
Action
Memory
Relationship
```

这些仍由现有系统负责。

---

# 10. Tick Interval

必须配置：

```text id="j2k6q8"
runtime_tick_interval_seconds
```

推荐默认：

```text 1 second
```

但不要要求每秒都调用 LLM。

---

# 11. Critical Rule：Tick ≠ LLM Call

不能：

```text id="m4v8q1"
每 tick
→ LLM
```

必须：

```text id="r7x3k5"
Tick
↓
deterministic evaluation
↓
是否到达 ambiguity / decision point
↓
只有必要时调用 DecisionCoordinator
```

继续严格遵守 Phase 6 的：

> LLM 只在 ambiguity points 被调用。

---

# 12. Tick ≠ World Mutation

单纯：

```text id="q9c2m6"
tick
```

不应该自动产生：

```text event
mutation
revision++
```

除非确实发生了：

```text id="b8x4n1"
world state transition
```

例如：

```text Action completes
Commitment expires
Goal progresses
Need changes
```

---

# 13. Catch-up Semantics

如果：

```text id="k6p2w4"
Runtime 停止 10 分钟
```

重新启动后：

**不能简单执行 600 次 tick。**

必须有：

```text id="u9m3x7"
elapsed-time catch-up
```

策略必须 deterministic。

---

# 14. Catch-up 必须有上限

配置：

```text id="e2r7m5"
max_catchup_seconds
```

默认建议：

```text 300 seconds
```

如果停机超过：

```text 5 分钟
```

不能无限 replay tick。

---

# 15. Catch-up 不能产生大量重复 Experience

例如：

```text id="s5v9k2"
停止 30 分钟
重启
```

不能：

```text 生成 1800 个 tick
```

然后产生大量：

```text Experience
Memory
Events
```

Catch-up 应该执行：

```text id="q8m2d4"
必要的状态跃迁
```

而不是模拟每一秒。

---

# 16. Autonomous Tick Pipeline

建议：

```text id="h7x1n5"
RuntimeTick
    ↓
Clock
    ↓
Sandbox world time evaluation
    ↓
Goal / Action evaluation
    ↓
due events
    ↓
Action lifecycle
    ↓
Experience / Memory
    ↓
Continuity
```

如果需要 Decision：

```text id="w4p8c2"
DecisionCoordinator
```

但只在真正需要的时候调用。

---

# 17. Autonomous Action

已有：

```text id="v6m2x9"
ActionSystem
ActionInstance
```

保持不变。

Phase 14 只是让：

```text id="q3n7k1"
AutonomousLifeLoop
```

获得真实时间驱动。

---

# 18. ActionInstance Recovery

Runtime 重启后：

```text id="p2x6m4"
如果存在 active ActionInstance
```

必须：

```text id="r8k3v1"
恢复，而不是重新创建第二个 ActionInstance。
```

至少验证：

```text id="c1m7x9"
action_instance_id
goal_id
step_id
status
started_at
```

保持一致。

---

# 19. Active Action 不能重复启动

非常重要：

```text id="n4q8s2"
tick
tick
tick
```

如果同一个 Action 已经：

```text active
```

不能：

```text id="g6m2p7"
创建第二个实例
```

必须继续推进原实例。

---

# 20. Goal Continuity

如果 Goal：

```text active
```

重启：

```text id="x8c3m1"
Goal 仍 active
```

如果 Goal：

```text completed
```

重启：

```text id="p7n2q5"
绝不重新激活。
```

如果 Goal：

```text cancelled
failed
```

也必须保持 terminal。

---

# 21. Commitment Continuity

Phase 9 已经支持：

```text id="f3m8k1"
pending
scheduled
active
in_progress
completed
cancelled
declined
expired
broken
```

Phase 14 不改状态机。

只确保：

```text id="r5x9c2"
真实时间推进
```

能够让现有 Commitment lifecycle 正常继续。

---

# 22. Commitment 不因为停机而错误激活

例如：

```text id="w2m7q4"
Commitment due tomorrow
```

今天停机 10 分钟后恢复：

不能提前：

```text id="j8p4n1"
activate
```

必须遵守现有：

```text earliest_at
latest_at
due_at
```

---

# 23. Commitment Expiration

如果 Runtime 停机跨过：

```text id="m9q2x7"
due_at + grace
```

恢复后必须：

```text id="v4c8n5"
settle existing lifecycle
```

不能永久保持 open。

不要新建第二套 expiration system。

---

# 24. Goal / Commitment 时间统一

必须使用：

```text id="a6k3r8"
同一个 Clock
```

避免：

```text Goal = UTC
Commitment = local
Action = system local
```

产生边界错误。

---

# 25. Experience Timestamp

Autonomous Action 完成后的：

```text id="n7m2q4"
Experience.created_at
```

必须反映：

```text world event time
```

而不是：

```text process restart time
```

如果 catch-up：

必须明确：

```text logical event time
```

与：

```text ingest time
```

区分。

---

# 26. Memory 时间语义

同样：

```text id="v8m3k1"
Memory.created_at
```

继续使用现有 Memory semantics。

不要因为 catch-up：

```text id="q4n7s2"
把十分钟前的事件全部伪装成现在发生。
```

---

# 27. Continuity Snapshot

Runtime 重启后：

```text id="h6p3x8"
ContinuitySnapshot
```

必须来自：

```text 当前真实 Sandbox state
```

而不是：

```text memory of previous runtime
```

---

# 28. Runtime Checkpoint

Phase 14 可以使用已有 Sandbox persistence。

禁止：

```text id="j8m4p2"
runtime_checkpoint.json
character_state.json
life_state.json
```

另外复制一份世界状态。

唯一权威仍然是：

```text id="x5q1m8"
Sandbox persistence
```

---

# 29. Tick Persistence

不要：

```text id="r7m2c5"
every tick → database commit
```

导致：

```text 高频 DB 写入
```

应该只在：

```text id="k4n8p1"
实际 state mutation
```

发生时持久化。

---

# 30. Tick Crash Safety

如果：

```text id="b6q2x9"
tick 正在运行
```

进程突然退出：

重启后：

```text id="n5m3r8"
不能因为最后一次 tick 不完整而复制动作。
```

Event / Mutation / Action persistence 必须保持现有原子性。

---

# 31. Runtime Start

启动顺序：

```text id="p9x4m1"
Load Sandbox
↓
Load Character
↓
Recover active Goal / Action / Commitment
↓
Recover world time
↓
Construct Continuity
↓
Start Tick Scheduler
↓
Start OneBot Gateway
```

顺序允许根据当前代码调整，但：

**必须先恢复世界，再接收外部事件。**

---

# 32. Critical Rule：先恢复世界，再收 QQ

不能：

```text id="m7c2q4"
OneBot CONNECTED
↓
QQ message
↓
Sandbox 还没恢复
```

否则可能产生：

```text stale world
wrong relation
duplicate action
```

---

# 33. Gateway Integration

Phase 13 的 OneBot Gateway 保持独立：

```text id="u8n3m5"
Gateway
```

只需要把：

```text inbound events
```

交给：

```text SandboxRuntime
```

同时 Autonomous Tick：

```text id="s6p1x8"
由 Runtime scheduler
```

独立运行。

二者共享：

```text id="d4m8q2"
同一个 Sandbox
```

---

# 34. Tick 与 External Event 并发

这是 Phase 14 最关键的问题之一。

可能同时：

```text id="x9p3m6"
TICK
+
QQ MESSAGE
```

到达。

必须避免：

```text tick 修改世界时
message 读取半更新状态
```

---

# 35. Unified Runtime Ordering

推荐：

```text id="k2m7v4"
Sandbox event loop / serialized mutation boundary
```

所有：

```text Tick
ExternalWorldEvent
Action completion
Commitment event
```

进入统一的 deterministic ordering point。

不要让：

```text OneBot lane
tick scheduler
```

各自直接 mutate Sandbox。

---

# 36. Ordering Rule

建议：

```text id="q7n3x5"
同一 runtime：
Events acquire a monotonic runtime sequence.
```

例如：

```text TICK#100
EXTERNAL#101
TICK#102
```

实际：

```text id="m4p8x1"
Sandbox
```

按照序列处理。

---

# 37. Tick 不得绕过 Event Spine

禁止：

```python id="c8n2m7"
autonomous_loop.current_world["x"] = ...
```

必须：

```text id="w5q3k9"
Tick
↓
deterministic event/fact
↓
existing Mutation/Event path
```

---

# 38. LLM 决策与 Tick

如果 autonomous life loop 到达：

```text id="a7m3x9"
multiple candidate actions
```

则：

```text id="p4k8n2"
DecisionCoordinator
```

可以被调用。

但：

```text id="x1q7m5"
每 tick
```

不能触发 LLM。

测试必须统计：

```text id="f6m2c8"
ticks = N
llm_calls << N
```

---

# 39. LLM Failure during Autonomous Life

如果：

```text id="u8m3q5"
Decision LLM unavailable
```

不要：

```text crash runtime
```

而是：

```text id="j2k7n4"
保持现有 world state
延迟决策
下一次 decision opportunity 再尝试
```

不要无限 retry。

---

# 40. Autonomous Life 不得主动发 QQ

这一点非常重要。

Phase 14：

```text id="s5n8m2"
Autonomous Life
```

可以：

```text world action
goal progress
need/project progress
```

但：

**不允许自动给主人发 QQ。**

例如：

```text id="d3x7q1"
“角色突然想空凛了”
```

不能：

```text send QQ message
```

主动社交以后单独设计。

---

# 41. No Proactive Messaging

禁止：

```text id="m8c2v5"
主动 DM
随机聊天
主动群发
scheduled messages
autonomous ping
```

---

# 42. Autonomous World Actions

如果当前 Phase 7 已经存在：

```text Minecraft action
rest
eat
listen_music
watch_drama
```

等 Action Definition：

直接复用。

不要重新创建：

```text id="z6n2q4"
LifeAction
```

---

# 43. Test Environment Clock

必须有：

```text id="v3m7k1"
FakeClock
```

测试不能依赖：

```text real sleep
time.sleep
wall clock
```

尤其跨时间窗口测试。

---

# 44. Test Clock Controls

FakeClock 至少：

```python id="f4x8m2"
now()
advance(seconds)
set(timestamp)
```

---

# 45. Tick Tests

至少：

### A. Tick without state transition

```text id="q5m2x7"
N ticks
→ no mutation
```

---

### B. Tick triggers action start

```text id="w8n3c1"
time reaches decision point
→ existing ActionInstance starts
```

---

### C. Repeated tick

```text id="k2m6p4"
same action already active
→ no duplicate
```

---

### D. Action completion

```text id="r7x3m9"
tick
→ completion
→ Experience
```

---

### E. Goal continuation

```text id="d4n8q2"
goal remains active
```

---

### F. Goal completion

```text id="s5m1x7"
proper terminal
```

---

# 46. Restart Tests

至少：

### A. Active Action restart

```text id="u8q2m5"
start action
↓
persist
↓
restart
↓
same action_instance_id
```

---

### B. Active Goal restart

```text id="j6m3p1"
persist
restart
→ same goal
```

---

### C. Commitment restart

```text id="c5x8n2"
persist commitment
restart
→ same lifecycle state
```

---

### D. Due Commitment after downtime

```text id="v3q7m9"
stop
↓
clock advance
↓
restart
→ expiration/activation handled correctly
```

---

# 47. Catch-up Tests

至少：

### A. Short downtime

```text id="h7m2x5"
30 sec downtime
→ deterministic catch-up
```

### B. Long downtime

```text id="k3n8q1"
30 min downtime
→ bounded catch-up
```

### C. No per-second replay

断言：

```text id="p6x2m9"
30 min downtime
≠ 1800 tick executions
```

---

# 48. External Event + Tick Ordering Tests

至少：

```text id="w8m3c5"
Tick
Message
Tick
```

验证：

```text runtime sequence:
T1 < M1 < T2
```

并检查：

```text world state
relationship
commitment
goal
```

没有半状态。

---

# 49. Same-time Tie Breaking

如果：

```text id="r4x7m2"
tick timestamp == external event timestamp
```

必须有 deterministic tie-break。

建议：

```text id="j8n3q5"
runtime sequence number
```

决定顺序。

不要依赖 asyncio task completion order。

---

# 50. No Duplicate Autonomous Events

测试：

```text id="m7q2x4"
same tick replay
```

不得：

```text duplicate ActionStarted
duplicate Experience
duplicate Goal completion
```

---

# 51. Autonomous vs External Isolation

测试：

```text id="k5n8p1"
Autonomous action
```

不能：

```text automatically appear as QQ reply
```

同时：

```text QQ message
```

不能：

```text restart autonomous life system
```

---

# 52. Resource Bound

长期运行必须验证：

```text id="x7m2q9"
tick count ↑
memory usage 不无限增长
```

至少：

```text bounded task count
bounded queue
bounded event trace
bounded temporary state
```

---

# 53. Scheduler Task Safety

必须确保：

```text id="q4n8m1"
只有一个 tick scheduler task
```

不能：

```text restart() twice
→ two schedulers
```

必须有 test：

```text id="m2x7p5"
start()
start()
→ one scheduler
```

---

# 54. Gateway + Scheduler Startup

同样：

```text id="s8q3k1"
start()
start()
```

不能产生：

```text duplicate OneBot gateway
duplicate tick scheduler
```

---

# 55. Runtime Stop / Restart

测试：

```text id="x5m9q2"
start
stop
start
```

要求：

```text exactly one tick loop
exactly one gateway
```

不出现 orphan task。

---

# 56. Crash Recovery

本阶段至少模拟：

```text id="c7p2n5"
Action active
process crashes
restart
```

检查：

```text id="m3q8x1"
Action ID preserved
Goal preserved
Commitment preserved
world state preserved
```

---

# 57. Do Not Build a Checkpoint System

不要新增：

```text id="q8m2p4"
runtime_checkpoint.db
life_checkpoint.json
state_snapshot.json
```

继续复用：

```text id="x1n7m3"
existing persistence
Continuity
Sandbox store
```

---

# 58. Do Not Rewrite Phase 7

Phase 7 / 7.1 是已经完成的 autonomous foundation。

禁止：

```text id="v2m8q4"
reimplement GoalManager
rewrite ActionManager
new planner
new agent
```

本阶段只：

```text id="p3x7n1"
connect clock → existing autonomous lifecycle
```

---

# 59. Do Not Change Relationship

Autonomous life actions：

```text id="j8q2m4"
```

如果会产生：

```text SocialInteractionFact
```

继续走已有 Phase 8。

不能：

```text id="m5x7p1"
autonomous tick
→ relationship direct write
```

---

# 60. Do Not Change Memory

Experience / Memory：

```text id="q7n3m8"
仍然只由真实 sandbox facts
```

Tick 本身不是 Memory。

---

# 61. Do Not Change Conversation Runtime

Phase 12.1：

```text id="s4m8q2"
ConversationRuntime
Response Commit Guard
```

保持不变。

消息与 Autonomous Tick 共享同一 Sandbox，但不是同一条 response pipeline。

---

# 62. Do Not Change OneBot Gateway

Phase 13.1：

```text id="u9m2x5"
OneBot Gateway
```

只做 transport。

本阶段不要：

```text gateway → scheduler
```

做跨层业务逻辑。

由：

```text Runtime
```

统一启动两个子系统。

---

# 63. Configuration

允许新增：

```text id="p7k3m8"
runtime.tick_interval_seconds
runtime.max_catchup_seconds
runtime.shutdown_timeout_seconds
```

但先检查已有 config。

禁止：

```text multiple time configs
```

---

# 64. Default Values

推荐：

```text id="x2m8q4"
tick_interval_seconds = 1
max_catchup_seconds = 300
shutdown_timeout_seconds = 5
```

这些只是默认值。

所有测试必须通过 FakeClock / explicit config 控制。

---

# 65. Logging

可以增加：

```text id="m7q3n2"
RUNTIME_TICK
RUNTIME_CATCHUP
AUTONOMOUS_ACTION_STARTED
AUTONOMOUS_ACTION_COMPLETED
RUNTIME_RECOVERED
```

但：

```text trace-only
```

不能因为 tick log：

```text id="v8m2p5"
修改 revision
```

---

# 66. Trace Bounding

tick trace 不能无限积累。

如果 EventBus 当前：

```text id="k2n7x4"
in-memory trace
```

必须遵循现有 bounded behavior。

禁止：

```text append forever
```

---

# 67. Time Drift

Scheduler 必须避免：

```text id="j4p8m2"
每次 sleep(interval)
↓
callback duration
↓
next sleep
```

导致长期 drift。

推荐使用：

```text id="q7m3n8"
monotonic next_deadline
```

或等价机制。

---

# 68. Busy Loop Protection

如果：

```text id="x3n8m1"
一个 tick 内没有 state transition
```

不能：

```text immediately tick again
```

必须遵守：

```text configured interval
```

---

# 69. Catch-up Implementation

推荐：

```text id="m5q2x7"
elapsed = clock.now() - persisted_world_time
```

然后：

```text if elapsed <= max_catchup:
    process deterministic due transitions
else:
    process bounded recovery
```

不要：

```text replay every second
```

---

# 70. Catch-up 不调用 N 次 LLM

这是重点。

如果：

```text id="s9m3x2"
停机 1 小时
```

不能：

```text 3600 tick
→ 3600 LLM calls
```

必须：

```text id="p7x2m4"
0
```

或：

```text small bounded number
```

只在真实 decision boundary。

---

# 71. LLM Call Budget Test

例如：

```text id="k4n8q2"
FakeClock advance 1 hour
```

断言：

```text id="x8m3p1"
llm_calls <= configured decision opportunities
```

而不是：

```text llm_calls == tick count
```

---

# 72. Realistic 24h Simulation

至少构造一个 deterministic scenario：

```text id="m2q7n5"
start
↓
8h no external message
↓
runtime autonomous ticks
↓
character state progresses
↓
new message arrives
↓
CognitiveContext sees latest state
```

要求：

```text id="j3x8p4"
reply context reflects current world
```

而不是初始 seed。

---

# 73. Long-run Test

FakeClock：

```text id="q5m2n8"
advance several hours
```

检查：

```text bounded memory
bounded tasks
no duplicate ActionInstance
no duplicate Goal
no duplicate Experience
no runaway LLM
```

---

# 74. Real Clock Smoke Test

CI 不依赖真实 1-hour runtime。

允许一个非常短的：

```text id="v7x3m1"
real scheduler smoke test
```

验证：

```text start
wait tiny interval
stop
```

不要依赖精确 wall-clock timings。

---

# 75. No Proactive QQ

再次强调：

```text id="k8m2q5"
Autonomous tick
≠
send QQ
```

绝不自动：

```text send_private_msg
send_group_msg
```

---

# 76. No Autonomous Social Agent

禁止：

```text id="p3n7x2"
SocialAgent
FriendAgent
AutonomousChatAgent
```

---

# 77. No Emotion

禁止：

```text id="m8q2v4"
mood
emotion
attachment
jealousy
```

---

# 78. No Personality Evolution

禁止：

```text id="x7n3m1"
trait learning
personality drift
```

---

# 79. No New Planner

继续：

```text id="q5m8p2"
Planner = No
```

使用已有：

```text Goal
DecisionCoordinator
ActionSystem
```

---

# 80. Character Isolation

测试：

```text id="j2n7m4"
Character A
Character B
```

两个 Runtime：

```text id="m5x8p1"
独立 clock
独立 Sandbox
独立 autonomous life
```

不能：

```text shared scheduler
shared Action
shared Goal
```

---

# 81. Multi-runtime Clock Isolation

尤其测试：

```text id="v8q3n2"
Character A clock +1h
Character B clock +0
```

要求：

```text id="p7m2x4"
A advances
B unchanged
```

---

# 82. Shutdown / Restart Integration

至少：

```text id="x4n8q2"
Runtime.start()
↓
advance clock
↓
stop()
↓
create new Runtime
↓
start()
↓
state preserved
```

---

# 83. Existing Phase Regression

全部保留：

```text id="j7m3q8"
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
```

---

# 84. No schema migration unless unavoidable

优先：

```text id="m2x7p4"
reuse existing persistence
```

如果确实需要世界时间持久化：

必须优先审计现有 Sandbox world state 是否已经保存对应时间。

若已有：

```text world timestamp
```

直接复用。

不要新建：

```text runtime_clock table
```

---

# 85. Test Baseline

当前：

```text id="p8m3x1"
1361 passed
```

要求：

```text id="q7n2m4"
1361 全部保留
+
Phase 14 tests
```

不得：

```text skip
xfail
delete
weaken
```

---

# 86. Completion Criteria

必须：

```text id="x5m8q2"
ruff
ruff format
mypy
pytest
CI success
```

同时：

```text id="j3q7n1"
HEAD == origin/main
working tree clean
```

---

# 87. Final Report

```text id="m8q2x5"
# CatooBot v2.1 Phase 14 Report · Long-Lived Runtime & Autonomous Life Continuity

Phase 14 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## Clock

authoritative clock:
real-time mode:
FakeClock:
world time persistence:
catch-up:
max catch-up:

## Autonomous Life

tick interval:
Goal evaluation:
Action evaluation:
ActionInstance continuity:
duplicate action prevention:

## Restart

active Goal recovery:
active Action recovery:
Commitment recovery:
Experience continuity:
Memory continuity:

## External Event + Tick

runtime ordering:
tie breaking:
shared Sandbox:
race protection:

## LLM

tick count:
LLM calls:
decision opportunities:
LLM failure behavior:

## Resource Safety

scheduler count:
queue bound:
task bound:
trace bound:
long-run stability:

## Shutdown

graceful:
timeout:
orphan tasks:

## QQ

autonomous QQ sending: No
proactive messaging: No
OneBot semantics changed: No

## Isolation

character:
clock:
sandbox:
goal/action:

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

## Architecture

new World system: No
new Life system: No
new Planner: No
new Agent: No
new Memory system: No
new Relationship system: No
new Experience system: No
new Conversation system: No
new QQ transport system: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Final

Phase 14 complete: Yes / No
Phase 15 entered: No
```

---

# 88. Phase 14 的真正完成定义

完成之后：

```text id="z8m3q1"
没人发消息
        ↓
Clock continues
        ↓
Sandbox continues
        ↓
Goal / Action continues
        ↓
Experience / Memory continues
        ↓
Continuity continues
```

突然：

```text id="m4p7x2"
QQ message
        ↓
OneBot Gateway
        ↓
ExternalWorldEvent
        ↓
same Sandbox
        ↓
最新 CognitiveContext
        ↓
ConversationResponse
```

如果：

```text id="q2n8m5"
Runtime restart
```

那么：

```text id="x7m3p1"
不是重新开始人生
```

而是：

```text id="v4q8n2"
从之前已经持久化的世界继续。
```

这才是 Phase 14 的真正目标：

> **让 CatooBot 从“事件驱动时才运行的角色程序”，变成一个具有真实时间连续性的长期在线角色 Runtime。**

完成后停止。

不要自动进入 Phase 15。