# CatooBot 服务端方块归因（Server-Side Block Attribution）设计方案

> 状态：**设计稿，未实现、未部署**。本轮只产出方案，供后续单独批准。
> 目的：关闭 N9-B（执行窗口内目标变化不得误报成功）所需的**服务端侧归因能力**。
> 结论先行：客户端侧（Mineflayer）无法区分「我挖掉的」与「别人在此期间移除的」——
> 实测外部 `/setblock air` 落在本次挖掘 2570/3000ms（ratio 0.857），与真实挖掘同形，产品归因只能给
> `SELF_INFERRED`、`strict_self_proof=false`。因此必须由**服务端**提供可信破坏事件。

## 1. 平台与机制选型

* 目标服务器：**Minecraft 1.21.1 + Fabric**（`online-mode=false`、`gamemode=survival`）。
* 首选原生机制：**fabric-api 的 `PlayerBlockBreakEvents.AFTER`**（服务端事件，含 `player`、`world`、`pos`、`blockState`）。
  * 不使用 Bukkit 插件：Bukkit 插件**不能**原生运行在 Fabric 服务器上，需 Cardboard/Bukkit4Fabric 之类的桥接层，
    会引入额外不确定性与许可/兼容风险，**不采用**。
* 备选（若 `PlayerBlockBreakEvents.AFTER` 语义不足）：`ServerPlayNetworkHandler` 侧监听
  `PlayerActionC2SPacket`（`START_DESTROY_BLOCK`/`STOP_DESTROY_BLOCK`）做交叉验证，但**仅作辅助**，
  因为「客户端说我在挖」不等于「服务端确认方块被移除」。

## 2. 事件载荷（协议草案 v1）

```json
{
  "schema": 1,
  "event_id": "uuid-v4",
  "server_time_ms": 1792278901683,
  "player_uuid": "00000000-0000-0000-0000-000000000000",
  "player_name": "Catodayo",
  "dimension": "minecraft:overworld",
  "pos": {"x": -241, "y": 71, "z": -142},
  "block_before": "minecraft:oak_log",
  "block_after": "minecraft:air",
  "cause": "PLAYER_BREAK",
  "tool": {"item": "minecraft:iron_axe", "meta": {}}
}
```

约束：
* `player_uuid` 必须是服务端**认证后的身份**（`GameProfile#getId()`），不接受客户端自报。
* `server_time_ms` 取服务端单调时钟（`System.nanoTime()` 换算或 `world.getTime()` + 启动时刻），**不用客户端时间**。
* `block_before/after` 都必填；`block_after` 必须是 `air/cave_air/void_air/water/lava` 之一才可视为「移除」。
* 事件里**不含**聊天、物品栏、实体、区块数据（最小化）。

## 3. 与产品动作的关联规则

服务端只负责**陈述事实**；关联由 CatooBot 侧完成，按以下**唯一匹配**规则：

1. 取当前任务里**唯一处于 `WAITING_ACTION`** 的 `dig` 步骤，且其 `action_id` 已派发。
2. 匹配条件（全部满足）：`pos` 完全一致（格坐标）、`dimension` 一致、`player_uuid` 等于本 bot、
   `server_time_ms ∈ [action_started_at - 500ms, action_finished_at + 500ms]`。
3. 匹配成功 → 该事件作为 `strict_self_proof=true` 的证据，`attribution=SELF_CONFIRMED`。
4. 不满足任一条件 → **不消费**该事件（记录为外部事件），本次动作按现有语义继续判定，
   且**不得**把 `BLOCK_REMOVED` 当成 `SELF_CONFIRMED`。

## 4. 关键区分：外部移除 vs 玩家破坏

| 情形 | 服务端事件 | 产品结论 |
| --- | --- | --- |
| 本 bot 正常挖掘完成 | `PLAYER_BREAK` + player_uuid=bot | `SELF_CONFIRMED`（可判成功） |
| 外部 `/setblock air` / 命令移除 | **无** `PlayerBlockBreakEvents` 事件（命令不走该事件） | 若在动作窗口内 → `EXTERNAL_REMOVAL` → **fail-closed** |
| 另一玩家挖掉同一坐标 | `PLAYER_BREAK` + player_uuid=他人 | `EXTERNAL_BREAK` → 本次动作 **fail-closed** |
| bot 挖掉后掉落物被他人捡走 | `PLAYER_BREAK` + 后续 `pickup` 由他人 | 破坏归因仍为 `SELF_CONFIRMED`；**库存净增**另行判定 |

