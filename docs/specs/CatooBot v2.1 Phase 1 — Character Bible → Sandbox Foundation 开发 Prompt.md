# CatooBot v2.1 — Phase 1
# Character Bible → Sandbox Foundation
# 人物档案、角色实例、世界种子与数据边界重构

---

# 一、你的任务

当前 CatooBot 已完成 v2.0 Character Life Sandbox 的第一版实现。

现在不要继续扩展新的生活行为。

本阶段只负责解决：

1. Character Bible 是不是当前角色的唯一 Canonical Source
2. 人物档案是否能够完整编译
3. Character Bible 是否真的能初始化 Character Entity
4. Character Bible 是否真的能初始化 Pet / Space / Object / Inventory / Mode / Action / Project / Social Space
5. 旧 Persona / Memory / Relationship / World 是否已经与新角色完全隔离
6. Sandbox Seed 是否仍存在大量绕过 Character Bible 的硬编码
7. 为后续 Entity Interaction / QQ External Influence / Memory Integration 建立稳定的基础数据契约
8. 不在本阶段大规模修改 QQ 对话行为

本阶段完成后，必须达到：

> **只更换 Character Bible，就可以初始化出一个不同角色的 Sandbox，而不需要修改 Python 源码中的角色硬编码。**

---

# 二、最重要的工作原则

这次不是“给现在代码打几个补丁”。

必须先阅读当前仓库完整代码，然后识别：

```text
Character Bible
Character Entity
Pet
Space
Object
Inventory
Mode
Action
Project
Social Space
Relationship
Continuity
Memory
Sandbox Runtime
```

之间现在到底是什么关系。

不要假设当前设计和文档完全一致。

以当前真实代码为准。

---

# 三、人物档案附件

本次我会同时提供：

# 【Character Bible / 人物档案附件】

这份附件就是本版本中的：

```text
Canonical Character Source
```

不要要求我重新整理成人工规定的 YAML/JSON 格式。

你必须直接阅读当前附件。

人物档案可能是自然语言 Markdown，包含：

- 角色档案
- 人格
- 状态
- 行为
- 生活方式
- 关系
- 世界
- 宠物
- 空间
- 物品
- 习惯
- 行为示例
- 对话示例
- 隐含规则

必须尽可能将其编译成结构化 Character Bible。

---

# 四、第一步：完整代码审计

在修改任何代码之前，完整检查：

```text
app/sandbox/
app/character/
app/continuity/
app/memory/
app/social/
app/response/
plugins/chat/
app/core/
app/database/
app/config/
```

重点检查：

### Character Bible

```text
bible.py
parser
compiler
coverage
validation
```

### Sandbox

```text
runtime
models
seed
entities
spaces
objects
inventory
needs
actions
decision
modes
events
```

### Character

```text
persona
state
relationship
context
runtime
```

### Conversation

```text
conversation runtime
chat plugin
external event
response generation
```

### Memory

```text
memory write path
memory retrieval
memory reset
embedding
```

### Continuity

```text
continuity state
open loops
shared experiences
interaction profile
micro events
```

---

# 五、首先输出“现状架构报告”

在真正修改前，先生成一份内部审计结果。

至少回答：

```text
1. Character Bible 当前从哪里进入系统？
2. Bible Compiler 当前真正输出什么？
3. 哪些 Bible 字段真的进入 Sandbox？
4. 哪些字段只是 Prompt？
5. 哪些字段完全没被使用？
6. CharacterEntity 如何初始化？
7. Pet 如何初始化？
8. Space 如何初始化？
9. Object 如何初始化？
10. Inventory 如何初始化？
11. Mode 如何初始化？
12. Action 如何初始化？
13. Project 如何初始化？
14. Social Space 如何初始化？
15. 哪些地方存在角色硬编码？
16. 哪些地方存在旧角色硬编码？
17. 哪些地方存在旧 Persona？
18. 哪些地方存在旧 Memory / Relationship / Continuity？
19. 哪些数据应该保留？
20. 哪些数据必须清理？
```

---

# 六、Character Bible 必须成为唯一 Canonical Source

最终必须形成：

```text
Character Bible
       ↓
Character Definition
       ↓
Sandbox Seed
       ↓
Runtime Character
```

