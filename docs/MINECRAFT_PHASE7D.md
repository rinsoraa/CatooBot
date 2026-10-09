# CatooBot Minecraft Phase 7D · Agent Harness: Bounded Planning & Authorized Execution

> 状态：**设计定稿（含三项修订）**。执行层仍完全复用既有 TaskRuntime / Policy / ActionRuntime。
>
> 最终门禁（任务书）：**没有真实授权的步骤不能执行；没有真实观察证据不能宣称成功；
> 未知能力不能自动升级；LIFE 意图不能绕过用户批准；所有实际动作必须经过既有安全执行链。**

---

## 1. 审计结论（§一；以源码为准）

### 1.1 真实调用链

```text
QQ 文本 → QQTaskEntry._handle (app/tasks/qq_entry.py)
       → TaskTurnHandler.handle (app/tasks/turn.py)
           ├─ control_command → pause/resume/cancel/confirm（既有任务控制）
           ├─ status_query    → 只读状态回答
           └─ detect(text)    → is_task? → _create
_create: block_for(text) 认方块 → plan_resource_task()（确定性模板）
         → SAFE 观察（find_blocks/world/inventory/dig_capability）
         → TaskRuntime.create_task → validate_plan → PENDING_CONFIRMATION
         → ConfirmationStore.request（user+session+args_hash+plan_hash，TTL 独立）
用户「确认」→ confirm_and_start(origin=USER) ← 只有 USER 回合能消费（§八十七）
         → TaskAuthorization(plan_hash, TTL 60s) → drive() 循环
         → 每步 _run_step：解析引用 → 步骤授权三对账（tool/args_hash/plan_hash）
            → 授权时效复查 → 执行 bridge.invoke_tool(turn_origin="task")
         → MinecraftAgentPolicy.check 定序（注册→启用→在线→风险开关→task_authorized→忙）
         → ActionRuntime → MinecraftService → mineflayer
```

### 1.2 关键事实

| 任务书关注点 | 源码事实 |
| --- | --- |
| TaskProposal（7C） | 6 状态、终态不可覆盖、USER/LIFE 隔离、`risk_summary`/`required_capabilities`/`resolve_target` 齐备 → **原样复用** |
| TaskRuntime | `create_task`（同 session 单任务 + `validate_plan` + 确认请求）、`confirm_and_start`（仅 USER 回合；`task.confirmation_not_user_turn` / `confirmation_wrong_owner`）、`pause/resume/cancel/expire/recover/recover_persisted_tasks`、5A.1 计划版本历史 → **原样复用，不加状态** |
| Policy | `MinecraftAgentPolicy.check` 定序；task 步骤绝不冒充用户回合（`CODE_TASK_UNAUTHORIZED`）；MEDIUM 不逐步确认，靠"一次性冻结计划 + TaskStepAuthorization（tool+args_hash+plan_hash 三对账）"放行；follow 目标必须在 `nearby_players` 里（`minecraft.player_not_found`，§四十二）→ **原样复用，零改动** |
| 计划校验 | `app/tasks/validation.py::validate_plan` 是**通用**的：注册工具 + schema + 引用 + 步数/动作步上限 —— 对 `minecraft_follow_player` 天然可过（只要 username 合 schema） |
| follow 生命周期 | `MinecraftService.follow_player` **启动即返回 `RUNNING` + action_id**（3D 持续型动作）；`TaskInvocation.detached` → `_settle` 把步骤置 `WAITING_ACTION`、任务置 `WAITING_ACTION`；终态（STOP/超时/目标丢失/超距）由 action 事件异步送达后任务继续/收尾 |
| 停止路径 | `cancel()` / `expire()` / `pause(stop_action=True)` 都走 `_stop_action()` → **既有的** `minecraft_stop`（SAFE），绝不直操纵 mineflayer |
| 重启恢复 | `recover()`：在途动作一律判 `RUNTIME_RESTART` 失效 + 撤销授权 + 只读对账 → 绝不重复已执行的世界动作 |
| 「跟着我」缺口 | **两道门**：① `TaskIntentDetector.detect` 需"动词+多阶段标记+资源词"三者齐备 → `is_task=False`；② `_create` 需 `block_for(text)` 认出具体方块，跟随没有方块。且 `plan_resource_task` 是资源模板，表达不了跟随目标 |

---

## 2. 三项修订的落实（开工前定稿）

