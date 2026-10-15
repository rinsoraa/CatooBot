# CatooBot Minecraft Phase 8F — 基础背包管理与简易合成闭环

## §1 基线与工作区（Step 0 实测）

| 项 | 值 |
| --- | --- |
| 任务书基线 | `bc0dc12bfbdf66cdd67b65fcd32618e2432ff9ae` |
| 实测镜像 HEAD == origin/main | **是**，`bc0dc12bfbdf66cdd67b65fcd32618e2432ff9ae`（302 commits，`git status --porcelain` 为空） |
| 权威开发目录 | `E:\WorkSpace ZCode\CatooBot`（非 git 仓库；密钥只在这里） |
| 发布镜像 | `E:\WorkSpace ZCode\CatooBot_github`（`scripts/` 永不发布） |
| 任务书归档 | `docs/specs/CatooBot Minecraft Phase 8F — Basic Inventory & Crafting Workflow.md`（23038 bytes，SHA256 `523ED817D57577E3CE3AFF2F75259699EEB3541D16EE2286BC4426DF46D6AF13`，逐字节一致） |
| 历史保护 | 未 reset / 未 force-push / 未改写 8A–8E 历史 |

## §2 Step 0 只读审计（真实代码证据）

| 关注点 | 实测结论 |
| --- | --- |
| 合成工具 | **已存在**：`app/tools/builtins/minecraft_craft.py:93` `MinecraftCraftTool`；注册于 `app/tools/runtime.py:143` |
| 合成参数契约 | `recipe_id`（必填，形状 `[!]结果名*每刀产出=材料*数量[+…]`，由配方查询产出）+ 可选 `crafting_table{x,y,z}`；**一次一个配方**（不批量、不做配方链、不自动备料/放工作台） |
| 配方查询 | `app/tools/builtins/minecraft_recipe.py:92`（SAFE）；`service.recipe_lookup` 同步返回 `recipe_id/result/requires_table/available/ingredients` |
| 背包读取 | `app/tools/builtins/minecraft_inventory.py:60`（SAFE）；结果形状 `{"items":[{"name","count"}…]}`（与既有验证器 `_counts` 同口径） |
| 风险/动作映射 | `ACTION_RISK`：`minecraft_recipe_lookup="SAFE"`、`minecraft_craft="MEDIUM"`、`minecraft_inventory="SAFE"`；`TOOL_ACTION`：`recipe_lookup`/`craft` |
| 执行语义 | `service.craft`（`service.py:1881`）启动即 `RUNNING`，终态经事件带 `result` 的 before/after 背包快照；执行前重验工作台（missing/invalid/too_far） |
| 任务适配器 | `app/integrations/minecraft/task_adapter.py` 的 `_SERVICE_ROUTES` **原本缺** `minecraft_recipe_lookup` 与 `minecraft_craft` → 合成链路根本不通（§8 缺陷 ①） |
| 规划层 | `app/tasks/planner.py` 的 `SAFE_OBSERVATION_TOOLS` **已含** `minecraft_inventory`/`minecraft_recipe_lookup`（无需新增观察能力）；`plan_wood_task` 是有界计划蓝本 |
| 后置验证 | `TaskRuntime._verify_final_state`(2746) → `_verify_inventory`(2870)：真实重读背包，**只**校验最小增量 → 无法表达材料消耗（§8 需最小扩展） |
| 能力目录 | `app/tasks/capabilities.py` 已把「合成/制作」映射到 `("minecraft_recipe_lookup","minecraft_craft")` 并写明限制 |
| FU4 白名单关系 | `turn_context.UNSUPPORTED_CAPABILITIES` 原本含 `minecraft_craft`（§5.3 事实）；`AUDITED_CAPABILITIES` = 只读 ∪ 单步动作，**始终不含**合成 |
| 任务层缺口 | **没有**任何合成计划/形状/意图解析器（本阶段新增） |

## §3 设计（合成 = 受管任务，不是自由能力调用）

