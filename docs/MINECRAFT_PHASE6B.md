# Minecraft Phase 6B — Activity Decision Engine / Transition Guard

> 目标：让 Activity Runtime 在 Episode **接近结束 / 到期 / 被重大事件影响**时，
> 对当前 Episode 做出可审计的 **Continue / Extend / Transition** 决策。

```
World Clock
    ↓
Current ActivityEpisode
    ↓
Transition Guard（最短/最长时长、延长预算、撞车）
    ↓
Activity Decision Engine（规则优先）
    ├── CONTINUE
    ├── EXTEND
    └── TRANSITION
    ↓
ActivityRuntime（**只有它**能改生命周期）
```

**本阶段仍然不执行任何 Minecraft 动作**（§一）：决策只改 Episode 生命周期 + 产出"下一个活动提示"。
决策 → `minecraft_move_to` / `dig` / `TaskRuntime.confirm_and_start()` / 自动建 MEDIUM 任务 —— 全部禁止，
有 AST 级 guard 守着（§二五/§五二）。

---

## 1. Decision Engine（§三-§五/§二四）

`app/activity/decision.py::ActivityDecisionEngine` —— **确定性规则引擎**：没有 LLM、没有 `random`、
没有概率阈值（§三/§二一/§二三）。v1.0 §22 的 ``ActivityContinuationEvaluator`` 里"模型那一半"
（continue vs extend 的偏好、具体下一个活动）按 §121 的"规则优先"留给后续阶段，这里只留一个
``advisor`` 接口（**6B 永远是 None**）。

一次决策**只**产出（§四）：

```python
ActivityDecision(decision, reason_code, next_activity_hint, extension_seconds, confidence, trace_id)
```

`decision ∈ {CONTINUE, EXTEND, TRANSITION}` —— 没有 `EXECUTE` / `ACT` / `DO_TOOL`。

**流水线严格按 §二四 的顺序**（硬规则永远在"想换什么"之前）：

```
1 load current Episode        2 validate（一致性检查，只报不修）
3 now                         4 hard interruption（可突破最短时长）
5 min duration                6 max duration（hard rule：到上限必须换）
7 transition window（没进窗口 → 什么都不做）
8 窗口内但没到期 → 只立 transition_pending，**不切活动**
9 到期 → 延长护栏（预算 + 不越硬上限）→ EXTEND
10 否则 → 撞车护栏 → TRANSITION（含下一个活动提示）
11 trace  12 只有 ActivityRuntime 改生命周期  13 持久化  14 既有事件
```

## 2. Transition Window（§八/§十/§十一/§三六）

新增 `world.activity.transition_window_minutes`（默认 **5**）。语义与 v1.0 §15-§17 一致：

```
15:24  pending=False   （窗口外：连续 tick 什么都不做）
15:25  pending=True    （进窗口：只是**准备**，状态仍然 ACTIVE）
15:29  pending=True    （仍然 ACTIVE，没有新 Episode）
15:30  触发决策         （延长 / 换活动由护栏与 Planner 决定）
```

* 普通 tick 在窗口外**不选活动、不建 Episode、不写事件**（§十；有测试数着事件条数）；
* 窗口内也**绝不提前切**（§十一）；
* 一个 transition 里**最多一次决策**（§三七）。

## 3. Continue（§五/§十三）

`now < planned_end_at`（或窗口内还没到期）→ **CONTINUE**：不动 Episode、不建新 Episode。
`transition_pending` 是**派生/运行字段**，不是新的生命周期状态（§九：
`ActivityStatus` 仍然只有 7 个，没有 `TRANSITION_PENDING` / `DECIDING` / `WAITING_MODEL`）。

> 与 v1.0 §18 的措辞差异（记录在案）：v1.0 把"更新 planned_end_at"写在 Continue 里；
> **6B 按任务书 §五/§六 把两者拆开** —— CONTINUE 不动生命周期，只有 EXTEND 改
> `planned_end_at`。审计上更清楚：改没改时间一眼能看出来。

## 4. Extend（§六/§十六）

到期、且护栏允许 → `EXTEND`：`planned_end_at += extension`（默认 = 该活动的 typical 时长），
**绝不越过 `max_end_at`**（§十五）。

