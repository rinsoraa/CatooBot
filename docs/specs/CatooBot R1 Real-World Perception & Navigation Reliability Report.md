# CatooBot R1 — 真实世界感知与导航可靠性 最终报告

```text
CatooBot R1 VERDICT: PARTIAL
R1.1 PERCEPTION INTEGRITY: PASS
R1.2 NAVIGATION RELIABILITY: PASS
R1.3 TARGET FRESHNESS / LIFECYCLE: PASS
R1.4 EVIDENCE / CI / HYGIENE: PASS
BASELINE SHA: 32649eb1856feba56e970d51026f79cf6ad477fc
IMPLEMENTATION SHA: 38ca66d0271550846233c0466843112286583db9
FINAL HEAD == origin/main: YES（闭环时核验；当前报告提交见 Git 历史）
DIRTY: 0
scripts/ TRACKED: 0
PYTEST: 4638 passed / 0 failed / 0 skipped (617.26s, dev tree)
RUNTIME NODE TESTS / E2E: move_to.test.js 84/84 checks passed（含 R1 两组新增）；runtime e2e 走 CI
RUFF / FORMAT / MYPY / WEBUI: ruff check . 通过；ruff format --check . 通过；mypy app Success（346 源文件）；WebUI 未改动（无前端变更）
REAL WORLD PERCEPTION GATES: P1 PASS(E3/E4: 感知 vs runtime /status 差 0.000；第三源独立客户端实体 BLOCKED) | P2 PASS(E3) | P3 PASS(E3/E4) | P4 PASS(E3/E4) | P5 PASS(E3/E4)
REAL NAVIGATION GATES: N1 PASS(1) | N2 PASS(3/3) | N3 PASS(3/3) | N4 PASS(3/3) | N5 PASS 1/2（1 次真实绕障成功、1 次 30s 动作超时 fail-closed） | N6 BLOCKED（测试地形无高一格平坦落脚点） | N7 PASS（fail-closed，绝不 SUCCEEDED） | N8 BLOCKED（无法稳定构造） | N9 见 §5（结构层） | N10 PASS(1) | N11 PASS（单测竞态矩阵） | N12 部分（P4 真实断线）
FALSE-SUCCESS GUARD: PASS
STOP/CANCEL / LATE EVENT TERMINAL INVARIANT: PASS
ROOT CAUSES FIXED: 5（R1-RC1 path_search_timeout 误杀、R1-RC2 TIMEOUT/CANCELLED 丢导航证据、R1-RC3 计划/动作坐标口径不一致、R1-RC4 local 层无守卫、R1-RC5 状态对账不失效感知）
OPEN BOUNDARIES: N6/N8 环境无法构造；N5 绕障 2 次中 1 次超时；find_blocks 无 coverage 字段（空结果 vs 区域未加载在数据层仍不可区分）
CI RUNS: 见 §9
TASKBOOK ARCHIVE: 47138 bytes / SHA256 861AB366BFA82FB138DE6CC9F5F5576618F02749CEE30694B0BD732110E8306A / identical=True
FOLLOW-UP CREATED: NO
```

## 1. 开工基线与真实 Git 状态

| 项 | 值 |
| --- | --- |
| `origin/main`（开工） | `32649eb1856feba56e970d51026f79cf6ad477fc` |
| 镜像 HEAD（开工） | 同 SHA；ahead/behind = 0/0；`git status --porcelain` 空 |
| commits | 336 |
| 开发目录 | `E:\WorkSpace ZCode\CatooBot`（**非 git 仓库**，唯一开发真源） |
| 运行版本一致性 | 开工时 CatooBot 与 runtime **均未运行**（25580/8500/8080 未监听），门禁脚本自起 runtime，确保载入的正是当前工作树代码 |
| 真实服务器 | Java 1.21.1 Fabric（`online-mode=false`、`gamemode=survival`、`spawn-protection=16`），世界 overworld，25565 在线 |
| 依赖锁定 | mineflayer 4.39.0、mineflayer-pathfinder 2.4.5、minecraft-data 3.117.0、node v26.8.1、java 21.0.8 |
| 导航常量（代码） | `MOVE_TO_RADIUS=1.5`（`runtime.js:57` 硬编码）、`MOVE_MAX_DISTANCE`=config 64、`MOVE_TIMEOUT_MS`=30000（Python 从不设置）；pathfinder `thinkTimeout=5000`/`tickTimeout=40`，CatooBot 从不覆盖 |
| 感知周期 | near 1.0s / local 4.0s / extended 20.0s（`config.yaml:557-559`） |

