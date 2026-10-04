# CatooBot v2.1 Phase 10 · Social Experience & Shared Memory Continuity

## 0. 基线与任务定位

当前基线：

```text
HEAD: ecc40e38a96edf4fa12ddeb73c820463ffa00383
Phase: 9.1.1
Tests: 1249 passed
CI: success
```

本阶段正式进入：

# Phase 10 · Social Experience & Shared Memory Continuity

核心目标：

> 让角色不仅“记得自己做过什么”，还能够对“和某个人一起经历过什么”形成稳定、可召回、可追溯的社会经历连续性。

本阶段不是创建新的 Memory 系统，也不是创建新的 Relationship 系统。

必须建立在现有链路上：

```text
Sandbox verified fact
    ↓
Experience
    ↓
MemoryCandidate
    ↓
existing MemoryRepository
    ↓
CognitiveContext
```

并进一步把：

```text
person_id
shared activity
duration
commitment
provenance
```

真正串成一条可追溯的“共同经历”链。

---

# 1. 本阶段要解决的问题

当前系统已经能够表达：

```text
“她和某人有一个约定”
“约定履行了”
“关系发生了变化”
“这件事成为了一条 social memory”
```

但还不足以稳定表达：

```text
“她和这个人真的一起玩过什么”
“那次共同活动持续了多久”
“这是一次真实发生过的共同经历，而不是单纯一次 promise outcome”
“下次这个人回来时，她可以从过去共同经历中自然获得上下文”
```

尤其要避免这种情况：

```text
Person A + commitment fulfilled
→ 只记住“守约了”

却没有：

“和 A 一起玩了 Minecraft 30 分钟”
“这次共同活动具体是什么”
“这次经历来自哪一个真实 ActionInstance / SocialInteractionFact”
```

Phase 10 的重点就是补齐这一层。

---

# 2. 核心设计原则

## 2.1 Sandbox 仍然是唯一世界事实来源

共同经历只能来自已经验证的 Sandbox fact。

允许来源：

```text
SOCIAL_INTERACTION(shared_activity)
COMMITMENT_FULFILLED
已存在的 verified SocialInteractionFact
```

禁止来源：

```text
LLM 猜测
聊天文本直接生成经历
Memory 反推世界发生过什么
Relationship 数值变化反推共同活动
```

Memory 永远不能反向改变 Sandbox。

---

# 3. 不允许创建第二套 Experience 系统

这是本阶段最重要的架构限制之一。

禁止：

```text
SharedExperienceManager
CollectiveMemoryManager
SocialMemoryManager
RelationshipMemoryManager
```

这类与现有 Experience / Memory 平行的新基础设施。

优先扩展：

```text
app/sandbox/experience.py
app/sandbox/memory_foundation.py
app/sandbox/cognitive.py
```

必要时可以少量修改：

```text
app/sandbox/runtime.py
app/sandbox/relations.py
app/sandbox/commitments.py
app/sandbox/store.py
```

但必须复用现有：

```text
ExperienceRecord
ExperienceBuilder
MemoryCandidate
MemoryCandidateBuilder
SandboxMemoryStore
MemoryRepository
CognitiveContext
```

不要重复实现已有能力。

---

# 4. Phase 10 的核心概念：Shared Social Experience

不要新建独立 Experience 模型。

在现有 `ExperienceRecord` 体系内增加“共同经历”的可表达能力。

可以根据实际代码选择：

```text
ExperienceKind.shared_activity
```

或者使用现有 ExperienceKind + 明确的 social metadata。

但最终外部语义必须能够明确识别：

```text
这是一次角色与某个人共同经历的真实事件
```

至少必须能够追溯：

```text
character_id
person_id
activity
duration_minutes
commitment_id（如果存在）
action_id
action_instance_id（如果存在）
source_event_ids
correlation_id
causation_id
timestamp
```

其中：

```text
person_id
```

必须是 Phase 8 的稳定 PersonIdentity。

绝不能使用：

```text
QQ ID
message sender string
display_name
```

