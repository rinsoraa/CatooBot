# 任务书 29 实施报告：预约超时原子性与 P2 跨维度补证

日期：2026-10-18。
任务书：[Taskbook 29](specs/CatooBot%20Minecraft%20P2.6%20%E2%80%94%E2%80%94%20Reservation%20Timeout%20Atomicity%20and%20Cross-Dimension%20Closure.md)。先归档任务书，再修改实现和开展实验。
开工镜像 HEAD / origin/main：`5f5c0146a78f9a54029bff89fb7058ae2723814b`，起始镜像工作区干净；源/镜像的既有历史差异未混入本轮。

## 1. 裁定与边界

**预约超时修复：PASS。P2 本任务受验证的影子范围技术验收：PASS。**
最终构建为 `p2-shadow-6`；真实跨维度严格配对、新构建 10/5/5 样本、退出会话拒绝和默认关闭均取得证据。
本轮工程门禁的本机结果见 §6，远端 CI 与最终提交在发布后核验，具体回执随交付提供，不预写尚未发生的远端成功。

**N9-B 仍 BLOCKED，P3 未启用，R1 总体不因此自动 PASS。**
实验是受控客户端协议与真实 Fabric 服务端的 shadow 验证，不是产品 TaskRuntime 端到端执行。没有把 SELF_CONFIRMED 写入产品任务成功判据，也不证明掉落、拾取、库存净增或技能学习已成功。
仅对本轮 Minecraft 1.21.1 / Fabric Loader 0.19.5 / Fabric API 0.116.17+1.21.1、原版维度、所测 oak_log 与水平几何负责，不推广为所有方块/维度/客户端版本均可靠。

## 2. 首次失败与原子性修复

新增回归先走原实际预约函数，旧实现结果：

```text
FAIL in-flight read timeout must leave ZERO valid reservations (actual=1)
ReserveBridgeTest: 6/7, exit 1
```

旧实现只在进入 `Booking.reserve()` 前检查 expired；读取已经开始时，HTTP 可以先返回 timeout，后续读取仍创建预约。
首次失败保存在 `data/task29/java-first-failure.log`，其 SHA-256 归档在公开证据清单。

修复文件：

- `RequestState.java`：每个请求仅有一个不可变结果，timeout/interruption 与完成竞争同一个 CAS；截止时间也在提交时检查，不依赖等待线程恰好被唤醒。
- `Booking.java`：准备、读取与提交分离；世界读取不持账本锁；提交前复核会话与 action 唯一性。在账本临界段中预分配对象/容量，先通过请求结果 CAS 再发布预约；该段没有世界读取、网络或等待。
- `CatooAttributionMod.java`：HTTP 路径和测试共享 `reserveForRequest()`，成功获胜时返回同一成功结果；取消获胜时迟到读取无法通过提交门；使用真实 Thread 身份判定主线程。

```text
PENDING ──成功提交 CAS──► SUCCESS（同一预约）
   ├────超时/中断 CAS──► CANCELLED（无有效预约）
   └────校验/工作异常──► FAILURE（无有效预约）
所有终局不可覆盖；失败响应之后不能再转为成功。
```

nonce 仍在首次验证时消耗：失败/取消请求不能重放旧 nonce；新 nonce 在没有有效同 action 预约时可以重试。并发准备在最终提交时再次检查 action 唯一性，避免移出慢读取后引入新竞态。

## 3. 退出会话补强与版本隔离

复核发现此前只有 JOIN 的会话替换逻辑，缺少 DISCONNECT 失效入口。
最终 v6 增加实际玩家连接对象、UUID 和当前会话的一致性校验；旧连接迟到退出不能撤销新连接。
活动预约统计排除失效会话与到期记录。提交期间会话退出/替换时不能发布旧候选。

本轮先以 v5 完成过中间构建实验，再补强退出链、构建 v6 并完整重做最终实验。v5 数据仍在本机独立保留，**未用于 v6 的验收样本**。

## 4. 最终构建与跨维度完整配对

新 JAR 与服务器部署文件 SHA-256 完全一致：

```text
5b9dec7c0e80ff08b8e7a953438d1ac5f4f0783d7d008ad6609cc8cd37352fb2
```

客户端为本轮独立离线测试身份 Task29Bot / Task29Mate；UUID 仅为本测试服观察到的离线标识，不是在线认证身份。
测试使用受控 CLI 启动/停止进程及 Mineflayer 协议，没有通过 Windows UI 自动化终端，没有操作 QQ。

严格跨维度实验在同一次尝试中首次通过，未使用第二/第三次候选：

| 证据 | 结果 |
|---|---|
| 运行 ID | `eb5b64a5-aecc-4046-9a10-f6b34ce0742a` |
| 目标 P / B | `(10005,100,10023)` / `minecraft:oak_log`，两个维度严格相同 |
| 主世界预约 | `d61e46f8-5422-4229-93f5-68e0cf314668`，TTL 120000ms |
| 会话 | `df814296-1440-4c14-b270-e657ecf0bbc5`，seq 1；两次事件保持相同当前连接 |
| 下界真实 AFTER | `849d0544-88e2-4c29-8556-df5f0f8e32be`，维度 `minecraft:the_nether`，before oak_log / after air |
| 下界接收结论 | UNKNOWN / `target_or_identity_or_dimension_mismatch`，未消费主世界预约 |
| 主世界保全 | 服务端 `execute if block` 独立核查仍为 oak_log |
| 回主世界真实 AFTER | `805edb55-99c7-42b5-922d-10934cb0a6f4` |
| 原预约正确消费 | 同一预约 ID 返回 SELF_CONFIRMED，累计消费数恰增 1；未换会话、未使用新预约 |

