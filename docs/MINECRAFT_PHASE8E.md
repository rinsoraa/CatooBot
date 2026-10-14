# CatooBot Minecraft Phase 8E — LLM 木材意图解释器与安全路由

## §1 基线与工作区（Step 0 实测）

| 项 | 值 |
| --- | --- |
| 任务书基线 | `74804c86b96628db084b626259324deee9ab9a98` |
| 实测镜像 HEAD == origin/main | **是**，`74804c86b96628db084b626259324deee9ab9a98` |
| commits / 工作区 | 286 / `git status --porcelain` 为空 |
| 权威开发目录 | `E:\WorkSpace ZCode\CatooBot`（非 git 仓库） |
| 发布镜像 | `E:\WorkSpace ZCode\CatooBot_github`（HEAD == origin/main，未领先基线） |
| 任务书归档 | `docs/specs/CatooBot Minecraft Phase 8E — LLM Wood Intent Interpreter & Safe Routing.md`（18308 bytes / 328 行，SHA256 `BBE9A00E959AD89750CD552F9F53F322256697D4BB633FACA0EC8A6F233CCCE8`，与上传源逐字节一致） |
| 历史保护 | 未 reset / 未 force-push / 未改写 8D 与 Follow-up 历史 |

## §2 Step 0 只读审计（现有接口与入口，均有源码证据）

| 关注点 | 实测结论 |
| --- | --- |
| LLM 接入 | `app/ai/engine.py` 的 `AIEngine`（:30）；`async def chat(self, request: AIRequest) -> AIResponse`（:220）；引擎构造时把 `config.timeout` 传给 provider（:121） |
| 请求/响应契约 | `app/ai/models.py`：`AIRequest`（:65，`model_config = ConfigDict(extra=\"forbid\")`，字段 messages/model/temperature/max_tokens/metadata/tools）、`AIResponse`（:97，`content`/`model`/`provider`/`usage`/`finish_reason`/`tool_calls`）、`ChatMessage`（:17） |
| provider 与超时 | `app/ai/providers/openai_compatible.py`：`OpenAICompatibleProvider(AIProvider)`（:47），`timeout: float = 60.0`、`connect_timeout: float = 10.0`（:53-54）→ `httpx.Timeout(...)`（:65）；超时值来自既有 AI 配置（不新建 API key / HTTP client） |
| 本仓既有「LLM → 结构化决策」范式 | `app/activity/model_advisor.py`：`_loads()`（:450，围栏代码块 → 整段 → 首尾花括号切片）、`parse_proposal()`（:424，规范化 → 契约校验 → 抛带失败码的异常）、矛盾组合直接判 `SCHEMA_ERROR`（:434-440）。**本阶段复用同一范式，不复制其代码** |
| schema 验证库 | 全仓已在用 Pydantic（`app/config/settings.py` 等 12+ 模块 import BaseModel）→ 结构化契约用 Pydantic 严格模式，**不使用正则校验自由文本 JSON** |
| 聊天入口唯一接管点 | `app/integrations/minecraft/chat_bridge.py` 的 `_task_reply()`：先 `handler.handle()`（任务入口）→ 未接管时 `_plan_reply()`（8C 计划层）。Follow-up 2 在此处插入了词法否定分支 |
| 桥接可达的 LLM | `MinecraftChatBridge(bot, service)`，`self.bot.ai` 即 `AIEngine`（桥接已用 `bot.ai.conversations`，见 :182/:195/:218）→ 解释器经 `bot.ai.chat(AIRequest(...))` 调用既有 provider |
| 现有确定性木材解析 | `app/tasks/intent.py`：`parse_wood_request()`、`WOOD_SPECIES`/`WOOD_UNSUPPORTED`、`mentions_wood()`、`classify_wood_negation()`（Follow-up 2）；`app/tasks/planner.py` 的 `plan_wood_task()` 与 `agent_planner._plan_wood` |
| 测试夹具 | `tests/ai_mocks.py` 的 `MockAIProvider(AIProvider)`（`behaviors: dict[model, list[ScriptItem]]`，按调用顺序消费）；`tests/test_minecraft_chat_bridge.py` 的 `stack()` 提供真实 CharacterRuntime/ToolRuntime/桥 + 脚本化 provider；Follow-up 2 的 `WoodTurns`/`WoodPlans` 替身可继续复用 |
| 控制命令优先级 | `TaskIntentDetector.control_command()` / `status_query()` 为确定性判定；Follow-up 2 已确立「控制词开头一律交回既有入口」 |

