# CatooBot Minecraft Phase 3C · Navigation Runtime / move_to

> 在 Phase 3B/3B.1 Action Runtime 之上加入第一项真实移动能力：`move_to(x, y, z)`。
> 验证「高层 Action → ActionRuntime → Mineflayer Pathfinder → Minecraft 实际移动」
> 能稳定完成、取消、超时与失败。**非破坏性导航**：不挖、不放、不搭桥，不可达就如实失败。
> 普通 LLM 仍不能直接调用 move_to（不加入 LLM Tool Registry）。

---

## 1. 修改文件清单

**新增**

| 文件 | 说明 |
|---|---|
| `minecraft_runtime/test/move_to.test.js` | 注册表属性 / 坐标与距离校验 / 非破坏性 Movements（22 checks） |
| `minecraft_runtime/test/smoke_real_server.js` | 真实服务器 Smoke Test 脚本（§二十一，可选/不进 CI） |
| `docs/MINECRAFT_PHASE3C.md` | 本文档 |

**修改**

| 文件 | 变更 |
|---|---|
| `minecraft_runtime/package.json` | 依赖 `mineflayer-pathfinder@^2.4.5`；`npm test` 加入 move_to 单测 |
| `minecraft_runtime/runtime.js` | 加载 pathfinder 插件；spawn 后 `configureMovements`；`ACTION_REGISTRY` 抽取导出；`move_to` 注册（exclusive/LOW/30s/距离校验/run/cleanup）；`POST /minecraft/move_to`；`status.pathfinder` 诊断；`require.main` 守卫 + `module.exports`（供测试） |
| `minecraft_runtime/action_runtime.js` | 成功响应带 `result`（§八）；动作自带 `ActionError` 码保留（`path.not_found` 不被包成 `action.failed`）；FAILED 事件带 `code` |

> **Phase 4H.1 起本文件里的 `goto()` 语义已作废**：`move_to` 不再调用 `bot.pathfinder.goto()`
> （mineflayer-pathfinder 2.4.5 在空路径上会静默 resolve，把"没找到路"报成成功），
> 改为自己挂 Pathfinder 生命周期 + 用实际位置硬校验到达，并新增 `path.not_reached`。
> 见 [MINECRAFT_PHASE4H1.md](MINECRAFT_PHASE4H1.md)。本文件其余内容（坐标校验、
> 非破坏性 Movements、ActionRuntime 契约）仍然有效。
| `minecraft_runtime/test/e2e.js` | Phase 3C Test A/B/C（成功 / STOP 真停 / 超时），E2E 用 `MC_MOVE_TIMEOUT_MS=2500` |
| `app/config/settings.py` | `MoveToConfig.max_distance` + `MinecraftActionConfig` + `MinecraftConfig.action` |
| `app/integrations/minecraft/runtime_client.py` | `move_to()` |
| `app/integrations/minecraft/service.py` | `move_to()`（坐标/世界边界/距离校验 + 错误翻译）；`MinecraftPathNotFound`；`MC_MOVE_MAX_DISTANCE` 注入；`snapshot.pathfinder` |
| `app/web/api/minecraft.py` | `POST /api/v1/minecraft/move_to`；disabled 快照补 `pathfinder` |
| `config/config.example.yaml`、`config/config.yaml` | `minecraft.action.move_to.max_distance: 64` |
| `tests/test_minecraft_service.py` | FakeRuntime 扩展（move_to 端点 + pathfinder 状态镜像） |
| `tests/test_minecraft_actions.py` | +8 个 move_to 服务层用例 |
| `tests/test_minecraft_world.py` | +1：move_to 移动期间感知安静（§二十三） |
| `tests/test_web_api_minecraft.py`、`tests/test_web_routes.py` | move_to 端点测试 + 路由快照 213 条 |
| `webui/src/types/minecraft.ts`、`api/minecraft.ts`、`pages/Minecraft.vue`、`pages/__tests__/minecraft.spec.ts`、`dist/*` | Target X/Y/Z + MOVE TO + Moving to 显示 + 4 个用例 + 重建 |
| `docs/WEBUI_API_CONTRACT.md`、`WEBUI_V1_ROUTE_MATRIX.md`、`README.md` | 契约 §7.5、路由矩阵 213 条、索引 |

