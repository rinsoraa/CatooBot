# R3 检查点：Python 采集证据消费者与恢复边界

日期：2026-10-20。实施依据为开工前归档的[任务书](specs/CatooBot%20Minecraft%20R3%20Python%20Collection%20Evidence%20Consumers.md)（spec sha256 `f909c4521070fccbcbb71f4a524c4c68285e6d9256222d38411f8f31b5566026`，归档收据与证据见 [evidence JSON](evidence/MINECRAFT_R3_CONSUMER_EVIDENCE.json)）。本项门禁全部 PASS，**R3 仍为 IN_PROGRESS，原 G1–G4 均未由此关闭，R8 未完成**。不据本子项进入 R4。

## 1. 实现

新增纯消费者 `app/tasks/collection_evidence.py`：只读当前冻结任务记录，逐步骤复核每一段采集证据，绝不读世界、不重发动作、不写库、不改变授权或预算：

- 严格破坏证明：schema 恰为 3、`server_block_break` 来源、action/session/reservation/event/actor/维度/坐标/方块前后态与实际步骤参数一致；
- 恰好一个服务端掉落，且 UUID/实体 ID/物品/数量为有界真整数并与本次破坏精确关联；
- 拾取必须绑定同一次 dig（break_action/event/reservation）、同一连接（session_ref/seq/actor/维度/实例）与解析后的参数，库存前后必须是恰好 +1 的真整数；
- 单一连接贯穿全部 dig，action/reservation/event/drop-UUID/entity-ID 全局唯一（拒绝重复贡献）；
- 终态（`before_step_id=None`）要求每个已验证 dig 都被恰好的 pickup 消费；派发前只校验已完成前缀；
- 旧缓存 "已验证成功" 不被读取（字段 `record.result["collection_verification"]` 只写不读）；
- 非采集任务严格 no-op（`checked=False, ok=True`），P3 原子破坏、探索、合成、取消/过期/授权语义不变。

接线三处：`app/tasks/runtime.py`（`_ensure_steps_verified` 顶部：每次派发前 + `_finish` 终态）、`app/tasks/skill_learning.py`（资格门与复用判定）、`app/integrations/minecraft/memory_bridge.py`（成功终态写记忆前）。资源/权限/表/TTL/步数/重规划/等待预算全部未改，MEDIUM 默认关闭。

**一处有意的语义选择**：归因本身有缺陷（`break_attribution`/`break_proof`）时沿用既有"世界变了 → 等新计划"路径（`COLLECTION_DEFERRED_REASONS`），只有**证据图**缺陷（掉落/拾取/连接/身份复用/部分完成）才新增硬失败 `FAILED(VERIFICATION)` 且零后续派发。理由：不改动此前已验证的外部归因语义，同时满足"不完整证据不得继续派发"。

## 2. 门禁结果

| 门禁 | 结果与证据 |
|---|---|
| 确定性测试 | 新增 `tests/test_collection_evidence.py` **72 项**：13 项正向（12 个受支持目标 ID + 1 个通用木材任务）、46 类确定性反例（缺失/错类型/未知版本/目标/actor/session/action/reservation/event 错配/旧实体/重复贡献/错物品/错数量/库存不净增/终态不可复活）、7 项图级规则（缺拾取、重复拾取、身份复用、已完成前缀、缓存否定、取消不复活、非采集 no-op）、3 项真实 TaskRuntime 集成、3 项消费端联动 |
| 零后续派发（真 TaskRuntime） | 坏 drop 证据 → 任务 `FAILED/VERIFICATION`，且 `minecraft_dropped_items`/`minecraft_pickup_item` **从未派发**；正向完整证据任务 `SUCCEEDED` |
| 原子 P3 兼容 | 非采集单 dig 任务照常 `SUCCEEDED`，`collection_verification` 不存在（严格 no-op） |
| 真实记录离线复核 | `scripts/verify_collection_evidence_offline.py`：24/24 真实归一化记录 accepted、62/62 注入反例按期望原因码拒绝、96/96 跨用例扫描、2/2 对照；源文件 sha256 运行前后一致。**明确声明：未执行任何新的世界任务** |
| 全量 pytest | 5019 项（基线 4947 + 新增 72）：**5019 passed**，795.28 秒，零失败。此前一轮曾出现 1 项既有 test_runtime_scheduler 时序用例在全量负载下偶发失败（单跑 5/5 PASS，未改断言、未放宽），最终复跑全绿，两轮日志都保留 |
| ruff / format / mypy | `ruff check app tests scripts` PASS；`ruff format --check app tests scripts` 仅剩既有本地脚本 `scripts/r1u_final_gates.py`（未纳入发布、HANDOFF 已记录）；`mypy app` **351 files PASS** |
| Node/Java 复用判定 | 逐文件核对协议检查点 692 项清单：`minecraft_runtime` **33/33**、`server_mod` **30/30** 字节相同 → 仅复用既有证据；本轮改动 6 个 Python 文件（3 app + 3 tests） |
| 归档 | 改前源码 `data/r3/consumer-20261020/source-before.zip`（sha256 `3d577575…`）、改前清单、首轮 80 项失败日志、最终日志、离线复核 JSON、静态检查日志、evidence JSON 与本报告 |

