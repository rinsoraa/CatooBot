# CatooBot R1 综合任务书：真实世界感知与导航可靠性

**任务名称：** CatooBot R1 — Real-World Perception Integrity & Navigation Reliability
**任务目标：** 在现有 CatooBot / Mineflayer / TaskRuntime 架构内，建立“世界观察可信、目标坐标可追溯、导航结果可验证、失败原因可诊断、失败与取消不会产生幽灵动作”的真实世界基线，并修复通过复现证实的根因。
**仓库：** `rinsoraa/CatooBot`
**任务书参考基线：** `32649eb1856feba56e970d51026f79cf6ad477fc`（R0 最终文档校正提交）
**参考提交：** https://github.com/rinsoraa/CatooBot/commit/32649eb1856feba56e970d51026f79cf6ad477fc
**R0 最终报告：** `docs/specs/CatooBot R0 Final Closure Report.md`
**工作性质：** 一个综合、可终止的 R1 工作周期；不是按每项新发现依次拆成 Follow-up 的任务序列。
**初始裁定：** 尚未开始 R1 实测；本任务书定义验收契约，不预设根因，也不预设任何场景必然失败或通过。

> **基线纪律：** 参考 SHA 仅表示任务书编写时的主线基线。开工必须读取当时真实 `origin/main`、镜像 HEAD、ahead/behind、dirty 状态和 CI。若远端已前进，应在最新可安全继续的提交上工作并记录差异；禁止 reset、force-push、覆盖用户变更或将旧 SHA 假称为当前 HEAD。R0 已关闭，不得为此任务重复重做 R0。

---

## 0. 本任务的最终原则：一次性收敛，不无限 Follow-up

### 0.1 本轮必须完成

1. 先对现有感知与导航实现做只读基线审计，再建立可重放的失败/成功场景矩阵；禁止先凭猜测改参数。
2. 在可用真实 Minecraft 服务器上，使用真实 CatooBot Runtime、MinecraftService、真实 Provider（如果场景依赖模型）和真实自动化第二客户端/观察端，采集足以区分“观察错误、目标错误、路径不可达、规划超时、到达判定失败、任务生命周期错误”的证据。
3. 对 R1 范围内可复现的根因，在本轮统一缺陷矩阵中登记并完成根因修复、反例测试、全量回归与真实服务器复验；不得每发现一项就停工并要求新建 Follow-up。
4. 即使没有找到所有路径失败的单一代码根因，也必须交付成功率/失败分类/环境条件的测量结果，并把系统无法保证的能力明确列为 `PARTIAL`，不能通过降低阈值制造 `PASS`。
5. 更新实现、测试、文档、变更记录和 R1 最终报告；对证据不足、无法使用真实服务器或无法构造可重复场景的门禁，必须标记为 `BLOCKED` / `DEVIATION` 并解释影响。

### 0.2 禁止事项

- 不允许只因 `pathfinder` Promise resolve 或事件报 `goal_reached` 就判移动成功；成功必须由既有 runtime 的真实位置再验证成立。
- 不得通过放宽到达半径、增加动作总超时、增加最大移动距离、跳过路径搜索、把失败翻译成成功或更改失败阈值来提高通过率。
- 不得 `/tp` 罐头来证明产品导航成功；不得由测试脚本或另一客户端直接控制罐头的位移来替代产品执行链。可用 op 夹具客户端布置观察点/目标，但必须与被测动作链分离并如实记录。
- 不得把 SAFE 世界快照、缓存的语义投影、旧的 `minecraft_find_blocks` 命中或 LLM 输出当作“此刻真实世界”的无条件真相。
- 不得把大范围世界采样（如 `extended` 层）当作精确的方块级寻址。精确目标必须来自适当的实时/有边界查询，并且保留真实坐标和 block/entity ID。
- 不得新增平行导航执行器、第二套 Action/Task 状态机、平行授权/确认系统、绕过 Policy / `TaskAuthorization` / `TaskStepAuthorization` 的路径，或给模型直接传坐标即可执行的权限。
- 不得修改任务范围外的采集数量、战斗、建造、配方排序、AI 性格/记忆策略等产品逻辑，除非实测证明它是 R1 根因链中的必要部分并在报告中明确理由与影响范围。
- 不得将本地门禁脚本、服务器日志中的密钥/用户隐私、数据库、世界备份或临时凭证提交到仓库。继续遵守 `scripts/` 0 tracked 的既有仓库纪律。
- 不得因本轮 E2E flake 而删除/放宽断言或超时。保留首轮失败证据，只重跑失败 job，并分别记录原始 run 与最终 attempt。

### 0.3 只允许以下三种最终裁定

- **`R1 PASS`**：R1.1 感知可信度、R1.2 导航可达性与到达判定、R1.3 失败/取消生命周期、R1.4 可观测性和证据链全部满足下文 DoD；必需的真实服务器场景均有可审计证据。
- **`R1 PARTIAL`**：有可重复的产品能力缺陷尚未修复，或主要可达场景的成功率仍不达标。必须给出真实场景、复现率、根因/未明原因、安全状态、影响和已完成的工作；不得靠另发 Follow-up 代替当前裁决。
- **`R1 BLOCKED`**：只有在外部真实服务器、Provider、第二客户端、必要权限或可安全复现场景客观不可用，导致关键门禁无法执行时使用。先完成静态、单元和集成可做项，留下可复放门禁与诊断，再记录阻塞事实及影响。

在范围内发现的新缺陷必须并入同一任务周期；只有明确是 R1 非目标或存在不可越过的客观风险时，才可以登记为 `OUT_OF_SCOPE` / `BLOCKED`。最终报告必须写 `FOLLOW-UP CREATED: NO`，除非用户明确决定重规划项目阶段。

---

## 1. R1 范围与系统边界

R1 包含两个相互关联但不能混为一谈的技术域。

### 1.1 真实世界感知（Perception Integrity）

审计并验证当前已存在的以下数据路径：

`Minecraft Runtime raw snapshot → RuntimeClient → MinecraftService / WorldStateCache / WorldPerception → world_view() / semantic model / read-only tools → planner observation → frozen PlanTarget / PlanStep`

