# CatooBot Minecraft R7 正式验收报告

日期：2026-10-22。状态：**COMPLETE**。

## 1. 范围

R7 首期把真实任务经历接入既有 Phase 7E 程序性技能层：

- 真实成功任务 -> CANDIDATE；
- 第二条独立成功 -> ACTIVE；
- 新任务检索 ACTIVE 技能，检查适用性，作为既有规划/任务链的计划候选；
- 复用成功追加证据；失败/部分成功/未知不写成成功；
- 不新增执行器、不注册新工具、不改变风险/授权/确认规则。

R7 没有新增生产代码；复用已发布 R6 冻结字节中的 SkillService/SkillAwarePlanner/SkillStore。

## 2. 真实服务器矩阵

正常 Bot.start、隔离 SQLite、公开测试 Bible。`attempt-01` **5/5 PASS**：

| 用例 | 结果 | 证据 |
|---|---|---|
| success-1 | PASS | 真实泥土采集 SUCCEEDED；技能 CANDIDATE，success=1 |
| success-2 | PASS | 第二条独立采集 SUCCEEDED；技能 ACTIVE，success=2，reason=promoted_by_independent_success |
| skill-reuse | PASS | 检索到 ACTIVE 技能，计划复用 dig/dropped_items/pickup/inventory；任务 SUCCEEDED，success=3 |
| reuse-counterexample | PASS | 确认前外部移除目标；任务 REPLANNING，未宣告成功；技能成功计数未增加 |
| not-applicable | PASS | 请求圆石但现场无石头；不适用/无目标，规划期拒绝 |

证据：`data/r7/skills-20261022/r7-results.json`、`r7.log`、`task-events.json`；脱敏汇总 `docs/evidence/MINECRAFT_R7_SKILL_CLOSURE.json`。

## 3. 确定性技能回归

- `tests/test_skill_learning.py`、`test_skill_service.py`、`test_skill_wiring.py` → **81 passed in 4.48s**。
- 覆盖：资格门、CANDIDATE→ACTIVE、独立证据、适用性、计划候选、复用回流、失败/歧义、版本/重启、工具面变化、授权不放大。
- 真实矩阵的失败复用只到 REPLANNING；失败→STALE/INVALIDATED 的状态机由上述确定性测试覆盖，没有把“未成功”冒充成反例学习完成。

## 4. 门禁映射

| 门禁 | 结果 | 说明 |
|---|---|---|
| R7-A1 经历与来源 | PASS | 真实 TaskRecord/服务端采集证据生成候选；无来源不生成 |
| R7-A2 候选与冲突 | PASS | CANDIDATE→ACTIVE 需第二条独立成功；81 项确定性测试覆盖冲突/撤销/版本 |
| R7-A3 适用性判断 | PASS | 检索时检查服务器/工具面/资源类；不适用回退；真实 not-applicable |
| R7-A4 受控复现 | PASS | 复用仍走既有规划/确认/执行/核验；真实复用成功；失败不宣告成功 |
| R7-A5 真实服务器矩阵 | PASS | 5/5；世界恢复 108 文件、哈希一致 |
| R7-A6 工程与发布 | PASS | 81 项技能测试通过；生产代码自 R6 发布未变，R6 全量 5106 pytest/Node/静态证据适用；发布 `4e797ca`，CI 39684566873 三 job success |

## 5. 发布

- 发布提交：`4e797ca1deebf9c442625a3fc55ebd26b94bbb03`
- CI：https://github.com/rinsoraa/CatooBot/actions/runs/39684566873 ，三 job 全部 success

## 5. 边界

- 真实矩阵只覆盖采集类技能；放置/战斗/交付技能尚未接入技能层（现有 Phase 7E 只支持资源类）。
- 真实 counterexample 终止在 REPLANNING，未在真实服上跑到 STALE/INVALIDATED；该状态机由确定性测试覆盖。
- 未验证跨角色/跨服务器迁移、R8 长期闭环。
- 技能不注册工具、不扩大权限、不替代实时观察。
