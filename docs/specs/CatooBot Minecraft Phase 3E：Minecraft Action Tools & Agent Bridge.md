# CatooBot Minecraft Phase 3E
## Minecraft Action Tools & Agent Bridge

## 阶段目标

Phase 3A～3D 已完成：

- Minecraft 连接
- World Perception
- Semantic World Model
- movement-aware diff
- Action Runtime
- look_at
- chat
- move_to
- follow_player
- stop
- Pathfinder
- 动作取消 / cleanup / timeout
- 真实 Minecraft Java Server Follow 已人工验证通过

现在开始第一次将 Minecraft Action Runtime 接入 CatooBot 的 LLM Tool 系统。

本阶段唯一核心目标：

> **让罐头的大模型能够在理解用户意图后，安全地调用已经存在的 Minecraft 感知与行动能力。**

本阶段不新增 Minecraft 底层能力。

---

# 一、核心架构

当前架构：

CatooBot Core
↓
MinecraftService
↓
Minecraft Runtime
↓
Mineflayer / Pathfinder
↓
Minecraft

本阶段增加：

CatooBot LLM
↓
Minecraft Tool Layer
↓
Minecraft Policy Gate
↓
MinecraftService
↓
Action Runtime
↓
Minecraft Runtime
↓
Minecraft

完整结构：

```text
                    CatooBot LLM
                         │
                         ▼
                Minecraft Tool Layer
                         │
                         ▼
                  Action Policy
                         │
             ┌───────────┼───────────┐
             │           │           │
            READ        SAFE        LOW
             │           │           │
        world state   chat/look    move/follow
             │           │           │
             └───────────┼───────────┘
                         ▼
                  MinecraftService
                         │
                   HTTP / Events
                         │
                         ▼
                  Action Runtime
                         │
              ┌──────────┼──────────┐
              │          │          │
           look_at    move_to     follow
              │          │          │
              └──────────┼──────────┘
                         ▼
                      Minecraft
                         │
                         ▼
                  World Perception
                         │
                         ▼
                    CatooBot LLM
```

本阶段重点是打通：

> **LLM → Tool → Policy → Action Runtime → Minecraft → Event → CatooBot**

---

# 二、本阶段只开放 6 个 Minecraft Tools

允许加入普通 LLM Tool Registry：

```text
minecraft_world
minecraft_chat
minecraft_look_at
minecraft_move_to
minecraft_follow_player
minecraft_stop
```

能力等级：

```text
minecraft_world          READ
minecraft_chat           SAFE
minecraft_look_at        SAFE
minecraft_stop           SAFE
minecraft_move_to        LOW
minecraft_follow_player  LOW
```

禁止新增或暴露：

```text
dig
place
attack
craft
eat
drop
inventory mutation
container mutation
item transfer
redstone interaction
combat
building
autonomous task execution
```

这些全部留到后续阶段。

---

# 三、最重要的边界：LLM 不直接碰 Runtime

禁止：

```text
LLM
↓
HTTP /minecraft/move_to
```

必须：

```text
LLM
↓
Minecraft Tool
↓
Policy Gate
↓
MinecraftService
↓
Runtime
```

Tool Layer 不允许：

- 自己访问 Mineflayer
- 自己操作 Pathfinder
- 自己操作 bot
- 自己构造 Minecraft Runtime HTTP 请求

所有实际动作必须经过现有：

```text
MinecraftService
Action Runtime
```

以确保：

- action_id
- timeout
- cancellation
- safety
- concurrency
- event
- logging

继续统一。

---

# 四、minecraft_world

现有只读 Minecraft World Tool 可以保留并升级为本阶段第一类正式 Agent Tool。

Tool：

```text
minecraft_world
```

描述必须明确：

> 查询罐头当前 Minecraft 世界状态，只读，不会移动或修改世界。

返回：

- 当前维度
- 当前坐标
- 当前环境
- 附近玩家
- 附近实体
- 兴趣点
- 地形摘要
- 必要的方向 / 距离
- 当前活动状态

默认返回：

> Semantic World Model

不要把 45～51KB Raw Snapshot 直接送进 LLM。

Raw Snapshot 继续只存在 Runtime / Perception 层。

---

# 五、Tool 返回必须区分“状态”和“事实”

例如：

