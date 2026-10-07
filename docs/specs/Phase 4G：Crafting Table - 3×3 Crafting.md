基于当前仓库最新提交 `52921f6` 开始 Phase 4G。

先完整阅读：
- Phase 4F `minecraft_recipe_lookup`
- Phase 4F `minecraft_craft`
- `recipe_id` canonical signature 实现
- `MinecraftService`
- `MinecraftAgentPolicy`
- `ConfirmationStore`
- `TurnOrigin`
- `ActionRuntime`
- `minecraft_runtime/runtime.js`
- `minecraft_runtime/test/recipe.test.js`
- `minecraft_runtime/test/craft.test.js`
- `smoke_real_server.js`
- WebUI Crafting 面板
- 当前 `MAX_DATA_DEPTH = 6` 实现与测试

不要根据历史报告猜当前接口。

---

# 一、Phase 4G 目标

把 Phase 4F 的：

```text
玩家自身 2×2 Crafting
```

扩展为：

```text
玩家自身 2×2
+
明确指定 Crafting Table 的 3×3
```

核心仍然只有：

```text
minecraft_recipe_lookup
minecraft_craft
```

**不要新增：**

```text
minecraft_workbench_lookup
minecraft_workbench_craft
```

避免出现重复 Craft API。

---

# 二、向后兼容要求

Phase 4F 当前调用：

```json
{
  "item": "minecraft:stick"
}
```

必须继续工作。

Phase 4F 当前：

```json
{
  "recipe_id": "stick*4=oak_planks*2"
}
```

也必须继续工作。

因此 `crafting_table` 必须是：

```text
OPTIONAL
```

而不是改成 required。

---

# 三、recipe_lookup 新参数

扩展为：

```json
{
  "item": "minecraft:chest",
  "crafting_table": {
    "x": 100,
    "y": 64,
    "z": 100
  }
}
```

其中：

```text
crafting_table = null / omitted
```

表示：

```text
2×2 player inventory crafting
```

提供后表示：

```text
指定的这个 Crafting Table
```

---

# 四、craft 新参数

扩展为：

```json
{
  "recipe_id": "chest*1=iron_ingot*?",
  "crafting_table": {
    "x": 100,
    "y": 64,
    "z": 100
  }
}
```

实际 recipe_id 格式以当前 canonical signature 实现为准。

不要重新设计 recipe_id。

---

# 五、为什么 Crafting Table 必须是坐标

不要允许：

```text
"crafting_table": "nearest"
```

不要允许：

```text
"crafting_table": "auto"
```

不要允许：

```text
"crafting_table": "any"
```

LLM 必须明确指定：

```text
x
y
z
```

原因：

> 用户确认的必须是一个明确的世界交互对象，而不是一个执行时才决定的隐式目标。

---

# 六、recipe_lookup 行为

### 没有 Crafting Table

继续：

```text
bot.recipesFor(itemType, null, 1, null)
```

或者使用当前 4F 已验证的等价调用方式。

### 指定 Crafting Table

先：

```text
table = bot.blockAt(position)
```

然后验证：

```text
table.name === "minecraft:crafting_table"
```

再：

```text
bot.recipesFor(itemType, null, 1, table)
```

