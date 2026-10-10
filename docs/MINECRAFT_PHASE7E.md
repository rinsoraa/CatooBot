# CatooBot Minecraft Phase 7E · Skill Learning & Procedural Memory（技能学习与程序性记忆）

> 状态：**实施中**。本文件先落审计结论与最小设计（任务书 §8/§9），随后记录实现、
> 数据模型、迁移理由、适用性算法、端到端切片、门禁与已知限制。
>
> 基线：`072edc0d8d7491b439dc8a8dec747d4e4dc9d78d`（Phase 7D.2.1）。
> 阶段唯一目标（任务书 §6）：建立**最小但完整**的程序性技能生命周期 ——
> 真实终态+步骤结果+可验证后置条件 → 证据资格门 → 结构化技能候选/验证 → 持久化/幂等 →
> 有界检索 → 适用性评估 → **计划候选**（不是任务）→ 既有批准/确认 → 既有执行链 →
> 结果回流到技能。
>
> **执行层永远是既有链路**：`TaskProposal → AgentPlan →（批准/确认）→ TaskRuntime → Policy
> → AgentBridge → ActionRuntime → MinecraftService`。技能**没有**任何执行入口。

---

## 1. 审计结论（以源码为准，行号取自基线 `072edc0`）

### 1.1 学习证据的真实来源

| 结论 | 源码事实 |
| --- | --- |
| 任务终态事件由 `TaskRuntime._publish` 同步发出 | `app/tasks/runtime.py:1412-1417`；事件名常量在 `app/tasks/models.py:210-248`（`task.succeeded / task.failed / task.expired / task.cancelled`…） |
| 事件载荷**不含执行结果** | `_event_payload`（runtime.py:1713-1736）只有 `task_id/event_seq/source/session_id/step_id/state/step_state/tool/failure/message/plan_version/replan_reason/recovery_outcome/timestamp` —— 要拿步骤结果必须**重新读持久化 TaskRecord** |
| 因此学习必须回到 TaskRecord | `TaskRuntime.get(task_id)`（runtime.py:306）→ `TaskRecord`：`steps[i].state/status/failure/error/result/attempts/action_id/started_at/finished_at`、`verification`、`plan_version`、`plan_history`、`event_seq`（models.py:332-438、668-913） |
| 成功任务的**独立**后置验证已存在 | `_finish`（runtime.py:1260-1284）用一次新鲜 SAFE `minecraft_inventory` 读校验 `expected_final_state.inventory_delta`（`_verify_inventory` 1286-1307）；结果落在 `record.verification` |
| 现有"任务经历"记忆桥在收尾时被调用 | `bot._publish_task_event`（bot.py:1588-1602）→ `_remember_task_outcome`（1562-1579，只认 succeeded/failed/expired，**不认 cancelled**）→ 后台读 `tasks.get(task_id)` → `MinecraftMemoryBridge.on_task_finished(record)`（memory_bridge.py:288-312） |
| 该桥**不是**技能库 | 成功时写 `MinecraftMemoryFact(kind=TASK, source=TASK_RESULT)`；`dedupe_key = task:{objective}`（model.py:221-225）→ 同目标的多轮任务会合并成一行、`task_id` 不进键；`_remember_task_target`（314-340）**不检查 dig 步骤状态**就写 RESOURCE 事实 —— 这正是 7D §10.4 的歧义面 |
| 任务终态并非全部可见 | `create_task` 的校验失败分支只 `_save(TASK_FAILED)` **不 publish**（runtime.py:386-392）；`task.cancelled` 桥不消费 —— 学习层必须自己覆盖这四种终态，不能只依赖现有桥 |

### 1.2 规划与执行的接入点

* `BoundedAgentPlanner.plan(objective, *, target, observe)`（agent_planner.py:124-137）是
  **跟随 / 资源 / 未知**三分派点；`_policy_blocked`（264-277）只做预检，真正的校验在
  `TaskRuntime._validate_plan`（runtime.py:1446-1455，经 `create_task` 386-392）。
* `AgentPlanService(planner=…)` 是**鸭子类型**接缝（agent_service.py:64-98）：任何提供
  `async plan(objective, *, target, observe) -> PlanningResult` 的对象都行；三个入口
  （`record_user_plan` / `plan_follow_from_user` / `plan_from_proposal`）都会自动走它。
* **但 QQ 资源任务的真实入口不过 planner**：`QQTaskEntry._handle`（qq_entry.py:337-414）
  → `TaskTurnHandler.handle`（turn.py:197-233）→ `_create`（445-496）直接调
  `plan_resource_task(...)` 再 `runtime.create_task(...)`。所以"去挖一块橡木并捡回来"
  这条链的复用接缝在 **`turn.py::_create`**，而不是 AgentPlanService。
* 计划候选落地成任务的**唯一**通道是 `AgentPlanService` 或 `TaskTurnHandler` 调
  `TaskRuntime.create_task(...)`；两者都会过 `validate_plan`（validation.py:34-106）与确认门。
