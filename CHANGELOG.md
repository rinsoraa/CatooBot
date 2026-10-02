# Changelog

本文件记录 CatooBot 的版本演进。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号见 `pyproject.toml`；日期取自真实提交历史（本仓库 2026-09-30 起）。

## [Unreleased] — v2.1 Phase 7 Remediation · Goal 状态恢复 + Action Instance 一致性

- **blocked → active（§1）**：冷却期结束后重试成功的 blocked 目标会真正回到
  `active`（同一 goal_id、同一 correlation，不克隆），并发布
  GOAL_ACTIVATED(reason="resumed_after_cooldown")；GOAL_BLOCKED 历史保留。
  消除了「状态是 blocked 却在执行 Action」的语义不一致（§17 审计确认
  blocked 只在 `_block()` 设置、只在成功重试/取消时离开）。
- **GoalStep 绑定 Action Instance（§4-§6）**：ACTION_STARTED / COMPLETED /
  INTERRUPTED 的 payload 统一携带 `action_id` + `action_instance_id`；
  `GoalStep.action_instance_id` 成为一等字段（不再依赖
  `result["action_instance"]`）；`on_action_completed` 以 instance 精确匹配，
  action_id 仅在没有 instance 且**唯一**候选步骤时作兼容回退——
  两个目标共享同一 action 定义时，一次完成绝不会同时推进两者（核心回归测试）。
- **Resume 重绑（§7）**：Phase 3 的 interrupt→resume 产生新 ActionInstance 后，
  `GoalManager.rebind_instance()` 把正在等待该动作的步骤改绑到新实例
  （同一 GoalStep，不新建步骤），随后该实例完成才推进目标。
- **启动扫描按 Seed 发现耗尽资源（§10-§13）**：`GoalDetector.sweep()` 改为遍历
  `ActionDefinition.effects` 推导出的「世界里真正可补货的 (inventory, item) 集合」
  （`_restockable_targets()`），不再依赖 `inventory.items` 里是否残留 0 计数键；
  去重仍按 character+kind+target。附带修正 `take_item` 的耗尽语义：
  INVENTORY_DEPLETED 现在在**该物品**归零时发布（「最后一瓶」），payload 增加
  `container_empty` 说明容器是否也空了——此前是容器整体为空才发。
- 新增 `tests/test_sandbox_goals_remediation.py`（7 个测试：blocked 复活、
  instance 精确匹配、歧义回退不动、STARTED/COMPLETED 同实例、resume 重绑、
  无残留 0 键时启动恢复、非可补货物品永不成为目标），总测试 1171。

## [Unreleased] — v2.1 Phase 7 · Autonomous Life Loop & Goal Layer（她为什么会一直做下去）

- **目标层（§4-§9/§27）**：新增 `app/sandbox/goals.py` ——
  `Goal`（goal_id/character_id/kind/status/priority/reason/source/
  source_event_id/causation/correlation/target_*/progress/metadata/
  retry_count/next_eligible_at/current_step）+ `GoalStep`（只物化"当前下一步"，
  不预生成计划树）+ `GoalDetector`（事件驱动：INVENTORY_DEPLETED→补给、
  PET_HUNGRY→照顾宠物、PROJECT_PROGRESS_CHANGED→继续项目；启动再扫一次）
  + `GoalManager`（去重键 = character+kind+target，存在即更新不重建；
  单一"执行目标"（优先级：照顾宠物 0.8 > 补给 0.6 > 项目 0.4）；
  阻塞→冷却→再评估，重试上限后取消——绝不每 tick 空转）。
- **可补给性完全由 Seed 决定（§10/§11/§41）**：`restock_actions()` 从
  `ActionDefinition.effects` 的 `inventory:<key>:<item>` 反查——世界里没有
  任何动作能补回该物品，它就不是目标；无物品名/角色名硬编码。
  顺手修正模板语义：`buy_snacks` 补货对象从饮料改为零食（原为 Phase 1 笔误）。
