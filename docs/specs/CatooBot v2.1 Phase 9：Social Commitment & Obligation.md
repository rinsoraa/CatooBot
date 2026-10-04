# CatooBot v2.1 Phase 9
## Social Commitment & Obligation

### 一、当前基线

当前最终基线：

`672518174ea5a5ba6eb12b935c2124c6cf05a4b2`

已完成：

- Phase 1 — Character Bible → Definition → Seed → Runtime
- Phase 2 — Entity Interaction + Event Bus + State Mutation
- Phase 3 — External Influence + Wakeup + Interrupt / Resume
- Phase 3 Remediation
- Phase 3.5 — State Authority Closure
- Phase 4 — Experience / Memory / Continuity
- Phase 4 Remediation
- Phase 5 — Cognitive Context Bridge
- Phase 5 Remediation
- Phase 6 — Cognitive Decision & Intent
- Phase 7 — Autonomous Life Loop & Goal
- Phase 7 Remediation
- Phase 7.1 — Resume Binding Integrity
- Phase 8 — Social & Relationship Dynamics
- Phase 8.1 — Person Identity + Cognitive Revision

当前：

```text
1207 tests passed
CI success
HEAD = origin/main = remote main
working tree clean
```

---

# 二、本阶段唯一目标

建立：

> **Social Commitment / Obligation Layer**

让角色与他人之间的：

- 约定
- 承诺
- 已答应的事情
- 预约
- 待履行事项
- 改期
- 取消
- 失约

成为真正的**持续状态**。

核心链：

```text
Social Interaction
        ↓
Commitment Detection
        ↓
Commitment
        ↓
Obligation
        ↓
Goal / Goal Step
        ↓
Action / Decision
        ↓
Outcome
        ↓
Commitment Update
        ↓
Relationship Consequence
        ↓
Experience / Memory
```

---

# 三、核心原则

必须严格区分：

```text
Relationship
=
“我和这个人是什么关系”

Commitment
=
“我答应这个人要做什么”

Goal
=
“当前世界我要持续完成什么”

Memory
=
“过去发生过什么”
```

不要把 Commitment 直接塞进：

```text
Relationship.metadata
```

也不要把 Commitment 直接写成：

```text
Memory
```

Commitment 是独立的**当前世界社会状态**。

---

# 四、开始编码前：审计现有 Promise / Obligation 能力

首先搜索全仓库：

```text
promise
commitment
obligation
appointment
schedule
pledge
todo
agreed
agreed_to
promised
deadline
social task
shared plan
```

同时检查：

```text
app/character/
app/continuity/
app/sandbox/
app/character/relationship.py
```

可能已有 v1.2 的：

```text
open_loops
shared_experiences
```

如果这些已经表达“未完成社会事项”：

> 必须评估是否可以复用。

禁止直接建立：

```text
CommitmentSystemV2
PromiseSystemV2
SocialObligationManagerV2
```

而没有解释旧系统为何不能使用。

最终报告必须给出：

```text
现有 commitment / promise 能力:
现有 open loop 能力:
本 Phase 复用:
本 Phase 新增:
```

---

# 五、建立 Social Commitment

如果现有模型无法表达，建立：

```text
SocialCommitment
```

或等价模型。

至少：

```text
commitment_id
character_id
person_id

kind
status

description

created_at
updated_at

source_interaction_id
source_event_id

due_at
earliest_at
latest_at

target_action
target_activity

priority

correlation_id
causation_id

metadata
```

状态至少：

```text
pending
active
scheduled
in_progress
completed
cancelled
declined
expired
broken
rescheduled
```

不要一次引入几十种状态。

---

# 六、Commitment 必须 Character-scoped

同一个人：

```text
person_X
```

在：

```text
罐头
```

和：

```text
阿澈
```

世界里：

> Commitment 完全独立。

必须使用：

```text
character_id + commitment_id
```

查询和持久化。

---

# 七、Commitment 与 PersonIdentity

必须使用 Phase 8 已经稳定的：

```text
person_id
```

不能重新把：

```text QQ id
```

存进 Commitment。

