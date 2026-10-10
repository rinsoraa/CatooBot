# Phase 7D.2 — Concurrency & Persistence Closure

> **执行对象：** ZCode  
> **仓库：** `rinsoraa/CatooBot`  
> **审计基线：** Phase 7D.1 报告提交 `08a5013`（完整 SHA：`08a5013f11d4b1c5254ae04bac8726406d2eef18`）。开始前必须重新确认实际分支、HEAD、工作树和远端状态；如果已有后续提交，基于最新代码审计与修复，不得重置或覆盖用户改动。  
> **当前状态：** Phase 7D.1 暂为 `BLOCKED`，不能因报告称 PASS 而跳过下面的核验。  
> **本阶段目标：** 修复跨会话跟随预留竞态、AgentPlan SQLite CAS/payload 一致性与崩溃恢复、跟随任务缺失关联时的安全清理，并重新完成真实 Minecraft Java 中途世界变化门禁 C。

---

## 0. 执行纪律与安全不变量

先读取当前源码并确认历史审计指出的问题是否仍然存在；若后续代码已修复其中某项，应提供具体代码位置和测试证据，不要重复修改已解决的实现。

本阶段是针对性整改，不是重写 AgentPlan 或 TaskRuntime。必须保持：

- Minecraft 注册工具数 **19 → 19**。
- 新增 Action 数为 **0**。
- TaskRuntime 状态数 **不增加**。
- `allow_medium=false` 保持不变。
- 不改变既有 Policy、风险分级、用户确认与 TaskStepAuthorization 语义。
- 不新增 `setblock`、任意命令执行、任意 Python/JavaScript/Shell 执行或其他高权限生产工具。
- 世界动作继续通过现有 TaskRuntime、Policy、ActionRuntime、MinecraftService 与 Mineflayer 路径。
- AgentPlan 只存计划与关联信息，不复制 TaskRuntime 的运行状态。
- 不删改用户现有工作，不 force-push，不将临时测试配置留在生产配置中。

开始时记录：`git status --short`、分支名、完整 HEAD SHA、远端 HEAD，以及相对 `08a5013` 的提交关系。完成时再次记录，并提供最终 commit 与可查验的 CI 记录。

## 1. P1 — 修复跟随请求的跨会话预留竞态

### 1.1 已观察到的代码风险

当前 `AgentPlanService.plan_follow_from_user()` 使用 reserve-then-link：先创建 `PLANNING` 预留记录，然后创建 Task，最后把计划更新为 `LINKED` 并写入 `task_id`。但 `_follow_retry_or_yield()` 在碰到一个尚无 `task_id` 的 `PLANNING` 记录时，可能马上生成 `|rN` 重试指纹并继续创建新 Task。

原负面测试 `test_concurrent_requests_only_one_task` 使用相同 `session_id`，容易由 TaskRuntime 的同会话任务互斥替 AgentPlan 掩盖问题。该测试不足以证明不同 QQ session 的并发请求也被安全协调。

### 1.2 修复要求

1. 审核 `plan_follow_from_user()`、`_follow_retry_or_yield()`、`_create()`、`count_by_stem()`、唯一指纹索引和 `TaskRuntime.create_task()` 的完整调用顺序。
2. 对同一个逻辑请求的重复跟随意图，确保预留层本身能协调并发；**不能把同 session 的 TaskRuntime 互斥当成唯一防线**。
3. 当请求发现同指纹记录为 `PLANNING` 且还没有 `task_id` 时，应将它解释为“另一请求可能正在预留/创建任务”，保守地让路或返回明确的处理中状态，不得立即创建另一个 `|rN` 任务。
4. 只有在能可靠证明前一个预留已经终止、已取消/过期/恢复补偿，且没有活动任务关联时，才允许执行有界重试。不能把任务读取异常或短暂读不到状态解释成“原任务已终态”。
5. 如果通过 reservation lease/过期策略区分活跃预留与崩溃遗留预留，必须保证 lease 有明确上限，并与已有计划 TTL、`recover()` 和唯一索引保持一致。不要无界等待，也不要因刚建立的预留很快触发重复任务。
6. 审核指纹的隔离范围，至少考虑 source、objective、已验证的 `player_uuid`、`server_id`、请求者身份和时间桶。不能因为不同服务器同名/相似目标或不同 QQ 用户而意外共用一个计划、任务或对另一用户泄露任务信息。若决定不把某字段放进指纹，必须解释并由测试证明其安全性。
7. 并发请求竞争同一基础指纹或重试指纹时，只有一个请求可以继续创建 Task。另一请求必须返回已有计划/任务信息（仅允许当前调用者获知的范围），或明确拒绝；不得因 `create()` 返回旧预留记录而继续创建第二个任务。
8. reserve 成功而 Task 创建失败时，预留要以可审计方式终结；Task 创建成功但 link 失败时，使用现有安全路径取消未执行的待确认任务。失败补偿本身若失败，必须记录实际状态并阻止确认，不得宣称已经取消成功。
9. 说明 reserve、Task 创建、link 和进程崩溃各个边界的恢复语义。不要假设 AgentPlan 数据库和 TaskRuntime 自动处于一个跨表事务内。

