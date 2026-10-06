# CatooBot Minecraft Phase 4J — Tool Capability Knowledge（挖掘能力只读模型）

> 只回答一件事：**罐头现在站在这里、现在主手拿着这个东西，面对这个方块，实际能不能挖，预计多久。**
>
> `minecraft_dig_capability` 是**只读 SAFE 工具**：不改世界、不改背包、不装备、不切槽、**不移动、不导航、不挖**。

## 一、完整链路

```text
minecraft_world              → 看到目标方块与坐标
minecraft_inventory          → 看到当前主手
minecraft_dig_capability(x,y,z)
        → can_dig / dig_time_ms / held_item / 两种距离 / reason
     ↓ LLM 自己决定
if can_dig:      minecraft_dig
else:            minecraft_inventory → minecraft_equip → minecraft_dig
```

上面是**模型/Agent 的决策链**，不是工具内部自动执行的（§二十一）：capability 不会替你去 equip，
更不会替你去挖。

## 二、参数与语义（§四/§五）

```json
{"x": 100, "y": 64, "z": 100}
```

* 三个参数**必填且必须是整数**（`100.5` 直接拒绝）——查的是"**那个位置真实存在的方块**"，
  不接受调用者自己声称的 block name，也不接受 `expected_tool`：
  问题永远是「**我现在真实拿着的东西**，能不能挖这个真实方块」，不是「假设我有某把工具」。

## 三、事实来源（§六/§十一/§十二/§十三/§三十）

全部是 **Mineflayer 的运行时**，绝不自己维护方块硬度 / 工具等级 / 最佳工具表：

| 事实 | 来源 |
| --- | --- |
| 那个位置是什么方块 | `bot.blockAt(pos)`（读不到 → 结构化错误 `minecraft.block_unavailable`） |
| 当前主手 | `bot.heldItem`（只投影 `{name, count}`，绝不出 raw Item / NBT / components / 内部 id） |
| 能不能挖 | `bot.canDigBlock(block)` |
| 预计多久 | `bot.digTime(block)`（毫秒；坏值/不可算 → `null`，绝不返回负数或 `Infinity`） |

每次调用都是**实时读取**（§六）：不拿 `minecraft_world` / `minecraft_inventory` 之前的缓存当硬事实。

## 四、返回结构（§十四/§十五/§十六）

```json
{"ok": true,
 "position": {"x": 100, "y": 64, "z": 100},
 "block": {"name": "iron_ore"},
 "held_item": {"name": "stone_pickaxe", "count": 1},
 "distance": {"goal_near": 1, "raw": 4.28},
 "can_dig": true,
 "dig_time_ms": 1250,
 "reason": null}
```

* **两种距离口径分开**（§十五，沿用 Phase 4H.1 的教训）：
  * `distance.goal_near` —— 罐头**占的方块格** → 目标方块格（GoalNear 口径）；
  * `distance.raw` —— 眼睛 → 方块中心的浮点距离（**与 `minecraft_dig` 的距离门禁同一个量**）；
  绝不混成一个含糊的 `distance` 让模型猜。
* `reason` 只有有限枚举：`null`（能挖）/ `air` / `too_far` / `not_diggable`
  —— 不推测"工具等级不够"这种运行时没说的事（§十六）；"那个位置没有方块"是**结构化错误**
  `minecraft.block_unavailable`（404），而"那里是空气"是**正常数据**（`reason: "air"`，§七/§八）。
* **不返回任何推荐**：没有 `recommended_tool` / `best_tool`（§十七）——那是另一套离线知识，
  本阶段不做；要不要换工具由模型/用户决定，换工具走独立的 `minecraft_equip`（§十八）。

## 五、风险与策略（§三/§十九/§二十）

| 项 | 值 |
| --- | --- |
| risk | **SAFE** |
| exclusive | **false**（可与 move_to / dig / place / equip / pickup / craft 并行读取） |
| online | **required**（不进 OFFLINE_TOOLS） |
| 用户明确要求 | 不需要（任何回合都能查） |
| 确认门 | 不需要（不产生任何 PENDING） |
| ActionRuntime | **不新增 Action**，走既有的只读动作通道（`dig_capability`），
  路径仍然是 Tool → Agent Bridge → Policy → Service → runtime（工具绝不直接碰 Mineflayer） |

## 六、真机上观察到的真实事实（不人为规定，§二十四）

同一块 `stone`，三次查询（1.21.1 + Fabric，服务器自己说了算）：

```text
拿 minecraft:dirt            → can_dig=true  / dig_time_ms=7500
拿 minecraft:stone_pickaxe   → can_dig=true  / dig_time_ms=600     ← 快 12 倍
空手                          → can_dig=true  / dig_time_ms=7500
```

两个值得记住的结论：

