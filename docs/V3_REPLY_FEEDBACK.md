# v3.0 设计 · 回复效果闭环（Task 20）

> 状态：**设计已按评审修订（v2）**，方向/非目标/里程碑/「绝不动硬门槛」保持不变
> 修订来源：用户评审 8 条（判定顺序、极性新实现、按回合结算、只看自己开的口、EMA 与基线、
> ambient 权重、momentum 增益、私聊只记录）
> 相关代码：`app/social/`（attention / policy / monitor / cognition）、`app/response/delivery.py`、
> `plugins/chat/plugin.py`（`_post_reply` / `_turn_finished`）、`app/behavior/scheduler.py`、
> `app/memory/outbox.py`（Task 14）

## 1. 问题

决策只发生在**说之前**（社交认知 + 参与策略 + 注意力），**说之后零反馈**：没人接话、被无视、
被嫌刷屏，都不影响她下次判断。结果可能是：安静群里越说越少，或嫌弃她的群里越说越多。

## 2. 目标与非目标

**目标**：把"她说的每回合之后发生了什么"变成可观测、可查询、可（软）回流的信号。

**非目标**：不引入 `random()<p` / bandit；不改变硬门槛（冷却、日限、开关、额度累积语义）；
不把"没人接话"当成"她说错了"；不追求回复率 KPI。

## 3. 观测模型：**一回合一行**（关键修订 ③）

她一个回合约 2–3 条气泡：按**消息**结算会让 EMA 被气泡数加权、负信号重复计，
而且 `MessageDelivery.last_sent_ids` 每个 scope 只保留最后一条 id（`delivery.py:58/113`）。
因此观测表按 **turn_id** 落一行，回流只看**回合级**结果。

`ReplyOutcome`：

| 字段 | 含义 |
|---|---|
| `turn_id` | 回合 id（幂等键：同一回合只会有一行） |
| `scope_key` / `is_group` | `group:<id>` / `user:<id>`；私聊**只记录不回流** |
| `reason_code` | 她**为什么开口**（`SocialDecision.reason_code`） |
| `self_initiated` | 见 §6：只有自己开的口才参与系数 |
| `sent_at` / `window_seconds` | 说出时间；观察窗默认 90s |
| `replies` / `first_reply_after` | 窗口内**别人**的消息数 / 首条延迟 |
| `addressed_back` | 窗口内有人 @ 她 / 回复她的 message_id / 戳她 |
| `polarity` | **0 或 −1**（永不 +1，见 §4） |
| `baseline` | **她开口之前的等长窗口**内别人的消息数（`monitor.external_recent()`，天然排除她自己） |
| `verdict` | `engaged` / `ambient` / `silence` / `negative` / `unknown` |
| `settled_at` / `status` | 结算时间与状态（`pending` → `settled`） |

> baseline 用「前一个等长窗口」而不是 30 分钟人均速率：直接可比、不需要分母，也不依赖
> monitor 的内存态 deque（重启即空）——重启由 §8 的 `unknown` 兜底。

## 4. 判定规则（无 LLM，纯规则 + 保守词典）

**顺序（修订 ①）**：

1. `polarity < 0` → **negative**（有人 @ 她说"别刷屏"必须是负，不能被 `addressed_back` 抢走）；
2. 否则 `addressed_back` → **engaged**；
3. 否则 `replies ≥ max(1, baseline)` → **ambient**（群在聊，但没针对她）；
4. 否则 → **silence**，并附 `quiet_group = (replies <= baseline)`。

**极性是全新实现，规则从紧（修订 ②）**：仓库里没有情感推断代码，所以：

- 词典**只能产出 0 或 −1**，永远不产出 +1；正向信号一律来自结构化的 `addressed_back`；
- 判 −1 必须**同时**满足：紧邻她的消息（窗口内第一条别人消息）+ **强指向**（@她 / 引用她的
  message_id / 该窗口消息量极低时紧跟其后）+ 命中负面词表（"别刷屏 / 别说了 / 闭嘴 / 烦"等保守小表）；
- 其余一律记 0。

## 5. 存储与清理

- **迁移 18** `reply_outcomes`（上表字段；`turn_id` 唯一；索引 `(scope_key, sent_at DESC)`、
  `(verdict, sent_at DESC)`；`polarity` 可空 = 未结算）。
- **结算任务**：共享调度器（不新建循环），每 30s 扫已过窗口的 `pending` 行 →
  `misfire_policy="skip"`；**重启后过期很久的行直接标 `unknown`，不补算**。
- **保留期** `social.feedback.retention_days`（默认 30），每日清理（同 `ai_usage` 模式）。
- **写失败**：走 Task 14 的 outbox，新增 `kind="reply_outcome"`；重放 handler 在
  `app/social/feedback.py` 注册（**不能只依赖 `MemoryManager._replay_entry`**，它对未知 kind
  会 raise），并让 social 侧能拿到 `bot.outbox`。