需要核验：

- self 当前坐标、维度、在线状态、健康/饥饿和持有物等动态事实是否来自对应的真实快照；
- `near` / `local` / `extended` / `interesting` 分层缓存是否独立刷新，层龄是否准确，旧层是否会误覆盖新层或伪装成当前快照；
- 缺层、过期层、服务异常、断线、重连时是否明确返回 unavailable/stale/missing，而不是伪造空数组、空背包或“附近不存在目标”；
- 动态实体/玩家/环境字段是否跟随最新成功帧，空间量（世界坐标、相对坐标、距离、bearing、direction、compass）能否从同一时刻的数据和数学定义重算得到；
- `minecraft_find_blocks` 等精确目标查询是否对 block ID、坐标和距离如实返回；不能把 broad snapshot 的柱面采样、兴趣点或聚合的语义摘要冒充精确目标查询结果；
- 计划时的世界事实能否回溯到观察时间、目标来源和计划冻结参数；当世界在观察与执行之间发生变化时，现有 target/expected-state 检查是否阻止旧目标被静默替换或错误地宣称完成。

### 1.2 导航可靠性（Navigation Reliability）

审计并验证现有链路：

`planner observation / frozen coordinate → minecraft_move_to → MinecraftService / TaskCoordinator → ActionRuntime (minecraft_runtime/runtime.js) → pathfinder events → actual-position arrival check → terminal event → TaskRuntime step result/detail → task state / postcondition verification`

重点解决 R0 留下的两类真实疑点：

1. 历史 Phase 8I attempt 6 中，冻结目标约距离 6.8 格时 `minecraft_move_to` 失败，任务 `PAUSED`，后续步骤保持 `PENDING`。
2. R0 真实导航场景 N3/N5 在距目标约 2.83 格、到达半径 1.5 格时出现 `path_search_timeout`；任务安全停止、库存不变，但说明当前导航对可达场景可能不稳定。

上述坐标和距离是**历史复现线索，不是现在必须使用的固定坐标**。开工时先核验世界状态与路径几何；如果旧地形/区块已经变化，构造等价、可重复的安全测试场景并说明其与历史案例的差异。

### 1.3 不属于 R1 的能力开发

本阶段不增加新的 Minecraft action/tool，也不把“导航成功”扩大成采集成功、建造成功、战斗成功或完整自主游戏成功。可以使用既有 SAFE 查询和 LOW `minecraft_move_to` 验证导航，必要时在门禁中串接一个非破坏性观察作为独立后置核验。若使用已有挖掘/采集链作为辅助验收，必须限制在既有能力、既有授权和既有确认门内，不扩大动作风险或预算。

---

## 2. 开工基线、源代码审计与保护区

### 2.1 必须先完成的只读检查

记录以下结果后再动代码：

- `origin/main`、镜像 HEAD、两者关系（ahead/behind）、`git status --porcelain`、最近提交；
- 当前有效运行版本与提交 SHA，避免“测试用旧进程、提交用新代码”；
- 当前服务器/Runtime/Provider/第二客户端可用性、Minecraft 版本、维度、账号模式、测试区块是否加载；
- `mineflayer`、`mineflayer-pathfinder`、`minecraft-data` 的实际锁定版本及 Runtime 配置；
- 当前导航常量与对应配置来源；按 Phase 4H.1 文档所记的契约，默认参考值为 `MOVE_TO_RADIUS = 1.5`、`MOVE_MAX_DISTANCE = 64`、`MOVE_TIMEOUT_MS = 30000`，开工需以当前代码再次确认，不能盲信文档；
- 感知分层周期与 freshness 判定；Phase 2 文档参考默认周期 `near=1s`、`local=4s`、`extended=20s`，并有 `WorldStateCache` 分层时间戳；Phase 8E 曾记录 `world_view` 超过 15 秒为 stale 的边界，开工需核对现在的实现；
- 所有已有导航相关单测、Node `move_to` 测试、Runtime E2E、感知测试和 R0 新增的 `tests/test_r0_navigation_evidence.py`；
- 从 `CatooBot_backups/` 找到 R0 的 `_r0_gate_R2b.log`、`_r0_gate_R2e.log` 及 Phase 8I 历史日志（如果仍可得），校验内容、时间和对应提交；如本机没有这些日志，标记为本地证据缺失，不要伪造或靠报告猜日志细节。

### 2.2 必须审计的实现/文档区域

至少审查下列区域及其真实调用关系；具体实现文件以实际仓库搜索为准，不得仅因文件名猜测实现：

- `minecraft_runtime/runtime.js`、`minecraft_runtime/test/move_to.test.js`、`minecraft_runtime/test/e2e.js`；
- `app/integrations/minecraft/world.py`、`service.py`、`runtime_client.py`、`task_coordinator.py`、相关 bridge/events；
- `app/tasks/runtime.py`、`app/tasks/planner.py`、`app/tasks/agent_planner.py`、相关计划模型/后置验证；
- `tests/test_minecraft_world.py`、`tests/test_minecraft_service.py`、`tests/test_minecraft_actions.py`、`tests/test_minecraft_find_blocks_tool.py`、`tests/test_task_runtime.py`、`tests/test_minecraft_task_integration.py`、`tests/test_r0_navigation_evidence.py`；
- `docs/MINECRAFT_PHASE_2.md`（分层世界感知、缓存和移动感知世界 diff）；
- `docs/MINECRAFT_PHASE_3E.md`（工具与 Runtime 生命周期）；
- `docs/MINECRAFT_PHASE_4H.1.md`（move_to false-success guard 与硬门禁）；
- `docs/MINECRAFT_PHASE_8D.md`、`docs/MINECRAFT_PHASE_8I.md` 和 R0 final report（木材模板、历史失败、R0 当前边界）。

### 2.3 保护区与不变量（除已证明的 R1 必需根因修复外必须保持）

