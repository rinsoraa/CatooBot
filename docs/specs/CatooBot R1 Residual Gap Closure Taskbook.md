# CatooBot R1 残余缺口一次性收敛任务书

**任务名称：** CatooBot R1 Closure — Residual Navigation & Perception Gaps
**任务目标：** 在已完成 R1-RC1…RC5 的基础上，针对当前 R1 报告中的 PARTIAL 项目进行一次统一复现、根因修复、真机门禁与最终裁定；不把每个未通过项拆成多个 Follow-up。
**仓库：** `rinsoraa/CatooBot`
**R1 原始基线：** `32649eb1856feba56e970d51026f79cf6ad477fc`
**R1 实现提交：** `38ca66d0271550846233c0466843112286583db9`
**R1 收尾报告提交：** `3f400d6adb8f5603be0219ca90b6cfe829b76587`
**参考报告：** `docs/specs/CatooBot R1 Real-World Perception & Navigation Reliability Report.md`
**性质：** R1 残余缺口的单一闭环任务，不是新能力阶段，不是 Follow-up 序列。

> **基线纪律：** 上述 SHA 是编写本任务书时已核验的参考值，不代表 DSH 实际开工时最新的远端 HEAD。开工必须重新读取 `origin/main`、镜像 HEAD、ahead/behind、dirty、CI、当前 Bot/Runtime 版本。若 main 已前进，基于最新可安全继续的主线执行并记录差异；不得 reset、force-push、覆盖用户变更或将旧 SHA 当作当前 SHA。

---

## 0. 最终目标与裁决纪律

本轮不是重新做一遍已经通过的全部 R1，也不是否定已修复的 RC1…RC5。只收敛报告中仍影响 R1 完整出口的缺口：

1. N5 绕障路径 2 次中 1 次达到 30 秒动作超时；
2. N6 高一格/斜坡因测试地形不可构造而 BLOCKED；
3. N8 搜索预算边界无法稳定通过自然地形触发；
4. N9 目标变化门禁只复用 8F/8G/8I 历史证据，本轮未直接复验；
5. N12 断线已通过 P4 覆盖，但维度一致性缺少门禁；
6. `minecraft_find_blocks` 返回缺少可信的 coverage/`captured_at`，空命中可能无法区分“完整已搜区域确实没有目标”与“相关区块未加载/观察覆盖不完整”；
7. P1 第三方独立客户端因视距拿不到实体，需按证据等级处理，不得把受阻的第三方观测夸成 PASS。

### 0.1 禁止的收敛方式

- 不得只为获得 PASS 而增加 `MOVE_TIMEOUT_MS`、扩大 `MOVE_TO_RADIUS`、扩大任务级后置半径、改写 pathfinder `thinkTimeout`、关闭失败分类或降低重试/验证强度。
- 不得用 `/tp` 让被测罐头完成被统计为“导航成功”的路径；`/tp` 只能用于明确标注的夹具客户端或测试区准备，且不得计入产品导航位移。
- 不得把无法构造的物理场景标为真机 PASS；可以用确定性 Node/契约测试验证事件语义，但必须保留 E2 与 E3/E4 的证据等级差别。
- 不得静默把 `find_blocks` 的空数组当作“已确认无目标”，除非本次搜索的覆盖完整性有可验证依据。
- 不得让不同维度的坐标被误认为同一世界坐标；本任务不实现跨维度传送或跨维度导航，只要求明确绑定、检测与安全拒绝。
- 不得新增第二套导航状态机/执行器、授权来源、旁路 runtime、数据库迁移或改变 R0 的 TaskAuthorization/TaskStepAuthorization、双确认门、冻结计划、`plan_hash`、Policy、STOP/CANCEL 权威。
- 不得因为某个门禁仍不稳定就提前停止整个任务并另写 Follow-up。范围内缺口必须进入本任务的统一矩阵，直至修复、客观阻塞或以更合适的证据形式重新界定。

### 0.2 结束裁决

只允许 `R1 PASS`、`R1 PARTIAL`、`R1 BLOCKED`。如果某项确实受硬性外部条件阻塞，完成所有可做的自动化、契约、只读诊断后如实标为 BLOCKED，并说明影响；不得以文字说明代替证据，也不得伪造真实世界 PASS。即使最后仍是 PARTIAL，本任务周期也必须结束并交付一个完整裁定，不再自动产生下一份 Follow-up 任务书。

### 0.3 分项裁定必须与场景表一致（不可出现“分项 PASS、关键门禁未达标”）