## 6. 回流（只动软状态，且有死区）

`SocialEngagement`（内存态 + **持久化**到 `bot_state`，见修订 ⑤）：

- **时间衰减**：`decay = 0.5 ** (Δt / half_life)`，半衰期 7 天；**按时间而非按样本**
  （实现为「时间衰减的加权证据」：`value`/`weight` 各自按 Δt 衰减，均值 = value/weight，
  有效值再按「距她最后一次开口的时长」衰减——久无新证据时软系数自然回归 1.0）
  （按样本会让半衰期取决于她说话频率——而那正是本回路控制的量，会形成反馈环）；
  启动时按 Δt 补一次衰减，重启不清零。
- **样本死区**：已结算的 `self_initiated` 样本 **< 8** 时，软系数恒为 **1.0**。
- **只看自己开的口（修订 ④，最重要）**：
  `self_initiated = reason_code ∉ {direct_mention, reply_to_bot, direct_follow_up}`。
  被点名的回合几乎必然 engaged，混进 EMA 会把系数顶到 >1，于是她在没人理她的场合说得更多
  ——正是要避免的失效模式。被点名的回合**照常记录、照常上 WebUI，但不参与系数**。
- **量表（修订 ⑥）**：`engaged +1`、`ambient 0`、`silence+quiet 0`、`silence(热闹群) −0.25`、
  `negative −1`。ambient 不再是正证据——"群里在聊但没人理她"给 +0.25 等于奖励在热闹群刷屏。
- **接入点**：
  - `SocialAttention.note_reply_outcome(group_id, score)`：momentum 增量 = **`score × 0.1`**
    （`attention.py` 里 momentum ∈ [0,1]、每次 `note_*` 只加 0.2~0.3，直接喂 ±1 会瞬间打满）；
    **negative 只抵消已有 momentum，不让 momentum 变负**（保持现有语义）。
  - `ParticipationPolicy.credit_participation` 的额度累积速率 × `0.9 ~ 1.1`（由 ema 线性映射，
    死区内恒 1.0）；冷却/日限/开关一律不变。
- 私聊：只记录，不回流（同意保持）。

## 7. WebUI

`/social` 只读卡片「她最近说得怎么样」（`SocialAdminService.reply_feedback(days=1|7)`）：
engaged 率、ambient/silence/negative 计数、`quiet_group` 占比、中位首条接话延迟、
按 `reason_code` 分组的 engaged 率、当前 ema 与样本数（含死区提示）、`unknown` 占比；无数据显示"暂无"。
被点名的回合单独一组展示（标注"不参与系数"）。

## 8. 降级与可观测

- 结算/写库异常 → WARNING + `reply_feedback_failed`，绝不抛进调度器；
- **`unknown` 分支（修订 ⑤）**：窗口内进程重启、群被关、样本不足、过期很久的 pending、
  私聊行 → `unknown`，**绝不落 silence**；重启是最常见的观测缺口，必须显式建模；
- 指标：`reply_outcomes_settled`、`reply_feedback_failed`、`reply_outcome_enqueued`。

## 9. 测试与验收

1. 判定顺序：`addressed_back=True ∧ polarity=-1` → **negative**（修订 ① 的回归）；
2. 极性：词典只产 0/−1；"别刷屏"但非紧邻 → 0；紧邻但无强指向 → 0；
3. 回合级：一个回合只落一行（turn_id 幂等）；私聊行不参与回流；
4. `self_initiated`：被点名回合不改变 ema（系数仍 1.0 或在死区内），自己开的口才改变；
5. EMA：按时间衰减（Δt 翻倍 → 半衰期正确）、持久化与重启续算、样本 < 8 死区恒 1.0；
6. ambient 记 0、negative 只抵消 momentum 不使其变负；
7. 迁移 18 建表/索引；保留期清理；outbox 重放（新 kind）；
8. WebUI 卡片渲染（含"暂无"与死区提示）；
9. **硬门槛回归（前提修订）**：断言的是 **engagement_ema = 0（中性）时**，同参数下冷却/日限/开关
   的行为与今天逐案一致——否则这条测试在软系数生效后自相矛盾。

## 10. 里程碑（每步独立提交 + 独立验收）

1. 迁移 18 + `ReplyOutcome` 模型/存储 + 插件按回合落 `pending`（含 `self_initiated` 计算）
2. 结算任务（窗口扫描 + §4 判定 + `unknown` 分支）+ 单元/端到端测试
3. `SocialEngagement`（时间衰减 + 持久化 + 死区）+ 两处软回流 + 硬门槛回归
4. WebUI 卡片 + `SocialAdminService`
5. 保留期清理 + 指标 + outbox handler 注册 + README `social` 段落补一句
