基于当前仓库最新基线 `689c8e3` 开始 Phase 4E。

先完整阅读当前：
- Phase 4B `minecraft_dig`
- Phase 4C `minecraft_place`
- Phase 4D `minecraft_inventory`
- Phase 4D `minecraft_equip`
- Phase 4D `minecraft_inventory_move`
- `ActionRuntime`
- `MinecraftService`
- `MinecraftAgentPolicy`
- `ConfirmationStore`
- `TurnOrigin`
- WebUI Minecraft 页面
- `smoke_real_server.js`
- 现有 Node / Python / WebUI 测试

不要按历史实现猜当前架构。

---

# 一、Phase 4E 目标

实现 Minecraft 最小 Container 能力：

```text
minecraft_container_inspect
minecraft_container_transfer
```

最终闭环：

```text
minecraft_container_inspect
        ↓
看到指定 Chest / Barrel 里的真实物品
        ↓
明确选择一个 container slot
        ↓
明确选择一个 player inventory slot
        ↓
minecraft_container_transfer
        ↓
MEDIUM confirmation
        ↓
ActionRuntime
        ↓
open container
        ↓
实时重新校验 container + inventory
        ↓
单槽、单物品、单数量 transfer
        ↓
重新读取 container + inventory
        ↓
close container
        ↓
确认最终状态
```

本阶段的核心目标：

> **让 CatooBot 能可靠地读取一个箱子/桶，并把一个明确的物品在“容器槽 ↔ 自己背包槽”之间移动一次。**

---

# 二、严格范围

本阶段只允许：

### 支持

```text
minecraft:chest
minecraft:barrel
```

并且：

> 只支持单方块容器。

### 明确禁止

```text
double chest
trapped chest
shulker box
hopper
dispenser
dropper
furnace
blast furnace
smoker
brewing stand
enchanting table
anvil
villager
horse/chest entity
minecart chest
trading
crafting table
container chaining
```

尤其：

**不要因为 `openContainer()` 能打开更多类型，就顺便支持。**

4E 的目标是建立 Container Window 生命周期，不是一次实现整个 Minecraft container API。

---

# 三、为什么不直接暴露 open_container

不要增加：

```text
minecraft_open_container
minecraft_close_container
```

这种长期状态型 LLM 工具。

正确设计：

```text
minecraft_container_inspect
```

内部：

```text
validate
→ openContainer
→ read
→ close
→ return snapshot
```

`minecraft_container_transfer` 内部：

```text
validate
→ openContainer
→ validate window
→ validate source/destination
→ transfer
→ reread
→ close
→ return result
```

这样：

> LLM 永远不会拥有一个“半开着的 container window 状态”。

这也是本阶段最重要的生命周期边界。

---

# 四、minecraft_container_inspect

风险：

```text
SAFE
```

但必须：

```text
online
```

并且：

```text
exclusive = true
```

原因：

> 虽然读取本身是只读，但打开真实 Minecraft Window 是有生命周期的客户端状态，不能和其他 foreground action 并发。

不能加入：

```text
NON_EXCLUSIVE_TOOLS
```

---

# 五、Container Inspect 参数

使用：

```json
{
  "x": 100,
  "y": 64,
  "z": 100
}
```

要求：

- x/y/z 必须 integer
- additionalProperties=false
- 世界边界验证
- 不允许坐标浮点
- 不自动导航

---

# 六、Container 类型验证

执行前：

```text
block = bot.blockAt(target)
```

必须明确验证：

```text
block.name === "minecraft:chest"
```

或者：

```text
block.name === "minecraft:barrel"
```

否则：

```text
minecraft.container_unsupported
```

不要通过“它看起来像一个 container”判断。

不要根据 `blockEntity` 猜类型。

---

# 七、禁止 Trapped Chest

如果当前方块是：

```text
minecraft:trapped_chest
```

明确拒绝。

原因：

> 打开 trapped chest 可能产生游戏机制副作用，因此 Phase 4E 第一版不能把它归入 SAFE inspect。

错误：

```text
minecraft.container_unsupported
```

不要特殊绕过。

---

# 八、距离限制

沿用当前 dig/place 的近距离交互思想。

建议：

```text
CONTAINER_MAX_DISTANCE = 5
```

