# CatooBot Minecraft Phase 3B
## Safe Action Layer

### 阶段目标

在 Phase 3A movement-aware WorldPerception 之上，建立 Minecraft Action Runtime 的基础层。

本阶段目标：

> 让 CatooBot 开始拥有“可控的动作接口”，但暂时不实现真正的 Minecraft 移动。

本阶段结束后，系统应该已经具备未来 `move_to / follow / dig / place / craft` 共用的：

- Action 请求模型
- Action 生命周期
- Action ID
- 超时
- 取消
- 并发互斥
- 状态回报
- 安全停止
- 失败恢复

但本阶段只开放两个实际动作：

```text
look_at
stop
```

现有：

```text
chat
```

纳入统一 Action 体系，但不重复实现聊天能力。

---

# 一、严格禁止

本阶段禁止：

- move_to
- follow
- pathfinder
- setControlState 暴露
- forward/back/left/right/jump 等原始控制 API
- dig
- place
- break
- attack
- craft
- eat
- equip
- 自动 Agent
- LLM 自主动作循环

不要安装 `mineflayer-pathfinder`。

不要实现任何真正改变角色位置的能力。

---

# 二、架构

保持：

CatooBot
↓
MinecraftService
↓
HTTP
↓
minecraft_runtime
↓
Mineflayer
↓
Minecraft

在 runtime 内新增：

```text
ActionRuntime
```

结构：

```text
Minecraft Runtime
│
├── Connection Runtime
│
├── World Snapshot
│
└── Action Runtime
      ├── Action Registry
      ├── Action Queue / Lock
      ├── Cancellation
      ├── Timeout
      ├── Safety Stop
      └── Action Events
```

未来：

```text
move_to
follow
dig
place
attack
...
```

全部必须复用这个 Action Runtime。

---

# 三、Action 模型

新增统一 Action 状态：

```text
IDLE
QUEUED
RUNNING
SUCCEEDED
FAILED
CANCELLED
TIMEOUT
```

每次动作都有：

```json
{
  "action_id": "...",
  "action": "look_at",
  "status": "RUNNING",
  "started_at": 0,
  "finished_at": null
}
```

Action ID 必须由 runtime 生成。

禁止调用方自定义 action_id。

---

# 四、Action Registry

实现统一注册：

```text
look_at
stop
chat
```

未来可以继续：

```text
move_to
follow
dig
place
...
```

但本阶段只允许以上三个进入 Registry。

---

# 五、Action API

## 1. look_at

新增：

```http
POST /minecraft/look_at
```

请求：

```json
{
  "x": 120,
  "y": 65,
  "z": -230
}
```

含义：

> 让罐头将头部朝向指定世界坐标。

禁止传：

```text
yaw
pitch
```

不要让上层自己处理 Minecraft 旋转数学。

runtime 使用 Mineflayer：

```text
bot.lookAt(...)
```

执行完成后返回：

```json
{
  "action_id": "...",
  "action": "look_at",
  "status": "SUCCEEDED"
}
```

look_at 是低风险动作，不改变世界、不移动玩家。

---

# 六、stop

新增：

```http
POST /minecraft/stop
```

含义：

> 停止当前所有可中断动作。

本阶段即使尚未存在移动，也必须实现这个接口，因为它是未来所有移动类 Action 的硬停止入口。

底层允许使用：

```text
bot.clearControlStates()
```

但禁止把这个底层 API 暴露给 CatooBot。

stop 必须：

- 幂等
- 无 bot 时也安全
- 当前没有动作时也返回成功
- 可以取消 RUNNING / QUEUED Action
- 返回被取消的 action_id 列表

---

# 七、chat 纳入 Action Runtime

现有：

```http
POST /minecraft/chat
```

继续保持原 API contract。

但内部改由：

```text
ActionRuntime.execute("chat", ...)
```

处理。

不得破坏 Phase 1 已有接口和测试。

chat：

- 必须有 action_id
- 必须记录 started / completed / failed
- 仍然保持 256 字符限制
- 不允许因为 action runtime 重构而改变现有聊天行为

---

# 八、并发策略

第一版采用：

```text
单一 foreground action
```

同一时间最多允许：

```text
1 个 RUNNING Action
```

只有：

```text
chat
```

可以定义为非互斥通信动作。

例如：

```text
look_at RUNNING
```

此时：

```text
look_at
```

再次执行：

默认拒绝并返回：

ACTION_BUSY

不要自动覆盖正在执行的 action。

---

# 九、stop 优先级

stop 必须拥有最高优先级。

任何未来的：

```text
move_to
follow
dig
attack
```

都必须服从 stop。

虽然本阶段没有这些动作，但 Action Runtime 的结构必须提前确定：

```text
stop
 ↓
cancel current action
 ↓
clear movement controls
 ↓
return IDLE
```

---

# 十、Action 超时

所有 Action 必须具备 timeout。

建议：

```text
look_at:
5s

chat:
5s

stop:
3s
```

超时后：

```text
TIMEOUT
```

并执行必要 cleanup。

look_at TIMEOUT 不得让 runtime 进入坏状态。

---

# 十一、Action Events

增加 runtime → CatooBot 的内部事件：