正确：

```text
commitment.person_id
=
person_abc
```

QQ：

```text
person_abc.external_ids["qq"]
```

只是身份入口。

---

# 八、Commitment 必须有来源事实

一个 Commitment 不能凭空出现。

至少必须能回答：

> “为什么系统认为这个承诺存在？”

必须有：

```text
source_interaction_id
source_event_id
```

完整 provenance：

```text
Commitment
    ↓
SocialInteractionFact
    ↓
ExternalWorldEvent
```

如果 Commitment 来自 Sandbox 内部行动：

```text
Commitment
    ↓
Sandbox Event
```

也必须可追溯。

---

# 九、不要让 LLM 自动创造 Commitment

本阶段第一版：

```text
Commitment Detection
=
确定性
```

例如输入事件已经明确表示：

```text
invitation_accepted
appointment_confirmed
promise_made
```

才创建 Commitment。

不要：

```text
每条聊天
→ LLM
→ “我觉得这可能是一个约定”
```

---

# 十、Acceptance ≠ Commitment

这是非常重要的。

例如：

```text
空凛：
“来打 Minecraft？”

罐头：
“好。”
```

这里产生：

```text
invitation_accepted
```

但是否产生长期 Commitment：

> 必须取决于是否存在明确未来行动。

例如：

```text
“今晚一起玩”
```

可以：

```text
commitment = scheduled shared gaming
```

而：

```text
“有空一起玩”
```

可能只是：

```text
social intention
```

不要自动生成高强度 Commitment。

---

# 十一、Commitment 类型

第一阶段至少支持：

```text
shared_activity
appointment
help
follow_up
deliverable
```

例如：

```text
一起玩 Minecraft
帮忙做某件事
之后回复某件事
约定某个时间
完成一个明确事项
```

不要在 Phase 9 做复杂现实社会合同。

---

# 十二、确定 Commitment Strength

可以增加：

```text
strength
```

例如：

```text
weak
normal
strong
```

或者：

```text
explicit
implicit
```

第一阶段推荐：

```text
explicit
soft
```

只有明确表达：

```text
“好，今晚一起玩”
“我明天帮你做”
“晚上八点一起看”
```

才产生强 Commitment。

模糊表达：

```text
“下次吧”
“有时间一起”
“以后再说”
```

不自动生成 active commitment。

---

# 十三、Commitment 时间语义

如果事件明确带时间：

```text
今晚
明天
周六
8点
30分钟后
```

必须解析为：

```text
earliest_at
latest_at
due_at
```

但：

> 不要让 Phase 9 变成自然语言时间解析项目。

优先复用已有时间解析器。

如果没有可靠解析：

```text
只保存原始 time_hint
```

不要猜具体日期。

---

# 十四、Commitment 不直接等于 Goal

正确：

```text
Commitment:
与空凛今晚一起玩 Minecraft

Goal:
履行这个社会承诺

Goal Step:
启动 Minecraft / 准备联机
```

Goal 是：

> 执行层

Commitment 是：

> 社会义务层

因此：

```text
Commitment
   ↓
Goal
```

是允许的。

但：

```text
Memory
   ↓
Commitment
```

本阶段禁止。

---

# 十五、Commitment → Goal

建立一个轻量的：

```text
CommitmentGoalBridge
```

或者复用 GoalDetector。

职责：

> 把当前有效且到执行窗口的 Commitment 转成 Goal。

例如：

```text
Commitment:
今晚一起打 Minecraft
status=pending

↓ 到 earliest_at

Goal:
fulfill_commitment
```

Goal target：

```text
person_id
commitment_id
target_activity
```

不要把整个 Commitment JSON 复制到 Goal。

只引用：

```text
commitment_id
```

---

# 十六、不要每 Tick 重复创建 Goal

Commitment：

```text
C1
```

只能产生：

```text
Goal G1
```

而不是：

```text
G1
G2
G3
G4
```

使用：

```text
commitment_id
+
goal kind
```

去重。

---

# 十七、Commitment Goal 需要优先级

默认：

