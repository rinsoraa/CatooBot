# CatooBot Minecraft Phase 7C · Life Intent → Task Proposal / Intent Execution Boundary

> 本阶段的目标（任务书 §零）：
>
> **建立从自主意图到结构化任务提案的安全桥梁，使罐头能够明确表达"我想做什么、为什么想做、
> 需要什么能力、预期会改变什么"，但暂时不自动执行自主 Minecraft 任务。**
>
> 终点：**罐头能够将一个自主想法转化为清晰、可审计、知道自身能力边界的任务提案，
> 但这个提案不会自行获得执行权。**

7C 的执行层仍然是：

```text
NONE
```

---

## 1. 闭环（§一/§五）

```text
LifeIntent ──┐
             ├──→ TaskProposal ──→ Feasibility / Capability Check ──→ Proposal Decision
QQ 用户请求 ─┘                                                          ├── REJECTED
                                                                        ├── NEEDS_MORE_INFORMATION
                                                                        ├── NEEDS_USER_APPROVAL
                                                                        └── READY_FOR_FUTURE_EXECUTION
```

`READY_FOR_FUTURE_EXECUTION` **只**代表提案通过了当前的**静态**检查，
不代表获得任何真实世界操作权限（§一）。

四个概念必须分清（§五）：

```text
LifeIntent   = 为什么想做        （7A；app/initiative/）
TaskProposal = 准备怎样做        （7C；app/tasks/proposal*.py，**不是**任务）
Task         = 已进入执行系统的任务（5A；app/tasks/runtime.py —— 7C 的 LIFE 路径绝不创建它）
Action       = 实际发生的世界操作  （4B+；app/integrations/minecraft/agent.py）
```

新增代码（5 个模块，都在 `app/tasks/`）：

| 文件 | 职责 |
| --- | --- |
| `app/tasks/capabilities.py` | 能力目录：从既有 19 个原子工具的 `ACTION_RISK` + 工具注册表提取 |
| `app/tasks/proposal.py` | 提案模型 / 状态机 / 能力缺口 / 目标解析 / 指纹 / 静态检查 |
| `app/tasks/proposal_store.py` | 持久化（内存 + SQLite；指纹唯一、终态不可覆盖） |
| `app/tasks/proposal_events.py` | `proposal.*` 事件（state update，不含推理） |
| `app/tasks/proposal_service.py` | 编排：意图/用户请求 → 提案；过期；恢复；只读视图 + 身份解析 |

**没有第二套 TaskRuntime**（§二）：7C 一次都没有 import `app.tasks.runtime`（源码级断言见 §14）。
**没有新 Minecraft 工具、没有新 ActionRuntime action、没有新 TaskRuntime 状态**（§十五）：
19 个工具一个没加（`capability_catalog()` 的条目数与 `ACTION_RISK` 严格相等），
`ProposalStatus` 里**没有** `RUNNING` / `EXECUTING` / `CONFIRMED`。

---

## 2. 来源隔离：USER 与 LIFE（§四）

两条路径**共用**同一套 schema、能力目录、可行性检查与风险表，但**授权规则不同**：

| | USER | LIFE |
| --- | --- | --- |
| 入口 | QQ 任务入口（既有 5A/5B 链）**旁路记账** | 7A 新建的 LifeIntent（调度器一轮一读） |
| 目标 | 发起人 QQ 号 → **可信身份桥** → MC 玩家（§八） | `related_player`（若有）→ 同一座身份桥 |
| 权限 | 仍由既有 TaskAuthorization / Confirmation 决定 | **零额外权限**：提案到此为止 |
| 会不会建任务 | 会 —— 但那是既有任务链建的，不是提案建的 | **绝不** |

`LIFE` 的提案 `initiator` 是角色 id（`罐头@1dae9716`），`USER` 的 `initiator` 是 QQ 号；
USER 提案没有 `intent_id`，LIFE 提案必有。来源由**代码路径**决定，输入文本改不了它
（`tests/test_proposal_security.py::test_life_intent_cannot_name_its_own_source`）。

