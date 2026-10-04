# CatooBot v2.1 Phase 3
## External Influence + Sandbox Wakeup + Action Interrupt

### 一、基线

上一阶段已经完成：

`v2.1 Phase 1`
Character Bible → CharacterDefinition → World Seed → Runtime

`v2.1 Phase 2`
Entity Interaction + Sandbox Event Bus + State Mutation

当前基线 commit：

`258ba0da0d1e8090905bfda8bf0a9d218a398cac`

Phase 2 已经验证：

- Sandbox Event Bus 已实际运行
- EventBus 为 SandboxRuntime 私有实例
- Entity Interaction 已实际进入 Runtime
- State Mutation 已成为运行时行为的统一变更入口
- Action 生命周期已进入 Event Chain
- Pet interaction 已进入 Event Chain
- Inventory depletion 已进入 Event Chain
- Movement 已进入 Event Chain
- causation_id / correlation_id 已可追踪
- 无限事件链已有边界保护
- Multi-Sandbox 已隔离
- 第二角色已回归
- 1035 tests passed
- CI success

本阶段必须以 commit：

`258ba0da0d1e8090905bfda8bf0a9d218a398cac`

作为唯一开发基线。

不要修改 Phase 2 的核心架构来“另起一套”。

---

# 二、本阶段唯一核心目标

让：

> **外部世界事件能够影响 Sandbox，但外部系统不能直接修改 Sandbox 状态。**

也就是：

```text
External World
      ↓
ExternalWorldEvent
      ↓
External Influence Layer
      ↓
Sandbox Wakeup
      ↓
Decision / Interrupt Evaluation
      ↓
Interaction / Action
      ↓
State Mutation
      ↓
SandboxEvent
```

Sandbox 依旧是独立运行的世界。

QQ 只是 External World 的一种输入来源。

---

# 三、重要边界

本阶段可以实现：

- External Event 抽象
- QQ Event Adapter / normalization
- Sandbox Wakeup
- Influence Evaluation
- Action Interrupt
- Action Resume
- External Event → Sandbox Event

本阶段禁止：

- Memory Feedback
- Long-term Memory
- Embedding
- 新的 World 系统
- 新的 Persona 系统
- 重做 Character Bible
- 每个外部事件调用 LLM
- 每个 tick 调用 LLM
- QQ handler 直接修改 Sandbox 字段
- QQ 专属逻辑进入 Sandbox 核心

---

# 四、建立 ExternalWorldEvent

建立通用：

```text
ExternalWorldEvent
```

至少包含：

- event_id
- timestamp
- source
- actor_id
- event_type
- content
- metadata
- urgency
- correlation_id

建议 source 可以是：

```text
qq
delivery
timer
system
web
other
```

不要写成：

```python
QQMessageEvent
```

作为 Sandbox 的核心类型。

QQ 应该是 adapter。

---

# 五、建立 External Event Adapter

现在项目已有外部事件处理逻辑。

不要推翻现有 plugin/chat。

建立：

```text
ExternalEventAdapter
```

负责：

```text
OneBot / QQ event
        ↓
ExternalWorldEvent
```

Adapter 负责协议转换。

Sandbox 不认识：

- OneBot
- NapCat
- QQ message object
- group_id/message_id 等协议细节

Sandbox 只认识：

```text
ExternalWorldEvent
```

---

# 六、External Influence Layer

建立：

```text
ExternalInfluenceEvaluator
```

负责：

> 判断这个外部事件是否可能改变 Sandbox 当前世界状态。

结果不要直接变成 Action。

建议至少支持：

```text
NO_EFFECT
OBSERVE
WAKE
INTERRUPT
REJECT
```

也可以采用等价的 enum / decision model。

必须允许未来扩展。

---

# 七、不要让 Influence Evaluator 直接修改状态

错误：

```text
QQ message
→ evaluator
→ character.location = ...
```

错误：

```text
QQ invitation
→ evaluator
→ current_action = ...
```

正确：

