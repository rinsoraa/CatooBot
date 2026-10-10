# CatooBot Phase 7D Follow-up — Minecraft Dig Attribution（根因修复记录）

> **本文件是独立记录**：Phase 7D §10.4、Phase 7E 的历史结论**一字未改**，只在它们的
> 相应位置补了一行指向本文件的收口说明。基线 commit `73bfae5`（7E.1.2）。
> **第二轮（2026-10-10）**：服务器 `online-mode` 已由用户改为 `false`，**真实 Java 门禁已执行**
> （§7，四个真机用例全 PASS），并因此揪出两处只有真机才能暴露的缺陷（§3.2）—— 历史内容全部保留。

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

**客户端协议的能力边界（必须承认，且决定了整个设计）**：没有"这个方块是我破坏的"这种回执。
每个坐标变化都只是一次 `block_update`/`block_change`。所以本阶段**不试图证明过程**，而是：

1. 把「世界发生了什么」与「现有证据归给谁」拆成两个**互相独立**的结论；
2. 只在**有真实执行归属证据**时，才把方块消失记成"罐头亲手挖的"；
3. 证据不足一律 `AMBIGUOUS`（既不当成功、也不当反例），并留下稳定原因码。

## 2. 归因模型（两条互不推出的轴）

| 轴 | 取值 | 含义 |
| --- | --- | --- |
| `world_effect` | `BLOCK_REMOVED` / `BLOCK_REMAINS` / `UNKNOWN` | **世界**发生了什么。刻意**没有**"外部移除"这种值：说不了"谁干的"就不说。 |
| `attribution` | `SELF_CONFIRMED` / `EXTERNAL_INDICATED` / `AMBIGUOUS` | 现有证据把这次变化**归给谁**。刻意**没有**过强的 `EXTERNAL_CONFIRMED`。 |

`world_effect` 的判据（**真机取证后收紧过一次**，见 §3.2）：

* 服务器**亲口说**该坐标变成了 air（`block_update`/`block_change` 包，`type == 0`）→ `BLOCK_REMOVED`；
* 服务器**亲口说**该坐标还是非 air（纠正包，`type != 0`）→ `BLOCK_REMAINS`（"这次破坏没生效"的正面证据）；
* 只有客户端本地视图说没了、服务器什么都没说 → `UNKNOWN`（**不知道**，绝不写成"世界变了"）；
* 本地读数与原方块一致 → `BLOCK_REMAINS`；读不到 → `UNKNOWN`。

自证依据（payload 的 `confirm_basis`，`strict_self_proof` 如实标注）：

* `self_break_progress` —— **正面证据**：按坐标过滤后观察到**罐头自己**实体在该坐标的破坏进度包；
* `dig_lifecycle_timing` —— **时序 + 服务器确认**：自己的 `diggingCompleted` 在该坐标走完、
  变化发生在**我们自己发出完成包之后**（`removal.ratio >= 0.85`）、**服务器确认变成 air**、
  且没有观察到任何外部破坏信号。

判定顺序（`resolveAttribution`，纯函数）：

1. 世界效果不是 `BLOCK_REMOVED` → `AMBIGUOUS`（`block_still_present` / `world_effect_unknown` /
   `block_change_not_confirmed_by_server`）；
2. 观察到**别人**在该坐标的破坏进度 **且** 我们自己也按时挖完 → `AMBIGUOUS` / `conflicting_evidence`（**不站队**）；
3. 只观察到别人的破坏进度 → `EXTERNAL_INDICATED`（带实体 id/名字）；
4. 观察到自己的破坏进度 → `SELF_CONFIRMED` / `self_break_progress`（`strict_self_proof = true`）；
5. 自己的完成回执 + `ratio >= 0.85` + 服务器确认 air + 无外部信号 → `SELF_CONFIRMED` / `dig_lifecycle_timing`；
6. 其余（没有完成回执 / 变化早于本次动作开始 / 明显早于预期完成时刻 / 拿不到预期时长）→ `AMBIGUOUS` + 稳定原因码。

