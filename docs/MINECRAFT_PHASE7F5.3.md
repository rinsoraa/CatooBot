# Phase 7F.5.3 — In-flight Cancellation & Expiry Reconciliation

本阶段只修复**异步动作已经进入真实调用链**时的取消、过期与事件关联安全问题，
不新增任何 Minecraft 游玩能力。

核心不变量：**用户取消或任务过期后，不能因为一个已经开始的异步请求稍后才被 Runtime 接受，
而留下仍在执行、无法停止、或无法被关联的世界动作。**
不宣称 exactly-once；也绝不把「发送过 stop 请求」等同于「动作肯定已经停止」。

- 基线：7F.5.2 代码 `b3014b9fac9b20fa01bb71b0e9fffb93f13d5142`、7F.5.2 最终发布 `e49e3824d16b887cb11dbeec79acebf92bf3548f`。
- 任务书归档：`docs/specs/CatooBot Phase 7F.5.3 — In-flight Cancellation & Expiry Reconciliation.md`
  （6704 bytes / 116 行，SHA256 `797E129B210526A343A49C5B2ADDB79CC56D4D0F93953C205CF7F3BF33EE6698`，与上传源逐字节一致）。

## §1 审计：三个真实缺口（改动前）

§二 要求的六个窗口逐一核对后，确认三处**真实缺陷**（其余窗口当时已由 7F.4/7F.5.2 覆盖）：

1. **终态任务无法被终态事件匹配。** `find_pending_by_action()`（两个存储实现）都走
   `list_pending()`，而后者用 `state NOT IN (四个终态)` 过滤 —— 于是「取消/过期之后才被
   Runtime 接受、并被 7F.5.2 的 `_record_settlement_under_pause()` 如实写成 `ACCEPTED` +
   `pending_action_id`」的那条动作，**永远匹配不到它的终态事件**：终态事件被静默丢弃，
   步骤的 `dispatch_state` 永远停在 `ACCEPTED`、`pending_action_id` 永远挂着。
   这正是任务书 §三.A「终态任务不能仅因终态状态而使其尚未结算的真实动作关联永久失去事件匹配能力」。
2. **`cancel()` 无条件切断在途关联。** 它在 `_stop_action()` 之后**无条件**清空
   `pending_action_id` / `pending_step_id`，并把 `WAITING_ACTION` 的步骤直接标成 `CANCELLED`。
   两个后果：① 连"按 action_id 匹配"的依据都没有了；② 那个 `CANCELLED` 是**假象** ——
   外部动作可能仍在跑（§三.B「不能让该动作脱离生命周期管理」）。
3. **事件先于 `action_id` 落盘到达时永久丢弃。** §二窗口 6：`on_action_event()` 匹配不到就
   `return None`，事件（真实终态）就此消失，事后也再没有任何机会收敛这一步。

另有一处语义缺口：`_pause_ambiguous_dispatch()` 会**无条件**把 `record.state` 置为 `PAUSED` ——
对已经是 `CANCELLED`/`EXPIRED` 的任务，这等于把终态改写成另一种状态。

## §2 实现

### §2.1 终态任务同样可被动作匹配（存储层）

`app/tasks/store.py`：新增 `find_by_action(action_id, *, include_terminal=False)` 与
`list_outstanding(limit=None)`，两个存储实现都提供：

- `find_by_action(..., include_terminal=True)`（InMemory `:106`、Sqlite `:252`）：**含终态**的
  精确匹配。Sqlite 侧先用 SQL `LIKE` 对 payload 做**廉价文本预筛**、再在 Python 侧精确比对
  （`pending_action_id` 只存在于 payload 里、表没有这一列；action_id 只含 `[a-z0-9_]`，
  不含 LIKE 通配符，因此预筛不会漏匹配）。**不加列、不迁移。**
- `list_outstanding()`（InMemory `:127`、Sqlite `:281`）：返回**任何状态**（含终态）里仍有
  `INTENT`/`ACCEPTED` 步骤的记录，供身份对账使用（原先用 `list_pending()` 会把
  「终态 + 仍未结算的动作」漏掉）。
