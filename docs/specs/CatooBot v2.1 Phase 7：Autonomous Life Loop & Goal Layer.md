# CatooBot v2.1 Phase 7
## Autonomous Life Loop & Goal Layer

### 一、当前基线

当前最终基线：

`2a56086519ed1797c35425d142987fcd1311bb86`

已经完成：

- Phase 1 Character Bible → Definition → Seed → Runtime
- Phase 2 Entity Interaction + Event Bus + Mutation
- Phase 3 External Influence + Wakeup + Interrupt
- Phase 3 Remediation
- Phase 3.5 State Authority Closure
- Phase 4 Experience / Memory / Continuity
- Phase 4 Remediation
- Phase 5 Cognitive Context
- Phase 5 Remediation
- Phase 6 Cognitive Decision & Intent

当前测试：

`1141 passed`

CI success。

---

# 二、本阶段唯一目标

建立：

> **Goal-driven Autonomous Life Loop**

使 Sandbox 不再只是：

```text
Action
→ 完成
→ 再选一个 Action
```

而变成：

```text
World Event / Need / Resource / Project
        ↓
Goal Detection
        ↓
Goal Created / Updated
        ↓
Goal Selection
        ↓
合法 Action Candidates
        ↓
Decision Layer
        ↓
执行一步
        ↓
State Mutation
        ↓
Event
        ↓
Goal Progress
        ↓
继续下一步
        ↓
Goal Completed
```

核心区别：

```text
Action = 现在做什么

Goal = 为什么持续做这件事
```

---

# 三、不要建立第二套 Agent Planner

非常重要。

本阶段禁止创建：

```text
LLM Planner
Agent Planner
Task Planner
Goal LLM
```

也不要把现有 Agent Runtime 改造成 Goal Engine。

本阶段 Goal Layer 是：

```text
deterministic
event-driven
world-authoritative
```

LLM 只沿用 Phase 6：

```text
DecisionRequest
→ DecisionCandidate
→ IntentProposal
```

用于多个合法选项之间的选择。

---

# 四、建立 Goal 模型

建立：

```text id="w3f6s8"
Goal
```

或等价模型。

至少包含：

```text id="p5c2xq"
goal_id
character_id

kind
status

priority
reason

created_at
updated_at

source_event_id
causation_id
correlation_id

target_entity
target_space
target_item
target_project

progress
metadata
```

状态至少：

```text id="b5tgj1"
pending
active
blocked
completed
cancelled
expired
```

---

# 五、Goal 必须属于 Character

所有 Goal 必须：

```text id="h4n9wd"
character_id
```

测试：

```text
罐头 Goal
≠
阿澈 Goal
```

同一个数据库也必须隔离。

---

# 六、不要把 Goal 等同于 Action

错误：

```text id="8r0n2c"
Goal = action_id
```

例如：

```text
go_shopping_cola
```

不是 Goal。

正确：

```text
Goal:
RESTOCK_RESOURCE

Step 1:
prepare_to_go_out

Step 2:
move_to_store

Step 3:
acquire_resource

Step 4:
return_home
```

Action 是实现 Goal 的临时行为。

---

# 七、Goal Source

Goal 必须知道为什么产生。

例如：

```text id="m9g0u1"
need_critical
inventory_depleted
project_milestone
pet_need
external_event
unfinished_task
scheduled_need
```

以后可以继续扩展。

不要把：

```text
“罐头觉得应该买可乐”
```

作为底层 source。

---

# 八、Goal Detector

建立：

```text id="z7x1n8"
GoalDetector
```

职责：

> 从 Sandbox 已经发生的事实事件中判断是否产生 / 更新 Goal。

例如：

```text
INVENTORY_DEPLETED
        ↓
GoalDetector
        ↓
RESTOCK_RESOURCE
```

或者：

```text
PET_HUNGRY
        ↓
PET_CARE
```

但注意：

> 如果当前系统已经有一个确定性的宠物即时反应路径，不要重复创建第二个“喂猫系统”。

Goal 只负责：

> 持续目标是否存在。

---

# 九、目标必须避免重复

同一资源：

```text
cola = 0
```

不能每个 Tick 创建：

```text
RESTOCK cola
RESTOCK cola
RESTOCK cola
RESTOCK cola
```

要求：

```text
dedupe key
=
character_id
+
goal kind
+
target
```

存在 active / pending Goal 时：