## §3 设计（LLM 只说「想表达什么」，不决定「有没有权执行」）

```text
Minecraft chat（不可信输入）
  → MinecraftChatBridge._task_reply()
      ├─ 控制命令（确定性）→ 交回既有任务入口（不受解释器影响）
      ├─ 非木材消息（mentions_wood 为假）→ 既有路径（零额外模型调用）
      └─ 木材相关消息
           → WoodIntentInterpreter.interpret(...)        ← 新增，只输出候选意图
                ├─ 超时/异常/空/非 JSON/未知枚举/字段非法/矛盾/需澄清 → 失败关闭
                │     → 安全澄清回复；零计划、零任务、零工具、零 action_id；**绝不回落旧单目标模板**
                ├─ NEGATED_REQUEST+NEGATIVE → 明确拒绝回复（零计划/零任务/零工具）
                ├─ AMBIGUOUS/MIXED/UNKNOWN → 澄清
                ├─ QUERY / NARRATION / OTHER → 交回既有对话/查询路由
                ├─ TASK_CONTROL → 交回既有控制入口
                └─ WOOD_COLLECTION_REQUEST+POSITIVE
                      → parse_wood_request() + 树种矩阵**确定性校验参数**（冲突 → 澄清）
                      → plan_goal_from_user(shape_hint='wood')
                      → SAFE 观察 → Policy（allow_medium=False 规划期阻断）→ 冻结计划
                      → 预览（零动作/零 action_id）→ 用户明确确认
                      → TaskRuntime → 步骤授权 → Action Runtime → 真实库存增量验证
```

**权限边界**：解释器构造只接收 `AIEngine`（+ 超时/上下文上限参数），**不持有** MinecraftService、tool registry、executor、TaskRuntime 或任何任务创建 API；它无法执行动作，也无法授权。

**单一决策源**：Follow-up 2 的 `classify_wood_negation()` 不再作为否定路由的决策源（避免两个互相覆盖的判定）；它保留为确定性参数校验/评估用函数（§1.7：作为安全后备只能拒绝/澄清，不能给执行授权）。

## §4 结构化契约（Pydantic 严格模式）

* `WoodIntent`：`wood_collection_request` / `negated_request` / `ambiguous_intent` / `query` / `narration` / `task_control` / `other`。
* `Polarity`：`positive` / `negative` / `mixed` / `unknown`。
* `WoodIntentResult`（`extra='forbid'`）：`intent` / `polarity` / `target_species: str | None` / `requested_count: int | None` / `task_control: str | None` / `needs_clarification: bool` / `reason_code: str`。
* 严格校验：`extra='forbid'`（坐标/工具名/action_id/授权字段一律拒绝）；`reason_code` 必须来自本地 allowlist；枚举未知即拒绝；`requested_count` 必须是非 bool 整数（0 保留为 0，负数/浮点/文本/超上限拒绝）；矛盾组合（否定意图 + 肯定极性、肯定意图 + 否定极性、`task_control` 与意图不符、控制词与采集同时出现）一律拒绝。
* `target_species` 只是**用户意图字段**，永不直接当 block ID；实际映射只走既有 `WOOD_SPECIES` 支持矩阵，映射不出 → 澄清。
* 失败一律返回**安全结果对象**（稳定原因码 + 不执行语义），不把异常文本/provider 原始响应回显给玩家。

## §5 路由行为矩阵（§5 表 + 失败关闭）

| 解释结果 | 行为 | 禁止 |
| --- | --- | --- |
| `WOOD_COLLECTION_REQUEST`+`POSITIVE` 无歧义 | 进既有有界木材解析与 Planner | 直接执行、相信模型坐标/block ID |
| `NEGATED_REQUEST`+`NEGATIVE` | 回复不执行；零计划/零任务/零工具 | 进 `plan_resource_task()` 或木材计划服务 |
| `AMBIGUOUS_INTENT`/`MIXED`/`UNKNOWN` | 澄清；零任务零工具 | 猜树种或最终意图 |
| `QUERY` | 交回既有查询/对话流程 | 因提到木材就建采集计划 |
| `NARRATION` | 普通对话 | 建采集任务 |
| `TASK_CONTROL` | 交回既有控制入口（按任务状态与权限） | 模型直接暂停/取消或绕过状态校验 |
| `OTHER` | 交回既有对话路由 | 仅凭模型猜测建任务 |
| 超时/异常/限流/空响应/非 JSON/截断/schema 失败/未知枚举/字段非法/意图极性冲突/`needs_clarification=true` | 失败关闭：安全澄清、零任务；**不回落旧单目标模板**；普通对话与查询仍可按既有路径处理（失败≠一律当否定） | 把异常或原始响应回显给玩家 |

