# CatooBot v0.4 开发任务
# Character Behavior Engine
# 角色行为引擎 + 自然聊天 + 主动行为 + 时间感 + 回复节奏

---

# 一、前置条件

CatooBot v0.3 已经完成。

目前已经具备：

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
- Long-term Memory
- Memory Retrieval
- Memory Extraction
- Session
- Context Builder
- WebUI
- Persona 管理
- Memory 管理
- Model 管理
- Prompt 管理
- Runtime 管理
- Logs
- SQLite Migration

并且已经完成 CatooBot 的角色设定。

现在进入：

# CatooBot v0.4

本版本不再重点解决“角色设定”。

重点解决：

> **角色行为。**

即：

```text
它什么时候说话
它怎么说话
它一次发几条消息
它会不会主动找人
它会不会延续话题
它会不会因为当前状态改变语气
它在群聊什么时候插话
它在不同时间是否有不同状态
它是否表现出“正在做某件事”的连续性
```

---

# 二、核心产品目标

最终希望 QQ 用户感受到的不是：

```text
发送消息
↓
AI API
↓
生成回答
```

而是：

```text
一个有自己的时间线、
当前状态、
聊天习惯、
社交关系、
活动状态、
话题偏好、
回复节奏的虚拟角色
```

---

# 三、最重要的架构原则

不要继续把这些行为写进 Chat Plugin。

新增：

```text
CharacterBehaviorEngine
```

整体：

```text
QQ Message
     ↓
CatooBot Core
     ↓
Character Runtime
     ↓
Behavior Engine
     ↓
Context Builder
     ↓
AI Engine
     ↓
Response Planner
     ↓
Message Delivery
```

同时存在一个后台：

```text
Behavior Scheduler
```

负责：

```text
主动聊天
状态变化
周期行为
时间感
空闲行为
```

---

# 四、v0.4 的核心模块

建议增加：

```text
app/
├─ behavior/
│  ├─ __init__.py
│  ├─ engine.py
│  ├─ scheduler.py
│  ├─ planner.py
│  ├─ triggers.py
│  ├─ timing.py
│  ├─ activity.py
│  ├─ initiative.py
│  ├─ presence.py
│  └─ models.py
│
├─ response/
│  ├─ __init__.py
│  ├─ planner.py
│  ├─ splitter.py
│  ├─ timing.py
│  └─ delivery.py
```

如果现有目录已有相似模块，应复用，不要机械创建重复结构。

---

# 五、不要破坏 v0.3

必须：

1. 先读取现有源码
2. 理解 v0.3 的真实结构
3. 增量开发
4. 保留现有 Persona / Memory / Relationship
5. 不重写 AI Engine
6. 不重写 Model Router
7. 不重写 OneBot Adapter
8. 不引入新的 Bot Framework

---

# 六、回复不应该永远“秒回”

这是 v0.4 最重要的行为之一。

当前如果：

```text
用户
↓
AI
↓
立即发送
```

会非常像 API。

新增：

```text
Response Timing Engine
```

用于决定：

```text
什么时候开始发送
```

---

# 七、回复延迟模型

设计：

```text
reply_delay
```

由以下因素共同决定：

```text
message_length
conversation_intensity
character_state
time_of_day
user_relationship
response_complexity
```

例如：

```text
简单寒暄
→ 快速回复

复杂问题
→ 稍长等待

连续快速聊天
→ 更快

角色状态为 busy
→ 更慢
```

不要固定：

```text
每次 2 秒
```

而应该是一个概率/区间模型。

---

# 八、不要机械模拟“打字”

不要每次：

```text
先发送 typing
等待 3 秒
发送文字
```

这样也会很假。

应该：

```text
Response Timing
+
Message Chunking
+
State
```

共同决定表现。

---

# 九、消息分段

AI 生成：

```text
你好呀！今天怎么样？

我刚刚还在想你前面说的那个问题。

感觉其实挺有意思的。
```

不要永远一次性发送成：

```text
[一整大段]
```

允许自然拆成：

```text
你好呀！今天怎么样？

我刚刚还在想你前面说的那个问题。

感觉其实挺有意思的。
```

