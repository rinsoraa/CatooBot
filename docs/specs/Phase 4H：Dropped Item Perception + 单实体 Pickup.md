基于当前仓库最新提交 `9a46e64` 开始 Phase 4H。

先完整阅读：

- Phase 2 / 2.1 / 3A 的 WorldPerception
- Phase 3B / 3B.1 ActionRuntime
- Phase 3C `move_to`
- Phase 3D `follow_player`
- Phase 4C `minecraft_inventory` / `minecraft_place`
- Phase 4D `minecraft_equip`
- Phase 4F / 4G `minecraft_recipe_lookup` / `minecraft_craft`
- `MinecraftAgentPolicy`
- `ConfirmationStore`
- `TurnOrigin`
- `MinecraftService`
- `runtime.js`
- `smoke_real_server.js`
- 当前 WebUI Minecraft 页面
- 当前全部 Minecraft tests

不要根据历史报告猜当前接口。

---

# 一、Phase 4H 目标

实现两个能力：

```text
minecraft_dropped_items
minecraft_pickup_item
```

目标闭环：

```text
Minecraft Item Entity
        ↓
minecraft_dropped_items
        ↓
LLM 看到附近掉落物
        ↓
用户明确要求拾取某一个实体
        ↓
minecraft_pickup_item
        ↓
MEDIUM confirmation
        ↓
ActionRuntime
        ↓
有限距离 Pathfinding
        ↓
进入拾取半径
        ↓
Minecraft 自动收集 Item Entity
        ↓
playerCollect / entityGone
        ↓
重新读取 Inventory
        ↓
确认物品真的进入背包
```

最终要实现：

> **“罐头看见地上的东西 → 知道是哪一个 → 主动走过去 → 把指定实体捡起来 → 确认背包真的增加。”**

---

# 二、严格范围

本阶段只做：

```text
Item Entity perception
+
single Item Entity pickup
```

禁止：

- 自动拾取附近所有掉落物
- 自动 loot
- 自动扫地
- 自动收集某种物品
- pickup all
- pickup nearest
- 自动循环拾取
- 自动挖掘
- 自动杀怪
- 自动攻击
- 自动开箱
- 自动 crafting
- 自动整理背包
- 自动寻找材料
- `collectblock.collect()`
- 把 dig + pickup 合成一个黑盒动作
- 让 LLM 直接操作 Mineflayer entity

---

# 三、工具

新增：

```text
minecraft_dropped_items
minecraft_pickup_item
```

风险：

```text
minecraft_dropped_items = SAFE
minecraft_pickup_item = MEDIUM
```

因此 Phase 4H 后：

```text
minecraft_dropped_items = SAFE / 非独占
minecraft_pickup_item = MEDIUM / 独占 / confirmation
```

`allow_medium` 默认仍然：

```yaml
allow_medium: false
```

---

# 四、minecraft_dropped_items

这是一个严格只读的语义化感知工具。

作用：

> 返回 bot 当前能够感知到的附近 Item Entity。

不要做成：

```text
minecraft_entities
```

也不要把：

- 玩家
- 动物
- 怪物
- 箭
- 经验球
- 载具
- 投射物

全部暴露给 LLM。

第一版只提供 Item Entity。

---

# 五、Item Entity 的底层判断

先阅读当前项目实际 Mineflayer 版本与当前 runtime 的 Entity 结构。

不要假设：

```text
entity.name === "item"
```

就直接写死生产逻辑。

应根据：

```text
bot.entities
itemDrop event
当前 Mineflayer Entity 类型/metadata
```

确认当前版本 Item Entity 的可靠识别方法。

最终建立一个内部 helper：

```text
isDroppedItemEntity(entity)
```

所有地方只通过这个 helper 判断。

---

# 六、Dropped Item 输出

LLM 不需要原始 Entity。

推荐：

