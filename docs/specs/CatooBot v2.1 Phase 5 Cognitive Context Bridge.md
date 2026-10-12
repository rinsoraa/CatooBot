# CatooBot v2.1 Phase 5
## Cognitive Context Bridge
### Sandbox Memory / Continuity → Conversation Context

---

# 一、当前基线

上一阶段已经完成并封版：

`v2.1 Phase 4 Remediation`

最终 commit：

`d881505bac3d22eb9820f42e9615da4276868666`

此前已经完成：

- Character Bible → Definition → Seed → Runtime
- Entity Interaction
- Sandbox Event Bus
- State Mutation
- External Influence
- Sandbox Wakeup
- Action Interrupt / Resume
- Need / Project / Knowledge State Authority
- Sandbox Experience
- Memory Candidate
- Sandbox Memory
- Memory provenance
- Character isolation
- ContinuitySnapshot
- Continuity character isolation
- Character-neutral Memory language

当前测试：

`1102 passed`

CI success。

---

# 二、本阶段唯一目标

把 Phase 4 已经建立好的：

```text
Sandbox Memory
ContinuitySnapshot
Recent Experience
Current Sandbox State
```

真正接入现有：

```text
CharacterRuntime
CharacterContextBuilder
ConversationTurnRuntime
```

最终形成：

```text
Sandbox
   ↓
Cognitive Context Bridge
   ↓
Character Context
   ↓
AI
```

本阶段不重新设计 Memory。

不重新设计 Sandbox。

不重新设计 Conversation Runtime。

---

# 三、首先审计现有聊天上下文链

当前已有：

```text id="l3f8ur"
CharacterRuntime.respond()
        ↓
_safe_memories()
        ↓
CharacterContextBuilder
        ↓
engine.chat()
```

已有参数：

```text id="pd1b7a"
continuity
interaction_profile
shared_experiences
context_trace
world
facts
memories
```

已有 Sandbox：

```text id="5d3s9b"
sandbox.context()
sandbox.facts_block()
sandbox.build_continuity()
sandbox.memory
sandbox.experiences
```

不要创建第二个：

```text ContextBuilder
MemoryContext
ConversationContext
```

如果现有 ContextBuilder 可以扩展：

> 优先扩展。

---

# 四、必须明确三种记忆来源

当前系统至少存在：

## A. Conversation Memory

现有：

```text
MemoryManager
```

主要负责：

- 用户聊天历史
- 关系相关记忆
- 旧版语义记忆
- session/user/group 维度信息

继续保留。

---

## B. Sandbox Memory

Phase 4 新增：

```text
SandboxMemoryStore
```

主要负责：

> 角色自己生活中经历过的长期世界事件。

例如：

```text
完成了某个项目
喂过宠物
某次外部邀请打断了正在进行的事情
首次获知某个世界事实
```

它不是用户聊天 Memory。

---

## C. ContinuitySnapshot

主要表示：

> “如果此刻继续生活/聊天，她应该知道自己最近处于什么延续状态。”

例如：

```text
当前行动
当前位置
最近重要经历
未完成项目
当前 Knowledge
重要 Memories
待处理外部事件
```

它也不是普通 Memory。

---

# 五、建立 CognitiveContext

建立一个轻量结构：

```text
CognitiveContext
```

或者等价模型。

建议至少包含：

```text
character_id

current_world
current_action
current_location

continuity

relevant_sandbox_memories
recent_experiences

conversation_memories
relationship

user_interaction_context
```

不要把它变成一个巨大万能对象。

目的只是：

> 在进入 CharacterContextBuilder 前，把不同来源的数据明确分区。

---

# 六、严格区分 Source

最终 Context 中必须能够知道：

```text
conversation_memory
sandbox_memory
continuity
sandbox_fact
recent_experience
relationship
```

属于不同来源。

禁止：

```text
全部拼成一个 memories list
```

因为以后调试时必须知道：

> 这句话是来自聊天历史，还是来自她自己的人生。

---

# 七、Sandbox Memory Retrieval

Phase 4 的：

```text
active_memories(limit)
```

