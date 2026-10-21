# CatooBot Minecraft R6 正式验收报告

日期：2026-10-22。状态：**IN_PROGRESS，真实矩阵与工程已 PASS，等待发布/CI 回执。**

## 1. 范围

R6 首期只完成“玩家物品交付”：

- 目标玩家来自当前服务器会话的真实实体观察（第二观察客户端 GuanTou）；
- 单一物品、明确数量 1–64；只从主手丢出；
- 丢出后重新读取掉落物实体确认 **DROPPED**；
- 目标玩家拾取后由观察客户端背包确认真实 **PICKED_UP**；
- 目标不存在、主手物品不符、数量不足、STOP 均拒绝/取消；
- 不支持 PVP、批量交易、自动补货、远距离给予、补偿性捡取。

## 2. 真实服务器矩阵

正常 `Bot.start`，隔离 SQLite，公开测试 Bible；第二观察客户端 GuanTou 独立 Mineflayer runtime 连接同一服务器。最终 `attempt-06`，**6/6 PASS**：

| 用例 | 结果 | 证据 |
|---|---|---|
| deliver-pickup | PASS | 任务 SUCCEEDED/DROPPED；GuanTou 真实拾取后背包有 dirt，记录 PICKED_UP |
| target-not-found | PASS | 目标不存在，规划期 `delivery.target_not_found` |
| item-not-held | PASS | 主手 bread，请求 dirt，`delivery.item_not_held` |
| count-insufficient | PASS | 主手 1 个，请求 2 个，`delivery.item_insufficient` |
| deliver-no-pickup | PASS | 任务 SUCCEEDED/DROPPED；观察客户端不拾取，明确未到 PICKED_UP |
| stop-before-drop | PASS | CANCELLED，未丢出 |

证据：`data/r6/delivery-20261022/r6-results.json`、`task-events.json`、`observer-runtime.log`；脱敏汇总 `docs/evidence/MINECRAFT_R6_DELIVERY.json`。

## 3. 历史失败（全部保留）

| attempt | 结果 | 失败与修复 |
|---|---|---|
| 01 | 0/1 | 观察客户端健康检查未带 bridge Bearer token；驱动补 header |
| 02 | 0/1 | delivery verify 的引用参数缺少 TaskStep.references，任务 VALIDATION FAILED；补 collect_references |
| 03 | 0/1 | 引用路径写成 result.entity_id，实际 TaskStep.result 顶层是 entity_id；改为 entity_id/entity_uuid |
| 04 | 6/6 | 首次全 PASS |
| 05 | 1/1 | R2Bot 在观察者拾取前自动捡回掉落物；测试在 DROPPED 后把 R2Bot 传走，重跑 |
| 06 | 6/6 | 最终全 PASS |

## 4. 门禁映射

| 门禁 | 结果 | 说明 |
|---|---|---|
| R6-A1 目标与身份 | PASS | 当前会话实体观察 username/UUID/距离；不存在/不可见拒绝；未用历史昵称授权 |
| R6-A2 物品与数量合同 | PASS | 主手 item/count、数量上限、空手/不符/不足拒绝；冻结 plan_hash |
| R6-A3 交付执行与状态 | PASS | `minecraft_drop_item` MEDIUM；丢出后 `minecraft_delivery_verify` 读掉落物 UUID/位置确认 DROPPED；观察客户端拾取后记录 PICKED_UP |
| R6-A4 取消/异常 | PASS | STOP 后 CANCELLED，未丢出；目标丢失/数量不足均不产生动作 |
| R6-A5 真实服务器矩阵 | PASS | 6/6；第二玩家真实在线/拾取；世界恢复 108 文件哈希一致 |
| R6-A6 工程与发布 | PASS（发布待补） | 5106 Python passed（614.53s）、Node 全链 ALL CHECKS PASSED、ruff/format/mypy 通过；发布/CI 待补 |

## 5. 工程验证

- Python：**5106 passed in 614.53s**，exit 0。
- Node：`minecraft_runtime npm test` → exit 0；全部单元组和 E2E **ALL CHECKS PASSED**。
- 静态：`ruff check app tests` PASS、657 files formatted、mypy 359 files PASS。
- Java：R6 未改 `server_mod` 源/候选 JAR，沿用 R3 158 项断言。
- 首次 Node E2E 失败（player distance）为夹具竞态，重跑通过；首次日志保留。
- 完整日志：`data/r6/delivery-20261022/`；脱敏汇总 `docs/evidence/MINECRAFT_R6_ENGINEERING.json`。

## 6. 边界

- 真实矩阵只覆盖“目标玩家真实拾取”与“目标不拾取”两种；未覆盖其他玩家先拾取/目标中途离开。
- 身份仅校验当前实体 username/UUID/距离；未接入完整 Identity Bridge 的 CANDIDATE/CONFLICT 授权表。
- 首期不支持批量物品、自动交易、远程给予、丢弃全部。
- 未验证 R7–R8、Integrated/LAN。
- 状态口径：任务成功只代表 DROPPED；PICKED_UP 由第二客户端真实背包观察补证，不把动作回执当交付成功。