但：

**不要每次都拆成 3 条。**

需要由 Response Planner 决定。

---

# 十、消息拆分规则

考虑：

```text
语义完整性
标点
段落
情绪
上下文
```

优先：

```text
自然段
↓
句子
```

避免：

```text
字级切割
```

---

# 十一、Response Planner

新增：

```text
CharacterResponsePlanner
```

输入：

```text
AIResponse
CharacterState
ConversationContext
Relationship
UserContext
```

输出：

```text
ResponsePlan
```

例如：

```python id="0lh4xw"
ResponsePlan(
    delay=2.4,
    messages=[
        "...",
        "..."
    ]
)
```

可以包含：

```text
delay
message_chunks
typing_simulation
delivery_strategy
```

---

# 十二、自然消息长度

角色不应该总是：

```text
一条消息 = 一大段
```

允许：

```text
短
短
中
长
```

并根据上下文动态变化。

例如：

用户：

```text
吃饭了吗
```

更适合短回复。

而：

```text
我最近在思考一个很复杂的问题……
```

可以自然长一些。

---

# 十三、情绪不是标签，而是状态

现在开始使用 v0.3 的 Character State。

状态至少：

```text
mood
energy
focus
activity
```

例如：

```text
mood = relaxed
energy = 0.7
focus = chat
activity = idle
```

这些状态会影响：

```text
语气
回复长度
回复速度
主动性
```

---

# 十四、状态变化

不要每条消息都随机：

```text
happy
sad
angry
happy
sad
```

这样会非常不自然。

应该具有：

```text
惯性
```

例如：

```text
happy
↓
经过普通聊天
↓
仍然大致 happy
```

发生明显事件后：

```text
happy
↓
negative event
↓
slightly down
```

再逐渐恢复。

---

# 十五、情绪变化应该有来源

至少支持：

```text
conversation
time
activity
user interaction
random ambient change
```

但：

**random 只能作为轻微扰动。**

不能：

```text
用户说你好
↓
随机变成悲伤
```

---

# 十六、Character Activity

角色应该拥有一个“正在做什么”的状态。

例如：

```text
idle
chatting
studying
gaming
reading
resting
working
eating
sleeping
```

这些不是现实监控。

只是：

> 角色世界中的虚构状态。

---

# 十七、Activity Scheduler

增加：

```text
ActivityManager
```

根据：

```text
time_of_day
day_of_week
character_preferences
current_activity
```

选择当前活动。

例如：

```text
凌晨
→ resting

白天
→ working / studying

晚上
→ relaxing / gaming
```

具体活动由 Persona / WebUI 配置。

不要写死。

---

# 十八、时间感

CatooBot 需要知道：

```text
当前日期
当前时间
星期
```

并根据这些信息调整聊天。

例如：

```text
早晨
晚上
深夜
工作日
周末
```

语气可以不同。

---

# 十九、时区

当前系统使用：

```text
Asia/Singapore
```

但：

**不要直接把时区写死在业务代码。**

放入配置：

```yaml
character:
  timezone: "Asia/Singapore"
```

未来可以修改。

---

# 二十、休息/睡眠状态

支持：

```text
sleep_schedule
```

例如：

```yaml
behavior:
  schedule:
    sleep:
      enabled: true
      start: "00:30"
      end: "08:00"
```

但要注意：

**“角色睡觉”不等于 Bot 真正下线。**

默认：

```text
QQ仍然可以收到消息
```

Behavior Engine 根据：

```text
sleeping
```

决定：

```text
不主动聊天
回复延迟增加
回复风格变化
```

如果 Persona 设定允许，也可以：

```text
稍后回复
```

---

# 二十一、主动聊天

这是 v0.4 的第二个核心功能。

角色可以：

> 在没有用户新消息时主动找用户聊天。

但必须严格控制。

新增：

```text
InitiativeEngine
```

---

# 二十二、主动聊天不是“随机发消息”

不能：

```text
每 10 分钟随机发一句话
```

这种行为很像程序。

必须考虑：

```text
time
user activity
last interaction
relationship
current activity
recent topics
memory
mood
cooldown
```

