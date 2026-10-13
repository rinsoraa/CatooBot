**CatooBot Minecraft Phase 8A — Routing Target Contract Fix**

基于当前提交 `8c4efdc578b8affac3cc8e3477e411c54a216184`，只修复下面这一个已确认的契约不一致。不要扩大范围，不要改动任何 Minecraft 权限或动作能力。

1. 检查 `app/tools/router.py` 的 `ToolOrchestrator.run()`。当本轮没有任何可解析的工具调用，即 `call is None` 且 `route.selected` 为空时，除设置 `reason_code="model_no_tool_call"` 外，还必须将 `route.route_target` 设为 `RouteTarget.CHAT_ONLY.value`。
2. 在 `tests/test_phase8a_action_routing.py` 的 `test_model_not_calling_a_tool_is_distinct_from_no_candidates` 中，新增断言：`route.route_target == RouteTarget.CHAT_ONLY.value`。
3. 保留当前 `last_traces` 清空顺序及所有既有执行行为。不得修改候选检索阈值、模型提示词、工具调用次数、`ACTION_RISK`、`trusted_players`、确认门或授权链。
4. 运行新增回归测试、相关工具路由测试、`ruff check .`、`ruff format --check .`、`mypy app`，并运行项目要求的 CI。只报告实际执行的结果。
5. 单独提交并发布，不与 Phase 8B 改动混在一个提交中。提交报告注明修正前后 SHA、测试结果与镜像状态。完成后即可重新核验并接受 Phase 8A；不得借此重新打开其他已通过的阶段。

**验收条件：** 无候选与有候选但模型未选择工具都归为 `CHAT_ONLY`，但保留各自不同的原因码；真正选中工具时，既有路由与执行行为不变。