```text
critical commitment
>
time-bound appointment
>
explicit shared activity
>
soft follow-up
```

但必须：

```text character-defined
```

且：

```text relationship context
```

只能影响排序，不直接执行。

---

# 十八、Commitment 与 Decision

例如：

```text
Commitment:
今晚与空凛一起玩

当前：
看动画

到执行窗口
```

合法候选：

```text
continue_current
fulfill_commitment
postpone
```

如果真的存在：

```text
两个以上合法方案
```

进入 Phase 6：

```text
DecisionCoordinator
```

否则 deterministic。

---

# 十九、Commitment 不是“强制行动”

即使：

```text
commitment = strong
```

也不能直接：

```text
ActionSystem.start()
```

仍然：

```text
Commitment
 ↓
Goal
 ↓
Decision
 ↓
Validator
 ↓
Action
```

如果 Character Rule 明确禁止：

```text
必须拒绝/取消/重新安排
```

---

# 二十、Commitment Fulfillment

必须定义：

```text
commitment completed
```

的事实条件。

例如：

### 一起玩

不是：

```text
ACTION_STARTED
```

而是：

```text
shared_activity
真正发生
```

例如至少：

```text
两人都进入共同活动
+
活动达到最小持续时间
```

具体阈值由实现决定。

---

# 二十一、Commitment Promise 与 Actual Outcome 分开

例如：

```text
答应一起玩
```

产生：

```text
Commitment
```

后来：

```text
真正一起玩了
```

产生：

```text
commitment_completed
+
shared_activity
```

如果：

```text
因为临时情况没去
```

产生：

```text
commitment_broken
```

如果：

```text
提前说明改天
```

产生：

```text
commitment_rescheduled
```

绝对不要把：

```text
promise
```

直接当：

```text fulfillment
```

---

# 二十二、Commitment outcome 必须影响 Relationship

不是直接：

```text
commitment completed
→ trust + 0.2
```

必须：

```text
Commitment Outcome
      ↓
SocialInteractionFact / SocialOutcome
      ↓
RelationshipUpdateEngine
```

继续复用 Phase 8。

例如：

```text
fulfilled
→ reliability_positive
```

```text
broken
→ reliability_negative
```

具体 relationship delta：

> 由 Phase 8 deterministic rules 管理。

不要在 Phase 9 再发明第二套关系数值逻辑。

---

# 二十三、Commitment 取消

支持：

```text
cancelled
```

必须知道：

```text
who cancelled
why
when
```

例如：

```text
character_cancelled
other_party_cancelled
system_expired
```

取消不一定是负关系结果。

例如：

```text
提前礼貌说明
```

与：

```text
完全失联
```

应该是不同 SocialInteractionFact outcome。

---

# 二十四、Commitment Broken

只有真正：

```text
due_at passed
+
required outcome absent
```

才认为：

```text
broken
```

不要：

```text
Action delayed 1 minute
→ broken
```

必须允许：

```text
grace window
```

或等价容错。

---

# 二十五、Commitment Reschedule

例如：

```text
今晚打不了
→ 改成明晚
```

不要：

```text
delete old commitment
create random new commitment
```

而应该：

```text
same commitment_id
status=rescheduled
revision += 1
old schedule archived in metadata
new due_at
```

或者项目中等价设计。

目标：

> 一个承诺的生命线可追踪。

---

# 二十六、Commitment Conflict

可能同时存在：

```text
C1: 20:00 与空凛打游戏
C2: 20:00 看一集动画
C3: 小喵需要照顾
```

这里：

```text
C3
```

可能是：

```text
need-critical
```

而不是社会承诺。

最终应：

```text
Goal priority
+
Decision Layer
```

解决。

不要建立：

```text
CommitmentConflictPlanner
```

---

# 二十七、Commitment 与 Goal Resume

如果：

```text
fulfill_commitment Goal
```

执行中的 Action 被：

```text
critical pet
```

打断：

```text
ACTION_INTERRUPTED
```

之后：

```text
ACTION_RESUMED
```

必须：

```text
GoalStep
+
Commitment
```

都保持一致。

---