---

# 二十三、主动聊天 Trigger

支持：

```text
time_based
topic_based
state_based
event_based
relationship_based
idle_based
```

例如：

```text
很久没聊天
+
关系较熟
+
角色当前状态 relaxed
```

才有可能主动发起对话。

---

# 二十四、主动聊天冷却

必须有：

```text
initiative_cooldown
```

例如：

```yaml
behavior:
  initiative:
    enabled: true
    min_interval_minutes: 120
```

不能频繁轰炸。

---

# 二十五、每日主动消息预算

设计：

```text
daily_initiative_limit
```

例如：

```yaml
behavior:
  initiative:
    daily_limit: 3
```

这是：

**全局或用户级配额。**

不要无限主动聊天。

---

# 二十六、用户活跃度

根据最近互动：

```text
last_seen
interaction_count
recent_message_count
```

判断：

```text
active
inactive
long_absence
```

不要使用敏感数据推断。

只使用 Bot 自己观察到的聊天活动。

---

# 二十七、主动聊天候选话题

不能每次：

```text
“你在干嘛？”
```

应该从：

```text
recent conversation
memory
character activity
character interests
unfinished topics
```

生成候选话题。

例如：

用户之前：

```text
我最近在做 Minecraft 房间
```

几天后角色可能自然提起：

```text
“对了，你前面那个 Minecraft 房间后来弄得怎么样了？”
```

这就是：

```text
Memory
+
Initiative
```

真正结合起来。

---

# 二十八、未完成话题 Topic Threads

新增：

```text
TopicThread
```

记录：

```text
topic
last_discussed_at
status
importance
participants
```

状态：

```text
active
waiting
resolved
forgotten
```

例如：

```text
Minecraft 房间
status = waiting
```

用户后来告诉角色完成了：

```text
status = resolved
```

---

# 二十九、Topic Manager

增加：

```text
TopicManager
```

功能：

```text
create()
update()
resolve()
forget()
get_active_topics()
get_related_topics()
```

这样角色不会每次只依赖最近 20 条聊天。

---

# 三十、主动聊天与 Topic Thread

例如：

```text
用户：
我最近在做一个网站。

Bot：
哦？做到哪里了？

用户：
首页差不多了，音乐页面还没搞完。

```

建立：

```text
topic:
个人网站
status:
active
```

几天后：

如果满足主动聊天条件：

角色可以自然继续：

```text
“你那个音乐页面后来弄完了吗？”
```

---

# 三十一、事件驱动主动行为

未来可以监听：

```text
new_memory
topic_resolved
long_absence
scheduled_time
character_activity_change
```

例如：

```text
长期没聊
+
当前 activity = idle
```

生成一个主动聊天候选。

但必须经过：

```text
Initiative Gate
```

最终判断是否真正发送。

---

# 三十二、Initiative Gate

不要让任何触发器直接发送消息。

流程必须：

```text
Trigger
 ↓
Candidate
 ↓
Initiative Gate
 ↓
Probability / Rule Evaluation
 ↓
Response Planner
 ↓
Send
```

Gate 至少考虑：

```text
cooldown
daily limit
user relationship
user recent activity
character state
time
topic relevance
```

---

# 三十三、主动聊天概率

可以采用：

```text
base_probability
+
relationship_modifier
+
topic_modifier
+
activity_modifier
-
cooldown_penalty
```

最后限制：

```text
0.0 ~ 1.0
```

不要让概率完全由 LLM 决定。

业务层必须有硬限制：

```text
daily_limit
cooldown
sleep
disabled user
```

---

# 三十四、群聊行为

这一版本也开始支持：

```text
Group Behavior
```

CatooBot 在群里：

**不能像私聊一样对每条消息都回复。**

---

# 三十五、群聊参与概率

需要判断：

```text
是否有人在说自己
是否有人 @ 自己
话题是否相关
群是否活跃
角色是否正在关注这个话题
角色与群关系
```

---

# 三十六、群聊普通消息

例如：

```text
A：今天吃什么
B：不知道
C：火锅？
```

CatooBot 不应该：

```text
“我也觉得火锅不错”
```

