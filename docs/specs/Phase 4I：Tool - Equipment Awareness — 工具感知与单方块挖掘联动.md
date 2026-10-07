基于当前仓库最终基线 `bd52f18` 开始 Phase 4I。

先完整阅读：

- Phase 4B `minecraft_dig`
- Phase 4D `minecraft_equip`
- Phase 4D `minecraft_inventory`
- Phase 4H.1 `move_to`
- `ActionRuntime`
- `MinecraftAgentPolicy`
- `ConfirmationStore`
- `TurnOrigin`
- `MinecraftService`
- `runtime.js`
- 当前 dig/equip/inventory Node tests
- 当前 `smoke_real_server.js`
- 当前 Minecraft WebUI

不要根据历史报告猜接口。

---

# 一、Phase 4I 目标

本阶段不追求“自动挖矿”。

目标只有：

> **让单方块 dig 第一次具备明确的 Tool Awareness。**

最终行为：

```text id="2m0n7d"
minecraft_inventory
        ↓
看到 held_item
        ↓
用户明确要求
“用我的石镐挖这个铁矿”
        ↓
minecraft_equip("minecraft:stone_pickaxe")   ← 用户单独确认/执行
        ↓
minecraft_dig(
    x,
    y,
    z,
    expected_block,
    expected_tool = "minecraft:stone_pickaxe"
)
        ↓
实时验证当前主手
        ↓
实时验证目标方块
        ↓
bot.canDigBlock(...)
        ↓
单方块挖掘
        ↓
真实世界验证
        ↓
WorldPerception / block change
```

---

# 二、绝对不要做自动换工具

本阶段明确禁止：

```text id="5ivjxe"
自动选择最佳工具
自动从背包找工具
自动 equip
自动切 hotbar
自动升级工具
自动制作工具
自动维修工具
自动补充工具
自动从箱子取工具
```

尤其禁止：

```text id="mmj78u"
minecraft_dig
    ↓
内部偷偷调用 minecraft_equip
```

原因：

当前 `minecraft_equip` 本身是独立的 MEDIUM action，并有独立 confirmation。

不能绕过它。

---

# 三、为什么不是新增 Tool Selection 工具

不要新增：

```text id="6k28xd"
minecraft_select_best_tool
minecraft_choose_tool
minecraft_prepare_tool
```

现有：

```text id="f1ad6a"
minecraft_inventory
minecraft_equip
```

已经足够。

4I 只扩展：

```text id="7m92e6"
minecraft_dig
```

使它可以要求：

```text id="k2jss1"
expected_tool
```

---

# 四、minecraft_dig Schema 扩展

当前已有：

```json id="7w2h4n"
{
  "x": 100,
  "y": 64,
  "z": 100,
  "expected_block": "minecraft:stone"
}
```

扩展为：

```json id="xh6q4r"
{
  "x": 100,
  "y": 64,
  "z": 100,
  "expected_block": "minecraft:stone",
  "expected_tool": "minecraft:stone_pickaxe"
}
```

其中：

```text id="7mcc5w"
expected_tool = OPTIONAL
```

这是为了完整保持 Phase 4B 向后兼容。

---

# 五、两种语义必须严格区分

## A. 未指定 expected_tool

```text id="l9kzxt"
expected_tool omitted
```

保持 Phase 4B 原行为。

含义：

> 只要求目标方块匹配，不对当前手持工具增加额外硬约束。

---

## B. 指定 expected_tool

例如：

```text id="3g5tqv"
expected_tool = minecraft:stone_pickaxe
```

含义：

> 当前执行瞬间，主手必须确实拿着这把物品。

不是：

> “你帮我找到一把石镐”。

绝对不要自动 equip。

---

# 六、expected_tool canonicalization

与 `minecraft_equip.expected_item` 一致：

```text id="7c35u3"
stone_pickaxe
==
minecraft:stone_pickaxe
```

内部统一 canonical item name：

```text id="7gjl5m"
minecraft:stone_pickaxe
```

结果里仍报告真实 Mineflayer item name。

---

