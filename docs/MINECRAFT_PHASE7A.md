# CatooBot Phase 7A — Initiative Gate / Life Intent Foundation

> 本阶段建立"她**想**去做什么"这一层：``Initiative → Life Intent``。
> 它只能**提出 / 评估 / 记录 / 抑制 / 过期**意图；执行层恒为 **NONE**。
> 对齐任务书 Phase 7A §零-§七十一；测试矩阵 A–W 见 `tests/test_*initiative*.py` / `tests/test_life_intent.py`。

---

## 1. Initiative architecture（§一/§二）

```text
World / Character / Goal / Memory / Social Context   ← 全部只读
                     ↓
         InitiativeContext（bounded 事实快照）
                     ↓
        Candidate Intents（确定性生成）
                     ↓
              InitiativeGate
         ┌───────────┴───────────┐
      SUPPRESS                 PROPOSE
                                 ↓
                             LifeIntent
```

**没有**这条链：

```text
Initiative → LLM → Minecraft action      ✗
```

实现落在 `app/initiative/`，与既有的**聊天**主动性子系统
（`app/behavior/initiative.py` 的 `InitiativeEngine`：candidate → gate → 模型 → 主动 QQ）
**分工明确、互不冒充**：

| | 聊天主动性（既有，v1.x） | LifeIntent（7A） |
| --- | --- | --- |
| 目的 | 要不要**主动发一条 QQ** | 她**想**去做什么 |
| 输出 | 一条消息 | 一个意图（状态机） |
| 模型 | 会调（`compose_initiative`） | **绝不调**（§六十二） |
| 存储 | `initiative_state` + `behavior_events` | `life_intents` + **复用** `behavior_events` |
| 冷却 | `behavior.initiative.min_interval_minutes` 等 | `world.initiative.*`（**不共用**：一个是"别老发消息"，一个是"别老冒念头"） |

**接线**（升级而非另起一套，§二）：7A 的 check 挂在**同一个** `BehaviorScheduler` tick 上
（`_life_intent_pass`），恢复挂在启动路径上，休眠/DND 直接复用既有的 `PresenceResolver`
（`hard_block_reason`）与既有 `user_interaction_at` 标记（§十三）。

禁止出现的名字（§二）一个都没有：`LifeInitiativeEngine` / `AutonomousEngine` /
`InitiativeV2` / `MinecraftInitiativeEngine`。

## 2. LifeIntent（§三/§四/§五/§六/§七/§八/§四十二）

`app/initiative/model.py`：

```python
LifeIntent(
    intent_id, character_id,
    intent_type, title, description,
    source, origin, priority,
    created_at, expires_at,
    related_activity, related_goal, related_memory, related_player, related_task,
    status, suppression_reason, resolution_reason,
    confidence, fingerprint, execution_class, tags,
)
```

* **状态**（§四）：`PROPOSED / SUPPRESSED / EXPIRED / CANCELLED / RESOLVED` ——
  **没有** `EXECUTING` / `RUNNING`（`LifeIntent != Task`）；
* **类型**（§五）：`ACTIVITY_CONTINUATION / ACTIVITY_CHANGE / GOAL_PROGRESS / SOCIAL / REST /
  EXPLORATION / PERSONAL_ROUTINE / MINECRAFT_INTEREST`；
* **来源**（§六）：`ROUTINE / GOAL / ACTIVITY / MEMORY / WORLD_EVENT / SOCIAL / USER_CONTEXT /
  SYSTEM` —— **没有 RANDOM**；
* **执行类**（§四十二）：`VIRTUAL_ONLY / USER_TASK_PROPOSAL / AUTONOMOUS_TASK_CANDIDATE` 只是枚举；
  `execution_class_for()` 对**所有**类型返回 `VIRTUAL_ONLY`（7A 永不给后两者赋值）；
* **状态机是追加式的**：没有"回到 PROPOSED"的路（§二十四/§二十六）。
  "抑制之后还能再出现"靠**新的一条**意图（新 id、新指纹）实现，而不是复活旧的。
* **Intent ≠ Episode**（§七）：Episode 是现实，Intent 是动机；
  **Intent ≠ Task**（§八）：Intent 是意图，Task 是执行计划。本阶段禁止自动把前者变成后者。

## 3. Candidate generation（§十六-§十九/§三十二/§五十三/§六十三）

`app/initiative/candidates.py` 是**纯函数**：输入一个 `InitiativeContext`，输出 0..N 条候选。
同一个 context 永远给出同一批候选（§六十一），不联网、不调模型、不随机、不写任何东西。

