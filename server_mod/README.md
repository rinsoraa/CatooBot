# CatooBot Server-Side Attribution (Fabric mod, P2 shadow)

> **P2 影子模式**：只观察并记录证据，**不参与任何任务终态**。默认关闭（`enabled=false`）。

## 职责（仅此四项）
1. 玩家 JOIN 时建立 `session_ref` / `session_seq`，记录服务端观察到的玩家名与 UUID；重连/断开使旧会话失效。
2. `POST /reserve`（仅 127.0.0.1 + HMAC-SHA256）：校验会话、nonce 单次使用、`action_id` 唯一、目标坐标处服务端自读方块 == `expected_block`、TTL。
3. `PlayerBlockBreakEvents.AFTER` 内做唯一匹配（消费即作废；多候选 → `AMBIGUOUS`；未登记 → `UNREGISTERED_BREAK`）。
4. 经有界 OUTBOX 异步 HMAC 推送到回环接收端；失败计数和记录，影子结果不进入产品 STOP/CANCEL 或终态链。

**禁止**：执行任何游戏动作、修改世界、判定任务成败、提供授权旁路。

## 构建与测试（无需 Gradle）

需要 JDK 21；使用 `JAVA_HOME`、PATH 中的 `javac` 或显式 `-JavaHome`。固定依赖：
Minecraft 1.21.1 / Fabric Loader 0.19.5 / Fabric API 0.116.17+1.21.1。
脚本只读取测试服的 JAR，不修改、启动或部署服务器。

```powershell
# 独立纯逻辑契约：Booking 23 + DimensionPolicy 12 + RequestState 25 + Shutdown 26（CI 同一入口）
pwsh -NoProfile -File server_mod/test.ps1
# 完整编译 + 上述契约 + EventJson 5 + ReserveBridge 15 + LifecycleBridge 10 + 类加载 Smoke
pwsh -NoProfile -File server_mod/test.ps1 -ServerDir "D:\Minecraft server\1.21.1"
# 仅构建
pwsh -NoProfile -File server_mod/build.ps1 -ServerDir "D:\Minecraft server\1.21.1"
```

Windows PowerShell 用户可将 `pwsh` 换成 `powershell.exe -ExecutionPolicy Bypass`，只对本次进程设置执行策略。
产物：`server_mod/build/catoobot-attribution-2.0.0.jar`（打印 SHA256；固定归档时间戳）。
编译/打包失败立即返回非零；旧的成功产物可保留，不代表本次构建成功，必须检查退出码。
`server_mod/build/` 及依赖 classpath 不同步、不入库。
CI 运行不依赖 Minecraft 的 86 项契约（含真实回环 HTTP）；完整入口共 116 项断言及类加载。真实服务端 E3 分开验证。

## 预约请求的提交与取消

`RequestState` 为每个请求保存唯一的终局结果。HTTP 超时/中断与预约成功提交竞争同一个 CAS；
超时先胜出时，任何迟到的读取都不能创建有效预约；提交先胜出时，HTTP 返回同一成功结果，不能再改报超时。
Booking 在不持有账本锁时读取世界，提交前重验会话和动作唯一性，并在账本锁内先通过请求提交门、再发布预约。
该临界段没有世界读取或等待；nonce 在首次验证时消耗，失败请求也不能重放旧 nonce。
服务端线程使用实际 Thread 对象识别，同名线程不能绕过调度。
JOIN/DISCONNECT 绑定实际连接对象；旧连接的迟到退出不能撤销新会话，已退出会话不能预约。

## 部署 / 禁用 / 回滚
1. 复制 jar 到服务器 `mods/`；
2. 复制 `config.sample.properties` → 服务器 `config/catoo-attribution.properties` 并填写 `token`；
3. 重启服务器（**仅隔离测试服**；生产服务器变更需单独批准）；
4. **禁用**：`enabled=false` 后重启 —— 不启动 HTTP、不登记、不推送，行为与未装模组一致；
5. **回滚**：删除 jar 与配置文件后重启。P1 已实测回滚（`Loading 43 → 42 mods`，无残留）。

## 影子判定语义
| 结果 | 含义 |
| --- | --- |
| `SELF_CONFIRMED` | 唯一有效登记 + 会话有效 + 方块前置条件匹配｜**影子候选，不影响任务终态** |
| `AMBIGUOUS` | 同坐标多候选，拒绝归因 |
| `UNKNOWN` | 无有效会话 / 会话失效 / 目标不匹配 |
| `UNREGISTERED_BREAK` | 真实玩家破坏但无登记（外部玩家证据；**不等于**本次动作成功） |