具体配置和常量风格必须先阅读当前代码。

计算 bot eyes 到 container block center 的距离。

超过：

```text
minecraft.container_too_far
```

不要自动 move_to。

---

# 九、不自动导航

明确禁止：

```text
inspect
→ move_to
→ open
```

和：

```text
transfer
→ move_to
→ open
```

Container Action 只负责交互。

---

# 十、单方块限制

Chest 有一个特殊问题：

Minecraft 单箱和双箱都可能表现为：

```text
minecraft:chest
```

因此必须根据当前 Mineflayer Window 的真实结构判断。

不要硬编码：

```text
“chest 永远 27 格”
```

必须先读取当前版本 `window` 的实际 container/player inventory 边界。

如果发现：

```text
container inventory size > 27
```

即代表双箱或其他非本阶段结构：

```text
minecraft.container_unsupported
```

不要支持双箱。

Barrel 正常是单方块容器。

---

# 十一、Inspect 输出

LLM 只需要得到容器语义状态，不需要 raw Window。

建议：

```json
{
  "ok": true,
  "container": {
    "type": "minecraft:chest",
    "position": {
      "x": 100,
      "y": 64,
      "z": 100
    },
    "size": 27
  },
  "slots": [
    {
      "slot": 0,
      "name": "minecraft:dirt",
      "count": 12
    },
    {
      "slot": 7,
      "name": "minecraft:sand",
      "count": 32
    }
  ]
}
```

只返回非空 slot。

不要返回：

```text
window object
NBT
internal item id
mouse/cursor state
packet
player inventory slots
```

这样：

```text
minecraft_inventory
```

仍然保持原来的聚合五项结构。

而：

```text
minecraft_container_inspect
```

才提供容器 slot，因为 Container Transfer 必须知道具体 container slot。

---

# 十二、slot 上限

因为当前阶段只允许：

```text
single chest / barrel
```

最多 27 个 container slots。

不过不要把：

```text
27
```

写死成判断唯一来源。

应该从实际 Window 边界得出：

```text
containerSlotCount
```

然后要求：

```text
containerSlotCount === 27
```

才允许进入本阶段。

---

# 十三、Close 是硬要求

Inspect：

```text
open
→ read
→ close
```

无论成功还是失败，都必须：

```text
close
```

Transfer：

```text
open
→ execute
→ verify
→ close
```

无论：

```text
success
failure
exception
timeout
cancel
disconnect
```

都必须 best-effort close。

---

# 十四、Action Runtime cleanup

这是 4E 的核心测试点。

Action controller 必须保存：

```text
openedWindow
```

生命周期：

```text
openedWindow = null
```

打开成功：

```text
openedWindow = window
```

cleanup：

```text
if (openedWindow) {
    await closeWindow(openedWindow)
    openedWindow = null
}
```

cleanup 必须：

- 至多执行一次
- 可重复调用
- close 出异常不能阻止 terminal state
- disconnect 时不能 crash
- timeout 后不能留下打开窗口

不要把 `close()` 逻辑只写在 happy path。

---

# 十五、Cancellation race

重点测试：

```text
open
→ transfer
→ completed
```

和：

```text
open
→ STOP
```

同时发生。

必须保证：

```text
CANCELLED
```

和：

```text
SUCCEEDED
```

不会双终态。

沿用当前 ActionRuntime 的 race guard。

---

# 十六、minecraft_container_transfer

风险：

```text
MEDIUM
```

必须：

```text
allow_medium
+
USER
+
explicit intent
+
trusted player
+
confirmation
```

沿用 4B/4C/4D。

---

# 十七、Transfer 参数

建议：

```json
{
  "x": 100,
  "y": 64,
  "z": 100,
  "direction": "withdraw",
  "container_slot": 0,
  "inventory_slot": 9,
  "item": "minecraft:dirt",
  "count": 1
}
```

direction：

```text
withdraw
deposit
```

含义：

### withdraw

```text
container_slot
→ inventory_slot
```

### deposit

```text
inventory_slot
→ container_slot
```

一次只能一个 source slot + 一个 destination slot。

---

# 十八、禁止模糊语义

禁止：

```text
“拿一些泥土出来”
“把泥土放进去”
“整理一下箱子”
“把所有沙子拿出来”
```

