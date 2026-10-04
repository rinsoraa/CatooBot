# CatooBot v2.1 Phase 4
## Sandbox Experience → Memory → Continuity Foundation

### 一、当前基线

当前已经完成并封版：

- Phase 1 — Character Bible → Definition → World Seed → Runtime
- Phase 2 — Entity Interaction + Sandbox Event Bus + State Mutation
- Phase 3 — External Influence + Sandbox Wakeup + Action Interrupt
- Phase 3 Remediation
- Phase 3.5 — State Authority Closure

当前基线 commit：

`10e3fd44b7e83c126ad88cf23e93e3bc9a95cfc7`

Phase 3.5 最终状态：

- Need 已进入 canonical mutation
- Project progress 已进入 canonical mutation
- Knowledge 已进入 canonical mutation
- NEED_CHANGED / PROJECT_PROGRESS_CHANGED / KNOWLEDGE_CHANGED 已进入事件体系
- Action effect 已有统一 causation root
- 1077 tests passed
- CI success
- GitHub mirror clean
- 真实 Character Bible 不在公开镜像

---

# 二、本阶段唯一目标

建立：

```text
Sandbox World
      ↓
Experience Recognition
      ↓
Memory Candidate
      ↓
Memory Store
      ↓
Continuity Foundation
```

本阶段解决：

> Sandbox 中真实发生的事情，如何成为以后可以被角色“记得”的信息。

不要在本阶段解决：

> 角色看到 Memory 后如何自然地说出来。

更不要在本阶段解决：

> LLM 如何自动决定她应该记住什么。

先把**可信记忆来源与数据结构**建立起来。

---

# 三、首先审计现有 Memory / Continuity

开始编码之前，必须先检查现有：

```text
app/memory/
app/core/
app/sandbox/
database/
plugins/
```

与 Memory、Semantic Memory、Continuity、Recent Memory、Relationship Memory 有关的现有代码。

特别寻找：

```text
Memory
SemanticMemory
Continuity
memory extraction
memory candidates
memory store
memory search
embedding
relationship memory
recent events
life_moment
note_user_interaction
```

要求：

1. 不得因为 Phase 4 新建第二套 Memory System。
2. 已有能力优先复用。
3. 如果现有 Memory API 不适合 Sandbox Event provenance，只做最小扩展。
4. 不得为了本 Phase 重写整个 Memory subsystem。

完成审计后，在最终报告中列出：

```text
现有 Memory subsystem:
<文件>

现有 Memory 数据模型:
<模型>

现有 Memory persistence:
<存储>

现有 Continuity:
<文件>

本 Phase 复用:
<具体 API>

本 Phase 新增:
<具体 API>
```

---

# 四、核心概念必须分开

这是本阶段最重要的设计约束。

## 1. World State

回答：

> 现在世界是什么样。

例如：

```text
fridge cola = 0
pet hunger = 0.3
character location = convenience_store
project progress = 0.42
```

---

## 2. Knowledge

回答：

> 角色现在知道什么。

例如：

```text
冰箱已经没有可乐
```

Knowledge 是当前认知状态。

---

## 3. Experience

回答：

> 角色经历了什么。

例如：

```text
刚刚喝掉最后一瓶可乐
```

Experience 是事件/行为片段。

---

## 4. Memory

回答：

> 哪些 Experience 值得长期保留。

例如：

```text
昨晚喝掉最后一瓶可乐，后来意识到冰箱空了。
```

Memory 不是事件日志复制品。

---

## 5. Continuity

回答：

> 当前这个角色，在跨时间/重启之后，应该保留哪些连续状态。

例如：

```text
当前项目进度
最近重要经历
已经知道的事情
关系变化
未完成事务
```

所以：

```text
World State
      ≠
Knowledge
      ≠
Experience
      ≠
Memory
      ≠
Continuity
```

禁止把它们混成一个 `memory` dict。

---

# 五、建立 Experience 层

建立统一的：

```text
ExperienceRecord
```

或者等价抽象。

