# CatooBot Minecraft Phase 3B · Safe Action Layer

> 在 Phase 3A movement-aware WorldPerception 之上建立 **Action Runtime 基础层**：
> 未来的 `move_to / follow / dig / place / craft` 共用的请求模型 / 生命周期 / action_id /
> 超时 / 取消 / 并发互斥 / 状态回报 / 安全停止 / 失败恢复全部就位。
>
> **本阶段只开放 `look_at` 与 `stop`，`chat` 纳入统一生命周期；没有加入任何移动能力**
> （无 `/minecraft/move*`、无 pathfinder、无 setControlState 对外 API）。

---

## 1. 修改文件清单

**新增**

| 文件 | 说明 |
|---|---|
| `minecraft_runtime/action_runtime.js` | ActionRuntime 独立模块（注册表 / 单前台锁 / 取消令牌 / 超时 / 事件 / 日志） |
| `minecraft_runtime/test/action_runtime.test.js` | Node 单测（44 checks：生命周期/互斥/取消/超时/停止/事件） |
| `tests/test_minecraft_actions.py` | Python 服务层测试 18 个（look_at / stop / 并发 / 生命周期 / 稳定性） |
| `docs/MINECRAFT_PHASE3B.md` | 本文档 |

**修改**

| 文件 | 变更 |
|---|---|
| `minecraft_runtime/runtime.js` | 接线 ActionRuntime：注册 look_at/chat/stop；新增 `POST /minecraft/look_at`、`POST /minecraft/stop`；chat 改走动作生命周期（HTTP 契约不变）；`status.action`；断开/收尾 cancelAll |
| `minecraft_runtime/test/e2e.js` | §十八动作流：look_at（事件回报 pitch）→ chat → stop → 仍 ONLINE，逐轮 ×3 |
| `minecraft_runtime/package.json` | `npm test` = 单测 + E2E |
| `app/integrations/minecraft/events.py` | 事件枚举 +5：`minecraft.action.started/completed/failed/cancelled/timeout` |
| `app/integrations/minecraft/runtime_client.py` | `look_at()` / `stop()` |
| `app/integrations/minecraft/service.py` | `look_at()` / `stop_action()`；动作错误类（busy/invalid/failed）；`_translate` 顺序；动作生命周期镜像；snapshot `action` 块 |
| `app/web/api/minecraft.py` | `POST /api/v1/minecraft/look_at`、`POST /api/v1/minecraft/stop`；disabled 快照补 action=IDLE |
| `tests/test_minecraft_service.py` | FakeRuntime +look_at/stop 端点与可编排响应 |
| `tests/test_web_api_minecraft.py`、`tests/test_web_routes.py` | 端点测试 + 路由快照 +2 |
| `webui/src/types/minecraft.ts`、`api/minecraft.ts`、`pages/Minecraft.vue`、`pages/__tests__/minecraft.spec.ts`、`dist/*` | Current Action 区 + Look At Test / STOP 按钮 + 4 个用例 + 重建 |
| `.github/workflows/ci.yml` | Minecraft 步骤改跑 `npm test`（单测 + E2E） |
| `docs/WEBUI_API_CONTRACT.md`、`WEBUI_V1_ROUTE_MATRIX.md`、`README.md` | 契约 §7.5、路由矩阵 212 条、索引 |

**未新增**：任何移动/挖掘/放置/攻击能力；`look_at`/`stop` **没有**暴露给 LLM Tools（§十四）。

## 2. Action Runtime 架构

```
Minecraft Runtime
├── Connection Runtime（Phase 1）
├── World Snapshot（Phase 2）
└── Action Runtime（Phase 3B · action_runtime.js）
      ├── Action Registry      注册表：look_at / chat / stop（未来 move_to/dig/… 复用）
      ├── Action Queue / Lock  单一 foreground（exclusive）互斥；chat 非互斥
      ├── Cancellation         令牌取消（stop / disconnect / shutdown）
      ├── Timeout              每动作超时（look_at 5s / chat 5s / stop 3s）
      ├── Safety Stop          stop：取消全部 → clearControlStates → IDLE
      └── Action Events        started/completed/failed/cancelled/timeout → CatooBot
```

- `action_id` **只由 runtime 生成**（`act_<base36 时间>_<序号>`），调用方不得自定义；
- 模块不依赖 Minecraft 对象：bot 由 `getBot()` 注入、动作由调用方注册（可被单测
  用假动作直接验证）；