### 1.3 必须增加的自动化测试

除已有同 session 测试外，至少补齐以下场景：

- **不同 session 并发：** 相同 QQ user、相同已验证 Minecraft 目标、不同 `session_id`；用 barrier/event 控制时序，使请求 B 确实在请求 A 的 `PLANNING` 已落盘、Task 尚未关联时到达。预期只能创建一个 Task。
- **不同用户并发：** 如果系统允许多个 QQ 用户分别绑定到同一 Minecraft UUID，验证不存在跨用户误用计划、任务 owner 或泄露任务详情。
- **不同服务器：** 同名/相似目标但 `server_id` 不同，不得意外复用另一个服务器的待确认任务。
- 旧任务处于 `PENDING_CONFIRMATION`、运行中、终态、任务读取异常四种情况下的重复请求行为。
- 并发竞争同一个 `|rN` 重试指纹。
- Task 创建后 link 失败、取消失败、进程在 reserve/link 之间中断后的恢复行为。

断言必须覆盖：Task 数量、Task owner/session、每个 `task_id` 与 AgentPlan 的双向关联、计划状态、确认门是否被调用、follow 工具是否派发，以及审计事件。不得只断言返回的 `action` 字符串。

## 2. P1 — 修复 SQLite CAS 的状态列与 JSON payload 不一致

### 2.1 已观察到的代码风险

`SqliteAgentPlanStore.occupy_for_approval()` 的 CAS SQL 更新独立 `status` / `updated_at` 列，但未同步更新 JSON `payload`。读取 `get()` 时从 `payload` 反序列化 AgentPlan。进程若在批准占用成功后、后续关联 Task 前中断，数据库列和对象 payload 可能出现相互矛盾的状态。

### 2.2 修复要求

1. 在单个 SQLite 事务/原子条件更新中，同时更新独立 `status`、`updated_at` 与 JSON `payload` 中对应字段。
2. CAS 的 `WHERE` 必须继续限制正确的 `plan_id`、当前状态为 `READY_FOR_APPROVAL` 以及计划未过期。不能仅靠 Python 读状态后再普通 UPDATE。
3. 并发批准只能让一个请求成功占用；失败请求不得创建 Task。
4. CAS 后立即重新 `get()`，状态、期限、批准处理中状态应一致。检查所有从独立列与 payload 双重保存同一事实的写入路径，避免只修一处导致其他路径仍可产生不一致。
5. 确定 `APPROVED` 的崩溃恢复策略：如果进程重启后发现一个 `APPROVED` LIFE 计划没有 `task_id`，它必须被识别为未完成的批准占用，并在不创建 Task、不自动恢复执行的前提下安全终结或进入明确定义的恢复状态。不得让它永久卡在开放状态，也不能让它被再次当成 `READY_FOR_APPROVAL`。
6. 恢复逻辑必须可幂等重复执行；终态不复活，已经关联到 Task 的计划状态不能被误取消。
7. 若批准失败后状态补偿为 `CANCELLED`，确保独立列与 payload 同步更新，并记录具体原因。日志记录失败时也不得把未完成状态说成已完成。

### 2.3 必须增加的真实 SQLite 测试

