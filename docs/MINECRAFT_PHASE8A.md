# CatooBot Minecraft Phase 8A — Natural-Language Action Routing & Traceability

自然语言行动路由与决策可观测性。**本阶段只解决“请求如何被路由、以及如何看到路由结果”**，
不新增任何 Minecraft 能力、不扩大授权范围、不重写 TaskRuntime。

## §1 基线与工作区

| 项 | 值 |
| --- | --- |
| 权威开发目录 | `E:\WorkSpace ZCode\CatooBot`（唯一开发真源，非 git 仓库） |
| 发布镜像 | `E:\WorkSpace ZCode\CatooBot_github`（`main`） |
| 起始 SHA | `d7258901a64c2f5828e86a629f90915b3606e5db`（270 commits，工作区干净） |
| 任务书 | `docs/specs/CatooBot Minecraft Phase 8A — Natural-Language Action Routing & Traceability.md`
|  | （18216 bytes / 266 行，SHA256 `92397E770F8C041F87E29C30B71285A0F27C69ED3CBFB6C67BBCEB1AAF4F9949`，与上传源逐字节一致） |

## §2 原始症状与可复现证据（任务书 §3 逐条取证）

现象：游戏里说一句明确的请求（例如“看看附近有什么方块”），角色有时“只聊天、不行动”。
任务书禁止把它笼统归为“LLM 没听懂”，要求逐层取证。**实际运行时配置**（用应用自身 `load_config()`：
`.env` → `config/config.yaml` → WebUI `config/overrides.yaml` → 环境变量）为：

```text
tools.enabled=True            tools.decision_mode=json
tools.candidate_tools=5       tools.max_calls_per_turn=3
agent.enabled=True            agent.autonomy=normal
agent.mode.simple/tool_assisted/multi_step=True
minecraft.enabled=True        minecraft.auto_start_runtime=True
minecraft.agent.trusted_players=[]（**空**）  minecraft.agent.tools.allow_low=True  allow_medium=True
overrides.yaml 存在（bot/logging/permissions/memory/ai.models/agent.planner.model=primary/...）
```

即：**没有**任何关闭 tools/agent 的覆盖项 → “工具没开”这条原因被排除；`trusted_players` 为空意味着
游戏内玩家的 LOW 动作对**所有人**都应按“不可信”拒绝（这是既有安全语义，本阶段未改）。

逐条排查结论（与理论风险分开）：

| 可能原因 | 证据 | 结论 |
| --- | --- | --- |
| 聊天没到桥 / 自我过滤 / 忙碌上限 | `chat_bridge.apply_event` 四条丢弃分支 | 已覆盖；本阶段补了**结构化记录**（原先零日志） |
| 任务侧截获 | `TaskTurnHandler.handle` + `TaskIntentDetector.detect` | 正常路径，非缺口 |
| `_try_agent` 归为普通聊天 | `TaskClassifier.classify` 仅 `multi_step` 接管 | 正常路径，非缺口 |
| tools runtime 未启用 | 见上表配置 | **被排除** |
| 候选检索选不出工具 | `registry.candidates` 词法打分 `min_score=0.08` | 关键词已覆盖 §4.2 说法 → 非缺口 |
| 模型没选工具 / 参数不合法 | `_extract_call` + 参数 schema 校验 | 真实存在，本阶段做成**可区分原因码** |
| 策略/身份/确认门拒绝 | `MinecraftAgentBridge.check` 固定顺序 | 真实存在，本阶段做成**可区分原因码** |
| 动作还在 RUNNING / 结果未验证 | `_fill_outcome` 区分 running/succeeded | 本阶段新增可区分性 |

**有证据的路由缺口（改动前）**：

* **G1**：`CharacterRuntime._generate` 调用 `tools.orchestrator.run(...)` 时**没有把决策结果带回来** ——
  候选、选中工具、reason 全被丢弃，仓库里没有任何结构化路由记录，只能靠人肉读控制台。
