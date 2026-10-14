# CatooBot Minecraft — Phase 8E Follow-up 阶段任务书

**标题：木材意图路由闭环与异常日志隐私收口**  
**类型：安全路由 Follow-up / 不扩大产品范围**  
**基线提交：** `2b667b0127f97d9ed61c264aed5463757f223aba`  
**基线说明：** Phase 8E 最终证据提交（公开仓库 `rinsoraa/CatooBot` 的 `origin/main` 应在执行前重新核验；若不匹配，停止并记录实际 HEAD，不得直接套用本任务书）。  
**上游实现提交：** `2622311ef0bb4e895faee37959e12d7ded417426`  
**本任务目标：** 修正木材消息被 LLM 分类为非肯定类别后仍可能回落 `TaskTurnHandler` 旧资源任务入口的问题，并保证异常日志不输出异常正文。  
**发布纪律：** 独立实现提交；不 reset、不 force-push、不改写 Phase 8D、8D Follow-up、8E 历史。

---

## §1. 背景与裁决

Phase 8E 已引入独立的 `WoodIntentInterpreter`，使用结构化 schema 将木材相关消息分类为肯定采集、否定、歧义、查询、叙述、任务控制或其他意图。解释器本身不持有 Minecraft 执行工具或任务创建 API，这是正确的权限分层。

发布后的代码审查发现两个需要关闭的问题：

1. `MinecraftChatBridge._wood_intent_reply()` 对 `QUERY`、`NARRATION`、`TASK_CONTROL`、`OTHER` 返回 `None`；而 `_task_reply()` 将 `None` 解释为继续调用 `handler.handle()`，未接管时还会进入 `_plan_reply()`。旧 `TaskTurnHandler._create()` 在满足旧任务检测与方块识别条件时仍可调用 `plan_resource_task()`。因此，模型即使**合法地返回非肯定类别**，同一条木材消息仍有机会落回旧资源模板。
2. `app/tasks/wood_intent.py` 中部分异常日志使用 `log.warning(..., exc)` 输出完整异常字符串；`chat_bridge.py` 也存在 `log.exception(...)`。异常正文不是安全遥测字段的可靠来源，可能夹带 provider 响应、请求上下文或其他敏感诊断信息。

本阶段是**路由与日志隐私修正**，不是重新实现 LLM 解释器，也不是修改 Minecraft 的授权或执行机制。

### 当前验收裁决

Phase 8E 当前只能视为 **PARTIAL（暂缓完整验收）**，直到本任务书 §11 的所有阻断项关闭并重新发布验证。

---

## §2. 基线审计与停止条件（必须先做）

执行前，在发布镜像 `E:\WorkSpace ZCode\CatooBot_github` 中做只读审计，确认：

- `git status --short` 为空；
- 当前镜像 HEAD 与 `origin/main` 完全一致；
- 若远端 HEAD 不等于本任务书基线 `2b667b0127f97d9ed61c264aed5463757f223aba`，记录实际 HEAD、提交数与差异，确认差异只有已知后续证据提交或经用户授权接受的内容；不得丢弃远端提交；
- 当前工作区不是权威开发目录时，不在镜像目录直接开发；遵循项目既有开发工作区与 GitHub 发布镜像分离的流程；
- `scripts/` 仍按仓库既有约定保持本地辅助脚本，不得把凭据、私有配置、日志或本机数据发布到仓库。

**停止条件：** 工作区不干净、基线来源无法确认、存在未知提交/差异、或需要改写历史才能继续时，先停止并报告，不得强行继续。

---

## §3. 本阶段目标与非目标

### 3.1 必须达成

