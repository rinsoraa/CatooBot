# CatooBot Phase 7F.5 — Runtime Lifecycle Reconciliation & Bridge Trust Boundary

> 基线：`f31b4ec600b4cebda002abd7c52d5bcaaa452542`（Phase 7F.4）。
> 本阶段加固 **Python TaskRuntime ↔ Node Minecraft Runtime** 之间的信任边界、动作关联与运行时生命周期恢复。
> **不新增任何 Minecraft 游玩能力**；不推翻 7F.4 的机制，只解决其遗留问题。
>
> 任务书归档：`docs/specs/CatooBot Phase 7F.5 — Runtime Lifecycle Reconciliation & Bridge Trust Boundary.md`
> （11484 bytes / 215 行，SHA256 `A79E3BE36D950EA1C1A958DA52BB0791C73A1981282D2E4A116573B591D085DA`，与上传源逐字节一致）。

---

## 1. 审计（§二）：状态机与故障窗口

### 1.1 哪些状态可靠持久化、哪些只能观察、哪些外部无法查询

| 事实 | 落在哪 | 可靠性 | 外部可查询？ |
| --- | --- | --- | --- |
| 派发意图（即将发出一次真实派发） | `agent_task_runs.payload` → `TaskStep.dispatch_state/gen/key/at`（7F.4） | **可靠持久化** | — |
| 外部**是否接受**了这次派发 | 无（只能由响应/事件推断） | **无法持久化** | **不可查询** |
| 已接受的 `action_id` | `TaskStep.action_id` + `TaskRecord.pending_action_id` | **可靠持久化** | **不可按键查询** |
| 动作真实进度 / 终态 | 只来自 `minecraft.action.*` 事件（Node → Python 回调） | **只能观察**（尽力而为，队列上限 200、失败即丢） | 只有 `GET /minecraft/status` 里**单个** `action` |
| 是哪个 Runtime **实例**接受的 | 7F.5 之前**无**；`action_id` 含进程内计数器，跨实例可能重号 | **本阶段必须补** | 本阶段补（health/status/事件都带实例标识） |
| 未决动作 ↔ 任务的关联 | `payload` 内的 `pending_action_id`；查询靠 `list_recent(50)` | 持久化可靠，**查询不完整** | 本阶段补齐（可重建索引） |

### 1.2 四个派发状态的语义（7F.4 已建立，本阶段保留并补齐实例信息）

| 状态 | 含义 | 本阶段变化 |
| --- | --- | --- |
| `INTENT` | 意图已持久化，外部是否接受**未知** | 同时记录**期望的运行时实例**（派发前的健康探测结果） |
| `ACCEPTED` | 已取得有效 `action_id`，等真实终态 | 追记**接受它的运行时实例**（权威关联） |
| `SETTLED` | 已取得明确终态 | 不变 |
| `AMBIGUOUS` | 无法可靠确定派发结果 → fail-closed | 歧义证据补齐：原因、实例、`action_id`、`dispatch_key`、代次、时间戳 |

### 1.3 故障窗口表（逐条对应任务书 §七.1 的十个场景）

| # | 故障窗口 | 现状（7F.4） | 本阶段目标行为 |
| --- | --- | --- | --- |
| 1 | 派发前 Runtime 尚未启动 | 意图已落盘，工具调用失败（`runtime.unreachable` → **歧义**） | 保持 fail-closed；`INTENT` 无 `action_id` → 受控暂停 + 人工核对 |
| 2 | 意图已持久化、请求未发出 | 同样 `INTENT` 无 `action_id` | 同上（磁盘上无法区分"没发出"与"发出了没答复"，故不得猜） |
| 3 | Runtime 已接受、客户端响应丢失 | `ambiguous=True` → 受控暂停 + 清授权 + 尽力真停 | 不变；歧义证据补上实例 |
| 4 | **Node 在动作进行中重启、Python 不重启** | 终态事件永不到达 → 任务停在 `WAITING_ACTION` **直到 TTL 过期**（被动） | **主动对账**：发现当前实例 ≠ 记录实例 → 立即分类为歧义、清授权、要求人工核对 |
| 5 | 重启后新动作产生与旧动作**相同的局部序号** | `action_id` = `act_<base36 ms>_<seq>`，同毫秒 tick + 同序号即重号 → 可能误匹配 | 事件关联必须**同时**校验实例；实例不同 → 不得结算新实例的动作 |
| 6 | 旧实例**迟到事件**在新实例启动后到达 | 只按 `action_id` 匹配 → 可能误结算 | 实例不匹配 → 不结算，转入歧义（旧动作状态不可查询） |
| 7 | 旧任务位于**最近 50 条之外**但仍有未决动作 | `list_recent(50)` → 事件被丢弃、任务等 TTL | 用**可重建的完整未决查询**（无固定条数上限） |
| 8 | 多条任务记录**冲突**关联同一 `action_id` | `next(...)` **取第一个匹配项**（猜测） | 多匹配 → 一律按**歧义**处理，绝不猜归属 |
| 9 | 健康检查**暂时**失败、之后恢复 | `_ensure_runtime` 计数并重启（有界） | 不得由**单次**失败推断实例已死；实例变化只凭**成功的**身份响应确认 |
| 10 | Runtime 已恢复运行，但旧动作真实状态**不可查询** | 无查询能力 → 无法确认 | **不得**因为新 Runtime 可用就重新派发；进入明确歧义 + 人工核对 |