> **为什么 0.85 是因果分界而不是拍脑袋的比例**：mineflayer 的 `finishDigging()` 在
> `waitTime = bot.digTime(block)` 时才发出 `block_dig status=2` —— "我们自己动手的时刻"是**可知**的；
> 早于它的坐标变化只可能来自我们之外的力量。7D.2 那次事故的变化系数是 **0.78**；
> **真机实测**（§7）真实自挖的系数是 **1.000/1.001**，被 `/setblock` 中途改掉的系数是 **0.11**，
> 另一个真实玩家抢先挖掉的系数是 **0.69** —— 阈值两边都留了很大余量。
> 预期时长**在动作开始那一刻**用 `bot.digTime`（与 `bot.dig` 内部同一个函数、同口径）重新算。

## 3. Node 端改动（根因修复）

### 3.1 主体

* 新增 `minecraft_runtime/dig_attribution.js`：证据采集器 + 纯判定。
  * **作用域**：按 `action_id` + 目标坐标 + 时间窗绑定；只监听本次动作，绝不跨动作/跨重连；
  * **坐标过滤**：`blockUpdate:<目标坐标>`、`diggingCompleted/Aborted`、
    `blockBreakProgressObserved/End` 全部按坐标比对；
  * **服务器真包**：听 `bot._client` 的 `packet` 事件里 `block_update`/`block_change`
    （`meta.name` 匹配，兼容 `packet_` 前缀与版本改名）→ 命令行级地记住"服务器亲口说了什么"；
  * **零泄漏**：`detach()` 幂等；摘掉之后迟到/重复事件一律忽略；证据条数上限 12。
* `minecraft_runtime/runtime.js` 的 `dig`：
  * `start()` 建采集器（`action_id` / 坐标 / `bot.digTime` 预期时长 / 会话 id / 维度 / 自己的实体 id）并挂监听；
  * `wait()` 出口：先把 `bot.dig()` 的 result/failure 解释清楚，**再等服务器确认**，最后判定并摘监听，
    结果里新增 `attribution`（**原有字段一个都没动**：`block_before/block_after/tool_*` 逐一保留）；
  * `block.break_unconfirmed` 失败分支也带归因（`detail.attribution`）；
  * `cleanup()` 额外摘监听（`controller.digAttribution`），`stopDigging` 行为不变；
  * **不缩短任何等待、不加睡眠、不改成功判据、不动风险/授权/确认/独占/超时**。

### 3.2 真机逼出来的两处修正（第一版设计**不够**，这里如实记录）

**修正 A —— 监听器必须最后摘，而且必须等"服务器自己的"方块变化包。**
第一版在 `bot.dig()` resolve 的瞬间就摘监听，于是"服务器确认"永远拿不到；
而 mineflayer 的 `finishDigging()` 会做**本地乐观更新**（`bot._updateBlockState(pos, 0)`），
所以"本地读出来是 air"根本不是世界证据。现在 `wait()` 在判定前有一个**有界真证据窗口**
（`MC_DIG_CONFIRM_WAIT_MS`，默认 3s，且用 `digConfirmBudgetMs()` 扣掉动作自身 timeout 的余量，
绝不让"等证据"把一次成功挖掘逼成超时）；真机实测服务器确认只比本地更新晚 **45ms**（§7 S 用例）。

**修正 B —— 服务器说"还是方块"必须当成"没有发生移除"。**
第一版把"收到任何该坐标的服务器包"当作移除确认，真机立刻打脸：非 op 客户端在
`spawn-protection` 范围内挖掘时，服务器**拒绝**这次破坏并回一个 `type != 0` 的纠正包，
而客户端本地却已经乐观地变成了 air。现在区分 `serverSaysAir`（→ `BLOCK_REMOVED`）与
`serverSaysPresent`（→ `BLOCK_REMAINS`），`strict_self_proof` 也要求 `serverSaysAir == true`。
这一条把**"客户端自以为挖完了、服务器根本没让破坏生效"**这类假成功也堵住了（§7 P 用例）。

