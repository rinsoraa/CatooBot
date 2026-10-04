# CatooBot v2.1 Phase 2
## Entity Interaction + Sandbox Event Bus + State Mutation

### 一、当前基线

当前项目已经完成并封版：

`v2.1 Phase 1: Character Bible → Definition → World Seed → Runtime`

最终基线 commit：

`dba08d9c868359e21d16dde475fff656adf08ad8`

Phase 1 已验证：

- Character Bible → CharacterDefinition → CharacterWorldSeed → SandboxRuntime 已成为真实初始化链
- Runtime 不再使用角色专属默认值作为 canonical 初始化入口
- 第二角色测试已经验证角色隔离
- 1020 tests passed
- ruff / format / mypy / CI 全部通过
- `config/character_bible.md` 已从公开仓库当前树移除
- 当前仓库只保留 `character_bible.example.md`
- 真实 Character Bible 仍保存在本地：
  `E:\WorkSpace ZCode\CatooBot\config\character_bible.md`
- 当前真实 Bible 不得上传 GitHub
- Git 历史中的旧 Bible 暴露暂不处理，本 Phase 不执行 history rewrite

### 二、当前本地工作流

实际开发工作区：

`E:\WorkSpace ZCode\CatooBot`

这里是 Zcode 唯一开发目录，可以包含：

- 最新源码
- 本地工作流
- API
- API Key
- 本地配置
- 测试环境
- 私有 Character Bible
- 其他开发数据

GitHub 发布镜像：

`E:\WorkSpace ZCode\CatooBot_github`

该目录只包含允许进入 GitHub 的干净文件。

以后仍然严格遵守：

```text
E:\WorkSpace ZCode\CatooBot
        ↓
开发 / 测试 / 实现
        ↓
清洗同步
        ↓
E:\WorkSpace ZCode\CatooBot_github
        ↓
安全检查
        ↓
Git commit / push
        ↓
GitHub
```

不要改变这个工作流。

---

# 三、Phase 2 的核心目标

这一阶段只解决一个核心问题：

> 让 Sandbox 真正成为一个“有实体、有事件、有状态变化、有因果关系的世界”。

Phase 1 已经解决：

> “角色是谁，以及角色生活在哪个世界里？”

Phase 2 要解决：

> “这个世界里的实体如何互相影响，以及世界变化如何产生后续变化？”

也就是说，本阶段开始从：

```text
状态容器
```

进化为：

```text
可运行的因果世界
```

---

# 四、本阶段必须建立的三个核心机制

## 1. Entity Interaction

建立统一的实体交互抽象。

至少应该能够表达：

```text
Character
Pet
Object
Space
Resource / Inventory
```

之间的交互。

不要只为“小喵”做一个专用接口。

不要出现：

```python
if pet.name == "小喵":
```

或者：

```python
if character.name == "罐头":
```

或者：

```python
if action == "feed_cat":
```

这样的角色专属核心逻辑。

系统必须能够支持未来：

```text
罐头 ↔ 小喵
罐头 ↔ 电脑
罐头 ↔ 冰箱
罐头 ↔ 门
小喵 ↔ 猫粮
角色A ↔ 宠物B
角色 ↔ NPC
角色 ↔ Object
```

而不需要修改 Entity Interaction 核心。

---

# 五、Sandbox Event Bus

建立 Sandbox 内部事件总线。

需要定义明确的：

```text
SandboxEvent
EventType
EventSource
EventTarget
EventPayload
EventMetadata
```

以及：

```text
EventBus
publish()
subscribe()
dispatch()
```

但不要做成复杂的分布式消息系统。

这是单进程 Sandbox Runtime 内部的事件系统。

必须满足：

### 事件应该是“事实”

例如：

```text
PET_HUNGRY
PET_APPROACHED
ITEM_CONSUMED
INVENTORY_DEPLETED
ACTION_STARTED
ACTION_COMPLETED
ENTITY_MOVED
NEED_CHANGED
OBJECT_STATE_CHANGED
INTERACTION_COMPLETED
```

事件记录的是：

> 世界发生了什么。

而不是：

> 角色应该怎么想。

不要把：

