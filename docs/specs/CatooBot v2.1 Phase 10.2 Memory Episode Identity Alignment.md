# CatooBot v2.1 Phase 10.2 · Memory Episode Identity Alignment

## 0. 基线

当前基线：

```text
HEAD: ebf61247f792de74adcec4f4340cf27cd5fa2c61
Phase: 10.1
Tests: 1271 passed
CI: success
```

本轮不是 Phase 11。

本轮只修复一个问题：

> Experience 已经采用 `ActionInstance > Commitment > Interaction`，但 Shared Memory 的 dedupe 仍采用 `Commitment > ActionInstance > Interaction`，导致同一个 Commitment 下的多个真实 ActionInstance 被错误合并成一个长期 Memory。

---

# 1. 已确认的问题

当前 `ExperienceBuilder` canonical episode identity：

```text
ActionInstance
    >
Commitment
    >
Interaction
```

例如：

```text
commitment_id = cm_many
action_instance_id = act_one
```

得到：

```text
episode_key = action:act_one
```

第二次：

```text
commitment_id = cm_many
action_instance_id = act_two
```

得到：

```text
episode_key = action:act_two
```

因此 Experience 层正确得到：

```text
Experience A = action:act_one
Experience B = action:act_two
```

Phase 10.1 测试已经证明这一点。不要改变这部分。 

---

# 2. Memory 层当前错误

当前：

```python
episode = (
    commitment_id
    or action_instance_id
    or interaction_id
    or chain
)
```

意味着：

```text
cm_many + act_one
→ shared:person:cm_many

cm_many + act_two
→ shared:person:cm_many
```

两个真实 episode 最终使用同一个 `dedupe_key`。

这违反 Phase 10 已确定的语义：

```text
同一个真实 episode
→ 一个 Memory

两个不同真实 episode
→ 两个 Memory

同一个 episode replay
→ 一个 Memory
```

---

# 3. 唯一目标

让 Shared Memory 的 episode identity 与 Experience 完全一致：

```text
ActionInstance
    >
Commitment
    >
Interaction
    >
correlation fallback
```

推荐直接复用：

```text
experience.episode_key
```

如果 `episode_key` 已存在：

```python
episode = experience.episode_key
```

这是最高优先级。

只有历史/手工构造的旧 `ExperienceRecord` 没有 `episode_key` 时，才使用 fallback：

```text
action_instance_id
>
commitment_id
>
interaction_id
>
chain
```

不要继续使用：

```text
commitment_id or action_instance_id
```

---

# 4. Shared Memory dedupe identity

继续使用：

```text
shared:<person_id>:<episode>
```

不要修改整个 Memory 系统。

例如：

```text
Person X
Commitment C1
ActionInstance A1
→ shared:X:action:A1
```

另一次：

```text
Person X
Commitment C1
ActionInstance A2
→ shared:X:action:A2
```

两条必须是不同 Memory。

而：

```text
same ActionInstance A1
replayed
```

仍然必须：

```text
shared:X:action:A1
```

即同一 Memory。

---

# 5. 不允许修改 Experience identity

禁止修改：

```text
ExperienceBuilder._episode_keys()
canonical episode key
persistent episode_key
```

Phase 10.1 已完成的：

```text
ActionInstance > Commitment > Interaction
```

保持原样。

---

# 6. 不允许改变 Commitment semantics

例如：

```text
一个 Commitment + 两个 ActionInstances
```

本轮仍然允许：

```text
Experience 1
Experience 2
```

以及现在修复后：

```text
Memory 1
Memory 2
```

但：

```text
一个 Commitment + 没有 ActionInstance
```

仍然应该：

```text
一个 commitment episode
→ 一个 Memory
```

因此：

```text
cm_single
无 instance
replay 两次
→ 1 Experience
→ 1 Memory
```

保持不变。

---

# 7. Interaction fallback

没有：

```text
action_instance_id
commitment_id
```

时：

```text
interaction_id
```

继续作为 episode identity。

例如：

```text
interaction:int_1
→ shared:person:interaction:int_1
```

不同 interaction：

```text
int_1
int_2
```

必须得到：

```text
两个 Experience
两个 Memory
```

---

# 8. Cross-runtime replay

必须保持：

```text
Runtime A
shared action A1
→ Experience action:A1
→ Memory shared:person:action:A1

restart

Runtime B
same verified fact / same A1
→ no second Experience
→ no second Memory
```

数据库 Experience 幂等继续由 Phase 10.1：

```text
(character_id, episode_key)
```

保证。

Memory dedupe 继续由：

```text
(character_id, dedupe_key)
```

保证。

不要把两个层合并成一个 repository。

---

# 9. 必须新增的测试

继续使用：

```text
tests/test_sandbox_shared_experience.py
```

或合理的 Memory integrity test。

## Test A · Same commitment, different ActionInstances

构造：

```text
C = cm_many

A1 = act_one
A2 = act_two
```

分别产生：

```text
shared_activity
commitment_id = cm_many
action_instance_id = act_one

shared_activity
commitment_id = cm_many
action_instance_id = act_two
```

执行：

```text
flush_experiences()
```

断言：

```python
assert experience_count == 2
assert active_shared_memory_count == 2
```

并且：

```python
dedupe_keys == {
    "shared:<person_id>:action:act_one",
    "shared:<person_id>:action:act_two",
}
```

---

# 10. Test B · Same ActionInstance replay

构造：

```text
commitment = cm_one
action_instance = act_one
```

重复发送同一个 episode。

断言：

```text
Experience = 1
Memory = 1
```

---

