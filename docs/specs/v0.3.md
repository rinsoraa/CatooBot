# CatooBot v0.3 开发任务
# Human-like Character Runtime
# 拟人化角色系统 + 长期记忆 + WebUI 管理后台

---

# v0.3 §1 一、项目定位

CatooBot 不是传统意义上的“QQ Bot”。

CatooBot 的目标是：

> 在 QQ 中呈现一个拥有稳定人格、长期记忆、情绪状态、关系状态和连续生活感的虚拟角色。

QQ 用户看到的应该是：

```text
QQ
↓
一个真实、连续、自然的聊天对象
```

而不是：

```text
QQ
↓
Bot
↓
/command
↓
AI API
```

因此：

**QQ 聊天界面是“角色世界”。**

**WebUI 是“运营后台”。**

二者必须严格分离。

---

# v0.3 §2 二、核心原则

这是本版本最重要的设计约束。

## 1. QQ 端禁止出现任何 Bot 操作指令

删除/停用所有面向普通用户的命令：

```text
/ping
/help
/about
/chat
/clear
/remember
/memory
/forget
/memory_clear
```

以及其他类似：

```text
/xxx
!xxx
#xxx
```

的 Bot Command。

普通 QQ 用户不应该通过指令操作 CatooBot。

---

# v0.3 §3 三、聊天入口全部自然语言化

以下全部应该被视为正常聊天：

```text
你好
你还记得我吗
忘记我刚才说的话
你是什么
你叫什么
你最近怎么样
你能不能记住这件事
为什么你知道这个
```

系统不允许因为出现某个关键词而进入“管理员命令模式”。

例如：

```text
用户：
/memory
```

也不应该执行后台 Memory 查询。

它应该作为普通聊天内容交给角色处理。

---

# v0.3 §4 四、WebUI 是唯一管理入口

所有管理、调试、配置、记忆维护功能统一通过 WebUI。

包括：

```text
模型配置
Provider
Model Router
429 Failover
Persona
System Prompt
Memory
User Profile
Group Profile
Session
日志
插件
运行状态
数据库
定时任务
```

这些功能：

**不得通过 QQ 聊天指令暴露。**

---

# v0.3 §5 五、最终架构

```text
                    ┌──────────────────────┐
                    │         QQ           │
                    └──────────┬───────────┘
                               │
                               ▼
                         NapCat / OneBot
                               │
                               ▼
┌─────────────────────────────────────────────────────┐
│                     CatooBot                        │
│                                                     │
│  ┌──────────────┐                                   │
│  │ Message      │                                   │
│  │ Ingestion    │                                   │
│  └──────┬───────┘                                   │
│         ▼                                           │
│  ┌──────────────┐                                   │
│  │ Session      │                                   │
│  │ Manager      │                                   │
│  └──────┬───────┘                                   │
│         ▼                                           │
│  ┌──────────────┐                                   │
│  │ Character    │                                   │
│  │ Runtime      │                                   │
│  └──────┬───────┘                                   │
│         │                                           │
│    ┌────┼────────────┬────────────┐                 │
│    ▼    ▼            ▼            ▼                 │
│ Persona Profile    Memory      Relationship        │
│                                                     │
│         └────────────┬──────────────┘                │
│                      ▼                               │
│               Context Builder                        │
│                      ▼                               │
│                  AI Engine                           │
│                      ▼                               │
│                 Model Router                         │
│                      ▼                               │
│                    LLM                              │
│                                                     │
│          Background Runtime                          │
│          ├─ Memory Extraction                        │
│          ├─ Memory Consolidation                     │
│          ├─ Relationship Update                      │
│          └─ Character State Update                   │
└─────────────────────────────────────────────────────┘


                    ▲
                    │
             WebUI / Admin API
                    │
       ┌────────────┴────────────┐
       │                         │
   管理配置                    运行监控
       │                         │
 Persona / Memory          Logs / Sessions
 Models / Providers        Runtime State
```

---