- 不改 `ACTION_RISK` / `TOOL_ACTION` 的风险分类，不改 `allow_medium` 默认值和工具白名单/schema；
- 不削弱 Policy、`TaskAuthorization` / `TaskStepAuthorization`、用户/会话归属、一次性确认、确认到期、冻结计划与 `plan_hash`；
- 不改变 `TaskRuntime` / `ActionRuntime` 的唯一生命周期、终态幂等、STOP/CANCEL 权威、任务失败后抑制后续动作的契约；
- 不放宽 `MOVE_TO_RADIUS`、`MOVE_MAX_DISTANCE`、`MOVE_TIMEOUT_MS` 和木材规划观察范围等门槛来换通过率；如果实测证明其中某个参数不合理，先报告证据与安全含义，只有同一根因分析证明需要、且新增边界测试能证明不会产生假成功/危险路径时，才允许精确修改，并对变化给出单独审查记录；
- 不做无证据的依赖升级。若根因明确落在已锁定的上游版本，必须在本轮证据中说明为何 Runtime 防御无法解决、兼容性/回归结果如何；必要升级可纳入本轮，但不得仅因为“新版可能更好”而顺手升级。
- 不新增数据库 schema / migration，除非缺陷被证明不能在现有结构内安全修复；若确有必要，须证明其必要性、迁移兼容性和回滚路径，不得为 R1 观测日志滥增持久化数据。

---

## 3. R1.1：世界感知事实性与时间一致性

### 3.1 感知真相源矩阵

为每一类读数写清楚：原始来源、更新时间、缓存层、是否允许缺失、消费者，以及不可用时的安全语义。至少覆盖：

| 事实 | 最低核验要求 | 失败时要求 |
|---|---|---|
| self position / dimension / online | 和 Runtime 原始 snapshot / 独立 `/status` 读取相符；记录快照时间 | 缺失/离线不可伪装为原点或有效坐标 |
| near / local / extended | 分层 `fetched_at`、年龄和部分更新合并正确；未刷新的层保留旧值但明确标记其年龄 | 某层缺失不能把其它层整体清空或把旧值标成新值 |
| block target | `block.name/id + x/y/z + observed_at/source` 可关联到精确查询结果；负坐标、整数格与浮点距离口径一致 | 坐标或 block ID 缺失即拒绝作为可执行目标 |
| players/entities | 玩家名/实体 ID（如果已有）/位置/距离与原始帧一致；离开范围/消失/重生不应被误当成同一目标的当前状态 | 旧目标不可无限期保留为在线/附近的事实 |
| relative direction / bearing / compass | 从同一帧的 self position、yaw 与目标位置重算；检查坐标轴方向和边界扇区 | 不可用旧 yaw 配新位置制造混合帧 |
| environment / biome / weather / time phase | Runtime 原文与语义投影相符；分层缺帧不覆盖动态状态 | 失败、未知枚举或缺失不伪装成 `clear/day/overworld` |
| inventory/self held item（被相关任务使用时） | 明确数据源、捕获时间、快照是否独立；和世界变更核验分开 | 数据源缺失是 MISSING/UNAVAILABLE，不等于零库存 |
| disconnected / reconnect | 断开事件立即失效应失效的缓存；重新连接后的首帧重建基线 | 不能沿用上一连接的旧位置/旧区块/旧目标来执行动作 |

### 3.2 必须覆盖的感知自动化测试

在既有测试中补足必要用例（优先在现有测试文件中扩展）：

1. near 单层更新不会清掉 local/extended，且每层年龄独立增长；部分层更新不会让未更新层的 `age_seconds` 归零。
2. 动态字段取最新成功帧，静态/低频层保持自己真实的获取时间；任何聚合 `captured_at` 不得冒充每一层的刷新时间。
3. 断线立刻 invalidates 缓存；重连第一帧只建立新基线，旧连接中的玩家/实体/目标不会复活。
4. 世界移动窗口平移与真正方块新增/移除可以区分，已实现的 `WINDOW_SHIFT` / teleport rebase 机制没有回归。
5. raw snapshot → semantic model 中所有对象都能追溯到 raw 元素或明确聚合规则；不能无中生有、不能把 coarse terrain sample 描述为精确可达的方块目标。
6. 距离/方向数学覆盖负坐标、跨 0、四个主轴、斜方向扇区边界、垂直相邻、实体离开与重新出现等场景。
7. `minecraft_find_blocks` 精确观察结果与同一测试世界的真实 block ID/坐标相符；找不到/超范围/查询异常时是失败或空的**明确含义**，区分“已成功搜索且没有目标”和“无法观察”。
8. stale/old target 的消费：用一项既有计划在确认前/执行前改变世界，确认目标检查应使任务拒绝、暂停或走既有重新观察/重新确认流程；不得把邻近另一个方块默默替换成冻结目标，也不得用旧快照声称目标仍存在。

测试应针对缺陷边界，而非为追求数量制造重复断言。新测试名需能体现合同，例如 `test_partial_layer_refresh_preserves_other_layer_age`、`test_disconnect_invalidates_old_targets`、`test_find_blocks_coordinates_match_runtime_truth`。如果已有等价覆盖，报告应链接到现有测试而不是重复造套件。

### 3.3 真实世界感知门禁（P-Gates）

在真实 Java 1.21.1 服务器上验证；测试脚本只负责布置夹具或收集证据，事实必须来自真实 Runtime / 独立世界读取：

- **P1 原始快照一致性**：至少 3 个安全落点，逐次比较 CatooBot 看到的 self 坐标/维度/在线与 Runtime 原始 snapshot 或独立 `/status` 读取。误差定义必须基于当前坐标模型（整数方块格 vs 浮点实体坐标），不可临时选一个让测试通过的宽松容差。
- **P2 分层刷新与 stale**：确认 near/local/extended 层龄按实际采集时刻递增；至少制造一种“只有某层更新”的情况，另外一层不得伪装成新鲜。真实服务连续运行时，刷新周期允许调度抖动但必须记录测量值与当前契约的偏差。
- **P3 真实世界变更**：使用夹具客户端或安全可还原的方式对一个测试目标块执行可观察的变更，核实 raw snapshot / exact find_blocks 的状态与实际世界一致；若需要用 op 命令，仅允许对夹具目标块，必须记录其前后状态，并在结束时恢复或声明保留原因。不得修改生产世界中非测试目标。
- **P4 断开/重连或 Runtime 不可用**：验证世界状态进入明确 unavailable/invalid 状态、旧 target 不会继续作为新鲜事实；重新连接后建立新的观察基线。
- **P5 真实目标冻结链**：从真实观察找到一个精确方块/可导航目标，关联观察来源和时间、世界坐标、计划目标；随后再次读取世界状态，确认没有把语义摘要/模型提出的坐标冒充真实目标。

