# v3.0 设计 · Task 22 表达 / 口癖学习（Expression Learning）

> 状态：**设计待确认**（P3 规矩：先设计页，确认后编码）
> 相关代码（拟复用）：`app/memory/embedding.py`（向量）、`app/memory/retrieval.py`（打分骨架）、
> `app/character/context.py`（注入层 + trace）、`app/social/monitor.py`（群消息观测）、
> `app/database/database.py`（迁移链）、`app/web/routes/`（新页面）
> 一句话：**从群里的真人消息学"用词 / 句式"，回复时按会话、限额注入；学到的可停用、可删除、可追溯。**

---

## 0. 权威文本（逐字抄自 `E:\WorkSpace DSH\BOT\CatooBot-vibe-coding-prompt.local.md` 附录 A）

> 上下文压缩会丢，**以本附录为准**。写设计页时请原文抄进设计页开头，不要改写、不要凭记忆重述。
> ① ② 是硬约束（实现不得违反）；③–⑥ 是取舍建议（写进设计页即可）。

### 硬约束

**① 口癖只喂"用词 / 句式层"，不得绕过现有聊天风格限制。**
现有规则是：一个连续段落、默认一句 ≤30 字、最多两句 ≤45 字、不加尾缀、不出列表。
学到的口癖必须在这套限制**之内**生效，否则会与既有出站后处理打架，把她变成复读机。

**② 学到的表达必须可停用、可删除、可在 WebUI 看到来源。**
至少显示：来源群、样本数（几个不同用户、出现几次）、最近一次被用到的时间，并能追溯到原始消息。
它是从别人的话里学来的，属于"可撤回"数据。

### 取舍建议

**③ 只学群内真人消息**：排除她自己、其他机器人、@/引用里的被引文本、纯表情纯符号。

**④ 门槛与隔离**：同一表达需跨 ≥N 个不同用户、或出现 ≥M 次才入库；按群隔离不跨群；给容量上限与淘汰策略。

**⑤ 注入有预算**：每次回复最多注入 K 条、总长度上限；并在检索 trace 里显示"这次用了哪几条"（复用现有 RetrievalTrace 的模式）。

**⑥ 与 Task 20 解耦**：v1 不许用 engagement_ema 去调口癖权重。两个自适应回路叠在一起会无法归因。先只做"学 + 用 + 可关"。

---

## 1. 问题与边界

群里有自己的语言：某个群爱说「有一说一」，另一个群把「了属于是」当标点，还有人习惯用
「狠狠 X 了」。她（罐头）有自己的人设与说话风格，但**不会用任何一个群的"本地话"**——
因为没有任何机制把"这群人怎么说话"喂给她。

**目标**：学"用词 / 句式层"的表达，回复时按**会话所属的群**、在**风格限制之内**、按**预算**注入；
全程可停用、可删除、可追溯来源。

**非目标（v1 明确不做）**：

- **不做人格改写**：不动 `active_persona` / Bible / 说话风格规则；口癖只是"这一句里的用词偏好"。
- **不学具体内容**：不学人名、群名、事件、数字、链接、账号；只学**可复用的短语/句式骨架**。
- **不跨群**：A 群学到的表达绝不注入 B 群（④）。
- **不参与任何行为决策**：不影响"要不要回 / 什么时候回 / 回几条"，也不读 `engagement_ema`（⑥）。
- **不做自学习闭环**：v1 没有"用得好就加权"的机制——只有"学 + 用 + 可关"（⑥）。

## 2. 数据模型

新增表（**迁移 19**；Task 12 的"删 `memories.vector` JSON 列"顺延为迁移 20，其解冻条件不变）：

```
expression_patterns
  id            INTEGER PK
  scope_key     TEXT      -- group:<gid>；v1 只支持群（私聊不学）
  pattern       TEXT      -- 学到的表达本体（做过去噪与脱敏）
  kind          TEXT      -- word | pattern（词 / 句式骨架）
  sample_count  INTEGER   -- 出现次数
  speaker_count INTEGER   -- 不同发言人数量
  first_seen_at / last_seen_at / last_used_at
  use_count     INTEGER
  status        TEXT      -- active | disabled（用户停用）| archived（淘汰/删除）
  created_at / updated_at

expression_samples          -- 来源可追溯（②）：保留原始消息的最小证据
  id            INTEGER PK
  pattern_id    INTEGER   -- FK → expression_patterns
  message_id    TEXT      -- OneBot message_id：能回查原消息
  user_id       TEXT      -- 发言者（脱敏展示用）
  text          TEXT      -- 原始片段（≤80 字，落库前 redact）
  seen_at       INTEGER
  UNIQUE(pattern_id, message_id)

expression_vectors          -- 复用 embedding 服务；与 memory_embeddings 同构（blob + norm，JSON 列不引入）
  pattern_id    INTEGER PK
  model / dimensions / vector_blob / norm / version / created_at
```

