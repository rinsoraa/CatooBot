# CatooBot v1.2 开发任务
# Human Conversation Runtime + Character Continuity
# 目标：让 CatooBot 从“多个高级模块组成的 AI”进一步变成“持续生活、持续交流、具有连续性与个人习惯的网络角色”

---

# v1.2 §1 一、版本定位

当前 CatooBot 已经完成：

- v0.1 QQ / OneBot 11 / NapCat
- v0.2 AI Engine / Model Router / Provider Failover
- v0.3 Persona / Memory / Relationship / WebUI
- v0.4 Character Behavior / Initiative / Response Timing
- v0.5 Semantic Memory / Embedding / Hybrid Retrieval
- v0.6 Tool Runtime
- v0.7 Agent Runtime
- v0.8 Persistent World
- v0.9 Social Cognition
- v1.0 Episode-based World Activity
- v1.1 Multimodal / Image Understanding / Sticker Runtime

现在进入：

# CatooBot v1.2

本版本不以“增加更多功能”为主要目标。

本版本核心目标：

> **让 Persona、Memory、Relationship、World、Social Cognition、Media、Agent、Response Planner 不再像彼此独立的模块，而是共同构成一个连续的 Character Runtime。**

用户应该逐渐产生这样的体验：

> “这个角色不是每次收到消息才临时生成一个回答，而是真的一直在网上生活、记得我们以前发生过什么、知道我们最近聊到哪里、知道自己刚才在干什么，也会因为刚才发生的事情改变接下来几句话的状态。”

---

# v1.2 §2 二、最终产品目标

v1.1：

```text
CatooBot：
能够看懂图片和表情，
也能够自己收藏和使用表情。
```

v1.2：

```text
CatooBot：
能够持续地“作为同一个人”进行聊天。
```

最终用户体验应该从：

```text
消息
→ AI
→ 回复
```

升级到：

```text
持续世界
    ↓
角色当前状态
    ↓
与用户的关系
    ↓
当前对话连续性
    ↓
过去共同经历
    ↓
当前消息
    ↓
当前即时情绪
    ↓
判断是否以及如何回应
    ↓
生成一段真正连续的聊天行为
    ↓
更新角色短期连续状态
```

---

# v1.2 §3 三、最重要的设计原则

## 3.1 不再继续堆 Prompt

禁止通过单纯增加大量 System Prompt 来模拟“真人感”。

例如不要通过：

```text
“你要像真人”
“你偶尔可以说短句”
“你有时候可以不回复”
“你可以使用表情”
```

来解决所有问题。

这些只能作为行为规则的一部分。

真正的拟人感必须由：

```text
Runtime State
Conversation Turn
Continuity
Interaction Pattern
Shared Experience
World Context
Response Sequence
```

共同产生。

---

# v1.2 §4 四、核心概念变化

当前：

```text
Persona
Memory
Relationship
World
Social
Media
Response
```

v1.2 需要增加一层：

# Character Continuity

并增加一层：

# Human Conversation Runtime

最终：

```text
                         Character Runtime
                               │
               ┌───────────────┼────────────────┐
               │               │                │
               ▼               ▼                ▼
        Character State    Continuity      Relationship
               │               │                │
               └───────────────┼────────────────┘
                               ▼
                    Conversation Runtime
                               │
             ┌─────────────────┼────────────────┐
             ▼                 ▼                ▼
          Social             Memory           World
          Context            Context          Context
             │                 │                │
             └─────────────────┼────────────────┘
                               ▼
                       Character Decision
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
             Content       Expression      Timing
                │              │              │
                └──────────────┼──────────────┘
                               ▼
                       Response Sequence
                               │
                               ▼
                              QQ
                               │
                               ▼
                     Continuity Update
```

---

# v1.2 §5 五、首先必须完整阅读当前源码

开始修改之前：

**不要直接新增模块。**

必须先完整阅读当前仓库的真实代码。

重点阅读：

```text
app/character/
app/memory/
app/social/
app/world/
app/media/
app/response/
app/agent/
plugins/chat/
app/core/
app/config/
app/database/
```

以及：

```text
Message
Context Builder
EventBus
Behavior Scheduler
Task Store
WebUI
Migration
Tests
```

必须确认当前真实实现。

---

# v1.2 §6 六、必须先生成架构审计

在开始改代码之前，内部先完成：

```text
Current Message Flow
Current Conversation Flow
Current Character Flow
Current Social Flow
Current Memory Flow
Current World Flow
Current Media Flow
Current Response Flow
Current State Mutation Flow
```

重点回答：

1. 一条 QQ 消息当前经过哪些模块？
2. 哪个模块最终决定“是否回复”？
3. 哪个模块决定“回复什么”？
4. 哪个模块决定“什么时候回复”？
5. 哪个模块保存短期上下文？
6. 哪个模块保存长期记忆？
7. 哪个模块保存当前活动？
8. 哪个模块保存关系状态？
9. 哪个模块保存当前对话主题？
10. 哪些状态会在回复后发生变化？
11. 哪些状态实际上没有被写回？
12. 哪些模块之间存在重复判断？
13. 哪些模块之间存在逻辑冲突？
14. 当前是否存在多处分别生成 Context 的情况？
15. 当前消息是否可能同时触发多个 Response Task？

先审计，再修改。

---

# v1.2 §7 七、核心目标：Conversation Turn Runtime

新增：

```text
ConversationTurnRuntime
```

这是 v1.2 最核心的新组件之一。

---

# v1.2 §8 八、为什么需要 Conversation Turn

真人不会严格：

```text
一条消息
→ 一次完整回答
```

而会：

```text
用户连续发送
用户补充
用户改口
用户打断
用户继续
```

所以消息必须先形成：

```text
Conversation Turn
```

而不是每一条消息独立处理。

---

# v1.2 §9 九、Conversation Turn 生命周期

目标：

```text
Message Received
      ↓
Message Buffer
      ↓
Debounce
      ↓
Message Grouping
      ↓
Turn Classification
      ↓
Context Assembly
      ↓
Response Decision
      ↓
Response Generation
      ↓
Delivery
      ↓
Turn Finalization
      ↓
Continuity Update
```

---

# v1.2 §10 十、Message Buffer

新增：

```text
MessageBuffer
```

作用：

暂时缓存同一用户短时间内连续发送的消息。

例如：

```text
22:01:01 你干嘛呢
22:01:02 在吗
22:01:03 我突然想起来一件事
```

不要立即：

```text
3 次 AI 调用
3 次回复
```

应合并为：

```text
一个 Turn
```

---

# v1.2 §11 十一、Debounce

增加：

```text
debounce window
```

默认值应可配置。

例如：

```yaml
conversation:
  debounce:
    enabled: true
    direct_message_ms: ...
    group_message_ms: ...
```

不要写死。

---

# v1.2 §12 十二、Debounce 不是固定等待

不能：

```text
每条消息固定等待 2 秒
```

而应该考虑：

```text
消息间隔
消息长度
当前是否已经在生成回复
用户是否连续输入
群聊 / 私聊
当前角色状态
```

动态确定 Turn 是否结束。

---

# v1.2 §13 十三、用户连续发送后的聚合

例如：

```text
你玩 Minecraft 吗
我最近也在玩
但是我的服务器一直崩
```

最终 Context 应理解为：

```text
User Turn:
用户连续发送三条消息，
核心话题是 Minecraft 服务器问题。
```

而不是三个互不相关的问题。

---

# v1.2 §14 十四、Turn Classification

增加：

```text
TurnClassifier
```

至少：

```text
single
continuation
multi_message
correction
interruption
follow_up
topic_shift
closing
```

例如：

```text
“不是，我说的是另一个”
→ correction

“对了还有个事”
→ topic_shift

“等等”
→ interruption

“你觉得呢”
→ follow_up
```

