基于当前最终树 `3e4e2e4` 开始 Phase 4K。

先完整阅读：

- Phase 2 / 2.1 / 3A WorldPerception
- Phase 3C / 3D / 4H.1 Movement
- Phase 4B `minecraft_dig`
- Phase 4C `minecraft_place`
- Phase 4D `minecraft_inventory` / `minecraft_equip`
- Phase 4H `minecraft_dropped_items` / `minecraft_pickup_item`
- Phase 4J `minecraft_dig_capability`
- `MinecraftAgentPolicy`
- `ConfirmationStore`
- `TurnOrigin`
- `MinecraftService`
- `ActionRuntime`
- `runtime.js`
- `smoke_real_server.js`
- 当前全部 Python / Node / WebUI 测试

不要根据历史报告猜当前代码。

---

# 一、Phase 4K 目标

本阶段不是实现一个新的“自动挖矿”黑盒。

目标分成两层：

### 产品能力

新增一个只读工具：

```text
minecraft_find_blocks
```

作用：

> 在当前 bot 周围有限范围内，寻找指定名称的实际方块位置。

### 验收能力

使用已有原子能力完成一次真实资源链：

```text
minecraft_find_blocks
        ↓
minecraft_dig_capability
        ↓
minecraft_equip（必要时）
        ↓
minecraft_move_to
        ↓
minecraft_dig
        ↓
minecraft_dropped_items
        ↓
minecraft_pickup_item
        ↓
minecraft_inventory
```

但：

**这条链必须由 Agent/Smoke/测试编排，不允许变成一个 `minecraft_gather_resource` 工具。**

---

# 二、为什么 4K 不增加 gather_resource

禁止新增：

```text
minecraft_gather_resource
minecraft_collect_resource
minecraft_mine_resource
minecraft_auto_mine
minecraft_collect_block
```

原因：

当前已经存在：

```text
find
move
equip
capability
dig
dropped_items
pickup
inventory
```

这些能力已经分别经过确认门、ActionRuntime、真实服务器验证。

现在需要验证的是：

> **这些原子能力能否可靠串成一条真实资源行为链。**

而不是再包一层黑盒。

---

# 三、新工具

只新增：

```text
minecraft_find_blocks
```

风险：

```text
SAFE
```

属性：

```text
online = required
exclusive = false
confirmation = none
explicit_intent = not required
```

可以与：

```text
move_to
follow_player
dig
place
equip
inventory_move
container_transfer
craft
pickup
```

并行读取。

不要进入：

```text
OFFLINE_TOOLS
```

---

# 四、Tool Schema

建议：

```json
{
  "block_names": [
    "minecraft:iron_ore",
    "minecraft:deepslate_iron_ore"
  ],
  "max_distance": 16,
  "max_results": 8
}
```

字段：

### `block_names`

required：

```text
array[string]
```

限制：

```text
1 <= len <= 8
```

所有名称：

- non-empty
- canonicalize
- `iron_ore` == `minecraft:iron_ore`
- 小写
- `minecraft:` 前缀统一

---

### `max_distance`

optional：

```text
integer
```

建议默认：

```text
16
```

限制：

```text
1 <= max_distance <= 32
```

不要允许 LLM 发：

```text
1000
10000
```

避免大范围全局扫描。

如果省略：

```text
16
```

---

### `max_results`

optional：

```text
integer
```

默认：

```text
8
```

限制：

```text
1 <= max_results <= 16
```

---

# 五、不要支持 block tag

第一版只接受：

```text
minecraft:iron_ore
minecraft:oak_log
minecraft:stone
minecraft:dirt
```

这种**明确 block name**。

不要支持：

```text
#ores
#logs
#mineable/pickaxe
```

也不要自己解析：

```text
tool tags
block tags
```

原因：

> 4K 解决“目标在哪里”，不是建立一个 Minecraft 分类知识库。

以后要支持 tags，再单独设计。

---

# 六、不要接受坐标起点