```json
{
  "items": [
    {
      "entity_id": 123,
      "item": {
        "name": "minecraft:dirt",
        "count": 3
      },
      "position": {
        "x": 100.35,
        "y": 64.12,
        "z": 101.84
      },
      "distance": 3.7
    }
  ]
}
```

仅暴露：

```text
entity_id
item.name
item.count
position
distance
```

不要暴露：

- raw metadata
- packet
- internal numeric id
- entity object
- UUID 原始结构
- physics internals
- velocity raw vector
- server-specific metadata

除非后续真实需求证明必要。

---

# 七、距离范围

增加配置：

```yaml
minecraft:
  agent:
    actions:
      pickup:
        max_distance: 16
```

默认建议：

```text
16 blocks
```

具体字段命名必须遵循当前 `dig/place/craft/container` 配置结构。

不要创建大量细碎配置。

例如不要现在就增加：

```text
scan_distance
entity_distance
pickup_distance
chase_distance
collection_distance
```

第一版一个：

```text
pickup.max_distance
```

足够。

---

# 八、Dropped Items 工具是非独占

因为它只是读取：

```text
minecraft_dropped_items
```

应该允许与：

```text
move_to
follow_player
dig
place
equip
container_transfer
craft
```

并行。

加入：

```text
NON_EXCLUSIVE_TOOLS
```

类似：

```text
minecraft_world
minecraft_inventory
minecraft_recipe_lookup
minecraft_dropped_items
```

不要因为“实体列表变化频繁”就把它做成 exclusive。

---

# 九、排序

为了让 LLM 看到稳定输入：

推荐：

```text
sort by:
1. distance ascending
2. entity_id ascending
```

不要：

```text
bot.entities Object.values()
```

直接原顺序返回。

这样相同世界状态下工具输出尽量稳定。

---

# 十、实体数量上限

不要把几十/几百个 Item Entity 全塞给 LLM。

建议：

```text
max_items = 32
```

或者当前项目统一的感知上限。

输出：

```json
{
  "items": [...],
  "truncated": false
}
```

如果超过上限：

```json
{
  "items": [...32],
  "truncated": true
}
```

这样 LLM 知道列表并不完整。

---

# 十一、实体生命周期

必须正确处理：

```text
itemDrop
entityMoved
entityGone
playerCollect
```

