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

## 9. Follow-up 4：待决提案仲裁跨决策路由（Pending Handoff Arbitration）

### 9.1 缺陷根因（§3 调用链审计，真实代码）

* FU3 的接受门**只存在于 `_route_turn` 的 `TASK_PROPOSAL` 分支内**（`app/integrations/minecraft/chat_bridge.py` 原 650–680 行）；
  而 `_route_turn` 在它**之前**就已提前返回：`CHAT/NARRATION` → CHAT_ONLY、`CLARIFY` → 澄清、`UNSUPPORTED` → BLOCKED、
  `QUERY/ATOMIC_ACTION` → `_run_capability()`、`CANCEL/STOP` → 交回既有控制路径。
* 真实后果（FU3 run 2/3 的 E6）：明确接受语被判成非 `task_proposal` 时**根本到不了接受门**（日志只有
  `model_no_tool_call`/`task_handled`），且 run 3 中该消息被既有任务入口消费、推进了先前的白桦任务（出现 action_id）。
  **E6 原始记录保留在 §8.5，未被删除或改写为 PASS。**
* 第二个缺陷在**测试**里：`tests/test_phase8i_handoff_route_matrix.py` 的 Harness 把 kind 固定为 `TASK_PROPOSAL`，
  因此 FU3 的 27 条矩阵**不能代表**非 `TASK_PROPOSAL` 的真实路由。

### 9.2 修复：确定性仲裁点（§3，最小实现）

* 新增 `MinecraftChatBridge._arbitrate_pending_handoff(username, session_id, message, kind)`，在 `_route_turn`
  **最顶部**（`decision = outcome.decision` 之后、任何 kind 分支之前）调用；返回非 `None` 即接管本回合。
* 契约（判定顺序固定）：`REJECT_PROPOSAL` → 作废提案并放行；`NEW_GOAL` → 作废旧提案并放行（消息仍走自己的路由）；
  `ACCEPT_PROPOSAL` → **同用户同 session 的有效提案**存在则消费**恰好一次**并交**既有** `_accept_pending_handoff`
  （仍过既有批准门与执行确认门），否则放行；`NOT_ACCEPT` → 仅在存在待决提案时记一条 `handoff_not_accept`，不消费、不改路由。
* 安全边界：`CANCEL/STOP` **保持既有控制权威** —— 即便措辞命中接受词也不消费提案；仲裁**从不创建任务**，
  普通聊天不会因此获得任何执行能力；没有有效待决提案时接受词单独**不建任何资源任务**。
* `TASK_PROPOSAL` 分支内的旧接受门已移除，该分支只保留「合成解析先于木材解析」与自身路由；
  `classify_handoff_reply()` 与 `_is_new_craft_request()` 未改（分类器契约与白名单不变）。

### 9.3 回归测试（§4A/§4B）

* `tests/test_phase8i_handoff_route_matrix.py`：**原 27 条全部保留 + 新增 25 条 = 52 条**；Harness 新增 `kind` 形参、
  `_run_capability`/`_delegate_task` 记录桩（`send()` 默认仍为 `TASK_PROPOSAL`，旧断言零改动）。
* §4B 跨 kind 一致性：同一句接受语在 `TASK_PROPOSAL`/`QUERY`/`ATOMIC_ACTION`/`CHAT`/`NARRATION`/`CLARIFY`/`UNSUPPORTED`
  **七类**下都必须消费提案恰好一次，且 `_run_capability`、`_delegate_task`、计划/观察调用**全部为空**。
* E6 同构用例：接受语以 `ATOMIC_ACTION` 送达时提案被消费，且**同一条消息**不得进入既有任务入口；
  随后无关消息仍能正常进入该入口（证明仲裁只接管那一句）。
* 其余覆盖：新采集目标不被吞掉（按自身 kind 继续）、询问不消费且查询行为不变、`CANCEL/STOP` 不消费、
  无待决提案时接受词零任务、拒绝在 QUERY 类下仍作废且不复活、跨 session/跨用户隔离跨 kind 成立。
* 8I 套件 + 依赖套件合跑 **134 passed**（矩阵 52 + 交接 9 + 桥接 13 + 分类规则 5 + 缺口数量 3 + 8G/8H 规划 51 + 1）。

### 9.4 真实服务器验证（§5 第 3 项，本地门禁脚本，不发布）

* 新增场景 F（`scripts/phase8i_handoff_smoke_real.py`，`MC_SMOKE_SCENES=F`）—— **受控回放**：真实解释器
  （真实 Provider + 真实上下文）照常运行，只把**这一句**的 kind 改写；并用 `_RouteKindSpy` 包住 `_route_turn`
  记录**真实收到**的类别，避免「自说自话」。
* run F1（`CatooBot_backups/_phase8i_fu4_gateF1.log`）：提案与接受语均由**真实第二个 Mineflayer 客户端** `IdCraft8G`
  发出（`how=second_client`）→ 提案被消费、`handoff_accepted`、资源任务 `task_fe1b725d4297`
  （`objective=砍橡木原木 1 根`、`plan_hash=64a3ffcd22a26a26c043`、确认前零 `action_id`）。
* run F2（`_phase8i_fu4_gateF2.log`，**PASS**）：`F3 受控回放原类别=['task_proposal'] 到达路由的类别=['atomic_action']
  接受路由=True` —— 独立证明「接受语以 `atomic_action` 到达 `_route_turn`，提案仍被恰好消费一次（消费后剩余键 `{}`）」。
  同轮：先前任务 `task_479e5d5dc2ac` 前后 `PENDING_CONFIRMATION → PENDING_CONFIRMATION` 且**无新 `action_id`**；
  新任务 `task_c9319b4991bf`（`objective=砍橡木原木 1 根`、无合成步骤、确认前零 `action_id`）；
  **同一句话未建立第二个任务**；接受动作本身**未改变真实背包**（`{}` → `{}`）。
* 替代证据（如实标注）：run F2 的提案与接受均走**真实聊天桥事件注入**（身份 `IdTurn8E`），非第二个真实客户端
  （日志逐条打印 DEVIATION）；同一路径在 run F1 用的是**真实第二个客户端**。
### 9.5 §5 第 1/2/4/5/6 项与自然误分类下的接受（场景 E 重跑）

* 场景 E 重跑（`_phase8i_fu4_gateE3.log`）：**通过=1 失败=0 BLOCKED=0，REAL SERVER: PASS**，12 项检查全部 `[ok]`。
* **自然误分类（无强制回放）下的 E6**：该轮真实模型把接受语两次判成 `model_no_tool_call`（**非** `task_proposal`），
  路由日志随后仍出现 `handoff_accepted` 并建立资源任务 `task_84a7be4e50dd` —— 即 FU3 的 E6 失败原因（接受门到不了）
  在本阶段被真实链路直接消除（不是靠夹具把类别改成 `task_proposal`）。
* §5 第 1 项（建立提案）E1 `handoff_proposed`（`how=second_client`）；第 4 项（存在橡木提案时提交「砍两根白桦原木」）
  E3 `handoff_new_goal`、无 `handoff_accepted`、旧提案被取代、新任务目标不含橡木（本轮附近无白桦 → 安全拒绝，符合 fail-closed）；
  第 5 项（另一 session 重复合成请求）E2 待决键并存 `['IdCraft8G','IdCraft8G2']`、本会话提案未被消费；
  第 6 项（拒绝后再发接受词）E4 无 `handoff_accepted`、提案不复活；第 2 项（明确接受）见 E6 与 §9.4 的 run F1/F2。
* 夹具修正（本地脚本，不发布）：场景 E 的接受语原先由**另一个身份**（真实第二客户端 `IdCraft8G`）发出，
  而提案是在注入身份下建立 → 按 session 隔离设计**本就不该被消费**（安全属性），随后该消息被**既有**任务入口
  消费并推进了先前任务（属既有控制路径语义，不在本阶段范围）。修正为「用提案自身的会话键发言」后 E6 通过。
* 夹具缺陷（本地脚本，不发布）：插入场景 F 时误把场景 E 的收尾 `_dump_routes_tail`/`return bool(ok)` 挤到 F 之后，
  导致 E 返回 `None` 而显示 FAIL（其 12 项检查当时其实全是 `[ok]`）。已修正并重跑；**未放宽任何断言**。

### 9.6 保留边界（如实）

* 仲裁只处理**待决资源提案**；`CANCEL/STOP` 与既有任务入口仍是控制语义的权威（本阶段未改其行为）。
* FU4 不扩展导航可达性、配方变体选择、容器/丢弃、真实成功迁移演练等其他自主能力。
* FU3 场景 D 的跨场景污染、`test_social.py::test_direct_mention_not_blocked_by_sleeping` 的时段抖动
  仍为独立既有事项，未在本阶段顺手修改。

## 10. Follow-up 5：接受表达的目标一致性门（Handoff Acceptance Target Consistency Gate）

### 10.1 缺陷根因（§1，真实代码）