不能只使用 `InMemoryAgentPlanStore`。使用真实临时 SQLite 数据库，执行真实迁移与 `SqliteAgentPlanStore`：

- 先创建 `READY_FOR_APPROVAL` 计划，再执行 `occupy_for_approval()`；分别检查 SQL `status` 列、`payload.status` 和 `get()` 返回对象，三者必须一致。
- 两个并发批准请求竞争相同计划，最多一个 CAS 成功、最多创建一个 Task。
- CAS 成功后模拟进程在 Task 创建前中断，重新构建 Store/Service 并运行 `recover()`；计划不能被误认为待批准，也不能自动创建 Task。
- Task 已创建但关联更新失败、Task 取消补偿成功/失败的恢复情形。
- `expire_due()` 与 CAS 交错运行时，过期计划仍然不能被批准。
- 同一恢复过程重复运行两次，不能重复创建 Task、重复派发动作或复活终态。

如果能通过直接 SQL 状态断言更清楚地验证一致性，应同时断言实际数据库列与 JSON 内容，不要只测对象层面的 mock。

## 3. P1 — 跟随任务缺失 AgentPlan 时安全取消

### 3.1 当前差异

`_verify_follow_identity()` 目前对跟随 Task 缺少 AgentPlan、`plan_for_task()` 异常或计划目标 UUID 缺失的情况会 fail-closed 拒绝确认，但有些路径只返回否决消息，并没有尝试取消仍处于 `PENDING_CONFIRMATION` 的跟随 Task。现有负面测试也可能将“没有调用 cancel”当成预期。

### 3.2 修复要求

1. 先从当前 Task 的冻结步骤识别是否包含 `minecraft_follow_player`。如果 Task 已被证明是普通非跟随任务，可继续原确认流程。
2. 一旦确认这是待确认跟随 Task，AgentPlan 缺失/读取异常、目标 UUID 缺失、服务器身份不可验证或身份桥不可用，都应拒绝本次确认，并通过现有 `TaskRuntime.cancel()` 尝试取消这个尚未执行的任务。
3. 如果取消成功，回复与日志可说明任务已取消；若取消失败，只能说明“确认被拒绝、取消未能确认”，不可谎称已取消。无论取消是否成功，本次都不允许进入 `confirm_and_start()` 或派发 `minecraft_follow_player`。
4. 如果读取 Task 本身失败，必须阻止确认并记录错误。无法定位 task_id 时应明确记录“任务无法读取，无法执行取消”，不能把拒绝确认误报成取消成功。
5. 身份一致时仍需校验 VERIFIED、`player_uuid`、`server_id`、`player_name` 与冻结 `username` 一致。不能为了让普通任务通过而降低跟随任务的门禁。

### 3.3 必须更新的负面测试

将测试从“返回否决文案”提升到“确认门确实无法执行、必要的取消确实发生”：

- AgentPlan 缺失 → 调用既有取消路径；
- `plan_for_task()` 异常 → 取消并拒绝；
- 目标 UUID 缺失 → 取消并拒绝；
- REVOKED / CONFLICT / MISSING / 换号 / 跨服务器 / username 不一致 → 取消并拒绝；
- 取消本身抛异常 → 仍然拒绝确认，记录取消失败，不伪称已取消；
- 一致身份 → 允许继续原有确认路径；
- 普通非跟随 Task → 不触发 follow 专用取消。

如现有测试只直接调用 `_verify_follow_identity()`，还要至少增加入口/调用链级测试，证明否决之后 `handler.handle()` 中的 `confirm_and_start()` 没有被调用。

## 4. P1 — 重做 Real Java C：必须先运行，后改变世界

### 4.1 上一轮证据为什么不符合要求

上一份报告记录的顺序是：Task 于 22:12:45 创建，第二客户端在 22:12–22:35 执行多次 `/setblock ... air`，用户到 22:36:08 才确认任务开始。按照该时间线，世界变化发生在 Task 开始执行之前。此外，实际 dig 坐标 `(-984, 83, 649)` 与报告声称被清除区域 `(-987..-993, 81..83, 649..650)` 不一致。