# 二十八、Commitment Persistence

增加最小表：

```text
sandbox_commitments
```

至少：

```text
commitment_id
character_id
person_id

kind
status
strength

created_at
updated_at
earliest_at
latest_at
due_at

source_interaction_id
source_event_id

target_activity
target_action
metadata
```

索引：

```text
character_id + status
character_id + person_id
character_id + due_at
character_id + status + person_id
```

---

# 二十九、启动恢复

Runtime restart：

```text
Commitments reload
↓
active commitments restored
↓
expired commitments evaluated
↓
Goal bridge re-evaluates
```

不要：

```text
every old commitment
→ new goal
```

必须根据：

```text status
time window
existing goal
```

判断。

---

# 三十、Continuity

Phase 5/7 ContinuitySnapshot：

只放：

```text
important active commitments
```

最多：

```text
3
```

例如：

```text
今晚答应和空凛一起玩
明天约了某件事
还有一个待跟进事项
```

不要把历史所有 Commitment 塞进 Prompt。

---

# 三十一、Cognitive Context

当前对话中：

如果用户就是 Commitment 的 person：

```text
相关 Commitment
```

可进入：

```text
CognitiveContext
```

例如：

```text
“你今晚还来吗？”
```

Context 可包含：

```text
active commitment:
shared gaming
due tonight
person = current user
```

但：

> Context 只是提供信息。

不要在 Context Builder 里自动执行 Goal。

---

# 三十二、Memory

Commitment 本身：

```text
created
```

不要自动成为长期 Memory。

但是：

```text
meaningful fulfillment
major broken commitment
major reschedule
```

可以进入：

```text
Experience
→ MemoryCandidate
```

继续沿用 Phase 4。

---

# 三十三、Relationship Memory

如果：

```text
fulfilled commitment
```

导致显著 Relationship 变化：

```text
RELATIONSHIP_CHANGED
```

Phase 4 ExperienceBuilder 再决定是否形成：

```text
social Memory
```

Phase 9 不直接写：

```text
Memory
```

---

# 三十四、External Event

外部世界入口保持：

```text
QQ
 ↓
ExternalWorldEvent
 ↓
SocialInteractionFact
 ↓
Commitment detection
```

不重新实现 QQ adapter。

---

# 三十五、Commitment Detection

第一版本只允许：

```text
explicit semantic facts
```

例如：

```text
invitation_accepted + explicit future time/activity
promise_made
appointment_confirmed
```

不要：

```text
普通聊天
→ Commitment
```

---

# 三十六、不要过度 NLP

第一版可以使用已有：

```text
semantic_kind
target_activity
metadata
time_hint
```

如果这些信息不完整：

> 宁可不创建 Commitment。

不要猜。

---

# 三十七、Commitment revision

增加：

```text
revision
```

每次：

```text
reschedule
cancel
change target
change due_at
```

递增。

Goal 在执行前验证：

```text
commitment_id
+
commitment_revision
```

避免：

```text
旧 Commitment Proposal
```

执行了已经改期的 Commitment。

---

# 三十八、Commitment Staleness

Decision / GoalStep 可以记录：

```text
commitment_revision
```

如果：

```text
current_revision != request_revision
```

则：

```text
stale
```

必须重新评估。

可以复用 Phase 6：

```text
world_revision
cognitive_revision
```

不要再建立第三套全局 revision。

---

# 三十九、建议：Commitment 不改变 world_revision

Commitment 是：

```text
社会状态
```

不是：

```text
物理世界
```

所以：

```text
affects_world=False
```

但它必须增加：

```text
cognitive_revision
```

并且：

```text
commitment_revision
```

用于自身生命周期。

---

# 四十、事件

继续使用现有：

```text
SandboxEventType
```

新增少量：

```text
COMMITMENT_CREATED
COMMITMENT_ACTIVATED
COMMITMENT_RESCHEDULED
COMMITMENT_FULFILLED
COMMITMENT_CANCELLED
COMMITMENT_BROKEN
```

不要再创建一个 EventBus。

事件必须：

```text
causation_id
correlation_id
```