* `classify_handoff_reply()`（`app/integrations/minecraft/chat_bridge.py`）的判定顺序是
  「拒绝 → 新**合成**目标 → 询问 → **接受** → 新**采集**目标 → 兜底」，而 `_is_acceptance()` 只要求
  **消息开头**出现接受词且紧跟标点。于是「好，去砍五根白桦原木」在「新采集目标」分支**之前**
  就被判成 `ACCEPT_PROPOSAL`，随后按**旧**橡木提案的目标与数量消费提案，用户当轮明确的白桦目标被静默丢弃。
* 这正是 FU4 验收条件「新目标不能静默使用旧提案的目标与数量」未关闭的边界：分类器是**纯消息函数**，
  看不到待决提案，因此「这句接受是否与当前提案相容」必须在**有上下文**的地方判定。

### 10.2 修复（§3，最小实现，不引入第二套授权机制）

* `app/tasks/intent.py` 新增公共薄封装 `explicit_log_count(text) -> int | None`：复用既有 `_parse_log_count`，
  让调用方能区分「**明确**写了数量」与「没写（默认 1 根）」—— 缺了它就无法判断「同树种但数量不同」。
* `chat_bridge.py` 新增两个纯函数与一个判定入口，全部复用既有解析器：
  * `_acceptance_remainder(text)` —— 接受词之后的剩余内容（`""` = 纯接受）；
  * `_is_pure_acceptance_tail(rest)` —— 剩余内容本身仍是确认说法（例：「好，**就按这个方案来**」）；
  * `handoff_target_verdict(message, pending)` —— 三态判定：
    `ACCEPT_PROPOSAL`（纯接受，或同树种且未给出不同数量）/ `NEW_GOAL`（不同树种或不同的明确数量）/
    `NOT_ACCEPT`（接受词后是与提案无关的内容，或无法无歧义判定 → fail closed）。
* `_arbitrate_pending_handoff()` 的接受分支改为**先做目标一致性校验**：
  `NEW_GOAL` → 作废旧提案并记 `handoff_new_goal`，消息继续走自己的既有路由；
  `NOT_ACCEPT` → 记 `handoff_not_accept` 且**保留**提案（fail closed）；
  只有 `ACCEPT_PROPOSAL` 才调用既有 `_accept_pending_handoff()`（目标仍**只能**来自待决提案）。
* `CANCEL/STOP` 仍保持既有控制权威（不消费）；未新增工具、权限、状态机、schema 或迁移；
  `classify_handoff_reply()` 的既有契约未改写（四种返回值不变），一致性判定是**额外的确定性校验**。

### 10.3 契约表（§3，纯函数、无模型、无任务）

| 当前消息（待决提案 = `oak_log × 2`） | `classify_handoff_reply` | `handoff_target_verdict` | 仲裁结果 |
|---|---|---|---|
| `同意` / `可以` / `好，就按这个方案来` | `accept_proposal` | `accept_proposal` | 消费一次（目标来自提案） |
| `好，去砍橡木原木` / `好，去砍原木` / `好，去砍两根橡木原木` | `accept_proposal` | `accept_proposal` | 消费一次；**不得重复建任务** |
| `好，去砍五根白桦原木` | `accept_proposal` | **`new_goal`** | 作废旧提案 + 按白桦路由 |
| `同意，改砍白桦` | `accept_proposal` | **`new_goal`** | 作废旧提案 + 按新目标路由/澄清 |
| `可以，不过这次砍五根橡木原木` | `accept_proposal` | **`new_goal`** | 作废旧提案；**不静默采用旧数量** |
| `好，给我做 64 块橡木木板` | `new_goal`（合成分支更早命中） | `not_accept`（不参与） | 作废旧提案 + 重新评估合成 |
| `好，先跟我去另一个地方` | `accept_proposal` | **`not_accept`** | 保留提案 + 消息走自己的意图处理 |
| `好难` / `可以吗？` / `嗯？` | `not_accept` | 不参与 | 不消费、保留提案 |
| `好，去砍竹子` | `accept_proposal` | `not_accept` | 不消费（解析器报不支持 → fail closed） |

### 10.4 回归测试（§4）

* `tests/test_phase8i_handoff_route_matrix.py`：原 52 条**全部保留** + 新增 **37 条 = 89 条**；
  新用例全部驱动**真实** `_route_turn()` 与**真实** `_arbitrate_pending_handoff()`（只把既有资源/合成入口换成记录桩）。
* §4.2 断言覆盖：`_pending_handoffs` 保留/消费/作废、`_accept_pending_handoff()` 未被意外调用
  （资源入口的目标记录为空）、资源入口收到的目标与数量、消息仍进入自身路由、无真实观察/计划调用；
  合法新任务**不**以「任务数为零」阻止（只断言旧提案目标未被代入）。
* 跨决策类别（§4.2 末段）：三类冲突消息在 `QUERY`/`ATOMIC_ACTION` 下同样不消费提案（防止重新引入 FU4 的跨路由问题）。
### 10.5 真实服务器验证（§5，本地门禁脚本，不发布）

* 新增场景 G（`scripts/phase8i_handoff_smoke_real.py`，`MC_SMOKE_SCENES=G`），用 `_wood_reply` spy 记录
  **既有资源入口真正收到**的目标（objective + 候选方块）：
  * **G1/G2 —— 匹配接受（§5 第 1、2 项，PASS）**：真实第二个 Mineflayer 客户端（`IdCraft8G`）请求「给我做 8 块橡木木板」
    → 真实缺料 `oak_log(缺 2)` → 待决提案 `{item: oak_log, block: oak_log, amount: 2, requested: 2}`；
    发送「好，去砍橡木原木」→ `handoff_accepted`、提案被消费一次、
    资源入口收到 `('砍橡木原木 2 根', 'minecraft:oak_log')`（**目标来自提案**）→
    任务 `PENDING_CONFIRMATION`、确认前零 `action_id`、`plan_hash` 由 `_dump_steps` 打印、真实背包不变。
  * **G3 —— 不同树种的新目标（§5 第 3 项，PASS）**：新提案（`requested=3`，`amount` 仍为 2）后发送
    「好，去砍五根白桦原木」→ `handoff_new_goal`、**无** `handoff_accepted`、旧提案被作废、
    资源入口收到 `('好，去砍五根白桦原木', 'minecraft:birch_log')` —— **白桦目标生效、旧橡木目标从未被代入**。
  * **G5 —— 新合成目标（§5 第 5 项，PASS）**：「好，给我做 64 块橡木木板」→ `handoff_new_goal`、
    spy 记录为空（**没有触发任何旧资源采集**）、无 `handoff_accepted`、背包不变。
* 新增场景 H（`MC_SMOKE_SCENES=H`，自包含隔离进程）覆盖 §5 第 4、6 项；
  **H PASS 的关键不变量**：H2「可以，不过这次砍五根橡木原木」→ **无** `handoff_accepted`、
  资源入口从未收到旧提案的 `2 根` 目标、背包不变（**旧数量没有被静默套用**）；
  H4 另一身份（`IdCraft8G9`）的接受词 **未消费**本会话提案、提案仍然存在。
* **如实标注的替代证据与边界**：H2/H5（以及整场景 G 中的 G4）这些措辞在**上游决策层**未产出可用决策
  （`model_no_tool_call` → 既有澄清路径，不再进入 `_route_turn`），因此桥接按契约**澄清**并
  **保留**提案（fail-closed）。此时「新数量进入自己的路由」与「拒绝作废提案」这两步在真实服务器上
  未被观察到；核心不变量（**绝不消费旧提案、绝不套用旧数量**）仍然成立且被断言。
  「拒绝后不复活」另有既有真实证据（FU3 场景 E4）与自动化矩阵覆盖。

### 10.6 保留边界（如实）

* 一致性判定是**纯函数**（消息 + 待决提案），不调用模型；上游决策层失败时桥接走既有澄清，
  仲裁不介入（因此该轮不会消费提案，符合 §3.2 的 fail-closed 要求）。
* 接受词后若既不是新目标也不是确认说法，一律**不消费**（fail closed），可能让用户需要重述；
  这是刻意的安全取向，未引入任何猜测式匹配。
* 导航可达性、配方变体排序、真实成功迁移演练、`test_social.py` 时段抖动仍为独立后续事项。


## 11. Follow-up 5 Follow-up：复合意图接受门（Compound Intent Acceptance Gate）

阶段任务书：`docs/specs/CatooBot Minecraft Phase 8I FU5 Follow-up — Compound Intent Acceptance Gate.md`
（来源 `E:\UserData\Downloads\writing-block(16).md`，3149 bytes，SHA256
`1F4552A6386C826513A5BD977988827D38882A6A00A1904D3E64663712226FCD`，逐字节归档）。
开工基线：`6002264c64f2bd2872961f69b3ef1b2ad4181436`（= Phase 8I FU5 收尾）。

### 11.1 缺陷根因（§必测场景 3/4，真实代码）

