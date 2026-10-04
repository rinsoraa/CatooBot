# CatooBot v2.1 Phase 8
## Social & Relationship Dynamics

### 一、当前基线

当前最终基线：

`229e28566adbfe7f91ba9ef11a58d6e9e86c6849`

已完成：

- Phase 1 Character Bible → Definition → Seed → Runtime
- Phase 2 Entity Interaction + Event Bus + State Mutation
- Phase 3 External Influence + Wakeup + Interrupt / Resume
- Phase 3 Remediation
- Phase 3.5 State Authority Closure
- Phase 4 Experience / Memory / Continuity
- Phase 4 Remediation
- Phase 5 Cognitive Context
- Phase 5 Remediation
- Phase 6 Cognitive Decision / Intent
- Phase 7 Autonomous Life Loop / Goal
- Phase 7 Remediation
- Phase 7.1 Resume Binding Integrity

当前：

```text
1178 passed
CI success
HEAD = origin/main = remote main
working tree clean
```

---

# 二、本阶段唯一目标

建立：

> **Relationship Dynamics Bridge**

让角色之间的关系成为：

```text
Character
    ↕
External Person / Social Entity
    ↕
Verified Social Interaction
    ↓
Relationship State
    ↓
Cognitive Context
    ↓
Decision weighting
```

同时保持：

```text
Relationship
≠
Persona
≠
Memory
≠
Sandbox World State
```

---

# 三、第一原则：先审计已有 Relationship / Social 系统

Phase 8 开始编码前，必须先审计：

```text
app/character/
app/continuity/
app/core/
app/sandbox/
plugins/chat/
```

搜索：

```text
Relationship
relationship
social cognition
social memory
interaction profile
shared experience
trust
closeness
familiarity
boundary
friend
social space
```

重点找：

```text
v0.9 Social Cognition
RelationshipManager
relationship models
interaction profiles
existing persistence
```

必须优先复用。

### 禁止

重新建立：

```text
RelationshipSystemV2
SocialSystemV2
RelationshipManagerV2
```

本阶段绝对不要出现第二套 Relationship System。

最终报告必须明确：

```text
现有 Relationship subsystem:
现有 Social Cognition:
现有 Relationship persistence:
本 Phase 复用:
本 Phase 新增:
```

---

# 四、Relationship 必须分成“静态关系”与“动态关系”

Character Bible 已经提供：

```text
relationships
```

这是：

> 角色世界中的初始关系设定。

例如：

```text
core friend
trusted person
```

不要覆盖它。

Phase 8 新增：

```text
Dynamic Relationship State
```

回答：

> 现在双方的关系状态是什么。

因此：

```text
CharacterDefinition.relationships
=
initial/canonical relationship

Dynamic Relationship
=
runtime relationship state
```

---

# 五、建立 Relationship State

如果现有模型可以扩展，直接扩展。

如果确实不存在合适结构，再建立等价：

```text
RelationshipState
```

至少包含：

```text
character_id
subject_id
target_id

relation_type

trust
familiarity
closeness
social_comfort

positive_interactions
negative_interactions
interaction_count

last_interaction_at

created_at
updated_at

source
metadata
```

具体字段可复用现有模型。

不要为了追求字段数量而重复已有结构。

---

# 六、Character ID 必须参与隔离

关系必须：

```text
character_id
```

隔离。

同一个人物：

```text
空凛
```

在：

```text
罐头世界
```

与：

```text
阿澈世界
```

应当是两个不同 Relationship State。

禁止：

```text
global relationship map
```

---

# 七、人物身份必须与 QQ ID 分离

这是本阶段重要设计。

Sandbox / Relationship 层不能把：

```text
QQ user_id
```

直接当成：

```text
Person identity
```

建议：

```text
Person / SocialEntity
```

至少支持：

```text
person_id
external_ids
display_name
metadata
```

其中：

```text
external_ids:
  qq:<id>
```

只是外部身份映射。

这样以后可以：

```text
QQ
论坛
Minecraft
其他社交空间
```

映射到同一个 Person。

不要把：

