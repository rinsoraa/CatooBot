# Phase 7F.5.3 Follow-up — Cancel/Expire Snapshot Race Closure

- 权威基线：Phase 7F.5.3 最终发布 `2eee8311dcb8f8de44a07a8da5cdad4c0ecdc555`
- 起始 SHA：`2eee8311dcb8f8de44a07a8da5cdad4c0ecdc555`
- 任务书归档：`docs/specs/CatooBot Phase 7F.5.3 Follow-up — CancelExpire Snapshot Race Closure.md`
  （7215 bytes / 150 行，SHA256 `B5B0EF8E9EA3ADD26F2BDB6F9ED76D535807E3CBAAA35D6354D5E906270B8FAF`，与上传源逐字节一致）
- 阶段性质：**定向修正**，不新增任何 Minecraft 能力、不放宽任何既有断言、不引入数据库迁移。

## §1 根因与精确交错顺序

### 1.1 代码路径（审计实测）

`cancel()` / `expire()` 在 7F.5.3 的实现顺序是：

1. `record = await self._require(task_id)` —— 从 store **整份读出一份快照对象**；
2. `await self._stop_action()` —— 经既有 SAFE 控制面 `minecraft_stop` 停前台动作，**这是一个长
   await，且当时没有任何串行化**；
3. `inflight = self._inflight_step(record)` —— 在**第 1 步那份旧快照**上判定在途；
4. `self._cancel_record(record, reason)` / `self._save(record, event=TASK_EXPIRED)` —— 把**旧快照
   整份写回 store**。

store 语义（实测，两处都证实）：

- `SqliteTaskStore.save`：`INSERT INTO agent_task_runs (...) VALUES (...) ON CONFLICT(task_id) DO
  UPDATE SET ... payload=excluded.payload` —— **payload 整列覆盖，没有任何版本/乐观并发校验**；
- `SqliteTaskStore.load` / `InMemoryTaskStore._copy`：都是 `TaskRecord.from_payload(json.loads(...))`
  —— 每次 `_require()` 拿到的都是**独立副本**，不是活引用；
- 因此「读快照 → await → 按旧快照算 → 整份写回」= 教科书级的 **lost update**。

### 1.2 被修的那段交错（确定性复现）

| 步 | 协程 A（取消/过期） | 协程 B（结算 `_settle`） | store 里的真相 |
|---|---|---|---|
| 1 | 读快照 `record_A`（`dispatch_state=INTENT`、`action_id=""`） | — | INTENT，无 action_id |
| 2 | `await self._stop_action()` ← **挂在这里** | 动作返回「已接受」 | — |
| 3 | — | `_require()` → RUNNING → 写 `action_id=act_srv_1`、`pending_action_id=act_srv_1`、`ACCEPTED`、`WAITING_ACTION` | **真实关联已落盘** |
| 4 | `_inflight_step(record_A)` 在旧快照上判 → `action_id` 为空 → 走「只是派发意图」分支 | — | — |
| 5 | 整份保存 `record_A` | — | `CANCELLED` + `dispatch_state=INTENT` + `action_id=""` + `pending_action_id=""` ← **关联被抹掉** |

后果：世界动作仍在外部运行，而持久化记录里**再也找不到它** —— `find_by_action` 匹配不到任何
任务，迟到的真实终态事件只能落进暂存区或被丢弃，动作彻底脱离生命周期管理。

### 1.3 修复前 / 修复后的同一探针输出（确定性证据）

探针脚本（一次性，位于本地 `CatooBot_backups/_race_probe.py`，**不进入发布镜像**）用
`asyncio.Event` 门闩把交错固定成 1.2 节的顺序：

修复前（旧顺序）：

```text
MID   state=WAITING_ACTION action_id='act_srv_1' pending='act_srv_1' dispatch=ACCEPTED
FINAL state=CANCELLED action_id='' pending='' dispatch=INTENT recovery='cancel_during_dispatch'
world_actions=1 stop_calls=1
```

修复后（本阶段）：

