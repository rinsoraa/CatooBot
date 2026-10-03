# CatooBot WebUI v1.0 · W5 领域审计（Domain Map）

> 本文件是 W5 的审计产物：把 v0.8 已有的领域能力逐条对照到 v1 的新信息架构，
> 并记录「已有 accessor / 新增最小 API / 仍未暴露」三态。审计基于真实源码
> （file:line 为证），不按旧页面名字猜测用途。基线：W4 `51cfaeef`。

---

## 1. 映射总表（旧能力 → v1 页面 → 现有/新增 API → 处理）

| 领域 | 旧页面 / 路由 | 旧 Service（真实方法） | v1 页面 | v1 API | 迁移处理 |
|---|---|---|---|---|---|
| 角色 | `GET/POST /character`（`routes/identity.py:329-330`） | `AdminService.get_persona/save_persona`（`services/admin.py:181,184`） | `/character`（`pages/character/Character.vue`） | W2 `GET/PATCH /api/v1/character` + `GET /character/state` | 只读展示 + 既有 PATCH；**不搬迁** persona 表单（配置中心负责） |
| 角色状态 | `POST /character/state`（`:331`） | `AdminService.set_state`（`admin.py:197`） | `/character` 状态卡 | **W5 新增** `PATCH /api/v1/character/state` | 最小 action 补齐 |
| 角色导出/导入 | `/character/export|import[/confirm]`（`:332-334`） | `export_character/import_character_*`（`admin.py:426,432,440`） | 暂不迁移（§99 禁止导出配置；角色数据导出保留在 `/legacy`） | — | 记录为 known limitation |
| 世界总览 | `GET /sandbox`（`routes/sandbox.py:517`） | `_sandbox_data()`（`:476-506`）+ `SandboxRuntime.context()`（`sandbox/runtime.py:3162`） | `/character/world` | W2 `GET /api/v1/world`（W5 扩块） | 读模型加宽（needs_full/spaces/objects/inventories/pet/social_spaces/action_defs/modes_defs/goals_full + action 细节 + interrupted 对象） |
| 需求 | `GET /sandbox/needs`（`:519`） | `NeedSystem.all/bands/pressure`（`sandbox/needs.py:29,74,84`） | `/character/world` NeedList | 同上 `needs_full[]` | 仅真实 band/level/growth，**不给假趋势** |
| 世界轨迹 | `GET /sandbox/trace`（`:521`） | `sandbox.store.recent_events/timeline` | `/character/world/timeline` | **W5 新增** `GET /api/v1/world/timeline` | 事件粒度沿用，不做每秒一条 |
| 话题 | `GET /topics`、`POST /api/topics/{action}/{id}`（`:515-516`） | `BehaviorService.list_topics/topic_action`（`services/behavior.py:301,305`） | `/character/world/timeline` 话题区 | **W5 新增** `GET /api/v1/world/topics`、`POST /world/topics/{id}/{action}` | 迁移 + 危险动作确认 |
| 行为设置 | `/sandbox/chat`、`/behavior/*`（`:510-514`） | `BehaviorService.save_settings/preview/...` | 不迁移（属配置中心，W4 已覆盖回复/分段/作息） | W4 `/api/v1/config` | 由配置中心承担，避免第二套入口 |
| 用户 | `GET/POST /users`（`identity.py:335-336`） | `AdminService.list_users/save_user_profile`（`admin.py:227,243`） | `/social`（用户 tab） | **W5 新增** `GET /api/v1/social/users`、`PATCH /social/users/{id}` | 读模型合并人数/关系/承诺/经历 |
| 人物详情 | 无（旧页只有列表） | `RelationshipStore.get/important`（`sandbox/relations.py:494,539`） | `/social/users/{person_id}` | **W5 新增** `GET /api/v1/social/users/{id}` | 五区块详情（Identity/Relationship/Commitments/Experiences/Memories） |
| 群组 | `GET /groups`、`POST /groups/toggle`（`:337-338`） | 原为裸 SQL（`identity.py:292-297`）→ **W5 提升为** `AdminService.set_group_participation` | `/social`（群组 tab） | **W5 新增** `GET /api/v1/social/groups`、`PATCH /social/groups/{id}` | 单一写路径：旧路由与新 API 都走 service |
| 关系 | 无独立页（`/social` 面板间接展示） | `RelationshipStore.get/important`（只读） | `/social/relationships` | **W5 新增** `GET /api/v1/social/relationships` | 只读（§22；无合法 Admin 写路径） |
| 承诺 | 无（Phase 9 之后才存在的领域） | `CommitmentManager.all/open/for_person`（`sandbox/commitments.py:612,618,624`） | `/social/commitments` + 详情 | **W5 新增** `GET /api/v1/social/commitments[,/{id}]` | 只读 + 因果链（person→space→goal→action→outcome） |
| 会话 | `GET /sessions`、`POST /api/sessions/clear`（`identity.py:339-340`） | `AdminService.list_sessions/session_context/clear_session`（`admin.py:277,289,297`） | `/social/sessions` | **W5 新增** `GET /api/v1/social/sessions` | 只展示运行期会话（不含聊天正文，§25）；清理由旧页承担 |
| 社交空间 | `GET /sandbox/inspectors`（`:518`） | `SandboxRuntime.social_spaces`（`runtime.py:376`）+ `sandbox.social_space_map`（`config/settings.py:705`） | `/social` 空间区块 | **W5 新增** `GET /api/v1/social/spaces` | person ≠ space 显式说明（§21） |
| 记忆浏览 | `GET /memory`（`routes/memory.py:527`） | `MemoryAdminService.list_memories/search`（`services/memory.py:26,48`） | `/memory` | W2 `GET /api/v1/memories`（W5 扩展过滤/分页） | 加 `person/category/layer/status/limit/offset` 与 `total/next_cursor` |
| 记忆时间线 | `GET /memory/timeline`（`:530`） | `MemoryAdminService.timeline`（`:96`） | `/memory/timeline` | **W5 新增** `GET /api/v1/memories/timeline` | 迁移 |
| 记忆详情 | `GET /memory/detail/{id}`（`:538`） | `MemoryAdminService.detail`（`:102`） | `/memory/{id}` | W2 `GET /api/v1/memories/{id}` | 迁移 + provenance 渲染（W5 补 `memory_id` 别名） |
| 记忆健康 | `GET /memory/health`（`:531`） | `MemoryAdminService.health/embedding_status/consolidation_status`（`:190,210,223`） | `/memory/health` | W2 `GET /api/v1/memories/health` | 组装一屏（未知一律 `—`） |
| 记忆纠正 | `GET/POST /memory/correction`、`/apply`（`:540-542`） | `MemoryCorrectionService.*` | 暂不迁移（LLM 计划型操作，v1 先只读+单条编辑） | W5 新增 `POST /memories/{id}/edit` | 记录为 known limitation |
| 工具 | `GET /tools`、`/tools/{name}`、`/tools/*`（`routes/tools.py:344-350`） | `ToolAdminService.*`（`services/tools.py:36,45,75,82,93,96,133,169,211,219,222,227,239,243,248,251`） | `/abilities/tools` + `/{name}` | **W5 新增** 全部 `/api/v1/tools*` | 整域迁移（含权限/测试/缓存） |
| 贴纸 | `GET /stickers`、`POST /api/stickers/*`（`routes/media.py:168-170`） | `StickerAdminService.*`（`services/media.py:30,69,83,88,93`） | `/abilities/media` | **W5 新增** `/api/v1/stickers*` | 迁移；`preview_url` 恒 null（无静态路由，不伪造） |
| 口癖 | `GET /expressions`、toggle/delete（`routes/expressions.py:84-86`） | `ExpressionAdminService.*`（`services/expressions.py:33,36,47,50,53`） | `/abilities/media`（口癖区） | **W5 新增** `/api/v1/expressions*` | 迁移 |
| Agent | `GET /agent`、`/agent/tasks*`、`/agent/*`（`routes/agent.py:267-274`） | `AgentAdminService.*`（`services/agent.py:29,39,56,59,77,119,126,147`） | `/abilities/agent` + 任务详情 | **W5 新增** `/api/v1/agent*` | 真实 Agent Runtime（`app/agent/runtime.py:106-132`）→ 迁移（§42 边界） |
| 日志 | `GET /logs`（`routes/ops.py:225`） | `AdminService.logs`（`admin.py:375`）+ narration WS | `/system/logs` | W2 `/logs/tail`、`/logs/channels`（W5 扩展 channel/level/q） | 升级为 Debug Console（暂停/自动滚动/仅清视图） |
| Runtime | `GET /runtime`、`POST /api/runtime/{action}`（`ops.py:223-224`） | `AdminService.runtime_action`（`admin.py:404`）+ `RuntimeScheduler`（`runtime/scheduler.py:50,54,43-46,101`）+ `OneBotGateway`（`gateway.py:216-235,543-546`） | `/system/runtime` | W2 `/runtime*`（W5 扩展 process/onebot/lanes/hub） | 运维页 + 危险动作收进「高级操作」 |
| 凭据 | `GET /credentials`（`routes/media.py:171`） | `ToolAdminService.credentials/set_credential/delete_credential` | `/system/credentials`（W4 已建） | W2 `/api/v1/credentials*` | 已迁移（W4） |
| 配置 | `/config`、`/prompts` | `ConfigAdminService.*` | `/system/settings`（W4 已建） | W2/W4 `/api/v1/config*` | 已迁移（W4） |