目前更像“列出重要记忆”。

Phase 5 需要增加：

```text
retrieve_relevant(query)
```

或者等价能力。

要求：

输入：

```text
query
character_id
current sandbox state
```

输出：

```text
relevant Sandbox memories
```

---

# 八、暂时不要用 Embedding

这一阶段仍然：

```text
LLM = 不调用用于检索
Embedding = 不启用
```

先做：

```text
deterministic retrieval
```

可结合：

```text
keyword overlap
memory type
importance
recency
current location
current action
project id
actor/entity
```

形成稳定评分。

例如：

```text
score =
    keyword_match
    + entity_match
    + recency
    + importance
    + current_world_relevance
```

具体权重由实现决定。

要求：

> 相同输入 + 相同数据库状态 → 相同排序。

---

# 九、检索必须有预算

不能把所有 Sandbox Memory 塞进 Prompt。

增加明确：

```text
max memories
max characters / tokens
```

例如：

```text
默认只取 3~5 条
```

具体上限根据现有配置体系决定。

不能硬编码一个以后无法调整的巨大数量。

---

# 十、当前世界状态拥有最高时效性

例如 Memory：

```text
“冰箱里还有可乐。”
```

而 World State：

```text
cola = 0
```

那么聊天上下文必须明确：

```text
当前 World State
>
旧 Sandbox Memory
```

Memory 只作为历史参考。

禁止旧 Memory 覆盖当前世界事实。

---

# 十一、Continuity 优先级

建议 Context 层顺序：

```text
System / Persona
        ↓
Current World State
        ↓
Continuity Snapshot
        ↓
Relationship
        ↓
Relevant Conversation Memory
        ↓
Relevant Sandbox Memory
        ↓
Recent Experience
        ↓
Conversation History
        ↓
User Message
```

具体顺序可以根据现有 ContextBuilder 调整，但必须满足：

> **当前世界事实永远不能被旧 Memory 覆盖。**

---

# 十二、不要让 Memory 成为硬事实

Sandbox Memory 注入 Prompt 时必须明确：

```text
这些是过去经历/长期记忆，只能作为参考。
如果与当前世界状态冲突，以当前世界状态为准。
如果与最新用户消息冲突，以当前消息为准。
```

尤其是：

```text
world_fact
```

也不能直接当成：

```text
current_world_state
```

---

# 十三、加入 Memory Context Trace

现有：

```text
context_trace
```

已经存在。

Phase 5 扩展：

```text
sandbox_memory
continuity_snapshot
recent_experience
conversation_memory
```

每一层都应该能够报告：

```text
included
count
reason
query
```

例如：

```text
sandbox_memory:
included=true
count=3
reason=3 relevant memories
```

调试时可以知道：

> 为什么这一轮模型看到了这三条记忆？

---

# 十四、不要修改 Sandbox 状态

这是本阶段最重要的边界。

Cognitive Context Bridge：

```text
READ ONLY
```

允许：

```text
Sandbox → read
Memory → read
Continuity → read
```

禁止：

```text
Context → Sandbox mutation
```

禁止调用：

```text
adjust_need()
update_project_progress()
set_knowledge()
move_entity()
feed_pet()
take_item()
_start_action()
```

---

# 十五、不要通过 Memory 自动改变 Personality

例如不能：

```text
Memory
→ personality update
```

也不能：

```text
Memory
→ behavior rule
```

这一阶段 Memory 只提供：

```text
context
```

角色怎么理解、怎么表达，由：

```text
Persona
CharacterContext
LLM
```

共同决定。

---

# 十六、Continuity 的接入

当前已有：

```text
runtime.build_continuity()
```

Phase 5 接入：

```text
CharacterRuntime.respond()
        ↓
Sandbox Continuity Snapshot
        ↓
CognitiveContext
        ↓
CharacterContextBuilder
```

如果当前请求已经有 v1.2：

```text
continuity
```

必须明确区分：

```text
v1.2 Conversation Continuity
```

和：

```text
v2.1 Sandbox ContinuitySnapshot
```

禁止互相覆盖。

可以合并成最终 Context，但来源必须保留。

