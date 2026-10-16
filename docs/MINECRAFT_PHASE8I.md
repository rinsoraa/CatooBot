# Phase 8I — 合成目标的资源任务交接与验证后续规划

> 归档任务书：`docs/specs/CatooBot Minecraft Phase 8I — Craft Goal Resource Handoff & Verified Continuation.md`
> （18212 bytes，SHA256 `DAFA8A8B79971BBBE716969FF98DE544ABA32C3BEF9366C23BFCFED7BB539C5A`，403 行，逐字节归档核验）
> 开工基线：`5117f70f958f6f750b16bd1e1e71fffbc68fc2a5`（= Phase 8H Follow-up 2 收尾）

## 1. 目标与范围

合成计划因**缺料**无法完成时：可解释缺料诊断（H1）→ 在**真实观察确认存在目标**且该材料属于**既有资源任务能力**时
提出资源获取提案（H2）→ 用户明确接受后走**既有** AgentPlan 批准 + 执行确认两道门（H3）→ 资源任务结束后按**现在的
真实背包**重新规划合成（H4）→ 全程只有**一次**交接（H5）。

本阶段**不新增**任何工具、权限、执行器、数据库 schema/字段/迁移，也不建立平行任务状态机。

## 2. 现状审计（P0，真实代码）

* `app/tasks/planner.py:137` `plan_resource_task(objective, *, observe, block_name, drop_item=None, max_distance=16,
  equip_tool=None, inventory=None) -> PlannedTask` —— 既有确定性资源模板「找 →（需要就）换工具 → 走 → 挖 → 捡 → 复核」；
  只用 SAFE 观察；**在 max_distance 内找不到目标即 `raise ObservationFailed`** —— 既有能力**自带真实观察校验**（H2 的证据来源）。
* `app/tasks/intent.py:108/112/122` —— `WOOD_MAX_TARGETS = 2`（H5 硬上限）、`WOOD_SPECIES`（6 种原木 → `minecraft:<x>_log`）、
  `WOOD_UNSUPPORTED`（竹子/菌柄/去皮/木板/树叶/木头方块 + 稳定原因码）。
* `app/integrations/minecraft/chat_bridge.py` —— `_wood_reply`（既有有界木材入口，`shape_hint="wood"`）、`_craft_turn`
  （`shape_hint="craft"`）、`_craft_failure_hint`（缺料回话挂点）；`AgentService.observe` 是既有观察入口（直接复用，不加新管道）。
* 规划层失败 detail 已是稳定原因码形态（如 `insufficient_material:oak_log(缺 2)`），本阶段只读它。

## 3. H1/H2：`app/tasks/resource_handoff.py`（新增）

* `craft_gap_from_detail(detail) -> CraftGap`：按**稳定原因码**分类；**只有** `insufficient_material` 才解析缺什么缺多少；
  `crafting_table_required` / `inventory_unavailable` / `no_recipe` / `search_budget_exceeded` **绝不**被翻译成「材料不足」。
* `HANDOFF_BLOCKS` **只**由 `WOOD_SPECIES` 派生；不支持物品 `propose_handoff` 直接返回 None 且**连观察都不做**。
* `async propose_handoff(*, detail, observe, max_distance=16, craft_goal="")`：必须拿到**真实 `minecraft_find_blocks` 观察**
  且 `ok != False` 且有 ≥1 候选才产出提案；`amount = max(1, min(requested, WOOD_MAX_TARGETS))` 并披露夹取；
  提案文本覆盖任务书 §4.1 全部要素（缺什么缺多少、为什么不可行、真实观察来源、预计既有步骤、会改变世界、未满足前置条件，
  以及「这只是提案、确认前不会有采集动作」）。

## 4. H3/H4/H5：`app/integrations/minecraft/chat_bridge.py`

* **H2 回话**：`_craft_turn` 在 `action != "created"` 时调用 `_craft_handoff(...)` 把提案接到缺料回话后；确认不到目标时给
  `_no_target_note(...)`（「真实观察不到可处理目标，所以建立不了可执行的资源计划」）；不支持物品给准确原因码说明。
