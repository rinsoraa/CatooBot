# CatooBot Phase 7F.2.2 — Coordinate Domain Hardening

## 目标

在当前 `07f722f95aa72a545b76bceb88c91052ba47152b` 基线上，收紧探索坐标的数值有效性验证。

Phase 7F.2.1 已统一终态到达判定；本阶段只处理一个剩余问题：

**坐标转换成功，不代表坐标有效。**

必须确保 NaN、正负 Infinity、布尔坐标以及其他不可接受的数据，绝不能通过 `verified_arrival()` 成为正向探索记忆的依据。

这是一个小型防御性补丁，不是新能力阶段。

## 一、先核对现状

核对权威源、发布镜像及当前 Git 状态，确认基线和已有改动。重点检查：

- `app/tasks/exploration.py`
  - `_clean_position`
  - `verified_arrival`
  - `exploration_facts`
  - `arrival_fact`
- `app/memory/minecraft/model.py`
  - `number_or`
  - `cluster_position`
- `tests/test_exploration_feedback.py`

不要覆盖无关工作区改动，也不要重写已经通过验收的 Phase 7F.2.1。

## 二、最小修复

### 1. 收紧坐标解析

调整 `_clean_position()`，要求 x、y、z 三个值都能转换为有限的实数。

具体要求：

- 拒绝 `bool`，不要让 Python 的 `True`、`False` 被当作 1、0。
- 使用 `math.isfinite()` 或等价的严格检查，拒绝 NaN、正 Infinity、负 Infinity。
- 数值转换失败时返回 `None`，继续保持 fail-closed。
- 不要随意规定 Minecraft 世界坐标的固定绝对上限；合法负坐标和合法小数坐标仍须允许。
- 对数字字符串的兼容策略先检查当前真实数据来源与序列化格式。若保留兼容性，也必须拒绝 `"NaN"`、`"Infinity"` 等非有限值。

尽量复用现有 `_clean_position()`，不要为到达验证、新事实提取和到达事件再分别实现三套坐标检查。

### 2. 保持调用链不变

`verified_arrival()` 仍必须同时要求：

- 任务最终状态严格为 `SUCCEEDED`；
- `verification` 是有效映射；
- `checked is True`；
- `ok is True`；
- `position_within` 含有效、有限的实际 x/y/z 坐标。

任何条件不满足，均不得产生已验证到达坐标。

`exploration_facts()`、`arrival_fact()`、`exploration_outcome()` 继续依赖现有统一判定，不得绕过。

## 三、必须新增的测试

建议以参数化测试覆盖以下输入：

- x、y 或 z 为 NaN。
- x、y 或 z 为正 Infinity、负 Infinity。
- x、y 或 z 为布尔值 `True`、`False`。
- 缺字段、错误类型、不可转换的字符串。
- 合法负坐标、合法小数坐标仍正常通过。
- 如果保留数字字符串兼容性，合法有限数字字符串仍符合预期。

必须验证两层行为：

1. 单元层：无效坐标使 `_clean_position()` 返回 `None`，且 `verified_arrival()` 不确认到达。
2. 记忆层：对于最终状态 `SUCCEEDED`、`checked/ok=True`，但坐标为 NaN 或 Infinity 的矛盾数据，不能写入正向 `EVENT` 到达经历或 actionable `LOCATION` 新事实。

同时回归验证：

- `CANCELLED` / `FAILED` / `EXPIRED` 加矛盾成功验证仍然不会写入正向事实。
- 正常 `SUCCEEDED`、有效坐标、合格 POI 仍能完成原有记忆回流。
- 重复发布依然幂等。
- 不新增无关测试数据库或持久化产物。

## 四、明确禁止

- 不改变 LifeIntent → TaskProposal → AgentPlan 既有链路。
- 不改变两道批准 / 确认门。
- 不修改 19 个工具的注册表、`ACTION_RISK` 或 `allow_medium=false`。
- 不修改 TaskAuthorization、TaskStepAuthorization、Policy、停止、取消、暂停、超时或重规划语义。
- 不新增数据库表、迁移或第二套记忆系统。
- 不接入多段探索，不增加真实世界操作能力。
- 不修改真实 Character Bible、凭据、`.env` 或敏感配置。

## 五、验证和交付

先运行新增测试与探索反馈测试，再按现有流程串行运行全量门禁：

- `ruff check .`
- `ruff format --check .`
- `mypy app`
- `pytest tests -q`
- WebUI typecheck、Vitest、build、Playwright E2E
- `minecraft_runtime npm test`
- 数据库 v36 → v36 幂等性核验

继续执行现有两个真实 Fabric 门禁：

- `scripts/feedback_smoke_real.py`
- `scripts/explore_smoke_real.py --sections all`

真机门禁不可由 mock、跳过或仅查看退出码替代。

按现有双目录发布纪律交付：

- 权威源目录：`E:\WorkSpace ZCode\CatooBot`
- 发布镜像：`E:\WorkSpace ZCode\CatooBot_github`

真机 smoke 脚本仅保留在本地，不加入 GitHub。推送后核对提交、远端 `main`、工作流 head SHA 与两项 CI job 结果。

## 六、验收标准

1. 有限合法坐标保持兼容。
2. NaN、Infinity、布尔坐标不能通过严格到达判定。
3. 无效坐标绝不产生正向到达记忆。
4. Phase 7F.2.1 的终态矛盾测试继续通过。
5. 原有 LifeIntent 反馈闭环与两道权限门行为不变。
6. 全量测试、真机门禁、CI 均有真实通过证据。
7. 明确列出所有未执行或 SKIPPED 项，不得把未执行写成 PASS。

只有证据完整时才能给出最终 PASS。