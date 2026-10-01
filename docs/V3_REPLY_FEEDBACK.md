# v3.0 设计 · 回复效果闭环（Task 20）

> 状态：**设计待确认**（按任务书 P3 的规矩，先交设计页，确认后再编码）
> 相关代码：`app/social/`（attention / policy / cognition）、`app/response/delivery.py`、
> `plugins/chat/plugin.py`（`_post_reply` / `_turn_finished`）、`app/behavior/scheduler.py`

## 1. 问题

今天她的社交决策只发生在**说之前**：`SocialCognitionEngine.decide` 用结构化规则判断"要不要接话"，
`ParticipationPolicy` 用冷却/日限/额度管住频率，`SocialAttention` 记 momentum/疲劳。
但**她说完之后，系统一无所知**——没人接话、被无视、被人说"别刷屏"，都不会改变她下次的判断。
结果是：她可能在安静群里越说越少，也可能在嫌弃她的群里越说越多。

## 2. 目标与非目标

**目标**：把"她说的每条消息之后发生了什么"变成可观测、可查询、可（软）回流的信号。

**非目标（明确不做）**：
- 不引入 `random() < p`、不做 bandit/探索 —— 决策仍是 gate + 评分 + 阈值（仓库红线）。
- 不改变任何硬门槛语义（冷却、日限、参与开关、`participation_probability` 的额度累积）。
- 不把"没人接话"直接当成"她说错了"（见 §4 的区分规则）。
- 不做逐条消息的回复率 KPI 追求——她是个角色，不是运营工具。

## 3. 观测模型

`ReplyOutcome`（一条她说出去的消息 → 一次结算）：

| 字段 | 含义 |
|---|---|
| `scope_key` | `group:<id>` 或 `user:<id>`（私聊只统计，不回流） |
| `message_id` | 她发出的消息 id（来自 `MessageDelivery.last_sent_ids`） |
| `sent_at` | 说出时间 |
| `reason_code` | **她为什么开口**（`SocialDecision.reason_code`：direct_mention / reply_to_bot / direct_follow_up / topic_interest / participation_rate / initiative …）——这是闭环的核心维度 |
| `window_seconds` | 观察窗（默认 90s，可配） |
| `replies` | 窗口内**别人**发的消息条数（同会话） |
| `first_reply_after` | 第一条别人消息的延迟（秒；无则 NULL） |
| `addressed_back` | 窗口内是否有人 @ 她 / 回复她 / 戳她（布尔） |
| `polarity` | 窗口内接话的极性：+1 友好 / 0 中性 / -1 排斥（保守词典 + 现有情感推断；判不准记 0） |
| `baseline` | 该群最近 30 分钟的**人均消息速率**（用于区分"安静群"与"被无视"，见 §5） |
| `verdict` | `engaged` / `ambient` / `silence` / `negative` / `unknown`（判定规则见 §5） |

## 4. 判定规则（无 LLM，纯规则）

给定窗口观察与基线：

1. `addressed_back` 为真 → **engaged**（有人明确回应她）；
2. 否则若 `replies ≥ 1` 且 `polarity < 0` → **negative**（有人在排斥）；
3. 否则若 `replies ≥ baseline_expected`（窗口内按 baseline 预期的最低条数，默认 max(1, round(rate × W))）→ **ambient**（群在聊，只是没针对她）；
4. 否则 → **silence**，并附带 `quiet_group = True/False`（该群本窗口本来就低于预期速率时为真）。

> 关键点：`silence + quiet_group` 视为"中性"（群本身就安静），只有 `silence` 且群本来热闹、
> 或 `negative`，才计入负信号。这条规则进代码注释，也会有测试。

## 5. 存储与清理

- **迁移 18**：新表 `reply_outcomes`（上述字段；索引 `(scope_key, sent_at DESC)`、`(verdict, sent_at DESC)`）。
- **结算任务**：挂在共享调度器上（不新建循环），每 30s 扫一次"已过观察窗但未结算"的行——为控制开销，
  说出的每条消息**先落一行 `pending`**（轻量：id/scope/reason/sent_at/window），结算时补全其余字段。
- **保留期**：`social.feedback.retention_days`（默认 30），由调度器每日清理（与 `ai_usage` 同一模式）。
- **写失败**：复用 Task 14 的 outbox（`kind="reply_outcome"`），绝不静默丢。

## 6. 回流（只动软状态）

新增 `SocialEngagement`（沿用 `SocialAttention` 的形态：内存态 + 衰减，可快照）：

- 每个群维护 `engagement_ema`（指数滑动平均，半衰期默认 7 天）：
  `engaged=+1`、`ambient=+0.25`、`silence+quiet=0`、`silence(热闹群)=−0.25`、`negative=−1`。
- **接入点**：
  - `SocialAttention.note_reply_outcome(group_id, score)` —— 作为 momentum 的第二个来源（权重低，
    现有 `note_mention/note_thread` 语义不变）；
  - `ParticipationPolicy.credit_participation` 的**额度累积速率**乘以 `0.9 ~ 1.1` 的软系数（由 ema 线性映射）
    —— 即"她最近说得有人接，就稍微多说一点；总被无视，就稍微收敛"，但**冷却/日限/开关一律不变**。
  - `SocialDecision.reason_code` 的统计只用于**展示与调试**，不改变判定分支。
- 私聊：只记录不回流（私聊没有"群氛围"概念）。

## 7. WebUI

`/social` 新增只读卡片「她最近说得怎么样」（`SocialAdminService.reply_feedback(days=1|7)`）：

- engaged 率、ambient/silence/negative 计数、`quiet_group` 占比（用于自证"沉默多为群安静"）；
- 中位首条接话延迟；
- 按 `reason_code` 分组的 engaged 率（回答"哪种开口方式最有效"）；
- 明确标注样本量与窗口长度；无数据显示"暂无"。

## 8. 降级与可观测

- 结算任务异常 → WARNING + `reply_feedback_failed` 指标，绝不抛进调度器；
- 无基线数据（新群）→ `baseline=0`，`verdict` 只在有接话/被回应时给结论，否则 `unknown`；
- 指标：`reply_outcomes_settled`、`reply_feedback_failed`、`reply_outcome_enqueued`。

## 9. 测试与验收

1. 单元：判定规则（engaged / ambient / silence+quiet / silence+热闹 / negative）、`quiet_group` 边界、
   ema 衰减与半衰期、软系数映射（0.9~1.1 且不越界）；
2. 端到端：她说一句 → 群里有人接话 → 落库 `engaged` 且 ema 上升；群里没人说话且本来安静 →
   `silence+quiet` 且 ema 不变；有人说"别刷屏" → `negative` 且 ema 下降；
3. 迁移 18 建表 + 索引；保留期清理；
4. WebUI 卡片渲染（含无数据路径）；
5. 硬门槛回归：冷却/日限/开关的行为与今日完全一致（同参数同结果）。

## 10. 里程碑（每步独立提交 + 独立验收）

1. 迁移 18 + `ReplyOutcome` 模型 + 落 `pending`（插件 `_post_reply` 处一行调用）
2. 结算任务（窗口扫描 + 判定规则）+ 单元/端到端测试
3. `SocialEngagement` + 两处软回流 + 回归测试（硬门槛不变）
4. WebUI 卡片 + `SocialAdminService`
5. 保留期清理 + 指标 + 文档（README `social` 段落补一句）