- `stop` 是**控制面动作**：不占前台、不产生自己的动作记录；幂等、无 bot 也安全、
  返回被取消的 `action_id` 列表。`clearControlStates()` 只在这里（与 look_at 超时
  cleanup）内部使用，绝不作为 API 暴露。

## 3. Action 状态机

```
IDLE ──提交──▶ QUEUED ──开始执行──▶ RUNNING ──┬──▶ SUCCEEDED
                                              ├──▶ FAILED      （动作自身抛错；记录后转 action.failed）
                                              ├──▶ CANCELLED   （stop / disconnect / shutdown）
                                              └──▶ TIMEOUT     （超时 + 动作自身 cleanup）
```

- 并发策略：**同一时间最多 1 个前台（exclusive）动作**；再提交 exclusive →
  拒绝并返回 `ACTION_BUSY`（不排队、不覆盖）。`chat` 是唯一非互斥通信动作，可与
  前台动作并存。
- 终态落定是幂等的（重复 finish 被忽略，绝不重复发事件）。
- `minecraft.disconnected` → 进行中动作全部 `CANCELLED`（reason=disconnect），
  **零僵尸动作**；进程收尾（SIGTERM）同样 cancelAll。
- 动作 JSON：`{action_id, action, status, started_at, finished_at, elapsed_ms}`。

## 4. API Schema

| 端点 | 请求 | 响应 |
|---|---|---|
| `POST /minecraft/look_at` | `{"x": 120, "y": 65, "z": -230}`（**禁止 yaw/pitch**） | `{ok, action_id, action:"look_at", status}`；status ∈ SUCCEEDED/TIMEOUT/CANCELLED |
| `POST /minecraft/stop` | —（幂等） | `{ok, status:"IDLE", cancelled:[action_id…]}` |
| `POST /minecraft/chat` | `{"message": …}`（Phase 1 契约不变） | `{ok, sent:true, action_id, action:"chat", status}`（新增字段向后兼容） |

错误码（HTTP）：`action.invalid` 400 / `action.not_online` 400 / `action.unknown` 404 /
`action.busy` 409 / `action.failed` 500。坐标校验在 runtime 与 Service 双侧同规则
（有限数字、世界边界 |x|,|z| ≤ 3e7、-512 ≤ y ≤ 2048），Service 侧校验先于任何网络往返。

CatooBot `/api/v1`（契约 §7.5）：`POST /minecraft/look_at`、`POST /minecraft/stop`；
`GET /minecraft` 的 `action` 块 = Current Action 视图（IDLE 表示从未有动作）。

## 5. Event Schema

```
minecraft.action.started / completed / failed / cancelled / timeout
{
  "event": "minecraft.action.completed",
  "session_id": "...", "timestamp": 0,
  "action_id": "act_…", "action": "look_at",
  "status": "SUCCEEDED", "started_at": …, "finished_at": …, "elapsed_ms": 12,
  "result": {"yaw": 45.0, "pitch": 89.1}      // 状态回报：完成瞬间的读数（look_at）
}
```

- `chat` 的 started/completed 附带 `message`；
- `failed` 只带错误 message（**绝不发内部异常堆栈**）；`cancelled`/`timeout` 带 `reason`；
- Python 侧：枚举扩展、事件校验、订阅者分发（与 Bridge 事件同一通道）、
  生命周期镜像（started → RUNNING，终态 → SUCCEEDED/FAILED/CANCELLED/TIMEOUT）。

## 6. Safety Gate

```
ActionRequest → validate(参数/世界边界) → enabled? → online?（action.not_online）
              → action allowed?（注册表；未知 → action.unknown）
              → concurrency check（前台忙 → action.busy）
              → execute（超时/取消令牌保护）→ 终态事件 + 日志
```

- 本阶段风险等级：`look_at = SAFE`、`chat = SAFE`、`stop = SAFE`（等级字段已登记在
  注册表条目上，为未来 SAFE/LOW/MEDIUM/HIGH/DESTRUCTIVE 分级预留；**未实现任何
  更高风险动作**）；
- Action 异常不影响 Node runtime 存活、不影响连接状态、不影响 WorldPerception
  （单测 + 服务层测试覆盖）；