作为长期身份主键。

---

# 5. Shared Activity 的事实来源

Phase 9 已经定义：

```text
SOCIAL_INTERACTION
interaction_type = shared_activity
```

并且事实中可以存在：

```text
duration_minutes
target_activity
commitment_id
interaction_id
```

本阶段必须直接复用这条事实链。

对于：

```text
duration_minutes < MIN_FULFILL_DURATION_MINUTES
```

的 shared activity：

```text
不视为一次完整共同经历
```

不要创建长期社会记忆。

注意：

不要重新定义一个新的 duration threshold。

应复用 Phase 9 已有：

```text
MIN_FULFILL_DURATION_MINUTES
```

如果为了避免依赖环需要移动常量，允许将它移动到一个中立的已有工具位置，但禁止复制出第二份阈值。

---

# 6. Commitment-bound 与 informal shared activity 都应该支持

必须支持两种路径。

## 路径 A：有 Commitment

例如：

```text
今晚一起打 Minecraft
↓
Commitment
↓
Goal
↓
ActionInstance
↓
SOCIAL_INTERACTION(shared_activity)
↓
COMMITMENT_FULFILLED
```

最终共同经历必须能够关联：

```text
person_id
commitment_id
action_id
action_instance_id
duration
```

这条路径的身份链必须是精确的。

---

## 路径 B：没有 Commitment

例如：

```text
某人临时上线
↓
直接一起玩了 30 分钟
↓
没有事先约定
```

只要 Sandbox 已经产生：

```text
verified SOCIAL_INTERACTION(shared_activity)
duration >= minimum
```

也应该能够形成：

```text
Shared Social Experience
```

但是：

```text
不得因此自动创建 Commitment
不得自动创建 Goal
不得自动改变 Relationship
```

这只是已经发生过的一次共同经历。

---

# 7. 同一个行为链禁止生成重复 Experience

这一点必须保持 Phase 4 的原则：

> 同一个行为链不能因为多个事件而制造一堆近似重复经历。

例如：

```text
ACTION_COMPLETED
SOCIAL_INTERACTION(shared_activity)
COMMITMENT_FULFILLED
RELATIONSHIP_CHANGED
```

可能全部来自同一次共同活动。

不要最终产生：

```text
经验 1：完成了打游戏
经验 2：和某人一起打游戏
经验 3：约定履行了
经验 4：关系变好了
```

然后全部作为四个高度相似的长期记忆。

应该利用现有：

```text
correlation_id
causation_id
source_event_ids
```

将同一个行为链进行聚合。

最终 Experience 必须尽可能成为：

```text
一次真实共同经历
```

而不是：

```text
一串事件日志
```

---

# 8. Shared Experience 的信息优先级

如果同一行为链同时拥有：

```text
Action
Shared Activity
Commitment outcome
Relationship change
```

共同经历应保留最有用的社会上下文。

例如最终 Experience 应能够表达类似：

```text
和 Person A 一起进行了 gaming，持续 35 分钟；
这次活动履行了 cm_xxx 的约定；
来源 ActionInstance = ai_xxx。
```

但注意：

这只是结构化事实。

不要在代码中生成：

```text
“她很开心”
“她觉得和这个人关系更好了”
“这是她最珍贵的一次回忆”
“她很喜欢和这个人一起玩”
```

这些属于模型叙事或 Relationship 层，不能由 Phase 10 硬编码推导。

---

# 9. Shared Experience 的重要性规则

共同活动必须区分：

```text
发生过
```

与：

```text
值得长期记住
```

因此：

## 所有真实共同活动

可以进入 Experience 层。

但：

## 并非所有共同活动都必须进入长期 Memory

使用 deterministic rule。

推荐规则：

### 普通短共同活动

```text
达到 minimum duration
但没有其他社会意义
→ Experience
→ MemoryCandidate importance < promotion threshold
→ 不进入长期 Memory
```

### 有明确 Commitment 的共同活动

```text
真实履行 commitment
→ Experience importance 提升
→ 可以成为 social memory
```