* **G2（真实缺陷）**：`ToolOrchestrator.last_traces` 的清空动作排在**“无候选”早退之后** → 没有候选的回合会
  把**上一轮**的 `ToolTrace` 留在 `last_traces` 里，诊断看起来像这一轮调过工具（stale trace）。
* **G3**：`tools` 为 `None` / 未启用这条分支只回普通聊天，**零诊断痕迹**，看起来与“模型没听懂”一模一样。
* **G4**：`_try_agent` 的分类结论只 narrate 到控制台，没有结构化记录。
* **G5**：`chat_bridge` 的四种丢包（未启用 / 自身发言 / 悄悄话 / 忙碌）**一行记录都不留**。

## §3 真实调用链（以源码为准，非文档复述）

```text
minecraft.chat
  -> MinecraftChatBridge.apply_event        app/integrations/minecraft/chat_bridge.py:86
       （丢弃：未启用 / 空 / 自身发言 / 悄悄话 / _inflight>=MAX_INFLIGHT=4）
  -> TaskTurnHandler.handle                 app/tasks/turn.py:201
       （control_command 无任务则不接管；status_query；TaskIntentDetector.detect 命中即任务侧回答）
  -> CharacterRuntime.respond               app/character/runtime.py:103
  -> CharacterRuntime._generate             app/character/runtime.py:266
       (a) agent 启用 -> AgentHarness._try_agent  app/character/runtime.py:415
             handle_control 命中 -> 纯对话；TaskClassifier.classify（仅 multi_step 接管）
       (b) tools 为 None/未启用 -> engine.chat（普通聊天）
       (c) 否则 -> ToolOrchestrator.run      app/tools/router.py:142
             candidates = ToolRouter.candidates -> registry.candidates(query, limit=candidate_tools)
             无候选 -> 普通聊天早退；有候选 -> json 决策 -> _extract_call
  -> ToolExecutor.execute -> ToolPolicy.authorize（权限/频率/循环/预算）
  -> Confirmation（minecraft.confirmation_required）
  -> MinecraftAgentBridge 信任门（user_not_trusted / action_not_allowed / busy / player_not_found）
  -> MinecraftService -> 既有 Minecraft Action Runtime -> 结构化结果 / action 事件
```

注：`TAK`（任务）路径与工具路径**互斥**：任务侧命中即由任务侧回答，不会再走一遍工具回合。

## §4 实现（逐文件）

### 4.1 `app/tools/models.py` — 路由证据的数据结构

* `RouteTarget(StrEnum)`：`CHAT_ONLY` / `MINECRAFT_TOOL` / `TASK` / `CLARIFY` / `BLOCKED`（有限枚举）。
* `new_route_id()`：`rt_` + `uuid4().hex[:12]`（一次性关联 ID，与任务/动作 ID 可串起来）。
* `@dataclass RouteTrace`：`route_id` / `session_id` / `source` / `turn_origin` / `route_target` /
  `reason_code` / `candidates` / `selected` / `tool_called` / `policy_outcome` / `task_id` /
  `action_id` / `action_state` / `duration_ms` / `text_chars` / `tool_trace_ids` / `fallbacks`，
  外加 `note_fallback()`（上限 4 条）、`as_log_fields()`、`as_payload()`。
* `class RouteJournal`：进程内**有界**记录环（`max_traces=64`），`record/recent/last/snapshot`。
  **不落盘、不加表、不加迁移**。

### 4.2 `app/tools/router.py` — 决策事实 + 修 G2