```text
Minecraft 游戏内自然语言（真实客户端 → 服务器 → runtime → 回调 → 真实 MinecraftChatBridge）
  → TurnDecisionInterpreter（真实 Provider，严格 JSON + 严格校验）
      kind=task_proposal（合成**不进**自由能力白名单：模型永远不能点名 minecraft_craft 做原子调用）
  → chat_bridge TASK_PROPOSAL 分支（**合成解析先于木材解析**）
      ├─ 否定/「0 个」/未知物品/树种歧义 → 澄清或明确不做（零计划、零任务、零工具）
      └─ 肯定请求 → AgentPlanService.plan_goal_from_user(shape_hint="craft")
           → BoundedAgentPlanner._plan_craft
                ├─ 真实读背包（minecraft_inventory）—— 读不到就 fail-closed
                ├─ 真实查配方（minecraft_recipe_lookup）—— 绝不猜配方/材料/产出量
                ├─ 需要工作台的配方 → 如实拒绝（本阶段不做方块放置）
                ├─ 材料不足 → `insufficient_material:名称(需要 X，现有 Y)`，绝不自动补料/丢物品/开容器
                ├─ batches = ceil(N/q)（单目标上限 CRAFT_MAX_BATCHES=4），最多两级（原木→木板→木棍）
                └─ 冻结计划：每刀一个 minecraft_craft(recipe_id) + 末尾 minecraft_inventory
                     expected_final_state = {inventory_delta: 产物净增, inventory_consumed: 材料净耗}
                → 规划期即过 Policy 闸（allow_medium=false → BLOCKED_BY_POLICY，零任务）
  → 既有确认门（MEDIUM 计划 + 两道授权）→ 既有 Action Runtime → 真实服务器执行
  → 独立后置验证：**真实重读**背包，产物净增 ≥ 期望 且 材料净耗 ≥ 期望（同一份前后快照）
```

边界（都在代码里、不靠注释承诺）：一次只冻结一个目标；只做**自身 2×2** 的配方；
配方 id 只来自真实查询；同一物品既是材料又是产物时只写**净变化**；部分完成如实报告；
工具自称成功但背包无相符增量 → 不宣告成功（且**不重试**，避免重复制作）。

## §4 实现改动（13 文件，+1682 / −7）

| 文件 | 增删 | 说明 |
| --- | --- | --- |
| `app/tasks/craft_intent.py` | +290（新） | 确定性合成意图解析（动词之后=目标、之前=材料；5 类稳定原因码） |
| `app/tasks/planner.py` | +262 | `plan_craft_task()` + `CRAFT_MAX_BATCHES` + 真实配方选择（优先 `available`） |
| `app/tasks/models.py` | +24 / −1 | `ExpectedFinalState.inventory_consumed`（空则不进 payload，plan_hash 兼容） |
| `app/tasks/runtime.py` | +16 / −2 | `_verify_inventory` 同时校验产物净增与材料净耗 |
| `app/tasks/agent_planner.py` | +76 | `craft` 形状 + 规划期 Policy 闸 + checks 证据 |
| `app/integrations/minecraft/chat_bridge.py` | +127 | 合成先于木材解析、合成回话（数量取自冻结计划） |
| `app/integrations/minecraft/task_adapter.py` | +12 | 接入 `minecraft_recipe_lookup`（观察）与 `minecraft_craft`（执行步） |
| `app/tasks/turn_decision.py` | +9 / −2 | 合成属于 `task_proposal`；带参能力参数名契约 |
| `app/tasks/turn_context.py` | +6 / −2 | `minecraft_craft` 移出「未开放」事实清单（白名单仍未开） |
| `tests/test_phase8f_craft_intent.py` | +117（新） | 18 条：解析与安全边界 |
| `tests/test_phase8f_craft_plan.py` | +255（新） | 14 条：计划、批次、材料、策略闸、后置条件兼容 |
| `tests/test_phase8f_craft_execution.py` | +300（新） | 7 条：执行闭环与真实哨兵（工具轨迹/任务状态/真实背包） |
| `tests/test_phase8f_craft_routing.py` | +188（新） | 7 条：聊天桥路由与零副作用 |

## §5 确定性测试与静态检查

| 项 | 结果 |
| --- | --- |
| 本阶段新增测试 | **46 条**（4 个文件，全绿） |
| 全量 `pytest tests -q` | **4253 passed**（823.86s）（8F 前基线 4207 passed） |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 全部已格式化 |
| `mypy app` | Success（341 source files；8F 前 340） |
| 无解释的新增 skip | 无 |

