# CatooBot Phase 7F.2.2.1 — Numeric Conversion Overflow Hardening（数值转换溢出加固）

> 状态：**代码 / 回归 / 数据库幂等 / 真机门禁全部通过并经真实服务器收口**。
> 基线：`0469c3a672983328627af81823ae248ecae40e3d`（Phase 7F.2.2）。
> 本文件记录：审计发现（§1）、最小修复与规则（§2）、回归矩阵（§3）、门禁结果（§4–§5）、
> 安全边界确认（§6）、交付与限制（§7）。
>
> **阶段唯一目标**：补齐 `_finite_axis()` 对**数值转换溢出**的处理。
> **核心不变量**：坐标转换要么给出**有限浮点数**、要么 fail-closed 返回 `None`；
> `OverflowError` **绝不允许**向上传播到 `verified_arrival()` 及其调用链。
>
> 这是一次**小型防御性补丁**，不是新能力阶段：没有新工具、没有新表、没有新迁移、没有新执行入口。

---

## 1. 审计发现（整数 → 浮点转换溢出未覆盖）

Phase 7F.2.2 已覆盖 `bool` / `NaN` / `±Infinity` / 错误类型 / 缺失字段 / 不合法字符串，
但**未覆盖整数到浮点的转换溢出**：

| 输入 | 修复前行为 | 风险 |
| --- | --- | --- |
| `10 ** 400` / `-(10 ** 400)` | `float(...)` 抛 `OverflowError: int too large to convert to float`；`except (TypeError, ValueError)` **不接** | 异常穿透 `_clean_position()` → `verified_arrival()` 调用链，以"抛异常"替代契约要求的 fail-closed `None` |
| `10 ** 309` | 同上（超出 `float` 值域 ≈ 1.8e308） | 同上 |
| 有限 `int` / `float` / 数字字符串 | 正常 | 无变化 |

前提实测（修复前等价逻辑）：`except (TypeError, ValueError)` 下 `float(10 ** 400)` →
`OverflowError: int too large to convert to float`；修复后同一输入返回 `None`。

---

## 2. 最小修复（纳入 `OverflowError`）

`app/tasks/exploration.py` 的 `_finite_axis()`，唯一改动是把 `except` 扩为三元组：

```python
def _finite_axis(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None
```

要点：

* 对无法转换为**有限浮点数**的输入统一返回 `None`。
* `math.isfinite()` 把关**保留**。
* `bool`、错误类型、非有限值的既有拒绝行为**保留**（回归测试锁定，未放宽）。
* **不新增**任意世界坐标绝对上限。
* 合法负坐标、小数及有限数字字符串的兼容性**不变**。
* `verified_arrival()` 与任何下游调用链（`exploration_facts` / `arrival_fact` /
  `exploration_outcome`）**一字未改** —— `_finite_axis` 仍是其唯一坐标判定入口。

### 结果分类（原有语义一字未改）

| 条件 | outcome |
| --- | --- |
| `SUCCEEDED` + 完整验证 + 可信新事实 | `NEW_FACTS_VERIFIED` |
| `SUCCEEDED` + 完整验证 + 无可信新事实 | `ARRIVED_NO_NEW_FACT` |
| `FAILED` | `BLOCKED` |
| `CANCELLED` / `EXPIRED` / 状态未知 / 完成证据缺失或矛盾 / 坐标溢出或非有限 | `CANCELLED_OR_INTERRUPTED` |

坐标溢出只让到达判定**不成立**，绝不伪造事实或匹配枚举，一律 **fail-closed**。

---

## 3. 新增回归矩阵

在 `tests/test_exploration_feedback.py` 新增 `TestNumericConversionOverflowHardening`（**37 项**，含参数化），
同时覆盖**单元层**与**真实记忆存储层**；断言的是**具体返回值与实际记忆写入结果**，不只是"异常没有向外传播"：

