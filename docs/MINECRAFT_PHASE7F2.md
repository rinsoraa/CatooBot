# CatooBot Phase 7F.2 — Exploration Continuity & LifeIntent Feedback（探索连续性与生活意图反馈）

> 状态：**代码 / 回归 / 数据库幂等 / 真机门禁全部通过并经真实服务器收口**。
> 基线：`14409677967d2ec7dba01e1ac2d27bf3189adcdf`（Phase 7F.1）。
> 本文件记录：运行环境审计（§1）、调用链与最小设计（§2–§3）、实现与规则（§4–§6）、
> 验收矩阵与门禁（§7–§8）、真实服务器结果（§9）、交付与限制（§10–§11）。
>
> **阶段唯一目标**：把 7F.1 的**真实探索结果**接成**可信、可追溯、fail-closed** 的反馈闭环 ——
> 真实终态 → 结构化探索结果投影 → 真实记忆桥回流（经历 + **有证据**的新事实）→
> 既有 InitiativeGate / 候选生成 → LifeIntent → TaskProposal → AgentPlan → 两道门（批准 / 确认）。
> **反馈链绝不自行获得执行权限**：没有独立验证过的事实不生成"继续探索"意图；批准前不建任何 Task，
> 批准后也只停在**第二道确认门**。
>
> **执行层永远是既有链路**：`LifeIntent → TaskProposal → AgentPlan →（批准 / 确认）→ TaskRuntime → Policy
> → AgentBridge → ActionRuntime → MinecraftService`。反馈回路**没有**任何独立的执行入口。

---

## 1. 运行环境审计（只读，不动生产库）

| 项 | 结论 |
| --- | --- |
| 权威源码 | `E:\WorkSpace ZCode\CatooBot`（含密钥，**非** git 仓库） |
| git 镜像 / 发布源 | `E:\WorkSpace ZCode\CatooBot_github`（origin = `https://github.com/rinsoraa/CatooBot.git`） |
| 发布前备份 | 提交前将改动备份至 `E:\WorkSpace ZCode\CatooBot_github`（镜像即备份源） |
| 基线 | `HEAD = origin/main = 14409677967d2ec7dba01e1ac2d27bf3189adcdf`（Phase 7F.1，CI 绿） |
| 生产库 | `E:\WorkSpace ZCode\CatooBot\data\catoobot.db`，migration **36** |
| 迁移脚本 | `scripts/migrate_db.py --db <path> --json`（**先备份副本**再演练） |
| 迁移演练 | v36 → v36 **幂等**，**无新表**、无数据丢失（`lost_rows {}`） |
| 运行时 | `.venv\Scripts\python.exe`；Minecraft Fabric 1.21.1 服务器 `127.0.0.1:25565`（online-mode=false） |
| 账号 | offline，username = `Catodayo` |

> 只有代码、运行进程、数据库身份都确认后，才给出生产验收结论；无法确定的一律记为前置阻塞项，
> **不伪造**生产结论。

---

## 2. 实际调用链（以当前源码为准）

```
[TaskRuntime]  _finish（真实世界验证到达，见 7F.1）
  → _publish("task.succeeded"/..., payload)     # 既有终态事件（唯一发布点）
      → Bot._remember_task_outcome / MinecraftMemoryBridge.on_task_finished(record)
          _project_exploration(record)
            ARRIVED  → 写 EVENT 经历      extra.actionable=False
            NEW_FACT → 写 LOCATION 事实   extra.actionable=True + exploration_outcome
          （store 去重；同一终态重复发布不重复写）
  ↓（读取侧）
[InitiativeContextAdapter.collect]  _memories(作用域优先)
  → MemorySignal(actionable=?, exploration_outcome=?, provenance/evidence/domain)
[propose_intents]  _memory_intents
  actionable explore fact → MINECRAFT_INTEREST(semantic_key=explore_fact, tags∋exploration_feedback)
  explore-shaped memory    → MINECRAFT_INTEREST
  keyword EXPLORATION      → EXPLORATION（actionable=False 的到达记忆被跳过）
  → InitiativeGate / 冷却 / 频率 / 指纹去重（既有抑制，一字未改）
  ↓
[TaskProposalService.propose_from_intent]  → TaskProposal（source=LIFE）
  ↓
[AgentPlanService.plan_from_proposal]      → AgentPlan（READY_FOR_APPROVAL，**不建 Task**）
  ↓（第一道门：批准）→ 只建 PENDING_CONFIRMATION Task（**不执行**）
  ↓（第二道门：确认）→ TaskRuntime → 既有执行链
```

