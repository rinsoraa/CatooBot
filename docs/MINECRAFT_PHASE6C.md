# Phase 6C — Activity Planner / Rolling Horizon / Persistent Goals

> 6A 回答"她现在在做什么"；6B 回答"当前活动继续、延长还是切换"；
> **6C 回答"接下来几个小时，她大概准备做什么"。**
>
> 一句话记住边界：**这是 Plan，不是 Reality。** 现实永远只有 `ActivityEpisode`。

---

## 0. 动手前的三个决定（先查再写，§二/§四十五）

| # | 决定 | 理由 |
| --- | --- | --- |
| **A** | **不新建目标系统**：`PersistentGoal` 是既有 `app/sandbox/goals.py`（`sandbox_goals` 表，migration 23）的**只读投影** | §二 要求先查现有实现。既有层已经有 `kind` / `status` / `priority` / `progress`（本来就是 0.0~1.0，正是 §二十四 推荐的口径）/ `target_item` / `target_project`，而且**本来就**遵守"Goal 是世界上下文，永远不是动作"。再建一张 `activity_goals` 就是第二套目标系统 |
| **B** | 日程锚点是**代码里的默认一天**（`DEFAULT_ANCHORS`），不加 YAML 旋钮 | §十二 只要求 `ScheduleAnchor` 这个形状 + §十三 的优先级；§六十八 明确"不要增加几十个 tuning knobs"。锚点可注入（测试/运维给不同的一天） |
| **C** | **WebUI 保持纯只读**：计划只有 `GET`，没有 force select，也**没有**"重排"按钮 | §五十八 说"管理员只读查看…也不能 Force select。第一版只读"。§六十八 允许的"手动刷新"只做进程内入口（`refresh_plan(trigger=MANUAL)`），供 CLI/smoke 用 |
| **D** | 时段仍用既有四档（morning / afternoon / evening / night） | §四十 说"如果角色已有更细时间系统：复用"。既有 `WorldClock` 就是这四档："day" 即 afternoon，"late_night" 并进 night。在 planner 里另立一套时段才是真的会打起来 |
| **E** | 补 6 个活动名的时长档与人话标签（`free_time` / `resting` / `relax` / `music` / `watching_show` / `building`） | 6B 的兜底集合 `("idle","free_time","resting")` 里有两个**没有**时长档/标签的裸 token；6C §十六 的自由活动池又要用 `music`/`relax`；§二十二 的例子里明确点名 `building` |

---

## 1. ActivityPlanner（§三/§十/§十七）

`app/activity/planner.py` 升级为一个**纯函数**规划器：

```python
plan_next(character_state, current_episode, *, now, horizon=None, context=None) -> ActivityPlan
```

* **纯计算**：无数据库、无沙盒、无 Minecraft、无模型、无随机（有源码级 guard 测试）。
* **6A 的两个入口签名一字未改**：`initial()` / `next_after()` 仍在，语义仍是"下一步 / 延长 / 收尾"，
  但它们现在**从同一份计划里取"下一步"** —— 所以"Planner 的选择"和"计划里写的下一步"
  永远是同一个东西（§十七），不会出现两套口径。
* `PlannerContext` 是一次规划的全部输入（状态 / 当前 Episode / 历史 / 目标快照 / 锚点 /
  started_today / horizon / 上限 / 触发点）—— 全是**只读事实**，没有任何句柄或回调。

## 2. Rolling Horizon（§六-§九/§四十一/§六十二）

* 配置 `world.activity.planning_horizon_minutes`（默认 **240**，范围 **60~720**，超范围直接
  validation error）；§七 的"绝不允许一次规划 24 小时 / 生成全天完整时间表"由范围本身兜住。
* 条目上限 `world.activity.max_future_episodes`（默认 **6**，`ge=1, le=6`）。
* **铺法**（确定性四步循环）：现实优先（`CONTINUATION`）→ 锚点窗口 → "装不下就先别开始"
  （§十五）→ 相邻不同名（§十五/§十九）。
