# Changelog

本文件记录 CatooBot 的版本演进。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号见 `pyproject.toml`；日期取自真实提交历史（本仓库 2026-09-30 起）。

## Minecraft Phase 6A — 世界活动运行时（Activity Episode）

* **「当前活动」升级为有生命周期的 Episode**：新增 `app/activity/`（model/clock/store/events/
  planner/runtime/projection/adapters）。`ActivityEpisode` 是"她此刻在做什么"的**唯一事实来源**：
  稳定 ID（`ACT-YYYYMMDD-NNN`，可反解、重启不变）、7 状态显式状态机、min/typical/max 时长、
  标准化转移原因（`TIME_EXPIRED`/`TASK_STARTED`/…）、`related_task_id` 引用、只读观察。
  **不新增 Minecraft 工具**（仍是 19 个）、不新增 ActionRuntime action、不新增 TaskRuntime 状态。
* **升级复用而不是新系统**：检查过仓库 —— `app/world/` 不存在（她的世界是 `app/sandbox/`）；
  v1.x 曾有一张 `activity_episodes`（迁移 v10）并在 v14 被整体 DROP，6A 按同一语义**重建**
  （迁移 **29**）；`CharacterState` 里 v1.0 声明但从未写入的死字段（`current_activity_episode_id` /
  `activity_started_at` / `activity_planned_end_at` / `activity_status`）这次**接线**成 Episode 投影，
  并补上 `activity_source`。
* **状态机与并发**：终态没有出口（`COMPLETED/CANCELLED/EXPIRED/INTERRUPTED → ACTIVE` 全部拒绝）；
  每次转移都是 compare-and-set（SQLite 走事务），两个事件同时收尾只会有一次最终转移；
  数据库层用 partial unique index 保证"每个角色最多一个 live primary Episode"。
* **持久化与幂等**：`activity_episodes` + `activity_transitions`（迁移 29）；状态转移立即落盘、
  运行中的观察按 `persistence_interval_seconds`（默认 60s）节流；`activity_transitions` 上的
  partial unique index + `INSERT OR IGNORE` 保证**一次性转移的事件只发一次**（重启重试也不会
  再发一个 `activity.completed`；`EXTENDED` 可重复）。
* **重启恢复**：`recover()` 按时间对账三种情况（窗口内继续 / 过期交给 Planner / 超硬上限终结），
  带 `recovery_grace_seconds` 宽限；**绝不伪造离线期间的活动**（离线 8 小时后旧 Episode 只被终结，
  新活动的 `started_at` 只能是"现在"）；没有 Episode 是合法状态，不生成占位数据。
* **世界 tick 只推进不决策**：复用现有的 `RuntimeScheduler`（`activity=` 钩子）在每次 tick 末尾
  调一次 `ActivityRuntime.advance()` —— 到期/超时/排下一个，**绝不**"每分钟重新问她该干什么"。
  Activity 不在时调度器照常工作（沙盒契约不变）。
* **确定性 Planner**：`ActivityPlanner` 只在"她空着 / Episode 到期"时按世界时钟的**时段表**给出下一个
  虚拟活动（确定性旋转，无 `random`、无 LLM）；日程表里的名字沿用沙盒的动作词表
  （`eating/gaming/reading/out/sleeping/idle/…`）并**禁止** Minecraft 活动名。
* **CharacterState 投影**：`activity` / `activity_status` / `activity_source` / 起止时间只由
  Episode 投影写入；Episode 一终结就把快照清空（"她刚结束一件事"）；WebUI 上的"直接改 activity"
  会被翻译成一次显式换活动（`source=USER` / `reason=MANUAL`），**不许绕过 Episode**（§十四）。
* **Task ↔ Activity**：订阅现有 `task.*` 事件 —— 开始/恢复 → 任务型 Episode（`source=TASK`，
  只引用 `task_id`）；**暂停 → INTERRUPTED**（任务 PAUSED 时活动不能还 ACTIVE）；四个终态映射到
  四种不同的 Episode 状态；重启恢复复用同一条 Episode（不重建、不换 id）。
* **Minecraft 只读观察**：`MinecraftObservationAdapter` 只允许调 `snapshot()` / `world_view()`，
  **观察不是命令** —— 看一眼世界不会创建/修改/终结任何 Episode；掉线时不会凭空出现
  `minecraft_*` 虚拟活动（虚拟活动 ≠ 真实 Minecraft 行动）。
* **安全边界**：Activity 这一层不 import 也不调用任何世界动作（AST 级 guard），
  不认识 `MinecraftService`；Policy/工具/确认门也不认识 Activity；
  事件只有一个同步 sink + 日志（**不会**触发 LLM turn）；Activity 不写 Memory（连 memory 都不 import）。
* **配置**：只新增 `world.timezone` 与 `world.activity`（enabled / persistence_interval_seconds /
  recovery_grace_seconds / recent_episode_limit）—— **`allow_medium` 默认值没有变化**。
* **API / WebUI**：`GET /api/v1/world/activity?limit=10`（只读、恒 200）；World 页新增只读卡片
  「当前活动（Phase 6A）」（Episode ID/状态/开始/计划结束/时长/来源/关联任务/原因/延长次数 +
  最近 ≤10 条），**没有任何** start/cancel/extend 入口。
* **测试与文档**：新增 `tests/test_activity_episode.py`、`test_activity_runtime.py`、
  `test_activity_recovery.py`、`test_activity_projection.py`、`test_activity_events.py`、
  `test_activity_minecraft_adapter.py`（覆盖任务书 A–P 矩阵，含幂等/主唯一/假时钟/恢复/
  LLM 隔离/记忆隔离/离线不造假/安全 guard）；文档 `docs/MINECRAFT_PHASE6A.md` +
  `docs/README.md`；真机门禁脚本 `scripts/activity_smoke_real.py`（A–E，只读核对）。

## Minecraft Phase 5C — 身份桥与持久世界记忆

* **身份桥（Identity Bridge）**：新增 `app/integrations/minecraft/identity.py`。
  `MinecraftServerIdentity` 的 `server_id` 是 `edition|host|port|world_key` 的确定性摘要
  （同一台服务器重启后不变，换服必变）；`player_uuid` 是 canonical identity、`username` 只是
  显示名；`IdentityLink(platform=qq, user_id, server_id, player_uuid, status=VERIFIED)`；
  来源优先级「用户显式验证 > 运维配置 > 无绑定」，**绝不**用昵称/同名自动认定。
  迁移 28 建 `minecraft_identity_links`（partial unique index 钉死"一个 uuid 在一台服务器上
  只能有一个 VERIFIED 主体"）；冲突请求一律 `identity.conflict` 拒绝且不覆盖；解绑 = REVOKED、
  历史行保留。绑定只建立"谁是谁"：`grants_permission=False`，**不授予任何权限**。
* **QQ 绑定命令**：新增 `app/integrations/minecraft/identity_commands.py`（认领式订阅，
  注册在任务入口之前）：两步确认（先报「服务器 + 玩家名 + UUID 尾号」，回「确认绑定」才写）、
  二次确认时重新解析并比对 uuid（人走了/换人了就什么都不改）、「解除绑定」、普通聊天不认领。
* **Minecraft 记忆域**：新增 `app/memory/minecraft/`（model/store/writer/reconcile/retrieval），
  复用**现有**记忆引擎（同一张 `memories` 表、同一套 dedupe/conflict/embedding/quota），
  只用专用 scope `character:<角色>:minecraft` 与普通聊天记忆隔离，每条 `source=minecraft`、
  provenance 带 `domain=minecraft` + `server_id`。**不新增第二套存储/向量库**。
  七种 kind（PLAYER/LOCATION/RESOURCE/TASK/EVENT/RELATIONSHIP/PREFERENCE）、五种来源
  （OBSERVED/USER_STATED/TASK_RESULT/DERIVED/SYSTEM）与各自的置信度上限
  （DERIVED ≤0.70）、新鲜度 ACTIVE/STALE/INVALIDATED/SUPERSEDED（引擎 status 直接映射，
  **过期不删历史**）。
* **写入策略**：只在任务收尾（SUCCEEDED/FAILED/EXPIRED）、玩家出现、重要地点/资源观察、
  用户明确说过的事、关系变化时写；`moved 1 block` / `look_at` / 路径更新 / 每 tick 一律不写
  （域里根本没有这类 API）。**任务成功**时额外写**一条**世界事实：取第一条带完整坐标的
  `minecraft_dig` 步骤 → 「(x,y,z) 附近有一块 \<expected_block\>」（`RESOURCE` / `TASK_RESULT`），
  同一 16 格按语义身份强化 —— 这是"刚才那棵树在哪里"能被检索、也能被当前世界复核的基础。
  任务结果只留语义摘要，不留 action_id / 超时 / pathfinder 调试 /
  checkpoint 树；失败任务写成"当时的情况"（`temporary`），不下永久结论。
* **去重与冲突**：`dedupe_key = sha256(domain|server_id|kind|subject)` 作为语义身份 ——
  同一件事反复观察只强化那一条（观察次数 +1、置信度 +0.05 且不超上限）；矛盾内容保留冲突
  （旧条 `superseded` + `conflicts_with_id`），绝不覆盖。
* **世界对账**：`MinecraftMemoryReconciler` 只做单向 `WorldPerception → Memory`。
  世界说"不在了" → `INVALIDATED`（行还在）；"还在" → 刷新复核时间；"读不到"（太远 / 桥挂了 /
  `reason=unavailable`）→ **什么都不做**；非位置事实只按时间变 `STALE`。
  位置类事实按 `radius` 区分"聚簇锚点"（锚点空 = unknown）与"就是那一格"（空 = absent）。
  复核只用 SAFE 只读，记忆桥/对账器上**没有**任何世界修改方法。
* **检索适配器**：`MinecraftMemoryRetriever` 最多注入 **5 条**、每条一句话（≤120 字、
  整块 ≤420 字），优先级「当前玩家 > 附近资源/地点 > 最近任务 > 事件/关系/偏好 > 历史」，
  已失效的排最后并超出预算直接不进上下文；检索时对前 2 条位置事实做一次真实世界复核 ——
  世界说没有了就把那句话当场改写成「（这条已经被当前世界证伪）」（**当前世界优先**）。
* **接入**：`CharacterRuntime.respond()` 把记忆块拼在她"此刻处境"后面（QQ 与游戏内共用同一条
  路径，平台名由会话前缀判定）；Bot 在连接层**真的起来之后**（`start()` 里人设已载入）装配
  `MinecraftMemoryBridge`（复用同一个 SQLite / 记忆引擎；记忆 scope 用**角色名**，放 `__init__`
  会拿到 `default` —— 真机上错过一次），
  订阅 `minecraft.player_joined` → 记忆、`task.succeeded/failed/expired` → 任务经验、
  周期对账（默认 300s，随 shutdown 一起停）、`minecraft.memory.linked_players` 运维显式配置
  （玩家不在线就等下一轮；已有显式绑定绝不覆盖）。**她自己不算"一个玩家"**：自己的
  `player_joined` 一律跳过、`online_players()` 也排除自己（否则会记出「Catodayo 在这个服务器里
  活动过」，甚至能把自己绑给自己 —— 真机踩到过）。任何一步失败**只降级记忆**
  （`memory_degraded` 可见、`context_block()` 返回空串），聊天与任务完全不受影响。
* **LLM 安全边界**：记忆内容一律是"不可信上下文" —— 试图改规则的语句照记但置信度压到 0.40、
  provenance 标 `untrusted_directive`，说十遍也不长信心；关系事实带 `grants_permission=False`。
  测试里有源码级守卫：Agent / TaskRuntime / Tools 的授权链路**不认识**记忆桥。
* **API / WebUI**：新增只读端点 `GET /api/v1/minecraft/memory`（恒 200；未启用 → `enabled:false`）；
  WebUI Minecraft 页新增只读卡片 **Identity & World Memory**（状态 + 绑定表（只显示 UUID 尾号）+
  最近 50 条事实）。
* **配置**：新增 `minecraft.memory`（`enabled` / `reconcile_interval_seconds` / `context_items` /
  `linked_players`）—— **`allow_medium` 默认值没有变化**，记忆也不会降低任何动作的确认要求。
* **历史数据（缺陷期间）的处理**：第一版装配点缺陷写下的 `character:default:minecraft` 那批事实
  **保留、不删除、不盲迁**（2026-10-08 决定）：代码里显式登记为 `LEGACY_SCOPE_KEYS` +
  `is_legacy_scope()`，默认读路径（检索 / 对账 / 状态计数）一律排除，只留只读审计出口
  `store.legacy_facts()` / `bridge.legacy_view()` / WebUI 的 `Legacy (audit only)` 一栏 /
  API 的 `legacy` 数组（每条带 `legacy_scope: true`）；角色名拿不到时的兜底 key 从
  `default` 改为 `unscoped`，保证新数据不会再落进那个审计抽屉。
* **真机取证**：新增 `scripts/memory_smoke_real.py`（identity / memory / world / setblock /
  retrieval / restart 六段，含 `/setblock` 交互段与跨进程持久化子进程取证）；
  新增 `tests/test_minecraft_identity.py`、`test_minecraft_memory.py`、
  `test_minecraft_memory_reconciliation.py`、`test_minecraft_memory_retrieval.py`、
  `test_minecraft_memory_security.py`、`test_minecraft_memory_recovery.py`。
  Minecraft 工具仍是 **19** 个，未新增任何 Minecraft tool / ActionRuntime action。

## Minecraft Phase 5B — QQ 任务入口与统一任务控制

* **QQ 只是入口，不是执行器**：新增 `app/tasks/qq_entry.py`（QQ Task Entry），只做
  "身份规范化 → 意图判断 → 调用现有 Planner/TaskRuntime → 展示现有 Plan summary / 翻译现有
  Task 事件"。**不新增任何 Minecraft 工具**（仍是 19 个）、不新增 TaskRuntime 状态、
  不新增 ActionRuntime action、QQ 不做第二套确认令牌、不维护平行状态机。
* **入口顺序与认领**：事件总线新增最小认领语义（处理器返回真值 = 认领，后续处理器跳过；
  返回 `None` 的旧行为不变）；任务入口在 Bot 装配时注册并**排在人格插件之前**，
  只有确认是任务请求/控制命令时才认领 —— 普通聊天照旧走人格回复（"我想吃蛋糕"不建任务）。
* **身份**：`QQIdentity(platform=qq, user_id=<稳定QQ号>, session_id=private:<uid>|group:<gid>)`，
  复用项目既有会话身份；昵称/群名片只用于措辞。任务记录新增 `source`（qq / minecraft_chat /
  webui），WebUI 面板与 checkpoint detail 都带上（不记任何凭据）。
* **统一控制**：确认（真实 USER 回合 + 现有 ConfirmationStore，绑 task_id/user_id/session_id/
  plan_hash/plan_version/arguments）、暂停、继续、停止全部调用现有 TaskRuntime 方法；
  QQ 层不自己 `minecraft_stop`、不强行 resume（`replan_required`/授权过期/plan_hash 不符都由
  TaskRuntime 判）。状态查询（"任务怎么样了"）返回 objective/状态/第 N/M 步/当前一步。
* **归属隔离**：只有发起人能控制；别人（含同群成员）一律「你不是这个任务的发起人…」，
  且**拒绝不改任务状态**（在调用任何 runtime 方法之前返回）；跨会话互不可见；
  一个 user/session 最多一个任务（跨会话用 `active_for_user` 挡）。
* **事件 → QQ**：任务事件走现有事件流（补上了 `task.created` / `task.confirmation_required` 的
  publish —— 原来只写 checkpoint）；消息短、不带 `action_id`/`plan_hash`/checkpoint/raw snapshot；
  事件载荷新增单调递增的 `event_seq`，QQ 侧按 `(task_id, event_seq)` 幂等去重，同一件事只发一次。
  重规划/授权到期/重启恢复分别有对应说法（"原计划已经作废" / "授权过期，计划没变" / "我重启过…"）。
* **启动恢复**：Bot 装配任务运行时后调用 `recover_persisted_tasks()`，
  重启过的任务旧动作作废、旧确认作废、并通过 QQ 通知发起人。
* 真机实测（2026-10-07/08 两轮，真实 QQ 私聊 + 群聊 + 真实 Java 服务器）：
  **REAL QQ: PASS（24/24 项）** —— QQ 建任务 → 确认 → `dig`/`pickup` 两个 MEDIUM 真动作 →
  `FINAL INVENTORY VERIFIED {'oak_log': 1}`；暂停 / 继续 / 取消 / 授权到期（过期后重新确认才继续）/
  重规划（v2 新 hash + 重新确认）/ 非发起人被拒（零副作用）/ 重启恢复（等确认的任务留在等确认）/
  任务 TTL 过期全部在真机上跑过（详见 `docs/MINECRAFT_PHASE5B.md` §9）。
  另修掉两个真机暴露的 bug：
  ① 入口回复与 `task.confirmation_required` 事件会让**同一份计划发两遍** → 改成同步 publish +
     处理期排队 + 只跳过「回复已说明」的事件；
  ② 重启恢复遇到**停在 PENDING_CONFIRMATION** 的任务会尝试非法转移（`→ PAUSED`）→ 异常冒到
     Bot 装配块 → 整块任务能力被关掉。现在按任务所处状态归位（等确认的继续等确认并重挂确认条目）、
     逐条隔离、恢复失败只降级恢复；
  ③ 重启时的恢复通知会在 **NapCat 还没重连**时发出而丢失 → 发送路径改成有界重试
     （直接回复 1 次短退避、通知 3 次退避，失败日志带 attempt/error）。
* 验证：`tests/test_qq_task_entry.py`（25，§三十一 A–L + 认领语义 + 去重 + MEDIUM 提示）+
  WebUI vitest 面板显示 `source`；真机 QQ 门禁用 `scripts/task_qq_smoke.py` 逐阶段取证
  （NapCat + 真实 QQ 消息由操作者发送，判定从 SQLite checkpoint + 日志）。

## Minecraft Phase 5A.1 — 任务韧性（Replanning / Authorization Expiry / Runtime Restart Recovery）

* **不新增任何 Minecraft 工具**（19 个保持原样）、**不新增 ActionRuntime action**、
  不新增 `minecraft_replan` / `minecraft_recover` / `minecraft_task` —— 只补 TaskRuntime 的可靠性。
  四条底线写进文档与测试：旧 Plan 不授权新 Plan、旧 Action 不在重启后继续、
  旧 Confirmation 不授权新 arguments、旧世界假设不驱动新的世界动作。
* **真实重规划**：世界变了（`WORLD_CHANGED` / `TARGET_LOST`）→ 旧计划 `SUPERSEDED`
  （不覆盖、留档）→ `REPLANNING`（只读对账 + 只允许 SAFE 查询，`drive()` 原地返回）→
  Planner 出新计划 → `plan_version += 1`、新 `plan_hash`、清空授权 → `PENDING_CONFIRMATION`
  → 用户确认后才继续动世界。确认摘要第一句就是事实（「目标已经发生了变化。原计划已停止。」
  +「新的计划（第 2 版）：…」+「需要重新确认。」）。
* **授权到期**：授权有效期独立于任务总时长（`TaskConfig.authorization_ttl_seconds`，生产取确认门 TTL），
  到期时停在安全边界 → 清空 `authorization` → 回 `PENDING_CONFIRMATION` + 新确认条目 +
  `task.authorization_expired` 事件；计划不变（不是重规划），但旧确认条目**不能复用**。
  真机只等 1~2 秒（`SMOKE_AUTH_TTL`），绝不等 600 秒。
