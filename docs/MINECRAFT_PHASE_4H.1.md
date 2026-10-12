# CatooBot Minecraft Phase 4H.1 — Move_to Reliability Hardening（消除 Pathfinder 假成功）

> **不是新能力**：这是基础设施可靠性修复。`minecraft_move_to` 的 `SUCCEEDED` 从此不能由
> `bot.pathfinder.goto()` 的 Promise resolve 单独决定。

## 一、根因（真机确认）

```text
远距离 move_to
→ HTTP RUNNING
→ （2.4.5）goto() 很快 resolve
→ ActionRuntime SUCCEEDED
→ 但 final_position 距离目标约 28 格（有时干脆是原点，位移 0）
```

`mineflayer-pathfinder` **2.4.5** 的 `lib/goto.js`：

```js
function noPathListener (results) {
  if (results.path.length === 0) {
    cleanup()                        // ← 空路径直接 resolve（成功！）
  } else if (results.status === 'noPath') {
    cleanup(error('NoPath', '...'))  // ← 真正该报错的顺序排在后面
  }
  ...
}
```

也就是说：**"没有路径"会被当成"到了"**。`move_to` 撞上这条路时报告成功，而罐头一步没走。

## 二、修复（生产 runtime，不是 smoke workaround）

`minecraft_runtime/runtime.js` 的 `move_to.wait()` 不再调用 `goto()`：

1. **先挂监听、再 setGoal**（§七）——`goal_reached` / `path_update` / `goal_updated` /
   `path_stop` 四个事件全部就位之后才 `bot.pathfinder.setGoal(state.goal)`，
   否则第一个事件会丢，动作永远不会落终态；
2. 到达判定由 **CatooBot 自己**用**重新读取的实际位置**做（§五/§八/§十五），
   绝不相信 Pathfinder 的说法；
3. 距离口径与 `GoalNear` 完全一致：`GoalNear` 构造时就把目标取整成方块格
   （`this.x = Math.floor(x)`），`isEnd` 比的也是 `bot.entity.position.floored()` ——
   所以门禁比的是"**罐头现在占的方块格 → 目标方块格**"。原始浮点三维距离
   （含站在方块顶上的高度差）另算一份 `raw_distance_to_target`，只做诊断；
4. 结果里带 `target` / `final_position` / `distance_to_target` / `raw_distance_to_target`
   （§十九：都来自**判定瞬间**重新读到的位置，不是 Pathfinder 的预测）。

### 事件语义（逐条，§九-§十七）

| 事件 | 处理 |
| --- | --- |
| `goal_reached` | **二次验证**位置：`reached` → SUCCEEDED；否则 `path.not_reached` |
| `path_update` `noPath` | FAILED `path.not_found`（**空路径也一样** —— §十一，不再静默成功） |
| `path_update` `timeout` | FAILED `path.not_found`（§十三） |
| `path_update` `partial` | 继续等（只是"先走近一点"；整体 30s 超时兜底 —— §十二） |
| `path_update` `success` + **空路径** | 只有真的在半径内才 SUCCEEDED，否则 `path.not_reached`（§十：本阶段最重要的回归） |
| `path_update` `success` + 非空路径 | 继续等 `goal_reached`（§九：绝不立即成功） |
| `path_stop` | 验证位置：在半径内 → SUCCEEDED，否则 `path.not_reached`（§十五） |
| `goal_updated` 换成别的 Goal | FAILED `goal.changed`（§十四，防御分支） |
| token 已取消（STOP / 超时 / 断开） | 一切事件都不再落终态（§十六：token 最高优先级） |

### 终态与清理

* 唯一硬门禁（§二十一）：`reached`（即 GoalNear 口径距离 ≤ `MOVE_TO_RADIUS = 1.5`），
  **与"移动了多少格"无关**；
