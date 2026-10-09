# CatooBot Phase 6B
# Activity Decision Engine / Transition Guard

## 0. 阶段目标

Phase 6A 已完成：

- ActivityEpisode
- ActivityRuntime
- Activity lifecycle
- Primary Episode
- Task ↔ Activity binding
- World Tick
- Persistence
- Recovery
- Episode event
- Minecraft observation adapter
- Activity → CharacterState projection
- Activity ↔ Memory isolation
- Real Java / Real QQ verification

Phase 6B 的目标：

> **让 Activity Runtime 在 Episode 接近结束 / 到期 / 被重大事件影响时，能够对当前 Episode 做出可审计的 Continue / Extend / Transition 决策。**

最终形成：

```text
World Clock
    ↓
Current ActivityEpisode
    ↓
Transition Guard
    ↓
Activity Decision Engine
    ├── CONTINUE
    ├── EXTEND
    └── TRANSITION
    ↓
ActivityRuntime
```

本阶段仍然：

**不允许 Activity Runtime 自主执行 Minecraft 世界动作。**

---

# 一、最高优先级架构原则

必须严格区分：

```text
ActivityEpisode
    =
当前活动生命周期

ActivityDecision
    =
决定当前活动下一步怎么办

TaskRuntime
    =
真实任务执行

Minecraft ActionRuntime
    =
真实 Minecraft 动作执行
```

禁止：

```text
ActivityDecision
→ minecraft_move_to
```

禁止：

```text
ActivityDecision
→ minecraft_dig
```

禁止：

```text
ActivityDecision
→ TaskRuntime.confirm_and_start()
```

禁止：

```text
ActivityDecision
→ 自动创建 MEDIUM Task
```

6B 只能改变：

```text
ActivityEpisode lifecycle
```

以及：

```text
next activity intent / hint
```

---

# 二、先检查现有实现

实现之前必须完整检查：

```text
app/activity/
app/world/
app/core/
app/tasks/
app/memory/
WebUI world/activity
```

同时重新阅读：

```text
docs/specs/v1.0.md
```

重点：

- §3 Activity Architecture
- §15 World Tick
- §16 Transition Window
- §17 Continue
- §18 Extend
- §19 Transition
- §20 Duration
- §21 Bounce Guard
- §22 Activity Merge
- §65 Consistency Checker
- §120 Decision Trace
- §121 Rule > Model
- §122 Decision Output
- §123 Model Frequency
- §124 Model Failure
- §125 Planner Failure
- §126 Primary Activity
- §127 Unknown Activity
- §128 Social Cognition
- §149 Performance

已有能力必须复用。

禁止再创建：

```text
NewActivityRuntime
ActivityRuntimeV2
MinecraftActivityPlanner
QQActivityPlanner
```

等平行系统。

---

# 三、6B 暂时不接 LLM

这是硬约束。

6B 第一版：

```text
ActivityDecisionEngine
=
deterministic rule engine
```

不允许：

```text
LLM
→ 每分钟 decision
```

不允许：

```text
LLM
→ 随机 next activity
```

也不允许：

```text
LLM
→ 绕过 hard constraints
```

原因：

v1.0 明确要求规则优先于模型，并要求模型失败时有 deterministic fallback。

以后可以做 Model-assisted decision，但留给后续阶段。

---

# 四、Decision 结果

统一定义：

```python
ActivityDecision(
    decision,
    reason_code,
    next_activity_hint,
    extension_seconds,
    confidence,
    trace_id,
)
```

其中：

```text
decision:
CONTINUE
EXTEND
TRANSITION
```

不要加入：

```text
EXECUTE
ACT
DO_TOOL
```

---

# 五、CONTINUE

含义：

> 当前活动继续，不改变 Episode。

例如：

```text
planned_end = 15:30
current = 15:24
```

仍然：

```text
CONTINUE
```

不得提前创建新 Episode。

---

# 六、EXTEND

含义：

> 当前活动继续，但延长生命周期。

例如：

```text
current episode:
gaming

planned_end:
15:30

decision:
EXTEND + 30min
```

必须：

