# CatooBot v2.1 Phase 9.1
## Commitment Outcome Integrity

### 一、当前基线

当前 Phase 9：

`87b12746e79aea4c7ac98bccaaaef62e44302fa3`

当前：

- 1237 tests passed
- CI success
- HEAD = origin/main = remote main

Phase 9 主体已经完成：

- SocialCommitment
- CommitmentDetector
- CommitmentGoalBridge
- Commitment Persistence
- Commitment Revision
- Commitment → Goal
- Goal → Decision → Action
- Fulfillment / Broken / Reschedule
- Relationship outcome
- Memory outcome
- Continuity / CognitiveContext
- Character isolation

本轮不是 Phase 10。

只修 Commitment 生命周期的正确性。

---

# 二、问题 1：Fulfilled Commitment 必须让对应 Goal 进入 COMPLETED

当前问题：

```text
COMMITMENT_FULFILLED
```

产生后：

```text
Commitment.status = completed
```

但：

```text
GoalKind.fulfill_commitment
```

在：

```text
GoalManager._evaluate()
```

中直接 return。

因此关联 Goal 没有完成路径。

下一轮 `cancel_stale()` 反而可能：

```text
commitment.open == False
→ Goal.cancel()
```

导致：

```text
成功履约
→ Goal cancelled
```

这是错误语义。

---

# 三、建立 Commitment Fulfillment → Goal Completion 桥

优先复用现有：

```text
SandboxEventBus
GoalManager
```

不要新建第二套机制。

推荐：

```text
COMMITMENT_FULFILLED
        ↓
GoalManager.on_commitment_fulfilled(commitment_id)
        ↓
找到唯一 target_commitment == commitment_id 的 open Goal
        ↓
Goal.status = completed
Goal.progress = 1
current_step = completed
        ↓
GOAL_COMPLETED
```

必须：

- 精确按照 `commitment_id`
- 不按 person/activity 猜
- 不创建新 Goal
- 不创建新 GoalStep
- 不重新执行 Action

---

# 四、必须保持 Goal 与 Commitment 双向引用边界

Commitment：

```text
commitment_id
```

Goal：

```text
target_commitment = commitment_id
metadata["commitment_id"] = commitment_id
```

因此：

```text
COMMITMENT_FULFILLED
→ exact goal
```

而不是：

```text
person_id
+
activity
→ 猜 Goal
```

---

# 五、Fulfillment Goal 完成的状态

成功履约之后：

```text
Commitment.status == completed
Goal.status == completed
Goal.progress == 1.0
Goal.current_step.status == completed
```

必须同时成立。

---

# 六、事件因果

正确链：

```text
ACTION_COMPLETED
    ↓
SOCIAL_INTERACTION(shared_activity)
    ↓
COMMITMENT_FULFILLED
    ↓
GOAL_COMPLETED
```

`GOAL_COMPLETED`：

```text
causation_id = COMMITMENT_FULFILLED.event_id
```

或项目中等价的直接因果链接。

Correlation：

> 继续使用 Goal 自己的 `goal_<id>` correlation。

不要把 Goal 重新并入 Action correlation。

---

# 七、新测试：成功履约一定完成 Goal

新增：

```text
TestFulfilledCommitmentCompletesExactGoal
```

流程：

```text
Commitment C
→ Goal G
→ GoalStep
→ Action Instance
→ shared_activity
→ Commitment C completed
```

断言：

```text
C.status == completed
G.status == completed
G.progress == 1
G.current_step.status == completed
```

并且：

```text
GOAL_COMPLETED
```

存在。

---

# 八、新测试：成功履约绝不能变成 cancelled

新增：

```text
TestFulfilledCommitmentIsNeverCancelledAsStale
```

完成履约之后：

运行：

```text
commitment_bridge.cancel_stale()
```

必须：

```text
G.status == completed
```

不能：

```text
cancelled
```

---

# 九、问题 2：Shared Activity Fulfillment 必须精确匹配 Commitment

当前：

```text
person_id
+
activity
```

过于宽松。

例如：

```text
C1:
今天 20:00 Minecraft

C2:
下周 20:00 Minecraft
```

不能因为下午随便玩一次 Minecraft 就任意完成 C1/C2。

---

# 十、优先 commitment_id

当共享活动由 Commitment Goal 驱动：