* `run(..., route: RouteTrace | None = None)`：把“这一回合发生了什么”写进路由记录。
* **把 `self.last_traces = []` 提到函数最前**（修 G2：无候选回退不再留下上一轮的陈旧 trace）。
* 无候选 → `CHAT_ONLY` + `no_tool_candidates`；有候选 → `MINECRAFT_TOOL` + 候选清单。
* 模型没给调用 → 仅当 `route.selected` 为空时记 `model_no_tool_call`（否则会覆盖真实结果码）。
* 预算耗尽 → `budget_exceeded`；实际执行 → `selected` + `tool_called` + `tool_trace_ids`。
* 新增模块级 `_OUTCOME_REASON` 映射 + `_fill_outcome()`：**只有真的成功**才置 `tool_called=True`；
  成功且有 `action_id` 且状态是 `RUNNING/PENDING/ACCEPTED/STARTED` → `action_running`；
  状态是 `PENDING_CONFIRMATION/CONFIRMATION_REQUIRED` → `confirmation_required`；否则 `action_succeeded`；
  失败 → `policy_outcome=error_type`、`action_state=failed`、`reason_code` 由稳定映射得出
  （`policy_denied` / `invalid_tool_decision` / `task_authorization_denied` / `minecraft_offline` /
  `minecraft_disabled` / `clarify_needed` / `tool_failed`）。

### 4.3 `app/character/runtime.py` — 路由的“总账”

* `__init__` 增 `self.routes = RouteJournal(max_traces=64)`。
* `_generate` 构造 `RouteTrace`（`source` 取 `origin_metadata["source"]`、`turn_origin`、`text_chars`），
  并把 `route` 传进 `_try_agent` 与 `orchestrator.run`；各出口都设码：
  tools 未启用 → `tool_runtime_disabled`；`AIError` → `BLOCKED/ai_error`；
  其他异常 → `CHAT_ONLY/tool_path_error`(+fallback)；`clarify_reason` 且无结果 → 保留 `CLARIFY`（§4.4）。
* 新增 `record_route(route, text, *, started)`：补 `duration_ms`/`text_chars`、写记录环、打一条
  结构化日志 `[Route] {...}`。**纯记账**：不重试、不放宽、不伪造终态。
* `_try_agent(..., route=None)`：控制命令 → `control_reply`(CHAT_ONLY)；未到 multi_step → fallback
  `agent_not_multi_step:<kind>`；成功 → `TASK/agent_multi_step/task_id`；目标不清 → `CLARIFY/agent_goal_unclear`。

### 4.4 `app/integrations/minecraft/chat_bridge.py` — 丢包也有记录

* 四条丢弃分支各留稳定原因码：`mc_chat_disabled` / `mc_chat_unusable` / `mc_chat_self_speech` /
  `mc_chat_private` / `mc_chat_busy`（`BLOCKED`），尽量带 `session_id` 与 `text_chars`。
* 由任务侧回答的回合记 `task_handled`（`TASK` + `task_id`）。
* 新增 `_route(...)`：写进 `bot.character.record_route(...)`；**记账失败只 debug，绝不影响聊天**。

## §5 路由行为契约（任务书 §4 对应实现）

| 场景 | 期望 | 实现/证据 |
| --- | --- | --- |
| 4.1 闲聊 / 提到 MC 但没有动作请求 | 不调工具、不建任务、不发额外游戏聊天 | 无候选 → `CHAT_ONLY/no_tool_candidates`（测试 + 真机段落 2） |
| 4.2 明确单步请求 | 走既有 SAFE/LOW 工具链；禁用/拒绝时**不绕过开关**并留原因 | `MINECRAFT_TOOL` + 选中工具 + `tool_called`（真机段落 1/3） |
| 4.3 多步骤目标 | 仍走 Task/Agent/TaskRuntime；不拆成无约束连续原子调用 | 任务侧命中 → `TASK/task_handled`；agent → `TASK/agent_multi_step` |
| 4.4 参数不足 / 歧义 | SAFE 观察或澄清，**绝不猜坐标/身份** | `CLARIFY`；被拒动作**没有 action_id**（真机段落 4） |
| 4.5 控制与终止 | `stop` 不被聊天分支吞掉；一条消息只处理一次 | 真机段落 3：选中 `minecraft_stop` 并真的执行 |
| 4.6 路由优先级 | 先既有 Task/Agent，再工具路由；**不新增一次模型调用做分类器** | 未新增任何模型调用；顺序未改 |

