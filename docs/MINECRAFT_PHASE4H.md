# CatooBot Minecraft Phase 4H — Dropped Item Perception + 单实体 Pickup

> 本阶段只做两件事：**看得见地上的掉落物**（只读），和**捡起其中一个明确指定的掉落物**（MEDIUM）。
>
> 原则：`minecraft_dropped_items` 是**投影**（不泄露 raw metadata / 内部数字 id / UUID / velocity /
> entity 对象）；`minecraft_pickup_item` 是**一次一个明确实体**的世界操作 —— 不自动捡一片、
> 不做 `/collectblock`、不嵌套 `move_to`、不顺手挖方块。

## 一、两个工具，没有第三个

```text
minecraft_dropped_items   SAFE  非独占  只读      同步返回（一次投影）
minecraft_pickup_item     MEDIUM 独占   detached  启动即 RUNNING，终态经事件送达
```

* 没有 `minecraft_pickup_all` / `minecraft_pickup_nearest` / `minecraft_collect_all`；
* 没有"捡最近的那个"：目标必须是模型上一轮从 `minecraft_dropped_items` 里拿到的 `entity_id`，
  而且还要带 `expected_item` 做第二层身份校验（§十四）。

## 二、dropped_items 的语义投影（§四-§十一）

```json
{"ok": true, "online": true, "total": 2, "truncated": false,
 "items": [{"entity_id": 85351,
            "item": {"name": "oak_log", "count": 1},
            "position": {"x": 160, "y": 56, "z": 162},
            "distance": 2.57}]}
```

* 每个条目**只有四个字段**（`entity_id` / `item{name,count}` / `position` / `distance`）——
  绝不出 raw metadata、packet、内部数字 id、UUID、velocity、mineflayer entity 引用；
* 排序：距离升序，其次 `entity_id` 升序（同一世界状态下输出稳定、可复现）；
* 上限 32 条 + `truncated` 标记（有界载荷，不把一屏掉落物塞进上下文）；
* **只列 Item 实体**：玩家 / 动物 / 怪物 / 箭与抛射物 / 经验球 / 船与矿车 / 展示框一律不出现
  （`isDroppedItemEntity` 是唯一判断入口，不散落在各处）；
* 读不出物品栈或位置的实体**整条不列**（宁可不给，也不给半个实体 —— 没有 `name: null` 这种行）；
* 非独占：读的时候其它只读工具照常可用；它**不**缓存（`entityGone` / 被捡走后立刻从列表消失）。

### 真实 1.21.1 的 metadata 形态（§六）

`entity.metadata` 在不同版本里长得不一样，解码器对三种形态都认，且**绝不硬编码槽位数字**：

| 形态 | 出现在 | 怎么读 |
| --- | --- | --- |
| 按 key 索引的对象，值为字符串类型 `'item_stack'` | 1.20.2+（含 1.21.1） | 先按 `registry.entitiesByName['item'].metadataKeys` 找到叫 `item` / `item_stack` 的那一项（1.21.1 是第 8 项）再解码 |
| 同上，但值为**数字**类型 | 老版本 | 同一路径 |
| `[{key, type, value}]` 原始数组 | 部分版本 / 插件 | 按 `type`（或位置）取 `value` |

* 1.21+ 的物品栈结构是 `{itemId, itemCount, addedComponentCount, removedComponentCount,
  components, removeComponents}` —— **没有** `present` 字段，只有 `itemId` 有值就是"有物品"；
  老版本会用 `present: false` 表示空栈，这种仍然按"空"处理；
* 这一条是**真机 smoke 抓出来的**：一开始按 `present` 判断，真实服务器上 `dropped_items` 恒返回 0 条
  （实体明明在地上）。修复后真机能读出 `oak_log ×1`（见第十节）。
* 槽位推算（`droppedItemSlotIndex`）只在原始数组形态下作为回退，公式与 mineflayer 的
  item-metadata 索引公式一致（`5/6 + entityMetadataHasLong`）。

## 三、pickup_item 的语义（§十三-§三十六）

```json
{"entity_id": 85351, "expected_item": "minecraft:oak_log"}
```

**启动阶段**（同步反馈，出错直接 4xx/5xx，不会挂起）：