- **Goal → Decision → Action（§15/§16/§28/§32）**：目标步先经 Phase 6
  `DecisionCoordinator`（新 trigger：goal_step / goal_conflict / goal_blocked），
  候选来自 Seed 且带规则/物件/库存前置；单候选→确定性（无 LLM），
  多候选→才用模型；执行前经 validator 重读世界（world_revision 过期即
  拒绝——有测试）。
- **持久化与恢复（§18/§33/§34）**：迁移 23 新增 `sandbox_goals` /
  `sandbox_goal_steps`（含 character+status、character+kind+target、
  next_eligible_at 索引）；重启后恢复目标并逐条复核世界——失效的步骤被
  丢弃重新规划，不盲信旧计划。
- **事件与因果（§21/§22）**：GOAL_CREATED / ACTIVATED / PROGRESS / BLOCKED /
  COMPLETED / CANCELLED / STEP_STARTED / STEP_COMPLETED 进入既有
  `SandboxEventType`；目标事件自成一条 correlation（`goal_<id>`），
  用 causation 指回触发事实（喝掉最后一瓶 → 目标链）。
- **与 Continuity / Memory 的边界（§19/§20/§42）**：`ContinuitySnapshot`
  新增 `active_goals`（最多 3 条），聊天上下文以「未完成目标」中性行呈现；
  只有 GOAL_COMPLETED 进入 Experience→Memory 候选（GOAL_CREATED 不会）；
  Memory 不反向生成目标。
- **打断与恢复（§30/§31）**：目标步执行中的动作被外部紧急事件打断后，
  经 Phase 3 的 interrupt/resume 机制继续——目标保持未完成并在动作恢复后
  接着推进（有测试）。
- 新增 `tests/test_sandbox_goals.py`（23 个测试覆盖 §36 的 20 项 +
  24 小时自主模拟 + 同种子可复现），总测试 1164。

## [Unreleased] — v2.1 Phase 6 · Cognitive Decision & Intent Layer（LLM 可提议，沙盒才决定）

- **决策层（§5-§20）**：新增 `app/sandbox/intent.py` ——
  `DecisionTrigger` / `DecisionCandidate`（候选只从世界生成：continue /
  action:<owned> / postpone，未拥有的动作不可能出现）/ `DecisionRequest`
  （含 request_id、character_id、trigger、截断的 CognitiveContext、候选、
  约束、deadline、**world_revision**、causation/correlation）/
  `IntentProposal`（严格 JSON：candidate_id + confidence）/
  `DecisionGate`（唯一候选→确定性；≥2 候选才需要模型）/ `DecisionValidator`
  （执行前重读实时状态：候选存在、动作存在、规则允许、物件/库存/前置满足、
  deadline、**world_changed 失效检测**）/ `DecisionCoordinator`（编排
  gate → 事件 → 模型 → 校验 → 事件）。
- **决策事件入既有 spine（§16/§17）**：新增
  DECISION_REQUESTED / PROPOSED / ACCEPTED / REJECTED / FALLBACK，
  PROPOSED/ACCEPTED/FALLBACK 一律挂在 REQUESTED 之下（RECEIVED →
  REQUESTED → …），全链带 causation/correlation。
- **邀请走进决策管线（§14）**：外部影响层判定"值得打断"后不再直接起动作，
  而是由 `_run_invitation_decision()` 建候选（核心朋友邀请优先级 0.85 >
  继续 0.5 > 推迟 0.4，规则受阻会成为不可执行候选）→ Coordinator →
  校验通过才 ACTION_REQUESTED →（必要时）ACTION_INTERRUPTED →
  ACTION_STARTED → WORLD_EXTERNAL_INFLUENCE。
- **确定性回退（§11/§28）**：模型不可用/超时/非法 JSON/未知候选/低置信度
  （< decision_min_confidence）→ DECISION_FALLBACK → 确定性挑选
  （临界需求 → 候选优先级 → 偏好继续执行），沙盒永不卡死；全部候选非法时
  DECISION_REJECTED 且零副作用。
