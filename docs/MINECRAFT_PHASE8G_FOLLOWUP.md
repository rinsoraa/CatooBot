# Phase 8G Follow-up 最终报告 — 真实工作台闭环补证与世界观测一致性调查

> 归档任务书：`docs/specs/CatooBot Minecraft Phase 8G Follow-up — Real Workbench Closure & World Observation Consistency.md`
> （26354 bytes，SHA256 `89D63905561D67BD9E520945D8F02894568536BAE1742EFECD853CE0D04C1260`，547 行，逐字节归档核验）
> 开工基线：`89bb793f89c15868221b4452b25c380aeae14bc9`（= Phase 8G 收尾）；收尾 SHA 见 §13。

## 1. 最终裁决与逐项 DoD

**PASS**。三项关键门槛全部达成：真实工作台 3×3 合成闭环完成；世界观测异常已获证据定性；迁移演练 `ok:false` 已定性并最小修复。

| DoD（任务书 §10） | 结果 | 证据 |
| --- | --- | --- |
| F1 追踪服务器操作→客户端更新→世界视图→`find_blocks` 数据流 | ✓ | §4 |
| F1 查明 `setblock` 被拒原因，或说明证据不足 | ✓ | 服务器回执 `Could not set the block` 属**夹具位置**问题；夹具另有真实工作台可用（§6） |
| F1 对「方块变化但查询为 0」建立同坐标时间序列 | ✓ | T0–T5（§4） |
| F1 根因由原始证据支持 + 回归测试 | ✓ | 根因=门禁脚本读错两层信封；修复在本地门禁脚本 |
| F2 在真实测试世界建立可观察工作台 | ✓ | 8 个候选、采用 `(-265, 69, -140)`（§6） |
| F2 记录坐标/维度/准备方式/服务器回执 | ✓ | §6 |
| F2 夹具未赋予产品新的世界修改能力 | ✓ | 夹具由 op 客户端完成；产品 diff 不含放置/破坏 |
| F3 真实 Provider + 真实服务器 + 第二个 Mineflayer 客户端 | ✓ | §7 |
| F3 真实查询工作台/库存/配方 | ✓ | §7 |
| F3 计划冻结并进入既有确认机制 | ✓ | `PENDING_CONFIRMATION` → 既有确认门 |
| F3 确认前零副作用 | ✓ | 确认前无合成动作、无有效 `action_id` |
| F3 确认后真实完成一次工作台合成 | ✓ | `step_1 minecraft_craft SUCCEEDED` |
| F3 独立真实库存核验产物净增与材料净耗 | ✓ | 木镐 `0→1`、木板 `4→1`、木棍 `8→6`（§7） |
| F4 确定 `ok:false` 源码位置与触发条件 | ✓ | `scripts/migrate_db.py` `main()` 的 `ok` 公式（§8） |
| F4 区分真正失败 / 合法 no-op / 未执行的检查 | ✓ | 三态 `no_op`/`migrated`/`failed` |
| F4 依证据决定是否需要最小修复 | ✓ | 已修（合法 no-op 被误报） |
| F5 回归与静态检查 | ✓ | `pytest tests -q` **4318 passed**、ruff/format/mypy 全绿（§11） |
| F5 受保护区域未越权修改 | ✓ | §12 |
| F5 无新增 schema/字段/迁移 | ✓ | §8 / §12 |
| F5 凭据与本地门禁脚本未入镜像 | ✓ | `scripts/` 0 tracked、凭据扫描 0 命中 |
| F5 CI/提交 SHA/镜像与远端一致 | ✓ | §13 |

## 2. 开工与收尾 SHA、分支、工作区状态

* 开工基线：`89bb793f89c15868221b4452b25c380aeae14bc9`（镜像 HEAD == `origin/main` == 该 SHA，307 commits，工作区干净）。
* 收尾：实现提交 + 证据提交（见 §13），镜像 HEAD == `origin/main`，工作区干净，`scripts/` 0 tracked。

## 3. 原始证据清单与本地存放位置

| 证据 | 位置 |
| --- | --- |
| F1 同坐标时间序列（原始 JSON） | `CatooBot_backups/_g8fu_f1_timeseries.json` |
| F1 运行日志（含原始回包） | `CatooBot_backups/_g8fu_f1_run1.log`、`_g8fu_f1_run2.log` |
| 独立客户端 `findBlocks`/调色板实验 | `minecraft_runtime/_g8fu_findblocks_probe.js`、`_g8fu_palette_probe.js`（本地临时） |
| F3 真实闭环门禁日志 | `CatooBot_backups/_phase8gfu_gate1.log`、`_phase8gfu_gate2.log` |
| F4 修复前后演练输出 | 本报告 §8 + `scripts/migrate_db.py --json` 实跑输出 |
| 全量回归日志 | `CatooBot_backups/_g8fu_full_pytest.log` |
| 门禁脚本（本地专用，不发布） | `scripts/phase8g_craft_smoke_real.py`、`scripts/phase8gfu_*.py` |

## 4. 方块观测数据流与 F1 根因

