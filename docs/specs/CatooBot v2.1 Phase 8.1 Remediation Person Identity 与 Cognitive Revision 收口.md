# CatooBot v2.1 Phase 8.1
## Person Identity Persistence + Cognitive Revision Integrity

### 一、当前基线

当前 Phase 8 commit：

`f1a8d370d8c65a644d6cf0f7f771c5ab490fb66d`

当前：

- Relationship Dynamics 已实现
- PersonIdentity 已实现
- SocialInteractionFact 已实现
- RelationshipState 已实现
- Relationship Mutation / Event 已实现
- Invitation / Acceptance / Shared Activity 已实现
- Character isolation 已实现
- 1195 tests passed
- CI success

本轮不是 Phase 9。

只修以下三个问题：

1. PersonIdentity.external_ids 持久化错误
2. 多 Core Friend 的身份映射不能正确区分
3. Relationship 变化与 Decision Context 的 revision 一致性

不要重做 Relationship System。

---

# 二、问题 1：external_ids 持久化必须保存真实平台映射

当前内存：

```python
PersonIdentity(
    person_id="person_qq_xxx",
    external_ids={"qq": "123456"}
)
```

是正确的。

但当前：

```text
sandbox_persons.external_ids
```

写入类似：

```json
{"person_id": "person_qq_xxx"}
```

这是错误的。

---

# 三、正确的持久化模型

`remember_person()` 必须接收：

```text
person_id
display_name
external_ids
source
```

或者等价参数。

例如：

```text id="2zpc8u"
remember_person(
    person_id="person_qq_xxx",
    display_name="空凛",
    external_ids={"qq": "123456"},
)
```

数据库：

```json
{
  "qq": "123456"
}
```

---

# 四、禁止伪造 external_ids

不要：

```json
{"person_id": "..."}
```

作为 external_ids。

`person_id` 已经是独立字段。

必须保持：

```text
person_id
≠
external_ids
```

---

# 五、兼容旧数据库

历史 Phase 8 数据可能已经写入：

```json
{"person_id": "..."}
```

不要删除 migration 24。

启动/读取时：

如果发现旧格式：

```text
external_ids == {"person_id": "..."}
```

可以视为 legacy invalid mapping：

- 不要把它当作 QQ identity
- 可以保留数据库原值
- 下一次收到真实平台身份时覆盖为正确格式

不要自动猜 QQ ID。

---

# 六、测试

新增：

```text
TestPersonExternalIdPersistence
```

流程：

```text
QQ = 123456
↓
PersonIdentity
↓
remember_person()
↓
DB
```

断言：

```text
external_ids["qq"] == "123456"
```

并且：

```text
external_ids 不含 "person_id"
```

---

# 七、问题 2：多个 Core Friend 必须一一映射

当前：

```python
for_qq()
```

只要 QQ ID 在：

```text
core_friend_ids
```

里，就取：

```text
_bible_core_person()
```

而这个函数目前返回第一个 core person。

必须修复。

---

# 八、设计原则

必须支持：

```text
QQ_A → Person_A
QQ_B → Person_B
```

而不是：

```text
QQ_A → Person_A
QQ_B → Person_A
```

---

# 九、优先采用明确映射

不要通过：

```text
list index
```

猜测：

```text
core_friend_ids[0]
→ relationship[0]
```

这是脆弱的。

推荐增加兼容配置：

```yaml
core_friend_ids:
  "123456": "空凛"
  "234567": "某核心好友"
```

或者等价：

```text
core_friend_identities
```

结构。

如果当前配置仍然是：

```text
["123456"]
```

必须保持向后兼容。

当：

```text
只有一个 core_friend_id
+
只有一个 Bible core friend
```

继续映射。

当：

```text
多个 core friend
+
旧 list 配置无法表达映射
```

不要猜。

应该：

- 使用普通 `person_qq_<hash>` identity
- 记录 warning
- 不错误绑定到某个 Bible person

或者采用项目已有的明确配置机制。

---

# 十、测试

新增：

```text
TestMultipleCoreFriendIdentityMapping
```

至少构造：

```text
Bible:
Friend A = core
Friend B = core

QQ:
123 → A
234 → B
```

验证：

```text
person_A != person_B
```

并且：

```text
relationship(A)
≠
relationship(B)
```

