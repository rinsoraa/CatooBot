基于当前最终树 `5779de8` 开始 Phase 4F。

先完整阅读当前：
- Phase 4C `minecraft_inventory` / `minecraft_place`
- Phase 4D `minecraft_equip` / `minecraft_inventory_move`
- Phase 4E `minecraft_container_inspect` / `minecraft_container_transfer`
- `MinecraftAgentPolicy`
- `ConfirmationStore`
- `TurnOrigin`
- `MinecraftService`
- `ActionRuntime`
- `minecraft_runtime/runtime.js`
- WebUI Minecraft 页面
- `smoke_real_server.js`
- 当前所有 Minecraft 测试

不要按历史阶段报告猜实现。

---

# 一、Phase 4F 目标

实现最小可靠的 Minecraft Crafting：

```text
minecraft_recipe_lookup
minecraft_craft
```

最终闭环：

```text
minecraft_inventory
        ↓
minecraft_recipe_lookup
        ↓
看到可执行配方
        ↓
用户明确要求
        ↓
minecraft_craft
        ↓
MEDIUM confirmation
        ↓
ActionRuntime
        ↓
Mineflayer bot.craft(...)
        ↓
重新读取真实 inventory
        ↓
确认材料消耗 + 产物增加
```

---

# 二、严格限制范围

本阶段只允许：

```text
玩家自身 2×2 inventory crafting
```

也就是：

```text
craftingTable = null
```

明确禁止：

- crafting table
- 自动寻找 crafting table
- 自动走到 crafting table
- 自动放置 crafting table
- container
- furnace
- blast furnace
- smoker
- smithing
- stonecutter
- brewing
- enchanting
- villager
- recipe chain
- 自动先做中间材料
- 自动补材料
- 自动挖材料
- 自动开箱取材料
- 自动 equip
- 自动移动 inventory slot
- 连续多个不同配方
- 批量生产
- 自动生产“所有够做的东西”

本阶段只做：

> **查一个目标配方 + 执行一次该配方。**

---

# 三、新工具

新增：

```text
minecraft_recipe_lookup
minecraft_craft
```

风险：

```text
recipe_lookup = SAFE
craft = MEDIUM
```

默认：

```yaml
allow_medium: false
```

保持不变。

---

# 四、minecraft_recipe_lookup

这是只读能力。

作用：

> 根据指定输出物品，查询当前玩家自身 2×2 crafting 能执行的配方。

建议输入：

```json
{
  "item": "minecraft:stick"
}
```

支持：

```text
stick
minecraft:stick
```

统一 canonical name。

---

# 五、recipe_lookup 不得暴露 raw Recipe

LLM 不应该看到 Mineflayer Recipe 原始对象。

不要返回：

- raw recipe object
- numeric item id
- metadata 原始结构
- internal minecraft-data object
- JS prototype data

只返回语义化结果。

例如：

```json
{
  "ok": true,
  "item": "minecraft:stick",
  "recipes": [
    {
      "recipe_id": "…",
      "result": {
        "name": "minecraft:stick",
        "count_per_craft": 4
      },
      "requires_table": false,
      "available": true,
      "ingredients": [
        {
          "name": "minecraft:oak_planks",
          "count": 2
        }
      ]
    }
  ]
}
```

---

# 六、为什么必须有 recipe_id

不要让 `minecraft_craft` 只接受：

```text
minecraft_craft(item="stick")
```

因为一个目标物品可能存在多个不同配方。

minecraft-data 的 recipe 数据明确允许同一输出物品存在多个不同 recipe。

例如 stick 可以由不同木材的木板制作。

所以：

```text
recipe_lookup
→ 产生明确 recipe_id
→ minecraft_craft(recipe_id)
```

才能让用户确认的是：

> “用哪一种材料做这个东西”

而不是：

> “大概随便找一个配方”。

---

# 七、recipe_id 必须稳定

禁止：

```text
recipe_id = recipes[index]
```

直接把数组 index 当永久 ID。

原因：

- recipe 顺序未必稳定
- 当前 inventory 改变可能改变 `recipesFor()` 返回结果
- 多个相似配方不能靠数组序号长期区分

建议根据规范化后的：

```text
result
requires_table
ingredients / shape
```

生成稳定 canonical signature，再得到：

```text
recipe_id
```

实现细节由当前代码风格决定。

recipe_id 不需要暴露内部数字 ID。