不得存在：

```text
Character Bible
+
旧 Persona
+
硬编码罐头
+
默认 CharacterEntity
+
另一个角色配置
```

多个来源竞争角色定义。

---

# 七、Character Definition

如果当前项目还没有合适的数据结构，新增：

```python
CharacterDefinition
```

至少分为：

```text
identity
appearance
personality
values
preferences
habits
behavior_rules
modes
speech
boundaries
relationships
pet_definitions
space_definitions
object_definitions
inventory_definitions
action_definitions
project_definitions
social_space_definitions
livelihood
world_rules
```

如果已有等价结构：

直接扩展已有结构，不重复创建。

---

# 八、Static Facts 与 Runtime State 必须彻底分开

例如人物档案：

```text
姓名：罐头
年龄：18
生日：1月6日
身高：160cm
```

属于：

```text
Character Definition / Canonical Fact
```

当前状态：

```text
现在在哪里
现在做什么
现在困不困
现在是否在游戏
```

属于：

```text
Character Entity Runtime State
```

不能混。

---

# 九、CharacterEntity 必须从 Bible 初始化

当前如果存在：

```python
CharacterEntity()
```

这种依赖默认值的初始化方式：

必须重构为：

```python
CharacterEntity.from_definition(character_definition)
```

或者等价机制。

目标：

```text
Character Bible
        ↓
CharacterEntity
```

而不是：

```text
硬编码 CharacterEntity
+
Character Bible
```

---

# 十、禁止角色硬编码泄漏

搜索整个仓库：

```text
罐头
小罐头
Catodayo
小喵
Minecraft
草
www
```

以及角色具体：

```text
年龄
生日
关系
生活方式
作息
偏好
人物特征
```

找出所有硬编码。

然后分类：

### 合法硬编码

例如：

```text
系统枚举
Action ID
技术默认值
```

### 非法角色硬编码

例如：

```text
CharacterEntity 默认 name = 罐头
默认 pet = 小喵
默认 location = livingroom
默认喜欢 Minecraft
默认深夜模式
```

这些都必须迁移到：

```text
Character Bible / Seed
```

---

# 十一、Pet 必须从 Bible 初始化

如果 Character Bible 包含宠物：

必须实现：

```python
PetEntity.from_definition(...)
```

而不是永远：

```text
Pet = 小喵
```

必须支持至少：

```text
id
name
species
personality/traits
habits
preferences
needs
initial_location
initial_state
relationship_to_owner
behavior_rules
```

---

# 十二、Pet Definition 与 Pet Runtime 分离

例如：

### Definition

```text
name = 小喵
species = cat
traits = lazy
favorite_places = ...
```

### Runtime

```text
hunger = 0.72
energy = 0.41
location = sofa
activity = sleeping
```

不能混成一个对象。

---

# 十三、Space 必须从 Character Bible Seed

如果人物档案描述：

```text
居住环境
房间
店铺
周边
社交空间
```

这些必须进入：

```text
SpaceDefinition
```

最终：

```text
Character Bible
↓
Space Seed
↓
SpaceSystem
```

---

# 十四、Space 必须支持最基础的属性

至少：

```text
id
name
type
parent_space
connections
allowed_actions
objects
entities
properties
```

如果当前已经具备，就不要重写，只修初始化来源。

---

# 十五、Object 必须从 Bible Seed

人物档案如果声明：

```text
冰箱
电脑
手机
床
沙发
电视
衣柜
猫粮盆
水盆
```

应该通过：

```text
ObjectDefinition
```

进入 Runtime。

不能继续依赖：

```python
build_objects()
```

这种永久固定角色世界的方式。

可以保留系统级对象模板：

```text
FridgeTemplate
ComputerTemplate
BedTemplate
```

但是实例化参数必须来自 Character Definition / Seed。

---

# 十六、Inventory 必须从 Bible Seed

例如：

```text
冰箱
├── 可乐
├── 布丁
└── 蛋糕
```

必须支持：

```text
initial_inventory
```

而不是：

```text
每个罐头默认都有这些。
```

---

# 十七、Inventory 定义格式

支持：

