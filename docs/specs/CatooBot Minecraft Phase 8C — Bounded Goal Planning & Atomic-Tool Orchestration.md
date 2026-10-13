# CatooBot Minecraft Phase 8C — Bounded Goal Planning & Atomic-Tool Orchestration

**中文标题：有界目标规划与原子工具编排**  
**阶段目标：** 把用户提出的、当前能力范围内的 Minecraft 目标，稳定送入项目已经存在的有界规划与任务执行链；计划必须基于真实观察、可校验、可预览、可授权，并由既有 `TaskRuntime` 串联原子工具完成。  
**路线位置：** Phase 8A（自然语言路由与可观测性）+ 8B（跨平台身份连续性与信任上下文）完成后，为 8D 的资源采集和后续简单游玩闭环做好入口/规划衔接。

> **基线提示：** 本任务书编写时核对的 `main` HEAD 为 `9bf125c9e3d396950ee678060eab585006742b20`。DSH 开工必须重新读取最新 `origin/main`、提交历史和工作区状态；如果 HEAD 已前进，以新的干净 `main` HEAD 作为真实基线，不得硬 reset 到旧 SHA，也不得将未提交或本地私有数据覆盖掉。

---

## §1. 开工门禁与工作纪律

1. 权威开发目录：`E:\WorkSpace ZCode\CatooBot`；发布镜像：`E:\WorkSpace ZCode\CatooBot_github`。依项目既有双目录发布工作流操作，工作区源码是开发真源，干净镜像才是 GitHub 发布来源。
2. 开工时报告：`main`/`origin/main` SHA、工作区状态、最新 Phase 8A/8B 文档版本和 CI 状态。不可假定仓库还停在任务书编写时的 SHA。
3. 不读取、打印或提交 `.env`、真实 `config/config.yaml`、`data/`、`minecraft_runtime/auth.json`、QQ/NapCat token、真实服务器凭据、角色 Bible 私有源文件。诊断运行配置时只报告脱敏后的 bool/枚举/计数。
4. 不回头重审 Phase 7F 生命周期修复、8A 路由、8B 身份桥或 8B Follow-up；只有 Phase 8C 实际复现且有证据的新缺陷才列为阻塞项。
5. 先审计，后设计，再编码。若现有代码已经完整满足某项契约，不得为了“做一个新阶段”复制一个规划器、TaskRuntime 或权限系统；应将该项标为“既有能力已满足”，把实现限制在实际缺口。
6. 不得把本阶段与 8D/8E/8F 功能合并发布。本阶段不是树木批量采伐、制作/合成或玩家物品交付的实现阶段。

## §2. 必须核验的现有源码与阶段文档

先按当前 HEAD 阅读完整实现，不可仅凭类名或历史报告推断接线状态：

- 路由与用户入口：
  - `app/integrations/minecraft/chat_bridge.py`：`apply_event()`、`_reply()`、`_task_reply()`、`_session_id()`。
  - `app/tasks/turn.py`：`TaskTurnHandler.handle()`、`_create()`、`_replan()`、状态/确认/暂停/恢复/取消入口。
  - `app/tasks/intent.py`：`TaskIntentDetector.detect()`、`detect_follow()`、`control_command()`、纯查询排除规则。
  - `app/character/runtime.py` 与 `app/tools/router.py`：Phase 8A `RouteTrace` / `RouteTarget` / `reason_code` 的记录与收尾路径。
- 既有规划与提案：
  - `app/tasks/planner.py`：`plan_resource_task()`、`plan_exploration_task()`、`ModelTaskPlanner`（若存在于当前 HEAD）。
  - `app/tasks/agent_planner.py`：`BoundedAgentPlanner`、`PlanningOutcome`、`detect_shape()`、资源/跟随/探索/未知目标分支。
  - `app/tasks/agent_plan.py`、`app/tasks/agent_plan_store.py`、`app/tasks/agent_service.py`：`AgentPlan` 的计划/审计职责、USER/LIFE 路径、确认/批准、过期/重规划与持久化语义。
  - `app/tasks/proposal.py`、`app/tasks/proposal_service.py`、`app/tasks/capabilities.py`：`TaskProposal` 与能力目录、目标身份状态、unsupported/unknown 的表达。
  - `app/tasks/skill_planner.py`、`app/tasks/skill_service.py`、`app/tasks/skill_learning.py`：技能只提供候选，不是授权或执行入口。
