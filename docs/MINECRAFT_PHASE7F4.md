# CatooBot Phase 7F.4 — Action Dispatch & Recovery Safety（派发与恢复安全）

> 基线：`4ce219d2acda2aa92486ac4ca88c9eb1227f7d9c`（Phase 7F.3）。
> 本文件记录：原始恢复缺口与每个崩溃窗口的处理策略（§1）、关联键与派发代次的定义（§2）、
> runtime 幂等/查询能力的**实测结论**（§3）、故障注入用例的原始证据（§4）、
> 门禁结果（§5）、CI/数据库/边界（§6）、失败与遗留风险（§7）。
>
> **这是恢复可靠性阶段**：不新增 Minecraft 能力、不扩大权限。
> **明确不宣称 exactly-once** —— 外部动作到底有没有发生，本阶段无法证明；
> 本阶段能保证的是：**绝不因为恢复而把同一个动作再派发一次**。

---

## 1. 原始恢复缺口与每个崩溃窗口的处理策略

### 1.1 缺口（改动前的真实行为）

派发顺序是**先调用、后落盘**：`_run_step` 把 `step.state = RUNNING` 只写在内存里、
`TASK_STEP_STARTED` 只 publish 不 save，然后才 `_invoke_step`（**外部副作用在这里发生**），
直到 `_settle` 才 `_save`。于是有两个独立缺口：

1. **派发窗口**：进程在"外部已接受动作"与"`_settle` 落盘 `action_id`"之间退出时，
   持久化记录里那一步仍是 `PENDING`、`pending_action_id` 为空。`recover()` 唯一的"曾经派发过"
   判据就是 `pending_action_id/pending_step_id`，因此这种情况既不置 `replan_required`、
   也不清授权；`resume()` 的三个守卫全部不成立 → **再次派发同一个动作**
   （而 Node runtime 是子进程，可能比 Python 活得更久）。
2. **响应丢失**：`_invoke_step` 的 `except` 把传输异常记成 `code="task.internal"`、
   步骤记成 `FAILED`，`step.action_id` 为空 → `resume()` 的"陈旧动作"守卫同样不成立 →
   **同样会被重新派发**，而外部可能已经在执行。

### 1.2 每个崩溃窗口的处理策略

| # | 崩溃时点 | 磁盘上的形态 | 处理策略 | 为什么不会重复派发 |
| --- | --- | --- | --- | --- |
| 1 | 写入派发意图**之前** | 该步 `dispatch_state == ""` | 恢复后可安全重来 | 意图落盘在**调用工具之前** → 磁盘上连意图都没有就**证明**外部没被调用 |
| 2 | 意图已落盘、请求尚未发出 | `INTENT`，无 `action_id` | 受控暂停 + 人工介入 | 与 #3 磁盘形态**无法区分** → 一律 fail-closed，绝不猜 |
| 3 | runtime 已接受、`action_id` 未落盘 | `INTENT`，无 `action_id` | 受控暂停 + 人工介入 | 同上：宁可停，也不重发一个**可能已经在跑**的动作 |
| 4 | `action_id` 已落盘、未进入稳定等待 | `ACCEPTED` + `pending_action_id` | 旧动作失效（`RUNTIME_RESTART`）+ 清授权 + 要求重新规划 | 既有语义（`pending` 判据）本就安全，本阶段用测试把它**锁住** |
| 5 | 外部动作已结束、终态未落盘 | 与 #4 相同 | 同 #4 | 磁盘形态相同 → 同样不重做；终态是否真的成功由**只读对账**回答 |
| 6 | 恢复扫描 / 重复事件投递 | — | 幂等：分类不被覆盖、重复事件丢弃 | 歧义分类写进 `record.recovery`；`pending_action_id` 一旦消费即清空 → 第二个事件匹配不到 |
| 7 | 动作**明确终止**后用户显式恢复 | `SETTLED` | 允许恢复（既有语义） | 终态已知 ⟹ 不是歧义；但必须走**新代次 + 新关联键**（可审计） |
| 8 | 崩溃期间取消 / 过期 / 撤销授权 | — | 终态任务一律早返回；歧义一律清授权 | `recover()` 对终态早返回；歧义态 `resume()` 直接被拒 |
| 9 | 多段路线的当前段 | `INTENT` 在第 k 段 | 整任务停，后续段一步不走 | 歧义态 `drive()`/`resume()` 都拒绝；前序路点仍先经逐段验证 |
| 10 | 关联不上 / 冲突的 runtime 结果 | — | 事件丢弃 + 歧义 fail-closed | 匹配不到任务的事件直接 `return None`，绝不猜、绝不推进 |