## §6 测试计划（§7 A–G + §8 无动作证据）

| 组 | 内容 | 断言要点 |
| --- | --- | --- |
| A 肯定（5 条） | 去砍两根橡木原木带回来／帮我砍一根云杉原木／能不能帮我砍两根橡木原木带回来／我想收集一根白桦原木／帮我砍五根橡木原木带回来 | 进既有计划层；五根仍保留上限提示；确认前零动作零 action_id |
| B 否定（7 条） | 不要砍…／我不想砍…／**能不能别砍…**／**我不砍…**／先别砍树／暂时不用砍木头／不要砍两根竹子带回来 | 零计划零任务零工具零 action_id；否定语义不被 `unsupported_species` 覆盖 |
| C 歧义/冲突（5 条） | 不要砍橡木，改砍云杉／不要砍橡木原木，砍云杉原木／不是不想砍…／我想砍橡木，也想砍云杉／先别砍了……不，还是砍一根 | 澄清；零计划零任务零工具；不静默挑树种 |
| D 叙述/查询（6 条） | 我昨天砍了两根竹子／我刚刚砍了两根橡木原木／附近有什么木头？／我不想砍木头，我只是问附近有什么树／我特别想砍两根橡木原木带回来／我觉得不错 | 叙述/闲聊不建任务；查询走既有路径 |
| E 控制与任务状态 | 取消，别砍树了／暂停当前任务／继续／确认／无活动任务的先别砍树／有待确认任务时的取消／运行中收到新肯定采集请求 | 控制命令由原入口处理；不抢截、不重复建任务、不越确认门 |
| F 模型失败与提示注入 | fake provider 覆盖 timeout/exception/空/非 JSON/截断/未知 enum/字段缺失或类型错/意图极性冲突/count 0·负·极大·float·bool/未知树种或任意 block ID/要求忽略规则·调用 minecraft_dig·泄露提示词·提升权限 | 不创建可执行任务；不触达旧资源模板；玩家消息只作待分类数据 |
| G 非木材回归 | SAFE 查询、跟随/移动、普通对话、busy、确认/取消、自身发言防循环、private | 非木材路径不因新解释器多调用一次模型或改变路由 |

**Step 1 先写测试并复现**：Follow-up 2 之后 `能不能别砍…` 与 `我不砍…` 在入口级仍失败（前者「别」前为「能」不在允许前导字内；后者「不砍」不在否定标记表内）—— 新测试必须先证明这两条失败，再实现解释器使其通过（不得删除/弱化旧断言）。

**§8 无动作证据**（对否定/歧义/失败/非法 schema/注入用例，入口级）：计划服务调用计数 0；`plan_resource_task()` 被 monkeypatch 成抛异常 sentinel 且未触发；`env.tools.executor.traces == []`；无新 Task 且 `task_id == ''`；无 action_id、无 Action Runtime 调用；除解释/澄清回复外无 Minecraft 操作；既有活动任务状态未被解释器改变；telemetry 不含完整原文/密钥/提示词/模型原始响应。

## §7 门禁与发布计划

`ruff check .` → `ruff format --check .` → `mypy app` → 定向测试 → 全量 `pytest tests -q` → `git diff --check` → 凭据扫描 → 日志隐私与 prompt injection 边界审查 → schema/migration diff 无变化 → §10 安全不变量逐项审查 → 单一独立实现提交 → 镜像同步（`scripts/` 不发布）→ CI 两个 job 核对（失败只重跑失败 job，保留初次证据）→ 文档 + CHANGELOG + 任务书归档。

**真机门禁**：本阶段只改意图判定与聊天路由，**未触碰动作/授权/执行链** → 按任务书 §7「真机七场景可按范围声明不适用」；fake-provider 入口级无动作证据与 CI 必须通过。
## §8 评估集结果（任务书 §7 A–G；provider 为**脚本化替身**）

