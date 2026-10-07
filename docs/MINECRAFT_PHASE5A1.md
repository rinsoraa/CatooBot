# Minecraft Phase 5A.1 — Task Resilience（重规划 / 授权到期 / 重启恢复）

> 目标：把 TaskRuntime 从"能连续执行"升级成 **"面对世界已经变化时仍然不会偷偷做错事"**。
> 本阶段**不新增任何 Minecraft 工具**（仍是 19 个），**不新增任何 ActionRuntime action**，
> 也不新增 `minecraft_replan` / `minecraft_recover` / `minecraft_task` 这类工具 ——
> 增长的只有编排层自己的可靠性。

不变的四条底线（§四十六）：

```
旧 Plan         └─ 不会授权新 Plan
旧 Action       └─ 不会在 restart 后继续
旧 Confirmation └─ 不会授权新 arguments
旧世界假设      └─ 不会直接驱动新 World Action
```

## 一、Replanning（§三 A / §四-§九 / §二十二-§二十五）

世界变了不是"暂停一下接着跑"，而是**旧计划的假设已经不成立**：

```
步骤失败（WORLD_CHANGED / TARGET_LOST）
  ↓
REPLANNING：旧计划 mark SUPERSEDED（不覆盖，留档）
  ↓  只读对账（SAFE：world / dig_capability / dropped_items）
Planner 做 SAFE 观察 → 生成新计划
  ↓
replan()：plan_version += 1、新 plan_hash、清空 authorization
  ↓
PENDING_CONFIRMATION（摘要明确写"这是新计划、为什么"）
  ↓
用户在对话里确认 → 才允许动世界
```

* **重规划期只允许 SAFE 查询**（§五）：`world / inventory / find_blocks / dig_capability /
  dropped_items / recipe_lookup / container_inspect`；`move_to / equip / dig / place /
  pickup_item / inventory_move / container_transfer / craft` 一步都不许走 ——
  `drive()` 在 `REPLANNING` 状态下直接原地返回。
* **对账 ≠ 自动修复**（§二十二）：对账只回答"目标还在不在、还是不是那个东西"，
  绝不挑替代目标；新目标是 Planner + 用户确认的事。
* 重规划摘要（§九/§三十二）第一句就是事实，例如
  「目标已经发生了变化。原计划已停止。」+「新的计划（第 2 版）：…」+「需要重新确认。」；
  既不说"任务失败"，也不说"正在自动寻找替代目标……"。

## 二、Authorization Expiry（§三 B / §十-§十二 / §二十六）

* 授权有效期独立于任务总时长：`TaskConfig.authorization_ttl_seconds`（生产取确认门 TTL，
  默认 60s；**不新增配置旋钮**，也不需要动 `minecraft.agent.confirmation.ttl_seconds`）。
* 到期时任务停在**安全边界**（下一步还没开始，绝不在动作中间硬切）→ 清空 `authorization`
  → 回 `PENDING_CONFIRMATION` + 挂**新的**确认条目 + 发 `task.authorization_expired`。
* 计划本身没变 → `plan_hash` 不变、**不是**重规划；但旧确认条目已经消费过，**不能复用**。
* `resume`（对话/WebUI）遇到过期授权同样走这条；`PAUSED` 的任务授权过期也回到
  `PENDING_CONFIRMATION`（§十二）。
* 时钟全部注入（`TaskRuntime(clock=...)`），所以"t0+ttl-1 有效 / t0+ttl 过期"是确定性单测，
  真机也只等 1~2 秒（`SMOKE_AUTH_TTL`），**绝不等 600 秒**。

## 三、Runtime Restart Recovery（§三 C / §十三-§二十 / §二十七-§二十八）

```
进程重启（SQLite 里有非终态任务）
  ↓
recover_persisted_tasks()
  ↓
有 "WAITING_ACTION + action_id" → 那一步判为失效（RUNTIME_RESTART，不查询、不猜、不重做）
  ↓  并把**旧确认一起作废** + replan_required = True
只读对账（§二十一）→ RECONCILED / WORLD_CHANGED / TARGET_LOST / TARGET_ALREADY_DONE / OFFLINE / UNKNOWN
  ↓
世界变了 → REPLANNING；其余 → PAUSED（failure=RUNTIME_RESTART）
  ↓
TASK_RECOVERED 事件 + checkpoint（含 reason 与 outcome）
```

* 只要**有一步真的派出去过**，这份旧确认就不能再放行任何世界动作（§十七/§四十二）：
  `authorization = None` + `replan_required = True` + `resume` 直接拒绝
  （`task.replan_required`），必须先有新计划 + 新确认。
