# CatooBot v1.1 — Multimodal Message + Autonomous Sticker Runtime

## v1.1 §0 任务目标

你现在负责对现有 CatooBot 项目进行一次完整的 v1.1 功能升级。

当前项目已经完成：

- v0.1 QQ / OneBot 11 / NapCat Adapter
- v0.2 AI Engine / Provider / ModelRouter
- v0.3 Persona / Memory / Relationship / WebUI
- v0.4 Character Behavior Engine
- v0.5 Semantic Memory
- v0.6 Tool Runtime
- v0.7 Agent Runtime
- v0.8 Persistent World / Background Life
- v0.9 Social Cognition / Group Autonomous Participation
- v1.0 Episode-based World Activity Model

本次 v1.1 的目标不是重新设计 CatooBot，而是在现有架构上增加：

1. 多模态消息理解
2. 普通图片视觉识别
3. QQ 表情 / 商城表情识别
4. Bot 自主收藏用户发送的表情包
5. Bot 自己维护、理解和检索表情包库
6. Bot 自主决定是否使用表情包
7. Bot 自主选择具体使用哪一个表情包
8. 手动导入表情包后自动识别与建立索引
9. 每次启动自动扫描新增加或变化的表情包，但不得重复分析未变化的文件
10. WebUI 表情包管理与调试
11. 与 v0.9 Social Cognition / v1.0 World Activity 兼容
12. 保持 QQ 侧“角色世界”定位，不增加任何公开管理命令

---

# v1.1 §1 首先必须阅读现有代码

开始修改之前，不要直接写代码。

先完整检查当前项目：

- OneBot Adapter
- Message Parser / Normalizer
- AIEngine
- Provider
- ModelRouter
- ConversationContext
- Character Runtime
- Character Response Planner
- Social Cognition
- World Activity Runtime
- World Activity Context
- Tool Runtime
- Agent Runtime
- Memory Runtime
- Database / SQLite
- WebUI
- Logger
- Config
- EventBus
- Scheduler
- Tests

重点找出：

1. 当前 OneBot 11 message segment 的解析路径
2. text / image / face / mface 当前会如何进入 CatooBot
3. 当前消息对象 / MessageContext 的实际数据结构
4. 当前 Response Planner 如何生成回复
5. 当前 OneBot Sender 如何发送消息
6. 当前 AI Provider 是否已经支持 image_url / multimodal content
7. 当前 WebUI 路由和组件结构
8. 当前 SQLite migration 机制
9. 当前 v0.9 Social Cognition 如何读取当前消息
10. 当前 v1.0 World Activity Context 如何暴露当前活动
11. 当前 Character Runtime 最终回复出口在哪里
12. 当前是否已经存在媒体下载 / 缓存机制

不得假设代码结构与文档描述完全一致。

必须以当前仓库真实代码为准。

先形成一份内部实现报告：

- Existing Message Flow
- Existing AI Flow
- Existing Response Flow
- Existing WebUI
- Existing Database
- Integration Points
- Potential Conflicts
- Required Migrations

确认现有架构之后再修改。

---

# v1.1 §2 核心设计原则

本次升级必须遵守以下原则。

## v1.1 §2.1 普通图片与表情包必须是两个不同的概念

系统内部明确区分：

- native_face
- sticker
- image
- unknown_media

绝对不能把“图片”直接等同于“表情包”。

---

## v1.1 §2.2 普通 image 不得自动进入 Sticker Library

这是硬规则。

收到普通：

```text
type = image
```

并且不存在明确的 QQ 表情元数据时：

- 可以进行视觉分析
- 可以进行 OCR
- 可以生成图片描述
- 可以提供给 Character Runtime
- 可以进入 ImageAnalysisCache

但是：

不得自动保存到 Sticker Library。

即：

```text
普通图片
→ Image Understanding
→ 不进入 Sticker Library
```

---

## v1.1 §2.3 明确的 QQ 表情必须识别为 Sticker Candidate

以下情况可作为高置信 Sticker Candidate：

- type = mface
- type = face
- type = image 且存在：
  - emoji_id
  - emoji_package_id
  - key
  - 或其他明确 QQ 商城表情字段

不要简单根据 `type == image` 判断。

NapCat / OneBot 当前可能把商城表情转成包含表情元数据的 image，因此必须保留这些字段。

---

## v1.1 §2.4 手动导入表情包属于明确的 Sticker Source

用户主动将文件放进指定 Sticker Library / Import Directory 时：

这些文件已经被用户明确标记为表情包来源。

因此可以进入：

```text
StickerLibraryIndexer
```

进行：

- 文件识别
- 去重
- 视觉分析
- OCR
- 情绪分析
- 标签生成
- 语义描述
- 使用场景分析
- Sticker embedding / semantic index
- 质量检查

---

# v1.1 §3 新增总体架构

新增：

```text
MediaRuntime
```

下面分为：