FU5 的 `handoff_target_verdict()` 只比对**木材目标与数量**（`parse_wood_request(rest)` 的树种与
`explicit_log_count(rest)` 的明确数量），而 `parse_wood_request` **只抽取动作 + 树种 + 数量、忽略其余从句**。
因此「好，去砍橡木原木并跟我走」与「好，去砍橡木原木，然后探索附近」在目标上与待决 `oak_log × 2`
提案**一致** → 判为 `ACCEPT_PROPOSAL` → 消费旧提案，用户当轮明确说出的第二个意图（跟随 / 探索）被静默丢弃。

### 11.2 修复（§实现原则：复用既有解析器，不新增内容黑名单）

* `app/tasks/intent.py`：新增模块级 `_COMPOUND_CONNECTORS`（**只**取既有多阶段词表 `_MULTI_STEP_MARKERS`
  里的并列/顺承连接词「并/然后/再/接着/顺便」，再补「之后/以后/同时/另外/而且/以及/and then」，**不含**
  「回来/带回来/捡起来」这类单任务收尾语 —— 它们本来就是既有采集计划的一部分）与公共函数
  `has_compound_connector(text)`（只表达「有连接结构」，不做语义判断）。
* `app/integrations/minecraft/chat_bridge.py`：新增 `_has_extra_intent(rest)` —— 复用
  `_DETECTOR.detect_follow(rest)`（既有 `FOLLOW_INTENT_KEYWORDS`，含「跟我」）、`parse_craft_request(rest)`
  （合成意图）与 `has_compound_connector(rest)`；`handoff_target_verdict()` 在**冲突检查之后、返回
  `ACCEPT_PROPOSAL` 之前**加 `if _has_extra_intent(rest): return NOT_ACCEPT`。
* 判定顺序刻意保持：树种/数量**冲突**仍是 `NEW_GOAL`（作废 + 走自己的路由，FU5 既有断言不变）；只有
  「目标相容但整条消息还有别的意图」才落到 `NOT_ACCEPT`（fail closed：**保留**提案，消息交回自己的既有路由）。
* 边界未改：不新增工具/权限/授权机制/平行状态机；未动 `ACTION_RISK`/`TOOL_ACTION`/`allow_medium` 默认值/
  Policy/两道确认门/冻结计划与 `plan_hash`/STOP-CANCEL 权威/schema；未放宽任何预算或断言。

判定表（真实函数输出，§必测场景 1–8）：

| 消息 | 待决提案 `oak_log × 2` | 判定 |
| --- | --- | --- |
| `同意` / `好，就按这个方案来` | 保留 | `ACCEPT_PROPOSAL` |
| `好，去砍橡木原木` / `好，去砍原木` / `好，去砍两根橡木原木` | 消费 | `ACCEPT_PROPOSAL` |
| `好，去砍橡木原木带回来` | 消费（单任务收尾语不算复合） | `ACCEPT_PROPOSAL` |
| `好，去砍橡木原木并跟我走` | **保留** | `NOT_ACCEPT` |
| `好，去砍橡木原木，然后探索附近` | **保留** | `NOT_ACCEPT` |
| `好，去砍五根白桦原木` / `同意，改砍白桦` | 作废 | `NEW_GOAL` |
| `可以，不过这次砍五根橡木原木` | 作废 | `NEW_GOAL` |
| `好，给我做 64 块橡木木板` / `好，先跟我去另一个地方` | 保留 | `NOT_ACCEPT` |

### 11.3 自动化回归（§验收门禁：驱动真实 `_route_turn()` 与真实仲裁）

`tests/test_phase8i_handoff_route_matrix.py` 由 89 条扩到 **99 条**（原 89 条零删除、零放宽）：

* 契约表新增 2 行（两条复合消息 → `NOT_ACCEPT`）；
* `test_fu5fu_compound_intent_never_consumes`（parametrize 2 条复合消息）：断言 `_accept_pending_handoff()`
  （Harness 新增 `accept_calls` 计数包装）**零调用**、资源入口 `wood_replies == []`、待决提案**仍在**
  `_pending_handoffs`、`handoff_not_accept` 出现而 `handoff_accepted` 不出现、消息仍进入**自己的**既有路由
  （`wood_routes == [message]`、`delegate_calls == [message]`，即额外意图不被吞掉）、`plans.calls == []`；
* `test_fu5fu_compound_holds_across_decision_kinds`（QUERY/ATOMIC_ACTION × 2 条）：非 `TASK_PROPOSAL`
  类别下同样不消费，证明一致性修复未重新引入 FU4 的跨路由问题；
* `test_fu5fu_pure_acceptance_still_works_after_compound`：先发复合消息（不消费）再发纯接受
  `好，去砍橡木原木`，必须**恰好消费一次**并进入既有资源入口 —— 证明门不是一刀切。

### 11.4 真实服务器验证（§验收门禁：至少一次复合意图负向场景）

本地门禁脚本 `scripts/phase8i_handoff_smoke_real.py`（**不发布**）新增自包含场景 **I**；最终一轮
`CatooBot_backups/_phase8i_fu5fu_gateI4.log`：`通过=1 失败=0 BLOCKED=0` → `REAL SERVER: PASS`。
真实链路（真实 Java 1.21.1 + 真实 CatooBot Minecraft Runtime + 真实 Provider + **真实第二个 Mineflayer
客户端** `IdCraft8G`，如实标注为自动化客户端，不是真人）：

| 子用例 | 消息 | 上游 | 一致性门 | 资源入口 | 待决提案 | 真实背包 |
| --- | --- | --- | --- | --- | --- | --- |
| I1 | `好，去砍橡木原木并跟我走` | `task_proposal`（第二客户端投递） | **触发**（`handoff_not_accept`） | `targets=[]`（未收到旧目标） | **保留** | 不变 |
| I2 | `好，去砍橡木原木，然后探索附近` | `task_proposal`（第二客户端投递） | **触发** | `targets=[]` | **保留** | 不变 |
| I3（对照） | `好，去砍橡木原木` | `task_proposal`（第二客户端投递） | 消费一次 | 收到 `砍橡木原木 2 根` | 已消费 | 不变 |

* I1/I2 均以真实待决提案（`{'item': 'oak_log', 'block': 'oak_log', 'amount': 2, 'requested': 2}`，
  `craft_goal` 为当轮真实合成请求）为前提，且**全程零世界修改**（提案阶段无采集、无合成、无 `action_id`）。
* I3 的作用是反证：同一份待决提案在复合消息之后**仍然有效且可被纯接受消费**，因此 I1/I2 的「未消费」
  是**门**的确定性结果，不是提案已失效。
* 中间迭代如实保留：`_phase8i_fu5fu_gateI1.log`（I1 通过、I2 上游 `model_no_tool_call` 未建新提案、
  I3 对照 3 次 `no_effect`）、`_gateI2.log`、`_gateI3.log`。这些迭代暴露的是**夹具/上游抖动**：
  ① 前一条消息自身路由留下的待确认任务会消费后续请求 → 夹具新增 `_cancel_user_tasks()`（按用户名取消
  **所有**未终态任务，走既有 `runtime.cancel`）并对待决提案建立做有界重试；② 上游决策层对纯接受偶尔
  不产出可用决策（`model_no_tool_call`）→ 对照改为依次尝试**多种等价纯接受说法**（`好，去砍橡木原木` /
  `同意` / `好，就按这个方案来` / `可以`，均为 FU5 契约表中的纯接受）。两处都只改**本地夹具**，
  未改产品、未放宽任何断言或阈值。

### 11.5 保留边界（如实）

* 判定是**确定性纯函数 + 既有解析器复用**，不做语义理解：`并/然后/再/接着/顺便/之后/…` 这类连接结构
  一律按「可能不止一个意图」fail closed，用户可能需要重述（刻意的安全取向）。
* 复合消息的**额外意图**（跟随 / 探索）不会被吞进待决提案，而是走消息自己的既有路由；**跟随/探索本身
  的能力域不属于本阶段**，本阶段只保证它不被静默丢弃。
* 上游决策层偶尔不产出可用决策（`model_no_tool_call`）时，桥接走既有澄清路径，仲裁不介入；此时
  「不消费」由 fail-closed 澄清保证，属替代证据（已在 §11.4 说明）。
* 导航可达性、配方变体排序、真实成功迁移演练、`test_social.py` 时段抖动仍为独立后续事项。
* **（本轮 Follow-up 更正）** §11.5 第二条已被取代：复合类消息**不再**交回它自己的路由，而是**澄清**
  （不消费、不执行、不替用户猜）—— 见 §12.4。

## 12. Follow-up 5 Follow-up（第二轮）：无连接词复合意图与澄清

用户报告（本轮驱动）：FU5-FU 只覆盖「跟随 / 合成 / 预先列举的连接词」三类信号，因此
`好，去砍橡木原木，帮我挖附近的石头` 这类**没有连接词**的复合指令仍会被判成对旧提案的接受。

### 12.1 修复前实测（生产代码，非推测）

