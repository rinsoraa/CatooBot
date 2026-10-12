# CatooBot Minecraft Phase 4C — 单方块 Place + Inventory 只读切片

> Phase 4B（`minecraft_dig`）的**对称实现**：加入 `minecraft_inventory`（只读背包切片，SAFE）与
> `minecraft_place`（放置单个方块，MEDIUM，同样要用户确认）。
>
> 原则：**当前手里有什么，就只能明确要求放什么；目标、方向、手持物品都必须明确；一次最多改一个方块。**

## 一、六个阶段的闭环

```text
LLM → minecraft_inventory / minecraft_world       （看清手里有什么、目标位置什么样）
    → minecraft_place(x, y, z, face, expected_item)
    → MinecraftActionPolicy（SAFE/LOW/MEDIUM 分级 + 意图门 + 可信门 + 忙 + 确认门）
    → MinecraftService → Action Runtime（exclusive，detached）
    → Mineflayer placeBlock(referenceBlock, faceVector)
    → 服务器确认 → 复核目标方块 → SUCCEEDED（事件带 result）
    → WorldPerception 下一次扫描看到新增方块
```

## 二、minecraft_inventory（SAFE，只读）

* 结构完全对称于 `minecraft_world`：`bridge_from(context)` → 策略门 → Service → ToolResult；
* 返回的切片只有五项：`online` / `selected_hotbar_slot` / `held_item{name,count}` /
  `items[{name,count}]`（按物品名聚合、按数量倒序、最多 40 种）；
* **不外泄** slot 原始结构 / NBT / internal id / window 对象 / 容器状态 / 盔甲 / cursor
  （工具只投影这五项，有测试断言）；
* 风险 SAFE：不需要用户意图、不需要可信玩家、不需要确认；但**仍需在线**
  （刻意不放进 `OFFLINE_TOOLS`——不在世界里时如实回答 `online:false`）；
* 非独占：移动/挖掘跑着的时候也能读（`NON_EXCLUSIVE_TOOLS` 加了一项）；
* 路由与工具：`GET /api/v1/minecraft/inventory`（读端点恒 200）+ `minecraft_inventory` 工具。

## 三、minecraft_place（MEDIUM，单方块）

参数（`additionalProperties: false`）：

```json
{"x": 100, "y": 64, "z": 100, "face": "up", "expected_item": "minecraft:dirt"}
```

* **整数坐标**：`100.5` 直接拒绝（方块坐标没有小数）；世界边界同 dig；
* **face 只有六个值**：`up/down/north/south/east/west` → `(0,±1,0)/(0,0,∓1)/(±1,0,0)`，
  绝不接受任意 `{dx,dy,dz}` 或浮点方向；`reference = target − face_vector`
  （face=up 时参考方块就是目标正下方那一格）；
* **expected_item 的语义**：不是"我希望最后出现什么"，而是
  **"我确认当前主手必须拿着这个物品，然后才能执行放置"**——runtime 执行前实时检查
  `heldItem != null && name 匹配 && count > 0`，否则 `held_item_missing` / `held_item_changed`；
  **不 equip、不切 hotbar**（名字比较时把 `minecraft:` 前缀归一化，`dirt` 与 `minecraft:dirt` 等价，
  回执仍报实际名）；
* **只往空气里放**：目标不是 air → `target_occupied`（不碰 grass/水/雪/藤蔓等 replaceable 语义）；
* 参考方块必须是实心（空气 → `reference_block_missing`），区块没加载 → `block_unavailable`；
* 距离 ≤ `max_distance`（默认 5，眼睛 → 目标方块中心，与 dig 同口径）→ 超了 `block_too_far`，
  **不自动 move_to**；
* 执行：`await bot.placeBlock(referenceBlock, faceVector)`（交给 Mineflayer，不自造右键/packet），
  完成后**重新读**目标方块：仍是 air → `block_place_unconfirmed`；与 expected_item 不同 →
  同样 `block_place_unconfirmed`（带 expected/actual）。**只有 `air → expected_item` 才算成功**；
* 成功结果（事件 result）：

```json
{"position": {...}, "block_before": "air", "block_after": "dirt",
 "reference_block": "grass_block", "face": "up",
 "item_before": {"name": "dirt", "count": 12}, "item_after_count": 11}
```

`item_after_count` 只如实上报、**不作为成功依据**（creative/modded 服务器行为不同；
真实服务器实测：runtime 内瞬时读到 5、随后的背包查询读到 4——客户端背包更新有延迟）。

