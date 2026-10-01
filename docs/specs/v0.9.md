# CatooBot v0.9
# Social Cognition Engine
# 社交认知引擎 + 群聊自主观察 + 上下文续话 + 智能插话

---

# v0.9 §1 一、前置条件

CatooBot v0.8 已经基本完成，并且已经拥有完整的：

- Persona
- Character Identity
- Memory
- Semantic Memory
- Relationship
- Behavior Engine
- Initiative Engine
- Character State
- Activity
- Topic
- Persistent World
- World Timeline
- Persistent Goals
- Agent Runtime
- Tool Runtime
- Response Planner
- Session
- WebUI
- OneBot
- NapCat

当前最大问题不是功能不足，而是：

> **群聊中的“自主参与行为”仍然过度依赖概率。**

当前可能存在类似：

```text
should_reply_probability = 0.2
```

或者：

```text
random() < probability
```

这类逻辑。

这不是本版本希望继续发展的方向。

---

# v0.9 §2 二、v0.9 核心目标

将：

```text
群聊参与
=
概率随机
```

升级为：

```text
群聊参与
=
社交上下文理解
+
角色状态
+
当前话题
+
与角色的相关性
+
与角色上一次发言的连续性
+
与群成员关系
+
当前行为状态
+
参与价值判断
+
硬性行为限制
```

即：

```text
Observe
↓
Understand
↓
Evaluate Relevance
↓
Evaluate Social Context
↓
Decide
↓
Reply / Stay Silent
```

---

# v0.9 §3 三、最重要的产品效果

必须首先实现以下场景。

## 场景 A：直接对话延续

群里：

```text
User A：
@CatooBot 在干嘛呢

CatooBot：
在看剧。

User A：
看的什么剧
```

第二句话：

**没有 @CatooBot。**

CatooBot 依然应该识别：

```text
这是对我上一句话的直接追问
```

然后回复：

```text
《XXX》
```

---

# v0.9 §4 四、为什么这一点不能靠概率

不要：

```text
用户说：
看的什么剧

if random() < 0.3:
    回复
else:
    不回复
```

应该：

```text
当前消息
↓
检测 Conversation Continuation
↓
发现：
上一条 Bot Message =
“在看剧”

当前：
“看的什么剧”

语义相关
+
指代明确
+
时间距离短
+
同一群聊
+
同一用户
↓
High Confidence Follow-up
↓
回复
```

---

# v0.9 §5 五、v0.9 要建立三个核心层

```text
Group Conversation Monitor
        ↓
Social Cognition Engine
        ↓
Group Participation Policy
```

---

# v0.9 §6 六、总体架构

最终：

```text
                         Group Message
                               │
                               ▼
                    Group Conversation Monitor
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
             Direct Address       Normal Message
             @ / Name / Reply           │
                    │                   ▼
                    │          Conversation Buffer
                    │                   │
                    │         ┌─────────┴─────────┐
                    │         ▼                   ▼
                    │   Follow-up Detector   5-Message
                    │                       Observer Trigger
                    │
                    └──────────────┬──────────────┘
                                   ▼
                         Social Cognition Engine
                                   │
          ┌────────────────────────┼─────────────────────────┐
          ▼                        ▼                         ▼
   Conversation Thread       Character State          Social Context
          │                        │                         │
          ▼                        ▼                         ▼
       Topic                     Activity                 Members
          │                        │                         │
          └────────────────────────┼─────────────────────────┘
                                   ▼
                         Relevance Evaluation
                                   │
                                   ▼
                         Participation Decision
                         ┌─────────┼─────────┐
                         ▼         ▼         ▼
                       REPLY    OBSERVE     IGNORE
                         │
                         ▼
                  Response Generation
                         │
                         ▼
                  Response Planner
                         │
                         ▼
                         QQ
```

---

# v0.9 §7 七、不要推翻现有 Behavior Engine

当前已有：

```text
Behavior Engine
Initiative Engine
Character State
Topic
Memory
Relationship
```

必须复用。

Social Cognition Engine 是：

> 在这些能力之上增加“社交判断”。

不是重新创建一套：

```text
Persona
State
Memory
Behavior
```

---

# v0.9 §8 八、建立 Group Conversation Monitor

新增：

```text
app/social/
├─ __init__.py
├─ monitor.py
├─ cognition.py
├─ continuation.py
├─ observer.py
├─ participation.py
├─ context.py
├─ thread.py
├─ relevance.py
├─ attention.py
├─ models.py
└─ policy.py
```

如果现有项目已经存在：

```text
group behavior
conversation
topic
```

优先升级现有模块。

不要机械创建重复系统。

---

# v0.9 §9 九、Conversation Buffer

每个群维护一个短期：

```text
GroupConversationBuffer
```

记录最近消息。

至少：

```text
message_id
user_id
nickname
timestamp
content
segments
reply_to
is_bot_message
```

---

# v0.9 §10 十、Buffer 不应该无限增长

默认：

```yaml
social:
  group_context:
    max_messages: 30
```

只保留近期消息。

更早的内容：

通过现有：

```text
Topic
Memory
```

提供。

---

# v0.9 §11 十一、特别记录 Bot 自己的消息

Group Buffer 必须清楚记录：

```text
is_bot_message = true
```

因为：

```text
Bot 上一条消息
+
下一条用户消息
```

是判断自然续话的关键。

---

# v0.9 §12 十二、Active Conversation Thread

建立：

```text
ConversationThread
```

至少：

```text
thread_id
group_id
participants
topic
last_message_id
last_bot_message_id
status
created_at
updated_at
expires_at
```

状态：

```text
active
waiting
idle
closed
```

---

# v0.9 §13 十三、Bot 主动参与后开启 Conversation Window

例如：

```text
User：
@CatooBot 在干嘛？

Bot：
在看剧。

```

发送后：

```text
active_thread = true
```

并设置：

```text
follow_up_window
```

例如：

```yaml
social:
  continuation:
    enabled: true
    window_minutes: 10
    max_messages: 8
```

---

# v0.9 §14 十四、Continuation Detector

当前一条新消息到达：

如果：

```text
active_thread = true
```

必须优先判断：

> 这句话是不是在继续和 CatooBot 说话？

---

# v0.9 §15 十五、Continuation Detector 的判断因素

至少考虑：

```text
1. 与 Bot 上一条消息的语义相关性
2. 与 Bot 上一条消息之间的时间距离
3. 是否由之前对话的用户发送
4. 是否存在代词/省略
5. 当前话题是否连续
6. 是否存在“那、呢、然后、什么、哪个、为什么、怎么”等跟问结构
7. 当前消息是否明显依赖 Bot 上一句话才能理解
```

例如：

```text
Bot：
我在看剧。

User：
看的什么剧？
```

即使没有：