```text
MediaRuntime
├── MessageMediaNormalizer
├── MediaCache
├── ImageUnderstandingRuntime
└── StickerRuntime
    ├── StickerDetector
    ├── StickerAcquisitionEngine
    ├── StickerLibrary
    ├── StickerLibraryIndexer
    ├── StickerAnalyzer
    ├── StickerRetriever
    ├── StickerSelector
    ├── ExpressionDecisionEngine
    └── StickerSender
```

注意：

不要把这些功能直接堆进 MessageHandler。

必须保持模块化。

---

# v1.1 §4 Media Normalizer

新增统一媒体标准结构。

建议：

```python
MediaContent
```

至少包含：

```text
media_id
media_type
source_type
file
url
mime_type
file_size
sha256
width
height
duration
is_animated

emoji_id
emoji_package_id
emoji_key
emoji_summary

source_message_id
source_user_id
source_group_id
created_at
```

其中：

```text
media_type:
    image
    sticker
    native_face
    unknown
```

source_type 可以：

```text
qq_image
qq_mface
qq_face
manual_import
```

注意不要丢失 NapCat 的原始字段。

---

# v1.1 §5 Image Understanding Runtime

新增：

```text
ImageUnderstandingRuntime
```

职责：

- 获取图片
- 缓存图片
- 校验格式
- 必要时压缩 / 转换
- 调用视觉模型
- OCR
- 场景识别
- 对象识别
- 图片描述
- 情绪 / 氛围识别
- 文字识别
- 上下文理解
- 结果缓存

---

## v1.1 §5.1 输出结构

建议：

```json
{
  "image_id": "img_xxx",
  "summary": "...",
  "objects": [],
  "scene": "",
  "ocr_text": [],
  "people_count": 0,
  "actions": [],
  "mood": "",
  "visual_tags": [],
  "confidence": 0.0
}
```

不得保存隐藏 chain-of-thought。

只保存结构化视觉结果。

---

# v1.1 §6 Vision Provider

不要创建一个新的完全独立 AI Provider。

复用现有：

```text
AIEngine
Provider
ModelRouter
```

增加：

```text
VisionModelCapability
```

例如：

```python
supports_text = True
supports_image = True
supports_audio = False
supports_video = False
```

Provider 需要能够判断某个模型是否支持视觉。

---

## v1.1 §6.1 Vision Request

统一抽象：

```python
VisionRequest
```

包含：

```text
images
text_context
system_instruction
detail
model
timeout
```

Provider 层负责转换成实际 API 请求。

不要把 SiliconFlow 特有请求格式泄漏到 Character Runtime。

---

# v1.1 §7 Sticker Asset System

新增独立的：

```text
StickerLibrary
```

它不是 Memory。

不要把表情包资产存进 Long-term Memory。

Sticker Library 是：

```text
Character Asset
```

不是：

```text
Memory
```

---

# v1.1 §8 Sticker 数据模型

建议创建：

```text
sticker_assets
```

至少包含：

```text
id
file_path
file_name
mime_type
file_size
sha256
phash
width
height
is_animated

origin
origin_user_id
origin_group_id
origin_message_id

emoji_id
emoji_package_id
emoji_key

visual_summary
ocr_text

emotion_tags
intent_tags
scene_tags
style_tags
general_tags

intensity
humor
expressiveness
reusability
novelty

quality_score
safety_status

analysis_version
analysis_model
analyzed_at

embedding_version
embedding_status

usage_count
last_used_at
last_selected_at

created_at
updated_at

status
```

status：

```text
active
disabled
duplicate
archived
invalid
```

---

# v1.1 §9 Sticker Acquisition Engine

新增：

```text
StickerAcquisitionEngine
```

它负责判断：

> 用户发来的这个 Sticker，CatooBot 是否值得收藏？

绝不能设计成：

```python
if sticker:
    save()
```

---

# v1.1 §10 自主收藏决策

增加：

```text
StickerAcquisitionEvaluator
```

输入：

```text
当前消息
当前对话上下文
当前用户关系
当前话题
用户最近使用情况
当前角色人格
角色当前情绪
当前世界活动
现有 Sticker Library
候选 Sticker 信息
```

输出结构化决策：

```json
{
  "decision": "save",
  "confidence": 0.91,
  "novelty": 0.86,
  "reusability": 0.91,
  "expressiveness": 0.93,
  "reason_codes": [
    "high_reusability",
    "novel_expression",
    "fits_character_style"
  ]
}
```

允许：

```text
save
reject
defer
```

---

# v1.1 §11 收藏判断必须考虑已有表情包

例如现有：

```text
无语猫 01
无语猫 02
无语猫 03
无语猫 04
```

新收到：

```text
无语猫 05
```

即使内容不错，也要能够判断：

```text
duplicate / low novelty
```

而不是继续无限收集。

---

# v1.1 §12 表情包语义分析

每个 Sticker 都应该被视觉模型分析。

至少提取：

```text
visual_summary
emotion
intent
tone
intensity
humor
expressiveness
scene
style
OCR
tags
```

例如：