## 四、Action Runtime（复用，未新建）

* `exclusive = true` + `detached = true`：与 dig/move_to/follow 互斥（不能边放边走），
  启动即 `RUNNING`，终态经 `minecraft.action.completed/failed/cancelled/timeout` 事件；
* `start` 阶段同步校验（held item / 目标 / 参考 / 距离）→ 失败直接返回，动作不进入 RUNNING；
* `cleanup`：`clearControlStates()`（place 是短动作，没有 stopPlacing）；CANCELLED/TIMEOUT/
  断开/退出都走同一套 cleanup，且**至多一次**（Phase 3B.1 语义）；
* 终态唯一：stop 与 placeBlock 同时落定也只产生一个终态（有 race 用例）；
* 配置：`minecraft.action.place.timeout`（5~120s，默认 30）与 `max_distance`（≤6，默认 5），
  随进程环境注入 runtime（`MC_PLACE_TIMEOUT_MS` / `MC_PLACE_MAX_DISTANCE`）。

## 五、确认门（沿用 Phase 4A/4B，未改）

第一次调用 → `minecraft.confirmation_required` + PENDING；确认摘要写清**放什么、放哪里、哪个面**
（例：`放置 minecraft:dirt 到 (100, 64, 100) 的 up 面`）。指纹覆盖**全部**参数（tool+x+y+z+face+
expected_item）：任何一个变了 → `confirmation_mismatch` 并作废重挂；过期 → `confirmation_expired`
并重挂；一次性消费。**确认只负责授权**：消费之后 runtime 仍要重新检查目标/参考/手持物品/距离。

TurnOrigin 规则不变：QQ/WebUI 的用户回合可以直接走到确认；**游戏内聊天仍要求可信玩家**
（否则 `user_not_trusted`）；INITIATIVE/BACKGROUND/SYSTEM 连确认都不产生。

## 六、错误码

| 错误码 | 含义 |
|---|---|
| `minecraft.held_item_missing` | 主手没拿东西 / 数量为 0（不自动装备） |
| `minecraft.held_item_changed` | 主手物品与 expected_item 不一致（带 expected/actual） |
| `minecraft.target_occupied` | 目标位置已经有方块（只往空气放） |
| `minecraft.reference_block_missing` | 参考方块是空气/不存在（没有可依附的面） |
| `minecraft.block_unavailable` | 目标区域没加载（blockAt 返回 null） |
| `minecraft.block_place_unconfirmed` | placeBlock resolve 了但世界里没有预期方块（带 expected/actual） |
| `minecraft.action_invalid` | 坐标非整数 / 越界 / face 非法 / expected_item 非法 |

## 七、WebUI（§二十六）

连接页新增 **Place Test** 面板：

* 显示 **主手物品 / 快捷栏槽 / 背包摘要**（来自 `GET /api/v1/minecraft/inventory`，随轮询刷新）；
* 表单：X / Y / Z / **Face（下拉六选一）** / Expected Item + `[用手持物品填入]` `[PLACE]` `[STOP]`；
* 本地拦截：小数坐标、空物品名、非法 face 一律不发请求；
* `POST /api/v1/minecraft/place` 是**开发者入口**：可以跳过"这一轮是不是用户对话"，
  但**必须过确认门**——点 PLACE 只会得到 `409 confirmation_required` 与待确认信息；
  管理台按钮仍然只有 CREATE TEST / CANCEL / EXPIRE。

## 八、测试

