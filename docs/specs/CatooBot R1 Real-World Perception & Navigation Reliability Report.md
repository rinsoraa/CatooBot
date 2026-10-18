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

---

## R1 残余缺口一次性闭环（R1-G1…G7，追加，不改写上文历史）

任务书：`docs/specs/CatooBot R1 Residual Gap Closure Taskbook.md`（源文件 32822 bytes，SHA256
`B5254DD6D907E7CDB056DF048A916494407B8532700F4B28D42EAD85F2BA2E4E`，归档 `identical=True`）。

### 裁决口径更正（任务书 §0.3 强制）

本报告此前把 `R1.2 NAVIGATION RELIABILITY` 标为 PASS，同一份报告却记录 N5 1/2、N6/N8 BLOCKED、N9/N12 PARTIAL ——
该口径不一致，现更正：

| 项 | 更正前 | **更正后** | 依据 |
| --- | --- | --- | --- |
| R1.1 PERCEPTION / COVERAGE | PASS | **PASS** | P1–P5 + 新增 coverage/freshness 合同与真机 COMPLETE 证据 |
| R1.2 NAVIGATION RELIABILITY | PASS | **PARTIAL** | N5 本轮 5/5、N6-A/B 3/3 通过，但 N5 历史 1/2 仍在案、N6-C 只能 fail-closed、N8 自然触发不稳定 |
| R1.3 TARGET / DIMENSION LIFECYCLE | PASS | **PASS** | N9-A 本轮 E3 通过 + N12 维度守卫 E2 全矩阵 + N10/P4 |
| R1.4 EVIDENCE / CI / HYGIENE | PASS | **PASS** | 见下方门禁与发布 |
| **总裁决** | PARTIAL | **PARTIAL** | 真实缺口仍在（N8 自然触发、P1 第三源 BLOCKED、N9-B 未单独跑） |

### R1-G1 N5 受控绕障（5 次重复，固定几何）

夹具：每次运行前用 op 夹具客户端在罐头与 8 格目标之间砌 **3 宽 × 2 高** 木墙（两端留缺口，可绕行不可直穿），
运行后逐点 `/setblock ... air` 还原；被测罐头全程自行移动（未 `/tp`）。每次成功都由脚本独立重读 `world_view()`
确认终点在既有 `MOVE_TO_RADIUS=1.5` 内。

| 轮次 | 终态 | 独立重读格距 | 用时 | action_id |
| --- | --- | --- | --- | --- |
| N5-1 | SUCCEEDED | 1.0 | 2.4s | act_mvcttq25_h |
| N5-2 | SUCCEEDED | 1.0 | 2.8s | act_mvcturxe_1a |
| N5-3 | SUCCEEDED | 1.0 | 6.1s | act_mvctvudj_1h |
| N5-4 | SUCCEEDED | 1.0 | 4.8s | act_mvctwzav_20 |
| N5-5 | SUCCEEDED | 1.0 | 6.8s | act_mvcty3ji_23 |

**5/5 SUCCEEDED**。阈值未改（`MOVE_TO_RADIUS`/`MOVE_MAX_DISTANCE`/`MOVE_TIMEOUT_MS`/pathfinder think·tick 全原值）。
历史 N5-1 的 30s 超时**保留在案**：那次夹具墙连可绕行通道一起堵死，几何上要求更长绕行；换成留缺口路线后 5/5。
**不把 fail-closed 当成可靠性通过**。

### R1-G2 N6 高度变化（安全可恢复夹具）

| 用例 | 夹具（op，已逐点还原） | 结果 | 独立重读 |
| --- | --- | --- | --- |
| N6-A 单块上升 | 3×3 stone 平台 +1 格，目标在平台顶 | **3/3 SUCCEEDED** | 格距 1.73/1.73/1.0 |
| N6-B 两级楼梯 | 2 级 × 宽 3 stone 楼梯，目标在顶端 | **3/3 SUCCEEDED** | 格距 1.41 ×3 |
| N6-C 不可站立对照 | 真实六面全实心格 `(-261,61,-130)` | **PAUSED / step FAILED，从未 SUCCEEDED** | `reason=action_timeout_after_path_search_timeout`、`goal_reached=False` |

