# CatooBot Phase 6A
# World Activity Runtime / Activity Episode Foundation

## 0. 阶段目标

Phase 5C / 5C.1 已完成：

- Minecraft Identity Bridge
- QQ ↔ Minecraft player binding
- Persistent Minecraft Memory
- World reconciliation
- Memory retrieval
- Memory failure isolation
- Minecraft task continuity
- QQ / Minecraft / WebUI unified task entry
- 19 个 Minecraft tools
- TaskRuntime
- ActionRuntime
- Confirmation / Policy / Authorization
- Real Java Server + Real QQ verification

现在开始：

# Phase 6A — World Activity Runtime / Activity Episode Foundation

目标：

> 将现有角色世界中的 `activity` 从“瞬时状态标签”升级为具有生命周期的 `ActivityEpisode`。

必须实现：

```text
World Clock
    ↓
Activity Episode
    ↓
Activity Runtime
    ↓
Character / World State projection
```

但：

**本阶段不得实现自主 Minecraft 行动。**

---

# 一、最高优先级原则

## 1. Activity Episode 是当前活动的唯一事实来源

最终必须形成：

```text
ActivityEpisode
    ↓
WorldState
    ↓
CharacterState
    ↓
Behavior / Social Cognition
```

而不是：

```text
ActivityEpisode = A
CharacterState.activity = B
```

产生两个相互矛盾的状态。

现有：

```python
activity
```

字段可以继续保留。

但它必须变成：

```text
current activity snapshot
```

而不是独立状态源。

---

# 二、先检查现有实现

开始修改之前，必须先完整检查仓库已有：

```text
app/world/
app/core/
character state
activity
behavior scheduler
initiative
task runtime
memory
timeline
database migrations
WebUI world/activity
```

以及：

```text
docs/specs/v0.8.md
docs/specs/v1.0.md
```

特别检查：

- 已存在的 Character State
- 已存在的 Activity
- 已存在的 World Runtime
- 已存在的 scheduler
- 已存在的 event/timeline
- 已存在的 persistence

如果已经存在对应能力：

**升级 / 复用，不得创建第二套平行系统。**

禁止出现：

```text
Old Activity System
+
New Activity System
```

也禁止：

```text
MinecraftActivityRuntime
+
GeneralActivityRuntime
```

两套互不相干的活动系统。

---

# 三、ActivityEpisode 数据模型

建立统一：

```python
ActivityEpisode(
    id,
    activity_type,
    activity_name,

    location,
    social_state,
    tags,

    started_at,
    planned_end_at,

    min_duration,
    typical_duration,
    max_duration,

    status,

    transition_reason,
    source,

    parent_episode_id,

    created_at,
    updated_at,
)
```

字段可以根据已有 schema 做适配，但语义必须保留。

---

# 四、Episode ID

必须具有稳定、可审计的唯一 ID。

例如：

```text
ACT-20261008-001
```

或者 UUID。

要求：

- 全局唯一
- 持久化
- 日志使用
- Event 可引用
- WebUI 可定位
- Recovery 后保持不变

不得使用：

```text
activity_name
```

作为唯一 ID。

---

# 五、Activity Status

至少：

```text
SCHEDULED
ACTIVE
EXTENDED
COMPLETED
INTERRUPTED
CANCELLED
EXPIRED
```

如果项目已经有 enum/state machine：

优先扩展已有状态模型。

不要创建多个状态 enum 表达同一生命周期。

---

# 六、状态转移

建立明确状态机：

```text
SCHEDULED
    ↓
ACTIVE
    ├──→ EXTENDED
    ├──→ COMPLETED
    ├──→ INTERRUPTED
    ├──→ CANCELLED
    └──→ EXPIRED

EXTENDED
    ├──→ EXTENDED
    ├──→ COMPLETED
    ├──→ INTERRUPTED
    ├──→ CANCELLED
    └──→ EXPIRED
```

必须拒绝非法转移。

例如：

```text
COMPLETED → ACTIVE
CANCELLED → ACTIVE
EXPIRED → ACTIVE
```

全部拒绝。

---

# 七、Duration Model

Episode 必须包含：

```text
min_duration
typical_duration
max_duration
```

例如：