### meaningful / major social interaction

```text
→ importance 提升
→ 可以成为 social memory
```

### 长时间共同活动

可以给予 deterministic importance bonus。

但必须：

```text
有明确阈值
可测试
不依赖随机
不依赖 LLM
```

不要设计复杂评分系统。

---

# 10. Social Memory 必须包含 Person Context

这是 Phase 10 最重要的 Memory 改造之一。

现在 Memory 已经具备：

```text
character_id
provenance
dedupe_key
```

本阶段必须让共同经历的 Memory 能够明确知道：

```text
person_id
```

建议至少放入：

```python
provenance = {
    ...
    "person_id": "...",
    "activity": "...",
    "commitment_id": "...",
    "action_id": "...",
    "action_instance_id": "...",
}
```

不要因为这个需求给 `memories` 新建一套独立 social-memory 表。

优先复用现有 `Memory.provenance`。

---

# 11. Shared Memory 的内容

长期 Memory 可以形成类似：

```text
和 Person A 一起玩了 Minecraft 35 分钟
```

或者：

```text
和 Person A 一起完成了一次 gaming 活动
```

有 Commitment 时可以体现：

```text
和 Person A 约好的 gaming 活动顺利完成
```

但不要把 Memory 写成角色小说。

Memory 仍然是：

```text
可事实核验
简短
可检索
可追溯
```

---

# 12. Shared Memory 的 Deduplication

必须保证：

## 同一个事件重复 replay

只能产生：

```text
1 个 Experience
1 个 MemoryCandidate
最多 1 个 active Memory
```

不能重复堆积。

---

## 两次真实发生的共同活动

例如：

```text
10 月 1 日一起玩 30 分钟
10 月 3 日一起玩 40 分钟
```

必须允许：

```text
两个不同 Experience
```

并且长期记忆可以是：

```text
两次不同的 episodic social memories
```

不能因为：

```text
same person + same activity
```

就错误 dedupe 成一条。

所以 dedupe identity 必须包含：

```text
episode identity / correlation / source experience
```

而不是简单：

```text
person_id + activity
```

---

# 13. Commitment Memory 与 Shared Memory 不要无脑重复

必须检查：

```text
COMMITMENT_FULFILLED
```

已经会生成：

```text
commitment_outcome
```

本阶段不要简单再复制一条近似相同的：

```text
“和某人履约了”
“和某人一起活动了”
```

两个长期 Memory。

优先方案：

对于同一真实 episode：

```text
Shared Activity Experience
+
Commitment outcome metadata
```

形成一个信息更完整的 social episode。

如果现有结构确实要求两个 candidate，也必须证明：

```text
两个 Memory 的语义不同
```

而不是：

```text
同一句话写两遍
```

新增测试必须防止 social memory explosion。

---

# 14. Person-aware Memory Retrieval

这是 Phase 10 必须真正完成的另一半。

现在 Cognition 已经能够：

```text
current world
relationships
commitments
memories
recent experiences
```

但共同经历必须能够做到：

```text
当前聊天对象是谁
↓
解析为 person_id
↓
优先召回这个 person 的 social memories
```

例如：

```text
当前聊天对象 = Person A
```

则：

```text
Person A 的共同经历
```

应该获得 deterministic retrieval boost。

而：

```text
Person B 的共同经历
```

不应该因为内容恰好相似就排在 Person A 前面。

---

# 15. 不要使用全局关系猜测

必须遵守：

```text
character_id + person_id
```

双重隔离。

如果：

```text
Character A
Person X
```

有共同经历。

而：

```text
Character B
Person X
```

没有。

那么 B：

```text
绝不能召回 A 的 shared memories
```

---

# 16. Retrieval 推荐实现

优先扩展现有：

```text
SandboxMemoryStore.retrieve_relevant()
```

而不是创建：

```text
social_memory_retrieve()
```

等第二套检索系统。

当前已经存在：

```text
keyword
entity
importance
recency
```

的 deterministic scoring。

本阶段可以增加：