```text
planned_end_at += extension
```

但：

```text
planned_end_at <= max_allowed_end
```

绝对禁止无限延长。

---

# 七、TRANSITION

含义：

> 当前 Episode 结束，并准备创建下一个 Episode。

注意：

```text
ActivityDecision = TRANSITION
```

不等于：

```text
next Episode immediately active
```

必须有明确 lifecycle：

```text
current Episode
→ terminal state
→ create next Episode
→ activate next Episode
```

---

# 八、Transition Window

增加：

```yaml
world:
  activity:
    transition_window_minutes: 5
```

默认：

```text
5 minutes
```

可配置。

v1.0 要求接近 `planned_end_at` 时进入 transition window，但**不能因为进入 window 就立即切换活动**。

---

# 九、transition_pending 不应成为 TaskRuntime State

不要修改 TaskRuntime。

也不要为了 Activity 决策无意义增加大量 lifecycle 状态。

建议：

```python
transition_pending: bool
```

作为 Activity Decision Runtime 的派生/运行字段。

或者等价内部 decision flag。

不要让：

```text
ActivityStatus
```

无限膨胀成：

```text
ACTIVE
EXTENDED
TRANSITION_PENDING
DECIDING
DECISION_FAILED
WAITING_MODEL
...
```

保持生命周期状态和决策状态分离。

---

# 十、普通 Tick 行为

普通 World Tick：

```text
if now < planned_end_at - transition_window:
    do nothing
```

不得：

```text
选择新 Activity
```

不得：

```text
调用 LLM
```

不得：

```text
创建 Episode
```

不得：

```text
写大量 event
```

v1.0 明确要求普通 60 秒 Tick 只检查是否接近 / 超过 `planned_end_at`。

---

# 十一、Transition Window 行为

进入 window 时：

```text
transition_pending = true
```

并记录：

```text
decision_window_entered_at
```

但是：

```text
current Episode 仍然 ACTIVE
```

不能提前结束。

不能提前创建下一个 Episode。

---

# 十二、Decision Trigger

Decision 只能由明确原因触发：

```text
TIME_NEAR_END
TIME_EXPIRED
TASK_STARTED
TASK_COMPLETED
TASK_FAILED
TASK_INTERRUPTED
USER_INTERACTION
WORLD_EVENT
GOAL_CHANGED
RECOVERY
MANUAL
```

不要出现：

```text
RANDOM_TICK
```

作为 decision reason。

---

# 十三、Continue Rule

默认规则：

```text
if now < planned_end_at:
    CONTINUE
```

Transition window 内也可以：

```text
if current activity still valid:
    CONTINUE / EXTEND
```

但不能：

```text
random transition
```

---

# 十四、Minimum Duration Guard

Episode：

```text
min_duration
```

之前不得随意 Transition。

例如：

```text
gaming
min=20 min
```

10 分钟时即使 Planner 说：

```text
transition
```

也必须拒绝 / 改为：

```text
CONTINUE
```

除非存在：

```text
hard interruption
```

例如：

```text
TASK_STARTED
USER_INTERRUPT
SYSTEM_RECOVERY
```

Hard interruption 可以突破 minimum duration。

---

# 十五、Maximum Duration Guard

任何活动不得：

```text
planned duration > max_duration
```

也不得：

```text
extend
```

到：

```text
> max_duration
```

达到 max：

```text
CONTINUE
```

必须变成：

```text
TRANSITION
```

这是 hard rule。

---

# 十六、Extension Guard

每次 EXTEND：

必须记录：

```text
extension_count
extension_seconds
previous_planned_end
new_planned_end
reason
```

不要无限 extend。

建议默认：

```text
max_extensions_per_episode = 2
```

配置化即可。

不要新增大量复杂参数。

---

# 十七、Bounce Guard

这是 6B 的核心。

防止：

```text
gaming
→ watching
→ gaming
→ watching
```

快速来回跳。

建立：

```text
ActivityBounceGuard
```

至少看：

```text
previous activity
current activity
recent transitions
minimum stable duration
cooldown
```

---

# 十八、Bounce 示例

禁止：