```json
{
  "online": true,
  "dimension": "minecraft:overworld",
  "position": {
    "x": 120.5,
    "y": 64,
    "z": -230.5
  },
  "nearby_players": [
    {
      "username": "空凛",
      "distance": 6.4,
      "relative_direction": "front_right"
    }
  ]
}
```

不要让 Tool 返回自然语言作为唯一数据源。

建议：

```text
structured result
+
可选 summary
```

LLM 可以读取结构化结果。

---

# 六、minecraft_chat

现有 chat 能力正式接入 LLM Tool。

Tool：

```text
minecraft_chat
```

参数：

```json
{
  "message": "我来啦！"
}
```

限制：

- 继续使用 Phase 1 的长度限制
- 保持原有校验
- 继续经过 Action Runtime
- 继续生成 action_id
- 不允许绕过 Service
- 不允许直接写 websocket / runtime

Tool 返回：

```json
{
  "action_id": "...",
  "status": "SUCCEEDED"
}
```

---

# 七、minecraft_look_at

Tool：

```text
minecraft_look_at
```

参数：

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
```

Tool 不负责计算 Minecraft rotation。

Runtime 继续计算。

Tool 描述应该让 LLM 理解：

> 让罐头看向 Minecraft 世界中的某个位置，不会移动，不会修改世界。

风险：

```text
SAFE
```

---

# 八、minecraft_move_to

Tool：

```text
minecraft_move_to
```

参数：

```json
{
  "x": 120,
  "y": 64,
  "z": -230
}
```

不要增加：

```text
speed
yaw
pitch
jump
pathfinding options
```

Tool 只表达：

> 我要去这个 Minecraft 世界位置。

底层继续：

```text
ActionRuntime
↓
Pathfinder
↓
GoalNear
```

---

# 九、move_to 必须保留现有安全限制

Tool 层不得放宽：

- 最大距离
- 非破坏性导航
- timeout
- exclusive
- stop
- cleanup

特别是：

```text
canDig=false
scaffoldingBlocks=[]
allow1by1towers=false
```

继续生效。

LLM 无论如何不能通过 Tool 参数要求：

> “为了过去可以挖墙。”

这种能力尚不存在。

---

# 十、minecraft_follow_player

Tool：

```text
minecraft_follow_player
```

参数：

```json
{
  "username": "空凛",
  "distance": 2.5
}
```

Tool 描述必须明确：

> 持续跟随指定 Minecraft 玩家。

返回必须是：

```json
{
  "action_id": "...",
  "status": "RUNNING",
  "target": "空凛"
}
```

而不是等到 follow 完成再返回。

这是本阶段最重要的异步 Tool。

---

# 十一、minecraft_stop

Tool：

```text
minecraft_stop
```

无参数。

描述：

> 立即停止当前 Minecraft 行动。

必须具有最高优先级。

如果当前：

```text
move_to RUNNING
```

或者：

```text
follow_player RUNNING
```

Tool 调用：

```text
minecraft_stop
```

必须真正停止 Minecraft。

返回：

```json
{
  "status": "IDLE",
  "cancelled": [
    "act_xxxxx"
  ]
}
```

如果没有正在运行的 Action：

```json
{
  "status": "IDLE",
  "cancelled": []
}
```

---

# 十二、Tool Policy Gate

这是 Phase 3E 最重要的新组件。

增加：

```text
MinecraftActionPolicy
```

职责：

> 决定 LLM 是否可以执行这个 Minecraft Tool。

Policy 必须检查：

```text
Tool
↓
Minecraft Enabled?
↓
Minecraft Online?
↓
Action Type
↓
Risk Level
↓
Current Action
↓
Context
↓
Allow / Reject
```

---

# 十三、风险分级

正式建立：

```text
SAFE
LOW
MEDIUM
HIGH
DESTRUCTIVE
```

本阶段：

```text
minecraft_world = SAFE
minecraft_chat = SAFE
minecraft_look_at = SAFE
minecraft_stop = SAFE
minecraft_move_to = LOW
minecraft_follow_player = LOW
```

暂未实现：

```text
dig = MEDIUM
place = MEDIUM
attack = HIGH
```

不要提前注册这些 Tool。

---

# 十四、当前阶段的 Policy 规则

默认：

### SAFE

自动允许。

### LOW

在用户明确要求 Minecraft 行动时允许。

例如：

> “罐头，过来。”

可以：

```text
minecraft_move_to
```

例如：

> “罐头跟着我。”

可以：

```text
minecraft_follow_player
```

---

# 十五、模型自主行动暂时禁止

即使模型自己推理：

> “空凛就在附近，我应该跟过去。”

本阶段也不要让它自动执行 LOW Action。

必须满足：

```text
用户明确请求
```

或者已有明确的 CatooBot Minecraft task context。

第一版只允许：

```text
explicit user intent
```

触发 LOW Action。

这样避免第一次接 Tool 就让 LLM 获得完全自主控制能力。

---

# 十六、意图判断

不要在 Tool 层写复杂 NLP。

Tool Layer 不负责判断：

> “用户是不是要让罐头过去。”

这是 CatooBot 原有 LLM 的职责。

Tool Layer 只负责：

> “模型已经决定调用 minecraft_move_to，我是否允许这个调用？”

---

# 十七、Tool 描述必须高质量

Tool description 必须告诉模型：

### minecraft_world

```text
只读查询 Minecraft 当前世界。
不会移动，不会修改世界。
```

### minecraft_look_at

```text
让罐头朝向指定世界坐标。
不会移动。
```

### minecraft_move_to

```text
让罐头通过非破坏性寻路移动到指定世界坐标。
不能挖方块或放方块。
如果没有可行路径会失败。
```

### minecraft_follow_player

```text
持续跟随指定 Minecraft 玩家。
玩家移动时会自动重新规划。
这是持续动作，返回 RUNNING action_id。
使用 minecraft_stop 可以停止。
```

### minecraft_stop

```text
立即停止当前 Minecraft 行动。
优先级最高。
```

---

# 十八、不要把 Minecraft Tool 结果全部永久塞进上下文

这是一个非常重要的设计。

例如：

```text
minecraft_world
```

返回：

```text
附近玩家
附近实体
环境
地形
```

这些应该作为：

```text
当前 Minecraft Context
```

而不是永久聊天历史。

---

# 十九、Minecraft Context

建立一个轻量：

```text
Minecraft Context
```

包括：

```text
online
world
position
activity
current_action
nearby_players
```

LLM 每次需要时获取最新状态。

不要把：

```text
10 秒前 world snapshot
20 秒前 world snapshot
30 秒前 world snapshot
```

全部塞进 conversation history。

---

# 二十、Action Event 必须回到 CatooBot

当：

```text
minecraft_move_to
```

Tool 返回：

```text
RUNNING
```

后：

```text
Minecraft
↓
Action Completed
```

事件：

```text
minecraft.action.completed
```

必须重新进入 CatooBot Event Bus。

形成：

```text
LLM
↓
move_to
↓
RUNNING
↓
Minecraft
↓
completed
↓
CatooBot
↓
当前 Minecraft Context 更新
↓
LLM 下一轮可以继续推理
```

---

# 二十一、不要自动生成多轮 LLM

第一版禁止：

```text
move_to
↓
completed
↓
自动再调用 LLM
↓
再决定一个动作
```

除非 CatooBot 当前会话本身正处于一个明确的 Agent Turn。

不要在事件回调中启动无限 Agent Loop。

这样可以避免：

```text
move
→ think
→ move
→ think
→ move
→ 无限循环
```

---

# 二十二、事件语义

当 Action：

### RUNNING

不要模拟 completed。

### SUCCEEDED

可以进入：

```text
minecraft.activity
```

### FAILED

进入：

```text
minecraft.last_action
```

### CANCELLED

进入：

```text
minecraft.last_action
```

### TIMEOUT

进入：

```text
minecraft.last_action
```

但都不要自动触发新的 Minecraft Action。

---

# 二十三、聊天事件

Minecraft 玩家：

```text
空凛：罐头过来
```

通过：

```text
minecraft.chat
```

进入 CatooBot。

LLM 可以结合：

```text
minecraft_world
```

来决定：

```text
move_to
```

例如：

```text
空凛：罐头过来
↓
CatooBot 读取附近玩家
↓
发现空凛 front_right 7m
↓
move_to(空凛附近位置)
```

第一版不要自动执行：

```text
follow_player
```

除非模型明确选择该 Tool。

---

# 二十四、明确处理 Action Context

当模型发起：

```text
minecraft_follow_player
```

CatooBot 应保存：

```text
current_minecraft_action:
{
  action_id,
  type: follow_player,
  target: 空凛,
  status: RUNNING
}
```

否则后续：

> “罐头，停下。”

模型可能不知道当前正在 Follow。

---

# 二十五、stop 的特殊处理

即使：

```text
current action = follow_player
```

用户说：

> “停。”

应该允许：

```text
minecraft_stop
```

无需额外确认。

STOP 是 SAFE + interruptive action。

---

# 二十六、并发策略

保持 Action Runtime：

```text
1 个 foreground action
```

因此：

```text
move_to RUNNING
```

再次：

```text
follow_player
```

必须返回：

```text
minecraft.action_busy
```

LLM 得到明确结果：

> 当前正在执行 Minecraft 行动。

不要自动取消当前行动。

除非模型显式调用：

```text
minecraft_stop
```

---

# 二十七、重要的 Tool 错误必须结构化

禁止把：

```text
Traceback
Node exception
HTTP stack
```

直接返回给 LLM。

必须结构化：

```json
{
  "ok": false,
  "error": {
    "code": "minecraft.action_busy",
    "message": "当前正在执行另一个 Minecraft 行动"
  }
}
```

至少支持：

```text
minecraft.offline
minecraft.disabled
minecraft.action_busy
minecraft.action_invalid
minecraft.path_not_found
minecraft.player_not_found
minecraft.player_lost
minecraft.follow_target_too_far
minecraft.action_timeout
minecraft.action_failed
```

---

# 二十八、Tool 成功返回

推荐统一：

```json
{
  "ok": true,
  "action": "move_to",
  "action_id": "...",
  "status": "RUNNING"
}
```

即时完成：

```json
{
  "ok": true,
  "action": "look_at",
  "action_id": "...",
  "status": "SUCCEEDED"
}
```

---

# 二十九、不要改变现有 Runtime API

Phase 3E 的 Tool Layer 应建立在已经存在的：

```text
MinecraftService
```

之上。

不要：

- 改 Runtime 的 move_to 行为
- 改 Pathfinder
- 改 Action Runtime 生命周期
- 改 WorldPerception
- 改 cancellation semantics

除非发现明确的兼容 Bug。

---

# 三十、WebUI

保持现有 Debug UI。

可以新增一个：

```text
LLM Tool Debug
```

只读显示：

```text
Available Tools
minecraft_world
minecraft_chat
minecraft_look_at
minecraft_move_to
minecraft_follow_player
minecraft_stop
```

显示：

```text
risk
enabled
allowed
```

不要提供“模拟 LLM”的复杂控制台。

---

# 三十一、权限 / 配置

增加：

```yaml
minecraft:
  agent:
    tools:
      enabled: true
      allow_safe: true
      allow_low: true
