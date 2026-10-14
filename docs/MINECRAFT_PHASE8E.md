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

## §14 Phase 8E Follow-up — 路由闭环与日志隐私收口

**基线**：`2b667b0127f97d9ed61c264aed5463757f223aba`（Phase 8E 最终证据提交；Step 0 只读审计实测镜像 `HEAD == origin/main == 基线`、工作区干净、289 commits）。

**任务书归档**：`docs/specs/CatooBot Minecraft Phase 8E Follow-up — 木材意图路由闭环与异常日志隐私收口.md`（26886 bytes / 367 行，SHA256 `A399DA0B2DB06F406E9499F1D62CE31ECB205D39C8098F37EBD9F965C9669062`，逐字节一致）。

### §14.1 缺陷（修复前，真实入口复现）

`_wood_intent_reply()` 对 `QUERY` / `NARRATION` / `TASK_CONTROL` / `OTHER` 返回 `None`，而 `_task_reply()` 把 `None` 当成「交回任务入口」→ 真实 `TaskTurnHandler._create()`（`turn.py:503`）仍可调用旧单目标 `plan_resource_task()`。

**修复前实测**：`pytest tests/test_phase8e_followup_routing_closure.py -q` → **4 failed, 11 passed**，失败点正是 `PlanResourceSentinel: plan_resource_task 不该被调用`（回溯落在 `turn.py:503`）。

配套缺陷（日志）：`app/tasks/wood_intent.py` 的 context/provider 失败用 `log.warning(..., exc)` 输出**异常正文**；`chat_bridge.py` 有 7 处 `log.exception(...)`（自动附带 traceback 与异常字符串）；「回复发送失败」直接打 `exc`。

### §14.2 修复：显式路由结果（不再用 `None` 承担多种安全语义）

`app/integrations/minecraft/chat_bridge.py` 新增模块级 `_WoodRoute`（StrEnum，六值）与 `_WoodRouteDecision`（frozen dataclass：`route` + `reply`），并把 `_wood_intent_reply()` 重写为返回结论的 `_wood_route()`。

| `_WoodRoute` | 语义 | 调用方行为 |
| --- | --- | --- |
| `NOT_WOOD` | 这条消息不归木材分支管 | 原样走既有入口 |
| `HANDLED` | 木材分支已生成回复（否定） | 直接回复，**终止**任务路由 |
| `NORMAL_CHAT` | 查询/叙述：普通角色对话 | `_task_reply()` 直接 `return None` —— **不经过**任务入口与 `_plan_reply()` |
| `DELEGATE_CONTROL` | 控制命令 / 状态查询 / 活动任务 busy | 交回既有确定性控制入口（§4.3） |
| `WOOD_POSITIVE_REQUEST` | 仅 `WOOD_COLLECTION_REQUEST + POSITIVE` | 进入木材专用计划链（仍过确定性参数校验） |
| `SAFE_CLARIFY` | 歧义 / 需澄清 / 模型自称控制 / OTHER / 未知 / 解释器缺失或失败 | 澄清并**终止**任务路由 |

`_task_reply()` 的木材块因此变成：`NORMAL_CHAT → return None`；有 `reply` → 直接返回；其余（仅 `DELEGATE_CONTROL`）才继续调用 `handler.handle()`。

**控制命令优先级未变**：`_wood_intent_preempts()` 仍在解释器之前拦截控制词/状态查询/活动任务，控制权始终由确定性检测器给出。

### §14.3 路由矩阵（每条分类 → 目标路由 → 副作用）

| 模型/故障输入 | 目标路由 | 计划服务 | 旧 `plan_resource_task` | TaskRuntime 创建 | 工具 trace / action_id |
| --- | --- | --- | --- | --- | --- |
| `WOOD_COLLECTION_REQUEST + POSITIVE`（参数一致） | `WOOD_POSITIVE_REQUEST` → 既有木材计划链 | 1 次（正常链路） | 0 | 按既有语义创建待确认任务 | 确认前 0；确认后走既有授权链 |
| `NEGATED_REQUEST + NEGATIVE` | `HANDLED`（明确不做） | 0 | 0 | 0 | 0 / 无 |
| `QUERY` | `NORMAL_CHAT` | 0 | 0 | 0 | 0 / 无 |
| `NARRATION` | `NORMAL_CHAT` | 0 | 0 | 0 | 0 / 无 |
| `OTHER` | `SAFE_CLARIFY` | 0 | 0 | 0 | 0 / 无 |
| `AMBIGUOUS_INTENT` / `needs_clarification=true` | `SAFE_CLARIFY` | 0 | 0 | 0 | 0 / 无 |
| `TASK_CONTROL`（确定性检测器未识别） | `SAFE_CLARIFY` | 0 | 0 | 0 | 0 / 无 |
| timeout / exception / 空 / 非 JSON / schema 失败 | `SAFE_CLARIFY` | 0 | 0 | 0 | 0 / 无 |
| 确定性控制命令 / 状态查询 / busy | `DELEGATE_CONTROL` → 既有入口 | 0 | 0（控制路径不走 `_create`） | 0 | 0 / 无 |
| 非木材消息 | 不进入木材分支 | 0 | 0 | 0 | 0 / 无 |

