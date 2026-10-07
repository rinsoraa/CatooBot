基于当前最终基线 `90854ac` 开始 Phase 5A.1。

这是 TaskRuntime 的可靠性强化阶段。

## 一、范围

本阶段：

**不新增任何 Minecraft LLM Tool。**

当前 19 个 Minecraft tools 必须保持不变。

**不新增任何 Minecraft ActionRuntime action。**

禁止新增：

```text
minecraft_task
minecraft_replan
minecraft_recover
minecraft_resume
minecraft_retry
minecraft_gather_resource
```

TaskRuntime 仍然只是：

```text
Plan
State
Authorization
Checkpoint
Recovery
Replanning
```

---

# 二、5A 当前已经 PASS 的能力

不要重复实现：

- TaskState / StepState
- Plan / Execution 分离
- plan_hash
- TaskAuthorization
- TaskStepAuthorization
- TurnOrigin.TASK
- action_id 绑定
- pause / resume / cancel / expire
- SQLite checkpoint
- SAFE retry
- LOW/MEDIUM 不自动 retry
- final inventory verification
- task event
- 单 foreground Task

当前这些全部保持兼容。

---

# 三、本阶段只补三条“真机尚未证明”的能力

### A. Real Replanning

真实世界发生变化：

```text
已确认 Plan
↓
目标消失 / 方块被改变
↓
旧 Step 失败
↓
REPLANNING
↓
SAFE observation
↓
生成新 Plan
↓
新 plan_hash
↓
新 Confirmation
↓
继续执行
```

---

### B. Authorization Expiry

确认后等待授权 TTL 到期：

```text
authorized
↓
expires
↓
PENDING_CONFIRMATION
↓
不能继续执行 LOW/MEDIUM
↓
用户重新确认
↓
执行
```

---

### C. Runtime Restart Recovery

明确支持：

```text
SQLite 读回 Task
```

但：

> **不恢复旧 Mineflayer action。**

如果 checkpoint 显示：

```text
WAITING_ACTION
action_id = old
```

进程重启后必须：

```text
old action = invalid
↓
RUNTIME_RESTART
↓
PAUSED / REPLANNING
```

绝不能：

```text
假设 action 仍在运行
假设 action 已成功
自动重复 MEDIUM action
```

---

# 四、Replanning 的安全原则

这是本阶段最重要的规则：

> **旧 Plan 的授权绝不能授权新 Plan。**

例如：

原计划：

```text
find oak_log @ A
move_to A
dig A
pickup X
```

用户已经确认。

执行过程中：

```text
A 已经被别人挖掉
```

TaskRuntime 不允许直接：

```text
find oak_log @ B
move_to B
dig B
```

而必须：

```text
TARGET_LOST / WORLD_CHANGED
↓
REPLANNING
↓
SAFE observation
↓
new Plan
↓
new plan_hash
↓
new confirmation
```

---

# 五、Replanning 不允许自动产生新的世界动作

Replanning 阶段允许：

```text
minecraft_world
minecraft_inventory
minecraft_find_blocks
minecraft_dig_capability
minecraft_dropped_items
minecraft_recipe_lookup
minecraft_container_inspect
```

等 SAFE 查询。

禁止直接：

```text
minecraft_move_to
minecraft_equip
minecraft_dig
minecraft_place
minecraft_pickup_item
minecraft_inventory_move
minecraft_container_transfer
minecraft_craft
```

直到新 Plan 获得授权。

---

# 六、Plan Hash

重新规划后：

```text
old_plan_hash != new_plan_hash
```

必须。

不能：

```text
重新规划
但沿用旧 plan_hash
```

否则旧授权可能错误覆盖新动作。

---

# 七、Plan History

一个 Task 必须保存：

```text
plan_version
plan_hash
created_at
confirmed_at
superseded_at
reason
```

例如：

```text
Plan v1
→ WORLD_CHANGED
→ superseded

Plan v2
→ user confirmed
→ active
```

不要覆盖旧 Plan。

至少 checkpoint 中要保留审计信息。

---

# 八、Replan Reason

至少支持：

```text
TARGET_LOST
WORLD_CHANGED
RUNTIME_RESTART
AUTHORIZATION_EXPIRED
```

不要所有情况写成：

```text
REPLANNING
```

LLM / WebUI 必须知道为什么重新规划。