---

# v1.2 §15 十五、回复进行中时收到新消息

这是 v1.2 的关键功能。

假设：

```text
User:
你觉得这个怎么样？

Bot 正在生成
```

用户又发送：

```text
不是，我是说下面那个
```

系统必须检测：

```text
current response = stale
```

然后：

```text
取消 / 丢弃旧 Response
重新基于最新 Turn 生成
```

---

# v1.2 §16 十六、Response Generation ID

为每次 Response Generation 增加：

```text
response_generation_id
```

例如：

```text
generation 41
```

新消息进入：

```text
generation 42
```

如果 41 尚未发送完成：

```text
41 = stale
```

不得发送。

---

# v1.2 §17 十七、避免过期回复

严格禁止：

```text
用户已经改变话题

Bot 仍发送几秒前生成的旧答案。
```

必须：

```text
Generation Check
```

在：

- LLM 返回
- Response Planner
- Delivery 前

至少检查一次。

---

# v1.2 §18 十八、Response Cancellation

如果当前 AI 请求支持取消：

优先取消。

如果 Provider 不支持：

允许等待返回，但：

```text
不要发送旧结果。
```

---

# v1.2 §19 十九、Follow-up Folding

例如：

```text
用户：
你现在在干嘛

用户：
看啥呢
```

第二条应该被理解为第一条的 follow-up。

Context：

```text
User:
你现在在干嘛？
看啥呢？
```

Bot：

```text
我在看《xxx》
```

而不是：

```text
回答：
“我在看剧。”

再次回答：
“……”
```

---

# v1.2 §20 二十、Conversation Session

当前已有 Session 时：

不要简单按：

```text
每条消息 = Session
```

而要增加：

```text
ConversationTurn
```

结构：

```text
Session
 ├─ Turn 1
 │   ├─ User messages
 │   └─ Assistant response
 │
 ├─ Turn 2
 │
 └─ Turn 3
```

---

# v1.2 §21 二十一、Turn Context

每个 Turn 至少包含：

```text
turn_id
session_id
user_id
group_id
message_ids
started_at
ended_at
classification
topic
response_status
generation_id
```

---

# v1.2 §22 二十二、Conversation Turn 与 Memory 分离

Conversation Turn：

```text
当前交流结构
```

Memory：

```text
长期值得记住的信息
```

不要把每个 Turn 自动写入长期 Memory。

---

# v1.2 §23 二十三、Character Continuity

新增：

```text
CharacterContinuityState
```

这是 v1.2 最核心的第二个组件。

---

# v1.2 §24 二十四、Continuity 的定义

Continuity 不是：

```text
“角色脑内思维”
```

也不是：

```text
Chain of Thought
```

而是：

> **角色在短时间尺度上保持“我还是刚才那个我”的结构化状态。**

---

# v1.2 §25 二十五、Continuity State 建议结构

```python
CharacterContinuityState(
    current_interest,
    recent_emotion,
    current_focus,
    unfinished_thought,
    open_loops,
    recent_events,
    recent_social_context,
    active_conversation_context,
    recurring_topics,
    recent_expression_style,
    temporary_preferences,
    last_response_context,
    next_natural_direction
)
```

这些字段必须是：

```text
结构化
```

而不是隐藏思维文本。

---

# v1.2 §26 二十六、Current Interest

例如：

```text
current_interest = "Minecraft 木屋屋顶"
```

角色今天一直在做这个事情。

用户突然聊到 Minecraft：

自然提高相关性。

---

# v1.2 §27 二十七、Current Focus

例如：

```text
focus = "正在看动画高潮部分"
```

因此用户问：

```text
“你现在回我方便吗？”
```

可能得到：

```text
“可以，不过我刚看到关键地方……”
```

而不是：

```text
“当然可以。”
```

---

# v1.2 §28 二十八、Unfinished Thought

角色可以有：

```text
unfinished_thought
```

例如：

```text
“刚才突然想到一个新房间布局”
```

它不是隐藏 reasoning。

只是一个：

```text
character continuity item
```

可以自然影响未来主动聊天。

---

# v1.2 §29 二十九、Open Loop

新增：

```text
OpenLoop
```

用于保存：

> 角色生活中尚未完成、尚未解决、尚未说完、尚未跟进的事情。

例如：

```text
Minecraft 木屋还没建完
某部动画还没看完
想试某个 Mod
和用户聊到一半的话题
打算做的事情
准备尝试的东西
```

---

# v1.2 §30 三十、Open Loop 类型

至少：

```text
activity
conversation
goal
interest
question
plan
shared_event
```

状态：

```text
open
paused
completed
abandoned
expired
```

---

# v1.2 §31 三十一、Open Loop 生命周期

例如：

```text
“我准备把房间改一下”
```

创建：

```text
OpenLoop
```

之后世界活动：

```text
建造
```

逐步推进。

最后：

```text
completed
```

这样角色才会有：

> “上次没做完的事情今天继续做。”

---

# v1.2 §32 三十二、Shared Experience

新增：

```text
SharedExperience
```

这是 Memory 与 Relationship 之间的特殊层。

---

# v1.2 §33 三十三、Shared Experience 的定义

它不是：

```text
“用户说过什么”
```

而是：

> **“我和这个用户共同经历 / 形成过什么。”**

---

# v1.2 §34 三十四、Shared Experience 类型

至少：

```text
shared_event
inside_joke
nickname
recurring_joke
shared_project
memorable_moment
mutual_reference
unfinished_story
shared_topic
```

例如：

```text
“我们之前一起折腾过那个 Minecraft 服务器”
```

这是 Shared Experience。

---

# v1.2 §35 三十五、Shared Experience 不应大量写入

必须使用：

```text
SharedExperienceExtractor
```

只有有长期意义的互动才能进入。

例如：

```text
普通：
“今天吃饭了吗？”
```

不要存。

而：

```text
“我们把这个问题折腾了三天终于解决了。”
```

可以存。

---

# v1.2 §36 三十六、Inside Joke

支持：

```text
inside joke
```

例如：

```text
用户和 Bot 曾经因为某个 bug 形成一个固定玩笑。
```

之后再次出现相关话题：

CatooBot 可以自然引用。

不要每次都明确解释这个梗是什么。

---

# v1.2 §37 三十七、Shared Experience Retrieval

不要每条消息加载所有共同经历。

只在：

```text
topic related
semantic similarity
entity related
relationship relevant
```

时召回。

---

# v1.2 §38 三十八、Interaction Profile

新增：

```text
InteractionProfile
```

区别于 Relationship。

---

# v1.2 §39 三十九、Relationship 与 InteractionProfile 的边界

Relationship：

```text
我们有多熟
信任程度
亲近程度
互动历史
```

InteractionProfile：

```text
这个人通常怎么聊天
```

---

# v1.2 §40 四十、InteractionProfile 建议字段

```text
preferred_response_length
preferred_tone
typical_active_hours
favorite_topics
frequent_topics
humor_style
teasing_style
common_phrases
message_frequency
multi_message_habit
topic_shift_frequency
follow_up_frequency
media_usage_habit
sticker_usage_habit
```

---

# v1.2 §41 四十一、不要把 InteractionProfile 设计成静态标签

不要：

```text
user = talkative
user = funny
user = likes_long_reply
```

单个永久标签太僵硬。

应该是：

```text
observed patterns
+
confidence
+
last_updated
```

例如：

```json
{
  "multi_message_habit": {
    "value": 0.82,
    "confidence": 0.91,
    "sample_count": 47
  }
}
```

---

# v1.2 §42 四十二、InteractionProfile 必须随时间更新

例如最近用户：

```text
经常深夜聊天
```

可以提高：

```text
night_activity_score
```

一段时间不再深夜聊天：