- `find_pending_by_action()`（`:102` / `:248`）保留原语义（仅非终态），内部委托新方法。
- 协议（`:38` / `:42`）同步声明新方法；`TaskRuntime.on_action_event()` 改用 `include_terminal=True`。

### §2.2 终态绝不被改写

- `_pause_ambiguous_dispatch()`（`app/tasks/runtime.py:1210`）：`record.state.terminal` 为真时
  **不改变终态**（不置 `PAUSED`、不设 `replan_required`），但仍如实记录歧义证据；
  如果这一步已经拿到**真实 `action_id`**，则**保留** `pending_action_id`（否则终态任务上的
  真实动作会失去事件匹配能力），证据里多记一条 `terminal_state_preserved: true` 与 `task_state`。
  非终态路径行为与 7F.4 完全一致（清授权 + `replan_required` + `PAUSED` + 清 pending）。
- `on_action_event()`（`:1798`）：命中终态记录时，只**收敛步骤证据**
  （`dispatch_state → SETTLED`、步骤终态/状态/结果、清空 pending），绝不 `_enter(RUNNING)`、
  绝不 `drive()`、绝不 `_pause_after_failure()` —— 也就是说，迟到终态事件**永远不会**让一个
  已取消的任务重新进入派发或续跑流程。

### §2.3 取消 / 过期不再切断在途关联

`cancel()`（`:2107`）与 `expire()`（`:2147`）现在先经 `_inflight_step()`（`:1282`）判断
**在途派发**（`INTENT` 或 `ACCEPTED`），不再只看 `pending_action_id` 是否为空（§二明确要求）：

| 在途状态 | 处理 |
| --- | --- |
| 已经拿到**真实 `action_id`** | 保留 `pending_action_id`/`pending_step_id`、步骤留在 `WAITING_ACTION`、写入 `_outstanding_action_evidence()`（含 `stop_after_accept`/`stop_requested`）；终态事件随后可匹配并收敛 |
| 只有 `INTENT`（外部是否已接受**未知**） | **保留 INTENT 与证据**，让 `_settle` 有机会拿到真实 `action_id` 并再停一次；绝不假装"什么都没发生" |
| 没有在途派发 | 沿用原行为（步骤标 `CANCELLED`、清空 pending） |

`_outstanding_action_evidence()`（`:1296`）复用**既有 payload 的 `recovery` 字段**（不新增 schema、
不迁移）记录：`dispatch_key`、`dispatch_generation`、`dispatch_state`、`runtime_instance`、
`action_id`、`pending_action_id`、`stop_requested`、`stop_after_accept`、
`stop_after_accept_requests`、`stop_confirmed`、`at`。于是**从持久化记录就能判断**：动作是否已确认
终结（`SETTLED`）、是否仍需对账（`INTENT`/`ACCEPTED`/`AMBIGUOUS` + pending 非空）、
是否仍需要停止（`stop_confirmed: false`）。

### §2.4 拿到真实 `action_id` 之后**再停一次**

`_record_settlement_under_pause()`（`:1040`）在「调用返回时任务已经不是 `RUNNING`」时：

- **`detached`（Runtime 已接受）** → 落盘真实 `action_id`、实例、代次、`ACCEPTED`，**保留 pending**，
  然后**再经既有 SAFE 控制面 `_stop_action()` 停一次**（取消/过期时那一次 stop 可能先于动作创建），
  并在证据里写 `stop_after_accept: true`、`stop_after_accept_requests += 1`、`stop_confirmed: false`
  与说明文字。**停不掉不改变结论**，只是留给人工核对 —— 绝不假装已经停止。
- **`ambiguous`（结果未知）** → 走 `_pause_ambiguous_dispatch()`（终态度保留）如实记录歧义证据，
  **不重发**。
- **明确答复（成功 / 明确失败）** → `SETTLED`，如实收敛（这正是"请求已有定论"的情形）。

`_stop_action()` 走的是既有 `minecraft_stop` SAFE 控制面（`_invoke` 空 `task_id` 分支），
**不经过**步骤授权器，也没有新增任何世界动作能力。

