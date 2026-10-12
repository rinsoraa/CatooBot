基于当前仓库最新提交 `5487d73` 开始 Phase 4H.1。

这是一个**基础设施可靠性修复阶段**，不是新 Minecraft 能力。

先完整阅读：

- `minecraft_runtime/runtime.js`
- `minecraft_runtime/action_runtime.js`
- `minecraft_runtime/test/move_to.test.js`
- `minecraft_runtime/test/e2e.js`
- `minecraft_runtime/test/smoke_real_server.js`
- 当前 `move_to` 的 ActionRuntime 生命周期
- 当前 `follow_player`
- 当前 `pickup_item`
- 当前 Pathfinder 依赖版本与 lockfile

---

# 一、问题已经被真实服务器确认

当前真实服务器发现：

```text
远距离 move_to
→ HTTP RUNNING
→ Pathfinder goto() 很快 resolve
→ ActionRuntime SUCCEEDED
→ 但 final_position 距离目标约 28 格
```

这是 production false-success，不是 Smoke 测试问题。

当前仓库：

```text
minecraft_runtime/package-lock.json
→ mineflayer-pathfinder = 2.4.5
```

当前 `move_to`：

```js
await bot.pathfinder.goto(state.goal)
```

然后 Promise resolve 后直接：

```text
SUCCEEDED
```

当前 Pathfinder 2.4.5 `lib/goto.js` 的实现存在：

```js
if (results.path.length === 0) {
    cleanup()
} else if (results.status === 'noPath') {
    cleanup(error('NoPath', ...))
}
```

因此 `path.length === 0` 时会直接 resolve，可能把未到达目标误报成成功。

**不要把这个问题继续留给 smoke。必须修生产 runtime。**

---

# 二、修复原则

必须建立一个新的硬契约：

> `minecraft_move_to` 的 SUCCEEDED 不能由 `pathfinder.goto()` Promise resolve 单独决定。

只有：

```text
Pathfinder 完成
+
CatooBot 独立验证当前 bot 位置确实满足 GoalNear
```

才能：

```text
SUCCEEDED
```

否则：

```text
FAILED
```

---

# 三、不要升级 Pathfinder 作为本阶段主要修复

虽然上游存在针对 goto 无路径错误行为的修复工作，但当前 CatooBot 明确锁定 `2.4.5`，本阶段不要因为这个问题顺手升级：

```text
mineflayer
mineflayer-pathfinder
minecraft-data
```

不要扩大依赖变更面。

我们应该让：

```text
CatooBot move_to
```

对底层 Pathfinder 的这个缺陷具备自己的防御层。

未来可以单独建立 dependency upgrade 阶段。

---

# 四、禁止继续把 goto() resolve 当最终成功条件

当前：

```js
await bot.pathfinder.goto(state.goal)
```

可以继续作为导航驱动的一部分，

但它返回后必须立即执行：

```text
verifyMoveToReached()
```

不能：

```text
goto resolve
→ return success
```

---

# 五、Move_to 最终成功判据

当前：

```text
MOVE_TO_RADIUS = 1.5
```

保持不变。

最终实际位置：

```text
bot.entity.position
```

目标：

```text
target = new Vec3(params.x, params.y, params.z)
```

计算：

```text
distance = position.distanceTo(target)
```

只有：

```text
distance <= MOVE_TO_RADIUS
```

才允许：

```text
SUCCEEDED
```

否则：

```text
FAILED
code = path.not_found
```

或者使用当前项目最合适的结构化错误码。

推荐：

```text
path.not_reached
```

但先检查当前错误码体系。

不要为了方便继续把它伪装成：

```text
action.failed
```

如果已有：

```text
path.not_found
```

可以区分：

```text
path.not_found
path.not_reached
```

其中：

- `path.not_found` = 明确无路径
- `path.not_reached` = Pathfinder 返回结束/resolve，但最终位置不满足目标

---

# 六、优先绕开 2.4.5 goto 的空路径 bug

更推荐把 `move_to.wait()` 改成 CatooBot 自己监听 Pathfinder 生命周期，而不是继续：

```js
await bot.pathfinder.goto(...)
```

建议：