## §6 安全不变量核对（任务书 §6）

* `ACTION_RISK` **未改**（仍是 19 个工具、同样的 SAFE/LOW/MEDIUM 分级）。
* **未新增任何** Minecraft 工具或 Action Runtime action。
* `allow_medium` 默认值**未改**（真机门禁强制 `False`；真实配置文件里仍是 `True`，与本阶段无关）。
* 两道确认门、`TaskAuthorization`/`TaskStepAuthorization`/参数指纹/冻结计划/Policy **未改**。
* `TurnOrigin` 仍由可靠调用来源派生（真机实测 `user`；BACKGROUND 回合用例证明不能驱动 LOW）。
* 所有真实动作仍经 `MinecraftAgentBridge → MinecraftService → Action Runtime`，未走旁路。
* 自我发言过滤保留（真机段落 6 实测 `mc_chat_self_speech`）。
* **不加表、不加迁移**（迁移演练见 §7）。
* **不存思维链、不存聊天原文**：路由记录只有长度与结构化事实（测试 + 真机段落 5 断言）。

## §7 测试与门禁

| 门禁 | 结果 |
| --- | --- |
| 新增 `tests/test_phase8a_action_routing.py` | **16 passed**（确定性、无 `sleep`、不依赖真实 LLM） |
| 相关既有套件 | 241 passed（router/tools/tool_runtime/character*/chat_integration/mc chat bridge/agent*） |
| 全量 `pytest tests -q` | **3879 passed in 671.76s**（基线 3863 + 新增 16，精确吻合） |
| `ruff check .` | All checks passed |
| `ruff format --check .` | **666 files already formatted** |
| `mypy app` | Success（336 source files） |
| `minecraft_runtime npm test` | ALL CHECKS PASSED（exit 0） |
| 迁移演练（副本） | `version [36,36]`、`tables [83,83]`、`integrity [ok,ok]`、`added_tables []`、`lost_rows {}`、`ok:false`（已最新版的**预期 no-op**） |
| 真实 Java 门禁 | **PASS（6 段落全绿，exit 0）**，见 §8 |

新增测试矩阵（`tests/test_phase8a_action_routing.py`）：

* §8.1 正向：闲聊零候选、模型没选（与“无候选”区分）、世界查询走工具链、背包选中 `minecraft_inventory`、
  `停下` 落到 `minecraft_stop`、多步骤 → `TASK/task_handled`、agent multi_step → `TASK/agent_multi_step`。
* §8.2 负向/歧义：`tools` 关闭 → `tool_runtime_disabled`（配置原因，不是“模型没听懂”）、
  未知工具 → `invalid_tool_decision` 且不执行、不可信玩家 LOW → `policy_denied`、
  BACKGROUND 回合不能驱动 LOW 动作。
* §8.3 追踪与回退：桥接丢包三因各有原因码、**stale trace 回归**、路由证据不含原文、
  记录环有界（80 回合后 ≤64）、目标不清 → `CLARIFY`。

## §8 真实 Java 服务器门禁（`scripts/route_trace_smoke_real.py`，本地专用不发布）

把**合成的一条游戏内聊天事件**喂进**真实** `MinecraftChatBridge`，在真实 `MinecraftService`
（真 Java 服务器 + 真 Node runtime，`view distance` 与感知层都是真的）上跑；
只有模型是脚本化的（确定性），工具执行 / 策略门 / 信任门 / Service / Action Runtime 全是真的。

**结论：PASS（exit 0）**

