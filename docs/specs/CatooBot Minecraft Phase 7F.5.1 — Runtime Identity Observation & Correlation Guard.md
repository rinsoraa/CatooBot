# CatooBot Phase 7F.5.1 — Runtime Identity Observation & Correlation Guard

## 一、基线与目标

权威基线：

- Phase 7F.4：`f31b4ec600b4cebda002abd7c52d5bcaaa452542`
- Phase 7F.5 代码：`4d732628c0f04f36bafbf51560f166fda99a0a80`
- Phase 7F.5 最终发布：`eb4d88d4f14eba8a9b5fd968a92b97178902f3af`

本阶段是 **7F.5 的定向修正，不是新的 Minecraft 能力阶段**。

目标是让 Runtime 身份变化观测真正贯穿运行时调用链，确保成功终态事件能够正确关联实例，并且在身份变化时，不发生新的未授权派发、不误判新动作或旧动作、不依赖被动等待 TTL 才发现故障。

开始前确认 `main`、发布镜像和工作树的实际状态。不要假定用户本地状态与以上 SHA 一定相同。

## 二、问题 A：成功身份响应必须真正触发观测

审查并修正：

- `app/integrations/minecraft/service.py`
- `app/integrations/minecraft/runtime_client.py`
- `app/integrations/minecraft/task_adapter.py`
- `app/integrations/minecraft/task_coordinator.py`
- `app/tasks/runtime.py`
- `app/core/bot.py`

目前 `_note_runtime_instance()` 的函数逻辑存在，但仓库内调用点集中在 `_start_runtime()` 的启动就绪检查中。`_ensure_runtime()`、周期状态轮询和 `status()` 的成功响应没有完整接入身份观测。

必须实现：

1. 首次成功取得 `runtime_instance` 后，必须及时保存当前实例身份。
2. 每次成功的 health/status 响应均应按统一协议处理身份字段，且不因 `auto_start_runtime=false` 而绕过实例跟踪。
3. 只有成功且包含有效身份的响应才能更新身份；超时、连接失败、401、无效响应或缺少身份字段，不得被当作实例已经变化。
4. 确认身份变化后，旧实例的未决动作必须进入既有 fail-closed 对账路径。
5. 不能仅通过单元测试直接调用 `_note_runtime_instance()` 就声称集成完成；必须覆盖真实的 `MinecraftService` 请求路径。

重点覆盖：Python 进程持续运行、外部管理的 Node Runtime 从实例 A 重启到实例 B 的情况。

## 三、必须处理的派发顺序和并发问题

不能简单地在现有 `_ensure_runtime()` 中增加一个回调，然后认为问题解决。

当前任务执行可能先持久化 `INTENT`，之后才通过 Agent Bridge 发出实际调用。若在这个窗口内探测到 Runtime 实例变化，旧任务对账可能把当前任务也标记为歧义，但已经开始的调用仍可能继续执行。

必须明确并实现安全顺序：

1. 确保新的动作派发具备可信的当前实例身份。
2. 将已有旧实例动作的对账与新的派发请求正确排序。
3. 对账中的 `stop` 等安全控制调用不得与身份观测互相递归等待，形成死锁。
4. 身份变化时，不能仅因 TaskRuntime 内存中的旧对象尚未刷新，就继续派发被暂停或授权已清除的动作。
5. 不允许通过重复派发、宽松重试、跳过确认或伪造成功状态来规避并发问题。

请先梳理 TaskRuntime、Agent Bridge、Service 与 Runtime 的实际调用时序，再决定如何实现同步、串行化或前置检查。必须用故障注入验证设计，而不是仅在注释中承诺执行顺序。

## 四、必须修正事件实例匹配的缺口

在 `TaskRuntime.on_action_event()` 中实现清晰、严格的关联规则。

- 记录有实例 ID，事件实例 ID 相同：正常处理。
- 记录有实例 ID，事件实例 ID 不同：按既有歧义语义处理，不得结算。
- 记录有实例 ID，但事件缺失实例 ID：**不得自动结算**，因为事件来源无法可靠验证。
- 旧持久化记录没有实例 ID：仅保留明确界定的兼容行为，并记录这是旧格式回退路径。
- 新派发无法取得可信实例身份：必须在真正发出外部动作之前按安全策略处理；不能默默把所有新任务降级成仅按 `action_id` 关联。

注意：`action_id` 仍然不是服务端幂等键。不得将实例 ID 或 `dispatch_key` 宣称为 exactly-once 保证。