1. 为木材相关消息建立明确的路由结果，不能继续使用含义模糊的 `None` 同时表示“交回任务入口”和“这条消息由普通聊天处理”。
2. 只有经过严格验证的 `WOOD_COLLECTION_REQUEST + POSITIVE` 才能进入木材采集规划路径。
3. 木材消息被分类为 `QUERY`、`NARRATION`、`OTHER`、歧义、否定、需澄清或模型/schema 失败时，不得调用旧 `TaskTurnHandler` 资源创建路径，也不得回落通用多步规划路径来猜测并执行采集。
4. 控制命令继续由既有确定性检查与 `TaskTurnHandler` 控制流程处理。LLM 的 `TASK_CONTROL` 标签不能代替命令识别、玩家身份、活动任务状态或权限验证。
5. 使用真实 `TaskTurnHandler` / 真实任务入口集成测试，证明非肯定分类不会调用 `plan_resource_task()`、木材计划服务、TaskRuntime 创建或执行工具。
6. 收紧异常日志，不记录异常正文或原始 provider 内容；用含敏感标记的故障替身验证日志内容。
7. 保持 Phase 8D、8D Follow-up 2、8E 已有安全约束和回归断言，不弱化旧测试。

### 3.2 明确不做

- 不新增 Minecraft 工具、动作、权限、端点、数据库表或迁移。
- 不扩大木材任务预算，不改变最多 2 根原木的单任务限制。
- 不调整 `ACTION_RISK`、`allow_medium` 默认值、`TaskRuntime` 状态机、总步数/动作步数预算、步骤授权、可信玩家规则、`TurnOrigin` 派生或两道确认门。
- 不改木材计划冻结目标坐标、`expected_block`、掉落物/拾取引用和最终库存增量验证逻辑。
- 不将 LLM 输出升级为执行计划、坐标、方块 ID、权限或动作授权。
- 不将本阶段扩展为英文/多语言意图识别、模型准确率基准、通用 NLU 或真实 Provider 性能测试。
- 不以删除测试、放宽断言、扩大超时、屏蔽失败日志、增加 skip 的方式换取绿灯。

---

## §4. 路由契约：明确区分“接管”与“交回”

### 4.1 设计要求

审查 `MinecraftChatBridge._task_reply()`、`_wood_intent_preempts()`、`_wood_intent_reply()` 和现有 `_plan_reply()` 的调用关系。为木材消息采用显式、不可混淆的内部路由结果，例如 enum / tagged result / 结构化对象；具体实现可由开发者选择，但必须可测试、可审计。

语义至少区分：

- `WOOD_HANDLED`：木材分支已经生成回复；终止本次任务路由，不再进入 `handler.handle()` 或 `_plan_reply()`。
- `NORMAL_CHAT`（或同等语义）：木材消息由普通角色对话/既有只读查询流程处理；不得再次经过可创建任务的旧入口。
- `DELEGATE_CONTROL`：仅由确定性控制命令识别产生，交回既有控制入口进行状态和权限校验。
- `WOOD_POSITIVE_REQUEST`：只能来自通过 Pydantic schema 与矛盾组合校验的肯定采集结果，并且仍须通过确定性参数校验，再进入当前木材专用规划链。
- `SAFE_CLARIFY`：否定歧义、参数冲突、解释器缺失/超时/异常、空/非 JSON/schema 错误、未知/不一致结果等失败关闭结果；直接澄清并结束本次任务路由。

可以采用其他名称，但不得再让同一个 `None` 同时承担不同安全语义。

### 4.2 路由矩阵（必须实现并测试）

