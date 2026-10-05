# CatooBot Minecraft Phase 3E — Minecraft Action Tools & Agent Bridge

> 目标（任务书唯一核心目标）：**让罐头的大模型能够在理解用户意图后，安全地调用已经存在的
> Minecraft 感知与行动能力。** 本阶段不新增任何 Minecraft 底层能力。

Phase 3A–3D 已有：连接、世界感知、语义世界模型、movement-aware diff、Action Runtime
（look_at / chat / move_to / follow_player / stop）、Pathfinder、取消/cleanup/timeout，
并在真实 Minecraft Java 服务器上人工验证通过。Phase 3E 把它们接进 LLM Tool 系统。

---

## 一、架构（LLM → Tool → Policy → Service → Runtime → Event → Context）

```text
CatooBot LLM
     │  （json 决策 / native function call —— 既有 ToolOrchestrator，未改）
     ▼
Minecraft Tool Layer   app/tools/builtins/minecraft_actions.py + minecraft_world.py
     │  validate（ToolRuntime 的 schema 校验）
     ▼
MinecraftActionPolicy  app/integrations/minecraft/agent.py
     │  enabled / online / risk / explicit intent / busy / context
     ▼
MinecraftAgentBridge.invoke()
     │  结构化 ok/error（§二十七/§二十八），异常绝不外泄
     ▼
MinecraftService       app/integrations/minecraft/service.py（既有，未改动作语义）
     │  HTTP（127.0.0.1）
     ▼
Action Runtime         minecraft_runtime/action_runtime.js（注册表/互斥/超时/取消/cleanup）
     ▼
Mineflayer / Pathfinder
     ▼
Minecraft
     ▼
World Perception（Phase 2/3A，只读）
     ▼
action 事件 → MinecraftAgentContext（当前动作 / 最近动作 / 活动）→ 下一轮 LLM 上下文
```

**LLM 永远不会直接碰 runtime**：Tool 层里没有 HTTP、没有 mineflayer、没有 pathfinder
（`tests/test_minecraft_agent_tools.py::test_tool_layer_never_touches_runtime_transport`
直接对源码断言这件事）。所有动作都经过 `MinecraftService` → Action Runtime，
因此 `action_id` / timeout / cancellation / safety / concurrency / event / logging 全部统一。

---

## 二、六个 Tool 与风险分级（§二/§十三）

| Tool | 风险 | 需要在线 | 独占 | runtime 动作 | 说明 |
|---|---|---|---|---|---|
| `minecraft_world` | SAFE | 否（离线也能答「不在世界里」） | — | 无（只读） | 语义世界模型只读查询 |
| `minecraft_chat` | SAFE | 是 | 否（唯一允许并存） | `chat` | 在服务器里说一句话 |
| `minecraft_look_at` | SAFE | 是 | 是 | `look_at` | 只改朝向，不移动 |
| `minecraft_stop` | SAFE | 否（安全停止永远可用） | 控制面 | `stop` | 最高优先级停止，幂等 |
| `minecraft_move_to` | LOW | 是 | 是 | `move_to` | 非破坏性寻路移动 |
| `minecraft_follow_player` | LOW | 是 | 是 | `follow_player` | 持续跟随（启动即 RUNNING） |

风险词表（§十三）：`SAFE / LOW / MEDIUM / HIGH / DESTRUCTIVE`。本阶段**只有 SAFE / LOW
有对应动作**；`allow_medium / allow_high / allow_destructive` 默认 false 且没有实现，
dig / place / attack / craft / inventory / container 等一律没有 Tool（Phase 4 再说）。

> 注意两个不同的"风险"：`ToolMetadata.risk_level`（low/medium/high）是通用 Tool Runtime
> 的既有词表（决定 `tools.permissions.allowed_risk_levels`）；Minecraft 的动作分级是
> `ACTION_RISK`（SAFE/LOW/…），由 `MinecraftActionPolicy` 权威判定。六个 Tool 的通用
> 风险都是 `low`，真正的门在动作分级与意图门上。

### Tool 参数（§六-§十一）