```text
person_match
```

作为 social-memory 的 deterministic boost。

例如逻辑：

```text
if memory provenance.person_id == current_person_id:
    social_person_match = 1.0
else:
    social_person_match = 0.0
```

然后把它纳入已有 scoring。

不要直接：

```text
只返回这个人的 Memory
```

因为其他世界事实仍然可能对当前对话有用。

应该是：

```text
Person-specific social memory
    → strong deterministic boost

Current-world fact
    → remains authoritative

Unrelated social memory
    → normal retrieval
```

最终排序仍然完全 deterministic。

---

# 17. CognitiveContext 必须暴露 Person Identity

当前：

```text
relationship_target
```

可能还是平台 handle。

Phase 10 要求在进入 Memory retrieval 前：

```text
QQ / platform handle
↓
PersonIdentity
↓
person_id
↓
canonical display name
```

然后 retrieval 可以同时获得：

```text
person_id
display_name
external_id
```

但：

长期 Memory 关联必须始终使用：

```text
person_id
```

---

# 18. 当前世界优先级不能被改变

必须保留 Phase 5 的原则：

```text
Current World
    >
Continuity
    >
Memory
```

例如：

昨天 Memory：

```text
“和 Person A 一起在家打游戏”
```

今天当前世界：

```text
角色正在睡觉
```

绝不能因为 memory 被召回就让：

```text
current_world
```

变成：

```text
正在打游戏
```

Memory 永远只是 reference。

---

# 19. Memory / Experience 不能直接修改 Relationship

绝对禁止：

```text
Memory retrieval
→ trust +=
→ closeness +=
```

也禁止：

```text
Shared Memory
→ automatically change relationship
```

Relationship 的变化仍然只能来自：

```text
SocialInteractionFact
→ RelationshipUpdateEngine
```

即 Phase 8 的唯一规则入口。

Phase 10 可以让：

```text
共同经历影响“角色看到了什么”
```

但不能绕过 Phase 8 修改：

```text
trust
familiarity
closeness
social_comfort
```

---

# 20. Memory 不得反向创造 Experience

禁止：

```text
看到旧 Memory
→ 创建一个新的 Experience
```

也禁止：

```text
CognitiveContext recall
→ Sandbox state mutation
```

CognitiveContext 仍然只读。

---

# 21. No LLM Rule

Phase 10 的：

```text
Experience aggregation
Memory candidate
importance
dedupe
person matching
retrieval ranking
```

全部 deterministic。

禁止：

```text
LLM 判断“这次算不算共同经历”
LLM 判断“这次回忆是否重要”
LLM 判断“他们关系是否更亲密”
LLM 生成事实
```

LLM 以后可以读取这些上下文进行自然语言表达。

但不能负责构造世界事实。

---

# 22. Character Isolation

所有新增字段和逻辑必须保持：

```text
character_id
```

隔离。

至少测试：

```text
Character A + Person X + shared activity
Character B + Person X + shared activity
```

两套：

```text
Experience
Memory
retrieval
```

完全互不污染。

---

# 23. Persistence

Phase 10 的关键结果必须可重启恢复：

```text
Experience
Memory
provenance
person_id
commitment_id
```

都不能只存在于：

```text
runtime memory
```

测试：

```text
create experience
→ persist
→ restart Runtime
→ retrieve
→ same person context still finds it
```

---

# 24. Replay Safety

必须测试：

```text
同一个 SOCIAL_INTERACTION event replay
```

不会产生：

```text
第二个 Experience
第二个 Memory
第二个 social episode
```

也测试：

```text
同一个 Commitment outcome replay
```

不会重新制造一份新的长期 shared memory。

---

# 25. 重点回归场景

必须新增或保留测试，至少覆盖：

### A. 有 Commitment 的共同活动

```text
Commitment
→ Goal
→ ActionInstance
→ shared_activity
→ fulfillment
→ shared social experience
```

---

### B. 无 Commitment 的共同活动

```text
shared_activity
→ valid social experience
```

