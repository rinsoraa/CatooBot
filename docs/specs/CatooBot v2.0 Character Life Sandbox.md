# CatooBot v2.0
# Character Life Sandbox
# 从“World Activity 系统”升级为“持续运行的角色生活沙盒”

---

# §1 一、版本目标

当前 CatooBot 已经具备：

- NapCatQQ
- OneBot 11
- WebSocket
- Character Runtime
- Persona
- Memory
- Relationship
- Conversation Runtime
- Continuity
- Social Cognition
- Media / Vision / Sticker
- Response Runtime
- Tool Runtime
- Agent Runtime
- World Activity / ActivityEpisode
- WebUI

现在正式进入：

# CatooBot v2.0 — Character Life Sandbox

本版本不是继续扩展当前 World Activity。

本版本需要：

> **废弃当前“World = Activity + Event + Schedule”的设计，将角色生活重新建立为一个真正持续运行的 Life Sandbox。**

最终目标：

> **角色不是“拥有一个世界状态”，而是“生活在一个世界里”。**

---

# §2 二、极其重要：人物档案是唯一 Canonical Source

本次开发时，我会同时提供一份：

# 【人物档案附件】

这份附件定义 v2.0 中角色的一切核心设定。

必须将人物档案视为：

```text
Canonical Character Bible
```

也就是说：

```text
人物档案
>
旧 Persona
>
旧 Memory
>
旧 Relationship
>
旧 World
>
旧 Continuity
>
旧 Activity
```

人物档案具有最高的人物设定权。

---

# §3 三、旧人物数据必须彻底清理

当前 CatooBot 中已经存在一个旧人物。

v2.0 不能继续沿用这个旧人物的任何人格、记忆或关系数据。

必须首先完成：

## Character Data Reset

清除旧角色相关：

```text
旧 Persona
旧 Persona Prompt
旧 Character Identity
旧 Long-term Memory
旧 Episodic Memory
旧 Semantic Memory
旧 Memory Embedding
旧 Relationship
旧 Interaction Profile
旧 Shared Experience
旧 Continuity
旧 Open Loop
旧 World State
旧 ActivityEpisode
旧 Character Goals
旧 Character-specific Preferences
旧 Character-specific Behavioral State
旧 Character-specific Social State
```

不得将旧角色的数据和新人物档案混合。

---

# §4 四、注意：不要删除整个 CatooBot 数据库

禁止：

```text
删除整个 SQLite
删除所有表
重建整个数据库
删除 QQ 配置
删除 OneBot 配置
删除 Model 配置
删除 Tool 配置
删除 WebUI 管理员配置
删除系统日志
```

只清除：

```text
Character-specific data
```

以及与旧角色直接相关的 World 数据。

---

# §5 五、需要建立 Character Reset / Reinitialize 机制

v2.0 不应该把这次清理写成一次性的手工 SQL。

新增：

```text
CharacterLifecycleManager
```

支持：

```text
reset_character()
initialize_character_from_bible()
rebuild_character_runtime()
```

WebUI 未来也可以执行：

```text
Reset Character
Reinitialize Character
Rebuild Character World
```

但默认必须要求确认。

---

# §6 六、重置范围必须明确

建立：

```text
CharacterDataNamespace
```

例如：

```text
character_identity
character_persona
character_memory
character_relationship
character_continuity
character_world
character_open_loops
character_shared_experiences
character_preferences
character_social_world
character_inventory
character_projects
character_pet
```

这些全部属于角色数据域。

---

# §7 七、用户数据不能因为重置人物而删除

例如：

```text
QQ User
QQ Group
User ID
Group ID
Message Logs
```

这些属于：

```text
External User / Platform Data
```

不得删除。

但：

```text
用户与旧人物的关系
旧人物与用户的 Memory
旧人物与用户的 Shared Experience
```

必须清除。

否则新人物会继承旧角色的关系。

---

# §8 八、人物档案导入

V2.0 必须实现：

```text
Character Bible Loader
```

支持从人物档案附件读取设定。

如果附件是 Markdown：

```text
.md
```

必须解析。

如果附件是：

```text
.txt
.docx
.json
.yaml
```

也尽量支持，但优先以当前实际附件格式实现。

---

# §9 九、不能直接把人物档案全部塞进 System Prompt

这是 v2.0 最重要的要求之一。

不能：

```text
人物档案
↓
几万字 System Prompt
↓
每次聊天全部发送
```

必须建立：

# Character Bible Compiler

将人物档案编译成结构化 Character Definition。

---

# §10 十、Character Bible Compiler

流程：

```text
Character File
        ↓
Parser
        ↓
Section Extraction
        ↓
Fact Extraction
        ↓
Rule Extraction
        ↓
Relationship Extraction
        ↓
World Extraction
        ↓
Behavior Extraction
        ↓
Preference Extraction
        ↓
Mode Extraction
        ↓
Character Bible
        ↓
Sandbox Seed
```

---

# §11 十一、人物档案中的信息必须被分类

至少分成：

```text
Identity
Appearance
Personality
Values
Motivations
Preferences
Habits
Behavior Rules
Modes
Speech Style
Social Boundaries
Relationships
Entities
Locations
Objects
Inventory
Projects
World Rules
Routine Hints
Work / Livelihood
Social Spaces
Pet
```

---

# §12 十二、信息分类原则

每一条人物档案都必须回答：

```text
它是什么？
```

分类为：

### Static Fact

例如：

```text
年龄
生日
身高
名字
性别
```

进入：

```text
Character Bible
```

---

### Behavioral Rule

例如：

```text
回家一定先换睡衣
```

进入：

```text
Behavior Rule
```

---

### Mode Rule

例如：

```text
凌晨 2~5 点进入深夜模式
```

进入：

```text
Mode Runtime
```

---

### World Entity

例如：

```text
宠物
房间
冰箱
电脑
```

进入：

```text
Sandbox Entity
```

---

### World State

例如：

```text
冰箱有可乐
```

进入：

```text
Sandbox State
```

---

### Relationship Fact

例如：

```text
空凛是核心朋友
```

进入：

```text
Relationship Seed
```

---

### Long-term Preference

例如：

```text
喜欢 Minecraft
```

进入：

```text
Preference Profile
```

---

# §13 十三、人物档案必须成为可验证的 Canonical Data

编译完成后，WebUI 必须可以查看：

```text
Character Bible
```

显示：

```text
Identity
Personality
Modes
Rules
Relationships
World Entities
Preferences
Projects
Constraints
```

管理员能够知道：

> 当前运行中的 CatooBot，到底加载了哪些人物设定。

---

# §14 十四、如果人物档案中的内容无法结构化

不要静默丢弃。

WebUI / Import Report 必须显示：

```text
Parsed
Partially Parsed
Unresolved
Conflict
```

例如：

```text
原文：
“她有时候会突然整理房间。”

解析：
Behavior Candidate

状态：
Needs Rule Confirmation / AI Interpretation
```

不得默默丢失。

---

# §15 十五、人物档案内部冲突处理

如果人物档案本身出现冲突：

例如：

```text
前面：
不工作

后面：
偶尔接线上零工
```

不要自行覆盖。

应该识别为：

```text
Potential Canonical Conflict
```

如果上下文可以解释：

```text
正式工作 = 无
零工 = 有
```

可以通过语义合并。

不能直接选择最后一句覆盖前一句。

---

# §16 十六、v2.0 核心架构

最终架构：

```text
                       Character Bible
                              │
                              ▼
                   Character Initialization
                              │
                              ▼
                     Character Life Sandbox
                              │
       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
       ▼                      ▼                      ▼
 Character Entity        Other Entities          Spaces
       │                      │                      │
       └──────────────────────┼──────────────────────┘
                              │
                         World Objects
                              │
                              ▼
                       Sandbox Simulation
                              │
       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
       ▼                      ▼                      ▼
     Needs                  Actions                Events
       │                      │                      │
       └──────────────────────┼──────────────────────┘
                              │
                       Decision Engine
                              │
                              ▼
                     Character State Update
                              │
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
           Continuity      Relationship     Social
               │              │              │
               └──────────────┼──────────────┘
                              │
                        Character Context
                              │
                     Conversation Runtime
                              │
                              ▼
                       Response Decision
                              │
                  ┌───────────┼───────────┐
                  ▼           ▼           ▼
               Content    Expression    Timing
                  │           │           │
                  └───────────┼───────────┘
                              ▼
                       Response Sequence
                              │
                              ▼
                             QQ
                              │
                              ▼
                       External Event
                              │
                              └──────────────► Sandbox
```

