# R3 检查点：采集协议协商与兼容拒绝

日期：2026-10-20。实施依据为开工前归档的[协议任务书](specs/CatooBot%20Minecraft%20R3%20Collection%20Protocol%20Negotiation%20and%20Compatibility.md)。本项 E2、Java、E3 和现场恢复门禁通过；**R3 仍为 IN_PROGRESS，原 G1–G4 均为 PARTIAL，R8 未完成**。不据本子项开始 R4。

## 实现

签名 session、采集预约和签名回执逐次确认 `catoobot.collection.v1`。缺失、未知、类型错误或回执降级时，采集 dig/pickup 在动作开始前拒绝。计划可能已执行合法导航，不能扩大为整个任务零移动。新服务端保留无该字段的 P3 原子破坏协议，但这种预约不能生成采集来源链。

Node 消费者限定 12 ID 的实际掉落映射、单个实体及数量一，校验实体 UUID/ID、actor/session/dimension、break event/reservation/action 与 pickup 预约的精确关联。默认关闭、HMAC、有界输入、既有等待和取消预算不放宽。候选版本 `2.1.0-r3.1`，文件名仍为 `catoobot-attribution-2.0.0.jar`，SHA-256：`59b5c9393b4f2f24472fa3c73d732646b317e95fcdaa5c544d493f67045e3a2c`。

## 证据与范围

| 项目 | 本项结果 |
|---|---|
| Node 完整入口 | `npm test` 含真实本地协议 E2E PASS；严格归因 84/84，包含真实 ActionRegistry 的不兼容零 dig/零拾取导航调用 |
| Java/Fabric | 固定 Minecraft 1.21.1 / Fabric Loader 0.19.5 / API 0.116.17 / JDK 21；12 套及 Smoke 通过，真实 HTTP ProtocolBridge 37/37 |
| Python | 本项未改 Python 产品/测试；629 文件逐项匹配上轮冻结清单，明确复用 4947 passed、对应静态检查，不声称重跑 |
| 旧 JAR | 真实未声明协议的原 JAR `74b051d1…8bbb62f` 下，一条产品 Task 在 dig 前以 `collection_protocol` 拒绝；目标保留、库存未增、无 pickup |
| 新 JAR 正向 | 全部 12 ID 各零库存/已有三个同类物品两种基线，24/24 SUCCEEDED；自动形状路由，不传 wood/resource 提示；确认前无 dig/pickup action |
| P3 兼容 | 新服务端上的旧 P3 wire 请求严格破坏通过，且没有 server_collection。此为操作者授权的协议探针，不是 TaskRuntime/确认链或实际旧客户端二进制验收 |
| 生命周期 | 同一候选两个真实 JVM：省略 enabled 的默认关闭及显式开启均 PASS；正常保存、exit 0、端口释放，开启轮 worker/HTTP 执行器退出 |
| 恢复 | 本项新的 107 文件快照全部哈希一致后恢复，清理 18 个实验新增世界文件，原 JAR 恢复，服务器重启至 Done |

24 条正向记录由隔离驱动进入实际 Planner/AgentPlanService/TaskRuntime 服务边界，**不是实际 Minecraft/QQ 消息入口或正常 Life 长期装配证据**。逐项导出另外核对了确认前状态、plan/action、签名 session、break/drop/pickup 的共同关联以及独立 RCON 库存净增和目标移除；不只复制 PASS。旧轮 84 条负向保留原范围，不冒充最新协议的完整反向复验。Provider 的 9/9 是未变 Python 语义层的既有记录，本项没有重新调用或扩大其范围。

`old-01/matrix.log` 继承的 `positive_matrix: PASS` 汇总名称有误：实际是一条旧协议拒绝记录，不能计入 24 条成功采集。部署 helper 首次把 SHA 字面量多写一位，校验在写入前拒绝；改为从构建日志读取实际散列后才部署，失败回执保留。导出 helper 首次混淆任务会话与 Minecraft 连接会话，断言失败后改为分别校验两个命名空间；产品记录未改写，此项审计工具失败亦保留。

## 快照、运行状态与交付

修改前源码为 `data/r3/protocol-20261020/source-before.zip`。测试前正常保存停服的新快照 `server-before.zip` 共 107 文件，SHA-256：`dea6feb831612c5ef1f102a4e056cc16e493109dfd2cae07f83f5d2351b3327e`。没有使用更早的 105 文件快照覆盖后续世界。

恢复回执记录原文件在重启前全部一致；原服务端 JAR `74b051d1bd9e33631c1acadf860bbb4a898306f9b24c2e9854e84f3118bbb62f` 已还原，恢复进程 PID 53552，游戏/RCON/证据端口在线。此快照原本已含早期 R3 实验模组，恢复现场不等于撤除整个 R3。日常 Bot PID 48848、Bridge PID 43188 保持，隔离 25583/25584 已释放；未重载日常服务或发送 QQ 消息。进程状态为本次收尾核验，不应被后续交接当作永久事实。

开发目录仍无 Git；发布镜像 main 干净且仍在 `d121127ef5b78016430c56a6c3f15f9d2a4cb6a4`。本项未同步、提交、推送或在线刷新远端。692 文件的当前源码清单、39 项原始记录/归档散列及脱敏逐任务关联见[检查点 JSON](evidence/MINECRAFT_R3_PROTOCOL_COMPATIBILITY.json)；就绪日志使用独立副本，运行中的追加日志不作为冻结文件。

## 原阶段剩余门禁

- G1：正常 Life/Goal/Memory、技能/重规划、实际用户通道与共享准入的实服装配验证。
- G2：Python 层完整采集证据消费者、跨代/重放/乱序/竞争等完整反例；Node 本项通过不等于全链消费者均通过。
- G3：最终源码逐 ID 工具/耐久、危险/路径、旧实体/合并/其他玩家、库存不净增等矩阵；现有取消证据不扩大为所有 dig/pickup 动作窗口。
- G4：允许的真实 QQ、最终工程结果、白名单发布前备份、对应 CI 与 COMPLETE 回执。

R4–R8、Integrated/LAN 和真实长期 Life/Play/Learning 验收保持原要求。