### 1.4 鉴权边界审计（§六）

| 端点 | 现状 | 目标 |
| --- | --- | --- |
| `GET /minecraft/health` | 仅在 `BRIDGE_TOKEN` 非空时校验（Python **从不设置**它 → 实际匿名） | 纳入鉴权；同时返回**实例标识**供生命周期对账 |
| `GET /minecraft/status` | 同上（匿名） | 纳入鉴权；返回实例标识 |
| `POST /minecraft/move_to`、`dig`、`place`、`craft`、`container_transfer`、`pickup_item`、`follow_player`、`chat`、`equip`、`inventory_move`、`connect`、`look_at` | 匿名 | 纳入鉴权（缺失/错误/格式错 → **401，且无任何副作用**） |
| `POST /minecraft/stop`、`/disconnect` | 匿名 | 纳入鉴权（控制端点同样受保护） |
| `BRIDGE_TOKEN` 部署值 | **无**（全仓只有 `runtime.js:44` 一处引用） | 由 Python 每进程生成并经 `MC_BRIDGE_TOKEN` 注入；**未配置则拒绝启动**（不留匿名旁路） |
| Token 泄漏面 | — | 不得进入 Git / 日志 / 错误响应 / 事件摘要 / WebUI |

**唯一的 Python → Node 生产通路是 `MinecraftService`（`app/integrations/minecraft/service.py`）**；
真机脚本各自构造 `MinecraftService(None, config.minecraft)` 并自行拉起 runtime（因此每进程 token 对它们同样成立）；
没有测试直接构造 `MinecraftRuntimeClient`；WebUI 只对 Python 说话，不直连 Node。

## 2. 最小修复计划

在**不新增第二套任务系统、不做数据库迁移**的前提下，复用现有 `agent_task_runs.payload` 与既有协议：

1. **运行时实例身份**：Node 在进程启动时生成实例标识（`rt_<base36 时间>_<随机>`），写入 `health`、`status`、**以及每一条 `minecraft.action.*` 事件**；Python 在既有健康探测里记录当前实例，并在**接受动作时**把它固化到 `TaskStep.dispatch_runtime_instance`。实例标识**只解决关联唯一性，不是幂等键**。
2. **完整未决动作查询**：给任务存储加两个**可从持久化记录重建**的方法 —— `list_pending()`（`WHERE state NOT IN 四个终态`，无固定条数上限）与 `find_pending_by_action(action_id)`（多条匹配**全部**返回）。`on_action_event()` 与启动恢复都改用它，彻底摆脱"最近 50 条"。
3. **事件关联加固**：0 匹配 → 未知事件，什么都不改；≥2 匹配 → **歧义**（暂停全部匹配记录，绝不取第一个）；1 匹配 → 再校验实例，实例不符（旧实例迟到事件）→ 不结算，转歧义。
4. **生命周期对账**：Python 观察到**当前实例 ≠ 记录实例**（依据成功的身份响应，不是单次失败）时，主动把所有属于旧实例的未决动作分类处理；无查询能力 → 一律 `AMBIGUOUS` + 清授权 + 要求人工核对，并保存原因/实例/`action_id`/`dispatch_key`/代次/时间戳。
5. **桥接鉴权**：Python 每进程生成 `bridge_token` 并注入 `MC_BRIDGE_TOKEN`；Node 在**所有路由之前**强制校验（缺失/错误 → 401，且不触发动作、不改任务状态）；未配置 token 时**拒绝启动**。Python 客户端对每次请求带上 `Authorization`。既有 E2E harness 迁移到同一安全契约（不再保留匿名旁路）。
6. **测试**：新增 Python 侧生命周期故障注入（覆盖 §七.1 的 10 个场景）+ **协议层**鉴权测试（真实 HTTP 入口：缺失/错误/有效凭据、stop/cancel 同样受保护、鉴权失败无副作用、token 不出现在响应/日志） + Node 侧 E2E 鉴权段（真实 spawn + 真实 HTTP）。
7. **不动的东西**：19 工具 / `ACTION_RISK` / `allow_medium=false` / 两道门 / Policy / LifeIntent·Proposal·AgentPlan 语义 / 多段 ≤3 与 24 格预算 / `explore_legs=1` 默认值 / 无界重试 / 真实 Bible 与密钥。

