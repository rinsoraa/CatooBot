# CatooBot v2.1 Phase 11 · Social Cognition & Conversational Continuity

## 0. 基线

当前最终基线：

```text id="q7m0xr"
HEAD: 6a602c401dd857491bd2bc5b99e4f9d9220beef2
Phase: 10.2
Tests: 1277 passed
CI: success
```

正式进入：

# Phase 11 · Social Cognition & Conversational Continuity

本阶段不是：

```text
Phase 10.3
```

也不是 Memory / Relationship / Commitment 的继续扩张。

---

# 1. Phase 11 的核心目标

Phase 8～10 已经解决：

```text id="m1g4nx"
这个人是谁
↓
和这个人的关系是什么
↓
现在对这个人有什么社会义务
↓
过去和这个人一起经历过什么
↓
这些经历如何持久化
```

现在还缺少：

```text id="v7q2kd"
当这个人再次出现时，
系统到底应该把什么带进“这一轮认知”？
```

当前 `CognitiveContext` 已经有：

```text
person
relevant_relationships
relevant_commitments
relevant_memories
recent_experiences
continuity
retrieval
```

本阶段要做的不是继续增加大量字段，而是：

> **把这些信息组织成“当前这一轮社交认知状态”。**

目标：

```text id="q0p4k9"
Current Person
    ↓
Current Social Situation
    ↓
Relevant Relationship State
    ↓
Open Commitments
    ↓
Relevant Shared Memories
    ↓
Recent Shared Experiences
    ↓
Current World
    ↓
当前这一轮 Cognition
```

最终让 LLM 看到的是：

```text
“我正在和谁互动”
“我们现在处于什么关系状态”
“我们之间有没有未完成的事”
“过去哪些共同经历与当前话题相关”
“现在世界里实际上发生什么”
```

而不是一堆互相平级的数据库字段。

---

# 2. 核心原则

## 2.1 CognitiveContext 仍然是只读模型

绝对保持：

```text id="2u4m0m"
CognitiveContext
    = read-only projection
```

它不能直接修改：

```text
Sandbox
Relationship
Commitment
Goal
Memory
Experience
```

---

# 3. 不创建第二套 Social Context 系统

禁止：

```text id="x0ot5w"
SocialContextManager
SocialStateManager
ConversationMemoryManager
SocialThreadManager
RelationshipContextManager
```

不要建立一个与 `CognitiveContext` 平行的新系统。

优先扩展：

```text id="m3fr0c"
app/sandbox/cognitive.py
```

以及必要的：

```text id="1v7nac"
app/sandbox/continuity_snapshot.py
app/sandbox/memory_foundation.py
app/sandbox/relations.py
app/sandbox/commitments.py
```

但：

```text id="y4qvra"
CognitiveContext
```

仍然是唯一的认知投影。

---

# 4. Phase 11 不重新定义 Person

继续使用：

```text id="91kd0w"
PersonIdentity.person_id
```

平台 handle 只能用于 resolve：

```text
QQ / external id
    ↓
PersonIdentity
    ↓
person_id
```

所有：

```text
memory
relationship
commitment
shared experience
```

仍使用 `person_id`。

---

# 5. 新增核心概念：Current Social Situation

不要创建新系统。

在现有 `CognitiveContext` 内增加一个**非常小的结构化投影**：

```python id="3f2p8d"
social_situation
```

或者等价字段。

它至少应该能够表达：

```text id="m7x0zq"
person_id
relationship summary
open commitments summary
recent relevant shared experiences
relevant shared memories
social continuity state
```

推荐形式：

```python id="w59c2v"
social_situation = {
    "person_id": "...",
    "relationship": {...},
    "open_commitments": [...],
    "recent_shared_experiences": [...],
    "relevant_shared_memories": [...],
}
```

它不是一个新的数据库对象。

只是：

```text
CognitiveContext
```

的 read-model projection。

---

# 6. 为什么需要这个结构

当前 CognitiveContext 虽然已经有：

```text
relationships
commitments
memories
experiences
```

但这些数据之间没有明显的社会上下文层次。

例如：

```text
Person A
Relationship:
    familiarity = 0.8

Memory:
    和 A 一起玩过 Minecraft

Commitment:
    周五一起看电影

Experience:
    昨天一起玩了 40 分钟
```

LLM 得到的是四组平行信息。

Phase 11 希望认知模型看到：