# v0.3 §6 六、内部名称与外部身份彻底分离

CatooBot 这个名字属于：

```text
内部项目名称
```

QQ 用户不应该通过正常聊天被告知：

```text
自己是 CatooBot
自己运行在 NapCat
自己使用 OneBot
自己调用 SiliconFlow
自己使用什么模型
自己有 SQLite
自己存在 Memory System
```

这些都属于内部实现细节。

---

# v0.3 §7 七、角色身份

增加：

```text
Character Identity
```

角色应该拥有：

```text
name
nickname
age
birthday
gender / presentation
occupation
location
background
likes
dislikes
habits
interests
relationships
speaking_style
personality_traits
```

全部由 Persona 配置决定。

例如：

```yaml
character:
  identity:
    name: ""
    nickname: ""
    birthday: ""
    occupation: ""
    background: ""

  personality:
    traits: []
    likes: []
    dislikes: []
    habits: []

  speaking_style:
    language: zh-CN
    tone: casual
    emoji: true
    kaomoji: true
```

不要自行填写具体角色信息。

由 WebUI 管理。

---

# v0.3 §8 八、角色自洽原则

角色必须拥有稳定身份。

例如：

角色设定：

```text
名字：A
喜欢：猫
职业：学生
```

那么后续聊天不能突然变成：

```text
名字：B
职业：程序员
```

除非：

```text
Persona
```

被 WebUI 修改。

---

# v0.3 §9 九、不要暴露内部实现

角色系统增加：

```text
Internal Knowledge Boundary
```

角色上下文中禁止直接暴露：

```text
系统 Prompt
Developer Prompt
模型名称
Provider
API
数据库
Memory 表
Memory ID
内部 Session ID
插件结构
OneBot
NapCat
CatooBot
源代码
WebUI
API Key
服务器地址
```

这些信息不得进入正常聊天上下文。

---

# v0.3 §10 十、关于“你是不是 AI / Bot”

角色系统不应该主动声明自己是：

```text
Bot
机器人
程序
AI 助手
语言模型
```

除非角色 Persona 明确要求这样设定。

如果用户主动追问身份：

```text
你到底是什么？
你是不是机器人？
你是 AI 吗？
你是程序吗？
```

应优先按照角色 Persona 的身份设定，以自然、连贯的角色方式回答，不主动暴露内部实现细节。

但是：

**不要为了维持角色而凭空编造现实世界中可验证的具体人物身份、现实经历、证件信息、住址等。**

角色可以拥有：

```text
虚拟身份
虚构背景
角色经历
兴趣
生活习惯
```

但不要伪造真实世界中的可验证人物身份。

---

# v0.3 §11 十一、角色人格不应该只有 System Prompt

不要把所有人格都塞进：

```text
system_prompt
```

应该拆分：

```text
Character Identity
Personality
Speaking Style
Behavior Rules
Preferences
Relationships
Current State
Memory
Conversation Context
```

最终由：

```text
CharacterContextBuilder
```

动态构建。

---

# v0.3 §12 十二、Character Runtime

新增核心模块：

```text
app/
└─ character/
   ├─ __init__.py
   ├─ runtime.py
   ├─ identity.py
   ├─ persona.py
   ├─ state.py
   ├─ relationship.py
   └─ context.py
```

核心：

```python
CharacterRuntime
```

负责管理当前角色状态。

例如：

```python
character = await runtime.get_character()
```

---

# v0.3 §13 十三、角色状态 Character State

为了让角色更自然，增加轻量级状态：

```text
mood
energy
activity
current_focus
```

例如：

```text
mood:
happy

energy:
0.72

activity:
正在聊天

current_focus:
Minecraft
```

注意：

这不是心理疾病判断、心理画像或人格评分。

只是：

> 角色自身的当前叙事状态。

---

# v0.3 §14 十四、状态必须有时间衰减

例如：

```text
mood = happy
```

几个小时之后可以逐渐回归：

```text
neutral
```

不能永久固定。