## 2. R0 证据复用与文档对账

* R0 原始日志在本机可得并逐项复核：`CatooBot_backups/_r0_gate_R1f.log`（R1 交接 4/4）、`_r0_gate_R2b.log`（N1 SUCCEEDED、位移 7.77 格、`oak_log +1`）、`_r0_gate_R2e.log`（N3 `path_search_timeout`、`distance_to_target=2.83`、`raw=3.04`、`radius=1.5`、任务 PAUSED、后续步骤全 PENDING）、`_phase8i_fu3_gate3_E.log`（Phase 8I 历史导航失败）。
* 口径复核：`distance_to_target` = **方块格**欧氏距离（对 (-257.67,64,-135.46) 与 (-256,64,-138) 离线复算得 2.83，与日志一致）；`raw_distance_to_target` = 浮点 3D（3.04）✓。两者不混用。
* 文档与代码不符处（已在本轮修正文档）：`world.py` 注释曾称「每层有独立的 fetched_at」（实际只有 age，本轮补齐每层 `captured_at`）；`DEFAULT_ARRIVE_RADIUS` 注释曾称与 move_to 半径对齐（实际 2.0 vs 1.5，本轮改为与动作同口径的方块格判定）。

## 3. 缺陷矩阵（根因 → 证据 → 修复 → 回归 → 真机复验）

| ID | 类别 | 现象与证据 | 根因 | 修复 | 回归 | 真机复验 |
| --- | --- | --- | --- | --- | --- | --- |
| R1-RC1 | N-SEARCH | 修复前 10 次导航 9 成功，N2-2 在 2 格内 `path_search_timeout`；Node 探针同目标连续 `partial` **26.6s** 后 `timeout`（`_r1_probe1.log`） | `path_update(status='timeout')` 立即判失败，而 pathfinder 的 `thinkTimeout` 是**累计搜索预算**且仍持有可用路径 | `runtime.js`：已到达→成功；`path.length>0`→继续（既有动作超时兜底）；**无可用路径→保持 `path_search_timeout` fail-closed** | `move_to.test.js` 新增 4 条断言 | 修复后：N1–N4 共 10 次全部到达（见 §5） |
| R1-RC2 | N-LIFE | TIMEOUT/CANCELLED 事件只带 `reason`，导航证据全丢 | `action_runtime.finish` 的取消路径不携带 detail | `action_runtime.withTerminalDetail()` + `move_to.terminalDetail()`（纯新增字段） | `move_to.test.js` 断言 `detail.reason=action_timeout_after_path_search_timeout` | N5-1/N7 真机事件带该 reason（§5） |
| R1-RC3 | P-MATH | 任务级 `position_within` 浮点判定把**合法到达**判失败（真机 N4-2 格距 1.414、浮点 2.10） | 计划冻结浮点 waypoint/浮点后置条件，与 move_to 的方块格口径不一致；planner 还用 `int()` 向零截断 | `planner._cell()`（floor）+ 单段/多段/最终后置条件统一整数格；`runtime._read_position_once` 用**方块格距离**判定并如实上报 `raw_distance` | 新增 3 条测试（后置条件=动作参数、负数 floor、格角到达不误判） | 修复后 N2-2/N2-3（浮点 2.27/2.49）任务级 SUCCEEDED |
| R1-RC4 | P-STALE | `local` 层载荷为空时刷新时间戳并清空 POI | `world.py` local 分支缺 `is not None` 守卫（near/extended 有） | 同口径守卫 + `view()['layers'][l]['captured_at']` | 新增 2 条 world 测试 | P2 真机每层 age/captured_at 独立 |
| R1-RC5 | P-STALE | 状态对账发现 DISCONNECTED 时缓存仍 `available=True` 且 age 无限增长 | 只有 connected/disconnected 事件会 invalidate | `service._poll_loop` 对账离开 ONLINE 即 `perception.invalidate()` | 新增 1 条 service 测试（spy 计数） | P4 真机 `/kick` 后 available=False、semantic=None |

## 4. 感知真相源与门禁 P1–P5（E3/E4）

