# CatooBot Phase 7F.5.3 — In-flight Cancellation & Expiry Reconciliation

## 一、基线与目标

权威基线：

- Phase 7F.5.2 代码：`b3014b9fac9b20fa01bb71b0e9fffb93f13d5142`
- Phase 7F.5.2 最终发布：`e49e3824d16b887cb11dbeec79acebf92bf3548f`

本阶段只修复异步动作已经进入真实调用链时的取消、过期和事件关联安全问题，不新增任何 Minecraft 游玩能力。

核心不变量：

**用户取消或任务过期后，不能因为一个已经开始的异步请求稍后才被 Runtime 接受，而留下仍在执行、无法停止或无法被关联的世界动作。**

不得宣称 exactly-once；不得把“发送过 stop 请求”等同于“动作肯定已经停止”。

## 二、必须先梳理的实际时序

审计以下调用链：

`TaskRuntime._run_step → _dispatch_guard → _invoke_step → MinecraftTaskInvoker → AgentBridge → MinecraftService → Runtime HTTP → TaskRuntime._settle`

特别确认以下窗口：

1. `INTENT` 已持久化，尚未开始真实调用。
2. 派发复核通过，HTTP 请求已经开始，但 Node Runtime 尚未接受动作。
3. Node Runtime 已接受动作，HTTP 响应仍在途，`action_id` 尚未写入 TaskRecord。
4. `cancel()` / `expire()` 与 `stop_action()` 交错执行，停止请求可能先于动作创建到达。
5. 动作响应返回时，TaskRecord 已经是 `CANCELLED` 或 `EXPIRED`。
6. 动作终态事件先于 `action_id` 持久化到达 Python。

不要仅按“取消前 `pending_action_id` 是否为空”来判断外部动作一定没有发生。

## 三、必须解决的两个问题

### A. 终态任务不能留下不可对账的在途动作

目前 `_record_settlement_under_pause()` 在任务状态已经不是 `RUNNING` 时，仍可能将异步动作写为 `ACCEPTED`，并写入 `pending_action_id`。

必须明确设计：

- `CANCELLED` / `EXPIRED` 任务如果没有任何外部调用发生，应该保留准确的终态，清除未实际派发的 `INTENT`，不得伪造动作关联。
- 如果外部请求已经发出，但动作是否被接受仍然未知，应按既有歧义语义处理，不得假设请求失败，也不得自动重发。
- 如果 Runtime 已接受动作，必须持久化真实 `action_id`、实例身份、派发代次和动作证据。
- 终态任务不能仅因终态状态而使其尚未结算的真实动作关联永久失去事件匹配能力。
- 终态状态不能被异步响应覆盖回 `RUNNING` 或 `WAITING_ACTION`。
- 应当能从持久化记录判断动作是否已确认终结、是否仍需对账，以及是否仍需要停止。

优先复用现有 payload 和存储机制；如认为必须新增表或迁移，先提交明确的必要性论证，不得为了方便直接扩张 schema。

### B. 取消 / 过期不能只做一次可能过早的 stop

如果 `stop_action()` 在 Runtime 接受动作之前执行，它不一定停止稍后才创建的动作。

必须设计并验证完整的生命周期协议：可能需要在获得真实 `action_id` 后再次执行既有 SAFE 控制面停止、保留可关联的终态事件、或者使用其他经过验证的 fail-closed 流程。

选择的设计必须同时满足：

- 没有真实派发时，不产生任何新世界动作；
- 请求已经发出但结果未知时，不盲目重试；
- Runtime 已接受动作但任务已经取消 / 过期时，不能让该动作脱离生命周期管理；
- 无法证明动作已停止时，明确保留人工核对证据；
- 终态事件无论先于还是晚于 TaskRecord 的更新到达，都不会被悄悄丢弃；
- 不通过锁引入 `stop_action → health → identity reconciliation` 重入死锁。

## 四、必须新增的确定性测试

使用 `asyncio.Event`、可控 Runtime HTTP 层或存储钩子制造精确交错，不使用随机 sleep。

至少覆盖：

1. **取消发生在请求已进入调用链、Node 尚未接受动作时。** 验证如果 stop 先到，后到的请求不会导致世界动作留下无人管理的执行状态。
2. **过期发生在同一窗口。** 验证 TTL 结束后，同样不会发生迟到的未管理动作。
3. **Runtime 已接受动作，响应尚未返回时取消。** 验证获得真实 `action_id` 后，不会只靠先前那次 stop 就错误假定动作已经停止。
4. **Runtime 已接受动作，响应尚未返回时过期。** 与取消分别测试，不得用同一个测试替代两种语义。
5. **动作事件先于 action_id 持久化到达。** 验证事件不会永久丢失，并且重复事件处理幂等。
6. **取消 / 过期与 stop 请求交错。** 验证最终动作状态、停止证据、TaskRecord 终态以及外部动作启动次数相互一致。
7. **未知派发结果。** 不得重发；必须保留 `dispatch_key`、实例身份、代次、动作标识（若已取得）、歧义原因和时间。
8. **已取消任务的迟到终态事件。** 事件处理后任务仍保持原有终态，不会重新进入正常派发或续跑流程；同时动作证据仍可审计、可收敛。

每个测试至少断言：

`task.state`、`step.state`、`dispatch_state`、`dispatch_generation`、`action_id`、`pending_action_id`、`authorization`、`replan_required`、真实动作启动次数、停止调用次数，以及从持久化存储重新读取到的最终状态。

其中至少一组必须使用真实 SQLite 存储和完整的持久化回读，而不仅是内存替身。

## 五、安全与产品边界

- 不增加 Minecraft 工具，不改变 19 个工具的注册表。
- `ACTION_RISK`、`allow_medium=false`、两道独立确认门全部保持不变。
- 不绕过 `TaskAuthorization`、`TaskStepAuthorization`、Policy、AgentBridge 或 Action Runtime。
- 不加入无界重试，不宣称 exactly-once。
- 不修改 Character Bible、`.env`、凭据、`config/config.yaml` 或 `data/`。
- 不为修复生命周期问题而引入新的自动执行能力。
- `scripts/` 仍然只用于本地真实门禁，不进入 GitHub 发布镜像。

## 六、发布与验收

运行：

- `ruff check .`
- `ruff format --check .`
- `mypy app`
- 全量 `pytest tests`
- WebUI typecheck、Vitest、build 和 E2E
- `minecraft_runtime npm test`
- 数据库迁移与完整性检查
- 新增的取消 / 过期竞态门禁
- 适用的真实 Fabric 回归

发布时提供代码提交和最终 SHA、镜像与 `origin/main` 一致性、CI run ID / attempt / 两个 job 的实际结论、pytest 的通过数与 SKIPPED 明细。

只有当在途取消、在途过期、迟到响应和终态事件都具备可验证的安全收敛路径时，才允许裁定 PASS。

完成前不启动 Phase 7F.6 的新游玩能力开发。