# CatooBot Server-Side Block Attribution — P2 影子模式实施报告

> 任务书：`docs/specs/CatooBot Server-Side Block Attribution — P2 Shadow Mode Taskbook.md`
> （8461 bytes，SHA256 `41EE257B7E6D899B1E273FAFDE57E291B7B135FBF42AEAA94AF165B904933750`，归档 `identical=True`）。
> 本轮**只实现影子模式**：未接入任务终态、未启用严格归因、未开始 P3、**未宣称 N9-B 关闭**。

## 1. 开工基线（实测）

| 项 | 值 |
| --- | --- |
| `origin/main` / 本地 HEAD | `4a76d24d70ea41158a8ee09e1dba09687b92d39f`（本轮起始） |
| commits / dirty / `scripts/` tracked | 353 / 0 / 0 |
| 服务器 | MC **1.21.1** + Fabric Loader **0.19.5** + fabric-api **0.116.17+1.21.1** + Java **21.0.8** |
| dig 成功判据（当前产品） | `minecraft_runtime/runtime.js` 唯一硬门：`after.name !== state.blockName` → 成功（`block.break_unconfirmed` 反之） |
| `SELF_INFERRED` 的位置 | 仅作为**归因证据字段**写入结果（`dig_attribution` schema 2），**不参与**成功判定 → 影子模式不可能反向影响终态 |

## 2. 服务端模组：已实现（可复现构建）

源码位置：`server_mod/`（**不含任何本机一次性产物依赖**）：

| 文件 | 职责 |
| --- | --- |
| `src/main/java/catoo/attrib/CatooAttributionMod.java` | 模组入口：JOIN 会话绑定、AFTER 事件观察、`/reserve` HTTP、HMAC 推送 |
| `src/main/java/catoo/attrib/Booking.java` | 纯逻辑：会话表 / 登记簿 / 唯一匹配 / 消费 / TTL / 计数（**无 Minecraft 依赖**） |
| `src/main/java/catoo/attrib/Hmac.java` | HMAC-SHA256 签名与校验（token 缺失一律拒绝） |
| `src/main/java/catoo/attrib/Json.java` | 极简扁平 JSON 解析（零额外依赖） |
| `build.ps1` | 可复现构建：从服务器安装目录自动发现 intermediary/loader/brigadier/fabric-api 内嵌模块并编译打包 |
| `fabric.mod.json` / `config.sample.properties` / `README.md` | 元数据、配置样例（`enabled=false` 默认关闭）、部署禁用回滚说明 |

### 已实现语义（对应任务书 §3/§4/§5/§6）

* **会话绑定**：JOIN 时分配 `session_ref`（UUID）+ `session_seq`（单调）；同名重连使**旧会话与旧登记失效**；记录服务端观察到的名字与 UUID（离线模式 UUID 仅作会话内标识，非在线认证身份）。
* **动作登记**：`POST /reserve`（仅 `127.0.0.1`），要求 `action_id` / `nonce`(≥16) / `dimension` / `pos` / `expected_block` / `session_ref` / `ttl_ms`；
  校验：字段齐全、nonce **单次使用**、同一 `action_id` 不得重复登记、**服务端自读该坐标方块并比对 `expected_block`**、TTL 用**服务端自身 `nanoTime`**。
* **事件关联**：`PlayerBlockBreakEvents.AFTER` 中按 `pos` + `block_before` + 会话匹配；**消费即作废**；
  多候选 → `AMBIGUOUS`；无登记的真实破坏 → `UNREGISTERED_BREAK`（外部玩家证据，**不是**本次成功）；无有效会话 → `UNKNOWN`。
* **认证/幂等/竞态**：HMAC-SHA256（失败拒绝、token 缺失拒绝）、`event_id` + 唯一消费、会话失效清空登记、事件推送失败进 OUTBOX 且**不阻塞** STOP/CANCEL。
* **影子隔离**：`enabled=false` 默认关闭（关闭时不启动 HTTP、不登记、不推送）；全部实现**不触碰**产品成功判据、授权、确认门、任务状态。

### 构建产物（可复现）