每次延长都必须留痕（§十六）：`extension_count` / 新的 `planned_end_at` / 原因都在 Episode 行上，
**延长的秒数进转移审计行**（`activity_transitions` 里 `EXTENDED` + `reason="TIME_EXPIRED +1200s"`）。
`max_extensions_per_episode`（默认 2）是硬上限，重启也不会被刷回来（§四十；有 SQLite 用例）。

## 5. Transition（§七/§二十）

只有"已经到期/到了硬上限 + 不能（或不该）再延长"才收尾并排下一个 —— 生命周期严格是：

```
current Episode → terminal → create next Episode → activate next
```

终态按原因区分：**到硬上限 → `EXPIRED`**（她用完了这条命）；窗口到期/撞车 → `COMPLETED`。
`ActivityDecision = TRANSITION` **不等于**"下一个立刻 active"。

## 6. Minimum / Maximum Duration（§十四/§十五）

| 护栏 | 规则 | 例外 |
| --- | --- | --- |
| 最短时长 | `elapsed < min_duration` → **CONTINUE**（哪怕 Planner 说该换了） | **硬中断**（任务开始/完成/失败/被打断、用户交互、恢复、手动）可以突破 |
| 最长时长 | `now >= max_end_at` → **必须 TRANSITION**（EXPIRED） | 无例外（hard rule；连"同名合并 → EXTEND"的捷径也不许走） |
| 延长 | 次数 ≤ `max_extensions_per_episode`，且 `planned_end_at ≤ max_end_at` | 到顶就 CONITNUE→TRANSITION |

## 7. Bounce Guard（§十七/§十八）

`ActivityBounceGuard`：**刚结束过的活动**在 `bounce_cooldown_minutes`（默认 10）内不许立刻回来
（`gaming → reading → gaming` 会被拒），另外当前 Episode 没稳定够 `minimum_stable_seconds` 也会被拒。
被拒之后按 §十八 二选一（**确定性**）：能延长就 EXTEND（原因记 `BOUNCE_GUARD`），否则换一个
**中性**活动（`idle`，夜里是 `napping`）。整个过程没有任何随机。

## 8. Merge（§十九）

* **优先 EXTEND**：她还在做同一件事时，决策流水线第 9 步直接延长 —— 不制造"结束 + 又开一个一样的"；
* 已经产生相邻同活动时，**展示层**合并（`merge_adjacent`）：API/WebUI 的时间线把相邻同名合成一段，
  `episode_ids` **一个都不丢**（原始审计永远在库里）；`status()["merged_timeline"]` 从旧到新给。

## 9. Consistency Checker（§二十/§二一）

`WorldConsistencyChecker`（v1.0 §65）：

| 规则 | 结果 |
| --- | --- |
| `started_at` 不在未来 | ERROR |
| `ended_at >= started_at` | ERROR |
| 每个角色最多一条 live primary Episode | ERROR |
| 当前 Episode 必须是"活着"的状态 | ERROR |
| 活动与位置明显冲突（`sleeping` + `kitchen` …） | **WARNING**（绝不静默吞掉） |
| 时长档必须 `0 < min ≤ typical ≤ max` | ERROR |

**只检测、只报告，绝不自动修**（§二一）：检查器没有 store、没有 fix/repair/delete 方法（有测试守着）；
发现异常时决策引擎如实给 `CONTINUE + NO_VALID_TRANSITION`，怎么修留给 Runtime 的 transition / recovery。

## 10. Decision Trace（§二二/§三九/§四八）

每次决策留一条结构化 trace（内存环形，最近 32 条）：

```
trace_id / episode_id / character_id / current_activity / trigger / decision / reason_code /
elapsed / planned_end_at / min_duration / typical_duration / max_duration / extension_count /
time_period / transition_pending / next_activity_hint / extension_seconds / guard_results / decided_at
```

**绝不保存隐藏思维链**（§二二 末句 / §四八 末句；有测试断言 trace 里没有
`chain_of_thought` / `thinking` / `reasoning`）。事件名**不增加**（§三九）：决策的**结果**仍然走 6A 已有的
`activity.extended` / `activity.completed` / … 生命周期事件，决策本身只写 trace + 日志。