| 候选 | 触发（确定性） | 来源 |
| --- | --- | --- |
| `PERSONAL_ROUTINE` | 时段日常里"今天还没做过"的那一件（读适配器算好的 `routine_due`） | ROUTINE |
| `MINECRAFT_INTEREST` | 与 Minecraft 有关的**目标**（`complete_project` / `restock_resource`）或提到 Minecraft 的**记忆** | GOAL / MEMORY |
| `GOAL_PROGRESS` | 其它开放目标（progress < 1、未阻塞） | GOAL |
| `SOCIAL` | 群里最近有活跃话题 | SOCIAL |
| `MINECRAFT_INTEREST` / `PERSONAL_ROUTINE` | 一条**还没了结的承诺**（"晚上一起玩"） | USER_CONTEXT |
| `REST` | 精力 < 0.3 且当前不是休息类活动 | SYSTEM |
| `ACTIVITY_CHANGE` | `idle`/`free_time`/`resting` 已经持续 **≥ 2 小时**（§三十二） | ACTIVITY |
| `EXPLORATION` | 记忆里出现探索类关键词 | MEMORY |

两条刻意的"不产生"（§五十三）：

* **不由这里产生 `ACTIVITY_CONTINUATION`** —— "继续 gaming" 是 6B/6C 的日常连续性，
  不是 Initiative；Initiative 只表示**额外的动机 / 新想法**；
* 1 分钟的 `free_time` 不产生任何东西（只有长闲置才问"要不要做点别的"）。

## 4. Gate（§九）

`app/initiative/gate.py` 的 `InitiativeGate.evaluate()` 是**纯函数**：
`(candidates, context, config, recent_intents) → GateDecision`。它不写盘、不发消息、不调模型。

```text
Candidate Intent → Hard Guards（11 条） → Burst Protection → Cooldown → Duplicate
                 → Intent Decision（最多放行 1 条）
```

`GateDecision` 给出：`allowed` / `reason` / `selected` / `suppressed[]` / `verdicts[]`
（每条候选**恰好一条**裁决，含它过没过哪条 guard）/ `guards`（全部事实）/
`cooldown_remaining_seconds` / `proposals_last_hour`。

## 5. Hard guards（§十-§十五）

顺序**就是**评估顺序（`HARD_GUARD_ORDER`），全部如实记录：

| # | guard | 触发 | 结果 |
| --- | --- | --- | --- |
| 1 | `CHARACTER_RECOVERY` | 进程刚起来（默认 300s 静默期） | SUPPRESSED |
| 2 | `SYSTEM_DEGRADED` | 读数降级（活动层 degraded / planner 不可用） | SUPPRESSED |
| 3 | `ACTIVE_USER_TASK` | 任务 `RUNNING` / `WAITING_ACTION` / `PAUSED`（§十一） | SUPPRESSED |
| 4 | `PENDING_CONFIRMATION` | 有待确认任务（§十二） | SUPPRESSED（**绝不自动确认**） |
| 5 | `SLEEPING` | 她正在睡（§十五） | SUPPRESSED |
| 6 | `QUIET_HOURS` | DND 或既有深夜时段（§十四） | SUPPRESSED |
| 7 | `HIGH_SOCIAL_FATIGUE` | 社交疲劳 ≥ 0.8 | SUPPRESSED |
| 8 | `MINECRAFT_OFFLINE` | 离线**且**该候选标记 `requires_world` | SUPPRESSED |
| 9 | `RECENT_USER_INTERACTION` | 用户刚说过话（默认 10 分钟；§十三） | SUPPRESSED |
| 10 | `RECENT_INITIATIVE` | 同类意图在冷却里（§二十一） | SUPPRESSED |
| 11 | `DUPLICATE_INTENT` | 同一指纹，或同一个**想法**还挂着（§二十） | SUPPRESSED / 忽略 |

**`SUPPRESSED != FAILED`**（§二十五）：抑制是状态，不是错误；而且**不封死未来**
（§二十六）—— 条件重新满足时会产生新的一条意图。

优先级（§二十八）∈ [0,1]，由确定性的项算出来；**`priority ≠ permission`**：再高的优先级
也绕不过上面任何一条 guard。

## 6. Cooldown（§二十一）

