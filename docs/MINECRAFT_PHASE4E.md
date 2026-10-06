# CatooBot Minecraft Phase 4E — Container Control：Chest / Barrel 读取 + 单项存取

> Phase 4D（背包写操作）之后的**容器读写**：`minecraft_container_inspect`（只读看一个箱子/桶，
> SAFE）与 `minecraft_container_transfer`（在一个明确容器格与一个明确背包格之间搬一次，MEDIUM）。
>
> 原则：**只支持单方块 Chest / Barrel；一次只动一个物品、一个容器格、一个背包格、一个数量；
> 目标格被占用就拒绝（绝不交换、绝不换格）；报成功之前必须关窗，结果以真实重读为准。**

## 一、闭环

```text
LLM → minecraft_container_inspect(x, y, z)            （看清第几格有什么 → 拿到 container_slot）
    → minecraft_container_transfer(x, y, z, direction = withdraw | deposit,
                                   container_slot, inventory_slot, item, count)
    → MinecraftActionPolicy（风险开关 → USER 回合 → 可信玩家 → 忙 → 确认门）
    → MinecraftService → Action Runtime（exclusive）
    → openContainer → 实时校验 source / destination → bot.transfer（source/dest 各钉死单槽）
    → 重新读 container + inventory → close → SUCCEEDED（事件带 result）
```

**生命周期边界（本阶段最重要的一条）**：没有 `minecraft_open_container` /
`minecraft_close_container` 这种长期状态型工具。窗口的生命周期完全在动作内部：
`inspect` = validate → open → read → close；`transfer` = validate → open → validate window →
validate source/dest → transfer → reread → close。**LLM 永远不会拥有一个"半开着的窗口"。**

## 二、支持范围（其余一律 `container.unsupported`）

| | |
| --- | --- |
| 支持 | `minecraft:chest`（单箱）、`minecraft:barrel`（单桶），方块名比较时归一化 `minecraft:` 前缀 |
| 明确拒绝 | trapped chest、双箱（窗口 54 格）、shulker box、hopper、dropper、dispenser、furnace / blast furnace / smoker、brewing stand、crafting table、村民/马/矿车箱子、交易 |
| 判定方式 | 只按 `block.name` 判类型（**不靠** blockEntity、不靠"看起来像容器"）；再按**真实窗口结构**判定单方块（`window.inventoryStart === 27` 且玩家侧 36 格，双箱是 54 → 拒绝） |

## 三、minecraft_container_inspect（SAFE，只读，但**独占**）

* 参数：`{x, y, z}`（整数、世界边界、`additionalProperties: false`）；**不自动导航**（超过距离上限直接拒绝）。
* 风险 SAFE 的含义，仅限："对支持的单方块 chest/barrel 做只读 inspection"。它仍然 **exclusive**
  （打开真实窗口是有生命周期的客户端状态，不能和其他前台动作并发），也仍然需要在线；
  **没有**放进 `OFFLINE_TOOLS` / `NON_EXCLUSIVE_TOOLS`。
* 输出（模型看到的 JSON）：

```json
{"ok": true,
 "container": {"type": "chest", "label": "Chest", "position": {"x": 100, "y": 64, "z": 100}, "size": 27},
 "slots": [{"slot": 0, "name": "dirt", "count": 12}, {"slot": 7, "name": "sand", "count": 32}]}
```

* `slots` **只列非空**格子（容器内编号 0..26）；**不外泄** window 对象 / NBT / 内部 id / mouse-cursor /
  packet / 玩家背包格；`minecraft_inventory` 的聚合五项结构保持不变（模型平时看不到槽位，
  槽位只在 inspect 结果与 WebUI 调试面板里出现）。
* 工具摘要会直接说清内容（`看了 (100, 64, 100) 的 Chest：dirt×12（第 0 格）、sand×32（第 7 格）。`）。

## 四、minecraft_container_transfer（MEDIUM）

参数（`additionalProperties: false`）：