逐步衰减。

---

# v1.2 §43 四十三、不要把用户画像变成永久事实

所有行为画像都应允许：

```text
decay
reversal
uncertainty
```

---

# v1.2 §44 四十四、Affective Context

当前已有：

```text
mood
energy
focus
```

v1.2 增加轻量的：

```text
AffectiveContext
```

不要把它设计成复杂心理模型。

---

# v1.2 §45 四十五、AffectiveContext 建议

```text
amusement
interest
curiosity
social_energy
annoyance
warmth
engagement
```

都应该：

```text
0.0 ~ 1.0
```

---

# v1.2 §46 四十六、状态变化必须有原因

例如：

```text
amusement +0.2
reason = funny_message
```

而不是：

```text
每分钟随机变化
```

---

# v1.2 §47 四十七、事件驱动的情绪变化

输入可能来自：

```text
message
shared_joke
world_event
activity_completion
activity_failure
user_response
memory_recall
social_interaction
```

---

# v1.2 §48 四十八、情绪恢复

保留现有 mood 的渐进恢复。

新增 affect context 时也允许衰减：

```text
curiosity
amusement
annoyance
```

但不要每 tick 随机变化。

---

# v1.2 §49 四十九、Conversation-specific Affect

例如：

角色当前整体：

```text
mood = neutral
```

但因为用户刚刚说了很好笑的话：

```text
current_turn_amusement = 0.82
```

这不会永久改变 mood。

这是短期 affect。

---

# v1.2 §50 五十、Character Continuity Update

一次回复结束后：

```text
Response
↓
Continuity Update
```

更新：

```text
current_interest
recent_emotion
recent_social_context
last_response_context
open_loops
shared_experience candidates
interaction patterns
```

---

# v1.2 §51 五十一、必须避免“状态污染”

例如一次：

```text
用户讲了一个笑话
```

不要永久：

```text
mood = very_happy
```

应该：

```text
turn affect
```

随时间衰减。

---

# v1.2 §52 五十二、Context Fusion

新增：

```text
CharacterContextAssembler
```

或者扩展现有 Context Builder。

目标：

将所有模块的相关信息组合成：

```text
CharacterContext
```

---

# v1.2 §53 五十三、CharacterContext 不应包含全部数据

禁止：

```text
把整个 Memory
整个 World
整个 Relationship
整个 Social History
整个 Sticker Library
```

全部塞进 Prompt。

必须：

```text
Relevant Retrieval
```

---

# v1.2 §54 五十四、Context 层次

建议：

```text
Layer 0:
System / Persona

Layer 1:
Current Turn

Layer 2:
Immediate Conversation Context

Layer 3:
Current Character State

Layer 4:
Relationship / Interaction Profile

Layer 5:
World Context

Layer 6:
Relevant Memory

Layer 7:
Shared Experience

Layer 8:
Media Context

Layer 9:
Agent Result
```

只注入相关层。

---

# v1.2 §55 五十五、Immediate Conversation Context

优先级最高。

包含：

```text
最近几条消息
当前 Turn
用户当前问题
用户最近补充
回复中是否被打断
```

---

# v1.2 §56 五十六、World Context

只注入：

```text
当前活动
activity detail
location
当前 focus
secondary context
recent micro event
```

而不是完整 World Timeline。

---

# v1.2 §57 五十七、Memory Context

只注入：

```text
与当前话题相关
与用户相关
与当前行为相关
```

---

# v1.2 §58 五十八、Shared Experience Context

只注入：

```text
当前 Topic 相关
当前用户相关
当前语义相似
```

---

# v1.2 §59 五十九、Response Decision

v1.2 的核心变化：

不要让 LLM 只有：

```text
“我要回复什么？”
```

而应该先回答：

```text
Should respond?
What is the conversational intent?
How much should I say?
Should I ask?
Should I continue?
Should I use media?
Should I split messages?
Should I wait?
```

---

# v1.2 §60 六十、Conversation Decision

新增：

```text
ConversationDecisionEngine
```

输出：

```json
{
  "respond": true,
  "intent": "follow_up",
  "engagement": 0.74,
  "response_style": "casual",
  "message_shape": "short",
  "should_ask": false,
  "should_continue": false,
  "expression_opportunity": true
}
```

---

# v1.2 §61 六十一、不要完全依赖模型

ConversationDecisionEngine：

```text
rules
+
state
+
social context
+
model
```

组合。

---

# v1.2 §62 六十二、低信息回复

系统必须允许：

```text
嗯
哦
哈哈
草
笑死
啊？
确实
行
好
```

甚至：

```text
……
```

但必须结合：

```text
relationship
topic
emotion
context
```

决定。

---

# v1.2 §63 六十三、不要每条消息都“完整回答”

例如：

```text
用户：
我刚吃完饭
```

不一定需要：

```text
“听起来不错，你今天吃的是什么？”
```

可以：

```text
“这么早就吃完了”
```

也可以：

```text
“吃了啥”
```

也可以：

```text
“我也刚好饿了”
```

---

# v1.2 §64 六十四、Follow-up 行为

真人经常：

```text
回答 + 自然追问
```

但不是每次追问。

引入：

```text
question_opportunity
```

而不是：

```text
每次都问一个问题
```

---

# v1.2 §65 六十五、反问频率

系统应该控制：

```text
连续追问次数
```

例如：

用户连续回答：

```text
嗯
```

Bot 不应该继续：

```text
你呢？
怎么啦？
为什么？
然后呢？
```

变成人审问。

---

# v1.2 §66 六十六、Conversational Momentum

增加：

```text
conversation_momentum
```

用于判断当前对话是否正在自然延续。

例如：

```text
高：
连续讨论、追问、情绪高

中：
普通闲聊

低：
短回答、重复、无新信息
```

---

# v1.2 §67 六十七、Momentum 不是概率

继续遵守此前架构原则：

不要：

```python
random() < momentum
```

而是用：

```text
momentum
```

作为决策输入。

---

# v1.2 §68 六十八、Topic Continuity

当前 Topic 系统继续使用。

v1.2 增加：

```text
topic momentum
topic age
topic enthusiasm
topic fatigue
```

这样：

```text
一个话题聊了 30 分钟
```

角色可能开始自然转移。

---

# v1.2 §69 六十九、Topic Fatigue

例如：

```text
同一个话题
连续 20 轮
```

不应该永远：

```text
继续问同样的问题。
```

Topic 可以：

```text
deepening
stable
fatiguing
completed
```

---

# v1.2 §70 七十、自然 Topic Shift

角色可以自然地从：

```text
Minecraft
```

转到：

```text
“说起来，我刚才看到一个挺离谱的东西”
```

但不要：

```text
完全随机跳题
```

Topic Shift 必须有连接原因。

---

# v1.2 §71 七十一、Shared Topic Bridge

如果有：

```text
SharedExperience
```

可以作为话题桥。

例如：

```text
Minecraft
↓
之前一起折腾服务器
↓
想到服务器问题
↓
自然带出相关话题
```

---

# v1.2 §72 七十二、Response Shape

Response Planner 扩展为：

```text
ResponseSequence
```

允许：

```text
text
sticker
native_face
pause
typing_pause
```

---

# v1.2 §73 七十三、ResponseSequence 示例

例如：

```json
{
  "sequence": [
    {
      "type": "text",
      "text": "你等会"
    },
    {
      "type": "pause",
      "duration": 0.8
    },
    {
      "type": "text",
      "text": "这个也太离谱了"
    },
    {
      "type": "sticker",
      "sticker_id": "xxx"
    }
  ]
}
```

---

# v1.2 §74 七十四、连续消息发送

允许：

```text
“不是”
```

停顿：

```text
“我真服了”
```

但：

**不要为了模拟真人而机械分段。**

分段必须来自：