`scope_key` 用现有 `group:<gid>` 约定；`UNIQUE(scope_key, pattern)` 保证同群同表达只有一行（④ 的隔离与去重）。

**去噪与准入**（③④，全部在写入前判定，拒绝要有原因码）：

| 情况 | 处理 |
|---|---|
| 她自己的消息 / 其他 bot（`user_id` 命中 `self_id` 或 bot 名单） | 丢弃 `sender_is_bot` |
| 消息以 @ 或引用开头，且被引文本占正文一半以上 | 只取她的话部分，取不到则丢弃 `quoted_only` |
| 纯表情 / 纯符号 / 纯数字 / 纯链接 | 丢弃 `no_lexical_content` |
| 长度 > 12 字（词）或 > 20 字（句式） | 丢弃 `too_long`——只学短表达，天然远离"整句搬运" |
| 含人名/群名/URL/账号/@/数字 | 丢弃 `contains_entity` |
| 命中敏感/攻击性/歧视词表 | 丢弃 `sensitive` |
| 命中现有 `_FORBIDDEN_PATTERNS`（记忆侧的禁忌判定） | 丢弃 `forbidden` |
| 单次发言者的孤立梗（同群只有 1 人用过且 < M 次） | 先入"候选"，不注入 `below_threshold` |

**门槛（④，可配）**：`min_speakers ≥ 2`（跨 ≥N 个不同用户）**或** `sample_count ≥ 3` 才从候选转为 `active`；
`min_speakers=1` 时必须有 `sample_count ≥ M`（默认 M=3），防止一个人的口误变成她的口癖。
**容量与淘汰**：每群上限 `max_patterns_per_group`（默认 80）；超出时按
`score = 0.5·(speaker_count 归一) + 0.3·(sample_count 归一) + 0.2·新近度衰减` 淘汰最低分到 `archived`（不物理删除，可恢复）。

## 3. 学习管线（写入侧）

```
群消息（现有 SocialMonitor 观测流，零新增订阅）
  → 候选切分：按标点/语气词边界切出 2~12 字的片段（词）与固定句式骨架（pattern）
  → 准入过滤（上面的表，逐条记原因码）
  → upsert expr(scope_key, pattern)：sample_count += 1，speaker_count 用 (pattern, user_id) 去重计数
  → 样本落盘 expression_samples（保留 message_id 以便溯源）
  → 门槛满足 → status=active；写向量（异步、失败不阻塞，复用 embedding 的 cache/failure 计数）
  → 播报 `[Expression.Learn] group=<gid> pattern=… kind=… samples=n speakers=m status=…`
```

- 学习是**后台、异步、有额度**的：`expression.learn_max_per_hour`（默认 60，按群），
  超限只记计数不落库；整个链路失败只 WARNING，绝不影响聊天。
- **不学她自己的输出**：即便她在群里说话，`user_id == self_id` 直接丢弃——否则会自我强化。
- 播报走 narrator 的 `mind` 频道（"记住了这个群的说话方式：…"），但**不向群内宣布**（与表情包收藏同规矩）。

## 4. 注入管线（读取侧）

注入点在 `CharacterContextBuilder`，**作为独立层 `expressions`**，与 facts/continuity 同级：

1. 只用**当前会话所属群**的 `active` 表达（私聊、跨群一律不注入）——scope 硬隔离；
2. 候选打分复用现有骨架：关键词重叠 + 向量相似（`math.sumprod`，与记忆检索同一套）+ 新近度；
3. **预算**（⑤）：最多 `inject_max_items = 3` 条、总长度 `inject_max_chars = 24`；超预算截断；
4. 注入形式是**用词提示而不是句子**：

```
这个群的人习惯这样说（只是用词偏好，不是让你复读；仍按你自己的风格说）：
  · 有一说一
  · 狠狠地X
{以上仅供参考，不要为了用而用；不自然就不用}
```

5. **注入不改变任何行为决策**：是否回复、延迟、分段、是否发表情包全部照旧；
6. trace：`context_trace` 增加 `expressions: {injected: [...], scores: {...}, budget: {items, chars}}`，
   WebUI 的检索 trace 页照原样式显示"这次用了哪几条"（⑤）。

**硬约束 ① 的落实方式**（关键）：注入只加"用词偏好"，出站仍**无条件**过既有后处理——
`MessageChunker`（一连续段落 / 概率拆分）、`_STYLE_LIMITS`（≤30 字、≤45 字、不加尾缀、不出列表）、
`audit_claims`（虚假陈述剥离）。**顺序是"注入 → 生成 → 既有后处理"，后处理不因口癖解锁任何豁免**；
并加一条测试：注入极端口癖（含换行、列表符号、超长句式）后，出站消息仍满足全部风格限制。

## 5. 可停用 / 可删除 / 可追溯（硬约束 ②）