---

## 3. 提案模型（§三）

```python
TaskProposal(
    proposal_id,          # TP-YYYYMMDD-NNN（与 7A 的 INT-… 同一套编号风格）
    source,               # USER | LIFE | SYSTEM
    objective,            # 一句可检查的目标（标题+描述+关联记忆，最多 200 字）
    intent_id, initiator, # 关联意图 / 发起人（审计用）
    target,               # {status, server_id, player_uuid, player_name, source, reason}
    required_capabilities,# 扁平能力 id 列表（§十二 的 Required Capabilities）
    requirements,         # 逐条 {capability_id, gap, risk_class, available, reason}
    expected_effects,     # 预期会改变什么（来自能力目录的说明）
    risk_summary,         # {classes, max_risk, would_require_confirmation, source}
    feasibility,          # SUPPORTED | PARTIALLY_SUPPORTED | UNSUPPORTED | UNKNOWN
    status, created_at, expires_at, updated_at, fingerprint, reason,
)
```

* `status` 只有六个值：`REJECTED / NEEDS_MORE_INFORMATION / NEEDS_USER_APPROVAL /
  READY_FOR_FUTURE_EXECUTION / EXPIRED / CANCELLED`；
* 终态（`REJECTED / EXPIRED / CANCELLED`）**没有出口**（`ALLOWED_PROPOSAL_TRANSITIONS`）；
* 序列化走 `.value`（**不是** `str(枚举)`）—— 这个坑 7A 踩过一次：
  `str(ProposalStatus.X)` 会给出 `"ProposalStatus.X"`，回读时静默退回默认值
  （来源退回 SYSTEM、状态退回 NEEDS_MORE_INFORMATION）。7C 的 `_text()` 统一处理，
  并有测试盯着 `to_payload()/from_payload()` 的往返。

---

## 4. 能力目录（§六）

`capability_catalog(*, registry=None, minecraft_online=False, tool_risk_table=None)`：

```text
capability_id / description / input_schema / expected_effect / risk_class / available / limitations
```

* **来源是既有的 19 个 Minecraft 原子工具**（`app.integrations.minecraft.agent.ACTION_RISK`），
  风险分类**一个字都没改**（§九）；`input_schema` 从工具注册表读（`registry.metadata(name)`），
  读不到就空表 —— 目录建设绝不会失败；
* `available` 由**实际连接状态**决定：MC 没连上时世界类能力一律 `unavailable`
  （保守，不假装可用）；在线状态来自 `MinecraftService.snapshot().connection.status == "ONLINE"`；
* 只**读**注册表/连接状态，从不调用任何工具（运行时断言见 §14）。

---

## 5. 能力缺口与可行性（§六/§七）

目标文本 → 需要哪些能力：`required_capabilities(objective)` 用一张**确定性**关键词表
（`OBJECTIVE_CAPABILITY_HINTS`：橡木/原木/挖/采集/收集/捡/跟着/跟随/看看/观察/合成/制作/放/
箱子/拿出来/背包…），命中顺序即返回顺序；`needs_design_information(objective)` 认
`DESIGN_REQUIRED_KEYWORDS`（刷铁机/农场/机制/设计/规划/村庄/村民/僵尸/红石/自动化）这类
**"有工具也做不了、得先有方案"**的目标。

缺口四值（§七）：`SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / UNKNOWN`，
对应建议四值：`NEEDS_NEW_SKILL / NEEDS_NEW_TOOL / NEEDS_WORLD_OBSERVATION / NEEDS_USER_INPUT`
（**只是建议**，不会自动写/装/执行任何工具）。

判定规则（确定性、单向保守）：

