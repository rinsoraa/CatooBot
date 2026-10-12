# Phase 7F.5.3 Follow-up 2 — Event Settlement & Ambiguous Reconciliation Serialization

基线：Phase 7F.5.3 Follow-up 最终发布 68946234123e6cb67d04bcba2374a20edf762e5b

本阶段**只修复生命周期状态更新的一致性**，不增加任何 Minecraft 功能、不新增工具、不新增表或迁移。

任务书归档：[docs/specs/CatooBot Minecraft Phase 7F.5.3 Follow-up 2 — Event Settlement & Ambiguous Reconciliation Serialization.md](specs/CatooBot Minecraft Phase 7F.5.3 Follow-up 2 — Event Settlement & Ambiguous Reconciliation Serialization.md)
（7955 bytes / 146 行，SHA256 081435591CBEC9681B2D40EFCE3F33CEECF1BACBADA3B3ABFA7800F72F88D617，与上传源逐字节一致）

---

## §1 两个审计发现的根因与精确交错

### §1.1 发现 A：on_action_event() 未参与生命周期锁

**根因**：事件处理路径会（a）读取 TaskRecord、（b）修改步骤与 pending 关联、（c）把**整份 payload** 写回，
但全程**不持有** cancel()/expire()/_settle() 共用的那把按任务生命周期锁。而 TaskRecord 在存储层是
**一整列 payload**（ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload），所以"写回整份"
就是"用我手里这份快照覆盖磁盘上的那一份"。

精确交错（修复前）：

    事件协程                                              取消协程
    T1  find_by_action(act_1, include_terminal=True)
        -> 记录 R（WAITING_ACTION / ACCEPTED，action_id=act_1，gen=1）
    T2                                                    cancel() 取锁 -> 读最新 R
                                                          -> 落盘 CANCELLED（保留真实 pending 关联）
                                                          -> 出锁
    T3  改内存里的 R：step -> SETTLED/SUCCEEDED，pending = ""
    T4  _save(R)  ← 整份 payload 覆盖
        => CANCELLED 被写回 WAITING_ACTION/RUNNING：**任务复活**；
           或者真实 action_id / pending 关联被清掉：迟到终态事件再也匹配不到这个任务，
           动作从此脱离生命周期管理。

**修复**（与 §三 的三段式一致，锁 = 串行化，锁内重查 = 数据新鲜度）：

    事件协程（修复后）                                    取消协程
    T1' 无锁探测候选（只为决定要给哪些任务加锁）
    T2' 取**该任务**的生命周期锁（多重匹配时按 task_id 排序，逐把加锁，锁内绝不取第二把）
    T3' 锁内**重新查询** find_by_action(action_id, include_terminal=True)
    T4' 重新验证：匹配数量 / 步骤（pending_step_id）/ 派发代次 / 关联键 / Runtime 实例身份
    T5' 若 target.state.terminal（**从最新记录读**）-> 只收敛步骤证据 + 清 pending，
        **绝不** _enter(RUNNING / WAITING_ACTION / PAUSED)、**绝不** drive()
    T6' 出锁 -> _flush_pending_stops()（锁外 SAFE stop）
    T7' 需要续跑时 drive() 在**锁外**执行（它可能真的发世界动作，持锁会把取消挡整个动作时长）

重查后匹配消失时**区分三种情形，绝不永久丢事件**：
* 已在"已结算 action_id"环里 -> 重复终态事件，幂等空操作；
* 否则 -> 早到/未知事件，重新进有界暂存区，等真实关联建立后立刻消费。

### §1.2 发现 B：_pause_ambiguous_dispatch() 持锁却用调用方的旧快照

**根因**：包装方法取得锁之后，把**调用方传入的** record/step 直接交给内部方法。
**锁只保证互斥，不保证数据新鲜度** —— 调用方是在取锁**之前**读的记录。

精确交错（修复前）：

    调用方                                                取消协程
    T1  record = _require(id)   （RUNNING / WAITING_ACTION）
    T2'                                                   cancel() 取锁 -> 落盘 CANCELLED -> 出锁
    T3  _pause_ambiguous_dispatch(旧 record, 旧 step)
    T4  取锁成功
    T5  按**旧快照**判断 record.state.terminal == False -> record.state = PAUSED
        -> 清授权 / replan_required / _save
        => CANCELLED 被改写成 PAUSED：**终态丢失**，而且是在"用户已经取消"之后。

