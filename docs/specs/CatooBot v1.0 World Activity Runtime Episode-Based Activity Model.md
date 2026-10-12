# CatooBot v1.0
# World Activity Runtime
# Episode-Based Activity Model
# 持续活动片段 + 活动时间模型 + 世界状态一致性 + Social Cognition 联动优化

---

# v1.0 §1 一、版本定位

CatooBot v0.9 已经完成。

目前已经具备：

- Persona
- Character Identity
- Memory / Semantic Memory / Episodic Memory
- Relationship
- Behavior Engine
- Initiative Engine
- Response Timing
- Persistent World
- World Timeline
- Persistent Goals
- Agent Runtime
- Tool Runtime
- Social Cognition Engine
- Group Conversation Monitor
- Group Conversation Buffer
- Active Conversation Thread
- Conversation Continuation Detection
- 5-message Group Observer
- Participation Decision
- Social Attention
- Social Fatigue
- Character Activity Context
- WebUI

当前 v0.8 / v0.9 世界活动系统已经可以运行，但实际日志暴露出一个结构性问题：

> **Activity 仍然更像“当前状态标签”，而不是“具有开始时间、持续时间、结束时间和延续逻辑的活动片段”。**

因此 v1.0 不以增加更多活动种类为目标。

本版本的核心目标是：

> **重构 World Activity Runtime，使“活动”成为有生命周期、有持续时间、有惯性、有结束条件的 Activity Episode。**

---

# v1.0 §2 二、目前已确认的问题

当前日志存在类似：

```text
09-30 08:00  activity  在厨房吃早饭
09-30 09:00  ambient   吃早饭的时候……
09-30 10:00  ambient   吃早饭的时候……
09-30 11:00  ambient   吃早饭的时候……
```

这意味着：

```text id="a4h8f3"
Activity = 吃早饭
```

实际上持续了 3 小时。

同时还存在：

```text id="a3n6r9"
14:00 在 Minecraft 里建房子
14:01 在客厅看剧
14:03 在房间听歌
14:05 在房间打游戏
```

这意味着当前活动切换机制存在：

- Activity 选择过于频繁
- `transition_minutes` 没有真正成为有效边界
- Routine 每 Tick 参与 Activity 决策
- Activity 缺少明确的生命周期
- Ambient 与 Activity 的生命周期耦合不正确

---

# v1.0 §3 三、v1.0 最重要的架构变化

必须从：

```text
Tick
 ↓
Routine
 ↓
Pick Activity
 ↓
Set Activity
```

升级成：

```text
World Clock
 ↓
Activity Planner
 ↓
Create Activity Episode
 ↓
Activity Runtime
 ↓
等待 Episode 到期
 ↓
Evaluate Continue / Extend / Transition
 ↓
Create Next Episode
```

核心原则：

> **World Tick 不应该每分钟重新决定“角色现在做什么”。**

World Tick 只是让世界时间前进。

真正的 Activity Decision 只应该发生在：

- 当前 Episode 即将结束
- 当前 Episode 被主动打断
- Routine / World Event 发生重大变化
- 用户交互导致活动改变
- Goal / Agent / Character State 触发有效转移
- 系统恢复需要重新建立当前 Episode

---

# v1.0 §4 四、必须先做的事情

在任何修改之前：

1. 完整阅读当前 CatooBot v0.9 源码。
2. 重点阅读：
   - `app/world/`
   - `CharacterState`
   - `BehaviorScheduler`
   - `Activity`
   - `Routine`
   - `WorldTimeline`
   - `AmbientLife`
   - `PersistentGoal`
   - `InitiativeEngine`
   - `Social Cognition`
   - `Character Context`
   - `WebUI World`
3. 找出当前 Activity 的真实调用链。
4. 找出所有修改 Activity 的入口。
5. 找出所有绕过 `can_change_activity()` 的路径。
6. 找出所有 `force=True` 修改活动的调用。
7. 找出所有可能导致 Activity 在普通 Tick 中被重新选择的地方。
8. 找出 Ambient 如何读取当前 Activity。
9. 找出 Social Cognition 如何读取 Character Activity Context。

**不要根据本 Prompt 假设代码结构一定如此。**

必须以当前 v0.9 实际源码为准增量修改。

---

# v1.0 §5 五、核心设计：Activity Episode

新增：

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

字段可根据现有项目调整，但必须至少表达：

```text
是什么活动
什么时候开始
预计什么时候结束
当前是否仍在持续
为什么结束
由什么机制创建
```

---

# v1.0 §6 六、Activity Episode 状态

至少：

```text
scheduled
active
extended
completed
interrupted
cancelled
expired
```

建议：

### scheduled

已经计划，但尚未开始。

### active

当前正在进行。

### extended

已经超过原计划结束时间，但活动被延长。

### completed

自然结束。

### interrupted

被其他事件/用户行为中断。

### cancelled

计划取消。

### expired

系统发现已经超过生命周期且没有成功恢复。

---

# v1.0 §7 七、World State 中的 Activity 改造

当前 Character / World State 中不要只保存：

```text
activity = "playing_game"
```

应该至少保存：

```text
current_activity_episode_id
current_activity
activity_started_at
activity_planned_end_at
activity_status
activity_source
```

如果目前已有 `activity` 字段：

保留它作为：

```text
current activity snapshot
```

但真正的生命周期以 Episode 为准。

---

# v1.0 §8 八、Activity Episode 是活动状态的唯一事实来源

以后：

```text
CharacterState.activity
```

只是当前 Episode 的派生状态。

即：

```text
ActivityEpisode
      ↓
WorldState
      ↓
CharacterState
      ↓
Behavior / Social Cognition
```

不要出现：

```text
ActivityEpisode = gaming
CharacterState = watching_show
```

这种状态分裂。

---

# v1.0 §9 九、Primary Activity

每个时刻只能存在一个：

```text
primary_activity_episode
```

这是角色当前正在进行的主要活动。

例如：

```text
Primary:
吃早饭
```

---

# v1.0 §10 十、Secondary Context

允许额外存在：

```text
secondary_context
```

例如：

```text
Primary:
吃早饭

Secondary:
刷手机
```

注意：

**Secondary Context 不应该注册成新的 Primary Activity Episode。**

这样可以表达：

```text
吃饭的时候刷手机
```

而不会导致：

```text
08:00 吃饭
08:05 刷手机
08:10 吃饭
08:15 刷手机
```

不断切换。

---

# v1.0 §11 十一、Activity 与 Micro Activity

v1.0 只要求：

```text
Primary Activity
+
Secondary Context
```

暂时不要建立复杂的多级活动树。

例如：

```text
Primary:
watching_show

Secondary:
checking_phone
listening_to_music
```

最终：

```text
Social Cognition
```

应该仍然把：

```text
watching_show
```

作为主要活动。

---

# v1.0 §12 十二、Activity Duration Profile

每个 Activity 必须拥有自己的持续时间特征。

例如配置：

```yaml
activities:

  breakfast:
    min_duration_minutes: 15
    typical_duration_minutes: 30
    max_duration_minutes: 45

  lunch:
    min_duration_minutes: 20
    typical_duration_minutes: 40
    max_duration_minutes: 60

  watching_show:
    min_duration_minutes: 20
    typical_duration_minutes: 60
    max_duration_minutes: 180

  gaming:
    min_duration_minutes: 30
    typical_duration_minutes: 90
    max_duration_minutes: 240

  listening_music:
    min_duration_minutes: 10
    typical_duration_minutes: 30
    max_duration_minutes: 120

  scrolling_phone:
    min_duration_minutes: 5
    typical_duration_minutes: 20
    max_duration_minutes: 60
```

