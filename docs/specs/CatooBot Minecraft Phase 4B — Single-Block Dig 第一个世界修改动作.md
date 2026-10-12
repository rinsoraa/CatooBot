# CatooBot Minecraft Phase 4B
## Single-Block Dig / 第一个世界修改动作

## 阶段目标

Phase 4A 已完成：

- Minecraft Chat → USER Turn
- trusted player
- Confirmation Store
- Confirmation Gate
- MEDIUM/HIGH/DESTRUCTIVE 风险基础设施
- Action Policy
- Action Runtime
- World Perception
- move_to
- follow_player

现在开始加入第一个真正能够修改 Minecraft 世界的动作：

> `minecraft_dig`

本阶段只允许：

> **破坏一个明确指定的 Minecraft 方块。**

不要实现连续挖矿、矿物搜索、自动找矿、工具选择、挖矿任务或自主采集。

最终闭环必须是：

```text
用户说：
“把我面前这个方块挖掉”

        ↓

minecraft_world
        ↓

确定目标方块
        ↓

minecraft_dig
        ↓

MEDIUM Policy
        ↓

Confirmation Required
        ↓

用户确认
        ↓

再次调用完全相同参数
        ↓

Confirmation Consumed
        ↓

MinecraftService
        ↓

ActionRuntime
        ↓

Mineflayer dig
        ↓

Block Broken
        ↓

WorldPerception 发现真实变化
        ↓

CatooBot 获得真实结果
```

---

# 一、重要：Phase 4B 前置安全硬化

当前 Phase 4A 存在一个只影响 Debug/Test 的潜在问题：

```text
POST /api/v1/minecraft/agent/confirm
action=create_test
```

可以创建 PENDING confirmation。

目前因为生产注册表没有 MEDIUM/HIGH Tool，所以没有实际动作可以消费它。

Phase 4B 即将注册：

```text
minecraft_dig = MEDIUM
```

因此必须在正式注册 `minecraft_dig` 之前处理这个问题。

## 要求

生产环境中的：

```text
create_test
```

不得创建能够授权正式 Minecraft Action 的 Confirmation。

推荐方案：

### 方案 A，优先

`create_test` 只允许创建：

```text
test.*
```

或测试专用、生产 Tool Registry 不存在的 action。

例如：

```text
minecraft_test_confirmation
```

而不能：

```text
minecraft_dig
minecraft_attack
minecraft_place
```

### 方案 B

如果项目已有明确测试模式：

```text
ENV=test
```

则 `create_test` 只在测试环境开放。

### 方案 C

删除生产 WebUI 的 `create_test` 创建能力，只保留：

- 查看
- cancel
- expire

选择与现有项目架构最一致的方案。

---

## 完成标准

在 `minecraft_dig` 注册后：

> WebUI Debug 不能凭空为正式 `minecraft_dig` 创建一条可被真实 USER Turn 消费的 Confirmation。

测试代码仍然可以直接注入 ConfirmationStore / Fake Tool 做 Confirmation 测试。

不要依赖：

```text
“管理员是可信的”
```

来解决这个问题。

这是授权语义问题，而不是权限等级问题。

---

# 二、严格范围

本阶段只允许新增：

```text
minecraft_dig
```

并为它提供：

- Runtime Action
- Service
- Agent Tool
- MEDIUM Policy
- Confirmation
- World Verification
- WebUI Debug
- 单块 Dig E2E

---

# 三、严格禁止

禁止实现：

```text
minecraft_mine
minecraft_mine_ore
minecraft_collect
minecraft_gather
minecraft_strip_mine
minecraft_break_area
minecraft_dig_multiple
minecraft_auto_mine
```

禁止：

- 自动搜索矿物
- 自动寻找方块
- 自动选择多个目标
- 自动连续挖掘
- 自动补工具
- 自动切换镐子
- 自动合成工具
- 自动获取掉落物
- 自动导航到目标
- 自动修改第二个方块

本阶段：

> 一个 Tool Call = 最多一个目标方块。

---

# 四、风险等级

加入：