```text
MID   state=CANCELLED action_id='' pending='' dispatch=INTENT (取消已落盘终态，仍阻塞在 stop)
FINAL state=CANCELLED action_id='act_srv_1' pending='act_srv_1' dispatch=ACCEPTED recovery='cancel_during_dispatch'
      stop_requested=True stop_confirmed=False stop_after_accept=True
world_actions=1 stop_calls=2
```

两次输出都是**实测**：修复前真实 `action_id` 与 pending 关联被旧快照抹掉；修复后同一交错下
关联完整保留、终态保持 `CANCELLED`、并多出一次「接受之后」的停止请求（`stop_calls=2`）。

### 1.4 同源问题（一并修掉）

`_pause_ambiguous_dispatch()` 也是「`await self._stop_action()` 之后整份保存」，同样会把这几秒里
落盘的更新覆盖掉 —— 它现在与取消/过期/结算共享同一把按任务锁（见 §2）。

## §2 修复后的状态转换设计

采用任务书 §3 允许的前两种方式**同时**落地：**按任务串行化** + **终态转换与异步 stop 分离**，
并在 stop 之后用**重新读取 + 只合并**代替整份覆盖。

### 2.1 按任务的生命周期锁（可重入）

`app/tasks/runtime.py` 新增 `_LifecycleLock`：

- 可重入（同一 asyncio 任务重入不自锁：`_owner` + `_depth`），跨协程严格互斥；
- `TaskRuntime._lifecycle(task_id)` 返回该任务的锁，锁表**有界**（≥256 时只回收
  `idle`＝没人持有也没人排队的表项）；
- 进入临界区的只有「读-改-写」三个动作，**外部调用（stop）绝不夹在里面** —— 这正是任务书
  §3.3 要的：安全控制调用与身份观测/对账之间不存在互相递归等待。
- 已接入：`cancel()`、`expire()`、`_merge_stop_evidence()`、`_settle()`（改名 `_settle_locked` 为内层）、
  `_pause_ambiguous_dispatch()`（同样拆出 `_pause_ambiguous_dispatch_locked` 内层）。

### 2.2 cancel() / expire() 变成三段式

```text
① 临界区内：重新读取最新记录 → 按**最新真相**判定在途 → 落盘终态（CANCELLED / EXPIRED）
② 出临界区：await self._stop_action()          # 外部调用不夹在临界区里
③ 再进临界区：重新读取最新记录 → **只合并**停止证据 → 保存
```

关键点：

- 第 ① 步已经把**生命周期真相**落盘（任务先到终态），不再存在「stop 到一半、终态还没写」的窗口；
- 第 ③ 步读的是**那一刻的 store**，所以第 ② 步期间 `_settle()` 写进去的真实 `action_id` /
  `pending_action_id` / `ACCEPTED` **原样保留**，只补 `stop_requested / stop_after_accept /
  stop_after_accept_requests / stop_confirmed / note`（`stop_confirmed` 恒为 `False`：
  「请求过 stop」绝不等于「动作已停止」）；
- 合并用 `detail.get(...) or ...` 与 `max(...)` 语义，**只增不减**：更新的证据永远不会被旧值降级；
- 确定没有在途派发时不编造任何停止证据（这一下 stop 是空操作）。

### 2.3 在途判定抽成 `_mark_inflight_for_terminal()`

对**最新记录**做判定，行为与 7F.5.3 完全一致（只是改在最新快照上执行）：

- 有真实 `action_id` → 保留 `pending_action_id / pending_step_id`，写
  `<cancel|expired>_with_outstanding_action` 证据；
- 只有派发意图（`INTENT`）→ 保留 `INTENT` 与 `<b>_during_dispatch` 证据，让 `_settle()` 拿到真实
  `action_id` 后**再次**走既有 SAFE 控制面停止；
- 都没有 → 按原样清空（cancel 额外把 `WAITING_ACTION` 的步骤标 `CANCELLED`，与旧语义一致；
  expire 不标，保持旧语义）。

## §3 变更文件

