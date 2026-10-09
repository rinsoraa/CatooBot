# CatooBot Phase 7B
# Virtual Autonomous Activity / Initiative-to-Plan Integration

## 0. 阶段目标

Phase 7A 已完成：

- InitiativeEngine / InitiativeGate
- LifeIntent 生成、抑制、去重、冷却与过期
- 持久化与恢复
- Goal / Memory / Social / Activity context
- QQ 意图查询
- Minecraft 在线与离线隔离
- 无自主 Task 创建、无世界动作
- Real Java A–D 与 Real QQ A/B 验证

Phase 7B 的唯一核心目标：

> 将 7A 生成的合格 LifeIntent 真正接入既有 ActivityPlanner，使角色的主动意图能够影响未来活动计划，并在符合时间、状态和决策约束时转化为 Virtual Activity Episode。

当前已知缺口：

```text
InitiativeService
    ↓
suggested_activities()
    ↓
activity_name → bonus
    X
ActivityPlanner 尚未真正消费这一建议
```

本阶段必须闭合这条连接。

## 一、最高优先级原则

必须保持：

```text
LifeIntent = 动机 / 意图
ActivityPlan = 未来安排
ActivityEpisode = 当前活动事实
TaskRuntime = 真实任务执行
```

允许：

```text
LifeIntent
    ↓
ActivityPlanner candidate ranking
    ↓
ActivityPlan
    ↓
6B DecisionEngine
    ↓
ActivityRuntime
    ↓
Virtual ActivityEpisode
```

绝对禁止：

```text
LifeIntent → TaskRuntime.create_task()
LifeIntent → TaskRuntime.confirm_and_start()
LifeIntent → Minecraft tool
LifeIntent → ActionRuntime
LifeIntent → QQ proactive message
```

本阶段不实现自主 Minecraft 执行。

## 二、先检查并复用现有架构

开始前检查：

```text
app/initiative/
app/activity/
app/world/
app/character/
app/behavior/
app/tasks/
app/memory/
app/web/
docs/specs/v1.0.md
docs/MINECRAFT_PHASE7A.md
docs/MINECRAFT_PHASE6B.md
docs/MINECRAFT_PHASE6C.md
docs/MINECRAFT_PHASE6C1.md
docs/MINECRAFT_PHASE6D.md
```

文档文件名以仓库实际名称为准。

重点检查：

- `InitiativeService.suggested_activities()`
- `InitiativeGate`
- `LifeIntentStore`
- `ActivityPlanner.plan_next()`
- Activity candidate eligibility 与 ranking
- ActivityPlan 持久化、版本、supersede
- 6B BounceGuard 与 hard constraints
- 6C.1 Plan reconciliation
- 6D Model Advisor
- 现有 Task → Activity Adapter

不得新建：

```text
LifeActivityPlanner
AutonomousActivityRuntime
MinecraftLifeRuntime
InitiativePlannerV2
```

等平行系统。

## 三、Initiative → Planner Adapter

优先升级 7A 已有的 Activity Bridge。

将结构化建议传给现有 Planner，而不是让 InitiativeEngine 自己修改计划。

建议使用类似：

```python
InitiativeActivityHint(
    activity_name: str,
    score_bonus: float,
    intent_id: str,
    source: str,
)
```

实际结构应与现有代码风格兼容。

要求：

1. 只读取有效、未过期、未取消且允许参与规划的 LifeIntent。
2. 已 `SUPPRESSED`、`EXPIRED`、`CANCELLED` 或 `RESOLVED` 的意图不能直接作为活动提议。
3. 只读取有界数量的候选意图。
4. 相同意图不能因为多个重复 check 被重复加分。
5. Adapter 只能返回数据，不能修改 Episode、Plan、Task 或 Minecraft 世界。
6. Adapter 故障时，Planner 应退回原有确定性行为，并记录降级状态。

优先复用 7A 的 fingerprint、cooldown、burst protection，不建立第二套计时器。

## 四、候选评分集成

把 Initiative 建议接入现有 ActivityPlanner 的候选评分流程。

候选评分可以考虑：

```text
base score
+ routine preference
+ anchor fit
+ goal relevance
+ initiative bonus
- recent repetition penalty
```

具体公式以现有 Planner 为准，不得为了接入 Initiative 重写整个评分系统。

### 硬性要求

Initiative bonus 只能改变合格候选之间的软排序。

不得：