LLM 必须先 inspect，然后形成精确参数。

---

# 十九、参数验证

### 坐标

```text
integer
finite
world bounds
```

### container_slot

```text
integer >= 0
```

随后由实际 window size 验证。

### inventory_slot

沿用 Phase 4D：

```text
9..44
integer
```

不要重新发明 slot 编号体系。

### count

```text
integer >= 1
```

### item

```text
non-empty
```

支持：

```text
dirt
minecraft:dirt
```

内部统一 canonical name。

---

# 二十、Withdraw 预检查

打开 container 后重新读取真实状态。

container source：

```text
window.slots[container_slot]
```

必须：

```text
exists
name matches expected item
count >= requested count
```

否则：

```text
minecraft.item_not_found
minecraft.item_changed
minecraft.item_count_insufficient
```

不要使用 inspect 时保存的旧状态作为最终依据。

---

# 二十一、Withdraw destination

重新读取：

```text
bot.inventory.slots[inventory_slot]
```

要求：

### empty

允许。

### same item

只有在合法 stack capacity 内才允许。

### different item

拒绝：

```text
minecraft.destination_occupied
```

绝不交换。

绝不换 destination slot。

绝不自动寻找其他空槽。

---

# 二十二、Deposit 预检查

container source：

```text
bot.inventory.slots[inventory_slot]
```

要求：

```text
exists
name matches expected item
count >= requested
```

否则：

```text
item.not_found
item.changed
item.count_insufficient
```

---

# 二十三、Deposit destination

container slot：

```text
window.slots[container_slot]
```

允许：

```text
empty
```

或者：

```text
same item + stack not full
```

如果是不同物品：

```text
minecraft.destination_occupied
```

绝不自动交换。

---

# 二十四、Stack Capacity

不要硬编码：

```text
64
```

必须使用当前 Minecraft version 的真实 item stack size 信息。

优先使用项目当前已经依赖的：

```text
minecraft-data
```

或 Mineflayer 当前公开 Item 数据。

不要自行维护物品最大堆叠表。

---

# 二十五、Transfer API

优先使用：

```text
bot.transfer(...)
```

并且必须：

```text
window = 当前打开的 container window
```

source / destination：

```text
sourceStart === sourceSlot
sourceEnd === sourceSlot + 1

destStart === destinationSlot
destEnd === destinationSlot + 1
```

即：

> 两个方向都必须把 source/destination 钉死在单槽。

不要：

```text
source range = entire inventory
dest range = entire inventory
```

否则 Mineflayer 会自己选择 slot，违反本阶段确定性要求。

当前 Mineflayer API 明确支持 `transfer` 并可指定 window、source range、destination range 和 count。

---

# 二十六、不要优先使用 withdraw/deposit

虽然当前 Container Window 有：

```text
window.withdraw(...)
window.deposit(...)
```

但这些接口按 item type/count 操作，不足以表达本阶段要求的：

```text
“就是这个 container slot → 就是这个 inventory slot”
```

因此本阶段优先使用：

```text
bot.transfer(...)
```

做单槽到单槽的确定性操作。

---

# 二十七、Transfer 后重新读取

绝对不能：

```text
transfer resolve
→ success
```

必须重新读取：

```text
container source
container destination
inventory source
inventory destination
```

根据方向判断实际变化。

---

# 二十八、Withdraw 成功条件

例如：

```text
container slot 0: dirt ×12
inventory slot 9: empty
```

请求：

```text
withdraw dirt ×1
```

完成后至少必须：

```text
container slot 0: dirt ×11
inventory slot 9: dirt ×1
```

但：

> 不要硬编码简单 `-1 / +1`。

必须读取真实最终状态，再依据真实变化判定成功。

---

# 二十九、Deposit 成功条件

例如：

```text
inventory slot 9: dirt ×1
container slot 0: empty
```

完成后：

```text
inventory slot 9: empty
container slot 0: dirt ×1
```

同样：

> 真实重读是硬门禁。

---

# 三十、最终 result

Withdraw：