| 输入前置条件 / 解释结果 | 允许的后续路线 | 必须禁止 |
|---|---|---|
| 确定性检测器识别出的控制命令（如取消、暂停、继续、确认） | 既有 `TaskTurnHandler.handle()`，由既有控制逻辑核验任务状态与权限 | 解释器直接改变任务状态；新建木材任务 |
| 有活动任务时的状态查询 / busy 情况 | 既有任务状态/忙碌处理，保持已有优先级与行为 | 解释器覆盖活动任务、重建任务或改变状态 |
| 木材消息 → `WOOD_COLLECTION_REQUEST + POSITIVE` 且无需澄清 | `parse_wood_request()` + 支持树种矩阵确定性校验 → 当前木材专用计划链 | 进入通用旧资源模板；信任模型的坐标、block ID 或授权字段 |
| 木材消息 → `NEGATED_REQUEST + NEGATIVE` | 明确不执行的回复，终止本次路由 | 计划服务、`plan_resource_task()`、`_plan_reply()`、工具或 TaskRuntime 创建 |
| 木材消息 → `AMBIGUOUS_INTENT` / `MIXED` / `UNKNOWN` / `needs_clarification=true` | 澄清并终止本次路由 | 猜测最终意图、挑选一个树种、进入任何任务创建路径 |
| 木材消息 → `QUERY` | 进入普通聊天/既有只读查询语义，不经过可创建任务的 `TaskTurnHandler` 资源入口 | 查询因包含“木头/砍树”等词而创建采集任务 |
| 木材消息 → `NARRATION` | 普通角色对话，不经过可创建任务的旧入口 | 将叙述转换成采集请求 |
| 木材消息 → `OTHER` | 普通角色对话或安全澄清；采用明确策略并终止任务路由 | 把 `OTHER` 当成“未识别、继续猜任务”并回落旧模板 |
| 木材消息 → `TASK_CONTROL`，但确定性检查并未识别为控制命令 | 不执行控制；安全澄清或普通对话，并终止任务创建路由 | 仅凭 LLM 标签执行确认、取消、暂停或继续 |
| 解释器缺失、异常、超时、限流、空输出、非 JSON、截断、schema 不合格、未知结果 | 安全澄清并终止任务路由 | 任何旧资源模板 fallback；以异常作为许可或肯定意图 |
| 非木材消息 | 完全走原有路径，行为保持不变 | 额外调用木材解释器或改变现有非木材任务路由 |

### 4.3 控制命令优先级

- 控制命令优先由既有确定性 `detector.control_command(message)` 等逻辑识别，不把控制权交给模型。
- 既有任务状态查询、活动任务 busy 保护必须维持原行为。
- 控制命令如果自身含有木材词（例如“取消，别砍树了”），仍然由旧控制入口处理；解释器不能抢截。
- 如果模型将非控制消息标为 `TASK_CONTROL`，这不构成控制权限；应当澄清/普通对话，不得执行。
- 不允许通过将 `TASK_CONTROL` 改名为 `OTHER`、或反过来利用回落旧入口的办法绕开本契约。

---

## §5. 必须解决的核心缺陷与实现指导

### 5.1 修复非肯定类别的回落路径

在唯一聊天入口处明确处理木材分类结果。非肯定木材结果必须在 `_task_reply()` 的任务路由继续向下执行之前，决定“终止任务路由并走普通对话/澄清”或“交给确定性控制入口”。

不接受以下实现：

- 仅在 `_wood_intent_reply()` 内把 `None` 改成另一个含糊的 `None`；
- 只依赖 `_wood_preempts()` / `mentions_wood()` 的词法判定证明安全；
- 只 monkeypatch `AgentPlanService`，没有守住 `TaskTurnHandler._create()` 中的 `plan_resource_task()`；
- 对 `OTHER` 只记录日志，然后继续执行旧链；
- 将所有消息一律标为否定；
- 为了规避问题而对所有木材消息无条件阻止正常聊天或只读查询；
- 删除 `handler.handle()` 或 `_plan_reply()` 的通用功能，导致非木材行为回归。

### 5.2 确定性参数校验继续有效

即使 LLM 输出 `WOOD_COLLECTION_REQUEST + POSITIVE`，仍须执行：

1. `parse_wood_request()`；
2. 本地支持树种矩阵验证；
3. 模型 `target_species` / `requested_count` 与确定性解析的冲突检测；
4. SAFE 观察与现有有界木材专用规划链；
5. Policy 与现有冻结计划、预览、用户明确确认、TaskRuntime、步骤授权、Action Runtime、最终库存增量验证。

解析缺失、冲突或不支持时应澄清，不得转回 `TaskTurnHandler._create()` 作为补救。

### 5.3 日志异常收口

审计 `app/tasks/wood_intent.py`、`app/integrations/minecraft/chat_bridge.py` 以及本次变更可达的相关日志点。

要求：

