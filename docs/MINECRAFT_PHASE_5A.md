# Minecraft Phase 5A — 多步骤任务运行时（Task Runtime）

> 目标：让罐头能接一件"要做好几步、中间还要等世界给回音"的事 —— 期间可暂停、可恢复、
> 可取消、可重规划。**本阶段不新增任何 Minecraft 原子工具**（19 个保持原样），也不把
> 已有工具包装成 `minecraft_gather_resource` 这类黑盒：增长的是一条**通用的编排层**，
> 第一期只把它接到 Minecraft 工具循环上。

## 一、它是什么，不是什么

**是**：一个工作流编排器。它负责状态、步骤调度、等待、恢复、取消、checkpoint、
授权校验、失败分类、最终校验 —— 只认识「工具 + 参数 + 风险 + action_id + 结果」。

**不是**：Minecraft 的事务系统。逐条写明（§一百零二）：

* **不提供通用回滚**：`rollback_supported = false` 是硬事实，接口与 UI 都如实暴露。
* **不承诺世界状态原子**：每一步各自成立，世界在两步之间可能被玩家/怪物改动。
* **失败后只有四条路**：停下（PAUSED）、重规划（REPLANNING）、用户重确认、取消。
  没有"补偿事务"，也没有"假装已恢复"。

调用链严格保持：

```
LLM → TaskRuntime → Agent Bridge / Tool Loop → Policy → Confirmation
    → MinecraftService → ActionRuntime → Mineflayer
```

TaskRuntime **绝不**直接调 Mineflayer，也**绝不**直接发 runtime HTTP。

## 二、状态机

任务状态（显式枚举，绝不出现 `None`/`"running"`/`"done"`）：

```
PLANNING → PENDING_CONFIRMATION → RUNNING ⇄ WAITING_ACTION
                 ↓                  ↓
              PAUSED ←──────────────┘
                 ↓
             REPLANNING（新计划 → 新确认）
终态：SUCCEEDED / FAILED / CANCELLED / EXPIRED
```

步骤状态：`PENDING / RUNNING / WAITING_ACTION / WAITING_CONFIRMATION / SUCCEEDED / FAILED /
SKIPPED / CANCELLED`。

非法转移会被 `ALLOWED_TASK_TRANSITIONS` 直接拒绝（抛 `task.invalid_transition`），
而不是"看起来对就放行"。

## 三、计划与执行分离

* **Plan（冻结）**：`objective` + 有序步骤（`tool` / `arguments` / `risk` / `depends_on`）+
  `expected_final_state`。`plan_hash = sha256(canonical_json(plan))[:20]`（只看真正决定
  世界操作的东西 —— 不含 state/时间戳/结果）。
* **Execution（可变）**：每一步的 `state`、`status`、`action_id`、`result`、`attempts`、
  `started_at/finished_at`，以及审计用的 `resolved_arguments`（引用解析后的最终参数）。
* 用户确认之后计划**不可静默修改**：改动任一步的参数/工具/顺序 → `plan_hash` 对不上 →
  当前步骤 `AUTHORIZATION` 失败，需要重新确认。

### 两阶段计划（§六十八）

用户确认的必须是"知道目标在哪之后、接下来具体做什么"，而不是"某个未知目标自动选择"：

1. **Phase A（只允许 SAFE 观察）**：`minecraft_world` / `inventory` / `find_blocks` /
   `dig_capability` —— 把"去哪、挖什么、手上有没有工具"先查清楚；
2. **Phase B（动作计划）**：写成**解析后的坐标**（`{"x": 332, "y": 71, "z": 220}`），
   然后请用户确认。

`app/tasks/planner.py` 的 `plan_resource_task` 是确定性模板（真机 demo / 测试用）；
`ModelTaskPlanner` 让主模型出**结构化 JSON**（绝不解析自由文本），两者都由
`app/tasks/validation.py` 做确定性校验后才可能进入确认。

计划期的取舍规则（写死在代码里，可测试）：

* 候选目标只挑**和罐头差不多高**的那一截树干（`y ≤ bot_y + 1`）—— 头顶的树冠要去爬树
  才够得到，写进计划只会变成一次必然失败的移动；
* `dig_capability.can_dig = true`（运行时自己说"现在挖得动"）→ **不写 move_to**；
  `reason=too_far` 才写；空气/挖不动/读不到 → 如实失败，不生成计划。

## 四、授权（TaskAuthorization / TaskStepAuthorization）

* 用户确认的是**一整份冻结计划**（一条一次性确认条目，绑 `session + user + task_id +
  plan_hash + 计划内容指纹`，TTL 复用 `minecraft.agent.confirmation.ttl_seconds`）。
* 确认时签出 `TaskAuthorization`（`task_id/user_id/session_id/plan_hash/approved_at/expires_at`）。
* 每个步骤执行前比对 `TaskStepAuthorization`：`step_id + tool + 模板参数指纹 + plan_hash`。
* 世界动作（LOW/MEDIUM）还要求：授权有效期内 + 计划未被改过（`authorization.plan_hash ==
  现算 plan_hash`，用存的那一份比，而不是自比自）。