```text
pwsh -NoProfile -File server_mod/build.ps1 -ServerDir "D:\Minecraft server\1.21.1"
jar=E:\WorkSpace ZCode\CatooBot\server_mod\build\catoobot-attribution-2.0.0.jar
jar_sha256=202B06A2C0B56DC0C61874D765E1BEE02C5358480C14E9CEAEB5B239016349CF
```

## 3. E2 自动化测试（已执行）

`server_mod/src/test/java/catoo/attrib/BookingTest.java`（纯 JVM，不需 Minecraft）→ **15/15 全部通过**：

| # | 用例 | 结果 |
| --- | --- | --- |
| 1 | JOIN 分配 `session_seq=1` | PASS |
| 2 | 服务端读到方块匹配 → 登记成功 | PASS |
| 3 | 目标方块不匹配 → 拒绝（`target_mismatch`） | PASS |
| 4 | 重复 nonce → 拒绝（`nonce_replay`） | PASS |
| 5 | 同一 `action_id` 重复登记 → 拒绝（`duplicate_action`） | PASS |
| 6 | 正常破坏 → 唯一匹配 `SELF_CONFIRMED` | PASS |
| 7 | 已消费登记不可复用 | PASS |
| 8 | 未登记会话的破坏 → `UNKNOWN`（不冒认） | PASS |
| 9 | 同坐标多候选 → `AMBIGUOUS` | PASS |
| 10 | 未登记破坏 → `UNREGISTERED_BREAK`（外部证据，绝不记为自证） | PASS |
| 11 | 同名重连后旧登记 → `UNKNOWN`（`stale_session`） | PASS |
| 12 | 重启/失效 → 待匹配记录清空 | PASS |
| 13 | HMAC 自洽校验通过 | PASS |
| 14 | HMAC 错误 → 拒绝 | PASS |
| 15 | token 缺失 → 拒绝（绝不放行） | PASS |

实现期发现并修正的一个真实语义缺陷：**会话失效的旧登记曾被计入 AMBIGUOUS 而不是 `UNKNOWN(stale_session)`**，
已改为「过期/异会话登记先排除，再统计候选」，并有对应用例（#11）。

## 4. 未完成项（如实标注，不粉饰）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| CatooBot 侧影子接收端（`/attribution/event` 消费 + 统计） | **NOT IMPLEMENTED** | 本轮上下文与时间用于服务端模组与 E2；模组侧已按 `bot_url` 预留推送与 OUTBOX |
| E2 剩余用例（响应不确定、通道超时、过期登记、服务器重启后同名重连的真实进程行为） | **PARTIAL** | 上述 15 条已覆盖核心矩阵；剩余项需要跨进程/HTTP 集成测试环境 |
| **E3 真实服务器三组对照（10 正常 + 5 外部 + 5 他人）** | **NOT RUN** | 任务书 §1 明确：**无隔离测试服时不得重启或改动现有生产服务器**，须先完成代码与 E2 并另行获准部署。当前只有一台生产服务器，**未获准部署**，故未做 |
| 影子统计口径（匹配率/UNKNOWN 比例/与产品终态差异） | **NOT RUN** | 依赖上述 E3 样本 |
| P3 严格归因接入 | **未授权** | 任务书禁止 |

## 5. N9-B 状态

**仍为 BLOCKED**。影子模式不产生任何 `strict_self_proof=true` 的真实证据（P2 明确禁止接入终态），
且 E3 样本未采集。关闭条件不变：P3 获授权 + 严格归因接入 + 真实执行窗口复验。

## 6. 安全与卫生

* 未修改产品代码：本轮唯一产品树变更是新增 `server_mod/` 目录（服务端模组源码与文档），运行时零改动；
* 未改授权链、工具白名单、Policy、确认门、数据库 schema、世界动作执行链；
* `token` 只出现在 `config.sample.properties` 的占位符（`CHANGE_ME`），不入库、不写日志；
* `scripts/` 保持 0 tracked；构建产物在 `server_mod/build/`（不入库）。

---

## P2 追加：影子接收端实现 + E3 真实服务器验证（本轮，获准在生产测试服临时部署）

### 1. 授权与前置