```text
InventoryDefinition
{
    owner_id
    object_id
    item_id
    quantity
    properties
}
```

允许：

```text
Fridge:
cola = 10
pudding = 3
```

---

# 十八、Mode 必须从 Bible 初始化

人物档案中的：

```text
HOME
OUTDOOR
DEEP_NIGHT
GAMING
ONLINE_SOCIAL
```

不能只存在 Python 里的枚举和固定描述。

必须成为：

```text
ModeDefinition
```

至少：

```text
id
name
trigger_conditions
exit_conditions
priority
speech_policy
behavior_preferences
activity_preferences
social_preferences
```

---

# 十九、模式可以叠加

数据结构必须支持：

```text
primary_mode
secondary_modes
```

例如：

```text
HOME
+
GAMING
+
ONLINE_SOCIAL
```

不要设计为：

```text
mode = one enum only
```

---

# 二十、Action 必须从 Character Definition / Action Registry 构建

Action 可以保留系统模板。

例如：

```text
eat
sleep
play_minecraft
watch_animation
browse_forum
feed_pet
buy_food
take_out_trash
```

但角色是否拥有某个 Action，以及它的具体行为偏好：

应该来自：

```text
Character Definition
```

---

# 二十一、Action Definition 与 Action Instance 分开

Definition：

```text
“玩 Minecraft 是什么行为”
```

Instance：

```text
“罐头现在正在玩 Minecraft”
```

不要混。

---

# 二十二、Project 必须成为第一等数据

如果 Character Bible 中存在：

```text
长期 Minecraft 世界
长期建筑项目
长期游戏目标
线上委托
其他持续目标
```

必须初始化成：

```text
ProjectDefinition
ProjectEntity
```

而不是只用：

```text
activity_detail = "建房子"
```

---

# 二十三、Project 必须有生命周期

至少：

```text
planned
active
paused
completed
abandoned
```

允许：

```text
progress
current_task
next_step
```

---

# 二十四、Social Space 必须从 Bible 初始化

如果人物档案描述：

```text
Minecraft 群
猫图群
甜品群
论坛
游戏服务器
```

必须初始化为：

```text
SocialSpaceEntity
```

而不是让 QQ Group 配置承担全部 Social World。

---

# 二十五、真实 QQ 群与模拟 Social Space 分离

真实：

```text
QQ Group
```

只是：

```text
External Platform Binding
```

而：

```text
Minecraft Group
Cat Group
Dessert Group
Forum
```

属于 Character World 的：

```text
SocialSpace
```

如果没有真实 QQ，可以后台模拟存在。

---

# 二十六、建立 Character World Seed

最终应有：

```python
CharacterWorldSeed
```

至少：

```text
character
pets
spaces
objects
inventories
projects
social_spaces
relationships
actions
modes
rules
```

---

# 二十七、Sandbox 初始化必须完全从 Seed 生成

目标：

```text
Character Bible
       ↓
Compiler
       ↓
CharacterWorldSeed
       ↓
SandboxInitializer
       ↓
Sandbox Runtime
```

不要：

```text
Character Bible
       +
大量 build_xxx() 默认角色状态
```

---

# 二十八、Seed 生成之后必须可序列化

实现：

```text
export_seed()
```

例如：

```json
{
  "character": {...},
  "pets": [...],
  "spaces": [...],
  "objects": [...],
  "inventories": [...],
  "projects": [...],
  "social_spaces": [...],
  "modes": [...],
  "actions": [...]
}
```

这是 Debug 与未来迁移的基础。

---

# 二十九、Sandbox 初始化必须可复现

同一个：

```text
Character Bible
+
Seed
+
Simulation Seed
```

必须得到相同初始化结果。

---

# 三十、Character Bible Parser 必须兼容自然语言档案

不能要求人物档案必须改成你自己规定的 YAML。

当前提供的人物档案可能包含：

```text
# 标题
## 小标题
### 模式
表格
列表
自然语言段落
对话示例
```

Parser 应尽可能识别语义结构。

---

# 三十一、特别处理中文标题

必须支持类似：

```text
一、角色档案
二、人格分层
2.1 外出模式
2.2 宅家模式
三、人物关系网
四、价值观与动机
五、生活状态
六、对话示例
七、扮演准则
```