```python
ACTION_RISK["minecraft_dig"] = "MEDIUM"
```

因此：

```text
minecraft_dig
    ↓
MEDIUM
    ↓
allow_medium
    ↓
USER Turn
    ↓
trusted player（仅 Minecraft Chat）
    ↓
Confirmation
    ↓
Action
```

不要改现有：

```text
SAFE
LOW
```

行为。

---

# 五、Tool Schema

注册正式 Tool：

```text
minecraft_dig
```

参数建议：

```json
{
  "x": 120,
  "y": 64,
  "z": -230,
  "expected_block": "minecraft:stone"
}
```

要求：

```text
additionalProperties = false
```

参数：

### x

有限数字。

### y

有限数字。

### z

有限数字。

### expected_block

必须为非空字符串。

建议格式：

```text
minecraft:stone
```

---

# 六、为什么必须保存 expected_block

Confirmation 不应该只确认：

```text
“挖坐标 120,64,-230”
```

因为用户确认之后，世界可能发生变化。

例如：

第一次：

```text
120,64,-230 = minecraft:stone
```

用户：

> 确认

等待期间：

```text
120,64,-230 = minecraft:diamond_ore
```

这时候不能因为：

> 坐标没变

就直接执行。

必须再次验证：

```text
current_block.name == expected_block
```

否则：

```text
minecraft.dig
```

拒绝。

错误：

```text
minecraft.block_changed
```

这样用户真正确认的是：

> **“挖这个位置的这个方块”**

而不是：

> “无条件破坏未来这个坐标上出现的任何东西”。

---

# 七、建议 Confirmation arguments

Confirmation 的 arguments 必须完整保存：

```json
{
  "x": 120,
  "y": 64,
  "z": -230,
  "expected_block": "minecraft:stone"
}
```

不要只 hash：

```text
x/y/z
```

也不要只 hash：

```text
expected_block
```

必须全部进入 arguments_hash。

现有 ConfirmationStore 已经按整个 arguments 做 hash，继续复用。

---

# 八、minecraft_dig Tool Description

Tool description 必须明确：

> 破坏一个指定的 Minecraft 方块。
>
> 这是 MEDIUM 风险动作。
>
> 会真实修改 Minecraft 世界。
>
> 必须先使用 `minecraft_world` 确认目标方块和坐标。
>
> 需要用户确认。
>
> 每次最多破坏一个方块。
>
> 如果目标方块已经变化、无法破坏、距离太远或不存在，则失败。
>
> 不会自动寻找其他方块，也不会连续挖掘。
>
> 不会自动导航。
>
> 不会自动准备工具。

特别强调：

> **不要假装挖掉。必须等待实际 Action Result。**

---

# 九、minecraft_world → minecraft_dig

模型必须能够形成：

```text
minecraft_world
↓
找到：
玩家前方 3 格
方块：
minecraft:stone
坐标：
120,64,-230
↓
minecraft_dig(
  x=120,
  y=64,
  z=-230,
  expected_block="minecraft:stone"
)
```

禁止：

```text
minecraft_dig(
  x=120,
  y=64,
  z=-230,
  expected_block="unknown"
)
```

禁止根据自然语言自己猜 block name。

---

# 十、Runtime Action

Action Registry 增加：

```text
minecraft_dig
```

属性：

```text
exclusive = true
risk = MEDIUM
timeout = configurable
```

建议第一版：

```text
timeout = 30s
```

可配置：

```yaml
minecraft:
  action:
    dig:
      timeout: 30
```

范围：

```text
5～120 秒
```

---

# 十一、Action 生命周期

正常：

```text
QUEUED
↓
RUNNING
↓
Mineflayer dig
↓
Block Broken
↓
SUCCEEDED
```

失败：

```text
FAILED
```

取消：

```text
STOP
↓
CANCELLED
```

超时：

```text
TIMEOUT
```

---

# 十二、底层 Mineflayer API

使用当前版本提供的：

```javascript
bot.canDigBlock(block)
bot.dig(block, ...)
bot.stopDigging()
```

