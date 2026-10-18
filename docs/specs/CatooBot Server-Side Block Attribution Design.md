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
