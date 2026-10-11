# Phase 7F.5.1 — Runtime Identity Observation & Correlation Guard

本阶段是 **Phase 7F.5 的定向修正**，不引入任何新的 Minecraft 能力：让「Runtime 实例身份」
真正贯穿运行时调用链，并在身份不可信时 fail-closed。

- 基线：7F.4 `f31b4ec600b4cebda002abd7c52d5bcaaa452542`、7F.5 代码 `4d732628c0f04f36bafbf51560f166fda99a0a80`、7F.5 最终发布 `eb4d88d4f14eba8a9b5fd968a92b97178902f3af`。
- 任务书归档：`docs/specs/CatooBot Phase 7F.5.1 — Runtime Identity Observation & Correlation Guard.md`
  （8424 bytes / 163 行，SHA256 `F13467F896C12DD112C2415C41D7F56876D098B45839B2F0198058AE1D0B8E1A`，与上传源逐字节一致）。

## §1 审计：7F.5 没做到的地方

7F.5 交付了「实例标识 + 事件按实例关联 + 实例变化触发对账」，但审计（逐行查调用点）发现：

1. **身份观测没有真正贯穿调用链。** `app/integrations/minecraft/service.py` 里
   `_note_runtime_instance()` 的调用点只有一处 —— `_start_runtime()` 的启动就绪检查。
   `_ensure_runtime()`（每次 API 调用前都会跑）、`status()`、`_poll_loop()` 的**成功响应都没有接入**。
   后果：**外部托管 Runtime（`auto_start_runtime=false`）在 Python 不重启的情况下换了实例时，
   Python 永远观测不到** —— 旧动作只能被动等 TTL 过期，而不是主动对账。
2. **适配器从不回传实例。** `TaskInvocation.runtime_instance` 全仓没有任何赋值点，
   于是 `_settle` 里「以接受方为准」的关联永远退化成派发前的期望值。
3. **事件缺实例时会错误结算。** 7F.5 的判定是 `if recorded and incoming and recorded != incoming` ——
   事件**不带**实例（`incoming == ""`）时两个条件都不成立，直接走正常结算路径。
   而「事件来源无法可靠验证」正是必须 fail-closed 的情况。
4. **派发与对账之间没有排序保证。** 对账改的是**持久化记录**，编排层手里的 `record` 可能是旧快照；
   落盘派发意图之后、真正发出外部动作之前没有任何复核 —— 已被对账判成歧义、授权已清掉的动作，
   仍然可能被发出去。
5. **旧格式记录没有区分手段。** 「7F.5.1 之前写下的行」与「新派发但拿不到实例」在磁盘上长得一样
   （都是空实例串），无法一个走兼容路径、一个 fail-closed。

## §2 实现

### §2.1 身份观测贯穿所有成功响应（§二）

`MinecraftService` 里四处成功路径统一按同一协议处理身份字段：

| 位置 | 作用 |
| --- | --- |
| `_start_runtime()` 就绪循环 | 启动后第一次拿到实例 A（原有） |
| `_ensure_runtime()` 的健康探测 | **每次 API 调用前**都会刷新身份（本次新增） |
| `status()` 的成功响应 | 显式状态查询（WebUI/QQ 只看状态也会刷新） |
| `_poll_loop()` 的周期轮询 | **外部托管 Runtime** 换实例时的正常发现路径（本次新增） |

协议本身由 `_note_runtime_instance()` 保证（7F.5 已实现，本次未放宽）：

- 只有 **成功** 且 **含有效 `runtime_instance` 字段** 的响应才能更新身份；
- 超时 / 连接失败 / **401** / 非 JSON 无效响应 / 缺字段 **一律不更新**（因此不会被误判成"实例变了"）；
- 首次只记录、不通知；同实例不重复通知；**真的换了**才通知一次；
- 通知走 `asyncio.create_task`（不在请求路径里同步跑对账）。

另新增 `probe_runtime_instance()`：主动探测一次，**失败如实抛给调用方**，
让「Runtime 不可达」（按既有传输层语义）与「Runtime 可达但不报告身份」（必须 fail-closed）可以分开判定。

### §2.2 派发前的可信身份（§三.1 / §四）

`TaskRuntime._resolve_dispatch_identity(record, step)` 在**落盘派发意图之前**运行：

- 没装配身份提供者 → 返回空串（纯单测替身；不假装有身份能力）；
- 装配了但当前不知道实例 → 调 `identity_probe` 主动探测：
  - 探测**失败** → 返回空串（Runtime 不可达，交给传输层按既有语义报告）；
  - 探测成功但**没有实例** → **fail-closed 暂停**（`runtime_instance_unavailable`），
    **绝不**在"只按 action_id 关联"的前提下把外部动作发出去；