* 失败一律**清 Goal**（`setGoal(null)`）——`SUCCEEDED`/`FAILED` 都不触发 ActionRuntime 的
  cleanup（既定契约，Phase 4H 的教训），所以 `wait` 自己收；`cleanup` 只负责
  STOP / 超时 / 断开，并顺手摘掉挂在 `controller.moveListeners` 上的监听器
  （复用 ActionRuntime 已有的 `cleaned` 标记，没有第二套 cleanup 状态 —— §二十四）；
* `MOVE_TO_RADIUS = 1.5`、`MOVE_MAX_DISTANCE = 64`、`MOVE_TIMEOUT_MS = 30000` **都没有改**（§二十二/§二十三）；
* **依赖没有动**：`mineflayer-pathfinder` 仍然锁 2.4.5（§三/§三十六）——我们是在自己的
  runtime 里加防御层，不是靠升级上游。

## 三、新错误码（结构化失败，§二十）

| 错误码 | HTTP | 何时 | `detail` |
| --- | --- | --- | --- |
| `path.not_found` | 500 | 明确没有路径（`noPath` / 路径搜索 `timeout`） | `target` / `actual` / `distance_to_target` / `radius` / `goal_reached` / `reason` |
| `path.not_reached` | 500 | Pathfinder 结束了（含空路径静默 resolve），但**实际位置不在到达半径内** | 同上 |
| `goal.changed` | 409 | Goal 被换成别的（防御分支） | 同上 |

`reason` 取值：`no_path` / `path_search_timeout` / `goal_reached_without_arrival` /
`empty_path_without_arrival` / `path_stopped_short` / `goal_changed`。

对外（LLM / WebUI）稳定码：`path.not_reached` → **`minecraft.path_not_reached`**
（`_TOOL_STATUS` 500，契约表已同步）；`path.not_found` 保持 `minecraft.path_not_found`。

## 四、验证

* **Node 单测** `move_to.test.js`：**25 → 76 checks**。新增的是真正的 ActionRuntime 行为测试
  （假 pathfinder 会发真事件，不再是 `goto = Promise.resolve()`）：

  | 用例 | 内容 |
  | --- | --- |
  | A / A2 | 正常到达 → completed（结果形状 + 清 Goal + 摘监听）；`goal_reached` 却还在 28 格外 → `path.not_reached` |
  | B | **核心回归**：`path=[] + status='success'` + 实际 28 格 → **绝不 SUCCEEDED**，`path.not_reached` |
  | C | `path=[] + success` 且真的在半径内 → completed（"已经在目标"仍然算到达） |
  | D | `path=[] + noPath` / 非空 path + noPath → `path.not_found` |
  | E | `status='timeout'` → FAILED |
  | F | `status='partial'` → 不落终态，等到 `goal_reached` 才成功 |
  | G | `path_stop`：远处 → `path.not_reached`；半径内 → completed |
  | H | 换 Goal → `goal.changed`（且自己 setGoal 发的那次不会误判） |
  | I / K | STOP 竞态：只有一个终态（CANCELLED），cleanup 恰好一次，晚到的 `goal_reached` 不翻案 |
  | J | 超时后晚到的 `goal_reached` 仍然是 TIMEOUT；先到则 completed |
  | L | 距离口径（GoalNear 口径 vs 浮点）与负数坐标取整 |

* **flying-squid E2E**：Test A 的容差从 2.5 **收紧到 1.5**（completed 现在保证在半径内），
  新增 **Test D（false-success guard）**：对头顶 40 格空气里的目标发 move_to →
  必须**不是** completed，且终态之后 `goal == null`、无僵尸动作。
  实测输出：`[e2e] move_to false-success guard ✓ minecraft.action.failed/path.not_found`。
* **Python**：`tests/test_minecraft_actions.py` 新增 `path.not_reached` 的翻译测试，
  `tests/test_web_api_minecraft.py` 新增 Service↔API 错误码一致性测试；pytest **2289**。
* **真机 smoke**（1.21.1 + Fabric，`REAL SERVER: PASS`）：