P1–P5 的每项都要输出 `PASS/FAIL/BLOCKED/DEVIATION` 与 trace；仅 fake runtime 的单测不能代替真实服务器门禁。

---

## 4. R1.2：真实导航可达性与距离分层可靠性

### 4.1 开始前先还原问题，不要直接调参

1. 从 R0 日志和 Phase 8I §7.5 抽取历史失败的完整证据：任务/step/action ID、冻结目标、动作开始时坐标、最终坐标、`detail.reason`、距离口径、半径、动作时长、pathfinder 事件序列（若运行时已有记录）、goal cleanup、任务终态和后续步骤状态。
2. 检查 `distance_to_target`（与 `GoalNear` 方块格口径一致）和 `raw_distance_to_target`（浮点三维距离）是否被混用；用真实坐标离线复算历史记录。如果历史日志缺少必要字段，只标记为 evidence gap，不反向编造原因。
3. 现场先检查目标方块/站立位置是否真实存在、目标区块是否加载、dimension 是否一致、路径是否受不可破坏障碍/高度/液体/实体影响；不能把几何不可达和 path search timeout 混为一类。
4. 在不动生产参数前，至少复现一次 R0 的 `path_search_timeout` 家族或证明旧地形已改变、不能原样复现；同时重建历史约 6.8 格故障的等价场景。必须真实记录第一次尝试结果，不要在第一次失败后立刻换场景而不留失败证据。
5. 找到根因后先在 Node/单测中以真实 ActionRuntime 事件顺序重现；若运行时事件显示规划超时，不能用 stub `goto=Promise.resolve()` 来替代实际失败机制。

### 4.2 导航试验场景矩阵

使用可重复、安全的地形和目标。可达路径应由罐头自身走完；任何 teleport 都只能用于夹具玩家/目标布置，不计入产品成功。建议在现有能力允许范围内至少覆盖以下矩阵：

| ID | 场景 | 目标 | 必须记录/验证 |
|---|---|---|---|
| N1 | 已处在到达半径内 / 目标附近 | 1 个静态坐标 | 返回成功只因实际位置满足现有 radius；不得因空路径事件直接成功；goal 清理完成 |
| N2 | 平坦、开阔的近距离 | 约 2–4 格，专门覆盖 R0 2.83 格失败邻域 | 至少 3 次独立运行；真实移动/位置变化，实际终点在 ≤既有半径内；记录耗时 |
| N3 | 平坦、开阔的中距离 | 约 6–8 格，关联历史 Phase 8I 失败尺度 | 至少 3 次独立运行；禁止传送罐头；记录起终点与重复成功率 |
| N4 | 常用观察距离内 | 约 8–16 格，对应现有木材目标观察的常用范围 | 至少 3 次独立运行；目标来源于精确真实查询/冻结计划，而不是 LLM 猜坐标 |
| N5 | 可安全绕过的障碍/转角 | 目标可达但不能直线到达 | 证明 pathfinder 执行完整路线，成功必须由真实位置确认；不通过拆除障碍作弊 |
| N6 | 可安全通过的地面高度变化/斜坡/阶梯 | 有高度差但路径确实可达 | 验证垂直差与水平距离口径不误判，落点安全，无误判到达 |
| N7 | 真正不可达或无路径目标 | 选取安全的实体障碍/高处/封闭位置等可证明不可达目标，不改世界物理生成假结果 | 必须结构化失败，原因与实际地形相符；绝不能 SUCCEEDED；不得留下运行中的 Goal |
| N8 | 搜索超时或规划耗尽边界 | 在安全测试区构造会触发当前 timeout/search-limit 的案例（如果环境可稳定构造） | 若不能稳定构造，不强行宣称复现；记录 `BLOCKED/DEVIATION`，用已有真实 R0 timeout 证据和可重复 runtime 测试补足 |
| N9 | 目标在观察后改变/消失 | 目标块/实体于冻结计划之后被夹具改变或移除 | 绝不能静默重选邻近目标、错误完成或继续后续危险动作；失败/重观察/重新确认语义正确 |
| N10 | 移动中 STOP / CANCEL / timeout | 罐头实际正在行走时触发既有控制命令 | 只允许一个确定终态，`goal=null`、`isMoving=false`、动作/任务终态符合合同；延迟事件不能复活 |
| N11 | 迟到、重复或竞态事件 | 在可控 runtime 测试中安排 `goal_reached`、`path_update`、STOP/timeout 的先后顺序 | 终态只落一次；先确立取消/timeout 后的晚到 success 不能翻案 |
| N12 | 断线/维度/区块可用性改变 | Runtime 断线、目标维度不一致或观察失效 | 明确 unavailable/stale/validation/target lost 等正确失败分类，不使用上次位置做假成功 |

**重复性下限：** N2、N3、N4 各至少 3 次真实运行（不同 action_id，不能复用旧终态）；N5、N6 各至少 2 次真实运行；N1、N7、N9、N10 各至少 1 次真实运行；N11 至少覆盖单测中的主要竞态组合；N8 若环境可复现至少 2 次。若其中某项因服务器条件不能合法构造，需列出客观条件及替代证据，不允许静默省略。

如果受测试时间限制，优先保证所有关键类别都有真实证据，不能只挑最容易的正向案例；不要把 `N8` 的不可控超时与 `N7` 的确定不可达混为一类。

### 4.3 成功与失败的语义契约