---

## 3. 实现：运行时实例身份、动作关联与持久化

### 3.1 实例标识（Node 侧）

`minecraft_runtime/runtime.js` 在**进程启动时**生成一次：

```js
const RUNTIME_INSTANCE = `rt_${Date.now().toString(36)}_${crypto.randomBytes(4).toString('hex')}`
```

它在**本进程生命周期内稳定**，并出现在三处：

| 位置 | 作用 |
| --- | --- |
| `GET /minecraft/health` → `runtime_instance` | Python 用它做生命周期对账（**只有成功的身份响应才算数**） |
| `GET /minecraft/status` → `runtime_instance` | 状态可观测性 / 人工核对 |
| **每一条 `minecraft.action.*` 事件** → `runtime_instance` | 事件归属校验（旧实例事件不得结算新实例的动作） |

`action_runtime.js` 把它记进动作 record（`view()`/快照可见），并在 `started` 与终态事件里都带上。

### 3.2 动作关联（Python 侧，持久化在既有 payload 里）

`TaskStep` 新增一个字段（**双向序列化**，无需迁移）：

| 字段 | 含义 |
| --- | --- |
| `dispatch_runtime_instance` | **接受这次动作的 Runtime 实例**（派发时先记期望值，`ACCEPTED` 时以响应/事件为准固化） |

它只解决「这条事件属于哪个实例」的**关联唯一性**：不是幂等键、不提供任何服务端去重或查询保证。
旧记录（7F.5 之前）该字段为空串 → 跳过实例校验（按 action_id 关联），**向后兼容**。

### 3.3 四个派发状态语义（保留 7F.4）

`INTENT`（意图已落盘、外部结果未知）/ `ACCEPTED`（`action_id` 已知、等终态）/ `SETTLED`（终态已知）/
`AMBIGUOUS`（无法可靠确定 → fail-closed）。全部可持久化、可序列化，重复事件幂等。

## 4. 完整未决动作查询（摆脱「最近 50 条」）

新增两个**可从持久化记录重建**的存储方法（`app/tasks/store.py`，`TaskStore` 协议 + 内存/SQLite 两实现）：

| 方法 | 语义 |
| --- | --- |
| `list_pending(limit=None)` | **所有**非终态任务（SQL：`WHERE state NOT IN (四个终态)`，默认**不限条数**） |
| `find_pending_by_action(action_id)` | 所有仍在等这个 `action_id` 的未决任务（返回**列表**，多匹配交给上层判歧义） |

* 用**真实的 `state` 列**做完整性过滤，再在 Python 侧解析 payload —— 不依赖 `json_extract`、
  不引入内存索引、**不新增表、不做迁移**；
* `recover_persisted_tasks()` 改用 `list_pending()`（默认不再截断），启动恢复能重建**全部**相关联的未决任务；
* `on_action_event()` 改用 `find_pending_by_action()`。

### 4.1 事件关联规则（`on_action_event`）

| 匹配情况 | 行为 |
| --- | --- |
| 0 条 | 未知事件 → **什么都不改**（任务状态、授权、派发代次一律不动） |
| ≥ 2 条 | **歧义**：把所有匹配记录都安全停下（`action_id_ambiguous`），**绝不取第一个匹配项猜归属** |
| 1 条，实例不符 | 旧实例迟到事件 → **不结算**，转歧义（`stale_runtime_instance_event`） |
| 1 条，实例一致（或缺失） | 正常结算为 `SETTLED`，并**持久化清理** `pending_action_id/pending_step_id`（重复事件只被接受一次） |

## 5. Runtime 重启前后的恢复策略

### 5.1 如何识别实例变化（不靠猜）

`MinecraftService._note_runtime_instance()` 的纪律：

* **只有成功的身份响应**才更新「当前实例」；
* 单次请求失败、单次健康检查失败、无身份的响应 → **一律不改实例、不触发对账**（§五.1 明确禁止据此推断）；
* 首次见到实例只记录不通知；同一实例重复成功不重复告警；**真的换了**才通知一次；
* 通知接到 `TaskRuntime.on_runtime_instance_changed()`（`app/core/bot.py` 装配，`app/tasks/runtime.py` 实现）。