* `TaskPlan.observations` **不进 plan_hash**（models.py:532-545 只包含 objective/steps/
  expected_final_state）→ 可以安全地用它携带 `skill_reference` 审计字段（不改变授权指纹）。
* 步骤引用机制已存在：`collect_references` / `resolve_arguments`（validation.py:127-160）——
  技能的"动态参数"必须复用它，不另造一套。

### 1.3 存储与迁移的既有做法

* `_MIGRATIONS: list[tuple[int, str, str]]`（database.py:20-1537），当前最高 **33**；
  `Database._migrate`（1570-1590）按版本补齐并写 `schema_migrations`，脚本必须幂等；
  测试 `tests/test_activity_plan_persistence.py:226-238` 钉住"最高版本 == 33"（新增迁移要一起改）。
* 记忆引擎（`memories` + `memory_embeddings` + FTS5 + 合并/压缩/保留/配额）是**通用记忆**；
  它的既有域适配先例（sandbox / minecraft）都是"复用 category/layer/status + provenance +
  dedupe_key"，但它们的记录都是**自然语言事实**，没有步骤模板与生命周期机。
* `app/sandbox/memory_foundation.py` 提供了"候选 → 晋升"的两段式先例（`sandbox_memory_candidates`
  表 + `dedupe_key` + reason_code），但它是沙盒经历专用。
* 角色重置/导入的白名单是 `app/sandbox/lifecycle.py::CHARACTER_TABLES`（24-63）——
  新表若要随角色重置清空，必须登记在那里。
* 配置块房型：小而有界的 pydantic `BaseModel`（如 `TaskRuntimeConfig` settings.py:1117-1127），
  挂到 `AppConfig`，并同步 `config/config.example.yaml`（`tests/test_config_example.py`）。
* `character_id` 的来源：`Bot._activity_character_id()`（bot.py:1340-1353）=
  `sandbox.character_id`（形如 `罐头@1dae9716`）；服务器身份来自
  `MinecraftMemoryBridge.server_id()`（memory_bridge.py:151）。

### 1.4 7D §10.4 dig 归因缺陷（本阶段只做隔离，不修它）

* `minecraft_dig` 在 JS 侧是 `detached: true`（minecraft_runtime/runtime.js:2018）→ 步骤先
  `WAITING_ACTION`（`started_at` 由 `_settle` 落），终态由 `on_action_event` 落 `finished_at`
  与 `step.result`（runtime.py:1076-1084）→ **步骤时长可测量**。
* `wait()` 的复核（runtime.js:2149-2156）只验"方块不再是原方块"：**别人移除**与**自己挖掉**
  在 `step.result` 里长得一样（`block_before/block_after` 无归因字段）。
* 计划期已知 `dig_capability.dig_time_ms`；因此**保守可用的判别**是：实测时长显著短于
  预期挖掘时长 ⇒ 方块很可能是被别人移除的 ⇒ 该样本标 `AMBIGUOUS`（保留来源、不计正向）。
  仅此一条不够，故本阶段对**所有改动世界的步骤**一律要求"独立后置条件"（运行时自己的
  新鲜 SAFE 读校验）才计正向证据。
* 不修改 7D 执行路径：隔离全部发生在学习资格门。

---

## 2. 最小设计（任务书 §8 要求：先写清再实施）

### 2.1 数据模型（新增，两个表，迁移 34）

```text
procedural_skills                        -- 一条"可复用的方法"
  skill_id (SK-…唯一) / schema_version / name / objective_pattern / summary
  status: CANDIDATE | ACTIVE | STALE | INVALIDATED | REJECTED（显式转移表，终态不可复活）
  character_id / server_id / environment / tools_signature
  slots / steps / preconditions / success_criteria / required_capabilities / risk_summary
  version / supersedes_skill_id / superseded_by / fingerprint(唯一索引)
  success_count / failure_count / ambiguous_count / last_*_at / reason / payload

procedural_skill_evidence                -- 学习账本（一次任务一行，幂等）
  evidence_id / skill_id（可空=未晋升的学习尝试）/ subject_key
  task_id / plan_version / plan_hash / step_ids / outcome / verdict
  verdict: POSITIVE | AMBIGUOUS | REJECTED | COUNTEREXAMPLE
  reason_code（稳定码）/ postcondition_kind / postcondition_ok / detail / payload
  UNIQUE(subject_key, task_id, plan_version, plan_hash)   -- 重复终态/重复回调/重启不重复计数
```

**为什么新增而不是塞进 `memories`**（任务书 §9 要求的对比）：

* 复用 `memories` 方案：只能把步骤模板/前条件/生命周期压进 `provenance` 自由 JSON，而
  记忆引擎会把它当**自然语言事实**处理 —— 合并/压缩会改写正文、保留策略会过期删除、
  配额会裁剪、语义检索会把技能当聊天记忆注入。技能需要的是**结构化、带版本、带条件、
  终态不可复活**；这些语义与记忆引擎相反。**不采用**。
