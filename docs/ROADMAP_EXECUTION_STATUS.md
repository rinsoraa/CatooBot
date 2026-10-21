# Rx v3.0 持续执行状态

更新：2026-10-21。长期目标：R8 中 Life、Play、Learning 在真实服务器和长期会话形成可追溯闭环。此表记录证据状态，不修改产品定义或路线要求。

当前阶段：**R4 IN_PROGRESS，工程与真实矩阵 PASS，等待发布/CI 回执**。[R4 验收报告](MINECRAFT_R4_BOUNDED_BUILD_REPORT.md)。
2026-10-22：真实服务器 10/10 PASS（attempt-12）；5088 Python passed（661.08s）、Node 全链 ALL CHECKS PASSED、ruff/format/mypy 通过。发布/CI 通过后标记 R4 COMPLETE。

[前序 R3.3 负向矩阵](MINECRAFT_R3_3_REMAINING_NEGATIVE_REPORT.md)。
2026-10-21 18:30 完成 11/11 真服负向用例：工具/耐久 4 项、事实缺失 1 项、支撑/液体危险 2 项、路径失败 1 项、库存满 1 项、旧掉落 1 项、外部移除 1 项；108 文件恢复、测试服 PID 62664。前五次夹具失败保留。
仍待：事实过期真服证据、G4 真实 QQ 文案复测、最终全量工程与发布 CI；R3 仍 IN_PROGRESS。

[前序 R3.2 补证](MINECRAFT_R3_2_LIVE_FINAL_REPORT.md)。
2026-10-21 17:38 完成 84/84 USER 负向矩阵（12 ID × 7 类）与 3/3 LIFE/USER 采集；
LIFE 冷启动抑制、自然 300 秒恢复后 warm 提案、错误 owner 拒绝、记忆与技能复用均有实服证据。
首次三次失败（RCON 超时、scheduler RECENT_INITIATIVE、复用 SQLite 的 DUPLICATE_INTENT）保留。
剩余 G3 类别、真实 QQ 文案复测、最终全量工程与发布 CI 尚未完成；R3 仍 IN_PROGRESS。

最新补证：[R3.1 终态验证竞态与恢复证据](MINECRAFT_R3_1_TERMINAL_RECOVERY_REPORT.md)。
已复现并修复最终库存读取期间 CANCELLED/EXPIRED 被旧结算改写为成功/失败；
新增14项确定性测试，相关回归590项通过，改动范围静态检查通过。该子项补上
新 TaskRuntime/重开SQLite/技能与记忆拒绝、早到事件和拾取结算竞争证据；
此前G2报告的范围应结合本报告核对。R3整体仍IN_PROGRESS，G1/G3/G4不变。

| 阶段 | 当前证据与裁定 | 下一出口 |
|---|---|---|
| R0 | 历史收敛报告已归档；不得以子项目替代其他阶段 | 后续相关变更持续回归 |
| R1 | dedicated 范围功能出口通过；R1.4 的 16 项真机导航与三项 CI 首次全部通过，提交 1d2bf36；N9-B 显式严格模式已关闭缺口 | 见最终审计；Integrated/LAN 必需兼容项保留 |
| P1/P2 | P1 只读、P2.6 影子配对和 P2.7 服务端自然退出已有实测，均为各自范围 | 不等于 P3 或 R1 总体通过 |
| P3 | COMPLETE：27 产品任务、3 协议补验、5 真实 Provider、真实 QQ 纯聊天；4705 Python；ad9484c 的 CI 39186718041 attempt 1 三 job PASS | 同一 P3 JAR 已供 R1.4 真机补证；日常严格开关默认关闭 |
| R2 | COMPLETE：真实 Goal/Memory、两道门、SUCCEEDED/CANCELLED、自然恢复/确认过期、当前角色/冲突/世界检查，4732 Python；d121127 的 CI 39216169585 attempt 1 三 job success，交付回执核验通过 | 收尾离线是当时现场；R3 续作已复核日常 Bot/Bridge 在线，不冒充新代码已部署或 QQ 通道已验收 |
| R3 | **COMPLETE**：12 ID 合同；24/24 正向（零/已有库存双基线）；84/84 七类负向；11/11 剩余负向；逐 ID STOP/CANCEL；LIFE/Goal 双门 3/3；最终 5069 Python、Node 全链、Java 158 项、静态检查；发布 `804f35f` / CI 39592796282 三 job success。见[R3 正式报告](MINECRAFT_R3_FINAL_REPORT.md) | R4 受限放置与简单建造；先制定 R4 任务书 |
| R4 | **IN_PROGRESS（awaiting release/CI）**：build 合同/3×3×3 区域/1–3 格逐格 place；真实矩阵 10/10（单块、3 格、材料不足、占用、参考缺失、越界、外部变化、错误 owner、部分 STOP）；5088 Python/Node/静态通过。见[R4 报告](MINECRAFT_R4_BOUNDED_BUILD_REPORT.md) | 发布回执通过后标记 COMPLETE；R5 战斗安全 |
| R5 | 未取得完整受限战斗门禁 | 白名单目标、预算、风险、撤退与取消 |
| R6 | 身份设施不等于交付闭环 | DROPPED/PICKUP_CONFIRMED/UNKNOWN 分离 |
| R7 | 既有技能模块不等于真实复用已通过 | 来源、检索、适用性、反例、跨重启复现 |
| R8 | 未达到；保持完整长期目标 | 路线全部用例矩阵与长期角色证据审计 |

下一阶段只能在当前规格的所有必需门禁 PASS 后开始；真实测试仅使用用户授权的服务器及 QQ 会话。当前服务现场必须重新核验，不能把上一轮“已停止/已恢复”当作长期不变事实。

Integrated Server / LAN 仍未验收，作为 R8 前的独立必需兼容工作保留；Dedicated 的功能出口不代表双环境通过。完整长期目标没有收窄。
