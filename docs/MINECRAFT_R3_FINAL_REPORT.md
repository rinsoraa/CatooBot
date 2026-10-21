# CatooBot Minecraft R3 正式验收报告

日期：2026-10-21。状态：**COMPLETE**（R3 正式交付回执）。

发布提交：`804f35f50943e59b807a427284c2235a00d23004`
CI：https://github.com/rinsoraa/CatooBot/actions/runs/39592796282 （attempt 1，三个 job 全部 success）

## 1. 范围与支持集

R3 覆盖 12 个资源目标 ID：

`oak_log`、`spruce_log`、`birch_log`、`jungle_log`、`acacia_log`、`dark_oak_log`、`stone`、`cobblestone`、`coal_ore`、`iron_ore`、`dirt`、`sand`。

统一合同、意图准入、来源绑定、严格服务端证据、掉落/拾取/库存增量验证均复用既有执行链，没有第二执行器。默认 MEDIUM 保持关闭；只有隔离测试 profile 显式启用。

## 2. G1–G4 映射

| 门禁 | 结果 | 关键证据 |
|---|---|---|
| G1 逐资源合同、意图与事实 | PASS | 12 ID 合同与别名/否定/歧义/数量准入；真实 Provider 9/9；正常 Bot.start 装配；LIFE/Goal 双门 3/3、错误 owner 拒绝、记忆/技能复用；[R3.2 实时报告](MINECRAFT_R3_2_LIVE_FINAL_REPORT.md) |
| G2 独立证据与协议反例 | PASS | P3 严格归因；R3 协议协商/兼容；Python 采集证据消费者；链上跨代/恢复/重放/竞争；R3.1 终态竞态 14 项；[证据链报告](MINECRAFT_R3_CHAIN_REPORT.md)、[消费者报告](MINECRAFT_R3_CONSUMER_REPORT.md)、[协议报告](MINECRAFT_R3_PROTOCOL_COMPATIBILITY_REPORT.md)、[R3.1](MINECRAFT_R3_1_TERMINAL_RECOVERY_REPORT.md) |
| G3 真实服务器逐 ID 矩阵 | PASS | 24/24 正向（12 ID × 0/3 基线）；84/84 七类 USER 负向；11/11 剩余类别（工具耐久、事实缺失、支撑/液体危险、路径失败、库存满、旧掉落、外部移除）；逐 ID cancel_move；[R3.2 实时报告](MINECRAFT_R3_2_LIVE_FINAL_REPORT.md)、[R3.3 报告](MINECRAFT_R3_3_REMAINING_NEGATIVE_REPORT.md) |
| G4 用户通道、工程、交付 | PASS | 真实 QQ 歧义/否定复测通过（无内部原因码、零任务零动作）；5069 Python passed、Node 全链、Java 158 项断言、ruff/format/mypy；发布提交与 CI 三 job success；[G4 报告](MINECRAFT_R3_3_G4_ENGINEERING_AND_QQ_REPORT.md) |

## 3. 验证数字

- 正向：24/24 PASS（12 ID × 零基线/已有同类库存）。
- 负向：84/84 PASS（12 ID × 7 类：默认风险关闭、工具变化、目标变化、重力、移动后取消、drop 缺失、pickup 缺失）。
- 剩余负向：11/11 PASS（工具耐久 4、事实缺失 1、支撑/液体危险 2、路径失败 1、库存满 1、旧掉落 1、外部移除 1）。
- LIFE/Goal：3/3 PASS（LIFE 来源 0 基线、USER 3/6 基线；错误 owner 拒绝；ACTIVE 技能 1、成功证据 3）。
- Python：**5069 passed in 802.83s**，exit 0。
- Node：全部单元组通过，E2E **ALL CHECKS PASSED**。
- Java：7 个 pure contract suite 共 158 项断言通过；Java 字节自上次完整 Fabric 构建未变，按 G4 规则复用旧全链证据。
- 静态：`ruff check app tests` PASS、643 files formatted、mypy 351 files PASS。
- 真实 QQ：歧义回复“采集目标或数量还不明确，请先选定一种资源并说明数量，我再规划。”；否定回复“收到，不执行任何采集，铁矿石不动。”；`CHAT_ONLY`，零任务零工具动作。
- 世界恢复：每轮真实测试均使用本轮新快照；R3.3 恢复 108 文件哈希一致，测试服恢复运行 PID 62664；R3.2 恢复 107 文件，PID 48016。

## 4. 历史失败与证据保留

R3.2 首次尝试的 RCON 超时、scheduler RECENT_INITIATIVE、复用 SQLite 的 DUPLICATE_INTENT；R3.3 的五次夹具问题；真实 QQ 旧进程复现的内部原因码，全部保留在原 attempt 目录和旧日志中，未删除或改写。最终 attempt 通过后未修改产品代码以“修复”测试。

## 5. 明确边界

- 本报告只完成 R3；R4–R8 尚未开始，Integrated Server / LAN 兼容性仍未验收。
- “事实过期”由确定性合同测试 `test_stale_resource_observation_does_not_freeze_an_executable_plan` 覆盖，真服补的是目标缺失；“其他玩家”由 Java `CollectionLedgerTest` 的 other-player pickup 反例覆盖，真服另有旧掉落与外部移除。没有把确定性测试冒充真服执行。
- 真实 QQ 只验证非动作歧义/否定文案，不宣称 QQ 正向采集或 LIFE 执行通过。
- 真实 Player 视觉/图片理解、长期会话、R4–R8 均不在本回执范围。

R3 正式交付完成；下一阶段按路线图制定 R4 任务书。
