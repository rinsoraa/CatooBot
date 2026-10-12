# Phase 7F.5.2 — Pre-dispatch Pause Race Closure

本阶段是**定向并发安全修正**，不新增任何 Minecraft 游玩能力：把「用户暂停」与「派发动作」
之间的竞态彻底关掉 —— 暂停之后**一次外部动作都不能发出**，持久化的 `PAUSED` **绝不被**
旧内存快照覆盖回 `RUNNING`，已经发出的调用则**必须有记录**。

- 基线：7F.5.1 代码 `3ba395ec1367878eef152c551a9105116a0fdf84`、7F.5.1 最终发布 `5181e07b7f95f8e7663fdaec23d18b1f0ce175b6`。
- 任务书归档：`docs/specs/CatooBot Minecraft Phase 7F.5.2 — Pre-dispatch Pause Race Closure.md`
  （6413 bytes / 111 行，SHA256 `B737A048B4D90EC73C6845B8FEF901F31EBFD4ADA83B65A212F29ADFD6A452A5`，与上传源逐字节一致）。

## §1 审计：三处真实缺陷（改动前的代码）

任务书点名的三个地方全部实测确认，而且它们**必须一起修**（只补一条判断解决不了问题）：

1. **`_dispatch_precheck()` 不拒绝 `PAUSED`。** 改动前它只判 `fresh.state.terminal`，
   而 `PAUSED` **不是终态** —— 一个刚被用户暂停的任务，它的 `INTENT` 步骤照样能通过复核。
   同时它对非 SAFE 步骤只检查「授权对象是否为 None」，**没有**校验授权有效性、`plan_hash`、
   `plan_version`，也没有检查 `replan_required` / `pause_requested`。
2. **`pause()` 会把任务直接持久化成 `PAUSED`。** 当 `pending_action_id` 为空时（而派发意图
   已落盘、外部请求可能正在路上的那段时间里它正是空的 —— `pending_action_id` 要到 `_settle`
   才写），`pause()` 走「立即暂停」分支：`_enter(PAUSED)` + `_save`。
3. **派发被拒后，`drive()` 仍持有旧的 `RUNNING` 内存对象。** 原来 `_run_step` 在复核失败时
   返回 `"stop"`，而 `drive()` 的循环用的是**进来时 load 的那份** `record`（另一个协程暂停时
   改的是 store 里那一份）：旧对象看不见 `PAUSED` → 下一轮继续推进同一步 → 重新 `_settle`／
   再次 `dispatch_generation += 1`，并把持久化的 `PAUSED` **覆盖回 `RUNNING`**。

> 这正是任务书强调「不能仅在 `_dispatch_precheck()` 里补一条 `PAUSED` 判断就认为问题已经解决」的原因：
> 判断补上了，旧对象照样会把状态写回去，流程照样会继续往下走。

## §2 实现：四条安全不变量

### §2.1 发出动作前必须确认任务仍可执行（统一谓词）

新增 `_dispatch_allowed()`（`app/tasks/runtime.py:354`）：**所有判定都基于从 store 重新 load 的
持久化真相**，返回「(原因码, 持久化快照)」—— 原因码为空串才表示允许派发。被拒绝的原因码有：

| 原因码 | 含义 |
| --- | --- |
| `task.not_running` | **只有 `RUNNING` 明确允许派发**（终态 / `PAUSED` / 等确认 / 等动作 / 重规划 / PLANNING / WAITING_USER 一律拒绝） |
| `task.replan_required` | 旧计划已失效，必须有新计划 + 新确认 |
| `task.pause_requested` | 用户已请求暂停（外部动作计数必须为零） |
| `step.missing` | 这一步不在持久化计划里 |
| `step.not_intent` / `step.dispatch_unresolved` | 落盘后复核要求步骤仍是**本次代次的 `INTENT`**；落盘前复核则要求**没有悬空的未决派发** |
| `step.generation_changed` | 派发代次已变（别人已经推进过这一步） |
| `authorization.missing` / `.expired` / `.plan_changed` / `.plan_version_changed` | 非 SAFE 动作：授权必须**仍有效**，且仍绑在**同一版**冻结计划上 |
| `runtime.instance_changed` | 记录下来的实例与当前实例不一致 → 先做受控歧义暂停再拒绝 |

