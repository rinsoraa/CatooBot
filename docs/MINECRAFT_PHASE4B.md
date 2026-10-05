# CatooBot Minecraft Phase 4B — Single-Block Dig / 第一个世界修改动作

> 本阶段加入 **`minecraft_dig`**：破坏**一个明确指定的方块**。这是第一个真正修改 Minecraft
> 世界的动作，所以它一次带上四道门：`expected_block` + 用户确认 + `block_changed` 复检 +
> 真实服务器 smoke。
>
> 没有连续挖矿、没有矿物搜索、没有自动找方块/换目标/换工具/捡掉落/导航——**一个 Tool Call
> = 最多一个方块**（§三）。

---

## 一、Phase 4A Debug 确认安全硬化（§一/§三十八）

`POST /api/v1/minecraft/agent/confirm` 的 `create_test` 只能创建**测试专用工具名**
（`minecraft_test_*`）的确认：

```python
if not tool.startswith("minecraft_test_"):
    raise bad_request(
        "create_test 只能创建测试专用工具（minecraft_test_* 前缀）的确认；"
        "正式动作的确认必须来自用户回合",
        code="minecraft.confirmation_invalid",
    )
```

为什么必须这样：4B 之后 `minecraft_dig` 是正式 MEDIUM 动作，如果管理台能凭空造出它的
PENDING，就能被一个真实 USER 回合消费成**正式授权**——那是授权语义问题，不是"管理员可信"
能解决的。测试专用名字在生产注册表里不存在（`ACTION_RISK` 里也没有），所以永远无法被消费。
测试代码仍然可以直接操作 `ConfirmationStore` / 注入风险表（`risk_table=`）做确认测试。

## 二、Tool Schema 与风险（§四/§五/§八）

```json
{
  "type": "object",
  "properties": {
    "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"},
    "expected_block": {"type": "string", "minLength": 1}
  },
  "required": ["x", "y", "z", "expected_block"],
  "additionalProperties": false
}
```

* `ACTION_RISK["minecraft_dig"] = "MEDIUM"` → 走确认门（Phase 4A 的基础设施，一行没改）；
* 风险开关 `minecraft.agent.tools.allow_medium`（默认 **false**：新装即关，打开后才有可能执行）；
* Tool description 写死了能力边界（"每次最多破坏一个方块；不会自动寻找其他方块、不会连续挖掘、
  不会导航过去、不会自动换工具或捡掉落物；挖不动就失败"），并明确要求先 `minecraft_world`
  看清目标再动手、不要假装挖掉。

## 三、完整流程（§二十五/§二十六/§六十）

```text
用户：「把我面前这个石头挖掉。」
  LLM → minecraft_world（看清方块名 + 坐标）
      → minecraft_dig(x, y, z, expected_block)
  判定顺序：注册 → enabled → 工具启用 → 在线 → allow_medium → USER 回合 → trusted（游戏内）
           → 忙 → **需要确认**（挂 PENDING，不执行）→ 模型如实告诉用户
用户：「确认。」
  新 USER 回合 → 模型用**完全相同参数**再调一次
           → 确认消费（user/session/参数指纹全匹配，一次性）
           → MinecraftService.dig → Action Runtime（启动即 RUNNING）
           → Mineflayer dig → 复核 blockAt（真没了才算成功）
           → `minecraft.action.completed` 事件带 result{position, block_before, block_after}
           → WorldPerception 下一次 near diff 看到那次移除
```

**确认是授权，不代替校验**（§二十六/§六十/§六十一）：消费确认之后仍要过
`online / 方块存在 / 方块未变化 / 可挖 / 距离 / 忙`；任何一条失败都不执行。

## 四、目标方块校验（§十三-§十七）

