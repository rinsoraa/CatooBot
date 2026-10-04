# CatooBot v2.1 · Console World Log Change-Driven Refinement

## 任务目标

当前 Bot 启动后，PowerShell 每秒都会输出完全相同的世界状态：

```text
🌍 世界 │ 吃蛋糕（厨房） · home · 沙盒心跳（有点饿（很强烈）；想上网找人聊（很强烈）；想找点乐子（很强烈））
🌍 世界 │ 吃蛋糕（厨房） · home · 沙盒心跳（有点饿（很强烈）；想上网找人聊（很强烈）；想找点乐子（很强烈））
🌍 世界 │ 吃蛋糕（厨房） · home · 沙盒心跳（有点饿（很强烈）；想上网找人聊（很强烈）；想找点乐子（很强烈））
```

这影响终端日志阅读。

本次任务只解决：

> **终端世界日志刷屏问题。**

最终效果：

```text
第一次获得世界状态
→ 输出一次

后续世界状态没有发生“终端可见变化”
→ 不输出

发生真正可见变化
→ 输出一次最新世界状态
```

同时必须保证：

> **世界模拟、Sandbox tick、Need 演化、Goal、Action、Decision Opportunity、External Influence 等后台逻辑完全保持原有频率和语义。**

---

# 1. 先审计，不要直接修改

开始修改前，先定位当前：

```text
🌍 世界 │ ...
```

这条日志的实际产生位置。

搜索：

```text
🌍 世界
世界
沙盒心跳
heartbeat
sandbox heartbeat
tick
RuntimeScheduler
SandboxRuntime.tick
```

确认：

1. 哪个函数生成世界日志；
2. 哪个函数在每秒 tick 时调用它；
3. 日志到底来自 `RuntimeScheduler`、`SandboxRuntime`、trace/reporting，还是其他观察层；
4. 当前世界日志展示了哪些字段；
5. 是否已经存在 snapshot / state projection / observer / reporter 机制可以复用。

完成审计后再修改。

---

# 2. 核心原则：Tick 与 Console Output 必须解耦

当前可能类似：

```text
RuntimeScheduler
    ↓
每 1 秒 tick
    ↓
Sandbox.tick()
    ↓
print_world_state()
```

改成：

```text
RuntimeScheduler
    ↓
每 1 秒 tick
    ↓
Sandbox.tick()
    ↓
构造终端可见世界快照
    ↓
与上一次已输出快照比较
    ├── 相同 → 不输出
    └── 不同 → 输出一次
```

必须明确：

```text
tick frequency != console log frequency
```

---

# 3. 严禁降低世界 Tick 频率

不要为了减少日志而修改：

```text
runtime.tick_interval_seconds
RuntimeScheduler
Sandbox.tick()
actual elapsed time
```

Phase 14 / 14.1 已经确定：

```text
scheduler interval = 1 second
world advancement = actual elapsed time
```

必须继续保持。

禁止使用：

```python
if tick_count % 10 == 0:
    ...
```

或：

```python
sleep(10)
```

或任何“每 N 秒才更新世界”的方案。

本任务只改变：

```text
console reporting
```

而不是：

```text
world simulation
```

---

# 4. 不要简单用 world_revision 判断日志

禁止直接实现：

```python
if world_revision != last_world_revision:
    print_world()
```

原因：

当前架构已经明确：

- `world_revision` 是世界 mutation 的失效线；
- Need 的连续 drift 不一定产生 Mutation；
- 某些终端可见状态变化可能不直接对应 `world_revision`；
- 终端日志的需求是“显示内容发生变化”，不是“revision 发生变化”。

所以必须比较：

> **最终终端真正显示出来的语义状态。**

---

# 5. 创建 Console World Snapshot

增加一个轻量的只读终端投影，例如：

```python
@dataclass(frozen=True)
class ConsoleWorldSnapshot:
    action_id: str | None
    action_label: str | None
    location: str | None
    social_space: str | None
    visible_need_bands: tuple[str, ...]
```

实际字段以当前项目已有的世界日志内容为准，不要机械照抄。

核心要求：

### Snapshot 必须只包含“终端可见语义”

例如目前日志：

```text
🌍 世界 │ 吃蛋糕（厨房） · home · 沙盒心跳（有点饿（很强烈）；想上网找人聊（很强烈）；想找点乐子（很强烈））
```