至少包含：

```text
id
character_id
timestamp
event_type
summary
source_event_ids
causation_id
correlation_id
location
actors
importance
```

可以增加：

```text
action_id
interaction_type
external_source
metadata
```

但不要设计成过度庞大的万能事件对象。

---

# 六、Experience 必须来自真实 Sandbox Event

禁止：

```text
LLM
 ↓
自由生成记忆
```

作为 Phase 4 的基础路径。

正确：

```text
SandboxEvent
      ↓
Experience Builder
      ↓
ExperienceRecord
```

例如：

```text
ACTION_COMPLETED
ITEM_CONSUMED
PET_FED
PROJECT_PROGRESS_CHANGED
KNOWLEDGE_CHANGED
EXTERNAL_EVENT_RECEIVED
ACTION_INTERRUPTED
ACTION_RESUMED
```

都可以成为 Experience 的候选来源。

但：

> 并不是每一个 Event 都自动变成长期 Memory。

---

# 七、Experience Builder

建立：

```text
ExperienceBuilder
```

或者等价组件。

负责：

```text
Sandbox Event
      ↓
是否具有 Experience 意义
      ↓
生成 ExperienceRecord
```

至少支持：

```text
ACTION_COMPLETED
ACTION_INTERRUPTED
ACTION_RESUMED
INTERACTION_COMPLETED
PET_FED
PROJECT_PROGRESS_CHANGED
KNOWLEDGE_CHANGED
EXTERNAL_EVENT_RECEIVED
SOCIAL_SPACE_CHANGED
```

但是必须有**噪声过滤**。

例如：

```text
每一次 NEED_CHANGED
每一次 tick
每一次普通 queue operation
每一次内部 debug event
```

不能全部直接进入 Memory。

---

# 八、Memory Candidate

建立：

```text
MemoryCandidate
```

或者等价结构。

建议至少：

```text
candidate_id
character_id
memory_type
summary
source_experience_ids
source_event_ids
importance
confidence
created_at
observed_at
scope
status
dedupe_key
```

其中：

```text
memory_type
```

至少支持：

```text
episodic
semantic
social
world_fact
```

如果现有 Memory 模型已经有类型体系，复用现有定义。

---

# 九、不要让 Memory 复制整个 Event

错误：

```text
Event
→ 完整 JSON
→ 保存成 Memory
```

Memory 应该是：

> 从 Experience 中抽出的持久化表示。

例如事件：

```text
ACTION_COMPLETED
action=drink_cola
item=cola
quantity=1
location=kitchen
```

Memory candidate 可以是：

```text
“她刚喝掉了一瓶可乐。”
```

并保留：

```text
source_event_ids
```

用于追溯。

---

# 十、Memory Provenance

每一个 Memory 必须能够回答：

> “这条记忆是从哪里来的？”

至少：

```text
memory
 ↓
experience
 ↓
sandbox event
```

必须能够反向追踪。

例如：

```text
Memory #42
   ↓
Experience #31
   ↓
ACTION_COMPLETED evt_xxx
   ↓
ACTION_EFFECT_APPLIED evt_yyy
```

不要只保存一个文本字符串。

---

# 十一、Character ID 必须进入 Memory

当前项目未来支持不同 Character Bible。

Memory 绝不能只有：

```text
memory_id
```

必须至少有：

```text
character_id
```

作为隔离边界。

要求测试：

```text
罐头 Memory
≠
阿澈 Memory
```

即使两个角色运行同一个 Memory Store，也不能互相读取。

---

# 十二、Memory Scope

建议至少支持：

```text
private
social
world
self
```

或者复用现有 scope。

意义：

```text
private
→ 角色自己的内部记忆

social
→ 与其他实体的关系/互动

world
→ 世界事实

self
→ 关于自己的长期认识
```

本阶段不要求做复杂 ACL。

但字段必须存在。

---

# 十三、Importance

不要让所有 Experience 永久变成 Memory。

建立确定性的初始 importance 机制。

例如：

### 高重要性

