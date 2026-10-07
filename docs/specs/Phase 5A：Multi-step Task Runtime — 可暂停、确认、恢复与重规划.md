基于当前仓库最新基线 `1f7b5b6` 开始 Phase 5A。

先完整阅读：

- 当前 CharacterRuntime / Agent Loop
- `app/tools/runtime.py`
- `app/integrations/minecraft/agent.py`
- `app/integrations/minecraft/confirmation.py`
- `app/integrations/minecraft/service.py`
- `minecraft_runtime/action_runtime.js`
- 所有现有 Minecraft Tool
- 当前 TurnOrigin 实现
- 当前 WebUI Agent / Task 相关页面与 API
- 当前事件总线 / session / conversation 状态存储
- 当前测试体系

不要凭历史阶段报告猜当前架构。

---

# 一、Phase 5A 的定位

这是一个**编排层 / Task Runtime 阶段**。

本阶段：

**不新增 Minecraft 原子工具。**

当前 19 个 Minecraft LLM tools 全部保持原样。

不新增：

```text
minecraft_gather_resource
minecraft_mine
minecraft_collect
minecraft_build
minecraft_task
```

也不把已有多个工具重新包装成一个 Minecraft-specific black box。

目标是增加一个通用的：

```text
TaskRuntime
```

第一期只接入 Minecraft Tool Loop。

---

# 二、5A 要解决的真实问题

当前：

```text id="s9x7ad"
LLM
 ↓
minecraft_move_to
 ↓
RUNNING + action_id
 ↓
当前 Agent Turn 结束
```

Minecraft Action Event：

```text id="5b0v0k"
minecraft.action.completed
```

只更新：

```text id="r8rcqf"
MinecraftAgentContext
```

不会自动启动新的 Agent Turn。

因此当前系统无法可靠完成：

```text id="j3q9kr"
“去那棵树那里，把原木挖下来并捡起来”
```

因为：

```text
move_to
```

完成之后：

> 没有一个持久的“任务”知道自己应该继续下一步。

Phase 5A 就是解决这个问题。

---

# 三、最终目标

让系统能够形成：

```text id="8w1w1q"
USER:
“去附近那棵橡树，挖一块原木并把掉落物捡起来”
        ↓
TaskRuntime
        ↓
PLAN
        ↓
用户确认计划
        ↓
RUNNING
        ↓
minecraft_find_blocks
        ↓
minecraft_dig_capability
        ↓
minecraft_equip
        ↓
minecraft_move_to
        ↓
WAITING_ACTION
        ↓
action.completed
        ↓
resume task
        ↓
minecraft_dig
        ↓
action.completed
        ↓
minecraft_dropped_items
        ↓
minecraft_pickup_item
        ↓
action.completed
        ↓
minecraft_inventory
        ↓
VERIFY
        ↓
TASK SUCCEEDED
```

这里每一步依旧调用**已有原子工具**。

TaskRuntime 不接触 Mineflayer。

---

# 四、最重要的架构边界

必须严格保持：

```text id="lxr2u1"
LLM
 ↓
TaskRuntime
 ↓
Agent Bridge / Tool Loop
 ↓
Policy
 ↓
Confirmation
 ↓
MinecraftService
 ↓
ActionRuntime
 ↓
Mineflayer
```

禁止：

```text id="1ko23x"
TaskRuntime
 ↓
Mineflayer
```

也禁止：

```text id="3m7sga"
TaskRuntime
 ↓
minecraft_runtime HTTP
```

直接调用。

TaskRuntime 只负责：

- 任务状态
- 步骤状态
- 调度
- 等待
- 恢复
- 取消
- checkpoint
- retry / replan
- authorization
- failure classification

---

# 五、第一原则：TaskRuntime 不创造新的 Minecraft 能力

例如：

```text id="20l8f3"
TaskRuntime step:
tool = minecraft_move_to
args = ...
```

而不是：

```text id="qps6u1"
TaskRuntime:
move_to_internal(...)
```

全部实际动作仍然通过：

```text id="rfh2r4"
MinecraftAgentBridge
```

执行。

---

# 六、Task 状态机

新增：

```text id="7r27sa"
TaskState
```

至少：

```text id="h4c9em"
PLANNING
PENDING_CONFIRMATION
RUNNING
WAITING_ACTION
WAITING_USER
PAUSED
REPLANNING
SUCCEEDED
FAILED
CANCELLED
EXPIRED
```

状态必须是显式枚举。

禁止用：

```text id="mx5p98"
None
"running"
"done"
```

这种散落字符串。

---

# 七、Step 状态机

每个 Task 有 ordered steps。

新增：

```text id="v8ltcj"
StepState
```

至少：

```text id="12kqx5"
PENDING
RUNNING
WAITING_ACTION
WAITING_CONFIRMATION
SUCCEEDED
FAILED
SKIPPED
CANCELLED
```

---

# 八、Task 数据结构

建议：

```json id="8o6w7j"
{
  "task_id": "task_...",
  "session_id": "...",
  "user_id": "...",
  "origin": "USER",
  "created_at": "...",
  "updated_at": "...",
  "state": "PENDING_CONFIRMATION",
  "objective": "去附近的橡树挖一块原木并捡回来",
  "current_step": 1,
  "steps": [
    {
      "step_id": "step_1",
      "tool": "minecraft_find_blocks",
      "arguments": {...},
      "risk": "SAFE",
      "state": "PENDING"
    }
  ]
}
```

具体字段名称遵循当前项目模型风格。

---

# 九、Task 必须有 checkpoint

每一个会改变 Task state 的状态转移都必须持久化或至少进入可靠的 session-backed state。

至少：