```text
gaming
min=20m
typical=60m
max=180m
```

不要：

```text
start → hardcode end
```

也不要：

```text
每 tick random duration
```

Duration 是 Episode 的生命周期属性。

---

# 八、不要每 Tick 重新选择 Activity

绝对禁止：

```text
每分钟
→ LLM
→ 重新问：
“现在应该干什么？”
```

World Tick 只负责：

```text
时间前进
状态更新
Episode 生命周期推进
```

Activity Decision 只能发生在：

```text
1. Episode 即将结束
2. Episode 被中断
3. Routine 重大变化
4. World Event 重大变化
5. User interaction
6. Goal / Character State 触发
7. Runtime recovery
```

这必须通过代码路径体现。

---

# 九、Activity Runtime

新增或升级：

```text
ActivityRuntime
```

职责：

```text
start episode
get current episode
advance time
extend episode
complete episode
interrupt episode
cancel episode
expire episode
recover episode
```

不负责：

```text
LLM generation
QQ message
Minecraft tool execution
Memory retrieval
Policy authorization
```

---

# 十、Activity Decision 与 Runtime 分离

必须分开：

```text
ActivityPlanner
```

和：

```text
ActivityRuntime
```

Planner：

```text
“下一步可能应该是什么”
```

Runtime：

```text
“当前 Episode 到底是什么”
```

本阶段可以实现一个非常简单的 deterministic Planner。

不要上 LLM autonomy。

---

# 十一、6A 不实现 Life Scheduler 的自主选择

本阶段：

```text
Planner
=
deterministic test planner
```

只允许产生：

```text
next activity
transition reason
duration
```

例如：

```text
ACTIVE gaming
↓
episode expires
↓
planner
↓
resting
```

但不得：

```text
LLM
→
“我要去 Minecraft 挖矿”
```

更不得直接：

```text
planner
→
minecraft_move_to
```

---

# 十二、Primary Activity

每个角色同一时间只能存在：

```text
1 primary activity episode
```

建立：

```text
current_primary_episode_id
```

任何时刻：

```text
0 or 1
```

不能出现：

```text
primary A
primary B
```

同时 ACTIVE。

---

# 十三、Secondary Context

允许未来支持：

```text
primary = eating
secondary = using_phone
```

但是：

**6A 第一版可以只建数据模型，不做复杂 Secondary Runtime。**

如果实现：

Secondary 不得成为 Primary Episode。

不要出现：

```text
08:00 eating
08:05 phone
08:10 eating
08:15 phone
```

不停切 Primary。

---

# 十四、CharacterState Projection

现有：

```text
CharacterState.activity
```

必须由：

```text
current ActivityEpisode
```

派生。

例如：

```python
CharacterState.activity == episode.activity_name
```

并且：

```text
current_activity_episode_id
activity_started_at
activity_planned_end_at
activity_status
activity_source
```

至少能够被恢复。

禁止反向修改 Episode：

```text
CharacterState.activity = xxx
```

然后 Episode 不知道。

---

# 十五、Location

Episode 可以有：

```text
location
```

但第一版允许：

```text
semantic location
```

例如：

```text
home
bedroom
minecraft_base
minecraft_world
unknown
```

不要在这一阶段要求每个虚拟 Activity 都必须有现实坐标。

---

# 十六、Minecraft Observation Adapter

这是本阶段与 Minecraft 最重要的连接点。

Minecraft 当前真实状态可以作为：

```text
ActivityObservation
```

输入 Activity Runtime。

例如：

```text
MinecraftObservation(
    server_id,
    player_uuid,
    position,
    current_task_id,
    online,
    nearby_players,
    current_action,
    observed_at,
)
```

注意：

这是：

```text
观察
```

不是：

```text
命令
```

---

# 十七、Minecraft 不得驱动 Activity Action

例如：

```text
MinecraftObservation:
“她在基地附近”
```

不得直接：

```text
move_to
```

也不得：

```text
dig
```

Minecraft Observation 只能帮助判断：

```text
当前 episode 是否仍然合理
```

---

# 十八、Task 与 Activity Episode 的关系

这是非常重要的一层。

Task：

```text
“帮 Rinsora 挖一块橡木”
```