`_ensure_runtime()` 的每次健康探测顺带完成身份探测，所以**每次派发前的实例都是最新的**。

### 5.2 未决动作的分类处理

| 情形 | 处理 |
| --- | --- |
| 有可靠终态证据 | 按已知结果收敛（`SETTLED`） |
| 有可靠证据证明动作已失效 | 既有恢复语义：暂停 + 要求重新规划（`RUNTIME_RESTART`） |
| 外部状态**无法确认**（含"实例已换人"） | 进入明确歧义：`AMBIGUOUS` + 失败分类 `DISPATCH_AMBIGUOUS` + **清授权** + `replan_required` + 人工核对 |
| Runtime 已恢复但旧动作状态**不可查询** | **不得重新派发**（§五.2 明令禁止）；歧义状态不会被"新 Runtime 可用"洗掉 |

歧义证据（`record.recovery.detail`）**完整留存**：`dispatch_reason` / `runtime_instance` / `action_id` /
`dispatch_key` / `dispatch_generation` / `step_id` / `at` 时间戳。

恢复与健康检查本身**不产生任何世界动作**（`_stop_action()` 是既有 SAFE 控制面，只在歧义暂停时尽力真停）。

## 6. 桥接 API 鉴权边界

### 6.1 覆盖的端点

鉴权门位于 `handleRequest()` 的**所有路由之前**（`runtime.js`），因此下面每一条都受保护：

| 类别 | 端点 |
| --- | --- |
| 身份 | `GET /minecraft/health`、`GET /minecraft/status` |
| 只读世界 | `GET /minecraft/world/snapshot`、`GET /minecraft/inventory`、`GET /minecraft/inventory/slots` |
| 动作 | `POST /minecraft/move_to`、`/look_at`、`/dig`、`/place`、`/craft`、`/container_inspect`、`/container_transfer`、`/pickup_item`、`/equip`、`/inventory_move`、`/follow_player`、`/chat`、`/recipe_lookup`、`/dig_capability`、`/find_blocks` |
| 控制 | `POST /minecraft/connect`、`/stop`、`/disconnect` |

### 6.2 凭据注入与拒绝行为

* Python 每进程生成 `secrets.token_urlsafe(32)`（`external_bridge_token` 配置非空则用它），
  经 `MC_BRIDGE_TOKEN` 注入 Node 进程环境；客户端对**每一次**请求带 `Authorization: Bearer <token>`；
* Node：**未配置凭据则拒绝启动**（实测 `exit=1` + `MC_BRIDGE_TOKEN 未配置：拒绝启动（桥接动作 API 不允许匿名调用）`）
  —— 不留匿名旁路；
* 缺失 / 错误 / 格式错的凭据 → `401 auth.invalid`，在**进入任何动作逻辑之前**返回；
* 凭据**不进** Git、日志、错误响应、事件内容、任务 payload 与 WebUI（`app/` 内只有 `service.py` 三处使用 + 配置字段）；
* 桥接 token **不是**授权替代品：它只校验"调用者身份"，与 `TaskAuthorization` / `TaskStepAuthorization` /
  用户确认门是**正交**的两件事。

### 6.3 实际拒绝请求的证据（真实 HTTP）

`minecraft_runtime npm test` 的 E2E 段在**真实 spawn 的 runtime** 上验证：缺失凭据 → 401、错误凭据 → 401、
`health`/`stop`/`disconnect`/`move_to` 无凭据 → 全部 401、有效凭据 → 200，**且鉴权失败后 `action.active_count == 0`**
（没有动作被启动），并且凭据不出现在 401 响应体与 runtime 日志里；
Python 侧 `tests/test_bridge_auth_boundary.py` 用**真实本地 HTTP 服务器 + 真实客户端**验证同样的契约。

---

## 7. 故障注入证据（§七）

### 7.1 Runtime 生命周期（10 个场景 → 14 个用例）

文件：`tests/test_runtime_lifecycle_reconciliation.py`（14 项）。断言对象是**外部世界动作真实启动次数**、
任务状态、**授权状态**、**派发代次**，以及**事件匹配到了谁**。