```text
@Bot
```

也应该识别为：

```text
direct_follow_up
```

---

# v0.9 §16 十六、Continuation Detector 不能只使用关键词

禁止：

```python
if "什么" in message:
    follow_up = True
```

必须结合：

```text
Conversation Context
+
Semantic Similarity
+
Dialogue Structure
+
Previous Bot Message
```

---

# v0.9 §17 十七、Continuation Detector 最好使用轻量模型

如果当前 Model Router 支持：

增加一个：

```text
Social Decision Model
```

例如：

```yaml
social:
  decision_model: ""
```

为空时：

```text
使用默认快速模型
```

不要默认调用最高成本的主模型。

---

# v0.9 §18 十八、Continuation Decision 输出必须结构化

例如：

```json
{
  "is_follow_up": true,
  "confidence": 0.94,
  "target_message_id": "123456",
  "reason_code": "direct_topic_continuation"
}
```

`reason_code` 只能是：

```text
direct_follow_up
reply_to_bot
topic_continuation
implicit_question
ambiguous
unrelated
```

禁止输出隐藏 Chain of Thought。

---

# v0.9 §19 十九、Continuation Confidence 只是决策依据

这里允许：

```text
confidence
```

但：

**不能再用随机概率：**

```text
random() < confidence
```

错误：

```text
confidence = 0.94
↓
94% 回复
```

正确：

```text
confidence = 0.94
↓
超过 continuation threshold
↓
确定属于对话延续
↓
进入回复流程
```

---

# v0.9 §20 二十、直接 @CatooBot

如果：

```text
@CatooBot
```

默认：

```text
High Priority
```

直接进入：

```text
Response Pipeline
```

不再经过普通群聊参与概率。

但仍然要经过：

```text
Safety
Permission
Message Validity
```

---

# v0.9 §21 二十一、直接回复 Bot 消息

如果 OneBot 消息中：

```text
reply_to = bot_message
```

应该视作：

```text
High Priority
```

即使没有：

```text
@Bot
```

也应进入回复流程。

---

# v0.9 §22 二十二、Bot 名称/别名

支持：

```text
character.name
character.nicknames
character.aliases
```

例如：

```text
Catoo
Cat
小猫
```

用户：

```text
小猫你在干嘛？
```

也可以识别为：

```text
direct_address
```

---

# v0.9 §23 二十三、不要误判普通“你”

普通消息：

```text
A：
你们今天吃什么？
```

不能因为：

```text
“你”
```

就认为是在叫 CatooBot。

需要：

```text
Conversation Context
+
当前线程
+
上下文指向
```

共同判断。

---

# v0.9 §24 二十四、5-Message Observer

这是本版本第二个核心。

当：

```text
群聊消息没有明确提及 Bot
```

并且：

```text
不属于 Active Conversation
```

则进入：

```text
Observation Buffer
```

---

# v0.9 §25 二十五、每 5 条消息进行一次认知观察

例如：

```text
Message 1
Message 2
Message 3
Message 4
Message 5
```

达到：

```text
5 messages
```

触发：

```text
Group Observer
```

注意：

**只统计其他用户消息。**

CatooBot 自己发送的消息：

```text
不计入 5 条。
```

---

# v0.9 §26 二十六、每次 Observer 使用什么上下文

不要只把刚刚 5 条消息丢进去。

应该：

```text
Trigger Batch:
最近新增 5 条

+
Recent Group Context:
最近 20~30 条

+
Current Character State

+
Current Activity

+
Current Focus

+
Current Topics

+
Relevant Memory

+
Relationship Summary

+
Group Participation State
```

---

# v0.9 §27 二十七、Group Observer 的核心问题

模型不要被问：

> “你想不想回复？”

这种问法太容易变成随机。

应该：

```text
请判断当前群聊是否出现了值得角色自然参与的内容。

判断：
1. 当前话题是什么？
2. 是否与角色当前状态相关？
3. 是否与角色兴趣相关？
4. 是否与角色长期记忆相关？
5. 是否存在角色能够自然贡献的信息？
6. 如果参与，是否会显得突兀？
7. 当前是否已经有人充分讨论？
8. 当前角色参与是否会打断其他人的对话？
9. 当前消息是否值得角色加入？
```

最终：

```text
REPLY
or
IGNORE
```

---

# v0.9 §28 二十八、Observer 必须看到 Character State

例如：

```text
activity:
watching_tv

activity_detail:
正在看《XXX》

mood:
relaxed

focus:
当前正在看电视剧

interest_topics:
影视、音乐、Minecraft
```

群里出现：

```text
A：
最近《XXX》好不好看？
```

Observer 应该得到：

```text
relevance = high
```

---

# v0.9 §29 二十九、Activity 必须增加“可分享上下文”

v0.8 的 Activity 如果只有：

```text
watching_tv
```

是不够的。

需要：

```text
ActivityContext
```

至少：

```text
activity_type
activity_name
activity_detail
current_focus
topic_tags
started_at
shareability
```

例如：

```json
{
  "activity_type": "watching_tv",
  "activity_name": "watching_series",
  "activity_detail": "正在看《XXX》第三集",
  "current_focus": "剧情",
  "topic_tags": ["电视剧", "悬疑"],
  "shareability": 0.8
}
```

这样用户问：

```text
看的什么剧？
```

CatooBot 才有足够的信息自然回答。

---

# v0.9 §30 三十、Character State Context Builder

增加：

```text
SocialCharacterContext
```

专门给 Social Cognition 使用。

不要把整个 Character Runtime 全部塞给模型。

结构：

```text
Current State
Current Activity
Current Focus
Mood
Current Topic
Interests
Relevant Memory
Relationship Summary
```

---

# v0.9 §31 三十一、不要把无关 Memory 塞入群聊 Observer

例如角色记忆：

```text
用户 A 喜欢什么音乐
用户 B 的生日
过去三个月的所有聊天
```

不相关的都不要进入。

必须先通过：

```text
Memory Retrieval
```

得到相关信息。

---

# v0.9 §32 三十二、Group Context 的用户关系

Observer 应知道：

```text
当前群有哪些参与者
谁正在说话
Bot 与当前发言者的关系
```

但只提供：

```text
必要的非敏感摘要。
```

例如：

```text
User A:
relationship = familiar
```

不要给模型：

```text
完整私人 Memory
```

除非当前明确需要。

---

# v0.9 §33 三十三、Social Relevance

建立：

```text
RelevanceEvaluator
```

输出：

```text
topic_relevance
character_relevance
conversation_relevance
social_fit
contribution_value
```

---

# v0.9 §34 三十四、这些不是“随机概率”

这是：

> 对当前对话状态的结构化评价。

最终可以得到：