# 七、Confirmation Fingerprint 必须升级

这是本阶段必须重点处理的地方。

Phase 4B 原来：

```text id="n8d3sp"
tool
x
y
z
expected_block
```

4I 如果传：

```text id="fk60y8"
expected_tool
```

必须加入 fingerprint：

```text id="l3jm6q"
tool
x
y
z
expected_block
expected_tool
```

如果：

```text id="2r1yby"
同一个方块
```

从：

```text
expected_tool = null
```

改成：

```text
minecraft:stone_pickaxe
```

必须：

```text id="m6x7qk"
confirmation mismatch
→ 旧 confirmation 作废
→ 新建 PENDING
```

反之亦然。

不能让旧的无工具确认授权一个“要求石镐”的操作。

---

# 八、Confirmation 摘要

没有工具：

```text id="c71j2m"
挖掘 minecraft:stone
位置 (100,64,100)
```

指定工具：

```text id="4tczjb"
使用 minecraft:stone_pickaxe 挖掘 minecraft:iron_ore
位置 (100,64,100)
```

必须把：

```text id="8chqd7"
block
position
tool
```

都讲清楚。

不要只显示：

```text id="zq36k1"
执行 minecraft_dig
```

---

# 九、Action Runtime 不增加新的 Action

仍然使用：

```text id="zgxns0"
dig
```

不要新增：

```text id="2sn9kz"
dig_with_tool
```

保持：

```text id="f8jzwj"
minecraft_dig → ActionRuntime action = dig
```

这样：

- 4B 行为保持
- 4I 只是增强校验
- Action Event schema 不需要产生第二套

---

# 十、Tool 校验必须发生在 Runtime start

执行前实时：

```text id="6nj1ap"
held = bot.heldItem
```

如果：

```text id="xv13c5"
expected_tool != null
```

要求：

```text id="ut9o98"
held != null
held.name == expected_tool
held.count > 0
```

否则：

```text id="71ctwp"
minecraft.held_item_missing
```

或：

```text id="50bg69"
minecraft.held_item_changed
```

具体错误码遵循当前项目风格。

---

# 十一、绝对禁止使用缓存

不要：

```text id="a2ng0p"
minecraft_inventory
→ held_item = stone_pickaxe
→ 3 秒后 dig
→ 假设还是 stone_pickaxe
```

必须：

```text id="2s7f53"
dig start
→ 重新读取 bot.heldItem
```

因为用户可能在确认期间：

- 换工具
- 移动物品
- 被其他动作改变手持状态

---

# 十二、工具错误分类

建议：

### 没有 expected_tool

继续旧行为。

### 有 expected_tool + 空手

```text id="2zj4h2"
minecraft.held_item_missing
```

### 有 expected_tool + 当前物品不同

```text id="om0m6j"
minecraft.held_item_changed
```

detail 至少：

```json id="au14l0"
{
  "expected": "minecraft:stone_pickaxe",
  "actual": "minecraft:iron_pickaxe"
}
```

---

# 十三、当前工具不一定“适合”目标

注意：

本阶段第一版**不要自行推导完整的 Minecraft tool tier 规则**。

例如：

```text id="jtsm7p"
diamond_pickaxe
stone_pickaxe
iron_pickaxe
golden_pickaxe
```

不需要在 CatooBot 内维护一张“最佳工具表”。

本阶段只有：

```text id="4dt8dg"
expected_tool
```

的身份约束。

真正能不能破坏：

仍然交给：

```text id="c9kgz5"
bot.canDigBlock(block)
```

---

# 十四、canDigBlock 成为最终“能不能挖”的硬门

执行前：

```text id="i3x85y"
if (!bot.canDigBlock(block))
```

返回：

```text id="f7q3bs"
minecraft.block_undiggable
```

不要：

```text id="x8h3pq"
自动换工具
```

---

# 十五、工具不能让 dig 绕过既有风险

`minecraft_dig` 仍然：

```text id="qfgm88"
MEDIUM
exclusive
detached
confirmation
```

不要：