**这是本方案的核心价值**：`/setblock` 类命令移除**不产生** `PlayerBlockBreakEvents`，
因此「有事件 = 我挖的」「无事件 = 非玩家破坏」在服务端是**可判定**的；客户端侧则完全不可判定。

## 5. 异常与边界

* **重复事件**：按 `event_id` 去重（幂等），同 `event_id` 只消费一次。
* **迟到事件**：动作已落终态后到达 → 丢弃并计数（`late_events`），**不得**改写终态。
* **多候选择**（两个并发 dig 动作）→ `AMBIGUOUS` → 拒绝归因，动作按现有保守语义处理。
* **身份不可信**（UUID 与 bot 不符 / 无认证）→ `UNKNOWN`。
* **数据过期**（`server_time_ms` 早于动作窗口 - 5s）→ 丢弃。
* **服务器断线 / 重启**：事件通道断开 → 归因降级为 `SELF_INFERRED`（今日行为），**不放宽**任何成功判据。
* **STOP/CANCEL 与事件竞态**：终态唯一性优先；事件到达不复活动作。
* **mod 侧不执行采集、不调用产品工具、不提供授权旁路**：只上报只读事实。

## 6. 传输与安全

* 通道：mod → `127.0.0.1` 本机 HTTP（POST `/attribution/block_break`），与 Runtime 同机、不出网。
* 认证：共享密钥（`attribution_token`，只存在于 mod 配置与 CatooBot 私有配置中，**不落 git**），
  `Authorization: Bearer <token>`；密钥缺失 → 拒绝启动该通道（而不是无认证开放）。
* 完整性：`X-Signature: HMAC-SHA256(body, token)`；校验失败丢弃并计数。
* 防重放：`event_id` + `server_time_ms` 单调窗口（±5s）+ 已见 `event_id` 缓存（有界 LRU）。
* 连接失败语义：**fail-closed 到现有行为**（即退化为今日的 `SELF_INFERRED`），绝不因为归因服务不可用就放宽成功判据。

## 7. 兼容性 / 部署 / 回滚