### §14.4 真实入口测试证据（任务书 §6.1/§6.2）

`tests/test_phase8e_followup_routing_closure.py`（**15 项**）：使用**真实** `TaskRuntime`（`InMemoryTaskStore`）+ **真实** `TaskTurnHandler`（复用 `tests/test_task_turn.py` 的既有装配 `make_runtime` / `observations`）+ **真实** `MinecraftChatBridge`（`tests/test_minecraft_chat_bridge.stack`）；只替换解释器与计划层替身。

覆盖：非肯定矩阵（`OTHER` / `QUERY` / `NARRATION`，含表面肯定句与真查询，参数化 5 例）、澄清矩阵（`AMBIGUOUS_INTENT` / `needs_clarification=true` / 模型自称 `TASK_CONTROL`）、故障矩阵（`TimeoutError` / `RuntimeError` / `result=None`）、`NEGATED_REQUEST`、`QUERY` 走普通对话并断言 `route_target == CHAT_ONLY`、控制命令（「取消，别砍树了」）与非木材消息（解释器零调用）。

**Sentinel 与副作用（§6.2 逐项）**：

| 断言 | 实现方式 | 结果 |
| --- | --- | --- |
| `app.tasks.turn.plan_resource_task` sentinel | `monkeypatch.setattr(turn_module, "plan_resource_task", boom)`（抛 `PlanResourceSentinel`） | **0 次调用** |
| 木材计划服务 | `PlansStub.plan_goal_from_user` 计数且被调用即失败 | **0 次调用** |
| TaskRuntime 创建 | 包装 `runtime.create_task` 计数 | **0 次** |
| 任务是否存在 | `await runtime.active(SESSION) is None` | **确认为 None** |
| 工具 trace | `env.tools.executor.traces == []` | **空** |
| 工具执行 | `invoke` 替身在**任何**工具调用时抛断言错误 | 从未触发（= 无 action_id 的前提） |

### §14.5 日志隐私证据（任务书 §6.5）

`tests/test_phase8e_followup_log_privacy.py`（**4 项**）。敏感标记：`SENSITIVE_SENTINEL_PROVIDER_BODY_8EFU`、`SENSITIVE_SENTINEL_CONTEXT_8EFU`、`SENSITIVE_SENTINEL_ROUTE_8EFU`、`SENSITIVE_SENTINEL_SCHEMA_8EFU`。

| 注入点 | 断言 | 结果 |
| --- | --- | --- |
| provider 抛带标记异常 | 日志不含标记；含 `error_type=RuntimeError`；返回安全澄清 | **通过** |
| 上下文构造故障（`__str__` 抛标记异常） | 日志不含标记；含 `error_type=RuntimeError` | **通过** |
| schema 失败（标记在字段值里） | 日志不含标记；含 `schema rejected`（只字段路径与错误类型） | **通过** |
| 入口层解释器抛带标记异常 | 日志不含标记；含 `error_type=RuntimeError`；玩家只收到澄清文案 | **通过** |

**允许记录**：阶段/组件、**异常类型名**、稳定 `reason_code`、`text_chars`、schema 字段路径与错误类型摘要。
**禁止**：`str(exc)`、`repr(exc)`、`log.exception` 的 traceback、provider 正文、prompt、用户完整消息。

本轮把 `chat_bridge.py` 的 **7 处** `log.exception` 与 1 处 `%s, exc`、`wood_intent.py` 的 **2 处** `%s, exc` 全部改为类型名/稳定原因码。

### §14.6 变异验证（任务书 §7）

| 变异 | 做法 | 结果 | 恢复后 |
| --- | --- | --- | --- |
| 路由回退 | 把 `_wood_route()` 的 `QUERY/NARRATION` 分支改回 `DELEGATE_CONTROL`（旧回落语义） | **2 failed, 13 passed**，`PlanResourceSentinel` 命中（`plan_resource_task` 被调用） | **15 passed** |
| 日志回退 | provider 日志改回 `log.warning("...: %s", exc)` | **1 failed, 3 passed**，捕获日志中出现 `SENSITIVE_SENTINEL_PROVIDER_BODY_8EFU` | **4 passed** |

两次变异只在本工作树临时进行；恢复后 `Select-String -Pattern MUTATION` **无残留**，`ruff check .` 仍 All checks passed。

### §14.7 门禁（命令与实测结果）

