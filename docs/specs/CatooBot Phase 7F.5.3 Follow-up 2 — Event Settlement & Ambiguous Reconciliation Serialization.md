# CatooBot Phase 7F.5.3 Follow-up 2 — Event Settlement & Ambiguous Reconciliation Serialization

## 0. 基线与目标

从当前 GitHub main 开始：

`68946234123e6cb67d04bcba2374a20edf762e5b`

本阶段只修复生命周期状态更新的一致性，不增加任何 Minecraft 功能。

目标：让 `on_action_event()`、`_pause_ambiguous_dispatch()`、`_settle()`、`cancel()` 和 `expire()` 对同一 TaskRecord 的状态转换形成一致、可证明的并发协议。

必须保持：

- `CANCELLED` 不被任何迟到事件、歧义回调或旧快照改写为非终态；
- `EXPIRED` 不被任何迟到事件、歧义回调或旧快照改写为非终态；
- 真实 `action_id` 与 pending 关联不能被旧记录覆盖；
- 已确认的 `SETTLED` 不能被旧快照降级为 `INTENT`、`ACCEPTED` 或 `AMBIGUOUS`；
- 未知结果绝不触发自动重发，也不宣称 exactly-once。

## 1. 必须处理的两个审计发现

### A. on_action_event 未参与生命周期锁

当前事件处理路径可以读取 TaskRecord、修改步骤和 pending 关联，然后写回整份 payload，但没有与 cancel/expire/_settle 共用生命周期锁。

请实现安全串行化。不能只把原有函数从头到尾包进锁、继续使用锁外读出的旧快照。

建议在需要更新唯一匹配任务时：

1. 获取该任务的生命周期锁；
2. 在锁内重新查询 `find_by_action(action_id, include_terminal=True)`；
3. 重新验证匹配数量、步骤、dispatch generation、dispatch key 和 Runtime 实例身份；
4. 从最新持久化记录构造更新；
5. 写入事件证据和 `SETTLED` 状态；
6. 如果任务已经是 CANCELLED/EXPIRED，则只收敛步骤证据与 pending 关联，绝不重新进入 RUNNING、WAITING_ACTION、PAUSED，也不调用 drive()。

若重查后匹配记录消失，必须区分已结算的重复事件、仍未建立关联的早到事件和真正未知事件，不得因正常竞争将事件永久丢弃。

多个记录匹配同一 action_id 的歧义分支也必须保持安全，并避免按不同顺序获取多把任务锁造成死锁。

### B. _pause_ambiguous_dispatch 在持锁时使用旧快照

当前包装方法获取生命周期锁后，直接把调用方传入的 record 和 step 交给内部方法。锁不等于数据新鲜度。

修复要求：

1. `_pause_ambiguous_dispatch()` 获得任务锁之后，重新读取持久化 TaskRecord；
2. 使用 `step_id` 重新定位当前步骤；
3. 对照当前 dispatch generation、dispatch key、dispatch state、action_id 和 pending_action_id；
4. 如果任务已经 CANCELLED/EXPIRED，保持其真实终态，只记录仍有效的歧义证据；不得改成 PAUSED；
5. 如果此步骤已变成 SETTLED 或派发代次已经改变，不能用旧回调把新状态倒退为 AMBIGUOUS；
6. 如果动作仍处于可能已外发、结果未知的阶段，保留 fail-closed、清授权、不自动重发的既有语义。

不要仅依靠调用方传入的 `terminal_record` 布尔值判断当前记录是否为终态。

## 2. 新增的确定性竞态测试

新增独立测试文件，例如 `tests/test_task_lifecycle_event_race.py`。通过可控 store 包装器、`asyncio.Event` 门闩与真实 SQLite 测试确定性交错，不使用 sleep 作为同步机制。

至少覆盖以下场景：

### 场景 1：事件已读取旧记录，取消先完成

1. `on_action_event()` 读取 WAITING_ACTION/ACCEPTED 记录。
2. 门闩阻止事件写入 store。
3. cancel 完成终态落盘。
4. 放行事件处理。
5. 断言最终 task 仍为 CANCELLED，步骤证据可以 SETTLED、pending 可以清空，但任务不能复活；事件处理也不得触发新动作。

### 场景 2：事件与取消按相反顺序完成

让事件首先在同一任务生命周期锁内结算，然后再执行 cancel。