| 消息（待决提案 `oak_log × 2`） | 修复前判定 | 修复前额外意图信号 |
| --- | --- | --- |
| `好，去砍橡木原木，帮我挖附近的石头` | `accept_proposal`（**会消费旧提案**） | `False` |
| `好，去砍橡木原木，挖点石头` | `accept_proposal` | `False` |
| `好，去砍橡木原木，去挖石头` | `accept_proposal` | `False` |
| `好，去砍橡木原木再去挖石头` | `not_accept`（「再」恰在连接词表里） | `True` |
| `好，去砍橡木原木再回来` | `not_accept`（**误判**：单一任务） | `True` |

### 12.2 修复（确定性、复用既有解析器）

* `app/integrations/minecraft/chat_bridge.py` 新增 `_CLAUSE_SPLIT_RE`（按 `，,、；;。！!？?` 切分）、
  `_clause_is_goal(clause)`（复用 `parse_wood_request` / `parse_craft_request` / `_DETECTOR.detect_follow`
  / `_DETECTOR.detect` 的 `verb:` + `resource:` 理由组合 —— **不新增内容黑名单、不做语义猜测**）、
  `_has_second_clause_goal(rest)`（第一个从句之外还有从句本身是目标）、
  `_has_trailing_goal(rest, marker)`（「再」后面确实跟着新目标才算复合）。`_has_extra_intent()` 现按
  「跟随 → 合成 → 连接词 → 再+新目标 → 第二个从句是目标」依次判定。
* `app/tasks/intent.py`：`_COMPOUND_CONNECTORS` **移除「再」**（改由「标记之后是否是新目标」判定），
  消除 `去砍橡木原木再回来` 的误判；其余连接词集合不变。
* 澄清路径：新增纯函数 `is_compound_acceptance(message)`（接受词 + 与提案相容的采集描述 + **还有别的
  意图**）与 `compound_clarify_text(pending)`；`_arbitrate_pending_handoff()` 在 `NOT_ACCEPT` 分支命中
  前者时记录 `handoff_compound_clarify` 并返回澄清回复（**不消费、不执行、不猜「只想做其中一件」**）。

### 12.3 修复后判定表（真实函数输出）

| 消息 | 判定 | `is_compound_acceptance` | 行为 |
| --- | --- | --- | --- |
| `好，去砍橡木原木，帮我挖附近的石头` | `not_accept` | `True` | 澄清 |
| `好，去砍橡木原木，挖点石头` / `…，去挖石头` | `not_accept` | `True` | 澄清 |
| `好，去砍橡木原木再去挖石头` | `not_accept` | `True` | 澄清 |
| `好，去砍橡木原木并跟我走` / `…，然后探索附近` | `not_accept` | `True` | 澄清（本轮起） |
| `好，去砍橡木原木再回来` / `…带回来` / `…回来` | `accept_proposal` | `False` | 消费一次 |
| `好，去砍橡木原木` / `好，去砍原木` / `同意` / `好，就按这个方案来` | `accept_proposal` | `False` | 消费一次 |
| `好，去砍五根白桦原木` / `可以，不过这次砍五根橡木原木` | `new_goal` | `False` | 作废旧提案 |
| `好，给我做 64 块橡木木板` / `好，先跟我去另一个地方` | `not_accept` | `False` | 走自己的既有路由（不变） |

### 12.4 行为变更（如实）

复合类消息的处理**从「交回消息自己的既有路由」改为「澄清」**：此前第二个意图（跟随/探索/挖掘）
可能被静默丢掉（只执行木材那一半），本轮按用户给出的验收口径改为**问清再动**。相应地 FU5-FU 的
两条测试断言被**替换为更强的一组**（不消费 + 提案保留 + `handoff_compound_clarify` + 真实澄清话术 +
**零** `wood_routes` / `delegate_calls` / `capability_calls`）；原有断言其余部分零删除、零放宽。

### 12.5 自动化回归

`tests/test_phase8i_handoff_route_matrix.py` 由 99 条扩到 **116 条**（+17）：`COMPOUND_MESSAGES` 2 → 5 条
（新增 `…，帮我挖附近的石头`、`…，挖点石头`、`…再去挖石头`）驱动真实 `_route_turn()` + 真实仲裁；
契约表新增 5 行（含 `好，去砍橡木原木再回来` → `ACCEPT_PROPOSAL`）。

### 12.6 真实服务器验证

场景 **I** 扩到 3 条复合消息 + 1 条对照；最终一轮 `CatooBot_backups/_phase8i_fu5fu2_gateI3.log`：
`通过=1 失败=0 BLOCKED=0` → `REAL SERVER: PASS`。三条复合消息**全部**（真实第二个 Mineflayer 客户端
`IdCraft8G` 投递）：`handoff_not_accept` + `handoff_compound_clarify`、`targets=[]`（既有资源入口未收到
任何目标）、提案保留、澄清话术**真实送达游戏内**：

> 这句话里不止一件事，罐头一次只做一件，怕替你猜错就先不动手。刚才的采集提案还留着（oak_log ×2）：
> 你回一句只做哪一件，我就按那一件走既有流程。

对照的纯接受 `好，去砍橡木原木` 仍**恰好消费一次**（`被消费=True`），证明提案有效、门不是一刀切。

### 12.7 夹具修正与环境噪声（如实）

* 首跑 `_phase8i_fu5fu2_gateI1.log` 有 4 处「背包不变」失败：诊断确认是**环境噪声** —— 罐子自动捡起了
  世界掉落物 `rotten_flesh`（基线 `{}` → 之后 `{'rotten_flesh': 1}`），与产品副作用无关。夹具据此改为
  只对**与目标相关物品**（`oak_log/oak_planks/stick/cobblestone/stone/dirt/coal/iron_ore`）断言零副作用，
  同时打印完整背包快照；采集/合成/挖掘产物仍被严格覆盖。
* 中间迭代 `_phase8i_fu5fu2_gateI2.log` 为同一场景的诊断跑，保留在 `CatooBot_backups`。

### 12.8 仍未覆盖的形态（因此**通用**复合门仍未达成）

第二轮探测（真实函数输出）仍判成 `accept_proposal` 的形态：**既没有标点、也没有连接词、也没有「再」**
的第二种目标 —— 例 `好，去砍橡木原木去挖石头`、`好，去砍橡木原木探索附近`、`好，去砍橡木原木挖附近的石头`；
以及现有解析器完全识别不出的目标短语。这些形态**没有**被本轮修复覆盖，如实记录：**本阶段不宣称
「通用复合意图接受门」已达成**；「从句切分 / 连接词 / 标记后目标」三类信号 + 既有解析器是当前实现的
**真实检测范围**。

* **（FU5 Follow-up 3 更正）** 本节列出的三种形态已在 §13 由「完整消费的正向许可接受语法」覆盖，
  并经真实服务器验证；**但通用性声明仍未改变** —— 现在的检测范围是「整段必须被许可语法完整消费」，
  范围外的任何内容一律澄清（见 §13.6）。

## 13. FU5 Follow-up 3：完整消费的正向许可接受语法（Compound Acceptance Gate Closure）

### 13.1 缺陷复现（修复前，生产代码实测）

| 消息（待决提案 `oak_log × 2`） | 修复前判定 | 修复前额外意图信号 |
| --- | --- | --- |
| `好，去砍橡木原木去挖石头` | `accept_proposal`（**会消费旧提案**） | `False` |
| `好，去砍橡木原木探索附近` | `accept_proposal` | `False` |
| `好，去砍橡木原木挖附近的石头` | `accept_proposal` | `False` |

根因：判定依赖「跟随 / 合成 / 连接词 / 标点切分 / 标记后目标」等**信号枚举**，没有信号的第二目标
既不被识别，也不留下任何痕迹 —— 于是被当成单一接受。

### 13.2 设计（方案 A：完整消费）

* `app/tasks/intent.py` 新增：`_ACCEPTANCE_IGNORED`（**只有**标点与空白 —— 任何词都不在其中）、
  `_ACCEPTANCE_COUNT_RE`（完整数字或中文数字 + 可选既有原木单位）、`_ACCEPTANCE_TAILS`（既有已审计的
  单任务收尾语：带回来/拿回来/捡回来/采回来/取回来/弄回来/回来）、`_ACCEPTANCE_FILLERS`（复用既有
  `_WOOD_REQUEST_MARKERS` + 少量结构助词与语气词）、`_ACCEPTANCE_VOCAB`（**最长优先**词元表：
  `_WOOD_ACTIONS` + `_WOOD_ALIASES` 全部树种别名 + 通用木材名词 + 收尾语 + 填充词）、
  `_consume_acceptance_phrase()`（贪心扫描，返回**未能解释的字符**）、
  `unexplained_acceptance_tail()`（`None`=不是木材描述；`""`=完整消费；非空=未解释尾部）、
  `is_permissive_wood_acceptance()`。