| # | 场景 | 用例 | 动作计数 | 状态转移 | 授权检查 |
| --- | --- | --- | --- | --- | --- |
| 1 | 派发前 Runtime 未启动 | `test_dispatch_before_runtime_started_is_fail_closed` | 1（唯一一次尝试），恢复/驱动后仍 **1** | → `PAUSED` / `DISPATCH_AMBIGUOUS` | **已清** + `replan_required` |
| 2 | 意图已落盘、请求未发出 | `test_persisted_intent_keeps_instance_evidence_and_is_not_retried` | **0** | → `PAUSED` / `DISPATCH_AMBIGUOUS` | **已清** + 留实例/关联键证据 |
| 3 | 已接受但响应丢失 | `test_ambiguous_dispatch_evidence_is_complete` | 1 | → `PAUSED` / `DISPATCH_AMBIGUOUS` | **已清**；证据含 reason/实例/action_id/key/代次/时间戳 |
| 4 | Node 动作中重启、Python 不重启 | `test_runtime_restart_mid_action_enters_explicit_ambiguity` | 1（**不重发**） | `WAITING_ACTION` → `PAUSED` / `DISPATCH_AMBIGUOUS` | **已清**；`dispatch_reason=runtime_instance_changed` |
| 5 | 重启后新动作重用旧局部序号 | `test_stale_instance_event_does_not_settle_the_new_action` | 1 | 未 `SUCCEEDED` → `PAUSED` / `DISPATCH_AMBIGUOUS` | **已清**；`stale_runtime_instance_event` |
| 6 | 旧实例迟到事件 | 同 #5（事件带 `INSTANCE_B`，任务记录 `INSTANCE_A`） | 1 | 不结算，转歧义 | **已清** |
| 7 | 未决任务在最近 50 条之外 | `test_pending_task_beyond_the_recent_window_is_still_found` | 0 | `WAITING_ACTION` → 步骤 `SUCCEEDED`（**被找到并结算**） | 消费后 `pending_action_id` **持久化清空** |
| 8 | 多条记录冲突关联同一 action_id | `test_conflicting_action_correlation_is_ambiguous` | **0** | **两条都** → `PAUSED` / `DISPATCH_AMBIGUOUS` | 两条都**已清**；`action_id_ambiguous` |
| 9 | 健康检查暂时失败、之后恢复 | `test_transient_failure_does_not_change_instance` | — | 实例**不变**、不触发对账 | 不涉及授权变更 |
| 10 | Runtime 已恢复但旧动作不可查询 | `test_runtime_back_but_old_action_unqueryable_stays_ambiguous` | 1（**不重发**） | 保持 `PAUSED` / `DISPATCH_AMBIGUOUS` | `resume()` 仍被拒 |

补充用例（同样属于 §四 的要求）：

| 要求 | 用例 | 证据 |
| --- | --- | --- |
| 匹配一致时正常结算 | `test_matching_instance_event_settles_normally` | → `SUCCEEDED`，动作计数 **1** |
| 接受动作时记下实例 | `test_accepted_action_records_the_accepting_instance` | `dispatch_runtime_instance == INSTANCE_A`，`ACCEPTED` |
| 未知事件不改变任何东西 | `test_unknown_action_event_changes_nothing` | 状态/pending/授权/代次全部不变 |
| 重复终态事件只接受一次 | `test_duplicate_terminal_event_is_consumed_once` | 第二次返回 `None`，动作计数不变，pending 已清 |
| 恢复重建**全部**关联未决任务 | `test_recovery_rebuilds_correlated_pending_tasks_beyond_window` | 61 条记录中**最老**的未决任务出现在恢复集合里 |

### 7.2 API 鉴权（8 个场景 → 8 个协议层用例 + E2E 段）

Python 侧文件 `tests/test_bridge_auth_boundary.py`（**真实本地 HTTP 服务器 + 真实 `MinecraftRuntimeClient`**）；
Node 侧在 `minecraft_runtime npm test` 的 E2E 里（**真实 spawn 的 runtime + 真实 HTTP 请求**）。