* 对账只做 SAFE 读，**且结果优先于旧 action 状态**（§十九）：checkpoint 说 `dig = RUNNING`
  而世界说那块地方已经是 `air` → `TARGET_ALREADY_DONE` → 重规划，**绝不重复挖**（§十八）。
* 动作完成与 checkpoint 落盘的竞态（§二十）也走同一条：靠世界事实对账，不靠"猜"。
* 跨进程重启只保证 `SQLite 读回 Task`；正在跑的 Mineflayer action 一定失效，
  第一版**不恢复**它（文档写死 `process restart durability` 的边界）。
* 恢复是**幂等**的：已经恢复过的任务再次扫描不会重复告警/重复对账。

## 四、Plan History（§七）

`TaskRecord.plan_history`：每一版都留档，**绝不覆盖**旧计划：

| 字段 | 含义 |
| --- | --- |
| `version` | 第几版（从 1 开始，重规划递增） |
| `plan_hash` | 那一版的计划指纹（只含决定世界操作的内容） |
| `summary` | 当时的确认摘要（含每一步的人话） |
| `steps` | 审计快照（step_id / tool / risk / canonical arguments） |
| `created_at` / `confirmed_at` / `superseded_at` | 时间线 |
| `reason` | 被取代的原因（`WORLD_CHANGED` / `RUNTIME_RESTART` / …） |
| `status` | `PENDING_CONFIRMATION` / `ACTIVE` / `SUPERSEDED` / `COMPLETED` |

WebUI 的 Task Detail 同时显示 **v1 SUPERSEDED 与 v2 PENDING_CONFIRMATION**，不只显示当前计划。

## 五、新增事件与审计

* 新增事件：`task.recovered`、`task.authorization_expired`（`task.replanning` 沿用）。
  payload 仍然只有 `task_id / step_id / state / timestamp` + 计划版本/原因，**没有 raw 世界状态**。
* checkpoint（`agent_task_checkpoints.detail`）显式记下
  `RUNTIME_RESTART` / `RECONCILED` / `REPLANNING` / `AUTHORIZATION_EXPIRED`，
  例如 `recovery reason=RUNTIME_RESTART outcome=TARGET_ALREADY_DONE plan_version=1`、
  `replan v2 reason=WORLD_CHANGED old=… new=…`。

## 六、状态机增量

只加了一条转移：`PENDING_CONFIRMATION → REPLANNING`（还没确认也可以重新规划）；
`REPLANNING → RUNNING` 仍然**不允许**（重规划期间只读，必须经 `replan()` 走到等确认）。
`drive()` 在 `REPLANNING` 与 `WAITING_ACTION` 状态都原地返回。

## 七、接口与 UI

* 端点没有变（仍然只有 `GET /minecraft/task`、`GET /minecraft/task/{id}`、
  `POST /minecraft/task/{id}/{pause|resume|cancel}`）；**仍然没有 confirm 端点**。
* `snapshot_payload` 新增：`plan_version`、`plan_status`、`plan_history`、`replan_required`、
  `replan_reason`、`recovery{reason,outcome,at,detail,message}`、
  `authorization{plan_hash,plan_version,approved_at,expires_at,remaining_seconds,valid}`、
  `authorization_expired_at`（剩余时间由服务端算，UI 只倒计时）。
* WebUI 面板新增 Plan Version / Replans / Authorization / Replan Reason / Recovery
  与**计划版本历史表**；`RESUME` 在需要重规划时会被后端拒绝（`task.replan_required`），
  用户要说「确认」让罐头重新观察出新计划。

## 八、验证

| 层 | 内容 |
| --- | --- |
| `tests/test_task_recovery.py`（19） | §三十五 A–O：WAITING_ACTION 读回、旧 action 失效、RUNTIME_RESTART、对账五种结论、绝不重复世界动作、授权到期（假时钟 t0+ttl-1 有效 / t0+ttl 过期）、重新确认、版本递增、hash 变化、恢复 checkpoint、recovered/replanning 事件、重确认后跑完；另含**真 SQLite**（`Database` + `SqliteTaskStore`）持久化与恢复 |
| `tests/test_task_replanning_security.py`（12） | §三十六 8 条：v1 不能授权 v2、v2 hash ≠ v1、v1 参数不能被换、未确认时 MEDIUM 永远被拒、TASK 凭据跨 task/跨 step 被拒且**一次性**、过期授权不能执行、WebUI/SYSTEM 不能确认 |
| `tests/test_minecraft_task_integration.py`（9） | task → action → 世界变了 → reconcile → replan → confirm → 新目标；以及"持久化重启后必须有真实用户确认" |
| WebUI vitest | 计划版本历史 / 恢复原因 / 授权状态 / replan 次数 |
| 真机 | `scripts/task_smoke_real.py` 新增「真实重规划」与「授权到期」两段；`scripts/task_restart_smoke_real.py` 用**两个进程**验证重启恢复 |