* **不复制第二套词表**：树种用 `WOOD_SPECIES`/`_WOOD_ALIASES`、动作词用 `_WOOD_ACTIONS`、单位用
  `_LOG_COUNT_UNITS`、礼貌词用 `_WOOD_REQUEST_MARKERS`。
* 接线（`chat_bridge.py`）：`handoff_target_verdict()` 的最后一道门从「关键词信号」改为「整段必须被
  许可语法完整消费」（`unexplained_acceptance_tail(rest)` 为空）；既有信号保留为**附加**护栏，只会增加
  拒绝、不会放宽接受。`is_compound_acceptance()` 同步纳入未解释尾部 → 未解释内容一律走澄清路径。
* 失败语义：冲突目标仍是 `NEW_GOAL`（先于完整消费判定，作废旧提案并走自己的路由）；其余未解释内容
  → `NOT_ACCEPT` → 澄清（`handoff_compound_clarify`：不消费、不执行、保留提案）。

### 13.3 判定表（真实函数输出）

| 类别 | 消息 | 判定 |
| --- | --- | --- |
| §3.1 合法接受 | `同意` / `可以` / `好，就按这个方案来` / `好，去砍橡木原木` / `好，去砍原木` / `好，去砍两根橡木原木` / `好，去砍橡木原木带回来` / `好，去砍橡木原木再回来` | `accept_proposal`（消费一次，目标只来自提案） |
| §3.2 冲突新目标 | `好，去砍五根白桦原木` / `同意，改砍白桦` / `可以，不过这次砍五根橡木原木` / `好，给我做 64 块橡木木板` | `new_goal`（作废 + 自己的路由/澄清） |
| §3.3 复合/未知 | `…并跟我走` / `…，然后探索附近` / `…，帮我挖附近的石头` / `…，挖点石头` / `…再去挖石头` / `…去挖石头` / `…探索附近` / `…挖附近的石头` / `…，帮我找附近的煤` / `…然后去钓鱼吧` / `…挖附近的青金石` / `…顺手把栅栏修了` | `not_accept` + 复合 → 澄清 |

例（修复后真实输出）：`去砍橡木原木去挖石头` → 未解释尾部 `石头`；`去砍橡木原木探索附近` → `探索附近`；
`去砍橡木原木挖附近的石头` → `附近石头`；`去砍橡木原木再去挖石头` → `石头`；
`去砍橡木原木，帮我找附近的煤` → `找附近煤`；八条合法接受全部 → 未解释尾部为空。

### 13.4 自动化测试

`tests/test_phase8i_handoff_route_matrix.py` 由 116 条扩到 **165 条**（原断言零删除、零放宽）：

* §3.1 八条全部覆盖（新增 `…带回来`、`…再回来`），断言恰好消费一次且目标来自提案；
* `COMPOUND_MESSAGES` 5 → **12 条**（§3.3 九条 + §5.3 三条未知尾部：未知闲聊、未知资源、未知动作），
  每条驱动真实 `_route_turn()` 与真实 `_arbitrate_pending_handoff()`，断言 `_accept_pending_handoff()`
  零调用、提案仍在 `_pending_handoffs`、出现 `handoff_compound_clarify`、返回非空澄清话术、
  `wood_routes`/`delegate_calls`/`capability_calls` 全空、`plans.calls == []`；
* 新增许可语法单测：7 条完整消费、11 条未解释尾部（逐条给出**真实**剩余文本）、以及非木材文本
  （`去挖石头`/`就按这个方案来`）返回 `None`（不由许可语法裁定）。

### 13.5 真实服务器验证（任务书 §6）

`CatooBot_backups/_phase8i_fu5fu3_gateI1.log`：**`通过=1 失败=0 BLOCKED=0` → `REAL SERVER: PASS`**。

* 六条复合消息（真实待决提案 `oak_log × 2`）全部：`handoff_not_accept` + `handoff_compound_clarify`、
  `targets=[]`（既有资源入口未收到任何目标）、提案保留、澄清话术**真实送达游戏内**、与目标相关物品
  （原木/木板/木棍/圆石/石头/泥土/煤/铁矿石）无净变化；
* 对照：纯接受 `好，去砍橡木原木`、合法收尾语 `好，去砍橡木原木带回来`、`好，去砍橡木原木再回来`
  各自在**新提案**上**恰好消费一次**（证明提案有效、门不是一刀切）；
* 投递方式（如实）：I1–I4 由**真实第二个 Mineflayer 客户端** `IdCraft8G` 投递并进入仲裁；
  I5/I6 与两条收尾语对照因第二个客户端不可用 → **回退真实聊天桥事件注入**（日志逐条标注 `DEVIATION`），
  属**替代证据**（仍走真实决策 → 真实仲裁 → 真实桥接回复），与真实客户端证据分开记录；
* 环境噪声：全程背包 `{'rotten_flesh': 1}`（罐子自动拾取的世界掉落物），完整快照已打印，
  断言只针对本次目标相关物品。

### 13.6 保留边界（如实）

* 许可语法是**闭集**：整段必须被「木材目标短语 + 已审计收尾语 + 填充词 + 标点/数量」完整解释。
  因此**过度拒绝是可能的**（更迂回的说法会被澄清而不是接受）—— 这是刻意选择的 fail-closed 取向。
* 本阶段**不声称**理解任意自然语言：范围是 `WOOD_SPECIES` 的六种原木 + 已审计动作词/收尾语/礼貌词；
  被澄清的第二个目标**不解析、不执行**（任务书 §3.3 明确不要求能力）。
* 本阶段范围外（既有后续事项）：Phase 8D 导航可达性、配方变体按库存可满足度排序、真实成功迁移演练、
  `tests/test_social.py::test_direct_mention_not_blocked_by_sleeping` 时段抖动。

### 13.7 裁决

**PASS**（任务书 §9 各项均满足）：§3.3 九条已知反例全部不再消费旧提案；只有整段被许可语法完整解释
才消费；未知尾部不被静默忽略；纯接受、相容单目标描述与已审计收尾语继续通过；树种/数量冲突与新合成
目标保持既有 `NEW_GOAL` 行为；复合输入保留提案、不调用接受或执行入口并返回澄清；跨决策类别、
用户/session、过期、拒绝、STOP/CANCEL 与双确认契约无回归；权限模型与受保护区域未修改。

## 14. FU5 Follow-up 4：完整消费 ≠ 单一意图 —— 单目标结构门（Single Intent Structure Gate）

### 14.1 静态审计候选的实测复现（基线 `0c527bf`，**未改生产逻辑**）

§13 的完整消费判定**被证实是必要但不足的**：下列消息的每个词元都在许可词表里、未解释尾部为空，
却包含第二个动作目标 —— 修复前实测（真实函数输出）：

| 消息（待决提案 `oak_log × 2`） | `classify_handoff_reply` | `handoff_target_verdict` | 未解释尾部 | `_has_extra_intent` | 判定 |
| --- | --- | --- | --- | --- | --- |
| `好，去砍橡木原木去砍橡木原木` | `accept_proposal` | **`accept_proposal`** | `''` | `False` | 误当成单目标接受 |
| `好，去砍橡木原木砍橡木原木` | `accept_proposal` | **`accept_proposal`** | `''` | `False` | 同上 |
| `好，去砍橡木原木去采集木头` | `accept_proposal` | **`accept_proposal`** | `''` | `False` | 同上 |
| `好，去砍橡木原木再砍橡木原木` | `accept_proposal` | `not_accept` | `''` | **`True`** | 仅被「再 + 新目标」护栏拦下（语法本身**通过**） |
| `好，去砍橡木原木去挖原木` | `accept_proposal` | **`accept_proposal`** | `''` | `False` | 误当成单目标接受 |

机制分别记录（任务书 §3 要求）：第 4 条**只有**既有护栏命中，许可语法对它判「完整消费通过」；
前 3 条与第 5 条**两个机制都不拦**。因此缺口不在词表，而在「词元可识别」被当成了「只有一个目标」。

### 14.2 修复：单目标结构门（`app/tasks/intent.py`）

新增（**全部由既有词表派生**，不新增第二套词表）：`_GENERIC_WOOD_NOUNS`（与 `parse_wood_request` 一致）、
`_ACCEPTANCE_TOKEN_KINDS`（词元 → 类别：`_WOOD_ACTIONS` / `_WOOD_ALIASES` / 通用名词 / `_ACCEPTANCE_TAILS` /
`_ACCEPTANCE_FILLERS`，最长优先）、`_acceptance_token_kinds()`（切成**类别序列**，有无法解释的字符则 `None`）、
`_acceptance_structure_error()`（确定性结构自动机）、`acceptance_structure_violation()`（稳定原因码）；
`is_permissive_wood_acceptance()` 语义升级为「完整消费 **且** 符合单目标结构」。

许可结构（确定性、可审计）：

```
填充词* 动作 填充词* [数量] (树种? 通用名词? 且至少其一) 填充词* [数量] 填充词* [收尾语] 填充词*
```