Activity：

```text
“执行 Minecraft 采集任务”
```

TaskRuntime 负责：

```text
任务执行事实
```

ActivityEpisode 负责：

```text
当前角色活动上下文
```

建议关系：

```text
ActivityEpisode
    └── related_task_id
```

而不是：

```text
Task = Activity
```

---

# 十九、Task → Activity

当用户任务开始：

```text
TaskRuntime = RUNNING
```

Activity Runtime 可以创建：

```text
activity_type = task_execution
activity_name = minecraft_task
source = task
related_task_id = ...
```

任务结束：

```text
Task SUCCEEDED
```

Episode：

```text
COMPLETED
```

或者：

```text
INTERRUPTED
```

具体语义由真实事件决定。

不要复制整个 Task 状态机。

---

# 二十、Activity 不得替代 Task

绝对禁止：

```text
ActivityEpisode
→
自行执行 Tool
```

Activity：

```text
“我正在挖东西”
```

不等于：

```text
ActionRuntime 正在挖东西
```

真实 Minecraft 动作依然只能由：

```text
TaskRuntime
→
Policy
→
Confirmation / Authorization
→
Agent Bridge
→
MinecraftService
→
ActionRuntime
```

产生。

---

# 二十一、Memory 的关系

Activity Episode 完成后：

可以产生：

```text
semantic / episodic summary
```

但：

**6A 不复制 Activity 历史到 Memory。**

可以先提供：

```text
activity memory hook
```

但默认只写：

```text
重要 Activity
```

不要：

```text
每个 Episode 都写 Memory
```

否则会污染现有 Memory Engine。

---

# 二十二、时间模型

如果项目已有 `WorldClock`：

必须复用。

如果没有：

实现最小：

```text
WorldClock
```

必须支持：

```text
now()
elapsed_since()
period()
timezone()
```

默认时区：

```text
Asia/Singapore
```

但必须可配置。

---

# 二十三、Logical Time

禁止：

```text
每秒写数据库
```

使用：

```text
logical time
+
periodic persistence
```

推荐：

```text
30~60 秒
```

更新运行态。

重大 Episode transition：

立即持久化。

---

# 二十四、Fake Clock

测试必须支持：

```python
FakeClock
```

能够：

```text
advance(10 minutes)
advance(1 hour)
```

绝不能为了测试：

```text
sleep(3600)
```

---

# 二十五、Episode Persistence

建立数据库表或复用现有 persistence：

```text
activity_episodes
```

至少记录：

```text
id
character_id
activity_type
activity_name
location
social_state
tags
started_at
planned_end_at
min_duration
typical_duration
max_duration
status
transition_reason
source
parent_episode_id
related_task_id
created_at
updated_at
```

如果已有 world/activity table：

优先 migration，而不是创建重复表。

---

# 二十六、当前 Episode 唯一性

数据库层必须尽可能保证：

```text
每个 character
最多一个 ACTIVE / EXTENDED primary episode
```

建议 partial unique index。

避免：

```text
两个进程同时创建 ACTIVE Episode
```

---

# 二十七、Recovery

进程重启：

必须：

```text
load current episode
```

根据时间进行 reconcile：

### 情况 A

```text
now < planned_end
```

继续 ACTIVE。

### 情况 B

```text
planned_end <= now <= max_end
```

进入：

```text
EXPIRED / evaluate extension
```

### 情况 C

```text
超过 max_duration
```

必须终结。

不能无限延长。

---

# 二十八、Recovery 不得伪造活动

例如：

```text
Bot offline 8 小时
```

不能启动后说：

```text
“我这 8 小时一直在 Minecraft 里砍树。”
```

除非有真实 runtime / task / observation 证据。

正确：

```text
Activity episode resumed/reconciled
```

或者：

```text
episode expired while offline
```

---

# 二十九、Offline / Minecraft 分离

Minecraft offline：

不能自动创建：

```text
minecraft_digging
minecraft_exploring
```

虚拟生活 Episode：

可以独立存在。

因此：

```text
virtual activity
≠
real Minecraft action
```

这个边界必须测试。

---

# 三十、Activity Source

至少：

```text
USER
TASK
ROUTINE
WORLD_EVENT
RECOVERY
SYSTEM
```

