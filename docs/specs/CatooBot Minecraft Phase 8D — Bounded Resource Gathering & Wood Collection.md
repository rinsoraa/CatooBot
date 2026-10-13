# CatooBot Minecraft Phase 8D — Bounded Resource Gathering & Wood Collection

> **交付类型：** DSH 可直接执行的阶段任务书  
> **阶段目标：** 在真实 Minecraft 聊天入口实现小规模、有界、可审计的木材采集；复用既有观察工具、冻结计划、确认、步骤级授权、TaskRuntime 与 Action Runtime。  
> **验收原则：** 真实目标、真实世界、真实执行回执；不把“计划已创建”说成“物品已采集”。

---

## §1. 基线与开工纪律

### 1.1 基线

| 项 | 值 |
|---|---|
| 起始基线 | `a1b2079d4e1189e25b9e6b8692477b5da0d56917` |
| 基线说明 | Phase 8C Follow-up：planning failure must not fall through to tool execution |
| 预计开工时公开 `main` commits 数 | 280（开工时必须重新核实，不可照抄） |
| 权威开发目录 | `E:\WorkSpace ZCode\CatooBot` |
| 干净发布镜像 | `E:\WorkSpace ZCode\CatooBot_github` |
| 公开仓库 | `https://github.com/rinsoraa/CatooBot` |

**第一步必须重新读取当前 HEAD、`origin/main`、源工作区状态及镜像状态。** 如果 `main` 已领先此基线，先核实新增提交并以实际最新、经过审查的 HEAD 为基线；不能 reset、硬重置、覆盖工作区或把较新提交当成不存在。报告必须同时记录任务书基线与实际开工基线。

### 1.2 工作区与发布约定

1. 在源工作区实现与测试，按既有流程将**获准公开的文件**同步到干净镜像再提交。
2. 现有约定中 `scripts/` 是本地专用、通常不跟踪。真机门禁脚本及原始证据留在源工作区；不得为方便 CI 或归档而把本地运行脚本、真实配置、运行数据库、认证文件、日志或角色 Bible 私有源意外推上 GitHub。
3. 不覆盖或清理用户已有数据、测试世界、运行进程或未提交改动。任何必要的测试状态清理必须只针对本阶段创建的、明确标记的测试对象。
4. 不允许把 Phase 8C/Follow-up 的历史报告改写成符合本阶段结果的样子。本阶段使用独立文档、独立测试和独立提交。
5. 不得把真实 QQ 号、完整 UUID、令牌、密钥、私有配置、聊天原文、Prompt 或推理内容写入提交、日志或发布报告。

---

## §2. 产品目标与范围

### 2.1 产品目标

让罐头在 Minecraft 游戏内接受一个明确的、有限的木材采集请求，并像一个谨慎的玩家一样执行：先观察，再说明计划，等真实用户确认后，按冻结的有限步骤接近指定原木、挖掘、识别并拾取相应掉落物，最后用真实世界/背包观察确认采集结果。

第一版目标请求可包含如下自然语言意图：

- “帮我砍一棵树，把木头带回来。”
- “帮我收集两根附近的橡木原木，然后捡起来。”
- 对明确树种的有限采集请求，例如“帮我采两根云杉原木并捡回来。”

上面是语义示例，不代表现有检测器已经识别全部句式；DSH 必须以源码审计和回归测试明确支持的最终语法为准，并把支持与不支持的表达记录在文档中。

### 2.2 有界 MVP 的硬上限

本阶段按现有任务预算实施一个**小批量采集 MVP**：

- 每个任务最多规划 **2 个原木方块**；实际数量还必须受当前 `MAX_STEPS`、`MAX_ACTION_STEPS`、步骤风险和现有运行时校验约束，若实际预算只能容纳更少目标，以更小值为准。
- 2 个目标必须来自同一组可验证、相邻的树干原木，或来自请求中已明确且经观察确认的资源目标；不得把搜索范围内互不相干的木结构拼成“一棵树”。若当前感知不能证明它们属于同一组树干，则必须澄清或拒绝，不得假称识别了整棵树。
- “砍一棵树”在本阶段只表示：从一组满足验证条件、可达且有界的树干目标中采集至多 2 个原木；**不承诺砍倒完整树冠或清空所有树干**。确认摘要必须显示具体树种（若可知）、目标数量、目标位置/范围摘要和将执行的步骤。
- 用户明确要求超过本阶段上限时，不得静默截断并声称完整完成。应明确告知当前一次任务最多采集的数量，请用户确认这份有限计划，或要求拆成新任务；不得自动接续创建下一批任务。
- 采集的完成定义是目标物品被正确拾取，并经背包实际增量复核。**本阶段“带回来”指收进罐头当前 Minecraft 背包，不代表交给玩家、放入箱子或运送到指定位置。** 交付/搬运属于 Phase 8F。

