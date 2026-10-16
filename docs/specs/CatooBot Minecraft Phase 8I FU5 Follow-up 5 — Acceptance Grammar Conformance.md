# CatooBot Phase 8I FU5 Follow-up 5 — 接受语法位置约束与完整消费一致性审计

**阶段定位：** Follow-up 4 的定向补充审计。目标不是继续增加词元，而是证明结构自动机实际执行的顺序约束与文档声明的许可语法一致。

**仓库：** `rinsoraa/CatooBot`
**基线提交：** `4d8776e6f9d53a60a4903f01bcc51d7172ec2440`
**基线链接：** https://github.com/rinsoraa/CatooBot/commit/4d8776e6f9d53a60a4903f01bcc51d7172ec2440
**权威开发目录：** `E:\WorkSpace ZCode\CatooBot`
**发布镜像：** `E:\WorkSpace ZCode\CatooBot_github`

---

## 1. 本阶段要回答的问题

FU5 Follow-up 4 已将接受判定升级为“完整消费 + 单动作/单目标结构”。公开代码显示，`_acceptance_structure_error()` 对数量 token 的处理可能允许它出现在声明文法未允许的位置；同时 `_ACCEPTANCE_COUNT_RE` 的单位部分是可选的，而既有 `explicit_log_count()` / `parse_wood_request()` 的数量语义要求命中单位。

这形成了需要**先实测、再裁决**的候选风险：结构自动机可能接受不符合文档顺序、或仅因裸数字就被完整消费的表达。此处是代码审查提出的候选，不应在运行测试前写成已复现缺陷。

待决提案统一使用 `oak_log × 2`。首先在基线代码上记录以下消息的真实结果：

1. `好，两根去砍橡木原木` —— 数量 token 位于动作之前；
2. `好，去砍橡木原木带回来两根` —— 数量 token 位于已审计收尾语之后；
3. `好，去砍 2026 橡木原木` —— 数字没有原木数量单位，不应被悄悄当成合法数量 token；
4. `好，去砍橡木原木 2026` —— 目标后出现没有单位的裸数字；
5. 一条合法对照：`好，去砍两根橡木原木带回来`；
6. 一条数量冲突对照：`好，去砍三根橡木原木`。

如实验表明候选均已被当前路径拒绝，必须保留证据并检查是否仍有同类反例；不得为了证明有缺陷而修改产品代码。

## 2. 不可违反的安全与范围边界

1. 不新增 Minecraft 工具、执行器、权限、任务状态机、schema、数据库迁移或消息旁路。
2. 不改变 Policy、`ACTION_RISK`、`TOOL_ACTION`、`TaskAuthorization`、`TaskStepAuthorization`、双确认、冻结计划/`plan_hash`、逐步后置验证、STOP/CANCEL 权威。
3. 复合、含糊或结构不合文法的接受表达，必须保留待决提案并走现有 `handoff_compound_clarify`；不得消费提案、创建/推进任务或调用资源/能力执行入口。
4. 明确冲突的树种/数量继续按已有 `NEW_GOAL` 语义处理；不得把明确的新目标改写成对旧提案的确认。
5. 不拓展任意自然语言理解能力；只修复被测试证实的结构/数量 token 边界。
6. Follow-up 3 与 Follow-up 4 的所有既有断言原样保留，不删除、不放宽。

## 3. 基线实测：先测后改

在不修改生产代码的情况下，为 §1 候选新增最小诊断或参数化回归，并记录：

- `classify_handoff_reply()`；
- `handoff_target_verdict()`；
- `explicit_log_count()` 与 `parse_wood_request()` 的真实数量解释；
- `unexplained_acceptance_tail()`；
- `acceptance_structure_violation()` 的具体原因码；
- `_has_extra_intent()`；
- 实际 `_route_turn()` + `_arbitrate_pending_handoff()` 是否调用 `_accept_pending_handoff()`；
- 提案是否保留，以及 `wood_routes`、`delegate_calls`、`capability_calls`、`craft_calls`、`plans.calls` 是否为空。