---

# 八、recipe_lookup 使用 Mineflayer 官方 API

优先：

```js
bot.recipesFor(...)
```

因为它会基于当前 inventory 判断哪些 recipe 能够实际制作。Mineflayer 文档明确说明，`recipesFor(..., craftingTable=null)` 只返回能够在玩家自身 inventory crafting 中执行的配方。

若需要解释：

```text
“没有足够材料”
```

可以辅以：

```js
bot.recipesAll(...)
```

但不要为了列举所有 Minecraft 配方而把整个 recipe database 塞进 LLM。

---

# 九、recipe_lookup 输出状态

至少区分：

```text
available
insufficient_material
crafting_table_required
recipe_not_found
```

不要全部返回：

```text
[]
```

建议：

```json
{
  "item": "minecraft:stick",
  "recipes": [
    {
      "recipe_id": "...",
      "available": true,
      "requires_table": false,
      ...
    }
  ]
}
```

如果只有工作台配方：

```json
{
  "item": "minecraft:chest",
  "recipes": [],
  "status": "crafting_table_required"
}
```

不要自动去找工作台。

---

# 十、minecraft_recipe_lookup 的独占性

这是纯读取：

```text
exclusive = false
risk = SAFE
```

可以与：

```text
move_to
follow_player
minecraft_inventory
```

并行。

但是仍然必须在线。

不加入：

```text
OFFLINE_TOOLS
```

---

# 十一、minecraft_craft 参数

建议第一版：

```json
{
  "recipe_id": "…"
}
```

**只执行一次 recipe。**

不要增加：

```text
count
times
amount
batch_size
```

第一版一次调用只能完成一次配方执行。

原因：

> Craft 的 `count` 在 Mineflayer 里是“执行配方多少次”，不是“产出多少个目标物品”。第一版直接固定为 1，可以彻底避免 LLM 对数量语义的误解。

后续如果需要批量 crafting，再独立设计。

---

# 十二、minecraft_craft 是 MEDIUM

原因：

它会真实改变：

```text
inventory
material counts
crafted items
```

因此：

```text
minecraft_craft = MEDIUM
```

执行链：

```text
registered
→ Minecraft enabled
→ online
→ explicit USER intent
→ trusted player（Minecraft Chat）
→ allow_medium
→ confirmation
→ runtime
```

INITIATIVE / BACKGROUND / SYSTEM 不能执行。

WebUI developer 只能发起 confirmation，不得消费真实确认。

继续保证：

```text
confirmation_not_user_turn
```

的安全边界。

---

# 十三、Confirmation 必须绑定 recipe_id

confirmation fingerprint：

```text
tool
recipe_id
```

即可。

因为 recipe_id 已经唯一绑定：

```text
目标输出
材料
配方形状
是否需要工作台
```

但确认摘要必须把人类可读信息展开。

例如：

```text
用 2 个 minecraft:oak_planks 制作 4 个 minecraft:stick
```

不要只写：

```text
执行 recipe abc123
```

---

# 十四、执行前重新解析 recipe

confirmation 不代表世界仍然没有变化。

确认后重新：

```text
recipesFor(...)
```

并：

```text
recipe_id → 当前真实 Recipe
```

找不到：

```text
minecraft.recipe_changed
```

或者更明确的：

```text
minecraft.recipe_unavailable
```

都可以。

不能拿第一次 lookup 返回的旧 Recipe 对象直接执行。

---

# 十五、执行前重新验证 inventory

重新读取当前真实 inventory。

至少确认：

```text
recipe 仍 available
```

不能使用旧的：

```text
minecraft_recipe_lookup
```

结果作为执行依据。

用户在两次消息之间可能：

- 挖掉材料
- 合成掉材料
- 从容器拿走材料
- inventory 被其他动作改变
- 被服务器修改

所以：

> Lookup 是建议，Craft start 时的真实 inventory 才是硬门禁。

---

# 十六、严格禁止自动准备材料

如果：

```text
oak_planks 不足
```

不要：

```text
自动开箱
自动移动 inventory
自动挖木头
自动把原木做木板
```

直接：

```text
minecraft.material_insufficient
```

或者当前项目统一错误码。

---

# 十七、严格禁止 recipe chain

例如：

```text
用户要求 chest
```

如果缺：

```text
oak_planks
```

不要：

```text
oak_log
→ oak_planks
→ chest
```

