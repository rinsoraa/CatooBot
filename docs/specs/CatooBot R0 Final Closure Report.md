# CatooBot R0 Final Closure Report

```text
R0 TECHNICAL VERDICT: PASS
R0.1 HANDOFF/ARBITRATION: PASS
R0.2 NAVIGATION: PASS
R0.3 EVIDENCE/DOCUMENTATION: PASS
R0.4 MIGRATION DRILL: PASS
LOCAL FULL TESTS: pytest tests -q → 4632 passed, 0 failed, 0 skipped (802.40s, dev tree)
CI: @CI@
REAL CLIENT GATES: R1 4/4 PASS (IdCraft8G second client; R1c bridge injection = DEVIATION) | R2 N1 PASS, N3/N5 PASS, N6 PASS (see 8)
FINAL HEAD == origin/main: @HEAD@
DIRTY: 0
FOLLOW-UP CREATED: NO
```

任务书逐字节归档：`docs/specs/CatooBot R0 Final Closure — Handoff Arbitration, Navigation Reliability, Evidence Reconciliation & Migration Drill.md`
（源文件 38919 bytes，SHA256 `9FB3E8E64CC43722B3E133E3CEE8BE254AA4D9685B20602B3E617A9496B6C4D1`，`identical=True`）。

## 1. Step A：开工基线与真实 Git 状态

| 项 | 值 |
| --- | --- |
| `origin/main`（开工） | `67ad74d976689a73a98f736cd2d46af9469b3950` |
| 镜像 HEAD（开工） | 同上（ahead/behind `0/0`，331 commits） |
| `git status --porcelain` | 空 |
| `scripts/` tracked | 0 |
| 权威开发目录 | `E:\WorkSpace ZCode\CatooBot`（**非** Git 仓库，含密钥） |
| 运行中 Bot | 可见窗口 `run.py`（25580/8500/8080/25565 在线） |
| 真实环境 | Java 1.21.1、`online-mode=false`、bot `Catodayo`（op）、夹具客户端 `CatodayoMate`（op）、第二真实客户端 `IdCraft8G` |
| 生产库 | `data/catoobot.db`（v36、83 表）；**演练只用隔离副本** |

已有证据清点（`CatooBot_backups/`，SHA256 已记录）：`_phase8i_fu5fu3_gateI1.log`(13800B `15EC699F…`)、
`_phase8i_fu5fu4_gateJ1.log`(10479B `6D5590F0…`)、`_phase8i_fu5fu5_gateK1.log`(5874B `397A63A7…`)、
`_phase8i_gate7.log`(14433B)、`_phase8gfu_gate2.log`(8664B)。历史导航失败线索：
`_phase8i_fu3_gate3_E.log` 第 32 行 `step_1 tool=minecraft_move_to state=FAILED action_id=act_mvappwg6_10`、
任务 `task_655b658b8d34` `PAUSED`、后续步骤 `PENDING`（8I 文档 §7.5 另记 `act_mvakseat_s`、目标 `(-272,64,-133)`≈6.8 格）。

## 2. R0.1：交接接受与新目标仲裁（PASS）

### 2.1 修复前实测（机制分离，未预设失败）

§3.2 四条候选在基线 `67ad74d` 上的真实结果：**全部为真实缺陷**，且**两条 `NEW_GOAL` 路径都绕过**复合/结构门。

| 消息 | classify | target_verdict | 真实路由效果（修复前） |
| --- | --- | --- | --- |
| `好，去砍三根橡木原木再砍橡木原木` | accept_proposal | new_goal | 作废后整句交给木材解析器 |
| `好，去砍五根白桦原木，然后去挖石头` | accept_proposal | new_goal | 同上（`wood_routes=[整句]`） |
| `好，给我做 64 块橡木木板，然后去砍橡木原木` | new_goal（`_is_new_craft_request` 早退） | not_accept | **静默只合成** |
| `好，去砍五根白桦原木去挖石头` | accept_proposal | new_goal | 整句交给木材解析器 |

`QUERY`/`ATOMIC_ACTION` 入口同样落到能力入口（`capability_calls=1`）—— 跨决策类别也能绕过。

### 2.2 修复（确定性、闭集、fail-closed；不新增执行器/权限/状态机）

1. `handoff_mixed_intent()`：复用 `has_compound_connector`/`_has_second_clause_goal`/`_has_trailing_goal`/
   `acceptance_structure_violation ∈ {second_action, second_target}`，并要求未解释尾部出现**目标词**
   （`_RESOURCE_WORDS`/`_ACTION_VERBS`）—— 因此 `同意，改砍白桦` 这类干净新目标不被降级成澄清。
