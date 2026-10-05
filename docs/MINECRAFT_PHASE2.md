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

## 0. 修改文件清单

**新增**

| 文件 | 说明 |
|---|---|
| `app/integrations/minecraft/world.py` | 感知核心：Raw* 模型 / 语义映射器 / 分层缓存 / WorldPerception（去抖事件） |
| `app/tools/builtins/minecraft_world.py` | 只读工具 `minecraft_world`（对话上下文用世界摘要） |
| `tests/test_minecraft_world.py` | 单元测试 10 个（解析/聚合/无发明/缓存失效/事件去抖/阈值/离线退避） |
| `docs/MINECRAFT_PHASE2.md` | 本文档 |

**修改**

| 文件 | 变更 |
|---|---|
| `minecraft_runtime/runtime.js` | 空间数学（bearing/relative_direction/compass）、柱面表层扫描、POI 判定、`GET /minecraft/world/snapshot` |
| `minecraft_runtime/test/e2e.js` | snapshot 有效性 + 方向数学重算一致断言 + 各层字节数输出 |
| `app/integrations/minecraft/events.py` | `MinecraftWorldEvent` / `WORLD_EVENT_NAMES` / `parse_world_event` |
| `app/integrations/minecraft/runtime_client.py` | `world_snapshot(layers)` |
| `app/integrations/minecraft/service.py` | 感知生命周期（start/stop）、`_perception_loop`、`_dispatch_world_event`、`world_view()`、断开即时失效 |
| `app/integrations/minecraft/__init__.py` | 导出世界事件类型 |
| `app/tools/builtins/__init__.py`、`app/tools/runtime.py` | 注册 `minecraft_world` 工具 |
| `app/character/runtime.py` | `minecraft` 引用 + 工具上下文注入 `minecraft_world` |
| `app/core/bot.py` | 构造时把 MinecraftService 交给角色运行时 |
| `app/web/api/minecraft.py` | `GET /api/v1/minecraft/world`（World Debug，恒 200） |
| `app/config/settings.py` | `MinecraftConfig` +6 个感知配置项 |
| `config/config.example.yaml`、`config/config.yaml` | `minecraft:` 段补感知字段 |
| `tests/test_minecraft_service.py` | FakeRuntime +snapshot 端点；感知集成测试 2 个 |
| `tests/test_web_api_minecraft.py` | World Debug 端点测试 2 个 |
| `tests/test_web_routes.py` | 路由快照 +1 条 |
| `webui/src/types/minecraft.ts`、`api/minecraft.ts`、`pages/Minecraft.vue`、`pages/__tests__/minecraft.spec.ts`、`dist/*` | World Debug 区（环境/玩家/生物/POI/地形/JSON 折叠）+ 测试 + 重建 |
| `docs/WEBUI_API_CONTRACT.md`、`WEBUI_V1_ROUTE_MATRIX.md`、`README.md` | 契约 §7.5 +world、路由矩阵 210 条、索引 |

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
| `minecraft.world.changed` | 近层签名**增/改/删**（并集比较）≥ `world_change_block_threshold`（默认 10）个方块，或环境**语义签名**（biome/time_phase/weather/dimension；刻意不含 `time_of_day_ticks`/`light`）变化；**聚合为一条** | `{changed_blocks, environment_changed, environment(签名)}` |
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

**单元测试**（`tests/test_minecraft_world.py`，10 个）：

- 载荷解析：离线载荷/非 dict/坏结构分别得到正确结果或 ValueError；
- terrain 类别映射（grass_block→grassland、oak_log/leaves→forest、water→water、功能方块不归地形）；
- 语义聚合**可追溯、不发明**：terrain/POI 逐条来自 raw；entities 按类型计数 + 主方位 + 最近距离；
- 缓存断开整体作废（`online=False`、raw=None、age=None）；
- `service.receive_event(disconnected)` 令感知缓存立即失效（Test 10 的 Python 侧）；
- 首帧只建基线不发事件；player.nearby 冷却期内不重复；world.changed 低于阈值不发、超阈值聚合成一条；离线 snapshot 作废缓存 + 下一拍退避（不空转）。

**集成测试**（真实 HTTP，不走 mock）：

- `test_start_runs_perception_and_world_view`：`service.start()` 拉起感知循环 → spawned 事件 → 真 HTTP 轮询 FakeRuntime 的 snapshot → `world_view()` 读出语义模型 + raw 原文 + 层龄；disconnected 后立即 `available=False`；
- `test_perception_disabled_keeps_world_view_unavailable`：未开感知时如实报告原因；
- `test_world_endpoint_reports_primed_semantic_model`：`GET /api/v1/minecraft/world` 返回语义模型/raw/层龄；未启用时恒 200 + `available:false`。