---

# §17 十七、旧 World 架构处理方式

当前：

```text
World Activity
World Event
Routine
ActivityEpisode
World Timeline
World Planner
```

不允许直接继续作为 v2.0 的核心。

必须进行：

# World → Sandbox Migration

---

# §18 十八、不是简单删除 ActivityEpisode

需要重新定义。

原来的：

```text
ActivityEpisode
```

降级为：

```text
Action Instance
```

即：

> Character Entity 当前正在执行的一个行为。

例如：

```text
Action:
watch_animation
```

不是：

> World 的核心结构。

---

# §19 十九、最终 Sandbox 的核心不是 Activity

核心是：

```text
Entity
State
Space
Object
Need
Action
Rule
Event
Decision
```

Activity 只是 Action 的一种表现形式。

---

# §20 二十、Character Entity

创建：

```text
CharacterEntity
```

至少包含：

```text
id
name
type
identity_ref
location
state
needs
current_action
current_mode
inventory_ref
relationship_ref
knowledge_ref
continuity_ref
```

其中：

```text
type = character
```

---

# §21 二十一、Character Entity 必须与 Character Bible 分开

Character Bible：

```text
“罐头是什么样的人”
```

Character Entity：

```text
“现在沙盒里的罐头是什么状态”
```

例如：

Bible：

```text
爱吃布丁
```

Entity：

```text
当前饥饿度 = 0.61
当前库存：
布丁 × 2
```

---

# §22 二十二、其他实体

创建通用：

```text
Entity
```

支持：

```text
character
pet
npc
social_space
object
vehicle
location
```

v2.0 最少实现：

```text
character
pet
object
location
social_space
```

---

# §23 二十三、小喵必须作为真实实体

如果人物档案中存在宠物：

自动生成：

```text
PetEntity
```

例如：

```text
Pet:
小喵
```

至少有：

```text
name
species
age
location
hunger
energy
mood
sleep
activity
needs
owner_relationship
habits
preferences
```

---

# §24 二十四、宠物不应该只是角色 Prompt 中的一句话

错误：

```text
Character Prompt:
“她有一只叫小喵的猫。”
```

正确：

```text
Sandbox:
PetEntity(id=cat_001)
```

---

# §25 二十五、Pet Behavior

宠物有自己的行为：

例如：

```text
sleep
eat
drink
wander
approach_owner
sit_near_owner
jump_on_keyboard
play
groom
```

行为由：

```text
Pet Need
Pet Preference
Environment
Time
Owner State
```

共同决定。

---

# §26 二十六、宠物不需要使用大模型决定每一步

例如：

```text
猫饿了
→ 找主人

猫困了
→ 找舒服的位置睡

猫在睡觉
→ 不要每 10 分钟随机换行为
```

这些可以由规则系统完成。

只有复杂行为选择才调用 AI。

---

# §27 二十七、Space System

建立：

```text
Space
```

支持树和连接图：

```text
World
 └─ Apartment
    ├─ Bedroom
    ├─ LivingRoom
    ├─ Kitchen
    ├─ Bathroom
    └─ Entrance
```

外部：

```text
Outside
 ├─ Hallway
 ├─ Elevator
 ├─ ConvenienceStore
 ├─ DessertShop
 └─ Neighborhood
```

---

# §28 二十八、Space 必须支持进入 / 离开

例如：

```text
Character:
LivingRoom

↓ walk

Kitchen

↓ exit apartment

Hallway

↓ elevator

Outside
```

每次移动：

```text
Location Transition Event
```

---

# §29 二十九、空间必须影响行为

例如：

```text
Kitchen:
可以做饭
可以吃东西
可以取饮料

Bedroom:
睡觉
换衣服

LivingRoom:
看电视
玩游戏
刷手机

Outside:
进入外出模式
```

不能只是：

```text
location = "home"
```

---

# §30 三十、World Object

创建：

```text
WorldObject
```

例如：

```text
Fridge
Computer
Phone
Bed
Sofa
TV
Wardrobe
TrashBag
CatFoodBowl
WaterBowl
```

对象可以有：

```text
state
inventory
interaction
location
owner
properties
```

---

# §31 三十一、Inventory System

建立：

```text
Inventory
```

支持：

```text
Character Inventory
Object Inventory
Space Inventory
```

例如：

```text
Fridge
├── Cola × 10
├── Pudding × 3
└── Cake × 1
```

---

# §32 三十二、物品消耗

当角色执行：

```text
Drink Cola
```

Sandbox：

```text
Fridge.cola -= 1
```

不能只是生成一句：

> “罐头喝了可乐。”

而数据库仍然：

```text
Cola = 10
```

---

# §33 三十三、缺货驱动行为

例如：

```text
Cola = 0
```

触发：

```text
ReplenishmentNeed
```

然后：

```text
Need:
Buy Cola
```

---

# §34 三十四、Need System

建立：

```text
NeedSystem
```

至少支持：

```text
hunger
thirst
sleepiness
energy
hygiene
social_need
entertainment
pet_care
household_maintenance
work_need
project_progress
```

不是所有角色都必须启用所有 Need。

由 Character Bible 定义。

---

# §35 三十五、Need 不能每分钟随机变化

应该使用：

```text
time
activity
environment
consumption
events
```

计算。

例如：

```text
hunger
```

慢慢增长。

---

# §36 三十六、Need 阈值

例如：

```text
0.0~0.5:
无明显影响

0.5~0.7:
轻微影响

0.7~0.85:
开始产生行为倾向

0.85~1.0:
强制优先处理
```

具体阈值可从 Character Bible 初始化，也可配置。

---

# §37 三十七、Need 不等于强制动作

例如：

```text
hunger = 0.8
```

不一定：

```text
立即吃饭
```

而是：

```text
增加吃东西的行为权重
```

除非：

```text
critical threshold
```

---

# §38 三十八、Action System

建立：

```text
Action
ActionDefinition
ActionInstance
```

例如：

```text
watch_animation
play_minecraft
eat_pudding
drink_cola
feed_cat
take_out_trash
sleep
browse_forum
chat_online
go_outside
buy_food
work_commission
```

---

# §39 三十九、Action Definition

至少：

```text
id
name
duration_profile
requirements
effects
interruptibility
priority
allowed_spaces
required_objects
tags
completion_conditions
```

---

# §40 四十、Action 不要再通过每个 Tick 重新随机选择

当前：

```text
Tick
→ choose_activity
```

全部废止。

应该：

```text
Current Action
↓
Continue
```

直到：

```text
complete
interrupted
cancelled
blocked
expired
```

---

# §41 四十一、Action Duration

每个 Action 支持：

```text
min_duration
typical_duration
max_duration
```

例如：

```text
watch_animation:
30m / 90m / 240m

eat_pudding:
3m / 8m / 15m

play_minecraft:
30m / 120m / 360m
```

具体内容由 Character Bible / Runtime Config 决定。

---

# §42 四十二、Action Continuation

Action 是否继续：

考虑：

```text
goal_progress
focus
energy
need_pressure
completion
interestingness
interruptions
external_events
```

---

# §43 四十三、Decision Engine

建立：

```text
SandboxDecisionEngine
```

输入：

```text
Character State
Current Action
Needs
World State
Available Actions
Character Bible
Relationship Context
Open Loops
Current Interest
External Events
Time
```

输出：

```text
continue
extend
switch
interrupt
sleep
move
interact
```

---

# §44 四十四、Decision Engine 不是随机概率机

禁止：

```python
if random() < 0.5:
```