* 依赖：`fabric-loader`（已装）、`fabric-api`（若未装需加）；Java 21.0.8 ✓；MC 1.21.1 ✓。
* 部署：mod jar → `D:\Minecraft server\1.21.1\mods\`，重启服务器；配置写在 `config/catoobot-attribution.json`（服务器侧）。
* 回滚：删除 jar + 配置文件即可；CatooBot 侧在通道不可用时自动退化为现有行为（无需改产品）。
* 版本绑定：mod 的 `fabric.mod.json` 固定 `depends: fabric-api >= 0.100`, `minecraft ~1.21.1`。

## 8. 测试矩阵（实施时）

| 用例 | 期望 |
| --- | --- |
| 本 bot 正常挖掉方块 | `SELF_CONFIRMED`，任务 SUCCEEDED |
| 外部 `/setblock air`（动作窗口内） | `EXTERNAL_REMOVAL`，任务 FAILED，**永不 SUCCEEDED** |
| 他人挖掉同一坐标 | `EXTERNAL_BREAK`，任务 FAILED |
| 重复 `event_id` | 只消费一次 |
| 迟到事件 | 丢弃，终态不变 |
| 并发两个 dig | `AMBIGUOUS` → 不归因 |
| token 错误 / 无签名 | 丢弃 + 计数，退化 |
| 归因服务不可用 | 退化为 `SELF_INFERRED`，行为与今日一致 |
| 服务器断线重连 | 事件通道重连，历史事件不重放 |
| STOP/CANCEL + 事件竞态 | 终态唯一，不被复活 |

## 9. 成本与验收

* 实施：mod 约 200–300 行 Java + 传输层；CatooBot 侧约 150–250 行（消费 + 归因态机 + 5–8 个集成测试）。
* 验收：E3（真实服务器）+ E2（消费逻辑单测）；**只有** `strict_self_proof=true` 的样本允许判 N9-A/N9-B PASS。
* 风险：mod 版本漂移、事件窗口边界、UUID 与离线模式（`online-mode=false` 下 UUID 为离线 UUID，需以「本 session 的 bot 名 + 服务端返回的 UUID」为准）。

---

# 修订版 v2（设计安全复审后）—— 保留 v1 全文，仅追加

> 任务书：docs/specs/CatooBot Server-Side Block Attribution — Design Safety Review & Protocol Revision.md
> （8407 bytes，SHA256 4C95FF904A9B058D337D5394AA3D18F767C595952615572969D3F7D60EC5E680，归档 identical=True）。
> 本轮只修订设计与协议：不写/不部署 Fabric 模组、不改正式服务器、不改授权策略、不宣称 N9-B 已关闭。

## R0. v1 的四处硬伤（复审结论）

| # | 问题 | 为什么是硬伤 |
| --- | --- | --- |
| 1 | 事件缺失/通道故障的处理在 v1 的 §3/§4/§5/§6 自相矛盾（一处退化为 SELF_INFERRED 并按今日行为，一处 fail-closed） | 实现时必然挑有利的一条 → 等于没有约束 |
| 2 | 用服务端 System.nanoTime 换算值与客户端动作窗口比较 | 两进程时钟起点不同，跨进程比毫秒无意义；且客户端时间戳可伪造 |
| 3 | 把离线模式 UUID 当在线身份，未定义会话绑定 | online-mode=false 下 UUID 为离线推导，同名重连/伪造无法区分 |
| 4 | 未区分「没有证明自己挖掘成功」与「已证明是外部操作」 | 前者 UNKNOWN、后者 EXTERNAL_CONFIRMED，后果不同；混用会导致错误话术与错误终态 |

## R1. P0-1：fail-closed 语义统一（唯一口径）

### R1.1 归因四态

| 状态 | 含义 | 是否允许以挖掘成功推进 |
| --- | --- | --- |
| SELF_CONFIRMED | 唯一匹配、可信、本会话、本 action_id 的服务端玩家破坏事件 | 允许（仍需其它后置条件） |
| EXTERNAL_CONFIRMED | 已证明目标变化由外部造成（他人破坏 / 无登记的命令移除） | 禁止 |
| UNKNOWN | 证据不足：无事件、多候选、迟到、通道断开、认证失败、数据缺失、服务端重启 | 禁止 |
| REJECTED | 身份/目标/会话不匹配、HMAC 失败、重放 → 事件丢弃 | 禁止（动作按 UNKNOWN 处理） |

**新增硬约束**：功能**已启用**时，通道不可用或无事件只能是 UNKNOWN，**不得**回退 SELF_INFERRED 再报成功；
功能**未启用**时，产品行为与今日逐字节一致（今日即 SELF_INFERRED，也正是 N9-B BLOCKED 的原因，如实保留）。

### R1.2 保守策略对四个现实场景的影响（代价必须写清）

| 场景 | 启用后的行为 | 代价 |
| --- | --- | --- |
| 正常挖掘（事件唯一匹配） | SELF_CONFIRMED，按原逻辑可成功 | 无（多一次登记往返，约 5–20ms） |
| 服务端临时故障/通道断开 | UNKNOWN → 任务暂停/失败 | 可用性下降：故障期挖掘不可用（有意为之，宁失败不误报） |
| 服务器重启 | 全部登记失效 → UNKNOWN | 重启后首批挖掘可能失败，需重规划 |
| 断线重连 | 新 session_seq → 旧登记作废 → UNKNOWN | 重连后首个动作需重新登记 |

不为了看起来可用而保留已被真实反例否定的成功判据。

### R1.3 生命周期复用（禁止第二套状态机）

归因结论只作为 dig 步骤结果中的证据字段（attribution.state）参与既有判定：SELF_CONFIRMED 走现有成功路径；
其余走**既有**结构化失败/暂停语义（action.failed、任务 PAUSED）并写既有审计。不新增任务状态、不改授权链、不改确认门。

## R2. P0-2：因果关联协议（废止跨进程时钟比较）

### R2.1 动作开始：登记

* 客户端在派发 dig 前，经**已认证回环通道**调用 POST /attribution/reserve，提交：
  session_ref、session_seq、action_id、nonce(32B 随机)、dimension、整数坐标 pos、expected_block、ttl_ms。
* 服务端用**自己世界的真实读数**核验 pos 处方块 == expected_block；不等 → 拒绝登记。
* 服务端返回 reservation_id；过期判定使用**服务端自己的内部单调 TTL**，不回传任何可比时间戳。
* 登记失败/响应不确定/无法核验前置条件 → 本次动作 strict_proof=false（不具备严格归因能力）。

### R2.2 服务端事件：唯一匹配在服务端完成

PlayerBlockBreakEvents.AFTER 回调内：
1. 读取 player、world（维度）、pos、state（破坏前状态）；
2. 在该玩家当前 session_ref 下查找未过期、未消费且 pos/dimension/expected_block 全等的登记：
   恰好 1 条 → 绑定 reservation_id、标记已消费、生成事件；0 条 → 生成 reservation_id=null 的事件（未登记破坏）；
   多于 1 条 → 生成 AMBIGUOUS 并把全部候选标为不可用；
3. 事件只投递一次（服务端侧 event_id 幂等）。

### R2.3 动作结束：只消费自己那条

客户端接受为 SELF_CONFIRMED 需**全部**满足：reservation_id 与本动作登记一致、session_seq 一致、action_id 一致、
pos/dimension 一致、玩家身份匹配、事件未过期未消费未标 AMBIGUOUS。任一不满足 → UNKNOWN/REJECTED，不成功。
动作结束时仍无事件 → UNKNOWN（不是 SELF_INFERRED，也不是成功）。

## R3. P1-5：身份与会话契约（离线模式）

* UUID 来源：服务端 GameProfile#getId()；online-mode=false 下是**离线推导 UUID**，不是在线认证身份，措辞与日志必须照此写。
* 绑定：服务端在 ServerPlayConnectionEvents.JOIN 生成 session_ref（随机）+ session_seq（全局单调），记录 name/offline_uuid/join_at_seq；bot 经已认证回环通道取回 session_ref。
* 同名重连：JOIN 时发现同 name 旧会话 → 旧 session_ref 立即作废、其未消费登记全部失效、旧事件到达只能 UNKNOWN。
* 冲突：session_seq 与服务端当前记录不一致（会话已被替换）→ REJECTED；未知玩家/UUID 不匹配/重复身份 → REJECTED；任何 REJECTED 不产生成功归因。

## R4. P1-6：认证、重放与原子性

| 要求 | 契约 |
| --- | --- |
| 仅本机 | 只监听 127.0.0.1；非回环来源拒绝并计数 |
| 密钥 | attribution_token 仅在服务端配置与 CatooBot 私有配置；不进 git、不写日志（日志只记指纹前 8 位） |
| HMAC | X-Signature = HMAC-SHA256(body, token)；校验失败不得生成可消费事件（直接 REJECTED 并计数） |
| 幂等 | event_id + reservation_id 双键幂等；重复投递只消费一次 |
| 防重放 | 旧 session_seq 事件、服务器重启前遗留事件、超 TTL 事件一律不得绑定新动作 |
| 重启 | 服务端重启 → 登记与 session_seq 全部作废；客户端重连握手时清空本地待匹配表 |
| 通道不可用 | 启用状态下 → UNKNOWN（非成功）；禁止回退 SELF_INFERRED 并成功 |
| 原子性 | 单写入者：验证 → 唯一匹配 → 消费 → 写审计 → 提交终态；用既有 action 状态 CAS 保护；任一步失败即 UNKNOWN 且终态唯一 |

## R5. P1-4：服务器事件的证据边界（真实实验为准）

* PlayerBlockBreakEvents.AFTER = 服务端已执行完玩家破坏；其 BlockState 参数是**破坏前**状态。
* 不得假设回调自带破坏后状态 → 回调内显式 world.getBlockState(pos) 实读，并注明其它模组可能抢先改动。
* 命令、其它模组、外部系统对世界的修改：不得仅因时间接近而归因于玩家。
* 语义清单：正常事件产生；取消的事件（BEFORE 被取消）不会到 AFTER；方块被替换以回调内实读为准；
  监听器异常记日志但不得产出确认；重启全部失效；多人同刻破坏同一坐标 → AMBIGUOUS。
* **实现前必须先做真实服务器实验**（不以假设替代实验）：stub mod 只打日志记录所有 AFTER，依次执行 /setblock air、
  玩家手动挖掘、他人挖掘，记录哪些情形真的产生事件（预期 /setblock 不产生，但以实验记录为准）。
* AFTER 只能证明服务端记录的玩家破坏，不能推出掉落、拾取、库存净增或任务整体成功。

## R6. 状态转换与竞态矩阵

| 场景 | 归因结果 | 任务终态（沿用既有语义） |
| --- | --- | --- |
| 唯一匹配事件 + 位置复核通过 | SELF_CONFIRMED | 可成功（仍需其它后置条件） |
| 外部 /setblock air 致目标消失 | EXTERNAL_CONFIRMED（世界已变且无对应登记事件）或 UNKNOWN | 不可成功 |
| 其他玩家挖掉目标 | EXTERNAL_CONFIRMED | 不可成功 |
| 动作窗口内无事件 | UNKNOWN | 不可成功 |
| HMAC 错/身份不匹配 | REJECTED | 不可成功（事件丢弃） |
| 同一事件重复投递 | 仅首次消费 | 终态不变 |
| 两个候选无法消歧 | AMBIGUOUS → UNKNOWN | 不可成功 |
| 服务端/通道重启 | UNKNOWN | 不可成功 |
| STOP/CANCEL 与事件同时到达 | 事件不复活任务 | 终态唯一（CANCELLED 优先） |
| 方块已移除但拾取/库存未确认 | 仅确认破坏归因 | 不推断拾取/库存成功 |
| 登记成功但事件迟到（已终态） | 丢弃 + 计数 | 终态不变 |
| 登记被服务端拒绝 | strict_proof=false → UNKNOWN | 不可成功 |

## R7. P2：最小实现规格（供单独批准，本轮不实施）

1. 服务端模组职责：JOIN 签发会话、接收登记并核验世界前置条件、AFTER 唯一匹配与投递、重启作废。
   禁止职责：不执行任何游戏动作、不挖不放不改世界、不判定任务成败、不提供授权旁路、不读聊天。
2. 客户端→服务端：POST /attribution/reserve（R2.1）。
3. 服务端→客户端：POST http://127.0.0.1:&lt;bot 端口&gt;/attribution/event（HMAC，字段见 R2.2/R4）。
4. 标识关系：session_ref（JOIN）含 session_seq → reservation_id（登记）→ action_id（产品侧）含 event_id（事件）；
   一个 reservation_id 只能被一个 event_id 消费，一个 action_id 只能消费自己 reservation_id 的事件。
5. 状态转换：R1.1 四态 + R6 矩阵，只在验证/匹配/消费/终态四个既有落点发生。
6. 异常矩阵：超时/丢包/重复/迟到/重启/STOP，见 R6。
7. 真实实验方案：stub mod 日志 → /setblock air 对比 手动挖掘 对比 他人挖掘；记录事件有无与回调内实读值。
8. 部署/兼容/回滚：Fabric 1.21.1 + fabric-api；配置 config/catoobot-attribution.json；回滚=删 jar + 配置文件；
   功能未启用时客户端行为不变（仅启用时 fail-closed）。
9. 日志与敏感边界：只记 event_id/reservation_id/action_id/pos/dimension/结果状态/丢弃原因/计数；不记 token、不记聊天、不记他人物品栏。
10. 分阶段与验收门禁：
    * P1 stub mod 只打日志（真实实验证明 /setblock 行为）→ 验收：实验记录表；
    * P2 通道 + 登记 + 事件（只记录、不参与终态，影子模式）→ 验收：E2 单测 + 影子对比（SELF_CONFIRMED 命中率、UNKNOWN 比例）；
    * P3 接入终态（启用开关默认关闭）→ 验收：R6 全矩阵 + E3 真实服务器 N9-B 复验；
    * 每阶段独立回滚点，且不改变 MOVE_*/thinkTimeout/授权/确认门。

## R8. 实现前风险审查结论

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 启用后正常挖掘因通道抖动失败（可用性下降） | 高 | 分阶段启用；先影子模式统计；开关默认关；失败码可诊断 |
| 事件与登记错位（把他人的破坏算成本次） | 高 | 唯一匹配 + 消费即作废 + AMBIGUOUS 拒绝 + 位置/方块/会话三重校验 |
| 时钟/时间窗误用（v1 硬伤） | 已消除 | 改用服务端 TTL + nonce，不做跨进程时间比较 |
| 离线模式身份冒充 | 中 | session_ref + session_seq + 回环认证；同名重连作废旧登记 |
| 通道被本机其它进程利用 | 中 | 仅回环 + token + HMAC + 幂等/防重放；token 不入 git/日志 |
| 回调读不到破坏后状态 | 中 | 回调内显式 getBlockState + 实验验证；只把事件当破坏行为证据 |
| 模组崩溃影响服务器 | 中 | 只读、异常全捕获、失败只记日志不阻断 |
| 把 BLOCK_REMOVED 误当拾取/库存成功 | 中 | 明确分层：破坏归因 ≠ 掉落 ≠ 拾取 ≠ 库存净增 ≠ 任务成功 |

**结论**：设计已可进入 P1（stub mod 真实实验）阶段，但**本轮不实施**。在拿到 /setblock 是否产生 AFTER 的真实实验记录前，
任何实现都只能停留在设计层。**N9-B 保持 BLOCKED**：客户端侧证据不足的结论不变，关闭条件是本方案 P3 落地并取得
strict_self_proof=true 的真实样本。
