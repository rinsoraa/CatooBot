# CatooBot Minecraft P2.7 实施报告：服务端退出生命周期与资源清理

日期：2026-10-18。开工基线：`831929b7cb86f217c96e04adc82b6c738ed84ff9`，Git 镜像工作区干净。
任务规格：[P2.7](specs/CatooBot%20Minecraft%20P2.7%20%E2%80%94%E2%80%94%20Server%20Shutdown%20Lifecycle%20and%20Resource%20Cleanup.md)。

## 1. 结果与范围

**P2.7 生命周期修复及本轮受验证的 dedicated-server 范围：PASS。**
最终 `p2-shadow-7` 在同一 JAR 上完成四轮真实 Fabric 启停，全部 Java 子进程自然 exit 0、完整保存世界、释放游戏及归因 HTTP 端口，无须强制结束 Minecraft 进程。默认关闭行为保持成立。

只对 Minecraft 1.21.1 / Fabric Loader 0.19.5 / Fabric API 0.116.17+1.21.1、JDK 21、Windows dedicated server 及所测本机回环网络负责。没有验证 integrated server、局域网开放世界或其他版本；本模组仍是实验 shadow 构建。**P3 未启用，N9-B 仍 BLOCKED，R1 总体不自动 PASS。**
本轮没有修改产品任务、授权、动作成功判据或学习资格，不把服务端停服与用户动作 STOP/CANCEL 混为一谈。

## 2. 先规范名称、归档任务，再实现

先完成 15 份旧规格重命名、17 份文档的路径引用替换，再归档 P2.7，之后才修改 Java。
名称遵循 `CatooBot Minecraft Rx/Px —— 任务标题.md`；历史 26/27/28/29 对应 P2.3/P2.4/P2.5/P2.6。已有 Phase 阶段沿用历史编号，报告不重新编号。
完整映射和原/新 SHA 见 [命名索引](SPEC_NAMING_INDEX.md)。旧规格的归档字节声明保留其当时含义；此次路径修订单独记录，不回写历史 PASS/PARTIAL 或抹除失败。
开发目录与发布镜像既有的历史正文差异分别保留，没有用整目录同步覆盖。

## 3. 实现与退出次序

- `ShadowResources` 单独拥有每代 HTTP listener、两线程有界执行器、1024 容量内存队列、outbox worker、投递连接与计数器。先完成配置再绑定，启动失败回收已创建资源。
- `SERVER_STARTED` 建立本代资源；`SERVER_STOPPING` 清理；`SERVER_STOPPED` 作幂等兜底。关闭配置不注册上述资源/归因回调。
- `RequestRegistry` 将请求绑定到资源所有者；停服关闭接纳并取消所有待处理请求。旧 handler 即使迟到至重启后也不能进入新代 registry。
- `RequestState` 仍由单一 CAS 决定成功或取消。取消唤醒等待者，成功工作完成才正常唤醒；截止时间、中断、已提交成功优先级保持 P2.6 契约。世界读取不持账本锁，迟到提交仍须通过原请求状态门。
- 关闭请求接纳 → 清除 server executor/Thread → 失效并移除会话、玩家引用及预约 → 停 HTTP、取消 handler → 断开投递连接、唤醒 worker → 有界 join/await。清理不等待队列 flush 或需要服务器主线程完成的 HTTP 工作。
- 队列取消量记作 `shutdown_dropped`；进行中投递未获 2xx 记为失败。所有连接在 finally 释放；日志分别报告 worker/executor 是否实际退出。`/stats` 新增待发数和进行中标识供诊断。

## 4. 首次失败与修复过程

1. 首轮配置异常注入后端口无法重绑：当时先绑定再配置。调整为先配置、后绑定，真实回环端口重绑测试通过。保留 `java-first.log`，未删掉该验收。
2. 受限运行环境的完整回归曾等待超时，且失败测试留下自身线程池；读取线程栈后只清理命令行已确认的 `ReserveBridgeTest` PID，未操作 Minecraft 服务。正常本机权限回归另发现成功请求过早唤醒使既有中断检查失败；将唤醒恢复为工作完成或显式取消，原 15 项 bridge 检查全部通过。保留 `java-second.log`、`java-full.log`。
3. 首次真机驱动错误地读取 `snapshot()['self_confirmed']`（实际字段是 `shadow_states['SELF_CONFIRMED']`），驱动 exit 1；其真实正向事件和 finally 正常停服日志保留在 `first-harness-failure/`，不计入最终四轮。只修驱动，未更换最终 JAR、未放宽断言，完整重做。
4. 定向 pytest 首次有 8 个 Windows 临时目录权限错误（17 项通过），在正常临时目录权限下复验全部 25 项通过，未修改产品代码或测试断言。

## 5. 最终构建与真实启停

源码构建与测试服部署 SHA-256 完全一致：

