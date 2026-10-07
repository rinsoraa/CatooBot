基于当前仓库 `acc93bb` 开始 Phase 4D。

先完整阅读当前：
- Phase 4C `minecraft_inventory`
- Phase 4C `minecraft_place`
- Phase 4B `minecraft_dig`
- `MinecraftActionPolicy`
- `ConfirmationStore`
- `ActionRuntime`
- `minecraft_actions.py`
- `MinecraftService`
- `runtime_client.py`
- WebUI Minecraft 页面
- `smoke_real_server.js`
- 现有 inventory/place/dig 测试

不要按旧阶段报告猜测当前接口。

---

# 一、Phase 4D 目标

实现最小的 Minecraft 背包写操作：

```text
minecraft_equip
minecraft_inventory_move
```

目标闭环：

```text
minecraft_inventory
        ↓
发现物品
        ↓
minecraft_equip / minecraft_inventory_move
        ↓
真实 inventory state 改变
        ↓
minecraft_inventory 再读
        ↓
后续 minecraft_place 可以直接使用
```

本阶段重点是：

> **让 CatooBot 能明确、可验证地整理自己的背包，并主动把一个指定物品拿到手上。**

---

# 二、严格限制范围

本阶段禁止：

- openContainer
- chest
- barrel
- shulker
- furnace
- villager
- trading
- crafting
- pickup item
- drop item
- toss
- auto loot
- 自动整理整个背包
- 自动排序
- 自动寻找“最优”槽位
- 自动补货
- 自动寻找物品
- 自动从容器拿物品
- 自动移动多个 stack
- 自动批量搬运
- 任何 World 修改
- 自动触发 place / dig

特别注意：

**4D 不实现 Container。**

Container 放到 Phase 4E。

---

# 三、风险模型

Inventory 写操作本身虽然不直接修改 Minecraft 世界方块，但它会改变真实角色状态，并且会直接影响后续世界动作。

因此本阶段统一按：

```text
minecraft_equip          = MEDIUM
minecraft_inventory_move = MEDIUM
```

处理。

保持：

```text
allow_medium = false
```

默认值不变。

两者都必须：

```text
USER Turn
+
explicit intent
+
trusted player（Minecraft Chat 来源时）
+
MEDIUM confirmation
```

禁止：

```text
INITIATIVE
BACKGROUND
SYSTEM
```

绕过确认。

不要因为“只是背包”就放宽 TurnOrigin。

---

# 四、minecraft_equip

新增：

```text
minecraft_equip
```

作用：

> 把一个已经存在于自身 inventory 中的、明确指定的物品装备到指定位置。

第一版只支持：

```text
destination = hand
```

不要实现：

```text
head
torso
legs
feet
off-hand
```

这些以后再做。

Mineflayer 当前公开 API 提供：

```js
bot.equip(item, destination)
```

其中 `destination` 包括 `hand`、盔甲槽和 `off-hand`。本阶段只开放 `hand`。

---

# 五、minecraft_equip 参数

建议：

```json
{
  "item": "minecraft:dirt"
}
```

不要让 LLM 指定：

```text
slot
itemId
metadata
NBT
internal numeric id
```

原因：

> 4D 第一版目标是“把哪一个物品拿到手里”，不是让 LLM 操作 Minecraft 内部槽位模型。

`item`：

- 非空
- 字符串
- 支持 `dirt` == `minecraft:dirt`
- 内部统一标准化成 `minecraft:dirt`

---

# 六、Equip 的选择规则必须确定性

如果背包里有：

```text
dirt × 64
dirt × 12
```

不得让 LLM 再指定一个模糊 stack。

应该：

1. 枚举 inventory 中 `name` 匹配的 Item
2. 按当前 inventory slot 的稳定顺序选择第一个合法 stack
3. 调用 `bot.equip(item, "hand")`

不要：

- 随机选择
- 按数量最大选择
- 自动寻找“更方便”的 stack
- 自动改变 slot

并在结果中返回：

```json
{
  "item": "minecraft:dirt",
  "source_slot": 37,
  "destination": "hand"
}
```

---

# 七、Equip 执行前实时校验

执行时重新读取：

```text
bot.inventory
bot.heldItem
```

如果目标物品不存在：

```text
minecraft.item_not_found
```

如果 bot 已离线：

```text
minecraft.offline
```

如果已经手持目标物品：

可以直接返回成功语义：

```text
already_equipped = true
```

但必须再次读取实际 `heldItem` 确认，而不能仅凭缓存判断。

---

# 八、Equip 后必须重新验证

`bot.equip()` resolve 不能直接作为最终事实。

执行后：

```text
actual = bot.heldItem
```

要求：

```text
actual != null
normalize(actual.name) == normalize(expected_item)
actual.count > 0
```

否则：

```text
minecraft.equip_unconfirmed
```