```json
{
  "visual_summary": "一只猫瘫倒在地上，一脸生无可恋",
  "emotion": ["无奈", "疲惫"],
  "intent": ["吐槽", "表达无语"],
  "tone": ["轻松", "夸张"],
  "intensity": 0.77,
  "humor": 0.72,
  "expressiveness": 0.91,
  "tags": [
    "猫",
    "无语",
    "生无可恋",
    "吐槽"
  ]
}
```

---

# v1.1 §13 表情包语义索引

复用已有 Semantic Memory 的 EmbeddingProvider 抽象，但：

不要把 Sticker Asset 混进普通 Memory 表。

为 Sticker 建独立 namespace：

```text
sticker_asset
```

如果当前 embedding provider 只有文本 embedding：

可以对：

```text
visual_summary
emotion
intent
tags
scene
```

拼接后进行文本 embedding。

如果未来存在可靠的 image embedding provider：

允许增加：

```text
image_embedding
```

但不要因为暂时没有 image embedding 而阻塞 v1.1。

---

# v1.1 §14 Sticker Library Indexer

新增：

```text
StickerLibraryIndexer
```

职责：

启动时扫描 Sticker Import Directory。

例如：

```text
data/stickers/
```

流程：

```text
scan
 ↓
file discovered
 ↓
sha256
 ↓
lookup
 ↓
new / modified?
 ↓
analyze
 ↓
index
```

---

# v1.1 §15 启动扫描必须是增量的

每次 Bot 启动：

必须自动扫描。

但：

“扫描” ≠ “全部重新调用视觉模型”。

规则：

```text
文件未变化
→ 跳过视觉分析

新文件
→ 分析

文件内容变化
→ 重新分析

analysis_version 变化
→ 根据配置重新分析

model/prompt version 变化
→ 可标记 stale
```

必须通过：

```text
sha256
analysis_version
analysis_model
prompt_version
```

判断。

---

# v1.1 §16 启动扫描不得阻塞 CatooBot 正常上线

启动流程：

```text
CatooBot Core Start
        ↓
QQ Connection Ready
        ↓
Character Runtime Ready
        ↓
WebUI Ready
        ↓
StickerLibraryIndexer Background Job
```

后台慢慢处理。

不能因为：

```text
1000 个 GIF
```

导致：

```text
QQ 无法连接
```

---

# v1.1 §17 Indexer 状态

WebUI 应显示：

```text
待分析：17
分析中：2
已完成：481
失败：3
重复：26
```

允许：

```text
重新分析失败
重新建立索引
暂停扫描
继续扫描
重新分析过期版本
```

---

# v1.1 §18 Sticker Acquisition 不应阻塞回复

例如：

用户发：

```text
[mface]
```

不能：

```text
先分析 5 秒
↓
分析完成
↓
Bot 才开始回复
```

正确：

```text
收到消息
 ↓
快速识别 Sticker Candidate
 ↓
进入正常 Character Runtime
 ↓
正常回复
 ↓
后台进行收藏判断
```

收藏是后台学习行为。

---

# v1.1 §19 用户表情包的保存流程

收到用户 Sticker：

```text
OneBot Event
 ↓
Normalize
 ↓
Detect Sticker
 ↓
保存原始媒体到 Media Cache
 ↓
快速进入 Character Runtime
 ↓
后台：
    StickerAcquisitionEvaluator
         ↓
      save?
       ├── no
       ├── defer
       └── yes
              ↓
       StickerAnalyzer
              ↓
       Deduplication
              ↓
       Library Insert
              ↓
       Semantic Index
```

---

# v1.1 §20 必须保留来源信息

用户发来的表情包如果进入自己的 Sticker Library：

应该保留：

```text
origin = user_message
origin_user_id
origin_group_id
origin_message_id
created_at
```

但这些来源信息：

- 只用于管理与审计
- 不自动暴露给其他普通 QQ 用户
- 不在角色回复中提及“我从某某那里偷到了这个表情”
- 不进入普通 Long-term Memory
- WebUI 可查看

---

# v1.1 §21 Sticker 去重

至少使用：

```text
SHA-256
Perceptual Hash
Semantic Similarity
```

三层去重。

### SHA-256

完全相同文件：

直接判定重复。

### pHash

轻微压缩 / 缩放 / 格式变化：

判断视觉近似。

### Semantic Similarity

内容高度接近：

作为低 novelty 的参考。

不要把语义相似直接当成删除条件。

因为：

两个都是：

```text
猫猫无语
```

也可能有不同表达价值。

---

# v1.1 §22 Sticker Library 必须有“人格偏好”

Sticker 不是一个纯技术素材库。

建立：

```text
Character Sticker Preference
```

可以记录：

```text
偏爱的视觉风格
偏爱的动物
偏爱的夸张程度
偏爱的表达方式
常用情绪
不喜欢的类型
```

例如：

```text
cute = high
cat = high
dog = medium
abstract = medium
aggressive = low
formal = low
```

不要硬编码这些具体值。

应该允许角色通过实际使用逐渐形成偏好。

---

# v1.1 §23 Expression Runtime

新增：

```text
ExpressionRuntime
```

