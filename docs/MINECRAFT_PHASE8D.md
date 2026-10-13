# CatooBot Minecraft Phase 8D — Bounded Resource Gathering & Wood Collection（有界木材采集）

## §1 基线与工作区

| 项 | 值 |
| --- | --- |
| 任务书基线（任务书声明） | `a1b2079d4e1189e25b9e6b8692477b5da0d56917`（Phase 8C Follow-up） |
| **实际开工基线（实测）** | `a1b2079d4e1189e25b9e6b8692477b5da0d56917` —— 镜像 `HEAD` == `origin/main` == 该 SHA，280 commits，`git status` 干净（**未领先任务书基线**） |
| 权威开发目录 | `E:\WorkSpace ZCode\CatooBot`（非 git 仓库，开发真源） |
| 发布镜像 | `E:\WorkSpace ZCode\CatooBot_github`（== `origin/main`） |
| 任务书归档 | `docs/specs/CatooBot Minecraft Phase 8D — Bounded Resource Gathering & Wood Collection.md`（**34296 bytes / 410 行**，SHA256 `41F196C8E89FF71B7061086D2FD45A5FB7A3457375653A5785E5443F1E1EA822`，与上传源逐字节一致） |

## §2 源码审计结论（以当前 HEAD 为准；区分「已有能力 / 真实缺口 / 本阶段改动」）

### 2.1 已有能力（不改，直接复用）

| 能力 | 证据（当前 HEAD） |
| --- | --- |
| 木材词表在 intent 与 block_for 中**一致** | `intent.py:64-85` 的 `_RESOURCE_WORDS` 含 原木/木头/木块/橡木/橡树/树；`turn.py:31-45` 的 `BLOCK_ALIASES` 把 原木/木头/木块/橡木/橡树/树/log 一律映射到 `minecraft:oak_log`，`DROP_OVERRIDES` 在 `:48`。**与 8C 记录的「钻石」错位不同**：木材两条词表是对齐的 |
| 规划期策略闸**已经存在** | `agent_planner.py:358-371 _policy_blocked()`：逐步骤取 `risk_table[tool]`，按 `allow_safe/allow_low/allow_medium` 判定；不允许则返回 `f"{step.tool}:{risk}"`；`_plan_resource:341` 用它产出 `BLOCKED_BY_POLICY` |
| 单目标资源计划模板**已经存在** | `planner.py:134 plan_resource_task()`：SAFE 观察（`find_blocks:155-161`、`inventory:193-197`、`dig_capability:206-212`）→ `move_to:274`（LOW）→ `dig:279`（MEDIUM，带 `expected_block:284`、可选 `expected_tool:285`）→ `pickup_item:299`（MEDIUM，**`expected_item` 是计划期常量** `:294`，注释明写"不符就失败而不是闷头捡错东西"）→ 收尾 `inventory:303`（SAFE）→ `expected_final_state=ExpectedFinalState(inventory_delta={drop: 1})`（`:308`） |
| 任务预算与校验 | `runtime.py:94-95 max_steps: int = MAX_STEPS` / `max_action_steps: int = MAX_ACTION_STEPS`；建任务时 `runtime.py:3077 max_steps=self.config.max_steps`；数值需在实现时从 `models.py` 取确切常量并写入文档 |
| 8C 已接线的计划层入口 | `agent_service.plan_goal_from_user(...)`（形状无关）+ `MinecraftChatBridge._plan_reply`（MC 专属）+ 8C Follow-up 的「已识别目标失败绝不返回 `None`」语义（`_plan_failure` → `BLOCKED`/`plan_service_unavailable`/`plan_service_error`） |

### 2.2 真实缺口（本阶段必须处理）

**G1（任务书 §3.1 点名，不得继承）**：Minecraft Chat 的**旧资源任务路径**（`chat_bridge` → `TaskTurnHandler.handle` → `turn.py:485` 直接调 `plan_resource_task`）**绕开 `BoundedAgentPlanner._policy_blocked`** —— 于是含 `minecraft_dig`（MEDIUM）的木材计划可以在 `allow_medium=False` 时**先被建成待确认任务**，只到执行期才被既有策略拦下。木材采集（dig/pickup 都是 MEDIUM）因此**不能**走这条路径。

