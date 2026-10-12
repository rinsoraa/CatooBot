# CatooBot Phase 7F.2.1 — Terminal-State Invariant Hardening（探索投影终态不变量加固）

> 状态：**代码 / 回归 / 数据库幂等 / 真机门禁全部通过并经真实服务器收口**。
> 基线：`424dce4cca8135593adeda23c9b3990805f2b9a2`（Phase 7F.2）。
> 本文件记录：审计发现（§1）、最小修复与规则（§2）、回归矩阵（§3）、门禁结果（§4–§5）、
> 安全边界确认（§6）、交付与限制（§7）。
>
> **阶段唯一目标**：在不改变 7F.2 正常业务行为的前提下，补牢探索结果投影的**终态约束**。
> **核心不变量**：只有最终状态严格为 `SUCCEEDED`，且运行时独立验证证据完整
> （`verification.checked is True`、`verification.ok is True`、`position_within` 带**实际到达坐标** x/y/z），
> 才允许探索投影写入正向 `EVENT` 到达经历或 `LOCATION` 新事实。
> `FAILED` / `CANCELLED` / `EXPIRED` / 状态未知 / 状态与证据互相矛盾的记录一律 **fail-closed**。
>
> 这是一次**小型防御性修复**，不是新增探索能力：没有新工具、没有新表、没有新迁移、没有新执行入口。

---

## 1. 审计发现（三处弱判定）

审计范围：`app/tasks/exploration.py`（`_arrived` / `exploration_facts` / `exploration_outcome` /
`arrival_fact`）、`app/integrations/minecraft/memory_bridge.py`（`on_task_finished` /
`_project_exploration`）、`tests/test_exploration_feedback.py`，并对照 `TaskRuntime._verify_position_within`
在成功 / 失败 / 取消时如何写 `TaskRecord.verification`。

修复前，探索投影里有**三处各自为政、且都不检查终态**的到达判定：

| 位置 | 修复前的判定 | 弱点 |
| --- | --- | --- |
| `_arrived()` | 只看 `checked` / `ok` / `Mapping` | 不检查 `record.state`；矛盾记录（如 `CANCELLED` + 成功字段）被当成到达 |
| `exploration_outcome()` 内联判定 | 同上 | 状态为 `SUCCEEDED` 的分支与真实到达判定脱钩，矛盾记录可落 `NEW_FACTS_VERIFIED` |
| `arrival_fact()` | 自己一套更弱的 `checked` / `ok` + `_clean_position` | 能绕过状态要求，`CANCELLED`/`FAILED` 也可能写 `EVENT` 到达经历 |

`exploration_facts()` 通过 `_arrived()` 间接继承弱点，会给矛盾记录写出 **actionable `LOCATION`** 事实，
进而喂给候选生成、形成"她真的发现了新地方"的错误连续性。

> `TaskRuntime._verify_position_within`（成功路径）会写
> `position_within = {x, y, z, distance}` + `expected_position`，并在失败时写 `ok=False, position_within=None`。

---

## 2. 最小修复（统一到单一严格判定）

在 `app/tasks/exploration.py` 新增**唯一**的已验证到达判定，并让所有调用点经它：

```python
def verified_arrival(record: Any) -> dict[str, float] | None:
    if _state_of(record) != "SUCCEEDED":
        return None
    verification = getattr(record, "verification", None)
    if not isinstance(verification, Mapping):
        return None
    if verification.get("checked") is not True or verification.get("ok") is not True:
        return None
    return _clean_position(verification.get("position_within"))
```

调用点全部收敛（不再有任何更弱的独立判定）：

* `_arrived(record)` → `return verified_arrival(record) is not None`
* `_arrival_payload(record)` → `return dict(verified_arrival(record) or {})`
* `exploration_outcome(record, ...)` → `arrived = verified_arrival(record) is not None`
* `arrival_fact(record, ...)` → `position = verified_arrival(record)`（删除弱检查）
* `_clean_position` 上移（body 不变：必须含 x/y/z 且可 `float(...)`，否则 `None`）
* `verified_arrival` 加入 `__all__`

### 结果分类（原有语义一字未改）

