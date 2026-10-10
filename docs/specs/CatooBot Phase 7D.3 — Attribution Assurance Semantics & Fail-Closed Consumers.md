# CatooBot Phase 7D.3 — Attribution Assurance Semantics & Fail-Closed Consumers

## 0. 任务目标

你现在负责 CatooBot 的 Python / Node.js / Mineflayer / SQLite 多层执行证据系统。

当前基线：`d60d380`。

前序交付：

- Phase 7E.1.2：严格步骤执行证据门禁，提交 `73bfae5`。
- Phase 7D Follow-up：挖掘归因第一轮实现及第二轮真实 Minecraft 修正，核心提交 `7be2831`。
- 最终测试夹具加固，提交 `d60d380`。

本阶段目标：

**修复挖掘归因结果的证据等级语义，确保 Runtime、技能学习、复用反馈与长期记忆不会把时序推断误当成严格自挖证明。**

这不是重新实现 Phase 7D Follow-up，也不是再调整一次 `SELF_MIN_RATIO` 就结束。

先核查当前源码与现有测试，复现真实问题后实施最小完整修复。不要只给设计报告，不要等用户确认再继续，不要为了测试通过而放宽断言。

## 1. 不可违反的产品原则

以仓库的 `CatooBot 产品定义.md` 为产品语义依据。

必须保持以下区别：

- 计划不等于执行。
- `Task SUCCEEDED` 不等于每一步都由罐头亲自造成。
- 方块消失不等于罐头挖掉它。
- 服务端确认方块变成 air，只能证明服务端确认了世界效果，不能自动证明具体行为执行者。
- 背包增量属于效果证据，不能独立证明某个 `minecraft_dig` 步骤的归属。
- 观察到其他实体的破坏进度，并不一定证明该实体最终完成了方块移除。
- 无法归因的经历不应该惩罚技能，也不能被当作可信的技能成功样本。
- 角色的长期记忆必须区分亲身确认经历、推断和未能归因的世界变化。

不能通过自由文本、模型自述、任务目标或模型置信度替代执行证据。

## 2. 基线源码审计与问题复现

首先检查：

- `minecraft_runtime/dig_attribution.js`
- `minecraft_runtime/runtime.js`
- `minecraft_runtime/test/dig_attribution.test.js`
- `minecraft_runtime/test/e2e.js`
- `minecraft_runtime/test/probe_dig_attribution_real.js`
- `app/tasks/skill_learning.py`
- `app/tasks/skill_service.py`
- `app/tasks/runtime.py`
- `app/integrations/minecraft/memory_bridge.py`
- `tests/test_dig_attribution.py`
- 与技能反馈、记忆恢复及 Minecraft 执行相关的其他实际测试。

核对当前依赖锁定的 Mineflayer / minecraft-protocol 版本及事件语义，不得依赖未验证的上游行为。

重点审计以下问题。

### 2.1 `strict_self_proof` 的语义过宽

当前 `minecraft_runtime/dig_attribution.js::collector.resolve()` 中的 `strict_self_proof`，只要归因为 `SELF_CONFIRMED`，并满足自身破坏进度或 `serverUpdateSaysAir`，就可能设为 `true`。

其中 `dig_lifecycle_timing` 是基于时序的归属推断。服务端确认方块变成 air，不会单独证明谁完成了移除。

真机报告中的自挖 S 用例使用的是 `dig_lifecycle_timing`，不能把它包装成直接的执行者证明。

先新增会失败的测试，再重构归因契约。

### 2.2 Python 实际消费门禁不够严格

当前 `app/tasks/skill_learning.py`：

- `dig_attribution()` 读取归因载荷；
- `_dig_attribution_verdict()` 核对世界效果和归因标签；
- `self_dig_confirmed()` 根据判定结果返回布尔值。

当前正向判定没有严格要求直接执行者证据，并且 `action_id` 的匹配检查仅在两侧 ID 都非空时才判不匹配。

必须要求用于严格归因的载荷完整、类型正确、版本兼容，并精确关联到当前步骤。缺少任意关键身份字段或不匹配时，一律 fail-closed。

不能只在 `self_dig_confirmed()` 增加一个 `strict_self_proof is True` 判断就结束，因为当前 Runtime 的时序推断路径本身可能将该字段设为 true。

### 2.3 未解析的实体身份不能被标为外部确认

当前 `onBreakProgress` / `onBreakProgressEnd` 的逻辑，会把不匹配自身实体 ID 的事件记为外部事件。实体身份缺失时，这个推断不成立。