* **进程重启恢复**：`recover_persisted_tasks()` 扫 SQLite 非终态任务 →
  「WAITING_ACTION + action_id」的那一步判为失效（`RUNTIME_RESTART`，不查询、不猜、不重做），
  **旧确认一并作废**（`authorization=None` + `replan_required=True`，`resume` 直接拒绝）→
  只读对账（`RECONCILED` / `WORLD_CHANGED` / `TARGET_LOST` / `TARGET_ALREADY_DONE` /
  `OFFLINE` / `UNKNOWN`，**世界事实优先于旧 action 状态**）→ 世界变了就 `REPLANNING`，
  否则 `PAUSED`。恢复幂等，全程零世界动作（动作完成与 checkpoint 的竞态也走同一条）。
* **Plan 版本历史**：`plan_history`（version / plan_hash / summary / steps / created_at /
  confirmed_at / superseded_at / reason / status），WebUI 同时显示 v1 `SUPERSEDED` 与
  v2 `PENDING_CONFIRMATION`；快照新增 `plan_version` / `plan_status` / `replan_required` /
  `replan_reason` / `recovery` / `authorization{remaining_seconds, valid}` / `authorization_expired_at`。
* **审计**：新增事件 `task.recovered` / `task.authorization_expired`；checkpoint 的 detail 显式记录
  `RUNTIME_RESTART` / `RECONCILED` / `REPLANNING` / `AUTHORIZATION_EXPIRED`。
* **状态机**只加一条转移：`PENDING_CONFIRMATION → REPLANNING`（未确认也可重规划）；
  `REPLANNING → RUNNING` 仍不允许。
* 验证：`tests/test_task_recovery.py`（19，含**真 SQLite** 持久化与假时钟 t0+ttl-1 / t0+ttl）、
  `tests/test_task_replanning_security.py`（12，§三十六 8 条 + TASK 凭据一次性）、
  `tests/test_minecraft_task_integration.py` 新增两例、WebUI vitest 新增计划版本历史用例。
  真机：`scripts/task_smoke_real.py`（真实重规划 + 授权到期，`--sections` 可只跑某一段）与
  `scripts/task_restart_smoke_real.py`（**两个进程**验证重启恢复）→ `REAL SERVER: PASS`：
  重规划链跑出 `v1 superseded → v2（新 hash）→ 新确认 → v2 五步全 SUCCEEDED →
  FINAL INVENTORY VERIFIED {'oak_log': 1}`，重启恢复 10/10，授权到期 8/8。

## Minecraft Phase 5A — 多步骤任务运行时（Task Runtime）

* 新增**通用编排层** `app/tasks/`（`TaskState`/`StepState` 显式枚举 + `ALLOWED_TASK_TRANSITIONS`）：
  **不新增任何 Minecraft 原子工具**（19 个保持原样），也不把已有工具包装成
  `minecraft_gather_resource` 这类黑盒。TaskRuntime 只认识「工具 + 参数 + 风险 +
  action_id + 结果」，链路上仍严格是
  `LLM → TaskRuntime → Agent Bridge/Tool Loop → Policy → Confirmation → Service →
  ActionRuntime → Mineflayer`（绝不直接调 Mineflayer 或 runtime HTTP）。
* **计划与执行分离**：冻结计划有 `plan_hash`（只含决定世界操作的内容）；执行期每步记
  `template_arguments / resolved_arguments / arguments_hash` 作为审计。用户确认之后计划
  不可静默修改 —— 任一步参数/工具/顺序被改动都会让 `plan_hash` 对不上，当前步骤直接
  `AUTHORIZATION` 失败并要求重新确认（授权比对用**存下来的那份** hash，不是现算的）。
* **两阶段计划（§六十八）**：Phase A 只用 SAFE 工具把"去哪、挖什么、手上有没有工具"查清楚
  （`world`/`inventory`/`find_blocks`/`dig_capability`），Phase B 生成**解析过坐标**的冻结
  动作计划再请用户确认 —— 绝不确认"某个未知目标，自动选择"。计划期只挑罐头够得到的那一截
  树干（`y ≤ bot_y + 1`），运行时说 `can_dig` 就不写多余的 move_to。
* **授权模型**：一次确认整份冻结计划 → 签出 `TaskAuthorization`；每步执行前比对
  `TaskStepAuthorization`（step_id + tool + 模板指纹 + plan_hash）。新增 `TurnOrigin.TASK`，
  它**不是**用户回合：Policy 走单独分支，靠"已确认计划 + 步骤授权"放行 MEDIUM，
  **绝不冒充 USER**；授权凭据由 bridge 铸一次性 token（进程内随机 + 30s TTL），
  手工拼 ToolContext 伪造 `task_authorized` 会被拒（fail-closed）。
* **checkpoint 持久化**：复用同一个 SQLite（迁移 27）：`agent_task_runs`（当前快照）+
  `agent_task_checkpoints`（append-only 转移日志）。明确写入"跨进程重启不恢复正在跑的动作"：
  恢复时旧 action 一律按 `RUNTIME_RESTART` 处理并要求重规划。
* **异步等待与恢复**：持续型动作启动即 `WAITING_ACTION`，事件经
  `MinecraftTaskCoordinator` 严格按 `action_id` 绑定当前步骤唤醒；别人的事件不会误唤醒本任务。
* **pause / resume / cancel / expire**：只在安全边界暂停（有前台动作就等它自然结束，
  绝不在 `bot.dig()` 中间硬切状态）；恢复重新校验授权时效 + 世界事实（旧动作失效 → REPLANNING，
  授权过期 → 回 PENDING_CONFIRMATION）；取消立刻终态并**经现有 `minecraft_stop`** 真停，
  迟到的 `completed` 不能翻案、不留前台动作；TTL 到点 EXPIRED。
* **失败分类与重试**：`AUTHORIZATION/VALIDATION/TARGET_LOST/ACTION_FAILED/TIMEOUT/CANCELLED/
  WORLD_CHANGED/OFFLINE/BUSY/INTERNAL`；SAFE 最多重试 2 次，LOW/MEDIUM **一律 0 次**
  （绝不"偷偷再挖一次"）；MEDIUM 失败 → 暂停等用户；连续没有进展 → 暂停（STALLED）。
* **完成判定**：最后一步 action 完成 ≠ 任务完成。计划里的 `expected_final_state`
  （如 `inventory_delta: {oak_log: 1}`）必须用 **SAFE 重新读一次背包**复验后才算 SUCCEEDED。
* **对话入口**：`app/tasks/turn.py` 把用户回合翻译成任务操作（"去砍一棵橡树，挖一块原木并
  捡回来" → 观察 → 冻结计划 → 列出让他确认；"确认/先停一下/继续/停止这个任务" → 控制命令）；
  普通对话仍然是普通对话（"你在哪""背包里有什么""附近有铁矿吗"不创建任务）。游戏内回复
  按行发，确认摘要里每一步都看得见。
* **API / WebUI**：新增 `GET /api/v1/minecraft/task`、`GET /api/v1/minecraft/task/{id}`、
  `POST /api/v1/minecraft/task/{id}/{pause|resume|cancel}`（**没有 confirm**）；
  Minecraft 页新增 Multi-Step Task 面板（Objective/State/Progress/Current Step/Current
  Action/Confirmation/Plan/Last Result/Failure/Rollback，按钮只有 PAUSE/RESUME/CANCEL）。
  WebUI 的「继续」只沿用仍在有效期内的授权，过期就如实拒绝。
* 配置只加四个旋钮（默认保守）：`task.ttl_seconds=600` / `task.max_steps=16` /
  `task.max_replans=2` / `task.no_progress_limit=3`；其余上限是代码常量。
* 验证：`tests/test_task_runtime.py`（32）+ `tests/test_task_authorization.py`（12）+
  `tests/test_task_turn.py`（18）+ `tests/test_minecraft_task_integration.py`（7，确定性 E2E）+
  `tests/test_web_api_minecraft_task.py`（7）+ WebUI vitest 4 个新增用例。
  真机 `scripts/task_smoke_real.py`：正向 5 步全 SUCCEEDED + `FINAL INVENTORY VERIFIED
  {'oak_log': 1}`、pause → resume → SUCCEEDED、cancel → `goal=null`/`isMoving=false` → `REAL SERVER: PASS`。

## Minecraft Phase 4K — Resource Targeting + 单资源真实闭环

* 新增 LLM 工具 `minecraft_find_blocks`（**SAFE 只读、非独占、不新增 ActionRuntime 复合动作**）：
  在罐头**当前所在位置**附近（默认 16 格，最大 32）找指定名称的方块，最多返回 8 条（最大 16），
  每条给出 `block{name}` / `position{x,y,z}` / `distance{goal_near, raw}`（与
  `minecraft_dig_capability` **共用同一套距离口径**），按 `goal_near → raw → x → y → z` 稳定排序。
* 事实来源是 Mineflayer 的 `bot.findBlocks({point, matching: [blockId…], maxDistance, count})`：
  优先按 **block id 数组**匹配，绝不自己遍历世界；起点永远是罐头当前位置（不接受坐标起点）。
  只接受明确的方块名（`iron_ore` / `minecraft:iron_ore` 都会规范成裸名并去重），**不支持 block tag**。
  名字不认识 → `minecraft.block_name_unknown`（422，点出哪个名字）而不是空结果；
  范围内没有 → 正常空结果（不是 404）；被条数上限截断 → `truncated: true`（不是失败）。
* **只定位，不移动、不装备、不挖、不拾取**，也不返回任何"推荐/最佳"（最近 ≠ 最适合挖）；
  不写 WorldPerception 缓存（on-demand query）；任何回合都能调用，不需要确认。
* 配置只加两个键：`minecraft.action.find_blocks.max_distance`（16）与 `.max_results`（8），
  硬上限 32 / 16 在 Service 与 runtime 两侧都拦（不允许"扫全世界"）。
* **真实资源链**（由 smoke 编排，**没有**包成 gather_resource 黑盒）：
  `find_blocks → dig_capability →（必要时）明确 equip → move_to → 再查 capability → dig →
  dropped_items（只认这次挖出来的实体）→ pickup_item → inventory 复核`。
  真机实测：天然橡木在 11 格外（此时 `can_dig=false / reason=too_far`）→ move_to completed
  （`distance_to_target=1.41`）→ 到地方再查 `can_dig=true / dig_time_ms=3000 / raw=1.82` →
  `dig`（oak_log → air）→ 新出现的 Item Entity `#105357 oak_log×1` → pickup
  （`inventory_before=0 → inventory_after=1`、playerCollect、实体消失）→ 重读背包 oak_log 0 → 1
  → 逐槽恢复 → `REAL SERVER: PASS`。
* 验证：Node `find_blocks.test.js` **41 checks**（A–J）；Python 新增
  `tests/test_minecraft_find_blocks_tool.py`（15 个用例 A–K，含"省略上限时用配置默认值"走真实 Service）；
  WebUI 新增 Find Blocks 面板（FIND + 候选表 + **USE AS TARGET 只填坐标**，没有 AUTO GATHER，
  vitest +7）；flying-squid E2E 在真实假服务器上命中 8 个 grass_block 并覆盖拒绝路径；
  真机 smoke 资源链全绿。
* 顺带：`app/tools/schema.py` 的依赖无关校验器补上了 `minItems`（`block_names` 的空数组
  现在在 schema 层就被拒）。

## Minecraft Phase 4J — Tool Capability Knowledge（挖掘能力只读模型）

* 新增 LLM 工具 `minecraft_dig_capability`（**SAFE 只读、非独占、不新增 ActionRuntime 动作**）：
  给三个整数坐标，回答"罐头**现在站的这个位置、现在主手拿着的东西**，对这个方块能不能挖、
  预计多少毫秒"。事实全部来自 Mineflayer 运行时（`bot.blockAt` / `bot.heldItem` /
  `bot.canDigBlock` / `bot.digTime`），**不自己维护方块硬度/工具等级/最佳工具表**。
* 返回 `block` / `held_item{name,count}` / **两种距离口径**（`distance.goal_near` 与
  `distance.raw`，后者与 dig 的距离门禁同一个量）/ `can_dig` / `dig_time_ms` / `reason`
  （`null` / `air` / `too_far` / `not_diggable`）；那里没方块是结构化错误
  `minecraft.block_unavailable`，那里是空气是**正常数据**；**不返回任何推荐工具**。
* **只读**：不改世界、不改背包、不装备、不切槽、**不移动、不导航、不挖**；任何回合都能查、
  不需要确认、可与前台动作并行读取；**不新增任何用户配置**；`minecraft_dig` 一行没动。
* 真机上观察到的真实事实（服务器自己说了算）：同一块 stone，拿 dirt / 空手都是
  `can_dig=true, dig_time_ms=7500`，拿 stone_pickaxe 是 `can_dig=true, dig_time_ms=600`
  —— `bot.canDigBlock` 不看工具（只判 diggable + 距离），工具差异体现在 `digTime` 上，
  我们如实把两个数都给模型，不替它编规则。
* 验证：Node `dig_capability.test.js` **43 checks**（A–L，含"前台动作跑着时只读查询照样执行"与
  "连续查询零副作用"）；Python 新增 `tests/test_minecraft_dig_capability_tool.py`（17 个用例 A–O）；
  WebUI 新增 Dig Capability 面板（CHECK + 七项事实，**没有** AUTO EQUIP/AUTO DIG，vitest +6）；
  真机 smoke 新增 DIG CAPABILITY 段（三种状态 + 世界未改 + 背包恢复 + ensureIdle）→
  `REAL SERVER: PASS`。

## Minecraft Phase 4I — Tool Awareness（工具感知与单方块挖掘联动）

* `minecraft_dig` 增加**可选** `expected_tool`：给了就要求"**执行瞬间**主手确实拿着它"
  （运行前**重新读** `bot.heldItem`，绝不缓存 `minecraft_inventory` 几秒前的结论）；
  主手是别的物品 → 409 `minecraft.held_item_changed`（detail 带 expected/actual）、
  空手 → 400 `minecraft.held_item_missing`，两种情况都**不挖**。
  **不给 = Phase 4B 行为一个字节都不变**（schema 里 `expected_tool` 不是必填）。
* **绝不自动装备**（本阶段的红线）：`expected_tool` 只校验主手，不会从背包里拿工具、
  不会切 hotbar、不会调用 equip —— 换工具走独立的 MEDIUM 动作 `minecraft_equip`（有自己的确认）。
  Node 单测与真机 smoke 都专门验证了"背包里有石镐也拒绝"。
* 规范化（§六）：`stone_pickaxe` == `minecraft:stone_pickaxe`（大小写也一致），
  且在**进确认门之前**统一 —— 确认指纹（`tool+x+y+z+expected_block+expected_tool`）因此稳定，
  换工具/删工具都是另一个动作（mismatch → 旧确认作废 + 按新参数重挂）。摘要写成
  「使用 minecraft:stone_pickaxe 挖掘 minecraft:iron_ore（120, 64, -230）」，工具/方块/位置都在。
* 结果新增 `tool_expected` / `tool_actual` / `tool_actual_after`（语义快照，绝不出 raw Item object；
  没要求工具时前两项是 null）；**成功硬门不变**：`block_after != block_before`
  —— 工具耐久/NBT 不参与判定，也不做自动修理/换下一把/补工具。
* 不判断"哪种工具更合适"（第一版**没有** tool tier 知识）：能不能破坏仍然由 `bot.canDigBlock(block)`
  说了算（挖不动 → 沿用 4B 的 `minecraft.block_not_diggable`）。
* 边界：不新增工具（没有 select_best_tool/choose_tool）、不改 place/pickup/craft、
  `minecraft_inventory` 仍只有五项、**没有新增任何用户配置**、dig 不调用 move_to 也不重新引入
  `pathfinder.goto()`。
* 验证：Node `dig.test.js` **59 → 95 checks**（K–T：缺省/匹配/mismatch/空手/身份变了/canDigBlock
  仍是硬门/**不自动装备**/终态恰好一次/取消 cleanup 不变）；Python 新增
  `tests/test_minecraft_dig_tool.py`（A–J）；WebUI Dig Test 加 Expected Tool + 当前主手 +
  Held item mismatch 提示（vitest 466）；真机 smoke 新增工具感知段（显式 equip → 重读 →
  `dig(expected_tool)` → 真实世界复核 → 不带工具再挖一次）→ `REAL SERVER: PASS`。
* 顺带修好 smoke 的环境问题（如实打印，不是放宽）：开局主手卫生（place 需要主手拿着能放的方块）、
  place 的参考方块必须是实心表面、4I 夹具只清自己给的工具并把主手还原、夹具上/还原改有界等待。

## Minecraft Phase 4H.1 — Move_to Reliability Hardening（消除 Pathfinder 假成功）

* **生产 bug 修复（不是新能力）**：`minecraft_move_to` 的 `SUCCEEDED` 以前由
  `bot.pathfinder.goto()` 的 resolve 决定，而 mineflayer-pathfinder **2.4.5** 的 `goto()` 在
  **空路径**上会静默 resolve（`if (results.path.length === 0) cleanup()` 排在 `noPath` 判断之前）
  —— 真机上 `completed` 时罐头还在 28 格外（有时位移为 0）。现在 `move_to.wait()` 自己挂
  Pathfinder 生命周期（`goal_reached` / `path_update` / `goal_updated` / `path_stop`，
  **先挂监听再 setGoal**）并用**重新读取的实际位置**决定到达：唯一硬门禁是
  GoalNear 半径（1.5 格，口径与 `GoalNear.isEnd(floored)` 一致），与"移动了多少格"无关。
* 事件语义逐条落定：`noPath`（含空路径）→ `path.not_found`；路径搜索 `timeout` →
  `path.not_found`；`partial` → 继续等；`success` + 空路径 → 只有真在半径内才成功，
  否则 **新增的 `path.not_reached`**；`path_stop` → 验证位置；换 Goal → `goal.changed`；
  token（STOP/超时/断开）永远最高优先级，晚到的 `goal_reached` 不会翻案。
  失败都带结构化 `detail{target, actual, distance_to_target, raw_distance_to_target,
  radius, goal_reached, reason}`；成功/失败/取消都会自己清 Goal（SUCCEEDED/FAILED 不触发
  ActionRuntime 的 cleanup），cleanup 仍然恰好一次。
* 对外稳定码新增 **`minecraft.path_not_reached`**（Service 异常 + `_TOOL_STATUS` 500 + 契约表）；
  `MOVE_TO_RADIUS` / `MOVE_MAX_DISTANCE` / `MOVE_TIMEOUT_MS` / 依赖版本**都没有动**
  （不靠升级上游，自己在 runtime 里加防御层）。
* 验证：Node `move_to.test.js` **25 → 76 checks**（含核心回归 B：`path=[] + success` + 实际
  28 格 → 绝不 SUCCEEDED；STOP/超时竞态只允许一个终态；cleanup 恰好一次）；
  flying-squid E2E 的 Test A 容差 2.5 → **1.5**，并新增 **Test D（false-success guard）**：
  对不可达目标 move_to 绝不 completed；pytest **2289**；真机 smoke 新增
  **REAL MOVE_TO FALSE-SUCCESS GUARD** 硬门禁（目标不可达且不改世界）→
  实测 `FAILED / path.not_found` + `distance_to_target 13.04`（旧代码在这里报 completed），
  同一次运行里正向（`positive final distance <= 1.5`）、真 STOP（`CANCELLED` + `goal=null` +
  `isMoving=false` + 漂移 0.00）与 Phase 4H 的整条拾取链全部 PASS → `REAL SERVER: PASS`。
