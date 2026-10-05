# CatooBot Minecraft Phase 3D · follow_player Dynamic Navigation

> 在 Phase 3C `move_to` 之上加入 `follow_player`：罐头能找到指定玩家并**持续跟随**，
> 目标移动时由官方 Dynamic Goal 自动重新规划；目标离开/失效时安全结束；
> STOP 时立即停止真实移动。仍然属于机械层，**未暴露给 LLM**。

---

## 1. 修改文件清单

**新增**

| 文件 | 说明 |
|---|---|
| `minecraft_runtime/test/follow_player.test.js` | 54 checks：注册表/校验/目标解析/Dynamic Goal（真实 GoalFollow）/entity 重绑/丢失宽限/追逐上限/cleanup |
| `docs/MINECRAFT_PHASE3D.md` | 本文档 |

**修改**

| 文件 | 变更 |
|---|---|
| `minecraft_runtime/action_runtime.js` | **持续型动作（detached）支持**：`start`/`wait` 两阶段——启动失败同步反馈（如 404），启动成功立刻返回 `RUNNING`，终态由事件送达（此前 follow 会阻塞 HTTP 到超时） |
| `minecraft_runtime/runtime.js` | `allow1by1towers=false` 硬化；`follow_player` 注册（exclusive/LOW/detached/120s/校验/start/wait/cleanup）；`POST /minecraft/follow_player`；`status.pathfinder` 增加 `username`/`distance`；`FOLLOW_DEFAULTS` 导出 |
| `minecraft_runtime/test/e2e.js` | Phase 3D Test A–E（跟随/STOP/丢失/太远/超时），E2E 环境 `MC_FOLLOW_TIMEOUT_MS=8000`、`MC_FOLLOW_MAX_CHASE_DISTANCE=16` |
| `minecraft_runtime/test/move_to.test.js`、`package.json` | 注册表断言含 follow_player；`npm test` 加入 follow 单测 |
| `minecraft_runtime/test/smoke_real_server.js` | 真实服务器 Smoke 增加 follow 段（第二个客户端当目标，走动后继续跟 + STOP） |
| `app/config/settings.py` | `FollowPlayerConfig`（timeout 10–600 默认 120；max_chase_distance 默认 64） |
| `app/integrations/minecraft/runtime_client.py`、`service.py` | `follow_player()` + `MinecraftPlayerNotFound`(404) / `MinecraftPlayerLost` / `MinecraftFollowTargetTooFar`；`MC_FOLLOW_TIMEOUT_MS` / `MC_FOLLOW_MAX_CHASE_DISTANCE` 注入；镜像带 distance |
| `app/web/api/minecraft.py` | `POST /api/v1/minecraft/follow_player`；disabled 快照补 distance |
| `app/web/config_registry.py` | 新配置键标注（叶子名/说明/类名） |
| `config/config.example.yaml`、`config/config.yaml` | `minecraft.action.follow_player.{timeout,max_chase_distance}` |
| `tests/test_minecraft_service.py` | FakeRuntime +follow_player 端点与状态镜像 |
| `tests/test_minecraft_actions.py`、`test_web_api_minecraft.py`、`test_minecraft_world.py`、`test_web_routes.py` | follow 服务层 8 个用例 + WebAPI 3 个 + 跟随期间感知安静 + 路由快照 214 条 |
| `webui/src/*`、`dist/*` | Follow Player 表单（Player/Distance/[FOLLOW]）+「Following: 名字 · Distance」显示 + 3 个用例 + 重建 |
| `docs/WEBUI_API_CONTRACT.md`、`WEBUI_V1_ROUTE_MATRIX.md`、`README.md` | 契约 §7.5、路由矩阵 214 条、索引 |

**未新增**：dig / place / attack / craft / eat / inventory / building / combat /
自动任务 / LLM Tool / 自主 Agent / Minecraft 行动决策 / 长距离自动分段导航。

## 2. GoalFollow 实现方式（§六/§七/§十三）

```js
const entity = bot.players[username].entity           // 活 entity 引用（不是坐标）
bot.pathfinder.setGoal(new goals.GoalFollow(entity, distance), true)   // true = dynamic
```