* **一次规划，一次落盘**：`plan.plan_id` 由存储层分配（`PLAN-YYYYMMDD-NNN`），Planner 不碰存储。

**触发点与冷却**（§八/§九）：普通 tick **只**做一次廉价检查（内存计划 + 一次只读状态/目标签名），
真的重算只发生在八个触发点上：

| 触发点 | 类型 | 说明 |
| --- | --- | --- |
| `EPISODE_ENDED` | **硬** | Episode 结束/换活动 = 计划的前提没了 |
| `RECOVERY` | **硬** | 重启对账之后 |
| `MANUAL` | **硬** | 管理员/CLI 手动刷新（WebUI 没有这个按钮） |
| `ANCHOR_CHANGED` | **硬** | 锚点配置变了 |
| `USER_INTERACTION` | **硬** | 用户交互 |
| `GOAL_CHANGED` | 软 | 走冷却（目标进度一直在动，否则等于每 tick 重算） |
| `STATE_CHANGED` | 软 | 走冷却（状态签名四舍五入到 0.01，抹掉抖动） |
| `PLAN_EXHAUSTED` | 软 | 覆盖不到 `now + 10 分钟` 了 |

## 3. Current vs Future（§五/§五十九/§七十二）

```
CharacterState.activity / activity_status / …    ← ActivityEpisode 的派生快照（现实）
ActivityPlan.items                              ← 未来的意图（可能随时被重排）
```

给 LLM 的上下文是**两块**（`activity_context_block` + `plan_context_block`），计划那块自带
「这是**计划**，不是现在正在做的事」与「如果计划和现状不一致，以现状为准」。所以
"你接下来准备干嘛？"与"你现在在干嘛？"必然是两个答案（§七十二 的真机门禁就测这个）。

计划里第一条可能是 `CONTINUATION`（"她现在这件事继续到几点"）—— 它是**预测**，不是现实；
`plan.next_item(now)` 会跳过它，所以"下一步"永远不会等于"现在这一步"。

## 4. Routine（§十/§十一/§三十九）

* 既有 `ROUTINE_BY_PERIOD`（时段 → 候选活动）**保留**，但降级为**偏好来源之一**
  （还有锚点、目标、自由池）。
* Routine **不创建 Episode**（§十一）：`Routine → ActivityPlanner → ActivityPlan → ActivityEpisode`。
  代码里没有任何 `Routine → ActivityRuntime.start(...)` 的路径（有测试守着）。
* 固定活动（睡觉/三餐）**不给习惯分** —— 它们的位置由锚点决定；否则"吃饭"会靠习惯分
  在她一整天里到处冒出来（真机前实测过：06:13 的早饭、17:30 的提前晚饭）。

## 5. Schedule Anchors（§十二-§十五）

```python
ScheduleAnchor(anchor_id, activity, target_time="HH:MM",
               window_before, window_after, priority, hard, days=None)
```

* **优先级**（§十三）：`sleep > meal > fixed_event > routine_activity > free_activity`
  （`AnchorPriority`，权重 1.0 → 0.2，tie-break 用它）。
* **硬锚点**（§十四）**不**等于"12:00:00 强制切活动"：它只做两件事 —— 窗口内把这件事顶到候选前面，
  以及"窗口 + 6B 的 transition guard"。真机/测试都验过：窗口里当前活动才 3 分钟 → 仍然 CONTINUE。
* **弹性锚点**（§十五）：窗口内他人让位、窗口外线性衰减；空档装不下候选就**跳过空档**
  （避免"玩到一半被打断"）；锚点之后**不回到锚点前那件事**（`gaming → lunch` 而不是
  `gaming → lunch → gaming`）。
* `anchor_adherence(...)` 给出"锚点遵守度"（§六十三 的 3 天模拟检查项），**只统计已经过去的锚点**。

## 6. Fixed / Flexible / Free（§十八/§十九）

`app/activity/profiles.py`：18 个活动的画像（`ActivityProfile`）。

