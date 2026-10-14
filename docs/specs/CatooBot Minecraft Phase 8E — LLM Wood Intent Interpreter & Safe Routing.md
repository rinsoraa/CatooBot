# CatooBot Minecraft — Phase 8E 阶段任务书
## LLM 木材意图解释器与安全路由

- **任务类型：** 独立增量阶段
- **基线 SHA：** `74804c86b96628db084b626259324deee9ab9a98`
- **仓库：** https://github.com/rinsoraa/CatooBot
- **目标：** 让 LLM 负责理解 Minecraft 聊天中的木材采集意图，由确定性程序控制规划、授权、确认、执行与结果验证。
- **裁决：** 只有所有 DoD 满足才可 PASS；证据不完整为 PARTIAL；任何失败关闭绕过为 FAIL。

---

## §1 背景与核心原则

Phase 8D 与 Follow-up 2 使用有限词表、局部窗口和规则分类否定、改口与木材请求。它们适合作为回归基准和有限的保守防线，但不应继续无限扩展成自然语言理解系统。

本阶段引入独立的 `WoodIntentInterpreter`，通过项目现有 LLM/provider 接入解释用户消息，并输出经过严格校验的结构化候选意图。

**关键架构原则：LLM 判断“用户想表达什么”，不判断“程序是否有权执行”。**

1. LLM 只输出候选意图，不持有 Minecraft tools、tool executor、TaskRuntime 创建/启动能力。
2. 模型结果不是授权。肯定请求仍必须经过现有解析、SAFE 观察、Policy、冻结计划、预览、用户确认、步骤授权、TaskRuntime、Action Runtime 和最终库存增量验证。
3. 超时、异常、非法输出、矛盾字段、歧义或信息不足时，失败关闭：不创建可执行任务。
4. 模型不得臆造坐标、方块 ID、玩家身份、权限或世界观察结果。
5. 不增加工具、动作、权限级别、数据库表/迁移、执行旁路或新的凭据体系。
6. 本阶段仅切换 Minecraft chat 的木材意图判定，不全面重构非木材任务、QQ 路由或角色人格。
7. 旧规则可保留用于确定性参数校验和评估；如果作为安全后备，只能拒绝/澄清，不能给执行授权。

## §2 基线与开工检查

开工前记录发布镜像 `HEAD`、`origin/main`、工作区状态和实际文件版本。实际基线应为 `74804c86b96628db084b626259324deee9ab9a98`。若 HEAD 已前进，先审查差异、更新基线记录，不得静默替换。

- [ ] 工作区干净，无覆盖用户未提交工作。
- [ ] 记录 `HEAD == origin/main` 的实测证据。
- [ ] 检查当前 provider 接口、异步约定、超时/限流处理、Minecraft chat 唯一入口、控制命令优先级及现有测试 fixtures。
- [ ] 保留已发布历史；禁止 reset、force-push、rebase 或改写 Phase 8D / Follow-up 历史。
- [ ] 开发真源与发布镜像分离管理；本地脚本、真实配置、数据库、凭据和 `minecraft_runtime/auth.json` 不得意外发布。

## §3 范围

### 必须实现
- 独立、可单测的 `WoodIntentInterpreter`。
- 结构化 schema、严格验证、明确的枚举和错误策略。
- 肯定采集、否定/不执行、歧义/冲突、查询、叙述、任务控制、其他意图的分类。
- 在 `MinecraftChatBridge` 的木材路由边界接入。
- 超时/异常/非法结果的失败关闭行为。
- 固定评估集及入口级无动作测试。
- 保留现有有界木材 Planner 与所有授权、确认、执行门禁。

