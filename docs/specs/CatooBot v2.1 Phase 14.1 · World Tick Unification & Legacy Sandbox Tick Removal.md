# CatooBot v2.1 Phase 14.1 · World Tick Unification & Legacy Sandbox Tick Removal

## 0. 基线

当前：

```text
HEAD: 607ddb3de922d4333fd3fe79767cc7f280d9051c
Phase: 14
Tests: 1380 passed
CI: success
```

本阶段不是 Phase 15。

本轮正式确认并落实一个架构决定：

> **彻底取消历史 Sandbox “每 10 分钟刷新一次”的 World Tick 机制。**

从本阶段开始：

> **RuntimeScheduler 每 1 秒是唯一的 World Tick 驱动。**

---

# 1. 最终时间模型

今后 CatooBot 只有：

```text
RuntimeScheduler
    ↓
每 1 秒唤醒一次
    ↓
计算实际经过的 real elapsed time
    ↓
Sandbox.tick(minutes=elapsed_minutes)
```

因此：

```text
现实 1 秒
→ 世界时间约前进 1 秒

现实 10 秒
→ 世界时间约前进 10 秒

现实 10 分钟
→ 世界时间约前进 10 分钟
```

注意：

```text
1 秒 scheduler interval
≠
1 分钟世界时间
```

世界推进量必须来自：

```text
actual elapsed time
```

而不是固定 tick step。

---

# 2. 正式废弃旧的“每 10 分钟 Sandbox Tick”

历史机制：

```text
sandbox_tick
interval = sandbox.tick_seconds
sandbox.tick()
```

本轮必须彻底移除。

尤其是当前默认：

```text
sandbox.tick_seconds = 600
```

代表的旧：

```text
每 600 秒触发一次 Sandbox Tick
```

不再作为 Runtime 世界时间推进机制。

---

# 3. 不允许保留双 Tick

最终必须满足：

```text
SandboxRuntime.tick()
```

在真实 Bot Runtime 中只有：

```text
1 个 owner
```

即：

```text
RuntimeScheduler
```

禁止：

```text
RuntimeScheduler
+
shared scheduler sandbox_tick
```

同时存在。

---

# 4. 不保留旧 fallback

与上一版 14.1 Prompt 不同：

本轮**不需要**：

```text
runtime.enabled=false
→ legacy sandbox_tick
```

因为用户已经明确决定：

> 取消旧的 10 分钟刷新机制。

因此：

```text
legacy sandbox_tick
```

必须彻底退出生产运行路径。

---

# 5. RuntimeScheduler 成为唯一 World Tick Authority

职责：

```text
RuntimeScheduler
    = world clock advancement
    = autonomous life evaluation trigger
```

已有 shared scheduler：

```text
shared scheduler
    = memory / cleanup / behavior / feedback / other jobs
```

但：

```text
shared scheduler
```

不再拥有：

```text SandboxRuntime.tick()
```

调用权。

---

# 6. 删除旧 sandbox_tick 注册

审计：

```text
app/core/bot.py
```

找到类似：

```python
ScheduledJob(
    name="sandbox_tick",
    handler=sandbox.tick,
    ...
)
```

将其从生产启动流程中移除。

不是：

```text disabled by config
```

而是：

```text no longer registered
```

---

# 7. 不需要创建新的 Tick System

继续使用：

```text RuntimeScheduler
```

禁止新增：

```text WorldScheduler
TickScheduler2
SandboxScheduler
LifeScheduler
```

---

# 8. `sandbox.tick_seconds` 的处理

必须先全仓库审计：

```text sandbox.tick_seconds
```

的所有使用位置。

分类：

### A. 如果它只用于历史：

```text
每 10 分钟执行一次 Sandbox Tick
```

那么：

```text 删除 / 废弃该配置语义
```

不应该继续作为世界时间驱动配置存在。

### B. 如果它还有其他独立用途

必须：

```text 保留用途
```