- 执行与安全：
  - `app/tasks/runtime.py`、`app/tasks/models.py`、`app/tasks/validation.py`：`TaskRuntime` 生命周期、冻结计划/哈希、参数引用、步骤限制、终态/授权/派发证据。
  - `app/integrations/minecraft/agent.py`、`app/integrations/minecraft/confirmation.py`、`app/tools/policy.py`、`app/tools/executor.py`、`app/integrations/minecraft/service.py`。
  - `app/integrations/minecraft/identity_context.py`、`app/integrations/minecraft/identity.py`、`app/integrations/minecraft/memory_bridge.py`：目标身份必须来源于可靠的身份/世界观察，不从自然语言昵称推断 UUID。
- 文档：`docs/MINECRAFT_PHASE_7C.md`、`docs/MINECRAFT_PHASE_7D.md`、`docs/MINECRAFT_PHASE_7E.md`、`docs/MINECRAFT_PHASE_7F.1.md`、`docs/MINECRAFT_PHASE_7F.2.md`、最新 `docs/MINECRAFT_PHASE8A.md`、Phase 8A FIX 记录与 `docs/MINECRAFT_PHASE8B.md`（含 §13 Follow-up）。

### 当前已知的实现背景（开工后仍需用最新源码逐项确认）

- Phase 8A 已建立有限的 `RouteTarget`（`CHAT_ONLY` / `MINECRAFT_TOOL` / `TASK` / `CLARIFY` / `BLOCKED`）与有界路由记录；8A FIX 已确保“模型未选择工具”最终归 `CHAT_ONLY`，理由码仍与“无候选”区分。
- Minecraft 入站聊天先进入 `MinecraftChatBridge` 的任务侧 `_task_reply()`，只有任务侧未接管才进入 `CharacterRuntime.respond()` 的普通对话/工具路由。这两个路径不可重复执行同一请求。
- `TaskIntentDetector.detect()` 是确定性识别器，并明确排除纯查询；原始识别规则需要动作动词、多阶段标记和资源/方块词。不要为了提高命中率而让普通闲聊、纯查询或含关键词的句子自动变成任务。
- `TaskTurnHandler` 已有资源任务路径，`plan_resource_task()` 会先做 SAFE 观察，再生成有限 `TaskPlan`。`BoundedAgentPlanner` 已表示 `follow/resource/explore/unknown` 等形状与六种 `PlanningOutcome`。`AgentPlan` 是规划/审计层，不是第二个任务运行时；`TaskRuntime` 才是执行状态真源。
- `TaskProposal`（7C）、`AgentPlan`（7D）、`Task`（5A）和技能记忆（7E）有不同职责，禁止把它们合并成一个状态机。
- 当前资源任务模板是有限的资源目标链，不等价于“树木采伐/多原木批量收集”；后一能力留给 8D。
- 当前能力目录从现有 `ACTION_RISK` 与工具注册元数据派生。未知能力不得因为工具存在或 LLM 给出步骤就升级为受支持。

## §3. 本阶段目标与边界

### §3.1 必须达到的目标

1. 从真实入口开始，建立可测试的“自然语言目标 → 确定性识别/已有目标形状 → SAFE 世界观察 → 有界结构化计划 → 既有校验 → 计划呈现/确认 → 既有 TaskRuntime → 原子工具执行/结果验证”路径，找出现有路由中真实的接线缺口并做最小修正。
2. 对当前已有规划器支持的 `resource`、`follow`、`explore` 目标形状，核验 Minecraft chat 入口的真实接线路径；凡是尚未进入有界任务/计划链的形状，必须复用现有模板/服务完成最小接线。任何目标均不得因为入口不同而丢失计划确认、任务归属、步骤授权、action_id 关联或结束验证。
3. 对目标模糊、缺乏真实观察、身份缺失、能力未知、政策关闭或工具不可用的情况，产生明确可审计结论（澄清/信息不足/不支持/策略阻止/前置条件阻止），不臆造计划。
4. 确保一次用户请求只由一个入口路径接管。若任务路径已处理该回合，不得再次触发自由工具调用；如果任务入口拒绝接管，普通聊天要正常继续。
5. 让任务路由能利用 Phase 8A 的结构化路由记录（如现有 `TASK` 路由字段/`task_id`）进行关联，且记录失败不得改变执行决策。
6. 继续复用 Phase 8B 的身份事实：需要指定玩家时，只能使用当前服务器上可信身份桥解析的 VERIFIED 目标；`UNBOUND` / `REVOKED` / `CONFLICT` / `SERVER_MISMATCH` / `DEGRADED` 不可被自然语言模型补全成身份。