那么至少应该能够区分：

```text
当前 Action
当前 Location
当前 Social Space
当前展示出来的 Need 状态
```

但不要把以下内容放进去：

```text
tick counter
scheduler timestamp
monotonic timestamp
elapsed internal bookkeeping
trace id
内部 float
revision bookkeeping
随机数状态
其他不可见内部字段
```

---

# 6. 必须比较“显示语义”，不是整个 Sandbox

错误方案：

```python
json.dumps(entire_sandbox_state)
```

然后比较 JSON。

这样会因为内部各种无关字段持续变化导致重新打印。

正确模型：

```text
Sandbox
   ↓
build_console_world_snapshot()
   ↓
ConsoleWorldSnapshot
   ↓
equality comparison
   ↓
Console Reporter
```

终端日志只由这个 projection 决定。

---

# 7. Need 的比较方式

当前日志显示：

```text
有点饿（很强烈）
想上网找人聊（很强烈）
想找点乐子（很强烈）
```

假设内部 Need：

```text
0.81
0.82
0.83
0.84
```

但是显示文本一直都是：

```text
很强烈
```

则：

```text
不输出
```

不要因为内部数值每秒变化就刷屏。

反过来，如果：

```text
很强烈
↓
非常强烈
```

并且这是当前终端真正显示的状态变化，则：

```text
输出一次
```

即：

> 比较“终端显示值”，不是比较内部连续数值。

---

# 8. 首次启动必须输出一次

启动完成后：

```text
last_console_world_snapshot = None
```

第一次获得有效世界状态：

```text
current_snapshot != None
```

必须输出：

```text
🌍 世界 │ ...
```

然后：

```text
last_console_world_snapshot = current_snapshot
```

---

# 9. 后续无变化时完全静默

例如：

```text
tick 1
世界 = 吃蛋糕 / 厨房 / home / 很强烈
→ 输出

tick 2
世界 = 吃蛋糕 / 厨房 / home / 很强烈
→ 不输出

tick 3
世界 = 吃蛋糕 / 厨房 / home / 很强烈
→ 不输出

...

tick 60
世界 = 吃蛋糕 / 厨房 / home / 很强烈
→ 不输出
```

即：

```text
60 次 tick
≈ 0 次重复世界日志
```

---

# 10. 发生状态变化时立即输出一次

例如：

```text
吃蛋糕（厨房）
↓
打开电脑（卧室）
```

则：

```text
🌍 世界 │ 打开电脑（卧室） · home · ...
```

输出一次。

随后继续保持安静，直到下一次可见状态变化。

---

# 11. “沙盒心跳”不得再作为每秒日志依据

当前的：

```text
· 沙盒心跳（……）
```

很可能只是因为 Scheduler 每秒执行而被重复打印。

必须检查其语义。

如果“沙盒心跳”只是：

> Scheduler 每秒运行一次的运行时心跳

则不要每秒把它作为世界日志打印出来。

推荐改成：

```text
世界状态变化
    ↓
世界日志输出
```

而不是：

```text
Scheduler heartbeat
    ↓
世界日志输出
```

如项目本身已经存在独立 heartbeat / trace 日志机制，可以保留，但不要把它混入每秒的 `🌍 世界` 状态报告。

---

# 12. 不要删除其他重要日志

本次任务不是“减少所有日志”。

现有真正事件型日志应该继续存在，例如：

```text
💬 QQ
🧠 决策
⚡ 打断
▶ 恢复
❤️ 关系
📌 承诺
```

实际项目已有哪些类别就继续保留。

只修改：

```text
🌍 世界
```

这一类重复状态日志。

---

# 13. 不要新建任何核心架构

本次任务属于：

```text
Observability / Console Reporting
```

禁止借机新增：

```text
Social System
Planner
Agent
Emotion System
Memory System
Relationship System
Conversation DB
EventBus
Scheduler
World System
新的 Persistence
```

也禁止改变：

```text
Sandbox truth
Goal semantics
ActionInstance semantics
NeedSystem semantics
ExternalInfluenceEvaluator
ConversationRuntime
OneBot architecture
```

尽量只在当前已有：

```text
logger / trace / observer / reporter / runtime
```

链路中做最小修改。

---

# 14. 日志状态无需持久化

例如：

```python
last_console_world_snapshot
```

