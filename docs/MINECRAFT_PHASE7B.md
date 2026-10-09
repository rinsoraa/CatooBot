# CatooBot Phase 7B — Virtual Autonomous Activity / Initiative-to-Plan Integration

> 7B 的唯一核心目标：把 7A 生成的合格 **LifeIntent** 真正接进既有 **ActivityPlanner**，
> 让她的主动想法影响未来的虚拟生活安排，并在合规转移时**落成虚拟 ActivityEpisode** ——
> 同时她**依然**没有因此获得任何执行 Minecraft 世界动作的能力。
> 对齐任务书 Phase 7B §零-§二十；测试见 `tests/test_initiative_plan_bridge.py`。

---

## 1. 闭环（§零/§二十）

```text
LifeIntent（7A：动机/意图，只提/评/记/抑/过期）
      ↓  LifeIntentService.hints()        ← 纯数据（dict），只读、bounded、不写任何东西
InitiativeHintBook（activity 侧：名称必须来自实际 Registry）
      ↓  initiative_fit（**软**项，权重 1.0）
ActivityPlanner 候选池 + 排序
      ↓
ActivityPlan（滚动视野；条目带 intent_id 归因）
      ↓  6B DecisionEngine（最短/最长时长、撞车护栏、窗口、硬中断）
ActivityRuntime
      ↓
Virtual ActivityEpisode（**虚拟**生活活动，不是任务、不是 Minecraft 动作）
```

**没有**这条链（§一/§十八）：`LifeIntent → TaskRuntime.create_task()` /
`confirm_and_start()` / Minecraft tool / ActionRuntime / QQ proactive message。
桥这一层**源码级**拿不到任何执行面（`tests/test_initiative_plan_bridge.py` 里的 AST 守卫）。

## 2. 分层与不反向依赖（§二/§六）

* `app/activity/initiative.py`（**本阶段新增**）：桥的**形状**与整理逻辑 ——
  `InitiativeActivityHint` / `InitiativeHintBook` / `hint_book_from()`；
* `app/initiative/service.py`：只交**纯数据** `hints()`（`intent_id / intent_type / source /
  related_activity / score_bonus / expires_at`）—— 它**不认识活动注册表**，
  所以"类型 → 已注册活动"的翻译（`INTENT_ACTIVITY_MAP`）留在 activity 侧；
* 于是：**7A 的包仍然只 import 自己 + 标准库**（它的 AST 守卫一字未改），
  activity 侧也不 import `app.initiative`（只认鸭子类型的 `hints()`）；
* 接线只有一处：`bot.py` 在装配完两边之后 `planner.intent_source = self.initiative`。

**没有**新建 `LifeActivityPlanner` / `AutonomousActivityRuntime` / `MinecraftLifeRuntime` /
`InitiativePlannerV2` 这类平行系统（§二）。

## 3. 建议的形状与硬性质（§三）

`InitiativeActivityHint(activity_name, score_bonus, intent_id, source, intent_type)`：

| 要求 | 实现 |
| --- | --- |
| 只读**有效**意图（§三.1/§三.2） | 只有 `PROPOSED` 且未过期的意图进 `hints()`；`SUPPRESSED / EXPIRED / CANCELLED / RESOLVED` 一律不交 |
| bounded（§三.3） | 读取上限 `HINT_LIMIT = MAX_HINTS = 3` |
| 不重复加分（§三.4） | 同一个活动只留**最大**加成；同一个 `intent_id` 在一次规划里只出现一次 |
| Adapter 只返回数据（§三.5） | 返回 dict / 值对象；没有任何写入口，也没有指向 Episode/Plan/Task 的句柄 |
| 故障可降级（§三.6） | 来源抛异常 → **空书** + `degraded` 说明写进 `constraints.initiative`，Planner 逐字退回原行为 |
| 名称来自实际 Registry（§六） | `minecraft`（根本不注册）/ `minecraft_task` / `dig` / `move_to` … 一律**丢弃**并记 `not_registered` |

## 4. 候选评分集成（§四）

新增一个**软**项 `initiative_fit`（权重 **1.0**），只加给**已经合格**的候选：

```text
score = anchor_fit(3.0) + routine_preference(2.0) + goal_relevance(1.2) + time_period_fit(1.0)
      + energy_fit(0.96) + focus_fit(0.64) + flexibility(0.5) + initiative_fit(1.0) * bonus
      - repetition_penalty(1.8)
```