* 新增两个最小表：仍属同一套记忆/技能体系 —— 走同一个 `Database`（同一 SQLite、WAL、
  迁移链、事务），审计历史**复用 `behavior_events`**（`skill.*` 前缀），与 7A/7C/7D 的
  房型一致；不新建第二记忆引擎、不新建第二任务状态机。**采用**。

### 2.2 学习资格门（`skill_learning.py`，纯函数 + 稳定 reason code）

判定顺序（任一不满足即给出原因码，并写一条 `REJECTED`/`AMBIGUOUS` 证据）：
`not_terminal` → `not_succeeded`（FAILED/CANCELLED/EXPIRED/PAUSED/WAITING_* /PENDING_*/REPLANNING）
→ `no_steps` → `step_not_in_final_version`（旧 plan 版本/被 supersede 的步骤不许拼进来）
→ `step_not_succeeded:<id>` → `step_waiting_action` → `unverified_world_change`（改动世界的步骤
缺少独立后置条件）→ `ambiguous_world_change`（dig 时长显著短于预期）→ `no_verifiable_postcondition`
→ `unsupported_step:<tool>`（不在当前注册工具/无服务路由）。全部通过 → `QUALIFIED`。

晋升（两段式，任务书 §7.2）：第一条合格任务 → `CANDIDATE`；**独立第二条**合格任务
（不同 `task_id`、同归一化方法、同样有后置条件）→ `ACTIVE`。阈值是**证据条数**，不是模型打分。

### 2.3 归一化与去重

* 一次性执行值（坐标、`entity_id`、session、UUID）→ **槽位**（`target_position`、
  `drop_entity`…），槽位在复用前必须由新鲜 SAFE 观察或用户请求重新解析；
* 步骤间依赖复用既有 `{"from_step","path"}` 引用（不新造变量机制）；
* `fingerprint = sha256(character|server|tools_signature|normalized_steps)`，唯一索引去重；
* 方法变化 → 新 `version` + `supersedes_skill_id`（旧版转 `STALE`，证据不覆盖）。

### 2.4 适用性评估（封闭集合，可审计）

`APPLICABLE | INAPPLICABLE | UNKNOWN | STALE` + reason code；逐条检查：目标/槽位匹配、
工具仍注册且 schema/风险未变（`tools_signature`）、服务器一致、生命周期未失效、
前置条件可由**新鲜 SAFE 观察**证实（观察失败/不可读 ⇒ `UNKNOWN`，**永不**当作满足）、
`VERIFIED` 身份（目标相关技能）、步骤/动作/风险预算在既有上限内。

### 2.5 与既有规划器的接入（两处，均只产出"计划候选"）

1. `turn.py::TaskTurnHandler._create`：资源任务在调 `plan_resource_task` **之前**问一次
   技能服务；适用 → 用技能模板物化 `TaskPlan`（含 `skill_reference` 观察）→ 交给**同一条**
   `runtime.create_task`（照旧过 `validate_plan` + 确认门）。不适用/异常 → **原样回退**。
2. `app/tasks/skill_planner.py::SkillAwarePlanner`（包装 `BoundedAgentPlanner`）：注入
   `AgentPlanService(planner=…)`，覆盖 LIFE/跟随路径；异常一律回退基规划器。

### 2.6 端到端垂直切片（E10）

"资源任务"（当前已支持、参数边界清晰）：真实成功任务 → 资格门 → 技能候选 → 第二条真实
成功任务 → `ACTIVE` → 重启后新实例检索到 → 适用性评估 → 生成计划候选 → 既有
`create_task`(+确认门) → 真实执行 → 结果回流（成功累计 / 失败转 `STALE`）。

### 2.7 风险门禁与降级

技能服务任何读写异常 → `degraded_reason` 置位、学习与检索**整体降级为不可用**，
既有规划/聊天/任务链**行为逐字不变**；技能文本一律当数据，不改变任何 gate；
`allow_medium=false`、19 工具、ActionRuntime 动作集合、TaskRuntime 状态集合、迁移 31/32/33
的一字不改。

---

## 3. 实施记录（新增文件与理由）

| 文件 | 作用 |
| --- | --- |
| `app/tasks/skill.py` | 技能模型（状态机 / 槽位 / 步骤模板 / 前后条件 / 证据 / 指纹 / 序列化） |
| `app/tasks/skill_learning.py` | **学习资格门**与归一化（纯函数：真实记录 → 方法候选 或 稳定原因码） |
| `app/tasks/skill_store.py` | 存储（`SkillStore` 协议 + 内存实现 + `SqliteSkillStore`；三张表 + `behavior_events` 审计） |
| `app/tasks/skill_service.py` | 门面：学习 / 检索 / 适用性 / 物化 / 回流 / 只读视图 / 降级 |
| `app/tasks/skill_planner.py` | 包装规划器（技能优先、风险闸门同源、异常原样回退） |
| `app/database/database.py` | **迁移 34**：`procedural_skills` + `procedural_skill_evidence` + `procedural_skill_usage` |
| `app/config/settings.py` | `skills:` 配置块（五个旋钮，全部有界） |
| `app/tasks/turn.py` | 资源任务入口接缝：模板之前问一次技能；异常回退 |
| `app/core/bot.py` | `_setup_skills()` 装配 + 终态钩子（与记忆桥**互不依赖**） |
| `app/web/api/domain.py` | `GET /api/v1/world/skills` 只读观测（无任何执行/复用触发入口） |
| `tests/skill_fakes.py`、`tests/test_skill_learning.py`、`tests/test_skill_service.py`、`tests/test_skill_wiring.py` | 夹具 + 三组回归（资格门 / 服务与真实链 / 接线与安全边界） |

