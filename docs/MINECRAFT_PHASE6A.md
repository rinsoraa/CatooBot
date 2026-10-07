# Minecraft Phase 6A — World Activity Runtime / Activity Episode Foundation

> 目标：把角色的「当前活动」从**瞬时状态标签**升级为有生命周期的 **ActivityEpisode** ——
> 有开始、有计划结束、有延长上限、有结束原因，可持久化、可重启恢复、可审计。
>
> 6A 的终点是「**罐头知道自己现在正在做什么，而且这个状态是可持续、可恢复、可审计的**」，
> **不是**「她已经可以自己在 Minecraft 里到处跑」。后者留给 6B/6C。

一句话架构（§八/§九）：

```
World Clock（app/activity/clock.py）
      ↓
ActivityPlanner（确定性；只说「下一步可能是什么」）
      ↓
ActivityEpisode + ActivityRuntime（唯一权威：「当前到底是什么」）
      ↓
CharacterState 投影（activity 只是 Episode 的派生快照）
      ↓
Behavior / Social Cognition / 聊天上下文
```

**三种事件源各管一段，谁也不替谁决策**：

| 来源 | 谁决定 | 落成什么 Episode |
| --- | --- | --- |
| 她的虚拟日常（沙盒） | 沙盒自己的决策引擎（本来就是她的日程） | `source=ROUTINE`、`activity_type=virtual_life` |
| 真实任务 | `TaskRuntime` 的 `task.*` 事件 | `source=TASK`、`activity_type=task_execution`、带 `related_task_id` |
| 她"空着"、或 Episode 到期 | 本阶段的 **确定性 Planner** | `source=ROUTINE`（按世界时钟的时段查表） |

其余事件（用户交互 / 世界事件 / 重启恢复）只**记录观察**，不自己改状态。

---

## 一、architecture（为什么是"升级复用"而不是新系统）

写代码前先把仓库翻了一遍（§二 要求），结论如下：

* **`app/world/` 不存在** —— 这个项目里"她的世界"是 `app/sandbox/`（SandboxRuntime：
  空间/物件/库存/规则引擎 + needs + 目标 + 承诺）。所以 6A **不新建** `app/world/`，
  新领域叫 `app/activity/`（与 `app/tasks/` 平级，语义清楚）。
* 迁移 **v10 曾经建过 `activity_episodes`**（v1.x 的 "World Activity Runtime"，
  设计文档 `docs/specs/v1.0.md` §5-§9 就是它），**v14 跟着 v0.8 的 persistent world 一起被
  DROP**（"v2.0 的沙盒取代了它"）。所以表名与语义有历史先例：6A 按 §二十五 **重建同一张表**
  （迁移 **29**），不另起名字、不造第二套活动系统。
* `CharacterState` 里**早就声明了 v1.0 的 episode 字段**（`current_activity_episode_id` /
  `activity_started_at` / `activity_planned_end_at` / `activity_status`），但**从来没有写入方**
  —— 是死字段。6A 把它们**接线**：由 Episode 投影写入。
* 世界时间只有一处：`RuntimeScheduler`（默认 1s，调 `SandboxRuntime.tick(minutes=…)`，
  并向沙盒事件总线发 `runtime_tick`）。6A **复用**它：`RuntimeScheduler(activity=…)`
  在每次 tick 末尾推一次 `ActivityRuntime.advance()`，**不新建第二个循环、不另造时钟**。
* 沙盒的「当前动作」是 `ActionInstance`（表 `sandbox_actions`，带 started/planned_end/status/source）
  —— 语义上就是 Episode 的前身。6A **不改造沙盒内核**：沙盒继续当她的生活模拟器，
  它报来的活动变成 `source=ROUTINE` 的 Episode；两层之间是**单向**的观察关系。

一句话：**Episode 是"活动生命周期"的唯一权威，沙盒是"虚拟生活"的模拟器，TaskRuntime 是"任务执行"的权威**。

## 二、Episode model（§三/§四/§七/§二十五）

```python
ActivityEpisode(
    episode_id,  # ACT-YYYYMMDD-NNN（全局唯一 / 持久化 / 可反解 / 重启不变）
    character_id,  # 与沙盒同一个 character_id（名字@圣经哈希），拿不到就退回角色名
    activity_type,  # virtual_life / task_execution / user_interaction / recovery / system
    activity_name,  # 世界自己的词表（eating/gaming/reading/out/sleeping/idle…）
    location,
    social_state,
    tags,
    started_at,
    planned_end_at,
    ended_at,
    min_duration,
    typical_duration,
    max_duration,  # §七：时长是 Episode 的属性
    status,
    transition_reason,
    source,
    parent_episode_id,  # 接在哪个 Episode 之后（暂停→恢复、到期→下一个）
    related_task_id,  # §十八：只**引用**任务，绝不复制任务状态机
    extension_count,  # 延长了几次（有上限，§二十七 C）
    observation,  # 最近一次只读观察（§十六：只用于判断"还合理吗"）
    created_at,
    updated_at,
)
```

