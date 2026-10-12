# CatooBot v2.1 Phase 10.1 · Persistent Episode Idempotency

## 0. 基线

当前基线：

```text
HEAD: f7e2b8423e4f956a2ece5159b0df8da4c806fe63
Phase: 10 · Social Experience & Shared Memory Continuity
Tests: 1263 passed
CI: success
```

本轮不是 Phase 11。

目标只有一个：

> 修复 Shared Experience episode identity 只存在 Runtime 内存、重启后无法继续幂等的问题。

不要重做 Phase 10。

---

# 1. 当前已确认的问题

当前 `ExperienceBuilder` 使用：

```python
self._by_correlation
self._by_episode
```

进行 episode aggregation / replay dedupe。

这些都是 Runtime 内存索引。

而 `ExperienceRecord.id` 当前是：

```python
f"exp_{uuid.uuid4().hex[:12]}"
```

数据库的 `sandbox_experiences` 保存逻辑是：

```sql
ON CONFLICT(id) DO NOTHING
```

因此：

```text
同一 Runtime replay
    → _by_episode 命中
    → 一个 Experience

Runtime restart
    → _by_episode 清空
    → 相同 fact 再次 replay
    → 新 UUID
    → 第二条 sandbox_experiences
```

这是不符合 Phase 10：

```text
一个真实行为 = 一个 episode
```

的持久化语义。

---

# 2. 本轮唯一目标

让 episode identity 成为：

```text
Runtime-safe
+
Restart-safe
+
Replay-safe
+
Character-scoped
```

即：

```text
同一 character
+
同一 episode identity
→ 永远只能对应一个 persisted Experience
```

---

# 3. Episode Identity 规则不得改变

继续使用 Phase 10 已建立的优先级：

```text
ActionInstance
    >
Commitment
    >
InteractionFact
```

现有 `_episode_keys()` 的语义不要改变。

例如：

```text
action_instance_id = act_123
→ episode key = action:act_123
```

有 commitment：

```text
commitment_id = cm_123
→ episode key = commitment:cm_123
```

没有上述二者：

```text
interaction_id = int_123
→ episode key = interaction:int_123
```

不要重新设计新的 episode semantics。

---

# 4. 必须把 episode identity 持久化

推荐方案：

为 `sandbox_experiences` 增加：

```text
episode_key TEXT
```

并建立 character-scoped unique constraint / unique index：

```text
(character_id, episode_key)
```

要求：

```text
episode_key != ""
```

但注意：

现有历史 ExperienceRecord 可能没有 episode key。

必须：

```text
兼容历史 rows
```

不要删除已有 experience。

如果采用 nullable：

```text
episode_key TEXT
```

允许历史记录保持 NULL。

新 Phase 10 shared activity 必须写入稳定 episode_key。

---

# 5. episode_key 的存储

不要把多个 key 混成不可查询的字符串。

对于 Shared Experience：

优先存：

```text
action:<action_instance_id>
```

否则：

```text
commitment:<commitment_id>
```

否则：

```text
interaction:<interaction_id>
```

只存一个 canonical episode key。

这和当前 `_episode_keys()` 的优先级保持一致。

例如：

```text
action_instance_id = act_001
commitment_id = cm_001
interaction_id = int_001
```

最终：

```text
episode_key = action:act_001
```

而不是：

```text
action:act_001|commitment:cm_001|interaction:int_001
```

其他 identity 继续留在 metadata 中。

---

# 6. Character Isolation

unique identity 必须是：

```text
(character_id, episode_key)
```

不能只有：

```text
episode_key
```

因为：

```text
Character A + action:act_001
Character B + action:act_001
```

必须允许各自拥有自己的 Experience。

---

# 7. Store 必须成为最终持久化幂等边界

不要只在 ExperienceBuilder 增加：

```python
if episode_key in self._by_episode
```

因为这仍然无法解决 restart。

最终数据库写入必须具备幂等行为。

例如：

```text
save_experience(record)
```

必须能够识别：

```text
character_id + episode_key
```

已经存在。

然后：

```text
不要插入第二条
```

同时最好返回：

```text
inserted
```

