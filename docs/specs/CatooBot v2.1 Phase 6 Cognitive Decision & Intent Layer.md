# CatooBot v2.1 Phase 6
## Cognitive Decision & Intent Layer

### 一、当前基线

当前已经完成：

- Phase 1 — Character Bible → Definition → World Seed → Runtime
- Phase 2 — Entity Interaction + Event Bus + State Mutation
- Phase 3 — External Influence + Sandbox Wakeup + Interrupt / Resume
- Phase 3 Remediation
- Phase 3.5 — State Authority Closure
- Phase 4 — Experience → Memory → Continuity
- Phase 4 Remediation
- Phase 5 — Cognitive Context Bridge
- Phase 5 Remediation

当前最终基线：

`3c5f4f01760c294d044230e482b08a22904071bb`

当前：

`1124 passed`

CI success。

---

# 二、本阶段唯一目标

建立一个：

> **受 Sandbox 严格约束的认知决策层。**

目标不是让 LLM 控制 Sandbox。

目标是：

```text id="h4j1a2"
Sandbox / External Event
        ↓
判断是否需要决策
        ↓
DecisionRequest
        ↓
Candidate Actions / Intent Candidates
        ↓
Cognitive Context
        ↓
LLM
        ↓
IntentProposal
        ↓
Hard Constraint Validation
        ↓
Interaction / Action
        ↓
State Mutation
        ↓
Sandbox Event
```

---

# 三、核心原则

本 Phase 必须严格保持：

```text id="xsy3cz"
LLM = Decision Maker
Sandbox = Authority
```

LLM：

- 可以选择候选方案
- 可以判断优先级
- 可以在多个合法方案之间做选择
- 可以根据 Persona / Memory / Continuity 形成角色化决策

LLM 不可以：

- 直接修改 Sandbox
- 直接修改 location
- 直接修改 inventory
- 直接修改 needs
- 直接修改 project
- 直接修改 knowledge
- 直接创建不存在的 action
- 直接执行 Interaction
- 直接绕过 ActionSystem

---

# 四、首先审计现有 DecisionRequest

Phase 2 已经存在：

```text id="j3x9n7"
DecisionRequest
```

开始前先搜索全仓库：

```text
DecisionRequest
decision
decision engine
DecisionManager
Proposal
Intent
```

要求：

1. 不创建第二套 DecisionRequest。
2. 如果已有接口只有基础字段，扩展它。
3. 优先复用已有 Decision / Agent / Model Router 能力。
4. 不因为 Phase 6 再造一个新的 Agent Runtime。

最终报告必须列出：

```text id="k05kqk"
现有 DecisionRequest:
现有 DecisionManager:
现有 decision engine:
现有模型调用入口:
本 Phase 复用:
本 Phase 新增:
```

---

# 五、建立 Candidate Action / Intent

建立统一：

```text id="q5wj6b"
DecisionCandidate
```

或者等价结构。

必须描述：

```text id="lv2e9b"
candidate_id
action_id / interaction_id
label
reason / metadata
requirements
priority
```

最重要的是：

> Candidate 必须来自 Sandbox 当前合法能力。

例如：

```text id="21gacm"
当前：
watch_animation

外部：
gaming invitation

Candidates:
A = continue_current_action
B = play_minecraft
C = postpone_gaming
```

如果 Sandbox 当前根本没有 `play_minecraft`：

> Candidate 不得出现。

不能让 LLM 凭空创造 Action。

---

# 六、DecisionRequest

建立完整：

```text id="h7p4s2"
DecisionRequest
```

至少包含：

```text id="m8ul8v"
request_id
character_id
trigger
context
candidates
constraints
deadline
correlation_id
causation_id
```

其中：

### trigger

说明：

> 为什么现在需要角色决定。

例如：

```text external_invitation
pet_interaction
resource_shortage
conflicting_goals
action_interrupt
scheduled_event
```

---

# 七、DecisionRequest 必须带 Cognitive Context

DecisionRequest 应该能够看到：

```text id="n7cnq6"
Current World
Current Action
Needs
Continuity
Relevant Sandbox Memory
Recent Experience
Conversation Memory
Relationship Context
Character Rules
```

优先复用 Phase 5 的：

```text id="jjx4q8"
CognitiveContext
```

不要重新建立第二套 Memory Retrieval。

---

# 八、LLM 只看到候选，不看到写权限

错误：

```text id="7x2o7r"
“你想做什么都可以。”
```

正确：

```text id="zaxg8y"
现在存在三个合法候选：
1. continue_current_action
2. play_minecraft
3. postpone_invitation

只能选择其中一个。
```

这样模型输出天然受约束。

---

# 九、IntentProposal

LLM 输出：

```text id="g6bwd5"
IntentProposal
```