| 文件 | 变更 |
|---|---|
| `app/tasks/runtime.py` | 新增 `_LifecycleLock` 类、`TaskRuntime._lifecycle()`、`_mark_inflight_for_terminal()`、`_merge_stop_evidence()`；`cancel()` 与 `expire()` 改成三段式；`_settle()` / `_pause_ambiguous_dispatch()` 拆出 `*_locked` 内层并加锁；`__init__` 增加 `_lifecycle_locks` |
| `tests/test_cancel_expire_snapshot_race.py` | **新增 8 项**确定性竞态用例（见 §4） |

- **无数据库迁移、无新表、无 payload 结构变更**（新增字段一个都没有；`_merge_stop_evidence`
  复用既有 `record.recovery["detail"]`）。
- 未改动：19 工具 / `ACTION_RISK` / `allow_medium=false` / 两道确认门 / TaskAuthorization /
  TaskStepAuthorization / Policy / AgentBridge / ActionRuntime / 多段 ≤3 段与 24 格 /
  `explore_legs=1` 默认值。
- 既有测试**一条都没有放宽**：`tests/test_inflight_cancel_expire.py`（10 项）、
  `tests/test_task_runtime.py`、`tests/test_agent_plan_follow.py` 在新顺序下全部原样通过。

## §4 新增的确定性用例（每个证明了什么）

```text
tests/test_cancel_expire_snapshot_race.py（8 项）
```

| 用例 | 交错形状 | 证明了什么 |
|---|---|---|
| `test_cancel_keeps_action_link_written_while_its_stop_was_pending` | 取消的 stop 挂着 → 结算先写入真实 `action_id` → 再放行 stop | ①取消先落盘终态；②两次 `_save` 之后真实 `action_id / pending / ACCEPTED` 仍然完整（**旧代码在此丢关联**）；③一次真实世界动作、两次 stop（接受后再停一次）；④终态任务不再放行任何步骤；⑤**一旦关联落盘，后续任何一次 save 都不得把它抹掉**（逐次快照断言） |
| `test_expire_keeps_action_link_written_while_its_stop_was_pending` | 同上，换成过期 | 过期是**独立用例**，绝不用取消用例代替；终态保持 `EXPIRED` |
| `test_cancel_in_accepted_but_undelivered_window` | 底层已接受（`action_id` 已生成）、调用方**还没拿到**响应时取消 | 任务书 §2.C 要求的那个窗口：取消时 `dispatch_state` 仍是 `INTENT`、`pending_action_id` 为空（如实留证据，不假装没发生）；随后交付响应 → 关联完整保留、终态不翻案、再 `drive` 不产生新动作 |
| `test_expire_in_accepted_but_undelivered_window` | 同上，换成过期 | 同窗口的过期独立覆盖 |
| `test_before_accept_window_is_a_distinct_scenario` | 请求进调用链、**底层还没接受**时取消 | 证明两个窗口**不是同一个场景改名**：此刻 `accepted` 未置位、**世界动作计数为 0**（对比窗口 2 的 1），且「接受之后再停一次」恰好 1 次 |
| `test_late_true_terminal_event_converges_without_reviving_terminal_task` | 竞态收尾后投真实终态事件（再投一次重复的） | 迟到终态事件只收敛步骤证据（`SETTLED` + 清 `pending`），**绝不复活终态任务**；重复事件是幂等空操作 |
| `test_terminal_event_before_action_id_is_deferred_then_consumed` | 终态事件**早于** `action_id` 落盘 | 事件先暂存（返回 `None`，不静默丢弃），拿到真实关联后立刻消费 → `SETTLED` + `pending` 收敛，终态保持 `EXPIRED` |
| `test_cancel_snapshot_race_survives_real_sqlite_round_trip` | 同一交错跑在**真实 SQLite** 上 | 换一个全新 `SqliteTaskStore` 从数据库完整回读：`CANCELLED` + 真实 `action_id` + `ACCEPTED` + `pending` + 代次 1 + `stop_confirmed=False`，且终态记录仍能被 `find_by_action(include_terminal=True)` 匹配到 |

### 4.1 这些用例确实能抓住旧缺陷（确定性反证）

把 `cancel()` / `expire()` 临时还原成修复前的旧顺序（无锁、stop 在算在途之前、整份保存），
再跑同一个文件：

