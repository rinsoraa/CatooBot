# CatooBot Minecraft Phase 8E Follow-up 2 — 非肯定木材路由的角色回合能力隔离与零副作用闭环

> **任务类型**：安全路由收口 + 入口级验证（实现与验证并行，先复现再修复）  
> **基线**：`b4a05de4f8052445dda294600a1c2514c28a5cee`（Phase 8E Follow-up 最终证据提交）  
> **目标裁决**：把当前“旧 `plan_resource_task()` 不再被触发”的局部保证，提升为“非肯定木材分类不会通过角色回合中的 Agent/Tool 路径创建任务或执行 Minecraft 动作”的可验证保证。  
> **产品边界**：不扩展木材玩法；不改变工具权限、风险分级、确认门或任务执行语义；只增加按单回合生效的能力隔离及相应证据。

---

## §1. 背景与问题定义

Phase 8E Follow-up 已修复一处真实的旧路径回落缺陷：`QUERY` / `NARRATION` 不再通过 `TaskTurnHandler._create()` 落到 `plan_resource_task()`；`OTHER`、歧义及解释器失败进入安全澄清；控制命令仍由确定性入口接管。相关实现位于：

- `app/integrations/minecraft/chat_bridge.py`：`MinecraftChatBridge._reply()`、`_task_reply()`、`_wood_route()`。
- `app/tasks/wood_intent.py`：`WoodIntentInterpreter` 与结构化结果。
- `app/character/runtime.py`：`CharacterRuntime.respond()` / `_generate()`。
- `app/agent/runtime.py`：独立的 `AgentRuntime.run()` 多步骤任务链。
- `app/tools/runtime.py` 及相关编排：`ToolOrchestrator.run()` 工具调用链。

**仍需解决的架构边界**：`_WoodRoute.NORMAL_CHAT` 目前通过 `_task_reply()` 返回 `None`，随后由 `_reply()` 继续调用普通的 `CharacterRuntime.respond()`。在现有代码中，这个常规角色回合可能调用 `CharacterRuntime._try_agent()`，也可能调用 `ToolOrchestrator.run()`。因此，仅证明 `TaskTurnHandler` 和旧 `plan_resource_task()` 未被调用，并不足以证明该回合不会重新启动 Agent 任务或执行 Minecraft 工具。

该差距不表示已经发生了真实 Provider 误分类或世界动作；当前缺少的是对**能力边界的结构性约束和反例测试**。本阶段不得以“真实模型大概不会这样返回”替代安全控制。

### 本阶段采用的策略

对**木材消息**按解释器结果作如下处理：

| 分类 / 情况 | 本阶段的目标行为 | 角色回合能力 |
|---|---|---|
| `WOOD_COLLECTION_REQUEST + POSITIVE`，无需澄清，确定性参数校验通过 | 只进入现有木材专用计划链；回复计划预览或既有失败结果 | 不进入通用角色工具链；计划/执行继续走既有受控链 |
| `NEGATED_REQUEST + NEGATIVE` | 明确回复未执行，结束本回合路由 | 不调用普通角色回合、Agent 或 Tool 编排 |
| `AMBIGUOUS_INTENT`、`MIXED`、`needs_clarification=true` | 安全澄清，结束路由 | 不调用普通角色回合、Agent 或 Tool 编排 |
| `OTHER`、未知/不一致结果、解释器缺失/异常/超时/非法 JSON/schema | 安全澄清，结束路由 | 不调用普通角色回合、Agent 或 Tool 编排 |
| `QUERY` / `NARRATION` | 允许角色以普通对话方式回应，但使用**单回合 CHAT_ONLY 模式** | 禁止 `AgentRuntime`、禁止 `ToolOrchestrator`、禁止工具调用和动作型副作用 |
| 模型标为 `TASK_CONTROL`，但确定性控制检测器不承认 | 安全澄清，结束路由 | 模型标签不能执行控制，不调用工具或 Agent |
| 确定性控制命令 / 既有任务状态查询 / busy | 保留原来的确定性任务入口、状态与权限检查 | 不调用木材解释器；不因本阶段改变原控制流程 |
| 非木材消息 | 保持现有路径和行为 | 本阶段不改变既有 Agent/Tool 能力 |

**重要取舍**：本任务书将 `QUERY` / `NARRATION` 的“普通角色回答”定义为**不可启动 Agent 或工具的受限角色回合**，而不是全能力的普通回合。它可能不能在这一回合内调用 Minecraft 只读工具取得实时世界数据，这是刻意的保守边界。如果未来需要木材查询的实时只读世界观察，应另行设计独立、明确 allowlist 的只读查询路由；不得把当前 `CHAT_ONLY` 偷换成任意工具可用。