测试必须清楚区分“helper 返回什么”与“消息是否真的消费了待决提案”。只测 `acceptance_structure_violation()` 不足以证明路由安全。

## 4. 若缺陷实测成立：修复原则

### 4.1 数量语法须与既有数量解析一致

- 如果许可语法将一段文本标为数量，必须与既有数量语义一致，不得出现“结构自动机把裸数字当数量，但 `parse_wood_request()` 不把它视为明确数量”的解释分歧。
- 数字是否必须带原木单位，须以既有 `_LOG_COUNT_UNITS` / `_parse_log_count()` 的契约为准；不要新增第二套单位列表。
- 一个任务中的数量 token 必须至多出现一次，并且只能位于文法允许的位置。

### 4.2 状态转移须实际执行文法位置约束

参照当前文档声明的单目标结构：

```text
填充词* 动作 填充词* [数量] (树种? 通用名词? 且至少其一)
填充词* [数量] 填充词* [收尾语] 填充词*
```

结构自动机不应仅因“当前状态允许某类别”就接受它。应明确定义每个 token 类别可到达的前置状态、是否转移状态、可重复次数，以及终态条件。

- 动作必须恰好一个，且必须先于目标；
- 目标必须恰好一个短语（树种 + 通用名词可共同构成一个目标）；
- 数量 token 的语义、单位和位置必须一致，重复/错位必须 fail closed；
- 收尾语只能出现在目标之后；数量不应因为处于 `after`/收尾状态而意外再次获准；
- 许可填充词可用于既有合法说法，但不得令动作、目标、数量、收尾的顺序约束失效。

不得用简单“出现数字就拒绝”或重复添加字符串黑名单代替结构化修复。

### 4.3 失败语义

- 结构不符合唯一单目标文法，或数量无法无歧义解释：`NOT_ACCEPT` → `handoff_compound_clarify`；
- 提案保留，不调用 `_accept_pending_handoff()`，不触发资源路由、委派、能力、合成或计划入口；
- 树种/明确数量冲突仍走 `NEW_GOAL`，但只有现有路由能够确认其为冲突新目标时才保留该行为；不要把畸形数字当成冲突目标；
- 纯接受、原有合法单目标描述、合法收尾语必须继续通过。

## 5. 自动化验收矩阵

新增以下分组，所有负向用例都必须走真实 `_route_turn()` 与真实 `_arbitrate_pending_handoff()`：

1. **数量位置反例：** §1 的 1–4 条；对每条分别断言结构原因码、未消费提案、澄清回复及执行入口零调用。
2. **合法对照：** `好，去砍两根橡木原木带回来` 继续能接受；数量恰为待决提案数量时消费一次且目标只来自提案。
3. **冲突对照：** `好，去砍三根橡木原木` 继续符合既有 `NEW_GOAL` 语义，旧提案不得被当作接受消费。
4. **重复/错位 token：** 重复数量、数量在收尾后、动作之前的数量、没有单位的裸数字；每条的结果必须能从明确文法解释，不允许“看起来测试通过但原因不清”。
5. **执行副作用断言：** `accept_calls == []`、原提案仍在 `_pending_handoffs`、出现 `handoff_not_accept` 和 `handoff_compound_clarify`、澄清文本非空；`wood_routes`、`delegate_calls`、`capability_calls`、`craft_calls` 全空，`plans.calls == []`。
6. **跨入口一致性：** 至少覆盖 `TASK_PROPOSAL`、`QUERY`、`ATOMIC_ACTION`，证明分类入口不会绕过仲裁边界。
7. **澄清后恢复：** 对负向输入澄清后，再给合法纯接受，原提案仍可且只可消费一次。
8. **既有矩阵不回归：** Follow-up 3 的许可集/冲突集/未知尾部测试与 Follow-up 4 的五条结构反例、机制分离、跨入口、真实路由断言均原样通过。

