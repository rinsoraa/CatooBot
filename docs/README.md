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
| 6 | `app/agent/` | v0.7 | 45 | 已完成 |
| 7 | `app/behavior/` | v0.8 + v1.0 | 31 | 已完成 |
| 8 | `app/character/`、`app/response/`、`app/core/`、`app/config/`、`plugins/` | 混版（v0.3/v0.5/v0.6/v0.7/v0.8/v0.9/v1.1/v1.2/v2.0） | ~64 | 已完成（逐条判定） |
| 9 | `app/web/` | 按域（conversation v1.2 / sandbox v2.0 / social v0.9 / identity v0.3 / tools v0.6 / agent v0.7 / behaviour v0.8 / memory v0.5） | ~26 | 已完成 |
| 10 | `tests/` | 按被测模块 | 66 | 已完成 |

`app/sandbox/` 的裸 `§N` 是正确的（它就是 v2.0），不需要前缀。三处无法定位的：
v0.4 行为引擎（回复延迟/分段/作息，规格不在 docs/specs/）标 `v0.4 规格，原文缺失`；
`test_plugin_capabilities` 的「Task 11」与 `test_reply_feedback` 的「§4 of the design」分别指向
任务清单与 V3 设计文档，非版本规格，保持原样。混版文件已在 docstring 注明引用来源。

## 配置段 → 热生效 / 需重启

热加载覆盖审计的结论（「构造时捕获配置、`_apply` 从不下发」是一类 bug，已逐段核查）：
**热生效**的都配了与时刻无关的测试（`test_webui_config.py` / `test_chat_behavior_settings.py`）。

| 配置段 | 生效方式 | 备注 |
|---|---|---|
| `logging` | 热生效 ✓ | level / log_dir / color / narrate / narrate_thinking / narrate_world_ticks |
| `permissions` | 热生效 ✓ | superusers / admins |
| `ai` | 热生效 ✓ | providers / models / cooldown / concurrency / **context（已补推）** |
| `memory` | 热生效 ✓ | retrieval / extraction / **consolidation + schedule（已补推）** / policy / retention |
| `behavior` | 热生效 ✓ | reply / chunking / group / initiative / **schedule 作息（已补推）** |
| `character` | 热生效 ✓ | 时区（重建 presence）；人设走 `/character` 页单独热加载 |
| `social` | 热生效 ✓ | 全段（policy / continuation / observer / monitor） |
| `tools`（单工具） | 热生效 ✓ | provider / timeout / cache / api key |
| `onebot` | 需重启 | host / port / path / access_token（WebUI 保存后已标注） |
| `web` | host/port 需重启 | 账号密码即时（AuthService）；地址端口已标注 |
| `database.url` | 需重启 | WebUI 已标注 |
| `memory.semantic.embedding` | 需重启 | 向量服务启动时构建，暂不热切换 |
| `ai.usage.retention_days` | 需重启 | 注册在调度器闭包里 |
| `media` / `agent` / `sandbox` / `conversation` / `continuity` / `bot` | 需重启 | 无 WebUI 表单，config.yaml 编辑后重启生效 |

**本轮修复的三个同类 bug**（都配了回归测试）：
1. `behavior.schedule`（作息睡眠/DND/夜间）：`BehaviorService._apply` 原来不推给 `PresenceResolver`。
2. `ai.context`（上下文窗口）：`AIEngine.reconfigure` 原来不推给 `ConversationManager`。
3. `memory.consolidation`（巩固节奏/阈值）：`_apply_memory` 原来不推给 `MemoryConsolidator` 与
   `ConsolidationScheduler`。

## 设计文档