# 11. Test C · Same Commitment without ActionInstance

构造：

```text
commitment = cm_one
action_instance = ""
```

发送两次同一个 commitment episode。

要求：

```text
Experience = 1
Memory = 1
```

---

# 12. Test D · Different interactions

无：

```text
action_instance
commitment
```

只有：

```text
interaction_id = int_one
interaction_id = int_two
```

要求：

```text
Experience = 2
Memory = 2
```

---

# 13. Test E · Cross-runtime same ActionInstance

保持 Phase 10.1 原测试，同时增加 Memory 明确断言：

```text
Runtime A
→ action:A1
→ Memory 1

restart

Runtime B
→ replay action:A1
→ Experience remains 1
→ Memory remains 1
```

不能因为新的 `ExperienceRecord.id` 而产生第二个 Memory。

---

# 14. Test F · Character isolation

两个 Character：

```text
Character A + action:A1
Character B + action:A1
```

必须：

```text
Experience = 2
Memory = 2
```

且：

```text
memory_id
dedupe_key
character_id
```

完全隔离。

---

# 15. Memory provenance

Shared Memory 当前已经保存：

```text
person_id
name
activity
duration_minutes
commitment_id
action_id
action_instance_id
```

继续保持。

建议额外加入：

```text
episode_key
```

例如：

```python
provenance["episode_key"] = experience.episode_key
```

这样可以明确审计：

```text
Memory
→ Experience episode
```

但：

**不要依赖 provenance JSON 做 dedupe。**

dedupe 仍然只使用：

```text
dedupe_key
```

---

# 16. Memory identity 应与 Experience identity 同源

不要在 Memory 层重新发明 episode priority。

推荐：

```python
episode = experience.episode_key
```

这样形成：

```text
Sandbox Event
    ↓
ExperienceBuilder
    ↓
canonical episode_key
    ↓
MemoryCandidateBuilder
    ↓
shared:<person>:<episode_key>
```

只有一个 canonical episode identity。

---

# 17. 不要修改 retrieval

Phase 10 的：

```text
person_match = 0.2
keyword = 0.36
entity = 0.12
importance = 0.20
recency = 0.12
```

保持不变。

本轮不修改 retrieval。

---

# 18. 不要修改 promotion rules

保持：

```text
SHARED_ACTIVITY_BASE_IMPORTANCE
SHARED_COMMITMENT_BONUS
SHARED_LONG_BONUS
SHARED_MAJOR_BONUS
PROMOTION_THRESHOLD
```

全部不变。

本轮只是：

```text
Memory identity alignment
```

不是重新调整“什么值得记住”。

---

# 19. 不修改 Phase 9

禁止修改：

```text
CommitmentManager
CommitmentDetector
match_shared_activity
fulfill
reschedule
CommitmentGoalBridge
GoalManager
ActionInstance
```

---

# 20. 不修改 Phase 8

禁止修改：

```text
RelationshipUpdateEngine
SocialInteractionFact
PersonIdentity
relationship deltas
cognitive_revision
```

---

# 21. 不创建新系统

禁止：

```text
SocialMemoryManager
SharedMemoryRepository
EpisodeMemoryManager
ExperienceMemoryBridge
Planner
Agent
```

只允许修改：

```text
MemoryCandidateBuilder
tests
```

以及必要的最小兼容代码。

---

# 22. 不修改数据库 schema

Phase 10.1 已经完成：

```text
sandbox_experiences.episode_key
UNIQUE(character_id, episode_key)
```

本轮：

```text
不新增 migration
不修改 episode schema
```

---

# 23. 不改变历史 Memory

历史：

```text
memories
sandbox_memory_candidates
```

不进行大规模重写。

本轮只保证：

```text
Phase 10+ 新 shared memories
```

使用正确 identity。

不要扫描数据库批量重建旧 Memory。

---

# 24. 特别防止这个回归

最终代码不能再次出现：

```python
episode = commitment_id or action_instance_id
```

因为：

```text
Commitment ≠ Episode
```

更准确的是：

```text
Commitment = social obligation

ActionInstance = concrete execution

Episode = concrete lived event chain
```

一个 Commitment 理论上可以对应多个实际尝试/执行实例，因此不能让 Commitment 在存在 ActionInstance 时遮蔽 ActionInstance。

---

# 25. 完成标准

要求：

```text
ruff
ruff format
mypy
pytest
```

所有：

```text
1271 existing tests
```

必须保留并通过。

新增至少：

```text
5+ tests
```

重点验证：

```text
same commitment + different action instances
```

最终必须：

```text
CI success
working tree clean
HEAD == origin/main
```

不得：

```text
force push
history rewrite
```

---

# 26. 最终报告

```text
# CatooBot v2.1 Phase 10.2 Report · Memory Episode Identity Alignment

Phase 10.2 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## Identity

Experience identity:
Memory identity:
canonical source:

## Critical Case

same commitment + different ActionInstances:
Experience count:
Memory count:
dedupe keys:

## Replay

same ActionInstance:
same commitment without instance:
same interaction:
cross-runtime:

## Isolation

character isolation:
person isolation:

## Provenance

episode_key:
person_id:
commitment_id:
action_instance_id:

## Regression

Phase 8:
Phase 8.1:
Phase 9:
Phase 9.1:
Phase 9.1.1:
Phase 10:
Phase 10.1:

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

Phase 10.2 complete: Yes / No
Phase 11 entered: No
```

---

# 27. 停止

完成本轮后停止。

不要继续做：

```text
Phase 10.3
Phase 11
```

等审计确认后再进入下一阶段。