- **持活 entity 引用**：`GoalFollow.hasChanged()` 在目标移动超出一个 range 时返回 true，
  pathfinder 随即 `resetPath('goal_moved')` 自动重规划——官方 Dynamic Goal 语义
  （已核对 2.4.5 源码：`index.js:437`）。
- **绝不缓存静态坐标**（那只是「去玩家刚才的位置」）；**绝不手写 100ms 轮询 setGoal**
  （会频繁重建路径、刷 `goal_updated`、破坏生命周期）。
- **不用 `goto`**：pathfinder 文档明确 `goal_reached` 不会对 dynamic goal 触发，
  `goto` 会永远挂起——follow 的生命周期由 Action Runtime 管理，不是 `goto`。

## 3. Dynamic Goal 生命周期（§八/§九/§十）

```
QUEUED → RUNNING（启动成功立即返回；HTTP 200 {status:"RUNNING"}）
          │  持续跟随（正常情况下不会自动 SUCCEEDED）
          ├─ STOP / disconnect / shutdown → cleanup → CANCELLED
          ├─ 120s 超时（可配 10~600s）      → cleanup → TIMEOUT
          ├─ 目标消失 > 3s 宽限             → FAILED player.lost（动作自清 Goal）
          └─ 距目标 > max_chase_distance    → FAILED follow.target_too_far
```

**持续型动作（detached）**：ActionRuntime 用 `start`/`wait` 两阶段——`start` 的失败
（如 `player.not_found`）仍同步反馈给调用方（404）；启动成功后立刻返回 RUNNING，
终态经 `minecraft.action.{cancelled,timeout,failed}` 事件异步送达。
`path_update`/`path_reset`/`goal_updated` 仍是 runtime 内部状态，**不做事件**（§十四）。

## 4. Target entity 管理（§十五/§十六）

Follow 运行态维护 `{username, targetEntity, lastSeenAt}`，每 250ms 监督一次：

1. `bot.players[username]?.entity` 不存在 → 不立刻失败；记录 `lastSeenAt`，
   连续超过 **3s 宽限**（`MC_FOLLOW_LOST_GRACE_MS`，默认 3000）才 FAILED `player.lost`
   ——玩家 logout/login（或 entity 短暂刷新）在宽限内可无缝恢复；
2. entity 对象**身份变化**（服务器重建引用）→ 才重绑一次 `GoalFollow` 并 `setGoal`；
   **同一个 entity 移动绝不 setGoal**（交给 pathfinder 的 `goal_moved`）——
   单测断言「实体移动 300ms 内 setGoal 次数不变，换对象后才 +1」；
3. 与目标的直线距离 > `max_chase_distance`（默认 64，可配）→ FAILED
   `follow.target_too_far`（避免玩家跑 500 格时追到世界尽头）。

## 5. STOP / cleanup（§十一）

与 `move_to` 完全同形（3C 的两件套）：

```js
cleanup(bot) {
  bot.pathfinder.setGoal(null)   // 硬清 Goal（优先于 pathfinder.stop 的软标记）
  bot.clearControlStates()
}
```

3B.1 的取消语义保证：**stop 里同步先 cleanup 再报 CANCELLED**（Minecraft 已停止导航后
才对外报告）。E2E Test B 从外部验证三个不变量：`goal == null`、`isMoving == false`、
停止后 400ms 位置漂移 ≤ 0.3 格。disconnect/shutdown 同路径（cancelAll → cleanup）。

## 6. Target lost 策略（§十 E）

见 §4 第 1 条：宽限 3s（可配/可在测试中调小），短暂丢失不失败；超时才 FAILED
`minecraft.player_lost`，并在失败前清掉 Goal（不留残余跟随意图）。

## 7. max chase distance（§十七）

不设 `max_distance=64` 的起点限制（目标本就会移动），改设**最大追逐距离**：
默认 64 格（`minecraft.action.follow_player.max_chase_distance`），超过 → FAILED
`minecraft.follow_target_too_far`。

## 8. timeout（§十三）

默认 **120s**（`minecraft.action.follow_player.timeout`，限制 10~600；第一版不支持 0=无限）。
超时 → ActionRuntime 取消路径 → `minecraft.action.timeout` → cleanup（清 Goal + 清控制位）。
长期跟随模式留给未来的专门设计，不让普通 Action 无限占用前台。

## 9. E2E（§二十三~§二十七，flying-squid）