| 命令 | 结果 |
| --- | --- |
| `pytest tests/test_phase8e_followup_routing_closure.py -q` | **15 passed**（修复前 4 failed / 11 passed） |
| `pytest tests/test_phase8e_followup_log_privacy.py -q` | **4 passed** |
| 木材全套（8D intent/plan/routing + 8E intent/repro + 本轮两套） | **202 passed** |
| 较宽回归 `-k "wood or intent or turn or task_entry or agent_plan or skill or minecraft_chat or chat_bridge or phase8"` | **786 passed, 3338 deselected** |
| 全量 `pytest tests -q` | **4124 passed**（568.54s；= 8E 基线 4105 + 本轮新增 19，精确吻合） |
| `ruff check .` | All checks passed（exit 0） |
| `ruff format --check .` | **686 files already formatted**（exit 0） |
| `mypy app` | Success: no issues found in **338** source files（exit 0） |

**被按新契约收紧的 3 处既有断言**（不是放宽）：`tests/test_phase8d_wood_routing.py` 两处与 `tests/test_phase8e_wood_intent.py` 一处原先断言「查询/叙述必须交回既有入口」（`turns.calls == 1 / == 2`），即本次修复的缺陷本身；现改为 `turns.calls == 0` 并新增 `route_target == CHAT_ONLY` 证据。

### §14.8 安全不变量（逐项）

未改动：`app/integrations/minecraft/agent.py`（`ACTION_RISK`）、`app/config/settings.py`（`allow_medium` 默认）、`app/database/database.py`（迁移）、`app/tasks/runtime.py`（状态机）、`app/tasks/validation.py`（预算）、`app/tasks/planner.py`（木材冻结目标/拾取引用/库存增量）、`app/tools/registry.py` 与 `builtins/` 工具 schema。

`app/tasks/wood_intent.py` 本轮只改**日志**（schema/allowlist/validator/`SYSTEM_PROMPT` 未改）。未新增工具/动作/表/迁移/配置项；两道确认门、步骤授权、`trusted_players`、`TurnOrigin` 派生与 Action Runtime 生命周期未触碰。

### §14.9 裁决与限制

**本轮裁决：PASS（本节范围内）** —— §6.1 真实入口防回落、§6.2 副作用计数、§6.5 日志隐私与 §7 变异验证全部取得自动化证据；Phase 8E 的 PARTIAL 在本 Follow-up 关闭（发布与 CI 证据见 CHANGELOG 与提交链）。

限制（不夸大）：真实 provider 仍未运行（解释器行为只在脚本化替身下验证）；查询/叙述改走普通对话后由角色回合回答，其**内容质量**不在本阶段范围；`OTHER → SAFE_CLARIFY` 是本轮选定的明确策略（§4.2 允许「普通对话或安全澄清」，此处选澄清以免对看似动作请求的消息静默忽略）。

### §14.10 发布与 CI（实测）

| 项 | 值 |
| --- | --- |
| 实现提交 | **`f1ef44532102db2605366ef0c015c646b9b6c758`**（`Phase 8E Follow-up: wood routing closure & log privacy`；9 文件 **+1069/−46**） |
| 提交纪律 | 独立实现提交；**未 reset、未 force-push、未改写** 8D / 8D Follow-up / 8D Follow-up 2 / 8E 历史 |
| 远端 / 镜像 | 镜像 == `origin/main` == 实现提交；**290 commits**；工作区干净；`scripts/` **0 tracked** |
| CI run | **38554613584**（https://github.com/rinsoraa/CatooBot/actions/runs/38554613584） |
| attempt | **1**（一次通过；**无 flake、无重跑**） |
| 两个 job | `lint · format · types · tests` **success**；`webui · typecheck · tests · build` **success** |
| CI 上 pytest | **4121 passed, 3 skipped in 325.88s**（= 本地 4124 − 3 条既有平台条件性 skip；新增 19 项**无一被跳过**） |
| CI 上 Browser E2E | **7 passed (21.3s)** |
| CI 上 `minecraft_runtime` | find-blocks-test ALL CHECKS PASSED；e2e `bridge auth boundary + runtime instance ✓`；e2e ALL CHECKS PASSED |
| CI 上 ruff format / mypy | 665 files already formatted（CI 只见镜像内文件）；Success: no issues found in 338 source files |
| 改动文件清单 | `app/integrations/minecraft/chat_bridge.py`、`app/tasks/wood_intent.py`、`tests/test_phase8e_followup_routing_closure.py`（新增）、`tests/test_phase8e_followup_log_privacy.py`（新增）、`tests/test_phase8d_wood_routing.py`、`tests/test_phase8e_wood_intent.py`、`docs/MINECRAFT_PHASE8E.md`、`CHANGELOG.md`、任务书归档 |
| 受保护文件 | `git diff --name-only` 对 `agent.py`（`ACTION_RISK`）、`config/settings.py`（`allow_medium`）、`database/database.py`（迁移）、`tasks/runtime.py`、`tasks/validation.py`、`tasks/planner.py`、`tools/registry.py`、`tools/builtins/` **全部零命中** |
| 真机门禁 | **不适用**（本轮只改分类路由与日志，未触碰动作/授权/执行链；按任务书 §10.8 声明范围，**不等于**真机链路经本轮验证） |
| 裁决 | **PASS**（本节范围；Phase 8E 的 PARTIAL 由此关闭） |