职责：

判断：

> 当前情况下，CatooBot 是否应该使用表情包？

注意：

它不是每条消息都必须调用。

可以根据当前对话场景决定是否需要进行表达决策。

---

# v1.1 §24 Expression Decision

建议输出：

```json
{
  "response_mode": "text_and_sticker",
  "sticker_intent": "amused",
  "emotion": "开心",
  "intensity": 0.68,
  "selection_required": true
}
```

允许：

```text
text
text_and_sticker
sticker_only
```

不要使用：

```text
random.random() < 0.3
```

作为核心逻辑。

---

# v1.1 §25 Expression Decision 输入

必须考虑：

```text
当前用户消息
当前对话历史
当前话题
用户情绪
用户关系
角色人格
角色当前 mood
角色当前 energy
角色当前 focus
World Activity Context
当前正在进行的 ActivityEpisode
Social Cognition Context
最近是否使用过表情
当前频道 / 群聊场景
```

---

# v1.1 §26 World Activity Compatibility

不要破坏 v1.0 World Activity。

Expression Runtime 可以读取：

```text
primary_activity
activity_detail
location
mood
energy
focus
secondary_context
interaction_overlay
```

例如：

角色当前：

```text
primary_activity = watching_show
```

用户发搞笑表情：

可以自然地回复：

```text
哈哈哈哈
[猫猫笑]
```

但不能因为发送表情而修改：

```text
primary_activity = chatting
```

除非现有 Activity Runtime 本身认为角色真的停止了原活动。

---

# v1.1 §27 Social Cognition Compatibility

不要重写 v0.9 Social Cognition。

只增加：

```text
ExpressionContext
```

例如：

```text
should_participate
social_intent
topic
user_emotion
relationship_state
activity_relevance
expression_opportunity
```

Group Cognition 最终决定：

```text
是否参与
```

Character / Expression Runtime 决定：

```text
参与后是否使用 Sticker
```

即：

```text
Social Cognition
     ↓
Should Respond?
     ↓
Character Response Planner
     ↓
Expression Decision
     ↓
Sticker Selection
```

不要让 Sticker Runtime 绕过 Social Cognition 强行在群聊发消息。

---

# v1.1 §28 Sticker Selector

新增：

```text
StickerSelector
```

输入：

```text
desired_emotion
desired_intent
tone
intensity
current_topic
user_emotion
relationship
character_preference
world_activity
```

检索：

```text
candidate retrieval
 ↓
semantic similarity
 ↓
emotion matching
 ↓
intent matching
 ↓
style preference
 ↓
novelty
 ↓
usage frequency
 ↓
cooldown
 ↓
social suitability
 ↓
final candidate
```

---

# v1.1 §29 Sticker Selector 不得直接使用普通图片

StickerSelector 的数据源只能来自：

```text
StickerLibrary
NativeFaceRegistry
```

不能查询：

```text
ImageAnalysisCache
```

不能查询：

```text
普通用户图片
```

因此从架构上保证：

```text
普通图片绝不会被自动选作回复表情包。
```

---

# v1.1 §30 Native Face

支持 QQ：

```text
face
```

可以作为低成本 Expression Candidate。

建立：

```text
NativeFaceRegistry
```

记录：

```text
face_id
display_name
emotion
intent
tags
```

但不要要求所有 QQ 原生表情都保存为本地文件。

---

# v1.1 §31 MFace

支持：

```text
mface
```

保留：

```text
emoji_id
emoji_package_id
key
summary
```

如果能够稳定复用原始 QQ 表情信息：

优先使用原生 mface 发送。

如果原生元数据不可复用：

使用已经缓存的本地 Sticker Asset。

必须做 Sender Capability 检测，不要假设所有账号 / NapCat 版本行为完全一致。

---

# v1.1 §32 Sticker Sender

新增：

```text
StickerSender
```

负责：

```text
本地 sticker
native face
mface
```

并统一转化成现有 OneBot message segment。

禁止 Character Runtime 直接拼：

```json
{
  "type": "image"
}
```

所有媒体发送必须走：

```text
StickerSender
```

然后：

```text
OneBot Adapter
```

---

# v1.1 §33 响应结构

不要再让 Character Runtime 只返回：

```python
str
```

逐渐支持结构化 Response：

```python
ResponsePlan
```

例如：

```json
{
  "blocks": [
    {
      "type": "text",
      "text": "哈哈哈哈你这也太离谱了"
    },
    {
      "type": "sticker",
      "sticker_id": "sticker_00127"
    }
  ]
}
```

也允许：

```json
{
  "blocks": [
    {
      "type": "sticker",
      "sticker_id": "sticker_00127"
    }
  ]
}
```

不要强迫文字存在。

---

# v1.1 §34 表情包发送冷却

要有合理的：

```text
per-user cooldown
per-group cooldown
global cooldown
```

但不能简单变成：

```text
每 60 秒只能发一次
```

需要根据：

```text
conversation intensity
expression type
group context
direct conversation
```

动态判断。