### 2.3 阶段边界

Phase 8D 只负责：

1. 让已支持的木材请求通过单一、受控的任务规划入口；
2. 对资源目标做只读观察、确定性目标选择和有界计划生成；
3. 对最多两个原木目标执行“观察/接近/挖掘/辨认掉落物/拾取/最终背包复核”的有限链路；
4. 对授权、目标变化、错误掉落物、路径失败、取消与部分完成做 fail-closed 处理；
5. 提供真实 Java + Mineflayer 端到端证据。

以下内容不属于 Phase 8D：

- 3 个以上方块的批量采集、无限循环、自动补充下一批或无人监督持续挖掘；
- 制作/合成工具、配方选择与材料规划（Phase 8E）；
- 把物品交给玩家、放进容器或送到指定地点（Phase 8F）；
- 新的技能学习/效果反馈系统（Phase 8G）；
- 泛化到任意方块、任意自然语言目标的完整游戏代理（Phase 8H）；
- 采集矿石、挖掘隧道、砍伐树叶、清理建筑、攻击生物/玩家、放置方块、自动装备从未在计划中明确列出的物品。

---

## §3. 必须先审计的现状

先用当前 HEAD 重新核对源码，不要根据本任务书中的路径猜实现细节。审计结论必须写入 `docs/MINECRAFT_PHASE8D.md`，并区分“已有能力”“真实缺口”“本阶段改动”。

重点核对：

1. `app/tasks/intent.py`：`TaskIntentDetector.detect()`、`detect_follow()`、`control_command()`、`status_query()`、查询排除规则与资源词表；当前任务识别要求动作词、多阶段标记和资源词，可能存在用户表达与实际意图识别不一致。
2. `app/tasks/turn.py`：`TaskTurnHandler.handle()`、`_create()`、`block_for()`、`_record_agent_plan()`；确认 Minecraft Chat 与 QQ 是否共用相关对象/服务，不能无意改变 QQ 路径。
3. `app/tasks/planner.py`：`plan_resource_task()`、`Observation`、`PlannedTask`、`plan_exploration_task()`、引用解析/结果结构。现有 `plan_resource_task()` 以**单个目标方块**为基本单位，不可未经审计就当作“砍一棵树”的批量计划。
4. `app/tasks/agent_planner.py`：`BoundedAgentPlanner.plan()`、资源形状分派、`_policy_blocked()`、`PlanningOutcome`。核对 `allow_medium=False` 是否在木材计划创建之前生效。
5. `app/tasks/agent_service.py`：`plan_goal_from_user()`、计划指纹预留、`TaskRuntime.create_task()`、`_link_reserved_plan()`、失败补偿和 `AgentPlan.plan_hash`；该服务是现有计划/审计层，不得复制它的职责。
6. `app/tasks/runtime.py`、`app/tasks/validation.py`、`app/tasks/models.py`：任务/步骤状态机、`MAX_STEPS`、`MAX_ACTION_STEPS`、`validate_plan()`、冻结计划、引用解析、授权过期、取消、步骤失败/暂停、`action_id` 对账和终态后置条件。
7. `app/integrations/minecraft/agent.py` 及 `minecraft_runtime/`：现有工具 Schema/风险表/策略门、目标块校验、`dig`、掉落物观察和 `pickup_item` 的真实结果字段与约束。
8. Phase 8C 文档及 Follow-up：确认 `follow`/`explore` 已接线，规划服务缺失/异常不会返回 `None` 而掉入 `CharacterRuntime` / `ToolOrchestrator` 单步工具链；普通聊天、SAFE 查询和控制命令仍不被误接管。
9. Phase 7F 的失败停机/到达复核语义与现有真实 smoke 脚本：复用现有检查方式、端口隔离、可见窗口启停、超时和脱敏机制，不另造一套任务执行器。

### §3.1 本阶段必须处理的已知风险

Phase 8C 文档记录了一个相关缺口：Minecraft Chat 的旧资源任务路径直接调用 `plan_resource_task()`，可能绕开 `BoundedAgentPlanner` 的规划期风险闸；MEDIUM 风险在执行期依然会被现有策略拦截，但计划可能先被建成待确认状态。

