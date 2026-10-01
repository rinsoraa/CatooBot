# CatooBot v0.8 开发任务
# Persistent World + Background Life Runtime
# 持续世界状态 + 后台生活线 + 长期计划 + 时间连续性

---

# v0.8 §1 一、前置条件

CatooBot v0.7 已经完成。

当前已经具备：

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
- Topic
- Behavior Engine
- Initiative Engine
- Response Timing
- Memory Engine
- Semantic Memory
- Episodic Memory
- Embedding
- Hybrid Retrieval
- Memory Consolidation
- Tool Runtime
- Tool Registry
- Tool Router
- Tool Policy
- Tool Permission
- Tool Budget
- Weather
- Search
- Calculator
- Time
- Agent Runtime
- Goal
- Task
- Plan
- Step
- Observation
- Evaluator
- Replanning
- Agent Budget
- Task Persistence
- WebUI
- Runtime Management
- Persona Management
- Memory Management
- Tool Management
- Agent Management
- Logs

现在进入：

# CatooBot v0.8

核心目标：

> **建立一个持续运行的、具有时间连续性和状态连续性的角色世界。**

---

# v0.8 §2 二、v0.8 解决的问题

目前：

```text id="zfl8h4"
用户聊天
↓
角色响应
↓
任务完成
↓
结束
```

v0.8：

```text id="0xw8wq"
时间持续流逝
↓
角色状态变化
↓
活动变化
↓
计划推进
↓
话题状态变化
↓
角色可能产生新的内部事件
↓
某些事件影响 Memory / Relationship / Initiative
↓
下一次用户出现时
角色已经“生活”了一段时间
```

---

# v0.8 §3 三、核心产品体验

希望用户经过几天后重新找 CatooBot：

不是：

```text id="5fln4a"
您好，又见面了。
```

而是：

```text id="kq3w4a"
自然延续之前的状态与话题。
```

例如角色之前处于：

```text id="njc8y5"
正在完成一个虚拟角色世界中的长期项目
```

几天后用户问：

```text id="2k3qgo"
最近怎么样？
```

角色应该能够基于：

```text id="4k5pwf"
时间流逝
+
活动状态
+
长期计划
+
未完成 Topic
+
Relationship
+
Memory
```

自然回答。

---

# v0.8 §4 四、非常重要的现实边界

CatooBot 可以拥有：

```text id="8d1x31"
虚拟生活
虚拟活动
虚拟计划
虚拟兴趣
虚拟背景事件
虚拟角色世界状态
```

但是：

**不能把虚拟状态伪装成现实世界中真实发生过、且可验证的行为。**

例如允许：

```text id="8gjrw9"
角色世界里：
“今天整理了一会儿房间”
```

但如果现实中没有对应系统或工具，不应该声称：

```text id="zwv14u"
“我刚刚真的去了某个现实地点”
```

更不能虚构：

```text id="x4d6fs"
现实中的真实人物见面
现实中的真实工作经历
真实购买行为
真实线下活动
真实外部账号操作
```

除非确实存在对应工具并真实执行。

这一点必须贯穿整个 v0.8。

---

# v0.8 §5 五、v0.8 核心架构

```text id="r7m3hm"
                         World Runtime
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
          Time Engine     State Engine     Activity Engine
              │               │               │
              └───────────────┼───────────────┘
                              ▼
                     Life Scheduler
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
      Life Events         Long Goals        Daily Routine
           │                  │                  │
           └──────────────────┼──────────────────┘
                              ▼
                    Character Runtime
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
     Memory              Relationship             Topic
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
                      Behavior / Initiative
                              │
                              ▼
                         QQ / WebUI
```

---

# v0.8 §6 六、增加 World Runtime

新增：

```text id="k97p8f"
app/
├─ world/
│  ├─ __init__.py
│  ├─ runtime.py
│  ├─ clock.py
│  ├─ state.py
│  ├─ events.py
│  ├─ scheduler.py
│  ├─ activity.py
│  ├─ routine.py
│  ├─ goals.py
│  ├─ timeline.py
│  ├─ rules.py
│  └─ models.py
```

如果当前项目已经有：

```text id="d4r9st"
Character State
Behavior Scheduler
```

必须复用并逐步升级，不要创建互相独立的第二套系统。

---

# v0.8 §7 七、World Runtime 的职责

World Runtime 只负责：

```text id="4t9y0c"
时间
状态
活动
计划
事件
角色世界时间线
```

不负责：

```text id="w2m7l9"
AI 生成
QQ 消息
Tool 执行
Memory Retrieval
```

---

# v0.8 §8 八、Character State 升级

v0.4 已有：

```text id="7u9v65"
mood
energy
focus
activity
```

v0.8 增加：

```text id="1lzjmy"
location
social_state
schedule_state
current_goal
current_project
last_activity_change
```

注意：

这些都是：

> 角色内部世界状态。

---

# v0.8 §9 九、State Model

设计：

```python id="ih97a1"
CharacterWorldState(
    mood,
    energy,
    focus,
    activity,
    location,
    social_state,
    schedule_state,
    current_goal,
    current_project,
    updated_at
)
```

---

# v0.8 §10 十、状态变化必须有原因

禁止：

```text id="q0v3gs"
每分钟随机改变状态。
```

状态变化应该来自：