> 更新，不重复创建。

---

# 十、第一条完整 Goal Chain：Resource Restock

必须实现：

```text id="n5v4kx"
ITEM_CONSUMED
        ↓
INVENTORY_DEPLETED
        ↓
GoalDetector
        ↓
RESTOCK_RESOURCE
        ↓
Goal active
```

第一版不要求支持所有物品。

必须使用：

```text
seed
inventory metadata
action definitions
```

判断某个物品是否：

```text
restockable
```

不要写：

```python
if item == "cola":
```

---

# 十一、Restock Goal 必须是通用的

目标结构应该类似：

```text id="q2a6tj"
kind = restock_resource

target_item = cola
inventory_key = fridge
desired_quantity = ...
source = inventory_depleted
```

以后可以自然支持：

```text
cola
cat_food
pudding
cake
juice
```

只要 World Seed 提供对应资源。

---

# 十二、建立 Goal Step

建立：

```text id="p7k9s2"
GoalStep
```

或者等价结构。

至少：

```text id="9f7s3n"
step_id
goal_id
kind
status
action_id
target
order
requirements
result
```

状态：

```text
pending
active
completed
blocked
skipped
failed
```

---

# 十三、Goal Step 不要提前生成整棵计划树

第一版只保存：

> 当前下一步。

例如：

```text
Goal:
restock cola

current_step:
leave_home
```

完成后：

```text
current_step:
travel_to_store
```

这样比一次创建：

```text
leave
walk
elevator
street
store
buy
walk_back
enter_home
put_away
```

更稳。

---

# 十四、不要重复造路径系统

当前 Phase 3 Remediation 已经把：

```text
SpaceSystem
```

修成：

```text
对称闭包
+
BFS 多跳可达
```

Goal Layer 直接复用：

```text
spaces.route(...)
```

或者现有等价能力。

不要再创建：

```text
GoalRoutePlanner
WorldNavigationSystem
```

第二套路线系统。

---

# 十五、Goal → Action 的关系

Goal 不直接修改 Sandbox。

正确：

```text
Goal
 ↓
需要下一步
 ↓
产生合法 Action Candidates
 ↓
DecisionGate
 ↓
Decision
 ↓
ActionSystem
```

因此：

```text
Goal
```

是：

> 目标上下文。

而：

```text
Decision
```

是：

> 当前一步该做什么。

---

# 十六、Goal 与 Phase 6 Decision 的集成

新增 DecisionTrigger，例如：

```text
goal_step
goal_conflict
goal_blocked
```

但继续复用：

```text
DecisionCoordinator
DecisionCandidate
DecisionValidator
IntentProposal
```

不能建立：

```text
GoalDecisionEngine
```

第二套决策系统。

---

# 十七、Goal 的决策规则

例如：

```text
Goal:
restock cola

合法候选：

A. continue_current_action
B. move_to_store
C. postpone
```

Decision Layer 决定：

```text
现在要不要行动
```

但 Goal Layer 负责保证：

> 如果选择了 postpone，Goal 不会凭空消失。

---

# 十八、Goal Persistence

必须持久化。

Sandbox 重启：

```text
Goal:
restock cola
status=active
```

不能丢失。

要求：

```text
Runtime restart
        ↓
Goals reload
        ↓
current step reload
        ↓
life continues
```

---

# 十九、Goal 与 Continuity

Goal 应该进入 Phase 5 已有：

```text
ContinuitySnapshot
```

例如：

```text
未完成目标：
- 补充可乐
- Minecraft 小城建设
```

但不要把所有 Goal 都注入聊天。

只提供：

```text
active important goals
```

---

# 二十、Goal 与 Memory

Goal 本身不是 Memory。

只有：

```text
Goal completed
Goal failed
Goal cancelled after meaningful attempt
major milestone
```

才允许通过现有 Experience / Memory pipeline 形成记忆候选。

例如：

```text
RESTOCK cola completed
```

可以产生：

```text
“完成了一次补给。”
```

但：

```text
goal_created
```

不应该自动变成长期记忆。

---

# 二十一、Goal Event

继续使用现有：

```text
SandboxEventType
```

新增合理事件：

```text
GOAL_CREATED
GOAL_ACTIVATED
GOAL_PROGRESS
GOAL_BLOCKED
GOAL_COMPLETED
GOAL_CANCELLED
```

必要时：