- 不记录 `str(exc)`、`repr(exc)`、完整 provider 响应、请求体、提示词或用户完整消息；
- 不使用会自动输出异常字符串的 `log.exception()`，除非能证明其 traceback/exception 链不会泄露本任务禁止的信息；本 Follow-up 默认要求改为安全结构化日志；
- 允许记录有限的稳定字段：阶段/组件、错误类型名、稳定 `reason_code`、耗时（若已有）、文本长度、schema 字段路径与错误类型；
- schema validation 日志继续只输出截断后的字段路径/错误类型摘要，不记录输入值；
- 若某个 provider/library 的异常类型名称或异常文本可能包含秘密，日志只记录固定的类别码，不直接序列化异常对象；
- 不以“日志文件里这次没出现 key”替代代码层面的字段限制。

---

## §6. 必需测试：真实入口，而不是只测替身

### 6.1 真实 TaskTurnHandler 的防回落测试（阻断门禁）

使用仓库真实 `TaskTurnHandler` 及其现有测试夹具/运行时装配；允许替换底层观察、Runtime 或 provider，但不得用一个始终返回 `TaskTurnOutcome(False)` 的 `TurnsDouble` 替代真实 `handle()` 来证明旧入口没被调用。

对下列每组输入，控制解释器（fake/scripted provider）返回相应合法结构化结果，然后从真实 Minecraft chat 入口发消息：

| 输入消息 | 脚本化 LLM 结果 | 必须断言 |
|---|---|---|
| `帮我砍两根橡木原木带回来` | `OTHER` | 旧 `TaskTurnHandler._create()` 不启动；`plan_resource_task()` 不调用 |
| `去砍两根橡木原木带回来` | `QUERY` | 不进入任何采集任务创建路径 |
| `帮我砍一根云杉原木` | `NARRATION` | 不创建任务；进入普通对话语义 |
| `附近有什么木头？` | `QUERY` | 不创建采集任务；保留查询/聊天行为 |
| `我昨天砍了两根竹子` | `NARRATION` | 不创建采集任务 |
| `帮我砍两根橡木原木带回来` | `AMBIGUOUS_INTENT` / `needs_clarification=true` | 澄清后终止路由；不进入旧模板 |
| `帮我砍两根橡木原木带回来` | timeout / exception / empty / invalid JSON / invalid schema | 安全失败并终止路由；不进入旧模板 |
| `帮我砍两根橡木原木带回来` | `NEGATED_REQUEST + NEGATIVE` | 零任务、零计划、零工具、零 action_id |

注意：上述一些组合故意让消息表面上像肯定采集请求，却让模型返回非肯定类别。这是在验证系统如何面对**模型分类错误**，不是在声称此类输出在语言上正确。安全设计必须在这种条件下仍关闭执行路径。

### 6.2 Sentinel 与副作用断言

对所有 6.1 中的非肯定/失败用例：

- monkeypatch **`app.tasks.turn.plan_resource_task`**（即被 `TaskTurnHandler._create()` 直接导入/调用的符号）为立即抛出唯一可辨识异常的 sentinel；
- 同时为可触达的木材计划服务/`plan_goal_from_user` 安装调用计数器或 sentinel；
- 断言所有 sentinel 调用计数均为 0；
- 断言没有新 Task / task_id / action_id；
- 断言 `env.tools.executor.traces == []`；
- 断言 Action Runtime 没有收到动作调用；
- 断言 TaskRuntime 现有活动任务的 ID、状态、授权和步骤预算均未被解释器更改；
- 断言控制命令仍由既有确定性控制处理，解释器零调用；
- 断言非木材消息仍不触发木材解释器。

若运行时无法直接观察 `TaskRuntime.create_task()`，应在测试中给该边界增加可注入的 spy/包装器或在现有 Runtime 接口处计数；不得仅用“没有工具 trace”推断“没有建任务”。

### 6.3 肯定木材请求保真测试

- `WOOD_COLLECTION_REQUEST + POSITIVE` 且参数一致时，仍只进入现有木材专用规划链；
- 预览和用户明确确认前，工具 trace 为空、没有 action_id；
- 参数冲突、未知树种、显式 0 根或模型要求澄清时，安全澄清；
- `allow_medium=False` 时仍按既有策略门禁拒绝可执行计划；
- 一次最多 2 根的预算与原有冻结目标/最终库存增量断言保持不变；
- 不能因为本次修改把正向木材任务转回旧 `plan_resource_task()` 模板。