```text id="y0cryd"
Task 创建
Plan 生成
Confirmation pending
Confirmation consumed
Step started
Step action_id assigned
Action waiting
Action completed
Step succeeded
Step failed
Task paused
Task resumed
Task cancelled
Task succeeded
Task failed
```

不要只有：

```text id="m0iv02"
Python 内存对象
```

然后进程重启全部丢失。

---

# 十、持久化边界

如果当前仓库已有统一数据库 / task persistence：

**复用。**

不要为了 5A 再建立：

```text id="wz8f2b"
task.db
sqlite_tasks.db
```

第二套存储。

如果当前没有合适持久化：

第一期允许：

```text id="d4wdjf"
session-scoped persistent storage
```

但必须明确：

```text id="dc9a7a"
process restart durability = NOT SUPPORTED
```

不要声称“可恢复”到跨进程。

优先使用当前已有 session/state 持久化设施。

---

# 十一、计划与执行必须分离

5A 必须有：

```text id="j09ahc"
TaskPlan
```

和：

```text id="wfvt5k"
TaskExecution
```

两个概念。

Plan 是：

> “准备做什么”。

Execution 是：

> “现在做到哪里”。

---

# 十二、TaskPlan 必须冻结

一旦用户确认：

```text id="z9lkgf"
PLAN_CONFIRMED
```

当前 plan：

```text id="h8i0sr"
不可静默修改
```

如果：

```text id="1na4g6"
step arguments
```

发生变化：

必须：

```text id="6f5oo2"
旧授权失效
→ 新计划 / 新确认
```

特别是 MEDIUM+。

---

# 十三、为什么需要“计划确认”

当前单个 MEDIUM Tool：

```text id="k3f6hd"
第一次调用
→ confirmation_required
```

5A 不能简单让一个多步骤任务：

```text id="a0x5u9"
step1 confirm
step2 confirm
step3 confirm
step4 confirm
```

否则“Task Runtime”基本没有意义。

第一期设计：

> **对一个已经完全展开的冻结任务计划进行一次用户确认。**

确认摘要必须列出全部将执行的世界修改 / 低风险动作。

例如：

```text id="2s7qeg"
任务：
去 11 格外的橡木树。

计划：
1. 走到目标附近
2. 装备 minecraft:stone_axe
3. 挖 1 个 minecraft:oak_log
4. 拾取产生的 minecraft:oak_log
```

用户：

```text id="q2vd6r"
确认
```

然后 Task 才能开始。

---

# 十四、确认不能授权未知未来动作

这是整个 5A 最重要的安全规则之一。

如果已确认：

```text id="zbdtql"
move_to A
equip stone_axe
dig A
pickup entity X
```

之后运行过程中发现：

```text id="4p1yl7"
A 消失
```

TaskRuntime 想改成：

```text id="t5kv3n"
move_to B
dig B
```

这是：

**新的世界修改计划。**

不能因为：

```text id="b7x0ay"
“这个任务已经确认过”
```

就自动授权。

必须：

```text id="h3y2rf"
REPLANNING
→ 新 Plan
→ 新 Confirmation
```

---

# 十五、SAFE / LOW / MEDIUM 的 Task 授权模型

现有直接工具 policy 不要破坏。

为 Task Runtime 增加独立的：

```text id="3jsiqc"
TaskAuthorization
```

其中至少包含：

```text id="1wz8ig"
task_id
user_id
session_id
plan_hash
approved_at
expires_at
approved_steps
```

Task 子步骤执行时：

```text id="elx1k9"
TaskRuntime
→ AgentBridge task-step invocation
```

不是伪造：

```text id="06xj6d"
TurnOrigin.USER
```

**严禁把 Task step 冒充 USER Turn。**

---

# 十六、不要修改 TurnOrigin 语义去“骗过 Policy”

当前：

```text id="o5p8x4"
USER
INITIATIVE
BACKGROUND
SYSTEM
```

继续保持真实来源。

可以新增：

```text id="v6d5qd"
TASK
```

但：

```text id="TASK"
```

不能自动意味着：

```text id="USER"
```

Task step 的授权应该来自：

```text id="3l9wsk"
已确认的 TaskAuthorization
```

而不是：

```text id="0eq2vr"
TurnOrigin.is_user == true
```

---

# 十七、Task Step Authorization

推荐新增：

```text id="3a1ue3"
TaskStepAuthorization
```

字段：

```text id="f24rmd"
task_id
step_id
tool
arguments_hash
risk
plan_hash
status
```

执行前要求：

```text id="ka6l7o"
step.tool
+
canonical arguments
+
plan_hash
```

全部匹配。

---

# 十八、MEDIUM Step

如果计划里有：

```text id="c1ey2k"
minecraft_equip
minecraft_dig
minecraft_place
minecraft_pickup_item
minecraft_inventory_move
minecraft_container_transfer
minecraft_craft
```

必须在 Plan Confirmation 里明确列出。

执行时：

```text id="3w5s1u"
TaskStepAuthorization
```

才允许 TaskRuntime 调用该工具。

如果 arguments 改变：

```text id="4e7u0r"
authorization mismatch
```

Task 不得继续执行。

---

# 十九、LOW Step

现有 LOW：

```text id="fuj8aq"
minecraft_move_to
minecraft_follow_player
```

计划确认后，可以作为：

```text id="0u6y1d"
USER explicitly authorized plan step
```

继续执行。

但：

> 不能因此把 LOW 全局变成“以后随便走”。

授权必须限制在：

```text id="9sr5qv"
task_id
step_id
arguments_hash
```

---

# 二十、SAFE Step

SAFE：