本阶段必须一次一个 recipe。

用户必须主动提出下一步。

---

# 十八、只支持 craftingTable=null

执行：

```js
await bot.craft(recipe, 1, null)
```

不要：

```text
bot.craft(recipe, 1, someBlock)
```

不要自动打开 crafting table。

Mineflayer 的 `craft()` 本身支持传入 crafting table，但本阶段明确只使用 `null`，把工作台留给后续阶段。

---

# 十九、Craft ActionRuntime

使用当前：

```text
ActionRuntime
```

不要直接在 Service：

```text
await bot.craft(...)
```

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

# 二十、Craft action 性质

```text
exclusive = true
risk = MEDIUM
detached = true
```

原因：

- crafting 期间 inventory 正在改变
- 不能和 inventory_move 并行
- 不能和 equip 并行
- 不能和 container_transfer 并行
- 不能和 dig/place/move_to/follow 并行

例如：

```text
minecraft_craft + minecraft_inventory_move
→ action.busy
```

必须成立。

---

# 二十一、Craft 前后状态快照

执行前保存：

```json
{
  "recipe_id": "...",
  "result": {
    "name": "minecraft:stick",
    "count_per_craft": 4
  },
  "ingredients": [
    {
      "name": "minecraft:oak_planks",
      "count": 2
    }
  ],
  "inventory_before": ...
}
```

不要保存 live Item object。

全部保存：

```text
{name, count}
```

或者当前项目已有 semantic snapshot。

不要再次踩 Phase 4E 那种 live reference 快照问题。

---

# 二十二、调用 bot.craft

执行：

```js
await bot.craft(recipe, 1, null)
```

Mineflayer 官方文档说明，Promise resolve 时 crafting 已完成且 inventory 已更新。

但：

> Promise resolve 仍然不能直接当作 CatooBot 最终成功依据。

必须再次读取 inventory。

---

# 二十三、成功验证

至少验证：

### 产物

目标物品总数：

```text
after_output - before_output >= recipe_result_count
```

例如：

```text
stick
before = 4
after = 8
count_per_craft = 4
```

通过。

### 材料

对应 ingredient 的总数量必须出现合理减少。

但不要硬编码：

```text
-2
```

因为：

- stack 分布不同
- recipe ingredient 表达可能包含多个 stack
- 模组环境可能存在差异

应根据当前 Recipe 的 ingredient semantic description 计算预期下界，并以最终 inventory 为硬事实。

---

# 二十四、为什么不能只检查产物

必须同时检查：

```text
material decreased
+
output increased
```

否则可能出现：

```text
bot.craft() resolve
但服务器没有真正消耗材料
```

或者：

```text
inventory packet 延迟
```

导致错误判定。

最终成功必须证明：

```text
材料发生合理消耗
+
目标物品增加
```

---

# 二十五、Inventory 总量计算

推荐增加内部 helper：

```text
countInventoryItem(bot, canonicalItemName)
```

按所有玩家 inventory slots 汇总。

不要只检查：

```text
heldItem
```

因为 crafted item 很可能落到其他 slot。

---

# 二十六、结果结构

建议：

```json
{
  "recipe_id": "...",
  "result": {
    "name": "minecraft:stick",
    "count_per_craft": 4,
    "crafted_count": 4
  },
  "ingredients": [
    {
      "name": "minecraft:oak_planks",
      "consumed": 2
    }
  ],
  "before": {
    "result_count": 4,
    "ingredient_counts": {
      "minecraft:oak_planks": 8
    }
  },
  "after": {
    "result_count": 8,
    "ingredient_counts": {
      "minecraft:oak_planks": 6
    }
  }
}
```

最终事实全部来自重新读取的 inventory。

---

# 二十七、Error Codes

至少提供：

```text
minecraft.recipe_not_found
minecraft.recipe_unavailable
minecraft.material_insufficient
minecraft.recipe_changed
minecraft.craft_failed
minecraft.craft_unconfirmed
```

如果当前项目已有更统一的 namespace，则按照项目风格调整。

不要返回：

```text
500 craft failed
```

一类没有语义的错误。

---

# 二十八、Cancellation

Craft 同样需要：

```text
CANCELLED
TIMEOUT
disconnect
shutdown
```

但注意：

> `runCancellable()` 不能取消底层 `bot.craft()` Promise。

所以需要明确判断：

```text
craft 实际是否已经开始
```