可以是 runtime 内存状态。

不需要：

```text
DB
continuity snapshot
memory
sandbox persistence
```

重启之后：

```text
last_console_world_snapshot = None
```

然后首次世界状态重新输出一次即可。

这是正常行为。

---

# 15. 并发 / 一致性

当前：

```text
RuntimeScheduler
```

与外部事件已经共享 world lock。

获取 Console Snapshot 时，必须使用现有安全读取路径。

不能出现：

```text
tick 正在修改状态
↓
console reporter 读取了一半
↓
Action 是旧的
Location 是新的
Need 是更早的
↓
输出错误组合
```

优先复用现有：

```text
world lock
read-only snapshot
runtime state view
```

不要再发明一套新的并发模型。

---

# 16. 测试

新增针对 Console World Reporter 的测试。

至少覆盖：

### Test A：连续相同状态

执行 10 次 tick：

```text
可见状态完全一致
```

期望：

```text
世界日志只输出 1 次
```

---

### Test B：Action 改变

```text
tick 1 → 吃蛋糕
tick 2 → 吃蛋糕
tick 3 → 吃蛋糕
tick 4 → 打开电脑
tick 5 → 打开电脑
```

期望：

```text
输出：
吃蛋糕
打开电脑
```

总计 2 次。

---

### Test C：内部 Need float 变化

```text
0.81
0.82
0.83
0.84
```

但显示 band 永远是：

```text
很强烈
```

期望：

```text
不重复输出
```

---

### Test D：Need band 变化

```text
很强烈
↓
非常强烈
```

期望：

```text
输出一次
```

---

### Test E：无关内部字段变化

以下变化不得触发输出：

```text
tick counter
timestamp
elapsed time
trace id
internal bookkeeping
revision bookkeeping
```

---

### Test F：重启

新建 Runtime：

```text
last_console_world_snapshot = None
```

首次获得世界状态：

```text
输出一次
```

---

### Test G：外部事件导致可见变化

模拟：

```text
OneBot Event
→ Sandbox
→ Action / Location / Social state changed
```

期望：

```text
输出一次最新世界状态
```

---

### Test H：长时间稳定运行

连续：

```text
20~60 次 scheduler tick
```

世界可见状态不变化。

期望：

```text
🌍 世界输出 <= 1 次
```

同时确认：

```text
Scheduler ticks 仍然正常执行
Sandbox 仍然正常推进
```

---

# 17. 必须证明“日志少了，但世界没变慢”

测试中必须明确区分：

```text
scheduler tick count
```

与：

```text
console world output count
```

例如：

```text
scheduler ticks = 60
console world logs = 1
```

这是成功的结果，而不是：

```text
scheduler ticks = 1
console world logs = 1
```

后者属于错误实现。

---

# 18. 完整回归

完成后运行项目原有全部检查：

```bash
pytest
ruff check .
ruff format --check .
mypy ...
```

再确认 Phase 14、14.1、15、16 全部通过。

尤其不能因为此次修改破坏：

```text
RuntimeScheduler
World Tick Unification
Autonomous Behavior Stability
Live Social Influence
```

---

# 19. 人工验收

启动 Bot。

期望看到：

```text
🌍 世界 │ 吃蛋糕（厨房） · home · 沙盒心跳（……）
```

之后如果状态没有变化：

```text
[终端保持安静]
```

当角色真正发生可见状态变化：

```text
🌍 世界 │ 打开电脑（卧室） · home · ...
```

再继续保持安静。

不要再出现：

```text
🌍 世界 │ ...
🌍 世界 │ ...
🌍 世界 │ ...
🌍 世界 │ ...
```

---

# 20. 最终报告

完成后请报告：

1. 原始刷屏日志的调用来源；
2. 修改了哪些文件；
3. ConsoleWorldSnapshot 最终包含哪些字段；
4. 为什么没有直接使用 `world_revision`；
5. RuntimeScheduler 是否仍保持 1 秒；
6. Sandbox / Need / Goal / Action 是否完全保持原语义；
7. 新增了多少测试；
8. 完整测试结果；
9. CI 结果；
10. commit SHA。

最终明确说明：

> **本次修改只降低终端日志噪声，没有降低世界模拟频率。**

完成本任务后停止，不进入 Phase 17，也不要扩展任何新的角色能力。