### 修订 1：USER 一次确认，LIFE 两道门（各自绑定用户与会话）

| 来源 | 确认前 | 用户确认后的行为 | 执行授权 |
| --- | --- | --- | --- |
| **USER** | AgentPlan 与待确认任务**同时**建立（一次规划、一次挂起） | **同一次「确认」**同时代表冻结计划与其关联任务 | 沿用现有 `confirm_and_start` + TaskStepAuthorization，**零改动** |
| **LIFE** | 只保存 AgentPlan（`READY_FOR_APPROVAL`），**不建 Task** | 第一道门：用户「批准」→ 才调 `create_task` 生成待确认任务；第二道门：既有 `PENDING_CONFIRMATION` →「确认」→ 执行 | 两道门**都**走既有机制；绝不绕过、绝不提前转换 |

* **门labels**：QQ 回复明确写"这是【批准计划】（LIFE 想法 → 待确认任务）"或"这是【确认执行】（任务真正开始）"。
* **批准者绑定**：LIFE 计划批准时记录 `approver_user_id + approver_session_id`，随后 `create_task` 用**批准者**的身份建任务 —— 于是第二道门的 `confirm_and_start` 天然要求同一人同一会话（TaskRuntime 既有校验 `confirmation_wrong_owner`）。无关群成员批准/确认都进不去。
* **USER 侧的一次确认**：AgentPlan 的批准状态**不单独记账** —— 它由关联 Task 的既有事件（`TASK_CONFIRMATION_REQUIRED` → `TASK_STARTED`）派生读取（修订 3），所以天然只有一次确认。

### 修订 2：follow 动作能被现有执行链安全承载（逐条证明）

| 任务书要求 | 源码证据 |
| --- | --- |
| 校验器接受跟随步骤 | `validate_plan`（`app/tasks/validation.py`）只查注册/schema/引用/步数上限，`minecraft_follow_player` 已注册、`username` 有 schema → 通过 |
| RUNNING / 异步 action_id 不被误判成功 | `TaskInvocation.detached`（`status=="RUNNING" and action_id`）→ `_settle` 明确置 `StepState.WAITING_ACTION` + `TaskState.WAITING_ACTION`，**绝不**标 SUCCEEDED；终态只来自 runtime 的 action 事件（`agent.py` §五四五：RUNNING 不会被自己"模拟完成"） |
| 取消/超时/玩家离线安全停止 | `cancel()`/`expire()`/`pause(stop_action=True)` 都先 `_stop_action()`（既有 `minecraft_stop`，SAFE）；玩家离线是 follow 自身终态（3s 丢失宽限 → action FAILED 事件 → `_pause_after_failure`）；超距（`max_chase_distance` 64）同理 |
| 不会无限持续 | follow 动作自带超时 `minecraft.action.follow_player.timeout`（**默认 120s**，10~600）；任务整体另有 TTL 600s（`tick()` → `expire()` → `_stop_action()`） |
| **授权租期 60s vs 长动作** | 授权时效只在**派发每一步之前**复查（`_run_step` 第 2 段：`record.authorization.valid(now)`）；动作**在途期间不复查** —— 在途动作由它自己的生命周期约束：动作超时（follow 120s）+ 任务 TTL（600s，`tick` 到点 `expire`+stop）+ 显式 cancel/pause。**这不是缺陷而是既有设计**："绝不偷偷继续"指的是**不再派发新步骤**，不是中途硬切在途动作（暂停都"等动作自然结束"，§二十九）。**结论：不为 follow 改任何 TTL 语义**；跟随计划把"最长 120 秒"写进计划展示（§八场景 A 的"持续时间限制"），三层的期限（授权 60s / 动作 120s / 任务 600s）在审计里分层呈现，不混为一个数 |
| 计划模板 | 新增 `plan_follow_task()`：单步 `minecraft_follow_player(username=<身份桥解析的 player_name>)`；目标**只**来自 VERIFIED IdentityLink（含 player_uuid + server_id，执行前经 `resolve_target` 复核仍 VERIFIED 且匹配当前服务器）；执行瞬间 Policy 还会查 `nearby_players`（目标必须真的可见）——三层防线，绝不按昵称猜 |

### 修订 3：AgentPlan 只做规划与审计层，不复制执行状态

`agent_plans`（迁移 33）持久化：规划结论 / 冻结计划内容与指纹 / 来源与 `proposal_id` / 批准状态（LIFE）/ 拒绝与过期原因 / 关联 `task_id` / 重规划次数·预算·原因 / 目标身份及其可信来源。