或：

```text
existing
```

让 Runtime 可以知道是否真的产生了新的 persisted episode。

禁止依赖：

```text
random UUID conflict
```

来实现幂等。

---

# 8. Existing Experience aggregation 必须保持

当前 Runtime 内存聚合仍然保留。

正确结构应该是：

```text
Event
 ↓
ExperienceBuilder
 ↓
Runtime-local aggregation
 ↓
persistent episode identity
 ↓
DB idempotency
```

两层共同保证：

```text
实时事件链
+
重启后的持久化幂等
```

不要删除 `_by_episode`。

---

# 9. Restart 后 replay 的行为

必须新增真正的跨重启测试。

场景：

```text
Runtime A
    ↓
shared_activity
    ↓
flush_experiences()
    ↓
DB 中 1 条 Experience
    ↓
shutdown
    ↓
Runtime B
    ↓
重新产生完全相同的 SocialInteractionFact
    ↓
flush_experiences()
```

要求：

```text
sandbox_experiences count:
1
```

而不能：

```text
2
```

同时：

```text
memory count:
1
```

保持不变。

---

# 10. Replay identity 测试要求

必须分别测试：

### A. 同 Runtime replay

现有测试保持：

```text
same fact twice
→ one episode
```

不要删。

---

### B. Cross-runtime replay

新增：

```text
runtime A
→ persist

runtime B
→ same fact replay
→ persist

count == 1
```

---

### C. Cross-runtime same interaction_id

同一个：

```text
interaction_id
```

必须保持同一 episode。

---

### D. Cross-runtime same action_instance_id

同一个：

```text
action_instance_id
```

必须保持同一 episode。

---

### E. Cross-character same episode key

例如：

```text
Character A:
action:act_001

Character B:
action:act_001
```

必须得到：

```text
2 experiences
```

而不是错误 dedupe。

---

# 11. Experience metadata 不能丢

数据库已有：

```text
metadata JSON
```

继续保存：

```text
person_id
activity
duration_minutes
commitment_id
action_id
action_instance_id
interaction_id
significance
```

本轮增加：

```text
episode_key
```

可以选择：

```text
metadata["episode_key"]
```

辅助审计。

但最终幂等判断必须使用独立可索引字段：

```text
sandbox_experiences.episode_key
```

不要依赖 JSON LIKE 查询。

---

# 12. Persistence API

建议修改：

```python
SandboxStore.save_experience()
```

使其能够：

```text
INSERT ... ON CONFLICT(character_id, episode_key)
```

或先 query 再 insert。

优先数据库原子约束。

禁止：

```text
SELECT then INSERT
```

作为唯一幂等保障，因为并发下存在 race。

如果数据库 schema / SQLite 能力允许：

```text
UNIQUE(character_id, episode_key)
```

优先。

---

# 13. Existing historical rows

历史：

```text
sandbox_experiences
```

中没有 episode_key 的 rows：

```text
episode_key = NULL
```

允许继续存在。

不要尝试危险地回填所有历史 Experience。

本阶段只保证：

```text
Phase 10+ 新 Shared Experience
```

具备持久化 episode identity。

---

# 14. 非 Shared Experience 不要被错误 dedupe

必须注意：

Phase 10 的 episode key 当前重点是 Shared Activity。

不要让：

```text
ACTION_COMPLETED
GOAL_COMPLETED
RELATIONSHIP_CHANGED
PET_FED
```

等已有 Experience 因 schema 新增 unique key 被错误合并。

可以：

```text
episode_key = NULL
```

对于没有稳定 episode identity 的旧类型。

或者为它们使用已经存在的唯一事实 identity，但不得改变原有语义。

本轮重点是：

```text
shared_activity
```

---

# 15. Memory 层不要重复设计

Memory 继续使用：

```text
dedupe_key
```

当前逻辑保持。

不要新增：

```text
social_memory_episode_table
```

不要新增第二个 repository。

不要修改：

```text
person_match
```

权重。

不要改变：

```text
MemoryCandidateBuilder
```

的 promotion 规则，除非只是为了适配新的 episode_key 字段。

---

