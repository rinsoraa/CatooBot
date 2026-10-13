# CatooBot Minecraft Phase 8A — Natural-Language Action Routing & Traceability

## 自然语言行动路由与决策可观测性

**任务类型：** 现有路径修复 + 结构化诊断 + 单元/集成测试。

**实现角色：** DSH 负责审查、实现和本地取证；所有结论必须由源码、测试或运行日志支撑。

**验收原则：** 只修复已确认的路由缺口；不把尚未复现的理论风险升级成阻塞项；不扩大 Minecraft 动作能力或授权范围。

**目标：** 让自然语言中的简单 Minecraft 意图能可靠进入现有工具/任务路径，并让每次分流结果及其失败原因都可观察、可测试、可解释。

## 1. 开工前必做：确认代码基线与工作区

1. 读取仓库实际位置、当前分支、`git status --short`、`git log -5 --oneline` 和当前 `HEAD`；不要假定基线仍是本任务书编写时的 SHA。
2. 以本地权威开发目录为工作区：`E:\WorkSpace ZCode\CatooBot`。不要把 `E:\WorkSpace ZCode\CatooBot_github` 当作日常实现目录；按项目既有流程在完成后同步干净发布镜像。不要提交密钥、`.env`、真实服务器鉴权文件、数据库/用户数据或本地脚本。
3. 先查看现有设计和验收证据，至少包括：
   - `docs/MINECRAFT_PHASE7F5.3.md`
   - `docs/MINECRAFT_PHASE7F5.3_FOLLOWUP.md`
   - `docs/MINECRAFT_PHASE7F5.3_FOLLOWUP2.md`
   - `docs/MINECRAFT_PHASE7E.md`
   - `app/integrations/minecraft/chat_bridge.py`
   - `app/character/runtime.py`
   - `app/tools/router.py`
   - `app/tools/runtime.py`
   - `app/tools/registry.py`
   - `app/integrations/minecraft/agent.py`
   - `app/tasks/turn.py`、`app/tasks/intent.py`、相关 TaskRuntime 路由
   - `app/core/bot.py` 装配路径与 `config/config.example.yaml`
   - `tests/test_minecraft_chat_bridge.py`、工具/Agent/任务相关测试。
4. 使用当前源码给出真实调用链，不要只复述文档：

```text
Minecraft chat event
  -> MinecraftChatBridge
  -> TaskTurnHandler / existing task-intent path
  -> CharacterRuntime.respond
  -> CharacterRuntime._try_agent (when eligible/enabled)
  -> ToolRuntime / ToolOrchestrator / ToolRouter (when enabled)
  -> selected builtin Minecraft tool
  -> MinecraftAgentBridge.invoke / Policy / Confirmation / TaskStepAuthorization
  -> MinecraftService
  -> existing Minecraft Action Runtime
  -> structured result / action event
```

按实际源码修正以上图，不得为了让图好看而假定某个阶段一定经过。

## 2. 当前产品意图与阶段定位

罐头不是一个只会聊天的机器人，也不是一个无限制自动化挖矿脚本。她应该在真实游戏聊天中理解用户表达，利用世界观察与记忆确定目标，判断应聊天、询问、调用已有原子工具还是启动受控多步骤任务；执行层继续负责风险、授权、动作和真实结果。

本阶段只解决“请求如何被路由以及如何看到路由结果”。它不负责实现树木采伐/批量资源收集/玩家物品交付，也不负责训练模型。

## 3. 开发前的诊断问题：用证据判断，不允许先入为主

重点追踪下面每一种可能的“只聊天/没有动作”原因：

- 游戏内聊天没有到达 `MinecraftChatBridge`，或触发了自己消息过滤/忙碌上限。
- `TaskTurnHandler` 的控制命令或资源任务检测器截获了请求，导致请求未走普通工具回合。
- `CharacterRuntime._try_agent` 把请求归到普通聊天，或多步目标的计划入口未触发。
- `CharacterRuntime._generate` 因 `tools` runtime 未启用而直接调用普通 `AIEngine.chat()`。
- 工具已关闭、Minecraft 连接层未启用、Minecraft 离线或策略开关关闭。
- ToolRouter 候选检索没有选出需要的 Minecraft 工具；候选工具/描述/关键词不足。
- 模型未选择工具、返回格式无法解析、参数不完整或目标需要澄清。
- Minecraft Policy/身份可信校验/确认门/TaskStepAuthorization 拒绝请求。
- 工具被调用但 Service/Action Runtime 失败、动作仍为 `RUNNING`，或最终世界结果未验证。

**严禁将这些情况统一记录为“LLM 没听懂”。** 对照实际配置和源码逐层定位。仓库里的 `config/config.example.yaml` 不能代替用户当前的运行时有效配置；若配置由 WebUI/overrides 覆盖，必须指出真实的读取优先级与可核验证据。