2. 两条 `NEW_GOAL` 路径：先作废旧提案（§3.1(5)），混合消息再返回稳定原因码
   **`handoff_conflict_compound_clarify`** + 非空澄清话术（不执行任何子目标）；干净冲突仍 `handoff_new_goal`。
3. **真机门禁暴露的两条真实缺陷**（均在 R0.1 范围内修复）：
   * `_task_reply` 在模型输出无效时提前澄清、**不进 `_route_turn`** → 待决提案 + `同意` 无法消费。
     已改为**先跑真实仲裁**再澄清。
   * `_control_turn` 命中「确认」词但无待确认事项时返回**空 `_TurnReply()`** → 整个回合并吞。
     已改为 `_delegate_task()` 在任务入口**明确未处理**时返回 `None`；`_control_turn` 对
     「仅 confirm 命中」回落继续仲裁/决策，**stop/cancel/pause/resume 与状态查询仍绝不回落给模型**。

### 2.3 回归矩阵（旧断言零删除/零放宽）

* `tests/test_phase8i_handoff_route_matrix.py`：191 → **217** 条（R0 冲突复合矩阵 26 条：机制分离表、
  负向真实路由断言、跨 `TASK_PROPOSAL`/`QUERY`/`ATOMIC_ACTION`、作废后不复活、干净冲突对照）。
* `tests/test_r0_handoff_arbitration_priority.py`：**4** 条（无效模型决策下仍仲裁；无提案时仍安全澄清；
  确定性控制仍优先）。
* Follow-up 3/4/5 的全部正向/冲突/复合/数量/结构/隔离/拒绝/STOP 用例原样保留并通过。

## 3. R0.2：真实导航可达性与失败边界（PASS）

### 3.1 审计（既有实现已按 9 类状态区分，未重写导航）

| 状态 | 既有确定性机制 |
| --- | --- |
| 目标/实体选择错误或歧义 | 工具/服务校验（`action.invalid` → `VALIDATION`）+ 规划期真实观察 |
| 观察过期/目标丢失 | `block.not_found`/`block.changed` → `TARGET_LOST`/`WORLD_CHANGED` |
| 无可用路径 | `path.not_found` + `detail.reason=no_path` |
| 路径规划成功但执行失败 | `path.not_reached`（`empty_path_without_arrival`/`path_stopped_short`/`goal_reached_without_arrival`） |
| 移动超时 / 路径搜索超时 | `minecraft.action.timeout` → `TIMEOUT`；`path_search_timeout` |
| 已在 `goal_near` 半径内 | `moveArrival.reached`（重新读取实际位置，不采信 pathfinder resolve） |
| 到达附近但后置条件不满足 | `MinecraftMoveUnconfirmedError`（`minecraft.move_unconfirmed`） |
| 暂停/取消/STOP | `TaskState.PAUSED`（`pause_requested`/`_pause_after_failure`）与 `CANCELLED` |
| 迟到/重复事件 | `_settled_action_ids` + `terminal_record`：只收敛证据、绝不复活 |

### 3.2 发现并修复的真实缺陷（证据链）

JS 运行时把失败 `detail`（目标/实际坐标/距离/半径/`reason`）放进 `minecraft.action.*` 事件，但 Python 边界
`MinecraftTaskCoordinator._handle_terminal` 只转发 `result`/`error`/`code`，`TaskRuntime.on_action_event`
也没有该参数 → **任务记录无法区分 `no_path` 与 `path_search_timeout` 等状态**。已补 `detail` 转发并写入
`step.result['detail']`（纯新增键，任务记录本就整份存 JSON，**无 schema/迁移**）。
测试：`tests/test_r0_navigation_evidence.py`（**6** 条）覆盖 detail 落库、超时与路径失败区分、
失败后不派发后续世界动作、迟到/重复终态不复活、协调器转发 detail。

### 3.3 真实门禁（不使用传送制造成功，不放宽任何阈值）

| 场景 | 结果 | 关键真实读数 | 日志 |
| --- | --- | --- | --- |
| N1 真实可达移动 | PASS | 起点 `(-268.5,65,-135.5)` → 终点 `(-259.39,65,-141.5)`，位移 **7.77 格**，终态 `SUCCEEDED`，独立核验 `oak_log` **+1**，计划目标来自 ≤16 格真实观察 | `_r0_gate_R2b.log` |
| N3/N5 真实路径失败 | PASS | `reason=path_search_timeout`、`distance_to_target=2.83`、`radius=1.5`、`goal_reached=false`、`target=(-256,64,-138)`、`actual=(-257.67,64,-135.46)`、任务 `PAUSED`、仅 1 步派发、后续全 `PENDING`、库存不变 | `_r0_gate_R2e.log` |
| N6 移动中 STOP | PASS | `WAITING_ACTION → CANCELLED`，6 秒后仍 `CANCELLED`（不复活），无 `RUNNING/WAITING_ACTION` 残留 | `_r0_gate_R2b.log`、`_r0_gate_R2e.log` |