至少：

```text id="v0y2bj"
request_id
candidate_id
confidence
reason
```

其中：

```text id="l6adri"
reason
```

仅用于调试。

不要把 reasoning 自动写入 Memory。

不要把 reasoning 当人格文本。

更不要保存模型 Chain-of-Thought。

---

# 十、LLM 输出必须 Structured

禁止：

```text id="z12g4r"
解析模型自然语言：
“嗯我觉得还是去玩Minecraft比较好……”
```

推荐：

```json
{
  "candidate_id": "play_minecraft",
  "confidence": 0.82
}
```

或者等价严格结构。

必须：

- schema validate
- enum validate
- request_id match
- candidate_id exists
- character_id match

---

# 十一、Decision 失败必须有确定性 fallback

LLM 可能：

- timeout
- invalid JSON
- unknown candidate
- low confidence
- provider error
- empty response

不能让 Sandbox 卡死。

必须：

```text id="xqrw65"
LLM failed
    ↓
Deterministic fallback
    ↓
valid candidate
```

Fallback 优先级建议：

```text id="o0r8sb"
1. hard safety / world rule
2. urgent need
3. currently running action
4. deterministic priority
5. first stable candidate
```

具体按现有系统设计。

---

# 十二、LLM 不得每个 Tick 调用

这是硬性要求。

以下都不能自动调用 LLM：

```text id="xqk0yq"
每个 tick
每个 NEED_CHANGED
每个 ITEM_CONSUMED
每条 QQ 消息
每个 Memory
```

只有：

```text id="9v8y5r"
DecisionRequest
```

才能触发 LLM。

---

# 十三、Decision Gate

建立：

```text id="w3b9a5"
DecisionGate
```

负责回答：

> 这个事件是否真的需要 LLM 决策？

例如：

### 不需要 LLM

```text id="frk4ji"
宠物饿了
+
唯一合法动作 = feed_pet
```

直接：

```text deterministic
```

---

### 不需要 LLM

```text id="4o2u7f"
库存消耗
→ shortage event
```

只是事实。

不需要决定。

---

### 需要 LLM

```text id="mwhqtw"
多个合法候选
+
Character Preferences / Relationship / Current Context
之间存在真实权衡
```

此时：

```text DecisionRequest
```

才创建。

---

# 十四、Phase 6 第一案例：游戏邀请

必须实现完整：

```text id="8p9d4f"
Current:
watch_animation

External:
game_invitation

Candidates:
continue_current_action
play_minecraft
postpone_invitation

DecisionGate:
需要判断

DecisionRequest
        ↓
CognitiveContext
        ↓
LLM
        ↓
IntentProposal
        ↓
candidate validation
        ↓
ACTION_INTERRUPTED
        ↓
play_minecraft
```

注意：

不要依赖：

```text id="m7s6az"
if content == "来打 Minecraft"
```

使用 Phase 3 的：

```text id="c8w9pg"
semantic_kind = game_invitation
target_activity = gaming
```

作为外部事件语义。

---

# 十五、必须测试一个“不使用 LLM”的案例

例如：

```text id="2d6v7w"
pet hungry
```

如果只有：

```text feed_pet
```

唯一合法 Candidate：

```text feed_pet
```

则：

```text DecisionGate
→ NO_LLM
→ deterministic execution
```

新增测试确保：

```text engine.chat call count == 0
```

---

# 十六、Decision Event

把认知决策本身纳入 Sandbox Event。

新增合理事件类型：

```text id="x6j2ww"
DECISION_REQUESTED
DECISION_PROPOSED
DECISION_ACCEPTED
DECISION_REJECTED
DECISION_FALLBACK
```

不要另建第二套 event system。

继续使用：

```text id="kba67q"
SandboxEventType
```

---

# 十七、完整因果链

例如游戏邀请：

```text id="n5gw0p"
EXTERNAL_EVENT_RECEIVED
        ↓
DECISION_REQUESTED
        ↓
DECISION_PROPOSED
        ↓
DECISION_ACCEPTED
        ↓
ACTION_INTERRUPTED
        ↓
ACTION_REQUESTED
        ↓
ACTION_STARTED
```

如果 LLM 失败：

```text id="w8j8ks"
DECISION_REQUESTED
        ↓
DECISION_FALLBACK
        ↓
DECISION_ACCEPTED
```

如果所有候选都非法：

```text id="xw5w1p"
DECISION_REJECTED
```

所有事件必须有：

```text id="b0g3s4"
causation_id
correlation_id
```

---

# 十八、Decision 本身不得改变 Sandbox

LLM 返回：

```text id="trsp0f"
play_minecraft
```

这一刻：

> 世界还没有改变。

只有：