**未新增**：follow / dig / place / attack / craft / eat / inventory / building / mining /
combat / LLM Tool / 自主 Agent / 自动任务规划 —— 注册表仍然只有 `look_at / chat / move_to / stop`。

## 2. Pathfinder 版本

`mineflayer-pathfinder@2.4.5`（官方 PrismarineJS 实现；`bot.loadPlugin(pathfinder.pathfinder)`
——该包导出的 inject 函数在 `.pathfinder` 属性上）。

## 3. Movements 配置（非破坏性，§三）

```js
configureMovements(movements):
  movements.canDig = false          // 绝不为了到达目标挖方块
  movements.scafoldingBlocks = []   // 绝不搭桥/搭塔（默认含泥土/圆石 → 会主动放方块）
  movements.canOpenDoors = false    // 保守默认
```

其余保持 pathfinder 默认（`allowSprinting` / `allowParkour` / `allow1by1towers`——都是
跳跃/疾跑类，不修改世界）。**目标不可达 → `NO_PATH` 失败，绝不自动挖墙/搭桥/绕圈/改目标。**

## 4. move_to Action 生命周期

```
POST /minecraft/move_to {x,y,z}
  → validate（有限数字 / 世界边界 / 距当前位置 ≤ max_distance）
  → exclusive 前台锁（忙 → action.busy 409）
  → RUNNING（action.started 事件）
  → GoalNear(x, y, z, radius=1.5) + bot.pathfinder.goto(goal)
  → goal_reached → SUCCEEDED
      result: {target:{x,y,z}, final_position:{x,y,z}, distance_to_target}
  → noPath/内部超时 → FAILED（500 path.not_found，message「无法找到到达目标的非破坏性路径」）
  → STOP/disconnect/shutdown → CANCELLED（先 cleanup 再报）
  → 30s 超时 → TIMEOUT（cleanup 后落定）
```

请求**禁止** yaw/pitch/speed/WASD/controlState——上层不控制底层运动。
`GoalNear`（半径 1.5）而非 `GoalBlock`：玩家位置通常不是严格 block center。

## 5. cleanup / STOP 设计（§十一/§十二，本阶段最重要的验收）

```js
move_to.cleanup(bot):
  bot.pathfinder.setGoal(null)      // 硬清 Goal（优先于 pathfinder.stop 的软标记）
  bot.clearControlStates()          // 全局移动兜底（两件套都保留）
```

顺序保证：**Minecraft 已停止执行导航后，才对外报告 CANCELLED**（3B.1 的 runCleanup 在
`stop()` 里同步执行，先 cleanup 再 finish）。

验证不止于 Action 状态——Node E2E Test B 断言：
`status.pathfinder.goal === null`、`status.pathfinder.moving === false`、
且停止后 400ms 位置不再漂移（≤0.3 格）。

## 6. 无路径策略（§十）

- Pathfinder `goto` 以具名错误 reject（`NoPath` / `Timeout`）→ runtime 分类为
  `path.not_found`（HTTP 500，`minecraft.path_not_found`），Action 记 **FAILED** 并发出
  `minecraft.action.failed`（带干净中文 message 与 code，**不透传 Pathfinder 内部错误对象**）。
- 失败时动作自己也会 `setGoal(null)`——绝不留残余导航意图。
- 不自动挖/搭/绕无限圈/改变目标。

## 7. timeout 策略（§十三）

