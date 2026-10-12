# CatooBot Minecraft Phase 4F — Crafting：单个配方（玩家 2×2 背包合成）

> Phase 4E（容器读写）之后的**合成**能力：`minecraft_recipe_lookup`（只读查配方，SAFE）与
> `minecraft_craft`（执行**一次**配方，MEDIUM）。
>
> 原则：**只支持玩家自身 2×2 背包合成；一次一个配方、一次只执行一次；材料不够就直接失败
> （绝不自动准备材料、绝不做中间材料）；配方必须先用 lookup 拿到明确的 recipe_id。**

## 一、闭环

```text
minecraft_inventory                        （看清背包里有什么）
        ↓
minecraft_recipe_lookup(item)              （拿到 recipe_id + 材料清单 + 现在够不够）
        ↓
用户明确要求（USER 回合 + 可信玩家 + allow_medium）
        ↓
minecraft_craft(recipe_id)                 （MEDIUM → 确认门 → 一次配方）
        ↓
MinecraftService → ActionRuntime（exclusive，detached）
        ↓
bot.craft(recipe, 1, null)                 （craftingTable = null：只用玩家自己的 2×2）
        ↓
重新读取真实 inventory → 产物增加 + 材料减少 → SUCCEEDED（事件带 result）
```

## 二、严格范围（其余一律拒绝）

| | |
| --- | --- |
| 支持 | **玩家自身 2×2 背包合成**（`craftingTable = null`），一次一个配方、一次执行一次 |
| 明确禁止 | 工作台（不找、不走过去、不放一个）、容器、熔炉/高炉/烟熏炉、锻造台、切石机、酿造、附魔、村民交易、recipe chain（不自动做中间材料）、自动补料/自动挖矿/自动开箱取料、自动 equip / 移动槽位、批量生产、一次多个不同配方 |

判据都在 runtime 里：只有 `requires_table == false` 的配方才会被列出来（真实配方表 + 真实背包）。

## 三、minecraft_recipe_lookup（SAFE 只读，非独占）

* 参数：`{item}`（整数无关；`stick` 与 `minecraft:stick` 都接受，运行时归一化）；
* 风险 SAFE、**非独占**（`NON_EXCLUSIVE_TOOLS` 多了一项）：可与导航/背包读并行，**但仍需在线**；
* 数据来源是 `bot.recipesFor(itemType, metadata, 1, null)`（按**当前真实背包**判定能不能做）
  与配方表的全集对比 → 如实区分四种状态：

```json
{"ok": true, "item": "stick", "status": "available", "total": 12,
 "recipes": [{"recipe_id": "stick*4=oak_planks*2",
              "result": {"name": "stick", "count_per_craft": 4},
              "requires_table": false, "available": true,
              "ingredients": [{"name": "oak_planks", "count": 2}]}]}
```

| status | 含义 |
| --- | --- |
| `available` | 至少一个 2×2 配方现在材料就够 |
| `insufficient_material` | 有 2×2 配方，但当前材料都不够（仍然逐个列出，方便模型看出差什么） |
| `crafting_table_required` | 只有工作台配方 → `recipes: []`（**绝不自动去找工作台**） |
| `recipe_not_found` | 这个物品没有配方 / 物品名不在表里 |

* 只返回语义投影：`recipe_id` / `result` / `requires_table` / `available` / `ingredients`；
  **不返回** raw Recipe / 数字 id / metadata / inShape / delta / JS 内部结构（有测试断言）。
* `recipes` 按"材料齐了优先 + recipe_id 稳定排序"排列，最多列 12 条（`total` 给全量）。

## 四、recipe_id = 稳定的可读签名（§七）

* `recipe_id` = `[!]<结果名>*<每刀产出>=<材料名>*<数量>[+<材料名>*<数量>…]`，
  例如 `stick*4=oak_planks*2`；需要工作台的加 `!` 前缀（`!chest*1=oak_planks*8`）。
* **绝不用数组下标**：同一个输出有多个配方时（1.21.1 里 stick 有 12 个），序号会随背包/版本变化；
  可读签名只由 `result + requires_table + ingredients/shape` 决定，背包怎么变都一样。
* 极端情况下两条配方的可读签名完全相同（同名字材料同产物、只有形状不同）→ 这时才追加
  6 位形状摘要后缀（`~ab12cd`），且后缀只由形状决定（仍然与背包无关）。1.21.1 全库 1470 条
  配方实测零冲突，所以模型看到的一律是可读 id。
* 可读的另一个好处：**确认摘要可以直接从 id 展开**（见下）。

## 五、minecraft_craft（MEDIUM）