最终任务应按取消语义保持 CANCELLED；事件证据仍然真实，且已 SETTLED 的步骤不能被降级为 INTENT / ACCEPTED / AMBIGUOUS。

### 场景 3：歧义处理等待锁时，取消先写入终态

1. 使用旧 RUNNING / INTENT 快照启动 `_pause_ambiguous_dispatch()`。
2. 让该方法等待生命周期锁。
3. cancel 先持锁并写入 CANCELLED。
4. 放行歧义处理。
5. 断言任务保持 CANCELLED，不出现 PAUSED 覆盖终态，且真实动作关联、授权状态及歧义证据符合最新持久化事实。

### 场景 4：异步身份探测与取消并发

在 `_resolve_dispatch_identity()` 等待 `_identity_probe()` 时触发 cancel。探测返回缺失或不一致的身份后，歧义回调不得把 CANCELLED 改成 PAUSED，更不得发出世界动作。

### 场景 5：SETTLED 单调性

控制真实终态事件、歧义回调与取消的交错顺序，证明已确认结算的步骤不会被陈旧回调降级。

### 场景 6：真实 SQLite 回读

至少一个事件竞态与一个歧义回调竞态使用真实 SQLite，并用新的 store 实例读取最终记录。

### 每个相关用例的断言

检查 task state、step state、dispatch_state、dispatch generation、action_id、pending_action_id、pending_step_id、authorization、replan_required、Runtime 实例身份、recovery 证据、动作启动次数、stop 调用次数和最终持久化结果。

所有终态事件都必须保持幂等，终态任务的 drive() 不得再次发起世界动作。

## 3. 审查生命周期锁与 SAFE stop

现有 `_LifecycleLock` 注释称不能在持锁期间发起外部调用，但 `_pause_ambiguous_dispatch_locked()` 和 `_record_settlement_under_pause()` 仍包含锁内 `_stop_action()` 等待。

请逐一审查这些调用链，并决定是否能统一为：

1. 锁内读取最新状态并保存安全的中间状态；
2. 锁外调用已有 SAFE `minecraft_stop`；
3. 再获取锁、读取最新记录，只合并停止请求证据。

不能让锁外 SAFE stop 的返回值覆盖更晚的终态事件。`stop_confirmed` 没有真实停止证据时必须保持 False。

如果有调用链无法安全重构，必须列出具体路径、等待边界与确定性重入测试；不能笼统地把所有持锁 stop 都描述成已经证明的“有界延迟”。

## 4. 产品与安全边界

不得新增 Minecraft 工具，不得修改 `ACTION_RISK`、`allow_medium=false`、两道独立确认门或绕过 TaskAuthorization、TaskStepAuthorization、Policy、AgentBridge、ActionRuntime。

不得增加自动重发、无界重试、新表或数据库迁移；不得修改 Character Bible、`.env`、凭据、用户 `config/config.yaml` 或 `data/`。

`minecraft_stop` 必须继续经现有 SAFE 控制面。真实测试脚本继续只留在本地 `scripts/`，不得发布到 GitHub。

保持源码工作树、发布镜像和 `origin/main` 的分离；发布前核对 SHA、差异、凭据扫描与 clean status。

## 5. 门禁和最终交付

先运行新增的确定性测试，再跑全量 pytest、ruff、format、mypy、WebUI typecheck/Vitest/build/E2E 和 `minecraft_runtime npm test`。

使用真实 SQLite 验证持久化读回；真实 Fabric 测试要验证动作关联、取消终态、迟到事件收敛和零重发。无法实际验证的部分必须明确标记 PARTIAL。

CI flake 只允许保留证据并按纪律重跑失败 Job，不得更改断言、超时或阈值来伪造通过。

交付内容必须包含：

1. 两个竞态的根因和精确交错图；
2. 所有状态修改入口与生命周期锁的覆盖清单；
3. 新增测试对应的具体证明；
4. SQLite 完整性、迁移、凭据扫描及镜像核验；
5. CI 每个 run、attempt、job 的结论；
6. 真实 Fabric 验证结果；
7. 已知剩余限制；
8. 明确 PASS / PARTIAL 裁决。

只有 `on_action_event()`、`_pause_ambiguous_dispatch()` 和 `_settle()` 等所有相关状态更新路径都能防止旧快照覆盖新事实，且上述竞态全部得到独立确定性验证，才允许宣布生命周期竞态收口。