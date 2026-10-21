# R3.2 最终源码实服矩阵补充报告

日期：2026-10-21。状态：**R3 仍为 IN_PROGRESS**；本报告补充 R3.2 A3 的真实 LIFE/Goal 双门与 G3 的 84 项 USER 负向矩阵，不宣告 R3.2 或 R3 完成。

## 1. 测试范围与现场

- 正常 `Bot.start` 装配生活、活动、意图、提案、计划、技能、Minecraft 记忆与任务链；隔离临时 SQLite；公开测试 Bible；未发送 QQ 消息。
- 最终源码冻结 667 文件，运行前后逐文件 SHA-256 一致，漂移 0。
- 候选模组 JAR SHA-256 `59b5c9393b4f2f24472fa3c73d732646b317e95fcdaa5c544d493f67045e3a2c`。
- 测试世界快照 107 文件，`server-before.zip` SHA-256 `8ce44c7252a22b25f2d1d8c2a8a4ef6961bdeb70d13c14014498cbd15ba4d08f`。
- 工作目录：`data/r3/normal-assembly-20261021/live-final-01/`；驱动 `combined_matrix_final.py`，SHA-256 `8158f71e67abb34f3411919bc375851b8c68188e8819034a60f821f77d49ad4f`。
- 隔离 profile 在 `Bot.start` 后停止共享 BehaviorScheduler，仅显式调用真实 `Initiative.check`；保留真实 300 秒恢复期、默认意图冷却、任务 TTL/步数/重规划预算。未修改产品代码或门禁。

## 2. USER 逐 ID 负向矩阵

12 个目标 ID × 7 类负向场景，共 84 项，全部 PASS：

| 类别 | 结果 |
|---|---|
| `default_risk` 默认风险关闭 | 12/12 PASS |
| `tool_changed` 确认前工具变化 | 12/12 PASS |
| `target_changed` 确认前目标消失/变化 | 12/12 PASS |
| `gravity` 上方重力方块 | 12/12 PASS |
| `cancel_move` 实际移动后 STOP/CANCEL | 12/12 PASS |
| `drop_missing` 缺失掉落证据 | 12/12 PASS |
| `pickup_missing` 缺失拾取证据 | 12/12 PASS |

上一轮唯一未完成的 `sand-cancel_move` 本轮 PASS：实际移动超过 2 格后取消，两次延后坐标一致，且无恢复执行。逐项结果见 `attempt-04/product-results.json`，任务事件见 `attempt-04/task-events.json`，桥接证据见 `attempt-04/bridge-events.json`。

## 3. LIFE/Goal 双门与技能复用

- 冷启动：`Initiative.check` 返回 `CHARACTER_RECOVERY`，4 条候选全部抑制，零提案、零任务。
- 自然等待 567.1 秒后，显式 warm check 返回 `proposed`，创建 `INT-20261021-005`；来源为持久 Goal，候选来源同时包含 GOAL/MEMORY/ROUTINE。
- LIFE 提案 `TP-20261021-001` 保留完整目标“挖一块沙子并捡回来”，状态 `NEEDS_USER_APPROVAL`，最大风险 MEDIUM，必须确认。
- 错误 owner 确认被拒绝，任务保持 `PENDING_CONFIRMATION`，零世界动作。
- 真实采集 3 项全部 PASS：
  - `life-source-sand-0`：LIFE 来源，初始库存 0，结束 1，SUCCEEDED；
  - `life-source-sand-3`：USER 来源，初始库存 3，结束 4，SUCCEEDED；
  - `life-source-sand-6`：USER 来源，初始库存 6，结束 7，SUCCEEDED，计划包含 `skill_reuse` 通过检查。
- 采集证据图均通过；实际 SQLite 记忆事实 2 条，ACTIVE 技能 1 个，成功证据 3 条。

主要证据：`attempt-04/life-startup.json`、`attempt-04/life-product-results.json`、`attempt-04/life-memory-skill.json`。

## 4. 首次失败与保留

本补充轮共 4 次尝试，前三次失败均保留在 `attempt-01` 至 `attempt-03`：

1. **attempt-01**：首条 RCON `forceload add` 在服务器启动后 6 秒卡顿期间超时，尚未开始任何产品用例。驱动和源码未变。
2. **attempt-02**：84/84 负向矩阵通过；LIFE warm check 被共享 BehaviorScheduler 产生的 `RECENT_INITIATIVE` 抑制，未进入任何 LIFE 用例。判定为验收夹具时序问题，不是产品拒绝。
3. **attempt-03**：84/84 负向矩阵再次通过；因复用上一次的 `product.db`，warm check 被既有 PROPOSED 意图判为 `DUPLICATE_INTENT`。产品去重逻辑正确，夹具未清理临时数据库。

attempt-04 使用同一产品源码、新临时 SQLite，并显式停止 BehaviorScheduler 后通过。

## 5. 世界恢复

- 使用本轮同一 `server-before.zip` 恢复 107 个文件，恢复前逐文件哈希一致。
- 移除本轮新增世界文件 13 个（region/entities/poi/chunks.dat）。
- 恢复后测试服已重新启动，PID 48016；恢复回执见 `restore-receipt.json`。

## 6. 结论与仍未完成

本报告关闭：

- R3.2 A3 的正常装配 LIFE/Goal 双门、真实采集、记忆与技能复用；
- G3 的 12 ID × 7 类 USER 负向矩阵；
- 上一轮 `sand-cancel_move` 未完成项。

仍未完成，R3 不得标记 COMPLETE：

- G3 其余负向类别：必要事实缺失/过期、路径失败、工具/耐久、旧掉落/合并/其他玩家、库存不净增、资源特有支撑/液体/危险；
- G4 真实 QQ 文案复测、最终冻结源码全量 pytest/Node/Java/静态检查、发布白名单同步与 CI；
- 正式 R3 回执与 G1–G4 逐项映射。

原始失败、日志和证据不改写；`verification.json` 为本轮脱敏汇总。