如果需要区分旧记录与新记录，请优先利用现有 payload 序列化机制进行兼容性标记，不要为了方便直接引入新数据库迁移。

## 五、必须新增的测试

### A. Service 集成测试

使用真实本地 HTTP 服务与真实 `MinecraftRuntimeClient` 请求路径验证：

1. 第一次成功 health 返回实例 A，Service 会记录 A。
2. Python 保持运行，外部 Runtime 从 A 重启到 B。
3. 后续成功 health 或 status 返回 B，Service 能识别变化并触发一次对账。
4. 运行在 `auto_start_runtime=false` 时，实例识别仍然有效。
5. 暂时请求失败后恢复 A，不得误判实例变化。
6. 401、无效响应、缺失实例字段均不得伪造新身份。
7. 对账与新派发并发时，任务授权、动作调用计数和持久化状态仍满足安全不变量。
8. 身份变化的回调不会因为等待 `stop` 或发起健康检查而发生死锁。

### B. 事件关联矩阵

针对记录实例 ID 与事件实例 ID 的全部组合编写测试，包括：

- 相同实例；
- 不同实例；
- 事件缺少实例；
- 旧记录缺少实例；
- 未知 action_id；
- 重复终态事件；
- 多任务冲突关联；
- 旧实例事件迟到；
- Runtime 已恢复但旧动作不可查询。

测试必须断言最终任务状态、授权状态、`dispatch_state`、`replan_required`、持久化证据以及外部真实动作启动次数。

### C. Runtime 进程重启门禁

尽量使用真实 Node Runtime + Python Service 完成一次重启故障注入：

- 先让一个动作进入未决状态；
- 在 Python 不退出的情况下重启 Node Runtime；
- 通过真实健康检查路径确认新实例；
- 验证旧动作被歧义暂停，证据完整；
- 验证动作不会因 Runtime 重新可用而再次派发；
- 验证第二个无关的新任务不会因旧任务的恢复逻辑而绕过自身的确认门。

不能只调用 `TaskRuntime.on_runtime_instance_changed()` 的单元测试替代以上集成证据。

## 六、保持产品安全边界不变

以下约束全部保持：

- 不新增 Minecraft 工具或世界修改能力。
- 注册工具数量维持 19 个。
- `ACTION_RISK` 不变，`allow_medium=false` 不变。
- 计划批准与执行确认仍是两道独立确认门。
- 不绕过 `TaskAuthorization`、`TaskStepAuthorization`、Policy、AgentBridge 或 Action Runtime。
- LifeIntent、TaskProposal、AgentPlan 的职责语义不变。
- 多段路线最多 3 段，共享总距离预算不超过 24 格。
- `explore_legs=1` 默认值不变。
- 不加入无界重试，不宣称 exactly-once。

不修改用户真实 Character Bible、`.env`、密钥、`config/config.yaml` 或 `data/`。

## 七、发布与验收证据

完成修改后，执行：

- `ruff check .`
- `ruff format --check .`
- `mypy app`
- 全量 `pytest tests`
- WebUI typecheck、Vitest、build 和 E2E
- `minecraft_runtime` 全套测试
- 数据库版本、83 张业务表、完整性、外键和数据行数检查
- Runtime 身份变化、事件关联和派发计数的新增集成门禁
- 可安全执行的真实 Fabric 回归测试

保留原有测试阈值；不得为了使 CI 通过而放宽断言。若出现环境敏感 flake，记录失败点，按既有纪律重跑失败任务，并保留运行链接。

发布前确认：

- 源工作树与发布镜像预期内容一致；
- 发布镜像与 `origin/main` 最终 SHA 一致；
- `scripts/` 仍未发布；
- 无真实凭据进入日志、事件、任务 payload、WebUI 或 Git；
- 不引入不必要的数据库迁移；
- CI 报告包含每次运行的 URL/ID、最新 attempt、两个 job 的真实结论、实际 SKIPPED 列表。

## 八、验收门槛

只有当真实成功的身份响应能够触发实例观测、外部托管 Runtime 重启测试通过、事件缺失实例 ID 不再错误结算、并发派发没有二次动作或死锁，才可裁定 `PASS`。

只证明局部函数正确、只证明直接调用对账函数正确，或只证明 Managed Runtime 的启动流程正确，都不足以通过本阶段。

若核心集成问题尚存，应明确裁定 `PARTIAL`，列出失败证据与最小修正，不得用文档描述替代实际行为。

完成后再评估 Phase 7F.6；本阶段不要提前引入新的游玩能力。