```text
ExternalWorldEvent
        ↓
InfluenceDecision
        ↓
DecisionRequest / ActionRequest
        ↓
正常 Sandbox Action / Mutation
        ↓
SandboxEvent
```

External Influence 只是决定：

> “外部世界值得进入 Sandbox 处理”。

而不是自己修改 Sandbox。

---

# 八、建立 Sandbox Wakeup

现在 Sandbox 以 tick 维持世界。

Phase 3 增加：

```text
sandbox.wakeup(...)
```

或者等价接口。

要求：

普通情况下：

```text
tick()
```

重要外部事件：

```text
external_event
    ↓
wakeup
    ↓
immediate evaluation
```

Wakeup 不等于立即让角色行动。

Wakeup 的语义只是：

> Sandbox 不要等下一个 10 分钟 Tick，现在立即评估这个事件。

---

# 九、建立 Pending External Event Queue

外部事件不要直接递归进入 Sandbox。

建议：

```text
External Event
      ↓
Queue
      ↓
Sandbox Wakeup
      ↓
Process
```

至少处理：

- FIFO
- timestamp
- event priority / urgency
- correlation_id
- duplicate protection

不要把 QQ 消息直接塞成 SandboxEvent。

因为：

```text
ExternalWorldEvent
```

和：

```text
SandboxEvent
```

语义不同。

前者：

> 外部世界发生了某事。

后者：

> Sandbox 内部世界发生了某事。

---

# 十、External Event → Sandbox Event

处理之后，如果外部事件真正影响 Sandbox：

应该产生类似：

```text
EXTERNAL_EVENT_RECEIVED
```

或者：

```text
WORLD_EXTERNAL_INFLUENCE
```

但注意：

这个 Event 仍然是：

> Sandbox 已经接受了某个外部事实。

例如：

```text
空凛邀请联机
```

不是：

```text
罐头决定去打 Minecraft
```

后者属于 Decision / Action。

---

# 十一、Case A：空凛邀请联机

必须实现一个完整因果链。

场景：

```text
Character:
罐头

Current Action:
watch_animation

External Event:
空凛发送：
“来打 Minecraft”
```

流程：

```text
ExternalWorldEvent
        ↓
External Influence
        ↓
Sandbox Wakeup
        ↓
识别为可能改变当前行为的重要事件
        ↓
Interrupt Evaluation
        ↓
当前 Action 被打断
        ↓
ACTION_INTERRUPTED
        ↓
新 Action Request
        ↓
play_minecraft
        ↓
ACTION_STARTED
```

注意：

本阶段不要为了自然语言理解重新设计整个 AI 系统。

现有的：

```text
InterruptEvaluator
ProposalValidator
ActionSystem
DecisionRequest
```

优先复用。

---

# 十二、Case B：无影响消息

例如：

```text
“你吃饭了吗”
```

假设当前：

```text
watch_animation
```

那么：

```text
ExternalWorldEvent
        ↓
InfluenceEvaluator
        ↓
OBSERVE / NO_EFFECT
        ↓
当前 Action 不变
```

要求：

- 不产生错误 State Mutation
- 不打断当前 Action
- Sandbox 可以记录 external influence fact
- QQ 不直接操作内部状态

---

# 十三、Case C：高优先级外部事件

至少设计一个通用接口：

```text
urgency = high
```

例如未来：

```text
快递
紧急系统事件
现实世界重大输入
```

高优先级事件应该可以绕过普通 debounce / wait。

但是本阶段不要求实现所有现实世界来源。

只建立机制。

---

# 十四、Action Interrupt / Resume

Phase 2 已经存在：

```text
ACTION_INTERRUPTED
```

现在把它真正用于 External Influence。

建立：

```text
InterruptedActionContext
```

至少保存：

```text
action_id
definition_id
progress
location
started_at
planned_end_at
interrupt_reason
resumable
```

注意：

不要简单保存整个 Runtime。

只保存恢复一个 Action 所需的信息。

---

# 十五、恢复机制

例如：