```text
语义单元
情绪变化
临场修正
自然停顿
```

---

# v1.2 §75 七十五、Response Sequence 不是动画脚本

禁止：

```text
每条消息固定：
2句
0.8秒
表情
```

否则会产生新的机械感。

---

# v1.2 §76 七十六、Typing / Delay

保留现有 ReplyTiming。

v1.2 增加输入：

```text
current_activity
focus
conversation_momentum
message_complexity
user_familiarity
recent_response_rate
```

---

# v1.2 §77 七十七、回复速度应该表现“能力状态”

例如：

```text
角色正在玩游戏
→ 回复可能慢

角色正在等消息
→ 回复可能快

角色刚刚主动发消息
→ 对方回复后可能很快回

角色正在睡觉
→ 不应该即时回复
```

必须读取 World Context。

---

# v1.2 §78 七十八、不要把延迟完全随机

应：

```text
state-aware timing
```

仍允许自然抖动，但只是微调。

---

# v1.2 §79 七十九、沉默也是一种响应

角色可以决定：

```text
no_response
```

但必须受到：

```text
Social Cognition
Behavior Policy
Relationship
Conversation Context
```

共同限制。

---

# v1.2 §80 八十、不要让沉默变成固定概率

禁止：

```python
random() < 0.2
```

决定不回复。

---

# v1.2 §81 八十一、沉默原因

内部可以结构化记录：

```text
already answered
low conversational value
user appears finished
current activity interruption cost high
group conversation not relevant
```

不要记录隐藏思维链。

---

# v1.2 §82 八十二、群聊兼容 v0.9

不要重写 Social Cognition。

改造为：

```text
Incoming Messages
↓
Conversation Turn
↓
Lightweight Media Understanding
↓
Social Cognition
↓
Character Decision
```

---

# v1.2 §83 八十三、群聊连续消息

如果同一个用户：

```text
A
A
A
```

连续发送：

Social Cognition 应该将它视为：

```text
one conversational burst
```

而不是三个完全独立事件。

---

# v1.2 §84 八十四、Group Topic Context

Social Cognition 应获得：

```text
current_topic
topic_momentum
conversation_direction
recent_participants
media_semantics
bot_activity_relevance
```

---

# v1.2 §85 八十五、删除冲突规则

检查当前 Persona / Character Context 中是否还有：

```text
“群聊只回应 @”
```

这种绝对规则。

必须修改为：

```text
是否参与由 Social Cognition 决定。
如果决定参与，则自然参与。
```

---

# v1.2 §86 八十六、群聊不要过度拟人

群聊中的：

```text
沉默
观察
偶尔一句
短插话
```

应该比：

```text
长篇回复
完整解释
```

更常见。

---

# v1.2 §87 八十七、Media Integration

继续使用 v1.1 MediaRuntime。

但 v1.2 必须让：

```text
Image
Sticker
NativeFace
```

成为 Conversation Context 的一部分。

---

# v1.2 §88 八十八、轻量媒体语义优先

收到图片：

先得到：

```text
media_type
available
basic summary
```

Social Cognition 可以先运行。

只有需要深度理解时：

```text
Vision Runtime
```

再展开。

---

# v1.2 §89 八十九、Sticker 仍然必须独立于 Image

严格保持：

```text
Image
≠
Sticker
```

不能因为 Conversation Runtime 需要表达，就允许普通 Image 被 StickerSelector 使用。

---

# v1.2 §90 九十、Expression Decision

v1.1 继续保留。

v1.2 增加：

```text
current_turn_emotion
character_affect
relationship
conversation_style
recent_expression
```

作为输入。

---

# v1.2 §91 九十一、表情使用要有个人习惯

例如：

角色最近越来越喜欢某种猫猫表情。

应该逐渐形成：

```text
preferred_expression_style
```

但是不要硬编码。

由实际行为统计 + 角色偏好形成。

---

# v1.2 §92 九十二、Sticker Repetition Guard

继续阻止：

```text
同一表情连续发送
```

并进一步考虑：

```text
same semantic expression
```

避免：

```text
三张不同的“无语猫”
```

连续出现。

---

# v1.2 §93 九十三、Agent Integration

Agent Result：

```text
Agent
↓
Character Continuity
```

例如：

用户让 Bot 查资料：

完成后：

```text
Agent Result
```

如果结果与当前 Conversation 相关：

注入 Character Context。

---

# v1.2 §94 九十四、Agent 不应该直接成为人格

禁止：

```text
Agent → final response
```

继续：

```text
Agent Result
→ Character Runtime
→ Response Decision
→ Response Sequence
```

---

# v1.2 §95 九十五、World Integration

World Activity 是：

```text
角色当前正在做什么
```

Continuity 是：

```text
角色最近持续关注什么
```

两者不同。

---

# v1.2 §96 九十六、Activity 与 Open Loop

例如：

```text
Primary Activity:
玩 Minecraft

Open Loop:
木屋屋顶还没建完
```

即使 Activity 暂时变成：

```text
吃饭
```

Open Loop 仍然存在。

回来后：

```text
继续建造
```

---

# v1.2 §97 九十七、Activity Detail

v1.0 ActivityEpisode 增加：

```text
detail
entities
current_object
progress
next_action
```

---

# v1.2 §98 九十八、World Micro Events

增加：

```text
WorldMicroEvent
```

例如：

```text
发现了一个新 Mod
看到搞笑剧情
游戏里完成一个小目标
突然想到一个点子
音乐切到喜欢的歌
东西打翻了
收到消息
```

这些不是大型 Life Event。

只是：

```text
micro continuity
```

---

# v1.2 §99 九十九、Micro Event 不要刷屏

Micro Event 默认：

```text
只存在于内部
```

只有与：

```text
用户对话
主动聊天
当前活动
Memory
```

相关时才产生外部影响。

---

# v1.2 §100 一百、Character Continuity 与 World

World 发生：

```text
Micro Event
```

可以更新：

```text
recent_events
current_interest
open_loops
affective_context
```

例如：

```text
发现好看的建筑
↓
interest + 0.2
open_loop:
“以后想试这种建筑风格”
```

---

# v1.2 §101 一百零一、Continuity 与主动聊天

Initiative Engine 可以读取：

```text
open_loops
shared_experiences
recent_events
current_interest
unfinished_thought
```

从而生成：

```text
自然的话题候选
```

---

# v1.2 §102 一百零二、主动聊天必须避免“找存在感”

候选必须回答：

```text
为什么是现在？
```

理由可能是：

```text
刚发生了世界事件
刚完成了一件事
突然想起共同经历
想继续之前的话题
某个 Open Loop 有进展
```

而不是：

```text
距离上次聊天 6 小时
→ 自动说话
```

---

# v1.2 §103 一百零三、Proactive Topic Candidate

新增：

```text
ProactiveTopicCandidate
```

字段：

```text
source
topic
relevance
freshness
relationship_relevance
world_relevance
shared_history_relevance
naturalness
```

---

# v1.2 §104 一百零四、Initiative Gate

继续：

```text
Behavior Policy
```

再加入：

```text
naturalness
```

如果：

```text
没有自然理由
```

就不要主动说话。

---

# v1.2 §105 一百零五、Continuity Decay

Continuity 不是永久存储。

字段：

```text
current_interest
unfinished_thought
temporary_preference
recent_emotion
```

需要衰减。

---

# v1.2 §106 一百零六、不同状态不同 TTL

例如：

```text
recent_emotion
→ 分钟级 / 小时级

current_interest
→ 小时级 / 天级

open_loop
→ 天级

shared_experience
→ 长期
```

不要全部永久保存。

---

# v1.2 §107 一百零七、Continuity Persistence

需要持久化：

```text
character_continuity
open_loops
micro_events
shared_experiences
interaction_profile
```