---

# 十七、推荐最终 Context 结构

建议：

```text
CharacterContext
├── identity / persona
├── current_state
├── sandbox
│   ├── current_world
│   ├── continuity
│   ├── relevant_memories
│   └── recent_experiences
├── relationship
├── conversation_memory
├── conversation_history
└── user_message
```

不要：

```text
memories = [
    conversation_memory,
    sandbox_memory,
    continuity,
]
```

因为三者语义完全不同。

---

# 十八、Conversation Memory 与 Sandbox Memory 不能混淆

例如用户说：

> “你上次那个 Minecraft 房顶做好了吗？”

可能同时命中：

```text
Conversation Memory:
上次和空凛聊 Minecraft

Sandbox Memory:
图书馆屋顶完成

World State:
当前项目 progress = 1.0
```

最终 Context 应该同时提供：

```text
Conversation memory
Sandbox memory
Current world
```

让模型自己形成自然回答。

不要预先把它们压成一句：

```text
“用户问的是已经完成的 Minecraft 屋顶。”
```

那会丢失来源和上下文。

---

# 十九、Sandbox Memory Query 构造

可以从：

```text
user_text
current action
current location
current project
relationship target
```

构造 retrieval query。

但不要调用 LLM。

例如：

```text
user_text = “你上次那个屋顶怎么样了”
current project = Minecraft 小城
location = livingroom
```

query 可以包含：

```text
用户原话
+
当前项目名
+
相关实体
```

---

# 二十、相关性检索必须支持“当前角色”

所有 Sandbox Memory 查询必须：

```text
character_id = runtime.character_id
```

数据库层和业务层都必须双重隔离。

不能出现：

```text
SELECT ... FROM memories WHERE status='active'
```

却没有：

```text
character_id
```

---

# 二十一、旧 MemoryManager 不要被 Phase 5 替换

继续：

```text
self.memory.retrieve_for_session(...)
```

不要删除。

Phase 5 的：

```text
sandbox memory retrieval
```

与它并列：

```text
conversation_memories = MemoryManager...
sandbox_memories = SandboxMemoryStore...
```

最后进入 ContextBuilder。

---

# 二十二、工具和 Agent 先不做 Memory Bridge

本阶段主要接：

```text
普通 Character conversation
```

暂时不要重构：

```text
ToolRuntime
AgentRuntime
```

它们可以继续收到当前已有：

```text
ToolContext
world metadata
```

后续单独决定是否接 Sandbox Memory。

---

# 二十三、主动发言也可以复用，但不要重复开发

现有：

```text
compose_initiative()
```

已经调用：

```text
respond()
```

因此只要 Cognitive Context Bridge 接入正确：

```text
主动聊天
```

就自然拥有：

```text
Sandbox Memory
Continuity
```

不要为 Initiative 再开发第二套 Memory Retrieval。

---

# 二十四、Agent 先保持现状

本 Phase：

```text
Agent = unchanged
```

除非现有接口必须做最小兼容。

不要：

```text
Memory → Agent planning
```

作为本 Phase 功能。

---

# 二十五、需要增加的 API

可以是：

```text
SandboxMemoryStore.retrieve_relevant()
```

以及：

```text
SandboxRuntime.cognitive_context()
```

或等价接口。

建议：

```text
runtime.cognitive_context(
    query=user_text,
    relationship_target=...
)
```

返回：

```text
CognitiveContext
```

但必须是：

```text
read-only
```

---

# 二十六、Context Builder 适配

扩展：

```text
CharacterContextBuilder.build()
```

增加：

```text
sandbox_context
```

或等价参数。

内部生成：

```text
sandbox continuity
sandbox memories
recent experiences
```

分层 Prompt。

---

# 二十七、Prompt 表示方式

建议：

```text
【当前世界】
...

【近期延续状态】
...

【她自己经历过的相关往事】
...

【最近发生的经历】
...
```

但是最终文本风格由现有 Prompt 架构控制。

这些内容要明确是：

> 参考信息。

不要让模型认为这些都是用户刚刚说的话。

---

# 二十八、引用来源