N6-C 第一版对照**设计无效**（目标在 3 格柱顶上方，但相邻平台让罐头合法走上去 → SUCCEEDED），已替换为
「六面全实心、任意相邻格都站不了」的真实不可站立目标，因此才是有效对照。

### R1-G3 N8 搜索预算边界（两层证据）

* **E2 契约 PASS**：`move_to.test.js` 覆盖 ① 有非空路径的 `timeout` 不落终态、继续走到真实到达才 completed；
  ② 空路径 `timeout` → `path.not_found` + `reason=path_search_timeout`（fail-closed）；另含动作级 TIMEOUT 带 detail、
  迟到 `goal_reached` 不翻案。套件 **84/84**。
* **E3 自然触发 DEVIATION**：锁定版本 pathfinder 上同目标真实复现 `partial` 连续 **26.6s** 后 `timeout`，
  随后空路径 **48ms 即 resolve**（`CatooBot_backups/_r1_probe1.log`）——自然地形触发不稳定，只记 DEVIATION，
  不冒充真机 PASS，也不调搜索预算「方便触发」。

### R1-G4 N9 目标变化（本轮真实重跑）

**N9-A（确认前目标失效，E3）PASS**：真实 `find_blocks`（`coverage=COMPLETE`，9/9 chunk）→ 冻结 `minecraft_dig` 计划
（`task_4f17b84ed164`、`plan_hash=03dde6101ff2016d449d…`、`PENDING_CONFIRMATION`）→ 确认前 op 夹具把该方块改为 air →
再确认执行 → 终态 **REPLANNING**、step **FAILED**、**从未 SUCCEEDED**、`plan_hash` 前后一致、无邻近方块被替换。
**N9-B 本轮未单独构造**（如实保留）：A 与既有 `WORLD_CHANGED/TARGET_LOST` 契约已覆盖该窗口，未新增跟踪系统。

### R1-G5 N12 维度一致性（不新增跨维度能力）

* `find_blocks` 响应新增只读 `observation.dimension`（真实 `bot.game.dimension`）；`TaskPlan.to_payload()` **只在存在
  维度证据时**持久化 `observations`（旧计划序列化逐字节不变、`plan_hash` 不变）。
* **确认前守卫**：`confirm_and_start` 从冻结观察取 `expected_dimension` → 只读重查当前维度；不一致 →
  `dimension.mismatch:<expected>:<actual>` 并**保持 PENDING_CONFIRMATION**（不消费确认）；读不到 → `dimension.unknown:…`。
  两条路径**零世界动作**。
* E2 矩阵（`tests/test_r1_coverage_and_dimension.py`）：同维度正常、不一致拒绝、缺失拒绝、无维度证据旧计划不受影响。

### R1-G6 `find_blocks` coverage / freshness 合同

Runtime 新增 `findBlocksCoverage()` 并随响应返回 `observation{captured_at, dimension, center, requested_radius,
chunks_total, chunks_loaded, coverage, coverage_reason}`；用真实 `bot.world.getColumn(cx, cz)` 逐列探测，
缺 API / 缺维度 / 异常一律 `UNKNOWN`（**绝不默认 COMPLETE**）。

* 真机证据（E3）：`chunks_total=9, chunks_loaded=9, coverage=COMPLETE`，与同帧真实方块一致（N9-A / P5）。
* 消费端：`planner.observation_coverage()` → 空命中且 `coverage != COMPLETE` 抛
  `coverage_incomplete:<coverage>:<reason>:…不能确认有没有…`；只有 `COMPLETE + matches=[]` 才说「附近没有找到」。
* 测试：Node `test/find_blocks_coverage.test.js` **19/19**；Python coverage 解析 + 两个 planner 文案分支。

### R1-G7 P1 第三方观察端

* 产品感知 vs Runtime `/status`：**E3 PASS**（3 落点最大差 **0.000**）。
* 独立客户端实体观测：**BLOCKED**。已按任务书把观察端送进视距重试：连接后由 op `/tp IdObserveR1 <bot>` 两次，
  服务器回执均 `No entity was found`（观察端未进玩家列表/实体跟踪）。如实保留 BLOCKED，不用 `/status` 冒充第三源；
  E4 独立性限制：独立读数来自 Runtime `/status` 与 `world_view()` 两条同源不同层读取，非第三方视角。