| 文件 | 覆盖 |
|---|---|
| `minecraft_runtime/test/place.test.js`（91 checks） | 注册表属性（exclusive/MEDIUM/30s/detached/cleanup）、禁止清单（place_multiple/build/bridge/schematic…）、参数校验（整数坐标 / 六 face / item）、start 阶段七类拒绝（held missing/changed、occupied、reference missing、unavailable、too far）、wait 阶段复核（仍 air / 放错方块 / 成功结果形状）、inventorySlice（聚合 / 无 slot·NBT·window / 离线）、以及**真实 place 动作 + 假 bot** 跑 ActionRuntime 的 CANCELLED / TIMEOUT / race（cleanup 恰好一次、终态唯一） |
| `tests/test_minecraft_inventory_tool.py`（13） | schema / 结构化五项 / 不外泄原始字段 / 空手空背包如实报告 / 离线 / disabled / 无 bridge / SAFE 无需意图 / 忙时仍可读 / 服务错误结构化 / 与 dig 在 executor 上互不干扰 |
| `tests/test_minecraft_place_tool.py`（16） | schema / 非法参数（小数坐标、非法 face、缺 item、多余参数）→ schema 层就拒且不挂确认 / disabled / offline / 非用户回合 / 不可信玩家 / allow_medium=false / 确认全流程（首次 required → 消费 → 再要）/ **改任一参数（x/y/z/face/expected_item）→ mismatch + 新确认** / 会话绑定 / 过期 / 独占 / 服务层六类错误 → 结构化 detail / 一次只放一块 / LLM 整轮流程 |
| `tests/test_web_api_minecraft.py`（24） | inventory 读端点恒 200 + 只读投影 + 绝不触发动作；place 端点参数非法 422 不挂确认、首次 409 `confirmation_required`、未启用 503 |
| `webui/src/pages/__tests__/minecraft.spec.ts`（32） | Place 面板：主手/槽位/背包摘要、提交体（含 face 与 expected_item）、小数坐标与空物品名本地拦截、用手持物品填入、被确认门拒绝时如实报错 |
| `minecraft_runtime/test/e2e.js`（flying-squid） | inventory 只读切片（字段集固定、空手→`held_item:null`）、空手 place → `held.item_missing`、非法 face → `face.invalid`、小数坐标 → `action.invalid`、**移动中也能读背包**（非独占）。成功路径不在这台假服务器上跑（它没有 `/give`、也没有挖掘掉落，bot 永远空手） |
| `minecraft_runtime/test/smoke_real_server.js` | 真实服务器：inventory 只读 → 找一个"实心方块 + 上方是空气"的目标 → place → 六层证据（RUNNING / completed / block_before=air / block_after=expected / 真实扫描 / 感知输入 3 次稳定）→ 手持物品 before/after 如实记录 → 把自己放的那一块挖回 → ensureIdle。没有手持物品或没有合适目标 → **SKIPPED**（不伪造） |

## 九、真实服务器结果（2026-10-06，操作者的局域网世界）

```text
✓ inventory 只读切片（online=true，1 种物品）
  主手：sand×5
✓ place 启动 → RUNNING（action_id=...）
✓ real place completed（block_before=air → block_after=sand）
✓ block_after == expected_item（期望 sand，得到 sand）
✓ 结果带 reference_block=sand / face=up
✓ world block changed（该位置现在是 sand）
✓ WorldPerception input stable（连续 3 次都是 sand，得 3/3）
  手持物品 before=sand×5 after=sand×4（result.item_after_count=5）
  已把自己放的那一块挖回（该位置现在 null）
✓ smoke 收尾：runtime 已 IDLE
REAL SERVER: PASS
```

注：`sand×5` 是之前几轮 dig smoke 挖出来的沙子被 bot 顺手捡走的（正好证明了 dig 的掉落与
pickup 是真实服务器行为）。place STOP 段与 dig 一样：place 是**极短动作**，真实 STOP 很难稳定
抢在服务器确认之前 —— 按 §三十三 不作为硬门禁（race/cleanup 由 `place.test.js` 覆盖）。

## 十、已知限制

1. 只放**一个**方块：没有连续建造、建筑规划、schematic、自动补货、自动找放置面；
2. 只往**空气**里放（replaceable 方块语义留给后续阶段）；
3. 只用**当前主手**物品，不自动装备/切槽/装满；
4. 不导航：目标必须在 5 格内；
5. 不捡掉落、不合成、不操作容器/红石、不攻击；
6. 放置的物品**不会自动回收**（smoke 只在物品徒手可挖时替自己清理）；
7. `allow_medium` 默认仍是 **false**（默认值没有变）。

## 十一、Phase 4D readiness

下一阶段（`inventory` 的写入面 / container / craft）可以照抄本阶段：
`minecraft_place` 已经把「手持物品硬约束 + 目标状态校验 + 二次验证 + 确认门 + 事件回流」
这套骨架跑通了。4D 真正需要新增的是 **hotbar/背包写操作**（`equip`/`moveItem`）与
**容器窗口**（`openContainer`/`withdraw`/`deposit`）——建议同样先做"明确指定一个槽位/一个物品"
的最小动作，并把「改背包」也纳入 MEDIUM+确认门（它是真实世界状态的一部分）。