- 把 `eligible=False` 的候选提升为 eligible。
- 越过 sleep / meal 等硬约束。
- 越过 energy / focus hard guard。
- 越过 ScheduleAnchor。
- 越过 6B 的 min/max duration、BounceGuard。
- 越过用户任务优先级。
- 越过 TaskRuntime 的授权与确认要求。

必须满足：

```text
Hard Constraints
    >
Candidate Eligibility
    >
6B Transition Guards
    >
Initiative Soft Preference
```

若现有架构的执行顺序不同，应通过适配保证 Initiative 只有软偏好效力。

## 五、无 Initiative 时的行为一致性

这是重要回归门禁。

当：

```text
没有有效 LifeIntent
```

或：

```text
initiative.enabled = false
```

时，Planner 应尽可能保持 7A/6C 之前的原有结果。

不得因为新增 Adapter：

- 改变候选排序。
- 无意义地增加 Plan 版本。
- 频繁刷新 Horizon。
- 创建额外 Episode。
- 影响普通 Tick 复杂度。

如果新增字段只是审计用途，不能让它们参与内容哈希，导致相同计划不断升级版本。

## 六、活动名称与执行边界

只允许使用现有 Activity Registry 中的名称。

虚拟 Activity 如：

```text
music
reading
gaming
free_time
relax
minecraft
```

是否有效必须以实际 Registry 为准，不得直接假定全部存在。

必须严格区分：

```text
minecraft
    = 虚拟兴趣 / 角色活动

minecraft_task
    = 真实 Task 驱动的活动
```

`minecraft_task` 只能由现有 Task → Activity Adapter 根据真实 Task 事件创建或更新。

Initiative 不得提出或创建 `minecraft_task`，也不得将 `minecraft` 虚拟活动解释为已经连接服务器或正在执行真实 Minecraft 动作。

Minecraft 离线时，允许存在虚拟 `minecraft` 兴趣；但不得声称正在真实服务器中活动。

## 七、活动落实时机

Planner 产生新计划不代表活动立即开始。

必须保留：

```text
LifeIntent
→ ActivityPlan
→ 6B DecisionEngine
→ ActivityRuntime
→ ActivityEpisode
```

不得：

```text
LifeIntent
→ ActivityRuntime.force_transition()
```

只有现有生命周期规则允许转移时，才可以真正启动下一个 Episode。

不得提前结束当前 Episode，也不得因重复 Initiative 检查产生重复 Episode。

## 八、Intent 生命周期与计划归属

必须明确区分：

```text
Intent 被纳入 Future Plan
```

与：

```text
Intent 对应的活动已经开始
```

二者不能混为一谈。

同一个意图不应在每次重新规划时无限重复加分。

请先检查现有持久化结构，优先复用：

- `life_intents`
- `activity_plans`
- `activity_plan_items`
- 现有事件审计

尽量避免新增表。

若现有 schema 无法可靠追踪“哪一条 Intent 影响了哪一条计划”，允许做最小的向后兼容迁移，但必须证明其必要性。

要求：

1. 能追踪 Intent 对 Plan / plan item 的影响。
2. Plan 被 supersede 时，不得把旧计划继续当成当前计划。
3. 不能因为未来计划中出现某活动，就声称该活动已经开始。
4. 按照 7A 的状态语义处理 `RESOLVED`；不得重新解释成“执行成功”。
5. 过期、取消、被抑制的意图不得被错误恢复为有效执行意图。
6. 进程崩溃或重启不得复制意图、计划或 Episode。

最终语义写入 `docs/MINECRAFT_PHASE7B.md`。

## 九、用户任务与待确认任务优先

复用 7A 已有 Gate。

当存在：

```text
RUNNING
WAITING_ACTION
PENDING_CONFIRMATION
PAUSED
```

等任务状态时，必须按当前已有语义抑制或限制 Initiative。

尤其在：

```text
PENDING_CONFIRMATION
```

期间：

- 不得自动确认。
- 不得产生与任务争抢的自主活动执行方向。
- 不得修改已有 confirmation。
- 不得改变 `allow_medium`。
- 不得取消或暂停用户任务。

不要复制 TaskRuntime 的状态机。

## 十、6B / 6C / 6C.1 / 6D 集成

必须保留现有职责：

### 6B

继续负责：

```text
CONTINUE / EXTEND / TRANSITION
min/max duration
BounceGuard
transition window
```

### 6C

继续负责：