## §15 Phase 8E Follow-up 2 — 非肯定木材路由的角色回合能力隔离与零副作用闭环

## §16 Phase 8E Follow-up 3 — 通用 LLM 回合决策契约与统一路由

基线 `4acd53d2fa559cf4e10ef4cd62f9f667c29f1fc2`（8E Follow-up 2 最终证据提交，293 commits）。

### §16.1 被修的架构问题（§4.1 明令禁止的形状）

改动前的 `_task_reply` 是「先看消息里有没有木材词」：`if mentions_wood(message):` → 木材专用判定；
否则 `handler.handle()`（既有任务入口）→ 未接管再 `_plan_reply`（follow/explore）→ 仍未接管才轮到角色回合。
也就是说：**只有含「木头」的消息经过专用判断**，其余消息的去向由一个**硬编码的分支顺序**决定，
没有任何统一的、类型化的「这句话想让我做什么」的决策结论，也没有可审计的 decision 证据。

### §16.2 统一入口调用链（改动后）

```
minecraft.chat → MinecraftChatBridge._reply → _task_reply
  1) _control_turn：确定性控制优先（控制词句首 / detector.control_command；活动任务的
     状态查询；未终态 busy）→ 直达既有任务入口，**绝不等待模型**（§7.1）
  2) _decide_turn：TurnDecisionInterpreter.decide(message, recent_context) → 契约校验后的 TurnDecisionOutcome
  3) 决策失败（超时/异常/空/非法 JSON/schema）→ _decision_clarify 安全澄清（**绝不降级进执行路径**）
  4) 木材消息（mentions_wood）→ 既有确定性木材链 _wood_route（§4.3：正向仍走既有校验+计划链；
     否定/歧义/需澄清 = 明确不做或澄清；DELEGATE_CONTROL → 直达既有任务入口）
  5) _route_turn：按 kind 分流九类 → CHAT/NARRATION→CHAT_ONLY 角色回合；CLARIFY→澄清；
     UNSUPPORTED→BLOCKED；QUERY/ATOMIC_ACTION→_run_capability（单个已审计能力）；
     TASK_PROPOSAL→既有任务入口优先，再计划层，最后 BLOCKED；CANCEL/STOP→既有控制路径
```

九类路由与副作用（`tests/test_phase8e_followup3_routing_matrix.py`，15 项，全绿）：

| kind | 落点 | 工具 | Agent 面 | 任务 | action_id |
| --- | --- | --- | --- | --- | --- |
| CHAT / NARRATION | CHAT_ONLY 角色回合 | 0 | 0 | 0 | 无 |
| QUERY | 单个只读白名单能力 | 恰好 1 次 | 0 | 0 | 视能力而定 |
| ATOMIC_ACTION | 单个动作白名单能力 | 恰好 1 次 | 0 | 0 | 视能力而定 |
| TASK_PROPOSAL | 既有任务入口 → 计划层 | 0 | 0 | 既有链 | 既有链 |
| CLARIFY / UNSUPPORTED | 澄清 / BLOCKED | 0 | 0 | 0 | 无 |
| CANCEL / STOP | 既有控制路径 | 0 | 0 | 既有语义 | 无 |
| 决策失败 | 安全澄清（decision_* 原因码） | 0 | 0 | 0 | 无 |

### §16.3 类型化决策契约（`app/tasks/turn_decision.py`）

* `TurnDecisionKind` 九值：`chat/query/narration/atomic_action/task_proposal/clarify/unsupported/cancel/stop`。
* `TurnDecision` 字段**恰好 6 个**（`kind/reply_text/capability_id/arguments/task_goal/clarification_question`），
  `extra=forbid`；文本单行化、去控制字符、限长；`arguments` ≤8 键、键名受限、值只允许标量/标量列表。
* 能力白名单**只有 5 个已审计能力**：只读 `minecraft_world/find_blocks/inventory/look_at` + 动作 `minecraft_stop`；
  `minecraft_dig/place/attack/craft/container` **不在其中**（契约层直接 schema 失败）。
* 禁止模型自授权限：`action_id/authorized/risk/allow_medium/trusted/executed/success` 等字段一律被拒。
* 失败原因码由**可信代码**生成：`decision_model_error/timeout/empty_response/invalid_json/schema_invalid/
  decision_engine_unavailable/unsupported_capability/parameter_validation_failed`；有界重试（≤2）且**保留首次真实原因**。