---

## §2. 基线审计与禁止假设

实施前在发布镜像仓库执行只读审计，确认工作区干净且 `HEAD == origin/main == b4a05de4f8052445dda294600a1c2514c28a5cee`。若不一致，停止实施并先记录实际基线，不要覆盖用户改动。

检查并在报告中列出以下真实调用链：

1. `MinecraftChatBridge.apply_event()` → `_reply()` → `_task_reply()`。
2. 木材解释器如何返回 `_WoodRouteDecision`，以及 `NORMAL_CHAT` 当前如何继续进入角色回合。
3. `CharacterRuntime.respond()` → `_generate()` → `_try_agent()` → `AgentRuntime.run()` 的任务路径。
4. `CharacterRuntime._generate()` → `ToolOrchestrator.run()` → 工具 runtime / Minecraft Agent Bridge 的路径。
5. `TaskTurnHandler.handle()` / `_create()` → `plan_resource_task()` 的旧单目标路径。
6. 确认 `TurnOrigin.USER`、`origin_metadata`、可信玩家、Policy、风险等级、确认门和步骤授权各自负责什么；不得把这些控制语义混为同一个布尔开关。

**禁止假设**：

- 不得认为“不调 `TaskTurnHandler`”就等于“不创建任何任务”。
- 不得认为“`env.tools.executor.traces == []`”就单独足以证明 AgentRuntime 没创建任务；必须检查 Agent 路径的调用计数/任务记录。
- 不得把 `TurnOrigin.BACKGROUND` 当作 `CHAT_ONLY` 的替代品。回合来源是可信事实/既有策略输入，不是通用的能力隔离标志；将 USER 改成 BACKGROUND 会歪曲来源语义，而且 SAFE 工具仍可能被调用。
- 不得临时全局关闭 `self.bot.tools.enabled`、修改共享 `AgentRuntime.config.enabled` 或修改全局风险配置。Minecraft 聊天并发时，这些共享状态切换可能污染其他回合。
- 不得只使用永远返回 `handled=False` 的 `TaskTurnHandler` 替身来证明完整路由安全。

---

## §3. 目标与非目标

### 3.1 必须达到的目标

1. 给木材解释器的非肯定分类增加**从聊天入口一直传递到角色生成层**的显式路由/回合模式。
2. `QUERY` / `NARRATION` 可以生成自然语言回复，但本回合不能调用 Agent 或 Tool 编排；不得有 Minecraft 工具调用、动作 `action_id` 或任何任务创建。
3. `OTHER`、否定、歧义、需澄清和解释器失败必须直接澄清并终止本轮任务/工具路由，不进入 `CharacterRuntime` 的生成链。
4. 只有解释器输出为肯定采集意图、极性一致、无需澄清，且 `parse_wood_request()`、支持树种矩阵与模型/确定性参数一致性校验通过时，才可以进入现有木材专用计划链。
5. 旧 `TaskTurnHandler._create()` / `plan_resource_task()`、AgentRuntime 任务创建以及 ToolRuntime/Minecraft 动作链均由入口级测试覆盖。
6. `CHAT_ONLY` 必须是**单回合、显式、fail-closed、无共享全局副作用**的能力约束。
7. 对相关路由的异常、测试日志与可达日志点做隐私审查，避免在本阶段路径中输出完整聊天原文、异常正文、Provider 响应或 prompt。
8. 非木材聊天、既有确定性控制命令、活动任务 busy/status、正常肯定木材计划和现有确认链保持回归通过。

### 3.2 明确不做

- 不新建 Minecraft 工具、Action Runtime 动作、风险级别、权限、确认方式、任务类型、数据库表或迁移。
- 不改变 `ACTION_RISK`、`allow_medium=False` 默认值或真实用户配置。
- 不改变 19 个既有工具及其 schema；不扩大现有 Agent/Tool 的全局权限。
- 不改变 TaskRuntime 生命周期、状态机、总步数/动作步数预算、`TaskAuthorization` / `TaskStepAuthorization`。
- 不改变木材计划的冻结目标坐标、`expected_block`、掉落物/拾取引用、同组树干证明或最终库存增量验证。
- 不扩展英文/多语言意图识别，不以真实 Provider 的自然语言准确率取代确定性安全控制。
- 不把本阶段扩展成广泛重构 CharacterRuntime、AgentRuntime 或通用对话工具政策；实现范围限于单回合能力模式接缝及必要测试。
- 不用删除测试、缩弱断言、加 skip、放宽超时/阈值或修改 CI 语义换取绿灯。

---

## §4. 推荐的实现契约

实现者可以在保持等价约束的前提下选择最小设计，但必须先给出具体调用时序，并证明模式不会被其他并发回合读取或覆盖。

