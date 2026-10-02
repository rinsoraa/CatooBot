# Changelog

本文件记录 CatooBot 的版本演进。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号见 `pyproject.toml`；日期取自真实提交历史（本仓库 2026-09-30 起）。

## [Unreleased] — v2.1 Phase 3 Remediation · 架构旁路修复

- **动作启动不再绕过 Mutation（§9）**：`_start_action` 删除
  `move_entity 失败 → 直接写 location` 的回退；移动一律经 canonical gate
  （`SpaceSystem` 改为对称闭包 + 多跳 BFS，跑腿路线合法可达），失败则
  ENTITY_MOVE_REJECTED + ACTION_FAILED、动作 `blocked`、current_action 不变、
  拒绝记录以 `ok=False` 进 MutationLog（可审计）。
- **SocialSpace 变更入 spine**：新增 `update_social_space()`
  （presence/temperature → StateMutation → SOCIAL_SPACE_CHANGED），
  `_touch_social_space` 与 `note_user_interaction` 全部改走它；
  源码级扫描测试保证不再出现 `social.* =` 直接赋值。
- **删除 ACTIVITY_IDS 硬编码表**：activity 改为 `ActionDefinition.activity`
  结构化字段，由 Seed 的动作模板声明（gaming/reading/eating/out/…）；
  `_sync_state`、`_activity_index`、`_action_for_activity` 只读定义元数据，
  runtime 不再出现任何 action-id→语义映射；新增「第三活动 crafting」测试
  证明不同 Bible 无需改核心代码。
- **队列淘汰尊重紧急度（§13）**：满队列时仅允许*更紧急*的新事件淘汰
  最弱（同级最老）事件，等强或更弱者被拒（返回 False，不标记已消费）；
  被淘汰的事件可再次提交（淘汰 ≠ 消费）；critical 不会被普通事件挤掉；
  淘汰确定性有测试钉死（FIFO 在同级内保持）。
- 新增 `tests/test_sandbox_remediation.py`（13 个测试），总测试 1066。

## [Unreleased] — v2.1 Phase 3 · External Influence + Sandbox Wakeup + Action Interrupt

- **ExternalWorldEvent（§4）**：协议无关的外部事实类型
  （event_id/timestamp/source/actor_id/event_type/content/metadata/urgency/
  semantic_kind/target_activity/actor_relationship/correlation_id）——
  `app/sandbox/external.py`；QQ/OneBot/NapCat 对象不进入沙箱。
- **ExternalEventAdapter（§5/§18）**：`app/sandbox/external_adapters.py`
  以*原语*为输入产出外部事件（可在无 QQ 对象的环境完整测试），并把
  "来一起联机"这类措辞经系统级 cue 词表翻译为
  `semantic_kind=game_invitation / target_activity=gaming`；插件只负责
  提交原语（plugins/chat 不再构造协议对象、不再碰沙箱状态）。
- **External Influence Layer（§6/§7）**：`ExternalInfluenceEvaluator` 只回答
  NO_EFFECT / OBSERVE / WAKE / INTERRUPT / REJECT（含 `activity_unavailable`
  等 reason_code），输入是 urgency/语义/关系/可用活动/可打断度——不改状态、
  不选动作、不调 LLM；是否拥有某活动由 World Seed 决定。
- **Sandbox Wakeup + 队列（§8/§9/§13/§19）**：`submit_external()` 入队
  （按 event_id 幂等去重、FIFO+紧急度优先），`wakeup()` 立即评估而不是等
  10 分钟 tick；`sandbox_state` 持久化 pending/seen，重启既不重放已消费事件
  也不丢未处理事件（§20）。
- **Action Interrupt / Resume（§14/§15）**：`InterruptedActionContext`
  （definition/progress/remaining_minutes/interrupt_reason/resumable——
  只存恢复所需，不存 Runtime 快照）；被打断的动作在新动作完成后按**剩余
  时间**恢复，并发出 ACTION_REQUESTED→ACTION_RESUMED 的新生命周期事件；
  存在临界需求时不假装没发生，直接放弃恢复。
- **因果链（§10/§11）**：EXTERNAL_EVENT_RECEIVED →（INTERRUPT 时）
  ACTION_INTERRUPTED → ACTION_STARTED → WORLD_EXTERNAL_INFLUENCE，
  causation 逐级串联；非法/不可表示的外部事件发 EXTERNAL_EVENT_REJECTED
  且世界状态零变更。所有效果仍经 Phase 2 的 Mutation/Event spine
  （含快递 present 改走 apply_object_effect、需求变化走 _adjust_need）。