```json
{
  "topic_relevance": 0.91,
  "character_relevance": 0.88,
  "conversation_relevance": 0.94,
  "social_fit": 0.81,
  "contribution_value": 0.87
}
```

这些数字只用于：

```text
规则判断 / 排序
```

**不能再拿来做 `random() < score`。**

---

# v0.9 §35 三十五、Participation Decision

增加：

```text
ParticipationDecision
```

输出：

```text
reply
observe
ignore
defer
```

---

# v0.9 §36 三十六、四种行为

### reply

现在参与。

### observe

继续观察，不回复。

### ignore

当前内容与角色无关。

### defer

话题有一定价值，但当前插入不自然，等待下一条。

---

# v0.9 §37 三十七、为什么需要 defer

例如：

```text
A：
你们吃什么？

B：
不知道。

C：
随便吧。
```

角色其实对“吃什么”有兴趣。

但：

```text
当前群聊已经快结束
```

立即插话可能很奇怪。

Observer 可以：

```text
defer
```

下一条消息如果重新出现相关话题，再参与。

---

# v0.9 §38 三十八、参与理由

Decision 内部需要：

```text
reason_code
```

例如：

```text
direct_follow_up
topic_interest
current_activity_relevance
memory_relevance
user_relationship
helpful_contribution
humor_opportunity
shared_interest
```

或者：

```text
no_relevance
already_discussed
conversation_too_fast
low_contribution_value
poor_timing
```

---

# v0.9 §39 三十九、禁止模型输出隐藏思维链

不要要求：

```text
请详细分析你为什么想回复。
```

只需要：

```json
{
  "decision": "reply",
  "reason_code": "current_activity_relevance",
  "confidence": 0.91,
  "response_goal": "自然加入当前电视剧话题"
}
```

`response_goal` 必须简短，不得记录隐藏推理。

---

# v0.9 §40 四十、回复生成和“是否参与”分开

这是关键。

第一步：

```text
Social Cognition
↓
应该回复吗？
```

第二步：

```text
Character AI
↓
应该怎么回复？
```

不能：

```text
一个 Prompt
↓
既判断是否回复
又生成最终回复
```

否则模型很容易为了完成任务而“强行生成一句话”。

---

# v0.9 §41 四十一、当 Decision = reply

流程：

```text
Social Cognition
↓
ParticipationDecision = reply
↓
构造 Conversation Context
↓
加入相关 Memory
↓
加入 Character State
↓
AI Response
↓
Response Planner
↓
Send
```

---

# v0.9 §42 四十二、当 Decision = ignore

完全不调用：

```text
主回答模型
```

直接：

```text
silence
```

这样可以节省模型成本。

---

# v0.9 §43 四十三、当 Decision = observe

记录：

```text
Observation
```

但不回复。

---

# v0.9 §44 四十四、当 Decision = defer

保存：

```text
pending_social_topic
```

继续观察。

如果后续继续：

```text
topic
```

则重新评估。

---

# v0.9 §45 四十五、5-message Observer 不应该每次都调用主模型

建议：

```text
Social Decision Model
```

使用：

```text
快速 / 低成本模型
```

只有：

```text
decision = reply
```

时才调用主模型生成自然回复。

---

# v0.9 §46 四十六、成本优化

普通群聊：

```text
Message 1 → 不调用 LLM
Message 2 → 不调用 LLM
Message 3 → 不调用 LLM
Message 4 → 不调用 LLM
Message 5 → Social Observer
```

如果：

```text
ignore
```

结束。

如果：

```text
reply
```

再调用：

```text
Main AI
```

---

# v0.9 §47 四十七、但是 Active Conversation 特殊处理

例如：

```text
Bot：
我在看剧。

User：
看的什么？
```

不能等到：

```text
第 5 条消息
```

才判断。

必须：

```text
Active Conversation
↓
Immediate Follow-up Detection
```

优先级高于 5-message Observer。

---

# v0.9 §48 四十八、Active Conversation Window

配置：

```yaml
social:
  continuation:
    enabled: true
    window_minutes: 10
    max_messages: 8
```

如果窗口过期：

```text
active_thread = false
```

恢复为：

```text
5-message observer
```

---

# v0.9 §49 四十九、Active Conversation 不应该无限持续

如果：

```text
用户连续和 Bot 聊 30 条
```

也必须存在：

```text
max_messages
```

之后重新评估线程。

但自然连续对话应该优先保持。

---

# v0.9 §50 五十、Thread Topic

Active Thread 中：

需要记录：

```text
topic
subtopic
participants
last_bot_message
last_user_message
```

---

# v0.9 §51 五十一、指代消解

支持：

```text
Bot：
我在看《XXX》。

User：
好看吗？
```

模型应该理解：

```text
“它” / “这个” / “那部” / “好看吗”
```

是指：

```text
《XXX》
```

而不是要求用户重新说明。

---

# v0.9 §52 五十二、上下文不足时不要强行回复

如果：

```text
User：
那你呢？
```

当前没有可靠上下文。

可以：

```text
自然询问
```

例如：

```text
你说的是哪件事呀？
```

而不是：

```text
猜一个。
```

---

# v0.9 §53 五十三、群聊上下文窗口

默认：

```text
recent_messages = 20~30
```

但是：

```text
Thread Context
+
Topic Context
```

可以额外提供。

---

# v0.9 §54 五十四、消息分组

Observer 应该看到：

```text
User A：
......

User B：
......

User C：
......
```

而不是只有：

```text
A text
B text
C text
```

保留发言者身份。

---

# v0.9 §55 五十五、发言者关系

可以给：

```text
sender_relationship
```

例如：

```text
User A:
familiar

User B:
new

User C:
close
```

这样：

```text
用户熟悉程度
```

可以影响社会参与。

但不能让关系成为：

```text
“关系差就不理”
```

这种单一规则。

---

# v0.9 §56 五十六、不要只关注群里最后一个人

Observer 应该理解：

```text
最近 20~30 条
```

形成：

```text
conversation topic
```

而不是：

```text
只分析最后一句。
```

---

# v0.9 §57 五十七、Group Topic Detection

增加：

```text
GroupTopicDetector
```

识别：

```text
current_topic
topic_transition
topic_intensity
```

例如：

```text
之前：
电影

现在：
工作

即将：
游戏
```

---

# v0.9 §58 五十八、Topic Transition

如果当前 Topic 已经变化：

旧：

```text
电视剧
```

新：

```text
工作
```

那么：

即使角色喜欢电视剧，也不应该突然回来插电视剧。

---

# v0.9 §59 五十九、Topic Relevance

需要：

```text
current_topic
+
character_interests
+
current_activity
+
current_goals
+
memory
```

一起判断。

---

# v0.9 §60 六十、角色兴趣不是永远一样

可以使用：

```text
Persona Interests
+
Current Focus
+
Recent Topics
```