权重刻意低于锚点(3.0)/习惯(2.0)/目标(1.2) —— 于是硬约束永远优先：

```text
Hard Constraints > Candidate Eligibility > 6B Transition Guards > Initiative Soft Preference
```

具体地：`_rejection()`（能量/时段/锚点/装不下/未知活动/固定活动）**先**判，分数**后**算；
`_rank()` 的 tie-break 一字未改（§三十七）；被建议的活动只是**进入候选池**（放在目标亲和之后、
自由池之前），它仍然要自己通过资格判定才可能被排进计划。

## 5. 无 Initiative 时的行为一致性（§五）

**重要回归门禁**：没有有效意图（或 `initiative.enabled = false`）时，计划与 6C **逐字一致** ——
候选池、排序、条目、**内容签名**全部相同（`tests/test_initiative_plan_bridge.py` 有对拍测试）。
新增字段都是**审计用**，且刻意**不参与** `content_signature()`：

* `Candidate.intent_id` / `PlanItem.intent_id`：进 JSON 载荷，**不进**签名 ——
  于是"只是多记了一个 id"不会让同名计划不断 +1 版本（§五/§八）；
* 采纳/丢弃的建议明细写在 `constraints.initiative`（只读审计），同样不进签名。

## 6. 名称与执行边界（§六）

* 虚拟活动名一律以**实际 Registry** 为准（`ACTIVITY_PROFILES` + `plannable()`）；
* `INTENT_ACTIVITY_MAP`：`MINECRAFT_INTEREST → building`（虚拟的"捣鼓建造"）、
  `REST → relax`、`SOCIAL → online`、`ACTIVITY_CHANGE / PERSONAL_ROUTINE → 用它自己的
  related_activity`。**没有 `minecraft` 这个活动**（6C 就禁止它当虚拟活动），
  所以"想玩 Minecraft"落成的是**已注册的虚拟活动**，绝不冒充真实服务器行为；
* `minecraft_task` **只能**由既有 Task→Activity Adapter 依据真实 Task 事件创建 ——
  意图层提出或创建它是不可能的（桥会丢掉这个名字）；
* Minecraft 离线时：虚拟兴趣照旧可以影响计划（§三十七），但计划里**不会**出现任何
  "她正在真实服务器里活动"的说法（Episode 的 `activity_type` 仍是 `virtual_life`，
  只有任务链才会写 `task_execution`）。

## 7. 活动落实时机（§七）

计划产出 ≠ 活动开始。落成 Episode 的唯一路径仍然是：

```text
Plan item → 6B 决策（CONTINUE / EXTEND / TRANSITION 与全部硬护栏） → ActivityRuntime
```

* 没有 `ActivityRuntime.force_transition()` 之类的入口（它本来就不存在，7B 也没加）；
* 计划刷新**绝不**提前结束当前 Episode（对拍测试：刷新前后 `episode_id` 不变）；
* 最短时长没到就 CONTINUE —— 建议再大也压不过 6B 的门。

## 8. Intent 生命周期与计划归属（§八）

| 语义 | 实现 |
| --- | --- |
| Intent 被纳入 **Future Plan** | 计划条目/候选上的 `intent_id` 归因（审计） |
| Intent 对应的活动**已经开始** | 只有真的开出 `ActivityEpisode` 才算；计划里出现某活动**不等于**开始了 |
| 同一条意图不无限重复加分 | 一次规划只取一条建议、取**最大**加成；跨规划由 7A 的 fingerprint/bucket/cooldown 管住 |
| Plan 被 supersede | 6C 的既有语义不变（旧的只标 `SUPERSEDED`，绝不当作当前计划） |
| `RESOLVED` | 按 7A 语义 = **已被更高层处理**，绝不重新解释成"执行成功" |
| 过期/取消/被抑制 | 一律不参与规划（`hints()` 前置过滤），也不会被"恢复"成有效意图 |
| 崩溃/重启 | 意图/计划/Episode 都不会复制：意图靠 `fingerprint` 唯一索引，计划靠内容签名，Episode 靠 partial unique index |

**没有新增表、没有新增迁移**（仍 31）—— 归因复用三个既有结构的 JSON 列。

