# P3 严格服务端归因与 N9-B 执行窗口报告

日期：2026-10-19。基线：`ddb4eb44f417c0483903dea7771cd90e2cea610c`。任务书先归档于 [P3 规格](specs/CatooBot%20Minecraft%20P3%20——%20Strict%20Server%20Attribution%20and%20N9-B%20Execution%20Closure.md)。

## 当前裁定

发布后核验：`ad9484cb284d1dabfb755028642d446e62775d26` 已发布，提交前 1306 文件快照逐项校验通过；[CI 39186718041](https://github.com/rinsoraa/CatooBot/actions/runs/39186718041) attempt 1 三 job success。P3 交付回执 COMPLETE；N9-B 在显式严格模式范围 PASS。后续 R1 出口逐项核验见 [R1 最终审计](MINECRAFT_R1_FINAL_AUDIT.md)，历史失败与下述发布前条件保留。

G1 契约、G2 真机产品矩阵、G3 真实 Provider/QQ 语义复验及最终本地工程回归均 PASS。最终 Python **4705 项通过（631.31s）**；待提交镜像的全量 lint、format、mypy 与 Java 纯契约通过，凭据扫描通过。P3 发布出口仍受对应提交的备份校验与远端 CI 约束：CI 未成功前不进入下一阶段。N9-B 已有严格模式下的五次真实执行窗口负向和五次正向对照；对应提交 CI 成功后，才可按本报告证据关闭严格模式范围的缺口。R1 总体及 R8 均未宣称完成。

## 实现

严格模式默认关闭。开启后，runtime 在派发 `dig` 前以 HMAC 获取当前服务端连接会话并预约冻结目标；预约响应也验签并校验 nonce、action 和 session。响应不确定或取消、连接/玩家/维度/目标变化时不派发挖掘。

独立事件入口只接受 HMAC 服务端破坏证据，逐项匹配事件来源/schema、预约、动作、会话代、UUID、维度、坐标及方块前后态。只有唯一匹配且实际目标已消失才成功，输出 schema 3、`STRICT`、`server_block_break` 及完整关联字段；掉落与库存增量不由破坏证据推断。Python 消费者重验步骤动作及解析后的实际参数，旧时序推断仍不能冒充严格证明。

缺证据、认证失败、外部移除、其他玩家破坏和迟到事件不回退到客户端推断成功；取消/超时/断线/重启的既有终态保持权威。协议限制请求体、活动关联数量与缓存；当前会话 nonce 不因容量清理重新变成可用。

真实 QQ 发现两个旁路：旧任务关键词入口把询问当任务；修复后通用 Agent 又把询问拆成分析计划。两次反例均保留。共享任务识别器否决明确询问/否定，角色 NORMAL 回合对这些木材表达复用单回合 CHAT_ONLY，阻断 Agent 和工具；不改变全局工具开关，也不宣称 QQ 已整体迁移统一语义路由。

## G1 与工程记录

- Node 完整 `npm test` 两轮通过；新增严格归因集成 36 项、真实 runtime HTTP 边界 8 项。覆盖早到/重复/迟到、登记期间取消、会话变化、缺回执、认证和慢请求等。
- Java 最终完整入口 10 套、160 项断言及 Smoke 类加载通过；CI 运行无游戏依赖的纯契约子集，不能替代真机实验。
- Python 第一轮 4694 通过、1 失败（配置注册表缺说明）；修复后第二轮 4697 通过。随后 QQ 安全边界补充回归 45 项通过。第三次全量在受限进程下因系统临时目录权限产生 1722 个 setup error，不是有效 PASS；正常用户权限的最终全量 **4705 通过（631.31s）**，最新零观察调用计数断言另有 32 项 QQ 回归通过。
- 待提交镜像 ruff/format 全量通过（720 文件格式一致），mypy app 347 文件通过；Java CI 入口 110 断言、5 套通过。最终白名单为 34 文件，13 处历史源目录/镜像差异保留。日志保留于本机 `data/p3/`，不上传私有配置、数据库或 QQ 原始窗口。

## G2 真机矩阵

Fabric 1.21.1 dedicated server，Java 21；最终模组 `2.0.0-p3.1`，JAR SHA-256：`4e6cbe2f71914317a68d394d788b0800cfa20739033a49566ef2e32e406dfb21`。同一候选完成 27 项实际 TaskRuntime 门禁，全部 PASS。

| 用例 | 数量 | 实际结果 |
|---|---:|---|
| 正常破坏 | 5 | SUCCEEDED，schema 3 严格证据 |
| N9-B 执行窗口外部 `/setblock air` | 5 | PAUSED，无严格自证、无替换目标 |
| 独立非 OP 玩家实际破坏 | 5 | PAUSED，不归因给 bot |
| 实际 TaskRuntime 取消 | 2 | CANCELLED，延后独立读取确认目标仍在 |
| 事件丢失、错误签名 | 各 1 | PAUSED |
| 合法事件重复 | 1 | SUCCEEDED，单次消费 |
| 迟到事件 | 1 | PAUSED，补投后任务/序号不变 |
| 已登记但回执连接丢失 | 1 | PAUSED，零挖掘，目标仍在 |
| 同名重连、实际 JVM 重启 | 各 1 | CANCELLED，旧动作不复活 |
| 真正动作超时 | 1 | PAUSED / step TIMEOUT，目标仍在 |
| 严格关闭对照 | 1 | 旧行为成功，明确没有严格证明 |
| 严格开启但缺密钥 | 1 | PAUSED，零挖掘 |

N9-B 注入在产品 WAITING_ACTION 且实际 Mineflayer `targetDigBlock` 已设置之后；后者来自开始挖掘包派发后的状态，只用于窗口诊断，不充当成功证明。独立玩家的实际挖掘、服务器方块和背包读数与产品事件分别记录。

这些任务由受控 USER 驱动真实 TaskRuntime、确认/Policy、AgentBridge、MinecraftService 与 Mineflayer，AgentPlan/TaskProposal 留审计记录；测试准备使用独立 RCON/客户端，不计作产品动作。不把程序化 USER 驱动说成 QQ 端到端采集，也不把单步 dig 成功说成拾取或资源技能成功。

另有 3 项真实协议补验（不并入产品任务数）：预约 TTL 后同 nonce 返回 409 `nonce_replay`；预约过期后实际玩家破坏为 `UNREGISTERED_BREAK`，动作/预约引用为空；重连后旧 session_ref 返回 409 `session_not_current`。

最终矩阵两代 JVM 均正常 stop、全维度保存、自然 exit 0，退出 2062/1328ms；补验第三代同样保存并释放 HTTP/outbox。中间原型的探针失败与旧 JAR 样本不计入最终 27 项。

## G3 语义与真实 QQ

使用本机原配置的真实 Provider，通过实际 `TurnDecisionInterpreter` 分别得到：明确采集 `task_proposal`、询问 `chat`、否定 `cancel`、歧义 `clarify`、停止 `stop`。这五项是语义解释器验收，不伪装为全部 QQ 入口都经过该解释器。

真实 QQ 仅使用获授权 bot 私聊。`P3-G3-询问01` 进入只读规划而失败；`02` 仍进入通用 Agent 分析计划而失败；最终 `03` 路由为 `CHAT_ONLY / minecraft_non_action_turn`，`tool_called=false`，task_id/action_id 为空，未进 Agent，收到自然语言答复。其他聊天未使用。

## 回滚与现场

开工现场原服务器正在运行。先保存停服，再备份本轮当前世界及原配置共 99 文件，备份 SHA-256 `8a52fbbae06706914ea11b9e460e1f77cf3dcf042aee0368a4c5201ccf636e49`。全部测试结束正常停服，逐文件恢复并校验 99 个原始哈希、清除额外世界文件，移除测试模组及私有 token 配置；随后重新启动原始服务器至 Done，恢复开工运行状态。恢复哈希指重新启动之前的校验，运行中的世界文件会正常变化。

CatooBot 原配置和数据库在重载前另有本地备份。控制台退出时发现旧实例报告未关闭 aiohttp session，保留日志，不能据此宣称 Python 应用关闭已通过生命周期验收；本轮 Fabric 自然退出门禁有独立证据。角色数据、角色姓名和全局 MEDIUM 开关未改。

## 可复核证据与边界

脱敏关联清单：[P3 证据 JSON](evidence/MINECRAFT_P3_STRICT_ATTRIBUTION.json)。包含每个任务/计划/action/严格 server_proof、独立世界和背包读数、真实 Provider 类型化输出、最终 QQ 路由、回滚及原始私有证据文件哈希。

仅验证本轮 dedicated Fabric 1.21.1 与已列协议/动作范围；集成服务器、自定义维度及其他游戏版本没有新验收。不发布稳定 Release，不自动为日常实例启用严格模式。安装、启用和禁用见 [模组说明](../server_mod/README.md)。