* 解析严格：整段 JSON → 围栏 → 单对象候选；**绝不从任意文本里抠 JSON 交给执行器**。

### §16.4 安全边界（不新增旁路）

* QUERY/ATOMIC_ACTION 走既有 `ToolExecutor.execute`（**它自己就做 schema 校验 + `policy.authorize`**）→
  ToolRegistry → ToolPolicy → MinecraftAgentBridge → MinecraftService → 既有 Action Runtime；未新增任何执行路径。
* `ToolContext` 用既有 `CharacterRuntime._tool_context(turn_origin=TurnOrigin.USER)`（意图键由可信来源派生，调用方无法手写）。
* 角色侧能力隔离继续用 FU2 的**每回合参数** `CharacterTurnMode.CHAT_ONLY`（不写任何全局开关）。
* `ACTION_RISK`、19 工具、`allow_medium` 默认、`trusted_players`、两道确认门、任务授权、冻结计划、
  参数指纹与 Action Runtime 生命周期**零改动**（见 §16.7 的逐文件核验）。

### §16.5 本轮修掉的真实回归（由既有测试抓出来）

1. **TASK_PROPOSAL 必须先问既有任务入口**：初版直接进 follow/explore 计划层，导致资源任务
   （如「去砍一棵橡树，挖一块原木并捡回来」）无法再创建 —— 既有 `TaskTurnHandler` 才是它的权威路径。
   现已改为「既有入口 → 计划层 → BLOCKED」，并由 `test_task_proposal_consults_the_existing_entry_first` 看住。
2. **控制词出现在句首时不等模型**（如「取消，别砍树了」）：`detector.control_command` 对该串返回空，
   因此新增句首前缀判定，直达既有入口；`test_control_prefix_never_waits_for_the_model` 断言**决策模型零调用**。
3. **8C Follow-up 的 `plan_service_unavailable` 语义保留**：形状已识别但计划层未装配时仍报该码，
   而不是笼统的 `decision_unsupported`。

### §16.6 测试与变异（M1–M7）

| 组 | 文件 | 数量 | 结果 |
| --- | --- | --- | --- |
| 决策契约 | `tests/test_phase8e_followup3_decision_contract.py` | 33 | 全绿 |
| 路由矩阵（§9.2） | `tests/test_phase8e_followup3_routing_matrix.py` | 15 | 全绿 |
| 受影响既有套件 | chat_bridge / 8A / 8C / 8D / 8E / FU1 / FU2 / 隐私 | — | 全部通过（见 §16.7） |

**§9.3 允许的最小适配（语义确已变化，一律用真实契约替身 `TurnDecisionStub`，未放宽断言）**：
`ChatStack` 新增 `decision=` 注入点（默认 CHAT）；8C 的 `wire()` 注入 TASK_PROPOSAL；
8A 多步目标用例注入 TASK_PROPOSAL；FU2 的两条「NORMAL 模式」对照断言按新架构改为「一律 CHAT_ONLY」，
并保留原有的全局开关反例断言（`tools.enabled is True`、`runtime.tools is chat.tools`）。

| 变异 | 变异点 | 结果 |
| --- | --- | --- |
| M1 绕过统一入口 | `_task_reply` 直接 `_delegate_task` | **被捕获**：2 failed |
| M2 普通聊天映射到可执行 Agent 路径 | CHAT/NARRATION 返回 `NORMAL` 模式 | **未被判别**（见 §16.8：候选检索为空 → 仍无工具/Agent 调用，属纵深防御） |
| M3 无效输出继续进执行路径 | `if False and not decision_outcome.ok` | **未被判别**（kind 兜底 CHAT_ONLY + 木材链仍拦住，属纵深防御） |
| M4 跳过既有任务入口 | TASK_PROPOSAL 直接进计划层 | **被捕获**：1 failed |
| M5 STOP/CANCEL 依赖模型 | 关闭确定性控制判定 | **被捕获**：1 failed |
| M6 异常正文写日志 | detail 追加 `error: str(exc)` | **被捕获**：1 failed |
| M7 回合决策被缓存复用 | `_decide_turn` 缓存首次 outcome | **被捕获**：1 failed |

每次变异后都按原字节恢复（`identical to original: True`），仓库内 `MUTATION` 残留 **False**。

### §16.7 门禁与安全核验（实测）

* `ruff check .` → All checks passed（exit 0）；`ruff format --check .` → **691 files**（exit 0）；
  `mypy app` → Success: no issues found in **339** source files（exit 0）。