```text
GOAL_STEP_STARTED
GOAL_STEP_COMPLETED
GOAL_STEP_FAILED
```

但不要过度增加事件数量。

---

# 二十二、Goal Causation

例如：

```text
INVENTORY_DEPLETED
        ↓
GOAL_CREATED
        ↓
GOAL_ACTIVATED
        ↓
GOAL_STEP_STARTED
        ↓
ACTION_REQUESTED
        ↓
ACTION_STARTED
        ↓
ITEM_ACQUIRED
        ↓
GOAL_STEP_COMPLETED
        ↓
GOAL_COMPLETED
```

所有事件必须有：

```text
causation_id
correlation_id
```

使整个 Goal 行为可以追踪。

---

# 二十三、Goal Blocked

例如：

```text
Goal:
restock cola

当前：
商店不可达
```

必须：

```text
GOAL_BLOCKED
```

而不是：

```text crash
```

Goal 可以等待下一次：

```text
World Event
Tick
External Event
```

再尝试。

---

# 二十四、Goal 不应无限重试

建立：

```text
retry_count
last_attempt_at
next_eligible_at
```

或等价结构。

例如：

```text
move_to_store failed
```

不能每个 Tick：

```text
try
try
try
try
```

而应该：

```text
blocked
↓
cooldown
↓
再次评估
```

---

# 二十五、宠物 Goal

第二条具体 Goal：

```text
PET_CARE
```

流程：

```text
PET_HUNGRY
        ↓
GoalDetector
        ↓
PET_CARE
        ↓
feed_pet
        ↓
PET_FED
        ↓
GOAL_COMPLETED
```

注意：

如果现有宠物机制已经直接完成 feed interaction：

> Goal 作为上层目标观察并跟踪。

不要重复实现 feed。

---

# 二十六、Project Goal

第三类只建立基础支持：

```text
PROJECT_PROGRESS
```

例如：

```text
Minecraft city
```

当前 project：

```text
progress = 0.72
```

Goal：

```text
complete_project
```

只有当前系统存在：

```text
project.next_action
```

或者 ActionDefinition 能表达下一步时才生成。

不要为项目建立新的 Planner。

---

# 二十七、Goal Priority

建立确定性 Goal priority。

例如：

```text
critical survival / critical need
>
pet critical care
>
urgent external obligation
>
resource shortage
>
unfinished project
>
routine / leisure
```

具体权重由项目数据决定。

不要把：

```text
罐头
可乐
小喵
Minecraft
```

写死在核心逻辑。

---

# 二十八、不要让 Goal Layer 每 Tick LLM

Goal Evaluation：

```text
✅ deterministic
```

Decision：

```text
只有存在真实选择时
→ Phase 6
```

所以：

```text
Tick
→ GoalDetector
→ 如果只有唯一合法下一步
→ deterministic

如果多个合法方向真正存在
→ DecisionCoordinator
```

---

# 二十九、Goal Conflict

可能同时出现：

```text
RESTOCK_RESOURCE
PET_CARE
PROJECT
```

Goal Manager 必须给出：

```text
active goal
```

或者候选集合。

但：

> 不要让多个 Goal 同时修改 Action。

只能存在：

```text
one active execution goal
```

其他 Goal：

```text
pending
blocked
deferred
```

---

# 三十、Goal 不得覆盖外部紧急事件

例如：

```text
正在补货
```

突然：

```text
critical pet hunger
```

应该：

```text
Goal PET_CARE
    >
Goal RESTOCK
```

如果需要打断：

继续使用：

```text
Phase 3 Interrupt
+
Phase 6 Decision
```

不要自己发明：

```text
GoalInterruptEngine
```

---

# 三十一、Goal 与 Action Resume

如果 Goal A 的 Action 被外部事件打断：

```text
RESTOCK
   ↓
ACTION_INTERRUPTED
   ↓
PLAY_MINECRAFT
```

外部行为完成后：

```text
ACTION_RESUMED
```

应该继续：

```text
RESTOCK Goal
current step
```

而不是重新创建整个 Goal。

---

# 三十二、Goal 与 World Revision

Goal Step 执行也要接受当前：

```text
world_revision
```

如果：

```text
world changed
```

必须重新评估：

```text
goal
step
candidate
```

不要执行过期 Action。

---

# 三十三、数据库

新增最少必要表：

```text
sandbox_goals
sandbox_goal_steps
```