`_commitment_step_shared()`

必须在：

```text
SocialInteractionFact.metadata
```

中写：

```text
commitment_id
```

例如：

```json
{
  "commitment_id": "cm_xxx",
  "target_activity": "gaming",
  "duration_minutes": 35
}
```

这样 CommitmentDetector：

```text
_on_shared_activity()
```

优先：

```text
commitment_id
→ exact commitment
```

---

# 十一、无 commitment_id 的共享活动

只有在：

```text
commitment_id == ""
```

时才允许 fallback。

fallback 必须同时满足：

```text
person_id match
+
kind == shared_activity
+
open
+
target_activity match
+
current_time >= earliest_at
+
current_time <= due_at + grace
```

并且：

```text
候选数 == 1
```

才能 fulfill。

如果：

```text
候选数 == 0
```

则：

```text
不 fulfill
```

如果：

```text
候选数 > 1
```

则：

```text
不 fulfill
+
WARNING
```

禁止猜。

---

# 十二、不要使用 latest_at 代替 due/grace

`latest_at` 是安排窗口边界。

履约通常允许一定 grace。

使用：

```text
earliest_at
+
due_at
+
DEFAULT_GRACE_MINUTES
```

定义真实履约窗口。

具体语义保持当前 Phase 9：

> due + 120 分钟仍可视为允许完成；超过才 broken。

不要改变已有 grace 语义。

---

# 十三、新测试：未来承诺不会被提前完成

新增：

```text
TestFutureCommitmentNotFulfilledEarly
```

例如：

```text
Commitment:
今天 20:00 一起 Minecraft

现在：
15:00

shared_activity:
15:00
duration = 30
```

必须：

```text
Commitment.status != completed
```

---

# 十四、新测试：多个相同活动承诺时不猜

构造：

```text
C1:
今天 20:00 Minecraft

C2:
今天 22:00 Minecraft
```

同一 person。

发生：

```text
shared_activity
```

但：

```text commitment_id отсутствует
```

必须：

```text 两个都保持 open
```

并记录 warning。

---

# 十五、问题 3：Reschedule 必须精确定位 Commitment

当前：

```text
_on_reschedule()
→ person_id
→ 第一个 open commitment
```

这是错误的。

---

# 十六、Reschedule 优先 commitment_id

SocialInteractionFact metadata 应支持：

```text
commitment_id
```

当存在：

```text
commitment_id
→ exact commitment
```

只有：

```text commitment_id == ""
```

才允许 fallback。

---

# 十七、Reschedule fallback

必须：

```text
person_id
+
open
+
可能的 activity/description target
```

并要求：

```text
candidate count == 1
```

否则：

```text
不修改任何 Commitment
+
WARNING
```

---

# 十八、测试 Reschedule Isolation

新增：

```text
TestRescheduleTargetsExactCommitment
```

两个同一 person：

```text
C1 Minecraft
C2 Movie
```

reschedule event：

```text commitment_id = C2
```

只能：

```text
C2.revision += 1
C2.due_at changed
C1 unchanged
```

---

# 十九、多承诺无 ID 时必须拒绝猜测

新增：

```text
TestAmbiguousRescheduleDoesNothing
```

两个 open commitments。

没有 commitment_id。

结果：

```text
C1 unchanged
C2 unchanged
```

并有 WARNING。

---

# 二十、问题 4：Action Instance 必须保持 Phase 7.1 严格纪律

当前：

```text
_commitment_step_shared()
```

仍允许：

```text
step.action_instance_id == ""
```

作为 wildcard。

这是不允许的。

---

# 二十一、严格规则

`_commitment_step_shared()` 只能返回：

```text
goal.status == active
step.status == active
step.action_instance_id == action.id
```

三个条件必须同时满足。

不能：

```text
empty instance → accept
```

---

# 二十二、测试

新增：

```text
TestCommitmentSharedActivityRequiresExactActionInstance
```

构造：

```text
GoalStep:
action_id = play_game
action_instance_id = ""
status = active
```

Action completed:

```text
instance = A
```

必须：

```text
不会生成 commitment shared_activity fact
```

---

# 二十三、不要破坏现有 Invite Acceptance

当前：

```text
accepted_invitation
```

路径可以没有 GoalStep。

这种情况仍然允许：

```text
accepted invitation
→ shared activity
```