- 日志：每个动作 started/completed/failed/cancelled/timeout 各一行
  （`[Minecraft Action] completed action=look_at id=… elapsed=12ms`），绝不记录
  密码/token/auth 信息。

## 7. 新增测试

**Node 单测**（`test/action_runtime.test.js`，44 checks）：completed + action_id/事件顺序/
日志、id 唯一、单前台互斥（action.busy 409 + 被拒不占位 + 原动作跑完）、非互斥并存、
校验失败不进入生命周期、离线错误码（含 chat 兼容）、stop 取消+清控制位+幂等+事件不重复、
超时 TIMEOUT + cleanup + 不坏状态、FAILED + 无堆栈、cancelAll(disconnect)、初始 IDLE。

**Node E2E**（`test/e2e.js`，逐轮 ×3）：join → spawn → **look_at**（SUCCEEDED +
同一 action_id 的 started/completed 事件 + 事件回报 pitch≈+90 + 无水平位移）→ chat
（含 chat 动作事件）→ **stop**（IDLE、幂等、无误报取消）→ 连接仍 ONLINE、
`action_active_count == 0`（无僵尸/无并发残留）→ 离开/被踢。

**Python**（`tests/test_minecraft_actions.py`，18 个）：`test_look_at_success`、
`test_look_at_invalid_coordinates`、`test_look_at_when_offline`、`test_look_at_timeout`、
`test_look_at_failed_translated`、`test_stop_is_idempotent`、`test_stop_cancels_running_action`、
`test_stop_when_idle`、`test_stop_when_runtime_down_still_succeeds`、
`test_second_exclusive_action_rejected`、`test_action_started_event`、
`test_action_completed_event`、`test_action_failed_event`、`test_action_cancelled_event`、
`test_action_timeout_event_mirrors`、`test_action_failure_does_not_break_connection_or_perception`、
`test_action_events_do_not_emit_world_events`、`test_chat_still_works_and_is_an_action`。

**WebUI**：4 个 vitest 用例（IDLE 初始态与按钮可用性、Look At Test 提交 (+5,0,+5) 测试
坐标、STOP 报告取消列表、RUNNING 态显示名称/ID/耗时）；Playwright 巡游页不受影响
（E2E 环境 minecraft disabled → 仍走引导卡分支）。

**Phase 1-3A 回归**：chat 契约不变（`sent:true`、错误码/校验顺序照旧）、
WorldPerception 全部用例不变；`look_at` 只改朝向 → 无位置变化 → 不产生
`WINDOW_SHIFT` / `world.changed`（§十九；动作事件也不会被当成世界事件分发）。

## 8. 全量测试结果（2026-10-05）

| 门禁 | 结果 |
|---|---|
| `npm test`（action_runtime 单测 + E2E） | **70/70** checks + ALL CHECKS PASSED（动作流 ×3 轮） |
| `pytest tests`（全量） | 见交付报告（含 18 个动作用例 + WebAPI 4 个 + 快照） |
| ruff check / ruff format --check / mypy app | 全绿 |
| vue-tsc / vitest / Playwright / vite build + dist 模块图 | 全绿（398 vitest） |
| GitHub Actions CI | 双 job 全绿（Minecraft 步骤已改跑 `npm test`） |

## 9. 已知限制

1. **flying-squid 会把朝向回写**：`lookAt` 生效后服务器随即发 forcedMove（position 包）
   把 yaw/pitch 重置——因此 E2E 断言动作事件里的 `result.pitch`（完成瞬间读数）而不是
   之后回读 snapshot。真实 Minecraft 服务器以客户端朝向为准，不存在此现象
   （已在 Phase 3A/3B 的 E2E 中以注释与文档标注）。
2. `elapsed_ms` 由 runtime 计算；WebUI 轮询 3s，RUNNING 中的耗时显示为最近一次上报值。
3. 非互斥动作目前只有 chat；多前台队列（排队而不是拒绝）未实现——本阶段按任务书
   §八选择「拒绝而不是排队」。
4. **`look_at`/`stop` 尚未接入 LLM Tools**（任务书 §十四：Phase 3B 是基础设施验证
   阶段，LLM 暴露属于 Phase 3C/Action Agent 阶段）；当前只能由 Service / WebUI / 测试调用。

## 10. Phase 3C readiness