```text
"罐头觉得应该去喂猫"
```

作为底层 World Event。

这是后续 Decision Layer 的事情。

---

# 六、State Mutation

建立统一的状态变化机制。

核心原则：

> Event 描述事实，Mutation 改变状态。

例如：

```text
ITEM_CONSUMED
        ↓
StateMutation
        ↓
inventory["cat_food"] -= 1
        ↓
InventoryChanged
```

不要：

```text
tick()
    ↓
直接修改几十个状态
    ↓
顺手 append event
```

应该尽量形成：

```text
Action / Interaction
        ↓
Event
        ↓
Mutation
        ↓
State Change
        ↓
New Event
```

也就是说：

**事件和状态变化之间要有明确因果关系。**

---

# 七、建立明确的因果链

至少实现以下几个完整案例。

## Case A：小喵饿了

完整链：

```text
Pet hunger increases
        ↓
PET_HUNGRY
        ↓
Pet interaction / world reaction
        ↓
PET_APPROACHED 或 equivalent interaction event
        ↓
Character receives interaction
        ↓
Feed action becomes applicable
        ↓
Cat food consumed
        ↓
PET_FED
        ↓
Pet hunger reduced
        ↓
Interaction completed
        ↓
Character resumes previous activity
```

注意：

不能直接：

```text
tick()
    hunger > threshold
    → pet_care += 1
    → hunger = 0
```

必须让事件与状态变化可以被追踪。

---

# 八、Case B：喝掉最后一瓶可乐

完整链：

```text
Character drinks cola
        ↓
ITEM_CONSUMED
        ↓
cola inventory decreases
        ↓
INVENTORY_DEPLETED
        ↓
world / need layer detects resource shortage
        ↓
restock need / world event
```

这一阶段暂时不要实现完整的：

```text
决定出门
→ 换衣服
→ 出门
→ 去便利店
→ 买可乐
→ 回家
```

只实现：

> “资源被消耗 → 库存归零 → 世界产生可观测的补给需求/事件”

购物行为留到后续阶段。

---

# 九、Case C：角色移动

实现：

```text
ENTITY_MOVE_REQUEST
        ↓
验证目标 Space
        ↓
StateMutation
        ↓
location changed
        ↓
ENTITY_MOVED
```

要处理：

- 当前空间
- 目标空间
- 目标空间是否存在
- 是否允许进入
- 移动是否产生事件

不要直接：

```python
character.location = "kitchen"
```

作为外部 canonical API。

底层仍然可以最终修改字段，但必须通过统一 mutation / command 路径。

---

# 十、Case D：Action 生命周期

现有 Action System 已经存在。

Phase 2 不要推翻它。

而是把 Action 生命周期逐渐接入 Event Bus：

```text
ACTION_REQUESTED
        ↓
ACTION_STARTED
        ↓
ACTION_EFFECT_APPLIED
        ↓
ACTION_COMPLETED
```

失败则：

```text
ACTION_FAILED
```

例如：

```text
feed cat
```

应该最终产生可观察事件。

这样以后：

```text
QQ
Memory
Continuity
Agent
UI
Analytics
```

都可以消费这些事实事件，而不需要直接窥探 ActionSystem 内部状态。

---

# 十一、Interaction Resolver

建立统一的 Interaction Resolver。

它的职责不是“替角色做决定”。

它只负责回答：

> 当前两个实体之间是否存在可执行的交互，以及交互需要什么条件。

例如：

```text
Character + Pet
    → feed
    → pet
    → observe

Character + Computer
    → use
    → play game

Character + Fridge
    → inspect
    → take item

Character + Door
    → open
    → enter
```

结果应该类似：

```text
InteractionCandidate
```

包括：

```text
source
target
interaction_type
requirements
effects
```

不要在这里调用 LLM。

---

# 十二、Decision 与 Interaction 必须分开

非常重要：

```text
Decision
```

回答：

> “角色想不想做？”

而：

```text
Interaction
```

回答：

> “这个世界允许不允许做，以及做了会发生什么？”

所以：

```text
角色想喂猫
        ↓
Decision Layer
        ↓
Interaction Resolver
        ↓
验证条件
        ↓
State Mutation
        ↓
Event
```

