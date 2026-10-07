基于当前最终树 `a8b5bcb` 开始 Phase 4J。

先完整阅读：

- Phase 4I `minecraft_dig + expected_tool`
- Phase 4D `minecraft_inventory`
- `MinecraftService`
- `MinecraftAgentPolicy`
- `ActionRuntime`
- `runtime.js`
- `minecraft_world`
- 当前 dig/equip Node/Python/WebUI tests
- `smoke_real_server.js`

不要按历史报告猜当前接口。

---

# 一、Phase 4J 目标

实现一个新的：

```text
minecraft_dig_capability
```

这是**只读 SAFE 工具**。

目标：

> 告诉 LLM：当前站在这个位置、当前主手拿着这个物品时，面对指定方块，实际能不能挖，以及预计需要多久。

最终：

```text
minecraft_world
      ↓
看到目标方块
      ↓
minecraft_inventory
      ↓
看到当前主手
      ↓
minecraft_dig_capability
      ↓
can_dig
dig_time_ms
current_tool
      ↓
LLM 决定是否调用 minecraft_equip / minecraft_dig
```

---

# 二、严格范围

本阶段只实现：

```text
查询当前实际挖掘能力
```

禁止：

- 自动装备
- 自动换槽
- 自动移动
- 自动挖
- 自动导航
- 自动寻找矿
- 自动找工具
- 自动制作工具
- 自动修工具
- 自动连续采集
- 最佳工具排序
- 工具耐久管理
- 资源规划
- mining loop
- resource gathering

特别重要：

> `minecraft_dig_capability` 永远不能修改世界或 inventory。

---

# 三、新工具风险

```text
minecraft_dig_capability = SAFE
```

并且：

```text
exclusive = false
```

它可以和：

```text
move_to
follow_player
dig
place
inventory
equip
pickup
craft
```

并行读取。

仍然要求在线。

不要加入：

```text
OFFLINE_TOOLS
```

---

# 四、Tool Schema

建议：

```json
{
  "x": 100,
  "y": 64,
  "z": 100
}
```

三个参数：

```text
required
integer
```

只针对一个明确方块。

不允许：

```text
x/y/z 浮点
```

不允许：

```text
block name
```

原因：

> capability 应该查询**真实当前位置的真实方块**，不要让调用者提供一个“自己声称的 block name”。

---

# 五、不要增加 expected_tool 参数

这里与 `minecraft_dig` 故意不同。

不要：

```json
{
  "x": 100,
  "y": 64,
  "z": 100,
  "expected_tool": "minecraft:diamond_pickaxe"
}
```

因为这个工具的问题是：

> “我现在真实拿着的东西，能不能挖这个真实方块？”

而不是：

> “假设我有某把工具，能不能挖？”

后者需要独立的离线知识系统，暂时不做。

---

# 六、Start 时实时读取

执行时：

```text
bot.online
+
bot.blockAt(target)
+
bot.heldItem
```

全部实时读取。

不能使用：

```text
minecraft_world
minecraft_inventory
```

之前返回的缓存作为硬事实。

---

# 七、目标方块不存在

如果：

```text
block == null
```

返回：

```text
minecraft.block_unavailable
```

结构化错误。

---

# 八、目标是空气

如果：

```text
block.name == minecraft:air
```

返回正常数据：

```json
{
  "ok": true,
  "position": {...},
  "block": "minecraft:air",
  "can_dig": false,
  "reason": "air"
}
```

不要把空气当异常服务器错误。

---

# 九、距离

复用当前 dig 的交互距离语义。

如果目标超过当前 dig 的最大交互距离：

```text
can_dig = false
reason = "too_far"
```

不要：

```text
move_to
```

不要让 capability 工具产生任何 movement。

---

# 十、当前主手

返回：

```json
{
  "held_item": {
    "name": "minecraft:stone_pickaxe",
    "count": 1
  }
}
```

如果空手：

```json
{
  "held_item": null
}
```

不要返回：

- raw Item
- NBT
- components
- internal id

继续使用 semantic projection。

---

# 十一、canDigBlock

优先使用当前 Mineflayer：

```js
bot.canDigBlock(block)
```

