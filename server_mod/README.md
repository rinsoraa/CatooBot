# CatooBot Server-Side Attribution (Fabric mod, P2 shadow)

> **P2 影子模式**：只观察并记录证据，**不参与任何任务终态**。默认关闭（`enabled=false`）。

## 职责（仅此四项）
1. 玩家 JOIN 时建立 `session_ref` / `session_seq`，记录服务端观察到的玩家名与 UUID；同名重连使旧会话与旧登记失效。
2. `POST /reserve`（仅 127.0.0.1 + HMAC-SHA256）：校验会话、nonce 单次使用、`action_id` 唯一、目标坐标处服务端自读方块 == `expected_block`、TTL。
3. `PlayerBlockBreakEvents.AFTER` 内做唯一匹配（消费即作废；多候选 → `AMBIGUOUS`；未登记 → `UNREGISTERED_BREAK`）。
4. 把事件以 HMAC 签名 POST 给 CatooBot 回环地址（失败进 OUTBOX，绝不阻塞 STOP/CANCEL）。

**禁止**：执行任何游戏动作、修改世界、判定任务成败、提供授权旁路。

## 构建与测试（无需 Gradle）

需要 JDK 21；使用 `JAVA_HOME`、PATH 中的 `javac` 或显式 `-JavaHome`。固定依赖：
Minecraft 1.21.1 / Fabric Loader 0.19.5 / Fabric API 0.116.17+1.21.1。
脚本只读取测试服的 JAR，不修改、启动或部署服务器。

```powershell
# 独立纯逻辑契约：Booking 23 + DimensionPolicy 12（CI 同一入口）
pwsh -NoProfile -File server_mod/test.ps1
# 完整编译 + 上述契约 + EventJson 5 + ReserveBridge 6 + 类加载 Smoke
pwsh -NoProfile -File server_mod/test.ps1 -ServerDir "D:\Minecraft server\1.21.1"
# 仅构建
pwsh -NoProfile -File server_mod/build.ps1 -ServerDir "D:\Minecraft server\1.21.1"
```

Windows PowerShell 用户可将 `pwsh` 换成 `powershell.exe -ExecutionPolicy Bypass`，只对本次进程设置执行策略。
产物：`server_mod/build/catoobot-attribution-2.0.0.jar`（打印 SHA256；固定归档时间戳）。
编译/打包失败立即返回非零；旧的成功产物可保留，不代表本次构建成功，必须检查退出码。
`server_mod/build/` 及依赖 classpath 不同步、不入库。
CI 只运行不依赖 Minecraft 的 35 项契约；完整 Fabric 编译、类加载和真实服务端 E3 分开验证。

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
* 最新任务书 27 的 G4 严格同坐标配对证据仍缺失，P2 保持 PARTIAL。P3 未启用，N9-B 仍 BLOCKED；
* 现有主线程桥测试覆盖排队超时；执行中读取世界后超时与提交的竞态仍需专项复核。