| 情况 | feasibility | status |
| --- | --- | --- |
| 需要的都在线可用，且最高风险是 SAFE/LOW | `SUPPORTED` | `READY_FOR_FUTURE_EXECUTION` |
| 需要的都在线可用，但含 MEDIUM/HIGH/DESTRUCTIVE | `SUPPORTED` | `NEEDS_USER_APPROVAL` |
| 有 `PARTIALLY_SUPPORTED`（例如离线/不可用） | `PARTIALLY_SUPPORTED` | `NEEDS_MORE_INFORMATION` |
| 有 `UNKNOWN`（要方案，或目标太模糊） | `UNKNOWN` | `NEEDS_MORE_INFORMATION` |
| 有 `UNSUPPORTED`（目录里没有这个能力） | `UNSUPPORTED` | `NEEDS_MORE_INFORMATION` |

**不得通过猜测把 `UNKNOWN` 自动升级成 `SUPPORTED`**（§七）：判定是纯函数，
"再跑一遍"不会变（`test_unknown_never_upgrades_itself`）。

例（真实结果，离线时）：

```text
目标：建造一台刷铁机
→ required=["design_plan"] gap=UNKNOWN  feasibility=UNKNOWN  status=NEEDS_MORE_INFORMATION
  suggestions=[NEEDS_USER_INPUT]
```

---

## 6. 身份与目标玩家（§八）

对**需要指定玩家**的提案（关键词表 `PLAYER_TARGET_KEYWORDS`：跟着/跟住/跟随/来我身边/带我去…，
或 LifeIntent 带 `related_player`），必须走可信身份桥：

```text
QQ user → IdentityStore.links_for(platform="qq", user_id=…) → VERIFIED IdentityLink
        → (server_id, player_uuid, username)
```

* **只认 VERIFIED**：`REVOKED` → 直接 `REJECTED`（`target_revoked`）；
  `CONFLICT` → `REJECTED`（`target_conflict`）；没有绑定 → `NEEDS_MORE_INFORMATION`
  （`target_unresolved`）；服务器不匹配 → `MISSING`（`server_mismatch`）；
* 多条 VERIFIED 指向**不同 UUID** → `CONFLICT`（`multiple_verified_targets`）——
  **绝不**因为同名/多人就把两名真实用户合并成一个目标；
* 身份层读不到（异常）→ `MISSING`（`identity_unavailable`）+ `degraded_reason`，
  **不猜、不放行**；
* 只读视图只给 UUID **后四位**（`uuid_suffix`），与 QQ / WebUI 其它地方一致。

---

## 7. 风险与授权（§九）

* 复用既有 `ACTION_RISK`（SAFE=只读观察 / LOW=移动跟随 / MEDIUM=挖掘放置物品操作…），
  **本阶段不改变任何风险分类**；
* `risk_summary.would_require_confirmation` 只是**描述**：真执行时 MEDIUM 仍然要走既有
  确认链（7C 没有确认入口，也没有 Policy / ConfirmationStore 句柄）；
* LIFE 不比 USER 多任何权限：同目标同风险下两者 status / max_risk / would_require_confirmation
  完全一致（`test_life_proposal_gets_no_more_permission_than_user`）；
* `allow_medium=false` **一个字没改**（测试直接断言 `AppConfig().minecraft.agent.tools.allow_medium is False`）。

---

## 8. 去重与过期（§十）

* **指纹**：`source | objective(归一化) | target.status:target_key | intent_id | 时间桶`。
  目标**状态**也进指纹 —— "解析不到 / 被撤销 / 已验证"是三种不同的事实，
  不能因为都"没有 uuid"就被当成同一份提案合并掉；
* **时间桶** 3600s：同一个桶里同一份提案只有一行（`INSERT OR IGNORE` + `fingerprint` 唯一索引），
  重启 / 崩溃重放都不会多出来；下一个桶才可能再提一份（"按桶去重"不是"永久压制"）；
* **TTL** 默认 6 小时（`world.proposals.ttl_hours`）；到点 `expire_due()` 置 `EXPIRED` 并记事件；
* **终态不可覆盖**：`update_status` 先过 `proposal_transition_allowed`，再带上
  `WHERE proposal_id=? AND status=?` 的 CAS；重启 `recover()` 只处理**开着**的行，
  终态连碰都不会被碰。**过期、撤销或失效的提案永远不会被恢复成有效的自动执行请求**；