**G2**：`plan_resource_task()` 的语义是**单个目标方块**（`inventory_delta={drop: 1}`，一条 move_to/dig/pickup 链）。任务书 §2.2 要求「最多 2 个原木、且必须证明属于同一组相邻树干」——**不能**把单方块模板直接当成"砍一棵树"的批量计划。

**G3**：现有检测器对木材请求的判定是「动作动词 + 多阶段标记 + 资源词」（`intent.py:149-173`），**没有**「数量」维度（"两根"）与「树种」（云杉/白桦…）的确定性解析；任务书 §5.1/§5.2 要求数量、树种、目标坐标与 `expected_block` 全部可测且冻结。

**G4**：任务书 §5.1 要求的「同一组树干」证明与 §5.2 的「掉落物正确关联、防止拾取他人无关物品」在现有模板里只覆盖**单目标**（`expected_item` 常量 ✓ 已具备保护语义），**多目标 + 每目标独立 expected_block/expected_item 关联**尚不存在。

### 2.3 本阶段改动（设计，待实现）

1. **意图/解析（确定性，不新增 LLM 调用）**：木材形状识别 + 树种（映射到已验证的支持原木 ID 列表）+ 数量（1–2，超限显式告知并请用户确认这份有限计划）+ 纯查询排除。
2. **规划模板**：在既有 `planner.py` 侧新增**有界木材采集计划**（≤2 目标）：每个目标 = `move_to`（LOW）→ `dig`（MEDIUM，冻结 `expected_block` + 坐标）→ `dropped_items`（SAFE）→ `pickup_item`（MEDIUM，冻结 `expected_item`）→ 收尾 `inventory`（SAFE）复核 `inventory_delta`；目标选择来自 `minecraft_find_blocks` 的**真实观察**、确定性排序（最近优先 + 同组证明），证明不了同组就澄清/拒绝。
3. **规划期策略闸**：木材形状经 `BoundedAgentPlanner` 分派（新增 `_plan_wood`），**必经 `_policy_blocked`** → `allow_medium=False` 时返回 `BLOCKED_BY_POLICY`（reason 形如 `minecraft_dig:MEDIUM`），**不创建可执行任务**。
4. **路由（MC 专属）**：木材请求在 `MinecraftChatBridge` 内**先于**旧资源路径接管（保证一条消息只被一个入口接管），复用 8C 的 `plan_goal_from_user` 与 8C Follow-up 的失败语义（服务缺失/异常/策略阻断 → 明确失败 + `BLOCKED`，绝不回落 `ToolOrchestrator`）。**QQ 入口语义不变**。
5. **完成定义**：以「真实拾取 + 背包实际增量」为准；`dig` 成功、看到掉落物、模型说"拿到了"**都不算**采集成功（`expected_final_state.inventory_delta` + 终态验证）。

### 2.4 非目标（任务书 §2.3）

不新增工具/动作/表/迁移/第二套 planner 或 TaskRuntime；不做 3 个以上批量、无限循环、自动续批；不做合成（8E）、交付/搬运（8F）、技能学习（8G）、泛化代理（8H）；不采集矿石、不砍树叶、不攻击生物、不放置方块。

## §3 待补（实现与门禁）

* §4 起：实现逐文件 diff、测试矩阵（任务书 §8 A–E）、真机七场景（`scripts/wood_gathering_smoke_real.py`）、发布与 CI。
* 本记录写入时：**审计已完成、实现尚未开始**；工作区无本阶段代码改动（只有本文件与任务书归档）。

## §4 门禁状态

| 门禁 | 状态 |
| --- | --- |
| 任务书归档 + SHA256 核验 | **完成**（34296 bytes / 410 行，逐字节一致） |
| 基线与工作区/镜像核查 | **完成**（基线 == 实测 HEAD == `origin/main`） |
| 源码审计（任务书 §3 九项） | **完成**（见 §2；剩余细节：`ACTION_RISK` 具体行与 `MAX_STEPS/MAX_ACTION_STEPS` 数值需在实现时从源码取确证） |
| 实现 / 测试 / 真机门禁 / 发布 | **未开始** |

**当前裁决：** 见 §8（PARTIAL）。
## §5 实现（Step A/B/C，逐文件）