```text id="xw20lo"
minecraft_world
minecraft_inventory
minecraft_recipe_lookup
minecraft_dropped_items
minecraft_find_blocks
minecraft_dig_capability
minecraft_container_inspect
```

不需要计划确认。

TaskRuntime 可以为了：

- 重新验证
- 查找目标
- 判断下一步

在任务执行期间调用 SAFE 工具。

但是：

> SAFE 也不能因此自行改变世界。

---

# 二十一、Dynamic SAFE Replanning

允许：

```text id="3xk4le"
Task 已运行
↓
SAFE 查询发现世界和 Plan 假设不同
↓
Task 暂停当前 step
↓
重新推理
```

例如：

```text id="qjn8f5"
目标木头被别人挖掉
```

可以用 SAFE：

```text id="2izq7k"
minecraft_find_blocks
```

寻找新的候选。

但是如果要产生新的 MEDIUM/LOW 世界动作：

```text id="ny7q4f"
新 Plan
→ 需要重新授权
```

---

# 二十二、Task Runtime 不允许隐式 retry 世界动作

例如：

```text id="0ovv6y"
dig failed
```

不能：

```text id="qx25w1"
自动再 dig 一次
```

尤其：

```text id="5sj2fo"
MEDIUM
```

失败后必须重新判断。

如果相同 arguments、相同目标、相同授权，而且失败属于明确 transient error：

可以有限重试，但：

```text id="a7j8f4"
max_attempts = 1
```

第一期建议：

> **世界修改动作默认不自动重试。**

---

# 二十三、SAFE 查询可以有限 retry

例如：

```text id="m8h5x1"
minecraft_find_blocks
```

因为网络/状态瞬态失败：

可以：

```text id="a6i0oq"
最多 2 次
```

但必须记录：

```text id="32f2sg"
attempt
reason
```

---

# 二十四、Action 完成后的恢复

例如：

```text id="qud6u3"
Task:
step 1 = move_to
```

执行：

```text id="7k8s5h"
RUNNING
```

Task 状态：

```text id="p2jpqa"
WAITING_ACTION
```

保存：

```text id="02chru"
action_id
step_id
```

Action completed：

```text id="9sm4d9"
TaskRuntime 收到事件
```

然后：

```text id="0ykf1k"
验证 action_id 对应当前 step
```

不能：

```text id="z1r2pq"
只看 action = move_to
```

---

# 二十五、不能误消费别的 Action Event

当前系统存在：

```text id="hmneux"
action_id
```

必须严格绑定：

```text id="tu20fc"
task_id
step_id
action_id
```

例如：

```text id="nkr8xw"
Task A waiting action_1
Task B action_2 completes
```

Task A 不能被唤醒。

---

# 二十六、只允许一个 foreground Task

第一期：

```text id="9w3yyr"
每个 Minecraft session
最多一个 RUNNING foreground Task
```

否则：

```text id="fy7o6q"
Task A move_to
Task B dig
```

会互相撞 ActionRuntime。

新 Task：

```text id="s6k3me"
已有 task running
→ task.busy
```

SAFE 查询仍可以正常进行。

---

# 二十七、Task Cancellation

新增：

```text id="6jtsf9"
TaskRuntime.cancel(task_id)
```

用户：

```text id="2g78f7"
“停下这个任务”
```

Task：

```text id="bch2ka"
CANCELLED
```

同时：

```text id="eeb4ly"
如果当前 step 有 foreground action
→ minecraft_stop
```

但：

> TaskRuntime 不能自己直接操纵 Mineflayer。

必须通过现有：

```text id="6v4e1m"
minecraft_stop
```

---

# 二十八、Task STOP 的优先级

Task cancellation 必须：

```text id="owwqjo"
USER CANCEL
```

优先级高于：

```text id="e2i4df"
晚到 action.completed
```

和 4H.1 一样：

```text id="m8mnh1"
CANCELLED
```

不能被迟来的：

```text id="u7w2de"
SUCCEEDED
```

翻案。

---

# 二十九、Task Pause

第一期支持：

```text id="92c9u0"
PAUSED
```

暂停方式：

- 等当前 action 自然结束
- 或在安全边界调用 stop
- 然后保存 checkpoint

不要在：

```text id="rzxstq"
bot.dig()
```

中间硬切 Task state。

---

# 三十、Resume

用户：

```text id="bcp1tu"
“继续”
```

TaskRuntime：

```text id="n3b4d7"
读取 checkpoint
↓
检查 task authorization 是否仍有效
↓
检查 current world facts
↓
继续 next step
```

如果：

```text id="zyb3rd"
MEDIUM authorization 已过期
```

必须：

```text id="km27ni"
重新确认
```

---

# 三十一、Task TTL

不要让 Task 无限存在。

建议：

```text id="m2y3gn"
task.ttl = 10 minutes
```

具体配置按照当前项目风格。

超时：

```text id="p0z03b"
EXPIRED
```

如果当前 action 正在运行：

```text id="lcvp9k"
stop
→ action cancelled
→ task expired
```

---

# 三十二、Step Timeout

Task 自己有：

```text id="ikf8c2"
task TTL
```

Action 已经有：

```text id="ddtnzs"
move/dig/etc timeout
```

第一版不要再给每一个 step 增一套 timeout。

只使用：

```text id="8w7lvt"
existing ActionRuntime timeout
+
Task overall TTL
```

避免两个 timer 语义互相覆盖。

---

# 三十三、Task Failure Classification

至少：

```text id="fkr8s1"
AUTHORIZATION
VALIDATION
TARGET_LOST
ACTION_FAILED
TIMEOUT
CANCELLED
WORLD_CHANGED
OFFLINE
BUSY
INTERNAL
```

不要所有错误都：

```text id="mg6jtf"
FAILED
```