```text id="b0g6yy"
time_passage
activity
scheduled_event
user_interaction
task_completion
task_failure
topic_progress
relationship_event
ambient_event
```

---

# v0.8 §11 十一、状态变化不是随机模拟

例如：

```text id="lfr3wm"
上午
→ energy 较高

深夜
→ energy 较低
```

这是：

```text id="7r7f2d"
规则驱动
```

而不是每隔 30 秒：

```text id="6xsk2r"
random.uniform(...)
```

---

# v0.8 §12 十二、Time Engine

增加：

```text id="qyw7nv"
WorldClock
```

负责：

```text id="r2x5a4"
current time
local date
weekday
time period
elapsed time
```

必须支持：

```yaml id="rbnxjo"
world:
  timezone: "Asia/Singapore"
```

时区必须可配置。

---

# v0.8 §13 十三、不要把系统时间直接当成所有角色时间

支持角色：

```text id="6g5v3w"
timezone
```

角色世界时间可以不同。

但默认：

```text id="2c5zqk"
character timezone = configured default
```

---

# v0.8 §14 十四、Time Period

抽象成：

```text id="c21j8d"
late_night
morning
afternoon
evening
night
```

不要在业务里大量直接判断：

```python id="2a6kkr"
if hour > 18:
```

统一通过：

```text id="fvr0pd"
TimeEngine
```

提供。

---

# v0.8 §15 十五、时间流逝机制

不要每秒写数据库。

采用：

```text id="7x8n42"
logical time
+
periodic persistence
```

例如：

```text id="tl0v5j"
每 30~60 秒
```

更新运行时状态。

关键状态变化时写数据库。

---

# v0.8 §16 十六、后台生活线

增加：

```text id="3l1v7y"
LifeScheduler
```

负责：

```text id="ngc5za"
日常活动
周期事件
计划推进
状态衰减
未完成事项
```

---

# v0.8 §17 十七、Daily Routine

角色可以有一个虚拟日程：

```yaml id="xk09u1"
routine:
  enabled: true

  morning:
    - "wake"
    - "breakfast"

  daytime:
    - "work"
    - "study"

  evening:
    - "relax"
    - "gaming"

  late_night:
    - "rest"
```

具体内容必须：

**由 WebUI 配置，不要硬编码。**

---

# v0.8 §18 十八、Routine 不等于真实世界行为

Routine 表示：

> 角色世界中的日常生活模拟。

不是：

> 系统自动声称角色真的在现实世界中做这些事情。

---

# v0.8 §19 十九、Activity Transition

例如：

```text id="0p4zck"
activity:
reading

时间到了：

activity:
resting
```

需要：

```text id="k40q8m"
ActivityTransition
```

---

# v0.8 §20 二十、Activity Transition 应平滑

不要：

```text id="6jv9m0"
gaming
↓
sleeping
```

毫无过渡。

可以：

```text id="pr1l7f"
gaming
↓
winding_down
↓
resting
↓
sleeping
```

但不要创建几十种状态。

保持简单。

---

# v0.8 §21 二十一、Long-term Goals

v0.7 已经有 Task/Goal。

v0.8 新增：

```text id="hsj9f7"
PersistentGoal
```

区别：

### Agent Goal

```text id="l7qz4e"
一次任务
```

### Persistent Goal

```text id="5t9w0x"
长期目标
```

例如：

```text id="86ifqd"
完成一个虚拟项目
学习某个虚构技能
逐步完善角色房间
持续研究一个兴趣
```

---

# v0.8 §22 二十二、PersistentGoal 生命周期

```text id="5cfzgu"
created
active
paused
progressing
completed
abandoned
archived
```

---

# v0.8 §23 二十三、Goal Progress

每个长期目标拥有：

```text id="33byxq"
progress
milestones
next_action
last_progress_at
```

例如：

```text id="z5xvkg"
Goal：
完成角色世界中的个人项目

Progress：
0.65

Next:
整理资料
```

---

# v0.8 §24 二十四、长期目标不能无限自动推进

非常重要。

角色不能：

```text id="h8yytd"
每天自动调用 100 个 Tool
```

而应该：

```text id="8l6cd9"
需要时推进一步
```

或者：

```text id="5z0k7p"
产生一个内部 Life Event
```

---

# v0.8 §25 二十五、Long-term Goal 与用户聊天

如果用户聊天涉及：

```text id="4o7b3v"
角色当前正在推进的长期目标
```

可以自然提及。

例如：

```text id="1v9c4u"
用户：
最近在忙什么？

角色：
最近有点在折腾那个之前没弄完的东西，
终于快收尾了。
```

而不是：

```text id="l9t8az"
PersistentGoal#3 progress=0.82
```

---

# v0.8 §26 二十六、不要让角色每次聊天都提长期目标

Persistent Goal 是：

```text id="4y0ky1"
背景状态
```

只有：

```text id="h2jb40"
相关时
```

才进入 Context。

---

# v0.8 §27 二十七、Milestone

长期目标支持：

```text id="c9q5fz"
milestones
```

例如：

```text id="c8t0dn"
阶段 1
阶段 2
阶段 3
```

完成 milestone 可以触发：

```text id="l5m8ow"
Character State
Topic
Memory
Behavior Candidate
```

---

# v0.8 §28 二十八、Life Event