| 段落 | 断言 | 真机证据 |
| --- | --- | --- |
| 1 明确世界查询 | `MINECRAFT_TOOL` + 选中 `minecraft_find_blocks` + `tool_called` + `action_succeeded` | 真服务器返回「附近找到 8 个：oak_log((-254,65,-120))…」 |
| 2 闲聊 | `CHAT_ONLY` + `no_tool_candidates` + 零工具执行 | 无新增 trace |
| 3 「停下来」 | 选中 `minecraft_stop` 且 `tool_called=True` | 未被聊天/控制分支吞掉 |
| 4 不可信玩家 LOW | `policy_denied` + `minecraft.user_not_trusted` + **无 action_id** | 根本没进 Action Runtime |
| 5 自身发言过滤 | `mc_chat_self_speech` + 零动作 | §6.8 语义保留 |
| 6 路由证据 | 5 条记录、`rt_` 前缀、三种 target 可区分、无原文、有界 | `[BLOCKED, CHAT_ONLY, MINECRAFT_TOOL]` |

真实环境事实：`runtime 实例 rt_mv5wypfg_1e3ed959`、位置 `(-255.54, 63.0, -118.67)`、维度 `overworld`；
脚本全程强制 `allow_medium=False`。

**首轮失败如实记录（都是脚本自身问题，未放宽任何断言）**：

1. 第一版把请求方写成 `trusted[0] if trusted else "Catodayo"` —— `trusted_players` 为空时退化成**她自己的账号名**，
   于是每个回合都被正确地判成 `mc_chat_self_speech`。修正为“挑一个不是她本人的玩家名”。
2. 第一版假设 `ToolTrace` 有 `success` 字段（实际字段是 `status`/`result_summary`/`error_type`）→ 断言写错。
3. 第一版 `minecraft_find_blocks` 参数写成 `block=`（真实 schema 要求 `block_names`）→ 被工具参数校验拒绝。
4. 第一版没有等**感知层**世界视图就绪：策略门的 `online` 取自 `world_facts().online`（不是连接状态），
   感知还没出结果时非 `OFFLINE_TOOLS` 的动作会被 fail-closed 判成 `minecraft_offline` —— 这是**设计行为**，
   脚本改为有界等待感知视图就绪（`wait_for`，不是 sleep 造假）。

## §9 已知限制与未完成项（只列有证据的）

* `RouteJournal` 是**进程内**有界结构（64 条），进程重启即清空；这是刻意的“不加表不加迁移”取舍。
* Phase 8A 只交付**结构化日志 + 单元/集成测试 + 真机报告**；WebUI 展示列为后续建议，本阶段未改任何 UI。
* 任务侧（`TASK`）路由的真机端到端由既有 7F.x 门禁覆盖；本阶段的真机脚本聚焦**工具/闲聊/控制/拒绝**四条路径。
* 树木采伐、批量资源收集、玩家物品交付等**新玩法不在本阶段范围**（任务书最终范围声明）。
* 未新增任何“额外一次模型调用做分类器”的分流逻辑。

## §10 变更文件

```text
app/tools/models.py                        RouteTarget / new_route_id / RouteTrace / RouteJournal
app/tools/router.py                        决策事实 + 修 G2 + _fill_outcome
app/character/runtime.py                   RouteTrace 构造/传参/record_route + 各出口原因码
app/integrations/minecraft/chat_bridge.py  丢包原因码 + task_handled
tests/test_phase8a_action_routing.py       新增 16 项
CHANGELOG.md / docs/README.md / docs/MINECRAFT_PHASE8A.md
scripts/route_trace_smoke_real.py          真机门禁（本地专用，不发布）
scripts/stop_catoobot.ps1 / start_*_visible.ps1  本地运维脚本（不发布）
```

## §11 裁决

**PASS**：已从源码定位“只聊天/不行动”的具体路由原因（G1–G5，其中 G2 是可复现的真实缺陷），
给出可区分的原因码与结构化证据，正/负向契约均有自动化测试，真实 Java 服务器门禁 6 段落全绿；
未新增能力、未扩大授权、未改风险表与确认门、未加迁移。

## §12 Follow-up 修正（Phase 8A FIX — Routing Target Contract Fix）

**基线 `8c4efdc` → 修正后见提交记录。** 任务书 §7 要求先把 8A 的一个契约不一致单独修掉，再评估 8B。

### §12.1 不一致（已确认，非理论风险）