不要：

```json
{
  "x": ...,
  "y": ...,
  "z": ...,
  "block_names": [...]
}
```

因为第一版的语义就是：

> 从 bot 当前所在位置寻找。

起点必须始终：

```text
bot.entity.position
```

这样才能让 LLM 形成：

```text
当前世界
→ 当前附近资源
```

而不是执行任意远程扫描。

---

# 七、数据来源

优先使用当前 Mineflayer：

```js
bot.findBlocks(options)
```

官方接口支持：

- `point`
- `matching`
- `maxDistance`
- `count`

并返回距离最近的一组**坐标**，已经按距离排序。

因此不要自己遍历：

```text
整个世界
```

也不要自己扫：

```text
x ± N
z ± N
y ± N
```

除非当前版本 `findBlocks()` 对某种情况确实无法满足需求。

---

# 八、matching 使用 block id / ids

优先：

```text
matching = [id1, id2, ...]
```

而不是：

```js
matching = block => block.name === ...
```

原因：

> Mineflayer 原生已经支持 block id / id array，避免每个候选位置重新构造高层 Block 对象。

但如果当前 CatooBot 的版本兼容层更适合 function matching，可以按当前代码结构选择。

不要因此改动 Mineflayer 依赖。

---

# 九、输出结构

建议：

```json
{
  "ok": true,
  "query": {
    "block_names": [
      "minecraft:iron_ore",
      "minecraft:deepslate_iron_ore"
    ],
    "max_distance": 16,
    "max_results": 8
  },
  "matches": [
    {
      "block": "minecraft:iron_ore",
      "position": {
        "x": 103,
        "y": 12,
        "z": 141
      },
      "distance": {
        "goal_near": 5,
        "raw": 5.42
      }
    }
  ],
  "truncated": false
}
```

不要返回：

- raw Block object
- metadata
- NBT
- internal block id
- chunk object
- pathfinder internals

---

# 十、距离口径

沿用当前项目已经建立的双距离语义：

```text
distance.goal_near
distance.raw
```

其中：

```text
goal_near
```

用于和 Movement 语义一致。

```text
raw
```

表示：

> 当前 bot 视角中心/眼睛到目标方块中心的实际浮点距离。

具体计算口径必须与 `minecraft_dig_capability` 当前实现保持一致，不要在 4K 又新造第三种距离算法。

---

# 十一、排序

最终输出排序：

```text
1. distance.goal_near
2. distance.raw
3. x
4. y
5. z
```

或者，如果直接使用 Mineflayer 的最近结果：

至少在 semantic projection 最后再做稳定排序。

这样：

> 相同世界状态下，LLM 每次看到的候选顺序尽量稳定。

不要按照 JS object enumeration 顺序。

---

# 十二、max_results

结果最多：

```text
16
```

如果找到超过请求数量：

```json
{
  "matches": [...],
  "truncated": true
}
```

如果没有超出：

```json
{
  "matches": [...],
  "truncated": false
}
```

注意：

> `truncated` 表示“搜索结果受数量限制截断”，不是“搜索失败”。

---

# 十三、没有结果

正常返回：

```json
{
  "ok": true,
  "matches": [],
  "truncated": false
}
```

不要：

```text
404
```

也不要：

```text
minecraft.block_not_found
```

因为：

> 世界正常，只是当前范围内没有目标。

---

# 十四、区块未加载

如果搜索区域包含当前客户端未加载区块：

不要自动：

```text
move_to
```

也不要自动：

```text
waitForChunksToLoad
```

把当前能从 runtime 可靠查询到的结果返回即可。

如果 Mineflayer 明确无法查询某范围：

可以在结果中提供：

```text
coverage
```

或：

```text
incomplete
```

但不要声称“整个世界没有这种资源”。

---

# 十五、不要把“最近”理解成“最佳”

必须明确：

```text
minecraft_find_blocks
```

只回答：

> 哪些目标方块在范围内。

不回答：