但：

```text
recent_turn_cache
```

可以保留较短生命周期。

---

# v1.2 §108 一百零八、数据库迁移

新增：

```text
conversation_turns
conversation_messages
character_continuity
open_loops
shared_experiences
interaction_profiles
affective_events
micro_events
```

如果当前架构已有等价表：

必须复用，不重复创建。

---

# v1.2 §109 一百零九、Migration 要求

禁止：

- 删除数据库
- 重建数据库
- 清空旧 Memory
- 清空 Relationship
- 清空 World
- 清空 Social 数据
- 清空 Sticker Library

必须增量迁移。

---

# v1.2 §110 一百一十、Continuity Update Queue

不要让一次回复完成后立即同步执行所有分析。

可以：

```text
Response Sent
↓
Continuity Update Queue
```

后台更新：

```text
interaction profile
shared experience candidate
open loop
affective event
```

核心回复不应被这些后台任务阻塞。

---

# v1.2 §111 一百一十一、重要：只有真正有意义的内容才写入 Continuity

不要每条：

```text
“哈哈”
```

都生成：

```text
MicroEvent
```

必须经过：

```text
ContinuityRelevanceEvaluator
```

---

# v1.2 §112 一百一十二、Continuity Relevance

输入：

```text
message
turn
relationship
topic
world
history
```

输出：

```text
ignore
temporary
update
create_open_loop
candidate_shared_experience
```

---

# v1.2 §113 一百一十三、禁止隐藏思维链

所有分析必须只保留：

```text
reason_code
summary
confidence
source
```

例如：

```json
{
  "type": "shared_experience_candidate",
  "summary": "双方再次提及此前一起解决的 Minecraft 服务器问题",
  "confidence": 0.91
}
```

不要保存：

```text
完整模型内部 reasoning
```

---

# v1.2 §114 一百一十四、Interaction Pattern Learning

记录用户：

```text
连续发送消息
喜欢使用表情
经常追问
经常突然转话题
通常喜欢短回复
经常深夜出现
```

但必须：

```text
统计
+
衰减
+
置信度
```

---

# v1.2 §115 一百一十五、Interaction Pattern 不应污染 Persona

用户习惯是：

```text
User-specific context
```

不能修改：

```text
global Persona
```

---

# v1.2 §116 一百一十六、Relationship 不应因为消息数量简单升级

当前已有的阶段机制可以保留作为基础指标。

但新增：

```text
relationship_quality
```

考虑：

```text
shared_experiences
trust_signals
conversation_depth
mutual_jokes
supportive_interactions
recent_interaction_quality
```

---

# v1.2 §117 一百一十七、Relationship Dynamics

关系可以：

```text
变暖
稳定
变冷
恢复
```

但必须有实际事件依据。

不要：

```text
每 10 次消息 +1 好感
```

---

# v1.2 §118 一百一十八、不要模拟现实心理疾病、人格障碍等

本版本只做：

```text
character state
conversation behavior
relationship dynamics
```

不要引入医学或心理诊断模型。

---

# v1.2 §119 一百一十九、Natural Conversation Noise

可以适量允许：

```text
短句
重复字
轻微口语化
自我修正
停顿
话说一半
突然想到补充
```

但：

不要主动制造拼写错误。

---

# v1.2 §120 一百二十、Self-correction

例如：

```text
“周五……不对，是周六”
```

这类修正可以自然出现。

但不要为了拟人而每条消息都修正。

---

# v1.2 §121 一百二十一、Message Shape Diversity

ResponsePlanner 可以从：

```text
single
multi_sentence
multi_message
sticker_only
text_and_sticker
short_ack
follow_up
```

中选择。

选择依据：

```text
context
emotion
relationship
topic
current_state
```

---

# v1.2 §122 一百二十二、不要使用固定比例

禁止：

```text
30% sticker
20% short
50% long
```

作为最终表现逻辑。

统计只能：

```text
monitor
```

不能直接：

```text
驱动行为
```

---

# v1.2 §123 一百二十三、Conversation Repair

新增：

```text
ConversationRepair
```

处理：

```text
用户误解
角色误解
上下文错位
回答跑题
媒体理解错误
用户纠正
```

例如：

用户：

```text
不是这个
```

角色：

```text
“哦哦，你说的是另一个，我刚理解歪了。”
```

然后重新生成当前 Turn。

---

# v1.2 §124 一百二十四、不要防御式解释

错误修复应该短。

不要：

```text
“很抱歉，我之前对您的问题产生了误解……”
```

真人更可能：

```text
“哦靠，我理解歪了。”
```

具体语气交给 Persona。

---

# v1.2 §125 一百二十五、Conversation Repair 状态

增加：

```text
repair_count
misunderstanding_type
resolved
```

避免同一个误解循环。

---

# v1.2 §126 一百二十六、Conversation Memory Window

当前上下文应采用：

```text
recent turns
+
topic summary
+
relevant memory
+
relevant shared experiences
```

而不是无限累积消息。

---

# v1.2 §127 一百二十七、Conversation Summarizer

当 Turn 数量达到阈值：

```text
最近消息
↓
Summary
```

Summary 应包含：

```text
topic
important facts
open questions
tone
unresolved points
shared references
```

不要记录完整原文。

---

# v1.2 §128 一百二十八、Topic Summary

例如：

```text
当前正在讨论 Minecraft 服务器崩溃。

用户使用 Forge 1.20.1。
此前已经尝试过重装。
目前仍未解决。
用户情绪略烦躁。
```

这属于：

```text
conversation context
```

不是长期 Memory。

---

# v1.2 §129 一百二十九、Context Compression

必须能：

```text
压缩
恢复
更新
```

避免随着长期聊天 Token 无限增长。

---

# v1.2 §130 一百三十、Conversation Archive

长期旧 Conversation：

可以：

```text
archive
```

但不要自动变成 Memory。

---

# v1.2 §131 一百三十一、WebUI

v1.2 必须增加：

```text
Conversation Runtime
```

页面。

---

# v1.2 §132 一百三十二、Dashboard

显示：

```text
Active Turns
Buffered Messages
Interrupted Responses
Stale Responses
Average Turn Length
Average User Burst
Conversation Repairs
Topic Shifts
```

---

# v1.2 §133 一百三十三、Conversation Explorer

管理员可以查看：

```text
Session
↓
Turn
↓
Messages
↓
Decision
↓
Response Sequence
↓
Continuity Update
```

---

# v1.2 §134 一百三十四、Continuity Inspector

显示：

```text
Current Interest
Current Focus
Recent Affect
Open Loops
Recent Events
Shared Experiences
Interaction Profile
```

并显示：

```text
来源
更新时间
置信度
TTL / decay
```

---

# v1.2 §135 一百三十五、Context Inspector

管理员选择某一条消息：

显示最终 Character Context 是：

```text
Persona
Current Turn
World
Relationship
Memory
Shared Experience
Social
Media
Continuity
Agent Result
```

哪些被注入：

```text
included
```

哪些被过滤：

```text
excluded
```

以及原因。

---

# v1.2 §136 一百三十六、Response Simulator

WebUI 输入：

```text
用户
消息
群聊上下文
时间
```

模拟：

```text
Turn Classification
Conversation Decision
Context Assembly
Character Decision
Response Sequence
Expression Decision
Timing
```

但：

**绝对不得发送 QQ 消息。**

---

# v1.2 §137 一百三十七、Conversation Replay

允许管理员选择历史 Turn：

```text
Replay
```

重新执行：

```text
Context Assembly
Decision
Response
```

默认：

```text
dry-run
```

不会真实发送。

---

# v1.2 §138 一百三十八、Continuity Debugging

可以查看：