```text id="9dgbzn"
因为“工具已经确认过”
→ 把 dig 降成 LOW
```

Dig 本身仍然是真实世界修改。

---

# 十六、Dig 与 Equip 必须是两个动作

正确：

```text id="c80vzy"
USER:
“先把石镐拿到手里”
↓
minecraft_equip
↓
confirmation
↓
completed

USER:
“用石镐挖铁矿”
↓
minecraft_dig(expected_tool=stone_pickaxe)
↓
confirmation
↓
实时检查 heldItem
↓
dig
```

不要：

```text id="s3q0ja"
一个 minecraft_dig 自动完成 equip + dig
```

---

# 十七、一个自然语言 Turn 可以调用两个工具，但 ActionRuntime 不能嵌套

LLM Tool Loop 可以：

```text id="4r3k9r"
minecraft_inventory
→ minecraft_equip
→ minecraft_inventory
→ minecraft_dig
```

但每个 action：

```text id="1w1vtd"
独立
```

不能：

```text id="t5m43c"
dig action
→ invoke equip action
```

---

# 十八、Dig 成功 Result 扩展

当前成功已有：

```json id="x4n4ve"
{
  "position": {...},
  "block_before": "...",
  "block_after": "..."
}
```

如果 expected_tool 被提供：

增加：

```json id="6qj9xz"
{
  "tool_expected": "minecraft:stone_pickaxe",
  "tool_actual": "minecraft:stone_pickaxe"
}
```

如果未提供：

```text id="4t6j7w"
tool_expected = null
```

可以不返回，具体以当前 result schema 风格决定。

不要返回 raw Item object。

---

# 十九、工具状态在动作结束后也要记录

建议读取：

```text id="m7s5k6"
heldItem after
```

用于结果：

```json id="34x7rv"
{
  "tool_actual_after": {
    "name": "minecraft:stone_pickaxe",
    "count": 1
  }
}
```

但：

> 不要把工具数量变化作为 dig 成功硬门。

硬门仍然：

```text id="d1a4ss"
block_after != block_before
```

---

# 二十、不要把耐久度写死

如果 Mineflayer 当前 Item 暴露 durability / NBT / components：

可以做内部观察。

但第一版：

```text id="w7k3lx"
不要把 durability 作为成功条件
```

也不要：
- 自动修理
- 自动换下一把工具
- 自动判断“马上坏了”
- 自动补工具

这些以后单独做。

---

# 二十一、Tool Read-only 信息

`minecraft_inventory` 当前只返回：

```text id="t4v4o6"
online
selected_hotbar_slot
held_item
items
```

不要把 raw durability/NBT 加进去。

如果当前 WebUI 需要显示工具信息：

可以从：

```text id="3f2k89"
held_item
```

扩展一个**最小 semantic 字段**，但只有当当前 Mineflayer data 能稳定跨版本提供时才做。

否则不加。

---

# 二十二、Tool-aware dig 与 place

本阶段不要修改 `minecraft_place` schema。

但是检查：

```text id="2gd1y9"
minecraft_place
```

不要意外受到 expected_tool 逻辑影响。

Tool awareness 第一版只进入：

```text id="2vbzkg"
minecraft_dig
```

---

# 二十三、Tool-aware Pickup

不要修改：

```text id="1qrqmw"
minecraft_pickup_item
```

Item Pickup 不需要工具。

---

# 二十四、Tool-aware Craft

不要修改：

```text id="y0m3cr"
minecraft_craft
```

Craft 暂时只负责配方。

---

# 二十五、Python Schema Tests

扩展：

```text id="a2oxz1"
tests/test_minecraft_dig_tool.py
```

至少覆盖：

A. 旧 schema 无 expected_tool → 完全兼容

B. expected_tool 合法

C. expected_tool canonicalization

D. expected_tool missing → reject

E. held_item mismatch → reject

F. held_item missing → reject

G. confirmation fingerprint 包含 tool

H. tool 改变 → confirmation mismatch

I. expected_tool 删除 → confirmation mismatch

J. Service/runtime error mapping

---

# 二十六、Node Runtime Tests