| 分组 | 用例 | 断言的不变量 |
| --- | --- | --- |
| 单元 `_finite_axis` | `10**400` / `-(10**400)` / `10**309` / `-(10**309)` | 返回 `None`（不抛 `OverflowError`） |
| 单元 `_finite_axis` | 合法有限值 `0` / `116` / `-116` / `63.5` / `"116"` / `"-100.25"` | 等于 `float(value)`（兼容性未变） |
| 单元 `_finite_axis` | `NaN` / `±Infinity` / `True` | 返回 `None`（既有拒绝行为未放宽） |
| 单元 `_clean_position` | `10**400` / `-(10**400)` / `10**309` 各作 x、y、z（3×3） | 返回 `None` |
| 单元 `_clean_position` | 执行期观察里 POI 坐标溢出 | 该兴趣点被跳过，`exploration_facts()` 返回 `()` |
| 单元 `verified_arrival` | `SUCCEEDED` + `checked/ok=True` 但实际坐标各轴溢出（3×3） | `None`；`arrived=False`；`arrival={}`；outcome `CANCELLED_OR_INTERRUPTED` |
| 记忆层 | 同上 + 合格 POI，经**真记忆桥**发布 | 存储只留 `TASK`；无 `EVENT` / `LOCATION`；无 actionable |
| 记忆层 | 同一溢出终态重复发布 3 次 | 无 `EVENT` / `LOCATION`（稳定 fail-closed） |

同时回归验证（既有用例继续通过）：合法有限坐标仍完整回流；`CANCELLED` / `FAILED` / `EXPIRED` +
矛盾成功验证仍不写正向事实；重复发布仍幂等。未新增无关测试数据库或持久化产物。

---

## 4. 本地全量门禁

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 643 files already formatted |
| `mypy app` | Success: no issues found in 336 source files |
| `pytest tests -q` | **3734 passed**（基线 3697 + 37） |
| WebUI `npm run typecheck` | 通过 |
| WebUI `npm test`（Vitest） | 75 files / **528 passed** |
| WebUI `npm run build` | 通过（重建产物与已提交 `webui/dist` **逐字节一致**，无 churn） |
| WebUI `npm run test:e2e`（Playwright） | **7 passed**（23.1s） |
| `minecraft_runtime npm test` | 全绿（exit 0） |
| 迁移演练 `scripts/migrate_db.py --db <副本> --json` | v36 → v36 **幂等**；83 表；`integrity ok`；`lost_rows {}` |

> 迁移演练的 `ok:false` 是**已处于最新版（v36）时的预期 no-op**：版本 `[36, 36]`、表数不变、无新增迁移。
> 补充核验：副本共 84 张表（83 业务表 + `sqlite_sequence`）、`pragma quick_check` = `ok`、
> `foreign_key_check` **0 条违规**、`schema_migrations` 36 行（最高 36）。
>
> 说明：本轮全量 `pytest` 与本机 WebUI build / `minecraft_runtime` e2e **并行**执行，耗时 724.80s
> （串行基线 595.66s），结果同为全绿、无失败、无跳过。
> `minecraft_runtime` 套件内有一条**既有、与本次改动无关**的 SKIP：
> `[e2e] dropped_items / pickup 成功路径：SKIPPED（假服务器不支持 /summon 掉落物，造不出真实 Item Entity）`。

---

## 5. 真实 Fabric 1.21.1 门禁

前置：先备份 live DB（`scripts/backup_db.py` → `CatooBot_backups\catoobot-20261011-181229.db`，
v36 / 83 tables / 86763 rows / `integrity=ok`），再停掉 CatooBot 释放 runtime 的**唯一会话**
（停前 PID 35704 `python .\run.py` + 子进程 44156 `node runtime.js`；停后 25580 / 8500 释放）；
服务器 `127.0.0.1:25565`（online-mode=false，offline `Catodayo`）保持在线。
门禁跑完后已按原方式（全局 Python 3.12 + `python .\run.py`）重启 CatooBot，25580 / 8500 恢复。