没有新增：Minecraft 原子工具（19→19）、ActionRuntime 动作（0）、TaskRuntime 状态（0）、
第二套记忆引擎 / 任务状态机 / 调度循环。迁移 31/32/33 一字未改（34 是新增）。

## 4. 学习资格门（逐条对照任务书 §7.2）

| 任务书要求 | 实现 |
| --- | --- |
| 1. 来源是真实持久化记录 | 学习唯一入口 `SkillService.on_task_finished(record)`，由 `Bot._remember_task_outcome` 在**终态事件**后用 `tasks.get(task_id)` 读**持久化** `TaskRecord` 后调用；模型回复/聊天记忆/提案文本都不在这条路上 |
| 2. 只有真实 `SUCCEEDED` | `qualify()` 先查 `state`：不可读 → `unknown_state`；非终态 → `not_terminal`；终态但非成功 → `not_succeeded:<STATE>`（FAILED/CANCELLED/EXPIRED 全覆盖） |
| 3. 只用**当前最终计划版本**的步骤 | 取 `record.plan.steps`（当前版本）+ 要求 `plan_status == COMPLETED`；旧版本（被 supersede）的失败步骤根本不在其中 → `final_plan_not_completed` 兜底 |
| 4. 幂等（重试/异步 action/事件重放/重启） | 证据表 `UNIQUE(subject_key, task_id, plan_version, plan_hash)` + 服务层先查 `evidence_for_task` → 重复终态/重复回调/重启都返回 `duplicate`，计数不重复累加（内存与真库各有一条测试） |
| 5. 依赖世界变化的步骤必须有可验证后置条件 | 主力：运行时**自己的**独立后验（`_finish` 用新鲜 SAFE 背包读算出的 `inventory_delta`）覆盖该步的预期效果；`minecraft_dig` 另加"目标位置已经不是原方块"的新鲜 SAFE 复核；缺失 → `world_change_unverified:<step>` = **AMBIGUOUS**；`minecraft_place` 的效果不在背包里 → 没有步骤级验证器就一律歧义（不靠 `ok` 冒充） |
| 6. dig 归因歧义必须排除 | 时长信号：计划期 `dig_capability.dig_time_ms` 已知、实测步骤时长 < 预期 × `dig_short_ratio`（默认 0.5）→ `ambiguous_world_change:<step>` = AMBIGUOUS。**残余窗口如实声明**（见 §7） |
| 7. 可解释 | 每个拒绝/歧义都写一条证据行（`verdict` + `reason_code`）并可经只读视图看到；没有用"模型置信度"替代判定 |
| 两段式晋升 | 第一条合格证据 → `CANDIDATE`（**不参与复用**）；独立第二条合格证据 → `ACTIVE`（`promotion_min_successes` 默认 2，最小 2） |

## 5. 归一化 / 去重 / 版本（§7.3）

* 一次性值 → 槽位：坐标 `x/y/z` 折叠成 `{"$slot": "target_position"}`；`entity_id` 保持既有
  `{"from_step","path"}` 引用（不新造变量机制）；
* 不可安全泛化的值保留为**硬约束**（例如 equip 的物品、dig 的 `expected_block/expected_tool`），
  同时抽出 `inventory_has` 前置条件 —— 复用前必须还在背包里（§7.4 宁可判不适用）；
* 指纹 `sha256(character|server|tools_signature|objective_pattern|normalized_steps)`，
  唯一索引去重；**位置无关**（同一方法的两次不同坐标任务合并成一条技能）；
* 方法变化（步骤/契约变）→ 新指纹新行；`version/supersedes_skill_id/superseded_by` 留 lineage，
  旧证据不被覆盖；工具契约（名字+风险+schema 指纹）变化 → 适用性评估判 `STALE`；
* 技能正文**不落**世界快照 / 凭据 / 聊天记录 / 原始 Task payload，只留 ID 引用。

## 6. 有界检索与适用性（§7.4）

* 检索：按 `(character_id, server_id, method_class)` 只取 `ACTIVE`，上限 `retrieve_limit`（默认 5）；
  `method_class` 由装配处注入的确定性分类器给出（资源目标 = `resource:<block>:<drop>`，
  与规划器同一张别名表），不做模糊文本匹配；