### 4.1 使用显式 dispatch 结果，不再用 `None` 表达回合模式

目前 `_WoodRouteDecision` 已在木材分类层表达六种路由。需要继续将决定传递到 `_reply()` / `CharacterRuntime.respond()`，使以下两件事不再混淆：

- “这一条消息由桥接层处理完了”；
- “需要普通角色回复，但只能生成文本，不能启动 Agent/Tool”。

可以采用专用 dataclass / enum，例如 `ChatDispatchDecision` / `CharacterTurnMode`，或等价的显式机制。接口名称不作硬性规定，语义必须可读、可测。

最低限度应明确区分：

- `TASK_REPLY`：确定性任务/木材计划分支已生成回复，结束路由；
- `CLARIFY_REPLY` / `WOOD_HANDLED`：澄清/否定/失败关闭回复，结束路由；
- `CHAT_ONLY`：只允许角色自然语言回答，Agent 与工具调度都关闭；
- `NORMAL`：非木材消息沿既有完整能力路径处理；
- `DELEGATE_CONTROL`：仅供确定性识别的控制命令、状态查询或既有 busy 逻辑交给旧任务入口。

不要在一个 `str | None` 返回值中暗藏这些不同语义。若为保持兼容而保留原函数，至少需要增加显式路由对象/参数，并由调用方强制处理。

### 4.2 CHAT_ONLY 必须在能力调用之前生效

对于 `QUERY` / `NARRATION`：

- 可调用角色回复生成，但 `CharacterRuntime._generate()` 必须在进入 Agent 与 Tool 分支**之前**判断当前回合模式。
- 在 `CHAT_ONLY` 模式下，不得调用 `AgentRuntime.handle_control()` / `AgentRuntime.run()` / `_try_agent()`，不得调用 `ToolOrchestrator.run()`，不得将本轮导向 Agent 计划或工具执行兜底。
- 回复仍使用实际玩家消息、正常会话与现有角色生成配置；不得伪造玩家身份、改变 `TurnOrigin.USER`、改写 `origin_metadata` 或通过切换可信名单规避策略。
- 此模式仅作为当前一次 `respond()` 调用的参数/上下文，不能写回共享配置；异常退出后也不得影响后续非木材回合。
- 模式检查要早于所有可触发 Agent/Tool 调度的分支。只跳过某一个分支不算通过。
- 如果角色模型在 CHAT_ONLY 回合仍返回看似工具调用的 JSON 文本，该文本也不得被解释/重新路由为工具请求。必要时以安全普通回复替换不符合纯文本回合契约的输出，不能再回调工具路径。
- 记录符合现有 RouteTrace 约定的 `CHAT_ONLY` 路由证据；只记录安全字段，例如 `reason_code`、`text_chars`、耗时和工具调用计数，不写消息正文。

**不要**通过在调用前后临时修改 `tools.enabled`、Agent 配置或全局对象状态来实现。此类全局开关在并发回合间不安全，而且会改变无关聊天行为。

### 4.3 对不同木材类别的确定性行为

| `_WoodRoute` / 结果 | 必须行为 | 不可达的能力 |
|---|---|---|
| `WOOD_POSITIVE_REQUEST` | `_wood_positive_reply()` → `parse_wood_request()` → 支持树种矩阵与参数一致性校验 → 现有 `plan_goal_from_user(shape_hint="wood")` | 不得经过 `TaskTurnHandler._create()` 的通用资源模板，不得直接执行动作 |
| `HANDLED` / 明确否定 | 返回确定性的“不执行”回复 | CharacterRuntime、AgentRuntime、ToolOrchestrator、任务创建 |
| `SAFE_CLARIFY` | 返回安全澄清/失败文案并结束路由 | 普通角色工具回合、Agent、任何计划/动作路径 |
| `NORMAL_CHAT`（只用于 `QUERY` / `NARRATION`） | 调用角色自然语言回答，传入单回合 `CHAT_ONLY` 模式 | Agent、ToolOrchestrator、旧资源模板、任务创建和 Minecraft 动作 |
| `DELEGATE_CONTROL` | 调用原 `TaskTurnHandler.handle()`，沿用状态/身份/权限校验 | 木材解释器不得代行控制，不得同时新建木材任务 |
| 非木材 | 原有流程原样运行 | 不得额外调用木材解释器或被误置为 CHAT_ONLY |

`OTHER` 与任何无法识别/不一致结果采用当前 Follow-up 已选定的 `SAFE_CLARIFY`，不得将其当作 `NORMAL_CHAT` 进而开放全能力角色回合。

### 4.4 正向木材计划失败不能回落