```text
4 failed, 4 passed
FAILED test_cancel_keeps_action_link_written_while_its_stop_was_pending
FAILED test_expire_keeps_action_link_written_while_its_stop_was_pending
FAILED test_late_true_terminal_event_converges_without_reviving_terminal_task
FAILED test_cancel_snapshot_race_survives_real_sqlite_round_trip
```

失败点是「终态真相必须先落盘」（旧代码把终态留到 stop 返回之后才写）与「stop 调用没有达到 2 次」
（旧代码不会在拿到 `action_id` 之后再停一次）；§1.3 的探针则直接打出旧代码**丢关联**的那一行。
还原修复后同一批用例 **8 passed**，且 `tests/test_inflight_cancel_expire.py` 一并 **18 passed**。

## §5 门禁（全部实测）

| 门禁 | 结果 |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `663 files already formatted` |
| `mypy app` | Success（336 source files） |
| 全量 `pytest tests -q` | **`3854 passed in 505.32s`**（基线 3846 + 新增 8）；格式修正后在最终树上复跑 **`3854 passed in 572.91s`**（同一结果，无 flake） |
| WebUI typecheck / Vitest / build / Playwright E2E | typecheck exit 0 / Vitest **528 passed** / build ✓（8.40s）/ Playwright E2E **7 passed**（20.6s） |
| `minecraft_runtime npm test` | exit 0（ALL CHECKS PASSED；套件内既有 SKIP 与本阶段无关） |
| 迁移演练（副本） | `version [36,36]`、`integrity [ok,ok]`、`tables [83,83]`、`added_tables []`、`lost_rows {}`、`ok:false`（**已是最新版的预期 no-op**）。**无新表无新迁移。** |
| 真实 Fabric 门禁 | 四段真实 Fabric 门禁：**在途取消 PASS（19/19）**、**探索回归 PASS**、**实例重启回归 PASS（20/20）**、**反馈：首次 FAIL 1 项（门禁顺序副作用，已证实）、单独复跑 PASS** |

### 5.1 真实 Fabric 门禁细节

四段真实门禁（真实 Fabric 1.21.1，运行顺序：在途取消 → 探索回归 → 实例重启 → 反馈）：

**① `scripts/cancel_inflight_smoke_real.py` PASS（19/19）** —— 本阶段的关键真机门禁。真实起点
`(-255.5, 64.0, -122.5)`；真实动作被接受并落盘 `action_id=act_mv5lu2uv_1`（`WAITING_ACTION` +
`ACCEPTED`，**世界动作次数 1**）→ 真实 `cancel()` → 任务立刻 `CANCELLED`；「在途动作要么仍在管理之下、
要么已由真实终态事件收敛」（两种都不出现「无人管理」）；**真实 `action_id` 始终留在步骤上可审计**；
`stop_after_accept=True`、`stop_after_accept_requests>=1`、`stop_confirmed=False`（**绝不假装动作已停止**）；
确实经既有 SAFE 控制面发过 stop（1 次）→ 等真实终态事件到达后未决关联收敛（`SETTLED` + 步骤终态记实
`CANCELLED`）→ **绝不重发：世界动作次数仍为 1** → 再驱动终态任务也不产生新世界动作。

**② `scripts/explore_smoke_real.py --sections all` PASS** —— 单段 `SUCCEEDED` 且 `verification.ok=True`
（实际距 **0.86** ≤ 半径 2.0、位移 3.9、任务结束后无遗留移动）；不可达目标 → 安全 `PAUSED/ACTION_FAILED`
（**绝不冒充到达**）；导航中取消 → `CANCELLED` + 经 `minecraft_stop` 真停 + 驱动终态任务不再移动。

**③ `scripts/runtime_restart_identity_smoke_real.py` PASS（20/20）** —— 真实实例 A → 停掉 Node
（Python 不退出，`auto_start_runtime=False`）→ 外部重新拉起 → 真实健康检查发现**新实例 B** →
旧动作 fail-closed `PAUSED/DISPATCH_AMBIGUOUS`、授权已清、`replan_required=True`、证据含
原因/旧实例/真实 `action_id`/派发关联键/代次 → **Runtime 恢复后零重发**（世界动作计数 1）→
第二个新任务停在自己的确认门。