```text
A
→ B
→ A
```

如果：

```text
B < cooldown
```

则：

```text
reject A
```

改为：

```text
CONTINUE B
```

或者选：

```text
neutral activity
```

必须 deterministic。

---

# 十九、Adjacent Activity Merge

如果：

```text
Episode A = gaming
Episode B = gaming
```

且中间没有真实 interruption：

不要：

```text
A COMPLETED
B ACTIVE
```

制造大量无意义 Episode。

应该：

```text
merge adjacent same activity
```

或者直接：

```text
EXTEND
```

优先使用：

```text
EXTEND
```

但如果确实已经产生两个 Episode：

可以在 Timeline presentation / history 层进行语义 merge。

不得丢失原始 audit。

v1.0 明确要求相邻相同 Activity 避免无意义拆分。

---

# 二十、Activity Consistency Checker

建立：

```text
WorldConsistencyChecker
```

在：

```text
snapshot
activity transition
recovery
```

之前检查。

至少：

### Rule 1

```text
started_at <= now
```

### Rule 2

```text
ended_at >= started_at
```

### Rule 3

不存在两个：

```text
ACTIVE primary episodes
```

### Rule 4

current Episode：

```text
status=ACTIVE
```

### Rule 5

Location / Activity 没有明显冲突。

例如：

```text
sleeping + kitchen
```

至少：

```text
WARNING
```

不得静默吞掉。

这些规则与 v1.0 §65 一致。

---

# 二十一、Consistency Checker 不负责修复

特别重要。

Checker：

```text
detect
report
```

不能：

```text
auto mutate
```

例如发现：

```text
two active episodes
```

不能自己：

```text
delete one
```

必须：

```text
ERROR / DEGRADED
```

然后由：

```text
ActivityRuntime recovery / transition
```

决定如何修复。

---

# 二十二、Decision Trace

每次 Decision 必须有可解释 trace。

至少：

```text
trace_id
episode_id
current_activity
elapsed
planned_end
min_duration
typical_duration
max_duration
time_period
reason
guard_result
decision
next_activity_hint
```

不要保存 hidden chain of thought。

只保存：

```text
structured decision facts
```

v1.0 明确要求 Decision 可以解释，但不保存隐藏思维链。

---

# 二十三、Reason Code

至少：

```text
BEFORE_END
TRANSITION_WINDOW
MAX_DURATION
MIN_DURATION_GUARD
BOUNCE_GUARD
TASK_STARTED
TASK_COMPLETED
TASK_FAILED
TASK_INTERRUPTED
USER_INTERACTION
WORLD_EVENT
RECOVERY
NO_VALID_TRANSITION
```

不要写：

```text
because_i_felt_like_it
```

---

# 二十四、Decision Pipeline

严格顺序：

```text
1. load current Episode
2. validate Episode
3. determine current time
4. check hard interruption
5. check min duration
6. check max duration
7. check transition window
8. evaluate extension
9. evaluate bounce guard
10. evaluate transition
11. create Decision Trace
12. execute only ActivityRuntime lifecycle mutation
13. persist
14. publish event
```

不要：

```text
LLM first
then check safety
```

必须：

```text
hard rules first
```

---

# 二十五、Decision Engine 不拥有世界写权限

必须建立架构守卫：

```text
ActivityDecisionEngine
```

不能 import：

```text
MinecraftService mutation API
ActionRuntime
Minecraft tools
```

不能直接调用：

```text
move_to
follow_player
dig
place
pickup
craft
container_transfer
```

建议加入 AST / source guard。

---

# 二十六、Task 与 Decision

Task 仍然优先。

例如：

```text
Activity = gaming
Task starts
```

Activity Decision 不允许说：

```text
继续 gaming
```

Task adapter 应发：

```text
TASK_STARTED
```

Activity 立即收敛：

```text
gaming
→ minecraft_task
```

由现有 Task ↔ Activity adapter 完成。

6B 不复制这一逻辑。

---

# 二十七、Task 完成

Task：

```text
SUCCEEDED
```

Activity：

```text
minecraft_task
```

进入：

```text
COMPLETED
```