## 4. 路由行为契约

将当前分支路径清晰地收敛成以下可验证行为。可按最小改动复用现有组件，不要求一定新增一个名为 Router 的类。

### 4.1 普通聊天

普通聊天、情绪回应、闲聊或用户只是提到 Minecraft 但没有提出动作请求时，不得调用 Minecraft 动作工具、不创建任务、不发送额外游戏聊天。

### 4.2 明确的单步 Minecraft 请求

例如：

- “看看附近有什么方块/玩家。”
- “看看我的背包。”
- “看向这个已经观察到的位置。”
- “跟着我。” / “走到我旁边。”（目标必须从可靠上下文/当前世界观察解析，不得编造坐标或从昵称猜身份。）
- “停下来。”

当请求明确、工具已启用、世界在线且策略放行时，应当使用现有 SAFE/LOW 工具链。工具被禁用或请求不满足策略时，不能偷偷绕过开关；必须拒绝或退回安全解释，并留下可诊断原因。

### 4.3 多步骤目标

请求需要多个有序动作、异步等待或后置验证时，继续使用当前 Task/Agent/TaskRuntime/AgentPlan/Proposal 路径。不能为了让工具“立刻执行”而直接把一个复合目标拆成无约束的连续原子工具调用。

本阶段不重写 TaskRuntime，不扩大支持的多步骤目标类别。若发现既有检测器漏掉某种说法，只能先加入与已有能力一致、可测试的最小识别/路由修正；树木采伐、物品交付等新玩法留给后续阶段。

### 4.4 参数不足或意图歧义

在目标玩家不明、坐标未知、目标方块不确定、物品数量/对象不清楚时，应使用 SAFE 观察工具获取真实信息，或向用户澄清。不得生成猜测的坐标、玩家身份、方块名或物品数量后执行动作。

### 4.5 控制与终止

已有任务的确认、暂停、继续、取消，以及 Minecraft `stop` 的当前语义不变；一个用户消息不得在不同路由分支重复处理成两个 USER 回合或重复创建两个动作。`stop` 不可被普通聊天分支吞掉，也不可因为“当前忙碌”而变成无法停止。

### 4.6 工具与任务的路由优先级

先复用现有可确认的 Task 控制/任务创建和 Agent 路径，然后让普通回合使用适用的现有 Tool Router。只有当审计证明该顺序导致真实的错误优先级时才调整；任何调整都要防止同一请求同时启动 Task 和直接工具动作。

**不要额外增加一次模型调用作为默认分类器**，除非基线证据证明现有路由无法完成需求且 DSH 能说明新增延迟、token 成本、失败回退与验收收益。优先修复已有调用链和提示/候选检索，避免产生第二个互相矛盾的意图判断器。

## 5. 结构化路由可观测性

为每条游戏内用户回合建立可关联、字段明确、可脱敏的路由证据。优先复用现有 `ToolTrace`、Task 记录、Minecraft action ID、日志字段和只读诊断接口，不建新的持久化体系。除非仓库证据表明确有必要，本阶段不新增数据库表/迁移。

最小诊断字段建议如下（以现有类型做兼容设计，不要求机械照抄字段名）：

- `route_id` 或能稳定关联到本回合的一次性 ID；`session_id` 可使用既有会话标识，但不能把密钥或不必要的个人信息写日志。
- `turn_origin`；平台/来源类别；不要把玩家可控文本作为授权事实。
- `route_target`：`CHAT_ONLY`、`MINECRAFT_TOOL`、`TASK`、`CLARIFY`、`BLOCKED` 或等价的有限枚举。
- `intent_reason_code` / `fallback_reason`：使用稳定、简短的原因码，不保存完整模型思维链。
- 工具候选名称及相关度分数（如已有）；最终选中的工具名；工具调用是否实际发生。
- `policy_outcome` / 稳定拒绝码（如能够安全取得）；不要记录授权凭据、API Key 或完整确认 token。
- `task_id`、`action_id` 和动作状态（若产生）。
- 阶段耗时/结束状态及失败类别。

### 5.1 隐私与日志要求

默认不持久化完整游戏聊天原文、完整 Prompt、模型隐藏思维链、API Key、Token、确认凭据、完整原始上下文或整个背包数据。可以使用消息长度、脱敏摘要或不可逆的关联 ID 进行关联。严禁为了诊断把敏感信息写入日志。

### 5.2 状态必须可区分

至少可以辨认：

`chat_only`、`task_handled`、`tool_runtime_disabled`、`minecraft_disabled`、`minecraft_offline`、`no_tool_candidates`、`model_no_tool_call`、`invalid_tool_decision`、`policy_denied`、`confirmation_required`、`task_authorization_denied`、`tool_failed`、`action_running`、`action_succeeded`、`action_failed`、`result_unverified`。