用户批准：①在**本机测试服**临时部署 P2 模组（明确测试窗口、备份世界、验证回滚）；②实现 CatooBot 侧影子接收端（独立模块、默认关闭、不参与终态）。
世界备份：`E:\WorkSpace ZCode\CatooBot_backups\mc_world_p2_20261018-132057.zip`，**20387006 bytes**，SHA256 `10FE3D00A0D6901817587F56992137C18D8EDCB7B7E2BB3A86E55885CAD90F7C`
（首次尝试因 `session.lock` 占用失败，随后**先停服再备份**成功；失败记录保留）。

### 2. CatooBot 侧影子接收端（新模块，默认关闭）

`app/integrations/minecraft/attribution_shadow.py`：
* `ShadowLedger`（幂等 `event_id`、状态归一、匹配率/未比率、重复与签名失败计数）；
* `ShadowServer`（仅 `127.0.0.1`，`/attribution/event` + `/attribution/stats`，HMAC-SHA256 校验，缺 token 拒跑）；
* `reserve()` 客户端登记辅助；`python -m app...attribution_shadow` 需 `CATOO_ATTRIBUTION_TOKEN` 才启动。
**零终态影响**：不 import 任何任务/策略模块，不被任何任务路径调用。
E2：`tests/test_attribution_shadow.py` **8/8 通过**（签名三态、幂等、状态归一、统计口径、未知不计入自证/外部、HTTP 收/拒、仅回环绑定）。

### 3. 模组修复（E3 期间发现的真实缺陷）

`Json.flat` 原先用逗号切分 JSON，导致 `pos="-247,67,-141"` 被切坏 → 登记坐标错误 → 所有事件变成 `UNREGISTERED_BREAK`。
已改为**带引号感知的扫描式解析**；重编后 jar SHA256 由 `202B06A2…` 变为 `F53EAD593857127F12F58A6C19D510D8442593303A46F44CEAE15B6156915728`。

### 4. E3 真实服务器结果（原始、未美化）

设计样本：10 次 bot 正常挖掘 + 5 次 `/setblock` 移除 + 5 次另一玩家挖掘。

| 指标 | 实测 |
| --- | --- |
| 送达影子接收端的事件 | **7** |
| `SELF_CONFIRMED`（唯一匹配） | **2** |
| `UNREGISTERED_BREAK`（未匹配登记，**不记为本次成功**） | **5** |
| `UNKNOWN` | 0 |
| 重复事件 / HMAC 失败 | 0 / 0 |
| 匹配率 | 0.2857（2/7） |

模组侧计数：`AFTER name=Catodayo` **6** 次、`AFTER name=CatodayoMate` **1** 次（共 7）；`AFTER_ERROR` 0。

**结论（如实）**：
* **正确性验证通过的部分**：另一玩家挖掘**从未**被记成 bot 的自证（`UNREGISTERED_BREAK` 或未匹配）；出现的事件里唯一匹配的确实得到 `SELF_CONFIRMED`；`/setblock` 组**没有产生任何玩家破坏事件**（与 P1 一致）。
* **发现的缺口（未解决）**：20 个设计动作中只有 7 个产生了送达事件。10 次 bot 挖掘里只有 6 次触发 `AFTER`、其中 2 次与登记唯一匹配；其余 4 次事件与登记未匹配（原因待查：可能是挖掘期间方块被客户端预测提前置 air、或登记与事件坐标/方块前置条件不一致）。
* 因此 E3 **不达标**：样本量与覆盖率都不满足「10 正常 + 5 外部 + 5 他人」的可复核要求，**不判 PASS**。

### 5. 回滚（已验证）

删除 `mods/catoobot-attribution-2.0.0.jar` 与 `config/catoo-attribution.properties` → 重启 →
`Loading 42 mods`（装模组时为 43）、**端口 19132 已关闭**、无 catoo-attrib 新日志。服务器回到未装模组状态。

### 6. N9-B 与 P3

**N9-B 仍 BLOCKED**；影子模式按设计**不产生** `strict_self_proof=true` 证据。**P3 未授权、未开始。**

---

## P2 修复轮（任务书 24）：管道修复 + E2 契约 + E3 重跑（结果如实）