## 11. Task Integration（§二六-§二八）

任务的优先级**完全沿用 6A 的那套适配器**，6B 不复制也不改写：

| 任务事件 | Episode |
| --- | --- |
| `task.started` / `task.resumed` | 任务型 Episode（`source=TASK`，只引用 `task_id`） |
| `task.paused` | **INTERRUPTED**（`reason=USER_INTERACTION`）——任务暂停时活动不能还 ACTIVE |
| 四个终态 | COMPLETED / INTERRUPTED / CANCELLED / EXPIRED（映射表只有一张：`model.TASK_STATE_OUTCOME`） |

任务到来时，决策引擎**不会**说"继续 gaming"：`TASK_STARTED` 属于**硬中断**，
直接 `TRANSITION`（`reason=TASK_STARTED`）——不过真正落地的路径是 6A 的适配器 `switch_to`。
暂停不会再产生第二个 Episode、也不会重复发 `activity.interrupted`（§二八：事件幂等，6A 的
`activity_transitions` partial unique index 兜底）。

## 12. Minecraft observation boundary（§四四）

照旧：观察**只**用于一致性判断与决策上下文（在线/附近玩家/位置/任务状态），
**绝不** `observation → action`。决策引擎连 `MinecraftService` 这个名字都不 import（AST guard）。

## 13. Security boundary（§二五/§五二）

AST 级 guard（`tests/test_activity_minecraft_adapter.py`）：`app/activity/**` 不许 import
`app.integrations` / `app.tools` / `app.ai` / `app.character` / `app.tasks`，代码里不许出现
`MinecraftService` / `ActionRuntime` / `TaskRuntime` / `ConfirmationStore` / `confirm_and_start` /
`consume` / `allow_medium` / `Policy`；`ActivityDecisionEngine` **没有** start/complete/extend 这类方法，
构造参数里也**没有** store —— 决策层改不了任何生命周期，更没有世界写权限。

## 14. Real QQ（§四三/§四六）

QQ 只能**读**：问「你现在在干嘛？」得到当前 Episode（6A 的上下文注入 + `QQ 任务控制` 仍走 5B 的 TaskRuntime）。
「你别玩了」这类"命令她停止生活"**不开放**（§四三）——6B 没有 QQ 强制管理活动这一层。

本轮真机**未执行**该条（见 §15 末尾）：QQ→活动上下文的读取路径由 6A 的实现与单测覆盖，
但"真机上问到当前 Episode"没有证据，按纪律记 `SKIPPED`。

## 15. Real Java（§四五 A–D）

| 真实门禁 | 要做的 | 证据（真机实测，均为只读观察） | 判定 |
| --- | --- | --- | --- |
| A | 真跑一个 Minecraft 任务 | `ACT-20261008-020 minecraft_task ACTIVE source=TASK task=task_0e414b8c92cf` | PASS |
| B | 任务运行中在 QQ 里说「暂停」 | `10:54:32` 日志**当场**出现 `018 activity.interrupted reason=USER_INTERACTION`，且 `10:54:33` 起新活动接管（中间**没有重启**）—— 实时路径 | PASS |
| C | 任务恢复 | `020 INTERRUPTED/USER_INTERACTION` → **`022 minecraft_task ACTIVE TASK_STARTED task=task_0e414b8c92cf parent=ACT-20261008-020`**；live Episode 始终只有一条（021 是她中途回到自己的 `out`，被任务恢复正常抢占为 `COMPLETED/TASK_STARTED`） | PASS |
| D | Episode 接近 `planned_end` | `019` 典型时长 1800s、窗口 5 分钟 → `planned_end=1791429872`、窗口在 `1791429572` 打开；日志 `11:19:33/34/35` 与 `11:19:57`（重启后）连续出现 `decision=CONTINUE reason=TRANSITION_WINDOW trigger=TIME_NEAR_END elapsed=1500s..1524s pending=True`，状态始终 `ACTIVE`，**没有任何世界动作** | PASS |