```text
QQ user_id = permanent identity
```

写死。

---

# 八、Relationship 不等于 SocialSpace

当前：

```text
SocialSpace
```

表示：

> 角色在哪里进行社交。

而：

```text
Relationship
```

表示：

> 角色与谁是什么关系。

所以：

```text
QQ group
≠
relationship
```

一个群可以有很多人。

一个人也可以出现在多个 Social Space。

不要混为一个对象。

---

# 九、建立统一 Social Interaction Fact

Relationship 不能直接读取：

```text
QQ 原始消息
```

然后自己猜。

应该接收已经标准化的：

```text
SocialInteractionFact
```

或者等价模型。

至少：

```text
interaction_id
character_id
person_id

interaction_type

source
timestamp

social_space_id

outcome

importance
metadata
```

例如：

```text
message_received
direct_chat
group_interaction
game_invitation
game_played
help_received
help_given
shared_activity
interaction_ignored
invitation_accepted
invitation_declined
```

具体类型按照现有 Social Cognition 体系复用。

---

# 十、Relationship 只能根据“事实”更新

不要：

```text
一条普通消息
→ trust + 10
```

不要：

```text
“哈哈”
→ 更亲密
```

应该：

```text
SocialInteractionFact
    ↓
Relationship Update Rule
    ↓
StateMutation
    ↓
RELATIONSHIP_CHANGED
```

---

# 十一、Relationship Update Rule

建立：

```text
RelationshipUpdateEngine
```

或者复用已有 Social Cognition。

职责：

> 根据已经确定的互动事实更新动态关系。

例如：

### 稳定正向互动

```text
shared_activity
help_given
help_received
successful_game_session
reliable_followup
```

可以：

```text
familiarity ↑
closeness ↑
trust ↑
```

但变化应该：

- 小
- 渐进
- 可追踪
- 可逆

---

# 十二、不要瞬间大幅改变关系

例如：

```text
第一次聊天
```

不能：

```text
陌生人 → 最亲密朋友
```

Relationship 应具有：

```text
inertia
```

关系变化应该是：

```text
many interactions
+
consistent outcomes
↓
gradual state change
```

而不是：

```text
one event
↓
massive score jump
```

---

# 十三、关系维度需要有限、可解释

不要建立几十个：

```text
emotion
attachment
affection
liking
admiration
dependency
...
```

优先保留已有模型中的核心维度。

如果需要新的维度，必须证明：

> 没有它无法表达现有关系行为。

建议核心：

```text
trust
familiarity
closeness
social_comfort
```

其余尽量派生。

---

# 十四、关系更新也必须走 State Mutation

这点与 Phase 3.5 完全一致。

禁止：

```python
relationship.trust += 0.1
```

作为业务 canonical path。

必须：

```text
SocialInteractionFact
      ↓
RelationshipUpdate
      ↓
StateMutation
      ↓
RELATIONSHIP_CHANGED
```

Mutation 至少包含：

```text
target
field
before
after
source
reason
```

---

# 十五、Relationship Event

继续使用：

```text
SandboxEventType
```

不要建立第二 EventBus。

至少新增：

```text
SOCIAL_INTERACTION
RELATIONSHIP_CHANGED
```

如果现有事件体系已经有等价类型，复用。

不要为了“事件更细”一次新增十几个事件。

---

# 十六、Relationship Event 必须有因果链

例如：

```text
EXTERNAL_EVENT_RECEIVED
      ↓
SOCIAL_INTERACTION
      ↓
RELATIONSHIP_CHANGED
```

或者：

```text
ACTION_COMPLETED
      ↓
SHARED_ACTIVITY
      ↓
RELATIONSHIP_CHANGED
```

使用：

```text
causation_id
correlation_id
```

因此可以回答：

> 为什么这一刻 trust 变了？

必须能够追到：

```text
具体互动事实
```

而不是：

```text
LLM 推测
```

---

# 十七、外部 QQ 消息接入

Phase 3 已经有：

```text
ExternalWorldEvent
```

Phase 8 不重新做 QQ adapter。

