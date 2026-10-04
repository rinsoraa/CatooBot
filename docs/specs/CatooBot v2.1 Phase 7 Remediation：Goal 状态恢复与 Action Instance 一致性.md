# CatooBot v2.1 Phase 7 Remediation
## Goal State Recovery + Action Instance Consistency

### 一、当前基线

当前 Phase 7 commit：

`5224af3e488c79d1bc0cf293bca9f562d154874d`

当前已完成：

- Goal / GoalStep
- GoalDetector
- GoalManager
- Goal persistence
- Goal → Decision → Action
- Goal interruption / resume
- 24h autonomous simulation
- deterministic same-seed simulation
- Character isolation
- Memory downstream boundary

当前测试：

`1164 passed`

CI success。

本轮不是 Phase 8。

只修以下三个明确问题。

---

# 二、问题 1：Blocked Goal 恢复后必须重新进入 Active

当前：

```text id="vtj8ts"
GoalStatus.blocked
    ↓
cooldown 结束
    ↓
active_goal() 再次选中
    ↓
advance()
```

但 `advance()` 只有：

```python id="gxgq2b"
if goal.status is GoalStatus.pending:
    goal.status = GoalStatus.active
```

因此 blocked goal 成功恢复后可能仍保持：

```text
status = blocked
```

却正在执行 Action。

这是状态语义错误。

---

## 要求

当一个：

```text id="jh9n1r"
blocked
```

Goal 的 cooldown 到期，且：

```text
next step
+
candidate
+
Decision
+
Action
```

全部重新合法后：

必须：

```text id="32w8zq"
status = active
```

并发布：

```text id="igq7wa"
GOAL_ACTIVATED
```

原因例如：

```text
reason = "resumed_after_cooldown"
```

---

## 不要

不要重新创建 Goal。

不要改变 goal_id。

不要重新创建 correlation。

不要把 blocked Goal 克隆成新 Goal。

---

## 测试

新增：

```text id="3k0n8c"
TestBlockedGoalReturnsToActiveAfterSuccessfulRetry
```

流程：

```text
Goal created
→ blocked
→ cooldown
→ retry
→ step valid
→ status = active
→ Action starts
```

验证：

```text goal_id 未变化
GOAL_ACTIVATED 出现
GOAL_BLOCKED 历史保留
status == active
```

---

# 三、问题 2：GoalStep 必须绑定 Action Instance，而不是只绑定 Action Definition

这是本轮最重要的修复。

当前 GoalStep 已经保存：

```text id="y52w1t"
result["action_instance"] = started.id
```

但：

```text id="8wklhg"
GoalManager.on_action_completed()
```

仍然只按照：

```text action_id == step.action_id
```

匹配。

这不够可靠。

---

# 四、建立明确的 Action Instance Identity

不要重新设计 ActionSystem。

优先复用现有：

```text id="8w4z1q"
ActionInstance.id
```

使 GoalStep 能够明确知道：

> 当前这个 Step 正在等待哪个具体 Action Instance 完成。

---

## 推荐方案

扩展 `ACTION_STARTED` / `ACTION_COMPLETED` payload：

```json id="0p6m44"
{
  "action_id": "buy_cola",
  "action_instance_id": "actinst_xxx"
}
```

或者使用你当前系统等价字段。

必须保证：

```text id="1e3bqj"
ACTION_STARTED
    ↓
ACTION_COMPLETED
```

能够关联同一个 Action Instance。

---

# 五、GoalStep 保存 Action Instance ID

新增明确字段，或统一使用已有：

```text id="1mxxep"
current_step.action_instance_id
```

如果模型已经有：

```text id="y6z4hb"
result["action_instance"]
```

可以做正式字段化。

不建议长期依赖：

```python
result["action_instance"]
```

这种弱结构。

最终建议：

```text id="f0r53o"
GoalStep
├── action_id
├── action_instance_id
└── status
```

---

# 六、Action Completed 匹配规则

`on_action_completed()` 必须优先：

```text id="2br32v"
event.action_instance_id
        ↓
GoalStep.action_instance_id
```

只有在历史兼容场景：

```text id="v1l0t7"
action_instance_id 缺失
```

才允许：

```text
action_id
```

作为 fallback。

并且 fallback 必须严格限制：

> 只能匹配当前唯一 active step。

禁止：

```text
遍历所有 Goal
只要 action_id 相同全部完成
```

---

# 七、Action Resume 必须重新绑定 Instance

Phase 3 的 Interrupt → Resume 会创建新的 Action lifecycle。

当前：

```text id="7j9nzs"
Goal Step:
action_instance = A

Action A interrupted
        ↓
resume
        ↓
Action B created
```

因此 Resume 后必须：

```text id="eh7y7v"
GoalStep.action_instance_id = B
```

不能继续保留 A。

---

## 推荐实现

当：

```python id="u2k6w9"
_resume_interrupted()
```

成功创建新的 ActionInstance 后：

如果这个 Action 对应某个 active GoalStep：

```text id="m9h7dj"
update GoalStep.action_instance_id
```

然后持久化 Goal。

不要创建新的 GoalStep。

---

# 八、完整生命周期测试

新增：

```text id="tn4cz5"
TestGoalStepUsesActionInstanceIdentity
```

至少构造：

```text
Goal A
Step A
action_id = same_action
instance_id = A
```

再构造一个：