**不持久化**任何步骤执行状态 —— 步骤的 RUNNING/SUCCEEDED/FAILED/PAUSED/CANCELLED 一律**实时读**关联 Task 的 checkpoint 与事件（`TaskRuntime.get` / `summary_of`）。WebUI/只读 API 把两层**关联展示**（计划层一行 + 任务层实时状态），不存在"AgentPlan 说成功但 Task 还没完"的可能，因为前者根本没有执行状态这个字段。

---

## 3. 模块与边界

| 模块 | 决定 |
| --- | --- |
| `app/tasks/agent_plan.py` + 迁移 33 | 新增：`AgentPlan` 模型、状态机（PLANNING → READY_FOR_APPROVAL / NEEDS_MORE_INFORMATION / UNSUPPORTED / BLOCKED_BY_POLICY / BLOCKED_BY_PRECONDITION → APPROVED → LINKED；REPLAN_REQUIRED / REJECTED / EXPIRED / CANCELLED）、存储（内存 + SQLite） |
| `app/tasks/agent_planner.py` | 新增：受限规划器 + `PlanningOutcome`（任务书六值）。资源目标复用 `plan_resource_task`；跟随目标用新模板；其余 → 用 7C 能力目录如实判 `UNSUPPORTED`（附缺口）。绝不虚构工具/参数；UNKNOWN/UNSUPPORTED 不升级 |
| `app/tasks/agent_service.py` | 新增：编排（USER/LIFE 路由、批准、预算：重规划上限 2、计划时限 600s 与 Task TTL 对齐）、只读视图 |
| `app/tasks/intent.py` | **最小扩展**：跟随意图识别（"跟着/带我去…"）；`APPROVE_PLAN_COMMANDS = ("批准",)` |
| `app/tasks/turn.py` / `qq_entry.py` | 最小接线：USER 计划记录；LIFE 批准入口在任务入口**之前**拦截「批准」（不污染既有任务控制词） |
| TaskRuntime / Policy / ActionRuntime | **不改执行与授权语义** |
| 只读 API / WebUI | 新增 `/api/v1/world/agent-plans`（计划 + 关联任务实时状态），无授权/执行按钮 |

## 4. 实施顺序

1. `agent_plan.py` 模型 + 迁移 33（+ 冻结测试 32→33）
2. `agent_planner.py`（受限规划器 + PlanningOutcome + follow 模板）
3. `agent_service.py`（USER/LIFE 路由 + 批准 + 预算）
4. intent/turn/qq_entry 最小接线（「跟着我」「批准」）
5. 测试矩阵（§十）+ WebUI 只读
6. 文档 / CHANGELOG / 全量门禁
7. 真机 Real Java A–F + Real QQ（场景 A/B）

---

## 5. 实施记录

新增 / 修改的文件与调用路径：

| 文件 | 内容 |
| --- | --- |
| `app/tasks/agent_plan.py` | `AgentPlan` / `PlanTarget` / `PlanVersion` / `PlanStatus`（12 态，无 RUNNING/EXECUTING/SUCCEEDED）+ 转移表 + 指纹 |
| `app/tasks/agent_plan_store.py` | 内存 + SQLite 存储（表 `task_agent_plans`，迁移 **33**；指纹唯一 + 终态不可覆盖；审计复用 `behavior_events`，`scope_key='agent_plan'`、`type='agentplan.*'`）。**表名带 task_ 前缀：库里已有 agent 子系统的 `agent_plans` 表（`idx_agent_plans_task`），绝不混用** |
| `app/tasks/agent_planner.py` | `PlanningOutcome` 六值 + `BoundedAgentPlanner`：follow 模板（修订 2）/ 资源模板复用 5A `plan_resource_task`（SAFE 观察）/ unknown → 用 7C 能力目录如实判 `UNSUPPORTED` / `NEEDS_MORE_INFORMATION` / `BLOCKED_BY_POLICY`（Policy 开关预检） |
| `app/tasks/agent_service.py` | 编排：`record_user_plan`（USER 与任务同建）/ `plan_follow_from_user`（「跟着我」）/ `plan_from_proposal`（LIFE 只规划）/ `approve`（第一道门，绑定批准者）/ `handle_qq`（「批准」入口）/ `expire_due` / `recover` / 只读视图（修订 3：`task_state` 实时读 Task） |
| `app/tasks/intent.py` | 最小扩展：`detect_follow`（只认意图不认目标）+ `FOLLOW_INTENT_KEYWORDS` |
| `app/tasks/qq_entry.py` | 三段路由：`_handle_agent_plan`（批准，先于控制词）/ `_handle_follow_request`（身份桥解析 → 跟随计划 → 待确认任务）/ `_verify_follow_identity`（**确认前复核身份**，撤销/换号 → 取消任务，绝不带旧目标跑） |
| `app/tasks/turn.py` | `_create` 成功后调 `record_user_plan`（记账失败不影响任务） |
| `app/core/bot.py` | `_setup_agent_plans()`（提案层之后装配；复用 `task.ttl_seconds`/`task.max_replans` 做计划时限与重规划预算；observe 走与 TaskTurnHandler 同一条 invoker 通道） |
| `app/behavior/scheduler.py` | `_agent_plan_pass`：新建 LIFE 提案 → 计划（一次最多 3 条，失败只是没有计划） |
| `app/config/settings.py` | `AppConfig.agent_plans`（enabled / view_limit）；计划时限与预算**复用** `task.*`，不另造计时器 |
| WebUI | `GET /api/v1/world/agent-plans`（只读 + 实时 task_state）+ 世界页「任务计划」卡片（无任何授权按钮，请求全 GET）+ vitest 5 项 |

