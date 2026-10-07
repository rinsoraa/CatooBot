# CatooBot Minecraft Phase 3D
## follow_player Dynamic Navigation

### 阶段目标

在 Phase 3C `move_to` 的基础上增加：

```text
follow_player
```

唯一目标：

> 让罐头能够找到 Minecraft 世界中的指定玩家，并持续跟随该玩家移动；目标移动时自动重新规划路径；目标离开/失效时安全结束；用户执行 STOP 时立即停止真实移动。

本阶段仍然属于 Minecraft 机械层。

---

# 一、严格范围

本阶段只新增：

```text
follow_player
```

允许：

- `GoalFollow`
- 动态 Pathfinder Goal
- 玩家目标解析
- 动态跟随
- 跟随状态
- 安全停止

禁止：

- dig
- place
- attack
- craft
- eat
- inventory
- building
- combat
- 自动任务
- LLM Tool
- 自主 Agent
- Minecraft 行动决策
- 长距离自动分段导航

---

# 二、第一步硬化 Phase 3C

在 `configureMovements()` 中明确加入：

```javascript
movements.allow1by1towers = false
```

继续保持：

```javascript
movements.canDig = false
movements.scafoldingBlocks = []
movements.canOpenDoors = false
```

目的：

明确建立：

> 当前所有导航动作都不允许主动修改世界。

不要依赖“scafoldingBlocks 为空所以当前碰巧不能搭塔”。

---

# 三、Action 定义

注册：

```text
follow_player
```

属性：

```text
exclusive = true
risk = LOW
```

Action Registry 最终：

```text
chat
look_at
move_to
follow_player
stop
```

---

# 四、API

新增：

```http
POST /minecraft/follow_player
```

请求：

```json
{
  "username": "空凛"
}
```

可选：

```json
{
  "username": "空凛",
  "distance": 2.5
}
```

第一版建议：

```text
默认跟随距离 = 2.5
```

并限制：

```text
minimum = 1.5
maximum = 6
```

不允许请求：

- yaw
- pitch
- speed
- WASD
- controlState
- pathfinder 参数

---

# 五、玩家目标解析

Runtime 使用：

```javascript
bot.players[username]
```

寻找目标。

需要同时满足：

```text
player 存在
player.entity 存在
```

否则：

```text
minecraft.player_not_found
```

HTTP：

```text
404
```

Action：

```text
FAILED
```

不得启动 Pathfinder。

---

# 六、非常重要：目标必须使用 Entity

不要只保存：

```text
username
```

Action 开始时应得到：

```text
targetEntity
```

并使用：

```javascript
new goals.GoalFollow(targetEntity, distance)
```

目标移动时由 Pathfinder 动态 Goal 跟踪。

不要自己每 100ms：

```text
读取玩家坐标
→ setGoal()
→ setGoal()
→ setGoal()
```

否则会：

- 频繁重建路径
- 产生 goal_updated
- 造成无意义计算
- 破坏 Action 生命周期

优先使用官方 Dynamic Goal。

---

# 七、Dynamic Goal

必须明确：

```javascript
const goal = new goals.GoalFollow(targetEntity, distance)
bot.pathfinder.setGoal(goal, true)
```

其中：

```text
true = dynamic
```

必须确认实际安装版本 2.4.5 的 `setGoal(goal, dynamic)` 行为。

不要使用：

```javascript
await bot.pathfinder.goto(goal)
```

作为 follow 的唯一生命周期机制。

原因：

Pathfinder 文档明确指出：

> `goal_reached` 不会对 dynamic goal 触发。

因此：

```text
move_to
可以：
goto
→ goal reached
→ SUCCEEDED

follow_player
不能：
goto
→ 永远等 goal reached
```

---

# 八、Follow Action 生命周期

Follow 是一种：

> 持续运行 Action。

因此：

```text
QUEUED
↓
RUNNING
↓
持续跟随
↓
STOP
↓
CANCELLED
```

正常情况下：

**不会自动 SUCCEEDED。**

---

# 九、Follow 默认时长

为了防止无限运行 Action：

```text
default timeout = 120s
```

可配置：

```yaml
minecraft:
  action:
    follow_player:
      timeout: 120
```

限制：

```text
minimum = 10s
maximum = 600s
```

第一版不支持：

```text
timeout = 0
```

无限运行。