以及：

```text
token.cancelled
```

之后不要错误报告 `SUCCEEDED`。

如果 Mineflayer 已经完成 crafting，但 CatooBot 同时收到 STOP：

使用当前 ActionRuntime race 规则，只允许一个终态。

真实底层 inventory 状态必须在测试中核验。

---

# 二十九、STOP 不设为 Real Server 硬门禁

Craft 通常非常短。

因此：

```text
Real STOP = SKIPPED 可以接受
```

但 Node：

```text
cancel
timeout
race
cleanup
terminal exactly once
```

全部必须测试。

不要为了抢取消窗口而修改 bot.craft 行为。

---

# 三十、minecraft_recipe_lookup 测试

Python：

```text
tests/test_minecraft_recipe_tool.py
```

覆盖：

- schema
- canonical item
- offline
- SAFE 不要求 confirmation
- recipe not found
- multiple recipes
- available recipe
- insufficient materials
- crafting table required
- output semantic projection
- raw Recipe 不泄露
- stable recipe_id
- busy 行为

---

# 三十一、minecraft_craft Python 测试

新增：

```text
tests/test_minecraft_craft_tool.py
```

覆盖：

- schema
- invalid recipe_id
- offline
- allow_medium=false
- no explicit intent
- untrusted Minecraft player
- confirmation_required
- confirmation mismatch
- WebUI self-authorization denied
- recipe unavailable after confirmation
- insufficient materials
- successful flow
- runtime failure
- craft_unconfirmed
- action.busy

---

# 三十二、Node Runtime Tests

新增：

```text
minecraft_runtime/test/recipe.test.js
minecraft_runtime/test/craft.test.js
```

Craft test 必须使用完整 fake inventory / fake Recipe。

### Recipe

A. recipe lookup

B. multiple recipes

C. stable recipe_id

D. insufficient materials

E. table-required recipe excluded from executable inventory crafting

### Craft

F. success

G. output reread

H. ingredient reread

I. craft_unconfirmed

J. recipe changed

K. insufficient materials

L. cancellation

M. timeout

N. race

O. cleanup exactly once

P. action.busy

---

# 三十三、flying-squid E2E

不要伪造真实 crafting。

如果当前 fake server 无法真实支持 inventory crafting：

至少覆盖：

```text
recipe lookup semantic projection
craft schema
empty inventory
recipe unavailable
action.busy
confirmation
```

成功路径使用：

```text
fake Recipe + fake inventory Node tests
+
Real Java Server
```

如果之后 flying-squid 能稳定提供 crafting capability，再增加成功路径。

---