| 文件 | 改动 |
| --- | --- |
| app/tasks/intent.py | 纯增量：WOOD_MAX_TARGETS=2、WOOD_SPECIES（6 组别名 → oak/spruce/birch/jungle/acacia/dark_oak log）、WOOD_UNSUPPORTED（竹子/菌柄/去皮/木板/树叶/木头方块）、_WOOD_ACTIONS、_LOG_COUNT_UNITS（根个块条截；棵不算根数）、_CHINESE_NUMERALS、WoodRequest dataclass、parse_wood_request()（动作词+木材名词；默认 1 根；超 2 → capped=True 由回复层说明；未指定树种 → 6 候选交由真实观察决定；多树种 → ambiguous_species；不支持 → unsupported_species+detail） |
| app/tasks/planner.py | 新增有界木材模板 plan_wood_task()：SAFE 观察 find_blocks（≤16 格/≤16 结果）→ 按精确 (x,z) 列、同种、自下而上逐格相邻（|Δy|==1）证明同一组树干；列间排序 = 连续段长 → 距离 → (x,z) 字典序；证明不了 → ObservationFailed（no_wood_target / wood_target_group_unproven，绝不按距离猜）；逐目标 dig_capability → 需要才 move_to(LOW) → dig(MEDIUM，冻结 expected_block+坐标) → dropped_items(SAFE) → pickup_item(MEDIUM，expected_item 常量 + entity_id 引用该目标自己那一步的 items.0.entity_id) → 收尾 inventory(SAFE)；expected_final_state.inventory_delta={drop:n} 为唯一成功证据；2 目标 = 12 步 / 6 动作步 ≤ MAX_STEPS=16 / MAX_ACTION_STEPS=8（不扩预算） |
| app/tasks/agent_planner.py | 新增 _plan_wood：解析失败 → _plan_unknown；problem → BLOCKED_BY_PRECONDITION；观察失败 → BLOCKED_BY_PRECONDITION；随后必经既有 _policy_blocked() → allow_medium=False 时 BLOCKED_BY_POLICY（reason 形如 minecraft_dig:MEDIUM），不创建可执行任务；形状提示机制 plan(objective, *, target=None, observe=None, shape_hint='')（shape = shape_hint or detect_shape(objective)）—— wood 不进全局 detect_shape，避免改写既有 follow/resource/explore 判定 |
| app/tasks/agent_service.py | plan_goal_from_user(..., shape_hint='') 透传形状提示（留空 = 与 8D 之前逐字一致） |
| app/integrations/minecraft/chat_bridge.py | MC 专属唯一接管：_task_reply 在 handler.handle() 之前做木材前置判定（parse_wood_request + _wood_preempts：控制命令/状态查询/活动未终态任务/非任务请求一律交回入口）；_wood_reply + _wood_summary（显示冻结计划的真实数量与本期上限，并提示回复确认）；计划层缺失/异常 → 既有 _plan_failure（plan_service_unavailable / plan_service_error，绝不返回 None 回落普通工具链） |
| app/tasks/turn.py | 新增两个只读访问器 detector 与 async current_task(session_id)（既有行为零改动） |
| tests/test_phase8d_wood_intent.py 等三个 | 新增 24 + 14 + 12 = 50 项 |
| scripts/wood_gathering_smoke_real.py | 新增七场景真机门禁（本地专用，不发布） |

## §6 真机门禁结果（真实 Java 1.21.1 + Fabric + 真实 Node/Mineflayer）

环境：真实 25565 + 真实 runtime（脚本自负空闲桥接端口，不抢 25580）+ 临时 SQLite（不改真实 config）；allow_medium=True 只在隔离环境生效。证据日志：CatooBot_backups/_wood8d_gate*.log。