- 新增 `tests/test_sandbox_external.py`（18 个测试：无影响消息 / 邀请中断 /
  恢复与临界需求抢占 / 幂等（含 legacy 桥）/ 队列优先级 / 双沙盒隔离 /
  协议隔离（AST 断言无 onebot 依赖）/ 第二角色阿澈（无该活动→REJECT）/
  重启持久化 / evaluator 单元面），总测试 1053。

## [Unreleased] — v2.1 Phase 2 · Entity Interaction + Event Bus + State Mutation

- **Sandbox 事件主干（§5/§13）**：新增 `app/sandbox/events.py`——
  `SandboxEvent`（event_id/timestamp/type/source/target/payload）+
  **causation_id**（谁导致了我）+ **correlation_id**（同一条行为链）；
  `EventBus` 为 Runtime 私有实例（§14，无全局单例），带 dispatch 深度护栏与
  correlation 链上限（§15：允许有界因果链，无限循环降级为丢弃+WARN）。
- **统一状态变更路径（§6/§52-§53）**：`take_item / acquire_item / move_entity /
  feed_pet / apply_object_effect` 成为唯一状态入口——每次变更记录
  source/reason/before/after 并发出对应事实事件
  （ITEM_CONSUMED / INVENTORY_DEPLETED / ENTITY_MOVED / PET_FED /
  OBJECT_STATE_CHANGED…）；`interactions.py` 只经此路径改状态。
- **Entity Interaction（§11-§12）**：`InteractionResolver` 按**实体 kind**
  （pet/object/space）给出 `InteractionCandidate`（feed/observe/take_item/
  inspect/move + requirements）并执行 `Request → 验证 → Mutation → Event`；
  不做决定、不调 LLM；宠物饥饿反应（pet_care ↑）改为总线订阅者而非内联 if。
- **动作生命周期入链（§10）**：ACTION_REQUESTED → STARTED → EFFECT_APPLIED →
  COMPLETED / FAILED / INTERRUPTED 全部发出事实事件并以
  `act_<instance.id>` 关联；`feed_cat` 专属分支删除，改为通用 `pet:feed`
  效果（§18：动作效果按 id 语义路由，不按角色特例）。
- **四条完整因果链落地**：宠物饿了（PET_HUNGRY→APPROACHED→feed→
  ITEM_CONSUMED→PET_FED，全链可重放）；库存耗尽（ITEM_CONSUMED→
  INVENTORY_DEPLETED，causation 直指消费事件）；实体移动（REQUESTED→
  验证→MUTATION→MOVED/REJECTED，非法空间绝不产生错误状态）；动作生命周期
  （含验证失败路径）。
- 事件总线为 transient（§20）：持久化仍走既有 SandboxEventRecord/store；
  第二角色（阿澈，无宠物、咖啡锚点）下整条主干照常工作，名称零依赖。
- 新增 `tests/test_sandbox_causality.py`（15 个因果链测试：链路/嵌套/防环/
  深度护栏/双沙盒隔离/第二角色回归/候选需求面），总测试 1035。

## [Unreleased] — v2.1 Phase 1 · Character Bible → Sandbox Foundation

- **Character Bible 成为唯一 Canonical Source（§6）**：新增
  `CharacterDefinition`（bible → 角色契约，§7）与 `CharacterWorldSeed`
  （bible+definition → 完整初始世界，§26），`SandboxRuntime` 的角色实体 /
  宠物 / 空间 / 物件 / 库存 / 动作 / 项目 / 社交空间 / 模式全部从 Seed
  初始化——`CharacterEntity()`/`PetState()` 零参默认名（罐头/小喵）已删除，
  name/location 改为必填（§9）。
- **Seed 可导出、可复现（§28/§29）**：`export_seed()` 输出完整初始世界 JSON；
  同一 bible+simulation_seed 重复构建逐字节一致（有测试断言）。
- **动作目录模板化（§20/§21）**：系统模板（`action_templates.py`）+ Seed 解析
  （锚点物品 `{drink}/{snack}/…`、空间映射、动作归属按 bible 关键词推导）——
  没有可乐的档案不会拥有买可乐动作链。