## 6. 真实服务器验证

继续使用本地专用 `scripts/phase8i_handoff_smoke_real.py`（不发布），运行真实 Java 1.21.1、真实 Runtime/MinecraftService、真实 Provider，并优先通过第二个 Mineflayer 客户端 `IdCraft8G` 投递一个新确认的数量位置/裸数字反例。

日志必须分开记录：

- 第二客户端实际发送且被真实服务器接收的消息；
- 决策、仲裁及 `handoff_compound_clarify` trace；
- 提案前后快照、游戏内真实送达的澄清话术；
- `targets=[]` 或等价的零执行证据；
- 目标相关背包前后快照；
- 真实客户端路径与聊天桥事件注入替代证据的区别。

如果第二客户端不可用，可以保留带 `DEVIATION` 标记的真实桥事件注入，但不能把它写成客户端证据。不得使用 `/give`、数据库写入或直接 Mineflayer 旁路制造通过结果。

## 7. 质量门禁与测试失败处理

- 先跑新加的定向测试，再运行 `pytest tests -q`。
- `ruff check .`、`ruff format --check .`、`mypy app`、`git diff --check` 全部通过。
- WebUI typecheck/tests/build 与构建产物一致性检查通过；GitHub Actions 两个 Job 均成功。
- 当前已知 `tests/test_social.py::TestCognitionEngine::test_direct_mention_not_blocked_by_sleeping` 存在时段抖动。不得修改/放宽该测试来掩盖问题；全量本地测试若仍失败，要分别记录本地结果与 CI 结果，明确是否为已知失败，不得把 `4571 passed, 1 failed` 描述成全绿。
- 检查受保护区域 diff 为 0、凭据扫描 0 命中；`scripts/` 与本地日志保持 untracked。
- 结束时记录 HEAD 与 `origin/main`、commit 数、dirty 状态、文件清单、提交 SHA 和任务书归档字节一致性。

## 8. 文档与交付报告

- `docs/MINECRAFT_PHASE8I.md` 追加新小节（建议 §15），保留 §10–§14 的历史记录与历史结论，不重写。
- `CHANGELOG.md` 记录动机、实测候选、修复原理、测试和限制。
- 将本任务书原文归档到 `docs/specs/` 并核对源文件与归档的字节/哈希一致性。
- 交付报告必须分别列出：修复前实测、最终结构规则、自动化结果、真实第二客户端证据、替代注入证据、本地全量测试结果与 CI 结果、尚未覆盖边界。

## 9. 最终裁决规则

只有满足以下条件才可裁定 `PASS`：

1. §1 每条候选都在基线被实测，并记录真实函数及仲裁输出；静态猜测不得写成运行时复现。
2. 数量 token 只有在语义、单位、数量值和位置均符合既有契约时才可参与接受；未解释的裸数字或结构错位不再被静默消费。
3. 合法纯接受、相容单目标描述、既有收尾语继续正常工作；明确树种/数量冲突的路由语义保持不变。
4. 结构负向用例在真实仲裁中保留提案、返回澄清、执行入口零调用；跨决策类型与 STOP/CANCEL/双确认契约无回归。
5. 测试、静态检查、WebUI、CI 结果完整报告且没有被隐瞒的失败；关键新增反例有真实第二客户端路径证据，或明确标注替代证据并按任务书保留相应限制。
6. 权限模型与所有受保护区域未变。

若所有候选已被当前代码正确拒绝，且新增测试证实没有同类可行反例，允许 `PASS / NO PRODUCTION CHANGE`，但仍须保留回归测试和真实路径证据。

## 10. 明确不在本阶段范围内

不处理 Phase 8D 导航可达性、配方变体按库存排序、真实成功迁移演练、Life→Play 新能力，或新的资源采集/建筑/战斗/交付。唯一目标是确保：**实现中的数量 token 解释、状态转移和声明文法严格一致，不会把畸形或错位表达静默当成对旧提案的接受。**
