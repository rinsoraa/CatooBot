# CatooBot Phase 7E.1 — Skill Evidence Idempotency & Usage Attribution Closure

> 类型：Phase 7E correctness follow-up（不是 Phase 7F，不扩展技能种类或自主执行能力）  
> 当前基线：`958143eb4a39a662fd71be330dfe87ebca4be7e2`  
> 仓库：`rinsoraa/CatooBot`  
> 目标：关闭技能学习的并发幂等与技能复用归因边界；最小改动，不重写 Phase 7E。

## 0. 开始前：确认真实基线

1. 检查 `origin/main` 是否仍为 `958143eb4a39a662fd71be330dfe87ebca4be7e2`。若已前进，先阅读新增提交与差异，使用实际最新基线并在报告说明。
2. 阅读并遵守：
   - `docs/MINECRAFT_PHASE7E.md`
   - `docs/MINECRAFT_PHASE7D.md` §10.4、§11
   - `app/tasks/skill_service.py`
   - `app/tasks/skill_store.py`
   - `app/tasks/skill.py`
   - `app/tasks/skill_learning.py`
   - `app/tasks/models.py` 中 `TaskPlan.to_payload()` / `from_payload()` / `plan_hash`
   - `app/tasks/runtime.py` 与 `TaskStore` 的 `TaskRecord` 持久化路径
   - `app/tasks/turn.py`、`app/tasks/agent_service.py`、`app/tasks/agent_plan.py`
   - `app/core/bot.py` 中 `_remember_task_outcome()` / `_publish_task_event()`
   - `tests/test_skill_service.py`、`tests/test_skill_wiring.py` 与数据库迁移测试。
3. 先写一份简短的根因分析和最小修复设计，然后立即实施，不要只交审计报告。

## 1. 已知问题 A：成功证据与技能计数不是一个原子操作

当前学习顺序大致为：

```text
查询 evidence_for_task()
→ 执行异步后置条件检查
→ _upsert_positive() 创建/递增 skill.success_count / 可能晋升 ACTIVE
→ add_evidence() 使用唯一索引落证据
```

同一个持久化 TaskRecord 若通过两个并发终态回调进入学习链，两个调用可能都在证据落库前通过重复检查。其后即使证据唯一约束只允许一条记录成功，两个调用仍可能先后增加 `success_count`，使只有一条独立成功证据的技能错误晋升 `ACTIVE`。当前 `test_duplicate_final_event_does_not_double_count` 是顺序调用，并不能证明并发时安全。

### 必须达成

1. 将“同一任务证据的唯一认领/写入”与“技能成功计数和状态晋升”设计为一个有事务保证的幂等操作；或使用经证明等价的存储层原子协议。
2. 数据库唯一索引不能只保护 evidence 行，而计数仍先行或独立更新。`success_count`、`failure_count`、`ambiguous_count`、技能状态与对应证据必须保持一致。
3. 同一 `subject_key + task_id + plan_version + plan_hash` 最多贡献一次计数；并发的同一任务只认领一次。
4. 两个不同 task_id、都满足资格门的独立成功任务仍应累计两条证据并按阈值晋升。
5. InMemorySkillStore 与 SqliteSkillStore 行为保持一致。不要只靠单进程 asyncio.Lock 声称跨恢复/存储层正确；若使用锁，必须说明其覆盖边界，SQLite 持久化路径仍需原子化。

### 必须新增的测试

- 使用 `asyncio.gather()` 对同一个 TaskRecord 并发调用 `on_task_finished()`；通过 barrier/fake postcondition 人为制造两个调用同时通过初始读取的竞争窗口。
- 至少在 `SqliteSkillStore` 真 SQLite 路径复现并通过；内存 store 也要有等价测试。
- 同一个 task 两个并发回调后必须满足：只有一条证据、`success_count == 1`、状态仍为 `CANDIDATE`，不能晋升为 `ACTIVE`。
- 两个不同 task_id 的真实合格记录并发学习后，必须恰好两条独立证据，`success_count == 2`，状态 `ACTIVE`；不得发生 lost update。
- 并发歧义/拒绝证据不能与正向证据互相覆盖，也不能因重复回调重复累加 ambiguous/failure 计数。
- 保留现有顺序重复、重启重放、跨版本和终态不可复活测试。

## 2. 已知问题 B：技能使用链在 Task 创建前登记，归因键不足以证明真实复用

当前 `SkillService.materialize()` 会在计划候选阶段调用 `note_usage(objective, plan_hash)`，也就是 TaskRuntime 任务尚未成功建立时就持久化技能关联。`procedural_skill_usage` 当前用 `(objective, plan_hash)` 的派生键关联 skill_id，但：

- 计划候选可能因为 `TaskBusy`、校验失败、过期、被拒绝或后续回退而没有实际成为该任务；
- `TaskPlan.hash_payload()` 有意不把 observations 放进 `plan_hash`；
- `TaskPlan.to_payload()` 当前也不序列化 `observations`，因此 `skill_reference` observation 不能被当作可靠的重启后归因依据；
- 一条未消费的旧使用记录可能把之后同目标、同计划哈希但实际走基规划器的 Task 错认成技能复用。其结果可能是无关任务使技能计数增长、变 STALE，或被错误 INVALIDATE。