* smoke 侧顺带如实化：删掉"用零距离 move_to 清残留 Goal"的 workaround（§三十三，现在由
  runtime 自己清）、开局把罐头放到地表安全点、STOP 段改为挑干燥落脚点、修掉一条"目标搞错"
  的旧感知断言（感知层没有 inventory，只有 `self.held_item`）。

## Minecraft Phase 4H — Dropped Item Perception + 单实体 Pickup

* 新增 LLM 工具 `minecraft_dropped_items`（SAFE 只读、**非独占**）：把地上的 Item 实体投影成
  语义列表（每条只有 `entity_id` / `item{name,count}` / `position` / `distance`），按距离再按
  `entity_id` 排序、上限 32 条 + `truncated`；玩家/动物/怪物/箭/经验球/船/展示框一律不出现；
  读不出物品栈或位置的实体整条不列；**不泄露** raw metadata / packet / 内部数字 id / UUID / velocity。
* 新增 LLM 工具 `minecraft_pickup_item`（MEDIUM、独占、detached）：`{entity_id, expected_item}`
  两个参数都必填，确认指纹绑 `tool + entity_id + expected_item`，摘要写成
  「拾取附近的 minecraft:oak_log（实体 #123）」。内部自己管导航（`GoalFollow(entity, 1.2)` dynamic，
  **不嵌套 move_to**、不用 mineflayer-collectblock），每 ~250ms 监管一次实体是否还在、
  身份是否被替换（绝不改绑）、物品名是否被换、距离是否还在上限内；进入 1.2 格半径就停导航 +
  清控制位，等服务器 `playerCollect` / `entityGone`，**成功判定 = 目标真的被我们收走 且
  背包增加**（`after > before`，不是 `after === before + count`）；被别的玩家捡走 →
  `pickup_target_lost`，实体消失但背包没增加 → `pickup_unconfirmed`（宽限 1.5s）。
* 真机抓出并修掉两处**成功路径**缺陷：① 服务器可能在两次轮询之间就把物品收进背包
  （掉落物掉到下层、罐头踩到），这时"进入半径"分支从没跑过 —— SUCCEEDED 不触发 cleanup，
  于是残留一个 `GoalFollow`；现在成功分支也自己 `setGoal(null)`（Node 测试 W4 钉住）。
  ② 掉落物读取原先要求 metadata 里有 `present` 字段，而 1.21+ 的物品栈
  （`{itemId, itemCount, components…}`）**没有**它 —— 真机上 `dropped_items` 恒返回 0 条；
  现在按 `metadataKeys` 找 `item`/`item_stack`（1.21.1 是第 8 项）+ 值扫描回退，并保留
  `present: false` = 空栈的老语义，**绝不硬编码槽位数字**。
* 确认门 / 可信玩家 / 一次性 / TTL / 开发者入口不得自授权全部不变；`allow_medium` 默认仍是
  **false**（现在覆盖七个 MEDIUM 动作）；配置只加 `pickup.max_distance`（16）与 `pickup.timeout`（30）。
* 验证：Node `dropped_items.test.js` 42 checks（含真实 1.21.1 metadata 形态）
  + `pickup_item.test.js` 77 checks（H–W，含"成功也必须收导航"）；Python 测试 +33（pytest 2287）；
  WebUI 新增 Dropped Items 面板与 Pickup Test 表单（PICKUP 只能发起确认请求）；
  flying-squid E2E 覆盖拒绝路径（真机 Item Entity 造不出来 → 明确 SKIPPED）；
  **真实服务器 smoke 真机把掉落物捡进背包**（dig → dropped_items → 真 STOP → pickup：
  playerCollect / 实体消失 / 背包 0 → 2 三项硬证据）→ `REAL SERVER: PASS`。
* 顺带把 smoke 脚本里的两处**噪声**改成如实陈述：① 远距离 `move_to` 在真机上可能"瞬间完成"
  （Pathfinder 对空路径静默 resolve）——改成按方向重试拿取消窗口，四个方向都拿不到就 SKIPPED，
  不再把"动作自己已经结束"记成 STOP 失败；② 背包比对区分"丢东西"（硬失败）与
  "多出地上散落物"（vanilla 行走自动拾取，如实打印备注）。

## Minecraft Phase 4G — Crafting Table（3×3 · 明确指定工作台）

* `minecraft_recipe_lookup` 与 `minecraft_craft` 增加**可选** `crafting_table: {x,y,z}`：
  不给 = 玩家自身 2×2（Phase 4F 行为完全兼容），给了 = **那一张工作台**的 3×3。
  **不新增工具**（没有 workbench_lookup/workbench_craft），只有一条 craft 语义。
* 只接受明确的整数坐标：`nearest` / `auto` / `any` 这类隐式目标一律拒绝（§五）。
  工作台实时验证：没有/被挖掉 → `crafting_table_missing`，不是 crafting_table
  （切石机、锻造台…一律不算）→ `crafting_table_invalid`，超过 `craft.crafting_table.max_distance`
  → `crafting_table_too_far`；**绝不自动去找/走过去/放一张工作台**。
* 确认指纹在有工作台时**额外绑定坐标**（同一配方换张工作台就是另一次授权），
  摘要写成「用 8 个 oak_planks 在 (100, 64, 100) 的 Crafting Table 制作 1 个 chest」。
* 执行前用**当前**配方表 + **当前**工作台方块 + **当前**背包重新解析（确认后工作台被挖掉/被换掉
  都不会继续），执行后仍然重读整个 inventory 复核（产物增加 + 材料减少）。
* `recipe_id` 规则不变（`!` 前缀已区分"需要工作台"，2×2 与 3×3 配方不会碰撞）；
  `MAX_DATA_DEPTH = 6` 保持不变（新字段只在第 3 层，没有再加深度）。
* 验证：Node `recipe.test.js` 78 checks / `craft.test.js` 83 checks（含 2×2↔3×3 架构回归、
  确认后工作台消失/被换/太远、3×3 的取消·超时·竞态）；Python 测试 +15；WebUI 新增
  Crafting Context 选择器（Player 2×2 / Crafting Table 3×3 + 坐标）；flying-squid E2E 用
  **真实 setblock 出来的工作台**验证 3×3 lookup 与全部拒绝路径；真实服务器 smoke 真机完成
  `8 木板 → 1 箱子`（table lookup / craft / 重读 / 还原工作台 / 逐槽恢复）→ `REAL SERVER: PASS`。

## Minecraft Phase 4F — Crafting（单个配方 · 玩家 2×2）

* 新增 LLM 工具 `minecraft_recipe_lookup`（SAFE 只读、**非独占**）：查"罐头自己 2×2 背包能做
  什么"，返回稳定可读的 `recipe_id`（如 `stick*4=oak_planks*2`）+ 产物 + 材料 + 现在够不够；
  如实区分 `available` / `insufficient_material` / `crafting_table_required` / `recipe_not_found`；
  只给语义投影，绝不暴露 raw Recipe / 数字 id / metadata。
* 新增 LLM 工具 `minecraft_craft`（MEDIUM）：只接受 `recipe_id`、**一次一个配方、一次执行一次**
  （没有 count/times —— 避免"执行几次"与"产出几个"的语义混淆）；只走玩家自身 2×2
  （`bot.craft(recipe, 1, null)`），不找/不放工作台、不自动准备材料、不做 recipe chain；
  确认摘要展开成「用 2 个 oak_planks 制作 4 个 stick」；执行前用**当前**配方表与**当前**背包
  重新解析（`recipe_changed` / `recipe_unavailable` / `material_insufficient`），执行后
  **重读整个背包**核验"产物增加 + 材料减少"（否则 `craft_unconfirmed`）。
* 顺手修掉一个真实 bug：`ToolResultProcessor` 的数据深度上限原来只有 4，会把
  "配方 → 产物/材料"这种第 5 层结构静默打成 `null`（模型只看到 null）。现在命名成
  `MAX_DATA_DEPTH = 6` 并加了边界回归测试。
* 验证：Node `recipe.test.js` 54 checks + `craft.test.js` 56 checks（F–P：成功/重读复核/
  unconfirmed/配方变化/材料不足/取消/超时/竞态/cleanup 恰好一次/独占）；Python 测试 +62；
  flying-squid E2E 验证语义投影与全部拒绝路径；真实服务器 smoke 真机完成
  `recipe_lookup → craft → 重读 inventory → 清夹具恢复`（`REAL SERVER: PASS`）。
* `allow_medium` 默认仍是 `false`（现在覆盖六个 MEDIUM 动作）。

## Minecraft Phase 4E — Container Control（Chest / Barrel 读取 + 单项存取）

* 新增 LLM 工具 `minecraft_container_inspect`（SAFE 只读，但**独占**）：读一个**单方块**
  Chest / Barrel 的真实内容（`{type, label, position, size}` + 只列非空格子的 `slots`）；
  内部固定 open → read → close，**没有** open/close 这种长期状态型工具（LLM 永远不持有半开的窗口）。
* 新增 LLM 工具 `minecraft_container_transfer`（MEDIUM）：**一个方向、一个容器格、一个背包格、
  一个物品、一个数量**（`direction = withdraw | deposit`）；目标格被别的物品占用 →
  `destination_occupied`（**绝不隐式交换、绝不换格**）；用 `bot.transfer` 把 source/destination
  各钉死在单槽；执行后**重新读**容器与背包两侧，按真实变化量判定，否则
  `container_transfer_unconfirmed`；报成功之前必须关窗，关不上如实报 `container_close_failed`。
* 只支持单方块 chest / barrel：trapped chest、双箱（窗口 54 格）、潜影盒、熔炉、漏斗、
  发射器等一律 `container_unsupported`；不做箱对箱搬运、批量整理、deposit-all/withdraw-all。
* 窗口生命周期是硬要求：成功/失败/异常/超时/取消/断开都必须 best-effort close；
  ActionRuntime 的 `start`/`run`/`cleanup` 新增 controller 参数（动作私有资源挂在其上），
  cleanup 至多一次、可重复调用、close 异常不阻止终态。
* 调试面：`POST /api/v1/minecraft/container_inspect`、`POST /api/v1/minecraft/container_transfer`；
  WebUI 新增 Container 面板（INSPECT 结果表 + 一键填 source + Transfer 表单）。
* 验证：Node `container.test.js` 85 checks（A–O 生命周期 + 类型/结构拒绝）、
  `container_transfer.test.js` 88 checks（P–AC：withdraw/deposit/单槽钉死/占用/合并/
  重读复核/取消与超时竞态/终态恰好一次）；Python 测试 +35；flying-squid E2E 用**真实 chest GUI**
  验证 open → read → close（并证明窗口没泄漏）；真实服务器 smoke 真机 inspect + withdraw + deposit
  且容器与背包逐项恢复。
* `allow_medium` 默认仍是 `false`（现在覆盖五个 MEDIUM 动作）。

## Minecraft Phase 4D — Inventory Control（Equip + 单项 Inventory Move）

* 新增 LLM 工具 `minecraft_equip`（MEDIUM）：把背包里**明确指定**的物品拿到主手；
  只支持 `destination=hand`，按槽位顺序确定性选第一个匹配 stack，已经拿在手里时如实回
  `already_equipped`（零副作用），执行后重读 `heldItem`，不符 → `equip_unconfirmed`。
* 新增 LLM 工具 `minecraft_inventory_move`（MEDIUM）：**一个物品、一个来源槽、一个目标槽、一个数量**
  （9–44 玩家窗口绝对槽位）；目标被别的物品占用 → `destination_occupied`（**绝不隐式交换**）；
  用 Mineflayer 原生 `transfer`（source/destination 钉死在单槽），执行后重读 source/destination，
  不按 `+count` 硬编码 → 否则 `move_unconfirmed`。
* 两个动作都走既有确认门（MEDIUM + USER 回合 + 可信玩家 + 独占 + 一次性确认）；
  摘要形如「把 minecraft:dirt 拿到手里」「把 37 格的 minecraft:dirt ×1 移到 9 格」；
  参数指纹覆盖全部参数；WebUI / 开发者入口**不能**自授权（非用户回合 → `confirmation_not_user_turn`）。
* 调试面：`GET /api/v1/minecraft/inventory/slots`（原始槽位表，只读恒 200；**不是** LLM 的数据源）、
  `POST /api/v1/minecraft/equip`、`POST /api/v1/minecraft/inventory_move`；
  WebUI 新增 Inventory Control 面板（Equip Test / Move Test + 槽位表 + 一键填 source）。
* `minecraft_inventory` 的 LLM 切片保持五项不变（不含槽位/NBT/window）；
  `allow_medium` 默认仍是 `false`。
* 验证：Node 单测 equip 59 checks / inventory_move 56 checks（含取消/超时/竞态/cleanup 恰好一次）、
  Python 测试 +34（两个工具文件 + API + 确认门不可自授权）、E2E 拒绝路径、
  真实服务器 smoke 真机 equip + inventory_move（含 `/give` 夹具与 `/clear`、
  逐槽比对确认状态完全恢复）。

## [Unreleased] — Minecraft Phase 4C · 单方块 Place + Inventory 只读切片

- **`minecraft_inventory`（SAFE，只读）**：背包切片只有五项——`online` /
  `selected_hotbar_slot` / `held_item{name,count}` / 按物品名聚合的 `items[{name,count}]`
  （数量倒序、上限 40 种）；raw slot / NBT / window / 容器 / 盔甲 / cursor 一律不进 LLM。
  不需要意图/可信/确认，但仍需在线（刻意不进 `OFFLINE_TOOLS`）；非独占（移动中也能读）。
- **`minecraft_place`（MEDIUM，单方块）**：`{x, y, z, face, expected_item}` ——
  坐标必须**整数**；`face` 只允许 up/down/north/south/east/west（`reference = target − face_vector`）；
  `expected_item` 是"当前主手必须拿着这个物品"的**硬约束**（不 equip、不切 hotbar，
  名字归一化 `minecraft:` 前缀）；只往**空气**里放；参考方块必须是实心；距离 ≤5；
  执行用 `bot.placeBlock(referenceBlock, faceVector)`，完成后**重新读**目标方块，
  只有 `air → expected_item` 才算成功（否则 `block_place_unconfirmed`，带 expected/actual）。
- **确认门沿用 4A/4B**：指纹覆盖 tool+x+y+z+face+expected_item，改任一项即失效并重挂；
  摘要写清"放什么、放哪里、哪个面"；确认只负责授权，执行前 runtime 仍重新校验
  （主手物品 / 目标 / 参考 / 距离）。
- **新错误码**（全部结构化、带 detail）：`minecraft.held_item_missing`、
  `minecraft.held_item_changed`、`minecraft.target_occupied`、
  `minecraft.reference_block_missing`、`minecraft.block_unavailable`、
  `minecraft.block_place_unconfirmed`。
- **action 行为**：place 与 dig/move_to/follow 互斥（exclusive）、启动即 RUNNING（detached）、
  cleanup 只清控制位；CANCELLED/TIMEOUT/断开/退出统一走 cleanup 且至多一次，
  stop 与 placeBlock 同时落定也只有一个终态（有 race 用例）。
- **感知联动**：放置成功后 WorldPerception 的 near diff 看到 `air → placed block`；
  Agent Context 的 `activity` 变成「刚放好了 <方块>」。
- **WebUI**：连接页新增 Place Test 面板（显示主手物品/快捷栏槽/背包摘要；X/Y/Z + Face 下拉 +
  Expected Item + 用手持物品填入 + PLACE/STOP；小数坐标与空物品名本地拦截）；
  新增 `GET /api/v1/minecraft/inventory` 与 `POST /api/v1/minecraft/place`
  （开发者入口，**必须过确认门**）。
- 配置：`minecraft.action.place.timeout`（默认 30s）与 `max_distance`（默认 5）；
  **`allow_medium` 默认仍为 false**（dig 与 place 都受它约束）。
- 测试：`minecraft_runtime/test/place.test.js`（91 checks，含 CANCELLED/TIMEOUT/race）、
  `tests/test_minecraft_inventory_tool.py`（13）、`tests/test_minecraft_place_tool.py`（16）、
  API/面板用例若干；flying-squid E2E 增加 inventory 只读 + place 的三类拒绝
  （空手/非法 face/小数坐标，这台假服务器没有 `/give` 与掉落，bot 永远空手）；
  真实服务器 smoke 增加 inventory + place 六层证据。
- **真实服务器（2026-10-06）**：`REAL SERVER: PASS` —— 真放（air → sand）、
  真实扫描到它、感知输入连续 3 次稳定、手持物品 5→4、并把自己放的那一块挖回。

## [Unreleased] — Minecraft Phase 4B · Single-Block Dig

- **第一个真正修改 Minecraft 世界的动作 `minecraft_dig`**（MEDIUM）：破坏**一个**明确指定的
  方块。参数 `{x, y, z, expected_block}`（`additionalProperties: false`），风险表加 MEDIUM，
  执行前 runtime 用实时状态复检（方块存在 / 与 expected_block 一致 / 可挖 / 距离），
  挖完再读一次 `blockAt` 确认真的没了（`block_break_unconfirmed` 绝不假装成功）。
- **一次只挖一块**：没有连续挖矿/矿物搜索/自动换目标/自动换工具/捡掉落/导航；
  目标超出 5 格直接拒绝（不把 dig 和导航耦合）；`exclusive` —— 不能边挖边走。
- **新错误码**（全部结构化、带 detail）：`minecraft.block_not_found`、`minecraft.block_changed`
  （带 `expected`/`actual`）、`minecraft.block_not_diggable`、`minecraft.block_too_far`、
  `minecraft.block_break_unconfirmed`；Mineflayer 的中断/异常统一翻译，绝不外泄原始文本。
- **Phase 4A Debug 硬化**：`POST /minecraft/agent/confirm` 的 `create_test` 只能创建
  `minecraft_test_*` 测试专用工具的确认——正式动作（dig）的确认只能来自真实用户回合，
  管理台不可能凭空造出一条可被消费的授权。
- **确认门照旧**：`allow_medium`（默认 false）→ USER 回合 → 可信玩家（游戏内）→ 忙 →
  需要确认（挂 PENDING）→ 用户说「确认」→ 相同参数再调用 → 消费 → 执行；参数/方块名变了
  → mismatch 并作废重挂；过期 → expired 并重挂。
- **感知联动**：方块被挖掉后，WorldPerception 的 near diff 真的看到那次移除
  （`world.changed`，阈值 1 时 `changed_blocks >= 1`）；挖成功后 Agent Context 的
  `activity` 变成「刚挖掉了 minecraft:stone」（不写长期记忆）。
- **WebUI**：连接页新增 Dig Test 面板（X/Y/Z/Expected Block + DIG/STOP）；`POST
  /api/v1/minecraft/dig` 走正式链路且**必须过确认门**（点 DIG 只会得到 409
  `confirmation_required` 与待确认信息）。
- 配置：`minecraft.action.dig.timeout`（5~120s，默认 30）与 `max_distance`（≤6，默认 5）。
- 测试：新增 `minecraft_runtime/test/dig.test.js`（54 checks）、`tests/test_minecraft_dig.py`（12）、
  `tests/test_minecraft_dig_flow.py`（15）、dig 的确认门用例（24）；flying-squid E2E 增加
  dig Test A–D（真挖 + block_changed + not_found + too_far，含感知同步）；真实服务器 smoke
  脚本（`test/smoke_real_server.js`）增加 dig/STOP/block_changed 段（本机服务器未开，
  跑出来如实打印 `REAL SERVER: NOT AVAILABLE`）。

## [Unreleased] — Minecraft Phase 4A · Action Confirmation Gate & Minecraft Chat User Bridge