### 1.3 实现落点

| 改动 | 位置 |
| --- | --- |
| **派发前落盘意图**（每次真实尝试都先落盘） | `_run_step`（`app/tasks/runtime.py:633-640`） |
| 没拿到答复 ⇒ 歧义 | `TaskInvocation.ambiguous`（`runtime.py:121`）+ `_invoke_step` 的 `except` |
| 终态分类落盘 | `_settle`：`ACCEPTED`（`:662`）/ `SETTLED`（`:676` / `:699`） |
| 歧义处理（受控暂停 + 清授权 + 人工介入） | `_pause_ambiguous_dispatch`（`runtime.py:777`） |
| 恢复时的未决派发 | `recover()`（`runtime.py:1056`） |
| `resume()` 拒绝未决派发 | `runtime.py:1324` |
| 最后一道防线（直接 `drive()` 也不行） | `drive()`（`runtime.py:521`） |
| 快照可见（人工核对） | `snapshot_payload`（`runtime.py:1920`） |

> `_pause_ambiguous_dispatch` 的三件事：步骤标 `FAILED` + 失败分类 `DISPATCH_AMBIGUOUS`、
> 派发状态标 `AMBIGUOUS`、**清授权并置 `replan_required`**。语义就是"想再动世界，必须有新计划 +
> 新确认" —— 这正是任务书要求的**受控暂停与人工介入**。
>
> 注意：`dispatch_state/generation/key/at` 属于**运行期状态**，不进 `plan_hash`
>（`hash_payload()` 只包含 step_id/tool/arguments/depends_on），所以派发本身不会作废自己的授权。

## 2. 派发关联键与派发代次

### 2.1 定义

* **关联键**：`dispatch_key_of(task_id, plan_hash, step_id, generation)`
  （`app/tasks/models.py:355`）→ `dsp_<20 位 hex>`，由四个要素共同决定：
  任务 id + **冻结** `plan_hash` + 步骤 id + **持久化代次**。
* **派发代次** `dispatch_generation`（`models.py:410`）：0 = 从未派发；
  **每次真实派发尝试前 +1 并落盘**（`runtime.py:633-638`）。

### 2.2 三条性质（都有测试）

| 性质 | 含义 | 用例 |
| --- | --- | --- |
| 同代次同键 | 同一代次重复观察 / 重放得到**同一个键**（可识别为同一次派发） | `test_key_is_stable_across_runtime_instances_and_generations` |
| 新代次新键 | 用户显式发起的新一次执行尝试 → 新键（**可审计，绝不掩盖重复派发**） | 同上 + `test_explicit_resume_after_clear_failure_uses_a_new_generation` |
| 重启不改键 | 代次来自**持久化记录**、不来自内存计数；换 runtime 实例读同一份记录，键与代次都不变 | `test_key_is_stable_across_runtime_instances_and_generations` |

另有一条结构性保证：**每次真实派发之前都先落盘意图**，所以"意图的条数"就等于"外部动作尝试的次数"
（`test_every_new_dispatch_attempt_persists_its_intent_first`）。

### 2.3 这个键**不是**什么

它不是服务端去重保证（见 §3）：当前 runtime 不接受客户端自带的幂等键，也无法按键查询。
键在本阶段的作用是**审计与人工核对的可追溯标识** —— 歧义消息里会带上它。
真正的安全来自"派发前落盘 + 歧义时 fail-closed"，而不是一个并不存在的服务端保证。

## 3. runtime 是否具备幂等接受与按键查询能力：**不具备**

这是任务书 §三.2 要求先判定的前提，结论是**否**，且逐条有代码证据：

