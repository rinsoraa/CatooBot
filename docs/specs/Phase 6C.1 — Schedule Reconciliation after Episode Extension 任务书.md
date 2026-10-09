# CatooBot Phase 6C.1
# Schedule Reconciliation after Episode Extension

## 0. 阶段性质

这是 Phase 6C 的 **consistency cleanup**，不是新能力阶段。

目标只有一个：

> 当当前 Activity Episode 被 6B `EXTEND` 后，现有 Future ActivityPlan 必须及时与新的现实结束时间对齐，避免 `CONTINUATION` 或后续计划项仍携带已经过时的时间边界。

本阶段不得引入：

- LLM
- Model Advisor
- 新 Activity Decision 规则
- 新 Minecraft tool
- 新 ActionRuntime action
- 新 TaskRuntime state
- Autonomous Minecraft action
- 新 Goal 系统
- 新 Schedule 系统

---

# 一、问题定义

当前已经存在：

```text
Current Episode
    ↓
6B EXTEND
    ↓
planned_end_at 延后
```

但：

```text
ActivityPlan
    ↓
CONTINUATION
    ↓
仍保存旧 planned_end
```

会造成：

```text
Reality:
gaming → 16:20

Plan:
gaming continuation → 16:00
rest → 16:00
music → 16:20
```

这属于 stale future schedule。

必须变成：

```text
Reality:
gaming → 16:20

Plan:
gaming continuation → 16:20
rest → 16:20...
```

或者重新规划得到新的合理时间线。

---

# 二、正确架构

允许：

```text
Episode EXTENDED
        ↓
Schedule Reconciliation
        ↓
现有 Plan 是否仍然有效？
        ├── YES → 仅更新 continuation 边界
        └── NO  → 触发一次受控 replan
```

不允许：

```text
Episode EXTENDED
→ 直接创建新 Episode
```

也不允许：

```text
Episode EXTENDED
→ ActivityDecisionEngine 再决策一次
```

6B 已经完成这个 Decision。

6C.1 只解决：

```text
CURRENT REALITY
        ↕
FUTURE PLAN
```

的一致性。

---

# 三、首选策略

优先采用：

## Strategy A — Continuation Reconciliation

如果当前 Plan 的第一项是：

```text
CONTINUATION
```

且活动名与当前 Episode 完全一致：

```text
plan.items[0].activity == current_episode.activity_name
```

那么：

```text
continuation.start = current_episode.started_at
continuation.end = current_episode.planned_end_at
```

只更新这个 continuation 的时间边界。

不得重新计算所有 candidate。

不得改变后续活动的 identity，除非时间发生冲突。

---

# 四、后续计划项冲突

例如：

```text
Current:
gaming → 16:20

Plan:
gaming continuation 14:00–16:00
rest 16:00–16:20
music 16:20–17:00
```

EXTEND 后：

```text
gaming → 16:20
```

则：

```text
rest = 16:00–16:20
```

已经无法存在。

此时必须：

```text
invalidate current plan
→ trigger controlled replan
```

不能简单把：

```text
rest.start = 16:20
```

然后继续硬推全部项目，除非现有 Planner 明确支持这种安全 shift。

优先：

```text
REPLAN
trigger=EPISODE_EXTENDED
```

---

# 五、EPISODE_EXTENDED 作为软触发

新增：

```text
EPISODE_EXTENDED
```

到 6C Plan Refresh Trigger。

它必须是：

```text
SOFT
```

不是 HARD。

含义：

```text
Episode EXTEND
→ 标记 plan stale / dirty
→ 在当前 refresh cooldown 允许时重新规划
```

不要：

```text
Episode EXTEND
→ 立即同步调用 Planner
```

以免一个活动连续多次 EXTEND 导致 Planner 抖动。

---

# 六、Cooldown

复用现有：

```text
planner_refresh_min_minutes
```

不要增加新 cooldown 配置。

例如：

```text
10:00 plan
10:05 extend
```

如果 cooldown 还没到：

```text
plan_dirty = true
```

而不是立即重算。

当下一次合法 planning trigger 到来：

```text
EPISODE_EXTENDED
+
cooldown satisfied
```