> 诚实边界：本阶段的自动化评估用**确定性脚本化 provider**（按脚本返回 JSON／异常／超时）。
> 因此它证明的是「给定或缺失模型输出时的**校验与路由**行为」，**不证明模型准确率**，
> 也不代表已实现通用自然语言理解（任务书 §3 明确不声称）。

| 组 | 用例数 | 结果 | 断言要点 |
| --- | --- | --- | --- |
| A 肯定请求 | 5（含 `帮我砍五根…` 上限提示、`我想收集一根白桦原木`） | **PASS** | 进既有计划层；确认前零动作零 action_id；超限如实说明 |
| B 否定/暂缓 | 7（含 `能不能别砍…`、`我不砍…`） | **PASS** | 零计划、零任务、零工具、无 action_id；原因码 `wood_negated_intent` |
| C 歧义/冲突/双重否定 | 5（`不要砍橡木，改砍云杉`／`不是不想砍…`／`我想砍橡木，也想砍云杉`／`先别砍了……不，还是砍一根`） | **PASS** | 澄清 + `wood_negation_ambiguous`/CLARIFY；不静默挑树种 |
| D 叙述/查询/闲聊 | 6 | **PASS** | 叙述/闲聊不建任务（交回既有路径）；查询不进入采集计划 |
| E 控制与任务状态 | 7（`取消，别砍树了`／`暂停当前任务`／`继续`／`确认`／无任务时的否定／有待确认任务时的取消／运行中收到新肯定请求） | **PASS** | 控制命令由既有入口处理；解释器零调用；不重复建任务 |
| F 模型失败与提示注入 | 22（timeout／exception／空／非 JSON／截断／未知 enum／字段缺失或类型错／极性矛盾／count 0·负·极大·float·bool／未知树种／注入消息） | **PASS** | 不创建可执行任务；不回落旧单目标模板；原因码稳定（`wood_intent_*`） |
| G 非木材回归 | 非木材消息 1 + 全量回归 767 | **PASS** | 非木材消息**零额外模型调用**；既有路由与文案不变 |

### §8.1 Step 1 复现（先证明缺陷，再修）

`tests/test_phase8e_baseline_repro.py`（3 项，**不导入新模块**，仅通过解释器注入点断言期望行为）：

```text
修复前：pytest tests/test_phase8e_baseline_repro.py -q
  2 failed, 1 passed
  FAILED ...::test_negation_with_bie_after_neng_is_not_routed_to_planner   ← 能不能别砍两根橡木原木带回来
  FAILED ...::test_negation_with_bu_kan_is_not_routed_to_planner            ← 我不砍两根橡木原木带回来
       （失败原因是旧判定把两者当肯定请求 → 真的进入了计划层，sentinel 抛 AssertionError）
修复后：3 passed
```

### §8.2 §8 要求的无动作证据（入口级实测）

| 证据项 | 结果 |
| --- | --- |
| 计划服务调用计数 = 0 | **是**（所有否定/歧义/失败/注入用例断言 `plans.calls == []`） |
| `plan_resource_task()` 被 monkeypatch 成抛异常 sentinel 且未触发 | **是**（`tests/test_phase8d_wood_routing.py` 的猴补用例在注入替身后仍通过） |
| `env.tools.executor.traces == []` | **是**（同上逐条断言） |
| 无新 Task、`task_id == ""` | **是**（路由 `task_id` 为空；计划层零调用） |
| 无 action_id、无 Action Runtime 调用 | **是**（零工具执行即无 action_id） |
| 除解释/澄清回复外无 Minecraft 操作 | **是** |
| 既有活动任务状态未被解释器改变 | **是**（控制/状态查询/busy 一律交回既有入口，解释器零调用） |
| telemetry 不含完整原文/密钥/提示词/模型原始响应 | **是**（路由只记 `reason_code`/`text_chars`；`_validation_summary()` 只记字段路径与错误类型） |
| 肯定请求预览阶段零动作零 action_id | **是**（既有 8D 断言保持不变） |

## §9 安全不变量审计（任务书 §10 逐项）