新增：

```text id="4zjsh3"
LifeEvent
```

表示：

> 角色世界里发生的一件内部事件。

例如：

```text id="e4o4pv"
完成了一个阶段
发现了一个新兴趣
改变了活动
开始一个新项目
结束一个旧项目
```

---

# v0.8 §29 二十九、Life Event 分类

```text id="7j1g0k"
activity
goal
project
interest
routine
relationship
ambient
milestone
```

---

# v0.8 §30 三十、Life Event 必须有时间戳

至少：

```text id="8z8p7x"
event_id
type
summary
created_at
related_goal
related_topic
```

---

# v0.8 §31 三十一、Life Event 不应该自动写成 Memory

只有 Memory Extraction / Consolidation 判断值得长期记忆才进入 Memory。

否则：

```text id="9km9a5"
LifeEvent
```

只留在世界时间线。

---

# v0.8 §32 三十二、Timeline

增加：

```text id="6s1z4v"
WorldTimeline
```

记录：

```text id="3xg49u"
活动变化
目标变化
Milestone
Life Event
Relationship Event
```

用途：

```text id="avkp8r"
角色连续性
```

---

# v0.8 §33 三十三、Timeline 与 Memory 的区别

### Timeline

```text id="9q6p73"
发生过什么
```

### Memory

```text id="ubmjko"
以后值得记住什么
```

### Topic

```text id="8vmlq2"
我们正在持续聊什么
```

### Persistent Goal

```text id="6cm5dp"
角色长期想完成什么
```

必须保持四者分离。

---

# v0.8 §34 三十四、World Event Bus

复用现有 Event Bus，或者在其上扩展：

```text id="s1q2me"
WorldEvent
```

例如：

```text id="u87v9v"
ActivityChanged
MoodChanged
GoalProgressed
MilestoneReached
RoutineStarted
RoutineEnded
LifeEventCreated
```

---

# v0.8 §35 三十五、Scheduler

v0.4 已有 BehaviorScheduler。

v0.8 不允许创建：

```text id="0v4j5f"
第二个完全独立 Scheduler
```

统一：

```text id="yw9v7q"
CatooBot Scheduler
   ├─ Behavior Jobs
   ├─ Initiative Jobs
   ├─ Agent Cleanup
   ├─ World Jobs
   └─ Memory Maintenance
```

---

# v0.8 §36 三十六、Scheduler Job

标准：

```python id="cl06tf"
ScheduledJob(
    id,
    type,
    next_run,
    interval,
    enabled,
    payload
)
```

---

# v0.8 §37 三十七、Scheduled Event

支持：

```text id="hi9xq3"
一次性事件
周期事件
时间窗口
条件事件
```

---

# v0.8 §38 三十八、条件事件

例如：

```text id="u3c0eo"
当：
角色持续处于 idle
超过：
3 小时

触发：
ambient event
```

但：

必须经过：

```text id="6h3o5c"
Behavior Policy
```

---

# v0.8 §39 三十九、条件事件不能无限触发

必须有：

```text id="7xq4cs"
cooldown
max_per_day
max_per_period
```

---

# v0.8 §40 四十、Ambient Event

环境事件是：

> 对角色世界的轻微变化。

例如：

```text id="xn8pkf"
“突然想起一个很久没碰的话题”
“今天比较有精神”
“最近总想继续某个兴趣”
```

但：

**不要每隔十分钟随机生成。**

---

# v0.8 §41 四十一、Ambient Event 生成规则

允许：

```text id="k5y4q0"
low_frequency
context-aware
state-aware
```

例如：

```text id="85v0fd"
长期某 Topic 未触碰
+
角色当前状态适合
+
没有最近主动消息
```

才可能产生一个：

```text id="j17s30"
topic resurfacing candidate
```

---

# v0.8 §42 四十二、Background Life 不等于 Background Messaging

非常重要。

角色后台可以：

```text id="9j4w5z"
改变状态
推进目标
更新 Timeline
```

但不代表：

```text id="w4p3o0"
一定要向 QQ 发消息。
```

是否发送：

必须经过：

```text id="8yc1or"
Initiative Engine
```

---

# v0.8 §43 四十三、Background Messaging

如果后台世界事件产生：

```text id="4x2d91"
值得与用户分享
```

可以生成：

```text id="9h0s7g"
Initiative Candidate
```

然后正常走：

```text id="4t3m6f"
cooldown
daily limit
relationship
DND
initiative gate
```

---

# v0.8 §44 四十四、不要把“发生了事件”直接等于“发消息”

例如：

```text id="mqlv4e"
角色完成了一个内部目标
```

不是：

```text id="1klup9"
必须 QQ：
“我完成了一个目标！”
```

应该先判断：

```text id="zdqk57"
这是否自然值得与该用户分享？
```

---

# v0.8 §45 四十五、Relationship Event

如果用户：

```text id="w81vnp"
长时间没有聊天
```

可能影响：

```text id="j8qmt0"
relationship state
```

但：

不要自动变成：

```text id="b3u21y"
“用户不在乎我”
```

这类主观/敏感心理判断。

可以记录：

```text id="3zv3w0"
last_interaction
interaction_frequency
relationship_stage
```

这些可观察数据。

---

# v0.8 §46 四十六、Relationship Decay