| Tool | 参数 | 返回（成功） |
|---|---|---|
| `minecraft_world` | 无 | `{ok, online, dimension, position, biome, time_of_day, weather, health, nearby_players[{name,distance,relative_direction,position}], nearby_entities, points_of_interest, terrain, age_seconds}` + 一句 summary |
| `minecraft_chat` | `{message}` | `{ok, action:"chat", action_id, status:"SUCCEEDED"}` |
| `minecraft_look_at` | `{x,y,z}` | `{ok, action:"look_at", action_id, status:"SUCCEEDED"}` |
| `minecraft_move_to` | `{x,y,z}` | `{ok, action:"move_to", action_id, status:"RUNNING"}` |
| `minecraft_follow_player` | `{username, distance?}`（1.5~6，默认 2.5） | `{ok, action:"follow_player", action_id, status:"RUNNING"}` |
| `minecraft_stop` | 无 | `{ok, action:"stop", status:"IDLE", cancelled:[action_id…]}` |

`look_at` **不接受** yaw/pitch（rotation 数学只在 runtime）；`move_to` **不接受**
speed/yaw/pitch/jump/pathfinding 参数——`additionalProperties: false` 会在 schema 层拒绝。

失败统一为结构化错误（§二十七），模型看到的是稳定词表：

```json
{"ok": false, "error": {"code": "minecraft.action_busy", "message": "当前正在执行 Minecraft 行动（follow_player）；如需打断，先用 minecraft_stop 停止它"}}
```

`minecraft.offline` / `minecraft.disabled` / `minecraft.action_busy` /
`minecraft.action_invalid` / `minecraft.path_not_found` / `minecraft.player_not_found` /
`minecraft.player_lost` / `minecraft.follow_target_too_far` / `minecraft.action_timeout` /
`minecraft.action_failed` / `minecraft.action_not_allowed`（风险开关或缺少用户意图）。
runtime 的内部错误码（`action.busy`、`path.not_found`、`player.lost`…）在
`agent.RUNTIME_ERROR_CODES` 一处翻译成上表，绝不外泄。

---

## 三、Policy Gate（§十二/§十四/§十五/§十六）

判定顺序固定（`MinecraftActionPolicy.check`）：

```text
Tool 已批准？ → minecraft 启用？ → tools 启用？ → 需要在线时在线？
→ 风险开关（allow_safe / allow_low / …） → LOW 需显式用户意图 → 独占动作是否忙？
→ 上下文（follow 目标必须在附近看得见）
```

* **SAFE 自动允许**（`world/ chat / look_at / stop`）。
* **LOW 只在「用户明确要求」时允许**。意图门认的是**结构性事实**：
  这一轮是不是用户发起的对话（`ToolContext.metadata["minecraft_explicit_intent"]`，
  由角色运行时 / 管理台干跑设置在用户回合上）。Tool Layer **不做 NLP**——「用户是不是
  要让罐头过去」由 LLM 判断（§十六），Policy 只回答「这个调用现在允许吗」。
  后台/自主回合（没有这个标记）里，模型自己推理出「我应该跟过去」也会被拒（§十五）。
* **忙时拒绝**：前台独占动作 RUNNING 时再要独占动作 → `minecraft.action_busy`，
  并明确告诉模型「要打断先用 minecraft_stop」；绝不自动取消当前动作（§二十六）。
  `chat` / `stop` / `world` 不受影响。
* **目标必须在附近**：`follow_player` 的 username 必须出现在当前语义模型的
  nearby players 里（感知可用时）——不猜、不拼、不跟随看不见的玩家（§四十二/§四十三）。
  感知不可用时跳过这一条，交给 runtime 的 `player.not_found`。

日志（§四十七）：`[MC Policy] allowed/rejected tool=… risk=… code=…`、
`[MC Tool] requested tool=… args=…`、`[MC Action] action_id=… status=…`。
绝不打印 token / 密码 / 密钥。

---

## 四、Minecraft Context（§十八/§十九/§二十二/§二十四/§四十八）

`MinecraftAgentContext` 完全是**事件驱动**的：`RUNNING` 不会被自己「模拟完成」，
终态只来自 runtime 的 `minecraft.action.*` 事件。