然后：

```text
Decision Engine
```

才决定下一个 activity。

不能：

```text
Task completed
→ LLM
→ execute next activity
```

---

# 二十八、Pause

当：

```text
Task PAUSED
```

Activity 已由 6A 映射成：

```text
INTERRUPTED
```

6B 不得再次创建：

```text
another episode
```

不得重复发布：

```text
activity.interrupted
```

应证明：

```text
event idempotency
```

---

# 二十九、Recovery

Recovery 时：

```text
existing Episode
```

优先。

不要因为：

```text
now > planned_end
```

马上新建活动。

流程：

```text
load
→ reconcile
→ consistency check
→ if valid: continue
→ if expired: decision
→ create next episode
```

---

# 三十、Unknown Activity

允许：

```text
unknown
```

但只能用于短暂 recovery。

不得：

```text
unknown
```

长期 ACTIVE。

如果 Planner 失败：

优先：

```text
CONTINUE current
```

或：

```text
routine fallback
```

不能：

```text
activity = null
```

这是 v1.0 §125–§127 明确规定的 fallback 方向。

---

# 三十一、Fallback Activity

至少准备：

```text
idle
free_time
resting
```

当没有合适 next activity：

不要让：

```text
current activity = null
```

而应该进入低语义活动。

---

# 三十二、Duration Profile

确保每种 Activity 有：

```text
min
typical
max
```

并且：

```text
0 < min <= typical <= max
```

非法配置必须在启动时被拒绝。

---

# 三十三、时间模型

必须继续使用 6A WorldClock。

测试使用：

```text
FakeClock
```

禁止：

```text
sleep()
```

制造时间。

---

# 三十四、快进测试

至少支持：

```python
clock.advance(minutes=1)
clock.advance(minutes=5)
clock.advance(hours=1)
```

并通过：

```text
ActivityRuntime.tick()
```

让 Episode 自然推进。

不能：

```text
模拟器直接修改 status
```

---

# 三十五、关键快进场景

### 1 小时

期望：

```text
少量 Episode transitions
```

不是：

```text
60 events
```

### 3 天

期望：

```text
合理 Episode 数量
```

不是：

```text
每分钟一个 activity
```

这是 v1.0 §70–§72 的核心验收思路。

---

# 三十六、Transition Window 测试

例如：

```text
planned_end = 15:30
window = 5m
```

15:24：

```text
transition_pending = false
```

15:25：

```text
transition_pending = true
```

但：

```text
Episode.status = ACTIVE
```

15:29：

仍然：

```text
ACTIVE
```

15:30：

触发：

```text
Decision
```

---

# 三十七、Decision Frequency

必须证明：

```text
60 second tick
```

不会：

```text
LLM call
```

不会：

```text
new episode
```

不会：

```text
multiple decisions
```

在一个 transition 中：

```text
最多一次 Decision
```

---

# 三十八、并发

处理：

```text
tick
+
task completion
+
user interaction
```

同时到达。

必须保证：

```text
one current Episode
one terminal transition
one decision
```

使用：

```text
transaction
state guard
CAS
lock
```

复用已有机制。

---

# 三十九、Decision 事件

建议：

```text
activity.decision
activity.extended
activity.transition_requested
```

但不要大量增加 Event。

最少满足：

```text
decision trace
```

可审计即可。

---

# 四十、Event Idempotency

同一个：

```text
episode_id
decision window
```

不得产生重复 Decision。

尤其：

```text
process restart
tick retry
event retry
```

都不得造成：

```text
EXTEND × 3
```

---

# 四十一、Memory

6B 默认：

```text
DO NOT WRITE MEMORY
```

除非现有 Activity Memory Hook 已经需要记录：

```text
important transition
```

但第一版建议：

```text
Decision Trace != Memory
```

保持独立。

否则几十分钟一次活动切换，很快污染 Memory。

---

# 四十二、Social Cognition

只能读取：

```text
current Episode
recent transitions
decision trace summary
```

不要修改：

```text
Episode
```

也不要通过：

```text
Social Cognition
```

触发 Activity transition。