Task UI / LLM 需要知道：

> 是目标没了、动作失败、权限没了，还是世界断线。

---

# 三十四、Failure Handling

第一期：

### SAFE failure

例如：

```text id="j1f8e9"
find_blocks → empty
```

Task 可以：

```text id="1tbj7m"
replan
```

但只能继续调用 SAFE。

### LOW/MEDIUM failure

例如：

```text id="kpp1j2"
dig failed
```

Task：

```text id="s1f4n8"
PAUSED / REPLANNING
```

不要自动进行新的世界修改。

需要用户重新确认新的 plan。

---

# 三十五、Rollback

非常重要：

**不要声称支持通用 rollback。**

Minecraft 世界修改不是事务。

例如：

```text id="60ojwv"
dig stone
```

之后：

```text id="u6b3em"
rollback
```

不等于：

```text id="7pln1t"
place stone
```

因为：

- 原方块状态可能复杂
- 掉落物可能变化
- tool durability 改变
- 玩家状态改变
- 其他玩家可能介入

因此第一期：

```text id="6lplx5"
rollback = NOT SUPPORTED
```

只支持：

```text id="2z3x4m"
stop
pause
replan
resume
```

---

# 三十六、Compensation

以后可以支持：

```text id="n9h8p7"
compensation_action
```

但 5A 第一版不要实现。

代码结构可以预留：

```text id="b0e2ov"
compensatable = false
```

不要假装已有 rollback。

---

# 三十七、Replanning

TaskRuntime 需要一个：

```text id="5hmjle"
replan()
```

但第一期只允许：

```text id="6z08tm"
SAFE observation
+
LLM replan
```

并生成：

```text id="vbl2hp"
new Plan
```

任何新：

```text id="e1g6h8"
LOW
MEDIUM
```

步骤都必须重新进入 confirmation。

---

# 三十八、Plan Hash

每个 Plan 生成：

```text id="nnsrqg"
plan_hash = sha256(canonical_json(plan))
```

Confirmation 绑定：

```text id="g8p4f7"
user
session
task_id
plan_hash
```

任何 Plan 改变：

```text id="0q1c9a"
hash changed
→ authorization invalid
```

---

# 三十九、Step Arguments Hash

每一步：

```text id="g25jhx"
arguments_hash
```

必须 canonical：

```text id="g3j4z0"
sorted keys
normalized item names
normalized coordinates
```

这样：

```text id="ak1vdn"
minecraft:stone_pickaxe
```

与：

```text id="5b5y1j"
stone_pickaxe
```

在已经 canonicalize 的工具里不要产生伪 mismatch。

---

# 四十、Task 与现有 ConfirmationStore 的关系

不要创建：

```text id="b8l7np"
TaskConfirmationStore
```

第二套确认数据库。

扩展现有：

```text id="v1qtn7"
ConfirmationStore
```

或在其上增加：

```text id="a6x9we"
plan_hash
task_id
```

但要保持现有直接工具确认完全兼容。

---

# 四十一、直接 Tool 调用不能受 TaskRuntime 污染

现有：

```text id="5t0zkp"
USER:
“拿石镐”
→ minecraft_equip
→ confirmation
```

仍然正常。

5A 不能导致：

```text id="4ngajv"
所有 Tool 都必须创建 Task
```

只在确实形成多步骤任务时进入 TaskRuntime。

---

# 四十二、如何进入 TaskRuntime

第一期不要增加新的 Minecraft Tool。

TaskRuntime 应由 CharacterRuntime / Agent Loop 根据：

```text id="m1d2x4"
用户意图需要多个连续步骤
```

创建。

建议触发条件：

### 明确多步骤请求

例如：

```text id="7sd4vj"
“去树那里挖一块原木并捡起来”
```

### 单工具不能直接完成但目标明显需要多个阶段

例如：

```text id="c5c3o5"
“帮我拿一块铁”
```

可以产生 TaskPlan。

---

# 四十三、不要误判普通聊天为 Task

例如：

```text id="qj8j2w"
“你现在在哪里？”
```

不产生 Task。

```text id="hzl0c7"
“背包里有什么？”
```

直接 SAFE Tool。

```text id="3r3m6p"
“看看附近有没有铁矿”
```

直接 SAFE `minecraft_find_blocks`。

只有：

```text id="9rtqkm"
需要跨多个 step
```

才创建 Task。

---

# 四十四、Planner

第一期可以使用当前主模型生成 Plan。

但必须：

```text id="zt5h47"
Plan output = structured JSON
```

不要靠自由文本解析：

```text id="g7c4z6"
“第一步……第二步……”
```

建议：

```json id="n5qj9f"
{
  "objective": "...",
  "steps": [
    {
      "tool": "minecraft_find_blocks",
      "arguments": {...}
    }
  ]
}
```

---

# 四十五、Planner 禁止直接执行 Tool

Planner：

```text id="2k8p1e"
只生成 plan
```

Executor：

```text id="7r1yps"
负责真正执行
```

必须分离。

否则：

```text id="gq5n3k"
Plan generation
```

期间可能偷偷执行 MEDIUM。

---

# 四十六、Plan Validation

生成 Plan 后：

必须由 TaskRuntime 做 deterministic validation：

- tool 是否注册
- schema 是否合法
- 参数是否 canonical
- risk 是否知道
- action 是否允许
- 是否在线
- 是否存在不允许的 compound tool
- 是否引用不存在的前置结果

不要让 LLM 自己宣称：

```text id="rj0k22"
“这个计划安全”
```

---

# 四十七、Step Dependencies

允许：

```text id="5j39th"
step 2
depends_on step 1
```

例如：

```text id="kjhwj5"
step1 find_blocks

step2 move_to
  target = step1.matches[0].position
```