本阶段不要加入：

```text
AUTONOMOUS
```

或者至少不要让它产生任何 Minecraft action。

如果未来需要自主来源，Phase 6B 再定义。

---

# 三十一、Transition Reason

标准化：

```text
TIME_EXPIRED
TASK_STARTED
TASK_COMPLETED
TASK_FAILED
USER_INTERACTION
WORLD_EVENT
SCHEDULED
RECOVERY
MANUAL
```

不要所有状态变化都写：

```text
transition
```

原因必须可审计。

---

# 三十二、Episode Events

建立：

```text
activity.scheduled
activity.started
activity.extended
activity.completed
activity.interrupted
activity.cancelled
activity.expired
activity.recovered
```

事件必须包含：

```text
episode_id
character_id
activity
timestamp
reason
source
```

---

# 三十三、事件必须幂等

同一个：

```text
episode_id
+
transition
```

不得发布两次有效完成事件。

尤其防止：

```text
restart
scheduler retry
```

造成：

```text
activity.completed
activity.completed
```

---

# 三十四、Event 不得产生 AI Turn

Activity 事件默认：

```text
state/event only
```

不得：

```text
activity.completed
→
LLM
→
主动 QQ 消息
```

Phase 6A 禁止自主对话。

---

# 三十五、Activity Timeline

提供最小 Timeline 查询：

```text
recent episodes
```

默认：

```text
最近 3~5 个
```

未来 Social Cognition 可以直接读取。

不要每次读取整个历史。

---

# 三十六、WebUI

不要一次做完整 Activity Inspector。

6A 只需要最小只读能力：

```text
Current Activity
Episode ID
Status
Start
Planned End
Duration
Source
Related Task
Transition Reason
```

必要时：

```text
Recent Episodes
```

最多展示最近 10 条。

---

# 三十七、WebUI 不得修改 Episode

6A：

WebUI：

```text
READ ONLY
```

不要加入：

```text
Start
Cancel
Extend
Force Transition
```

这些留到后续阶段。

---

# 三十八、QQ 不得直接控制 Activity

QQ：

```text
“你现在在干嘛？”
```

可以读取：

```text
Current Episode
```

返回：

```text
当前正在……
```

但：

```text
“停止你现在的生活”
```

不能直接操作 Activity Runtime。

任务控制仍然走 TaskRuntime。

---

# 三十九、Minecraft Chat

Minecraft Chat 同样：

```text
“你在做什么”
```

可以读取 Episode。

但不得改变 Episode。

---

# 四十、LLM Context

CharacterRuntime 可以获得：

```text
current activity
recent transitions
related task
```

建议预算：

```text
current episode = 1
recent transitions <= 3
```

不要把整个 Activity History 注入 prompt。

---

# 四十一、Activity Context 与 Memory 的优先级

建议：

```text
Current World
>
Current Task
>
Current Activity Episode
>
Recent Activity
>
Relevant Memory
>
Historical Memory
```

注意：

这不是授权优先级。

只是上下文优先级。

---

# 四十二、Minecraft 当前状态投影

当真实 Minecraft Task 运行：

```text
TaskRuntime RUNNING
```

可以建立：

```text
ActivityEpisode:
activity_type = minecraft_task
source = TASK
related_task_id = task_x
```

如果 Task paused：

```text
ActivityEpisode = INTERRUPTED
```

或者依据设计：

```text
EXTENDED/WAITING
```

必须统一，不允许每个模块自己解释。

---

# 四十三、任务恢复

如果：

```text
TaskRuntime
```

从 runtime restart 恢复：

Activity Runtime 不允许创建第二个 Episode。

应该：

```text
existing episode
→ reconcile
→ related_task_id 重新绑定
```

如果原 Episode 无法恢复：

```text
INTERRUPTED
```

并创建新的后续 Episode（如果 Planner 后续决定继续）。

---

# 四十四、Concurrency

必须处理：

```text
两个事件同时结束 Episode
```

例如：

```text
Task completed
+
World Tick expiry
```

只能发生一次最终 transition。

使用：

```text
transaction
compare-and-set
state guard
```

等已有数据库机制。

---

# 四十五、禁止随机行为

