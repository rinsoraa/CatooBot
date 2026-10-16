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