---

# 四十一、完整因果链

例如：

```text
EXTERNAL_EVENT_RECEIVED
        ↓
SOCIAL_INTERACTION
        ↓
COMMITMENT_CREATED
        ↓
COMMITMENT_ACTIVATED
        ↓
GOAL_CREATED
        ↓
GOAL_ACTIVATED
        ↓
DECISION_REQUESTED
        ↓
ACTION_STARTED
        ↓
SHARED_ACTIVITY
        ↓
COMMITMENT_FULFILLED
        ↓
RELATIONSHIP_CHANGED
```

注意：

```text
COMMITMENT_FULFILLED
```

之后的关系变化必须仍然走：

```text
SocialInteractionFact
→ RelationshipUpdateEngine
```

---

# 四十二、必须实现的案例

## Case A：明确答应今晚一起玩

输入：

```text
今晚一起打 Minecraft
```

且：

```text
invitation_accepted
explicit time/activity
```

产生：

```text
Commitment
```

但不立即：

```text
Action
```

等待执行窗口。

---

## Case B：执行窗口到来

```text
Commitment
↓
Goal
↓
Decision
↓
Action
```

如果唯一合法候选：

```text
deterministic
```

如果多个：

```text
LLM
```

---

## Case C：真正完成共享活动

必须：

```text
shared_activity
↓
COMMITMENT_FULFILLED
↓
relationship outcome
```

---

## Case D：拒绝

例如：

```text
邀请
↓
Decision
↓
Decline
```

则：

```text
invitation_declined
```

但：

```text
不创建 strong commitment
```

除非系统认为之前已经存在明确承诺。

---

## Case E：改期

```text
Commitment
↓
reschedule
↓
same commitment_id
revision +1
new due_at
```

旧 GoalStep / Decision：

```text
stale
```

---

## Case F：失约

```text
due_at
+
grace window
+
未完成
↓
COMMITMENT_BROKEN
↓
SocialInteractionFact
↓
Relationship update
```

---

# 四十三、测试

至少新增：

### Test 1
明确未来约定创建 Commitment

### Test 2
模糊“下次一起玩”不创建强 Commitment

### Test 3
Invitation ≠ Commitment

### Test 4
Commitment character isolation

### Test 5
Commitment persistence

### Test 6
Commitment restart recovery

### Test 7
Commitment → Goal

### Test 8
Commitment Goal 不重复创建

### Test 9
Commitment → Decision

### Test 10
Commitment fulfilled

### Test 11
Commitment broken

### Test 12
Commitment rescheduled

### Test 13
Old Goal rejected after commitment revision

### Test 14
Critical Goal interrupts commitment Goal

### Test 15
Commitment survives Action interrupt / resume

### Test 16
Fulfilled Commitment → Relationship outcome

### Test 17
Broken Commitment → Relationship outcome

### Test 18
Commitment itself不直接写 Memory

### Test 19
Major commitment outcome → Experience / Memory

### Test 20
Continuity只出现 active important commitments

### Test 21
CognitiveContext 当前相关人时显示 Commitment

### Test 22
No LLM for deterministic Commitment lifecycle

### Test 23
LLM only when multiple valid fulfillment choices

### Test 24
Commitment revision staleness

### Test 25
Cross-character commitment isolation

---

# 四十四、自治模拟

增加：

```text
24h / 48h social commitment simulation
```

确保：

```text
无 QQ
→
无 Commitment
```

不会出现虚假承诺。

有：

```text
synthetic scheduled commitment
```

能够：

```text
Goal
→
Action
→
Fulfillment
```

并正确结束。

固定 simulation seed 下：

```text
same commitment sequence
same goal sequence
same action sequence
```

---

# 四十五、LLM 边界

本阶段：

```text
LLM ≠ commitment detector
LLM ≠ commitment writer
LLM ≠ relationship scorer
LLM ≠ planner
```

LLM 只用于：

```text
多个合法履约方式之间的决策
```

继续使用：

```text
Phase 6 DecisionCoordinator
```

---

# 四十六、Relationship 边界

继续：

