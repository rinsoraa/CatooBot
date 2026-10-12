基于当前仓库最新提交 `a78369b` 开始 Phase 4C。

先完整阅读当前 Minecraft 架构、Phase 4B `minecraft_dig`、ActionRuntime、Agent Bridge、MinecraftService、WorldPerception、ConfirmationStore、WebUI 与现有 smoke/e2e 测试。

不要根据旧阶段报告猜实现。

---

# 一、Phase 4C 目标

实现 Minecraft 中最小可靠的：

1. `minecraft_inventory`：只读 inventory slice
2. `minecraft_place`：一次只放置一个方块

最终闭环：

```text
LLM
 ↓
minecraft_inventory / minecraft_world
 ↓
minecraft_place
 ↓
MinecraftActionPolicy
 ↓
MEDIUM confirmation
 ↓
MinecraftService
 ↓
Action Runtime
 ↓
Mineflayer placeBlock(...)
 ↓
Minecraft Server
 ↓
验证目标位置
 ↓
WorldPerception 检测新增方块
```

这是 Phase 4B：

```text
dig one block
```

的对称实现：

```text
place one block
```

---

# 二、绝对不要扩大范围

本阶段禁止实现：

- 自动导航到目标
- 自动寻找可放置位置
- 自动寻找 reference block
- 自动选择最佳放置面
- 自动切换 hotbar
- 自动 equip
- 自动从 inventory 取物
- 自动补货
- 自动拾取
- 自动合成
- 自动挖材料
- 连续放多个方块
- 建筑规划
- 建筑循环
- schematic
- bridge
- redstone
- container
- craft
- attack
- survival 自动资源管理

本阶段的原则：

> **当前手里有什么，就只能明确要求放什么。**
> 
> **目标、方向、手持物品都必须明确。**
> 
> **一次最多修改一个目标方块。**

---

# 三、先增加 minecraft_inventory 只读能力

当前 world snapshot 已经有：

```text
held_item
```

但这不足以让 LLM 判断“我手里有什么 / 背包里有什么”。

增加一个真正只读的：

```text
minecraft_inventory
```

## 风险

```text
SAFE
```

不需要：
- USER explicit intent
- trusted player
- confirmation

但按照当前 Minecraft policy，它仍然需要 Minecraft online。

不要把 inventory 放入 OFFLINE_TOOLS。

---

# 四、Inventory Slice 必须最小化

不要把 Mineflayer 原始 inventory/window/slot/NBT 全部暴露给 LLM。

建议返回：

```json
{
  "ok": true,
  "online": true,
  "selected_hotbar_slot": 0,
  "held_item": {
    "name": "minecraft:dirt",
    "count": 12
  },
  "items": [
    {
      "name": "minecraft:dirt",
      "count": 12
    },
    {
      "name": "minecraft:sand",
      "count": 24
    }
  ]
}
```

其中：

- `selected_hotbar_slot`
- `held_item.name`
- `held_item.count`
- inventory 中按物品名聚合后的 `name + count`

即可。

不要暴露：
- slot 全量原始结构
- NBT
- internal item id
- window 对象
- container state
- armor slots（本阶段没有用途）
- cursor state

如当前仓库 Mineflayer 版本已有稳定 inventory API，优先使用现有公开 API，不自己读取底层 packet。

Mineflayer 当前公开接口已有 `heldItem`、`quickBarSlot`、`inventory` 等能力。

---

# 五、minecraft_inventory 必须遵循 minecraft_world 的结构

参考：

```text
minecraft_world
```

的只读 Tool 结构。

必须经过：

```text
bridge_from(context)
→ policy.check(...)
→ Service
→ ToolResult
```

不要直接让 Tool 触碰 Mineflayer。

需要有：

- ToolMetadata
- input_schema = 空对象
- output_schema
- description
- when_to_use
- when_not_to_use
- limitations
- tests

建议使用：

