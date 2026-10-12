# CatooBot Minecraft Phase 4G — Crafting Table：3×3 合成（明确指定工作台）

> Phase 4F（玩家 2×2 合成）的**上下文扩展**：同样是 `minecraft_recipe_lookup` 与
> `minecraft_craft` 两个工具，多了一个**可选**的 `crafting_table` 坐标参数。
>
> 原则：**2×2 = 玩家自身背包；3×3 = 必须给出明确的工作台坐标**。
> 不自动导航、不自动寻找/放置工作台、不自动准备材料、不做 recipe chain。

## 一、两个上下文

```text
不给 crafting_table              → 玩家自身 2×2（Phase 4F 行为，完全兼容）
给 crafting_table = {x, y, z}     → 那一张工作台的 3×3
```

* 没给 `crafting_table` 时，`chest` 只回报 `crafting_table_required`；
  给了一张**有效**工作台 + 材料够，同一个 `chest` 才变成 `available`。
  这是必须成立的架构回归（2×2 与 3×3 是两个不同的世界操作上下文）。
* **绝不接受隐式目标**：`"nearest"` / `"auto"` / `"any"` 一律 422 拒绝 ——
  用户确认的必须是一个明确的世界交互对象，而不是执行时才决定的"最近的那个"。

## 二、recipe_lookup（SAFE，非独占；`crafting_table` 可选）

```json
{"item": "chest", "crafting_table": {"x": 100, "y": 64, "z": 100}}
```

* 给了坐标 → 先**实时**验证方块，再 `bot.recipesFor(itemType, null, 1, tableBlock)`；
* 输出多一个 `crafting_table` 字段（坐标原样带回，模型下一轮 craft 要用同一组坐标）：

```json
{"ok": true, "item": "chest", "crafting_table": {"x": 100, "y": 64, "z": 100},
 "status": "available", "total": 1,
 "recipes": [{"recipe_id": "!chest*1=oak_planks*8",
              "result": {"name": "chest", "count_per_craft": 1},
              "requires_table": true, "available": true,
              "ingredients": [{"name": "oak_planks", "count": 8}]}]}
```

* 状态语义不变（`available` / `insufficient_material` / `crafting_table_required` /
  `recipe_not_found`）；给了有效工作台时不会再出现 `crafting_table_required`。
* 摘要会写明是在哪张工作台上查的：`chest 在工作台 (100, 64, 100) 上现有材料能做：…`。

## 三、工作台的实时验证（§七/§九/§十八）

| 情况 | 错误码 | HTTP |
| --- | --- | --- |
| 那个位置没有方块 / 已被挖掉（air） | `minecraft.crafting_table_missing` | 404 |
| 那个位置不是工作台（箱子、熔炉、切石机…） | `minecraft.crafting_table_invalid` | 422 |
| 距离超过上限（默认 5 格，眼睛 → 方块中心） | `minecraft.crafting_table_too_far` | 422 |

* 只认 `minecraft:crafting_table` 一种：stonecutter / smithing_table / cartography_table /
  loom / fletching_table 一律 `invalid`（§八）；
* 坐标非法（非整数 / 越界 / 不是 `{x,y,z}`）→ `minecraft.action_invalid`（422），
  在**进确认门之前**就被 schema 与 Service 拦掉；
* **lookup 与 craft 各自验证一次**：确认后如果工作台被挖掉/被换掉，craft 直接
  `crafting_table_missing` / `crafting_table_invalid`，绝不继续（§十八）；
* **绝不自动导航**：太远就是太远（`too_far`），不会自己走过去，也不会 /place 一张工作台。

## 四、craft（MEDIUM，exclusive，detached；`crafting_table` 可选）

```json
{"recipe_id": "!chest*1=oak_planks*8", "crafting_table": {"x": 100, "y": 64, "z": 100}}
```

* 执行：`bot.craft(recipe, 1, tableBlock)`；**没有工作台坐标就是 `null`**（玩家 2×2）；
* 仍然只执行**一次**（count 固定 1），没有 `count` / `times` / `batch_size`；
* 执行后**重读整个 inventory**的复核规则与 4F 完全一致：
  `产物增加 ≥ 每刀产出` **且** `每种材料消耗 ≥ 配方所需`，否则 `craft_unconfirmed`；
  用了工作台就如实把坐标带回结果（`crafting_table`），2×2 时是 `null`。

## 五、确认门（§十五/§十六）

* fingerprint：没有工作台时只绑 `tool + recipe_id`（4F 不变）；给了工作台就**额外绑定**
  `crafting_table.x/y/z` —— 同一个配方在两个不同工作台上执行是两次不同的世界操作；
* 坐标变了 → 旧确认作废 + 按新坐标重新 PENDING（`confirmation_mismatch`）；
* 摘要（§十六/§三十九）：