`_dispatch_guard()`（`:412`）是它的调用包装：允许 → `True`；拒绝 → `False`（`runtime.instance_changed`
会先走既有的受控歧义暂停路径）。原有的 `_dispatch_precheck()` 名字与签名保持不变，内部委托给这个谓词。

**两个窗口用同一个谓词的不同阶段表达**：

* `expect_intent=False` —— 派发意图**还没落盘**（落盘前复核，`:864`）；
* `expect_intent=True` —— 派发意图**已经落盘**（真正发出外部请求之前的最后一道复核）。

### §2.2 派发失败后必须同步执行流程状态

三件事一起做才成立：

1. **`drive()` 每一轮都以持久化真相为准**（`:698`）：循环开头重新 `load`，不再信任进来时那份快照。
   注意 `last_progress_key` 是 `TaskRecord` 里**唯一不参与序列化**的字段（既有不对称），
   重新 load 会让它回到空串、使 no-progress 检测永远数不到「连续两次同一个动作」，
   因此按原语义把它带过去。
2. **`_run_step()` 用新的 `"blocked"` 结果表达「发出动作之前被拦下」**；`drive()` 收到它立即
   调 `_settle_blocked()` 返回，**不再进入下一轮**（`:743` 之后的 `outcome == "blocked"` 分支）。
   其余 `"stop"` 也一律改成返回**从 store 重新读出来的**记录 —— 状态改动可能来自别的协程，
   内存对象是旧快照。
3. **`_settle_blocked()`**（`:902`）做两件事：
   * 把落盘的派发意图**如实 retire**（`dispatch_state` 清空、`dispatch_at` 归零、步骤回到 `PENDING`）：
     复核是在 `_invoke_step` **之前**拒绝的 ⇒ 这一次派发**确认没有发出**，所以它**不该**被当成
     「结果未知」（那是 7F.4 针对「可能已经发出」的语义，用在这里就是谎报）。**代次保持不变** ——
     这一代从来不曾真正派发过，也绝不因为同一轮重复复核就再 `+1`；
   * 如果用户正是在这个窗口请求了暂停，就**如实落到 `PAUSED`**（外部动作计数为零）。

`_adopt_lifecycle()`（`:444`）负责把持久化真相里的生命周期字段取回内存对象，保证之后任何一次
落盘写的都是与 store 一致的状态 —— 这是「旧对象不能覆盖新状态」的结构性保证。

还有一处**顺序上的原子性**：落盘前复核返回之后到 `_save` 之间**没有任何 `await`**，所以 `pause()`
不可能挤在「复核通过」与「写意图」之间。（`_save` 自身也只做 `updated_at` + 直接 `store.save`，
写入前没有额外的 `await`。）

### §2.3 授权复核的暂停状态缺口

`authorize_step()`（`:495`）原来只判「任务不是终态 + 授权对象还在 + hash/参数对得上」，
因此**已暂停**的任务拿着没过期的授权照样放行。现在要求

```
if record.state is not TaskState.RUNNING:
    return False
```

即**只有明确允许执行的 `RUNNING` 才放行**。步骤授权天然发生在真实执行之前（`_run_step` →
`_invoke_step`），那时任务一定是 `RUNNING`；`WAITING_ACTION` 说明已经有动作在外部跑，
那时再授权新步骤没有正当场景。控制面（`minecraft_stop` 等 `task_id` 为空的调用）走的是
`invoke_developer`，**根本不经过**这个授权器，所以真停能力不受影响。

### §2.4 三类窗口分别处理（不是统一「清空重试」）

| 窗口 | 判定依据 | 处理 |
| --- | --- | --- |
| **外部动作尚未发出** | 落盘前/落盘后复核被拒（复核在 `_invoke_step` 之前） | 拒绝派发 + **retire 意图**；外部动作计数为零；用户已请求暂停则如实 `PAUSED` |
| **请求已发出、响应/`action_id` 未落盘** | 调用拿不到答复（传输层 code）或调用期间实例更换 | 沿用既有**歧义**语义：受控暂停 + 清授权 + 要求重新规划 + 人工核对，**绝不重发** |
| **已知动作正在运行** | `pending_action_id` 已存在 | 沿用既有生命周期：`pause_requested` + 尽力真停，等它结束后再停 |