| 检查 | 失败 |
| --- | --- |
| 实体还在 `bot.entities` 里 | 404 `minecraft.item_entity_not_found` |
| 它是 Item 实体（不是玩家/怪物/箭） | 422 `minecraft.item_entity_invalid` |
| 物品栈读得出来 | 422 `minecraft.item_entity_invalid` |
| 物品名与 `expected_item` 一致 | 409 `minecraft.item_entity_changed`（detail 带 expected/actual） |
| 距离 ≤ `pickup.max_distance` | 422 `minecraft.pickup_target_too_far`（detail 带 distance/max_distance） |

**执行**：挂 `playerCollect` / `entityGone` 监听 → `GoalFollow(targetEntity, 1.2)`（**dynamic**，
跟着活实体走）→ 每 ~250ms 监管一次：

| 观察 | 处理 |
| --- | --- |
| STOP 到达 | 收尾（见下），终态 `CANCELLED` |
| `bot.entities[id] !== targetEntity`（id 被复用/实体被换） | 409 `minecraft.target_replaced` —— **绝不改绑**；"被收走/消失"不算替换（那交给收集确认） |
| 实体上的物品名变了 | 409 `minecraft.item_entity_changed` |
| 别的玩家先捡走了 | 409 `minecraft.pickup_target_lost`（**即使**背包随后增加了也不算我们的） |
| 距离 > `pickup.max_distance` | 422 `minecraft.pickup_target_too_far`（不无限追） |
| 距离 ≤ 1.2 | 停导航 + `clearControlStates`，记下 `distance_collected`，等服务器收集 |
| 收到"我们这边"的收集/消失信号 | 去核对背包（见下） |
| entityGone 但背包没增加 | 宽限 1.5s 后 500 `minecraft.pickup_unconfirmed` |

**成功判定是硬门禁**（§三十三）：`inventory_after > inventory_before` **且**收到收集/消失信号。
绝不写成 `after === before + target.count`（掉落物可能被合并、被别的来源补齐）。

**timeout** → `TIMEOUT` + cleanup；**cleanup** 恰好一次：`setGoal(null)` + `clearControlStates` +
摘监听（幂等，`wait` 与 `cleanup` 都调得安全）。

> 真机发现的一处**成功路径缺陷**已修：服务器可能在两次轮询之间就把物品收进背包
> （掉落物掉到下层、罐头跟着掉下去正好踩到），这时"进入半径"分支从来没跑过 ——
> 而 ActionRuntime 的契约是 **SUCCEEDED 不触发 cleanup**，于是会残留一个 `GoalFollow`。
> 现在成功分支也会自己收导航（Node 测试 W4 钉住）。

## 四、确认门（§三十九）

* fingerprint = `tool + entity_id + expected_item`（id 变了就是另一次授权）；
* 摘要必须让人看出"罐头可能会走过去"：

```text
拾取附近的 minecraft:dirt（实体 #123）
```

* TurnOrigin / 可信玩家 / 一次性 / TTL / 开发者入口不得自授权（`minecraft.confirmation_not_user_turn`）
  全部不变；WebUI 的 PICKUP 按钮同样**只能发起确认请求**，永远拿不到执行权。

## 五、错误码总表（4H 新增八个）

| 错误码 | HTTP | 何时 |
| --- | --- | --- |
| `minecraft.item_entity_not_found` | 404 | 实体不在了（已被捡走 / 消失 / 换了会话） |
| `minecraft.item_entity_invalid` | 422 | 不是 Item 实体，或物品栈读不出来 |
| `minecraft.item_entity_changed` | 409 | 实体上的物品与 `expected_item` 不一致（含运行途中被换掉） |
| `minecraft.target_replaced` | 409 | `entity_id` 对应的实体被替换（不自动改绑） |
| `minecraft.pickup_target_lost` | 409 | 被别的玩家捡走了 |
| `minecraft.pickup_target_too_far` | 422 | 超过 `pickup.max_distance`（启动时或运行途中） |
| `minecraft.pickup_failed` | 500 | 底层失败 |
| `minecraft.pickup_unconfirmed` | 500 | 实体消失但背包没增加（不假报成功） |

## 六、配置

```yaml
minecraft:
  action:
    pickup:
      max_distance: 16           # 掉落物最远多少格就去捡（1~64）
      timeout: 30                # 单次拾取最长多少秒（5~120）
```