当前摘要将 `R1.2 NAVIGATION RELIABILITY` 标为 PASS，但同一份报告又记录 N5 仅 1/2、N6/N8 BLOCKED、N9/N12 PARTIAL。该状态口径必须在本轮统一：按本任务第 10、13 节的硬门槛，N5、N6 等关键门禁未达要求时，`R1.2` 不得继续标为 PASS；应改为 PARTIAL，并在最终摘要、矩阵和结论中一致呈现。类似地，coverage 如果无法证明完整，允许返回 UNKNOWN 作为安全行为，但不得把“无法判定空结果”写成“完整感知 PASS”；应按本任务定义的出口条件裁定 R1.1。总裁定由真实 DoD 决定，而不是保留既有标签。

---

## 1. 当前 R1 报告给出的基线事实

下面这些是本轮必须保留的已知事实，不要回退已通过的修复：

- R1-RC1：`path_update(status='timeout')` 不能单凭事件立即判失败；有可用路径时继续等待/行走，由现有 30 秒动作超时兜底；无可用路径时仍 fail-closed。
- R1-RC2：动作级 `TIMEOUT`/`CANCELLED` 现在应保留导航 detail，包括 target/actual/方块格距离/原始浮点距离/半径/稳定 reason。
- R1-RC3：`move_to` 到达判定与任务级后置条件使用方块格口径一致；原始浮点距离仅作诊断；planner 的方块格投影使用 floor，不能用向零截断。
- R1-RC4：空的 local 层 payload 不得刷新时间戳或清除旧 POI；每层 `captured_at`/age 必须真实反映成功观测时间。
- R1-RC5：轮询状态对账发现 Bot 离开 ONLINE 时必须失效感知缓存，即使 `connected/disconnected` 事件遗漏。
- 固定合同：`MOVE_TO_RADIUS=1.5`、`MOVE_MAX_DISTANCE=64`、`MOVE_TIMEOUT_MS=30000`、pathfinder 锁定版本与 think/tick timeout、任务级后置半径 2.0 均在开工时读实际值并建立基线；未经根因证据与书面说明不得改动。
- 当前 R1 核验记录：pytest 4638 passed/0 failed/0 skipped；`move_to.test.js` 84/84；CI run `39061860694` 与 `39062506524` 的报告记录为双 job success。必须重新确认当前代码是否仍包含这些修复，不可只凭报告假定。
- 当前真机事实：N1 PASS；N2/N3/N4 各 3/3；N5 1/2（一次绕障成功，一次绕行 6.92 格后 30 秒超时并 fail-closed）；N6 BLOCKED；N7 fail-closed PASS；N8 BLOCKED；N9 PARTIAL；N10 STOP/CANCEL PASS；N11 单测 PASS；N12 PARTIAL。

---

## 2. 一次性执行总流程

依次完成以下步骤，不要逐条等待用户批准：

**A. 只读基线与证据盘点 → B. 可重复的导航夹具 → C. N5/N6/N8 根因与验证 → D. N9/N12 生命周期/维度门禁 → E. find_blocks coverage 契约 → F. 合并修复 → G. 分层测试与真实服务器复验 → H. 文档、CI、卫生审计与最终裁定。**

若在 A 阶段发现 `origin/main` 已推进，先 diff 当前实现与 R1-RC1…RC5，确认无回退后再继续。先记录现有证据，再修改生产代码；不允许先调参后补根因。

---

## 3. R1-G1：N5 绕障超时的可观测诊断与可靠性修复

### 3.1 要回答的问题

N5-1 走行约 6.92 格后触发既有 30 秒动作超时。当前只能得出“终态 fail-closed”，不能得出绕障可靠。必须区分：

- pathfinder 持续持有可用路径但机器人没有物理进展；
- path 更新反复失败/路径频繁重算；
- 物理碰撞、狭窄拐角、垂直差或落脚点造成卡住；
- 已接近目标但距离口径/站立方块判断导致不能完成；
- 目标/Goal 被变更或动作终态事件顺序异常。

### 3.2 必须记录的诊断数据

为本地专用测试记录或受限 debug trace 添加最小必要字段，不记录密钥、provider 原始提示词和无必要的完整聊天：

- trial id、`action_id`、起点与目标、dimension、目标方块格、当前方块格、`distance_to_target` 与 `raw_distance_to_target`；
- `path_update` 的事件顺序、status、路径长度（如 API 提供）、是否仍持有有效 path、最近一次实际位移时间；
- 采样时 `goal` 是否仍为当前 Goal、`isMoving`、动作累计 elapsed、最近 1 秒/3 秒实际位置增量；
- 首次 timeout/partial/noPath 时间、最终事件与 `detail.reason`；
- 动作终态后 `goal == null`、`isMoving == false`、监听器 cleanup/没有在途动作的结果。