每条 Sandbox Memory 在内部必须保留：

```text
memory_id
source_event_ids
experience_ids
```

Prompt 层不一定显示 UUID。

但：

```text
context_trace
```

必须可以记录：

```text
memory_id
```

方便 WebUI/debug。

---

# 二十九、Memory 冲突处理

本阶段只做：

```text
current state > memory
newer memory > older memory
```

不要实现复杂推理。

例如：

旧：

```text
冰箱有可乐
```

新：

```text
冰箱没可乐
```

Memory foundation 已经支持 supersedes。

Context Bridge：

> 只读取 active memory。

不要同时把 superseded memory 注入。

---

# 三十、测试

至少新增以下测试。

## Test 1：Sandbox Memory enters chat context

已有 Memory：

```text
“完成了 Minecraft 小城图书馆屋顶”
```

用户：

```text
“那个屋顶怎么样了？”
```

验证：

```text
Sandbox Memory 被检索
进入 context trace
```

---

## Test 2：Irrelevant memory is excluded

数据库里存在：

```text
项目 A
项目 B
宠物
过去某次社交事件
```

用户只问：

```text
项目 A
```

验证：

> 不把全部 Memory 注入。

---

## Test 3：Current world overrides stale memory

Memory：

```text
cola = available
```

Current World：

```text
cola = 0
```

Context 必须同时存在，但：

```text
current world
```

明确高于旧 Memory。

---

## Test 4：Character isolation

同一个数据库：

```text
罐头 memory
阿澈 memory
```

用户查询阿澈：

```text
不能出现罐头 Memory
```

---

## Test 5：Continuity injection

构造：

```text
current action
current location
unfinished project
recent experience
```

验证：

```text
CognitiveContext
→ CharacterContextBuilder
→ prompt
```

完整出现。

---

## Test 6：Continuity source separation

同时存在：

```text
v1.2 continuity
v2.1 Sandbox ContinuitySnapshot
```

验证：

```text
两者没有互相覆盖
context trace 能看到两个不同来源
```

---

## Test 7：No Sandbox mutation

执行：

```text
respond()
```

前后比较：

```text
location
current_action
needs
inventory
projects
knowledge
mutation_count
event_count
```

必须全部不因 Context Bridge 自身发生变化。

---

## Test 8：Deterministic retrieval

相同：

```text
database
query
world
```

重复检索：

```text
结果顺序完全一致
```

---

## Test 9：Context budget

构造大量 Sandbox Memory。

验证：

```text
不会全部进入 prompt
不会超过设定预算
高相关 / 高重要性优先
```

---

## Test 10：No-LMM retrieval

源码 guard：

```text
Sandbox memory retrieval
```

不得调用：

```text
engine.chat()
```

不得调用：

```text
LLM
```

---

## Test 11：Existing conversation Memory remains

旧 MemoryManager 的结果：

```text
仍然进入 context
```

Phase 5 不能把旧 Memory 链弄坏。

---

## Test 12：Initiative reuses bridge

`compose_initiative()`：

```text
仍然通过 respond()
```

并自然拥有：

```text
sandbox memory / continuity
```

不允许建立第二套 initiative memory retrieval。

---

# 三十一、不要做的事情

本 Phase 严禁：

- Memory → Sandbox
- Memory → Persona
- Memory → Relationship inference
- Memory → Action selection
- LLM memory ranking
- Embedding
- Vector retrieval
- Memory extraction rewrite
- Character Bible 修改
- 新 Memory Store
- 新 EventBus
- 新 Mutation
- 新 World
- 新 ActionSystem
- QQ protocol rewrite
- Git history rewrite

---

# 三十二、旧 Continuity 与新 Continuity 的关系

不要删除：

```text
app/continuity/
```

也不要强行把 v1.2 ContinuityManager 改造成 Sandbox Continuity。

当前：

```text
v1.2 Continuity
= conversation continuity

v2.1 ContinuitySnapshot
= sandbox life continuity
```

Phase 5 只做：

```text
Context Bridge
```

让它们同时进入认知上下文。

未来有需要再统一。

---

# 三十三、失败降级

