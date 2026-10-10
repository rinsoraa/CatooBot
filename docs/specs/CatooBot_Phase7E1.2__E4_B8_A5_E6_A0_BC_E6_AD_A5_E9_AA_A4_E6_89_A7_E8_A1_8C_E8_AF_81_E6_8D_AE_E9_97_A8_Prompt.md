# CatooBot Phase 7E.1.2 — Strict Step Execution Evidence Gate

你正在维护 `https://github.com/rinsoraa/CatooBot`。本阶段是对 Phase 7E.1.1 的一个极窄边界收口，不是重新设计技能学习。

## 0. 基线与工作方式

- 期望基线：`4e7449cb6d5bf0d144897d17374be8aa8efe1a45`（Phase 7E.1.1）。开始前必须检查 `origin/main`，若其已前进，先确认变更并以最新 main 为基线，不能强行覆盖他人提交。
- 遵守仓库现有工作区与干净发布镜像隔离流程。先读对应项目说明，确认真实源码工作区和 GitHub 镜像目录，不要根据旧路径猜测。
- 不触碰 `config/`、`config/overrides.yaml`、`.env`、Character Bible、任何密钥或个人配置。不得把这些文件同步进镜像。
- 先复核根因、写会失败的回归测试，再做最小修复。保留 Phase 7E/7E.1/7E.1.1 的既有测试，不得通过放宽断言来变绿。

## 1. 本阶段唯一目标

严格收口 `app/tasks/skill_learning.py::step_ran()` / `any_step_ran()` 的“步骤真实执行过”判定。

当前实现除了 `started_at`、`action_id` 之外，还把这些状态本身都当成执行证据：

- `WAITING_CONFIRMATION`
- `SKIPPED`
- `FAILED`
- `CANCELLED`

这过于宽松：其中至少 `WAITING_CONFIRMATION` 与 `SKIPPED` 本身并不能证明工具曾被调用；`FAILED` / `CANCELLED` 也可能来自调用前的校验/授权/生命周期分支。Phase 7E.1.1 的安全约束是：**只有可审计的持久化事实能证明某一步确实开始执行，才可把任务后验失败判成技能方法反例**，不能以“状态不是 PENDING”代替执行证明。

请先沿着 `TaskRuntime._run_step`、`_settle`、`on_action_event` 和 `TaskRecord` 的持久化路径核对真实语义，再确定最小、保守且可解释的判定条件。

## 2. 必须满足的判定语义

1. `started_at` 与非空 `action_id` 是强执行证据；注意时间戳 `0` 不能用普通 truthiness 误判为空，应按是否为 `None` 判定。
2. 若仅有 `WAITING_CONFIRMATION` 或 `SKIPPED` 状态、没有开始时间和动作 ID，不得认定执行过。
3. 单有 `FAILED` / `CANCELLED` 状态，但没有开始时间、动作 ID或其他被源码证明可靠的执行证据时，不得自动推断方法运行过。若源码能证明某个额外字段是“调用已经开始”的强证据，可以纳入白名单，并在代码注释说明来源与不变量。
4. `RUNNING` / `WAITING_ACTION` / `SUCCEEDED` 等状态是否能单独证明执行，必须以 TaskRuntime 的实际状态写入顺序为准，不能只凭枚举名称猜测。
5. 对于有歧义、缺失或不一致的 checkpoint，必须 fail-closed：不产生 `COUNTEREXAMPLE`，留稳定的 `REJECTED` 原因码；不得从自由文本、错误消息或模型自述推断。
6. 只有当前既有闭合条件全部成立时，才允许计方法反例：任务终态为 `FAILED`、`failure == TaskFailure.VERIFICATION`、运行时后验 `verification.checked is True` 且 `verification.ok is False`，并且确实有步骤执行证据。
7. 不改变 Phase 7E.1.1 对 `CANCELLED`、`EXPIRED`、`TIMEOUT`、`OFFLINE`、`WORLD_CHANGED`、`AUTHORIZATION`、`BUSY`、`STALLED` 等结果的保守不计分政策。

## 3. 必须先新增的负向测试

放在 `tests/test_skill_feedback.py`，或放在最合适的现有纯判定测试模块。尽量表驱动，覆盖：