但：

```text
no commitment
no goal
```

---

### C. 低于 minimum duration

```text
duration < MIN_FULFILL_DURATION_MINUTES
→ no meaningful shared experience
→ no social memory
```

---

### D. 同一行为链的多事件合并

```text
ACTION_COMPLETED
SOCIAL_INTERACTION(shared_activity)
COMMITMENT_FULFILLED
```

最终只能产生一个 coherent social episode，而不是三四个重复体验。

---

### E. 两次不同共同活动

```text
Episode 1
Episode 2
```

必须能够同时存在。

不能错误 dedupe。

---

### F. Replay

相同事件 replay：

```text
Experience count 不增加
Memory count 不增加
```

---

### G. Person-aware retrieval

```text
当前聊天对象 = Person A

A 的 shared memory
→ ranking boost

B 的 shared memory
→ 不获得 A 的 boost
```

---

### H. Person isolation

不同 person：

```text
A
B
```

共同经历不能串。

---

### I. Character isolation

不同 character：

```text
Character A / Person X
Character B / Person X
```

不得串 Memory。

---

### J. Current World precedence

旧 Memory：

```text
过去和 Person A 一起 gaming
```

当前世界：

```text
角色正在 sleep
```

最终 CognitiveContext：

```text
current_world = 当前真实世界
memory = reference
```

---

### K. Relationship isolation

插入 shared memory：

```text
relationship 不变
```

读取 shared memory：

```text
relationship 不变
```

只有 SocialInteractionFact 才改变 relationship。

---

### L. Restart

Persistence 后：

```text
restart
→ experience / memory / provenance / person link 仍然存在
```

---

# 26. 与 Phase 9 的兼容

Phase 10 不得破坏：

```text
Commitment lifecycle
Goal lifecycle
Goal completion
ActionInstance binding
Reschedule
Ambiguous shared activity
Exact commitment_id
Fulfillment window
Grace period
Character isolation
```

尤其不得重新修改：

```text
CommitmentDetector.match_shared_activity()
```

除非为了接入 Experience 所必须，而且必须是非语义性的适配。

Phase 9 已经完成的严格 matching 规则必须保持。

---

# 27. 与 Phase 8 的兼容

Relationship 规则不重写。

以下机制必须保持原样：

```text
SocialInteractionFact
→ RelationshipUpdateEngine
→ deterministic delta
→ cognitive_revision
```

Phase 10 只能消费：

```text
verified social facts
```

不能替代 RelationshipUpdateEngine。

---

# 28. 与 Phase 4 Memory Foundation 的兼容

继续复用：

```text
MemoryCandidateBuilder
SandboxMemoryStore
MemoryRepository
```

禁止：

```text
new table "social_memories"
new repository
new retrieval engine
new embedding path
```

除非审计后发现现有 schema 完全无法表达需求。

即使真的必须改 schema：

```text
优先 migration + backward compatible
```

禁止破坏已有 memory rows。

---

# 29. 与 Phase 5 CognitiveContext 的兼容

CognitiveContext 仍然：

```text
read-only
deterministic
bounded
no LLM
```

Phase 10 可以增加：

```text
relevant_social_experiences
```

但只有在现有：

```text
recent_experiences
relevant_memories
relevant_relationships
```

无法清晰表达时才增加。

优先复用已有 fields。

不要让 CognitiveContext 无限增长。

---

# 30. 不要提前做 Phase 11

本阶段只完成：

```text
真实共同经历
→ 社交 Experience
→ Social Memory
→ Person-aware recall
```

不要实现：

```text
emotion model
personality evolution
deep attachment model
automatic relationship stage promotion
social graph
friendship ranking
romantic system
self-reflection agent
dream system
diary agent
planner
agent
autonomous social scheduling
```

这些都不是 Phase 10。

---

# 31. 不修改 Character Bible

禁止：

```text
config/character_bible.md
```

参与本阶段行为逻辑改写。

核心代码必须保持：

```text
character-agnostic
```

不要出现：

```python
if character.name == "罐头":
```