再执行：

```text
replan
```

---

# 七、Plan Dirty

可以增加内部派生状态：

```python
plan_dirty: bool
```

但不要增加新的持久化状态机。

它可以：

```text
memory/runtime state
```

或者根据：

```text
current_episode.planned_end_at
plan continuation end
```

动态判断。

优先动态判断，避免新的 persistence state。

---

# 八、版本行为

如果只是：

```text
continuation.end
```

发生改变：

这仍然算：

```text
Plan Content Changed
```

因此：

```text
plan_version += 1
```

但必须继续使用已有的：

```text
content_hash
```

判断机制。

不能因为每次读取时刻不同就产生新版本。

---

# 九、计划历史

旧 Plan：

```text
SUPERSEDED
```

新 Plan：

```text
ACTIVE_PLAN
```

旧 Plan 不得删除。

必须保留：

```text
superseded_by
```

---

# 十、Trigger Audit

新 Plan 必须记录：

```text
trigger = EPISODE_EXTENDED
```

例如：

```text
PLAN-... v8
trigger=EPISODE_EXTENDED
```

使未来可以回答：

```text
为什么计划突然变了？
```

---

# 十一、不要触发 Activity Decision

特别重要：

```text
EPISODE_EXTENDED
```

是：

```text
6B 已经完成的 Decision
```

6C.1 只做：

```text
Plan reconciliation
```

不能再次产生：

```text
CONTINUE
EXTEND
TRANSITION
```

Activity Decision。

否则可能形成：

```text
EXTEND
→ replan
→ decision
→ EXTEND
→ replan
→ ...
```

潜在活锁。

---

# 十二、不得影响当前 Episode

Reconciliation 期间：

```text
current_episode
```

完全不变。

不能：

```text
修改 started_at
修改 current status
创建 duplicate Episode
```

唯一允许的是：

```text
future plan
```

变化。

---

# 十三、不得提前执行下一活动

例如：

```text
gaming EXTEND
```

不得：

```text
gaming EXTEND
→ immediately start rest
```

也不得：

```text
plan says rest 16:20
→ runtime immediately create rest Episode
```

只有真正：

```text
current Episode ends
```

才由已有 6B / 6C 流程进入下一活动。

---

# 十四、Task Integration

如果：

```text
minecraft_task
```

被延长：

6C.1 也必须正确处理。

但：

```text
TaskRuntime
```

仍然是现实执行事实来源。

不得：

```text
Task extension
→ ActivityPlanner creates Minecraft action
```

---

# 十五、Minecraft

不增加任何 Minecraft 能力。

保持：

```text
Minecraft Observation
→ context only
```

本阶段没有：

```text
minecraft_* mutation
```

---

# 十六、QQ

QQ 查询：

```text
你接下来准备干嘛？
```

应该看到：

```text
最新 Plan
```

而：

```text
你现在在干嘛？
```

仍然读取：

```text
ActivityEpisode
```

两者必须继续隔离。

---

# 十七、Extension 示例

测试：

```text
10:00
gaming
10:00–10:30
```

Plan：

```text
gaming CONTINUATION 10:00–10:30
rest 10:30–10:45
music 10:45–11:30
```

10:20：

```text
6B EXTEND +20
```

当前现实：

```text
gaming → 10:50
```

6C.1：

```text
旧 continuation = 10:30
```

发现：

```text
stale
```

执行：

```text
EPISODE_EXTENDED
→ replan
```

最终：

```text
gaming CONTINUATION 10:00–10:50
rest 10:50–11:05
music 11:05–11:50
```

---

# 十八、连续 EXTEND

必须测试：

```text
10:20 extend +10
10:35 extend +10
10:50 extend +10
```

不得：

```text
Planner × 3
```

如果 cooldown 尚未满足：

```text
plan_dirty=true
```

只在下一次合法 planning refresh 中重算。

---

# 十九、EXTEND 到 max_duration

如果 6B 已经：

```text
max_duration
```

那么：

```text
不会产生 EXTEND
```

因此：

```text
没有 EPISODE_EXTENDED
```

6C.1 不应该自己处理 max guard。

6B 继续拥有这个职责。