可以有：

```text id="zz94gq"
interaction recency
```

但：

不要简单：

```text id="3pcc3f"
30 天没聊
→ 好感度 -10
```

不要把关系做成游戏数值。

更合理：

```text id="v3yb7w"
活跃关系
较久未互动
```

这样的离散状态。

---

# v0.8 §47 四十七、用户重新出现

如果：

```text id="q2x1oh"
较久没互动
```

用户重新发消息：

CatooBot 可以结合：

```text id="4h2ae9"
last_seen
Memory
Relationship
Topic
```

自然恢复关系。

不应该：

```text id="5dqg0q"
你已经 17 天 4 小时 12 分钟没来了。
```

除非用户明确需要这种精确统计。

---

# v0.8 §48 四十八、角色“记得时间”

允许自然：

```text id="vm4r5h"
“感觉已经有一阵子了。”
```

而不是：

```text id="7r7e53"
last_seen=...
```

---

# v0.8 §49 四十九、Daily Summary

可以为角色世界生成：

```text id="8aj6tx"
daily_summary
```

内容：

```text id="go9vhk"
今天做了什么
完成了什么
当前关注什么
明天可能继续什么
```

但：

这属于内部角色状态，不需要每天发送给用户。

---

# v0.8 §50 五十、Daily Summary 与 Memory

Daily Summary 是：

```text id="x5d7jj"
短期世界摘要
```

不是长期 Memory。

第二天可以被压缩/删除。

---

# v0.8 §51 五十一、World Snapshot

每天保存一个：

```text id="1u6fku"
WorldSnapshot
```

包含：

```text id="1ix4r5"
state
activity
current_goals
topics
pending_events
```

这样 WebUI 可以查看角色在某天的大致状态。

---

# v0.8 §52 五十二、World Snapshot 恢复

CatooBot 重启后：

```text id="7t8b40"
读取 latest snapshot
+
replay recent events
```

恢复：

```text id="j8o2g6"
Character World State
```

---

# v0.8 §53 五十三、事件溯源

v0.8 可以开始使用轻量：

```text id="s04gk8"
Event Sourcing
```

不是要求所有数据库都改成 Event Sourcing。

只对：

```text id="k4orh9"
World State
```

采用：

```text id="0osb8h"
State Snapshot
+
Recent Events
```

---

# v0.8 §54 五十四、World State 一致性

不能出现：

```text id="ziay7a"
数据库：
reading

内存：
gaming
```

需要：

```text id="b0g694"
WorldStateManager
```

统一更新。

---

# v0.8 §55 五十五、状态修改必须原子

例如：

```text id="l7e4d9"
activity
+
current_goal
+
timeline
```

应该通过一个 World State Transition 更新。

避免部分更新。

---

# v0.8 §56 五十六、World Event Idempotency

相同事件不能执行两次。

例如：

```text id="5zeux4"
milestone_reached
```

必须有：

```text id="a8fda1"
event_id
```

防止重启后重复触发。

---

# v0.8 §57 五十七、Scheduler Crash Recovery

如果 Scheduler 中断：

恢复后：

```text id="3pfv8c"
重新计算 next_run
```

不能因为宕机：

```text id="u29g2w"
一次性补发 100 个过去事件。
```

---

# v0.8 §58 五十八、Missed Event Policy

如果事件本来：

```text id="v9jjb4"
08:00
```

系统 12:00 才恢复。

默认：

```text id="z8zq6s"
skip
```

对于关键任务：

可以：

```text id="z2kmco"
catch-up once
```

需要配置。

---

# v0.8 §59 五十九、WebUI World Dashboard

新增：

```text id="af3g6r"
/world
```

显示：

```text id="8t1dfu"
Current Time
Current Activity
Current Mood
Current Energy
Current Focus
Current Goal
Current Project
Social State
Schedule State
```

---

# v0.8 §60 六十、World Timeline

页面：

```text id="z0jlcj"
/world/timeline
```

显示：

```text id="e4k891"
时间
事件
类型
来源
相关 Goal
相关 Topic
```

---

# v0.8 §61 六十一、Daily Life 页面

页面：

```text id="db0pi1"
/world/routine
```

可以编辑：

```text id="x6gb3g"
Morning
Day
Evening
Night
Sleep
```

支持：

```text id="9z4lpk"
拖拽
启用/禁用
修改时间段
修改活动
```

如果当前 WebUI 技术栈不适合复杂拖拽：

普通表单也可以。

---

# v0.8 §62 六十二、Persistent Goals 页面

```text id="f1d0w2"
/world/goals
```

显示：

```text id="sw6qzr"
Goal
Status
Progress
Milestones
Next Action
Last Progress
```

支持：

```text id="hlm4vr"
创建
编辑
暂停
恢复
完成
归档
```

---

# v0.8 §63 六十三、Life Events 页面

```text id="xq4qot"
/world/events
```

可以：

```text id="l7ny9v"
查看
筛选
查看关联 Goal
查看关联 Topic
```

默认只读。

---

# v0.8 §64 六十四、World Simulator

新增：

```text id="2wdl5f"
/world/simulator
```

管理员可以模拟：

```text id="1rjq2q"
时间推进 1 小时
时间推进 1 天
触发某个事件
完成一个 milestone
用户重新出现
```

查看：