直接私聊可以更自由。

高活跃群聊要更谨慎。

---

# v1.1 §35 防止连续发送同一个表情

必须维护：

```text
recent_sticker_history
```

避免：

```text
哈哈
[同一张猫]
哈哈
[同一张猫]
哈哈
[同一张猫]
```

除非角色明确有特殊情境。

---

# v1.1 §36 表情使用统计

记录：

```text
usage_count
last_used_at
success_count
rejected_count
selection_count
```

可以进一步统计：

```text
最常用
最少使用
高命中率
低命中率
常被选中但因冷却无法发送
```

用于后续优化 Sticker Selector。

---

# v1.1 §37 WebUI

增加：

```text
表情包库
```

至少包括：

### Library

显示：

- 缩略图
- 文件名
- 情绪
- 标签
- 来源
- 使用次数
- 最近使用
- 状态

支持：

```text
搜索
筛选
标签过滤
情绪过滤
删除
禁用
重新分析
重新索引
查看详情
```

---

# v1.1 §38 Sticker Import

WebUI 支持：

```text
上传文件
上传文件夹
```

上传后：

```text
queued
↓
indexing
↓
analyzing
↓
ready
```

---

# v1.1 §39 WebUI 调试页

新增：

```text
Expression Debugger
```

能够输入：

```text
用户消息
用户情绪
当前话题
角色状态
```

然后模拟：

```text
Expression Decision
```

显示：

```text
是否使用表情
为什么
目标情绪
候选表情
最终选择
淘汰原因
cooldown
similarity
```

但：

**模拟器绝不能真实发送 QQ 消息。**

---

# v1.1 §40 Sticker Acquisition Debugger

WebUI 可以查看：

```text
用户发来的表情
 ↓
是否识别为 Sticker
 ↓
是否允许收藏
 ↓
模型判断
 ↓
去重
 ↓
最终决策
```

例如：

```text
Candidate
confidence: 0.96

Decision:
SAVE

Reason:
- high novelty
- strong expression
- useful in casual conversation
- not duplicate
```

---

# v1.1 §41 Image Analysis Debugger

普通图片：

```text
图片
 ↓
Vision Analysis
 ↓
Summary
Objects
OCR
Scene
Mood
Confidence
```

明确显示：

```text
Asset Type: IMAGE
Sticker Eligible: NO
```

即使图片非常像 meme：

默认仍然：

```text
Sticker Eligible = NO
```

除非它来自明确的 Sticker Source。

---

# v1.1 §42 Image Cache

创建：

```text
ImageAnalysisCache
```

使用：

```text
sha256
```

进行去重。

相同图片再次发送：

不再次调用视觉模型。

---

# v1.1 §43 Media Storage

建议结构：

```text
data/
├── media/
│   ├── cache/
│   └── incoming/
│
├── stickers/
│   ├── library/
│   ├── imported/
│   └── archived/
│
└── vision/
    └── cache/
```

不要把所有内容混在：

```text
data/images/
```

---

# v1.1 §44 数据库迁移

必须使用现有 SQLite migration 机制。

不得：

- 删除数据库
- 重建数据库
- 清空已有 Memory
- 清空 World Activity
- 清空 Social Cognition 数据
- 破坏已有任务

新增 migration：

```text
media_assets
image_analysis
sticker_assets
sticker_usage
sticker_analysis_queue
sticker_preferences
native_faces
```

具体表结构以现有项目 ORM / database architecture 为准。

---

# v1.1 §45 失败处理

视觉模型调用可能：

- timeout
- 429
- 5xx
- invalid image
- unsupported format
- too large
- malformed response

必须通过现有 AI Provider / ModelRouter / retry / fallback 机制。

不能新增一套独立的错误处理体系。

---

# v1.1 §46 Vision Model fallback

支持：

```text
Primary Vision Model
↓
Fallback Vision Model
↓
失败
↓
保存待处理任务
```

视觉分析失败：

不能阻塞 QQ 正常聊天。

---

# v1.1 §47 GIF / 动图

Sticker 系统必须考虑：

```text
gif
webp animation
短动画
```

至少：

- 判断是否动画
- 保存原文件
- 获取首帧或代表帧用于视觉分析
- OCR
- 情绪 / 动作理解

不需要在 v1.1 完成完整视频理解。

如果当前视觉 Provider 不直接支持 GIF：

可以：

```text
抽取 1~3 个关键帧
↓
Vision Model
↓
聚合分析
```

---

# v1.1 §48 图片预处理

在视觉分析之前：

- 检查格式
- 限制最大尺寸
- 必要时缩放
- 必要时转 WebP / JPEG
- 保留原文件
- 生成分析副本

不得修改原始 Sticker。

---

# v1.1 §49 敏感内容与隐私保护

普通图片中的：

- 私人照片
- 身份信息
- 联系方式
- 地址
- 证件
- 私密内容

不得自动进入 Sticker Library。

即使视觉上具有 meme 属性也不保存。

如果属于普通 image：

直接保持：

```text
image
```

并只进入 Image Understanding。