关键接缝（**不新建**）：

* `TaskRuntime._publish` 是终态事件的**唯一**发布点；反馈回路复用既有的 `publish` 钩子，
  与其它任务同一套（**不另立第二套记忆系统**）。
* `MinecraftMemoryBridge.on_task_finished` 是 Minecraft 终态回流的**唯一**入口；探索只新增
  一个 `_project_exploration` 投影分支。
* `InitiativeContextAdapter` / `propose_intents` / `InitiativeGate` 是候选生成的**唯一**通道；
  探索只新增一个确定性的信号消费分支。
* `TaskProposalService` / `AgentPlanService` / 两道门是落地与执行的**唯一**通道，反馈回路没有旁路。

---

## 3. 最小设计

### 3.1 结构化探索结果（`app/tasks/exploration.py`）

以**真实持久化 TaskRecord + 运行时验证结果**为依据，纯函数投影探索结果：

| 结果 | 判定 |
| --- | --- |
| `ARRIVED_VERIFIED` | `SUCCEEDED` 且 `verification.{checked,ok,position_within}` 齐备，且**没有**可确认的新事实 |
| `ARRIVED_NO_NEW_FACT` | 同上，显式表示"已确认到达但无新信息"（驱动"不重复生成意图"） |
| `NEW_FACTS_VERIFIED` | 已确认到达，且存在**由真实 SAFE 观察支持**的新事实（封顶 `MAX_NEW_FACTS = 5`） |
| `BLOCKED` | 目标不可达或前置条件不满足 |
| `CANCELLED_OR_INTERRUPTED` | 取消 / 中断 / 未完成，或**验证证据缺失** |

* `is_exploration_task` 用**结构**判定（`expected_final_state.position_within` 存在），不靠关键词或旗标。
* `arrival_fact` 产生的到达事实 `extra.actionable=False`；`exploration_facts` 产生的有证据新事实
  `extra.actionable=True`。
* **绝不**把动作返回成功 / 计划批准 / 任务建立当成真实探索完成；证据不足 → `CANCELLED_OR_INTERRUPTED`。

### 3.2 记忆桥探索投影（`app/integrations/minecraft/memory_bridge.py`）

复用现有 `MinecraftMemoryBridge` / `MinecraftMemoryStore`（**不新增第二套记忆系统、无新表**）：

* 到达 → 写一条 `EVENT` 经历（"她走到过这里"，`actionable=False`）。
* 每条**有证据的新事实** → 写一条 `LOCATION` 事实（`actionable=True` + `exploration_outcome`）。
* 复用 store 的来源隔离 / 服务器作用域 / 新鲜度 / 去重 / 冲突规则；旧观察**不**自动覆盖更新的世界事实。

### 3.3 LifeIntent 回流（`app/initiative/candidates.py` + `adapters.py`）

* `MemorySignal` 增加 `actionable` / `exploration_outcome` 字段。
* `_memory_intents` 的**确定性**优先级：actionable 探索事实 → explore 形状的记忆 → 关键词 `EXPLORATION`。
* `actionable=False` 的到达经历被**明确跳过**（普通到达不反复生成"继续探索"意图，符合任务书 §4.3）。
* 冷却 / 频率上限 / 指纹去重 / 恢复静默期 / 抑制规则**一字未改**；`InitiativeGate` 不可绕过。
* 后续意图**仍只是想法**，不能直接调用 TaskRuntime / ActionRuntime / MinecraftService。