* **H3 接受**：待决交接放内存 `_pending_handoffs[(username, session_id)]`（TTL 600s）；当决策层把用户回复判为 `task_proposal` 时，
  `_accept_pending_handoff(...)` 把交接转成**既有木材入口**能解析的规范说法（如 `砍橡木原木 2 根`），再走 **既有** `_wood_reply`
  （`shape_hint="wood"`）→ 既有 AgentPlan 批准 + 执行确认两道门；**合成计划的授权绝不用来授权采集**；接受是**一次性**的（H5）。
* **H4 重新规划**：`_remember_craft_goal(...)` 记住原始合成目标；资源任务之后的下一次合成请求**总是**由规划器按**现在的真实背包**
  重新算配方/预算/工作台/逐步后置条件（绝不复用旧 DAG），并明确回话「这是**新**计划，需要重新确认」。
* 进程重启后 `_pending_handoffs` 丢失 → 不自动恢复旧授权，用户需重新确认（任务书 §6）。

## 5. 测试

* `tests/test_phase8i_resource_handoff.py`（9 条）：缺料解析；四种非材料失败不触发交接且**不观察**；未确认目标不提案；
  证据 + 硬上限夹取 + 观察调用形态；不支持物品拒绝；提案文本要素。
* `tests/test_phase8i_bridge_handoff.py`（13 条）：`_gap_detail_of` 读稳定原因码；提案只在真实观察到目标时留下待决交接；
  无目标/不支持物品的诚实回话；非材料失败不提案；规范说法可被既有木材入口解析；**接受 → 既有计划入口 + 仍需确认 + 一次性**；
  无待决时落回原路由；过期方案要求重新观察；原始合成目标被记住。

## 6. 尚未完成 / 未验证（如实记录）

* **真实服务器门禁场景 A–D 未执行**（需要停可见窗口 Bot + 第二个真实 Mineflayer 客户端跑完整「缺料 → 提案 → 接受 →
  资源任务 → 重新规划 → 合成」链路）。按任务书裁决规则，本阶段因此判 **PARTIAL**。
* 未新增数据库 schema/字段/迁移；未触及受保护区域（风险映射、Policy、授权、确认门、Action Runtime）。
* 真机门禁已在 Follow-up 执行（见 §7）：H1/H2 提案与安全拒绝已取得真机证据，完整合成闭环与 C/D 场景仍缺证据。

## 7. Follow-up：真实服务器门禁（本地脚本，不发布）

脚本 `scripts/phase8i_handoff_smoke_real.py`：真实 Java 1.21.1 + 真实 `MinecraftService` + 真实 Provider +
**第二个真实 Mineflayer 客户端** `IdCraft8G` 在游戏内发自然语言（自动化客户端，不是真人）。

### 7.1 真机门禁暴露并修复的两条**真实产品缺陷**（否则 H1/H2 在生产上不可达）

1. `app/tasks/craft_plan.py` 的记忆化缓存只存原因码：写入 `impossible[(item, amount)] = CRAFT_PLAN_MATERIAL`、
   复述时 `raise CraftPlanError(memo, item)` → 丢掉 `(缺 N)`。真机实测 detail 变成 `insufficient_material:acacia_log`，
   `craft_gap_from_detail` 解析不出缺什么缺多少 → **资源交接提案永不触发**。修复：缓存 `(reason, detail)` 两元组并原样复述
   （原因码、计划结果、fail-closed 语义全部不变）。
2. `app/integrations/minecraft/chat_bridge.py` 的 `_gap_detail_of` 只读 `out["checks"][-1]["detail"]`，而
   `AgentService.plan_goal_from_user` 的真实失败形态是 `{"action": "plan_rejected",
   "reply": "现在排不了这件事：<reason>:<detail>", "outcome": ..., "plan": None, "record": None}` —— **没有 checks 字段**
   → detail 永远取空 → H2 提案在真机上永远不出现。修复：先读 checks（兼容既有），再从真实 reply 里按**稳定原因码前缀**取 detail。

回归测试：`tests/test_phase8i_craft_gap_amount.py`（3 条，真实 `build_craft_plan` 触发 memo 复述路径）
+ `tests/test_phase8i_bridge_handoff.py` 新增 2 条真实形态用例（`REAL_REJECTED` 夹具）。既有测试零删除、断言零放宽。

### 7.2 已取得的真机证据（attempt 4，`CatooBot_backups/_phase8i_gate4.log`）