```text
2×2：用 2 个 oak_planks 制作 4 个 stick
3×3：用 8 个 oak_planks 在 (100, 64, 100) 的 Crafting Table 制作 1 个 chest
```

* TurnOrigin / 可信玩家 / 一次性 / TTL / 开发者入口不得自授权（
  `minecraft.confirmation_not_user_turn`）全部不变。

## 六、`recipe_id` 规则不变（§十二）

仍然是 4F 的可读规范签名（`[!]<结果名>*<每刀产出>=<材料名>*<数量>…`）：
`requires_table` 已经体现在 `!` 前缀里，所以 2×2 配方与 3×3 配方**不会互相碰撞**，
也不需要临时拼接 `crafting_table=true/false`。形状不同的两条同名单配方才用
6 位形状摘要后缀区分（4F 已有测试）。

## 七、错误码总表（4G 新增三个）

| 错误码 | HTTP | 何时 |
| --- | --- | --- |
| `minecraft.crafting_table_missing` | 404 | 那里没有工作台（没加载 / 被挖掉） |
| `minecraft.crafting_table_invalid` | 422 | 那里不是 crafting_table（detail 带实际方块名） |
| `minecraft.crafting_table_too_far` | 422 | 超过 `craft.crafting_table.max_distance` |
| 4F 的六个 | — | `recipe_not_found` / `recipe_changed` / `recipe_unavailable` / `material_insufficient` / `craft_failed` / `craft_unconfirmed` |

`minecraft_inventory` 的输出**没有**任何变化（仍然只有五项：`online` /
`selected_hotbar_slot` / `held_item` / `items`）—— 不把"最近的工作台"这种东西塞进去
污染只读工具（§二十二）。

## 八、WorldPerception（§二十三）

工作台本身没有发生变化，合成只改 inventory → **不假装** `world.changed`；
硬事实来源仍然是"重新读取的 inventory"。

## 九、配置

```yaml
minecraft:
  action:
    craft:
      timeout: 30                          # 单次合成（2×2 或 3×3）
      crafting_table:
        max_distance: 5                    # 指定工作台时的最大交互距离（格）
```

只加了这一个键（不拆 `table_timeout` / `lookup_distance` 那一堆）。
`allow_medium` 默认仍是 **false**（六个 MEDIUM 动作）。

## 十、验证

* **Node 单测**（真实 1.21.1 配方表 + 假 inventory + 假工作台 block）：
  * `recipe.test.js` → **78 checks**（4F 的 A–E + 4G：A table-aware lookup / B table not found /
    C invalid table / D too far / E 3×3 配方被选中 / F 2×2 配方带工作台仍可用 +
    §三十七 2×2↔3×3 架构回归 + `nearest/auto` 参数校验 + real block 的 name/position 传递）；
  * `craft.test.js` → **83 checks**（4F 的 F–P + 4G：G `craft(recipe,1,table)` /
    H 事后重读复核 / I 带工作台的 recipe changed / §十八 确认后工作台被挖掉·被换掉·太远 /
    2×2 配方带工作台 / J–M 3×3 的取消·超时·竞态·终态恰好一次）。
* **Python**：`tests/test_minecraft_recipe_tool.py`（4G +6）与
  `tests/test_minecraft_craft_tool.py`（4G +6）+ Web API（+3：坐标参数、3×3 确认摘要、
  错误码映射一致）；`MAX_DATA_DEPTH = 6` 的边界回归测试保持不变（§四十二：
  新增的 `crafting_table` 只在第 3 层，没有再加深度）。
* **flying-squid E2E**：observer 用 `/setblock` 放一张**真实工作台** →
  `recipe_lookup(chest, table)` 真的跑通（`requires_table: true` + 材料语义 + 坐标回传）；
  另验证 `nearest` 被拒、`table.missing` / `table.invalid` / `table.too_far`、
  3×3 craft 的材料不够 / 工作台不在 / 忙时 `action.busy`；
  3×3 成功路径明确 **SKIPPED**（假服务器没有 /give）。
* **真实服务器 smoke**（§三十二-§三十六）：`/give 8 木板` + 就地放临时工作台
  （记录原方块）→ `recipe_lookup(chest, table)` 动态拿到 `!chest*1=oak_planks*8` →
  `craft`（RUNNING → completed，带工作台坐标）→ 重读 inventory（chest 0→1、木板 −8）→
  `/clear` 产物与材料 → 还原工作台 → 逐槽签名比对 → ensureIdle → `REAL SERVER: PASS`。
  同一轮里 2×2（4F：木板 → 木棍）与 3×3（4G：木板 → 箱子）都真机通过。

## 十一、本阶段不做（4H 及以后）

自动寻找 / 走到 / 放置工作台、熔炉与高炉冶炼、烟熏炉、锻造台、切石机、织布机、制图台、
酿造、附魔、村民交易、容器与背包自动补料、recipe chain、批量合成。