- **游戏内玩家说话 = USER 回合**（新 `MinecraftChatBridge`）：Minecraft 玩家在服务器里说
  「罐头，你过来」现在会走正常 Tool Loop（SAFE 不限、LOW 需可信玩家），回复再用
  `MinecraftService.send_chat` 发回游戏内；沙盒外部事件链**保持不变**（两个消费者，
  互不替代、互不触发）。会话独立于 QQ：`minecraft:{host}:{port}:{username}`。
- **防循环硬门禁**：罐头自己发的聊天绝不回到她自己的 LLM 回合（拿不到自己的名字时宁可不回）；
  悄悄话不公开回、系统行忽略、并发上限 4 条、回复最长 200 字符。
- **可信玩家**（`minecraft.agent.trusted_players`）：游戏内 LOW 及以上动作只对名单里的人执行，
  其他人得到 `minecraft.user_not_trusted` 且**零动作调用**——但仍能正常聊天；
  QQ/WebUI 用户不受这份名单约束。身份由运输层 `origin_metadata` 声明，
  **不能**靠它伪造用户回合（派生键覆盖，有测试）。
- **确认门基础设施**（MEDIUM/HIGH/DESTRUCTIVE）：确认对象绑定 `user_id + session_id +
  arguments_hash`，只能由新的 USER 回合消费，一次性、TTL（默认 60s）、有界内存、重启全失效；
  错误码 `confirmation_required / invalid / expired / mismatch / not_user_turn` 全部结构化。
  首次调用返回"需要确认"+ PENDING 信息；用户明确说「确认」后再用相同参数调用才会执行。
- **本阶段没有任何世界修改动作**：dig / place / attack / craft / eat / inventory / container /
  redstone 一律没有 Tool（有安全测试断言生产注册表里不存在这些名字）；确认门由测试桩驱动验证。
- 策略顺序固化为 §三十三 的十步，并把「需要在线」从按名字列白名单改成「默认需要在线，
  只有 minecraft_world / minecraft_stop 例外」——Phase 4B 加动作时不会漏掉在线门。
- WebUI 连接页新增 **Pending Confirmation** 面板（只读 + CREATE TEST / CANCEL / EXPIRE），
  `GET /api/v1/minecraft` 新增 `agent.confirmations` 与 `agent.trusted_players`；
  新增 `POST /api/v1/minecraft/agent/confirm`（**只能缩小授权**，没有 confirm/consume）。
- 新增测试：`tests/test_minecraft_confirmation.py`（16）、
  `tests/test_minecraft_agent_confirm_gate.py`（18）、`tests/test_minecraft_chat_bridge.py`（18）。

## [Unreleased] — Minecraft Phase 3E.1 · Intent Propagation Integrity

- **修掉 Phase 3E 留下的安全边界缺口**：`CharacterRuntime._tool_context()` 曾**无条件**
  写 `minecraft_explicit_intent = True`，于是主动发言 / 后台生成 / 管理台干跑也带着
  "用户明确要求"的标记——LOW 动作（move_to / follow_player）在这些回合里会被放行。
- **新增结构化事实来源 `TurnOrigin`**（`app/character/turn.py`）：
  `USER`（真实用户消息）/ `INITIATIVE`（主动发言）/ `BACKGROUND`（后台、行为页预览）/
  `SYSTEM`（管理台干跑、系统生成）。`respond()` 的 `turn_origin` 是**必填关键字参数**
  （没有默认值可依赖，来源不明就写不出口），策略层读到的意图布尔**只能**由它派生。
- **禁止字符串推断**：不做 `user_text == "（主动发起）"` 这类判断——同一句话在不同回合里
  结论不同（用户回合里写「（主动发起）」仍放行；主动发言回合里写「罐头你过来」仍拒绝）。
- 调用点全部显式声明：QQ/WebUI 用户消息 = `USER`；`compose_initiative` = `INITIATIVE`；
  行为页"代打一句话看她怎么回"的预览 = `BACKGROUND`（预览绝不动游戏世界）；
  管理台工具干跑 = `SYSTEM`（`note` 里说明"LOW 需要用户回合"）。
- Policy / 六个 Minecraft Tool / Action Runtime / 风险等级**一行未改**；SAFE 动作
  （查世界、停止）在任何回合都照常可用。
- 新增 `tests/test_minecraft_intent_propagation.py`（12）：全部走真实
  `CharacterRuntime` → `_tool_context` → `ToolRuntime` → `MinecraftActionPolicy` → 假 Service
  链路，含任务书点名的六个用例（user/initiative/background/system × move_to/follow_player）、
  "字符串不可推断"、`respond` 无默认值、SAFE 不受影响、管理台干跑按系统回合。

## [Unreleased] — Minecraft Phase 3E · Minecraft Action Tools & Agent Bridge

- **罐头的大模型第一次能安全地使用 Minecraft 身体**：六个正式 Tool 进入 CatooBot 的
  Tool Registry —— `minecraft_world`（只读世界）/ `minecraft_chat` / `minecraft_look_at` /
  `minecraft_stop`（SAFE）/ `minecraft_move_to` / `minecraft_follow_player`（LOW）。
  挖、放、攻击、合成、背包/容器等**一律没有 Tool**（Phase 4 才做）。
- **LLM 不直接碰 runtime**：路径固定为 Tool → `MinecraftActionPolicy` → `MinecraftService`
  → Action Runtime（`action_id`/timeout/取消/cleanup/事件/日志全部沿用既有机制）；
  Tool 层没有 HTTP、没有 mineflayer、没有 pathfinder（源码级测试断言）。
- **风险分级 + Policy 门**：SAFE 自动允许；**LOW 必须有用户明确要求**（意图门认"这一轮是不是
  用户发起的对话"这一结构事实，Tool Layer 不做 NLP）——模型自己在自主回合里推理出"我应该跟过去"
  也会被拒；离线/未启用/前台忙/跟随目标不在附近各有稳定错误码；配置 `minecraft.agent.tools.*`
  （`allow_safe/allow_low` 默认 true，MEDIUM 及以上默认 false 且尚无实现）。
- **持续型动作**：`move_to` 与 `follow_player` 启动即返回 `RUNNING + action_id`，绝不阻塞请求
  （导航最长 30s、跟随最长 120s）；终点/失败/超时/取消经 `minecraft.action.*` 事件回到
  CatooBot 的 Minecraft Context。为此 `move_to` 由同步语义改为与 follow 同款的 `start`/`wait`
  两阶段——**动作语义（非破坏性、距离上限、超时、STOP、cleanup）一行未改**。
- **Minecraft Context**：事件驱动地维护"正在做什么 / 上一次动作 / 刚做成了什么"，世界事实
  （坐标/附近玩家）按需现取；每轮给模型注入一行紧凑处境（≈150–240 字符），
  Raw Snapshot 与历史快照永不进上下文；**Action 事件绝不自动开新的 Agent 回合**。
- **顺手修掉的循环收敛缺口**：被循环守卫拦下的工具调用现在也消耗本轮调用预算，
  否则「拦住 → 不记账 → 模型再要一次 → 又拦住」会一直转到时间预算耗尽。
- **WebUI**：连接页新增「LLM Tool Debug（只读）」——六个工具的风险/启用/是否允许与原因，
  加上 Agent 上下文（在线/维度/坐标/附近玩家/当前与最近动作）；`GET /api/v1/minecraft`
  新增 `agent` 块；MOVE TO 按钮文案改为"已开始移动（到达/失败由事件更新）"。
- 新增 `tests/test_minecraft_agent_tools.py`（44）+ `tests/test_minecraft_agent_e2e.py`（9，
  自然语言闭环：你过来 → world → move_to；跟着我 → follow RUNNING；停 → stop 取消）。

## [Unreleased] — v2.1 · Console World Log: Change-Driven Reporting

- **审计结论**：刷屏来自 `SandboxRuntime.tick()` 末尾的 `narrate().world(...)`
  （`narrate_ticks` 打开时每 tick 一行）——Phase 14 把 tick 变成每秒一次之后，
  同一行世界状态被每秒重复打印。
- **只改终端日志（§2/§3/§13）**：新增 `app/runtime/console_world.py` ——
  `ConsoleWorldSnapshot`（frozen dataclass，只含**终端可见语义**：动作名 / 地点 /
  模式 / 渲染后的需求条目）+ `console_world_snapshot(runtime)`（只读投影，复用
  原来的 `status_line`/`modes.ids`/`needs.summary_line`）+ `ConsoleWorldReporter`
  （与上一次输出比较，相同则完全静默）。`tick()` 的世界推进、Need 演化、
  Goal/Action/Decision/Influence/世界锁**一行未改**，调度频率仍是 1 秒
  （测试断言 ticks == 60 时世界日志只 1 行）。
- **比较的是显示值，不是内部值（§4-§7）**：需求按渲染后的 band 文本比较（排序归一化），
  同一 band 内的 float 漂移不触发输出，跨 band（如"想喝点冰的"→"想喝点冰的（很强烈）"）
  才输出一次；计数器/时间戳/revision 等内部变化一律不触发。状态只在 runtime 内存，
  重启后首次状态照常输出一次（§14）。
- **其余日志不受影响（§12）**：QQ/决策/打断/恢复/关系/承诺等事件型日志链路未动；
  配置项 `logging.narrate_world_ticks` 语义更新为"可见状态变化时播报"，
  WebUI 配置页标签同步更新。
- 新增 `tests/test_console_world_log.py`（9 个测试：60 次 tick 只打印 1 次且世界照常推进、
  内部变化不打印、同 band 漂移不打印、动作变化打印一次后继续安静、需求跨 band 打印一次、
  外部事件改变可见状态打印一次、重启后首次再打印、快照字段仅显示语义、
  Reporter 仅在变化时输出），总测试 1430。

## [Unreleased] — v2.1 Phase 16 · Live Social Influence & Autonomous Interaction Continuity

- **审计优先（§4）**：影响判定、打断/恢复、社交事实、会话、Commit Guard 全部已存在
  （Phase 3/7/8/9/12/13/14），本轮只补会话连续性缺口，未新建任何社交系统。
- **社交会话（§28/§29/§46-§49/§87/§90）**：新增 runtime-only 的
  `_social_session`（person / social_space / started_at / last_activity_at / turns /
  interrupted，只存计数与时间，**绝不保存聊天正文**），超时由
  `social.interaction_episode_timeout_seconds`（默认 900s）控制。会话进行中，
  同一人/同一空间的后续消息**不再制造嵌套打断**（发布 trace-only 的
  `SOCIAL_INTERRUPT_SUPPRESSED`）：事实照记、回复照出，她的生活只有一层暂停。
- **单层暂停（§8/§28）**：`_interrupt_action` 现在拒绝覆盖已存在的
  `InterruptedActionContext`（第二次打断按不可恢复处理），暂停身份永不丢失。
- **会话结束即恢复生活（§91/§92）**：会话静默超时后，下一次 tick 会先把暂停的
  生活接回（同一 `InterruptedActionContext`、同一暂停身份、诚实的新生命周期），
  再考虑新决策。
- **保持不动**：`ExternalInfluenceEvaluator` 仍是唯一影响入口且确定性（无 LLM）、
  InterruptEvaluator/Goal/Commitment/Relationship/Memory/Experience/Conversation
  语义与身份、Commit Guard 的 stale 判定、Phase 13/13.1 的 lane 与策略、Phase 14 的
  世界锁；无主动 QQ（§44/§86）。
- 新增 `tests/test_live_social_influence.py`（14 个测试：NO_EFFECT 不碰她的生活、
  OBSERVE 边聊边继续、WAKE 不重启动作、INTERRUPT 暂停精确实例并记录原因与剩余时长、
  会话结束后同一暂停身份恢复、重复消息不嵌套打断（一层 + trace）、20 条消息仍是一个
  会话且只存计数、空闲时无暂停、同一 transport 事件只产生一次关系/承诺副作用、
  影响判定 20 条消息 0 次模型调用、回复思考期间会话变化 → stale 静默不发送、
  打断中重启不产生第二个实例也不自动完成、社交路径绝不主动发 QQ、OneBot 端到端
  （收到消息→社交事实→回复→出站，她的动作不受影响）），总测试 1421。

## [Unreleased] — v2.1 Phase 15 · Autonomous Behavior Stability & Need-Driven Life Coherence

- **先审计、再最小修复（§2/§23/§69）**：实测 3 小时无人聊天模拟——0 次 LLM 调用、
  1 个目标、动作实例数远小于 tick 数、需求有界（无抖动、无饥饿、无 runaway）。
  结论：既有确定性环路本身是稳的，本阶段只补一处真正的缺口。
- **决策机会守卫（§7-§11/§26）**：新增 `SandboxRuntime.decision_opportunity()`——
  由世界已有的状态（world_revision / 运行中的实例 / critical 需求 band / 活动目标的
  **dedupe key**）算出的确定性身份；`tick()` 只在身份变化时进入决策点，同一未改变的
  处境不再重复决策（更不会重复问模型），并发布 trace-only 的
  `AUTONOMOUS_ACTION_SUPPRESSED(reason=same_opportunity)`；正常决策发布
  `AUTONOMOUS_DECISION`。守卫纯粹是运行时控制状态：不落库、不进世界、不进记忆（§10）。
  tick 报告新增 `decision_opportunity` / `decision_suppressed` / `action_selected`（§33/§75）。
- **保持不动**：Goal 优先级档位、Commitment → Goal 桥、Relationship/Memory/Experience
  identity、Conversation/OneBot/Clock 语义（§25/§69-§74）；无主动 QQ（§40/§90）。
- 新增 `tests/test_autonomous_behavior_stability.py`（14 个测试：同一处境不重复决策且
  不重复问模型、真实世界变化会重新打开机会、需求只在真实完成后被缓解且只一次、
  需求始终在 0..1、平静 tick 不换动作不重启实例、无可行步骤的目标进入冷却而非每秒重试、
  不可能的需求不会启动非法动作、多压力下选择确定（同种子两次一致）、低优先级目标不被
  丢失且无重复目标、打断/恢复不产生第二个实例、重启后需求/目标/实例连续、
  6 小时与 24 小时空聊天模拟有界（动作≠tick 数、LLM 调用有界、记忆不爆炸、承诺不被凭空
  创建）、同种子重放序列一致（不同种子各自确定）），总测试 1407。

## [Unreleased] — v2.1 Phase 14.1 · World Tick Unification (Legacy Tick Removed)

- **旧的“每 10 分钟刷新一次 Sandbox”正式退役（§2-§6）**：`Bot._start_sandbox_jobs()`
  不再注册 `sandbox_tick` 作业（不是配置关闭，而是不再存在这条注册路径）；
  共享 scheduler 继续跑它的记忆/清理/维护作业，但**不再拥有**
  `SandboxRuntime.tick()` 的调用权。全仓审计确认再无该作业名。
- **唯一世界时钟（§5/§9-§11/§26）**：`RuntimeScheduler` 是唯一 owner，每
  `runtime.tick_interval_seconds`（默认 1s）醒一次，把**真实经过时间**作为步长传给
  `SandboxRuntime.tick(minutes=elapsed/60)`——调度频率与世界推进量彻底分离
  （1 秒间隔 ≠ 1 分钟世界时间）；不再走 `_tick_minutes()` 的 1 分钟下限。
- **`sandbox.tick_seconds` 语义重定义（§8/§33）**：保留字段与既有测试兼容，但只作为
  手动 tick / 恢复单次步进的**上限兜底**；示例配置与注释里“生活节奏：10 分钟一跳”的
  旧说法已删除，改为说明新的时间模型。
- **保留不变**：`tick`/`wakeup` 的世界锁（§28/§29）、有界 catch-up
  （`max_catchup_seconds`，§23/§24；重启恢复仍由沙箱自身的有界 `_settle_gap` 完成）、
  Goal/Action/Commitment/Experience/Memory 语义与 Phase 10/13/14 的 identity（§17-§22/§27）。
- 新增 `tests/test_runtime_sole_tick.py`（13 个测试：代码中不再出现旧作业名、
  共享 scheduler 不注册任何拥有 sandbox.tick 的作业（真实 Bot 构造）、真实 10 秒 →
  世界 10 秒、真实 600 秒只处理一次、短 tick 逐秒累计不漂移、唯一 owner 计数、
  重启后仍唯一、重复 tick 不产生第二个动作、目标/经历/记忆不重复、承诺按真实时间
  激活与失约且只 settle 一次、100 tick < 10 次模型调用、自主 tick 绝不发 QQ、
  停机重启后锚点=现在且无额外追帧），总测试 1393。

## [Unreleased] — v2.1 Phase 14 · Long-Lived Runtime & Autonomous Life Continuity

- **审计优先（§84）**：世界时间锚点 `last_tick` 早已持久化（`sandbox_state`），
  且沙箱 `_settle_gap` 已实现「有界恢复、不按秒重放」——两者直接复用，
  **未新增迁移、未新建世界时间表、未新增第二套世界/生命系统**。
- **薄调度器 `app/runtime/scheduler.py`（§9-§16/§64）**：唯一职责是"每隔 Δt 问一次
  世界"——调用既有 `SandboxRuntime.tick()`；世界步长仍是 `sandbox.tick_seconds`，
  调度间隔是新增的 `runtime.tick_interval_seconds`（默认 1s）。
  **关键修正**：调度器把**真实经过时间**作为步长传入（而不是让沙箱的 1 分钟下限兜底），
  否则 1 秒调度会让她的生活以 60 倍速前进；tick ≠ LLM（无决策不叫模型，测试断言
  `llm_calls ≪ ticks`）、tick ≠ mutation（没有真实跃迁就不写世界）。
- **追帧有界（§13-§15/§69-§71）**：停机的经过时间超过干扰线时只走**一次有界步进**
  （`runtime.max_catchup_seconds`，默认 300s；测试：停机 30 分钟 → 1 次 tick、
  processed_seconds=300、而非 1800 次），并发布 trace-only 的 `RUNTIME_CATCHUP`；
  不会因此灌入大量 Experience。
- **统一排序点（§34-§36/§49）**：`SandboxRuntime.tick()` 与 `wakeup()` 现在共享
  同一把世界锁——tick 与外部消息绝不交错半个状态（测试用插桩证明两段各自原子完成）。
- **生命周期（§31-§33/§53-§55/§74）**：`Bot` 按「恢复世界 → 启动调度器 → 启动 OneBot
  网关」的顺序接线，关闭时反向停止；`start()` 幂等（两次调用仍只有一个循环），
  `stop()` 后无 orphan；调度循环用单调 deadline（§67）并在落后时重锚（§68 无忙循环）。
  新增 trace-only 事件 `RUNTIME_TICK` / `RUNTIME_CATCHUP`（不推进 revision，事件历史
  沿用既有上限）。
- **边界**：自主生活**绝不主动发 QQ**（无任何主动消息路径，§40/§41/§75）；不改
  Relationship / Memory / Conversation / OneBot 语义（§59-§62）；两角色两个 runtime
  的时钟与生活完全独立（§80/§81）。
- 新增 `tests/test_runtime_scheduler.py`（19 个测试：tick 无跃迁不改状态、到期完成
  动作并落经历、重复 tick 不产生第二个实例、tick 不做模型调用（40 tick ≤ 5 次）、
  活跃动作跨重启保持同一实例、目标跨重启仍在、承诺跨重启同状态、停机跨过窗口后
  被 settle、短停机一次有界步进、长停机 30 分钟≠1800 tick、追帧不灌经历、
  Tick/Message 共享一个排序点且各自原子、Tick/Message/Tick 无半状态、幂等 start、
  stop→start 仍一个循环、2000 次 tick 只有零星真实跃迁、8 小时空聊天窗口后收到
  消息看到当前世界、10 小时长跑有界且无重复目标/实例、双 runtime 时钟与生活隔离），
  总测试 1380。