* **时长三值**：`planned_end_at = started_at + typical_duration`，硬上限
  `max_end_at = started_at + max_duration`。**绝不** `start → hardcode end`，也**绝不**
  每 tick 随机（§四十五 有源码级测试：整个包不许出现 `random`）。
* **词表统一**：`activity_name` 用沙盒动作定义里的那套 token（`app/sandbox/action_templates.py`），
  这样 `CharacterState.activity` 不会出现"两套语言"。给模型看的人话在
  `model.ACTIVITY_LABELS` 里映射（`reading` → 看书），**只影响措辞**。
* 文件：`app/activity/model.py`（模型 + 状态机 + 时长 + ID）、`clock.py`（世界时钟）、
  `store.py`（持久化）、`events.py`（事件）、`planner.py`（确定性日程）、
  `runtime.py`（生命周期）、`projection.py`（角色状态投影）、`adapters.py`（四个事实源接线）。

## 三、state machine（§五/§六/§四十四）

```
SCHEDULED ──→ ACTIVE ──┬─→ EXTENDED ──┬─→ EXTENDED（可以再延长）
    │                  │              ├─→ COMPLETED
    ├─→ CANCELLED      ├─→ COMPLETED  ├─→ INTERRUPTED
    └─→ EXPIRED        ├─→ INTERRUPTED├─→ CANCELLED
                       ├─→ CANCELLED  └─→ EXPIRED
                       └─→ EXPIRED
```

* 一张显式的 `ALLOWED_ACTIVITY_TRANSITIONS` 表；**终态没有任何出口**
  （`COMPLETED → ACTIVE` / `CANCELLED → ACTIVE` / `EXPIRED → ACTIVE` / `INTERRUPTED → ACTIVE`
  全部拒绝，抛 `InvalidActivityTransition`）。
* **并发（§四十四）**：每次转移都是 `store.transition(expect=[当前状态], to=…)` 的
  **compare-and-set**；两个事件同时收尾时只有第一个拿到 CAS，第二个读到 `None` → 静默忽略。
  SQLite 实现把它放进一个事务里（`run_in_transaction`）。
* **唯一性（§十二/§二十六）**：运行态由一个 `asyncio.Lock` + CAS 串行化；
  数据库层还有 partial unique index（`WHERE status IN ('SCHEDULED','ACTIVE','EXTENDED')`）
  兜住"两个进程同时想开 ACTIVE Episode"。
* 每一次转移都追加一行审计（`activity_transitions`），并带
  **标准化原因**（`TIME_EXPIRED` / `TASK_STARTED` / `TASK_COMPLETED` / `TASK_FAILED` /
  `USER_INTERACTION` / `WORLD_EVENT` / `SCHEDULED` / `RECOVERY` / `MANUAL`）。

## 四、persistence（§二十三/§二十五/§二十六）

* 迁移 **29** 建两张表（都是 `CREATE TABLE IF NOT EXISTS`，可重复执行）：
  `activity_episodes`（一行 = 一个 Episode）+ `activity_transitions`（一行 = 一次转移）。
* **不每秒写库**（§二十三）：Episode 的**状态转移**立即落盘；运行中的**观察**按
  `world.activity.persistence_interval_seconds`（默认 60s）节流落盘。
* **事件幂等**（§三十三）：`activity_transitions` 上有一个 **partial unique index**
  （`WHERE transition IN ('ACTIVE','COMPLETED','INTERRUPTED','CANCELLED','EXPIRED')`），
  `INSERT OR IGNORE` 的 `rowcount` 决定要不要发事件 —— 重启之后重放同一条终结事件
  也只会被丢掉（`EXTENDED` 不在索引里，因为它可以合法重复）。
* 旧数据没有 Episode 是**合法状态**（§五十二）：迁移不生成任何占位 Episode。

## 五、recovery（§二十七/§二十八）

`ActivityRuntime.recover()` 在启动时对账（**只**处理已经存在的 Episode）：

| 情况 | 条件 | 行为 |
| --- | --- | --- |
| A | `now < planned_end` | 继续 ACTIVE（如果还没开始就先 ACTIVE），发 `activity.recovered` |
| B | `planned_end ≤ now ≤ max_end` | 发 `activity.recovered`，然后交给 Planner（延长一次或换下一个） |
| C | `now > max_end` | `EXPIRED`（绝不无限延长），再由 Planner 排一个新的 |

