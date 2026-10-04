# CatooBot v2.1 Phase 12.1 · Response Commit Guard & Proposal Protocol Hardening

## 0. 基线

当前基线：

```text
HEAD: a3dbd7e6f0f51ecfb42c3542e23d721148dd9856
Phase: 12
Tests: 1313 passed
CI: success
```

本轮不是 Phase 13。

只解决 Phase 12 最后一处响应提交竞态，以及两个很小的 proposal protocol hardening。

---

# 1. 唯一核心问题：Response Commit Race

当前流程：

```text
ConversationTurn
↓
world_revision / cognitive_revision snapshot
↓
CognitiveContext
↓
LLM
↓
_validate()
↓
ConversationResponse
↓
caller / adapter
↓
send
```

现在 revision 只在 `_validate()` 检查一次。

存在：

```text
_validate() passed
↓
world/cognition changed
↓
adapter sends stale response
```

这违反 Phase 12 的：

```text
stale response must never be sent
```

---

# 2. 本轮最终目标

建立统一的：

```text
Response Commit Guard
```

语义：

```text
ConversationResponse
    ↓
commit/send guard
    ↓
re-check world_revision
re-check cognitive_revision
    ↓
fresh → allowed
stale → suppressed
```

---

# 3. 不允许把 Guard 放进 QQ / OneBot Adapter

禁止：

```python
if runtime.world_revision != response.world_revision:
    ...
```

散落在：

```text
NapCat adapter
OneBot adapter
QQ sender
```

中。

Guard 必须属于现有：

```text
ConversationRuntime / SandboxRuntime
```

让上层 transport 只需要调用统一 API。

---

# 4. 推荐 API

可以增加：

```python
async def commit_response(
    response: ConversationResponse,
) -> ConversationResponse
```

或者等价名称。

它必须：

```text
1. 检查 response.character_id
2. 检查 response.world_revision
3. 检查 response.cognitive_revision
4. 如果 stale：
       return silent/fallback
5. 如果 fresh：
       return original response
```

不要直接发送网络请求。

---

# 5. 更严格的推荐模式

推荐：

```text
ConversationRuntime.respond()
    ↓
returns validated response

上层：
    ↓
runtime.commit_conversation_response(response)
    ↓
fresh?
   ├── yes → response may be sent
   └── no  → silent response
```

这样：

```text
send transport
```

永远不会自己重新定义 stale 规则。

---

# 6. Response Commit 的行为

fresh：

```text
mode = reply
source = llm
```

保持不变。

stale：

```text
mode = silent
source = fallback
reason = world_changed / cognitive_changed
text = ""
```

并且：

```text
memory_refs = []
experience_refs = []
action_candidate_id = ""
```

全部清空。

禁止把 stale response 的引用继续暴露给 sender。

---

# 7. Character Isolation

commit guard 必须检查：

```text
response.character_id == runtime.character_id
```

如果不一致：

```text
silent
reason = character_changed
```

不要允许：

```text
Character A response
```

进入：

```text
Character B runtime
```

---

# 8. Send 语义

本阶段仍然：

```text
ConversationRuntime
= 生成 + 校验 + commit guard
```

不直接发送 QQ。

最终：

```text
ConversationResponse
→ external adapter
```

但是 adapter 只能接收：

```text
commit_conversation_response()
```

返回的新鲜 Response。

不要让 adapter 自己拿：

```text
respond()
```

的原始结果直接发送。

---

# 9. TOCTOU Regression Test

新增测试：

### test_response_becomes_stale_after_validation_before_commit

需要故意构造：

```text
respond()
→ proposal valid
→ _validate() passed
```

然后：

```text
在 commit 前修改 world_revision
```

再：

```text
commit_conversation_response(response)
```

断言：

```python
response.mode == "silent"
response.source == "fallback"
response.reason == "world_changed"
response.text == ""
response.memory_refs == []
response.experience_refs == []
response.action_candidate_id == ""
```

---

# 10. Cognitive stale commit test

同样：

```text
respond()
→ validated
→ cognitive_revision++
→ commit()
```