## [Unreleased] — v2.1 Phase 13.1 Remediation · Lane Ordering & Lifecycle Integrity

- **overflow 不再脱离 lane（§1-§10）**：lane 溢出不再把被降级的消息丢到 lane 外
  并发执行（那会让 M3 抢在 M2 之前进入沙箱、破坏 FIFO）——改为**原地降级**：
  `_LaneItem.respond = False`，仍保留原位置，按序 ingest，只是不再进入对话运行时
  （无 LLM 调用、无出站回复）。FIFO 现在对**世界事实的摄入**成立，而不只是对回复
  顺序成立；跨 lane 仍并发（测试用慢 LLM 验证交错）。
- **优雅关闭与文档一致（§11-§19）**：`stop()` 先停收件，再按
  `shutdown_timeout`（新增配置，默认 5s）等待已开始的 turn 与已提交的出站投递收尾，
  超时才取消剩余任务，然后关闭传输——不会挂死，也不留 orphan；未开始的会话工作
  可以丢弃，但已进入沙箱的事实不受影响。
- **反向 WS 生命周期回调（§21-§29）**：既有 `OneBotV11Server` 增加 connect/
  disconnect 生命周期回调（`set_lifecycle_handler`），`ServerTransport` 转发给网关；
  网关状态正确经历 CONNECTED → DISCONNECTED → CONNECTED，断线**不产生任何沙箱
  事实**（无 SOCIAL_INTERACTION / EXTERNAL_EVENT_RECEIVED）。反向 WS 下由 NapCat
  重连到我们（`reconnect_client()`），退避阶梯仅作为状态恢复去抖，**不再暗示
  CatooBot 主动拨号**（仅有 `can_dial` 的传输才会主动重连）。
- **响应策略明确为上限（§32）**：`_mode_ceiling()` 现在只**下调**、绝不上调沙箱的
  影响判定——`reject` 与 `no_effect` 一律静默，@ 提及也必须在沙箱策略允许时才说话。
- 新增/加强测试 12 个（overflow 保序且保事实、被降级消息无 LLM 无出站、多次溢出
  五条消息全序、跨 lane 并发仍成立、已开始的 turn 完成后再关闭、挂起 turn 不阻塞
  关闭、断线回调传播与重连、重连后重放去重、断线不产生沙箱事实、reject/no_effect/
  mention 三态策略门控），总测试 1361。
- **测试去时序化**：网关测试的跨 lane 交错与 overflow 起点原先依赖固定 sleep，
  在 CI（Linux/负载更高）上判定不稳；现全部改为事件握手（A 等 B 完成、M1 进入
  turn 用 Event 通知），本地连跑三次稳定。顺带把 CI 的 pytest 步骤改为失败时输出
  `::error::` 注解（job 日志不一定可读，注解 API 可以），并清理仓库根部遗留的
  一次性补丁脚本。

## [Unreleased] — v2.1 Phase 13 · Real External Runtime Integration & OneBot Event Gateway

- **审计优先（§75 Step 1）**：传输层已存在并全部复用——`OneBotV11Server`（反向 WS、
  token 校验、单连接、`get_login_info` 解析 self_id）、`BotApi`（`send_msg` 等）、
  `parse_event`、`adapt_qq_message`（QQ 原语 → ExternalWorldEvent）。**没有第二套
  运行架构**；新建的只有网关与规范化层 `app/integrations/onebot/`。
- **入站规范化（§7-§9/§35-§38）**：`NormalizedMessageEvent`（transport_event_id /
  self_id / message_type / message_id / user_id / group_id / display_name /
  raw_message / plain_text / segments / mentioned_self / reply_to_bot /
  raw_time / ingest_time / ingest_seq / social_space_id），支持 raw OneBot JSON 与
  既有 typed `MessageEvent` 两种入口；未知字段与未知 segment **原样保留**（§93），
  只有真正无法成型的事件才拒绝（§92）。Sandbox 只见 `ExternalWorldEvent`。
- **身份与门禁（§10-§16/§19/§20/§54）**：`person_id` 只由 `persons.for_qq()` 解析
  （nickname 绝不绑定身份）；self-message 守卫与"非本账号 self_id 一律丢弃"双保险；
  去重键 = `self_id + transport_event_id`（`message:<self_id>:<message_id>`，无
  message_id 时用规范字段 digest，**绝不是随机 UUID**），有界 TTL+容量去重缓存
  （重启即空，不假装跨进程幂等）。
- **排序与背压（§21-§31）**：`character_id:social_space_id` 一条 lane，lane 内 FIFO、
  lane 间并发；每 lane 有 `max_pending_per_lane`，溢出时**确定性丢弃最旧的未开始
  回复**，但该消息的世界事实会立即单独入库（§31：绝不静默丢失已验证事实）。
- **对话链（§32/§39-§48）**：lane worker 先 ingest（`submit_external` + `wakeup`），
  再由既有 `ConversationRuntime` 生成回复 → `commit_conversation_response()` **先
  提交后入队** → outbound worker 用既有 `BotApi` 发送；`silent` 绝不触网；同一
  turn 只发一次；失败有界重试（`outbound_max_retries`，默认 3）且重发的是同一条
  已提交回复，耗尽后记录 `delivery_status=failed`，不无限重试、不重新跑模型。
- **连接生命周期（§49-§53/§60/§61）**：CONNECTING/CONNECTED/DISCONNECTED/
  RECONNECTING/STOPPED；断线重连走 1/2/4/8/16 秒封顶退避；连接状态**永不进入
  Sandbox**；`stop()` 先停收件、等 lane/出站收尾、再关传输，不留 orphan task；
  `drain()` 作为统一的"空闲点"。
- **trace-only 事件（§64/§65）**：`EXTERNAL_TRANSPORT_RECEIVED / DEDUPED / DROPPED /
  OUTBOUND_RESPONSE_QUEUED / SENT / FAILED`，只记传输事实（turn_id / mode / status /
  attempts），不推进任何 revision、不存聊天历史、不落库。
- **配置（§57/§58）**：扩展现有 `onebot` 段（不另起一套）：`gateway_enabled`（默认
  false，opt-in）、`self_ids`、`dedupe_ttl`、`dedupe_max_size`、
  `max_pending_per_lane`、`outbound_max_retries`、`reconnect_max_seconds`；
  example 里 token 恒为 `""`，真实 token 仍只在本地 config/.env。
- **接线**：`Bot` 在 `gateway_enabled=true` 且 Sandbox 启用时构建网关——消息交给
  沙箱回复（v1.2 聊天回复路径让位并告警），notice/meta/request 仍走原处理路径；
  关闭时行为与之前完全一致。
- 新增 `tests/test_onebot_gateway.py`（25 个测试：私聊全链回复、群聊字段与身份分离、
  @ 与非 @ 策略、self-message 守卫、重复事件只入沙箱一次、跨账号同 message_id、
  未知账号/未知人、lane 内 FIFO、跨 lane 并发、溢出丢弃但保事实、静默不触网、
  stale 不入队、重试成功/耗尽、重连与退避封顶、stop 无 orphan、无直连捷径、
  100 次重放仍一次 turn、畸形事件与未知字段、单 turn 异常隔离、端到端
  QQ→回复、传输适配器映射与事件分流）+ `tests/fake_onebot.py`；总测试 1349。

## [Unreleased] — v2.1 Phase 12.1 Remediation · Response Commit Guard & Proposal Protocol

- **响应提交守卫（§2-§8/§13）**：新增 `ConversationRuntime.commit(response)` 与
  `SandboxRuntime.commit_conversation_response(response)`——这是回复交给 transport 的
  **唯一**入口（adapter 不得自行判断 stale）。提交时重查 `character_id` 与
  `world_revision` / `cognitive_revision`：任一不符即抑制为 silent/fallback
  （reason = `character_changed` / `world_changed` / `cognitive_changed`），并把
  text / memory_refs / experience_refs / action_candidate_id 全部清空；新鲜响应原样返回。
  守卫严格只读：不推进 revision、不写任何状态、不发送网络请求。
- **提交可观测（§14）**：新增 trace-only 事件 `CONVERSATION_RESPONSE_COMMITTED`
  （payload 带 `commit_status = fresh|suppressed`、mode/source/reason/refs），
  不推进任何 revision、不存聊天历史。
- **严格 JSON 协议（§15-§17）**：`_parse()` 不再从输出里“抠” JSON——整段输出必须
  **就是**一个 JSON object；前后有任何文字、多个对象、顶层非 object
  （list/string/null）一律视为无效（silent + fallback，无 mutation）。NaN / Infinity /
  -Infinity 通过 `parse_constant` 直接拒绝，并在 `ResponseProposal.confidence`
  上加 `allow_inf_nan=False` 与有限性复核（§17 双层）。
- 未改动：mode 集合、memory/experience 引用白名单、action candidate 语义、
  置信度阈值（仍复用 `decision_min_confidence`）、Phase 8-11 语义、schema。
- `tests/test_sandbox_conversation.py` 新增 11 个测试（fresh 提交原样返回、
  TOCTOU 世界变化抑制发送、认知变化抑制发送、跨角色响应拒绝提交、
  重复提交确定且只读、JSON-only 通过、前缀文字/后缀文字/双对象/非 object 顶层/
  NaN 与 Infinity 全部拒绝），总测试 1324。

## [Unreleased] — v2.1 Phase 12 · Conversational Response Runtime & Fact-Safe Social Reply

- **新模块 `app/sandbox/conversation.py`（§5-§59）**：`ConversationTurn`（本轮 turn：
  turn_id / character_id / source / external_actor_id / person_id / social_space_id /
  group_id / message_text / event_id / world+cognitive revision；**不落库**）、
  `ResponseProposal`（严格 JSON：mode / text / memory_refs / experience_refs /
  action_candidate_id / confidence）、`ConversationResponse`（含 source / reason /
  dropped_refs / 两个 revision 戳）、`ConversationRuntime`（receive → resolve person →
  `cognitive_context()` → 提案 → 校验 → 响应）。**没有新数据库、没有第二套认知系统、
  没有 Planner/Agent/情绪系统**。
- **唯一上下文来源（§15）**：所有上下文来自既有的 `SandboxRuntime.cognitive_context()`
  （Phase 11 的 person / 关系 / 未完成约定 / 最近共同经历 / 共同记忆 / 当前世界），
  Response Runtime 不自己查记忆或关系。角色名取自 `CharacterDefinition`，不硬编码。
- **事实安全（§18/§19/§23/§51/§52）**：引用必须存在于本轮上下文——记忆引用只能取
  本轮检索/情境里的 `memory_id`，经历引用只能取本轮 `experience_id` / `episode_key`；
  行动只能引用调用方给出的**世界派生候选**（`action_candidate_id`）。越界引用一律
  丢弃并记入 `dropped_refs`，绝不执行动作（命名候选 ≠ 执行）。
- **过期保护（§25-§27）**：响应在返回前重新核对 `world_revision` 与
  `cognitive_revision`；模型思考期间世界或认知一变（关系/承诺变化只动 cognitive），
  整条回复作废（mode=silent、source=fallback、reason=world_changed / cognitive_changed），
  不重试、不发送。
- **失败处理（§53-§57）**：模型不可用 / 非法 JSON / 低置信（复用既有
  `decision_min_confidence`）/ 空文本 / 超长（新增唯一长度配置
  `conversation_max_response_chars`，超长直接拒绝而非截断）→ 一律 silent + fallback，
  不抛异常、不编造人格化兜底台词。
- **只说法不写世界（§2/§32/§62 V）**：生成回复前后 world/cognitive revision、关系、
  承诺、目标、记忆、经历、动作、mutation 审计全部不变；`ConversationResponse.text`
  只是语言，永不成为事件/记忆/承诺/目标。
- **事件（§33/§34）**：新增 `CONVERSATION_RESPONSE_PROPOSED` /
  `CONVERSATION_RESPONSE_EMITTED`，仅作 trace：记录 turn_id/mode/source/refs/
  action_candidate/reason 与 ≤80 字文本摘要，**不推进任何 revision、不存聊天历史**。
- 新增 `tests/test_sandbox_conversation.py`（22 个测试：私聊正常回复与提示词边界、
  上下文只含当前 person、双角色隔离、合法/非法记忆引用、合法/非法经历引用、
  合法/非法行动候选（不执行）、世界变化与认知变化导致的 stale 作废、生成前后
  全状态只读、非 LLM 层确定性、模型不可用/非法 JSON/低置信/空文本/超长、
  策略上限（silent 时根本不叫模型）、当前世界优先（sleep 不被 gaming 记忆覆盖）、
  群聊字段透传、邀请流程不被重复决策），总测试 1313。

## [Unreleased] — v2.1 Phase 11 · Social Cognition & Conversational Continuity

- **Social Situation 投影（§5/§26）**：`CognitiveContext` 新增唯一的小投影
  `social_situation`（person_id / relationship / open_commitments /
  recent_shared_experiences / relevant_shared_memories / continuity 计数），
  实时从既有 Sandbox / Relationship / Commitment / Experience / Memory 组合而成，
  **不建表、不新增系统、不删任何既有字段**（§3/§18/§27）。事实层次固定为
  当前世界 > 关系 > 未完成约定 > 最近共同经历 > 长期共同记忆（§8）。
- **确定性相关性（§11-§15/§23/§24）**：共同经历只取**当前对话者**、且有稳定
  episode key 的 `shared_activity`，按「最近 → importance」排序，最多 3 条；
  「最近」只有一个来源：新增配置 `social_context_recent_window_minutes`
  （默认 2880 分钟 = 48 小时，0 = 不限时只用条数）。长期记忆沿用 Phase 10 的
  加权检索（权重一字未改），并按 person 过滤出「属于这个人的」共同记忆。
- **身份一致与失败安全（§20/§21）**：handle → person_id 在检索前解析，person /
  关系 / 约定 / 记忆 / 情境共用同一个 id；无法解析时 person={}、
  social_situation={}、person boost=0、关系与约定为空——绝不按名字或历史猜人。
- **只读不变量（§17/§33/§37-§40）**：构建上下文前后 world_revision /
  cognitive_revision / 关系状态 / 承诺（状态与 revision）/ 记忆条数 / 经历条数 /
  mutation 审计长度全部不变；连续两次构建输出完全一致（§41）。
- **提示词渲染（§26/§55）**：聊天侧新增【最近与当前对话者一起做过的事】一段
  （仅来自已发生的共同经历，最多 3 条 + layer 标记），既有关系/约定/经历渲染不变。
- 附带：`store.recent_experiences()` 现在一并返回 `metadata` 与 `episode_key`
  （只增列，向后兼容），供情境按 person 过滤。
- 新增 `tests/test_sandbox_social_cognition.py`（14 个测试：handle→person 全链一致、
  无法解析时不猜、四类社会事实正确归位、记忆与经历角色不混、当前对话者的记忆获得
  boost、他人不借光、跨角色情境隔离、当前世界优先（sleep 不被 gaming 记忆覆盖）、
  构建只读（revision/关系/承诺/记忆/经历/审计不变）、两次构建完全一致、
  上限（3 约定/3 经历/memory_context_limit）、超出时间窗的旧 episode 不进「最近」、
  记忆 provenance.episode_key 与经历 episode_key 对齐、提示词渲染），总测试 1291。

## [Unreleased] — v2.1 Phase 10.2 Remediation · Memory Episode Identity Alignment

- **记忆 episode identity 与经历层同源（§3/§16）**：`MemoryCandidateBuilder` 不再自己
  推导优先级（旧的 `commitment_id or action_instance_id …` 会让同一承诺下的两次真实
  活动共用一个 dedupe key），而是直接使用经历层的 canonical `experience.episode_key`
  （`action:<ActionInstance>` > `commitment:<id>` > `interaction:<fact id>`），
  只有 Phase 10.1 之前的历史记录没有该字段时才走同优先级的 fallback。
  因此：**一个承诺 + 两个 ActionInstance → 两条 Experience + 两条 Memory**；
  同一实例重放 / 同一承诺且无实例重放 / 跨重启重放仍各自只有一条。
- **provenance 增加 episode_key（§15）**：社会记忆的 provenance 记录其所属 episode
  （用于「Memory → Experience episode」审计），dedupe 依旧只用 `dedupe_key`，
  不从 JSON 反推；检索权重与 promotion 规则一字未改（§17/§18），未改 schema、
  未新增 migration、未回填历史 Memory（§22/§23）。
- `tests/test_sandbox_shared_experience.py` 新增
  `TestMemoryEpisodeIdentityAlignment`（6 个测试：同承诺两个实例 → 2 条经历 + 2 条记忆
  且 dedupe key 为 `shared:<person>:action:act_one|act_two`、同实例重放仍是 1+1、
  同承诺无实例仍 1+1（`commitment:` 键）、两个不同事实 → 2+2（`interaction:` 键）、
  跨重启重放记忆行 id 不变、两个角色同 episode key 各自一条且 id/key/character 隔离），
  并把 social spec 单测期望对齐到带前缀的 canonical key；总测试 1277。

## [Unreleased] — v2.1 Phase 10.1 Remediation · Persistent Episode Idempotency

- **episode identity 落库（§4-§7/§12）**：迁移 **26** 给 `sandbox_experiences` 增加
  `episode_key TEXT` 与 `UNIQUE(character_id, episode_key)` 索引（SQLite 中 NULL 互不
  相等 → 历史行天然不受影响，未回填、未删除）。canonical key 沿用 Phase 10 的优先级
  （`action:<ActionInstance>` > `commitment:<id>` > `interaction:<fact id>`，只存一个），
  另外写入 `metadata["episode_key"]` 便于审计。
- **数据库是幂等边界（§7/§9）**：`SandboxStore.save_experience()` 改为
  `INSERT … ON CONFLICT DO NOTHING` 并返回 `inserted / existing / unavailable`——
  幂等保证来自数据库唯一约束，不使用「先查后写」作为唯一保障；Runtime 的 flush 记录
  `duplicate_episodes_skipped` 计数。重启后重放同一事实不再产生第二条经历行。
- **内存聚合保持（§8）**：`ExperienceBuilder` 的 `_by_correlation` / `_by_episode`
  原样保留，两层各司其职（实时链 + 持久化幂等）。
- **不误合并（§21）**：同一 commitment、**不同 ActionInstance** 的两条事实现在
  各自成 episode（instance 优先于 promise）；同一 commitment 且都没有实例时仍是
  一条（一个承诺一次履行 = 一个 episode）。非 shared 的既有经历类型不带 episode key
  （NULL），语义不变（§14）。
- `tests/test_sandbox_shared_experience.py` 新增 `TestPersistentEpisodeIdentity`
  （8 个测试：跨重启重放仍只有一条经历行且 memory 不增、同 episode key 在两个角色
  世界各得一条、同实例两条事实合一条、无实例时以 fact id 兜底且跨重启幂等、
  两条不同事实是两条 episode、同承诺无实例仍一条、同承诺不同实例必须是两条、
  历史 NULL 行可共存不被误并），总测试 1271。

## [Unreleased] — v2.1 Phase 10 · Social Experience & Shared Memory Continuity

- **审计与复用（§3/§33）**：没有新建任何 Experience/Memory/Relationship 基础设施——
  全部扩展现有 `ExperienceBuilder`、`MemoryCandidateBuilder`、`SandboxMemoryStore`、
  `CognitiveContext`（`MemoryRepository` 表结构未动，无新表、无新检索器）。