作为主要决策逻辑。

可以有很小的：

```text
controlled variation
```

但只能在多个都合理的候选中产生差异。

---

# §45 四十五、角色行为应该由“动机 + 约束”产生

例如：

```text
想继续 Minecraft
+
还有精力
+
项目未完成
+
没有强制需求

→ Continue
```

而不是：

```text
random choice
```

---

# §46 四十六、AI Decision

不是所有 Decision 都需要 LLM。

采用：

```text
Rule Engine
↓
Candidate Generation
↓
Deterministic Constraints
↓
AI Decision
↓
Validation
```

AI 只处理：

```text
多个合理行为之间的选择
复杂上下文
角色偏好
非确定性生活细节
```

---

# §47 四十七、AI 不得违反 Canonical Character Rules

例如档案规定：

```text
不上学
```

AI 不得决定：

```text
去上课
```

例如：

```text
现实身份不透露
```

AI 不得决定：

```text
告诉网友住址
```

---

# §48 四十八、World Rule Engine

建立：

```text
WorldRuleEngine
```

负责硬规则：

```text
人物身份
空间规则
物品规则
需要规则
关系规则
世界限制
```

---

# §49 四十九、External Event System

QQ 输入必须转换成：

```text
ExternalWorldEvent
```

而不是直接修改角色 Activity。

---

# §50 五十、External Event 示例

例如：

```text
UserMessage
GroupMention
GroupConversation
FriendInvitation
MediaReceived
Reminder
DeliveryArrived
WeatherChange
TimeEvent
```

---

# §51 五十一、QQ 消息处理

原本：

```text
QQ
↓
Chat Plugin
↓
Character
```

v2.0：

```text
QQ
↓
OneBot
↓
ExternalEvent
↓
Sandbox
↓
Character Decision
↓
Conversation Runtime
```

---

# §52 五十二、外部消息不一定改变沙盒

例如：

用户：

```text
“哈哈哈哈”
```

可能：

```text
Character receives social stimulus
```

但：

```text
current action unchanged
```

只产生：

```text
Interaction Event
```

---

# §53 五十三、外部事件可以中断当前 Action

例如：

```text
罐头：
watch_animation

空凛：
“来打游戏”
```

系统：

```text
ExternalEvent
↓
Relationship Priority
↓
Interrupt Evaluation
↓
Current Action interrupted
↓
New Action = play_minecraft
```

---

# §54 五十四、Interrupt System

建立：

```text
InterruptEvaluator
```

考虑：

```text
event_priority
relationship_priority
current_action_interruptibility
character_energy
social_context
urgency
```

---

# §55 五十五、特殊关系

人物档案中如果存在核心人物：

例如：

```text
空凛
```

必须初始化为：

```text
SpecialRelationshipProfile
```

支持：

```text
priority
trust
familiarity
interrupt_priority
privacy
conversation_style
special_rules
```

---

# §56 五十六、人物档案中的“核心朋友”不能只做普通 Relationship Tier

必须支持：

```text
core_friend
trusted_friend
friend
acquaintance
```

关系类型可以由档案定义。

---

# §57 五十七、Relationship 触发 Sandbox 行为

例如：

```text
Core Friend invites gaming
```

可能：

```text
interrupt current entertainment
```

普通网友：

```text
不一定中断
```

---

# §58 五十八、Social World

如果人物档案描述：

```text
多个群
论坛
游戏服务器
兴趣社区
```

建立：

```text
SocialSpace
```

例如：

```text
Minecraft Group
Dessert Group
Cat Group
Game Forum
Server Chat
```

---

# §59 五十九、SocialSpace 是世界实体

它具有：

```text
id
name
type
topic
activity
participants
character_presence
interest
social_temperature
```

---

# §60 六十、角色可以同时处于多个 SocialSpace

例如：

```text
Minecraft Group:
active

Cat Group:
lurking

Dessert Group:
offline
```

不要求角色只能在一个社交空间。

---

# §61 六十一、QQ 群只是 SocialSpace 的真实入口之一

真实 QQ：

```text
QQ Group 123
```

可以映射：

```text
SocialSpace:
Minecraft Group
```

其他没有真实 QQ 的社交空间：

可以作为：

```text
Simulated Background Social Space
```

存在。

---

# §62 六十二、注意：模拟社交空间不是伪造真实人物

不得凭空生成：

```text
真实存在的真人
真实社交行为
真实身份
```

模拟对象必须是：

```text
角色世界中的虚构 NPC / background activity
```

用于角色生活连续性。

---

# §63 六十三、Livelihood / Online Work

如果人物档案规定：

```text
线上零工
```

建立：

```text
LivelihoodProfile
```

支持：

```text
available
current_commissions
skills
workload
income
deadlines
```

---

# §64 六十四、线上零工不是正式职业

如果人物档案明确：

```text
不用工作
```

但：

```text
偶尔接委托
```

系统必须允许：

```text
formal_employment = false
casual_income = true
```

不能简单把 character occupation 改成：

```text
freelancer
```

---

# §65 六十五、Commission Entity

支持：

```text
Commission
```

例如：

```text
Minecraft Building Request
Game Guide Writing
Illustration Commission
```

包含：

```text
client_type
scope
progress
deadline
reward
status
```

---

# §66 六十六、生活环境模拟

Sandbox 至少要能够模拟：

```text
房间
冰箱
食物
饮料
垃圾
猫
电脑
手机
床
沙发
衣物
快递
外卖
购物
```

但：

不要为了“完整模拟”而实现毫无角色价值的现实细节。

原则：

> **只有能够影响角色生活、行为、聊天、世界连续性的状态，才进入 Sandbox。**

---

# §67 六十七、World Simulation Tick

实现：

```text
SandboxTick
```

默认：

```text
every 10 minutes
```

但可配置。

---

# §68 六十八、10 分钟 Tick 的职责

每次 Tick：

```text
Advance World Time
↓
Update Needs
↓
Update Entity State
↓
Update Object State
↓
Update Action Progress
↓
Check Completion
↓
Check Scheduled Events
↓
Check Critical Needs
↓
Check External Events
↓
Check Decision Required
```

---

# §69 六十九、10 分钟 Tick 不得默认调用 LLM

如果：

```text
没有重要变化
当前 Action 仍然有效
没有外部事件
没有 Critical Need
没有 Decision Point
```

则：

```text
Tick 完成
```

不要调用 AI。

---

# §70 七十、事件驱动唤醒

以下事件必须能够即时唤醒 Sandbox：

```text
QQ Message
Friend Message
Group Mention
Friend Invitation
Pet Need Critical
Object State Change
Action Completed
Commission Deadline
Food Depleted
Package Arrived
Manual Admin Event
```

---

# §71 七十一、Simulation Time

Sandbox 必须有：

```text
WorldClock
```

支持：

```text
real_time
simulation_time
pause
resume
time_skip
```

默认：

```text
real_time
```

---

# §72 七十二、服务器停止后的恢复

不要简单：

```text
每分钟补跑所有历史生活
```

重新启动后：

```text
读取上次 Sandbox Snapshot
↓
计算 elapsed time
↓
根据关键规则做有限状态恢复
```

例如：

```text
角色昨天 03:00 睡觉
现在系统重启后是 13:20
```

应该恢复为：

```text
可能已经醒来
```

而不是：

```text
把 03:00~13:20 的所有十分钟 Tick 全部模拟一遍。
```

---

# §73 七十三、Recovery

需要：

```text
SandboxRecoveryEngine
```

处理：

```text
missed ticks
expired actions
time passage
needs
scheduled events
```

---

# §74 七十四、Sandbox Snapshot

每次重要状态改变：

保存 Snapshot：

```text
Character
Entities
Spaces
Objects
Needs
Actions
Relationships
Projects
Inventory
```

不要每 10 分钟保存整个超大 JSON。

使用：

```text
state delta
+
periodic snapshot
```

---

# §75 七十五、Sandbox State Machine

整个 Sandbox 状态：

```text
initializing
running
paused
recovering
degraded
stopped
```

---