- **模式 Definition 化（§18/§19）**：五模式不再是 Python 枚举；触发
  （home/outdoor/time/action/social）、时间窗、口癖短语、语气提示全部来自
  bible Modes 段；叠加机制保留。
- **迁移 21**：`sticker_assets.scope`（character/global）+ 存量回填；重置时
  角色表情清除、全局资产与平台数据保留（§44-§46）。
- **重置清单补缺口（§38）**：`character_states / topics / behavior_events /
  initiative_state / social_observations / reply_outcomes / expression_* /
  memory_relations` 与 `social_engagement` 设置键进入角色重置范围；
  `PRESERVED_TABLES` 移除三个不存在的幽灵表名。
- **接口预留（§49-§53）**：`mutations.py`（StateMutation 审计 + MutationLog +
  EntityInteraction/DecisionRequest/SandboxEventBus），动作完成与位置变更已
  开始记录 source/reason/before/after。
- **Parser 升级（§30-§37）**：空间 kind/parent/connections、物件库存键、
  `（充足）=10`、社交空间正则提取（替代硬编码关键词表）、规则带
  source_section 溯源、对话示例舞台指示 → Behavior Candidate（不自动成规则）、
  Coverage 升级为 Parsed→Compiled→Seeded→RuntimeConnected→Tested 链。
- WebUI 去「小喵/罐头」写死标签；沙盒 AI tie-break 提示词改为由
  CharacterDefinition 注入；persona 同步去硬编码（habits/traits/dislikes/
  关系句全部来自 bible 数据）。

## [Unreleased] — v3 线（任务 0–25）

### 记忆
- 关键词候选改走 **SQLite FTS5** 索引（迁移 15，`search_text` 双字切分 + 触发器同步）。
- 向量改为 **float64 blob + 预存范数**（迁移 16，`math.sumprod` 单次点积）：候选池内检索
  由 ~159 ms 降到 ~18 ms；JSON 列保留为回退，删除条件已写入代码注释与文档。
- `compression_use_llm` 的 LLM 压缩真正实现；抽取向上的配额与保留策略未变。
- 数据库写入失败不再丢失：**outbox**（JSONL + fsync + 原子重写）落盘并在恢复后重放（任务 14）。
- **图片记忆**：她看懂的图片会成为可回忆的记忆（`source=vision`），WebUI 记忆页可按来源筛选（任务 21）。
- **抽取再也不能静默失败**（任务 25）：每次抽取输出一行
  `[Memory.Extract] saved=x/y reason=…`；原因分类 `disabled / no_models / empty_content /
  parse_failed / timeout / ai_error / invalid_item`；解析失败时 WARN 附脱敏后的前 200 字；
  空 content 但带 reasoning 的回复单独标注并**重试一次**指定模型（未配置时明确提示
  `memory.extraction.model`）；失败重试保留根因（`reason=empty_content retry=failed`）；
  `saved` 只统计**真正入库**的行；连续 5 次零入库 → 一次 WARN + `/memory/health` 红条 +
  `memories_extracted` / `memory_extract_failed{reason}` 计数。

### AI 与运行时
- 按 `base_url` 的并发闸门、限流/服务端错误退避、逐次调用的用量行（迁移 17，`ai_usage`）。
- 启动校验：模型绑定了不存在的 provider → **ERROR 并列出已知 provider**；
  AI 已启用但没有任何可用模型 → **ERROR + 仪表盘横幅**（任务 25）。
- 事件循环卡顿看门狗（任务 16）；实时播报与状态经鉴权 WebSocket 推送（任务 17）。
- 推理模型只输出思考、导致空回复时：同模型重试一次再故障转移，绝不把空回复交给角色层。
- 相对路径（贴纸库/媒体/日志/插件目录）一律按 `PROJECT_ROOT` 解析：
  从别的工作目录启动也不会换掉数据位置（任务 25）。

### 社交与行为
- **回复效果回流**（任务 20，迁移 18）：每回合记录观察 → 结算
  `engaged / ambient / silence / unknown`（保守极性、只记 0/−1）→ 时间衰减加权的参与度 EMA
  软性反馈给群参与决策；群参与概率改为**确定性额度**（1.0 = 每条都参与）。
- `[Initiative] Gate rejected` 仅在拒绝原因发生变化时记 INFO，其余降为 DEBUG（任务 25）。
- 世界事实注入 + 越界声明审计：沙盒实体标签命中即注入真实数字（含库存）并禁止编造，
  出站再做一次矛盾声明剥离；模型思考内容只上控制台、不落盘。