* 状态更新失败（并发下已被改成别的终态）→ 尊重先到的那个，`expire_due` 直接跳过。

---

## 9. 与 7A / 7B / ActivityPlanner 的关系（§十一）

```text
7B（继续负责）：LifeIntent → ActivityPlan（软项 initiative_fit）
7C（新增）    ：LifeIntent → TaskProposal
```

* **当前计划不会自动变成 TaskProposal**：`_proposal_pass` 的输入只有
  "这一轮 check 真的**新建**的 LifeIntent"，与 ActivityPlan / ActivityEpisode 无任何关系；
* 7B 的桥一字未改（`app/activity/initiative.py` 与 Planner 都没动）；
* 提案不会创建 / 修改 / 影响 ActivityEpisode（源码级 + 运行时句柄级断言）。

---

## 10. 只读可观测（§十二）

`GET /api/v1/world/proposals`（只读，`execution_layer: "NONE"`）：

```text
proposal_id / source / objective / intent_id / initiator /
target{status, player_name, server_id, uuid_suffix, reason} /
required_capabilities / capability_gaps[{capability_id, gap, reason}] /
feasibility / risk_summary / status / reason / created_at / expires_at / updated_at / terminal
```

WebUI 世界页新增「任务提案（她准备怎样做）」只读卡片，逐条展示上面每一项，
**没有任何 Execute / Confirm / Start / Run / Approve 入口**，页面发出的请求全是 GET
（vitest 断言方式与 7A 一致）。不展示完整 UUID、原始 QQ 事件对象或模型隐藏推理
（事件载荷只有 `proposal_id/source/status/feasibility/intent_id/capability_gaps/…`）。

---

## 11. 存储与迁移（§十）

* **只新增一张表** `task_proposals`（迁移 **32**）—— 先查过现有库：
  `tasks` 是"已进入执行系统"的任务（5A），`life_intents` 是"为什么想做"（7A），语义都不同；
* **历史复用既有 append-only 审计表** `behavior_events`（`scope_key='proposal'`、
  `type LIKE 'proposal.%'`），所以**没有第二张历史表**；
* 唯一索引 `idx_task_proposals_fingerprint` + `INSERT OR IGNORE` 保证幂等；
* 换库/老库升级照旧走 `_MIGRATIONS`（迁移是**只加不删**的）；
  "最新迁移号"的冻结点守卫在 `tests/test_activity_plan_persistence.py`（现在钉 32）。

---

## 12. 接线

* `app/core/bot.py`：`_setup_task_proposals()`（在 `_setup_world_initiative()` **之后**装配；
  失败只降级为 `self.proposals = None`，绝不拖垮启动），启动时 `recover()`；
* `app/behavior/scheduler.py`：生命意图那一趟跑完顺手把**新建**的意图交给提案层
  （`_proposal_pass`，一次最多 `max_per_pass` 条；整段失败只是没有提案）；
* `app/tasks/qq_entry.py`：QQ 任务入口在**真的建了任务**（`outcome.action == "created"`）时
  旁路记一条 USER 提案；任何失败都被吞掉 —— 绝不回头影响回复或任务链；
* 配置 `world.proposals.{enabled, recent_limit, max_per_pass, ttl_hours}`（默认开启；
  与 7A 的 `world.initiative` 同一套旋钮风格），已进 WebUI 配置注册表与
  `config/config.example.yaml`；
* 刻意**不**把 7C 的模型加进 `app/tasks/__init__.py` 的 barrel：
  那会让 `import app.tasks` 连带拉起 `app.integrations.minecraft.agent`（现状是干净的），
  而 7C 只需要直接 import 自己的模块。

---

## 13. 测试（§十三 矩阵 17 项）

新增 **77** 项（`tests/test_task_proposal.py` 59 + `tests/test_proposal_security.py` 18，
夹具 `tests/proposal_fakes.py`：真 store + 真 service + 可注入的只读事实，不联网不调模型）：