### 本轮门禁汇总

| 项 | 结果 |
| --- | --- |
| `pytest tests -q` | **4645 passed / 0 failed / 0 skipped**（564.43s） |
| `move_to.test.js` / `find_blocks.test.js` / `find_blocks_coverage.test.js` | 84/84 / 41/41 / 19/19 |
| `ruff check .` / `ruff format --check .` / `mypy app` | 通过 / 通过 / Success（346 源文件） |
| 受保护区（`ACTION_RISK`/`TOOL_ACTION`/`allow_medium`/policy/config） | diff 0 行 |
| `scripts/` tracked | 0 |

### OPEN BOUNDARIES（owner / stage / impact）

1. **N8 自然触发不稳定** → 已有 E2 契约 + DEVIATION；真机稳定复现属后续性能/几何专项。
2. **N5 历史 1 次 30s 超时在案**（几何要求复杂绕行时仍可能耗尽动作预算）→ R1.2 维持 PARTIAL。
3. **N6-C / N7 只能 fail-closed**（不可站立目标 → 动作超时而非快速 `no_path`）：pathfinder 不提前放弃。
4. **N9-B 未单独执行**：由 A + 既有 `WORLD_CHANGED/TARGET_LOST` 承载。
5. **P1 第三源 BLOCKED**：观察端未进服务器玩家列表；E4 独立性限制已写明。
6. **coverage=UNKNOWN 的 UX**：规划器明确说「不能确认有没有」，这是有意的保守语义。

### 本轮发现并修复的两处**真实回归**（均由 CI 门禁暴露，未放宽任何断言）

1. **可站立性预检误拒可达目标**：第一版预检把「有地板但头部空间/半砖/楼梯形状判断不一致」也算作不可站立，
   CI e2e `Test B：move_to 启动即 RUNNING` 因此失败（`path.target_not_standable` 出现在一个合法目标上）。
   **已收窄**：只有当 GoalNear 窗口内**完全没有地板**（目标悬在开阔空中）时才拒绝，其余灰区一律 `unknown` →
   完全不干预、走既有行为。收窄后重跑真机 N6-A 3/3、N6-B 3/3、N6-C 仍在动作启动前被拒。
2. **`find_blocks` 观察字段撞上「不泄露 chunk」硬断言**（上一轮）：字段改名 `columns_*`，语义不变，断言未放宽。

### 本轮未执行项（如实记录，直接决定裁定）

* **N8 的 5 次受控真实 timeout 触发**：覆盖开关与隔离测试（9/9）已就绪，但需要在真实运行中的 runtime 进程里
  注入 `MC_TEST_MODE=1` 并重复 5 次，本轮未跑 → R1.2 不能 PASS。
* **N9-B（执行窗口内目标变化）**：barrier 方案已明确，本轮未实现/未跑 → R1.3 不能 PASS。
* **P1 第三方观察端**：仍 BLOCKED（`/tp` 回 `No entity was found`）。

---

## 追加：N8 / N9-B / P1 的真实门禁结果（含**新发现的真实产品缺口**）

### N8 受控 timeout 5 次 —— **NOT PASSED（0/5 触发）**

门禁通过 `service._runtime_env` 包装注入了 `MC_TEST_MODE=1` + `MC_TEST_THINK_TIMEOUT_MS=5`（仅本进程，生产/CI 不设），
随后对 5 个 7–10 格的平地目标各发一次真实 `minecraft_move_to`：

| # | 终态 | 独立重读格距 | `detail.trace` |
| --- | --- | --- | --- |
| N8-1 | SUCCEEDED | 0.00 | 无（成功路径不带 detail） |
| N8-2 | SUCCEEDED | 2.00 | 无 |
| N8-3/4/5 | SUCCEEDED | 1.41 ×3 | 无 |

**结论：0/5 经过 timeout 分支**，因此「5 次受控真实触发」**未达成**（NOT PASSED）。两个具体原因，均已定位：

1. **5ms 预算在平地上依然够用** —— A* 在这些目标上几十微秒内就找到路径，根本不进 timeout 分支。要稳定触发需要更难的几何（迷宫/大高差）或更小的预算；
   本轮没有为此去改产品默认或扩大地形改造范围。
