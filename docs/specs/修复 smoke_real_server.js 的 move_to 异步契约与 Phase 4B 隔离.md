请先阅读当前仓库源码，不要凭历史实现猜测。

目标：修复 `minecraft_runtime/test/smoke_real_server.js`，让真实 Java Server Smoke Test 与当前 Phase 3E+ ActionRuntime 的 detached action 契约一致。

本次只允许优先修改 Smoke Test / 测试辅助代码。
**不要为了让测试通过而修改 ActionRuntime、move_to、dig 的运行时语义。**
除非阅读当前源码后发现真正的 runtime bug，否则不要动生产运行时代码。

当前已确认的问题：

`move_to` / `follow_player` 从 Phase 3E 起都是 detached 持续动作：
- HTTP 启动成功立即返回 `{status:"RUNNING", action_id}`
- 终态通过 `minecraft.action.completed`
- `minecraft.action.failed`
- `minecraft.action.cancelled`
- `minecraft.action.timeout`
  等事件异步报告。

但 `smoke_real_server.js` 的“近距离 move_to”仍按旧同步契约要求：

```js
resp.body.status === 'SUCCEEDED'
```

导致第一次 move_to 实际进入 RUNNING 后，脚本继续提交第二、第三、第四个 exclusive move_to，最终全部 action.busy。
随后 Phase 4B 的 dig 又被这个 move_to 占用，造成：
- expected_block 错误测试得到 action.busy，而不是 block.changed
- 真 dig 得到 action.busy
- dig action_id 不存在
- 等待 completed 超时

这就是本次真实服务器 Smoke FAIL 的直接原因。

---

# 一、修复 detached move_to Smoke

在 `smoke_real_server.js` 增加一个通用的“等待指定 action 终态”的 helper。

要求：
- 输入 `action_id`
- 等待并返回这个 action 对应的 terminal event
- 支持：
  - `minecraft.action.completed`
  - `minecraft.action.failed`
  - `minecraft.action.cancelled`
  - `minecraft.action.timeout`
- 超时必须返回明确失败信息
- 不要只等待 completed 后再猜测失败
- 不要发明不存在的 API
- 复用当前 smoke 文件已有的 `events` 和 `waitFor`

建议抽象成类似：

```js
waitForActionTerminal(actionId, label, timeoutMs)
```

但具体实现名称由现有代码风格决定。

---

# 二、修复“近距离 move_to”阶段

当前逻辑：

```text
循环几个方向
→ POST move_to
→ 必须立即 SUCCEEDED
→ 否则继续提交下一个
```

必须改成：

```text
循环几个方向
→ POST move_to
→ 必须首先是 RUNNING
→ 保存 action_id
→ 等待该 action 进入 terminal
→ 如果 completed：
      记录该方向成功
→ 如果 path.not_found / failed：
      记录该方向失败
      然后再尝试下一个方向
→ 如果 cancelled / timeout：
      视为该方向失败
      确保 foreground 已经释放后再继续
```

**关键要求：绝不允许在前一个 exclusive action 尚未进入终态时继续提交下一个 move_to。**

对于成功案例：

```text
POST move_to
→ RUNNING
→ minecraft.action.completed
→ 读取事件 result
→ 记录 moved / movedDir
```

然后才能进入后续逻辑。

同时保留“真实世界地形未知”的鲁棒性：
- 不要求四个方向必定有路径
- 某个方向 path.not_found 可以继续尝试
- 四个方向全部失败时要产生清晰的环境相关 smoke 结果，而不是留下一个仍在 RUNNING 的 action

---

# 三、Phase 4B 前增加“Runtime Idle Barrier”

Phase 4B 是本次真正的硬门禁。

进入：

```text
// Phase 4B 硬门禁：单方块 dig 真实验证
```

之前，必须确保没有任何 foreground action 残留。

不要依赖“理论上 move_to 已经结束”。

建议：

1. 调用 `/minecraft/stop`
2. 检查返回的 `cancelled`
3. 查询 `/minecraft/status`
4. 按当前 runtime 实际暴露的状态字段确认 foreground / pathfinder 已经停止
5. 必要时短暂等待 terminal event
6. 最终保证进入 Phase 4B 时 Runtime 是 IDLE

不要发明新的状态 API；先阅读现有 `/minecraft/status` 实现，使用项目已有字段。

这一层是为了防止未来 smoke 前置阶段再次污染 dig 硬门禁。

---

# 四、Phase 4B 的错误测试必须真正验证 block.changed