这些仅作为默认示例。

真正配置需要通过 WebUI 管理。

---

# v1.0 §13 十三、duration 不是固定时间

例如：

```text
gaming:
typical = 90 min
```

不意味着：

```text
90 分钟后强制结束。
```

它表达的是：

> 这个活动通常会持续多久。

Episode 可以：

```text
提前结束
正常结束
延长
```

---

# v1.0 §14 十四、planned_end_at

每个 Episode 创建时必须得到：

```text
planned_end_at
```

例如：

```text
14:00
开始 Minecraft

planned_end_at = 15:25
```

在：

```text
14:01
14:02
14:03
```

Tick 中：

**绝对不能重新随机选择新的 Activity。**

---

# v1.0 §15 十五、World Tick 的新职责

每 60 秒 Tick：

只检查：

```text
现在是否已经接近或超过 planned_end_at？
```

如果：

```text
now < planned_end_at
```

则：

```text
保持当前 Episode
```

什么都不切换。

---

# v1.0 §16 十六、Transition Window

增加：

```yaml
world:
  activity:
    transition_window_minutes: 5
```

含义：

> 在预计结束前的一小段时间，开始准备下一次 Activity Decision。

例如：

```text
planned_end = 15:30

15:25
进入 transition window
```

但：

**不要因此立即切换。**

应该进入：

```text
transition_pending
```

状态。

---

# v1.0 §17 十七、Transition Decision

在 transition window：

系统只做一次：

```text
continue
extend
transition
```

决策。

---

# v1.0 §18 十八、Continue

如果当前活动仍然合理：

```text
continue
```

更新：

```text
planned_end_at
```

例如：

```text
原：
15:30

延长：
16:10
```

不能创建新的 Activity Episode。

---

# v1.0 §19 十九、Extend

如果：

```text
当前 Activity
+
当前状态
+
当前时间
+
当前 Focus
```

都支持继续：

允许：

```text
extend
```

例如：

```text
Minecraft
→ +45 min
```

---

# v1.0 §20 二十、Transition

只有当前活动：

```text
即将结束
+
不再适合继续
```

才创建新的 Episode。

---

# v1.0 §21 二十一、Activity Transition 不允许由简单随机直接决定

禁止：

```python
if random.random() < 0.45:
    continue
else:
    transition()
```

这一类代码不能继续成为核心机制。

---

# v1.0 §22 二十二、Activity Continuation Decision

建立：

```text
ActivityContinuationEvaluator
```

输入：

```text
current activity
elapsed time
energy
mood
focus
time of day
routine
current goals
current topic
recent activity history
```

输出：

```json
{
  "decision": "continue",
  "recommended_extension_minutes": 45,
  "reason_code": "high_focus"
}
```

或者：

```json
{
  "decision": "transition",
  "next_activity_hint": "rest",
  "reason_code": "energy_low"
}
```

---

# v1.0 §23 二十三、Decision 不是 Probability

和 v0.9 Social Cognition 一样：

不要使用：

```text
confidence -> random threshold
```

而要使用：

```text
state
+
rules
+
activity profile
+
context
```

形成结构化 Decision。

---

# v1.0 §24 二十四、Activity Momentum

增加：

```text
activity_momentum
```

范围：

```text
0.0 ~ 1.0
```

表示：

> 当前活动继续维持的惯性。

它不是直接的“继续概率”。

例如：

```text
gaming:
0.85

watching_show:
0.80

break:
0.40

breakfast:
0.25

scrolling_phone:
0.30
```

但：

最终是否继续仍由 ActivityContinuationEvaluator 决定。

---

# v1.0 §25 二十五、Momentum 的来源

Momentum 可以来自：

```text
activity_type
elapsed_time
focus
goal
interest
current_state
recent_user_interruption
fatigue
```

例如：

```text
Minecraft + 高 Focus
→ momentum 高

吃饭 + 已经 40 分钟
→ momentum 下降

看剧 + 剧情正高潮
→ momentum 高
```

---

# v1.0 §26 二十六、活动结束原因

每个 Episode 完成时必须记录：

```text
transition_reason
```

至少：

```text
natural_completion
schedule_boundary
extended
user_interruption
goal_trigger
state_change
energy_low
sleep_transition
manual
recovery
```

---

# v1.0 §27 二十七、Activity 结束必须可解释

WebUI 中应该可以看到：

```text
14:00
Minecraft

15:32
结束

reason:
natural_completion

next:
watching_show
```

而不是只有：

```text
activity changed
```

---

# v1.0 §28 二十八、Routine 的职责重新定义

当前：

```text
routine.period
=
当前时段的候选 Activity 列表
```

v1.0 改成：

> Routine 是“生活倾向 / 活动意图”，不是每分钟活动抽签器。

例如：

```yaml
routine:

  morning:
    preferred:
      - breakfast
      - phone
      - study

  afternoon:
    preferred:
      - gaming
      - watching_show
      - building
      - music

  evening:
    preferred:
      - dinner
      - gaming
      - watching_show
      - music

  night:
    preferred:
      - relax
      - reading
      - rest
```

---

# v1.0 §29 二十九、Routine 不直接创建 Activity

正确：

```text
Routine
 ↓
Activity Planner
 ↓
Activity Episode
```

错误：

```text
Routine
 ↓
set_activity()
```

---

# v1.0 §30 三十、Activity Planner

新增：

```text
ActivityPlanner
```

职责：

```text
根据当前时间
+
Routine
+
Current State
+
Activity History
+
Persistent Goals
+
Character Preferences

生成：
next Activity Episode
```

---

# v1.0 §31 三十一、Activity Planner 不需要规划一天全部活动

v1.0 推荐：

> 滚动规划未来 1～4 小时。

例如：

```text
当前：
14:00

规划：
14:00~15:30 Minecraft
15:30~15:50 Rest
15:50~17:00 Watching Show
```

不要求：

```text
一次规划 24 小时。
```

---

# v1.0 §32 三十二、Rolling Horizon

配置：

```yaml
world:
  activity:
    planning_horizon_minutes: 240
```

系统只保证：

```text
未来几小时
```

有合理活动。

---

# v1.0 §33 三十三、Future Schedule 是计划，不是真实状态

必须区分：

```text
planned_activity
```

与：

```text
current_activity
```

例如：

```text
15:30 计划休息

但 15:25 当前 Minecraft 状态仍然很投入
```

Activity Planner 可以：

```text
延迟休息
```

---

# v1.0 §34 三十四、计划具有弹性

增加：

```text
schedule_flexibility
```

例如：

```yaml
activities:
  gaming:
    schedule_flexibility: 0.8

  breakfast:
    schedule_flexibility: 0.2
```

含义：

> 越高，越允许因当前状态延长或提前结束。

不是随机概率。

---

# v1.0 §35 三十五、固定活动与自由活动

建议支持：

```text
fixed
flexible
free
```

例如：

```text
早餐:
fixed

Minecraft:
flexible

听歌:
free
```

---

# v1.0 §36 三十六、固定活动

固定活动：

```text
08:00 早餐
12:00 午餐
18:00 晚餐
00:30 睡觉
```

这些作为：