所有新增 telemetry 必须有界、脱敏，默认不变更常规聊天日志。若 runtime API 不暴露某项字段，记录 `UNAVAILABLE`，不猜测填充。

### 3.3 可重复的绕障试验

在罐头当前所在的安全测试区域周围布置可逆夹具，建立一个明确可绕行的单障碍或墙角路线，并验证障碍物旁存在连续可通行路径。夹具必须记录原状、变更坐标和恢复结果。不要用传送改变被测罐头的测试位移。

- 先原样重放失败路线至少 3 次以复核失败形态；若环境变更导致无法复刻历史起点，应写出差异，并使用等价可控路线。
- 在修复后对同一路线至少连续 5 次测试，起点/目标/障碍几何、服务器版本和运行参数保持一致。每次成功都必须由独立 `world_view()` 重新读取真实终点证明在原有半径内；仅有 `goal_reached` 不算成功。
- 5 次中任何一次超时都必须留存该次路径/进展 trace，不允许删除 outlier。若确认根因并可在本轮最小修复，修复后重新跑完整 5 次；若无法达到 5/5，则 R1 总裁决必须保留 PARTIAL，并清楚标注失败概率/条件和安全行为，不可将“失败关闭”当成“可靠性通过”。
- 修复方向以证据为准：优先修正路线/落脚点判定、事件处理或路径进展监测等明确根因；如果考虑 bounded recovery/replan，必须复用当前 ActionRuntime 与当前 action lifecycle，明确动作预算、终态唯一性、STOP 优先级和取消清理，不得引入平行状态机。重试不准悄悄刷新授权或改变冻结目标。

### 3.4 验收

N5 应有：修复前基线数据、根因证明、最小修复、反例单测、5 次受控真机重复数据、每次独立终点、超时与 cleanup 证据。阈值不动。若只有某种危险/不安全几何能触发超时，应按真实障碍几何和路径规划能力边界说明，不要把不合理的路径要求强行算作可达。

---

## 4. R1-G2：N6 高一格/斜坡导航门禁——构造安全夹具，不再接受“地形不可用”作为无期限阻塞

### 4.1 建立可恢复测试地形

测试前选择安全、可弃置的测试区域，并由具备权限的夹具客户端/管理员在罐头附近构造：

- **用例 A：单块上升**——可从下层安全站立面走到高一格的平台，平台有至少一个完整方块的稳定落脚面；
- **用例 B：连续楼梯/缓坡**——至少连续 2–3 个上升台阶，台阶/平台宽度足够，出口无遮挡；
- **用例 C：高度差但不可安全落脚的对照**——作为负向/澄清/失败案例，不要求罐头进行危险跳跃或掉落。

布置前快照原始方块，测试后按快照逐点还原。若环境权限无法建造可恢复地形，先用真实服务器里的既有楼梯区域；只有确实两种方式都不可用才允许 BLOCKED，并附权限、坐标、地形与尝试日志。

### 4.2 门禁

- A 至少 3/3，B 至少 3/3；被测罐头自行移动，全程记录真实起点、目标和轨迹，独立读取最终位置。
- 任务目标应设置在可安全站立的平台/楼梯末端，不以悬空坐标作为正向导航成功要求。
- 必须验证 floor/负坐标口径、方块格距离与 `goal_near` 一致；不能把因目标设在不可站立的点导致的拒绝误报为路径规划 bug。
- 对照用例 C 应安全失败或在调用导航前被既有验证拒绝；不得误报 SUCCEEDED，且应清除 Goal/listener。

若单块上升或楼梯路线无法成功，记录 pathfinder 每次 status、方块碰撞/落脚点证据和实际坐标；修复应针对已证明的根因，不允许扩大 radius/timeout 来替代落脚点问题。

---

## 5. R1-G3：N8 搜索预算边界——用确定性运行时契约补足不稳定的自然地形触发

N8 的目的在于验证 pathfinder `thinkTimeout`/`path_update(timeout)` 的语义，不是强求某个 Minecraft 地形每次都恰好产生 timeout。把它改成两层证据，不伪装成单一“真机通过”。

### 5.1 确定性 Node/运行时契约门禁（强制）

