# CatooBot Minecraft Phase 8C — Bounded Goal Planning & Atomic-Tool Orchestration（有界目标规划与原子工具编排）

## §1 基线与工作区

| 项 | 值 |
| --- | --- |
| 起始基线（开工实测） | `9bf125c9e3d396950ee678060eab585006742b20`（== `origin/main`，277 commits，工作区干净） |
| 权威开发目录 | `E:\WorkSpace ZCode\CatooBot`（非 git 仓库，开发真源） |
| 发布镜像 | `E:\WorkSpace ZCode\CatooBot_github`（唯一 GitHub 发布来源） |
| 任务书归档 | `docs/specs/CatooBot Minecraft Phase 8C — Bounded Goal Planning & Atomic-Tool Orchestration.md`（27906 bytes / 266 行，SHA256 `31993322AE8FA35AECF1FF8CD1EB84D510B2768C456C2BE4C678F90854F69673`，与上传源逐字节一致） |

开工核对：任务书写的基线是 `9bf125c`，实测 `main`/`origin/main` 就是它，因此无需换基线（未做任何 reset）。

## §2 真实调用链（以源码为准）

### §2.1 Minecraft 聊天入口

```text
minecraft.chat 事件
  → MinecraftChatBridge.apply_event()（丢弃：未启用 / 空 / 自身发言 / 悄悄话 / 忙碌上限）
  → create_task(_reply)
  → TaskTurnHandler.handle(session_id, user_id=username, text)      ← 任务侧
      ├─ control_command：无当前任务 → 不接管（交回聊天）
      ├─ status_query：有当前任务 → 状态回复
      ├─ TaskIntentDetector.detect()：非任务 → 不接管（交回聊天）
      ├─ 有活动任务 → busy（不新建）
      └─ _create() → block_for(text) 认不出方块目标 → 不接管（交回聊天）
                    → 技能计划（Phase 7E）或 plan_resource_task()
                    → TaskRuntime.create_task(冻结计划)
                    → AgentPlanService.record_user_plan()（**仅记账**）
  → 若任务侧未接管：CharacterRuntime.respond() → 工具路由（Phase 8A 的 RouteTrace）
```

### §2.2 三类目标形状的规划与执行（QQ 入口 vs Minecraft 聊天入口）

| 形状 | 检测器 | 规划器 | QQ 入口 | Minecraft 聊天入口 |
| --- | --- | --- | --- | --- |
| `resource` | `TaskIntentDetector.detect()`（动作动词 + 多阶段标记 + 资源词，`intent.py:171`） | `plan_resource_task()`（`planner.py:134`）／`BoundedAgentPlanner._plan_resource`（`agent_planner.py:323`） | `TaskTurnHandler._create`（资源模板）+ `AgentPlanService.record_user_plan` 记账 | **已接线**（同上） |
| `follow` | `TaskIntentDetector.detect_follow()`（`intent.py:182`） | `BoundedAgentPlanner._plan_follow`（`agent_planner.py:167`） | `qq_entry._handle_follow_request`（`qq_entry.py:463`）→ `proposals.resolve_target()` → `AgentPlanService.plan_follow_from_user`（`agent_service.py:263`） | **未接线**（`detect_follow` 全仓只被 `qq_entry.py:362` 调用） |
| `explore` | `detect_shape()`（`agent_planner.py:97-108`） | `BoundedAgentPlanner._plan_explore`（`agent_planner.py:271`）→ `plan_exploration_task`（`planner.py:488`） | 仅经 LIFE 提案 `plan_from_proposal`（`agent_service.py:637`）；**用户聊天入口未接线** | **未接线** |

**计划层是否被 Minecraft 聊天入口使用？** 结论：**没有用于规划**。证据：
* `bot.py:1285` 建 `BoundedAgentPlanner`、`bot.py:1303` 建 `AgentPlanService`、`bot.py:1317` 存为 `self.agent_plans`；
* `bot.py:1321-1325` 把 service 注入 `self.task_turns._plans`（Minecraft 聊天入口的 handler）与 QQ 入口的 handler；
* 但 `turn.py` 里对 `_plans` 的唯一使用是 `_record_agent_plan()`（`turn.py:239-254`）—— 它把**已建好的**任务用 `record_user_plan()` 记一条 AgentPlan（`risk_summary={}`），**不是**规划调用；
* `bot.py:1341` 的装配注释也自述：计划层 与「**非 AgentPlan 的资源任务入口**（`TaskTurnHandler`）」是两个入口。