* N1 **全程没有传送罐头**；`/tp` 仅用于把 op 夹具客户端送到目标区块加载世界（与产品执行链分离）。
* 门禁脚本对夹具自检（op 客户端 `/setblock` 回执偶发 `PartialReadError`）只如实记录，断言落在端到端真实事实。
* 未解决边界：长距离导航可靠性（8D 能力域）、配方变体按库存可满足度排序 —— 见 §12。

## 4. R0.3：证据链关联与文档对账（PASS）

关联链（真实可获得字段）：输入来源/玩家与 session → 路由 reason/target（`RouteTrace`）→ 待决提案
（item/block/amount/requested/craft_goal/at）→ `task_id`/`plan_hash` → `step_id`/`action_id`/`item.state`
→ 动作 `result`（含超时后新增的 `detail`）→ 任务终态与 `verification` → 独立世界坐标/背包快照 → 游戏内回复。

| 项 | 状态 |
| --- | --- |
| R0.1 场景可证明是否消费旧提案、是否建新任务、哪个路由赢得仲裁、零执行入口 | 有（路由 reason 计数 + 资源入口 spy + 任务/步骤快照） |
| R0.2 场景可关联 `task_id`/`plan_hash`/`step_id`/`action_id`/真实起止坐标/动作结果/后续步骤抑制/终态 | 有 |
| 移动失败的结构化原因 | **本轮补齐**（`step.result['detail']`） |
| 缺失项 | `plan_hash` 只在任务记录里可读（门禁已打印）；动作级 provider/LLM 原始输出按隐私要求不留存（设计上不存在） |

文档对账（**追加式**，历史保留）：`docs/MINECRAFT_PHASE8I.md` 新增 §16；`docs/MINECRAFT_PHASE8H.md` 的
库存感知候选/有界搜索修复与 8I 遗留「配方变体排序未解决」的表述**不冲突**：8H 修的是候选枚举与有界搜索，
8I 遗留的是「所有变体都 `available:false` 时按 `recipe_id` 字母序退化」—— 后者仍**未修**，本次不改写 8H 结论。
8D/8E 的木材模板、目标观察、路径失败、STOP/CANCEL、任务状态术语经核对与当前实现一致（无倒改历史）。

## 5. R0.4：真实成功数据库迁移演练（PASS）

* 样本（真实历史备份）：`CatooBot_backups/catoobot-pre-v18-20261001.db`，1224704 bytes，
  SHA256 `44DAB4F83220E43A2A1289EAEF60448148EBE78E9BCA6E188B98EA9B0BDF9AAF`，复制后 SHA256 一致。
* 迁移前（隔离副本）：`integrity_check=ok`、58 张业务表、70 个 index/trigger/view。
* 用**当前仓库正式迁移入口** `scripts/migrate_db.py --db <隔离副本> --json`：**version 13 → 36**、
  tables **58 → 83**（新增 29 张）、`integrity ['ok','ok']`、`lost_rows {}`、`schema_evidence_ok true`、
  `changed_count 526`、`status "migrated"`、`ok true`、**退出码 0**。
* 迁移后：83 张业务表、141 个 index/trigger/view、`integrity_check=ok`。
* 第二次运行（幂等契约）：`version [36,36]`、`status "no_op"`、`schema_changed false`、**退出码 0**。
* 生产库**全程只读**；DB 文件/备份/密钥**未入库**（`scripts/` 0 tracked，镜像无 `.db`）。

## 6. 真实服务器门禁（R0.1 + R0.2）

脚本 `scripts/r0_final_smoke_real.py`（**本地专用、不发布**）：真实 Java 1.21.1 + 真实 CatooBot Runtime/
MinecraftService + 真实 Provider（`enabled=True`）+ 真实第二个 Mineflayer 客户端 `IdCraft8G`（**自动化客户端，非真人**）。

| 场景 | 投递路径 | 结果 |
| --- | --- | --- |
| R1a 纯接受 | `how=second_client` | `handoff_accepted=1`、资源入口收到 `('砍橡木原木 2 根','minecraft:oak_log')`、任务 `PENDING_CONFIRMATION` 且**确认前零 action_id**、相关库存不变 |
| R1b 同目标复合 | `second_client` | `handoff_compound_clarify=1`、不消费、提案保留、零执行 |
| R1c 冲突新目标+第二动作 | `bridge_injection`（**替代证据/DEVIATION**） | `handoff_conflict_compound_clarify=1`、`handoff_new_goal=0`、旧提案作废、`targets=[]`、零执行 |
| R1d 干净单一冲突 | `second_client` | `handoff_new_goal=1`、旧提案作废、按自身路径评估 |
| R2 N1 / N3 / N6 | 真实第二客户端 | 见 §3.3 |

