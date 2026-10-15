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
* 循环用「正在展开栈」检测（`recipe_cycle`）—— **按当前递归路径**判定，绝不用全局 visited；
* 记忆化的**真实范围**（Phase 8H Follow-up 对齐）：只有「这个物品**根本没有候选配方**」会按
  `(item, amount)` 缓存 —— 它的结论不依赖库存、跨分支复用安全；**成环与超深是路径相关的、
  不跨分支缓存**，但它们在候选尝试**之前**抛出，所以不消耗搜索预算；候选尝试次数由集中上限
  `max_recipe_search_states` 硬性约束（超限即 `search_budget_exceeded`）；
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

---

## 8. Phase 8H Follow-up 结论（追加，**不改写上文历史**）

> 归档任务书：`docs/specs/CatooBot Minecraft Phase 8H Follow-up — Complete Schema Drift Detection & Search Memoization Contract.md`
> （13024 bytes，SHA256 `763445588E702BFF9A44DA6F730CCE1C65474CCECB998AD8093E128371C9AED0`，265 行，逐字节归档核验）

### 8.1 F1：完整、可信的 SQLite schema 漂移检测

**旧实现的真实检测范围**（先承认）：只有「版本不变 + `added_tables` 非空」这一条 —— 它**证明不了**没有别的
schema 变化，而且删掉一张**空表**时连 `lost_rows` 都抓不到。

**新增**（`app/database/migration_report.py`）：`schema_snapshot(connection)` / `schema_snapshot_of(path)` /
`schema_fingerprint(snapshot)` / `schema_diff(before, after)`：

* 数据**只来自真实 SQLite 元数据**（`sqlite_master` + `PRAGMA table_info`），覆盖 table / index / trigger / view
  四类对象，外加每张表每一列的类型/非空/默认值/主键位；
* 规范化 = 折叠空白 + 去结尾分号（确定性，且不做会制造假阴性的过度改写）；
* 明确排除 SQLite 内部对象：`sqlite_%` 名字与 `sqlite_autoindex_%` 自动索引（它们跟着 UNIQUE/PRIMARY KEY
  约束走，正常变化不算产品漂移）；`schema_diff` 只记对象名/方向，不写整库内容；
* 快照为空或读取失败 → `SchemaSnapshotError`：**绝不静默降级成合法 `no_op`**；**绝不只比表数量**。

**三态契约收紧**（实现/文档/测试一致）：`no_op` = 版本不变 + 前后完整性 ok + 无丢行 + **schema 证据可用且无变化**；
`migrated` = 版本上升（**预期内的 schema 变化不算错**）；`failed` = 完整性异常 / 丢行 / 版本倒退 /
**版本未变却出现 schema 变化** / **必需证据采集失败**。报告字段保持兼容，并新增 `schema_evidence_ok`、
`schema_changed`、`schema_changed_without_version_bump`、`schema_diff`。

本地演练脚本（`scripts/migrate_db.py`，**不发布**）在迁移前后各采集一次真实快照并传给三态判定；采集失败即
`schema_evidence_ok=False` → `failed`。

**真实证据**（真实库副本 `catoobot-20261015-184006.db`，83 表、v36）：

* no-op 演练：`schema_evidence_ok: true`、`schema_changed: false`、`status: "no_op"`、`ok: true`、**退出码 0** ✓
* 同一真实副本上给 `activity_episodes` 加一列（版本保持 36）：`changed_count=2`、
  `schema_changed_without_version_bump: true`、`status: "failed"`、`ok: false` ✓

**测试**：`tests/test_phase8h_followup_schema_drift.py`（11 条，全部操作**隔离的真实临时 SQLite 库**：
no_op / 版本上升 + 预期变化 / 新增表 / 新增列 / 表定义变化 / 删除空表 / 索引变化 / 视图与触发器变化 /
证据缺失 → failed / 内部对象不误报 / 文件快照确定性）+ 既有迁移状态测试更新到新契约（10 条）。

**检测范围与已知边界**（如实声明，不宣称「完整」）：本实现比较的是 SQLite **可观察的 schema 元数据**
（对象定义 + 表列信息），覆盖表/列/索引/触发器/视图的新增、删除与定义变化；**不**声称能发现
「定义文本完全等价但页级/物理布局不同」的情形（例如 `VACUUM` 后的物理差异、纯数据内容变化）——
那些不属于 schema 漂移，也不在本阶段范围内。

### 8.2 F2：搜索记忆化与文档对齐

**审计结论**（真实代码为准）：`craft_plan.py` 里 `impossible` 缓存**只在「这个物品根本没有候选配方」时写入**；
成环与超深在**候选尝试之前**抛出，因此**不消耗搜索预算**，也不跨分支缓存（它们本质是路径相关的）；
候选尝试次数由集中上限 `max_recipe_search_states` 硬性约束。结论：**实现本身是有界且正确的**，
但 §2.3 的文档把「没有候选 / 成环 / 超深」都写成会记忆化 —— 与实际不符。

**最小处理**（任务书 §3.2 允许）：**只改文档**使之与实现一致（明确记忆化的真实范围、路径相关的失败不缓存、
预算是按候选尝试计数），并补针对性测试证明：同一无配方子问题只查一次且失败稳定（候选集合缓存）；
成环/超深不吃搜索预算；不同库存下可行性结论不共享（绝不把「库存不足」缓存成永久结论）；
候选失败后库存/节点/`requires_table`/累计输入**完整回滚**（成功计划与只给可行候选时逐字段一致）；
预算按每一次候选尝试计数，超限只给稳定原因码 `search_budget_exceeded` 且**绝不返回部分计划**。
**没有为改代码而增加复杂度，也没有放宽预算或改变计划正确性。**

### 8.3 本阶段重新执行的检查

* 重新执行：`pytest tests -q`（全量）、`ruff check .`、`ruff format --check .`、`mypy app`、`git diff --check`、
  真实库副本 no-op 演练、真实副本漂移模拟、CI 两个 Job。
* 仅引用历史证据（未重跑）：真实 Minecraft 服务器合成门禁 —— 本阶段**没有改动**任何影响正式合成执行的代码
  （改动只在迁移报告模块与测试；`craft_plan.py` 只改了文档，代码未动），故不重跑真机门禁。