关键约束：**恰好一个动作**、**恰好一个目标短语**（树种+通用名词算同一个目标）、数量与收尾语各至多一次
且必须落在结构位置；任何第二个动作/目标都给出原因码。`chat_bridge.handoff_target_verdict()` 末门改为
`tail or structure or _has_extra_intent(rest)` → `NOT_ACCEPT`（既有关键词护栏保留为**只增不减**的附加层），
`is_compound_acceptance()` 同步纳入结构违反 → 走既有 `handoff_compound_clarify` 澄清路径。

修复后实测（同一批消息，全部 `second_action`）：

| 消息 | 结构原因码 | 未解释尾部 | `_has_extra_intent` | `handoff_target_verdict` |
| --- | --- | --- | --- | --- |
| `好，去砍橡木原木去砍橡木原木` | `second_action` | `''` | `False` | `not_accept` |
| `好，去砍橡木原木砍橡木原木` | `second_action` | `''` | `False` | `not_accept` |
| `好，去砍橡木原木去采集木头` | `second_action` | `''` | `False` | `not_accept` |
| `好，去砍橡木原木再砍橡木原木` | `second_action` | `''` | `True` | `not_accept` |
| `好，去砍橡木原木去挖原木` | `second_action` | `''` | `False` | `not_accept` |

合法单目标与冲突语义**零变化**：`同意`/`可以`/`好，就按这个方案来`/`好，去砍橡木原木`/`好，去砍原木`/
`好，去砍两根橡木原木`/`好，去砍橡木原木带回来`/`好，去砍橡木原木再回来` 仍 `accept_proposal`；
`好，去砍五根白桦原木` 等冲突目标仍 `new_goal`；`好，给我做 64 块橡木木板` 仍 `not_accept`。

### 14.3 自动化验收矩阵（§5 逐项）

`tests/test_phase8i_handoff_route_matrix.py` **165 → 191 条**（+26，原断言零删除、零放宽）：

1. `STRUCTURAL_COMPOUND_MESSAGES`（5 条）逐条断言 `unexplained_acceptance_tail == ""`、
   `acceptance_structure_violation == "second_action"`、`is_permissive_wood_acceptance is False`、
   `handoff_target_verdict == NOT_ACCEPT`；
2. 每条驱动真实 `_route_turn()` + 真实 `_arbitrate_pending_handoff()`：`accept_calls == []`、提案仍在
   `_pending_handoffs`、`handoff_not_accept` 有而 `handoff_accepted` 无、`handoff_compound_clarify` + 非空
   澄清话术（含「一件」）、`wood_routes`/`delegate_calls`/`capability_calls`/`craft_calls` 全空、`plans.calls == []`；
3. 至少三种入口类别：`TASK_PROPOSAL`（上一项）+ `QUERY`/`ATOMIC_ACTION`（参数化 10 条）均不得绕过；
4. 结构澄清之后用户再发纯接受 → 提案仍可且**只可**消费一次（5 条）；
5. 机制分别记录的专项断言（结构门单独即可拦下 `去砍橡木原木去砍橡木原木`；`再砍橡木原木` 同时命中两个机制）；
6. §5.8 无回归：Follow-up 3 的许可集、冲突集、九条已知复合例、三条未知尾部例、11 条尾部单测**原样保留**并全部通过。

### 14.4 真实服务器验证（§6；本地脚本 `scripts/phase8i_handoff_smoke_real.py`，不发布）

新增场景 **J**（复用场景 I 实现，仅换消息集/标签/请求下标）：`CatooBot_backups/_phase8i_fu5fu4_gateJ1.log` →
**`通过=1 失败=0 BLOCKED=0` / `REAL SERVER: PASS`**。真实 Java 1.21.1、真实 Runtime/MinecraftService、真实 Provider。

| 子用例 | 消息 | 投递路径 | 判定/证据 |
| --- | --- | --- | --- |
| J1 | `好，去砍橡木原木去砍橡木原木` | **真实第二客户端 `IdCraft8G`** | `handoff_compound_clarify`、`targets=[]`、提案保留、澄清话术送达、目标物品无变化 |
| J2 | `好，去砍橡木原木砍橡木原木` | **真实第二客户端** | 同上 |
| J3 | `好，去砍橡木原木去采集木头` | **真实第二客户端** | 同上 |
| J4 | `好，去砍橡木原木再砍橡木原木` | **真实第二客户端** | 同上（此例同时命中「再 + 新目标」护栏） |
| J5 | `好，去砍橡木原木去挖原木` | 第二客户端不可用 → **真实聊天桥事件注入**（日志 `DEVIATION`，替代证据） | 同上 |
| J-对照 | `好，去砍橡木原木`（纯接受） | 同上 | 提案**恰好消费一次**（门不是一刀切） |

每条子用例均打印：真实接收/仲裁 trace、判定与 `handoff_compound_clarify`、游戏内实际送达回复、
提案前后快照、目标相关物品前后快照、`targets=[]`。未使用 `/give`、数据库写入、直接 Mineflayer 旁路或
假客户端结果；环境噪声照旧全量记录（全程 `{'rotten_flesh': 1}`，断言只针对目标相关物品）。

### 14.5 质量门禁

* `pytest tests -q` → **4571 passed, 1 failed**；唯一失败是任务书 §10 **明确排除**的既有**时段相关**抖动
  `tests/test_social.py::TestCognitionEngine::test_direct_mention_not_blocked_by_sleeping`
  （夹具自身断言 `PresenceResolver.is_sleeping()` 在 `sleep_start=00:00 / sleep_end=23:59` 的当天最后一分钟为 True）；
  **未改其断言、未放宽任何检查**，与本阶段改动无关（算术自洽：4546 + 新增 26 = 4572 = 4571 + 1）。
* `ruff check .`、`ruff format --check .`、`mypy app`、`git diff --check`、凭据扫描、受保护区域审计见交付报告；
  本地门禁脚本与日志保持 untracked（`scripts/` 0 tracked）。

### 14.6 保留边界（如实）

* 结构门是**闭集文法**：它证明「整段只匹配一个动作 + 一个目标短语」，不声称理解任意自然语言；
  因此**过度拒绝是可能的**（更迂回的说法会被澄清而非接受），这是刻意选择的 fail-closed 取向。
* 被澄清的第二个目标**不解析、不执行**，也不新增任何能力（任务书 §2.3）。
* J5 目前只有注入替代证据（第二客户端在那一刻不可用）；J1–J4 有关键类真实客户端证据。
* 本阶段范围外（不变）：8D 导航可达性、配方变体按库存可排序性、真实成功迁移演练、
  `test_social.py` 时段抖动。

### 14.7 裁决

**PASS**。静态审计候选全部被实际测试（4 条确为反例、1 条仅被既有护栏拦下，均给出真实输出）；修复后所有
结构不唯一的输入都不消费旧提案、不触发任何执行入口，并走既有澄清路径；合法单任务（含已审计收尾语）
继续通过；完全回归、静态检查与 CI 通过；权限与执行安全边界未发生变化。

## 15. FU5 Follow-up 5：接受语法的数量 token 与既有数量契约一致性（Acceptance Grammar Conformance）

### 15.1 修复前实测（基线 `4d8776e6f9d53a60a4903f01bcc51d7172ec2440`，**未改生产逻辑**）

§1 的候选在基线上**全部实测为真实缺陷**（真实函数输出，待决提案 `oak_log × 2`）：

| 消息 | `explicit_log_count` | `unexplained_acceptance_tail` | `acceptance_structure_violation` | `handoff_target_verdict` |
| --- | --- | --- | --- | --- |
| `好，两根去砍橡木原木`（数量在动作前） | `2` | `''` | `''`（**漏检**） | **`accept_proposal`** |
| `好，去砍橡木原木带回来两根`（数量在收尾语后） | `2` | `''` | `''`（**漏检**） | **`accept_proposal`** |
| `好，去砍 2026 橡木原木`（裸数字，无单位） | **`None`** | `''`（**漏检**） | `''`（**漏检**） | **`accept_proposal`** |
| `好，去砍橡木原木 2026`（目标后裸数字） | **`None`** | `''`（**漏检**） | `''`（**漏检**） | **`accept_proposal`** |
| `好，去砍两根橡木原木带回来`（合法对照） | `2` | `''` | `''` | `accept_proposal`（正确） |
| `好，去砍三根橡木原木`（冲突对照） | `3` | `''` | `''` | `new_goal`（正确） |

两条独立缺陷：**① 数量 token 的位置约束未被真正执行**（自动机只要「当前状态允许该类别」就放行）；
**② 数量单位可省略** —— `_ACCEPTANCE_COUNT_RE` 的单位组是可选的，而 `_parse_log_count` 要求
`_LOG_COUNT_UNITS` 单位，于是「许可语法把裸数字当数量」与「`parse_wood_request` 认为用户没写数量」
（`requested=1`）出现解释分歧。

### 15.2 修复：数量 token 的单位与位置（`app/tasks/intent.py`）