参数（`additionalProperties: false`）：

```json
{"recipe_id": "stick*4=oak_planks*2"}
```

* **只有 recipe_id**：第一版没有 `count` / `times` / `batch_size` —— Mineflayer 的 `count` 是
  "执行几次配方"而不是"产出几个"，直接固定为 1，彻底避免语义误解；
* schema 里带 `pattern`（`^!?[a-z0-9_]+\*[0-9]+=`）：形状不对的 id 在**进确认门之前**就被拒；
* MEDIUM → 确认门（USER 回合 + 可信玩家 + allow_medium + 一次性确认）；exclusive + detached；
* 确认摘要**展开成人话**（§十三）：`用 2 个 oak_planks 制作 4 个 stick`；
  fingerprint 只绑 `tool + recipe_id`（id 已经唯一绑定输出/材料/形状/是否需要工作台）。

### 执行前重新解析（§十四/§十五）

确认只是授权：`start` 阶段**重新**从当前配方表解析 recipe_id，并用当前背包重算可用性：

| 情况 | 错误码 |
| --- | --- |
| 反解不出物品名 / 物品没有配方 | `recipe_not_found`（404） |
| 签名对不上（材料或形状变了） | `recipe_changed`（409） |
| 配方需要工作台 | `recipe_unavailable`（409） |
| 2×2 配方但材料不够 | `material_insufficient`（409，detail 带 `missing[{name,need,have}]`） |

**绝不自动准备材料**：不自动开箱取料、不移动背包、不挖矿、也绝不先做中间材料（§四十三：
只有 `oak_log` 时做 `stick` 直接失败，不会先做木板）。

### 执行与复核（§二十二/§二十三）

* `await bot.craft(recipe, 1, null)`：只走玩家自身 2×2；
* **不信任它的 resolve**：重新读整个背包（`countInventoryItem` 按名字汇总所有槽位，
  不只看 heldItem），要求 `产物增加 ≥ 每刀产出` **且** `每种材料消耗 ≥ 配方所需`；
  否则 `craft_unconfirmed`（500，带 before/after 的真实数字）。

成功结果（事件 result）：

```json
{"recipe_id": "stick*4=oak_planks*2", "item": "stick",
 "result": {"name": "stick", "count_per_craft": 4, "crafted_count": 4},
 "ingredients": [{"name": "oak_planks", "consumed": 2}],
 "before": {"result_count": 0, "ingredient_counts": {"oak_planks": 2}},
 "after":  {"result_count": 4, "ingredient_counts": {"oak_planks": 0}}}
```

before/after 全部是 `{name, count}` 语义快照（**不存 live Item**；4E 的教训）。

## 六、错误码

| 错误码 | HTTP | 何时 |
| --- | --- | --- |
| `minecraft.recipe_not_found` | 404 | 物品没有配方 / recipe_id 反解不出物品 |
| `minecraft.recipe_changed` | 409 | 确认时的签名与现在的不一致 |
| `minecraft.recipe_unavailable` | 409 | 需要工作台（本阶段只支持 2×2） |
| `minecraft.material_insufficient` | 409 | 材料不够（带 missing） |
| `minecraft.craft_failed` | 500 | 底层 `bot.craft` 失败 |
| `minecraft.craft_unconfirmed` | 500 | resolve 了但重读 inventory 对不上 |
| `minecraft.action_invalid` | 422 | recipe_id 形状不对 / 参数非法 |

## 七、取消与竞态（§二十八/§二十九）

* `runCancellable` 不会取消底层的 `bot.craft` Promise —— 所以：
  * 取消/超时按当前 ActionRuntime 的 race 规则只落一个终态（CANCELLED / TIMEOUT），
    **绝不因为底层恰好完成就报 SUCCEEDED**；
  * 底层如果已经改完背包，如实暴露（单测里就是这么验证的：取消后 `stick` 确实 +4，
    但终态仍然是 CANCELLED —— 这是被明确接受的语义，不偷偷改 ActionRuntime）；
* cleanup 只保留"清移动控制位"这一条与其他短动作一致的兜底，**没有**扩大 4E 的 async cleanup 契约
  （§四十四）：如果将来发现 CANCELLED/TIMEOUT 与底层 crafting Promise 的终止状态无法可靠同步，
  单独开 Phase 4E.1，而不是混进这一版。

## 八、WorldPerception

合成改的是 **inventory**，不是世界方块 → **不假装** `world.changed`（§三十八）。
本阶段的硬事实来源是"重新读取的 inventory"；`snapshot.self` 只能作为附加参考。