```json
{"x": 100, "y": 64, "z": 100, "direction": "withdraw",
 "container_slot": 0, "inventory_slot": 9, "item": "minecraft:dirt", "count": 1}
```

* `direction`：`withdraw` = 容器格 → 背包格；`deposit` = 背包格 → 容器格。
  一次只能一个 source 槽 + 一个 destination 槽。
* 坐标整数；`container_slot` 是 ≥ 0 的整数（真正上界由**真实窗口大小**决定，在 runtime 里校验）；
  `inventory_slot` 沿用 Phase 4D 的 `9..44`（主背包 + 快捷栏，不重新发明编号）；`count ≥ 1`；`item` 非空。
* **禁止模糊语义**：模型必须先 inspect 再给出精确参数。"拿一些泥土出来 / 整理一下箱子 /
  把所有沙子拿出来"这种要求不许猜——参数对不上就是 `item.changed` / `item.count_insufficient`。

### 执行前的实时校验（打开窗口之后，inspect 时的旧状态不算数）

| 检查 | 失败码 |
| --- | --- |
| source 存在 | `item.not_found` |
| source 的物品名与 `item` 一致（归一化比较） | `item.changed`（带 expected/actual） |
| source 数量 ≥ `count` | `item.count_insufficient`（带 available/requested） |
| destination 空，或**同名且未满**（容量来自真实 `item.stackSize` / minecraft-data，绝不硬编码 64） | `destination.occupied` |

**绝不隐式交换、绝不换 destination 槽、绝不自动找空位。**

### 执行与复核

* 用 Mineflayer 原生 `bot.transfer({window, itemType, count, sourceStart/End, destStart/End})`，
  把 source 与 destination 各自**钉死在单个槽位**上（不给 transfer 自己挑槽的机会）；
  不用 `window.withdraw/deposit`（它们按 item type 操作，表达不了"就是这一格 → 就是那一格"）。
* **不信任 transfer 的 resolve**：完成后重新读 container 与 inventory 两侧，按**真实变化量**判定：

```text
movedOut  = source_before.count - source_after.count
gainedIn  = destination_after.count - (destination_before.count or 0)
成功条件：movedOut ≥ count 且 gainedIn > 0
```

  否则 `container.transfer_unconfirmed`（500，带真实的 container_after / inventory_after）。
* 成功结果（事件 result）：

```json
{"direction": "withdraw", "position": {"x": 100, "y": 64, "z": 100},
 "container_type": "chest", "item": "dirt", "count": 1,
 "container_slot": 0, "inventory_slot": 9,
 "container_before": {"name": "dirt", "count": 12}, "container_after": {"name": "dirt", "count": 11},
 "inventory_before": null, "inventory_after": {"name": "dirt", "count": 1},
 "moved_out": 1, "gained_in": 1}
```

  deposit 同形（before/after 各自对应方向的 source/destination）。

## 五、窗口生命周期与 cleanup（本阶段的核心测试点）

* 动作把打开的窗口记在 ActionRuntime 的 `controller.window` 上（Phase 4E 给 `start`/`run`/`cleanup`
  加了 controller 参数，见 `action_runtime.js` 头注释；既有动作的 `cleanup(bot)` 行为不变）。
* **无论成功、失败、异常、超时、取消（STOP）、断开**，窗口都必须被关掉：
  * 正常路径：`transfer` 复核完 → close → 才落终态；
  * 失败路径（start 抛错 / transfer 抛错 / 复核不符 / 取消 / 超时）：动作自己 close，
    再由 runtime 的 `cleanup` 兜一层（close 幂等：当前窗口不是它 → 什么都不做）；
  * `cleanup` 至多执行一次、可重复调用、close 抛错不能阻止终态、disconnect 时不能崩。
* **超时/取消期间才开出来的窗口**：`openContainer` resolve 之后立刻检查 token，已取消 → 立刻关掉再
  按取消收尾（绝不留下开着的窗口）。