### 3.4 提案与授权边界

`LifeIntent → TaskProposal → AgentPlan → 批准 → 待确认 Task → 确认执行 → TaskRuntime`：

* LIFE 计划**批准之前不建执行任务**；
* 计划**批准后仍需第二道确认**才能执行；
* 用户拒绝 / 取消 / 身份缺失 / 世界离线 / 能力不足时如实结束或阻止；
* 维持现有 `Policy` / 授权 / 风险 / 超时 / 停止 / 暂停 / 恢复语义；
* 不改 `allow_medium=false`，不新增工具 / 动作 / 隐式授权方式。

---

## 4. 实现清单（修改文件）

* 新增：`app/tasks/exploration.py`、`tests/test_exploration_feedback.py`、
  `scripts/feedback_smoke_real.py`、本文件 `docs/MINECRAFT_PHASE7F2.md`、
  `docs/specs/CatooBot Phase 7F.2 — Exploration Continuity & LifeIntent Feedback.md`（任务书副本）。
* 修改：`app/initiative/candidates.py`、`app/initiative/adapters.py`、
  `app/integrations/minecraft/memory_bridge.py`、`app/tasks/planner.py`、`app/core/bot.py`、
  `CHANGELOG.md`、`docs/README.md`。**无新表、无新迁移**（最高仍 **36**）。

---

## 5. 幂等与恢复

* 同一探索终态**重复发布**：记忆桥 store 去重，不重复写新事实、不反复生成意图。
* 重启后：历史事实与对应任务的**来源仍可追溯**（`task_id` / 服务器作用域 / 时间）。
* 已被抑制 / 解决 / 过期的 LifeIntent **不因重放**再次变为有效（既有抑制规则）。
* 更新失败时保留原始经历，生活规划正常降级，**不编造**新的探索结果。

---

## 6. 安全边界（一字未松）

`allow_medium=false`、**19** 个注册工具、`ACTION_RISK`、两道门（批准计划 + 确认执行）、身份校验、
`TaskAuthorization` / `TaskStepAuthorization`、pause / resume / cancel / expire 与停止语义**全部不变**。
反馈回路**没有**任何绕过 Policy、确认门、授权或停止机制的路径。

---

## 7. 验收矩阵（任务书 §五 1–10）

| # | 验收点 | 证据 |
| --- | --- | --- |
| 1 | 到达成功但无新事实：保存结果，不制造重复 LifeIntent | `test_exploration_feedback.py` 到达无新事实用例 |
| 2 | 到达成功且有可信新事实：进入记忆与意图候选管线 | 有新事实入管线用例 + §9 真机 |
| 3 | 目标不可达：不产生正向探索事实 | 不可达用例（BLOCKED） |
| 4 | 取消 / 超时：记录实际结果，不产生虚假成功经历 | 取消 / 超时用例 |
| 5 | 观察缺失 / 离线 / 陈旧 / 矛盾：不晋升为确认事实 | fail-closed 用例 |
| 6 | 重复终态 / 重放 / 重启：记忆与意图幂等 | 幂等用例 |
| 7 | LifeIntent → TaskProposal → AgentPlan 真实路径可追溯 | 可追溯用例 + §9 真机 |
| 8 | 未批准 / 未确认时不执行任何世界动作 | 两道门用例 + §9 真机 |
| 9 | 新候选进入现有生活规划，旧虚拟活动规则保持有效 | 生活规划接入用例 |
| 10 | InMemory 与 SQLite 行为一致，升级幂等 | §1 迁移演练 + 测试内存 store |

---