| 事件 | 上下文变化 |
|---|---|
| `minecraft.spawned` | `online=true`，记 username |
| `minecraft.disconnected` / `kicked` | `online=false`，清空 current_action（runtime 已取消动作） |
| `action.started` | `current_action = {action, action_id, status:RUNNING}` |
| `action.completed` | 清 current、写 `last_action`（含 `result`）、`activity = "刚走到 (x, y, z)"` |
| `action.failed` / `cancelled` / `timeout` | 清 current、写 `last_action`（含稳定 code）；`activity` 保持上一次成功的事实 |

世界事实（维度/坐标/附近玩家）不缓存快照：每次要读时向感知层**现取**
（`service.world_view()`，内存投影，无 IO），所以「10 秒前的 world snapshot」永远不会
堆进对话历史（§十八）。

**每轮注入一行**（§十九/§四十五，≈150–240 字符，随 `CharacterContextBuilder` 的
`minecraft=` 进入 system prompt）：

```text
罐头正在 Minecraft 里（minecraft:overworld，(120, 64, -230)，plains）；附近玩家：空凛（6.4格，front_right）；正在做：follow_player（RUNNING）。
```

模型需要更多事实时自己调 `minecraft_world`（它返回结构化结果 + summary）；raw snapshot
（45KB+）永远留在感知层，绝不进 LLM。

WebUI `GET /api/v1/minecraft` 的 `agent` 块提供同一份只读投影（上下文 + 六个工具的风险/
启用/是否允许），连接页新增「LLM Tool Debug（只读）」面板（§三十/§四十八）——
没有"模拟 LLM"控制台。

---

## 五、Async Action / Event Bridge（§二十/§二十一/§二十二/§三十三/§三十七/§四十六）

* `move_to` / `follow_player`：Tool → Service → **RUNNING + action_id 立刻返回**，
  绝不 sleep 等完成；测试断言工具请求耗时 < 1s（跟随最长跑 120s、导航最长 30s）。
* 终态由 `minecraft.action.completed|failed|cancelled|timeout` 事件回到 CatooBot：
  更新 Service 镜像（`action.result` / `action.error` / `action.code`）与 Agent Context。
* **绝不自动开启新的 Agent Turn**（§二十一/§四十六）：事件回调只更新 Context；
  只有既有对话/Agent 回合里的推理才会再叫模型。测试断言事件之后 LLM 调用次数不变。
* 防循环：通用 Tool Runtime 的 `max_calls_per_turn` + 循环守卫（同一调用重复即拦）。
  Phase 3E 顺手修了一个收敛缺口：**被拦下的调用现在也消耗本轮预算**，否则
  「拦住 → 不记账 → 模型再要一次 → 又拦住」会一直转到时间预算耗尽（每次都是真实模型往返）。

### move_to 改成持续型动作（本阶段唯一的既有行为变更）

任务书 §33/§37 要求「`move_to` RUNNING 立即返回」「Tool request 不被 30s / 120s action
timeout 阻塞」，而 Phase 3C 的 `move_to` 是**同步**的（HTTP 挂到到达或失败，最长 30s）。
因此 Phase 3E 把 `move_to` 改成与 `follow_player` 完全一样的 `start`/`wait` 两阶段：

* HTTP 响应：`{action_id, action:"move_to", status:"RUNNING"}`；
* 到达：`minecraft.action.completed` 事件带 `result{target, final_position, distance_to_target}`；
* 无路径：`minecraft.action.failed`（`code=path.not_found`）；
* 超时/取消：`TIMEOUT` / `CANCELLED` 事件 —— 与 stop 的 cleanup 语义完全不变。

**动作本身的语义一行未改**：非破坏性（`canDig=false`、`scaffoldingBlocks=[]`、
`allow1by1towers=false`、`canOpenDoors=false`）、max_distance=64、GoalNear r=1.5、
30s 超时、每次 cleanup `setGoal(null)` + `clearControlStates()`、路径不可达即失败。
顺带修掉一个既有的不一致：客户端请求超时 15s < 导航超时 30s，同步语义下"走路超过 15s
就报错但动作还在跑"。现在导航不再占用 HTTP 请求。