| 检查 | 失败错误码 | 说明 |
|---|---|---|
| 坐标合法（复用世界坐标校验） | `minecraft.action_invalid` | Service 与 runtime 各校验一次 |
| `expected_block` 非空字符串 / ≤64 字符 / 无控制字符 | `minecraft.action_invalid` | Service 层（与 follow 的 username 同规则） |
| 位置有方块且不是空气 | `minecraft.block_not_found` | **不调用 `bot.dig`** |
| `block.name == expected_block` | `minecraft.block_changed`（带 `expected`/`actual`） | §六/§十四：用户确认的是「这个位置的这个方块」 |
| 距离 ≤ `max_distance`（默认 5，眼睛→方块中心） | `minecraft.block_too_far` | §十七：绝不自己走过去（不把 dig 和导航耦合） |
| `bot.canDigBlock(block)` | `minecraft.block_not_diggable` | §十六：不换工具、不找角度、不靠近 |
| dig 结束后复核 `blockAt` | `minecraft.block_break_unconfirmed` | §二十三：不信 Promise，客户端/服务器不同步时不能报成功 |

前六条是 **start 阶段同步校验**（失败即返回，动作根本没进 RUNNING）；最后一条在 wait 阶段。

## 五、Mineflayer 实现（§十二/§十九/§二十/§三十一）

```js
async start(bot, params)   // 校验（见上表）→ 返回 {block, position, blockName}
async wait(bot, params, token, state)
    await bot.dig(state.block, true)        // forceLook：朝向由 Mineflayer 负责
    const after = bot.blockAt(state.position)
    if (after && after.name === state.blockName) throw block.break_unconfirmed
    return { position, block_before, block_after }
cleanup(bot) { bot.stopDigging(); bot.clearControlStates() }
```

* `dig` 是**持续型动作**（`detached: true`，与 move_to/follow_player 同款）：HTTP 启动即
  `RUNNING`，终态经事件；挖掘可能几秒到几十秒，绝不阻塞调用方；
* `exclusive = true`：挖掘中 `move_to` / `follow_player` / 再来一次 `dig` 都是
  `minecraft.action_busy`（不能边挖边走）；`chat`/`world`/`stop` 不受影响；
* `bot.diggingAborted` 之类的中断：被 stop/断开/超时叫停 → `CANCELLED`/`TIMEOUT`（由取消
  token 判定）；没人叫停却被打断 → `block.dig_aborted`；其它异常 → `action.failed`。
  **原始异常文本绝不外泄给模型**；
* 配置：`minecraft.action.dig.timeout`（5~120s，默认 30）与 `max_distance`（≤6，默认 5），
  随进程环境注入 runtime（`MC_DIG_TIMEOUT_MS` / `MC_DIG_MAX_DISTANCE`）。

## 六、Action 生命周期与 STOP（§十一/§二十一）

`QUEUED → RUNNING → (SUCCEEDED | FAILED | CANCELLED | TIMEOUT)`，终态正好一个事件。
用户说「停」→ `minecraft_stop` → `bot.stopDigging()` + `clearControlStates()` → `CANCELLED`，
**方块不会被继续破坏**（真实服务器 smoke 里逐块验证）。timeout / disconnect / shutdown
同样走 cleanup（Phase 3B.1 的语义，至多一次）。

## 七、WorldPerception 联动（§二十四/§五十二）

挖掉之后不是只有 "Action completed"：近层扫描（表层柱面）下一次就会看到那一柱消失，
`world.changed` 的 `changed_blocks >= 1`（阈值 1 时）。Python 侧新增测试用「挖前/挖后」两份
raw snapshot 验证这条链路；flying-squid E2E 里则在真实世界验证「方块真的没了 + 感知层同步消失」。

## 八、WebUI（§三十六/§三十七）

连接页新增 **Dig Test** 面板：`X / Y / Z / Expected Block` + `[DIG]` `[STOP]`。

* 走正式链路（`POST /api/v1/minecraft/dig` → bridge 的开发者判定 → **确认门** → Service），
  不直接碰 Mineflayer；