- 计划服务不存在、抛异常、参数冲突、目标不支持、观察不充分或 Policy 阻断，都只能返回相应明确结果/澄清。
- 不得在木材专用计划路径返回失败后，继续将相同原文交给 `TaskTurnHandler.handle()`、`_plan_reply()`、AgentRuntime 或 ToolOrchestrator 作为兜底。
- 肯定木材计划仍使用已批准的规划期 Policy 与双确认机制。确认前必须为零 Minecraft 动作和零动作 `action_id`。
- 对 `allow_medium=False` 的阻断，必须保持现有规划期语义；不允许为使测试通过而在测试或运行时打开全局开关。

---

## §5. 日志与隐私审查

本轮前一个 Follow-up 已将若干异常正文日志改为异常类型名。本任务仍须审计从木材聊天进入受限角色回合这条路径中实际可达的日志点。

### 5.1 必须满足

- 不记录完整用户聊天文本、Prompt、模型原始响应、Provider 响应正文、密钥或身份绑定敏感数据。
- 不使用 `log.exception()` 输出可能包含 Provider 请求/响应文本的 traceback，除非能证明异常链不带受禁数据；本阶段原则上使用安全结构化错误记录。
- 不把 `str(exc)` / `repr(exc)` 作为日志字段。优先只记录异常类别名和内部稳定原因码。
- 路由日志可记录：阶段/组件、稳定 `reason_code`、`text_chars`、耗时、模式、工具调用计数、错误类型及 schema 字段路径/错误类型摘要。
- 检查现有聊天入口日志是否仍然打印 `message` 的截断文本。即使只记录前 40/60/80 字符，对于短消息也可能等于完整原文；若本轮契约宣称不写原文，应改为长度/稳定分类字段，或明确隔离并记录为不在本阶段承诺内。不得一边宣称“日志无原文”一边保留此类原文日志。

### 5.2 必需的隐私测试

新增或扩展 `caplog` 测试，使用互不相同的敏感标记注入：

1. Provider 抛出带标记的 `RuntimeError`；
2. CHAT_ONLY 的普通角色生成阶段发生错误；
3. 返回疑似工具调用 JSON / 非纯文本输出；
4. 木材路由结果/上下文构造失败；
5. 原始用户消息含敏感标记，检查本阶段可达日志不包含原文标记。

断言应验证：标记不出现在日志中；日志包含允许的错误类型/稳定原因码；播放器/用户收到的是安全、短小、普通的回复或澄清，而不是内部异常正文、原始 Prompt 或假工具调用 payload。

---

## §6. 必需的测试设计（先写测试，再实现）

所有测试使用脚本化 Provider / 固定解释器输出，不调用真实模型或真实 API。入口级路由必须使用项目现有真实装配能力；仅在边界处注入脚本解释器和计数/拒绝执行的测试替身。

### 6.1 双执行面反例测试（阻断门禁）

新增独立测试文件，建议：`tests/test_phase8e_followup2_turn_capability.py`。

使用：

- 真实 `MinecraftChatBridge`（项目 `tests/test_minecraft_chat_bridge.stack`）；
- 真实 `CharacterRuntime` 与 `ToolRuntime`；
- 真实 `TaskTurnHandler` + `TaskRuntime`（可复用 `tests/test_task_turn.py` 的 `make_runtime()` / `observations()`）；
- 对 `app.tasks.turn.plan_resource_task` 安装抛异常 sentinel 并计数；
- 对木材 `AgentPlanService.plan_goal_from_user()` 计数且一旦在非肯定用例中被调用即失败；
- 显式计数 `TaskRuntime.create_task()`；
- 对 `ToolOrchestrator.run()` 增加测试 spy/sentinel，证明 CHAT_ONLY 路径调用次数严格为 0；
- 对 `AgentRuntime.run()`（以及相关 control/dispatch 入口）增加调用计数/哨兵，证明 CHAT_ONLY 路径不触发 Agent；如果测试装配中未默认启用 Agent，必须显式装配一个可调用、enabled 的测试 Agent，不能以“没有 Agent 实例”当成能力隔离证据；
- 底层 Minecraft Service 对所有动作调用可被计数，任何写世界动作调用立即失败。

### 6.2 分类矩阵

至少覆盖下列测试（可以参数化，但每个测试必须报告分类与副作用计数）：

