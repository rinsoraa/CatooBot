# CatooBot Minecraft Phase 3B.1
## Cancellation Cleanup Integrity

## 目标

在不增加任何 Minecraft 新动作的情况下，修复 Action Runtime 的取消语义。

当前：

```text
TIMEOUT
→ cleanup()

CANCELLED
→ 不 cleanup()
```

必须改为：

```text
TIMEOUT
→ cleanup()

CANCELLED
→ cleanup()
```

原因：

未来 `move_to / follow / dig / place` 都可能有必须主动终止的底层状态。

例如：

```text
move_to
→ Pathfinder Goal

STOP
→ Action CANCELLED
→ 必须清除 Pathfinder Goal
```

不能出现：

```text
ActionRuntime = CANCELLED
Minecraft Bot = 继续移动
```

---

# 一、严格范围

只允许修改：

- `minecraft_runtime/action_runtime.js`
- `minecraft_runtime/test/action_runtime.test.js`
- 必要的 Phase 3B 文档

禁止新增：

- move_to
- follow
- pathfinder
- dig
- place
- attack
- craft
- Agent
- LLM Tool

---

# 二、Action Cleanup 语义

重新定义：

```text
cleanup()
```

含义：

> 当一个 Action 被强制终止后，负责清理该 Action 已经施加到 Minecraft Runtime 的底层状态。

需要执行 cleanup 的情况：

```text
TIMEOUT
CANCELLED(stop)
CANCELLED(disconnect)
CANCELLED(shutdown)
```

正常：

```text
SUCCEEDED
FAILED
```

不强制执行 cleanup，除非 Action 自己定义特殊需求。

---

# 三、修复 ActionCancelled 分支

当前逻辑：

```js
if (status === STATES.TIMEOUT && typeof def.cleanup === 'function') {
    def.cleanup(getBot())
}
```

改为：

```js
if (
    (status === STATES.TIMEOUT || status === STATES.CANCELLED) &&
    typeof def.cleanup === 'function'
) {
    try {
        def.cleanup(getBot())
    } catch (cleanupError) {
        log(...)
    }
}
```

要求：

- cleanup 异常不能让 Runtime 崩溃
- cleanup 异常不能改变原本的终态
- cleanup 仍然只能执行一次
- terminal event 仍然只能发一次

---

# 四、注意 runCancellable 的语义

不要误以为：

```text
runCancellable()
```

能够真正取消内部 Promise。

它只是让 ActionRuntime 停止等待该 Promise。

因此：

> 对未来所有有副作用的 Action，真正的终止必须由 Action 自己的 `cleanup()` 实现。

本阶段不要实现 Pathfinder，但必须把这个契约固定下来。

---

# 五、增加测试

必须增加：

## Test 1

`test_cancelled_action_runs_cleanup`

设计一个：

```text
slow_cleanup_action
```

运行后调用：

```text
runtime.stop()
```

验证：

```text
status == CANCELLED
cleanup_called == true
```

---

## Test 2

`test_disconnect_cancel_runs_cleanup`

执行：

```text
runtime.cancelAll("disconnect")
```

验证：

```text
CANCELLED
cleanup_called == true
```

---

## Test 3

`test_shutdown_cancel_runs_cleanup`

验证 shutdown reason 同样执行 cleanup。

---

## Test 4

`test_cancel_cleanup_error_does_not_break_runtime`

让 cleanup：

```js
throw new Error("cleanup boom")
```

验证：

```text
Action = CANCELLED
Runtime 仍可执行后续 Action
不会抛出 cleanup exception
不会重复终态事件
```

---

## Test 5

验证 cleanup 只执行一次：

```text
stop()
↓
cancelAll()
↓
timeout race
```

最终：

```text
cleanup_count == 1
terminal_event_count == 1
```

---

# 六、重点测试 race condition

必须覆盖：

```text
Action 正在 RUNNING
        ↓
stop()
        ↓
token.cancel()
        ↓
底层 promise 恰好同时 resolve
```

最终只能得到：

```text
CANCELLED
```

不能：

```text
CANCELLED + SUCCEEDED
```

也不能重复终态事件。

---

# 七、clearControlStates

保留当前：

```js
bot.clearControlStates()
```

它仍然是 Runtime 层的全局 Safety Stop。

但是不要把它视为替代 Action Cleanup。

未来：

```text
stop()
├── action.cleanup()
└── bot.clearControlStates()
```

两者都保留。

---

# 八、Regression

必须继续保持：

- Phase 1
- Phase 2
- Phase 2.1
- Phase 3A
- Phase 3B

全部测试通过。

至少：

```text
pytest
npm test
vitest
vue-tsc
Playwright
Node E2E
ruff
format
mypy
GitHub Actions
```

---

# 九、完成定义

必须满足：

```text
[ ] TIMEOUT → cleanup
[ ] CANCELLED → cleanup
[ ] disconnect → cleanup
[ ] shutdown → cleanup
[ ] cleanup exception 不破坏 Runtime
[ ] cleanup 不重复
[ ] terminal event 不重复
[ ] stop 继续幂等
[ ] clearControlStates 保留
[ ] 没有新增任何 Minecraft Action
[ ] 全量 CI 通过
```

最终标记：

PHASE 3B.1 COMPLETE
READY FOR PHASE 3C