| 条件 | outcome |
| --- | --- |
| `SUCCEEDED` + 完整验证 + 可信新事实 | `NEW_FACTS_VERIFIED` |
| `SUCCEEDED` + 完整验证 + 无可信新事实 | `ARRIVED_NO_NEW_FACT` |
| `FAILED` | `BLOCKED` |
| `CANCELLED` / `EXPIRED` / 状态未知 / 完成证据缺失或矛盾 | `CANCELLED_OR_INTERRUPTED` |

矛盾状态一律以 **fail-closed** 拒绝正向记忆，不为匹配某个枚举伪造事实。

---

## 3. 新增回归矩阵

在 `tests/test_exploration_feedback.py` 新增 `TestTerminalStateInvariant`（6 项），
前三项**验证真实记忆存储写入结果**（不只看 `exploration_outcome()` 返回值）：

| 用例 | 构造 | 断言的不变量 |
| --- | --- | --- |
| `test_cancelled_with_contradictory_success_verification` | `CANCELLED` + `checked=True` + `ok=True` + 成功 `world` 步 + POI | 存储只留 `TASK`；无 `EVENT` / `LOCATION`；无 actionable；`exploration_facts==()`、`arrival_fact is None` |
| `test_failed_with_contradictory_success_verification` | `FAILED` + 同样的矛盾成功字段 | 存储只留 `TASK`（outcome `BLOCKED`）；无正向事实 |
| `test_expired_with_contradictory_success_verification` | `EXPIRED` + 同样的矛盾成功字段 | 存储只留 `TASK`；`arrived=False` |
| `test_succeeded_without_actual_arrival_coords` | `SUCCEEDED` 但 `position_within={}` | 缺实际到达坐标 → 无 `EVENT` / `LOCATION`；outcome `CANCELLED_OR_INTERRUPTED` |
| `test_succeeded_with_full_verification_writes_positive_memory` | `SUCCEEDED` + 完整到达 + 合格 POI | 仍写 `EVENT` + actionable `LOCATION`，outcome `NEW_FACTS_VERIFIED` |
| `test_repeated_publish_stays_idempotent` | 同一终态发布 3 次 | `EVENT` / `LOCATION` 各 1 条（去重强化，不新增重复事实） |

失败 / 取消只保留既有允许的任务经历记录（`TASK`），不产生可用于后续探索规划的正向事实。

---

## 4. 本地全量门禁

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 641 files already formatted |
| `mypy app` | Success: no issues found in 336 source files |
| `pytest tests -q` | **3644 passed**（基线 3638 + 6） |
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
  真机探索 `SUCCEEDED` + `verification.ok=True` → 真记忆桥写 3 条（1 条 actionable `LOCATION`
  新事实 + 1 条 `EVENT` 到达 + 1 条 `TASK`）→ `NEW_FACTS_VERIFIED` → 1 条 `exploration_feedback`
  `MINECRAFT_INTEREST` → 真 `TaskProposal`（`created`，来源 LIFE）→ 真 `AgentPlan`
  （`READY_FOR_APPROVAL`，规划阶段 active 未变）→ 批准后停在 **`PENDING_CONFIRMATION`** 且**零真实动作**。
* `scripts/explore_smoke_real.py --sections all` → **PASS**：
  段落 1 positive（距目标 1.11 格 `SUCCEEDED`）/ 段落 2 blocked（安全 `PAUSED`，绝不冒充到达）/
  段落 3 cancel（真停且不再产生新动作）。

真机门禁不通过跳过、mock、放宽断言或仅看退出码来宣告 PASS。

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

* `app/tasks/exploration.py`：新增 `verified_arrival` 共享严格判定；`_arrived` / `_arrival_payload` /
  `exploration_outcome` / `arrival_fact` 全部改走它；`_clean_position` 上移；`__all__` 增补。
* `tests/test_exploration_feedback.py`：新增 `TestTerminalStateInvariant`（6 项）。
* `CHANGELOG.md` / `docs/MINECRAFT_PHASE_7F.2.1.md` / `docs/README.md`：文档。

**限制**：探索仍是**确定性单段**只读 + 移动闭环；不含多段 / 无界路线或世界修改。
本阶段只加固投影边界的 fail-closed，不扩大探索能力。
