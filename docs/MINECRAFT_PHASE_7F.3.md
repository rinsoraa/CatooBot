# CatooBot Phase 7F.3 — Bounded Multi-leg Exploration（有界多段探索）

> 基线：`173819a19c27b45d383b2aa35a5462e32f41e0ac`（Phase 7F.2.2.1）。
> 本文件记录：架构审计结论（§1）、路线生成算法/预算/段数的精确定义（§2）、
> 逐段后置条件的实现位置与授权覆盖（§3）、失败止步的**原始测试证据**（§4）、
> 单段逐字节回归证明（§5）、门禁结果（§6）、安全边界对比（§7）、
> 变更文件（§8）、失败/重跑/环境问题记录（§9）。
>
> **阶段目标**：实现第一个有界多段探索闭环 —— 沿一条**预先规划、长度有上限**的路线
> 逐段移动、重新观察、**独立验证实际到达**，并在失败/取消/证据不足时可靠停止。
>
> **这不是**把多组 `move_to` 堆进 planner：真正的改动是补齐了
> "**每一段到达后才允许执行下一段**" 这个执行期不变量（见 §1）。

---

## 1. 架构审计结论（先审计再实施）

任务书 §一 要求先回答一个关键技术问题：

> **当前全任务最终状态验证是否足以保证每一段到达后才执行下一段？**

**结论：不足以。** 审计证据（改动前）：

* `app/tasks/planner.py` 的探索模板只产出**一个** target，
  `expected_final_state.position_within` 只覆盖**终点**；
* `app/tasks/runtime.py` 的 `drive()` 是逐步骤循环，**步骤之间没有任何后置条件校验**；
  只有 `next_step()` 返回 None 时 `_finish()` 才用 SAFE 读验证一次**终态**；
* 因此存在真实缺口：第 1 段 `move_to` 自述成功、实际没到，只要**末段**恰好到达，
  整个任务仍会被判 `SUCCEEDED` —— 中途段完全没有独立证据。

由此确定最小机制（全部复用既有原语，不新造授权路径）：

| 环节 | 复用的既有能力 |
| --- | --- |
| 逐段到达判定 | `TaskRuntime._verify_position_within`（真读 SAFE `minecraft_world` + 有界等待 6×0.6s + fail-closed） |
| 逐段后置条件冻结 | 放进 `ExpectedFinalState.to_payload()` → **自动进入 `plan_hash`** |
| 授权覆盖 | `_confirmation_arguments()` = `{plan_hash, plan.hash_payload()}`；`TaskStepAuthorization.plan_hash` 取**授权时记录的那份**并与现算比对 |
| 逐段证据持久化 | `TaskRecord` 的双向序列化（payload 无 schema 守卫，**不需要新迁移**） |
| 门控点 | `drive()` 取到下一步之后、执行之前的前置守卫（同时覆盖**崩溃恢复**） |

**为什么守卫放在"执行前"而不是"结算后立即验证"**：进程可能在
"动作已结算（`step.state=SUCCEEDED`）"与"到达验证"之间崩溃；恢复后 `drive()` 会直接从
**下一步**继续。若把验证挂在结算路径上，恢复就会跳过它，下一段将在**没有上一段到达证据**
的情况下开始走。放在执行前的前置守卫对两条路径都成立。

---

## 2. 路线生成算法、距离预算与最大段数（精确定义）

### 2.1 常量（单一来源，不各写一份）

| 常量 | 值 | 位置 |
| --- | --- | --- |
| `MAX_EXPLORE_LEGS` | `3` | `app/tasks/models.py:495` |
| `MAX_EXPLORE_ROUTE_DISTANCE` | `24.0`（**整条路线**，非每段） | `app/tasks/models.py:499` |
| `MIN_LEG_SEPARATION` | `1.0`（更近即视为重复路点） | `app/tasks/models.py:503` |
| `EXPLORE_DEFAULT_DISTANCE` | `= MAX_EXPLORE_ROUTE_DISTANCE` | `app/tasks/planner.py:320` |
| `EXPLORE_MAX_LEGS` | `= MAX_EXPLORE_LEGS` | `app/tasks/planner.py:323` |
| `MIN_LEG_DISTANCE` | `4.0`（与既有 POI 选择口径一致） | `app/tasks/planner.py:326` |
| `_LEG_DISTANCE_TOLERANCE` | `0.35`（规划期坐标四舍五入到 0.1） | `app/tasks/validation.py:41` |