```text
Routine
Anchors
Goals
Candidate eligibility / ranking
Rolling Horizon
ActivityPlan
```

### 6C.1

继续负责：

```text
Episode EXTEND → Schedule Reconciliation
```

### 6D

继续负责：

```text
可选 Model Advisor
Rule > Model
```

7B 不得新增另一套 Model Advisor，也不得因为 Initiative 自动触发 6D 模型调用。

即使 `model_advisor.enabled=true`，Initiative 本身也不能成为额外的模型调用入口。

## 十一、不要每 Tick 重算

复用现有 ActivityPlanner 刷新触发器及 cooldown。

不得：

```text
每 30 秒
→ 全量读取 LifeIntent
→ 重算整个 Horizon
→ 生成新 Plan
```

只在现有合法规划触发点读取必要的 Initiative hints。

如果 6C.1 的 `plan_dirty`、planner refresh cooldown 或 Plan content hash 能满足要求，优先复用。

普通 Tick 仍应保持低成本。

## 十二、只读可观测性

在现有 WebUI Activity / Initiative 视图中复用或增加最小只读信息：

```text
Intent ID
Intent type / source
Suggested Activity
Score bonus
Candidate eligibility
Selected / Rejected
Rejection reason
Related Plan / Version
Related Episode（仅当真实开始）
```

不允许加入：

```text
Execute
Force Activity
Force Plan
Run Minecraft
Confirm Intent
```

不要显示隐藏思维链、完整 prompt、密钥或原始 provider 响应。

## 十三、QQ 语义

复用现有 QQ 上下文，严格区分：

```text
你最近想干嘛？
    → LifeIntent / 当前想法

你接下来准备干嘛？
    → 当前 Future Plan

你现在在干嘛？
    → 当前 ActivityEpisode
```

不能混淆三者。

7B 不得通过 Initiative 发送主动 QQ 消息。

普通 QQ 对话、Task Entry 和原有人格回复不能被吞消息或误判成任务。

## 十四、测试矩阵

新增或更新测试，至少覆盖：

### Planner Adapter

- 有效 PROPOSED intent 能被转换成 bounded hint。
- 已过期意图不能作为 hint。
- SUPPRESSED / CANCELLED / RESOLVED 意图不能直接参与。
- 重复 intent 不重复累计加成。
- Adapter failure 回到 baseline planner。
- 相同输入得到相同结果。

### Candidate Ranking

- hint 能影响允许候选的软排序。
- 无 hint 时保持原有 Planner 行为。
- 低优先级 hint 不得压过硬 Anchor。
- energy / focus 不允许的候选仍然被拒绝。
- 不允许通过 hint 创建新 Activity 名称。
- `minecraft` 只能作为已注册的虚拟活动。
- `minecraft_task` 不能成为 Initiative 候选。

### 生命周期

- Future Plan 不会提前改变当前 Episode。
- 合法转移时，虚拟活动能成为真实 ActivityEpisode。
- 同一意图不会创建重复 Episode。
- Plan supersede / recovery 不会重复消费意图。
- Intent expiry 与 plan refresh 正确协作。
- 连续规划不会产生 Episode 或 Plan 爆炸。

### 安全边界

- 活跃用户任务优先。
- PENDING_CONFIRMATION 不被突破。
- 没有 Task 创建。
- 没有确认绕过。
- 没有 Minecraft 世界修改。
- 没有 Initiative → QQ proactive message。
- 没有 Initiative LLM 调用。
- 不改变 6D Advisor 默认关闭状态。
- `allow_medium=false` 不变。

### 长时间仿真

在 FakeClock 下运行至少 24 小时，验证：

- 意图数量有界。
- Plan 版本不会每个 Tick 增加。
- Episode 数量合理。
- Intent 不无限重复加分。
- BounceGuard 无回归。
- 没有持续空转规划。
- 没有缺失或重复的当前 Episode。

禁止通过真实 `sleep()` 制造时间。

## 十五、真实 Java 门禁

所有门禁必须使用真实 Minecraft / CatooBot 环境，不以 mock 代替真实证据。

### Real Java A：在线兴趣

真实 Minecraft 在线时：

```text
MINECRAFT_INTEREST
→ Virtual Minecraft candidate
→ ActivityPlan
```

证明：

```text
world_actions = 0
new Task count = 0
```

如果当时没有合格的兴趣意图，可以通过现有合法输入产生意图，但不得直接修改 Planner 输出或伪造现实状态。

