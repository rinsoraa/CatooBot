# CatooBot v2.1 Phase 7.1
## Resume Binding Integrity

### 一、当前基线

当前最新 commit：

`d903a14474554b754d659a491b707ad7ebe8b943`

已完成：

- Goal Layer
- Goal persistence
- Goal → Decision → Action
- Goal interruption / resume
- Action Instance identity
- blocked → active recovery
- startup depleted resource recovery
- 1171 tests
- CI success

本轮不是 Phase 8。

只修：

1. `GoalManager.rebind_instance()` 的错误匹配边界
2. Resume 后 Action Instance 绑定的立即持久化

---

# 二、问题 1：rebind_instance 必须只操作“正在等待 resume 的 active Step”

当前逻辑：

```python
if step.action_id != action_id:
    continue

if step.action_instance_id not in ("", old_instance_id):
    continue

step.action_instance_id = new_instance_id
return True
```

问题：

它没有检查：

```text
goal.status
step.status
```

可能把一个：

```text
failed / blocked / pending
```

的旧 GoalStep 错误重绑。

---

# 三、严格匹配规则

`rebind_instance()` 只能匹配：

```text
goal.status == active
```

并且：

```text
step.status == active
```

并且：

```text
step.action_id == action_id
```

并且：

```text
step.action_instance_id == old_instance_id
```

这里不要再允许：

```text
step.action_instance_id == ""
```

作为 resume rebinding 的默认匹配。

因为 Resume 的定义是：

> 一个已经运行中的 Action 被中断，然后创建一个新的 ActionInstance。

所以必须有：

```text
old instance
→
new instance
```

明确的一一对应关系。

---

# 四、如果 old_instance_id 不存在

如果调用：

```text
rebind_instance(
    old_instance_id="",
    new_instance_id="..."
)
```

必须返回：

```text
False
```

并且：

- 不修改任何 GoalStep
- 不创建新的 GoalStep
- 不修改任何 Goal 状态

---

# 五、如果匹配超过一个

理论上不应该发生。

但为了防御：

```text
matches > 1
```

必须：

```text
拒绝重绑
```

而不是：

```text
第一个命中就 return True
```

返回：

```text
False
```

并记录明确的 debug / warning 信息。

不要静默把错误状态写入某个 Goal。

---

# 六、核心回归测试

新增：

```text
TestResumeRebindOnlyTouchesActiveStep
```

构造：

```text
Goal A:
status = blocked
step.status = failed
action_id = same
instance_id = ""

Goal B:
status = active
step.status = active
action_id = same
instance_id = OLD
```

执行：

```text
rebind_instance(
    action_id=same,
    old_instance_id=OLD,
    new_instance_id=NEW
)
```

必须：

```text
Goal A:
instance_id 仍为空

Goal B:
instance_id == NEW
```

---

# 七、测试 active Goal + non-active Step

分别构造：

```text
Goal.status = active
Step.status = failed
```

和：

```text
Goal.status = blocked
Step.status = active
```

两种情况都必须：

```text
rebind = False
```

不能修改。

---

# 八、测试 missing old instance

```text
old_instance_id=""
```

必须：

```text
return False
```

并且状态零变化。

---

# 九、问题 2：Resume rebind 必须立即持久化

当前流程：

```text
ACTION_INTERRUPTED
        ↓
_resume_interrupted()
        ↓
_start_action()
        ↓
new ActionInstance
        ↓
rebind_instance()
        ↓
继续运行
```

`rebind_instance()` 当前是同步内存修改。

需要确保：

> 一旦新的 ActionInstance 已经正式启动，GoalStep 的新的 instance_id 也已经持久化。

---

# 十、不要新建 Persistence System

复用现有：

```text
GoalManager._persist(goal)
SandboxStore.save_goal()
```

不要：

```text
GoalRebindRepository
GoalResumeStore
```

第二套持久化。

---

# 十一、推荐接口

可以把：

```python
rebind_instance()
```

改成 async：

```python
async def rebind_instance(...)
```

然后：

```text
_resume_interrupted()
    ↓
instance = _start_action(...)
    ↓
await goals.rebind_instance(...)
```

内部：

```text
找到唯一 active step
    ↓
修改 action_instance_id
    ↓
更新 goal.updated_at
    ↓
await _persist(goal)
```

