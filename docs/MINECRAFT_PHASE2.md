# CatooBot Minecraft Phase 2 · World Perception & Semantic World Model

> Phase 目标（任务书）：罐头进入 Minecraft 后，CatooBot 以罐头为中心获取周围世界的
> 结构化状态，并转换成 LLM 可稳定理解的 Semantic World Model。**只实现「眼睛」，
> 不实现「手」**——移动/挖掘/建造/寻路/Agent 一律属于 Phase 3+。

Phase 1 架构（连接层）不变，在其上新增感知链路：

```
Minecraft Runtime（Node）
  ↓ GET /minecraft/world/snapshot?layers=near,local,extended   ← Raw World Snapshot
WorldPerception（app/integrations/minecraft/world.py）
  ├─ Raw* pydantic 模型（事实数据，原样保留）
  ├─ WorldStateCache（分层缓存，不同层不同刷新周期；断开整体作废）
  ├─ build_semantic_model（纯函数：raw → Semantic World Model）
  └─ 差异检测 → 语义级事件（去抖合并）
  ↓ service._notify_listeners（与 Bridge 事件同一订阅机制）
minecraft_world 只读工具（对话上下文）/ WebUI World Debug / Phase 3+ 行动层
```

## 1. Raw Snapshot schema（runtime → CatooBot）

`GET /minecraft/world/snapshot?layers=near,local,extended`（layers 子集可选，默认全部；
未在线返回 `{ok, online:false}`）。自顶部字段：

| 段 | 内容 |
|---|---|
| `self` | position{x,y,z}、yaw、pitch、dimension、health、food、game_mode、held_item |
| `players[]` | username、pos、rel{dx,dy,dz}、distance、bearing、relative_direction、compass（≤20，按距离） |
| `entities[]` | type、kind、pos、rel、distance、bearing、relative_direction、compass（≤40，按距离） |
| `environment` | biome、time_of_day_ticks、time_phase(day/sunset/night/sunrise)、weather(clear/rain/thunder)、light、dimension |
| `blocks.near` | radius 6，柱面表层扫描（每柱最高非空气方块），≤220 列，每列含 name/rel/pos/distance/bearing/relative_direction/compass |
| `blocks.local` | radius 32、step 8 粗采样柱面，≤120 列 |
| `blocks.extended` | radius 96，8 方位 × 48/96 格 = 16 个采样点（带 biome） |
| `blocks.interesting` | 附近「值得注意」方块（功能方块/光源/传送门/矿石…），立体 ±3 格 × 半径 10，≤40 |

**空气不进协议**：柱面扫描只取最高非空气方块 + 兴趣方块；原版/洞穴空气全部剔除。

## 2. 空间模型（§三）

每个空间对象同时保留世界坐标（`pos`）与相对量（`rel.dx/dy/dz`、`distance`、
`bearing`、`relative_direction`、`compass`）。全部由 **runtime 侧数学**计算，LLM 不做推断：

- `bearing`：相对罐头朝向的方位角，0=正前，正值=右（[-180,180]）；
- `relative_direction`：bearing 8 扇区（front/front_left/front_right/left/right/back/back_left/
  back_right）+ 贴身垂直（水平距离 <2 且 |dy|≥1.5 → above/below），共 10 种；
- `compass`：世界 8 方位（north=-Z，east=+X，顺时针）。

E2E 用「从 rel+yaw 重算」做数学一致性断言（Test 6 的机器执行版）。

## 3. Semantic World Model schema（Python → 对话/Agent）

纯函数 `build_semantic_model(raw)`，**不从无中发明对象**：

```json
{
  "captured_at": 1730000000.0,
  "self": {"location": "plains", "dimension": "overworld", "position": {...},
           "health": 20, "food": 20, "game_mode": "survival", "held_item": null, "yaw": 90},
  "environment": {"biome": "plains", "time_phase": "day", "weather": "clear", "light": 15, ...},
  "terrain":     [{"type": "forest", "direction": "north", "distance": 8.0, "samples": 12}],
  "players":     [{"name": "RinsoraNeko", "direction": "front_right", "distance": 6.0, "compass": "west"}],
  "entities":    [{"type": "cow", "count": 3, "direction": "west", "distance": 7.0}],
  "points_of_interest": [{"type": "crafting_table", "direction": "front", "distance": 4.0, "pos": {...}}]
}
```

- `terrain`：local/extended/near 柱面按「地形类别 × 罗盘方位」聚合（类别映射：
  grassland/forest/water/sand/stone/snow/dirt/farmland/built），按样本数排序取前 8；
- `entities`：按类型聚合计数 + 主方位 + 最近距离，按最近排序取前 12；
- `points_of_interest`：interesting 方块去重，按距离取前 10。

## 4. 更新周期（§六，可配置）

