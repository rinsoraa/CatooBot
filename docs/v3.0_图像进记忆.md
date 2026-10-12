# v3.0 设计 · 图像进记忆（Task 21）

> 状态：**里程碑 ① ② ③ 已实现**。③ `query_image_memory` 已按"解冻"做成**只读**内置工具
> （复用 `MemoryManager.list_memories(source='vision')`，不新增 SQL/DB 路径、不重复看图/入库）。
> 触发条件未满足（真库 `source=vision` 行数为 0），工具作为可选能力存在，不强制走它。
> 相关代码：`app/media/vision.py`、`app/media/memory_bridge.py`、`app/memory/manager.py`、
> `app/tools/builtins/query_image_memory.py`、`app/memory/model.py`（`SOURCES`）

## 1. 问题

她"看懂"的每张图只活在两个地方：`image_analysis` 的 sha256 缓存，和那一轮的 prompt。
事后她**无法回忆"我看过什么"**——用户过两天问"我发的那个图里写了啥"，她只能重新看图
（若图已失效则彻底不知道）或干脆编造。而图片理解的结果（摘要/物体/场景/文字）本来就已经算出来了。

## 2. 目标与非目标

**目标**：把**有信息量**的图片理解结果写成一条**可检索的回合级记忆**，让"看过什么"能自然被想起来。

**非目标（明确不做）**：
- 表情包不进记忆：贴纸是**素材**，归 `StickerLibrary`（v1.1 的边界"普通 image ≠ sticker"保持不变）；
- 不做以图搜图 / 图像向量索引：检索走**文字摘要**（现有混合检索即可），不引入新的向量空间；
- 不把每张图都变成记忆：准入规则见 §3，宁可少记；
- 本次不新增 `query_image_memory` 工具（列入 §9 的可选第三步）；
- 图片记忆**不参与** Task 20 的回复效果回流（她"看图"不是她开口），也**不进** `facts.py` 的世界事实
  （照片不是世界状态，避免被当成"真实世界事实"注入）。

## 3. 准入规则（结构化，全部可测）

只对 `media_type == "image"`（含被视觉判定**不是**表情包的照片）考虑写入。
表情包（`sticker`）一律不写，无论内容多有意思——它已经在表情库里。

**信息量门槛**：`summary` 非空，且至少满足其一：
`ocr_text` 非空 ／ `people_count > 0` ／ `len(objects) >= 2` ／ `scene` 非空且 `confidence >= 0.6`。
不满足 → 只留缓存，不写记忆（一张模糊的风景照不值得一条记忆）。

**回合级合并**：同一回合里的多张图**合成一条**记忆（与 Task 20 的"一回合一行"一致）：
`（发来 3 张图）第一张：…；第二张：…`（最多 3 张，超出只记数量）。

**指纹去重**：记忆内容里带 `[img:<sha256 前 16 位>]`，写入前用现有 `find_same`（content_hash）
与指纹双保险；同一张图重复发送**不产生多条记忆**（内容 hash 因指纹相同而命中）。

## 4. 写入什么

- `content`：`看过一张图片：{summary}；场景：{scene}；包含：{objects}；文字：{ocr}`
  ——直接复用 `VisionResult.as_text()` 的既有格式（**不新增编造空间**，与 v1.1 的视觉叙述同源）；
- `category="event"`、`layer="episodic"`、`event_at=now`、`summary={summary}`；
- `source="vision"`：**新增一个 source 值**（登记进 `Memory.SOURCES`，WebUI 可按它过滤/审计）；
- `confidence = min(0.8, 0.4 + vision.confidence × 0.4)`：视觉不确定的记忆不该显得确凿；
- `importance = 0.3 + 0.1 × min(3, 信息量点数)`（0.3~0.6，永不与用户明说的偏好竞争）；
- **不写**：raw bytes、URL、`raw_response`、模型 thinking（沿用 §"不保存思维链"）。