- 这一步记录过旧实例、而当前实例已经变了 → **fail-closed 暂停**
  （`runtime_instance_changed_before_dispatch`）。

### §2.3 真正服务这次请求的实例（§三.1）

`MinecraftTaskInvoker` 在调用**前后**各读一次实例，并把调用后的读数
（=`_ensure_runtime()` 那次成功身份探测之后的真实值）写进 `TaskInvocation.runtime_instance`，
由 `_settle` 落盘成权威关联。若调用期间实例换了人**且这个动作已经跑到外部**（detached），
则整条调用按**歧义**返回（`runtime.instance_changed`，走既有的 fail-closed 分支），
真实 `action_id` 留在 `detail` 里供人工核对。

### §2.4 发出外部动作之前的最后复核（§三.4）

`TaskRuntime._dispatch_precheck(record, step, risk=...)` 从 **store 重新 load**，用持久化真相判断：

- 任务已经终态 / 这一步不存在 → 不发；
- 这一步的 `dispatch_state` 不再是 `INTENT`（已被判歧义 / 已结算）→ 不发；
- 派发代次对不上 → 不发；
- 非 SAFE 步骤的授权已被对账清掉 → 不发；
- 记录的实例与当前实例不一致 → 先按歧义暂停，再不发。

**为什么不用锁**：对账路径里的 `stop` 是安全控制调用，它自己会走 `_ensure_runtime()` → 又一次身份探测。
若用一把跨派发的锁保护"派发"，`stop` 就会在同一条链上重入等待 → 死锁。
所以排序保证用的是**持久化状态比较 + 发动作前复核**，而不是互斥量；另外
`_note_runtime_instance()` 先更新实例、再发通知，通知本身是 create_task，
同一实例的嵌套观测会直接早返回（§4.1 的 Service 集成测试第 8 项有专门测试）。

### §2.5 事件关联矩阵（§四）

`TaskRuntime.on_action_event()` 的严格判定（**不结算一律返回 None**）：

| 记录（step） | 事件 | 处理 |
| --- | --- | --- |
| 带身份追踪 + 有实例 | 同实例 | 正常结算（`SETTLED`） |
| 带身份追踪 + 有实例 | 不同实例 | 歧义 `stale_runtime_instance_event`，不结算 |
| 带身份追踪 + 有实例 | **缺实例** | 歧义 `missing_runtime_instance_event`，不结算 |
| 带身份追踪 + 无实例 | 缺实例 | 歧义 `runtime_instance_missing`，不结算 |
| 带身份追踪 + 无实例 | 有实例 | 歧义 `runtime_instance_unattributed_event`，不结算 |
| **旧格式行**（无追踪标记） | 任意 | 旧格式回退路径：按 action_id 兼容结算 + 持久化 `dispatch_legacy_fallback` |
| 命中**多条**记录 | — | 全部歧义 `action_id_ambiguous` |
| `action_id` 匹配不到 | — | 什么都不改（状态/授权/代次一律不动） |

**旧格式兼容标记**用现有 payload 序列化承载（`TaskStep.dispatch_identity_tracked` /
`dispatch_legacy_fallback`），**没有引入任何数据库迁移**：任务 payload 是单个 TEXT 列，
`from_payload` 全程 `payload.get(...)` 带默认值，旧行天然安全。

## §3 变更文件

| 文件 | 变更 |
| --- | --- |
| `app/integrations/minecraft/service.py` | `_ensure_runtime` / `status` / `_poll_loop` 接入身份观测；新增 `probe_runtime_instance()` |
| `app/integrations/minecraft/task_adapter.py` | 调用前后各读一次实例并回传；调用期间换实例且动作已外发 → 歧义；新增 `RUNTIME_INSTANCE_SWAPPED`；按**能力**接线身份提供者与探测 |
| `app/tasks/runtime.py` | `identity_probe` 构造参数；`_resolve_dispatch_identity()`；`_dispatch_precheck()`；`_settle` 三处分支补身份/追踪标记并新增「可达但无身份」fail-closed；`on_action_event` 严格矩阵 |
| `app/tasks/models.py` | `TaskStep.dispatch_identity_tracked` / `dispatch_legacy_fallback`（双向序列化） |
| `tests/test_runtime_identity_observation.py` | **新增 10 项** Service 集成测试（真实 HTTP + 真实客户端 + 真实服务请求路径） |
| `tests/test_event_correlation_matrix.py` | **新增 13 项** 事件关联矩阵 + 派发身份守卫 |
| `scripts/runtime_restart_identity_smoke_real.py` | **新增** 真实 Node Runtime 外部重启门禁（本地专用，不进 GitHub 发布） |

## §4 测试与门禁

### §4.1 新增测试（23 项）