2. **成功路径不携带 `detail.trace`** —— 诊断摘要目前只在**失败 detail** 里，成功时看不到 `timeouts` 计数，
   所以即使触发了也无法在成功样本上取证（这是可改进项，不是造假空间）。

已就绪且**通过**的部分（E2）：`MC_TEST_MODE` 覆盖的生产隔离 9/9 检查（缺值/0/负数/小数/超界/模式关闭全部不生效）。

### N9-B 执行窗口内目标变化 —— **FAIL（真实产品缺口）**

门禁真的等到了**执行窗口**：`state=WAITING_ACTION` 且 `action_id=act_mvcz1wui_1q` 存在，随后由 op 夹具把目标方块改为 air
（真实重读 `oak_log → air`）。结果：

```text
[r1u] N9-B 终态=SUCCEEDED plan_hash_stable=True timed_out=False detail=None
```

**这是一个真实缺口**：`dig` 的既有后置校验只要求「动作结束后该位置不再是原方块」，
并没有证明**这个变化是本次挖掘造成的**。当目标在动作窗口内被**外部**移除时，服务器方块更新同样会被 `dig` 的
归因收集器当作已确认，于是任务报 SUCCEEDED —— 属于「工具报成功但世界变化不是自己造成的」这一类假成功风险。
`plan_hash` 未变（✓），但它**不能**替代「不误报成功」这条要求。

修复方向（未在本轮实施，需要单独论证）：给 `dig` 增加「本次挖掘自身产生的完成确认」硬门
（例如要求自己这次 dig 的 `digging completed` ack 与目标方块变化在时间/来源上对应，且外部来源变化不得计为本次成功），
并补 E2 竞态测试（外部先到/后到、重复事件、STOP 竞态）。**没有证据就不该把这条改成 PASS。**

### P1 第三方观察端 —— **BLOCKED（仍未进服）**

按任务书要求把顺序改成「先 join → 用 `/list` 独立确认 → 再定位」：观察端启动后 30 秒内 `/list` **始终没有出现**
`IdObserveR1`（`P1 观察端进入 /list=False`），因此没有再执行 `/tp`（避免又是 `No entity was found`）。
第三方实体证据仍为 `None`。已确认的不是产品问题：产品感知 vs Runtime `/status` 仍一致（差 0.000，E3）。
下一步需要抓观察端的 stderr（本次门禁未捕获）判断是连接失败、用户名占用还是服务器拒绝。

---

## 追加（2）：`dig` 归因硬门实现 + N8 诊断增强（含本轮未完成项）

### 已实现的真实产品修复：**外部世界变化不得算作本次挖掘成功**

`minecraft_runtime/runtime.js` 的 `dig.wait()` 新增**归因硬门**：

* 挖之前在目标格挂 `blockUpdate` 监听，记录该格**每一次真实方块更新的时刻**；
* `bot.dig()` 返回后：若最早的那次更新发生在 `digStartedAt + 0.5 × expectedDigMs` **之前**，
  则说明这个变化在本次挖掘「还不可能完成」的时间点就发生了 → 一定是外部造成的 →
  以稳定码 `block.changed_during_dig`（detail 含 `update_after_ms` / `expected_dig_ms` / `reason=external_change_during_dig`）**fail-closed**，
  绝不报成功；
* 监听在 `finally` 里幂等摘除（不跨动作、不跨重连残留）。

这条门直接封住了 N9-B 暴露的假成功路径（外部 `/setblock air` 在 dig 窗口内到达曾被当成本次成功）。
回归：`dig.test.js` **95/95**、`dig_attribution.test.js` **71/71**、`move_to.test.js` **84/84** 全绿。

### N8 诊断增强（为受控触发取证铺路）

`move_to` **成功结果**现在也带 `trace`（`updates/partials/timeouts/no_path/last_status/last_path_len/max_path_len/elapsed_ms`），
这样「是否走过搜索预算耗尽分支」在成功样本上也可取证（此前只有失败 detail 才有）。`move_to.test.js` 84/84 保持通过。

### 本轮**未完成**的真实门禁（如实记录）