也不要硬编码：

```text
空凛
小喵
Minecraft
可乐
```

等角色内容。

---

# 32. 不改 Git History

禁止：

```text
filter-repo
BFG
rebase history rewrite
force push
```

继续使用当前：

```text
mirror
→ commit
→ push
```

工作流。

真实 Character Bible 仍然不能上传到 GitHub。

---

# 33. 推荐实现顺序

不要一次性重写。

按以下顺序实现：

## Step 1

审计现有：

```text
ExperienceBuilder
MemoryCandidateBuilder
SandboxMemoryStore
CognitiveContextBuilder
MemoryRepository
```

确认现有字段和 persistence 能力。

---

## Step 2

实现：

```text
verified shared_activity
→ coherent Experience
```

先把 Experience 层做好。

---

## Step 3

实现：

```text
Experience
→ social MemoryCandidate
```

严格控制 promotion。

---

## Step 4

实现：

```text
person_id-aware provenance
```

---

## Step 5

实现：

```text
CognitiveContext
→ person-aware social memory retrieval
```

---

## Step 6

补：

```text
replay
dedupe
persistence
character isolation
```

---

## Step 7

完整测试：

```text
ruff
ruff format
mypy
pytest
```

---

# 34. 测试要求

基线：

```text
1249 passed
```

要求：

```text
原有 1249 tests 全部保留
+
新增 Phase 10 tests
```

不得为了通过测试删除或弱化旧测试。

测试必须至少覆盖：

```text
shared activity → experience
shared activity → memory
person-aware retrieval
commitment-linked shared experience
informal shared experience
same-episode dedupe
repeated-episode distinction
replay safety
persistence
character isolation
person isolation
current-world precedence
relationship isolation
no-LLM
```

---

# 35. 完成后的质量门槛

必须全部：

```text
ruff                  ✅
ruff format           ✅
mypy                  ✅
pytest                ✅
CI                    ✅
working tree clean    ✅
origin/main == HEAD   ✅
```

不得存在：

```text
新增 flaky test
ignored test
xfail 偷过
skip 偷过
临时 debug code
hardcoded character data
```

---

# 36. Phase 10 完成报告必须包含

最终只报告事实，不要写“感觉完成”。

格式：

```text
# CatooBot v2.1 Phase 10 Report · Social Experience & Shared Memory Continuity

Phase 10 commit:
HEAD:
origin/main:
CI:

tests:
previous tests:
new tests:

## Shared Experience

verified source:
minimum duration:
person_id persistence:
commitment linkage:
action_instance linkage:
correlation / causation:

## Memory

social memory:
promotion rule:
dedupe:
replay safety:
provenance:

## Person-aware Retrieval

person resolution:
person match scoring:
unrelated-person isolation:
current-world precedence:

## Persistence

experience:
memory:
provenance:
restart recovery:

## Character Isolation

passed / failed:

## Architecture

new Experience system: No
new Memory system: No
new Relationship system: No
new Planner: No
new Agent: No
LLM used for fact construction: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Regression

Phase 8:
Phase 8.1:
Phase 9:
Phase 9.1:
Phase 9.1.1:

## Final

Phase 10 complete: Yes / No
Phase 11 entered: No
```

---

# 37. 最终边界

本阶段完成后，我们应该得到：

```text
人物 A
    ↓
真实共同活动
    ↓
Social Experience
    ↓
Social Memory
    ↓
下次 A 出现
    ↓
person_id resolve
    ↓
相关共同经历获得 retrieval boost
    ↓
CognitiveContext
    ↓
LLM 读取这些事实
    ↓
自然地继续这段关系
```

而不是：

```text
聊天记录
    ↓
LLM 猜测
    ↓
“她应该记得”
```

Phase 10 的核心不是让模型“更会编”。

而是让系统真正拥有：

> **“我们一起经历过什么”这条可以验证、持久化、检索、隔离、重启恢复的事实链。**

完成后停止。

不要自动进入 Phase 10.1 / Phase 11。