1. `path_update(status='timeout')` 且仍持有非空可用路径：不得立刻失败；保持当前动作，后续只有真实抵达才成功，或由既有 `MOVE_TIMEOUT_MS=30000` 终止。
2. `path_update(status='timeout')` 且路径为空/无可用路径：以结构化 `path_search_timeout` fail-closed，不可 SUCCEEDED。
3. 仍有路径时后续 `goal_reached` 必须做实际位置二次校验；超时/取消先落终态时，任何迟到 `goal_reached` 都不得翻案。
4. `TIMEOUT` / `CANCELLED` 均保留 target/actual/距离/半径/reason detail，且只产生一个终态、清理 Goal/listener。
5. 测试既要覆盖独立事件注入的确定性边界，也应尝试用锁定版本的真实 `mineflayer-pathfinder` 重放历史 `partial → timeout` 探针。若真实依赖的自然 timeout 依然不稳定，报告将“事件合同 E2 PASS、自然触发 E3 DEVIATION/非稳定”分开记录。

### 5.2 与 N7 真实不可达门禁的关系

N7 继续作为真实 Java 服务器上的不可达 fail-closed 门禁：不成功、可诊断、终态后无 Goal/listener/在途动作。N8 不需要为证明事件合同而故意把真实游戏世界卡到超时；不应调整路径搜索预算以方便触发。

---

## 6. R1-G4：N9 目标变化生命周期——本轮真实重跑，而非只引用历史阶段

必须至少做以下两个子场景，并把它们放入当前 R1 的统一真机日志与报告：

### N9-A：确认前目标失效

1. 用真实 `minecraft_find_blocks`/SAFE 观察找到明确目标，记录观察时间、dimension、精确方块 ID 和坐标。
2. 创建冻结计划，记录 `task_id`、`plan_hash`、目标、`expected_block`/后置条件和确认状态。
3. 在确认前由夹具客户端移除或替换该精确目标方块；不碰罐头的工具执行链。
4. 再确认/恢复计划时，当前实现必须依既有 lifecycle 重新验证冻结目标：目标已变化/丢失就拒绝、暂停或按既有 `WORLD_CHANGED`/`TARGET_LOST` 契约处理；不允许静默改选邻近方块，不允许目标物品库存变化。

### N9-B：动作计划冻结后目标变化

1. 对可安全测试的目标建立已确认任务，先保证任务仍遵循既有两阶段确认和 Policy。
2. 在目标方块尚未被实际挖掘之前让夹具客户端改变目标，观察 TaskRuntime 的中止/重规划处理。
3. 证明旧 `plan_hash` 不被偷换、目标不被自动替代、旧任务不继续派发依赖旧目标的后续世界动作、无库存假增量；报告终态、每步状态与真实世界快照。

如果任务执行时序不允许稳定插入，设计受控 event barrier/test fixture 在动作派发前后卡点；该 barrier 只能用于确定性测试，不得绕过生产的观察/授权/Policy。至少一个子场景必须由真实服务器执行，另一项可由精确的 Runtime/TaskRuntime 集成测试补齐，并标明证据等级。

---

## 7. R1-G5：N12 维度一致性——不做跨维度移动，只要保证绝不误用坐标

当前坐标工具输入缺少 dimension 字段。本任务不新增跨维度传送/导航能力，也不允许仅凭相同 `x/y/z` 跨维度执行。采用**当前维度绑定 + 执行前一致性守卫**：

1. 规划用到世界坐标时，必须在同一观察上下文中记录可信 `dimension`（例如 `minecraft:overworld`、`minecraft:the_nether`），并将它作为计划观察证据的一部分冻结/纳入计划指纹所覆盖的字段；实际是否纳入 `plan_hash` 必须先审计现有 hash 契约，避免不经评估破坏兼容性。
2. 对 `minecraft_find_blocks` 目标，坐标只在此次真实观察对应的 dimension 中有效。不要通过浮点坐标、玩家当前位置或语言猜测来补写 dimension。
3. 在计划确认前、移动动作派发前或关键后置验证点，只读重查当前 dimension；若与冻结目标 dimension 不一致、缺失或来源不可信，则 fail-closed，回报稳定原因码（优先复用既有 reason-code 模式），不派发移动/挖掘等世界动作。
4. 对用户直接给出 `x/y/z` 但没有维度信息的请求，明确按当前维度解释并绑定当前维度；如果无法确认当前维度则澄清/拒绝。绝不默认把 Overworld 坐标当成 Nether 坐标，或反过来。
5. 增加单元及集成矩阵：同维度正常、规划后 dimension 变化、dimension 缺失、观察 dimension 与当前 dimension 不一致、旧观察在断线/重连后被尝试重用。后 4 类必须零导航动作/零世界改动。
6. 如发现需要增加动作输入字段才能安全拒绝，先论证是否可以由外围冻结观察上下文实现；只有确实不能满足时才允许做**向后兼容、默认安全拒绝**的 schema 扩展，并同步 Tool Registry、Agent description、运行时校验、API 契约和测试。禁止新增跨维度执行方式。