第三行与「假象」的边界单独补了一道守卫：`_settle()`（`:939`）在结算**之前**重新读持久化状态，
如果不是 `RUNNING` 就交给 `_record_settlement_under_pause()`（`:1020`）—— **只记步骤证据、
绝不把任务状态推回 `RUNNING`/`WAITING_ACTION`**；若动作确实已经外发，则照样落盘
`action_id` + `pending_action_id` + `pause_requested`，即「动作有记录、状态不撒谎」，
绝不出现「任务说已暂停、动作却在外面跑且没有任何记录」。

### §2.5 不用锁，以及为什么不会死锁

任务书明确要求：若考虑用锁 / 派发租约，**必须先证明**不会因 `_pause_ambiguous_dispatch()` 里的
`stop_action()`、Runtime 健康检查或实例变化回调而死锁。本阶段的结论是**不加锁**：

* 调用链是 `对账 → _pause_ambiguous_dispatch → _stop_action → _invoke(minecraft_stop)` →
  Service `_ensure_runtime()` → **健康检查** → `_note_runtime_instance()` → 可能**再次**观测到
  实例变化 → 回到 `on_runtime_instance_changed()`。这条链是**真实存在**的，加一把跨网络调用的锁
  就会在同一条链上重入；
* 排序保证改为**持久化状态比较 + 发动作前复核**（无等待原语）；
* 身份对账的**再入保护**（`:281` / `:1582` / `:1590` / `:1620`）只打标记、**绝不等待**：重入直接
  返回空列表。跳过一轮是安全的 —— 对账本身幂等，而且**派发**另有落盘前/落盘后两道身份复核，
  安全性并不依赖这个回调跑完。

## §3 变更文件

| 文件 | 变更 |
| --- | --- |
| `app/tasks/runtime.py` | 新增 `_dispatch_allowed()`（:354）、`_dispatch_guard()`（:412）、`_adopt_lifecycle()`（:444）、`_settle_blocked()`（:902）、`_record_settlement_under_pause()`（:1020）；`_dispatch_precheck()` 改为委托统一谓词；`_run_step()` 落盘前复核（:864）+ 落盘后复核返回 `"blocked"`；`drive()` 每轮重读持久化真相（:698）并处理 `blocked`；`_settle()` 结算前新鲜度守卫（:939）；`authorize_step()` 要求 `RUNNING`（:495）；`pause()` 对未决派发意图走受控歧义暂停（:1782）；`on_runtime_instance_changed()` 再入保护（:1582） |
| `tests/test_predispatch_pause_race.py` | **新增**：11 项确定性故障注入（含真实 SQLite 回读） |
| `tests/test_task_runtime.py` | `test_e_authorize_step_matrix` 改为在 `RUNNING` 上验授权矩阵（语义收紧，断言一条未放宽） |
| `tests/test_task_authorization.py` | `test_bridge_accepts_a_step_the_runtime_authorizes` 同上 |

**无数据库迁移、无 schema 变更、无新增工具。**

## §4 测试与门禁

### §4.1 新增测试：`tests/test_predispatch_pause_race.py`（11 项）

交错注入全部是**确定性的**：用可控 store 钩子（只在「首次把 `dispatch_state` 写成 `INTENT`」的
那次 `save` 上、且在 `super().save()` **之后**触发，因此那时磁盘上已经是「意图已落盘、外部请求
还没发出」的真实中间态），或用 `asyncio.Event` 门闩把调用精确停在中间 —— **不依赖 sleep 碰运气**。