### Real Java B：离线兴趣

真实 Minecraft 离线时：

允许虚拟兴趣影响活动计划。

必须：

- 不创建真实 Minecraft Task。
- 不产生 Minecraft 世界动作。
- 不声称 bot 正在真实服务器内活动。

### Real Java C：用户任务优先

真实任务处于 `RUNNING` 或 `PENDING_CONFIRMATION` 时，新的 Initiative 必须被正确限制。

保留任务和工具计数，证明无副作用。

### Real Java D：真实 Episode 落地

至少真实观察一次：

```text
LifeIntent
→ Plan item
→ 合法的 6B 转移
→ 对应的 Virtual ActivityEpisode
```

证明 Plan item 与实际 Episode 的活动名及关联记录一致。

不得直接调用 `force_transition()` 构造通过结果。

## 十六、真实 QQ 门禁

至少：

### QQ A

“你现在在干嘛？”

返回当前 ActivityEpisode，不返回尚未开始的未来活动。

### QQ B

“你接下来准备干嘛？”

返回当前 Future Plan，并能体现 Initiative 对规划的影响。

### QQ C

“你最近想干嘛？”

返回有效的 LifeIntent，而不是声称它已执行。

### QQ D

“你自己去 Minecraft 玩玩？”

最多产生虚拟兴趣及规划建议；任务数、确认状态、世界动作计数不得增加。

所有 QQ 实测必须记录时间、实际回复和对应的数据库 / 日志证据。

## 十七、文档与 CI

新增：

```text
docs/MINECRAFT_PHASE7B.md
```

更新：

```text
CHANGELOG.md
docs/README.md
```

完整运行：

```text
ruff check
ruff format --check
mypy
pytest
Node tests / e2e
WebUI typecheck
Vitest
WebUI build
committed-bundle checks
CI 双 job
```

同时验证 5A–5C、6A–6D.1 与 7A 回归。

不要通过降低断言、跳过真实门禁或把 mock 写成 Real PASS 来解决失败。

## 十八、禁止范围

Phase 7B 不做：

- 自主 Minecraft Task 创建
- 自主 Minecraft 动作
- 自动挖矿、采集、建造、跟随
- 自动确认或授权
- 新 Minecraft tool
- 新 ActionRuntime action
- 新 TaskRuntime state
- Initiative LLM
- 主动 QQ 消息
- 新的平行 ActivityPlanner / TaskRuntime
- 修改 Policy / ConfirmationStore 语义
- 修改 `allow_medium`
- 把 Plan 冒充成 Reality

## 十九、硬门禁与判定

最终必须逐项报告：

```text
PHASE 7B = PASS / BLOCKED

Commit:
CI:

Initiative → Planner integration:
PASS / FAIL

Candidate ranking:
PASS / FAIL

Hard constraints:
PASS / FAIL

Intent lifecycle:
PASS / FAIL

Plan persistence / recovery:
PASS / FAIL

Virtual Episode transition:
PASS / FAIL

6A / 6B / 6C / 6C.1 / 6D / 6D.1 regression:
PASS / FAIL

24h simulation:
PASS / FAIL

Real Java A / B / C / D:
PASS / FAIL

Real QQ A / B / C / D:
PASS / FAIL

No Minecraft world mutation:
PASS / FAIL

No Task creation:
PASS / FAIL

No confirmation bypass:
PASS / FAIL

No proactive QQ:
PASS / FAIL

No Initiative LLM:
PASS / FAIL

No new Minecraft tools / Actions / TaskRuntime states:
PASS / FAIL

allow_medium unchanged:
PASS / FAIL

Skipped:
...

Known limitations:
...
```

任意核心 Real Java / Real QQ 门禁未执行或失败：

```text
PHASE 7B = BLOCKED
```

不要把“代码已具备能力”写成“真机已验证”。

## 二十、阶段终点

最终形成：

```text
LifeIntent
    ↓
InitiativeActivityHint
    ↓
ActivityPlanner
    ↓
ActivityPlan
    ↓
6B DecisionEngine
    ↓
ActivityRuntime
    ↓
Virtual ActivityEpisode
```

成功标准：

> 罐头的主动想法第一次真正影响她未来的虚拟生活安排，并能在合法转移时反映到当前活动中；但她依然没有因此获得执行 Minecraft 世界动作的能力。

Phase 7B 完成前，不进入自主 Minecraft 执行阶段。