1. **`bot.canDigBlock` 不看工具**（它是 `block.diggable && 眼睛到方块中心 ≤ 5.1`）——
   所以"拿错东西"时它仍然是 `true`，真正体现工具差异的是 `digTime`（7500ms vs 600ms）。
   我们不替它编一套规则，如实把两个数都给模型。
2. `dig_time_ms` **确实来自运行时的工具感知计算**：同一个方块，拿对工具明显更快
   （真机 smoke 里就是这么交叉验证的）。

## 七、WebUI（§二十八）

Minecraft → **Dig Capability** 面板：`X / Y / Z` + `CHECK`，显示
`Block / Held Item / GoalNear Distance / Raw Distance / Can Dig / Dig Time / Reason`。
**没有** `AUTO EQUIP`、**没有** `AUTO DIG` —— 面板只回答事实（页面测试里专门断言了这一点）。

## 八、配置与文档（§二十七/§二十九）

* **没有新增任何用户配置**（不建 `tool_capability_timeout` / `recommendation_strategy` 之类）；
* 工具数量 **+1**（18 个 Minecraft 工具）；`docs/README.md` / `CHANGELOG.md` /
  `docs/WEBUI_API_CONTRACT.md` / `config/config.example.yaml` / `app/web/config_registry.py`
  的文案已同步（`allow_safe` 的说明里补上了只读的能力查询）。

## 九、验证

* **Node** `minecraft_runtime/test/dig_capability.test.js`（**43 checks · A–L**）：
  注册表属性（SAFE / 非独占 / 不是新 Action / reason 枚举）、投影每一条事实、`canDigBlock` 真/假、
  `digTime` 正常/抛错/Infinity/负数、空气（含 `cave_air`）、`block.unavailable` 404、
  坐标校验（浮点/字符串/缺参/越界）、**完全没有副作用**（连续 10 次查询零 `dig`/`equip`/切槽/清控制位）、
  输出字段固定（没有推荐字段）、以及**前台 move_to 跑着时只读查询照样执行**（同时验证独占动作仍被 `action.busy` 拒绝）。
* **Python** `tests/test_minecraft_dig_capability_tool.py`（**17 个用例 · A–O**）：
  schema（只有三个整数、没有 block name / expected_tool）、离线、非法参数、`block_unavailable`、
  空气、`too_far`、`can_dig` 真/假、`dig_time_ms`、主手有/无、不泄露 raw Item、**SAFE 任何回合都能用 + 不产生确认**、
  非独占（前台动作跑着也能查）、Service 错误结构化。
* **WebUI** vitest +6（页面 84 个）：初始不显示结论、CHECK 发 `{x,y,z}` 并显示七项、
  挖不动时显示原因与 `-` 耗时、空手显示"空手"、非整数坐标本地拦截、
  `block_unavailable` 如实报错且**没有触发任何 equip/dig**（也没有那两个按钮）。
* **真实服务器 smoke**（1.21.1 + Fabric，`REAL SERVER: PASS`）：

```text
✓ capability 查询 → 200
✓ capability 回答的是那个真实方块（{"name":"stone"}）
✓ 两种距离口径分开上报（goal_near=2.24 / raw=2.51）
✓ capability mismatch-tool state = PASS（如实报告当前主手是 dirt）
✓ capability tool-held = PASS（held=stone_pickaxe / can_dig=true / dig_time_ms=600）
✓ real can_dig matches runtime = PASS（同一方块：石镐 600ms < dirt 7500ms）
✓ real dig_time_ms is finite/valid when diggable（600ms）
✓ capability empty-hand = PASS（held_item=null / can_dig=true / dig_time_ms=7500）
✓ no world modification = PASS（三次查询之后那个方块还是 stone；背包里没有多出挖下来的石头）
✓ inventory restored = PASS（capability 没有消耗/移动任何原有物品）
✓ ensureIdle = PASS（capability 查询之后 runtime 仍然 IDLE）
```

  smoke 这一段**全程没有挖任何方块**（§二十五）：只 `/setblock` 一块临时 stone 当确定目标，
  查完把它还原；主手用 `equip` / `inventory_move` 摆出三种状态，最后逐槽恢复。

## 十、边界（本阶段明确不做，§二/§三十/§三十一/§三十二）

自动装备 / 自动换槽 / 自动移动 / 自动挖 / 自动导航 / 自动找矿 / 自动找/制作/修工具 /
连续采集 / 资源规划 / mining loop；**最佳工具排序与推荐**；
**不建** `tool_capability.json` / `blocks.json` / `tool_tiers.yaml` 这种能力数据库
（Mineflayer 运行时已经给了 `canDigBlock` / `digTime`，优先用真实运行时事实）；
`minecraft_dig` 本阶段**一行没动**（没发现与 4I 的运行时有冲突）。

下一阶段（4K = Resource Gathering）才把"找方块 → move_to → equip → dig → dropped_items → pickup
→ 背包复核"编排起来，而且每一步都保持现有的 confirmation / risk 边界。