## 4. Python 端改动（归因进资格门与记忆）

* `app/tasks/skill_learning.py`
  * 纯读取器 `dig_attribution()` / `dig_attribution_reason()` / `self_dig_confirmed()`：
    只认 `step.result["attribution"]`（dict 或 JSON 文本），缺字段/坏结构 → `None`（fail-closed），
    并校验**归因载荷属于这一步**（`action_id` 非空且与步骤不一致 → `action_id_mismatch`）；
  * 资格门：`minecraft_dig` 正向样本必须有 `world_effect == BLOCK_REMOVED` **且**
    `attribution == SELF_CONFIRMED`，否则 `dig_attribution_unproven:<step_id>`（`AMBIGUOUS` 证据，只隔离不计分）；
  * 状态、后置条件（`block_absent`）、`inventory_delta` 仍是**效果证据**，不再单独充当挖掘的正向依据；
  * 旧的"实测时长不到预期一半 → `ambiguous_world_change`"**保留但降级为辅助**。
* `app/integrations/minecraft/memory_bridge.py`
  * `_remember_task_target()` 改成**逐挖掘步骤独立判定**；只有「步骤 `SUCCEEDED`」+
    「`world_effect == BLOCK_REMOVED`」+「`attribution == SELF_CONFIRMED`」三条同时成立，
    才写 `TASK_RESULT` 来源的 RESOURCE 事实（"她亲手挖过"）；任务经验（TASK 事实）照写。
* **没有新增表、没有新增迁移**（最高版本仍 **36**）；没有新增工具（19 个）、没有新增动作、
  没有新增 TaskRuntime 状态；`allow_medium` 默认仍 `False`；`ACTION_RISK` 未改。

## 5. 验收矩阵（任务书 §8 的逐行落点）

| # | 场景 | 判定 | 落点 |
| --- | --- | --- | --- |
| 1 | 自己挖（按时完成 + 服务器确认，无外部信号） | `SELF_CONFIRMED` / `dig_lifecycle_timing`（`strict_self_proof=true`） | Node 单测、flying-squid E2E、**真机 S** |
| 2 | 观察到**自己**的破坏进度 | `SELF_CONFIRMED` / `self_break_progress` | Node 单测 |
| 3 | 别人在挖同一个坐标（我们还没挖完） | `EXTERNAL_INDICATED` + 实体身份 | Node 单测、**真机 X（真实第二客户端）** |
| 4 | 别人在挖 + 我们也按时挖完 | `AMBIGUOUS` / `conflicting_evidence` | Node 单测 |
| 5 | 方块在**开始前**就变了 | `AMBIGUOUS` / `block_removed_before_self_dig_started`（start 校验另给 `block.not_found`） | Node 单测 + 既有 e2e Test C |
| 6 | **挖到一半**被外部移除（7D.2 形状，0.78） | `AMBIGUOUS` / `block_removed_before_self_dig_completion`（payload 保留 `ratio`） | Node 单测、**真机 E（0.11）** |
| 7 | 临近完成但早于自证下限 | `AMBIGUOUS` / `block_removed_before_self_dig_completion` | Node 单测（阈值边界） |
| 8 | 方块没了但**没有任何**归属证据 | `AMBIGUOUS` / `no_self_dig_completion_observed` | Node 单测 |
| 9 | 方块仍在原位 / 读不到 | `AMBIGUOUS` + `BLOCK_REMAINS` / `UNKNOWN` | Node 单测 + `block.break_unconfirmed` 分支带归因 |
| 10 | 拿不到预期时长 | `AMBIGUOUS` / `expected_dig_time_unknown` | Node 单测 |
| 11 | 只有背包变化（`inventory_delta`） | 不足以为挖掘背书 | Python：效果证据齐全 + 外部归因 → 仍 `dig_attribution_unproven` |
| 12 | 只有别的方块变化 | 坐标过滤 → 不产生证据 | Node 单测（别的坐标/别的包不记账） |
| 13 | 重复 / 迟到 / 过期事件 | 不覆盖、不重复记账、不复活 | Node 单测（detach 后忽略 + 条数上限）+ Python 重复终态事件 + 证据幂等 |
| 14 | **只有本地乐观更新、服务器没确认**（真机新发现） | `world_effect=UNKNOWN` + `AMBIGUOUS` / `block_change_not_confirmed_by_server` | Node 单测、**真机 P（world_effect 也不是 BLOCK_REMOVED）** |
| 15 | **服务器拒绝这次挖掘并回纠正包**（真机新发现） | `world_effect=BLOCK_REMAINS` + `AMBIGUOUS` | Node 单测、**真机 P（服务器侧复核：方块还在）** |