| 编号 | 原始消息示例 | 脚本化解释结果 | 必须结果 |
|---|---|---|---|
| T1 | `帮我砍两根橡木原木带回来` | `OTHER` | 安全澄清；不调用 CharacterRuntime、Agent、工具、计划服务或任务创建 |
| T2 | `去砍两根橡木原木带回来` | `QUERY` | 角色回复以 CHAT_ONLY 生成；Agent/Tool 编排均 0 次 |
| T3 | `帮我砍一根云杉原木` | `NARRATION` | 角色回复以 CHAT_ONLY 生成；Agent/Tool 编排均 0 次 |
| T4 | `附近有什么木头？` | `QUERY` | 普通自然语言回复，但不调用 Minecraft 只读工具、不调用 Agent/Tool；路由日志为 CHAT_ONLY |
| T5 | `我昨天砍了两根竹子` | `NARRATION` | 普通自然语言回复；不启动任务/工具 |
| T6 | 任一肯定采集句 | `AMBIGUOUS_INTENT + MIXED` | 安全澄清；终止普通角色/工具链 |
| T7 | 任一肯定采集句 | `OTHER + needs_clarification=true` | 安全澄清；终止普通角色/工具链 |
| T8 | 任一肯定采集句 | 模型标为 `TASK_CONTROL`，确定性检测器不识别 | 安全澄清；不执行控制或任务 |
| T9 | 任一肯定采集句 | Timeout / RuntimeError / `None` / 非 JSON / schema 失败 | 安全澄清；终止普通角色/Agent/工具链 |
| T10 | `不要砍两根橡木原木带回来` | `NEGATED_REQUEST + NEGATIVE` | 明确不执行；无角色工具链/Agent/计划/任务 |
| T11 | `取消，别砍树了`，含活动任务和无活动任务两种状态 | 不调用解释器 | 继续既有确定性控制入口；状态/权限语义不变 |
| T12 | `今天天气不错呀` | 不进入木材解释器 | 非木材回归：原角色回合模式与工具能力不变 |

对 T2/T3/T4/T5，额外要求 Provider 尝试返回类似 `minecraft_dig` / `minecraft_move_to` 的工具调用 JSON 或结构化输出。无论模型文本是什么，该回合都不能通过 `ToolOrchestrator` 执行它。不能只用一个温和的普通文本响应来证明安全。

### 6.3 逐条无副作用断言

对 T1、T6–T10，断言：

- `TaskTurnHandler.handle()` 是否调用符合路由契约；该类结果不得进入会创建任务的任务处理；
- `plan_resource_task()` sentinel 调用数为 0；
- 木材计划服务调用数为 0；
- `TaskRuntime.create_task()` 调用数为 0；
- `AgentRuntime.run()` 调用数为 0；
- `ToolOrchestrator.run()` 调用数为 0；
- 真实任务存储无新增任务，`runtime.active(session)` 仍为 `None`（或与既有 active task 一致）；
- Minecraft 动作调用数为 0、工具 trace 为空、无动作 `action_id`；
- 原有活动任务状态未被路由层偷偷改变；
- 只允许收到明确拒绝或安全澄清文案。

对 T2–T5，断言：

- 角色回答恰好一回合，`CHAT_ONLY` 模式确实传递到实际生成层；
- `AgentRuntime`/Agent dispatch、`ToolOrchestrator.run()`、`TaskRuntime.create_task()`、`plan_resource_task()` 均为 0；
- Minecraft Service 没有任何动作调用；
- 输出不泄漏内部工具调用 JSON、内部异常或 prompt；
- 路由 trace 能证明本轮使用了受限能力模式。

对 T11/T12，断言：控制命令零解释器调用、任务状态语义没有改变；非木材输入不额外触发解释器，仍走现有能力模式。

### 6.4 肯定请求与策略链回归

仍需运行现有正向木材用例，证明唯一允许的正向类别仍走专用 `plan_goal_from_user(shape_hint="wood")` 路径，并保持：

- `parse_wood_request()` 与支持树种矩阵的确定性校验；模型参数与解析冲突时澄清；
- SAFE 观察、木材目标证明与冻结计划不变；
- `allow_medium=False` 的规划期阻断不变；
- 计划服务不可用、失败或拒绝时，不回退到通用旧资源模板、Agent 或 Tool 调用路径；
- 预览/明确确认前零动作、零动作 `action_id`；确认之后仍走现有 TaskRuntime → 步骤授权 → Action Runtime → 真实库存增量验证。

### 6.5 模式隔离与并发测试

至少加入以下测试：

1. CHAT_ONLY 回合完成后，紧接一条非木材正常消息，后者仍能按原能力使用既有 Agent/工具路由。
2. 一条 CHAT_ONLY 回合执行期间，另一条独立非木材回合不会被误禁用 Agent/Tool。
3. CHAT_ONLY 回合抛异常、超时或取消后，共享 runtime/config/tool enabled 状态逐字不变。
4. 模式没有存储在全局 mutable 属性中，不会在下一条消息继承。
5. 如果当前聊天入口会并行处理事件，测试必须使用事件/屏障或等效确定性同步；不要用 `sleep` 证明并发隔离。

