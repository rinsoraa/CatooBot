# R3.3 剩余负向矩阵补充报告

日期：2026-10-21。状态：**R3 仍为 IN_PROGRESS**；本报告补齐 G3 中工具/耐久、事实缺失、危险/路径、库存不净增、旧掉落/外部干预类别，不宣告 R3 COMPLETE。

## 1. 测试范围

- 正常 `Bot.start` 装配；隔离临时 SQLite；公开测试 Bible；真实测试服；未发送 QQ 消息。
- 源码冻结 667 文件，漂移 0；候选 JAR SHA-256 `59b5c9393b4f2f24472fa3c73d732646b317e95fcdaa5c544d493f67045e3a2c`。
- 本轮新备份 108 文件，`server-before.zip` SHA-256 `44fde6a299133a56ebf4e4a9ad7369bbcec4df40a28f1a88737c9b16454a07e3`。
- 驱动 `remaining_matrix.py`，最终 SHA-256 见 `preparation.json`。

## 2. 逐项结果

| 用例 | 类别 | 结果 |
|---|---|---|
| `tool_durability-stone` | 工具耐久耗尽 | PASS：计划前拒绝 |
| `tool_durability-cobblestone` | 工具耐久耗尽 | PASS：计划前拒绝 |
| `tool_durability-coal_ore` | 工具耐久耗尽 | PASS：计划前拒绝 |
| `tool_durability-iron_ore` | 工具耐久耗尽 | PASS：计划前拒绝 |
| `facts_missing-no_target` | 必要事实缺失 | PASS：覆盖完整但无目标，计划拒绝 |
| `danger_support-stone` | 支撑缺失 | PASS：`resource.target_floor_unsafe`，计划拒绝 |
| `danger_liquid-stone` | 相邻液体 | PASS：安全前提拒绝（实测 `resource.target_floor_unsafe`；本轮未把液体邻近单独归因） |
| `path_failure-stone` | 路径失败 | PASS：任务 PAUSED，目标方块未变，无额外破坏/放置 |
| `inventory_full-stone` | 库存不净增 | PASS：背包满，任务 PAUSED，cobblestone 未增加 |
| `old_drop-sand` | 旧同类掉落 + 缺失掉落证据 | PASS：任务 PAUSED，旧沙掉落未被计入成功 |
| `external_removal-sand` | 外部移除拾取物 | PASS：dig 成功后外部清除掉落，任务 REPLANNING，未成功 |

逐项夹具、观察、计划回复、最终状态与库存前后在 `remaining-results.json` 中。

## 3. 首次失败与修复范围

前五次尝试均为验收夹具问题，全部保留在 `attempt-01`–`attempt-05`：

1. danger 支撑用例没有先以 `dig_capability` 预热目标，`find_blocks` 缓存未看到新方块；
2. danger 支撑用例未持镐，先被工具采收拒绝；
3. path 用例未持镐，未进入路径前提；
4. inventory 用例只填了 `inventory.1-26`，hotbar 仍有空位，任务正确 SUCCEEDED；
5. external removal 用例延续了上一例 `drop_missing` 的 relay 模式，dig 证据被丢弃。

最终 attempt-06 通过；产品代码未修改。

## 4. 世界恢复

- 使用本轮 `server-before.zip` 恢复 108 个文件，逐文件哈希一致。
- 移除本轮新增世界文件 12 个。
- 测试服已重新启动，PID 62664；恢复回执见 `restore-receipt.json`。

## 5. 仍未完成

- G3 的“事实过期”目前由确定性合同测试覆盖（`tests/test_resource_collection_contract.py` 的 `test_stale_resource_observation_does_not_freeze_an_executable_plan`），本轮真服只补了“目标缺失”；若要求真服过期注入，需另建时间控制夹具。
- G4：真实 QQ 文案复测、最终冻结源码全量 pytest/Node/Java/静态检查、发布白名单同步与 CI。
- 正式 R3 报告与 G1–G4 映射。

因此 R3 继续保持 IN_PROGRESS，不进入 R4。