返回：

```json
{
  "expected": "minecraft:dirt",
  "actual": "minecraft:sand"
}
```

---

# 九、minecraft_inventory_move

增加：

```text
minecraft_inventory_move
```

作用：

> 将一个指定物品的指定数量，从一个明确 inventory slot 移动到另一个明确 slot。

第一版必须是：

```text
一个物品
一个 source slot
一个 destination slot
一个 count
```

不允许：

```text
“把所有东西整理一下”
“把泥土放到快捷栏”
“把背包优化一下”
```

LLM 必须给出明确槽位。

---

# 十、Move 参数

建议：

```json
{
  "source_slot": 37,
  "destination_slot": 0,
  "item": "minecraft:dirt",
  "count": 1
}
```

其中：

```text
source_slot
destination_slot
```

必须是整数。

`count`：

```text
integer >= 1
```

`item`：

```text
non-empty string
```

全部 `additionalProperties=false`。

---

# 十一、为什么必须指定 source_slot

当前 `minecraft_inventory` 已经提供：

```text
items[{name,count}]
```

但它是聚合视图。

4D 需要第一次引入：

```text
inventory slot
```

但只在“写操作参数”里出现。

不要因此把 raw slots 全部暴露给 LLM。

也就是说：

```text
minecraft_inventory
```

仍然保持 Phase 4C 的最小切片：

```text
online
selected_hotbar_slot
held_item
items
```

**不能为了 inventory_move 修改其输出，让 LLM 获取原始 slot/NBT/window。**

---

# 十二、Slot 边界

先阅读当前 Mineflayer 版本实际 inventory slot 语义。

不要自己假设：

```text
0 = 第一格快捷栏
```

而应该：

- 使用当前 Mineflayer 真实 inventory slot API
- 在 Service/runtime 层明确 slot 范围
- 测试覆盖边界

如果当前 CatooBot 已经存在 slot 映射 helper，复用它。

不要新建第二套 slot 编号系统。

---

# 十三、Move 的实时校验

执行前读取真实 inventory。

必须检查：

### source

```text
source slot 存在
```

### item

```text
source item.name == expected item
```

### count

```text
source item.count >= requested count
```

### target

必须确认 destination slot 的当前状态。

如果目标 slot：

```text
为空
```

允许。

如果目标 slot：

```text
同名且可以合法堆叠
```

允许。

如果：

```text
不同物品占用
```

拒绝：

```text
minecraft.destination_occupied
```

不要自动交换。

---

# 十四、禁止隐式交换

例如：

```text
slot 37 = dirt
slot 0  = sand
```

请求：

```text
dirt slot37 → slot0
```

不能偷偷变成：

```text
dirt ↔ sand
```

第一版明确：

> destination 非空且不是合法同物品堆叠 → 拒绝。

---

# 十五、Move 使用 Mineflayer 原生接口

优先使用当前 Mineflayer 公开 inventory API。

Mineflayer 当前提供：

```text
bot.transfer(...)
bot.clickWindow(...)
bot.putAway(...)
```

等能力。具体采用哪一个必须根据当前项目版本和 inventory/window 语义选择，不要为了方便直接构造底层 packet。

如果 `bot.transfer()` 能覆盖当前玩家 inventory 内部移动，优先考虑它，因为它本身就是为 source range → destination range 的物品转移设计的。

---

# 十六、Move 后必须重新读取 inventory

不能：

```text
move promise resolve
→ 直接宣布成功
```

必须：

```text
move
↓
重新读取 source
重新读取 destination
↓
验证最终状态
```

成功至少要求：

```text
source count 减少 count
destination 增加 count
```

但考虑 Minecraft stack 合并规则：

不要硬编码“destination 一定增加 count”。

应该根据 source/destination 的最终真实状态计算结果。

---

# 十七、结果结构

Equip：

```json
{
  "item": "minecraft:dirt",
  "destination": "hand",
  "held_item": {
    "name": "minecraft:dirt",
    "count": 12
  },
  "already_equipped": false
}
```

Move：

```json
{
  "item": "minecraft:dirt",
  "source_slot": 37,
  "destination_slot": 0,
  "requested_count": 1,
  "source_after": {
    "name": "minecraft:dirt",
    "count": 11
  },
  "destination_after": {
    "name": "minecraft:dirt",
    "count": 2
  }
}
```

真实结果以重新读取后的 inventory 为准。

---

# 十八、Action Runtime

两个动作都必须复用：

```text
ActionRuntime
```

不要在 Service 里直接：

```text
await bot.equip(...)
await bot.transfer(...)
```

而绕过 runtime。

统一：

```text
Tool
 ↓
Agent Bridge
 ↓
Policy
 ↓
Service
 ↓
Runtime
 ↓
Mineflayer
```

---

# 十九、独占性