要求：

```text
silent
reason = cognitive_changed
```

---

# 11. Fresh commit test

正常：

```text
respond()
→ no state change
→ commit()
```

要求：

```text
same response
same refs
same text
```

---

# 12. Double commit

同一个 fresh response：

```text
commit(response)
commit(response)
```

必须 deterministic。

至少：

```text
second commit
```

不能重新造成：

```text
world mutation
memory mutation
relationship mutation
```

如果需要防止重复发送 token，由上层 transport 负责。

本阶段不要建立 message-send database。

---

# 13. No revision mutation by commit

`commit_conversation_response()` 本身必须：

```text
world_revision unchanged
cognitive_revision unchanged
relationship unchanged
commitment unchanged
goal unchanged
memory unchanged
experience unchanged
current_action unchanged
```

它只是：

```text
read + validate
```

不是 mutation。

---

# 14. Response trace semantics

不要把：

```text
CONVERSATION_RESPONSE_EMITTED
```

理解成：

```text
QQ/network send succeeded
```

Phase 12 当前语义是：

```text
response produced / accepted by sandbox runtime
```

本轮可以：

### 方案 A

不改事件名。

在 payload 增加：

```text
commit_status = fresh
```

### 或

只在 commit guard 中增加：

```text
CONVERSATION_RESPONSE_COMMITTED
```

但如果增加新事件：

```text
它必须仍然是 trace-only
```

不得推进 revision。

不要记录完整 message history。

---

# 15. 严格 JSON 协议修复

当前 `_parse()` 通过：

```python
text.find("{")
text.rfind("}")
```

提取中间 JSON。

本轮改为：

> 模型输出必须整体就是 JSON object。

允许：

```json
{"mode":"reply","text":"你好","confidence":0.9}
```

禁止：

```text
Here is the answer:
{"mode":"reply","text":"你好","confidence":0.9}
```

也禁止：

```text
{"mode":"reply","text":"你好","confidence":0.9}
Done.
```

正确行为：

```text
invalid
→ silent
→ fallback
→ no mutation
```

---

# 16. JSON object 限制

顶层必须：

```text
dict/object
```

禁止：

```json
[]
```

禁止：

```json
"reply"
```

禁止：

```json
null
```

---

# 17. Confidence hardening

最终：

```text
confidence
```

必须满足：

```text
finite
0.0 <= confidence <= 1.0
```

否则：

```text
invalid proposal
→ silent
→ fallback
```

例如：

```text
NaN
Infinity
-Infinity
```

都拒绝。

---

# 18. 不增加第二个 confidence threshold

继续：

```text
decision_min_confidence
```

不要新增：

```text
conversation_min_confidence
```

Phase 12 的原则不变。

---

# 19. Tests for strict JSON

新增：

### A. JSON only

```text
valid JSON
→ accepted
```

### B. Prefix text

```text
hello {"mode":"reply"...}
→ rejected
```

### C. Suffix text

```text
{"mode":"reply"...} hello
→ rejected
```

### D. Multiple objects

```text
{"mode":"reply"...}{"mode":"reply"...}
→ rejected
```

### E. NaN confidence

```text
{"confidence": NaN}
→ rejected
```

### F. Infinity

```text
{"confidence": Infinity}
→ rejected
```

---

# 20. 不改变当前 Response contract

继续：

```text
mode:
reply
acknowledge
defer
silent
```

不能新增：

```text
typing
sticker
voice
emotion
roleplay
```

---

# 21. 不改变 Memory references

继续：

```text
memory_refs
```

只能来自当前：

```text
CognitiveContext
```

---

# 22. 不改变 Experience references

继续：

```text
experience_refs
```

只能来自当前：

```text
recent_shared_experiences
recent_experiences
```

---

# 23. 不改变 Action semantics

继续：

```text
action_candidate_id
```

只是：

```text
world-derived candidate reference
```

命名永远：

```text
!= action execution
```

禁止本轮把：

```text
commit()
```

变成：

```text
execute action
```

---