- **共同经历（§4/§5/§6）**：新增 `ExperienceKind.shared_activity`——只由**已验证的**
  `SOCIAL_INTERACTION(shared_activity)` 事实产生，且时长必须达到 Phase 9 的
  `MIN_FULFILL_DURATION_MINUTES`（复用同一常量，未定义第二份阈值）；无 commitment
  的临时共同活动同样成立（不因此创建承诺/目标/关系变化）。经历 metadata 完整保留
  `person_id / activity / duration_minutes / commitment_id / action_id /
  action_instance_id / interaction_id / significance`。
- **一个真实行为 = 一个 episode（§7/§12/§24）**：ExperienceBuilder 新增 episode 索引
  （ActionInstance > commitment > interaction fact），因此
  `ACTION_COMPLETED + SOCIAL_INTERACTION(shared_activity) + COMMITMENT_FULFILLED`
  即使来自不同 correlation 也会聚合成**一条**共同经历（kind 升级表把
  shared_activity 排在最高），replay 同一事实也只有一个 episode、一条记忆；
  两条真实活动（不同 ActionInstance）仍是两条不同的 episodic social memory。
- **确定性重要性阶梯（§9/§21）**：`0.4` 基础 + 履约 `+0.25` + 长活动（≥60 分钟）
  `+0.05` + major `+0.15`；普通短共同活动因此**只有 Experience、不进长期记忆**
  （低于既有 promotion threshold 0.5），守约/重大/够长的才成为社会记忆。全程
  无 LLM、无随机。
- **记忆内容与溯源（§10/§11/§23）**：`MemoryCandidate` 新增 `provenance`，社会记忆
  落库时写入 `person_id / name / activity / duration_minutes / commitment_id /
  action_id / action_instance_id`（复用 `memories.provenance`，未加表）；记忆文案是
  可核验的一句话（"和 X 约好的 gaming 活动完成了（约 35 分钟）"），identity 含
  episode（`shared:<person>:<episode>`），重启后仍可按 person 召回。
- **Person-aware 检索（§14-§17）**：`retrieve_relevant()` 新增 `person_id` 参数与
  `person_match` 权重（四项权重等比重平衡，总和仍为 1.0）：当前对话者的共同经历获得
  flat boost，其他话题事实仍按原分数参与；不按 person 过滤、不引入第二套检索。
  `CognitiveContext` 在检索前把平台 handle 解析为 `person_id` 并暴露
  `person{person_id, display_name, external_id}`。
- **边界保持**：Memory 永不回写世界（§18/§20）、只有 SocialInteractionFact 能改
  relationship（§19）、`character_id` 全隔离（§22）、`match_shared_activity` 与
  Phase 8/9 语义未改动（§26/§27）。附带一处稳健性修正：candidate 插入改为
  `INSERT OR IGNORE`，同一 candidate 重放不再可能中断 tick。
- 新增 `tests/test_sandbox_shared_experience.py`（14 个测试：守约共同经历是一条完整
  episode 且吸收 action 记录、无承诺的共同活动不产生承诺/目标、低于阈值不算共同
  经历、长/major 的确定性加权与提升边界、两次真实活动两条记忆、replay 不堆积、
  person boost 与无关者不借用、跨角色同人不串、当前世界仍权威、记忆读写不动关系、
  重启后 person 链仍在、全链路无 LLM、social spec 显式），总测试 1263。

## [Unreleased] — v2.1 Phase 9.1.1 Remediation · Shared Activity Window Matching

- **fallback 匹配先收窗口再判歧义（§11/§14 修正）**：`match_shared_activity()`
  现在先构造「**当前真实履约窗口内**」的候选集
  （person + shared_activity + open + activity 匹配 + `earliest_at ≤ now`（若有）
  + `now ≤ due_at + 120min`（若有）），再判定：0 条不履约、1 条精确履约、
  >1 条才算 ambiguous（不履约 + WARNING）。**未来**的承诺（窗口未开）与**已过期**
  的承诺（超过 due+grace）都不再参与当前时刻的歧义判定——"今晚 20:00 的约定"不会
  因为"明晚还有一条同活动约定"而被判歧义。
- exact `commitment_id` 路径与 `_commitment_step_shared()` 的 ActionInstance 严格
  规则保持不变；未重新引入按 person/activity 猜承诺的逻辑。
- `tests/test_sandbox_commitments_integrity.py`：新增
  `test_the_open_window_wins_over_a_future_twin`（C1 今晚在窗口内、C2 明晚 →
  C1 completed、C2 open、fulfilled==1、ambiguous==0）；原歧义用例改为构造**真正
  重叠的窗口**（同一晚 8 点与 9 点两条同活动约定）后验证仍不猜。总测试 1249。

## [Unreleased] — v2.1 Phase 9.1 Remediation · Commitment Outcome Integrity

- **履约必须收口到自己的目标（§2-§6）**：新增总线处理器
  `GoalManager.on_commitment_fulfilled()`——`COMMITMENT_FULFILLED` → 按
  `target_commitment == commitment_id` 找到**唯一**目标（不按 person/activity 猜、
  不建新目标/新步骤、不重跑动作）→ `completed` + `progress=1` + 步骤 `completed`
  → 发布 `GOAL_COMPLETED`，`causation_id = COMMITMENT_FULFILLED.event_id`，
  仍用目标自己的 `goal_<id>` correlation。修掉了"成功履约反而被
  `cancel_stale()` 取消"的语义错误（`cancel_stale` 现在只会看到已完成目标）。
  处理器**按事件类型过滤**（总线是类型无关的，误响应其它带 commitment_id 的事件
  会被修正为只响应 COMMITMENT_FULFILLED）。
- **共享活动精确匹配（§9-§14）**：事实优先携带 `commitment_id`
  （由承诺目标驱动的活动由 `_commitment_step_shared()` 写入；被接受的邀请若唯一
  命中一条未完成承诺也会带上）。没有 id 时只允许严格回退：先按
  （person + shared_activity + open + activity）取候选——**候选 > 1 直接拒绝并
  WARNING**（同一活动两条承诺绝不猜）；候选 == 1 再用真实履约窗口校验
  （`earliest_at ≤ now ≤ due_at + grace`，不用 `latest_at`）——太早、太晚都不履约。
- **改期精确定位（§15-§19）**：`appointment_rescheduled` 同样优先
  `commitment_id`；否则候选必须恰好 1 条，模糊则**零状态变更 + WARNING**。
- **严格 ActionInstance（§20-§22）**：`_commitment_step_shared()` 现在要求
  「目标 active + 步骤 active/completed（ACTION_COMPLETED 先发布，故允许刚完成）
  + 步骤实例 id 非空且与完成实例精确相等」；空实例是 wildcard，直接拒绝——
  不会再凭 action_id 猜出共同经历。
- **持久化一致性（§27）**：履约后 `sandbox_commitments.status=completed`、
  `sandbox_goals.status=completed`、`current_step.status=completed` 三者同时落库；
  重启后已完成承诺/目标不会被重新加载或复活（历史行只留在库里）。
- 新增 `tests/test_sandbox_commitments_integrity.py`（11 个测试：履约完成精确目标
  且事件因果正确、履约不被 stale 取消、重启后三者一致且不复活、未来承诺不被提前
  履行、同活动双承诺不猜（WARNING）、唯一匹配无 id 也可履约、显式 id 精确履约
  （不受同活动双承诺影响）、改期只动指定承诺、模糊改期零变更、唯一承诺可无 id
  改期、空实例步骤绝不产生共同活动事实），总测试 1248。

## [Unreleased] — v2.1 Phase 9 · Social Commitment & Obligation

- **审计结论（§4）**：全仓 promise/commitment/obligation/appointment/pledge 能力为 **0**；
  既有的 v1.2 open loop（`app/continuity` 的 OpenLoop/SharedExperience、
  `app/behavior/topics` 的未聊完话题）是**对话层**的"话没说完"，没有时间语义、
  没有履行/失约概念，无法表达"答应某人某时做某事"——因此本阶段新建
  `SocialCommitment`，其余全部复用（EventBus / StateMutation / Goal / Decision /
  Experience / Memory / Continuity / CognitiveContext）。
- **新模块 `app/sandbox/commitments.py`**：`SocialCommitment`（commitment_id /
  character_id / person_id / kind / status / strength / description / revision /
  priority / target_action / target_activity / time_hint / earliest-latest-due /
  provenance（source_interaction_id + source_event_id）/ correlation + causation /
  metadata）、`CommitmentManager`、`CommitmentDetector`、`CommitmentGoalBridge`。
  **四种状态严格分离（§3）**：关系≠承诺≠目标≠记忆；person 只用 Phase 8 的
  `person_id`（绝不回存 QQ id）。
- **确定性检测（§9/§10/§12/§35/§36）**：只有 `invitation_accepted` /
  `promise_made` / `appointment_confirmed` 这类**显式事实** + 可解析的**未来**时间
  才创建承诺；模糊表达（下次/有空/改天/以后…）与无时间暗示一律不创建（宁可不建，
  不猜）；`parse_time_hint()` 只认一个封闭词表（今晚/明晚/明天/周X/N点/N分钟后/HH:MM），
  不可读即不创建。**邀请 ≠ 承诺，接受 ≠ 承诺**（§10）。
- **持久化（§6/§28）**：迁移 **25** → `sandbox_commitments`（含
  character_id+status / +person_id / +due_at / +status+person_id 四个索引）；
  `SandboxStore.save_commitment/list_commitments`；重启恢复（§29）后先
  `sweep()`（窗口激活/超时判定）再由桥重新评估——**绝不"旧承诺各建一个目标"**。
- **Commitment → Goal（§14-§17/§19）**：新增 `GoalKind.fulfill_commitment` +
  `GoalSource.social_commitment` + `Goal.target_commitment`（dedupe 用
  commitment_id，所以一个承诺永远只有一个目标）；`CommitmentGoalBridge.evaluate()`
  只在执行窗口内建目标，优先级 = kind 档位 + strength ± 关系亲近度（只影响排序）；
  承诺**绝不**直接 `ActionSystem.start()`，仍走 Goal → Decision → Validator → Action。
- **履行/失约/改期（§20-§25）**：`shared_activity` 真正发生且达到最小持续时间
  （10 分钟）才算 `fulfilled`——她自己的目标驱动完成同样成立（承诺提供了另一方）；
  due + grace（2 小时）之后仍无结果才算 `broken`（迟一分钟不算失约）；
  改期 = 同一 commitment_id、revision+1、旧计划归档进 metadata.schedule_history；
  取消区分 character/other_party/system，礼貌早退不是背叛。`revision` 同时用于
  §37/§38 过期保护：目标在**执行前**用 `commitment_id + commitment_revision`
  复核，不一致则取消该目标且不执行动作。
- **事件与因果（§40/§41）**：`COMMITMENT_CREATED/ACTIVATED/RESCHEDULED/FULFILLED/
  CANCELLED/BROKEN`，全部带 causation/correlation，指向创建它的那条验证事实；
  承诺变化记为 `StateMutation(affects_world=False)` 并只推进 `cognitive_revision`
  （§39，不新增第三套全局 revision）。
- **关系与记忆边界（§22/§32/§33/§46/§47）**：承诺结果只通过
  `SocialInteractionFact`（`commitment_fulfilled/broken/rescheduled`，
  INTERACTION_RULES 新增三条小步规则）进入 Phase 8 引擎——**没有**任何
  `commitment → trust += …`；承诺本身不写记忆，只有 meaningful/major 的结果经
  `ExperienceKind.commitment_outcome` → MemoryCandidate（social）进入既有记忆。
- **上下文（§30/§31）**：连续性快照只带最多 3 条最要紧的未完成承诺；
  与当前对话者相关的未完成承诺进入 CognitiveContext 与聊天提示词（仅作信息，
  Context Builder 不执行任何目标）。
- `_complete_action` 的共享活动事实泛化：邀请接受路径与"她自己按承诺去做"路径
  产生同一种 `shared_activity` 事实（身份用 ActionInstance 精确匹配）。
- 新增 `tests/test_sandbox_commitments.py`（30 个测试：显式约定/模糊不建/邀请≠承诺/
  角色隔离/持久化/重启恢复/窗口内成目标/一承诺一目标/走决策层/共享活动履行/
  grace 不误判/失约/改期同一生命线/礼貌取消/目标过期取消/执行前拒绝/打断恢复/
  关系后果（履行↑、失约↓）/自身创建不进记忆/重大结果进记忆/连续性最多 3 条/
  认知上下文只含当前对话者/生命周期不用 LLM/多方案才用 LLM/跨角色结果隔离/
  24h 无 QQ 零承诺/48h 后自然收束/同种子可复现/时间解析封顶），总测试 1237。

## [Unreleased] — v2.1 Phase 8.1 Remediation · Person Identity + Cognitive Revision

- **`external_ids` 持久化真实平台映射（§1-§6）**：`remember_person()` 现在接收
  `external_ids` / `source`，数据库里存的是真句柄（`{"qq": "123456"}`），
  不再把 `person_id` 塞进 external_ids（person_id 已是独立字段）。空映射永不
  覆盖已知映射。**兼容旧库**：`decode_external_ids()` 把
  `{"person_id": ...}` 视为 legacy 无效映射 → 读取时报告为空，绝不猜成 QQ 身份，
  下一次真实平台身份到达时覆盖。新增 `person_row()` 作为带 legacy 解码的读入口。
- **多 Core Friend 一一映射（§7-§11）**：`PersonIdentityResolver` 只在**显式配置**
  下把 QQ 绑到 Bible 好友——`core_friend_identities: {"123": "空凛"}` 或
  `core_friend_ids` 的映射写法；旧列表写法在「一个 QQ + 一个 Bible 核心好友」时
  继续生效（空凛行为不变）。**歧义不猜**：多核心好友 + 旧列表、或多 id 无名字，
  一律退化为 `person_qq_<hash>` 并记一次 WARNING。Bible 编译器同时修正
  `core=index==0` → 按 `type: core_friend` 判定（Bible 说了算；未声明时保留
  第一条为回退），因此「多个核心好友」现在真的能被识别。
- **Cognitive Revision（§12-§17/§24/§25）**：关系变化不再只被世界 revision 掩盖——
  新增 `runtime.cognitive_revision`（transient，不落库），**一次关系事实 = 一次
  bump**（无论动了几个维度）；`DecisionRequest` 同时记录
  `world_revision` + `cognitive_revision`，Validator 两者分别检查，认知过期返回
  `cognitive_changed`。**stale 即拒绝**：`world_changed` / `cognitive_changed`
  不再走确定性地重挑，而是直接 `DECISION_REJECTED`，本次请求的任何动作都不执行
  （其他被拒提案仍按 §11 回退）。`affects_world=False` 语义保留：关系不是物理世界。
- **邀请序（§18-§20）**：同一外部事件产生的社交事实现在**先完成**再构造
  `DecisionRequest`（`_apply_influence_effects` 返回本次事件的社交任务，决策分支
  前 await）——邀请决策看到的永远是最新关系；其他后台社交事实仍异步。
  关系变化只刷新认知版本，**不会**反向触发新决策。
- **Mutation 顺序与落库失败（§26/§27）**：`RelationshipUpdateEngine.apply()` 改为
  before → 计算 after → 应用 → **记 StateMutation** → 落库 → `RELATIONSHIP_CHANGED`；
  mutation 的 before/after 与最终持久化值一致。`save()` 失败时记 WARNING 并计入
  `persist_failures`，事件 payload 带 `persisted: false`——内存真相继续使用，
  世界不崩、不伪装成功、不新建 dirty 系统。
- 新增 `tests/test_sandbox_relations_identity.py`（12 个测试：QQ 句柄落库、
  生产路径落库、legacy 自映射不被当身份、双核心好友一一映射且关系隔离、
  旧单 id 行为保持、两种歧义都不乱绑（含 WARNING）、认知 bump 而世界不动、
  world/cognitive 三态独立、认知过期的 Proposal 被拒且不执行、邀请事实先于
  DecisionRequest、落库失败如实上报）+ `tests/fixtures/character_two_friends.md`；
  总测试 1207。

## [Unreleased] — v2.1 Phase 8 · Social & Relationship Dynamics

- **新模块 `app/sandbox/relations.py`（复用既有 Experience/Memory/Decision，不新建
  第二套社交系统）**：`PersonIdentity`（QQ id 与 Person 身份分离，`external_ids`
  保存 qq/bible 映射）、`SocialInteractionFact`（**唯一输入**——只有"被验证的互动事实"
  能推动关系）、`RelationshipState`（trust / familiarity / closeness / social_comfort
  + 正负互动计数，按 character_id + person_id 作用域，绝无全局 map）。
- **确定性小步更新（§12/§14/§16）**：`INTERACTION_RULES` 增量 × 显著性倍数，
  单事实上限 `MAX_DELTA_PER_FACT = 0.05`，跨过 `MILESTONES=(0.5,0.7,0.85)` 记为
  major；每次变化经既有 `StateMutation` 记录并发布 `RELATIONSHIP_CHANGED`，
  携带 causation_id（= SOCIAL_INTERACTION 事件）/correlation_id，因果链完整。
- **琐碎聊天过滤（§18）**：`classify_significance()` 把"嗯/哈哈/在吗/……"判为 trivial
  并乘 0 倍——事实仍计数（interaction_count），但四个维度一动不动。
- **邀请 ≠ 接受（§19/§33）**：`game_invitation` 是独立事实（只加熟悉度）；
  认识的人邀请只被 OBSERVE，本人邀请 + 可被决定打断时才进入决策；
  `invitation_accepted` 在决定接受时记录，`shared_activity` **只有该活动真正跑完**
  才记录；拒绝同样是独立事实（social_comfort 小幅下降）。
- **关系只影响"想要"，绝不执行（§21/§22）**：closeness/trust 以
  `min(0.15, closeness*0.15 + trust*0.05)` 加成候选优先级，并作为一行上下文进入
  决策/认知提示词；动作仍由既有 Decision 门控 → Action 轨道执行。
- **边界（§23-§27）**：认知上下文只带当前对话者的关系；连续性快照只带最重要的
  3 条；只有 major（跨里程碑）变化经 `CONDITIONAL_EXPERIENCE_EVENTS` 守卫进入
  体验/记忆（`relationship_changed`），普通漂移 0.01 不成为记忆；读记忆、读快照
  绝不改动关系。**没有** LLM 关系评分、**没有** Memory→Relationship、
  **没有** Relationship→世界变更。
- **持久化**：迁移 24 新增 `sandbox_persons` / `sandbox_relationships`
  （character_id + person_id 复合主键，按角色隔离）；Bible 关系作为
  `source="character_bible"` 的初始状态种入，重启后读回。
- **读路径纪律**：`RelationshipStore.get()` 返回副本、`relationship_for()` 只读不创建；
  关系记账标记 `affects_world=False`——它照样审计、照样持久化，但**不会让进行中的
  决策提案失效**（回归测试覆盖）。
- 新增 `tests/test_sandbox_relations.py`（17 个测试：确定性与渐变性、trivial 不移动、
  邀请/接受/拒绝三态、活动跑完才算共同经历、负向互动可恢复、角色隔离、
  Bible 初始状态、关系加成只改候选优先级、只影响当前对话者、只有 major 进记忆、
  读路径不改状态），总测试 1195。

## [Unreleased] — v2.1 Phase 7.1 Remediation · Resume Binding Integrity