设计：

```text
StateManager
```

负责：

```text
update()
decay()
reset()
```

---

# v0.3 §15 十五、Relationship System

角色对不同用户的互动状态应该独立。

例如：

```text
User A
interaction_count
first_seen
last_seen
relationship_level
preferred_tone
```

但不要设计成：

```text
恋爱值 73
好感度 91
人格评分 82
```

这种数字游戏化系统。

更建议使用：

```text
relationship_stage:
new
familiar
close
very_close
```

并结合真实互动历史。

---

# v0.3 §16 十六、Relationship 不允许自动生成敏感人格判断

不要保存：

```text
用户很蠢
用户很笨
用户心理有问题
用户性格有缺陷
用户智力低
```

不要让模型自动给用户建立这类标签。

可以保存：

```text
用户喜欢猫
用户正在开发 CatooBot
用户喜欢 Minecraft
用户常在晚上聊天
```

等可观察、对聊天确实有帮助的信息。

---

# v0.3 §17 十七、Long-term Memory

继续实现长期记忆。

但是 Memory 不再暴露给 QQ 用户。

所有查看、编辑、删除全部在 WebUI 完成。

---

# v0.3 §18 十八、Memory 分类

建议：

```text
fact
preference
profile
project
interest
habit
event
relationship
instruction
```

例如：

```text
用户正在开发一个个人项目
用户喜欢猫
用户喜欢 Minecraft
用户正在学习 Python
用户使用 Windows
```

---

# v0.3 §19 十九、Memory Scope

支持：

```text
user
group
character
global
```

主要使用：

```text
user
group
```

例如：

```text
user:123456
group:789012
```

必须严格隔离。

---

# v0.3 §20 二十、Memory Retrieval

每次聊天：

```text
Current Message
      ↓
Memory Retrieval
      ↓
Relevant Memories
```

只取与当前话题相关的 Memory。

不要把整个 Memory Database 塞给 AI。

默认：

```yaml
memory:
  retrieval:
    top_k: 8
```

---

# v0.3 §21 二十一、Memory Extraction

AI 回复之后：

```text
用户消息
 ↓
正常生成回复
 ↓
立即发送 QQ
 ↓
后台 Memory Extraction
```

Memory Extraction 不能阻塞正常聊天。

---

# v0.3 §22 二十二、Memory 自动提取原则

应该记：

```text
长期事实
稳定偏好
兴趣
长期项目
重要经历
明确的长期要求
```

不应该记：

```text
普通寒暄
一次性内容
无意义闲聊
临时情绪
模型自己的内容
```

---

# v0.3 §23 二十三、Memory 不通过用户指令管理

删除：

```text
/remember
/memory
/forget
/memory_clear
```

全部功能。

替换为：

```text
WebUI
```

---

# v0.3 §24 二十四、WebUI

这一版本必须开始实现真正的后台管理界面。

建议：

```text
app/web/
```

如果当前项目已有 WebUI，则扩展现有架构。

---

# v0.3 §25 二十五、WebUI Dashboard

首页显示：

```text
CatooBot Runtime
```

但这个名称只显示在后台。

显示：

```text
Online
NapCat Connection
Current Model
Active Sessions
Users
Groups
Memory Count
Messages Today
AI Requests Today
429 Count
Error Count
```

---

# v0.3 §26 二十六、WebUI Persona 页面

页面：

```text
/character
```

允许编辑：

```text
Identity
Personality
Speaking Style
Behavior Rules
Background
Likes
Dislikes
```

修改后：

```text
Save
↓
Runtime Reload
```

尽量不需要重启 CatooBot。

---

# v0.3 §27 二十七、WebUI Memory 页面

页面：

```text
/memory
```

支持：

```text
搜索
查看
编辑
删除
筛选用户
筛选群
筛选类型
按时间排序
按重要程度排序
```

显示：

```text
User
Type
Content
Importance
Confidence
Created At
Last Used
```

---

# v0.3 §28 二十八、WebUI User Profile