但：

```text 禁止它再次驱动 SandboxRuntime.tick()
```

不要因为删除旧 scheduler 就留下一个让人误以为“世界每 10 分钟推进”的配置。

---

# 9. 配置语义重新明确

Phase 14 最终只需要：

```text
runtime.tick_interval_seconds
```

表示：

> Runtime Scheduler 多久检查一次。

默认：

```text
1
```

而不是：

> 世界每多久前进一次。

---

# 10. World Advancement 不使用固定 step

禁止：

```python
sandbox.tick()
```

作为 Runtime 的正常 World Tick。

必须：

```python
sandbox.tick(minutes=elapsed_seconds / 60.0)
```

或等价语义。

---

# 11. 不能再走 `_tick_minutes()` 默认步长

如果：

```text
SandboxRuntime.tick()
```

在没有参数时会使用：

```text
_tick_minutes()
```

且该函数存在：

```text
minimum = 1 minute
```

之类逻辑：

那么生产 RuntimeScheduler 路径必须：

```text 显式传入 actual elapsed time
```

不要让 1 秒 scheduler 最终被解释成 1 分钟世界时间。

---

# 12. 这是本轮最重要的 Regression

必须测试：

```text 现实经过 10 秒
```

结果：

```text 世界约前进 10 秒
```

不能：

```text 世界前进 10 分钟
```

也不能：

```text 世界前进 10 秒 + legacy 额外 1 分钟
```

---

# 13. Full Bot Tick Count Test

必须使用：

```text FakeClock
```

启动：

```text Full Bot Runtime
RuntimeScheduler
```

确保：

```text 只有 RuntimeScheduler
```

负责：

```text SandboxRuntime.tick()
```

---

# 14. Full Bot World Advancement Test

模拟：

```text FakeClock.advance(10)
```

再运行 scheduler。

检查 Sandbox world time：

```text +10 seconds
```

允许有：

```text tiny scheduling tolerance
```

但不能：

```text +600 seconds
```

---

# 15. No Legacy Tick Test

启动完整：

```text Bot
```

检查 shared scheduler：

```text no job named "sandbox_tick"
```

并且：

```text RuntimeScheduler running == true
```

---

# 16. No Double Tick Test

FakeClock：

```text advance(600 seconds)
```

运行完整 Runtime。

检查：

```text SandboxRuntime.tick call count
```

只允许来自：

```text RuntimeScheduler
```

不能出现：

```text RuntimeScheduler call
+
legacy scheduler call
```

---

# 17. Action Regression

使用一个会因为时间推进而触发的 Action。

例如：

```text t=0
Action inactive

advance 10 sec

Action should only advance by 10 sec
```

不能突然：

```text advance 1 minute
```

---

# 18. Commitment Regression

构造：

```text Commitment due_at
```

模拟：

```text advance exact amount
```

检查：

```text activation / expiration
```

完全按照实际世界时间运行。

不能因为旧 10 分钟 tick 被额外执行而：

```text early activate
early expire
```

---

# 19. Goal Regression

同理：

```text Goal timing
```

必须基于：

```text actual world elapsed
```

而不是：

```text legacy fixed 10-minute tick
```

---

# 20. Experience Regression

Action 完成后：

```text one ActionInstance
→ one Experience
```

不能由于 double tick：

```text duplicate completion
duplicate Experience
```

---

# 21. Memory Regression

同样：

```text Experience
→ Memory
```

不能因为重复 tick：

```text duplicate social memory
```

Phase 10.1 / 10.2 的 identity semantics 必须保持。

---

# 22. Commitment Expiration

模拟：

```text due_at + grace
```

之后推进实际时间。

恢复：

```text broken / expired
```

必须只发生一次。

---

# 23. Catch-up 机制保持

Phase 14 已经存在：

```text max_catchup_seconds = 300
```

这一语义继续保留。

重启后：

```text elapsed downtime
```