### §2.5 先到事件的暂存与消费（§二窗口 6）

`_defer_action_event()`（`:1757`）：`on_action_event()` 匹配不到时，把事件**暂存**在
`self._deferred_action_events`（有界 FIFO，上限 `TaskConfig.max_deferred_action_events = 50`），
重复事件只保留最新一份。

`_consume_deferred_action_event()`（`:1777`）：**一旦这一步拿到真实 `action_id`**（无论走正常
`_settle` 还是 `_record_settlement_under_pause`），立刻消费早到的事件（消费一次即移除），
于是它**不会被永久丢弃**。

`_remember_settled_action()`（`:1791`）维护一个有界的「已结算 action_id」环
（`max_settled_action_ids = 200`）：**重复终态事件是幂等空操作** —— 既不改变任何状态，
也不会被误当成「未知事件」塞进暂存区。

### §2.6 不加锁、不引入死锁（延续 7F.5.2 的论证）

本阶段**没有**引入任何锁或派发租约。多出来的那次 stop 仍走既有 SAFE 控制面，而
`对账 → _pause_ambiguous_dispatch → _stop_action → 健康检查 → 身份变化回调 → 对账` 这条
**真实存在**的重入链仍由 7F.5.2 的「只打标记、绝不等待」再入保护挡住（无等待原语 ⇒ 无死锁）。

### §2.7 六个窗口 → 本阶段处理

| §二 窗口 | 处理 |
| --- | --- |
| 1. `INTENT` 已落盘、尚未开始调用 | 由 7F.5.2 的落盘前复核覆盖；取消/过期此时走 §2.3 第二行（保留 INTENT 让 settle 收敛） |
| 2. 复核通过、HTTP 已发出、Node 未接受 | 取消/过期 → 结果返回时由 §2.4 收敛（真实 action_id + 再停一次） |
| 3. Node 已接受、响应在途、`action_id` 未落盘 | 同上；**不得**假设先前那次 stop 已经停住它 |
| 4. `cancel()`/`expire()` 与 `stop_action()` 交错 | §2.4 的"接受后再停一次" + 证据计数 |
| 5. 响应返回时任务已 `CANCELLED`/`EXPIRED` | §2.4 + §2.2（终态度保留、只收敛证据） |
| 6. 终态事件先于 `action_id` 落盘 | §2.5 暂存 + 消费 + 幂等 |

## §3 变更文件

| 文件 | 变更 |
| --- | --- |
| `app/tasks/store.py` | 新增 `find_by_action(include_terminal=)`（协议 `:38`、InMemory `:106`、Sqlite `:252`）与 `list_outstanding()`（协议 `:42`、InMemory `:127`、Sqlite `:281`）；`find_pending_by_action` 委托新方法 |
| `app/tasks/runtime.py` | `TaskConfig` 新增 `max_deferred_action_events` / `max_settled_action_ids`（`:107`）；`__init__` 新增暂存区与已结算环（`:290`）；`_record_settlement_under_pause` 重写（`:1040`，接受后再停一次 + 完整证据）；`_pause_ambiguous_dispatch` 终态度保留（`:1210`）；新增 `_inflight_step`（`:1282`）与 `_outstanding_action_evidence`（`:1296`）；新增 `_defer_action_event`（`:1757`）/`_consume_deferred_action_event`（`:1777`）/`_remember_settled_action`（`:1791`）；`on_action_event` 含终态匹配 + 终态度保留（`:1798`）；`cancel`（`:2107`）/`expire`（`:2147`）保留在途关联 |
| `tests/test_inflight_cancel_expire.py` | **新增** 10 项确定性故障注入（含真实 SQLite 回读） |
| `tests/test_task_runtime.py` | `test_l_...` 更新为 7F.5.3 语义（终态保持 + 证据收敛），并补 `DispatchState` 导入 |
| `tests/test_agent_plan_follow.py` | 取消后的步骤断言更新为 7F.5.3 语义（仍受管理，而非假象的 CANCELLED），并补 `DispatchState` 导入 |
| `scripts/cancel_inflight_smoke_real.py` | **新增**真实在途取消门禁（本地专用，不进发布） |