```text
与 A 的当前社交上下文：

- 我们已经比较熟
- 昨天刚一起玩过
- 有一件未来约定
- 当前这个话题与昨天经历存在关联
```

注意：

这只是事实之间的组织方式。

**禁止在代码中生成心理结论。**

---

# 7. 不允许硬编码“亲密叙事”

禁止产生：

```text id="b17v6q"
“她很喜欢这个人”
“她很想见这个人”
“她特别依赖这个人”
“她觉得这是自己的好朋友”
“她很想念这个人”
```

除非这些内容已经存在于合法的 Persona / Relationship / Memory 数据中。

系统只能提供：

```text
事实
状态
来源
时间
关联
```

LLM 才负责自然语言层的表达。

---

# 8. 当前 Social Situation 的事实优先级

必须遵循：

```text id="b4i2vf"
Current World
    >
Current Relationship State
    >
Open Commitment
    >
Recent Experience
    >
Relevant Memory
```

解释：

### Current World

现在角色在做什么。

例如：

```text
正在睡觉
```

永远不能被过去 Memory 覆盖。

### Relationship

当前最新 relationship state。

### Commitment

当前仍未完成的社会义务。

### Experience

最近真实发生过什么。

### Memory

长期历史参考。

---

# 9. Memory 和 Experience 的区别必须保持

例如：

```text id="r4w9bm"
Memory:
“和 A 一起玩过 Minecraft 40 分钟。”

Experience:
昨天 20:00～20:40 的一次具体 episode。
```

Memory：

```text
长期 reference
```

Experience：

```text
具体 episode
```

不得互换。

---

# 10. Social Situation 的 Episode 关联

如果同时存在：

```text id="6yq2is"
Experience:
action:act_123

Memory:
shared:person:action:act_123
```

CognitiveContext 最终应该能够知道：

```text
这条 Memory 与哪一次 Experience 属于同一 episode
```

优先利用已经存在的：

```text id="q9k4w2"
provenance.episode_key
```

不要新建 episode identifier。

Phase 10.1 / 10.2 的 canonical identity 必须继续作为唯一来源。

---

# 11. 当前轮的“相关性”必须 deterministic

本阶段允许构建：

```text id="f8j1k4"
relevant_shared_memories
relevant_shared_experiences
```

但必须使用确定性规则。

推荐：

```text
当前 person 匹配
>
当前 commitment 关联
>
当前话题实体匹配
>
最近发生
>
importance
```

而不是：

```text
LLM 判断“这条回忆应该相关”
```

---

# 12. Shared Memory relevance

已有 Phase 10：

```text
person_match
```

继续保留。

不要修改当前 scoring 权重：

```text id="s7c1m2"
keyword: 0.36
entity: 0.12
importance: 0.20
recency: 0.12
person_match: 0.20
```

本阶段不重新调整这些权重。

---

# 13. Shared Experience relevance

当前：

```text id="m2x7s6"
recent_experiences
```

是全局 recent experience。

Phase 11 可以增加 deterministic person filter / boost。

例如：

```text id="s9a4u1"
current_person == experience.person_id
```

则作为：

```text
social relevance
```

优先。

但不要简单地：

```text
只返回当前 person 的全部 experience
```

应该继续遵守：

```text
limit
importance
recency
```

边界。

---

# 14. 当前话题与共同经历

假设：

```text id="f9r0e5"
Person A
```

过去：

```text
共同玩 Minecraft
```

当前 query：

```text
“昨天那个房子”
```

那么：

```text
Minecraft shared experience
```

应该比：

```text
与 A 的一次 unrelated snack experience
```

更优先。

但：

```text
current_world
```

仍然优先。

---

# 15. Open Commitment 的优先级

当前与 Person A 互动：

```text id="r1k3f2"
open commitment
```

则必须显式进入 Social Situation。

例如：

```text
“周五一起看电影”
```

这样 LLM 能看到：

```text
当前人与 A 的未完成社会事务
```

但：

```text
Memory
```

不能自动重新激活 Commitment。

---

# 16. Commitment 永远不能被 Memory 重新激活

禁止：

```text id="h2v6ax"
Memory:
“曾经约过电影”

↓
重新创建 open Commitment
```

必须仍然：

```text id="f0h9cs"
Commitment lifecycle
```

唯一真实来源。

---

# 17. Relationship 不因为 Context 构造而变化

以下操作：