- **成功**：既有运行时在判定瞬间重新读取实际位置，按既有 `GoalNear` / 方块格距离口径确认 `distance_to_target <= MOVE_TO_RADIUS`；结果包含实际坐标、目标坐标、距离、原始浮点距离（如果实现已提供）和持续时间。pathfinder 事件只是证据之一，不是成功的单独依据。
- **无路径**：真实 `noPath`/空路径不能到达时归入现有稳定错误类型，例如 `path.not_found` 与 `detail.reason=no_path`。若失败来自搜索本身 timeout，应保留 `path_search_timeout` 具体 reason，不伪装成几何上的“绝对不可达”。
- **路径结束但没到**：`path.not_reached` 及相应具体 reason，包含 `empty_path_without_arrival`、`path_stopped_short`、`goal_reached_without_arrival` 等适用类别。
- **搜索/总动作超时**：区分 path search timeout 与 ActionRuntime 的整体动作 timeout。`TIMEOUT` 不是 `no_path`，更不是成功。
- **目标改变**：已有 `goal.changed` / target lost / world changed 等现有语义优先适用；不能把另一个 goal 或邻近方块当成原始目标。
- **STOP/CANCEL**：控制路径保持权威，必须终止现有动作并清理 Goal/listeners；迟到的完成信号不能把已取消/超时的动作重新置为成功。
- **动作成功不等于资源任务成功**：如本次以木材路径做整体验证，还必须由既有后置条件和独立库存快照判断。不得只凭 `minecraft_move_to` 的终态推断后续 dig/pickup 已完成。

### 4.4 当前硬门禁不得被弱化

`docs/MINECRAFT_PHASE_4H.1.md` 明确指出，move_to 的 false-success guard 是基础设施硬门禁；其核心包括：监听器注册在 `setGoal` 之前、实际位置二次验证、空路径不静默成功、失败清 Goal、STOP/timeout 终态幂等。R1 必须逐条保留并扩展必要的反例测试。不要为了让更多导航尝试“绿灯”而将 `distance_to_target` 检查改成更宽松的阈值。

---

## 5. R1.3：目标新鲜度、冻结计划与感知—行动一致性

R1 应重点检查“感知正确但导航/计划使用过期信息”的跨层问题。

### 5.1 目标数据契约

对 `minecraft_move_to` 与所有由观察产出的导航计划，至少确认：

- 坐标来源是可追溯的观察结果或明确的用户指定坐标；LLM 可以协助表达意图，但不能创造“真实观察到”的位置；
- target 具有现有模型能承载的身份/位置/预期状态字段；如果 `target_id`、dimension、observed_at 等字段当前并不存在，不得为迎合报告而伪造。优先复用现有字段和日志；需要新增字段时先证明必要性并评估数据/API 兼容性；
- 计划确认后，坐标和 `expected_block` 等冻结字段不能被后续新观察暗中改写；如目标改变，应走现有失效、重观察、生成新 `plan_hash` 和重新确认的路径；
- 执行动作前，必须按现有工具约定验证坐标有效性、维度/范围/目标状态；不允许把 stale snapshot 当作刚刚观察的事实。

### 5.2 必测的跨阶段场景

1. **观察 → 计划**：目标确实来自真实 `find_blocks` 或真实 world observation，计划内坐标与原始结果一致。
2. **计划 → 确认等待**：确认等待过程中目标被删除/移动或数据失效；确认后不得照搬不再有效的目标执行破坏性动作，也不得悄悄换到邻近同类方块。按现有预条件和重规划合同拒绝/暂停/重新确认。
3. **导航中世界变化**：导航目标变成不可达或对应实体消失；动作明确失败或取消，不能因为缓存仍有目标就声称抵达。
4. **导航成功 → 后置条件**：实际位置确认成功；若该任务还有其它阶段，下一步只能在当前步骤真实成功且既有前置检查满足时派发。
5. **同类近邻目标干扰**：测试区域有两个相同 block ID 或两个近似实体，其中一个被移除/变化；验证没有“用另一个候选替代被冻结目标”的隐式行为。

R1 不要求建立新的目标跟踪系统；应该先验证已有冻结计划、`expected_block`、`block.changed`、`TARGET_LOST/WORLD_CHANGED` 和现有观察机制是否够用。若通过小范围修复即可守住契约，不要为此重构整个 planner。

---

## 6. 根因分类与修复策略

每个失败必须先落入如下分类表之一。允许同时命中多个类别，但必须区分“主根因”与“伴随症状”。

| 类别 | 典型证据 | 调查方向 | 不允许的伪修复 |
|---|---|---|---|
| P-STALE：感知过期 | world_view age/stale 与 Runtime 最新帧不符；目标在真实世界已变化 | layer timestamp、cache merge、disconnect/reconnect invalidation、确认等待时间 | 把 freshness 检查关掉，或把旧数据年龄重置为 0 |
| P-SOURCE：目标来源/坐标错误 | block ID/坐标与真实世界不符，或来自语义聚合而非精确目标查询 | runtime 查询范围、负坐标/取整、维度和采样协议、目标投影 | 让模型猜一个附近坐标；在失败后自动换最近目标 |
| P-MATH：空间数学不一致 | bearing/distance/GoalNear 实际口径不能相互复算 | floored position vs raw vector、y 高度、yaw/坐标轴、负数取整 | 只把断言容差拉大 |
| N-PATH：真实无路径 | 真实 `noPath`，地形/目标确实无法到达 | 地形几何、pathfinder 约束、目标点语义 | 报成功、循环重试无限期、破坏世界开路 |
| N-SEARCH：路径搜索超时 | `path_search_timeout`、真实路径/区块/节点边界 | path update 状态、搜索预算/事件时间、障碍复杂度、加载/实体影响、锁定依赖行为 | 直接当 `no_path` 或通过提高 timeout 无证据掩盖 |
| N-EXEC：路径执行失败 | 有路径但实际位置停在半径外，`path_stopped_short` 等 | 控制状态、速度/物理状态、路径切换、Goal 生命周期、实体碰撞 | 只根据 `goal_reached` 判成功 |
| N-OBS：观察与行动间目标变化 | 规划目标与现场 target 不一致 | 目标冻结、expected-state precondition、重观察/重确认路径 | 换成邻近同类目标继续执行 |
| N-LIFE：动作/任务生命周期 | STOP 后仍走、迟到事件复活、Goal/listener 残留、步骤继续派发 | ActionRuntime token/cleanup、terminal event idempotence、TaskCoordinator 和 TaskRuntime 传递 | 另加一套状态机或依靠测试后手动 stop |
| ENV：外部环境不稳定 | 服务器 tick lag、目标区块未加载、Runtime/客户端断线 | 记录服务器可用状态、重现条件、环境分层 | 将全部失败写成“环境问题”但没有证据 |