| 字段 | 说明 |
| --- | --- |
| `kind` | `fixed`（睡觉/三餐，只由锚点排）/ `flexible`（工作/家务/出门/项目）/ `free`（自由池） |
| `min/typical/max_duration` | **属性**，直接读 6A 的 `VIRTUAL_DURATIONS`（**不**复制第二份时长表） |
| `flexibility` / `energy_cost` / `focus_cost` / `social_preference` / `anchor_compatibility` | 打分与解释用 |

没有性格向量、没有概率、没有随机；`energy_cost` 是 0~1 的确定性常数（休息类为负）。

## 7. Candidate Generation（§十六/§三十三/§三十四）

来源顺序（确定性、去重保序、**上限 6**）：**锚点活动** → **时段习惯** → **目标亲和** →
**自由活动池** → （能量极低时）**安全集合** → 兜底。候选太少则用自由池/兜底补到至少 3 个。

* 目标亲和排在自由池之前是刻意的：自由池是"通用填充物"，如果它先占满上限，目标就永远进不了
  候选、§二十二 的排名加成会变成死代码。
* 每个候选带 `eligible` / `reason`（`UNKNOWN_ACTIVITY` / `PERIOD_ILLEGAL` / `ENERGY_TOO_LOW` /
  `ANCHOR_CONFLICT` / `NOT_MOVEABLE` / `RESTRICTED` / `NOT_ENOUGH_TIME`）。
* 资格判定在**每个计划项各自的时刻**重判一次（同一个候选在 12:10 被锚点挡住，在 13:30 就自由了）；
  `plan.candidates` 里那份判定说明的是"**规划那一刻**为什么选它"。

## 8. Candidate Ranking（§三十五-§三十九）

`score = Σ (term × weight)`，八项都能单独解释（`Candidate.breakdown`）：

| 项 | 权重 | 含义 |
| --- | --- | --- |
| `anchor_fit` | 3.0 | 锚点契合（窗口内 1.0，窗口外线性衰减） |
| `routine_preference` | 2.0 | 时段习惯（表内位次 + 确定性轮换；固定活动 0） |
| `energy_fit` | 0.96 | 能量契合（硬的那一半） |
| `focus_fit` | 0.64 | 专注契合（**软**信号，绝不拒绝任何活动） |
| `goal_relevance` | 1.2 | 目标相关度（亲和 × 优先级 × 未完成度） |
| `repetition_penalty` | −1.8 | 最近 ≤5 个 Episode 的重复惩罚（防震荡） |
| `flexibility` | 0.5 | 弹性（自由活动适合填空） |
| `time_period_fit` | 1.0 | 时段—类别契合（三档：1.0 / 0.5 / 0.15） |

* **score 不是概率**（§三十五 / v1.0 §23）：没有阈值比较、没有随机；权重是代码常量
  （可整份注入替换），配置面只加三个真旋钮（§六十八）。
* **tie-break**（§三十七，顺序一字不差）：`anchor_priority → goal_priority → routine_priority →
  name`（最后一道一定是稳定的活动名，字典序）。

## 9. Persistent Goals（§二十-§二十五/§五十三/§五十四）

见 §0 决定 A：**只读投影**，不建第二套目标系统。`PersistentGoal` 只做一件事 ——
给相关活动加 `goal_relevance` 分：

* **Goal ≠ Task**（§二十一）：目标层没有任何执行入口；`goal_relevance` 只是一个 0~1 的数。
* **Goal 不得覆盖生理约束**（§二十三）：能量极低时，优先级 1.0 的项目目标也排不出 `building`。
* **完成条件只有结构化来源**（§二十五）：本层只读既有状态（那由物品到手/宠物喂过/项目完成这类
  结构化事件驱动），**没有**"模型说完成就完成"这条路。
* **记忆/目标都不是权限**（§五十四）：`goals.py` 里不出现 `allow_medium`、`Policy`（有测试）。

## 10. Energy / Focus（§二十六-§二十九）