```text
为什么这个 Open Loop 被创建
为什么这个 Shared Experience 被召回
为什么当前 Topic 没有继续
为什么角色决定不追问
为什么当前回复只有一句
为什么用了这个 Sticker
```

但只显示：

```text
reason codes
structured explanation
```

不显示 hidden chain-of-thought。

---

# v1.2 §139 一百三十九、Metrics

增加：

```text
conversation_turn_rate
message_burst_rate
response_cancellation_rate
stale_response_rate
follow_up_rate
conversation_repair_rate
topic_switch_rate
continuity_update_rate
open_loop_creation_rate
shared_experience_creation_rate
memory_write_rate
```

---

# v1.2 §140 一百四十、拟人化质量指标

不要简单用：

```text
“像不像真人：87分”
```

而应该建立可观察指标：

```text
连续消息正确聚合率
过期回复率
重复回答率
上下文错位率
无意义追问率
重复表情率
Topic 跳转异常率
World 与回复冲突率
Relationship 与语气冲突率
```

这些可以用于测试。

---

# v1.2 §141 一百四十一、Humanity Regression Suite

增加专门的：

```text
tests/human_conversation/
```

---

# v1.2 §142 一百四十二、测试：连续消息

输入：

```text
你干嘛呢
在吗
我突然想到一件事
```

期望：

```text
一个 Turn
```

而不是三个独立 Response。

---

# v1.2 §143 一百四十三、测试：追问

```text
你在看什么
看的什么
哪一集
```

期望：

```text
连续对话
```

不重复解释。

---

# v1.2 §144 一百四十四、测试：纠正

```text
用户：
你说的是 A？

Bot:
……

用户：
不对，我说的是 B
```

旧回复必须过期。

---

# v1.2 §145 一百四十五、测试：打断

Bot 正在生成：

```text
Response A
```

用户：

```text
等等，不是这个
```

Response A：

```text
不得发送
```

必须生成：

```text
Response B
```

---

# v1.2 §146 一百四十六、测试：低信息回复

输入：

```text
哈哈
```

系统允许：

```text
哈哈
```

而不是强制生成完整段落。

---

# v1.2 §147 一百四十七、测试：连续追问

用户：

```text
怎么样
什么怎么样
为什么
那然后呢
```

不得：

```text
连续四次反问
```

---

# v1.2 §148 一百四十八、测试：Topic Fatigue

一个话题连续很多轮。

系统应该：

```text
逐渐降低继续重复追问的倾向
```

必要时自然转换。

---

# v1.2 §149 一百四十九、测试：Shared Experience

用户提到以前共同经历：

系统能够：

```text
召回 Shared Experience
```

但不要每次都强行提及。

---

# v1.2 §150 一百五十、测试：Open Loop

角色之前：

```text
木屋还没建完
```

后来再次进入 Minecraft Activity：

应提高：

```text
继续建造
```

的上下文相关性。

---

# v1.2 §151 一百五十一、测试：World Continuity

角色：

```text
当前正在看某动画
```

用户：

```text
看的哪一部？
```

必须能回答详细 Activity Detail。

不是：

```text
“看动画。”
```

---

# v1.2 §152 一百五十二、测试：Activity + Conversation

角色：

```text
正在玩 Minecraft
```

用户：

```text
你在干嘛？
```

Bot：

```text
自然回答当前具体活动。
```

回复后：

```text
Primary Activity 不应无故变成 chatting。
```

---

# v1.2 §153 一百五十三、测试：Group Social

普通群聊：

```text
Bot 不应每句话插入。
```

相关话题：

```text
Social Cognition 可以允许自然参与。
```

---

# v1.2 §154 一百五十四、测试：Media

用户：

```text
[搞笑表情]
```

Social Cognition 与 Character 都能看到：

```text
sticker semantics
```

---

# v1.2 §155 一百五十五、测试：Sticker Personality

同一种表达连续多次出现：

不得：

```text
每次使用同一 Sticker。
```

应该考虑：

```text
recent expression
personal preference
novelty
```

---

# v1.2 §156 一百五十六、测试：普通图片

用户：

```text
[一张普通照片]
```

Bot：

```text
能够理解照片
```

但：

```text
不能被 StickerSelector 使用
```

---

# v1.2 §157 一百五十七、测试：Agent

Agent Task 完成后：

Character 应自然表达结果。

不得：

```text
Task completed.
```

---

# v1.2 §158 一百五十八、测试：Conversation + Agent

用户：

```text
帮我查一下……
```

Bot 正在执行。

用户：

```text
算了不用查了
```

应该：

```text
取消 Agent
```

然后重新形成新的 Conversation Turn。

---

# v1.2 §159 一百五十九、测试：Memory

普通闲聊：

不产生大量长期 Memory。

---

# v1.2 §160 一百六十、测试：Shared Experience 与 Memory 分离

同一事件：

```text
可以成为 Shared Experience
```

但：

```text
不一定成为 Long-term Memory
```

反之亦然。

---

# v1.2 §161 一百六十一、测试：State Decay

验证：

```text
recent emotion
current interest
temporary preference
```

能够自然衰减。

---

# v1.2 §162 一百六十二、测试：Interaction Profile

长期模拟：

```text
用户习惯变化
```

验证 profile：

```text
不会永久固定
```

---

# v1.2 §163 一百六十三、测试：重启

重启 CatooBot：

必须保留：

```text
Relationship
Memory
World
Open Loops
Shared Experiences
Interaction Profile
Continuity
```

短期：

```text
当前正在生成的 Response
```

不得恢复执行。

---

# v1.2 §164 一百六十四、并发要求

同一个用户：

默认：

```text
one active turn
```

同一个群：

允许：

```text
multiple participants
```

但 Bot 只能保持一个：

```text
group conversation state
```

不能每个用户各自产生完全独立 Group Topic。

---

# v1.2 §165 一百六十五、Session Lock

增加：

```text
ConversationRuntimeLock
```

防止：

```text
两个回复同时发送
```

除非 ResponseSequence 本身就是明确连续消息。

---

# v1.2 §166 一百六十六、Task 与 Conversation Lock

Agent Task：

不能永久占用 Conversation Lock。

复杂任务运行期间：

用户仍然可以正常发消息。

---

# v1.2 §167 一百六十七、Stale Task Context

如果用户改变目标：

旧 Agent Task / Context：

必须变成：

```text
stale
```

不能继续污染当前回复。

---

# v1.2 §168 一百六十八、错误处理

任何 v1.2 新增 Runtime：

不得导致 CatooBot 主循环崩溃。

必须：

```text
catch
classify
log
recover
```

---

# v1.2 §169 一百六十九、Redis / SQLite / Cache

如果当前架构只使用 SQLite：

不要为了 v1.2 强行引入 Redis。

优先使用现有：

```text
SQLite
asyncio
in-memory cache
```

只有在当前代码已有基础时才扩展。

---

# v1.2 §170 一百七十、性能

不要让每条 QQ 消息增加：

```text
5 次 LLM 调用
```

v1.2 核心原则：

```text
多数消息：
1 次主要生成

复杂判断：
按需调用

后台更新：
异步
```

---

# v1.2 §171 一百七十一、Context Cost

必须监控：

```text
prompt tokens
memory tokens
world tokens
conversation tokens
continuity tokens
```

不能因为加入 Continuity 导致 Context 无限增长。

---

# v1.2 §172 一百七十二、Continuity Compression

当：

```text
recent_events
```

过多：

压缩成：

```text
summary
```

当：

```text
open_loops
```

完成：

归档。

---

# v1.2 §173 一百七十三、Shared Experience Compression

长期 Shared Experience：

可以：

```text
merge
deduplicate
summarize
```

例如：

```text
多次关于同一 Minecraft 服务器的经历
```

最终形成：

```text
shared experience thread
```

而不是几十条重复记录。

---

