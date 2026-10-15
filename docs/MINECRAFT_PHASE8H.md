# Phase 8H — 库存感知配方变体解析与有界计划搜索

> 归档任务书：`docs/specs/CatooBot Minecraft Phase 8H — Inventory-Aware Recipe Variant Resolution & Bounded Plan Search.md`
> （21735 bytes，SHA256 `8C2A12EA933E3E09D59DB0B7ED425B07DB8C02839E87BC3896780E12A0D66A4B`，451 行，逐字节归档核验）
> 开工基线：`af467e591382c2c6274201f40ed77f6ea1ccf300`（= Phase 8G Follow-up 收尾）

## 1. 问题与最小复现

8G Follow-up 的真实证据：请求 `wooden_pickaxe`，真实工作台查询返回 11 个变体，背包没有木板
（有橡木原木与木棍），所有变体 `available: false`。旧排序 `(not available, requires_table, recipe_id)`
**所有分量都相等**，于是退化成 `recipe_id` 字母序 → 先选 `!wooden_pickaxe*1=acacia_planks*3+stick*2`
→ 计划层如实报材料不足。

关键事实：**`available: false` 不等于该配方在受限 DAG 内不可实现**。它只说明「当前观察下不能直接满足
这份配方的即时材料需求」；橡木木板完全可以由背包里的橡木原木按真实配方做出来。

最小复现（单元级，`tests/test_phase8h_recipe_search.py`）：候选 `[金合欢, 竹子, 橡木]` 都 `available=false`，
背包 `{oak_log: 3, stick: 2}` + 真实橡木木板配方 → 旧实现选金合欢并失败；新实现回滚前两个候选、
选中橡木，计划冻结为 `[oak_planks*4=oak_log*1, !wooden_pickaxe*1=oak_planks*3+stick*2]`。

## 2. 实现（H1）

### 2.1 候选来源（只来自真实查询）

`app/tasks/planner.py`：新增 `craft_candidates(result, item, *, allow_table)` —— 从**真实**
`minecraft_recipe_lookup` 返回里取出**全部**可用候选（2×2 与 3×3 都保留），返回 `(候选元组, 原因码)`；
顺序是确定性的（真实 `available` → 不需要工作台 → `recipe_id`）但**只是尝试顺序**。
`craft_recipe_of()` 保留为兼容包装（取第一份），既有调用方与测试不受影响。

工作台四态语义沿用 8G Follow-up 的既有路径：不带工作台的查询命中 `crafting_table_required` 时，
**先真实观察一张工作台**，不可用即按六态如实拒绝，可用则用那张桌子的坐标重查并保留全部候选。

### 2.2 有界、可解释的候选搜索（`app/tasks/craft_plan.py`）

* `lookup` 接受单份（8F/8G 调用方）或**多份**候选（`_as_candidates` 规范化 + 按 `recipe_id` 去重）；
* 每个物品的候选按固定顺序尝试，配合**库存/节点/工作台标志的快照与回滚**：候选失败即回滚，
  换下一个候选继续 —— 只有**完整可行**的路径才会产出计划；
* 根目标的多份候选**各自完整求值**，可行的计划按任务书 §2.5 的确定性优先级排序选一份；
* 全部候选都不可行时，用 `_ERROR_PRIORITY` 挑一个**真实**的失败原因上报（材料不足优先，
  且 detail 里就是缺什么、缺多少），绝不用字母序第一个候选伪造可执行 DAG。

### 2.3 搜索预算与确定性（§2.4）

* 上限来自 `CraftBudget.max_recipe_search_states`（默认 **256**，集中式，就在预算模块里）；
* **一个搜索状态 = 对某个物品的某一刀配方的一次候选尝试**（含根候选）；
* 超限 → `CraftPlanError(CRAFT_PLAN_SEARCH = search_budget_exceeded)`，**明确拒绝**，
  绝不静默退化成字母序选择后继续制作；
* 循环用「正在展开栈」检测（`recipe_cycle`）；与库存无关的失败（没有候选 / 成环 / 超深）会
  按 `(item, amount)` 记忆化剪枝，重复子问题不再重复展开；
* 候选顺序、根候选求值顺序与排序键都是纯函数式确定的 → 同输入 + 同候选 + 同库存 = 同计划
  （`test_identical_inputs_produce_identical_plans`）。

### 2.4 候选排序（§2.5，`_plan_key`）

只对**完整可行**的计划排序：`(是否被夹取, 合成刀数, 总输入量, 是否需要工作台, 规范化 recipe_id 序列)`。
即：能完成全部目标 → 额外步骤更少 → 输入更少（浪费更少）→ 前置更少（优先 2×2）→ 稳定决胜。
任何缺材料、工作台不满足、预算超限或依赖不合法的候选都不会成为可执行计划。

## 3. 库存模拟与冻结（H2）

沿用 8G 的带库存模拟需求求解，并保持 8H 加深的语义：