## §3 有证据的真实缺口

| 编号 | 缺口 | 证据 | 影响 |
| --- | --- | --- | --- |
| **G1** | Minecraft 聊天的 `follow` 意图**完全没有任务侧接线** | `detect_follow` 的唯一调用点 `qq_entry.py:362`；`TaskTurnHandler.handle`（`turn.py:201-237`）无 follow 分支；`detect()`（`intent.py:149-173`）要求「动作动词 + 多阶段标记 + 资源词」，follow 短语不满足 | 游戏内「跟着我」不会建立受管控的跟随任务（含冻结计划、期限、确认门、身份复核）；该意图可能落到单步工具路由，或只得到一句聊天 |
| **G2** | Minecraft 聊天的 `explore` 意图**没有用户入口** | `_plan_explore` 只由 `BoundedAgentPlanner.plan`（`agent_planner.py:161`）到达，而该 planner 的用户入口只有 `plan_follow_from_user`（follow 专用）与 LIFE 的 `plan_from_proposal` | 游戏内探索请求无法走既有有界探索规划（7F.1-7F.3 的能力对用户聊天不可达） |
| **G3** | 计划层消费（待确认计划的批准/过期）在 Minecraft 聊天入口缺失 | `AgentPlanService.handle_qq` 的唯一调用点 `qq_entry.py:444-461` | 由 LIFE 产生、或在 Minecraft 侧产生的待确认 AgentPlan，在游戏内无法被批准 |
| **G4** | 结构化路由证据未覆盖任务侧接管 | Phase 8A 的 `RouteTrace` 只在 `CharacterRuntime.respond` 与 `ToolOrchestrator` 记录；`TaskTurnHandler` 接管时不产生 `RouteTrace`（`turn.py` 无 `RouteTrace` 引用） | 「这条消息为什么没进任务/为什么进了任务」在游戏内不可从路由记录里看清 |

### §3.1 审计后确认「既有能力已满足」的部分（不改代码）

* **唯一接管**：`TaskTurnHandler.handle` 返回 `handled=False` 时聊天路径必然继续（`chat_bridge` 依据 outcome 决定是否交给 `CharacterRuntime`）；任务侧接管则不再进入 `ToolOrchestrator` —— 无重复副作用（`turn.py:201-237`）。
* **纯查询/长文本不误建任务**：`intent.py:153`（>200 字符）与 `intent.py:157-158`（`_QUERY_PATTERNS`）。
* **控制命令**：无当前任务时 `turn.py:216-218` 交回聊天；他人任务由 `_control` 的 owner/session 校验拒绝（`turn.py:258+`）。
* **资源任务的冻结计划与确认门**：`_create` → `create_task(plan=planned.plan)` → 既有 `validate_plan`/确认门/TaskRuntime（5A/5A.1/7D）；计划哈希与步骤授权均走既有实现。
* **MEDIUM 阻止**：`BoundedAgentPlanner` 的 `allow_medium`（`agent_planner.py:139`）来自 `bot.py:1288`；`TaskTurnHandler` 的资源模板只产出 SAFE/LOW 步骤（既有模板语义）。
* **执行对账**：`TaskRuntime` 的派发/终态/歧义语义（7F.4/7F.5.x）不变，本阶段不触碰。

## §4 本阶段的实现范围（最小接线，不复制规划器）

依据任务书 §3.1.2「凡是尚未进入有界任务/计划链的形状，必须复用现有模板/服务完成最小接线」，本阶段只做接线，不新增规划器/运行时/授权系统：