```text id="p2gpgq"
State Changes
Events
Topics
Goals
Initiative Candidates
```

但是：

**模拟模式默认不发送 QQ。**

---

# v0.8 §65 六十五、Dry Run

World Simulator 必须支持：

```text id="e7lvf0"
dry_run = true
```

只改变临时状态。

---

# v0.8 §66 六十六、Background Runtime Toggle

WebUI：

```text id="9m1hvo"
World Runtime
Behavior
Initiative
```

可以独立启停。

例如：

```text id="5f2zso"
World Runtime = ON
Initiative = OFF
```

角色仍然有：

```text id="3n5v8z"
世界时间线
```

但不会主动发消息。

---

# v0.8 §67 六十七、Global Pause

WebUI 支持：

```text id="8ck4o8"
Pause World Runtime
```

暂停：

```text id="kqj58w"
Life Scheduler
Ambient Events
Background Goals
```

但：

```text id="n7gllj"
QQ 正常聊天
AI 正常
Memory 正常
```

---

# v0.8 §68 六十八、Character Vacation / Rest Mode

允许后台：

```text id="2k4n7b"
rest mode
```

影响：

```text id="cbh6to"
activity
initiative
background events
```

不是：

```text id="0b0m8u"
关闭 Bot
```

---

# v0.8 §69 六十九、World State 与 Persona

Persona：

```text id="x3b0pz"
“我是谁”
```

World State：

```text id="5t8x1a"
“我现在过着什么状态”
```

二者保持分离。

---

# v0.8 §70 七十、World State 与 Memory

例如：

```text id="m7j14t"
current_activity = reading
```

不要自动 Memory：

```text id="g36vf5"
角色喜欢阅读。
```

只有长期稳定趋势才通过 Memory 系统形成。

---

# v0.8 §71 七十一、World State 与 Behavior

Behavior 使用：

```text id="s2s2s6"
activity
mood
energy
time
```

决定：

```text id="jyx0y5"
回复风格
主动性
延迟
```

---

# v0.8 §72 七十二、World State 与 Agent

Agent 可以读取：

```text id="5a3jjd"
current_activity
current_goal
time
```

但：

不能随意修改 World State。

需要：

```text id="x3c17v"
WorldStateService
```

例如：

```python id="em1ajq"
await world_state.start_activity(...)
```

---

# v0.8 §73 七十三、World Event 与 Agent

Agent Task 完成：

可以产生：

```text id="3s4x8z"
TaskCompletedEvent
```

World Runtime 决定：

```text id="8d5m2e"
是否影响角色世界
```

而不是 Agent 直接写 Timeline。

---

# v0.8 §74 七十四、World Event 与 Tool

Tool Result：

```text id="o8nxw0"
external result
```

默认不进入 World Timeline。

只有真正影响角色世界模型的事件：

```text id="b4dcga"
才可以进入。
```

---

# v0.8 §75 七十五、角色长期目标必须可配置

WebUI 管理：

```text id="q3v3yi"
Name
Description
Priority
Milestones
Enabled
```

不要硬编码：

```text id="xr7qz7"
角色一定要学习某某东西。
```

---

# v0.8 §76 七十六、Goal Progress 来源

Progress 可以由：

```text id="z2o2m7"
manual
agent
life_event
milestone
```

更新。

但：

不能每次聊天随机：

```text id="3e1c1l"
progress += random
```

---

# v0.8 §77 七十七、Long Goal 自动推进

允许：

```text id="6n5k18"
low-frequency
bounded
```

例如：

```text id="2xol1r"
每隔一段时间
检查是否满足推进条件
```

最多推进：

```text id="1orq07"
1 个 milestone
```

避免暴走。

---

# v0.8 §78 七十八、Persistent Goal 与 Agent

如果某个长期 Goal 需要实际工具执行：

可以产生：

```text id="3pgo4p"
Agent Task
```

例如：

```text id="5f2v4n"
Persistent Goal:
完成角色世界中的资料整理

↓
Agent Task:
整理一次资料
```

但：

必须有限时、有限预算。

---

# v0.8 §79 七十九、Agent 不允许永久占用 Long Goal

每次：

```text id="90teeb"
Agent Task
```

完成后释放。

---

# v0.8 §80 八十、角色世界“项目”

可以提供：

```text id="vcqs58"
CharacterProject
```

用于长期兴趣项目。

例如：

```text id="ktyvkg"
name
description
status
progress
started_at
updated_at
```

项目可以连接：

```text id="z9pbl8"
Memory
Topic
Goal
LifeEvent
```

---

# v0.8 §81 八十一、Project 与 Goal 的区别

### Project

```text id="m9i34l"
长期存在的事项
```

### Goal

```text id="g4s8lh"
希望达成的目标
```

一个 Project 可以有多个 Goal。

---

# v0.8 §82 八十二、Project 生命周期

```text id="yz4vpw"
idea
active
paused
completed
archived
```

---

# v0.8 §83 八十三、用户项目与角色项目分开

非常重要。

用户说：

```text id="3mxsyf"
我在做一个网站。
```

这是：

```text id="ol5rca"
User Memory / User Topic / User Project
```

不是：

```text id="0qggn5"
Character Project
```

除非明确由角色自己拥有。

---

# v0.8 §84 八十四、不要混淆用户现实生活与角色虚拟生活