Current Focus 优先级高于静态 Interest。

例如：

```text
角色平常喜欢音乐
```

但现在：

```text
正在看剧
```

当前电视剧话题：

优先级可能高于音乐。

---

# v0.9 §61 六十一、Character Focus

增加：

```text
focus_score
```

例如：

```json
{
  "topic": "电视剧",
  "focus": 0.9
}
```

---

# v0.9 §62 六十二、Social Attention

增加：

```text
SocialAttentionState
```

例如：

```text
attention_level
current_topic
target_users
fatigue
```

这些不是用户心理。

仅表示：

> 角色当前对群聊的“注意程度”。

---

# v0.9 §63 六十三、Social Attention 来源

```text
recent_mentions
active_thread
interesting_topic
current_activity
conversation_intensity
time_of_day
```

---

# v0.9 §64 六十四、不要每条消息都改变 Attention

应该有：

```text
momentum
decay
```

例如：

```text
正在参与电视剧讨论
↓
短时间内继续关注电视剧
```

话题切换：

```text
电视剧
↓
工作
```

才逐渐降低。

---

# v0.9 §65 六十五、Social Fatigue

增加：

```text
social_fatigue
```

代表：

> 角色当前是否已经参与了很多群聊。

不是用户心理。

它只影响：

```text
参与倾向
```

而不是：

```text
强制禁止
```

---

# v0.9 §66 六十六、Fatigue 应该缓慢恢复

例如：

```text
连续 5 次参与
↓
fatigue ↑
```

一段时间没参与：

```text
fatigue ↓
```

避免：

```text
第一条就累
```

---

# v0.9 §67 六十七、硬限制和认知决策分离

最终：

```text
Social Cognition
=
软判断

Policy
=
硬限制
```

例如：

```text
模型认为：
应该回复

但：
daily_limit 达到

结果：
不能回复
```

---

# v0.9 §68 六十八、Participation Policy

统一判断：

```text
if direct_follow_up:
    reply

elif direct_mention:
    reply

elif cooldown_block:
    ignore

elif daily_limit_block:
    ignore

else:
    use_social_cognition()
```

---

# v0.9 §69 六十九、不是所有高相关话题都要回复

即使：

```text
topic_relevance = 0.95
```

还应该考虑：

```text
social_fit
contribution_value
conversation_timing
```

比如：

```text
两个人正在快速讨论一个私人问题
```

角色很感兴趣：

仍然可能：

```text
observe
```

而不是突然插话。

---

# v0.9 §70 七十、Contribution Value

模型需要判断：

> “如果 CatooBot 现在加入，能不能提供一点真实内容？”

例如：

```text
已有：
A：今晚吃火锅
B：我已经吃过了
```

CatooBot 没有新信息：

```text
贡献价值低
```

应该：

```text
ignore
```

而如果角色：

```text
正在讨论一家角色知道的餐厅
```

可以：

```text
reply
```

---

# v0.9 §71 七十一、避免“我也……”

重点增加：

```text
generic_reply_penalty
```

例如模型准备：

```text
我也觉得
哈哈确实
是啊
我也是
```

如果没有真正新增内容：

降低参与价值。

不要让角色为了参与而参与。

---

# v0.9 §72 七十二、信息贡献优先

参与优先级：

```text
相关且有信息
>
相关且有独特观点
>
相关且有共同经历
>
自然情绪回应
>
纯附和
```

---

# v0.9 §73 七十三、幽默参与

允许：

```text
humor_opportunity
```

但需要：

```text
social_fit
```

足够高。

不能：

```text
任何话题都开玩笑。
```

---

# v0.9 §74 七十四、角色当前活动可以成为参与来源

例如：

```text
activity:
watching_show

group:
A：
最近有什么好看的剧？
```

认知层：

```text
character_relevance ↑
```

此时更容易：

```text
reply
```

---

# v0.9 §75 七十五、角色当前活动可以直接回答事实型追问

例如：

```text
Bot：
在看剧。

User：
看的什么剧？
```

无需重新搜索 Memory。

直接使用：

```text
ActivityContext
```

---

# v0.9 §76 七十六、Activity Context 不应过时

如果：

```text
activity_started_at = 3 hours ago
```

角色已经切换活动：

不要继续回答：

```text
“我还在看剧。”
```

World Runtime 必须保证状态最新。

---

# v0.9 §77 七十七、Group Observer Prompt

设计独立 Prompt：

```text
你正在帮助一个虚拟角色判断自己是否应该自然加入当前群聊。

你不是在决定“模型是否要生成回复”，
而是在判断“这个角色此时是否有自然参与这段对话的理由”。

请综合：
- 最近群聊内容
- 对话主题
- 角色当前活动
- 角色当前关注点
- 角色兴趣
- 角色与参与者的关系
- 相关长期记忆
- 当前 Conversation Thread
- 当前聊天节奏

判断：

1. 当前是否已经在与角色对话？
2. 当前话题是否与角色有关？
3. 角色能否贡献有价值的内容？
4. 现在插话是否自然？
5. 是否应该继续观察？

只输出结构化 JSON。
不要输出思维过程。
```

---

# v0.9 §78 七十八、Observer 输出格式

统一：

```json
{
  "decision": "reply",
  "reason_code": "current_activity_relevance",
  "confidence": 0.92,
  "topic": "电视剧",
  "response_goal": "自然参与电视剧讨论并分享当前正在看的剧",
  "target_message_ids": ["123", "124"]
}
```

允许：

```text
reply
observe
ignore
defer
```

---

# v0.9 §79 七十九、Conversation Continuation 输出

```json
{
  "is_follow_up": true,
  "confidence": 0.96,
  "target_message_ids": ["125"],
  "reason_code": "direct_topic_continuation"
}
```

---

# v0.9 §80 八十、Social Decision Model 不负责最终回复

职责：

```text
是否参与
```

主模型：

```text
怎么说
```

必须分离。

---

# v0.9 §81 八十一、社会观察频率

默认：

```text
每 5 条外部群消息
```

执行一次：

```text
Group Observer
```

但以下情况立即触发：

```text
@Bot
Reply to Bot
Active Conversation Follow-up
明确称呼 Bot
高置信度 Continuation
```

---

# v0.9 §82 八十二、5 消息计数规则

只计算：

```text
其他用户消息
```

不计算：

```text
Bot 自己消息
系统事件
Bot 工具消息
WebUI
```

---

# v0.9 §83 八十三、Observer Batch Reset

触发一次 Observer 后：

```text
unobserved_count = 0
```

然后重新累计。

---

# v0.9 §84 八十四、不要因为 Observer 说 ignore 就立即完全忘记这 5 条

这些消息仍然保存在：

```text
Group Conversation Buffer
```

只是：

```text
Observer 已处理
```

标记为：

```text
observed
```