```text id="j1m9w7"
build cognitive context
retrieve memory
construct social situation
```

全部：

```text
world_revision 不变
cognitive_revision 不变
relationship 不变
```

---

# 18. 当前 Social Situation 不持久化

不要建表：

```text id="e6g0ko"
social_situations
```

它应该是：

```text
deterministic read model
```

实时从：

```text
Sandbox
Relationship
Commitment
Experience
Memory
```

构造。

重启后重新构造即可。

---

# 19. Conversation Turn Identity

本阶段允许为**当前 cognitive build** 引入一个轻量 turn identity：

```text id="q7h3nc"
query
relationship_target
person_id
```

但：

**不要把每一句聊天都写入数据库。**

Phase 11 不负责完整 Conversation History 系统。

---

# 20. Interaction Target Resolution

当前已经有：

```text
relationship_target
→ PersonIdentity
```

必须进一步保证：

```text id="u5d1mz"
relationship_target
resolved person_id
social_situation.person_id
memory person_match
relationship.person_id
commitment person_id
```

全部一致。

禁止：

```text
A:
external_id = QQ123

Memory:
person_id = B
```

这样的 silent mismatch。

---

# 21. Identity mismatch 安全规则

如果：

```text id="2x8n5f"
relationship_target
```

无法可靠解析为 PersonIdentity：

则：

```text
person = {}
social_situation = {}
person-specific memory boost = 0
relationship = []
commitments = []
```

不能猜。

尤其禁止：

```text
根据 display_name 猜 person
根据历史 message 猜 person
根据 Memory 猜 person
```

---

# 22. Social Continuity Summary

允许在 `social_situation` 中生成一个 deterministic summary，例如：

```text
{
    "has_open_commitments": true,
    "recent_shared_experience_count": 2,
    "relevant_shared_memory_count": 3
}
```

但不要生成：

```text
"relationship_feeling": "亲密"
```

除非这是已有明确字段的 projection。

---

# 23. “Recently happened” 定义

Phase 11 必须使用统一的时间窗口。

不要在不同模块中出现：

```text
30min
2h
24h
3days
```

各自硬编码。

优先复用：

```text
runtime clock
existing config
existing recency scoring
```

如确需新增配置：

```text
config.social_context_recent_window
```

必须只有一个来源。

---

# 24. Bounded Context

Social Situation 必须有严格上限。

推荐：

```text id="r9x1da"
open_commitments <= 3
recent_shared_experiences <= 3
relevant_shared_memories <= existing memory_context_limit
```

不要：

```text
把一个人的所有历史 Memory 都塞给模型
```

---

# 25. Context 不得无限扩大

保持：

```text id="c4y7mj"
memory_context_max_chars
```

并确保新增：

```text
social_situation
```

也受到整体 context budget 控制。

推荐：

```text
current_world
+
social_situation
+
continuity
+
memories
```

共同受 bounded limits 管理。

---

# 26. Social Context 的输出结构

建议最终 CognitiveContext payload 类似：

```json id="2nj8fh"
{
  "person": {
    "person_id": "...",
    "display_name": "...",
    "external_id": "..."
  },
  "social_situation": {
    "relationship": {...},
    "open_commitments": [...],
    "recent_shared_experiences": [...],
    "relevant_shared_memories": [...],
    "continuity": {
      "has_open_commitments": true,
      "recent_shared_experience_count": 1
    }
  },
  "current_world": {...},
  "relevant_memories": [...],
  "recent_experiences": [...]
}
```

注意：

`continuity` 本身已经存在于 CognitiveContext。

不要创建第二个 continuity system。

上面的：

```text
social_situation.continuity
```

只是社会语境 summary，不是新的 ContinuitySnapshot。

---

# 27. 不要删除现有 fields

Phase 11 不能为了新结构而删除：

```text id="z8y3nj"
relevant_relationships
relevant_commitments
relevant_memories
recent_experiences
person
retrieval
continuity
```

因为现有调用方可能直接使用这些字段。

可以新增：

```text
social_situation
```

但保持 backward compatibility。

---

# 28. DecisionCoordinator 的边界

Phase 11 可以让：

```text
DecisionRequest
```

读取更完整的 Social Situation。

但：

```text id="j2k5w4"
DecisionCoordinator
```

仍然只负责：

```text ambiguous decision
```

不要让它变成：

```text conversation manager
```

---