| 场景 | 结果（实测） |
|---|---|
| **Test A** follow → 目标 `/tp` 两跳（约 7 格/跳）→ 继续跟随 | ✓ 罐头真的走过去（位移 3.8 格），action 始终 RUNNING；`status.pathfinder.goal=GoalFollow`、`target.username=Followee`、`distance=2.5` |
| **Test B** follow → STOP | ✓ CANCELLED、goal=null、isMoving=false、停后 400ms 漂移 ≤0.3 格 |
| **Test C** 目标 quit → 3s 宽限 | ✓ 3010ms 时 FAILED `player.lost`，goal 清空 |
| **Test D** 目标 30.5 格 > 上限 16 | ✓ 252ms 时 FAILED `follow.target_too_far`，goal 清空 |
| **Test E** 持续跟随到超时（E2E 8s） | ✓ TIMEOUT、goal=null、isMoving=false |
| 收尾 | ✓ 连接仍 ONLINE、`active_count == 0`（无僵尸动作） |

手段：E2E 用服务器 `/tp`（flying-squid op 命令）确定性移动目标（先以探针确认
teleport 对另一个客户端可见）；每个 hop 带落点重试以吸收不可达地形。

## 10. 真实服务器 Smoke Test（§二十九）

```
真实服务器 Follow Smoke：NOT AVAILABLE
```

- 2026-10-05 23:0x 执行：`127.0.0.1:25565` 无响应（操作者的 NeoForge 服务器未运行）。
- 脚本已扩展 follow 段：`node minecraft_runtime/test/smoke_real_server.js`
  会拉起第二个真实客户端（`SMOKE_FOLLOW_TARGET`，默认 `SmokeTgt`）作为目标，
  完成 join → follow → 目标走动 → 继续跟（gap ≤ 12）→ STOP 真停 → leave；
  输出 PASS / FAIL / NOT AVAILABLE。
- **§二十九 要求真实服务器 Follow Smoke 在 Phase 3E（LLM Tool 暴露）之前通过。**
  服务器开启后重跑该脚本即可；若服务器是在线模式或白名单，脚本会以
  「目标客户端进不了服务器」的形式跳过 follow 段并如实提示。

## 11. 性能观察

- 监督循环 250ms/次，只有三类工作：entity 解析、距离比较、身份变化重绑；
  **正常移动期间零 setGoal**（对比「轮询重建路径」方案，避免路径抖动）。
- 跟随期间 WorldPerception 持续运行：连续四帧窗口平移全部 `WINDOW_SHIFT`、
  `changed_blocks=0`、零虚假 `world.changed`；重叠区真实变化照常触发（单测覆盖）。
- E2E 实测：target 移动后罐头在 1s 内收敛到 2.0~3.8 格内；STOP 取消 153–191ms；
  120s 超时在实际运行中由 8s 环境值替代验证（生产默认仍 120s）。

## 12. 已知限制

1. **地形依赖**：目标站在不可达处（深坑/墙后）时罐头只能靠近到路径允许的位置；
   `canOpenDoors=false`、不挖不放，故门后目标可能一直保持距离（不会失败，除非 > chase 上限）。
2. **丢失判定是「看不见」**：跨维度/超出视距也表现为 entity 消失 → 3s 后 `player_lost`
   （语义正确，但「跨维度跟随」不支持）。
3. 宽限 3s 固定语义（可配置/测试可调小），玩家快速反复上下线可能触发一次 FAILED。
4. 单目标；改距离或换目标需先 STOP 再重新 FOLLOW（不提供动态改参）。
5. LLM 仍不能调用 follow_player（§三十）；WebUI/Service/E2E 是唯一入口。

## 13. Phase 3E readiness

- 机械层已完备：`chat / look_at / move_to / follow_player / stop` 五个动作共享
  同一 Action Runtime（单前台、超时、取消、安全停止、cleanup 至多一次、事件）。
- 唯一未闭环项：**真实服务器 Follow Smoke（服务器当时未运行）**——Phase 3E 前必须补跑
  （脚本已就绪，一条命令即可）。
- 下一步建议：LLM Tool 暴露（Minecraft Action Tool，把动作包装成带风险分级与确认门的
  工具）、`dig/place=MEDIUM`、`attack=HIGH`；长距离导航在 Service 层做分段而非放宽上限。
