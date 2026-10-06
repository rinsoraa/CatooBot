# CatooBot Minecraft Phase 4I — Tool Awareness（工具感知与单方块挖掘联动）

> 本阶段不追求"自动挖矿"，只做一件事：**让单方块 dig 第一次具备明确的工具身份约束**。
>
> `minecraft_dig` 多了一个**可选**的 `expected_tool`：给了就要求"执行瞬间主手确实拿着它"。
> 它**只校验，绝不自动装备** —— 换工具永远是独立的 MEDIUM 动作 `minecraft_equip`（有自己的确认）。

## 一、完整链路

```text
minecraft_inventory                → 看清手里的东西（held_item / items）
        ↓ 用户："把石镐拿到手里"
minecraft_equip("minecraft:stone_pickaxe")   ← 独立 MEDIUM 动作 + 独立确认
        ↓ 用户："用石镐挖这个铁矿"
minecraft_dig(x, y, z, expected_block, expected_tool="minecraft:stone_pickaxe")
        ↓ 确认门（指纹含 expected_tool）
        ↓ runtime start：实时读 bot.heldItem → 身份不符直接拒绝
        ↓ bot.canDigBlock(block)（最终"能不能挖"的硬门）
        ↓ 单方块挖掘
        ↓ 真实世界复核（block_after != block_before）
```

## 二、`expected_tool` 的语义（§四-§十二）

| 情况 | 行为 |
| --- | --- |
| **没给** `expected_tool` | **Phase 4B 原行为，一个字节都不变**：只要求目标方块匹配，不对手持工具加约束 |
| 给了 + 主手正是它 | 正常挖；结果里如实带工具身份 |
| 给了 + 主手是**别的物品** | 409 `minecraft.held_item_changed`（`detail.expected/actual`），**不挖** |
| 给了 + **空手** | 400 `minecraft.held_item_missing`，不挖 |

* **每次执行都重新读** `bot.heldItem`（§十一：绝不拿几秒前 `minecraft_inventory` 的结论）——
  用户可能在确认期间换了工具、移了物品、或被别的动作改了手持；
* **规范化**（§六）：`stone_pickaxe` == `minecraft:stone_pickaxe`（大小写也不敏感），
  在**进确认门之前**统一成 canonical item name，所以模型换个写法不会把用户已确认的授权变成 mismatch；
  结果里的 `tool_expected` / `tool_actual` 沿用 runtime 的语义投影口径（裸物品名，与
  `minecraft_inventory` / `block_before` 一致）；
* **不判断"哪种工具更合适"**（§十三/§三十三）：第一版**没有** tool tier / 效率 / 附魔知识，
  真正能不能破坏仍然交给 `bot.canDigBlock(block)`；挖不动 → `minecraft.block_not_diggable`
  （沿用 Phase 4B 就有的稳定码，不新造一个名字）。

## 三、确认门（§七/§八）

* fingerprint = `tool + x + y + z + expected_block + expected_tool`（**新增最后一项**）：
  同一个方块，从"不要工具"改成"要石镐"（或反过来、或换一把镐）都是**另一个动作** →
  旧确认作废 + 按新参数重新挂一条 PENDING；
* 摘要把**工具 / 方块 / 位置**三样都讲清楚：

```text
不给工具：挖掉 minecraft:stone（120, 64, -230）
给了工具：使用 minecraft:stone_pickaxe 挖掘 minecraft:iron_ore（120, 64, -230）
```

## 四、结果（§十八/§十九）

成功结果在原有三个字段之外多了工具身份（**都是语义快照，绝不出 raw Item object**）：

```json
{ "position": {"x":120,"y":64,"z":-230},
  "block_before": "iron_ore", "block_after": "air",
  "tool_expected": "stone_pickaxe",
  "tool_actual": "stone_pickaxe",
  "tool_actual_after": {"name": "stone_pickaxe", "count": 1} }
```

* 没要求工具时 `tool_expected` / `tool_actual` 是 `null`（4B 消费者不受影响）；
* `tool_actual_after` 是**动作结束后再读一次**主手（镐子挖坏了会如实变少）——
  但**它不是成功硬门**：硬门永远是 `block_after != block_before`（§十九）；
* 耐久度/NBT **不参与**成功判定，也不做自动修理/换下一把/补工具（§二十）。

## 五、Activity（§三十四）

```text
用了工具：刚用 minecraft:stone_pickaxe 挖掉了 minecraft:iron_ore
没用工具：刚挖掉了 minecraft:stone
```

只陈述事实，不写"高效地""轻松"这类评价。

## 六、边界（本阶段明确不做）

不自动选工具、不自动从背包找工具、不自动 equip、不自动切 hotbar、不自动升级/制作/维修/补工具、
不从箱子取工具、不做 `minecraft_select_best_tool` 之类的新工具（§二/§三）；
dig 内部**绝不调用** equip（§十六/§十七：一个 Tool Call = 一个动作，LLM 可以在同一回合依次
调用 inventory → equip → dig，但每个 action 都独立，ActionRuntime 不嵌套）；
`minecraft_place` / `minecraft_pickup_item` / `minecraft_craft` 一行没动（§二十二-§二十四）；
`minecraft_inventory` 仍然只有五项切片（不加 raw durability/NBT）；
**没有新增任何用户配置**（继续用 `allow_medium` / `dig.timeout` / `dig.max_distance`，§三十七）；
没有重新引入 `pathfinder.goto()`，工具感知的 dig 也不调用 move_to、不自动导航（§三十九）。

## 七、验证