---

# v1.1 §50 角色自主性规则

必须贯彻：

> CatooBot 有权决定是否收藏、是否使用、使用什么表情包。

但是：

自主决策不能绕过：

```text
Media Type Boundary
Safety Policy
Sticker Selector
Cooldown
Social Cognition
OneBot Permission
```

即：

```text
Character Autonomy
≠
Unrestricted Side Effects
```

---

# v1.1 §51 不允许添加 QQ 管理命令

绝对不要添加：

```text
/sticker
/emoji
/save
/memory
/image
```

之类的 QQ 指令。

所有功能：

```text
自动发生
```

或：

```text
WebUI 管理
```

QQ 侧继续保持纯角色体验。

---

# v1.1 §52 不要将 Sticker Library 当作 Memory

严格区分：

```text
Memory
=
角色记得什么

Sticker Library
=
角色拥有什么表达资产

World
=
角色现在正在经历什么

Relationship
=
角色与用户关系如何

Social Cognition
=
角色现在是否应该参与

Expression Runtime
=
角色现在是否适合使用某种表达方式
```

不要混淆。

---

# v1.1 §53 Character Context 中增加媒体语义

用户发送：

```text
文字 + image
```

最终 Character Runtime 可以获得：

```text
text:
“这是什么”

image_context:
“一张 Minecraft 游戏截图，画面中有一个大型红石装置……”
```

用户发送：

```text
mface
```

可以获得：

```text
sticker_context:
“用户发送了一个表现震惊的猫咪表情”
```

这样模型不用重新看原始消息结构。

---

# v1.1 §54 Social Cognition 增加表达上下文

不重写 Social Cognition。

增加：

```text
incoming_media_type
incoming_sticker_semantics
image_context
expression_opportunity
```

例如：

```text
incoming_sticker = true
sticker_emotion = amused
sticker_intent = teasing
```

Social Cognition 可以更好判断：

```text
用户是否在开玩笑
是否是在回应 Bot
是否应该跟进
```

---

# v1.1 §55 直接回复用户发送的表情

如果用户发：

```text
[哭哭猫]
```

角色可以：

```text
“怎么啦”
```

也可以：

```text
“呜呜抱抱”
```

也可以：

```text
[抱抱猫]
```

也可以：

```text
“怎么啦”
[抱抱猫]
```

最终形式由 Character Runtime 自主决定。

---

# v1.1 §56 Bot 自己也可以发“没有文字的表情”

必须支持：

```text
STICKER_ONLY
```

否则整个系统会被限制成：

```text
文字 + 图片附件
```

而不是自然聊天。

---

# v1.1 §57 不要让 Agent Runtime 直接决定表情包文件

Agent Runtime 如果未来执行：

```text
查资料
搜索
完成任务
```

结果返回到 Character Runtime。

最终：

```text
Agent Result
 ↓
Character Runtime
 ↓
Response Planner
 ↓
Expression Decision
 ↓
Sticker Selector
```

Agent 不得直接：

```text
send_sticker
```

除非未来单独设计权限明确的表达能力。

---

# v1.1 §58 与 Tool Runtime 的关系

不要把 Sticker Selection 作为普通 Tool。

因为：

```text
发送表情包
```

属于 Character Expression。

它应该是：

```text
Character Runtime Capability
```

而不是：

```text
Generic Tool
```

实际网络发送仍然走：

```text
OneBot Adapter
```

---

# v1.1 §59 与 v1.0 World Activity 的关系

当角色：

```text
看动画
玩 Minecraft
听音乐
吃饭
睡觉
```

时，不应该因为发送表情就修改 Primary Activity。

用户消息只产生：

```text
Interaction Overlay
```

除非现有 Behavior / Activity Runtime 判断角色真的停止当前活动。

---

# v1.1 §60 未来扩展能力

当前只实现：

```text
image
sticker
face
mface
```

但架构必须保留：

```text
audio
video
record
```

扩展空间。

不要把整个 MediaRuntime 写成只支持 image。

---

# v1.1 §61 测试要求

至少增加以下测试。

## Message Parsing

测试：

- text
- image
- face
- mface
- image with emoji metadata
- malformed image
- unknown segment

---

## Image Understanding

测试：

```text
普通 JPG
PNG
WebP
GIF
超大图片
无效图片
重复图片
```

确认：

```text
普通图片不会进入 Sticker Library
```

---

## Sticker Acquisition

测试：

```text
明确 mface
image + emoji_id
用户连续发送相同 sticker
高度相似 sticker
完全新 sticker
低质量 sticker
```

确认：

```text
save
reject
defer
```

三种状态都正常工作。

---

## Startup Indexer

测试：

```text
第一次启动：
100 个新文件

第二次启动：
无变化

第三次：
新增 5 个

第四次：
修改 2 个

第五次：
analysis_version 升级
```

确保只处理需要处理的内容。

---

## Sticker Selector

测试：

```text
开心
伤心
震惊
无语
吐槽
安慰
认真
群聊
私聊
高频聊天
低频聊天
```