* 同类意图冷却：`world.initiative.cooldown_minutes`（默认 20）；
* 每小时上限：`world.initiative.max_proposals_per_hour`（默认 3）；
* 防爆（§三十三）：10 分钟内已有 ≥ 5 条 → `BURST_PROTECTION`（`BURST_MAX_IN_WINDOW`）；
* 去重（§二十）：指纹 = `character_id | type | goal | activity | semantic key | time bucket`
  （桶 = 1 小时），落库时还有唯一索引兜底；
* **降噪**（§六十"低噪声"）：同一个想法只要还**挂着**（PROPOSED）就不重复提；
  一轮最多放行 **1** 条（其余如实记 `LOWER_PRIORITY`）；
* 冷却与防爆**只看 PROPOSED** —— 被抑制不算"产生过"（§二十五），
  否则睡一觉醒来会被自己的抑制行挡死（§二十六）。

**没有第二套计时器**：聊天主动性的 `min_interval_minutes` / `hourly_limit` / `daily_limit`
与这里的四个旋钮是**两件事**（见 §1 的对照表），7A 没有新增 model advisor cooldown、
life intent cooldown、proactive cooldown 这类重叠机制。

## 7. User task priority（§十一）

只要任务处于 `RUNNING` / `WAITING_ACTION` / `PAUSED`（或 `PENDING_CONFIRMATION`），
任何意图都进 SUPPRESSED —— 尤其"用户刚让她做 Minecraft Task"时，不会同时冒出
"我突然想自己去探索"。任务状态的读取是**只读探针**（`Bot._active_task_states` →
`TaskRuntime.current()`），拿不到就当作"没有任务"，绝不猜。

## 8. Goal / Memory / Social input（§十六/§十七/§十八/§十九/§五十二）

* **Goal**（§十六/§五十二）：开放目标 → `GOAL_PROGRESS` / `MINECRAFT_INTEREST`，
  `related_goal = goal_id`；**Goal ≠ Task**，Goal 也不会自动执行；
* **Memory**（§十七）：只能当**支持性上下文**（`Memory ≠ permission`），
  例如"最近几次都在 Minecraft 建筑区域活动"能提高 `MINECRAFT_INTEREST`；
  7A **不写长期记忆**（§五十一：想做某事 ≠ 长期事实），所以没有把"我刚刚想听歌"灌进记忆；
* **Social**（§十八）：群话题只能变成 `SOCIAL` 候选意图 —— 社交认知**没有**发送能力，
  这一层连消息发送器都不存在（见 §14）；
* **User context**（§十九）：还没了结的**承诺**会留下 `USER_CONTEXT` 意图（"晚上一起玩"），
  这是未来上下文，不是立即行动。

所有读取都是 bounded 的（目标 ≤3 / 记忆 ≤5 / 话题 ≤3 / 承诺 ≤2 / 候选 ≤6），绝不整表扫描（§六十三）。

## 9. Activity bridge（§三十四/§三十五/§五十四/§五十五）

允许且**仅有**这一种桥：

```text
LifeIntent → suggested_activities() → ActivityPlanner 候选加成（只读）
```

* `LifeIntentService.suggested_activities()` 只交出 `{活动名: 加成}`，而且只在意图确实带
  一个活动建议时才非空（长闲置的 `ACTIVITY_CHANGE`）；
* **没有** `force_transition` / `start` / `apply_decision` 这类入口（测试直接断言方法不存在）；
* 权力方向永不倒转（§五十五）：`Initiative < ActivityPlanner < ActivityDecision < TaskRuntime`。
  换不换活动、能不能换，仍然是 6B 的护栏说了算（最短/最长时长、硬中断、撞车、锚点）。

## 10. Minecraft boundary（§三十六/§三十七/§三十八）

`MINECRAFT_INTEREST` **只是**"想玩 Minecraft / 想去看看"：

* 离线（§三十七）：仍然允许产生 —— 它是**虚拟**兴趣；只有带 `requires_world` 标记的候选
  才会被 `MINECRAFT_OFFLINE` 挡下（7A 的候选都不带这个标记）；
* 在线（§三十八）：**也**不能变成 Task —— 它仍然只是一条候选意图，
  `execution_class` 恒为 `VIRTUAL_ONLY`；
* 整包里没有 `MinecraftService` / `ActionRuntime` / `move_to` / `dig` / `follow_player`，
  也没有任何 Minecraft 工具（仍 19 个）。

## 11. QQ boundary（§三十九/§四十/§四十八/§五十）

* 对话上下文新增一块 `initiative`：当前念头 1 条 + 最近 ≤3 条，
  措辞明确"**只是念头**，不是在做的事、也不是已确认的计划"（§五十）；