* 适用性检查项（每项都产出稳定 reason code）：生命周期必须 `ACTIVE`（否则 `skill_not_active:*`）、
  `schema_version`、角色、服务器、工具契约指纹（`tools_signature_mismatch` → `STALE`）、
  必需工具仍在（`tool_missing:*` → `INAPPLICABLE`）、风险在当前配置内
  （`risk_not_allowed:MEDIUM` 等，与 `minecraft.agent.tools.*` **同源**）、步骤非空、
  前置条件由**新鲜 SAFE 观察**证实（`inventory_has`：读不到/异常 → `UNKNOWN`，**永不**当满足）；
* 目标的方位/存在性在**物化期**用新鲜 `find_blocks` 解析（解析不到 = 不适用 → 回退），
  即"参数来源"必须实时重新观察，绝不沿用旧坐标。

## 7. 已知限制与残余风险（如实声明，不当作已解决）

1. **dig 归因的残余窗口**：若另一名玩家"真的挖掉"目标方块（产生掉落物）恰好与罐头的挖掘
   重叠，现有 `step.result` 没有归因字段，**无法区分**。本阶段只做隔离（后置条件 + 时长信号）
   并把不可归因样本判为 `AMBIGUOUS`；7D §10.4 的执行路径本身**未改**，该缺陷仍按独立 follow-up 跟踪。
2. **时长信号的作用范围**：它依赖计划里的 `dig_capability` 预期时长。`TaskPlan.observations`
   不随任务持久化（5A 既有语义），所以**重启后**再学习同一条记录时该信号不可用；主力隔离
   （后置条件）不受影响。技能复用时会在物化期重新探测并把结果放进计划观察里。
3. **技能使用链按 `(objective, plan_hash)` 记账**：物化发生在"任务还不存在"之前，所以不记
   `task_id`；同一技能 + 同一目标 + 同一计划哈希的重复复用会共用一条链（同指向该技能，无害）。
4. **只支持资源类方法**：`SkillAwarePlanner` 只对 `detect_shape == "resource"` 的目标问技能；
   跟随/未知目标仍走既有能力目录判定（本阶段不做泛化，避免为演示编造能力）。
5. **不作为训练数据的东西**：计划被生成/被批准/任务被建立都**不计**成功；只有"合格终态 +
   独立后置条件"才计。模型权重训练、在线自改代码、自动安装新能力一律**没有**实现。

---

## 8. 验收矩阵（E1–E15 逐条 → 测试）

| ID | 验收点 | 证据（测试） |
| --- | --- | --- |
| E1 | 真实任务证据入口 | `TestEvidenceEntry`（真实 TaskRecord 通过；缺步骤/状态不可读被拒）+ `TestLearningFromARealRun`（记录由**真实 TaskRuntime** 跑出）；技能层 AST 守卫证明它不调任何工具 |
| E2 | 成功资格门 | `TestSuccessGate`：FAILED/CANCELLED/EXPIRED/PAUSED/WAITING_ACTION/WAITING_USER/PENDING_CONFIRMATION/REPLANNING/PLANNING/RUNNING 全部拒绝，reason code 精确；步骤未执行 → `step_not_run:*` |
| E3 | 已知歧义隔离 | `TestDigAmbiguityQuarantine`：无独立后置条件 → `world_change_unverified:*`（AMBIGUOUS）；实测时长 < 预期×0.5 → `ambiguous_world_change:*`；`minecraft_place` 无步骤级验证器 → 一律歧义；7D.2 真机形态（假成功 + 掉落物丢失 → 任务 FAILED）被拒 |
| E4 | 当前最终计划版本 | `TestFinalPlanVersion`：重规划 A 失败/B 成功 → 只取 B；最终版本未标记 COMPLETED → 拒；归一化保留引用、坐标折叠成槽位 |
| E5 | 幂等 / 重启恢复 | `TestLearningLifecycle::test_duplicate_final_event_does_not_double_count`（内存）+ `TestPersistenceAcrossRestart::test_sqlite_evidence_is_idempotent_across_restart`（真库重启后仍只一条）+ 迁移 34 的 `UNIQUE(subject_key, task_id, plan_version, plan_hash)` |
| E6 | 技能结构与溯源 | `TestStructureAndDedup`（模板不含一次性值/session/user；判据存在）+ 技能 `extra{subject_key, sample_objective, source_task_id}` |
| E7 | 去重 / 版本 | `test_same_method_merges_but_different_method_does_not` + `test_changed_method_creates_a_new_version_with_lineage`（版本 +1、旧版 STALE、`superseded_by` 回链、旧证据保留） |
| E8 | 检索与匹配 | `test_similar_objective_matches_and_other_does_not`（同方法类命中，异目标/闲聊不命中） |
| E9 | 适用性检查 | `TestRetrievalAndApplicability`：签名变化 → STALE；工具缺失 → INAPPLICABLE；服务器不匹配 → INAPPLICABLE；风险不允许 → INAPPLICABLE；观察失败 → **UNKNOWN**（永不升级）；CANDIDATE 不可复用 |
| E10 | 计划集成 | `TestVerticalSlice`（真库 → 重启 → 检索 → 物化 → 过 `validate_plan` → 真实 `create_task`）+ `TestLearningFromARealRun::test_reuse_through_the_same_planner_and_runtime`（真实运行时两次成功→ACTIVE→复用→反馈回流） |
| E11 | 执行边界 | `TestSourceLevelBoundaries`（AST：禁止调用/导入执行面）+ `test_materialized_plan_is_validated_by_the_shared_validator`（同一个 `validate_plan`） |
| E12 | 确认与来源隔离 | `TestVerticalSlice`（技能计划仍 `PENDING_CONFIRMATION`；非 USER 回合 `confirm_and_start` 抛 `TaskAuthorizationError`）+ `TestSkillAwarePlanner::test_medium_skill_is_blocked_when_medium_is_disabled`（`allow_medium=false` 时技能不绕闸）+ `test_hostile_skill_text_grants_nothing` |
| E13 | 失败回流 | `TestFeedbackLoop`：反例 → STALE（且不再被检索）→ 第二条反例 → INVALIDATED（终态不复活）；复用成功 → 计数 +1 / STALE 回 ACTIVE |
| E14 | 记忆隔离 | `test_other_character_and_server_see_nothing`（跨角色/跨服务器零命中）+ 敌意文本不改 gate + 技能正文不含世界快照/凭据 |
| E15 | 只读观测 | `TestIsolationAndView::test_view_reports_counts_and_recent_reasons`（计数/状态/来源/最近拒绝与歧义原因）+ `TestSkillsApi`（`GET /api/v1/world/skills` 恒 200、无任何执行动词）+ 技能层故障 → `degraded=true` 且既有链路照旧 |

