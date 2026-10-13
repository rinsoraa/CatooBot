# CatooBot Minecraft Phase 8D Follow-up 2
# Wood Negation Guard & No-Action Routing Closure

> **任务类型：** 小范围缺陷修复 / 独立 Follow-up 提交  
> **当前状态：** 待实现  
> **产品范围：** Minecraft 游戏内聊天入口的木材意图判断与路由  
> **基线分支：** `main`  
> **基线 SHA：** `186dac5df704f0b3ba7fea6470b946f5f8515431`  
> **前序修复：** Phase 8D Follow-up `0922c285c0380901ff463d428576acb18114c79d`  
> **基线证据提交：** `186dac5df704f0b3ba7fea6470b946f5f8515431`（记录 CI run 38413743509 的提交）

---

## 1. 任务目标

修复木材采集入口对**否定、暂缓或互相冲突的采集表达**识别不足的问题。当前已有词法规则可能把「不要砍……」「我不想砍……」判成肯定采集请求，并由既有 `TaskIntentDetector` 路径继续创建木材计划预览。

本补丁必须保证：

1. 明确否定或暂缓的木材采集表达**不会创建新 Task、不会调用木材计划服务、不会进入旧的单目标 `plan_resource_task()` 路径，也不会执行任何 Minecraft 工具**。
2. 否定表达必须在肯定请求标记（例如「要」「想」「去」「带回来」）之前作语义裁定；不能因为文本里包含肯定标记就覆盖其前面的否定。
3. 同一句中出现否定和另一个肯定采集目标、或出现无法可靠判断作用范围的双重否定时，必须 **CLARIFY / 不建任务 / 零工具执行**，不能猜测用户真正想采集什么。
4. 现有肯定请求、支持树种、非法树种拒绝、树种歧义澄清、0 数量澄清、数量上限、叙述过滤、busy/状态查询/任务控制路径以及所有安全不变量保持成立。

**本阶段只关闭意图解析与路由边界，不扩展 Minecraft 能力。**

## 2. 基线事实与问题路径

基线代码已包含：

- `app/tasks/intent.py`：`parse_wood_request()`、`is_wood_request()`、`WOOD_SPECIES`、`WOOD_UNSUPPORTED`、`WOOD_MAX_TARGETS = 2`。
- `app/integrations/minecraft/chat_bridge.py`：`_task_reply()` 在既有任务入口之前调用木材判定；`_wood_preempts()` 决定是否接管；`_wood_reply()` 处理 BLOCKED / CLARIFY / 计划创建回复。
- `tests/test_phase8d_wood_intent.py` 与 `tests/test_phase8d_wood_routing.py`：覆盖 Phase 8D 以及上一轮 Follow-up 的树种、数量、唯一接管、零动作等回归。

当前风险点：

1. `is_wood_request()` 的 `_WOOD_REQUEST_MARKERS` 包含 `"要"`、`"想"` 等子串标记；否定句可能因包含这些词被误认作肯定请求。
2. 支持树种且 `wood.problem` 为空时，`_wood_preempts()` 目前直接返回 `detector.detect(message).is_task`。旧任务检测器主要检查动作词、多步骤标记和资源词，不裁定否定语义。
3. 简单地让否定语句“不走木材分支”并不合格：它可能落回 `handler.handle()`，重新进入旧单目标资源路径；若任务入口不接管，也可能继续走角色回合与工具编排。否定护栏必须在正确位置**明确接管并返回不执行的结果**，而不是简单返回 `False`。

本补丁应以代码当前真实调用链为准，不得只修改 helper 而不修改入口路由。

## 3. 本阶段范围

### 3.1 允许修改的文件

1. `app/tasks/intent.py`
   - 新增或调整确定性的木材否定/冲突意图识别 helper。
   - 保持 `parse_wood_request()` 的原有树种、数量解析职责；除非实现确有必要，不要把路由判断、模型调用或副作用塞进解析器。
2. `app/integrations/minecraft/chat_bridge.py`
   - 将否定/冲突裁定接入 `_task_reply()` / `_wood_preempts()` / `_wood_reply()` 的实际路径。
   - 确保否定请求有专用的非执行回复及稳定路由证据，并且不会回落至旧资源模板或普通工具执行链。
