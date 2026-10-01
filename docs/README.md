# 设计文档索引

本目录是 CatooBot 的设计与运维文档。代码与文档里的 `§` 编号指向下表中的规格文档。

| 文档 | 内容 | 状态 |
|---|---|---|
| [V1.2_DESIGN.md](V1.2_DESIGN.md) | v1.2 会话运行期与角色延续的设计、对旧代码的审计 | 已实现 |
| [V2.0_SANDBOX_PLAN.md](V2.0_SANDBOX_PLAN.md) | v2.0 Character Life Sandbox 的迁移方案：旧架构审计、Bible→Seed 映射、旧数据清理范围、模块处置 | 已实现 |
| [V3_REPLY_FEEDBACK.md](V3_REPLY_FEEDBACK.md) | 回复效果回流：观察 → 结算 → 参与度 EMA 的设计与约束 | 已实现（任务 20） |
| [V3_IMAGE_MEMORY.md](V3_IMAGE_MEMORY.md) | 图片记忆：哪些图值得记、记成什么、里程碑 ③ 的触发条件与硬约束 | ①② 已实现 |
| [V3_DATA_EXPORT.md](V3_DATA_EXPORT.md) | 角色数据导出/导入：范围、格式、导入安全流程、验收 | 里程碑 1 已实现 |
| [bible_source_罐头.txt](bible_source_罐头.txt) | 内置角色的人物档案源文件（Bible 编译器输入） | 内容资产 |

## 关于 v2.0 规格本体（§1–§207）

代码注释与上述文档中的 `§NNN` 引用的是 v2.0 的**角色生活沙盒规格**。
该规格原文没有随仓库分发（它是编写期的独立文档，只在开发环境中使用）。
如果你要公开它，把它作为 `docs/V2.0_SPEC.md` 放进本目录即可——
所有 `§` 引用就会指向这里；在那之前，可读入口是
[V2.0_SANDBOX_PLAN.md](V2.0_SANDBOX_PLAN.md)（迁移方案）与
[bible_source_罐头.txt](bible_source_罐头.txt)（角色 Bible 源）。

## 运维速查

```bash
python scripts/backup_db.py                 # 在线快照（VACUUM INTO + 校验）→ ../CatooBot_backups/
python scripts/migrate_db.py --db <file>    # 对单个库文件预演/应用迁移（先副本、后真库）
python -m app.sandbox.transfer export       # 导出角色数据（→ data/exports/character-<ts>.json）
python -m app.sandbox.transfer inspect <f>  # 离线校验导出包：格式、内容哈希、逐表计数
```

角色数据导出/导入的设计与安全流程见 [V3_DATA_EXPORT.md](V3_DATA_EXPORT.md)。