不能只支持：

```text
## Static Facts
## Modes
```

这种内部格式。

---

# 三十二、对话示例必须单独处理

人物档案里的：

```text
对话示例
```

不能直接变成 World Rule。

应进入：

```text
Speech Calibration Examples
```

供：

```text
Character Response Runtime
```

使用。

---

# 三十三、示例中的事实也要能提取

例如：

```text
“猫跳上键盘”
```

不能因为它出现在 Example 里就自动成为硬规则。

但可以提取成：

```text
Behavior Candidate
```

然后与正式档案规则合并。

---

# 三十四、扮演准则必须优先级最高

人物档案中的：

```text
明确行为规则
明确隐私边界
明确关系规则
```

必须成为：

```text
Canonical Behavior Policy
```

而不是普通 Few-shot。

---

# 三十五、建立 Rule Source

每条 Canonical Rule 记录：

```text
rule_id
source_document
source_section
source_text
compiled_target
status
```

这样可以从 Runtime 反查人物档案原文。

---

# 三十六、Coverage 必须升级

当前 Coverage 不要只统计：

```text
runtime
prompt_only
```

改为：

```text
Parsed
Compiled
Seeded
Runtime Connected
Tested
```

最终：

```text
档案条目
→ Compiler
→ Runtime
→ Test
```

形成完整证据链。

---

# 三十七、覆盖率报告示例

例如：

```text
档案：
“回家第一件事是换猫猫睡衣”

Parser:
Behavior Rule

Compiler:
HomeEntryRule

Seed:
loaded

Runtime:
HomeEntryAction

Test:
PASS

Status:
FULLY IMPLEMENTED
```

---

# 三十八、旧人物数据 Reset

创建：

```text
CharacterResetPlan
```

首先扫描：

```text
旧 Persona
旧 Memory
旧 Relationship
旧 Continuity
旧 Sandbox
旧 User Relationship
旧 Character Goals
旧 Projects
旧 Character Preferences
```

并生成：

```text
Reset Preview
```

---

# 三十九、Reset 不要立即删除

第一次运行：

```text
Preview
```

生成：

```text
reset_report.json
```

确认：

```text
哪些表
哪些记录
多少条
```

然后执行 Reset。

---

# 四十、Reset 后建立新角色

执行顺序：

```text
旧角色清理
↓
Character Bible Import
↓
Compile
↓
Validate
↓
Build Seed
↓
Initialize Sandbox
↓
Initialize Character
↓
Initialize Pet
↓
Initialize Spaces
↓
Initialize Objects
↓
Initialize Inventory
↓
Initialize Projects
↓
Initialize Social Spaces
↓
Initialize Relationships
```

---

# 四十一、旧记忆必须清理

尤其检查：

```text
episodic_memory
semantic_memory
memory_embeddings
memory_links
memory_consolidation
```

不能留下旧角色记忆。

---

# 四十二、旧 Relationship 必须清理

例如：

```text
user → old character relationship
```

必须删除。

但是：

```text
User identity
QQ group
QQ account
```

保留。

---

# 四十三、旧 Continuity 必须清理

包括：

```text
current_interest
recent_events
open_loops
shared_experiences
affective_context
```

全部不要继承旧角色。

---

# 四十四、角色获取的 Sticker / Media 数据必须重新划分

先不要删除系统级：

```text
Native Face
Built-in Sticker
Global Media Cache
```

但是必须识别：

```text
Character-acquired Sticker
Character-specific Media Memory
```

如果当前数据结构无法区分：

本阶段先建立字段 / namespace：

```text
scope
character_id
origin
```

不要直接粗暴删除。

---

# 四十五、Reset 后新人物不得继承旧角色专属 Sticker

除非该 Sticker 被明确标记为：

```text
global_asset
```

否则：

```text
character_asset
```

必须隔离。

---

# 四十六、同样处理 Image Analysis Cache

区分：

```text
platform/cache
```

与：

```text
character_memory
```

本阶段先做好数据边界。

不要求现在删除所有图片缓存。

---

# 四十七、与现有 Conversation / Response 的兼容

本阶段：

**暂时不要重写 QQ 回复流程。**