本阶段不得：

```python
random.choice(...)
random.uniform(...)
```

决定：

```text
current_activity
duration
transition
```

所有测试必须 deterministic。

---

# 四十六、测试矩阵

至少新增：

```text
tests/test_activity_episode.py
tests/test_activity_runtime.py
tests/test_activity_recovery.py
tests/test_activity_projection.py
tests/test_activity_events.py
tests/test_activity_minecraft_adapter.py
```

覆盖：

### A. Episode creation

```text
SCHEDULED
```

PASS

### B. Start

```text
SCHEDULED → ACTIVE
```

PASS

### C. Extend

```text
ACTIVE → EXTENDED
```

PASS

### D. Completion

```text
ACTIVE → COMPLETED
```

PASS

### E. Interrupt

```text
ACTIVE → INTERRUPTED
```

PASS

### F. Cancel

```text
SCHEDULED → CANCELLED
```

PASS

### G. Expire

```text
ACTIVE → EXPIRED
```

PASS

### H. Invalid transition

全部拒绝。

### I. Primary uniqueness

同一 character 不允许两个 primary ACTIVE。

### J. Fake clock

时间推进不依赖 sleep。

### K. Restart

Episode persistence/recovery 正确。

### L. Duplicate event

同一 transition 不重复发布。

### M. Task relationship

Task ↔ Activity 正确绑定。

### N. Minecraft offline

offline 不产生虚假 Minecraft activity。

### O. Memory isolation

Activity 不会大量污染 Minecraft Memory。

### P. LLM isolation

Activity event 不自动触发 LLM turn。

---

# 四十七、Minecraft 真机门禁

本阶段必须真实连接 Java Server，但**不要求自主行动**。

---

## Real Smoke A：Task → Activity

真实：

```text
QQ 创建 Minecraft Task
→ 确认
→ Task RUNNING
```

应该看到：

```text
ActivityEpisode
source=TASK
related_task_id=<real task>
status=ACTIVE
```

PASS。

---

## Real Smoke B：Task Completed

真实完成：

```text
move_to
dig
pickup
inventory
```

已有 5B/5C 安全链。

Task：

```text
SUCCEEDED
```

Activity：

```text
COMPLETED
reason=TASK_COMPLETED
```

PASS。

---

## Real Smoke C：Pause

Task：

```text
PAUSE
```

Activity 必须进入定义好的中断状态。

不能：

```text
Task PAUSED
Activity 仍然 ACTIVE
```

PASS。

---

## Real Smoke D：Restart

在：

```text
Activity ACTIVE
```

的时候重启 CatooBot。

必须：

```text
Episode persisted
Episode recovered
same episode_id
no duplicate active episode
```

PASS。

---

## Real Smoke E：Minecraft Offline

Minecraft Server disconnect：

不得凭空创建：

```text
minecraft_exploring
minecraft_gathering
```

PASS。

---

# 四十八、现实世界真实性

必须严格区分：

```text
Virtual Activity
```

与：

```text
Real Minecraft Activity
```

例如：

```text
ActivityEpisode:
“休息”
source=ROUTINE
```

可以是角色世界中的虚拟状态。

但是：

```text
Minecraft:
“我刚才在基地里散步”
```

只有存在真实 observation 才能这么说。

没有 observation：

不得声称真实 Minecraft 行动发生。

---

# 四十九、Security

Activity Runtime：

不得访问：

```text
MinecraftService mutation API
```

除非经过：

```text
TaskRuntime
```

架构级测试：

```text
ActivityRuntime
does not import/call:
move_to
dig
place
pickup
follow
equip
container_transfer
```

最好增加源码级 guard。

---

# 五十、Policy 不认识 Activity

绝对禁止：

```text
activity=trusted_companion
→ allow_medium=true
```

或者：

```text
activity=minecraft_task
→ bypass_confirmation
```

Activity 是 context。

不是 permission。

---

# 五十一、Recovery Failure Isolation

如果：

```text
activity DB unavailable
```

必须：

```text
TaskRuntime continues
QQ continues
Minecraft Task continues
```

Activity Runtime：

```text
degraded
```

即可。

不能：

```text
Activity failure
→ Bot startup failure
```

---