```text
minecraft.action.started
minecraft.action.completed
minecraft.action.failed
minecraft.action.cancelled
minecraft.action.timeout
```

事件至少：

```json
{
  "event": "minecraft.action.completed",
  "session_id": "...",
  "timestamp": 0,
  "action_id": "...",
  "action": "look_at"
}
```

chat action 还可以附带：

```text
message
```

但不要把内部异常堆栈直接发给 LLM。

---

# 十二、CatooBot Service 层

在：

```text
MinecraftService
```

增加：

```python
async def look_at(x, y, z)
async def stop_action()
```

不要把 runtime HTTP 细节暴露到插件层。

Service 负责：

- 参数验证
- runtime 错误翻译
- action 生命周期镜像
- action event 转发

---

# 十三、Action Safety Gate

本阶段建立统一安全门：

```text
ActionRequest
    ↓
validate
    ↓
enabled?
    ↓
online?
    ↓
action allowed?
    ↓
concurrency check
    ↓
execute
```

未来增加风险等级：

```text
SAFE
LOW
MEDIUM
HIGH
DESTRUCTIVE
```

本阶段：

```text
look_at = SAFE
chat = SAFE
stop = SAFE
```

未来：

```text
move_to = LOW
dig = MEDIUM
place = MEDIUM
attack = HIGH
```

禁止在本阶段提前实现这些等级对应的动作。

---

# 十四、不要让 LLM 直接调用 Action Runtime

本阶段：

Minecraft Action Runtime 只提供底层服务能力。

不要把：

```text
look_at
stop
```

立即加入普通 CatooBot LLM Tools。

先由：

```text
Service
WebUI
测试
```

调用。

原因：

Phase 3B 是基础设施验证阶段。

LLM Tool 暴露属于 Phase 3C / Action Agent 阶段。

---

# 十五、WebUI

Minecraft Debug 页面增加：

## Current Action

显示：

```text
Action:
Status:
Action ID:
Started:
Elapsed:
```

提供开发用按钮：

```text
[ Look At Test ]
[ STOP ]
```

Look At Test 可以使用：

```text
当前玩家附近测试坐标
```

不要做自由脚本输入框。

---

# 十六、Action 日志

每个 action：

```text
started
completed
failed
cancelled
timeout
```

都必须有日志。

示例：

```text
[Minecraft Action] started action=look_at id=...
[Minecraft Action] completed action=look_at id=... elapsed=123ms
```

不要记录：

- 密码
- token
- auth 信息

---

# 十七、测试

至少新增：

### look_at

```text
test_look_at_success
test_look_at_invalid_coordinates
test_look_at_when_offline
test_look_at_timeout
```

### stop

```text
test_stop_is_idempotent
test_stop_cancels_running_action
test_stop_when_idle
```

### concurrency

```text
test_second_exclusive_action_rejected
```

### action lifecycle

```text
test_action_started_event
test_action_completed_event
test_action_failed_event
test_action_cancelled_event
```

### chat regression

确保 Phase 1：

```text
POST /minecraft/chat
```

行为完全不变。

### runtime stability

Action 异常不得：

- 杀掉 Node runtime
- 杀掉 CatooBot
- 破坏 Minecraft Connection State
- 破坏 WorldPerception

---

# 十八、E2E

Node E2E 增加：

```text
加入服务器
↓
进入世界
↓
look_at
↓
检查 action completed
↓
chat
↓
stop
↓
检查 connection 仍 ONLINE
↓
主动离开
```

重复至少 3 次。

验证：

- 无僵尸 action
- 无残留 timer
- 无并发 action
- runtime 不崩

---

# 十九、与 Phase 3A 的关系

Phase 3A：

```text
WorldPerception
```

必须继续正常工作。

Action 执行期间：

```text
WorldPerception
```

继续运行。

不要因为 action 开始就停止世界感知。

这为未来：

```text
move_to
```

执行过程中的实时 world observation 做准备。

尤其需要验证：

```text
look_at
```

不会被错误识别成：

```text
world.changed
```

也不会触发：

```text
WINDOW_SHIFT
```

---

# 二十、完成定义

只有以下全部满足：

```text
[ ] ActionRuntime 独立存在
[ ] look_at 可执行
[ ] stop 可执行
[ ] chat 纳入统一 Action 生命周期
[ ] action_id 正常生成
[ ] action 状态机完整
[ ] timeout 有效
[ ] stop 可取消 action
[ ] 同一时间只允许一个 exclusive action
[ ] Action Events 正常
[ ] WebUI 可观察 action
[ ] LLM Tool 暂不暴露
[ ] 没有 move_to
[ ] 没有 pathfinder
[ ] 没有 setControlState 对外 API
[ ] WorldPerception 不受影响
[ ] Phase 1 / 2 / 2.1 / 3A 全部回归通过
[ ] Node E2E 全部通过
[ ] CI 双 job 全绿
```

最终输出：

1. 修改文件清单
2. Action Runtime 架构
3. Action 状态机
4. API Schema
5. Event Schema
6. Safety Gate
7. 新增测试
8. 全量测试结果
9. Phase 3C readiness

禁止在本阶段自行进入 `move_to` / Pathfinder。