* **close 失败**：世界状态可能已经变了 —— 如实报 `container.close_failed`（500，带上已测得的结果），
  **绝不吞掉** close error；如果 transfer 本身也失败了，主因是 transfer 的错误，
  close 的错误附加在 detail 里（两条事实都不丢）。
* **意外关闭**（玩家/服务器关掉窗口、方块被破坏）：`container.closed`（409），绝不无限等待。
* 打开容器前若发现还残留一个窗口（上一次异常留下的），先 best-effort 关掉再开 ——
  否则 `openBlock` 会等不到新的 `windowOpen`。

## 六、确认门（与 dig/place/equip/inventory_move 同一套）

* `inspect` = SAFE（不需要用户意图、不需要可信玩家、不需要确认）；`transfer` = MEDIUM（全都要）。
* 摘要就是授权文本：

```text
从 (100, 64, 100) 的容器第 0 格取 minecraft:dirt ×1 到背包第 9 格
把背包第 9 格的 minecraft:dirt ×1 放入 (100, 64, 100) 的容器第 0 格
```

  （类型在**执行时**才由 runtime 验证——本阶段只可能是 Chest / Barrel，所以摘要里写"容器"不写死类型。）
* Confirmation hash 覆盖 `tool / x / y / z / direction / container_slot / inventory_slot / item / count`：
  任一字段变化 → 旧确认作废 + 按新参数重新 PENDING。
* 确认只负责**授权**：真正执行前的 open → reread → validate 仍然完整跑一遍。
* TurnOrigin 语义不变：只有 USER 能产生 explicit intent；游戏内可信玩家门不变；
  **WebUI / 开发者入口（SYSTEM 回合）拿不到执行权** —— 非用户回合消费确认 →
  409 `minecraft.confirmation_not_user_turn`（有测试钉住）。

## 七、独占与并发

```text
container_inspect / container_transfer  exclusive（开窗是客户端状态）
minecraft_inventory                      非独占只读（忙时也能读）
```

`container_transfer + equip / inventory_move / dig / place / move_to / container_inspect`
全部互斥 → `minecraft.action_busy`（409）。

## 八、错误码

| 错误码 | HTTP | 何时 |
| --- | --- | --- |
| `minecraft.container_unsupported` | 422 | 不是 chest/barrel 方块、双箱、窗口结构不符 |
| `minecraft.container_too_far` | 422 | 超过距离上限（不会自己走过去） |
| `minecraft.container_open_failed` | 500 | 服务器没给窗口 / 打开失败 |
| `minecraft.container_closed` | 409 | 窗口在动作过程中被关掉 |
| `minecraft.container_close_failed` | 500 | 动作做完了但关不上（结果一并如实上报） |
| `minecraft.container_transfer_unconfirmed` | 500 | transfer resolve 了但重读的真实状态不符 |
| `minecraft.item_not_found` / `item_changed` / `item_count_insufficient` / `destination_occupied` / `slot_invalid` | 404/409/409/409/422 | 复用 Phase 4D 的槽位/物品语义 |

## 九、调试面（WebUI / HTTP）

| 路由 | 说明 |
| --- | --- |
| `POST /api/v1/minecraft/container_inspect` | 开发者入口 `{x,y,z}`：SAFE，同步返回容器快照（仍然独占） |
| `POST /api/v1/minecraft/container_transfer` | 开发者入口：先校验参数，再过确认门（第一次 409） |

WebUI 的 **Container** 面板：INSPECT 显示 `type / position / size / slots`（非空格子表，
点一行即可把它填成 Transfer 的 source）；Transfer 区填
`direction / container slot / inventory slot / item / count` 后按 TRANSFER ——
按钮只能**发起**确认（`confirmation_required`），真正的确认必须由用户在对话里做出。

## 十、配置

```yaml
minecraft:
  action:
    container:
      timeout: 30          # 单次容器动作最长秒数（5~120）
      max_distance: 5      # 最大交互距离（格，眼睛 → 容器方块中心）
```