```text
重大项目完成
第一次认识关键人物
关系重大变化
长期目标重大推进
关键外部事件
重要行动被打断
第一次发现某个世界事实
```

### 中重要性

```text
普通完整 Action
普通 Pet interaction
普通 social interaction
```

### 低重要性

```text
普通 tick
普通 need drift
无意义状态变化
```

低重要性允许：

```text
仅保留 Experience
不进入长期 Memory
```

---

# 十四、第一阶段不要使用 LLM 判断 Importance

本阶段先使用：

```text
确定性规则
```

而不是：

```text
每条 Event
→ LLM
→ “这值不值得记？”
```

原因：

- 成本不可控
- 难以测试
- 难以解释
- 可能污染 Memory

以后可以在高价值候选上接 LLM refine。

但不要现在做。

---

# 十五、Deduplication

同一个事实不能每次重启都产生新的 Memory。

至少建立：

```text
dedupe_key
```

例如：

```text
character_id
+
memory_type
+
semantic identity
```

实现：

```text
相同 Experience
→
不重复写 Memory
```

注意：

不同时间真实重复发生的事件：

```text
连续三天喝可乐
```

不能全部因为相似就错误去重。

所以 dedupe 不应只依赖 summary 文本。

优先考虑：

```text
source_event_id
correlation_id
event type
domain entity
time window
```

---

# 十六、Memory Candidate 与最终 Memory 分离

非常重要。

流程：

```text
Sandbox Event
      ↓
Experience
      ↓
MemoryCandidate
      ↓
Dedup / Importance / Validation
      ↓
Memory
```

本阶段允许：

```text
candidate
```

状态。

不要所有候选立即变成永久 Memory。

---

# 十七、Memory 生命周期

至少定义：

```text
candidate
active
superseded
archived
```

或者等价状态。

例如：

```text
“冰箱有可乐”
```

后来：

```text
“冰箱没有可乐”
```

不能让两条互相冲突的 Semantic Memory 永久以同等状态存在。

但本阶段暂时不做复杂冲突推理。

只建立：

```text
supersedes / superseded_by
```

能力。

---

# 十八、Knowledge → Memory

Knowledge 不等于 Memory。

但某些重要 Knowledge 变化可以产生 Memory Candidate。

例如：

```text
KNOWLEDGE_CHANGED
key=fridge_empty_cola
```

可以产生：

```text
semantic/world_fact candidate
```

而普通：

```text
KNOWLEDGE_CHANGED
```

不一定应该成为 Memory。

必须经过 significance rule。

---

# 十九、Action → Episodic Memory

完整 Action 是第一优先级的 episodic memory 来源。

例如：

```text
ACTION_COMPLETED
```

可以生成：

```text
Experience
memory_type=episodic
```

但不要记录所有内部细节。

优先保留：

```text
做了什么
和谁
在哪里
为什么
结果
```

如果事件链提供这些信息，则引用它们。

---

# 二十、External Influence → Social / Episodic Memory

例如：

```text
空凛邀请联机
→ ACTION_INTERRUPTED
→ Minecraft
```

这是很好的 episodic experience。

但不要在本阶段自己生成：

```text
“空凛永远是她最重要的人”
```

这种人格/关系结论。

Memory 只能记录：

> 发生过什么。

关系抽象留到后续 Relationship / Social Memory Phase。

---

# 二十一、Pet Experience

例如：

```text
PET_HUNGRY
→ PET_APPROACHED
→ feed
→ PET_FED
```

可以形成：

```text
episodic experience
```

例如：

> 小喵饿了，她给它喂了猫粮。

但不要把：

```text
pet hunger = 0.2
```

直接存为长期 Memory。

那是 World State。

---

# 二十二、Project Memory

例如：

```text
PROJECT_PROGRESS_CHANGED
completed=true
```

这是高价值 Memory candidate：

```text
“Minecraft 小城的图书馆屋顶完成了。”
```

但普通：

```text
0.35 → 0.36
```

不需要长期记忆。