* A1：真实规划结论 `insufficient_material:oak_log(缺 1)`（修复后带真实数量）。
* A2：**H2 提案在真机首次完整触发** —— 第二个真实客户端在游戏内说「给我做 4 块橡木木板」→ 路由 `handoff_proposed`；
  回话含「这只是一个提案」「资源目标来自真实观察：附近确认到可处理的 oak_log（真实查询到 8 个候选，最近 1.41 格）」
  「确认之前不会有任何采集动作」「预计步骤：… 挖掘目标方块（既有 minecraft_dig）…」；提案阶段**零任务、零工具轨迹、零库存变化**。
* A3：用户明确接受 → 建立**真实资源任务**（user=IdCraft8G、session=minecraft:127.0.0.1:25565:IdCraft8G、停既有确认门、
  计划含真实挖掘步骤且无合成步骤、**确认前零 action_id**）。
* A5（负向路径）：当真实观察半径 16 内**没有**可处理橡木时，回话如实拒绝 ——「缺的 oak_log 理论上可以采（oak_log），
  但罐头在附近真实观察不到可处理的目标，所以现在建立不了可执行的资源计划（不会假设附近一定有）」（`_no_target_note`）。
* 场景 B：真实观察不到目标（acacia_log）→ **不产出提案、不建任务、零执行、零库存变化**（attempt 4/5 均 PASS）。

### 7.3 未完成（如实记录，因此本阶段裁定 PARTIAL）

* 场景 A 的**完整闭环**（提案 → 采集成功 → 重新规划 → 新确认 → 合成 → 独立核验产物与净耗）未达成：既有 8D 采集链
  在本轮世界条件下未收敛（attempt 4 停在 `REPLANNING`、原木净增 0；attempt 5 时半径 16 内已无可观察橡木 → 产品走
  **如实拒绝**分支，行为正确但不构成闭环证据）。
* 场景 C（一轮后仍不足）、D（中途取消 / 外部改料）未取得真机完整证据。
* 因此真机门禁**不判 PASS**：核心实现与两条真实缺陷修复已交付，真机证据覆盖「提案 + 安全拒绝 + 接受建任务」，
  但「至少一个真实合成闭环」与 C/D 场景缺证据。

### 7.4 第三条真实缺陷：资源提案被**静默接受**（违背 H3）与最小修复

* 现象（attempt 6，`_phase8i_gate6.log`）：场景 C4 的**重复**合成请求先产出 `handoff_proposed`、随后又 `plan_created`
  （`task_9324aa95caf5`）；场景 D1 的合成请求也直接 `plan_created`（`task_31f9f57c5719`）——**没人接受，却建了采集任务**。
* 根因：`_route_turn` 的 `TASK_PROPOSAL` 分支**先**调用 `_accept_pending_handoff`，只要存在待决提案就把它当作接受；
  而决策层对「给我做 64 块橡木木板」与「好，去砍橡木原木」**都**判 `task_proposal`。于是重复或无关的合成请求会在
  无人接受时把上一轮提案变成任务，违背 §4「提案≠已建任务」与 H3「资源获取必须用户明确接受」。
* 修复（`app/integrations/minecraft/chat_bridge.py`）：新增 `_is_new_craft_request(message)`（`parse_craft_request` 命中、
  `problem` 为空且 `item` 非空）；该分支先解析 `craft`，若这句话**本身就是一次可执行的新合成请求** → 作废上一轮提案
  （`_pending_handoffs.pop`）并按新请求重新评估；只有**不是**合成请求时（如「好，去砍橡木原木」「可以」）才尝试接受。
* 测试：`tests/test_phase8i_handoff_acceptance_rule.py`（5 条）固定该判定 —— 有效/重复合成请求都不是接受，纯接受语不是合成请求。

### 7.5 采集链未收敛的真实根因：冻结目标的**可达性**（环境夹具说明，不放宽阈值）

* 现象（attempt 6）：场景 A4 的 `step_1 minecraft_move_to` **FAILED**（`act_mvakseat_s`），后续步骤停在 `PENDING`，任务 `PAUSED`；
  冻结目标 `(-272, 64, -133)` 距罐头 ≈ **6.8 格**。