```text
Commitment Outcome
→ SocialInteractionFact
→ RelationshipUpdateEngine
```

禁止：

```text
Commitment
→ trust += ...
```

---

# 四十七、Memory 边界

继续：

```text
Commitment outcome
→ Experience
→ Memory Candidate
```

禁止：

```text
Memory
→ Commitment
```

---

# 四十八、Goal 边界

允许：

```text
Commitment
→ Goal
```

禁止：

```text
Relationship
→ 自动创建 Commitment
```

除非本阶段有明确事实。

---

# 四十九、工程边界

禁止：

- 新 EventBus
- 新 Mutation System
- 新 Goal System
- 新 Decision System
- 新 Relationship System
- 新 Memory System
- 新 Planner
- 新 Agent
- Embedding
- Vector Search
- LLM commitment extraction
- LLM commitment scoring
- Memory → Commitment
- Character Bible rewrite
- QQ adapter rewrite
- Git history rewrite

---

# 五十、测试门禁

执行：

```powershell
ruff check .
ruff format --check .
mypy app
pytest -q
```

要求：

```text
原 1207 tests 全部保留
+
Phase 9 tests
=
全部通过
```

CI 必须 success。

---

# 五十一、Git 双目录

开发：

`E:\WorkSpace ZCode\CatooBot`

发布：

`E:\WorkSpace ZCode\CatooBot_github`

发布前继续扫描：

- API keys
- tokens
- .env
- 真实 Character Bible
- local DB
- logs
- Zcode private workflow
- local paths
- secrets

不得上传：

`E:\WorkSpace ZCode\CatooBot\config\character_bible.md`

不要：

```text
git filter-repo
BFG
force push
history rewrite
```

---

# 五十二、最终报告必须包含

```text
Phase 9 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

现有 promise / obligation 能力:
<审计结果>

SocialCommitment:
<文件>

Commitment persistence:
<表 + migration>

Commitment detection:
<入口>

Commitment → Goal:
<入口>

Commitment → Decision:
<入口>

Fulfillment:
<事实条件>

Broken:
<判定>

Reschedule:
<机制>

Revision:
<机制>

Stale protection:
<测试>

Relationship outcome:
<入口>

Memory outcome:
<入口>

Continuity:
<入口>

Character isolation:
<结果>

LLM:
<调用条件>

24h/48h simulation:
<结果>

是否 Memory → Commitment:
No

是否 Relationship → direct Commitment:
No

是否增加 Planner:
No

是否增加 Agent:
No

是否进入 Phase 10:
No
```

完成后停止。

# 五十三、最终原则

Phase 8 解决了：

> **“这个人对角色来说意味着什么？”**

Phase 9 解决：

> **“角色和这个人之间，有哪些尚未完成、但真实存在的社会约定？”**

最终形成：

```text
Social Interaction
        ↓
Relationship
        ↓
Commitment
        ↓
Goal
        ↓
Decision
        ↓
Action
        ↓
Outcome
        ↓
Relationship
        ↓
Experience
        ↓
Memory
```

这会让“朋友关系”第一次拥有真正的**时间性和责任性**。

例如：

```text
空凛：
“今晚八点一起打 Minecraft？”

罐头：
“好。”
```

系统不应该仅仅得到：

```text
trust + 0.02
memory = “空凛邀请过我”
```

而应该形成：

```text
Person = 空凛
Relationship = core friend
Commitment = tonight shared gaming
Goal = fulfill commitment
```

到了晚上：

```text
当前 Action
+
Commitment
+
Needs
+
Relationship
+
Memory
```

共同进入 Decision。

如果真正一起玩了：

```text
shared_activity
→ commitment fulfilled
→ relationship update
→ experience
→ memory
```

如果没做到：

```text
commitment broken
→ social outcome
→ relationship consequence
```

这样以后“罐头答应过什么、欠谁什么、约过什么、有没有做到”，就不再是聊天上下文里的松散文字，而成为 Sandbox 中真正存在的社会世界状态。

**现在 Phase 8.1 可以封版，直接让 Zcode 开始 Phase 9。**