# §76 七十六、角色状态不能直接由 QQ Plugin 修改

禁止：

```python
character.activity = "gaming"
```

这种跨层直接修改。

必须：

```text
External Event
↓
Sandbox
↓
Decision
↓
State Mutation
```

---

# §77 七十七、所有状态写入必须通过 Sandbox

例如：

```text
CharacterState.activity
CharacterState.location
CharacterState.mode
```

必须由：

```text
SandboxStateManager
```

统一写入。

---

# §78 七十八、Character Mode System

人物档案中的模式不能再只是 Prompt。

建立：

```text
CharacterMode
```

支持：

```text
primary_mode
secondary_modes
mode_priority
trigger_conditions
exit_conditions
entered_at
```

---

# §79 七十九、模式可以叠加

例如：

```text
HOME
+
DEEP_NIGHT
+
ONLINE_SOCIAL
```

或者：

```text
HOME
+
GAMING
+
ONLINE_SOCIAL
```

---

# §80 八十、模式由 Sandbox 推导

输入：

```text
location
time
current_action
social_context
game_state
energy
external_presence
```

输出：

```text
CharacterModeSet
```

---

# §81 八十一、模式不要直接修改人格

Mode 只修改：

```text
表达风格
行为偏好
可接受行为
活动偏好
Response Style
```

不能改变：

```text
Core Identity
```

---

# §82 八十二、Speech Policy

Character Bible 中的语言风格必须映射到：

```text
SpeechPolicy
```

例如：

```text
HOME:
lazy
casual
self-talk

OUTDOOR:
polite
brief
formal

GAMING:
short
technical
focused

ONLINE:
chatty
casual
humorous

DEEP_NIGHT:
quiet
slow
minimal
```

---

# §83 八十三、Speech Policy 必须和当前 Context 联动

而不是：

```text
永远使用固定口吻。
```

---

# §84 八十四、Response Runtime

最终聊天仍使用现有：

```text
ConversationTurnRuntime
Character Runtime
ResponsePlanner
Media Runtime
```

不要重新写一套聊天系统。

---

# §85 八十五、Character Context 来源改成 Sandbox

最终 Character Context 必须能够获得：

```text
Current Sandbox Time
Current Space
Current Activity/Action
Current Action Detail
Current Mode
Current Entity Interactions
Current Needs
Current Objects
Current Pet State
Current Open Loops
Current Social Space
Current Relationship
Current Continuity
Relevant Memory
```

---

# §86 八十六、AI 看见的是“当前世界状态”，不是整个沙盒

例如：

```text
当前：
客厅
沙发
小喵在腿旁边睡觉
电脑正在运行 Minecraft
冰箱还有 2 个布丁
当前正在和空凛聊天
```

而不是：

```text
整个 Apartment JSON
所有历史事件
所有库存
所有世界对象
```

---

# §87 八十七、Context Relevance

建立：

```text
SandboxContextSelector
```

根据：

```text
current message
current action
topic
relationship
social event
```

选择相关世界信息。

---

# §88 八十八、World Knowledge 与 Character Knowledge 分离

Sandbox：

```text
房间里有一个快递
```

Character 不一定知道：

```text
不知道箱子里是什么
```

所以支持：

```text
WorldState
CharacterKnowledge
```

---

# §89 八十九、Character Knowledge

角色知道的内容来源：

```text
observation
memory
interaction
direct experience
world state discovered
```

不能自动知道整个 Sandbox。

---

# §90 九十、这一步非常重要

例如：

```text
Kitchen:
Fridge contains pudding
```

角色：

```text
知道 / 不知道
```

取决于：

```text
最近是否查看冰箱
是否记得
是否刚买回来
```

这样世界才有真正的“认知层”。

---

# §91 九十一、External Interaction

用户发送：

> “你在干嘛？”

系统应该：

```text
Query Sandbox
↓
Current Character State
↓
回答
```

而不是：

```text
重新让 LLM 猜一个活动
```

---

# §92 九十二、用户要求角色做某件事

例如：

> “去买点可乐啊。”

不要直接：

```text
set activity = shopping
```

而是创建：

```text
ExternalIntent
```

然后 Sandbox 判断：

```text
是否愿意
是否有必要
当前状态
当前活动
关系
库存
```

最后决定：

```text
accept
delay
ignore
negotiate
```

---

# §93 九十三、这对于“罐头的人格”非常重要

用户不是上帝。

例如：

> “你现在去打扫房间。”

如果档案中的角色：

```text
非常懒
没有必要
正在打游戏
```

她可能：

```text
“不要，懒得动。”
```

而不是机械执行。

---

# §94 九十四、但是核心朋友可以产生更高影响

如果空凛说：

> “来打 Minecraft。”

角色可能：

```text
愿意中断当前娱乐
```

但不是因为：

```text
外部命令
```

而是：

```text
Relationship + Character Preference
```

综合结果。

---

# §95 九十五、Sandbox Event Priorities

建立：

```text
Critical
High
Normal
Low
Ambient
```

例如：

```text
Critical:
宠物严重缺粮

High:
核心朋友邀请

Normal:
普通消息

Low:
普通环境变化

Ambient:
灯光变化
```

---

# §96 九十六、事件不会全部唤醒 AI

只有：

```text
needs_decision = true
```

才交给 AI。

---

# §97 九十七、Sandbox Determinism

对于相同：

```text
state
rules
event
```

程序层应该得到一致结果。

AI 只负责：

```text
ambiguous decisions
```

这样调试可复现。

---

# §98 九十八、Simulation Trace

WebUI 增加：

```text
Sandbox Trace
```

显示：

```text
Time
State Change
Event
Action
Decision
Reason Code
Entity Interaction
```

例如：

```text
02:40
Character watching anime

02:43
External Event:
Core friend invitation

02:43
Interrupt Evaluation:
High Priority

02:43
Action:
watch_anime interrupted

02:44
Action:
start_minecraft

02:44
Mode:
HOME + GAMING + ONLINE_SOCIAL
```

---

# §99 九十九、不要记录隐藏思维链

只保存：

```text
decision
reason_code
structured factors
result
```

不要保存：

```text
hidden reasoning
chain-of-thought
```

---

# §100 一百、WebUI Sandbox Dashboard

增加：

```text
Sandbox
├── Overview
├── Character
├── Entities
├── Spaces
├── Objects
├── Inventory
├── Needs
├── Actions
├── Modes
├── Relationships
├── Social World
├── Projects
├── Events
├── Timeline
├── Snapshots
├── Simulation Trace
└── Simulator
```

---

# §101 一百零一、Sandbox Overview

显示：

```text
World Time
Character Location
Current Action
Current Mode
Needs
Energy
Focus
Active Entities
Open Loops
Active Social Spaces
```

---

# §102 一百零二、Character Inspector

显示：

```text
Identity
Static Facts
Current State
Modes
Needs
Current Action
Knowledge
Inventory
Relationship
```

---

# §103 一百零三、Pet Inspector

显示：

```text
小喵
location
activity
hunger
energy
mood
last_interaction
next_need
```

---

# §104 一百零四、Space Inspector

例如：

```text
Apartment
Living Room
Kitchen
Bedroom
```

显示：

```text
Entities
Objects
State
Available Actions
```

---

# §105 一百零五、Inventory Inspector

显示：

```text
Fridge
Cabinet
Character Inventory
```

---

# §106 一百零六、Action Inspector

显示：

```text
Current Action
Started
Elapsed
Expected End
Progress
Interruptibility
Reason
```

---

# §107 一百零七、Mode Inspector

显示：

```text
Primary Mode
Secondary Modes
Trigger
Entered At
Priority
```

---

# §108 一百零八、External Event Inspector

管理员可以看到：

```text
QQ Message
Group Event
User Action
Pet Event
World Event
```

如何影响 Sandbox。

---

# §109 一百零九、Sandbox Simulator

允许管理员：

```text
Pause
Advance 10m
Advance 1h
Inject Event
Move Entity
Change Inventory
Trigger Pet Need
Send Test Message
```

所有 Simulation 操作默认：

