# CatooBot Minecraft Phase 3C
## Navigation Runtime / move_to

### 阶段目标

在 Phase 3B / 3B.1 Action Runtime 基础上，加入 Minecraft 第一项真正的移动能力：

> `move_to(x, y, z)`

本阶段目标不是让罐头“自己玩 Minecraft”，而是验证：

```text
高层 Action
↓
ActionRuntime
↓
Mineflayer Pathfinder
↓
Minecraft 实际移动
```

能够稳定完成、取消、超时和失败。

---

# 一、严格范围

本阶段只允许新增：

```text
move_to
```

允许安装：

```text
mineflayer-pathfinder
```

禁止：

- follow
- dig
- place
- attack
- craft
- eat
- inventory manipulation
- building
- mining
- combat
- LLM Tool
- 自主 Agent
- 自动任务规划

本阶段结束后：

> CatooBot 可以通过程序/API 请求罐头移动到指定坐标。

但普通 LLM 仍然不能直接调用 move_to。

---

# 二、Pathfinder

使用官方：

```text
mineflayer-pathfinder
```

通过：

```js
bot.loadPlugin(pathfinder)
```

加载。

使用：

```js
const { Movements, goals } = require('mineflayer-pathfinder')
```

以及：

```js
new Movements(bot)
new goals.GoalNear(...)
```