仍然采用：

```text 有界 catch-up
```

而不是重新生成：

```text 每秒 tick
```

---

# 24. Catch-up 也不使用 legacy tick

禁止：

```text downtime
↓
legacy sandbox_tick
↓
1 分钟 step
```

所有 recovery 必须通过：

```text RuntimeScheduler
+ explicit elapsed time
```

完成。

---

# 25. FakeClock 是唯一测试时间控制方式

绝大多数测试：

```text 不使用 sleep
不等待真实时间
```

使用：

```text Clock.now()
Clock.advance()
```

---

# 26. Scheduler Interval 与 World Time 必须彻底分离

最终概念：

```text runtime.tick_interval_seconds
    = scheduler wakeup frequency

elapsed_seconds
    = actual world time progression
```

两者绝不能混淆。

---

# 27. 不允许改变 Sandbox tick semantics

如果现有：

```text SandboxRuntime.tick(minutes=...)
```

已经正确支持：

```text elapsed time
```

保持不动。

本轮主要修：

```text caller / ownership
```

而不是重新设计：

```text Sandbox tick internals
```

---

# 28. 继续保留世界锁

Phase 14 已建立：

```text tick()
+
wakeup()
```

共享：

```text world lock
```

继续保留。

不要因为删除 legacy scheduler 而删除世界锁。

---

# 29. Tick 与 QQ Event

最终：

```text QQ Message
+
RuntimeScheduler Tick
```

都进入：

```text same Sandbox serialization boundary
```

防止：

```text half-state
```

---

# 30. Scheduler Responsibility

最终完整结构：

```text
                    Runtime
                       │
          ┌────────────┴────────────┐
          ↓                         ↓
 RuntimeScheduler              OneBot Gateway
          ↓                         ↓
   World Tick                    External Event
          ↓                         ↓
          └────────────┬────────────┘
                       ↓
                    Sandbox
```

两个入口共享：

```text world lock
```

但只有：

```text RuntimeScheduler
```

推进：

```text world time
```

---

# 31. Shared Scheduler Responsibility

现有 shared scheduler 可以继续运行：

```text memory consolidation
cleanup
feedback
maintenance
```

前提：

```text 不调用 SandboxRuntime.tick()
```

---

# 32. No legacy compatibility mode

不新增：

```text legacy_tick_enabled
legacy_sandbox_tick
sandbox_tick_interval
```

这样的兼容配置。

用户已经明确取消旧的十分钟刷新机制。

---

# 33. Configuration Cleanup

如果：

```text sandbox.tick_seconds
```

已经只用于 legacy tick：

请：

```text 删除 example config
删除 runtime 使用
删除相关 dead code
```

如果历史代码还有其他用途：

必须：

```text 明确重命名/重新定义
```

使其不再代表：

```text Sandbox world refresh interval
```

---

# 34. Documentation / Comments

删除或修改任何：

```text “每 10 分钟 Sandbox 刷新”
“sandbox tick interval drives world”
```

这类过时注释。

统一说明：

> RuntimeScheduler 每秒检查一次，以实际经过时间推进世界。

---

# 35. Startup

完整 Bot：

```text load Sandbox
↓
restore world
↓
start RuntimeScheduler
↓
start OneBot Gateway
```

shared scheduler 可以按现有顺序启动。

但：

```text 不再注册 sandbox_tick
```

---

# 36. Shutdown

保持 Phase 14：

```text stop RuntimeScheduler
↓
wait / cancel according to timeout
```

shared scheduler：

```text stop
```

二者不重复处理 Sandbox Tick。

---

# 37. Start Idempotency

调用：

```text start()
start()
```

仍然只能有：

```text one RuntimeScheduler task
```

---

# 38. Stop / Start

```text start
stop
start
```

必须：

```text exactly one RuntimeScheduler
```

不能：

```text duplicate world tick loop
```

---

# 39. Long Run

至少：