### §3.2 明确非目标

- 不新增 Minecraft 工具、Action Runtime action、复合式 `minecraft_collect`/`minecraft_gather`/`auto_mine` 等黑盒能力。
- 不做树木砍伐、批量采木、连续采集、合成/制作、容器批量搬运或玩家间物品交付（留给 8D–8F）。
- 不创建第二个规划器/任务状态机/授权系统/记忆引擎，不重写 TaskProposal / AgentPlan / TaskRuntime。
- 不新增 LLM 调用只是为了把一句话分类；优先复用现有确定性 intent detector 与已有规划调用。若审计证明某个已有受限模型规划路径确有必要，必须使用结构化输出 + 现有 validate_plan，不能解析自由文本直接执行。
- 不改变 `ACTION_RISK`、`allow_safe/allow_low/allow_medium` 的默认值、`trusted_players`、`TurnOrigin` 派生、两道确认门、TaskAuthorization/TaskStepAuthorization、策略顺序、步骤授权、计划哈希或 Action Runtime 生命周期。
- 不把身份绑定、关系阶段、长期记忆、技能候选、LifeIntent 或 LLM 输出视为行动授权。
- 不降低工具 schema、步骤上限、移动/探索预算、执行超时或既有失败断言。
- 默认不新增数据库表/迁移；如果审计证明本阶段的已要求契约只有新增 schema 才能实现，必须先说明不可复用的具体证据，且不能自行扩展本任务范围。

## §4. 需要回答的审计问题（每项给源码证据）

1. Minecraft chat 入站时，哪些文本当前由 `TaskTurnHandler` 接管，哪些落到 `CharacterRuntime` / `ToolOrchestrator`？同一回合有没有重复任务/工具副作用的可能？
2. 对现有目标形状 `resource`、`follow`、`explore`，逐条列出：识别入口、计划生成函数、观察工具、计划校验、计划持久化/关联、确认者绑定、TaskRuntime 执行入口及最终完成证据。
3. `BoundedAgentPlanner` 和 `AgentPlanService` 当前是否已被 Minecraft chat 入口使用？若否，缺口在哪里；如果已有路径完整，则不要另造接线。
4. 对多步资源目标，用户可在确认前看到的是否是冻结计划里的准确工具、参数、目标坐标、风险、步骤数量和完成条件？坐标是否来自当时 SAFE 观察，且参与计划哈希/确认指纹？
5. 所有可变更世界的步骤是否必须经过既有确认/TaskStepAuthorization/Policy/AgentBridge/Service/Action Runtime？在 `allow_medium=false` 时涉及 MEDIUM 的计划是否如实阻止，而非被规划器或来源转换绕过？
6. 行动开始后，执行回执是否从 `TaskRuntime` 按 task_id/step_id/action_id/runtime instance 对账？运行中的 detached 动作有没有被错误地当作成功？中间失败、取消、重启/歧义后会不会继续派发下一步？
7. 纯查询、普通闲聊、控制命令、“确认”但无待确认任务、其他玩家试图控制任务时，是否仍保持既有语义？是否误建 Task？
8. 对 `NEEDS_MORE_INFORMATION`、`UNSUPPORTED`、`BLOCKED_BY_POLICY`、`BLOCKED_BY_PRECONDITION` 是否有明确、真实的用户反馈和结构化 reason？是否出现把“规划失败”伪装成“已经开始/完成”的情况？
9. `RouteTrace` 是否能区分 `TASK`/`CLARIFY`/`BLOCKED` 与普通 `CHAT_ONLY`；是否记录 `task_id`、原因码及必要的执行关联；是否避免落盘原始聊天/Prompt/思维链/完整 UUID/凭据？
10. 是否已存在满足要求的路径和测试？报告中逐条引用源码与测试名。没有证据的猜测不得写成根因。

## §5. 目标行为契约

