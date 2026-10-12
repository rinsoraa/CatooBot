# CatooBot Minecraft Phase 4K — Resource Targeting + 单资源真实闭环

> 两层目标：
> **① 产品能力**：新增一个只读工具 `minecraft_find_blocks`（在当前已加载的范围里找指定方块的位置）；
> **② 验收能力**：用**已有的原子能力**跑通一次真实资源链 —— 但它必须由 Agent/Smoke/测试编排，
> **不包成一个 `minecraft_gather_resource` 黑盒**。

## 一、完整链路（每一步都是现有原子工具，各自保持自己的风险/确认边界）

```text
minecraft_find_blocks          只定位（SAFE 只读）
        ↓ candidate positions
minecraft_dig_capability       当前主手挖不挖得动、大概多久（SAFE 只读）
        ↓ can_dig / dig_time_ms
minecraft_equip                必要时**明确**换一把工具（MEDIUM + 确认）
        ↓
minecraft_move_to              走过去（LOW；4H.1 起 completed ⇒ GoalNear 已复核过）
        ↓
minecraft_dig_capability       到了之后再查一次（距离变了，结论也会变）
        ↓
minecraft_dig                  挖掉那一个方块（MEDIUM + 确认）
        ↓ block_before → block_after
minecraft_dropped_items        找**这次挖出来**的 Item Entity
        ↓
minecraft_pickup_item          一次只捡这一个实体（MEDIUM + 确认）
        ↓ playerCollect + entity gone + inventory increased
minecraft_inventory            重新读背包
```

**没有新增任何复合工具**：不建 `minecraft_gather_resource` / `collect_resource` / `auto_mine`
（§二/§四十三），也不做 `while inventory < target` 的循环。

## 二、`minecraft_find_blocks` 语义（SAFE 只读）

```json
{"block_names": ["minecraft:iron_ore", "minecraft:deepslate_iron_ore"],
 "max_distance": 16, "max_results": 8}
```

| 项 | 规则 |
| --- | --- |
| `block_names` | 必填，1~8 个方块名；`iron_ore` / `minecraft:iron_ore` / `IRON_ORE` 都规范成裸名并去重；**不支持 `#ores` 这种 tag**（§五） |
| `max_distance` | 可选，默认 16，**1~32**（越界直接拒：不允许"扫全世界"） |
| `max_results` | 可选，默认 8，**1~16** |
| 起点 | 永远是**罐头当前所在位置**（不接受坐标起点，避免任意远程扫描，§六） |
| 事实来源 | Mineflayer 的 `bot.findBlocks({point, matching: [blockId…], maxDistance, count})` —— 优先按 **block id 数组**匹配（§七/§八），绝不自己遍历世界 |
| 输出 | `{ok, query{…}, matches:[{block:{name}, position:{x,y,z}, distance:{goal_near, raw}}], truncated}` |
| 距离口径 | 与 `minecraft_dig_capability`**共用同一套算法**（§十：不新造第三种距离）：`goal_near` = 罐头占的方块格 → 目标方块格；`raw` = 眼睛 → 方块中心的浮点距离 |
| 排序 | `goal_near → raw → x → y → z`（§十一：同世界状态下顺序稳定可复现） |
| `truncated` | "被条数上限截断"，**不是失败**（§十二） |
| 没有结果 | 正常 `{ok: true, matches: [], truncated: false}` —— **不是** 404，也不是 `block_not_found`（§十三） |
| 名字不认识 | `minecraft.block_name_unknown`（**422**）并点出是哪个名字 —— 绝不返回空结果，否则模型分不清"不存在"和"附近没有"（§二十二） |
| 查不了 | `minecraft.block_query_unavailable`（运行时给不出这个查询能力时，绝不假装"附近没有"） |
| 不做的事 | 不移动、不装备、不挖、不拾取、不改世界/背包；**不返回推荐/最佳/最优**（§十五/§十七：最近 ≠ 最适合挖）；不写 WorldPerception 的缓存（on-demand query，§十八） |

策略：`risk = SAFE`、`exclusive = false`、需要在线、不需要确认、不需要明确意图、
USER / INITIATIVE / BACKGROUND / SYSTEM 都能调用 —— 但它只会返回事实，
绝不因为"发现了 iron_ore"就自动 move/equip/dig/pickup（§五十）。

## 三、配置（§四十七）

```yaml
minecraft:
  action:
    find_blocks:
      max_distance: 16    # 默认搜索半径（格，1~32）
      max_results: 8      # 默认最多返回几条（1~16）
```

只有这两个键（硬上限 32 / 16 在 runtime 与 Service 两侧都拦）；
没有 `resource_gathering.*` 这种一整套配置。

## 四、真机证据（§二十九 顺序 · `REAL SERVER: PASS`）