```json
{
  "direction": "withdraw",
  "position": {
    "x": 100,
    "y": 64,
    "z": 100
  },
  "item": "minecraft:dirt",
  "count": 1,
  "container_slot": 0,
  "inventory_slot": 9,
  "container_before": {
    "name": "minecraft:dirt",
    "count": 12
  },
  "container_after": {
    "name": "minecraft:dirt",
    "count": 11
  },
  "inventory_before": null,
  "inventory_after": {
    "name": "minecraft:dirt",
    "count": 1
  }
}
```

Deposit 同样返回 source/destination before/after。

最终结果以真实重读为准。

---

# 三十一、Close 后再考虑 success

真正动作完成顺序：

```text
transfer
→ reread
→ verify
→ close
→ SUCCEEDED
```

如果 close 失败：

不要直接把 transfer 当成完全成功。

应该根据当前 ActionRuntime error semantics 设计：

```text
container.close_failed
```

如果世界状态已经改变但 close 出错：

结果必须如实报告。

不要吞掉 close error。

---

# 三十二、Unexpected close

Container window 可能在 transfer 过程中因为：

- 玩家关闭
- 服务端强制关闭
- disconnect
- block 被破坏
- server state change

而消失。

必须得到明确错误：

```text
minecraft.container_closed
```

不要无限等待。

---

# 三十三、Container identity

不要仅凭：

```text
x,y,z
```

一直认为它还是原来的 container。

打开成功后记录：

```text
block type
position
```

执行前/执行后必要时重新确认。

例如：

```text
chest
→ 目标方块被替换
```

不能继续拿旧 window 当成真实世界。

---

# 三十四、Confirmation

摘要必须明确：

### withdraw

```text
从 (100,64,100) 的 Chest 第 0 格取 minecraft:dirt ×1 到背包第 9 格
```

### deposit

```text
把背包第 9 格的 minecraft:dirt ×1 放入 (100,64,100) 的 Chest 第 0 格
```

Confirmation hash 必须覆盖：

```text
tool
x
y
z
direction
container_slot
inventory_slot
item
count
```

任一字段修改：

```text
旧 confirmation 作废
重新 PENDING
```

Confirmation 只负责授权。

真正执行前：

```text
open container
→ reread
→ validate
```

仍然必须完整运行。

---

# 三十五、TurnOrigin

沿用：

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

能产生 explicit intent。

Minecraft Chat：

```text
trusted player
```

要求继续存在。

WebUI：

```text
SYSTEM
```

只能发起测试/确认流程。

不得自授权。

特别测试：

```text
WebUI developer
→ create pending
→ consume
→ confirmation_not_user_turn
```

必须继续成立。

---

# 三十六、Inventory 与 Container 并发

当前：

```text
minecraft_inventory
```

是非独占 SAFE。

但：

```text
minecraft_equip
minecraft_inventory_move
minecraft_container_transfer
```

全部 exclusive。

因此：

```text
container_transfer + inventory_move
```

必须：

```text
minecraft.action_busy
```

同样：

```text
container_transfer + equip
container_transfer + dig
container_transfer + place
container_transfer + move_to
```

都必须互斥。

---

# 三十七、WebUI

Minecraft 页面增加：

## Container

### Inspect

填写：

```text
x
y
z
```

按钮：

```text
INSPECT
```

显示：

```text
type
position
size
slots
```

### Transfer

填写：

```text
x
y
z
direction
container slot
inventory slot
item
count
```

显示：

```text
container before
inventory before
confirmation state
action state
container after
inventory after
```

但：

> WebUI 按钮绝不能获得真实 MEDIUM 执行权。

必须经过现有 confirmation / USER turn 边界。

---

# 三十八、Python Tests

新增：

```text
tests/test_minecraft_container_tool.py
tests/test_minecraft_container_transfer_tool.py
```

至少覆盖：

### Inspect

- schema
- offline
- invalid coords
- unsupported block
- trapped chest
- too far
- double chest
- open failure
- read success
- close success
- close failure
- unexpected close

### Transfer

- schema
- direction invalid
- container slot invalid
- inventory slot invalid
- item mismatch
- source missing
- insufficient count
- destination occupied
- legal stack merge
- MEDIUM disabled
- no explicit intent
- untrusted Minecraft player
- confirmation_required
- confirmation consume
- confirmation mismatch
- confirmation expiry
- WebUI self-authorization denied
- successful withdraw
- successful deposit
- open/close lifecycle
- runtime error mapping

---

# 三十九、Node Tests

新增：

