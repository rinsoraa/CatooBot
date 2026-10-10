# CatooBot Phase 7F.1 — Bounded Autonomous Exploration（有界自主探索）

> 状态：**代码 / 回归 / 真机门禁全部通过并经真实服务器收口**。
> 基线：`a267afe77bbc65f93d8b753f8e57188d9da911c0`（Phase 7D.3）。
> 本文件记录：运行环境审计（§1）、调用链与最小设计（§2–§3）、实现与规则（§4–§6）、
> 验收矩阵与门禁（§7–§8）、真实服务器结果（§9）、交付与限制（§10–§11）。
>
> **阶段唯一目标**：建立**最小但完整**的自主游玩闭环 ——
> 真实角色状态 / 合格 LifeIntent → 有界探索提案 → 既有 AgentPlan 与能力、前置条件校验 →
> 既有批准和确认**两道门** → 既有 TaskRuntime → 已注册的 SAFE 观察与 LOW 导航工具 →
> 真实 Minecraft 结果验证 → 可追溯经历。
> **第一版只读 + 移动**：绝不挖、放、攻击、合成、开容器或做任何世界修改。
>
> **执行层永远是既有链路**：`TaskProposal → AgentPlan →（批准 / 确认）→ TaskRuntime → Policy
> → AgentBridge → ActionRuntime → MinecraftService`。探索**没有**任何独立的执行入口。

---

## 1. 运行环境审计（只读，不动生产库）

| 项 | 结论 |
| --- | --- |
| 权威源码 | `E:\WorkSpace ZCode\CatooBot`（含密钥，**非** git 仓库） |
| git 镜像 / 发布源 | `E:\WorkSpace ZCode\CatooBot_github`（origin = `https://github.com/rinsoraa/CatooBot.git`） |
| 基线 | `HEAD = origin/main = a267afe77bbc65f93d8b753f8e57188d9da911c0`（Phase 7D.3，CI 绿） |
| 生产库 | `E:\WorkSpace ZCode\CatooBot\data\catoobot.db`，migration **36** |
| 迁移脚本 | `scripts/migrate_db.py --db <path> --json`（**先备份副本**再演练） |
| 迁移演练 | v36 → v36 **幂等**，**无新表**、无数据丢失（`lost_rows {}`） |
| 运行时 | `.venv\Scripts\python.exe`；Minecraft Fabric 1.21.1 服务器 `127.0.0.1:25565`（online-mode=false） |
| 账号 | offline，username = `Catodayo` |

> 只有代码、运行进程、数据库身份都确认后，才给出生产验收结论；无法确定的一律记为前置阻塞项，
> **不伪造**生产结论。

---

## 2. 实际调用链（以当前源码为准）

```
[BoundedAgentPlanner.plan]
  detect_shape(objective)                     # FOLLOW → RESOURCE → EXPLORE → UNKNOWN
    == "explore" → _plan_explore(objective, observe)
        plan_exploration_task(objective, observe=observe)
            _safe_observe(observe, "minecraft_world", {})   # 规划期唯一执行的（SAFE 读）
            _explore_target(view, sx, sy, sz, max_distance) # 只读世界事实选目标
            → TaskPlan(move_to + look_at, expected_final_state.position_within)
        → PlanningResult(READY_FOR_APPROVAL, plan, risk_summary)
  ↓（AgentPlanService / TaskTurnHandler：只落计划，不执行）
[批准计划门]  + [确认执行门]
  ↓
[TaskRuntime]  _validate_plan（工具在册 / 风险 / 距离 / 前置条件）
  _run → step: move_to（LOW，detached → WAITING_ACTION）
              step: look_at（SAFE）
  _finish → _verify_final_state → _verify_position_within
              重新读 SAFE minecraft_world  → 真实坐标落在 radius 内 → verification.ok
  ↓
[_publish task.* / MinecraftMemoryBridge.on_task_finished]  # 经历回流，与其它任务同一套
```

关键接缝（**不新建**）：

* `BoundedAgentPlanner.plan(objective, *, target, observe)` 是 follow / resource / explore /
  unknown 的分派点；explore 只是新增一个确定性分支。
* `AgentPlanService` / `TaskTurnHandler` 是计划落地的**唯一**通道——探索和别的一样，
  只产出「计划候选」，真实执行永远要过两道门。
* `TaskRuntime._validate_plan` 与 `_finish` 是校验与结算的**唯一**位置；探索复用它们，
  没有旁路。

---

## 3. 最小设计

### 3.1 有界探索目标怎么选

`_explore_target` **只读真实世界事实**，不猜、不随机：

1. **兴趣点**：世界视图 `semantic.points_of_interest` 里，距离在 `[4, max_distance]` 的
   **最近**一个——太近（脚边）不算"探索"，太远超预算不算。
2. **兜底**：`semantic.terrain` 聚合里**最开阔**的罗盘方向（`north/south/east/west`，
   Minecraft `-Z = north`、`+X = east`），向前走一步（`step = clamp(4, max_distance, 开阔度-1)`）。

