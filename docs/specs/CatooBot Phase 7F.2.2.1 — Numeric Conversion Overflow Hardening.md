# CatooBot Phase 7F.2.2.1 — Numeric Conversion Overflow Hardening

## 目标

在当前 `0469c3a672983328627af81823ae248ecae40e3d` 基线上，补齐 `_finite_axis()` 对数值转换溢出的处理。

当前坐标域加固已覆盖 bool、NaN、±Infinity、错误类型、缺失字段及不合法字符串；本补丁只处理尚未覆盖的整数浮点转换溢出。

## 一、最小代码变更

修改 `app/tasks/exploration.py` 中的 `_finite_axis()`：

- 将 `OverflowError` 纳入坐标转换异常处理。
- 对无法转换为有限浮点数的输入，统一返回 `None`。
- 保留现有 `math.isfinite()` 检查。
- 保留对 bool、错误类型、非有限值的现有拒绝行为。
- 不新增任意世界坐标绝对上限。
- 不改变合法负坐标、小数及有限数字字符串的兼容性。
- 不改变 `verified_arrival()` 与任何下游调用链。

## 二、回归测试

在 `tests/test_exploration_feedback.py` 中新增测试：

1. 超大正整数不能导致 `_finite_axis()` 抛出 `OverflowError`。
2. 超大整数作为 x、y、z 任一坐标轴时，`_clean_position()` 必须返回 `None`。
3. `SUCCEEDED` + `checked/ok=True`，但实际坐标包含超大整数时，`verified_arrival()` 不得确认到达。
4. 相同异常坐标通过记忆桥时不得产生正向 `EVENT` 或 actionable `LOCATION`。
5. 合法有限坐标、已有终态矛盾测试及幂等测试继续通过。

测试应断言具体返回值和实际记忆写入结果，而不仅是异常没有向外传播。

## 三、禁止范围扩张

不修改：

- 工具注册表、`ACTION_RISK`、`allow_medium=false`。
- TaskRuntime、TaskAuthorization、TaskStepAuthorization、Policy 或两道确认门。
- LifeIntent、TaskProposal、AgentPlan、探索结果枚举及记忆模型。
- 数据库 schema、migration 36、真实 Character Bible、凭据或敏感配置。
- 多段探索及任何新增真实世界操作能力。

## 四、验证和发布

先运行新增测试及 `tests/test_exploration_feedback.py`，再运行：

- `ruff check .`
- `ruff format --check .`
- `mypy app`
- 全量 `pytest tests -q`
- WebUI typecheck、Vitest、build、E2E
- `minecraft_runtime npm test`
- 数据库 v36 → v36 幂等性核验

由于本补丁只改变输入数值转换异常处理，仍须保留真实 Fabric 门禁；不得以 mock 或跳过替代。按既有双目录发布纪律同步、提交并推送，最终核对 `main` 和两个 CI job 的结果。

交付报告如实列出改动、测试、CI 和任何未完成项。仅在实际执行的验证全部通过时裁决 PASS。