`allow_medium` 默认仍是 **false**：打开后 `container_transfer` 仍然要用户确认。
没有为容器单独拆能力开关（不细化到"只许读不许写"这种粒度）。

## 十一、验证

* **Node 单测**（假窗口，§四十一：真的模拟 openContainer / window.slots / inventoryStart /
  closeWindow / transfer / bot.inventory.slots，而不是把 transfer 换成空函数）：
  * `minecraft_runtime/test/container.test.js`（**85 checks**）：A open success / B open failure /
    C inspect read / D close after success / E close after failure / F close after timeout
    （晚到的窗口立刻关掉）/ G close after cancellation / H disconnect while window open /
    I cleanup exactly once / J close 抛错如实上报 / K chest / L barrel / M trapped chest reject /
    N double chest reject / O unsupported block reject + 距离 / 未加载 / 参数校验 / 窗口结构推导；
  * `minecraft_runtime/test/container_transfer.test.js`（**88 checks**）：P withdraw / Q deposit /
    R exact source slot / S exact destination slot / T wrong item / U insufficient count /
    V destination occupied / W legal stack merge / X post-transfer reread /
    Y transfer unconfirmed / Z cleanup after error / AA cancellation race / AB timeout race /
    AC terminal exactly once。
* **Python**：`tests/test_minecraft_container_tool.py`（12）、
  `tests/test_minecraft_container_transfer_tool.py`（19，含确认门全流程、参数指纹、过期、
  不可信玩家、非用户回合、错误码 → 结构化 detail、activity 只写事实）、
  `tests/test_web_api_minecraft.py`（+4：两个端点的校验 / 只读 / 不可自授权 / 错误码映射一致）。
* **WebUI**：页面测试 +9（INSPECT / 参数本地拦截 / 槽位表填 source / TRANSFER 八字段 /
  本地校验 / 被确认门拒绝 / 未 INSPECT 的空态）。
* **flying-squid E2E**：这台假服务器**真的**会发 chest GUI 窗口 →
  inspect 的 `open → read → close` 被真实走了一遍（200 + type=chest + size=27 + 空 slots +
  第二次仍能打开证明没漏窗口）；transfer 只验证拒绝路径（箱子里没有东西 → `item.not_found`、
  参数校验、`action.busy` 独占）。STOP 段明确 **SKIPPED**（毫秒级，没有可取消窗口）。
* **真实服务器 smoke**（§四十二-§四十五）：
  `SMOKE_CONTAINER_TARGET="x,y,z"` 可用操作者自己的容器；否则就地造临时箱子
  （只覆盖 air/water，结束后按原方块名精确还原）。证据：
  inspect（open 成功 / 类型 / size=27 / slots 与真实容器一致 / 再次 inspect 成功 = close 成功）、
  withdraw（RUNNING + action_id / completed / container_before+after / inventory_before+after /
  真实变化量 / 重读容器与背包）、deposit（反向再走一遍）、最后
  **容器内容与背包布局都逐项恢复**、window 已关闭、ensureIdle、disconnect → `REAL SERVER: PASS`。
  装填夹具物品时会先试 1.16 的 `/replaceitem`，不行再试 1.17+ 的 `/item replace`
  （脚本不假定服务器版本；两种都不行就 SKIPPED，绝不伪造）。
* smoke 另外在方块交互之前把罐头 `/tp` 到**干燥实地**：水里/水边放方块会被服务器拒绝
  （水会流回目标格），这一步让 dig / place / container 三段都可复现。

## 十二、本阶段不做（4F 及以后）

半自动或全自动的容器操作：双箱、潜影盒、熔炉/漏斗/发射器、箱子对箱子搬运、批量整理 / 自动排序、
`deposit-all` / `withdraw-all`、自动补货、合成（craft / smelt）、交易（trade）、
掉落物拾取、容器内容进入世界模型（WorldPerception 现在**没有**容器内容感知 —— 本阶段也不假装有，
证据只用 Action result + 容器重读 + 背包重读）。