| 能力 | 结论 | 证据 |
| --- | --- | --- |
| 客户端自带幂等键 / 关联键 | **无** | 没有任何路由读 `body.action_id` / `request_id` / `idempotency_key`；`dig` 路由只读自己的参数（`minecraft_runtime/runtime.js:4172-4184`，body 解析 `:3900-3932`） |
| `action_id` 稳定性 | **不全局唯一** | 进程内计数器：`let seq = 0; nextActionId() { seq += 1; return "act_" + now().toString(36) + "_" + seq.toString(36) }`（`minecraft_runtime/action_runtime.js:97-102`）→ 重启后从 `_1` 重来 |
| 按键查询动作状态 | **无** | 没有任何 `GET` 接 `action_id`；只有 `GET /minecraft/status` 里的**单个** `action` 对象（`runtime.js:3872-3894`），且注册表 `const active = new Map()`（`action_runtime.js:94`）终止即 `active.delete`（`:210`），只留**一条**最近完成视图（`:212`） |
| 跨进程/重启保持 | **不保持** | 注册表纯内存、无 `fs`；Python 侧会自动重启桥接（`service.py:1228-1244`，有次数上限）→ 在途动作与终态事件一起消失 |
| 去重 / 重放保护 | **无** | 全仓 grep `idempot|dedup|replay|request_id`（`runtime.js`）**零命中**；重复提交 = **两个独立动作**，唯一护栏是 `action.busy` 409 前台互斥（`action_runtime.js:251-259`） |
| 终态事件可靠性 | **尽力而为** | 事件队列上限 200、投递失败即丢最旧（`runtime.js:1295`/`:1341`/`:1414-1416`） |

**因此**：按任务书要求采用**明确记录能力限制 + fail-closed 歧义处理**的路线，
**不实现、也不宣称 exactly-once**。当外部是否接受无法确认时，系统**安全停止并要求人工介入**，
而不是猜一个"应该没执行吧"。

## 4. 故障注入用例的原始证据

注入方式：可控 store 替身（在指定持久化点"断电"，只崩一次）+ 可编排执行替身
（模拟"runtime 可能已接受但响应丢失"）。**不通过杀生产进程模拟崩溃**。
所有断言的对象都是：**外部世界动作真实启动次数**、真实 `action_id`/关联键/代次、
**从 store load 回来的持久化状态**、以及最终世界动作数。

| 用例 | 崩溃时点 | 外部动作计数 | 恢复终态 | 授权检查 |
| --- | --- | --- | --- | --- |
| `test_crash_before_intent_persisted_proves_no_action_was_sent` | 写入意图前 | 崩时 **0** → 恢复后恰好 **1** | `SUCCEEDED` | 授权仍有效（没有任何需要怀疑的副作用） |
| `test_intent_without_action_id_is_fail_closed` | 意图已落盘 | **0** | `PAUSED` / `DISPATCH_AMBIGUOUS` | 授权**已清**、`replan_required=True`、`resume()` 被拒（`task.replan_required`） |
| `test_resume_still_refuses_when_replan_flag_is_cleared` | 同上（抹掉 replan 标记） | **0** | — | `resume()` 仍被拒（`task.dispatch_unresolved`）——纵深防御 |
| `test_lost_response_after_acceptance_never_re_dispatches` | runtime 可能已接受 | **1**（恢复/重放后仍是 1） | `PAUSED` / `DISPATCH_AMBIGUOUS` | 授权**已清**；`action_id` 为空但**代次 1 + 关联键已落盘** |
| `test_action_id_persisted_but_not_waiting_is_invalidated_not_retried` | `action_id` 已落盘 | **0**（旧动作不重做） | `PAUSED` / `RUNTIME_RESTART` | 授权**已清**、`pending_action_id` 清空 |
| `test_action_finished_but_terminal_unsaved_never_re_dispatches` | 终态未落盘 | **1**（恢复后仍是 1） | `PAUSED` / `RUNTIME_RESTART` | 授权**已清**、`replan_required=True` |
| `test_repeated_recovery_is_idempotent_and_keeps_classification` | 重复恢复扫描 | **0** | 分类保持 `DISPATCH_AMBIGUOUS` | `recovery.reason` 不被覆盖 |
| `test_duplicate_terminal_event_is_ignored` | 重复事件投递 | 计数不变 | 第二次事件 `None` | — |
| `test_unknown_action_event_changes_nothing` | 关联不上的事件 | **1**（不变） | 仍 `WAITING_ACTION` | `pending_action_id` 不变 |
| `test_explicit_resume_after_clear_failure_uses_a_new_generation` | 明确失败后显式恢复 | **1 → 2**（恰好再来一次） | `SUCCEEDED` | 代次 1→2、**键已变**；`SETTLED` 才允许 |
| `test_cancelled_task_with_intent_is_not_resurrected` | 崩溃期间被取消 | **0** | 保持 `CANCELLED` | `resume()` 被拒 |
| `test_expired_task_with_intent_is_not_resurrected` | 崩溃期间过期 | **0** | 保持 `EXPIRED` | — |
| `test_unresolved_current_leg_blocks_every_later_leg` | 多段第 1 段未决 | **0**（一步都没走） | `PAUSED` / `DISPATCH_AMBIGUOUS` | 第 2/3 段仍 `PENDING` |
| `test_recovery_still_verifies_previous_leg_before_the_next_move` | 多段前序段未验证 | 恢复后继续，**第 1 段补验先于第 2 段移动** | `SUCCEEDED`，逐段证据 `[1,2,3]` | 授权未被无故清除 |
| `test_key_is_stable_across_runtime_instances_and_generations` | — | — | — | 同代次同键 / 新代次新键 / 重启不改键 |
| `test_every_new_dispatch_attempt_persists_its_intent_first` | — | **1** | `SETTLED`、代次 1 | 派发前必有一次意图落盘 |

