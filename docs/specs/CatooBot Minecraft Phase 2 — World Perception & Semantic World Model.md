# CatooBot Minecraft Phase 2
## World Perception & Semantic World Model

### 阶段目标

在已完成的 Minecraft Phase 1 连接层之上，为罐头增加 Minecraft 世界感知能力。

本阶段唯一目标：

> 罐头进入 Minecraft 后，CatooBot 能够以罐头自身为中心获取周围 Minecraft 世界的结构化状态，并将其转换成 LLM 可稳定理解的语义世界模型。

本阶段结束后，系统必须能够回答：

- 我在哪里？
- 我所在的维度是什么？
- 现在是什么环境？
- 附近有哪些方块/地形？
- 附近有哪些玩家？
- 附近有哪些实体？
- 哪些位置值得关注？
- 某个对象相对于罐头在哪个方向、距离多远？

### 本阶段明确禁止

不要实现：

- Minecraft 自动移动
- Pathfinder
- 自动寻路
- move_to
- 跟随玩家
- 自动挖矿
- 自动建造
- 自动战斗
- craft
- place
- mine
- Agent Loop
- 自主任务
- LLM 自动决策 Minecraft 行动

本阶段只实现“眼睛”，不实现“手”。

---

# 一、架构

保持 Phase 1 的边界：

CatooBot Core
↓
MinecraftService
↓
HTTP
↓
minecraft_runtime
↓
Mineflayer
↓
Minecraft

在此之上增加：

Minecraft Runtime
↓
Raw World Snapshot
↓
CatooBot World Perception
↓
Semantic World Model

不要让 Mineflayer 原始对象直接进入 LLM。

---

# 二、Raw World Snapshot

在 runtime 中增加：

GET /minecraft/world/snapshot

至少提供：

## Self

- position x/y/z
- yaw
- pitch
- dimension
- health
- food
- game mode
- current held item

## Players

每个附近玩家至少提供：

- username
- position
- distance
- bearing
- vertical offset
- relative direction

## Entities

每个附近实体至少提供：

- type
- position
- distance
- bearing
- relative direction

## Blocks

提供以罐头为中心的局部方块数据。

不要直接将大范围所有空气方块输出给上层。

至少需要支持：

- block name
- relative x/y/z
- world x/y/z
- distance
- relative direction

## Environment

至少：

- biome
- time of day
- weather
- light level
- dimension

---

# 三、空间模型

所有空间对象必须同时保留：

世界坐标：

x/y/z

以及相对坐标：

dx/dy/dz

以及：

distance
bearing
relative_direction

例如：

{
  "distance": 6.4,
  "bearing": 42,
  "relative_direction": "front_right"
}

其中 relative_direction 必须由系统计算，而不是依赖 LLM 推断。

至少支持：

- front
- front_left
- front_right
- left
- right
- back
- back_left
- back_right
- above
- below

---

# 四、扫描范围

不要一次把整个 Chunk 数据直接交给 LLM。

采用分层感知：

## Near

约 8～12 blocks。

特点：

- 高频
- 高详细度

## Local

约 32 blocks。

特点：

- 中频
- 结构化压缩

## Extended

约 64～128 blocks。

特点：

- 低频
- 主要保存地形、兴趣点、结构等摘要

不要将大量 air 方块作为无意义数据发送到模型。

---

# 五、Semantic World Model

在 Python / CatooBot 一侧增加 World Perception / Semantic Mapper。

Raw Snapshot：

“事实数据”

Semantic World Model：

“面向 Agent 的世界理解数据”

示例：

{
  "self": {
    "location": "plains",
    "dimension": "overworld"
  },

  "terrain": [
    {
      "type": "oak_forest",
      "direction": "north",
      "distance": 8
    },
    {
      "type": "river",
      "direction": "west",
      "distance": 10
    }
  ],

  "players": [
    {
      "name": "空凛",
      "direction": "front_right",
      "distance": 6
    }
  ],

  "entities": [
    {
      "type": "cow",
      "count": 3,
      "direction": "west"
    }
  ],

  "points_of_interest": [
    {
      "type": "crafting_table",
      "direction": "front",
      "distance": 4
    }
  ]
}