链应该是：

```text
QQ
 ↓
ExternalWorldEvent
 ↓
SocialInteractionFact
 ↓
Relationship Update
```

Relationship 层不能知道：

```text
OneBot
NapCat
QQ Message object
```

---

# 十八、聊天消息不是全部都算 Interaction

需要明确过滤。

例如：

```text
“嗯”
“哈哈”
“在吗”
```

可能只是：

```text
conversation event
```

不一定产生关系变化。

关系更新必须经过：

```text
interaction significance
```

至少确定性分类：

```text
trivial
normal
meaningful
major
```

不要让任何消息都改变 trust / closeness。

---

# 十九、邀请事件

例如：

```text
空凛：
来打 Minecraft
```

Phase 3 已识别：

```text
game_invitation
```

Phase 8 可以生成：

```text
SocialInteractionFact:
type = game_invitation
person = friend
activity = gaming
```

但：

> **邀请本身不等于邀请被接受。**

只有：

```text
Decision
→ play
```

之后，才能形成：

```text
invitation_accepted
shared_activity
```

因此：

```text
邀请
≠
共同活动
```

这一步必须严格分开。

---

# 二十、关系必须允许“负结果”

例如：

```text
邀请被拒绝
多次失约
不回应
约定后反复取消
```

可以产生：

```text
trust ↓
closeness ↓
social_comfort ↓
```

但仍然：

- 小幅
- 有依据
- 可恢复

不要因为一次拒绝直接变成：

```text
enemy
```

---

# 二十一、Relationship 与 Decision 的关系

Phase 6 已经支持：

```text
DecisionRequest
CognitiveContext
Candidate
IntentProposal
Validator
```

Phase 8 允许 Relationship 进入：

```text
Decision Context
```

例如：

```text
Current:
watch_animation

Candidate:
play_game_with_person
continue_animation
```

Relationship 可以影响：

```text
candidate priority / desirability
```

但：

> Relationship 不可以直接替 LLM 选择 Action。

---

# 二十二、必须保持 Sandbox Authority

例如：

```text
trust = 0.9
```

不能直接产生：

```text
must_accept_invitation
```

只能：

```text
Candidate score ↑
```

最终还是：

```text
Decision Gate
→ Decision
→ Validator
→ Action
```

---

# 二十三、Relationship 与 Cognitive Context

Phase 5 已有：

```text
CognitiveContext
```

增加：

```text
relevant_relationships
```

或等价字段。

只放当前相关的人。

例如：

```text
用户 = 空凛
```

上下文包含：

```text
与当前角色关系状态
```

而不是：

```text
数据库所有关系
```

---

# 二十四、Relationship Context 预算

最多：

```text
当前对话者
+
当前 Social Event actor
+
当前 Goal / Action 涉及人物
```

避免：

```text
20 个网友
+
30 个群友
+
所有历史关系
```

全部进入 Prompt。

---

# 二十五、Relationship 不等于 Memory

Relationship：

```text
现在和这个人是什么关系
```

Memory：

```text
以前发生过什么
```

允许：

```text
Memory
→ Context
```

但本阶段禁止：

```text
Memory
→ 自动改变 Relationship
```

否则：

> 记忆层会反向控制世界关系。

关系必须来自：

```text
verified SocialInteractionFact
```

---

# 二十六、Relationship 与 Continuity

Continuity 可以包含：

```text
important active relationships
```

例如：

```text
core friend
recently interacted
relationship trend
```

但不要把完整 Relationship DB 塞进 Snapshot。

只保留：

```text
重要 / 最近 / 当前相关
```

---

# 二十七、Relationship 与 Memory

当发生：

```text
major relationship change
```

例如：

```text
关系明显升高
重大冲突
关系恢复
首次建立重要关系
```

可以：

```text
RELATIONSHIP_CHANGED
↓
Experience
↓
Memory Candidate
```

普通：

```text
trust 0.50 → 0.51
```

不应该产生长期 Memory。

继续复用 Phase 4 ExperienceBuilder 的 significance 机制。

---

# 二十八、Relationship 初始状态