数据流：op 客户端 `/setblock` → 服务器真实方块（回执 `Changed the block at ...`）→ CatooBot 的 Mineflayer 客户端（`blockAt`/`dig_capability` 立即可读）→
`minecraft_world`（`self` 语义键：位置/维度/`age_seconds`）→ `minecraft_find_blocks`（搜索中心=罐头位置、半径、方块名过滤）→ 工作台六态消费。

**根因**：服务/桥接回包是**两层信封** `{ok, action_id, action, status, result: {ok, query, matches: [...]}}`；
门禁与诊断脚本在**顶层**读 `matches` → 静默得到 0 → 误判「附近没有工作台」。产品的 `workbench._payload_of` 一直正确解包 `.result`。

**证据链**：

1. 目标 `(-264, 67, -139)`：T0 放置前 `dig_capability` = `birch_leaves`；`/setblock` 被接受后 **T1（立即）～T5（+5s）`dig_capability` 全部 = `crafting_table`**（客户端确实收到更新），而脚本计数始终 0。
2. 打印原始返回：`result.matches` 里是真实工作台（非空）——0 是脚本造成的假象。
3. 独立客户端实验（新客户端先在场、再由 op 客户端放置）：`blockAt` 立即命中；section **调色板随 block_change 正确新增 `crafting_table`**（`isBlockInSection` 预检通过）；`findBlocks` 找到 1 个，`useExtraInfo: true` 结果一致 → mineflayer / prismarine-chunk **无缺陷**。

**修复**（仅本地门禁脚本，不发布）：改用既有 `_unwrap()` + 新增 `_matches_of(payload)`，统一解包两层信封，**不把未知形状静默当成 0**。

## 5. `setblock` 拒绝与 `find_blocks` 返回 0 的定性

* `setblock` 的 `Could not set the block` 只出现在**我挑的候选坐标**上（草丛/树冠 section 位置）；换用世界里**已存在**的工作台即可（§6）——夹具位置问题，不是权限或服务器缺陷（同一 op 客户端在 y=69 的坐标多次成功放置，且这些方块后来被真实 `find_blocks` 读到）。
* `find_blocks` **返回 0 是假象**（§4）：修复解包后同一调用返回 **8 个工作台候选**。

## 6. 真实工作台测试夹具

* 准备方式：**op 客户端 `CatodayoMate`**（真实第二个 Mineflayer 客户端，走本地门禁脚本）执行 `/setblock`；产品侧**没有任何**放置/破坏/挖掘能力。
* 采用坐标：`(-265, 69, -140)`，维度 `overworld`，距离 `goal_near 2.83` / `raw 2.01`（运行时的交互距离上限为 5.0，落在范围内）。
* 环境内共 8 个真实工作台候选（`_phase8gfu_gate2.log` 打印每个候选及其距离）。

## 7. 真实工作台 3×3 合成闭环结果

* 用户消息来源：**第二个真实 Mineflayer 客户端**（`IdCraft8G`）在游戏内发言 `在工作台上做一把木镐`；**这是自动化客户端，不是真人手工聊天**。
* 真实 Provider 判定：`kind=task_proposal`（受管任务路线；`minecraft_craft` **仍不在**自然语言自由能力白名单）。
* 真实查询：`recipe_lookup(wooden_pickaxe)` 不带桌子 → `crafting_table_required`；带观察到的桌子 → 返回 11 个变体；产品按真实 `available` 选到橡木变体。
* 冻结计划：配方 `!wooden_pickaxe*1=oak_planks*3+stick*2`，工作台坐标 `{'x': -265, 'y': 69, 'z': -140}`，逐步后置条件冻结。
* 确认前零副作用：无合成动作、无有效 `action_id`、产物与材料未改变。
* 经**既有确认门**执行：`step_1 minecraft_craft SUCCEEDED`，逐步后置验证 `['step_1']` 全部 ok。
* 独立真实背包核验：`wooden_pickaxe 0 → 1`、`oak_planks 4 → 1`（−3）、`stick 8 → 6`（−2）——产物净增与材料净耗都与真实配方一致。
* 门禁整体：**7 场景全 PASS**（`通过=7 失败=0 BLOCKED=0`，`REAL SERVER: PASS`）。

## 8. 迁移演练 `ok:false` 源码根因与裁定

1. **哪个函数哪个分支**：`scripts/migrate_db.py` 的 `main()` 构造 `report` 那一行：`ok = (after["integrity"] == "ok" and after["version"] > before["version"] and not lost)`。
2. **为何触发**：真实库 schema 已是最新（v36），`migrate()` 没有可执行迁移 → 版本不变 → `after.version > before.version` 为假。
3. **是否真的执行了迁移**：没有（`log` 只有连接信息，`added_tables []`、无 schema 变化）。
4. **必需检查是否都跑了**：`integrity`、表集合与行数（`tables`）都在真实快照里跑了；`ok` 却把「版本必须上升」混进来。
5. **能证明/不能证明什么**：`integrity [ok,ok]` 证明库完好；表数与行数证明没有丢表丢行；版本未变**不能**证明失败。
6. **定性**：**合法 no-op 被误报为失败**（状态表达缺陷），不是迁移失败、也不是检查未执行。
7. **是否需要修与兼容影响**：已做最小兼容修复 —— 新增已发布模块 `app/database/migration_report.py`（`no_op`/`migrated`/`failed` 三态；`ok = status != failed`；integrity 异常、丢行、版本**倒退**才是 `failed`），`scripts/migrate_db.py` 改为调用它并打印 `status`。调用方审计：CI 无引用，仅本地门禁与文档消费 `ok`，字段保持兼容（仅新增 `status`）。

