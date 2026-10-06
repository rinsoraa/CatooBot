# CatooBot Minecraft Phase 4D — Inventory Control：Equip + 单项 Inventory Move

> Phase 4C（`minecraft_place`）之后的**第一组背包写操作**：`minecraft_equip`（把指定物品拿到主手）
> 与 `minecraft_inventory_move`（把一个明确槽位上的物品移动指定数量到另一个明确槽位）。
> 两者都是 **MEDIUM**（改状态）→ 必须走同一套确认门。
>
> 原则（§二十一/§二十二）：**一次只操作一个明确物品 / 一个明确槽位；目标被占用就拒绝，绝不隐式交换；
> 结果以 runtime 的真实重读为准，绝不按 `+count` 硬编码报成功。**

## 一、闭环

```text
LLM → minecraft_inventory                     （只读：手里有什么、背包里有什么——按物品名聚合，无槽位）
    → minecraft_equip(item)                   （把"这个物品"拿到主手）
    → minecraft_inventory_move(source_slot, destination_slot, item, count)
    → MinecraftActionPolicy（风险开关 → USER 回合 → 可信玩家 → 忙 → 确认门）
    → MinecraftService → Action Runtime（exclusive，detached）
    → Mineflayer bot.equip(item, 'hand') / bot.transfer({单槽 source → 单槽 dest})
    → **重新读 heldItem / source / destination** → SUCCEEDED（事件 result）或 *_unconfirmed
    → WorldPerception 下一次扫描看到新的手持/背包状态
```

## 二、minecraft_equip（MEDIUM）

参数（`additionalProperties: false`）：

```json
{"item": "minecraft:dirt"}
```

* 只有 `item` 一个参数；**`destination` 固定 `hand`**，不接受盔甲/副手/快捷栏槽位；
* 名字比较与 `minecraft_inventory` 一致（`dirt` ≡ `minecraft:dirt`），但**不**接受"猜"的名字；
* 选物品的方式是**确定性的**：runtime 按槽位从小到大找第一个匹配的 stack
  （`findInventoryItem`）——不随机、不按数量、不挑"更合适"的 stack、不换槽；
* 已经拿在手里 → 不调用 Mineflayer，直接如实回 `already_equipped: true`（且**实时**重读
  `heldItem`，不看缓存），槽位一个都不动；
* 背包里没有 → `item.not_found`（404），**绝不自动造物、绝不改成别的物品**；
* 执行用 Mineflayer 原生 `bot.equip(item, 'hand')`：物品在快捷栏时就是"切到那一格"，
  在主背包时会被放进一个空快捷栏格；**执行后重新读 `heldItem`**，不是预期物品 →
  `equip.unconfirmed`（500，带 expected/actual）；
* 成功结果（事件 result）：

```json
{"item": "dirt", "destination": "hand", "source_slot": 12,
 "held_item": {"name": "dirt", "count": 12}, "already_equipped": false}
```

它是 `minecraft_place` 的**前置**：`expected_item` 必须与当前主手一致，所以"想放没拿着的方块"
= 先 `minecraft_inventory` 看清 → `minecraft_equip` → 再 `minecraft_place`（两次独立确认）。

## 三、minecraft_inventory_move（MEDIUM）

参数（`additionalProperties: false`）：

```json
{"source_slot": 37, "destination_slot": 9, "item": "minecraft:dirt", "count": 1}
```

* 槽位是**玩家窗口绝对槽位**：主背包 9–35 + 快捷栏 36–44（`PLAYER_SLOT_MIN/MAX`）。
  非整数 / 越界 / `source == destination` → 400 `slot.invalid`；
* `count` 必须是 ≥ 1 的整数（`item.invalid`），`item` 必须与 source 槽位上的**真实**物品一致
  （不一致 → `item.changed`，数量不够 → `item.count_insufficient`）；
* source 是空的 → `item.not_found`；**目标被别的物品占用 → `destination_occupied`（409），
  绝不隐式交换、绝不换目标槽位重试**；目标同名可堆叠（未满）→ 允许合并；
* 执行用 Mineflayer 原生 `bot.transfer({window, itemType, count, sourceStart/End, destStart/End})`，
  把 source/destination 都**钉死在单个槽位**上（不给 transfer 自己挑槽的机会）；
* **执行后重新读 source 与 destination**，按真实状态判定：
  `movedOut = source_before.count - source_after.count`、`destGained = dest_after.count - dest_before.count`，
  要求 `movedOut >= count` 且 `destGained > 0`；否则 `move.unconfirmed`（500，带
  `source_after` / `destination_after` 的真实值）——**绝不硬编码 `+count`**；
* 成功结果（事件 result）：

```json
{"item": "dirt", "source_slot": 37, "destination_slot": 9, "requested_count": 1,
 "source_after": {"name": "dirt", "count": 2}, "destination_after": {"name": "dirt", "count": 1}}
```

## 四、风险、意图与确认（与 dig/place 同一套门）

* 两个工具都是 **MEDIUM**（`ACTION_RISK`），默认 `allow_medium: false`
  ——打开后**仍然**要用户确认；
* 门顺序不变：minecraft 启用 → 工具启用 → 在线 → 风险开关 → **USER 回合**（`TurnOrigin.USER`）
  → 可信玩家（来自游戏内玩家说话时）→ 独占忙 → 确认门；