在真正活动完成后产生共享活动事实。

但：

> 如果它对应一个已有 Commitment，则优先使用 commitment_id 精确履约。

---

# 二十四、Commitment outcome → Relationship 保持

仍然：

```text
COMMITMENT_FULFILLED
↓
SocialInteractionFact
↓
RelationshipUpdateEngine
```

不要：

```text
CommitmentManager.fulfill()
→ trust += ...
```

禁止改 Phase 8 设计。

---

# 二十五、Commitment outcome → Memory 保持

仍然：

```text
COMMITMENT_FULFILLED
↓
Experience
↓
MemoryCandidate
```

不要：

```text
GOAL_COMPLETED
→ duplicate Memory
```

同一履约不能形成两条重复 Experience / Memory。

---

# 二十六、Correlation

保持：

```text
Commitment correlation
Goal correlation
Social fact correlation
```

但：

```text
Goal COMPLETED
```

属于 Goal 自己的 correlation。

不要把它和 Social Fact 合并成一个 behaviour chain。

---

# 二十七、Persistence

成功履约后：

必须最终持久化：

```text
Commitment = completed
Goal = completed
GoalStep = completed
```

不要出现：

```text
Commitment completed
Goal active
```

这种跨重启不一致状态。

新增：

```text
TestFulfillmentPersistence
```

直接重新构造 Runtime：

```text
Commitment.status == completed
Goal.status == completed
GoalStep.status == completed
```

---

# 二十八、异步顺序

当前：

```text
CommitmentManager._emit_outcome_fact()
```

使用：

```text
asyncio.create_task()
```

本轮需要保证：

```text
COMMITMENT_FULFILLED
→ Goal completion
```

发生顺序稳定。

优先方案：

让：

```text
CommitmentManager.fulfill()
```

先更新 Commitment，再发布 event。

由同步 EventBus handler：

```text
GoalManager.on_commitment_fulfilled()
```

完成 Goal 状态。

如果当前 EventBus 是同步 handler，这应该是最简单方案。

不要为了这个引入新的 async event bus。

---

# 二十九、不能因为 Goal Completion 调用 Action

Goal completion 只是：

```text
状态收口
```

不能：

```text
GOAL_COMPLETED
→ Action
```

---

# 三十、不要进入 Phase 10

本轮禁止：

- Relationship 新功能
- Social Planner
- Persona adaptation
- Memory → Commitment
- Commitment → Relationship direct write
- 新 Goal System
- 新 Decision System
- 新 EventBus
- 新 Mutation System
- 新 Memory System
- LLM commitment detection
- Embedding
- Vector Search
- Character Bible rewrite
- Git history rewrite

---

# 三十一、测试门禁

执行：

```powershell
ruff check .
ruff format --check .
mypy app
pytest -q
```

要求：

```text
原 1237 tests 全部保留
+
新增 remediation tests
=
全部通过
```

CI 必须 success。

---

# 三十二、最终报告

必须报告：

```text
Phase 9.1 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

1. Fulfillment → Goal completed:
结果

2. Fulfillment never becomes stale cancellation:
结果

3. Future commitment cannot be fulfilled early:
结果

4. Exact commitment_id fulfillment:
结果

5. Ambiguous shared activity:
结果

6. Exact reschedule targeting:
结果

7. Ambiguous reschedule:
结果

8. Exact ActionInstance requirement:
结果

9. Fulfillment persistence:
结果

10. Character isolation:
结果

是否修改 Goal priority:
No

是否修改 Relationship rules:
No

是否 Memory → Commitment:
No

是否增加 Planner:
No

是否增加 Agent:
No

是否进入 Phase 10:
No
```

本轮完成后停止。

# 三十三、最终验收

必须：

```text
✅ Commitment fulfilled → exact Goal completed
✅ Goal 不因 fulfilled commitment 被 stale cancel
✅ GoalStep completed
✅ commitment_id 优先精确匹配
✅ 多个相同活动承诺不会猜
✅ 未来承诺不会提前履行
✅ reschedule 精确定位
✅ ambiguous reschedule 不修改任何状态
✅ shared activity 严格使用 ActionInstance
✅ restart 后 Commitment / Goal / Step 三者一致
✅ 原 1237 tests 全保留
✅ CI success
```

**本轮完成后再进入 Phase 10。**