> 哪一个最适合挖。

尤其不要返回：

```text
recommended
best
optimal
```

因为：

- 最近的不一定可达
- 最近的不一定可挖
- 最近的矿可能在墙后
- 工具可能不合适

下一步仍必须：

```text
minecraft_dig_capability
```

---

# 十六、与 Dig Capability 的正式关系

推荐链：

```text
minecraft_find_blocks
        ↓
得到 candidate positions
        ↓
minecraft_dig_capability
        ↓
can_dig / dig_time
        ↓
LLM 决定
```

如果：

```text
can_dig = false
```

不要：

```text
find_blocks
→ 自动 equip
```

让模型自己决定是否：

```text
minecraft_equip
```

---

# 十七、与 Move_to 的关系

`minecraft_find_blocks` 不移动。

正确：

```text
find
→ move_to
```

而不是：

```text
find
→ service.move_to
```

工具之间保持独立。

---

# 十八、与 WorldPerception 的关系

不要修改现有：

```text
near/local/extended
```

world cache。

也不要把全部找到的 block 自动写入 WorldStateCache。

`minecraft_find_blocks` 是：

```text
on-demand query
```

不是新的持久世界缓存。

这样不会重新引入 Phase 2.1 的 cache consistency 复杂度。

---

# 十九、Python 工具层

新增：

```text
app/tools/builtins/minecraft_find_blocks.py
```

必须走：

```text
Tool
→ Agent Bridge
→ Policy
→ MinecraftService
→ runtime
```

Tool 本身不能碰 Mineflayer。

---

# 二十、Service API

增加：

```text
MinecraftService.find_blocks(...)
```

输入：

```text
block_names
max_distance
max_results
```

必须：

- canonicalize
- validate
- bounds check

不要把任意字符串直接传给 runtime。

---

# 二十一、错误码

至少：

```text
action.invalid
minecraft.offline
minecraft.block_query_unavailable
```

如果 block name 无法在当前 Minecraft registry 中解析：

建议：

```text
minecraft.block_name_unknown
```

HTTP：

```text
422
```

而不是：

```text
500
```

---

# 二十二、未知 block name

例如：

```text
minecraft:banana_ore
```

如果当前 Minecraft registry 没有：

必须：

```text
minecraft.block_name_unknown
```

不能返回：

```text
matches: []
```

否则 LLM 无法区分：

```text
“这个方块不存在”
```

和：

```text
“存在，但附近没有”
```

---

# 二十三、Policy

```text
risk = SAFE
exclusive = false
online = required
confirmation = none
explicit intent = none
trusted = none
```

允许：

```text
BACKGROUND
INITIATIVE
SYSTEM
USER
```

因为只是查询。

不过：

> 工具本身不能因为后台调用而启动移动、装备或挖掘。

---

# 二十四、Real Resource Chain

4K 真正重要的不是新工具单独通过。

而是验证：

```text
find
→ capability
→ equip
→ move
→ dig
→ dropped_items
→ pickup
→ inventory
```

这条链必须由 Smoke 明确编排。

---

# 二十五、推荐真实资源

优先：

```text
minecraft:oak_log
```

原因：

- 容易在地表找到
- 不需要地下开采
- 对不同服务器环境更友好
- 掉落 oak_log / wood 类资源易观察
- 已经在 4H 真机成功验证过 Item Entity pickup

如果当前世界找不到 oak_log：

备用：

```text
minecraft:stone
```

但 stone 本身的掉落处理要考虑服务器实际行为。

不要因为方便而直接把一个固定坐标硬编码进生产逻辑。

---

# 二十六、Resource Chain 不自动选择工具

假设：

```text
find_blocks
→ oak_log
```

之后：

```text
dig_capability
```

如果：

```text
held_item = dirt
can_dig = true
dig_time = 7500
```

而 inventory 中有 axe：

不要：

```text
自动 equip axe
```

让 LLM：

```text
minecraft_equip
```

然后再次：