- **总开关**：`expression.enabled`（默认 `false`，灰度开启）；关闭后不再学、不再注入，已学数据保留。
- **单条**：WebUI 可 `disabled`（保留数据、不再注入，可恢复）；可**物理删除**（连带 samples 与向量）。
- **WebUI 页面 `/expressions`**（导航与「记忆/社交」同级）：
  - 表格列：表达 | 类型 | 来源群 | 样本数（出现次数 + 不同用户数） | 最近一次用到 | 状态 | 操作（停用/启用/删除）
  - 行展开 → **来源样本**：原始消息片段 + 发言人 + 时间 + message_id（②的"可追溯到原始消息"）
  - 顶部：总开关、阈值与预算编辑（保存即热加载）、每群容量占用
  - 顶部提示语写明数据性质："这些表达是从群友的话里学来的，随时可停用或删除"
- 计数与日志：`expressions_learned / expressions_rejected{reason} / expressions_injected /
  expressions_evicted`，并在 `/memory/health` 风格的"健康度"里给一行（复用现有卡片模式）。

## 6. 配置段 `expression:`

```yaml
expression:
  enabled: false                 # 灰度；关闭 = 不学不注入
  learn_max_per_hour: 60         # 每群每小时最多入库候选
  min_speakers: 2                # 跨 ≥N 个不同用户
  min_occurrences: 3             # 或出现 ≥M 次（min_speakers=1 时必须 ≥3）
  max_patterns_per_group: 80     # 容量上限，超出按分数淘汰到 archived
  inject_max_items: 3            # 每次回复最多注入 K 条
  inject_max_chars: 24           # 注入总长度上限
  groups: []                     # 白名单：为空 = 所有群；填入则只学这些群
```

两个 YAML（`config.yaml` / `config.example.yaml`）与 `overrides.yaml` 走既有热加载机制；
新增配置段必须登记进 `ConfigAdminService` 的页面与 **热加载覆盖审计表**（见 docs/README.md）。

## 7. 里程碑（每步独立提交 + 独立验收）

1. **迁移 19 + 数据层**：三张表 + `ExpressionStore`（upsert/门槛/淘汰/停用/删除/样本溯源）+ 单元测试。
2. **学习管线**：接入群消息观测流 + 准入过滤原因码 + 额度 + 播报；端到端测试（三人用过 → 入库；
   她自己的消息 / 纯表情 / 含链接 → 拒收）。
3. **注入管线**：context 层 + 打分 + 预算 + trace；**风格限制回归测试**（注入后仍 ≤2 句 / ≤45 字 / 无列表）。
4. **WebUI `/expressions`** + 总开关 + 单条停用/删除 + 来源样本展示。
5. **观测与文档**：计数、健康行、README 配置段说明、本设计与实现差异回填。

## 8. 验收（每条可测）

1. **不越权**：注入含换行/列表/超长口癖后，出站仍满足"一连续段落、≤30 字、最多两句 ≤45 字、无尾缀、无列表"；
2. **不跨群**：A 群的表达不出现在 B 群的 prompt（注入 trace 断言 + prompt 文本断言）；
3. **不学自己/机器人/@引用/纯表情**：四类输入全部 `rejected`，并有原因码；
4. **门槛**：同一人重复 N 次不激活（除非 `min_occurrences` 达标且 `min_speakers=1`）；跨 2 人即激活；
5. **可撤回**：WebUI 删除后，prompt 不再出现该表达、向量与样本一并删除（行数与文件核对）；
6. **可追溯**：任一条表达都能列出 ≥1 条原始消息（含 message_id 与发言人）；
7. **预算**：注入条数与字数不超上限（trace 断言）；
8. **与 Task 20 解耦**：把 `engagement_ema` 设成极端值，注入权重与选择结果**不变**（⑥）；
9. **失败不影响聊天**：学/注入链路抛错时，回复照常（端到端 + 故障注入测试）；
10. **热加载**：`expression.enabled` 与阈值在 WebUI 保存后立即生效（配一条与时刻无关的测试）。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 把她变成"群里的复读机" | 硬约束 ①：注入只是用词提示 + 出站后处理不豁免 + 预算 3 条/24 字 + "不自然就不用"指令 |
| 学进攻击性/敏感表达 | 敏感词表 + `_FORBIDDEN_PATTERNS` + 可用 WebUI 停用；数据可撤回 |
| 学进人名/群名/链接（隐私） | `contains_entity` 硬拒绝；样本落库前 `redact` |
| 单群话语权被一个人带偏 | `min_speakers ≥ 2` 默认；`speaker_count` 参与淘汰打分 |
| 与 Task 20 的自适应回路互相干扰 | ⑥：v1 完全解耦，不读 engagement_ema |
| 数据膨胀 | 每群容量上限 + 淘汰到 `archived` + 学习额度 |