```text
watch_animation
        ↓
QQ invitation
        ↓
interrupt
        ↓
play_minecraft
        ↓
Minecraft 完成
        ↓
resume watch_animation
```

恢复时：

- 恢复原 Action 的语义
- 恢复合理的剩余时间/进度
- 恢复当前世界状态
- 生成新的 Action lifecycle events

不要让旧 Action 假装从没被打断。

---

# 十六、Interrupt 与 Decision 必须分离

正确：

```text
External Event
       ↓
Influence
       ↓
Interrupt Evaluation
       ↓
DecisionRequest
       ↓
Action
```

不要把：

```text
QQ Event
```

直接映射成：

```text
play_minecraft
```

否则以后：

```text
小喵叫
快递到了
外卖来了
空凛聊天
系统通知
```

都会变成一堆 hardcoded if/elif。

---

# 十七、不要增加角色特例

绝对禁止：

```python
if actor == "空凛":
```

作为核心行为逻辑。

也禁止：

```python
if content == "来打 Minecraft":
```

作为 Sandbox 核心逻辑。

正确应该是：

```text
actor relationship
event semantics
urgency
interaction relevance
action candidate
character rules
```

角色关系已经在 CharacterDefinition / Relationship 层。

Minecraft 是否存在已经由 World Seed 决定。

---

# 十八、QQ Adapter 可以做语义解析

允许 QQ adapter / upstream cognition 把原始消息转换成：

```text
event_type = invitation
activity = gaming
target = character
```

但不要把：

```text
"空凛喊她联机"
```

写死成唯一行为映射。

例如统一结构：

```text
ExternalWorldEvent:
  source=qq
  actor_id=user_x
  event_type=invitation
  semantic_kind=game_invitation
  target_activity=gaming
  content="来打 Minecraft"
```

Sandbox 再根据自己的世界状态、关系、当前 Action、规则处理。

这样以后：

```text
“一起看番”
“来语音”
“陪我打游戏”
```

都是同一种 External Influence 体系。

---

# 十九、Wakeup 必须是可重复安全的

同一个 event 不能因为：

```text
QQ handler
+
tick
+
retry
```

被执行三次。

需要：

```text
idempotency
```

至少根据：

```text
event_id
```

阻止同一个外部事件被重复应用。

---

# 二十、Persistence

本阶段不要重做数据库。

ExternalWorldEvent 是否持久化可以复用现有：

```text
SandboxEventRecord
```

或者现有 ExternalEvent persistence。

但至少要确保：

Sandbox 重启后：

- 不会重复处理已经消费的外部事件
- 未处理的重要外部事件不会无故丢失

如果当前 persistence 层已经有 pending external events，复用它。

---

# 二十一、测试

本阶段至少新增：

## Test 1：No-effect message

```text
watch_animation
+
普通 QQ 消息
→
No interrupt
→
Action unchanged
```

## Test 2：Game invitation interrupt

```text
watch_animation
+
game invitation
→
wake
→
interrupt
→
ACTION_INTERRUPTED
→
new action request
→
gaming action started
```

## Test 3：Resume

```text
old action interrupted
→
new action complete
→
old action resumes
```

## Test 4：Duplicate external event

同一个 `event_id` 发送两次：

```text
只处理一次
```

## Test 5：Priority

```text
normal event
high urgency event
```

高优先级能够触发立即处理。

## Test 6：Multi-sandbox external isolation

External Event A：

```text
Sandbox A
```

不能进入：

```text
Sandbox B
```

## Test 7：QQ protocol isolation

Sandbox 测试不得直接依赖：

```text
OneBot
NapCat
QQ Message object
```

## Test 8：Second character

使用“阿澈”继续测试：

- 无宠物
- 无 Minecraft
- 不依赖罐头
- 不依赖空凛
- External Influence 仍能工作

---

# 二十二、不能破坏 Phase 2

必须保持：

```text
External Event
    ↓
Sandbox Wakeup
    ↓
Interaction / Action
    ↓
State Mutation
    ↓
Sandbox Event
```

所有真正的状态变化仍然只能走 Phase 2 已建立的 mutation / event spine。