**预算语义（关键）**：24 格是**整条路线的总预算**，多段**共享**它。
任务书明确要求"不得把原有单段预算无意扩大为每段预算" —— 若按每段 24 格，3 段就是
**3 倍的授权扩张**。实现方式：`EXPLORE_DEFAULT_DISTANCE` 直接**等于** `MAX_EXPLORE_ROUTE_DISTANCE`，
且生成器用累计长度与它比较。

### 2.2 生成算法（`_explore_waypoints`，`app/tasks/planner.py:401`）

确定性、可审计、只读真实世界视图：

1. **第 1 段 = `:func:_explore_target` 的结果** —— 与 Phase 7F.1 **完全相同**
   （最近的合规 POI；没有 POI 时用最开阔罗盘方向的有界一步）；
2. **第 2..N 段只从真实 POI 里挑**，候选必须：坐标经 `finite_number` **严格有限**、
   与起点水平距离 ∈ [`MIN_LEG_DISTANCE`, `max_distance`]；
3. 候选按 `(round(距离,3), round(x,3), round(z,3))` **全序排序**，逐个贪心取第一个可接受项
   —— 同一个世界视图**必然**得到同一条路线；
4. 可接受条件（缺一不可）：与**所有**已选路点相距 ≥ `MIN_LEG_SEPARATION`；
   与**上一段**相距 ≥ `MIN_LEG_DISTANCE`；`累计总长 + 本段长度 ≤ max_distance`；
5. 没有可接受候选 → **明确缩短路线**（返回更少的段）。
   **绝不**用重复路点凑段数、**绝不**编造坐标、**绝不**为了让 3 段成立而放宽任何规则。

### 2.3 计划形状

`n` 段 → `2n+1` 步：每段 `minecraft_move_to`(LOW) + `minecraft_look_at`(SAFE)，
末尾**一次** `minecraft_world`(SAFE) 提供执行期真实新事实（Phase 7F.2 的记忆回流来源）。

上限核对：3 段 = **7 步** ≤ `MAX_STEPS=16`；动作步 **3** ≤ `MAX_ACTION_STEPS=8`；
任务 TTL `600s`；重规划上限 `2`（未改）。**不存在**任何越界路径。

### 2.4 实测样例

起点 `(-8, 70, 5)`，三个真实 POI `z=11 / 17 / 23`（各距起点 6 / 12 / 18 格）：

```json
{"start": {"x": -8.0, "y": 70.0, "z": 5.0}, "budget": 24.0,
 "legs": [
   {"index": 1, "step_id": "step_1", "position_within": {"x": -8.0, "y": 70.0, "z": 11.0, "radius": 2.0}, "distance": 6.0},
   {"index": 2, "step_id": "step_3", "position_within": {"x": -8.0, "y": 70.0, "z": 17.0, "radius": 2.0}, "distance": 6.0},
   {"index": 3, "step_id": "step_5", "position_within": {"x": -8.0, "y": 70.0, "z": 23.0, "radius": 2.0}, "distance": 6.0}]}
```

`STEPS = step_1:move_to, step_2:look_at, step_3:move_to, step_4:look_at, step_5:move_to,
step_6:look_at, step_7:world`；`plan_hash = 4d279ce7c4c40280d3bf`；
`validate_plan(...) = []`（无问题）。总距离 18 ≤ 24。

---

## 3. 逐段后置条件的实现位置与授权覆盖

### 3.1 冻结结构

`ExpectedFinalState.route`（`app/tasks/models.py:633`）：

```
route = {"start": {x,y,z}, "budget": float, "legs": [
    {"index": int, "step_id": str, "position_within": {x,y,z,radius}, "distance": float}, ...]}
```

单段计划**不带** `route` 字段 —— 所以 7F.1/7F.2 的计划 payload 与 `plan_hash` 逐字节不变（§5）。