## 8. 本轮实际执行的门禁（串行）

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| ruff check | `ruff check .` | All passed |
| ruff format | `ruff format --check .` | 641 files already formatted |
| mypy | `mypy app` | Success（336 files） |
| 全量 pytest | `pytest tests -q` | **3638 passed**（基线 3608 + 30） |
| WebUI typecheck / Vitest / build / Playwright | `npm run typecheck` / `npm test` / `npm run build` / `npm run test:e2e` | 绿（528 + 7） |
| Minecraft Node 单测 / E2E | `minecraft_runtime npm test` | ALL CHECKS PASSED |
| 迁移演练 | `scripts/migrate_db.py --db <backup> --json` | v36→v36 幂等，无新表，`lost_rows {}` |
| 真实服务器（7F.2 反馈链） | `scripts/feedback_smoke_real.py` | **PASS**（见 §9） |
| 真实服务器（7F.1 回归） | `scripts/explore_smoke_real.py --sections all` | **PASS**（见 §9） |

> 重型门禁**串行**运行，不放宽既有断言。

---

## 9. 真实 Java 服务器门禁 —— **PASS**

### 9.1 Phase 7F.2 反馈链（`scripts/feedback_smoke_real.py`）

Fabric 1.21.1，offline `Catodayo`，先停 CatooBot 以独占 runtime。脚本把**真实** `MinecraftMemoryBridge`
接到 `TaskRuntime.publish` 钩子（与 `Bot._remember_task_outcome` 同一条集成链），随后走**真实**
`InitiativeContextAdapter` / `propose_intents` / `TaskProposalService` / `AgentPlanService`：

| 步骤 | 结果 |
| --- | --- |
| 真实规划 → `READY_FOR_APPROVAL`，计划 = `move_to + look_at + world` | ✓ |
| 真机执行 → 真世界到达复核 | `SUCCEEDED`；`verification.ok=True` |
| 真记忆桥写入 | 3 条（1 actionable `LOCATION` 新事实 + 1 `EVENT` 到达 + 1 `TASK`） |
| 结构化结果 | `NEW_FACTS_VERIFIED`（actionable facts=1） |
| 真候选生成 | 1 条带 `exploration_feedback` 的 `MINECRAFT_INTEREST` |
| 真 TaskProposal | `created`，来源 `LIFE`，可追溯到 LifeIntent（`intent_id` 一致） |
| 真 AgentPlan | `READY_FOR_APPROVAL`，规划阶段 `active` 未变（**不建 Task**） |
| 第一道门：批准 | 停在 `PENDING_CONFIRMATION`（**不执行**） |
| 第二道门：未确认 | 任务仍是 `PENDING_CONFIRMATION`，角色**零真实动作** |

**脚本输出末尾 `REAL SERVER: PASS`。**

### 9.2 Phase 7F.1 回归（`scripts/explore_smoke_real.py --sections all`）

| 段 | 场景 | 结果 |
| --- | --- | --- |
| `positive` | 真实规划 → 执行 → 真世界验证到达 | `SUCCEEDED`；`verification.ok=True`；距目标 1.58 格（半径 2.0）；真实位移 8.1 格 |
| `blocked` | 目标不可达 | `PAUSED`（`ACTION_FAILED`）；**绝不 SUCCEEDED** |
| `cancel` | 导航中取消 | `CANCELLED` + 经 `minecraft_stop` 真停 + 不再产生新动作 |

**PASS，无 SKIPPED 项。**

---

## 10. 交付清单

* 见 §4 修改文件清单。
* **迁移清单**：**无新表、无新迁移**（最高仍 **36**）。
* **敏感文件核对**：未改动 `.env` / `minecraft_runtime/auth.json` / Character Bible / 密钥。
* commit SHA / 镜像一致性 / CI 结果见 §12。

---

## 11. 残余限制与后续建议

* 探索仍是**确定性单段**闭环（一个 `move_to` + 一个 `look_at` + 一次 `world` 读），**不是**无界 LLM 循环，
  也不含多段路线或世界修改。
* 新事实质量受感知层 `points_of_interest` / `terrain` 事实质量约束；事实缺失时退化为罗盘兜底方向。
* 后续（7F.3+）可在**不松安全边界**的前提下扩展：多段有界路线、探索中遇到新事实的反应、
  以及让探索经历更细粒度地参与生活规划。

---

## 12. 交付与远端一致性

（提交后回填：commit SHA、镜像 `git status` 干净、远端一致性、CI 结论。）