* 两者都 **exclusive**（`NON_EXCLUSIVE_TOOLS` 只多一个只读的 `minecraft_inventory`）：
  移动/挖/放跑着的时候提交 equip/move → `minecraft.action_busy`；
* 确认摘要（用户在对话里看到的原文）：

```text
把 minecraft:dirt 拿到手里
把 37 格的 minecraft:dirt ×1 移到 9 格
```

* 确认指纹覆盖**全部**参数：换物品 / 换槽位 / 换数量都会触发 `confirmation_mismatch`
  并按新参数重挂一条（用户确认的是"这一件事"，参数一变就不是同一件事）；
* **WebUI 与开发者入口拿不到执行权**：`invoke_developer` 的 turn_origin 是 SYSTEM，
  消费确认时先撞"来源门" → `minecraft.confirmation_not_user_turn`（409），
  runtime 一个请求都收不到（有测试）。真正的确认只能由用户在对话里说"确认"。

## 五、调试面（WebUI / HTTP）

| 路由 | 说明 |
| --- | --- |
| `GET /api/v1/minecraft/inventory/slots` | **调试**用原始槽位表（`slot/name/count/hotbar` + `hotbar_start`/`inventory_start`）；只读、恒 200；**不是 LLM 的数据源** |
| `POST /api/v1/minecraft/equip` | 开发者入口 `{item}`：先参数校验，再过确认门（第一次 409） |
| `POST /api/v1/minecraft/inventory_move` | 开发者入口 `{source_slot,destination_slot,item,count}`：槽位在进确认门**之前**校验 |

`minecraft_inventory`（LLM 侧）**仍然只有五项聚合切片**，槽位号只出现在
WebUI 的 Inventory Control 面板与 smoke 里——模型看不到原始槽位，所以它只能按物品名操作；
要按槽位操作时必须由用户明确报出槽位（或开发者照着面板操作）。

## 六、错误码

| 错误码 | HTTP | 何时 |
| --- | --- | --- |
| `minecraft.item_not_found` | 404 | 背包里没有这个物品 / source 槽位是空的 |
| `minecraft.item_changed` | 409 | source 槽位上的物品与请求不一致（带 expected/actual） |
| `minecraft.item_count_insufficient` | 409 | source 的数量不够（带 available/requested） |
| `minecraft.destination_occupied` | 409 | 目标被别的物品占用（或同名堆已满）——绝不交换 |
| `minecraft.slot_invalid` | 422 | 槽位不是 9–44 的整数 / source == destination（runtime 侧） |
| `minecraft.equip_unconfirmed` | 500 | equip resolve 了但主手不是预期物品（带 expected/actual） |
| `minecraft.move_unconfirmed` | 500 | transfer resolve 了但 source/destination 的真实状态不符 |

## 七、验证

* **Node 单测**：`minecraft_runtime/test/equip.test.js`（59 checks：选槽确定性、already_equipped、
  三阶段校验、CANCELLED/TIMEOUT/race/cleanup 恰好一次、注册表白名单）+
  `inventory_move.test.js`（56 checks：槽位范围、同槽、count、occupancy、合并、transfer 参数钉死、
  重读校验、`destination full` → `destination_occupied`）；
* **Python**：`tests/test_minecraft_equip_tool.py`（17：schema/离线/禁用/allow_medium/意图门/可信/确认
  全流程/参数变化/过期/独占/错误码/equip→place 联动链）、`tests/test_minecraft_inventory_move_tool.py`
  （17，含"目标被占用绝不交换"与"同名合并允许"）、`tests/test_web_api_minecraft.py`（+5：槽位视图、
  两个端点的校验与"开发者入口不能自授权"、错误码映射与 Service 异常一致）；
* **E2E（flying-squid）**：只验证拒绝路径（假服务器没有 `/give`，背包永远空）——槽位视图、
  参数校验、空槽位/不存在物品的拒绝、独占冲突；STOP 段明确 **SKIPPED**；
* **真实服务器 smoke**：`node minecraft_runtime/test/smoke_real_server.js`（不进 CI）——
  `inventory/slots` → `already_equipped`（零副作用）→（背包只有一件物品时用服务器自己的
  `/give` 造一件夹具，结束后 `/clear`）→ `equip` 真机换手（HTTP RUNNING + 终态 completed +
  重读主手 + WorldPerception）→ `inventory_move` 真机搬运（终态 result + 重读槽位表 +
  WorldPerception）→ 搬回去 → 恢复主手 → 清夹具 → **逐槽比对布局** → ensureIdle → disconnect。
  报告为 PASS 的那一次：`36:sand×4` 布局与 `sand×4` 主手在测试前后完全一致。

## 八、本阶段不做（Phase 4E 及以后）

* 容器（箱子/熔炉/工作台）、合成、丢弃（drop）、拾取（pickup）、批量整理 / 自动排序；
* **自动装备链**（"要放土就自己找土"）、自动补货、多物品搬运；
* 盔甲 / 副手装备（equip 只支持 `hand`）；
* 按物品名搬运（move 必须给槽位）、按槽位装备（equip 只给物品名）——两条路径刻意不混。