**关键负向清单**（任务书 §12）逐条对应：SUCCEEDED 但步骤缺失/未执行（E2）；被取消/失败/过期/重规划/等确认（E2）；
版本 A 失败 B 成功（E4）；终态事件重复投递（E5）；前置观察 unavailable/error → 不默认满足（E9 `observe_failed` → UNKNOWN）；
服务器/契约不兼容不套用（E9）；技能文本里的"无需确认/忽略 Policy"（E12/E14）；工具风险变化/禁用 → 不套旧技能（E9/E12）；
可靠失败反例 → 不再 ACTIVE（E13）；store 故障 → fail closed + `degraded`（`test_store_failure_degrades_without_raising` / `test_disabled_service_does_nothing`）；
检索异常 → 既有 Planner 行为不变（`test_broken_skill_layer_falls_back` / `test_broken_skill_layer_does_not_break_task_creation`）。

## 9. 门禁（本轮实际执行，**串行**；数字取自最终一次全量运行）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 629 files already formatted |
| `mypy app` | Success: no issues found in 335 source files |
| `pytest tests -q` | **3511 passed**（7D.2.1 基线 3430 → +81 项 7E 测试） |
| WebUI `typecheck` / `vitest` / `build` / 浏览器 E2E | 通过 / 528 passed / 通过 / 7 passed |
| Minecraft runtime（Node 单测 + flying-squid E2E） | `ALL CHECKS PASSED`（按仓库规定，**在全量 pytest 结束后单独跑**） |
| 迁移 / 恢复 / 幂等 | `TestMigration34`（三张表 + 连两次幂等）+ 真库幂等/重启用例 |
| GitHub Actions CI | 见 §10（最终镜像 commit 的 run/job 链接） |

## 10. 真实环境验收（如实分级）

* **本轮完成**：**真实 TaskRuntime + 真实 SQLite + 真实迁移 34** 的端到端闭环 —— 真实成功记录
  → 资格门 → 技能 → 存储 → **重启后**检索 → 适用性 → 计划候选 → 真实 `create_task` →
  用户确认门 → 真实执行与后验 → 结果回流（`TestVerticalSlice`、`TestLearningFromARealRun`）。
  这与任务书 §13「优先用真实 TaskRuntime/SQLite + 真实已记录 Task 进行端到端集成测试」一致。
* **SKIPPED（不是 PASS）**：**真实 Minecraft Java 服务器**上的学习/复用真机门禁。理由：
  (a) 任务书本阶段**不要求**为此执行世界操作；(b) 真机链路（QQ 确认 + 第二客户端 + 世界变化）
  在 7D.2 已逐条取证，7E 不新增世界语义；(c) 本轮为纯学习/复用链，真实服务器能额外证明的只有
  "同一条链在真机上跑得通"，而它与 7D 执行链的同构性已由 `TestVerticalSlice` 的**真实 TaskRuntime** 覆盖。
  如需真机复核，按任务书 §13 的低风险受控目标 + 操作者确认流程单独执行。
* **已知缺陷（独立 follow-up，不属 7E）**：7D §10.4 dig 归因歧义 —— 本阶段只做**学习数据隔离**，
  未改执行路径（见 §7.1）。

---

## 12. 7E.1 正确性收口（2026-10-10，基线 `958143e`）