官方 API 当前提供 `GoalNear`、`GoalBlock`、`GoalFollow`、`setGoal`、`goto`、`stop` 等导航能力。([https://github.com/PrismarineJS/mineflayer-pathfinder](https://github.com/PrismarineJS/mineflayer-pathfinder))

---

# 三、Pathfinder 初始化

机器人进入世界后初始化：

```text
bot
 ↓
Movements
 ↓
bot.pathfinder.setMovements(defaultMovements)
```

本阶段使用最保守的默认移动能力。

重点：

### 禁止主动破坏世界

第一版：

```js
movements.canDig = false
```

除非测试证明：

> 不关闭 dig 就无法安全实现基本导航。

不要让 Pathfinder 为了到达目标主动：

- 挖方块
- 放方块
- 搭路
- 破坏环境

Phase 3C 的 `move_to` 应定义为：

> **非破坏性移动。**

如果目标不可达：

```text
NO_PATH
```

而不是：

```text
自动挖墙
自动搭桥
```

---

# 四、move_to Action

向 Action Registry 增加：

```text
move_to
```

属性：

```text
exclusive = true
risk = LOW
```

虽然移动会改变玩家位置，但目前没有主动修改世界。

---

# 五、参数

API：

```http
POST /minecraft/move_to
```

请求：

```json
{
  "x": 120,
  "y": 64,
  "z": -230
}
```

禁止：

```text
yaw
pitch
speed
WASD
controlState
```

LLM / CatooBot 不应该控制底层运动。

---

# 六、参数校验

继续使用 Phase 3B 的世界坐标规则：

```text
x/z ∈ [-30000000, 30000000]
y ∈ [-512, 2048]
必须为有限数字
```

另外增加：

## 最大移动距离

第一版：

```text
max_distance = 64 blocks
```

相对于当前玩家位置计算。

如果：

```text
distance > 64
```

返回：

```text
minecraft.action_invalid
```

不要让第一版直接允许数千格长距离移动。

这个限制必须配置化：

```yaml
minecraft:
  action:
    move_to:
      max_distance: 64
```

以后 Phase 3D/4 再考虑动态调整。

---

# 七、目标定义

使用：

```text
GoalNear(x, y, z, radius)
```

第一版：

```text
radius = 1.5
```

即：

> 罐头进入目标点约 1.5 格范围即视为到达。

不要使用：

```text
GoalBlock
```

作为第一版默认目标。

原因：

玩家位置通常不是严格的整数 Block Center。

---

# 八、Action 生命周期

正常：

```text
QUEUED
↓
RUNNING
↓
Pathfinder
↓
goal_reached
↓
SUCCEEDED
```

返回：

```json
{
  "action_id": "...",
  "action": "move_to",
  "status": "SUCCEEDED",
  "result": {
    "target": {
      "x": 120,
      "y": 64,
      "z": -230
    },
    "final_position": {
      "x": 119.8,
      "y": 64,
      "z": -229.7
    },
    "distance_to_target": 0.42
  }
}
```

---

# 九、Pathfinder 事件

至少监听：

```text
goal_reached
path_update
path_reset
path_stop
```

特别关注：

```text
path_reset
```

原因：

Pathfinder 官方提供的 reset reason 包含：

- goal_updated
- movements_updated
- block_updated
- chunk_loaded
- goal_moved
- dig_error
- stuck
- no_scaffolding_blocks
- place_error

这些事件可用于区分：

> 路径重新计算

和：

> 彻底失败。

不要把每次 path_update 都变成 CatooBot Event。

普通 path_update 只作为 runtime 内部状态。

---

# 十、无路径

如果目标不可达：

最终：

```text
FAILED
```

错误码：

```text
minecraft.path_not_found
```

HTTP：

```text
500
```

Action 事件：

```text
minecraft.action.failed
```

message：

```text
无法找到到达目标的非破坏性路径
```

不要：

- 自动挖
- 自动搭桥
- 自动绕无限圈
- 自动改变目标

---

# 十一、STOP 是本阶段最重要的测试

进行：

```text
move_to
↓
罐头正在走
```

然后：

```http
POST /minecraft/stop
```

必须严格：

```text
stop
↓
token.cancel()
↓
move_to.cleanup()
↓
bot.pathfinder.setGoal(null)
↓
clearControlStates()
↓
Action = CANCELLED
```

顺序必须保证：

> Minecraft 已停止执行导航后，才对外报告 CANCELLED。

最终必须验证：

```text
ActionRuntime.active_count == 0
```

并且：

```text
bot.pathfinder.goal == null
```

以及：

```text
bot.pathfinder.isMoving() == false
```

不能只验证 Action 状态。

这是本阶段最重要的验收标准。

---

# 十二、move_to cleanup

必须：

```js
cleanup(bot) {
    bot.pathfinder.setGoal(null)
    bot.clearControlStates()
}
```

如果项目选择使用：

```js
bot.pathfinder.stop()
```

必须额外验证：

> stop 后立即不再继续移动。

不能只依赖 pathfinder.stop 的语义。

优先使用：

```js
bot.pathfinder.setGoal(null)
```

作为硬清 Goal。

---

# 十三、Timeout

第一版：

```text
move_to timeout = 30s
```

超时：

```text
TIMEOUT
↓
cleanup
↓
clear Goal
↓
clearControlStates
```

返回：

```text
minecraft.action.timeout
```

不要因为路径很远而自动无限等待。

---

# 十四、玩家/世界状态变化

由于 Phase 3A 已完成 movement-aware diff：

```text
move_to 执行期间
↓
WorldPerception 继续运行
```

不得暂停感知。

验证：

```text
罐头移动
↓
WorldPerception
↓
WINDOW_SHIFT
```

不会产生：

```text
大量虚假 world.changed
```

如果实际世界发生变化：

仍然使用 Phase 3A/2.1 的规则。

---

# 十五、Runtime 状态

Current Action：

```text
action = move_to
status = RUNNING
```

WebUI 必须能够观察：

```text
目标坐标
Action ID
状态
耗时
```

可以先只显示：

```text
Moving to:
X Y Z
```

不需要做地图。

---

# 十六、CatooBot Service API

增加：

```text
POST /api/v1/minecraft/move_to
```

请求：

```json
{
  "x": 120,
  "y": 64,
  "z": -230
}
```

Service 层先做坐标 + 距离验证。

错误：

```text
minecraft.action_invalid
minecraft.action_busy
minecraft.runtime_down
minecraft.path_not_found
minecraft.action_failed
```

不要把 Pathfinder 的内部错误对象原样传给 CatooBot。

---

# 十七、暂不进入 LLM Tools

这一阶段：

```text
move_to
```

不加入 CatooBot 普通 LLM Tool Registry。

只能由：

- Service
- WebUI Debug
- 自动化测试

触发。

原因：

先验证机械层。

下一阶段再：

```text
CatooBot
↓
Minecraft Action Tool
↓
move_to
```

---

# 十八、WebUI Debug

Minecraft Debug 页面增加：

```text
Target X [ ]
Target Y [ ]
Target Z [ ]

[ MOVE TO ]

[ STOP ]
```

必须显示：

```text
Current Action
move_to
RUNNING
act_xxx
```

完成后：

```text
SUCCEEDED
```

失败：

```text
FAILED
```

停止：

```text
CANCELLED
```

---

# 十九、测试

## Unit

增加：

```text
test_move_to_registers_as_exclusive
test_move_to_validates_coordinates
test_move_to_rejects_too_far
test_move_to_rejects_offline
test_move_to_busy
test_move_to_timeout
test_move_to_cleanup_clears_goal
test_move_to_success
test_move_to_no_path
```

---

# 二十、Node Runtime E2E

使用现有 flying-squid 测试环境。

但是：

**仅用 flying-squid 不能作为最终移动可信度证明。**

因为此前已经发现 flying-squid 对部分玩家状态存在与真实服务器不同的处理。

所以：

### Test A：flying-squid

验证：

```text
join
↓
spawn
↓
move_to
↓
goal reached
↓
SUCCEEDED
```

### Test B：

验证：

```text
move_to
↓
STOP
↓
CANCELLED
↓
goal == null
↓
isMoving == false
```

### Test C：

验证：

```text
move_to
↓
timeout
↓
TIMEOUT
↓
goal == null
```

---

# 二十一、真实 Minecraft Server Smoke Test

如果开发环境有真实 Java Minecraft server：

至少进行一次：

```text
加入真实服务器
↓
move_to 近距离坐标
↓
成功
↓
STOP
↓
move_to
↓
真实停止
```

如果 CI 环境无法提供真实 Minecraft server：

不强制 CI。

但必须在文档中记录：

```text
真实服务器验证：
PASS / NOT AVAILABLE
```

---

# 二十二、特别测试地形

至少准备三个场景：

### 平地

```text
A → B
```

### 障碍物

```text
A ███ B
```

必须绕行。

### 高低差

```text
A
████
    B
```

确保默认 Movements 能安全处理。

禁止为了通过测试自动破坏方块。

---

# 二十三、移动期间的世界感知

至少验证：

```text
move_to RUNNING
↓
玩家位置持续变化
↓
World Snapshot 持续更新
↓
Semantic World Model 坐标正确
```

并验证：

```text
窗口移动
≠
世界大规模变化
```

---

# 二十四、风险等级

本阶段：

```text
move_to = LOW
```

而不是 MEDIUM/DESTRUCTIVE。

但增加：

```text
max_distance
no_dig
no_place
stop
timeout
```

作为安全门。

未来：

```text
dig = MEDIUM
place = MEDIUM
attack = HIGH
```

另行实现。

---

# 二十五、完成定义

必须满足：

```text
[ ] mineflayer-pathfinder 正确安装
[ ] bot 正常加载 pathfinder
[ ] Movements 初始化
[ ] move_to Action 注册
[ ] exclusive=true
[ ] 最大距离 64
[ ] GoalNear radius=1.5
[ ] 默认禁止 dig
[ ] 不主动 place
[ ] move_to 成功
[ ] move_to 无路径失败
[ ] move_to timeout
[ ] stop 真正终止移动
[ ] cleanup 清除 Pathfinder Goal
[ ] cleanup 清除控制位
[ ] goal == null
[ ] isMoving == false
[ ] Action = CANCELLED
[ ] WorldPerception 在移动期间继续工作
[ ] movement-aware diff 无异常
[ ] WebUI 可以测试
[ ] Service API 完整
[ ] 没有 LLM Tool
[ ] 没有 follow
[ ] 没有 dig/place/attack
[ ] Phase 1–3B.1 全回归通过
[ ] Node E2E 通过
[ ] CI 双 job 全绿
```

完成后标记：

```text
PHASE 3C COMPLETE
READY FOR PHASE 3D
```

并输出：

1. 修改文件清单
2. Pathfinder 版本
3. Movements 配置
4. move_to Action 生命周期
5. cleanup / STOP 设计
6. 无路径策略
7. timeout 策略
8. WebUI
9. 单元测试
10. E2E
11. 真实服务器 Smoke Test
12. 性能观察
13. 已知限制
14. Phase 3D readiness