## 6. 本轮实际执行的门禁（串行）

| 门禁 | 结果 |
| --- | --- |
| `ruff check .` / `ruff format --check .` | All checks passed / 632 files already formatted |
| `mypy app` | Success: no issues found in 335 source files |
| `pytest tests -q` | **3585 passed**（本轮 Python 源码未再改动，故沿用同树的这次全量结果） |
| WebUI typecheck / Vitest / build / 浏览器 E2E | 通过 / 528 passed / 通过 / 7 passed |
| Minecraft runtime Node 单测（16 个文件，含 `dig_attribution.test.js`） | **全部 OK**（`dig_attribution` 62/62） |
| flying-squid E2E | `ALL CHECKS PASSED`；归因实测 `AMBIGUOUS/block_change_not_confirmed_by_server`（假服务器不回"自己的方块变化包" → 如实判未确认，**没有**伪造成自证）。**另外修了测试基建的一处脆弱**：假服务器上 `/setblock` 夹具偶发不生效（CI 负载下更明显，本轮 CI 两次都卡在 `dirt 已放置`），现在夹具放置助手会在等待期间**重发命令**直到方块真的出现（仍然是"必须真的出现"才算过，断言一条没放宽） |
| 范围核对 | 迁移最高版本 **36**（无新增）；`ACTION_RISK` 19 个工具；无新动作/新状态；`config/`、`.env`、`config/overrides.yaml` 未触碰 |

## 7. 真实 Java 服务器门禁 —— **PASS**（第二轮；第一轮为 SKIPPED，理由见 §7.3）

环境：`D:\Minecraft server\1.21.1`（Fabric 1.21.1，`127.0.0.1:25565`），用户已把
`online-mode` 改为 `false`；探针 `minecraft_runtime/test/probe_dig_attribution_real.js`
（自带独立端口的 runtime，不动用户那个；跑完清场）。

| 用例 | 场景 | 实测结论（`dig_attribution` 载荷） |
| --- | --- | --- |
| **S** | 罐头自己挖（op，`/setblock` 造的石头） | `world_effect=BLOCK_REMOVED`、`attribution=SELF_CONFIRMED`、`confirm_basis=dig_lifecycle_timing`、`reason_code=self_dig_completed_at_expected_time`、`removal.ratio=1.000`、`strict_self_proof=true`、`flags.server_block_update_says_air=true`；evidence 顺序：本地 air@7502ms → 自己完成@7502ms → **服务器确认 air@7519ms** |
| **E** | 挖到一半（800ms）被 `/setblock ... air` 改掉（7D.2 的形状，**没有**任何破坏动画） | `world_effect=BLOCK_REMOVED`、`attribution=AMBIGUOUS`、`reason_code=block_removed_before_self_dig_completion`、`removal.ratio=0.11`、`source=server` → **没有被记成自挖** |
| **X** | 第二个**真实**客户端（`CatodayoMate`）抢先挖同一个方块，我们的客户端随后开始挖 | `attribution=EXTERNAL_INDICATED`、`confirm_basis=external_break_progress`、`reason_code=external_break_progress_observed`、`removal.ratio=0.69`、`flags.external_break_entities` 里是对方用户名（6 次进度包 stage 4→9 + 2 次 end 包，实体 id 可解析） |
| **P** | 非 op 客户端在 `spawn-protection` 范围内挖掘（服务器**拒绝**这次破坏） | `world_effect=BLOCK_REMAINS`、`attribution=AMBIGUOUS`；并且**服务器侧复核**（op 客户端读同一坐标）仍然是 `stone` —— 客户端自称挖完、本地视图变 air，但系统没有把它记成"世界变了"，更没有记成自挖 |