必须区分：

- 已可靠解析的自身实体；
- 已可靠解析的其他实体；
- 实体身份未知。

只有第三方实体身份已可靠解析且确认为非自身实体时，才可记为 `EXTERNAL_INDICATED` 的依据。未解析的实体不能被直接判成外部行为。

### 2.4 服务端方块更新必须有一致的时序裁决

审计：

- `serverSaysAir()`；
- `serverSaysPresent()`；
- `worldEffectOf()`；
- `collector.resolve()` 中的 `first(SERVER_BLOCK_UPDATE)`；
- 目标坐标过滤和采集器的动作时间窗口。

目前使用“是否曾出现过某类包”和“第一条服务端更新”的逻辑，可能在多条相互矛盾的状态更新中产生不一致结论。

必须让 Runtime 对目标坐标的服务端更新按顺序进行一致处理，并区分：

- 原方块得到服务端确认，仍然存在；
- 原方块被确认移除；
- 目标块变成另一个非 air 方块；
- 服务端更新互相冲突或无法解释；
- 仅存在本地乐观更新，没有权威确认。

不得仅使用 `type !== 0` 就宣称原方块仍然存在。必须核对当前版本协议中的方块状态语义，判断是否能区分原方块与其他非 air 方块。

相互冲突或顺序不明的证据不得通过简单布尔 OR 得到更强的结论。

## 3. 重构归因契约：效果和行为归属必须分开

继续保留两个独立维度。

### 3.1 世界效果

保留或等价支持：

- `BLOCK_REMOVED`
- `BLOCK_REMAINS`
- `UNKNOWN`

`BLOCK_REMOVED` 必须对应可说明的权威状态证据；本地视图中的乐观更新不能单独将它设为 `BLOCK_REMOVED`。

### 3.2 行为归属

必须具备语义明确的等级：

- `SELF_CONFIRMED`
- `SELF_INFERRED`（可以采用符合现有命名规范的等价名称）
- `EXTERNAL_INDICATED`
- `AMBIGUOUS`

规则：

**`SELF_CONFIRMED`**

只在存在足够可靠的、与当前动作及目标坐标对应的自身执行证据，并且服务端世界效果得到确认、没有未解决的冲突时使用。

对“直接执行者证据”的具体定义必须来源于已核实的协议 / Mineflayer 语义。不能直接将 `diggingCompleted`、时长达到阈值或任意 air 更新称作直接执行者证明。

**`SELF_INFERRED`**

只有当前客户端证据能够说明自身挖掘生命周期符合预期、目标方块得到服务端移除确认、且没有发现相互冲突的外部信号时，才可使用。

这种结果仍是推断，不是严格证明；不能与 `SELF_CONFIRMED` 混用。

**`EXTERNAL_INDICATED`**

必须有可以可靠解析的外部实体身份及相关目标坐标的破坏进度证据。不能声称该实体一定完成了最终移除。

**`AMBIGUOUS`**

适用于实体身份未知、证据冲突、缺少服务端确认、事件和动作无法关联、客户端仅有本地乐观更新，以及其他无法充分归因的情况。

原因码必须稳定、具体、可审计。

### 3.3 `strict_self_proof` 的处理

不要继续保留一个名称严格、语义却只是时序推断的布尔字段。

优先将其重构为明确的证据等级 / 确认依据契约，并考虑通过新的 `schema` 版本明确标注语义变化。如果为兼容旧载荷而保留 `strict_self_proof`，其为 true 的条件必须确实代表严格自身执行证据，不能由 `dig_lifecycle_timing` 单独成立。

不要把更改字段名当成完成任务。所有生产消费者都必须使用同一套明确契约。

## 4. Python 技能学习：只认可符合证据等级的样本

在 `app/tasks/skill_learning.py` / `app/tasks/skill_service.py` 中落实归因等级。

1. `SELF_CONFIRMED` 必须通过严格归属门禁以及现有资格门，才可产生 `POSITIVE`。
2. `SELF_INFERRED` 应保留为可审计的推断样本，但不能直接计入严格正向技能成功，也不能作为技能方法失败的反例。
3. `EXTERNAL_INDICATED` 不得用于正向学习或方法失败计分。
4. `AMBIGUOUS` 保守记录，不计入正向、反例或技能状态晋升 / 降级。
5. 缺少归因载荷、错误 schema、缺少 action ID、action ID 不匹配、非法字段类型及不一致 checkpoint，必须 fail-closed。
6. 不得用 `inventory_delta`、`block_absent` 或 `Task SUCCEEDED` 绕过归属门禁。
7. 原有方法反例闭合条件、`step_ran()` 的严格证据白名单、绑定幂等消费和存储事务语义全部保留。