### 3.2 解析必须 fail-closed（`_route_of`，`app/tasks/models.py:539`）

三种结果**必须**区分：不存在（`None`）/ 合法 / **存在但非法**（`{"invalid": True, "reason": ...}`）。
第三种的语义是"计划必须被拒绝"，**绝不能**当成"没有路线" —— 否则一份被篡改或损坏的多段计划
会**静默退化**成"不做逐段校验"，等于把 fail-closed 变成 **fail-open**。

严格性：路点坐标用 `finite_number`（拒绝 `bool` / `NaN` / `±Infinity` / 转换溢出 /
不可转换值），索引必须从 1 连续递增，半径必须为有限正数，`distance` 必须有限非负。
**注意**这里**不复用** `_position_within_of`：后者对 `NaN` 只做 `float()` 不做 `isfinite()`
（既有的终点后置条件语义如此），而路线目标决定**执行顺序**，必须严格有限。

### 3.3 授权覆盖（复用既有机制，未新造）

* `TaskPlan.hash_payload()` 包含 `expected_final_state.to_payload()` → `route` 里的
  **路点坐标、半径、段距离、预算、起点**全部进入 `plan_hash`；
* `_confirmation_arguments()` = `{"plan_hash": ..., "plan": plan.hash_payload()}`
  → 用户确认绑定的就是这份含路线的计划；
* `TaskStepAuthorization.plan_hash` 取**授权记录里存的那份**（不是现算的），
  执行时与 `record.plan_hash` 比对 → **批准后任何路点/距离改动都会被检测**并判
  `TaskFailure.AUTHORIZATION`。

### 3.4 计划校验（`_route_problems`，`app/tasks/validation.py:69`，由 `validate_plan` 调用于 :237）

拒绝整份计划（不静默裁剪）的情形：段数 > 3、预算 > 24、起点/预算非法、路线结构非法、
`step_id` 不存在、`step_id` 不是移动步、路点顺序与步骤顺序不符、路点重复（< 1.0 格）、
声明距离与坐标不一致（> 0.35 格）、**总距离超预算**、最后一段与期望终态不一致。
缩短路线只允许发生在**规划期**（那时还没有任何授权）。

### 3.5 执行期守卫与证据（`app/tasks/runtime.py`）

| 位置 | 作用 |
| --- | --- |
| `drive()` :514 | 取到下一步后、执行前：`_ensure_legs_verified(record, before_step=step)`；返回 False 直接终止 |
| `_ensure_legs_verified` :1342 | 把所有"移动步已 `SUCCEEDED` 且位次在当前步之前"的段逐个验证；已记录过的段跳过（幂等） |
| `_leg_target_of` :1280 | 路点目标 → 有限坐标 + 正半径，否则 fail-closed |
| `_leg_evidence` :1297 | 逐段证据：`index/step_id/task_id/action_id/target/position/distance/verified/failure/message/checked_at` |
| `_fail_leg` :1322 | 写入证据 + `FAILED` + `TaskFailure.VERIFICATION`（与 `_finish` 同一失败分类） |
| `TaskRecord.leg_verifications` | `app/tasks/models.py:871`，**双向序列化** :1040 / :1080（恢复/重放不丢证据） |
| 快照投影 | `runtime.py:1808`（WebUI/QQ 可见） |

"走完"的判据是**持久化的** `StepState.SUCCEEDED` —— 不是 `move_to` 的返回值，
也不是"动作进了 RUNNING"，更不是上报位置。

---

## 4. 失败止步的原始测试证据

任务书 §四 要求："必须增加真正检查'中途验证失败后后续动作没有发生'的测试，
不能只检查最终 Task 状态。" 因此证据是**真实发生的 move_to 目标序列**（`world.move_plan()`）：

