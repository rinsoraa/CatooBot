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