按当前实际支持能力构造路由矩阵，至少覆盖以下场景（具体自然语言措辞根据源码/schema制定并固定测试夹具）：

| 用户意图 | 目标行为 | 不允许的行为 |
|---|---|---|
| 普通闲聊 | 只对话；零 Task、零世界动作 | 仅因提到 Minecraft/树/木头就建立任务 |
| SAFE 世界/背包/附近方块查询 | 走已有只读工具路径并报告真实结果 | 创建多步 Task 或杜撰观察结果 |
| 已支持的多步资源目标 | 若检测/前置/能力允许，先 SAFE 观察，再产生有限冻结计划与既有确认；确认前零世界修改 | 自由循环挖掘、自动批量采集、猜坐标、未确认先改世界 |
| 已支持的跟随目标 | Minecraft chat 若将该意图接管为任务，必须走现有身份解析、AgentPlan/TaskRuntime 路径；执行前复核 VERIFIED 目标、当前服务器、在线/可见前置 | 从自然语言昵称直接认人、绕过 VERIFIED 身份、把用户的话交给单步自由工具调用以规避任务确认/生命周期 |
| 已支持的探索目标 | Minecraft chat 的探索任务复用既有有界探索规划和真实位置验证；遵守原预算/路点限制与确认门 | 超预算移动、把到达计划点当成已到达、忽略失败后仍跑后续段，或任由模型自由扩展移动序列 |
| 信息不足/未知能力 | `CLARIFY` / `NEEDS_MORE_INFORMATION` / `UNSUPPORTED` 等准确结果，并解释必要缺口 | 虚构不存在的工具、参数或成功结果 |
| 策略/配置阻止 | `BLOCKED_BY_POLICY`/既有稳定错误原因；零受阻动作 | 静默放宽风险开关或换执行来源规避 Policy |
| 任务已在运行 | 遵循既有单会话任务约束，或如实报告 busy | 并行再建一个前台任务，重复派发相同动作 |
| 用户确认/取消/暂停/继续 | 必须匹配既有任务 owner/session/hash/authorization；控制动作走原控制面 | 由不同用户、昵称或身份记忆代替任务所有权 |

以上只定义现有能力的接线契约，不等于本阶段必须为所有目标新增实现；未满足前置的目标应如实失败/澄清。

## §6. 有界编排与计划规范

### §6.1 计划只由受限模板/模型规划路径产生

- 优先复用 `plan_resource_task()`、`plan_exploration_task()`、`BoundedAgentPlanner`、`AgentPlanService` 和已有规划器。不要复制这些逻辑到 `chat_bridge.py` 或新建 `phase8c_planner.py` 后绕过原校验。
- 每一步必须是注册表中的既有原子工具，参数必须符合真实 tool schema；禁止任何复合工具名、未知工具、任意 shell/HTTP/runtime 直调。
- SAFE 观察只可通过已允许的观察工具列表；观察失败就返回失败结论，不创建可执行的未知计划。
- 动态参数/步骤引用使用已有 `collect_references()`、`resolve_arguments()` 与 `validate_plan()`；不得另造变量插值协议。
- 计划需要包括明确 objective、按序 steps、工具和参数、risk、必要的真实 observations、expected final state/可验证后置条件。对于当前实现确实未支持的字段，不得编造“已验证”。

### §6.2 硬预算必须沿用已有上限

以实际 `main` 为准检查并保持项目既有约束。已知源码包括 `MAX_STEPS = 16`、`MAX_ACTION_STEPS = 8`，以及探索的路点/总路程预算。任务 TTL、授权 TTL、动作自身超时、重规划次数和 SAFE retry 额度是不同的限制，不得合并或静默放大。

- 任意步骤/动作数超限 → 校验拒绝整个计划。
- LOW/MEDIUM/世界修改动作不得通过额外循环绕过单步工具限制或获取无界重试。
- 每次真实世界修改之前必须有有效的原计划/步骤授权；计划变动即重新校验/确认，禁止沿用旧授权执行新参数。
- detached/RUNNING + `action_id` 的动作等待真实终态事件。不得按模型回答、工具返回或经过时间自行伪造 SUCCEEDED。
- 失败/取消/过期/运行时实例变化/dispatch ambiguity 遵循现有 fail-closed 与 TaskRuntime 对账语义；不得另写简单重试把不确定动作再执行一次。

