# CatooBot Phase 7F.2.2 — Coordinate Domain Hardening（探索坐标域加固）

> 状态：**代码 / 回归 / 数据库幂等 / 真机门禁全部通过并经真实服务器收口**。
> 基线：`07f722f95aa72a545b76bceb88c91052ba47152b`（Phase 7F.2.1）。
> 本文件记录：审计发现（§1）、最小修复与规则（§2）、回归矩阵（§3）、门禁结果（§4–§5）、
> 安全边界确认（§6）、交付与限制（§7）。
>
> **阶段唯一目标**：收紧探索坐标的**数值有效性**验证。
> **核心不变量**：坐标“能转成 float”**不等于**坐标有效。`NaN` / `±Infinity` / `bool`
> （`True` / `False` 是 `int` 子类）/ 缺字段 / 类型异常 / 不可解析字符串，**一律不得**通过
> `verified_arrival()` 成为正向探索记忆（到达 `EVENT` 经历或 actionable `LOCATION` 新事实）的依据。
> 合法负坐标与合法小数坐标（含有限数字字符串）照常通过。
>
> 这是一次**小型防御性补丁**，不是新能力阶段：没有新工具、没有新表、没有新迁移、没有新执行入口。

---

## 1. 审计发现（坐标解析过松）

审计范围：`app/tasks/exploration.py`（`_clean_position` / `verified_arrival` / `exploration_facts` /
`arrival_fact` / `exploration_outcome`）、`app/memory/minecraft/model.py`（`number_or` / `cluster_position`）、
`tests/test_exploration_feedback.py`，并对照 Phase 7F.2.1 已统一的终态到达判定。

修复前，`_clean_position()` 只是对 x/y/z 逐个做 `float(value[key])`，因此：

| 输入 | 修复前行为 | 风险 |
| --- | --- | --- |
| `float("nan")` / `"NaN"` | `float(...)` 成功 → 变成 `nan` | `nan` 通过 `verified_arrival()`，写出“到达坐标 x=nan”的正向记忆 |
| `float("inf")` / `"Infinity"` / `"1e400"` | `float(...)` 成功 → 变成 `inf` | 非有限坐标冒充真实到达 |
| `True` / `False` | 是 `int` 子类，`float(True)==1.0` | 布尔被当成 1/0，伪造坐标 |
| 缺字段 / 非映射 / 不可解析字符串 | 抛异常或误判 | 边界不统一 |

> `verified_arrival()` 是**唯一**的已验证到达判定（Phase 7F.2.1）；`exploration_facts()` / `arrival_fact()` /
> `exploration_outcome()` 全部委派给它，因此只需在这一处的坐标解析上加固，调用链无需改动。

---

## 2. 最小修复（收紧到有限实数域）

在 `app/tasks/exploration.py` 引入单一坐标轴校验，并让 `_clean_position()` 复用它：

```python
def _finite_axis(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean_position(value: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    cleaned: dict[str, float] = {}
    for key in _POSITION_KEYS:
        if key not in value:
            return None
        number = _finite_axis(value[key])
        if number is None:
            return None
        cleaned[key] = number
    return cleaned
```

要点：

* `bool` 先于数值判断被显式拒绝（`True` / `False` 绝不变成 1 / 0）。
* `math.isfinite()` 拒绝 `NaN` / `+Infinity` / `-Infinity`（含 `"NaN"` / `"Infinity"` / `"1e400"` 这类字符串）。
* 缺字段、非映射、类型异常、不可解析字符串一律 fail-closed 返回 `None`。
* **不设** Minecraft 世界坐标的固定绝对上限：合法负坐标、合法小数坐标照常通过。
* 保留有限数字字符串兼容性（现有真实数据来源与序列化格式仍可能给数字字符串），但拒绝其非有限值。
* `verified_arrival()` 的判定条件**一字未改**：仍要求 `SUCCEEDED` + `verification` 映射 +
  `checked is True` + `ok is True` + `position_within` 含有效有限 x/y/z。

### 结果分类（原有语义一字未改）

| 条件 | outcome |
| --- | --- |
| `SUCCEEDED` + 完整验证 + 可信新事实 | `NEW_FACTS_VERIFIED` |
| `SUCCEEDED` + 完整验证 + 无可信新事实 | `ARRIVED_NO_NEW_FACT` |
| `FAILED` | `BLOCKED` |
| `CANCELLED` / `EXPIRED` / 状态未知 / 完成证据缺失或矛盾 / 坐标非有限 | `CANCELLED_OR_INTERRUPTED` |

坐标无效只让到达判定**不成立**，绝不伪造事实或匹配枚举，一律 **fail-closed**。

---

## 3. 新增回归矩阵

在 `tests/test_exploration_feedback.py` 新增 `TestCoordinateDomainHardening`（53 项，含参数化），
同时覆盖**单元层**与**真实记忆存储层**：