对于每个根因：

1. 保存最小失败 trace（动作前状态、target、关键事件序列、实际坐标、耗时、error/code/detail、TaskRuntime step/task 状态）。
2. 在 Node 或 Python 层写能稳定重现该机制的最小测试。测试必须模拟真实故障事件顺序，不得通过立即 resolve 的空 Promise 假设导航总会成功。
3. 修改最少的生产代码；不改变不相关的工具、策略、权限、模型提示词或任务计划上限。
4. 增加正向、反向、竞态与旧行为回归测试，并复跑 R0 导航证据测试。
5. 在真实服务器复验相同几何/相同故障类，证明根因已被移除或被正确分类；如果问题无法修复，诚实降级最终裁定，不得将单测 PASS 代替真实 PASS。

---

## 7. 真实服务器证据等级和门禁纪律

### 7.1 证据分级（最终报告每行必填）

- **E0 静态/源码检查**：可证实调用关系、参数和保护区差异，不能证明运行行为。
- **E1 自动化单测**：Mock/Fake/Node 事件测试；能证明确定性逻辑，不等价于真实 Minecraft 世界行为。
- **E2 集成/E2E**：Runtime 与 CatooBot 服务间的真实协议/测试服务器行为；不能自动等价于 Java 1.21.1 真服。
- **E3 真实服务器 + 真实 CatooBot Runtime**：真实 Java 1.21.1 服务器、当前被测 CatooBot Runtime/MinecraftService、匹配的真实运行代码版本。
- **E4 独立世界核验**：在 E3 基础上，由独立读数（真实 `/status`/位置读取、世界 block/entity 读取、背包快照等）验证动作结果；这是 PASS 场景的优先证据。

第二 Mineflayer 客户端是自动化客户端，不是人类玩家。必须写“真实第二客户端（自动化）”，不要写“真人实测”。bridge injection、fixture 直接发事件、直接调用函数或直接构造事件只能是替代证据，必须标记 `DEVIATION`，不能升级成 E3。

### 7.2 真实成功移动的严格证据

每次 E3/E4 导航尝试至少记录：

- run/test case ID、开始时间、Bot/Runtime/服务器版本、被测提交 SHA、当前 dimension；
- `task_id`（若通过 TaskRuntime）、`plan_hash`、`step_id`、`action_id`（按实际可得字段填写）；
- 起点真实坐标、冻结 target 坐标、target 来源/观察时间、到达半径与距离口径；
- 开始/终态时间、pathfinder 关键状态（若可获得）、error/code/reason、`detail`；
- 结束时独立真实坐标、与目标的距离、`goal == null`、`isMoving == false`，以及任务步骤后继状态；
- 若目标是资源方块，额外提供执行前后 target block 状态与独立库存差值；没有发生库存变化也如实记录，不能把单纯移动标成采集闭环。

日志不留存 API key、密钥、完整 provider 原始提示词或无必要的全量聊天隐私。使用稳定原因码和短的脱敏上下文。

### 7.3 真实服务器准备与还原

- 测试前确认被测 Bot 的运行进程确实载入当前提交代码；必要时通过受控重启确保版本一致。记录旧进程终止与新进程健康状态。
- 优先选安全、可逆的测试区域。可以用 op 夹具客户端建立障碍/目标，但每次更改应登记坐标、原始方块、修改内容和恢复结果。
- `/tp`、`/spreadplayers` 等准备命令如果使用，只允许用于夹具客户端或明确的测试区块布置，不能用于被测 Bot 的成功路径，也不能让 fixture 直接控制 Bot 的位移。
- 若无法原地恢复某测试块或区域，测试前必须确认这是可弃置的测试区域并在报告中写明；不在用户世界中任意破坏方块、容器或建筑。
- 场景之间清理旧的非终态任务、动作、Pathfinder Goal 与 test fixture；但不能以“清场”命令改变刚被测量的结果。
- 若第二客户端、真实 Provider 或服务端不可用，应先完成所有单测、E2E、只读诊断和本地脚本，最终将被阻塞的真实 gate 标为 BLOCKED，而非假 PASS。

---

## 8. 建议的一个工作周期（必须按顺序交付，不逐条停工）

### Step A — Baseline & Evidence Inventory（只读）

- 核对真实 Git 基线、运行代码 SHA、服务状态、依赖锁定版本、导航参数与世界快照 freshness 设置。
- 读取 R0 报告、Phase 2、3E、4H.1、8D、8I 中与当前实现有关的章节；标记文档与代码不符处。
- 收集 R0 原始日志（如果本地存在）并建立 R1 evidence manifest，先禁止修改生产代码。
- 输出初始测试计划，记录被复用的既有测试及尚未覆盖的场景。

### Step B — Reproduce Before Fix（先量化，不猜根因）

- 至少实测 N2/N3/N4 的一次正向移动和历史失败邻域；如某场景失败，不要先调参，要先保存完整 detail 和独立坐标。
- 实测 P1/P2/P3/P4 中可用的真实观测路径，并对照 Runtime 原始快照。
- 把所有发现登记入一个统一的 R1 Defect Matrix：ID、首次版本/提交、场景、复现步骤、根因证据、影响、安全状态、测试文件、修复提交、真实复验结果。

### Step C — Root-Cause Decision（形成可审计判定）

- 对各类失败按 §6 分类；明确哪些是感知、规划、导航执行、运行时生命周期或纯环境因素。
- 对 `path_search_timeout` 必须查看路径搜索实际状态和环境条件，不能因为距离近就认定 radius 错，亦不能因为是 pathfinder timeout 就一概归因于依赖。
- 如果多个症状共用一个根因，归并修复；如果根因不同，仍在本轮统一处理，不拆成多个 Follow-up。

### Step D — Consolidated Fix（批量修复）