### 媒体
- **识别 → 判断 → 收藏**：QQ 原生表情/动画表情与普通图片都在"要不要回复"决策之前
  后台识别，识别摘要并入参与判断；配字被当作对方真正想说的话。
- 普通图片永不进入表情库（`looks_like_sticker` 边界）；表情收藏**真正下载文件**
  （sha256 去重 + 平均哈希 + 粘贴检测），收藏判定与视觉摘要共享一次下载。

### 运维与安全
- WebUI 路由清单守卫（97 条）+ 登录限流 + CSRF；`server.py` 拆分为 13 个域路由模块（任务 18）。
- 日志脱敏扩展；测试与运行目录解耦并有"不得写真实数据"的守卫。
- **`scripts/backup_db.py`**：`VACUUM INTO` 在线快照 + `integrity_check` + 逐表行数核对
  （绝不对运行中的 WAL 库做文件拷贝）。
- **`scripts/migrate_db.py`**：对单个库文件预演/应用迁移链，报告版本、新增表、只许增不许减的行数核对。
- **角色数据导出/检查**（任务 24 里程碑 1，`app/sandbox/transfer.py`）：
  `python -m app.sandbox.transfer export|inspect`；单文件 JSON、含 schema 版本 / Bible hash /
  逐表计数 / 内容哈希，流式写入（单块 < 64 KiB）；`inspect` 离线校验格式、哈希与计数；
  不含任何密钥与平台数据（有测试断言）。与迁移 19（删 JSON 列）配套的保险。
- 迁移 14–18 已在真库执行（schema 13 → 18），预演先于真库、备份先于预演。

### 打包
- `LICENSE`（Apache-2.0）、`NOTICE`（角色内容不随代码授权）、`EULA.md`（隐私说明 + AI 声明）。

### 重启后的观察期检查项（本批改动的落地核对）
- 模型页 `ai_usage` 出现 `purpose=social_observer`，且延迟约 3.4s（`models.decision` 指向非推理模型后）。
- `[Watchdog]` 卡顿告警带上了「正在执行：… / 任务栈：…」线索，可直接定位阻塞来源。
- `/social/group` 显示「因 poor_timing 连续 N 次未开口」（defer 只推迟、不否决）。
- 模型页 `ai_empty_finish_length` 停止增长（`ai.max_tokens` 建议 2048 起）。
- `/memory/health` 无红条，`[Memory.Extract]` 无 `parse_failed`。

## [2.0.0] — 2026-10-01 · Character Life Sandbox

- 人物档案 → **Bible 编译器** → Seed：实体/空间/物件/需求/动作/规则/模式；
  有界 tick、中断与恢复、快照与回放、沙盒 WebUI 全家桶、角色重置。
- 删除旧 World runtime 全部代码/测试/界面：活动、主动性与生活片段归沙盒所有；
  `CharacterState` 只能由 `SandboxStateManager` 写入。
- 群参与与主动性并入 `/sandbox/chat`（私聊回复 / 群参与 / 主动回复三块独立配置），
  `/behavior` 重定向；配置段顺序修正，两处配置文件对齐。
- 角色页人设改为从 Bible 编译，支持版本感知的人设同步与热加载。

## [1.2.0] — 2026-09-30 · Human Conversation Runtime + Character Continuity

- 回合运行期：合并/去抖、生成中断、静默判定、分段与延迟模型（区间 + 抖动 + 状态因子）。
- 角色延续：未闭环话题、共同经历、互动画像、情绪惯性。
- 历史时效标记（`[今天凌晨4点]` 式）与出站标记剥离。
- 表情包采集落盘、视觉确认的表情包可收藏、回复会接住"配字"而不是播报收藏。

## [0.1.0] — 2026-09-30 · 从零自研的运行时骨架

- 自研 Event / Message / Segment / Router / Command / Permission / Plugin / Config / DB / Logger，
  **不依赖** NoneBot / NcatBot / MaiBot / AstrBot / Koishi。
- OneBot 11 反向 WebSocket 适配（token 鉴权、单连接接管、断线保活）、
  API 客户端（echo/future/超时/断开清理）、事件总线、命令系统、权限、插件加载、SQLite（WAL）。
- 内置 `/ping` `/help` `/about`；WebUI 起步（登录 + 仪表盘）。