* 初始真实库存是**唯一**基线（`inventory_before`）；同名需求合并，后来者先吃前面剩下的库存；
* 由本计划造出来的东西只有**剩余部分**留在库存里（父节点要用的那份已被消耗）—— 少扣这一笔会把
  「同一物品既是材料又是产物」的净变化算错；
* 根目标是用户**要的东西**，产出的每一份都留在库存里（不扣 amount）；
* 整数刀数的余量照实进净值（例：1 根原木 → 4 块木板，木镐吃 3 块，净增木板 +1）；
* 计划冻结：真实 `recipe_id` 序列、依赖（`depends_on`）、工作台坐标、逐步预期库存前后条件、
  `plan_hash`（8G 已把逐步后置条件纳入哈希）；执行链与逐步后置验证**未改**。

## 4. 迁移三态边界复核（H3）

审计 `app/database/migration_report.py` 后**收紧**契约（原实现只看 `integrity_after`/版本/丢行，
存在「版本不变但表结构变化 → 被报成合法 no_op」的误报路径）：

* `failed`：**迁移前或后**完整性不是 ok；行数减少；版本**倒退**；或**版本没变却观察到表结构变化**
  （未经迁移的 schema 变更不能当成合法 no-op，报告里另有 `schema_changed_without_version_bump` 标志）；
* `migrated`：版本上升（真的执行了迁移），且前后完整性 ok、没有丢行；
* `no_op`：以上都不是且版本未变（没有待执行迁移，**合法 no-op**）；
* `integrity_before` 参与判定：在一份本来就损坏的库上「演练成功」不能算健康结论。

无新增 schema/字段/迁移；调用方只有本地 `scripts/migrate_db.py` 与测试，均同步更新。
真实库副本演练仍为 `status: no_op`、`ok: true`、退出码 0。

## 5. 测试矩阵（H5）

新增 `tests/test_phase8h_recipe_search.py`（8 条）：库存感知变体选择、全部不可行的真实原因、
搜索预算超限、成环拒绝、候选去重、确定性、刀数更少优先、2×2 优先。
`tests/test_phase8g_followup_migration_status.py` 扩到 10 条（新增：版本不变但有新表 = failed、
入口库不完整 = failed、版本上升 + 新表 = migrated）。
§6.1 其余条目由既有 8F/8G 套件覆盖（2×2/3×3 选择、工作台六态阻断、同名材料合并、
跨分支库存共享、循环/深度/步骤/输入超限、伪造 recipe_id 不改变运行时事实、
确认前零副作用、逐步后置验证、取消与迟到事件）。

## 6. 真实门禁（H6）

见 §7（真实 Provider + 真实服务器 + 第二个真实 Mineflayer 客户端；脚本本地专用，不发布）。

## 7. 门禁结果

环境：真实 Java 1.21.1 服务器 + 真实 CatooBot Minecraft Runtime（真实 Mineflayer 客户端，用户名 `Catodayo`）
+ 真实 Provider（模型 `primary`）+ **第二个真实 Mineflayer 客户端**在游戏内发言（**自动化客户端，不是真人**）。
脚本本地专用（`scripts/phase8g_craft_smoke_real.py`，不发布）；原始日志 `CatooBot_backups/_phase8h_gate2.log`。

### 场景 A：所有直接变体不可用，但前置合成可行（任务书 §7 场景 A）

* 真实前置（op 客户端，与产品执行链分离）：`/clear oak_planks` + `/give oak_log/stick`
  → 真实背包 `{oak_log: 18, stick: 18, oak_planks: 0}` ✓（木板不足、原木与木棍够）
* 请求（第二个客户端）：`在工作台上做一把木镐`；真实 Provider 判定 `kind=task_proposal`（受管路线）。
* **冻结计划（全部来自真实查询，未硬编码）**：
  1. `oak_planks*4=oak_log*1` —— 2×2 前置步骤，**不需要工作台**（所以该步没有工作台坐标是正确的）；
  2. `!wooden_pickaxe*1=oak_planks*3+stick*2` —— 3×3，工作台坐标 `{'x': -265, 'y': 69, 'z': -140}`
     来自真实观察（8 个候选里最近的那张）。
* 确认前零副作用；经既有确认门执行；逐步后置验证 `['step_1', 'step_2']` 全部 ok。
* **独立真实背包核验**：`oak_log 18 → 17`（−1）、`oak_planks 0 → 1`（+1 = 整数刀数余量）、
  `stick 18 → 16`（−2）、`wooden_pickaxe 3 → 4`（+1）—— 与冻结的逐步预期完全一致 ✓

### 其余场景与整体结论

* 场景 B（工作台缺失的安全对照）：真实观察后如实拒绝，零任务、零世界修改 ✓
* 场景 D（取消：确认阶段 D1 + 执行中 D2）：PASS ✓
* 场景 E（材料被外部消耗）：任务 `PAUSED` / `ACTION_FAILED`，不宣告成功、不重试 ✓
* 场景 1/2/3（真实世界查询、真实背包与配方读取、既有 2×2 合成闭环）：PASS ✓
* 整体：`通过=7 失败=0 BLOCKED=0`，**`REAL SERVER: PASS`** ✓
