# 设计文档索引

本目录是 CatooBot 的设计与运维文档。代码注释里的 `§N` 引用有**明确的版本归属**，
规则见下面两节。

## 规格文档（`docs/specs/`）

十份开发规格按版本入库；每份都是当版实现与评审的依据。

| 版本 | 文件 | 主题 | 锚点形式 |
|---|---|---|---|
| v0.3 | [specs/v0.3.md](specs/v0.3.md) | 拟人化角色系统 + 长期记忆 + WebUI 管理后台 | `# v0.3 §N 一、…` |
| v0.5 | [specs/v0.5.md](specs/v0.5.md) | Semantic Memory Engine + 混合检索 + 记忆巩固 | `# v0.5 §N 一、…` |
| v0.6 | [specs/v0.6.md](specs/v0.6.md) | Tool Runtime + Contextual Tool Use | `# v0.6 §N 一、…` |
| v0.7 | [specs/v0.7.md](specs/v0.7.md) | Agent Runtime + Planning + Task Orchestration | `# v0.7 §N 一、…` |
| v0.8 | [specs/v0.8.md](specs/v0.8.md) | Persistent World + Background Life Runtime | `# v0.8 §N 一、…` |
| v0.9 | [specs/v0.9.md](specs/v0.9.md) | Social Cognition Engine + 群聊自主参与 | `# v0.9 §N 一、…` |
| v1.0 | [specs/v1.0.md](specs/v1.0.md) | World Activity Runtime + Episode-Based Activity Model | `# v1.0 §N 一、…` |
| v1.1 | [specs/v1.1.md](specs/v1.1.md) | 多模态消息 + 自主表情包系统（唯一用阿拉伯编号） | `## v1.1 §2.1 …` |
| v1.2 | [specs/v1.2.md](specs/v1.2.md) | Human Conversation Runtime + Character Continuity | `# v1.2 §N 一、…` |
| v2.0 | [specs/v2.0.md](specs/v2.0.md) | Character Life Sandbox（当前架构） | `# §N 一、…`（裸编号） |

## `§N` 引用约定

1. **裸 `§N` 只表示 v2.0**（[specs/v2.0.md](specs/v2.0.md)）。沙盒、角色档案、
   重置范围、恢复、模式等当前架构的引用都用裸编号。
2. **旧模块一律带版本前缀**：`v0.5 §37`、`v1.1 §2.2`、`v0.9 §62-§66`。
   一个分组共享一个前缀——`v0.9 §62-§66/§101` 表示全部来自 v0.9。
3. **锚点文字不要改**：规格标题是 grep 的落点（`# v0.5 §37 三十七、Memory Compression`、
   v2.0 为 `# §1 一、版本目标`）。要改标题就得同时改全仓引用。
4. 子编号（`v0.6 §24.1`、`v1.1 §5.1`）指向该节内部的阿拉伯子标题，不单独设锚点。

## 模块 → 规格版本

写注释时按下表给引用加前缀（这就是各模块的设计依据版本）：

| 模块 | 规格版本 |
|---|---|
| `app/character/`、`app/response/`、`app/web/`、`app/config/`、`app/core/`、`app/message/`、`app/permissions/`、`app/commands/` | v0.3 |
| `app/memory/` | v0.5 |
| `app/tools/`（含 `builtins`） | v0.6 |
| `app/agent/` | v0.7 |
| `app/behavior/` | v0.8 + v1.0（世界与活动两代） |
| `app/social/` | v0.9 |
| `app/media/` | v1.1 |
| `app/conversation/`、`app/continuity/` | v1.2 |
| `app/sandbox/` | v2.0（裸 §N） |
| `plugins/`、`tests/` | 引用其调用方的版本 |

**前缀迁移进度**（逐批提交，每批独立验证）：

| 批次 | 模块 | 版本 | 引用数 | 状态 |
|---|---|---|---|---|
| 1 | `app/memory/` | v0.5 | 50 | 已完成 |
| 2 | `app/social/` | v0.9 | 38 | 已完成 |
| 3 | `app/conversation/`、`app/continuity/` | v1.2 | 57 | 已完成 |
| 4 | `app/media/` | v1.1 | 17 | 已完成 |
| 5 | `app/tools/` | v0.6 | 38 | 已完成 |
| 6 | `app/agent/` | v0.7 | ~45 | 待做 |
| 7 | `app/behavior/` | v0.8 + v1.0 | ~31 | 待做 |
| 8 | `app/character/`、`app/response/`、`app/core/`、`app/config/`、`plugins/` | v0.3 | ~90 | 待做 |
| 9 | `app/web/` | v0.3（页面里的沙盒/社交段是 v2.0/v0.9，需逐条看） | ~26 | 待做 |

`app/sandbox/` 的裸 `§N` 是正确的（它就是 v2.0），不需要前缀。
未加前缀的旧模块引用仍按本表解读。批次 8、9 里混版的模块（`app/web/`、`plugins/`、
`app/character/` 的沙盒/社交钩子）必须逐条对照小节标题判断，不能按模块表一把梭。

## 设计文档

| 文档 | 内容 | 状态 |
|---|---|---|
| [V1.2_DESIGN.md](V1.2_DESIGN.md) | v1.2 会话运行期与角色延续的设计、对旧代码的审计 | 已实现 |
| [V2.0_SANDBOX_PLAN.md](V2.0_SANDBOX_PLAN.md) | v2.0 迁移方案：旧架构审计、Bible→Seed 映射、旧数据清理范围、模块处置 | 已实现 |
| [V3_REPLY_FEEDBACK.md](V3_REPLY_FEEDBACK.md) | 回复效果回流：观察 → 结算 → 参与度 EMA 的设计与约束 | 已实现（任务 20） |
| [V3_IMAGE_MEMORY.md](V3_IMAGE_MEMORY.md) | 图片记忆：哪些图值得记、记成什么、里程碑 ③ 的触发条件与硬约束 | ①② 已实现 |
| [V3_DATA_EXPORT.md](V3_DATA_EXPORT.md) | 角色数据导出/导入：范围、格式、导入安全流程、验收 | 里程碑 1 已实现 |
| [bible_source_罐头.txt](bible_source_罐头.txt) | 内置角色的人物档案源文件（Bible 编译器输入） | 内容资产 |

## 运维速查

```bash
python scripts/backup_db.py                 # 在线快照（VACUUM INTO + 校验）→ ../CatooBot_backups/
python scripts/migrate_db.py --db <file>    # 对单个库文件预演/应用迁移（先副本、后真库）
python -m app.sandbox.transfer export       # 导出角色数据（→ data/exports/character-<ts>.json）
python -m app.sandbox.transfer inspect <f>  # 离线校验导出包：格式、内容哈希、逐表计数
```

角色数据导出/导入的设计与安全流程见 [V3_DATA_EXPORT.md](V3_DATA_EXPORT.md)。