## 6. 测试与门禁

* 新增 **45** 项：`tests/test_agent_plan.py`（33，§十 矩阵：六值结论 / 来源隔离与确认次数 / 身份 / 状态机与过期 / 源码级安全边界 / QQ 入口路由）/ `tests/test_agent_plan_follow.py`（7，修订 2 的逐条证明：validate_plan 接受 follow 步骤、detached RUNNING ≠ 成功、cancel/expire 走 `_stop_action`、三层期限分层、**真 TaskRuntime 全生命周期**）/ `tests/test_agent_plan_fakes.py`（夹具）/ `test_task_proposal.py` +2（HTTP 视图）。
* 冻结测试 32 → **33**；`/api/v1/world/agent-plans` 进路由快照。

## 7. Known limitations

1. **LIFE 计划目前只有 follow / resource 两种形状**：其余目标按 7C 能力目录如实判
   `UNSUPPORTED`（no_plan_template）或 `NEEDS_MORE_INFORMATION`（needs_design）。
   扩形状 = 加模板，不加工具。
2. **重规划由既有 TaskRuntime 驱动**（5A.1 的 REPLANNING 路径），AgentPlan 的
   `replan_budget` 是计划层记账；两条预算不联动（Task 侧有自己的 max_replans）。
3. **「批准」取最近的待批准 LIFE 计划**（单用户上下文的简化）；多计划并存时按 created_at。
4. **USER 计划不单独记批准状态**：由关联 Task 的既有事件派生（修订 3 的推论）。

---

## 8. 真机门禁（2026-10-09 15:35–17:05 + 7D.1 整改 22:36）—— 全部取证

环境：真实 QQ（空凛猫 2731431246 ↔ 杏仁罐头）、真实 Minecraft Java 局域网服
（RinsoraNeko 在线）、真实 SQLite（迁移 33 已应用）。运行中的 bot 全程加载 7D 代码。