* 定性：既有导航的到达判定是 `goal_near` **半径语义**（既有安全行为），真实世界里路径走不通时任务按既有语义停在非终态 ——
  产品行为正确，**不放宽任何距离/超时/阈值**。挖矿步骤本身在可达时可用（attempt 6 场景 C 已真实采到 2 个原木、`verification.ok=True`）。
* 门禁做法：新增环境夹具 `_stand_near_target` —— 由 **op 客户端**（`CatodayoMate`）用 `/tp` 把自动化客户端送到冻结目标旁
  （等价于真人自己走过去），**与产品执行链完全分离**，每次 `_run_resource` 前执行并打印真实坐标作为证据。
  attempt 7 的 `/tp` 回执出现 `PartialReadError`（op 客户端读包抖动），但真实自身坐标 `{'x': -277.5, 'y': 65.0, 'z': -135.5}` 证明传送生效。
* **仍未验证的边界（如实记录）**：本闭环**不**证明 6.8 格以外的导航能走通 —— 那属于既有 8D 导航能力的问题域，不在 8I 范围；
  夹具不改变产品任何距离/路径判定，产品自身**没有**任何放置、破坏或传送能力。

### 7.6 最终真机门禁（attempt 7，`_phase8i_gate7.log`）：**4/4 PASS**

* 总判定：`通过=4 失败=0 BLOCKED=0` → `REAL SERVER: PASS`。
* 场景 A **完整闭环**（§7.3 未完成项已达成）：A1 真实规划 `insufficient_material:oak_log(缺 1)`；A2 **真实第二个** Mineflayer 客户端
  在游戏内请求 → `handoff_proposed`（真实观察 8 个候选、最近 2.24 格）+ 提案阶段零任务/零轨迹/零库存变化；A3 明确接受 →
  真实资源任务 `task_2662bdaf41a3`（`PENDING_CONFIRMATION`、`user=IdCraft8G`、含挖掘步骤与**无**合成步骤、确认前零 `action_id`）；
  A4 `SUCCEEDED` → **独立重读背包 `oak_log 0→1`**；A5 重新规划 → **新**任务 `task_f066819a14ce`（不复用旧资源任务）、
  冻结 `oak_planks*4=oak_log*1`、预期产出 `{'oak_planks': 4}` / 预期耗料 `{'oak_log': 1}`、确认前零 `action_id`；
  A6 `SUCCEEDED` → **独立核验 `oak_planks 0→4`、`oak_log 1→0`**，运行时逐步后置验证 `['step_1']` 全过。
* 场景 B（无可确认目标 → 拒绝且零执行）、场景 C（一轮后仍不足 → 如实报缺口、不自作主张开第二轮）、
  场景 D（D1 计划阶段取消 `CANCELLED`、零执行、库存不变、不复活不迟到重试；D2 外部改料后不得宣告成功）全部 PASS。
* **DEVIATION（如实）**：场景 C/D 期间第二个真实 Mineflayer 客户端不可用 → 回退**真实聊天桥事件注入**
  （同一 `MinecraftChatBridge` → 同一真实决策层 → 同一真实任务链），脚本逐条打印该偏差；**场景 A 使用的是第二个真实客户端**。
* 结论：§7.3 的 PARTIAL 记录**保留为历史**，本条为后续补充证据；真机门禁最终裁定 **PASS**（导航可达性边界见 §7.5）。

## 8. Follow-up 3：显式接受门（Explicit Handoff Acceptance Gate）

### 8.1 缺陷根因（§3 调用链审计，真实代码）

调用链：`用户消息 → TurnDecision → _route_turn → 待决提案判定 → 既有资源入口 → 批准门 + 执行确认门`。
五个审计问题的真实答案（**修复前**）：

1. **当前消息如何被判定为接受？** 修复前**没有任何语义判定**：`_route_turn` 的 `TASK_PROPOSAL` 分支只要
   `_pending_handoffs` 里存在该 `(user, session)` 的待决项，就直接调用 `_accept_pending_handoff` 消费它 ——
   也就是把「决策层判成 `TASK_PROPOSAL`」当成了接受证据（fail-open）。
2. **新目标如何防止消费旧提案？** 修复前只有 `_is_new_craft_request()`（仅识别**合成**请求）：新的**采集**请求
   （如「砍两根橡木原木」）走 `else` 分支 → **被当成接受**，随后由 `_canonical_wood_objective(pending.block,
   pending.amount)` 用**旧提案的目标与数量**建任务，用户当轮的新目标被静默丢弃 —— 任务书 §1 的风险路径成立。