### 6.4 控制 / 活动任务矩阵

至少覆盖：

- `取消，别砍树了`；
- `暂停当前任务`；
- `继续`；
- `确认`；
- 有待确认任务时收到取消；
- 运行中任务收到新木材采集请求；
- 有活动任务时的状态查询；
- 模型返回 `TASK_CONTROL`，但确定性 detector 未识别控制词。

断言：原有状态机优先级不变；模型不会直接执行控制；活动任务不会被覆盖或重复创建。

### 6.5 日志隐私测试

用以下方式注入故障：

- provider 抛出 `RuntimeError("SENSITIVE_SENTINEL_PROVIDER_BODY_8EFU")`；
- 上下文/提示构造故障携带 `SENSITIVE_SENTINEL_CONTEXT_8EFU`；
- 在适合的入口异常路径中携带 `SENSITIVE_SENTINEL_ROUTE_8EFU`；
- 非法 schema 错误输入包含敏感标记，但验证错误日志只记录字段路径/错误类型。

通过 `caplog` 或项目现有日志捕获工具断言：

- 日志不包含上述任何完整敏感标记；
- 日志不含原始消息、完整 prompt、provider response/body；
- 日志可保留稳定 reason code、错误类型类别、字段路径、文本长度等白名单字段；
- 异常被处理后仍返回安全澄清，不向玩家回显异常正文。

---

## §7. 测试组织与变异验证

新增或调整测试文件应遵循仓库惯例。推荐将本 Follow-up 的集成测试与隐私测试独立命名，例如：

- `tests/test_phase8e_followup_routing_closure.py`
- `tests/test_phase8e_followup_log_privacy.py`

名称可调整，但必须保持职责清晰，不能把所有断言塞入一条宽泛测试。

必须完成一次变异/反证检查：

1. 临时构造一个会让非肯定结果 `return None` 并继续进入真实 `handler.handle()` 的旧式路径；对应路由闭环测试必须失败。
2. 临时让日志重新输出 `str(exc)`；日志隐私测试必须失败。
3. 恢复正确实现后，重新运行所有相关测试并保留前后命令、退出码和失败点证据。

变异验证只在本地临时工作树完成，不把故意错误代码提交到主分支；恢复后执行 `git diff --check` 并审阅 diff，确认没有留下临时修改。

---

## §8. 实现边界与代码审查要求

预期主要触及：

- `app/integrations/minecraft/chat_bridge.py`：明确路由结果、阻止非肯定木材结果回落旧任务入口、保留控制优先级；
- `app/tasks/wood_intent.py`：安全日志字段与异常处理；
- 新增 Follow-up 路由/日志测试；
- `docs/MINECRAFT_PHASE8E.md`、本任务书归档、`CHANGELOG.md`、必要时 `docs/README.md`。

实际修改文件可有合理差异，但如果涉及下列任一受保护文件或机制，必须先给出必要性与风险分析，并将其作为本阶段的高风险变更重新审查：

- `app/integrations/minecraft/agent.py` 的 `ACTION_RISK`；
- `app/config/settings.py` 或真实配置的 `allow_medium` 默认/值；
- `app/tools/registry.py`、`app/tools/builtins/` 的工具与 schema；
- `app/tasks/runtime.py`、`app/tasks/validation.py` 的状态机、预算和步骤授权；
- `app/tasks/planner.py` 中木材专用计划的冻结目标/拾取引用/库存增量逻辑；
- `app/database/database.py` 或迁移目录。

本阶段原则上不需要改动这些受保护文件。若为了绕开路由缺陷而改动它们，应视为越界并重新评估。

---

## §9. 必需门禁

按顺序执行并记录真实退出码：