| 用例 | 场景 | 原始证据（实际发生的动作） | 终态 |
| --- | --- | --- | --- |
| `test_unarrived_first_leg_blocks_every_later_leg` | 第 1 段动作自述成功、实际距目标 6 格 | `moves == [(-8,70,11)]`（第 2/3 段一次都没发生） | `FAILED` / `VERIFICATION` |
| `test_unarrived_middle_leg_blocks_the_rest` | 第 2 段没到 | `moves == [(-8,70,11), (-8,70,17)]`，证据 `[True, False]` | `FAILED` / `VERIFICATION` |
| `test_unreachable_first_leg_stops_as_action_failure` | 第 1 段路径找不到 | `moves == [(-8,70,11)]`，`leg_verifications == []` | `PAUSED` / `ACTION_FAILED` |
| `test_offline_world_blocks_before_the_next_leg` | 复核时世界读不到 | `moves == [(-8,70,11)]`，证据 `position is None` | `FAILED` / `VERIFICATION` |
| `test_missing_self_position_blocks_before_the_next_leg` | 世界视图缺自身坐标 | `moves == [(-8,70,11)]` | `FAILED` / `VERIFICATION` |
| `test_leg_verification_happens_before_the_next_leg_moves` | 顺序性质（恢复安全依赖） | `calls.index("minecraft_world") < 第 2 次 move_to 的下标` | `SUCCEEDED` |
| `test_cancel_mid_route_leaves_no_residual_action_or_extra_leg` | 导航中取消 | `moves` 仍只 1 条，`minecraft_stop` 已调用，再次 `drive()` **零新调用** | `CANCELLED` |
| `test_post_approval_leg_tamper_is_detected_and_not_executed` | 批准后偷改第 2 段路点 | `moves == [(-8,70,11)]`（被改路点**未执行**） | `FAILED` / `AUTHORIZATION` |
| `test_new_interest_points_do_not_add_unapproved_waypoints` | 执行期出现新兴趣点 | `moves == [(-8,70,11), (-8,70,17), (-8,70,23)]`，仍 7 步 | `SUCCEEDED` |

其余自动化覆盖：`test_every_leg_is_verified_by_a_fresh_real_coordinate_read`（三段各有独立
真实坐标证据）、`test_world_reads_are_bounded_to_one_per_leg`（每段恰好一次验证读，幂等）、
`test_leg_evidence_survives_a_store_round_trip`（证据持久化）、
`test_redrive_replays_nothing_and_keeps_the_evidence`（终态重放零动作）、
以及 `TestPlanValidationRejectsBadRoutes` 的段数/预算/重复/非移动步/
**NaN·Infinity·bool·溢出·不可转换字符串** 六类非法坐标全部拒绝。

---

## 5. 单段逐字节回归证明（Phase 7F.1/7F.2 行为未变）

用**同一份观察替身**分别跑**镜像里未改动的旧代码**（`173819a`）与**改动后的工作区代码**：

| | 旧代码（`CatooBot_github`） | 新代码（工作区） |
| --- | --- | --- |
| `plan.to_payload()` | 见下（完全相同） | 与左列**逐字节相同** |
| `plan_hash` | `028311297b1e114e9fac` | `028311297b1e114e9fac` |
| `route` 字段 | 无 | 无（`HAS_ROUTE=False`） |

输出一致，即：**`plan_exploration_task` 默认（`legs=1`）的计划与授权指纹一字未变。**
另有单测 `test_single_leg_plan_is_unchanged_from_phase_7f1` 以手写"7F.1 形状"计划做同样断言
（`to_payload()` 相等 + `plan_hash` 相等 + payload 里没有 `route`）。

---

## 6. 门禁结果

### 6.1 本地门禁（按既有流程串行）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 647 files already formatted |
| `mypy app` | Success: no issues found in 336 source files |
| `pytest tests -q` | **3760 passed in 669.63s (0:11:09)**（基线 3734 + 26，精确吻合） |
| `tests/test_multi_leg_explore.py` | 26 passed |
| WebUI `npm run typecheck` | 通过（exit 0） |
| WebUI `npm test`（Vitest） | 75 files / **528 passed**（exit 0） |
| WebUI `npm run build` | 通过（✓ built in 9.10s） |
| WebUI `npm run test:e2e`（Playwright） | **7 passed (23.6s)** |
| `minecraft_runtime npm test` | ALL CHECKS PASSED（exit 0） |
| 迁移演练 `scripts/migrate_db.py --db <副本> --json` | v36 → v36 **幂等**；83 表；`integrity ok`；`lost_rows {}` |