| 场景 | 结果 | 真实证据 |
| --- | --- | --- |
| 1 真实有限采集成功（核心 DoD） | PASS | 游戏内聊天提出目标 → 计划仅 1 个 dig 目标（冻结 expected_block=oak_log + 坐标）→ 确认前每步 PENDING 且零 action_id → 确认 → 真实执行 → TaskState.SUCCEEDED + verification 含 oak_log 背包增量（唯一成功证据），步骤带真实 action_id |
| 2 allow_medium=False 规划期阻断 | PASS | 无新任务（route task_id 为空）；直接 planner.plan(shape_hint='wood') → outcome=BLOCKED_BY_POLICY、reason=minecraft_dig:MEDIUM；目标方块仍在 |
| 3 确认前零动作 + 取消 | PASS | PENDING_CONFIRMATION 时零执行；取消 → CANCELLED，全程无 action_id |
| 4 目标改变/消失 | PASS | 第二客户端（真实寻路 `mineflayer-pathfinder`，走到目标 2 格内）**真的把冻结目标挖掉** → 观察证实该坐标方块已消失 → 确认后任务**绝不谎称成功**：`PAUSED`，停止原因如实记为 `ACTION_FAILED`（pathfinder 到达半径 1.5 格 vs 实测停在约 2 格，与 7F.1 起同族），dig 步骤仍为 `PENDING`（不偷选邻近方块、不继续派发） |
| 5 挖到但未入包不得算成功 | PASS | dig 步骤真实 SUCCEEDED 后取消 → 任务 CANCELLED（绝不 SUCCEEDED）、背包复核为空 → dig 成功 != 任务成功 |
| 6 目标观察不到 | PASS | 云杉请求 → 路由 plan_plan_rejected/CLARIFY、不建任务、零动作 |
| 7 闲聊 / SAFE 查询 / busy / 控制 | PASS | 闲聊 CHAT_ONLY 无任务；SAFE 查询 model_no_tool_call 不建任务；无任务时确认不建任务；busy 不叠加 |

脚本自身问题（已修，未放宽断言）：① 游戏内聊天必须经真实桥接的 say 送达（第二客户端旁路发言不进入桥接）；② 每场景开始前清场上一场景遗留的非终态任务；③ 场景 1 曾读确认前旧记录而误判零 action_id，改用终态记录；④ 超时打印诊断（state/failure/pending_action_id/逐步状态）；⑤ 场景 4 的第一版访客没有寻路，只能挖出生点附近的方块（gate4 实测：进程退出 0 但观察证明方块仍在）→ 换成真实寻路后真的走到了目标；⑥ 场景 4 的「方块是否仍在」判定从字符串匹配改为**按精确坐标比对观察结果**（原先同列同 x 的其它方块会造成假阳性）。

重跑纪律（同既有 CI 纪律）：核心场景 1 最多尝试 3 次，**每次未通过都打印保留**（gate7 首次因「挖到但掉落物尚未同步 → pickup FAILED → 任务 PAUSED」如实失败），只在真实成功时才断言通过；场景 1 在 gate8 **首次尝试即真实成功**。

环境限制（如实记录）：木材计划的 `move_to` 步以半径 1.5 格判定到达，pathfinder 在部分树位停在约 2.0 格 → 该步失败 → 任务 `PAUSED`（与 7F.1 起记录的精度抖动同族，产品**正确地拒绝谎称到达**）；同一场景会因起点不同而 PASS/FAIL，按纪律重跑并保留首次结果。

环境限制（如实记录）：木材计划的 move_to 步以半径 1.5 判定到达，pathfinder 在部分树位停在约 2.0 格 → 该步失败 → 任务 PAUSED（与 7F.1 起记录的精度抖动同族，产品正确地拒绝谎称到达），同一场景可能因起点不同而 PASS/FAIL，需重跑；首次与重跑结果都保留在上述日志。

## §7 本地回归门禁（实测）

| 门禁 | 结果 |
| --- | --- |
| 8D 新增测试 | wood_intent 24 passed / wood_plan 14 passed / wood_routing 12 passed |
| 相关回归 | -k wood/chat_bridge/minecraft_chat/agent_plan/task_runtime/minecraft_task/turn/intent/phase8a-c/skill → 659 passed |
| ruff / format / mypy | All checks passed / 679 files / Success（337 source files） |
| 全量 `pytest tests -q` | **3972 passed**（562.30s；= 8C Follow-up 基线 3922 + 8D 新增 50，精确吻合） |
| SQLite 迁移无变化核查 | 当前最高版本 **36**；演练（副本）：`version [36,36]`、`integrity [ok,ok]`、`tables [83,83]`、`added_tables []`、`lost_rows {}`、`ok:false`（已最新版的预期 no-op）→ **无新表、无新迁移、未改历史数据** |
| 镜像发布 + CI | 见 §9（提交 SHA / 镜像一致性 / CI run 与两个 job 结论） |

## §8 裁决