---

# 九、重新规划后的 Confirmation

确认摘要必须明确这是新计划。

例如：

```text
原计划中的橡木已不存在。

新的计划：
1. 寻找新的橡木
2. 前往新的目标
3. 挖一块原木
4. 拾取掉落物

需要重新确认。
```

绝不能：

```text
继续执行
```

---

# 十、Authorization Expiry

不要修改现有：

```text
minecraft.agent.confirmation.ttl_seconds
```

直接复用当前 TaskAuthorization 的 expires_at。

测试中可以使用：

```text
极短 TTL / fake clock
```

不要真的让真机等待 600 秒。

---

# 十一、Fake Clock

TaskRuntime 单测应该增加：

```text
Clock abstraction
```

或复用当前已有 clock 注入。

必须能 deterministic 测：

```text
t0 confirmed
t0 + ttl - 1
→ valid

t0 + ttl
→ expired
```

禁止：

```text
time.sleep(600)
```

---

# 十二、Authorization Expired 行为

如果当前 Task 正在：

```text
PENDING_CONFIRMATION
```

保持。

如果当前：

```text
PAUSED
```

保持：

```text
PENDING_CONFIRMATION
```

如果当前 action 已经正在运行：

不要强行终止底层 action。

必须在安全边界处理。

与现有 pause/cancel contract 保持一致。

---

# 十三、Runtime Restart

实现一个明确的：

```text
recover_persisted_tasks()
```

或者复用当前 TaskRuntime 启动恢复入口。

启动时扫描：

```text
agent_task_runs
```

找到：

```text
WAITING_ACTION
RUNNING
```

之类的非终态任务。

---

# 十四、Runtime Restart 恢复规则

如果 step：

```text
WAITING_ACTION
```

且保存：

```text
action_id
```

必须：

```text
action_id 失效
```

生成：

```text
RUNTIME_RESTART
```

不要查询 Mineflayer 去“猜”。

因为旧 Node runtime/action lifecycle 已不存在。

---

# 十五、Restart 后 Task 状态

建议：

```text
WAITING_ACTION
→ PAUSED
```

failure/reason：

```text
RUNTIME_RESTART
```

并进入：

```text
REPLANNING
```

具体状态由当前 TaskState 设计决定，但最终必须满足：

> **不能直接从旧 step 继续执行世界动作。**

---

# 十六、重启后的 SAFE Recovery

重启后允许：

```text
minecraft_world
minecraft_inventory
minecraft_find_blocks
minecraft_dropped_items
```

重新观察。

重新检查：

```text
online
dimension
position
inventory
target
```

---

# 十七、Restart 后禁止的事情

绝对禁止：

```text
自动重新 dig
自动重新 place
自动重新 equip
自动重新 pickup
自动重新 move_to
```

直到：

```text
new plan
+
new authorization
```

成立。

---

# 十八、避免重复世界动作

典型场景：

```text
旧 action：
dig oak_log

进程重启
↓
Task 看到 step=dig
```

绝不能：

```text
再次 dig
```

因为第一次可能实际上已经把方块挖掉，只是 checkpoint 没来得及记录。

正确：

```text
RUNTIME_RESTART
↓
SAFE world observation
↓
发现 oak_log 已经变 air
↓
旧目标已完成/世界已变化
↓
REPLAN
```

---

# 十九、最终一致性原则

Recovery 时：

> **世界事实优先于旧 action 状态。**

例如：

旧 checkpoint：

```text
dig = RUNNING
```

真实世界：

```text
target = air
```

不能再次 dig。

应该：

```text
WORLD_CHANGED / TARGET_ALREADY_COMPLETED
```

然后重新规划。

---

# 二十、Action Completed 与 Restart Race

测试：

```text
action completed
```

刚好发生在：

```text
checkpoint save
```

之前。

重启后：

旧 Task 可能还显示：

```text
WAITING_ACTION
```

但世界已经改变。

必须：

```text
SAFE observation
→ reconcile
```

而不是重复执行。

---

# 二十一、Reconciliation

新增内部：

```text
reconcile_task(record)
```

目标：

> 对持久化的 Task 与当前真实 Minecraft 状态做有限、只读的事实对账。

只允许 SAFE 查询。

输出：