之后如果确实需要“长期跟随模式”，再设计专门的 Follow Mode，而不是让普通 Action 无限占用 foreground。

---

# 十、Follow 终止条件

### A. STOP

```text
STOP
↓
Goal null
↓
clearControlStates
↓
CANCELLED
```

与 `move_to` 完全一致。

---

### B. Disconnect

```text
disconnect
↓
cleanup
↓
CANCELLED
```

---

### C. Shutdown

同上。

---

### D. Timeout

```text
120s
↓
TIMEOUT
↓
cleanup
```

---

### E. Target Lost

如果：

```text
bot.players[username] == null
```

或者：

```text
player.entity == null
```

连续超过：

```text
3s
```

则：

```text
FAILED
```

错误码：

```text
minecraft.player_lost
```

不要因为玩家瞬间被服务器移除一帧就立刻失败。

必须允许短暂 entity refresh。

---

# 十一、Cleanup

严格复用 3C：

```javascript
cleanup(bot) {
    bot.pathfinder.setGoal(null)
    bot.clearControlStates()
}
```

必须验证：

```text
goal == null
isMoving == false
```

---

# 十二、Follow 状态

`status.pathfinder` 扩展：

```json
{
  "goal": "GoalFollow",
  "target": {
    "username": "空凛",
    "x": 123,
    "y": 64,
    "z": -230
  },
  "distance": 2.5,
  "moving": true
}
```

不要只返回：

```text
target = null
```

需要让 WebUI/debug 能知道当前正在跟谁。

---

# 十三、不要把目标位置缓存成静态坐标

错误设计：

```text
玩家当前位置
↓
GoalNear(x,y,z)
```

这实际上只是：

> 去玩家刚才的位置。

Follow 必须：

```text
GoalFollow(player.entity)
```

这样玩家移动后 Goal 可以动态变化。

---

# 十四、Pathfinder 动态状态

跟随过程中：

```text
path_update
path_reset
goal_updated
```

仍然是 Runtime 内部状态。

不要每次：

```text
path_update
```

发送：

```text
minecraft.action.*
```

否则 QQ / CatooBot 会被 path update 刷爆。

只发送 Action 级事件：

```text
started
completed
failed
cancelled
timeout
```

---

# 十五、目标丢失

必须避免：

```text
player.entity
```

对象变成 stale reference 后继续跟。

推荐在 Follow Runtime 中维护：

```text
username
targetEntity
lastSeenAt
```

周期检查：

```text
bot.players[username]?.entity
```

如果服务器重建了 entity：

更新：

```text
targetEntity
```

并重新建立 Dynamic Goal。

但只有在：

> **旧 entity 已失效**

时才允许重绑。

正常移动不得频繁 setGoal。

---

# 十六、玩家重新出现

如果玩家：

```text
logout
```

然后：

```text
login
```

可以：

```text
重新发现 entity
```

在 3 秒 grace period 内恢复。

如果超过：

```text
3s
```

仍不存在：

```text
FAILED player_lost
```

---

# 十七、距离限制

Follow 不设置：

```text
max_distance = 64
```

因为目标本身会移动。

但增加：

```text
max_chase_distance = 64
```

含义：

> 如果罐头与目标的直线距离超过 64 格，则停止追逐并失败。

错误：

```text
minecraft.follow_target_too_far
```

这样避免：

```text
玩家跑 500 格
↓
罐头持续追到世界尽头
```

默认：

```text
64 blocks
```

可配置。

---

# 十八、Follow 与 WorldPerception

必须继续：

```text
WorldPerception
```

持续运行。

跟随期间：

```text
玩家移动
↓
WINDOW_SHIFT
```

不得产生虚假的：

```text
world.changed
```

同时：

```text
players
position
distance
direction
```

必须持续更新。

---

# 十九、Action Safety

风险：

```text
follow_player = LOW
```

原因：

- 不改世界
- 不挖
- 不放
- 不攻击

但它属于：

> 持续自动移动

因此：

```text
exclusive = true
STOP required
timeout required
```

---

# 二十、Service API

新增：

```python
async def follow_player(
    self,
    username: str,
    distance: float = 2.5,
) -> dict:
```

Service 层验证：

- username 非空
- 长度 <= 16
- 不允许控制字符
- distance 在 1.5～6
- Minecraft ONLINE

---

# 二十一、WebUI

Minecraft 页面增加：