只确保：

```text
Character Context
```

未来可以读取：

```text
Sandbox State
Character Definition
Current Mode
Current Action
Current Location
Current Project
Current Pet State
```

---

# 四十八、不要在这个阶段实现复杂 Entity Interaction

例如：

```text
猫跳键盘
用户邀请打游戏
冰箱空了
```

这些属于后续 Phase。

本阶段只需要：

```text
数据结构
初始化基础
事件接口
```

不要提前实现复杂逻辑。

---

# 四十九、建立后续 Phase 所需接口

本阶段至少准备：

```python
SandboxEventBus
ExternalInfluence
EntityInteraction
StateMutation
DecisionRequest
```

但可以先是稳定接口 + 最小实现。

---

# 五十、未来 External Event 接口

定义：

```python
ExternalEvent
```

至少：

```text
id
source
type
timestamp
actor_id
target_id
payload
priority
```

未来 QQ 就通过它进入 Sandbox。

---

# 五十一、未来 Entity Interaction 接口

定义：

```python
EntityInteraction
```

例如：

```text
actor
target
interaction_type
context
effects
```

本阶段不需要完成全部行为。

但数据结构必须稳定。

---

# 五十二、未来 State Mutation 接口

统一：

```python
SandboxStateManager.apply(...)
```

而不是：

```python
entity.xxx = ...
```

散落在各个模块里。

---

# 五十三、状态变化必须记录来源

每次 mutation：

```text
source
reason
before
after
timestamp
```

例如：

```text
inventory:
cola
before=1
after=0

source=character_action
reason=drink_cola
```

---

# 五十四、不要引入第二套数据库

复用当前 SQLite / Repository / Migration 架构。

---

# 五十五、不要大规模重写稳定模块

优先：

```text
Adapter
Factory
Initializer
Compiler
Repository
Namespace
```

而不是：

```text
rewrite everything
```

---

# 五十六、不要改变以下系统的核心逻辑

本阶段不要重写：

```text
OneBot
AIEngine
ModelRouter
ToolRuntime
AgentRuntime
ConversationTurnRuntime
SocialCognition
ResponseDelivery
MediaRuntime
```

只做必要的数据兼容。

---

# 五十七、Legacy World 的处理

当前旧：

```text
WorldActivity
Routine
ActivityEpisode
WorldEvent
WorldTimeline
```

本阶段不要立即全部删除。

先标记：

```text
legacy
```

建立：

```text
Legacy World Compatibility Layer
```

确保现有系统还能启动。

---

# 五十八、但是禁止 Legacy World 继续创建新的 Character Life State

本阶段完成后：

新角色状态必须来自：

```text
Sandbox
```

Legacy World 只读 / 兼容。

---

# 五十九、Character State 的来源必须明确

最终：

```text
Sandbox = source of truth
```

Character State 只是：

```text
projection / compatibility view
```

不能继续存在两套互相写入的 State。

---

# 六十、同步关系

最终：

```text
Sandbox State
      ↓
Character State Projection
      ↓
Character Context
```

不能：

```text
Character State
↔
Sandbox State
```

双向随意写。

---

# 六十一、Mode State 也必须以 Sandbox 为 source of truth

未来：

```text
Sandbox
→ current_mode
```

Character Runtime 读取。

不能：

```text
Character Runtime 自己再推一遍 Mode。
```

---

# 六十二、验证没有双重 Source of Truth

请全局搜索：

```text
activity =
current_activity =
location =
current_location =
mode =
current_mode =
is_sleeping =
```

并列出所有写入点。

最终每类状态：

必须只有一个 authoritative writer。

---

# 六十三、测试

至少新增：

## Character Bible Parsing

测试：

```text
中文自然语言人物档案
标题
表格
列表
模式
关系
对话示例
扮演准则
```

---

# 六十四、Character Bible Compilation

测试：

```text
identity
personality
mode
relationship
pet
space
object
inventory
project
social_space
```

全部能够进入结构化 Definition。

---

# 六十五、Character Seed Test

使用本次人物档案初始化：

检查：

```text
Character
Pet
Spaces
Objects
Inventory
Modes
Actions
Projects
Social Spaces
Relationships
```

