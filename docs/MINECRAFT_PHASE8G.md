# Phase 8G — 工作台感知与受限合成扩展

> 归档任务书：`docs/specs/CatooBot Minecraft Phase 8G — Workbench-Aware Crafting & Bounded Crafting Expansion.md`
> （原文 22074 bytes，SHA256 `4185C808…E24F75D`，512 行；接收时逐字节归档核验）
> 开工基线：`7b16c7947ca5b10c0bd997481dd7b0f097b61d5e`（= Phase 8F 收尾，镜像 == `origin/main`，305 commits）

## 1. 目标与边界

| 目标 | 状态 |
| --- | --- |
| G1 工作台感知（六态：可用/未找到/不可达/过期/未知/错误） | 已实现（`app/tasks/workbench.py`） |
| G2 工作台合成（3×3，坐标冻结进计划，执行期实时复核） | 已实现（`minecraft_craft.crafting_table`） |
| G3 受限多步 DAG（深度 ≤4、合成步 ≤12、单目标 ≤16、总输入 ≤256） | 已实现（`app/tasks/craft_plan.py`） |
| G4 保留 8E 统一决策链（类型化决策 → 既有任务链） | 保持不变（`turn_decision`/`turn_context` 仅补事实与示例） |
| G5 安全闭环（冻结、授权、逐步验证、取消、异常） | 已实现 + 门禁覆盖 |

**明确不实现**（本阶段一字未改）：自动放置/破坏方块、自动挖掘/采集新矿物、拾取、容器操作、
丢弃物品腾空间、玩家物品交付、战斗、新任务状态机、`ACTION_RISK`/`TOOL_ACTION` 映射、
`allow_medium` 默认值、两道确认门、授权规则、数据库 schema 与迁移。

## 2. 代码清单

新增：

* `app/tasks/craft_budget.py` —— 合成预算唯一来源：`CraftBudget(max_depth=4, max_craft_steps=12,
  max_per_item=16, max_total_inputs=256, workbench_search_radius=16, workbench_ttl_seconds=30.0,
  allow_approach=True, max_target_batches=4)`；`effective_max_craft_steps = min(max_craft_steps,
  MAX_ACTION_STEPS - (1 if allow_approach else 0)) = 7`；`effective_max_steps = MAX_STEPS - 1 = 15`。
  预留那一格是因为 `validation.validate_plan` 把 **LOW/MEDIUM/HIGH/DESTRUCTIVE** 都算动作步
  （上限 8），工作台的接近步也占一格 —— 宁可少做一刀，也不能让到位+合成超出执行器上限。
* `app/tasks/workbench.py` —— 只读工作台观察。六态 + `WorkbenchObservation.to_payload()/explain()`；
  距离口径与 runtime 一致（眼高 1.62 → 方块中心，`interaction_distance`）；
  `WORKBENCH_MAX_DISTANCE = 5.0` 与 runtime 的 `MC_CRAFTING_TABLE_MAX_DISTANCE` 同值。
* `app/tasks/craft_plan.py` —— 受限多步 DAG 的**纯**构建与库存模拟（无 I/O）：
  深度优先需求求解、同名需求合并、`visiting` 成环检测、四道预算硬检查、
  相对初始库存的**净值**（`inventory_delta`/`inventory_consumed`）、`capped`/`planned_amount`。

改动（要点）：

* `app/tasks/models.py`：`TaskStep.expected_state`（空则**不进** payload）；`TaskPlan.hash_payload()`
  在存在逐步后置条件时把它纳入 `plan_hash`（改它必须重新确认）。
* `app/tasks/runtime.py`：`_ensure_steps_verified` / `_verify_step_state` / `_fail_step_verification` /
  `_step_needs_baseline`；钩子在 `drive()` 派发前与 `_finish()` 开头。
* `app/tasks/planner.py`：`_CraftRecipe.requires_table`；`craft_recipe_of(..., allow_table=)`
  （优先真实 `available` → 非工作台 → `recipe_id`）；`plan_craft_task` 重写为工作台感知 + DAG，
  含显式接近步、逐步 `expected_state`、`depends_on` 与被消耗物的净值；新增 `craft_plan_summary`。
* `app/tasks/agent_planner.py`：`_plan_craft` 的 checks 里带上 `planned_amount/depth/craft_steps/
  requires_table/workbench/nodes`（全部来自计划里记录的观察）。
* `app/integrations/minecraft/chat_bridge.py`：合成摘要按 `planned_amount` 披露夹取 + 工作台状态；
  新增 `_craft_failure_hint` 把工作台/材料/数量/配方原因码翻译成玩家能懂的话。
