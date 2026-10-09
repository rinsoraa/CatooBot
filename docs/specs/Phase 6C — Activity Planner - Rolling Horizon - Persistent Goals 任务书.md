# CatooBot Phase 6C
# Activity Planner / Rolling Horizon / Persistent Goals

## 0. 阶段目标

Phase 6A：

```text
ActivityEpisode
```

已经解决：

> “她现在在做什么？”

Phase 6B：

```text
ActivityDecisionEngine
```

已经解决：

> “当前活动继续、延长还是切换？”

Phase 6C：

> **解决“下一段活动是什么，以及未来几个小时大致怎么安排”。**

最终架构：

```text
World Clock
      ↓
Current Character State
      ↓
Routine
      +
Schedule Anchors
      +
Persistent Goals
      +
Activity History
      +
Character Preferences
      ↓
ActivityPlanner
      ↓
Rolling Horizon
      ↓
ActivityEpisode
```

必须遵守：

**Phase 6C 仍然不执行任何 Minecraft 世界动作。**

---

# 一、最高优先级原则

6C 只负责：

```text
planning
ranking
scheduling
episode generation
```

不负责：

```text
Minecraft action
Task execution
Confirmation
Policy
QQ autonomous messaging
ActionRuntime
```

禁止：

```text
ActivityPlanner
→ move_to
ActivityPlanner
→ dig
ActivityPlanner
→ pickup
ActivityPlanner
→ TaskRuntime.confirm_and_start()
```

Planner 只产生：

```text
next activity proposal
```

然后交给：

```text
ActivityRuntime
```

创建新的 Episode。

---

# 二、严格复用已有 6A / 6B

首先完整检查：

```text
app/activity/
app/world/
app/character/
app/tasks/
app/memory/
```

以及：

```text
docs/specs/v1.0.md
```

重点复用：

```text
ActivityEpisode
ActivityRuntime
ActivityDecisionEngine
WorldClock
CharacterState
Routine
Memory
Task Adapter
ConsistencyChecker
```

禁止建立：

```text
ActivityPlannerV2
MinecraftPlanner
QQPlanner
LifePlanner
```

等第二套 Planner。

---

# 三、ActivityPlanner

新增或升级：

```text
app/activity/planner.py
```

核心接口建议：

```python
plan_next(
    character_state,
    current_episode,
    now,
    horizon,
    context,
) -> ActivityPlan
```

如果仓库已有 Planner 接口：

优先兼容已有接口，而不是新造 API。

---

# 四、ActivityPlan

建议：

```python
ActivityPlan(
    plan_id,
    generated_at,
    horizon_start,
    horizon_end,
    candidates,
    selected,
    constraints,
    source,
)
```

至少能够表达：

```text
planned activity
planned start
planned end
reason
priority
anchor relation
goal relation
```

不要把未来计划当成当前事实。

---

# 五、Future Schedule 与 Current Activity 必须分离

必须明确：

```text
current_activity
```

和：

```text
planned_activity
```

不是一回事。

例如：

```text
Current:
gaming

Future plan:
rest 15:30
music 15:50
```

即使当前 gaming 延长：

```text
Future plan
```

可以重新规划。

不能直接把：

```text
planned_activity
```

当成：

```text
current_activity
```