```text
开始监听：
    goal_reached
    path_update
    goal_updated
    path_stop

然后：
    bot.pathfinder.setGoal(goal)
```

因为当前 Pathfinder README 明确有：

```text
goal_reached
path_update
goal_updated
path_stop
```

并且 `path_update` 会提供：

```text
success
partial
timeout
noPath
```

([GitHub](https://github.com/PrismarineJS/mineflayer-pathfinder/blob/master/readme.md))

这样可以让 CatooBot 自己定义：

```text
什么时候真正算成功
```

而不是接受 2.4.5 `goto()` 的错误 resolve 语义。

---

# 七、监听器顺序

必须：

```text
注册 listener
↓
setGoal(goal)
```

不要反过来。

防止：

```text
setGoal()
→ goal_reached
→ listener 尚未注册
```

造成丢事件。

---

# 八、GoalReached 也必须二次验证

即使收到：

```text
goal_reached
```

也不能直接：

```text
SUCCEEDED
```

必须：

```text
position = bot.entity.position
distance = position.distanceTo(target)
```

再次验证：

```text
distance <= MOVE_TO_RADIUS
```

否则：

```text
path.not_reached
```

---

# 九、Path_update 行为

对：

```text
path_update
```

逐一处理。

### `status = 'noPath'`

立即：

```text
FAILED
code = path.not_found
```

但前提：

```text
path.length > 0
```

不要复制 Pathfinder 2.4.5 那个错误顺序。

### `status = 'timeout'`

继续：

```text
FAILED
code = path.not_found
```

或当前项目统一 timeout mapping。

### `status = 'partial'`

不能失败。

继续等待。

### `status = 'success'`

不能立即成功。

继续等待：

```text
goal_reached
```

或者满足独立位置验证条件。

---

# 十、空路径是重点

如果收到：

```text
path_update
{
    path: []
    status: 'success'
}
```

绝对不能：

```text
resolve
```

先检查：

```text
current distance <= MOVE_TO_RADIUS
```

### 如果满足

可以认为：

```text
target already reached
```

→ SUCCEEDED

### 如果不满足

必须：

```text
FAILED
path.not_reached
```

这就是本阶段最重要的回归测试。

---

# 十一、Path_update 空路径 + noPath

如果：

```text
path = []
status = noPath
```

必须：

```text
path.not_found
```

不能：

```text
SUCCEEDED
```

---

# 十二、Path_update partial + empty

如果出现：

```text
path = []
status = partial
```

不要立即失败。

继续等待后续事件。

但必须有整体：

```text
MOVE_TIMEOUT_MS
```

防止无限等待。

---

# 十三、Path_update timeout

如果：

```text
status = timeout
```

进入：

```text
FAILED
```

并清 Goal。

不能：

```text
SUCCEEDED
```

---

# 十四、GoalChanged

如果：

```text
goal_updated
```

发现：

```text
newGoal !== state.goal
```

立即：

```text
FAILED
code = goal.changed
```

不过当前 ActionRuntime 已经 exclusive，不允许正常外部覆盖。

这个分支仍然保留，作为 runtime 防御。

---

# 十五、PathStop

如果：

```text
path_stop
```

且 token 没有触发：

```text
CANCELLED
```

说明底层导航自己停止。

必须：

```text
verify current distance
```

如果：

```text
distance <= radius
```

→ SUCCESS

否则：

```text
FAILED
path.not_reached
```

不要直接：

```text
SUCCEEDED
```

---

# 十六、STOP 行为必须保持现有契约

用户主动：

```text
POST /minecraft/stop
```

应该：

```text
cleanup
→ setGoal(null)
→ clearControlStates
→ CANCELLED
```

不能：

```text
path_stop
→ FAILED
```

覆盖掉主动取消。

因此 ActionRuntime token 是最高优先级。

处理事件时：

```text
if token.cancelled
    ignore normal completion
```

---

# 十七、Timeout

`MOVE_TIMEOUT_MS = 30000`

保持。

如果 30 秒到：

```text
TIMEOUT
```

然后：

```text
cleanup
→ setGoal(null)
→ clearControlStates
```

不能把 timeout 之后 Pathfinder 晚到的：

```text
goal_reached
```

当成：

```text
SUCCEEDED
```

---

# 十八、Race Protection

至少覆盖：

```text
goal_reached
+
STOP
```

同时发生。

最终只能：

```text
CANCELLED
```

或者：

```text
SUCCEEDED
```

不能双终态。

同样：

```text
path_update noPath
+
goal_reached
```

只能一个终态。

以及：

```text
timeout
+
goal_reached
```

只能：

```text
TIMEOUT
```

---

# 十九、成功结果

成功后继续返回：

```json
{
  "target": {
    "x": ...,
    "y": ...,
    "z": ...
  },
  "final_position": {
    "x": ...,
    "y": ...,
    "z": ...
  },
  "distance_to_target": ...
}
```

但这里的：

```text
distance_to_target
```

必须来自**最终成功判定瞬间**重新读取的实际位置。

不能使用：

```text
pathfinder
```

内部预测位置。

---

# 二十、失败结果

建议：

```json
{
  "code": "path.not_reached",
  "detail": {
    "target": {
      "x": ...,
      "y": ...,
      "z": ...
    },
    "actual": {
      "x": ...,
      "y": ...,
      "z": ...
    },
    "distance_to_target": 28.13,
    "radius": 1.5
  }
}
```

这样以后 LLM / smoke / 调试页面都知道：

> Pathfinder 结束了，但机器人实际上没有到。

---

# 二十一、不要用“移动距离”判成功

不能：

```text
移动了 10 格
→ 成功
```

也不能：

```text
位置改变
→ 成功
```

唯一硬门禁：

```text
distance_to_target <= MOVE_TO_RADIUS
```

---

# 二十二、不要修改 GoalNear 半径

保持：

```text
MOVE_TO_RADIUS = 1.5
```

不要因为 false success：

```text
改成 3
```

也不要：

```text
改成 0.5
```

本阶段只修成功语义。

---

# 二十三、不要修改 move_to 的最大距离

继续：

```text
MOVE_MAX_DISTANCE = 64
```

不改变。

---

# 二十四、cleanup

保持当前：

```text
setGoal(null)
clearControlStates()
```

并确保：

```text
cleanup exactly once
```

当前 ActionRuntime 已经保证：

```text
controller.cleaned
```

所以不要另建第二套 cleanup 状态。

---

# 二十五、Node 单测

扩展：

```text
minecraft_runtime/test/move_to.test.js
```

当前测试主要是：

```text
registry
validation
movements
```

现在必须增加真正的 ActionRuntime 行为测试。

至少覆盖：

### A

正常目标：

```text
goal_reached
distance <= 1.5
→ SUCCEEDED
```

### B

**核心回归：**

```text
path_update:
path=[]
status='success'

bot distance=28

→ NOT SUCCEEDED
→ path.not_reached
```

### C

空 path + 已在目标：

```text
path=[]
status='success'
distance <= 1.5
→ SUCCEEDED
```

### D

path_update noPath：

```text
→ FAILED/path.not_found
```

### E

path_update timeout：

```text
→ FAILED
```

### F

partial：

```text
partial
→ continued
```

### G

path_stop：

```text
far from goal
→ FAILED/path.not_reached
```

### H

goal_changed：

```text
→ FAILED/goal.changed
```

### I

STOP race：

```text
goal_reached + STOP
→ exactly one terminal state
```

### J

TIMEOUT race：

```text
goal_reached + timeout
→ TIMEOUT
```

### K

cleanup exactly once

### L

final_position / distance_to_target 正确

---

# 二十六、Fake Pathfinder

Fake Pathfinder 必须至少模拟：

```text
setGoal
goal
isMoving
path_update
goal_reached
goal_updated
path_stop
```

不要继续只 fake：

```text
goto = Promise.resolve()
```

否则根本测不到本阶段的问题。

---

# 二十七、真实服务器 Smoke：必须新增一个硬门禁

这是最重要的一项。

`smoke_real_server.js` 必须加入：

```text
REAL MOVE_TO FALSE-SUCCESS GUARD
```

不要只测：

```text
近距离 move_to
```

因为那很容易通过。

---

# 二十八、Real Server False-success Test

选择一个：

```text
distance ≈ 20~30 blocks
```

的目标。

但目标必须是：

```text
真实世界中可达或明确不可达
```

第一阶段最好选择：

> **明确不可达的目标。**

例如：

```text
一个被无法穿越结构阻挡的位置
```

但不要改变世界。

如果 Smoke 能安全找到天然不可达位置：

直接用。

---

# 二十九、Real Test 必须证明

如果 Pathfinder 由于 2.4.5 的空路径 bug：

```text
goto resolve
```

CatooBot **必须不能**返回：

```text
SUCCEEDED
```

必须：

```text
FAILED/path.not_reached
```

或者：

```text
FAILED/path.not_found
```

两者都可接受，取决于实际事件原因。

---

# 三十、Real Server Positive Test

同时保留已有：

```text
近距离 move_to
```

要求：

```text
RUNNING
→ completed
→ distance <= 1.5
```

这里：

```text
distance_to_target
```

必须由真实最终位置计算。

---

# 三十一、Real STOP

继续：

```text
远距离 move_to
→ RUNNING
→ STOP
→ CANCELLED
→ goal=null
→ isMoving=false
→ position stable
```

保持现有测试。

---

# 三十二、Real no-path

如果能找到安全的真实 no-path 目标：

必须测试。

如果环境无法稳定构造：

```text
SKIPPED
```

允许。

但：

```text
FALSE-SUCCESS GUARD
```

不能 SKIPPED。

这一项必须用真实服务器证明。

---

# 三十三、Smoke 中不要继续用“零距离 move_to 清 Goal”

当前 smoke 为了处理残留 Goal 使用：

```text
向当前位置发一次零距离 move_to
```

这个 workaround 在生产 bug 修复后应该删除。

真实意义应该变成：

```text
任何 move_to
→ 成功时 Goal 自己完成并清理
→ 失败时 runtime cleanup 清理
→ STOP 时 cleanup 清理
```

Smoke 不应该依赖“再提交一个动作”清理前一个动作残留。

---

# 三十四、Follow 不要动

Phase 3D / 4H 的：

```text
follow_player
```

不要因为 move_to 修复而改。

除非测试证明 follow 也复用了同样错误的 goto 语义。

先检查。

---

# 三十五、Pickup 不要动

Phase 4H：

```text
GoalFollow
```

当前真机 STOP 和成功 cleanup 已经证明。

不要把 move_to patch 重构到 pickup。

本阶段只针对：

```text
move_to
```

---

# 三十六、不要升级依赖

不要修改：

```text
package.json
package-lock.json
```

除非测试证明当前 API 完全无法实现这一修复。

优先：

```text
CatooBot 自己防御底层 Pathfinder bug
```

---

# 三十七、自动化验证

必须：

```text
pytest
Node
Vitest
Playwright
ruff check
ruff format --check
mypy
```

全部通过。

尤其：

```text
Node move_to
```

必须不低于当前测试数量，并新增完整 ActionRuntime 行为验证。

---

# 三十八、Real Server 验收

最终至少打印：

```text
✓ move_to positive → RUNNING
✓ move_to positive → completed
✓ positive final distance <= 1.5

✓ move_to false-success guard
  Pathfinder finished/empty-path scenario
  → NOT SUCCEEDED
  → structured failure

✓ move_to STOP
  → CANCELLED
  → goal=null
  → isMoving=false
  → no drift
```

最终：

```text
REAL SERVER: PASS
```

---

# 三十九、Phase 门禁

本阶段不是新能力。

结果只能：

```text
Phase 4H.1 = PASS
```

或者：

```text
Phase 4H.1 = BLOCKED
```

只要真实服务器仍能出现：

```text
move_to completed
distance > MOVE_TO_RADIUS
```

就：

```text
BLOCKED
```

---

# 四十、最终报告

使用：

```text
Phase 4H.1 = PASS / BLOCKED

commit:
<sha>

root cause:
mineflayer-pathfinder 2.4.5 goto()
empty path resolves before noPath status

production fix:
<actual implementation>

automatic:
Node move_to = ...
pytest = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
positive move_to = ...
false-success guard = ...
STOP = ...
goal cleanup = ...

REAL SERVER: PASS / BLOCKED
```

特别记录：

```text
false-success guard = PASS
```

是本阶段的核心证据。

不要把 smoke workaround 当 production fix。