# CatooBot Phase 7C
# Life Intent → Task Proposal / Intent Execution Boundary

## 0. 阶段目标

Phase 7A 已完成 LifeIntent。
Phase 7B 已完成 Initiative → ActivityPlanner 集成。

本阶段目标：

> 建立从自主意图到结构化任务提案的安全桥梁，使罐头能够明确表达“我想做什么、为什么想做、需要什么能力、预期会改变什么”，但暂时不自动执行自主 Minecraft 任务。

本阶段不要求实现通用 Agent Harness，也不要求自动创造和安装任意工具。

## 一、架构原则

必须保持：

```text
LifeIntent
    ↓
Intent Assessment
    ↓
Task Proposal
    ↓
Feasibility / Capability Check
    ↓
Proposal Decision
    ├── REJECTED
    ├── NEEDS_MORE_INFORMATION
    ├── NEEDS_USER_APPROVAL
    └── READY_FOR_FUTURE_EXECUTION
```

本阶段的执行层仍然是：

```text
NONE
```

`READY_FOR_FUTURE_EXECUTION` 只代表提案通过当前的静态检查，不代表获得真实世界操作权限。

禁止：

```text
LifeIntent → TaskRuntime.create_task()
LifeIntent → TaskRuntime.confirm_and_start()
LifeIntent → Minecraft tool
LifeIntent → ActionRuntime
LifeIntent → auto-confirm
```

## 二、先检查现有实现

检查并复用：

```text
app/initiative/
app/activity/
app/tasks/
app/integrations/minecraft/
app/memory/
app/tools/
app/database/
```

尤其检查：

- LifeIntent 的状态与来源
- TaskRuntime 的现有任务模型
- TaskPlan 与 plan_hash
- Task 的风险分类
- Minecraft 原子工具及其 schema
- QQ / Minecraft / WebUI 入口
- 已验证的 QQ ↔ Minecraft IdentityLink
- ConfirmationStore 与 Policy
- 当前 ActivityPlan 和 ActivityEpisode
- 现有任务审计与持久化

不得创建第二套 TaskRuntime。

## 三、Task Proposal 数据模型

优先复用现有任务提案结构。如果不存在，新增一个最小模型：

```python
TaskProposal(
    proposal_id,
    source,
    objective,
    intent_id,
    initiator,
    target,
    required_capabilities,
    expected_effects,
    risk_summary,
    feasibility,
    status,
    created_at,
    expires_at,
)
```

实际字段应与当前仓库兼容。

Proposal 必须记录来源：

```text
USER
LIFE
SYSTEM
```

其中 LIFE 表示自主意图提出的候选任务；它本身不授予额外权限。

## 四、User Task 与 Life Task 必须区分

### USER

例如：

```text
QQ 用户：
跟着我。
```

应复用现有 QQ Task Entry、身份桥和 TaskRuntime。

如果 QQ 用户已经绑定 Minecraft 玩家，必须通过可信的 IdentityLink 解析真实的 MC 身份，再检查服务器状态和操作权限。

不能将消息发送者的昵称直接当作游戏玩家 ID。

### LIFE

例如：

```text
LifeIntent：
我想去 Minecraft 收集一点橡木。
```

只能创建：

```text
TaskProposal(source=LIFE)
```

不得创建真实 Task，不得直接调用 Minecraft 工具。

两条路径共享任务 schema、能力描述和可行性检查，但保留不同的来源及授权规则。

## 五、Proposal 不是任务

必须明确区分：

```text
LifeIntent
    = 为什么想做

TaskProposal
    = 准备怎样做

Task
    = 已进入现有执行系统的任务

Action
    = 实际发生的世界操作
```

创建提案不能增加 TaskRuntime 的活动任务数量。

提案进入 READY 状态也不能导致任何真实 Minecraft 操作。

## 六、Capabilities

建立或复用统一的能力描述。

至少能够表达：

```text
capability_id
description
input_schema
expected_effect
risk_class
available
limitations
```

优先从现有 19 个 Minecraft 原子工具及其他已注册能力提取。

不得因为某个工具存在，就声称整个目标必然可完成。

例如：

```text
目标：
建造刷铁机

现有能力：
移动、观察方块、挖掘、放置、物品栏操作
```

可行性检查必须能够指出：

```text
已具备：
移动、观察、放置等部分能力

仍需确认：
设计方案、材料数量、村民与僵尸机制要求、
服务器规则、实际验证方法
```

如果缺乏可靠方案，应返回 NEEDS_MORE_INFORMATION，而不是假装提案已可执行。

## 七、Capability Gap

Proposal 必须支持识别能力缺口：

```text
SUPPORTED
PARTIALLY_SUPPORTED
UNSUPPORTED
UNKNOWN
```

不得通过猜测将 UNKNOWN 自动升级为 SUPPORTED。

当能力不足时，可以提出：

```text
NEEDS_NEW_SKILL
NEEDS_NEW_TOOL
NEEDS_WORLD_OBSERVATION
NEEDS_USER_INPUT
```

这些只是后续工作建议，不允许自动编写、安装或执行任意工具。

## 八、Identity 与目标玩家

对需要指定玩家的提案，必须使用可信身份桥：

```text
QQ user
    ↓
VERIFIED IdentityLink
    ↓
Minecraft server_id + player_uuid
```