**Phase 8D 的木材采集路径不得继承这项缺口。** 木材采集涉及 `minecraft_dig` 与 `minecraft_pickup_item`（均为 MEDIUM），因此该路径必须在创建可执行任务之前通过现有 planner/policy 审查，并如实把不允许的计划以 `BLOCKED_BY_POLICY`（或项目既有等价结果）返回。只在执行期等待它暂停，不满足本阶段的规划期验收。

---

## §4. 架构与单一入口约束

### 4.1 必须复用既有链路

预期的职责顺序：

```text
Minecraft chat
  → 既有任务入口 / 控制命令识别
  → 确定性 wood intent 与目标种类解析
  → SAFE 观察（world / find_blocks / dig_capability / inventory 等已注册工具）
  → 现有有界 planner + planning policy gate
  → AgentPlan 计划记录 + TaskRuntime 冻结任务
  → 游戏内显示实际的数量/步骤摘要
  → 同一用户、同一 session 的明确确认
  → 既有 TaskAuthorization / TaskStepAuthorization
  → Policy → AgentBridge → Service → Action Runtime → Mineflayer
  → 对每步的真实回执对账 → 背包增量与终态验证 → 如实回复
```

函数或对象名称若在最新 HEAD 已变化，采用源码实际存在的实现并在报告中说明，不得为了贴合此图重建重复层。

### 4.2 单一接管与失败语义

- 每条消息只能被一个任务入口接管一次；不得同时进入 `TaskTurnHandler` 和另一个计划入口而产生两个 Task/AgentPlan。
- 控制命令、状态查询、普通闲聊、纯查询、目标不足和支持的 wood 请求必须有互斥明确的路由结果。
- 对已识别为支持的 wood 多步目标，一旦任务入口开始接管，后续任何服务缺失、规划异常、观察失败或计划被策略拒绝，都必须返回明确失败/澄清并记录稳定 reason code；不得返回“交回普通聊天”的 `None`，更不得回退到 `ToolOrchestrator` 的单步工具链。
- 仅非任务类消息可回到普通角色对话；这条回退规则必须有测试证明。
- 不要将 `AgentPlan` 当成执行状态真源；真实运行状态以 `TaskRuntime` 为准。

### 4.3 保持 QQ 兼容

本阶段针对 Minecraft wood 入口。不应把新语义无条件扩展到 QQ 入口。任何共用函数修改都必须明确解释为什么不会改变既有 QQ 消息检测、回复措辞、确认/取消或计划指纹。至少运行相关 QQ 入口回归测试。

---

## §5. Wood intent 与目标选择契约

### 5.1 确定性识别

- 采用规则/类型化解析，不增加一次 LLM 调用来猜目标数量、方块类别或真实世界坐标。
- 为当前支持的“树 / 原木 / 木头 / 具体树种原木 / 数量”表达建立清楚、确定性的映射和测试。
- 对每个支持的别名，证明它同时满足任务意图识别、目标块映射和 planner 选择的一致性；修复本阶段涉及的检测器—`block_for()` 词表错位，但不要顺手重构所有非木材资源词表。
- 纯查询语句（如“附近有什么木头？”“橡木原木在哪里？”）不得创建任务；“帮我采两根橡木原木并捡回来”这类明确动作请求才能进入任务规划。
- 未指定树种时，只能在经过审计且真实工具确实支持的可选原木列表中确定性选择；优先选择符合距离/可达/同一树干条件的最近目标。若工具不能给出足够信息来证明目标同组或不属于互不相关的木构件，应拒绝/澄清，而不是凭猜测声称识别一棵树。
- 本阶段只支持 Minecraft 1.21.1 环境下已验证的普通树干原木种类。未验证的树种、树干变体、树叶、去皮木、木头方块、竹子/下界菌柄等不可静默映射为橡木；要么明确列入支持矩阵并验证，要么明确拒绝。

### 5.2 目标/动作必须冻结

- 计划中的每一个目标方块必须包含明确坐标、期望方块 ID（例如 `minecraft:oak_log`）及由既有校验支持的相关参数。
- 每个 `minecraft_dig` 步骤都应绑定计划时确认的目标块；实际挖掘前若目标消失、方块变为其他种类或目标不再满足可达条件，必须按既有失败/暂停/重规划语义停下。
- 计划确认后，不允许把失效的坐标自动替换为附近另一个方块；需要新目标时，必须走既有重新规划与再次确认流程。
- 每次挖掘后的掉落物选择必须防止拾取他人的无关物品。`expected_item` 必须是确定值，不能仅凭“最近掉落物”就声称拾取成功；无法确定对应掉落物时宁可失败，也不得扩展成任意拾取。
- 对实际拾取结果使用当前工具的真实返回结构与观察数据，最终用实际 inventory 复核采集数量。计划里预期有物品、工具调用返回 success、看到掉落物或模型回复“拿到了”，都不能单独作为采集成功证据。