* `scripts/feedback_smoke_real.py` → **PASS**：
  真机探索 `SUCCEEDED`（起点 `(-261.55, 71.0, -124.5)`，计划只用
  `minecraft_move_to` + `minecraft_look_at` + `minecraft_world`，风险只在 SAFE/LOW）
  + `verification.ok=True` → 真记忆桥写 3 条（1 条 actionable `LOCATION` 新事实 + 1 条 `EVENT` 到达
  + 1 条 `TASK`）→ `NEW_FACTS_VERIFIED`（actionable facts=1）→ 1 条 `exploration_feedback`
  `MINECRAFT_INTEREST` → 真 `TaskProposal`（`created`，来源 LIFE，`intent_id` 一致）→ 真 `AgentPlan`
  （`READY_FOR_APPROVAL`，`proposal_id` / `intent_id` 可追溯，规划阶段 active 未变）→ 第一道门批准后
  停在 **`PENDING_CONFIRMATION`** 且**零真实动作**。
* `scripts/explore_smoke_real.py --sections all` → **PASS**：
  段落 1 positive：起点 `(-262.5, 64.0, -128.5)` → 目标 `(-257.0, 64.0, -128.0, radius=2.0)`（距 5.5 格）
  → 终态 `SUCCEEDED`，真世界复核 `position_within.distance=1.70`（**落在半径内，本轮无精度抖动**）、
  位移 3.9 格、结束后无遗留移动；
  段落 2 blocked：不可达目标 → 安全 `PAUSED`（`failure=ACTION_FAILED`），**绝不冒充到达**、无持续移动；
  段落 3 cancel：导航进行中取消 → `CANCELLED` + 经 `minecraft_stop` 真停 + 不再产生新动作。

> **环境记录（既有编码脆弱性，与本次补丁无关）**：本轮首次以**管道重定向 stdout** 的方式运行
> `feedback_smoke_real.py` 时，脚本在打印 `✓` 处抛
> `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'`（管道下 Python 按系统 locale 取 GBK）。
> 设置 `PYTHONIOENCODING=utf-8` 后正常运行并 PASS。该问题属于**门禁脚本在本机管道环境下的输出编码脆弱性**，
> 不在本阶段修复范围（§三 禁止范围扩张），已如实记录。

> **真实门禁会真实写 live DB**：本轮新增 1 条 LifeIntent（`life_intents` 98 → 99）；
> 事后核验 `integrity ok`、最高迁移仍 **36**、仍 **83** 张业务表。

---

## 6. 安全边界（一字未松）

* 19 个工具、`ACTION_RISK`、`allow_medium=false` **未改**。
* `LifeIntent → TaskProposal → AgentPlan`、`TaskAuthorization` / `TaskStepAuthorization`、
  计划批准 + 第二道确认、Policy、AgentBridge、ActionRuntime **未改**。
* pause / resume / cancel / expire、超时、重规划、停止机制 **未改**。
* **无新表、无新迁移**（最高仍 36）；无第二套记忆系统。
* 未触碰真实 Character Bible、凭据、`.env`、敏感配置。
* 反馈链绝不自行获得执行权限：批准前不建 Task，批准后只停在 `PENDING_CONFIRMATION`。

---

## 7. 变更文件与限制

* `app/tasks/exploration.py`：`_finite_axis()` 的 `except` 由 `(TypeError, ValueError)` 扩为
  `(TypeError, ValueError, OverflowError)`（并同步 docstring 说明溢出语义）。
  `_clean_position` / `verified_arrival` 及其调用链**未改**。
* `tests/test_exploration_feedback.py`：新增 `TestNumericConversionOverflowHardening`（37 项，单元层 + 记忆层）。
* `CHANGELOG.md` / `docs/MINECRAFT_PHASE7F2.2.1.md` / `docs/README.md`：文档。
* `docs/specs/CatooBot Phase 7F.2.2.1 — Numeric Conversion Overflow Hardening.md`：任务书归档
  （与上传源文件 **SHA256 逐字节一致**，2609 bytes）。

**限制**：探索仍是**确定性单段**只读 + 移动闭环；不含多段 / 无界路线或世界修改。
本阶段只补齐数值转换溢出的 fail-closed，不扩大探索能力。