### N12 验收

断线/恢复继续复用 P4；维度变更/缺失至少要有一个真 Runtime/Service 集成验证和完整单测。若 Java 服务器无法安全切换维度，可以由受控 test fixture 模拟 status/observation dimension 的变化，但必须标为 E2，不可谎称真实跨维度真机门禁。

---

## 8. R1-G6：`minecraft_find_blocks` 的 coverage / freshness 合同

当前报告确认 `find_blocks` 缺少 `coverage`/`captured_at`，因此空 `matches` 不一定表示整个请求范围已被完整搜索。此问题直接关系“观察无目标”与“建不了任务”的诚实性，不能无限延期。

### 8.1 先只读审计实际 Runtime 能确认什么

审计 `minecraft_find_blocks` 的端到端路径：Tool schema/Registry → Python tool adapter/Service → `minecraft_runtime/runtime.js` 实际搜索实现 → Mineflayer API → World/Chunk 可用性。以真实可验证的数据为准，不推测 API 能保证的覆盖范围。

至少区分：

- `COMPLETE`：能证明所请求范围中所有应搜索区域都已加载且实际搜索已完成；
- `PARTIAL`：只能确认范围的一部分可搜索；
- `UNKNOWN`：Runtime 无法可靠确认覆盖范围或区块可用性；
- 调用本身失败/离线/过期应使用现有错误/available 语义，不得伪装为 `matches=[]`。

### 8.2 最小兼容性扩展

在现有查询响应上添加可选的只读元数据，优先采用以下语义（字段名可在审计后与项目命名规范对齐，但不得改变既有字段含义）：

```json
{
  "matches": [],
  "observation": {
    "captured_at": 0.0,
    "dimension": "minecraft:overworld",
    "center": {"x": 0, "y": 64, "z": 0},
    "requested_radius": 8,
    "coverage": "COMPLETE|PARTIAL|UNKNOWN",
    "coverage_reason": "stable_reason_code"
  }
}
```

- 上面是字段语义示意，不是可直接复制的假数据；`captured_at` 必须来自实际搜索帧；实际坐标、dimension 和查询半径必须来自 Runtime 实际观察/输入。
- 如果 Runtime 无法精确证明 `COMPLETE`，只能返回 `UNKNOWN` 或 `PARTIAL`。不得用“findBlocks 正常返回”推断全半径的 chunk 都已加载。
- `matches=[] + coverage=COMPLETE` 才允许被规划器当成“在该范围内没有命中的目标”；`matches=[] + PARTIAL/UNKNOWN` 必须返回不确定/观察不完整原因，不可对用户宣称“附近肯定没有”。
- `matches` 非空也不意味着目标执行时仍存在；执行前仍走现有冻结目标核验/后置条件。coverage 不授予权限，不代替确认，不直接生成可执行动作。
- 优先只新增响应字段和观察证据，不新增 DB schema/迁移；如果计划持久化结构需要新字段，先评估是否能放入现有 observations JSON；禁止为本任务做无必要迁移。
- 更新 runtime response schema、Python adapter、Tool Registry/契约定义、文档与测试。兼容旧消费者：新增字段是可选的，旧字段和旧稳定错误码不破坏。

### 8.3 必须测试

1. 完整加载范围且目标不存在 → `COMPLETE + matches=[]`；
2. 部分区块缺失/无法查证覆盖 → `PARTIAL/UNKNOWN + matches=[]`，规划器不得当作确认无目标；
3. 观察过期/断线 → 不复用旧 `captured_at` 或旧坐标；
4. 非空命中 → 命中坐标可追溯至同一时刻的查询观察与维度；
5. local/near 层空载荷/部分刷新不更新其 `captured_at`，不清空其他层 POI（RC4 回归）；
6. 离开 ONLINE 即使未收到 disconnect event 仍使 world perception unavailable，重连建立新基线（RC5 回归）；
7. 查询半径/最大命中数/输入校验与既有行为不变。