---

## §7 变异验证

至少执行以下临时变异，保存修复前失败证据；变异只在工作树临时应用，最终必须复原并确认无残留：

### M1 — CHAT_ONLY 回落工具编排

临时让 CHAT_ONLY 模式继续调用 `ToolOrchestrator.run()`，或移除早期能力分支。期望：T2/T3/T4/T5 至少有测试失败，工具编排 spy 或写世界动作 sentinel 捕获到违规调用。

### M2 — CHAT_ONLY 回落 AgentRuntime

临时让 CHAT_ONLY 模式继续调用 `_try_agent()` / `AgentRuntime.run()`。期望：相关 Agent 哨兵/任务计数测试失败。

### M3 — 控制命令被木材分支抢截

临时让确定性控制命令先运行解释器。期望：T11 失败（解释器调用计数不再为 0 或原控制状态不符）。

### M4 — 日志重新输出原文或异常正文

临时重新引入 `log.warning("... %s", exc)` 或输出 `message`。期望：相应 `caplog` 测试失败。

**任何要求中的变异未能被测试捕获，视为测试覆盖缺口；不得仅恢复代码后就声称变异验证通过。**

---

## §8. 实施步骤

### Step 0 — 只读审计与工作区基线

- 验证基线、远端、发布镜像与干净工作区；记录 `HEAD`、commit 数、`origin/main`。
- 阅读并记录 §2 所列调用链；确认 `AgentRuntime` 是否在真实 Minecraft 装配下启用，工具开关来自何处。
- 搜索 `chat_bridge.py`、`character/runtime.py`、Agent/Tool 路径中会记录原文、异常正文或 prompt 的日志。
- 不做代码修改。

### Step 1 — 新增测试并复现

- 新建 `tests/test_phase8e_followup2_turn_capability.py` 和必要隐私测试。
- 在真实桥接 + 真实 `CharacterRuntime` + 真实 `ToolRuntime` 的装配中注入脚本分类结果。
- 将 Agent、Tool 编排、旧资源模板、计划服务、TaskRuntime 与 Minecraft Service 设置计数器/sentinel。
- 先运行测试并记录至少一项确实暴露当前缺口的失败；不允许删除/跳过失败用例。

### Step 2 — 设计并实现显式单回合模式

- 在变更说明中写出 dispatch 对象从 `MinecraftChatBridge` 到 `CharacterRuntime.respond()` / `_generate()` 的传递链。
- 选择单回合、显式、不可并发污染的 `CHAT_ONLY` 参数/模式；类型名可不同，但语义须相同。
- 保证 CHAT_ONLY 在任何 Agent/Tool dispatch 发生之前拦截；不能靠共享 config 或 `TurnOrigin` 伪造实现。
- 对非肯定、失败和澄清结果，保留直接安全回复，不经过角色工具链。
- 正向木材仍走 8D 专用计划链，不得在正向规划失败后回退其他路径。

### Step 3 — 日志隐私收口

- 对本阶段可达路径的异常日志和原文日志逐项审计。
- 改为结构化安全字段（错误类型、稳定原因码、长度等）。
- 完成 §5 的敏感标记 `caplog` 测试。

### Step 4 — 回归与变异

- 执行 §6 的所有入口级测试与 §7 的变异测试。
- 执行 Phase 8D / 8E / Phase 8E Follow-up 原有路由、schema、隐私、确认门、策略与非木材回归。
- 若旧断言因新契约而需改写，必须逐条说明为什么原断言表达了旧缺陷，新增更强的目标路由、副作用、能力隔离断言；不得以“适配新代码”为由无证据地删断言。

### Step 5 — 全量门禁与安全审计

按 §9 顺序执行完整命令；记录命令、exit code、精确测试数、skip 数、耗时与失败原因。CI 第一次失败必须保留，再按仓库纪律只重跑失败 job；不得覆盖初次失败证据。

### Step 6 — 发布

- 单一独立实现提交；不得 reset、force-push 或改写 Phase 8D / 8E / 两次 Follow-up 历史。
- 同步发布镜像与远端；确认 `scripts/` 本地脚本仍未被意外追踪。
- 更新 `CHANGELOG.md`、`docs/MINECRAFT_PHASE8E.md`、`docs/README.md` 和本任务书归档。
- 等最终证据提交的 CI 两个 job 全部通过后，才报告最终裁决。

---

## §9. 必需门禁

以下命令名可按仓库既有工具环境适配，但不得省略同等检查：