### §6.3 规划结果与用户反馈

统一核验并复用已有结果枚举，特别区分：

- READY_FOR_APPROVAL（有计划，尚未得到执行授权）；
- NEEDS_MORE_INFORMATION（缺必要信息或目标不明确）；
- UNSUPPORTED（当前能力目录无法支撑目标）；
- BLOCKED_BY_POLICY（风险/配置明确禁止）；
- BLOCKED_BY_PRECONDITION（世界、身份、观察或目标等前置不满足）；
- REPLAN_REQUIRED（原冻结计划失效，需要重新观察/计划/确认）。

不要在入口自行创造第二套近义状态。每种结果都要确保用户看得到诚实而简短的回复；内部原因码必须可在日志/测试中核验。

## §7. Phase 8A / 8B 集成要求

1. 路由证据使用已有 `RouteTrace` 与 `RouteTarget`（如果当前 HEAD 仍保留对应 API）。不要增加另一套并行日志系统。
2. 任务侧接管回合的记录应包含稳定 reason code 与真实 `task_id`；已被 TaskTurnHandler 接管的回合不得再调用 ToolOrchestrator 执行同一请求。
3. 失败记录只记结构化事实：route/task/action ID、目标路由、reason、有限的工具/步骤名称、状态、耗时与长度。不得记原文、完整 Prompt、CoT、凭据、完整 UUID/QQ ID或背包私密细节。
4. Phase 8B `IdentityContext` 仅作身份/关系/记忆上下文，不是执行者身份凭据。动作 Policy 只接受当前调用链按原规则产生的 `TurnOrigin`/`origin_metadata` 与既有授权状态。
5. 跨平台 session/history 继续隔离。不因 `VERIFIED` 或同一个 `relationship_key` 拼接通道原始聊天历史，也不把另一通道的“确认”当成这条任务的确认。

## §8. 必须新增/补齐的自动化测试

先复用现有测试文件/fixtures，不要为了这阶段复制一整套大栈。新增测试建议放在 `tests/test_phase8c_bounded_orchestration.py` 或与现有模块最贴近的文件中。

### A. 路由与唯一接管

- Minecraft chat 中至少覆盖 `resource`、`follow`、`explore` 三种已有规划形状：各自按当前支持条件进入受限计划路径，或以真实、预期的身份/前置/策略 reason 明确拒绝；不得把应由 Task/AgentPlan/TaskRuntime 管理的多步目标只交给单次自由工具调用。三类 shape 若在最新 HEAD 已接通，测试其真实路径即可，不得重复重写实现。
- 明确的已支持多步目标进入既有 Task/AgentPlan/TaskRuntime 路径，生成一个有界计划；同一条消息不同时执行 ToolOrchestrator 与 TaskRuntime。
- 纯聊天与 SAFE 查询保留既有路径，不创建 Task。
- 纯查询词、目标关键词重叠、长文本/混合语句不得误触发任务。
- 已有活动任务时，新目标按既有 busy/owner/session 规则处理，不复制建任务。
- 控制命令与“确认”在无待确认任务时不被误当成新计划。

### B. 计划、观察与校验

- SAFE 观察失败/无目标/世界视图未就绪 → 明确失败/信息不足，不创建可执行任务。
- 计划只含注册工具；非法工具名、缺参、错误类型、无效/非有限坐标、缺失引用/前一步引用错误、危险超限计划均被拒绝。
- 动态参数在确认前物化冻结，确认摘要、plan hash、step authorization 使用的参数一致。
- 多步骤计划的执行序列与数据依赖顺序正确；一个步骤失败或被取消后，不会启动下一个步骤。
- 规划器输出 UNKNOWN/UNSUPPORTED 时不能被 skill candidate、LLM 输出或 capability name 自动升级。

### C. 授权与生命周期

- 任何需要确认/批准的步骤在门禁之前零世界动作；确认以后仍经过既有 `TaskRuntime` → Policy/confirmation/task authorization → AgentBridge → Service → Action Runtime。
- `allow_medium=false` 时含 MEDIUM 步骤的计划按照当前既有契约被阻止；测试不得修改默认值以“通过”。
- 任务 owner/session 不匹配时，不能确认/暂停/继续/取消另一个用户的任务，且拒绝不改变状态。
- `RUNNING` + `action_id` 不被标成完成；失败、取消、过期、runtime 重启/歧义时按既有规则对账，后续世界修改动作数不增加。
- 身份缺失/撤销/冲突/跨服不匹配/存储 DEGRADED 时，不能把玩家目标从自然语言猜出或继续旧目标。