* `pytest tests -q`（全量）→ **4182 passed in 613.86s**（exit 0；= 基线 4134 + 本轮新增 33 契约 + 15 矩阵 = 48 项，精确吻合）；无新增 skip。
* `git diff --check` exit 0；改动文件凭据扫描 **0 命中**；`scripts/` 仍 **0 tracked**。
* 受保护文件（`agent.py` / `config/settings.py` / `database/database.py` / `tasks/runtime.py` /
  `tasks/validation.py` / `tasks/planner.py` / `tools/registry.py` / `tools/builtins/`）**零改动**。
* 未新增工具/动作/表/迁移/配置项。

### §16.8 未验证事项与如实限制

* **真实 Provider：SKIPPED**。环境不调用真实模型服务，全部验证使用脚本化 provider；
  因此**没有**准确率、误判分布、延迟或 token 成本数据，**不得**用替身冒充准确率。
* M2/M3 两个变异未被自动测试判别（都是「纵深防御仍然拦住」的情形，已在 §16.6 逐条说明原因）。
  它们对应的安全属性由矩阵测试的**零副作用断言**（零工具 / 零 Agent / 零任务）持续看住。
* 上下文缺口如实记录：本阶段只把「玩家消息 + 最近 4 条会话摘要」给决策器，
  **未接入**记忆、实时世界观察、身份解析结果、背包与能力目录 —— 这些是 FU4 的范围。
* 木材质检仍在**决策之后**运行（`mentions_wood` 是确定性词法判断），
  因此木材意图不需要模型也能被安全处理；非木材能力目录的语义精度仍受 provider 能力限制。

### §16.9 裁决与发布

| 项 | 值 |
| --- | --- |
| 实现提交 | **`2b46afe690e3b3ee980e09cdbd44549e425eb4da`**（`Phase 8E Follow-up 3: general LLM turn decision contract & unified routing`；13 文件 **+2286/−28**） |
| 提交纪律 | 实现提交与证据提交**分开**；M1–M7 变异只在工作树内临时进行并逐字节恢复（`identical to original: True`，无 `MUTATION` 残留）；未 reset、未 force-push、未改写历史 |
| 远端 / 镜像 | 镜像 == `origin/main` == 实现提交；**294 commits**；工作区干净；`scripts/` **0 tracked** |
| CI run | **38590034819**（https://github.com/rinsoraa/CatooBot/actions/runs/38590034819） |
| attempt | **1**（一次通过；**无 flake、无重跑**） |
| 两个 job | `lint · format · types · tests` **success**；`webui · typecheck · tests · build` **success** |
| CI 上 pytest | **4179 passed, 3 skipped in 365.95s**（= 本地 4182 − 3 条既有平台条件性 skip；本轮新增 **48 项无一被跳过**） |
| CI 上 Browser E2E | **7 passed (22.3s)** |
| CI 上 `minecraft_runtime` | find-blocks-test ALL CHECKS PASSED；e2e `bridge auth boundary + runtime instance ✓`；e2e ALL CHECKS PASSED |
| CI 上 format / mypy | 670 files already formatted（CI 只见镜像内文件）；Success: no issues found in 339 source files |
| 真机门禁 | **不适用**（本轮只改回合决策与聊天路由，未触碰动作/授权/执行链；不得据此宣称真机链路已验证） |
| 裁决 | **PASS**（本阶段范围；真实 Provider 语义验证留待 FU4） |
* 裁决：**PASS**（本阶段范围内：所有 MC 自然语言消息经统一决策入口、九类路由全部落到既有已审计路径、
  零副作用与真实哨兵断言齐备、本地与 CI 门禁全绿）。真实 Provider 语义验证列入 FU4。

基线 `b4a05de4f8052445dda294600a1c2514c28a5cee`（实测 == `origin/main`，291 commits，工作区干净）。

### §15.1 问题：局部保证不够 —— 还剩两个执行面

Follow-up 1 只保证「不再回落到旧 `TaskTurnHandler._create()` / `plan_resource_task()`」。但 `QUERY` /
`NARRATION` 走的是 `_reply()` → `CharacterRuntime.respond()` 的**完整能力角色回合**，其中还有两个执行面：

1. **Agent 面**：`_generate()` → `_try_agent()` → `AgentRuntime.run()` 可创建多步任务；
2. **工具面**：`_generate()` → `ToolOrchestrator.run()` → 工具 runtime → Minecraft Agent Bridge。

也就是说：非肯定木材分类仍然可能经由角色回合创建任务或执行 Minecraft 动作。本阶段把保证提升为
**单回合能力隔离**：`QUERY` / `NARRATION` 允许角色自然语言回答，但**本回合**禁止 Agent 与工具编排；
其余非肯定分类（否定/歧义/需澄清/OTHER/失败）**根本不进入角色回合**。

### §15.2 实现（逐文件）