| 分组 | 用例 | 断言的不变量 |
| --- | --- | --- |
| 单元 `_clean_position` | x/y/z 各为 `NaN` / `±Infinity`（各 3 轴） | 返回 `None` |
| 单元 `_clean_position` | x/y/z 各为 `True` / `False`（各 3 轴） | 返回 `None`（bool 不当 1/0） |
| 单元 `_clean_position` | 缺字段（x/y/z 各缺一） | 返回 `None` |
| 单元 `_clean_position` | 错误类型（`None` / list / dict / object） | 返回 `None` |
| 单元 `_clean_position` | 不可解析或非有限字符串（`north` / `` / `NaN` / `inf` / `-inf` / `Infinity` / `-Infinity` / `1e400`） | 返回 `None` |
| 单元 `_clean_position` | 输入非映射（`None` / list / 逗号字符串 / int） | 返回 `None` |
| 单元 `_clean_position` | 合法负坐标 + 小数 + 整数 | 返回规范化 `{x,y,z}` 浮点 |
| 单元 `_clean_position` | 有限数字字符串（`"116"` / `"63.5"` / `"-100"`） | 兼容 → 浮点 |
| 单元 `verified_arrival` | x/y/z 各为 `NaN` / `±Infinity` / `True` | `None`，且 `exploration_outcome().arrived is False` |
| 单元 `verified_arrival` | `position_within` 非映射 | `None` |
| 单元 `verified_arrival` | 合法负坐标 + 小数 + 整数 | 返回规范化浮点 |
| 记忆层 | `SUCCEEDED` + `checked/ok=True` 但某轴为 `NaN` / `±Infinity` / `True` + 合格 POI | 存储只留 `TASK`；无 `EVENT` / `LOCATION`；无 actionable；outcome `CANCELLED_OR_INTERRUPTED` |
| 记忆层 | 同一无效终态重复发布 3 次 | 无 `EVENT` / `LOCATION`（稳定 fail-closed，无新增重复事实） |

同时回归验证（既有用例继续通过）：`CANCELLED` / `FAILED` / `EXPIRED` + 矛盾成功验证仍不写正向事实；
正常 `SUCCEEDED` + 有效坐标 + 合格 POI 仍完整回流；重复发布仍幂等。未新增无关测试数据库或持久化产物。

---

## 4. 本地全量门禁

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 642 files already formatted |
| `mypy app` | Success: no issues found in 336 source files |
| `pytest tests -q` | **3697 passed**（基线 3644 + 53） |
| WebUI `npm run typecheck` | 通过 |
| WebUI `npm test`（Vitest） | 75 files / **528 passed** |
| WebUI `npm run build` | 通过 |
| WebUI `npm run test:e2e`（Playwright） | **7 passed** |
| `minecraft_runtime npm test` | 全绿（含 e2e） |
| 迁移演练 `scripts/migrate_db.py --db <副本> --json` | v36 → v36 **幂等**；83 表；`integrity ok`；`lost_rows {}` |

> 迁移演练的 `ok:false` 是**已处于最新版（v36）时的预期 no-op**：版本 [36, 36]、表数不变、无新增迁移。

---

## 5. 真实 Fabric 1.21.1 门禁

前置：停掉 CatooBot 释放 runtime 唯一会话；服务器 `127.0.0.1:25565`（online-mode=false，offline `Catodayo`）。

* `scripts/feedback_smoke_real.py` → **PASS**：
  真机探索 `SUCCEEDED` + `verification.ok=True` → 真记忆桥写 5 条（3 条 actionable `LOCATION`
  新事实 + 1 条 `EVENT` 到达 + 1 条 `TASK`）→ `NEW_FACTS_VERIFIED` → 1 条 `exploration_feedback`
  `MINECRAFT_INTEREST` → 真 `TaskProposal`（`created`，来源 LIFE）→ 真 `AgentPlan`
  （`READY_FOR_APPROVAL`，规划阶段 active 未变）→ 批准后停在 **`PENDING_CONFIRMATION`** 且**零真实动作**。
* `scripts/explore_smoke_real.py --sections all` → **PASS**：
  段落 1 positive（距目标 0.71 格 `SUCCEEDED`）/ 段落 2 blocked（安全 `PAUSED`，绝不冒充到达）/
  段落 3 cancel（真停且不再产生新动作）。

> 说明：首次运行 `explore_smoke_real.py` 段落 1 因 pathfinder 精度抖动停在距目标 2.26 格（允许 2.0）
> → `verification.ok=False` → 门禁如实判 FAIL；重跑距离 0.71 格通过。门禁**不通过跳过、mock、
> 放宽断言或仅看退出码**来宣告 PASS，每次结果均以真实 `verification` 为准。

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

* `app/tasks/exploration.py`：新增 `_finite_axis`（拒绝 `bool` / 不可转换 / 非有限值）；`_clean_position`
  改为逐轴复用该判定，缺字段 / 非映射 / 无效一律返回 `None`；`import math`。`verified_arrival` 及其调用链不变。
* `tests/test_exploration_feedback.py`：新增 `TestCoordinateDomainHardening`（53 项，单元层 + 记忆层）。
* `CHANGELOG.md` / `docs/MINECRAFT_PHASE_7F.2.2.md` / `docs/README.md`：文档。

**限制**：探索仍是**确定性单段**只读 + 移动闭环；不含多段 / 无界路线或世界修改。
本阶段只加固坐标域的 fail-closed，不扩大探索能力。