1. **给 Minecraft 聊天入口接上既有计划层**（`AgentPlanService`）：follow 走 `plan_follow_from_user`、explore 走同一个 `BoundedAgentPlanner.plan` 分派的既有核心，**不复制规划逻辑**；
2. **目标身份只来自可信来源**：游戏内说话者 → 在线玩家视图的权威 UUID → `IdentityStore.by_player` 的 VERIFIED link → 对手平台用户 → `TaskProposalService.resolve_target`；任一环节不成立 → 明确的 fail-closed 结果，**绝不从昵称/文本猜**；
3. **确认前复核身份**：与 QQ 入口一致，`confirm` 时复核 VERIFIED/服务器/在线（复用既有复核语义，不新建授权）；
4. **路由证据**：任务侧接管也产生 `RouteTrace`（`route_target=TASK`，带 `task_id` 与稳定 reason code），写入失败不影响执行；
5. **计划层消费**：Minecraft 聊天的待确认计划批准走既有 `AgentPlanService`（不新建状态）。

**明确非目标**（任务书 §3.2）：不新增工具/动作、不做树木批量采集与物品交付（8D-8F）、不改 `ACTION_RISK`/`allow_medium` 默认/`trusted_players`/`TurnOrigin` 派生/两道确认门/任务授权/计划哈希/Action Runtime 生命周期、不加表不加迁移。

## §5 实现（逐文件）

| 文件 | 改动 |
| --- | --- |
| `app/tasks/agent_service.py` | 把 `plan_follow_from_user` 的**主体**提取为形状无关的 `plan_goal_from_user(*, objective, user_id, session_id, target, source="qq", now=None)`（形状仍由既有 `BoundedAgentPlanner.plan` 决定；`source` 参数化，`create_task(source=...)` 不再硬编码 `qq`）；`plan_follow_from_user` 变成**逐字委托**的薄包装（签名/指纹/补偿路径/返回结构全部不变）。**修正**：`record_user_plan` 与 `_hash_of` 取 `TaskPlan.plan_hash`（旧代码读不存在的 `.hash`，导致记账 plan_hash 恒为空串）。 |
| `app/integrations/minecraft/chat_bridge.py` | 新增模块级 `_DETECTOR = TaskIntentDetector()`（与任务入口**同一套**确定性识别，不新增第二套意图判断、不额外调模型）；`_task_reply` 在任务入口未接管时改调 `_plan_reply`；新增 `_plan_reply`（只接管 follow/explore 两种已支持形状，先排除纯查询/控制命令/状态查询）、`_follow_target`（8B 身份解析 → 仅 VERIFIED 且对手平台为 QQ → 既有 `proposals.resolve_target`）、`_plan_reply_text`（按 action 如实回话 + 记 RouteTrace：`plan_created/TASK`、`plan_target_unverified/CLARIFY`、`plan_<action>/BLOCKED`）。 |
| `app/tasks/turn.py` | 把 `handle()` 的 `origin` 形参**真正传下去**（`_create(..., origin=...)` → `create_task(origin=...)`）：此前 `_create` 里硬编码 `TurnOrigin.USER.value`，形参形同虚设（非 USER 调用方会被记成 USER）。今日调用方都传 USER，行为不变。 |
| `tests/test_phase8c_bounded_orchestration.py` | 新增（16 项，见 §6）。 |

**接线位置的选择**：计划层接线放在 `MinecraftChatBridge._task_reply`（MC 专属），**不放进** `TaskTurnHandler`。理由：`TaskTurnHandler` 同时服务 QQ 入口（`qq_entry`），放进去会改变 QQ 的既有行为与既有 15+ 项回复断言；放在 MC 桥接层能保证**QQ 零影响**，且计划层与任务入口共用同一个 `bot.agent_plans` 服务（单一计划源）。

## §6 新增测试（16 项，确定性、不用 sleep、不依赖真实 LLM）

复用 `tests/test_minecraft_chat_bridge.py` 的 `ChatStack`：**角色运行时 / 工具运行时 / 策略门 / 工具链全是真的**，只有脚本化模型与假 `MinecraftService`；任务入口、计划层、身份解析用最小替身（身份替身返回**真实的** `IdentityContext`，避免把「接线对不对」测成「替身字段全不全」）。

