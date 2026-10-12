# CatooBot Phase 7F.2.1 — Terminal-State Invariant Hardening

## 任务目标

在不改变 Phase 7F.2 正常业务行为的前提下，补牢探索结果投影的终态约束。

核心不变量：

**只有最终状态为 `SUCCEEDED`，且存在完整、成功的运行时独立验证证据，才允许探索投影写入正向 `EVENT` 到达经历或 `LOCATION` 新事实。**

`FAILED`、`CANCELLED`、`EXPIRED`、状态未知，以及状态与验证字段互相矛盾的记录，都不得通过探索投影写入正向到达经历或新事实。

这是一次小型防御性修复，不是新增探索能力的阶段。

## 一、先审计现状

以当前 GitHub `main` 的 Phase 7F.2 版本为起点，先核对实际源代码、镜像与当前提交，不要假设工作区没有后续变化。

重点审计：

- `app/tasks/exploration.py`
  - `_arrived`
  - `exploration_facts`
  - `exploration_outcome`
  - `arrival_fact`
- `app/integrations/minecraft/memory_bridge.py`
  - `MinecraftMemoryBridge.on_task_finished`
  - `_project_exploration`
- `tests/test_exploration_feedback.py`

确认现有 `TaskRuntime` 成功、失败及取消时如何写入 `TaskRecord.verification`，避免只看测试夹具就认定运行时存在故障。

## 二、实施最小修复

### 1. 统一到达判定

收紧探索到达判定：只有同时满足以下条件，才可认定为已验证到达：

- `record.state` 的规范化值严格为 `SUCCEEDED`。
- `verification` 是可读取的映射。
- `verification.checked is True`。
- `verification.ok is True`。
- `verification.position_within` 是有效映射，且含有实际到达坐标 `x`、`y`、`z`；不得只凭目标坐标推导实际到达。

缺失、类型异常、字段冲突或无法确认时，一律拒绝提升为已验证到达。

优先将约束落实在共享判定函数中，并检查所有调用点是否仍有独立的弱判定。

### 2. 正向记忆必须受终态约束

`exploration_facts()` 只有在任务最终成功、到达验证完整且执行期 SAFE `minecraft_world` 观察可信的情况下，才能生成 actionable `LOCATION` 事实。

`arrival_fact()` 必须使用同一套严格到达判定，不应通过较弱的重复检查绕过状态要求。

取消、失败、过期或状态矛盾的任务不得写入正向到达事件。

### 3. 保留原有结果分类和正常路径

不得改变以下现有语义：

- 成功且有可信新事实：`NEW_FACTS_VERIFIED`。
- 成功到达但没有可信新事实：`ARRIVED_NO_NEW_FACT`。
- 失败：`BLOCKED`。
- 取消、过期、中断或缺乏完成证据：`CANCELLED_OR_INTERRUPTED`。

在状态与证据矛盾时，以 fail-closed 方式拒绝正向记忆，不得为了匹配某个枚举而伪造事实。

## 三、必须增加的回归测试

在 `tests/test_exploration_feedback.py` 中补充明确覆盖以下情况的测试：

1. `CANCELLED` + `verification.checked=True` + `verification.ok=True` + 有成功的 `minecraft_world` 步骤：不得写入 `EVENT` 或 `LOCATION`。
2. `FAILED` + 同样的矛盾成功验证字段：不得写入正向记忆。
3. `EXPIRED` + 同样的矛盾成功验证字段：不得写入正向记忆。
4. 成功的 TaskRecord 但缺少实际到达坐标：不得写入正向记忆。
5. 正常 `SUCCEEDED` + 完整到达验证 + 合格的新兴趣点：仍能正确写入 `EVENT` 与 actionable `LOCATION`，并生成预期的 `NEW_FACTS_VERIFIED`。
6. 重复发布同一终态仍保持幂等，不新增重复事实。
7. 失败或取消最终只留下既有允许保留的任务经历记录；不得产生可用于后续探索规划的正向事实。

对第 1–3 项，应验证记忆存储中的实际写入结果，而不只是验证 `exploration_outcome()` 返回值。

测试可以构造不符合正常生命周期的矛盾 TaskRecord，因为这里测试的是投影边界的防御能力，而不是宣称真实运行时已经产生这种状态。

## 四、严格禁止的范围扩张

本阶段不做：

- 多段探索、循环探索或开放式 LLM 决策。
- 新 Minecraft 工具、风险等级或执行入口。
- 新数据库表、迁移或第二套记忆系统。
- 对 `ACTION_RISK`、`allow_medium=false`、19 个工具注册表的修改。
- 绕过 LifeIntent、TaskProposal、AgentPlan、`TaskAuthorization`、步骤授权或任何现有批准与确认门。
- 改动 pause/resume/cancel/expire、超时、重规划、停止机制。
- 将 LifeIntent 或探索记忆直接变成执行权限。
- 修改真实 Character Bible、凭据、`.env` 或敏感配置。

保持现有两道门：计划批准后最多进入 `PENDING_CONFIRMATION`，未经第二道确认不得开始执行。

## 五、验证与交付门禁

先运行新测试与相关探索反馈测试，然后按现有流程串行执行完整门禁：

- `ruff check`
- `ruff format --check`
- `mypy app`
- 全量 `pytest tests`
- WebUI typecheck、Vitest、build、E2E
- `minecraft_runtime npm test`
- 数据库迁移幂等性核验：维持 v36，不新增迁移，无数据丢失

真实 Fabric 1.21.1 服务器门禁仍须执行：

- `scripts/feedback_smoke_real.py`：正常成功路径和 LifeIntent 反馈链通过。
- `scripts/explore_smoke_real.py --sections all`：positive、blocked、cancel 全部通过。

真机门禁不得通过跳过、mock 代替真实服务器、放宽断言或仅检查脚本退出码来宣告 PASS。

## 六、仓库与交付纪律

- 先确认起始提交、工作区改动及源目录/发布镜像的现状。
- 权威源码目录：`E:\WorkSpace ZCode\CatooBot`。
- GitHub 发布镜像：`E:\WorkSpace ZCode\CatooBot_github`。
- 保持双目录隔离：在权威源目录完成并验证修改，按既有发布流程同步到干净镜像；不要擅自把本地测试脚本、数据、凭据或真实角色设定加入 Git。
- `scripts/` 中的真机门禁脚本继续仅供本地使用，不纳入发布镜像。
- 不覆盖或丢弃无关工作区改动。
- 提交代码和文档，推送后核对 `origin/main`、提交哈希及对应 GitHub Actions 结果。

## 七、最终交付报告必须包含

1. 起始提交与最终提交 SHA。
2. 实际改动文件和每项修复理由。
3. 新增矛盾状态测试及各自验证的写入不变量。
4. 全量本地门禁结果与真实 Fabric 门禁原始摘要。
5. CI 两个 job 的结果。
6. 确认 19 个工具、`allow_medium=false`、权限链、迁移版本 36 和敏感文件边界均未改变。
7. 明确列出任何未完成或 SKIPPED 项；不得将未执行项写成 PASS。

最终裁决只有在代码、回归测试、真机及 CI 证据齐全时才能给出 PASS。