**④ `scripts/feedback_smoke_real.py`：首次 FAIL 1 项，单独复跑 PASS（如实记录）**

第一次按上述顺序运行：反馈链本身**全部通过** —— 6 条真实记忆 → `NEW_FACTS_VERIFIED`（actionable 事实 4 条）→
`exploration_feedback` 意图 1 条 → `MINECRAFT_INTEREST` → TaskProposal（源 LIFE、可追溯 intent_id）→
AgentPlan `READY_FOR_APPROVAL` → 批准后 `PENDING_CONFIRMATION` 且**规划阶段未建执行任务**。
唯一失败项是脚本最后一条 `check(not base._moving(service))` ——「批准后未确认：角色没有产生任何真实动作」。

该项度量的是「**角色此刻是否仍在物理移动**」（`_moving()`），不是动作计数；而紧随其前的 ③ 会留下一个
**未决的真实动作**（歧义暂停、动作仍可能在外跑），角色因此在 ④ 断言时**仍在滑行**。
在干净起点上**单独复跑 ④ → PASS**（同一份代码、同一份数据库、同一套断言未做任何放宽），
证明这是**门禁顺序副作用**，不是本阶段引入的行为回归。它与 7F.5.3 记录的那次 FAIL 属**不同症状**
（那次是记忆内容去重 / 固定 5 槽候选窗口导致 `exploration_feedback=0`；本次反馈链完全正常）。

**教训（与 7F.3 一致）：真机门禁的顺序与副作用必须显式考虑** —— ④ 应排在 ③ 之前，或单独运行。

真机前已备份 live DB：`CatooBot_backups/catoobot-20261013-024735.db`。真机后 live DB：
`integrity ok`、`quick_check ok`、FK 违规 0、84 张表（83 业务 + `sqlite_sequence`）、最高迁移 **36**；
`life_intents` 143、`activity_episodes` 247、`task_proposals` 42、`agent_task_runs` 218。
环境已恢复：CatooBot 运行中（python 36736 + node 28944），25580 / 8500 / 25565 全部在线。

### 5.2 CI

| 项 | 值 |
|---|---|
| run id / URL | `38293848353` —— https://github.com/rinsoraa/CatooBot/actions/runs/38293848353 |
| attempt 1 | `webui · typecheck · tests · build` **success**；`lint · format · types · tests` **failure** —— 唯一失败步骤是 `Minecraft runtime tests (ActionRuntime 单测 + E2E)`，失败点 `[e2e] FAILED: Error: ASSERT FAILED: Test A：夹具 dirt 已放置` |
| 处置 | **绝不改断言 / 超时 / 阈值**；只 `POST /repos/rinsoraa/CatooBot/actions/runs/38293848353/rerun-failed-jobs`（HTTP 201） |
| attempt 2（最新） | **两个 job 均 success，run conclusion = success** |
| CI 上 pytest（attempt 2） | `3851 passed, 3 skipped in 276.59s` —— 与本地 `3854 passed` **精确对账**：差的 3 条是既有平台条件性 skip，本阶段新增 8 项**无一被跳过** |
| CI 上 minecraft_runtime | `[find-blocks-test] 41/41 ALL CHECKS PASSED`、`[e2e] bridge auth boundary + runtime instance ✓`、`[e2e] ALL CHECKS PASSED` |
| 实际 SKIPPED 清单（minecraft_runtime 假服务器限制，全部既有、与本阶段无关） | ① `equip / inventory_move STOP`（假服务器背包永远是空的）② `container STOP`（inspect 毫秒级完成）③ `craft 成功路径`（flying-squid 没有 /give）④ `craft STOP`（合成毫秒级完成）⑤ `3×3 craft 成功路径`（没有 /give，凑不出 8 块木板）⑥ `dropped_items / pickup 成功路径`（假服务器不支持 /summon） |

本次 CI flake 与 7F.5.3 §4.6.1 记录的是**同一类**（假服务器夹具/区块加载滞后），本地 `npm test` exit 0。

## §6 安全边界与已知限制

### 6.1 边界保持不变