---

# v0.9 §85 八十五、Observation Record

增加：

```text
SocialObservation
```

保存：

```text
observation_id
group_id
message_range
topic
decision
reason_code
confidence
created_at
```

用于：

```text
WebUI Debug
```

不是 Memory。

---

# v0.9 §86 八十六、WebUI 社交认知页面

新增：

```text
/social
```

Dashboard：

```text
Messages Observed
Observer Calls
Follow-up Calls
Reply Decisions
Ignore Decisions
Defer Decisions
Participation Rate
False Positive Rate
```

---

# v0.9 §87 八十七、Social Observation Debugger

页面：

```text
/social/observations
```

显示：

```text
最近 30 条群聊
Character State
Current Topic
Observer Decision
Reason Code
Confidence
```

---

# v0.9 §88 八十八、Decision Replay

管理员点击：

```text
Replay
```

重新使用当前模型分析：

```text
同一段历史消息
```

比较：

```text
old decision
new decision
```

但：

**Replay 不发送 QQ 消息。**

---

# v0.9 §89 八十九、WebUI Group Context

管理员可以看到：

```text
当前群：
xxx

Current Topic：
电视剧

Current Social Attention：
0.82

Current Character Activity：
watching_show

Active Thread：
true

Unobserved Messages：
3/5
```

---

# v0.9 §90 九十、WebUI 手动触发 Observer

支持：

```text
Analyze Now
```

立即对当前群聊进行一次认知判断。

不发送 QQ。

除非管理员明确选择：

```text
Test Reply
```

---

# v0.9 §91 九十一、Social Policy 配置

新增：

```yaml
social:

  enabled: true

  decision_model: ""

  group_context:
    max_messages: 30

  continuation:
    enabled: true
    window_minutes: 10
    max_messages: 8

  observer:
    batch_size: 5
    min_context_messages: 20

  participation:
    daily_limit: 30
    cooldown_seconds: 90

  attention:
    enabled: true

  fatigue:
    enabled: true

  topic:
    enabled: true
```

这些只是默认值。

---

# v0.9 §92 九十二、旧概率系统处理

当前如果已经存在：

```text
participation_probability
```

不要直接删除。

改造成：

```text
legacy_prior
```

只能：

```text
低权重 tie-breaker
```

不得作为主要决策依据。

---

# v0.9 §93 九十三、不要再出现：

```python
if random.random() < participation_probability:
    reply()
```

这种核心逻辑。

---

# v0.9 §94 九十四、如果必须保留概率

只允许在：

```text
多个完全等价的候选行为
```

之间做极小随机扰动。

例如：

```text
两个都合理的回复时间
```

而不是：

```text
决定要不要插话
```

---

# v0.9 §95 九十五、Decision Score 不等于 Probability

可以存在：

```text
relevance_score
social_fit_score
contribution_score
```

但最终由：

```text
Decision Policy
```

决定。

例如：

```text
if relevance >= threshold
and social_fit >= threshold
and contribution >= threshold:
    reply
```

---

# v0.9 §96 九十六、阈值可配置

例如：

```yaml
social:
  thresholds:
    follow_up: 0.75
    topic_relevance: 0.70
    social_fit: 0.65
    contribution_value: 0.65
```

但不要只：

```text
sum(score) > threshold
```

建议采用：

```text
硬条件
+
多个维度
```

避免某一个分数过高掩盖其他严重问题。

---

# v0.9 §97 九十七、直接跟问优先级

推荐：

```text
Priority 1:
reply_to_bot

Priority 2:
direct_follow_up

Priority 3:
explicit_bot_name

Priority 4:
highly relevant topic

Priority 5:
interesting topic

Priority 6:
generic social participation
```

越靠后：

```text
越需要强相关性
```

---

# v0.9 §98 九十八、主动插话优先级

非 @、非 Follow-up：

只有：

```text
topic highly relevant
+
contribution useful
+
social_fit good
```

才应该回复。

普通：

```text
“我也觉得”
```

不应该成为回复理由。

---

# v0.9 §99 九十九、角色正在做什么应该真实影响判断

如果：

```text
activity:
sleeping
```

正常群聊：

即使：

```text
topic_relevance = 0.85
```

也可能：

```text
observe
```

如果：

```text
@Bot
```

则仍然：

```text
优先回复
```

除非 Persona / Behavior Policy 有明确限制。

---

# v0.9 §100 一百、角色当前 Focus

例如：

```text
focus:
watching_series
```

群里：

```text
电视剧讨论
```

则：

```text
relevance ↑
```

如果：

```text
focus:
working
```

群里：

```text
完全无关闲聊
```

则：

```text
participation ↓
```

---

# v0.9 §101 一百零一、Social Fatigue

如果角色刚刚连续参与：

```text
5 次
```

然后群里又出现普通话题：

Observer 仍然可以认为：

```text
interesting
```

但：

```text
social_fit
```

受到 fatigue 影响。

不是：

```text
强制沉默
```

---

# v0.9 §102 一百零二、避免过度抑制

不要出现：

```text
“因为 fatigue，所以永远不回复。”
```

直接 @：

仍然优先。

真正重要的兴趣话题：

仍然可以参与。

---

# v0.9 §103 一百零三、群聊参与后的 Thread Tracking

Bot 回复后：

必须：

```text
记录最后一条 Bot Message
```

并更新：

```text
active_thread
topic
participants
```

这样：

```text
看的什么剧？
```

才能继续接住。

---

# v0.9 §104 一百零四、Thread Expiration

如果：

```text
超过 10 分钟
```

没有相关后续：

```text
thread.status = waiting / closed
```

恢复：

```text
normal observer
```

---

# v0.9 §105 一百零五、同一用户连续追问

例如：

```text
Bot：
我在看剧。

A：
什么剧？

Bot：
《XXX》。

A：
好看吗？

Bot：
还挺不错。

A：
主要讲什么？
```

即使没有任何 @：

全部都应该属于：

```text
same conversation thread
```

---

# v0.9 §106 一百零六、多人加入 Bot 对话

例如：

```text
A：
@Bot 你看什么剧？

Bot：
《XXX》

B：
我也看过！

A：
好不好看？

C：
我觉得一般
```

CatooBot 应该理解：

```text
当前群聊正在围绕 Bot 的回答延续话题。
```

所以：

```text
A/B/C 的消息
```

都可能进入：

```text
Thread Context
```

而不是只跟踪最初的 A。

---

# v0.9 §107 一百零七、多人线程成员

Thread：

```text
participants:
A
B
C
Bot
```

动态更新。

---

# v0.9 §108 一百零八、Thread Context 与 Group Context

两者区别：

### Group Context

最近整个群聊。

### Thread Context

当前与 Bot 相关的局部对话。

最终：