如果你认为保持同步更符合当前架构，也可以：

```text
rebind_instance()
    ↓
mark_dirty
    ↓
await persist
```

但最终必须保证：

> Resume 成功返回后，数据库已经记录新的 Action Instance。

---

# 十二、持久化失败怎么办

如果：

```text
Action 已经启动
+
GoalStep persistence 失败
```

不能假装成功。

需要：

```text
记录明确错误
```

并确保下次启动能够安全恢复。

优先方案：

```text
Action Instance 是世界运行事实
GoalStep 是目标执行状态
```

因此如果 Goal persistence 失败：

- 不要创建第二个 Goal
- 不要再启动第二个 Action
- 保留当前 Action
- 标记 Goal runtime dirty / pending persistence
- 在现有 flush 机制再次尝试
- 不要直接把世界 rollback

不要为这个问题引入新的事务系统。

---

# 十三、增加崩溃恢复测试

新增：

```text
TestResumeRebindPersistsBeforeNextTick
```

流程：

```text
Goal
↓
Action Instance A
↓
interrupt
↓
resume
↓
Action Instance B
↓
rebind
↓
立即重新构建 Runtime / 读取 DB
```

验证：

```text GoalStep.action_instance_id == B
```

而不是：

```text A
```

---

# 十四、强制验证完成关联

继续保证：

```text
ACTION_COMPLETED(instance=B)
```

只推进：

```text
GoalStep(instance=B)
```

并且：

```text GoalStep(instance=A)
```

绝不能再次推进。

---

# 十五、不要改变现有三项修复

保持：

### blocked → active

```text
blocked
↓
cooldown
↓
合法重试
↓
active
```

### startup resource recovery

继续：

```text
ActionDefinition.effects
↓
restockable targets
```

不要重新依赖 inventory mapping 中的 0-count key。

### exact completion match

继续：

```text action_instance_id
```

优先匹配。

---

# 十六、不要修改 Goal Priority

本轮不动：

```text
pet care > resource shortage > project
```

不改变 priority 体系。

---

# 十七、不要增加新功能

禁止：

- Phase 8
- Relationship System
- Social Memory
- Persona adaptation
- New Planner
- Agent Planner
- New Goal System
- New ActionSystem
- New EventBus
- New Mutation System
- Memory → Goal
- LLM Planner
- Embedding
- Vector Search
- QQ architecture
- Character Bible
- Git history rewrite

---

# 十八、工程测试

执行：

```powershell
ruff check .
ruff format --check .
mypy app
pytest -q
```

要求：

```text
原 1171 tests 全部保留
+
本轮 remediation tests
=
全部通过
```

CI 必须 success。

---

# 十九、源码审计

检查：

```text
app/sandbox/goals.py
app/sandbox/runtime.py
```

搜索：

```text
rebind_instance
action_instance_id
current_step
StepStatus.active
StepStatus.failed
GoalStatus.active
GoalStatus.blocked
```

确认：

```text
只有 active Goal + active Step
```

可以执行 resume rebind。

---

# 二十、最终报告

必须报告：

```text
Phase 7.1 Remediation commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

rebind scope:
<最终匹配规则>

blocked/failed step protection:
<测试结果>

missing old instance:
<测试结果>

ambiguous match:
<测试结果>

resume persistence:
<实现方式>

crash/restart rebind:
<测试结果>

same action_id multi-goal:
<结果>

是否修改 Goal priority:
No

是否增加 Planner:
No

是否增加 Agent:
No

是否修改 Memory → Goal:
No

是否进入 Phase 8:
No
```

完成后停止。

---

# 二十一、最终验收标准

必须：

```text
✅ rebind 只修改 active Goal
✅ rebind 只修改 active GoalStep
✅ 必须存在 old_instance_id
✅ 同一 action_id 的其他 Goal 不受影响
✅ failed/blocked/pending Step 不会被误绑定
✅ ambiguous match 拒绝
✅ Resume 后新 instance_id 立即持久化
✅ Runtime 崩溃/重建后仍识别新 instance
✅ ACTION_COMPLETED(new instance) 只推进正确 Step
✅ 原 1171 tests 全部保留
✅ 新增测试全部通过
✅ CI success
```

本轮完成后停止。

**不要进入 Phase 8。**