坐标系与方向映射是确定性的；目标点永远落在预算内。

### 3.2 预算

复用既有口径，**不新增一批配置旋钮**：

| 预算 | 值 | 来源 |
| --- | --- | --- |
| 探索距离上限 | `EXPLORE_DEFAULT_DISTANCE = 24.0` 格 | `planner.py`（对齐 `move_to.max_distance` 思路） |
| 到达半径 | `DEFAULT_ARRIVE_RADIUS = 2.0` 格 | `models.py` |
| 动作步数 | `move_to` + `look_at` 共 2 步 | 既有 `MAX_ACTION_STEPS` 之下 |
| 任务时长 / 重规划 | 既有 `TaskConfig.ttl_seconds` / `max_replans` | 不改 |

### 3.3 到达后置条件（真实世界判定）

`ExpectedFinalState` 新增 `position_within`（`x/y/z/radius`）。`_finish` 在把任务结算成
`SUCCEEDED` **之前**调 `_verify_final_state`：

* **重新读一次** SAFE `minecraft_world`（**不信任** `move_to` 的动作自述）；
* 由**真实世界坐标**算到目标点的距离，`distance <= radius` 才算到达；
* 结果写进 `record.verification`（`checked / position_within / expected_position / ok / message`）。

离线 / 不可用 / 读不到自身坐标 → **硬失败**，绝不冒充到达。

### 3.4 感知收敛的有界重试（真机逼出来的修正）

世界视图由感知层 near 层按固定间隔（默认约 1s）轮询真实世界得到。刚到达时缓存可能滞后
一个轮询周期，若只读一次会**错杀**真实到达。因此 `_verify_position_within` 对**同一份 SAFE 读**
做**有界重试**：

| 配置 | 值 | 语义 |
| --- | --- | --- |
| `TaskConfig.position_verify_attempts` | `6` | 最多读 6 次 |
| `TaskConfig.position_verify_interval_seconds` | `0.6` | 每次之间等 0.6s |

* 只有**读到了坐标但还没进半径**才重试（等感知刷新）；
* 离线 / 无坐标是**硬失败不重试**；
* 到期仍在半径外 → 判失败（**fail-closed**）。

既不放行"还没到"，也不因缓存延迟错杀真实到达。

---

## 4. 形状路由与前置条件（fail-closed）

`detect_shape` 的确定性优先级：`FOLLOW` → `RESOURCE` → `EXPLORE` → `UNKNOWN`（**绝不靠模型猜**）。
探索关键词复用 7A 的 `EXPLORE_KEYWORDS` 同词表。

`_plan_explore` 的 fail-closed 分支：

| 条件 | 结论 | reason |
| --- | --- | --- |
| `observe is None` | `BLOCKED_BY_PRECONDITION` | `observe_unavailable` |
| `allow_safe` / `allow_low` 任一为假 | `BLOCKED_BY_POLICY` | `explore_requires_safe_and_low` |
| 世界离线 / 不可用 / 读不到坐标 | `BLOCKED_BY_PRECONDITION` | `ObservationFailed` 文本 |
| 其它规划异常 | `NEEDS_MORE_INFORMATION` | `planning_failed:<类型>` |
| 计划含 SAFE/LOW 以外的风险 | `BLOCKED_BY_POLICY` | `_policy_blocked` 原因 |
| 正常 | `READY_FOR_APPROVAL` | `explore_plan_ready` |

**探索只用 SAFE 读 + LOW 移动**；任一被关掉就如实说做不了，**绝不**偷偷放宽。

---

## 5. 工具路由修复

`task_adapter._SERVICE_ROUTES` 补上 `minecraft_look_at → _look_at`。这是真机暴露的缺口：
计划里的 `look_at` 原本因为缺路由无法执行。修复后每个探索步骤工具都有对应 service 路由
（`tests/test_agent_plan_explore.py::test_look_at_route_calls_service_look_at` 守住）。

---

## 6. 经历、记忆与连续性

* 探索任务只有 `move_to` + `look_at`：**不产生资源事实、不写世界、不重复记忆**。
* 终态经既有 `TaskRuntime._publish` → `MinecraftMemoryBridge.on_task_finished` 回流，
  与其它任务**同一套**，不另立第二套记忆系统。
* 第一版不写挖 / 放 / 容器类证据；不保存思维链，不复制完整世界快照或非必要 Task payload。

---

## 7. 验收矩阵（任务书 §五 逐条落点）

