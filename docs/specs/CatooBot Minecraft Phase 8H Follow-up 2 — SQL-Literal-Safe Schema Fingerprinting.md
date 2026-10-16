# CatooBot Phase 8H Follow-up 2 — SQL-Literal-Safe Schema Fingerprinting

## 0. 任务元信息

**阶段名称：** Phase 8H Follow-up 2  
**中文名称：** SQL 字面量安全的 Schema 指纹比较  
**性质：** Schema 漂移检测边界修复、最小实现与回归验证  
**目标：** 防止 SQL 规范化过程把语义不同的 schema 定义误判为相同，关闭上一轮 Follow-up 中尚未覆盖的字符串字面量变化场景。

**仓库：** `https://github.com/rinsoraa/CatooBot`  
**权威开发目录：** `E:\WorkSpace ZCode\CatooBot`  
**GitHub 发布镜像：** `E:\WorkSpace ZCode\CatooBot_github`  
**目标分支：** `main`  
**预期基线 SHA：** `de78187d16abf0b921ed8b499ffc7110026451f1`

该 SHA 为预期值。开工时重新核验开发目录、镜像、`origin/main` 与工作区状态；若实际仓库已有新提交，必须以真实 HEAD 为后续基线，不得回退或覆盖其他改动。

## 1. P0：复核已发布实现

必须实际阅读：

- `app/database/migration_report.py`
- `tests/test_phase8h_followup_schema_drift.py`
- `tests/test_phase8g_followup_migration_status.py`
- `docs/MINECRAFT_PHASE8H_FOLLOWUP.md`
- `docs/MINECRAFT_PHASE8H.md`
- `CHANGELOG.md`

### 1.1 已有实现

当前 `schema_snapshot()` 读取真实 SQLite 元数据：

- `sqlite_master` 中 table、index、trigger、view 的定义。
- `PRAGMA table_info()` 中的表列信息。
- `schema_diff()` 对快照进行新增、删除与修改比较。
- `rehearsal_status()` 根据完整性、schema 变化、版本、数据损失和证据完整性给出演练状态。

当前 `_normalize_sql()` 使用全局空白折叠：

`re.sub(r"\s+", " ", text).strip()`

这会处理 SQL 定义中的所有空白，不区分普通语法空白与字符串字面量中的内容。必须针对这个行为建立可复现的验证，而不是只根据正则表达式进行推测。

## 2. F1：复现 SQL 字面量规范化的潜在漏报

### 2.1 必须覆盖的场景

使用隔离的真实 SQLite 数据库，分别建立下列对象，然后保持对象名称不变，仅改变有语义差异的字符串字面量。

**场景 A：同名触发器改变条件中的字符串**

例如，先创建包含以下条件的触发器：

`WHEN NEW.name = 'a  b'`

再将同名触发器替换为：

`WHEN NEW.name = 'a b'`

要求确认：

- SQLite 的实际 schema 定义能够反映这两份不同的 SQL 定义。
- 旧规范化方法是否会将它们处理成相同文本。
- 新快照能否识别 `trigger:<name>` 的定义变化。
- 在 schema 变化且迁移版本不变时，演练应判定为 `failed`。

**场景 B：同一张表的 CHECK 约束改变字符串字面量**

创建带有 `CHECK` 约束的表，再改变约束表达式中的字符串字面量内容，保持表名与版本号不变。

要求确认：

- `sqlite_master.sql` 的变化能被检测到。
- 即使 `PRAGMA table_info()` 返回的列信息没有体现 CHECK 表达式的差异，schema 快照仍能识别表定义发生变化。
- 不能仅靠表数量、列数量或列类型比较来判定 schema 相同。

测试必须运行在临时数据库中，不能修改开发环境的真实业务数据库。

## 3. F2：修复规范化策略

### 3.1 核心原则

**规范化不能消除 SQL 中具有语义的差异。**

可以选择以下两种实现方案之一，但必须以最小改动为优先：

**方案 A：保守比较原始 schema 定义，优先推荐。**

- 比较 SQLite 实际存储的 schema 定义文本。
- 只执行能够证明不改变定义语义的最小处理，例如首尾非语义空白处理。
- 不对引号内字符串、带引号的标识符、约束表达式或触发器正文做全局空白折叠。
- 接受少量格式差异产生的误报风险，避免因过度规范化漏掉真实 schema 变化。
- 保留现有对象名、类型与列快照结构；除非存在明确必要性，不重写整套 schema 检测机制。

**方案 B：具备 SQL 词法意识的规范化。**

仅当确实需要消除语法空白差异时才采用。

- 必须正确区分普通 SQL 空白、单引号字符串、双引号标识符、反引号标识符和方括号标识符。
- 不得把字符串字面量或带引号标识符中的空白折叠掉。
- 如果处理注释或其他 token，必须确保不会把语义不同的 SQL 转换成相同结果。
- 必须通过专门的词法边界测试证明其正确性。

不要为了追求更漂亮的哈希而引入未经验证的 SQL parser 或复杂的重写逻辑。**当无法证明两份定义等价时，应保留差异，让原有 fail-closed 演练策略处理，而不是把它们强行规范化成相同结果。**

### 3.2 兼容性要求

保留现有：

- `schema_snapshot()`、`schema_fingerprint()` 与 `schema_diff()` 的公开调用契约。
- `rehearsal_report()` 和 `rehearsal_status()` 的三态业务语义。
- 现有 JSON 字段与差异摘要结构。
- SQLite 内部对象排除规则。
- 无变化的真实数据库副本返回 `no_op` 的行为。
- 正常版本递增、完整性通过且不存在数据损失的迁移返回 `migrated` 的行为。