```text
✓ move_to positive → RUNNING → completed（4,0 方向）
✓ positive final distance <= 1.5（GoalNear 口径 1 格，原始浮点 1.17 格）
✓ move_to positive → goal cleanup = PASS（成功后 goal=null / isMoving=false）
✓ move_to positive → 独立量测也同意到达（实际 1.17 格）
✓ STOP 取消了移动中的 move_to
✓ move_to 终态 = cancelled（minecraft.action.cancelled）
✓ goal == null / ✓ isMoving == false / ✓ 停止后位置不再漂移（实测 0.00 格 / 阈值 0.6）
✓ move_to goal cleanup = PASS（STOP 段之后没有残留 Goal / 没有在移动）
✓ move_to false-success guard
  Pathfinder finished/empty-path scenario（目标：不可达）
  → NOT SUCCEEDED（minecraft.action.failed / path.not_found：路径计算超时（非破坏性））
✓ 结构化失败 = PASS（path.not_found，目标：脚下 20 格的实心岩层）
✓ 失败 detail 如实带距离（13.04 格，半径 1.5）
  → structured failure：{"target":…,"actual":…,"distance_to_target":13.04,
                         "raw_distance_to_target":12.83,"radius":1.5,
                         "goal_reached":false,"reason":"path_search_timeout"}
✓ move_to goal cleanup = PASS（失败终态之后没有残留 Goal / 没有在移动）
✓ move_to 段收尾：runtime 已 IDLE（无前台动作 / goal=null / isMoving=false）
```

  同一次运行里 Phase 4H 的 `dropped_items` / 真 STOP / `playerCollect` / 实体消失 /
  背包增加 / 逐槽签名恢复也全部 PASS。

## 五、smoke 的配套改动（都是"如实"，不是 workaround）

1. **删掉了"向当前位置发一次零距离 move_to 清残留 Goal"**（§三十三）：现在任何终态
   （成功/失败/取消）都由 runtime 自己收掉导航意图，smoke 只**验证** `goal == null`；
2. 正式加入 **REAL MOVE_TO FALSE-SUCCESS GUARD** 硬门禁（§二十七-§三十二）：
   对**真的不可达**的目标（脚下 20 格的岩层 / 斜下方 30 格岩层 / 头顶 30 格空气，
   都不改世界）发 move_to，要求终态是**结构化失败**且 `distance_to_target >= 5`
   （证明罐头确实没到）。如果某个候选其实可达（岩层里有洞穴），smoke 会用
   **自己从 /status 读到的位置**独立量测判断"是不是真的到了"，只会对
   `completed 但实际很远` 报假成功 → 这一项**不允许 SKIPPED**；
3. 正向段按方向 × 距离重试（12 个候选），全都没路径时先 `/tp` 挪到干燥落脚点再试一轮；
4. 开局用 `/spreadplayers` 把罐头放到**地表安全点**（它的位置跨会话保留，上一次可能把它
   留在洞穴/深水里，那样 dig/place/craft 夹具都会跑不动）；只动罐头自己，不改世界；
5. STOP 段的"挪远制造取消窗口"改成**挑一块离掉落物 6~12 格的干燥落脚点**
   （原来固定 `-7` 格，可能掉悬崖/落水，pickup 会以 `target_too_far` 起不来）；
6. 修掉两条**目标搞错**的旧断言：4D 的"WorldPerception 输入反映搬运结果"原来在快照 JSON 里
   搜物品名（感知层根本没有 inventory，只有 `self.held_item`），现在比对
   "感知的 `self.held_item` == 重读的真实主手"；
7. 出异常时把出错位置（栈顶 2 行）打进 smoke 日志，真机排查不用重跑。

## 六、不做的事

升级 mineflayer / mineflayer-pathfinder / minecraft-data（单独建 dependency upgrade 阶段）；
改 `follow_player` 或 `pickup_item`（它们用的是 `setGoal` + 自己轮询，从来不走 `goto`）；
新增任何 Minecraft 能力；改 `allow_medium` 默认值。