系统必须明确：

```text id="snk0f8"
User World
Character World
```

二者分离。

角色可以了解用户：

```text id="k8d8ou"
用户正在做某个网站。
```

但不应该变成：

```text id="g8v0mi"
角色自己正在帮助用户在现实生活中完成该网站。
```

除非用户明确请求协作，并通过 Tool/Agent 真实执行。

---

# v0.8 §85 八十五、WebUI World Configuration

允许：

```text id="yqv5vz"
Timezone
Sleep Schedule
Routine
State Rules
Ambient Event Frequency
Goal Limits
World Runtime
```

---

# v0.8 §86 八十六、World Frequency

配置：

```yaml id="7e7f38"
world:
  tick_seconds: 60

  ambient:
    enabled: true
    min_interval_minutes: 60

  goals:
    enabled: true
    max_progress_events_per_day: 3
```

具体值只是默认建议。

---

# v0.8 §87 八十七、不要高频写数据库

World Tick：

```text id="sxc7uh"
内存计算
```

关键事件：

```text id="d8mppx"
写 DB
```

周期：

```text id="4z3q0a"
Snapshot
```

减少 SQLite 压力。

---

# v0.8 §88 八十八、World Runtime 崩溃隔离

如果：

```text id="55ao1m"
World Runtime
```

异常：

```text id="k6v0gi"
QQ Chat
```

必须继续工作。

World Runtime 重新恢复。

---

# v0.8 §89 八十九、World Runtime Health

WebUI：

```text id="7q5r2p"
/world/health
```

显示：

```text id="mb8t2e"
Scheduler
Last Tick
Next Tick
State Persistence
Pending Events
Failed Jobs
```

---

# v0.8 §90 九十、测试

新增：

```text id="5p8vx3"
tests/
├─ test_world_clock.py
├─ test_world_state.py
├─ test_activity_transition.py
├─ test_routine.py
├─ test_life_events.py
├─ test_persistent_goals.py
├─ test_world_timeline.py
├─ test_world_scheduler.py
├─ test_world_snapshot.py
├─ test_world_recovery.py
└─ test_world_simulator.py
```

---

# v0.8 §91 九十一、Time 测试

测试：

```text id="yw0xgk"
timezone
day change
week change
sleep period
time period
elapsed time
```

---

# v0.8 §92 九十二、State 测试

测试：

```text id="2s6kfz"
activity transition
energy decay
mood persistence
state atomic update
```

---

# v0.8 §93 九十三、Routine 测试

测试：

```text id="wq9p9j"
schedule start
schedule end
overlap
disabled routine
timezone
```

---

# v0.8 §94 九十四、Goal 测试

测试：

```text id="4b5d35"
create
progress
milestone
pause
resume
complete
archive
```

---

# v0.8 §95 九十五、Timeline 测试

测试：

```text id="xw6d5h"
event insert
event idempotency
ordering
replay
```

---

# v0.8 §96 九十六、Snapshot 测试

测试：

```text id="s5o2j8"
save
load
restore
corrupt snapshot
fallback to previous snapshot
```

---

# v0.8 §97 九十七、Scheduler Recovery 测试

模拟：

```text id="2j56tt"
process crash
restart
```

验证：

```text id="w9jj0j"
不会重复执行旧事件。
不会同时运行两个 Scheduler。
```

---

# v0.8 §98 九十八、Background Life 测试

模拟：

```text id="2k6s2d"
推进 1 天
```

观察：

```text id="pc1rdm"
Activity
State
Goal
Timeline
```

是否合理变化。

---

# v0.8 §99 九十九、Initiative 联动测试

模拟：

```text id="0h6zfz"
LifeEvent
```

验证：

```text id="h9fyy6"
是否产生 Initiative Candidate
```

但：

必须继续通过：

```text id="t5r7cf"
Initiative Gate
```

---

# v0.8 §100 一百、Memory 联动测试

LifeEvent：

```text id="o6d8av"
完成长期项目
```

不应自动保存 Memory。

经过：

```text id="lo4nqs"
Memory Extraction / Consolidation
```

才决定是否记忆。

---

# v0.8 §101 一百零一、Agent 联动测试

Persistent Goal：

```text id="qf3z7d"
```

产生 Agent Task。

完成后：

```text id="2nyz8t"
Goal progress
```

更新。

---

# v0.8 §102 一百零二、QQ 集成测试

用户几天后重新聊天：

```text id="esx3wp"
“最近怎么样？”
```

系统应该综合：

```text id="8x9x3p"
World State
+
Recent Activity
+
Persistent Goals
+
Topics
+
Memory
+
Relationship
```

生成自然回复。

---

# v0.8 §103 一百零三、长时间模拟测试

WebUI Simulator：

```text id="0v47lh"
推进 1 天
推进 3 天
推进 7 天
```

验证：

```text id="9t2na3"
不会出现：
状态失控
事件爆炸
消息爆炸
任务无限增加
Memory 爆炸
```

---

# v0.8 §104 一百零四、事件爆炸保护

所有 Background Event 必须有：

```text id="i1tfs1"
global limit
per type limit
per day limit
cooldown
```

禁止：

```text id="oqhv7i"
每个 tick 一个事件
```

---

# v0.8 §105 一百零五、后台消息爆炸保护