* **宽限**：`recovery_grace_seconds`（默认 30s）—— 刚过期几秒不算过期，免得每次重启都强行转移。
* **绝不伪造活动**（§二十八）：离线 8 小时之后不会出现"她这 8 小时一直在砍树"。
  旧 Episode 只会被**终结**（`ended_at = 现在`），新的活动只有 `started_at = 现在` 的那条；
  测试里断言"没有任何 Episode 的时间窗覆盖离线区间"。
* **没有 Episode 就不造**（§五十二）：`recover()` 返回 `action=none`；"她现在做什么"
  交给下一次世界 tick（那是正常的时钟事件，不是恢复期的臆造）。
* **失败隔离**（§五十一）：Activity 数据库不可用时 → 只设 `degraded_reason`、读返回空、
  写被忽略；任务、Minecraft、QQ 一概不受影响（有测试）。

## 六、Task relation（§十八/§十九/§四十二/§四十三）

| 任务事件 | Episode |
| --- | --- |
| `task.started` / `task.resumed` | `switch_to(minecraft_task, type=task_execution, source=TASK, related_task_id=…)`，`reason=TASK_STARTED` |
| `task.paused` | **INTERRUPTED**（`reason=USER_INTERACTION`）——§四十二：任务暂停时活动不能还 ACTIVE |
| `task.succeeded` | COMPLETED（`reason=TASK_COMPLETED`） |
| `task.failed` | INTERRUPTED（`reason=TASK_FAILED`） |
| `task.cancelled` | CANCELLED（`reason=MANUAL`） |
| `task.expired` | EXPIRED（`reason=TIME_EXPIRED`） |
| 其它（`plan_ready` / `confirmation_required` / `step_*` / `replanning` / `authorization_expired`） | **不动**：它们是同一个活动内部的阶段 |

* 每个终态映射到**不同**的 Episode 状态，审计能一眼看出"取消/超时/失败"。
* **恢复不重建**（§四十三）：如果已经有一条 live Episode 绑着同一个 `task_id`，就复用它；
  暂停之后再恢复会开**新** Episode 并把 parent 指向前一条（状态机不允许 INTERRUPTED→ACTIVE）。
* 任务结束后**不自动续摊**：下一个 tick 让她回到自己的日常（Planner 决定），
  这也是"绝不自主行动"的一部分。

## 七、Minecraft observation boundary（§十六/§十七/§二十九/§四十二/§四十八）

```python
MinecraftObservation(
    online,
    server_id,
    player_uuid,
    position,
    nearby_players,
    current_action,
    current_task_id,
    task_state,
    observed_at,
)
```

* 全是从**只读**镜像（`service.snapshot()` / `service.world_view()`）读出来的**事实**，
  观察器只允许调用这两个方法（AST 级测试守着）。
* **观察不是命令**（§十七）：看一眼世界**不会**创建、修改或终结任何 Episode；
  它只写进 `episode.observation` 供审计，并让 `runtime.plausible(observation)`
  回答"这个 Episode 与当前世界还一致吗"（例如她掉线了 → `minecraft_offline`）。
  **是否中断由任务事件/时钟决定，不由观察决定。**
* **虚拟 ≠ 真实**（§二十九/§四十八）：Planner 的日程表在构造时就会拒绝
  Minecraft 活动名；`start()` 对非 TASK 来源的 `minecraft_*` 名字**直接拒绝**；
  Minecraft 掉线时不会凭空出现 `minecraft_exploring` 这类活动（有测试）。

## 八、security boundary（§四十九/§五十）

* Activity 这一层**不认识任何世界动作**：`move_to` / `dig` / `place` / `pickup` /
  `follow_player` / `equip` / `inventory_move` / `container_transfer` / `craft` / `look_at`
  在 `app/activity/**` 里既没有调用、也没有 import（**AST 级** guard 测试）。
* **Policy 不认识 Activity**（§五十）：`app/integrations/minecraft/agent.py`、
  `app/tools/executor.py`、`app/integrations/minecraft/confirmation.py` 里不出现
  `ActivityRuntime` / `ActivityEpisode` / `activity_episodes` / `activity_status`。
  「活动=trusted_companion → allow_medium」这类推断在结构上不可能发生。
* **事件不产生 AI Turn**（§三十四）：`ActivityEventPublisher` 只有一个同步 sink + 日志，
  Bot 装配时**没有**传 sink；角色侧只有 `context_block()` 这一个**读**入口
  （不许调用 `start/switch_to/complete`）。
* **Activity 不写 Memory**（§二十一）：这一层连 memory 都不 import，
  `ActivityRuntime.__init__` 也不接受任何记忆对象 —— 不是"没写"，是"拿不到"。

## 九、real smoke（§四十七 A–E）