```text
Thread Context
+
Recent Group Context
```

共同给 Social Cognition。

---

# v0.9 §109 一百零九、不要无限扩大 Thread

如果话题已经明显转移：

```text
电视剧
↓
工作
```

Thread 可以：

```text
closed
```

重新建立新 Thread。

---

# v0.9 §110 一百一十、Topic Transition Detector

复用现有 Topic Engine。

不要创建完全不同的 Topic 系统。

---

# v0.9 §111 一百一十一、Group Observer 与 Memory

Memory Retrieval 需要：

```text
current_group_topic
+
current_activity
+
current_message_batch
```

作为 Query。

而不是每 5 条消息：

```text
把所有用户 Memory 全部召回。
```

---

# v0.9 §112 一百一十二、Memory 的角色相关性

如果 Memory：

```text
用户喜欢 Minecraft
```

当前群聊：

```text
Minecraft
```

与 CatooBot：

```text
角色的共同兴趣
```

相关：

可以提高参与价值。

但：

不能因为一个 Memory 存在就强制参与。

---

# v0.9 §113 一百一十三、角色 Activity 与 Topic 的相关性

例如：

```text
Activity:
watching_series

Group Topic:
电视剧

```

则：

```text
current_activity_relevance = high
```

---

# v0.9 §114 一百一十四、Participation Reason Logging

每次 Observer：

记录：

```text
decision
reason_code
topic
activity
thread
```

但不要记录：

```text
完整隐私聊天
```

除非当前项目已有安全的 Debug 存储策略。

---

# v0.9 §115 一百一十五、数据保留

Social Observation 默认只保留有限时间。

配置：

```yaml
social:
  observation_retention_days: 30
```

过期：

```text
archive/delete
```

不要永久保存每次模型判断。

---

# v0.9 §116 一百一十六、性能

### 正常群聊

```text
消息 1~4
```

尽可能：

```text
O(1)
```

不调用 LLM。

### 第 5 条

调用：

```text
Social Decision Model
```

### Active Thread

可以单条触发：

```text
Continuation Detector
```

但使用：

```text
短上下文
低成本模型
```

---

# v0.9 §117 一百一十七、不要让 Observer 阻塞 QQ 收消息

群聊消息到来：

必须先：

```text
保存消息
↓
继续 Event Loop
```

Observer：

```text
异步后台任务
```

---

# v0.9 §118 一百一十八、并发控制

同一个群：

如果上一轮 Observer 尚未完成：

新消息继续进入 Buffer。

禁止：

```text
5 messages
→ observer A

又 5 messages
→ observer B

又 5 messages
→ observer C
```

同时运行。

应该：

```text
per-group observer lock
```

结束后：

如果期间积累了足够新消息：

再安排下一轮。

---

# v0.9 §119 一百一十九、Decision Staleness

如果 Observer 分析的是：

```text
Message 1~20
```

执行决策前群里已经新增：

```text
Message 21~30
```

必须检查：

```text
decision_stale
```

如果话题已经明显变化：

丢弃旧 Decision。

不要对过时话题插嘴。

---

# v0.9 §120 一百二十、Stale Threshold

可以配置：

```yaml
social:
  observer:
    max_staleness_messages: 5
```

超过：

```text
重新观察
```

---

# v0.9 §121 一百二十一、发送前最终检查

即使 Observer 决定：

```text
reply
```

在真正发送前，还要执行：

```text
Final Participation Gate
```

检查：

```text
topic 是否仍然存在
thread 是否仍然有效
cooldown
daily limit
character state
group settings
```

如果当前话题已经切走：

```text
取消回复
```

---

# v0.9 §122 一百二十二、Response Planner 保持不变

一旦最终决定回复：

继续使用：

```text
Response Planner
```

管理：

```text
delay
message split
style
```

Social Cognition 只决定：

```text
要不要说
```

---

# v0.9 §123 一百二十三、不要生成“为了回应而回应”的内容

如果 Decision：

```text
reply
```

但：

AI 无法生成真实贡献：

应该：

```text
cancel
```

不能强行发送：

```text
哈哈确实
```

---

# v0.9 §124 一百二十四、Final Response Quality Gate

增加：

```text
response_quality
```

在发送前检查：

```text
是否真正回应当前话题
是否和前文一致
是否与 Character State 一致
是否无意义
是否重复
是否突然转移话题
```

如果不合格：

```text
cancel
```

或者重新生成。

最多：

```text
1 次
```

---

# v0.9 §125 一百二十五、必须支持“沉默”

这是整个系统非常重要的能力。

最终结果可以是：

```text
CatooBot：
什么都不说。
```

而不是：

```text
必须生成一句。
```

---

# v0.9 §126 一百二十六、测试：用户直接续话

必须实现：

```text
A：
@Bot 在干嘛？

Bot：
在看剧。

A：
看的什么？

Bot：
《XXX》。
```

---

# v0.9 §127 一百二十七、测试：多轮续话

```text
A：
@Bot 在干嘛？

Bot：
在看剧。

A：
看的什么？

Bot：
《XXX》。

A：
好看吗？

Bot：
……
```

全部不使用 @。

仍然保持：

```text
Active Thread
```

---

# v0.9 §128 一百二十八、测试：话题转移

```text
Bot：
在看剧。

A：
看的什么？

Bot：
《XXX》。

B：
对了，明天开会几点？
```

此时：

```text
Conversation Thread
↓
电视剧
```

已经转移。

B 的：

```text
开会
```

不应该被识别为 Bot follow-up。

---

# v0.9 §129 一百二十九、测试：完全无关群聊

```text
A：
今天吃什么？

B：
火锅。

C：
烧烤。

D：
我都行。
```

CatooBot：

```text
silence
```

不应该因为：

```text
participation_probability = 30%
```

随机插话。

---

# v0.9 §130 一百三十、测试：兴趣话题

Character:

```text
interest:
Minecraft
```

Group:

```text
A：
最近有人玩 1.21 的新内容吗？

B：
我玩了一下。
```

Observer：

应该提高：

```text
character_relevance
contribution_value
```

如果确实有话可说：

```text
reply
```

---

# v0.9 §131 一百三十一、测试：当前活动

Character:

```text
activity:
watching_series
activity_detail:
正在看《XXX》
```

Group：

```text
A：
最近有没有什么好看的剧？
```

应该更容易：

```text
reply
```

---

# v0.9 §132 一百三十二、测试：当前活动完全无关

Character:

```text
activity:
sleeping
```

Group：

```text
A：
今晚玩游戏吗？
```

如果没有特别关系：

应该：

```text
observe
```

或者：

```text
ignore
```

---

# v0.9 §133 一百三十三、测试：直接 @

无论：

```text
topic relevance
activity
fatigue
```

只要：

```text
@CatooBot
```

都进入高优先级处理。