下面这个测试：

```js
expected_block: 'minecraft:bedrock'
```

必须在 Runtime 空闲状态下执行。

要求：

```text
HTTP status = 409
error.code = block.changed
detail.expected = minecraft:bedrock
detail.actual = target.name
```

然后再次通过 world snapshot 确认：

```text
target block 仍然存在
```

如果出现：

```text
action.busy
```

这不应该被当成 `block.changed` 测试失败后继续往下跑，而应该明确指出 Smoke 前置动作隔离失败。

---

# 五、真正 dig 测试

确保错误 expected_block 测试完成后 Runtime 仍为空闲。

然后：

```text
POST /minecraft/dig
```

要求立即得到：

```text
200
status = RUNNING
action_id 存在
```

然后通过 action terminal event 等待：

```text
minecraft.action.completed
```

或明确失败事件。

不要再使用：

```text
action_id undefined
```

继续等待 60 秒这种情况。

如果 POST dig 本身不是 200/RUNNING，应立即输出完整响应并跳过后续“completed”等待。

---

# 六、dig 完成后验证三个层面

真实 dig 必须同时验证：

### 1. ActionRuntime

completed event：

```text
block_before = target.name
block_after != target.name
```

### 2. Minecraft 真实世界

重新 snapshot：

```text
target block != target.name
```

### 3. WorldPerception

等待系统感知到该位置发生变化。

最终要明确打印：

```text
✓ real dig completed
✓ world block changed
✓ WorldPerception detected block removal
```

---

# 七、重新挖同一位置

保留现有逻辑：

```text
expected_block = 原方块
```

要求得到：

```text
block.not_found
```

并且不能启动新的 dig action。

---

# 八、Dig STOP 测试

保留当前 STOP 测试设计，但同样遵守 detached action 规则：

```text
POST dig
→ RUNNING
→ started event
→ POST /minecraft/stop
→ action.cancelled
```

验证：

```text
目标方块仍然存在
```

并确认没有继续破坏。

这里如果真实服务器附近没有合适的慢速方块：

```text
明确打印 SKIPPED
```

不能伪造 PASS。

---

# 九、最终执行顺序

建议最终 smoke 顺序成为：

```text
1. connect
2. spawn

3. move_to real-server smoke
   ├─ near move
   ├─ wait terminal
   ├─ far move
   └─ STOP

4. follow_player real-server smoke
   ├─ target joins
   ├─ follow
   ├─ target moves
   ├─ follower moves
   └─ STOP

5. HARD IDLE BARRIER
   └─ 确保没有残留 foreground action

6. Phase 4B dig smoke
   ├─ 找可挖目标
   ├─ wrong expected → block.changed
   ├─ real dig → RUNNING
   ├─ completed
   ├─ block removed
   ├─ WorldPerception changed
   └─ re-dig → block.not_found

7. optional slow-dig STOP
8. disconnect
```

---

# 十、验收标准

修改后至少执行：

```powershell
node test/smoke_real_server.js
```

真实 Java Server `127.0.0.1:25565` 必须保持开启。

期望看到类似：

```text
✓ connect
✓ 已进入世界
✓ move_to 近距离启动 → RUNNING
✓ move_to 近距离 → completed
✓ move_to 远距离启动 → RUNNING
✓ STOP 取消 move_to
✓ goal == null
✓ isMoving == false

✓ expected_block 不符 → block.changed
✓ block.changed 带 expected/actual
✓ 拒绝后目标方块原地未动

✓ dig 启动 → RUNNING
✓ dig completed
✓ block_before 正确
✓ block_after 已改变
✓ WorldPerception 看到方块变化
✓ 同一位置再挖 → block.not_found

✓ follow_player 启动
✓ 目标移动后 follow 仍在运行
✓ 跟到目标附近
✓ STOP 取消跟随
✓ goal == null
✓ isMoving == false

REAL SERVER: PASS
```

最后要求：

1. 运行所有相关 Node/Vitest 测试。
2. 运行 Python pytest。
3. 运行 lint / typecheck（按仓库现有 CI 标准）。
4. 再运行一次真实 Java Server Smoke。
5. 汇总：
   - 修改文件
   - 根因
   - 修复方式
   - 自动化测试结果
   - 真实服务器 Smoke 结果
6. 如果真实 dig 仍然没有真正执行，不得宣称 Phase 4B PASS。
7. 不要为了通过 smoke 修改生产运行时的 detached action 语义。