- **Service 集成测试（10 项）**：不直接调 `_note_runtime_instance()`，而是起真实本地 HTTP
  服务 + 真实 `MinecraftRuntimeClient`（服务按 `runtime_port` 自建客户端 = 生产同一路径）：
  首次成功 health 记录 A / 外部重启后 `status()` 与 `_ensure_runtime()` 都能发现 B /
  `auto_start_runtime=false` 仍然有效 / 暂时失败后恢复 A 不误判 / 401·无效响应·缺字段都不伪造身份 /
  变化只触发一次对账 / 回调里等 `stop_action()` 与再次探测**不死锁**。
- **事件关联矩阵（13 项）**：§2.5 表格的每一行；断言最终任务状态、授权状态、`dispatch_state`、
  `replan_required`、**从 store 读回的持久化证据**、**外部真实动作启动次数**。
- **派发身份守卫**：身份变了绝不派发（0 次世界动作）；Runtime 可达但无身份 → fail-closed；
  没装配提供者 → 明确界定的旧行为；实例变化后 `drive()` 不再重发且真的尝试 `stop`。

### §4.2 门禁结果（全部实测）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | **656 files already formatted** |
| `mypy app` | Success（**336** source files） |
| 全量 `pytest tests -q` | **3825 passed in 640.68s**（基线 3802 + 新增 23，精确吻合） |
| WebUI typecheck / Vitest / build / Playwright E2E | exit 0 / **75 files · 528 passed** / ✓ built in 7.63s / **7 passed (21.5s)** |
| `minecraft_runtime npm test` | **ALL CHECKS PASSED**（exit 0） |
| 迁移演练（副本） | `version [36,36]`、`integrity [ok,ok]`、`tables [83,83]`、`added_tables []`、`lost_rows {}`、`ok:false`（已最新版的预期 no-op）；副本 `quick_check ok`、FK 违规 0、83 张业务表、最高迁移 36 |
| 真实 Java 门禁 ①：`scripts/explore_smoke_real.py --sections all` | **PASS**（exit 0）：单段正向 / 不可达安全暂停 / 导航中取消三段全绿 |
| 真实 Java 门禁 ②：`scripts/feedback_smoke_real.py` | **PASS**（exit 0，19 项）：3 条真记忆 → `MINECRAFT_INTEREST` → TaskProposal(LIFE) → AgentPlan `READY_FOR_APPROVAL` → 批准后停在确认门且零真实动作 |
| 真实 Java 门禁 ③：`scripts/runtime_restart_identity_smoke_real.py` | **PASS**（exit 0，**20/20**）—— 见下 |

**§五.C 真实 Node Runtime 重启门禁的实测证据**（在**真实 MC 服务器**上，Python 全程不退出）：

* 真实的成功 health 响应拿到实例 A `rt_mv45ywm5_9c05ad48`；
* 真实世界动作 `minecraft_move_to` 派出去了（世界动作计数 1），进入 `WAITING_ACTION`，
  `action_id=act_mv45yyta_1`，未决动作记下的实例正是 A；
* 停掉 Node 进程（pid 15756）后 Python 仍在运行、旧任务记录仍可读；把 `auto_start_runtime` 置 false
  以证明**是 Python 观测到的**、不是它自己重启的；
* **外部**重新拉起 Node（新 pid 24556）→ 通过**真实健康检查路径**发现新实例 B `rt_mv45z0sa_f2883256`；
* 旧动作 fail-closed：`PAUSED` / `DISPATCH_AMBIGUOUS` / 授权已清 / `replan_required=True`，
  证据里 `dispatch_reason=runtime_instance_changed`、实例 A、真实 `action_id`、
  `dispatch_key`、派发代次，步骤 `dispatch_state=AMBIGUOUS` 且被判 `FAILED`；
* **Runtime 恢复后没有重新派发**（世界动作计数仍是 1）；
* 第二个无关新任务停在自己的确认门，且**没有**因为旧任务的恢复逻辑而绕过（世界动作计数仍是 1）。

**真机前后数据库**：真机前备份 `CatooBot_backups\catoobot-20261012-023043.db`
（v36 / 83 表 / 87852 行 / `integrity ok`）；真机后 live DB `integrity ok`、
最高迁移仍 **36**、**84** 张表（83 业务 + `sqlite_sequence`）、FK 违规 0；
`life_intents` 118 → **122**、`activity_episodes` 214 → **217**。

环境已恢复：CatooBot 运行中（python **56188** + node **22932**），25580 / 8500 / 25565 全部在线。

### §4.3 首次失败与重跑（如实记录）

1. **6 个 7F.4/7F.5 既有测试首次失败**：都是 `make_runtime` 没装配身份提供者的纯替身场景，
   我把"记录与事件都没有实例"也判成了 fail-closed。**修正的是语义而不是断言**：追踪标记改为
   `self._runtime_instance_of is not None`（"这次派发处于身份追踪之下"），没装提供者就不假装有身份能力。