```text
minecraft_dig_capability
```

确认效果。

---

# 二十七、Resource Chain Confirmation

4K 不新增“资源采集 confirmation”。

每一个世界修改动作仍然使用既有门：

```text
minecraft_equip → MEDIUM confirmation
minecraft_dig   → MEDIUM confirmation
minecraft_pickup_item → MEDIUM confirmation
```

这意味着：

> 4K 本阶段不做 task-level confirmation aggregation。

不要创建：

```text
resource_task_confirmation
gather_confirmation
```

---

# 二十八、为什么现在不做 Task Runtime

因为现在我们还在建立：

```text
原子能力
```

每个能力都已经有：

- policy
- confirmation
- ActionRuntime
- verification

如果现在直接加：

```text
ResourceGatherRuntime
```

会同时引入：

- 子动作调度
- 子动作失败
- rollback
- confirmation aggregation
- retry
- persistence

这些全部属于 5A。

4K 只证明：

> **现有原子能力可以被顺序组合。**

---

# 二十九、Smoke 正向链

建议：

```text
ensureIdle
↓
inventory before
↓
find oak_log
↓
choose first candidate
↓
dig_capability(candidate)
↓
若当前工具不理想：
    equip（明确 fixture）
↓
move_to(candidate)
↓
capability 再查一次
↓
dig(candidate, expected_block)
↓
等待 block changed
↓
dropped_items
↓
找到由这次 dig 产生的目标 Item Entity
↓
pickup_item
↓
playerCollect
↓
entity gone
↓
inventory increased
↓
inventory reread
↓
ensureIdle
```

---

# 三十、不要因为 smoke 方便而把 dig 嵌入 find

例如：

```text
minecraft_find_blocks
→ 找到 oak_log
→ 顺便自动挖
```

绝对禁止。

---

# 三十一、如何识别这次 dig 产生的 Item Entity

优先使用：

```text
playerCollect / Item Entity position / item name
```

但 Smoke 可以保存：

```text
dig_position
```

以及：

```text
inventory_before
```

然后在 `minecraft_dropped_items` 中选择：

```text
item.name == expected_drop
+
distance_to_dig_position <= small radius
+
entity was created after dig start
```

不要只选择“当前最近的 oak_log”。

避免把世界里原有掉落物错认成刚挖出来的资源。

---

# 三十二、Resource Chain 不自动 pickup 所有掉落物

只允许：

```text
一个目标 Item Entity
```

即：

```text
minecraft_pickup_item(entity_id, expected_item)
```

不能：

```text
for every dropped item:
    pickup
```

也不能：

```text
pickup nearest
```

---

# 三十三、Resource Chain 必须允许失败

例如：

```text
find
→ candidate
→ move_to
→ dig
```

如果：

```text
dig_capability.can_dig = false
```

链必须停止。

不能：

```text
“反正挖一下试试”
```

---

# 三十四、Resource Chain 必须验证每一层

不能：

```text
move_to completed
→ 假设已经到
```

必须相信当前 `4H.1` 的：

```text
completed
=> GoalNear verification passed
```

然后继续。

Dig 必须：

```text
block before
→ block after
```

Pickup 必须：

```text
playerCollect
+
entity gone
+
inventory increase
```

Inventory 必须重新读。

---

# 三十五、真实 Smoke Fixture

建议：

```text
SMOKE_RESOURCE_BLOCK="minecraft:oak_log"
```

optional。

如果没有指定：

自动搜索。

如果：

```text
matches = []
```

则：

```text
SKIPPED
```

但 4K 的工具本身仍可通过单独测试。

---

# 三十六、不要自动生成资源

禁止：

```text
/setblock oak_log
```

默认用来制造资源。

真实资源链应该尽量使用世界中自然存在的方块。

如果测试环境没有资源：

可以提供：

```text
SMOKE_RESOURCE_FIXTURE=1
```

由操作者明确开启。

Fixture 必须：