* **适配既有字段，不新建**：`energy` / `current_focus` / `schedule_state`（作息）/ `social_state` /
  `mood` 都从 `CharacterState` 读；`current_focus` 是**文字**时（例如 `"reading"`）专注度视为未知 → 中性。
* **能量是硬的**（§二十八）：`energy < 0.25` 时只允许 `LOW_ENERGY_SAFE` 里的活动
  （睡觉/休息/看书/听音乐/发呆/吃饭/打理自己/看剧…）。§二十八 的"除非角色 profile 明确允许"
  落成一张**显式**白名单 `LOW_ENERGY_EXEMPT`，**默认空**。
* **专注是软的**（§二十九）：只进打分（`focus_fit`），从不拒绝任何活动。

## 11. Plan Persistence（§四十三-§四十五/§六十七）

迁移 **30** 建两张表（都是 `CREATE TABLE IF NOT EXISTS`，可重复执行）：

* `activity_plans`（`plan_id` / `character_id` / `plan_version` / `status` / 时间 / `source` /
  `trigger` / `content_hash` / `superseded_by` / `constraints` JSON / `candidates` JSON）
  + **partial unique index** `idx_activity_plans_active`（每角色最多一份 `ACTIVE_PLAN`）；
* `activity_plan_items`（`plan_id` + `sequence` 主键 / 活动 / 起止 / `reason` / `priority` /
  `anchor_id` / `goal_id` / `score`）。

三条不变量：

1. **写新计划 = 一个事务**：作废旧计划 + 插入新计划与条目（§六十七 事务性）；
2. **旧计划永不删除**（§四十四）：只标 `SUPERSEDED` 并记 `superseded_by`；
3. **版本只在内容真的变了才 +1**（§四十三）：`content_signature()` 用**相对偏移**而不是绝对时刻
   —— 否则"同样决定的计划晚一分钟生成"也会被当成内容变化，版本号就失去意义了。

**计划项≠Episode**（§四十六）：没有 id、没有状态机、不进 `activity_episodes`。

## 12. Recovery（§四十七-§四十九）

* 重启后**先认现实**（当前 Episode），再认计划；`recover()` 的结果里带上 `plan` 字段
  （`none` / `loaded` / `stale`）。
* **计划过期就重排**，绝不盲目接着跑一份未来的时间表；计划里的条目**永远不会**被当成现实
  （没有 Episode 时 `recover()` 仍然返回 `action="none"`）。
* **Planner 失败**（§四十八）：保住现有活动、绝不让 `activity` 变空；连 `planner.initial()`
  都炸了还有最后兜底（`idle`／夜里 `resting`），而且这个兜底**不经过 Planner 对象**。
* **计划存储坏了**：计划只在内存里用（没有计划号）、降级原因如实记；活动照常。
* **没有可用候选**（§四十九）：`free_time`（白天/有精神）或 `resting`（低能量/夜里），确定性。

## 13. Task Integration（§三十/§三十一/§六十一）

* 任务型 Episode 仍然只带 `related_task_id`（引用，不复制状态机）；计划**绝不**创建/确认任务。
* 任务开始/结束都走 6A 的既有事件路径；任务结束后**不自动续摊**（6A 语义不变），
  下一次 tick 会重排计划并按计划第一条开新 Episode。
* 任务完成会把计划一起重排（真机门禁 Real Java B 的证据就是"计划号变了"）。

## 14. Minecraft Observation（§五十五/§五十六）

* 6C 可以知道"Minecraft 在线/最近任务/最近记忆/玩家在不在"，但这些**只**影响上下文，
  绝不产生 Minecraft 动作、绝不自动创建任务。
* **绝不产出 Minecraft 活动名**：6A §二十九 的禁令原样保留（planner 层有源码级 guard）。
  需要表达"她对 Minecraft 的兴趣"时用虚拟活动 `building`（**不带** `related_task_id`，
  因此永远不能被说成"她在现实世界里动手"）。

## 15. Security（§六十六/§六十七/§七十六）