# v1.2 §174 一百七十四、最终架构

完成后目标：

```text
                          CatooBot
                             │
               ┌─────────────┴─────────────┐
               │                           │
        Persistent Character        Conversation Runtime
               │                           │
        ┌──────┼───────┐          ┌───────┼────────┐
        │      │       │          │       │        │
      Persona Memory Relationship Turn   Social   Media
        │      │       │          │       │        │
        └──────┼───────┘          └───────┼────────┘
               │                           │
               └─────────────┬─────────────┘
                             ▼
                    Character Context
                             │
                             ▼
                    Character Decision
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
           Content        Expression      Timing
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                     Response Sequence
                             │
                             ▼
                            QQ
                             │
                             ▼
                  Continuity Update
                             │
          ┌──────────────────┼─────────────────┐
          ▼                  ▼                 ▼
      Open Loops       Shared Experience   Interaction
                                           Profile
```

---

# v1.2 §175 一百七十五、v1.2 核心模块最终清单

新增：

```text
✅ ConversationTurnRuntime
✅ MessageBuffer
✅ TurnClassifier
✅ ResponseGenerationManager
✅ ResponseCancellation
✅ ConversationDecisionEngine
✅ CharacterContinuityState
✅ ContinuityUpdateEngine
✅ ContinuityRelevanceEvaluator
✅ OpenLoopManager
✅ SharedExperienceManager
✅ SharedExperienceExtractor
✅ InteractionProfile
✅ InteractionPatternLearner
✅ AffectiveContext
✅ TopicMomentum
✅ TopicFatigue
✅ ConversationRepair
✅ ResponseSequence
✅ ContextFusion / CharacterContextAssembler
✅ World Micro Event
✅ Continuity Decay
✅ Context Compression
```

兼容升级：

```text
✅ Character Runtime
✅ Response Planner
✅ Response Timing
✅ Social Cognition
✅ World Activity
✅ Memory
✅ Relationship
✅ Topic
✅ Initiative
✅ Media / Sticker
✅ Agent
✅ WebUI
```

---

# v1.2 §176 一百七十六、明确禁止的实现方式

绝对禁止使用以下方式作为主要实现：

```text
❌ 大量 Prompt 强行模拟真人
❌ random() 决定是否回复
❌ random() 决定是否使用表情
❌ 每分钟随机修改人格状态
❌ 每条消息都调用 Memory
❌ 每条消息都调用 Vision
❌ 每条消息都创建新的 Topic
❌ 每条消息都生成 Memory
❌ 每条消息都更新 Relationship
❌ 每条消息都生成 Life Event
❌ 固定比例决定短回复/长回复
❌ 固定概率决定主动聊天
❌ 固定概率决定沉默
❌ 每条群消息都等待 @
❌ 每个模块自己创建独立上下文
❌ Agent 直接生成最终回复
❌ Sticker Runtime 绕过 Character Runtime
❌ World Activity 无理由改变
❌ Continuity 保存隐藏 Chain of Thought
❌ 用内部状态替代真实用户上下文
```

---

# v1.2 §177 一百七十七、非常重要：不是所有“真人行为”都要模拟

不要为了所谓拟人度加入：

```text
故意撒谎
故意装忙
故意制造错误
故意延迟回复
故意不回答
故意制造情绪波动
```

除非有明确上下文依据。

目标不是：

> “模拟人类所有缺点。”

而是：

> “让角色的行为具有连续性、上下文敏感性、个人习惯和自然的不确定性。”

---

# v1.2 §178 一百七十八、最终体验标准

完成以后，以下行为应该自然发生。

## 场景 A：连续消息

用户：

```text
你在干嘛
在吗
我突然想到一件事
```

Bot：

```text
只形成一次自然回复
```

---

## 场景 B：追问

用户：

```text
你在看什么
```

Bot：

```text
我在看……
```

用户：

```text
哪一部
```

Bot 应该直接理解：

```text
还是在问刚才那个。
```

而不是重新解释上下文。

---

## 场景 C：用户打断

Bot 正在生成。

用户：

```text
不是不是
```

旧回复：

```text
废弃
```

新回复：

```text
基于修正后的上下文。
```

---

## 场景 D：过去共同经历

用户：

```text
又遇到那个问题了
```

如果历史里有：

```text
双方以前一起处理过类似问题
```

Bot：

```text
自然联想到那次经历。
```

不要机械说：

```text
根据我的记忆，你之前……
```

---

## 场景 E：当前世界状态

角色：

```text
primary activity = Minecraft building
detail = 木屋屋顶还没做完
```

用户：

```text
你干嘛呢
```

Bot：

```text
我还在折腾那个木屋，屋顶怎么盖都怪怪的……
```

而不是：

```text
我在玩 Minecraft。
```

---

## 场景 F：低信息聊天

用户：

```text
哈哈哈哈
```

Bot 可以：

```text
草
```

也可以：

```text
哈哈
```

也可以：

```text
[笑疯表情]
```

而不是必须：

```text
“哈哈哈哈，看来你也觉得这件事情很有趣。”
```

---

## 场景 G：话题疲劳

长时间讨论：

```text
Minecraft
```

角色应该能够自然：

```text
继续深入
```

或者：

```text
自然转换话题
```

而不是：

```text
不断重复提问。
```

---

## 场景 H：主动聊天

角色正在：

```text
Minecraft
```

突然发现：

```text
一个很好看的建筑
```

可以产生：

```text
“我刚发现一种屋顶搭法，感觉还挺好看的”
```

前提：

```text
自然
相关
当前有必要
```

而不是：

```text
每隔 X 小时主动说话。
```

---

# v1.2 §179 一百七十九、最终验收标准

v1.2 不以“代码量”作为完成标准。

以以下结果作为硬验收：

### 1. 连续消息

同一用户快速连续发送：

```text
不会造成多个机械回复。
```

### 2. 过期回复

用户打断后：

```text
旧回复不会继续发送。
```

### 3. 上下文连续

追问：

```text
不需要重复解释。
```

### 4. 共同经历

相关话题：

```text
能自然召回 Shared Experience。
```

### 5. 世界连续

Activity：

```text
有具体 detail。
```

### 6. 短期状态连续

角色：

```text
会记得自己刚刚在聊什么 / 做什么 / 对什么感兴趣。
```

### 7. 用户习惯连续

用户：

```text
聊天习惯会逐渐被观察到。
```

### 8. Topic 连续

话题：

```text
不会频繁无理由跳转。
```

### 9. 情绪连续

情绪：

```text
不会每条消息重新归零。
```

### 10. Expression 连续

表情：

```text
不是固定模板轮换。
```

### 11. Group Social

群聊：

```text
不是“有 @ 才说话”。
```

### 12. Agent

Agent：

```text
只是解决任务，
不会接管人格。
```

### 13. World

World：

```text
不会因为一次聊天随机改变主活动。
```

### 14. Memory

Memory：

```text
不会被短期聊天垃圾污染。
```

### 15. 性能

普通文本：

```text
不会因为 v1.2 增加大量额外模型调用。
```

---

# v1.2 §180 一百八十、开发顺序

严格按照以下顺序：