> 迁移演练的 `ok:false` 是**已处于最新版（v36）时的预期 no-op**（版本 `[36,36]`、表数不变、无新增迁移）。
> 补充核验：副本共 84 张表（83 业务表 + `sqlite_sequence`）、`pragma quick_check` = `ok`、
> `foreign_key_check` **0 条违规**、`schema_migrations` 36 行（最高 36）。**本阶段未新增任何迁移。**

### 6.2 真实 Fabric 1.21.1 门禁（三段脚本，全部 PASS）

执行前：备份 live DB（`CatooBot_backups\catoobot-20261011-191851.db`，v36 / 83 tables / 86963 rows /
`integrity=ok`），停 CatooBot 释放 runtime **唯一会话**（25580 / 8500 释放、25565 在线）；
执行后按原方式重启并确认 25580 / 8500 恢复。

**① `scripts/explore_multi_leg_smoke_real.py --sections all` → PASS**

* 段落 A（多段正向）：起点 `(-267.47, 64.0, -128.5)`；真实冻结路线 **2 段**
  （`step_1 → (-262.0,64.0,-129.0)` 本段 5.5 格、`step_3 → (-263.0,64.0,-135.0)` 本段 6.1 格），
  总距离 **11.6 ≤ 24**、`budget = 24.0`；终态 `SUCCEEDED`、`verification.ok=True`（距 1.58）。
  **逐段证据 2/2 全部 verified 且带真实坐标**：第 1 段 `action=act_mv3qhr56_1` 距 **0.71** 格、
  第 2 段 `action=act_mv3qhse8_3` 距 **1.58** 格（允许 2.0）；位移 7.0 格；结束后无遗留移动。
* 段落 B（中途段真失败 → 后面的段一步不走）：把第 2 段目标改成同一 x/z、**正上方 40 格**
  （`(-268.0,104.0,-130.0)`：水平距离不变，故计划校验仍成立，但真实上不去）→ 终态 `PAUSED` /
  `failure=ACTION_FAILED`（说明：路径计算超时（非破坏性））；逐段证据 1 条 `[(1, True)]`
  （第 1 段已有独立真坐标证据）；**第 3 段（`step_5`）从未被执行（`PENDING`）**；无持续移动。

> 同一脚本**两轮**分别规划出 **3 段**（总 15.9 格，逐段实际距离 1.55 / 0.71 / 1.04）与 **2 段**
> （总 11.6 格，逐段实际距离 0.71 / 1.58）—— 这正是「观察不足就明确缩短路线」的真实行为，
> 而不是每次硬凑 3 段。

**② `scripts/explore_smoke_real.py --sections all`（Phase 7F.1 单段回归）→ PASS**

* 段落 1 正向：起点 `(-268.5, 67.0, -120.5)` → 目标 `(-274.0, 63.0, -123.0, radius=2.0)`（距 7.2），
  终态 `SUCCEEDED`、`verification.ok=True`（`position_within.distance = 1.82`）、位移 7.8、无遗留移动；
* 段落 2 blocked：不可达目标 → 安全 `PAUSED` / `ACTION_FAILED`，**绝不冒充到达**、无持续移动；
* 段落 3 cancel：导航中取消 → `CANCELLED` + 经 `minecraft_stop` 真停 + 不再产生新动作。

**③ `scripts/feedback_smoke_real.py`（Phase 7F.2 反馈回归）→ PASS（19 项全绿）**

起点 `(-271.56, 69.0, -125.5)`；探索 `SUCCEEDED` + `verification.ok=True` → 真记忆写 **4 条** →
`MINECRAFT_INTEREST` LifeIntent → `TaskProposal`（created，来源 LIFE，`intent_id` 一致）→
`AgentPlan`（`READY_FOR_APPROVAL`，`proposal_id` / `intent_id` 可追溯，规划阶段 active 未变）→
第一道门批准 → 停在 **`PENDING_CONFIRMATION`** 且**零真实动作**。

> 真实门禁会真实写 live DB：本轮 `life_intents` 99 → **104**、`activity_episodes` 191 → **199**；
> 事后核验 `integrity ok`、最高迁移仍 **36**、仍 **83** 张业务表。