- 默认 **30s**（`MC_MOVE_TIMEOUT_MS`，可被测试环境调小）；超时 → 3B.1 的取消路径 →
  `minecraft.action.timeout` 事件 → cleanup（setGoal(null) + clearControlStates）。
- Node E2E 用 `MC_MOVE_TIMEOUT_MS=2500`：可达的 12–16 格约需 3s+，从而得到**确定性超时**。

## 8. WebUI（§十五/§十八）

Minecraft 页 Current Action 卡新增：

```
Moving to: X Y Z （正在移动）       ← 来自 status.pathfinder.target/moving
Target X [ ]  Target Y [ ]  Target Z [ ]
[ MOVE TO ]   [ Look At Test ]   [ STOP ]
```

- 目标输入在拿到位置后预填空（当前 +4 x）；非法/空输入本地拦截（不发请求）。
- Action 卡照旧显示 action / status / action_id / started / elapsed；
  完成后 SUCCEEDED、失败 FAILED、停止 CANCELLED。

## 9. 单元测试（§十九）

| 测试 | 位置 | 覆盖 |
|---|---|---|
| `test_move_to_registers_as_exclusive` | Node `move_to.test.js` | exclusive=true、risk=LOW、timeout=30s、cleanup 存在、注册表只多出 move_to 这一个动作 |
| `test_move_to_validates_coordinates` | Node `move_to.test.js` | 字符串/缺参/NaN/Infinity/世界边界外 → action.invalid；合法坐标归一化通过 |
| （附加）非破坏性 Movements | Node `move_to.test.js` | canDig=false、scafoldingBlocks=[]、canOpenDoors=false |
| （附加）动作自带错误码保留 | Node `action_runtime.test.js` | 抛出的 `path.not_found` 原样冒泡，FAILED 事件带 code、无内部堆栈 |
| `test_move_to_rejects_too_far` | Python | 99 格 > 64 → action_invalid（本地先拒，不发 HTTP）；59 格通过 |
| `test_move_to_rejects_offline` | Python | action.not_online → minecraft.not_connected |
| `test_move_to_busy` | Python | action.busy → minecraft.action_busy（409） |
| `test_move_to_timeout` | Python | TIMEOUT 状态原样返回（带 action_id） |
| `test_move_to_success` | Python | SUCCEEDED + result(target/final_position/distance_to_target) |
| `test_move_to_no_path` | Python | path.not_found → minecraft.path_not_found（500） |
| `test_move_to_cleanup_clears_goal` | Python（镜像）+ Node E2E Test B（真断言） | 取消后 goal/moving 归零 |
| （附加）runtime 不可达 | Python | 503 runtime_down；镜像 pathfinder 回落空闲 |

## 10. E2E（§二十，flying-squid）

| 场景 | 结果（实测） |
|---|---|
| **Test A** join → spawn → move_to 近距离 → goal_reached → SUCCEEDED | ✓ 位移 4.4–4.5 格、`distance_to_target` 0.71–1.28、单次 861–1163 ms；成功后 `goal == null` |
| **Test B** move_to → STOP → CANCELLED → goal == null → isMoving == false | ✓ stop 取消于 153–191 ms；`goal=null`、`moving=false`、停后 400ms 位置漂移 ≤0.3 格 |
| **Test C** move_to → timeout → TIMEOUT → goal == null | ✓ `elapsed=2502ms` 触发 TIMEOUT；`goal=null`、`moving=false` |
| 收尾 | ✓ Test A/B/C 后连接仍 ONLINE、`action.active_count == 0`（无僵尸） |

注：E2E 只证明**机械层**；§二十二的特殊地形（围墙绕行/台阶）由 flying-squid 自然地形
覆盖（diamond_square 有明显高低差；本脚本对不可达地形会如实报 no-path 并换方向重试）。

## 11. 真实服务器 Smoke Test（§二十一）

```
真实服务器验证：NOT AVAILABLE
```

- 2026-10-05 执行：`127.0.0.1:25565` 无响应（操作者的 NeoForge 服务器当时未运行）。