1. 保存原方块
2. 创建一个天然兼容目标
3. 完整测试
4. 还原原方块

默认不开启。

---

# 三十七、Real STOP

4K 不新增新的 STOP 硬门禁。

原因：

- `move_to` 已通过 4H.1
- `dig` 已通过 4B
- `pickup` 已通过 4H

只需要确认组合链中：

```text
ensureIdle
```

在正常结束后成立。

如果中途失败：

必须：

```text
foreground action not leaked
goal == null
isMoving == false
```

---

# 三十八、Node Tests

新增：

```text
minecraft_runtime/test/find_blocks.test.js
```

至少：

A. canonical names

B. unknown block id

C. max distance

D. max result

E. truncation

F. sorting

G. no side effects

H. block positions

I. multiple block ids

J. safe concurrent read

不要新建 ResourceGather Action test。

因为 4K 不新增复合 Action。

---

# 三十九、Python Tests

新增：

```text
tests/test_minecraft_find_blocks_tool.py
```

至少：

A. schema

B. canonicalization

C. unknown block

D. offline

E. SAFE

F. no confirmation

G. bounded distance

H. max_results

I. output projection

J. raw data not leaked

K. concurrent with foreground action

---

# 四十、flying-squid E2E

优先覆盖：

```text
find_blocks
```

如果 fake server 能提供实际 blocks：

测试：

```text
find oak_log
→ result
→ sorted
```

如果无法提供稳定资源 fixture：

至少测试：

- schema
- unknown block
- empty result
- max results
- action busy 不影响 SAFE 查询

不要伪造完整 resource chain。

---

# 四十一、Real Server Smoke 硬门禁

4K 本阶段第一次真正意义上的**组合链硬门禁**：

```text
REAL SERVER: PASS
```

至少：

```text
find target = PASS
dig capability = PASS
move_to = PASS
dig = PASS
world changed = PASS
dropped item = PASS
pickup = PASS
playerCollect = PASS
entity gone = PASS
inventory increased = PASS
inventory restored/fixture cleaned = PASS
ensureIdle = PASS
```

如果任意一步只是 fake bot：

```text
Phase 4K = BLOCKED
```

---

# 四十二、不要要求“自动选工具”

真实 smoke 可以人为准备：

```text
stone_pickaxe
```

并明确：

```text
minecraft_equip
```

然后：

```text
minecraft_dig_capability
```

用它验证链。

但不要让 4K 建立：

```text
best_tool
tool_optimizer
```

这仍然属于后续 Tool Capability Knowledge。

---

# 四十三、不要把 4K 做成自动挖矿

禁止：

```text
while inventory < target:
    find
    move
    equip
    dig
    pickup
```

禁止：

```text
minecraft_gather_resource
```

禁止：

```text
resource_count
```

禁止：

```text
max_mining_blocks
```

禁止：

```text
auto_refill
```

---

# 四十四、WebUI

新增：

## Find Blocks

输入：

```text
Block Names
Max Distance
Max Results
```

按钮：

```text
FIND
```

结果：

```text
Block
Position
GoalNear Distance
Raw Distance
```

每一项可以：

```text
USE AS TARGET
```

但按钮只是：

> 把坐标填到后续调试表单。

不能自动：

```text
move
equip
dig
pickup
```

---

# 四十五、WebUI Resource Chain

可以增加一个“Chain Preview”而不是“Gather”按钮：

```text
Find
→ Capability
→ Move
→ Dig
→ Pickup
```

仅展示当前数据和建议下一步。

不要增加：

```text
AUTO GATHER
```

因为这一层属于 5A Task Runtime。

---

# 四十六、Activity

`minecraft_find_blocks`：

不产生 activity。

资源链中现有动作照常产生：

```text
走到目标
挖掉方块
捡起掉落物
```

不要新增：

```text
“找到资源”
```

作为 world activity。

---

# 四十七、配置

新增：

```yaml
find_blocks:
  max_distance: 16
  max_results: 8
```