至少支持：

```text
character_id
status
kind
priority
created_at
updated_at
source_event_id
correlation_id
current_step
target
progress
metadata
```

并建立：

```text
character_id + status
character_id + kind + target
```

合理索引。

不要重设计现有：

```text
memories
sandbox_experiences
sandbox_memory_candidates
```

---

# 三十四、Goal 恢复

启动时：

```text
Runtime
 ↓
restore Goals
 ↓
检查 current_step
 ↓
检查 world_revision / current world
 ↓
如果仍合法
    → continue
否则
    → re-evaluate
```

不能盲目继续旧计划。

---

# 三十五、必须实现的完整案例

## Case A：最后一瓶可乐

必须至少完成：

```text
喝最后一瓶
    ↓
ITEM_CONSUMED
    ↓
INVENTORY_DEPLETED
    ↓
GOAL_CREATED(restock_resource)
    ↓
GOAL_ACTIVATED
    ↓
生成合法 Goal Step
    ↓
Decision / deterministic
    ↓
开始执行
```

本阶段至少要求达到：

> Goal 能够产生、持续存在、驱动下一步。

---

## Case B：小喵饿了

```text
PET_HUNGRY
    ↓
PET_CARE Goal
    ↓
feed_pet
    ↓
PET_FED
    ↓
Goal completed
```

---

## Case C：Goal 被外部事件打断

```text
RESTOCK Goal active
    ↓
空凛发来游戏邀请
    ↓
External Influence
    ↓
Decision
    ↓
当前 Action interrupted
    ↓
Minecraft
    ↓
Action resumed
    ↓
RESTOCK Goal continues
```

---

# 三十六、测试

至少新增：

### Test 1
Inventory depletion creates exactly one Goal

### Test 2
Duplicate depletion does not create duplicate Goal

### Test 3
Goal persists across restart

### Test 4
Goal step executes through normal Action System

### Test 5
Pet hunger creates PET_CARE Goal

### Test 6
PET_CARE completes after PET_FED

### Test 7
Goal blocked does not spin

### Test 8
Goal cooldown / retry bound

### Test 9
Multiple Goals priority

### Test 10
Critical Pet Goal outranks routine project Goal

### Test 11
External invitation interrupts Goal Action

### Test 12
Interrupted Goal resumes correctly

### Test 13
Stale Goal Step rejected after world change

### Test 14
Character isolation

罐头 Goal：

```text
≠
阿澈 Goal
```

### Test 15
Second Character

阿澈没有：

```text
cola
Minecraft
pet
```

不能生成这些角色专属 Goal。

### Test 16
No LLM for single-candidate goal step

### Test 17
LLM only through Phase 6 when multiple candidates exist

### Test 18
Goal events form causation chain

### Test 19
Continuity contains active important Goal

### Test 20
Completed Goal can become Experience / Memory candidate

但：

```text
GOAL_CREATED
```

不会自动形成长期 Memory。

---

# 三十七、Autonomous Life Simulation Test

这是本 Phase 最重要的测试之一。

创建一个完全没有 QQ 输入的 Sandbox。

运行：

```text
simulate(hours=24)
```

要求：

```text
角色不应该一直停留在 idle
```

并能够产生：

```text
Action
Need changes
Goal
Movement
Goal progress
Goal completion
```

同时：

```text
LLM calls
```

必须保持极低且可解释。

在纯确定性场景：

```text
可以为 0
```

不要为了测试强制调用 LLM。

---

# 三十八、24 小时模拟必须可重复

使用固定：

```text
simulation_seed
clock
```

运行两次：

```text
same seed
same Bible
same starting state
```

应该得到：

```text
same Goal sequence
same Action sequence
same world result
```

如果 LLM 介入：

> 不作为 deterministic simulation 的核心路径。

测试应该使用 Fake Decision Provider。

---

# 三十九、性能

Goal evaluation 不得：

```text
每个 tick 全表扫描所有 Goal
```

使用：

```text
character_id
status
eligible_at
priority
```

索引。

---

# 四十、不要创建“生活脚本”

禁止：

```python
if hour == 14:
    eat_lunch()
if hour == 18:
    go_shopping()
if hour == 23:
    watch_tv()
```

角色生活必须来自：

```text
Need
Goal
World State
Action
Preference
Rules
Decision
```

而不是固定时间脚本。

---