## 九、真机硬门禁（§四十一-§四十四）

```
Plan v1 = PASS            initial plan / initial confirmation
WORLD_CHANGED = PASS      smoke 自己扮演另一个玩家把目标挖掉
Plan v2 = PASS            new plan hash（v1 的 hash 与 v2 不同）
Confirmation v2 = PASS    v1 授权不能执行 v2
Task resumed = PASS       确认 v2 之后才继续，最终 FINAL INVENTORY VERIFIED
authorization expiry = PASS   短 TTL → MEDIUM 被拒 → 重新确认 → 继续
runtime restart safety = PASS SQLite 读回 → RUNTIME_RESTART → 只读对账 → PAUSED/REPLANNING
```

## 十、真机实测记录（2026-10-07，本机真实服务器）

脚本：`scripts/task_smoke_real.py`（正向 / pause-resume / cancel / 重规划 / 授权到期）与
`scripts/task_restart_smoke_real.py --phase create|recover`（**两个进程**）。

| 项 | 结果 |
| --- | --- |
| Plan v1 生成 → 确认 → 5 步全链路 → `FINAL INVENTORY VERIFIED {'oak_log': 1}` | PASS |
| Task pause / resume / cancel + `goal=null` / `isMoving=false` / 无泄漏前台动作 | PASS |
| **world change**（smoke 用 `/setblock … air` 扮演另一个玩家，读回确认已变 air） | PASS |
| **授权到期**（短 TTL：签出 → 过期 → LOW/MEDIUM 被拒 → 继续 → `PENDING_CONFIRMATION`（计划不变）→ 重新确认 → 继续跑完） | PASS |
| **RUNTIME RESTART SAFETY**（create 进程退出 → recover 进程：SQLite 读回 `WAITING_ACTION` + 原 action_id → `RUNTIME_RESTART` → 只读对账 `RECONCILED` → 旧 action 失效 → 旧确认作废 → `PAUSED` → **无重复世界动作** → `resume` 被拒 `task.replan_required`） | PASS |
| **重规划链**（v1 → WORLD_CHANGED → v1 `SUPERSEDED` → v2 → 新确认 → v2 成功） | **PASS**（`REAL SERVER: PASS`） |

重规划链的真实输出（`--sections replan`）：

```
Plan v1 generation = PASS（plan_hash=c576ebb75041426d2e67）
world change = PASS（(332,76,220) 现在是 air）
v1 confirmation = PASS（确认后第一步就发现目标没了）
old plan rejected = PASS（TARGET_LOST）
Plan v1 superseded = PASS（status=SUPERSEDED）
对账：reason=TARGET_LOST outcome=TARGET_ALREADY_DONE
new plan targets another block = PASS（(332,76,220) → (354,72,205)）
Plan v2 = PASS / new plan hash = PASS（c576ebb75041426d2e67 → ac7143709bd1b3319276）
v2 需要重新确认 / 旧确认已作废 / v1 授权不能执行 v2
new confirmation = PASS（WAITING_ACTION）
5 步全 SUCCEEDED（move_to → dig → dropped_items → pickup_item → inventory）
Task resumed = PASS（SUCCEEDED）/ Final verification = PASS（{'oak_log': 1}）
计划历史可审计（['SUPERSEDED', 'COMPLETED']）→ REAL SERVER: PASS
```

**已知限制（smoke，不是产品）**：真机上一次跑完五段时，罐头会被前几段移动/挖过，
所以后面几段能不能跑取决于"她当下站的地方附近有没有够得到的橡木"。跑单段最稳：

```powershell
.venv\Scripts\python.exe scripts	ask_smoke_real.py --sections replan
.venv\Scripts\python.exe scripts	ask_smoke_real.py --sections expiry
```

`--anchor x,z` 可以让脚本先把她放到指定那一列的地面（x/z 用真实数字，PowerShell 里别写尖括号）。
smoke 会在开头用 op 权限做环境准备（清一小块树冠 + 绝对坐标 `/tp` 落地 + SAFE `find_blocks`
确认附近有地面高度的橡木），并预检"事件是否真的回调到本脚本"（runtime 被 CatooBot 托管时它会
如实拒绝，而不是让任务卡死）。

## 十一、已知限制

* 重启恢复**不恢复**正在跑的 Mineflayer action（设计如此，见 §十七）；
* 重启后不能"接着跑旧计划"：必须新计划 + 新确认（比任务书要求更严，宁可多问一次）；
* QQ 侧任务入口仍然不做（§三十三：QQ → 单工具；Minecraft Chat / WebUI → Task），留给 5B；
* 没有通用 rollback / 补偿事务（与 5A 一致）。