| 组 | 用例（断言对象） |
| --- | --- |
| A 路由与唯一接管 | 已验证身份 + 「跟着我」→ 计划层恰好接一次、`source="minecraft"`、目标来自身份绑定、路由 `plan_created/TASK` + `task_id`、回复含「确认」；未核验身份 → **零调用**计划层 + `plan_target_unverified/CLARIFY` + 文案说清要绑定；探索请求 → 进计划层且**不伪造玩家目标**；纯查询 → 零调用且照常聊天（`CHAT_ONLY`）；闲聊 → 零调用；控制命令「确认」→ 零调用（任务入口接管）；状态查询 → 零调用；任务入口已接管（create）→ 计划层与角色侧都不动；任务入口 busy → 计划层不得叠计划 |
| B 计划结果如实呈现 | `plan_rejected` → 回复其真实文案 + `plan_plan_rejected/CLARIFY` + 空 `task_id`；计划层抛异常 → **仍由角色侧正常回答**（绝不静默丢消息）；`busy` → `plan_busy/BLOCKED` |
| C 会话与身份边界 | 传给计划层的是**说话者本人**的 MC 身份与会话（不冒用他人）；计划层未装配 → 照常聊天 |
| D 可观测与隐私 | 路由证据只有 `text_chars` 与稳定原因码，`routes.snapshot()` 里**不含聊天原文** |
| 记账回归（本次修正） | `_hash_of` 读 `TaskPlan.plan_hash`（旧代码读 `.hash` → 恒空串）；`_plan_payload` 保持原样 |

## §7 审计新发现（两路只读子代理审计；分「本阶段修」与「记录不改」）

### 7.1 本阶段修掉的（有证据、最小改动）

* **G9 `TurnOrigin` 形参失效**（`turn.py:207` 收 `origin`，`:505` 硬编码 `USER`）→ 已把来源一路传下去。
* **计划哈希记账恒空**（`agent_service.py:230` 与 `_hash_of:1128` 读不存在的 `.hash`）→ 已改取 `TaskPlan.plan_hash`，并加回归测试。这是「计划哈希/审计」不变量第一次真正落到记账层。

### 7.2 记录但**不改**的（附证据与理由）

| 发现 | 证据 | 为什么不改 |
| --- | --- |
| **G5 词表不一致导致误投 agent runtime** | `detect()` 的资源词表含「钻石」（`intent.py:64-85`），而 `_create` 的 `BLOCK_ALIASES` 没有（`turn.py:31-45`）→ 识别为任务却拿不到 block → `turn.py:474-477` 交回聊天 → `character/runtime.py:514-524` 的 `agent.run` **另建一个 agent 任务** | 修它要动两套既有词表/入口语义，属「新增支持类别」范围；本阶段只做接线，留给后续阶段按任务书 §3.2 处理 |
| **G6 同一事件有第二个消费者** | `plugins/minecraft/plugin.py:221-222` 无条件 `sandbox.submit_external` + `wakeup()`（不以任务是否接管为条件） | 这是 **Phase 4A 明确设计的双消费者**（世界认知/行为活动），去掉会破坏既有世界模型；本阶段只保证「任务/聊天二选一」不重复派发 |
| **G7 并发窗口（重复只读观察）** | `turn.py:213` 读 current → `_create` 内 await 观察（`:483-490`）→ `create_task`（`:501`），仅靠 `TaskBusy` 兜底 | 不产生重复**世界修改**（只可能重复一次 SAFE 观察），且 `create_task` 仍 fail-closed；加锁会动执行期既有并发语义，超出本阶段范围 |
| **G8 任务接管跳过角色侧关系/记忆记账** | `chat_bridge.py:159-161` 只写会话 + 回游戏，不调 `respond` → `character/runtime.py:156-157` 的关系/记忆不执行 | 涉及角色侧记账语义与既有测试断言；本阶段已在文档记录该不对称 |
| **chat 资源计划绕开规划期策略闸** | `turn.py:485` 直接调 `plan_resource_task`，不经过 `BoundedAgentPlanner` 的 `_policy_blocked`（`agent_planner.py:358-371`）→ MEDIUM 步骤会一路到确认，直到**执行期**被 `agent.py:420-427` 拒绝 | **安全性不受影响**（执行期 fail-closed，绝不执行 MEDIUM）；改规划期闸会改变既有资源任务行为。本阶段**新增的** follow/explore 接线走 `BoundedAgentPlanner.plan`，**天然带规划期策略闸** |
| **LIFE 非 READY 结论无用户反馈 + 聊天把失败回报成 created** | `agent_service.py:719` 一律 `action="planned"`；`turn.py:524-531` 一律 `action="created"`（`runtime.py:685-691` 实际返回 FAILED） | 改既有回复语义会影响既有 15+ 项断言与 QQ 路径；本阶段**新增路径**已如实区分 `created/plan_rejected/busy`（见 §6 B 组） |