- 采用最小必要生产代码变更和明确的合同测试；不新增 parallel executor/state machine。
- 同步补充感知 freshness、坐标数学、终态幂等、cleanup 和 task-step failure detail 的缺口。
- 如根因链证明需要跨 Python 与 Node 修改，按接口两端同时修并增加契约测试。
- 如果没有可证实的代码缺陷，不做无谓重构；继续完成场景测量、失败分类和证据补齐。

### Step E — Local Regression（本地门禁）

至少运行（具体以实际项目工具命令为准）：

- 所有新增/修改用例及既有感知测试；
- 导航 Node tests（至少 `minecraft_runtime/test/move_to.test.js` 对应的所有检查）；
- 相关 Python tests（world/service/actions/task runtime/planner/bridge 和 R0 navigation evidence）；
- `pytest tests -q` 完整套件；
- `ruff check .`、`ruff format --check .`、`mypy app`、前端检查/build、Runtime E2E；
- `git diff --check`、凭据扫描、保护区 diff、tracked/untracked 清单审计。

不得删除/跳过既有测试以改善通过率；测试确需调整时必须证明旧契约已改变且原因明确。记录真实耗时、通过/失败/skipped 数量及全部非成功原因。

### Step F — Real Server Gates（E3/E4）

- 运行 P1–P5 中所有可用真实门禁，以及 N1–N12 的强制场景与重复性下限。
- 所有可达距离类别必须用被测 Bot 自行移动；终点独立读回且半径满足当前硬门禁。
- 无路径、搜索超时、目标变化、STOP/CANCEL 都必须按真实情况终止；不能通过传送、拆障、旁路 Runtime 或直接写入数据库伪造结果。
- 若场景不可达/不可控，应分别给出 E3/E4 的实际失败证据或明确 BLOCKED；禁止把环境无法稳定构造当成已通过。

### Step G — Docs & Contract Reconciliation

- 追加更新 `docs/MINECRAFT_PHASE_2.md`（仅当感知行为/契约发生变化）、`docs/MINECRAFT_PHASE_4H.1.md`（仅当 move_to 行为/事件合同变化）、`docs/MINECRAFT_PHASE_8D.md`/`8I.md`（只增加面向当前状态的 R1 交叉引用，不倒改历史实验记录）、`docs/README.md`（必要时）和 `CHANGELOG.md`。
- 新增一份单一真相源报告：`docs/specs/CatooBot R1 Real-World Perception & Navigation Reliability Report.md`。
- 将本任务书按字节一致的方式归档到：`docs/specs/CatooBot R1 Real-World Perception & Navigation Reliability Taskbook.md`。报告需记录源文件字节数、SHA-256、仓库归档文件字节数/SHA-256 与 `identical=True`。不能将任务书自身的 commit SHA 固化到任务书内容中。
- 不新增重复版本的路线图；若仓库里确有正式 roadmap 文件，按实际路径更新 R1 状态。若没有，最终报告仅提供可复制的状态文本，不随意创建第二份路线图。

### Step H — Final Verdict & Repository Hygiene

- 最终审计 HEAD 与 `origin/main`、dirty、tracked/untracked、`scripts/`、敏感文件和保护区域 diff。
- CI 必须关联最终实现提交；若发生已知 E2E flake，保留首轮失败，仅重跑失败 job，报告明确区分首轮与最终 attempt，并说清楚重跑通过不代表根因修复。
- 交付 `R1 PASS/PARTIAL/BLOCKED`，附缺陷矩阵、真实场景表、测试结果、CI run、文档链接和 R1 → 下一阶段的边界。
- 报告必须明确 `FOLLOW-UP CREATED: NO`，除非用户明确批准另行拆解产品路线。

---

## 9. 最终 DoD（Definition of Done）

### R1.1 感知可信度 — 全部必需

- [ ] `world_view` 的 online/available/stale/missing 语义经过代码审计和单测验证；缺失数据不伪装为真实空结果。
- [ ] 每层 freshness 时间戳和 partial merge 的行为可证明；断线/重连、移动窗口平移与真实世界变更均有回归覆盖。
- [ ] 实际坐标、目标 block ID/位置、相对距离/方向的数学定义经过重算验证；真实 P1–P5 门禁按可用条件执行并记录。
- [ ] Planner 的可执行目标能追溯到真实观察/明确用户坐标；观察失效或冻结目标变化时不会静默换目标。

### R1.2 导航可达性和成功判定 — 全部必需

- [ ] N2/N3/N4 达到指定重复次数，所有控制良好的可达路径成功率记录完整；每次成功均由真实位置再次读取证明在既有到达半径内。
- [ ] N5/N6 的可达绕障与高度变化成功路径通过真实运行验证；若环境不能构造，写明客观阻塞，不伪造 PASS。
- [ ] N7 不可达路径能失败关闭并留下结构化原因；N8 timeout（若可构造）能和 no_path、overall action timeout 区分。
- [ ] 历史约 6.8 格故障被复现并根因修复，或客观证明旧场景不能原样复现并在等价场景中给出可靠测量；不能把它悄悄从矩阵中删掉。
- [ ] 保留 Phase 4H.1 所有 false-success guard、监听器顺序、成功实际位置核验、失败 Goal 清理、原有半径/距离/超时合同（除非有充分根因证据和经批准的精确变化）。

### R1.3 生命周期和失败隔离 — 全部必需

- [ ] N9 目标变化不会转成错误成功或静默改目标。
- [ ] N10/N11 STOP、CANCEL、timeout、迟到/重复事件只产生一个合法终态；`goal=null` / 不在移动 / listener cleanup 验证通过。
- [ ] 失败后后续步骤保持 `PENDING` 或按既有 TaskRuntime 契约终止；没有在途动作、遗留 Goal 或失败后继续派发世界动作。
- [ ] R0 `tests/test_r0_navigation_evidence.py` 及既有 STOP/CANCEL、TaskRuntime、ActionRuntime 测试全部保留并通过。

### R1.4 质量与证据 — 全部必需