如果项目更适合常量：

可以使用固定安全上限：

```text
distance <= 32
results <= 16
```

但不要增加：

```text
resource_gathering.*
```

一整套配置。

---

# 四十八、文档

新增：

```text
docs/MINECRAFT_PHASE4K.md
```

同步：

```text
config/config.example.yaml
app/web/config_registry.py
webui/src/pages/Minecraft.vue
docs/README.md
CHANGELOG.md
```

明确：

```text
minecraft_find_blocks = SAFE
```

并写清：

> 只定位，不移动、不装备、不挖、不拾取。

---

# 四十九、工具数量

当前 4J 已经有：

```text
18 个 Minecraft LLM tools
```

本阶段新增：

```text
minecraft_find_blocks
```

因此完成后：

```text
19 个 Minecraft LLM tools
```

不要新增任何 resource-gather compound tool。

---

# 五十、特别安全回归

必须继续证明：

```text
INITIATIVE
BACKGROUND
SYSTEM
```

都可以调用：

```text
minecraft_find_blocks
```

但这个 SAFE 工具只能返回事实。

它不能因为：

```text
“发现 iron_ore”
```

自动启动：

```text
move_to
equip
dig
pickup
```

---

# 五十一、MAX_DATA_DEPTH

当前：

```text
MAX_DATA_DEPTH = 6
```

保持。

`find_blocks` semantic output 不要设计成：

```text
matches
  → block
    → position
      → ...
```

过度嵌套。

推荐最多：

```text
matches
  → block
  → position
  → distance
```

避免再次碰到 Phase 4F 的深度问题。

---

# 五十二、验收门禁

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

并且真实服务器：

```text
REAL SERVER: PASS
```

硬门禁：

```text
find_blocks = PASS
capability = PASS
move_to = PASS
dig = PASS
dropped_items = PASS
pickup = PASS
inventory = PASS
ensureIdle = PASS
fixture/world restored = PASS
```

任何环节只有 fake：

```text
Phase 4K = BLOCKED
```

---

# 五十三、Real STOP

不新增硬门禁。

但 Smoke 中如果主动 STOP 正在运行的：

```text
move_to
```

必须仍然得到当前 4H.1 的：

```text
CANCELLED
goal=null
isMoving=false
```

如果 STOP `dig`：

继续沿用 4B 的现有行为。

不要为了 4K 改这些动作。

---

# 五十四、最终报告

```text
Phase 4K = PASS / BLOCKED

commit:
<sha>

新增工具:
minecraft_find_blocks

risk:
find_blocks = SAFE

自动化:
pytest = ...
Node = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
find_blocks = ...
capability = ...
move_to = ...
dig = ...
world change = ...
dropped item = ...
pickup = ...
playerCollect = ...
entity gone = ...
inventory increased = ...
restore = ...
ensureIdle = ...

REAL SERVER: PASS / BLOCKED
```

任何没实际跑的：

```text
SKIPPED
```

不要伪造 PASS。

---

# 五十五、Phase 4K 真正完成后的行为链

最终必须能真实证明：

```text
“附近有什么橡木？”
        ↓
minecraft_find_blocks
        ↓
oak_log @ (x,y,z)
        ↓
minecraft_dig_capability
        ↓
当前工具状态
        ↓
minecraft_equip（如需要）
        ↓
minecraft_move_to
        ↓
minecraft_dig
        ↓
minecraft_dropped_items
        ↓
找到刚产生的 Item Entity
        ↓
minecraft_pickup_item
        ↓
minecraft_inventory
        ↓
oak_log 数量增加
```

其中每一步都是现有或本阶段明确的原子工具。

**不要把这一串包装成一个新工具。**

Phase 4K 结束后，才进入真正的 **5A Multi-step Task Runtime**：届时再解决“一个用户意图需要连续调用多个 MEDIUM/LOW 动作时，如何统一确认、暂停、恢复、失败回滚、重新规划”等问题。