### 7.3 审计确认「既有能力已满足」（不改）

所有变更世界的步骤都过：计划校验（`runtime.py:685`）→ 确认门（`:720-727`，必须真实 USER 回合 + 同 owner/session）→ 步骤级授权（`:870-871/:879-889`，工具 + 参数指纹 + plan_hash）→ Policy（`agent.py:428-439`）→ AgentBridge（`agent.py:802-818`）→ Service/Action Runtime；SAFE 步骤免授权但同样过 Policy/Bridge；执行对账按 `task_id + plan_hash + step_id + 代次 + action_id + 实例`严格匹配，detached/结果未知一律 fail-closed 暂停且**不续派下一步**（7F.4/7F.5.x 语义未动）。

## §8 门禁状态（截至本记录；**本阶段尚未裁决**）

| 门禁 | 状态 |
| --- | --- |
| `ruff check .` | **通过**（All checks passed） |
| `ruff format --check .` | **通过**（674 files） |
| `mypy app` | **通过**（Success，337 source files） |
| `pytest tests/test_phase8c_bounded_orchestration.py -q` | **16 passed** |
| 相关套件（chat bridge / 8A 路由 / 8B 身份 / agent plan 全家 / mc agent e2e） | **195 passed** |
| 全量 `pytest tests -q` | **通过：3921 passed**（591.40s；= 8B Follow-up 基线 3905 + 本次新增 16，精确吻合） |
| 迁移演练（无 schema 变更） | **通过**：`tables [83,83]`、`added_tables []`、`lost_rows {}`、版本与完整性无变化（`ok:false` = 已最新版的预期 no-op） |
| 真机 `scripts/bounded_orchestration_smoke_real.py` 六场景 | **待实现并执行**（不得以 mock/skip 替代） |
| 镜像发布 + CI 两个 job | **未执行** |

## §9 真实 Java 服务器门禁（`scripts/bounded_orchestration_smoke_real.py`，本地专用不发布）

环境：**真实 Java 1.21.1 + Fabric**（测试服 25565）+ **真实 Node/Mineflayer** 运行时（本地桥接改用空闲端口，不与用户 Bot 抢 25580）；临时 SQLite（不碰 live DB）；**真实第二个 Mineflayer 客户端** `IdSmoke8C` 在游戏里说话、可被观察；`allow_medium` 强制 `False`。

**最终一轮：6/6 PASS，0 断言失败，exit 0。**