任务书归档：`docs/specs/CatooBot P2 Shadow Mode — Attribution Pipeline Audit, Coverage Localization & E3 Retest.md`
（5384 bytes，SHA256 `1BB903B7333FEA9FED9620674B9B6C5BF43349CA4EE0F70FE8922BDFA3C1D7CC`，`identical=True`）。

### 1. 第一阶段：管道缺口已修（模组版本 `p2-shadow-2`）

| 任务书要求 | 实现 |
| --- | --- |
| 1. `reserve()` 必须校验 `session_ref` 属于当前有效会话 | `Booking.reserve` 通过 `byRef` 查会话，无效/过期 → `session_not_current`（409），**不信任载荷** |
| 2. JOIN/登记/事件显式关联会话 | 删除全局 `lastSessionRef`；JOIN 建 `Session(name,uuid,ref,seq,world)`，事件按 `currentSessionOf(name)` 取会话 |
| 3. 匹配含维度/精确坐标/规范化方块 ID/玩家身份/会话 | `match(session, playerUuid, pos, blockBefore)`：会话一致 + UUID 一致 + 坐标相等 + `canonicalBlock()` 精确相等 |
| 4. 事件载荷字段齐全 | `event_id / reservation_id / action_id / session_ref / session_seq / player_uuid / dimension / pos / block_before / block_after / shadow / reason` |
| 5. 接收端校验必填与关联一致性 | Python `validate_event()`：缺必填 → `REJECTED`；`SELF_CONFIRMED` 缺任一关联字段 → `UNKNOWN(unverified_correlation:…)`；无世界变化 → `UNKNOWN` |
| 6. 推送移出事件回调、异步有界 OUTBOX | `ArrayBlockingQueue(1024)` + 守护线程 `catoo-attrib-outbox`；溢出计数 `outbox_overflow`；回调只 `offer()` 不阻塞 |
| 7. 修复矛盾描述 | `config.sample.properties`（`enabled=false` 默认关闭）、README 语义表、本报告同步更新 |

模组新增 `GET /stats`（`reserved/rejected/expired/ambiguous/unknown/consumed/identity_rejected/push_ok/push_fail/outbox_overflow`）。
构建：`server_mod/build.ps1`，jar SHA256 **`3E5981FB6FB1D77F80FEF450280B9987BD60016DB28EBD3400BAE13F8244A0F6`**。

### 2. 第二阶段：E2 契约测试（真跑全绿）

* **Java 逻辑 18/18**：两会话 `session_seq` 单调、当前会话登记成功、未知/非当前 `session_ref` 被拒、自身会话可登记、
  **`oak_log` 不匹配 `dark_oak_log`（精确 ID）**、nonce 重放拒绝、唯一匹配 `SELF_CONFIRMED`、已消费永不再自证、
  他人会话不能消费 bot 登记、身份不匹配不能自证、未登记破坏记 `UNREGISTERED_BREAK`、多候选 `AMBIGUOUS`、
  会话替换后旧登记 `UNKNOWN`、失效清空、`canonicalBlock` 提取、HMAC 三态。
* **Python 17/17**：签名三态、`validate_event` 全关联字段逐项缺失降级、缺必填 `REJECTED`、无世界变化 `UNKNOWN`、
  非自证状态透传、幂等与重复计数、降级计数、`UNREGISTERED_BREAK`/`UNKNOWN` 不计入严格匹配率、HTTP 收/拒、仅回环。

### 3. 第三/四阶段：E3 本轮**无效**（如实记录，不采纳为证据）

部署新 jar 后重跑 10/5/5，结果：

* 模组 `/stats`：`rejected=15`、`consumed=0`、`unknown=1`；逐条拒绝原因均为 **`session_not_current`**；
  原因：测试客户端未持有 `session_ref`（该值由服务端 JOIN 时签发），按新契约**应当**被拒绝 —— 这本身证明第 1 条修复生效；
* 但这也意味着**本轮没有产生有效登记**，`SELF_CONFIRMED` 必然为 0；接收端展示的 2 次 `SELF_CONFIRMED` 来自**上一轮遗留进程**
  （统计跨轮累积，且 `match_rate` 键说明运行的是旧版接收端），属于**污染数据，不采纳**。