- **不越权（§3/§12/§18）**：决策本身零世界变更（有测试逐项比对）；LLM 只在
  DecisionRequest 产生时被调用——每 tick、每次宠物/库存事件、每条聊天消息
  都不调用（有测试与源码事实支撑）；闲聊路径（character/runtime.py）零决策
  引用；Phase 2 的模糊裁决 tie-break 也被包装进同一事件/校验管线（不再有
  第二条 LLM 决策通道）。
- **候选/上下文预算（§23/§24）**：决策 prompt 只带世界行/状态行/需求行/
  目标 + 至多 3 条相关记忆（复用 Phase 5 检索与预算）+ 编号候选清单；
  CharacterDefinition 的 boundaries 仅取前 3 条作为约束行。
- **配置**：`sandbox.decision_model`（由 `models.decision` 统一注入并参与
  启动校验）、`sandbox.decision_timeout`（20s）、
  `sandbox.decision_min_confidence`（0.35）。
- 新增 `tests/test_sandbox_intent.py`（17 个测试：确定性单候选零模型调用、
  真实宠物链零模型、纯 tick 零模型、模型选择端到端、事件因果链、
  未知候选/低置信/超时回退、world_revision 失效与 deadline 过期、
  决策零变更、第二角色世界级否决 + 伪造提案 validator 兜底、
  规则禁止 LLM 选择、决策上下文带相关记忆（仅供参考）、
  决策事件不成记忆、gate/候选来源面），总测试 1141。

## [Unreleased] — v2.1 Phase 5 Remediation · Context Prompt Character-Neutral

- **Prompt 固定标签去性别化**：Phase 5 注入的三段固定文案改名——
  【近期延续状态】（角色近期生活状态…）/【相关生活记忆】（过去的重要经历…）/
  【最近发生的经历】（近期发生的重要经历…）；语义不变，不再假定角色性别，
  角色口吻仍由 Persona/LLM 层决定。
- **模块注释中性化**：cognitive.py 的 docstring/注释由 "what she may see /
  her world / her life" 改为 "the character / the character's world/life"。
- 新增 `tests/test_sandbox_neutral_context.py`（6 个测试：女性角色段落无
  性别词、男性角色 Lin 端到端 prompt 标签中性且动态数据（木工）照常进入、
  未标注性别角色正常工作、动态 seed 数据（项目/空间名）仍进 prompt、
  AST 级源码 guard（只查非 docstring 字符串常量，放过「他人」这类普通词）、
  旧标签确已退役），总测试 1124。

## [Unreleased] — v2.1 Phase 5 · Cognitive Context Bridge（Sandbox → 聊天上下文）

- **CognitiveContext（§5/§25）**：`app/sandbox/cognitive.py` ——
  `CognitiveContext` + `CognitiveContextBuilder`，入口
  `SandboxRuntime.cognitive_context(query, relationship_target)`；
  组装当前世界切片、v2.1 ContinuitySnapshot、相关 Sandbox 记忆、
  最近经历与检索记账，并提供 `as_prompt_payload()` 供 ContextBuilder 渲染。
  **严格只读**：不调用任何 mutation/Action API、不调 LLM（有源码 guard 测试）。
- **确定性检索（§7-§9）**：`SandboxMemoryStore.retrieve_relevant(query,
  entities, limit, min_score, max_chars, require_evidence)` ——
  评分 = 0.45×关键词(复用 bigrams) + 0.15×实体命中 + 0.25×重要度 +
  0.15×新近度（14 天半衰期）；**证据门槛**（无关键词/实体命中不入选，
  重要度+新近度不能单独捞人）；只读 active（superseded 永不注入）；
  候选窗口 200 行、条数与字符双预算；排序 (-score, -id) 完全确定。
  预算可配：`sandbox.memory_context_limit / _max_chars / _min_score /
  _require_evidence / experience_context_limit`。