- `TaskState.FAILED + failure=VERIFICATION + verification.checked=True + verification.ok=False`，但所有步骤都是 `PENDING`：必须 REJECTED，不得使技能 STALE。
- 同上，但某步骤仅为 `SKIPPED`，且没有 `started_at` / `action_id`：必须 REJECTED。
- 同上，但某步骤仅为 `WAITING_CONFIRMATION`，且没有 `started_at` / `action_id`：必须 REJECTED。
- 同上，但仅有调用前失败的 `FAILED` / `CANCELLED` 状态、没有可靠的执行事实：必须 REJECTED。
- 时间戳 `started_at=0`：若该字段在仓库时钟语义下代表已开始，必须正确识别为存在，而不能因 `0` 的 falsy 属性漏掉。
- 正向控制：真实开始时间/动作 ID或被源码明确证明的等价强证据 + 同一套 `VERIFICATION` 后验失败条件，仍产生 `COUNTEREXAMPLE`，保留 `STALE → INVALIDATED` 现有行为。
- 不要只测试纯函数：至少有一项服务层回归证明上述不合格反馈会被幂等消费、保留原因码，但不改变 `failure_count` / `success_count` / skill status。
- 保持 InMemorySkillStore 与 SQLite store 语义一致；没有必要就不要增加迁移。

测试必须能在修复前复现问题、修复后通过。若某种记录组合根据当前 runtime 不变量理论上不可达，也仍需明确写出依据；不能仅凭“正常情况下不会发生”跳过判定审查。

## 4. 严格范围边界

本阶段禁止：

- 新增 Minecraft tool、ActionRuntime action、TaskRuntime state 或第二套执行入口。
- 改动 `_finish` 的成功/失败判定语义，改动任何 Minecraft JS 执行行为，或尝试修复 `minecraft_dig` 归因问题。
- 改动用户确认、批准、身份、取消、暂停、恢复、授权过期、超时语义。
- 改变 `allow_medium=false`、现有 19 个工具边界、技能只产出计划候选、`CANDIDATE` 不可复用、终态不可复活等约束。
- 改写历史证据 verdict/reason 或追溯重算旧技能状态。

本阶段只收紧“执行证据是否成立”的纯判定及必要的服务级回归。Phase 7D §10.4 `minecraft_dig` 的归因缺陷必须保持为独立 follow-up，不要顺手混进本提交。

## 5. 串行验收门禁

依仓库现有规范串行执行，前一门禁结束后再运行下一门禁：

1. `ruff check .`
2. `ruff format --check .`
3. `mypy app`
4. `pytest tests -q`
5. WebUI typecheck / vitest / build / 浏览器 E2E（仅当本改动或仓库验收规范要求时照常执行，结果如实记录）
6. Minecraft Node 单测及 flying-squid E2E（若不受改动影响，可标记为按既有策略执行；不要伪造结果）
7. 检查迁移冻结断言、工具/动作/state 数量、`allow_medium=false`、敏感配置未触碰。
8. 确保本阶段确实只改预期文件，并用 Git diff 审查是否有无关改动。

提交并推送后核验 `origin/main` 指向的新提交、GitHub Actions 实际 run 和两个 job 的最终 conclusion。若 CI 有重试，报告 attempt 1 与最终结果；不得只写最终绿灯而隐藏首次失败。

## 6. 文档与最终报告

- 在 Phase 7E 文档中追加短小的 Phase 7E.1.2 记录；不要覆盖 7E.1.1 的历史结论。
- 报告：根因、修复前失败测试、修复后测试、判定规则白名单及其源码依据、InMemory/SQLite 一致性、最终提交 SHA、CI run/job 链接、所有串行门禁结果。
- 区分 `PASS` / `BLOCKED` / `SKIPPED`；不要把未运行的真机门禁写成 PASS。
- 如发现需要修改工具执行语义才能保证“真的运行过”，停止扩大范围，报告证据缺口，不要自行添加新状态或执行入口。

## 7. 完成标准

只有在不合格状态本身不再被当作执行证明、可归因验证失败仍能正确计为反例、所有回归与既有门禁通过、远端提交及 CI 可核验的条件下，才报告 `PASS`。否则明确列出阻断项与证据，不得用测试数量替代语义正确性。