1. `_ACCEPTANCE_COUNT_RE` 改为**与 `_parse_log_count` 同契约**：数字或中文数字 **必须** 带
   `_LOG_COUNT_UNITS`（`根|个|块|条|截`）单位，单位不可省略；不新增第二套单位列表。
2. `_acceptance_structure_error()` 的数量分支改为显式位置约束：`count_repeated`（已出现过）优先，
   其次只允许前置状态 `action` / `target`（即声明文法中的两处 `[数量]`），其余一律 `count_misplaced`。
3. 文档串补上**类别转移表**（前置状态 / 是否转移 / 可重复次数 / 违反原因码 / 终态条件），使实现与
   声明文法可逐项对照。

修复后实测（同一批消息，`rest = _acceptance_remainder(message)`）：

| 消息 | `unexplained_acceptance_tail` | 结构原因码 | `handoff_target_verdict` |
| --- | --- | --- | --- |
| `好，两根去砍橡木原木` | `''` | `count_misplaced` | `not_accept` |
| `好，去砍橡木原木带回来两根` | `''` | `count_misplaced` | `not_accept` |
| `好，去砍 2026 橡木原木` | `2026` | `unexplained` | `not_accept` |
| `好，去砍橡木原木 2026` | `2026` | `unexplained` | `not_accept` |
| `好，去砍两根橡木原木带回来` | `''` | `''` | `accept_proposal`（合法） |
| `好，去砍三根橡木原木` | `''` | `''` | `new_goal`（冲突） |
| `好，去砍 3 根橡木原木`（带单位） | `''` | `''` | `new_goal`（冲突，与既有数量解析一致） |

裸数字的拒绝由**两级**共同保证：完整消费（tail 非空）与结构门（`unexplained`）；数量错位只由结构门
拦下（`count_misplaced`）。树种/数量冲突的 `NEW_GOAL` 语义与纯接受/合法收尾语行为**零变化**。

### 15.3 自动化验收矩阵（§5 逐项；真实 `_route_turn()` + 真实 `_arbitrate_pending_handoff()`）

`tests/test_phase8i_handoff_route_matrix.py` **191 → 217 条**（+26，原断言零删除、零放宽）：

1. §5.1 数量位置反例 4 条：原因码 + `explicit_log_count` 一致性断言（裸数字两边都不算数量）
   + `handoff_target_verdict == NOT_ACCEPT`；
2. §5.4 重复/错位 token 4 条（`count_repeated` 优先于 `count_misplaced`）；
3. §5.5 负向公共断言 4 条真实路由：`accept_calls == []`、提案仍在 `_pending_handoffs`、
   `handoff_not_accept` 有而 `handoff_accepted` 无、`handoff_compound_clarify` + 非空澄清话术、
   `wood_replies`/`wood_routes`/`delegate_calls`/`capability_calls`/`craft_calls` 全空、`plans.calls == []`；
4. §5.6 跨入口：`TASK_PROPOSAL` + `QUERY`/`ATOMIC_ACTION`（8 条）；
5. §5.7 澄清后恢复：再发纯接受 → 只消费一次（4 条）；
6. §5.2/§5.3 合法与冲突对照各 1 条（合法数量位置消费一次且目标只来自提案；`三根` 走既有 `NEW_GOAL`、
   不消费旧提案）；
7. §5.8 无回归：Follow-up 3（许可集/冲突集/未知尾部）与 Follow-up 4（五条结构反例、机制分离、跨入口、
   真实路由）全部原样通过。

### 15.4 真实第二客户端证据（§6）

本地专用 `scripts/phase8i_handoff_smoke_real.py`（**不发布**）新增场景 **K**：
`CatooBot_backups/_phase8i_fu5fu5_gateK1.log` → **`通过=1 失败=0 BLOCKED=0` / `REAL SERVER: PASS`**。
真实 Java 1.21.1 + 真实 Runtime/MinecraftService + 真实 Provider + 真实待决 `oak_log × 2`。

| 子用例 | 消息 | 投递路径 | 证据 |
| --- | --- | --- | --- |
| K1 | `好，两根去砍橡木原木`（数量在动作前） | **真实第二 Mineflayer 客户端 `IdCraft8G`**（`how=second_client`） | `handoff_compound_clarify`、`targets=[]`、提案保留、澄清话术真实送达、目标物品无变化 |
| K2 | `好，去砍橡木原木 2026`（目标后裸数字） | **真实第二 Mineflayer 客户端**（`how=second_client`） | 同上 |
| K-对照 | `好，去砍橡木原木`（纯接受） | 同上 | 提案**恰好消费一次** |

**本次无替代注入证据**（场景 K 两条均由真实第二客户端投递，日志无 `DEVIATION`）；替代注入证据仍见
§14.4 的 J5（历史记录）。世界自然掉落照旧全量记录（全程 `{'rotten_flesh': 1}`），断言只针对目标相关物品。

### 15.5 质量门禁

* `pytest tests -q` → **4598 passed, 0 failed**（4572 + 新增 26）。本时段**未触发**既有已知时段抖动
  `tests/test_social.py::TestCognitionEngine::test_direct_mention_not_blocked_by_sleeping`；该抖动在上一轮
  （§14.5）为唯一失败项，**其断言未被修改或放宽**，本地结果与 CI 结果分别记录。
* `ruff check .`、`ruff format --check .`、`mypy app`、`git diff --check`、凭据扫描、受保护区域审计见交付报告；
  本地门禁脚本与日志保持 untracked（`scripts/` 0 tracked）。

### 15.6 保留边界（如实）

* 结构门仍是**闭集文法**：它保证「整段匹配一个动作 + 一个目标短语 + 至多一个带单位数量」，不声称理解
  任意自然语言；**过度拒绝是可能的**（例如数量在动作之前的自然说法会被澄清），这是刻意 fail-closed。
* 数量文法只覆盖既有 `_LOG_COUNT_UNITS` 的五个单位与既有数字/中文数字集合；不新增单位或量词。
* 被澄清的表达**不解析、不执行**，也不新增任何能力。
* 本阶段范围外（不变）：8D 导航可达性、配方变体按库存可排序性、真实成功迁移演练、`test_social.py` 时段抖动。

### 15.7 裁决

**PASS**。§1 每条候选都在基线被实测（4 条确为反例，附真实函数与仲裁输出）；数量 token 只有在语义、

## 16. R0 最终闭环：交接仲裁、导航可靠性、证据链与迁移演练（本轮新增）

任务书：`docs/specs/CatooBot Minecraft R0 —— Handoff Arbitration, Navigation Reliability, Evidence Reconciliation & Migration Drill.md`
（38919 bytes，SHA256 `9FB3E8E64CC43722B3E133E3CEE8BE254AA4D9685B20602B3E617A9496B6C4D1`，逐字节归档）。
开工基线 `67ad74d976689a73a98f736cd2d46af9469b3950`。本节**追加**，§10–§15 的历史结论原样保留。

### 16.1 R0.1 修复前实测（先用生产函数最小复现，不预设失败）

待决提案 `oak_log × 2`，§3.2 的四条候选在基线 `67ad74d` 上的真实机制（`classify_handoff_reply` →
`handoff_target_verdict` → 复合/结构门 → `_arbitrate_pending_handoff` → `_route_turn` 效果）：

| 消息 | classify | target_verdict | tail | 结构 | 真实路由效果（修复前） |
| --- | --- | --- | --- | --- | --- |
| `好，去砍三根橡木原木再砍橡木原木` | accept_proposal | new_goal | `''` | `second_action` | 旧提案作废后**整条消息交给木材解析器** |
| `好，去砍五根白桦原木，然后去挖石头` | accept_proposal | new_goal | `然后石头` | `unexplained` | 同上（`wood_routes=[整句]`、`delegate=1`） |
| `好，给我做 64 块橡木木板，然后去砍橡木原木` | **new_goal**（`_is_new_craft_request` 早退） | not_accept | None | `not_wood` | **静默只合成**，丢掉「然后去砍橡木原木」 |
| `好，去砍五根白桦原木去挖石头` | accept_proposal | new_goal | `石头` | `unexplained` | 整条消息交给木材解析器 |

即两条 `NEW_GOAL` 路径（分类器早退 + 接受分支冲突）**都绕过**复合/结构门，§3.1(5) 明确禁止。
`QUERY`/`ATOMIC_ACTION` 入口同样落入能力入口（`capability_calls=1`），即跨决策类别也能绕过。

### 16.2 R0.1 修复（确定性、闭集、fail-closed，不新增执行器/权限/状态机）

1. `handoff_mixed_intent(message)`（`app/integrations/minecraft/chat_bridge.py`）：复用既有
   `has_compound_connector`/`_has_second_clause_goal`/`_has_trailing_goal`/
   `acceptance_structure_violation ∈ {second_action, second_target}`，并对未解释尾部要求出现
   **目标词**（`_RESOURCE_WORDS`/`_ACTION_VERBS`）才算第二目标 —— 因此 `同意，改砍白桦` 这类
   干净新目标（残留只是填充词「改」）仍走自己的路由，不被降级成澄清。