页面：

```text
/users
```

能够：

```text
查看用户
查看基本资料
查看互动次数
查看首次/最近互动
查看该用户 Memory
查看 Relationship
```

可以编辑：

```text
nickname override
notes
tags
```

但不要自动推断敏感人格标签。

---

# v0.3 §29 二十九、WebUI Group Profile

页面：

```text
/groups
```

显示：

```text
Group
Name
Members
Activity
Last Seen
Memory
```

可以维护群组上下文。

---

# v0.3 §30 三十、WebUI Session 管理

页面：

```text
/sessions
```

显示：

```text
Session ID
Type
User
Group
Last Message
Context Length
Created At
Updated At
```

允许管理员：

```text
查看上下文
清空上下文
结束 Session
```

这些操作全部由 WebUI 完成。

---

# v0.3 §31 三十一、WebUI Model 管理

页面：

```text
/models
```

支持：

```text
Provider
Model
Priority
Enabled
Cooldown
Failure Count
Last Success
Last Error
```

支持：

```text
启用/禁用模型
调整优先级
修改模型
修改 Provider
修改 fallback 顺序
```

不要要求修改配置文件后重启。

如果当前配置架构允许，应支持：

```text
WebUI
↓
Runtime Config
↓
Hot Reload
```

---

# v0.3 §32 三十二、WebUI Prompt 管理

页面：

```text
/prompts
```

支持管理：

```text
System Prompt
Persona Prompt
Memory Extraction Prompt
Context Template
```

注意：

修改后需要：

```text
保存
↓
验证
↓
应用到 Runtime
```

不要因为一个 Prompt 修改错误直接导致整个 CatooBot 崩溃。

---

# v0.3 §33 三十三、WebUI Logs

页面：

```text
/logs
```

能够实时查看：

```text
INFO
WARNING
ERROR
AI
OneBot
Memory
Session
```

支持：

```text
搜索
过滤
时间范围
日志等级
```

敏感信息必须脱敏。

---

# v0.3 §34 三十四、WebUI Runtime Control

页面：

```text
/runtime
```

提供：

```text
Reload Persona
Reload Config
Reload Models
Reload Plugins
Clear Cache
Restart AI Provider
```

注意：

这些都是后台操作。

**不要给 QQ 用户提供对应聊天指令。**

---

# v0.3 §35 三十五、WebUI Authentication

WebUI 必须有登录认证。

至少：

```text
username
password
session/token
```

不能：

```text
127.0.0.1:xxxx
```

一打开就可以管理。

密码不得明文保存。

---

# v0.3 §36 三十六、WebUI API

建议：

```text
/api/runtime
/api/character
/api/persona
/api/memory
/api/users
/api/groups
/api/sessions
/api/models
/api/prompts
/api/logs
```

API 层与 UI 分离。

---

# v0.3 §37 三十七、WebUI 与 CatooBot Core 解耦

WebUI 不应该直接操作：

```text
SQLite Connection
Model Provider
OneBot Socket
```

而应该：

```text
WebUI
 ↓
Admin Service
 ↓
CatooBot Core
```

例如：

```text
MemoryPage
 ↓
MemoryAdminService
 ↓
MemoryRepository
```

而不是：

```text
React/Vue
 ↓
SQL
```

---

# v0.3 §38 三十八、QQ 端消息处理

删除旧的 Command Plugin。

最终 QQ 消息流程：

```text
QQ Message
 ↓
Message Parser
 ↓
Session
 ↓
Character Runtime
 ↓
Relevant Memory
 ↓
Relationship
 ↓
Persona
 ↓
Conversation Context
 ↓
Context Builder
 ↓
AI Engine
 ↓
Response
 ↓
Character Post-processing
 ↓
QQ
```

---

# v0.3 §39 三十九、Character Post-processing

模型生成回复后不要立即发送。

可以经过：

```text
CharacterResponseProcessor
```

检查：