但是本 Phase：

**不要实现新的 LLM Decision Layer。**

只建立稳定接口。

例如：

```text
DecisionRequest
InteractionCandidate
InteractionResult
```

以后 Phase 3/4 再接。

---

# 十三、Event Bus 必须支持确定性

Sandbox 是一个长期运行的世界。

所以事件系统必须能够：

- 明确事件顺序
- 明确事件时间
- 明确 source
- 明确 target
- 明确 payload
- 明确处理结果

至少支持：

```text
event_id
timestamp
event_type
source_entity_id
target_entity_id
payload
causation_id
correlation_id
```

其中：

### causation_id

表示：

> 这个事件是谁导致的。

例如：

```text
ITEM_CONSUMED
        ↓ causation_id
ACTION_EFFECT_APPLIED
```

### correlation_id

表示：

> 这一整个行为链属于哪一个 interaction/action。

这样以后调试：

```text
为什么小喵最后会被喂？

```

可以沿着：

```text
PET_HUNGRY
→ PET_APPROACHED
→ INTERACTION_STARTED
→ ACTION_STARTED
→ ITEM_CONSUMED
→ PET_FED
```

追完整链路。

---

# 十四、不要把 Event Bus 做成“万能全局变量”

需要考虑 Runtime 生命周期。

推荐：

```text
SandboxRuntime
    └── EventBus
```

而不是：

```text
global_event_bus
```

事件总线应该属于 Sandbox 实例。

这样未来即使同时测试：

```text
Character A Sandbox
Character B Sandbox
```

也不会互相污染。

---

# 十五、事件处理必须避免递归爆炸

例如：

```text
Event A
 → handler
 → Event B
 → handler
 → Event C
 → Event A
```

必须有基础保护。

至少需要：

```text
dispatch depth / processing guard
event chain limit
duplicate protection where appropriate
```

并且测试循环事件不会无限递归。

不要简单禁止嵌套事件，因为：

```text
Event → Mutation → Event
```

本来就是本系统需要支持的。

正确做法是：

> 允许有界因果链，禁止无限事件循环。

---

# 十六、Tick 与 Event 必须共存

现有 Sandbox 大约每 10 分钟 Tick 一次。

保留这个机制。

但现在开始明确：

```text
Tick
```

负责：

- 时间推进
- 被动状态变化
- 缓慢需求变化
- 环境变化
- 周期性 world simulation

而：

```text
Event
```

负责：

- 立即发生的重要变化
- 实体交互
- Action 结果
- 外部后续事件
- 状态变化通知

因此：

```text
Tick
    ↓
发现小喵饥饿
    ↓
publish(PET_HUNGRY)
```

之后由 Event 系统继续处理。

不要继续扩大 Tick 方法，使所有行为都塞进去。

---

# 十七、本 Phase 暂时不要实现的东西

这是非常重要的边界。

本阶段禁止进入：

### 1. QQ External Influence

暂时不做：

```text
QQ消息
→ Sandbox
```

后续单独做 External Influence Layer。

### 2. Memory Feedback

不要做：

```text
Sandbox Event
→ Memory
→ Embedding
→ Long-term Memory
```

### 3. LLM Autonomous Decision

不要做：

```text
每个 tick
→ LLM
→ 决定角色做什么
```

### 4. Agent Runtime 重构

现有 Agent Runtime 暂不重构。

### 5. 大规模修改 Persona / Relationship

Phase 1 已建立 CharacterDefinition。

本阶段不要重新定义角色人格系统。

### 6. 新 World 系统

不要重新造第二套 World。

继续使用当前 Sandbox。

---

# 十八、角色数据必须继续保持 Bible 驱动

Phase 2 不允许重新引入：

```text
罐头
小喵
空凛
可乐
Minecraft
```

作为核心逻辑中的角色特例。

例如：

错误：

```python
if pet.name == "小喵":
```

错误：

```python
if character.name == "罐头":
```

错误：

```python
if item == "cola":
```

正确：

```text
Entity
Interaction
Inventory
Need
Action
Event
Mutation
```