```text
✓ find_blocks = PASS（HTTP 200，候选顺序 oak_log → stone → dirt，命中 4 个 **天然** oak_log）
  4K 资源链目标：oak_log @ (332,70,220)，goal_near=11 / raw=10.85
✓ dig capability = PASS（can_dig=false / dig_time_ms=null / reason=too_far）   ← 11 格：超过挖掘距离
  → 链路按顺序先 move_to（不是硬闯）
✓ move_to = PASS（minecraft.action.completed，distance_to_target=1.41）
  4K：到目标后 capability = can_dig=true / dig_time_ms=3000 / raw=1.82      ← 到地方再查一次
✓ dig = PASS（oak_log → air）
✓ world changed = PASS（那个位置现在是 air）
✓ dropped item = PASS（#105357 oak_log×1）                                   ← 只认这次挖出来的
✓ pickup RUNNING = PASS
✓ pickup = PASS / playerCollect = PASS（inventory_before=0 → inventory_after=1）
✓ entity gone = PASS（dropped_items 里不再有它）
✓ inventory reread = PASS（oak_log 现在 1 个）
✓ inventory increased = PASS（pickup 报 1，重读=1）
✓ restore = PASS（原有物品一个没丢；夹具方块与主手都还原）
✓ ensureIdle = PASS（资源链结束后 runtime 回到 IDLE）
REAL SERVER: PASS
```

* 这次用的是**世界里天然存在的橡木**（`find_blocks` 真搜出来的，不是硬编码坐标）；
  世界里没有目标时 smoke 会**明确 SKIPPED**，只有显式 `SMOKE_RESOURCE_FIXTURE=1`
  才就地放一个天然兼容的方块（先存原方块、结束后还原）—— 默认不造资源（§三十六）。
* 支持 `SMOKE_RESOURCE_BLOCK="minecraft:oak_log"` 指定目标；**不指定就自动搜索**
  （候选顺序：oak_log → stone → dirt，§三十五/§二十五）。
* 识别"这次挖出来的 Item Entity"用的是：**挖之前先记下已有的掉落物 id** →
  挖完只接受"新出现的 + 在挖点 4 格内"的那一个（§三十一：绝不只挑"当前最近的 oak_log"）。

## 五、验证

* **Node** `minecraft_runtime/test/find_blocks.test.js`（**41 checks · A–J**）：
  注册表属性与默认值、参数规范化与边界（空数组/非数组/空白/9 个名字/小数/越界）、
  名字→**block id 数组**、起点是罐头当前位置（floored）、`count = max_results + 1`（用来判断截断）、
  方块坐标、两种距离口径、未知方块名 422（带 `unknown` 列表）、运行时给不出查询 →
  `block.query_unavailable`、截断与空结果、**排序稳定可复现**（`goal_near → raw → x → y → z`）、
  输出只有三个字段（无推荐/无 raw Block）、**连续 5 次查询零副作用**、
  以及前台 `move_to` 跑着时照样能查（同时验证独占动作仍被 `action.busy` 拒绝）。
* **Python** `tests/test_minecraft_find_blocks_tool.py`（**15 个用例 · A–K**）：
  schema（`block_names` 1~8、两个可选整数、**没有坐标起点**）、非法参数在 schema 层被拒、
  规范化与去重、省略上限时用**配置默认值**（走真实 Service + FakeRuntime，断言发给 runtime 的 body）、
  未知方块名、离线、**SAFE 四个回合都能用且不产生确认**、非独占、输出投影与排序透传、
  不泄露 raw 数据、空结果/截断如实透传、Service 边界与错误映射。
* **WebUI** vitest +7（页面 91 个）：Find Blocks 面板（`FIND` + 候选表 + 两列距离）+
  **USE AS TARGET 只把坐标填进 Dig / Capability 表单**（专门断言它没有再发
  move / equip / dig / pickup 请求）、空结果如实显示、未知名字如实报错、缺名字本地拦截、
  以及**没有 AUTO GATHER 按钮**（§四十五）。
* **flying-squid E2E**：真实假服务器上 `find_blocks(['minecraft:grass_block'])` 命中
  **8 个真实 grass_block**；语义投影形状与不泄露字段、未知名字 422、范围内没有 → 正常空结果、
  上限拒绝、`max_results=1`、以及 `move_to` 跑着时只读查询照样能执行。
* **真机 smoke**：见上（`REAL SERVER: PASS`）。

## 六、边界（本阶段明确不做）

自动挖矿循环（`while inventory < target`）、`resource_count` / `max_mining_blocks` / `auto_refill`、
复合工具（gather/collect/auto_mine）、方块 tag 解析、工具推荐与最佳工具排序、
task-level confirmation aggregation（§二十七：链上每一个世界修改动作仍然各用各的确认门）、
ResourceGatherRuntime 子动作调度/回滚/重试/持久化（那是 5A）。
`minecraft_dig` / `place` / `equip` / `pickup` / `craft` / `inventory` 本阶段**都没有改**。