### 5.3 数量边界与资源预算

- 每个任务最多 2 个原木目标；显式数量、实际观察到的候选数量与预算三者取最小值，但如果请求数量被截断必须在确认前明确显示。
- `MAX_STEPS`、`MAX_ACTION_STEPS`、动作风险和每工具参数限制均沿用当前项目值；不得为满足采集计划提高这些上限。
- 最坏路径预算必须包含必要的 `move_to`、`dig`、`dropped_items`、`pickup_item`、可选的既有装备步骤和最终 `inventory` 复核。默认以每个目标最多一次必要移动、一次挖掘和一次拾取做预算估算；如果既有真实路径需要更多步骤，降低每任务目标数量，而不是扩大执行预算。
- 不允许无界发现—挖掘循环、后台自动再次规划、无限拾取重试、失败后自动改挖别的原木或完成一个批次后自动开启下一个批次。

---

## §6. 风险、确认与安全不变量

### 6.1 风险闸

以下是当前已有的风险语义，开工后仍要以最新源码确认：

- `minecraft_world`、`minecraft_find_blocks`、`minecraft_dig_capability`、`minecraft_inventory`、`minecraft_dropped_items` 等为 SAFE 只读观察工具（以实际 `ACTION_RISK` 为准）。
- `minecraft_move_to` 为 LOW。
- `minecraft_dig`、`minecraft_pickup_item`、如需要的 `minecraft_equip` 为 MEDIUM。
- `allow_medium` 的**默认值必须继续为 `False`**。禁止改成默认启用，禁止用代码、环境变量或用户消息伪造策略允许。

必须测试两个独立情况：

1. **`allow_medium=False`**：木材采集计划在规划期被明确阻止，不创建可执行采集任务，不产生 `dig` / `pickup_item` 的 `action_id`，世界与背包不变。
2. **隔离测试配置中显式设置 `allow_medium=True`**：仅用于本地专用测试服、临时配置/临时数据库，以验证功能在策略确实允许的情况下是否能完成；原始真实 `config/config.yaml` 不得修改。即便此时，仍必须有明确用户请求、计划展示、真实用户确认、任务所有权校验和步骤级授权。

测试报告必须说明第二种场景使用的是隔离测试配置；不得据此宣称生产默认配置也允许采集。若产品必须让用户实际使用木材采集，文档应明确需要用户自行、明确地启用相关风险等级，不能替用户更改默认设置。

### 6.2 不可改动的安全边界

- `ACTION_RISK` 风险表及现有 19 个 Minecraft 工具不得增加、重新分类或扩大权限。
- 不得更改 `allow_medium=False` 默认值、`trusted_players`、`TurnOrigin` 派生、两道确认门、`TaskAuthorization` / `TaskStepAuthorization`、Policy、AgentBridge、Service、Action Runtime 与 TaskRuntime 状态机。
- 不得绕过工具 Schema、参数校验、冻结计划哈希、参数指纹或 `action_id`/runtime instance 对账。
- 不得将 identity/VERIFIED 身份结果当成游戏动作授权；不得从玩家昵称推断可信目标。木材采集不需要借用另一个玩家的身份。
- 对 LOW/MEDIUM 世界修改步骤，不自动重试；出现 block changed、path not reached、掉落物不匹配、pickup 失败、取消或 dispatch ambiguity 时不再派发后续世界修改步骤。
- 不提供世界修改 rollback；失败报告真实的“未完成/部分完成”，不假称物品已回滚。
- 不得新增工具、动作、数据库表、Schema 迁移、第二套 planner/TaskRuntime 或无边界的通用 Agent 执行器。

---

## §7. 实现交付与变更范围

在完成 §3 源码审计后，按最小范围实现。预计需触及的模块（实际可变更列表以 diff 为准）：