每次都插话。

应该有：

```text
Group Participation Gate
```

---

# 三十七、@CatooBot

@ 仍然是高优先级触发。

流程：

```text
@CatooBot
↓
必须处理
```

但：

不能显示：

```text
Bot Command
```

仍然只是正常角色对话。

---

# 三十八、群聊多用户上下文

Group Session 需要包含：

```text
当前群
当前用户
消息发送者
最近群聊上下文
```

同时：

```text
User Memory
+
Group Memory
```

分开注入。

---

# 三十九、不要把整个群聊历史塞给 LLM

必须继续使用：

```text
recent context
+
relevant topics
+
relevant memories
```

而不是整个数据库。

---

# 四十、角色主动行为应该可关闭

WebUI 增加：

```text
Behavior
├─ Auto Reply
├─ Initiative
├─ Group Participation
├─ Activity
└─ State Simulation
```

每项：

```text
enabled / disabled
```

---

# 四十一、用户级主动聊天控制

WebUI 的用户页面允许：

```text
initiative_enabled
```

例如：

```text
User A
主动聊天：开启

User B
主动聊天：关闭
```

关闭后：

```text
仍然可以正常回复
```

只是不会主动开始对话。

---

# 四十二、群级主动参与控制

同样：

```text
Group A
participation = enabled

Group B
participation = disabled
```

---

# 四十三、Do Not Disturb

增加：

```text
DND
```

例如：

```yaml
behavior:
  dnd:
    enabled: false
    start: "00:00"
    end: "08:00"
```

影响：

```text
主动聊天
群聊参与
```

不一定影响：

```text
被动回复
```

是否影响被动回复由 WebUI 配置。

---

# 四十四、Response Style

增加：

```text
Style Controller
```

影响：

```text
message_length
emoji_frequency
kaomoji_frequency
punctuation
line_breaks
formality
```

但这些是：

**风格参数。**

不是固定模板。

---

# 四十五、禁止模板化

不要：

```text
if mood == happy:
    return "好耶！~"
```

这会导致重复和机械。

状态只能改变：

```text
参数
```

最终自然语言仍由 AI 生成。

---

# 四十六、Character Response Planner

推荐流程：

```text
User Message
 ↓
Analyze Context
 ↓
Character Runtime
 ↓
Generate AI Response
 ↓
Response Planner
 ↓
Delay
 ↓
Split
 ↓
Send
```

Response Planner 负责行为。

LLM 负责语言内容。

两个职责必须分离。

---

# 四十七、不要让 LLM 决定硬规则

例如：

```text
daily_limit
cooldown
DND
disabled user
disabled group
sleep
```

全部由业务逻辑强制执行。

不能：

```text
Prompt：
请判断今天是否已经发过三条消息
```

然后让 LLM 自己判断。

---

# 四十八、后台 Scheduler

实现一个稳定的：

```text
BehaviorScheduler
```

长期运行。

可以：

```text
每 30 秒 tick
```

然后检查：

```text
Initiative
Activity
State
Topic
Scheduled Event
```

不要为每个用户创建无限 asyncio task。

---

# 四十九、Scheduler 必须可恢复

CatooBot 重启后：

```text
daily initiative count
cooldown
last initiative
scheduled behavior
```

应该尽量从数据库恢复。

---

# 五十、行为记录

建立：

```text
behavior_events
```

记录：

```text
event_id
type
user_id
group_id
reason
created_at
executed_at
status
```

例如：

```text
initiative_candidate
initiative_sent
initiative_skipped
state_change
activity_change
```

---

# 五十一、日志

增加：

```text
[Behavior]
[Initiative]
[Response]
[Activity]
[Relationship]
[Topic]
```

例如：

```text
[Initiative] Candidate generated
[Initiative] Gate rejected: cooldown
[Response] Planned 2 message chunks
[Response] Delay=2.8s
[Activity] character activity changed idle -> gaming
```

但：

不要在普通日志中记录完整聊天隐私。

---

# 五十二、WebUI — Behavior Dashboard

增加页面：

```text
/behavior
```

显示：