这意味着 arguments 可以包含：

```text id="oqo6f0"
reference
```

例如：

```json id="qj8xn6"
{
  "from_step": "step1",
  "path": "matches.0.position"
}
```

但：

> 动态解析出的最终 arguments 必须在执行前生成 canonical snapshot，并参与 authorization hash。

不能只 hash 原始模板。

---

# 四十八、Resolved Arguments

每个 Step 在执行前保存：

```json id="8f1f8h"
{
  "template_arguments": {...},
  "resolved_arguments": {...},
  "arguments_hash": "..."
}
```

这是非常重要的审计证据。

---

# 四十九、为什么需要 resolved arguments

例如：

```text id="b1qk3w"
step:
move_to(match_from_step1)
```

最终真正执行：

```text id="y8qrgv"
x=103
y=64
z=141
```

确认时必须知道：

> 到底要去哪里。

不能用户只确认：

```text id="15wbm7"
“走到刚才找到的那个地方”
```

然后 runtime 自己临时换目标。

---

# 五十、动态观察与计划

允许 SAFE observation 更新：

```text id="f0d5dc"
step precondition
```

例如：

```text id="6k2v35"
find_blocks
```

结果中的坐标被保存。

然后下一步：

```text id="s4ym7q"
move_to
```

使用这个已解析坐标。

如果目标消失：

```text id="x3g6y8"
不自动替换
```

进入：

```text id="0ri4n9"
REPLANNING
```

---

# 五十一、Task Event

新增内部事件：

```text id="tcw2p7"
task.created
task.plan_ready
task.confirmation_required
task.started
task.step_started
task.step_waiting
task.step_succeeded
task.step_failed
task.paused
task.replanning
task.resumed
task.cancelled
task.succeeded
task.failed
task.expired
```

事件 payload 至少包含：

```text id="m9y7j4"
task_id
step_id
state
timestamp
```

不要把 raw Mineflayer 状态塞进 Task Event。

---

# 五十二、Action Event 与 Task Event 的关系

Action：

```text id="4myv1n"
minecraft.action.completed
```

Task：

```text id="3k50ov"
task.step_succeeded
```

映射：

```text id="1g0clg"
action_id
→ 当前 task step
→ step succeeded
→ next step
```

不能：

```text id="k91v0h"
action completed
→ 直接触发新的 Agent Turn
```

必须先通过 TaskRuntime。

---

# 五十三、Task Resume Trigger

当：

```text id="x4t48d"
action.completed
```

出现：

TaskRuntime：

```text id="i8l7u5"
查找 waiting action_id
```

找到：

```text id="n3v1x8"
step
```

然后：

```text id="zj9yp2"
更新 checkpoint
→ step succeeded
→ 执行 next step
```

如果下一步需要 LLM 判断：

```text id="b6i0g0"
启动 planner/executor continuation
```

---

# 五十四、不要把每一步都重新请求 LLM

例如：

```text id="m5e2p0"
find
→ move
→ equip
→ dig
```

如果 Plan 已经明确：

```text id="g0f5sl"
```

就不需要：

```text id="oqx2jd"
每完成一步都完整重新规划
```

只有：

- 条件变化
- step failure
- target lost
- user interrupt
- authorization expired

才需要重新推理。

---

# 五十五、LLM Continuation Context

Resume 时给模型的上下文应该包含：

```text id="j1q4w8"
task objective
current step
completed steps
latest action result
relevant SAFE observations
remaining plan
failure if any
```

不要把：

```text id="vz2o3u"
整个历史 Minecraft raw snapshot
```

塞进去。

---

# 五十六、Context 大小

任务上下文需要有严格上限。

建议：

```text id="i8x4p3"
completed step summary <= 200 chars each
latest result = full structured result
raw snapshots = never
```

不要让 20 步任务把整个对话上下文拖爆。

---

# 五十七、Task Step Result

每一步保存：

```text id="5pyi57"
step result
```

包括：

```text id="5h5u7w"
tool
arguments
action_id
status
summary
structured result
started_at
finished_at
```

但对 LLM 注入时只选相关字段。

---

# 五十八、Task Retry Policy

第一期：

```text id="w9i6ov"
SAFE:
  max 2 retries

LOW:
  max 0 automatic retry

MEDIUM:
  max 0 automatic retry
```

如果 MEDIUM 失败：

```text id="gkp7q0"
Task paused / replanning
```

等待用户 / 新确认。

---

# 五十九、No hidden retries

绝不：

```text id="n4tp9q"
用户以为只挖一次
→ runtime 失败
→ 又偷偷挖一次
```

所有 retry 必须：

```text id="y1i0rc"
Task Event
+
attempt number
```

并遵守 authorization。

---

# 六十、Task User Commands

至少支持：

```text id="lhq3g7"
确认
暂停
继续
停止这个任务
取消任务
```

识别方式必须复用当前 CharacterRuntime 的 USER Turn。

不要新增：

```text id="minecraft_task_confirm"
```

这种工具。

---

# 六十一、“确认”不能确认错误 Task

如果同时存在：

```text id="w2v9ap"
Task A
Task B
```

第一期不允许两个 RUNNING task。

因此：

```text id="v1ttw9"
最多一个 PENDING/RUNNING foreground task
```

确认自动绑定当前 session/user + task_id。

---

# 六十二、Task Confirmation TTL

复用：

```text id="q0x3nj"
existing confirmation.ttl_seconds
```

不再新增另一套 TTL。

Confirmation 过期：

```text id="e3t7uk"
Task → PENDING_CONFIRMATION
```

不要偷偷继续。

---

# 六十三、Task Expiration

如果 Task 自身超过：