# 29. LLM 的职责

LLM 可以：

```text id="w4n6jf"
读取 Social Situation
理解当前聊天
生成候选 intent
选择行动
生成自然语言
```

LLM 不可以：

```text id="b9k1q3"
创建 person
修改 relationship
创建 commitment
创建 memory
创建 experience
声明过去发生过不存在的共同活动
修改 current world
```

---

# 30. Fact Construction Rule

任何：

```text
experience
relationship
commitment
goal
memory
```

必须继续来自现有 deterministic systems。

LLM 产生的：

```text
“我记得我们昨天玩过”
```

只是语言输出。

不能因为 LLM 说了：

```text
memory
```

就自动写入 Memory。

---

# 31. Social Recall 不自动产生 Memory

例如：

```text
LLM:
“诶，你上次是不是还在做那个建筑？”
```

这只是读取 Memory。

不能因为这个问题：

```text
→ new Memory
```

只有真实 Experience / verified event 才能生成新 Memory。

---

# 32. Social Recall 不自动改变 Relationship

读取：

```text
shared_memory
```

不会：

```text
trust +=
closeness +=
familiarity +=
```

Relationship 仍然只有：

```text
SocialInteractionFact
→ RelationshipUpdateEngine
```

---

# 33. Social Situation 不得修改 Revision

构建上下文：

```text id="f0v2c6"
world_revision unchanged
cognitive_revision unchanged
```

尤其是：

```text
memory retrieval
relationship retrieval
commitment retrieval
```

都必须是 read-only。

---

# 34. Character Isolation

严格：

```text id="xq8m3p"
character_id
+
person_id
```

隔离。

测试：

```text
Character A + Person X
Character B + Person X
```

两边 Social Situation 不得互相污染。

---

# 35. Person Isolation

测试：

```text id="t8k1la"
Person A
Person B
```

A 当前交互：

```text
只获得 A 的 person match
```

B 的 shared memory：

```text
不会因为 query 文字相似直接取得相同社会权重
```

---

# 36. Current World Precedence

必须新增回归：

```text id="qf4o20"
过去：
Person A + gaming memory

现在：
角色正在 sleep

当前 Social Situation:
    memory = gaming
    current_world = sleep
```

最终：

```text
current_world == sleep
```

不能出现：

```text
current_world == gaming
```

---

# 37. Relationship Revision Regression

建立 Context 前后：

```text
world_revision_before == world_revision_after
cognitive_revision_before == cognitive_revision_after
```

---

# 38. Commitment Regression

Context build：

```text id="w7c2pa"
不会 activate commitment
不会 complete commitment
不会 cancel commitment
不会 create commitment
```

---

# 39. Memory Regression

Context build：

```text id="s9g0bf"
不会 create memory
不会 duplicate memory
不会 change memory dedupe
```

---

# 40. Experience Regression

Context build：

```text id="c1v8mz"
不会 create Experience
不会 merge Experience
不会 modify Experience
```

---

# 41. Retrieval Determinism

同样：

```text character
person
query
world
memory state
```

连续 build 两次：

```text id="e3m4xv"
social_situation identical
retrieval identical
memory ordering identical
```

不得随机。

---

# 42. Bounded Ordering

同样数据：

```text A1 recent
A2 old
B1 recent unrelated
```

当前 Person A：

```text A1 > A2
A memories receive person_match
B memories receive no person boost
```

但最终仍然遵循：

```text query
entity
importance
recency
person
```

已有 scoring 不重写。

---

# 43. Tests

新增测试文件：

```text id="p5m1q7"
tests/test_sandbox_social_cognition.py
```

至少覆盖：

### A. Person resolution

```text QQ / external id
→ person_id
→ social_situation.person_id
```

---

### B. Identity failure

无法 resolve：

```text
→ no person-specific context
→ no guessing
```

---

### C. Social situation composition

当前 person：

```text
relationship
commitment
shared experience
shared memory
```

全部正确关联。

---

### D. Current-person memory preference

A 当前对话：

```text
A shared memories
```

获得正确 boost。

---

### E. Person isolation

A context：

```text
B memory
```

不产生 A 的 social boost。

---

### F. Character isolation

不同 character：

```text
same person
```

Social Situation 完全独立。

---

### G. Current-world precedence

过去 shared memory：

```text
gaming
```

当前 world：

```text
sleep
```

world remains sleep。

---