`minecraft_inventory`：

```text
NON_EXCLUSIVE
```

保持 Phase 4C 行为。

`minecraft_equip`：

```text
exclusive = true
```

`minecraft_inventory_move`：

```text
exclusive = true
```

原因：

> 正在移动/挖掘/放置时改变主手或 inventory，可能改变正在执行动作的物品语义。

禁止：

```text
move_to + equip
dig + inventory_move
place + inventory_move
follow + equip
```

并行。

---

# 二十、为什么 Equip 也必须 exclusive

即使：

```text
equip
```

本身不修改世界：

它改变：

```text
bot.heldItem
```

而后续 place/dig 对手持物品有实际依赖。

因此：

```text
equip
```

必须占用 foreground action。

---

# 二十一、Confirmation

复用现有 `ConfirmationStore`。

### equip

摘要：

```text
把 minecraft:dirt 拿到手里
```

### inventory_move

摘要：

```text
把 37 格的 minecraft:dirt ×1 移到 0 格
```

confirmation hash：

### equip

```text
tool
item
destination
```

### move

```text
tool
source_slot
destination_slot
item
count
```

任意字段改变：

```text
旧 confirmation 作废
新建 PENDING
```

执行前仍重新检查真实 inventory。

---

# 二十二、TurnOrigin

必须完整继承当前 Phase 3E.1 / 4A / 4B / 4C：

```text
USER
INITIATIVE
BACKGROUND
SYSTEM
```

只有：

```text
USER
```

才能满足 explicit intent。

Minecraft Chat 的 MEDIUM 操作继续受 trusted players 限制。

绝不能：

```text
origin_metadata
```

直接覆盖真正的 TurnOrigin。

---

# 二十三、Place 联动

这是 Phase 4D 一个非常重要的验收点。

测试链：

```text
minecraft_inventory
↓
发现 dirt 在 inventory
↓
minecraft_equip dirt
↓
confirmation
↓
成功
↓
minecraft_inventory
↓
held_item = dirt
↓
minecraft_place
```

这样才能证明：

> Inventory Control 真正成为 Minecraft 身体能力，而不是孤立的 demo。

---

# 二十四、Action Events

成功 equip：

```text
minecraft.action.completed
```

事件 result 应包含：

```text
item
destination
```

成功 inventory_move：

```text
minecraft.action.completed
```

result 包含：

```text
source_slot
destination_slot
requested_count
```

失败必须使用明确结构化错误。

不要在 LLM Context 里直接塞 raw inventory。

---

# 二十五、Activity

增加：

```text
equip = "刚把 {item} 拿到手里"
inventory_move = "刚把 {item} 从 {source} 格移到了 {destination} 格"
```

仍然遵守：

> activity 只陈述事实，不添加情绪或推测。

---

# 二十六、测试：Python

新增：

```text
tests/test_minecraft_equip_tool.py
tests/test_minecraft_inventory_move_tool.py
```

至少覆盖：

### equip

- schema
- missing item
- offline
- disabled
- allow_medium=false
- explicit intent
- trusted player
- confirmation_required
- confirmation consume
- argument mismatch
- item not found
- already equipped
- equip_unconfirmed

### inventory_move

- schema
- invalid source
- invalid destination
- invalid count
- item mismatch
- insufficient count
- destination occupied
- allowed stack merge
- confirmation
- mismatch confirmation
- successful flow
- failure flow

---

# 二十七、Node Runtime Tests

新增：

```text
minecraft_runtime/test/equip.test.js
minecraft_runtime/test/inventory_move.test.js
```

覆盖：

### Equip

A. item exists → equip

B. item missing

C. already equipped

D. equip rejection

E. post-equip verification

F. cancellation

G. timeout

H. race

### Move

A. empty destination

B. same item destination stack

C. occupied destination

D. insufficient source

E. item mismatch

F. transfer success

G. post-read verification

H. cancellation

I. timeout

J. race

---

# 二十八、flying-squid E2E

当前 flying-squid 没有 `/give`。

因此不要伪造成功。

可以：

- 给测试机器人注入 fake inventory
- 或通过已有测试 harness 构造 inventory state

必须分别验证：

```text
inventory read
equip
inventory_move
place prerequisite
```

真实服务器继续负责最终真实 Mineflayer 验证。

---

# 二十九、Real Java Smoke

扩展：

```text
minecraft_runtime/test/smoke_real_server.js
```

顺序：

```text
connect
↓
ensureIdle
↓
minecraft_inventory
↓
记录当前 held item / inventory
↓
找到一个真实存在的非手持物品
↓
minecraft_equip
↓
重新 inventory
↓
确认 held_item 改变
↓
minecraft_inventory_move
↓
重新 inventory
↓
确认 source / destination 改变
↓
恢复测试前状态
↓
ensureIdle
↓
disconnect
```