| # | 任务书要求 | 落点 |
| --- | --- | --- |
| 1 | 无合格意图时不创建任务 | `_plan_explore` 前置条件失败即返回 BLOCKED/NEEDS_MORE，不落任务 |
| 2 | LifeIntent 只生成提案 / 计划候选，不直接执行 | explore 只产出 `PlanningResult(READY_FOR_APPROVAL)` |
| 3 | 未批准 / 未确认的 LIFE 任务不能执行 | `test_unapproved_explore_cannot_execute` / `test_approval_creates_pending_task_but_not_execution` |
| 4 | 合法探索复用既有 TaskRuntime 与注册工具 | `plan_exploration_task` 只用 `minecraft_move_to` / `minecraft_look_at` |
| 5 | 导航成功 / 失败 / 不可达 / 世界变化 / 离线 / 超时 / 取消 / 停止 | `test_task_runtime_explore.py` 六项 + 真机三段 |
| 6 | 动作数 / 范围 / 时长受硬预算约束 | `EXPLORE_DEFAULT_DISTANCE` + 2 步 + 既有 ttl |
| 7 | 重复事件 / 重放 / 重启不重复执行或重复记忆 | 复用既有 checkpoint / event_seq / 幂等语义（未改） |
| 8 | 观察证据不足不伪造成功经历 | 离线 / 无坐标 → fail-closed，绝不 SUCCEEDED |
| 9 | InMemory 与 SQLite 行为一致、迁移幂等 | §1 迁移演练；测试用内存 store |
| 10 | 无绕过 Policy / 确认门 / 授权 / 停止的路径 | 复用既有唯一链路，无新执行入口 |

---

## 8. 本轮实际执行的门禁（串行）

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| ruff check | `ruff check .` | All passed |
| ruff format | `ruff format --check .` | 636 files already formatted |
| mypy | `mypy app` | Success（335 files） |
| 全量 pytest | `pytest tests -q` | **3608 passed** |
| WebUI typecheck / Vitest / build / Playwright | （本阶段前一轮，未触碰前端） | 绿 |
| Minecraft Node 单测 / E2E | `minecraft_runtime npm test` | ALL CHECKS PASSED |
| 迁移演练 | `scripts/migrate_db.py` | v36→v36 幂等，无新表，`lost_rows {}` |
| 真实服务器 | `scripts/explore_smoke_real.py` | **PASS**（见 §9） |

> 重型门禁**串行**运行，不放宽既有断言。

---

## 9. 真实 Java 服务器门禁 —— **PASS**

`scripts/explore_smoke_real.py`（Fabric 1.21.1，offline `Catodayo`，先停 CatooBot 以独占 runtime）：

| 段 | 场景 | 结果 |
| --- | --- | --- |
| `positive` | 真实规划 → `READY_FOR_APPROVAL` → 执行 → 真世界验证到达 | `SUCCEEDED`；`verification.ok=True`；真实坐标距目标 **1.70 格**（半径 2.0）；角色真实位移；无遗留移动 |
| `blocked` | 目标不可达（正上方 +40 格，在 ≤64 距离门内但走不通） | 真实 `path.not_found` → `PAUSED`（`ACTION_FAILED`）；**绝不 SUCCEEDED**；无持续移动 |
| `cancel` | 导航进行中（`WAITING_ACTION`）取消 | `CANCELLED` + 经 `minecraft_stop` 真停 + 不再产生新动作 |

**脚本输出末尾 `REAL SERVER: PASS`。** 无 SKIPPED 项。

（关键修复历史：真机第一轮 `blocked` 是 `VALIDATION` 超距离门挡下、`cancel` 因 +96 格超移动上限被
挡下——都不是要验证的真实路径。改成 +40 / +48 格后，`blocked` 走真实 `path.not_found`、
`cancel` 真进 `WAITING_ACTION`。）

---

## 10. 交付清单

* **新增**：`tests/test_agent_plan_explore.py`、`tests/test_task_runtime_explore.py`、
  `scripts/explore_smoke_real.py`、本文件 `docs/MINECRAFT_PHASE7F1.md`、
  `docs/specs/CatooBot Phase 7F.1 — Bounded Autonomous Exploration.md`（任务书副本）。
* **修改**：`app/tasks/models.py`（`position_within` / `DEFAULT_ARRIVE_RADIUS`）、
  `app/tasks/runtime.py`（位置后置校验 + 感知收敛有界重试）、`app/tasks/planner.py`
  （`plan_exploration_task`）、`app/tasks/agent_planner.py`（explore 形状路由）、
  `app/integrations/minecraft/task_adapter.py`（`look_at` 路由）、`tests/agent_plan_fakes.py`、
  `CHANGELOG.md`、`docs/README.md`。
* **迁移清单**：**无新表、无新迁移**（最高仍 **36**）。
* **敏感文件核对**：未改动 `.env` / `minecraft_runtime/auth.json` / Character Bible / 密钥。
* commit SHA / 镜像一致性 / CI 结果见 §11。

---

## 11. 残余限制与后续建议

* 第一版探索是**确定性单段**闭环（一个 `move_to` + 一个 `look_at`），**不是**无界 LLM 循环，
  也不含多段路线规划或世界修改。
* 目标选择依赖感知层已有的 `points_of_interest` / `terrain` 事实质量；事实缺失时退化为
  罗盘兜底方向。
* 后续（7F.2+）可在**不松安全边界**的前提下扩展：多段有界路线、探索中遇到新事实的反应，
  以及把探索经历接入生活规划（LifeIntent 回流）。