初始关系来自：

```text
CharacterDefinition.relationships
```

不要硬编码：

```text
if person == "空凛"
```

应该：

```text
Bible
→ CharacterDefinition
→ initial Relationship State
```

Phase 8 负责：

```text
initial
→ dynamic updates
```

---

# 二十九、不要修改 Character Bible

Character Bible 继续是：

> Canonical Character Source。

Relationship runtime state 不能回写 Bible。

即：

```text
Bible = 初始定义
Runtime Relationship = 当前状态
```

完全分离。

---

# 三十、Relationship persistence

如果已有 relationship DB：

> 扩展现有 persistence。

如果没有动态状态能力：

> 添加最小 migration。

至少：

```text
character_id
person_id
relation_type
trust
familiarity
closeness
social_comfort
updated_at
```

索引：

```text
character_id + person_id
character_id + updated_at
```

不要重做整个 relationship database。

---

# 三十一、Identity Mapping

建立稳定：

```text
PersonIdentityResolver
```

或复用已有 identity layer。

例如：

```text
qq:12345
minecraft:Steve123
web:user_abc
```

可以最终映射：

```text
person_001
```

但本阶段只要求：

```text
QQ
```

作为实际入口。

接口必须可扩展。

---

# 三十二、Relationship 更新的确定性测试

至少：

### Test 1

第一次普通消息：

```text
→ SocialInteractionFact
→ 不明显改变关系
```

### Test 2

多次有意义互动：

```text
→ familiarity / closeness 渐进增加
```

### Test 3

一次重大正向互动：

```text
→ 小幅 trust increase
```

### Test 4

邀请未接受：

```text
→ 不产生 shared_activity
```

### Test 5

邀请接受：

```text
→ shared_activity
→ relationship update
```

### Test 6

多次负向事件：

```text
→ trust / closeness 渐进下降
```

### Test 7

Character isolation

```text
Character A
person X

Character B
person X
```

两者完全隔离。

### Test 8

Relationship affects candidate ranking

但：

```text
does not execute action
```

### Test 9

Current Relationship enters CognitiveContext

只出现相关人物。

### Test 10

Relationship change → Experience

重大变化可以产生 Experience。

普通 0.01 变化不污染 Memory。

---

# 三十三、必须测试“邀请 ≠ 接受”

专门增加：

```text
TestInvitationIsNotAcceptance
```

流程：

```text
game_invitation
```

之后：

```text
relationship state
```

不能直接出现：

```text
shared_activity
```

只有：

```text
Decision
→ play
→ ACTION_STARTED / ACTION_COMPLETED
```

之后才能产生：

```text
shared_activity
```

---

# 三十四、Relationship 与 Phase 6 的完整链

至少实现：

```text
ExternalWorldEvent
        ↓
game invitation
        ↓
SocialInteractionFact
        ↓
Relationship Context
        ↓
DecisionCandidate
        ↓
LLM / deterministic Decision
        ↓
accept / decline
        ↓
Action
        ↓
Outcome
        ↓
Relationship Update
```

这样才能真正形成：

> **社交事件 → 决策 → 行动 → 社交结果 → 关系变化**

而不是：

```text
收到消息
→ trust + 0.1
```

---

# 三十五、不要让 LLM 直接决定 Relationship 数值

禁止：

```text
LLM:
trust = 0.87
closeness = 0.92
```

LLM 可以：

```text
interpret semantic social intent
```

但真正数值变化：

```text
InteractionFact
→ deterministic rule
→ Mutation
```

这样可测试、可解释。

---

# 三十六、不要建立“情绪系统”作为 Phase 8 副产品

本阶段不做：

```text
Mood Engine V2
Emotion Engine
Attachment Engine
```

如果现有情绪字段存在：

> 只复用，不扩展。

Relationship 是长期社会状态。

Mood 是短期状态。

两个概念不要混。

---

# 三十七、Goal 与 Relationship

Goal 可以由：

```text
social obligation
```

触发。

例如：

```text
答应陪某人玩
```

但本阶段不要建立：