* **Node 单测** `dig.test.js`：**59 → 95 checks**。Phase 4I 新增 K–T：

  | 用例 | 内容 |
  | --- | --- |
  | K | 缺省 / 显式 null / 空字符串 → `expected_tool = null`，4B 的假 bot（没有 heldItem）照样 start |
  | L/Q | 身份匹配 → 通过；结果带 `tool_expected` / `tool_actual` / `tool_actual_after`（无 raw item） |
  | M | 主手是别的物品 → `held.item_changed` + `detail.expected/actual`，不开始挖 |
  | N | 空手 → `held.item_missing`（detail 里 actual=null） |
  | O | start 之后主手变了：动作不改绑、不重新校验，只如实报告结束时的主手 |
  | P | `canDigBlock=false` 仍是最终硬门（身份对了也拒绝） |
  | **R** | **背包里有石镐也绝不自动装备**：没有任何 equip / setQuickBarSlot 调用 |
  | S/T | ActionRuntime：正常完成恰好一个终态（成功不触发 cleanup）；STOP → CANCELLED +
    cleanup 恰好一次 + 真的 `stopDigging` |

* **Python** `tests/test_minecraft_dig_tool.py`（新增，A–J）：schema 向后兼容、规范化
  （两种写法/大小写都是同一把工具、规范化发生在**确认门之前**）、非法值在挂确认之前被拒、
  `held.*` → 稳定错误码、指纹包含预期工具（换工具/删工具都 mismatch）、摘要含工具/方块/位置、
  以及 §二十七 的关键安全测试（只调 dig，永远不调 equip）。
* **WebUI**：Dig Test 面板新增 `Expected Tool` 输入 + `当前主手` 显示；
  不一致时明确显示 **Held item mismatch**（提示归提示，页面**不会**自己去 equip）；
  留空时不带 `expected_tool` 字段（4B 请求形状不变）。页面测试 +5（vitest 466）。
* **flying-squid E2E**：4B 的 dig 用例全部照旧通过（工具相关逻辑在 Node 单测里直测）。
* **真实服务器 smoke**（1.21.1 + Fabric，`REAL SERVER: PASS`）：

```text
✓ expected_tool mismatch = PASS（HTTP 409 / held.item_changed）
  （手里 dirt、要求 stone_pickaxe —— 正是任务书 §三十一 那条关键安全门）
✓ mismatch detail 带 expected/actual（{"expected":"stone_pickaxe","actual":"dirt"}）
✓ expected_tool mismatch 之后方块没有变化
✓ expected_tool mismatch 没有启动任何 dig Action（runtime 仍然 IDLE）
✓ no auto-equip = PASS（被拒之后主手一个物品都没变）
✓ equip 自己先完成（minecraft.action.completed）→ ✓ 重读主手 = minecraft:stone_pickaxe
✓ real tool-aware dig → RUNNING
✓ real tool-aware dig = PASS（stone → air）
✓ expected_tool match = PASS（tool_expected=stone_pickaxe / tool_actual=stone_pickaxe）
✓ 结果带动作后的主手快照（{"name":"stone_pickaxe","count":1}，无 raw item）
✓ block changed = PASS（该位置现在是 air）
✓ inventory/perception = PASS（感知连续 3 次一致 3/3；主手仍是 stone_pickaxe）
✓ backward-compatible dig = PASS（不带 expected_tool 照样挖，tool_expected=null）
✓ 4I inventory restored = PASS（原有物品没丢）
✓ ensureIdle = PASS（工具感知 dig 之后 runtime 回到 IDLE）
```

  smoke 这一段的顺序是**完全显式**的（§二十九）：给夹具 → 错误工具先被拒 → **自己** equip →
  重读主手 → `dig(expected_tool)` → 真实世界复核 → 不带工具再挖一次（向后兼容）→ 还原方块与背包。
  smoke 从不让 dig 帮它满足工具要求。

## 八、顺带修好的 smoke 环境问题（都如实打印，不是放宽）

1. **开局主手卫生**：Phase 4C 的 place 段用**主手物品**去放，而罐头的手持/背包是跨会话保留的 ——
   上一次可能把它留在"手持工具"（工具放不下去，真机上见过 `Server refused to place …`）。
   现在开局会检查主手是不是"看起来能放的方块"（正面名单 + 工具后缀双重判定），
   必要时换成背包里的可放物品，实在没有就 `/give` 一点沙当夹具，并明确打印这一步。
2. **place 的参考方块**必须是实心表面：原来取"最近的柱子"，可能取到水/树叶/草 → 服务器拒绝。
   现在只在实心名单里挑（水/植物不算）。
3. **4I 的夹具记账**：只清**这一段自己 `/give`** 出来的工具（本来就有的工具绝不清掉），
   并在结束时把主手**还原成进来时的样子**；挖 stone 掉落的 cobblestone 被顺手捡起时如实打印备注
   （vanilla 行为）、不当失败。
4. 临时方块的上/还原与感知层刷新改成**有界等待**（不再用固定 500ms 赌一次）。
5. 口径差异如实记录：`expected_block` 沿用 Phase 4B 的**原始方块名**（runtime 不做规范化，
   所以用 `minecraft_world` / 快照里的裸名）；`expected_tool` 则两层都会规范化（两种写法都行）。

## 九、本阶段不做（4J 及以后）

Tool Capability Knowledge（哪种工具能挖什么 / 更快 / 耐久 / 效率附魔 / modded tool tags）、
自动选/找/换/修/补工具、从容器取工具、多工具批处理、挖矿循环与自动寻矿。