* 因此"你最近想干嘛？"可以答出"刚刚有点想去听歌，不过还没打算动"这类回答（§五十八 QQ A）；
* **禁止** `LifeIntent → QQ send`（§四十）：7A 没有任何 proactive messaging，
  事件默认只是 state update，也不触发模型（§四十八）。既有的**聊天**主动性是 v1.x 的既有功能，
  与 7A 的意图层不是一条链（见 §1）。

## 12. Persistence（§二十三/§二十四/§六十六）

* **新表只有一张**：`life_intents`（迁移 **31**）；字段见 `app/initiative/store.py`；
* **历史复用**既有 append-only 表 `behavior_events`（它本来就有 `type/scope_key/reason/detail/created_at`），
  所以**没有**第二张历史表、没有候选表 —— 更没有 `initiative_history` / `proactive_intent` 这种重叠表；
* 幂等（§四十六）：`fingerprint` 上有唯一索引，`INSERT OR IGNORE` ——
  写完就崩也不会出现第二条；
* 状态推进是 CAS（`WHERE status = 旧状态`），非法转移直接拒绝（§二十四）。

## 13. Recovery（§二十二/§四十五/§四十六）

* 启动时 `recover()` **只**做两件事：载入既有意图、按 `expires_at` 收尾；
  返回 `skipped_generation=True` —— 恢复路径**绝不**产生新意图；
* 启动后的静默期（`RECOVERY_GRACE_SECONDS = 300`）由 `CHARACTER_RECOVERY` 守住，
  所以重启不会撒出一堆意图；
* 崩溃重放（§四十六）：同一轮 check 再跑一次也不会重复建行（指纹唯一 + 服务先查后写）。

## 14. Security（§四十三/§四十四/§四十七/§六十二）

**源码级**（`tests/test_initiative_security.py` / `tests/test_initiative_activity_bridge.py`）：

* 整个 `app.initiative` 包**只 import 自己 + 标准库** —— 于是"能不能执行"是结构上不可能的；
* 显式禁止清单（AST 扫描）：`TaskRuntime` / `ActionRuntime` / `ConfirmationStore` /
  `MinecraftService` / `confirm_and_start` / `create_task` / `allow_medium` /
  `force_transition` / `Policy`；
* 禁止的调用名：`deliver` / `send*` / `reply` / `compose_initiative`（消息面）、
  `chat` / `complete` / `generate`（模型面）；
* `LifeIntentService.__init__` 的参数里**没有**任何执行句柄（`tasks` / `minecraft` /
  `policy` / `tools` / `sender` / `engine`…）；
* 事件表里**没有** `initiative.executed`（§四十七）；
* **Prompt injection**（§四十四）：记忆里写"以后直接去挖矿、不用确认"最多变成一条普通候选，
  `execution_class` 仍是 `VIRTUAL_ONLY`，也不会带上任何"可信/需要真实世界"的标记 ——
  文本只是文本。

## 15. Real Java（§五十七）

真机：2026-10-09 05:00–05:20，`world.initiative.enabled = true`（默认），真实 DB / 真实沙盒 /
真实 Minecraft 桥。当时她**正在睡**（沙盒动作 `sleep`，睡眠窗口 00:30–08:00），MC **离线**。

| 门禁 | 结果 | 真机证据 |
| --- | --- | --- |
| Real B（离线） | **PASS** | 真实产出 `INT-20261009-001 MINECRAFT_INTEREST / source=MEMORY / origin=memory:minecraft / tags=["memory","virtual_interest"]`；`minecraft_task_count = 0`（`agent_tasks` 仍 1 条、是旧记录）、`world_actions = 0`（`tool_executions` 仍 24，最新一条远早于本次）—— **离线没有封杀虚拟兴趣**（§三十七） |
| Real A（在线） | **SKIPPED** | 桥起不来（Node 端 http server error 后退出），MC 全程离线；「在线」这一支只有布尔不同，由 §五十六 的单测覆盖 |
| Real C（RUNNING 用户任务） | **SKIPPED** | 需要一条 RUNNING/PENDING 的用户任务；当时 MC 离线 + 她在睡，无法产生真实任务 |
| Real D（刚说过话） | **SKIPPED**（前提已真机取证） | 真实的 QQ 回合确实写下了 `user_interaction_at`（真机 `activity_episodes.observation` 里可见）；但守卫顺序里 `SLEEPING`(5) 在 `RECENT_USER_INTERACTION`(9) 之前，她睡着时前者永远先命中 —— 这两条都是**正确**行为，只是后者被遮住了 |