| 用例 | 覆盖 | 关键断言 |
| --- | --- | --- |
| `test_pause_between_intent_and_dispatch_check_never_dispatches` | §三.1 | 动作计数 **0**；持久化 `PAUSED`；步骤 `AMBIGUOUS`；**代次 1**（没有重复 +1）；`action_id` 为空；授权已清；`replan_required`；`recovery.detail.dispatch_reason == paused_during_dispatch`；再 `drive` 仍 `PAUSED`；`resume()` 抛 `TaskAuthorizationError` |
| `test_authorize_step_refuses_a_paused_task` | §三.2 | 正对照：`RUNNING` + 有效授权 → `True`；暂停后**同样参数、同样未过期授权** → `False`；回读证明授权对象本身仍 `valid()` |
| `test_stale_in_memory_record_cannot_override_persisted_state` | §三.3 | 旧对象确实仍是 `RUNNING`；`drive()` 返回 `PAUSED`；回读 `PAUSED` **未被覆盖**；**代次 0、无悬空意图** |
| `test_cancel_between_intent_and_real_call_never_dispatches` | §三.4 | 计数 0；`CANCELLED`；意图被 retire（`dispatch_state == ""`） |
| `test_expire_between_intent_and_real_call_never_dispatches` | §三.4 | 计数 0；`EXPIRED`；意图被 retire |
| `test_lost_response_after_intent_stays_ambiguous_and_never_retries` | §三.4 | 计数 **1**（请求确实发出过一次）；`PAUSED` + `AMBIGUOUS` + 授权已清 + `replan_required`；再 `drive` 后计数**仍为 1** |
| `test_instance_change_and_user_pause_do_not_double_dispatch` | §三.5 | 暂停先到：计数 0、`PAUSED`、分类**幂等不被覆盖**（`paused_during_dispatch`）、不重发 |
| `test_instance_change_before_pause_in_the_same_window` | §三.5 | 实例变化先到：对账命中 **1** 条、计数 0、`PAUSED`、分类 `runtime_instance_changed`、不重发 |
| `test_identity_reconcile_reentrancy_does_not_deadlock` | §三.5 | 对账内部的控制面**再入**同一回调 → 立即返回 `[]`（`nested == [[]]`）；`asyncio.wait_for` 证明**没有挂起**；真停确实发生 |
| `test_inflight_pause_records_the_action_and_never_redispatches` | §三.6 | 调用真的在飞时暂停：计数 **1**、真停、任务保持 `PAUSED`（**不推回** `WAITING_ACTION`）、`pending_action_id == act_srv_1`、`pause_requested`、步骤 `WAITING_ACTION` + `ACCEPTED`；再 `drive` 计数仍 1 |
| `test_pause_race_survives_a_real_sqlite_round_trip` | 数据库回读 | 同一场竞态在**真实 SQLite** 上跑；用**全新 store** 从数据库回读：`PAUSED` / `AMBIGUOUS` / 代次 1 / 授权为空 / `replan_required` |

### §4.2 既有测试的语义更新（不是放宽断言）

§2.3 把「只有 `RUNNING` 放行步骤授权」定成硬规则后，两个原先**借用 `WAITING_ACTION` 当
「非终态」**来验授权矩阵的用例需要摆回 `RUNNING`：

* `tests/test_task_runtime.py::test_e_authorize_step_matrix`、
* `tests/test_task_authorization.py::test_bridge_accepts_a_step_the_runtime_authorizes`。

两处都用状态机**允许**的转移（`WAITING_ACTION → RUNNING`，`resume()` 走的就是这条）把任务摆回
「正在执行」，**原有断言一条未改**（有效 → 放行；参数/计划/任务对不上 → 一律拒绝）。
这是语义**收紧**而不是放宽：改动前那条路径本来就是「任意非终态都放行」。

### §4.3 本地门禁

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| Lint | `ruff check .` | **All checks passed!** |
| Format | `ruff format --check .` | **657 files already formatted** |
| Types | `mypy app` | **Success: no issues found in 336 source files** |
| 全量测试 | `pytest tests -q` | **3836 passed in 665.12s**（上一阶段基线 3825 + 本阶段新增 **11** 项，精确吻合） |
| WebUI types | `npm run typecheck` | exit 0 |
| WebUI unit | `npm test`（Vitest） | **75 files / 528 passed** |
| WebUI build | `npm run build` | ✓ built in 8.34s（重建 `webui/dist` 与已提交产物一致） |
| WebUI E2E | `npm run test:e2e`（Playwright） | **7 passed (27.5s)** |
| Minecraft runtime | `npm test` | **ALL CHECKS PASSED**（exit 0；含桥接鉴权段与实例段；套件内既有 SKIP：假服务器不支持 `/summon`、`/give` 等） |
| 迁移演练 | `scripts/migrate_db.py --db <副本> --json` | `version [36,36]`、`integrity [ok,ok]`、`tables [83,83]`、`added_tables []`、`lost_rows {}`、`ok:false`（已最新版的**预期 no-op**）。**无新表、无新迁移。** |

真机前已备份 live DB：`CatooBot_backups\catoobot-20261012-134250.db`（v36 · 83 表 · 87969 行 · integrity=ok）。

### §4.4 真实 Fabric 门禁（三段，按副作用顺序执行）

