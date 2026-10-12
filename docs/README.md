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
| [MINECRAFT_PHASE4B.md](MINECRAFT_PHASE4B.md) | Minecraft 单方块挖掘（Phase 4B）：第一个世界修改动作 minecraft_dig（MEDIUM + 确认 + expected_block + block_changed）、感知联动、真实服务器 smoke | 已实现（真实服务器 smoke PASS） |
| [MINECRAFT_PHASE4C.md](MINECRAFT_PHASE4C.md) | Minecraft 背包只读切片 + 单方块放置（Phase 4C）：minecraft_inventory（SAFE）/ minecraft_place（MEDIUM + face 六向 + expected_item 手主持物硬约束 + 二次验证） | 已实现（真实服务器 place PASS） |
| [MINECRAFT_PHASE4D.md](MINECRAFT_PHASE4D.md) | Minecraft 背包控制（Phase 4D）：minecraft_equip（换主手）/ minecraft_inventory_move（单物品单槽位搬运，目标被占用即拒绝，绝不隐式交换）+ 调试槽位表 | 已实现（真实服务器 equip + move PASS） |
| [MINECRAFT_PHASE4E.md](MINECRAFT_PHASE4E.md) | Minecraft 容器控制（Phase 4E）：minecraft_container_inspect（单方块 Chest/Barrel 只读，SAFE 但独占）/ minecraft_container_transfer（单物品单槽位存取，MEDIUM + 确认，绝不交换）+ 窗口生命周期与 cleanup | 已实现（真实服务器 inspect + withdraw + deposit PASS） |
| [MINECRAFT_PHASE4F.md](MINECRAFT_PHASE4F.md) | Minecraft 合成（Phase 4F）：minecraft_recipe_lookup（2×2 配方只读查询，SAFE 非独占）/ minecraft_craft（一次一个配方，MEDIUM + 确认，稳定可读 recipe_id，材料不够直接失败） | 已实现（真实服务器 craft PASS） |
| [MINECRAFT_PHASE4G.md](MINECRAFT_PHASE4G.md) | Minecraft 工作台 3×3（Phase 4G）：同一对工具增加**可选** crafting_table 坐标（2×2 保持兼容）、实时验证工作台、确认指纹含坐标、不自动寻找/放置工作台 | 已实现（真实服务器 3×3 craft PASS） |
| [MINECRAFT_PHASE4H.md](MINECRAFT_PHASE4H.md) | Minecraft 掉落物感知 + 单实体拾取（Phase 4H）：minecraft_dropped_items（Item 实体语义投影，SAFE 非独占）/ minecraft_pickup_item（一次一个明确实体，MEDIUM + 确认 + 真 STOP + 成功要求「实体被收走且背包增加」） | 已实现（真实服务器把 Item Entity 捡进背包 PASS） |
| [MINECRAFT_PHASE4H1.md](MINECRAFT_PHASE4H1.md) | Minecraft 导航可靠性加固（Phase 4H.1）：move_to 不再相信 `pathfinder.goto()` 的 resolve（2.4.5 空路径静默成功），自己挂生命周期 + 用实际位置硬校验到达；新增 `path.not_reached`；真机 false-success guard | 已实现（真实服务器假成功守卫 PASS） |
| [MINECRAFT_PHASE4I.md](MINECRAFT_PHASE4I.md) | Minecraft 工具感知（Phase 4I）：`minecraft_dig` 增加**可选** `expected_tool`（只校验执行瞬间的主手，**绝不自动装备**；指纹含工具；结果带 `tool_expected/tool_actual`） | 已实现（真实服务器工具感知 dig PASS） |
| [MINECRAFT_PHASE4J.md](MINECRAFT_PHASE4J.md) | Minecraft 挖掘能力只读模型（Phase 4J）：`minecraft_dig_capability`（SAFE 只读，非独占；用运行时的 `canDigBlock`/`digTime` 回答「现在能不能挖、多久」；两种距离口径；不推荐工具） | 已实现（真实服务器三种状态 PASS） |
| [MINECRAFT_PHASE4K.md](MINECRAFT_PHASE4K.md) | Minecraft 资源定位 + 单资源真实闭环（Phase 4K）：`minecraft_find_blocks`（SAFE 只读，非独占；`findBlocks` 按 block id 搜索、双距离口径、不推荐）；并用原子能力真机跑通 find → capability → equip → move → dig → dropped_items → pickup → inventory | 已实现（真实服务器天然橡木闭环 PASS） |
| [MINECRAFT_PHASE5A.md](MINECRAFT_PHASE5A.md) | Minecraft 多步骤任务运行时（Phase 5A）：TaskState/StepState 状态机、两阶段计划（SAFE 观察 → 冻结动作计划）、一次确认整份计划、TaskAuthorization/TaskStepAuthorization（TASK ≠ USER）、checkpoint 持久化、pause/resume/cancel/expire、失败分类与 no-progress、最终背包复验、任务 API 与面板 | 已实现（真实服务器任务闭环 + pause/resume + cancel PASS） |
| [MINECRAFT_PHASE5A1.md](MINECRAFT_PHASE5A1.md) | Minecraft 任务韧性（Phase 5A.1）：真实重规划（旧计划 superseded → SAFE 观察 → 新 plan_hash → 新确认）、授权到期（安全边界 → PENDING_CONFIRMATION → 重新确认）、进程重启恢复（SQLite 读回 → RUNTIME_RESTART → 只读对账 RECONCILED/WORLD_CHANGED/TARGET_LOST/TARGET_ALREADY_DONE/OFFLINE/UNKNOWN）、Plan 版本历史与审计 | 已实现（真实服务器重规划 + 授权到期 + 重启恢复 PASS） |
| [MINECRAFT_PHASE5B.md](MINECRAFT_PHASE5B.md) | Minecraft QQ 任务入口与统一任务控制（Phase 5B）：QQ 只是入口（身份 = 稳定 QQ 号 + 既有会话身份）、意图门（普通聊天不建任务）、确认/暂停/继续/停止全走现有 TaskRuntime、归属隔离（非发起人一律被拒且不改状态）、任务事件→QQ 短消息（event_seq 幂等去重）、启动恢复 | 已实现（真机 QQ 门禁见文档 §九） |
| [MINECRAFT_PHASE5C.md](MINECRAFT_PHASE5C.md) | Minecraft 身份桥 + 持久世界记忆（Phase 5C）：确定性 `server_id` 与 canonical `player_uuid`、QQ↔Minecraft 两步显式绑定与冲突拒绝、独立 Minecraft 记忆域（7 种 kind / 来源上限 / Freshness）、世界对账（绝不成反向写）、≤5 条检索适配器（检索时世界复核）、记忆安全边界（注入降级、记忆不授权限） | 已实现（真机身份 + 冲突拒绝 + 任务记忆 + 世界对账 + 跨进程持久化 PASS） |
| [MINECRAFT_PHASE6A.md](MINECRAFT_PHASE6A.md) | 世界活动运行时（Phase 6A）：ActivityEpisode 成为"当前活动"的唯一事实来源（7 状态显式状态机、min/typical/max 时长、稳定 Episode ID）、ActivityRuntime（推进/延长/收尾/中断/取消/超时/恢复，CAS 幂等事件）、确定性 Planner、CharacterState 投影（activity 只由 Episode 派生）、Task↔Activity 绑定、Minecraft 只读观察（观察不是命令）、世界 tick 只推进不决策 | 见文档 §九（真机门禁 A–E） |
| [MINECRAFT_PHASE6B.md](MINECRAFT_PHASE6B.md) | 活动决策引擎（Phase 6B）：Transition Guard（最短/最长时长、延长预算、撞车）+ 规则优先的 Decision Engine（CONTINUE / EXTEND / TRANSITION + 原因码 + 下一个活动提示 + trace）、Transition Window（窗口内只待命）、相邻同活动优先 EXTEND + 展示层合并、WorldConsistencyChecker（只报不修）、决策 trace（不含思维链）、只读决策 API 与面板 | 见文档 §15（真机 A–D） |
| [MINECRAFT_PHASE6C.md](MINECRAFT_PHASE6C.md) | 活动规划（Phase 6C）：ActivityPlanner → **Rolling Horizon**（`ActivityPlan`：3~6 候选、被拒原因、八项确定性打分、固定 tie-break）、日程锚点（睡觉/三餐 + 弹性窗口 + 五档优先级）、活动画像与 fixed/flexible/free、持久目标（复用既有 `sandbox_goals`，只影响排名）、能量硬规则 / 专注软信号、计划持久化（迁移 30；版本只在内容变化时 +1；旧计划永不删除）、重启"先认现实再认计划"、只读计划 API 与面板、QQ「接下来准备干嘛」与「现在在干嘛」分块 | 见文档 §16-§17（Real Java A–D + Real QQ A/B） |
| [MINECRAFT_PHASE6C.md §20](MINECRAFT_PHASE6C.md#20-phase-6c1--schedule-reconciliationepisode-延长后的计划对齐) | Episode 延长后的计划对齐（Phase 6C.1）：6B `EXTEND` 之后计划即时对齐 —— Strategy A（只改 continuation 边界）/ 冲突则受控 replan；`EPISODE_EXTENDED` **软**触发复用既有冷却（冷却内只标 `dirty`，派生不落盘）；不二次决策、不碰当前 Episode、不提前开下一个活动；`trigger=episode_extended` 审计 + 版本 +1；无新表（迁移仍 30）；只读面新增 `dirty` 三态 | 见文档 §20.8（Real A–D 窄门禁） |
| [MINECRAFT_PHASE6D.md](MINECRAFT_PHASE6D.md) | 模型辅助活动决策（Phase 6D）：`ActivityModelAdvisor`（**参谋**，不是权威）—— 只做 prepare/call/parse/propose；`StructuredModelProvider` 复用既有 `AIEngine`（无第二套客户端）；模型只在"到期做软决策"那一刻被问、到硬上限连问都不问；结构化输入（bounded、**没有**聊天/记忆库/凭据）与严格输出（枚举/注册表/≤160 字符/无思维链）；规则再验证（额度/锚点/撞车/候选）**优先拒绝 + 回退**；调用键 `activity:{episode}:{planned_end}` + 既有审计行做重启守卫（**无新表**）；默认关闭 = 纯规则等价；只读 `/world/activity/advisor` + WebUI 卡片 | 见文档 §14-§15（Real Java A–F + Real QQ A–E） |
| [MINECRAFT_PHASE7A.md](MINECRAFT_PHASE7A.md) | Initiative Gate / Life Intent（Phase 7A）：`LifeIntent`（**只提 / 评 / 记 / 抑 / 过期**，执行层恒为 NONE，`LifeIntent != Episode != Task`）+ 确定性候选生成（Goal / Memory / Social / 承诺 / 低能量 / 长闲置 / 时段日常，**不由这里产生活动连续性**）+ `InitiativeGate`（11 条 hard guard 定序 + 冷却 + 防爆 + 指纹去重 + 一轮最多 1 条）+ 只新增 `life_intents` 一张表（迁移 31，历史复用 `behavior_events`）+ 恢复静默期 + 源码级安全边界（整包只 import 自己与标准库）+ 只读 API/WebUI 卡片；只作为 **ActivityPlanner 的候选建议**，永不强制换活动 | 见文档 §15-§16（Real Java B PASS / A·C·D 未取证：当时她在真实睡眠窗口；Real QQ A/B PASS） |
| [MINECRAFT_PHASE7B.md](MINECRAFT_PHASE7B.md) | Initiative → Plan 闭环（Phase 7B）：`InitiativeActivityHint` + `InitiativeHintBook` 把合格 LifeIntent 变成 Planner 候选里的一个**软**项 `initiative_fit`（权重 1.0 < 锚点/习惯/目标；资格与 6B 护栏永远在前）；名称以实际 Registry 为准（`minecraft` 不注册 → 映射到虚拟活动 `building`；`minecraft_task` 永不参与）；审计归因 `intent_id` 刻意不进内容签名（无意图时与 6C 逐字一致）；只在规划触发点读、最多 3 条、故障退回原行为；无新表无新迁移 | 见文档 §15（Real Java A–D / Real QQ A–D） |
| [MINECRAFT_PHASE7D.md](MINECRAFT_PHASE7D.md) | Agent Harness（Phase 7D + 7D.2 收口）：`AgentPlan` 规划与审计层（**不是**第二套任务状态机，执行状态实时读 Task）+ `PlanningOutcome` 六值 + 受限规划器（复用 5A 资源模板 / 7C 能力目录）+ USER 一次确认、LIFE 两道门（批准只在 QQ）+ 「跟着我」进既有链（身份桥解析 + 确认前复核 + 否决即取消）+ 跨 session 预留租约 + CAS 列/payload 同事务写 + 列级事实恢复（含半写关联修复）+ 只新增 `task_agent_plans` 一张表（迁移 33）+ 只读 API/WebUI 卡片 | 见文档 §8（Real A/B/D/D'/E/F + Real QQ A/B + LIFE 两道门取证）与 §10（**Real C 于 7D.2 重做并取证**；7D.1 的 §9.2 取证已作废） |
| [MINECRAFT_PHASE7E.md](MINECRAFT_PHASE7E.md) | Skill Learning & Procedural Memory（Phase 7E）：从**真实成功任务**里学可复用的方法 —— 学习资格门（只认持久化 TaskRecord 的成功终态 + 最终计划版本 + 独立后置条件；dig 归因歧义判 `AMBIGUOUS` 不计正向）+ 结构化技能（槽位归一化 / 指纹去重 / 版本 lineage / 五态生命周期，`CANDIDATE` 绝不参与复用）+ 有界检索与适用性评估（工具契约 / 服务器 / 风险 / 新鲜 SAFE 前置，`UNKNOWN` 不升级）+ 计划候选接入既有 `validate_plan` / 批准 / 确认 / `TaskRuntime` 链 + 结果回流（成功累计 / 反例 → STALE → INVALIDATED）+ 三张新表（迁移 34）+ **7E.1** 原子证据认领（计数由证据行派生）+ **每任务唯一**的持久归因绑定（迁移 35）+ **7E.1.1** 反馈资格门（未启动/用户取消/外部失败不惩罚技能；反例只认运行时后验失败）与证据列/payload 一致（迁移 36）+ **7E.1.2** 执行证据严格白名单（只有源码可证明的痕迹才算"这一步跑过"，状态本身不算：`FAILED` 有调用前分支、`SKIPPED`/`WAITING_CONFIRMATION` 从不被写）+ 只读 API | 见文档 §1-§2（审计与最小设计）、§4-§6（资格门/归一化/适用性）、§8-§9（验收矩阵与门禁）、§12（7E.1）、§13（7E.1.1）、§14（7E.1.2） |
| [MINECRAFT_PHASE7D_FOLLOWUP.md](MINECRAFT_PHASE7D_FOLLOWUP.md) | Minecraft Dig Attribution（Phase 7D Follow-up）：修掉 7D §10.4 的挖掘假成功（根因＝mineflayer 只看"目标坐标变成 air"就判定挖完）+ **世界效果 / 执行归属两条独立轴**（`world_effect` ∈ BLOCK_REMOVED/BLOCK_REMAINS/UNKNOWN；`attribution` ∈ SELF_CONFIRMED/SELF_INFERRED/EXTERNAL_INDICATED/AMBIGUOUS，刻意没有过度断言的外部确认值）+ Node 端按 action_id/坐标/时间窗的**作用域化证据采集**（含服务器真包 vs 本地乐观更新的区分、零监听器泄漏、迟到事件忽略）+ 归因随既有动作终态落 `TaskStep.result`（**无新表、无新迁移**，最高仍 36）+ 技能资格门 `dig_attribution_unproven:<step_id>`（效果证据≠归属证据）+ 记忆桥**逐挖掘步骤**只写 `SELF_CONFIRMED` 的"她亲手挖过" | 结论 **PARTIAL**：无证据误归因已消除；严格自挖确认仍受客户端协议能力限制（协议里没有「这个方块是我破坏的」回执）；**真实 Java 门禁 PASS**（四个真机用例：自挖 `SELF_CONFIRMED`（ratio 1.000）/ 中途被 `/setblock` 改掉 `AMBIGUOUS`（ratio 0.11）/ 第二个真实客户端抢占 `EXTERNAL_INDICATED`（带对方用户名）/ 非 op 在 `spawn-protection` 内被服务器拒绝 → `BLOCK_REMAINS`），并因此修掉两处只有真机才暴露的缺陷（必须等「服务器亲口说该坐标变了」才判世界效果；服务器回纠正包 = 没有发生移除） **＋ Phase 7D.3（schema 2）**：拆出 `SELF_INFERRED`（时序推断）与 `SELF_CONFIRMED`（直接执行者证据＝自己的破坏进度），`strict_self_proof` 只为 `SELF_CONFIRMED` 置真；Python 消费门禁 fail-closed（schema/类型/`action_id`/严格依据），`SELF_INFERRED` 只隔离不计分；服务端更新按到达顺序裁决、`type != 0` 不再冒充原方块仍在；真实 Java 门禁 PASS（S 自挖 → `SELF_INFERRED` / E → `AMBIGUOUS` / X → `EXTERNAL_INDICATED` / P → `BLOCK_REMAINS`） |
| [MINECRAFT_PHASE7F1.md](MINECRAFT_PHASE7F1.md) | Bounded Autonomous Exploration（Phase 7F.1 有界自主探索）：第一个最小自主游玩闭环 —— 真实观察 → 有界探索提案（SAFE 读选目标，`move_to`+`look_at` 两步）→ 既有 AgentPlan 与能力/前置校验 → 既有批准+确认两道门 → 既有 TaskRuntime → 已注册 SAFE/LOW 工具 → **真实世界坐标**判定到达（`ExpectedFinalState.position_within` + 感知收敛有界重试）→ 经历回流；第一版只读+移动（不挖/放/攻击/合成/容器），`allow_medium=false`、19 工具不变，**无新表无新迁移**（最高仍 36） | 已实现：全量 `3608 passed`；**真实 Java 门禁 PASS**（positive 距目标 1.70 格 SUCCEEDED / blocked 真实 path.not_found 安全 PAUSED / cancel 真停且不再产生动作） |
| [MINECRAFT_PHASE7F2.md](MINECRAFT_PHASE7F2.md) | Exploration Continuity & LifeIntent Feedback（Phase 7F.2 探索连续性与生活意图反馈）：接上 7F.1 的**真实探索结果** —— 结构化探索结果投影（`ARRIVED_VERIFIED`/`ARRIVED_NO_NEW_FACT`/`NEW_FACTS_VERIFIED`/`BLOCKED`/`CANCELLED_OR_INTERRUPTED`，**只有** `SUCCEEDED` 且 `verification.{checked,ok,position_within}` 三者齐备才算到达）→ 真记忆桥回流（到达 `EVENT` 经历 + 有证据的新 `LOCATION` 事实，`actionable` 标记）→ 既有 `InitiativeGate`/候选生成（actionable 探索事实 → 探索形状 `MINECRAFT_INTEREST`；普通到达不反复生成意图）→ LifeIntent → TaskProposal → AgentPlan → 两道门；**fail-closed**：反馈链绝不自行获得执行权限、批准前不建 Task、批准后只停在第二道确认门；`allow_medium=false`、19 工具不变，**无新表无新迁移**（最高仍 36） | 已实现：全量 `3638 passed`；**真实 Java 门禁 PASS**（真机探索 `SUCCEEDED`+`verification.ok=True` → 3 条记忆 → `NEW_FACTS_VERIFIED` → 1 条 `exploration_feedback` 意图 → 真提案/计划 → 批准后 `PENDING_CONFIRMATION` 且零动作） |
| [MINECRAFT_PHASE7F2.1.md](MINECRAFT_PHASE7F2.1.md) | Terminal-State Invariant Hardening（Phase 7F.2.1 探索投影终态不变量加固）：把探索到达判定统一到唯一的严格函数 `verified_arrival` —— 只有 `SUCCEEDED` + `verification.{checked,ok}` 严格为真 + `position_within` 含**实际到达坐标** x/y/z 才算到达；`FAILED` / `CANCELLED` / `EXPIRED` / 状态与证据矛盾一律 fail-closed，绝不写正向 `EVENT` 到达经历或 actionable `LOCATION` 新事实。`_arrived` / `exploration_outcome` / `arrival_fact` 全部收敛到该判定，结果分类语义不变；`allow_medium=false`、19 工具不变，**无新表无新迁移**（最高仍 36）。 | 已实现：全量 `3644 passed`；**真实 Java 门禁 PASS**（feedback 反馈链 + positive/blocked/cancel） |
| [MINECRAFT_PHASE7F2.2.md](MINECRAFT_PHASE7F2.2.md) | Coordinate Domain Hardening（Phase 7F.2.2 探索坐标域加固）：在 7F.2.1 统一终态到达判定的基础上，收紧 `_clean_position` / `verified_arrival` 的坐标数值域 —— `bool`（`True`/`False`）、`NaN`、正负 `Infinity`、缺字段、错误类型、不可解析/非有限字符串一律 fail-closed，绝不写入正向 `EVENT` 到达经历或 actionable `LOCATION` 新事实；合法负坐标与小数（含有限数字字符串）照常通过。`verified_arrival` 调用链与结果分类语义不变；`allow_medium=false`、19 工具不变，**无新表无新迁移**（最高仍 36）。 | 已实现：全量 `3697 passed`；**真实 Java 门禁 PASS**（feedback 反馈链 + positive/blocked/cancel） |
| [MINECRAFT_PHASE7F2.2.1.md](MINECRAFT_PHASE7F2.2.1.md) | Numeric Conversion Overflow Hardening（Phase 7F.2.2.1 数值转换溢出加固）：在 7F.2.2 的有限实数域之上补齐**整数 → 浮点转换溢出**缺口 —— `_finite_axis()` 原本只捕获 `TypeError` / `ValueError`，而 `float(10 ** 400)` 抛的是 `OverflowError`，会让 `_clean_position()` 直接炸穿（以异常替代契约要求的 fail-closed `None`）；现纳入 `OverflowError` 并统一 fail-closed 返回 `None`。保留 `math.isfinite()` 与对 `bool` / 非有限值 / 错误类型的既有拒绝，**不新增**坐标绝对上限，不改合法负坐标 / 小数 / 有限数字字符串兼容性，`verified_arrival` 调用链与结果分类语义一字未改；`allow_medium=false`、19 工具不变，**无新表无新迁移**（最高仍 36）。 | 已实现：全量 `3734 passed`（基线 3697 + 37）；**真实 Java 门禁 PASS**（feedback 反馈链 + positive/blocked/cancel） |
| [MINECRAFT_PHASE7F3.md](MINECRAFT_PHASE7F3.md) | Bounded Multi-leg Exploration（Phase 7F.3 有界多段探索）：在 7F.1 单段探索之上实现第一个**有界多段**闭环 —— 最多 3 段、**共享同一份 24 格总路线预算**（绝不变成每段 24 格）；第 1 段沿用 7F.1 规则，后续段只从真实 POI 按全序贪心选取（与既有路点 ≥1 格、与上一段 ≥4 格、累计不超预算），观察不足即**明确缩短路线**。逐段目标 / 半径 / 距离写进 `ExpectedFinalState.route` → 进 `plan_hash`、确认指纹与步骤授权；`drive()` 在**执行下一步之前**验证所有已走完但未验证的段（置于执行前以同时覆盖崩溃恢复），任一段没到或读不到世界 → `FAILED(VERIFICATION)` 且**后续路点一步不走**；逐段证据持久化于`record.leg_verifications`。`legs=1` 默认与改动前**逐字节相同**（plan_hash `028311297b1e114e9fac`）。`allow_medium`、19 工具、两道门不变，**无新表无新迁移**（最高仍 36）。 | 已实现：新增 `tests/test_multi_leg_explore.py` 26 项；全量 `3760 passed`；**真实 Java 门禁 PASS**（多段逐段真坐标证据 + 中途失败止步；7F.1/7F.2 回归同轮 PASS） |
| [MINECRAFT_PHASE7F4.md](MINECRAFT_PHASE7F4.md) | Action Dispatch & Recovery Safety（Phase 7F.4 派发与恢复安全）：修掉「外部 runtime 已接受动作、但本地还没持久化 `action_id`」的恢复歧义。**派发前先落盘意图**（`dispatch_state=INTENT` +持久化代次 + 稳定关联键 `dispatch_key_of(task_id, plan_hash, step_id, generation)`），于是「磁盘上没有意图」证明外部没被调用（可安全重试），而「有意图但没有 `action_id`」无法区分「没发出」与「发出了没答复」→ 一律 fail-closed。四态状态机`INTENT`/`ACCEPTED`/`AMBIGUOUS`/`SETTLED`；歧义时**受控暂停 + 清授权 + `replan_required` + 尽力真停**并给出可核对关联键。`recover()` / `resume()` / `drive()` 三重守卫挡住未决派发；传输层失败按 code 判定为歧义。runtime **不具备**幂等键与按键查询能力（逐条证据见文档 §3），故**明确不宣称 exactly-once**。新增 19 项故障注入覆盖 10 个崩溃时点。边界：19 工具、`allow_medium=false`、两道门、多段 ≤3 段与 24 格预算、`explore_legs=1` 默认值全部未改；**无新表无新迁移**（最高仍 36）。 | 已实现：`pytest` **3779 passed**（基线 3760 + 19）；**真实 Java 门禁 PASS**（单段 + 反馈 + 多段正向/途中失败/取消） |
| [MINECRAFT_PHASE7F5.md](MINECRAFT_PHASE7F5.md) | Runtime Lifecycle Reconciliation & Bridge Trust Boundary（Phase 7F.5 运行时生命周期对账与桥接信任边界）：Node 进程启动时生成**运行时实例标识** `rt_<时间>_<随机>`，写进 `health`/`status`/每一条 `minecraft.action.*` 事件；Python 在既有健康探测里记录它，并在接受动作时固化到 `TaskStep.dispatch_runtime_instance`（只解决关联唯一性，**不是**幂等键）。新增**可从持久化记录重建**的 `store.list_pending()` / `store.find_pending_by_action()`（按真实 `state` 列取全部非终态、默认不限条数）→ `on_action_event()` 与启动恢复**彻底摆脱「最近 50 条任务」**（**无新表、无迁移**）；0 匹配不改任何状态、**≥2 匹配一律判歧义**（不再取第一个匹配项）、1 匹配还要校验实例（旧实例迟到事件不得结算新实例的动作）。只有**成功的身份响应**才更新实例：真的换人时旧实例的未决动作一律 `AMBIGUOUS` + 清授权 + `replan_required` + 人工核对，并留存原因/实例/action_id/dispatch_key/代次/时间戳，**绝不因新 Runtime 可用而重发**。桥接 API 增加**服务端凭据校验**：Python 每进程生成并经 `MC_BRIDGE_TOKEN` 注入，Node 在**所有路由之前**强制校验且**未配置凭据即拒绝启动**（实测 exit 1），缺失/错误凭据 → `401` 且零副作用，凭据不进 Git/日志/错误响应/事件/payload/WebUI（注册表标记为敏感项）。 | 已实现：新增 `tests/test_runtime_lifecycle_reconciliation.py` 14 项（覆盖 10 个生命周期场景）+ `tests/test_bridge_auth_boundary.py` 9 项（真实 HTTP 服务器 + 真实客户端）+ `minecraft_runtime` E2E 真实请求鉴权段；**真实 Java 门禁 PASS**（单段 + 反馈 + 多段正向/途中失败/取消） |
| [MINECRAFT_PHASE7F5.1.md](MINECRAFT_PHASE7F5.1.md) | Runtime Identity Observation & Correlation Guard（Phase 7F.5.1 运行时身份观测与关联守卫）：**7F.5 的定向修正**。观测侧：`_note_runtime_instance()` 原先只在 `_start_runtime()` 的就绪检查里被调用 → 现在 `_ensure_runtime()` 的每次健康探测、`status()` 与 `_poll_loop()` 的**成功响应**都按同一协议处理身份字段（只有成功且含有效身份的响应才更新；超时/连接失败/401/无效响应/缺字段一律不更新），于是**外部托管 Runtime（`auto_start_runtime=false`）在 Python 不重启的情况下换实例也能被主动发现**，不必等旧动作 TTL 过期；新增 `probe_runtime_instance()` 把「不可达」与「可达但不报告身份」分开。派发侧：`_resolve_dispatch_identity()` 在**落盘派发意图之前**确定实例，Runtime 可达却无身份 → **fail-closed 暂停**（绝不在只按 action_id 关联的前提下发动作）；适配器在调用前后各读一次实例并回传（调用期间换人且动作已外发 → 按歧义返回，真实 action_id 留在证据里）；`_dispatch_precheck()` 在**真正发出外部动作之前**用**持久化真相**复核（终态/已非 INTENT/代次不符/授权被清/实例不符 → 一律不发），**刻意不用锁**以免与对账里的 `stop` 互相递归等待成死锁。事件侧：记录有实例而事件**缺**实例 → **不得结算**（来源无法可靠验证）；新格式记录无实例 → 歧义；**旧格式行**走明确界定的兼容路径并持久化 `dispatch_legacy_fallback` 标记（借现有 payload 序列化，**无数据库迁移**）。 | 已实现：新增 `tests/test_runtime_identity_observation.py` 10 项（真实 HTTP + 真实客户端 + 真实服务请求路径，含 `auto_start_runtime=false`、暂时失败恢复不误判、401/无效/缺字段不伪造身份、回调等待 `stop` 不死锁）+ `tests/test_event_correlation_matrix.py` 13 项（关联矩阵 + 派发身份守卫）；全量 `3825 passed`（基线 3802 + 23）；**真实 Java 门禁 PASS**（7F.1 单段回归 + 7F.2 反馈回归 + **真实 Node Runtime 外部重启门禁 20/20**：真实世界动作进入未决 → 停 Node（Python 不退出）→ 外部重启 → 真实健康检查发现新实例 → 旧动作歧义暂停且证据完整 → **零重发** → 第二个新任务不越过自己的确认门） |
| [MINECRAFT_PHASE7F5.2.md](MINECRAFT_PHASE7F5.2.md) | Pre-dispatch Pause Race Closure（Phase 7F.5.2 派发前暂停竞态闭合）：**定向并发安全修正**，不新增玩法能力。审计确认三处真实缺陷：`_dispatch_precheck()` 只判终态而 **`PAUSED` 不是终态**、`pause()` 在 `pending_action_id` 为空时直接持久化 `PAUSED`、派发被拒后 `drive()` 仍持有旧 `RUNNING` 对象（会把持久化状态覆盖回去并重复推进同一步）。实现：统一谓词 `_dispatch_allowed()`（**只有 `RUNNING` 明确允许派发** + 步骤仍是本次代次的 `INTENT` + 代次未变 + 授权仍有效且绑同一版冻结计划 + 实例一致 + 未暂停/未要求重规划），落盘前与落盘后**两道复核**（落盘前复核到 `_save` 之间无 await）；`drive()` **每轮重读持久化真相**、`_run_step` 用新的 `blocked` 结果让流程立即停止；`_settle_blocked()` 把「确认没发出」的意图**如实 retire**（绝不谎报成「结果未知」，代次不变）；`authorize_step()` 只放行 `RUNNING`；`_settle()` 加结算前新鲜度守卫 + `_record_settlement_under_pause()`（只记步骤证据、绝**不把 `PAUSED` 推回 `RUNNING`**，但动作 `action_id`/`pending` 必须留记录，杜绝「任务已暂停而动作仍在跑且无记录」的假象）。**不加锁**：对账内 `stop_action()` → 健康检查 → 实例变化回调是真实重入链，故靠持久化状态比较 + 发动作前复核排序，并给身份对账加**只打标记、绝不等待**的再入保护。新增 11 项确定性故障注入（可控 store 钩子 + `asyncio.Event` 门闩，不靠 sleep），含真实 SQLite 回读。另勘误：7F.5 已有 Bearer 鉴权与缺凭据拒绝启动，此前报告误记为「无鉴权」。 |
| [MINECRAFT_PHASE7F5.3.md](MINECRAFT_PHASE7F5.3.md) | In-flight Cancellation & Expiry Reconciliation（Phase 7F.5.3 在途取消与过期对账）：**只修在途生命周期安全**，不新增玩法能力。审计确认三个真实缺口：`find_pending_by_action()` 走 `list_pending()`（排除四个终态）⇒ **终态任务上的真实动作永远匹配不到终态事件**；`cancel()` 无条件清空 pending 并把步骤直接标 `CANCELLED` ⇒ 匹配依据丢失且是「动作可能仍在跑」的假象；事件先于 `action_id` 落盘时 `return None` ⇒ **永久丢弃**（另：`_pause_ambiguous_dispatch` 会改写终态）。实现：存储层新增 `find_by_action(include_terminal=)` 与 `list_outstanding()`（payload LIKE 预筛 + 精确比对，**不加列不迁移**）；终态**绝不被改写**（只收敛步骤证据，`terminal_state_preserved`）；`cancel()`/`expire()` 经 `_inflight_step()` 保留在途关联并写 `_outstanding_action_evidence()`（`dispatch_key`/代次/实例/`stop_after_accept`/`stop_confirmed` 进既有 payload）；**拿到真实 `action_id` 后再经既有 SAFE 控制面停一次**（停不掉不改变结论）；`_defer_action_event()`/`_consume_deferred_action_event()` 让**先到事件不被永久丢弃**、`_remember_settled_action()` 让**重复事件幂等**。**不加锁**（仍靠 7F.5.2 的只打标记再入保护）。新增 10 项确定性故障注入（`asyncio.Event` 门闩，含**真实 SQLite 回读**）覆盖 §四 八场景 + 新增真机门禁 `scripts/cancel_inflight_smoke_real.py`（真实在途取消 → 动作仍在管理或已收敛、任务保持 `CANCELLED`、**世界动作次数始终 1**）；两处既有断言按新语义收紧。 |
| [MINECRAFT_PHASE7F5.3_FOLLOWUP.md](MINECRAFT_PHASE7F5.3_FOLLOWUP.md) | Cancel/Expire Snapshot Race Closure（Phase 7F.5.3 Follow-up 取消/过期快照竞态闭合）：**7F.5.3 的定向修正**，只修生命周期竞态。根因：`cancel()`/`expire()` 的顺序是「`_require()` 读快照 → `await _stop_action()` → 在**旧快照**上算 `_inflight_step()` → **整份写回**」，而 `_stop_action()` 是长 await —— 期间 `_settle()` 已把真实 `action_id`/`pending_action_id`/`ACCEPTED` 写进 store，随后那次保存用旧快照把它**整份覆盖**（Sqlite 侧 `ON CONFLICT ... payload=excluded.payload`，无版本校验）→ 世界动作仍在跑、记录里却找不到它，迟到终态事件再也匹配不到任何任务。修复：新增**按任务、可重入**的 `_LifecycleLock` + `TaskRuntime._lifecycle()`（锁表有界），`cancel()`/`expire()` 改**三段式**（①临界区内重读最新记录、按最新真相判在途并**先落盘终态** → ②出临界区才 `await _stop_action()`，外部调用绝不夹在临界区里 → ③再进临界区重读最新记录、**只合并**停止证据，只增不减），`_settle()`/`_pause_ambiguous_dispatch()` 的读-改-写同锁；不变量：取消仍 `CANCELLED`、过期仍 `EXPIRED`、已接受必须保留真实 `action_id`+pending 直到终态事件收敛、`stop_requested=True` 而 `stop_confirmed=False`（请求过 stop ≠ 已停止）、绝不重发、不宣称 exactly-once；**无新表无新迁移**（最高仍 36）。 | 已实现：新增 `tests/test_cancel_expire_snapshot_race.py` **8 项**确定性交错用例（**每一次** `minecraft_stop` 独立门闩，「取消那次 stop 还没回来、结算先写完真实关联」；逐次快照断言「一旦关联落盘，后续任何 save 都不得抹掉」；cancel/expire 各自的「已接受但响应未交付」窗口 + 「接受之前取消」对照窗口 + 迟到/早到/重复终态事件 + **真实 SQLite 全量回读**）；全量 `pytest` **3854 passed**（基线 3846 + 8）；**反证**：把顺序还原成修复前 → 同文件 **4 failed**，探针打出 `action_id='' pending='' dispatch=INTENT` 的丢关联结果；**真实 Java 门禁 PASS**（真实在途取消：真实 `action_id` 可审计、世界动作次数恒 1、stop 请求与终态收敛） |
`TaskProposal`（**准备怎样做**，执行层恒为 NONE，`LifeIntent != TaskProposal != Task != Action`）+ 能力目录（从既有 19 个原子工具的 `ACTION_RISK` 提取，风险分类一字未改）+ 能力缺口四值（`SUPPORTED/PARTIALLY/UNSUPPORTED/UNKNOWN`，**不靠猜把 UNKNOWN 升级成 SUPPORTED**）+ 可信身份桥解析目标（只认 VERIFIED；REVOKED/CONFLICT/缺失/服务器不匹配一律不猜）+ 指纹去重与 TTL（终态不可覆盖，重启不复活）+ 只新增 `task_proposals` 一张表（迁移 32，历史复用 `behavior_events`）+ 源码级/句柄级安全边界（不建 Task、不调工具、不改 Episode、不发 QQ）+ 只读 API/WebUI 卡片 | 见文档 §15（Real Java A/B/C/E + Real QQ A/C PASS；Real QQ D SKIPPED：既有检测器不认「跟着我」） |
| bible_source_罐头.txt | 内置角色的人物档案源文件（Bible 编译器输入） | **本地内容资产**：按「Character Bible 不得上传」约束，已移至 `config/bible_source_罐头.txt`（`config/` 不在发布镜像内） |
| bible_source_罐头.txt | 内置角色的人物档案源文件（Bible 编译器输入） | **本地内容资产**：按「Character Bible 不得上传」约束，已移至 `config/bible_source_罐头.txt`（`config/` 不在发布镜像内） |

## 运维速查

```bash
python scripts/backup_db.py                 # 在线快照（VACUUM INTO + 校验）→ ../CatooBot_backups/
python scripts/activity_smoke_real.py --phase report   # Phase 6A 真机门禁：当前 Episode / 最近活动（只读）
python scripts/migrate_db.py --db <file>    # 对单个库文件预演/应用迁移（先副本、后真库）
python -m app.sandbox.transfer export       # 导出角色数据（→ data/exports/character-<ts>.json）
python -m app.sandbox.transfer inspect <f>  # 离线校验导出包：格式、内容哈希、逐表计数
```

角色数据导出/导入的设计与安全流程见 [V3_DATA_EXPORT.md](V3_DATA_EXPORT.md)。