---

# v0.9 §134 一百三十四、测试：Reply Segment

用户：

```text
回复 Bot 的上一条消息
```

即使没有 @：

也应该：

```text
reply
```

---

# v0.9 §135 一百三十五、测试：5 Message Batch

群：

```text
M1
M2
M3
M4
M5
```

检查：

```text
只调用一次 Observer。
```

---

# v0.9 §136 一百三十六、测试：自己的消息不计数

```text
M1 user
M2 user
Bot
M3 user
M4 user
M5 user
```

计数应该：

```text
5 user messages
```

而不是：

```text
6 messages
```

---

# v0.9 §137 一百三十七、测试：Observer Ignore

Observer：

```text
decision = ignore
```

不要调用：

```text
Main Chat Model
```

---

# v0.9 §138 一百三十八、测试：Observer Reply

Observer：

```text
reply
```

然后：

```text
Main AI
```

生成自然回复。

---

# v0.9 §139 一百三十九、测试：Defer

Observer：

```text
defer
```

不要立即回复。

继续观察。

---

# v0.9 §140 一百四十、测试：Stale Decision

Observer：

```text
reply
```

尚未发送。

群里：

```text
5 条新消息
```

话题已经转移。

最终：

```text
Final Gate
↓
cancel
```

---

# v0.9 §141 一百四十一、测试：并发 Observer

模拟：

```text
大量群消息
```

确认：

```text
同一群最多一个 Observer。
```

---

# v0.9 §142 一百四十二、测试：成本控制

普通 20 条群聊：

应该接近：

```text
4 次 Observer
```

而不是：

```text
20 次主模型
```

---

# v0.9 §143 一百四十三、WebUI 配置

增加：

```text
Social Cognition
Group Participation
Continuation
Observer
Attention
Fatigue
```

全部可配置。

---

# v0.9 §144 一百四十四、WebUI 群聊实时状态

显示：

```text
Group
Current Topic
Active Thread
Last Bot Message
Unobserved Messages
Social Attention
Social Fatigue
Last Decision
```

---

# v0.9 §145 一百四十五、WebUI Decision Debug

管理员可以点击：

```text
Analyze
```

查看：

```text
Recent Group Messages
Character Context
Memory Context
Topic
Decision
Reason
Scores
```

---

# v0.9 §146 一百四十六、WebUI 手动测试

管理员可以：

```text
Paste group conversation
```

模拟：

```text
CatooBot 是否会参与
```

系统输出：

```text
decision
reason
relevance
social_fit
contribution_value
```

但：

不发送消息。

---

# v0.9 §147 一百四十七、旧概率系统迁移

如果现有 v0.8 中存在：

```text
participation_probability
insert_probability
random_reply_chance
```

不要马上删除数据库字段。

迁移：

```text
legacy_probability
```

只用于：

```text
observability / fallback
```

第一阶段：

```text
主决策不再使用。
```

---

# v0.9 §148 一百四十八、最终决策优先级

最终：

```text
1. Safety / System Policy
2. Direct Reply / @Bot
3. Active Conversation Continuation
4. Explicit Bot Name / Alias
5. High-Relevance Topic
6. Character Current Activity
7. Character Interest
8. Relationship Context
9. Social Timing
10. Generic Observation
```

最后一级：

```text
generic observation
```

才使用：

```text
5-message Observer
```

---

# v0.9 §149 一百四十九、硬规则与软判断

硬规则：

```text
@Bot
reply_to_bot
cooldown
daily limit
group disabled
social disabled
```

软判断：

```text
relevance
social fit
contribution
interest
topic
activity
```

硬规则优先。

---

# v0.9 §150 一百五十、最终行为模型

最终 CatooBot 群聊参与应该变成：

```text
                    新群消息
                       │
                       ▼
               是否 @ / Reply Bot？
                  ┌────┴────┐
                 是         否
                 │           │
                 ▼           ▼
             高优先级     Active Thread？
                              │
                         ┌────┴────┐
                        是         否
                        │           │
                        ▼           ▼
                  Follow-up     Buffer
                        │           │
                        ▼           ▼
                     Reply      5条消息？
                                     │
                                ┌────┴────┐
                               是         否
                               │           │
                               ▼           ▼
                          Group Observer  Silent
                               │
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
                  Reply      Defer      Ignore
                    │
                    ▼
               Final Gate
                    │
                    ▼
             Response Generation
                    │
                    ▼
              Response Planner
                    │
                    ▼
                    QQ
```

---

# v0.9 §151 一百五十一、核心思想

不要：

```text
“我有 30% 概率插话。”
```

而应该：

```text
“这个话题和我现在正在做的事情高度相关，
而且我确实能贡献一点内容，
现在插话也不突兀，
所以我决定说一句。”
```

或者：

```text
“这个话题我虽然感兴趣，
但现在插进去比较突兀，
所以我继续听。”
```

再或者：

```text
“这其实和我无关，
我保持沉默。”
```

这才是本版本真正要实现的“自主社交行为”。

---

# v0.9 §152 一百五十二、最终交付

完成：

# CatooBot v0.9

必须具备：

```text
✅ Group Conversation Monitor
✅ Group Conversation Buffer
✅ Active Conversation Thread
✅ Thread Tracking
✅ Conversation Continuation Detector
✅ Implicit Follow-up Detection
✅ Direct Address Detection
✅ Group Topic Detection
✅ Social Attention
✅ Social Fatigue
✅ Character Activity Context
✅ Character Focus
✅ Memory-assisted Social Cognition
✅ Relationship-assisted Social Cognition
✅ Social Relevance Evaluation
✅ Social Fit Evaluation
✅ Contribution Value Evaluation
✅ Group Observer
✅ 5-message Observation Trigger
✅ Decision Model
✅ Participation Decision
✅ Reply / Observe / Ignore / Defer
✅ Stale Decision Detection
✅ Final Participation Gate
✅ Response Quality Gate
✅ Observer Lock
✅ Observation Record
✅ Social Decision Debugging
✅ WebUI Social Dashboard
✅ WebUI Group Context
✅ WebUI Observation Debug
✅ WebUI Decision Replay
✅ WebUI Social Policy
✅ Legacy Probability Migration
```

同时：

```text
✅ 维持 v0.8 World Runtime
✅ 维持 Activity
✅ 维持 Persistent Goals
✅ 维持 Memory
✅ 维持 Semantic Retrieval
✅ 维持 Behavior
✅ 维持 Initiative
✅ 维持 Tool Runtime
✅ 维持 Agent Runtime
✅ 维持 Response Planner
✅ 维持 NapCat / OneBot
```

---

# v0.9 §153 一百五十三、开发硬性要求