---

# 三十、真实服务器必须尽量恢复环境

本次 Smoke 不应该长期污染你的 Minecraft 世界。

尤其：

```text
equip
```

结束后可以保留手持状态，但最好记录并恢复原 hotbar/hand 状态。

`inventory_move` 更应该：

```text
移动前记录
↓
测试
↓
恢复原位置
```

如果无法安全恢复：

```text
SKIPPED
```

也不要伪造。

---

# 三十一、测试物品选择

不要硬编码：

```text
minecraft:dirt
```

应该动态选择一个真实 inventory item：

要求：

```text
count >= 1
```

并且不是当前手持物品。

优先：

```text
非空普通可堆叠物品
```

这样不会因为某次 Smoke 前背包不同而失败。

---

# 三十二、Real Server 六层证据

Equip 至少：

```text
1. HTTP → RUNNING
2. Action completed
3. held_item before
4. held_item after
5. minecraft_inventory re-read
6. expected item == actual held item
```

Inventory Move 至少：

```text
1. HTTP → RUNNING
2. Action completed
3. source_before / destination_before
4. source_after / destination_after
5. minecraft_inventory re-read
6. requested transition actually occurred
```

不要只看 Action Runtime completed。

---

# 三十三、STOP

Equip / inventory_move 都是短操作。

因此：

```text
Real STOP = 非硬门禁
```

但单元测试必须覆盖：

```text
cancel
timeout
race
cleanup exactly once
```

真实服务器如果动作在 STOP 前已经完成：

```text
SKIPPED
```

不能伪造 CANCELLED PASS。

---

# 三十四、Container 明确留到 Phase 4E

不要在本阶段实现：

```text
openContainer
withdraw
deposit
click window
container close
chest state
```

尽管 Mineflayer 已提供：

```text
openContainer
clickWindow
transfer
closeWindow
```

这些属于另一套“窗口状态机”。

Phase 4E 再单独处理。

---

# 三十五、WebUI

Minecraft 页面增加：

```text
Inventory Control
```

包含：

### Equip Test

```text
Item
当前 held item
装备
STOP
```

### Move Test

```text
Source Slot
Destination Slot
Item
Count
MOVE
STOP
```

但：

> 开发者测试入口不得绕过 MEDIUM confirmation。

保持 Phase 4C 的规则。

---

# 三十六、文案

同步：

```text
config/config.example.yaml
app/web/config_registry.py
Minecraft.vue
docs/MINECRAFT_PHASE4D.md
CHANGELOG.md
```

说明：

```text
minecraft_inventory
```

仍是 SAFE / 只读。

新增：

```text
minecraft_equip = MEDIUM
minecraft_inventory_move = MEDIUM
```

默认：

```yaml
allow_medium: false
```

保持不变。

不要写成：

```text
“可以自由整理背包”
```

应该强调：

```text
一次只操作一个明确物品/槽位
```

---

# 三十七、验收门禁

Phase 4D 必须同时满足：

```text
pytest 全绿
ruff check
ruff format --check
mypy
Node 全绿
Vitest 全绿
Playwright 全绿
WebUI build 全绿
```

并且 Real Server：

```text
REAL SERVER: PASS
```

至少：

```text
inventory read = PASS
equip = PASS
held item reread = PASS
inventory move = PASS
source/destination reread = PASS
environment restore = PASS
ensureIdle = PASS
```

如果 equip 或 move 只在 fake bot 上通过：

```text
Phase 4D = BLOCKED
```

不要用单元测试代替真实 Mineflayer 证据。

---

# 三十八、最终报告格式

沿用 4C：

```text
Phase 4D = PASS / BLOCKED

commit:
<sha>

新增工具:
minecraft_equip
minecraft_inventory_move

risk:
equip = MEDIUM
inventory_move = MEDIUM

自动化:
pytest = ...
Node = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
inventory = PASS
equip = PASS
held item reread = PASS
inventory move = PASS
source/destination = PASS
environment restore = PASS
REAL SERVER: PASS
```

任何没有真正测试的项目：

```text
SKIPPED
```

不要伪造 PASS。

---

# 三十九、最终架构目标

Phase 4D 完成后，最小自然行为链应该成立：

```text
“罐头，把泥土放到手里”
        ↓
minecraft_inventory
        ↓
minecraft_equip("minecraft:dirt")
        ↓
confirmation
        ↓
ActionRuntime
        ↓
held_item = dirt
```

然后：

```text
“在我脚下放一块”
        ↓
minecraft_world
        ↓
minecraft_place
        ↓
confirmation
        ↓
air → dirt
```

这才算真正把：

```text
Perception
+
Inventory
+
Action
+
Confirmation
+
World Verification
```

串起来。

不要在 Phase 4D 提前进入 Container / Craft / Combat / 自动建造。