```text
hard schedule anchors
```

但是：

允许小范围提前/延后。

---

# v1.0 §37 三十七、Anchor

建立：

```text
ScheduleAnchor
```

例如：

```text
lunch:
target = 12:00
window = ±45 min
```

这样不会出现：

```text
12:00 午餐
```

变成：

```text
15:00 午餐
```

---

# v1.0 §38 三十八、Anchor 优先级

```text
sleep
meal
fixed event
routine activity
free activity
```

从高到低。

---

# v1.0 §39 三十九、自由活动池

没有固定安排时：

```text
free_time
```

Activity Planner 可以从：

```text
gaming
music
show
reading
relax
```

中选择。

但：

**选择一次以后，就形成 Episode。**

---

# v1.0 §40 四十、当前活动延续

如果：

```text
current_activity
```

仍然适合当前时间段：

优先：

```text
continue
```

而不是重新选新活动。

---

# v1.0 §41 四十一、Activity Completion

活动可以有：

```text
completion_condition
```

例如：

```text
meal:
吃完

watching_show:
一集结束 / 主动结束

gaming:
主动离开 / 精力下降

reading:
章节结束 / 主动停止
```

如果实际条件无法判断：

使用：

```text
planned_end
```

作为 fallback。

---

# v1.0 §42 四十二、Interruptible

每个 Activity 支持：

```text
interruptible: true/false
```

例如：

```text
sleeping:
false / limited

gaming:
true

watching_show:
true

eating:
true
```

---

# v1.0 §43 四十三、用户交互可以打断 Activity

例如：

角色：

```text
正在看剧
```

用户主动聊天：

```text
@Bot 你在干嘛？
```

Activity 不需要立即永久切换。

可以：

```text
Primary Activity:
watching_show

Secondary Context:
chatting with user
```

这样会比：

```text
watching_show
↓
chatting
↓
用户不说话
↓
watching_show
```

自然很多。

---

# v1.0 §44 四十四、Chatting 不一定是 Activity

把：

```text
chatting
```

尽量作为：

```text
interaction overlay
```

而不是 Primary Activity。

因为：

```text
正在看剧
+
和用户聊天
```

可以同时存在。

---

# v1.0 §45 四十五、Interaction Overlay

建议增加：

```text
InteractionOverlay
```

例如：

```text
watching_show
+
chatting_with_user
```

而：

```text
sleeping
```

可能变成：

```text
sleeping
+
responding_to_user
```

但应根据角色设定决定。

---

# v1.0 §46 四十六、Interaction Overlay 对 Social Cognition 非常重要

v0.9 已经完成群聊认知。

v1.0 必须保证它读取的不是：

```text
activity = chatting
```

而是：

```text
primary_activity = watching_show
interaction_overlay = chatting_with_user
current_focus = user conversation
```

这样群聊认知才能理解：

> “她刚刚在看剧，但现在暂时在和我聊天。”

---

# v1.0 §47 四十七、Social Cognition Context 升级

现有 v0.9：

```text
Character Activity Context
```

改成：

```text
Character World Context
```

至少提供：

```text
primary_activity
activity_elapsed_minutes
activity_remaining_estimate
location
mood
energy
focus
secondary_context
interaction_overlay
current_topic
current_goal
```

---

# v1.0 §48 四十八、群聊 Follow-up 必须读取 ActivityEpisode

场景：

```text
Bot：
在看剧。

User：
看的什么剧？
```

Social Cognition：

不要只读：

```text
activity = watching_show
```

而应该读：

```text
current_episode.activity_detail
```

例如：

```text
activity_detail:
正在看《XXX》第三集
```

这样就能自然回答。

---

# v1.0 §49 四十九、Activity Detail

每个 Episode 允许：

```text
activity_detail
```

例如：

```text
activity_type:
watching_show

activity_detail:
正在看《XXX》第三集
```

或者：

```text
gaming

activity_detail:
正在 Minecraft 里建房子
```

---

# v1.0 §50 五十、Activity Detail 不应该每分钟随机生成

Activity Detail 创建时生成一次：

```text
Episode Start
```

之后可以：

```text
Episode Progress Update
```

但不要：

```text
14:00：
在 Minecraft 建房子

14:01：
挖矿

14:02：
打怪

14:03：
装修
```

除非真的建立了更细粒度的 Activity Progress System。

v1.0 暂不需要。

---

# v1.0 §51 五十一、Activity Detail 可以有 Progress

对于：

```text
gaming
building_project
watching_show
reading
```

可选：

```text
progress_summary
```

例如：

```text
Minecraft：
房子已经搭完主体结构
```

这样世界和 Social Cognition 都能使用。

---

# v1.0 §52 五十二、Progress Update 应该稀疏

默认：

```text
每 30~120 分钟
```

或者：

```text
发生 milestone
```

才更新。

---

# v1.0 §53 五十三、不要为了“生活感”制造大量日志

这一点非常重要。

v1.0 的目标：

```text
减少虚假活动
```

不是：

```text
增加更多日志
```

---

# v1.0 §54 五十四、Ambient Event 重构

原有：

```text
ambient:
每 60 分钟检查
max 4/day
```

保留：

```text
硬冷却
每天上限
```

但改成：

```text
Activity Episode-based Ambient Opportunity
```

---

# v1.0 §55 五十五、Ambient Opportunity

每个 Activity Profile 可以定义：

```yaml
ambient:
  eligible: true
  max_per_episode: 1
  preferred_phase:
    - middle
    - late
```

例如：

```text
breakfast:
max_per_episode = 1

gaming:
max_per_episode = 2

sleep:
eligible = false
```

---

# v1.0 §56 五十六、Ambient 不能证明 Activity 仍然存在

例如：

```text
Breakfast Episode:
08:00~08:30
```

Ambient：

```text
08:15
刷手机
```

它属于：

```text
Breakfast Episode
```

而不是：

```text
Current Activity = breakfast
```

---

# v1.0 §57 五十七、Ambient 文本风格

不要：

```text
“吃早饭的时候，吃饭的时候又顺手刷了会儿手机”
```

重复 Activity。

改为：

```text
“顺手刷了一会手机。”
```

或者：

```text
“刚才突然想到午饭吃什么。”
```

Activity 已经由 Episode 表达。

Ambient 只表达：

> 这段活动里发生的一点小事。

---

# v1.0 §58 五十八、Ambient 与 Activity Episode ID 绑定

数据库：

```text
ambient_events
    episode_id
```

这样：

```text
08:15
Ambient
episode_id = breakfast_0800
```

WebUI 可以直接跳转到：

```text
Breakfast Episode
```

---

# v1.0 §59 五十九、Relationship Event 降噪

当前日志：

```text
04:00 relationship
05:00 relationship
```

如果来源只是：

```text
user came to chat
```

不要每次都生成 Timeline relationship Event。

改成：

```text
InteractionEvent
```

或者直接由：

```text
session/activity log
```

记录。

只有真正影响关系模型的变化才生成：

```text
RelationshipEvent
```

---

# v1.0 §60 六十、Timeline 分层

建立：

```text
timeline_visibility
```

至少：

```text
major
normal
trace
```

建议：

### major

- milestone
- relationship stage change
- important goal event

### normal

- Activity started / ended
- sleep / wake
- routine anchor
- project progress

### trace

- minor ambient
- temporary overlay
- trivial interaction

WebUI 默认：

```text
major + normal
```

---