---

# 二十三、Continuity Foundation

建立：

```text
ContinuitySnapshot
```

但不要把 Memory 直接等同于 Continuity。

至少包括：

```text
character_id
generated_at

current_world_state_summary
current_action
current_location

active_knowledge
important_recent_experiences
active_memories

unfinished_projects
pending_external_events

relationship_context
```

如果 Relationship 当前有稳定模型，可以引用，不要重建。

---

# 二十四、Continuity Snapshot 来源

Continuity 必须来自：

```text
World State
+
Recent Events
+
Knowledge
+
Important Memories
+
Unfinished Actions / Projects
```

不是：

```text
LLM 自己总结一段“角色现在的人生”
```

---

# 二十五、Continuity 先只生成，不影响角色

本 Phase 非常重要：

```text
ContinuitySnapshot
```

先作为：

```text
read model
```

存在。

暂时不要让它自动改变：

```text
DecisionEngine
Persona
Speech
Relationship
Action selection
```

也就是说：

```text
Sandbox
    ↓
Memory
    ↓
ContinuitySnapshot
```

先打通。

下一阶段再：

```text
ContinuitySnapshot
    ↓
Conversation / Decision context
```

---

# 二十六、Persistence

必须使用现有数据库。

不要新造一个平行 SQLite。

优先检查：

```text
现有 memory tables
现有 knowledge tables
现有 sandbox event records
```

复用现有 persistence abstraction。

如果需要 migration：

- 添加最少字段
- 保持 backwards compatible
- 为 character_id 提供必要默认/迁移策略
- 测试旧数据启动

---

# 二十七、Embedding 暂不启用

本阶段：

```text
Memory persistence
✅
Memory provenance
✅
Memory candidate
✅
Dedup
✅
Importance
✅
Continuity
✅

Embedding
❌
Vector DB
❌
LLM extraction
❌
LLM memory judge
❌
```

未来另开阶段。

原因是：

> 先验证 Memory 的“内容正确性”，再优化“检索方式”。

---

# 二十八、必须测试真实因果链

至少：

## Test 1：完成一个 Action

```text
ACTION_COMPLETED
→ Experience
→ MemoryCandidate
→ Memory
```

能够追溯 source_event_ids。

---

## Test 2：普通 Tick

```text
tick
→ 不产生无意义长期 Memory
```

---

## Test 3：最后一瓶可乐

```text
ITEM_CONSUMED
→ INVENTORY_DEPLETED
→ KNOWLEDGE_CHANGED
→ Experience
→ appropriate MemoryCandidate
```

不要要求每一个中间事件都独立生成 Memory。

---

## Test 4：小喵喂养

```text
PET_HUNGRY
→ PET_APPROACHED
→ ITEM_CONSUMED
→ PET_FED
→ 一条 episodic Experience / appropriate Memory
```

不能生成四五条重复 Memory。

---

## Test 5：游戏邀请

```text
EXTERNAL_EVENT_RECEIVED
→ ACTION_INTERRUPTED
→ play action
→ ACTION_COMPLETED
```

生成一条能够描述完整经历的 Experience。

---

## Test 6：Project milestone

只有：

```text
completed=true
```

或者重大 progress threshold：

```text
0.5 / 0.75 / 1.0
```

才进入高价值 Memory candidate。

---

## Test 7：Dedup

同一个 Event 重复处理：

```text
Memory count 不增加
```

---

## Test 8：Cross-character isolation

```text
罐头
→ memory A

阿澈
→ memory B
```

彼此不可见。

---

## Test 9：Restart persistence

```text
Sandbox
→ experience
→ memory
→ shutdown
→ restart
→ memory still exists
```

---

## Test 10：Continuity snapshot

构造：

```text
current action
location
knowledge
unfinished project
important memory
```

验证 ContinuitySnapshot 完整包含这些信息。

---

## Test 11：Knowledge ≠ Memory

普通 Knowledge update：

```text
KNOWLEDGE_CHANGED
```

不应该无条件增加长期 Memory。