3. `tests/test_phase8d_wood_intent.py`
   - 添加确定性 helper/分类矩阵用例。
4. `tests/test_phase8d_wood_routing.py`
   - 添加入口级回归，验证计划服务、任务入口和工具执行都未被错误调用。
5. `docs/MINECRAFT_PHASE8D.md`、`CHANGELOG.md`
   - 记录缺陷、修复、测试与提交/CI 证据。

任务书可归档到：`docs/specs/CatooBot Minecraft Phase 8D Follow-up 2 — Wood Negation Guard & No-Action Routing Closure.md`。

### 3.2 不允许扩大的范围

- 不新增工具、Minecraft 动作、权限类别、配置项、数据库表、迁移、持久化字段或 API。
- 不改 `ACTION_RISK`、`allow_medium` 的默认值、19 个工具注册、`TaskRuntime` 状态机、任务预算、冻结计划、授权、确认门、步骤级授权、`trusted_players`、`TurnOrigin` 或 Action Runtime 生命周期。
- 不将 `wood` 加入全局 `detect_shape()` 或以修改通用 `TaskIntentDetector` 的宽泛规则来解决单一入口问题，除非代码审计证明必须；若必须跨出上述范围，应先停止并在报告中说明原因，不要顺手重构。
- 不以 LLM/模型判断否定意图，不使用不稳定的自由文本推理作为安全闸。
- 不更改既有断言、sleep、E2E 超时、阈值、重试次数来“修绿”测试。
- 不发布本地 `scripts/`、真实配置、`data/`、数据库备份、日志、认证文件或任何凭据。

## 4. 期望的确定性行为

### 4.1 分类规则

实现一个边界明确、纯函数、可单测的判定方法。函数名由实现者根据现有风格决定，但语义至少能区分：

- **NEGATED / DEFERRED：** 对木材采集动作作了明确否定或暂缓，当前不应启动采集。
- **CONFLICTING / AMBIGUOUS：** 同一条消息同时包含否定目标与另一个肯定目标，或双重否定/修正语句导致作用范围不明确；必须澄清且不建任务。
- **NOT_NEGATED：** 没有检测到适用于采集动作的否定语义，继续既有肯定请求判断。

可以采用 enum、字面量或小型 dataclass；不要为了三个分类引入泛化 NLP 框架。否定规则必须考虑否定词相对动作词的位置和作用范围，不能用 `"不" in text` 这种全局包含检测，也不能让肯定标记先命中就忽略否定。

至少考虑以下否定/暂缓表达：`不要`、`别`、`先别`、`暂时别`、`不用`、`不必`、`不想`、`不打算`、`不需要`。具体规则应限定在木材采集动作附近，避免把普通词语中的字符误当否定。

对以下混合表达采用保守处理：`不要砍橡木，改砍云杉`、`不是不想砍橡木……`。只要规则无法可靠确认用户意图，就走 CLARIFY；不得自动选取某个树种，不得创建可执行计划。

### 4.2 入口路由顺序

路由处理需遵守以下优先级：

1. 既有消息过滤、控制命令、状态查询及当前活动任务/busy 语义保持原顺序和原行为。
2. 对本次新请求的木材意图执行否定/冲突裁定。
3. 明确否定或暂缓：给出简短的“不执行”确认，并记录稳定原因码，例如 `wood_negated_intent`，路由目标使用适当的非执行目标（建议 `CHAT_ONLY`）。必须在调用旧任务入口、计划服务和角色工具链之前完成接管。
4. 否定范围或目标冲突：回复用户需要澄清，原因码可使用 `wood_negation_ambiguous`，路由目标 `CLARIFY`。同样必须零计划、零任务、零工具执行。
5. 其余消息再进入现有逻辑：支持请求进入已有有界木材计划；不支持树种保持 `BLOCKED`；真多树种保持 `CLARIFY`；0 根保持 `CLARIFY`；叙述与纯查询保持既有语义。

**关键要求：** 不得把“检测到否定”实现为 `_wood_preempts() == False` 后直接交回 `handler.handle()`。在新任务场景中，这样做可能重入旧的 `plan_resource_task()`，并未解决缺陷。必须证明否定分支由 Minecraft 聊天唯一接管点处理完毕。

### 4.3 当前活动任务的边界