* 因此：**覆盖率没有定位、E3 指标不成立**，任务书第三、四阶段**未完成**。

### 4. 结论与缺口

| 通过条件（任务书 §6） | 状态 |
| --- | --- |
| 1. E2 会话/维度/方块 ID/关联/竞态契约测试 | **满足**（Java 18/18 + Python 17/17） |
| 2. E3 样本量与可追溯原始尝试 | **不满足**（本轮无效，需客户端先取得 `session_ref`） |
| 3. 正常/外部/他人正确区分，未知不冒充 | **部分**（`UNREGISTERED_BREAK` 与 `UNKNOWN` 语义单测成立；真机未形成有效样本） |
| 4. 推送不阻塞 STOP/CANCEL、故障与溢出可审计 | **满足（代码层面）**：异步有界 OUTBOX + 溢出计数；真机溢出未触发 |
| 5. 影子模块不影响任何产品终态 | **满足**（产品代码零改动，模块不被任务路径调用） |
| 6. CI / 静态检查 / 卫生 / 回滚 | **满足**（见下） |

**P2 维持 PARTIAL**。下一步的最小必要工作（已定位）：给模组加认证回环 `GET /session?name=` 让客户端取到
`session_ref`/`session_seq`，重新跑 10/5/5 并建立每次尝试的时间顺序表（Phase 3 要求的字段已全部具备）。

---

## P2 修复轮（续）：`/session` 端点 + 部署前类加载冒烟 + E3 再次受阻（新证据）

### 1. 已落地的两项工程改进

* **部署前类加载冒烟**（`server_mod/src/test/java/catoo/attrib/Smoke.java`）：`Class.forName` + 实例化入口类，
  通过才允许部署。本轮验证 `[smoke] class_load_ok=true` —— 上一轮「类加载崩溃」若早做这一步就不会带到服务器上。
* **认证回环 `GET /session?name=`**：`X-Signature = HMAC-SHA256(name, token)`，返回 `session_ref` / `session_seq` / `player_uuid`，
  仅 `127.0.0.1`。客户端据此合法取得会话标识（上一轮 15/15 登记被拒的根因）。

### 2. E3 本轮再次受阻（如实记录，不采纳为证据）

客户端已能取得 `session_ref`，登记全部成功：

```text
mod /stats: reservations_active=15, rejected=0, expired=0, ambiguous=0, unknown=0, consumed=0, push_ok=0, push_fail=0
receiver  : total_events=0
```

E3 20 个动作（10 挖掘 / 5 `/setblock` / 5 他人挖掘）**一个事件都没有产生**；
而模组初始化日志显示监听器**已注册**：

```text
06:30:23.708 INIT mod=catoobot-attribution version=p2-shadow-2 shadow_enabled=true outbox_capacity=1024
06:30:23.803 HTTP_LISTEN 127.0.0.1:19132
06:30:23.805 OUTBOX_WORKER_STARTED
06:30:23.805 LISTENER_REGISTERED PlayerBlockBreakEvents.AFTER; ServerPlayConnectionEvents.JOIN
```

**新证据**：日志里 8 条 `AFTER name=...` 全部来自 **`p2-shadow-1`（05:22–05:25）**，`p2-shadow-2` **一条都没有**，
且 `AFTER_ERROR=0`（说明回调根本没有被调用，而不是回调内抛错）。
→ 结论：**`p2-shadow-2` 的 AFTER 回调未生效**，这是一处**回归**，尚未定位；
`JOIN` 监听器在同一实例里正常工作（`JOIN name=CatodayoMate` 有记录），所以问题局限在 AFTER 注册/回调本身。

### 3. 下一步（已缩小到可执行的二分）

1. 把 `p2-shadow-1` 与 `p2-shadow-2` 的 AFTER 回调做**逐行二分**（首要嫌疑：回调体内新增的 `pos.method_10263()` 等取值、
   `eventJson()` 调用、`OUTBOX.offer()`；其次是注册顺序与 lambda 捕获）；
2. 扩展 `Smoke`：用**合成事件**直接调用已注册的监听器（不依赖真实破坏），把「注册成功」升级为「回调可执行」；
3. 两项都通过后再跑 10/5/5，并输出每次尝试的时间顺序表。