不要：

```text
QQ → direct field mutation
```

---

# 二十三、禁止项目范围扩张

本阶段禁止：

- Memory Feedback
- Semantic Memory
- Embedding
- Long-term Memory
- Character Continuity 重构
- 全面重构 Persona
- 新 World Engine
- 新 Event Bus
- 第二套 Action System
- 每条 QQ 消息调用 LLM
- 每个 Tick 调用 LLM
- 修改 Character Bible Canonical Source

---

# 二十四、工程质量

完成后运行：

```powershell
ruff check .
ruff format --check .
mypy app
pytest -q
```

全部通过。

不要删除旧测试。

不要为了测试增加角色硬编码。

不要为了兼容 Phase 3 创建第二套接口。

---

# 二十五、GitHub 双目录流程

所有开发：

```text
E:\WorkSpace ZCode\CatooBot
```

完成后才同步：

```text
E:\WorkSpace ZCode\CatooBot_github
```

发布前检查：

- API key
- token
- .env
- local database
- logs
- private workflow
- real Character Bible
- local paths
- secrets

尤其不得把：

```text
E:\WorkSpace ZCode\CatooBot\config\character_bible.md
```

同步到公开镜像。

不要执行：

```text
git filter-repo
BFG
force push
history rewrite
```

本阶段也不要处理旧 Git 历史中的 Character Bible 暴露。

---

# 二十六、验收标准

Phase 3 只有在以下全部满足时才能标记 PASS：

```text
✅ ExternalWorldEvent 已建立
✅ QQ 可以通过 adapter 转成 ExternalWorldEvent
✅ Sandbox 不依赖 QQ 协议对象
✅ External Event Queue 存在
✅ Sandbox Wakeup 存在并实际使用
✅ Influence Evaluation 已实际接入
✅ No-effect event 不打断行为
✅ Important event 可以立即唤醒 Sandbox
✅ Action Interrupt 已实际使用
✅ InterruptedActionContext 存在
✅ Action Resume 已实际使用
✅ Game invitation 因果链成立
✅ duplicate event 不会重复执行
✅ high urgency 有优先处理路径
✅ Multi-Sandbox isolation
✅ 第二角色 regression
✅ 不绕过 Phase 2 Mutation/Event spine
✅ 没有 Memory Feedback
✅ 没有新的 LLM-per-message / LLM-per-tick
✅ 没有角色专属硬编码
✅ 1035 + 新增测试全部通过
✅ CI success
```

---

# 二十七、最终报告

必须报告：

```text
Phase 3 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

ExternalWorldEvent:
<文件 + 入口>

ExternalEventAdapter:
<文件 + 入口>

ExternalInfluenceEvaluator:
<文件 + 入口>

Sandbox Wakeup:
<文件 + 入口>

External Event Queue:
<文件 + 入口>

Interrupt:
<文件 + 入口>

Resume:
<文件 + 入口>

Case A:
普通消息 → 当前 Action 是否保持:

Case B:
游戏邀请 → interrupt → new action:

Case C:
interrupt → resume:

Idempotency:
<测试结果>

Multi-Sandbox:
<测试结果>

第二角色:
<测试结果>

是否绕过 Mutation/Event：
No

是否加入 Memory：
No

是否加入新的 LLM 自动决策：
No

是否修改 Git history：
No

GitHub mirror:
working tree:
```

完成后立即停止。

不要自行进入 Phase 4。

# 最重要的架构原则

Phase 2 建立的是：

```text
世界内部发生什么
```

Phase 3 建立的是：

```text
外部世界如何进入这个世界
```

两者必须保持边界：

```text
External World
      ↓
ExternalWorldEvent
      ↓
Influence
      ↓
Wakeup / Decision
      ↓
Sandbox Action
      ↓
Mutation
      ↓
Sandbox Event
```

而绝对不能退化成：

```text
QQ Message
    ↓
if/elif
    ↓
直接改罐头状态
```

本阶段完成后停止，等待下一阶段审核。