**修复**：

    进锁后：
    1. _store.load(task_id) **重新读取**最新持久化记录；
    2. 用 step_id **重新定位**当前步骤；
    3. **单调性守卫**（陈旧回调一律丢弃，不改任何状态）：
         · 步骤已不存在（计划被替换 / 重规划）；
         · 持久化代次 > 快照代次（新一轮派发已经开始）；
         · 关联键两边都非空且不同（不是同一次派发）；
         · store 里已是 SETTLED 而调用方快照**不是** SETTLED（真的被更新的事实替换掉了）；
    4. 终态判定**一律看最新记录**（明确不看调用方传入的 terminal 布尔值）：
         终态 -> 保持真实终态，只记录仍有效的歧义证据（terminal_state_preserved=true）；
    5. 非终态 -> 保留既有 fail-closed 语义（FAILED + DISPATCH_AMBIGUOUS + 清授权 +
         replan_required + 保留真实 action_id 的未决关联）；
    6. SAFE stop **只排队**（_defer_stop），由最外层出锁后执行（§2.3）。

### §1.3 更广的同类隐患：把"旧快照覆盖新事实"在**唯一写入口**一次堵住

覆盖清单（§2）显示还有多条转换路径**无法**持锁完成（_run_step 里有外部调用；_finish/_fail/
_settle_blocked/pause/resume/recover/replan 各自读-改-写）。但它们**都**经过同一个写入口
app/tasks/runtime.py 的 _save()（全仓 grep 证实：self._store.save( 在生产代码里**只出现一次**，
就在 _save 里）。

因此在该点加「**终态吸收**」：

* 写之前、在**同一把按任务锁内**复核持久化状态（检查与写入同临界区，不存在 TOCTOU 窗口）；
* 若持久化已是 CANCELLED/EXPIRED 而调用方想写非终态 -> **采纳终态真相**（状态/授权/pending
  关联/恢复证据），**保留调用方想写的步骤证据**，并把被取代的旧证据记进
  recovery.detail.superseded_by_terminal_state / superseded_evidence（绝不静默丢信息）；
* 于是 CANCELLED/EXPIRED 之后，**任何**旧快照都不可能把任务写回非终态。

这一条与 §1.1/§1.2 的关系：前者是"事件/歧义路径"的定点修复，后者是"所有路径"的兜底。

---

## §2 所有状态修改入口与生命周期锁的覆盖清单

### §2.1 直接持有的入口（锁内完成读-改-写）

| 入口 | 锁位置 | 说明 |
| --- | --- | --- |
| _save() | runtime.py 2990 起 | **唯一写入口**；每次写入前在同一把锁内做终态吸收 |
| _settle() | 1021 | 加锁外壳（可重入则直接进），出锁后 flush 被推迟的 stop |
| _pause_ambiguous_dispatch() | 1303 | 加锁外壳（可重入则直接进），出锁后 flush |
| on_action_event() | 2155 起 | 每个命中任务各持一次锁；drive() 在锁外 |
| _ambiguous_event_for_task() | 2173 | 多重匹配时按 task_id 排序逐把加锁 |
| cancel() | 2496 起 | 三段式：锁内落盘终态 -> 锁外 stop -> 再锁内只合并证据 |
| expire() | 2540 起 | 同上（并删除了 7F.5.3 遗留的 19 行死代码） |
| _merge_stop_evidence() | 1580 起 | 锁内重读，只合并停止证据（**不要求终态**，覆盖 PAUSED 路径） |

### §2.2 继承锁（由调用方持锁）

_settle_locked()、_record_settlement_under_pause()、_pause_ambiguous_dispatch_fresh()、
_on_action_event_locked()、_pause_ambiguous_for_action()。

### §2.3 未持锁、由 _save 终态吸收兜底的入口

create_task、confirm_and_start、_run_step（:880/:890/:935/:988 等校验/授权/no-progress 失败）、
_settle_blocked、_pause_after_failure、_begin_replanning、_expire_authorization、_fail_leg、
_ensure_legs_verified、_finish、_cancel_record、_fail、pause、resume、recover、replan。

**为什么这样分**：这些路径要么内含外部调用（_run_step 的 _invoke_step），要么是 drive() 循环内的
局部失败处理，把它们整段包进锁会让"取消"被一个真实世界动作挡在后面。它们的共同点是都经过 _save，
所以把"不许把终态写回非终态"放在写入口，既保证安全又不动它们的时序。

### §2.4 如实记录：仍然存在的**锁内 await**

| 路径 | 等待边界 | 为什么不重构 / 为什么安全 |
| --- | --- | --- |
| _begin_replanning() -> reconcile_task()（SAFE 只读世界查询） | 桥接 HTTP 调用自带超时（MinecraftRuntimeClient timeout） | 只读、不产生任何世界动作；由 _settle_locked/_on_action_event_locked 在锁内调用。它是**只读对账**，不影响"是否放行动作"的安全判定；身份对账重入有 _identity_reconcile_inflight 守卫（重入直接返回，不递归等待），因此结构性无死锁。有确定性重入测试。 |
| _merge_stop_evidence()（锁内 _save） | 本地 SQLite 写 | 纯本地写，无网络等待。 |

SAFE stop（minecraft_stop）**不再**出现在任何持锁路径里：_pause_ambiguous_dispatch_fresh 与
_record_settlement_under_pause 都只 _defer_stop()，由最外层出锁后 _flush_pending_stops() 执行，
再回锁内 _merge_stop_evidence() 只合并停止请求证据。stop_confirmed 在没有真实停止证据时恒为 False。

---

## §3 新增测试对应的具体证明

新增文件 tests/test_task_lifecycle_event_race.py（**9 项**，全部确定性：可控 store 门闩 +
asyncio.Event + 真实 SQLite，无 sleep 同步）。

| 用例 | 证明什么 |
| --- | --- |
| test_event_that_read_a_stale_record_cannot_resurrect_a_cancelled_task | 场景 1：事件先读到旧记录、取消先完成 -> 终态仍 CANCELLED、步骤证据照样收敛、pending 清空、零世界动作、drive() 不复活、重复终态事件幂等 |
| test_event_first_then_cancel_never_downgrades_the_settled_step | 场景 2：事件先在锁内结算、取消随后 -> CANCELLED，已 SETTLED 的步骤不被降级、真实 action_id 保留 |
| test_a_stale_ambiguity_callback_never_downgrades_a_settled_step | 场景 5：SETTLED 单调性 —— 陈旧歧义回调不改状态、不写 failure |
| test_ambiguity_pause_waiting_for_the_lock_keeps_the_terminal_state | 场景 3：歧义处理**等锁**期间取消先写终态 -> 保持 CANCELLED（不出现 PAUSED）、歧义证据完整、replan_required 不置位、stop_confirmed=False |
| test_identity_probe_racing_a_cancel_never_pauses_or_dispatches | 场景 4：_resolve_dispatch_identity 等身份探测时取消 -> 终态保持、零世界动作、关联键/代次可审计 |
| test_event_race_survives_a_real_sqlite_round_trip | 场景 6：真实 SQLite + **全新 store 实例**回读事件竞态结论 |
| test_ambiguity_race_survives_a_real_sqlite_round_trip | 场景 6：真实 SQLite + 全新 store 实例回读歧义竞态结论（含 find_by_action 仍可匹配终态记录） |
| test_an_unlocked_old_snapshot_can_never_downgrade_a_terminal_state | §1.3：未持锁路径的旧快照落盘时被终态吸收，并留下 superseded_by_terminal_state 审计痕迹 |
| test_terminal_absorption_keeps_step_evidence_and_linkage | §1.3：吸收时终态与真实关联保留，调用方想写的**步骤证据照样落盘** |

同时按新语义**收紧**了两处既有测试（旧断言依赖被修的缺陷）：
tests/test_task_runtime.py 的 replan 类用例改为用 paused_task() 走合法路径造非终态任务
（此前是"把已经跑完 SUCCEEDED 的记录硬改 PAUSED 再落盘"—— 那正是本阶段禁止的旧快照覆盖）。

既有相关套件全部回归通过（9 + 296 项，见 §4）。

---

## §4 门禁结果（全部实测）

| 门禁 | 结果 |
| --- | --- |
| ruff check . | All checks passed |
| ruff format --check . | 664 files already formatted |
| mypy app | Success: no issues found in 336 source files |
| 全量 pytest tests -q | **3863 passed in 662.65s**（基线 3854 + 新增 9，精确吻合） |
| 相关套件（9 + 296） | 全绿 |
| WebUI typecheck / Vitest / build / E2E | exit 0 / 75 files 528 passed / built in 9.48s / 7 passed (24.1s) |
| minecraft_runtime npm test | ALL CHECKS PASSED（含 bridge auth boundary + runtime instance 真实鉴权段） |
| 迁移演练（真实 SQLite 副本） | version [36,36] 幂等、integrity [ok,ok]、tables [83,83]、added_tables []、lost_rows {}、ok=false（已最新版的预期 no-op）；quick_check ok、外键违规 0、84 张表（83 业务 + sqlite_sequence）、schema_migrations 36 行 |
| 真机前备份 | CatooBot_backups/catoobot-20261013-045933.db（v36 / 83 表 / 89096 行 / integrity=ok） |

**无新表、无新迁移**（最高仍 36）；未引入自动重发、无界重试。

---

## §5 真实 Fabric 验证结果（Java 1.21.1，真实服务器）

前置：停 CatooBot（python 4936 + node 11360）释放唯一 runtime 会话 -> 25580/8500 释放、25565 在线。
顺序（显式考虑副作用，避免前一段留下的未决动作污染后一段）：
feedback -> explore --sections all -> cancel_inflight -> runtime_restart_identity。

| 门禁 | 结果 |
| --- | --- |
| scripts/explore_smoke_real.py --sections all | **PASS**（单段正向 SUCCEEDED；不可达目标安全停止且不冒充到达；导航中取消 -> CANCELLED + 经 minecraft_stop 真停 + 驱动终态任务不再移动） |
| scripts/cancel_inflight_smoke_real.py | **PASS 19/19**：真实在途取消保留真实 action_id 可审计、stop 请求证据 stop_confirmed=False（请求过 stop != 已停止）、真实终态事件到达后未决关联收敛、保持 CANCELLED、步骤派发状态收敛为 SETTLED、**世界动作次数始终 1**、再驱动终态任务也不产生新世界动作 |
| scripts/runtime_restart_identity_smoke_real.py | **PASS 20/20**：实例变化被真实健康检查路径观测到 -> 旧动作 fail-closed 歧义暂停（证据含旧实例 A、真实 action_id、派发关联键、派发代次、replan_required=True）-> **Runtime 恢复后没有重新派发（世界动作计数仍 1）** -> 第二个新任务停在自己的确认门、不绕过 |
| scripts/feedback_smoke_real.py | **FAIL 1 项**（既有失败，与本阶段改动无关，见 §5.1） |

真机后 live DB：integrity ok / quick_check ok / 外键违规 0 / 84 张表（83 业务）/ 最高迁移 36；
业务计数 life_intents 122 -> 148、activity_episodes 217 -> 249、task_proposals 42、agent_task_runs 228。
环境已恢复：CatooBot 运行中（python 36584 + node 33700），25580 / 8500 / 25565 全部在线。

### §5.1 feedback 门禁失败的根因与独立性证据（如实记录，未放宽任何断言）

**症状**：探索任务 SUCCEEDED 且 verification.ok=True、真记忆写入 6 条（4 条 actionable LOCATION），
但候选意图只有 1 条、**exploration_feedback 0 条** -> 回落普通 MINECRAFT_INTEREST ->
断言「有证据的新事实生成了探索形状 LifeIntent」失败。

**复现性**：连续 3 次运行给出完全相同的输出（本阶段代码 2 次、基线代码 1 次）。

**独立性（A/B 反证，最强证据）**：把 app/tasks/runtime.py 换成**已发布基线** 6894623 的版本
（SHA256 ECDB83728D2CBAC15B6391A5C2D2124320D33A78BB20866AE6F52DD6292A97EC，即**零行**本阶段改动），
在**同一台机器、同一数据库、同一门禁**下重跑 -> 同样 FAIL 1 项、同样的 exploration_feedback 0 条。
随后按 SHA256 恢复本阶段版本（7EC74E39E53329DE1A2E480C153D7F22FC511FD5CC330D6B164704C1A0C320F8，校验通过）。

**改动面证据**：相对基线逐文件内容哈希比对，本阶段只改 app/tasks/runtime.py 与
tests/test_task_runtime.py，并新增测试与文档；**app/memory 与 app/initiative 逐字节未变** ——
而失败断言依赖的正是这两个子系统的候选生成输出。

**数据取证（只读副本）**：memories 123 行（active 102 / stale 12 / invalidated 4 / superseded 5）；
内容去重键 content_hash 存在多组重复（最大一组 13 行同 hash）；探索坐标事实确实以 active 落库
（如 id=125 importance=0.65）。即失败发生在**候选生成**侧（内容去重 + 固定槽位窗口），
而不是"没写进记忆"。

**结论**：根因在 app/memory 的内容去重/状态机与 app/initiative 的固定槽位候选窗口（7F.5.3 阶段
已同样记录并独立复现过）。本阶段**未放宽断言、未跳过门禁、未修改数据库**；已列为后续单独修项。

---

## §6 CI

工作流 .github/workflows/ci.yml 两个 job：lint · format · types · tests（ruff / format / mypy / pytest +
WebUI 安装与 Playwright E2E + minecraft_runtime npm test）与 webui · typecheck · tests · build。

| 项 | 值 |
| --- | --- |
| run ID | **38308796190** |
| URL | https://github.com/rinsoraa/CatooBot/actions/runs/38308796190 |
| attempt | **1**（一次通过；本轮无 flake、无重跑） |
| lint · format · types · tests | completed / **success** |
| webui · typecheck · tests · build | completed / **success** |
| CI 上 pytest | **3860 passed, 3 skipped in 303.87s**（= 本地 3863 − 3 条既有平台条件性 skip；新增 9 项**无一被跳过**） |
| CI 上 minecraft_runtime | find-blocks-test 41/41 ALL CHECKS PASSED；e2e bridge auth boundary + runtime instance ✓；runtime exited cleanly ✓；e2e ALL CHECKS PASSED |
| 实际 SKIPPED 清单（全部为既有假服务器限制，与本阶段无关） | equip / inventory_move STOP（假服务器背包永远是空的）；container STOP（inspect 毫秒级完成）；craft 成功路径（flying-squid 没有 /give）；craft STOP（合成毫秒级完成）；3×3 craft 成功路径（凑不出 8 块木板）；dropped_items / pickup 成功路径（不支持 /summon） |

发布核对：源工作树与发布镜像**逐文件内容哈希 0 差异**（1149 个文件比对）；镜像 == origin/main ==
**697fcdac9c6b02c2cb90b030313927c95c47a8bd**（268 commits，工作树干净）；scripts/ **未发布**（镜像内不存在）；
minecraft_runtime/auth.json **未发布**（镜像内不存在）；config/config.yaml 与 data/ 被 .gitignore 忽略且未 tracked；
改动文件凭据扫描 0 命中；无新表无新迁移。

---

## §7 已知剩余限制

1. **feedback 门禁的既有失败**（§5.1）：候选生成侧的内容去重 + 固定槽位窗口，属 app/memory /
   app/initiative，本阶段未修。这是**已知**遗留项，不是回归。
2. action_id 仍**不是**服务端幂等键、桥接动作 API 仍仅绑 127.0.0.1 且无鉴权（本阶段未扩张边界）；
   **始终不宣称 exactly-once**。
3. 锁内仍有两处 wait（§2.4）：_begin_replanning 的只读对账、_merge_stop_evidence 的本地写。
   前者只读、不产生世界动作、且有确定性重入测试；后者是本地 SQLite 写。
4. 早到事件暂存区与"已结算 action_id"环、"待续跑"登记表都是**进程内有界**结构
   （max_deferred_action_events / max_settled_action_ids / max_pending_stop_requests /
   max_pending_drives），不承诺进程崩溃后保留。

---

## §8 裁决

**PASS。**

依据：§二/§三 的两个审计发现都已定点修复，并且**所有**状态更新路径（含无法持锁的那些）都经过
唯一写入口的终态吸收，因此「旧快照覆盖新事实」在结构上被堵住；§2 的 9 项确定性竞态测试逐条证明
了 §二 要求的 6 个场景与一个兜底场景；§5 的真实 Fabric 门禁证明了动作关联、取消终态、
迟到事件收敛与零重发。

唯一 FAIL 的是既有 feedback 门禁，已用 A/B 反证（基线代码同样失败）+ 改动面哈希证据
（app/memory、app/initiative 未变）+ 只读数据取证证明其与本阶段改动无关，并如实记录为后续修项 ——
**未放宽任何断言、未跳过任何门禁**。