**只有这两个键**（没有 `speed` / `radius` / `batch` / `auto_pickup`）。
`allow_medium` 默认仍是 **false**（现在是七个 MEDIUM 动作）。

## 七、WorldPerception

拾取不改方块 → **不假装** `world.changed`；硬事实来源是"重新读取的 inventory"和
"实体是否还在 `bot.entities` 里"。

## 八、安全边界（本阶段明确不做，§四十一）

批量拾取 / 捡最近/捡所有 / 自动扫地图捡掉落物 / `mineflayer-collectblock` /
自动挖方块换掉落物 / 丢弃物品 / 经验球 / 攻击·喂食·骑乘·剪羊毛·交易·和实体交互 /
跨维度取物 / 死亡掉落物的特殊处理。

## 九、WebUI（§四十）

* **Dropped Items 面板**：REFRESH 拉 `/minecraft/dropped_items`，表格列出
  `#id / 物品 / 数量 / 坐标 / 距离`，每行一个「用作目标」按钮（把 id 填进下面的表单）；
* **Pickup Test 面板**：`Entity ID` + `Expected Item` + `PICKUP` / `STOP`；
  PICKUP 只会得到 409 `confirmation_required`（开发者入口也不得自授权）；
* 没有 `PICKUP ALL` / `AUTO PICKUP` 这类按钮（§四十）。

## 十、验证

* **Node 单测**：
  * `dropped_items.test.js` → **42 checks**（A 提取 / B 过滤 / C 排序 / D 上限+truncated /
    E 读不全的实体整条不列 / F entityGone 不缓存 / G 被捡走后消失 /
    **H 真实 1.21.1 形态**（按 key 索引的对象 + 没有 `present` + `metadataKeys` 拿不到时的值扫描）；
  * `pickup_item.test.js` → **77 checks**（H 成功 / I 目标会移动 / J 被替换不改绑 / K 物品变了 /
    L 被别的玩家捡走 / M 途中被拉远 / N playerCollect / O 别的玩家 / P entityGone 没到账 →
    `unconfirmed` / P2 宽限内到账仍成功 / R cleanup / S STOP / T TIMEOUT / U 竞态 / V 终态恰好一次 /
    **W 真实 1.21.1 形态 + W4 成功也必须 `setGoal(null)`**）。
* **Python**：`tests/test_minecraft_dropped_items_tool.py`（12）/ `tests/test_minecraft_pickup_item_tool.py`（18）
  / Web API（+3）/ 路由快照（+2 条）/ 工具注册表与风险表枚举同步（17 个 minecraft 工具、七个 MEDIUM）；
  pytest 总数 **2287**。
* **flying-squid E2E**：`dropped_items` 空列表投影与字段白名单、拾取参数校验（`action.invalid`）、
  未知实体（`item_entity.not_found`）、只读工具在 `move_to` 期间仍可用而 pickup 撞 `action.busy`；
  真实 Item Entity 在假服务器上造不出来 → 明确 **SKIPPED**（不伪造）。
* **真实服务器 smoke**（1.21.1 + Fabric，`REAL SERVER: PASS`）：
  就地放一块 `oak_log`（记录原方块）→ `dig` 出**真实掉落物** → `dropped_items`
  读到 `#85351 oak_log ×1，2.57 格`（字段白名单校验通过）→ 把罐头挪远制造"要走一段"的窗口 →
  `pickup` RUNNING → **真 STOP** → `CANCELLED + goal == null + isMoving == false + 位置停稳 +
  掉落物还在 + 背包没增加` → tp 回原位、再挖一块、**真的把 Item Entity 捡进背包**
  （`real playerCollect = PASS`、`target entity gone = PASS`、`inventory increased = PASS`
  `oak_log 0 → 2`）→ `/clear` 夹具 + 还原临时方块 + 清掉夹具掉落物 → 逐槽签名与测试前**完全一致**
  → `ensureIdle` → `REAL SERVER: PASS`。

## 十一、本阶段不做（4I 及以后）

批量/自动拾取、掉落物生命周期管理（拾取冷却、合并）、丢弃与投掷、经验球、装备自动替换、
实体交互（攻击 / 喂食 / 骑乘 / 剪羊毛 / 交易）、库存预警与自动补料、村民与刷怪笼、
其他维度与传送门、箱子/桶以外的存储（潜影盒、漏斗、熔炉）。