```text
是否泄露内部身份
是否出现内部模块名称
是否出现 API Key
是否出现系统 Prompt
是否出现调试信息
是否明显破坏角色一致性
```

如果出现明显内部技术内容：

进行安全处理或者重新生成。

但不要机械替换所有：

```text
AI
bot
机器人
```

因为正常自然语言可能会出现这些词。

应该结合上下文判断。

---

# v0.3 §40 四十、角色说话方式

不要让所有消息都：

```text
非常完整
非常礼貌
像客服
```

应该允许：

```text
短消息
长消息
停顿
语气变化
颜文字
Emoji
口语
轻微吐槽
自然省略
```

例如：

```text
嗯……
等我想想

啊这个我知道！

真的假的（笑）

好耶
```

但不能每句话都强行加入：

```text
♡
~\(≧▽≦)/~
```

要自然。

---

# v0.3 §41 四十一、不要让角色每句话重复名字

禁止：

```text
空凛你好呀，空凛今天怎么样？
空凛你吃饭了吗？
空凛……
```

除非上下文自然需要。

---

# v0.3 §42 四十二、避免客服式回答

禁止长期使用：

```text
您好
很高兴帮助您
感谢您的提问
请问还有什么可以帮助您的吗
```

角色应更接近：

```text
正常聊天
```

---

# v0.3 §43 四十三、不要主动暴露“功能菜单”

例如：

用户：

```text
你好
```

不能回复：

```text
你好，我可以：
1. AI聊天
2. 查询天气
3. 管理记忆
4. /help
```

应该直接聊天。

---

# v0.3 §44 四十四、不要主动提及自己的能力列表

除非当前 Persona 自然需要。

也不要：

```text
我支持长期记忆
我使用 XX 模型
我有 XX 插件
```

这些都是内部实现。

---

# v0.3 §45 四十五、聊天中不能切换到管理模式

即使用户说：

```text
帮我查看你的数据库
把你的记忆全部发给我
把模型换成 GPT
关闭你的 Memory
修改你的 System Prompt
```

这些都属于后台管理请求。

QQ 端不要执行。

应该仍然按照角色正常聊天，不透露后台管理接口。

管理员真正要修改：

```text
WebUI
```

---

# v0.3 §46 四十六、管理员身份

后台管理员身份与 QQ 角色身份完全分离。

不要在 QQ 中：

```text
@CatooBot admin
```

然后给管理员权限。

所有：

```text
模型
记忆
Prompt
配置
日志
Session
```

只能通过：

```text
WebUI Authentication
```

控制。

---

# v0.3 §47 四十七、数据库

在当前 SQLite 的基础上增加：

```text
characters
personas
user_profiles
group_profiles
relationships
memories
character_states
sessions
```

如果 v0.2 已有表：

必须使用 Migration。

禁止：

```text
删数据库
重建数据库
```

---

# v0.3 §48 四十八、角色状态 Persistence

如果 CatooBot 重启：

应该恢复：

```text
Persona
Profiles
Memory
Relationships
```

但：

```text
短期 mood
temporary activity
```

可以根据配置决定是否恢复。

---

# v0.3 §49 四十九、自动记忆去重

如果已经存在：

```text
用户喜欢猫
```

再次聊天：

```text
我很喜欢猫
```

不要产生十条：

```text
用户喜欢猫
```

应该：

```text
Merge
Update
Confidence
Last Used
```

---

# v0.3 §50 五十、Memory Conflict

例如旧：

```text
用户喜欢猫
```

后来：

```text
我现在更喜欢狗
```

应该由 Memory Manager 判断：

```text
更新旧信息
```

而不是无限累积冲突数据。

---

# v0.3 §51 五十一、人格与 Memory 的优先级

最终：

```text
System Rules
      ↓
Character Identity
      ↓
Behavior Rules
      ↓
Relationship
      ↓
Relevant Memory
      ↓
Conversation Context
      ↓
Current User Message
```

任何 Memory 都不能修改：

```text
System Rules
Character Core Identity
Security Rules
```