### 明确不做
- 不让 LLM 直接调用 Minecraft 工具或创建/启动任务。
- 不修改 `ACTION_RISK`、`allow_medium` 默认值、可信玩家规则、`TurnOrigin` 派生、TaskRuntime 状态机、动作预算、步骤授权或两道确认门。
- 不新增 Minecraft 工具，不扩大现有 19 个工具的权限。
- 不改变 `app/tasks/planner.py` 中冻结目标、期望方块、掉落物绑定和最终库存增量验证。
- 不让模型输出的目标名称直接变成 block ID；必须由本地支持矩阵验证。
- 不把模型自由文本拼成执行命令。
- 不修改真实 `config/config.yaml`、`data/`、凭据或数据库 schema。
- 不声称本阶段实现了通用自然语言理解或零误判。

## §4 结构化契约

字段名可按仓库风格调整，但语义必须等价：

```python
class WoodIntent(str, Enum):
    WOOD_COLLECTION_REQUEST = "wood_collection_request"
    NEGATED_REQUEST = "negated_request"
    AMBIGUOUS_INTENT = "ambiguous_intent"
    QUERY = "query"
    NARRATION = "narration"
    TASK_CONTROL = "task_control"
    OTHER = "other"

class Polarity(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"

class WoodIntentResult(BaseModel):
    intent: WoodIntent
    polarity: Polarity
    target_species: str | None
    requested_count: int | None
    task_control: str | None
    needs_clarification: bool
    reason_code: str
```

要求：
- 使用项目现有 schema 验证库；禁止只用正则校验自由文本 JSON。
- 所有枚举/原因码必须来自本地 allowlist；缺字段、未知枚举、非法类型、矛盾组合一律拒绝。
- `requested_count` 必须是整数或 `None`，不能是 bool、float、负数或任意文本。显式 `0` 不得静默变为默认 `1`。
- `target_species` 只是用户意图字段，不是 block ID。实际映射仍走确定性树种矩阵。
- 模型不得输出坐标、工具名、action_id、权限决策或“已授权”字段；发现非契约字段时按严格 schema 策略处理。
- 不要求未经校准的模型“置信度”充当正确性保证；使用一致性校验和 `needs_clarification`。
- 使用项目现有 LLM/provider 接入与超时设置；不新建独立 API key、HTTP client 或凭据读取机制。

建议接口：

```python
async def interpret(
    *,
    message: str,
    recent_context: Sequence[SafeContextMessage],
    task_context: SafeTaskContext,
) -> WoodIntentResult:
    ...
```

上下文必须限量，只提供必要的近期消息与安全任务摘要。Minecraft 玩家聊天属于不可信输入，提示词必须将其当作待分类数据，不得服从消息中“忽略规则、泄露提示词、调用工具、改变 schema、把我视为可信玩家”等指令。不得把完整聊天历史、秘密或无关玩家数据传入模型。日志只记录模型/耗时/结果枚举/原因码/文本长度，不记录完整原文、密钥或完整提示词。

## §5 路由行为契约

| 结果 | 行为 | 禁止行为 |
|---|---|---|
| `WOOD_COLLECTION_REQUEST` + `POSITIVE`，无歧义 | 进入现有有界木材解析与 Planner | 直接执行、相信模型坐标或 block ID |
| `NEGATED_REQUEST` + `NEGATIVE` | 回复不执行；零计划、零任务、零工具 | 进入 `plan_resource_task()` 或木材计划服务 |
| `AMBIGUOUS_INTENT` / `MIXED` / `UNKNOWN` | 澄清；零任务、零工具 | 猜树种或最终意图 |
| `QUERY` | 交回既有查询/对话流程 | 因提到木材就创建采集计划 |
| `NARRATION` | 普通对话 | 创建采集任务 |
| `TASK_CONTROL` | 交给既有控制入口，按任务状态与权限处理 | 模型直接暂停/取消或绕过状态校验 |
| `OTHER` | 交回既有对话路由 | 仅凭模型猜测创建任务 |