**无数据库迁移、无 schema 变更、无新增工具。**

## §4 测试与门禁

### §4.1 新增测试：`tests/test_inflight_cancel_expire.py`（10 项，全部确定性）

交错注入用可控调用替身 + `asyncio.Event` 门闩，把调用精确停在「请求已发出、响应还没回来」的
窗口（§二窗口 2/3），**不依赖随机 sleep**。

| 用例 | 覆盖 | 关键断言 |
| --- | --- | --- |
| `test_cancel_while_request_in_call_chain_keeps_action_managed` | §四.1（窗口 2） | 取消后**迟到的接受**被如实落盘：`CANCELLED` 保持、步骤 `WAITING_ACTION`+`ACCEPTED`、真实 `action_id`、`pending_action_id` 保留、`stop_after_accept`、**接受后再停一次**、`stop_confirmed: false`；世界动作 **1** 次；随后真实终态事件成功收敛（`SETTLED`、pending 清空） |
| `test_expiry_in_the_same_window_keeps_action_managed` | §四.2（窗口 2） | 与取消**分别**测试；过期任务同样保留关联 + 再停一次 + 不重发 |
| `test_cancel_after_accept_before_response_does_not_assume_stopped` | §四.3（窗口 3） | **不得**只靠先前那次 stop 就假定已停止：`stop_after_accept is True`、`stop_confirmed is False`、真实 stop 计数 +1 |
| `test_expire_after_accept_before_response_does_not_assume_stopped` | §四.4（窗口 3） | 同一窗口的**过期**语义，独立用例 |
| `test_event_arriving_before_action_id_is_not_lost` | §四.5（窗口 6） | 早到事件先被暂存（当次返回 `None`），`action_id` 一落盘**立刻被消费**并收敛（`SETTLED`/`SUCCEEDED`）；**重复事件幂等**（不再变化 `event_seq`） |
| `test_cancel_stop_and_late_event_stay_consistent` | §四.6 | 任务终态 / 步骤证据 / 停止证据 / 外部动作次数**四者互相一致**；终态事件不需要也不得再发 stop |
| `test_unknown_dispatch_outcome_never_redispatches_under_terminal_state` | §四.7 | 未知结果：**不重发**；保留 `dispatch_key`、代次、实例、歧义原因与时间、`terminal_state_preserved`；清授权；终态保持 `CANCELLED` |
| `test_late_terminal_event_for_cancelled_task_never_resumes_dispatch` | §四.8 | 迟到事件让证据收敛（步骤 `SUCCEEDED`）但任务**保持 `CANCELLED`**、pending 清空；再 `drive()` 不产生新动作 |
| `test_inflight_cancel_survives_a_real_sqlite_round_trip` | **真实 SQLite 回读** | 用**全新 store** 从数据库回读：`CANCELLED`、`pending_action_id` 保留、步骤 `ACCEPTED`、真实 `action_id`、代次 1、`stop_after_accept`；并验证 `find_by_action(include_terminal=True)` 能命中终态记录、`include_terminal=False` 命中不到 |
| `test_inject_sqlite_store_is_usable` | 共享夹具 | 7F.5.2 的 SQLite 注入替身仍可复用（两套门禁夹具一致） |

每个用例都断言 `task.state`、`step.state`、`dispatch_state`、`dispatch_generation`、`action_id`、
`pending_action_id`、`authorization`、`replan_required`、**真实动作启动次数**、**停止调用次数**，
以及存储回读结果。

### §4.2 既有测试的语义更新（收紧，不是放宽）

本阶段**故意**改变了两个旧语义，因此两处既有断言必须同步更新（都**没有**放宽任何安全断言）：

1. `tests/test_task_runtime.py::test_l_cancel_stops_the_action_and_ignores_late_completion`
   —— 旧断言是「已取消任务的迟到 completed **返回 None（被丢弃）**」，这正是缺口 1；
   现改为「事件**必须被处理**且**绝不改变终态**」，并新增 pending 收敛 / `SETTLED` / 步骤终态的断言
   （函数名同步改为 `..._late_completion_never_undoes_it`）。