1. 新增 Follow-up 路由闭环测试与日志隐私测试；
2. `pytest tests/test_phase8e_followup_routing_closure.py -q`（或实际文件名）；
3. `pytest tests/test_phase8e_followup_log_privacy.py -q`（或实际文件名）；
4. 木材相关全套测试：Phase 8D、Follow-up 1/2、Phase 8E 及本 Follow-up；
5. 较宽回归，覆盖 `wood`、`intent`、`turn`、`task_entry`、`agent_plan`、`skill`、`minecraft_chat`、`chat_bridge`、`phase8`；
6. `pytest tests -q`；
7. `ruff check .`；
8. `ruff format --check .`；
9. `mypy app`；
10. `git diff --check`；
11. 凭据/私有数据扫描；
12. 审查 `git diff --name-only`，确认 `scripts/` 未进入 tracked 发布内容、数据库/迁移无未批准变化；
13. 逐条重新审核 §4 路由矩阵、§6 副作用断言、§6.5 日志隐私测试和 §10 安全不变量；
14. 变异验证前后测试结果齐全，最终工作树不含临时变异。

**回归要求：** 不得新增无解释的 skip。若测试因环境原因失败，保留首次失败证据；只有在确认是环境敏感且原因记录充分时，才能仅重跑失败 job/用例。不得声称无 flake，除非证据确实支持。

---

## §10. 发布、CI 和证据要求

1. 完成修复与门禁后，创建一个独立、可审查的实现提交；不得 amend/重写 Phase 8E 历史。
2. 将变更按既有流程从权威开发目录同步到干净发布镜像；发布前比较源码、镜像与准备推送的内容，明确列出允许/禁止发布文件。
3. 发布后核对镜像 HEAD、`origin/main`、最终 SHA 和提交数量；确认工作区干净，`scripts/` 仍为 0 tracked（如仓库当前约定未变）。
4. 对最终实现提交与最终证据提交分别记录 GitHub Actions run URL、run ID、attempt、各 job 结论、pytest 数字、skip 清单、Browser E2E 与 Minecraft runtime job 状态。
5. CI 必须对最终提交通过；不能用较早提交的成功 CI 替代最终提交证据。
6. 更新 `docs/MINECRAFT_PHASE8E.md`，新增 Follow-up 小节：记录缺陷、修复前失败证据、修复后真实入口测试、日志隐私证据、门禁、提交链、CI、已知限制和本轮未验证项。
7. 更新 `CHANGELOG.md` 与文档导航；将本任务书本身归档到 `docs/specs/`，确保归档内容与任务书源一致并记录 SHA256。
8. 真实 Provider 测试不是本 Follow-up 的必需门禁；不得宣称它通过或以脚本化 Provider 结果代替真实模型准确率。真机测试是否适用需根据最终 diff 重新判断；若仅改分类路由与日志、不改动作/授权/执行链，可以按范围说明不适用，但不能写成“真机链路已验证”。

---

## §11. 最终报告模板（必填）

最终报告必须分别给出“事实证据”和“静态审查判断”，至少包含：

1. **裁决**：PASS / PARTIAL / FAIL，并逐条对应 §12 DoD；
2. **基线、实现提交、最终证据提交、镜像/远端 HEAD、工作区状态和提交数量**；
3. **旧回落路径说明**：修复前控制流、修复后显式路由结果及其终止点；
4. **真实入口测试证据**：使用真实 `TaskTurnHandler` 的哪些用例；不能只写替身测试通过；
5. **Sentinel 证据**：`app.tasks.turn.plan_resource_task`、木材计划服务/AgentPlan 服务调用计数、TaskRuntime 创建计数、工具 trace 和 action_id 的逐项结果；
6. **非肯定分类矩阵**：OTHER / QUERY / NARRATION / AMBIGUOUS / NEGATED / TASK_CONTROL 非确定性结果 / provider 失败各自的目标路由和副作用；
7. **日志隐私证据**：使用了哪些敏感标记、`caplog` 的断言结果、哪些字段被允许记录；不得贴出秘密本身；
8. **测试门禁**：每条命令、通过/失败数量、耗时、退出码、skip 与重跑情况；
9. **变异验证**：错误路由和 `str(exc)` 日志变异是否被测试捕获，恢复后结果；
10. **安全不变量清单**：未改项与若有偏离的解释；
11. **CI 证据**：run URL / ID / attempt / job 结论，最终 SHA 与 CI SHA 一致性；
12. **限制与未验证事项**：真实 Provider、实际模型误判率、延迟/token 成本、真机测试是否适用；不得夸大结论。