| # | 要求 | 证据 |
| --- | --- | --- |
| 1 | 未带凭据的动作请求被拒且无副作用 | `test_missing_credential_is_rejected_without_side_effects`（401 + `auth.invalid`，服务器侧确认**没有** Authorization 头）；E2E：`stop`/`disconnect`/`move_to` 无凭据 → 401 且 `active_count == 0` |
| 2 | 错误凭据被拒 | `test_wrong_credential_is_rejected`（服务器侧看到 `Bearer wrong-token`）；E2E 同 |
| 3 | 有效凭据可正常执行 | `test_valid_credential_is_actually_sent_and_allowed`（服务器侧看到正确凭据、返回 200 与 `runtime_instance`）；E2E 同 |
| 4 | `stop`/cancel 控制端点同样鉴权 | E2E：`POST /minecraft/stop` 无凭据 → 401 |
| 5 | 鉴权失败不改变 Task/授权/派发状态 | `test_auth_failure_is_definitive_not_ambiguity`（`auth.invalid` **不在** `AMBIGUOUS_TRANSPORT_CODES` 里 → 不会被当成"动作可能已接受"）；E2E `active_count == 0` |
| 6 | Token 不出现在日志/错误响应/事件/提交文件 | `test_missing_credential_is_rejected_without_side_effects` / `test_wrong_credential_is_rejected` 断言凭据不在错误消息与 detail 里；E2E 断言不在 401 响应体与 **runtime 日志**里；`app/` 内只有 `service.py` 与配置字段引用它 |
| 7 | 重启后凭据注入确定且可审计 | `test_bridge_token_is_generated_once_per_process_and_differs_across_restarts` / `test_runtime_env_injects_the_same_bridge_token` / `test_external_token_from_config_is_used_as_is`；Node：未配置凭据 → **拒绝启动**（实测 exit 1） |
| 8 | 现有 WebUI 与 Python → Node 路径保持兼容 | WebUI 只与 Python 说话，不直连桥接；`MinecraftService` 的每次调用都经同一个客户端带凭据；`minecraft_runtime npm test` 全部套件（含 E2E）通过 |

---

## 8. 门禁（§九）

### 8.1 本地（按既有流程串行）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 通过（无格式差异） |
| `mypy app` | Success: no issues found in 336 source files |
| 全量 `pytest tests -q` | **3802 passed in 579.47s**（基线 3779 + 23 项新增） |
| WebUI `npm run typecheck` | 通过（exit 0） |
| WebUI `npm test`（Vitest） | 75 files / **528 passed** |
| WebUI `npm run build` | 通过 |
| WebUI `npm run test:e2e`（Playwright） | **7 passed** |
| `minecraft_runtime npm test` | ALL CHECKS PASSED（含**新增鉴权段**：真实 spawn + 真实 HTTP） |
| 迁移演练 v36 → v36 | 幂等；83 表；`integrity ok`；`lost_rows {}`；`quick_check ok`；FK 违规 0 |

> 新增 23 项 = `test_runtime_lifecycle_reconciliation.py` 14 + `test_bridge_auth_boundary.py` 9。
> **无新表、无新迁移**（最高仍 36）。

### 8.2 真实 Fabric 1.21.1


前置：备份 live DB（`CatooBot_backups` 中的 v36 / 83 tables / 87670 rows / `integrity=ok` 快照）、停 CatooBot 释放 runtime 唯一会话
（25580 释放、25565 在线）；执行后按原方式重启（25580 / 8500 恢复）。顺序：7F.1 → 7F.2 → 多段（多段最后跑，避免污染前两段的起始环境）。

**① `scripts/explore_smoke_real.py --sections all`（单段 positive / blocked / cancel）→ PASS（13 项）**

* 正向：起点 `(-266.37, 69.0, -131.5)` → 目标 `(-270, 64, -129, r=2)`，`SUCCEEDED`、`verification.ok=True`
  （`position_within.distance = 1.59`）、位移 6.2、无遗留移动；
* blocked：不可达目标 → 安全 `PAUSED` / `ACTION_FAILED`，绝不冒充到达；
* cancel：导航中取消 → `CANCELLED` + 经 `minecraft_stop` 真停 + **驱动终态任务不再移动角色**。

**② `scripts/feedback_smoke_real.py`（Phase 7F.2 真实记忆反馈）→ PASS（19 项）**

起点 `(-270.67, 69.0, -126.53)`；探索 `SUCCEEDED` + 真世界到达复核 → 写入 **4 条**真实记忆 → `MINECRAFT_INTEREST`
LifeIntent → `TaskProposal`（created，来源 LIFE，`intent_id` 一致）→ `AgentPlan`（`READY_FOR_APPROVAL`，提案/意图可追溯，
规划阶段未建执行任务）→ 第一道门批准 → 停在 `PENDING_CONFIRMATION` 且**零真实动作**。

**③ `scripts/explore_multi_leg_smoke_real.py --sections all`（多段：正向 / 途中失败 / 取消）→ PASS**

* **A 正向**：起点 `(-268.54, 64.0, -128.5)`；真实冻结路线 **2 段**（本段 4.8 / 12.2 格，**总 17.0 ≤ 24**），`SUCCEEDED`（距 1.58），
  **逐段证据 2/2 全部 verified 且带真实坐标**（实际距 **1.57 / 1.58**，严格半径 2.0），
  action id `act_mv41wyl4_1` / `act_mv41wzb6_3`，位移 6.5；