全部存在。

---

# 六十六、Reset Test

先构造旧角色数据：

```text
old persona
old memory
old relationship
old world
old continuity
```

执行 Reset。

确认：

```text
全部角色数据清除
平台数据保留
```

---

# 六十七、New Character Isolation Test

初始化新角色后：

确认：

```text
旧角色名字不存在
旧记忆不存在
旧关系不存在
旧世界状态不存在
旧 Continuity 不存在
旧 Character Sticker 不继承
```

---

# 六十八、Seed Reproducibility Test

相同：

```text
Bible
Seed
Simulation Seed
```

重复初始化：

应该产生相同结果。

---

# 六十九、Coverage Test

每一个人物档案核心设定必须至少进入：

```text
Parsed
Compiled
```

无法实现的必须：

```text
Unresolved
```

不能 silently dropped。

---

# 七十、Repository Hygiene

本阶段同时检查 Git 仓库。

禁止提交：

```text
.env
真实 config.yaml
SQLite 数据库
data/
logs/
cache/
reset backups
session dumps
character runtime snapshots
真实用户数据
真实 QQ 消息
API keys
tokens
cookies
auth files
private media
```

---

# 七十一、检查 Git History

不仅检查当前工作树：

还检查最近提交中是否曾经提交过：

```text
密钥
数据库
聊天记录
运行快照
私人媒体
```

如果发现：

必须报告。

不要擅自重写 Git History。

除非我后续明确要求。

---

# 七十二、检查 `.gitignore`

确保：

```text
data/
logs/
cache/
.env
*.db
*.sqlite
*.sqlite3
__pycache__/
.pytest_cache/
reset backups
runtime snapshots
```

等不会进入 Git。

但不要盲目忽略：

```text
tests/
docs/
config examples
```

---

# 七十三、不要把人物档案本身提交进公开 GitHub

如果人物档案包含：

```text
真实用户关系
私人设定
私密信息
```

建议放在：

```text
data/private/
```

或外部挂载位置。

仓库只保留：

```text
character.example.md
```

或 schema / template。

如果当前仓库已经有真实档案：

先报告，不要擅自删除。

---

# 七十四、最终 Phase 1 目标

完成后应该形成：

```text
Character File
      ↓
Character Bible Parser
      ↓
Character Bible
      ↓
Character Definition
      ↓
Character World Seed
      ↓
Sandbox Initializer
      ↓
Runtime Sandbox
```

其中：

```text
CharacterEntity
PetEntity
Space
Object
Inventory
Mode
Action
Project
SocialSpace
Relationship
```

都真正来自 Character Definition。

---

# 七十五、Phase 1 明确不做

不要在本阶段完成：

```text
❌ QQ → Sandbox 完整自然语言影响
❌ Pet → Character 完整互动
❌ Object → Character 完整互动
❌ Memory → Sandbox Decision
❌ Sandbox → Long-term Memory
❌ Emergent Behavior
❌ 复杂生活模拟
❌ 24h 世界行为优化
❌ 重新设计 Response Runtime
❌ 重写 Social Cognition
```

这些全部留给后续 Phase。

---

# 七十六、开发完成后必须汇报以下内容

不要只回复：

> Phase 1 完成。

必须按照下面结构汇报。

---

## A. Architecture Audit

说明：

```text
当前 Character Bible 如何进入系统
当前 Sandbox 如何初始化
哪些地方原来是硬编码
哪些地方被修改
```

---

## B. Character Bible Schema

列出最终：

```text
Identity
Personality
Modes
Rules
Relationships
Pets
Spaces
Objects
Inventory
Projects
Social Spaces
Livelihood
```

分别由什么类表示。

---

## C. Bible → Runtime Mapping

给出实际映射表。

例如：

```text
人物档案“宠物小喵”
→ BiblePet
→ PetDefinition
→ PetEntity
→ SandboxRuntime.pet
```

---

## D. Hardcoded Character Values

列出：

```text
修改前
修改后
```

以及：

```text
仓库中是否仍存在角色硬编码
```

---

## E. Reset Report

必须明确：