前置：停 CatooBot 释放 runtime 唯一会话（25580/8500 释放、25565 在线），`PYTHONIOENCODING=utf-8`；
跑完已按原方式（**全局** Python 3.12 + `python .\run.py`）重启并确认三端口恢复。

| 门禁 | 结果 | 关键读数 |
| --- | --- | --- |
| ① `scripts/explore_smoke_real.py --sections all`（7F.1 单段回归） | **PASS** | 起点 `(-268.54,64,-128.5)` → 目标 `(-264,64,-130,r=2)`；`SUCCEEDED` + `verification.ok=True`（距 **0.85**）、位移 4.0、无遗留移动；不可达目标安全 `PAUSED`（不冒充到达）；导航中取消 → `CANCELLED` + 真停 + 驱动终态任务不再移动 |
| ② `scripts/feedback_smoke_real.py`（7F.2 反馈链） | **FAIL（1 项，非本阶段引入；见 §4.5）** | 首次：任务 `SUCCEEDED` + 到达复核 `ok=True` + 3 条真记忆（actionable=1）；仅「有证据的新事实生成了探索形状 LifeIntent」失败 |
| ③ `scripts/runtime_restart_identity_smoke_real.py`（7F.5.1 生命周期/身份） | **PASS（20/20）** | 真实实例 A → 真实 `move_to` 派出（世界动作计数 1）→ 停 Node（Python 不退）→ 外部重启 → 真实健康检查发现实例 B → 旧动作 `PAUSED`/`DISPATCH_AMBIGUOUS` + 证据完整 + 授权已清 + `replan_required` → **Runtime 恢复后零重发**（计数仍 1）→ 第二个新任务不越过自己的确认门 |

**本阶段最关键的观察（来自真机原始记录）**：上面每一次真实派发的任务记录都是
`step_1 = SUCCEEDED`、`dispatch_state = SETTLED`、`dispatch_generation = 1`、带**真实** `action_id`
（如 `act_mv4u36vy_1`）、**没有**任何 AMBIGUOUS、**没有**第二次派发、授权与确认门语义不变 ——
即「统一派发谓词 + 落盘后复核 + 每轮重读持久化真相」在真实世界里的正常路径上**一字未偏离**既有行为。

### §4.5 7F.2 的失败、根因与「与本阶段无关」的证据

7F.2 的失败如实记录在此，且**没有**改动它的任何断言、**没有**为让它变绿而放宽阈值或删库。

**失败签名 A（首次以及 13:48 重跑）：任务成功，但探索形状的 LifeIntent 没生成。**

* 事实：任务 `SUCCEEDED`、`verification.ok=True`、真记忆写入成功（5 条 / actionable=3），
  但 `propose_intents()` 只给出普通 `MINECRAFT_INTEREST`（tags `memory/virtual_interest`），
  没有 `exploration_feedback` 形状 → 门禁该项 ✗。
* 根因（逐层实测）：探索事实的落点与**上一次**真机探索**完全重合**（如 `(-264,64,-130) 附近 发现了 short_grass`），
  于是记忆写入被**内容去重**合并回**已有行**；而被合并的目标行状态已是 `superseded` / `stale`
  （实测：`id 95/94 = superseded`，本次两条 LOCATION 与它们坐标逐字相同）。
  候选生成器只看 `status=active` 的记忆窗口（`MAX_MEMORY_SIGNALS = 5`，
  `ORDER BY importance DESC, id DESC`），窗口里因此**没有任何 actionable 条目** → 走「普通 Minecraft 记忆」兜底分支。
* **与本阶段无关的证据**：我用一个**完全不引用 `app/tasks/runtime.py` 任何改动**的最小探针
  （只有 `MemoryManager` + `InitiativeContextAdapter` + `propose_intents`）在**同一个数据库**上
  复现出一模一样的结果 —— `context.memories` 5 条、actionable 全为 False、
  `intents: [(MINECRAFT_INTEREST, (memory, virtual_interest))]`。
  也就是说，这个失败是**数据库状态 + 未改动代码**的纯函数，本阶段的改动不可能造成它。
  根因完全落在 `app/memory`（去重/状态机）与 `app/initiative`（固定 5 槽窗口），两者本阶段一行未改。