- 现有 `detector.control_command(message)` 必须继续优先处理。
- 存在未终态任务时，保持现有 busy/任务控制路由；本补丁不另行实现“自然语言否定自动取消运行中任务”的新能力。
- 有活动任务时的状态查询仍由现有任务入口处理。
- 不要让新否定分支截获既有的明确“确认/取消/暂停/继续”命令。

## 5. 必须覆盖的回归矩阵

### A. 明确否定/暂缓 —— 不建任务、零执行

| 输入 | 预期 |
|---|---|
| `不要砍两根橡木原木带回来` | `NEGATED`；非执行确认；不创建计划 |
| `我不想砍两根橡木原木带回来` | `NEGATED`；不被“想”误判为肯定请求 |
| `别砍两根橡木原木带回来` | `NEGATED`；不创建计划 |
| `先别砍两根橡木原木带回来` | `NEGATED`；不创建计划 |
| `我不打算砍两根橡木原木带回来` | `NEGATED`；不创建计划 |
| `不用帮我砍两根橡木原木带回来` | `NEGATED`；“帮我”不能覆盖“不用” |
| `不要砍两根竹子带回来` | 否定优先；不得因竹子不支持而回复采集拒绝，不建任务、不执行 |
| `不要砍树` | `NEGATED`；不得因为未指定树种而生成观察/采集计划 |

对每条路由用例均应验证：

- `WoodPlans.calls == []`；
- 旧 `TaskTurnHandler` 的创建/资源模板路径未被调用（可 monkeypatch `plan_resource_task()` 为抛异常函数）；
- `env.tools.executor.traces == []`；
- 不存在新增的非终态 Task，不产生 `action_id`；
- 有一条稳定原因码的路由记录，记录不含聊天原文或其他敏感内容。

### B. 含冲突/双重否定 —— 澄清且零动作

至少测试：

- `不要砍橡木，改砍云杉` → `CLARIFY`，不选任一树种，不调用计划服务。
- 一个含否定词与肯定采集动作但作用范围不明确的表达 → 保守 `CLARIFY`。
- 一个双重否定/纠正句，例如 `不是不想砍两根橡木原木带回来` → 不创建计划、不调用工具；如果实现无法明确解析，必须澄清。

验证同 A：零计划、零旧资源模板调用、零工具 trace、无新 Task/action_id。

### C. 肯定请求必须保持可用

- `去砍两根橡木原木带回来` → 仍进入现有 wood plan path，`shape_hint="wood"`，生成待确认计划预览；在确认之前零动作。
- `帮我砍两根橡木原木带回来` → 同样进入 wood plan path。
- `去砍两根竹子带回来` → 仍得到既有 `wood_unsupported_species` / `BLOCKED`，而不是被否定护栏吞掉。
- `去砍两根橡木和云杉原木带回来` → 仍得到既有多树种 `CLARIFY`，不自动选树种。
- `砍0根橡木原木` → 仍得到 `wood_zero_count` / `CLARIFY`。
- `帮我砍五根橡木原木带回来` → 冻结计划仍最多两根，确认前摘要仍如实提示上限。

### D. 叙述、查询与普通聊天不得误接管

- `我昨天砍了两根竹子` → 既有叙述语义，不创建计划。
- `我刚刚砍了两根竹子` → 既有叙述语义，不创建计划。
- `附近有什么木头？` → 纯查询，不创建采集任务。
- `我不想砍木头，我只是问附近有什么树`（若 parser 仍将其视为木材相关文本）→ 不创建采集任务、不调用工具；回复按安全且一致的聊天/澄清语义处理。

### E. 控制、busy 与活动任务回归

继续运行原有测试并补最小必要场景，确保：

- 明确控制命令仍优先进入任务入口；
- 已有未终态任务不因否定护栏而创建第二个计划；
- 有任务时的状态查询仍由既有入口处理；
- 无活动任务时，否定护栏不会被任务入口再次解释为新的正向采集意图。

## 6. 技术实现要求