```text
name = minecraft_inventory
risk = SAFE
category = information
```

---

# 六、minecraft_place 的工具定义

增加：

```text
minecraft_place
```

风险：

```text
MEDIUM
```

和：

```text
minecraft_dig
```

完全一样：

```text
allow_medium == false
→ disabled

allow_medium == true
→ 仍然需要 USER explicit intent
→ MEDIUM confirmation
→ confirmation 绑定 user/session/tool/arguments hash
```

---

# 七、minecraft_place 的参数设计

不要让 LLM 同时填写 reference block 和 target block 两套容易互相矛盾的坐标。

第一版使用：

```json
{
  "x": 100,
  "y": 64,
  "z": 100,
  "face": "up",
  "expected_item": "minecraft:dirt"
}
```

含义：

> 在 `(100,64,100)` 放置一个 `minecraft:dirt`，放置面为 `up`。

其中：

```text
target = (x, y, z)
reference = target - face_vector
```

例如：

```text
face = up
target = (100,64,100)
reference = (100,63,100)
```

然后 runtime 使用：

```text
bot.placeBlock(referenceBlock, faceVector)
```

Mineflayer 的接口正是通过 reference block + 六向 face vector 确定目标位置，并在服务器确认后结束 Promise。

---

# 八、face 只允许六个值

严格使用 enum：

```text
up
down
north
south
east
west
```

转换成：

```js
up    → (0, 1, 0)
down  → (0,-1, 0)
north → (0, 0,-1)
south → (0, 0, 1)
east  → (1, 0, 0)
west  → (-1,0, 0)
```

不要接受任意 `{dx,dy,dz}`。

不要接受浮点方向。

不要让 LLM 任意构造 vector。

---

# 九、expected_item 的语义

这里非常重要。

`expected_item` 不是：

> “我希望最终出现什么方块”

而是：

> **“我确认当前主手必须拿着这个物品，然后才能执行放置。”**

例如：

```json
"expected_item": "minecraft:dirt"
```

执行前 runtime 必须实时检查：

```text
bot.heldItem != null
bot.heldItem.name == expected_item
bot.heldItem.count > 0
```

否则拒绝。

建议错误：

```text
minecraft.held_item_missing
minecraft.held_item_changed
```

不要自动 equip。

不要：

```text
bot.equip(...)
```

不要切 hotbar。

---

# 十、第一版只允许“空目标格”

为了避免第一阶段卷入 Minecraft replaceable block 语义：

目标：

```text
minecraft_place
```

第一版只允许：

```text
target block == air
```

如果目标不是 air：

```text
minecraft.target_occupied
```

直接拒绝。

不要自动覆盖：

- grass
- snow layer
- water
- lava
- vines
- tall grass
- flowers
- replaceable blocks

这些以后单独设计。

---

# 十一、reference block 校验

执行前实时读取：

```text
referenceBlock = bot.blockAt(referencePosition)
targetBlock = bot.blockAt(targetPosition)
```

要求：

### target

```text
targetBlock != null
targetBlock.name == air
```

### reference

```text
referenceBlock != null
referenceBlock.name != air
```

如果 reference 不存在：

```text
minecraft.reference_block_missing
```

如果 target 不为空：

```text
minecraft.target_occupied
```

如果世界区块未加载：

```text
minecraft.block_unavailable
```

不要猜。

---

# 十二、距离限制

和 dig 类似，第一版只允许近距离实际交互。

建议：

```text
PLACE_MAX_DISTANCE = 5
```

具体以当前 4B runtime 常量/风格为准，避免重复常量。

计算 bot eyes 到：

```text
target block center
```

或者按照当前项目 dig 的交互距离语义保持一致。

超过距离：

```text
minecraft.too_far
```

不要自动 move_to。

---

# 十三、整数坐标

Minecraft block position 必须是整数。

因此：

```text
x
y
z
```

必须：

```text
finite
integer
world boundary valid
```