| 场景 | 维度 | 结果与真实证据 |
| --- | --- | --- |
| 6 | 普通聊天与 SAFE 查询 | **PASS**：「嗯嗯」→ `no_tool_candidates`、无任务；「附近有什么方块？」→ `model_no_tool_call`、无任务；零步骤 |
| 3 | 目标不足 fail-closed / VERIFIED 成立 | **PASS**：未绑定「跟着我」→ `plan_target_unverified`(CLARIFY)、不建任务、零步骤、如实提示先绑定；用**真实权威 UUID**（尾号 `11f9`）写入 VERIFIED 绑定后同一请求 → `plan_created`/TASK、`PENDING_CONFIRMATION`、确认前零步骤、取消后零步骤 |
| 1 | 真实多步目标成功路径 | **PASS**：游戏内「去附近转转探索一下」→ 计划 3 步（`move_to`/`look_at`/`world`）→ 确认门 → 「确认」→ 既有 TaskRuntime **受授权执行** → **3 个真实动作步骤** → `SUCCEEDED` + 真实到达验证 `position_within.distance=0.98` |
| 2 | 确认前零执行 + 未确认即取消 | **PASS**：第二个探索计划停在 `PENDING_CONFIRMATION`、确认前零世界步骤；「取消任务」→ `CANCELLED`、全程零世界步骤（没有暗中派发） |
| 4 | MEDIUM 被既有策略门拒绝 | **PASS**：资源请求进入任务侧，计划风险 `['LOW','MEDIUM','SAFE','MEDIUM','SAFE']`；确认后 `PAUSED`；**受策略限制的步骤无一成功、且无 `action_id`**（执行期被既有门拦下）；没有任何动作真的修改世界 |
| 5 | 执行中取消不续派 | **PASS**：「确认」后取消 → `CANCELLED`；未启动的 `step_2`/`step_3` 保持 `PENDING`；取消后不再派发后续步骤 |

### §9.1 首次失败与重跑（如实记录；**未放宽任何断言/阈值**）

* 场景 1 前两轮 **FAIL**：到达验证 `distance=2.09` > 计划半径 `2.0`（**7F.1 起记录在案的 pathfinder 位置相关精度抖动**；产品**正确地**把任务判 `FAILED`、绝不谎称成功）→ 保留首次结果、不改阈值、重跑 → 第三轮起点不同、`distance=0.98` → **PASS**。
* 场景 4 首轮 FAIL 属**门禁断言写宽**（把 LOW 的合法 `move_to` 成功也算进「MEDIUM 不得成功」）→ 改为只对受策略限制的风险等级断言 → PASS。
* 场景 1 首轮另有两处**门禁探针缺陷**：`runtime.current()` 对已结束任务返回 `None`（改为按 `task_id` 用 `runtime.get()`）、spy 包错方法名（真实入口是 `invoke_task_step`，不是 `invoke_step`）→ 修脚本后 PASS。
* 门禁首轮还撞过一次 **`EADDRINUSE 25580`**（前一轮被中断留下的 runtime）→ 门禁改为**自负空闲端口**，不替用户杀进程。

### §9.2 门禁过程中发现并修掉的**产品缺陷**（本轮唯一产品侧修复）

`BoundedAgentPlanner` 的 explore / resource 形状返回的 `planned.observations` 是 **`Observation` 对象**列表，而 `TaskRuntime.create_task` 要求 **payload 字典**列表 → 新入口一传即 `TypeError`（资源形状从不把它传下去、跟随形状恰好是字典，所以这个类型错配长期未暴露）。修法：在 `plan_goal_from_user` 的**边界**上归一化（`dict(item)` / `item.to_payload()`），**不改规划器既有返回类型**，其他消费者零影响。

## §10 门禁与不变量核验

| 门禁 | 结果 |
| --- | --- |
| `pytest tests/test_phase8c_bounded_orchestration.py -q` | **16 passed** |
| 相关套件（chat bridge / 8A 路由 / 8B 身份 / agent plan 全家 / mc agent e2e） | **195 passed** |
| 全量 `pytest tests -q` | 见 §10.1（最终数字） |
| `ruff check .` | All checks passed |
| `ruff format --check .` | **675 files** already formatted |
| `mypy app` | Success: no issues found in **337** source files |
| 迁移演练（副本） | `version [36,36]`、`integrity [ok,ok]`、`tables [83,83]`、`added_tables []`、`lost_rows {}`、`ok:false`（预期 no-op）→ **无新表无新迁移** |
| 真实 Java 六场景 | **6/6 PASS**（见 §9） |
| 镜像 / CI | 见 §10.1 |

### §10.1 本地全量与 CI（发布后补记）

