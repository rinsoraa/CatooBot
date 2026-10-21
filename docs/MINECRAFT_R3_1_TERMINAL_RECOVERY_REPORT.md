# R3.1 终态验证竞态与恢复证据报告

日期：2026-10-21。依据开工归档的 R3.1 任务书。**本子项 PASS；R3 仍为
IN_PROGRESS，G1/G3/G4 与 R8 未完成。** 不据本报告开始 R4。

## 1. 真实发现与修复

最终库存 SAFE 读取等待期间，取消或过期已落盘后，旧 `_finish` 仍会按库存结果
覆盖为 SUCCEEDED 或 FAILED。新增真实 SQLite、asyncio.Event 门闩的四种组合
在修改产品代码前全部失败：正向库存将 CANCELLED/EXPIRED 改为 SUCCEEDED，
负向库存改为 FAILED。原始反例见本机 `g2-red-normal.log`。

修复 `app/tasks/runtime.py`：

- 最终观察在锁外完成，保证 STOP/CANCEL 不等待观察；结算前在既有按任务锁内
  重读当前状态、计划版本、散列和未完成步骤，状态/计划变化即不结算旧验证结果。
- 终态吸收从“终态不能回非终态”加强为“先落盘终态不能被替换成另一终态”；
  保留当前计划历史、验证和任务结果，同时保留调用方步骤证据。
- 被吸收的旧终态写入不再生成错误的终态 checkpoint；终态事件要求载荷状态一致。

未扩大资源集、风险或任务预算，未新增执行器、表或迁移。

## 2. 验收映射

| 门禁 | 当前证据 |
|---|---|
| T1 最终验证竞争 | SQLite 上 CANCELLED/EXPIRED × 库存通过/不通过四组合；无虚假成功/失败事件、checkpoint 或 COMPLETED 计划，无额外世界动作 |
| T2 重启恢复 | 关闭并重开 SQLite、新 TaskRuntime 扫描恢复、真实用户重新确认；合法图成功，坏掉落图失败且无重发；旧成功缓存不放行，技能/记忆拒绝伪成功，合法图仍写 TASK 记忆 |
| T3 乱序/竞争 | dig 证据写入前到达旧 pickup，不贡献成功；即使未来 action id 恰好相同，错 parent break 仍失败且迟到合法事件不复活；pickup 结算持锁时取消/过期排队，最终保持对应终态；旧终态快照不能改写结果 |
| T4 工程回归 | 专项最终 14 passed；相关任务/生命周期/采集/技能/Minecraft记忆回归 590 passed（该轮载入时专项为11例，后新增3例由最终专项覆盖）；改动两文件 ruff及format PASS；mypy app 351 files PASS |

本项真实 TaskRuntime/SQLite 使用受控外部动作通道；**没有新真服采集或 QQ 场景**。
整阶段仍须在最终源码上执行 R3 G4 全量工程及其余真实门禁。

## 3. 首次失败与环境问题

1. 默认临时目录权限导致四项 setup ERROR，未进入产品代码，保留 `g2-first.log`。
2. 初次工作区临时目录父目录缺失，保留 `g2-second.log`；补建父目录。
3. 沙箱 Python asyncio 建立 socketpair 卡在 accept，faulthandler 栈保存在
   `g2-red-diagnostic.log`；停止本轮卡住的测试进程后，以正常本机权限运行。
4. 产品反例首轮四项失败，修复后四项及相关既有用例 94 passed。
5. 扩展测试初版遗漏 resume 的 user/session 参数，两项失败；补齐真实归属。
   下一轮重复处理同一任务得到 duplicate，改用新技能服务单独核对坏图资格，
   没有放宽产品断言。各轮日志均保留。
6. Ruff 发现新测试一行过长，使用 formatter 修正；最终检查通过。

## 4. 归档与下一步

改前1382文件快照仍保存于 `data/r3/closure-20261020/`。本项新日志位于
`data/r3/closure-20261021/logs/`，可发布脱敏证据见
`docs/evidence/MINECRAFT_R3_1_TERMINAL_RECOVERY.json`。
未同步、提交、推送；未改日常配置、数据库或测试世界。

下一项是 R3 G1 正常 Bot 装配验证，随后完成逐 ID 真服矩阵、指定 QQ、发布及 CI。