覆盖（§9.1 十三项 / §9.2 十一项）：明确目标与数量、缺参默认/澄清、未知物品、无配方、材料不足、工作台要求、
背包读取失败、世界服务离线、非法决策 JSON、未注册/不允许能力、伪造授权字段、数量越界、模型额外操作；
计划生成后确认前零世界修改、未批准无可执行动作、真实注册能力、每步配方与参数、执行后真实读库存、
产物增量、材料消耗、数量错误失败关闭、工具成功但库存未变不宣告成功、部分完成如实报告、取消与异常后不重复执行。

## §6 真实 Provider 语义验证（新增 16 条**独立**预冻结样本）

样本与标签在运行前写定于 `CatooBot_backups/_phase8f_craft_eval.py`（**不修改** FU4 的 42 样本集）；
只调生产 `TurnDecisionInterpreter`（真实 Provider `primary` + 真实严格校验）：

| 轮次 | 改动 | 正确率 | 关键结论 |
| --- | --- | --- | --- |
| 1 | — | 0.5625（9/16） | **4 个明确合成请求全被答成 `unsupported`** —— 事实清单未随能力更新（缺陷 ④） |
| 2 | 事实清单 + 提示词：合成属 `task_proposal` | 0.8125（13/16） | 4/4 合成请求识别为 `task_proposal`；配方/库存问题仍被答成 `chat` |
| 3 | 补「配方/库存问题用 `query`」契约 | **0.875（14/16）** | query 4/4、clarify 2/2、task_proposal 4/4、stop 1/1、chat 1/1、narration 1/1；**违禁能力命中 0** |

第 3 轮逐类：chat 1/1、clarify 2/2、narration 1/1、query 4/4、stop 1/1、task_proposal 4/4、unsupported 1/3。
剩余 2 条不符项**不变更标签**、如实记录：

* 「帮我做一把钻石镐」「帮我合成 64 个钻石块」被判为 `task_proposal`（主标签是 `unsupported`/`clarify`）。
  它们随后由**计划层**用真实配方数据拒绝（本阶段只有 2×2，且材料不可得），因此属于「路由更宽松但链路仍然 fail-closed」；
  严格按预冻结标签计为不符，**不追溯放宽 accept 集合**。

## §7 真实服务器合成闭环（§10.2）

门禁脚本 `scripts/phase8f_craft_smoke_real.py`（**本地专用，不发布**；真实 Java Fabric 1.21.1 + 真实 runtime + 真实 Provider +
真实第二个 Mineflayer 客户端；`allow_medium=True` 只在本进程隔离配置里打开，`config/config.yaml` 未改）。

迭代 1–6 的真实缺陷与修复见 §8；第 7 轮 **3/3 PASS、断言失败总数=0**（`_phase8f_gate7.log`）：

| 证据 | 实测值 |
| --- | --- |
| 场景 1：有效参数世界查询 | 真实执行 `minecraft_find_blocks`，`status=ok`，摘要「附近找到 8 个：oak_log（(-291,63,-90)）…」；独立再读返回 8 个匹配方块；零世界修改工具 |
| 场景 2：真实背包/配方 | 背包 `{oak_log:13, oak_planks:4, stick:2}`；真实配方 `oak_planks*4=oak_log*1`（`requires_table=false`，每刀 4 个，耗 1 原木） |
| 场景 3：请求来源 | **真实第二个 Mineflayer 客户端**在游戏内发言（`sent=True`，任务归属 `user=IdCraft8F`、`session=minecraft:127.0.0.1:25565:IdCraft8F`，无回退） |
| 场景 3：决策与计划 | 真实 Provider `kind=task_proposal` → 路由 `plan_created/TASK` → 任务 `task_a16aacadb8df`；冻结配方 `stick*4=oak_planks*2`（来自真实查询，**未硬编码**） |
| 场景 3：后置条件 | `inventory_delta={stick:4}`、`inventory_consumed={oak_planks:2}` |
| 场景 3：确认前 | `PENDING_CONFIRMATION`；**零 action_id、零世界修改**（traces=[]） |
| 场景 3：执行 | 既有确认门（归属校验）→ `step_1 minecraft_craft state=SUCCEEDED action_id=act_mv964gtx_b` → `step_2 minecraft_inventory` |
| 场景 3：独立库存验证 | 执行后真实背包 `{oak_log:13, oak_planks:2, stick:6}` → 木棍 **+4**（期望 4）、木板 **−2**（期望 2） |
| 场景 3：运行时后置验证 | `{"checked":true,"ok":true,"inventory_delta":{"stick":4},"inventory_consumed":{"oak_planks":2},"expected":{...},"expected_consumed":{...}}` |