### 必须达成

1. 技能反馈只能关联到**实际由该技能候选物化并成功建立的具体任务/已持久化计划链**，不能仅凭目标文本和 `plan_hash` 推断“用过技能”。
2. 在真实任务对象可用后建立 durable link，或采用具有唯一 correlation token、持久关联、过期/撤销和显式消费语义的等价方案。不得依赖进程内变量或 TaskPlan observations 会自动持久化的假设。
3. 该关联需要覆盖 QQ USER 资源任务与经 `AgentPlanService` 的候选路径：若任务先作为 LIFE AgentPlan 持久化、之后由批准动作建立 Task，技能关联必须能从原计划安全传递到真正建立的 Task。
4. Task 创建失败、`TaskBusy`、`validate_plan` 拒绝、计划过期/取消、技能候选降级回退等情况不得留下会误认未来其他 Task 的可生效悬空关联。
5. 如果为持久关联增加字段或迁移，保持向后兼容；不能让新元数据进入授权 hash、绕过 `validate_plan` 或赋予额外权限。只有经审计证明有必要时才新增迁移；若需要，升级必须幂等且保留 v34 旧数据。
6. 学习/使用归因出错时，最安全的结果应是“不对技能做反馈”，而不是猜测其来源。

### 必须新增的测试

- 技能候选成功物化，但随后任务因 busy/创建失败：之后用相同 objective 与相同 plan_hash 创建一个基规划器 Task，终态不得回流到先前技能。
- 技能候选成功物化并成功创建 Task：重启进程后，终态仍能准确回流到原 skill_id。
- 使用技能的 Task 与相同 plan hash 的非技能 Task 并存时，只有真实绑定的那一个产生技能反馈。
- LIFE 路径通过 `AgentPlanService` 保存候选、用户批准并创建任务后，技能关联仍准确；未批准、取消、过期、创建失败的计划不能留下有效任务绑定。
- 同一技能在多次任务中复用、同目标重复请求、存储恢复和重复终态回调均不互相串线。

## 3. 不得改变的既有边界

- 不新增 Minecraft 原子工具、ActionRuntime 动作或 TaskRuntime 状态。
- `allow_medium=false` 保持不变；不改任何风险级别或能力目录契约。
- 不改变 Phase 7D 的确认/批准/身份/取消/暂停/恢复/超时语义。
- 技能依旧只输出计划候选；真实执行继续经过既有 `validate_plan` 与 `TaskRuntime → Policy → AgentBridge → ActionRuntime → MinecraftService`。
- 不将 `CANDIDATE` 直接作为可复用技能；`ACTIVE` 仍必须有两条真正独立且合格的证据。
- `INVALIDATED`、`REJECTED` 等终态不可复活。
- 7D §10.4 的 dig 归因缺陷仍是独立 follow-up。本次不修运行时 dig 语义，不把它改号为 7E；继续确保无法可靠归因的样本不计正向技能证据。
- 不修改 `config/`、`config/overrides.yaml`、API key、`.env` 或角色 Bible。

## 4. 实施、文档与交付

1. 先修正根因，再编写并运行新测试；不能通过放宽既有断言来“修复”测试。
2. 对原有技能晋升、回流、SQLite 真库重启、迁移 34、USER/LIFE 授权链全部回归。
3. 如果新增迁移，更新数据库最高版本测试、v34 → 新版本升级测试与重复迁移幂等测试。
4. 更新 `docs/MINECRAFT_PHASE7E.md` 与 `CHANGELOG.md`，明确记录两个竞态/归因缺口、实现方案及证据。不要覆盖或删除旧审计记录。
5. 串行执行项目既有门禁：
   - `ruff check .`
   - `ruff format --check .`
   - `mypy app`
   - `pytest tests -q`
   - WebUI typecheck / Vitest / build / browser E2E
   - Minecraft runtime Node tests + E2E（遵守项目约定，不与全量 pytest 并发）
6. 按源码工作区/干净发布镜像流程发布并检查 `origin/main` 最终 SHA。核对 GitHub Actions 的两个 job 均 success，给出真实 run/job URL。
7. 最终报告按 `PASS / BLOCKED / SKIPPED` 列清：每个问题的根因、修复文件、原子性与 task-link 设计、并发测试如何制造竞争、全部测试数、迁移情况、CI 证据、已知限制。

## 5. 验收标准

只有全部满足以下条件才报告 PASS：

- 同一条任务证据在并发重复回调下永远最多计一次；不同 task 的两条独立成功仍能晋升。
- 每一条技能反馈都有耐久、精确、唯一的 task-to-skill 归属；候选失败/回退或同形普通任务不会污染旧技能。
- 重启后归属不丢失，重复终态事件不会重复反馈，状态/计数/证据没有不一致窗口。
- 所有既有安全边界、7E 资格门、迁移兼容性与完整项目 CI 保持通过。
- 未复核过的内容写 `SKIPPED` 或列为限制，不得用 CI 绿色替代语义正确性。

开始执行。不要先询问确认；先做基线核实与最小修复，再交付完整证据。