```text
Current Activity
Current Mood
Current Energy
Last Initiative
Today's Initiative Count
Next Eligible Initiative
Active Topics
Group Participation
```

---

# 五十三、WebUI — Behavior Settings

允许配置：

```text
Reply Timing
Initiative
Activity
Sleep
DND
Group Participation
Message Splitting
Style
```

---

# 五十四、WebUI — Manual Trigger

后台管理员可以：

```text
Test Response
Test Initiative
Test State Transition
```

但是：

这些只能出现在 WebUI。

不要把测试接口暴露给 QQ。

---

# 五十五、WebUI — Preview

建议增加：

```text
Behavior Simulator
```

允许管理员输入：

```text
当前时间
当前状态
用户关系
最近聊天
Memory
```

然后：

```text
Preview
```

查看：

```text
角色可能如何回复
```

但：

**Preview 不能直接向 QQ 发消息。**

---

# 五十六、自动聊天安全阀

任何主动行为系统都必须有：

```text
global cooldown
user cooldown
group cooldown
daily limit
hourly limit
DND
disabled state
```

并且：

```text
任何一个硬限制触发
→ 立即禁止发送
```

---

# 五十七、重复主动话题检测

如果角色刚问：

```text
“最近忙不忙？”
```

短时间内禁止再次生成：

```text
“最近忙吗？”
“最近是不是很忙？”
```

需要 Topic / semantic duplicate check。

第一版可以使用：

```text
normalized text
keyword overlap
recent initiative history
```

不必马上使用 Embedding。

---

# 五十八、连续主动消息禁止

默认：

```text
角色主动发送一次消息
↓
如果用户没有回复
↓
不要立即继续发送
```

除非 WebUI 明确配置允许。

---

# 五十九、用户不回复

例如：

```text
Bot：
“你之前那个项目后来做完了吗？”
```

用户没回复。

角色不能：

```text
10分钟后
“在吗？”

30分钟后
“怎么不理我？”

1小时后
“你忙吗？”
```

这种行为禁止。

默认：

> 一次主动邀请 → 等待用户。

---

# 六十、主动聊天必须有“理由”

每次主动消息在内部必须生成：

```text
initiative_reason
```

例如：

```text
unfinished_topic
long_absence
character_activity
scheduled_topic
relationship
```

如果找不到合理理由：

```text
不要发送
```

---

# 六十一、角色生活状态不是用户可控的

QQ 用户不能通过：

```text
“你现在必须去睡觉”
```

修改内部角色状态。

除非角色本身在自然聊天中接受这个话题。

真正的系统状态修改：

```text
WebUI
```

---

# 六十二、时间意识

Context 中可以提供：

```text
current_datetime
weekday
time_period
character_activity
```

但不要直接塞：

```text
2026-09-29T20:07:31+08:00
```

这种机器字符串。

应该转换成更自然的 Context：

```text
现在是晚上八点左右，今天是星期二。
角色当前处于休闲状态。
```

---

# 六十三、时间 Context 不应伪造现实行为

不要让角色自动声称：

```text
“我刚刚真的去了楼下买奶茶”
```

除非这是明确的虚拟角色设定。

可以有：

```text
角色世界中的活动
```

但不要伪造真实世界可验证的行为。

---

# 六十四、状态与 Memory 分离

例如：

```text
mood = happy
```

这是：

```text
当前状态
```

不是：

```text
Memory
```

不要写进长期记忆。

---

# 六十五、Activity 与 Memory 分离

当前：

```text
activity = gaming
```

不应该自动生成：

```text
Memory：
CatooBot 喜欢在 20:00 玩游戏
```

除非该行为长期稳定并且确实值得保存。

---

# 六十六、Relationship 与 Initiative 联动

关系越熟：

并不是：

```text
越熟
→ 越频繁骚扰
```

而应该：

```text
越熟
→ 更可能自然主动
```

但仍受到：

```text
cooldown
daily_limit
user_activity
```

限制。

---

# 六十七、群聊与私聊行为差异

### 私聊

可以：

```text
主动聊天
话题延续
长期记忆
关系状态
```

### 群聊

重点：