### TASK 不是 USER

新增 `TurnOrigin.TASK`，它**不是**用户回合（`is_user == False`）：

* Policy 里 `origin == "task"` 走单独一条分支：需要 `facts.task_authorized`（
  `minecraft.task_authorization_missing` 否则拒绝），**不再**要求 USER 回合与可信玩家；
* 确认门对 TASK 步骤不重复弹确认（整份计划已经确认过），但**参数改了就重来**；
* 授权事实不能从 `ToolContext.metadata` 里读（那谁都能伪造）：bridge 在
  `invoke_task_step` 里先问 `TaskRuntime.authorize_step`，通过后**自己**铸一枚一次性凭据
  （`secrets.token_urlsafe`，30s 内有效），只有 bridge 自己能解开 —— 手工拼一个 ToolContext
  声称"我已授权"会被拒。

## 五、等待与恢复

* 持续型动作（move_to / dig / equip / pickup_item / follow_player）启动即返回 `RUNNING`，
  step 进入 `WAITING_ACTION` 并记下 `action_id`；
* 动作终态事件（`minecraft.action.*`）由 `MinecraftTaskCoordinator` 转发给 TaskRuntime，
  且**严格按 action_id 绑定当前步骤**：别的任务/别的动作的事件不会把它唤醒；
* 每个状态变化都落 checkpoint（复用同一个 SQLite：`agent_task_runs` 当前快照 +
  `agent_task_checkpoints` append-only 转移日志，迁移 27）；
* **跨进程重启只保证"读得回任务本身"**，正在跑的 Mineflayer action 一定已经失效：
  恢复时会把它标成 `RUNTIME_RESTART` 并要求重规划，绝不假装动作还在。

## 六、暂停 / 恢复 / 取消 / 过期

| 操作 | 语义 |
| --- | --- |
| pause | 在**安全边界**暂停：没有前台动作立刻 `PAUSED`；有动作则记 `pause_requested`，等动作**自然结束**（绝不在 `bot.dig()` 中间硬切状态） |
| resume | 重新校验授权时效 + 世界事实（在线/维度/坐标）；旧动作已失效 → `REPLANNING`；授权过期 → 回到 `PENDING_CONFIRMATION` 等用户再说一次 |
| cancel | 立刻终态 `CANCELLED`，并**经现有 `minecraft_stop`** 停掉前台动作；迟到的 `completed` 不能翻案，不留下前台动作 |
| expired | 超过 `task.ttl_seconds` → `EXPIRED`，当前动作先 stop，记录过期原因 |

WebUI 的「继续」不是用户回合：它只能把**仍在有效期内**的授权接着用完，授权过期时如实拒绝
（`task.resume_requires_user`）—— 控制台永远不能替用户授权。

## 七、失败分类与重试

稳定分类（不是所有错误都叫 FAILED）：`AUTHORIZATION / VALIDATION / TARGET_LOST /
ACTION_FAILED / TIMEOUT / CANCELLED / WORLD_CHANGED / OFFLINE / BUSY / INTERNAL`。

* **不隐式重试世界动作**：SAFE 查询最多重试 2 次（每次记 attempt 与原因）；
  LOW/MEDIUM 一律 0 次 —— 用户以为只挖一次，runtime 绝不能偷偷再挖一次；
* LOW/MEDIUM 失败 → 暂停等用户（`PAUSED`），要做新的世界动作必须**新计划 + 新确认**；
* `no_progress_limit`：连续 N 次"同一个工具 + 同一份参数 + 状态没变" → 暂停（`STALLED`）。

## 八、完成与最终校验

**最后一步 action 完成 ≠ 整个任务完成**。计划必须写 `expected_final_state`
（例如 `{"inventory_delta": {"oak_log": 1}}`），最后一步之后再**重新读一次背包**
（`minecraft_inventory`，SAFE），比对 `after - before` 才判定 `SUCCEEDED`；
对不上就是 `FAILED`（`VERIFICATION`），绝不从历史 action 结果宣布成功。

## 九、对话入口

用户回合由 `app/tasks/turn.py` 翻译成任务操作（规则写在代码里，不叫模型猜）：

| 用户说 | 做什么 |
| --- | --- |
| 「去砍一棵橡树，挖一块原木并捡回来」 | SAFE 观察 → 生成冻结计划 → 在游戏里列出来请他确认 |
| 「确认」 | `confirm_and_start(...)`（必须是真实用户回合 + 发起人 + 会话都对得上） |
| 「先停一下」/「继续」/「停止这个任务」 | pause / resume / cancel |
| 「你在哪？」「背包里有什么？」「附近有铁矿吗」 | **不创建任务**，照常走普通对话/SAFE 工具 |

一个会话同时只有一个前台任务（再提新请求会如实告诉用户"手上还有一件没做完"）。