- 19 个注册工具、`ACTION_RISK`、`allow_medium=false`、两道独立确认门；
- 不绕过 `TaskAuthorization` / `TaskStepAuthorization` / Policy / AgentBridge / ActionRuntime；
- `minecraft_stop` 仍只经现有 SAFE 控制面调用（绝不直接操纵 Mineflayer）；
- 无无界重试、不自动重放「结果未知」的动作、不宣称 exactly-once；
- 多段 ≤3 段、共享 24 格预算、`explore_legs=1` 默认值不变；
- 未修改 Character Bible、`.env`、凭据、`config/config.yaml`、`data/`；
- `scripts/` 下的真机/诊断脚本**只存在于本地工作目录**，不进入发布镜像。

### 6.2 已知限制（如实记录）

1. **`_settle()` 的「接受后再停一次」仍在锁内**：`_record_settlement_under_pause` 的 detached 分支
   是「先 stop、后 save」，因此这条路径会在持有该任务生命周期锁的情况下发起一次
   `minecraft_stop`。此时并发的 `cancel()` 的**第三次合并保存**会等这次 stop 返回后才拿得到锁 ——
   是**有界延迟、不是死锁**（桥接调用自身有超时；任务书 §3.3 要的"不得与身份观测互相递归等待"
   由可重入锁 + 7F.5.1 的异步回调派发共同保证，并有专门用例覆盖）。
   之所以没有把这次 stop 也挪到锁外：它会改变 7F.5.3 既有的「先停一次、再落盘证据」次序，
   而该次序是上一阶段明确验证过的语义；在**不改变既有语义**的前提下，本次只把**覆盖写**这一
   真正的缺陷路径（cancel/expire 的读-改-写）移出临界区。后续如要消除这点延迟，可把该分支
   同样改成三段式（与 cancel 一致），届时需重跑 7F.5.3 的 10 项在途用例。
2. `action_id` **仍然不是服务端幂等键**：本次修复保证的是「取消/过期不再抹掉真实关联」与
   「绝不因为恢复/取消而再发一次」，**不是** exactly-once。
3. 暂存区（早到事件）与已结算 action_id 环都是**进程内有界**结构（事件上限由
   `max_deferred_action_events` 控制）：进程重启期间到达的事件不保证留存 —— 与 7F.5.3 的记录一致。

## §7 裁决

**PASS** —— 任务书 §八 的四个门槛全部满足，且每一项都有可执行证据：

1. **旧快照覆盖风险已修复**：`cancel()` / `expire()` 改为三段式（终态先落盘 → 锁外 `stop` → 重新读取
   并**只合并**停止证据），并以按任务、可重入的生命周期锁把「生命周期转换 ↔ 结算落盘」串行化；
   §4 的逐次快照不变量（「一旦关联落盘，后续任何一次 save 都不得把它抹掉」）与真实 SQLite 回读用例覆盖。
2. **真正的「已接受、响应尚未返回」窗口有独立测试**：`test_cancel_in_accepted_but_undelivered_window` 与
   `test_expire_in_accepted_but_undelivered_window` 在**底层已接受、调用方还没拿到响应**时触发取消/过期；
   `test_before_accept_window_is_a_distinct_scenario` 用「世界动作计数 0 vs 1」证明两者不是同一场景改名。
3. **cancel 与 expire 各自通过**：两套独立用例（含「stop 未返回时响应先完成结算」的那一场）。
4. **完整门禁有证据支持**：`ruff check` / `ruff format --check`（663 files）/ `mypy app`（336 files）/
   全量 `pytest` **3854 passed**（基线 3846 + 8）/ WebUI typecheck + Vitest 528 + build + E2E 7 /
   `minecraft_runtime npm test` exit 0 / 迁移演练 v36→v36 幂等（83 表、`integrity ok`、`lost_rows {}`）/
   真实 Fabric 四段见 §5.1。

未宣称 exactly-once（`action_id` 仍不是服务端幂等键）；「结果未知」绝不等于失败，绝不自动重发。
§6.2 已如实列出唯一的遗留项（`_settle` 的「接受后再停一次」仍在锁内，属**有界延迟而非死锁**），
并给出最小后续修正方向。