---

## 六、Prompt / Tool Description 设计（§十七/§四十-§四十三）

Tool description 明确写死边界，例如：

* `minecraft_move_to`：*"通过非破坏性寻路移动到世界坐标；不能挖方块或放方块，找不到路就
  失败。启动后立即返回，是否到达由事件告知。"* 何时用：**用户明确要求**走过去时；
  何时不用：用户只是聊天提到位置、或想"顺便"移动时。
* `minecraft_follow_player`：*"持续跟随指定玩家…这是持续动作，立刻返回 RUNNING，
  用 minecraft_stop 可以停止。"* 何时不用：一次性的"过来"用 move_to；不要在用户没要求时主动跟人。
* `minecraft_stop`：*"立即停止当前 Minecraft 行动。最高优先级。"*

约束（写进 description + 既有 DECISION_SYSTEM 规则）：不要因为"应该/最好/顺便"自主调用
LOW 动作；已在 RUNNING 的动作不要重复调用；用户说"停"优先 stop；不知道目标位置先
`minecraft_world`；不假设玩家坐标、不编造世界事实；报错就按 error code 调整而不是无限重试；
不许用 chat 假装动作已完成。

「罐头你过来」的完整链路（§四十一）：`minecraft_world` 看附近玩家与坐标 →
确认目标玩家 → `minecraft_move_to`。候选检索保证这条链路可行：
`minecraft_move_to` 命中"过来"（score 1.0）时 `minecraft_world` 同时在候选里（0.319），
所以模型能看到两个工具。多个玩家而上下文无法确定"你"是谁时，宁可问用户，不要猜。

---

## 七、测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_minecraft_agent_tools.py`（44） | §34 六个 Tool 注册 + schema（无 yaw/pitch、无寻路参数）；§35 Policy（SAFE 允许 / LOW 需意图 / 离线 / 未启用 / 忙 / 未知工具 / 风险开关 / 目标必须可见 / MEDIUM 及以上不可用）；§36 Tool→ **只有** Service（含源码级"不碰 transport"断言）；§37 异步（RUNNING + action_id，<1s）；§38 事件→上下文（started/completed/failed/cancelled/disconnect、稳定错误码、"RUNNING 不许自己完成"）；上下文行紧凑 + 静默；快照；忙门用活上下文 |
| `tests/test_minecraft_agent_e2e.py`（9） | §39 自然语言闭环：Test A「你过来」→ world → move_to（坐标来自感知）、Test B「跟着我」→ follow RUNNING（<2s）、Test C「停」→ stop 取消 follow；模型调 dig/place/attack/craft → 未知工具且零 Minecraft 调用；自主回合 LOW 被拒 / SAFE 仍可用；重复调用被循环守卫拦下且回合收敛；事件不触发新回合；上下文行确实进入 system prompt |
| `minecraft_runtime/test/move_to.test.js` | move_to 注册表属性（`detached=true`、有 start/wait、无阻塞 run）、坐标校验、非破坏性配置、超时/半径 |
| `minecraft_runtime/test/e2e.js`（flying-squid） | Test A/B/C 按持续型语义重写：HTTP 立刻 RUNNING → 等 `completed`（result.distance_to_target ≤ 2.2）/ `cancelled`（goal null + isMoving false + 位置冻结）/ `timeout`（终态 TIMEOUT + goal null） |
| `tests/test_minecraft_actions.py` | 服务层：move_to 启动即 RUNNING、终点/失败经事件落到镜像（`result`/`error`/`code`）、错误翻译（busy/offline/invalid/path_not_found） |
| `tests/test_web_api_minecraft.py` | `GET /minecraft` 的 `agent` 块（未启用 / 启用两种形态、风险与 reason）、move_to 端点 RUNNING |
| `webui/src/pages/__tests__/minecraft.spec.ts` | LLM Tool Debug 面板：六个工具的 风险/状态/原因 + Agent 上下文（在线/维度/坐标/附近玩家/当前与最近动作） |