官方 API 明确提供该方法。([GitHub](https://github.com/PrismarineJS/mineflayer/blob/master/docs/api.md))

结果：

```text
can_dig = true / false
```

不要自行复制一整套 Minecraft block hardness / tool rule。

---

# 十二、digTime

使用：

```js
bot.digTime(block)
```

官方 API 返回预计挖掘时间，单位毫秒。([GitHub](https://github.com/PrismarineJS/mineflayer/blob/master/docs/api.md))

返回：

```json
{
  "dig_time_ms": 1500
}
```

如果当前 block 不可挖：

```text
dig_time_ms = null
```

不要返回负数。

---

# 十三、为什么现在只使用真实 Mineflayer 结果

不要自己实现：

```text
stone → pickaxe
iron_ore → iron_pickaxe
obsidian → diamond_pickaxe
```

这种硬编码规则。

因为：

- Minecraft 版本变化
- 模组方块
- 自定义服务器
- 工具状态
- 玩家模式
- 附魔
- 特殊工具

都会让简单规则失真。

当前版本已有 Mineflayer 的真实运行时能力，优先把它作为事实来源。

---

# 十四、输出结构

建议：

```json
{
  "ok": true,
  "position": {
    "x": 100,
    "y": 64,
    "z": 100
  },
  "block": {
    "name": "minecraft:iron_ore"
  },
  "held_item": {
    "name": "minecraft:stone_pickaxe",
    "count": 1
  },
  "distance": {
    "goal_near": 1,
    "raw": 4.28
  },
  "can_dig": true,
  "dig_time_ms": 1250,
  "reason": null
}
```

具体字段名遵循当前项目 semantic schema 风格。

---

# 十五、不要把 GoalNear 距离和 raw 距离混为一个字段

Phase 4H.1 已经建立：

```text
distance_to_target
raw_distance_to_target
```

因此本工具也必须明确区分：

```text
goal_near_distance
raw_distance
```

或者当前项目更合适的名称。

原因：

> `GoalNear` 判定与实际三维距离不是同一语义。

不要又引入一个含糊的：

```text
distance
```

然后让 LLM 猜。

---

# 十六、Reason

建议只使用有限枚举：

```text
null
air
too_far
unavailable
not_diggable
```

如果：

```text
bot.canDigBlock(block) === false
```

不要推测到底是：

```text
“工具等级不够”
```

除非 Mineflayer 有可靠依据。

第一版统一：

```text
reason = "not_diggable"
```

---

# 十七、Capability 不负责“推荐工具”

不要返回：

```json
{
  "recommended_tool": "minecraft:iron_pickaxe"
}
```

也不要：

```json
{
  "best_tool": ...
}
```

因为这会把：

```text
runtime capability
```

与：

```text
tool optimization knowledge
```

混在一起。

本阶段只回答：

> **当前这样挖，现实中是否可行，以及需要多久。**

---

# 十八、Capability 不调用 minecraft_equip

即使：

```text
held_item = dirt
can_dig = false
inventory 里有 stone_pickaxe
```

也必须：

```text
can_dig = false
```

然后让 LLM 自己决定是否：

```text
minecraft_equip
```

---

# 十九、ActionRuntime

这里不要新建 Action。

因为它是纯读。

不需要：

```text
runtime action = dig_capability
```

可以直接：

```text
Service read-only
```

但仍然遵循：

```text
Tool
 ↓
Agent Bridge
 ↓
Policy
 ↓
MinecraftService
```

不能：

```text
Tool → Mineflayer
```

---

# 二十、Policy

```text
risk = SAFE
online = required
explicit intent = not required
confirmation = not required
trusted player = not required
exclusive = false
```

行为应该与：

```text
minecraft_inventory
minecraft_world
minecraft_recipe_lookup
minecraft_dropped_items
```

一致。

---

# 二十一、与 minecraft_dig 的关系

最终推荐调用关系：

```text
minecraft_world
      ↓
minecraft_dig_capability
      ↓
if can_dig:
      minecraft_dig
else:
      minecraft_inventory
      ↓
      minecraft_equip
      ↓
      minecraft_dig
```

注意：

> 上述是 LLM/Agent 的决策链，不是 capability 工具内部自动执行。

---

# 二十二、Python Tests

新增：

```text
tests/test_minecraft_dig_capability_tool.py
```

至少覆盖：

A. schema

B. offline

C. invalid coords

D. block unavailable

E. air

F. too far

G. can_dig true

H. can_dig false

I. held item present

J. held item null

K. dig_time present

L. raw Item 不泄露

M. SAFE no confirmation

N. non-exclusive

O. move_to 正在执行时仍能查询

---

# 二十三、Node Tests

新增：

```text
minecraft_runtime/test/dig_capability.test.js
```

至少：

A. block lookup

B. held item projection

C. canDigBlock true

D. canDigBlock false

E. digTime

F. air

G. null block

H. too far

I. no side effects

J. does not equip

K. does not move

L. concurrent read while foreground action exists

---

# 二十四、Real Server Smoke

加入：

```text
DIG CAPABILITY
```

必须使用真实服务器。

建议测试三个状态：

### 状态 1：空手 + stone

```text
held_item = null
block = stone
```

得到真实：

```text
can_dig
dig_time_ms
```

### 状态 2：拿 stone_pickaxe + stone

```text
equip
→ capability
```

应该看到：

```text
held_item = stone_pickaxe
can_dig = true
dig_time_ms
```

### 状态 3：拿不合适物品

例如：

```text
held_item = dirt
block = stone
```

查看真实 Mineflayer 返回：

```text
can_dig
dig_time_ms
```

不要人为规定结果，只记录真实服务器事实。

---

# 二十五、不要实际挖方块

Phase 4J 本身：

```text
minecraft_dig_capability
```

不能产生世界修改。

Smoke 为了验证 capability：

可以：

```text
/equip
```

但不要真正：

```text
minecraft_dig
```

除非作为独立已有动作的回归验证。

最好：

```text
inventory fixture
→ equip
→ capability
→ restore
```

---

# 二十六、Real Server 硬门禁

至少：

```text
✓ capability empty-hand = PASS
✓ capability tool-held = PASS
✓ capability mismatch-tool state = PASS
✓ real can_dig matches runtime
✓ real dig_time_ms is finite/valid when diggable
✓ no world modification
✓ inventory restored
✓ ensureIdle
```

如果 capability 只能 fake bot：

```text
Phase 4J = BLOCKED
```

---

# 二十七、不要增加配置

Phase 4J：

**不增加用户配置。**

不要：

```text
tool_capability_timeout
recommendation_strategy
tool_priority
```

这是即时只读查询。

---

# 二十八、WebUI

Minecraft 页面增加：

## Dig Capability

输入：

```text
x
y
z
```

按钮：

```text
CHECK
```

显示：

```text
Block
Held Item
GoalNear Distance
Raw Distance
Can Dig
Dig Time
Reason
```

不提供：

```text
AUTO EQUIP
AUTO DIG
```

---

# 二十九、文档

新增：

```text
docs/MINECRAFT_PHASE4J.md
```

同步：

```text
config/config.example.yaml
app/web/config_registry.py
webui/src/pages/Minecraft.vue
docs/README.md
CHANGELOG.md
```

工具数量增加 1。

明确：

```text
minecraft_dig_capability = SAFE
```

并写清：

> 只回答当前状态下能否挖、预计多久，不会自动换工具、导航或挖掘。

---

# 三十、不要做 Tool Capability Database

本阶段不要创建：

```text
tool_capability.json
blocks.json
tool_tiers.yaml
```

也不要把 Minecraft Wiki 大量数据复制进仓库。

Mineflayer 当前 runtime 已提供：

```text
canDigBlock
digTime
```

优先使用真实运行时事实。([GitHub](https://github.com/PrismarineJS/mineflayer/blob/master/docs/api.md))

---

# 三十一、后续才做 Resource Gathering

4J 完成后不要立即让 capability 工具自己执行任务。

下一阶段：

```text
4K = Resource Gathering
```

才研究：

```text
寻找目标方块
→ move_to
→ equip
→ dig
→ dropped_items
→ pickup
→ inventory verification
```

但即使 4K，也不要做成一个黑盒 `collect_resource`。

应该是：

```text
LLM / Task Runtime
```

编排：

```text
world
→ find
→ capability
→ equip
→ move_to
→ dig
→ dropped_items
→ pickup
```

每一步保持现有 confirmation/risk 边界。

---

# 三十二、不要在 4J 修改 minecraft_dig

除非发现：

```text
canDigBlock / digTime
```

与当前 4I 真实 runtime 存在明确冲突。

正常情况下：

```text
minecraft_dig
```

保持不动。

---

# 三十三、验收门禁

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

以及：

```text
REAL SERVER: PASS
```

必须真实证明：

```text
empty hand → capability
tool held → capability
mismatch tool → capability
dig_time
no side effect
inventory restored
```

---

# 三十四、最终报告格式

```text
Phase 4J = PASS / BLOCKED

commit:
<sha>

新增工具:
minecraft_dig_capability

risk:
dig_capability = SAFE

自动化:
pytest = ...
Node = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
empty hand = ...
tool held = ...
mismatch tool = ...
can_dig = ...
dig_time_ms = ...
world unchanged = ...
inventory restored = ...
ensureIdle = ...

REAL SERVER: PASS / BLOCKED
```

没有真实跑的：

```text
SKIPPED
```

不要伪造 PASS。