```powershell
pytest tests/test_phase8e_followup2_turn_capability.py -q
pytest tests/test_phase8e_followup_log_privacy.py -q
pytest tests/test_phase8e_followup_routing_closure.py -q
pytest tests/test_phase8e_wood_intent.py tests/test_phase8e_baseline_repro.py -q
pytest tests/test_phase8d_wood_intent.py tests/test_phase8d_wood_plan.py tests/test_phase8d_wood_routing.py -q
pytest tests -q
ruff check .
ruff format --check .
mypy app
git diff --check
```

还必须完成：

- §6.1–§6.5 的入口、正向、并发与隐私测试；
- §7 中所有变异测试，并记录“变异后失败 → 恢复后通过”；
- 凭据与敏感内容扫描（不得只扫描密钥，也需检查敏感测试标记/原文日志）；
- `git diff --name-only` 对安全保护文件的范围审查；
- 检查数据库 schema / migration 无变化；
- 检查新增测试没有意外 `skip` / `xfail`，没有放宽既有超时、阈值或断言；
- 确认新增能力模式是逐回合而非共享可变全局状态。

### 9.1 数字一致性

- 先记当前全量基线测试数；新增/修改测试后的净增量必须用实际 collected/passed 结果核对，不可直接把理论新增项数当成净增量。
- 分开报告木材专用测试、宽回归、全量 pytest、工具检查和 CI 数字。
- CI 的既有平台 skip 必须与本阶段新增 skip 分开；不得把 `passed + skipped` 简写为全部通过而不披露 skipped。

### 9.2 真机门禁

若本阶段严格限制在路由、每回合能力策略与测试层，不修改动作执行、授权或 Minecraft Runtime，可按范围声明真机动作门禁不适用，但必须明确：

> “本轮没有验证真实 Java/Minecraft 世界中的动作链路；这不是动作链路 PASS 的证据。”

如果实际改动触碰动作调用、执行策略、TaskRuntime、授权、确认或 Action Runtime，则必须重新评估并执行适用的真实 Minecraft 门禁，不能继续沿用“不适用”声明。

---

## §10. 安全不变量（禁止变化）

逐项比较实现前后，并在最终报告中列“未改 / 证据”：

1. `app/integrations/minecraft/agent.py` 的 `ACTION_RISK`、工具映射和既有 Policy。
2. `allow_medium=False` 默认值与真实运行配置。
3. 19 个既有 Minecraft 工具及各自 schema。
4. `TurnOrigin` 派生的明确用户意图、`origin_metadata` 运输层事实和 `trusted_players` 可信玩家规则。
5. 用户确认门、Tool 确认/参数一致性、TaskRuntime 冻结计划确认与步骤授权。
6. TaskRuntime 状态机、恢复、取消/暂停/继续语义、任务步数/动作步数预算。
7. 木材 SAFE 观察、树干同组证明、冻结目标坐标、`expected_block`、掉落物与拾取引用、真实库存增量验证。
8. AgentRuntime 与 TaskRuntime 的既有生命周期及非木材路线。
9. 不新增工具、Action Runtime 动作、授权语义、数据库表、迁移或凭据配置。
10. 不在 CHAT_ONLY 时修改共享的 `bot.tools.enabled`、`agent.config.enabled` 或全局配置；不得存在 `try/finally` 临时关闭全局能力的并发竞态。
11. 普通非木材用户回合在没有 CHAT_ONLY 标记时仍保留既有能力。
12. 控制命令由确定性入口处理，木材解释器不能直接改变活动任务状态。

---

## §11. 最终报告必须回答的问题

报告至少包含以下内容，不可只贴 PASS 汇总表：

1. 最终裁决：PASS / PARTIAL / FAIL；每条 DoD 单独判定。
2. 基线 SHA、实现提交 SHA、最终证据 SHA、提交数量、镜像/`origin/main` 一致性、工作区状态。
3. 修改文件列表与净增删数；解释任何与提交 API 统计看似不同的计数口径。
4. 修复前真实控制流与复现结果；说明此前为什么“跳过旧 TaskTurnHandler”仍不代表工具安全。
5. 新的显式 dispatch / CHAT_ONLY 模式设计及调用链；说明它为什么不会使用共享全局开关。
6. `QUERY` / `NARRATION` 中的 Agent 调用数、ToolOrchestrator 调用数、TaskRuntime 创建数、Minecraft Service 动作数与 action_id 结果。
7. `OTHER`、否定、歧义、失败和非法 schema 的计划/任务/工具零副作用证据。
8. 肯定木材请求、策略阻断、确认前零动作与正向计划链回归证据。
9. 日志隐私测试与敏感标记变异测试结果；说明是否还存在可达路径输出截断原文的日志。
10. §7 四项变异的变异后失败、恢复后通过的原始测试摘要。
11. 定向/宽回归/全量测试、Ruff、format、Mypy、diff-check、凭据扫描、CI job 与 skip 数字。
12. 安全不变量逐项审查、真机门禁适用性与未验证事项；明确真实 Provider 准确率仍未验证。