可以映射到现有稳定原因码，但不能把“未调用”“被拒绝”“动作进行中”和“验证成功”混为一谈。不要宣称工具执行成功就代表最终游戏目标成功。

### 5.3 展示范围

第一选择是结构化日志和现有 WebUI/诊断 API 已支持的 trace；不要为满足“可观测”而顺便重写整个 WebUI。如果需要 UI 改动，必须保持只读、不显示机密、不引入额外的执行按钮。若没有足够的现有 UI 接缝，Phase 8A 可先以结构化日志 + 单元测试 + 可复现报告交付，并把 WebUI 展示列为有依据的后续建议，而不是做大规模 UI 重构。

## 6. 安全与兼容硬约束

以下各项必须原样保留：

1. `ACTION_RISK` 权威风险表不变；不增加任何 Minecraft 工具或 Action Runtime action。
2. `allow_medium=false` 的默认配置不变；本阶段不要求用户为了通过测试而开启中风险。
3. SAFE/LOW/MEDIUM 的既有策略、显式用户意图、游戏内可信玩家检查、确认门及一次性语义不变。
4. 保留 TaskAuthorization、TaskStepAuthorization、参数指纹、冻结计划及 Policy；不能由 LLM 文本、`core_friend` 标签、记忆或身份关联直接获得授权。
5. `TurnOrigin` 必须继续从可靠调用来源派生。`BACKGROUND`/`INITIATIVE`/`SYSTEM` 不能被伪装为 USER。
6. 所有实际游戏动作继续经过 `MinecraftAgentBridge` → `MinecraftService` → 现有 Minecraft Action Runtime。禁止直接调用 Mineflayer/Pathfinder/Bridge HTTP 绕开现有链路。
7. 保留工具调用数、执行时间、距离、数量、TTL 与并发限制。不允许无限重试或自动连续调用。
8. 保留“罐头的发言不能进入自己的 LLM 回合”的过滤机制。
9. 不修改历史数据库，不加新迁移、新表，不删除或重写技能/记忆/任务数据。
10. 不存储思维链；只保存结构化决策与事件证据。

## 7. 实现策略与允许改动范围

DSH 应先写出一份简短的基线结论（调用链、实际断点、配置控制点、现有 trace 能力），再选择最小修复。不要先大规模移动代码或统一重命名整个工具系统。

可能涉及但不保证需要修改的文件：

- `app/integrations/minecraft/chat_bridge.py`
- `app/character/runtime.py`
- `app/tools/router.py`
- `app/tools/registry.py`
- `app/tools/runtime.py`
- `app/tools/executor.py` / `app/tools/models.py`
- `app/integrations/minecraft/agent.py`
- `app/tasks/turn.py` / `app/tasks/intent.py`
- 已有配置模型或 WebUI 只读 trace 接缝
- 对应的 `tests/` 测试文件、`CHANGELOG.md`、`docs/README.md` 与新增 Phase 8A 阶段文档。

这些是候选检查点，不是要求全改。若实际缺陷只需要修改一处提示词、工具关键词或一个路由条件，就保持改动范围小。

## 8. 必须新增/修复的测试

测试必须确定性、隔离、可重复，不依赖真实 LLM，也不允许使用 `sleep` 伪造并发正确性；使用 fake AI responses、fake service、事件门闩或现有测试基建。

### 8.1 正向路由

- 普通闲聊不会调用 Minecraft 工具、不创建任务、不发送游戏聊天。
- 明确查询世界/背包的请求会将适当的 SAFE 工具列为候选；测试模型选择工具后能走过既有工具路径。
- 对已知可见玩家发出的明确“跟随/走到身边”请求，在开关和可信门均符合条件的情况下，可路由到已有对应工具；没有目标事实时不虚构玩家名/坐标。
- “停下/停止当前任务”只触发相应控制路径，不会同时启动新动作。
- 需要多步骤的、当前已支持的资源任务继续进入既有 TaskRuntime/TaskAuthorization 路径，不会被截断成一次无约束原子工具调用。

### 8.2 负向/歧义路由

- `tools.enabled=false` 时不得悄悄执行工具；诊断结果能指出运行时工具总开关关闭，而不是笼统归为 LLM 不理解。
- Minecraft 未启用/离线、对应工具禁用、风险开关关闭时不得调用真实动作。
- 未知玩家、缺少目标坐标、含糊数量或不支持的复合目标会澄清/拒绝，不猜参数。
- 不可信游戏玩家提出 LOW 动作请求必须继续被原策略拒绝；模型请求或用户文本不能把其加入可信名单。
- MEDIUM 调用仍需要原有确认；不同用户/会话/参数的确认不能复用。
- `BACKGROUND`、`INITIATIVE`、`SYSTEM` 回合不能伪造 USER 行为意图。
- 用户文本试图要求忽略规则、直接执行或伪造授权时，执行策略不受影响。