## 7. 受保护区域与仓库卫生审计

* `app/integrations/minecraft/agent.py`（`ACTION_RISK`/`TOOL_ACTION`）、`config/settings.py`（含 `allow_medium`）、
  `app/tools/policy.py`、`config/config.yaml`：**diff 0 行**。
* 未新增：工具、执行器、平行任务状态机、权限来源、数据库字段/schema/迁移。
* 未绕过：Policy、`TaskAuthorization`/`TaskStepAuthorization`、双确认门、冻结计划与 `plan_hash`、
  步骤后置验证、`TaskRuntime`/`ActionRuntime` 生命周期、STOP/CANCEL 权威。
* `scripts/` 在镜像中 **0 tracked**；本地门禁脚本、临时库（`_r0_migration/*.db`）与日志只在 `CatooBot_backups/`；
  凭据扫描 **0 命中**；`git diff --check` exit 0。

## 8. 变更文件与提交

代码（实现）：`app/integrations/minecraft/chat_bridge.py`、`app/integrations/minecraft/task_coordinator.py`、
`app/tasks/runtime.py`；测试：`tests/test_phase8i_handoff_route_matrix.py`、
`tests/test_r0_handoff_arbitration_priority.py`（新）、`tests/test_r0_navigation_evidence.py`（新）；
文档：`docs/MINECRAFT_PHASE8I.md`（§16）、`CHANGELOG.md`、本报告与任务书归档。

## 9. 质量门禁

* `pytest tests -q` → **4632 passed, 0 failed, 0 skipped**（802.40s）。
* `ruff check .`、`ruff format --check .`、`mypy app`、`git diff --check`、凭据扫描：见 §10 与最终提交。
* GitHub Actions：@CI@

## 10. 最终一致性

* 代码提交：@IMPL_SHA@；文档提交：@DOC_SHA@；镜像 HEAD == `origin/main` == @HEAD@；工作区 dirty=0。

## 11. 路线图 v2.0 状态更新文本（可直接复制）

```text
R0（已有能力安全与可靠性收敛）已完成：
- R0.1 交接接受与新目标仲裁：PASS。修复两条真实缺陷（模型决策无效时跳过确定性仲裁；确认词命中
  但无待确认事项时静默吞回合），并把冲突混合消息统一为「作废旧提案 + 稳定澄清」；真实第二客户端
  四类场景（纯接受/同目标复合/冲突复合/干净冲突）全部通过，纯接受恰好消费一次。
- R0.2 真实导航可达性与失败边界：PASS。补齐运行时结构化失败 detail 到任务步骤证据（可区分
  no_path / path_search_timeout / goal_reached_without_arrival 等）；真实 N1 可达闭环成功
  （位移 7.77 格、库存净增 1、未传送），N3/N5 真实路径失败安全停止且后续步骤不派发，N6 移动中
  STOP 权威生效且不复活。未解决边界：长距离导航可靠性（8D 能力域）。
- R0.3 证据链与文档对账：PASS。路由/提案/任务/计划哈希/步骤/动作/终态/独立库存与世界快照可关联；
  Phase 8I 历史保留，8H 修复与 8I 遗留配方变体排序问题分别记录（后者仍未修）。
- R0.4 真实成功迁移演练：PASS。真实旧版本备份（version 13，58 表）在隔离副本上迁移到 36
  （83 表）、完整性 ok、无丢行，二次运行合法 no_op。
R0 技术出口：PASS。下一步建议进入 R1（导航可靠性专项）；R0 的收敛结果是 R1 的起点，
不代表 R1 目标已完成。
```

## 12. 未解决边界与继续推进条件

1. **长距离导航可靠性**：N3 的真实失败发生在 2.83 格距离（`path_search_timeout`、半径 1.5），
   说明即便很近也可能规划超时；8D 模板半径（16 格）内的路径规划成功率与卡顿原因属 R1 专项。
2. **配方变体排序**：所有变体都 `available:false` 时退化为 `recipe_id` 字母序（会选到金合欢/竹子变体），
   应改按真实库存可满足度排序，且不得降低既有检查。
3. **既有测试时段抖动**：`tests/test_social.py::TestCognitionEngine::test_direct_mention_not_blocked_by_sleeping`
   在当天最后一分钟会因夹具自身时段（`00:00–23:59`）失败；本轮未改其断言/超时（应把「当前时间」注入为可控输入）。
4. **接手语义仍是闭集文法**：`handoff_mixed_intent` 只覆盖既有解析器能识别的第二目标信号；
   无法证明时一律澄清（不消费、不执行）。