```text FakeClock advance 10h
```

检查：

```text world elapsed ≈ 10h
```

没有：

```text extra 10-minute chunks
```

没有：

```text duplicate actions
duplicate goals
duplicate experiences
```

---

# 40. Real Clock Smoke

允许短时间：

```text 2–3 seconds
```

验证：

```text RuntimeScheduler
```

确实持续 tick。

不要依赖长时间墙钟测试。

---

# 41. LLM Budget

保持：

```text ticks >> LLM calls
```

例如：

```text 100 ticks
LLM calls < 10
```

不能：

```text one LLM call every second
```

---

# 42. LLM Failure

继续：

```text decision unavailable
→ world remains valid
→ scheduler continues
```

不无限 retry。

---

# 43. No proactive QQ

继续：

```text autonomous tick
≠
send QQ
```

---

# 44. No new Scheduler

禁止：

```text WorldScheduler
LifeScheduler
TickScheduler2
```

---

# 45. No new DB

本轮：

```text no migration
```

---

# 46. No Phase 15

完成后停止。

---

# 47. Tests baseline

当前：

```text 1380 passed
```

要求：

```text all 1380 preserved
+
Phase 14.1 tests
```

至少新增：

```text 1. no legacy sandbox_tick registration
2. RuntimeScheduler sole world tick owner
3. real elapsed time advancement
4. 10-second ≠ 10-minute regression
5. 600-second advancement exactly once
6. no duplicate Action
7. no duplicate Goal
8. no duplicate Experience
9. no duplicate Memory
10. Commitment timing correctness
11. full Bot startup wiring
12. runtime restart still one tick owner
```

---

# 48. Quality Gate

必须：

```text ruff
ruff format
mypy
pytest
CI success
```

并：

```text HEAD == origin/main
working tree clean
```

---

# 49. Final Report

```text
# CatooBot v2.1 Phase 14.1 Report · World Tick Unification & Legacy Sandbox Tick Removal

Phase 14.1 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## World Tick

authoritative owner:
RuntimeScheduler:
legacy sandbox_tick:
world tick count:

## Time Semantics

scheduler interval:
actual elapsed:
world advancement:
10-second regression:
600-second regression:

## Full Bot Wiring

startup:
scheduler jobs:
duplicate tick prevention:

## Autonomous Life

Goal:
Action:
ActionInstance:
Commitment:
Experience:
Memory:

## Catch-up

max catch-up:
downtime recovery:
per-second replay: No

## Restart

tick owner after restart:
duplicate scheduler: No

## Isolation

character:
clock:
sandbox:

## Regression

Phase 8:
Phase 8.1:
Phase 9:
Phase 9.1:
Phase 9.1.1:
Phase 10:
Phase 10.1:
Phase 10.2:
Phase 11:
Phase 12:
Phase 12.1:
Phase 13:
Phase 13.1:
Phase 14:

## Architecture

new scheduler: No
new World system: No
new Life system: No
legacy 10-minute world tick: Removed
new DB: No
new Planner: No
new Agent: No
new Memory system: No
new Relationship system: No
new Experience system: No
OneBot semantics changed: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Final

Phase 14.1 complete: Yes / No
Phase 15 entered: No
```

这次我建议**明确把“十分钟刷新”永久废弃，而不是做兼容开关**。从现在开始，时间模型就统一成：

```text
RuntimeScheduler
每 1 秒检查一次
        ↓
计算真实经过时间
        ↓
Sandbox 世界推进真实经过的时间
```

这样以后你看到：

```text
runtime.tick_interval_seconds = 1
```

就明确知道它是**调度频率**，而不是“角色一分钟过多久”的时间倍率。

这也会让后续的 **Phase 15** 简单很多：它可以直接假定 `Sandbox` 已经拥有一个稳定、连续、唯一的世界时钟，而不需要同时理解“旧十分钟 Tick”和“新一秒 Tick”两套时间模型。