另有一条更早的成功闭环（门禁第 4 轮，请求经真实聊天桥事件注入）：`task_d6f8de66031b` / `action_id=act_mv95ofrl_b`，
`oak_log 14→13`、`oak_planks 0→4`，同样通过独立库存验证。

## §8 门禁期间发现并修复的 4 个真实缺陷

| # | 现象（真实门禁证据） | 根因 | 修复 |
| --- | --- | --- | --- |
| ① | `planning_failed:KeyError: 没有为 minecraft_recipe_lookup 注册服务调用` | `task_adapter._SERVICE_ROUTES` 未含配方查询与合成 | 接入 `minecraft_recipe_lookup`（SAFE 观察）与 `minecraft_craft`（MEDIUM 执行步）；**不**改风险/授权/确认语义 |
| ② | `insufficient_material:bamboo(需要 2，现有 0)`（做木棍） | 多种配方时按 `recipe_id` **字母序**挑，选中了竹子配方 | 改为优先真实 `available`（运行时按**当前真实背包**判定），再按 id 排序 |
| ③ | 「用橡木木板做一根木棍」被判 `no_plan_template`（歧义） | 材料名本身也是可合成物品 → 目标歧义 | 目标取**合成动词之后**最近者；动词之前的命中视为**材料** |
| ④ | 4/4 明确合成请求被真实模型答成 `unsupported` | 决策事实清单/提示词仍把合成列为「未开放」 | 合成作为**受管任务**告知模型；`minecraft_craft` 移出未开放清单，**仍未**进自由能力白名单 |

另外修复了门禁自身的一处证据取法错误（工具轨迹 `ToolTrace` 只存 `status`/`result_summary`，不含结果载荷）。

## §9 受保护不变量核验（任务书 §6 清单）

| 不变量 | 核验 |
| --- | --- |
| `ACTION_RISK` 及映射 | **未改**（`minecraft_craft` 仍是 MEDIUM、`minecraft_recipe_lookup` 仍是 SAFE） |
| `allow_medium` 默认值 | **未改**（`config/config.yaml` 未动；真机门禁只在隔离进程内显式打开） |
| ToolPolicy / 两道确认门 | **未改**；合成计划必须过计划确认门，动作仍走 Action Runtime 与授权 |
| `TaskAuthorization` / `TaskStepAuthorization` | **未改**；真机门禁实际触发了「确认必须来自发起任务的人与会话」校验 |
| 身份校验 / 预算 / 距离 / 时间 | **未改** |
| 冻结计划与 `plan_hash` | 既有计划**逐字节不变**（`inventory_consumed` 空则不进 payload，有回归测试） |
| Action Runtime 生命周期 / 取消与停止语义 | **未改**；执行仍经既有工具链（`action_id` 由 Runtime 分配） |
| 真实库存验证 | **扩展而非替换**：新增材料净耗校验，产物净增语义不变 |
| FU4 决策契约与上下文可信度 | 九类 kind、严格校验、白名单机制、上下文来源与状态分类**逐字未改**；只更新「合成已作为任务开放」这一事实，并补充带参能力的**参数名契约** |
| 数据库 schema 与迁移 | **未改**（本阶段无迁移） |

## §10 未验证事项与下一阶段建议

* **工作台合成未开放**：需要 3×3 的配方一律如实拒绝（本阶段不做方块放置/寻找工作台）；下一阶段若要开放，需先解决工作台定位与「不自动放置」的边界。
* **合成链深度上限 2 级**：更长的配方链（木板→木棍→工具）不会自动展开；超出即如实拒绝。
* **单目标/单物品**：一次任务只冻结一个目标物品与数量；多目标请求澄清。
* **真实 Provider 评测的 2 条不符项**：见 §6（钻石镐/钻石块被判 `task_proposal`，随后由计划层用真实配方拒绝），标签未改。
* **世界查询的表述依赖**：有效参数（`block_names`）由真实模型给出；提示词已写明参数名契约，但换一种说法仍可能触发工具的 fail-closed 拒绝（那是安全行为，不是缺陷）。