| 不变量 | 结论 | 证据 |
| --- | --- | --- |
| `ACTION_RISK` 不变 | **未改** | `app/integrations/minecraft/agent.py` 不在改动清单（`git status --short` 无此项） |
| `allow_medium` 默认值与真实配置不变 | **未改** | `app/config/settings.py` 未改；未触碰 `config/config.yaml` |
| 19 个工具与其 schema 不变 | **未改** | `app/tools/registry.py`、`app/tools/builtins/*` 未改 |
| TaskRuntime 状态机/生命周期/预算/步骤授权不变 | **未改** | `app/tasks/runtime.py`、`app/tasks/validation.py` 未改 |
| 两道确认门、可信玩家规则、`TurnOrigin` 派生不变 | **未改** | 未改相关文件；控制命令仍由既有入口处理 |
| 木材计划冻结目标/期望方块/掉落物绑定/库存增量验证不变 | **未改** | `app/tasks/planner.py` 未改 |
| SAFE 观察不可由模型编造数据替代 | **满足** | 解释器只输出意图字段；坐标/方块 ID 由既有 `parse_wood_request` + 支持树种矩阵 + SAFE 观察决定 |
| 计划预览前零动作、确认前零 action_id/零动作 | **满足** | 既有 8D 断言在注入替身后全部保持通过 |
| 否定/歧义/模型失败/schema 失败/注入不产生可执行任务 | **满足** | §8.2 表逐条 |
| 解释器无执行权限 | **满足** | `WoodIntentInterpreter` 只持有 `engine`（+ 超时/上下文上限），无 service/registry/executor/task API |
| 不新增工具/动作/权限/绕行/表/迁移 | **满足** | `app/database/database.py` 未改（无 schema/迁移变化） |

**凭据与隐私**：改动文件凭据扫描 **0 命中**；`git diff --check` **exit 0**；日志只记录原因码/枚举/耗时/文本长度；
提示词明确把玩家消息标注为「待分类的数据」，并在注入用例中验证模型输出仍必须通过严格 schema（带 `tool`/`authorized` 的输出被拒）。

## §10 本地门禁（命令与实测结果）

| 命令 | 结果 |
| --- | --- |
| `pytest tests/test_phase8e_wood_intent.py -q` | **48 passed** |
| `pytest tests/test_phase8e_baseline_repro.py -q`（修复前 → 修复后） | **2 failed, 1 passed** → **3 passed** |
| 木材五套件（8D 三套 + 8E 两套） | **183 passed** |
| 较宽回归 `-k "wood or intent or turn or task_entry or agent_plan or skill or minecraft_chat or chat_bridge or phase8"` | **767 passed, 3338 deselected** |
| 全量 `pytest tests -q` | **4105 passed**（746.06s；= 基线 4054 + 新增 51，精确吻合），exit 0 |
| `ruff check .` | All checks passed（exit 0） |
| `ruff format --check .` | **684 files already formatted**（exit 0） |
| `mypy app` | Success: no issues found in **338** source files（exit 0） |
| `git diff --check` | exit 0 |
| 凭据扫描 / schema 与迁移 diff | 0 命中 / `app/database/database.py` 未改 |

## §11 DoD 逐条（任务书 §12）

| # | 条件 | 结果 |
| --- | --- | --- |
| 1 | 基线与发布历史正确，镜像/远端/工作区可核验 | **满足**（§1 + §12） |
| 2 | 解释器独立、无工具权限、无任务创建或执行 API | **满足**（§9 最后两行） |
| 3 | 输出 schema 严格验证；非法/矛盾结果安全拒绝 | **满足**（§7 + 48 项 schema/failure 测试） |
| 4 | 肯定请求进入原有有界 Planner，无直接执行路径 | **满足**（肯定路径仍走 `plan_goal_from_user(shape_hint=wood)` → 既有 Planner/Policy/确认门） |
| 5 | 否定/歧义/模型失败/非法 schema/注入均零任务零计划零工具零 action_id | **满足**（§8.2） |
| 6 | 任务控制与活动任务状态保持原行为 | **满足**（控制命令零解释器调用；busy/状态查询交回既有入口） |
| 7 | Phase 8D/Follow-up 2 既有回归通过；非木材路由无意外变化 | **满足**（木材五套件 183 + 较宽回归 767 全绿；8D 契约测试改注入替身后**断言逐条未变**） |
| 8 | 相关回归、全量 pytest、Ruff、format、Mypy、diff check 通过，无新增意外 skip | **满足**（§10 + §12） |
| 9 | §10 安全不变量无未批准变化，无凭据或本地数据泄露 | **满足**（§9） |
| 10 | 文档、CHANGELOG、归档、CI 和发布证据齐全 | **满足**（本文件 + CHANGELOG + README + specs 归档 + §12） |
| 11 | 报告区分真实执行证据与未验证事项 | **满足**（§8 的诚实边界声明 + §13 已知限制） |