# v1.0 §61 六十一、Activity Timeline 记录方式

不要每分钟写：

```text
activity still gaming
```

只记录：

```text
ActivityStarted
ActivityExtended
ActivityCompleted
ActivityInterrupted
```

这样世界日志会非常干净。

---

# v1.0 §62 六十二、Activity Episode 数据库存储

建议至少增加：

```text
activity_episodes
```

字段：

```text
id
character_id
activity_type
activity_name
activity_detail

started_at
planned_end_at
ended_at

status

location
social_state

source
transition_reason

parent_episode_id

created_at
updated_at
```

如果已有 Character State 表：

继续保留 Snapshot。

---

# v1.0 §63 六十三、Episode 与 Timeline

Timeline 只引用：

```text
episode_id
```

而不是重复存一份所有内容。

---

# v1.0 §64 六十四、Activity History Query

WebUI：

```text
当前活动：
Minecraft

开始：
14:00

持续：
82 分钟

预计结束：
15:45

状态：
active

来源：
routine

连续性：
high
```

---

# v1.0 §65 六十五、Activity Consistency Checker

v1.0 强烈建议新增：

```text
WorldConsistencyChecker
```

每次：

```text
snapshot
```

之前检查：

### 规则 1

不能：

```text
current_activity.started_at > now
```

### 规则 2

不能：

```text
ended_at < started_at
```

### 规则 3

不能存在：

```text
两个 active primary activity
```

### 规则 4

current Episode 必须：

```text
status = active
```

### 规则 5

Location / Activity 不允许出现明显冲突。

---

# v1.0 §66 六十六、状态冲突示例

如果：

```text
activity = sleeping
location = kitchen
```

Consistency Checker 应：

```text
WARNING
```

而不是静默接受。

---

# v1.0 §67 六十七、自动修复原则

一致性问题：

优先：

```text
repair from ActivityEpisode
```

例如：

```text
CharacterState.activity
```

错了：

从：

```text
current_activity_episode
```

重新同步。

不要：

```text
random reset
```

---

# v1.0 §68 六十八、Recovery

重启时：

```text
latest snapshot
+
current active Episode
```

检查：

```text
planned_end_at
```

如果：

```text
now < planned_end
```

继续活动。

如果：

```text
now > planned_end
```

不要创建：

```text
一堆补发活动
```

只做一次：

```text
recovery evaluation
```

---

# v1.0 §69 六十九、Recovery Evaluation

例如：

```text
快照：
14:00 Minecraft

现在：
18:00
```

不要：

```text
14:00 Minecraft
15:00 看剧
16:00 听歌
17:00 打游戏
```

全部补出来。

应该：

```text
14:00~18:00
发生时间跳跃
↓
恢复当前世界状态
↓
选择一个合理当前 Episode
```

如果有：

```text
catch_up
```

只记录：

```text
world time advanced
```

而不是伪造历史活动。

---

# v1.0 §70 七十、World Simulation

WebUI 已有世界预演。

v1.0 必须改成：

```text
advance(1h)
```

通过：

```text
ActivityEpisode lifecycle
```

自然推进。

不能：

```text
每 minute random activity
```

---

# v1.0 §71 七十一、模拟测试示例

快进：

```text
1 小时
```

期望：

```text
0~少量 Episode transitions
```

而不是：

```text
60 个 Activity events
```

---

# v1.0 §72 七十二、快进 3 天测试

期望：

```text
合理的 Activity Episode 数量
合理的 Meal
合理的 Sleep
少量 Ambient
少量 Life Events
```

而不是：

```text
几百条重复活动。
```

---

# v1.0 §73 七十三、活动事件爆发保护

旧规则：

```text
max 10/hour
max 40/day
```

保留。

但 v1.0 还需要：

```text
max_activity_transitions_per_hour
```

例如：

```yaml
world:
  activity:
    max_transitions_per_hour: 6
```

超过：

```text
禁止继续快速切换
```

并优先：

```text
继续当前活动
```

---

# v1.0 §74 七十四、连续活动重复保护

禁止：

```text
gaming
→ music
→ gaming
→ music
```

五分钟内反复切换。

可以加入：

```text
recent_activity_history
```

例如最近：

```text
3~5 个 Episode
```

Activity Planner 应避免无意义来回切换。

---

# v1.0 §75 七十五、Activity Anti-Bounce

建立：

```text
ActivityBounceGuard
```

检测：

```text
A
B
A
B
A
```

如果短时间反复：

优先：

```text
延长当前活动
```

或：

```text
进入 neutral / rest
```

而不是继续来回跳。

---

# v1.0 §76 七十六、同类 Activity 合并

例如：

```text
14:00 gaming
14:30 gaming
14:50 gaming
```

如果没有真正离开：

应该是：

```text
14:00~15:30 gaming
```

不是：

```text
3 个独立 Episode
```

---

# v1.0 §77 七十七、Activity Episode Merge

支持：

```text
merge_adjacent_same_activity
```

但：

如果中间确实存在：

```text
activity B
```

则不合并。

---

# v1.0 §78 七十八、Location Transition

如果：

```text
Activity:
watching_show
```

Location：

```text
living_room
```

切换：

```text
gaming
```

Location：

```text
room
```

需要有：

```text
location transition
```

但不一定每次生成 Timeline Event。

---

# v1.0 §79 七十九、Location 是派生状态

优先：

```text
Activity Profile
```

定义推荐 location。

如果：

```text
Character Preference / World Event
```

改变：

才覆盖。

---

# v1.0 §80 八十、Social State 是派生状态

同样：

```text
gaming → alone
watching_show → alone
eating → alone
```

只是默认值。

不要让：

```text
social_state
```

独立随机。

---

# v1.0 §81 八十一、Energy 不应该每 tick 大幅变化

建议：

```text
Activity Episode
```

决定：

```text
energy_delta_per_hour
```

例如：

```text
sleeping:
+15/h

gaming:
-8/h

watching_show:
-3/h

eating:
+4/h
```

但必须设置上下限：

```text
0~1
```

---

# v1.0 §82 八十二、Energy 需要衰减模型

可以：

```text
energy = current_energy + activity_delta * elapsed_hours
```

而不是：

```text
每分钟 random
```

---

# v1.0 §83 八十三、Mood 同样尽量连续

不要：

```text
happy
↓
sad
↓
happy
```

每分钟乱切。

Mood 应：

```text
slow-moving state
```

受到：

```text
activity
interaction
life events
rest
```

影响。

---

# v1.0 §84 八十四、Focus

Focus 可以：

```text
higher when:
current activity stable
goal relevant
interesting topic
```

lower when：

```text
fatigue high
activity boring
frequent interruptions
```

---

# v1.0 §85 八十五、Social Cognition 联动：必须更新

v0.9 已经完成，不允许推翻。

但 v1.0 完成后，要将它的：

```text
Character Activity Context
```

升级为：

```text
World Activity Context
```

---

# v1.0 §86 八十六、Social Cognition 新 Context

至少：

```json
{
  "primary_activity": {
    "type": "watching_show",
    "detail": "正在看《XXX》第三集",
    "elapsed_minutes": 38,
    "planned_remaining_minutes": 25
  },

  "secondary_context": [
    "listening_to_music"
  ],

  "interaction_overlay": {
    "active": false
  },

  "location": "living_room",
  "focus": "high",
  "energy": 0.72
}
```

---