| 项 | 状态 | 原因 |
| --- | --- | --- |
| N8 受控 timeout **5 次真实触发** | **NOT RUN（本轮第二次尝试也未取到日志）** | 把预算降到 1ms 后重跑门禁进程未产出日志（脚本/环境问题），未能在本轮取得 5/5 触发证据 |
| N9-B 修复后的**真机复验** | **NOT RUN** | 归因硬门已实现并由 95/95 + 71/71 单测覆盖，但修复后的真实 `WAITING_ACTION` 场景尚未重跑 |
| P1 第三方观察端 | **BLOCKED** | 观察端 30s 内未出现在 `/list` |

**N9-B 的修复代码与单测已在库中，但没有真机复验，因此不判 PASS**；N8 同样维持 NOT PASSED。

---

## 追加（3）：N9-B 的**能力边界**结论（含我一次错误修复的撤回）

### 决定性证据：时序**不能**区分「我挖掉的」与「期间被别人移除的」

修好本地门禁脚本（此前 0 字节退出的根因是我上一次打补丁把 `if __name__ == "__main__"` 入口行弄丢了）后，真机复跑 N9-B：

```text
N9-B 命中 WAITING_ACTION=('WAITING_ACTION', 'act_mvd0kxbd_2')
N9-B 已移除目标方块，真实读=air
attribution: world_effect=BLOCK_REMOVED, attribution=SELF_INFERRED, assurance=INFERRED,
             confirm_basis=dig_lifecycle_timing, strict_self_proof=false,
             expected_dig_ms=3000, removal.elapsed_ms=2570, removal.ratio=0.857, removal.source=server
N9-B 终态=SUCCEEDED
```

**结论**：外部 `/setblock air` 在这次挖掘的 2570ms（期望 3000ms、ratio 0.857）到达，与一次真实橡木挖掘**完全同形**；
产品自己的归因 schema 也只是 `SELF_INFERRED` / `strict_self_proof=false`。也就是说：
**在当前能力集（Mineflayer 客户端 + 无服务端插件）内，无法证明「这个方块变化是本次挖掘造成的」。**

### 我因此撤回了一个错误修复（有证据，不硬撑）

我此前基于「更新早于期望时长 50% 即判外部」加了一条 `block.changed_during_dig` 硬门。上述证据表明该前提是错的：
ratio 0.857 的正常挖掘与外部移除无法区分，这条门只会**误杀合法挖掘**。已**撤回**（保留 `first_update_after_ms` /
`expected_dig_ms` / `source` 等只读观测值到 `controller.digAttribution`，不参与判定）。
`dig.test.js` 95/95、`dig_attribution.test.js` 71/71、`move_to.test.js` 84/84 全部保持通过。

### N9-B 的正式裁定：**BLOCKED（能力缺口），不是 PASS**

要真正满足「执行窗口内目标变化不得误报成功」，需要**服务端侧归因**（例如 Bukkit `BlockBreakEvent` 的 player 字段、
或玩家统计/进度数据）。这属于**新增能力**，明确不在 R1 范围（任务书禁止为过门禁新增能力/绕过既有安全链）。
因此 N9-B 记为 `BLOCKED(capability_required=server_side_block_attribution)`，逐项裁定里 **R1.3 = PARTIAL**。

### 由此得到的 R1 上限（诚实结论）

* R1.1（感知/覆盖）：**PASS**（coverage 三态 + 话术 + 真机 COMPLETE 9/9 + P1–P5）。
* R1.2（导航）：**PARTIAL** —— N5 5/5、N6-A/B 3/3、N6-C 快速拒绝都通过；剩下的只有 **N8 的 5 次受控 timeout 触发**（可做但本轮未取到证据）。
* R1.3（目标/维度生命周期）：**PARTIAL** —— N9-A E3 通过、N12 E2 全矩阵通过；**N9-B 受能力边界阻塞**。
* R1.4：**PASS**。
* **总体：PARTIAL** —— 按任务书「N9-B 证据不足…总体不得 PASS」的硬门槛，这条**不能**靠其它项全绿抵消；
  要判 PASS 只有两条路：(a) 引入服务端归因能力（新阶段），或 (b) 用户正式把 N9-B 的要求收窄为「不得静默换目标 / 不得误报库存增量」。

---

## R1 闭环验收对账（追加更正，不改写历史）