1. 先对基线代码做只读审计，画清 `_reply()` → `_task_reply()` → `_wood_preempts()` → `_wood_reply()` / `handler.handle()` 的实际分支；在报告中指出否定拦截的精确位置。
2. 分类 helper 必须是确定性的纯逻辑，不读配置、不访问网络、不调用模型、不操作数据库，不产生动作副作用。
3. 分类规则优先考虑否定词与采集动作的局部关系。避免：
   - 任何包含 `不` 的消息一律判否定；
   - 仅检查“要/想/去/带回来”便覆盖否定；
   - 命中否定后只返回 `False` 并落回旧任务入口；
   - 对含冲突意图的请求选一个树种继续规划；
   - 通过修改全局 `_ACTION_VERBS`、`_MULTI_STEP_MARKERS` 或资源表来绕开路由问题。
4. 对明确否定和冲突意图，使用专用稳定 reason code；只保存现有允许的路由元数据（如文本长度），不记录原始聊天内容。
5. 不改变正常肯定木材请求的冻结计划、确认前零执行、授权、Policy、Action Runtime、库存增量复核逻辑。
6. 新测试必须是确定性的，不用 sleep，不依赖真实 LLM 输出；入口回归使用项目现有 `stack()` / 替身方式，并在关键 case 中断言零工具 trace。

## 7. 建议的实施步骤与提交纪律

### Step 0 — 锁定基线

- 确认实际 `HEAD == origin/main == 186dac5df704f0b3ba7fea6470b946f5f8515431`；若已前进，停止套用旧基线并报告真实 HEAD。
- 确认工作区干净，确认已有未提交改动并非本阶段内容时先停止，不覆盖、不 reset。
- 记录 `git status --short`、`git rev-parse HEAD`、`git remote -v`、当前 CI 基线。

### Step 1 — 先写失败测试

- 为 `is_wood_request`/新增分类 helper 添加否定与冲突矩阵。
- 为 `chat_bridge` 添加否定请求的入口级回归，先证明当前缺陷可复现：不得产生计划、旧资源模板调用或工具执行。
- 确认新增测试在旧实现上按预期失败；保留失败证据。

### Step 2 — 最小实现

- 加入局部、确定性的否定/冲突分类。
- 在唯一 Minecraft 聊天接管点完成非执行回复与路由。
- 确保活动任务/控制命令优先级及旧肯定路径不变。
- 不修改任何工具动作、Planner 执行模板、TaskRuntime 或安全策略文件。

### Step 3 — 分层验证

按顺序执行：

1. 新增 helper/意图解析测试；
2. `tests/test_phase8d_wood_routing.py`；
3. 原 Phase 8D 三套件：
   `pytest tests/test_phase8d_wood_intent.py tests/test_phase8d_wood_plan.py tests/test_phase8d_wood_routing.py -q`
4. 相关回归，至少保留上一轮查询：
   `pytest tests -q -k "wood or intent or turn or task_entry or agent_plan or skill or minecraft_chat or chat_bridge or phase8"`
5. 全量：`pytest tests -q`；
6. `ruff check .`、`ruff format --check .`、`mypy app`。

报告中给出每条命令的退出码和完整统计，不用“通过”二字替代数字。测试总数需与新增加的测试用例数量、跳过项和基线统计相互吻合。

### Step 4 — 范围与安全复核

- `git diff --check`；检查完整 diff、改动文件名单及凭据扫描。
- 确认 `ACTION_RISK`、`allow_medium` 默认值、19 工具注册、TaskRuntime/预算/授权/确认门/Action Runtime/数据库 Schema 均未被修改。
- 检查 `scripts/`、`config/config.yaml`、`data/`、数据库、认证文件和本地日志没有进入暂存区或发布镜像。
- 若差异意外触碰 planner 执行模板、实际工具动作、权限/授权或 Runtime 生命周期：停止发布，解释原因；本任务书不授权扩大范围。

### Step 5 — 发布与 CI

- 按仓库既有双目录/镜像流程发布；保持一个独立实现提交，不 reset、不改写前序历史。
- 运行 CI。若只有既有环境敏感 E2E 失败，先保留失败证据，再严格按现有纪律只重跑失败 job；不得改断言、超时、阈值或重试策略来换绿。
- 最终证据文档记录实现 SHA、最终证据 SHA、run ID、attempt、每个 job 结果、测试统计、镜像/`origin/main` 一致性以及工作区状态。

## 8. 真机门禁裁定