# v1.0 §87 八十七、Social Cognition 不需要知道全部 Activity History

只需要：

```text
current episode
recent transitions
relevant detail
```

默认：

```text
最近 3~5 个 Activity Episode
```

即可。

---

# v1.0 §88 八十八、Social Cognition 可以使用 Activity Duration

例如：

```text
watching_show
elapsed = 5min
```

用户突然问：

```text
在干嘛？
```

非常容易回答。

如果：

```text
watching_show
elapsed = 3h
```

则系统应该：

```text
检查 current Episode validity
```

如果已经过期：

先修复 World State。

不要让 Social Cognition 读取过时活动。

---

# v1.0 §89 八十九、Social Cognition 不得自己修复 World State

如果：

```text
Activity Episode
```

过期：

Social Cognition 只能：

```text
request world state refresh
```

由：

```text
WorldStateService
```

修复。

---

# v1.0 §90 九十、Social Cognition 与 Interaction Overlay

场景：

```text
Primary Activity:
watching_show

User：
@Bot 在干嘛？
```

Bot 回复：

```text
在看剧。
```

此时可以建立：

```text
Interaction Overlay:
chatting
```

如果用户：

```text
看的什么？
```

读取：

```text
Primary Activity Detail
```

而不是把：

```text
Primary Activity = chatting
```

这样会更自然。

---

# v1.0 §91 九十一、Chat Overlay 的生命周期

默认：

```text
start:
收到直接消息

end:
对话窗口关闭
```

但不改变：

```text
Primary Activity
```

---

# v1.0 §92 九十二、Social Cognition 参与群聊

v0.9 的：

```text
5-message Observer
```

现在需要读取：

```text
Primary Activity
Activity Detail
Activity Tags
Current Focus
Energy
Social State
Interaction Overlay
```

而不是只读取：

```text
activity string
```

---

# v1.0 §93 九十三、活动与群聊相关性

例如：

```text
Primary:
Minecraft Building

Group:
Minecraft
```

Social Cognition：

```text
character_activity_relevance ↑
```

如果：

```text
Primary:
breakfast

Group:
Minecraft
```

则：

```text
activity relevance
≈ low
```

即使角色长期喜欢 Minecraft，也不能简单强制参与。

---

# v1.0 §94 九十四、活动结束后上下文必须即时刷新

当：

```text
ActivityEpisode
```

结束：

必须：

```text
Character Context invalidation
```

使：

```text
Social Cognition Cache
Behavior Context
Initiative Context
```

失效/更新。

防止：

```text
World:
gaming

Social Cognition:
仍然以 gaming 为当前活动
```

---

# v1.0 §95 九十五、缓存失效

Activity Transition：

触发：

```text
invalidate:
activity_context
initiative_context
social_context
```

Memory 不需要全量失效。

---

# v1.0 §96 九十六、WebUI Activity Detail

增加页面：

```text
/world/activities
```

显示：

```text
Current Episode
Start Time
Planned End
Elapsed
Remaining
Duration Profile
Location
Activity Detail
Secondary Context
Transition Reason
Momentum
Source
```

---

# v1.0 §97 九十七、Activity History

管理员可以按：

```text
today
yesterday
7 days
custom range
```

查看：

```text
Activity Episodes
```

---

# v1.0 §98 九十八、Activity Timeline

例如：

```text
08:00  早餐          35m
08:35  刷手机         18m
08:53  Minecraft      96m
10:29  休息           20m
10:49  看剧           71m
12:00  午餐           42m
```

这个才应该成为新的 World Timeline 主体。

---

# v1.0 §99 九十九、Activity Statistics

WebUI 显示：

```text
今日：
Gaming 2h 10m
Watching Show 1h 15m
Music 45m
Eating 1h 05m
Sleep 8h
```

但：

**这些是虚拟世界统计，不代表真实用户行为。**

---

# v1.0 §100 一百、Activity Quality Metrics

WebUI 增加：

```text
平均 Episode 时长
平均 Transition 次数
Activity Bounce 次数
Forced Transition 次数
Extension 次数
Interrupted 次数
Consistency Error 数
```

重点观察：

```text
activity_bounce
```

如果很高：

说明系统仍然在乱切。

---

# v1.0 §101 一百零一、目标指标

v1.0 不是追求：

```text
更多活动
```

而是：

```text
更合理的活动持续时间
更少无意义 Transition
更少重复活动
更少 World Timeline 噪声
更一致的 Current Activity Context
```

---

# v1.0 §102 一百零二、推荐初始质量指标

可以在 Test 环境中观察：

```text
平均 Activity Duration
```

以及：

```text
Transitions / Active Hour
```

如果：

```text
Transitions > 4~6 / hour
```

一般应该警惕。

但这个数字只是质量检查参考，不写死成绝对规则。

---

# v1.0 §103 一百零三、世界活动一致性检查

增加：

```text
WorldConsistencyChecker
```

至少检查：

### Activity

```text
只能有一个 active primary episode
```

### Location

```text
必须与 Primary Activity 相容
```

### Schedule

```text
不能同时存在两个互斥的 fixed anchor
```

### Sleep

```text
sleeping 与 active gaming 冲突
```

### Time

```text
planned_end < started_at
```

全部报警。

---

# v1.0 §104 一百零四、自动修复优先级

如果发现矛盾：

```text
ActivityEpisode
>
WorldState
>
CharacterState Snapshot
```

越上层越接近真实状态。

---

# v1.0 §105 一百零五、Recovery Priority

重启：

```text
DB Active Episode
↓
检查 now
↓
继续 / 完成 / Replan
```

而不是：

```text
重新随机生成 Activity
```

---

# v1.0 §106 一百零六、World Snapshot

Snapshot 至少保存：

```text
current_episode_id
current_activity
activity_started_at
activity_planned_end_at
secondary_context
current_goal
current_project
mood
energy
focus
location
```

---

# v1.0 §107 一百零七、Snapshot 与 Activity Episode

Snapshot 只是：

> 当前世界的快照。

Episode 才是：

> 当前活动的事实记录。

不要混淆。

---

# v1.0 §108 一百零八、Activity Planner 与 Persistent Goal

长期 Goal：

```text
current_goal
```

可以提高某些 Activity 的优先级。

例如：

```text
Goal:
完成虚拟项目

候选：
building
gaming
watching_show
```

如果角色正在：

```text
高 Focus
```

则：

```text
building
```

更合适。

---

# v1.0 §109 一百零九、Goal 不应强制覆盖生活

Persistent Goal：

不能：

```text
每天 24h build
```

必须仍受：

```text
sleep
meal
rest
energy
```

限制。

---

# v1.0 §110 一百一十、Activity Planner 与 Energy

Planner 应避免：

```text
energy = 0.15
gaming 2 hours
```

除非角色设定允许。

否则：

```text
rest / sleep / light activity
```

优先。

---

# v1.0 §111 一百一十一、Activity Planner 与 Time Anchors

例如：

```text
11:50
```

发现：

```text
12:00 lunch anchor
```

则：

提前结束：

```text
current gaming
```

或者：

将其控制在：

```text
planned_end <= 12:30
```

避免：

```text
12:00 lunch
13:45 still lunch
```

---

# v1.0 §112 一百一十二、Anchor Conflict Resolution

如果：

```text
current episode
```

跨越：

```text
fixed anchor
```

必须：

```text
evaluate transition
```

而不是：

```text
ignore anchor
```

---