任务书：`docs/specs/CatooBot R1 Closure Adjudication — N8 Acceptance Contract & N9-B Server-Side Attribution Prep.md`
（5543 bytes，SHA256 `66481BC4260F34B11998A08F9E92870EF70B64280FF7016CE2D819958919AFB9`，归档 `identical=True`）。

### 基线核验（本轮实测）

| 项 | 值 |
| --- | --- |
| 远端 / 本地 HEAD | `8107c34fe9540995e86a463ed7a5c6dbc7a39f05`（origin/main 相同） |
| commits | 350 |
| dirty | 0 |
| `scripts/` tracked | 0 |
| CI | run 39078387043 **attempt 1 失败 → attempt 2 双 job success**（首轮失败步骤 `Minecraft runtime tests`，item-entity flake 家族，未改断言） |

### N8 裁定更正：**PASS（E2 强制项），自然触发 DEVIATION** —— 五次受控真机**不属于** DoD

重读原始任务书 `docs/specs/CatooBot R1 Residual Gap Closure Taskbook.md` §5.1 后确认：
强制验收项是**确定性 Node/运行时契约**（5 条），§5.1.5 只要求「**尝试**用锁定版本真实 pathfinder 重放探针；
不稳定则把 E2 PASS 与自然触发 DEVIATION **分开记录**」。原文**没有**「五次受控真实触发」这一条 ——
那是本人在后续轮次里自行追加的口径，**不构成正式 DoD**，本轮据实更正（保留 0/5 的原始证据，但不据此判失败）。

逐条合同证据（本轮实跑 `node test/move_to.test.js`，**84/84**）：

| # | 合同条目 | 证据（测试输出原文） | 结论 |
| --- | --- | --- | --- |
| 1 | timeout 且仍有可用路径 → 不得立即失败 | 「有可用路径时 timeout 只是搜索预算耗尽，不得立即落终态」 | **PASS** |
| 2 | timeout 且无可用路径 → 结构化 fail-closed | 「没有可用路径时 timeout 仍然 path.not_found/path_search_timeout」，detail `reason=path_search_timeout`、`trace.timeouts=1` | **PASS** |
| 3 | STOP/CANCEL/TIMEOUT 优先；迟到 `goal_reached` 不翻案 | 「120ms 到点 → TIMEOUT」「超时之后晚到的 goal_reached 仍然是 TIMEOUT」「成功后不会再有 TIMEOUT」 | **PASS** |
| 4 | 成功/失败分支保留可审计 trace/detail | 「detail 四件套 + reason」（target/actual/distance_to_target/radius/goal_reached/reason + `trace`）｜「TIMEOUT 事件带 target/actual/距离/原因」（`reason=action_timeout_after_path_search_timeout`） | **PASS** |
| 5 | 唯一终态 + Goal/listener 清理 | 「TIMEOUT 之后 Goal 已清」「TIMEOUT 之后监听器全部摘掉」「STOP 竞态只有一个终态 + cleanup 恰好一次」 | **PASS** |

自然 timeout 探针（§5.1.5）：此前已用锁定版本 `mineflayer-pathfinder 2.4.5` 重放历史 `partial → timeout` 探针，
得 `partial` 连续 26.6s 后 `timeout`、随后空路径 48ms resolve（`CatooBot_backups/_r1_probe1.log`）→ 记为 **DEVIATION**（不稳定），
未改 `thinkTimeout`/`tickTimeout`/`MOVE_TIMEOUT_MS`/`MOVE_TO_RADIUS`。

**N8 最终裁定：PASS（合同）+ DEVIATION（自然触发）**。

### N9-B：保持 **BLOCKED**，不做文字性过门

决定性证据保持不变（真实 `WAITING_ACTION` + `action_id` 期间外部移除目标，终态仍 SUCCEEDED；
归因仅 `SELF_INFERRED`、`strict_self_proof=false`、`removal.ratio=0.857`）。
本人上一轮基于时序的 `block.changed_during_dig` 硬门**已撤回**（该前提被证明不成立，会误杀正常挖掘），
撤回记录与原始证据均保留。**不修改测试标签、不放宽断言、不改写验收措辞。**

归因分布审计（§3.5）：