* `app/tasks/craft_intent.py`：补基础 3×3 物品的路由识别（工作台/木镐/木斧/木锹/木锄/木剑/箱子/梯子）；
  新增 `_STATION_ITEMS`（工作台是**工位**不是材料）。
* `app/tasks/turn_context.py` / `turn_decision.py`：能力事实写明「合成=受管任务（2×2 与工作台 3×3，
  工作台必须已存在且可达）」，并补「世界/位置类问句用 query」的示例。

## 3. 关键契约

**工作台六态**（`WorkbenchStatus`）：`AVAILABLE` / `NOT_FOUND` / `UNREACHABLE` / `STALE` / `UNKNOWN` /
`ERROR`。只有 `AVAILABLE` 能用于执行；`UNREACHABLE` 且原因恰为 `workbench_too_far` 且
`allow_approach` 时才允许**一个显式** `minecraft_move_to`（LOW，到达半径 2.0）；其余一律如实拒绝，
响应里带真实状态与原因码（`crafting_table_required:<status>:<reason>:<explain>`）。
产品**绝不**放置、破坏、挖掘或搬动工作台。

**逐步后置验证**：带库存预期的步骤在派发**之前**记录真实背包基线（`inventory_baselines`），
派发下一步之前用**新鲜真实读取**验证（`step_verifications`，同时写进 `step.result`），
失败即任务 `FAILED(VERIFICATION)`、**不自动重试**（避免重复制作）、后续步骤一步不派发；
缺基线或前置步未成功 → fail-closed；无逐步后置条件的旧计划完全不进这段逻辑。

**数量语义**：请求数超过预算时**夹取 + 明确披露**（`capped`/`planned_amount`），绝不静默少做；
DAG 总刀数超过动作步预算则**明确拒绝**（`step_budget_exceeded`）。
## 4. 真实 Provider 语义验证（§10.1）

样本与标签在**看到任何模型输出之前**写定（`CatooBot_backups/_phase8g_craft_eval.py`，28 条、14 类，
独立于 FU4 的 42 条与 8F 的 16 条）；只调用生产 `TurnDecisionInterpreter`；运行后标签未改。

| 轮次 | 准确率 | 判决成功率 | 平均/P95 延迟 | 重试>1 | 违禁能力命中 |
| --- | --- | --- | --- | --- | --- |
| 第 1 轮（原始提示词） | 26/28 = 0.9286 | 1.0 | 3700 / 4684 ms | 0 | 0 |
| 第 2 轮（补「世界/位置问句 → query」示例后） | **27/28 = 0.9643** | 1.0 | 3713 / 4598 ms | 0 | 0 |

逐类（第 2 轮）：query 4/4、clarify 4/4、unsupported 4/4、atomic_action 2/2、task_proposal 9/10、
chat 1/1、narration 1/1、stop 1/1、cancel 1/1。

唯一不符项是**标签本身可争议**的一条：「没有工作台也能做木镐吗」我预冻结为
`task_proposal`（可接受 `clarify`），模型判为 `query` + `minecraft_world` —— 这是一句**问句**，
判成只读查询同样安全且更贴切。标签未改，两种口径一并披露：
严格计 **27/28 = 0.9643**；若把该条按「问句→query 也合规」计则为 28/28。
**违禁能力命中 0**（`minecraft_craft` 不在 FU4 自然语言白名单里，模型一次都没输出）。

## 5. 真实服务器门禁（§10.2，脚本 `scripts/phase8g_craft_smoke_real.py`，本地专用不发布）

环境：真实 Java 1.21.1 服务器（`D:\Minecraft server\1.21.1`，`online-mode=false`）+ 真实 CatooBot
Minecraft Runtime（真实 Mineflayer 客户端，用户名 `Catodayo`）+ 真实 Provider（模型 `primary`）。

| 场景 | 结论 | 关键证据 |
| --- | --- | --- |
| 1 世界查询（有效参数） | PASS | 真实执行 `minecraft_find_blocks` 并取回真实方块列表 |
| 2 真实背包与配方读取 | PASS | 逐条打印真实 inventory 与真实 recipe（含 `requires_table`） |
| B 需要工作台但附近没有 | PASS | 真实半径 16 内 0 个工作台；路由 `plan_plan_rejected`、零任务、零世界修改 |
| C 多步/单步合成闭环 | PASS | 冻结真实 `recipe_id` → 确认门 → `minecraft_craft` 真实执行 → 独立重读背包核对增减 |
| D 取消（确认阶段 + 执行中） | PASS | D1 确认阶段取消：CANCELLED、零合成、零改动；D2 执行中取消：完成 0–1/2 步、不再合成 |
| E 材料被外部改变 | PASS | op 客户端 `/clear` 清空木板（真实外部改动）→ 任务 `PAUSED`/`ACTION_FAILED`「材料不够」，不宣告成功、不重试 |
| A 真实工作台合成（3×3） | **BLOCKED（环境）** | 世界里没有工作台；op 客户端 `/setblock` 6 次均被服务器拒绝（真实回执 `Could not set the block`），真实 `minecraft_find_blocks` 半径 16 内 0 候选；产品按设计 fail-closed |