# v1.0 §113 一百一十三、活动连续性与现实合理性

推荐定义几个硬逻辑：

```text
sleep:
long duration

meal:
short duration

show:
episode duration

gaming:
long flexible duration

music:
flexible duration

phone:
shorter duration
```

但是：

**不要追求现实世界绝对精确。**

目标是：

```text
看起来合理
```

而不是：

```text
模拟真实人类行为科学实验。
```

---

# v1.0 §114 一百一十四、活动模型不要绑定太强的固定规律

例如：

不要：

```text
每天 8:00 一定早餐
每天 9:00 一定 Minecraft
```

而应该：

```text
routine preference
+
anchors
+
current state
+
history
```

共同决定。

---

# v1.0 §115 一百一十五、Activity Variation

允许每天存在小变化：

```text
早餐略晚
今天多玩一会
今天看剧少一点
```

但：

变化必须是：

```text
bounded
```

不要：

```text
今天早餐 3 小时
明天凌晨才早餐
```

---

# v1.0 §116 一百一十六、Variation 不应该重新使用随机抽签

可使用：

```text
bounded variation
```

例如：

```text
meal start = anchor + small_offset
```

但：

最终仍受：

```text
hard constraints
```

控制。

---

# v1.0 §117 一百一十七、日志改造

不要再产生大量：

```text
“当前仍在吃早饭”
```

只记录 Episode Lifecycle：

```text
ActivityStarted
ActivityExtended
ActivityCompleted
ActivityInterrupted
ActivityRecovered
```

---

# v1.0 §118 一百一十八、日志示例

期望：

```text
08:00 ActivityStarted
    breakfast
    planned_end=08:35

08:13 Ambient
    episode=breakfast_0800
    summary=顺手刷了一会儿手机

08:35 ActivityCompleted
    breakfast
    reason=natural_completion

08:35 ActivityStarted
    free_time
    planned_end=09:00

09:00 ActivityStarted
    minecraft
    planned_end=10:20
```

而不是：

```text
08:00 breakfast
09:00 breakfast
10:00 breakfast
11:00 breakfast
```

---

# v1.0 §119 一百一十九、WebUI Debug

每一个 Episode 点击后可以看到：

```text
Activity:
Minecraft

Started:
14:00

Planned End:
15:30

Actual End:
15:47

Duration:
107 min

Reason:
natural_completion

Extensions:
1

Original Plan:
90 min

Extended:
17 min
```

---

# v1.0 §120 一百二十、活动决策 Trace

对于每一次 Transition：

WebUI 可以显示：

```text
Current Activity
Elapsed
Energy
Focus
Time Period
Routine Preference
Goal Relevance
Momentum
Decision
Reason
Next Activity
```

不需要保存隐藏思维链。

---

# v1.0 §121 一百二十一、不要让模型决定所有 Activity

建议：

### Rule-based

处理：

```text
sleep
meal
anchor
minimum duration
maximum duration
cooldown
hard conflicts
```

### Model-assisted

处理：

```text
continue vs extend
具体 next activity preference
当前状态解释
```

最终：

```text
Rule > Model
```

---

# v1.0 §122 一百二十二、Activity Decision Model 输出

例如：

```json
{
  "decision": "extend",
  "extension_minutes": 30,
  "reason_code": "high_focus",
  "next_hint": null
}
```

或者：

```json
{
  "decision": "transition",
  "next_hint": "rest",
  "reason_code": "energy_low"
}
```

---

# v1.0 §123 一百二十三、模型调用频率

Activity Decision Model：

只在：

```text
transition window
```

调用。

不要：

```text
每分钟调用。
```

---

# v1.0 §124 一百二十四、模型失败时

如果 Activity Decision Model：

```text
timeout
```

使用：

```text
rule-based fallback
```

例如：

```text
活动没到 max_duration
→ continue

已经达到 max_duration
→ transition
```

---

# v1.0 §125 一百二十五、Activity Planner 失败

如果 Planner 失败：

使用：

```text
current activity continuation
+
routine fallback
```

不能：

```text
activity = null
```

---

# v1.0 §126 一百二十六、永远保证 Primary Activity 有值

除非：

```text
system startup / recovery
```

否则：

```text
Primary Activity
```

不能长时间为空。

如果没有合适活动：

使用：

```text
idle
free_time
resting
```

等低语义活动。

---

# v1.0 §127 一百二十七、Activity “Unknown”

允许：

```text
unknown
```

只作为：

```text
短暂 recovery state
```

不得长期存在。

---

# v1.0 §128 一百二十八、Social Cognition 与 Activity “Unknown”

如果：

```text
activity = unknown
```

Social Cognition 不应该假设：

```text
角色正在做什么。
```

应降低：

```text
activity relevance
```

但不影响：

```text
@Bot
follow-up
```

---

# v1.0 §129 一百二十九、v0.9 社交系统允许做的小幅调整

世界活动升级完成后，允许对 v0.9 Social Cognition 做以下兼容性调整：

### 可以改

```text
Character Activity Context
→ World Activity Context
```

### 可以改

```text
activity string
→ episode-aware activity state
```

### 可以改

```text
activity relevance
```

使其读取：

```text
current episode
detail
elapsed
focus
```

### 不要改

```text
5-message observer
active conversation
follow-up
decision model
final gate
```

除非因为接口变化必须适配。

---

# v1.0 §130 一百三十、Social Cognition 的 Activity Context 示例

对于：

```text
14:35
Minecraft
```

应该得到：

```json
{
  "primary_activity": "minecraft_building",
  "activity_detail": "正在 Minecraft 里建房子",
  "elapsed_minutes": 35,
  "planned_remaining_minutes": 40,
  "location": "room",
  "focus": "high",
  "energy": 0.72,
  "secondary_context": [],
  "interaction_overlay": null
}
```

这样：

群里：

```text
A：
最近有人在玩 Minecraft 吗？
```

v0.9 就可以自然得到：

```text
current_activity_relevance = high
```

---

# v1.0 §131 一百三十一、Active Conversation + Activity

例如：

```text
Bot：
我在看剧。

User：
看的什么？
```

Activity Episode：

```text
activity_detail = 正在看《XXX》
```

直接用于 Response Context。

不需要：

```text
重新搜索 Memory
```

---

# v1.0 §132 一百三十二、Active Conversation 不改变 Episode

聊天开始：

```text
interaction_overlay = chatting
```

Activity：

```text
watching_show
```

聊天结束：

```text
interaction_overlay = null
```

Activity：

```text
watching_show
```

继续。

---

# v1.0 §133 一百三十三、用户主动打断 Activity

例如：

```text
用户：
@Bot 过来帮我看看这个
```

如果 Agent Task 开始：

可以：

```text
Primary Activity:
watching_show

Overlay:
assisting_user_task
```

完成后：

```text
回到 watching_show
```

这比：

```text
删除原 Activity
```

自然。

---

# v1.0 §134 一百三十四、如果用户交互持续很久

例如：

```text
聊天 90 分钟
```

如果角色原 Activity 已经自然结束：

那么：

```text
Interaction Overlay
```

可以逐步转为：

```text
Primary Activity = chatting
```

但这个转换必须经过：

```text
Activity Transition
```

而不是硬编码。

---

# v1.0 §135 一百三十五、活动层级建议

v1.0 最终保持：

```text
Primary Activity
Secondary Context
Interaction Overlay
```

这已经足够。