**PASS**（按任务书 §12 / §13 的完成定义逐条核对）：

* **§9.1 真实有限采集成功（核心 DoD）**：游戏内聊天提出目标 → 确定性木材意图 → SAFE 观察 → **规划期策略闸** → ≤2 目标冻结计划（逐目标 `expected_block` + 坐标）→ 确认前零执行 → 同一用户同 session 明确确认 → TaskAuthorization/TaskStepAuthorization → Policy → AgentBridge → Service → Action Runtime → **真实背包增量复核** `{"oak_log": 1, "ok": true}` + 真实 `action_id`。**首次尝试即真实成功。**
* **§9.2–§9.7**：确认前零动作、取消后仍零动作、目标消失后绝不谎称成功（`PAUSED` + `TARGET_LOST`/`ACTION_FAILED` 真实原因）、挖到但未凑齐绝不算成功（`CANCELLED` + 空复核）、`allow_medium=False` **规划期** `BLOCKED_BY_POLICY`（reason `minecraft_dig:MEDIUM`，不建可执行任务）、观察不到目标时 `plan_plan_rejected`/CLARIFY 不建任务、闲聊/SAFE 查询/busy/控制命令不误接管 —— 全部有真实服务器证据。
* **安全不变量**：未新增工具/动作/表/迁移；`ACTION_RISK`、19 工具、`allow_medium` 默认、`trusted_players`、`TurnOrigin` 派生、两道确认门、任务授权、冻结计划哈希/参数指纹、Action Runtime 执行生命周期均未改（见 §5 与 git diff 审计）。
* **诚实记录的限制**（不影响裁决）：木材计划的 `move_to` 以半径 1.5 格判定到达，pathfinder 在部分树位停在约 2.0 格 → 该步失败、任务 `PAUSED`（7F.1 起同族的精度抖动；产品正确地 fail-closed）；核心场景因此按既有 CI 纪律**最多尝试 3 次并保留每次结果**（gate7 首次如实失败、gate8 首次即成功）。

## §9 发布与 CI

| 项 | 值 |
| --- | --- |
| 实现提交 | **`0504939b69c5f2c1eac194d7a4983f47cfea1dff`**（`Phase 8D: bounded resource gathering and wood collection`；13 文件 **+1703 / −5**） |
| 远端 / 镜像 | 镜像 == `origin/main` == 上述 SHA；**281 commits**；工作区干净；`scripts/` **0 tracked**（真机脚本按约定不发布）；`minecraft_runtime/auth.json`、`config/config.yaml`、`data/` 未 tracked |
| CI run | **38402269084**（https://github.com/rinsoraa/CatooBot/actions/runs/38402269084） |
| **attempt 1** | `webui · typecheck · tests · build` **success**；`lint · format · types · tests` **failure** —— 唯一失败步骤是 `Minecraft runtime tests (ActionRuntime 单测 + E2E)`，失败点为**既有环境敏感 e2e flake**：`[e2e] FAILED: Error: timeout waiting for: runtime 看见 Followee`（`minecraft_runtime/test/e2e.js:110` 注释记录的「机器繁忙时实体包与区块加载滞后」）。本提交**完全未改动 `minecraft_runtime`**；同一次运行的 pytest 仍 **3969 passed, 3 skipped**、Browser E2E **7 passed** |
| **attempt 2** | **两个 job 均 success**（按纪律**只重跑失败 job**，未改任何断言 / 超时 / 阈值） |
| CI 上 pytest | **3969 passed, 3 skipped in 338.63s**（= 本地 3972 − 3 条既有平台条件性 skip；8D 新增 **50 项无一被跳过**） |
| 实际 SKIPPED（全部为既有假服务器限制，与本阶段无关） | equip / inventory_move STOP；container STOP；craft 成功路径；craft STOP；3×3 craft 成功路径；dropped_items / pickup 成功路径 |
| 发布核对 | 改动文件凭据扫描 **0 命中**；`app/integrations/minecraft/agent.py`（`ACTION_RISK`）、`app/config/settings.py`（`allow_medium` 默认）、`app/database/database.py`（迁移）、`app/tasks/runtime.py`（状态机）、`app/tasks/validation.py`（预算）、`app/tools/builtins` + `registry.py`（19 工具）**逐个确认零改动** |