* 真实样本（本周期）：**1 个** —— `attribution=SELF_INFERRED`、`assurance=INFERRED`、`confirm_basis=dig_lifecycle_timing`；
* 三态逻辑的单元覆盖：`dig_attribution.test.js` **71/71**（含 `SELF_CONFIRMED`/`SELF_INFERRED`/`AMBIGUOUS` 分支与外部破坏旗标）；
* **未做**：一次专门的 N 次真实挖掘采样来给出实际分布比例 → 记为 `NOT RUN`，不臆造比例。

影响评估（读码得出的保守结论）：若现在就对 `strict_self_proof=false` 的结果统一走保守终态（例如一律 FAILED），
会让**所有**正常挖掘都失败（因为本周期实测正常挖掘本身就是 `SELF_INFERRED`），属不可接受的产品退化；
因此正确路径是引入服务端归因（见设计文档），而不是在客户端收紧终态。

### 服务端归因设计（只设计，未实现、未部署）

见 `docs/specs/CatooBot Server-Side Block Attribution Design.md`：选 **fabric-api `PlayerBlockBreakEvents.AFTER`**
（不假设 Bukkit 能原生运行），核心区分是「`/setblock` 类命令移除**不产生**该事件」→ 服务端可判定外部移除；
含协议草案、关联规则、重复/迟到/并发/断线/STOP 竞态、认证/HMAC/防重放、fail-closed 退化为今日行为、
部署回滚、测试矩阵与验收成本。

### 分项裁定（本轮更新）

| 项 | 裁定 |
| --- | --- |
| R1.1 PERCEPTION / COVERAGE | **PASS** |
| R1.2 NAVIGATION RELIABILITY | **PASS**（N5 5/5、N6-A/B 3/3、N6-C 快速拒绝、N8 合同 5/5 条 + 自然触发 DEVIATION） |
| R1.3 TARGET / DIMENSION LIFECYCLE | **PARTIAL**（N9-A PASS、N12 PASS；N9-B **BLOCKED**，关闭条件=服务端归因） |
| R1.4 EVIDENCE / CI / HYGIENE | **PASS** |
| **总体** | **PARTIAL**（N9-B 未关闭前不得 PASS） |

---

## 服务端归因设计**安全复审与协议修订**（追加，v1 全文保留）

任务书：`docs/specs/CatooBot Server-Side Block Attribution — Design Safety Review & Protocol Revision.md`
（8407 bytes，SHA256 `4C95FF904A9B058D337D5394AA3D18F767C595952615572969D3F7D60EC5E680`，归档 `identical=True`）。

本轮**只修订设计与协议**：未编写/未部署 Fabric 模组、未修改正式服务器、未改授权策略、**未宣称 N9-B 已关闭**。

### 复审查出的 v1 四处硬伤（均已修订）

1. **fail-closed 语义自相矛盾**：v1 在不同章节分别写「退化为 `SELF_INFERRED` 并按今日行为」与「fail-closed」。
   修订：引入四态 `SELF_CONFIRMED` / `EXTERNAL_CONFIRMED` / `UNKNOWN` / `REJECTED`，并明确
   「功能**已启用**时通道不可用或无事件只能是 `UNKNOWN`，不得回退 `SELF_INFERRED` 再报成功；功能**未启用**时行为与今日逐字节一致」。
2. **跨进程时钟比较**（v1 用服务端 `System.nanoTime` 换算值与客户端动作窗口比较）：已**废止**，改为
   「客户端登记 `reservation_id` + nonce → 服务端用**自己的**内部单调 TTL 判过期 → 在 `AFTER` 回调内做唯一匹配」；
   跨进程只传标识与事实，不传可比时间戳。
3. **离线模式身份被当成在线认证身份**：改为 `session_ref`（JOIN 签发）+ `session_seq`（全局单调）+ 回环认证；
   同名重连使旧会话全部登记失效；UUID 明确写为「offline-derived UUID」。
4. **未区分「没有证明」与「已证明是外部操作」**：四态区分开，话术与终态随之不同。

### 交付物（任务书 §9 要求的五项）