```text
Follow Player

Player:
[ 空凛________ ]

Distance:
[ 2.5 ]

[ FOLLOW ]
[ STOP ]
```

Current Action 显示：

```text
Following:
空凛

Distance:
2.5

Status:
RUNNING
```

如果玩家丢失：

```text
FAILED
Player lost
```

如果停止：

```text
CANCELLED
```

---

# 二十二、测试

## Node Unit

必须覆盖：

```text
test_follow_player_registered
test_follow_player_exclusive
test_follow_player_timeout
test_follow_player_invalid_username
test_follow_player_target_not_found
test_follow_player_invalid_distance
test_follow_player_cleanup_clears_goal
test_follow_player_risk_low
```

---

## Dynamic Goal

必须真实验证：

```text
GoalFollow
dynamic === true
```

不能只 mock 成普通 Goal。

---

# 二十三、E2E Test A

```text
join
↓
spawn
↓
observer player 空凛
↓
follow_player("空凛")
↓
空凛向前移动
↓
罐头重新计算路径
↓
继续跟随
```

验证：

```text
目标移动
≠
follow Action 结束
```

---

# 二十四、E2E Test B

```text
follow
↓
STOP
```

验证：

```text
CANCELLED
goal == null
moving == false
```

并且：

```text
400ms 后位置不再继续漂移
```

---

# 二十五、E2E Test C

```text
follow
↓
玩家移动
↓
继续跟
↓
玩家突然消失
↓
3 秒 grace
↓
FAILED player_lost
↓
cleanup
```

---

# 二十六、E2E Test D

```text
follow
↓
目标距离超过 64
↓
FAILED follow_target_too_far
↓
cleanup
```

---

# 二十七、E2E Test E

```text
follow
↓
120s
↓
TIMEOUT
↓
cleanup
```

测试可以缩短 timeout：

```text
FOLLOW_TIMEOUT_MS=2500
```

---

# 二十八、Movement-aware Perception

跟随期间至少验证：

```text
目标移动
↓
罐头移动
↓
WorldPerception 持续更新
↓
multiple WINDOW_SHIFT
↓
changed_blocks == 0
```

如果重叠区域真实方块变化：

仍应按照 Phase 2.1 / 3A 规则检测。

---

# 二十九、真实服务器验证

Phase 3C 的真实服务器 Smoke Test 当前仍是：

```text
NOT AVAILABLE
```

这一项不能永久保留。

本阶段完成后必须：

至少在一台真实 Minecraft Java Server 上完成：

```text
join
↓
follow
↓
目标移动
↓
follow continues
↓
STOP
↓
真正停止
↓
leave
```

建议优先使用你的实际 NeoForge 服务器。

CI 可以继续使用 flying-squid。

但：

> 真实服务器 Smoke Test 必须在 Phase 3E（LLM Tool 暴露）之前通过。

---

# 三十、不要暴露给 LLM

本阶段：

```text
follow_player
```

仍然不加入：

```text
CatooBot LLM Tool Registry
```

只能：

- Runtime API
- Service
- WebUI
- E2E

触发。

---

# 三十一、完成定义

必须全部满足：

```text
[ ] follow_player 注册
[ ] exclusive=true
[ ] risk=LOW
[ ] GoalFollow
[ ] dynamic Goal
[ ] 玩家 username 解析
[ ] target entity 更新
[ ] 目标丢失 grace period
[ ] 最大追逐距离
[ ] 默认 120s timeout
[ ] STOP 真停止
[ ] disconnect 真清理
[ ] shutdown 真清理
[ ] cleanup 至多一次
[ ] target 移动后继续跟随
[ ] WorldPerception 不产生虚假 world.changed
[ ] WebUI 可测试
[ ] Service API 完整
[ ] LLM Tool 仍未暴露
[ ] 不增加 dig/place/attack
[ ] Phase 1～3C 全回归通过
[ ] Node E2E 全通过
[ ] CI 双 job 全绿
[ ] 真实服务器 Follow Smoke Test PASS
```

最终标记：

```text
PHASE 3D COMPLETE
READY FOR PHASE 3E
```

并输出：

1. 修改文件清单
2. GoalFollow 实现方式
3. Dynamic Goal 生命周期
4. Target entity 管理
5. STOP / cleanup
6. Target lost 策略
7. max chase distance
8. timeout
9. E2E
10. 真实服务器 Smoke Test
11. 性能观察
12. 已知限制
13. Phase 3E readiness