1. `app/tasks/intent.py`：wood 请求识别、数量/目标种类规则及本阶段涉及的资源 alias 统一；保持查询排除和控制命令优先级。
2. `app/tasks/planner.py` 与/或 `app/tasks/agent_planner.py`：生成有限木材采集计划，明确每个目标块的坐标、方块预期值、步骤顺序和最终库存预期；通过已有风险闸。优先扩展既有确定性规划逻辑，不复制运行时。
3. `app/tasks/agent_service.py`：仅在确有必要时接入既有 `plan_goal_from_user()` 计划/任务关联路径；保留既有 reserve/link/补偿和 `plan_hash` 语义。
4. `app/tasks/turn.py` / `app/integrations/minecraft/chat_bridge.py`：只在单一接管与控制命令顺序要求下接线；对受支持 wood 目标的计划服务缺失、异常、观察失败和政策阻断都要有明确失败结果，不能 fallback 到普通 `ToolOrchestrator`。
5. `tests/`：为解析、预算、计划、策略、失败停机、确认、真实结果验证与单一接管添加确定性回归测试。
6. `docs/MINECRAFT_PHASE8D.md`、`docs/README.md`、`CHANGELOG.md` 和归档任务书按发布规则更新。

不得先按以上文件清单盲目改代码。若审计证明某模块不需修改，就不动，并在报告中解释已有实现如何满足要求。

---

## §8. 自动化测试要求

所有新增测试必须有确定性、无真实凭据、无 live DB 写入。不得靠 `sleep` 竞态、真实 LLM 随机回复或提高全局预算制造通过。

### A. 意图与路由

- 支持的中文 wood 请求准确落入唯一任务入口；数量和树种解析正确。
- 纯查询、普通闲聊、控制命令、任务状态查询仍按原有语义运行；不建木材任务。
- 受支持目标在计划服务缺失或抛异常时明确 BLOCKED/失败，不返回触发角色回合 fallback 的 `None`，`ToolOrchestrator` 无调用。
- 当前会话已有非终态任务时返回既有 busy 语义，不新建第二个木材任务；重复请求不产生两个 AgentPlan/Task。
- QQ 入口行为不变；原有 follow/explore 的 8C 路由回归全绿。

### B. 目标选择和计划预算

- 仅选择已支持的原木方块 ID；未知树种/木质方块/叶子不会被映射成错误目标。
- 多个目标确属同一组符合规则的树干候选；相邻性/高度/距离/目标数量有测试证明。
- 遇到独立建筑木柱、不同树种混杂、未能证明属于同一目标组、目标超距离、不可达或世界数据不完整时，按契约澄清/拒绝，不猜坐标。
- 一个和两个原木的合法计划能通过既有 `validate_plan()`；超出上限的请求不自动拓宽步骤数。
- 每个挖掘步骤都有确定 `expected_block` / 坐标，计划 hash 随冻结目标与步骤变化；确认后无法无声换目标。
- SAFE observations 的真实 payload 在进入 `TaskRuntime.create_task()` 时是正确的 payload 类型；不得重现 Phase 8C 的 `Observation` 对象与 dict 类型错配。

### C. 物品关联与最终验证

- 每个 pickup 都绑定正确的掉落物关联/引用和确定的 `expected_item`。
- 旁边存在无关物品时不得误拾；引用缺失、类型不符或项目不符应停下。
- 每个成功采集目标在实际 inventory delta 中体现；最终数量不足时只能报告部分完成/失败。
- “dig 返回成功但拾取失败”“找到了掉落物但背包没有增量”“目标块在执行前改变”等路径均不得报告 SUCCEEDED。

### D. 授权、策略与失败停机

- `allow_medium=False` 时在规划期阻止本阶段需要的 MEDIUM 采集步骤；无 world mutation、无 `action_id`，不留下可执行的待确认计划。
- 在测试配置显式允许 MEDIUM 时，确认前零动作；无确认、错用户、错 session、过期确认、确认参数/hash 不匹配时均不得开始。
- 真实用户确认后仍通过现有 TaskRuntime、每步骤授权、Policy、AgentBridge、Service、Action Runtime。
- 第一个目标或中间步骤失败后，不继续挖后续目标；取消之后不继续 dispatch；不重试任何世界修改动作。
- 已确认计划的目标块被其他真实客户端改变后，不执行陈旧目标，不擅自选另一个目标，不自动继续旧计划。
- 计划服务/存储/link 失败时不遗留可继续执行的孤儿任务；复用并验证既有补偿与恢复机制。

### E. 定向回归与测试命令

执行并逐条记录真实结果与退出码：

