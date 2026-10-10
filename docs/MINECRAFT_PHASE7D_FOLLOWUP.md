# CatooBot Phase 7D Follow-up — Minecraft Dig Attribution（根因修复记录）

> **本文件是独立记录**：Phase 7D §10.4、Phase 7E 的历史结论**一字未改**，只在它们的
> 相应位置补了一行指向本文件的收口说明。基线 commit `73bfae5`（7E.1.2）。

## 1. 根因（先说清"错在哪"）

`minecraft_runtime/node_modules/mineflayer/lib/plugins/digging.js`（mineflayer **4.39.0**，
`minecraft-protocol` **1.68.0**）里，`bot.dig()` 的结束判据是：

```js
function onBlockUpdate (oldBlock, newBlock) {
  if (newBlock?.type !== 0) return          // 只看目标坐标是不是变成了 air
  bot.removeListener(eventName, onBlockUpdate)
  ...
  bot.emit('diggingCompleted', newBlock)     // 没有时间校验、没有归属校验
  diggingTask.finish()
}
```

即 **任何**让目标坐标变成 air 的来源（别的玩家挖掉、`/setblock`、世界编辑、服务器插件）
都会让 `bot.dig()` 正常 resolve。7D 的 `wait()` 复核只验「方块不再是原来的方块」，
于是外部移除被当成"自己挖完了" —— 这就是 §10.4 第 2 条记录的假成功。

**客户端协议的能力边界（必须承认）**：没有"这个方块是我破坏的"这种回执。每个坐标变化
都只是一次 `block_update`。所以本阶段**不试图证明过程**，而是：

1. 把「世界发生了什么」与「现有证据归给谁」拆成两个**互相独立**的结论；
2. 只在**有真实执行归属证据**时，才把方块消失记成"罐头亲手挖的"；
3. 证据不足一律 `AMBIGUOUS`（既不当成功、也不当反例），并留下稳定原因码。

## 2. 归因模型（两条互不推出的轴）

| 轴 | 取值 | 含义 |
| --- | --- | --- |
| `world_effect` | `BLOCK_REMOVED` / `BLOCK_REMAINS` / `UNKNOWN` | **世界**发生了什么（原方块已经不在 / 还在原位 / 读不到）。刻意**没有**"外部移除"这种值：方块消失只说明世界变了。 |
| `attribution` | `SELF_CONFIRMED` / `EXTERNAL_INDICATED` / `AMBIGUOUS` | 现有证据把这次变化**归给谁**。刻意**没有**过强的 `EXTERNAL_CONFIRMED`。 |

自证依据（payload 的 `confirm_basis`，强弱分两级，审计字段 `strict_self_proof` 如实标注）：

* `self_break_progress` —— **正面证据**：按坐标过滤后，观察到**罐头自己**实体在目标坐标的
  破坏进度包（`blockBreakProgressObserved` 的 `entity` 就是自己）→ `strict_self_proof = true`；
* `dig_lifecycle_timing` —— **时序推断**：自己的挖掘生命周期在目标坐标走完
  （`diggingCompleted`，其语义就是"我们的挖掘任务还在进行时该坐标变成了 air"），
  且这次变化发生在**我们自己发出完成包的时刻之后**（`removal.ratio >= 0.85`），
  并且没有观察到任何外部破坏信号。

判定顺序（`resolveAttribution`，纯函数）：

1. 世界效果不是 `BLOCK_REMOVED` → `AMBIGUOUS`（`block_still_present` / `world_effect_unknown`）；
2. 观察到**别人**在目标坐标的破坏进度 **且** 我们自己也按时挖完 → `AMBIGUOUS` / `conflicting_evidence`（**不站队**）；
3. 只观察到别人的破坏进度 → `EXTERNAL_INDICATED`（带实体 id/名字，`entity` 解析不出来也照样算外部）；
4. 观察到自己的破坏进度 → `SELF_CONFIRMED` / `self_break_progress`（严格自证）；
5. 自己的完成回执 + `ratio >= 0.85` + 无外部信号 → `SELF_CONFIRMED` / `dig_lifecycle_timing`；
6. 其余（没有完成回执 / 变化早于本次动作开始 / 明显早于预期完成时刻 / 拿不到预期时长）→ `AMBIGUOUS` + 稳定原因码
   （`no_self_dig_completion_observed`、`block_removed_before_self_dig_started`、
   `block_removed_before_self_dig_completion`、`expected_dig_time_unknown`）。

> **为什么 0.85 是因果分界而不是拍脑袋的比例**：mineflayer 的 `finishDigging()` 在
> `waitTime = bot.digTime(block)` 时才发出 `block_dig status=2`（并做本地乐观更新）——
> 也就是说"我们自己动手的时刻"是**可知**的；早于它的坐标变化只可能来自我们之外的力量。
> 7D.2 那次事故的变化系数是 **0.78**，落在阈值之下 → 如实判歧义。
> 预期时长**在动作开始那一刻**重新算（`bot.digTime`，与 `bot.dig` 内部同一个函数同口径），
> 不是沿用计划期的旧探测值。