```text id="e6v4f7"
IntentProposal
        ↓
Validator
        ↓
ActionRequest
```

通过之后：

```text id="y1qj1h"
ActionSystem
```

才真正改变世界。

---

# 十九、Candidate Validation

建立：

```text id="zv5w3n"
DecisionValidator
```

负责检查：

```text id="v12bgb"
candidate exists
action exists
current state still compatible
requirements satisfied
world rules satisfied
character rules satisfied
interruptibility satisfied
```

这一步必须重新读取当前 Runtime 状态。

不能相信 LLM 给的旧状态。

---

# 二十、Race / Stale Decision

非常重要。

例如：

```text id="ap27sq"
DecisionRequest
    ↓
LLM processing
```

期间：

```text id="dnr1yr"
Pet becomes critical
```

这时旧 Proposal：

```text id="f1z70t"
play_minecraft
```

可能已经无效。

因此 `DecisionValidator` 必须在执行前重新验证。

Proposal 不应永久有效。

建议：

```text id="l4sg1t"
expires_at
world_revision / snapshot_revision
```

或者等价机制。

---

# 二十一、不要保存 LLM Reasoning 为 Memory

例如模型返回：

```text id="nq7v0v"
“因为我和空凛关系很好，而且今天已经看了很久动画……”
```

这不能自动进入：

```text id="u0wwi6"
Memory
```

只能：

```text id="rshb9w"
debug trace
```

而且遵循当前项目不持久化 Chain-of-Thought 的规则。

---

# 二十二、Relationship 可以作为输入，但暂不重构

Decision Context 可以读取现有：

```text id="yyx4og"
Relationship
```

例如：

```text trust
closeness
interaction context
```

但本阶段：

> 不重新设计 Relationship System。

它只是 Decision Context 的一个输入。

---

# 二十三、Character Rules 必须可见

LLM Decision Context 必须带：

```text id="jk0f0z"
CharacterDefinition.rules
boundaries
values
preferences
modes
relationships
```

但只注入与当前 Decision 有关的部分。

不能把完整 Character Bible 原文全部塞给模型。

优先使用：

```text id="zml22d"
compiled CharacterDefinition
```

---

# 二十四、决策上下文必须有预算

不能因为 Phase 6 就把：

```text id="fmytqf"
Character Bible
全部 Memory
整个 Sandbox
所有 Relationship
```

全部传给 LLM。

Decision Context 应该有：

```text id="0f6r0y"
world budget
memory budget
candidate budget
character rule budget
```

复用 Phase 5 Cognitive Context 的预算体系。

---

# 二十五、LLM Decision 与聊天回答分开

非常重要：

```text id="3m5r2u"
Decision LLM
```

与：

```text id="e3f1se"
Conversation LLM
```

是两个不同职责。

可以使用相同底层模型，但：

```text id="q50r4d"
Decision prompt
≠
Conversation prompt
```

Decision 输出结构化 Intent。

Conversation 输出自然语言。

---

# 二十六、Decision Model Routing

本阶段不要做新的 Model Router。

复用当前 AI Model / Provider 层。

但 Decision Request 可以支持：

```text id="gpdrr8"
decision_model
```

如果现有 Router 支持。

如果没有：

> 使用当前默认模型。

不要为了这一阶段重构模型系统。

---

# 二十七、测试

至少新增：

## Test 1：Deterministic path

唯一合法候选：

```text id="n3y7jb"
pet hungry
→ feed_pet
```

确认：

```text LLM calls = 0
decision events = deterministic
action executes
```

---

## Test 2：LLM decision path

三个合法候选：

```text id="x0yq9s"
continue
play
postpone
```

Fake model 返回：

```text play
```

验证：

```text DECISION_REQUESTED
→ DECISION_PROPOSED
→ DECISION_ACCEPTED
→ Action
```

---

## Test 3：Invalid candidate

LLM 返回：

```text unknown_action
```

验证：

```text proposal rejected
fallback
```

---

## Test 4：Timeout / provider failure

验证：

```text LLM error
→ DECISION_FALLBACK
→ deterministic candidate
```

---

## Test 5：Stale proposal

Decision 建立后：

```text world state changes
```

再提交 Proposal：

验证：

```text validator rejects stale proposal
```

不能执行过期行动。

---

## Test 6：No sandbox bypass

验证：

LLM Proposal 本身不会修改：

```text location
needs
inventory
projects
knowledge
current_action
```

只有 ActionSystem 执行后才变化。

---

## Test 7：Causation

验证：

```text EXTERNAL_EVENT_RECEIVED
→ DECISION_REQUESTED
→ DECISION_PROPOSED
→ DECISION_ACCEPTED
→ ACTION_REQUESTED
```

都有正确：

```text causation_id
correlation_id
```

---