严格自挖归因未成立，不能反向推断技能失败。不得将这个修复变成对真实失败方法的误伤。

## 5. MinecraftMemoryBridge：禁止虚构亲身经历

在 `app/integrations/minecraft/memory_bridge.py` 中：

- `_remember_task_target()` 只能在对应 `minecraft_dig` 步骤成功、世界效果确认、行为归属严格确认时，写入 `TASK_RESULT` 来源的“罐头亲手挖过”资源事实。
- `SELF_INFERRED` 不得写成已确认的亲身经历。
- `EXTERNAL_INDICATED` / `AMBIGUOUS` 不得写入“亲手挖过”的资源事实。
- 任务总体成功仍可保留为独立任务经历；不要因此虚构某个具体挖掘步骤的归属。
- 多个 `minecraft_dig` 步骤必须逐步检查，不能从其中一步推断其余步骤。
- 读不到或无法确认归属时不写该强陈述，并留下适当审计记录。

不得为方便实现而重写记忆系统、改变现有角色人格或破坏坐标对账与检索。

## 6. 历史证据与已晋升技能

先进行只读审计，判断现有历史记录中：

- 哪些 `POSITIVE` 技能证据包含旧版本 `dig_lifecycle_timing`；
- 哪些记录实际保存了归因 payload、`confirm_basis`、`strict_self_proof` 及 schema；
- 哪些已晋升技能依赖这些旧证据；
- 哪些历史记录由于缺乏字段而无法重建其严格归属。

不得删除学习账本、重置技能计数或静默重写历史证据。

如果旧证据不足以确认严格自挖，不得自动把它包装成新契约下的 `SELF_CONFIRMED`。先区分历史证据和新版本证据，再提出可审计、可回滚的隔离 / 重算方案。

若要修改既有技能状态，必须证明状态派生规则、历史证据归属、幂等性与存储事务均正确。不能仅根据当前任务状态批量失效技能。

本阶段没有证据表明必须新增数据库表或迁移；优先使用当前结果 payload 和证据账本。如果确实需要迁移，必须给出必要性理由及升级兼容测试。

## 7. 必须新增的回归测试

Node.js Runtime 单测至少覆盖：

- 自身直接执行者证据 + 服务端移除确认 → `SELF_CONFIRMED`。
- 仅有自身挖掘生命周期 + 时序达标 + 服务端 air → `SELF_INFERRED`，不得称为严格自挖证明。
- 服务端确认 air 但没有自身执行证据 → `AMBIGUOUS`。
- 本地乐观更新但无服务端确认 → `UNKNOWN` / `AMBIGUOUS`。
- 服务端纠正包证明原方块仍在 → 不得报方块移除成功。
- 目标方块被替换成另一个非 air 方块 → 不得混淆原方块仍存在与原方块被替换。
- 服务端先回非 air、后回 air，以及先回 air、后出现其他更新 → 依据完整有序事件处理，不得由 `first()` / `some()` 产生互相矛盾的严格证明。
- 外部实体 ID 明确、缺失、未知、无法解析、自身 ID 尚未可用等情况。
- 外部破坏进度与自身生命周期推断相冲突 → `AMBIGUOUS`。
- 缺少预期挖掘时长、边界 ratio、延迟服务端确认和无动画的外部变化。
- action ID、坐标、会话或维度不匹配的证据。
- 超时、取消、断线、重连和延迟 / 重复事件。
- `detach()` 清理不会泄漏监听器；已终结动作不被迟到事件改变。

Python 单元与服务级测试至少覆盖：

- `SELF_CONFIRMED`、`SELF_INFERRED`、`EXTERNAL_INDICATED`、`AMBIGUOUS` 的完整判定矩阵。
- 缺失 / 不匹配 `action_id` 必须 fail-closed。
- `strict_self_proof` 不得仅凭类型转换或时序推断被接受。
- 只有任务级背包增量，不能替单次挖掘提供归属证明。
- 任务整体 `SUCCEEDED` 不得覆盖归属不明的挖掘步骤。
- 非严格归因不产生 `POSITIVE` 或 `COUNTEREXAMPLE`，不改变技能计数 / 状态派生。
- 证据依旧保留稳定原因码并幂等消费。
- InMemory 与 SQLite 语义一致。
- MemoryBridge 仅为严格归因的已完成步骤写入“亲手挖过”的资源事实。
- 多个挖掘步骤按步骤单独判定。
- 旧 schema、历史 payload 缺字段和当前 schema 的向后兼容行为。
- 历史证据审计与新契约判定不混淆。