不要接受：

```text
100.5
64.2
```

直接拒绝。

---

# 十四、Action Runtime

新增：

```text
place
```

动作。

必须复用当前：

```text
ActionRuntime
```

生命周期：

```text
IDLE
→ QUEUED
→ RUNNING
→ SUCCEEDED / FAILED / CANCELLED / TIMEOUT
```

不得直接在 runtime.js route 中：

```text
await bot.placeBlock(...)
```

然后绕过 ActionRuntime。

---

# 十五、Place Action 的性质

`minecraft_place` 属于：

```text
exclusive = true
risk = MEDIUM
```

不能和：

```text
move_to
follow_player
dig
```

并行。

`minecraft_chat`
`minecraft_world`
`minecraft_inventory`
`minecraft_stop`

按照现有 non-exclusive / safety 规则处理。

---

# 十六、Place 的执行流程

Action Runtime `start`：

### Step 1

检查：

```text
bot online
```

### Step 2

解析：

```text
target
face
reference
expected_item
```

### Step 3

实时获取：

```text
heldItem
referenceBlock
targetBlock
```

### Step 4

验证：

```text
expected_item == heldItem.name
heldItem.count > 0
targetBlock == air
referenceBlock != air
distance <= max
```

### Step 5

保存本次 action 的不可变快照：

```json
{
  "target": {"x":100,"y":64,"z":100},
  "reference": {"x":100,"y":63,"z":100},
  "face": "up",
  "item_before": {
    "name": "minecraft:dirt",
    "count": 12
  },
  "block_before": "air",
  "reference_before": "minecraft:grass_block"
}
```

然后再进入 wait / execute。

---

# 十七、Place Action wait

调用：

```js
await bot.placeBlock(referenceBlock, faceVector)
```

不要自行模拟右键。

不要直接发 packet。

不要使用坐标 teleport。

Mineflayer 当前 API 已经将 reference block + face vector 的放置动作封装好，并在服务器确认放置后完成 Promise。

---

# 十八、成功后必须二次验证

和 4B 一样：

**不要把 `placeBlock()` Promise resolve 当成最终世界真相。**

执行完成后重新：

```text
actual = bot.blockAt(target)
```

要求：

```text
actual != null
actual.name == expected_item
```

成功返回：

```json
{
  "position": {"x":100,"y":64,"z":100},
  "block_before": "air",
  "block_after": "minecraft:dirt",
  "reference_block": "minecraft:grass_block",
  "face": "up",
  "item_before": {
    "name": "minecraft:dirt",
    "count": 12
  },
  "item_after_count": 11
}
```

---

# 十九、成功条件

Place 只有满足：

```text
Action Runtime completed
+
目标 block 从 air → expected_item
```

才能算成功。

如果 Promise resolve，但是 target 仍然是 air：

```text
minecraft.block_place_unconfirmed
```

绝不能报告成功。

---

# 二十、物品数量变化

成功后重新读取 held item / inventory。

如果：

```text
before count = 12
after count = 11
```

作为正常结果返回。

但不要硬编码“一定 -1”。

Creative / 特殊服务器 / modded 行为可能不同。

真正硬门禁仍然是：

```text
world target == expected block
```

---

# 二十一、Cancellation / STOP

必须像 dig 一样拥有 cleanup。

cleanup 至少：

```text
clearControlStates()
```

并确保：

```text
ActionRuntime terminal state
```

不会出现：

```text
cancelled + succeeded
```

双终态。

但是要承认：

> placeBlock 通常是短动作，真实 STOP 可能无法稳定抢在服务器确认之前。

所以：

- 单元测试必须验证 cancellation race
- flying-squid / mock runtime 必须验证 STOP/timeout cleanup
- Real Server smoke 如果动作快到无法抢占，可以 `SKIPPED`
- 绝不能伪造 PASS

---

# 二十二、Confirmation