都通过定义和 ID 工作。

测试角色也应该继续使用“阿澈”等非罐头数据验证通用性。

---

# 十九、需要新增的核心抽象

具体类型名称可以根据现有项目结构调整，但最终必须具备等价能力：

```text
SandboxEvent
EventBus

StateMutation
MutationResult

EntityInteraction
InteractionCandidate
InteractionRequest
InteractionResult

DecisionRequest
```

如果现有代码已经存在部分接口：

> 优先扩展，而不是重复创建第二套。

禁止出现：

```text
OldEvent
SandboxEvent
WorldEvent
RuntimeEvent
```

多套重叠事件系统。

本 Phase 的目标是统一，而不是增加抽象数量。

---

# 二十、Persistence

本 Phase 需要考虑事件和状态的持久化边界。

至少保证：

- Runtime 重启后核心状态仍然正确
- 不因为 EventBus 重新初始化导致状态丢失
- EventBus 本身可以是 transient
- 关键事件是否需要持久化，由现有 SandboxEventRecord / database 设计决定

不要为了事件系统重新设计整个数据库。

优先复用现有 persistence 层。

---

# 二十一、测试要求

这一阶段测试必须从“函数级测试”提升到：

> **因果链测试**

至少新增以下测试。

## Test 1：Pet Hungry Chain

验证：

```text
pet hunger increase
→ PET_HUNGRY
→ interaction/event
→ feed
→ inventory decrease
→ pet hunger decrease
→ PET_FED
```

检查每一步的 observable state。

---

## Test 2：Inventory Depletion Chain

验证：

```text
consume last cola
→ quantity = 0
→ INVENTORY_DEPLETED
→ restock-related event/need
```

不要实现购买流程。

---

## Test 3：Movement Chain

验证：

```text
move request
→ validation
→ mutation
→ location changed
→ ENTITY_MOVED
```

同时测试非法空间移动不会产生错误状态。

---

## Test 4：Action Lifecycle

验证：

```text
ACTION_REQUESTED
→ ACTION_STARTED
→ EFFECT
→ ACTION_COMPLETED
```

失败路径：

```text
ACTION_REQUESTED
→ ACTION_FAILED
```

---

## Test 5：Causation Chain

验证事件：

```text
event A
→ event B
→ event C
```

其中 B 的 `causation_id` 指向 A，C 指向 B 或符合你的最终设计。

---

## Test 6：Nested Events

验证：

```text
Event
→ Handler
→ Event
→ Handler
```

可以正常运行。

---

## Test 7：Infinite Loop Protection

构造：

```text
A → B → A → ...
```

确保不会无限递归。

---

## Test 8：Multi-Sandbox Isolation

创建两个 Sandbox Runtime：

```text
Sandbox A
Sandbox B
```

分别触发事件。

确保：

```text
A Event ≠ B Event
A State ≠ B State
```

不存在 global EventBus 污染。

---

## Test 9：Second Character Regression

继续使用非罐头 Character Bible。

确认：

- Entity Interaction 不依赖罐头
- Event Bus 不依赖小喵
- Inventory Mutation 不依赖可乐
- Action Event 不依赖 Minecraft
- World Event 不依赖空凛

---

# 二十二、测试必须证明“因果”，而不仅是“最终状态”

例如不能只写：

```python
assert pet.hunger == 20
```

还要验证：

```text
PET_HUNGRY
        ↓
interaction
        ↓
ITEM_CONSUMED
        ↓
PET_FED
```

也就是说：

> Phase 2 的验收重点是“为什么最终变成这样”。

而不是只有：

> “最终确实变成这样”。

---

# 二十三、日志 / Debug 能力

建议加入一个轻量级的 Sandbox Event Trace。

例如：

```text
[12:10:03]
PET_HUNGRY
source=pet_001

[12:10:04]
INTERACTION_STARTED
source=character_001
target=pet_001
type=feed

[12:10:04]
ITEM_CONSUMED
item=cat_food
quantity=1

[12:10:04]
PET_FED
target=pet_001
```

这个 Trace 对以后调试 CatooBot 非常重要。

但不要做成完整日志平台。

优先让：