## 3. 首次失败与修复范围（如实记录）

- 基线（11:28 未提交状态）全量 **80 failed / 4867 passed**（`red-baseline/pytest-first.log`）：`test_skill_feedback` 30、`test_skill_service` 22、`test_skill_closure` 13、`test_skill_learning` 6、`test_skill_wiring` 3、`test_dig_attribution` 2，其余 4 个模块各 1。`4867 + 80 = 4947` 与冻结基线精确相等，证明零新增/删除测试、全部为新引入回归。
- 根因：共享夹具停留在 schema-2（`confirm_basis=self_break_progress`、无 `server_proof`），与新契约冲突——**不是消费者判错**。
- 修复范围：新增 `tests/collection_facts.py`（与 24 条真实记录同形的 schema-3 证据构造器）；`tests/skill_fakes.py` 默认严格证据改为 schema-3、负向预设显式保留 schema-2 历史基线、`resource_record` 按本条记录的方块/坐标/动作构造 dig+pickup 结果；`tests/test_dig_attribution.py` 的 Step 替身补参数、串号用例按 schema-3 绑定语义收紧；`tests/test_minecraft_memory_recovery.py` 的 dig 证据按其自身坐标生成；`app/tasks/runtime.py` 增加 deferred 分支。**未放宽任何采集门禁，未修改历史证据。**
- 期间一次 `test_runtime_scheduler` 时序用例在全量负载下失败；该用例单跑 5/5 PASS，属既有 flaky，未改断言。

## 4. 现场与边界

本轮未启动/重启日常 Bot、Bridge 与测试服，未部署 JAR、未发 QQ 消息、未操作世界；改动只发生在开发目录（无 Git）。

离线复核脚本读取的是未发布的真实记录（data/ 不入库），与既有检查点脚本同性质：它是开发目录内的复核工具，不是发布物的一部分；其源码与输入散列都记录在 evidence JSON 里。发布镜像仍为干净的 `main` / `d121127ef5b78016430c56a6c3f15f9d2a4cb6a4`，本轮**未**同步、提交、推送或刷新远端，也未宣称任何 CI 结果。

## 5. 剩余门禁

- G1：正常 Life/Goal/Memory、技能/重规划、真实用户通道与共享准入的正常装配验证。
- G2：本项只关闭 Python 消费者子项；跨代/重放/乱序/竞争的完整反例矩阵与 Node 全链消费者仍需按原范围另行取证。
- G3：最终源码逐 ID 工具/耐久、危险/路径、旧实体/合并/其他玩家、库存不净增的真实矩阵。
- G4：允许的真实 QQ、最终工程结果、当前世界新备份/测试/恢复、白名单发布与对应 CI/COMPLETE 回执。

R4–R8、Integrated/LAN 与真实长期 Life/Play/Learning 验收保持原要求。

## 6. 待确认

1. 归因缺陷沿用 REPLANNING、证据图缺陷改为 FAILED——是否需要统一成同一种非成功语义？
2. `record.result["collection_verification"]` 目前只写不读；是否要暴露给 WebUI/API 供人工核对？
3. `memory_bridge` 在"任务 SUCCEEDED 但证据不成立"时整条记忆都不写（含 TASK 经历）——是否符合产品期望，还是应写一条明确的 UNKNOWN 经历？
4. `app/tasks/skill_learning.py` 的全文件 CRLF→LF（继承自 11:28 未提交修改）是否需要在发布前恢复原行尾？