* 性质：**历史相关的门禁脆弱性** —— 只有在同一个区域反复跑真机门禁、把 POI 事实重复到被 `superseded` 之后才会出现。
  这也解释了为什么上一阶段同样的门禁是 PASS。

**失败签名 B（后续两次重跑）：任务本身 `VERIFICATION` 失败。**

* 实测两条记录：距目标 **2.17 格**、**2.26 格**（允许 2.0 格）→「最终位置与探索目标不符」→
  任务 `FAILED`、`outcome=BLOCKED` → 门禁的任务级两项 ✗。
* 这是项目**已知**的 pathfinder 精度抖动（7F.1 与 7F.3 都有 2.26 格先例，见
  `docs/MINECRAFT_PHASE_7F.3.md`），与本阶段改动无关；**关键佐证**：这两次记录的三个步骤全部 `SUCCEEDED`、
  `dispatch_state` 全为 `SETTLED`、代次都是 1、带真实 `action_id` —— 派发路径行为完全正确，
  失败发生在**到达复核**这最后一步。
* 如实处置：**不改断言、不放宽 2.0 格半径**；记录失败点并重跑（本节即为重跑记录）。

**结论**：7F.2 的两类失败都经根因确认属于**既有环境/数据状态条件**，不是 7F.5.2 引入的回归；
本阶段自身的安全不变量与验收门槛（§五）全部由确定性的故障注入测试覆盖并通过。
同时如实留下一个**后续可修项**：新事实被去重合并进非 active 行时会「不可见」，
对「重复到访同一片区域」的探索回流不利，建议在后续阶段单独修（不在本阶段范围内扩边界）。

### §4.6 交付环境与数据库状态

* 真机后 live DB：`integrity ok`、最高迁移 **36**、**84** 张表（83 业务 + `sqlite_sequence`）、FK 违规 **0**。
* 环境已恢复：CatooBot 运行中（python **27752** + node **4524**），25580 / 8500 / 25565 全部在线。
* 本阶段**未**修改数据库 schema，**未**删除任何数据行；世界状态因真机门禁确有变化（既有事实，按纪律记录）。

### §4.6 CI（运行 ID / attempt / 两个 job 的结论 / 实际 SKIPPED 清单）

* 代码提交：`b3014b9fac9b20fa01bb71b0e9fffb93f13d5142`
  （`Phase 7F.5.2: pre-dispatch pause race closure (no dispatch after pause, stale state cannot overwrite persisted PAUSED)`），
  仓库共 260 commits；镜像 `HEAD = origin/main`，工作树干净。
* **CI run id `38209969258` · attempt 1 · 两个 job 均 success**（无 flake、无需重跑）：
  * `lint · format · types · tests` → **success**。逐步全绿：ruff check ✓ / ruff format --check ✓ / mypy app ✓ /
    **pytest → `3833 passed, 3 skipped in 260.48s`** / Browser E2E ✓ / Minecraft runtime tests ✓。
  * `webui · typecheck · tests · build` → **success**。逐步全绿：vue-tsc ✓ / vitest ✓ / vite build ✓ /
    **Verify the committed bundle matches the build ✓**（重建产物与已提交 bundle 一致，无 churn）。
  * URL：<https://github.com/rinsoraa/CatooBot/actions/runs/38209969258>
* CI 的 `3833 passed, 3 skipped` 与本地 `3836 passed` 精确对账：差的 3 条是**既有**平台条件性 skip，
  本阶段新增的 **11 项无一被跳过**。
* **实际 SKIPPED 清单**（全部是既有假服务器限制，与本阶段无关）：
  1. `equip / inventory_move STOP`：假服务器背包永远是空的，没有可取消的窗口；
  2. `container STOP`：inspect 毫秒级完成，没有可取消窗口；
  3. `craft 成功路径`：flying-squid 没有 `/give`；
  4. `craft STOP`：合成毫秒级完成，没有可取消窗口；
  5. `3×3 craft 成功路径`：箱子里凑不出 8 块木板；
  6. `dropped_items / pickup 成功路径`：假服务器不支持 `/summon`，造不出真实掉落物。

### §4.7 发布前核对（任务书 §七 清单）

