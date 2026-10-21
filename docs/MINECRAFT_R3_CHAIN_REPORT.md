# R3 检查点：采集证据链跨代 / 恢复 / 重放 / 竞争收口

日期：2026-10-20。实施依据为开工前归档的[任务书](specs/CatooBot%20Minecraft%20R3%20%E2%80%94%E2%80%94%20Collection%20Evidence%20Chain%20Counterexamples%20and%20Cross-Generation%20Closure.md)（spec sha256 c62a21c49d6eeb624e08573fbe56594d474354b72d21877e77edfd4189e1fd37，归档收据与证据见 [evidence JSON](evidence/MINECRAFT_R3_CHAIN_EVIDENCE.json)）。本项门禁全部 PASS，**R3 仍为 IN_PROGRESS**：G2 只关闭"链上反例与跨代/恢复收口"子项，G1/G3/G4 与 R8 均未因此改变。不据本子项进入 R4。

## 1. 本项补了什么

上一检查点用纯消费者证明了字段级错配不可通过；本项把同一判定放到**真实 TaskRuntime 与真实 SQLite** 上，补 G2 要求的跨代、恢复、重放、竞争四类反例：

| 类别 | 确定性断言 |
|---|---|
| 跨代（重规划） | 完成 dig 并已派发 pickup 后重规划到**新计划**：状态落到等确认、零新世界动作；纯消费者对新计划判 `step_not_succeeded`（旧 offer 不能为新计划提供成功）；旧 pickup 的迟到终态事件不能认领新计划任何步骤；新计划重新确认后必须重新派发**新**的 dig 动作 id |
| 恢复（真实 SQLite） | 记录带旧"验证成功"缓存落库 → 新 store 回读后消费者仍按当前证据重算；把落库掉落物品篡改为错物品后再次回读 → `drop_identity` 拒绝；技能资格同步拒绝（`collection_evidence_unproven`、AMBIGUOUS），不产生伪成功 |
| 重放 | 同一 pickup 终态事件两次：第二次不结算、零额外派发、`verified_pickups` 仍为 1；重复的 dig 终态事件带被篡改掉落时**不得改写**已落库的来源证据，后续 pickup 仍关联真实掉落并最终成功 |
| 竞争 | pickup 在途时取消：任务保持 `CANCELLED`，迟到终态事件不复活、不再派发世界动作，技能侧以 `not_succeeded` 拒绝 |

另外两条**结构性一致性**断言：全仓只有 `app/tasks/collection_evidence.py` 产出采集证据判决（runtime 只在"沿用既有语义"的常量里点名两个原因码），且三处消费者都从同一模块导入；Node 侧全仓只有 `minecraft_runtime/strict_attribution.js` 消费 `server_proof/server_collection`，通道层 `runtime.js` 不含 `server_proof`（只搬运不裁决）。

## 2. 门禁结果

| 门禁 | 结果 |
|---|---|
| G2-a 跨代隔离 | 新增 `tests/test_collection_evidence_chain.py::test_replan_cannot_reuse_the_previous_plans_dig_evidence` PASS |
| G2-b 恢复重放 | `test_restart_replay_recomputes_from_persisted_evidence`（真实 SQLite）PASS |
| G2-c 重放/竞争 | `test_duplicate_pickup_event_settles_once`、`test_duplicate_dig_event_cannot_restamp_the_break_evidence`、`test_cancel_while_pickup_in_flight_is_never_revived_by_the_late_event` PASS |
| G2-d 消费者一致 | `test_only_one_module_defines_the_collection_verdict`、`test_all_runtime_consumers_import_the_single_verdict` PASS |
| G2-e Node 全链 | `cd minecraft_runtime && npm test` **exit 0**、`[e2e] ALL CHECKS PASSED`、严格归因 **84/84**、全仓 node 断言标记 1219 个；`test_node_has_exactly_one_collection_evidence_consumer` PASS |
| G2-f 工程门禁 | 见下（pytest / ruff / format / mypy 日志均已归档） |
| G2-g 归档 | 改前源码 991 文件快照（sha256 655ca71c…）、规格散列、证据 JSON、本报告、ROADMAP/README/CHANGELOG 更新 |

新增测试 9 项（pytest / Node / ruff / mypy 日志均见 data/r3/chain-20261020/logs/）；全量 pytest 记录见 `data/r3/chain-20261020/logs/pytest-final.log`。

## 3. 首次失败与修复范围

新模块首轮 5 项失败，全部是**用例侧预期修正**，没有产品代码因此放宽：

1. 重规划后任务实际落到 `PENDING_CONFIRMATION`（等新计划确认），不是 `REPLANNING`——按真实状态机修正断言；
2. 受控运行时第一次读背包用于开工快照、之后才出现掉落——补背包读取序列，否则最终库存校验会按"没有净增"失败；
3. 取消后的技能拒绝原因码是 `not_succeeded:CANCELLED`，不是 `not_terminal`——按真实原因码修正；
4. 结构性断言过宽（runtime 的 `COLLECTION_DEFERRED_REASONS` 常量确实会点名两个原因码）——改为"只有消费者产出判定文本 + runtime 只允许该常量出现 2 次"；
5. 重规划用的新计划与原计划内容相同导致 `plan_hash` 不变——改成不同坐标的新计划，才真正验证跨代。

本轮**没有修改产品逻辑**（`app/` 下零改动）；新增测试与证据脚本在离线环境下完成。

## 4. 现场与边界

全程离线：未启动/重启日常 Bot、Bridge、测试服，未部署 JAR，未发 QQ 消息，未操作世界。开发目录无 Git；未同步、未提交、未推送，未宣称任何 CI 结果。

## 5. 剩余门禁

- G1：正常 Life/Goal/Memory、技能/重规划与真实用户通道的装配验证（需要动到日常装配，另立任务书）。
- G2 其余：真实服务器逐 ID 的工具/耐久与拒绝矩阵归在 G3；本项只覆盖链上反例与跨代/恢复/重放/竞争。
- G3：真实服务器逐 ID 正向/负向矩阵（12 个目标 ID、两种库存基线）、危险/路径、旧实体/合并/其他玩家、逐 ID STOP/CANCEL。
- G4：允许的真实 QQ、最终工程结果、当前世界新备份/测试/恢复、白名单发布与对应 CI/COMPLETE 回执。

R4–R8、Integrated/LAN 与真实长期 Life/Play/Learning 验收保持原要求。