```text
Dry Run
```

或者明确标记：

```text
Live Sandbox
```

---

# §110 一百一十、不能让 Simulator 默认影响真实 QQ

测试事件：

```text
Dry Run
```

不会发送 QQ。

---

# §111 一百一十一、Character Bible Inspector

显示：

```text
Canonical Fact
Source
Compiled Field
Runtime Usage
```

例如：

```text
“回家立刻换猫猫睡衣”

→ Behavior Rule

Used by:
HomeEntryAction
Mode Runtime
Sandbox Decision Engine
```

这样你可以确认：

> 档案里的这条设定到底有没有真正被系统使用。

---

# §112 一百一十二、Character Bible Coverage

v2.0 必须新增：

```text
Bible Coverage Report
```

显示：

```text
Total Facts
Implemented Facts
Partially Implemented
Prompt-only
Unresolved
```

例如：

```text
120 条设定

92 Runtime
18 Rule
6 Prompt-only
4 unresolved
```

最终目标：

```text
Prompt-only 尽可能接近 0
```

---

# §113 一百一十三、人物档案覆盖率不是简单字符串匹配

必须使用：

```text
semantic mapping
```

例如：

```text
“回家立刻换睡衣”
```

对应：

```text
Home Entry Rule
```

而不是说：

```text
“这句话存在于 Prompt，所以已实现。”
```

---

# §114 一百一十四、Canonical Facts

所有静态人物事实必须进入：

```text
CanonicalCharacterFacts
```

例如：

```text
name
age
gender
birthday
height
appearance
family
residence
```

---

# §115 一百一十五、Canonical Rules

例如：

```text
no_school
no_formal_job
privacy_strict
romance_avoidance
scolding_resistance
cat_family
homewear_on_arrival
```

---

# §116 一百一十六、Canonical Relationships

例如：

```text
空凛
type = core_friend
special_rules = ...
```

---

# §117 一百一十七、Canonical World Seed

人物档案中的：

```text
Apartment
Pet
Fridge
Dessert Shop
Convenience Store
Minecraft World
Social Spaces
```

进入：

```text
SandboxSeed
```

---

# §118 一百一十八、Seed 与 Runtime 分离

Seed：

```text
“角色初始世界长什么样”
```

Runtime：

```text
“现在世界变成什么样”
```

例如：

```text
Seed:
冰箱默认有可乐

Runtime:
今天已经喝掉 4 罐
```

---

# §119 一百一十九、重启不应该重新初始化生活

除非管理员执行：

```text
Reinitialize Sandbox
```

否则：

```text
读取现有 Runtime State
```

继续生活。

---

# §120 一百二十、但第一次加载新人物时必须 Seed

流程：

```text
旧角色 Reset
↓
读取新人物档案
↓
Compile Bible
↓
Build Sandbox Seed
↓
Initialize Entities
↓
Initialize Spaces
↓
Initialize Objects
↓
Initialize Inventory
↓
Initialize Relationships
↓
Start Simulation
```

---

# §121 一百二十一、初始化状态必须符合档案

如果档案规定：

```text
独居公寓
有猫
冰箱有可乐
有长期 Minecraft 存档
```

初始化必须能够生成对应状态。

---

# §122 一百二十二、随机初始状态只能出现在非关键字段

允许：

```text
今天小喵睡在沙发还是床
```

但不能随机：

```text
是否有猫
是否独居
是否上学
是否工作
是否喜欢 Minecraft
```

这些是 Canonical Fact。

---

# §123 一百二十三、随机性必须有 Seed

如果确实需要模拟随机生活：

支持：

```text
simulation_seed
```

确保开发测试可复现。

---

# §124 一百二十四、LLM 模拟必须有输出 Schema

任何 Sandbox Decision Model 必须结构化：

```json
{
  "decision": "continue",
  "action": null,
  "reason_codes": [
    "goal_not_complete",
    "high_focus",
    "no_urgent_need"
  ]
}
```

禁止自由文本直接修改数据库。

---

# §125 一百二十五、AI 不得直接写 Sandbox

禁止：

```text
LLM
→ SQL
```

必须：

```text
LLM
→ Structured Decision
→ Validator
→ SandboxStateManager
```

---

# §126 一百二十六、状态变更必须经过 Validator

例如：

```text
LLM：
罐头去上学了
```

Validator：

```text
occupation rule:
no_school

→ reject
```

---

# §127 一百二十七、Sandbox Action Transaction

一次 Action State Change 必须尽量原子化。

例如：

```text
drink cola
```

包括：

```text
Inventory -1
Thirst change
Affect
Event
Continuity
```

必须保持一致。

---

# §128 一百二十八、失败回滚

如果：

```text
Inventory 更新成功
Character 状态更新失败
```

必须回滚或恢复一致状态。

---

# §129 一百二十九、时间不是 AI 决定的

Sandbox Clock 必须由系统控制。

AI 不得：

```text
修改当前时间
```

---

# §130 一百三十、现实时间与角色时间

系统时间：

```text
2026-...
```

Sandbox Time：

默认与系统时间同步。

不能：

```text
角色说 3 点
实际上系统是下午 3 点
```

除非档案明确允许角色时间认知模糊。

---

# §131 一百三十一、时间驱动人格模式

模式系统读取：

```text
Sandbox Clock
```

而不是：

```text
模型猜现在是不是深夜
```

---

# §132 一百三十二、生活模拟不应该“填满每一分钟”

Sandbox 可以存在：

```text
idle
rest
thinking
watching
browsing
```

没有事件时：

```text
无需生成虚构细节。
```

---

# §133 一百三十三、避免世界幻觉

禁止：

```text
AI 随机宣布：
买了一个新电脑
搬了家
养了第二只猫
开始上学
认识了新邻居
```

除非：

```text
Canon
External Event
Sandbox Rule
明确批准
```

---

# §134 一百三十四、世界事件必须有来源

每个重要 World State Change：

```text
source:
system
character_action
pet_action
external_event
user_interaction
admin
simulation
```

---

# §135 一百三十五、世界历史

保留：

```text
Sandbox Timeline
```

但只保存：

```text
meaningful events
```

不要每十分钟写一条：

```text
“罐头仍然坐在沙发。”
```

---

# §136 一百三十六、事件等级

```text
major
normal
micro
ambient
```

---

# §137 一百三十七、Sandbox Timeline 与 Memory 分离

Timeline：

```text
世界发生了什么
```

Memory：

```text
角色以后应该记住什么
```

Continuity：

```text
近期仍然持续影响角色的东西
```

三者分离。

---

# §138 一百三十八、Sandbox → Memory

重要事件可以提交：

```text
MemoryCandidate
```

再由：

```text
MemoryExtraction
```

决定是否长期记忆。

---

# §139 一百三十九、Sandbox → Continuity

近期事件可以更新：

```text
Current Interest
Open Loop
Affective Context
Recent Events
```

---

# §140 一百四十、Sandbox → Conversation

当前世界状态可以进入：

```text
Character Context
```

只在相关时。

---

# §141 一百四十一、Conversation → Sandbox

聊天可以产生：

```text
External Event
Relationship Change
New Goal
New Open Loop
Activity Interrupt
Social State
```

---

# §142 一百四十二、User Commands 不得直接修改世界

即便用户说：

```text
“你现在就在厨房。”
```

也不能直接修改：

```text
location
```

除非人物选择相信并执行。

---

# §143 一百四十三、用户邀请 / 建议 / 请求必须经过 Character Decision

例如：

```text
“出去买草莓吧。”
```

这是：

```text
External Influence
```

不是：

```text
System Command
```

---

# §144 一百四十四、核心人物可以拥有特殊外部影响力

例如：

```text
Core Friend
```

可以：

```text
increase_interrupt_priority
increase_interest
increase_social_energy
```

但是仍然不能直接修改：

```text
Sandbox state
```

---

# §145 一百四十五、最终聊天流程

标准：