### H. Context build read-only

构建前后：

```text
world_revision
cognitive_revision
commitments
relationships
memories
experiences
```

均不改变。

---

### I. Determinism

同样输入：

```text
two builds
```

输出完全一致。

---

### J. Boundaries

验证：

```text
commitments <= 3
shared experiences <= 3
memory <= configured limit
```

---

### K. Same episode linkage

Memory：

```text
provenance.episode_key
```

可以与对应 Experience：

```text
episode_key
```

对齐。

---

# 44. 不做 Conversation History

本阶段禁止建立：

```text
conversation_messages
chat_history
message_embedding_store
conversation_memory_db
```

也不要实现完整聊天记录持久化。

Phase 11 只构建：

```text
current social cognition
```

---

# 45. 不做 Emotion System

禁止：

```text
mood engine
emotion engine
attachment score
jealousy system
longing system
```

Relationship 现有数值保持。

---

# 46. 不做 Personality Evolution

禁止：

```text
人格自动变化
character trait learning
persona evolution
behavioral trait weights
```

---

# 47. 不做 Social Graph

禁止：

```text
friend graph
friend ranking
network centrality
community detection
```

---

# 48. 不做 Planner / Agent

继续：

```text
Planner: No
Agent: No
```

---

# 49. 不改 Character Bible

保持：

```text
character-agnostic core
```

禁止硬编码：

```text
罐头
空凛
小喵
Minecraft
可乐
```

---

# 50. 不改变 Phase 9 / 10 identity semantics

严格保持：

```text
Experience:
ActionInstance > Commitment > Interaction

Memory:
Experience.episode_key

Commitment:
独立 lifecycle

Relationship:
独立 mutation path
```

---

# 51. Git 安全

继续使用：

```text id="q2r8xn"
E:\WorkSpace ZCode\CatooBot
↓
E:\WorkSpace ZCode\CatooBot_github
↓
commit
↓
push
```

禁止：

```text
force push
history rewrite
filter-repo
BFG
```

真实 Character Bible 不允许进入 public mirror。

---

# 52. 测试基线

当前：

```text id="j8r5y2"
1277 passed
```

要求：

```text
1277 全部保留
+
Phase 11 tests
```

不得通过：

```text
skip
xfail
删除旧测试
弱化断言
```

---

# 53. Quality Gate

必须：

```text
ruff
ruff format
mypy
pytest
CI
```

全部通过。

同时：

```text
HEAD == origin/main
working tree clean
```

---

# 54. 最终报告必须包含

```text id="z1k6n4"
# CatooBot v2.1 Phase 11 Report · Social Cognition & Conversational Continuity

Phase 11 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## Person Resolution

external handle:
person_id:
identity mismatch behavior:

## Social Situation

person:
relationship:
open commitments:
recent shared experiences:
relevant shared memories:

## Relevance

person match:
shared experience ranking:
memory ranking:
determinism:

## Boundaries

max commitments:
max shared experiences:
memory context limit:

## Read-only

world_revision changed: No
cognitive_revision changed: No
relationship changed: No
commitment changed: No
memory changed: No
experience changed: No

## Isolation

character isolation:
person isolation:

## Regression

Phase 8:
Phase 8.1:
Phase 9:
Phase 9.1:
Phase 9.1.1:
Phase 10:
Phase 10.1:
Phase 10.2:

## Architecture

new Social system: No
new Memory system: No
new Relationship system: No
new Conversation database: No
new Planner: No
new Agent: No
LLM used for fact construction: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Final

Phase 11 complete: Yes / No
Phase 12 entered: No
```

---

# 55. Phase 11 的真正完成定义

完成之后：

```text
Person A 发来消息
        ↓
PersonIdentity
        ↓
Current Social Situation
        ├── 当前 Relationship
        ├── 当前 Open Commitment
        ├── 最近 Shared Experience
        └── Relevant Shared Memory
        ↓
Current World
        ↓
CognitiveContext
        ↓
Decision / LLM
```

LLM 不再只是看到：

```text
“这是一个用户”
```

而是能够看到：

```text
“这是 Person A。
我和他现在是什么关系。
我们最近一起发生了什么。
我们有没有没完成的事情。
当前他说的话与过去什么有关。
而此刻我的世界里又正在发生什么。”
```

这就是 Phase 11。

不要把这些事实重新做成第二套系统。

完成后停止，不进入 Phase 12。