不得增加数据库业务 schema、字段或迁移。不得将检测失败降级成健康的 `no_op`。

## 4. F3：测试矩阵与判定条件

在 `tests/test_phase8h_followup_schema_drift.py` 增加针对性测试，并保持现有测试全部通过。

至少覆盖：

1. 相同数据库连续生成两次快照，结果相同。
2. 相同表、触发器或视图只改变字符串字面量内容，快照必须识别定义差异。
3. 同名 CHECK 约束只改变字符串字面量内容，必须被识别为表定义变化。
4. 版本不变但以上定义发生变化时，`schema_changed=True`，`schema_changed_without_version_bump=True`，最终状态为 `failed`。
5. 版本不变且 schema 完全不变时，仍为 `no_op`。
6. 版本上升且存在正常的预期 schema 变化时，仍为 `migrated`。
7. 快照读取失败或证据缺失时，不能产生虚假的 `ok=true`。
8. 现有新增表、列变化、表重命名、删除空表、索引、视图、触发器及 SQLite 内部对象测试继续通过。

测试应检查实际生成的快照、差异摘要和最终状态，不得只测试 `_normalize_sql()` 的字符串输出而不验证整个演练判定链。

如果 SQLite 的实际元数据存储方式使某个构造示例不能形成有效差异，先核实数据库的实际 SQL 元数据，再调整测试构造；不能通过硬编码快照伪造成功。

## 5. F4：真实数据库副本演练

使用上一阶段的真实数据库副本或其他不含敏感业务内容的隔离副本。

至少执行：

**合法 no-op：**

- 前后版本不变。
- 前后完整性检查通过。
- schema 快照证据完整。
- `schema_changed=False`。
- 最终 `status=no_op`、`ok=true`、退出码 0。

**版本不变的 schema 漂移：**

- 在隔离副本上创建同名 schema 对象定义变化。
- 保持迁移版本不变。
- 采集真实前后快照并输出差异。
- 确认最终 `status=failed`、`ok=false`。
- 不修改生产数据库。

本阶段没有真实待执行迁移时，不要求伪造一个真实成功迁移的证据。可以通过临时 SQLite 数据库测试正常的 `migrated` 状态，但必须明确区分单元级演练与真实业务数据库迁移。

## 6. F5：回归与审计

在权威开发目录执行：

```powershell
pytest tests -q
ruff check .
ruff format --check .
mypy app
git diff --check
```

要求：

- 全量测试在最终提交前通过。
- 不删除现有断言，不放宽阈值。
- 不触及 Minecraft 正式合成执行链。
- 不修改 `ACTION_RISK`、`TOOL_ACTION`、`allow_medium` 默认值、Policy、授权和确认门。
- 不新增业务 schema 或数据库迁移。
- 本地测试脚本与临时数据库不得进入发布镜像。
- 保留 CI 的真实结果及失败日志，不将一次重跑通过描述为历史故障已经根治。

上一阶段报告的 `test_direct_mention_not_blocked_by_sleeping` 时段相关抖动不属于本阶段的修复范围。除非本次改动确实触及该测试，否则不应顺手修改其断言、超时或调度语义。

## 7. Git 与发布

完成实现、测试和审计后：

1. 更新 `docs/MINECRAFT_PHASE8H_FOLLOWUP.md`，追加 Follow-up 2 结论，保留前一阶段历史记录。
2. 更新 `CHANGELOG.md`。
3. 检查最终 diff 与受保护区域。
4. 检查凭据和本地门禁文件，确保 `scripts/` 没有意外进入发布镜像。
5. 同步 `CatooBot_github` 与远端。
6. 核对最终 HEAD 与 `origin/main`，确认工作区干净。
7. 记录 CI 链接和实际 Job 结果。

## 8. 最终 DoD

- [ ] 已复现或通过真实 SQLite 元数据说明字符串字面量差异的实际检测行为。
- [ ] SQL 定义规范化不再将已证明有语义差异的字符串字面量折叠成相同定义。
- [ ] 触发器与 CHECK 约束字面量变化有完整演练级回归测试。
- [ ] 版本不变且发生语义性 schema 变化时，判定为 `failed`。
- [ ] 合法 no-op、正常 migrated、真正 failed 三态契约保持正确。
- [ ] schema 元数据读取失败仍然 fail-closed。
- [ ] 现有 schema 漂移测试全部通过。
- [ ] 完整 pytest、ruff、格式检查、mypy 和适用的 diff 检查通过。
- [ ] 没有新增业务 schema、字段、工具或权限。
- [ ] 原有 Minecraft 正式执行、安全授权及确认链没有变化。
- [ ] CI、提交 SHA、镜像同步与工作区状态可复核。

**裁决规则：** 只有在字面量差异测试、现有 schema 漂移回归及所有适用检查全部通过时，才可以裁定 `PASS`。如果选择保守的原始定义比较，应如实说明可能产生的格式差异误报，不能宣称完成了 SQL 语义等价判断。

## 9. 最终报告

最终报告应包括：

1. 开工与收尾 SHA。
2. 原规范化方法的实际失败案例。
3. 修复后的比较策略与设计依据。
4. 新增测试的 SQLite 实际 schema、快照差异和三态裁定。
5. 合法 no-op 与 schema 漂移副本演练结果。
6. 全量测试、静态检查和 CI 结果。
7. 兼容性、受保护文件、schema、凭据和本地脚本审计。
8. 最终发布状态及保留的边界限制。