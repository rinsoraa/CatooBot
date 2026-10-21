# CatooBot Minecraft R5 正式验收报告

日期：2026-10-22。状态：**IN_PROGRESS，真实矩阵与工程已 PASS，等待发布/CI 回执。**

## 1. 范围

R5 首期只完成“有界敌对生物交互与战斗安全”：

- 白名单：僵尸、骷髅、蜘蛛、洞穴蜘蛛；
- 单次只攻击一个冻结目标；每击独立 `minecraft_attack` 动作；
- 最多 4 击；每击间隔 650ms（尊重武器冷却）；只打 3.5 格内，不导航；
- 出手前检查生命/饥饿/主手剑；玩家/村民/宠物/爆炸物默认拒绝；
- 攻击前用 SAFE 实体观察取得 entity_id/UUID/类型/距离，冻结进 plan_hash；
- 攻击后用 SAFE 实体观察确认目标真的消失；目标仍存在则最后一步失败，不宣告击杀；
- 支持 STOP/CANCEL、目标丢失、低生命、无武器、超范围、村庄目标拒绝。

明确不包含：PVP、玩家攻击、无限追击/巡逻、自动换武器/补货、飞行/远程特殊怪、Boss、R6–R8。

## 2. 真实服务器矩阵

正常 `Bot.start`，隔离 SQLite，公开测试 Bible，测试服 `D:\Minecraft server\1.21.1`。最终 `attempt-07`，**9/9 PASS**：

| 用例 | 结果 | 证据 |
|---|---|---|
| kill-zombie | PASS | 4 次攻击 + 实体消失核验；任务 SUCCEEDED |
| kill-zombie-drop | PASS | 僵尸手持 iron_ingot，带掉落率 1.0；XP **5→10**；掉落物实测 iron_ingot |
| no-target | PASS | 规划期 `combat.no_target`，零任务零动作 |
| low-health | PASS | 生命被压到 6；规划期 `combat.low_health` |
| no-sword | PASS | 主手为空；规划期 `combat.weapon_required` |
| villager-forbidden | PASS | “攻击那只村民”拒绝，零任务 |
| out-of-range | PASS | 目标 8 格外；规划期 `combat.no_target` |
| target-lost | PASS | 确认前外部杀掉目标；任务 PAUSED，无假击杀 |
| stop-before-attack | PASS | 确认后立即 STOP；任务 CANCELLED，僵尸仍存活 |

证据：`data/r5/combat-20261022/r5-results.json`、`task-events.json`、`bridge-events.json`、`r5.log`；脱敏汇总 `docs/evidence/MINECRAFT_R5_BOUNDED_COMBAT.json`。

## 3. 历史失败（全部保留）

| attempt | 结果 | 失败与修复 |
|---|---|---|
| 01 | 0/1 | `combat_verify` 没解包 runtime 嵌套的 `result.entities`，误判目标消失；任务假 SUCCEEDED。修复：显式解包后再读实体列表 |
| 02 | 0/1 | 4 击间隔仅 300ms，低于剑冷却，僵尸未死；verify 正确失败。修复：650ms/击 |
| 03 | 2/3 | low-health 夹具的 instant_health 在伤害后把生命回满；修复：先 effect clear 再 damage |
| 04 | 8/8 | 首次全矩阵 PASS；增加掉落/经验用例 |
| 05 | 1/2 | 掉落用例选了最近的无关 glow ink sac；修复：按 iron_ingot NBT 精确选实体 |
| 06 | 9/9 | 首次全 PASS |
| 07 | 9/9 | 最终源码（测试注册表清单更新后）干净重跑，全 PASS |

所有 attempt 目录、原始 stderr、任务状态、事件日志均保留，未删除或改写。

## 4. 门禁映射

| 门禁 | 结果 | 说明 |
|---|---|---|
| R5-A1 目标身份与观察 | PASS | SAFE entities 观察 + entity_id/UUID/type/distance 冻结；玩家/村民/宠物/爆炸物拒绝 |
| R5-A2 安全前置 | PASS | 生命、食物、主手剑、距离检查；低生命/空手拒绝 |
| R5-A3 有界攻击 | PASS | 每击独立动作、650ms 冷却、最多 4 击、无导航、最后 SAFE 验证实体消失 |
| R5-A4 撤退/取消/异常 | PASS | STOP 后 CANCELLED 且目标仍存活；目标丢失 PAUSED；不自动改绑/追新目标 |
| R5-A5 真实服务器矩阵 | PASS（玩家实机待补） | 9/9；真实覆盖僵尸/村民/目标丢失/STOP/低生命/空手/超范围；**真实第二玩家未启动**，玩家拒绝由 Node runtime 单测和产品解析单测覆盖 |
| R5-A6 工程与发布 | PASS（发布待补） | 5099 Python passed（823.71s）、Node 全链 ALL CHECKS PASSED、ruff/format/mypy 通过；发布/CI 待补 |

## 5. 工程验证

- Python：`.venv/Scripts/python.exe -m pytest tests -q --tb=short` → **5099 passed in 823.71s**，exit 0。
- Node：`minecraft_runtime npm test` → exit 0；全部单元组和 E2E **ALL CHECKS PASSED**；新增 entities/attack 12 项检查。
- 静态：`ruff check app tests` PASS、653 files formatted、mypy 356 files PASS。
- Java：R5 未改 `server_mod` 源或候选 JAR，字节未变；沿用 R3 发布版 7 个 pure suite / 158 项断言证据。
- 首次工程失败：9 个旧测试仍写死 19 个工具；已更新为批准的 22 个工具，未放宽风险/授权策略。
- 完整日志：`data/r5/combat-20261022/`；脱敏汇总 `docs/evidence/MINECRAFT_R5_ENGINEERING.json`。

## 6. 边界

- 真实矩阵未启动第二玩家客户端；“玩家实体不可攻击”由 runtime 单测（`target.player`）和解析拒绝覆盖，不冒充真服玩家测试。
- 首期只覆盖 NoAI 僵尸靶；未验证移动追击、骷髅远程、蜘蛛跳跃、洞穴蜘蛛毒、玩家进入区域、低生命撤退。
- 未验证 R6–R8、Integrated/LAN。
- 事实来源：真实实体观察、攻击动作回执、死亡后实体消失、掉落物 NBT、XP 字段；不依赖动作自述。