## 9. 用户任务与待确认任务优先（§九）

完全复用 7A 的 Gate：任务处于 `RUNNING / WAITING_ACTION / PENDING_CONFIRMATION / PAUSED` 时，
意图一律被抑制（连建议都不会产生）→ 计划自然保持"没有她自己的想法"的样子。
不自动确认、不修改已有 confirmation、不改 `allow_medium`、不取消/暂停用户任务 —— 一条都没碰。

## 10. 6B / 6C / 6C.1 / 6D 集成（§十）

* **6B**：CONTINUE/EXTEND/TRANSITION、min/max duration、BounceGuard、transition window 全部原样；
* **6C**：Routine / Anchors / Goals / 资格与排序 / Rolling Horizon / ActivityPlan 全部原样，
  只是在加权求和里多了一个软项；
* **6C.1**：Episode 延长后的计划对齐（`plan_dirty` / 冷却 / 受控 replan）原样；
* **6D**：可选 Model Advisor 原样，**默认仍然关闭**；7B 没有新增第二个 Advisor，
  也没有让 Initiative 成为额外的模型调用入口（意图层源码级不 import 模型面）。

## 11. 不要每 Tick 重算（§十一）

建议**只在现有规划触发点**读一次：`plan_next()` 每次构建计划时调用一次
`hint_book_from(source, now=...)`（O(≤3) 的纯内存读取），普通 tick 完全不碰它 ——
`advance()` 依旧只做廉价检查（内存计划 + 一次只读签名）。24 小时快进仿真验证了
"计划版本不随 tick 增长、Episode 不爆炸"（§十四）。

## 12. 只读可观测性（§十二）

* `GET /api/v1/world/activity/plan`：候选带 `intent_id` 与 `breakdown.initiative_fit`，
  计划项带 `intent_id`，`constraints.initiative` 写明这次**采纳/丢弃**了哪些建议；
* WebUI 世界页的计划卡片新增两列「意图」（候选/计划项），分项里直接能看到 `initiative_fit`；
* **没有** Execute / Force Activity / Force Plan / Run Minecraft / Confirm Intent；
  也不显示思维链、prompt、凭据或原始 provider 响应（§十二）。

## 13. QQ 语义（§十三）

三问各归其位（复用既有上下文块，互不冒充）：

```text
你现在在干嘛？     → ActivityEpisode（现状）
你接下来准备干嘛？  → ActivityPlan（计划，**已能体现意图的影响**）
你最近想干嘛？     → LifeIntent（想法；**不会**说成已执行）
```

7B 没有主动消息：意图层从头到尾没有发过一条 QQ（§四十）；任务入口与人格回复也不受影响。

## 14. 测试（§十四）

`tests/test_initiative_plan_bridge.py`（**23 项**）：Planner Adapter（有界/确定/去重/降级/
注册表裁决/类型映射/只读）、Candidate Ranking（软排序/无 hint 逐字一致/压不过硬锚点/
资格不动/权重更软）、Lifecycle（计划不动现状/合法性仍由 6B 把关/归因落盘/审计字段不升版本）、
Security（桥的 AST 守卫/纯数据）、24h Fast Forward（版本不随 tick 涨、Episode 有界、加成不累积），
以及**服务侧**（只有 PROPOSED 才成为建议 / 被抑制的不交 / 过期的自动失效 / 重复 check 不叠加 /
`MINECRAFT_INTEREST` 真的以 `building` 进计划）。

## 15. 真机门禁（§十五/§十六）

见文末「真机取证」。


---

## 15. 真机取证（§十五/§十六）—— 全部 PASS

真机：2026-10-09 07:56–08:37，`world.initiative.enabled = true`（默认）+ 真实 Minecraft
（`127.0.0.1:25565`，`username=Catodayo`）+ 真实 QQ（Computer Use 亲手发、逐条复核）。