# 16. 不要修改 Phase 9

禁止修改：

```text
CommitmentDetector
match_shared_activity()
fulfill()
reschedule()
GoalManager.on_commitment_fulfilled()
ActionInstance binding
fulfillment window
ambiguous matching
```

Phase 9 / 9.1 / 9.1.1 行为必须保持原样。

---

# 17. 不要修改 Phase 8

Relationship：

```text
SocialInteractionFact
→ RelationshipUpdateEngine
```

继续是唯一 relationship mutation path。

本轮不得新增：

```text
Experience → Relationship
Memory → Relationship
```

---

# 18. No new architecture

禁止：

```text
new ExperienceManager
new SocialExperienceRepository
new EpisodeManager
new SocialMemoryManager
new Planner
new Agent
```

只允许修改现有：

```text
ExperienceBuilder
SandboxStore
migration
runtime flush path
tests
```

---

# 19. Migration

这是本轮唯一允许的 schema migration。

建议新增 migration：

```text
sandbox_experiences_episode_identity
```

要求：

```text
episode_key TEXT
```

加：

```text
UNIQUE(character_id, episode_key)
```

但必须注意 SQLite 中多个 NULL 可以共存，因此历史 rows 不受影响。

migration 必须：

```text
向后兼容
不修改历史 migration
不 rewrite migration history
```

---

# 20. Test minimum

在：

```text
tests/test_sandbox_shared_experience.py
```

或者合适的独立 integrity test 中新增至少：

### 1. cross-runtime replay

```text
restart + same fact
→ one experience
```

### 2. character isolation

```text
same episode_key
+ different character_id
→ two experiences
```

### 3. action instance persistent identity

```text
same action_instance_id
→ one persisted experience
```

### 4. interaction fallback persistent identity

无 action_instance / commitment：

```text
same interaction_id
→ one persisted experience
```

### 5. different interaction

```text
interaction A
interaction B
→ two experiences
```

---

# 21. 需要额外检查的边界

检查：

```text
same commitment
different action_instance
```

不要因为：

```text
commitment_id
```

而错误合并两个本来不同的 ActionInstance。

优先语义仍然是：

```text
ActionInstance > Commitment > Interaction
```

如果当前实现已经保证：

```text
one commitment → one fulfillment episode
```

可以保留，但必须写测试证明。

不要仅凭“理论上不会发生”跳过测试。

---

# 22. 重要：不要修改 Episode semantics

不要把本轮变成重新设计：

```text
Experience aggregation
```

当前：

```text
_action_instance
>
commitment
>
interaction
```

已经是 Phase 10 的设计决定。

本轮只是把这个 identity：

```text
persist
+
index
+
idempotent
```

---

# 23. 完成标准

必须：

```text
ruff
ruff format
mypy
pytest
```

要求：

```text
原 1263 tests 全部保留
+
新增 Phase 10.1 tests
```

CI：

```text
success
```

Git：

```text
working tree clean
HEAD == origin/main
```

禁止：

```text
force push
history rewrite
```

---

# 24. 最终报告格式

```text
# CatooBot v2.1 Phase 10.1 Report · Persistent Episode Idempotency

Phase 10.1 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## Episode Identity

canonical episode key:
ActionInstance:
Commitment:
Interaction:

## Persistence

schema migration:
unique constraint:
historical NULL compatibility:

## Replay

same runtime:
cross-runtime:
same action_instance:
same commitment:
same interaction_id:

## Isolation

character isolation:
different real episodes:

## Memory

memory dedupe:
memory count after replay:

## Regression

Phase 8:
Phase 8.1:
Phase 9:
Phase 9.1:
Phase 9.1.1:
Phase 10:

## Architecture

new Experience system: No
new Memory system: No
new Relationship system: No
new Planner: No
new Agent: No
LLM used: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Final

Phase 10.1 complete: Yes / No
Phase 11 entered: No
```

---

# 25. 停止条件

本轮只解决：

> **Phase 10 Shared Experience 的 episode identity 从 Runtime-only 变成 persistent + restart-safe + idempotent。**

完成后：

```text
停止。
```

不要进入 Phase 11。