如果现有 Runtime/协议实际上根本无法判断某一区域是否已加载，正确做法是返回 `UNKNOWN` 并让调用方保守澄清，而不是虚构 coverage。此时报告必须明确安全语义与用户体验影响，不能将 `UNKNOWN` 冒充 `COMPLETE`。

---

## 9. R1-G7：P1 第三方观察端的视距偏差与证据等级

当前 P1 中，产品感知坐标与 Runtime `/status` 在 3 个落点最大差 0.000；第三方客户端实体因视距/距离拿不到，结果 `target_not_visible`。这两件事必须分别记录：

- **产品感知 vs Runtime `/status`：E3 对照通过。**
- **独立客户端实体观测：当前 BLOCKED，不是 PASS。**

本轮优先把自动化观察客户端通过自身移动或在测试准备阶段由 op helper 调整到合法视距内，再重试；不得移动被测罐头来迁就观察端，不得用客户端视角缺数据来断言产品快照错误。

若服务器 view-distance/entity-tracking 机制依然使第三方客户端不可观测，则保留 `DEVIATION/BLOCKED`，继续用独立 `/status` 与 `world_view()` 对照完成可用的 E4 核验，并说明 E4 的独立性限制。本项不允许以更换日志中的字段来源伪造“第三源通过”。

---

## 10. 必须通过的统一门禁矩阵

| ID | 内容 | 最低证据要求 | R1 关闭要求 |
|---|---|---|---|
| P1 | 世界坐标与 Runtime 状态一致；第三方观察限制如实标注 | E3，尽量 E4 | 产品/Runtime 对照通过；第三源受阻不得隐瞒 |
| P2 | 分层 freshness、各层 `captured_at`、空载荷不刷新 | 单测 + E3 | PASS |
| P3 | 真实世界增删改、精确 `find_blocks` 命中 | E3/E4 | PASS，且查询 coverage 语义正确 |
| P4 | `/kick` 断线、available 失效、重连重建基线 | E3/E4 | PASS |
| P5 | 真实观察 → frozen planner target 坐标/维度可追溯 | E3/E4 | PASS |
| N1–N4 | 已有平地距离层，N2/N3/N4 各 3 次重复 | E3/E4 | 不回退，所有正向终点独立核验 |
| N5 | 受控绕障路线 | 至少 5 次同条件重复 + trace | 5/5 成功才可记可靠性 PASS；任一次失败必须保留并重新查明 |
| N6-A/B | 单块上升、楼梯/缓坡 | 各 3 次真实移动 | 3/3；无法构造须有客观环境证据与 E2 替代测试，不能冒充 E3 |
| N6-C | 不安全高度/无落脚点对照 | E2 + 可用时 E3 | 安全拒绝/失败关闭，无 false-success |
| N7 | 真实不可达 | E3 + E4 位置/状态 | PASS fail-closed，清理 Goal/listener |
| N8 | timeout + path 有效/为空两条分支 | E2 确定性 Node/Runtime + 可用的真实依赖探针 | 事件合同 PASS；自然触发不稳定须列 DEVIATION，而不是伪装真机 PASS |
| N9-A/B | 冻结目标在确认前/执行前变化 | 至少 1 项 E3，另一项可 E2 集成测试 | 旧目标不继续执行，不静默换目标，计划/库存/步骤可核验 |
| N10 | 移动中 STOP/CANCEL | E3/E4 | 唯一 CANCELLED 终态，无在途动作，延迟后不复活 |
| N11 | 迟到/重复/竞态事件 | Node + Python 单测 | PASS，旧终态不能翻案 |
| N12 | 断线与维度变化/缺失/不一致 | P4 的 E3/E4 + dimension 单测/集成 | 维度未知或不一致时零移动、零世界改动 |
| F1 | `find_blocks` coverage 与 capture time | E2 contract + E3 可用场景 | 空结果只有 coverage 完整时才能解释为目标不存在 |

**证据等级约定：** E2 = 确定性单元/运行时集成；E3 = 真实 Java 服务器 + 当前 CatooBot Runtime/MinecraftService；E4 = E3 基础上由动作链以外的独立重读核实实际世界状态。不得把 E2 称为真机，不得把动作返回值本身称为 E4。

---

## 11. 实现边界与保护区

本轮允许在根因与兼容性论证成立后修改：