验证选择逻辑。

---

## Response

测试：

```text
text
text_and_sticker
sticker_only
```

---

# v1.1 §62 回归测试

必须确保升级后原有：

- 文字聊天
- Persona
- Memory
- Relationship
- Behavior Engine
- Semantic Memory
- Tools
- Agent
- World Activity
- Social Cognition
- QQ group participation
- WebUI
- Task system

全部正常。

---

# v1.1 §63 性能要求

不要因为加入视觉系统导致每条文本消息都调用 Vision Model。

只有：

```text
image
sticker
```

才进入 MediaRuntime。

视觉分析尽量：

```text
cache first
```

表情包收集分析：

```text
background
```

启动索引：

```text
background
```

不得阻塞聊天。

---

# v1.1 §64 可观测性

Logger 至少增加：

```text
media_received
media_classified
image_analyzed
sticker_candidate_detected
sticker_acquisition_decision
sticker_saved
sticker_rejected
sticker_duplicate
sticker_selected
sticker_sent
vision_failed
sticker_analysis_failed
```

每条日志应包含：

```text
correlation_id
message_id
media_id
sticker_id
```

但不得把原始 Base64 / 私人图片内容大量写入日志。

---

# v1.1 §65 WebUI Dashboard

增加一个：

```text
Multimodal / Sticker
```

页面。

展示：

```text
图片识别次数
视觉模型调用次数
视觉缓存命中率
表情包总数
本周新增
自动收藏
自动拒绝
重复数量
表情包发送次数
最常用表情
最常被选择但冷却中的表情
分析失败
```

---

# v1.1 §66 资产生命周期

Sticker：

```text
candidate
 ↓
analyzing
 ↓
active
 ↓
disabled
 ↓
archived
```

不能简单：

```text
save forever
```

---

# v1.1 §67 Sticker Quality Gate

加入：

```text
StickerQualityEvaluator
```

检查：

- 文件完整性
- 图片是否可读
- 分辨率
- 是否过度模糊
- 是否真正具备表达意义
- 是否与已有资产重复
- 是否过度私人化
- 是否适合角色表达

最终：

```text
keep
reject
review
```

---

# v1.1 §68 重要：不要让 AI 产生假分析

视觉模型失败时：

不要生成：

```text
“这是一只猫”
```

之类的猜测结果。

应该：

```text
analysis_status = failed
```

然后正常处理消息。

---

# v1.1 §69 重要：不要强制所有 Sticker 都必须被 AI 分析后才能发送

如果用户刚发送：

```text
mface
```

Bot 已经知道它是 Sticker。

它可以立即理解：

```text
incoming media = sticker
```

如果视觉分析稍后完成：

再补充：

```text
emotion
intent
description
```

这样不会影响聊天延迟。

---

# v1.1 §70 重要：收藏与使用是两个独立决策

不要：

```text
因为值得收藏
→ 立即决定使用
```

收藏：

```text
StickerAcquisitionEvaluator
```

使用：

```text
ExpressionDecision + StickerSelector
```

完全分离。

一个表情包可以：

```text
收藏
但很少使用
```

也可以：

```text
经常使用
但不适合继续收藏其他相似版本
```

---

# v1.1 §71 最终数据流

## 收到普通图片

```text
QQ
 ↓
OneBot
 ↓
MediaNormalizer
 ↓
type=image
 ↓
ImageUnderstandingRuntime
 ↓
Vision Model
 ↓
ImageAnalysisCache
 ↓
Character Runtime
```

最终：

```text
不会进入 Sticker Library
```

---

## 收到用户表情包

```text
QQ
 ↓
OneBot
 ↓
MediaNormalizer
 ↓
Sticker Candidate
 ↓
快速进入 Character Runtime
 │
 └──────────────► Background Acquisition
                         ↓
                 StickerAcquisitionEvaluator
                         ↓
                     save?
                    /   |   \
                  yes  no  defer
                   ↓
             StickerAnalyzer
                   ↓
                Dedup
                   ↓
            Sticker Library
                   ↓
              Semantic Index
```

---

## Bot 主动使用

```text
Character Runtime
      ↓
Expression Decision
      ↓
need sticker?
   ├── no
   └── yes
         ↓
   StickerSelector
         ↓
   Sticker Library
         ↓
   candidate ranking
         ↓
   final sticker
         ↓
   StickerSender
         ↓
   OneBot
         ↓
   QQ
```

---

# v1.1 §72 最终验收标准

完成以后必须至少实现以下真实行为。

### 场景 1：普通图片

用户：

```text
[一张旅游照片]
```

Bot：

```text
能够理解图片内容并自然回答。
```

但：

```text
该图片不能自动进入 Sticker Library。
```

---

### 场景 2：用户发送 QQ 商城表情

用户：

```text
[mface]
```

Bot：

```text
能够理解这是一个表情。
```

后台：

```text
自动判断是否收藏。
```

如果认为有价值：

```text
自动加入自己的 Sticker Library。
```

不需要任何命令。

---