| 任务书 §十三 | 落在哪 |
| --- | --- |
| 1 LifeIntent 产生结构化 Proposal | `TestStructuredProposal`（含"目标只用意图自己的文本"） |
| 2 过期/被抑制的 Intent 不产生提案 | `TestIntentGating`（SUPPRESSED/EXPIRED/CANCELLED/RESOLVED + 过期 + 空目标） |
| 3 重复 Proposal 被合并 | `TestDeduplication`（同桶 merge / 不同目标新建 / 下一桶新建 / 指纹确定性） |
| 4 用户请求与自主意图不同来源 | `TestSourceIsolation` |
| 5 VERIFIED IdentityLink 正确映射 | `TestIdentityResolution`（+ 只给后四位） |
| 6 REVOKED / CONFLICT 无法解析目标 | `TestIdentityResolution`（+ 多人不合并 + 服务器不匹配 + 身份层故障降级） |
| 7 缺失能力被标记为缺口 | `TestCapabilityGaps`（离线 PARTIAL / design UNKNOWN / 目录缺项 UNSUPPORTED） |
| 8 未知能力不被假定可用 | `test_unknown_never_upgrades_itself` |
| 9 SAFE/LOW/MEDIUM 风险正确映射 | `TestRiskMapping` |
| 10 Life Proposal 不创建 Task | `test_proposal_security.TestSourceLevelGuards` / `TestNoExecutionHandles` |
| 11 Proposal 不调用 Minecraft 工具 | `TestNoWorldOrMessageSideEffects`（`_SpyMinecraft`：只碰 `enabled`/`snapshot`）+ `_SpyRegistry` |
| 12 Proposal 不修改 ActivityEpisode | 同上（源码级 + 无 activity 句柄） |
| 13 Proposal 不发起主动 QQ | `test_no_execution_shaped_calls`（send/reply/deliver 一个都不出现） |
| 14 重启不生成重复 Proposal | `test_restart_does_not_create_a_duplicate` + SQLite 版 |
| 15 过期提案不能被自动复活 | `test_expired_row_is_never_flipped_back` / `test_terminal_proposal_is_not_reopened` / `recover_never_revives` |
| 16 `allow_medium` 默认保持 false | `test_allow_medium_is_still_false` |
| 17 6A–6D.1 与 7A–7B 回归 | 全量 `pytest tests`（3318 项，见 §15） |

安全边界的写法与 7A 一致（AST 源码级 + 运行时句柄级），**白名单里只有两样只读事实**：

```text
app.integrations.minecraft.agent   # 风险表（§六/§九：复用既有定义）
app.integrations.minecraft.identity # 身份桥（§八：复用可信绑定）
```

`conn.execute(...)`（SQL）不被当成"执行动作"（`SQL_RECEIVERS` 白名单）；
禁止清单里包括 `create_task / confirm_and_start / start_task / run_task / confirm /
invoke / call_tool / execute / dispatch / send / reply / deliver` 等。

---

## 14. Known limitations

1. **提案不会自己变新**：`_proposal_pass` 只吃"这一轮新建的意图"。
   启动时已经挂着的 PROPOSED 意图不会补一份提案（避免重启就冒出一堆），
   代价是"老念头"要等下一轮（或同一个桶之外）才会被提上来。
2. **同一小时同目标只提一次**：指纹含时间桶，**被否决/过期**的提案在同一个桶里
   不会重新提一份（要等下一个桶）。这是 §十 的保守取法，代价是"被拒后立刻重提"做不到。
3. **目标解析只认显式绑定**：没有绑定、绑定被撤销或服务器对不上时，提案停在
   `NEEDS_MORE_INFORMATION` / `REJECTED`，**不会**退化成"跟着同名玩家"。
4. **能力目录的 available 是全有或全无**：`minecraft_online` 决定世界类能力是否可用，
   不区分"这个维度没加载"这类细分（真实的细分状态要等执行阶段的重新检查）。
5. **`NEEDS_MORE_INFORMATION` / `READY_FOR_FUTURE_EXECUTION` 不是许可证**：
   未来任何执行阶段都必须**重新**检查当前世界、能力、授权、目标和风险（§九）。