本补丁原则上只改**确定性意图分类与聊天路由**，不改实际 Minecraft 动作和计划执行链，因此核心验收以入口集成回归、零工具 trace、全量测试和 CI 为准；不要求仅为词法变更再次重复完整七场景真机采集门禁。

但如果实现 diff 触碰下列任一项：`planner.py` 的木材执行计划、`agent.py` / `ACTION_RISK`、工具实现、`TaskRuntime`、授权、动作执行或 `minecraft_runtime`，则已超出本阶段范围。不得以“只是小改动”跳过检查；应停止当前范围扩展并单独说明。若经批准确实要改动作执行路径，必须重跑 Phase 8D 对应真实 Java/Fabric/Mineflayer 门禁，不能用 mock、SKIPPED 或测试替身替代必需的真实成功场景。

## 9. 完成定义（Definition of Done）

以下项目全部满足才允许裁定 **PASS**：

- [ ] 否定/暂缓/冲突识别有确定性规则与单测，否定语义不会被肯定标记覆盖。
- [ ] `不要砍两根橡木原木带回来`、`我不想砍两根橡木原木带回来` 等否定用例不会创建 Task 或计划。
- [ ] 否定用例不触发旧 `plan_resource_task()`，计划服务不被调用，工具 trace 为空，没有 action_id。
- [ ] 不支持树种的否定表达优先按否定处理；肯定的不支持树种仍为 `BLOCKED`。
- [ ] 否定和肯定目标冲突、双重否定或作用范围不清楚时走 `CLARIFY`，零任务、零工具执行。
- [ ] 原有支持肯定请求、树种歧义、数量边界、0 根、确认前零执行、上限提示测试保持通过。
- [ ] 原有闲聊/查询/控制/busy/状态查询回归通过。
- [ ] 相关回归、全量 `pytest tests -q`、Ruff、格式检查、Mypy 全部通过；无新增非预期 skip。
- [ ] `git diff --check` 与凭据扫描通过；改动范围和安全不变量核查通过。
- [ ] 实现提交已发布；最终 SHA 与 `origin/main` 及发布镜像一致；CI 两个 job 成功（如合法重跑，记录两个 attempt 的真实结果）。
- [ ] 文档/CHANGELOG 已记录修复和证据；工作区干净；本地脚本/配置/数据/凭据未发布。

任何一项失败均不得整体 PASS：根据证据记为 `PARTIAL` 或 `FAIL`，并明确未通过项。不得以 CI 绿色代替否定路由的行为验证。

## 10. 最终报告格式

实现者提交以下报告：

1. **Verdict**：PASS / PARTIAL / FAIL，以及逐条 DoD 结果。
2. **Baseline & Commit Chain**：基线 SHA、实现 SHA、最终证据 SHA；说明没有重写历史。
3. **Root Cause & Fix**：否定/冲突分类规则、入口拦截位置、为何不会回落至旧任务路径。
4. **Behavior Matrix**：按第 5 节列出每一类输入及真实结果。
5. **No-Action Evidence**：对否定/冲突用例提供 `plans.calls == []`、旧资源模板未调用、`executor.traces == []`、无新 Task/action_id 的断言结果。
6. **Regression & Quality Gates**：命令、退出码、passed/failed/skipped 数字，Ruff/format/Mypy 统计。
7. **Security & Scope Audit**：改动文件清单、安全不变量、凭据扫描、未跟踪/本地文件核查。
8. **CI & Publish Evidence**：run ID、attempt、两个 job 的结论、最终 HEAD/镜像/`origin/main` SHA、工作区状态。
9. **Known Limitations**：尚未覆盖的自然语言否定形式必须如实列出；不得宣称完成通用自然语言理解。

---

## 给实现代理的执行指令

请以 `186dac5df704f0b3ba7fea6470b946f5f8515431` 为基线执行本任务书。先复核真实 HEAD 和工作区，不要 reset、不要覆盖用户改动；先添加失败测试，再做最小修复。核心目标是让木材否定/冲突表达在 Minecraft 聊天唯一接管点得到非执行处理，**不得简单返回 False 后落回旧 `TaskTurnHandler` 或角色工具链**。保持 Phase 8D 已验收行为和所有安全不变量，完成全量门禁、提交、CI、文档证据，再按第 10 节交付结束报告。