```text
minecraft_runtime/test/container.test.js
minecraft_runtime/test/container_transfer.test.js
```

必须重点覆盖：

### Lifecycle

A. open success

B. open failure

C. inspect read

D. close after success

E. close after failure

F. close after timeout

G. close after cancellation

H. disconnect while window open

I. cleanup exactly once

J. unexpected close

### Inspect

K. chest

L. barrel

M. trapped chest reject

N. double chest reject

O. unsupported block reject

### Transfer

P. withdraw

Q. deposit

R. exact source slot

S. exact destination slot

T. wrong item

U. insufficient count

V. destination occupied

W. legal stack merge

X. post-transfer reread

Y. transfer unconfirmed

Z. cleanup after error

AA. cancellation race

AB. timeout race

AC. terminal state exactly once

---

# 四十、flying-squid E2E

不要伪造真实 Container 成功。

如果 flying-squid 当前没有：

```text
Chest Container
```

或不能可靠模拟真实 inventory window：

可以只覆盖：

```text
unsupported
offline
schema
busy
confirmation
error mapping
```

成功路径由：

```text
fake Window unit tests
+
real Java Server
```

覆盖。

但如果现有 E2E harness 可以稳定创建 chest window，则增加真实成功路径。

---

# 四十一、Fake Window 测试

这里非常重要。

不要只 fake：

```js
bot.transfer = async () => {}
```

应该最少模拟：

```text
openContainer
window
window.slots
window.inventoryStart
window.close
bot.transfer
bot.inventory.slots
```

这样才能真正测试：

```text
open
→ transfer
→ reread
→ close
```

这一整套生命周期。

---

# 四十二、Real Java Server Smoke

扩展现有：

```text
minecraft_runtime/test/smoke_real_server.js
```

建议：

```text
connect
↓
ensureIdle
↓
找到/创建一个 single chest 或 barrel
↓
inspect
↓
找到一个真实存在的 container item
↓
找到一个空 player inventory slot
↓
withdraw ×1
↓
重新读 container + inventory
↓
deposit 回原 container slot
↓
重新读 container + inventory
↓
逐槽比较测试前后
↓
clear fixture（如果 smoke 创建了 fixture）
↓
确保 window 已关闭
↓
ensureIdle
↓
disconnect
```

---

# 四十三、Real Server Fixture

为避免污染真实世界，支持：

```text
SMOKE_CONTAINER_TARGET="x,y,z"
SMOKE_CONTAINER_TYPE="chest"
```

优先使用用户明确指定的现有 container。

如果没有指定：

可以尝试创建临时 fixture，但只有 bot 拥有足够权限时：

```text
/setblock x y z minecraft:chest
```

然后用服务器命令填入一个测试物品。

具体命令必须先根据当前服务器版本 / 权限实际验证，不要假定 `/item`、`/data` 一定可用。

如果无法可靠建立 fixture：

```text
SKIPPED
```

不要伪造 PASS。

---

# 四十四、Real Smoke 的恢复要求

如果使用 fixture：

```text
create
→ test
→ restore/delete
```

必须清理。

最终如果 fixture 无法删除：

```text
明确输出坐标
```

不允许静默遗留。

如果使用用户现有 Chest：

必须：

```text
withdraw
→ deposit 回原 slot
```

最终：

```text
container slot 完全恢复
inventory 完全恢复
```

---

# 四十五、Real Server 证据

Inspect 至少：

```text
1. open succeeded
2. type 正确
3. size 正确
4. slots 与真实 Container 对应
5. close succeeded
```

Withdraw 至少：

```text
1. RUNNING / action_id
2. completed
3. container_before
4. inventory_before
5. transfer executed
6. container_after
7. inventory_after
8. window closed
```

Deposit 再完成一次反向证据。

最后：

```text
container state restored
inventory state restored
```

---

# 四十六、WorldPerception

不要伪造：

```text
WorldPerception 检测到了 Chest inventory change
```

当前如果 WorldPerception 没有 container-content 感知：

本阶段只要求：

```text
Action Runtime result
+
Container reread
+
Inventory reread
```

足够形成可靠证据。

如果将来要把容器内容加入 world model，再单独设计 ContainerPerception。

---

# 四十七、Action Activity

增加事实性 activity：