3. **歧义如何 fail closed？** 修复前**不 fail closed**：询问、不确定、无关消息同样会消费提案。
4. **状态生命周期**：消费 = `pop`（`_accept_pending_handoff`）✓；过期 = `pop` + 如实提示 ✓；被新合成请求取代 = `pop` ✓；
   **明确拒绝/取消** = 未处理（提案残留，之后仍可能被接受）；存储以 `(username, session_id)` 为键 ✓。
5. **会话隔离**：`_pending_handoffs` / `_pending_craft_goals` 都以 `(str(username), str(session_id))` 为键 ✓，
   跨会话不共享；本次补齐了**真实路由层面**的证明（见 8.3 与 8.4）。

### 8.2 接受门契约（§4，最小实现）

`classify_handoff_reply(message)` 返回四态，判定顺序固定：

| 顺序 | 判定 | 结果 | 行为 |
| --- | --- | --- | --- |
| 1 | 含拒绝/取消/停止信号（取消/停止/停下/别/不用/算了/不要/不行/否决/拒绝/先不） | `reject_proposal` | 立即作废待决提案，绝不复活 |
| 2 | 是一次新的可执行**合成**请求（`_is_new_craft_request`） | `new_goal` | 作废旧提案，按新请求重新评估 |
| 3 | 含询问/不确定信号（吗/?/？/什么/怎么/为什么/多久/多少/能不能/是不是/要不要） | `not_accept` | fail closed，**不消费** |
| 4 | **明确的接受表达**（起始处出现接受词，且紧跟标点或结束） | `accept_proposal` | 消费提案 → **既有**木材/资源入口 → 既有两道门 |
| 5 | 是一次新的可执行**采集**请求（`parse_wood_request`） | `new_goal` | 走它**自己**的既有路由，绝不复用旧目标与数量 |
| 6 | 其余（无关消息、空消息） | `not_accept` | fail closed，**不消费**（提案保留到被明确接受或过期） |

* 「判成 `task_proposal`」**不是**接受证据：决策层把「给我做 64 块橡木木板」和「好，去砍橡木原木」都判成
  `task_proposal`，所以必须由本门自己判语义。
* 接受词要求**起始 + 边界**，避免「好难」「好像不行」被误判为接受（`好` 后面必须是标点或结束）。
* 每种判定都会写一条结构化路由记录（`handoff_accept_proposal` / `handoff_new_goal` / `handoff_reject_proposal`
  / `handoff_not_accept`），提案是否被消费可诊断。
* **未新增**任何 Minecraft 工具、权限、执行器、并行任务状态机或数据库 schema；授权仍由既有两道门决定。

### 8.3 真实 `_route_turn` 路由矩阵（§5）

`tests/test_phase8i_handoff_route_matrix.py`（**27 条**，全部通过）执行**真实的**
`MinecraftChatBridge._route_turn` 与**真实的** `_accept_pending_handoff`，只把「既有资源/合成入口」换成记录桩；
负向用例逐条断言「旧提案未被消费 / 没有真实计划与观察调用 / 未确认前零 `action_id` / 真实背包不变」。

| §5 场景 | 结果 |
| --- | --- |
| 待决提案 + 明确接受 | 消费提案 → `handoff_accepted` → 既有资源入口（目标=提案冻结的 `砍橡木原木 2 根`） |
| 重复提出合成请求 | `new_goal`：作废旧提案、按新请求重新评估，**不**当接受 |
| 新的**采集**请求 | `new_goal`：走自己的木材路由，**不**消费旧提案、**不**复用旧目标与数量 |
| 无关新任务 | `not_accept`：不消费，提案保留 |
| 仅询问/不确定 | `not_accept`：不启动任何东西，提案保留 |
| 明确拒绝/取消/STOP | `reject_proposal`：作废提案；随后「同意」**不得复活** |
| 提案已过期 | 不建任务 + 明确提示重新观察；提案被清理 |
| 不同用户 / 不同会话 | 跨会话**不能**消费；原会话提案完好 |
| 已经被接受一次 | 第二次接受**不**创建第二个资源任务（资源入口恰好被调用一次） |
| 新请求数量/树种不同 | 不得静默使用旧提案的目标与数量（新请求走它自己的路由） |