| 门禁 | 结果 | 关键真实数据 |
| --- | --- | --- |
| P1 原始快照一致性 | PASS | 3 个落点，感知坐标与 runtime `/status` **最大差 0.000**；维度 overworld；第三个独立源（第二客户端实体）因视距/距离拿不到 → 如实记 BLOCKED（`_r1_observe_probe.js` 返回 target_not_visible） |
| P2 分层刷新 | PASS | 14 次采样（1.6s 间隔）：near age 0.12–0.98s、local 0.1–3.38s、extended 3.38→14.61s **单调增长**；每层 `captured_at` 真实前进 |
| P3 真实世界变更 | PASS | op `/setblock` 于 (-251,68,-143) 放 glass → `dig_capability=glass`、exact `find_blocks` 命中 **1**；半径 8 内 bedrock 命中 0（明确空结果）；已还原 air |
| P4 断开 | PASS | 真实 `/kick Catodayo` → `available=False`、`online=False`、`semantic=None`、**不再给出旧坐标**；重新进世界后重建真实坐标基线 |
| P5 目标冻结链 | PASS | 真实 `find_blocks(oak_log)` → 产品 wood 规划器 → 计划动作坐标 **2/2** 全部出现在同一时刻真实观察集合；计划带 observations；临时夹具已还原 |

## 5. 导航门禁 N1–N12（E3/E4，真实 Java 服务器 + 真实 Runtime）

口径：`move_to` 到达 = 方块格 3D 距离 ≤ 1.5（重新读取实际位置）；任务级后置条件同为方块格、半径 2.0；本脚本再独立重读 `world_view()` 坐标做 E4 核对。

| ID | 场景 | 重复 | 结果 | 证据摘要 |
| --- | --- | --- | --- | --- |
| N1 | 已在半径内 | 1 | PASS | 位移 0.00、格距 0.0、`action_id` 真实 |
| N2 | 平地 3 格 | 3 | PASS 3/3 | 位移 1.42–2.07，格距 ≤1.41；其中 1 次浮点距 2.27 仍任务级成功（RC3 修复） |
| N3 | 平地 7 格 | 3 | PASS 3/3 | 位移 5.96–7.90，用时 1.5–7.0s |
| N4 | 8–16 格 | 3 | PASS 3/3 | 位移 10.33–11.69，用时 4.0–4.5s |
| N5 | 绕障（op 夹具墙，已还原） | 2 | 1 PASS / 1 FAIL（**fail-closed**） | N5-2 绕行 5.85 格到达；N5-1 绕行 6.92 格后 30s 动作超时，事件 `reason=action_timeout_after_path_search_timeout`（RC2 证据），绝不 SUCCEEDED |
| N6 | 高一格/斜坡 | 2 | BLOCKED | 该测试地形 16 方向无「高一格且四邻平坦」落脚点（脚本如实跳过，未伪造） |
| N7 | 真实不可达（六面实心） | 1 | PASS（fail-closed） | 全程未 SUCCEEDED；结构化终止 `action_timeout_after_path_search_timeout`；Goal/listener 已清 |
| N8 | 搜索预算边界 | — | BLOCKED | 无法在安全测试区稳定构造；替代证据：Node 探针真实 `timeout` 事件 + `move_to.test.js` 单元级 timeout 分支 |
| N9 | 目标在观察后变化 | 结构层 | PARTIAL | 目标失效/变化语义由 `TaskRuntime` 既有 `WORLD_CHANGED/TARGET_LOST` + 冻结计划/逐步后置验证承载（本轮未重跑采集闭环，属 8F/8G/8I 已验收域）；R1 未新增目标跟踪系统 |
| N10 | 行走中 STOP/CANCEL | 1 | PASS | `WAITING_ACTION → CANCELLED`，6 秒后不复活，唯一终态 |
| N11 | 迟到/重复/竞态 | 单测 | PASS | `move_to.test.js` I/K/J（终态幂等、晚到 goal_reached 不翻案）全绿 |
| N12 | 断线/可用性 | 部分 | PARTIAL | P4 真实 `/kick` 覆盖断线不可用；维度不一致用例未单独构造（现有 `validateWorldCoords` 只做世界边界，无维度字段——如实列为边界） |

真实服务器移动证据等级：以上均为 **E3**（真实服务器 + 真实 CatooBot Runtime/MinecraftService + 当前工作树代码）；到达结论另由脚本**独立重读**世界坐标核对（**E4**）。第二 Mineflayer 客户端在 P1 尝试中未取得罐头实体（视距限制）——如实记录，不用它替代产品链路证据。

## 6. 改动文件（本轮实现）