```text
QQ Message
↓
OneBot
↓
Conversation Turn
↓
External Event
↓
Sandbox Event Processor
↓
Sandbox Decision
↓
Character State
↓
Character Context
↓
Social Cognition
↓
Character Response Decision
↓
Response Sequence
↓
Media / Sticker
↓
Timing
↓
QQ
↓
Post-response Sandbox Update
↓
Continuity
```

---

# §146 一百四十六、群聊流程

```text
Group Messages
↓
Conversation Turn
↓
Media Semantics
↓
Social Cognition
↓
External Event
↓
Sandbox
↓
Character Context
↓
Response
```

---

# §147 一百四十七、如果群聊内容与角色世界无关

例如：

```text
群里讨论某个和罐头无关的话题
```

Sandbox：

```text
Character Action unchanged
```

Social：

```text
possibly observe
```

---

# §148 一百四十八、如果群聊与角色世界高度相关

例如：

```text
Minecraft 群讨论她正在做的城市
```

则：

```text
Interest ↑
Social Attention ↑
Potential Response ↑
```

---

# §149 一百四十九、空凛消息处理

如果当前角色为：

```text
DEEP_NIGHT
```

空凛发：

```text
“还没睡？”
```

Sandbox：

```text
External Event:
Core Friend Message
```

Decision：

```text
可能打断当前深夜内省
```

Mode：

```text
DEEP_NIGHT
+
ONLINE_SOCIAL
```

回复风格：

```text
熟悉
随意
无需正式
```

---

# §150 一百五十、空凛邀请联机

当前：

```text
Action:
watch_animation
```

外部：

```text
Core Friend Game Invitation
```

沙盒：

```text
InterruptEvaluator
```

因为：

```text
core_friend
+
favorite_activity
```

可能：

```text
interrupt
```

之后：

```text
Action:
play_minecraft
```

---

# §151 一百五十一、空凛说教

外部事件：

```text
“你又熬夜。”
```

如果人物档案规定：

```text
对空凛：
给面子，但不改变行为
```

Response：

```text
“知道了知道了”
```

Sandbox：

```text
sleep schedule unchanged
```

---

# §152 一百五十二、现实隐私

Character Bible：

```text
protected facts
```

Sandbox：

```text
private world facts
```

Conversation Runtime：

必须阻止角色自动透露：

```text
真实姓名
住址
家庭真实信息
平台内部身份
```

除非人物档案明确允许。

---

# §153 一百五十三、不要让 Sandbox 公开“内部模拟”

用户不应该看到：

```text
Sandbox
Entity
Action
Need
Decision
Mode
```

只看到自然聊天。

---

# §154 一百五十四、WebUI 是唯一可视化后台

QQ：

```text
纯角色体验
```

WebUI：

```text
完整 Sandbox 可视化
```

---

# §155 一百五十五、WebUI 可以暂停 Sandbox

支持：

```text
Pause Simulation
Resume Simulation
Advance Time
Reset Sandbox
Rebuild from Bible
Export Snapshot
Import Snapshot
```

---

# §156 一百五十六、WebUI Character Bible

必须支持：

```text
View
Import
Validate
Compile
Diff
Recompile
```

---

# §157 一百五十七、档案更新机制

未来如果修改人物档案：

必须支持：

```text
Old Bible
↓
New Bible
↓
Diff
↓
Impact Analysis
↓
Apply
```

不要每次直接清空整个 Sandbox。

---

# §158 一百五十八、静态事实变更

例如：

```text
喜欢草莓
→
更喜欢草莓
```

可以更新 Preference。

---

# §159 一百五十九、世界事实变更

例如：

```text
新增一只宠物
```

应该执行：

```text
Migration
```

不是简单 Prompt。

---

# §160 一百六十、不可随意修改的 Canonical Fact

例如：

```text
名字
年龄
核心关系
关键经历
```

必须显示：

```text
Canonical
```

管理员明确修改后才能改变。

---

# §161 一百六十一、Sandbox Version

需要：

```text
sandbox_schema_version
character_bible_version
runtime_version
```

支持未来升级。

---

# §162 一百六十二、数据迁移

必须设计：

```text
World v1 → Sandbox v2
```

但如果这是全新的 v2.0 角色世界：

允许：

```text
old character world data = reset
new sandbox = initialized
```

---

# §163 一百六十三、旧 World 数据处理

删除 / archive：

```text
old activity episodes
old world snapshots
old routine states
old world goals
old old-character events
```

不要让旧角色的生活混进新沙盒。

---

# §164 一百六十四、旧 Memory 处理

必须清除：

```text
episodic
semantic
embedding
consolidated
relationship memory
```

与旧角色相关的全部内容。

---

# §165 一百六十五、旧 Persona 处理

必须完全替换。

不能：

```text
old persona
+
new persona
```

---

# §166 一百六十六、初始化后的验证

启动 v2.0 后必须运行：

```text
Character Initialization Validator
```

检查：

```text
Identity
Modes
Rules
Relationships
Entities
Spaces
Objects
Inventory
Projects
Social Spaces
```

---

# §167 一百六十七、Bible Coverage Validator

检查：

```text
人物档案每个章节
```

是否已经：

```text
compiled
mapped
runtime-used
```

---

# §168 一百六十八、如果某个档案字段只是 Prompt

必须报告：

```text
Prompt-only
```

最终目标是：

```text
关键行为不应长期停留在 Prompt-only。
```

---

# §169 一百六十九、完整模拟测试

必须支持：

```text
Simulation Test
```

运行：

```text
24h
72h
7 days
```

但：

不真实发送 QQ。

---

# §170 一百七十、24 小时模拟

验证：

```text
Sleep
Wake
Food
Pet
Entertainment
Gaming
Social
Shopping
Needs
Mode
```

是否符合人物档案。

---

# §171 一百七十一、7 天模拟

重点观察：

```text
有没有活动乱跳
有没有世界状态丢失
有没有库存异常
有没有宠物消失
有没有人格漂移
有没有不符合人物档案的行为
有没有重复生活模板
```

---

# §172 一百七十二、World Simulation Regression

禁止出现：

```text
08:00 早餐
09:00 早餐
10:00 早餐
```

以及：

```text
14:00 Minecraft
14:02 看剧
14:04 听歌
14:06 刷论坛
```

除非有：

```text
实际 Event
Actual Completion
External Interrupt
Need
```

---

# §173 一百七十三、世界必须出现自然的生活连续性

例如：

```text
昨天：
Minecraft 建城

今天：
继续建城

晚上：
累了休息

第二天：
看到昨天做的部分
```

而不是每天重置。

---

# §174 一百七十四、世界必须支持“未完成生活”

例如：

```text
建筑做到一半
动画看到一半
零工做到一半
冰箱采购计划未完成
垃圾还没扔
```

这些：

```text
Open Loop
```

应该持续存在。

---

# §175 一百七十五、世界必须支持“因果链”

例如：

```text
喝掉最后一罐可乐
↓
可乐库存 0
↓
补货需求
↓
购物候选
↓
如果出门必要性达到阈值
↓
外出
↓
便利店
↓
购买可乐
↓
回家
↓
库存恢复
```

这是 v2.0 与旧 Activity 系统最核心的区别。

---

# §176 一百七十六、世界必须支持“外部干预”

例如：

```text
空凛邀请联机
↓
打断动画
↓
打开 Minecraft
↓
一起游戏
```

---

# §177 一百七十七、世界必须支持“内部驱动”

例如：

```text
小喵饿了
↓
找罐头
↓
罐头添粮
↓
恢复原活动
```

---

# §178 一百七十八、世界必须支持“环境驱动”

例如：

```text
天气变化
↓
是否出门
```

前提：

如果人物档案需要这种世界因素才实现。

---

# §179 一百七十九、世界必须支持“社会驱动”

例如：

```text
群里讨论 Minecraft
↓
当前兴趣很高
↓
更可能参与
```

---

# §180 一百八十、世界必须支持“角色偏好驱动”

例如：

```text
可乐
布丁
Minecraft
猫
```

形成行为倾向。

---

# §181 一百八十一、世界必须支持“角色拒绝”

角色不是世界工具调用器。

例如：