## 3. Node 端改动（根因修复）

* 新增 `minecraft_runtime/dig_attribution.js`：证据采集器 + 纯判定。
  * **作用域**：按 `action_id` + 目标坐标 + 时间窗绑定；只监听本次动作，绝不跨动作/跨重连；
  * **坐标过滤**：`blockUpdate:<目标坐标>`、`diggingCompleted/Aborted`、
    `blockBreakProgressObserved/End` 全部按坐标比对；
  * **服务器真包**：额外听 `bot._client` 的 `packet` 事件里 `block_update`/`block_change`
    （`meta.name` 匹配，兼容 `packet_` 前缀与版本改名）→ 记住"服务器亲口说该坐标变了"，
    与本地乐观更新分开记（`removal.source = server|local_or_server|none`）；
  * **零泄漏**：`detach()` 幂等，`wait()` 的 `finally` 与 `cleanup()` 都会摘监听器
    （取消/超时/断开/退出全覆盖）；摘掉之后迟到/重复事件一律忽略；证据条数有上限（12）。
* `minecraft_runtime/runtime.js` 的 `dig`：
  * `start()` 建采集器（`action_id` / 坐标 / `bot.digTime` 预期时长 / 会话 id / 维度 / 自己的实体 id）并挂监听；
  * `wait()` 出口先判定再摘监听，结果里新增 `attribution`（世界效果 + 归属 + 依据 + 原因码 + 证据摘要），
    **原有字段一个都没动**（`block_before/block_after/tool_*` 逐一保留）；
  * `block.break_unconfirmed` 失败分支也带上归因（`detail.attribution`）；
  * `cleanup()` 额外摘监听（`controller.digAttribution`），`stopDigging` 行为不变；
  * **不缩短任何等待、不加睡眠、不改成功判据、不动风险/授权/确认/独占/超时**。

## 4. Python 端改动（归因进资格门与记忆）

* `app/tasks/skill_learning.py`
  * 新增纯读取器 `dig_attribution()` / `dig_attribution_reason()` / `self_dig_confirmed()`：
    只认 `step.result["attribution"]`（dict 或 JSON 文本），缺字段/坏结构 → `None`（fail-closed），
    并校验**归因载荷属于这一步**（`action_id` 非空且与步骤不一致 → `action_id_mismatch`）；
  * 资格门新增：`minecraft_dig` 步骤必须有 `world_effect == BLOCK_REMOVED` **且**
    `attribution == SELF_CONFIRMED`，否则 `dig_attribution_unproven:<step_id>`（`AMBIGUOUS` 证据，只隔离不计分）；
  * 状态、后置条件（`block_absent`）、`inventory_delta` 仍是**效果证据**，不再单独充当挖掘的正向依据；
  * 旧的"实测时长不到预期一半 → `ambiguous_world_change`"**保留但降级为辅助**（不再是归因证明）。
* `app/integrations/minecraft/memory_bridge.py`
  * `_remember_task_target()` 改成**逐挖掘步骤独立判定**；只有
    「步骤 `SUCCEEDED`」+「`world_effect == BLOCK_REMOVED`」+「`attribution == SELF_CONFIRMED`」
    三条同时成立，才写 `TASK_RESULT` 来源的 RESOURCE 事实（"她亲手挖过"）；
    任务经验（TASK 事实）照写 —— "做过什么"仍然成立。
* **没有新增表、没有新增迁移**（最高版本仍 **36**）；没有新增工具（19 个）、没有新增动作、
  没有新增 TaskRuntime 状态；`allow_medium` 默认仍 `False`；`ACTION_RISK` 未改。

## 5. 验收矩阵（任务书 §8 的逐行落点）