### 6.3 CI（GitHub Actions）

* commit `7f9acc0eeee696f5b1876150ab02c87c00ed9023`，run **#169 / attempt 1** → **conclusion = success**；
* `lint · format · types · tests` → **success**：CI 上 `ruff check .` = All checks passed、
  `ruff format --check .` = 632 files already formatted（CI 只看到镜像内的文件）、
  `mypy app` = Success（336 source files）、
  `pytest tests -q` = **3757 passed, 3 skipped in 227.41s**（与本地 3760 精确对账：3 条为既有平台
  条件性 skip，本阶段新增 26 项**无一被跳过**）、WebUI Browser E2E = **7 passed (16.3s)**、
  `minecraft_runtime` 全部套件 = ALL CHECKS PASSED（其中 6 条**既有** SKIP：假服务器不支持
  `/summon` / `/give`，与本阶段无关）；
* `webui · typecheck · tests · build` → **success**；
* 本轮**没有出现**上一阶段那个 `minecraft_runtime` follow_player 偶发 flake：**attempt 1 即全绿**，无需重跑；
* 发布前已审计镜像 diff：恰好 12 个文件（9 改 3 新），密钥扫描干净，`scripts/` **未进入发布**。
## 7. 安全边界对比

| 项 | 状态 |
| --- | --- |
| 工具注册表 / `ACTION_RISK` / 19 个工具 | **未改** |
| `allow_medium`（默认 false）等风险开关 | **未改** |
| `TaskAuthorization` / `TaskStepAuthorization` / Policy / AgentBridge / ActionRuntime | **未改**（逐段后置条件只是**复用**它们的绑定） |
| 计划批准 + 第二道执行确认门 | **未改**（多段同样必须过两道门） |
| `pause` / `resume` / `cancel` / `expire` / 超时 / 停止 / 受控重规划语义 | **未改** |
| 世界修改能力（挖掘 / 放置 / 攻击 / 合成 / 开容器） | **未新增**：仍只 SAFE 观察 + LOW 移动 + SAFE 朝向 |
| 数据库表 / 迁移 | **无新表、无新迁移**（最高仍 36） |
| 记忆系统 | **无第二套**（逐段证据是任务记录字段，不是新记忆库） |
| 真实 Character Bible / 凭据 / `.env` / 敏感配置 | **未触碰** |
| 逐段验证本身的世界操作 | 只有 **SAFE 读**（`minecraft_world`），与既有终态复核同一条通道 |

## 8. 变更文件

* `app/tasks/models.py`：统一 fail-closed 数值谓词 `finite_number` (:506)、`_xyz_of` (:526)、
  路线解析 `_route_of` (:539)、常量 (:495 / :499 / :503)、`ExpectedFinalState.route` (:633) 与
  `legs()` / `leg_for_step()` / `route_invalid` (:650 / :661 / :669)、
  `TaskRecord.leg_verifications` (:871) 及其**双向序列化** (:1040 / :1080)。
* `app/tasks/exploration.py`：`_finite_axis` 收敛到 `models.finite_number` —— 计划边界与记忆边界
  从此共用**同一个** fail-closed 谓词，不再有两份等价实现；既有 37 项溢出测试原样通过。
* `app/tasks/validation.py`：`MOVE_TOOL` (:38)、`_leg_target_of` (:44)、`_planar_distance` (:62)、
  `_route_problems` (:69)，并由 `validate_plan` 调用 (:237)。
* `app/tasks/planner.py`：常量 (:320 / :323 / :326)、`_planar` (:394)、`_explore_waypoints` (:401)、
  `plan_exploration_task(..., legs=1)` (:494 / :534)。
* `app/tasks/runtime.py`：`drive()` 前置守卫 (:514)、`_leg_target_of` (:1280)、`_leg_evidence` (:1297)、
  `_fail_leg` (:1322)、`_ensure_legs_verified` (:1342)、快照投影 (:1808)。
