# CatooBot v2.1 Phase 9.1.1 · Shared Activity Window Matching Repair

基线必须保持：

- baseline：`bc911a1bbb245f309f0459846cdaccfba4b31785`
- 当前 Phase 9.1 的 1248 tests 全部保持
- 不得回滚任何 Phase 9.1 修复
- 不进入 Phase 10
- 不重构架构，只修一个匹配边界条件

## 本轮唯一目标

修复：

`CommitmentManager.match_shared_activity()`

当前实现先把所有：

`person + shared_activity + open + activity match`

的 commitment 放入 candidates，然后在 `len(candidates) > 1` 时直接判定 ambiguous，再检查时间窗口。

这会错误处理如下场景：

```text
C1：今晚 20:00 一起 gaming
C2：明晚 20:00 一起 gaming

当前时间：今晚 20:30

发生了一次无 commitment_id 的 shared_activity(gaming)
```

正确结果必须是：

```text
C1 → completed
C2 → open
```

不能因为存在未来的 C2 就把当前有效的 C1 判定为 ambiguous。

## 正确匹配规则

无 `commitment_id` 时，必须先构造“当前履约窗口内”的候选集合：

```text
person_id match
+ kind == shared_activity
+ commitment.open
+ target_activity match
+ earliest_at <= now（若 earliest_at 存在）
+ now <= due_at + DEFAULT_GRACE_MINUTES（若 due_at 存在）
```

然后：

```text
qualified_candidates == 0
    → 不履约

qualified_candidates == 1
    → 精确履约这一条

qualified_candidates > 1
    → ambiguous
    → 不履约
    → WARNING
```

注意：

- 未来 commitment 不应参与当前时刻的 ambiguity。
- 已经超过 `due_at + grace` 的 commitment 也不应参与 ambiguity。
- 不要改变 exact `commitment_id` 路径。
- exact `commitment_id` 路径继续优先于 fallback matching。
- 不要重新引入按 person/activity 猜 commitment 的旧逻辑。

## 精确 ActionInstance 规则保持不变

`_commitment_step_shared()` 当前的：

```text
goal.status == active
step.status in {active, completed}
step.action_instance_id != ""
step.action_instance_id == action.id
```

保持不动。

不要因为本轮修复而修改它。

这里允许 `completed` 是因为实际 runtime 顺序为：

```text
ACTION_COMPLETED
→ GoalManager.on_action_completed()
→ GoalStep completed
→ _commitment_step_shared()
```

实例 ID 仍必须严格相等，空实例绝不能作为 wildcard。

## 新增唯一必要回归测试

在：

`tests/test_sandbox_commitments_integrity.py`

新增测试：

### 当前窗口 commitment + 未来同 activity commitment

构造：

```text
C1 = 今晚一起打游戏
C2 = 明晚一起打游戏
```

将 clock 推进到 C1 的履约窗口内，但保持 C2 尚未进入窗口。

产生无 `commitment_id` 的：

```text
shared_activity
target_activity = gaming
duration >= MIN_FULFILL_DURATION_MINUTES
```

断言：

```python
assert C1.status.value == "completed"
assert C2.open
assert runtime.commitment_detector.fulfilled == 1
```

这个测试必须直接覆盖：

“多个 open commitment，但只有一个当前处于真实履约窗口”。

同时保留现有：

- future commitment cannot be fulfilled early
- ambiguous same-activity commitments
- exact commitment_id fulfillment
- exact reschedule
- ambiguous reschedule
- exact ActionInstance
- persistence
- character isolation

全部测试。

## 禁止事项

本轮禁止：

- 修改 Goal priority
- 修改 Relationship rules
- 修改 Memory
- 修改 Experience
- 修改 DecisionCoordinator
- 修改 Planner
- 新增 Agent
- 新增 EventBus
- 新增 Mutation system
- 改写 Character Bible
- 修改 Phase 8 / 8.1
- 修改 Phase 9 commitment 状态机
- 修改 exact commitment_id 路径
- 修改 ActionInstance 语义
- 进入 Phase 10
- rewrite git history
- force push

## 完成条件

必须执行：

```text
ruff
ruff format
mypy
pytest
```

要求：

```text
原 1248 tests 全部保持
新增测试通过
CI success
working tree clean
origin/main 与 HEAD 一致
```

最终报告必须明确：

```text
Phase 9.1.1 commit:
HEAD:
origin/main:
CI:

tests:
new tests:

mixed-window fallback:
current commitment:
future commitment:
ambiguous behavior:

ActionInstance:
persistence:
character isolation:

是否修改 Goal priority:
是否修改 Relationship:
是否修改 Memory:
是否增加 Planner:
是否增加 Agent:
是否进入 Phase 10:
```

本轮目标是把 Phase 9.1 的 shared-activity fallback matching 最后一处窗口边界收紧，然后停止。