* 全量 `pytest tests -q`：**3921 passed**（706.98s，exit 0；= 8B Follow-up 基线 **3905** + 本阶段新增 **16**，精确吻合）。
* 发布：`Phase 8C: bounded goal planning & atomic-tool orchestration` → **`e4b591f739332aceda828bc7cd3727d300104924`**（278 commits）；镜像 == `origin/main` == 该 SHA；工作区干净；**源工作区 vs 镜像内容差异 0**（`sync_github.py` 二次运行报 `mirror already up to date`）。
* **CI run `38348210461` attempt 1：`lint · format · types · tests` success / `webui · typecheck · tests · build` success。** CI 上 pytest **3918 passed, 3 skipped in 320.62s**（= 本地 3921 − 3 条既有平台条件性 skip）；Browser E2E **7 passed**；`minecraft_runtime` find-blocks-test ALL CHECKS PASSED + e2e `bridge auth boundary + runtime instance ✓` + e2e ALL CHECKS PASSED。
* 实际 SKIPPED（全部为既有平台/假服务器限制，与本阶段无关）：3 条 pytest 平台 skip；e2e `equip / inventory_move STOP`、`container STOP`、`craft 成功路径`、`craft STOP`、`3×3 craft 成功路径`、`dropped_items / pickup 成功路径`。
* live DB 未被本阶段写入（真机门禁用临时 SQLite）：`quick_check ok`、`max_version 36`、84 张表（83 业务 + `sqlite_sequence`）、外键违规 0。

### §10.2 安全不变量核验

`ACTION_RISK`（`app/integrations/minecraft/agent.py`）、19 个既有工具、`allow_medium` 默认值（`app/config/settings.py`）、`trusted_players`、`TurnOrigin` 派生、两道确认门、`TaskAuthorization`/`TaskStepAuthorization`、计划哈希/参数指纹、`TaskRuntime`/Action Runtime 状态机**均未被本阶段改变**；未新增工具/动作/表/迁移；未绕过任何既有授权或生命周期；未触碰 `.env`、真实 `config/config.yaml`、`data/`、角色 Bible 私有源。

## §11 已知限制（只列有证据的）

* **explore 到达半径依赖 pathfinder 精度**：计划半径 2.0，实测最终靠近距离在 0.98–2.09 之间波动；超出半径时任务**按设计**判失败（fail-closed），不影响安全，但会让真机门禁偶发 FAIL（需重跑）。
* **每会话一个任务**：会话里存在非终态任务（含 `PAUSED`）时，新的多步目标会得到 `busy`（既有设计）；门禁场景之间必须显式清场。
* **`TurnOrigin` 形参修正**不改今日行为（所有调用方都传 `USER`），但它消除了「非 USER 调用方被记成 USER」的潜在误标。
* §7.2 列出的 7 项既有缺口**未在本阶段修**（附证据与理由），留给后续阶段。

## §12 裁决

**PASS。** 六项必需真机维度**全部取得真实环境证据**：真实多步目标经游戏内聊天入口 → 既有有界规划 → 确认门 → 受授权 TaskRuntime 执行 → 可验证终态（到达 `0.98`）；确认前零执行、未确认即取消；目标不足 fail-closed（并以真实权威 UUID 证明 VERIFIED 后同一请求成立）；MEDIUM 被既有策略门阻止且无 `action_id`；执行中取消不续派；闲聊/SAFE 查询不误接管。未新增能力/工具/表/迁移，未扩大授权，未放宽任何断言（失败与重跑如实记录于 §9.1）。

## §13 Follow-up 修复（规划失败不得回退到工具执行）

**独立提交，不改写 §1–§12 的历史记录。** 任务书归档 `docs/specs/CatooBot Minecraft Phase 8C Follow-up — Planning Failure Must Not Fall Through to Tool Execution.md`（2143 bytes / 27 行，SHA256 `4D8143B6DB64DBB5E5A000292658479921454DAFCEB2A8B0C50037A9110D6EE5`）。

### §13.1 已确认的缺陷（改动前）

`MinecraftChatBridge._plan_reply` 把「计划服务是否装配」的判定放在**形状识别之前**：

```text
241-244:  plans = getattr(bot, "agent_plans", None)
          plan_goal = getattr(plans, "plan_goal_from_user", None)
          if not callable(plan_goal): return None   # 任何消息都直接交回角色回合
277-279:  except Exception: … return None           # 计划层抛异常同样交回
```