门禁自身需要的环境准备**如实标注**：`/give Catodayo oak_log N` 补材料、`/setblock` 放夹具工作台、
`/clear` 制造外部改动 —— 这些都由 **op 客户端**执行，产品自身不做任何放置/破坏/清空。

因此：**真实服务器闭环未完整达成**（缺「真实工作台单步合成」这一条），§10.2 不得记 PASS。
两处独立验证的分工也如实区分：**真实 Provider 语义验证已通过**（§4），**真实服务器合成闭环 PARTIAL**。

## 6. 门禁期间发现并修复的真实缺陷

1. **工作台自身坐标读不出来（会 100% 失效）**：真实世界视图的语义键是 `self`（pydantic 字段
   `self_state` 的 alias），我原先按 `self_state` 读 → 真机上永远 `missing_self_position`。
   已改为优先 `self`、兼容 `self_state`（单测夹具同步改成真实键 + 一条兼容用例）。
2. **基础 3×3 物品被路由到木材解释器**：「在工作台上做一把木镐」原先落入木材意图并报
   `wood_intent_schema_error`；`CRAFT_TARGETS` 补齐基础木制/3×3 物品后走合成路由。
3. **工位被当成材料**：「在工作台上做木镐」把 `crafting_table` 记成材料；新增 `_STATION_ITEMS`。
4. **DAG 库存模拟少扣一笔**：同一计划内被后续步骤消耗的产物没有从模拟库存扣除，
   导致计划级净增算多（例：1 原木→4 木板→2 木板做木棍，正确净增是「木板 +2」而不是 +4）。
5. **`plan_hash` 未覆盖逐步后置条件**：改逐步预期不需要重新确认（授权缺口）。
   现在存在逐步后置条件时进 hash；没有它的旧计划哈希与序列化逐字节不变。
6. **需要工作台的配方原先一律拒绝**（8F 语义）→ 现在「有可用工作台就能做，否则按六态如实拒绝」，
   并保留 `crafting_table_required` 原因码前缀。

## 7. 测试与静态检查

* `pytest tests -q` → **4308 passed**（8F 收尾 4253 → 新增 55 条：工作台 17、DAG 17、工作台规划 10、
  执行/取消/兼容 6、合成意图与路由新增 5）。无新增 skip、无删断言、无放松阈值。
* `ruff check .` → 通过；`ruff format --check .` → 通过；`mypy app` → Success（344 源文件）。
* 数据库：本阶段**不新增表/字段/迁移**（`SqliteTaskStore` 把整条记录写成一个 JSON `payload` 列，
  新增可选字段天然兼容）；迁移演练按 §12 复核为 no-op。
* 受保护文件审计：`app/integrations/minecraft/agent.py`（`ACTION_RISK`/`TOOL_ACTION`）**0 行改动**；
  `config/settings.py`、`app/tools/policy.py`、`config/config.yaml` 未触碰。

## 8. 未验证与遗留

* **真实工作台合成闭环**：见 §5 场景 A（BLOCKED）。首次闭环需要在世界里先有一个可达的工作台。
* 3×3 配方只覆盖「基础合成」：不自动备料、不连锁找替代配方、不做跨维度。
* 工作台定位依赖 runtime 的实时方块视图：门禁里出现过「服务器已确认方块改变、
  `minecraft_find_blocks` 仍报 0 候选」的环境现象（产品因此 fail-closed，未伪造可用工作台）——
  这是运行环境的观察延迟问题，值得在下一阶段单独复现与定性。
* 未实现（延续 8F/8G 边界）：需要容器的配方链、批量并发合成、合成中途重规划。

## 9. 裁决

**PARTIAL**。理由（按 §14/§15 如实列）：

* 已达成：G1–G5 全部落地；统一决策链未改；授权/确认/Policy/执行链全部沿用；
  真实 Provider 语义验证 27/28（违禁能力 0）；真实服务器 7/8 场景 PASS（含执行中取消与
  外部状态改变的真实 fail-closed 证据）；全量测试与静态检查通过；无新增表/迁移；受保护文件未改。
* 未达成：**真实工作台（3×3）单步合成的真实服务器闭环缺证据**（§5 场景 A BLOCKED）；
  据此不得记 PASS，也不得用「工作台相关的单测/规划测试通过」替代真实闭环。
