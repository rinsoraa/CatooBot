# CatooBot Minecraft R4 正式验收报告

日期：2026-10-22。状态：**COMPLETE**。

## 1. 范围

R4 只完成“受限方块放置与简单建造”的首期能力：

- 单一材料；
- 1–3 个目标格（合同上限 4），全部位于玩家所在方块格中心的 3×3×3 区域；
- 每个目标格显式坐标、face、参考方块、主手物品；
- 只使用主手已有材料，不自动装备/合成/补货/导航；
- 不覆盖非空气方块，不自动搭桥/拆除/大面积改造/爆炸物/红石。

产品改动：

1. `app/tasks/build_plan.py`：确定性 build 请求解析、区域/材料/target/reference 合同、逐步 `minecraft_place` 计划。
2. `app/tasks/turn.py`：正常任务入口识别 build 请求，规划失败不创建任务；成功进入既有 TaskProposal/TaskRuntime/确认链。
3. `app/tasks/validation.py`：把 `minecraft_place` 摘要改成用户可读的“在 (x,y,z) 放一个 item”。
4. `app/integrations/minecraft/task_adapter.py`：补齐 `minecraft_place` 的 TaskRuntime 服务路由。
5. `tests/test_build_contract.py`、`tests/test_build_plan.py`、`tests/test_build_turn.py`。

## 2. 真实服务器矩阵

正常 `Bot.start` 装配，隔离 SQLite，公开测试 Bible，测试服 `D:\Minecraft server\1.21.1`。最终运行 `attempt-12`，**10/10 PASS**：

| 用例 | 结果 | 关键证据 |
|---|---|---|
| single-dirt | PASS | 确认前空气；确认后目标为 dirt；任务 SUCCEEDED |
| single-crafting_table | PASS | 目标为 crafting_table；任务 SUCCEEDED |
| multi-3-dirt | PASS | 3 个目标格全部为 dirt；任务 SUCCEEDED |
| material-insufficient | PASS | 规划期拒绝 `build.material_missing_or_insufficient`，零任务零动作 |
| target-occupied | PASS | 规划期拒绝 `build.target_occupied`，stone 保留 |
| reference-missing | PASS | 规划期拒绝 `build.reference_missing`，零动作 |
| outside-region | PASS | 规划期拒绝 `build.cell_outside_region` |
| external-change | PASS | 确认前把目标改为 stone；任务 PAUSED/ACTION_FAILED，未覆盖、未宣告成功 |
| wrong-owner | PASS | 错误 owner 确认被拒绝，任务仍 PENDING_CONFIRMATION，目标仍空气 |
| cancel-partial | PASS | 第一格成功后 STOP；最终 CANCELLED，恰好 1 格已放置，未自动回滚/补放 |

证据：`data/r4/bounded-build-20261021/r4-results.json`、`task-events.json`、`assembly-receipt.json`、`r4.log`；脱敏汇总 `docs/evidence/MINECRAFT_R4_BOUNDED_BUILD.json`。

## 3. 历史失败（全部保留）

| attempt | 结果 | 失败与修复 |
|---|---|---|
| 01 | 0/1 | `minecraft_place` 没有 TaskRuntime 服务路由，DISPATCH_AMBIGUOUS/PAUSED；补 `_place` 路由 |
| 02 | 0/1 | 误报 reference_missing；检查夹具时序 |
| 03 | 2/10 | 3 格连线用浮点玩家位置判区域，x-1 被误判越界；改用玩家方块格中心 |
| 04 | 0/1 | 计划要求 inventory_consumed，但最终背包快照仍为 1；R4 完成门改为逐格真实方块读回，不把消耗当完成条件 |
| 05 | 2/10 | 多格夹具 reference 偶发空气；显式修复每个目标脚下参考方块 |
| 06 | 1/10 | 快速 fill/setblock 后客户端 chunk 未更新；夹具等待 0.5s |
| 07 | 2/10 | 多格目标包含 bot 脚下格，服务器拒绝放置；目标移到 x+1,z-1..z+1 |
| 08 | 4/10 | target-occupied 夹具未等待更新，规划读到空气；拒绝用例同样等待 0.5s |
| 09 | 10/10 | 首次全 PASS；源码仅剩 reference 错误细节字符串差异 |
| 10 | 10/10 | 最终源码干净重跑，全 PASS |
| 11 | 10/10 | 加入显式数量校验后的干净重跑，全 PASS |
| 12 | 10/10 | 加入用户可读放置摘要后的最终源码重跑，全 PASS |

所有 attempt 目录、原始 stderr、失败任务状态均保留，未删除或改写。

## 4. 门禁映射

| 门禁 | 结果 | 说明 |
|---|---|---|
| R4-A1 请求与区域合同 | PASS | 材料/数量/区域/目标/参考/主手校验；拒绝路径零任务零动作 |
| R4-A2 授权与冻结 | PASS | plan_hash 冻结逐步参数；确认前零世界修改；错误 owner 拒绝；MEDIUM 风险确认 |
| R4-A3 逐格执行与验证 | PASS | 每格一个 `minecraft_place`，runtime 放置后真实读回；全部步骤成功才 SUCCEEDED |
| R4-A4 取消/部分完成/外部变化 | PASS | STOP 后 CANCELLED 且 1 格保留；外部占位变化 PAUSED/ACTION_FAILED，未覆盖 |
| R4-A5 真实服务器矩阵 | PASS | 10/10；测试后恢复 108 文件、哈希一致（恢复脚本 PID 62492）；随后用候选 JAR 重启测试服 PID 44904 |
| R4-A6 工程与发布 | PASS | 5088 Python passed（661.08s）、Node 全链 ALL CHECKS PASSED、ruff/format/mypy；发布 `7c3a5a4`，CI 39638936965 三 job success |

## 5. 工程验证

- Python：`.venv/Scripts/python.exe -m pytest tests -q --tb=short` → **5088 passed in 661.08s**，exit 0。
- Node：`minecraft_runtime npm test` → exit 0；全部单元组通过，E2E **ALL CHECKS PASSED**。
- 静态：`ruff check app tests` PASS、647 files formatted、mypy 352 files PASS。
- Java：R4 未改 `server_mod` 源或候选 JAR，字节未变；沿用 R3 发布的 7 个 pure suite / 158 项断言证据，不重复冒充新跑。
- 完整日志：`data/r4/bounded-build-20261021/`；脱敏汇总 `docs/evidence/MINECRAFT_R4_ENGINEERING.json`。

## 6. 发布

- 发布提交：`7c3a5a41adeddc89f4f98ffe31621ec80c6cd275`
- CI：https://github.com/rinsoraa/CatooBot/actions/runs/39638936965 ，三 job 全部 success

## 6. 边界

- 首期真实矩阵只覆盖 `face=up`；其他 face 由 `minecraft_place` 既有能力支持，但未做 R4 真服矩阵。
- 解析器当前只接受单块或单轴直线范围；复杂结构、自动找支撑面、自动导航/装备/补货不在 R4。
- 未验证 Integrated Server / LAN；未验证 R5–R8。
- 事实来源：服务端真实世界读回 + 任务事件；未用聊天文案或动作自述代替成功。