```text
Relationship → 自动 Goal
```

作为普遍规则。

先完成：

```text
social fact
→ relation update
```

以后 Phase 9 再考虑：

```text
promise / obligation / social goal
```

---

# 三十八、Memory 与 Relationship

严格保持：

```text
Relationship
← SocialInteractionFact
```

而不是：

```text
Relationship
← Memory retrieval
```

Memory 可以记录：

> “那次一起打完了 Minecraft。”

但不能因为记忆被检索出来就再次增加 trust。

---

# 三十九、工程边界

本阶段禁止：

- 新 EventBus
- 新 Mutation System
- 新 Memory System
- 新 Goal System
- 新 Decision System
- 新 Planner
- 新 Agent
- 新 World System
- Embedding
- Vector Search
- LLM relationship scoring
- Memory → Relationship automatic updates
- Relationship → direct Sandbox mutation
- Character Bible rewrite
- QQ adapter rewrite
- Git history rewrite

---

# 四十、必须保持目前双目录工作流

开发：

`E:\WorkSpace ZCode\CatooBot`

发布：

`E:\WorkSpace ZCode\CatooBot_github`

发布前扫描：

- API key
- Token
- `.env`
- local DB
- logs
- private Zcode workflow
- true Character Bible
- local paths
- secrets

不得上传：

`E:\WorkSpace ZCode\CatooBot\config\character_bible.md`

不得：

```text id="p3ly0d"
git filter-repo
BFG
force push
history rewrite
```

---

# 四十一、测试规模要求

当前：

`1178 tests`

必须：

```text
原 1178 全保留
+
Phase 8 tests
=
全部通过
```

执行：

```powershell id="g49c7j"
ruff check .
ruff format --check .
mypy app
pytest -q
```

CI 必须 success。

---

# 四十二、最终架构目标

Phase 8 完成后：

```text
                       External World
                             ↓
                    ExternalWorldEvent
                             ↓
                    SocialInteraction
                             ↓
                    Relationship State
                             ↓
                  ┌──────────┴──────────┐
                  ↓                     ↓
           Cognitive Context       Experience
                  ↓                     ↓
              Decision              Memory
                  ↓
               Action
                  ↓
             Social Outcome
                  ↓
         Relationship Update
```

形成真正闭环：

```text
互动
 ↓
关系
 ↓
认知
 ↓
决策
 ↓
行为
 ↓
结果
 ↓
关系再次变化
```

---

# 四十三、最终报告

必须报告：

```text
Phase 8 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

现有 Relationship subsystem:
<文件 + 职责>

现有 Social Cognition:
<文件 + 职责>

Relationship State:
<文件 + 模型>

SocialInteractionFact:
<文件 + 模型>

Person Identity:
<文件 + 模型>

Relationship persistence:
<表 + migration>

Relationship mutation:
<canonical helper>

Relationship events:
<event types>

QQ → SocialInteraction:
<入口>

Invitation ≠ Acceptance:
<测试>

Accepted shared activity:
<测试>

Relationship → Decision:
<如何进入 candidate/context>

Decision → Social Outcome:
<如何形成事实>

Social Outcome → Relationship:
<如何更新>

Memory:
<是否仅接重大关系变化>

Character isolation:
<测试>

LLM：
<是否直接改变 relationship>
必须 No

Memory → Relationship：
必须 No

Relationship → direct Sandbox mutation：
必须 No

是否修改 Character Bible：
No

是否进入 Phase 9：
No
```

完成后停止。

---

# 四十四、最重要的原则

Phase 8 不应该变成：

```text
收到 QQ 消息
    ↓
模型觉得这个人很重要
    ↓
trust += 0.2
```

真正应该变成：

```text
外部事实
    ↓
发生了什么互动
    ↓
角色做了什么选择
    ↓
实际结果是什么
    ↓
根据结果更新关系
    ↓
关系进入后续认知
```

也就是：

> **关系不是 Prompt 里写出来的属性，而是长期生活过程中被“经历”出来的状态。**

本阶段完成后停止，不进入 Phase 9。