---

## §12. DoD（Definition of Done）

全部满足方可 PASS：

- [ ] 基线、发布历史、镜像/远端与工作区状态可核验，无 reset / force-push / history rewrite。
- [ ] 木材消息从 bridge 到 character generation 的回合模式显式、类型化、可测；`None` 不再暗含能力策略。
- [ ] `QUERY` / `NARRATION` 获得普通文字回复时使用逐回合 CHAT_ONLY，AgentRuntime、ToolOrchestrator 和所有 Minecraft 工具调用路径均被跳过。
- [ ] `OTHER`、否定、歧义、需澄清、模型失败与非法输出直接安全结束，不进入 CharacterRuntime 的工具/Agent 回合。
- [ ] 只有正向木材分类且确定性参数校验通过时才进入木材专用计划链；失败不回落通用任务/Agent/工具链。
- [ ] 使用真实 `MinecraftChatBridge`、`CharacterRuntime`、`ToolRuntime`、`TaskTurnHandler` 与真实 `TaskRuntime` 装配完成 T1–T12 的关键测试；Agent 路径也有实际哨兵/计数，非仅靠 absent/disabled 配置推断。
- [ ] 非肯定类别的 TaskRuntime 与 AgentRuntime 任务创建计数均为 0；TaskRuntime 没有新增任务；计划服务/旧资源模板/ToolOrchestrator/Minecraft Service 动作调用均为 0；无 `action_id`。
- [ ] 恶意或工具调用型脚本输出在 CHAT_ONLY 模式下不会进入 ToolOrchestrator，不会输出内部工具 JSON 作为成功动作声明。
- [ ] 并发/异常后模式不会污染其他回合，非木材正常路径保持原语义。
- [ ] 控制命令/状态查询/busy 行为不变，控制命令与非木材输入的木材解释器调用数为 0。
- [ ] 日志敏感标记测试通过；本轮声明范围内的日志不包含用户原文、异常正文、Provider body 或 Prompt；M4 变异可被捕获。
- [ ] 8D/8E/两次 Follow-up 相关回归、全量 pytest、Ruff、format、Mypy、diff-check 与凭据扫描全部通过；没有新增意外 skip。
- [ ] M1–M4 变异均被测试捕获，恢复后全部通过，仓库没有残留变异。
- [ ] CI 最终证据提交的两个 job 全部 success；最终远端 SHA 与被 CI 检查的 SHA 完全一致。
- [ ] `ACTION_RISK`、`allow_medium`、工具 schema、确认门、步骤授权、任务预算、冻结计划、库存增量验证及数据库 schema 均无未批准变化。
- [ ] 报告将自动化证据、静态审查、真实 Provider/真实世界未验证事项分开陈述，不把 mock 测试描述成真实模型或真机验证。

### 裁决规则

- **FAIL**：任何非肯定分类触发 Agent/Tool/Minecraft 动作，出现越过确认/授权/Policy，或 CHAT_ONLY 通过共享状态修改污染其他回合。
- **PARTIAL**：代码看似收口，但真实入口、Agent 哨兵、Tool 调用反例、并发隔离、变异证据或最终 CI 证据有任一缺失。
- **PASS**：所有 DoD 都有可重复、相互一致的代码/测试/CI 证据；不以真实 Provider 未测为由夸大意图分类准确率。

---

## §13. 推荐的实施判断

首选方案是**为 `CharacterRuntime.respond()` 增加显式的逐回合执行模式**，并从 MinecraftChatBridge 的显式 dispatch 结果传入。`CHAT_ONLY` 的语义只覆盖当前这一条木材查询/叙述回复：普通语言生成仍可进行，但在 `_generate()` 的 Agent 与 Tool 分支之前终止能力调度。这样既能保留角色回复体验，又不会把 `QUERY` / `NARRATION` 重新放进全能力工具链。

如果代码审查发现现有 `CharacterRuntime` 不适合新增参数，可以改用等价的独立 `respond_chat_only()` / 专用受限生成器；必须复用当前角色上下文/会话语义，并证明它绕过 Agent 和 Tool 编排。不得以“多套一个 helper”为由复制另一套有不同安全行为的角色运行时。

对于木材查询中确实需要实时世界观察的需求，留待独立的小范围设计：只允许精确列出的 SAFE/只读能力，禁止 Agent 规划、任何写世界动作、TaskRuntime/AgentRuntime 任务创建、动作 `action_id` 与确认绕过。该需求不属于本任务书的默认实现范围。