整个 World Runtime：

必须有：

```text id="2g1h0z"
max_initiative_candidates_per_day
max_background_messages_per_day
```

---

# v0.8 §106 一百零六、World Runtime 与 QQ 解耦

如果：

```text id="c4p5pg"
QQ 连接断开
```

World Runtime 可以：

```text id="qz06wa"
继续运行
```

但：

```text id="k9j99x"
Initiative Message
```

应该进入：

```text id="x6wwm5"
Pending
```

而不是无限重试发送。

---

# v0.8 §107 一百零七、Pending Initiative

如果 QQ 暂时离线：

```text id="wzqk1j"
主动消息候选
```

默认：

```text id="9p9xow"
TTL
```

过期自动丢弃。

不要：

```text id="qv2x2o"
上线后一次性补发十条。
```

---

# v0.8 §108 一百零八、角色“离线”状态

允许 World Runtime：

```text id="a7bq9k"
online
idle
away
resting
```

但：

这只是角色状态，不代表真实 QQ 账号在线状态。

---

# v0.8 §109 一百零九、角色休息时

可以：

```text id="t1i4v4"
减少 Initiative
减少后台事件
```

不代表：

```text id="3l5b9w"
停止所有系统
```

---

# v0.8 §110 一百一十、WebUI World Replay

增加：

```text id="m9g9m4"
/world/replay
```

允许管理员选择：

```text id="q3x8c9"
时间段
```

回放：

```text id="iw2vch"
State Changes
Events
Goals
Activities
Initiative Candidates
```

不真正向 QQ 发送。

---

# v0.8 §111 一百一十一、World Audit

记录：

```text id="bgqlk1"
state change
event
goal progress
activity transition
scheduler execution
```

用于 Debug。

---

# v0.8 §112 一百一十二、World State 与 WebUI Hot Reload

配置修改：

```text id="4v2qqs"
Routine
Sleep
Timezone
Goal
```

应该尽量：

```text id="0g0b2c"
Hot Reload
```

---

# v0.8 §113 一百一十三、配置示例

增加：

```yaml id="jv6l6d"
world:

  enabled: true

  timezone: "Asia/Singapore"

  tick_seconds: 60

  routine:
    enabled: true

  goals:
    enabled: true
    max_progress_events_per_day: 3

  ambient:
    enabled: true
    min_interval_minutes: 60

  events:
    max_per_hour: 10

  snapshot:
    interval_minutes: 30

  messaging:
    enabled: true
    max_background_messages_per_day: 3
```

所有值都应该可通过 WebUI 调整。

---

# v0.8 §114 一百一十四、不要硬编码生活内容

不要在 Python 中：

```text id="a5xv2z"
if morning:
    character.eat_breakfast()
```

而应该：

```text id="b2y0t1"
Routine Configuration
↓
Activity
```

角色生活设定由 WebUI 配置。

---

# v0.8 §115 一百一十五、不要过度模拟

v0.8 不是：

```text id="r3i7h3"
模拟每分钟角色在干什么。
```

而是：

```text id="6rqf2m"
模拟对聊天有价值的时间连续性。
```

优先：

```text id="6vq4c1"
活动
状态
长期目标
未完成话题
事件
关系
```

---

# v0.8 §116 一百一十六、角色生活变化应该稀疏

例如：

```text id="0h9q1j"
一天
```

产生：

```text id="1k6tvz"
少量 meaningful events
```

而不是：

```text id="i3v7ki"
几千条事件
```

---

# v0.8 §117 一百一十七、World State 与拟人化

目标：

不要让用户感觉：

```text id="4fjxq1"
“它每次都从零开始。”
```

而是：

```text id="fzqz5q"
“它一直在继续。”
```

---

# v0.8 §118 一百一十八、最终体验示例

例如：

周一：

```text id="9v3c4b"
用户：
最近在忙什么？

角色：
最近在弄点自己的小项目。
```

周三用户再次聊天：

```text id="0cx8on"
用户：
最近呢？

角色：
还在折腾那个东西，不过已经快收尾了。
```

这不应该完全来自：

```text id="6j7x8y"
旧聊天历史
```

而应该来自：

```text id="2v0t3m"
World State
+
Persistent Goal
+
Timeline
+
Memory
```

---

# v0.8 §119 一百一十九、最终产品目标

v0.7：

```text id="kv3g4u"
它可以为了目标完成事情。
```

v0.8：

```text id="i5rw2b"
它不仅能完成事情，
还拥有一条持续运行的角色时间线。
```

最终：

```text id="z0w4fu"
聊天不是一次次独立发生的。

每次用户回来时，
CatooBot 都已经在自己的时间线上
继续“存在”了一段时间。
```

---

# v0.8 §120 一百二十、开发顺序

严格按照：

```text id="dsykci"
1. 阅读 v0.7 源码
2. 阅读 Behavior Scheduler
3. 阅读 Character State
4. 阅读 Agent Task
5. World Clock
6. World State
7. Activity Transition
8. Routine
9. Persistent Goal
10. Milestone
11. Life Event
12. World Timeline
13. World Event Bus
14. World Scheduler Integration
15. Snapshot
16. Recovery
17. Background Life
18. Initiative Integration
19. Memory Integration
20. Topic Integration
21. Agent Integration
22. WebUI World Dashboard
23. Routine Management
24. Goal Management
25. Timeline
26. Simulator
27. Replay
28. Health
29. Tests
30. Long-term Simulation Tests
31. QQ Integration Tests
32. Regression
```