```

默认：

```text
allow_safe = true
allow_low = true
```

但是只针对本阶段已经注册的 Tool。

未来：

```text
allow_medium
allow_high
```

默认必须为：

```text
false
```

不要现在实现它们对应的动作。

---

# 三十二、LLM Tool Registry 集成

找到 CatooBot 当前正式 Tool Registry。

Minecraft Tools 必须作为正式 Tool 注册。

不要写：

```text
if minecraft:
    special prompt
```

不要通过 system prompt 模拟 Tool。

必须使用现有：

```text
Tool definition
name
description
parameters
handler
```

保持与其他 CatooBot Tool 一致。

---

# 三十三、Tool 生命周期

Tool handler：

```text
validate
↓
policy
↓
service
↓
runtime
↓
return structured result
```

绝不：

```text
Tool
↓
sleep 30s
↓
等 move_to 完成
```

`move_to`：

```text
RUNNING
```

立即返回。

`follow_player`：

```text
RUNNING
```

立即返回。

---

# 三十四、测试：Tool Registry

必须新增：

```text
test_minecraft_world_tool_registered
test_minecraft_chat_tool_registered
test_minecraft_look_at_tool_registered
test_minecraft_move_to_tool_registered
test_minecraft_follow_player_tool_registered
test_minecraft_stop_tool_registered
```

---

# 三十五、测试：Policy

必须覆盖：

```text
SAFE → allowed
LOW + explicit user intent → allowed
LOW + no explicit user intent → rejected
```

以及：

```text
Minecraft offline → rejected
Minecraft disabled → rejected
busy → rejected
```

---

# 三十六、测试：Tool → Service

必须验证 Tool 不直接访问 Runtime。

测试：

```text
Tool
↓
Mock MinecraftService
```

确保 handler 只调用 Service。

---

# 三十七、测试：异步 Action

### move_to

```text
Tool
↓
Service
↓
RUNNING + action_id
```

不得等待完成。

### follow_player

同上。

必须验证：

```text
elapsed < request timeout
```

不会因为 follow timeout=120s 而让 Tool 请求阻塞 120 秒。

---

# 三十八、测试：Action Event

模拟：

```text
minecraft.action.completed
```

验证：

```text
current_minecraft_action
```

正确结束。

模拟：

```text
minecraft.action.failed
```

正确记录错误。

模拟：

```text
minecraft.action.cancelled
```

正确清除 foreground action。

---

# 三十九、测试：自然语言真实闭环

不要只做 Tool unit tests。

至少新增：

### Test A

用户：

```text
“罐头，过来。”
```

模型选择：

```text
minecraft_move_to
```

目标：

> 空凛附近位置

验证：

```text
Tool
→ Service
→ Runtime
```

成功。

---

### Test B

用户：

```text
“罐头跟着我。”
```

模型选择：

```text
minecraft_follow_player
```

验证返回：

```text
RUNNING
```

而不是等待。

---

### Test C

用户：

```text
“停。”
```

模型选择：

```text
minecraft_stop
```

验证当前 Follow 被取消。

---

# 四十、模型 Prompt 约束

Minecraft Tool description 和 Agent prompt 必须明确：

1. 不要因为“应该”“最好”“可以顺便”而自主调用 LOW Action。
2. LOW Action 应有明确用户意图。
3. Action 已 RUNNING 时，不要重复调用同一个 Action。
4. 用户说“停”，优先调用 stop。
5. 在不知道目标位置时，先使用 minecraft_world。
6. 不要假设玩家坐标。
7. 不要编造 Minecraft 世界事实。
8. Tool 报错必须根据 error code 调整，而不是重复无限尝试。
9. 不允许通过 chat 工具伪造行动已经完成。
10. 不允许把 Minecraft Action 当作普通文本回复来假装执行。

---

# 四十一、特别处理“过来”

用户：

> “罐头过来。”

模型不能直接假设：

```text
move_to(0,0,0)
```

应该：

```text
minecraft_world
↓
寻找当前附近用户目标
↓
确认目标玩家
↓
move_to
```

如果有多个玩家：

```text
附近：
空凛
Alice
Bob
```

而当前上下文无法确定“你”指谁：

不要猜。

可以向用户询问：

> “你是在叫我去空凛那里吗？”

第一版宁可询问，不要错误移动。

---

# 四十二、Follow 的选择

用户：

> “跟着我。”

如果当前 Minecraft Chat sender 能映射到真实 Minecraft username：

优先：

```text
minecraft_follow_player(sender)
```

如果不能确定对应玩家：

不要猜。

可以：

```text
minecraft_world
```

确认附近玩家。

---

# 四十三、不要让 QQ 用户名和 Minecraft username 强绑定

必须区分：

```text
QQ identity
Minecraft identity
```

未来用户可能：

```text
QQ = 空凛
MC = RinsoraNeko
```

应该通过 CatooBot 当前已有用户 / Minecraft mapping 解决。

本阶段可以优先依赖：

```text
当前 Minecraft Chat sender
```

但不要把：

```text
QQ nickname == MC username
```

写死。

---

# 四十四、Memory 暂不扩大

Phase 3E 不正式实现：

```text
Minecraft Long-Term Memory
```

可以更新：

```text
当前 Minecraft Context
```

但不要新增：

```text
永久 POI
永久地图
永久地点记忆
```

这些留给后续阶段。

---

# 四十五、性能要求

Minecraft Tools 不得让：

```text
Raw Snapshot
```

进入 LLM。

LLM Context 继续控制在 Phase 2 的：

```text
≈249 bytes / ≈167 Chinese chars
```

量级。

Tool call arguments 必须小。

不要在每轮聊天自动调用：

```text
minecraft_world
```

除非：

- 用户的问题涉及 Minecraft
- 当前 Minecraft Context 已失效
- Agent 正在执行 Minecraft Task

---

# 四十六、重要的防循环机制

必须防止：

```text
move_to
↓
Action Completed
↓
LLM
↓
move_to
↓
Action Completed
↓
LLM
↓
...
```

Action event 默认只更新：

```text
Minecraft Context
```

不得自动启动新的 Agent Turn。

只有现有 CatooBot 对话 / Agent Loop 明确处于 active turn 时，才可以继续推理。

---

# 四十七、Logging

必须能够看到：

```text
[MC Tool] requested tool=...
[MC Policy] allowed/rejected ...
[MC Action] action_id=...
[MC Action] status=...
```

但禁止：

- auth token
- password
- API key
- Minecraft credentials

进入日志。

---

# 四十八、WebUI Debug

增加：

```text
Minecraft Agent Context
```

只读显示：

```text
Online
Position
Nearby Players
Current Action
Last Action
Available Tools
```

例如：

```text
Current Action:
follow_player
Target:
空凛
Status:
RUNNING
Action ID:
act_xxxxx
```

---

# 四十九、回归

必须通过：

```text
pytest
ruff
format
mypy
vitest
vue-tsc
Playwright
Node npm test
Node E2E
GitHub Actions 双 job
```

而且：

```text
Phase 1
Phase 2
Phase 2.1
Phase 3A
Phase 3B
Phase 3B.1
Phase 3C
Phase 3D
```

全部回归。

不要因为 LLM Tool 层改动破坏旧 Runtime API。

---

# 五十、验收标准

## Tool 注册

```text
[ ] minecraft_world
[ ] minecraft_chat
[ ] minecraft_look_at
[ ] minecraft_move_to
[ ] minecraft_follow_player
[ ] minecraft_stop
```

全部进入正式 Tool Registry。

---

## Policy

```text
[ ] SAFE 自动允许
[ ] LOW 需要明确用户意图
[ ] Minecraft offline 被拒绝
[ ] disabled 被拒绝
[ ] action busy 被拒绝
```

---

## Runtime

```text
[ ] move_to 行为不变
[ ] follow_player 行为不变
[ ] stop 行为不变
[ ] Action cleanup 不变
```

---

## Async

```text
[ ] move_to RUNNING 立即返回
[ ] follow_player RUNNING 立即返回
[ ] Action Completed 后通过 event 更新 Context
[ ] Tool request 不被 30s / 120s action timeout 阻塞
```

---

## Safety

```text
[ ] LLM 无法 dig
[ ] LLM 无法 place
[ ] LLM 无法 attack
[ ] LLM 无法 craft
[ ] LLM 无法直接 setControlState
[ ] LLM 无法直接访问 Mineflayer
```

---

## 自然语言

必须验证：

```text
“罐头你过来”
→ minecraft_world
→ minecraft_move_to
```

以及：

```text
“罐头跟着我”
→ minecraft_follow_player
```

以及：

```text
“停下”
→ minecraft_stop
```

---

# 五十一、完成定义

Phase 3E COMPLETE 必须意味着：

> 罐头的大模型第一次拥有了安全使用 Minecraft 身体的能力。

必须满足：

```text
[ ] LLM 可以查询 Minecraft 世界
[ ] LLM 可以在用户明确要求时移动
[ ] LLM 可以在用户明确要求时跟随玩家
[ ] LLM 可以看向目标
[ ] LLM 可以发 Minecraft Chat
[ ] LLM 可以停止当前动作
[ ] LOW Action 有明确 Policy Gate
[ ] 异步 Action 不阻塞对话请求
[ ] Action Event 可以回到 CatooBot Context
[ ] 不存在自主无限 Action Loop
[ ] 不存在绕过 Action Runtime 的路径
[ ] 没有新增破坏世界能力
[ ] 所有旧阶段回归通过
[ ] CI 双 job 全绿
```

最终输出：

1. 修改文件清单
2. Minecraft Tool Registry 架构
3. Tool Schema
4. Policy Gate
5. Risk Matrix
6. Minecraft Context 设计
7. Async Action/Event Bridge
8. Prompt / Tool Description 设计
9. 测试清单
10. 自然语言 E2E
11. 安全边界验证
12. 性能 / Context Size
13. 已知限制
14. Phase 4 readiness

---

# 五十二、Phase 4 不要提前实现

Phase 3E 完成后才能进入：

```text
Phase 4
Minecraft Interaction
```

Phase 4 才开始：

```text
dig
place
break
attack
eat
inventory
container
craft
```

并逐步增加：

```text
MEDIUM
HIGH
DESTRUCTIVE
```

风险等级与确认门。

不要在本阶段自行实现这些能力。