- 新增 wood 测试文件/类的定向 pytest；
- `tests/test_phase8c_bounded_orchestration.py`；
- `tests/test_minecraft_chat_bridge.py` 及相关 chat/route/intent 测试；
- `tests/test_agent_plan_negative.py` 与 AgentPlan/TaskRuntime 相关全套测试；
- 任务识别、计划校验、动作授权、真实运行时 E2E 的相关套件；
- 最后执行 `pytest tests -q` 全量测试。

测试文件具体名称按仓库当前布局选用。不得只报“测试通过”，必须列命令、通过/失败/skip 数字与退出码。

---

## §9. 真实 Java + Mineflayer 门禁

实现本地专用脚本：`scripts/wood_gathering_smoke_real.py`。遵守项目 `scripts/` 本地专用约定，不默认提交/发布脚本。

### 9.1 环境要求

- 真实 Java Minecraft **1.21.1 + Fabric** 测试服务器；实际服务端版本须取证，不凭配置文件猜。
- 真实 Node/Mineflayer 运行时与实际 CatooBot Minecraft 桥接，不用 fake server、mock executor 或模拟背包替代关键场景。
- 至少一个真实第二客户端用于发送 Minecraft chat；需要验证目标变化/取消时，可由第二客户端或受控测试操作改变测试区域中的测试方块。
- 使用专门的测试世界/区域、临时 SQLite 和隔离测试配置；不对 live DB 写入，不在有价值的用户世界或公共多人服务器上破坏方块。
- 测试启动/停止使用可见窗口或现有经过审计的进程机制；为本轮 runtime 选择空闲端口，不与用户 Bot 抢占 25580 等既有端口。不得杀死不属于本脚本的进程。
- 仅在本地隔离测试设置中按场景显式切换 `allow_medium`。场景开始前后都核对真实配置没有被修改。
- 场景准备、清理、超时、退出码和证据位置均要明确。测试被中断时不可留下会自动续挖的后台任务或无法识别的 runtime。

### 9.2 必需真机场景

**场景 1 — 真实有限采集成功（必需 PASS）**  
在可复现、隔离的真实测试区域布置或定位同一棵可验证树干的 1–2 个目标原木。通过真实游戏内聊天发出请求；展示准确数量和目标摘要；确认前验证无挖掘/拾取 world steps；使用明确的用户确认；真实 Mineflayer 执行计划步骤；最终用实际背包 delta 证明目标原木入包，并核对 TaskRuntime `SUCCEEDED`、每步终态、`task_id`/`step_id`/`action_id` 关联。若只能完成部分目标，应按实际结果 `PARTIAL/FAILED`，不得写 PASS。

**场景 2 — 默认/关闭 MEDIUM 策略**  
隔离运行配置中 `allow_medium=False`，同类木材请求必须在规划期给出策略阻断；不得创建可执行的采集任务，不发生 dig/pickup，不产生相关 `action_id`，真实世界/背包不变。

**场景 3 — 计划确认前取消/不确认**  
创建真实待确认木材计划后不确认或明确取消；确认前与取消后都必须无挖掘、拾取动作，任务终态和用户回复准确。

**场景 4 — 目标方块改变/消失**  
计划后、确认前或允许的执行边界上，由第二客户端在测试区改变指定方块。系统必须拒绝旧 `expected_block`/坐标，不得偷偷选择邻近新方块；不能继续派发后续采集步骤，必要时走既有重新规划并重新确认路径。

**场景 5 — 掉落物/拾取失败与部分完成**  
使用合法、可控的真实失败条件验证某一步失败后停止后续世界修改步骤。验证不能把“已挖掉但未入包”算作收集成功；不允许无限重试或任意拾取旁边其他物品。

**场景 6 — 路径/可达性失败**  
在真实世界使用可复现的不可达目标或受控路径阻断，确保失败按实际结果反映，后续步骤不继续；不得放宽到达半径或目标距离阈值。

**场景 7 — 普通聊天、SAFE 查询、busy 和控制命令**  
至少覆盖普通闲聊与纯查询不建任务；活动任务期间第二个 wood 请求被既有 busy 语义拒绝；确认/取消/状态查询由原控制面处理，不被误识别成新的采集目标。若这些子项不能在同一次运行中隔离，分开记录，但不得省略必需断言。

真实场景 1 是 Phase 8D DoD 的核心。任何必需真机场景因环境或前置条件无法执行，均报告 `BLOCKED`/`SKIPPED` 与实际原因；不得把 mock、CI E2E 或假背包视为等价替代。失败后可以修复再跑，但必须保留首次失败结果和重跑原因，不能删记录或弱化断言。