## Test 8：Character isolation

阿澈世界不存在 Minecraft。

即使 LLM 输出：

```text play_minecraft
```

Validator 必须拒绝。

不能依赖 prompt 告诉 LLM “阿澈没有 Minecraft”。

Sandbox 本身必须最终否决。

---

## Test 9：Rule constraint

构造一个 Character Rule：

```text 禁止某类行动
```

即使 LLM 选择该 Candidate：

```text Validator = reject
```

---

## Test 10：No LLM per tick

运行：

```text 多次 tick
```

没有 DecisionRequest：

```text LLM calls = 0
```

---

## Test 11：Memory-informed decision

构造：

```text Sandbox Memory
```

与：

```text Decision Candidate
```

确保 Decision Context 能看到相关 Memory。

但：

```text Memory
```

仍然只能提供参考。

---

## Test 12：Decision event is not Memory

Decision reasoning / proposal：

不能自动生成长期 Memory。

---

# 二十八、失败降级

以下情况必须不阻塞 Sandbox：

```text id="jlrz6a"
LLM timeout
LLM invalid output
LLM unavailable
decision module unavailable
```

有 deterministic fallback 时继续执行。

没有合法 fallback：

```text DECISION_REJECTED
```

而不是：

```text crash
```

---

# 二十九、Persistence

不要重设计数据库。

Decision Event 可以：

```text SandboxEventRecord
```

进入已有 Event persistence。

Pending Decision 是否持久化：

> 本阶段只有在现有 Runtime lifecycle 需要时才做。

优先避免新增复杂队列。

---

# 三十、禁止项目范围扩张

本阶段禁止：

- Memory → Sandbox 直接写入
- Memory 自动改变 Persona
- Relationship System 重构
- Character Bible 重构
- 新 EventBus
- 新 Mutation System
- 新 ActionSystem
- 新 World System
- QQ protocol rewrite
- Embedding
- Vector Search
- 每条消息调用 Decision LLM
- 每个 Tick 调用 Decision LLM
- Git history rewrite

---

# 三十一、工程要求

必须执行：

```powershell id="jv8vba"
ruff check .
ruff format --check .
mypy app
pytest -q
```

要求：

```text id="l2mygq"
原 1124 tests 全部保留
+
Phase 6 新测试
=
全部通过
```

CI success。

---

# 三十二、Git 双目录

开发：

`E:\WorkSpace ZCode\CatooBot`

发布：

`E:\WorkSpace ZCode\CatooBot_github`

发布前：

- API key
- API token
- `.env`
- 真实 Character Bible
- local DB
- logs
- Zcode private workflow
- local paths
- secrets

不得上传：

`E:\WorkSpace ZCode\CatooBot\config\character_bible.md`

不得：

```text id="cgtm5d"
git filter-repo
BFG
force push
history rewrite
```

---

# 三十三、最终报告

必须报告：

```text id="yfi5q0"
Phase 6 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

DecisionRequest:
<文件 + API>

DecisionGate:
<文件 + API>

DecisionCandidate:
<文件 + API>

IntentProposal:
<文件 + API>

DecisionValidator:
<文件 + API>

LLM invocation:
<唯一入口>

Deterministic path:
<测试>

LLM decision path:
<测试>

Fallback:
<测试>

Stale proposal:
<测试>

Causation:
<结果>

Character isolation:
<结果>

Character rule validation:
<结果>

No sandbox bypass:
<结果>

Memory-informed decision:
<结果>

Decision 是否每 Tick 调用：
必须 No

Decision 是否每条聊天调用：
必须 No

Decision 是否修改 Sandbox：
必须 No（直到 Validator → ActionSystem）

是否修改 Memory:
No

是否修改 Relationship:
No

是否进入下一 Phase:
No
```

完成后停止。

---

# 三十四、最终架构目标

完成本 Phase 后：

```text
                   ┌───────────────┐
                   │  External     │
                   │  World Event  │
                   └───────┬───────┘
                           ↓
                    Influence / Wakeup
                           ↓
                   ┌────────────────┐
                   │ Decision Gate  │
                   └───────┬────────┘
                           ↓
                  ┌──────────────────┐
                  │ DecisionRequest  │
                  └────────┬─────────┘
                           ↓
                    CognitiveContext
                           ↓
                     Candidate Set
                           ↓
                          LLM
                           ↓
                    IntentProposal
                           ↓
                 ┌───────────────────┐
                 │ DecisionValidator │
                 └────────┬──────────┘
                          ↓
                    Action Request
                          ↓
                    Action System
                          ↓
                     Mutation
                          ↓
                       Event
```

核心原则永远是：

> **LLM 可以提议，但只有 Sandbox 才有最终决定权。**

完成后停止，不进入 Phase 7。