---

# 二十、Recovery

如果 CatooBot：

```text
EXTEND
```

之后马上重启：

Recovery 必须检查：

```text
current Episode.planned_end_at
plan continuation end
```

如果不一致：

```text
reconcile / replan
```

不能继续使用旧计划。

---

# 二十一、Recovery Priority

保持：

```text
Episode > WorldState > Character snapshot > Future Plan
```

Future Plan 永远不能覆盖现实。

---

# 二十二、Plan Persistence

不能添加第二套：

```text
activity_reconciliation
```

存储。

继续使用：

```text
activity_plans
activity_plan_items
```

---

# 二十三、No New Tables

本阶段：

```text
migration count = 30
```

保持不变。

不新增 migration。

---

# 二十四、测试

至少新增：

```text
tests/test_activity_plan_reconciliation.py
```

覆盖：

### A
EXTEND 更新 continuation

### B
EXTEND 导致后续计划冲突

### C
冲突触发 replan

### D
trigger=EPISODE_EXTENDED

### E
cooldown

### F
连续 EXTEND

### G
版本只在内容变化时增加

### H
旧计划保留

### I
recovery 后 stale plan 修复

### J
current Episode 不被修改

### K
没有新 Episode

### L
没有 DecisionEngine 二次调用

### M
Minecraft world action = 0

### N
QQ current vs future 仍然正确

### O
6A/6B/6C regression

---

# 二十五、真机门禁

本阶段不需要重新跑完整 6C 大型 smoke。

只需要一个窄门禁：

## Real A

创建：

```text
gaming
```

有 Future Plan。

然后触发一次真实：

```text
EXTEND
```

验证：

```text
Current Episode end = new end
Future Plan = reconciled
```

---

## Real B

连续 EXTEND：

```text
EXTEND
EXTEND
```

验证：

```text
没有每次都立即 Planner refresh
```

并最终得到：

```text
1 次受控 replan
```

---

## Real C

重启后：

```text
current Episode
+
Future Plan
```

仍然一致。

---

## Real D

QQ：

```text
你现在在干嘛？
```

回答：

```text
current
```

QQ：

```text
你接下来准备干嘛？
```

回答：

```text
reconciled future plan
```

---

# 二十六、安全门禁

源码级：

```text
app/activity/
```

继续禁止：

```text
Minecraft mutation
Task execution
Confirmation
Policy bypass
```

---

# 二十七、不允许修改

不要修改：

```text
6B DecisionEngine
6B state machine
TaskRuntime
ActionRuntime
Minecraft tools
Minecraft runtime
allow_medium
```

除非发现真实兼容性 bug。

---

# 二十八、最终 CI

必须：

```text
ruff
format
mypy
pytest
Node
Vitest
vue-tsc
build
```

全部通过。

至少：

```text
6A regression
6B regression
6C regression
```

全部 PASS。

---

# 二十九、最终判定

```text
PHASE 6C.1 = PASS
```

要求：

```text
Plan freshness                PASS
EXTEND reconciliation         PASS
Soft trigger                  PASS
Cooldown                      PASS
Continuous extension          PASS
Recovery                      PASS
Current/Future isolation      PASS
No new Episode                PASS
No Decision recursion         PASS
No Minecraft action           PASS
Real Java                     PASS
Real QQ                       PASS
```

如果 Real A–D 任意一项缺失：

```text
PHASE 6C.1 = BLOCKED
```

---

# 三十、完成后

6C.1 完成后：

```text
6A  PASS
6B  PASS
6C  PASS
6C.1 PASS
```

然后才进入：

# Phase 6D — Model-Assisted Activity Decision

6D 的模型只能成为：

```text
Advisor
```

不是：

```text
Authority
```

即：

```text
Rule-based Guards
        ↓
Model Advisor
        ↓
Structured Proposal
        ↓
Rule Validation
        ↓
ActivityDecision
```

绝不：

```text
LLM
→ 直接改变 Episode
```

而且 v1.0 已明确规定 Model 只参与 `continue vs extend`、具体 next activity preference 和当前状态解释；模型只应在 transition window 调用，失败必须回落到规则系统。