## 九、调试面（WebUI / HTTP）

| 路由 | 说明 |
| --- | --- |
| `POST /api/v1/minecraft/recipe_lookup` | `{item}`：SAFE，同步返回语义投影 |
| `POST /api/v1/minecraft/craft` | `{recipe_id}`：先校验参数，再过确认门（第一次 409） |

WebUI 的 **Crafting** 面板：LOOKUP 显示 `status / item / recipes` 表（recipe_id / 产物 /
材料 / available / requires_table），点一行即可填成 Craft 的 recipe_id；Craft 区显示
**自动展开的可读摘要**（"用 2 个 oak_planks 制作 4 个 stick"）。按钮只能**发起**确认，
真正的确认必须由用户在对话里做出（开发者入口是 SYSTEM 回合，拿不到执行权）。

## 十、配置

```yaml
minecraft:
  action:
    recipe_lookup:
      timeout: 10          # 查配方（纯本地计算）最长多少秒
    craft:
      timeout: 30          # 单次合成最长秒数
```

`allow_medium` 默认仍是 **false**（现在覆盖六个 MEDIUM 动作）。

## 十一、验证

* **Node 单测**（真实 1.21.1 配方表 + 假 inventory + 忠实的 `recipesFor`/模拟 craft）：
  * `recipe.test.js`（**54 checks**）：A lookup / B multiple recipes / C stable recipe_id（含冲突后缀）
    / D insufficient materials / E table-required 被排除 + 未知物品 / 参数 / 在线 / 非独占 /
    不泄露 raw Recipe；
  * `craft.test.js`（**56 checks**）：F success / G output reread / H ingredient reread /
    I craft_unconfirmed（含"只消耗没产出"）/ J recipe changed·unavailable·not found /
    K material_insufficient（含"只有原木不做中间材料"）/ L 取消 / M 超时 / N race /
    O cleanup 恰好一次 / P action.busy + 参数 / 离线。
* **Python**：`tests/test_minecraft_recipe_tool.py`（44：schema / canonical / offline / 任何回合可用 /
  非独占 / 四种状态 / 不泄露 raw / 服务层错误）、`tests/test_minecraft_craft_tool.py`（18：
  schema / 形状校验在确认门之前 / allow_medium / 意图门 / 可信玩家 / 确认全流程 /
  摘要展开 / mismatch / 过期 / 独占 / 六类服务错误 / 绝不准备材料 / activity 只写事实 /
  LLM 整轮）、API +4（两个端点 + 不能自授权 + 错误码映射一致）。
* **WebUI**：页面测试 +9（LOOKUP 渲染配方表 / 缺物品名 / 被拒 / 只有工作台配方 /
  用作 recipe_id + 摘要 / CRAFT 上报 / 缺 recipe_id / 被确认门拒 / 空态）。
* **flying-squid E2E**：配方表在本地，所以"查配方"能真跑（语义投影 / recipe_id 稳定 /
  工匠台状态 / 未知物品），craft 只验证拒绝路径（空背包 → `material_insufficient`、
  `recipe.changed`、`recipe.not_found`、忙时 `action.busy`、查配方非独占）；
  craft 成功路径与 STOP 段明确 **SKIPPED**（假服务器没有 /give，合成毫秒级）。
* **真实服务器 smoke**（§三十四-§三十七）：`/give oak_planks 2` 造夹具 → recipe_lookup
  **动态**找到"木板 → 木棍"的 recipe_id（脚本里不写死）→ craft（RUNNING → completed）→
  重读 inventory（产物 0→4、材料 −2）→ `/clear` 清夹具并逐槽比对回到测试前 → ensureIdle →
  disconnect → `REAL SERVER: PASS`。

## 十二、顺手修掉的一个真实 bug（Phase 4F 复盘）

`app/tools/result.py` 的 `_sanitize_data` 原来把 `depth > 4` 的结构截成 `None`：
`recipe_lookup` 的"配方 → 产物/材料"正好是第 5 层，会被**静默**吃掉（模型只看到 null）。
现已命名成 `MAX_DATA_DEPTH = 6` 并加了边界回归测试（第 5 层保留、超深仍然截断）。
这是被 4F 的测试抓到的产品缺陷，不是测试问题。

## 十三、本阶段不做（4G 及以后）

工作台（3×3）与自动放置工作台、熔炉/高炉/烟熏炉冶炼、酿造、附魔、锻造台、切石机、
村民交易、容器/背包自动补料、recipe chain（自动做中间材料）、批量合成（一次多个配方或多次执行）、
"把所有够做的东西都做一遍"、掉落物拾取。