- **rebind 严格匹配（§3-§5）**：`GoalManager.rebind_instance()` 现在只接受
  **active Goal + active Step + 同一 action_id + 实际被中断的 instance id**
  四者同时成立；`old_instance_id=""`（或 new 为空）直接拒绝且状态零变化；
  多命中（理论不应出现）也拒绝并记 WARNING——不再"第一个命中就返回 True"，
  failed / blocked / pending 的旧步骤绝不会被静默收养。
- **立即持久化（§9-§13）**：`rebind_instance` 改为 async，改绑后立刻
  `_persist(goal)`（复用既有 `SandboxStore.save_goal`，无第二套持久化）；
  `save_goal` 现在返回 bool；写失败时记录 ERROR、把目标标记为 dirty 交给既有
  flush 重试——**不回滚世界、不启动第二个动作、不创建第二个目标**（§12）。
  Resume 返回时数据库已带新 instance id，崩溃/重建后目标恢复到新实例。
- 新增 `tests/test_sandbox_goals_binding.py`（7 个测试：blocked+failed 步骤
  不被收养而 active+active 步骤被正确改绑、active Goal + failed Step 拒绝、
  blocked Goal + active Step 拒绝、缺 old instance 拒绝且零变化、
  歧义匹配拒绝、resume 后**未经 tick 直接读库**即为新实例且旧实例完成事件
  不再推进、崩溃重建后目标读到新实例），总测试 1178。

## [Unreleased] — v2.1 Phase 7 Remediation · Goal 状态恢复 + Action Instance 一致性

- **blocked → active（§1）**：冷却期结束后重试成功的 blocked 目标会真正回到
  `active`（同一 goal_id、同一 correlation，不克隆），并发布
  GOAL_ACTIVATED(reason="resumed_after_cooldown")；GOAL_BLOCKED 历史保留。
  消除了「状态是 blocked 却在执行 Action」的语义不一致（§17 审计确认
  blocked 只在 `_block()` 设置、只在成功重试/取消时离开）。
- **GoalStep 绑定 Action Instance（§4-§6）**：ACTION_STARTED / COMPLETED /
  INTERRUPTED 的 payload 统一携带 `action_id` + `action_instance_id`；
  `GoalStep.action_instance_id` 成为一等字段（不再依赖
  `result["action_instance"]`）；`on_action_completed` 以 instance 精确匹配，
  action_id 仅在没有 instance 且**唯一**候选步骤时作兼容回退——
  两个目标共享同一 action 定义时，一次完成绝不会同时推进两者（核心回归测试）。
- **Resume 重绑（§7）**：Phase 3 的 interrupt→resume 产生新 ActionInstance 后，
  `GoalManager.rebind_instance()` 把正在等待该动作的步骤改绑到新实例
  （同一 GoalStep，不新建步骤），随后该实例完成才推进目标。
- **启动扫描按 Seed 发现耗尽资源（§10-§13）**：`GoalDetector.sweep()` 改为遍历
  `ActionDefinition.effects` 推导出的「世界里真正可补货的 (inventory, item) 集合」
  （`_restockable_targets()`），不再依赖 `inventory.items` 里是否残留 0 计数键；
  去重仍按 character+kind+target。附带修正 `take_item` 的耗尽语义：
  INVENTORY_DEPLETED 现在在**该物品**归零时发布（「最后一瓶」），payload 增加
  `container_empty` 说明容器是否也空了——此前是容器整体为空才发。
- 新增 `tests/test_sandbox_goals_remediation.py`（7 个测试：blocked 复活、
  instance 精确匹配、歧义回退不动、STARTED/COMPLETED 同实例、resume 重绑、
  无残留 0 键时启动恢复、非可补货物品永不成为目标），总测试 1171。

## [Unreleased] — v2.1 Phase 7 · Autonomous Life Loop & Goal Layer（她为什么会一直做下去）

- **目标层（§4-§9/§27）**：新增 `app/sandbox/goals.py` ——
  `Goal`（goal_id/character_id/kind/status/priority/reason/source/
  source_event_id/causation/correlation/target_*/progress/metadata/
  retry_count/next_eligible_at/current_step）+ `GoalStep`（只物化"当前下一步"，
  不预生成计划树）+ `GoalDetector`（事件驱动：INVENTORY_DEPLETED→补给、
  PET_HUNGRY→照顾宠物、PROJECT_PROGRESS_CHANGED→继续项目；启动再扫一次）
  + `GoalManager`（去重键 = character+kind+target，存在即更新不重建；
  单一"执行目标"（优先级：照顾宠物 0.8 > 补给 0.6 > 项目 0.4）；
  阻塞→冷却→再评估，重试上限后取消——绝不每 tick 空转）。
- **可补给性完全由 Seed 决定（§10/§11/§41）**：`restock_actions()` 从
  `ActionDefinition.effects` 的 `inventory:<key>:<item>` 反查——世界里没有
  任何动作能补回该物品，它就不是目标；无物品名/角色名硬编码。
  顺手修正模板语义：`buy_snacks` 补货对象从饮料改为零食（原为 Phase 1 笔误）。
- **Goal → Decision → Action（§15/§16/§28/§32）**：目标步先经 Phase 6
  `DecisionCoordinator`（新 trigger：goal_step / goal_conflict / goal_blocked），
  候选来自 Seed 且带规则/物件/库存前置；单候选→确定性（无 LLM），
  多候选→才用模型；执行前经 validator 重读世界（world_revision 过期即
  拒绝——有测试）。
- **持久化与恢复（§18/§33/§34）**：迁移 23 新增 `sandbox_goals` /
  `sandbox_goal_steps`（含 character+status、character+kind+target、
  next_eligible_at 索引）；重启后恢复目标并逐条复核世界——失效的步骤被
  丢弃重新规划，不盲信旧计划。
- **事件与因果（§21/§22）**：GOAL_CREATED / ACTIVATED / PROGRESS / BLOCKED /
  COMPLETED / CANCELLED / STEP_STARTED / STEP_COMPLETED 进入既有
  `SandboxEventType`；目标事件自成一条 correlation（`goal_<id>`），
  用 causation 指回触发事实（喝掉最后一瓶 → 目标链）。
- **与 Continuity / Memory 的边界（§19/§20/§42）**：`ContinuitySnapshot`
  新增 `active_goals`（最多 3 条），聊天上下文以「未完成目标」中性行呈现；
  只有 GOAL_COMPLETED 进入 Experience→Memory 候选（GOAL_CREATED 不会）；
  Memory 不反向生成目标。
- **打断与恢复（§30/§31）**：目标步执行中的动作被外部紧急事件打断后，
  经 Phase 3 的 interrupt/resume 机制继续——目标保持未完成并在动作恢复后
  接着推进（有测试）。
- 新增 `tests/test_sandbox_goals.py`（23 个测试覆盖 §36 的 20 项 +
  24 小时自主模拟 + 同种子可复现），总测试 1164。

## [Unreleased] — v2.1 Phase 6 · Cognitive Decision & Intent Layer（LLM 可提议，沙盒才决定）

- **决策层（§5-§20）**：新增 `app/sandbox/intent.py` ——
  `DecisionTrigger` / `DecisionCandidate`（候选只从世界生成：continue /
  action:<owned> / postpone，未拥有的动作不可能出现）/ `DecisionRequest`
  （含 request_id、character_id、trigger、截断的 CognitiveContext、候选、
  约束、deadline、**world_revision**、causation/correlation）/
  `IntentProposal`（严格 JSON：candidate_id + confidence）/
  `DecisionGate`（唯一候选→确定性；≥2 候选才需要模型）/ `DecisionValidator`
  （执行前重读实时状态：候选存在、动作存在、规则允许、物件/库存/前置满足、
  deadline、**world_changed 失效检测**）/ `DecisionCoordinator`（编排
  gate → 事件 → 模型 → 校验 → 事件）。
- **决策事件入既有 spine（§16/§17）**：新增
  DECISION_REQUESTED / PROPOSED / ACCEPTED / REJECTED / FALLBACK，
  PROPOSED/ACCEPTED/FALLBACK 一律挂在 REQUESTED 之下（RECEIVED →
  REQUESTED → …），全链带 causation/correlation。
- **邀请走进决策管线（§14）**：外部影响层判定"值得打断"后不再直接起动作，
  而是由 `_run_invitation_decision()` 建候选（核心朋友邀请优先级 0.85 >
  继续 0.5 > 推迟 0.4，规则受阻会成为不可执行候选）→ Coordinator →
  校验通过才 ACTION_REQUESTED →（必要时）ACTION_INTERRUPTED →
  ACTION_STARTED → WORLD_EXTERNAL_INFLUENCE。
- **确定性回退（§11/§28）**：模型不可用/超时/非法 JSON/未知候选/低置信度
  （< decision_min_confidence）→ DECISION_FALLBACK → 确定性挑选
  （临界需求 → 候选优先级 → 偏好继续执行），沙盒永不卡死；全部候选非法时
  DECISION_REJECTED 且零副作用。
- **不越权（§3/§12/§18）**：决策本身零世界变更（有测试逐项比对）；LLM 只在
  DecisionRequest 产生时被调用——每 tick、每次宠物/库存事件、每条聊天消息
  都不调用（有测试与源码事实支撑）；闲聊路径（character/runtime.py）零决策
  引用；Phase 2 的模糊裁决 tie-break 也被包装进同一事件/校验管线（不再有
  第二条 LLM 决策通道）。
- **候选/上下文预算（§23/§24）**：决策 prompt 只带世界行/状态行/需求行/
  目标 + 至多 3 条相关记忆（复用 Phase 5 检索与预算）+ 编号候选清单；
  CharacterDefinition 的 boundaries 仅取前 3 条作为约束行。
- **配置**：`sandbox.decision_model`（由 `models.decision` 统一注入并参与
  启动校验）、`sandbox.decision_timeout`（20s）、
  `sandbox.decision_min_confidence`（0.35）。
- 新增 `tests/test_sandbox_intent.py`（17 个测试：确定性单候选零模型调用、
  真实宠物链零模型、纯 tick 零模型、模型选择端到端、事件因果链、
  未知候选/低置信/超时回退、world_revision 失效与 deadline 过期、
  决策零变更、第二角色世界级否决 + 伪造提案 validator 兜底、
  规则禁止 LLM 选择、决策上下文带相关记忆（仅供参考）、
  决策事件不成记忆、gate/候选来源面），总测试 1141。

## [Unreleased] — v2.1 Phase 5 Remediation · Context Prompt Character-Neutral

- **Prompt 固定标签去性别化**：Phase 5 注入的三段固定文案改名——
  【近期延续状态】（角色近期生活状态…）/【相关生活记忆】（过去的重要经历…）/
  【最近发生的经历】（近期发生的重要经历…）；语义不变，不再假定角色性别，
  角色口吻仍由 Persona/LLM 层决定。
- **模块注释中性化**：cognitive.py 的 docstring/注释由 "what she may see /
  her world / her life" 改为 "the character / the character's world/life"。
- 新增 `tests/test_sandbox_neutral_context.py`（6 个测试：女性角色段落无
  性别词、男性角色 Lin 端到端 prompt 标签中性且动态数据（木工）照常进入、
  未标注性别角色正常工作、动态 seed 数据（项目/空间名）仍进 prompt、
  AST 级源码 guard（只查非 docstring 字符串常量，放过「他人」这类普通词）、
  旧标签确已退役），总测试 1124。

## [Unreleased] — v2.1 Phase 5 · Cognitive Context Bridge（Sandbox → 聊天上下文）

- **CognitiveContext（§5/§25）**：`app/sandbox/cognitive.py` ——
  `CognitiveContext` + `CognitiveContextBuilder`，入口
  `SandboxRuntime.cognitive_context(query, relationship_target)`；
  组装当前世界切片、v2.1 ContinuitySnapshot、相关 Sandbox 记忆、
  最近经历与检索记账，并提供 `as_prompt_payload()` 供 ContextBuilder 渲染。
  **严格只读**：不调用任何 mutation/Action API、不调 LLM（有源码 guard 测试）。
- **确定性检索（§7-§9）**：`SandboxMemoryStore.retrieve_relevant(query,
  entities, limit, min_score, max_chars, require_evidence)` ——
  评分 = 0.45×关键词(复用 bigrams) + 0.15×实体命中 + 0.25×重要度 +
  0.15×新近度（14 天半衰期）；**证据门槛**（无关键词/实体命中不入选，
  重要度+新近度不能单独捞人）；只读 active（superseded 永不注入）；
  候选窗口 200 行、条数与字符双预算；排序 (-score, -id) 完全确定。
  预算可配：`sandbox.memory_context_limit / _max_chars / _min_score /
  _require_evidence / experience_context_limit`。
- **分层 Prompt（§11/§17/§27）**：`CharacterContextBuilder.build()` 新增
  `sandbox_context` 参数，在「当前世界事实」之后渲染
  【近期延续状态】【她自己经历过的相关往事】【最近发生的经历】，
  每段自带「仅作参考；与当前世界/当前消息冲突时以它们为准」的声明——
  旧记忆永不覆盖当前世界事实。
- **来源分离 + Trace（§6/§13/§28）**：conversation memory（既有
  MemoryManager 链，未动）与 sandbox memory 分区并列；context_trace 新增
  `conversation_memory` / `continuity_snapshot` / `sandbox_memory` /
  `recent_experience` 图层（含 count/query/memory_ids），v1.2 continuity 与
  v2.1 ContinuitySnapshot 各自独立、互不覆盖。
- **接线（§16/§23/§26）**：`CharacterRuntime.respond()` 内部取桥接数据
  （sandbox 关闭/不可用时静默降级为 None，聊天不受影响）；检索 query 与
  facts 一致（主动发言传 topic/动机，因此 Initiative 经 respond() 自然获得
  Sandbox Memory 与 Continuity，未新建第二套检索）。
- 新增 `tests/test_sandbox_cognitive_bridge.py`（16 个测试：相关记忆入 prompt
  与 trace、无关记忆排除、当前世界优先声明、跨角色隔离、Continuity/经历注入、
  双 continuity 来源分离、respond 不改 Sandbox（含只读源码 guard）、
  确定性排序、预算上限与高分优先、superseded 剔除、旧对话记忆链保持、
  sandbox 缺席聊天可用、Initiative 复用桥接、无 LLM），总测试 1118。

## [Unreleased] — v2.1 Phase 4 Remediation · Continuity 隔离 + Character-Neutral Memory 文案

- **Continuity 按角色隔离（§remediation-1）**：快照持久化键从固定
  `continuity_snapshot` 改为 `continuity_snapshot:<character_id>`，
  persist/load 使用同一 scoped key；同一数据库上的两个角色互不覆盖。
  兼容：仅当 scoped key 不存在时读旧固定键，且旧快照必须
  `character_id` 与本角色一致才会被接受（无主的旧数据保持不可认领，
  绝不错误归属）——旧键不删除。
- **记忆文案改为 Character-Neutral（§8-§10）**：Phase 4 新代码里的
  「她…」句式全部替换为中性事实句——
  完成了{name} / {name}进行到一半被打断 / 继续进行{name} /
  与{target}完成了一次互动 / {pet}饿了，已添{food} / 首次获知：… /
  {actor}的邀请改变了当前安排 / 在{space}发生了社交活动；
  MemoryCandidate.content 同步中性化（并删除未经证据的「她答应了」推断，
  只陈述"外部事件改变了安排"）。角色口吻留给后续 Persona/NLG 层。
- 新增 `tests/test_sandbox_memory_remediation.py`（6 个测试：
  同库双角色快照互不覆盖且互不污染、旧键归属校验、第二角色文案零角色词、
  第三 synthetic 角色 Lin（木工/阅读）产出「完成了木工」而非「她做完了木工」、
  三份 foundation 源码的角色词/性别词扫描、模型无性别化默认值），
  总测试 1102。

## [Unreleased] — v2.1 Phase 4 · Sandbox Experience → Memory → Continuity Foundation

- **Experience 层（§5-§7）**：`app/sandbox/experience.py` ——
  `ExperienceRecord`（character_id/kind/summary/importance/location/actors/
  source_event_ids/causation/correlation/action_id/metadata）+ 订阅事件总线的
  `ExperienceBuilder`：只接受业务事件（动作完成/中断/恢复、交互、喂宠物、
  项目里程碑、首次知识、外部影响、社交露面），NEED_CHANGED/REQUESTED/
  STARTED/移动请求等噪音被过滤；同一条行为链（correlation）归并为**一条**
  Experience（喂猫链不会产生 5 条近似记录）；importance 为确定性评分。
- **Memory 候选（§8/§16/§19-§22）**：`MemoryType`（episodic/semantic/
  social/world_fact）+ `MemoryCandidate`（含 importance/confidence/scope/
  dedupe_key/provenance 字段）+ 确定性 `MemoryCandidateBuilder`；候选先落
  `sandbox_memory_candidates`（status: candidate/promoted/rejected/
  duplicate），importance ≥ 0.5 才晋升为长期记忆。
- **Memory Store（§10/§11/§15/§17）**：`SandboxMemoryStore` 复用**既有**
  `memories` 表与 `MemoryRepository`（无第二套存储、无 embedding 路径）：
  `character_id` 为隔离边界（同库双角色互不可见）、`provenance` JSON 记录
  memory→experience→event 全链、`dedupe_key` 幂等去重（同链重复处理不增行；
  不同日期的真实重复事件因 episodic 键含 correlation 而不被误并）、
  world_fact 语义身份变化时旧行 `superseded` + supersedes 关系。
- **迁移 22**：`memories` 增 character_id/provenance/dedupe_key（旧行默认空，
  向后兼容）+ 两个新表 `sandbox_experiences` / `sandbox_memory_candidates`
  + 索引；`SOURCES` 增 "sandbox"。
- **Continuity 读模型（§23-§25）**：`ContinuitySnapshot` +
  `ContinuitySnapshotBuilder`（world 摘要/当前动作与位置/知识/重要经历/
  活跃记忆/未完成项目/待处理外部事件/关系上下文），生成后写入
  `sandbox_state`；**只读**——不进入 Decision/Persona/Speech/Relationship，
  也不修改世界状态（有测试钉死）。
- 新增 `tests/test_sandbox_memory_foundation.py`（19 个测试：动作→记忆溯源、
  tick/需求漂移不污染、最后一瓶可乐、喂猫单条、邀请经历、项目里程碑、
  去重、跨角色隔离、重启持久化、Continuity 组合与只读、Knowledge≠Memory、
  provenance 全链、低分候选拒绝），总测试 1096。

## [Unreleased] — v2.1 Phase 3.5 · State Authority Closure（Need / Project / Knowledge 收口）

- **Need（§3）**：新增 canonical `adjust_need(key, delta=, relieve=, source,
  reason, correlation, causation_id)`（两种语义：绝对增量 / 按剩余压力比例
  缓解），每次真实变化 → StateMutation(needs:<key>, level, before/after) +
  NEED_CHANGED；`_complete_action` 的 need_relief/need_cost、委托完成的
  work_need、agent 结果、用户互动全部改走它；tick/离线结算的连续漂移
  按时间演化保留，但**带位跨越**（calm/soft/strong/critical）会发
  NEED_CHANGED（payload 带 band_before/after）——世界事实可追溯。
- **Project（§4）**：新增 canonical `update_project_progress(project_id,
  delta, source, reason, …)` → StateMutation + PROJECT_PROGRESS_CHANGED
  （payload 含 before/after/delta/completed）；击穿 1.0 时同事件标记
  completed=true 并把项目置为 completed，不另造第二套项目事件系统。