# 五十二、Migration

新增 migration 必须：

```text
transactional
idempotent
backward compatible
```

旧数据没有 Episode：

启动后：

```text
no current episode
```

是合法状态。

不要强行生成一个虚假的 Episode。

---

# 五十三、配置

6A 只允许真正需要的少量配置，例如：

```yaml
world:
  activity:
    enabled: true
    persistence_interval_seconds: 60
    recovery_grace_seconds: 30
    recent_episode_limit: 5
```

不要一次新增十几个：

```text
activity_mood_weight
activity_randomness
activity_llm_temperature
activity_autonomy_factor
```

这些都是后续阶段。

---

# 五十四、日志

必须形成：

```text
[World.Activity]
episode=<id>
created
started
extended
completed
interrupted
cancelled
expired
recovered
```

必须出现：

```text
episode_id
```

所有与 Activity 相关的日志都应能追踪到 Episode。

---

# 五十五、审计

最终能够回答：

```text
当前在做什么？
什么时候开始？
计划什么时候结束？
为什么开始？
为什么结束？
是否被打断？
是否与 Task 有关？
重启后是不是同一个 Episode？
```

不允许只有：

```text
activity=gaming
```

这种不可审计状态。

---

# 五十六、文档

新增：

```text
docs/MINECRAFT_PHASE6A.md
```

必须记录：

1. architecture
2. Episode model
3. state machine
4. persistence
5. recovery
6. Task relation
7. Minecraft observation boundary
8. security boundary
9. real smoke
10. known limitations

同时更新：

```text
CHANGELOG.md
docs/README.md
```

---

# 五十七、禁止范围再次确认

Phase 6A 不做：

- 自主 Minecraft 行动
- Autonomous Agent
- Life Scheduler autonomous decisions
- 自动探索
- 自动采集
- 自动建造
- 自动战斗
- 自动跟随
- 自动返回基地
- 自动资源管理
- 自主 QQ 主动消息
- LLM 每 tick 决策
- Memory 自动驱动行动
- Activity 自动调用 Minecraft Tool
- 新 Minecraft tool
- 新 ActionRuntime action
- 新 TaskRuntime state

---

# 五十八、硬门禁

最终：

```text
Code                       PASS
Unit                       PASS
Integration                PASS
Security                   PASS
CI                         PASS

Episode persistence        PASS
Episode recovery           PASS
Primary uniqueness         PASS
Task ↔ Activity            PASS
Minecraft observation      PASS
Minecraft offline          PASS
Event idempotency          PASS
LLM isolation              PASS
Memory isolation           PASS
Real Java smoke             PASS
Real QQ smoke               PASS
```

任何核心真实门禁失败：

```text
PHASE 6A = BLOCKED
```

不要因为单元测试通过就判 PASS。

---

# 五十九、最终报告格式

完成后必须输出：

```text
# PHASE 6A FINAL REPORT

PHASE 6A = PASS / BLOCKED

Commit:
CI:

Episode Model:
PASS / FAIL

State Machine:
PASS / FAIL

Persistence:
PASS / FAIL

Recovery:
PASS / FAIL

Primary Uniqueness:
PASS / FAIL

Task Binding:
PASS / FAIL

Minecraft Observation:
PASS / FAIL

Minecraft Offline:
PASS / FAIL

Event Idempotency:
PASS / FAIL

LLM Isolation:
PASS / FAIL

Memory Isolation:
PASS / FAIL

Real Java:
PASS / FAIL

Real QQ:
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

allow_medium default unchanged:
PASS
```

# 六十、Phase 6A 完成标准

完成后，系统应该达到：

```text
世界时间继续流逝
        ↓
当前 Episode 持续
        ↓
Episode 到期 / 中断 / 事件变化
        ↓
Activity Runtime 判断
        ↓
结束当前 Episode
        ↓
产生下一个 Episode
```

但此时：

```text
Episode
    ≠
Minecraft Action
```

这是刻意的。

6A 的终点应该是：

> **“罐头知道自己现在正在做什么，而且这个状态是可持续、可恢复、可审计的。”**

而不是：

> “罐头已经可以自己在 Minecraft 里到处跑。”

后者留到 Phase 6B/6C。