2. `tests/test_agent_plan_follow.py` 的取消段 —— 旧断言是「取消后步骤立刻 `CANCELLED`」，
   这正是缺口 2 的**假象**；现改为断言「仍旧受管理」：步骤 `WAITING_ACTION` +
   `dispatch_state == ACCEPTED` + `pending_action_id` 保留。

### §4.3 本地门禁

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| Lint | `ruff check .` | **All checks passed!** |
| Format | `ruff format --check .` | **659 files already formatted** |
| Types | `mypy app` | **Success: no issues found in 336 source files** |
| 全量测试 | `pytest tests -q` | **3846 passed in 505.48s**（上一阶段基线 3836 + 本阶段新增 **10** 项，精确吻合） |
| WebUI types | `npm run typecheck` | exit 0 |
| WebUI unit | `npm test`（Vitest） | **528 passed** |
| WebUI build | `npm run build` | ✓ built in 6.88s |
| WebUI E2E | `npm run test:e2e` | **7 passed (19.4s)** |
| Minecraft runtime | `npm test` | **ALL CHECKS PASSED**（exit 0；套件内既有 SKIP 为假服务器限制） |
| 迁移演练 | `scripts/migrate_db.py --db <副本> --json` | `integrity [ok,ok]`、`tables [83,83]`、`added_tables []`、`lost_rows {}`、`ok:false`（已最新版的**预期 no-op**）。**无新表、无新迁移。** |

真机前已备份 live DB：`CatooBot_backups\catoobot-20261013-005743.db`（v36 · 83 表 · 88705 行 · integrity=ok）。

### §4.4 真实 Fabric 门禁

前置：MC 服务器一度不在运行，已按用户授权用 `start.bat` 的同一命令重新拉起（25565 起来后再跑）；
停 CatooBot 释放 runtime 唯一会话（25580/8500 释放）；`PYTHONIOENCODING=utf-8`；
跑完已按原方式（**全局** Python 3.12 + `python .\run.py`）重启并确认三端口恢复。

| 门禁 | 结果 | 关键读数 |
| --- | --- | --- |
| ① `scripts/explore_smoke_real.py --sections all`（7F.1 回归） | **PASS** | 单段 `SUCCEEDED`（距 **1.60**、位移 3.8、无遗留移动）；不可达目标安全 `PAUSED`（不冒充到达）；导航中取消 → `CANCELLED` + 真停 + 驱动终态任务不再移动 |
| ② `scripts/runtime_restart_identity_smoke_real.py`（7F.5.1 回归，**与在途动作直接相关**） | **PASS（20/20）** | 真实实例 A → 真实动作派出（世界动作 1 次）→ 停 Node（Python 不退）→ 外部重启 → 真实健康检查发现实例 B → 旧动作 fail-closed `PAUSED`/`DISPATCH_AMBIGUOUS` + 证据完整 + 授权已清 → **零重发**（计数仍 1）→ 第二个新任务不越过确认门 |
| ③ **`scripts/cancel_inflight_smoke_real.py`（本阶段新增）** | **PASS（19 项）** | 真实起点 `(-255.5,64,-122.5)` → 真实动作 `act_mv5i1b72_1` 已接受（`WAITING_ACTION`、世界动作 1 次、`dispatch_state=ACCEPTED`）→ `cancel()` → 任务 `CANCELLED`，动作**要么仍在管理之下、要么已由真实终态事件收敛**（本次是后者：刚发的 stop 让它立刻 `cancelled` 结束）→ 真实 `action_id` 始终可审计、`stop_after_accept` 已记、`stop_confirmed: false`、确实发过 stop → 终态收敛为 `SETTLED`、任务**保持 `CANCELLED`** → **世界动作次数始终为 1**、再驱动终态任务也不产生新动作 |
| ④ `scripts/feedback_smoke_real.py`（7F.2） | **FAIL（1 项，非本阶段引入；见 §4.5）** | 任务 `SUCCEEDED` + 到达复核 `ok=True` + 真记忆（actionable 若干）；仅「有证据的新事实生成了探索形状 LifeIntent」失败 |