不要做：

```text
Activity
SubActivity
SubSubActivity
Task
MicroTask
...
```

---

# v1.0 §136 一百三十六、数据库 Migration

必须新增/修改：

```text
activity_episodes
activity_profiles
activity_transitions
activity_contexts
```

如果现有数据库已有类似表：

优先迁移，而不是重复创建。

---

# v1.0 §137 一百三十七、旧 Activity 数据迁移

如果历史数据只有：

```text
timestamp
activity
```

无法准确还原持续时间：

不要伪造。

历史数据只作为：

```text
legacy timeline
```

未来新系统从 v1.0 开始生成完整 Episode。

---

# v1.0 §138 一百三十八、不要为了补历史而虚构 Episode

例如：

```text
08:00 breakfast
09:00 breakfast
10:00 breakfast
```

不能推断：

```text
08:00~10:30 breakfast
```

除非历史数据明确支持。

---

# v1.0 §139 一百三十九、测试必须重点覆盖目前发现的问题

## Test A：早餐

期望：

```text
08:00 breakfast
08:30~08:45 end

绝不允许：
09:00 breakfast
10:00 breakfast
11:00 breakfast
```

---

## Test B：连续活动

期望：

```text
14:00 Minecraft
至少持续合理时间
```

不能：

```text
14:01 TV
14:03 Music
14:05 Gaming
```

---

## Test C：Activity Extension

Minecraft：

达到 planned_end：

如果 Focus 高、Energy 合适：

允许：

```text
extend
```

---

## Test D：Activity Transition

达到 max duration：

必须：

```text
transition
```

---

## Test E：Routine Anchor

```text
12:00 lunch
```

不能：

```text
当前 gaming
一直持续到 14:00
```

应在允许窗口内完成 transition。

---

# v1.0 §140 一百四十、Test F：User Interruption

```text
当前：
watching_show

用户开始聊天
```

应该：

```text
watching_show
+
interaction_overlay
```

而不是：

```text
watching_show → chatting → watching_show
```

每条用户消息都生成一次 Activity Transition。

---

# v1.0 §141 一百四十一、Test G：Ambient

Breakfast：

```text
1 episode
```

最多：

```text
1 ambient
```

不会：

```text
每小时一条 breakfast ambient
```

---

# v1.0 §142 一百四十二、Test H：Episode Merge

```text
gaming
```

如果中间没有实际 Activity 切换：

应保持：

```text
1 Episode
```

---

# v1.0 §143 一百四十三、Test I：Recovery

系统：

```text
14:00 gaming
```

快进：

```text
+4h
```

恢复：

不能：

```text
制造 4 小时历史活动
```

应该：

```text
recovery evaluation
→ 当前合理 Episode
```

---

# v1.0 §144 一百四十四、Test J：World Simulator

快进：

```text
1 day
```

检查：

```text
Activity Episodes
Transitions
Ambient Events
Goals
Snapshots
```

确保没有：

```text
Event Explosion
Activity Explosion
```

---

# v1.0 §145 一百四十五、Test K：Social Cognition Regression

测试：

```text
Bot：
在看剧。

User：
看的什么？

```

v0.9 必须继续：

```text
reply
```

而且使用：

```text
ActivityEpisode.detail
```

---

# v1.0 §146 一百四十六、Test L：Group Social Relevance

角色：

```text
activity = minecraft_building
```

群：

```text
最近有人玩 Minecraft？
```

Social Cognition：

应该提高：

```text
activity_relevance
```

而不是因为 Activity 改造导致 v0.9 判断失效。

---

# v1.0 §147 一百四十七、Test M：Activity Stale Context

Activity 已经结束：

```text
watching_show
```

Social Cognition 不允许继续认为：

```text
watching_show
```

必须读取新 Episode。

---

# v1.0 §148 一百四十八、Test N：Multiple Groups

不同群：

```text
Group A
Group B
```

共享：

```text
Character World State
```

但：

Social Conversation Context：

必须仍然隔离。

---

# v1.0 §149 一百四十九、性能测试

World Tick：

必须低成本。

Activity Decision Model：

只在必要时调用。

目标：

```text
普通 1 分钟 Tick：
不调用 LLM

Activity Transition：
最多一次 Decision

5-message Social Observer：
独立运行
```

---

# v1.0 §150 一百五十、日志格式升级

建议：

```text
[World.Activity]
started
completed
extended
interrupted

[World.ActivityPlanner]
planned
replanned

[World.ActivityDecision]
continue
extend
transition

[World.Ambient]
created
suppressed

[World.Recovery]
restored
reconciled
```

---

# v1.0 §151 一百五十一、日志中必须显示 Episode ID

例如：

```text
[World.Activity]
episode=ACT-20260930-140000
started=Minecraft
planned_end=15:30
```

这样所有相关：

```text
Ambient
Timeline
Social Context
```

都可以追踪。

---

# v1.0 §152 一百五十二、WebUI Activity Inspector

页面：

```text
/world/activity/{episode_id}
```

显示：

```text
Episode
Start
Planned End
Actual End
Duration
Status
Activity Profile
Location
Energy Delta
Momentum
Transition Reason
Ambient Events
Related Goal
Related Topic
Related Social Context
```

---

# v1.0 §153 一百五十三、World Activity Simulator

WebUI 支持：

```text
Start Episode
Advance 10min
Advance 30min
Advance 1h
Force Transition
Simulate Recovery
```

但：

默认：

```text
dry_run
```

不影响真实世界。

---

# v1.0 §154 一百五十四、Activity Planner Debug

管理员可以看到：

```text
Current Activity:
Minecraft

Candidates:
watching_show
music
rest
continue_minecraft

Constraints:
energy=0.72
focus=0.83
anchor=lunch 120min later
```

最终：

```text
Decision:
continue_minecraft
planned_end=15:20
```

不显示隐藏思维链。

---

# v1.0 §155 一百五十五、最终世界模型

v1.0 最终应该是：

```text
                        WORLD
                          │
                       Clock
                          │
                          ▼
                  Activity Planner
                          │
                    ┌─────┴─────┐
                    ▼           ▼
               Schedule      State
                    │           │
                    └─────┬─────┘
                          ▼
                  Activity Episode
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
 Primary Activity   Secondary Context   Interaction Overlay
        │
        ▼
   Duration Model
        │
        ▼
 Continue / Extend / Transition
        │
        ▼
 Next Activity Episode
        │
        ├──────────────┐
        ▼              ▼
   Ambient Event   World Timeline
        │
        ▼
 Character World Context
        │
        ├───────────────┐
        ▼               ▼
 Behavior         Social Cognition
```

---

# v1.0 §156 一百五十六、v1.0 与 v0.9 的关系

v1.0 的重点是 World Activity。

但允许对 v0.9 做必要适配：

```text
v0.9 Social Cognition
        ↓
读取新的 ActivityEpisode
        ↓
得到更丰富的 Character World Context
```

不要重新设计：

```text
Social Cognition
```

只修改接口和上下文。

---

# v1.0 §157 一百五十七、不要做的事情

本版本明确不要：

- 重写 Social Cognition
- 重写 Memory
- 重写 Agent
- 重写 Tool Runtime
- 增加大量新的活动类型
- 为每分钟生成 Activity Event
- 为每条 Activity 生成 Ambient
- 使用随机数作为 Activity Transition 的主要机制
- 伪造现实世界行为
- 建立无限复杂的活动层级
- 每日提前生成完整 24 小时硬日程
- 为了拟人化制造大量日志