* §三十七：WebUI 可以跳过"这一轮是不是用户对话"的判断（开发者直接调用动作），
  但**不能**跳过 MEDIUM 确认 —— 点 DIG 只会得到 `409 minecraft.confirmation_required`
  与 `detail.confirmation`，页面上方 Pending Confirmation 面板会列出这条待确认；
* 管理台按钮仍然只有 `CREATE TEST / CANCEL / EXPIRE`，**没有** confirm/consume。

## 九、测试

| 文件 | 覆盖 |
|---|---|
| `minecraft_runtime/test/dig.test.js`（54 checks） | 注册表属性（exclusive / MEDIUM / 30s / detached / start+wait / cleanup）、禁止清单（mine/place/attack… 都没有）、参数校验、start 阶段六类拒绝（含 `block.changed` 的 expected/actual）、wait 阶段复核（仍在 / 变空 / 中断 / 异常）、cleanup 安全性 |
| `tests/test_minecraft_dig.py`（12） | 服务层：启动即 RUNNING、坐标/expected_block 校验（含控制字符与长度）、六种错误翻译（`block.changed` 带 detail）、离线/忙/runtime down、配置注入（超时/距离 → 环境变量）、配置边界 |
| `tests/test_minecraft_agent_confirm_gate.py`（24） | 确认门跑在**真实** dig 上：需要确认→确认→执行→再要确认；非用户回合不创建也不消费；参数/方块名变化 → mismatch（作废重挂）；会话绑定；过期；`allow_medium=false` 与离线先于确认；dig 与前台动作互斥；不可信玩家不能 dig；不自动连续挖；确认摘要写清方块与坐标；生产注册表正好七个 Tool |
| `tests/test_minecraft_dig_flow.py`（15） | 自然语言闭环（§六十四 Test 1–5）：第一次只得到「需要确认」、确认后执行、过期/参数变化/不可信玩家都不执行；单回合不能连挖两块；主动回合不能 dig；缺 `expected_block` 被 schema 拒；挖成功后 `activity = 刚挖掉了 minecraft:stone`；**感知层真的看到方块消失**（world.changed + 合并视图里不再有那一柱） |
| `tests/test_web_api_minecraft.py` | dig 端点：参数非法 422 且不挂确认、第一次 409 `confirmation_required`（带 `detail.confirmation`）、没有 confirm/consume、`create_test` 拒绝正式动作 |
| `webui/src/pages/__tests__/minecraft.spec.ts` | Dig 面板：提交 `expected_block`、缺方块名本地拦截、被确认门拒绝时如实报错（不假装成功）、离线禁用 |
| `minecraft_runtime/test/e2e.js`（flying-squid） | Test A 真挖（dirt → air + 感知层同步 + 同位置再挖 `block.not_found`）、Test B `block.changed`（带 expected/actual，未破坏）、Test C 空气 → `block.not_found`、Test D 太远 → `block.too_far`（不导航） |
| `minecraft_runtime/test/smoke_real_server.js` | 真实服务器（§七十三-§七十五）：`block.changed` → 真挖 → 事件 → 感知 → 同位置再挖；STOP 段「挖掘中叫停，方块仍在」。**需要操作者启动 127.0.0.1:25565 的服务器才能跑** |

### flying-squid 的边界（为什么 STOP/超时不在 E2E 里）

flying-squid 收到挖掘包就**立刻**破坏方块（不模拟挖掘耗时，实测 dirt deleted 用时 4ms），
所以"挖掘中 STOP / 超时 / 断开"在这台假服务器上没有可观测窗口。这三条由三层覆盖：
① `dig.test.js`（start/wait/cleanup 语义与复核）；② `action_runtime.test.js`
（CANCELLED/TIMEOUT/断开取消 + cleanup 至多一次，用慢动作假动作）；③ 真实服务器 smoke。

## 十、真实 Minecraft Java Server Smoke（§三十九-§四十二/§七十三-§七十五）