| 文档 | 内容 | 状态 |
|---|---|---|
| [V1.2_DESIGN.md](V1.2_DESIGN.md) | v1.2 会话运行期与角色延续的设计、对旧代码的审计 | 已实现 |
| [V2.0_SANDBOX_PLAN.md](V2.0_SANDBOX_PLAN.md) | v2.0 迁移方案：旧架构审计、Bible→Seed 映射、旧数据清理范围、模块处置 | 已实现 |
| [V3_REPLY_FEEDBACK.md](V3_REPLY_FEEDBACK.md) | 回复效果回流：观察 → 结算 → 参与度 EMA 的设计与约束 | 已实现（任务 20） |
| [V3_EXPRESSION_LEARNING.md](V3_EXPRESSION_LEARNING.md) | Task 22 口癖学习：学用词/句式、按群限额注入、可停用可删除可追溯（附录 A 原文置顶） | 设计待确认 |
| [V3_IMAGE_MEMORY.md](V3_IMAGE_MEMORY.md) | 图片记忆：哪些图值得记、记成什么、里程碑 ③ 的触发条件与硬约束 | ①② 已实现 |
| [V3_DATA_EXPORT.md](V3_DATA_EXPORT.md) | 角色数据导出/导入：范围、格式、导入安全流程、验收 | 里程碑 1 已实现 |
| [WEBUI_V1_AUDIT.md](WEBUI_V1_AUDIT.md) | WebUI v1.0 W1 现状审计：页面/路由/服务/实时/安全与迁移阻碍 | W1 已完成 |
| [WEBUI_CONFIG_MATRIX.md](WEBUI_CONFIG_MATRIX.md) | WebUI v1.0 W1 配置矩阵：来源、优先级、逐键状态与迁移级别 | W1 已完成 |
| [WEBUI_API_CONTRACT.md](WEBUI_API_CONTRACT.md) | WebUI v1.0 API 契约：会话/配置/凭据/AI/领域/WS 与验收清单 | W1 已完成（含 §7.5 Minecraft） |
| [MINECRAFT_PHASE1.md](MINECRAFT_PHASE1.md) | Minecraft 连接层（Phase 1）：架构、Bridge API/事件、登录配置、验收记录 | 已实现 |
| [MINECRAFT_PHASE2.md](MINECRAFT_PHASE2.md) | Minecraft 世界感知（Phase 2）：Raw Snapshot/语义模型/分层缓存/感知事件/World Debug | 已实现 |
| [MINECRAFT_PHASE3B.md](MINECRAFT_PHASE3B.md) | Minecraft Safe Action Layer（Phase 3B）：Action Runtime/状态机/安全停止/look_at · stop | 已实现 |
| [MINECRAFT_PHASE3C.md](MINECRAFT_PHASE3C.md) | Minecraft Navigation Runtime（Phase 3C）：move_to 非破坏性导航/Pathfinder/STOP 真停/超时 | 已实现 |
| [MINECRAFT_PHASE3D.md](MINECRAFT_PHASE3D.md) | Minecraft 动态跟随（Phase 3D）：follow_player/GoalFollow dynamic/目标丢失宽限/最大追逐距离 | 已实现 |
| [MINECRAFT_PHASE3E.md](MINECRAFT_PHASE3E.md) | Minecraft LLM 工具与 Agent Bridge（Phase 3E）：六个 Tool/风险分级/Policy 门/上下文/事件回流/move_to 持续型化 | 已实现 |
| [MINECRAFT_PHASE4A.md](MINECRAFT_PHASE4A.md) | Minecraft 确认门与游戏内聊天桥（Phase 4A）：MEDIUM/HIGH 的 Confirmation Gate、可信玩家、Minecraft Chat → USER 回合、防循环 | 已实现 |
| [MINECRAFT_PHASE4B.md](MINECRAFT_PHASE4B.md) | Minecraft 单方块挖掘（Phase 4B）：第一个世界修改动作 minecraft_dig（MEDIUM + 确认 + expected_block + block_changed）、感知联动、真实服务器 smoke | 已实现（真实服务器 smoke 待操作者开服验证） |
| bible_source_罐头.txt | 内置角色的人物档案源文件（Bible 编译器输入） | **本地内容资产**：按「Character Bible 不得上传」约束，已移至 `config/bible_source_罐头.txt`（`config/` 不在发布镜像内） |

## 运维速查

```bash
python scripts/backup_db.py                 # 在线快照（VACUUM INTO + 校验）→ ../CatooBot_backups/
python scripts/migrate_db.py --db <file>    # 对单个库文件预演/应用迁移（先副本、后真库）
python -m app.sandbox.transfer export       # 导出角色数据（→ data/exports/character-<ts>.json）
python -m app.sandbox.transfer inspect <f>  # 离线校验导出包：格式、内容哈希、逐表计数
```

角色数据导出/导入的设计与安全流程见 [V3_DATA_EXPORT.md](V3_DATA_EXPORT.md)。