---

# v1.0 §158 一百五十八、开发顺序

严格按照：

```text
1. 阅读 v0.9 完整源码
2. 分析当前 Activity 写入路径
3. 分析 Routine
4. 分析 CharacterState
5. 分析 WorldStateService
6. 找出 Activity 强制切换问题
7. ActivityEpisode Model
8. Activity Profile
9. Activity Duration
10. Current Activity State Migration
11. ActivityPlanner
12. Planned End
13. Transition Window
14. Continue / Extend / Transition
15. Activity Momentum
16. Activity Completion
17. Activity Transition Reason
18. Primary Activity
19. Secondary Context
20. Interaction Overlay
21. Ambient Episode Binding
22. Ambient Opportunity
23. Activity Timeline Upgrade
24. Relationship Event Noise Reduction
25. Timeline Visibility
26. Recovery
27. Consistency Checker
28. Activity Bounce Guard
29. Activity Merge
30. Energy / Mood / Focus integration
31. Social Cognition Context Adapter
32. WebUI Activity Inspector
33. WebUI Timeline
34. WebUI Activity Simulator
35. WebUI Planner Debug
36. Database Migration
37. Unit Tests
38. Long-term Simulation Tests
39. Social Cognition Regression Tests
40. QQ Integration Tests
41. Regression
```

---

# v1.0 §159 一百五十九、最终验收目标

完成 v1.0 后，下面这种日志必须彻底消失：

```text
08:00 breakfast
09:00 breakfast
10:00 breakfast
11:00 breakfast
```

应该变成类似：

```text
08:00 ActivityStarted
      breakfast
      planned_end=08:35

08:17 Ambient
      顺手刷了一会手机

08:35 ActivityCompleted
      breakfast

08:35 ActivityStarted
      free_time
      planned_end=09:00

09:00 ActivityStarted
      Minecraft
      planned_end=10:25

10:25 ActivityExtended
      +35min

11:00 ActivityCompleted
      Minecraft
```

同时：

```text
14:00 Minecraft
14:01 不切
14:02 不切
14:03 不切
14:04 不切
14:05 不切
...
```

直到活动真正需要结束。

---

# v1.0 §160 一百六十、最终拟人化目标

v1.0 不应该让用户感觉：

> “这个 AI 每隔一分钟重新决定自己现在干什么。”

而应该让用户感觉：

> **“它做一件事情，会真的做一段时间；中途可能顺手干点别的，但不会莫名其妙一分钟换三件事。”**

例如：

```text
用户：
你刚刚在干嘛？

角色：
刚刚还在 Minecraft 里盖房子。
```

过了一小时：

```text
用户：
现在呢？

角色：
刚从 Minecraft 里出来，刚才又看了一会儿剧。
```

而不是：

```text
14:00 Minecraft
14:01 看剧
14:03 听歌
14:05 游戏
```

---

# v1.0 §161 一百六十一、开发硬性要求

1. 首先完整阅读现有 v0.9。
2. 必须找出为什么现有 `transition_minutes` 没有阻止 14:00~14:05 的快速 Activity 切换。
3. 必须修复所有绕过 Activity Transition Guard 的入口。
4. Activity Episode 必须成为当前活动生命周期的主要事实来源。
5. `CharacterState.activity` 必须与当前 Episode 同步。
6. Routine 不得在普通 Tick 中直接重新选择 Activity。
7. World Tick 不得每分钟进行 Activity Roulette。
8. Activity 必须拥有 `planned_end_at`。
9. Activity 是否继续必须独立判断。
10. Activity 是否延长必须独立判断。
11. Activity 是否切换必须独立判断。
12. Activity Decision 不得以随机概率作为核心逻辑。
13. 不同 Activity 必须拥有不同 Duration Profile。
14. Primary Activity 只能有一个。
15. Secondary Context 不能冒充 Primary Activity。
16. Interaction Overlay 不应频繁创建 Activity Transition。
17. Ambient 必须绑定 Activity Episode。
18. Ambient 不得延长 Activity 生命周期。
19. Relationship Event 必须降低无意义噪声。
20. World Timeline 应以 Activity Lifecycle 为主要记录方式。
21. Timeline 必须支持 major / normal / trace 分层。
22. Activity History 不应被每分钟 Tick 刷满。
23. Activity Planner 必须使用 Rolling Horizon。
24. Routine 是偏好/意图，不是硬编码时间表。
25. Fixed Anchors 必须优先于普通自由活动。
26. Persistent Goal 不能无限覆盖睡眠、吃饭和休息。
27. Activity 必须支持 Extend。
28. Activity 必须支持 Interrupt。
29. Activity 必须支持 Recovery。
30. Recovery 不得伪造过去的活动历史。
31. 必须存在 Activity Consistency Checker。
32. 必须存在 Activity Bounce Guard。
33. 相邻相同 Activity 应避免无意义拆分。
34. World Runtime 崩溃不能拖垮 QQ。
35. Social Cognition 不得直接修改 World State。
36. Social Cognition 必须读取新的 ActivityEpisode Context。
37. Active Conversation 仍然必须优先于普通 Group Observer。
38. v0.9 的 5-message Observer 逻辑保持不变，除非接口适配需要。
39. 必须进行 Social Cognition Regression。
40. 所有新增管理和 Debug 能力只能通过 WebUI。
41. 所有 Simulator 默认 Dry Run。
42. 不得因为“拟人化”增加大量随机事件。
43. 优先让活动“稳定地持续”，而不是让世界“频繁地变化”。
44. 所有 Activity Decision 都应可在 WebUI 中解释。
45. 不允许保存或展示隐藏 Chain of Thought。
46. 不允许虚构现实世界中真实发生的行为。
47. 必须保留现有 v0.9 代码能力，采用增量迁移。
48. 数据库必须安全 Migration，不允许删库重建。
49. 历史旧 Activity 数据不能为了迁移而伪造精确 Episode。
50. v1.0 的核心成功标准不是“活动更多”，而是“活动更连续、更合理、更可解释”。

---

# v1.0 §162 一百六十二、最终启动指令

现在开始：

**先完整阅读 CatooBot v0.9 当前源码和数据库结构，不要直接修改。**

首先定位并回答：

```text
1. 当前 Activity 是在哪里被创建的？
2. 每分钟 Tick 是否会重新调用 Activity Picker？
3. `transition_minutes` 为什么没有阻止 14:01 / 14:03 / 14:05 的 Activity 切换？
4. 哪些路径使用了 `force=True`？
5. Ambient 为什么会在 Activity 长时间不结束的情况下持续产生“当前活动相关”内容？
6. Social Cognition 当前读取哪些 Activity 字段？
```

完成分析后：

按照本 Prompt 增量实现：

```text
Activity Episode
+
Activity Duration Model
+
Activity Planner
+
Continue / Extend / Transition
+
Primary Activity
+
Secondary Context
+
Interaction Overlay
+
Episode-bound Ambient
+
World Timeline Upgrade
+
Consistency Checker
+
Recovery
+
Social Cognition Adapter
```

**不要先大量改代码。先定位真实问题，再在现有架构上做最小必要重构。**

最终目标：

> **让 CatooBot 的世界从“每分钟更新一次状态的模拟器”，升级成“由持续活动片段驱动、拥有自然时间连续性的角色世界 Runtime”。**