`ToolOrchestrator.run()` 有两条「本轮没有发生任何工具动作」的出口，但契约只兑现了一半：

| 出口 | `reason_code` | 修正前 `route_target` | 修正后 |
| --- | --- | --- | --- |
| 没有任何候选工具 | `no_tool_candidates` | `CHAT_ONLY` | `CHAT_ONLY`（不变） |
| 有候选、模型未给出可解析调用 | `model_no_tool_call` | **`MINECRAFT_TOOL`（残留候选阶段的值）** | **`CHAT_ONLY`** |

后果：诊断会把「模型没调用工具」显示成一次**工具路由**，与 `§5.2` 要求的「未调用 / 被拒绝 / 动作进行中 / 真实验证成功必须可区分」相冲突。

### §12.2 修正（最小改动，1 处逻辑 + 1 处断言）

* `app/tools/router.py`：在 `call is None` 且 `route.selected` 为空的分支里，除 `reason_code = "model_no_tool_call"` 外，**同时**把 `route.route_target` 置回 `RouteTarget.CHAT_ONLY.value`。

  仍然保留「只有本轮**从未**提取到可解析调用才记 `model_no_tool_call`」的既有条件 —— 一旦本轮提取到过调用（成功/被拒/未知工具），收官回合不会覆盖已确定的结果码。
* `tests/test_phase8a_action_routing.py::test_model_not_calling_a_tool_is_distinct_from_no_candidates`：新增断言 `route.route_target == RouteTarget.CHAT_ONLY.value`。
* **未改动**：候选检索阈值（`min_score`）、模型提示词、`max_calls_per_turn`、`last_traces` 清空顺序、`ACTION_RISK`、`trusted_players`、确认门、授权链。
* 验收条件核对：无候选 与 有候选但模型未选择 **都归为 `CHAT_ONLY`**，但仍保留各自不同的 `reason_code`；真正选中工具时的路由与执行行为不变（`test_explicit_world_query_routes_through_the_tool_path` 等既有断言未变、继续通过）。

### §12.3 实际执行的门禁（本修正）

| 命令 | 结果 |
| --- | --- |
| `pytest tests/test_phase8a_action_routing.py -q` | **16 passed** in 5.47s |
| `pytest tests/test_tools.py tests/test_tool_runtime.py tests/test_character.py tests/test_character_foundation.py tests/test_minecraft_chat_bridge.py -q` | **141 passed** in 30.29s |
| `pytest tests/test_minecraft_agent_e2e.py -q` | **10 passed** in 0.48s |
| `ruff check .` | All checks passed（exit 0） |
| `ruff format --check .` | 668 files already formatted（exit 0） |
| `mypy app` | Success: no issues found in 336 source files（exit 0） |

### §12.4 CI（GitHub Actions）

| 项 | 值 |
| --- | --- |
| run ID | **38328283966** |
| URL | https://github.com/rinsoraa/CatooBot/actions/runs/38328283966 |
| attempt | **1**（一次通过，无 flake、无重跑） |
| lint · format · types · tests | completed / **success** |
| webui · typecheck · tests · build | completed / **success** |
| CI 上 pytest | **3876 passed, 3 skipped in 334.69s**（= 本地 3879 − 3 条既有平台条件性 skip；本次修正只加断言、不增测试数，故计数与 8A 发布时一致） |
| CI 上 minecraft_runtime | find-blocks-test **41/41** ALL CHECKS PASSED；e2e `bridge auth boundary + runtime instance ✓`；`[e2e] ALL CHECKS PASSED` |
| 实际 SKIPPED（全部为既有假服务器限制，与本修正无关） | equip / inventory_move STOP；container STOP；craft 成功路径；craft STOP；3×3 craft 成功路径；dropped_items / pickup 成功路径 |

### §12.4 范围声明

本修正**只**修上述路由目标契约，不重开已验收阶段、不引入新能力、不改任何权限或授权语义。8A 的其余结论（G1–G5 审计、测试矩阵、真机 6 段落）保持原样有效。