```text
runtime event history
```

可测试、可读取、可诊断。

---

# 二十四、架构检查重点

完成后必须检查是否出现以下退化：

### 不允许

```text
tick() 里面出现大量 if/elif 状态脚本

PetSystem 继续自己修改 owner state

ActionSystem 自己偷偷修改十几个 subsystem

不同 subsystem 各自制造一种 Event

某个角色专属字符串重新进入核心逻辑

global EventBus

LLM 被偷偷接到 tick

QQ handler 直接修改 Sandbox 内部字段

memory handler 直接修改 Sandbox 状态
```

如果发现这些问题：

优先修正架构，而不是继续往上堆功能。

---

# 二十五、验收标准

Phase 2 完成必须满足：

```text
✅ Event Bus 存在并实际被 Runtime 使用
✅ Entity Interaction 存在并实际被 Runtime 使用
✅ State Mutation 存在并实际改变 Runtime 状态
✅ Action 生命周期进入事件链
✅ Pet interaction 进入事件链
✅ Inventory depletion 进入事件链
✅ Movement 进入事件链
✅ Event causation 可追踪
✅ 有界嵌套事件正常
✅ 无限循环被阻止
✅ 多 Sandbox 隔离
✅ 第二角色测试通过
✅ 没有重新引入角色硬编码
✅ 没有新的 QQ → Sandbox 行为
✅ 没有新的 Memory feedback
✅ 没有每 tick LLM 调用
✅ 没有第二套 World
```

---

# 二十六、工程要求

保持现有质量标准：

```text
ruff
ruff format
mypy app
pytest
```

全部通过。

不要为了新测试删除或者弱化旧测试。

不要修改无关功能。

不要为了测试硬编码特殊条件。

不要创建大量无法复用的抽象。

优先小步重构。

---

# 二十七、GitHub 发布要求

开发阶段只修改：

`E:\WorkSpace ZCode\CatooBot`

Phase 完成后再同步：

`E:\WorkSpace ZCode\CatooBot_github`

发布前必须扫描：

```text
API Key
API Token
.env
真实 Character Bible
数据库
日志
Zcode 私有工作流
本机路径
个人凭据
其他 secrets
```

不得将：

```text
E:\WorkSpace ZCode\CatooBot\config\character_bible.md
```

复制到公开镜像。

不要执行：

```text
git filter-repo
BFG
force push
history rewrite
```

历史 Bible 暴露问题本阶段继续保持现状，不处理。

---

# 二十八、最终报告格式

完成后不要只说“Phase 2 完成”。

必须报告：

```text
Phase 2 commit:
<hash>

HEAD:
<hash>

origin/main:
<hash>

remote main:
<hash>

ruff:
<result>

ruff format:
<result>

mypy:
<result>

pytest:
<result>

新增测试：
<数量>

总测试：
<数量>

Event Bus:
<实际入口文件 + 运行时调用点>

Entity Interaction:
<实际入口文件 + 运行时调用点>

State Mutation:
<实际入口文件 + 运行时调用点>

完成的因果链：
1.
2.
3.
4.

Causation / Correlation:
<实现方式>

Multi-Sandbox isolation:
<测试结果>

第二角色：
<测试结果>

是否重新引入角色硬编码：
<必须明确回答>

是否加入 QQ Influence：
<必须为 No>

是否加入 Memory Feedback：
<必须为 No>

是否加入 LLM Autonomous Decision：
<必须为 No>

是否修改 Git history：
<必须为 No>

GitHub mirror:
<状态>

working tree:
<状态>
```

如果任何一项无法满足，不要把 Phase 2 标记为完成。

## 最重要的执行原则

这一阶段不要追求功能数量。

真正的目标只有一个：

> **让 Sandbox 世界开始拥有可靠、可追踪、可测试的“因果性”。**

以后 CatooBot 的：

```text
Pet
Character
Object
Space
Inventory
Action
Need
QQ
Memory
Agent
```

都应该能够围绕这套：

```text
Event
→ Interaction
→ Mutation
→ State Change
→ Event
```

机制逐步接入。

完成本 Phase 后停止，不要自行进入 Phase 3。