身份映射缺失、冲突、撤销或服务器不匹配时，不得猜测目标。

尤其是：

```text
“跟着我”
```

必须能区分：

- 发起指令的 QQ 用户
- 这个用户在目标服务器中的 MC 玩家
- 当前实际在线且可跟随的玩家实体

禁止根据同名昵称将两名真实用户合并。

## 九、风险与授权

提案阶段必须识别真实操作的风险类别，并复用现有 Risk / Policy 定义。

例如：

- SAFE：只读观察
- LOW：移动或跟随
- MEDIUM：挖掘、放置、物品操作等

本阶段不改变任何风险分类，不修改 `allow_medium=false`。

即使提案来源是 LIFE，也不能获得比 USER 更高的权限。

任何未来执行阶段，都必须重新检查当前世界、能力、授权、目标和风险；旧提案不能当作永久有效的执行许可证。

## 十、重复与过期

复用现有 fingerprint、TTL 和审计设施。

相同目标、来源和上下文不能无限产生重复提案。

过期、撤销或失效的 LifeIntent 不得被恢复为有效的自动执行请求。

Proposal 的终态不能被重启恢复覆盖。

## 十一、与 ActivityPlanner 的关系

7B 继续负责：

```text
LifeIntent → ActivityPlan
```

7C 新增的路径为：

```text
LifeIntent
    ↓
TaskProposal
```

两者不能混淆。

例如：

```text
LifeIntent：
想玩 Minecraft

ActivityPlan：
下一段虚拟活动可以安排 Minecraft 兴趣活动

TaskProposal：
如果决定执行某个具体目标，则描述任务、能力和预期结果
```

当前计划不能自动变成 TaskProposal，更不能自动执行。

## 十二、WebUI 与审计

在现有只读页面中展示：

```text
Proposal ID
Source
Objective
Related Intent
Target
Required Capabilities
Feasibility
Capability Gaps
Risk Summary
Status
Expiry
```

只读视图不得提供直接执行按钮。

不能展示完整敏感凭据、原始 QQ 事件对象或模型的隐藏推理内容。

## 十三、测试矩阵

至少覆盖：

1. LifeIntent 产生结构化 Proposal。
2. 过期或被抑制的 Intent 不产生有效 Proposal。
3. 重复 Proposal 被合并或拒绝。
4. 用户请求与自主意图保持不同来源。
5. VERIFIED IdentityLink 正确映射玩家。
6. REVOKED / CONFLICT 身份无法解析执行目标。
7. 缺失能力被标记为能力缺口。
8. 未知能力不被假定为可用。
9. SAFE / LOW / MEDIUM 风险正确映射。
10. Life Proposal 不创建 Task。
11. Proposal 不调用 Minecraft 工具。
12. Proposal 不修改 ActivityEpisode。
13. Proposal 不发起主动 QQ 消息。
14. 重启不生成重复 Proposal。
15. 提案过期后不能被自动复活。
16. `allow_medium` 默认保持 false。
17. 6A–6D.1 与 7A–7B 全部回归通过。

## 十四、真实门禁

Real Java：

- 在线与离线时，LifeIntent 都可以产生只读任务提案。
- 未绑定的玩家目标被拒绝或要求补充身份。
- 已验证的身份可以正确解析目标，但不自动执行。
- 能力缺口在实际提案中被明确显示。
- 提案期间世界动作、Task 数量及确认状态均不发生变化。

Real QQ：

- “你想收集一些橡木吗？”等自然语言互动不能误建 Task。
- 自主意图只能提出 Proposal，不能触发执行。
- 用户明确要求“跟着我”时，解析到正确 MC 玩家并通过现有用户任务执行链；此用例如因已有功能限制无法完整验证，必须如实记录 BLOCKED 或 SKIPPED。

最终必须同时保留代码、CI、数据库和真实环境证据。不能将 mock 任务提案写成 Real Java PASS。

## 十五、禁止范围

本阶段不做：

- 自主 Task 创建
- 自主 Minecraft 行动
- 自动确认
- 任意代码生成与安装
- 自动创建或修改 Minecraft 工具
- 通用 Agent Harness
- 技能自动发布
- 新 Minecraft 工具
- 新 ActionRuntime action
- 新 TaskRuntime 状态
- 主动 QQ 消息
- 修改 Policy 或 ConfirmationStore 语义
- 修改 allow_medium

## 十六、验收标准

```text
PHASE 7C = PASS / BLOCKED

Task Proposal model:
PASS / FAIL

User / Life source isolation:
PASS / FAIL

Identity resolution:
PASS / FAIL

Capability gap detection:
PASS / FAIL

Feasibility checks:
PASS / FAIL

Risk classification:
PASS / FAIL

Proposal persistence / expiry:
PASS / FAIL

No Task creation from LIFE:
PASS / FAIL

No Minecraft world mutation:
PASS / FAIL

No confirmation bypass:
PASS / FAIL

Real Java:
PASS / FAIL

Real QQ:
PASS / FAIL

CI and regressions:
PASS / FAIL

No new Minecraft tools / Actions / TaskRuntime states:
PASS / FAIL

allow_medium unchanged:
PASS / FAIL
```

Phase 7C 的终点是：

> 罐头能够将一个自主想法转化为清晰、可审计、知道自身能力边界的任务提案，但这个提案不会自行获得执行权。