### 8.4 真实服务器验证（本地门禁脚本，不发布）

* **协议**：run 1 跑全场景（`CatooBot_backups/_phase8i_fu3_gate1.log`），run 2/3 用 `MC_SMOKE_SCENES=E` **隔离重跑**接受门场景
  （`_phase8i_fu3_gate2_E.log` / `_phase8i_fu3_gate3_E.log`），因为 run 1 中场景 D 出现跨场景污染（见 8.5）。
* **客户端类型（如实）**：场景 A 的 A2/A3/A5 与场景 E 的 E1/E3/E5 使用的是**第二个真实 Mineflayer 客户端**（`IdCraft8G`，
  日志逐条打印「注入方式=second_client」）；**跨会话用例**（身份 `IdCraft8G2`）与部分重试用的是**真实聊天桥事件注入**，
  脚本每次都打印 DEVIATION，绝不冒称第二客户端真机测试。
* **§6.1/6.2/6.5 —— 有效接受闭环（run 1 场景 A，全部 PASS）**：A2 缺料 → 真实观察（8 个候选、最近 2 格）→ `handoff_proposed`，
  提案阶段零任务/零轨迹/零库存；A3 明确接受 → 既有资源任务 `task_c5206db891df`（`PENDING_CONFIRMATION`、含挖掘步骤、
  **无**合成步骤、确认前零 `action_id`）；A4 `SUCCEEDED` + **独立重读背包 `oak_log` 净增 1**；A5 重新规划为**新**任务
  `task_1c0f172e3f78`（不复用旧资源任务、冻结 `oak_planks*4=oak_log*1`、确认前零 `action_id`）；A6 `SUCCEEDED` +
  **独立核验 `oak_planks` 净增 4、`oak_log` 净耗 1**、逐步后置验证通过。
* **§6.3/6.4 —— 关键负向路径（run 2/3 场景 E，E1–E5 全部符合契约）**：
  E1 缺料合成请求 → 真实提案（待决键 `['IdCraft8G']`）；E2 **另一个独立会话**（`IdCraft8G2`）重复合成请求 →
  **未消费**本会话提案，两个待决提案并存 `['IdCraft8G', 'IdCraft8G2']`（**双向隔离**证据）；E3 同会话新的**采集**请求（白桦）→
  `handoff_new_goal`、无 `handoff_accepted`、旧提案被取代、真实背包不变，且新建任务的目标是**白桦**
  （`objective=给我砍两根白桦原木`）—— **绝未复用旧提案的橡木目标与数量**；E4 作废后的「同意」**不得复活**；
  E5 重新建立待决提案成功。
* **E6 未通过（如实记录，不掩盖）**：run 2/3 中接受语（「好，去砍橡木原木」等）被**上游决策层**判成非 `task_proposal`
  （日志为 `model_no_tool_call` / `task_handled`），因此**根本没有到达接受门**，没有产生 `handoff_accepted`；run 3 中该消息还被
  路由到既有任务入口并使先前那条白桦任务继续（出现 `action_id`）。这说明**接受路径依赖上游分类**（真实模型非确定性），
  **不是接受门失效**：run 1 的 A3 用同一句话、同一身份成功走完「接受 → 既有资源入口 → 两道门」。
* **负向断言**（E2/E3/E4）：旧提案未被消费、无意外建立的任务、真实背包不变，全部成立。

### 8.5 保留边界与独立后续事项（不在本阶段扩展）

* 接受路径的**上游分类非确定性**：同一句接受语在不同轮次可能被判成聊天或控制，从而到不了接受门（fail-closed 方向安全）。
* E6 观察到「接受语被判成控制后由既有任务入口消费并推进了先前任务」—— 属**上游控制分类**范畴，记录为独立后续事项。
* run 1 场景 D 的 2 处失败是**跨场景污染**（同一会话里先前任务导致 `plan_already_planned`，且第二个真实客户端在 C/D 时段不可用），
  与接受门改动无关；隔离重跑（run 2/3）未再出现该污染。
* 既有未决事项仍然独立：导航可达性（§7.5）、配方变体排序（全部 `available: false` 时退化为 `recipe_id` 字母序）、
  真实成功数据库迁移演练（真实库始终最新版，`migrated` 仅有单元级证据）。
