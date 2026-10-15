# Phase 8H Follow-up 最终报告 — 迁移 schema 漂移检测与搜索记忆化契约补齐

> 归档任务书：`docs/specs/CatooBot Minecraft Phase 8H Follow-up — Complete Schema Drift Detection & Search Memoization Contract.md`
> （13024 bytes，SHA256 `763445588E702BFF9A44DA6F730CCE1C65474CCECB998AD8093E128371C9AED0`，265 行，逐字节归档核验）
> 开工基线：`07dc06472528a51f42a377f07df69858f347093b`（= Phase 8H 收尾）

## 1. 裁决与逐项 DoD

**PASS**（14 项 DoD 全部满足）。两条审计缺口都已关闭：schema 漂移检测范围已明确并补齐；搜索记忆化的实现与文档已一致，
且有针对性测试证明有界性与回滚完整性。

| DoD | 结果 | 证据 |
| --- | --- | --- |
| schema 漂移判定范围明确，不再把新增表检测当成完整比较 | ✓ | §3 |
| 版本不变时列/表定义/删除空表/索引/触发器/视图变化都按契约处理 | ✓ | §4 |
| 真实 no-op 不误报失败 | ✓ | §5（status=no_op、ok=true、退出码 0） |
| 真实失败不会被归类为 no_op/migrated | ✓ | §5（副本漂移 → failed） |
| 快照不完整/采集失败不产生虚假健康结论 | ✓ | schema_evidence_ok=False → failed（有测试） |
| 新增边界测试使用隔离的真实 SQLite 库 | ✓ | `tests/test_phase8h_followup_schema_drift.py`（tmp_path 真实库） |
| 记忆化实现与文档承诺一致 | ✓ | §6（只改文档 + 7 条测试；`craft_plan.py` 代码未改） |
| 循环/库存上下文/回滚/预算仍正确 | ✓ | §6 |
| Phase 8H 配方选择与完整 DAG 回归通过 | ✓ | §7（4347 passed） |
| pytest/ruff/format/mypy/git diff --check 通过 | ✓ | §7 |
| 无新增数据库 schema/字段/迁移 | ✓ | §8 |
| 受保护文件无未批准修改 | ✓ | §8 |
| CI/最终 SHA/镜像一致性/工作区可复核 | ✓ | §9 |
| 报告区分真实执行、历史证据与未验证内容 | ✓ | §7、§10 |

## 2. 开工与收尾 SHA

开工 `07dc06472528a51f42a377f07df69858f347093b`；收尾 `46123ea55a0c55abac29f7e05ae81d002b545c10`（实现 `0cb840b` + 证据 `46123ea`）。

## 3. Schema 快照的真实数据来源与规范化方法

* 数据来源：**真实 SQLite 元数据** —— `sqlite_master`（type/name/tbl_name/sql）与 `PRAGMA table_info(<表>)`（列名/类型/非空/默认值/主键位）。没有任何固定成功值或虚构 schema。
* 覆盖：table / index / trigger / view 四类对象 + 每张表每一列；能发现对象**新增、删除与定义变化**。
* 规范化：折叠所有空白、去结尾分号（确定性）；不做会制造假阴性的过度改写（不改大小写、不改标识符）。
* 内部对象：明确排除 `sqlite_%` 名字与 `sqlite_autoindex_%` 自动索引（跟随 UNIQUE/PRIMARY KEY 约束，正常变化不是产品漂移）。
* 失败语义：快照为空或读取失败 → `SchemaSnapshotError`；演练入口捕获后记 `schema_evidence_ok=False`，三态判定只能给 `failed`，**绝不静默降级为 no_op**。
* 差异摘要只记对象名/类型/方向（`added`/`removed`/`changed` + 计数 + 指纹），不写整库内容。**绝不只比表数量**。

## 4. 每类 schema 变化的测试结果

全部操作**隔离的真实临时 SQLite 库**（`tmp_path`），共 11 条，全部通过：无变化 → no_op；版本上升 + 预期变化 → migrated；
新增表 / 现有表新增列（diff.added 含 `column:items.weight`）/ 表定义变化（RENAME COLUMN，diff.changed 含 `table:items`）/
**删除空表**（diff.removed 含 `table:empty_one`，`lost_rows` 抓不到的那一类）/ 索引变化 / 视图与触发器变化（版本不变）→ 全部 failed；
schema 证据缺失 → failed（绝不 ok=true）；内部对象（AUTOINCREMENT/UNIQUE 产生的 `sqlite_*`）不误报；真实文件快照确定性（两次一致、diff=0）。
既有迁移状态测试（10 条）同步更新到新契约并通过。

## 5. no-op / 成功迁移 / 失败迁移的最终状态与真实演练输出

真实库副本 `catoobot-20261015-184006.db`（v36、83 表、integrity ok）：

* **合法 no-op**（`scripts/migrate_db.py --db <副本> --json`）：`version [36,36]`、`integrity ['ok','ok']`、`tables [83,83]`、`added_tables []`、
  `lost_rows {}`、**`schema_evidence_ok: true`**、`schema_changed: false`、`status: "no_op"`、`ok: true`、**退出码 0**。