**Node E2E**（flying-squid 真协议服务器 + 观察者玩家，3 轮循环中验证）：

- snapshot `online=true`、self 的 dimension 与 `/minecraft/status` 一致、time_phase/weather 合法；
- 观察者玩家出现在 `players` 且带 distance/bearing/relative_direction；
- near 层 169 列全部非空气、relative_direction 取值合法；
- **方向数学一致性**：从 `rel` 与 `self.yaw` 在测试侧独立重算 bearing/扇区，必须与 runtime 输出逐条一致（Test 6 的机器执行）；
- 层大小有界（≤220 列）；各层字节数随每轮输出（见 §8）。

| 门禁 | 结果 |
|---|---|
| `pytest tests`（全量） | 通过（1816+，含上列新增） |
| Node E2E | ALL CHECKS PASSED |
| ruff / ruff format / mypy app / vue-tsc / vitest / playwright E2E | 全绿 |

## 8. 性能 / 上下文体积评估（实测）

数据来自 E2E 的真实 snapshot（flying-squid + 观察者玩家，near=169 柱）：

| 对象 | 实测 | 说明 |
|---|---|---|
| Raw Snapshot（全层 near+local+extended+interesting） | ≈ **45–51 KB** | 只走本机 HTTP 与 Python 内存，**永不进 LLM 上下文** |
| Raw Snapshot（仅 near） | ≈ **29–33 KB** | 高频层；Local/Extended 按需（默认 4s / 20s） |
| Semantic World Model | ≈ **1.1 KB**（JSON；紧凑 ≈1.0 KB） | raw→语义压缩比约 **44×** |
| 工具 summary（LLM 真正看到的） | **249 B / 167 字** | 例：`罐头在 plains（overworld），坐标 (22.0, 60.0, 21.0)，day，附近玩家：Tester（back_left，5.7格），地形：grassland（south_east）、…` |

**上下文预算结论**：即使把语义模型整个 JSON 注入，也只有约 1 KB（≈0.3–0.4k token）；
工具默认只把 summary（≈0.1k token）给模型，结构化 data 留在工具结果里。
无论感知频率多高，**进入上下文的体积与扫描半径无关**（半径只影响本机 CPU/内存），
这是"空气不进协议 + 表层柱面 + 语义聚合"设计的直接收益。

**CPU/网络开销**：Near 1s/次（≈29 KB 本机 HTTP）、Local 4s、Extended 20s；
柱面扫描单次 169 列 ×（y 向上 8 + 向下 8）≈ 2.7k `blockAt` 查询，E2E 实测单次 snapshot
（含全部三层）在毫秒级完成，对事件循环无感（感知跑在 CatooBot 的 asyncio 里，
单次请求走本机回环，无阻塞调用）。

## 9. 已知限制

1. **Mod 服务器**：罐头是原版协议客户端，NeoForge/Forge 强制握手的服务器可能拒绝进入
   （Phase 1 已知限制的延续）；感知只对已进入的世界有效。
2. `light`/`biome` 依赖服务端方块光照与群系数据：部分服务端实现（如 flying-squid）
   不下发光照，字段为 `null`；真实服务器正常。
3. Near 层柱面扫描只取**地表最高块**——树冠会遮住树下地形（语义层把 leaves/log
   归为 forest，可接受）；地下/洞穴感知未做（Phase 3 可加洞穴扫描）。
4. Extended 只采样 16 个点：是「地形摘要」不是精确地图（任务书允许）。
5. 实体发现依赖实体已在 bot 视距内注册；远处实体由 extended 之外的层不覆盖。

## 10. Phase 3 建议

0. **（前置）重新设计 movement-aware world diff**——见 §11.3：当前 near signature 以世界坐标为键，
   移动能力引入后「窗口位移」会被误报为世界变化，必须先设计对齐/分类方案，再实现移动。
1. **动作层**：runtime 增加 `move_to / dig / place / look / follow` 端点 + CatooBot
   侧确认门与额度控制（「手」）；工具从只读扩展为带风险分级的动作工具。
2. **导航**：mineflayer-pathfinder 进 runtime，暴露 `goto(x,y,z)` 与寻路可行性查询。
3. **对话行动闭环**：把 Semantic World Model 注入对话上下文，允许 LLM 通过
   计划-确认-执行的小闭环完成「跟我来/帮我放个火把」类请求。
4. **洞穴/地下感知**、实体意图（entity metadata：村民职业、苦力怕引信状态）。
5. **记忆接入**：把 POI 发现写进长期记忆（「空凛家在工作台西边 20 格」）。

## 11. Phase 2.1 审计修复（World Perception Integrity Audit & Fix）

只修改 WorldStateCache / WorldPerception diff / 对应测试 / 本文档；**未新增任何 Minecraft 行动能力**。

