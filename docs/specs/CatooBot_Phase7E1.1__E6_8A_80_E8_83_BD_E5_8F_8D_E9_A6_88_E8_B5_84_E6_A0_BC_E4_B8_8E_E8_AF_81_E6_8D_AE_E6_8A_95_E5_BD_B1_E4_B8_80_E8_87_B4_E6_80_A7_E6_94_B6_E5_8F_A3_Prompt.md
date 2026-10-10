# CatooBot Phase 7E.1.1 — Skill Feedback Eligibility & Evidence Projection Consistency

## 0. 任务定位

本任务是 Phase 7E.1 的窄范围正确性收尾，不是 Phase 7F，不是重新设计技能系统，也不是修复 Minecraft `dig` 的实际执行语义。

最新已知基线：`c78ea641dbe51e4d189bb07c1893350a1d2ad3cb`。开工前必须先读取远端 `main`，确认有无后续提交；如有，核对差异后以真实最新基线为准。

Phase 7E.1 的两个主缺口已修复：`claim_evidence()` 将证据认领、计数和状态推导收进存储事务；技能复用归因从 `(objective, plan_hash)` 派生键改为 `task_id` 唯一绑定。本任务只封闭复核发现的两处残余问题：

1. **未真正执行的技能任务也可能作为反例惩罚技能。** 当前 `SkillService._record_reuse()` 将除 `SUCCEEDED` 外的所有任务终态统一当成 `COUNTEREXAMPLE`。技能绑定在任务进入 `PENDING_CONFIRMATION` 后就已建立，因此用户尚未确认时的 `CANCELLED` / `EXPIRED`，或任务还没开始任何步骤就失败/取消，都可能增加 `failure_count`，把好技能转为 `STALE`，甚至 `INVALIDATED`。现有 `TestFeedbackLoop` 通过绑定直接构造终态任务，但未证明任务实际尝试过技能步骤；其中 CANCELLED 也使用默认所有步骤 `SUCCEEDED` 的夹具，不能覆盖“未执行就取消”。
2. **SQLite 证据表的列与 JSON payload 不一致。** `SqliteSkillStore.claim_evidence()` 先把不含最终 `skill_id` 的 `evidence.to_payload()` 插入 `procedural_skill_evidence.payload`，随后只执行 `UPDATE ... SET skill_id = ?` 更新独立列，没有同步更新 JSON payload。`_from_evidence_row()` / `recent_evidence()` 又只读取 `payload`，所以重启后证据视图中的 `SkillEvidence.skill_id` 可能为空，与数据库列不一致；重复认领分支也从旧 payload 读取。计数目前按列聚合，故该缺陷主要破坏证据归属可读性/审计一致性，但必须修复。

## 1. 第一部分：定义“反例”资格，而不是只看任务终态

审计并修改：

- `app/tasks/skill_service.py`：`_on_task_finished()`、`_record_reuse()` 和所需的纯判定 helper。
- `app/tasks/skill.py`：如有必要，为“是否发生可归因执行”定义纯数据判断，不添加执行能力。
- `app/tasks/skill_store.py`：保持绑定消费与证据认领、状态派生的一次事务语义。
- `tests/test_skill_service.py`、`tests/test_skill_closure.py` 与相关测试夹具。

### 1.1 必须贯彻的语义