### 8.3 追踪与回退

- 每个被测试的请求最终留下唯一或明确可关联的路由记录。
- 日志/trace 可区分 no candidates、model did not call、policy denied、configuration disabled、tool failure、action running 与 action succeeded。
- 不记录密钥、授权 token、完整隐藏思维链；日志避免泄露完整的聊天原文。
- 模型输出 JSON 无效、工具调用参数 schema 无效、模型/服务超时或抛异常时，系统能按既有降级行为安全返回。
- 已有 action ID 和 task ID 能在相关记录间串联；但不为得到“成功”而虚构尚未到达的动作终态。

## 9. 运行与回归门禁

在工作区可用的情况下执行项目现有质量门禁，并如实记录实际命令、退出码与结果：

1. 针对新测试的快速回归。
2. 全量 Python tests（`pytest`，按项目 uv/环境的现有方式）。
3. Ruff lint、format check、mypy。
4. WebUI typecheck、Vitest、build 与已提交产物一致性检查（若本次改动影响 WebUI）。
5. Minecraft runtime 的 npm tests/E2E（若项目现有 CI 流程要求）。
6. 仅当真实 Java 测试环境确实可用时执行针对性的真实服务器 smoke；否则标记 `SKIPPED` 并解释，不得编造 PASS。

若 Minecraft runtime E2E 失败属于仓库已有的环境敏感问题：只重跑失败 job 并记录证据。禁止仅为通过测试而调整断言、超时、成功距离、动作预算或风险阈值。不要把一次重跑成功描述成原始故障已彻底解决。

## 10. 文档与交付清单

- 新增 `docs/MINECRAFT_PHASE8A.md`，记录代码基线、原始问题证据、架构调用链、实现点、行为契约、测试矩阵、门禁记录、已知限制及明确未完成事项。
- 更新 `docs/README.md` 阶段目录。
- 更新 `CHANGELOG.md`，准确写出实际改动，不宣称未实现的玩家行为。
- 如变更了面向开发者的路由/trace 契约，补充测试或短设计说明。
- 提供提交 SHA、干净工作区/镜像一致性检查结果、全量测试数字与真实服务器测试的 PASS/FAIL/SKIPPED 分项。
- 明确记录 `ACTION_RISK`、默认风险开关、确认门、任务授权、数据库迁移数量均未因本阶段而改变的证据。

## 11. 完成定义（Definition of Done）

Phase 8A 只有在下面条件全部满足时才能验收：

- 已从实际源码定位“只聊天/不行动”的具体路由原因，并提供至少一条可复现的测试或明确诊断证据。
- 现有支持的普通聊天、单步工具、多步骤任务、澄清、策略拒绝之间的路由契约有对应自动化测试。
- 诊断能区分配置关闭、无候选、模型未选择、策略拒绝、调用失败、动作运行中与真实成功；不依赖思维链或敏感原文。
- 没有引入新的 Minecraft 原子能力，没有扩大风险权限，没有绕过既有授权或生命周期链。
- 所有适用 CI/本地门禁通过；不能执行的真实服务器门禁明确写为 `SKIPPED`。
- 文档与提交反映真实完成范围。

如果发现额外理论上的并发疑虑但尚不能重现，不得把 Phase 8A 变成无期限的全仓生命周期复审。新增阻塞项必须提供最小复现、失败测试或可观察的真实错误。只要本阶段 DoD 满足，就验收并进入 Phase 8B。

## 12. DSH 最终报告格式

```text
CatooBot Minecraft Phase 8A — Final Report

1. Base SHA / final SHA:
2. Original symptom and reproducible evidence:
3. Actual call chain (source paths + functions):
4. Root cause(s), separated from hypotheses:
5. Changes made (file by file):
6. Route behavior matrix:
7. Safety invariants checked:
   - ACTION_RISK unchanged?
   - allow_medium default unchanged?
   - confirmation gates unchanged?
   - TaskAuthorization/Policy/AgentBridge/ActionRuntime preserved?
   - DB migrations/tables unchanged?
8. Tests and exact counts/exit codes:
9. Real Java server smoke: PASS / FAIL / SKIPPED per scenario:
10. Remaining known limitations (only evidenced items):
11. Docs/changelog updated:
12. Worktree and release-mirror status:
13. Verdict: PASS / PARTIAL / BLOCKED, with reasons:
```

**最终范围声明：** Phase 8A 只证明自然语言路由及诊断可用，不声称完成批量采集、完整简单生存玩法、玩家物品交付或模型训练。后续阶段按 8B–8H 单独交付。