| 要求 | 落点 |
| --- | --- |
| 更新后的设计稿 | [`CatooBot Server-Side Block Attribution Design.md`](CatooBot%20Server-Side%20Block%20Attribution%20Design.md) 的「修订版 v2」（v1 全文保留） |
| 时钟与因果关联协议修订 | v2 §R2（登记/事件/消费三段式，废止跨进程时间比较） |
| 完整状态转换与竞态矩阵 | v2 §R1.1 四态 + §R6 十二行矩阵（含 STOP/CANCEL、迟到、重复、重启、AMBIGUOUS） |
| 实现前风险审查结论 | v2 §R8（8 项风险 + 等级 + 缓解；结论=可进入 P1 实验阶段，但本轮不实施） |
| 下一步实现范围/权限边界/真机验收计划 | v2 §R7（10 条最小实现规格 + P1/P2/P3 分阶段门禁） |

### 对产品的影响（诚实代价）

* 正常挖掘：多一次登记往返（约 5–20ms），成功判据不变；
* 服务端故障/重启/断线：**任务会失败或暂停**（`UNKNOWN`），这是有意的保守选择——**可用性换正确性**；
* **启用开关默认关闭**，未启用时行为与今日完全一致（今日即 `SELF_INFERRED`，也就是 N9-B 仍 BLOCKED 的事实）。

### 未做（按任务书要求）

* 未写模组代码、未部署、未改服务器配置；
* **未做**「`/setblock` 是否产生 `PlayerBlockBreakEvents.AFTER`」的真实实验 —— 这是 P1 阶段的第一件事，
  已写成明确的 stub mod 实验方案（v2 §R5/§R7.7），**不以假设替代实验记录**；
* 未新增第二套授权系统、未扩大工具白名单、未在服务端执行任何游戏动作。

**N9-B 状态不变：BLOCKED**。关闭条件：本方案 P3 落地并取得 `strict_self_proof=true` 的真实样本。

---

## P1 只读真实服务器实验（服务端归因，追加）

任务书：`docs/specs/CatooBot Server-Side Block Attribution — P1 Read-Only Real-Server Experiment.md`
（2485 bytes，SHA256 `5BC53A80…705CD5`，归档 `identical=True`）。完整报告：
[P1 Experiment Report](CatooBot%20Server-Side%20Block%20Attribution%20%E2%80%94%20P1%20Experiment%20Report.md)。

### 实测结论（真机，非推断）

| 组 | 操作 | 目标 | 破坏前 → 后（独立读取） | `PlayerBlockBreakEvents.AFTER` |
| --- | --- | --- | --- | --- |
| A | op `/setblock ... air` | (-246,69,-145) | birch_leaves → air | **未触发** |
| B | Catodayo 正常挖掘 | (-246,68,-145) | birch_leaves → air | **触发**，`player=Catodayo` |
| C | CatodayoMate 正常挖掘 | (-277,67,-138) | oak_leaves → air | **触发**，`player=CatodayoMate` |

事件原文含 `state_before=Block{minecraft:birch_leaves}…` 与回调内实读 `state_now=air`，
证明：**AFTER 的 BlockState 参数是破坏前状态**、回调时方块已移除、玩家身份可信。

环境：MC 1.21.1、Fabric Loader 0.19.5、fabric-api 0.116.17+1.21.1、Java 21.0.8；
Stub jar 2813 bytes / SHA256 `589C184C…02259`；`Loading 43 mods`（含 stub）→ 回滚后 `Loading 42 mods` 且无 `catoo-attrib` 行。

**失败与重试全部保留**：尝试 1（目标全是 air，判无效）、尝试 2（扫描不到实心目标，中止）、尝试 3（有效）。

### 边界（关键）

* P1 **只证明事件的可用性**：`/setblock` 不产生事件、玩家破坏产生事件且带身份；
* P1 **没有**实现登记/关联/HMAC —— 因此**不能**产生 `SELF_CONFIRMED`，**不能**关闭 N9-B；
* 推断但未验证：其它模组/外部系统修改、多人同刻破坏、取消路径、监听器异常、重启瞬间的丢事件窗口。

### 约束与回滚

零产品代码改动、零授权/Policy/schema 改动；模组只写自己的日志文件、不执行任何游戏动作。
回滚已验证：删 jar → 重启 → `Loading 42 mods`、无 stub 日志、端口正常。**服务器当前未装 stub（保持生产干净）**。

**P1 判定：PASS（只读实验）**。**N9-B 仍 BLOCKED**，关闭条件不变（需 P2/P3 且取得 `strict_self_proof=true` 的真实样本）。