---

# v0.8 §121 一百二十一、最终交付

完成：

# CatooBot v0.8

必须具备：

```text id="jx4w5l"
✅ World Runtime
✅ World Clock
✅ Configurable Timezone
✅ Character World State
✅ Activity Transition
✅ Daily Routine
✅ Life Scheduler
✅ Persistent Goals
✅ Goal Progress
✅ Milestones
✅ Life Events
✅ World Timeline
✅ World Event Bus
✅ World Snapshot
✅ Crash Recovery
✅ Event Idempotency
✅ Missed Event Policy
✅ Background Life
✅ Ambient Events
✅ World / Behavior Integration
✅ World / Memory Integration
✅ World / Topic Integration
✅ World / Agent Integration
✅ Initiative Integration
✅ Background Message Safety Limits

✅ WebUI World Dashboard
✅ WebUI Routine
✅ WebUI Goals
✅ WebUI Timeline
✅ WebUI Events
✅ WebUI Simulator
✅ WebUI Replay
✅ WebUI Health
✅ WebUI Runtime Toggle

✅ Database Migration
✅ Unit Tests
✅ Integration Tests
✅ Long-term Simulation Tests
✅ Regression Tests
```

同时保持：

```text id="d2x7be"
✅ QQ 端无任何 Bot 命令
✅ QQ 端无 Agent 命令
✅ QQ 端无 Tool 命令
✅ WebUI 是唯一管理入口
✅ Persona 正常
✅ Memory 正常
✅ Semantic Retrieval 正常
✅ Behavior 正常
✅ Initiative 正常
✅ Tool Runtime 正常
✅ Agent Runtime 正常
✅ NapCat / OneBot 正常
```

---

# v0.8 §122 一百二十二、开发硬性要求

1. 首先完整阅读 CatooBot v0.7 源码。
2. 理解已有 BehaviorScheduler，不允许创建第二套独立 Scheduler。
3. 理解已有 CharacterState，不允许简单复制一套新的 State。
4. 理解 Agent Goal / Task，不要建立互相冲突的长期任务模型。
5. World Runtime 必须和 QQ Chat 解耦。
6. World Runtime 崩溃不能拖垮 QQ Chat。
7. Background Life 不能自动等于 Background Message。
8. 所有主动消息必须经过现有 Initiative Gate。
9. 所有后台事件必须有频率限制。
10. 所有状态变化必须可追踪。
11. 所有后台事件必须具备幂等性。
12. Scheduler 重启不能重复执行过去事件。
13. 不允许事件爆炸。
14. 不允许 Task 爆炸。
15. 不允许 Background Message 爆炸。
16. 不允许自动创建无限 Persistent Goals。
17. 不允许虚构现实世界中真实发生的行为。
18. 虚拟角色活动必须明确属于 Character World。
19. 用户现实世界信息与角色虚拟世界状态必须分离。
20. Agent 不得直接写 World State。
21. Memory 不得自动等于 World Event。
22. World Event 不得自动等于 Memory。
23. Topic 不得自动等于 Goal。
24. Goal 不得自动等于 Agent Task。
25. 必须保持 Persona / Memory / Relationship / Behavior / Agent / World 各层职责分离。
26. 所有新增配置必须可以 WebUI 管理。
27. 所有高风险或高频行为必须存在硬限制。
28. 所有后台能力都必须可以整体关闭。
29. Simulator / Replay 默认不得发送真实 QQ 消息。
30. 优先保证长期稳定性，而不是增加大量“生活细节”。

---

# v0.8 §123 一百二十三、最终开发目标

完成 v0.8 后，CatooBot 应该从：

```text id="v9kty1"
“一个会聊天、会记忆、会使用工具、会执行任务的 AI”
```

进一步变成：

```text id="o8w4k3"
“一个有持续时间线的虚拟角色。”
```

理想状态：

```text id="k0e5mx"
用户离开了几天
        ↓
世界时间继续流动
        ↓
角色状态继续变化
        ↓
角色自己的长期项目继续存在
        ↓
Topic 和 Memory 继续沉淀
        ↓
用户再次出现
        ↓
角色自然恢复关系与上下文
```

而不是：

```text id="j17xg4"
用户上线
↓
读取最近消息
↓
马上开始聊天
```

---

# v0.8 §124 一百二十四、现在开始

**首先完整阅读当前 CatooBot v0.7 的源码、数据库 Schema、Character State、Behavior Scheduler、Initiative Engine、Agent Runtime、Memory Engine、Topic Engine 和 WebUI。**

先分析：

```text id="2bky7m"
哪些能力已经存在
哪些能力可以直接复用
哪些能力需要升级
哪些能力不能重复实现
```

然后以最小侵入方式开发：

```text id="v6v6ui"
World Runtime
+
Persistent World State
+
Background Life
```

不要重写现有稳定系统。

不要为了“拟人化”制造大量随机行为。

目标不是让角色一直在后台“做事情”，而是让：

> **时间、状态、目标、事件、记忆和关系能够形成一条连续且可解释的角色生命线。**