Mineflayer 当前提供这些事件，其中 `playerCollect` 会告诉我们哪个 entity 收集了哪个 item entity。([GitHub](https://github.com/PrismarineJS/mineflayer/blob/master/docs/api.md))

不要只依赖一次：

```text
Object.values(bot.entities)
```

然后永久缓存 entity。

如果项目当前已经有 entity cache，可以复用。

如果没有：

可以使用：

```text
bot.entities
```

作为实时 source；

事件只负责 ActionRuntime 的生命周期监听。

不要为一个简单能力再建一套巨型世界数据库。

---

# 十二、Entity ID 语义

LLM 使用：

```text
entity_id
```

指定目标。

不要让 LLM 直接传：

```text
x
y
z
```

作为唯一目标。

原因：

> 地上的 Item 会移动。

因此：

```text
minecraft_dropped_items
→ entity_id
```

才是稳定的当前会话目标。

---

# 十三、minecraft_pickup_item schema

建议：

```json
{
  "entity_id": 123,
  "expected_item": "minecraft:dirt"
}
```

两个字段全部 required。

`expected_item` 是第二层身份校验。

例如：

```text
entity_id = 123
expected_item = minecraft:dirt
```

执行前必须确认：

```text
entity 仍存在
entity 仍然是 Item Entity
entity.id == 123
entity item == minecraft:dirt
```

否则拒绝。

---

# 十四、为什么必须 expected_item

不能只：

```json
{
  "entity_id": 123
}
```

因为 entity ID 本身不是足够的人类可读安全约束。

如果目标实体在确认期间消失、ID 被重新分配或者 runtime 状态发生变化：

必须要求：

```text
entity_id
+
expected_item
```

双重约束。

---

# 十五、confirmation fingerprint

绑定：

```text
tool
entity_id
expected_item
```

即可。

不要把：

```text
position
```

作为唯一身份。

但是建议把确认摘要展示：

```text
拾取 minecraft:dirt ×3
当前位置约 (100,64,102)
```

其中位置只是**确认时的人类可读提示**，不是授权身份本体。

---

# 十六、确认摘要必须表达“可能移动”

例如：

```text
拾取附近的 minecraft:dirt ×3（实体 #123）
```

而不是：

```text
在 (100,64,102) 执行拾取
```

因为目标实际上是 Item Entity。

---

# 十七、TurnOrigin

沿用所有前面阶段：

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

才有 explicit intent。

Minecraft Chat：

```text
trusted player
```

继续适用于 MEDIUM。

WebUI：

```text
SYSTEM
```

不能消费 confirmation。

必须继续测试：

```text
minecraft.confirmation_not_user_turn
```

---

# 十八、pickup 是 MEDIUM

虽然拾取本身不像 dig 那样直接改世界方块，但它：

1. 修改玩家 inventory
2. 会让 bot 主动移动
3. 可能进入用户没有预期的位置

所以：

```text
minecraft_pickup_item = MEDIUM
```

不能做成 LOW。

---

# 十九、ActionRuntime

新增：

```text
pickup_item
```

Action。

性质：

```text
exclusive = true
detached = true
risk = MEDIUM
```

不能：

```text
Service
→ bot.pathfinder
```

绕过 ActionRuntime。

必须：

```text
LLM
 ↓
Agent Bridge
 ↓
Policy
 ↓
Confirmation
 ↓
MinecraftService
 ↓
ActionRuntime
 ↓
runtime.js
 ↓
Mineflayer
```

---

# 二十、Pickup 不是“调用 move_to”

不能：

```text
minecraft_pickup_item
→ 调用 minecraft_move_to
→ 再调用 pickup
```

因为两个动作都会占用 foreground。

正确做法：

```text
pickup_item Action
```

内部自己管理：

```text
pathfinding
+
target entity
+
collection wait
```

---

# 二十一、为什么不能嵌套 move_to

当前：

```text
move_to
follow_player
dig
place
equip
inventory_move
container_transfer
craft
```

都是 ActionRuntime 的 foreground action。

所以：

```text
pickup_item
→ invoke(move_to)
```

会产生：

```text
action.busy
```

或者形成递归生命周期。

因此 Pickup 必须作为一个完整 ActionRuntime action 实现。

---

# 二十二、导航方式

第一版允许有限导航。

目标：

```text
Item Entity
```

建议：

```text
GoalNear
```

或者当前 pathfinder 里已经稳定验证过的动态 Entity goal。

但不要直接使用：

```text
mineflayer-collectblock
```

也不要把：

```text
dig + move + collect
```

封装进去。

Pathfinder 官方支持 `GoalNear` 等目标，也支持动态/复合路径目标；当前 CatooBot 已经使用这一套能力。([GitHub](https://github.com/PrismarineJS/mineflayer-pathfinder/blob/master/readme.md))

---

# 二十三、Item Entity 是动态目标

Item 会：

- 滑动
- 被水带走
- 被玩家推动
- 被其他玩家捡走
- 合并
- 消失

因此不能：

```text
start
→ 读取 position
→ GoalNear 固定坐标
→ 永远追旧坐标
```

---

# 二十四、推荐监督模型

每：

```text
250ms
```

检查一次目标。

检查：

```text
entity still exists
entity identity unchanged
item name unchanged
distance valid
```

如果目标位置变化：

```text
更新导航目标
```

如果已经进入：

```text
pickup_radius = 1.2
```

停止 pathfinder，等待 collection。

不要每 tick 高速重新 setGoal。

---

# 二十五、目标身份必须绑定 Entity Object

Action start 时：

```text
targetEntity = bot.entities[entity_id]
```

保存实际 object reference。

之后：

如果：

```text
bot.entities[entity_id] !== targetEntity
```

不要自动绑定新 entity。

这样避免：

```text
旧 Item 消失
↓
新 Item 获得相同 entity_id
↓
错误拾取新物品
```

第一版宁可：

```text
minecraft.target_replaced
```

也不能误拾取。

---

# 二十六、最大追踪距离

Action start：

```text
initialDistance <= max_distance
```

运行过程中：

```text
currentDistance <= max_distance
```

如果目标被拉远：

```text
minecraft.pickup_target_too_far
```

停止。

不要无限追。

---

# 二十七、不能自动打开危险路径

当前 Pathfinder 必须继续使用与 move_to/follow 一致的非破坏 Movement：

```text
canDig = false
scaffoldingBlocks = []
canOpenDoors = false
allow1by1towers = false
```

Pickup 不得为了拿一个掉落物：

```text
挖墙
垫方块
搭方块
开门
```

---

# 二十八、进入 Pickup Radius 后

当：

```text
distance <= pickup_radius
```

动作不再继续导航。

执行：

```text
pathfinder.setGoal(null)
```

然后：

```text
clearControlStates()
```

再等待：

```text
playerCollect
```

或者：

```text
inventory change
+
entity gone
```

---

# 二十九、成功判据

**必须同时尽量满足：**

1. `playerCollect` 事件明确指出 collector 是 bot
2. collected entity 是目标 entity
3. 目标 entity 消失/不再存在
4. Inventory 对应 item 数量增加

硬门禁：

```text
target entity 被实际收集
+
inventory increased
```

不要只：

```text
entity gone
```

就宣布成功。

因为 entity 可能：

- 被其他玩家捡走
- 被服务器销毁
- 掉进 unloaded chunk

---

# 三十、Inventory 验证

执行前：

```text
inventory_before = countInventoryItem(expected_item)
```

执行后：

```text
inventory_after = countInventoryItem(expected_item)
```

至少：

```text
after > before
```

即可作为第一版硬门禁。

不要强制：

```text
after === before + target.count
```

因为 Item Entity 可能发生：

- merge
- split
- pickup delay
- stack merge

最终以真实 inventory + playerCollect 为准。

---

# 三十一、结果结构

建议：

```json
{
  "entity_id": 123,
  "item": {
    "name": "minecraft:dirt",
    "count_before": 3
  },
  "distance_start": 5.4,
  "distance_collected": 0.9,
  "inventory_before": 10,
  "inventory_after": 13,
  "collected": true
}
```

如果最终数量不是 target 原始数量：

不要伪造。

如：

```text
target count = 3
inventory delta = 3
```

最好。

但硬门禁仍然：

```text
inventory_after > inventory_before
```

---

# 三十二、错误码

至少：

```text
minecraft.item_entity_not_found
minecraft.item_entity_invalid
minecraft.item_entity_changed
minecraft.pickup_target_too_far
minecraft.pickup_target_lost
minecraft.pickup_failed
minecraft.pickup_unconfirmed
minecraft.action_busy
```

不要返回：

```text
500 pickup failed
```

---

# 三十三、Entity Gone

如果：

```text
entityGone(targetEntity)
```

发生：

但此前没有：

```text
playerCollect(bot, targetEntity)
```

不要成功。

判断：

```text
entityGone + inventory increase
```

可以作为 fallback，但必须明确：

> 只有 inventory 真增加才能算成功。

如果：

```text
entityGone
+
inventory unchanged
```

返回：

```text
minecraft.pickup_unconfirmed
```

---

# 三十四、其他玩家拾取

如果：

```text
playerCollect(otherPlayer, targetEntity)
```

发生：

立即失败：

```text
minecraft.pickup_target_lost
```

不要继续追：

```text
bot.entities[entity_id]
```

中的替代实体。

---

# 三十五、目标类型改变

如果目标 Entity 原本：

```text
minecraft:dirt
```

之后读取到：

```text
minecraft:sand
```

立即失败：

```text
minecraft.item_entity_changed
```

不要继续。

---

# 三十六、Action Cleanup

cleanup 至少：

```text
pathfinder.setGoal(null)
clearControlStates()
```

cleanup exactly once。

适用于：

```text
SUCCEEDED
FAILED
CANCELLED
TIMEOUT
disconnect
shutdown
```

保持 ActionRuntime 当前 cleanup contract。

---

# 三十七、Real STOP

Pickup 可能有明显移动窗口，因此这次与 craft/equip 不一样。

真实 STOP **应当作为真实服务器 Smoke 的重要验证项**。

需要：

```text
pickup → RUNNING
↓
目标距离 > pickup_radius
↓
POST /minecraft/stop
↓
CANCELLED
↓
goal == null
↓
isMoving == false
↓
位置稳定
↓
目标 item 仍在
↓
inventory 不增加
```

如果真实目标太近导致动作瞬间完成：

```text
SKIPPED
```

但 Smoke 必须尽量主动找一个：

```text
约 6～10 格
```

的 Item Entity 来获得取消窗口。

---

# 三十八、不能用“slow item”作弊

不要：

```text
修改游戏 tick
修改 item physics
teleport item
fake server entity
```

来制造 STOP。

Real STOP 如果环境不具备窗口：

```text
SKIPPED
```

即可。

---

# 三十九、minecraft_dropped_items 工具测试

Python：

```text
tests/test_minecraft_dropped_items_tool.py
```

至少：

- schema
- SAFE
- online required
- non-exclusive
- empty list
- single item
- multiple items
- distance
- sorting
- max_items
- truncated
- raw entity fields 不泄露
- player/hostile entities 不进入
- stale/dead entity 不进入

---

# 四十、pickup Python tests

新增：

```text
tests/test_minecraft_pickup_item_tool.py
```

覆盖：

- schema
- missing entity_id
- missing expected_item
- offline
- allow_medium=false
- no explicit intent
- untrusted Minecraft player
- confirmation_required
- confirmation mismatch
- WebUI self-authorization denied
- target not found
- item mismatch
- target too far
- target replaced
- target lost
- action busy
- successful flow
- unconfirmed pickup
- stop flow

---

# 四十一、Node Runtime Tests

新增：

```text
minecraft_runtime/test/dropped_items.test.js
minecraft_runtime/test/pickup_item.test.js
```

Dropped Items：

A. item entity extraction

B. filtering

C. sorting

D. max_items

E. stale entity

F. entityGone

G. playerCollect

Pickup：

H. successful path

I. moving target

J. target replacement

K. wrong item

L. target lost

M. too far

N. playerCollect by bot

O. playerCollect by other player

P. entityGone without inventory change

Q. inventory increased but wrong entity

R. cleanup

S. STOP

T. TIMEOUT

U. race

V. terminal exactly once

---

# 四十二、Fake Bot 不要只 fake 一个 collect Promise

Fake Runtime 至少需要模拟：

```text
bot.entities
bot.pathfinder
entity position
entity identity
entity item
playerCollect
entityGone
inventory
```

测试：

```text
move
→ target moves
→ path changes
→ enter radius
→ playerCollect
→ entity gone
→ inventory +count
```

这样才能真正验证 4H 的动作状态机。

---

# 四十三、flying-squid E2E

如果当前 flying-squid 可以稳定产生 Item Entity：

必须增加：

```text
item entity perception
pickup
```

成功路径。

如果不能：

至少真实验证：

```text
dropped item inspection
schema
filtering
busy
confirmation
target not found
```

不得用 fake entity 冒充 E2E 成功。

真实 pickup 硬门禁仍由 Java Server Smoke 提供。

---

# 四十四、Real Server Fixture

Smoke 支持：

```text
SMOKE_PICKUP_TARGET="entity_id"
SMOKE_PICKUP_ITEM="minecraft:oak_planks"
```

优先使用：

```text
SMOKE_PICKUP_TARGET
```

指定实际 Item Entity。

如果没有指定：

尝试用服务器权限创建一个临时 Item Entity。

不要假设某个固定 `/summon item` NBT 在所有服务器版本都兼容。

应先读取当前 Server version。

如果可以可靠创建：

```text
生成一个单独的 Item Entity
→ 记录 entity_id
→ pickup
```

完成后 Item 本身应该消失，不需要额外清理。

如果无法创建：

```text
SKIPPED
```

不得假 PASS。

---

# 四十五、推荐 Real Smoke Fixture

优先：

```text
minecraft:oak_planks ×3
```

因为：

- 普通物品
- 可验证 inventory 增量
- 不涉及特殊实体
- 前面阶段已经广泛使用
- 容易清理

但不要硬编码服务器版本的 summon NBT。

如果直接通过玩家自然掉落方式获得 Item Entity 更可靠：

可以使用现有 dig smoke：

```text
dig 一个可掉落 oak_log
→ 等待真实 item entity 出现
→ dropped_items 找到
→ pickup
```

但：

**不要把 dig 自动嵌入 pickup Action。**

Smoke 可以把两个已经通过的动作串起来：

```text
dig
→ dropped_items
→ pickup
```

这反而是非常有价值的真实身体链测试。

---

# 四十六、Real Smoke 核心链

建议增加：

```text
Phase 4H
```

顺序：

```text
ensureIdle
↓
inventory before
↓
制造真实掉落物
↓
minecraft_dropped_items
↓
看到 entity_id / item / position
↓
POST pickup
↓
RUNNING + action_id
↓
目标移动/实体仍存在
↓
进入 pickup radius
↓
playerCollect
↓
entityGone
↓
inventory reread
↓
item count increased
↓
ensureIdle
```

---

# 四十七、真实 STOP Smoke

在真正 pickup 前再制造一个较远目标：

```text
6～10 blocks
```

然后：

```text
pickup → RUNNING
↓
STOP
↓
cancelled
↓
goal null
↓
isMoving false
↓
位置稳定
↓
目标 entity 仍存在
↓
inventory unchanged
```

如果动作自己先完成：

```text
SKIPPED
```

不能伪造。

---

# 四十八、WorldPerception

4H 不要强行把 Item Entity 混进当前 block world diff。

当前 WorldPerception 主要描述：

```text
blocks
environment
self
players/entities/POI baseline
```

本阶段可以新增：

```text
dropped item semantic perception
```

但不要破坏已有 block diff。

建议：

```text
minecraft_dropped_items
```

作为独立 read model。

如果未来需要：

```text
world snapshot
```

融合 Item Entity，再单独定义 schema。

---

# 四十九、Activity

真实成功后记录：

```text
捡起了 minecraft:oak_planks ×3
```

STOP：

```text
没有捡到 minecraft:oak_planks
```

只记录事实。

不要：

```text
“罐头成功抢到了掉落物”
```

不要添加情绪。

---

# 五十、WebUI

Minecraft 页面增加：

## Dropped Items

显示：

```text
entity id
item
count
position
distance
```

按钮：

```text
REFRESH
```

## Pickup Test

填写：

```text
Entity ID
Expected Item
```

显示：

```text
confirmation
target
action
inventory before
inventory after
```

但是：

> WebUI 的 PICKUP 按钮只能发起确认。

不能获得：

```text
SYSTEM → confirmation consume
```

的真实执行权。

---

# 五十一、配置

只需要：

```yaml
pickup:
  max_distance: 16
  timeout: 30
```

具体字段路径遵循当前配置结构。

如果 `pickup_radius` 已经存在于 runtime 常量：

暂时不需要暴露给用户配置。

不要过度参数化。

---

# 五十二、文档

新增：

```text
docs/MINECRAFT_PHASE4H.md
```

同步：

```text
config/config.example.yaml
app/web/config_registry.py
webui/src/pages/Minecraft.vue
docs/README.md
CHANGELOG.md
```

说明：

```text
minecraft_dropped_items = SAFE
minecraft_pickup_item = MEDIUM
```

以及：

```text
拾取只针对明确 Item Entity
不会自动拾取附近所有掉落物
不会自动挖掘
不会自动 loot
不会自动导航超过最大距离
```

---

# 五十三、Action Risk / Tool Registry

新增：

```text
minecraft_dropped_items: SAFE
minecraft_pickup_item: MEDIUM
```

`TOOL_ACTION`：

```text
minecraft_pickup_item: pickup_item
```

`minecraft_dropped_items`：

```text
NON_EXCLUSIVE_TOOLS
```

`minecraft_pickup_item`：

默认 exclusive。

Offline：

不增加到：

```text
OFFLINE_TOOLS
```

---

# 五十四、特别安全测试

必须证明：

```text
INITIATIVE
BACKGROUND
SYSTEM
```

不能触发：

```text
minecraft_pickup_item
```

只有：

```text
USER
```

且 confirmation 成功后才能执行。

另外：

```text
entity_id A
expected_item dirt
```

确认后如果变成：

```text
entity_id B
```

必须 mismatch。

如果变成：

```text
expected_item sand
```

必须 mismatch。

---

# 五十五、不能扩展到 Entity Actions

本阶段不要新增：

```text
minecraft_attack
minecraft_feed
minecraft_mount
minecraft_shears
minecraft_trade
minecraft_interact_entity
```

4H 只有：

```text
read dropped items
pickup one dropped item
```

---

# 五十六、不要使用 collectblock

即使 `mineflayer-collectblock` 可以直接封装：

```text
pathfinding
digging
collecting
```

也不能用于本阶段。

原因：

> 我们已经有独立的 dig / movement / inventory / perception / ActionRuntime 层。

当前阶段需要证明：

```text
Perception
→ Target Identity
→ Policy
→ ActionRuntime
→ limited Pathfinding
→ Collection Event
→ Inventory Verification
```

而不是把它压成一个插件调用。

---

# 五十七、验收门禁

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

并且真实 Java：

```text
REAL SERVER: PASS
```

硬门禁至少：

```text
dropped item perception = PASS
target identity = PASS
pickup RUNNING = PASS
real playerCollect = PASS
target entity gone = PASS
inventory increased = PASS
pickup STOP = PASS / SKIPPED
ensureIdle = PASS
```

最核心：

> **必须真实把一个 Item Entity 拾取进真实服务器上的 bot inventory。**

否则：

```text
Phase 4H = BLOCKED
```

不能用 fake bot 替代。

---

# 五十八、最终报告格式

```text
Phase 4H = PASS / BLOCKED

commit:
<sha>

新增工具:
minecraft_dropped_items
minecraft_pickup_item

risk:
dropped_items = SAFE
pickup_item = MEDIUM

自动化:
pytest = ...
Node = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
dropped item perception = ...
target identity = ...
pickup = ...
playerCollect = ...
entity gone = ...
inventory increased = ...
STOP = PASS / SKIPPED
ensureIdle = ...

REAL SERVER: PASS / BLOCKED
```

所有没真正执行的项目：

```text
SKIPPED
```

不得伪造 PASS。

---

# 五十九、完成后的行为链

4H 真正完成后，应能够形成：

```text
minecraft_dig
    ↓
Minecraft 掉落 Item Entity
    ↓
minecraft_dropped_items
    ↓
罐头知道：
“地上有 oak_log ×1，距离 6 格，实体 #123”
    ↓
用户：
“把那个捡起来”
    ↓
minecraft_pickup_item(#123, oak_log)
    ↓
confirmation
    ↓
有限寻路
    ↓
playerCollect
    ↓
inventory +1
```

这才是 Minecraft 身体层第一次形成完整的：

```text
破坏世界
→ 世界产生实体
→ 感知实体
→ 选择实体
→ 移动到实体
→ 与实体交互
→ 背包状态变化
→ 事实验证
```

不要在 4H 提前加入自动挖矿、自动收集路线、资源循环或任务规划。