---

## 2. 「已有 accessor 但 v1 未暴露」→ W5 补齐清单

| 数据 | 真实 accessor | W5 处理 |
|---|---|---|
| 需求全量（level/band/growth） | `NeedSystem.all`（`sandbox/needs.py:29`）、`bands`（`:74`）、`pressure`（`:84`） | 进 `needs_full[]` |
| 动作细节（definition/detail/reason/space） | `SandboxRuntime.current_action`（`runtime.py:406`）+ `actions.definition()`（`:3169`） | 进 `action{}` 扩展键 |
| 打断上下文 | `runtime._interrupted`（`:276`，`InterruptedActionContext` `models.py:227-243`） | 只读投影 `interrupted{}`／null |
| 社交会话 | `runtime._social_session`（`:316`）、`social_session_active()`（`:850`） | **最小新增** `runtime.social_session_snapshot()`（返回副本）→ `GET /social/sessions` |
| 空间/物件/库存/宠物 | `runtime.spaces/objects/inventories/pet_system`（`:356-363`） | 进 `world` 追加块 |
| 世界轨迹 | `sandbox.store.recent_events/timeline` | 新端点 `/world/timeline` |
| OneBot 队列/通道 | `gateway.pending_outbound()`（`:543`）、`busy()`（`:546`）、`_lanes`（`:221` 私有） | **最小新增** `OneBotGateway.stats()`（只读）→ `overview.runtime.onebot` |
| 进程运行时间 | `bot.started_at`（`core/bot.py:311`）、`Metrics.snapshot()` | 进 `runtime.process` |
| 记忆分页/总数 | `MemoryRepository.count`（`memory/repository.py:284`）、offset（`:243`） | 进 `/memories` 的 `total/next_cursor` |
| 贴纸资产元数据 | `StickerAsset`（`media/models.py:89-139`：usage_count/created_at/origin/safety_status） | 进 `/stickers` 行 |
| Agent 任务/轨迹 | `AgentRuntime` 持久化（`agent/runtime.py:473-670`） | 进 `/agent*` |