### D. 可观测性与隐私

- TASK/CLARIFY/BLOCKED 的目标、reason、task_id 与实际结果一致；无原文/Prompt/CoT/凭据泄露。
- 路由/诊断记录写入失败不能改变实际授权和执行流程。
- 重复请求、重复结果事件、进程恢复不能制造额外的世界动作或重复任务关联。

所有新增单测必须确定性，不使用 `sleep` 制造时序，不依赖真实 LLM。需要并发时使用 `asyncio.Event`/可控假 store/可控 executor。

## §9. 真实 Java 服务器验收

新增或扩展一个本地专用的 smoke 脚本（例如 `scripts/bounded_orchestration_smoke_real.py`），在已有真实 Java 1.21.1 + Fabric + 真实 Node/Mineflayer 运行时中验证。脚本只在本地，不得进入发布镜像；不得把用户真实 DB 当测试夹具，先备份后用临时/隔离数据。

至少覆盖：

1. 一个当前支持的多步用户目标：真实 SAFE 观察 → 精确冻结计划/摘要 → 确认之前零世界修改 → 由用户授权后通过既有 Action Runtime 完成计划中的有限动作 → 读取真实终态/后置条件。优先选择 Minecraft chat 入口目前缺失的既有规划形状；若可安全准备 VERIFIED 玩家身份，再增加一条跟随目标场景。
2. 用户未确认时取消/结束请求：确认前仍是零世界修改；没有任何暗中派发。
3. 观察没有找到目标或目标改变：计划失败/重规划，不能执行旧坐标或继续旧计划。
4. 计划包含 MEDIUM 而运行策略不允许时：阻止并记录 reason code；不准为了测试开启/放宽全局权限。
5. 一个步骤运行失败/路径不可达/取消：任务有明确终态/暂停状态和真实关联，不会启动后续步骤。
6. 普通聊天与 SAFE 查询的对照场景，证明不误建任务或重做动作。

脚本若因真实服务器、登录、客户端或感知层不可用而无法跑，应把该场景标为 `SKIPPED` 并说明具体原因，不能报告 PASS；CI E2E 的 flaky 必须保留第一次结果，只重跑失败 job，不放宽断言/超时/距离阈值。

## §10. 安全与数据库不变量

- `app/integrations/minecraft/agent.py::ACTION_RISK` 不变；当前 19 个原子工具不增加。
- `MinecraftAgentToolsConfig.allow_medium` 默认仍为 `False`。不得为了让资源任务通过而修改 default 或真实 config。
- `trusted_players`、`TurnOrigin` 派生、显式意图门、两道确认门与任务授权语义不变。
- `TaskAuthorization`/`TaskStepAuthorization`、计划哈希/参数指纹、`TaskRuntime`/Action Runtime 状态机不可绕过。
- 既有的取消/暂停/恢复/过期/重启/模糊派发对账保持不变；不宣称 exactly-once；不能自动重发结果未知的世界修改动作。
- 不新增 Minecraft action、不增加中间的 HTTP 直连或 mineflayer 直接调用、不以原子工具名之外的复合指令执行世界动作。
- 默认无新增迁移、无新表、不改历史数据。若实际 diff 触及 `app/database/database.py`，视为越界，除非有先行书面设计证明必要性并重新划定任务（此任务书不授权该变更）。
- 密钥与私有文件不入 Git；本地 smoke fixtures / `.env` / auth 文件 / 运维脚本不得发布。

## §11. 文档/发布交付

必须更新：

- `docs/MINECRAFT_PHASE8C.md`：基线、审计调用链、真实缺口、方案/非目标、实现文件/契约、路由矩阵、测试矩阵、真实 Java 结果、CI、限制与裁决。
- `docs/README.md`：加入 8C 索引行，说明已支持的目标与明确不支持的目标。
- `CHANGELOG.md`：新增 Phase 8C 条目，准确区别新增接线与既有能力，不写夸大宣传。
- `docs/specs/`：归档本任务书的逐字节副本并核对 SHA256。