# 24. 不接 DecisionCoordinator

本轮不要把：

```text
ConversationRuntime
```

改成：

```text
DecisionCoordinator
```

Action execution 继续由现有：

```text
DecisionCoordinator
ProposalValidator
ActionSystem
```

处理。

---

# 25. 不修改 ExternalWorldEvent

Phase 12.1 不需要重新设计：

```text
ExternalWorldEvent
```

QQ message adapter 仍负责：

```text QQ
→ ExternalWorldEvent
```

ConversationResponse：

```text response
→ adapter
```

两个方向继续分离。

---

# 26. 不修改 Phase 11

保持：

```text
CognitiveContext
Social Situation
PersonIdentity
```

全部语义不变。

---

# 27. 不修改 Phase 10

保持：

```text
Experience
episode_key
Memory
memory dedupe
```

完全不动。

---

# 28. 不修改 Phase 9

保持：

```text
Commitment
Goal
ActionInstance
Fulfillment
Reschedule
Window matching
```

完全不动。

---

# 29. No new architecture

禁止：

```text
ConversationManager2
ResponseManager
ResponseQueue
ConversationDatabase
MessageDatabase
ChatHistory
Agent
Planner
Emotion
Personality evolution
```

本轮只做：

```text
existing ConversationRuntime
+
commit guard
+
proposal parser hardening
+
tests
```

---

# 30. Git / workflow

继续：

```text
E:\WorkSpace ZCode\CatooBot
↓
E:\WorkSpace ZCode\CatooBot_github
↓
commit
↓
push
```

禁止：

```text
force push
history rewrite
filter-repo
BFG
```

真实 Character Bible 继续不进入 public mirror。

---

# 31. Tests baseline

当前：

```text
1313 passed
```

要求：

```text
1313 existing tests
全部保留
+
Phase 12.1 tests
```

至少新增：

```text
1. stale after validation
2. cognitive stale after validation
3. fresh commit
4. character mismatch
5. strict JSON prefix
6. strict JSON suffix
7. multiple JSON objects
8. NaN confidence
9. Infinity confidence
```

---

# 32. Quality Gate

必须：

```text
ruff
ruff format
mypy
pytest
```

全部通过。

CI：

```text
success
```

Git：

```text
working tree clean
HEAD == origin/main
```

---

# 33. Phase 12.1 不进入 Phase 13

本轮完成后：

```text
stop
```

不要添加：

```text
conversation history
emotion
personality evolution
```

---

# 34. 最终报告

```text
# CatooBot v2.1 Phase 12.1 Report · Response Commit Guard & Proposal Protocol Hardening

Phase 12.1 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## Response Commit

fresh response:
world stale after validation:
cognitive stale after validation:
character mismatch:
commit mutates world: No

## Protocol

strict JSON:
prefix text:
suffix text:
multiple JSON:
NaN:
Infinity:

## References

memory refs:
experience refs:
action candidate:

## Read-only

world revision changed: No
cognitive revision changed: No
relationship changed: No
commitment changed: No
goal changed: No
memory changed: No
experience changed: No

## Regression

Phase 8:
Phase 8.1:
Phase 9:
Phase 9.1:
Phase 9.1.1:
Phase 10:
Phase 10.1:
Phase 10.2:
Phase 11:
Phase 12:

## Architecture

new Conversation DB: No
new Memory system: No
new Relationship system: No
new Experience system: No
new Planner: No
new Agent: No
new Emotion system: No
new Personality Evolution: No
LLM used for fact construction: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Final

Phase 12.1 complete: Yes / No
Phase 13 entered: No
```

---

**这次不需要重新设计 Phase 12。** `a3dbd7e` 的主体已经正确：Context 唯一来源、引用白名单、Action 不自动执行、LLM 不写世界、Revision stale 检查、失败静默等都已经真实实现并有测试覆盖。

现在只把最后一个“**验证通过 → 真正交给发送层**”之间的竞态关掉，再把“strict JSON”从**实现注释里的严格**变成**实际严格**。完成 12.1 后，Phase 12 才适合真正封板。