- `PENDING_CONFIRMATION` 阶段用户取消或确认过期，不能说明技能方法失败；只能作为不计分的终止/拒绝证据（例如 `REJECTED` + 稳定 reason code `skill_task_not_started`）。
- 任务尚未启动任何步骤就 `FAILED` / `CANCELLED` / `EXPIRED`，不能作为 `COUNTEREXAMPLE`。
- 用户主动中止任务不等价于技能方法失败。对于开始执行后被用户取消的任务，默认也不把它当成方法反例；除非现有持久化证据能明确证明是方法步骤失败，并且不是用户中断、确认过期、授权失效或外部环境不确定导致。
- `EXPIRED` 本身不足以证明方法失败：区分尚未启动就过期、执行中超时和有具体步骤失败证据的情况；没有可归因的失败证据时不得惩罚技能。
- 只有符合既有、可审计的执行证据规则的任务失败，才可记为 `COUNTEREXAMPLE`。不得从自由文本、任务目标文字或模型自述推断失败归因。
- `SUCCEEDED` 不能仅凭 Task 顶层状态而跳过现有资格与最终计划检查。确认当前最终计划确实完成，关键步骤有真实终态，预期结果通过运行时已有后置验证；若无法核实则不得新增正向证据。
- 不合格的反馈仍应以幂等方式消费或终止对应 binding，避免重复终态事件稍后再次尝试计分；但它不得增加技能正向/反例计数或改变 ACTIVE/STALE/INVALIDATED 状态。
- `INVALIDATED` / `REJECTED` 等技能终态不可复活。保留现有 `derive_skill_state()`、唯一证据约束以及“证据认领 + 绑定消费 + 计数 + 状态”的事务边界，不要把正确性退回到服务层先查再写。

### 1.2 强制负向测试

使用真实 `TaskRecord` / `TaskStep` 结构和内存 + SQLite 两类 store，至少新增：

1. ACTIVE 技能绑定一个 `PENDING_CONFIRMATION` 任务后取消：记录可审计但 `failure_count` 不变、状态仍 ACTIVE。
2. ACTIVE 技能绑定一个未启动任何步骤即 `EXPIRED` 的任务：不计反例。
3. ACTIVE 技能绑定一个未启动任何步骤即 `FAILED` 的任务：不计反例。
4. 启动前被取消后重复投递终态事件：只消费一次绑定，不重复计数。
5. 任务已开始、存在可信失败步骤且失败确属执行结果：按既定规则记录反例，证明安全性不是把所有失败都吞掉。
6. 运行中被用户取消，即便已有部分步骤成功，也不能仅凭 `CANCELLED` 推断技能方法失败。
7. 证据无法判定是否已执行、步骤状态缺失或 checkpoint 不完整：保守不计正向/反例，并留下原因码。
8. 原有“执行技能后真实失败 → STALE；达到反例阈值 → INVALIDATED”行为仍被覆盖：只有符合新资格规则的反例才推进状态。
9. SQLite 真库与 InMemorySkillStore 对同一批执行记录给出一致的 verdict、计数与状态。

需要明确“方法反例”与“用户决定不继续”是不同事件语义。不得为通过测试而把所有 CANCELLED/EXPIRED 改为永远忽略；若某种超时或运行时失败应算反例，必须使用 TaskRecord 内已有的具体、可信证据定义，并补正反测试。

## 2. 第二部分：证据 `skill_id` 列与 payload 原子一致

修改 `SqliteSkillStore.claim_evidence()`：当事务确定证据所属 `skill_id` 后，必须在同一个事务里同时更新：

- `procedural_skill_evidence.skill_id`
- `procedural_skill_evidence.payload.skill_id`

重复证据的查询也必须返回与列一致的 `SkillEvidence`。不要通过只修改 API 展示层来掩盖数据库中列/payload 不一致。

因为 Phase 7E.1 已经产生了 payload 缺少 `skill_id` 的历史证据，本任务需要一个向前兼容的修复方式。优先新增迁移 **36**，根据 `skill_id` 列为已有有效 JSON payload 回填 `skill_id`；保留证据行、时间、verdict、reason、task_id 等所有既有字段，不删除或重建证据。迁移必须幂等、可从 v35 数据库升级，并且不改写无归属（`skill_id=''`）的 AMBIGUOUS/REJECTED 证据。若选择运行时兼容读而非迁移，必须证明数据库数据和读取投影两者都一致且历史数据可审计；不得仅口头称已修复。

### 2.1 强制测试