### §4.5 7F.2 的失败：与 7F.5.2 同一既有条件（第二次出现）

7F.2 这一次仍然只失败**同一项**，且根因与 `docs/MINECRAFT_PHASE7F5.2.md` §4.5 记录的**完全相同**：

- 探索落点/兴趣点与**历史**探索重合（locational POI 坐标逐字相同），于是记忆写入被**内容去重**
  合并回**已有行**；那些行的状态已是 `superseded` / `stale`；
- 候选生成器只看 `status=active` 的 5 槽窗口（`MAX_MEMORY_SIGNALS = 5`，
  `ORDER BY importance DESC, id DESC`），窗口里因此**没有任何 actionable 条目**，
  于是回落到普通 `MINECRAFT_INTEREST`，`exploration_feedback` 计数为 0；
- **与本阶段无关**：本阶段改动全部在 `app/tasks/{store,runtime}.py`；该判定完全由
  `app/memory`（去重/状态机）与 `app/initiative`（固定 5 槽窗口）在**未改动代码 + 数据库状态**
  下做出 —— 7F.5.2 阶段已用「零改动代码的最小探针」在同一数据库上复现出完全相同的输出
  （`context.memories` 5 条、actionable 全为 False、只有普通 `MINECRAFT_INTEREST`）。
- **未做任何处置**：不改断言、不放宽阈值、不删库；如实记录。

这已是**连续第二个阶段**在同一累积数据库状态下无法通过该项，说明它不是偶发抖动，
而是一个值得**单独修**的既有问题（新事实被去重合并进非 active 行时对候选生成不可见）。
本阶段按边界要求**不扩边界**去改记忆/候选代码，只把它明确列为后续可修项。

### §4.6 CI

<!--CI-->

## §5 安全与产品边界

以下全部不变：不新增 Minecraft 工具（注册仍 **19** 个）、`ACTION_RISK` 不变、
`allow_medium=false` 不变、两道独立确认门继续有效、不绕过 `TaskAuthorization` /
`TaskStepAuthorization` / Policy / AgentBridge / Action Runtime、不加入无界重试、
**不宣称 exactly-once**、不为修复生命周期问题引入新的自动执行能力。
未修改用户真实 Character Bible、`.env`、凭据、`config/config.yaml` 或 `data/`；
`scripts/` 仍只用于本地真机门禁、不进入 GitHub 发布镜像。

新增的"再停一次"**只经既有 `minecraft_stop` SAFE 控制面**，没有新增任何世界修改能力；
所有证据字段都写在**既有 payload** 里（`recovery.detail` 与 `TaskStep.dispatch_*`），
没有新表、没有迁移。

## §6 已知限制（如实记录）

1. **先到事件的暂存是进程内存态**（有界 FIFO，上限 50）：与 Node 侧事件队列一样，
   保证的是「**进程存活期间**不丢」。进程崩溃后事件与关联一起消失，此时任务仍是 fail-closed
   （不会因为丢事件而重发动作），但那条动作的终态只能靠人工核对。
2. **"再停一次"不等于"已经停止"**：证据里明确写 `stop_confirmed: false`；
   没有按键查询能力，无法证明动作真的停了 —— 这正是本阶段不宣称 exactly-once 的原因。
3. **终态任务的在途动作靠真实终态事件收敛**：如果那段动作对应的 Runtime 进程随后重启，
   事件不会再来，记录会一直保留 `dispatch_state=ACCEPTED` + `pending_action_id`（**这正是
   "仍需对账"的持久化信号**），需要人工核对；本阶段没有把终态任务也纳入身份变化对账的
   自动收敛（`list_outstanding` 已经提供了入口，但该路径留待后续阶段按需接入）。
4. 每次真实派发可多出一次 `minecraft_stop`（仅在"取消/过期与派发交错"时），
   这是"不把可能太早的 stop 当成已经停住"的代价。