---

# 十一、旧单核心好友行为必须保持

当前角色：

```text
罐头
```

仍只有一个 Core Friend：

```text
空凛
```

不要改变：

```text
空凛 QQ → Bible Person Identity
```

现有 Phase 8 测试必须继续通过。

---

# 十二、问题 3：Relationship Revision 与 Decision Context

当前：

```text
Relationship Mutation
    ↓
StateMutation(affects_world=False)
    ↓
world_revision 不变
```

这个原则可以保留。

不要简单改成：

```text
affects_world=True
```

因为 Relationship 不是 Physical World State。

但是 Phase 6 Decision 已经依赖：

```text
relationship.trust
relationship.closeness
```

所以需要增加一个独立的认知 revision。

---

# 十三、建立 Cognitive Revision

推荐：

```text
relationship_revision
```

或统一：

```text
cognitive_revision
```

优先推荐：

```text
cognitive_revision
```

因为未来还可能有：

```text
relationship
social context
character knowledge
conversation context
```

变化。

但不要过度设计。

本轮至少实现：

```text
relationship change
→ cognitive_revision += 1
```

---

# 十四、不要影响 world_revision

最终：

```text
Physical World Change
→ world_revision++

Relationship Change
→ cognitive_revision++

Both
→ corresponding revision
```

---

# 十五、DecisionRequest 必须记录 revision

当前 Phase 6 已经记录：

```text
world_revision
```

现在增加：

```text
cognitive_revision
```

或者等价字段。

DecisionRequest 创建时记录：

```text
world_revision_at_request
cognitive_revision_at_request
```

---

# 十六、Validator

Proposal 执行前：

```text
current_world_revision
current_cognitive_revision
```

分别检查。

只有：

```text
current_world_revision
==
request.world_revision
```

且：

```text
current_cognitive_revision
==
request.cognitive_revision
```

才认为 Proposal 没有 stale。

---

# 十七、哪些事情改变 cognitive_revision？

本轮只要求：

```text
RelationshipChanged
```

会：

```text
cognitive_revision++
```

不要把：

```text
每条 Memory
每个 tick
每个普通 event
```

都算进去。

---

# 十八、与 Phase 8 Invitation 的兼容

注意：

当前：

```text
game_invitation
→ relationship update
→ decision
```

如果关系更新发生在 Decision 前：

```text cognitive_revision
```

应该体现最新关系。

因此：

```text DecisionRequest
```

读取当前 revision。

---

# 十九、异步 social task 的处理

当前：

```text
_record_qq_interaction()
    ↓
asyncio.create_task(
    apply_social_interaction()
)
```

这会让：

```text
Relationship update
```

与：

```text
Decision
```

存在竞态。

本轮不要简单删除异步设计。

但必须保证：

### A. 如果关系事实属于触发当前 Decision 的同一个 External Event

应该先完成：

```text
SocialInteractionFact
```

再构造：

```text
DecisionRequest
```

即：

```text
ExternalEvent
    ↓
SocialInteractionFact
    ↓
Relationship Update
    ↓
Decision Context
    ↓
Decision
```

这样当前邀请决策看到的是最新关系。

### B. 非本次 Decision 的后台 Social Event

继续允许异步。

---

# 二十、不要让 Relationship Update 再次触发当前 Decision

例如：

```text
Invitation
→ relationship update
→ Decision
```

这是单向流程。

不要：

```text
RelationshipChanged
→ Decision
→ RelationshipChanged
→ Decision
```

本轮关系变化只是：

```text
cognitive_revision
```

刷新认知版本。

不自动启动新的 Decision。

---

# 二十一、测试 Cognitive Revision

新增：

```text
TestRelationshipChangeBumpsCognitiveRevision
```

验证：

```text
before = N
apply social interaction
after = N+1
world_revision 不变
```

---

# 二十二、测试 stale decision

构造：

```text
DecisionRequest
world_revision = 10
cognitive_revision = 5
```

然后：

```text
RelationshipChanged
→ cognitive_revision = 6
```

提交旧 Proposal：

必须：

```text
DECISION_REJECTED
reason = cognitive_changed
```

不能执行 Action。

---

# 二十三、测试 world / cognitive independent

### Case A

只有 World Mutation：

```text
world_revision++
cognitive_revision 不变
```