**每个崩溃点的预期行为与"为什么不重复派发"见 §1.2 的表格。**

---

## 5. 门禁结果

### 5.1 本地门禁（按既有流程串行）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 649 files already formatted |
| `mypy app` | Success: no issues found in 336 source files |
| `pytest tests -q` | **3779 passed in 673.91s (0:11:13)**（基线 3760 + 19，精确吻合） |
| `tests/test_action_dispatch_safety.py` | **19 passed** |
| WebUI `npm run typecheck` | 通过（exit 0） |
| WebUI `npm test`（Vitest） | 75 files / **528 passed**（exit 0） |
| WebUI `npm run build` | 通过（✓ built in 7.24s） |
| WebUI `npm run test:e2e`（Playwright） | **7 passed (19.5s)** |
| `minecraft_runtime npm test` | ALL CHECKS PASSED（exit 0） |
| 迁移演练 `scripts/migrate_db.py --db <副本> --json` | v36 → v36 **幂等**；83 表；`integrity ok`；`lost_rows {}` |

> 迁移演练的 `ok:false` 是**已处最新版（v36）时的预期 no-op**。补充核验：`pragma quick_check` = `ok`、
> `foreign_key_check` **0 条违规**、83 张业务表、最高迁移 36。**本阶段未新增任何迁移。**

### 5.2 真实 Fabric 1.21.1 门禁（三段脚本，全部 PASS）

执行前：备份 live DB（`CatooBot_backups\catoobot-20261011-231102.db`，v36 / 83 tables / 87366 rows /
`integrity=ok`），停 CatooBot 释放 runtime 唯一会话（25580 / 8500 释放、25565 在线）；
执行后按原方式重启（25580 / 8500 恢复）。按 **7F.1 → 7F.2 → 多段** 的顺序执行
（把会主动逼近不可达目标的多段门禁放最后，避免污染后续门禁的起始环境）。

**① `scripts/explore_smoke_real.py --sections all`（Phase 7F.1 单段回归）→ PASS（13 项）**

* 正向：起点 `(-268.5, 64.0, -127.5)` → 目标 `(-264.0, 64.0, -130.0, radius=2.0)`，终态 `SUCCEEDED`、
  `verification.ok=True`（`position_within.distance = 1.54`）、位移 4.1、无遗留移动；
* blocked：不可达目标 → 安全 `PAUSED` / `ACTION_FAILED`，绝不冒充到达；
* cancel：导航中取消 → `CANCELLED` + 经 `minecraft_stop` 真停 + **驱动终态任务不再移动角色**。

**② `scripts/feedback_smoke_real.py`（Phase 7F.2 反馈回归）→ PASS（19 项）**

起点 `(-261.55, 71.0, -124.5)`；探索 `SUCCEEDED` + `verification.ok=True` → 真记忆写 3 条 →
`MINECRAFT_INTEREST` LifeIntent → `TaskProposal`（created，来源 LIFE，`intent_id` 一致）→
`AgentPlan`（`READY_FOR_APPROVAL`，提案/意图可追溯，规划阶段未建执行任务）→ 第一道门批准 →
停在 `PENDING_CONFIRMATION` 且**零真实动作**。