```text
用户：
你现在去收拾房间。

Character:
不想。

Sandbox:
没有执行行动。
```

---

# §182 一百八十二、所有行为必须产生后果

例如：

```text
熬夜
↓
睡眠状态
↓
第二天下午醒
```

但不要过度模拟生理医学。

只做人物设定需要的叙事层。

---

# §183 一百八十三、角色生活不是现实人生模拟器

不要实现：

```text
真实生理系统
疾病
复杂医学
完整经济学
完整物理引擎
```

只实现：

> **人物档案需要的生活世界。**

---

# §184 一百八十四、Sandbox Complexity Budget

新增实体 / 系统之前必须回答：

```text
是否影响角色行为？
是否影响聊天？
是否影响世界连续性？
是否人物档案明确需要？
```

都不是：

不要实现。

---

# §185 一百八十五、架构原则

最终：

```text
Character Bible
= 定义角色是谁

Sandbox
= 角色在哪里生活

Entity
= 世界里有哪些存在

Space
= 世界由什么地方组成

Object
= 世界里有什么东西

Need
= 为什么角色需要做事

Action
= 角色正在做什么

Rule
= 什么事情必须/不应该发生

Decision
= 多个合理选择之间怎么选

Event
= 世界发生了什么

External Event
= 外部世界改变了沙盒

Continuity
= 近期发生过什么仍然影响角色

Memory
= 长期值得记住什么

Relationship
= 角色与用户是什么关系

Conversation
= 角色此刻如何和用户交流

Response
= 最终怎么表现
```

---

# §186 一百八十六、不得再次把 World 变成 Scheduler

禁止：

```text
World
→ Schedule
→ Activity
→ Random Pick
```

世界的核心必须是：

```text
State
+
Entities
+
Rules
+
Needs
+
Actions
+
Events
+
Decision
```

---

# §187 一百八十七、不得让 AI 每十分钟自由幻想

严格禁止：

```text
every 10 minutes
→ LLM
→ “模拟接下来十分钟生活”
```

正确：

```text
Simulation
→ deterministic update
→ detect decision point
→ AI only when needed
```

---

# §188 一百八十八、不得让 AI 直接修改数据库

必须：

```text
LLM
↓
Structured Decision
↓
Validation
↓
Sandbox Action
↓
State Mutation
```

---

# §189 一百八十九、必须支持可复现模拟

Sandbox：

```text
seed
clock
events
snapshots
```

都需要支持重放。

---

# §190 一百九十、Sandbox Replay

WebUI：

```text
Replay
```

显示：

```text
02:30
watching

02:40
pet need

02:41
pet approaches

02:42
feed cat

02:45
resume watching

02:49
external friend message

02:49
interrupt

02:50
gaming
```

这样管理员可以真正看到：

> 罐头的一天是怎么生活出来的。

---

# §191 一百九十一、Character Debugger

支持：

```text
Why is character doing this?
```

显示：

```text
Current Action
Need
Rule
Relationship
External Event
Decision Factors
```

例如：

```text
为什么现在去买可乐？

原因：
cola inventory = 0
night preference = high
replenishment need = high
shopping route available
```

不显示隐藏思维链。

---

# §192 一百九十二、Decision Trace

所有 AI Decision 保存：

```text
decision
reason_codes
inputs_summary
output
validator_result
```

不要保存完整内部 reasoning。

---

# §193 一百九十三、Performance

普通 Sandbox Tick：

尽可能：

```text
CPU / DB only
```

不调用 LLM。

---

# §194 一百九十四、LLM 调用触发条件

例如：

```text
多个候选动作都合理
外部事件复杂
角色关系影响重大
生活规划存在多种路径
人物档案没有直接规则
```

才调用。

---

# §195 一百九十五、可以为 Decision Model 单独配置模型

WebUI：

```text
Sandbox Decision Model
Sandbox Narrative Model
Character Response Model
Vision Model
Agent Model
```

但都通过当前 ModelRouter。

---

# §196 一百九十六、Sandbox Narrative Model

可选。

用于：

```text
把结构化状态转成细节
```

但：

不得改变世界事实。

---

# §197 一百九十七、Response Model

仍然通过 Character Runtime。

不要让 Sandbox Narrative Model 直接发送 QQ。

---

# §198 一百九十八、最终验收场景

至少实现以下真实场景：

## 场景 1：启动

新人物档案导入：

```text
Character Bible
↓
Sandbox
↓
罐头 + 小喵 + Apartment + Objects
```

---

## 场景 2：普通生活

没有 QQ 消息：

```text
Sandbox 自己运行。
```

---

## 场景 3：持续看剧

角色：

```text
watch_animation
```

经过 10 分钟：

```text
仍然 watching
```

不会随机换活动。

---

## 场景 4：猫饿了

```text
小喵 hunger ↑
↓
approach
↓
罐头 feed
↓
resume previous activity
```

---

## 场景 5：可乐喝完

```text
cola = 0
↓
replenishment need
↓
未来产生购物行为
```

---

## 场景 6：垃圾太多

```text
trash_level ↑
↓
required household action
↓
出门扔垃圾
```

---

## 场景 7：空凛消息

角色正在看剧：

```text
空凛：
“来打游戏”
```

最终可能：

```text
watching
→ interrupted
→ Minecraft
```

---

## 场景 8：普通网友消息

角色正在游戏：

```text
普通网友：
“晚上好”
```

可能：

```text
回复
```

但不一定：

```text
停止游戏。
```

---

## 场景 9：深夜

时间：

```text
03:30
```

角色：

```text
HOME
+
DEEP_NIGHT
```

若当前：

```text
游戏结束
社交下降
```

可能进入：

```text
deep night behavior
```

而不是单纯：

```text
sleep
```

---

## 场景 10：深夜 + 空凛

```text
03:30

深夜模式

空凛：
“还没睡？”
```

角色：

```text
DEEP_NIGHT
→ ONLINE_SOCIAL overlay
```

---

## 场景 11：网络社交

角色：

```text
HOME
```

但是：

```text
SOCIAL_SPACE = Minecraft Group
```

角色：

```text
可以高频聊天
```

同时：

```text
主活动仍然可能是坐在电脑前
```

不能因为聊天把现实活动改成：

```text
“脱离其他生活，只剩聊天”
```

---

## 场景 12：外出

可乐没有了。

甜食也没有了。

垃圾满了。

Sandbox：

```text
Home
↓
Need Outside
↓
Prepare
↓
Outdoor Mode
↓
Convenience Store
↓
Dessert Shop
↓
Home
↓
Home Mode
```

---

# §199 一百九十九、最终验收标准

v2.0 完成后：

### 人物档案

```text
人物档案中的核心设定
→
必须能够映射到 Runtime。
```

### 世界

```text
世界不再是活动列表。
```

而是：

```text
真实存在的角色生活环境。
```

### 时间

```text
时间自然向前。
```

### 行为

```text
行为由需求、规则、状态、环境、关系、外部事件共同产生。
```

### 外部世界

```text
QQ、群聊、用户、事件
```

可以影响 Sandbox。

### Sandbox

也能够反过来：

```text
影响角色回复。
```

### 生活连续性

```text
昨天发生的事情
可以影响今天。
```

### 角色自主性

```text
用户不能直接控制角色。
```

### 人物一致性

```text
Canonical Character Rules
不能被 LLM 随机覆盖。
```

---

# §200 二百、最重要的最终要求

完成后，CatooBot 应达到：

> **“罐头并不是在 QQ 里扮演一个角色，而是在 QQ 之外本来就生活着；QQ 只是她与外界发生联系的一个窗口。”**

也就是说：

如果此时没有任何 QQ 用户发消息：

```text
罐头仍然存在。
```

她可能：

```text
睡觉
玩 Minecraft
看动画
刷论坛
吃布丁
给小喵添粮
整理房间
处理一个 Minecraft 委托
和虚构的兴趣社区互动
发呆
进入深夜模式
完成某个未完成的小目标
```

QQ 消息只是：

```text
外部世界发生了一件事情。
```