7E 验收复核发现两个正确性缺口。本轮**只**修它们，不扩展技能种类、不动任何执行语义
（§3 边界全部保持：工具 19、ActionRuntime 动作 0、TaskRuntime 状态 0、`allow_medium=false`、
7D 的确认/批准/身份/取消/暂停/恢复/超时语义、技能只输出计划候选、CANDIDATE 不可复用、
终态不复活、7D §10.4 dig 归因缺陷仍独立跟踪）。

### 12.1 缺口 A：成功证据与技能计数不是一个原子操作

**根因**：原学习顺序是「查重（读）→ await 后置条件观察 → `_upsert_positive` 改计数/晋升 →
最后 `add_evidence` 落证据」。同一个 `TaskRecord` 的两个并发终态回调可以**都**通过那道读检查，
在唯一索引拦住第二条证据之前**各自**把 `success_count` 加过一次 —— 只有一条独立证据的技能
会错误晋升 `ACTIVE`；`ambiguous_count` 也走同一套"读-改-写"（`_bump_ambiguous`）。

**修复（最小改动，不重写 7E）**：

* 新增存储层原子操作 `SkillStore.claim_evidence(evidence, *, thresholds, new_skill=None,
  attach_subject=False, feedback=None) -> ClaimOutcome`：
  1. **先认领**：`INSERT OR IGNORE` 进证据表，唯一索引 `(subject_key, task_id, plan_version,
     plan_hash)` 是**唯一**的门（行数为 0 ⇒ 重复回调，直接返回、一个计数都不动）；
  2. 认领成功才解析目标技能（已存在 / 新建 / 按 `subject_key` 附着 / 复用反馈）；
  3. **计数由证据行聚合派生**（`SELECT verdict, COUNT(*) ... GROUP BY verdict`）而不是增量累加 ——
     并发认领天然收敛，不存在 lost update；
  4. 状态由纯函数 `derive_skill_state(current, counts, thresholds, trigger)` 唯一决定
     （终态无出口；CANDIDATE/STALE 达到阈值 → ACTIVE；反例达到阈值 → INVALIDATED，否则 STALE；
     歧义/拒绝只记数）；同一事务里整行写回（列 + payload 同步）。
  * SQLite 走 `Database.run_in_transaction`（单事务，跨进程有 SQLite 写锁 + 派生收敛兜底）；
    内存实现是**无 await 的临界区**（其中每个被 await 的存储方法自身都不让出事件循环），
    两种实现有等价性测试。
  * 复用反馈在同一事务里**消费绑定**（`consumed_at`），所以第二次终态回调拿到的是
    "绑定已消费 → duplicate"，不会再计一次。
* 服务层的查重预检**保留但降级为快速路径**（只省一次观察），不再承担正确性。

### 12.2 缺口 B：使用链在任务创建前登记，归因键不足以证明真实复用

**根因**：原 `materialize()` 在计划候选阶段就写 `procedural_skill_usage(objective|plan_hash)`。
任务没建成（`TaskBusy`/校验失败/过期/取消/回退）也会留下一条"活的"记录；而计划哈希**不含**
observations、`TaskPlan.to_payload()` 也不序列化 observations，所以任何同目标 + 同计划内容的
**普通任务**之后都可能被误认成"用过技能"，导致无关任务给技能加分 / 让技能转 STALE / 被错误
INVALIDATE。

**修复**：

* **删掉派生键使用链表**（迁移 35 `DROP TABLE`,不留兜底），改为每任务唯一的**持久绑定**：
  `procedural_skill_bindings`（`task_id` 唯一、`skill_id`、`subject_key`、`plan_version`、
  `plan_hash`、`created_at`、`expires_at`、`consumed_at`、`outcome`）；
* **只在任务真正建立之后写**：`SkillService.bind_task_for_plan(record, plan)` 由三条真实入口调用 ——
  `TaskTurnHandler._create`（QQ USER 资源任务）、`AgentPlanService.approve`（LIFE 第二道门）、
  `AgentPlanService.plan_follow_from_user`（USER 跟随）；绑定失败只降级（**宁可不反馈，也不猜**）；
* **归因只按 `task_id`**：`on_task_finished` 先查绑定，查不到就走普通学习资格门 ——
  目标文本与计划哈希**不再**参与归因；
* **LIFE 路径的引用传递**：AgentPlan 的 `checks` 是真实持久列（observations 不是），
  `SkillAwarePlanner` 把 `skill_id/skill_version/fingerprint` 写进 `skill_reuse` check 的 detail，
  批准建任务时从**已持久化的 AgentPlan** 读回来绑定 —— 未批准/取消/过期/建任务失败的计划
  不会留下任何有效绑定；
* 绑定随任务 TTL 带 `expires_at`（作废/清理语义，`prune_bindings`）；`task_id` 唯一意味着
  未来的任务**不可能**撞上旧绑定（旧设计误认的结构性根因被移除）。

### 12.3 顺带修掉的一个真缺陷（同族，来自 12.1 的测试）