# 三十四、Real Java Smoke

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
inventory before
↓
recipe lookup
↓
选择一个无需工作台、材料可控的 recipe
↓
craft
↓
RUNNING
↓
completed
↓
inventory reread
↓
验证 output 增加
↓
验证 ingredient 合理减少
↓
恢复 fixture
↓
ensureIdle
↓
disconnect
```

---

# 三十五、推荐 Smoke Recipe

不要依赖你当前世界“碰巧有材料”。

优先使用服务器命令创建临时 fixture。

推荐：

```text
2 × minecraft:oak_planks
→ minecraft:stick × 4
```

因为：

- 玩家 2×2 crafting 即可
- 不需要 crafting table
- recipe 简单
- 输出明确
- 很容易做 before/after
- 容易清理

但不要硬编码 recipe_id。

应通过：

```text
minecraft_recipe_lookup
```

找到对应 oak_planks → stick 配方。

---

# 三十六、Real Fixture

如果 bot 有权限：

```text
/give <bot> minecraft:oak_planks 2
```

执行前记录整个 inventory signature。

Craft：

```text
oak_planks ×2
→ stick ×4
```

完成后：

```text
clear <bot> minecraft:stick
clear <bot> minecraft:oak_planks
```

然后重新读取 inventory。

最终：

```text
before inventory signature
==
after inventory signature
```

如果 `/give` / `/clear` 不可用：

```text
SKIPPED
```

不得伪造 Real Server PASS。

---

# 三十七、Real Server 六层证据

至少输出：

```text
✓ recipe lookup = PASS
✓ recipe_id stable = PASS
✓ craft → RUNNING + action_id
✓ real craft completed
✓ output increased
✓ ingredient decreased
✓ inventory reread agrees
✓ fixture restored
✓ ensureIdle
✓ REAL SERVER: PASS
```

不能只输出：

```text
bot.craft resolved
```

就算成功。

---

# 三十八、不要修改 WorldPerception

Craft 本阶段主要修改：

```text
inventory
```

而不是：

```text
world blocks
```

所以不要假装：

```text
WorldPerception world.changed
```

如果当前 WorldPerception 已经能通过 `snapshot.self` 感知 inventory/held item变化，可以作为附加证据。

否则：

```text
inventory reread
```

就是本阶段的主要事实来源。

---

# 三十九、Activity

成功后记录事实：

```text
制作了 4 个 minecraft:stick
```

不要：

```text
“罐头做了很多木棍”
```

也不要添加情绪推测。

---

# 四十、WebUI

Minecraft 页面增加：

## Recipe Lookup

输入：

```text
Item
```

显示：

```text
Recipe ID
Result
Ingredients
Available
Requires Table
```

## Craft Test

填写：

```text
Recipe ID
```

显示：

```text
recipe summary
confirmation state
action state
before
after
```

真实 Craft 仍必须走 MEDIUM confirmation。

WebUI developer：

```text
SYSTEM
```

不得消费 confirmation。

---

# 四十一、文档

同步：

```text
config/config.example.yaml
app/web/config_registry.py
webui/src/pages/Minecraft.vue
docs/MINECRAFT_PHASE4F.md
CHANGELOG.md
docs/README.md
```

工具数量与实际一致。

当前已有 11 个 Minecraft LLM tools：

```text
minecraft_world
minecraft_chat
minecraft_look_at
minecraft_move_to
minecraft_follow_player
minecraft_stop
minecraft_dig
minecraft_inventory
minecraft_place
minecraft_equip
minecraft_inventory_move
minecraft_container_inspect
minecraft_container_transfer
```

注意：请先读取当前仓库实际数量，不要直接照抄这个列表；当前 4E 已经不是早期的 9 个工具。

新增：

```text
minecraft_recipe_lookup
minecraft_craft
```

默认：

```yaml
allow_medium: false
```

仍然保持。

---

# 四十二、与 4E 的边界

本阶段不得偷偷调用：

```text
minecraft_container_transfer
minecraft_equip
minecraft_inventory_move
minecraft_place
minecraft_dig
```

也就是说：

```text
Craft 只能使用当前 inventory 已经拥有的材料。
```

如果没有材料：

```text
失败
```

而不是：

```text
自动准备材料
```

这样能保持每个阶段都只有一个清晰能力边界。

---

# 四十三、特别测试：禁止“自动升级”

场景：

```text
用户：
“做一个箱子”
```

如果：

```text
只有 oak_log
没有 oak_planks
```

Phase 4F 不允许自己：

```text
log
→ planks
→ chest
```

必须：

```text
material_insufficient
```

或者让上层下一轮明确请求：

```text
把 oak_log 做成 oak_planks
```

这样后面的行为链才不会失控。

---

# 四十四、ActionRuntime 观察项

不要在本阶段重新设计 Phase 4E 的 Container Window cleanup。

但在 Craft 实现中不要继续扩大：

```text
async cleanup
```

的语义范围。

如果当前测试发现：

```text
CANCELLED/TIMEOUT
```

与底层 crafting Promise 的终止状态无法可靠同步：

先如实暴露问题，不要把 ActionRuntime 的终态语义偷偷改掉。

必要时单独开：

```text
Phase 4E.1 — Async Cleanup Contract Hardening
```

而不是混进 Craft commit。

---

# 四十五、验收门禁

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
recipe lookup = PASS
craft RUNNING = PASS
craft completed = PASS
output reread = PASS
ingredient reread = PASS
fixture restored = PASS
ensureIdle = PASS
```

如果真实服务器没有实际产出物品：

```text
Phase 4F = BLOCKED
```

不能以 fake bot 替代。

---

# 四十六、最终报告

沿用此前格式：

```text
Phase 4F = PASS / BLOCKED

commit:
<sha>

新增工具:
minecraft_recipe_lookup
minecraft_craft

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
recipe lookup = ...
craft = ...
output change = ...
ingredient change = ...
inventory restored = ...
ensureIdle = ...

Real STOP:
PASS / SKIPPED

REAL SERVER: PASS / BLOCKED
```

任何没有实际执行的项目：

```text
SKIPPED
```

不要伪造 PASS。