**③ `scripts/explore_multi_leg_smoke_real.py --sections all`（多段：正向 / 途中失败 / 取消）→ PASS**

* **A 正向**：起点 `(-262.5, 64.0, -128.5)`；真实冻结路线 **3 段**、总距离 **23.8 ≤ 24**（几乎用满总预算）：
  `step_1 → (-257,64,-128)` 5.5 格、`step_3 → (-268,64,-130)` 11.2 格、`step_5 → (-263,64,-135)` 7.1 格；
  终态 `SUCCEEDED`、`verification.ok=True`（距 1.65）。**逐段证据 3/3 全部 verified 且带真实坐标**：
  第 1 段 `act_mv3yrela_1` 距 **0.67**、第 2 段 `act_mv3yrfyg_3` 距 **1.58**、第 3 段 `act_mv3yriel_5` 距 **1.65**
  （严格半径 2.0）；位移 5.0；无遗留移动。
* **B 途中失败**：把第 2 段目标改成同一 x/z、**正上方 40 格**（水平距离不变 → 计划校验仍成立，但真实上不去）
  → 终态 `PAUSED` / `ACTION_FAILED`（路径计算超时）；逐段证据 1 条 `[(1, True)]`；
  **被改段之后的 4 个步骤全部 `PENDING`**（`step_4 / step_5 / step_6 / step_7` 一个都没执行）；无持续移动。
* **C 取消**：冻结 3 段路线 → 批准 → 导航中 `WAITING_ACTION` → `cancel` → `CANCELLED` + 真停；
  **后续路点段仍 `PENDING`**（`step_3 / step_5`）、驱动终态任务不再移动角色。

> 真实门禁会真实写 live DB：本轮 `life_intents` 99 → **115**、`activity_episodes` 191 → **210**、
> `task_proposals` 30 → 35；事后核验 `integrity ok`、最高迁移仍 **36**、仍 **83** 张业务表、FK 违规 0。

## 6. CI、数据库与敏感文件边界

### 6.1 CI（GitHub Actions）

* commit `7c8548c714a839747de166235d67809b4c8f4e44`，run **#171 / attempt 1 → conclusion = success**；
* `lint · format · types · tests` → **success**：CI 上 `ruff check .` = All checks passed、
  `ruff format --check .` = 634 files already formatted（CI 只看到镜像内文件）、
  `mypy app` = Success（336 source files）、
  `pytest tests -q` = **3776 passed, 3 skipped in 244.61s**（与本地 3779 精确对账：3 条为既有平台
  条件性 skip，本阶段新增 19 项**无一被跳过**）、WebUI Browser E2E = **7 passed (16.7s)**、
  `minecraft_runtime` 全部套件 = ALL CHECKS PASSED（既有 SKIP 见 §7.2）；
* `webui · typecheck · tests · build` → **success**；
* 本轮**没有出现** follow_player 偶发 flake：**attempt 1 即全绿**，无需重跑。

### 6.2 数据库

* 迁移：**无新增**（最高仍 36）；演练 `version [36,36]`、`tables [83,83]`、`integrity [ok,ok]`、
  `added_tables []`、`lost_rows {}`；副本 `quick_check ok`、FK 违规 0。
* 逐段证据与派发状态都在**既有** `agent_task_runs.payload`（单个 TEXT 列）里 —— 无新表、无新列、无迁移。
* 真机后 live DB：`integrity ok`、`max_version 36`、83 张业务表、FK 违规 0。

### 6.3 敏感文件边界

| 项 | 状态 |
| --- | --- |
| 19 个工具 / `ACTION_RISK` / `allow_medium=false` | **未改** |
| `LifeIntent` / `TaskProposal` / `AgentPlan` 语义 | **未改** |
| 计划批准 + 第二道执行确认门 / `TaskAuthorization` / `TaskStepAuthorization` / Policy | **未改**（歧义时是**清授权**，只收紧不放宽） |
| 多段 ≤3 段与共享 24 格预算 / `explore_legs=1` 默认值 | **未改** |
| 无界自动重试 | **未引入**（LOW/MEDIUM 动作仍然 attempts=1） |
| 世界修改能力 | **未新增**：仍只 SAFE 观察 + LOW 移动 + SAFE 朝向 |
| 真实 Character Bible / 密钥 / `.env` / 敏感配置 | **未触碰** |
| 发布边界 | `scripts/` 仍**不进** GitHub 发布（真机脚本只留本地） |