```text
RECONCILED
WORLD_CHANGED
TARGET_LOST
TARGET_ALREADY_DONE
OFFLINE
UNKNOWN
```

不要增加 Tool。

---

# 二十二、Reconciliation 不等于自动修复

如果发现：

```text
TARGET_LOST
```

不要自动：

```text
找另一个目标
```

只进入：

```text
REPLANNING
```

交给 Planner。

---

# 二十三、Real Replan 场景

必须创建一个真实 Java Server smoke：

```text
Task Plan v1
```

例如：

```text
find oak_log
→ move_to A
→ dig A
→ pickup
```

确认 v1。

然后在执行到：

```text
find / before move
```

或安全边界上：

让另一个测试客户端 / smoke 操作：

```text
把目标 A 挖掉
```

或者等环境自然变化。

Task：

```text
WORLD_CHANGED
```

必须停止旧 Plan。

---

# 二十四、新 Plan

然后：

```text
SAFE find_blocks
```

找到：

```text
oak_log B
```

生成：

```text
Plan v2
```

要求：

```text
plan_hash != v1
```

并：

```text
PENDING_CONFIRMATION
```

---

# 二十五、Real Replan Confirmation

真实用户再：

```text
确认
```

Task 才允许继续：

```text
move_to B
dig B
pickup B
```

最终：

```text
TASK SUCCEEDED
```

---

# 二十六、Real Authorization Expiry

不要真的等 600 秒。

Smoke/Test 专用配置：

```text
task.ttl / confirmation ttl
```

应该可以通过 test clock 或测试 override 设置成：

```text
1~2 seconds
```

流程：

```text
plan confirmed
↓
expire
↓
尝试执行 MEDIUM
↓
PENDING_CONFIRMATION
↓
确认
↓
继续
```

必须证明：

> 旧 confirmation 不能直接消费。

---

# 二十七、Real Runtime Restart Smoke

必须使用实际 SQLite。

流程：

```text
Task created
→ plan confirmed
→ step waiting_action
→ stop Python process
→ restart
→ load SQLite
→ detect RUNTIME_RESTART
→ reconcile
→ paused / replanning
```

这里允许：

```text
Action = old invalid
```

**不要尝试让 Node action 继续。**

---

# 二十八、Restart Smoke 不要求自动完成 Task

第一期只要求：

```text
安全恢复
```

即：

```text
不会继续执行旧动作
不会重复 world mutation
Task state 可读
原因正确
需要重新规划/确认
```

这是 PASS。

---

# 二十九、Task Event

新增：

```text
task.replanning
task.recovered
task.authorization_expired
```

如果当前命名体系允许。

事件 payload 继续保持：

```text
task_id
step_id
state
timestamp
```

不要塞 raw world state。

---

# 三十、Checkpoint Audit

追加：

```text
RUNTIME_RESTART
RECONCILED
REPLANNING
AUTHORIZATION_EXPIRED
```

每一步都可审计。

---

# 三十一、WebUI

Task Detail 增加：

```text
Plan version
Plan hash
Recovery reason
Replan count
Authorization expiry
```

并能明确看到：

```text
Plan v1
SUPERSEDED

Plan v2
PENDING_CONFIRMATION
```

不能只显示一个当前 Plan。

---

# 三十二、用户体验

用户看到：

```text
目标已经发生变化。
原计划已停止。

新的计划：
……
请确认是否继续。
```

而不是：

```text
任务失败
```

也不要：

```text
正在自动寻找替代目标……
```

直到新计划被确认。

---

# 三十三、QQ 暂不接入

本阶段不要接 QQ Task Entry。

保持：

```text
QQ → 单工具
Minecraft Chat → Task
WebUI → Task
```

QQ Task 作为下一阶段 5B。

这样先把 Task Runtime 的生命周期稳定下来。

---

# 三十四、不要新增 Tool

本阶段必须保持：

```text
Minecraft tools = 19
```

不得增加：

```text
minecraft_replan
minecraft_recover
minecraft_task
```

---

# 三十五、Tests：Task Recovery

新增：

```text
tests/test_task_recovery.py
```

覆盖：

A. load WAITING_ACTION

B. invalid old action

C. RUNTIME_RESTART

D. reconcile

E. target already changed

F. target lost

G. no duplicate world action

H. authorization expiry

I. re-confirmation

J. plan version increment

K. plan hash changes