* **AST guard**（不是 grep）：`app/activity/**` 不许 import `app.ai` / `app.tools` / `app.tasks` /
  `app.integrations.minecraft` / `app.agent` / `app.core` / `httpx` / `openai`；不许出现
  `confirm_and_start` / `execute_action` / `move_to` / `dig` / `place_block` / `send_message` 这类调用。
* **七条不变量**：Planner ≠ Executor；Plan ≠ Reality；Goal ≠ Task；Activity ≠ Task；
  Memory ≠ Permission；Observation ≠ Action；Future Schedule ≠ Current Activity。
* 新增的写入口只有"重排计划"（`refresh_plan`），它**不接受任何活动参数**（无 force select），
  也改不了当前 Episode（要改只能过 6B + Episode 状态机）。
* `allow_medium` 默认值没变；没有新 Minecraft 工具（仍 19 个）；没有新 ActionRuntime action；
  没有新 TaskRuntime 状态。

## 16. Real Java（§六十一，A–D）

| 门禁 | 要做的 | 判定依据（只读：smoke 脚本 + 真实库 + 日志） |
| --- | --- | --- |
| A | 真实 Minecraft 在线 → Planner context 能识别，但不执行动作 | |
| B | 真实 Task 进行 → `Activity = minecraft_task`；任务完成后**能重新规划** | |
| C | Minecraft 离线 → 不生成"我正在 Minecraft 里"，但可以有虚拟兴趣 | |
| D | 真实 world event → 不得绕过 `TaskRuntime` 直接产生 Minecraft 动作 | |

现场结果见本文件末尾的"真机取证"一节（与 6A/6B 一样，逐条贴证据）。

## 17. Real QQ（§五十九/§六十/§七十二）

* **QQ A**：`你现在在干嘛？` → 当前 `ActivityEpisode`（6B 那一轮漏掉的门禁，本阶段补上）。
* **QQ B**：`你接下来准备干嘛？` → 计划里的**下一步**（不是现状）。
* 必须能证明 `current != planned` 时不会把计划冒充现状：上下文里两块分开写、
  计划块自带"以现状为准"的声明。

## 18. 3-day Fast Forward（§六十二/§六十三/§六十四/§六十五）

用 `FakeClock.advance(...)` + `ActivityRuntime.advance()` 跑三天（每 15 分钟一次 tick），断言：

* Episode 与计划数量都**有界**（不会"每分钟一个"、也不会"几千个 plans"）；
* 每条 Episode 都有活动名（v1.0 §126）；没有 Minecraft 活动名混进来；
* 锚点遵守度 ≥ 阈值（吃饭/睡觉大致发生了）；
* 普通 tick 是 O(1) 量级（50 次 tick 远快于 1 秒），plan refresh 只在触发点发生。

## 19. Known Limitations

1. **不接 LLM**（§五十/§五十一）：决策与规划都只有规则那一半；`advisor` 接口留空（`None`），
   模型辅助留给 6D。
2. **锚点是代码里的默认一天**（决定 B）：没有 YAML 旋钮；要改得动代码或注入别的 `AnchorBook`。
3. **时段是既有四档**（决定 D）：没有单独的 `late_night` / `day`。
4. **计划没有"重排"入口**（决定 C）：WebUI 纯只读；手动刷新只在进程内（CLI/smoke/测试）。
5. **候选资格按规划时刻的状态判一次**：未来时刻在铺计划时会重判，但"能量/专注"用的是
   规划那一刻的值（计划是意图，会随触发点重算）。
6. **相邻同名只在展示层/铺计划时处理**：库里仍是两条记录（§十九 要求不丢原始审计）。
7. **QoL 债**：`activity_plan_items` 没有外键（本项目 SQLite 一贯不带 FK），靠事务保持一致。

---

## 真机取证

> 与 6A/6B 同一纪律：**没真跑过的一律写 SKIPPED，绝不写成 PASS**。

（本节的现场证据在真机轮次之后补写，格式与 `MINECRAFT_PHASE6B.md` §15 一致。）