### Case B

只有 Relationship Mutation：

```text
world_revision 不变
cognitive_revision++
```

### Case C

两者同时：

两者各自变化。

---

# 二十四、不要改变 Relationship 的语义

继续保持：

```text
Relationship ≠ World State
```

所以：

```text
affects_world=False
```

保留。

只是增加：

```text
cognitive_revision
```

作为另一条一致性线。

---

# 二十五、Persistence

`cognitive_revision` 本身可以是 Runtime transient revision。

除非现有 Decision persistence 明确要求跨重启，否则：

> 不新增数据库字段。

重启后：

```text
world_revision
cognitive_revision
```

从当前状态重新建立。

本轮不做持久化 revision 系统。

---

# 二十六、Relationship Mutation 顺序

当前：

```text
state.setattr()
save()
mutation.record()
event.publish()
```

这不是最佳顺序。

在不引入事务系统的情况下，至少调整逻辑为：

```text
before
↓
calculate after
↓
StateMutation record
↓
persist relationship
↓
RELATIONSHIP_CHANGED
```

并保持：

```text
StateMutation.before / after
```

与最终持久化值完全一致。

如果现有 MutationLog 是纯内存同步记录：

> 先 record 再 save。

---

# 二十七、Persistence failure

如果：

```text
relationship save = False
```

不能把它当成：

```text
relationship fully committed
```

至少记录：

```text
warning/error
```

并继续保持当前 world 不崩溃。

不要创建第二套 dirty system。

如果当前 Sandbox 已有统一 flush/dirty 机制：

> 复用。

---

# 二十八、不要扩展 Phase 8 功能范围

本轮禁止：

- Phase 9
- Relationship → Goal
- Relationship → Persona
- Memory → Relationship
- Social Planner
- Social Agent
- Emotion Engine
- Mood Engine V2
- Embedding
- LLM relationship scoring
- 新 EventBus
- 新 Mutation system
- 新 Decision system
- 新 Memory system
- Character Bible rewrite
- Git history rewrite

---

# 二十九、工程检查

执行：

```powershell id="lq4q40"
ruff check .
ruff format --check .
mypy app
pytest -q
```

要求：

```text
原 1195 tests 全部保留
+
本轮 remediation tests
=
全部通过
```

CI success。

---

# 三十、Git 双目录

开发：

`E:\WorkSpace ZCode\CatooBot`

发布：

`E:\WorkSpace ZCode\CatooBot_github`

发布前：

- API keys
- API tokens
- `.env`
- 真正 Character Bible
- local DB
- logs
- Zcode private workflow
- local paths
- secrets

不得上传：

`E:\WorkSpace ZCode\CatooBot\config\character_bible.md`

不得：

```text
git filter-repo
BFG
force push
history rewrite
```

---

# 三十一、最终报告

必须报告：

```text
Phase 8.1 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

Person external_ids persistence:
<结果>

Multiple core friend mapping:
<结果>

cognitive_revision:
<实现>

Relationship → cognitive_revision:
<结果>

world_revision 独立性:
<测试>

stale Decision:
<测试>

Invitation ordering:
<SocialInteraction 是否在 Decision 前稳定完成>

Relationship mutation ordering:
<结果>

Persistence failure:
<处理方式>

是否修改 Relationship priority:
No

是否增加 Emotion/Mood:
No

是否 Memory → Relationship:
No

是否 Relationship → Goal:
No

是否进入 Phase 9:
No
```

完成后停止。

# 三十二、最终验收

必须满足：

```text
✅ PersonIdentity.external_ids 保存真实平台映射
✅ 不再把 person_id 塞进 external_ids
✅ 多 Core Friend 不会全部绑定到第一个人
✅ 单 Core Friend 旧行为保持
✅ Relationship change 不增加 world_revision
✅ Relationship change 增加 cognitive_revision
✅ DecisionRequest 记录 cognitive_revision
✅ Validator 检查 cognitive_revision
✅ 关系变化可以使相关旧 Proposal stale
✅ 当前邀请 Decision 读取到正确关系状态
✅ Relationship Mutation 顺序清晰
✅ persistence 失败不会伪装成功
✅ 原 1195 tests 全保留
✅ 新测试全部通过
✅ CI success
```

**本轮完成后停止，不进入 Phase 9。**