L. recovery checkpoint

M. recovered event

N. replanning event

O. final resume after re-confirmation

---

# 三十六、Tests：Replanning Security

新增：

```text
tests/test_task_replanning_security.py
```

必须验证：

1. v1 confirmation 不能授权 v2

2. v2 hash != v1 hash

3. v1 action arguments 不能被替换

4. replan 后 MEDIUM step 在未确认时永远拒绝

5. TASK token 不能跨 task 使用

6. TASK token 不能跨 step 使用

7. expired authorization 不能执行

8. WebUI SYSTEM 不能确认

---

# 三十七、Tests：Persistence

验证：

```text
create
save
load
```

不是只测试：

```text
InMemoryTaskStore
```

必须至少使用当前真正 SQLite Store。

---

# 三十八、Integration

扩展：

```text
tests/test_minecraft_task_integration.py
```

覆盖：

```text
task
→ action
→ world change
→ reconcile
→ replan
→ confirm
→ action
```

---

# 三十九、Node 不需要新增 Minecraft Action

保持当前：

```text
Node runtime tests
```

全部回归。

不要为了 recovery 去改：

```text
ActionRuntime
```

除非真实测试发现明确 bug。

---

# 四十、flying-squid

本阶段不强求跨进程 fake Node。

重点：

```text
SQLite
Python TaskRuntime
real Minecraft
```

---

# 四十一、Real Server Hard Gate

必须：

```text
REAL SERVER: PASS
```

至少包含：

```text
initial plan = PASS
initial confirmation = PASS
world change = PASS
old plan rejected = PASS
replan generated = PASS
new plan hash = PASS
new confirmation = PASS
new world action = PASS
final inventory = PASS
```

以及：

```text
authorization expiry = PASS
```

以及：

```text
runtime restart safety = PASS
```

---

# 四十二、Recovery Hard Gate

真实服务器至少验证：

```text
没有重复挖
没有重复放
没有重复 equip world side effect
没有旧 action 被继续执行
没有旧 confirmation 被复用
```

---

# 四十三、Replanning Hard Gate

必须真实看到：

```text
Plan v1
→ WORLD_CHANGED
→ Plan v1 superseded
→ Plan v2
→ new confirmation
→ Plan v2 success
```

没有这条：

```text
Phase 5A.1 = BLOCKED
```

---

# 四十四、Authorization Hard Gate

必须证明：

```text
v1 confirmed
→ v2 not confirmed
→ MEDIUM denied
```

即使：

```text
task_id 相同
user 相同
```

也必须拒绝。

因为：

```text
plan_hash different
```

---

# 四十五、最终报告

沿用 5A 格式：

```text
Phase 5A.1 = PASS / BLOCKED

commit:
<sha>

Replanning:
world_changed = ...
new_plan = ...
new_confirmation = ...
plan_hash = ...

Recovery:
sqlite load = ...
runtime_restart = ...
reconcile = ...
no_duplicate_action = ...

Authorization:
expiry = ...
old_plan_reuse = ...
new_plan_requires_confirm = ...

自动化:
pytest = ...
Node = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
Plan v1 = ...
WORLD_CHANGED = ...
Plan v2 = ...
Confirmation v2 = ...
Task resumed = ...
Final verification = ...

Runtime Restart:
PASS / BLOCKED

REAL SERVER: PASS / BLOCKED
```

所有没有真正执行：

```text
SKIPPED
```

不得伪造 PASS。

---

# 四十六、5A.1 完成后的架构状态

最终：

```text
TaskRuntime
├── Plan
├── Authorization
├── Checkpoint
├── Action Wait
├── Pause
├── Cancel
├── Expire
├── Reconcile
├── Replan
└── Recovery
```

并且：

```text
旧 Plan
  └─ 不会授权新 Plan

旧 Action
  └─ 不会在 restart 后继续

旧 Confirmation
  └─ 不会授权新 arguments

旧世界假设
  └─ 不会直接驱动新 World Action
```

5A.1 的成果不是“多一个功能”，而是让 TaskRuntime 从：

> **能连续执行**

升级成：

> **面对世界已经变化时仍然不会偷偷做错事。**

完成后再进入：

```text
Phase 5B = QQ Task Entry + Unified Task Control
```

把 QQ 的自然语言任务正式接进已经稳定的 TaskRuntime。