* `app/tasks/agent_planner.py`：`explore_legs` 参数 (:134 / :144) 与透传 (:272)。
* `app/core/bot.py`：装配点按配置读取段数，**默认 1** (:1289)。
* `tests/test_multi_leg_explore.py`：新增 26 项。
* `docs/MINECRAFT_PHASE_7F.3.md`（本文件）、`CHANGELOG.md`、`docs/README.md` 索引行。
* `docs/specs/CatooBot Minecraft Phase 7F.3 — Bounded Multi-leg Exploration.md`：任务书归档。
* **本地专用、不进 GitHub 发布**：`scripts/explore_multi_leg_smoke_real.py`（真机多段门禁）。

## 9. 失败 / 重跑 / SKIPPED / 环境问题记录

### 9.1 真机门禁第一次运行的失败与根因（门禁脚本副作用，**不是**产品回归）

第一次按 7F.3 → 7F.1 → 7F.2 顺序运行，结果：**7F.3 PASS**，但 **7F.1 FAIL（2 项）**、
**7F.2 FAIL（3 项）**。

* 现象：7F.1 段落 1 终态 `PAUSED`（`verification={}`）；7F.2 探索 `PAUSED`（零记忆写入）。
* 根因：**新门禁脚本的 B 段把机器人一路向下带进了洞穴** —— 当时把第 2 段目标设成「地下 30 格」，
  机器人尝试逼近后停在 **y≈34**；随后 7F.1 / 7F.2 从洞穴里重新规划（选中 y=37 的地下 POI），
  非破坏性 pathfinding 超时 → 安全停止。
* **为什么判定是脚本副作用而不是产品回归**：① 7F.1 的计划仍是**单段**
  （`move_to` + `look_at` + `world`，正是本阶段**未改动**的路径），逐段守卫对它是纯 no-op；
  ② `verification={}` 说明任务**根本没走到验证阶段**，失败来自动作层（`ACTION_FAILED` / 超时）；
  ③ 唯一变量是起点高度从 y≈64-71 变成 y≈34。
* 处置：① 把 B 段的不可达目标从「地下 30 格」改为**正上方 40 格**（复用 Phase 7F.1 已验证可靠的
  同一配方，且**不再改变机器人的高度**）；② 备份并重置 Catodayo 的 playerdata
  （备份在 `logs/_playerdata_backup_20261011/`）使其回到世界出生点；
  ③ 改为 7F.1 → 7F.2 → 7F.3 的顺序重跑。
* 重跑结果：**三段全部 PASS**（见 §6.2）。

### 9.2 环境与既有限制（如实记录，未在本阶段修复）

1. **`_settle` 的「派发动作但未落盘」崩溃窗口（既有缺口）**：`step.action_id` 在内存里设置、
   由 `_save` 才落盘；进程若在两者之间崩溃，恢复后 `resume()` 有可能重发该步动作。
   这是**既有**恢复语义问题（由只读子代理审计发现）。修复它会改变 `recover` / `resume` 的既有行为，
   超出本阶段范围 —— 已记录，建议后续单独处理。本阶段的 `leg_verifications` 至少保证
   「已完成段的到达依据」不再只存在内存里。
2. **`ExpectedFinalState.to_payload()` 对 `route` 直接返回内部 dict 引用**（浅拷贝语义）。
   实际消费方（JSON 序列化、事件投影、plan_hash 计算）都不改写它，属低风险既有风格；
   门禁跑完后不再改代码，因此**未在本阶段调整**，如实记录。
3. **世界状态被真实门禁改变**：Catodayo 的 playerdata 被重置（位置回到出生点，背包与进度一并重置，
   已备份）；live DB 记录也随门禁增长（见 §6.2 末尾）。
4. `minecraft_runtime` 套件本轮 `ALL CHECKS PASSED`（exit 0）。该套件内**既有**、与本阶段无关的
   SKIP 记录见 Phase 7F.2.2.1（假服务器不支持 `/summon`，造不出真实掉落物实体）。
5. 首次把 B 段设成「向下」是**设计失误**（未考虑真机门禁之间的世界状态耦合）。已按 §9.1 修正并
   保留记录，供后续阶段注意：**真机门禁会改变世界状态，执行顺序与副作用必须显式考虑。**