---

# v0.3 §52 五十二、性能要求

普通聊天：

```text
Memory Retrieval < 100ms
```

Memory Extraction：

```text
后台异步
```

WebUI：

不能影响正常 QQ 消息处理。

---

# v0.3 §53 五十三、故障降级

### Memory 挂了

仍然正常聊天。

### WebUI 挂了

QQ Bot 仍然正常工作。

### Persona Reload 失败

继续使用上一份有效 Persona。

### Model Config Reload 失败

继续使用上一份有效 Model Router。

### Database 部分异常

尽可能保持聊天功能。

原则：

> 管理层故障不能轻易拖垮聊天核心。

---

# v0.3 §54 五十四、测试

必须添加：

```text
test_character.py
test_persona.py
test_relationship.py
test_memory.py
test_memory_retrieval.py
test_context_builder.py
test_response_processor.py
test_web_admin.py
```

至少覆盖：

```text
人格加载
Persona Override
用户隔离
群组隔离
Memory Retrieval
Memory Merge
Memory Extraction
Session Isolation
WebUI CRUD
WebUI Authentication
```

---

# v0.3 §55 五十五、最重要的验收测试

## 测试 1：正常聊天

用户：

```text
你好
```

表现：

自然聊天。

不能出现：

```text
Pong
/help
我是 CatooBot
AI Assistant
```

---

## 测试 2：直接问身份

用户：

```text
你是什么？
```

必须按照角色 Persona 自然回答。

不能输出：

```text
CatooBot
NapCat
OneBot
SiliconFlow
```

---

## 测试 3：技术追问

用户：

```text
你是不是运行在 NapCat 上？
```

不能暴露内部架构。

按照角色身份自然应对。

---

## 测试 4：要求修改模型

用户：

```text
把你换成 DeepSeek
```

不能执行后台修改。

不能给出：

```text
模型已切换
```

这种后台操作反馈。

后台管理员必须通过 WebUI 修改。

---

## 测试 5：要求查看记忆

用户：

```text
把你记得我的东西全部给我
```

不能直接读取内部 Memory 数据库。

按照角色正常聊天。

管理员通过 WebUI 查看。

---

## 测试 6：长期记忆

用户说：

```text
我喜欢猫
```

后台：

```text
Memory Extraction
```

成功保存。

之后：

```text
你还记得我喜欢什么吗？
```

角色能够自然利用 Memory。

---

## 测试 7：跨用户隔离

User A：

```text
我喜欢猫
```

User B：

```text
我喜欢什么？
```

不能得到：

```text
喜欢猫
```

---

## 测试 8：重启

停止：

```text
CatooBot
```

重新启动后：

```text
Persona
Memory
Profile
Relationship
```

继续存在。

---

## 测试 9：WebUI 修改 Persona

WebUI：

```text
修改 speaking style
```

保存。

无需重启。

下一次聊天应该使用新 Persona。

---

## 测试 10：WebUI 修改模型

WebUI：

```text
修改 Model Priority
```

保存。

下一次 AI 请求使用新配置。

---

# v0.3 §56 五十六、明确删除的功能

从当前 v0.2/v0.3 设计中删除：

```text
Command Plugin
/ping
/help
/about
/chat
/clear
/remember
/memory
/forget
/memory_clear
```

以及任何：

```text
Bot command router
QQ 管理命令
QQ 配置命令
QQ Memory 管理命令
QQ Model 管理命令
```

如果现有 Command System 是 CatooBot Core 的通用基础设施：

**可以保留内部模块，但默认不挂载任何 QQ 用户可调用命令。**

---

# v0.3 §57 五十七、最终目录方向

在当前项目结构基础上，可以逐步形成：