扩展：

```text id="5d5v36"
minecraft_runtime/test/dig.test.js
```

新增：

K. expected_tool absent → old behavior

L. expected_tool matches

M. expected_tool mismatch

N. held item null

O. held item changes after start validation

P. canDigBlock false

Q. success result includes tool identity

R. tool does not auto-equip

S. ActionRuntime terminal exactly once

T. cancellation cleanup unchanged

---

# 二十七、关键安全测试

必须证明：

```text id="v5z8mc"
minecraft_dig(
    expected_tool = stone_pickaxe
)
```

即使 inventory 中存在 stone_pickaxe：

也不能因为：

```text id="77ejx1"
“背包里有”
```

就自动 equip。

必须：

```text id="9j6v3e"
当前 heldItem 必须已经是 stone_pickaxe
```

---

# 二十八、Real Server Smoke

升级：

```text id="f0p7yp"
smoke_real_server.js
```

增加一个明确的 Tool-aware Dig 段。

建议测试：

```text id="q5vq40"
inventory before
↓
确保某个 tool 在 inventory 中
↓
minecraft_equip
↓
确认 held_item
↓
找到一个适合裸手/工具测试的 block
↓
minecraft_dig(expected_tool=指定工具)
↓
RUNNING
↓
completed
↓
真实世界 block changed
↓
inventory / perception 验证
```

---

# 二十九、不要在 Smoke 中隐式调用 Tool Action

Smoke 必须明确：

```text id="m9d4fc"
EQUIP
→ completed
→ re-read
→ DIG(expected_tool)
```

不能：

```text id="ro8k1m"
DIG
→ 如果没拿工具就帮它 equip
```

这样才能真正测试 4I 的 contract。

---

# 三十、Real Server 正向测试

优先：

```text id="v5q9a6"
minecraft:iron_pickaxe
```

或：

```text id="3y5vmd"
minecraft:stone_pickaxe
```

具体选择取决于真实服务器现有物品。

不要硬编码“必须有某个工具”。

可以支持：

```text id="26i4oa"
SMOKE_TOOL_ITEM="minecraft:stone_pickaxe"
```

如果没有：

```text id="5y5r2h"
SKIPPED
```

但最好 Smoke 能通过前面阶段已经建立的 fixture 机制 `/give` 获取。

不能依赖假设。

---

# 三十一、Real Server False-tool Test

必须新增：

```text id="j8a7ip"
expected_tool mismatch
```

例如：

```text id="gk53re"
手里是 dirt
expected_tool = stone_pickaxe
```

要求：

```text id="1q6d7n"
拒绝
block 不变
没有启动新的 dig Action
```

这条是 4I 的关键安全门。

---

# 三十二、Real Server 真正 Tool Dig

必须：

```text id="kz57ec"
held_item = expected_tool
```

然后：

```text id="7c3kry"
minecraft_dig
```

至少证明：

```text id="n3xvq2"
action RUNNING
action completed
block before
block after
real snapshot changed
WorldPerception changed
```

与 4B 一样。

---

# 三十三、不要引入新的 Tool Tier 规则

禁止现在做：

```text id="j3zc66"
“铁矿必须铁镐/钻石镐”
“石头最好石镐”
“木镐效率排序”
```

这些以后可以来自：

```text id="zv1npp"
Minecraft Data / block tags / tool tags
```

独立做 Tool Capability Knowledge。

Phase 4I 当前只有：

```text id="ewvt27"
expected_tool identity check
```

---

# 三十四、Activity

成功时：

```text id="pf91tw"
用 minecraft:stone_pickaxe 挖掉了 minecraft:iron_ore
```

未指定工具：

```text id="70kkap"
挖掉了 minecraft:stone
```

只陈述实际事实。

---

# 三十五、WebUI

Minecraft → Dig Test 增加：

```text id="5a6ns0"
Expected Tool
```

显示：

```text id="npb9t6"
当前主手
```

测试按钮：

```text id="tdqg4f"
DIG
```

但不能：

```text id="m8h0zl"
自动 equip
```