| # | 场景 | 判定 | 落点 |
| --- | --- | --- | --- |
| 1 | 自己挖（按时完成，无外部信号） | `SELF_CONFIRMED` / `dig_lifecycle_timing` | Node 单测（判定+接线）、真实运行时端到端 |
| 2 | 观察到**自己**的破坏进度 | `SELF_CONFIRMED` / `self_break_progress`（`strict_self_proof=true`） | Node 单测 |
| 3 | 别人在挖同一个坐标（无自己完成） | `EXTERNAL_INDICATED` + 实体身份 | Node 单测（合成动画事件）+ 判定矩阵 |
| 4 | 别人在挖 + 自己也按时挖完 | `AMBIGUOUS` / `conflicting_evidence` | Node 单测 |
| 5 | 方块在**开始前**就变了 | `AMBIGUOUS` / `block_removed_before_self_dig_started`（start 校验另给 `block.not_found`） | Node 单测 + 既有 e2e Test C |
| 6 | **挖到一半**被外部移除（7D.2 形状，0.78） | `AMBIGUOUS` / `block_removed_before_self_dig_completion`（payload 保留 `ratio`） | Node 单测 + 接线测试 |
| 7 | 临近完成但早于自证下限 | `AMBIGUOUS` / `block_removed_before_self_dig_completion` | Node 单测（阈值边界） |
| 8 | 方块没了但**没有任何**归属证据 | `AMBIGUOUS` / `no_self_dig_completion_observed` | Node 单测 |
| 9 | 方块仍在原位 / 读不到 | `AMBIGUOUS` + `BLOCK_REMAINS` / `UNKNOWN` | Node 单测 + `block.break_unconfirmed` 分支带归因 |
| 10 | 拿不到预期时长 | `AMBIGUOUS` / `expected_dig_time_unknown` | Node 单测 + flying-squid E2E 实测就是这一支（假服务器算不出 `digTime`） |
| 11 | 只有背包变化（`inventory_delta`） | 不足以为挖掘背书 | Python：效果证据齐全 + 外部归因 → 仍 `dig_attribution_unproven` |
| 12 | 只有别的方块变化 | 坐标过滤 → 不产生证据 | Node 单测（别的坐标/别的包不记账） |
| 13 | 重复 / 迟到 / 过期事件 | 不覆盖、不重复记账、不复活 | Node 单测（detach 后忽略 + 条数上限）+ Python 重复终态事件 + 证据幂等 |

## 6. 本轮实际执行的门禁（串行）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 632 files already formatted |
| `mypy app` | Success: no issues found in 335 source files |
| `pytest tests -q` | 见 §8 报告（基线 3561 → 新增 `tests/test_dig_attribution.py` 24 项） |
| WebUI typecheck / Vitest / build / 浏览器 E2E | 通过 / 528 passed / 通过 / 7 passed |
| Minecraft runtime Node 单测（16 个文件，含新增 `dig_attribution.test.js`） | 全部 OK |
| flying-squid E2E | `ALL CHECKS PASSED`（**attempt 1 在 dig 夹具放置处超时**＝已知的 E2E 抖动，重跑即绿；本轮归因断言实测输出 `AMBIGUOUS/expected_dig_time_unknown`） |
| 范围核对 | 迁移最高版本 **36**；`ACTION_RISK` 19 个工具；无新动作/新状态；`config/`、`.env`、`config/overrides.yaml` 未触碰 |

## 7. 真实 Java 服务器门禁 —— `SKIPPED`（环境所限，如实记录）

* 目标服务器 `127.0.0.1:25565`（`D:\Minecraft server\1.21.1`）是 **`online-mode=true`**；
  本机 bridge 的认证是 `offline`（`minecraft_runtime/auth.json`），一登录就被服务器踢：
  **`multiplayer.disconnect.unverified_username`**。
* 于是本阶段准备了取证探针 `minecraft_runtime/test/probe_dig_attribution_real.js`
  （S 自己挖 / E 挖到一半被 `/setblock ... air` / X 第二个真实客户端挖同一方块），
  实跑时**按设计**输出「连接失败，跳过（不伪造结论）」并以 0 退出 —— **没有**把它当成真实结论。
* 想真跑的三条路（都不由本阶段擅自改环境）：改用局域网开服（默认 offline）、
  把服务器改成 `online-mode=false` 后重启、或另备一份 microsoft 认证的 auth 文件
  （用 `MC_AUTH_FILE` 指过去）。
* 因此：**"真实服务器上的自挖是否带正面进度证据 / 双客户端是否会出现外部动画"这两件事，
  仍是未验证的假设**，本阶段不写成 PASS。
* flying-squid 假服务器**不能**替代这一项：它收到挖掘包就立刻破坏方块（没有任何动画），
  也没有第二个真实玩家 —— 用它"证明"外部动画路径就是伪造结论。

## 8. 结论：`PARTIAL`

* **已消除无证据误归因**：方块消失只证明世界变了；外部（别人的破坏进度）有正面证据；
  说不清的一律 `AMBIGUOUS` 且带稳定原因码；技能资格门与记忆桥都只认 `SELF_CONFIRMED`；
  7D.2 那种"挖到一半被外部弄没"的样本不再可能被记成罐头亲手挖的。
* **严格自挖确认仍受客户端协议能力限制**：现有客户端只有"自己的挖掘生命周期 + 时序"
  这一条可用依据（`dig_lifecycle_timing`），它是**推断**而非过程回执；
  理论上仍存在一个无法区分的窗口 —— 一次**迟于**我们自己完成时刻、且不发动画的
  服务器侧改动，会被算作自证。payload 如实保留 `confirm_basis` 与
  `flags`/`strict_self_proof`，让上层按需要提高门槛（例如只接受 `strict_self_proof`）。
* **真实 Java 门禁未执行**（§7，环境所限），因此本轮不给 `PASS`。