```text
@响应
相关话题参与
轻量插话
群体 Context
```

默认：

```text
群聊主动消息频率 < 私聊
```

---

# 六十八、不要做“全自动社交机器人”

CatooBot 的目标是：

```text
自然
克制
有连续性
```

而不是：

```text
消息越多越好
```

宁可：

```text
少说一次
```

也不要：

```text
强行插话
```

---

# 六十九、数据库新增

在已有数据库基础上增加：

```text
character_state
activities
topics
behavior_events
initiative_state
```

如果已有相似表：

优先复用。

必须通过 migration。

---

# 七十、测试

至少新增：

```text
tests/
├─ test_behavior_engine.py
├─ test_response_planner.py
├─ test_timing.py
├─ test_initiative.py
├─ test_activity.py
├─ test_topic_manager.py
├─ test_group_behavior.py
└─ test_scheduler.py
```

---

# 七十一、Response Timing 测试

测试：

```text
短消息
长消息
busy
sleeping
night
day
fast conversation
```

确保：

```text
delay
```

始终落在合理范围。

---

# 七十二、Initiative 测试

测试：

```text
cooldown
daily_limit
DND
disabled_user
disabled_group
sleep
long_absence
active_topic
```

至少验证：

```text
硬规则永远优先
```

---

# 七十三、主动消息重复测试

给：

```text
最近 10 次主动消息
```

不能产生近似重复内容。

---

# 七十四、Scheduler 测试

测试：

```text
启动
停止
tick
异常恢复
数据库失败
事件执行失败
```

单个行为失败不能导致 Scheduler 停止。

---

# 七十五、完整集成测试

最终测试：

```text
QQ Message
 ↓
OneBot
 ↓
Character Runtime
 ↓
Behavior Engine
 ↓
Memory Retrieval
 ↓
Context Builder
 ↓
AI Engine
 ↓
Response Planner
 ↓
Delay
 ↓
Message Split
 ↓
QQ
```

同时测试：

```text
无用户消息
 ↓
Scheduler
 ↓
Initiative
 ↓
Gate
 ↓
Topic / Memory
 ↓
AI
 ↓
Response Planner
 ↓
QQ
```

---

# 七十六、验收场景 1：自然回复

用户：

```text
今天好累
```

CatooBot：

应该像正常角色聊天。

不应该：

```text
理解您的需求
作为AI助手
我可以帮助您
```

---

# 七十七、验收场景 2：消息分段

复杂回答：

允许：

```text
第一段

第二段
```

但不应该每次强制拆分。

---

# 七十八、验收场景 3：回复速度

连续短消息：

```text
用户 A
用户 B
用户 C
```

不得全部严格：

```text
固定 2 秒
```

应该存在自然变化。

---

# 七十九、验收场景 4：未完成话题

用户：

```text
我最近在做个网站
```

几天后用户重新出现。

CatooBot 能够自然延续：

```text
“你之前那个网站后来怎么样了？”
```

前提是：

```text
Topic
+
Memory
+
Relationship
```

满足条件。

---

# 八十、验收场景 5：主动聊天

长时间无聊天。

如果：

```text
关系允许
Topic 存在
Cooldown 已结束
每日预算未超限
当前不在 DND
```

可以主动发起一次。

否则：

```text
什么都不发送
```

---

# 八十一、验收场景 6：主动消息无人回复

Bot 主动：

```text
“你之前做的那个项目后来完成了吗？”
```

用户没有回复。

之后：

**不应该连续追问。**

---

# 八十二、验收场景 7：群聊

群里 20 条消息：

只有 1 条与 CatooBot 当前兴趣/状态相关。

不要全部回复。

---

# 八十三、验收场景 8：@CatooBot

用户：

```text
@CatooBot 你还记得那个项目吗？
```

必须优先处理。

自然利用：

```text
Memory
Topic
Relationship
```

---

# 八十四、验收场景 9：睡眠

角色当前：

```text
sleeping
```

用户私聊：

仍可以回复，但：

```text
timing
style
initiative
```

应该受到 sleeping 状态影响。

---

# 八十五、验收场景 10：WebUI 修改

WebUI：