`None` 的语义是「这句话不归计划层管」→ `_task_reply` 返回 `None` → `CharacterRuntime.respond()` → 可能进入 `ToolOrchestrator` 的**单步工具链**。后果：一个**多步骤**目标（follow/explore）在计划层不可用或出错时，会被降级成**一次无约束的原子工具调用** —— 正是任务书 §3.2/§4.3 明令禁止的形态。

### §13.2 修复（最小、语义收紧）

* `app/integrations/minecraft/chat_bridge.py`：
  - **顺序调整**：先做形状识别（query_only / 控制命令 / 状态查询排除 → follow/explore 识别），**之后**才检查计划服务可用性。只有**非**受支持形状才返回 `None`（普通聊天 / 纯查询 / 控制命令语义不变）。
  - 计划服务缺失 → 明确失败答复 + `BLOCKED` 路由 `plan_service_unavailable`（**返回字符串**，不是 `None`）。
  - 计划层抛异常 → 明确失败答复 + `BLOCKED` 路由 `plan_service_error`（**返回字符串**）。
  - 新增 `_plan_failure(...)`：统一的「已识别多步目标失败」出口（记 BLOCKED + 返回可见文案），docstring 写明**为什么不能返回 None**。
* **孤儿任务补偿：无需新代码** —— 既有 `AgentPlanService` 已在 create/link 各失败点补偿（`_compensate_reserved_plan` / `_compensate_orphan_task`，见 `agent_service.py:387/402/421/478`；`_link_reserved_plan` 内部捕获存储故障），`plan_goal_from_user` 的通用化**保留**了这些路径。证据：既有回归测试 `tests/test_agent_plan_negative.py::test_link_persistence_failure_compensates_the_new_task` 与 `::test_crash_between_reserve_and_link_recovers_as_orphan`（后者断言 `summary["orphans"] == 1` 被对账/补偿）在本阶段全绿。

### §13.3 测试（新增/改写 3 项；该文件共 17 项）

| 用例 | 断言 |
| --- | --- |
| `test_plan_layer_error_never_falls_through_to_the_tool_path` | 计划层抛异常 → `BLOCKED`/`plan_service_error` + 明确文案；**角色侧回答没有出现**（`"我在的。" not in replies`）；`tools.executor.traces == []`（单步工具链没被走到） |
| `test_missing_plan_layer_keeps_plain_chat_working` | 计划层没装配 + **普通闲聊** → 照常由角色回答（前三类语义不变） |
| `test_supported_goal_without_plan_service_is_blocked` | 计划层没装配 + **follow 与 explore 两种形状** → 都得到 `BLOCKED`/`plan_service_unavailable`、非角色回答、零工具执行 |

**改写说明（诚实记录）**：原有的 `test_plan_layer_failure_falls_back_to_chat` 与 `test_missing_plan_layer_keeps_chat_working` **正是把缺陷行为写成了断言**（失败也要回落到角色回合）；本阶段按新契约收紧为「必须明确失败 + 不得回落」，这是**收紧**而不是放宽断言。

### §13.4 门禁与不变量

| 项 | 结果 |
| --- | --- |
| `pytest tests/test_phase8c_bounded_orchestration.py -q` | **17 passed** |
| 相关套件（agent plan 全家 + chat bridge + 8A 路由 + 8B 身份） | **167 passed**（含上面两个既有补偿测试） |
| 全量 `pytest tests -q` | **3922 passed**（725.15s；= 8C 基线 3921 + 本阶段净增 1） |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 已格式化（无待改文件） |
| `mypy app` | Success（337 source files） |
| 安全不变量 | `ACTION_RISK`、19 工具、`allow_medium` 默认、`trusted_players`、TurnOrigin 派生、两道确认门、TaskAuthorization/TaskStepAuthorization、Policy/AgentBridge/ActionRuntime **均未改**；无新工具/动作/表/迁移 |

### §13.5 裁决

**PASS**：规划失败不再回退到另一条工具执行路径（有「角色侧回答未出现 + 零工具执行」的真实证据），既有补偿路径被回归测试证明不留可执行孤儿任务，且前三类输入语义不变。