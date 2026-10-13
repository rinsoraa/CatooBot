# Phase 8C Follow-up — Planning Failure Must Not Fall Through to Tool Execution

## 目标

修复 MC 聊天中 `follow` / `explore` 请求在规划服务不可用或抛异常时，回退到 `CharacterRuntime.respond()` 并可能再次进入 `ToolOrchestrator` 的路径。

## 要求

1. 先区分普通聊天、纯查询、控制命令与已识别的受支持多步目标。前三类保持现有语义，不得误接管。
2. 对已经识别的 `follow` / `explore` 目标，如果计划服务未装配、调用抛异常或规划失败，返回明确的用户可见失败答复，并记录 `BLOCKED` 路由和稳定原因码，例如 `plan_service_unavailable` 或 `plan_service_error`。
3. 在该失败分支中不得返回用于普通聊天回退的 `None`；不得因规划失败而再次把原始目标送入普通角色工具链。
4. 检查计划服务可能抛异常的阶段，尤其是任务记录创建后、计划关联前后。必须确保不会遗留可执行的孤儿任务；如需补偿，复用既有取消/补偿机制。
5. 不得修改 `ACTION_RISK`、工具数量、`allow_medium` 默认值、两道确认门、`TaskAuthorization` / `TaskStepAuthorization` 或其他授权规则。
6. 不得新增工具、动作、数据库表或迁移。

## 测试要求

- 计划服务缺失时，受支持多步目标得到明确失败答复、路由为 BLOCKED，且不进入 `CharacterRuntime` 的工具执行路径。
- 计划服务抛异常时，验证 `ToolOrchestrator` 未被调用、没有新增世界步骤、没有伪成功状态。
- 覆盖任务已创建但计划关联失败的补偿路径，验证不存在可继续执行的孤儿任务。
- 普通聊天、SAFE 查询、控制命令保持原有行为。
- 成功路径仍然经既有有界计划、明确确认及 TaskRuntime。
- 执行相关测试、全量 pytest、Ruff、格式检查、Mypy，并核对最终 diff。

## 验收

只在实际证据证明规划失败不会回退到另一条工具执行路径、不会遗留可执行孤儿任务，且回归门禁通过后才裁定 PASS。作为独立修复提交发布，不改写 Phase 8C 已有历史记录。