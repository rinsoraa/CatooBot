# CatooBot Phase 7F.5.3 Follow-up — Cancel/Expire Snapshot Race Closure

## 0. 任务目标

基于当前 GitHub `main`：

`2eee8311dcb8f8de44a07a8da5cdad4c0ecdc555`

完成一次严格限定范围的生命周期竞态修复。

本次不是增加 Minecraft 能力，不是放宽既有测试，也不是单纯补文档。唯一目标是证明：**`cancel()` 或 `expire()` 与动作响应结算并发时，不会用旧 TaskRecord 覆盖新持久化状态，从而丢失真实 action_id 和未决动作关联。**

最终需要能够给出确定性证据，而不仅是“全量 pytest 通过”。

## 1. 已确认的审计起点

请先独立检查以下路径，不要直接相信任务书中的结论：

- `app/tasks/runtime.py`
  - `cancel()`
  - `expire()`
  - `_cancel_record()`
  - `_require()` / `_save()`
  - `_settle()`
  - `_record_settlement_under_pause()`
  - `_inflight_step()`
  - `_consume_deferred_action_event()`
- `tests/test_inflight_cancel_expire.py`
- `tests/test_action_dispatch_safety.py`
- `app/tasks/store.py`

当前 `cancel()` / `expire()` 的风险顺序是：

1. `_require(task_id)` 获取记录快照。
2. `await _stop_action()`，期间其他协程可能完成 `_settle()` 并保存真实 `action_id`。
3. 根据之前的记录计算 `inflight`。
4. 将旧记录保存为终态。

如果步骤 2 期间存储中的记录已经从 `INTENT` 变成 `ACCEPTED`，步骤 4 可能覆盖动作 ID 和 pending 关联。

请先确认这一推断是否成立，尤其核对 SQLite 和 InMemory store 的读取、复制与覆盖语义。

## 2. 必须修复的竞态

### A. cancel 与迟到响应并发

使用确定性门闩强制以下顺序：

1. 派发意图已经持久化，真实动作调用进入受控窗口。
2. 开始 `cancel()`；确认取消流程已经读取旧快照，并阻塞在可控的 `minecraft_stop` 调用中。
3. 在取消尚未完成持久化状态转换前，让动作调用返回“已接受”，使 `_settle()` 获得真实 `action_id` 并尝试保存。
4. 再放行 `minecraft_stop`，让取消流程完成。
5. 检查最终存储，确认取消流程没有覆盖真实动作关联。

### B. expire 与迟到响应并发

使用独立用例，执行与 A 同等强度的交错。不要以 cancel 用例代替 expiry 用例。

### C. 真正的“已接受、响应尚未返回”窗口

修正或替换当前不准确的测试门闩。

目前 `GatedInvoker.__call__()` 在 `super().__call__()` 之前等待，导致底层动作接受逻辑尚未运行。请构造一个能明确区分以下时刻的替身：

- 请求已进入调用链；
- 底层动作已经接受，并已生成 action_id；
- 调用方仍未收到 TaskInvocation；
- 调用方收到响应并执行结算。

在调用方仍未收到响应时分别触发 cancel 和 expire。

必须证明这与“接受之前取消”的窗口是不同的测试，而不是同一场景改了测试名称。

## 3. 修复方案要求

可以选择适合现有架构的状态协调方式，例如：

- 对同一任务的生命周期转换与结算进行可验证的串行化；
- 将终态转换和后续异步 stop 分开，先确保持久化真相具有正确的生命周期状态；
- 或使用基于最新持久化版本的受控合并更新，避免整份旧记录覆盖新记录。

无论采用哪种方式，都必须满足：

1. 不使用旧快照无条件覆盖较新的 `ACCEPTED` / `SETTLED` 证据。
2. cancel 后任务保持 `CANCELLED`；expire 后保持 `EXPIRED`。
3. 如果外部动作已接受，必须保留真实 `action_id`、步骤关联及 pending 信息，直至真实终态事件收敛。
4. 接受动作后必须按现有 SAFE `minecraft_stop` 控制面请求停止；“请求过 stop”仍然不能解释成“动作已停止”。
5. 未知调用结果不等于执行失败：不得自动重发，不得宣称 exactly-once。
6. 事件先到与重复终态事件仍须遵守现有暂存、消费和幂等规则。
7. 必要时验证任务持久化更新与事件发布的先后顺序，防止向外发布成功结论后又被旧记录覆盖。

不要为了通过测试删除安全断言、改变动作计数口径或增加任意 sleep。

## 4. 测试矩阵与必须断言

对 cancel 和 expire 分别覆盖：

- 接受之前取消/过期，随后动作才被接受；
- 动作已接受但响应尚未返回；
- stop 调用仍在等待期间，动作响应先完成结算；
- 任务已进入终态后，真实终态事件迟到；
- 终态事件先于 action_id 持久化到达；
- 完成以上交错后重新读取真实 SQLite 存储。

每个相关用例至少断言：

- task state；
- step state；
- dispatch_state 与 dispatch_generation；
- action_id、pending_action_id、pending_step_id；
- authorization 和 replan_required；
- recovery 中的停止及派发证据；
- 世界动作启动次数；
- stop 调用次数；
- 收到真实终态事件后的状态；
- 再次 drive 不会产生新世界动作。

对在途接受情形，必须证明世界动作次数始终为 1，且迟到响应不能恢复终态任务或触发再次派发。

特别检查：取消期间先后发生的两次 `_save()` 不得让更完整的新记录被旧快照覆盖。

## 5. 保持不变的安全边界

不得新增 Minecraft 工具、改变 `ACTION_RISK`、改变 `allow_medium=false`、绕过两道独立确认门，或绕过 `TaskAuthorization`、`TaskStepAuthorization`、Policy、AgentBridge、ActionRuntime。

不得引入无界重试、自动重放未知动作、数据库新表或迁移。不要修改 Character Bible、`.env`、凭据、用户 `config/config.yaml` 或 `data/`。

`minecraft_stop` 仍必须通过现有 SAFE 控制面调用。`scripts/` 只允许在本地工作目录中作为测试脚本存在，禁止复制进发布镜像或提交到 GitHub。

工作目录继续遵守项目的双目录隔离约定。发布前明确检查源码、干净镜像、`origin/main` 的 SHA 与差异；不要把含本地配置或秘密的工作目录直接发布。

## 6. 验收门槛

先跑新增的确定性交错测试，再跑完整 Python、ruff、format、mypy、WebUI、Minecraft runtime 和 E2E 门禁。

如果环境相关 E2E flake 再次出现，必须保留失败证据、明确区分失败 job 与测试断言；不得修改阈值或断言来消除失败。

真实 Fabric smoke 应覆盖真实动作在途取消，记录真实 action_id、实际动作次数、stop 请求及终态事件收敛情况。如果不能真实验证某项，必须如实标记 PARTIAL，不得用替身测试冒充真机通过。

**最终交付必须包含：**

1. 根因与精确交错顺序；
2. 修复后的状态转换设计；
3. 新增的确定性用例以及每个用例证明了什么；
4. 全量测试结果和 CI run / job 链接；
5. 真实 Fabric 测试结果或明确未覆盖的窗口；
6. SQLite 完整性、迁移、凭据扫描和发布镜像核验；
7. 明确的 PASS / PARTIAL 裁决。

只有在旧快照覆盖风险被修复、真实“接受后响应未返回”窗口得到独立测试、cancel 与 expire 各自通过，以及完整门禁有证据支持时，才允许宣告本次生命周期竞态修复完成。