## 8. 真实服务器验证

在现有真实 Minecraft 环境允许的情况下，重新运行第二轮的四个案例：

- S：罐头自己挖。
- E：`/setblock` 中途移除。
- X：真实第二个客户端挖同一个方块。
- P：服务器拒绝破坏。

补充验证时序边界：尝试在自身挖掘完成附近触发不带其他玩家破坏动画的外部坐标变化；如果无法稳定操纵该竞态，使用有序事件单测完整覆盖，同时如实承认真实服务器无法证明的部分。

不要把测试 Mock 说成真实协议证明。

对于标准 Java 客户端确实无法提供直接执行者证明的情况，接受“自挖推断”与“自挖严格确认”不同。此时必须保证归因推断不会被上层当作已确认亲身经历。

任何无法提供足够证据的真实服务器测试均标记 `SKIPPED`，附原因，不得伪造 PASS。

## 9. 安全、迁移和范围冻结

保持现有执行架构与所有安全语义：

- 当前 19 个工具和现有 `ACTION_RISK` 不变。
- 不增加 ActionRuntime 动作或 TaskRuntime 状态。
- `allow_medium` 默认 `False`。
- 用户确认、授权、取消、暂停、恢复、超时、离线和 World Changed 语义不变。
- 不改变真实 `minecraft_dig` 动作的授权路径和安全门。
- Phase 7E.1.2 的 `step_ran()` 白名单及 `started_at is not None` 判定不变。
- 技能继续只产生计划候选，不增加新的技能执行入口。
- 当前迁移版本 36 保持冻结；除非证明不可避免，不新增迁移。
- 不触碰 `.env`、真实密钥、Character Bible、敏感配置或测试账号权限文件。
- 不为测试更改服务器玩法配置，也不重置或删除任何学习数据。

当前真实测试服务器使用 `online-mode=false`，且曾为测试账号授予 OP。测试期间必须保持服务器为本地 / 隔离环境，不得暴露到不可信网络；如果后续工作需要改变服务器配置或权限，应先提出单独的安全方案，不能暗中修改。

## 10. 串行门禁

先测试，再修复。完成后按既有纪律串行执行：

1. `ruff check .`
2. `ruff format --check .`
3. `mypy app`
4. `pytest tests -q`
5. WebUI typecheck、Vitest、build。
6. 浏览器 E2E。
7. Minecraft Runtime Node 单测与 flying-squid E2E。
8. 真实 Java 服务器门禁（环境允许时）。
9. SQLite / InMemory 一致性、历史证据审计、迁移版本冻结。
10. diff 范围与敏感文件核对。
11. 仓库发布镜像一致性、提交和 GitHub Actions CI 核验。

必须如实记录第一次失败、根因、修复后结果和是否重试，不能用后一次绿色结果抹掉前一次失败。

## 11. 文档和最终报告

更新 Phase 7D Follow-up 文档和 CHANGELOG，新增本阶段独立章节，不覆盖历史结论。

报告至少包括：

1. 根因与真实代码路径。
2. `SELF_CONFIRMED` 与 `SELF_INFERRED` 的精确定义。
3. `strict_self_proof` 的新契约及兼容策略。
4. Runtime、Python 消费者与 MemoryBridge 的实际改动。
5. 新增失败测试、修复后测试及真实服务器结果。
6. 历史技能证据的审计结论及未处理的兼容边界。
7. 数据库迁移、工具、动作、TaskRuntime 状态和风险开关是否变化。
8. 所有门禁、真实服务器 `SKIPPED` 项、CI 和提交 SHA。
9. 仍受客户端协议限制的能力，不得用“测试全绿”掩盖。

最终裁定必须根据实际证据填写 `PASS` / `PARTIAL` / `BLOCKED`。

**验收核心：系统可以推断罐头可能完成了一次挖掘，但除非有充分且可追溯的证据，不能把这种推断伪装成严格确认，再让技能学习和长期记忆把它当成罐头已确认的亲身经历。**