---

## 7. 首次失败、重跑与遗留风险

### 7.1 首次真机门禁的失败（如实记录）

**7F.1：FAIL 1 项** —— 取消段落最后一项"取消后驱动终态任务不再移动角色"（其余 12 项全过）。

* 判定：终态任务的 `drive()` 立即 `return`，**不可能**产生动作；同一断言在此前两轮真机门禁都通过。
  因此判定为"取消后的物理滑行 / 感知缓存收敛"造成的**抖动**（`before` 读到的是取消前的缓存坐标）。
* 处置：按项目既有纪律（真实服务器时序抖动 → 如实记录 FAIL 并重跑）重跑 → **通过**。
  没有放宽阈值、没有跳过、没有只看退出码。

**多段门禁：FAIL 6 项** —— 逐条归因：

1. **段落 A（4 项）**：第 2 段真实落点距目标 **2.12 格 > 半径 2.0** → runtime **正确地**判该段验证失败、
   停在第二段、第 3 段一步未走（终态 `FAILED/VERIFICATION`）。这是项目**已知**的 pathfinder 精度抖动
   （7F.1 历史记录里就有 2.26 格的先例），**不是 7F.4 引入**；同时暴露我脚本的容差写松了（+0.75），
   所以出现了"✓ 落点在半径内"与"✗ 逐段验证未通过"并存的误导输出。
   → 已把脚本的逐段落点判定改成与 runtime **同口径**（严格半径、不加容差）。
2. **段落 B（2 项）**：脚本当时要求 3 段，而真实世界这一轮只给出 2 段 → 无法制造"中途段失败"。
   → 已改成自适应：≥2 段即可，首选把**第 2 段**做成不可达（后面还有段可验证"后续段一步不走"），
   世界只给 2 段时退化为"把最后一段做成不可达"，并断言**被改段之后的所有步骤**都是 `PENDING`。

**重跑结果：三段全部 PASS**（见 §5.2）。

### 7.2 SKIPPED

`minecraft_runtime` 套件内既有 SKIP（假服务器不支持 `/summon` / `/give`、空背包等），与本阶段改动无关；
本阶段**没有新增任何 SKIP**，也没有跳过任何真机段落。

### 7.3 遗留风险（因此**不宣称 exactly-once**）

1. **服务端没有幂等与查询能力**（§3 逐条证据）：无法证明外部动作是否真的发生。本阶段能保证的只是
   **不会因为恢复而再派发一次**；"到底发生没发生"必须由人工核对关联键后决定。
2. **桥接在动作执行中重启、而 Python 没重启**：终态事件永远不会到达 → 任务停在 `WAITING_ACTION`
   直到 TTL 过期（`EXPIRED`）。这是 fail-closed（不会重发），但本阶段**不主动探测**
   —— 那需要新增健康/查询通道，超出范围边界。
3. **`on_action_event` 只扫最近 50 条任务**（`store.list_recent(50)`）：超出窗口的事件会被丢弃 →
   任务等待至 TTL 过期（同样 fail-closed，不会重发）。
4. **桥接动作 API 无鉴权**（`BRIDGE_TOKEN` 未被 Python 设置，仅绑 127.0.0.1）：既有事实，
   按范围边界本阶段不动它。
5. **`action_id` 不全局唯一**（进程内计数器、重启从 `_1` 开始）：理论上存在"陈旧 pending 记录与新生代
   动作撞号"的极端可能；因为 `recover()` 在重启时会清掉旧 `pending_*`，实际风险很低，但如实记录。
6. **`dispatch_key` 是审计与人工核对的标识**，不是服务端去重保证；也不应被当成"已经去重"的证据。
7. **新增的成本**：每次真实派发尝试多一次 `_save`（意图落盘）。这是"崩溃后能知道派发可能已发生"的代价，
   换来的是不再有"恢复即重发"的路径。