---

## Test 12：Memory provenance

任意 Active Memory：

```text
memory
→ experience
→ source event
```

必须完整可追溯。

---

# 二十九、Memory 不得污染 Sandbox

本阶段必须遵守：

```text
Sandbox → Memory
```

允许。

但：

```text
Memory → Sandbox
```

暂时禁止。

也就是说本 Phase Memory 是：

> downstream consumer

不是：

> world state authority

这样不会破坏 Phase 3.5 建立的 State Authority。

---

# 三十、禁止项目范围扩张

本 Phase 禁止：

- LLM 自动记忆抽取
- Embedding
- Vector search
- Memory → Action
- Memory → Persona
- Memory → Relationship inference
- 新 EventBus
- 新 Mutation system
- 新 World system
- 新 Action system
- QQ architecture rewrite
- Character Bible rewrite
- Git history rewrite

---

# 三十一、源码架构要求

建议最终结构接近：

```text
SandboxEvent
      ↓
ExperienceBuilder
      ↓
ExperienceRecord
      ↓
MemoryCandidateBuilder
      ↓
MemoryStore
      ↓
ContinuityBuilder
      ↓
ContinuitySnapshot
```

如果当前项目已有对应模块，优先复用名称和接口。

不要为了“看起来架构漂亮”新建一堆层。

---

# 三十二、最终验收标准

必须全部满足：

```text
✅ Sandbox Event 可产生 Experience
✅ Experience 可产生 MemoryCandidate
✅ Candidate 有 importance / confidence / provenance
✅ Candidate 可以去重
✅ 高价值经历能够进入 Memory
✅ 普通 tick 不污染 Memory
✅ Knowledge 不等于 Memory
✅ Memory 带 character_id
✅ Memory 可以追溯到 Sandbox Event
✅ Restart 后 Memory 不丢
✅ ContinuitySnapshot 可以从当前世界重新生成
✅ Continuity 包含 current state + important memory
✅ Memory 不反向修改 Sandbox
✅ 没有 LLM-per-event
✅ 没有 embedding
✅ 没有新 World/EventBus/Mutation/Action System
✅ 第二角色隔离
✅ 所有既有测试保留
✅ CI success
```

---

# 三十三、Git 双目录要求

开发：

`E:\WorkSpace ZCode\CatooBot`

发布：

`E:\WorkSpace ZCode\CatooBot_github`

GitHub 镜像发布前继续检查：

```text
API keys
API tokens
.env
真实 Character Bible
local DB
logs
private workflow
local absolute paths
```

不得上传：

```text
E:\WorkSpace ZCode\CatooBot\config\character_bible.md
```

不得：

```text
git filter-repo
BFG
force push
history rewrite
```

---

# 三十四、最终报告

必须报告：

```text
Phase 4 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

现有 Memory subsystem:
<文件 + 职责>

Experience:
<文件 + 入口>

MemoryCandidate:
<文件 + 入口>

Memory Store:
<文件 + 入口>

Provenance:
<实现>

Dedup:
<实现>

Importance:
<实现>

Character isolation:
<测试>

Restart persistence:
<测试>

ContinuitySnapshot:
<文件 + 入口>

Continuity 来源:
<World / Knowledge / Memory / Action / Project>

Memory 是否反向影响 Sandbox:
No

是否使用 LLM:
No

是否使用 Embedding:
No

是否进入下一 Phase:
No
```

完成后停止。

---

# 最重要的原则

本阶段不是为了让角色“更聪明”。

而是为了让系统第一次拥有：

> **她确实经历过什么、哪些经历值得留下、这些记忆从哪里来的，以及重启之后如何保持连续性。**

核心链必须保持：

```text
Sandbox Event
      ↓
Experience
      ↓
Memory Candidate
      ↓
Memory
      ↓
Continuity Snapshot
```

而不是：

```text
LLM
 ↓
“我觉得她应该记得这个”
 ↓
写 Memory
```

本阶段完成后停止，不进入下一阶段。