```text
关闭 Initiative
```

立刻生效。

之后：

```text
Scheduler
```

不再主动发消息。

但：

```text
QQ 用户主动聊天
```

仍能正常回复。

---

# 八十六、验收场景 11：WebUI 模拟

管理员进入：

```text
/behavior
```

模拟：

```text
23:50
mood=relaxed
activity=reading
relationship=close
topic=unfinished
```

点击：

```text
Preview
```

得到：

```text
预期行为
回复延迟
是否主动
预计消息分段
```

但不会真的发消息。

---

# 八十七、不要实现的功能

本版本不要加入：

- Embedding
- Vector Database
- RAG
- Agent
- Tool Calling
- Complex Planning
- 复杂情绪心理模型
- 用户敏感画像
- 用户评分
- 自动操控用户情绪
- 强制维持关系
- 连续主动骚扰
- 伪造现实世界活动

---

# 八十八、未来版本方向

v0.4 完成后：

```text
CatooBot
│
├─ Identity
├─ Persona
├─ Memory
├─ Relationship
├─ Behavior
├─ Activity
├─ Topic
├─ Initiative
├─ Response Planner
└─ AI Engine
```

下一阶段可以继续发展：

```text
v0.5
Semantic Memory
Embedding
Vector Retrieval
```

再以后：

```text
v0.6
Tool System
```

然后：

```text
v0.7
Agent / Multi-step Reasoning
```

---

# 八十九、开发顺序

严格按照：

```text
1. 阅读 v0.3
2. Character Runtime 接入
3. Character State
4. Activity
5. Time Context
6. Response Timing
7. Response Planner
8. Message Splitter
9. Topic Thread
10. Initiative Engine
11. Initiative Gate
12. Scheduler
13. Group Behavior
14. Behavior WebUI
15. Runtime State
16. Database Migration
17. Tests
18. Integration Test
19. QQ 实际测试
20. Regression
```

每完成一个阶段：

```text
代码检查
↓
pytest
↓
类型检查
↓
Lint
↓
本地运行
↓
QQ 实测
```

---

# 九十、最终目标

CatooBot v0.4 完成后：

它不只是：

```text
一个会聊天的人设
```

而应该开始成为：

```text
一个“有自己的时间、状态、节奏、话题、
活动和社交行为”的连续角色。
```

最终用户感受到的是：

```text
“这个人刚刚在干别的事情，
现在才回复我。”

“她居然还记得我之前提到的那个项目。”

“她今天好像比平时安静一点。”

“我好几天没找她，
她居然自己提起了之前的话题。”

“在群里她也不是每句话都插嘴。”
```

这些感觉应该来自：

```text
Memory
+
Relationship
+
State
+
Activity
+
Topic
+
Initiative
+
Response Timing
+
Context
```

而不是来自一份越来越长的 System Prompt。

---

# 九十一、最终硬性要求

完成 v0.4 后必须保持：

```text
✅ QQ 端无任何 Bot Command
✅ QQ 端无 /help
✅ QQ 端无 /ping
✅ QQ 端无 Memory 管理
✅ QQ 端无模型管理
✅ QQ 端无配置命令
✅ WebUI 是唯一管理入口
✅ Persona 保持稳定
✅ Memory 保持隔离
✅ Relationship 保持连续
✅ Character State
✅ Activity
✅ Topic
✅ Initiative
✅ Response Timing
✅ Response Planner
✅ Group Participation
✅ Scheduler
✅ DND
✅ Cooldown
✅ Daily Limit
✅ WebUI Behavior Dashboard
✅ 行为测试
✅ 完整回归测试
```

现在开始：

**首先完整阅读 CatooBot v0.3 的源码与当前数据库结构。不要假定 v0.3 的目录和类名与本 Prompt 一致。**

先理解现有 `Persona / Memory / Relationship / Context / WebUI` 实现，然后在其基础上增量开发 `Character Behavior Engine`。

不得重写稳定的 OneBot、AI Engine、Model Router、Memory 核心功能。

任何新行为都必须做到：

> **默认克制、可解释、可配置、可关闭，并且不能破坏正常聊天。**