* **B 途中失败**：把第 2 段改成同一 x/z、**正上方 40 格** → `PAUSED` / `ACTION_FAILED`（路径计算超时），
  逐段证据 1 条 `[(1, True)]`，**被改段之后的 4 个步骤全部 `PENDING`**（`step_4/5/6/7`），无持续移动；
* **C 取消**：3 段路线导航中 `WAITING_ACTION` → `cancel` → `CANCELLED` + 真停 + **后续段 `step_3/step_5` 仍 `PENDING`** + 无移动。

> **本阶段真机门禁没有出现任何失败，因此没有重跑。**（7F.4 阶段记录的 pathfinder 精度抖动本轮未复现。）
> 真实门禁会真实写 live DB：本轮 `life_intents` 115 → **118**、`activity_episodes` 210 → **214**；事后 `integrity ok`、
> 最高迁移仍 **36**、仍 **83** 张业务表、FK 违规 0。
> **鉴权变更对真机门禁透明**：脚本各自 `service.start()` 拉起 runtime ⇒ 每进程凭据由 Python 生成并注入，
> 客户端自动携带 —— 不需要改脚本，也没有为通过门禁保留任何匿名旁路。

---

## 9. CI、数据库与敏感文件边界

### 9.1 CI（GitHub Actions）

| 项 | 结果 |
| --- | --- |
| commit | `4d732628c0f04f36bafbf51560f166fda99a0a80` |
| attempt 1 | `webui · typecheck · tests · build` = **success**；`lint · format · types · tests` = **failure** |
| 失败原因 | **项目已知的 follow_player E2E 抖动**：`[e2e] FAILED: Error: timeout waiting for: runtime 看见 Followee`
（`minecraft_runtime/test/e2e.js:110` 注释就记录过同一现象：机器繁忙时实体包与区块加载滞后）。
**不是本阶段引入**，且它发生在 follow 段、**早于**新增的鉴权段 |
| 处置 | 按既有纪律触发 `rerun-failed-jobs`（**没有**为通过而修改断言或放宽阈值） |
| attempt 2 | **两个 job 均 success** |

**attempt 2 的 CI 证据**：`ruff check .` = All checks passed；`ruff format --check .` = 637 files already formatted
（CI 只看到镜像内文件）；`mypy app` = Success（336 source files）；`pytest tests` = **3799 passed, 3 skipped in 311.69s**
（与本地 3802 精确对账：差的 3 条即既有平台条件性 skip，本阶段新增 **23 项无一被跳过**）；Browser E2E = 7 passed (21.3s)；
`minecraft_runtime` 全部套件 = ALL CHECKS PASSED，其中包含
**`[e2e] bridge auth boundary + runtime instance ✓ (missing/wrong -> 401, valid -> 200, no bypass, no token leak)`**
—— 鉴权边界在 CI 的真实 spawn + 真实 HTTP 下也通过了。


### 9.1.1 同一族 flake 的第二次实例（补记文档提交）

补记 CI 结果的**纯文档提交**（`cc6df91de62f730580ddbdd784a79694e32732e7`，只改 `docs/MINECRAFT_PHASE7F5.md`）
在 attempt 1 也失败了，但失败点**不是**本阶段任何代码路径：

```
[e2e] FAILED: Error: timeout waiting for: runtime 看见 Ghost
```

同一运行里 `[pickup-test]`、`[dig-capability-test] 43/43`、`[find-blocks-test] 41/41` 等全部通过 ——
与 §9.1 记录的 `runtime 看见 Followee` 属于**同一族**（follow 段落里等待"运行时看得见某个实体"的检查，
机器繁忙时实体包/区块加载滞后就会超窗；`minecraft_runtime/test/e2e.js:110` 早有此注释）。

处置同样如实：不改断言、不放宽阈值，触发 `rerun-failed-jobs` → **attempt 2 两个 job 均 success**。

**统计（本阶段三次 CI 运行）**：代码提交 attempt 1 失败（`看见 Followee`）→ attempt 2 成功；
文档提交 attempt 1 失败（`看见 Ghost`）→ attempt 2 成功。两次失败都在 follow 段、都与本阶段改动无关，
但**如实记录**，不掩盖。

### 9.2 数据库

* **无新表、无新迁移**（最高仍 36）；演练 `version [36,36]`、`tables [83,83]`、`integrity [ok,ok]`、`lost_rows {}`、
  `quick_check ok`、FK 违规 0；
* 逐段证据与运行时实例标识都落在**既有** `agent_task_runs.payload`（单个 TEXT 列）里；
* 真机后 live DB：`integrity ok`、`max_version 36`、83 张业务表、FK 违规 0。

