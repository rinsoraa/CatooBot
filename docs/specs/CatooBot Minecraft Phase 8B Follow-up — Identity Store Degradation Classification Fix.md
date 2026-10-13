# CatooBot Minecraft Phase 8B Follow-up — Identity Store Degradation Classification Fix

## 1. 基线与范围

当前基线：`7395553c14d0871e735422312543b3ba7e24c83d`

本任务只修复 Phase 8B 身份解析器对重复存储故障的诊断语义。不得重写身份系统，不得重新打开其他已验收阶段。

## 2. 已确认的问题

检查：

- `app/integrations/minecraft/identity_context.py`
- `app/integrations/minecraft/identity.py`
- `tests/test_phase8b_identity_context.py`

`IdentityResolver._resolve()` 通过比较 `degraded_reason` 前后变化判断本次读取是否失败。但 `IdentityStore` 会捕获读取异常并写入异常类型；相同类型的故障连续发生时，字符串可能不变，导致本次故障未被识别。

这会让身份查询实际失败却返回 `UNBOUND`，而不是 `DEGRADED`。

## 3. 必须实现的契约

1. 每次身份解析的存储读取结果都必须能准确区分：成功且无绑定、成功且已绑定、存储读取失败。
2. 同一种异常连续发生两次或多次时，每次受影响的解析都必须得到 `DEGRADED`，不得因错误标记没有变化而误判 `UNBOUND`。
3. 存储恢复后，后续成功的查询应依据实际数据恢复正常解析；不得因历史错误标记永久降级。
4. 必须考虑共享 `IdentityStore` 的并发访问，不能通过一个不具备请求隔离能力的全局标记清零操作制造竞态。
5. 优先采用能可靠表达单次读取结果的最小实现。若有必要修改既有 Store 接口，必须说明兼容性影响，并保持单一身份存储源；不得建立第二套 Store。
6. `DEGRADED` 仍然只代表身份上下文不可用，不得授予或改变任何 Minecraft 权限。

## 4. 新增确定性回归测试

至少覆盖：

- 首次读取失败 → `DEGRADED`。
- 同一种存储异常连续发生两次 → 两次都为 `DEGRADED`。
- 已有历史 `degraded_reason` 时再次发生同类错误 → 当前解析仍为 `DEGRADED`。
- 数据库恢复，且后续查询成功 → 根据真实数据返回正确的 `VERIFIED` 或 `UNBOUND`。
- QQ 与 Minecraft 两侧均满足上述降级契约。
- 并发身份解析期间，一个请求的失败状态不得污染另一个请求的成功结果，反之亦然。
- 身份层异常仍不影响普通聊天的继续执行。
- 既有身份、撤销、冲突、跨服务器隔离与权限边界测试全部保持通过。

测试不得依赖 `sleep` 或真实 LLM。应使用确定性的故障注入和可控并发门闩。

## 5. 不得改变的安全边界

- `ACTION_RISK` 与既有 Minecraft 工具数量不变。
- `allow_medium` 默认值、`trusted_players`、`TurnOrigin` 派生规则不变。
- 两道确认门、`TaskAuthorization`、`TaskStepAuthorization`、Policy、AgentBridge、Service 与 Action Runtime 保持不变。
- IdentityLink、关系阶段、长期记忆不得授予动作权限。
- 不得引入新的 Minecraft 动作、工具或无界重试。
- 默认不新增数据库表、不新增迁移，不修改真实运行数据。
- 不记录聊天原文、完整 UUID、QQ 号、密钥或思维链。

## 6. 验收门禁

运行：

- 新增身份降级回归测试。
- `pytest tests/test_phase8b_identity_context.py -q`
- 与身份桥、角色上下文和 Minecraft 安全门相关的测试。
- `pytest tests -q`
- `ruff check .`
- `ruff format --check .`
- `mypy app`
- 项目 CI，并核对实际 job 结论。

只报告真实执行的结果；不得通过放宽既有断言来取得 PASS。

## 7. 交付

使用独立提交，不与 Phase 8C 实现混合。提交报告必须包含：

- 修正前后 SHA。
- 故障根因与实际实现。
- 新增回归用例及精确结果。
- 全量测试、CI 与安全不变量核查。
- 是否涉及 Store API 改动或数据库 schema 变化。
- 工作区、发布镜像与 `origin/main` 状态。

**验收标准：** 每一次实际存储读取故障均能可靠识别为 `DEGRADED`；成功恢复后可以恢复准确解析；未绑定仍然是 `UNBOUND`；所有动作授权语义不变。完成后只需对本缺陷进行定向复核，不得因此重审其他已通过阶段。