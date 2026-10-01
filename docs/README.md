# 设计文档索引

本目录是 CatooBot 的设计与运维文档。代码与文档里的 `§` 编号指向下表中的规格文档。

| 文档 | 内容 | 状态 |
|---|---|---|
| [V2.0_SPEC.md](V2.0_SPEC.md) | **v2.0 角色生活沙盒规格本体（§1–§207）**——代码与文档里所有 `§NNN` 引用都指向这里 | 参考 |
| [V1.2_DESIGN.md](V1.2_DESIGN.md) | v1.2 会话运行期与角色延续的设计、对旧代码的审计 | 已实现 |
| [V2.0_SANDBOX_PLAN.md](V2.0_SANDBOX_PLAN.md) | v2.0 Character Life Sandbox 的迁移方案：旧架构审计、Bible→Seed 映射、旧数据清理范围、模块处置 | 已实现 |
| [V3_REPLY_FEEDBACK.md](V3_REPLY_FEEDBACK.md) | 回复效果回流：观察 → 结算 → 参与度 EMA 的设计与约束 | 已实现（任务 20） |
| [V3_IMAGE_MEMORY.md](V3_IMAGE_MEMORY.md) | 图片记忆：哪些图值得记、记成什么、里程碑 ③ 的触发条件与硬约束 | ①② 已实现 |
| [V3_DATA_EXPORT.md](V3_DATA_EXPORT.md) | 角色数据导出/导入：范围、格式、导入安全流程、验收 | 里程碑 1 已实现 |
| [bible_source_罐头.txt](bible_source_罐头.txt) | 内置角色的人物档案源文件（Bible 编译器输入） | 内容资产 |

## 关于 `§NNN` 引用（v2.0 规格）

代码注释与上述文档里的 `§NNN` 指向 **[V2.0_SPEC.md](V2.0_SPEC.md)**（§1–§207，
锚点格式 `# §N 中文数字、标题`，**不要改动标题文字**，全仓 grep 依赖它）。

- 全仓引用已核对：仓库内 **0 处** 解不掉的 `§N`（`§1–§207` 之外或带点号的写法）。
- 少数引用出自 **v1.x 规格**（媒体类型边界、工具运行时等），其原文不在本仓库，
  已就地标注为 `v1.x 规格，原文缺失`（共 8 处：媒体/贴纸 6 处、内置工具 2 处）。
- 有两处旧引用在 v2.0 里找到了语义对应并已改指：
  模式叠加 `§2.5 → §79 模式可以叠加`；离线恢复 `§209 → §73 Recovery / §72`。

## 运维速查

```bash
python scripts/backup_db.py                 # 在线快照（VACUUM INTO + 校验）→ ../CatooBot_backups/
python scripts/migrate_db.py --db <file>    # 对单个库文件预演/应用迁移（先副本、后真库）
python -m app.sandbox.transfer export       # 导出角色数据（→ data/exports/character-<ts>.json）
python -m app.sandbox.transfer inspect <f>  # 离线校验导出包：格式、内容哈希、逐表计数
```

角色数据导出/导入的设计与安全流程见 [V3_DATA_EXPORT.md](V3_DATA_EXPORT.md)。