它只能提供 context。

---

# 四十三、QQ

QQ 可以：

```text
你现在干嘛？
```

得到：

```text
当前 activity
```

但：

```text
“你别玩了”
```

不要让 6B 自己处理。

如果需要用户控制真正任务：

走：

```text
TaskRuntime
```

如果只是角色世界中的 activity：

本阶段暂不开放 QQ 强制管理。

---

# 四十四、Minecraft Observation

继续保持：

```text
observation only
```

允许：

```text
当前 Minecraft 在线
附近玩家
位置
Task 状态
当前 Minecraft activity
```

用于：

```text
consistency
decision context
```

禁止：

```text
observation
→ action
```

---

# 四十五、真实 Minecraft 门禁

本阶段不要求新的世界修改动作。

但是必须真实连接 Java Server。

## Real A

真实运行一个 Minecraft Task：

```text
Task RUNNING
→ Activity minecraft_task
```

完成：

```text
Activity COMPLETED
```

PASS。

## Real B

Task 运行中：

```text
QQ → 暂停
```

必须：

```text
task.paused
→ activity.interrupted
```

**这次必须真正现场观察实时路径。**

这正好补掉你 6A 报告里唯一尚未单独实测的路径。

PASS。

## Real C

Task resume：

```text
activity
→ new valid Activity
```

不能产生重复 active Episode。

PASS。

## Real D

Episode 接近 planned_end：

真实 World Tick：

```text
transition_pending
```

但：

```text
没有世界动作
```

PASS。

---

# 四十六、Real QQ

至少：

```text
你现在在干嘛？
```

得到：

```text
当前 Episode
```

然后：

```text
确认 / 暂停 / 继续 / 停止
```

仍然遵循 5B 的 Task 控制语义。

---

# 四十七、WebUI

增加只读 Decision Inspector：

```text
Current Activity
Elapsed
Planned End
Transition Window
Decision
Reason
Next Hint
Extension Count
Last Decision
```

不得加入：

```text
Force Decision
Force Transition
Extend
Cancel
```

全部只读。

---

# 四十八、Decision Trace API

增加只读：

```text
GET /api/v1/world/activity/decision
```

或者复用现有 world/activity API。

返回：

```json
{
  "episode_id": "...",
  "decision": "CONTINUE",
  "reason": "BEFORE_END",
  "transition_pending": false,
  "elapsed_seconds": 1200,
  "planned_end_at": "...",
  "extension_count": 0
}
```

禁止返回：

```text
chain_of_thought
```

---

# 四十九、性能门禁

普通 Tick：

```text
必须低成本
```

不得：

```text
每 tick 扫描所有历史 Episode
每 tick 查询整个 Memory
每 tick 调用 LLM
每 tick 查询 Minecraft 全世界
```

目标：

```text
O(1) current episode checks
```

或接近。

---

# 五十、测试文件

至少新增：

```text
tests/test_activity_decision.py
tests/test_activity_transition_window.py
tests/test_activity_bounce_guard.py
tests/test_activity_consistency.py
tests/test_activity_merge.py
tests/test_activity_decision_trace.py
```

并更新：

```text
tests/test_activity_runtime.py
tests/test_activity_recovery.py
tests/test_task_runtime.py
```

---

# 五十一、测试矩阵

必须覆盖：

### A
CONTINUE

### B
EXTEND

### C
TRANSITION

### D
min duration guard

### E
max duration guard

### F
transition window

### G
bounce guard

### H
adjacent merge

### I
consistency checker

### J
invalid episode

### K
recovery

### L
duplicate decision

### M
tick idempotency

### N
task completion

### O
task pause

### P
task failure

### Q
task cancellation

### R
Minecraft offline

### S
no world mutation

### T
no LLM during ordinary tick

### U
fallback after planner failure

### V
unknown recovery only

### W
3-day fast-forward

---

# 五十二、源码安全门禁

必须新增 AST/source guard：

```text
ActivityDecisionEngine
ActivityRuntime
ActivityPlanner
ActivityBounceGuard
WorldConsistencyChecker
```

不得 import/call：