* **失败（版本不变的 schema 漂移）**：同一真实副本给 `activity_episodes` 加一列（版本保持 36）→ `changed_count=2`、
  `schema_changed: true`、`schema_changed_without_version_bump: true`、`status: "failed"`、`ok: false`。
* **成功迁移**：单元级用真实临时库覆盖（版本 36→37 + 预期新增表 → `migrated`）；本阶段没有可执行的真实迁移，
  所以**没有**真实成功迁移的演练输出 —— 如实标注为未在本阶段真实执行。

## 6. 搜索记忆化实现与测试结论

* 审计（真实代码）：`craft_plan.py` 的 `impossible` 缓存**只在「该物品没有任何候选配方」时写入**；成环与超深在**候选尝试之前**抛出，
  **不消耗搜索预算**，也不跨分支缓存（路径相关）；预算 `max_recipe_search_states` 按**每一次候选尝试**（含子物品与根候选）计数。
* 结论：**实现本身有界且正确**；§2.3 的文档把「没有候选/成环/超深」都写成会记忆化 —— 与实现不符。
* 处理（任务书 §3.2 允许的最小方案）：**只改文档**使其与实现一致；`craft_plan.py` 的**代码未改**，未放宽预算、未改变计划正确性。
* 新增 7 条测试（全部通过）：无配方子问题只查一次且失败稳定；成环不吃预算；超深不吃预算；不同库存不共享可行性结论；
  候选失败后库存/节点/requires_table/累计输入完整回滚（与只给可行候选时逐字段一致）；预算按每次候选尝试计数；
  超限只给 `search_budget_exceeded` 且绝不返回部分计划。

## 7. 回归、静态检查与 CI

* `pytest tests -q` → **4347 passed**（8H 的 4329 + 新增 18：schema 漂移 11 + 记忆化 7）。
* `ruff check .` ✓；`ruff format --check .` → 721 files already formatted；`mypy app` → Success（345 文件）。
* `git diff --check`：仅归档任务书自身的 markdown 硬换行尾随空格（逐字节归档要求）。
* **本阶段重新执行**：上述全部 + 真实库副本 no-op 演练 + 真实副本漂移模拟 + CI 两个 Job。
* **仅引用历史证据（未重跑）**：真实 Minecraft 服务器合成门禁 —— 本阶段**没有改动**任何影响正式合成执行的代码
  （改动只在迁移报告模块与测试；`craft_plan.py` 只改了文档），故不重跑真机门禁。
* CI 诚实披露：第一次运行（run 38764461833）在**既有**测试 `tests/test_social.py::TestCognitionEngine::test_direct_mention_not_blocked_by_sleeping`
  失败（`assert False is True`；该用例使用 `sleep_start=00:00 / sleep_end=23:59`，在当天最后一分钟会判为「不在睡眠窗口」），
  与本阶段改动无关。按纪律**未修改任何断言或超时**，以新提交重跑（重跑成功 ≠ 根因修复）。

## 8. 受保护文件、schema、凭据、本地脚本与兼容性审计

* 受保护区域 diff **空**（`ACTION_RISK`/`TOOL_ACTION`、`config/settings.py`、`config/config.yaml`、`app/tools/policy.py`、授权与确认门、Action Runtime）。
* **无新增数据库 schema、字段或迁移**；业务表结构未动。
* 凭据扫描 0 命中；`scripts/` 在镜像中 **0 tracked**（本地演练脚本不发布）。
* 兼容性：`rehearsal_report` 的既有 JSON 字段全部保留（仅新增 schema 证据字段）；`rehearsal_status` 新增两个**必需**关键字参数
  （`schema_evidence_ok`/`schema_changed`）—— 调用方只有本地 `scripts/migrate_db.py` 与测试，均已同步更新（全仓 grep 确认无其他调用方）。

## 9. 最终提交、镜像同步与工作区状态

实现 `0cb840b` + 证据 `46123ea` + 报告归档提交；镜像 HEAD == `origin/main`；工作区干净；`scripts/` 0 tracked。

## 10. 未解决边界与继续推进条件

* **检测范围边界（如实声明）**：本实现比较 SQLite **可观察的 schema 元数据**（对象定义 + 表列信息），不声称能发现
  「定义文本完全等价但页级/物理布局不同」的情形（如 `VACUUM` 后的物理差异、纯数据内容变化）——那些不属于 schema 漂移。
  **不宣称「完整 schema 漂移检测已达成」**。
* **真实成功迁移未演练**：真实库已是最新版本，本阶段没有可执行的迁移；继续推进条件：出现真实待执行迁移时用真实副本演练并核对 `migrated` + 预期 schema 变化。
* **既有测试的时段相关抖动**属于本阶段范围之外；如需根治应单独提案把「当前时间」注入为可控输入，而**不是**放宽断言。
* 3×3 合成仍只覆盖基础合成（不自动备料、不连锁替代配方、不做容器配方链）—— 与 8H 一致的遗留边界。