这一点与 v1.0 §33 一致。([github.com](https://github.com/rinsoraa/CatooBot/blob/165f260/docs/specs/v1.0.md))

---

# 六、Rolling Horizon

新增配置：

```yaml
world:
  activity:
    planning_horizon_minutes: 240
```

默认：

```text
240
```

即：

```text
4 hours
```

Planner 只需要保证：

```text
未来 1–4 小时
```

有合理的 Activity 规划。

绝对禁止：

```text
一次规划 24 小时
```

或：

```text
生成全天完整时间表
```

v1.0 明确要求 rolling horizon。([github.com](https://github.com/rinsoraa/CatooBot/blob/165f260/docs/specs/v1.0.md))

---

# 七、最小 Planning Horizon

允许配置：

```text
60 ~ 720 minutes
```

推荐默认：

```text
240
```

超过范围直接 config validation error。

---

# 八、Planning 不等于每 Tick 重算

Planner 不能：

```text
每分钟
→ plan_next()
```

触发点至少应该是：

```text
1. 当前 Episode 结束
2. 当前计划快耗尽
3. Schedule Anchor 发生变化
4. Persistent Goal 发生变化
5. Character State 重大变化
6. User interaction
7. Recovery
8. Manual admin refresh
```

普通 Tick：

```text
不重新规划
```

---

# 九、Plan Refresh Cooldown

增加内部：

```text
last_planned_at
```

并设置最短刷新间隔：

```yaml
planner_refresh_min_minutes: 5
```

默认 5。

即：

```text
普通 Tick
+
没有重大触发
```

不得重新计算整个 Horizon。

---

# 十、Routine

必须检查项目现有 Routine。

如果现有 Routine：

```text
routine profiles
```

优先复用。

Routine 的角色：

```text
偏好 / 倾向 / 时间习惯
```

不是：

```text硬编码状态机
```

---

# 十一、Routine 不直接创建 Episode

必须：

```text
Routine
   ↓
ActivityPlanner
   ↓
ActivityPlan
   ↓
ActivityEpisode
```

禁止：

```text
Routine
   ↓
ActivityRuntime.start(...)
```

v1.0 §29 明确规定此边界。([github.com](https://github.com/rinsoraa/CatooBot/blob/165f260/docs/specs/v1.0.md))

---

# 十二、ScheduleAnchor

增加：

```python
ScheduleAnchor(
    id,
    activity,
    target_time,
    window_before,
    window_after,
    priority,
    hard,
)
```

例如：

```text
lunch:
target = 12:00
window = ±45m
priority = high
```

---

# 十三、Anchor 优先级

默认：

```text
sleep
meal
fixed event
routine activity
free activity
```

从高到低。

与 v1.0 §38 保持一致。([github.com](https://github.com/rinsoraa/CatooBot/blob/165f260/docs/specs/v1.0.md))

---

# 十四、Hard Anchor

Hard Anchor：

```text
sleep
meal
mandatory event
```

优先级最高。

但注意：

不是：

```text
12:00:00
强制切 Activity
```

而是：

```text
anchor window
+
transition guard
```

保持 6B 的弹性机制。

---

# 十五、Flexible Anchor

例如：

```text
lunch
window ±45m
```

Planner 应当避免：

```text
gaming ends at 11:59
lunch
gaming again at 12:01
```

应合理规划：

```text
gaming
→ lunch
```

---

# 十六、Free Activity Pool

自由时段至少有：

```text
free_time
gaming
music
watching_show
reading
relax
```

如果项目已有活动池：

复用。

否则建立最小 deterministic pool。

---

# 十七、一次选择后必须形成 Episode

例如：

```text
Planner:
gaming
15:00–16:00
```

必须：

```text
ActivityRuntime
→ ActivityEpisode
```

不能每分钟重新说：

```text
“继续 gaming?”
```

Planner 的选择具有 Episode 连续性。

---

# 十八、Activity Profile

建立或复用：

```python
ActivityProfile(
    activity,
    category,
    min_duration,
    typical_duration,
    max_duration,
    flexibility,
    anchor_compatibility,
    energy_cost,
    focus_cost,
    social_preference,
)
```

其中真正必须支持的第一批字段：

```text
min
typical
max
flexibility
```

其余如果已有模型则复用。

不要一次引入复杂人格数学。

---

# 十九、Fixed / Flexible / Free

每个 Activity 至少有：

```text
kind:
fixed
flexible
free
```

例如：

```text
sleep      fixed
meal       fixed
Minecraft  flexible
music      free
reading    free
```

这是 Planner 候选筛选的重要输入。

---

# 二十、Persistent Goal

建立：

```python
PersistentGoal(
    id,
    title,
    description,
    priority,
    progress,
    status,
    created_at,
    updated_at,
)
```

至少：

```text
ACTIVE
PAUSED
COMPLETED
CANCELLED
```

---

# 二十一、Goal 不等于 Task

必须严格分离：

```text
PersistentGoal:
“完成虚拟 Minecraft 建造项目”
```

和：

```text
Task:
“帮 Rinsora 去挖 1 个橡木”
```

Goal：

```text
长期方向
```

Task：

```text
当前具体执行
```

不得把 Goal 直接转成 Minecraft action。

---

# 二十二、Goal → Activity 只是优先级影响

例如：

```text
Goal:
完成 Minecraft 项目

Candidates:
building
gaming
watching_show
```

Focus 高时：

```text
building
```

排名可以提高。

但：

```text
Goal
≠
必须 building
```

v1.0 §108/§109 明确要求 Persistent Goal 不得强行覆盖生活。([github.com](https://github.com/rinsoraa/CatooBot/blob/165f260/docs/specs/v1.0.md))

---

# 二十三、Goal 不得覆盖生理约束

即使：

```text
goal priority = 100
```

也不能：

```text
24h building
```

必须服从：

```text
sleep
meal
rest
energy
```

这类硬生活约束。

---

# 二十四、Goal Progress

第一版只需要：

```text
0.0 ~ 1.0
```

或：

```text
0 ~ 100
```

二选一。

必须统一。

不要两个体系并存。

推荐：

```text
0.0 ~ 1.0
```

---

# 二十五、Goal Completion

Goal 完成条件可以暂时：

```text
manual
task result
activity result
external event
```

但不能：

```text
LLM 随便说完成
```

必须有结构化来源。

---

# 二十六、Character State 输入

Planner 至少读取：

```text
energy
focus
mood
time_period
sleep_state
social_state
current_topic
```

如果现有 Character State 字段名字不同：

适配，不重复创建。

---

# 二十七、Energy / Focus

第一版不做复杂动力学。

至少允许：

```text
energy 0.0~1.0
focus 0.0~1.0
```

Planner 使用：

```text
hard constraints
+
soft ranking
```

---

# 二十八、Energy Hard Rules

至少：

```text
energy very low
→ sleep / rest / light activity
```

不能：

```text
energy=0.15
→ gaming 2 hours
```

除非角色 profile 明确允许。

这是 v1.0 §110 的要求。([github.com](https://github.com/rinsoraa/CatooBot/blob/165f260/docs/specs/v1.0.md))

---

# 二十九、Focus

Focus 高：

更容易：

```text
reading
building
coding
Minecraft project
```

Focus 低：

更容易：

```text
music
show
relax
```

但这只是：

```text
ranking signal
```

不是硬动作。

---

# 三十、Activity History

Planner 至少读取：

```text
recent episodes <= 5
```

考虑：

```text
last_activity
last_transition
recent repetition
```

防止：

```text
gaming
rest
gaming
rest
gaming
```

无意义震荡。

6B Bounce Guard 仍然是最终护栏。

---

# 三十一、Planner 与 6B 的关系

6C：

```text
Planner
→ next candidate
```

6B：

```text
Decision Engine
→ 是否现在真的允许切换
```

因此：

```text
Planner says “gaming”
```

不等于：

```text
current activity immediately becomes gaming
```

必须经过：

```text
ActivityDecisionEngine
+
ActivityRuntime
```

---

# 三十二、Planner 不得绕过 Transition Guard

例如 Planner：

```text
next=sleep
```

但：

```text
current gaming
elapsed=3min
min_duration=20min
```

最终：

```text
CONTINUE
```

不是：

```text
TRANSITION
```

Planner 永远服从 6B。

---

# 三十三、Candidate Generation

Planner 至少生成：

```text
3~6 candidates
```

例如：

```text
sleep
reading
gaming
music
relax
```

候选数量必须有上限。

不要扫描全项目所有活动。

---

# 三十四、Candidate Rejection

每个 candidate 应有：

```text
eligible
rejected
reason
```

例如：

```text
gaming
eligible=false
reason=ENERGY_TOO_LOW
```

---

# 三十五、Candidate Ranking

每个 eligible candidate：

```text
score
```

但：

**score 不是 probability。**

不要：

```text
random threshold
```

只用于：

```text
deterministic ranking
```

v1.0 §23 明确要求 structured decision，不使用 probability threshold。([github.com](https://github.com/rinsoraa/CatooBot/blob/165f260/docs/specs/v1.0.md))

---

# 三十六、Score 来源

建议：

```text
anchor fit
routine preference
current state fit
goal relevance
recent repetition penalty
activity flexibility
time-period fit
```

最终：

```text
score = weighted deterministic sum
```

权重配置化。

不要引入随机。

---

# 三十七、Tie Break

必须 deterministic。

如果：

```text
gaming score=0.82
music score=0.82
```

必须固定 tie-break：

```text
1. anchor priority
2. goal priority
3. routine priority
4. stable activity name
```

不要：

```python
random.choice(...)
```

---

# 三十八、Goal Relevance

Goal：

```text
building project
```

则：

```text
building
```

获得：

```text
goal relevance bonus
```

但：

```text
sleep
```

仍然可以超过它。

---

# 三十九、Routine Preference

Routine：

```text
night:
music > reading
```

应该影响 score。

但不要：

```text
routine
→ direct set_activity
```

---

# 四十、Time Period

至少：

```text
morning
day
evening
night
late_night
```

如果角色已有更细时间系统：

复用。

Planner 以 WorldClock 当前时间计算。

---

# 四十一、Rolling Horizon 输出

例如：

```text
14:00
gaming
14:00–15:20

15:20
rest
15:20–15:40

15:40
music
15:40–16:30
```

不需要：

```text
16:30–17:30
17:30–18:30
18:30–19:30
```

规划满一整天。

---

# 四十二、计划的可修订性

Future Schedule 不是事实。

当前世界变化：

```text
world event
task
user interaction
goal change
```

可以：

```text
invalidate future plan
```

当前 Episode 不应被 Planner 直接修改。

---

# 四十三、Plan Version

建议：

```text
plan_version
```

每次真正重新生成 Horizon：

```text
version += 1
```

相同输入没有实际变化：

不要无意义增加版本。

---

# 四十四、Plan Superseded

旧计划：

```text
SUPERSEDED
```

新计划：

```text
ACTIVE_PLAN
```

历史保留。

不能：

```text
delete old plan
```

---

# 四十五、Plan Persistence

如果已有 schedule store：

复用。

否则可以新增：

```text
activity_plans
activity_plan_items
```

但先检查现有数据库。

不要出现：

```text
future_schedule
activity_plan
routine_schedule
```

三套重叠存储。

---

# 四十六、Plan 与 Episode 的关系

一个 Plan：

```text
plan
```

可以包含：

```text
多个 future episode proposals
```

实际运行后：

```text
proposal
→ ActivityEpisode
```

Episode 才是：

```text
CURRENT REALITY
```

Plan 只是：

```text
intention
```

---

# 四十七、Recovery

重启后：

```text
load current Episode
load active plan
```

优先：

```text
current reality
```

而不是：

```text
future plan
```

如果：

```text
active plan stale
```

可以重新规划。

不要：

```text
blind resume future schedule
```

---

# 四十八、Planner Failure

如果 Planner 失败：

不得：

```text
activity = null
```

必须：

```text
continue current
```

或者：

```text
routine fallback
```

例如：

```text
idle
resting
free_time
```

这与 v1.0 §125/§126 一致。([github.com](https://github.com/rinsoraa/CatooBot/blob/165f260/docs/specs/v1.0.md))

---

# 四十九、No Valid Candidate

如果：

```text
0 eligible
```

fallback：

```text
free_time
```

或者：

```text
resting
```

取决于：

```text
energy
time period
```

必须 deterministic。

---

# 五十、LLM

**6C 仍然不接 LLM。**

不要：

```text
Planner
→ LLM
→ final activity
```

未来模型只能：

```text
Advisor
```

不能：

```text
Authority
```

---

# 五十一、Model Interface 预留

如果现有 v1.0 设计要求：

```python
planner.advisor
```

可以保留：

```text
None
```

6C 不实现实际模型。

---

# 五十二、Social Cognition

Social Cognition 可以作为 planner context：

```text
current social context
recent interactions
```

但：

```text
Social Cognition
```

不能直接：

```text
set_activity()
```

只能影响 candidate ranking。

---

# 五十三、Memory

允许读取：

```text
Minecraft Memory
General Memory
```

但只取：

```text
relevant preferences
recent relevant experiences
```

默认：

```text
≤5 memories
```

不要把整个 memory DB 给 Planner。

---

# 五十四、Memory 不得成为权限

记忆：

```text
“RinsoraNeko 喜欢一起玩 Minecraft”
```

只能影响：

```text
social preference score
```

不能：

```text
grant permission
```

不能改变：

```text
allow_medium
```

---

# 五十五、Minecraft

6C 可以知道：

```text
Minecraft online
recent Minecraft task
recent Minecraft memory
player present
```

但 Planner 不得：

```text
自动创建 Minecraft Task
```

例如：

```text
next activity = minecraft
```

只能是：

```text
ActivityEpisode activity = minecraft
```

当前阶段甚至不要求真正进入 Minecraft 世界。

---

# 五十六、Minecraft Activity 与真实 Minecraft

如果当前：

```text
Minecraft Activity
```

没有：

```text
related_task_id
```

就不能声称：

```text
角色已经在现实 Minecraft 世界里行动。
```

必须继续遵守 6A 的 observation boundary。

---

# 五十七、Goal / Task / Activity 三层

最终必须清楚：

```text
Goal
=
长期想完成什么

Activity
=
当前时间段在干什么

Task
=
实际执行的一件事情
```

例如：

```text
Goal:
完成 Minecraft 建筑

Activity:
Minecraft

Task:
收集 8 个橡木
```

三层不能互相替代。

---

# 五十八、WebUI Planner Debug

管理员只读查看：

```text
Current Activity
Candidates
Rejected Candidates
Scores
Constraints
Goal Relevance
Routine Preference
Anchor
Selected
Plan Horizon
Plan Version
```

不显示：

```text
Chain of Thought
```

也不能：

```text
Force select
```

第一版只读。

---

# 五十九、QQ

QQ 可以问：

```text
你接下来准备干嘛？
```

返回：

```text
planned next activity
```

但：

```text
这是计划，不是当前状态。
```

例如：

```text
“我现在还在玩 Minecraft，计划结束后先休息一下。”
```

如果 current 与 plan 不一致：

```text
current > future plan
```

不能混淆。

---

# 六十、Real QQ 补门禁

6B 唯一 skipped：

```text
“你现在在干嘛？”
```

本阶段顺便真实验证：

### QQ A

```text
你现在在干嘛？
```

必须返回：

```text
current ActivityEpisode
```

### QQ B

```text
你接下来准备干嘛？
```

必须返回：

```text
planned next activity
```

而且必须能够证明：

```text
current != planned
```

时不会把 planned 冒充 current。

---

# 六十一、真实 Java 门禁

### Real Java A

真实 Minecraft 在线：

```text
Planner context
```

能够识别：

```text
minecraft online
```

但不执行动作。

PASS。

### Real Java B

真实 Task 进行：

```text
Activity = minecraft_task
```

Task completed：

```text
Planner receives completion
```

能够重新规划。

PASS。

### Real Java C

Minecraft offline：

Planner：

```text
不能生成“我正在 Minecraft 里”
```

但可以生成：

```text
virtual minecraft interest
```

PASS。

### Real Java D

真实 world event：

不得绕过：

```text
TaskRuntime
```

直接产生 Minecraft action。

PASS。

---

# 六十二、Fast Forward

必须支持：

```text
FakeClock.advance(hours=1)
```

例如：

```text
08:00
09:00
10:00
11:00
12:00
```

Planner 不应该：

```text
每分钟重新生成完整 horizon
```

应该：

```text
在必要 transition point
重新规划
```

---

# 六十三、3 Day Simulation

至少模拟：

```text
3 days
```

检查：

```text
Episode count
Plan count
Goal progress
Anchor adherence
No bounce explosion
No empty primary activity
```

不能产生：

```text
几千个 plans
```

或：

```text
每分钟一个 episode
```

---

# 六十四、Determinism

相同：

```text
time
state
routine
goal
history
```

必须：

```text
same candidates
same score
same selected activity
same plan
```

连续运行两次：

结果必须一致。

---

# 六十五、Planner Performance

普通 Tick：

```text
O(1)
```

Planner refresh：

只在：

```text
triggered
```

时运行。

不要：

```text
tick → scan all memory
tick → scan all history
tick → recompute all goals
```

---

# 六十六、Security Guard

ActivityPlanner：

禁止 import/call：

```text
MinecraftService mutation
ActionRuntime
Minecraft tools
TaskRuntime.confirm_and_start
ConfirmationStore
Policy
AgentBridge execute
```

允许：

```text
World read
Character read
Memory read
Task read-only
```

增加 AST guard。

---

# 六十七、数据库安全

如果新增表：

必须：

```text
transactional
idempotent
```

并且：

```text
old install
→ migrate
```

没有旧数据时：

正常启动。

---

# 六十八、配置

本阶段最多增加：

```yaml
world:
  activity:
    planning_horizon_minutes: 240
    planner_refresh_min_minutes: 5
```

以及如果确有必要：

```yaml
world:
  activity:
    max_future_episodes: 6
```

默认不要超过：

```text
6
```

不要增加几十个 tuning knobs。

---

# 六十九、测试文件

至少：

```text
tests/test_activity_planner.py
tests/test_activity_planning_horizon.py
tests/test_activity_goals.py
tests/test_activity_anchors.py
tests/test_activity_candidate_ranking.py
tests/test_activity_plan_persistence.py
tests/test_activity_plan_recovery.py
```

以及更新：

```text
tests/test_activity_runtime.py
tests/test_activity_decision.py
tests/test_activity_recovery.py
tests/test_activity_consistency.py
```

---

# 七十、测试矩阵

必须覆盖：

### A
Candidate generation

### B
Candidate rejection

### C
Deterministic ranking

### D
Tie break

### E
Routine influence

### F
Anchor priority

### G
Flexible anchor

### H
Free activity

### I
Goal influence

### J
Goal cannot override rest

### K
Energy constraint

### L
Focus influence

### M
History penalty

### N
Rolling horizon

### O
Plan version

### P
Plan superseded

### Q
Plan persistence

### R
Plan recovery

### S
Planner failure fallback

### T
No valid candidate

### U
Same input deterministic

### V
Task completion refresh

### W
Minecraft offline

### X
Minecraft observation only

### Y
No world mutation

### Z
No LLM during planning

### AA
3-day fast-forward

---

# 七十一、Real Evidence

最终必须至少拿到：

```text
REAL JAVA
A Planner context
B Task → replanning
C Minecraft offline
D no action bypass
```

以及：

```text
REAL QQ
A current activity
B next planned activity
```

---

# 七十二、Real QQ 必须区分 Current 与 Planned

必须特别测试：

```text
Current:
minecraft_task

Planned:
resting
```

QQ：

```text
你现在在干嘛？
```

回答：

```text
我现在还在处理 Minecraft 任务。
```

而：

```text
你接下来准备干嘛？
```

回答：

```text
计划完成后休息一下。
```

不能把两者混成：

```text
我现在正在休息。
```

---

# 七十三、6B Regression

必须跑：

```text
6B full suite
```

证明：

```text
Planner
```

没有破坏：

```text
Continue
Extend
Transition
Bounce
Task Binding
Recovery
```

尤其：

```text
Planner proposal
```

不能越过：

```text
6B hard guards
```

---

# 七十四、6A Regression

必须确认：

```text
ActivityEpisode
Recovery
Primary uniqueness
```

全部不回归。

---

# 七十五、5A / 5B / 5C Regression

必须保持：

```text
TaskRuntime
QQ Task Entry
Identity
Memory
```

全部 green。

---

# 七十六、最终安全不变量

必须始终：

```text
1. Planner ≠ Executor
2. Plan ≠ Reality
3. Goal ≠ Task
4. Activity ≠ Task
5. Memory ≠ Permission
6. Observation ≠ Action
7. Future Schedule ≠ Current Activity
```

---

# 七十七、绝对禁止

Phase 6C 不做：

- LLM Planner
- Autonomous Minecraft action
- 自动创建 Minecraft Task
- 自动确认 Minecraft Task
- 自动 dig
- 自动 move
- 自动 pickup
- 自动 craft
- 自动 build
- 自主 QQ
- Autonomous Social Cognition
- 新 Minecraft tool
- 新 ActionRuntime action
- 新 TaskRuntime state
- allow_medium 修改

---

# 七十八、文档

新增：

```text
docs/MINECRAFT_PHASE6C.md
```

必须记录：

1. ActivityPlanner
2. Rolling Horizon
3. Current vs Future
4. Routine
5. Schedule Anchors
6. Fixed / Flexible / Free
7. Candidate Generation
8. Candidate Ranking
9. Persistent Goals
10. Energy / Focus
11. Plan Persistence
12. Recovery
13. Task Integration
14. Minecraft Observation
15. Security
16. Real Java
17. Real QQ
18. 3-day Fast Forward
19. Known Limitations

更新：

```text
CHANGELOG.md
docs/README.md
```

---

# 七十九、硬门禁

最终：

```text
Code                      PASS
Unit                      PASS
Integration               PASS
Security                  PASS
CI                        PASS

Planner                   PASS
Rolling Horizon           PASS
Routine                   PASS
Anchors                   PASS
Candidate Ranking         PASS
Persistent Goal           PASS
Energy / Focus            PASS
Plan Persistence          PASS
Plan Recovery             PASS
Determinism               PASS
Fast Forward              PASS

6A Regression             PASS
6B Regression             PASS
5A/5B/5C Regression       PASS

Real Java                 PASS
Real QQ Current Activity  PASS
Real QQ Planned Activity  PASS

No World Mutation         PASS
No LLM                    PASS
No Task Bypass            PASS
```

任意核心 Real Java / Real QQ 门禁失败：

```text
PHASE 6C = BLOCKED
```

---

# 八十、最终报告格式

```text
# PHASE 6C FINAL REPORT

PHASE 6C = PASS / BLOCKED

Commit:
CI:

Planner:
PASS / FAIL

Rolling Horizon:
PASS / FAIL

Routine:
PASS / FAIL

Anchors:
PASS / FAIL

Candidate Ranking:
PASS / FAIL

Persistent Goals:
PASS / FAIL

Energy / Focus:
PASS / FAIL

Plan Persistence:
PASS / FAIL

Plan Recovery:
PASS / FAIL

Determinism:
PASS / FAIL

Fast Forward:
PASS / FAIL

6A Regression:
PASS / FAIL

6B Regression:
PASS / FAIL

5A/5B/5C Regression:
PASS / FAIL

Real Java:
PASS / FAIL

Real QQ Current:
PASS / FAIL

Real QQ Planned:
PASS / FAIL

No World Mutation:
PASS / FAIL

No LLM:
PASS / FAIL

No Task Bypass:
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

# 八十一、6C 的真正终点

完成后，系统应该从：

```text
6A:
我现在在做什么？
```

升级到：

```text
6B:
当前活动接下来怎么办？
```

再升级到：

```text
6C:
接下来几个小时，我大概准备做什么？
```

例如：

```text
现在：Minecraft
14:00–15:20

之后：
15:20–15:40 Rest

之后：
15:40–16:30 Music

之后：
16:30–17:30 Reading
```

但必须始终记住：

```text
这是 Plan
不是 Reality
```

真正的现实仍然只有：

```text
ActivityEpisode
```

而真正的世界动作仍然只有：

```text
TaskRuntime
→ Policy
→ Confirmation
→ AgentBridge
→ MinecraftService
→ ActionRuntime
```

6C 做完以后，**Phase 6D 才开始有资格引入“模型辅助 Activity Decision / Planner Advisor”**；再后面才是让自主 Activity 转化为真正 Minecraft Task 的那层。这样不会破坏你目前已经非常稳定的安全边界。