`/setblock` 类命令移除不产生玩家破坏事件 → 只会得到「没有事件」，即 `UNKNOWN`，**不得**记为外部归因成功。

## 已知边界（P2 未覆盖）
* 只支持原版三维度 `minecraft:overworld` / `the_nether` / `the_end`，自定义维度 fail closed；
* 事件维度与 UUID 来自实际事件对象；预约验证通过有界主线程调度读取玩家当前世界；
* `block_after` 依赖回调内 `world.getBlockState(pos)` 实读，其它模组可能抢先改动；
* 独立接收端已实现于 `app/integrations/minecraft/attribution_shadow.py`，需显式启动，未接入产品任务终态；
* 任务书 29 已取得最终 v6 的 G4 严格同坐标配对及 10/5/5 新样本，受验证影子范围通过，详见 `docs/TASK29_RESERVATION_AND_P2_REPORT.md`；P3 未启用，N9-B 仍 BLOCKED；
* 任务书 29 新增确定性读取中超时、提交/取消竞争、中断、会话替换及同名异线程测试；真机裁定见对应实施报告。
* P2.7 已修复所测 dedicated-server 的 HTTP/outbox 退出问题，四轮正常 stop 均自然 exit 0，详见 docs/MINECRAFT_P2_7_LIFECYCLE_REPORT.md；集成服务器与其它版本未验证，仍不标记稳定 Release。

## P3 实验性严格产品归因

P3 增加默认关闭的产品成功门禁；上述 P2 的“影子、N9-B BLOCKED”描述保留历史范围，当前证据与限制见 [P3 报告](../docs/MINECRAFT_P3_STRICT_ATTRIBUTION_REPORT.md)。

1. 构建 `./server_mod/build.ps1 -ServerDir '<隔离测试服目录>' -JavaHome '<JDK21目录>'`，正常停服并备份当前世界/配置，再安装 JAR。
2. 在私有 `config/catoo-attribution.properties` 设置 `enabled=true`、`port=19132`、随机 `token`（至少 16 字符），将 `bot_url` 指向实际 runtime 的 `http://127.0.0.1:<runtime_port>/minecraft/attribution/event`，不是 Python shadow 接收器。
3. CatooBot 环境设置同一 `CATOO_ATTRIBUTION_TOKEN`；YAML 设置 `minecraft.attribution_strict: true` 与 `minecraft.attribution_url: http://127.0.0.1:19132`，重启服务端与桥接。模组仅支持本机回环接口；不要公开 token 或端口。
4. 正常产品任务仍需原有风险许可、计划确认及步骤授权。没有签名服务端证明的 dig 不能在严格模式成功；单纯方块消失或库存变化不够。
5. 禁用产品门禁：`attribution_strict: false` 后重启桥接，回到已有客户端行为，该模式没有严格证明。禁用整个模组：正常停服，移除本轮 JAR/私有配置并恢复当前备份，重启验证；不要直接覆盖在线世界。

`/session` 的 HMAC 输入是 `name + "\n" + nonce`；`/reserve` 对原始请求体验签；响应 `X-Signature` 对原始响应体验签，消费者校验 nonce/关联字段。破坏事件含 schema 1/source=server_block_break，旧字段名 `shadow` 承载服务端匹配状态，只有唯一 `SELF_CONFIRMED` 才可成为严格产品证据。Bearer 无法替代事件 HMAC。

当前完整 Java 门禁为 160 项断言及 Smoke；纯契约入口新增 ProtocolTest。JAR 版本 `2.0.0-p3.1` 为实验候选，文件名保持构建脚本兼容，不是稳定发布。

## 服务端生命周期（P2.7）

启用时在 SERVER_STARTED 建立本代资源，SERVER_STOPPING 关闭接纳、取消未完成请求、失效会话、停 HTTP/执行器、释放投递连接并有界等待 worker；SERVER_STOPPED 幂等兜底。旧 handler 不能在重启后进入新代请求注册表。

内存 outbox 停服取消，不保证必达或持久化。队列取消量 shutdown_dropped 与投递失败 push_fail 分别计数，日志报告实际 worker/executor 退出情况。/stats 可观察 outbox_pending 与 delivery_in_flight；它们不代表产品任务成功。

可选本机真机夹具 src/test/resources/p27_lifecycle_real.js 依赖已备份的隔离离线测试服、测试 OP 身份 P27Bot/P27Mate、环境变量 CATOO_ATTRIBUTION_TOKEN/CATOOBOT_ROOT/P27_OUTPUT，以及独立 shadow 接收器；支持默认正向、--failure（接收器故意挂起）、--disabled。夹具会在固定测试区域建立房间，必须配合原世界备份和停服恢复；不会由 CI 自动部署或执行。