沿用 Phase 4A / 4B 的 ConfirmationStore。

第一次：

```text
minecraft_place(
  x,
  y,
  z,
  face,
  expected_item
)
```

应该：

```text
confirmation_required
```

确认摘要必须明确：

```text
放置 minecraft:dirt
位置 (100,64,100)
方向 up
```

至少让用户知道：

```text
什么东西
放在哪里
```

确认 hash 必须包含：

```text
tool
x
y
z
face
expected_item
```

任何参数变化：

```text
旧 confirmation 失效
→ 新建 confirmation
```

confirmation 只负责授权。

执行前 runtime 仍然必须重新检查：

```text
target
reference
held item
distance
```

---

# 二十三、TurnOrigin / Trusted Player

保持现有 3E.1 / 4A / 4B 规则：

### QQ/WebUI USER

正常：

```text
USER
→ explicit intent
→ MEDIUM confirmation
```

### Minecraft Chat

仍然要求：

```text
trusted player
```

才能执行 MEDIUM。

### INITIATIVE / BACKGROUND / SYSTEM

不能执行 place。

不能因为：

```text
minecraft_explicit_intent
```

被伪造而绕过 TurnOrigin。

---

# 二十四、WorldPerception 联动

Place 成功后应该和 dig 对称：

```text
world diff
```

看到：

```text
air
→ placed block
```

不能要求 LLM 自己重新解释 raw snapshot。

确认链应该保持：

```text
Action Runtime result
+
WorldPerception world.changed
```

---

# 二十五、minecraft_place Tool description

必须明确：

```text
一次只放一个方块。
不会导航。
不会自动换工具。
不会自动 equip。
不会寻找放置面。
不会自动寻找 reference block。
不会自动补货。
不会连续建造。
必须先确认 minecraft_world / minecraft_inventory。
```

同时强调：

```text
expected_item 是当前主手物品的硬约束。
```

---

# 二十六、WebUI

可以增加最小开发测试入口，但不要绕过 MEDIUM confirmation。

建议：

```text
Minecraft → Tools
```

显示：

```text
minecraft_inventory
minecraft_place
```

Place Test 至少允许开发者填写：

```text
x
y
z
face
expected_item
```

显示：

```text
当前方块
reference block
当前手持物品
```

但真实 place 不能通过一个“开发者按钮”绕过确认。

和 Phase 4B 一样：

```text
developer endpoint
```

只能做参数检查 / 测试桩能力。

真正 world-changing place 仍然必须通过真实 confirmation 语义。

---

# 二十七、测试必须覆盖

## Python Tool

新增：

```text
tests/test_minecraft_inventory_tool.py
tests/test_minecraft_place_tool.py
```

覆盖：

- schema
- 错误参数
- offline
- disabled
- explicit intent
- trusted player
- allow_medium=false
- confirmation required
- confirmation consume
- argument mismatch
- stale confirmation
- held_item mismatch
- occupied target
- invalid face
- invalid coords

---

# 二十八、Agent Flow

新增 place confirmation flow：

```text
首次调用
→ confirmation_required

同参数 + USER confirmation
→ execute

修改 x
→ confirmation_invalid / new confirmation

修改 face
→ confirmation_invalid / new confirmation

修改 expected_item
→ confirmation_invalid / new confirmation
```

必须证明 confirmation hash 真正绑定全部 place 参数。

---

# 二十九、Node Runtime Tests

新增：

```text
minecraft_runtime/test/place.test.js
```

至少覆盖：

### A

成功 place：

```text
air → dirt
```

### B

target occupied：

```text
stone → reject
```

### C

held item mismatch

### D

held item missing

### E

reference missing

### F

too far

### G

invalid face

### H

block place unconfirmed

### I

cancelled cleanup

### J

timeout cleanup

### K

race protection

---

# 三十、Real Java Smoke

必须新增：

```text
place
```

真实服务器测试。