```text
64402407173d0fad388fd6c8f8d90a2ea00eeccbdc73aa81838b459dec85dc8f
```

| 场景 | Java PID | stop 至 exit 0 | 世界保存/端口释放 | 资源清理 |
|---|---:|---:|---|---|
| healthy | 32872 | 1141 ms | PASS | worker/executor 退出，push_ok=1 |
| enabled-restart | 34792 | 812 ms | PASS | 同端口重启，空会话/预约、无积压 |
| hanging-receiver | 14692 | 1265 ms | PASS | push_fail=2、shutdown_dropped=2、worker/executor 退出 |
| disabled | 32160 | 1188 ms | PASS | 19132 未启动；真实挖掘期间归因日志字节不变 |

每轮均直接通过受控 CLI 子进程 stdin 发送 `stop`，等待原 PID exit 0 后才判定；四轮都出现 `All dimensions are saved`，25565/19132 全部关闭；19133 接收器也已关闭。

正向控制使用独立离线测试身份 P27Bot/P27Mate，实际服务端 UUID 仅作为测试会话标识。
run=`48ba51ea-27d4-4121-aa9d-b1dc52d02865`，坐标 `(11005,100,11003)`，oak_log → air；真实有效登记、AFTER 及 Python 接收器的 SELF_CONFIRMED 关联一致，签名拒绝/降级/重复为 0。
这是最终新构建的 **1 次正向控制**，不冒充重新完成 P2.6 的 10/5/5 或跨维度矩阵。

故障轮实际破坏 4 次、消费登记 4 次；接收器验证 2 次请求签名后故意不应答。停服前：push_fail=1、outbox_pending=2、delivery_in_flight=true。停服结束：push_fail=2、shutdown_dropped=2、push_ok=0，四条证据去向明确。服务端 AFTER 中的影子匹配不表示事件已投递成功，更不表示产品任务成功。

## 6. 工程验证和恢复

| 门禁 | 结果 |
|---|---|
| Java 默认入口（无 Fabric 依赖） | 86 项断言，4 suites，exit 0；包含真实回环 HTTP/挂起接收器 |
| Java 完整入口（固定本机依赖） | 116 项断言，8 suites（含类加载 Smoke），exit 0 |
| Python shadow / sync 定向 | 25 passed，exit 0 |
| Ruff / format / mypy | 发布镜像全量 lint、716 files 格式、347 app source files 类型检查通过 |
| 文档引用 | 两目录共 200 个入口链接通过，无旧文件路径残留（历史命名索引除外） |
| 可选真机夹具 | `p27_lifecycle_real.js`，语法检查与三种协议控制通过 |
| 全量产品检查 | Java/Python/Node/WebUI 由本提交远端既有 CI 验证，发布后核验实际回执 |

世界和配置在停服状态先备份，99 文件；备份 ZIP SHA-256：
`ef5d959a7ebadb03d09bd638aa7405fcd363be83a57c5beae5db8f6aec865b48`。
最终恢复 99/99 原文件哈希一致，无额外世界文件；临时 JAR、私有配置、测试身份及世界夹具全部撤回。日志作为实验痕迹保留，测试服维持开工前停止状态。
公开证据：[生命周期清单](evidence/MINECRAFT_P2_7_LIFECYCLE.json)。原始日志、世界 ZIP、临时凭据只在忽略目录 `data/p2_7/`；公开数据的 nonce 只保留 SHA-256。

## 7. 交付与后续限制

本地 `CatooBot` 是含私有配置的开发源；`CatooBot_github` 是脱敏 Git 镜像与提交前快照位置；GitHub 是远端版本库。仅同步已审阅的本轮变更及改名删除，提交前生成带逐文件哈希的 ZIP 快照并验证；远端 SHA、CI 以及快照回执随最终交付核验，不预写尚未发生的远端成功。

outbox 是有界内存队列，停服取消剩余项，不保证断电持久化、无限重试或停服必达。其他网络环境、长时间耐久、集成服务器和产品严格归因尚未验收；这些限制不因本轮自然退出通过而消失。
下一步应另立 P2 后续规格，补协议资源上限、长时间运行及目标运行环境兼容性，再评估是否满足制定 P3 规格的前置条件。

生命周期接线依据 Fabric 官方 [ServerLifecycleEvents](https://github.com/FabricMC/fabric-api/blob/1.21.1/fabric-lifecycle-events-v1/src/main/java/net/fabricmc/fabric/api/event/lifecycle/v1/ServerLifecycleEvents.java)；HTTP 停止与自有 executor 的回收依据 JDK 21 [HttpServer](https://docs.oracle.com/en/java/javase/21/docs/api/jdk.httpserver/com/sun/net/httpserver/HttpServer.html)。具体结果以以上本机构建与真实实验为准。