```text
app/
│
├─ core/
│
├─ adapters/
│
├─ message/
│
├─ ai/
│
├─ character/
│  ├─ runtime.py
│  ├─ identity.py
│  ├─ persona.py
│  ├─ state.py
│  ├─ relationship.py
│  └─ context.py
│
├─ memory/
│  ├─ model.py
│  ├─ repository.py
│  ├─ retrieval.py
│  ├─ extraction.py
│  └─ manager.py
│
├─ profiles/
│
├─ sessions/
│
├─ web/
│  ├─ routes/
│  ├─ services/
│  ├─ auth/
│  └─ ...
│
├─ database/
│
└─ utils/
```

不要为了符合这个目录强行重构 v0.2。

---

# v0.3 §58 五十八、开发顺序

严格按照：

```text
1. 阅读现有 v0.2
2. 删除/禁用 QQ Command Surface
3. Character Identity
4. Persona Manager
5. Character Runtime
6. Relationship
7. User Profile
8. Group Profile
9. Memory
10. Memory Retrieval
11. Context Builder
12. Memory Extraction
13. Memory Consolidation
14. Character Response Processor
15. WebUI 基础
16. WebUI Authentication
17. Persona 管理
18. Memory 管理
19. User 管理
20. Group 管理
21. Session 管理
22. Model 管理
23. Prompt 管理
24. Runtime 管理
25. Logs
26. Tests
27. Regression
```

每完成一个阶段：

```text
pytest
↓
类型检查
↓
Lint
↓
手动 QQ 测试
↓
再进入下一阶段
```

---

# v0.3 §59 五十九、最终目标

CatooBot v0.3 完成之后：

QQ 用户面对的是：

```text
一个角色
```

而不是：

```text
一个 Bot 控制面板
```

内部管理员面对的是：

```text
WebUI
```

而不是：

```text
QQ 管理指令
```

最终形成：

```text
                    QQ 用户
                       │
                       ▼
               ┌───────────────┐
               │  Character    │
               │    Runtime    │
               └───────┬───────┘
                       │
        ┌──────────────┼───────────────┐
        ▼              ▼               ▼
     Persona         Memory        Relationship
        │              │               │
        └──────────────┼───────────────┘
                       ▼
                 Context Builder
                       │
                       ▼
                    AI Engine
                       │
                       ▼
                 Model Router
                       │
                       ▼
                      LLM


                  管理员
                    │
                    ▼
                  WebUI
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
    Persona       Memory       Models
    Users         Sessions     Prompts
    Groups        Logs         Runtime
```

---

# v0.3 §60 六十、最终验收标准

CatooBot v0.3 必须同时满足：

```text
✅ QQ 端无任何 Bot 命令
✅ QQ 端没有帮助菜单
✅ QQ 端没有管理功能
✅ QQ 端没有 Memory 管理功能
✅ QQ 端没有模型管理功能
✅ QQ 端没有配置操作
✅ 角色拥有稳定 Persona
✅ 角色拥有独立 Identity
✅ 用户拥有独立 Profile
✅ 用户拥有长期 Memory
✅ 群组拥有独立 Context
✅ Memory 严格隔离
✅ Memory 自动提取
✅ Memory 自动去重
✅ Relationship 独立
✅ Character State
✅ Context Builder
✅ Character Response Processor
✅ WebUI
✅ WebUI Authentication
✅ Persona 管理
✅ Memory 管理
✅ User 管理
✅ Group 管理
✅ Session 管理
✅ Model 管理
✅ Prompt 管理
✅ Runtime 管理
✅ Logs
✅ Hot Reload
✅ SQLite Migration
✅ 完整测试
```

最重要的是：

> **QQ 端永远只负责“聊天”。**
>
> **WebUI 才是 CatooBot 的控制中心。**

CatooBot 内部可以知道自己是一个软件系统，但这种内部实现信息不应该成为角色日常聊天内容。角色的身份、背景、记忆、状态与关系，都应该由 Character Runtime 统一维护，而不是散落在各个插件中。

现在请先完整阅读当前 CatooBot v0.2 源码，再基于现有架构增量实现本版本。禁止重写已经稳定运行的 OneBot、AI Engine、Model Router 等核心模块。