* `minecraft_runtime/runtime.js`：move_to timeout 语义 + `terminalDetail` + 搜索超时标记。
* `minecraft_runtime/action_runtime.js`：`withTerminalDetail()`，TIMEOUT/CANCELLED 带 detail。
* `app/tasks/planner.py`：`_cell()`（floor）与探索计划后置条件同口径。
* `app/tasks/runtime.py`：`_floor_cell()`，`_read_position_once` 改方块格判定 + `raw_distance` 上报。
* `app/integrations/minecraft/world.py`：local 层守卫、每层 `captured_at`。
* `app/integrations/minecraft/service.py`：状态对账失效感知。
* 测试：`minecraft_runtime/test/move_to.test.js`、`tests/test_minecraft_world.py`、`tests/test_minecraft_service.py`、`tests/test_agent_plan_explore.py`、`tests/test_task_runtime_explore.py`。

## 7. 保护区审计

* 未改：`ACTION_RISK`/`TOOL_ACTION`（`agent.py` diff 0 行）、`allow_medium` 默认值、工具白名单/schema、Policy、`TaskAuthorization`/`TaskStepAuthorization`、双确认门、冻结计划与 `plan_hash` 语义、`TaskRuntime`/`ActionRuntime` 生命周期与 STOP 权威、数据库 schema/迁移（无新增）。
* 未放宽：`MOVE_TO_RADIUS`(1.5)、`MOVE_MAX_DISTANCE`(64)、`MOVE_TIMEOUT_MS`(30000)、pathfinder `thinkTimeout`、任务级后置条件半径(2.0) **全部保持原值**；本轮的「口径一致」是把后置条件改成与动作相同的方块格判定，而不是放大任何阈值。
* 未新增：Minecraft 能力/工具、平行导航执行器、第二套状态机、数据库表/字段/迁移。
* 本地专用文件（不入镜像）：`scripts/r1_nav_smoke_real.py`、`scripts/r1_perception_smoke_real.py`、`minecraft_runtime/_r1_search_probe.js`、`minecraft_runtime/_r1_observe_probe.js`、`CatooBot_backups/_r1_*`。收尾时已删除 `minecraft_runtime/_r1_*.js`。

## 8. 未解决边界（OWNER 明确）

1. **N6/N8 无法在现有测试地形/权限内稳定构造** → 后续真实环境专项（需要可控斜坡地形与可控搜索预算夹具）；本轮不伪造 PASS。
2. **N5 绕障 2 次中 1 次 30s 动作超时** → 绕障可靠性仍低于平地；归 R2/导航专项，R1 已证明 fail-closed 且带原因。
3. **N7 的「真不可达」在本环境表现为搜索预算耗尽而非快速 `no_path`** → pathfinder 对封闭目标不提前放弃；如需更快失败需专门评估（不改超时阈值）。
4. **`find_blocks` 无 coverage/captured_at 字段** → 「已加载范围为空」与「区域未加载」在数据层仍不可区分（审计发现，未改协议，属后续观察契约专项）。
5. **维度一致性**：`validateWorldCoords` 只校验世界边界，无维度字段 → 跨维度目标需后续阶段（R1 未扩大工具 schema）。
6. 配方变体排序、`test_direct_mention_not_blocked_by_sleeping` 时段抖动：按 R0 归属保持**不在本轮**。

## 9. 发布与 CI

* 实现+测试+文档提交：`38ca66d0271550846233c0466843112286583db9`（337 commits）。
  镜像 `HEAD == origin/main`（= 本文件所在提交），`dirty=0`，`scripts/` **0 tracked**，受保护区 diff **0 行**，`git diff --check` exit 0。
* CI：[run 39061860694](https://github.com/rinsoraa/CatooBot/actions/runs/39061860694) **attempt 1 两个 job 均 success**
  （`webui · typecheck · tests · build`、`lint · format · types · tests`），零非成功步骤，**本轮没有触发既有 E2E flake**。
  （记录口径：若后续补文档提交再次触发，另行记录 attempt；重跑通过不等于根因修复。）

## 10. 结论

R1 的真实世界基线成立：**观察到的世界可被独立验证**（P1–P5）、**声称抵达时真实位置确实抵达**（N1–N4、N5-2，独立重读）、**走不到时能说明原因并清理**（N5-1/N7：结构化失败 + Goal/listener 清理 + 事件带 detail）、**状态变化/取消不会继续信任旧目标**（N10、P4）。

裁决 **PARTIAL** 的唯一理由是覆盖度：N6/N8 因环境无法构造、N5 有 1/2 次超时、N9/N12 部分依赖既有已验收链而非本轮重跑。**没有任何 false-success、没有访问过期目标的成功、没有 STOP/CANCEL 后复活、没有真实可达场景缺少证据却被记为通过。**

FOLLOW-UP CREATED: NO