- **分层 Prompt（§11/§17/§27）**：`CharacterContextBuilder.build()` 新增
  `sandbox_context` 参数，在「当前世界事实」之后渲染
  【近期延续状态】【她自己经历过的相关往事】【最近发生的经历】，
  每段自带「仅作参考；与当前世界/当前消息冲突时以它们为准」的声明——
  旧记忆永不覆盖当前世界事实。
- **来源分离 + Trace（§6/§13/§28）**：conversation memory（既有
  MemoryManager 链，未动）与 sandbox memory 分区并列；context_trace 新增
  `conversation_memory` / `continuity_snapshot` / `sandbox_memory` /
  `recent_experience` 图层（含 count/query/memory_ids），v1.2 continuity 与
  v2.1 ContinuitySnapshot 各自独立、互不覆盖。
- **接线（§16/§23/§26）**：`CharacterRuntime.respond()` 内部取桥接数据
  （sandbox 关闭/不可用时静默降级为 None，聊天不受影响）；检索 query 与
  facts 一致（主动发言传 topic/动机，因此 Initiative 经 respond() 自然获得
  Sandbox Memory 与 Continuity，未新建第二套检索）。
- 新增 `tests/test_sandbox_cognitive_bridge.py`（16 个测试：相关记忆入 prompt
  与 trace、无关记忆排除、当前世界优先声明、跨角色隔离、Continuity/经历注入、
  双 continuity 来源分离、respond 不改 Sandbox（含只读源码 guard）、
  确定性排序、预算上限与高分优先、superseded 剔除、旧对话记忆链保持、
  sandbox 缺席聊天可用、Initiative 复用桥接、无 LLM），总测试 1118。

## [Unreleased] — v2.1 Phase 4 Remediation · Continuity 隔离 + Character-Neutral Memory 文案

- **Continuity 按角色隔离（§remediation-1）**：快照持久化键从固定
  `continuity_snapshot` 改为 `continuity_snapshot:<character_id>`，
  persist/load 使用同一 scoped key；同一数据库上的两个角色互不覆盖。
  兼容：仅当 scoped key 不存在时读旧固定键，且旧快照必须
  `character_id` 与本角色一致才会被接受（无主的旧数据保持不可认领，
  绝不错误归属）——旧键不删除。
- **记忆文案改为 Character-Neutral（§8-§10）**：Phase 4 新代码里的
  「她…」句式全部替换为中性事实句——
  完成了{name} / {name}进行到一半被打断 / 继续进行{name} /
  与{target}完成了一次互动 / {pet}饿了，已添{food} / 首次获知：… /
  {actor}的邀请改变了当前安排 / 在{space}发生了社交活动；
  MemoryCandidate.content 同步中性化（并删除未经证据的「她答应了」推断，
  只陈述"外部事件改变了安排"）。角色口吻留给后续 Persona/NLG 层。
- 新增 `tests/test_sandbox_memory_remediation.py`（6 个测试：
  同库双角色快照互不覆盖且互不污染、旧键归属校验、第二角色文案零角色词、
  第三 synthetic 角色 Lin（木工/阅读）产出「完成了木工」而非「她做完了木工」、
  三份 foundation 源码的角色词/性别词扫描、模型无性别化默认值），
  总测试 1102。

## [Unreleased] — v2.1 Phase 4 · Sandbox Experience → Memory → Continuity Foundation

- **Experience 层（§5-§7）**：`app/sandbox/experience.py` ——
  `ExperienceRecord`（character_id/kind/summary/importance/location/actors/
  source_event_ids/causation/correlation/action_id/metadata）+ 订阅事件总线的
  `ExperienceBuilder`：只接受业务事件（动作完成/中断/恢复、交互、喂宠物、
  项目里程碑、首次知识、外部影响、社交露面），NEED_CHANGED/REQUESTED/
  STARTED/移动请求等噪音被过滤；同一条行为链（correlation）归并为**一条**
  Experience（喂猫链不会产生 5 条近似记录）；importance 为确定性评分。