`019` 的完整生命周期（`activity_transitions`）与上面的算术互相印证：
`ACTIVE/SCHEDULED @1791428072`（10:54:32，暂停那一刻接管）→ `RECOVERED/RECOVERY @1791429597`
（11:19:57 重启；只记恢复、不伪造活动、状态保持 ACTIVE、窗口照旧 pending）→
`COMPLETED/TASK_STARTED @1791429826`（11:23:46 被恢复的任务正常抢占）。

**Real QQ（§四六）未执行**：6B 的 QQ 面是只读的，`你现在在干嘛？` 的取证留待下次真机轮次
（本次已由 B 间接证明 QQ→暂停链路可用，但"问她在干嘛并读到当前 Episode"这一条**没有证据**，
按纪律记 `SKIPPED`，绝不当作 PASS）。

观察方式（只读）：

```bash
# 决策视图（decision / reason / pending / elapsed / extensions）
.venv/Scripts/python.exe scripts/activity_smoke_real.py --phase decision
# 当前活动与最近 Episode（6A 的 report 阶段）
.venv/Scripts/python.exe scripts/activity_smoke_real.py --phase report
# 实时路径 / 窗口：直接看日志
grep -E "World.Activity" logs/catoobot.log | tail -20
```

## 16. Fast-forward simulation（§三三-§三五/§四九）

测试只用 `FakeClock.advance(...)` + `ActivityRuntime.advance()`（**绝不 sleep**）：

* **1 小时**（60 次 tick）→ ≤ 6 个 Episode、≤ 5 条完成事件；
* **3 天**（每 5 分钟一次 tick）→ 合理的 Episode 数量（远小于 tick 数的一半）；
* 普通 tick 是 O(1) 量级（只读当前 Episode + 少量历史）：50 次 tick < 2s（有性能用例）；
* 绝不做"每 tick 扫全表 / 查整个 Memory / 调 LLM"（§四九）。

## 16.1 真机发现（已修 / 已记）

* **决策日志一秒一条**（真机日志实测：最近 200 条里 197 条是逐 tick 的决策行）：平凡 CONTINUE
  （`BEFORE_END` / `MIN_DURATION_GUARD`）现在只在**状态签名变化时**记 INFO，其余降为 DEBUG ——
  决策本身仍然可在 trace/只读视图里查到。这是 §十/§四九 的精神（普通 tick 不该刷日志）。
* **窗口在真机上确实能观察到，但需要一条"能跑到窗口的长活动"**（已实测，见 §15 D）：
  沙盒自己的换活动（`WORLD_EVENT`）通常在 `planned_end - window` 之前就把活动换掉了。
  本次是一条 30 分钟（典型 1800s）的活动跑到窗口才取到证；来不及等的话，把
  `world.activity.transition_window_minutes` 临时调大（例如 60）就能立刻进入窗口 ——
  配置项本身就是为这种场合准备的。

## 17. Known limitations

1. **不接 LLM**（§三）：决策只有规则那一半；`advisor` 接口留空，模型辅助决策留给后续阶段。
2. **决策 trace 只在内存 + 日志**：可审计的**持久**事实是 Episode 行上的
   `extension_count` / `planned_end_at` / `transition_reason` 与转移审计行；重启后"上一次决策"的
   trace 不保留（§四十 要求的"不许重复 EXTEND"由持久计数保证，不靠 trace）。
3. **`transition_pending` 是派生字段**：由 `now` 与 `planned_end_at` 现算，不落盘（重启后自动重建，
   语义不变）。
4. **撞车护栏只按"活动名 + 冷却"判定**：不做语义相似（例如 `reading` 与 `online` 不算撞车）。
5. **相邻合并只在展示层**：库里仍然是两个 Episode（§十九 明确要求不丢原始审计）。
6. **QQ 不能强制改活动**（§四三）：只有任务控制走 TaskRuntime；"停止她的生活"不开放。
7. **6B 的专用 smoke 阶段未写**：真机取证目前用 6A 的 `--phase report` / `--phase pause` 加日志观察
   （见 §15 的观察方式）；后续可以把 6B 的 `decision` / `window` / `pause-live` / `resume` 四个阶段
   补进 `scripts/activity_smoke_real.py`。
8. **角色重置不覆盖活动表**：与 6A §十一 记的是同一件事（`CHARACTER_TABLES` 不含 `activity_episodes`
   / `activity_transitions`）。