所以，前一轮不能证明“任务已开始且完成至少一步后，执行期间改变下一步依赖的世界状态，进而触发安全响应”。本轮必须重做，不能只改文档措辞。

### 4.2 真机测试要求

使用真实 Minecraft Java 服务端、真实运行中的 bot、真实 QQ 入口和第二个真实玩家客户端/管理员控制台。不要用 mock 世界状态代替真实测试。

1. 选择一个可确定性复现的多步骤任务，并使用 SAFE 观察记录实际目标方块/目标位置、当前世界状态、计划步骤和 `plan_hash`。
2. 通过真实 QQ 提交请求并展示冻结计划；操作者按既有确认机制确认。记录确认时间、Task ID、状态变更事件和第一个实际步骤的结果。
3. 等待任务处于**已开始执行**的状态，并且至少一个步骤已有真实成功 checkpoint。不得在确认前改变测试目标。
4. 由第二个客户端在任务运行期间改变**下一步确实依赖**的世界状态。优先选择与真实冻结计划的下一步有直接关系的方块/目标；执行前后都用 SAFE 世界观察记录其位置与状态。`/setblock` 只允许从第二个有权限的测试客户端/管理员控制台执行，不能为 bot 增加命令执行通道。
5. 时间线必须证明：确认并开始执行 → 至少一步成功 → 外部世界变化 → 下一步骤观察/前置条件/动作结果 → 任务安全停止、重新规划、暂停或失败。以上事件必须有时间戳与相互对应的 Task/checkpoint/action ID。
6. 新的实际世界观察必须证明相关状态确实发生变化，且变化后没有继续对不满足前置条件的目标盲目执行。记录后续步骤是否被阻止，以及是否出现未授权动作/重复非幂等动作。
7. 若选择在异步 pickup 动作运行中改变地形，必须证明该动作在改变前已经开始运行，并记录改变发生时仍处于 `RUNNING/WAITING_ACTION`。不能把改变前、确认前的 `/setblock` 事件算作符合门禁。
8. 之前出现过 60s 确认 TTL 与 Computer Use 延迟不匹配。如果确实需要临时覆盖 TTL，必须在测试报告中记录原始配置、临时差异、使用范围与还原证明；测试完毕恢复原配置，并确认 `git status` 不残留临时配置或脚本。不得把临时 TTL 覆盖当成生产修复。
9. 证据至少包含：第二客户端操作时间/命令、初始和变更后世界快照、真实 QQ 消息、Task ID、plan hash、每步 checkpoint、关键 bot/ActionRuntime 日志、最终任务状态和“未执行的后续步骤”证明。

### 4.3 C 项验收判据

只有满足真实时间顺序和状态变化关联时才可标 `PASS`。如果实际变化没有影响下一步骤、坐标不匹配、缺乏时间戳或只能证明“之后碰巧超时”，必须继续标 `SKIPPED/BLOCKED`，说明缺少的因果证据并重做；不允许用单测、结构性断言或原有日志代替真实 C。

## 5. 文档、配置和证据一致性

更新 `docs/MINECRAFT_PHASE7D.md`、`CHANGELOG.md`、相关测试文档和整改记录：

- 将前一轮 Real Java C 标为“第一次尝试未满足运行中世界变化的时间顺序要求”，并记录本轮真实复验结果。
- 如果本轮 C 未满足判据，维持 `SKIPPED/BLOCKED`，不得写“无 SKIPPED”。
- 统一最终镜像 commit 和实际修改代码的 commit，写清两者的关系。
- 区分源码审计、自动化测试、真实 SQLite 测试、真实 Java/QQ 取证、CI 记录；不把一种证据冒充成另一种。
- 移除本轮临时脚本、临时日志注入或测试专用权限变更；不能删除项目原有的用户数据或既有日志。
- 保持数据库迁移版本 33，不新增迁移，除非证明确实不可避免；如果新增，先解释原因、兼容性与恢复策略，且不得清库重建。

## 6. 测试与质量门禁

先运行针对性测试，再运行完整测试矩阵：