```text id="o8k6c4"
task TTL
```

则：

```text id="p7k0g1"
EXPIRED
```

当前动作：

```text id="w5z1q8"
STOP
```

然后记录：

```text id="x6k2cy"
expiration reason
```

---

# 六十四、Task Pause 后世界可能变化

Resume 时必须重新验证：

```text id="5w6p8r"
online
dimension
position
target
```

不能直接：

```text id="3jv1m4"
继续旧动作
```

例如：

```text id="4c7w3y"
原木已被别人挖掉
```

必须：

```text id="k1x0sc"
replan
```

而不是继续旧 dig。

---

# 六十五、Task 不保证世界事务一致性

文档必须明确：

```text id="1u8kpa"
Task Runtime 是工作流编排器，
不是 Minecraft transaction system。
```

因此：

- 不提供通用 rollback
- 不承诺世界状态 atomic
- 每一步都独立验证
- 失败后只能停止 / 重规划 / 用户介入

---

# 六十六、First 5A Task Demo

必须用一个非常小的任务作为真正 E2E：

```text id="h3c2t0"
“去附近找一棵橡木，挖一块原木并捡回来”
```

计划：

```text id="cn7v2x"
1. minecraft_find_blocks
2. minecraft_dig_capability
3. minecraft_equip（如果需要）
4. minecraft_move_to
5. minecraft_dig
6. minecraft_dropped_items
7. minecraft_pickup_item
8. minecraft_inventory
```

---

# 六十七、Task Demo Confirmation

确认摘要：

```text id="w10pph"
任务：去附近找一棵橡木，挖一块原木并捡回来。

将执行：
1. 在 16 格范围内寻找橡木
2. 走到选定的目标位置
3. 如需工具，装备指定工具
4. 挖 1 个橡木原木
5. 只拾取这次挖出的目标掉落物
6. 验证原木进入背包
```

注意：

> 如果 Plan 在确认时还不知道最终坐标，则不得把未来动态目标写成“未知但自动选择”。

应该先执行 SAFE：

```text id="w93m9k"
find_blocks
```

然后生成**最终冻结计划**，再请求用户确认。

---

# 六十八、这意味着 Plan 分两阶段

### Phase A：Observation Plan

只允许：

```text id="m3j7zy"
SAFE
```

例如：

```text id="d3w4n9"
find_blocks
inventory
capability
world
```

### Phase B：Action Plan

包含：

```text id="p4x1z9"
LOW
MEDIUM
```

生成：

```text id="hz3y8m"
resolved arguments
```

然后确认。

这样用户真正确认的是：

> “知道目标在哪里之后，接下来具体要做什么。”

---

# 六十九、不能一次确认“未来任意目标”

禁止：

```text id="7c2m1e"
确认：
“帮我收集木头”
```

然后 TaskRuntime 自己决定：

```text
随便哪棵树
随便多少块
随便走多远
```

第一期的 TaskPlan 必须是**有限、明确、可审计**。

---

# 七十、Task Scope

第一期建议：

```text id="3h9p2s"
max_steps = 16
max_action_steps = 8
max_replans = 2
```

具体数值可配置或常量化。

不要一开始允许：

```text id="pl4u2b"
1000 steps
```

---

# 七十一、Task Liveness

Task Runtime 必须避免：

```text id="koe0w9"
LLM:
“再看看”
→ SAFE
→ “再看看”
→ SAFE
→ 无限循环
```

需要：

```text id="7m2s8a"
max_steps
max_replans
no_progress counter
```

---

# 七十二、No Progress Detector

如果连续：

```text id="1q0z9x"
3 次
```

都满足：

```text id="5v7k2c"
state unchanged
+
same tool
+
same args
```

则：

```text id="h1c3x0"
TASK_STALLED
```

暂停任务。

不要无限让 LLM 重复。

---

# 七十三、Task Planner Loop Protection

禁止：

```text id="x0fz8k"
find
→ same find
→ same find
→ same find
```

无限循环。

记录：

```text id="ax4m2p"
tool
arguments_hash
result fingerprint
```

用于 no-progress 检测。

---

# 七十四、Task Action Busy

如果 Task 尝试启动：

```text id="c6m1o8"
move_to
```

而已有：

```text id="a8q4se"
ActionRuntime foreground action
```

Task 不应该立即重新调用。

状态：

```text id="z7l2v5"
WAITING_ACTION
```

等待对应 action。

---

# 七十五、Unexpected Busy

如果 Task 正在执行：

```text id="7x0m2c"
step move_to
```

却出现：

```text id="y5c1u6"
action.busy
```

因为另一个来源占用了 runtime：

Task：

```text id="r8j1k4"
PAUSED
reason = BUSY
```

不要自动 STOP 别人的 action。

---

# 七十六、Task Event Source

TaskRuntime 应该订阅：

```text id="kp7q10"
MinecraftBridgeEvent
```

但只能消费：

```text id="pl1d9c"
action events
```

而不是直接监听 Mineflayer。

---

# 七十七、Task Event Replay

如果 TaskRuntime 重启：

如果当前 persistence 支持：

可以重新读取：

```text id="4n5x8m"
last task state
```

但第一期不需要恢复正在运行的 Mineflayer action。

如果：

```text id="yr6c2j"
process restarted
```

已有 action_id 已失效：

Task：

```text id="c4w8s2"
FAILED / PAUSED
reason = runtime_restart
```

不要假装 action 仍然存在。

---

# 七十八、Task UI / API

如果当前 WebUI 已有 Task/Agent task 页面：

**复用。**

至少提供：

```text id="h2e1y9"
GET task
GET active task
POST pause
POST resume
POST cancel
```

如果现有 API 风格不同：