| 层 | 默认周期 | 配置项 |
|---|---|---|
| Near（~6 格） | 1.0s | `minecraft.near_interval_seconds` |
| Local（~32 格） | 4.0s | `minecraft.local_interval_seconds` |
| Extended（~96 格） | 20.0s | `minecraft.extended_interval_seconds` |

`WorldPerception` 维护每层到期时间，一次循环按「最近的到期层」拉取
（runtime 端点按 layers 参数按需计算，Local/Extended 不必要时不出块数据）。
缓存：`WorldStateCache`（last raw + 每层 fetched_at + online 标志）；
**`minecraft.disconnected` 事件 → 整体失效**（Test 10），`world_view().available=false`。

## 5. 事件设计（§七）

语义级事件，按类型去抖（`world_event_cooldown_seconds`，默认 5s）：

| 事件 | 触发 | data |
|---|---|---|
| `minecraft.world.changed` | 近层签名变化 ≥ `world_change_block_threshold`（默认 10）个方块，或环境相位/天气/群系变化；**聚合为一条** | `{changed_blocks, environment_changed, environment}` |
| `minecraft.player.nearby` | 新玩家进入感知范围 | `{usernames, players:[{name,direction,distance}]}` |
| `minecraft.player.left_area` | 玩家离开 | `{usernames}` |
| `minecraft.entity.discovered` | 新实体类型出现 | `{types: {type: count}}` |
| `minecraft.entity.left_area` | 实体类型消失 | `{types}` |
| `minecraft.poi.discovered` | 新 POI 方块类型出现 | `{types}` |

上线首帧只建基线不发事件（避免进世界瞬间的风暴）。事件经
`MinecraftService._dispatch_world_event` 走与 Bridge 事件相同的订阅者机制
（`add_listener`），当前消费者：日志 + metrics；QQ 插件不转发（不骚扰）。

## 6. CatooBot 集成（§九，只读）

- **只读工具 `minecraft_world`**（ToolRuntime，category=information，risk=low）：
  返回语义模型的中文 summary + 结构化 data，供普通对话上下文使用——
  「罐头你附近有什么？」可以直接回答。工具描述与 `when_not_to_use` 明确写：
  移动/挖掘/建造等行动能力**尚未实现**（Phase 3+）。
- **WebUI**：`GET /api/v1/minecraft/world`（恒 200；未启用/未在线时
  `available:false`）+ Minecraft 页「World Debug（只读感知）」区
  （环境事实 / 附近玩家 / 附近生物 / 兴趣点 / 地形摘要 / Raw+语义 JSON 折叠），
  仅 ONLINE 时渲染，3s 轮询。
- 明确不做：罐头对「过来/帮我挖」类请求**没有**任何动作端点可调
  （runtime 只有 status/snapshot/chat/disconnect/connect 六个端点）。

## 7. 测试记录（2026-10-05）

| 门禁 | 结果 |
|---|---|
| `pytest tests`（全量） | 通过（含 `test_minecraft_world.py` 10 个新用例：解析/聚合/无发明/缓存失效/去抖/阈值） |
| Node E2E（flying-squid + 观察者） | snapshot：online、self/env 有效、观察者被发现、169 柱非空气、**方向数学重算一致**、层大小有界 |
| ruff / mypy / vitest / vue-tsc / playwright | 全绿 |

## 8. 已知限制

1. **Mod 服务器**：罐头是原版协议客户端，NeoForge/Forge 强制握手的服务器可能拒绝进入
   （Phase 1 已知限制的延续）；感知只对已进入的世界有效。
2. `light`/`biome` 依赖服务端方块光照与群系数据：部分服务端实现（如 flying-squid）
   不下发光照，字段为 `null`；真实服务器正常。
3. Near 层柱面扫描只取**地表最高块**——树冠会遮住树下地形（语义层把 leaves/log
   归为 forest，可接受）；地下/洞穴感知未做（Phase 3 可加洞穴扫描）。
4. Extended 只采样 16 个点：是「地形摘要」不是精确地图（任务书允许）。
5. 实体发现依赖实体已在 bot 视距内注册；远处实体由 extended 之外的层不覆盖。

## 9. Phase 3 建议

1. **动作层**：runtime 增加 `move_to / dig / place / look / follow` 端点 + CatooBot
   侧确认门与额度控制（「手」）；工具从只读扩展为带风险分级的动作工具。
2. **导航**：mineflayer-pathfinder 进 runtime，暴露 `goto(x,y,z)` 与寻路可行性查询。
3. **对话行动闭环**：把 Semantic World Model 注入对话上下文，允许 LLM 通过
   计划-确认-执行的小闭环完成「跟我来/帮我放个火把」类请求。
4. **洞穴/地下感知**、实体意图（entity metadata：村民职业、苦力怕引信状态）。
5. **记忆接入**：把 POI 发现写进长期记忆（「空凛家在工作台西边 20 格」）。