```text
MinecraftService mutation
ActionRuntime mutation
minecraft tools
TaskRuntime.confirm_and_start
ConfirmationStore.consume
Policy bypass
```

允许：

```text
Task observation
WorldPerception read
Memory read
CharacterState read
```

---

# 五十三、配置

只增加真正必要的：

```yaml
world:
  activity:
    transition_window_minutes: 5
    max_extensions_per_episode: 2
    bounce_cooldown_minutes: 10
```

不要加入：

```text
activity_randomness
activity_temperature
activity_personality_weight
activity_autonomy_probability
```

6B 不做这些。

---

# 五十四、文档

新增：

```text
docs/MINECRAFT_PHASE6B.md
```

至少记录：

1. Decision Engine
2. Transition Window
3. Continue
4. Extend
5. Transition
6. Minimum / Maximum Duration
7. Bounce Guard
8. Merge
9. Consistency Checker
10. Decision Trace
11. Task Integration
12. Minecraft observation boundary
13. Security boundary
14. Real QQ
15. Real Java
16. Fast-forward simulation
17. Known limitations

更新：

```text
CHANGELOG.md
docs/README.md
```

---

# 五十五、绝对禁止

6B 不做：

- LLM Activity Decision
- Autonomous Minecraft action
- 自动挖矿
- 自动采集
- 自动探索
- 自动建造
- 自动跟随
- 自动返回基地
- 自动生成 Minecraft Task
- 自动确认 Task
- 自动降低 Policy
- 自主 QQ 消息
- 自主社交
- 新 Minecraft tool
- 新 ActionRuntime action
- 新 TaskRuntime state

---

# 五十六、硬门禁

最终：

```text
Code                    PASS
Unit                    PASS
Integration             PASS
Security                PASS
CI                      PASS

Continue                PASS
Extend                  PASS
Transition              PASS
Transition Window       PASS
Duration Guards         PASS
Bounce Guard            PASS
Activity Merge          PASS
Consistency Checker     PASS
Decision Trace          PASS
Recovery                PASS
Task Binding            PASS
Minecraft Observation   PASS
Minecraft Offline       PASS
No World Mutation       PASS
No LLM per Tick         PASS
Real Java               PASS
Real QQ                 PASS
Fast Forward            PASS
```

任何核心门禁失败：

```text
PHASE 6B = BLOCKED
```

不能用纯单测替代 Real Java / Real QQ 门禁。

---

# 五十七、最终报告格式

```text
# PHASE 6B FINAL REPORT

PHASE 6B = PASS / BLOCKED

Commit:
CI:

Continue:
PASS / FAIL

Extend:
PASS / FAIL

Transition:
PASS / FAIL

Transition Window:
PASS / FAIL

Duration Guards:
PASS / FAIL

Bounce Guard:
PASS / FAIL

Activity Merge:
PASS / FAIL

Consistency Checker:
PASS / FAIL

Decision Trace:
PASS / FAIL

Recovery:
PASS / FAIL

Task Binding:
PASS / FAIL

Minecraft Observation:
PASS / FAIL

Minecraft Offline:
PASS / FAIL

No World Mutation:
PASS / FAIL

No LLM Per Tick:
PASS / FAIL

Real Java:
PASS / FAIL

Real QQ:
PASS / FAIL

Fast Forward:
PASS / FAIL

Real Evidence:
...

Skipped:
...

Known Limitations:
...

No New Minecraft Tools:
PASS

No New ActionRuntime Actions:
PASS

No New TaskRuntime States:
PASS

allow_medium unchanged:
PASS
```

# 五十八、6B 的最终能力边界

完成后：

```text
World Clock
    ↓
Episode
    ↓
Transition Window
    ↓
Decision Engine
    ↓
Continue / Extend / Transition
    ↓
Next Episode
```

此时罐头已经不仅知道：

> “我现在在玩 Minecraft。”

而是知道：

> “这个活动原计划什么时候结束，现在应该继续、延长，还是准备换活动。”

但她依旧**不会因为这个决定直接走进 Minecraft 世界执行任何事情**。

这条边界必须保持到后续阶段。