```text
打开了一个箱子并查看内容
从 Chest 取出了 minecraft:dirt ×1
把 minecraft:dirt ×1 放回 Chest
```

只记录实际发生的事实。

不要写：

```text
“罐头整理了一下仓库”
```

因为这会掩盖精确操作范围。

---

# 四十八、配置

默认：

```yaml
allow_medium: false
```

保持不变。

新增必要的 container action 配置：

```text
max_distance
timeout
```

具体字段命名必须遵循现有：

```text
PlaceConfig
EquipConfig
InventoryMoveConfig
```

的配置风格。

不要把 container 额外的能力开关拆得过细。

---

# 四十九、文档

同步：

```text
config/config.example.yaml
app/web/config_registry.py
webui/src/pages/Minecraft.vue
docs/MINECRAFT_PHASE4E.md
CHANGELOG.md
docs/README.md
```

更新工具数量。

当前 4D 后：

```text
11 个 Minecraft LLM tools
```

不要留下“九个工具”等旧文案。

Phase 4E 完成后应准确反映新增：

```text
minecraft_container_inspect
minecraft_container_transfer
```

---

# 五十、安全边界

本阶段必须保持：

```text
minecraft_container_inspect = SAFE
minecraft_container_transfer = MEDIUM
```

但不要因为 inspect 是 SAFE 就：

```text
自动导航
自动打开任意 container
打开 trapped chest
打开 furnace
```

SAFE 只代表：

> 对支持的 normal chest/barrel 做只读 inspection。

---

# 五十一、最终架构

Phase 4E 完成后：

```text
Perception
    ├─ world
    ├─ inventory
    └─ container inspect

State Control
    ├─ equip
    ├─ inventory_move
    └─ container_transfer

World Actions
    ├─ dig
    └─ place

Movement
    ├─ move_to
    └─ follow_player
```

全部仍统一：

```text
LLM
 ↓
Agent Bridge
 ↓
Policy
 ↓
Confirmation（MEDIUM+）
 ↓
MinecraftService
 ↓
ActionRuntime
 ↓
Mineflayer
 ↓
Minecraft
```

不能出现：

```text
LLM
 ↓
Mineflayer
```

---

# 五十二、Phase 4E 不允许提前实现

禁止偷偷加入：

```text
craft
smelt
trade
container automation
sorting
loot
deposit-all
withdraw-all
hopper automation
container-to-container transfer
recipe planning
```

本阶段只有：

```text
inspect one Chest/Barrel
transfer one item/count between one exact container slot and one exact inventory slot
```

---

# 五十三、验收门禁

必须：

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

以及真实 Java Server：

```text
REAL SERVER: PASS
```

至少：

```text
container inspect = PASS
open/close lifecycle = PASS
withdraw = PASS
container reread = PASS
inventory reread = PASS
deposit = PASS
container restored = PASS
inventory restored = PASS
window closed = PASS
ensureIdle = PASS
```

只要真实服务器没有真正完成：

```text
Chest/Barrel
→ withdraw
→ deposit back
```

就：

```text
Phase 4E = BLOCKED
```

不要拿 fake window 替代真实服务器证据。

---

# 五十四、Real STOP

Container Transfer 与 Equip/Inventory Move 类似，通常可能很快。

因此：

```text
Real STOP
```

不是硬门禁。

但 Node 单测必须证明：

```text
cancel
timeout
disconnect
close cleanup
race
terminal exactly once
```

全部成立。

若真实服务器动作在 STOP 前已完成：

```text
SKIPPED
```

不伪造 CANCELLED PASS。

---

# 五十五、最终报告

沿用前几个阶段：

```text
Phase 4E = PASS / BLOCKED

commit:
<sha>

新增工具:
minecraft_container_inspect
minecraft_container_transfer

risk:
inspect = SAFE
transfer = MEDIUM

container support:
chest = ...
barrel = ...
double chest = NOT SUPPORTED

自动化:
pytest = ...
Node = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
inspect = ...
open/close = ...
withdraw = ...
deposit = ...
container restored = ...
inventory restored = ...
window closed = ...

REAL SERVER: PASS / BLOCKED
```

任何实际没有跑的项目：

```text
SKIPPED
```

不要把 fake window / unit test 写成真实服务器 PASS。