1. `tests/test_agent_plan.py`
2. `tests/test_agent_plan_follow.py`
3. `tests/test_agent_plan_negative.py`
4. 新增的跨 session 预留竞态测试、SQLite payload/CAS 持久化测试、崩溃恢复测试与入口确认门测试
5. 完整 pytest
6. Ruff check + format check
7. mypy / Python 类型检查
8. WebUI Vitest、vue-tsc/typecheck 与 build
9. Node 测试和现有 E2E
10. GitHub Actions CI

对比阶段前后的以下不变量，必须给出实际来源（注册表/配置/测试/提交差异），不能只写“未改”：

| 不变量 | 期望结果 |
|---|---|
| Minecraft 注册工具数 | 19 → 19 |
| 新增 Action | 0 |
| TaskRuntime 状态新增 | 0 |
| `allow_medium` | 始终 `false` |
| Policy/确认语义 | 不变 |
| 数据库迁移 | 保持 33（除非有充分理由并单独说明） |
| AgentPlan 执行状态 | 不复制 TaskRuntime 运行状态 |
| 跟随任务缺少身份/计划关联 | fail-closed，不能派发 follow |
| 同请求跨 session 并发跟随 | 至多一个关联 Task |
| LIFE 过期或并发批准 | 过期不建 Task，同一计划至多一个 Task |
| SQLite CAS 与 payload | 状态一致，崩溃可恢复 |

CI 必须提供本次最终代码 commit 对应的可查验工作流链接或状态记录。如果当前连接无法查询该状态，要在报告里如实说明，不能直接宣称“CI success 已独立验证”。

## 7. 最终交付报告格式

按以下结构交付，不要只给一句 PASS：

1. **基线和最终提交：** 完整 SHA、分支、工作树状态、与 `08a5013` 的提交关系。
2. **整改摘要：** 每个问题的根因、修改文件、调用链变化及为何未改变安全策略。
3. **跨 session 竞态证据：** 控制时序的方法、两次请求 session、Task 数量、计划/任务关联及动作派发断言。
4. **SQLite 持久化证据：** 真实 SQLite 查询结果（独立状态列、JSON payload、`get()` 对象）、崩溃模拟与重复恢复结果。
5. **身份复核证据：** 缺计划、读失败、撤销/冲突/缺失、换号、跨服务器、username 不匹配、取消失败；每项是否阻止 `confirm_and_start()` 与 follow 派发。
6. **Real Java C：** 严格按时间排序的表格，包含确认时刻、第一步成功、外部世界变化、下一步依赖与结果、最终状态、Task/checkpoint/action ID、前后世界观察和所有已知失败尝试。
7. **自动化测试：** 各测试命令、通过/失败数量、失败测试与修复结果，不要省略失败尝试。
8. **CI：** 本次最终提交的 URL、状态、工作流/job 结果。
9. **安全不变量对比：** 工具数、Action 数、TaskRuntime 状态、`allow_medium`、Policy、migration 的前后证据。
10. **BLOCKED/SKIPPED/限制：** 明确列出所有未完成项；有任何强制项不满足则总体不能 PASS。
11. **环境还原：** 原始配置 vs 临时配置、恢复证明、无临时文件/修改残留。

## 8. 最终裁定规则

只有同时满足以下条件，才允许报告 `Phase 7D.2 = PASS`：

- 不同 QQ session 的并发跟随请求已被实测证明至多一个 Task；
- AgentPlan 与 Task 的关联在并发、存储失败与崩溃恢复下保持一致；
- SQLite CAS 同步更新独立列与 JSON payload，真实数据库读写与恢复测试通过；
- 跟随 Task 缺少 AgentPlan/可信身份时会否决确认，并按既有安全路径尝试取消；取消失败也不会放行；
- LIFE 过期及并发批准的原有门禁继续通过；
- Real Java C 已严格按“任务实际开始 → 至少一步成功 → 外部真实世界变化 → 下一步骤的可验证安全响应”的顺序完成；
- 所有原有安全不变量保持不变；
- 自动化回归与可查验 CI 证据满足要求；
- 最终工作树干净、无临时测试配置或脚本残留。

任一强制条件未完成，最终状态必须为 `BLOCKED` 或注明具体 `SKIPPED`，并说明下一项可复现工作。不要提前开启 Phase 7E。