> **Phase 3E 变更（2026-10-06）**：`move_to` 改为**持续型动作**（`detached: true`）——
> HTTP 启动即返回 `{status:"RUNNING", action_id}`，终点/失败经 `minecraft.action.completed`
> / `minecraft.action.failed`（`code=path.not_found`）/ `timeout` / `cancelled` 事件送达；
> 目的：LLM Tool 与任何调用方都不被最长 30s 的导航阻塞（Phase 3E §三十三/§三十七）。
> 动作语义本身（非破坏性寻路、max_distance、GoalNear r=1.5、30s 超时、cleanup 清 Goal、
> 不可达即失败、STOP 真停）**一行未改**；E2E Test A/B/C 的断言改为"HTTP RUNNING + 事件终态"。
> 详见 `docs/MINECRAFT_PHASE3E.md` §五。
- 脚本已就绪：`node minecraft_runtime/test/smoke_real_server.js`
  （环境变量 `SMOKE_HOST/SMOKE_PORT`，认证用本地 `minecraft_runtime/auth.json`）
  ——服务器开启后重跑即可得到 PASS/FAIL；连接成功但移动异常会给出 FAIL 与运行时日志尾部。
- CI 不依赖真实服务器（§二十一允许）。

## 12. 性能观察

- `move_to` 近距离（4.4 格）：**861–1163 ms**（含路径计算，flying-squid 本机）；
  16 格约 **3.2 s**（≈5 格/s，含疾跑）。路径计算在毫秒级，无阻塞事件循环迹象。
- 感知侧不受影响：移动期间 WorldPerception 持续运行；窗口平移由 Phase 3A 的
  movement-aware diff 吸收（连续 3 帧平移 → 全部 `WINDOW_SHIFT`、`changed_blocks=0`、
  零 `world.changed`；重叠区真实变化照常触发）。raw/语义模型体积与 Phase 2 一致
  （45 KB / 1.1 KB，LLM summary 249 B）。
- STOP 响应：取消在 **153–191 ms** 内完成（含 cleanup 与 goal 清空）。

## 13. 已知限制

1. **flying-squid 地形不确定性**：E2E 对目标方向做多候选重试；真实地形由 smoke 脚本覆盖。
2. `canOpenDoors=false`：门后的目标会被判 no-path（保守优先于"帮忙开门"）。
3. 垂直移动（跳跃/掉落）由 Pathfinder 自行处理（`maxDropDown=4`、`allow1by1towers=true`）——
   超过 4 格落差会被视为不可通行；`move_to` 不做垂直专用寻路。
4. 单次最大 64 格（可配置）——长距离需要多次 move_to（由上层在 Phase 3D 决定策略）。
5. `smoke_real_server.js` 会以本地身份登录目标服务器；仅在自有服务器上运行。
6. 感知的 WINDOW_SHIFT 判定仍以方块坐标锚点为准：移动中跨区块的极端快速位移会触发
   `TELEPORT_REBASE`（重建基线、不报事件）——行为正确但会丢失该帧的世界变化信息。

## 14. Phase 3D readiness

- 机械层已通：`move_to` 的成功/取消/超时/无路径/忙/离线全部可观测且可测试；
  Action Runtime 契约（cleanup 语义、单前台、事件）在 3B.1 后保持完整。
- 下一步（§二十四的规划）建议：
  `dig / place = MEDIUM`、`attack = HIGH`（沿用 Action Safety Gate 的风险分级与确认门）；
  `follow` 可基于 `GoalFollow` + move_to 的既有生命周期实现；
  LLM Tool 暴露（Minecraft Action Tool）与「先验证机械层」的边界在 Phase 3C 结束时可解除。
- 若要长距离导航：在 Service 层做分段 move_to（每段 ≤ max_distance）+ 单调进度检查，
  而不是放开第一版的 64 格上限。