## §12 发布与 CI（实测）

| 项 | 值 |
| --- | --- |
| 全量 `pytest tests -q` | **4105 passed**（746.06s；exit 0；= 基线 4054 + 新增 51） |
| 实现提交 | **`2622311ef0bb4e895faee37959e12d7ded417426`**（`Phase 8E: LLM wood intent interpreter & safe routing`；9 文件 **+1584/−65**） |
| 提交纪律 | 单一独立实现提交；**未 reset、未改写** 8D 与 8D Follow-up 历史 |
| 远端 / 镜像 | 镜像 == `origin/main` == `2622311`；**287 commits**；工作区干净；`scripts/` 仍 **0 tracked** |
| CI run | **38546626967**（https://github.com/rinsoraa/CatooBot/actions/runs/38546626967） |
| attempt | **1**（一次通过；**无 flake、无重跑**） |
| 两个 job | `lint · format · types · tests` **success**；`webui · typecheck · tests · build` **success** |
| CI 上 pytest | **4102 passed, 3 skipped in 338.35s**（= 本地 4105 − 3 条既有平台条件性 skip；新增 **51 项无一被跳过**） |
| CI 上 Browser E2E | **7 passed (21.0s)** |
| CI 上 `minecraft_runtime` | find-blocks-test **41/41** ALL CHECKS PASSED；e2e `bridge auth boundary + runtime instance ✓`；e2e ALL CHECKS PASSED |
| CI 上 ruff format / mypy | 663 files already formatted（CI 只见镜像内文件）；Success: no issues found in **338** source files |
| 实际 SKIPPED（全部为既有假服务器限制，与本阶段无关） | equip / inventory_move STOP；container STOP；craft 成功路径；craft STOP；3×3 craft 成功路径；dropped_items / pickup 成功路径 |
| 裁决 | **PASS**（DoD 11 项全满足，见 §11） |

## §13 已知限制与未验证事项（如实区分证据等级）

**真实执行证据（自动化，可重复）**：48 项解释器/schema/失败关闭/注入测试、3 项 Step 1 复现与回归、
183 项木材五套件、767 项较宽回归、4105 项全量 pytest、Ruff/format/Mypy/diff-check/凭据扫描、
以及 CI run 38546626967 与 38547733231 的真实 job 结论。

**静态审查**：§9 的 11 条安全不变量逐项比对（`git status --short` 改动清单 + 受保护文件未改 + `database.py` 未改）。

**未验证事项（本阶段没有证据，不做任何断言）**：

1. **未对真实 provider 运行解释器**：本阶段的评估全部使用脚本化 provider（按任务书要求「不依赖实时模型服务」）。
   因此**没有**真实模型的准确率、误判分布、延迟或 token 成本数据；不得把「输出合法 JSON」当作「意图一定正确」。
2. **未覆盖英文/其他语言**的木材意图表达；提示词与示例均为中文语境。
3. **模型与确定性参数冲突的处置是刻意的 fail-closed**：`target_species` 或 `requested_count` 与 `parse_wood_request()`
   不一致时一律澄清（可能对真实模型造成偏多的澄清，但绝不产生错误计划）。
4. **真实 provider 故障模式**（限流、截断、超长响应）只在脚本化替身中覆盖（timeout/异常/空/非 JSON/截断），
   真实服务端的分布未知。
5. **多语句/长上下文**未做专门评估：`recent_context` 上限 4 条、每条 120 字，`message` 上限 240 字（截断而非报错）。
6. **真机门禁不适用**：本阶段未触碰动作/授权/执行链，按任务书 §7 声明不适用；这**不等于**真机链路被本轮验证过。
| 真机门禁 | **不适用**：本阶段只改意图判定与聊天路由，未触碰动作/授权/执行链（任务书 §7 允许按范围声明不适用） |