# 四十一、Character Bible 驱动

Goal 的名称、优先级、可选行为必须来自：

```text
CharacterDefinition
WorldSeed
ActionDefinition
NeedDefinition
ProjectDefinition
```

禁止：

```text
if character.name == "罐头":
```

---

# 四十二、Memory Boundary

Goal Layer 可以：

```text
Goal Completed
→ Experience
→ Memory Candidate
```

但不能：

```text
Memory
→ Goal
```

除非未来专门建立“记忆触发目标”机制。

本阶段：

```text
Memory = downstream
Goal = world execution
```

---

# 四十三、LLM Boundary

本阶段继续保持：

```text
LLM = optional ambiguity resolver
```

不是：

```text
LLM = planner
LLM = scheduler
LLM = world simulator
```

---

# 四十四、最终架构

目标：

```text id="hjf2kf"
                 Sandbox World
                      │
          ┌───────────┼───────────┐
          ↓           ↓           ↓
        Needs       Events      External
          │           │           │
          └───────────┼───────────┘
                      ↓
                 Goal Detector
                      ↓
                  Goal Manager
                      ↓
                Current Goal
                      ↓
                 Goal Step
                      ↓
              Candidate Actions
                      ↓
                Decision Gate
                  ↙       ↘
           deterministic   LLM
                  ↘       ↙
                Intent
                    ↓
              Validator
                    ↓
              Action System
                    ↓
                 Mutation
                    ↓
                  Event
                    ↓
               Goal Progress
```

---

# 四十五、Phase 7 禁止事项

本阶段禁止：

- Memory → Goal
- Memory → Action
- LLM Planner
- Agent Planner
- 新 Agent Runtime
- 新 Action System
- 新 EventBus
- 新 Mutation System
- 新 World Engine
- Character Bible 重构
- Relationship 重构
- QQ 架构重构
- Embedding
- Vector Search
- 每 Tick LLM
- 每 Goal Step LLM
- Git history rewrite

---

# 四十六、工程检查

执行：

```powershell
ruff check .
ruff format --check .
mypy app
pytest -q
```

必须：

```text
原 1141 tests 全部保留
+
Phase 7 tests
=
全部通过
```

CI success。

---

# 四十七、Git 双目录

开发：

`E:\WorkSpace ZCode\CatooBot`

发布：

`E:\WorkSpace ZCode\CatooBot_github`

发布前检查：

- API keys
- API tokens
- `.env`
- 真正 Character Bible
- Local DB
- logs
- Zcode private workflow
- local absolute paths
- secrets

不得上传：

`E:\WorkSpace ZCode\CatooBot\config\character_bible.md`

不要执行：

```text
git filter-repo
BFG
force push
history rewrite
```

---

# 四十八、最终报告必须包含

```text
Phase 7 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

Goal model:
<文件>

GoalStep:
<文件>

GoalDetector:
<文件>

GoalManager:
<文件>

Goal persistence:
<表 + migration>

Goal → Decision:
<入口>

Goal → Action:
<入口>

Goal → Event:
<入口>

Restock Goal:
<完整测试>

Pet Care Goal:
<完整测试>

Goal interruption:
<测试>

Goal resume:
<测试>

Goal priority:
<测试>

Goal dedupe:
<测试>

Goal persistence:
<测试>

24h simulation:
<测试结果>

Determinism:
<测试结果>

LLM calls:
<数量/条件>

Memory:
<是否只从 Goal milestone 向下形成>

Character isolation:
<测试>

是否使用 LLM Planner:
必须 No

是否新增 Agent:
必须 No

是否修改 Memory → Goal:
必须 No

是否进入 Phase 8:
No
```

完成后停止。

---

# 四十九、最终原则

Phase 6 解决的是：

> **“角色面对选择时，如何做决定。”**

Phase 7 解决的是：

> **“为什么她会一直做下去，以及一件事没做完时为什么不会凭空消失。”**

所以最终必须形成：

```text
Goal
  ↓
Step
  ↓
Action
  ↓
World Change
  ↓
Event
  ↓
Goal Progress
  ↓
Next Step
  ↓
Goal Complete
```

这一步完成后，CatooBot 才会真正从：

> **“一个会做决定的角色”**

变成：

> **“一个会持续生活、持续追求目标、即使没有 QQ 消息也会继续运行的角色”。**

本阶段完成后停止，不进入 Phase 8。