```text
Goal B
Step B
action_id = same_action
instance_id = B
```

然后：

```text
ACTION_COMPLETED(instance_id=A)
```

必须：

```text Goal A step → completed
Goal B step → unchanged
```

这是本轮必须加入的核心回归测试。

---

# 九、Interrupt / Resume 测试

新增：

```text id="cw0uqq"
TestInterruptedGoalRebindsResumedActionInstance
```

流程：

```text Goal
→ Action Instance A
→ interrupt
→ external action
→ resume
→ Action Instance B
→ GoalStep.action_instance_id == B
→ B completed
→ only correct Goal advances
```

---

# 十、问题 3：启动恢复必须能够重新发现已耗尽资源

当前 `GoalDetector.sweep()` 通过：

```python id="a5t3e9"
inventory.items
```

寻找：

```text count(item) <= 0
```

这是不完整的，因为一个物品耗尽以后可能已经从 inventory mapping 中消失。

---

# 十一、正确的恢复方法

不要硬编码：

```text
cola
cat_food
pudding
```

继续使用 World Seed / ActionDefinition。

对于每个 inventory：

应该找出：

```text id="a5y8k3"
runtime.restock_actions(inventory_key, item)
```

能够补回的 item 集合。

然后：

```text id="q5n2j1"
for each seed-known restockable item:
    current_quantity = inventory.count(item)

    if current_quantity <= 0:
        ensure restock Goal exists
```

也就是说：

> **“哪些东西值得补货”由当前世界中真实存在的补货 Action 决定。**

而不是由 `inventory.items` 当前是否有 key 决定。

---

# 十二、恢复扫描必须保持去重

如果：

```text id="q3j1v2"
Goal 已存在
```

则：

```text 不创建新的 Goal
```

继续使用：

```text id="ys7u0h"
character_id + kind + target
```

dedupe。

---

# 十三、补充恢复测试

新增：

```text id="f4m8qy"
TestStartupSweepRecoversUnpersistedDepletedResource
```

模拟：

```text inventory["cola"] = 0
inventory.items 中已经不存在 cola
sandbox_goals 中也不存在 restock goal
```

然后：

```text Runtime restart
→ GoalDetector.sweep()
```

必须产生：

```text restock_resource(goal.target_item == cola)
```

而不能依赖：

```text inventory.items["cola"] == 0
```

---

# 十四、不要修改 Goal Architecture

本轮不要：

- 新 Goal System
- 新 Planner
- 新 Agent
- 新 EventBus
- 新 Mutation System
- 新 ActionSystem
- 新 Memory System
- 新 World System

继续使用当前：

```text
GoalManager
GoalDetector
DecisionCoordinator
ActionSystem
Mutation
SandboxEvent
```

---

# 十五、不要修改 Memory 方向

Goal：

```text GOAL_COMPLETED
    ↓
Experience
    ↓
Memory Candidate
```

继续保留。

不要做：

```text Memory → Goal
```

不要把：

```text GOAL_BLOCKED
GOAL_CREATED
```

自动变成长期 Memory。

---

# 十六、Goal priority 暂时保持现状

当前：

```text
pet care > resource shortage > project
```

本轮不要重新设计 priority 系统。

只确保：

```text Goal state correctness
```

和：

```text Action instance correctness
```

---

# 十七、工程审计

完成后对：

```text
app/sandbox/goals.py
app/sandbox/runtime.py
app/sandbox/events.py
app/sandbox/action_templates.py
tests/test_sandbox_goals.py
```

做静态检查。

重点搜索：

```text
action_id
action_instance
GOAL_ACTIVATED
GOAL_BLOCKED
GoalStatus.blocked
```

确保没有：

```text
blocked status + running action
```

这种不一致状态。

---

# 十八、测试要求

必须执行：

```powershell id="jv3qak"
ruff check .
ruff format --check .
mypy app
pytest -q
```

要求：

```text id="nvd8bs"
原 1164 tests 全部保留
+
新增 remediation tests
=
全部通过
```

CI success。

---

# 十九、Git 双目录

开发：

`E:\WorkSpace ZCode\CatooBot`

发布：

`E:\WorkSpace ZCode\CatooBot_github`

发布前继续检查：

- API keys
- API tokens
- `.env`
- 真实 Character Bible
- local DB
- logs
- private Zcode workflow
- local paths
- secrets

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

# 二十、最终报告

必须报告：

```text
Phase 7 Remediation commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

1. Blocked → Active:
<结果>

2. Action Instance identity:
<结果>

3. Resume instance rebinding:
<结果>

4. Startup depleted resource recovery:
<结果>

5. Multiple goals sharing action definition:
<结果>

6. Goal isolation:
<结果>

是否增加新的 Goal System:
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

必须满足：

```text
✅ blocked goal cooldown 后重新执行时 status = active
✅ GOAL_ACTIVATED 正确产生
✅ GoalStep 有明确 action_instance_id
✅ ACTION_COMPLETED 可以精确匹配 action instance
✅ 同 action_id 的其他 Goal 不会被误完成
✅ interrupt/resume 后重新绑定新的 action instance
✅ restart 能重新发现未持久化的 depleted resource
✅ 不依赖 inventory.items 中残留 0-count key
✅ 不引入角色/物品硬编码
✅ 原 1164 tests 全保留
✅ 新增测试全部通过
✅ CI success
```

本轮完成后停止。

**不要进入 Phase 8。**