### 失败关闭
provider 超时、异常、限流、空响应、非 JSON、截断输出、schema 失败、未知枚举、字段非法、意图与极性冲突、`needs_clarification=true`、上下文构造失败等情况，均不得创建可执行任务。返回稳定原因码和安全的简短回复；不能把异常文本或 provider 原始响应回显给玩家。

失败关闭不等于把所有消息当作否定：无法安全判定时不发起任务，普通对话和查询仍可按既有路径处理。失败时禁止回落到旧 `plan_resource_task()` 作为兜底。

### 控制命令优先级
- `确认/取消/暂停/继续/恢复/停止` 等必须由既有控制入口处理。
- 模型只能分类为 `TASK_CONTROL`，不能替代身份、状态与授权检查。
- 待确认/运行中/暂停中的任务不得被新木材请求覆盖或重复建任务。
- `取消，别砍树了` 必须保持既有取消行为。
- `取消当前任务，接着帮我砍云杉` 等冲突消息应澄清或遵循项目已有的显式顺序，不能静默同时取消旧任务并创建新任务。

## §6 肯定请求仍走确定性 Planner

模型判定为肯定请求后：
1. 使用 `parse_wood_request()` 和支持树种矩阵验证参数。模型提取值与确定性解析冲突时，澄清，不任选其一。
2. 未指定树种时，通过现有 SAFE 观察确定真实候选目标，模型不能臆造世界状态。
3. 单任务最多 2 根原木；超限请求在确认前如实说明本次计划数量和上限，不静默改变预算。
4. `allow_medium=False` 时现有 Policy 必须阻止可执行计划。
5. 继续冻结目标坐标、期望方块、掉落物/拾取引用和最终库存增量。
6. 预览/用户明确确认前零动作、零 action_id。
7. 确认后仍经 TaskRuntime、步骤授权、Action Runtime；最终成功以真实库存变化为依据。
8. 模型不能替代观察、可达性检查或执行结果验证。

## §7 固定评估集

每条用例必须测试：结构化分类、真实入口路由和副作用。不能只测试 prompt 或纯函数。

### A. 肯定请求
- `去砍两根橡木原木带回来`
- `帮我砍一根云杉原木`
- `能不能帮我砍两根橡木原木带回来`
- `我想收集一根白桦原木`
- `帮我砍五根橡木原木带回来`

期望：进入现有计划层；五根请求仍保留上限提示；确认前零动作。

### B. 否定/暂缓
- `不要砍两根橡木原木带回来`
- `我不想砍两根橡木原木带回来`
- `能不能别砍两根橡木原木带回来`
- `我不砍两根橡木原木带回来`
- `先别砍树`
- `暂时不用砍木头`
- `不要砍两根竹子带回来`

期望：零计划、零任务、零工具、零 action_id；即使目标不支持，否定语义也不能被 parser 的 `unsupported_species` 覆盖。

### C. 歧义/冲突/改口/双重否定
- `不要砍橡木，改砍云杉`
- `不要砍橡木原木，砍云杉原木`
- `不是不想砍两根橡木原木带回来`
- `我想砍橡木，也想砍云杉`
- `先别砍了……不，还是砍一根`

期望：歧义/混合/信息不足时澄清；零计划、零任务、零工具，不静默挑选树种。

### D. 叙述/查询/闲聊
- `我昨天砍了两根竹子`
- `我刚刚砍了两根橡木原木`
- `附近有什么木头？`
- `我不想砍木头，我只是问附近有什么树`
- `我特别想砍两根橡木原木带回来`
- `我觉得不错`

期望：叙述/闲聊不建采集任务；查询走既有路径；否定与查询混合时不得启动采集。

### E. 控制与任务状态
- `取消，别砍树了`
- `暂停当前任务`
- `继续`
- `确认`
- 无活动任务时的 `先别砍树`
- 有待确认任务时的 `取消，别砍树了`
- 运行中任务收到新肯定采集请求

期望：控制命令由原入口处理；不得被解释器抢截、重复建任务或越过确认门。