1. 真 SQLite 中新增一个正向证据，`SELECT skill_id, payload ...` 得到的列与 JSON 中 `skill_id` 完全一致。
2. 重启 `SqliteSkillStore` 后，`recent_evidence(skill_id=...)` 返回的每条 `SkillEvidence.skill_id` 正确。
3. 重复认领返回的证据归属也正确，且计数不变。
4. 无归属的歧义/拒绝证据仍保持空 `skill_id`，不被错误附到某条技能。
5. v35 → v36 升级保留所有旧证据行和既有非技能数据，迁移重复执行幂等；旧证据 payload 被修复后与 `skill_id` 列一致。
6. InMemorySkillStore 与 SQLite store 的公开证据读取契约一致。

## 3. 明确不在本任务范围内

- 不新增 Minecraft 工具、ActionRuntime 动作、TaskRuntime 状态；不改变 `allow_medium=false`。
- 不改变 QQ USER / LIFE 的批准与确认语义，不新增自动确认、不创建新的执行入口。
- 不修改 `minecraft_runtime/runtime.js` 的 `dig()` 执行语义；7D §10.4 的挖掘归因缺陷仍是独立 follow-up。
- 不扩展到 follow / building / crafting 等新技能类别；本任务只修学习反馈资格与证据投影一致性。
- 不重建通用记忆系统或第二套 TaskRuntime；不删除历史证据，不 force-push。
- 本轮不要求为验证存储/反馈逻辑执行真实服务器世界修改。真实 Java 门禁如未执行必须标记 SKIPPED，不能写 PASS。

## 4. 执行流程

1. 确认 `origin/main` 基线、工作树状态、迁移当前最高版本和既有测试结果。
2. 用最小复现测试先分别证明两个问题：未执行任务被错误计为反例；SQLite evidence column/payload 不一致。
3. 先写失败测试，再修复；不得删除或放宽已有 7E/7E.1 测试来让新测试通过。
4. 审计技能绑定在 `TaskTurnHandler._create`、`AgentPlanService.approve`、`plan_follow_from_user` 三条真实入口的所有终态语义；确认确认前/批准前退出不会误惩罚技能。
5. 执行完整门禁，保持串行：
   - `ruff check .`
   - `ruff format --check .`
   - `mypy app`
   - `pytest tests -q`
   - WebUI typecheck / Vitest / build / browser E2E
   - Minecraft runtime Node tests + flying-squid E2E（遵守项目既有纪律，不与全量 pytest 并发）
   - schema migration / SQLite idempotency / v35 → v36 upgrade / legacy payload repair tests
6. 更新 `docs/MINECRAFT_PHASE7E.md`、`docs/README.md`、`CHANGELOG.md`：记录此收尾阶段、迁移 36（如新增）、真实发现、测试及限制。修正 Phase 7E 文档顶部仍标注“实施中”的状态，按真实验收情况标为相应状态，不得写成已通过真实 Java 门禁。
7. 按项目既有源码工作区与干净 GitHub 镜像流程同步，只提交项目文件；不要触碰 `config/`、`config/overrides.yaml`、`.env`、角色 Bible 或密钥。
8. 推送后核对最终 commit SHA 与该 commit 的 GitHub Actions 两个 job；CI 的重跑只能如实记录，不能替代语义审查。

## 5. 完成标准

只有同时满足以下条件才能报 PASS：

- 未启动/用户主动取消/确认过期不会惩罚技能；真正可归因的执行失败仍能触发反例路径。
- 绑定消费、证据 verdict、计数与状态更新保持幂等与原子性。
- SQLite 的证据列与 payload 一致；历史记录经迁移或等价兼容路径恢复一致。
- 重启后 API/只读视图能够显示正确的技能归属和原因；无归属证据仍诚实为空。
- 所有原有 Phase 7E/7E.1 测试、两类 store 一致性测试及全量 CI 通过。
- 没有改变任何执行能力、授权路径、风险级别或 7D dig 运行时语义。

最终报告必须包括：`PASS / BLOCKED / SKIPPED`；两个问题的最小复现证据；修改文件与迁移；新增和全量测试数；并发/重复/重启证据；GitHub commit SHA 和 CI 链接；真实 Java 门禁状态；未解决风险。若任一条件未满足，准确报告 BLOCKED，不得以绿色 CI 代替。