- **Knowledge（§5）**：新增 canonical `set_knowledge(key, known=, source,
  reason, data=, …)` → StateMutation(knowledge:<key>, entry, before/after) +
  KNOWLEDGE_CHANGED（payload 含 key/before/after/source/reason）；
  learned_at/source/data 字段保持；与 Memory 继续分离（未接任何记忆层）。
- **因果连接（§11）**：`_complete_action` 先发 ACTION_EFFECT_APPLIED，随后
  所有效果（消耗/获取/物件/需求/项目/知识/宠物/回家移动）以它为
  causation_id、同一 correlation 挂进同一行为链；ACTION_COMPLETED 亦在同链。
- 新增 `tests/test_sandbox_state_authority.py`（11 个测试，含作用域感知的
  源码 guard：canonical helper 之外不得出现 needs.add/relieve、
  project["progress"]=、knowledge[...]=），总测试 1077。

## [Unreleased] — v2.1 Phase 3 Remediation · 架构旁路修复

- **动作启动不再绕过 Mutation（§9）**：`_start_action` 删除
  `move_entity 失败 → 直接写 location` 的回退；移动一律经 canonical gate
  （`SpaceSystem` 改为对称闭包 + 多跳 BFS，跑腿路线合法可达），失败则
  ENTITY_MOVE_REJECTED + ACTION_FAILED、动作 `blocked`、current_action 不变、
  拒绝记录以 `ok=False` 进 MutationLog（可审计）。
- **SocialSpace 变更入 spine**：新增 `update_social_space()`
  （presence/temperature → StateMutation → SOCIAL_SPACE_CHANGED），
  `_touch_social_space` 与 `note_user_interaction` 全部改走它；
  源码级扫描测试保证不再出现 `social.* =` 直接赋值。
- **删除 ACTIVITY_IDS 硬编码表**：activity 改为 `ActionDefinition.activity`
  结构化字段，由 Seed 的动作模板声明（gaming/reading/eating/out/…）；
  `_sync_state`、`_activity_index`、`_action_for_activity` 只读定义元数据，
  runtime 不再出现任何 action-id→语义映射；新增「第三活动 crafting」测试
  证明不同 Bible 无需改核心代码。
- **队列淘汰尊重紧急度（§13）**：满队列时仅允许*更紧急*的新事件淘汰
  最弱（同级最老）事件，等强或更弱者被拒（返回 False，不标记已消费）；
  被淘汰的事件可再次提交（淘汰 ≠ 消费）；critical 不会被普通事件挤掉；
  淘汰确定性有测试钉死（FIFO 在同级内保持）。
- 新增 `tests/test_sandbox_remediation.py`（13 个测试），总测试 1066。

## [Unreleased] — v2.1 Phase 3 · External Influence + Sandbox Wakeup + Action Interrupt

- **ExternalWorldEvent（§4）**：协议无关的外部事实类型
  （event_id/timestamp/source/actor_id/event_type/content/metadata/urgency/
  semantic_kind/target_activity/actor_relationship/correlation_id）——
  `app/sandbox/external.py`；QQ/OneBot/NapCat 对象不进入沙箱。
- **ExternalEventAdapter（§5/§18）**：`app/sandbox/external_adapters.py`
  以*原语*为输入产出外部事件（可在无 QQ 对象的环境完整测试），并把
  "来一起联机"这类措辞经系统级 cue 词表翻译为
  `semantic_kind=game_invitation / target_activity=gaming`；插件只负责
  提交原语（plugins/chat 不再构造协议对象、不再碰沙箱状态）。
- **External Influence Layer（§6/§7）**：`ExternalInfluenceEvaluator` 只回答
  NO_EFFECT / OBSERVE / WAKE / INTERRUPT / REJECT（含 `activity_unavailable`
  等 reason_code），输入是 urgency/语义/关系/可用活动/可打断度——不改状态、
  不选动作、不调 LLM；是否拥有某活动由 World Seed 决定。
- **Sandbox Wakeup + 队列（§8/§9/§13/§19）**：`submit_external()` 入队
  （按 event_id 幂等去重、FIFO+紧急度优先），`wakeup()` 立即评估而不是等
  10 分钟 tick；`sandbox_state` 持久化 pending/seen，重启既不重放已消费事件
  也不丢未处理事件（§20）。
- **Action Interrupt / Resume（§14/§15）**：`InterruptedActionContext`
  （definition/progress/remaining_minutes/interrupt_reason/resumable——
  只存恢复所需，不存 Runtime 快照）；被打断的动作在新动作完成后按**剩余
  时间**恢复，并发出 ACTION_REQUESTED→ACTION_RESUMED 的新生命周期事件；
  存在临界需求时不假装没发生，直接放弃恢复。
- **因果链（§10/§11）**：EXTERNAL_EVENT_RECEIVED →（INTERRUPT 时）
  ACTION_INTERRUPTED → ACTION_STARTED → WORLD_EXTERNAL_INFLUENCE，
  causation 逐级串联；非法/不可表示的外部事件发 EXTERNAL_EVENT_REJECTED
  且世界状态零变更。所有效果仍经 Phase 2 的 Mutation/Event spine
  （含快递 present 改走 apply_object_effect、需求变化走 _adjust_need）。
- 新增 `tests/test_sandbox_external.py`（18 个测试：无影响消息 / 邀请中断 /
  恢复与临界需求抢占 / 幂等（含 legacy 桥）/ 队列优先级 / 双沙盒隔离 /
  协议隔离（AST 断言无 onebot 依赖）/ 第二角色阿澈（无该活动→REJECT）/
  重启持久化 / evaluator 单元面），总测试 1053。

## [Unreleased] — v2.1 Phase 2 · Entity Interaction + Event Bus + State Mutation

- **Sandbox 事件主干（§5/§13）**：新增 `app/sandbox/events.py`——
  `SandboxEvent`（event_id/timestamp/type/source/target/payload）+
  **causation_id**（谁导致了我）+ **correlation_id**（同一条行为链）；
  `EventBus` 为 Runtime 私有实例（§14，无全局单例），带 dispatch 深度护栏与
  correlation 链上限（§15：允许有界因果链，无限循环降级为丢弃+WARN）。
- **统一状态变更路径（§6/§52-§53）**：`take_item / acquire_item / move_entity /
  feed_pet / apply_object_effect` 成为唯一状态入口——每次变更记录
  source/reason/before/after 并发出对应事实事件
  （ITEM_CONSUMED / INVENTORY_DEPLETED / ENTITY_MOVED / PET_FED /
  OBJECT_STATE_CHANGED…）；`interactions.py` 只经此路径改状态。
- **Entity Interaction（§11-§12）**：`InteractionResolver` 按**实体 kind**
  （pet/object/space）给出 `InteractionCandidate`（feed/observe/take_item/
  inspect/move + requirements）并执行 `Request → 验证 → Mutation → Event`；
  不做决定、不调 LLM；宠物饥饿反应（pet_care ↑）改为总线订阅者而非内联 if。
- **动作生命周期入链（§10）**：ACTION_REQUESTED → STARTED → EFFECT_APPLIED →
  COMPLETED / FAILED / INTERRUPTED 全部发出事实事件并以
  `act_<instance.id>` 关联；`feed_cat` 专属分支删除，改为通用 `pet:feed`
  效果（§18：动作效果按 id 语义路由，不按角色特例）。
- **四条完整因果链落地**：宠物饿了（PET_HUNGRY→APPROACHED→feed→
  ITEM_CONSUMED→PET_FED，全链可重放）；库存耗尽（ITEM_CONSUMED→
  INVENTORY_DEPLETED，causation 直指消费事件）；实体移动（REQUESTED→
  验证→MUTATION→MOVED/REJECTED，非法空间绝不产生错误状态）；动作生命周期
  （含验证失败路径）。
- 事件总线为 transient（§20）：持久化仍走既有 SandboxEventRecord/store；
  第二角色（阿澈，无宠物、咖啡锚点）下整条主干照常工作，名称零依赖。
- 新增 `tests/test_sandbox_causality.py`（15 个因果链测试：链路/嵌套/防环/
  深度护栏/双沙盒隔离/第二角色回归/候选需求面），总测试 1035。

## [Unreleased] — v2.1 Phase 1 · Character Bible → Sandbox Foundation

- **Character Bible 成为唯一 Canonical Source（§6）**：新增
  `CharacterDefinition`（bible → 角色契约，§7）与 `CharacterWorldSeed`
  （bible+definition → 完整初始世界，§26），`SandboxRuntime` 的角色实体 /
  宠物 / 空间 / 物件 / 库存 / 动作 / 项目 / 社交空间 / 模式全部从 Seed
  初始化——`CharacterEntity()`/`PetState()` 零参默认名（罐头/小喵）已删除，
  name/location 改为必填（§9）。
- **Seed 可导出、可复现（§28/§29）**：`export_seed()` 输出完整初始世界 JSON；
  同一 bible+simulation_seed 重复构建逐字节一致（有测试断言）。
- **动作目录模板化（§20/§21）**：系统模板（`action_templates.py`）+ Seed 解析
  （锚点物品 `{drink}/{snack}/…`、空间映射、动作归属按 bible 关键词推导）——
  没有可乐的档案不会拥有买可乐动作链。
- **模式 Definition 化（§18/§19）**：五模式不再是 Python 枚举；触发
  （home/outdoor/time/action/social）、时间窗、口癖短语、语气提示全部来自
  bible Modes 段；叠加机制保留。
- **迁移 21**：`sticker_assets.scope`（character/global）+ 存量回填；重置时
  角色表情清除、全局资产与平台数据保留（§44-§46）。
- **重置清单补缺口（§38）**：`character_states / topics / behavior_events /
  initiative_state / social_observations / reply_outcomes / expression_* /
  memory_relations` 与 `social_engagement` 设置键进入角色重置范围；
  `PRESERVED_TABLES` 移除三个不存在的幽灵表名。
- **接口预留（§49-§53）**：`mutations.py`（StateMutation 审计 + MutationLog +
  EntityInteraction/DecisionRequest/SandboxEventBus），动作完成与位置变更已
  开始记录 source/reason/before/after。
- **Parser 升级（§30-§37）**：空间 kind/parent/connections、物件库存键、
  `（充足）=10`、社交空间正则提取（替代硬编码关键词表）、规则带
  source_section 溯源、对话示例舞台指示 → Behavior Candidate（不自动成规则）、
  Coverage 升级为 Parsed→Compiled→Seeded→RuntimeConnected→Tested 链。
- WebUI 去「小喵/罐头」写死标签；沙盒 AI tie-break 提示词改为由
  CharacterDefinition 注入；persona 同步去硬编码（habits/traits/dislikes/
  关系句全部来自 bible 数据）。

## [Unreleased] — v3 线（任务 0–25）

### 记忆
- 关键词候选改走 **SQLite FTS5** 索引（迁移 15，`search_text` 双字切分 + 触发器同步）。
- 向量改为 **float64 blob + 预存范数**（迁移 16，`math.sumprod` 单次点积）：候选池内检索
  由 ~159 ms 降到 ~18 ms；JSON 列保留为回退，删除条件已写入代码注释与文档。
- `compression_use_llm` 的 LLM 压缩真正实现；抽取向上的配额与保留策略未变。
- 数据库写入失败不再丢失：**outbox**（JSONL + fsync + 原子重写）落盘并在恢复后重放（任务 14）。
- **图片记忆**：她看懂的图片会成为可回忆的记忆（`source=vision`），WebUI 记忆页可按来源筛选（任务 21）。
- **抽取再也不能静默失败**（任务 25）：每次抽取输出一行
  `[Memory.Extract] saved=x/y reason=…`；原因分类 `disabled / no_models / empty_content /
  parse_failed / timeout / ai_error / invalid_item`；解析失败时 WARN 附脱敏后的前 200 字；
  空 content 但带 reasoning 的回复单独标注并**重试一次**指定模型（未配置时明确提示
  `memory.extraction.model`）；失败重试保留根因（`reason=empty_content retry=failed`）；
  `saved` 只统计**真正入库**的行；连续 5 次零入库 → 一次 WARN + `/memory/health` 红条 +
  `memories_extracted` / `memory_extract_failed{reason}` 计数。

### AI 与运行时
- 按 `base_url` 的并发闸门、限流/服务端错误退避、逐次调用的用量行（迁移 17，`ai_usage`）。
- 启动校验：模型绑定了不存在的 provider → **ERROR 并列出已知 provider**；
  AI 已启用但没有任何可用模型 → **ERROR + 仪表盘横幅**（任务 25）。
- 事件循环卡顿看门狗（任务 16）；实时播报与状态经鉴权 WebSocket 推送（任务 17）。
- 推理模型只输出思考、导致空回复时：同模型重试一次再故障转移，绝不把空回复交给角色层。
- 相对路径（贴纸库/媒体/日志/插件目录）一律按 `PROJECT_ROOT` 解析：
  从别的工作目录启动也不会换掉数据位置（任务 25）。

### AI 与运行时（2026-10-05 复盘：推理预算 · 探针误报 · 决策钉模型）
- **症状**：后台持续出现 `Empty content from model=cn:deepseek-v4.1-flash finish=length`
  （当天 22 次，其中 17 次 completion=500、4 次 completion=8）；每次都白白级联
  primary→secondary→fallback，一次多花 10–15s。
- **根因**：推理模型的 `reasoning_content` 与正文**共用 completion 预算**——预算给少
  （沙盒裁决 500 / 探针 8）必然「思考吃满、正文为空」；沙盒裁决又**没接**
  `sandbox.decision_model` 旋钮，每次都从主力推理模型开始撞。**不关推理**，只把预算与
  模型钉按调用点真实成本对齐。
- **决策类预算上调**：沙盒裁决 500→**1200**；对话回复 `conversation_max_response_chars + 800`；
  意图判定 300→**800**；记忆整理 400→**900**（上限不是目标值，模型该短还是短）。
- **沙盒裁决接通模型钉**（`sandbox.decision_model` ← `models.decision`，实盘 `fallback`
  = `cn:minimax-m3`）：高频微决策不再从推理主力模型起步；钉错/被禁用时仍按既有语义
  回落到确定性选择、不影响世界。
- **统一探针 `app/ai/probe.py`**：模型钉死 / 温度 0 / 预算 256；「上游已回应、只是预算被
  推理占满（finish=length）」判为**连通正常（degraded）**并如实说明「真实调用使用各自
  完整预算，不受影响」——模型测试、凭据测试、provider 快检三处都不再误报失败；
  `finish=stop` 的空正文仍是真异常。
- **WebUI**：测试结果面板在成功但带说明时标签显示「说明」，不再写「错误信息」。
- 新增 `tests/test_ai_probe.py`（12）+ `tests/test_web_api_ai.py::TestProbeDegraded`（4），
  总测试 1937。

### 社交与行为
- **回复效果回流**（任务 20，迁移 18）：每回合记录观察 → 结算
  `engaged / ambient / silence / unknown`（保守极性、只记 0/−1）→ 时间衰减加权的参与度 EMA
  软性反馈给群参与决策；群参与概率改为**确定性额度**（1.0 = 每条都参与）。
- `[Initiative] Gate rejected` 仅在拒绝原因发生变化时记 INFO，其余降为 DEBUG（任务 25）。
- 世界事实注入 + 越界声明审计：沙盒实体标签命中即注入真实数字（含库存）并禁止编造，
  出站再做一次矛盾声明剥离；模型思考内容只上控制台、不落盘。

### 媒体
- **识别 → 判断 → 收藏**：QQ 原生表情/动画表情与普通图片都在"要不要回复"决策之前
  后台识别，识别摘要并入参与判断；配字被当作对方真正想说的话。
- 普通图片永不进入表情库（`looks_like_sticker` 边界）；表情收藏**真正下载文件**
  （sha256 去重 + 平均哈希 + 粘贴检测），收藏判定与视觉摘要共享一次下载。

### 运维与安全
- WebUI 路由清单守卫（97 条）+ 登录限流 + CSRF；`server.py` 拆分为 13 个域路由模块（任务 18）。
- 日志脱敏扩展；测试与运行目录解耦并有"不得写真实数据"的守卫。
- **`scripts/backup_db.py`**：`VACUUM INTO` 在线快照 + `integrity_check` + 逐表行数核对
  （绝不对运行中的 WAL 库做文件拷贝）。
- **`scripts/migrate_db.py`**：对单个库文件预演/应用迁移链，报告版本、新增表、只许增不许减的行数核对。
- **角色数据导出/检查**（任务 24 里程碑 1，`app/sandbox/transfer.py`）：
  `python -m app.sandbox.transfer export|inspect`；单文件 JSON、含 schema 版本 / Bible hash /
  逐表计数 / 内容哈希，流式写入（单块 < 64 KiB）；`inspect` 离线校验格式、哈希与计数；
  不含任何密钥与平台数据（有测试断言）。与迁移 19（删 JSON 列）配套的保险。
- 迁移 14–18 已在真库执行（schema 13 → 18），预演先于真库、备份先于预演。

### 打包
- `LICENSE`（Apache-2.0）、`NOTICE`（角色内容不随代码授权）、`EULA.md`（隐私说明 + AI 声明）。

### 重启后的观察期检查项（本批改动的落地核对）
- 模型页 `ai_usage` 出现 `purpose=social_observer`，且延迟约 3.4s（`models.decision` 指向非推理模型后）。
- `[Watchdog]` 卡顿告警带上了「正在执行：… / 任务栈：…」线索，可直接定位阻塞来源。
- `/social/group` 显示「因 poor_timing 连续 N 次未开口」（defer 只推迟、不否决）。
- 模型页 `ai_empty_finish_length` 停止增长（`ai.max_tokens` 建议 2048 起）。
- 后台不再刷 `Empty content from model=… finish=length`（决策预算已按调用点上调，沙盒裁决
  直接走 `cn:minimax-m3`，不再级联降级）。
- `/memory/health` 无红条，`[Memory.Extract]` 无 `parse_failed`。

## [2.0.0] — 2026-10-01 · Character Life Sandbox

- 人物档案 → **Bible 编译器** → Seed：实体/空间/物件/需求/动作/规则/模式；
  有界 tick、中断与恢复、快照与回放、沙盒 WebUI 全家桶、角色重置。
- 删除旧 World runtime 全部代码/测试/界面：活动、主动性与生活片段归沙盒所有；
  `CharacterState` 只能由 `SandboxStateManager` 写入。
- 群参与与主动性并入 `/sandbox/chat`（私聊回复 / 群参与 / 主动回复三块独立配置），
  `/behavior` 重定向；配置段顺序修正，两处配置文件对齐。
- 角色页人设改为从 Bible 编译，支持版本感知的人设同步与热加载。

## [1.2.0] — 2026-09-30 · Human Conversation Runtime + Character Continuity

- 回合运行期：合并/去抖、生成中断、静默判定、分段与延迟模型（区间 + 抖动 + 状态因子）。
- 角色延续：未闭环话题、共同经历、互动画像、情绪惯性。
- 历史时效标记（`[今天凌晨4点]` 式）与出站标记剥离。
- 表情包采集落盘、视觉确认的表情包可收藏、回复会接住"配字"而不是播报收藏。

## [0.1.0] — 2026-09-30 · 从零自研的运行时骨架

- 自研 Event / Message / Segment / Router / Command / Permission / Plugin / Config / DB / Logger，
  **不依赖** NoneBot / NcatBot / MaiBot / AstrBot / Koishi。
- OneBot 11 反向 WebSocket 适配（token 鉴权、单连接接管、断线保活）、
  API 客户端（echo/future/超时/断开清理）、事件总线、命令系统、权限、插件加载、SQLite（WAL）。
- 内置 `/ping` `/help` `/about`；WebUI 起步（登录 + 仪表盘）。