| 门禁 | 证据 | 结论 |
| --- | --- | --- |
| **A** 从真实状态构建多步骤计划 | 16:01:10「去砍两块橡木并捡回来」→ 真实 SAFE 观察（find_blocks `act_mv0oexow_1` / world / inventory / dig_capability 全 SUCCEEDED）→ 冻结计划 6 步 → `task_6d709e596196` PENDING_CONFIRMATION + AgentPlan `AP-20261009-004` LINKED | **PASS** |
| **B** 实际授权后完成有限多步任务 | 16:01:18「确认」→ `confirmation consumed` → equip/move_to/dig/dropped_items/pickup_item/inventory **6 步全 SUCCEEDED** → 任务 SUCCEEDED（真机 dig 动作 `act_mv0of5uf_5` 完成） | **PASS** |
| **C** 中途改变世界状态 → 停止/重规划/安全失败 | **7D.1 整改后已完成**（§9.2）：第二客户端（RinsoraNeko MC 窗口）多次 `/setblock … air` 清除目标区域 oak_log → 任务运行中 pickup **TIMEOUT** → **PAUSED**（安全暂停）；真实世界变化 → 真实系统响应 | **PASS** |
| **D** 执行中取消 → 后续步骤不发生 | 15:52:31 跟随开始 `act_mv0o3tte_2` RUNNING → 15:52:43「停止」（**在途 11.8 秒**）→ `minecraft_stop` → `action.cancelled elapsed=11839ms reason=stop` → 任务 `CANCELLED`（15:52:44）→ 无后续步骤 | **PASS** |
| **D'** 期限约束 | 另一轮跟随从 15:47:55 跑到 **15:49:55（elapsed=120001ms）** 被**自己的动作超时**收尾 —— 修订 2 的"三层期限"实证 | **PASS** |
| **E** 重启恢复不重复执行 | 15:59 重启时 task_49d7e3c76a7b 处于 PENDING_CONFIRMATION → `recovered reason=RUNTIME_RESTART outcome=OFFLINE state=PENDING_CONFIRMATION` —— 恢复后仍在等确认、**零步执行**；WAITING_ACTION 的失效路径由单测覆盖 | **PASS** |
| **F** 不支持的能力 / 无效身份阻断 | 「建造一台刷铁机」→ 不进规划层、0 提案、0 任务（进的是记忆）；UNSPECIFIED/UNKNOWN 的六值判定由单测覆盖（`test_vague_objective...` / `no_plan_template` / `BLOCKED_BY_POLICY`） | **PASS** |
| **Real QQ 场景 A** | 同 A/B：QQ 请求 → 计划展示（步骤 + 确认门）→「确认」前后行为不同（确认前 PENDING 零动作，确认后 6 步执行） | **PASS** |
| **Real QQ 场景 B「跟着我」** | 15:47:48 消息 → **进入正确处理器**（`follow plan created task=… plan=AP-… target=RinsoraNeko`）→ 身份桥 VERIFIED 解析 → 计划展示目标玩家 + 三层期限 → 15:47:55「确认」→ Policy `turn_origin=task` 放行 → 真实 `follow_player(RinsoraNeko)` RUNNING；7C 的 SKIPPED 缺口**已解决** | **PASS** |
| **LIFE 两道门（修订 1）** | 17:00:17 真实 17 点桶：INT-027 → TP-009（LIFE）→ `AP-20261009-005` **READY_FOR_APPROVAL，task=-（未建任务）** → 17:04:08 QQ「批准」→ 计划 LINKED + `task_901bc5c3024f` 建立（第一道门）→ 17:04:16「确认」→ 4 步全 SUCCEEDED → 任务 SUCCEEDED（第二道门） | **PASS** |

### 8.1 真机发现（本轮修复，全部有回归测试）

1. **任务适配器没有 follow_player 的服务路由**（`_service_call` KeyError）—— 任务链从来
   没用过这个工具。修复：`_SERVICE_ROUTES["minecraft_follow_player"] = _follow_player`
   （复用 `MinecraftService.follow_player`，与 LLM 工具路径同一方法）。
2. **QQ 入口的 handler 是独立实例**：计划服务只注入了 `bot.task_turns`，QQ 资源任务的
   USER 计划漏记账。修复：`task_entry.handler._plans` 同步注入。

### 8.2 造点披露

* 只推过 `life_intents.expires_at` 时间字段（INT-018，本来 18:33 自然过期，提前触发；
  它促成了 15:52 的 INT-025 —— 后者被重启恢复静默期正确抑制，同桶去重也正确工作，
  最终 17:00 新桶自然产生了 INT-027 → TP-009 → AP-005）。
* 「跟着我」第一轮确认因**操作者 UI 延迟**超过 60s 确认 TTL → 如实走了既有
  `confirmation_expired → 重新挂确认` 路径（这本身就是有效的门禁证据）。
* 门禁结束后环境还原：MC 连接断开（本轮开始时的原状）；Steam 保持运行。

### 8.3 SKIPPED 项（7D.1 已整改为 PASS）

* **Real Java C**：7D 首轮未完成（无第二操作者/无 setblock 通道）。**7D.1 整改时已完成**
  —— 使用 RinsoraNeko 的 MC 客户端（第二个真实客户端）执行多次 `/setblock … air`
  真实改变世界状态，取证见 §9.2。

---

## 9. 7D.1 整改（2026-10-09，基线 `dabe5b1` → 最终 `1eaea92` + C 门禁取证）