| 文件 | 改动 |
| --- | --- |
| `app/character/runtime.py` | 新增 `CharacterTurnMode`（`NORMAL` / `CHAT_ONLY`）与 `_chat_only_text()`；`respond(..., mode=)` → `_generate(..., mode=)`；**在 `route` 建立之后、`_tool_context` / Agent / 工具分支之前**插入 CHAT_ONLY 短路：只调一次 `engine.chat`，记 `route_target=CHAT_ONLY` + `reason_code="chat_only_turn"`，**绝不**进入 `_try_agent()` 或 `ToolOrchestrator.run()`。模式是**每回合参数**，不写回任何共享配置。 |
| `app/integrations/minecraft/chat_bridge.py` | 新增 `_TurnReply`（`reply` + `mode`，替换原先用 `str \| None` 暗藏语义）；`_task_reply()` 返回 `_TurnReply`：`NORMAL_CHAT` → `_TurnReply(None, CHAT_ONLY)`；`HANDLED`/`SAFE_CLARIFY`/计划失败 → 直接带回确定性回复（**不进角色回合**）；仅 `DELEGATE_CONTROL` 继续 `handler.handle()`；`_reply()` 把 `handled.mode` 作为 `mode=` 传给 `respond()`。 |

### §15.3 路由矩阵（任务书 §4.3）

| 分类 | 结果 | 角色回合 | Agent | 工具面 | 任务创建 |
| --- | --- | --- | --- | --- | --- |
| `WOOD_POSITIVE_REQUEST`（极性一致 + 参数校验通过） | 既有木材专用计划链 | 不进入 | 否 | 仅计划内既有工具 | 既有计划链（受确认门约束） |
| `NEGATED_REQUEST` | 确定性「未执行」回复 | **不进入** | 否 | 否 | 否 |
| `AMBIGUOUS_INTENT` / `MIXED` / `needs_clarification` | 安全澄清 | **不进入** | 否 | 否 | 否 |
| `OTHER` / 未知 / 不一致 / 解释器缺失或失败 | 安全澄清 | **不进入** | 否 | 否 | 否 |
| `QUERY` / `NARRATION` | 角色自然语言回答（**CHAT_ONLY**） | 进入（受限） | **否** | **否** | **否** |
| 模型自称 `TASK_CONTROL`（检测器不承认） | 安全澄清 | **不进入** | 否 | 否 | 否 |
| 确定性控制命令 / 状态查询 / busy | 既有任务入口 | 不进入 | 既有语义 | 既有语义 | 既有语义 |
| 非木材消息 | 既有完整路径 | 进入 | 既有语义 | 既有语义 | 既有语义 |

### §15.4 测试（`tests/test_phase8e_followup2_turn_mode.py`，10 项，全绿）

真实 `CharacterRuntime` / `ToolRuntime` / 真实 `TaskTurnHandler` / 真实 `MinecraftChatBridge`，只有模型、
解释器、计划层与 sentinel 是替身（**不是**永远 `handled=False` 的入口替身）。

* **§6.1 工具面反例**：模型对查询返回工具调用 JSON → 断言 `executor.traces == []`、回话里不含 `{"tool"`、
  `route_target=CHAT_ONLY` + `reason_code="chat_only_turn"`（`_chat_only_text()` 把疑似工具调用替换成安全文本）；
* **§6.1 Agent 面反例**：注入 `SpyAgent`（`enabled=True`）→ 断言 `handle_control` 与 `run` **零调用**；**对照**：
  随后一条非木材消息仍然咨询 Agent（证明探针是活的、隔离是**分类特定**的）；
* **模式是每回合参数**：捕获 `respond(mode=)` → 木材查询为 `CHAT_ONLY`、下一条普通消息为 `NORMAL`，且
  `tools.enabled` 与 `runtime.tools` 未变（**未使用全局开关**）；
* **并发隔离**：`asyncio.gather` 两条回合 → 路由证据中恰好一条 `chat_only_turn`，另一条不是；零工具执行；
* **非肯定矩阵**（参数化 6 例：`OTHER` / `NEGATED_REQUEST` / `AMBIGUOUS_INTENT` / `needs_clarification` /
  `NARRATION` 与 `QUERY` 的表面肯定句）：**零角色回合**（`turns == 0`）+ Agent 两面零调用 + 零工具 +
  零计划 + 零任务 + sentinel 零调用；
* **失败关闭**：解释器抛异常 → 零角色回合、零副作用；
* **叙述允许回答但不给能力**：`NARRATION` → `turns == 1` 且 `traces == []`、Agent 零调用。

### §15.5 变异验证（任务书 §7）

| 变异 | 结果 | 恢复后 |
| --- | --- | --- |
| **M1/M2**：`_generate()` 的 CHAT_ONLY 短路改为 `if False and ...`（即回落到 Agent/工具面） | **4 failed, 6 passed**（工具面、Agent 面、并发隔离、叙述四例精确失败） | 10 passed |
| **M3**：控制命令被木材分支抢截 | Follow-up 1 的 `test_control_commands_never_reach_the_interpreter` 等既有用例持续覆盖（本分支全绿） | — |
| **M4**：日志重新输出原文/异常正文 | Follow-up 1 的 `tests/test_phase8e_followup_log_privacy.py`（4 项）持续覆盖（本分支全绿） | — |