2. **8 个 `test_minecraft_task_integration.py` 失败**：`build_minecraft_task_runtime` 原先
   **急求值** `service.probe_runtime_instance`，而测试替身 `FakeService` 没有这个方法。
   改为**按能力接线**（`getattr`）—— 没有身份 API 的服务不被当成有身份能力。
3. **§五.C 门禁脚本自身三次失败**（不是产品问题）：`wait_for` 被传入了协程对象而不是谓词；
   `service.mirror_online/mirror_busy` 是我臆造的方法名（真实实现是 `snapshot()` 的
   `connection.status` 与 `pathfinder.moving`/`action.status`）；`coordinator.stop()` 未 await。
   每次都是脚本 bug，修好后重跑；**没有放宽任何断言**。

## §4.4 CI 与发布

* 提交：`3ba395ec1367878eef152c551a9105116a0fdf84`（`Phase 7F.5.1: runtime identity observation & correlation guard (fail-closed on unverifiable identity)`），
  推送到 `origin/main` 后 `HEAD == origin/main`，工作树干净，258 commits。
* CI：**run id 38164830085 / attempt 1**（`https://github.com/rinsoraa/CatooBot/actions/runs/38164830085`），
  两个 job **均 success**、**无失败步骤**、**无需重跑**：
  * `lint · format · types · tests` → success（其中 pytest **3822 passed, 3 skipped in 317.85s**，
    即本地 3825 − 3 条**既有平台条件性** skip；**新增 23 项无一被跳过**；Browser E2E 7 passed (21.5s)）；
  * `webui · typecheck · tests · build` → success。
* 实际 SKIPPED 列表（全部是**既有**、与本阶段无关的假服务器限制）：
  `[e2e] equip / inventory_move STOP`、`container STOP`、`craft 成功路径`、`craft STOP`、
  `3×3 craft 成功路径`、`dropped_items / pickup 成功路径`。
* 发布镜像审计：改动正好 10 个文件；`scripts/` **未发布**（真机门禁与诊断脚本只存在于本地）；
  `config/config.yaml` 虽在镜像目录里存在但被 `.gitignore:17` 忽略、**未被 tracked**；
  新增/改动文件密钥扫描**零命中**；源目录 vs 镜像**内容哈希差异 = 0**
  （app 336 / plugins 7 / tests 236 / docs 162 / .github 1 / webui 355 / minecraft_runtime 26 个文件逐一比对）。
  * 镜像 `webui/dist` 的 mtime 因本轮重建而复新（内容与已提交 bundle **逐字节一致**），
    故 `robocopy` 的时间戳视图会报差异 —— 以**内容哈希**为准。
* 无新表、无新迁移（最高仍 36）。

## §5 安全边界

- 不新增 Minecraft 工具（仍 19 个）、不改 `ACTION_RISK`、`allow_medium=false` 不变；
- 计划批准与执行确认仍是两道独立门，不绕过 `TaskAuthorization` / `TaskStepAuthorization` /
  Policy / AgentBridge / Action Runtime；
- LifeIntent / TaskProposal / AgentPlan 语义不变；多段 ≤3 段、共享 24 格、`explore_legs=1` 不变；
- 不新增无界重试；**不宣称 exactly-once**：实例标识只解决**关联唯一性**，
  `dispatch_key` 与实例 ID 都不是服务端幂等键，我们仍然没有按键查询能力；
- 不修改真实 Character Bible / `.env` / 密钥 / `config/config.yaml` / `data/`；
- **不引入数据库迁移**（用现有 payload 序列化承载兼容标记）。

## §6 已知限制与遗留风险（如实记录）

1. **服务端仍无幂等键、无按键查询**：动作的真实状态在 Runtime 重启后**无法确认** ——
   本阶段能保证的仍然只是"绝不因为恢复/观测而再派发一次"，不是 exactly-once。
2. **旧格式回退路径是兼容让步**：7F.5.1 之前写下的未决行仍按 action_id 结算（并留下标记）。
   新行一律走严格矩阵；这条让步只对旧数据生效。
3. **没装配身份提供者的运行期**（纯单测替身、或未来某个不实现身份 API 的适配器）
   不进入严格模式 —— 这是**按能力接线**的结果，生产装配（`build_minecraft_task_runtime`）
   一定会接入。
4. **每次派发多一次 store 读**（发出动作前的复核）与**一次身份探测**（首次派发时）——
   这是"发出动作前必须知道归谁"的代价。
5. 桥接在动作执行中重启、而 Python 未重启时，旧动作同样进歧义（本次由**主动探测**覆盖，
   不再只依赖 TTL）；但"这个动作到底有没有真的发生"依然无法证明。