而不是：

```text
角色生命开始运行。
```

---

# §201 二百零一、最终实现原则

CatooBot v2.0 应形成：

```text
人物档案
       ↓
Character Bible
       ↓
Sandbox Seed
       ↓
持续生活
       ↓
状态变化
       ↓
事件
       ↓
决策
       ↓
行动
       ↓
新的状态
       ↓
外界介入
       ↓
继续生活
```

最终：

```text
世界不是给 Bot 用的。

Bot 本身就是这个世界中的一个角色。
```

---

# §202 二百零二、开发顺序

严格按照：

```text
1. 阅读当前完整仓库
2. 阅读人物档案附件
3. 生成旧架构审计
4. 建立 Character Data Reset
5. 清除旧 Persona
6. 清除旧 Memory
7. 清除旧 Relationship
8. 清除旧 Continuity
9. 清除旧 Character World
10. 建立 Character Bible Schema
11. 建立 Character Bible Parser
12. 建立 Character Bible Compiler
13. 建立 Bible Coverage
14. 建立 Character Entity
15. 建立 Entity System
16. 建立 Pet Entity
17. 建立 Space System
18. 建立 World Object
19. 建立 Inventory
20. 建立 Need System
21. 建立 Action System
22. 建立 World Rule Engine
23. 建立 Decision Engine
24. 建立 Interrupt Engine
25. 建立 External Event System
26. 建立 Character Mode Runtime
27. 建立 Social Space
28. 建立 Livelihood / Project System
29. 建立 Sandbox Tick
30. 建立 Event-driven Wakeup
31. 建立 Sandbox Recovery
32. 建立 Sandbox Snapshot
33. 建立 Sandbox Replay
34. 建立 Character Knowledge Layer
35. 接入 Continuity
36. 接入 Memory
37. 接入 Relationship
38. 接入 Social Cognition
39. 接入 Conversation Runtime
40. 接入 Media Runtime
41. 接入 Agent Runtime
42. 接入 Response Runtime
43. 建立 WebUI Sandbox
44. 建立 Bible Inspector
45. 建立 Character Inspector
46. 建立 Entity Inspector
47. 建立 World Inspector
48. 建立 Decision Trace
49. 建立 Simulation Debugger
50. 数据库 Migration
51. 24h Simulation
52. 72h Simulation
53. 7-day Simulation
54. QQ 私聊测试
55. QQ 群聊测试
56. 空凛特殊关系测试
57. 外部事件中断测试
58. Pet 测试
59. Inventory 测试
60. Full Regression
```

---

# §203 二百零三、强制开发规则

必须：

```text
✅ 首先阅读当前真实代码
✅ 首先阅读人物档案附件
✅ 旧人物与新人物完全隔离
✅ 清除旧 Persona / Memory / Relationship / World
✅ 不删除 QQ / OneBot / User / Group / Tool / Model 配置
✅ 人物档案为 Canonical Source
✅ Character Bible 可检查
✅ Sandbox 可检查
✅ Sandbox 可暂停
✅ Sandbox 可重放
✅ Sandbox 可恢复
✅ 状态变化可追踪
✅ 决策可解释
✅ 世界状态持久化
✅ 角色行为不会每 Tick 随机改变
✅ AI 不是世界本身
✅ AI 不可绕过规则
✅ QQ 是外部世界入口
✅ Conversation 是外部世界事件之一
✅ World 与 Conversation 双向影响
```

---

# §204 二百零四、强制禁止

禁止：

```text
❌ 继续以 Activity Scheduler 作为 World 核心
❌ 每 10 分钟调用一次 LLM 生成生活
❌ 每 10 分钟随机换活动
❌ 用一个超长 System Prompt 代替 Sandbox
❌ LLM 直接写数据库
❌ LLM 直接修改 Canonical Character Facts
❌ 旧人格数据与新人物混合
❌ 自动继承旧 Memory
❌ 自动继承旧 Relationship
❌ 自动继承旧 World
❌ 把所有人物档案都永久塞进 Context
❌ 把整个 Sandbox 都塞进 Prompt
❌ 让用户指令直接修改角色状态
❌ 把角色当成“用户命令执行器”
❌ 为拟人而随机制造错误
❌ 为拟人而随机制造情绪
❌ 为拟人而随机改变活动
❌ 保存 Chain-of-Thought
```

---

# §205 二百零五、最终报告要求

开发完成后必须向我提供：

## 1. Character Bible Mapping

逐项说明：

```text
人物档案
→ Runtime
```

例如：

```text
“回家换睡衣”
→ HomeEntryRule

“小喵”
→ PetEntity

“冰箱永远有可乐”
→ Inventory Seed + Replenishment Rule

“空凛是核心朋友”
→ SpecialRelationshipProfile
```

---

## 2. Coverage Report

明确：

```text
Implemented
Partially Implemented
Prompt-only
Unresolved
```

不能声称“全部实现”而没有逐项证据。

---

## 3. Sandbox Architecture

说明：

```text
Entities
Spaces
Objects
Needs
Actions
Events
Rules
Decision
```

之间如何运行。

---

## 4. Reset Report

确认：

```text
旧 Persona
旧 Memory
旧 Relationship
旧 World
旧 Continuity
```

已经清理。

同时说明：

```text
QQ / OneBot / Users / Groups / Tools / Models
```

没有受到影响。

---

## 5. Simulation Report

至少运行：

```text
24h
72h
7 days
```

输出：

```text
Major Events
Mode Transitions
Actions
Pet Behavior
Inventory Changes
Open Loops
External Interruptions
```

---

## 6. QQ Integration Report

至少测试：

```text
普通聊天
连续消息
群聊
媒体
Sticker
空凛消息
空凛邀请
用户请求
世界状态查询
```

---

## 7. Known Limitations

必须诚实列出：

```text
哪些地方目前仍然是规则
哪些地方仍然依赖模型
哪些人物档案设定尚未结构化
哪些世界模拟仍然是简化版本
```

---

# §206 二百零六、最终目标

不要把 v2.0 做成：

```text
一个更复杂的 Bot
```

而要把它做成：

```text
一个持续存在的角色生命模拟器，
QQ 只是它与用户发生联系的接口。
```

最终用户发送：

```text
“你在干嘛？”
```

CatooBot 不应该：

```text
“根据 Persona 随机想一个回答。”
```

而应该：

```text
读取 Sandbox 当前状态
↓
罐头现在在哪里
↓
正在做什么
↓
为什么正在做
↓
小喵在哪里
↓
当前模式
↓
当前情绪 / 精力 / 专注
↓
当前未完成事情
↓
和这个用户是什么关系
↓
当前外部事件
↓
生成符合 Character Bible 的自然回答
```

---

# §207 二百零七、最核心的一句话

v2.0 最终必须从：

```text
“让 AI 扮演罐头”
```

升级到：

```text
“让罐头生活在一个持续运行的世界里，
然后让 AI 负责在这个世界里做出属于罐头的选择。”
```

现在开始：

**第一步不要写代码。**

先：

1. 阅读当前 CatooBot 最新源码。
2. 阅读本次附带的《人物档案》。
3. 建立旧架构 → 新 Sandbox 的完整迁移方案。
4. 建立人物档案 → Character Bible → Sandbox Seed 的映射表。
5. 列出所有旧数据需要清理的范围。
6. 列出所有现有模块哪些保留、哪些降级为 Sandbox 子系统、哪些废弃。
7. 然后再开始实现 v2.0。

不要因为当前旧 World 已经有大量代码，就为了“复用代码”而继续围绕 Activity Scheduler 打补丁。

如果现有 World 的核心抽象与 Character Life Sandbox 冲突：

**优先建立正确的 v2.0 Sandbox 架构，再迁移可以复用的底层能力。**

最终验收标准只有一个：

> **即使没有任何 QQ 消息，打开 WebUI，也应该能够看到“罐头此刻正在自己的生活里做什么”；而当 QQ 中发生某件事情时，这件事情应该像现实世界发生了一件事一样进入她的生活，并可能改变她接下来的行为。**