如果：

```text
Sandbox unavailable
```

则：

```text
chat still works
```

如果：

```text
Sandbox Memory unavailable
```

则：

```text
conversation Memory still works
```

如果：

```text
Continuity Snapshot unavailable
```

则：

```text
current world + conversation history still works
```

Cognitive Context Bridge 不能成为聊天单点故障。

---

# 三十四、性能要求

普通聊天：

```text
不要每次重新扫描全部 Sandbox Memory
```

应使用：

```text
bounded query
indexed lookup
limited candidate set
```

Phase 5 暂不做 Embedding，但应该让未来接 Embedding 时可以替换 retrieval backend，而不修改 CharacterRuntime。

---

# 三十五、最终架构目标

完成后应形成：

```text
                         Character Bible
                              ↓
                         Sandbox World
                              ↓
                   ┌──────────┴──────────┐
                   ↓                     ↓
              Current State          Event Stream
                   ↓                     ↓
             Continuity          Experience / Memory
                   └──────────┬──────────┘
                              ↓
                    Cognitive Context
                              ↑
                    Conversation Memory
                              ↑
                         User Context
                              ↓
                    CharacterContextBuilder
                              ↓
                             AI
```

其中：

```text
Sandbox
    = 世界真相

Memory
    = 过去经历

Continuity
    = 当前延续状态

Conversation Memory
    = 与用户互动历史

Cognitive Context
    = 本轮思考可见的信息集合
```

---

# 三十六、最终验收标准

必须全部满足：

```text
✅ Phase 4 Memory 能进入聊天 Context
✅ Sandbox Memory 与 Conversation Memory 分离
✅ ContinuitySnapshot 能进入聊天 Context
✅ v1.2 Continuity 不被破坏
✅ 当前 World State 优先于旧 Memory
✅ Memory 只作为参考
✅ Character ID 隔离
✅ Retrieval 有 relevance + importance + recency
✅ Retrieval 有明确预算
✅ Retrieval deterministic
✅ context_trace 可解释
✅ Memory/Continuity 全部 read-only
✅ respond() 不改变 Sandbox State
✅ Agent 不被重构
✅ Initiative 不建立第二套链
✅ 无 LLM retrieval
✅ 无 Embedding
✅ 原 1102 tests 全部保留
✅ 新增测试全部通过
✅ CI success
```

---

# 三十七、Git 双目录

开发：

`E:\WorkSpace ZCode\CatooBot`

发布：

`E:\WorkSpace ZCode\CatooBot_github`

发布前严格检查：

```text
API Key
API Token
.env
真实 Character Bible
Local DB
Logs
Zcode private workflow
local paths
secrets
```

不得上传：

`E:\WorkSpace ZCode\CatooBot\config\character_bible.md`

不得：

```text
git filter-repo
BFG
force push
history rewrite
```

---

# 三十八、最终报告

必须报告：

```text
Phase 5 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

CognitiveContext:
<文件 + 入口>

Sandbox Memory Retrieval:
<文件 + API>

Continuity Bridge:
<文件 + API>

Conversation Memory:
<是否保持原链>

ContextBuilder:
<具体修改>

Memory relevance:
<实现方式>

Memory budget:
<实现方式>

Current-world precedence:
<实现方式>

Character isolation:
<测试>

No-mutation:
<测试>

Determinism:
<测试>

Initiative reuse:
<测试>

是否使用 LLM:
No

是否使用 Embedding:
No

是否修改 Sandbox:
No

是否修改 Character Bible:
No

是否进入下一 Phase:
No
```

完成后停止。

---

# 三十九、最终原则

Phase 5 不是让模型“拥有更多资料”。

而是建立：

> **“这一刻的角色，应该看到哪些来自自己人生的资料？”**

最终必须保持：

```text
世界真相
    ↓
当前状态
    ↓
延续状态
    ↓
相关过去经历
    ↓
相关长期记忆
    ↓
本轮用户话题
    ↓
角色思考
```

而不是：

```text
把数据库里所有东西塞给 LLM
```

本阶段完成后停止，不进入 Phase 6。