### 9.3 真机证据最低标准

每个场景记录：

- 场景 ID、测试世界/服务器与客户端是否真实、实际版本；
- 使用的隔离配置（只记录风险开关状态，不导出私密配置）；
- 输入类别、目标解析结果、RouteTrace reason code；
- 观察到的目标方块 ID/坐标摘要、冻结计划数量与计划 hash 的脱敏摘要；
- 待确认、确认、授权、各步骤状态和动作关联结果；
- 方块前后事实、掉落物/拾取回执、最终 inventory delta、TaskRuntime 终态；
- 清理结果、进程/端口状态和本地证据文件路径；
- PASS / FAIL / BLOCKED / SKIPPED 裁决。

不得在公开日志中记录完整 UUID、QQ ID、认证数据或聊天原文。可用局部 hash/短摘要关联对象，但必须保证不同任务/步骤之间的证据关系可读。

---

## §10. 文档、全量门禁与发布

### 10.1 文档

- 新增 `docs/MINECRAFT_PHASE8D.md`，记录实现背景、路由链、支持树种/句式、目标选择算法、数量/距离/步骤上限、policy gate、失败语义、测试和真实结果。
- 更新 `docs/README.md` 索引与 `CHANGELOG.md`。
- 归档本任务书至 `docs/specs/CatooBot Minecraft Phase 8D — Bounded Resource Gathering & Wood Collection.md`。归档后核验字节数、行数和 SHA256。
- 文档必须说明：默认 `allow_medium=False` 时本能力会被阻止；要在实际运行中启用相关 MEDIUM 动作，必须由用户自己明确改变配置。本阶段不能替用户启用默认风险选项。

### 10.2 必须执行的发布前门禁

1. wood intent/planner/runtime 相关定向测试；
2. Phase 8C Follow-up 17 项测试与 Phase 8C 相关回归；
3. AgentPlan 预留/关联/孤儿补偿测试；
4. 全量 `pytest tests -q`；
5. `ruff check .`；
6. `ruff format --check .`；
7. `mypy app`；
8. 若改动 `minecraft_runtime/`，执行该目录 npm tests/E2E；如未改也要核实 CI 对现有 runtime 门禁的结果；
9. WebUI typecheck/Vitest/build/E2E（改 WebUI/API 时需本地执行；否则核实必需 CI job）；
10. SQLite 迁移与数据保护核查：报告最高 schema version、表数、无新迁移/表、live DB 未被写入的证据；
11. diff 安全审计：风险表、19 工具、默认配置、身份边界、确认门、授权和动作生命周期；
12. 改动文件凭据/私有内容扫描与发布清单核验；
13. 真实 Java 七场景 smoke 逐项裁定；
14. 镜像与 GitHub CI 最终核验。

### 10.3 发布纪律

- 只有全部必需本地门禁通过且真机成功路径真实 PASS 后才可提交发布。
- 发布镜像同步获准的源码、测试、文档、CHANGELOG、README 和任务书归档；`scripts/`、真实配置、认证文件、运行数据与私密源按原有规则保持本地。
- 独立提交，建议提交信息：`Phase 8D: bounded resource gathering and wood collection`。
- 推送后核对 `origin/main` 等于最终 SHA、镜像无差异、工作树干净，并核查 CI 两个必需 job 的最终状态。
- 对 CI flaky 只能按既有规则保留首次失败并重跑失败 job；不得放宽断言、超时、风险等级、数量或距离限制。
- 任一必需场景缺证据、真机成功路径没完成、策略门绕过、world state 结果无法对账或 CI 必需 job 未成功，则最终裁决保持 `PARTIAL`，不得为赶阶段而强行 PASS。

---

## §11. 必须保留的跨阶段回归

1. Phase 8C 的 `follow` / `explore` 路由不被木材 intent 抢占或改写。
2. Phase 8C Follow-up 的 `plan_service_unavailable` / `plan_service_error` 仍然 BLOCKED；不会落回 `CharacterRuntime` / `ToolOrchestrator`。
3. 计划服务异常、目标观察失败、目标过期或方块改变时无重复 Task、无孤儿可执行任务、无自动原子工具 fallback。
4. 普通聊天与 SAFE 查询不建任务；“附近有什么木头？”不是“帮我砍树”。
5. 任务 owner/session、真实 USER 回合确认、计划哈希和参数指纹必须继续有效；其他玩家不能确认/取消非本人任务。
6. 一个世界修改步骤失败或被取消，下一步骤不会继续执行；LOW/MEDIUM 世界修改不自动重试。
7. `ActionRuntime` detached action、dispatch ambiguity、runtime instance change 均遵循现有 fail-closed/reconciliation 语义。