### 场景 3：重复表情

用户反复发送同一表情：

```text
第一次 → 可能收藏
第二次 → 不重复收藏
第三次 → 不重复收藏
```

---

### 场景 4：手动导入

把：

```text
new_cat.gif
new_dog.webp
```

放入 Sticker Import Directory。

重启 Bot。

必须：

```text
自动发现
自动分析
自动建立索引
WebUI 中出现
```

但没有变化的旧表情：

```text
不得重复调用 Vision Model。
```

---

### 场景 5：Bot 自主使用

用户和 Bot 正常聊天。

在自然适合使用表情包的场景：

```text
Bot 可以主动选择使用表情包。
```

不适合时：

```text
Bot 可以完全不用。
```

允许：

```text
纯文字
```

允许：

```text
文字 + Sticker
```

允许：

```text
Sticker Only
```

---

### 场景 6：Sticker 与普通图片严格分离

即使：

```text
普通图片很搞笑
```

也不能自动进入：

```text
Sticker Library
```

即使：

```text
图片视觉上非常像 meme
```

只要它没有被明确识别为 Sticker Source：

```text
仍属于 IMAGE。
```

---

### 场景 7：QQ 群聊

必须兼容 v0.9：

```text
Social Cognition
→ 判断是否参与
→ Character Runtime
→ 决定是否使用 Sticker
```

不能因为 Sticker Runtime 自己判断“很好笑”就绕过 Social Cognition 在群里主动发图。

---

### 场景 8：World Activity

角色：

```text
primary_activity = watching_show
```

发送 Sticker：

```text
不会把 activity 改成 chatting。
```

用户短暂互动：

```text
只产生 interaction overlay。
```

---

# v1.1 §73 实现顺序

请严格按以下顺序开发：

1. 读取现有项目
2. 梳理消息链路
3. 设计 MediaContent
4. 修改 OneBot Message Normalizer
5. 增加 ImageUnderstandingRuntime
6. 增加 VisionProvider capability
7. 增加 ImageAnalysisCache
8. 增加 Sticker 数据模型
9. 增加 StickerLibrary
10. 增加 StickerLibraryIndexer
11. 增加增量扫描
12. 增加 StickerAnalyzer
13. 增加 StickerAcquisitionEvaluator
14. 增加 Sticker 去重
15. 增加 Sticker Semantic Index
16. 增加 ExpressionDecision
17. 增加 StickerSelector
18. 增加 StickerSender
19. 修改 ResponsePlan
20. 接入 Character Runtime
21. 接入 v0.9 Social Cognition compatibility layer
22. 接入 v1.0 World Activity Context
23. 增加 WebUI
24. 增加 Debugger
25. 增加 Tests
26. 执行 Migration
27. 执行完整 Regression
28. 实际连接 NapCat 进行 QQ 测试

---

# v1.1 §74 开发约束

严格遵守：

- 不重写已有 v0.9 Social Cognition
- 不重写已有 v1.0 World Activity
- 不删除现有数据
- 不破坏 SQLite
- 不增加 QQ 管理命令
- 不把 Sticker Library 当 Memory
- 不把普通 image 当 Sticker
- 不让 Agent 绕过 Character Runtime
- 不让 Sticker Runtime 绕过 Social Cognition
- 不让模型直接生成任意文件路径
- 不允许视觉分析失败阻塞正常聊天
- 不允许启动索引阻塞 Bot 上线
- 不允许重复分析未变化的 Sticker
- 不允许每条普通文本消息调用 Vision Model
- 不使用随机数作为是否发送 Sticker 的主要决策机制
- 不保存隐藏 chain-of-thought
- 只保存结构化分析与决策结果

---

# v1.1 §75 最终要求

完成后不要只告诉我：

```text
“代码写完了”
```

必须提供：

### Architecture Change Summary

说明增加了哪些模块。

### Database Migration Summary

说明增加了哪些表 / 字段。

### Message Flow

说明：

```text
image
sticker
face
mface
```

分别如何处理。

### Sticker Acquisition Flow

说明 Bot 如何自主收藏表情。

### Sticker Selection Flow

说明 Bot 如何自主决定是否使用表情以及如何选择具体表情。

### Startup Index Flow

说明手动导入表情后启动时如何自动分析，并说明如何避免重复分析。

### Vision Provider

说明当前使用哪个视觉模型 / Provider，以及如何接入现有 ModelRouter。

### WebUI

说明增加了哪些页面。

### Test Results

列出：

```text
unit tests
integration tests
migration tests
NapCat integration tests
```

### Known Limitations

必须明确列出现阶段尚未解决的问题。

最终目标不是：

> “给 CatooBot 加一个能发图片的功能”。

而是：

> **让 CatooBot 开始拥有自己的视觉感知能力，以及逐渐形成属于自己的“表情语言”。**

它能够看懂别人发送的图片和表情，能够判断哪些表情值得收藏，能够建立并维护自己的表情资产库，也能够在合适的时候自主选择使用哪一个表情，而普通图片始终与表情包资产严格隔离。