# CatooBot Minecraft R8 正式验收报告

日期：2026-10-22。状态：**COMPLETE**。

## 1. 端到端链路

R7 真实运行：用户意图“采集一块泥土” → 两次独立成功 → CANDIDATE → ACTIVE（success=2）→ 第三次技能复用成功（success=3）。R7 进程结束后世界恢复。

R8 新进程：同一隔离 SQLite/角色读到 ACTIVE 技能 success=3、3 条 POSITIVE 证据、2 条记忆；检索到 4 步采集技能，执行新采集任务 SUCCEEDED，success=4。真实服务器 attempt-01 **1/1 PASS**。

| 环节 | 证据 |
|---|---|
| 意图/输入 | R7 真实用户文本任务；R8 同角色新进程 |
| 观察/计划 | 当前世界/背包观察；技能建议 4 步 dig/dropped_items/pickup/inventory |
| 授权 | 既有 TaskRuntime 确认门，R8 任务 confirm_and_start 真实用户回合 |
| 执行 | R8 采集任务 SUCCEEDED |
| 独立核验 | 采集证据/服务端归因/背包变化；R7 资格门 |
| 回流 | R7 CANDIDATE→ACTIVE→reuse；R8 success 4 |
| 重启 | R7 进程/世界恢复后，R8 新进程读取同一 DB 并复用 |

## 2. 组合证据

- Life→Play：docs/evidence/MINECRAFT_R2_LIFE_PLAY_CLOSURE.json，真实服务器 LifeIntent→Proposal→Plan→Task→独立观察核验。
- Learning：docs/evidence/MINECRAFT_R7_SKILL_CLOSURE.json，真实 CANDIDATE→ACTIVE→复用；81 项技能测试。
- 交付：docs/evidence/MINECRAFT_R6_DELIVERY.json，6/6，DROPPED/PICKED_UP 分离。
- 战斗/建造：R5/R4 真实矩阵。
- 真实 QQ：docs/evidence/MINECRAFT_R3_3_QQ_RECHECK.json，真实私聊歧义/否定非动作；QQ 路径自 R3.3 未改动。
- 跨进程持久化：R8 新进程读 R7 DB：ACTIVE 技能 success=3、3 条 POSITIVE、2 条记忆。

## 3. 门禁映射

| 门禁 | 结果 | 说明 |
|---|---|---|
| R8-A1 跨系统/重启 | PASS | R7→R8 跨进程、世界恢复后技能/证据/记忆仍在；R8 无重复动作 |
| R8-A2 端到端简单游玩 | PASS | 用户意图→计划→确认→真实执行→核验→技能回流→重启后复用；交付/战斗/建造另有真实矩阵 |
| R8-A3 异常/反例 | PASS | R3/R4/R5/R6/R7 拒绝、歧义、STOP、目标丢失、外部移除、未知矩阵保留；R8 不宣告假成功 |
| R8-A4 长期角色 | PASS | R2 Life episode、R7 多任务/ACTIVE、R8 新进程复用；记忆/技能有来源；无自主搜索升级 |
| R8-A5 真实 QQ | PASS（沿用 R3.3） | 真实私聊歧义/否定非动作；无正向执行宣称；QQ 路径未改 |
| R8-A6 工程与发布 | PASS | 生产代码自 R6 未变；R6 5106 pytest/Node/静态 + R7 81 技能测试；发布 `8129bf2`，CI 39690232917 三 job success |

## 4. 边界

- R8 新的连续链是用户意图而非 LifeIntent 起点；Life 证据来自 R2，未在 R8 同一 DB 内重跑 Life episode。
- R8-A4 的“长期”以两个独立进程/一次世界恢复为范围，不是多日生产运行。
- 真实 QQ 为 R3.3 证据，未在当前构建重新发真实消息；QQ 代码路径未改动。
- R8 未新增生产代码；若后续代码变化，需重新跑全工程与 CI。
- 不支持任意自主游玩、无界探索、PVP/交易、跨版本迁移。

## 5. 发布

- 发布提交：`8129bf2d63a99726a3f7d8664dac881e4e289f4c`
- CI：https://github.com/rinsoraa/CatooBot/actions/runs/39690232917 ，三 job 全部 success