- [ ] 本地 `pytest tests -q`、Ruff、format、mypy、Runtime tests/E2E、前端检查/build 全部运行并如实报告。`skipped` 必须逐项说明，不可把 skipped 计作 PASS。
- [ ] 新增/更改的每个核心行为至少有一个自动化反例测试；每类真实世界门禁有 E3/E4 证据或明确 BLOCKED/DEVIATION。
- [ ] 无测试断言/超时/安全阈值被无依据放宽；无 R1 无关的风险策略、权限、Schema 或工具白名单变更。
- [ ] `git diff --check` 通过、凭据扫描无命中、敏感数据不入库、`scripts/` 仍为 0 tracked；保护区域 diff 要么为 0，要么逐行解释确有必要的变更。
- [ ] 最终实现已推送，`HEAD == origin/main`，dirty=0；CI 关联最终代码提交并记录最终 attempt。
- [ ] R1 报告、任务书归档和必要文档已提交；字节/哈希一致性有证据；R1 未通过的能力边界写清楚且不伪装完成。

**总裁决不得由加总分数决定。** 任何核心导航 false-success、旧目标执行、STOP/CANCEL 后复活、断线后使用 stale target、绕过授权或可达场景真实成功证据缺失，都不得判 `R1 PASS`。

---

## 10. 最终交付物清单

1. **R1 最终报告**：`docs/specs/CatooBot R1 Real-World Perception & Navigation Reliability Report.md`。
2. **任务书逐字节归档**：`docs/specs/CatooBot R1 Real-World Perception & Navigation Reliability Taskbook.md`。
3. **统一缺陷矩阵**：放在报告内或以仓库内一份明确引用的文件记录；每项含根因/证据/修复/回归/真实复验/剩余风险。
4. **自动化测试**：感知 freshness/merge/坐标、目标 stale、真实 ActionRuntime 事件顺序、路径失败分类、Goal cleanup、TaskRuntime 后续步骤抑制和迟到事件幂等。
5. **本地专用真机门禁与日志**：留在 `CatooBot_backups/` 或既有本地取证位置，不提交 `scripts/`，不公开凭证。
6. **CI 和卫生证明**：最终实现 SHA、CI run URL/attempt、pytest 与静态检查结果、E2E 非成功项、保护区域 diff、敏感文件扫描、dirty/tracked 状态。
7. **路线图更新文本**：说明 R1 PASS/PARTIAL/BLOCKED，并明确哪些行为能力可以安全进入后续产品阶段、哪些仍未达标；不把 R1 导航通过误称为“完全自主生存/采集/建造已实现”。

### 报告首屏必填模板

```text
CatooBot R1 VERDICT: PASS | PARTIAL | BLOCKED
R1.1 PERCEPTION INTEGRITY: PASS | PARTIAL | BLOCKED
R1.2 NAVIGATION RELIABILITY: PASS | PARTIAL | BLOCKED
R1.3 TARGET FRESHNESS / LIFECYCLE: PASS | PARTIAL | BLOCKED
R1.4 EVIDENCE / CI / HYGIENE: PASS | PARTIAL | BLOCKED
BASELINE SHA: <actual origin/main at start>
IMPLEMENTATION SHA: <actual implementation commit>
FINAL HEAD == origin/main: YES | NO
DIRTY: 0 | <details>
scripts/ TRACKED: 0 | <details>
PYTEST: <passed / failed / skipped / duration>
RUNTIME NODE TESTS / E2E: <result and all deviations>
RUFF / FORMAT / MYPY / WEBUI: <results>
REAL WORLD PERCEPTION GATES: <P1-P5 result per case and evidence grade>
REAL NAVIGATION GATES: <N1-N12 result per case, repetitions, E3/E4 evidence>
FALSE-SUCCESS GUARD: PASS | FAIL
STOP/CANCEL / LATE EVENT TERMINAL INVARIANT: PASS | FAIL
ROOT CAUSES FIXED: <count + IDs>
OPEN BOUNDARIES: <explicit owner / stage / impact>
CI RUNS: <URLs + attempts; retain first-run failures>
TASKBOOK ARCHIVE: <bytes + SHA256 + identical result>
FOLLOW-UP CREATED: NO
```

最终结论必须区分“代码改好”“单测通过”“测试服务器 E2E 通过”“真实 Java 服务器通过”和“独立世界核验通过”这五个不同证据层级。没有 E3/E4 证据的真实场景不能在报告中用一句“真机通过”概括。

---

## 11. 明确不纳入本轮的已知事项

- **配方变体排序**：R0 报告已明确仍未修，归属后续资源采集/合成相关阶段；除非证明它直接导致感知目标或导航目标错误，否则不在 R1 中修。
- **`test_direct_mention_not_blocked_by_sleeping` 时间夹具抖动**：归属测试确定性维护；R1 不得为此放宽断言。
- **开放式自然语言接受/意图识别**：不扩写 R0 交接闭集文法；未知/复合输入仍按安全澄清处理。
- **新增资源采集、建造、战斗、玩家物品交接或程序性技能学习**：属于后续产品阶段，不随导航修复扩大。
- **无证据的全局世界模型重构**：优先复用 Phase 2 的 `WorldStateCache`、层级时间戳、movement-aware diff、现有 Runtime snapshot 与精确查询工具。
- **无证据的依赖全面升级**：只对已证明的导航根因评估最小修复；任何依赖变更必须有锁文件差异、兼容测试和真实服务器复验。

---

## 12. 给执行 Agent 的最终指令

请严格以本任务书执行一个完整的 R1 工作周期。先只读审计，然后在真实服务器上复现与测量，再按证据完成统一根因修复、自动化回归、真实世界门禁和文档收尾。不要在中途因为“又发现一个边界”就停下来生成新的 Follow-up 任务书；范围内缺陷合并在当前缺陷矩阵并在本轮解决。不要为追求 PASS 放宽任何安全合同、把注入证据写成端到端测试，或用 teleport 代替罐头自己移动。若确实遇到客观外部阻塞，应把可做的工作完成，给出具体 BLOCKED 证据与安全影响，最终诚实裁定。

完成标准不是“Bot 大多数时候能走”，而是：**它观察到的世界能被独立验证；它声称抵达时真实位置确实抵达；它走不到时能说明为什么、停止并清理；世界或动作状态变化时不会继续信任过期目标；所有结论均可通过当前提交、自动化测试、真实日志和独立世界读数追溯。**