1. 首先完整阅读 CatooBot 当前源码。
2. 特别检查现有 Group Behavior / Initiative / Character State / Topic / Memory / WebUI。
3. 不创建第二套互相独立的 Behavior Engine。
4. 不创建第二套 Topic Engine。
5. 不创建第二套 Memory Retrieval。
6. 不创建第二套 Scheduler。
7. 不重写 Tool Runtime。
8. 不重写 Agent Runtime。
9. 不删除已有概率字段，先迁移为 legacy。
10. 核心群聊参与逻辑不得再使用 `random() < probability`。
11. 模型评分不是概率。
12. Confidence 不是随机触发概率。
13. 直接 @Bot 必须高优先级。
14. Reply to Bot 必须高优先级。
15. Active Conversation Follow-up 必须优先于 5-message Observer。
16. 普通群聊才进入 5-message Observer。
17. 5-message 只统计外部用户消息。
18. Bot 自己的消息不计数。
19. Observer 不得阻塞 QQ 消息接收。
20. 同一群同时只能存在一个 Observer。
21. 旧 Observer 的结果可能过期，必须进行 Stale Check。
22. 最终发送前必须再次进行 Final Participation Gate。
23. 如果没有真实贡献，不应为了回复而回复。
24. 可以选择沉默。
25. Observer 不需要生成最终回复。
26. Decision Model 与 Main Chat Model 分离。
27. 不要求模型输出隐藏思维链。
28. 只保存结构化 Decision。
29. Memory 只能作为参考信息。
30. Character Activity 必须实时。
31. Character Activity 可以影响参与决策。
32. 角色兴趣不能直接强制插话。
33. Social Fatigue 只能影响软判断。
34. 硬规则优先于软判断。
35. QQ 用户不能看到内部 Decision / Score / Observer / Prompt。
36. 所有调试都进入 WebUI。
37. 所有参数都应该 WebUI 可配置。
38. 普通群聊必须尽量减少主模型调用。
39. 必须重点测试“无 @ 的连续追问”。
40. 必须重点测试“真正相关但无 @ 的群聊话题”。
41. 必须重点测试“看起来相关但实际上不适合插话”的场景。
42. 必须重点测试“话题已经切走但旧 Decision 还没执行”的场景。
43. 最终目标不是提高回复率。
44. 最终目标是提高**参与决策的正确率和自然度**。

---

# v0.9 §154 一百五十四、最终验收标准

核心验收必须至少通过以下场景：

### Case 1：直接跟问

```text
A：
@Bot 在干嘛？

Bot：
在看剧。

A：
看的什么剧？

Bot：
《XXX》。
```

无 @，正常回复。

---

### Case 2：连续追问

```text
A：
@Bot 在干嘛？

Bot：
在看剧。

A：
看的什么？

Bot：
《XXX》。

A：
好看吗？

Bot：
……
```

整个过程属于同一个 Conversation Thread。

---

### Case 3：群聊无关

```text
A：
今晚吃什么？

B：
火锅。

C：
烧烤。

D：
都行。

E：
我还没下班。
```

Bot：

```text
沉默
```

---

### Case 4：兴趣话题

角色：

```text
当前兴趣：
Minecraft
```

群聊：

```text
A：
有人最近玩 Minecraft 吗？

B：
我昨天刚玩。
```

Observer 应识别：

```text
high relevance
```

如果有真实贡献：

```text
reply
```

---

### Case 5：当前活动相关

角色：

```text
activity：
watching_show

activity_detail：
正在看《XXX》
```

群聊：

```text
A：
最近有什么好看的剧？
```

Bot 有较强理由加入。

---

### Case 6：话题转移

```text
Bot：
我在看剧。

A：
看的什么？

Bot：
《XXX》。

B：
对了明天几点开会？
```

不能因为旧 Thread：

强行回答：

```text
《XXX》很好看。
```

---

### Case 7：重复废话

群聊：

```text
A：
哈哈哈哈

B：
哈哈

C：
笑死

D：
哈哈哈哈
```

即使 Observer：

```text
interesting = false
```

必须沉默。

---

### Case 8：贡献价值

群：

```text
A：
今晚吃火锅。

B：
不知道吃哪家。
```

如果角色没有任何相关信息：

```text
ignore
```

不能：

```text
我也觉得火锅不错。
```

---

### Case 9：群聊高相关

角色当前：

```text
activity：
watching_series
```

群聊突然：

```text
A：
《XXX》第三集也太离谱了。

B：
真的。
```

Observer：

```text
topic_relevance = high
current_activity_relevance = high
contribution_value = high
```

允许主动参与。

---

### Case 10：普通群聊 20 条

确认：

```text
最多约 4 次 Observer
```

而不是：

```text
20 次 Main Model
```

---

# v0.9 §155 一百五十五、最终产品目标

v0.8：

```text
CatooBot 有自己的生活。
```

v0.9：

```text
CatooBot 开始真正理解“社交场合”。
```

理想状态：

```text
普通群聊
↓
它不乱说话

有人直接问它
↓
它自然回答

别人接着它的话题追问
↓
它自然接住

群里聊到它正在关注的东西
↓
它会自己加入

虽然它感兴趣
但当前插话很突兀
↓
它继续旁观

没有任何价值
↓
它保持沉默
```

也就是说，最终不是：

```text
“这个 Bot 有 20% 概率发言。”
```

而是：

> **“它会像一个群成员一样，先听，再判断自己有没有必要说话。”**

---

# v0.9 §156 一百五十六、开发方式

现在开始：

**首先阅读当前 CatooBot 的完整源码，重点检查：**

```text
Group Message Pipeline
Group Behavior
Initiative Engine
Character State
Activity Context
Topic Engine
Memory Retrieval
Relationship
Response Planner
WebUI
```

然后：

```text
1. 找到当前群聊非 @ 参与逻辑
2. 保留旧逻辑作为 legacy fallback
3. 建立 Social Cognition Engine
4. 实现 Conversation Continuation
5. 实现 Active Thread
6. 实现 5-message Observer
7. 实现 Character State Context
8. 实现 Topic / Memory / Relationship 联动
9. 实现 Participation Decision
10. 实现 Final Gate
11. 替换旧概率主导逻辑
12. 增加 WebUI Debug
13. 增加完整测试
14. 使用真实群聊历史进行压力测试
```

**不要通过继续调整概率参数来“逼近”目标效果。**

本版本的核心改变必须是架构级的：

```text
旧：

Message
↓
Probability
↓
Reply / Ignore


新：

Message
↓
Conversation Context
↓
Social Cognition
↓
Character State
↓
Topic / Memory / Relationship
↓
Contribution Value
↓
Social Fit
↓
Participation Decision
↓
Final Gate
↓
Reply / Stay Silent
```

这才是 CatooBot 从“会自主发消息”进一步走向“**会自主判断什么时候该说话**”的关键一步。