```text
1. 阅读当前完整源码
2. 绘制真实 Message Flow
3. 绘制真实 Response Flow
4. 绘制 State Mutation Flow
5. Conversation Turn Runtime
6. Message Buffer
7. Debounce
8. Turn Classification
9. Response Generation ID
10. Stale Response Detection
11. Response Cancellation
12. Follow-up Folding
13. Character Continuity State
14. Continuity Persistence
15. Continuity Update
16. Continuity Decay
17. Open Loop
18. Shared Experience
19. Interaction Profile
20. Interaction Pattern Learning
21. Affective Context
22. Topic Momentum / Fatigue
23. Conversation Repair
24. Context Fusion
25. Response Decision
26. Response Sequence
27. Timing Integration
28. World Detail / Micro Event Integration
29. Social Cognition Integration
30. Media Integration
31. Sticker Integration
32. Agent Integration
33. Memory Integration
34. Relationship Integration
35. Initiative Integration
36. WebUI Conversation Runtime
37. WebUI Continuity Inspector
38. WebUI Context Inspector
39. WebUI Replay
40. Database Migration
41. Unit Tests
42. Integration Tests
43. Human Conversation Regression
44. NapCat Integration Test
45. 真实 QQ 私聊测试
46. 真实 QQ 群聊测试
47. 性能测试
48. Regression
```

---

# v1.2 §181 一百八十一、最小侵入原则

当前已经稳定的模块：

```text
Tool Runtime
Agent Runtime
Memory Engine
World Runtime
Social Cognition
Media Runtime
```

不要推倒重写。

优先方式：

```text
Adapter
Context Provider
Coordinator
Compatibility Layer
```

例如：

```text
Social Cognition
↓
Conversation Turn Adapter
```

而不是：

```text
重写 Social Cognition
```

---

# v1.2 §182 一百八十二、代码质量要求

所有新模块：

必须：

```text
typed
async-safe
testable
observable
configurable
```

不得：

```text
global mutable state
hidden singleton state
random side effects
直接依赖 QQ SDK
直接依赖模型供应商
```

---

# v1.2 §183 一百八十三、职责边界

必须保持：

```text
Persona
= 我是谁

Memory
= 我记得什么

Relationship
= 我与你是什么关系

InteractionProfile
= 我知道你平时怎么和我聊天

World
= 我现在生活在做什么

OpenLoop
= 我最近还有什么没完成

SharedExperience
= 我们共同经历过什么

Social Cognition
= 我现在是否应该参与

Continuity
= 我最近一直处于什么状态

Conversation Runtime
= 我们现在这一轮交流是怎么发展的

Response Planner
= 我要怎样把回复表现出来

Media / Expression
= 我要不要用视觉表达
```

任何模块不得承担其他模块的全部职责。

---

# v1.2 §184 一百八十四、最终产品哲学

不要把 v1.2 做成：

```text
“更聪明的 ChatGPT QQ Bot”
```

而要做成：

```text
“一个持续存在于网络中的角色”
```

它应该有：

```text
过去
现在
正在做的事情
未完成的事情
近期兴趣
共同经历
关系变化
聊天习惯
表达习惯
短期情绪
持续话题
偶尔的想法
```

同时：

```text
这些状态不应该全部被用户看见。
```

用户只看到：

```text
自然聊天
```

---

# v1.2 §185 一百八十五、最终目标

最终的 CatooBot 应该逐渐从：

```text
Message
↓
AI Response
```

变成：

```text
                       ┌───────────────┐
                       │  Persistent   │
                       │   Character   │
                       └───────┬───────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
       World                Memory              Relationship
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               │
                      Character Continuity
                               │
                      Conversation Runtime
                               │
                 ┌─────────────┼─────────────┐
                 │             │             │
              Social        Media          Agent
                 │             │             │
                 └─────────────┼─────────────┘
                               │
                       Character Decision
                               │
                 ┌─────────────┼─────────────┐
                 │             │             │
              Content       Expression      Timing
                 │             │             │
                 └─────────────┼─────────────┘
                               │
                       Response Sequence
                               │
                               ▼
                              QQ
                               │
                               ▼
                     Continuity Update
                               │
                ┌──────────────┼──────────────┐
                │              │              │
             Open Loops   Shared Events   Interaction
                                           Profile
```

最终用户不应该感觉：

> “这是一个 AI 每次看到我的消息后重新生成一次答案。”

而应该逐渐感觉：

> **“这是同一个人。她刚才就在做那件事，刚刚和我聊到这里，她记得我们以前那个梗，她知道我通常怎么说话，而且她这次的反应和前几次是连续的。”**

---

# v1.2 §186 一百八十六、开发完成后的最终报告

完成后不要只说：

```text
v1.2 完成。
```

必须提供：

## Architecture Changes

新增与修改了哪些模块。

## Conversation Flow

完整说明：

```text
Message
→ Turn
→ Decision
→ Context
→ Response
→ Continuity
```

## Continuity Flow

说明：

```text
什么进入 Continuity
为什么进入
什么时候衰减
什么时候删除
```

## Open Loop

说明创建、更新、完成机制。

## Shared Experience

说明如何提取、召回、合并。

## Interaction Profile

说明如何学习与衰减。

## Context Fusion

说明最终 Prompt 使用哪些上下文。

## Response Sequence

说明文字、停顿、Sticker、连续消息如何组合。

## Social Compatibility

说明 v0.9 如何兼容。

## World Compatibility

说明 v1.0 如何兼容。

## Media Compatibility

说明 v1.1 如何兼容。

## Agent Compatibility

说明 v0.7 如何兼容。

## Database Migration

完整列出新表、新字段及兼容策略。

## Test Results

必须列出：

```text
Unit Tests
Integration Tests
Conversation Tests
Continuity Tests
Social Tests
World Tests
Media Tests
Agent Tests
Regression Tests
NapCat Tests
```

## Known Limitations

明确列出当前仍然存在的不足。

---

# v1.2 §187 一百八十七、最终硬性要求

1. 首先阅读当前真实源码。
2. 不假设旧版架构仍然存在。
3. 不重写已经稳定的模块。
4. 不把所有逻辑塞进 Character Runtime。
5. 不通过 Prompt 堆砌拟人度。
6. 不使用随机数直接决定核心社交行为。
7. 不保存隐藏 Chain-of-Thought。
8. 不自动把所有消息写入 Memory。
9. 不自动把所有事件写入 Continuity。
10. 不把 World Activity 当成 Character Personality。
11. 不把 Relationship 当成 User Profile。
12. 不把 Interaction Profile 当成固定标签。
13. 不让 Agent 接管最终语言表达。
14. 不让 Social Cognition 与 Character Prompt 互相冲突。
15. 不让普通图片进入 StickerSelector。
16. 不让旧回复在用户打断后发送。
17. 不让多条连续用户消息产生机械重复回复。
18. 不让同一话题无限重复。
19. 不让每条消息都追问。
20. 不让每条消息都发完整句子。
21. 不让角色每分钟随机改变状态。
22. 不让 World 因为聊天而无理由跳活动。
23. 不让 Continuity 无限增长。
24. 不增加 QQ 管理命令。
25. 所有后台调试能力只存在于 WebUI。
26. 所有新增能力必须可配置。
27. 所有新增能力必须可以关闭。
28. 所有新增状态必须可追踪。
29. 所有状态变化必须有结构化原因。
30. v1.2 的目标不是“更像 AI”，而是“更像一个持续存在的角色”。

---

# v1.2 §188 一百八十八、正式开始

现在开始：

**第一步：完整阅读当前 CatooBot 仓库源码。**

不要马上写代码。

先建立：

```text
Current Architecture Map
Current Message Flow
Current Conversation Flow
Current Context Flow
Current State Flow
Current Response Flow
Current World Flow
Current Social Flow
Current Media Flow
Current Agent Flow
```

然后找出：

```text
哪些地方是独立模块
哪些地方是重复逻辑
哪些地方上下文断裂
哪些地方状态没有连续传递
哪些地方会产生过期回复
哪些地方会产生机械化对话
```

完成分析后，再按照：

```text
Conversation Turn
→ Character Continuity
→ Shared Experience
→ Interaction Profile
→ Context Fusion
→ Human Response Runtime
```

逐步实现。

最终不要追求：

> “每一次回复都像真人。”

而要追求：

> **“连续聊很久以后，它仍然像是同一个人。”**