遵循现有命名。

---

# 七十九、WebUI Task Detail

显示：

```text id="j7m4v2"
Objective
State
Progress 4/8
Current Step
Current Action
Plan
Confirmation
Last Result
Failure
```

不要显示：

```text id="9q0nwm"
raw Mineflayer
```

---

# 八十、Task Activity

用户能看到：

```text id="w6a8h3"
任务开始
已找到目标
正在走向目标
已到达
正在挖掘
正在寻找掉落物
已拾取
任务完成
```

这些是 Task-level activity。

底层 action activity 仍保留。

不要两边无限重复刷屏。

---

# 八十一、LLM Context

每轮给模型：

```text id="u1w9e3"
Current Task:
state
objective
step
remaining steps
latest result
```

最多：

```text id="m3c5p8"
240~400 chars
```

不要把整个 Task JSON 塞到系统 prompt。

---

# 八十二、Task 完成

只有：

```text id="g3y7p1"
所有 required steps = SUCCEEDED
+
final verification = PASS
```

才：

```text id="q6f4z2"
TASK SUCCEEDED
```

最后一步 action complete 不等于整个 Task complete。

---

# 八十三、Final Verification

Plan 必须定义：

```text id="1r5j7v"
expected_final_state
```

例如：

```json id="3k0n5m"
{
  "inventory": {
    "minecraft:oak_log": {
      "delta_min": 1
    }
  }
}
```

Task 最后使用：

```text id="x0w4t7"
minecraft_inventory
```

重新验证。

不能：

```text id="qz3j6s"
根据历史 action result
```

宣布任务成功。

---

# 八十四、任务结果

Task 最终返回：

```json id="48dn0k"
{
  "task_id": "...",
  "state": "SUCCEEDED",
  "objective": "...",
  "completed_steps": 8,
  "summary": "已挖到并拾取 1 个橡木原木",
  "verification": {
    "inventory_delta": {
      "minecraft:oak_log": 1
    }
  }
}
```

---

# 八十五、Task failure result

例如：

```json id="a8bcx1"
{
  "task_id": "...",
  "state": "FAILED",
  "reason": "TARGET_LOST",
  "step": "minecraft_pickup_item",
  "message": "目标掉落物已被其他玩家拾取"
}
```

不要只返回：

```text
失败
```

---

# 八十六、Tests：核心 TaskRuntime

新增：

```text id="q7x4v2"
tests/test_task_runtime.py
```

至少覆盖：

A. task creation

B. plan validation

C. plan hash

D. task confirmation

E. task authorization

F. step ordering

G. action_id binding

H. action completion resume

I. action failure

J. pause

K. resume

L. cancel

M. expiration

N. no-progress

O. max steps

P. max replans

Q. SAFE dynamic observation

R. MEDIUM plan authorization

S. changed args mismatch

T. new MEDIUM after replan requires new confirmation

U. WebUI cannot self-authorize

V. duplicate task rejected

W. runtime restart state

X. final verification

---

# 八十七、Tests：Security

新增：

```text id="9a7n3f"
tests/test_task_authorization.py
```

必须钉死：

### 1

```text id="0x7p1k"
USER confirms Plan A
```

不能执行 Plan B。

### 2

```text id="5r8n4a"
Step arguments changed
```

→ authorization mismatch。

### 3

```text id="9d4q2z"
Plan hash changed
```

→ confirmation invalid。

### 4

```text id="1s6k8m"
INITIATIVE
```

不能消费 TaskAuthorization。

### 5

```text id="0q4m7x"
BACKGROUND
```

不能消费。

### 6

```text id="n8h3d2"
SYSTEM / WebUI
```

不能自授权。

---

# 八十八、Tests：Minecraft Integration

新增：

```text id="6s2n7q"
tests/test_minecraft_task_integration.py
```

真实/fixture：

```text id="b7x0k1"
find
→ move
→ equip
→ dig
→ pickup
```

验证：

- step transitions
- action events
- confirmation
- task resume
- final inventory verification

不要直接测试 Mineflayer。

---

# 八十九、Node

5A 不新增 Minecraft Runtime action。

所以 Node 只增加：

```text id="5p8y2j"
task event / action bridge integration
```

相关测试。

现有：

```text id="h0b2w6"
ActionRuntime
```

保持原测试全部通过。

---

# 九十、E2E

首先做一个 deterministic fake-server E2E：

```text id="c3w1n7"
User
→ Task created
→ plan confirmed
→ move_to
→ action completes
→ task resumes
→ dig
→ action completes
→ pickup
→ inventory
→ success
```

同时：

```text id="l9m3t4"
failure → pause/replan
```

---

# 九十一、Real Java Smoke

5A 必须新增一个真正的：

```text id="k0v4n5"
TASK E2E
```

目标：

```text id="j9u6b2"
“去附近找一棵橡木，挖一块并捡回来”
```

真实服务器：

```text
find
→ capability
→ equip
→ move_to
→ dig
→ dropped_items
→ pickup
→ inventory
```

但这一次：

> **不能由 Smoke 直接顺序调用 HTTP API 假装 Task Runtime。**

必须让：

```text id="x5p0q8"
真实 TaskRuntime
```

负责执行 step transitions。

---

# 九十二、Real Task E2E 验收

必须看到：

```text id="d5y8n1"
TASK CREATED
TASK PENDING_CONFIRMATION
TASK CONFIRMED
TASK RUNNING
STEP 1 SUCCEEDED
STEP 2 SUCCEEDED
STEP 3 SUCCEEDED
...
TASK SUCCEEDED
FINAL INVENTORY VERIFIED
```

并且：

```text id="m4x7s2"
REAL SERVER: PASS
```