### 9.3 敏感文件边界

| 项 | 状态 |
| --- | --- |
| 19 工具 / `ACTION_RISK` / `allow_medium=false` | **未改** |
| LifeIntent / TaskProposal / AgentPlan 语义 | **未改** |
| 计划批准 + 第二道执行确认 / `TaskAuthorization` / `TaskStepAuthorization` / Policy | **未改**（桥接凭据**不是**它们的替代品） |
| 多段 ≤3 段与共享 24 格预算 / `explore_legs=1` 默认值 | **未改** |
| 无界重试 | **未引入** |
| 真实 Character Bible / 密钥 / `.env` | **未触碰**（凭据每进程内存生成，`.env` 未新增任何键） |
| `.env` / `config/config.yaml` / `data/` / `logs/` / `auth.json` | 镜像**未跟踪**（`git ls-files` 扫描为空） |
| 发布边界 | `scripts/` 仍**不进** GitHub 发布（实测镜像无 `scripts`） |
| 发布前密钥扫描 | 镜像 diff 命中项全是**代码引用**（变量名 / 环境变量名 / `secrets.token_urlsafe`），**无任何真实凭据值** |
| 凭据暴露面 | 不进 Git / 日志 / 错误响应 / 事件 / 任务 payload / WebUI（注册表标记为**敏感项**，值永不返回） |

---

## 10. 失败、重跑、SKIPPED 与遗留限制

### 10.1 开发期被仓库自己的契约测试抓住的一个真实缺口

新增 `minecraft.external_bridge_token` 设置后，`tests/test_web_api_config.py::TestConfigSchema::
test_every_item_is_annotated_and_carries_its_class` **失败**：`缺少说明: ['minecraft.external_bridge_token']`。
这不是误报 —— 该字段确实没有进入 WebUI 配置注册表，因此既没有中文标签/说明，也**没有被标记为敏感**，
意味着设置页**会把它当普通字段显示/返回**，直接违反 §六「凭据不得写入 WebUI」。

处置：在 `app/web/config_registry.py` 补 `_LABELS`（中文标签）、`_NOTES`（一句人话说明）与
`_SENSITIVE_LEAVES`（**敏感项**，值永不返回，只显示"是否已配置"），并新增
`test_bridge_token_field_is_sensitive_so_the_webui_never_sees_a_value` 把这条钉住。修好后全量 3802 passed。

### 10.2 SKIPPED

`minecraft_runtime` 套件内既有 SKIP（假服务器不支持 `/summon` / `/give`、背包等）与本阶段无关；
**本阶段没有新增任何 SKIP**，也没有以 mock 替代任何真实门禁。

### 10.3 遗留限制（因此**不宣称 exactly-once / 不宣称服务端幂等**）

1. **无法证明外部动作是否真的发生**：Minecraft runtime 仍然**不支持**客户端幂等键，也**没有按键查询**；
   实例标识只解决「这条事件属于哪个实例」的**关联唯一性**，不是幂等键、不提供去重保证。
2. **桥接重启后旧动作的终态永远不会到达**：本阶段让 Python **主动识别实例变化**并立即进入明确歧义
   （不再被动等到 TTL 过期）；但"那个动作到底做没做"依旧**不可查询** → 只能人工核对后重新规划。
3. **终态事件仍可能丢**（桥接事件队列上限 200、投递失败即丢最旧）：这属于"无法确认"→ 走歧义路径，
   **不会**被自动重发，但也**不会**被自动补上。
4. **`action_id` 仍是进程内计数器**（跨实例可重号）：现在靠实例标识区分归属；若持久化记录里**没有**实例
   （7F.5 之前的旧记录，或外部托管桥接未上报实例），则退化为仅按 `action_id` 关联 —— 已向后兼容，如实记录。
5. **桥接凭据只校验「调用者身份」**：它不是 `TaskAuthorization` / `TaskStepAuthorization` / 用户确认门的替代品；
   本机同用户下能读到 `MC_BRIDGE_TOKEN` 的进程依然可以调用该 API（这是本机信任边界的固有属性，不是本阶段引入）。
6. **实例变化依赖一次成功的身份响应**：若 Node 长时间不可达（一直 401/超时），实例变化不会被发现 ——
   此时也不会有任何新动作被派发（fail-closed）；恢复后第一次成功的 `health` 才会触发对账。
7. **新增成本**：每次真实派发前多一次 `_save`（7F.4 起）、每次 API 调用前一次健康探测（既有行为）、
   响应/事件里多一个实例字段；`.env` 未新增任何键（凭据每进程内存生成，除非运维显式配置）。