`bot.canDigBlock(block)` 用于判断目标方块是否可挖以及是否在有效范围内；`bot.dig()` 返回 Promise，完成或被中断后结束；`bot.stopDigging()` 可主动终止当前挖掘。根据项目实际安装版本 API 进行最终适配。([https://github.com/PrismarineJS/mineflayer/blob/master/docs/api.md](https://github.com/PrismarineJS/mineflayer/blob/master/docs/api.md))

---

# 十三、Dig Action 的 validate

在真正执行之前：

## 1. Minecraft Online

否则：

```text
minecraft.offline
```

---

## 2. 坐标合法

复用已有世界坐标校验。

---

## 3. Dimension

必须当前维度。

Phase 4B 不支持跨维度。

---

## 4. Block 存在

```text
bot.blockAt(position)
```

如果：

```text
null
```

失败：

```text
minecraft.block_not_found
```

---

# 十四、当前 Block 必须匹配 expected_block

读取：

```text
current_block.name
```

然后：

```text
if current_block.name != expected_block:
    FAILED
```

错误：

```text
minecraft.block_changed
```

返回结构化：

```json
{
  "expected": "minecraft:stone",
  "actual": "minecraft:dirt"
}
```

不要执行 dig。

---

# 十五、禁止挖空气

如果：

```text
air
cave_air
void_air
```

直接：

```text
minecraft.block_not_found
```

不要调用 `bot.dig()`。

---

# 十六、canDigBlock

执行：

```javascript
bot.canDigBlock(block)
```

如果 false：

```text
minecraft.block_not_diggable
```

不要尝试：

- 换工具
- 走近
- 自动寻找角度
- 自动移动

---

# 十七、距离要求

第一版只允许近距离 Dig。

建议：

```text
max_dig_distance = 5
```

以 Bot 当前坐标计算。

如果：

```text
distance > 5
```

返回：

```text
minecraft.block_too_far
```

不要自动 move_to。

原因：

Phase 4B 不应该把：

```text
Dig
+
Navigation
```

耦合起来。

---

# 十八、Line of Sight / 可见性

第一版建议：

> 目标必须能够被 Mineflayer 正常视线 / raycast 触达。

但不要自己实现复杂视觉系统。

可以优先使用：

```text
bot.canDigBlock()
```

以及：

```text
bot.dig()
```

实际失败作为最终验证。

如果项目已经有成熟的 block raycast helper，可以复用。

禁止本阶段为了 Dig 新建一套复杂的视觉系统。

---

# 十九、开始挖掘

建议：

```javascript
await bot.dig(block, true)
```

即让 Mineflayer 负责朝向目标方块。

不要在 Python 层自己控制：

```text
yaw
pitch
WASD
```

---

# 二十、Dig cleanup

这是本阶段最重要的安全点之一。

```javascript
cleanup(bot) {
    bot.stopDigging()
    bot.clearControlStates()
}
```

必须复用 Phase 3B.1 的 cleanup 语义。

以下情况必须 cleanup：

```text
CANCELLED
TIMEOUT
disconnect
shutdown
```

cleanup 至多一次。

---

# 二十一、STOP 行为

用户：

> 停下

Tool：

```text
minecraft_stop
```

如果当前：

```text
minecraft_dig RUNNING
```

必须：

```text
stop
↓
bot.stopDigging()
↓
clearControlStates()
↓
Action CANCELLED
```

必须真实停止。

不能只是：

```text
Action = CANCELLED
```

---

# 二十二、Dig Result

成功：

```json
{
  "action": "dig",
  "status": "SUCCEEDED",
  "result": {
    "position": {
      "x": 120,
      "y": 64,
      "z": -230
    },
    "block_before": "minecraft:stone",
    "block_after": "minecraft:air"
  }
}
```

Action 完成前必须至少重新读取一次：

```text
bot.blockAt(position)
```

确认目标已经不再是：

```text
expected_block
```

---

# 二十三、不要假设 bot.dig Promise 成功就一定代表世界已更新

Action 结束后：

```text
bot.blockAt(position)
```

再次确认。

如果仍然：

```text
minecraft:stone
```

则：

```text
minecraft.block_break_unconfirmed
```

失败。

这能避免客户端状态和服务器状态不同步时模型误以为“已经挖掉”。

---

# 二十四、WorldPerception 联动

方块被挖掉以后：

```text
Minecraft
↓
WorldPerception
↓
near diff
↓
removed block
↓
world state updated
```

这是本阶段第一次真正验证：

> **Action 改变世界 → WorldPerception 看到变化。**

必须验证：

```text
old:
stone

new:
air / missing

changed_blocks >= 1
```

不能只测试 Action API 返回 SUCCESS。

---

# 二十五、确认流程

完整用户流程：

### 第一轮

用户：

> 把我面前这个石头挖掉。

LLM：

```text
minecraft_world
```

↓

```text
发现：
stone
120,64,-230
```

↓

调用：

```text
minecraft_dig
```

第一次：

```text
confirmation_required
```

不执行。

---

### CatooBot 回复用户

应该明确：

> “挖掉你面前这个石头需要确认，要我现在挖吗？”

或者类似自然表达。

不能直接：

> “已经挖好了。”

---

### 第二轮

用户：

> 确认。

LLM：

再次调用：

```text
minecraft_dig(
  x=120,
  y=64,
  z=-230,
  expected_block="minecraft:stone"
)
```

Confirmation：

```text
PENDING
+
USER
+
same session
+
same user
+
same arguments
```

↓

```text
CONSUMED
```

↓

执行。

---

# 二十六、确认之后也必须重新做安全验证

不要认为：

```text
Confirmation == Permission to execute anything
```

确认只能说明：

> 用户授权了这个具体动作。

执行前仍必须：

```text
Minecraft online
风险允许
目标存在
目标 block 未变化
目标可挖
目标在距离内
Action 不忙
```

任意一个失败：

```text
不得执行
```

---

# 二十七、Confirmation 与 expected_block

Confirmation hash 必须包括：

```text
x
y
z
expected_block
```

用户确认的是：

```text
“把这里的 stone 挖掉”
```

不是：

```text
“把这里现在存在的东西挖掉”
```

---

# 二十八、参数变化

例如：

第一轮：

```text
stone @ 120,64,-230
```

创建：

```text
confirmation A
```

下一轮模型错误改成：

```text
stone @ 121,64,-230
```

必须：

```text
confirmation_mismatch
```

旧确认作废。

重新创建：

```text
confirmation B
```

不能直接执行。

---

# 二十九、玩家说“确认”但模型没调用同一个参数

不能直接在代码层猜：

```text
“确认” = 执行上一次任意 Dig
```

必须：

```text
LLM
↓
minecraft_dig
↓
same arguments
```

再由 ConfirmationStore 判断是否匹配。

---

# 三十、Action Error Codes

至少：

```text
minecraft.block_not_found
minecraft.block_changed
minecraft.block_not_diggable
minecraft.block_too_far
minecraft.block_break_unconfirmed
minecraft.offline
minecraft.action_busy
minecraft.action_timeout
minecraft.action_failed
minecraft.confirmation_required
minecraft.confirmation_invalid
minecraft.confirmation_expired
minecraft.confirmation_mismatch
minecraft.user_not_trusted
```

不要返回 Mineflayer 原始异常文本。

---

# 三十一、Mineflayer Dig 错误处理

`bot.dig()` 过程中可能发生：

- digging aborted
- target invalid
- server interruption
- disconnect

必须统一翻译成稳定 Action 错误。

尤其：

```text
diggingAborted
```

不能让 Node Runtime 崩溃。

如果是 stop：

```text
CANCELLED
```

如果是自然异常：

```text
FAILED
```

必须区分。

---

# 三十二、Action 并发

`minecraft_dig`：

```text
exclusive = true
```

因此：

```text
dig RUNNING
+
move_to
```

应：

```text
action_busy
```

不能让角色边挖边走。

同理：

```text
dig RUNNING
+
follow_player
```

拒绝。

---

# 三十三、LLM 不得在 Dig 过程中自动启动其它 Action

例如：

```text
minecraft_dig RUNNING
```

不要因为模型收到：

```text
action started
```

就：

```text
move_to
```

或者：

```text
look_at
```

事件只更新 Context。

不要自动启动新的 Agent Turn。

---

# 三十四、Minecraft Chat 场景

必须支持：

### 可信玩家

```text
空凛：
把我面前这个石头挖掉
```

允许进入：

```text
USER
+
trusted
```

然后：

```text
minecraft_world
→ minecraft_dig
→ confirmation
```

---

### 不可信玩家

```text
Steve：
把那个方块挖掉
```

不得执行。

返回：

```text
minecraft.user_not_trusted
```

但仍然可以聊天。

---

# 三十五、QQ / WebUI 场景

QQ 用户：

```text
罐头，把这个方块挖掉
```

LOW/ MEDIUM Policy：

```text
USER
```

无需 Minecraft trusted_players。

但 MEDIUM：

```text
Confirmation required
```

仍然必须发生。

---

# 三十六、WebUI Debug

Minecraft 页面增加：

## Dig Test

字段：

```text
X
Y
Z
Expected Block
```

按钮：

```text
[ DIG ]
[ STOP ]
```

必须真实调用 Tool / Service 的正式链路。

不要让 WebUI 直接操作 Mineflayer。

---

# 三十七、Debug Dig

WebUI 的直接 Action Test 可以绕过 LLM 的“自然语言意图判断”，因为它本质上是开发者直接调用动作。

但是：

> **不能绕过 MEDIUM Confirmation。**

测试：

```text
WebUI
↓
minecraft_dig
↓
confirmation_required
```

管理员按钮最多：

```text
create test pending
cancel
expire
```

不能：

```text
confirm
consume
```

---

# 三十八、Phase 4A Debug Confirmation 安全

修复后必须确保：

生产：

```text
create_test(minecraft_dig)
```

不可产生可执行 Confirmation。

测试环境仍可以：

```text
Fake Dig
↓
ConfirmationStore
```

完成 Confirmation 单元测试。

---

# 三十九、真实 Minecraft E2E

不要只使用 flying-squid。

至少增加一个：

```text
真实 Minecraft Java Server Smoke
```

测试环境：

```text
127.0.0.1:25565
```

建议使用已有局域网世界。

---

# 四十、真实 Dig Smoke

准备：

```text
Bot
玩家
一个普通石块
```

步骤：

```text
Bot Join
↓
确认 Bot 已看到目标玩家
↓
World Snapshot
↓
确认目标 Block = stone
↓
触发 minecraft_dig
↓
Confirmation
↓
用户确认
↓
再次 minecraft_dig
↓
Bot.dig
↓
等待完成
↓
服务器世界查询
↓
确认 Block = air
```

---

# 四十一、STOP Smoke

真实服务器：

```text
开始 Dig 一个明显需要数秒的方块
↓
马上 STOP
↓
Action = CANCELLED
↓
stopDigging()
↓
确认未继续破坏
```

不要只验证返回值。

---

# 四十二、Block Change WorldPerception Smoke

真实服务器：

```text
stone
↓
dig
↓
air
```

然后：

```text
WorldPerception
```

必须能够检测：

```text
removed block
```

或对应语义世界变化。

---

# 四十三、E2E Test A

```text
join
↓
world
↓
minecraft_dig
↓
confirmation_required
↓
no block broken
```

---

# 四十四、E2E Test B

```text
confirm
↓
same arguments
↓
dig
↓
RUNNING
↓
SUCCEEDED
↓
block_after = air
```

---

# 四十五、E2E Test C

```text
confirm
↓
block changes before execution
↓
minecraft.block_changed
↓
no digging
```

---

# 四十六、E2E Test D

```text
confirm
↓
expected block mismatch
```

必须：

```text
mismatch
```

不能执行。

---

# 四十七、E2E Test E

```text
dig RUNNING
↓
STOP
↓
CANCELLED
↓
stopDigging
```

---

# 四十八、E2E Test F

```text
dig
↓
disconnect
↓
CANCELLED
↓
cleanup
↓
runtime alive
```

---

# 四十九、E2E Test G

```text
dig
↓
timeout
↓
TIMEOUT
↓
cleanup
```

---

# 五十、E2E Test H

```text
untrusted Minecraft player
↓
request dig
↓
user_not_trusted
↓
zero Service action
```

---

# 五十一、事件验证

成功：

```text
minecraft.action.started
minecraft.action.completed
```

取消：

```text
minecraft.action.started
minecraft.action.cancelled
```

失败：

```text
minecraft.action.started
minecraft.action.failed
```

超时：

```text
minecraft.action.started
minecraft.action.timeout
```

终态必须最多一个。

---

# 五十二、World Event 验证

成功 Dig 后：

不得只有：

```text
Action completed
```

还应该观察：

```text
WorldPerception
↓
near diff
↓
真实方块 removal
```

确认 Phase 2.1 / 3A 的检测能力已经开始服务于行动层。

---

# 五十三、性能

单方块 Dig 不应：

- 重新扫描整个世界
- 重新建立全局地图
- 让 raw snapshot 进入 LLM
- 启动新的长期 Agent Loop

保持：

```text
Raw Snapshot ≈ 45～51KB
Semantic ≈ 1.1KB
LLM summary ≈ 249B
```

级别。

---

# 五十四、Memory

本阶段不要写：

```text
“这里有一个石头”
```

进长期 Minecraft Memory。

只更新：

```text
当前世界状态
当前 Action
最近 Action
```

永久 POI / 地图记忆继续留到后面。

---

# 五十五、Action Activity

成功时：

```text
activity =
“刚挖掉了 minecraft:stone”
```

可以进入 MinecraftAgentContext。

但不要自动写长期记忆。

---

# 五十六、不要实现“挖掉之后自动拾取”

本阶段：

```text
dig
```

只负责：

> 破坏方块。

不要自动：

```text
walk to drop
pickup
inventory
```

这些属于后续阶段。

---

# 五十七、不要自动装备工具

例如铁矿需要镐：

不要：

```text
Dig
↓
发现需要镐
↓
找镐
↓
装备
↓
继续
```

本阶段只允许：

```text
当前手持工具
+
bot.canDigBlock()
+
bot.dig()
```

失败即可。

---

# 五十八、允许玩家自己准备工具

这样第一版行为足够简单：

```text
玩家给罐头一把石镐
↓
罐头
↓
可以挖 stone / coal 等
```

但：

> 不做工具管理系统。

---

# 五十九、生产 Tool Registry

最终：

```text
minecraft_world      READ
minecraft_chat       SAFE
minecraft_look_at    SAFE
minecraft_move_to    LOW
minecraft_follow     LOW
minecraft_stop       SAFE
minecraft_dig        MEDIUM
```

暂时仍然没有：

```text
place
attack
craft
inventory
container
```

---

# 六十、Policy 顺序

必须继续严格：

```text
1. registered?
2. Minecraft enabled?
3. tool enabled?
4. online?
5. risk flag?
6. USER turn?
7. trusted player?
8. busy?
9. confirmation?
10. validate target / execute
```

注意：

> **Confirmation 是授权，不是代替 Action Validation。**

即使确认成功：

```text
block_changed
block_not_diggable
block_too_far
offline
busy
```

仍然必须拒绝。

---

# 六十一、Confirmation 消费之后

只有：

```text
Confirmation CONSUMED
```

然后：

```text
Action Validation
```

最后：

```text
MinecraftService.dig()
```

不要：

```text
consume confirmation
↓
直接执行
```

---

# 六十二、Tool Result

第一次：

```json
{
  "ok": false,
  "error": {
    "code": "minecraft.confirmation_required",
    "message": "这个 Minecraft 动作需要用户确认"
  },
  "confirmation": {
    "confirmation_id": "cfm_xxx",
    "tool": "minecraft_dig",
    "risk": "MEDIUM",
    "summary": "挖掉 minecraft:stone（120,64,-230）",
    "expires_at": "...",
    "status": "PENDING"
  }
}
```

确认后的执行：

```json
{
  "ok": true,
  "action": "dig",
  "action_id": "act_xxx",
  "status": "RUNNING"
}
```

完成事件：

```json
{
  "action": "dig",
  "action_id": "act_xxx",
  "status": "SUCCEEDED",
  "result": {
    "block_before": "minecraft:stone",
    "block_after": "minecraft:air"
  }
}
```

---

# 六十三、错误不得伪装成功

绝不允许：

```text
Dig failed
↓
回复用户：
“已经挖掉了”
```

必须根据真实 Action / World Result 回答。

例如：

> “没挖掉，那个方块已经不是石头了。”

或者：

> “我够不到那个方块。”

---

# 六十四、自然语言测试

至少：

### Test 1

用户：

> 罐头，把我面前这个石头挖掉。

预期：

```text
world
→ dig
→ confirmation_required
```

---

### Test 2

用户：

> 确认。

预期：

```text
dig same args
→ execute
```

---

### Test 3

用户：

> 确认

但上一条 Confirmation 已过期。

预期：

```text
confirmation_expired
```

不能执行。

---

### Test 4

用户：

> 把刚才那个挖掉。

但模型给了不同坐标。

预期：

```text
confirmation_mismatch
```

---

### Test 5

Minecraft 玩家 Steve：

> 罐头，把这个挖掉。

预期：

```text
minecraft.user_not_trusted
```

无 Dig。

---

# 六十五、不得出现自动连续挖矿

明确增加测试：

```text
test_dig_does_not_chain_into_second_block
```

挖一个 block 后：

不得：

```text
LLM
→ dig
→ another dig
```

除非用户开始新的明确回合。

---

# 六十六、不得出现自主 Dig

主动回合：

```text
TurnOrigin.INITIATIVE
```

即使模型调用：

```text
minecraft_dig
```

也必须：

```text
minecraft.action_not_allowed
```

不会创建 Confirmation。

---

# 六十七、Confirmation 不得被 SYSTEM 预创建后消费

保留 Phase 4A 的安全不变量：

```text
SYSTEM
BACKGROUND
INITIATIVE
```

不得消费 Confirmation。

只有：

```text
USER
```

可以。

---

# 六十八、完整安全断言

必须增加生产注册表级断言：

正式生产 Tool 必须仅有：

```text
world
chat
look_at
move_to
follow_player
stop
dig
```

不存在：

```text
place
attack
craft
inventory
container
```

也不存在：

```text
minecraft_test_confirmation
```

这种生产授权动作。

---

# 六十九、文件结构建议

建议新增：

```text
app/tools/builtins/minecraft_dig.py
```

或者遵循项目当前 builtin Tool 组织方式。

Runtime：

```text
minecraft_runtime/runtime.js
```

新增：

```text
dig
```

Action Runtime：

继续复用：

```text
ActionRuntime
```

不要创建第二套 Action Manager。

---

# 七十、Service

新增：

```python
async def dig(
    self,
    x: float,
    y: float,
    z: float,
    expected_block: str,
) -> dict:
```

Service 负责：

- 类型校验
- 配置校验
- Runtime API
- 稳定错误转换

不负责：

- Minecraft 视觉
- Pathfinder
- LLM 判断

---

# 七十一、Runtime Endpoint

新增：

```http
POST /minecraft/dig
```

请求：

```json
{
  "x": 120,
  "y": 64,
  "z": -230,
  "expected_block": "minecraft:stone"
}
```

Runtime 必须再次验证：

- block exists
- block name
- canDigBlock
- online

Service 和 Runtime 都可以做基本参数验证。

---

# 七十二、不要让 HTTP Endpoint 绕过 Tool Policy

WebUI/Service 直接调用：

```text
minecraft.dig
```

可以用于开发测试。

但真正的：

```text
LLM → Tool
```

必须仍经过：

```text
MinecraftActionPolicy
Confirmation
```

---

# 七十三、真实服务器是本阶段硬门禁

这次不要只靠 flying-squid。

使用已有：

```text
127.0.0.1:25565
```

真实 Minecraft Java 局域网服务器完成：

```text
join
↓
world snapshot
↓
target stone
↓
confirmation
↓
dig
↓
block actually broken
↓
world perception sees removal
```

必须至少 PASS 一次。

---

# 七十四、真实服务器 STOP

必须真实验证：

```text
dig
↓
立即 stop
↓
block 不被继续破坏
```

这条尤其重要。

---

# 七十五、真实服务器 Block Changed

准备：

```text
stone
```

建立 Confirmation。

然后玩家自己把它换成：

```text
dirt
```

再让罐头执行。

必须：

```text
minecraft.block_changed
```

不破坏 dirt。

---

# 七十六、全量回归

必须通过：

```text
pytest
ruff
ruff format --check
mypy
vitest
vue-tsc
Playwright
Node npm test
Node E2E
GitHub Actions 双 job
```

特别回归：

- Phase 4A Confirmation
- Minecraft Chat
- Phase 3E Intent
- Phase 3D Follow
- Phase 3C Move
- Phase 3B Action Runtime
- WorldPerception

---

# 七十七、完成定义

只有全部满足：

```text
[ ] Phase 4A create_test 安全边界已硬化
[ ] minecraft_dig = MEDIUM
[ ] Tool 正式注册
[ ] 只允许单个方块
[ ] expected_block 必填
[ ] Confirmation hash 包含完整参数
[ ] 用户第一次请求不会执行
[ ] 用户确认后才执行
[ ] Confirmation 只能 USER Turn 消费
[ ] trusted Minecraft player 生效
[ ] block_changed 会拒绝
[ ] block_not_diggable 会拒绝
[ ] block_too_far 会拒绝
[ ] canDigBlock 校验
[ ] 不自动 move
[ ] 不自动 equip
[ ] 不自动 pickup
[ ] 不自动连续 dig
[ ] ActionRuntime exclusive
[ ] stopDigging cleanup
[ ] timeout cleanup
[ ] disconnect cleanup
[ ] action event 正常
[ ] WorldPerception 检测真实 block removal
[ ] LLM 获得真实 Action Result
[ ] flying-squid E2E
[ ] 真实 Minecraft Java Server Smoke PASS
[ ] STOP Smoke PASS
[ ] block_changed Smoke PASS
[ ] Phase 1～4A 全回归通过
[ ] CI 双 job 全绿
```

最终标记：

```text
PHASE 4B COMPLETE
READY FOR PHASE 4C
```

输出：

1. 修改文件清单
2. Phase 4A Debug Confirmation 安全修复
3. minecraft_dig Tool Schema
4. Risk / Confirmation 流程
5. Target Block Validation
6. Mineflayer Dig 实现
7. Action 生命周期
8. STOP / cleanup
9. WorldPerception 联动
10. LLM 自然语言 E2E
11. flying-squid E2E
12. 真实 Minecraft Java Smoke
13. 性能观察
14. 已知限制
15. Phase 4C readiness

---

# 七十八、Phase 4C 仍然不要马上做复杂建造

4B 完成后再做：

```text
Phase 4C
minecraft_place
```

但同样只做：

> **放置一个指定方块。**

这样你就会得到第一套完整的：

```text
读世界
   ↓
确认目标
   ↓
破坏方块
   ↓
世界变化
   ↓
重新感知
```

然后：

```text
放置方块
   ↓
世界变化
   ↓
重新感知
```

最后再把 `dig + place` 组合起来，才进入：

> **“罐头开始真正操作 Minecraft 环境。”**

目前正式状态我会定为：

```text
Phase 4A  ✅
   │
   ├─ Confirmation ✅
   ├─ Minecraft Chat USER Bridge ✅
   ├─ Trusted Player ✅
   └─ 防循环 ✅
   
Phase 4B  🚀
   │
   └─ 单方块 Dig
```

而这一次和之前最大的不同是：**4B 会第一次让 LLM 的 Tool Call 真正改变外部世界。** 所以我宁愿把 `expected_block + confirmation + block_changed + 真实 Java Server Smoke` 四道门一次性做好，也不要为了“能挖”而把它们简化掉。 (｀・ω・´)b