变异仅在本工作树临时进行，恢复后 `MUTATION` 无残留、`ruff check .` 通过。

### §15.6 门禁（命令与实测结果）

| 命令 | 结果 |
| --- | --- |
| `pytest tests/test_phase8e_followup2_turn_mode.py -q` | **10 passed**（2.62s），exit 0 |
| 相关回归 `-k "wood or intent or turn or task_entry or agent_plan or skill or minecraft_chat or chat_bridge or phase8 or character"` | **902 passed, 3232 deselected**（102.42s），exit 0 |
| 全量 `pytest tests -q` | **4134 passed**（596.75s；= 8E Follow-up 1 基线 4124 + 新增 10，精确吻合），exit 0 |
| `ruff check .` | All checks passed，exit 0 |
| `ruff format --check .` | **687 files already formatted**，exit 0 |
| `mypy app` | Success: no issues found in **338** source files，exit 0 |

### §15.7 安全不变量与范围

**零改动**：`app/integrations/minecraft/agent.py`（`ACTION_RISK`）、`app/config/settings.py`（`allow_medium` 默认）、
`app/database/database.py`（迁移）、`app/tasks/runtime.py`（状态机）、`app/tasks/validation.py`（预算）、
`app/tasks/planner.py`（木材冻结目标/掉落物与拾取引用/库存增量）、`app/tools/registry.py` 与 `app/tools/builtins/`（19 工具与 schema）。
未新增工具/动作/表/迁移/权限；`TurnOrigin.USER`、`origin_metadata`、可信玩家、Policy、风险等级、确认门与步骤授权语义均未改；
CHAT_ONLY **不是**策略或授权通道（它只关闭本回合的 Agent/Tool 调度，不授予任何权限）。

### §15.8 裁决与限制

**PASS**（本节范围）：`QUERY` / `NARRATION` 的本回合能力被显式限制为「只能生成文本」，
其余非肯定分类根本不进入角色回合；两个执行面均有反例测试与变异验证。

限制（如实）：
1. CHAT_ONLY 回合**不能**调用只读 Minecraft 工具取得实时世界数据（任务书 §1 明确的刻意取舍）；
2. 真实 provider 仍未运行（解释器与模型都是脚本化替身），因此没有准确率/延迟/token 数据；
3. 本轮只改「单回合能力模式接缝」，未重构成通用对话工具政策；
4. 真机门禁**不适用**（未触碰动作/授权/执行链），**不等于**真机链路经本轮验证。

### §15.9 发布与 CI（实测）

| 项 | 值 |
| --- | --- |
| 实现提交 | **`716225ae674c9b989a17e9b50168a26c65afacef`**（`Phase 8E Follow-up 2: per-turn capability isolation for non-affirmative wood routing`；6 文件 **+890/−12**） |
| 提交纪律 | 独立实现提交；**未 reset、未 force-push、未改写** 8D / 8D Follow-up / 8D Follow-up 2 / 8E / 8E Follow-up 历史 |
| 远端 / 镜像 | 镜像 == `origin/main` == 实现提交；**292 commits**；工作区干净；`scripts/` **0 tracked** |
| CI run | **38562249896**（https://github.com/rinsoraa/CatooBot/actions/runs/38562249896） |
| attempt | **1**（一次通过；**无 flake、无重跑**） |
| 两个 job | `lint · format · types · tests` **success**；`webui · typecheck · tests · build` **success** |
| CI 上 pytest | **4131 passed, 3 skipped in 756.13s**（= 本地 4134 − 3 条既有平台条件性 skip；新增 10 项**无一被跳过**） |
| CI 上 Browser E2E | **7 passed (18.7s)** |
| CI 上 `minecraft_runtime` | find-blocks-test / e2e `bridge auth boundary + runtime instance ✓` / e2e **ALL CHECKS PASSED** |
| CI 上 ruff format / mypy | 666 files already formatted（CI 只见镜像内文件）；Success: no issues found in 338 source files |
| 受保护文件 | `git diff --name-only` 对 `agent.py`（`ACTION_RISK`）、`config/settings.py`（`allow_medium`）、`database/database.py`（迁移）、`tasks/runtime.py`、`tasks/validation.py`、`tasks/planner.py`、`tools/registry.py`、`tools/builtins/` **全部零命中**；凭据扫描 **0 命中** |
| 真机门禁 | **不适用**（本轮只改单回合能力模式接缝，未触碰动作/授权/执行链） |
| 裁决 | **PASS**（本节范围） |