| 核对项 | 结论 |
| --- | --- |
| 源工作树与发布镜像预期内容一致 | ✅ 第二次 `sync_github.py` 报 `mirror already up to date` |
| 发布镜像与 `origin/main` 最终 SHA 一致 | ✅ 镜像 `HEAD = origin/main = b3014b9`、`git status` 干净 |
| `scripts/` 仍未发布 | ✅ 实测镜像内**不存在** `scripts/` 目录 |
| 无真实凭据进入日志/事件/任务 payload/WebUI/Git | ✅ 本次改动 8 个文件逐一扫描（`gho_`/`sk-`/`AKIA`/`MC_BRIDGE_TOKEN=`/`Bearer <长串>`/`password=`）**全部 clean**；镜像内 tracked 敏感文件仍只有白名单示例 `config/config.example.yaml` |
| 不引入不必要的数据库迁移 | ✅ **无新表、无新迁移**（演练 `version [36,36]`） |
| CI 报告含 URL/ID/attempt/两 job 结论/SKIPPED 清单 | ✅ 见 §4.6 |

## §5 安全边界

以下全部不变：不新增 Minecraft 工具（注册工具仍 **19** 个）、`ACTION_RISK` 不变、
`allow_medium=false` 不变、两道独立确认门（计划批准 + 执行确认）继续有效、不绕过
`TaskAuthorization` / `TaskStepAuthorization` / Policy / AgentBridge / Action Runtime、
LifeIntent / TaskProposal / AgentPlan 的职责语义不变、多段路线最多 3 段且共享总距离 ≤ 24 格、
`explore_legs=1` 默认值不变、不加入无界重试、**不宣称 exactly-once**。
未修改用户真实 Character Bible、`.env`、密钥、`config/config.yaml` 或 `data/`；
发布镜像与真实工作树继续保持隔离，`scripts/` 仍不得进入 GitHub 发布。

### §5.1 勘误：7F.5 的桥接鉴权（重要）

7F.5.1 的交付报告里，我在「已知限制」中写过「桥接动作 API 仍仅绑 127.0.0.1、**无鉴权**」。
**这句话是错的**，特此勘误：

* Phase **7F.5 已经**实现桥接动作 API 的鉴权：鉴权门位于 `handleRequest()` 的**所有路由之前**
  （`minecraft_runtime/runtime.js:3974-3977`），凭据不匹配 → `401` + `auth.invalid`，**不触发任何动作**；
* 凭据由 Python **每进程生成**（`secrets.token_urlsafe(32)`）并经 `MC_BRIDGE_TOKEN` 注入；
  客户端对**每一次**请求带 `Authorization: Bearer <token>`；
* Node 侧在启动时若**未配置**该凭据则**拒绝启动**：`runtime.js:4396-4397` 打印
  「MC_BRIDGE_TOKEN 未配置：拒绝启动（桥接动作 API 不允许匿名调用）」并 `exit(1)`；
* 该边界已在 7F.5 的文档 `docs/MINECRAFT_PHASE_7F.5.md` §6「桥接 API 鉴权边界」中完整记录，
  并有协议层 + E2E 测试；**7F.5.1 的文档里本来就没有那句错误说法**（错误只出现在我的对话报告里）。

原因说明：那句「无鉴权」是**沿用 7F.4 遗留风险清单里的旧描述**，没有重新核对 7F.5 的实际改动 ——
属于核实失误，不是产品缺陷。本阶段起，涉及既有能力的描述一律以**当次核对过的源码**为准。

## §6 已知限制（如实记录）

1. `action_id` **仍然不是服务端幂等键**，桥接注册表仍是内存态、只保留一条最近完成视图，
   没有按键查询能力 —— 所以本阶段依然只保证「**不会因为恢复/暂停而再发一次**」，
   **不保证 exactly-once**。
2. `pause()` 遇到未决派发意图时走的是**受控歧义暂停**（清授权 + `replan_required` + 人工核对）：
   这是「无法确认外部状态」时的诚实选择，代价是用户暂停后需要重新规划才能继续；
   这是刻意的安全偏向，不是缺陷。
3. `drive()` 每轮多一次 store 读取（换来「旧对象不能覆盖新状态」的结构性保证）；
   每次真实派发也多一次 `_save`（7F.4 起就是这样：意图先落盘）。
4. 本阶段**没有**引入跨网络调用的锁 / 派发租约；排序保证来自持久化状态比较 + 发动作前复核。
   如果将来引入租约，必须先证明它与 `_pause_ambiguous_dispatch()` 的 `stop_action()`、
   健康检查、实例变化回调**不会形成互相等待**。