2. 两条 `NEW_GOAL` 路径都先**作废旧提案**（§3.1(5)），混合消息再返回**稳定原因码**
   `handoff_conflict_compound_clarify` + `conflict_compound_clarify_text()` 澄清（不执行任何子目标）；
   干净单一冲突新目标仍记 `handoff_new_goal` 并走既有新目标路径。
3. 两处**真实缺陷**（本轮真机门禁直接暴露）：
   * `_task_reply` 在「模型输出无效」时提前返回澄清，**根本不进 `_route_turn`** → FU4 的确定性
     仲裁被跳过：待决提案 + `同意` 无法被消费（接受语义被模型可用性绑架）。已改为**先跑真实仲裁**
     再决定是否澄清（`CANCEL`/`STOP` 已由第 1 步确定性控制处理，不会走到这里）。
   * `_control_turn` 命中「确认」词但没有待确认事项时返回**空 `_TurnReply()`**（`reply=None`）→
     `_task_reply` 直接返回它，用户只看到角色闲聊。已改为 `_delegate_task()` 在任务入口**明确没处理**
     时返回 `None`；`_control_turn` 对「只是 confirm 命中」的回落继续走仲裁与统一决策，而
     **stop/cancel/pause/resume 依旧绝不回落给模型**（既有 `test_stop_does_not_wait_for_a_hanging_provider` 继续通过）。

### 16.3 R0.2 导航失败的可诊断状态与证据链修复

审计结论（既有实现已按 9 类状态区分，无需重写导航）：

| 状态 | 既有确定性机制 |
| --- | --- |
| 目标/实体选择错误或歧义 | 工具/服务校验（`action.invalid` → `VALIDATION`）+ 规划期真实观察 |
| 观察过期 / 目标丢失 | `block.not_found`/`block.changed` → `TARGET_LOST`/`WORLD_CHANGED` |
| 无可用路径 | `path.not_found` + `detail.reason=no_path` |
| 路径规划成功但执行失败 | `path.not_reached`（`empty_path_without_arrival`/`path_stopped_short`/`goal_reached_without_arrival`） |
| 移动超时 | ActionRuntime `minecraft.action.timeout` → `TaskFailure.TIMEOUT`；路径搜索超时为 `path_search_timeout` |
| 已在 `goal_near` 半径内 | `moveArrival.reached`（重新读取的实际位置，不采信 pathfinder resolve） |
| 到达附近但后置条件不满足 | `MinecraftMoveUnconfirmedError`（`minecraft.move_unconfirmed`） |
| 暂停/取消/STOP | `TaskState.PAUSED`（`pause_requested`/`_pause_after_failure`）与 `CANCELLED` |
| 迟到/重复事件 | `_settled_action_ids` + `terminal_record`：只收敛证据，绝不复活 |

**发现的真实缺陷（本轮修复）**：JS 运行时把结构化失败 `detail`（目标/实际坐标/距离/半径/`reason`）
放进 `minecraft.action.*` 事件，但 Python 边界 `task_coordinator._handle_terminal` **只转发
`result`/`error`/`code`，丢掉了 `detail`**，`TaskRuntime.on_action_event` 也没有该参数 → 任务记录只剩
一句 message，**无法区分 `no_path` 与 `path_search_timeout` 等状态**。已按 §4.2「补最少必要诊断」修复：
协调器转发 `detail`，运行时写入 `step.result['detail']`（纯新增键，任务记录本就整份存 JSON，**无 schema/迁移**）。

### 16.4 真实服务器门禁证据（本地脚本，**不发布**）

脚本 `scripts/r0_final_smoke_real.py`（本地专用）；日志全部在 `CatooBot_backups/`：

| 场景 | 结果 | 日志 |
| --- | --- | --- |
| R1 四类仲裁（R1a 纯接受 / R1b 同目标复合 / R1c 冲突复合 / R1d 干净冲突） | **4/4 PASS** | `_r0_gate_R1f.log` |
| R2 N1 真实可达移动（不传送） | **PASS**：真实位移 7.77 格、终态 `SUCCEEDED`、独立核验 `oak_log` 净增 1 | `_r0_gate_R2b.log` |
| R2 N3/N5 真实路径失败 | **PASS**：`reason=path_search_timeout`、`distance_to_target=2.83`、`radius=1.5`、`goal_reached=false`、任务 `PAUSED`、后续步骤全 `PENDING`、库存不变 | `_r0_gate_R2e.log` |
| R2 N6 移动中 STOP | **PASS**：`WAITING_ACTION → CANCELLED`，6 秒后仍 `CANCELLED`（不复活），无在途动作 | `_r0_gate_R2b.log`、`_r0_gate_R2e.log` |

* R1 的 R1a/R1b/R1d 由**真实第二个 Mineflayer 客户端 `IdCraft8G`** 投递（`how=second_client`）；
  R1c 用真实聊天桥事件注入（`how=bridge_injection`，**替代证据**，如实标注 DEVIATION）。
* N1 全程**没有**传送罐头（`/tp` 只用于把 op 客户端 `CatodayoMate` 送到目标区块加载世界）；
  独立位置核验 = 执行前后各一次 `service.world_view()` 的真实坐标读回。
* 门禁脚本对夹具自检（op 客户端 `/setblock` 回执偶发 `PartialReadError`）只如实记录，
  断言落在端到端真实事实（真实移动/独立库存净增/计划目标来自有界真实观察）。

### 16.5 R0.4 真实迁移演练（隔离副本，真实旧版本样本）

* 样本：`CatooBot_backups/catoobot-pre-v18-20261001.db`（1224704 bytes，
  SHA256 `44DAB4F83220E43A2A1289EAEF60448148EBE78E9BCA6E188B98EA9B0BDF9AAF`，`integrity_check=ok`）。
* 复制到隔离目录后再迁移（生产库全程只读、未改动）：**version 13 → 36**、tables **58 → 83**、
  `integrity ['ok','ok']`、`lost_rows {}`、`schema_evidence_ok true`、`changed_count 526`、
  `status "migrated"`、退出码 0；第二次运行 → `version [36,36]`、`status "no_op"`、退出码 0（不二次破坏）。
* 结果：**R0.4 PASS**（有真实版本上升与迁移报告、完整性/数据保留证据）。

### 16.6 本轮新增测试（旧断言零删除、零放宽）

* `tests/test_r0_handoff_arbitration_priority.py`（4 条）：模型输出无效时待决仲裁仍必须先跑；
  `CANCEL/STOP` 的确定性控制仍优先于仲裁。
* `tests/test_r0_navigation_evidence.py`（6 条）：失败 `detail` 落到 `step.result`；超时与路径失败区分；
  失败后不派发后续世界动作；迟到/重复终态事件不复活；协调器转发 `detail`。
* `tests/test_phase8i_handoff_route_matrix.py`：191 → **217** 条（R0 冲突复合矩阵 26 条）。

### 16.7 保留边界（如实）

* R0.2 收敛的是**已知历史失败**的可诊断性与安全停止；长距离导航可靠性（8D 能力域）与
  「配方变体按库存可满足度排序」仍是独立后续事项 —— N3 的真实失败（2.83 格、半径 1.5、
  `path_search_timeout`）说明**即使很近也可能规划超时**，本节不把它当作已解决。
* `handoff_mixed_intent` 仍是**闭集文法**判定：只覆盖既有解析器能识别的第二目标信号；
  无法证明时一律澄清（不消费、不执行）。
* 结构/数量/单位契约、跨用户/session/TTL、拒绝不复活等既有语义未变。单位、
数值与位置均符合既有契约时才参与接受，裸数字与错位/重复数量不再被静默消费；合法纯接受、相容单目标描述与
既有收尾语继续正常工作，明确树种/数量冲突的 `NEW_GOAL` 语义不变；结构负向用例在真实仲裁中保留提案、
返回澄清、执行入口零调用，跨决策类别与 STOP/CANCEL/双确认契约无回归；本地全量测试与 CI 结果完整报告
（无隐瞒失败）；关键新增反例 K1/K2 有**真实第二客户端**路径证据；权限模型与受保护区域未变。
---

## R1 交叉引用：导航失败的历史记录已被根因修复覆盖（追加，不改写上文）

本阶段记录过的「约 6.8 格 `minecraft_move_to` 失败、任务 PAUSED」以及 R0 记录的「2.83 格
`path_search_timeout`」在 R1 被定性并修复：根因是 **`path_update(status='timeout')` 被立即判失败**，
而 pathfinder 的 `thinkTimeout` 只是**累计搜索预算**（超预算时仍持有可用路径）。R1 之后这类可达场景会继续走完，
失败只在真正没有可用路径或动作整体超时发生，并且**两种终态都会带结构化 detail**
（`action_timeout` / `action_timeout_after_path_search_timeout`）。

R1 未改动本阶段的任何交接/合成/授权语义；本文的历史实验记录保持原样。