**写入者**：回合运行时之后、由插件在"这轮结束"时统一提交（与 `_post_reply` 同一位置），
scope 用会话 key（`user:x` / `group:y`）——她只在**收到图的会话**里记得这张图。

## 5. 检索

**不新增检索通道**：内容里天然含实体词（"看过一张图片：…可乐…"），关键词（FTS5）与语义两路都能命中，
现有 `retrieve_for_session` 直接可用。要做的是**验证**（§8 的召回测试）。

WebUI：记忆页筛选器新增 `source=vision` 选项（`list_memories(source=...)` 已支持按 source 过滤？实现时确认，
不支持则补一行 WHERE），详情页沿用现有"来源/关系"展示。

## 6. 与既有体系的一致性

| 体系 | 关系 |
|---|---|
| 贴纸库边界 | 不动：`sticker` 仍只进 `StickerLibrary`，照片永远不是贴纸 |
| 视觉缓存 | 复用：`image_analysis` 仍是唯一"看图"入口，记忆只是它的**衍生**（不重复调用模型） |
| Task 14 outbox | 复用 `kind="remember"`（已注册 handler），DB 写失败自动入队重放，**不新增 kind** |
| Task 20 回流 | 不参与：图片记忆不是"她开口"，不进 `engagement` |
| 世界事实 | 不进：照片内容不是世界状态，`FactSelector` 不读它 |
| 去重/冲突/配额 | 交给现有 `remember()`（近重复、冲突、配额逻辑一律复用） |

## 7. 失败与降级

- 视觉不可用/分析失败 → **不写记忆**（缓存里是空结果，没有可写的内容），已有 WARNING 保持；
- 记忆写入失败 → outbox 入队 + WARNING（`kind="remember"`），恢复后重放；
- 配额触发（某 scope 超过 `max_active_per_user`）→ 由既有配额逻辑归档最不重要的，行为不变。

## 8. 测试与验收

1. 准入：表情包不写；`summary` 为空不写；只有模糊场景且 confidence 低不写；命中任一信息量条件才写；
2. 回合级合并：一回合 3 张图 → **一条**记忆且含 3 张的摘要；超过 3 张只记数量；
3. 指纹去重：同一张图（同 sha256）发两次 → 仍只有一条记忆；
4. 召回：发过一张有文字的图 → 之后问"那张图写了什么" → `retrieve_for_session` 命中该记忆
   （断言召回的 content 含图里的关键词）；
5. scope 隔离：A 会话的图不出现在 B 会话的召回里；
6. 视觉失败不写；DB 挂掉走 outbox（复用既有 outbox 测试模式）；
7. `source="vision"` 进入 `SOURCES` 且 WebUI 记忆页可按它过滤。

## 9. 里程碑（每步独立提交 + 独立验收）

1. 写入路径：`VisionResult` → 回合级 episodic 记忆（准入 + 合并 + 指纹 + `source="vision"`）+ 上述 1~3、6 的测试
2. WebUI：记忆页 `source=vision` 筛选 + 详情展示（含 7 的测试）
3. **延后，且带触发条件**：`query_image_memory` 工具（让 agent/角色显式检索"我看过的那张图"）。
   只有满足**任一**触发条件才做，否则不做（自然召回已经够用，多一个工具只会多一条绕过审计的 DB 访问路径）：
   - 图片记忆已大量入库（`source=vision` 的行数可观）却**从未**出现在任何一次召回的命中里；
   - 实际对话里出现"她答不出你上次发的那张图"（可复现的失败案例）；
   - 用户明确要求她**主动回看**图片（而不是被自然想起）。

   真做时的两条硬约束：
   - **只读**，且复用现有检索与权限（与 `query_memory` 同级、同一套 scope 过滤与白名单），**不新增 DB 访问路径**；
   - **绝不绕过** media 的图片缓存与 sha256 去重（查"那张图"时先走 `image_analysis` 缓存与指纹，
     不重复调用视觉模型、不重复入库）。