---

# 九十三、Real Pause / Resume

至少再测试一次：

```text id="f7n2w3"
Task running
↓
USER pause
↓
PAUSED
↓
USER resume
↓
continue
↓
SUCCEEDED
```

如果环境无法稳定制造 pause window：

可以在：

```text id="5k1m8q"
SAFE observation / between steps
```

暂停。

但不允许伪造：

```text id="j4p7c9"
“正在 dig 时成功暂停”
```

---

# 九十四、Real Cancel

必须真实验证：

```text id="6v0j2t"
Task running move_to
↓
USER cancel
↓
Task CANCELLED
↓
minecraft_stop
↓
goal null
↓
isMoving false
```

这是 5A 第一个 task-level safety hard gate。

---

# 九十五、没有通用 rollback

Real Task Smoke 只验证：

```text id="62s1r0"
cancel
→ stop
→ no leaked foreground action
```

不要验证：

```text id="0p8q3x"
world restored
```

因为 Task Runtime 不提供 rollback。

---

# 九十六、文档

新增：

```text id="l7y3h8"
docs/MINECRAFT_PHASE5A.md
```

同步：

```text id="s4f0q2"
docs/README.md
CHANGELOG.md
```

配置文档只在确有需要时增加：

```text id="r1u7x5"
task.ttl
task.max_steps
task.max_replans
task.no_progress_limit
```

默认必须保守。

---

# 九十七、不要为了 5A 增加大量配置

第一期只允许：

```text id="q4e9s7"
task.max_steps = 16
task.max_replans = 2
task.no_progress_limit = 3
task.ttl = 10m
```

其余使用代码常量。

---

# 九十八、Backward Compatibility

现有单工具行为必须完全不变：

```text id="u8k1v2"
USER:
“跟着我”
→ minecraft_follow_player
```

仍然直接 tool call。

```text id="v4n6c0"
USER:
“把石镐拿到手里”
→ minecraft_equip
→ confirmation
```

仍然直接 tool call。

只有：

```text id="8a3m7y"
多步骤任务
```

才创建 TaskRuntime。

---

# 九十九、禁止将所有 Agent Turns Task 化

这条非常重要。

不要：

```text id="l5k2q9"
每次 CharacterRuntime 响应
→ task.create()
```

否则 CatooBot 会变成一个笨重 workflow engine。

Task 是：

> **跨多个行动步骤、需要异步等待/状态恢复/任务生命周期管理的长期意图。**

普通对话仍然是普通对话。

---

# 一百、5A 的最终硬门禁

必须：

```text id="e9q5k2"
pytest 全绿
ruff check
ruff format --check
mypy
Vitest 全绿
Playwright 全绿
WebUI build 全绿
Node 全绿
```

并且：

```text id="3g5j0m"
REAL SERVER: PASS
```

硬门禁：

```text
Plan generation              PASS
Plan confirmation             PASS
Task persistence/checkpoint   PASS
Async action resume           PASS
MEDIUM authorization          PASS
changed-step rejection       PASS
SAFE dynamic observation      PASS
Task pause                    PASS
Task resume                   PASS
Task cancel                   PASS
Action cancel propagation     PASS
No progress guard             PASS
Final verification            PASS
Real resource task            PASS
Real Task cancellation       PASS
No leaked foreground action  PASS
```

---

# 一百零一、最终报告格式

```text id="q6m9w1"
Phase 5A = PASS / BLOCKED

commit:
<sha>

TaskRuntime:
state_machine = ...
checkpoint = ...
plan_confirmation = ...
task_authorization = ...
pause/resume = ...
cancel = ...
replan = ...
retry_policy = ...

Minecraft:
atomic tools = unchanged
compound Minecraft tool = NONE

自动化:
pytest = ...
Node = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
task creation = ...
confirmation = ...
find = ...
capability = ...
equip = ...
move_to = ...
dig = ...
dropped_items = ...
pickup = ...
inventory verification = ...
pause/resume = ...
cancel = ...
final verification = ...
ensureIdle = ...

REAL SERVER: PASS / BLOCKED
```

任何没有真实执行的：

```text id="0c4m7v"
SKIPPED
```

不得写 PASS。

---

# 一百零二、5A 完成后的架构

最终形成：

```text
                    CharacterRuntime
                           │
                           ▼
                    ┌──────────────┐
                    │ TaskRuntime  │
                    └──────┬───────┘
                           │
                  Plan / State / Checkpoint
                           │
                           ▼
                    Agent Bridge
                           │
               ┌───────────┴───────────┐
               ▼                       ▼
          SAFE Queries            Action Tools
               │                       │
               │                Confirmation
               │                       │
               └───────────┬───────────┘
                           ▼
                   MinecraftService
                           │
                           ▼
                    ActionRuntime
                           │
                           ▼
                       Mineflayer
```

其中：

```text
TaskRuntime
```

永远不知道：

```text
Block digging packets
Pathfinder internals
Mineflayer Item objects
Container windows
```

它只认识：

```text
Tool
Arguments
Risk
Authorization
Action ID
Result
Task state
```

这才是 5A 真正应该建立的抽象。

---

# 一百零三、第一条正式 Task

5A 完成时，必须可以真实跑通：

```text
“去附近找一棵橡木，挖一块原木并捡回来”
```

并且用户只需要**确认一次冻结后的完整计划**。

最终：

```text
find
→ move
→ equip
→ dig
→ dropped item
→ pickup
→ inventory
→ task success
```

全部由已有原子工具完成。

**不新增 `minecraft_gather_resource`。**

5A 的成果不是“又增加一个 Minecraft 工具”，而是：

> **CatooBot 第一次拥有真正的“任务生命期”。**