### 11.1 三个问题的根因与修改前后行为

| # | 问题 | 根因 | 修改前 | 修改后 |
|---|---|---|---|---|
| 1 | 分层缓存互相覆盖 | `WorldStateCache.update()` 直接 `self._raw = raw` | 只拉一层的一帧会把其它层数据整体丢弃（语义模型随层龄抖动缺层） | near / local(+interesting) / extended **各自独立存储**：更新某层只替换该层；动态状态（self/players/entities/environment）取最新成功帧；`raw` 改为各层拼回的合并投影（只读） |
| 2 | 环境 diff 常驻噪声 | `env != prev_env` 比较整段 dump | `time_of_day_ticks` 每 tick 在变、`light` 会闪烁 → `minecraft.world.changed` 每个冷却周期都误报 | 新增 `environment_signature()`：只比较 **biome / time_phase / weather / dimension**；事件载荷的 `environment` 也改为该签名 |
| 3 | 方块「移除」漏检 | `changed` 只遍历当前签名 `signature.items()` | 方块被挖掉/消失（旧坐标已不在当前柱面）不计入变化，挖矿/建造场景几乎不触发 world.changed | 并集比较 `previous.keys() \| current.keys()` 且 `previous.get(key) != current.get(key)`：**增/改/删都计数** |

**附带修复（同根因域）**：`_diff` 原先对「本次请求的 raw」做差异——partial poll 会看到空层产生幽灵变化；
现在一律基于缓存**合并视图**做差异，且在没有 near 数据的帧上不建立/不更新方块基线（首帧不误报）。

### 11.2 新增测试（`tests/test_minecraft_world.py`，+11）

| 测试 | 覆盖 |
|---|---|
| `test_partial_near_update_preserves_local_and_extended` | 修复 1：near 更新后 local/interesting/extended 仍在（**语义模型与 raw view 双断言**：water 只来自 local、sand 只来自 extended） |
| `test_partial_local_update_preserves_near_and_extended` | 修复 1：local 更新替换 local+interesting，near/extended 保留 |
| `test_partial_extended_update_preserves_near_and_local` | 修复 1：extended 更新只覆盖 extended |
| `test_layer_age_advances_independently` | 层龄独立（任务书逐值示例：t=1 更新 near → near=0 / local=1 / extended=1） |
| `test_environment_signature_ignores_ticks_and_light` | 签名只含 4 字段 |
| `test_environment_tick_change_alone_does_not_emit_world_changed` | Test A |
| `test_environment_time_phase_change_emits_world_changed` | Test B |
| `test_environment_weather_change_emits_world_changed` | Test C |
| `test_environment_biome_change_emits_world_changed` | Test D |
| `test_world_changed_detects_removed_block` | 修复 3：A,B,C→A,B ⇒ `changed_blocks == 1` |
| `test_world_changed_counts_multiple_removed_blocks` | 修复 3：移除 3 块 ≥ 阈值 3 ⇒ 事件，计数 3 |

### 11.3 Phase 3 前置边界：movement-aware world diff（**当前未实现**）

**当前 near signature 以世界坐标 `(x,y,z)` 为键；Phase 2 罐头不移动，「签名差异＝世界变化」成立。**

Phase 3 一旦加入移动/跟随：观察窗口本身在平移，同一批方块在窗口内换坐标 →
当前实现会把**位移**误报为 `minecraft.world.changed`。

**Phase 3 开始前必须先重新设计 movement-aware world diff**，例如：

- 以罐头所在区块为基准做窗口对齐（比较「本区块内的相对坐标」）；
- 或把差异显式分成「世界变化」与「窗口位移」两类，并在事件里分别标注；
- 并对阈值/冷却做移动场景的重新标定。

在此之前，任何移动能力**不得复用**本差异逻辑（任务书 §九 的硬边界）。

### 11.4 当前剩余风险

1. near 签名是「柱面表层」快照：树冠遮挡处、地下的变化不反映（Phase 2 既定取舍，§9 已记）。
2. 环境签名刻意不含 `light`：光照剧变（火把被挖、连续光照过渡）不产生事件；如需 light 事件须单独设计阈值。
3. 合并视图的 `fetched_at` 是「最新成功帧」时间；各层真实龄期看 `layers.*.age_seconds`（两者勿混用）。
4. 若首轮只拉到 local/extended，方块基线推迟到首个 near 帧才建立：此前（≤1s）的方块变化不触发事件（可接受）。

### 11.5 Phase 3 readiness

Phase 2.1 完成后，感知层数据一致性通过（partial merge / layer age / 环境签名 / 增删改检测全覆盖），
**可以进入 Phase 3**；但 Phase 3 的第一个工作项必须是 §11.3 的 movement-aware world diff 重设计。