回归：Phase 1 / 2 / 2.1 / 3A / 3B / 3B.1 / 3C / 3D 的既有测试全部保留并通过
（Node 单测 + Node E2E + 服务层 + WebUI）。

---

## 八、安全边界验证（§五十）

| 要求 | 状态 |
|---|---|
| LLM 无法 dig / place / attack / craft | ✅ 这些 Tool 不存在；调了只会得到 `unknown_tool`，且不产生任何 Minecraft 调用（有测试） |
| LLM 无法直接 setControlState / 碰 Mineflayer | ✅ Tool 源码不含 transport/mineflayer/pathfinder；执行只在 runtime 内部 |
| LOW 必须有明确 Policy Gate | ✅ 意图门 + 风险开关 + 在线 + 忙 + 目标可见，五道 |
| 异步 Action 不阻塞请求 | ✅ move_to / follow 启动即 RUNNING，<1s |
| Action 事件回到 Context | ✅ 事件驱动 current/last/activity，含 result 与稳定 code |
| 不存在自主无限 Action Loop | ✅ 事件不触发新回合；循环守卫 + 被拦调用记账 → 回合按预算收敛 |
| 没有绕过 Action Runtime 的路径 | ✅ 六个 Tool 只调 MinecraftService |
| 没有新增破坏世界的能力 | ✅ Phase 3E 未新增任何 runtime 动作 |

---

## 九、性能 / Context Size（§四十五）

* `minecraft_world` 返回结构化语义模型（几百字节量级）+ 一句 summary；raw snapshot 45KB+
  永不进 LLM（Phase 2 的实测：raw 45KB → 语义 1.1KB → LLM 上下文 ≈249B）。
* 每轮注入的 Minecraft 上下文固定在 ≈150–240 字符（`context_line(limit=240)`）。
* 工具参数都很小（坐标三个数 / 一个 username / 一句话）。
* 不会每轮自动调 `minecraft_world`——只有模型判断需要时才调（候选检索按需给出 schema）。

---

## 十、已知限制（§四十四 及其他）

1. **没有 Minecraft 长期记忆**：不新增永久 POI / 地图 / 地点记忆，只有运行期上下文（Phase 4+）。
2. **意图门是结构性的**：它保证「非用户回合不许 LOW」，但不判断用户在用户回合里到底想不想；
   后者由 LLM + prompt 约束负责（§十六）。模型在用户回合里"理解错"仍可能移动——
   所以还有距离上限、非破坏性寻路、STOP 与事件回流兜底。
3. **多玩家歧义**：上下文无法判断"你"是谁时靠模型询问用户（prompt 约束），Policy 不做消歧。
4. **感知延迟**：附近玩家/坐标来自感知层（near 层 1s 节拍），极端情况下有最长几秒的滞后；
   follow 目标校验因此只在感知可用时生效。
5. **QQ ↔ Minecraft 身份不绑定**：`minecraft_chat` 的发送者是 MC username，QQ 身份与
   MC 身份是两套（§四十三）；本阶段不做映射，只在需要时用"当前 MC chat sender"。
6. **`move_to` 语义变更**：见 §五——WebUI 的 MOVE TO 按钮现在立刻返回"已开始移动"，
   终点由 Current Action 面板与事件确认。
7. `minecraft_world` 的 cache_ttl=2s：同一轮里连续两次查询会复用同一份只读结果。

---

## 十一、Phase 4 readiness

* 风险分级表、配置开关（`allow_medium/high/destructive` 已就位且默认关闭）、
  Policy 判定顺序、结构化错误词表、事件→上下文回流、WebUI 只读投影都已通用化：
  Phase 4 加 `dig/place/attack` 时只需要
  ① runtime 注册新动作；② `ACTION_RISK` 登记风险；③ 一个 Tool 类 + 注册；
  ④ 打开对应开关。**不需要**改 Policy、Bridge、Context 或前端面板。
* 届时必须补的：MEDIUM/HIGH 的确认门（现在只有布尔开关）、破坏性动作的背包/容器语义、
  以及更细的上下文（手持物/容器状态）。
* `minecraft.interaction.*`（Phase 4 配置段）现在不存在——本阶段不提前实现。