- `minecraft_runtime/runtime.js` / `action_runtime.js`：路径事件/终态 detail、导航诊断、world snapshot/query response 的只读 observation metadata；
- `app/integrations/minecraft/world.py` / `service.py` / runtime client 和对应协议 schema：分层 captured time、coverage、dimension 绑定、离线失效；
- `app/tasks/planner.py` / `agent_planner.py` / `runtime.py`：冻结观察/维度前置条件、目标失效后的既有状态语义、计划后置一致性；
- 对应的 Node/Python 契约、服务、planner、TaskRuntime 测试；必要文档与 CHANGELOG。

非本轮默认改动：

- `ACTION_RISK` / `TOOL_ACTION`、`allow_medium`、Policy 行为、双确认门、授权来源；
- 既有 MOVE_TO/任务级距离与超时常量、pathfinder 依赖版本与 think/tick timeout；
- 数据库 schema/迁移、第二套执行器/状态机、跨维度传送能力、资源采集/合成/建造新能力；
- 用户聊天/Provider 的无关路由、配方变体排序和 `test_direct_mention_not_blocked_by_sleeping` 时段抖动。

如确有必要改动一个保护项，必须在统一缺陷矩阵先给出原始契约、可重放根因、替代方案比较和精确风险，然后实施；不得以“为了测试通过”作为理由。

---

## 12. 测试执行顺序：避免无效重复消耗

1. **先跑受影响的细粒度 tests**：`minecraft_runtime/test/move_to.test.js`、感知 world/service tests、planner/runtime/postcondition tests、find_blocks/tool schema/adapter tests、相关目标丢失与断线测试。
2. **复现测试 N5 前先启动最小诊断**，不要立刻重跑 617 秒全量套件。N5 基线至少 3 次；修复后按 §3.3 跑 5 次。
3. 完成所有代码与场景夹具后，再运行一次完整 `pytest tests -q`、Runtime Node tests/E2E、Ruff check、format check、mypy、WebUI typecheck/tests/build（如相关脚本在项目 CI 中）。
4. 保留所有失败结果；若 CI 确认是既有 E2E flake，只重跑失败 job，报告首轮与重跑 attempt，不能声称根因已修。
5. 逐条检查 `git diff --check`、敏感信息扫描、protected-area diff、tracked/untracked 状态；本地脚本/探针/日志不入库，`scripts/` 继续 0 tracked。
6. 使用可见窗口重启 Bot 时，必须确认加载的是最终实现提交并记录 PID/端口/commit；结束时 25580/8500/8080/25565 按实际部署架构核验，不以旧进程仍在监听冒充新代码生效。

---

## 13. R1 最终 DoD（统一出口）

### R1.1 感知完整性

- `world_view`、每层 age/`captured_at`、空载荷、断线/重连行为均有契约测试和可用的真机证据。
- `find_blocks` 返回结果带可验证的 `captured_at`、dimension 与 coverage 状态；未知 coverage 的空结果不得用于确认“无目标”。
- 目标、坐标、dimension 可以追溯到真实观察；维度变化或 stale observation 会安全拒绝。

### R1.2 导航可靠性

- N1–N4 不回退；N5 在固定受控绕障路线至少 5/5 成功；N6 高一格与楼梯/缓坡正向门禁各 3/3；每次成功均由独立位置读回验证。
- N7 fail-closed；N8 timeout 事件语义按 §5 全覆盖；N5 任一失败都不能因最终 fail-closed 而被算作可靠性 PASS。
- 原有 false-success guard、`goal == null`、`isMoving == false`、listener cleanup、唯一终态和取消优先级全部不回退。

### R1.3 目标 freshness 与生命周期

- N9 至少两个目标变化窗口中的本轮门禁全部有结果；旧目标不会在目标消失/改变后被继续执行，也不会自动替换成邻近目标。
- N12 维度缺失/变化/不一致时零移动、零破坏；断线后的 old target 不被复用。
- STOP/CANCEL、TIMEOUT、迟到事件时序、后续步骤抑制全部通过。

### R1.4 证据、CI 与仓库卫生

- 所有被测门禁逐项记录 E2/E3/E4、输入条件、坐标、动作/任务 ID、终态、detail、独立世界状态、失败与恢复结果。
- pytest、Node runtime、静态类型/格式、CI 和安全扫描结果真实、完整；不能删除/跳过断言换取通过。
- 当前最终实现推送后 `HEAD == origin/main`、dirty=0、`scripts/` 0 tracked；DB/密钥/本地探针未入库；保护区无未解释 diff。
- 报告明确列出未解决项的 owner/stage/风险，保持 history append-only，不倒改既有 Phase 8D/8I/R0 事实。