---

## §12. DoD（Definition of Done）

只有全部满足，才能裁决 PASS：

1. 基线和发布历史可核验，无 reset、force-push 或历史改写；
2. 路由语义已明确，`None` 不再同时代表不同的安全动作；
3. 对木材消息，只有 `WOOD_COLLECTION_REQUEST + POSITIVE` 且参数校验通过的结果可以进入木材采集计划链；
4. `QUERY`、`NARRATION`、`OTHER`、歧义、否定、需澄清、模型/provider/schema 失败都不会进入旧 `TaskTurnHandler._create()` 资源任务路径或不受控 `_plan_reply()` 采集回落路径；
5. 确定性控制命令仍由原入口执行，模型不能独立触发控制操作；活动任务状态和 busy 语义不变；
6. 使用真实 `TaskTurnHandler` 的入口级测试证明上述性质；非肯定类别测试中的 `app.tasks.turn.plan_resource_task` sentinel 未触发；
7. 对非肯定/失败用例，计划服务和木材计划服务调用为 0、TaskRuntime 新建任务为 0、工具 trace 为空、没有 action_id 和 Action Runtime 调用；
8. 肯定木材请求仍通过确定性参数校验、SAFE 观察、Policy、计划冻结、预览、确认、步骤授权与最终库存验证；确认前零动作；
9. 异常日志不输出异常正文、原始消息、prompt 或 provider body，隐私测试与日志变异验证均通过；
10. Phase 8D / Follow-up 1 / Follow-up 2 / 8E 既有测试与非木材回归全部通过，没有删除或弱化旧断言；
11. `pytest tests -q`、Ruff、format、Mypy、diff check 和凭据扫描通过，没有新增无解释 skip；
12. CI 对最终提交成功，提交/镜像/远端一致，文档、CHANGELOG、任务书归档与最终报告齐全；
13. 报告明确区分自动化执行证据、静态审查和未验证事项。

### 裁决规则

- **PASS**：全部 DoD 满足，且没有未关闭的路由闭环或日志隐私缺陷。
- **PARTIAL**：主要实现和测试存在，但真实入口覆盖、关键副作用计数、日志隐私证据或最终发布核验任一不足。
- **FAIL**：非肯定/失败分类仍能创建采集任务或动作；控制命令被模型绕过确定性检查；异常正文泄露敏感标记；安全不变量被未授权修改；或通过删改断言掩盖问题。

**特别强调：** 仅凭全量 pytest 全绿、CI 通过、Pydantic schema 严格或 fake provider 输出合法 JSON，不能证明分类误判时的任务入口安全。此阶段的决定性证据是：真实聊天入口中，非肯定分类无法到达任何可创建资源任务的旧路径。

---

## §13. 推荐执行顺序

1. **只读审计**：确认基线与现有控制流，不先改代码。
2. **写回归测试并证明当前缺陷**：使用真实 `TaskTurnHandler`，让模型替身对一条表面肯定的采集句返回 `OTHER` / `QUERY` / `NARRATION`，确认修复前测试会抓到旧入口回落。
3. **实现明确路由结果**：在唯一聊天入口关闭非肯定结果的任务回落；控制命令仍优先交给现有确定性逻辑。
4. **补齐副作用计数**：计划服务、`plan_resource_task`、TaskRuntime 创建、工具 trace、action_id 全部可观察。
5. **修复日志隐私**：替换异常正文记录方式，加入敏感标记 `caplog` 测试。
6. **变异验证**：恢复旧路由与异常正文日志的变异均必须使测试失败。
7. **执行全套门禁**：木材套件、较宽回归、全量 pytest、Ruff、format、Mypy、diff/凭据扫描。
8. **审阅 diff 与安全不变量**：确认范围收敛，无不必要的执行链/配置/数据库修改。
9. **独立提交、同步镜像与发布**：验证最终 SHA、CI、文档、任务书归档。
10. **提交 §11 格式最终报告**：任何关键证据缺失就按 PARTIAL 处理，不把未验证事项表述为 PASS。