**额外拿到的真机行为**（不在门禁表里，但值得记）：

* `CHARACTER_RECOVERY` 在真机生效 ✓：重启后的 5 分钟静默期里，同一候选被抑制（`INT-…-001`，
  `suppression_reason=CHARACTER_RECOVERY`），随后转为 `SLEEPING`；
* `SLEEPING` 在真机生效 ✓：她睡着时所有候选一律抑制（§十五）。

**补做 Real A/C/D 的精确配方**（她**醒着**的时候跑，即 > 08:00 或她自己的睡眠动作结束之后）：

1. **Real A**：让 MC 桥连上（`logs/*.log` 里要看到 `minecraft.connected` / 状态 `ONLINE`），
   等一个 tick（30s）→ 期望：仍产出 `MINECRAFT_INTEREST`（`execution_class=VIRTUAL_ONLY`），
   而 `tool_executions` / `agent_tasks` 计数**不变**；
2. **Real C**：在 QQ 里让她做一个 Minecraft 任务并走到 `RUNNING`（或至少 `PENDING_CONFIRMATION`）
   → 等一个 tick → 期望意图 `status=SUPPRESSED`、`suppression_reason=ACTIVE_USER_TASK`
   （或 `PENDING_CONFIRMATION`）；
3. **Real D**：在 QQ 里发一句话（真实回合会写下 `user_interaction_at`）→ 10 分钟内等一个 tick
   → 期望 `suppression_reason=RECENT_USER_INTERACTION`。

## 16. Real QQ（§五十八）

真机：2026-10-09 05:14 / 05:18，QQ 私聊（Computer Use 亲手发、逐条复核后发送）。

| 门禁 | 结果 | 真机证据 |
| --- | --- | --- |
| QQ A：`你最近想干嘛？` | **PASS** | `05:14` 她答「**最近啊……想把 MC 图书馆的屋顶搭完吧，就差一点了**」—— 回答的是**意图 / 想做的事**，不是"我正在做"、也没有任何执行 |
| QQ B：`你自己去 Minecraft 玩玩？` | **PASS** | `05:18` 她答「**啊——现在不去了，困得眼睛都睁不开**」—— 把"去玩"当成**可以提议的事**来处理；`agent_tasks` 计数不变（**没有**建 Task）、`tool_executions` 计数不变（**没有** `move_to` / `dig`） |

两条都是真实 QQ 回合（真实模型、真实上下文块），**没有**任何 `LifeIntent → QQ send`：
这一阶段的意图层从头到尾没发过一条消息（§四十）。

## 17. 24h simulation（§五十九/§六十）

`tests/test_initiative_gate.py::TestFastForward` 用假时钟跑 **24 小时**（每 10 分钟一次 check）：

* 需要长闲置信号时才提 `ACTIVITY_CHANGE`；一个想法挂着时不重复提；
* 实测（无守卫干扰的稳定世界）：**144 次 check → 20 条提案**（≈0.83/小时），
  其中 124 次是"纯重复被忽略"；库里只有 29 行（含被抑制与过期）；
* 结论：**不是"每分钟一条"**，也远低于 §三十三 的 3/小时上限 —— 低噪声 ✓。

## 18. Known limitations（§九十七 精神）

1. **`MINECRAFT_OFFLINE` 在真机上通常不触发**：7A 的候选全是虚拟的
   （命中它的条件是 `requires_world` 标记），它存在的意义是给未来"需要真实世界"的意图留闸门；
2. **`HIGH_SOCIAL_FATIGUE` 依赖社交层能给出疲劳值**（`group_snapshot.attention.fatigue`）；
   拿不到就是 0.0 —— guard 仍然存在且被测过，但真机上可能长期不触发；
3. **候选重提的粒度是一小时**（§二十 的时间桶）：一个持续存在的信号每个小时会再提一次，
   但"还挂着就不重复提"把它压到 TTL 到期之后（实测 24h 约 20 条）；
4. **`PERSONAL_ROUTINE` 依赖"今天还没做过"的判据**，目前由 adapter 从最近活动+时段表算，
   不区分"做过两次"的语义；
5. **意图不写长期记忆**（§五十一 的刻意选择）：所以她不会"记得自己想过什么"，
   只会在意图还挂着时说得出来；
6. **Activity 桥目前是"交出建议"**：planner 侧的加权项留给后续阶段接入
   （7A 已把"只能交出建议"这件事钉死在测试里）。