**只有 R1.1、R1.2、R1.3、R1.4 均符合硬性 DoD 才能总判 PASS。** 如果 N5 未达重复率要求或 coverage 仍不能安全区分，必须 PARTIAL；如果核心环境根本不可用，才可 BLOCKED。不得以“所有失败都是 fail-closed”抵消导航可靠性不足。

---

## 14. 文档与交付

- 更新 R1 现有报告 `docs/specs/CatooBot R1 Real-World Perception & Navigation Reliability Report.md`，保留当前 RC1…RC5 和原始 PARTIAL 证据，新增本任务闭环章节，不抹除历史失败。
- 按项目规范更新受影响的 `docs/MINECRAFT_PHASE_2.md`（perception/coverage）、`docs/MINECRAFT_PHASE_4H.1.md`（只在 move_to 合同改变时）、`docs/MINECRAFT_PHASE_8D.md`/`8I.md`（追加当前目标 freshness/导航边界交叉引用）、`docs/README.md` 和 `CHANGELOG.md`（仅当实际需要）。不新增第二份路线图。
- 将本任务书以逐字节归档方式保存到：`docs/specs/CatooBot R1 Residual Gap Closure Taskbook.md`。报告写明源文件 bytes/SHA-256、归档 bytes/SHA-256、`identical=True`。
- 本地 smoke/probe/scripts 留在 `CatooBot_backups/` 或规定的本地目录，不提交、不上传秘密。归档报告可引用文件名/哈希，但不要公开聊天全文或 provider 原始响应。
- 最终给出一个总裁定及 P/N/F 全矩阵，不再以新建 Follow-up 代替本轮最终结论。

### 最终报告首屏必填

```text
CatooBot R1 CLOSURE VERDICT: PASS | PARTIAL | BLOCKED
R1.1 PERCEPTION / COVERAGE: PASS | PARTIAL | BLOCKED
R1.2 NAVIGATION RELIABILITY: PASS | PARTIAL | BLOCKED
R1.3 TARGET / DIMENSION LIFECYCLE: PASS | PARTIAL | BLOCKED
R1.4 EVIDENCE / CI / HYGIENE: PASS | PARTIAL | BLOCKED
BASELINE SHA: <actual origin/main at start>
IMPLEMENTATION SHA: <current code commit>
FINAL HEAD == origin/main: YES | NO
DIRTY: 0 | <details>
scripts/ TRACKED: 0 | <details>
PYTEST: <passed / failed / skipped / duration>
RUNTIME NODE TESTS / E2E: <results and first-run failures>
N5 CONTROLLED DETOUR: <attempts / successes / failures / cause>
N6 STEP / STAIRS: <attempts / results / E3 or deviation>
N8 TIMEOUT CONTRACT: <E2 result + any real-probe deviation>
N9 TARGET MUTATION: <subcase results / evidence levels>
N12 DIMENSION GUARD: <matrix result>
FINDBLOCKS COVERAGE CONTRACT: <COMPLETE/PARTIAL/UNKNOWN semantics verified>
P1 THIRD-PARTY OBSERVER: <PASS or explicit BLOCKED/DEVIATION>
FALSE-SUCCESS GUARD: PASS | FAIL
STOP/CANCEL/LATE EVENT INVARIANT: PASS | FAIL
ROOT CAUSES FIXED: <IDs + proof>
OPEN BOUNDARIES: <owner/stage/impact; no ambiguous “later”>
CI RUNS: <URL + attempts; retain failures>
TASKBOOK ARCHIVE: <bytes + SHA256 + identical result>
FOLLOW-UP CREATED: NO
```

---

## 15. 给 DSH 的最终执行指令

请把本任务作为 **R1 残余缺口的一次性闭环工作周期**执行。第一步只读审计当前远端与工作树，不得直接调整导航常量。按本任务一次性完成 N5 绕障诊断与重复验证、N6 安全地形夹具、N8 运行时超时契约、N9 目标变化真实复验、N12 维度绑定/拒绝和 `find_blocks` coverage/freshness 合同。先保留证据，再按统一根因矩阵修改；所有相关回归与真实门禁结束后才跑一轮最终全量测试。

对确实无法构造的环境情形，必须给出客观阻塞证据和合格的替代证据等级；不能把替代测试改写成真机通过，也不能让环境阻塞无限延期本轮交付。最终交付报告需明确 R1 是否真正达到 PASS，不得以分项 PASS 或安全失败关闭替代导航可靠性证明。**不要创建 Follow-up 7/8/9；此任务结束时必须给出完整且诚实的最终裁定。**
