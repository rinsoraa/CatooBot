# CatooBot Phase 7D.2.1 — Follow Veto Cancellation Result Closure

## 目标

修复 Phase 7D.2 验收复核发现的一个残余问题：`_follow_veto_message()` 只依据 `cancel()` 是否抛出异常判定取消成功，没有核验返回的 TaskRecord 是否真正处于 `CANCELLED` 状态。

基线：
- 仓库：`rinsoraa/CatooBot`
- 当前 `origin/main`：`c7bf23a0112a76e365c31d70fb8489795176e841`
- 核心整改提交：`6551f16f621fcdf419be6e7d36832a7159ac501e`

第一步必须确认当前 branch、HEAD、工作树状态与远端关系。不得覆盖现有修改；以当前最新代码为基线。

## 一、修复取消结果判定

检查：
- `app/tasks/qq_entry.py` 中 `_follow_veto_message()`
- `app/tasks/runtime.py` 中 `TaskRuntime.cancel()`
- 真实 `TaskRecord` 的状态类型与终态定义

必须满足：

1. `cancel()` 如果返回 awaitable，先等待并获取其实际返回值；不能丢弃返回值。
2. 只有取得明确、可信的 `CANCELLED` 结果时，才向用户报告“任务已取消”。
3. 返回记录为 `SUCCEEDED`、`FAILED`、`EXPIRED` 等其他终态时，不得宣称本次成功取消。应保留身份否决，并明确说明取消未能确认或任务已经进入其他终态；不能伪造取消成功。
4. 返回 `None`、返回对象缺少状态、状态无法识别或取消调用抛出异常时，保守处理：继续拒绝此次确认，并且不声称取消成功。
5. 没有 `task_id`、任务无法读取等既有 fail-closed 分支不得被放宽。
6. 无论取消结果是什么，本次身份否决都不得进入 `confirm_and_start()` 或派发跟随动作。
7. 正常返回 `CANCELLED` 的真实任务仍应显示取消成功。不要因为本修复破坏正常取消流程。

不要修改 Policy、风险级别、确认授权语义、`allow_medium`、TaskRuntime 状态集合或 Minecraft 工具集合。

## 二、补充回归测试

修改 `tests/test_agent_plan_closure.py` 与必要的 `tests/test_agent_plan_negative.py` 测试桩。测试桩必须模拟真实 `cancel()` 的返回契约，不能继续把任意 `None` 当成成功取消。

至少覆盖：

- `cancel()` 返回状态为 `CANCELLED` 的 TaskRecord：报告取消成功。
- `cancel()` 返回 `SUCCEEDED`：仍拒绝确认，但不得报告“已取消”。
- `cancel()` 返回其他非 `CANCELLED` 终态：仍拒绝确认，不得误报成功。
- `cancel()` 返回 `None` 或无法识别的状态：保守拒绝，不得误报成功。
- `cancel()` 抛出异常：仍拒绝确认，并如实说明取消未能确认。
- `task_id` 缺失或 Task 状态无法读取：维持原有 fail-closed 行为。
- 所有否决场景中，入口链级测试均断言 `confirm_and_start()` 未被调用。

如发现 `TaskRecord` 的状态枚举或取消 API 的既有契约存在差异，先以当前源码为准，不得通过修改测试桩来掩盖真实契约。

## 三、验证要求

1. 运行新增测试及相关测试文件。
2. 运行全量 pytest、Ruff check、Ruff format check、mypy。
3. 运行项目现有 WebUI 类型检查、Vitest、build、浏览器 E2E 与 Minecraft runtime 测试；不得把未运行的门禁记为 PASS。
4. 将修复和测试提交到干净的 GitHub 镜像目录，按项目现有流程推送，不得 force-push。
5. 验证 GitHub Actions 实际 run 与 job 结果；记录精确 commit SHA 和 CI URL。
6. 检查工作树、源码与镜像的一致性。

## 四、范围边界

本次只关闭取消结果判定缺口。不重做已经完成的 Real Java C，不修改 Phase 7D.2 的其他逻辑，不新增工具、ActionRuntime 动作、TaskRuntime 状态或数据库迁移。

如果检查发现该缺口不会影响现有生产调用契约，也必须用返回状态测试证明，而不能直接忽略。若仍有任何非 `CANCELLED` 结果被错误报告为取消成功，阶段继续保持 BLOCKED。

## 五、最终报告

必须给出：
- 修复前后行为差异；
- 新增测试及实际执行结果；
- 完整门禁结果；
- 实际镜像 commit 与 CI run/job 链接；
- 工作树、镜像与配置状态；
- 剩余 BLOCKED / SKIPPED 项。

不得仅凭本地汇总或提交信息宣布通过。只有返回状态判定、回归测试和实际 CI 全部通过，才能将该单点整改标记为 PASS。