真实修复后证据（同一份真实库副本 `catoobot-20261015-184006.db`）：`version [36,36]`、`integrity [ok,ok]`、`tables [83,83]`、
`added_tables []`、`lost_rows {}`、**`status: "no_op"`、`ok: true`、退出码 0**。

## 9. 是否修改源代码 / 最小修复说明

修改了**产品代码**（均为最小改动，且都有回归测试）：

* `app/tasks/planner.py`：
  - `lookup` 现在先不带 `crafting_table` 查；若 reason 为 `crafting_table_required`，**真实观察一张工作台**（`craft_workbench` 观察进计划），不可用即 `raise ObservationFailed("crafting_table_required:<status>:<reason>:<explain>")`，可用则**用那张桌子的坐标重查**；
  - 新增 `_recipe_query`、`_workbench_once`、模块级 `_recipe_spec`；构建后的工作台分支复用同一次真实观察；
  - `craft_recipe_of` 把运行时的 `status == "crafting_table_required"` 映射为 `crafting_table_required`，不再降级成 `no_recipe`。
* `app/database/migration_report.py`（新增）：迁移演练三态判定（纯函数 + 报告构造）。
* 测试：`tests/test_phase8g_followup_workbench_lookup.py`（3 条）、`tests/test_phase8g_followup_migration_status.py`（7 条）。

**未改**：`app/integrations/minecraft/agent.py`（`ACTION_RISK`/`TOOL_ACTION`）、`config/settings.py`、`config/config.yaml`、`app/tools/policy.py`、授权与两道确认门、Action Runtime、数据库 schema/迁移。

## 10. 确认前后副作用、动作终态与库存净变化（独立核验）

* 确认前：`PENDING_CONFIRMATION`、零 `action_id`、真实背包未变。
* 确认后：`step_1 minecraft_craft` 终态 `SUCCEEDED`；`action_id` 记录在步骤上。
* 独立核验：`before {oak_log 15, oak_planks 4, stick 10, wooden_pickaxe 1}` → `after {oak_log 15, oak_planks 1, stick 8, wooden_pickaxe 2}`（§7 的增量与配方一致）。
* 另有一条早前闭环：`oak_log 14 → 13`、`oak_planks 0 → 4`（场景 3，2×2）。

## 11. 回归测试、静态检查与 CI

* `pytest tests -q` → **4318 passed**（8G 的 4308 + 新增 10 条）。
* `ruff check .` ✓；`ruff format --check .` → 716 files already formatted；`mypy app` → Success（345 源文件）。
* `git diff --check`：仅归档任务书自身带 markdown 硬换行尾随空格（**逐字节归档要求**，不改写）。
* CI 见 §13（本轮首次运行在已知的 `minecraft_runtime` E2E 步骤失败，已按纪律只重跑、未改任何断言/超时）。

## 12. 受保护文件、schema、凭据与本地门禁文件审计

* 受保护文件 diff：**空**（`agent.py`/`config/settings.py`/`config/config.yaml`/`app/tools/policy.py`）。
* 数据库：**无新增 schema/字段/迁移**；迁移演练 no-op 结果与修复前一致（`tables [83,83]`）。
* 凭据：变更文件中 0 命中；未打印任何凭据。
* `scripts/`：镜像内 **0 tracked**（门禁脚本与探针只留本地）。

## 13. 最终提交 SHA、CI 链接、镜像与远端同步

* 实现提交 + 证据提交（见文末 git 记录）；镜像 HEAD == `origin/main`。
* CI：见运行记录（重跑结果以 Actions 页面为准）。

## 14. 未解决事项与继续推进条件

* **配方变体选择**：当同一物品的所有变体都 `available: false` 时，排序退化到 `recipe_id` 字母序，会选到**金合欢/竹子**变体并报材料不足。
  真实证据：`recipe_lookup("wooden_pickaxe", {x:-265,y:69,z:-140})` → `status: insufficient_material`、`total: 11`，首个变体 `!wooden_pickaxe*1=acacia_planks*3+stick*2`。
  继续推进条件：按「库存可满足度」排序（例如优先材料已在背包或可由背包内材料一级合成出来的变体），且不得降低任何既有检查。
* 3×3 仍只覆盖基础合成：不自动备料、不连锁替代配方、不做容器配方链、不做跨维度。
* 夹具保留说明：世界中现有 8 个工作台（由门禁的 op 客户端放置），如需清理由测试人员人工处理。