脚本：`scripts/activity_smoke_real.py`（**只读**核对，不代替你操作）。

```bash
.venv/Scripts/python.exe scripts/activity_smoke_real.py --phase report      # 当前 Episode + 转移审计
.venv/Scripts/python.exe scripts/activity_smoke_real.py --phase task-start  # A：QQ 建任务 + 确认
.venv/Scripts/python.exe scripts/activity_smoke_real.py --phase task-done   # B：任务跑完
.venv/Scripts/python.exe scripts/activity_smoke_real.py --phase pause       # C：暂停
.venv/Scripts/python.exe scripts/activity_smoke_real.py --phase restart     # D：重启（同一 episode_id）
.venv/Scripts/python.exe scripts/activity_smoke_real.py --phase offline     # E：Minecraft 掉线不造假活动
```

| 阶段 | 你要做 | 脚本核对 |
| --- | --- | --- |
| A | 在 QQ 里让她做一个 Minecraft 任务并「确认」 | 出现 `source=TASK` + `related_task_id` 非空的 live Episode，`reason=TASK_STARTED` |
| B | 等任务真的跑完（挖到 + 捡到） | 那条 Episode → `COMPLETED` + `TASK_COMPLETED`，且有 `ended_at` |
| C | 任务进行中在 QQ 里说「暂停」 | 活动**不是** ACTIVE → `INTERRUPTED`（`reason=USER_INTERACTION`） |
| D | 活动 ACTIVE 时重启 CatooBot，再重新进服 | 同一个 `episode_id` 仍然 live、`started_at` 没被重置、没有第二条 live |
| E | 让 Minecraft 掉线 | 表里没有凭空出现的 `minecraft_*` 活动 |

WebUI：**World 页**新增只读卡片「当前活动（Phase 6A）」——
Episode ID / Activity / Status / Started / Planned End / Duration / Source / Related Task /
Transition Reason / Extensions，以及最近 ≤10 条；`GET /api/v1/world/activity?limit=10`
（读端点恒 200，没有活动能力时 `enabled:false`）。**没有任何** start / cancel / extend 入口（§三十七）。

## 十、known limitations

1. **不自主行动**（§五十七）：6A 不新增任何 Minecraft 工具（仍是 **19** 个）、
   不新增 ActionRuntime action、不新增 TaskRuntime 状态；Activity 不会调用世界动作，
   也没有"自动砍树/自动探索/自动跟随/自动回基地"这类东西。`allow_medium` 默认值未变。
2. **Secondary Context 只建了模型**（§十三）：`ActivityEpisode` 支持 `tags`/`notes` 形态，
   但 6A 不实现二级活动运行时；同一角色同一时间只有**一个** primary Episode。
3. **Planner 是"日程表"不是"日程引擎"**：它只在"她空着 / Episode 到期"时按世界时钟的时段
   查表（确定性旋转）。真正有自由度的是沙盒（她的日常模拟器）；6A 不引入 LLM 自主决策。
4. **观察只用于判断，不用于驱动**：`plausible()` 目前只把结论暴露给审计，
   不自动中断 Episode（是否中断由任务事件/时钟决定）。这是刻意的保守选择。
5. **`schedule_state` 不由 Episode 派生**：`awake/resting/sleeping` 仍然是沙盒/presence 的
   领域；6A 只接管 `activity*` 这一组字段（§十四 明确列出的那几个）。
6. **character_id 与 5C 的偏移**：Activity 用沙盒的权威 `名字@圣经哈希`；
   Phase 5C 的 Minecraft 记忆用的是 `名字:minecraft`（当时决定"暂选 A"）。两者各自隔离，
   但同一角色在不同表里的 `character_id` 写法不统一 —— 与 5C §十一 记的是同一件事，
   等后续阶段一起收。
7. **WebUI 只读**（§三十七）：没有 Start / Cancel / Extend / Force Transition；
   管理员的"直接改 activity"会被**翻译成一次显式换活动**（source=USER / reason=MANUAL），
   不绕过 Episode（§十四）。
8. **角色重置不覆盖活动表**：`CHARACTER_TABLES`（她重置时清空的那批表）**没有**包含
   `activity_episodes` / `activity_transitions`（Phase 5C 的 `minecraft_identity_links` 也一样）。
   重置之后旧 Episode 会留在库里，但因为 `character_id` 变了（新名字/新圣经哈希），
   她对它们既看不见也读不到 —— 只是占地方。要不要把"角色重置/导出"的范围一起扩到这两张表，
   留给后续阶段统一处理（改的是**破坏性**语义，不在 6A 范围内自作主张）。

9. **真机门禁需要你在本机跑**：A–E 依赖真实 QQ + 真实 Java 服务器 +
   （D）重启 CatooBot，脚本只做只读核对。