### 4. 环境状态（已回退干净）

删除 `mods/catoobot-attribution-2.0.0.jar` 与 `config/catoo-attribution.properties` 后重启：
服务器 `Loading 42 mods`、25565 正常；模组 HTTP 端口 19132 关闭；Bot 25580/8500/8080 在线。

---

## P2 修复轮（续 2）：E3 有效样本产出 + 覆盖率缺口定位到「非全部玩家破坏都产生 AFTER」

### 1. 关键进展：改用**水平目标**后事件正常触发

上一轮 E3 全无事件的根因**不是模组回归**，而是**测试目标选在正下方**（挖脚下）——这种破坏在该服务器/客户端组合下
不产生 `PlayerBlockBreakEvents.AFTER`。改为水平方向（同一 y / y+1，距离 2–4 格）后：

```text
mod /stats    : consumed=10, push_ok=10, push_fail=0, rejected=0, expired=0, ambiguous=0, outbox_overflow=0
receiver stats: total_events=10, shadow_states={SELF_CONFIRMED:10}, reasons={verified_correlation:10},
                strict_match_rate=1.0, downgraded=0, duplicate_events=0, rejected_signature=0
```

**10/10 正常挖掘全部唯一匹配并通过接收端关联校验**（`verified_correlation`），零降级、零重复、零签名失败。

### 2. 三组对照（有效证据汇总）

| 组 | 设计样本 | 有效证据 | 结论 |
| --- | --- | --- | --- |
| 正常挖掘 | 10 | **10/10 送达并唯一匹配**，接收端 `SELF_CONFIRMED` | ✅ 达标 |
| 登记窗口内 `/setblock` 移除 | 5 | **0 个事件**（与 P1 一致）；未记为外部归因 | ✅ 达标（未知不冒认） |
| 他人挖掘 | 5 + 4（补测） | **3 个事件，全部 `UNREGISTERED_BREAK`（`no_reservation`）**，从未记入 bot 自证 | ⚠️ 仅 3 个有效事件，**样本不足** |

### 3. 残留缺口（未解决，直接决定 P2 不能 PASS）

**并非每次玩家破坏都会产生并送达 AFTER 事件**：

* 补测中 4 次 `CatodayoMate` 挖掘经客户端复核**确实改变了世界**（`before: grass_block/short_grass → after: air`），
  但接收端事件总数没有增加（仍为 3）；
* 反向也成立：`/setblock` 组的世界变化**从不**产生事件（这是设计要的区分性）；
* 因此「无事件」既可能是外部命令移除，也可能是**未观察到的玩家破坏** —— 在这一点上，
  **不能**把「无事件」当作「外部归因已证实」，接收端/政策层面必须维持 `UNKNOWN`（当前实现已如此）。

覆盖率缺口的候选原因（尚不能区分，保持 UNKNOWN）：瞬时破坏方块（短草）、相邻破坏的合并、
事件监听在特定方块/时机不被调用、或客户端预测导致的服务器侧 no-op。

### 4. 判定

| 通过条件（任务书 §6） | 状态 |
| --- | --- |
| 1. E2 会话/维度/方块 ID/关联/竞态 | ✅ Java 18/18 + Python 17/17 |
| 2. E3 样本量（10/5/5） | ⚠️ 10 ✅ / 5 ✅ / 他人仅 3 ❌ |
| 3. 正常、外部、他人正确区分且未知不冒充 | ✅（10 自证 / 0 误记 / 3 他人→UNREGISTERED / setblock→无事件不冒充） |
| 4. 推送不阻塞、故障与溢出可审计 | ✅（异步有界 OUTBOX；本轮 10/10 push_ok，溢出 0） |
| 5. 不影响任何产品终态/授权 | ✅（产品代码零改动） |
| 6. CI / 静态检查 / 卫生 / 回滚 | ✅（回滚后 `Loading 42 mods`、19132 关闭） |

**P2 = PARTIAL**：仅差「他人挖掘 5 个有效样本」与「覆盖率缺口的原因定位」两项；
其余契约与真机证据均已成立。**N9-B 仍 BLOCKED**，P3 未授权。