Mineflayer 当前公开 API 的 `recipesFor()` 第四个参数就是 `Block | boolean | null` 类型的 crafting table。([github.com](https://github.com/PrismarineJS/mineflayer/blob/master/index.d.ts))

---

# 七、Crafting Table 验证

实时验证：

```text
block != null
block.name == minecraft:crafting_table
```

否则：

```text
minecraft.crafting_table_missing
```

如果：

```text
block.name != minecraft:crafting_table
```

返回：

```text
minecraft.crafting_table_invalid
```

不要猜。

---

# 八、不要支持“工作台类”

不要把以下内容当作 Crafting Table：

```text
stonecutter
cartography_table
smithing_table
loom
fletching_table
```

第一版：

```text
minecraft:crafting_table
```

只有这一种。

---

# 九、Crafting Table 距离

与 4B/4C/4E 保持一致。

建议：

```text
CRAFTING_TABLE_MAX_DISTANCE = 5
```

具体如果已有交互距离 helper，则复用。

计算：

```text
bot eyes
→ crafting table block center
```

超过：

```text
minecraft.crafting_table_too_far
```

禁止：

```text
自动 move_to
```

---

# 十、不要自动导航

绝对禁止：

```text
recipe_lookup
→ 自动去找工作台
```

以及：

```text
craft
→ move_to(table)
→ craft
```

本阶段只负责：

> 使用“我明确告诉你的这一张工作台”。

---

# 十一、recipe_lookup 输出

如果指定 Crafting Table：

```json
{
  "ok": true,
  "item": "minecraft:chest",
  "crafting_table": {
    "x": 100,
    "y": 64,
    "z": 100
  },
  "recipes": [
    {
      "recipe_id": "...",
      "result": {
        "name": "minecraft:chest",
        "count_per_craft": 1
      },
      "requires_table": true,
      "available": true,
      "ingredients": [...]
    }
  ]
}
```

不要继续返回 raw `Recipe`。

---

# 十二、recipe_id 必须保持兼容

当前 4F 已经使用：

```text
产物
+
材料
+
形状
+
是否需要工作台
```

生成 canonical signature。

4G 不得重新定义这一规则。

尤其：

```text
2×2 recipe
```

与：

```text
3×3 recipe
```

不能产生错误碰撞。

如果当前 `requires_table` 已经参与签名：

继续使用。

如果当前实现的“形状”不足以区分两个 recipe：

先修复 recipe canonicalization，再继续 4G。

不要靠：

```text
crafting_table=true/false
```

临时拼接字符串。

---

# 十三、recipe_lookup 的状态

至少继续正确区分：

```text
available
insufficient_material
crafting_table_required
recipe_not_found
```

对于：

```text
item = chest
crafting_table omitted
```

应该：

```text
crafting_table_required
```

而：

```text
item = chest
crafting_table = valid table
```

才可以得到：

```text
available
```

前提是材料足够。

---

# 十四、minecraft_craft

仍然：

```text
risk = MEDIUM
exclusive = true
detached = true
```

不要修改风险等级。

---

# 十五、Confirmation Fingerprint

4F 原来：

```text
tool
recipe_id
```

4G 加入工作台坐标。

因此：

### 无 Crafting Table

```text
tool
recipe_id
```

### 有 Crafting Table

```text
tool
recipe_id
crafting_table.x
crafting_table.y
crafting_table.z
```

任意坐标变化：

```text
旧 confirmation 作废
→ 新 PENDING
```

这是强制要求。

因为：

```text
同一个 recipe
```

在两个不同工作台位置执行，是两个不同的世界操作上下文。

---

# 十六、Confirmation 摘要

2×2：

```text
用 2 个 oak_planks 制作 4 个 stick
```

保持。

3×3：

```text
用 8 个 oak_planks 在 (100,64,100) 的工作台制作 1 个 chest
```

具体模板根据 recipe 自动展开。

不能只：

```text
执行 recipe xxx
```

---

# 十七、确认后的重新验证

与 4F 保持：

```text
confirmation
↓
当前 recipe table
↓
当前 table block
↓
当前 inventory
↓
craft
```

绝对不能：

```text
lookup 的 Recipe object
→
等确认
→
直接执行旧 object
```

---

# 十八、执行前必须重新验证 Crafting Table

确认后：

```text
table = bot.blockAt(...)
```

要求：

```text
table.name == minecraft:crafting_table
```

如果用户确认后把工作台挖掉：

```text
minecraft.crafting_table_missing
```

如果工作台被替换成箱子：

```text
minecraft.crafting_table_invalid
```

不能继续。

---

# 十九、Craft Action

调用：

```js
await bot.craft(recipe, 1, craftingTable)
```

而不是：

```js
await bot.craft(recipe, 1, null)
```

当：

```text
crafting_table
```

存在时。

Mineflayer 当前 `craft()` 官方签名支持传入 Crafting Table Block。([github.com](https://github.com/PrismarineJS/mineflayer/blob/master/index.d.ts))

---

# 二十、只执行一次 Recipe

继续：

```text
count = 1
```

不要重新引入：

```text
times
count
batch_size
```

4G 只是把 Crafting context 从：

```text
2×2
```

扩展到：

```text
3×3
```

不是增加批量生产。

---

# 二十一、3×3 recipe 的执行验证

执行后重新读取整个 inventory：

```text
inventory_after
```

验证：

```text
result count increase
+
每种 ingredient 合理减少
```

与 4F 相同。

不要因为使用 Crafting Table 就放宽验证。

---

# 二十二、Crafting Table 不应成为 inventory 输出

`minecraft_inventory` 仍只返回当前五项：

```text
online
selected_hotbar_slot
held_item
items
```

不要往 inventory 输出里加入：

```text
nearest_crafting_table
last_crafting_table
```

那会把世界状态污染进 inventory 工具。

---

# 二十三、WorldPerception

不要假装：

```text
craft
→ world.changed
```

Crafting Table 本身没有发生变化。

本阶段主要验证：

```text
inventory before
inventory after
```

如果当前 `snapshot.self` 已包含 inventory/held state：

可以作为辅助证据。

---

# 二十四、与 minecraft_place 的联动边界

Craft 本身不得：

```text
自动放置 Crafting Table
```

例如：

```text
用户：
“做一个 chest”
```

如果没有当前 Crafting Table：

```text
minecraft.recipe_lookup
→ crafting_table_required
```

不能：

```text
place crafting table
→ equip
→ craft
```

必须等上层明确完成：

```text
minecraft_place
```

或者用户提供已有工作台坐标。

---

# 二十五、与 inventory_move / equip 的并发

保持：

```text
crafting
+
equip
```

→ `action.busy`

```text
crafting
+
inventory_move
```

→ `action.busy`

```text
crafting
+
container_transfer
```

→ `action.busy`

```text
crafting
+
dig
```

→ `action.busy`

```text
crafting
+
place
```

→ `action.busy`

因为 crafting 正在修改 inventory / item state。

---

# 二十六、Node Runtime

继续复用现有：

```text
craft
```

Action。

不要增加：

```text
craft_at_table
```

这样 ActionRuntime 不会出现两个语义相同的 action。

Action state：

```text
IDLE
→ QUEUED
→ RUNNING
→ SUCCEEDED/FAILED/CANCELLED/TIMEOUT
```

保持现有 detached 行为。

---

# 二十七、配置

如果当前 `CraftConfig` 已有：

```text
timeout
```

继续复用。

如果需要增加：

```text
max_distance
```

只增加一个：

```text
crafting_table.max_distance
```

不要为了 4G 创建一堆：

```text
table_timeout
recipe_timeout
lookup_distance
craft_distance
```

过度配置。

---

# 二十八、Python Tests

扩展：

```text
tests/test_minecraft_recipe_tool.py
tests/test_minecraft_craft_tool.py
```

新增至少：

### recipe_lookup

A. 2×2 lookup 与 4F 完全兼容

B. 3×3 table lookup

C. table missing

D. invalid table block

E. too far

F. no table → crafting_table_required

G. table + sufficient materials → available

H. recipe_id 不因为 inventory 变化漂移

I. table 坐标进入 semantic result

### craft

J. optional crafting_table schema

K. confirmation fingerprint 包含 table 坐标

L. 坐标变化 → confirmation mismatch

M. confirmed 后 table 被挖掉 → crafting_table_missing

N. confirmed 后 table 被替换 → crafting_table_invalid

O. successful 3×3 craft

P. inventory verification

Q. action.busy

R. WebUI SYSTEM 不得自行消费 confirmation

---

# 二十九、Node Tests

扩展：

```text
minecraft_runtime/test/recipe.test.js
minecraft_runtime/test/craft.test.js
```

新增：

A. table-aware recipe lookup

B. table not found

C. invalid table

D. too far

E. 3×3 recipe selected

F. 2×2 recipe with table still works

G. `craft(recipe,1,table)`

H. post-inventory verification

I. recipe changed

J. race

K. timeout

L. cancellation

M. terminal exactly once

---

# 三十、Fake Bot

Fake Bot 必须增加：

```text
blockAt(tablePos)
recipesFor(...)
craft(recipe, 1, table)
inventory
```

不要把：

```text
craftingTable = {}
```

当作真实 block。

必须至少验证：

```text
table.name
table.position
```

都正确传递。

---

# 三十一、flying-squid E2E

如果 flying-squid 当前不能真实支持：

```text
3×3 crafting
```

不要伪造成功。

至少测试：

```text
table missing
invalid table
schema
confirmation
busy
recipe lookup semantic projection
crafting_table_required
```

真实 3×3 成功路径由 Real Server Smoke 硬门禁覆盖。

---

# 三十二、Real Java Smoke

扩展：

```text
minecraft_runtime/test/smoke_real_server.js
```

建议：

```text
connect
↓
ensureIdle
↓
记录完整 inventory
↓
创建/指定临时 Crafting Table
↓
给 bot 足够的测试材料
↓
recipe_lookup(chest, table)
↓
验证 available
↓
craft
↓
RUNNING + action_id
↓
completed
↓
inventory reread
↓
验证 chest +1
↓
验证 oak_planks 合理减少
↓
清理 chest
↓
清理 crafting table
↓
恢复 inventory
↓
逐槽签名比较
↓
ensureIdle
↓
disconnect
```

---

# 三十三、推荐 Real Smoke Recipe

优先：

```text
8 × minecraft:oak_planks
→ chest ×1
```

这是一个非常好的 3×3 测试：

- 明确需要 Crafting Table
- 输出 1 个
- 材料简单
- 2×2 无法执行
- 3×3 可以执行
- before/after 很容易验证

但：

> 不要硬编码 recipe_id。

必须先：

```text
minecraft_recipe_lookup(
  item = minecraft:chest,
  crafting_table = target
)
```

取得真实 recipe_id。

---

# 三十四、Real Fixture

支持：

```text
SMOKE_CRAFTING_TABLE_TARGET="x,y,z"
```

优先使用用户指定的已有 Crafting Table。

如果没有：

只有在 bot 有权限时，才允许：

```text
/setblock x y z minecraft:crafting_table
```

但必须：

1. 记录原方块名
2. 创建临时工作台
3. Smoke 完成
4. 恢复原方块

如果无法安全恢复：

```text
SKIPPED
```

不要伪造 PASS。

---

# 三十五、材料 Fixture

如果需要 oak_planks：

```text
/give <bot> minecraft:oak_planks 8
```

如果 `/give` 不可用：

不要假定可以继续。

可选：

```text
SMOKE_CRAFTING_TABLE_ITEM
```

但不要自动改生产逻辑。

如果无法获得材料：

```text
REAL SERVER = SKIPPED
```

而不是：

```text
PASS
```

---

# 三十六、真实证据

至少：

```text
✓ table lookup = PASS
✓ table type = minecraft:crafting_table
✓ table distance = PASS
✓ recipe requires table = true
✓ recipe available = true
✓ craft RUNNING + action_id
✓ craft completed
✓ output increased
✓ ingredients decreased
✓ inventory reread agrees
✓ fixture restored
✓ inventory restored
✓ ensureIdle
✓ REAL SERVER: PASS
```

---

# 三十七、特别验证“2×2 与 3×3 是两个上下文”

必须有一个测试证明：

```text
minecraft_chest
```

在：

```text
crafting_table = null
```

时：

```text
crafting_table_required
```

而在：

```text
crafting_table = valid table
```

时：

```text
available
```

这是一条非常重要的架构回归。

---

# 三十八、WebUI

当前 Crafting 面板增加：

```text
Crafting Context
```

例如：

```text
[ Player 2×2 ]
[ Crafting Table 3×3 ]
```

选择：

### Player

不需要坐标。

### Crafting Table

显示：

```text
x
y
z
```

按钮：

```text
LOOKUP
CRAFT
```

Recipe table 中显示：

```text
requires_table
crafting_table position
```

但真实 Craft 仍然必须经过 MEDIUM confirmation。

WebUI SYSTEM：

```text
不能消费 confirmation
```

---

# 三十九、Confirmation UI

确认摘要：

2×2：

```text
用 2 个 oak_planks 制作 4 个 stick
```

3×3：

```text
用 8 个 oak_planks 在 (100,64,100) 的 Crafting Table 制作 1 个 chest
```

不要把：

```text
recipe_id
```

本身当确认摘要。

---

# 四十、文档

同步：

```text
config/config.example.yaml
app/web/config_registry.py
webui/src/pages/Minecraft.vue
docs/MINECRAFT_PHASE4G.md
CHANGELOG.md
docs/README.md
```

明确：

```text
2×2 = 不需要工作台
3×3 = 需要明确指定 Crafting Table
```

并强调：

```text
不自动导航
不自动寻找工作台
不自动放置工作台
不自动准备材料
不自动 recipe chain
```

---

# 四十一、不要扩展到工作台以外

严格禁止：

```text
smithing_table
furnace
blast_furnace
smoker
stonecutter
loom
cartography
brewing
enchanting
```

这些以后单独规划。

---

# 四十二、MAX_DATA_DEPTH 回归

Phase 4F 刚修复：

```text
MAX_DATA_DEPTH = 6
```

Phase 4G 不得回退。

至少保留：

```text
第 5 层 recipe result/material
```

的回归测试。

如果 Crafting Table semantic projection 新增一层：

不要偷偷再次提高到 10。

先检查数据结构是否过度嵌套。

---

# 四十三、不要修改 ActionRuntime 取消语义

当前 4F 已明确没有修改：

```text
runCancellable
```

的底层 Promise 语义。

4G 继续保持。

如果出现：

```text
craft + crafting_table
```

相关的新 race 问题：

单独报告，不要把 ActionRuntime 大改混进 4G。

---

# 四十四、验收门禁

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

硬门禁：

```text
table lookup = PASS
2×2/3×3 context distinction = PASS
3×3 craft = PASS
inventory output reread = PASS
ingredient reread = PASS
fixture restored = PASS
inventory restored = PASS
ensureIdle = PASS
```

如果只在 fake bot 成功：

```text
Phase 4G = BLOCKED
```

不能用单测替代真机。

---

# 四十五、Real STOP

继续：

```text
craft STOP = 非硬门禁
```

因为实际 crafting 很快。

但是 Node 单测必须覆盖：

```text
cancel
timeout
race
cleanup
terminal exactly once
```

真实服务器如果 STOP 前已经完成：

```text
SKIPPED
```

不伪造。

---

# 四十六、最终报告格式

```text
Phase 4G = PASS / BLOCKED

commit:
<sha>

功能:
minecraft_recipe_lookup
minecraft_craft

contexts:
2×2 = PASS
3×3 = PASS

risk:
recipe_lookup = SAFE
craft = MEDIUM

自动化:
pytest = ...
Node = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
crafting_table lookup = ...
3×3 craft = ...
output = ...
ingredients = ...
inventory restored = ...
table restored = ...
ensureIdle = ...

Real STOP:
PASS / SKIPPED

REAL SERVER: PASS / BLOCKED
```

没有实际验证：

```text
SKIPPED
```

不要伪造。