---

## §12. Definition of Done（DoD）

只有以下各项全部满足，Phase 8D 才能裁定 PASS：

- [ ] 以真实当前 HEAD 开工，工作区/镜像/发布边界核查完整。
- [ ] 木材 intent、树种/数量解析确定性、可测试；纯查询与非任务消息没有误接管。
- [ ] 真实资源目标来自 SAFE 观察；没有目标、观察不完整、不可达、同树归属不确定或目标改变时正确 fail-closed。
- [ ] 任务最多 2 个原木目标并符合现有任务步骤/动作预算；不扩张预算、不自动跑第二批。
- [ ] 木材计划通过已有 `BoundedAgentPlanner`/policy 受控入口；`allow_medium=False` 时规划期明确阻止；无可执行任务、无相关 `action_id`、世界与背包不变。
- [ ] `allow_medium=True` 的隔离配置测试中，真实用户确认前零挖掘/拾取；确认后每个动作都经过 TaskRuntime/步骤授权/Policy/AgentBridge/ActionRuntime。
- [ ] 采集成功以真实拾取及 inventory delta 为准；每个目标块、掉落物和拾取结果可对账；没有伪成功/误拾。
- [ ] 真实 Java/Mineflayer 场景 1（有限采集成功）真实 PASS；其余 §9.2 必需场景有明确真实证据并通过。任何必需场景不能执行时不得 PASS。
- [ ] 中途失败、取消、目标变化、错误掉落物或派发歧义后，不继续派发后续世界修改步骤；不重试 MEDIUM 动作。
- [ ] Phase 8C 与 Follow-up 的路由、权限、确认、失败回退和补偿回归全部通过。
- [ ] 全量 pytest、Ruff、format、Mypy 与相关 Node/CI 门禁通过；所有数字、退出码与 CI URL 可核验。
- [ ] 无新增动作/工具/Schema 迁移；安全不变量与 live DB 未变有证据。
- [ ] 文档、README、CHANGELOG、spec 归档、镜像与 GitHub `origin/main` 一致；私密文件未发布；工作区干净。

---

## §13. 最终报告格式（逐项回答，不得省略）

1. 任务书 SHA256、任务书归档结果、任务书基线、实际开工 Base SHA、最终 SHA、提交数与工作区/镜像/`origin/main` 状态。
2. 当前源码真实调用链、Wood intent 识别/解析/规划/确认/执行/验证函数；新增与复用路径分开列出。
3. 支持的原木种类、语言句式、数量语义、不支持目标及澄清规则。
4. 每任务硬上限、实际 step/action step 预算、距离范围、SAFE observations 和目标同组证明方式。
5. `allow_medium=False` 与隔离 `True` 两条策略场景的实际证据；确认前零执行、确认后的授权链证据。
6. 文件逐一 diff 理由、变更规模、任何未修复审计发现和为什么不越界处理。
7. 行为矩阵：闲聊、SAFE 查询、wood 请求、未知树种、目标缺失/变化、策略阻止、busy、错 owner、确认/取消、部分拾取/执行失败。
8. 新增测试确切数量、定向测试结果、相关回归、全量 pytest 数字和退出码。
9. Ruff、format、Mypy、Node/WebUI（如适用）、CI run URL/attempt 与两个必需 job 结果；首次失败/重跑如实记录。
10. 真 Java 七场景逐项 PASS/FAIL/BLOCKED/SKIPPED 与脱敏的任务/步骤/动作关联、inventory delta 和终态证据。
11. 数据库 Schema/迁移、live DB 保护、真实配置未改与凭据扫描结果。
12. 文档/README/CHANGELOG/spec 归档、文件 SHA256、镜像差异与发布清单。
13. 已知限制、尚未解决的路径精度/掉落物/目标归属问题和对用户的实际影响。
14. 最终裁决 `PASS` / `PARTIAL` / `FAIL`，按 §12 每项给出具体证据；不得把“脚本退出 0”或“任务已创建”单独当作成功证据。

---

## §14. 一句话原则

**只采集观察确认的目标、只执行用户确认的冻结计划、只把真实进入背包的物品算作成果；任何不确定性都缩小范围或停下来，而不是扩大权限、预算或猜测目标。**