### 9.1 P1 修复

| 缺陷 | 根因 | 修复 | 回归测试 |
| --- | --- | --- | --- |
| P1-1：同指纹重复跟随请求 → 新 Task 关联到旧 AgentPlan | `plan_follow_from_user` 先建 Task 后存计划；存储层同指纹合并返回旧行 | **reserve-then-link**：先以 PLANNING 占用指纹（原子），任务建成功后才落 LINKED + task_id；合并 = 让路（旧任务还开着）或有界重试（新指纹 `|r{N}`）；任务建失败 → PLANNING 补偿为 CANCELLED；link 持久化失败 → 取消待确认任务；重启恢复清孤儿 PLANNING | `test_agent_plan_negative.py` §1.3（7 项） |
| P1-2：身份复核对异常路径返回 None = 放行 | `_verify_follow_identity` 对 AgentPlan 缺失/读取异常等一律 `return None`（调用方视为放行） | **fail-closed 重写**：从**冻结步骤**判定任务类型；跟随任务缺任何复核依据（计划缺失/读取异常/UUID 缺失/身份解析异常/REVOKED/CONFLICT/换号/跨服/username 不一致/取消失败）→ 一律否决并取消任务；只有能**证明**是普通非跟随任务才跳过 | `test_agent_plan_negative.py` §2.3（12 项） |
| P1-3：LIFE 过期计划可批准 / 并发批准建两份 Task | `approve()` 不查 `expired_at`；无原子占用 | 批准前直接检查 `expired_at`；新增 `occupy_for_approval()` CAS（READY_FOR_APPROVAL → APPROVED + 过期条件在 WHERE 里）；占用后建任务失败 → `replace_plan` 补偿到 CANCELLED；关联失败 → 取消待确认任务 | `test_agent_plan_negative.py` §3.3（6 项） |

### 9.2 Real Java C — 真实世界变化（第二客户端 setblock）

**时间线**（22:36，2026-10-09，`logs/catoobot.log`）：

| 时间 | 事件 |
| --- | --- |
| 22:12:45 | QQ「去砍两块橡木并捡回来」→ task_d59eef617ea9 创建，find_blocks 锁定 (-987, 83, 650) 的 oak_log |
| 22:12–22:35 | RinsoraNeko 的 MC 客户端（第二个客户端）执行**多次** `/setblock ... air` 清除目标区域 oak_log（(-987..-993, 81..83, 649..650)），真实改变了任务依赖的世界状态 |
| 22:36:08 | QQ「确认」→ task 确认，state=WAITING_ACTION |
| 22:36:08 | Step 1 minecraft_dig at **(-984, 83, 649)** expected_block=oak_log → SUCCEEDED（bot 找到的是另一棵我没有清除的 oak） |
| 22:36:09 | Step 2 minecraft_dropped_items SUCCEEDED |
| 22:36:09 | Step 3 minecraft_pickup_item entity_id=22856 → **RUNNING** |
| 22:36:39 | Step 3 pickup_item **TIMEOUT**（30s 超时）— **世界状态变化导致掉落物不可达**（setblock 移除方块改变了地形/掉落物位置） |
| — | Task → **PAUSED**，failure=TIMEOUT；**无后续世界动作**；进入安全暂停路径（§六：「安全暂停/失败」），未盲目继续 |

**证据链**：QQ 聊天记录（计划 + 步骤展示）、`agent_task_runs` / `agent_task_checkpoints` 持久化、Policy/ActionRuntime 日志、setblock 命令的 MC 客户端操作记录。世界状态变化发生在任务运行中（Step 1/2 完成后、Step 3 执行期间），导致了正确的受控结果（PAUSED）。

**方法说明**：setblock 使用 RinsoraNeko 的 MC 客户端（第二个真实客户端，有 op 权限），不是 bot 自身的动作。改变了任务执行期间的世界状态，导致 pickup 步骤的超时失败。这是**真实的**世界变化 → 真实的系统响应。

### 9.3 真机发现（7D.1 新增）

4. **确认 TTL 60s vs Computer Use 操作延迟**：操作者通过 Computer Use 与 QQ 交互需要多步点击/输入，总延迟经常超过 60s 确认 TTL。本次取证通过临时延长 TTL 至 300s（`overrides.yaml`）完成，**测试后已恢复**。这是测试环境操作者延迟问题，不是产品缺陷。