不要让 Semantic World Model 成为唯一数据源。

Raw Snapshot 必须保留。

---

# 六、世界状态缓存

加入 World State Cache。

目的：

避免每次聊天都重新扫描整个世界。

至少保存：

- last snapshot
- last semantic model
- timestamp
- player observations
- entity observations
- points of interest

不同范围允许不同刷新周期。

建议：

Near：
约 0.5～1 秒

Local：
约 2～5 秒

Extended：
约 10～30 秒

最终周期必须可配置。

---

# 七、事件化

加入感知变化事件，但避免每一个方块变化都直接触发 CatooBot。

至少支持语义级变化：

minecraft.world.changed
minecraft.player.nearby
minecraft.player.left_area
minecraft.entity.discovered
minecraft.entity.left_area
minecraft.poi.discovered

事件必须去抖 / 合并。

例如：

不要因为附近 50 个方块变化发送 50 次事件。

应该聚合为：

minecraft.world.changed

并附带变化摘要。

---

# 八、WebUI

Phase 2 可以增加一个简单的 World Debug 页面。

显示：

- 当前坐标
- 当前维度
- 当前生物群系
- 附近玩家
- 附近实体
- 当前地形摘要
- 当前 POI
- Raw Snapshot
- Semantic World Model

目标是方便开发调试。

不需要做游戏地图。

---

# 九、CatooBot 集成

本阶段暂时不要让 LLM 自主决定 Minecraft 行动。

可以增加只读能力：

get_minecraft_world_state()

或者：

get_minecraft_world_summary()

用于普通对话上下文。

例如用户询问：

“罐头你附近有什么？”

CatooBot 可以读取 Semantic World Model 后回答。

但用户说：

“罐头过来。”

本阶段不得执行移动动作。

必须明确返回：

Minecraft Action 尚未在 Phase 2 实现。

---

# 十、验收标准

### Test 1

进入 Minecraft 后：

GET /minecraft/world/snapshot

能够返回有效数据。

### Test 2

位置正确。

与 Minecraft 中实际坐标误差必须在合理范围。

### Test 3

能够发现附近玩家。

玩家移动后距离/方向正确更新。

### Test 4

能够发现附近实体。

例如牛、鸡、村民等。

### Test 5

能够识别基础方块。

例如：

- grass_block
- dirt
- stone
- oak_log
- water

### Test 6

Relative Direction 正确。

例如玩家位于罐头右前方，系统必须输出：

front_right

而不是让 LLM 自己根据坐标判断。

### Test 7

Semantic World Model 与 Raw Snapshot 一致。

不能出现语义层凭空制造不存在的对象。

### Test 8

大量空气方块不会导致上下文无限膨胀。

### Test 9

玩家移动 / 实体移动 / 环境变化不会产生异常事件风暴。

### Test 10

断开 Minecraft 后：

World Snapshot 必须正确失效。

不得保留假在线状态。

---

# 十一、完成定义

Phase 2 COMPLETE 的定义：

> 罐头可以进入 Minecraft。
>
> 系统能够持续获得罐头周围的结构化 Minecraft 状态。
>
> 系统能够将这些状态转换成稳定、可解释、面向 Agent 的 Semantic World Model。
>
> CatooBot 可以读取这个世界模型，并在聊天中准确描述罐头当前所处环境。
>
> 但是本阶段不允许罐头主动改变 Minecraft 世界。

完成后输出：

1. 修改文件清单
2. Raw Snapshot schema
3. Semantic World Model schema
4. 感知架构
5. 更新周期设计
6. 事件设计
7. WebUI Debug 页面
8. 单元测试
9. 集成测试
10. 性能/上下文体积评估
11. 已知限制
12. Phase 3 建议

不要提前实现 Phase 3 的行动能力。