已执行记录的 `step.effective_arguments` 会把 `{"from_step","path"}` **解析成字面量**
（例如 `entity_id: 42`）。原归一化优先用它 → 同一方法的第二次学习指纹不同 → 会**另建一条技能**，
并且把一次性动态 ID 写进技能正文（违反 §7.3/E6）。修复：归一化一律取**冻结模板**
`step.arguments`（`_template_arguments`），引用保持引用形态、坐标照旧折叠成槽位。

### 12.4 迁移 35

* 新增 `procedural_skill_bindings`；
* `procedural_skills` 增加 `subject_key` 列（+ `json_extract` 回填 + `(character_id, server_id,
  subject_key)` 索引）；
* 删除 `procedural_skill_usage`（34 的派生键使用链表）；
* 升级兼容：`TestMigration35::test_upgrade_from_a_v34_database_applies_35` 把库退化成 v34
  （删绑定表、删 `subject_key` 列、删迁移记录）后重连 —— 列/表被补回、`memories` 里的既有
  业务数据不动、版本落到 35；最高版本冻结测试同步到 **35**。

### 12.5 测试（新增 17 项，全部可失败）

| 任务书要求 | 测试 |
| --- | --- |
| 同一 TaskRecord 并发回调（barrier 制造竞争窗口），内存 + 真 SQLite | `TestConcurrentEvidenceClaims::test_same_task_concurrent_callbacks_claim_once_in_memory` / `..._real_sqlite`：恰一条证据、`success_count == 1`、仍 `CANDIDATE` |
| 两个不同 task 并发仍累计两条并晋升（无 lost update） | `..._accumulate_two_evidences` / `..._real_sqlite`：`success_count == 2`、`ACTIVE`、证据两条 |
| 歧义/拒绝与正向互不覆盖、不重复累加 | `..._do_not_cross`（并发）+ `..._attaches_to_the_same_subject_sequentially`（顺序附着）+ `..._counts_once_real_sqlite` |
| 阈值来自配置且参与事务内派生 | `..._thresholds_are_respected_from_the_store_transaction`（阈值 3 → 两条不晋升） |
| 候选物化但任务没建成 → 之后同形普通任务不得回溯 | `TestTaskBindingAttribution::test_candidate_without_a_created_task_never_feeds_back`（`last_used_at == 0`） |
| 重启后归因不丢、重复终态不重复反馈 | `..._binding_routes_the_terminal_event_after_restart`（真库重启 + 二次回调 duplicate） |
| 同 plan_hash 的技能任务与普通任务并存只有绑定那条回流 | `..._only_the_bound_task_of_two_identical_plans_feeds_back` |
| LIFE 路径（AgentPlan → 批准 → 建任务）绑定准确；未批准/建任务失败无绑定 | `..._life_path_transfers_the_reference_through_the_agent_plan` |
| 无绑定任务绝不按文本猜来源 | `..._unknown_task_without_binding_is_never_guessed` + 源码级 `..._store_is_the_only_attribution_source` |
| 两种 store 行为一致 | `TestStoreProtocolParity::test_in_memory_and_sqlite_agree` |
| 既有顺序重复 / 重启重放 / 跨版本 / 终态不可复活 | 7E 的 `TestLearningLifecycle` / `TestPersistenceAcrossRestart` / `test_changed_method_creates_a_new_version_with_lineage` / `TestFeedbackLoop` 全部保留并通过 |

### 12.6 门禁（本轮实际执行，串行）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` / `ruff format --check .` | All checks passed / 630 files already formatted |
| `mypy app` | Success: no issues found in 335 source files |
| `pytest tests -q` | **3528 passed**（7E 基线 3511 → +17 项 7E.1 测试） |
| WebUI typecheck / Vitest / build / 浏览器 E2E | 通过 / 528 / 通过 / 7 passed |
| Minecraft runtime（Node 单测 + flying-squid E2E） | `ALL CHECKS PASSED`（全量 pytest 结束后单独跑） |
| 迁移最高版本冻结 / v34→35 升级 / 重复迁移幂等 | `test_latest_migration_is_idempotent`（35）+ `TestMigration35` 三项 |
| CI | 见 7E.1 最终报告（run/job 链接）。**首轮** Python job 红在 Node E2E 的时序敏感断言
（`[e2e] ASSERT FAILED: player distance`，follow 家族，与本轮 Python 改动无关且本地/历史 CI 都出现过），
按既有纪律重跑该 job 通过 —— 如实记录，不用绿色替代语义正确性 |

### 12.7 真实环境与限制（如实分级）

* 本轮为**真实 TaskRuntime + 真实 SQLite + 真实迁移 35** 的闭环（含重启后归因）；
* **真实 Java 服务器**：仍 `SKIPPED`（本阶段不新增世界语义，与 7E 同理由）；
* 限制：绑定只在三条真实入口建立（新增入口必须同样调用 `bind_task_for_plan`，否则该任务
  **不会**产生技能反馈 —— 保守方向，不会误归因）；`ambiguous` 证据在**并发**下若先于
  技能创建落库，会以 `skill_id=""` 留在账本里（可审计、不影响计数），顺序场景才附着到技能。