“服务端独立核查”指独立于 Mineflayer 客户端缓存与 dig 返回值的服务端条件读取，不冒充独立第三方服务。
完整关联字段、世界读数与所有最终尝试见 [脱敏机器可读证据](evidence/TASK29_SHADOW.json)。nonce 仅归档 SHA-256，未发布 token。

## 5. 样本、关闭与回滚

| 最终 v6 项目 | 分母与结果 |
|---|---|
| 正常主采样 | 10 次登记 / 10 次真实 AFTER / 10 次正确 SELF_CONFIRMED |
| 外部命令移除 | 5 次有效登记窗口内移除；5 次无玩家 AFTER，全部 UNKNOWN_NO_PLAYER_EVENT |
| 他人正常破坏 | 5 次真实他人 AFTER；全部 UNKNOWN / stale_session，未消费机器人预约 |
| 额外控制 | 1 次正向控制 + 1 次跨维度负向及其同预约主世界正向 |
| 退出后旧 Session | `/session` 404，旧 Session `/reserve` 409 / session_not_current |
| 默认关闭复验 | 最终成功轮 2 次真实挖掘，服务端均确认 air；无新归因日志、无新接收事件、19132 关闭 |

统计不混淆：本轮最终投递事件共 18 条，其中 SELF_CONFIRMED 12、UNKNOWN 6；事件匹配率为 12/18=0.6667，不能冒充正常主采样匹配率 10/10。
5 次外部命令无事件仍保留在尝试分母中，不进入“送达事件数”分母，不计为外部归因已证实。
最终 receiver 降级/重复/签名拒绝为 0，server push_fail/outbox_overflow 为 0；这表示本轮未观察到这些失败，不表示做过所有网络故障注入。

默认关闭首次 v6 验证未通过：实验进程尚未完成即进入停服，第二次 air 核查失败、exit 1。原始失败保留为 `disabled-first-failed.log`；未放宽断言或半径，重新使用同一 JAR、相同几何，等待进程 exit 0 与两次服务端 air 读数后才停服，最终复验通过。成功轮 ID：`a18bd819-037c-403a-bcc6-8a54b5165e9e`。
客户端首次连接有既有 SlotComponent 解码警告；未以该警告作为世界或归因证据，各项验收依赖实际登记、事件和服务端读数。

开工测试服已停止、无归因 JAR/配置。改动前完整备份世界和相关文件，共 99 个文件：

```text
server-before-task29.zip SHA-256:
5923103570b4dc31253dd11250506b452305f3bb56a6868d30c4f0158b234795
```

回滚后，99 个原文件全部逐字节哈希一致，无额外世界文件，本轮新增 JAR/配置和测试身份/夹具撤回。曾实际启动原 42 模组验证服务器可运行；最终再次恢复相同备份并保持开工前停止状态，临时 19132/19133 端口关闭。
保留服务器日志作为证据；不把日志回退为“实验没有发生”。世界备份与含本机信息的原始日志仅在忽略目录 `data/task29/`。

## 6. 工程检查

| 命令 / 入口 | 实际结果 |
|---|---|
| `server_mod/test.ps1` 默认 | Booking 23 + DimensionPolicy 12 + RequestState 25，60 项契约 |
| `server_mod/test.ps1 -ServerDir ...` | 再加 EventJson 5 + ReserveBridge 15，共 80 项断言，类加载通过，exit 0 |
| Python 全量 | 4670 passed，627.44s，exit 0 |
| shadow + 同步定向 | 25 passed（17 + 8），exit 0 |
| Node `npm test` | 原子动作单测 + 协议 E2E 首次 ALL CHECKS PASSED，exit 0 |
| Ruff / format | Git 发布镜像全量 lint 通过、全量 format 714 files 通过；开发目录发布范围 627 files 通过 |
| mypy | 347 source files，无问题 |
| 实验脚本 | `node --check` 通过；可选真机入口未加入自动 CI |
| CI 接线 | JDK 21 纯契约 job 增加请求生命周期矩阵；不依赖本机服务器目录 |

WebUI 未改动；依赖已完成的上一轮本机 528 项与基线远端通过记录，远端既有 CI 仍会对本次提交完整验证。
无数据库迁移、无新增工具和产品运行配置变更；产品任务链、成功判据、确认门、授权和学习资格源码均未修改。
开发目录本身无 Git 元数据，直接对该目录运行全量 lint 会同时检查 `data/` 下本机临时诊断脚本并产生 lint 提示；这些原始辅助脚本和运行数据不发布。正式工程门禁已在 Git 发布镜像对全部发布文件执行，结果如表所列。

## 7. 已知限制和下一步

本轮另外确认：启用模组的 Minecraft `stop` 保存世界后，HTTP 服务线程仍可能保持 JVM/19132 存活。实验中在确认全部维度保存且 25565 关闭后，只清理了经监听 PID 和启动参数验证的本轮 Java 进程，再进行恢复。
这是 **Minecraft 服务端进程退出生命周期问题**，与 CatooBot 用户的动作 STOP/CANCEL 不是同一概念；本轮没有把它描述为已修复。客户端动作取消由独立产品路径和既有 E2 验证，未在本轮新做完整真实产品任务的取消压力实验。

下一步先为服务端 HTTP/outbox 与服务器停止生命周期制定独立任务及回归，完成退出清理与兼容性收敛，再评估 P3 独立任务书。当前仍是实验影子构建，不能据本轮结果标记稳定 Release 或开启严格产品终态归因。