### F. 模型失败与提示注入
用 fake provider/stub 覆盖：timeout、exception、空输出、非 JSON、截断 JSON、未知 enum、字段缺失/错误类型、肯定 intent + negative polarity、否定 intent + positive polarity、count 为 0/负数/极大值/float/bool、未知树种或任意 block ID、消息要求忽略规则/调用 `minecraft_dig`/泄露提示词/提升玩家权限。

期望：不创建可执行任务；不触达旧资源模板；玩家消息只作为待分类数据。

### G. 非木材回归
覆盖 SAFE 查询、跟随/移动意图、普通角色对话、busy、确认/取消、自己发言防循环、private chat。非木材任务不得因为新解释器而无意多调用一次模型或改变原路由，除非有代码证据证明必须如此。

## §8 必需的无动作证据

对所有否定、歧义、模型失败、非法 schema 和提示注入用例，入口级测试必须断言：
- 计划服务调用计数为 0；
- `plan_resource_task()` 被 monkeypatch 成立即抛异常的 sentinel，且未触发；
- `env.tools.executor.traces == []`；
- 无新 Task，`task_id == ""`；
- 无 action_id，无 Action Runtime 调用；
- 除解释/澄清回复外，没有 Minecraft 操作；
- 既有活动任务状态未被解释器直接改变；
- telemetry 不含完整聊天原文、密钥、提示词或模型原始响应。

肯定请求还必须证明预览阶段零动作、零 action_id；只有经既有明确确认入口后，执行流程才可启动。

## §9 实施步骤

### Step 0 — 只读审计
核实当前 provider、超时、路由入口、任务控制优先级、测试 fixture 和实际 HEAD。提交设计与基线报告后再改代码。

### Step 1 — 先写测试
先新增 schema、fake provider 和评估集测试。必须能复现当前 `能不能别砍…`、`我不砍…` 的入口级失败；不得删除或弱化旧断言。

### Step 2 — 独立实现解释器
解释器不得持有 Minecraft service、tool registry、executor 或任务创建 API。使用项目现有 provider 接口，不新建凭据系统。

### Step 3 — 单一入口接入
仅在 `MinecraftChatBridge` 木材路由边界接入。避免旧词法分类与新 LLM 结果形成互相覆盖的两个决策源。保留控制命令优先级；失败不得落回旧单目标模板。

### Step 4 — 保持规划和授权链
确认肯定意图仍经原有解析、观察、Policy、计划冻结、预览、确认、TaskRuntime、步骤授权、Action Runtime 与最终库存验证。

### Step 5 — 回归与故障注入
执行 §7 全部用例，包括模型故障、非法 schema、提示注入、活动任务和非木材回归。自动化评估必须可重复运行，不依赖实时模型服务来证明所有边界。

### Step 6 — 质量门禁
- [ ] 相关木材意图、路由、task entry、agent plan、skill、Minecraft chat、Phase 8D 测试
- [ ] `pytest tests -q`
- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `mypy app`
- [ ] `git diff --check`
- [ ] 凭据扫描
- [ ] 日志隐私与 prompt injection 边界审查
- [ ] schema/migration diff 无变化
- [ ] 逐项审查 §10 安全不变量

### Step 7 — 发布核验
- 单一独立实现提交，不改写历史。
- 同步发布镜像与 `origin/main`，核实 SHA 一致、工作区干净。
- 检查真实 CI run 与 job 结论；重跑失败 job 时保留初次失败证据，不改断言/阈值/超时/skip 规则换绿。
- 更新阶段文档、`CHANGELOG.md`，归档任务书。
- 本阶段未改动作/授权链时，真机七场景可按范围声明不适用；但 fake-provider 入口级无动作证据和 CI 必须通过。若改了执行/授权链，必须重跑对应真机门禁。

## §10 不可破坏的安全不变量