但 smoke 不得修改现有 Dig/Follow 的通过结论。

顺序：

```text
connect
→ move / follow prerequisite
→ ensureIdle
→ inventory read-only
→ 选择真实 held item
→ 找一个已知 air target + valid reference
→ place
→ completed
→ target block changed
→ inventory count re-read
→ WorldPerception changed
→ ensureIdle
→ disconnect
```

---

# 三十一、Real Server 测试要求

建议让 smoke 支持：

```text
SMOKE_PLACE_TARGET="x,y,z"
SMOKE_PLACE_FACE="up"
SMOKE_PLACE_ITEM="minecraft:dirt"
```

如果没有配置：

- 尝试寻找一个安全的 air target + valid reference
- 但不得破坏现有世界
- 不得自动大量放置
- 只放 1 个 block

如果没有合适目标：

```text
SKIPPED
```

不要假 PASS。

最好默认目标是：

```text
reference block 上方一格
```

这样验证语义最清楚。

---

# 三十二、真实 Place 的六层证据

和 4B 对齐。

成功必须尽量得到：

```text
1. HTTP → RUNNING / action_id
2. Action Runtime → completed
3. result.block_before = air
4. result.block_after = expected_item
5. real world snapshot → target = expected_item
6. WorldPerception → 检测到 air → placed block
```

另外记录：

```text
held item before
held item after
```

但不要把 inventory count 变化作为世界修改成功的唯一依据。

---

# 三十三、不要把 Real STOP 设为 4C 硬门禁

因为 place 是极短动作。

Real STOP 可以：

```text
PASS
```

或者：

```text
SKIPPED（动作在 STOP 前已完成）
```

但：

```text
单元测试 / mock
```

必须验证 cleanup 和 terminal race。

---

# 三十四、文档

同步：

```text
README
config/config.example.yaml
app/web/config_registry.py
WebUI Minecraft 页面
```

只在确实已有地方需要更新时修改。

必须保持：

```yaml
allow_medium: false
```

默认值不变。

描述应从：

```text
minecraft_dig
```

升级为：

```text
minecraft_dig / minecraft_place
```

不能改成“所有破坏/建设能力已开放”。

---

# 三十五、必须保持的架构边界

绝对禁止：

```text
LLM
 ↓
Mineflayer
```

必须保持：

```text
LLM Tool
 ↓
Agent Bridge
 ↓
Policy
 ↓
Service
 ↓
Action Runtime
 ↓
Mineflayer
```

Inventory 则：

```text
LLM Tool
 ↓
Agent Bridge / Policy
 ↓
Service
 ↓
Runtime read-only state
```

---

# 三十六、验收门禁

Phase 4C 不得因为 CI 绿就自动 PASS。

至少要求：

```text
pytest 全绿
ruff check
ruff format --check
mypy
Node 全绿
Vitest 全绿
Playwright 全绿
build 全绿
```

以及真实 Java Server：

```text
REAL SERVER: PASS
```

其中至少：

```text
inventory read-only
place RUNNING
place completed
air → expected block
real snapshot changed
WorldPerception changed
ensureIdle
```

全部通过。

如果真实 place 没有真正修改世界：

```text
Phase 4C = NOT PASS
```

不能用 unit test 替代。

---

# 三十七、最终报告

完成后只报告真实结果：

```text
Phase 4C = PASS / BLOCKED

commit:
<sha>

新增工具:
minecraft_inventory
minecraft_place

risk:
inventory = SAFE
place = MEDIUM

自动化:
pytest
Node
Vitest
Playwright
lint
mypy

Real Server:
inventory = PASS
place = PASS / SKIPPED
world change = PASS / ...
WorldPerception = PASS / ...
```

若真实 STOP 因动作过快：

```text
place STOP = SKIPPED
```

绝不伪造。

最后再检查当前仓库有没有把“工具数量”“本阶段没有放置能力”等旧文案残留，只有确实不准确时才修。