- **Memory 候选（§8/§16/§19-§22）**：`MemoryType`（episodic/semantic/
  social/world_fact）+ `MemoryCandidate`（含 importance/confidence/scope/
  dedupe_key/provenance 字段）+ 确定性 `MemoryCandidateBuilder`；候选先落
  `sandbox_memory_candidates`（status: candidate/promoted/rejected/
  duplicate），importance ≥ 0.5 才晋升为长期记忆。
- **Memory Store（§10/§11/§15/§17）**：`SandboxMemoryStore` 复用**既有**
  `memories` 表与 `MemoryRepository`（无第二套存储、无 embedding 路径）：
  `character_id` 为隔离边界（同库双角色互不可见）、`provenance` JSON 记录
  memory→experience→event 全链、`dedupe_key` 幂等去重（同链重复处理不增行；
  不同日期的真实重复事件因 episodic 键含 correlation 而不被误并）、
  world_fact 语义身份变化时旧行 `superseded` + supersedes 关系。
- **迁移 22**：`memories` 增 character_id/provenance/dedupe_key（旧行默认空，
  向后兼容）+ 两个新表 `sandbox_experiences` / `sandbox_memory_candidates`
  + 索引；`SOURCES` 增 "sandbox"。
- **Continuity 读模型（§23-§25）**：`ContinuitySnapshot` +
  `ContinuitySnapshotBuilder`（world 摘要/当前动作与位置/知识/重要经历/
  活跃记忆/未完成项目/待处理外部事件/关系上下文），生成后写入
  `sandbox_state`；**只读**——不进入 Decision/Persona/Speech/Relationship，
  也不修改世界状态（有测试钉死）。
- 新增 `tests/test_sandbox_memory_foundation.py`（19 个测试：动作→记忆溯源、
  tick/需求漂移不污染、最后一瓶可乐、喂猫单条、邀请经历、项目里程碑、
  去重、跨角色隔离、重启持久化、Continuity 组合与只读、Knowledge≠Memory、
  provenance 全链、低分候选拒绝），总测试 1096。

## [Unreleased] — v2.1 Phase 3.5 · State Authority Closure（Need / Project / Knowledge 收口）

- **Need（§3）**：新增 canonical `adjust_need(key, delta=, relieve=, source,
  reason, correlation, causation_id)`（两种语义：绝对增量 / 按剩余压力比例
  缓解），每次真实变化 → StateMutation(needs:<key>, level, before/after) +
  NEED_CHANGED；`_complete_action` 的 need_relief/need_cost、委托完成的
  work_need、agent 结果、用户互动全部改走它；tick/离线结算的连续漂移
  按时间演化保留，但**带位跨越**（calm/soft/strong/critical）会发
  NEED_CHANGED（payload 带 band_before/after）——世界事实可追溯。
- **Project（§4）**：新增 canonical `update_project_progress(project_id,
  delta, source, reason, …)` → StateMutation + PROJECT_PROGRESS_CHANGED
  （payload 含 before/after/delta/completed）；击穿 1.0 时同事件标记
  completed=true 并把项目置为 completed，不另造第二套项目事件系统。
- **Knowledge（§5）**：新增 canonical `set_knowledge(key, known=, source,
  reason, data=, …)` → StateMutation(knowledge:<key>, entry, before/after) +
  KNOWLEDGE_CHANGED（payload 含 key/before/after/source/reason）；
  learned_at/source/data 字段保持；与 Memory 继续分离（未接任何记忆层）。
- **因果连接（§11）**：`_complete_action` 先发 ACTION_EFFECT_APPLIED，随后
  所有效果（消耗/获取/物件/需求/项目/知识/宠物/回家移动）以它为
  causation_id、同一 correlation 挂进同一行为链；ACTION_COMPLETED 亦在同链。
- 新增 `tests/test_sandbox_state_authority.py`（11 个测试，含作用域感知的
  源码 guard：canonical helper 之外不得出现 needs.add/relieve、
  project["progress"]=、knowledge[...]=），总测试 1077。

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