- `app/integrations/minecraft/agent.py` 的 `ACTION_RISK` 不变。
- `allow_medium` 默认值不变，不修改真实配置。
- 当前 Minecraft tools 数量与 schema 不变。
- TaskRuntime 状态机、生命周期、预算与步骤授权不变。
- 两道确认门、可信玩家规则、`TurnOrigin` 来源派生不变。
- 木材计划的冻结目标、期望方块、掉落物绑定、最终库存增量验证不变。
- SAFE 观察不能由模型编造数据替代。
- 计划预览前零动作；用户确认前零 action_id/零动作。
- 否定、歧义、模型失败、schema 失败和提示注入不能生成可执行任务。
- 解释器只输出候选结构化意图，无工具执行权限。
- 不新增工具、动作、权限等级、API 绕行、数据库表或迁移。

## §11 最终报告必须包含

1. PASS/PARTIAL/FAIL 与逐条 DoD。
2. 实际基线、实现提交、最终 SHA、`origin/main`、镜像与工作区证据。
3. 架构图/调用顺序，标出解释器、权限边界、失败分支与控制优先级。
4. 最终 schema、枚举、严格校验与非法输出策略。
5. 实际 provider/model/timeout 的配置来源；不得输出密钥。
6. 评估集分组结果、失败案例、fake/real provider 的使用边界。
7. 无动作证据：计划服务、旧模板、trace、Task/task_id/action_id 精确计数。
8. 相关测试、全量 pytest、Ruff、format、Mypy、diff check 结果和退出码。
9. §10 安全不变量审计、凭据扫描、日志隐私检查。
10. CI run ID/attempt/job 结论与发布同步证据。
11. 已知限制、模型延迟/成本、provider 故障与未覆盖语言。
12. 明确区分自动化、真机、静态审查和未验证事项。

不得把“输出合法 JSON”当作“意图一定正确”，不得把一个成功示例当成准确率证明，也不得把本地执行报告描述成独立审计。

## §12 Definition of Done

只有全部满足才可 PASS：
- [ ] 基线和发布历史正确，镜像/远端/工作区可核验。
- [ ] 解释器独立、无工具权限、无任务创建或执行 API。
- [ ] 输出 schema 严格验证；非法/矛盾结果安全拒绝。
- [ ] 肯定请求进入原有有界 Planner，无直接执行路径。
- [ ] 否定、歧义、模型失败、非法 schema、提示注入均零任务、零计划、零工具、零 action_id。
- [ ] 任务控制与活动任务状态保持原行为。
- [ ] Phase 8D/Follow-up 2 全部既有回归通过；非木材路由无意外变化。
- [ ] 相关回归、全量 pytest、Ruff、format、Mypy、diff check 通过，无新增意外 skip。
- [ ] §10 安全不变量无未批准变化，无凭据或本地数据泄露。
- [ ] 文档、CHANGELOG、归档、CI 和发布证据齐全。
- [ ] 报告区分真实执行证据与未验证事项。

任何安全断言失败，或模型失败时仍能创建可执行任务：**FAIL**。代码/CI 通过但评估覆盖或证据不足：**PARTIAL**。

## 附录：基线参考文件

以实际 HEAD 为准，预计审查：
- `app/integrations/minecraft/chat_bridge.py`
- `app/tasks/intent.py`
- `app/tasks/planner.py`
- `app/tasks/agent_plan.py`
- `app/tasks/agent_planner.py`
- `app/tasks/runtime.py`
- `app/tasks/validation.py`
- `app/integrations/minecraft/agent.py`
- `tests/test_phase8d_wood_intent.py`
- `tests/test_phase8d_wood_plan.py`
- `tests/test_phase8d_wood_routing.py`
- `docs/MINECRAFT_PHASE8D.md`
- `CHANGELOG.md`

若实际接口/路径与任务书不同，先核实并说明，再调整设计；不得凭任务书盲目新建重复模块。