---

## 3. 刻意不迁移 / 已知限制

| 项 | 原因 |
|---|---|
| 角色导出/导入（`/character/export|import`） | §99：v1 不做配置/数据导出（Bible 与 Secret 泄露面）；旧入口保留在 `/legacy` |
| 记忆纠正（LLM 计划型） | W5 只提供按 id 的编辑/归档/删除；计划型纠正留给后续（记录为限制） |
| 行为设置页（`/sandbox/chat`、`/behavior/*`） | 属配置中心（W4 已覆盖回复延迟/分段/作息/主动），避免第二套入口 |
| 干跑模拟（`POST /sandbox/simulate`） | 备份/恢复型重操作，风险高，W5 不迁移（旧页仍在 `/legacy`） |
| 贴纸预览图 | 无静态文件路由；`preview_url` 恒 `null`（不伪造 URL） |
| 聊天正文 | 架构上运行期会话 ≠ 聊天数据库（§25），不展示 |
| 关系/承诺写入 | Core 无 Admin 写路径（§22/§96）→ 只读 |
| 角色头像 | 项目不存在 avatar/portrait 概念（审计 0 命中）→ 不伪造 |

---

## 4. 新增的运行时/会话只读访问器（唯一的两处 Core 改动）

| 文件 | 方法 | 语义 |
|---|---|---|
| `app/sandbox/runtime.py` | `social_session_snapshot()` | 返回会话状态**副本**（person/space/started/last_activity/turns/interrupted）或 `None`；不改任何状态 |
| `app/integrations/onebot/gateway.py` | `stats()` | 返回连接状态、计数器与每通道队列深度；纯读 |

其余一律复用既有 accessor；没有任何新的引擎、存储或第二套真相。