| 门禁 | 结果 | 真机证据 |
| --- | --- | --- |
| Real Java A（在线兴趣） | **PASS** | 08:08:16 的 `state_changed` 规划（v87）里：采纳的建议含 `{"activity_name":"building","score_bonus":0.812,"intent_id":"INT-20261009-005","intent_type":"MINECRAFT_INTEREST"}`，候选 `building` 带 `initiative_fit=0.81` 进计划；**意图层零世界动作、零新任务** |
| Real Java B（离线兴趣） | **PASS** | 桥 `status=DISCONNECTED` 之后 08:13:16 的规划（v88）**同样**带着 `building / fit=0.81 / INT-…-005`；`agent_tasks` 计数不变、无 Minecraft 世界动作；在线时也不会声称她在真实服务器里活动（Episode `activity_type` 仍是 `virtual_life`） |
| Real Java C（用户任务优先） | **PASS** | 真实任务 `task_a429455cb186` 处于 `PENDING_CONFIRMATION` 时：`08:36:20 [World.Initiative] check action=idle reason=PENDING_CONFIRMATION`；任务/工具计数不变；**没有**自动确认、没有改 confirmation、没有取消任务 |
| Real Java D（真实落地） | **PASS** | 08:00:32 的一次**真实** 6B 转移（`decision=TRANSITION reason=MAX_DURATION → activity.expired → activity.scheduled`）把虚拟活动落成 `ACT-20261009-028 reading`（`activity_type=virtual_life`、`related_task_id` 空、`parent_episode_id=ACT-027`）；它与计划条目/候选一致，候选带 `intent_id=INT-20261009-009 / initiative_fit=0.83`；全程没有 `force_transition`（这个 API 根本不存在） |
| Real QQ A：`你现在在干嘛？` | **PASS** | `08:24` 答「**看书呢，熬了一晚上没睡，脑子现在有点糊**」↔ 当前 Episode `reading`（不是未来活动） |
| Real QQ B：`你接下来准备干嘛？` | **PASS** | `08:24` 答「**补个觉吧，实在撑不住了**」= 未来安排（计划 ≠ 现状） |
| Real QQ C：`你最近想干嘛？` | **PASS** | `08:24` 答「**还能想啥，把图书馆屋顶搭完呗**」= 有效 LifeIntent（`INT-…-005`），**没有**声称已执行 |
| Real QQ D：`你自己去 Minecraft 玩玩？` | **PASS** | `08:25` 答「**啊——行吧，去搭两下屋顶，反正也睡不着**」= 虚拟兴趣 + 规划建议；`agent_tasks` / `tool_executions` 计数不变（没有 Task、没有确认、没有世界动作） |

### 15.1 真机发现（本轮新增）

1. **建议上限 3 太窄**：她同时挂着 4 条念头时，最低分的那条（当时正好是 `MINECRAFT_INTEREST`）
   会被整条挤出去 → 读取上限放宽到 **5**（**仍然有界**，与 7A 的记忆窗口同宽）并记了原因；
2. **任务占位时建议也必须失效**（§九）：只"不再提新的"不够 —— 已经提过的旧想法在任务期间
   继续给 Planner 加权就不叫"限制"了 → 新增 `HINT_BLOCKING_REASONS`（任务占位 / 待确认 / 睡着 /
   静默期 / 降级 / 社交疲劳 …）命中即**清空建议**；刻意**不含** MINECRAFT_OFFLINE（§三十七）
   与 RECENT_* / DUPLICATE（那些只是"现在别再提"）；
3. **计划项的归因要靠候选的 JSON 补回来**：`activity_plan_items` 没有 `intent_id` 列
   （不新增迁移），所以重载时从同一份计划的 `candidates` JSON 反查 —— 于是重启之后界面与 API
   仍然答得出"哪条意图影响了它"；
4. **WebUI 的只读视图可能被浏览器缓存**（同一 URL 的 GET 会命中缓存 → 卡片显示旧时间戳）。
   取证因此以 **DB + 日志**为准；这是可观测性上的一个小提示，不影响任何状态。

### 15.2 造点披露

* 只推 `activity_episodes` 的 `planned_end_at` / `started_at`（让世界 tick 真的做一次决策，
  **不改** `activity_name` / `status`）；删过取证期间自己产生的 `life_intents` 行以便再观察一次；
* 配置侧**临时**把 `logging.level` 调到 DEBUG 当证据通道，跑完已还原为 INFO（其它配置未动）；
* 真机用了**真实任务**（她真的连上服务器、真的挖了 `oak_log` 并捡回来 —— 那是 5A 任务链的既定行为）；
  Real D 的那次转移是 6B 自己的 `MAX_DURATION` 路径，不是构造出来的。