## 十、API 与 WebUI

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/minecraft/task` | 当前活动任务的只读投影（没有则 `task: null`；读端点恒 200） |
| GET | `/api/v1/minecraft/task/{task_id}` | 单个任务详情 |
| POST | `/api/v1/minecraft/task/{task_id}/pause` | 暂停 |
| POST | `/api/v1/minecraft/task/{task_id}/resume` | 继续（只沿用仍有效的授权） |
| POST | `/api/v1/minecraft/task/{task_id}/cancel` | 取消（经 minecraft_stop 真停） |

**没有 confirm 端点** —— 计划确认只能来自用户自己的回合。
Minecraft 页新增 Multi-Step Task 面板：Objective / State / Progress / Current Step /
Current Action / Confirmation / Plan（逐步一句人话 + 风险 + 状态）/ Last Result /
Failure / Rollback；按钮只有 PAUSE / RESUME / CANCEL。面板里**没有 raw Mineflayer 状态**。

## 十一、配置（只加四个旋钮，默认保守）

```yaml
task:
  ttl_seconds: 600        # 30~3600：一个任务最多活多久
  max_steps: 16           # 1~64：计划里最多几步
  max_replans: 2          # 0~5：最多重规划几次
  no_progress_limit: 3    # 1~10：连续几次没进展就暂停
```

其余上限是代码常量（例如动作步上限 8、SAFE 重试 2），不开放配置。

## 十二、事件

任务事件走既有总线（payload 至少 `task_id/step_id/state/timestamp`，绝不塞 raw 世界状态）：
`task.created / plan_ready / confirmation_required / started / step_started / step_waiting /
step_succeeded / step_failed / paused / replanning / resumed / cancelled / succeeded /
failed / expired`。

底层 `minecraft.action.*` 事件照旧保留 —— 任务事件是**加一层编排语义**，不是替代。

## 十三、自动化验证

| 层 | 内容 |
| --- | --- |
| `tests/test_task_runtime.py` | 32 个用例 A–X：创建/确认/拒绝非用户回合、步骤授权、引用解析、异步等待与恢复、暂停/恢复/取消/过期、重试策略、no-progress、最终校验 |
| `tests/test_task_authorization.py` | 12 个用例：§八十七 六条（Plan A 授权不能执行 Plan B、参数变更、plan_hash 变更、INITIATIVE/BACKGROUND/SYSTEM 不能消费授权）+ TASK≠USER + bridge fail-closed |
| `tests/test_task_turn.py` | 18 个用例：意图识别（普通对话不建任务）、创建→确认→暂停→继续→取消、别的会话不能停我的任务、错误如实上报 |
| `tests/test_minecraft_task_integration.py` | 7 个用例：**确定性 E2E**（Service 边界替身）—— 协调器事件名回归、find→move→dig→pickup→inventory 全链路 SUCCEEDED + 最终背包校验、MEDIUM 失败→暂停不重试、策略拒绝 MEDIUM 时任务也停下、计划被改过必须拒绝 |
| `tests/test_web_api_minecraft_task.py` | 7 个用例：只读投影、暂停/继续/取消、没有 confirm、未装配 503 |
| `webui/src/pages/__tests__/minecraft.spec.ts` | 4 个新增用例：空态、详情展示、PAUSE 调用与状态回显、无任务时按钮不渲染 |

## 十四、真实服务器验证（`scripts/task_smoke_real.py`）

不是 smoke 顺序调 HTTP 假装任务：连的是真 Java 服务器 + 真 Node runtime，由**真实
TaskRuntime** 驱动每一步。

1. **正向**：SAFE 观察（16 格找橡木，没有就放大到 32）→ 5 步冻结计划 → SYSTEM 回合确认被拒
   （`task.confirmation_not_user_turn`）→ 用户确认 → `move_to → dig → dropped_items →
   pickup_item → inventory` 全部 SUCCEEDED → `TASK SUCCEEDED` + `FINAL INVENTORY
   VERIFIED {'oak_log': 1}`；
2. **pause / resume**：在 WAITING_ACTION（安全边界）暂停 → 真正 PAUSED → 继续 → 跑完 SUCCEEDED；
3. **cancel**：任务正在 `move_to` 时用户取消 → `CANCELLED` → 经 `minecraft_stop` 真停 →
   `goal == null`、`isMoving == false`、没有泄漏的前台动作。

跑法：`node minecraft_runtime/runtime.js`（或让 Bot 托管）+ 一个 Java 服务器，
然后 `.venv/Scripts/python.exe scripts/task_smoke_real.py`。
脚本会**显式打开** `minecraft.agent.tools.allow_medium`（只改本进程的配置对象，不写回
`config.yaml` —— 生产默认仍然是 false）。

## 十五、已知限制

* 跨进程重启不恢复正在进行的动作（只能读回任务本身，动作一定按失效处理）；
* 没有通用回滚 / 补偿事务；
* 任务入口第一期接在**游戏内聊天**（`MinecraftChatBridge`）；QQ 侧仍走单工具路径；
* 规划模板 `plan_resource_task` 覆盖"资源采集"这一族；其它目标由 `ModelTaskPlanner`
  产出结构化计划，同样要过确定性校验与用户确认。