不发布 `scripts/`、真实配置/数据库、`minecraft_runtime/auth.json` 或角色 Bible 私有文件。使用既有的 `sync_github.py`/镜像流程，不从工作区直接整体 push。

## §12. 验收门禁与完成定义

### 必须执行

1. Phase 8C 新增/相关定向 pytest。
2. `pytest tests -q` 全量测试。
3. `ruff check .`。
4. `ruff format --check .`。
5. `mypy app`。
6. WebUI typecheck/Vitest/build/E2E（如果改 WebUI/API，必须本地跑；否则也要核对 GitHub CI job 实际结果）。
7. `minecraft_runtime` npm tests/E2E（按项目当前流程执行）。
8. SQLite 迁移无变化核查（如无 schema 改动，明确报告当前最高版本、无新增迁移/表；不要只写“应该没改”）。
9. 真实 Java smoke，逐条 PASS/FAIL/SKIPPED，并保留真实 log/artifact 的本地路径（不得发布敏感数据）。
10. Git diff 安全审计：确认风险表/门/授权/任务生命周期未被意外改写；检查改动文件凭据与私有内容扫描。
11. CI 所有必需 job 最终 success；如首次 E2E 失败，只重跑失败 job并如实记录两个 attempt。

### DoD

- Minecraft chat 中 `resource`、`follow`、`explore` 三类已存在的规划形状都经过审计：对可满足前置的形状，入口接入现有有界规划/任务链；对不可满足前置的形状，输出正确的 fail-closed 结果。至少一条多步目标从真实 Minecraft chat 入口走到授权后的既有 TaskRuntime/Action Runtime 路径，且动作只经现有工具/授权链。
- 普通聊天/只读查询不被任务侧误接管；一条消息不能双重派发。
- 计划确认、步骤授权、冻结参数、工具注册/schema 校验、结果对账和终态检查全部可通过自动化证据核验。
- 目标不足/不支持/策略阻止/观察失败时如实失败或澄清，不自动放宽、不猜参数。
- 未新增动作/工具/表/迁移，未变更风险表、确认门、身份授权边界或执行生命周期安全不变量。
- 完成全量测试与 CI，并提供至少一个实际可执行的真实服务器路径（或诚实记录无法执行的具体阻塞）。
- 文档、镜像与 `origin/main` 一致；工作区干净；私有文件未发布。

## §13. 最终报告格式（必须逐项回答）

1. Base SHA / implementation SHA / final SHA / commits 数（实际可获取时）/ worktree / mirror / `origin/main`。
2. 开工审计的当前调用链与引用源码/函数，区分“已具备”与“真实缺口”。
3. 支持目标形状矩阵：入口、检测器、planner、SAFE observations、校验、confirm owner/session、TaskRuntime 执行入口。
4. 本阶段实际修复的根因及复现证据；若某项审计后无需改代码，解释为何现有实现已满足，不制造虚假缺陷。
5. 文件逐一 diff 理由；明确哪些既有 planner/runtime 复用，哪些接线改变了。
6. 行为矩阵：闲聊、SAFE 查询、支持的多步目标、信息不足、unsupported、policy blocked、目标变化、busy、确认/取消/他人控制。
7. 计划上限、参数冻结/引用、授权与动作关联保证；安全不变量 diff 核对。
8. 新增测试的精确数量、定向/全量 pytest 数字与退出码。
9. ruff/format/mypy/WebUI/npm/CI 的逐项实际结果、run ID、attempt；失败重跑如实记录。
10. 真 Java server smoke 各场景 PASS/FAIL/SKIPPED，实际结果关联（task_id/step_id/action_id/runtime instance 的脱敏摘要）。
11. 数据库 schema/迁移与数据保护结果、隐私/凭据扫描结果。
12. 文档/changelog/spec归档与镜像状态。
13. 已知限制——只列有源码或真实运行证据的事项。
14. 裁决：PASS / PARTIAL / FAIL，并按 §12 DoD 逐项给证据。PASS 后才能开始 8D；不得把 8D 的树木/批量资源采集夹带到本提交。

---

## 本阶段执行原则（一句话）

**把已有的有限规划能力从正确入口接入，并用真实证据证明授权后的原子步骤按序完成；不造第二套规划/执行系统，也不把“模型说可以”当成真实世界已完成。**