```text
旧 Persona：
删除多少

旧 Memory：
删除多少

旧 Relationship：
删除多少

旧 Continuity：
删除多少

旧 Sandbox：
删除多少

旧 Character Asset：
删除/保留多少
```

以及：

```text
哪些平台数据被保留。
```

---

## F. Sandbox Seed Report

输出当前人物初始化后的：

```text
Character
Pet
Spaces
Objects
Inventory
Modes
Actions
Projects
Social Spaces
Relationships
```

至少给结构化摘要。

---

## G. Bible Coverage Report

必须给：

```text
Total
Parsed
Compiled
Seeded
Runtime Connected
Prompt Only
Unresolved
```

最好列出：

```text
Unresolved Items
```

---

## H. Data Boundary Report

明确：

```text
Character Data
User Data
Platform Data
Global Assets
Character Assets
Media Cache
Memory
Sandbox State
```

现在分别存在哪里。

---

## I. Legacy World Report

说明：

```text
哪些旧 World 模块保留
哪些降级为 compatibility
哪些不再写入角色状态
哪些后续准备删除
```

---

## J. Source of Truth Report

必须明确：

```text
Character Identity → ?
Character Current State → ?
Location → ?
Activity/Action → ?
Mode → ?
Inventory → ?
Pet State → ?
World Time → ?
Relationship → ?
```

每一项只能有一个 authoritative source。

---

## K. Tests

至少汇报：

```text
Bible Parser Tests
Bible Compiler Tests
Seed Tests
Reset Tests
Isolation Tests
Coverage Tests
Migration Tests
Repository Hygiene Tests
Existing Regression Tests
```

提供：

```text
passed
failed
skipped
```

以及失败原因。

---

## L. Git / Repository Audit

明确：

```text
是否发现敏感文件
是否发现运行数据
是否发现数据库
是否发现日志
是否发现密钥
是否发现真实人物档案
是否发现不必要文件
```

如果发现：

只报告，不擅自执行危险历史清理。

---

## M. Remaining Problems

必须列出：

```text
Phase 2 需要解决什么
当前哪些联动仍未实现
哪些地方仍然是 mock / hardcoded
```

---

# 七十七、Phase 1 完成条件

只有当以下条件全部满足，才算 Phase 1 完成：

```text
✅ Character Bible 可以读取当前人物档案
✅ 中文自然语言档案可以正常解析
✅ Character Definition 成功生成
✅ CharacterEntity 从 Character Definition 初始化
✅ Pet 从 Character Definition 初始化
✅ Space 从 Character Definition 初始化
✅ Object 从 Character Definition 初始化
✅ Inventory 从 Character Definition 初始化
✅ Mode 从 Character Definition 初始化
✅ Action / Project / Social Space 从 Character Definition 初始化
✅ 旧角色 Persona 已隔离
✅ 旧 Memory 已隔离
✅ 旧 Relationship 已隔离
✅ 旧 Continuity 已隔离
✅ 旧 Sandbox 已隔离
✅ Character State 只有一个 authoritative source
✅ Sandbox State 只有一个 authoritative source
✅ Legacy World 不再作为新角色生活状态的写入来源
✅ Coverage Report 可追踪
✅ Git 仓库没有新增敏感数据
✅ 当前既有测试没有明显回归
```

---

# 七十八、最后的硬性要求

在你完成以上任务前：

**不要开始 Phase 2。**

不要自行继续实现：

```text
Entity Interaction
External Influence
Memory Feedback
QQ Influence
```

先把：

> **Character Bible → Character Definition → Sandbox Seed → Runtime**

这一条链完全做正确。

因为后面的所有系统：

```text
Pet Interaction
QQ Influence
Memory
Relationship
Emergent Behavior
```

全部建立在这条链之上。

如果人物档案没有真正进入 Sandbox：

后面做再多联动都只是建立在硬编码角色之上。

---

# 最终目标

本阶段最终得到的不是：

> “一个更会解析 Prompt 的系统。”

而是：

> **一个可以仅依靠 Character Bible 创建角色生活世界的 Sandbox Foundation。**

即：

```text
换一份人物档案
↓
重新初始化
↓
出现另一个不同角色
↓
不需要修改 Sandbox 核心代码
```

完成后停止。

等待下一阶段指令。