如果当前 Expected Tool 与手持不一致：

明确显示：

```text id="4czc2f"
Held item mismatch
```

---

# 三十六、文档

新增：

```text id="e6meo3"
docs/MINECRAFT_PHASE4I.md
```

同步：

```text id="d6bsm1"
config/config.example.yaml
app/web/config_registry.py
webui/src/pages/Minecraft.vue
docs/README.md
CHANGELOG.md
```

说明：

```text id="f4b1q7"
minecraft_equip = MEDIUM
minecraft_dig = MEDIUM
```

以及：

> `expected_tool` 只验证当前主手，不会自动装备。

---

# 三十七、配置

本阶段原则上：

**不增加新用户配置。**

继续使用：

```text id="7lx9jp"
allow_medium
dig.timeout
dig.max_distance
```

不要创建：

```text id="q36y6c"
tool_selection
auto_equip
preferred_tool
tool_tier
```

---

# 三十八、ActionRuntime

不要修改：

```text id="zjv42t"
ActionRuntime cancellation contract
```

不要给 dig 创建第二个 action。

只扩展：

```text id="y1u84x"
dig start validation
```

---

# 三十九、与 Phase 4H.1 的关系

必须保证：

```text id="a9kzx5"
move_to
```

当前已经不再使用：

```text id="f8v2x7"
pathfinder.goto()
```

4I 不得重新引入 goto。

Tool-aware dig 不得调用：

```text id="p0rm5e"
move_to
```

也不得自动导航。

---

# 四十、验收门禁

必须：

```text id="m3i68h"
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

```text id="t6zlcq"
REAL SERVER: PASS
```

至少：

```text id="d5w2v5"
backward-compatible dig = PASS
expected_tool match = PASS
expected_tool mismatch = PASS
no auto-equip = PASS
real tool-aware dig = PASS
block changed = PASS
inventory/perception = PASS
ensureIdle = PASS
```

---

# 四十一、Real STOP

Phase 4I 不要求重新建立一个新的 STOP 硬门禁。

因为 dig 已经在 4B 得到真实服务器的 ActionRuntime 验证。

本阶段只需证明：

```text id="5w6gfs"
expected_tool
```

不会破坏现有：

```text id="xkby4c"
STOP / TIMEOUT / cleanup / terminal race
```

已有 dig 单测继续全部通过即可。

---

# 四十二、最终报告格式

```text id="6z9i3k"
Phase 4I = PASS / BLOCKED

commit:
<sha>

变更:
minecraft_dig + optional expected_tool

risk:
minecraft_dig = MEDIUM

自动化:
pytest = ...
Node = ...
Vitest = ...
Playwright = ...
lint = ...
mypy = ...

Real Server:
legacy dig = PASS
tool match = PASS
tool mismatch = PASS
no auto-equip = PASS
tool-aware dig = PASS
world change = PASS
ensureIdle = PASS

REAL SERVER: PASS / BLOCKED
```

任何未真正验证的：

```text id="m5n1x8"
SKIPPED
```

不要伪造 PASS。

---

# 四十三、4I 的真正完成标准

完成后应该能够稳定表达：

```text id="o5om27"
“手里没有镐”
    ↓
minecraft_inventory

“把石镐拿到手里”
    ↓
minecraft_equip

“用石镐挖这个铁矿”
    ↓
minecraft_dig(
    expected_tool="minecraft:stone_pickaxe"
)

    ↓
实时验证：
heldItem == stone_pickaxe

    ↓
挖一个方块

    ↓
真实世界：
iron_ore → air
```

但如果：

```text id="x7b9f5d"
expected_tool = stone_pickaxe
heldItem = dirt
```

必须：

```text id="w7v6he"
拒绝
```

绝不能偷偷帮用户切换工具。

4I 做完后再考虑真正的：

```text id="o4ky2b"
Tool Capability Knowledge
```

即：

```text 哪种工具能挖什么
哪种工具更快
工具耐久
效率附魔
modded tool tags
```

那应该是后续独立阶段，而不是现在一起做。