- Action Runtime 已独立可用且被 chat 真实消费；未来任何动作只需：
  ① 在 `action_runtime.js` 注册表加一条目（exclusive/超时/validate/run/cleanup）；
  ② runtime 暴露对应 HTTP 端点；
  ③ 风险等级填 SAFE→DESTRUCTIVE 的对应值；④ 需要时在 Service/WebUI 加薄封装。
- 移动能力（Phase 3C/3B 之后）落地清单：`move_to`（exclusive、MEDIUM 前置校验
  目的地可达性）、`follow`（exclusive、可被 stop 打断）、`dig/place`（MEDIUM、
  需要 Action Safety Gate 的风险分级生效）、`attack`（HIGH，需额外确认门）。
- 感知侧已就绪：动作执行期间 WorldPerception 继续运行；移动引入窗口位移时由
  Phase 3A 的 movement-aware diff 吸收（rebase 阈值与日志已实测）。

## 11. Phase 3B.1 · Cancellation Cleanup Integrity（已完成）

**根因**：`cleanup()` 只在 TIMEOUT 分支执行；`CANCELLED`（stop / disconnect / shutdown）
不执行——未来 `move_to / follow` 的 Pathfinder Goal 会出现「ActionRuntime = CANCELLED，
而 Minecraft Bot 继续移动」。本节把 cleanup 语义修正为**所有强制终止一律清理**，
并固定契约与测试。**未新增任何 Minecraft 动作**（只改 `action_runtime.js` + 单测 + 本文档）。

### 11.1 修改前后行为

| 情况 | 修改前 | 修改后 |
|---|---|---|
| TIMEOUT | cleanup ✓ | cleanup ✓ |
| CANCELLED（stop） | **无 cleanup** | cleanup ✓ —— 且 `stop()` 里**同步**先 cleanup 再报 CANCELLED |
| CANCELLED（disconnect） | **无 cleanup** | cleanup ✓ |
| CANCELLED（shutdown） | **无 cleanup** | cleanup ✓ |
| SUCCEEDED / FAILED | 不强制 | 不强制（同前，除非动作自己定义特殊需求） |

### 11.2 实现要点

1. **`runCleanup(controller)`**：至多执行一次（`controller.cleaned` 守卫）；异常被吞掉并记
   `cleanup failed …` 日志——**不改终态、不崩 runtime**。终态只由幂等的 `finish()` 落定，
   因此终态事件永远只发一次。
2. **stop / cancelAll 顺序**：取消令牌 → **cleanup（真正终止底层状态）** →
   `finish(CANCELLED)`。§七 的两件套都保留：`action.cleanup()` 负责动作私有状态，
   `bot.clearControlStates()` 是 runtime 层全局移动兜底——cleanup 不是它的替代品。
3. **race 防线（§六）**：成功路径在 `await` 返回后检查 `token.cancelled`（或终态已落定），
   若成立则按取消处理（cleanup + CANCELLED/TIMEOUT）——任何交错下**绝不出现
   「记录 CANCELLED、响应 SUCCEEDED」的双终态**；正常微任务顺序下由 `token.onCancel` 先手。
4. **`runCancellable` 契约（§四，已写入模块头注释）**：它**只让 runtime 停止等待**该 promise，
   **不会取消底层操作**。未来所有有副作用的动作（move_to / follow / dig / place）的
   真实终止**必须由自己的 cleanup() 实现**——这是 Action Runtime 的固定契约。

### 11.3 新增测试（`test/action_runtime.test.js`：44 → 70 checks）

| 测试 | 覆盖 |
|---|---|
| stop 取消 → cleanup | 同步执行、终态 CANCELLED、cleanup 不重复 |
| disconnect 取消 → cleanup | `cancelAll("disconnect")` |
| shutdown 取消 → cleanup | `cancelAll("shutdown")` |
| cleanup 抛错 | 不崩 runtime（后续动作可执行）、终态仍 CANCELLED、终态事件一次、有日志 |
| cleanup 恰好一次 | stop → cancelAll×2 → 底层完成竞态：cleanup==1、终态事件==1、无双终态 |
| race（§六） | 底层 promise 恰在 stop 时完成 → 只能 CANCELLED、cleanup==1、无完成事件 |
| （强化）TIMEOUT | 断言 TIMEOUT 也执行动作 cleanup |