```bash
cd minecraft_runtime && node test/smoke_real_server.js     # 需要 127.0.0.1:25565 已开
```

脚本会（服务器不在线时如实打印 `REAL SERVER: NOT AVAILABLE` 并以 0 退出）：

1. 进世界 → 从近层快照里挑一个**徒手可挖**的方块（dirt/grass/sand/gravel/clay/snow/oak_log）
   且距离 ≤4；
2. 故意报错方块名 → 断言 `block.changed` 带 `expected/actual`，且方块原地未动；
3. 用正确方块名真挖 → 等 `completed` 事件 → 断言 `block_before`/`block_after` → 等感知层看到变化
   → 同位置再挖得到 `block.not_found`；
4. 挑一个"徒手要挖几秒"的方块（oak_log/stone/cobblestone/andesite/coal_ore）→ 启动 dig →
   `stop` → 断言 `cancelled` 含该 action_id → 800ms 后方块**仍在**；
5. move_to / follow_player 的老用例也按持续型语义更新了（启动即 RUNNING + 事件）。

**本次执行状态**：`[smoke] REAL SERVER: NOT AVAILABLE（127.0.0.1:25565 无响应）` ——
与 Phase 3C/3D 一样，真实服务器门禁需要操作者把局域网世界开起来再跑一次；
在此之前 4B 的"真实验证"证据来自 flying-squid E2E（真实 Mineflayer + 真实世界状态）。

## 十一、性能观察（§五十三）

* dig 只读**那一个**位置（`blockAt`），不重扫世界、不建全局地图；
* 感知层照旧按分层节拍轮询，raw snapshot（45KB）永不进 LLM；
* LLM 上下文仍是"每轮一行"（≈150–240 字符）；挖成功后的 `activity` 变成
  「刚挖掉了 minecraft:stone」，随时可以被下一轮引用；
* dig 结果（`position/block_before/block_after`）只进 Agent Context 的 `last_action`，
  不写长期记忆（§五十四/§五十五：这一阶段不做 Minecraft 长期记忆）。

## 十二、已知限制

1. **只有单方块**：没有连续挖掘、矿物搜索、自动换目标（Phase 4C+ 再说）；
2. **不导航**：目标必须在 5 格内（眼睛→方块中心），超出直接拒绝；
3. **不换工具**：用当前手持的工具挖，挖不动就失败（stone 徒手可挖但没有掉落物）；
4. **不捡掉落物**：破坏就是破坏（掉落物留在原地，`inventory` 属于后续阶段）；
5. **不跨维度**：只能在当前维度挖；
6. **确认只能在对话里做**：WebUI 的 DIG 按钮只能发起（拿到 `confirmation_required`），
   真正的确认必须由用户在新的对话回合里说「确认」；
7. **flying-squid 上的挖掘是瞬时的**：STOP/超时/断开中的 dig 只能在真实服务器上验证；
8. `allow_medium` 默认 **false**：不显式打开，`minecraft_dig` 在配置层就被拒。

## 十三、Phase 4C readiness

`minecraft_place`（放置一个指定方块）可以完全照抄本阶段的结构：

1. runtime 注册 `place`（`start` 校验：目标位置是空气/可替换方块、手上有方块、距离、朝向面）；
2. `ACTION_RISK["minecraft_place"] = "MEDIUM"` + 一个 Tool 类（复用 `ActionTool` 骨架）；
3. 确认门、可信门、事件回流、WebUI 面板、错误码词表**都不用改**；
4. 新增的错误码按 `block.*` 惯例扩展（例如 `minecraft.item_not_held` / `minecraft.target_occupied`）。

真正需要新增的能力在 Phase 4C：**手上物品**（`inventory` 的只读面）与"放置面"的选择
（`bot.placeBlock(block, faceVector)` 需要邻接面）。建议同样先只做"放一个方块"，
验证 `用户 → 确认 → 放置 → 世界改变 → 重新感知`，再考虑 dig+place 的组合。