支撑性协议诊断（同一轮真机）：

* 服务器**会**把别人挖掘的 `block_break_animation` 发给我们：旁观客户端收到 **12** 个原始包、
  mineflayer 转出 **10** 个 `blockBreakProgressObserved` 事件（所以 X 的正面外部证据路径是真实可用的）；
* op 的真实自挖：服务器确认包比本地乐观更新晚 **45ms**（S 用例 evidence 里的 7502 → 7519）；
* 非 op 在保护范围内挖掘：**永远**收不到自己坐标的服务器包，且世界侧方块不变。

### 7.3 第一轮为什么是 SKIPPED（历史保留）

第一轮（同一天早些时候）服务器还是 `online-mode=true`，本机 bridge 是 offline 认证，
一登录就被踢 `multiplayer.disconnect.unverified_username` → 探针按设计输出
"连接失败，跳过（不伪造结论）"。用户随后把 `online-mode` 改为 `false`，本阶段**没有**擅自改
任何服务器玩法配置；为跑门禁所做的**唯一**服务器侧改动是把两个测试用账号
（`Catodayo` / `CatodayoMate`）按**离线 UUID** 写进 `ops.json`（原文件已备份为
`ops.json.bak-p7df`），并重启过一次服务器（当时无任何玩家在线；探针自身没有改动过世界，
临时方块全部清成 air）。

## 8. 结论：`PARTIAL`（真机门禁 A–D 全 PASS，残余限制写明）

* **无证据误归因已消除**（真机 + 单测双重证据）：方块消失只证明世界变了；只有**服务器确认变成 air +
  自己的完成回执 + 时序一致 + 无外部信号**才算自挖；别人的破坏有正面证据（实体身份）；
  "服务器拒绝了这次挖掘"（本地乐观更新骗人）也已被识别为 `BLOCK_REMAINS`；
  说不清的一律 `AMBIGUOUS` 且带稳定原因码。7D.2 那种"挖到一半被外部弄没"的样本在真机上
  已被正确判为歧义。
* **残余限制（客户端协议层面）**：仍然没有"这个方块是我破坏的"回执。自证靠"我们自己发出完成包
  的时刻"这个因果分界 + 服务器对**该坐标**的 air 确认；理论上仍存在一个极窄窗口 ——
  一次**迟于我们完成时刻、且不发动画**的服务器侧（或第三方）改动，会与"我们自己挖完"
  无法区分。payload 如实保留 `confirm_basis` / `strict_self_proof` / `flags`，
  上层可随时把门槛提高到"只接受 `strict_self_proof`"。
* 因此按任务书的三态定义，本阶段给 **`PARTIAL`**（真机门禁已 PASS；给 PASS 会掩盖上面这条
  协议固有的窄窗口）。

## 9. 交付（镜像与提交）

| 项目 | 值 |
| --- | --- |
| 工作区 | `E:\WorkSpace ZCode\CatooBot`（改前基线 `73bfae5`） |
| 第一轮镜像（真机门禁前） | 代码 `eb139e1`（16 文件）+ 标签 `1af16ea` + 文档 `fa8478b`、`65e24f1` |
| 第二轮镜像（真机门禁 + 修正 A/B + e2e 断言 + 本文件） | 代码 **`7be2831`**（8 文件：`dig_attribution.js`、`runtime.js`、`test/dig_attribution.test.js`、`test/e2e.js`、`test/probe_dig_attribution_real.js`、本文件、`CHANGELOG.md`、`docs/README.md`） |
| 第二轮 CI | run `38063595076` **attempt 1 双 job 全部 success**（`lint · format · types · tests` + `webui · typecheck · tests · build`） |
