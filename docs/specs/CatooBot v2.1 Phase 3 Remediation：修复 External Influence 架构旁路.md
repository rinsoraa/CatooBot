# CatooBot v2.1 Phase 3 Remediation
## 修复 Phase 3 架构边界，不进入 Phase 4

当前基线：

`e18253b54212dc36605117e2cac42f48e459c667`

Phase 3 的主要功能已经完成，但在最终审核中发现 4 个架构问题。

本轮只做这些问题的修复。

**不要进入 Phase 4。**

---

# 一、修复目标

必须确保：

```text
External World
    ↓
ExternalWorldEvent
    ↓
Influence
    ↓
Wakeup
    ↓
Action / Interaction
    ↓
State Mutation
    ↓
Sandbox Event
```

这一链条没有任何旁路。

---

# 二、问题 1：禁止 _start_action() 绕过 Mutation

当前 runtime.py 的 `_start_action()` 存在类似：

```python
if not self.move_entity(...).ok:
    self.character.location = target_space
```

这是错误的。

要求：

1. 删除这种直接 location fallback。
2. `move_entity()` 失败后：
   - Action 不得继续进入错误 location
   - Action 应失败或进入明确的 rejected 状态
   - 必须产生对应 Event
3. 不允许为了“保证 action 能启动”而绕过 State Mutation。
4. Action start 的 movement 必须始终经过 canonical movement gate。

目标：

```text
Action Start
    ↓
需要移动
    ↓
move_entity()
    ↓
Mutation
    ↓
ENTITY_MOVED
```

失败：

```text
move_entity()
    ↓
ENTITY_MOVE_REJECTED
    ↓
ACTION_FAILED
```

不能：

```text
move_entity() failed
    ↓
character.location = target_space
```

新增至少一个测试：

```text
TestActionStartMovementFailureDoesNotBypassMutation
```

验证：

- 非法目标不会修改 location
- 不会产生 ACTION_STARTED
- 会产生明确失败事件
- mutation log 有记录
- current_action 不会伪装成成功状态

---

# 三、问题 2：SocialSpace 状态不能直接赋值

当前 `_apply_influence_effects()` 中类似：

```python
social.character_presence = "active"
social.social_temperature = ...
```

这种直接 Runtime 状态修改必须消除。

要求建立通用 mutation helper，例如：

```text
update_social_space()
```

或者等价设计。

必须走：

```text
External Event
    ↓
StateMutation
    ↓
SocialSpace State Change
    ↓
SandboxEvent
```

至少产生：

```text
SOCIAL_SPACE_CHANGED
```

或者复用现有统一 EventType，只要语义明确即可。

禁止：

```python
social.character_presence = ...
social.social_temperature = ...
```

作为 External Influence 的 canonical 状态修改入口。

新增测试：

```text
TestExternalSocialEffectUsesMutationSpine
```

必须验证：

- QQ 外部事件进入
- social presence 变化
- social temperature 变化
- mutation log 存在 before/after
- Sandbox event 存在
- plugin 不直接修改 state

---

# 四、问题 3：删除硬编码 ACTIVITY_IDS

当前 runtime.py 有类似：

```python
ACTIVITY_IDS = {
    "play_minecraft": "gaming",
    "play_singleplayer": "gaming",
    "watch_animation": "reading",
    "drink_cola": "eating",
    ...
}
```

这会重新引入 Phase 1 已经消除的角色/场景专属 Action ID 硬编码。

必须重构。

## 正确方向

Activity 必须成为 Action Definition / Seed 的显式数据。

例如：

```text
ActionDefinition
    id
    name
    activity
    tags
    modes
    ...
```

或者使用等价字段。

World Seed 应该直接提供：

```text
play_minecraft
    activity = gaming

watch_animation
    activity = entertainment / reading

drink_cola
    activity = eating
```

Runtime 只使用：

```text
definition.activity
```

而不是通过 action id 猜活动。

---

## 要求

重构：

```text
_activity_index()
_action_for_activity()
```

使其只依赖：

```text
self.actions.definitions
```

中的结构化 activity metadata。

不能再依赖：

```text
ACTIVITY_IDS["xxx"]
```

禁止在 Runtime 中重新出现：

```text
play_minecraft
drink_cola
watch_animation
go_shopping_cola
take_out_trash
```

这种“Action ID → 行为语义”的专用映射表。

---

## 特别测试

必须保持第二角色测试：

阿澈：

- 没有 Minecraft
- 没有 cola
- 可以有完全不同的 activities / actions

但 Runtime 不需要知道：

```text
阿澈没有 Minecraft
```

Runtime 只知道：

```text
world seed 中没有对应 activity/action
```

新增测试：

```text
TestActivityMetadataIsSeedDriven
```

至少证明：

两个不同 Character Bible：

```text
World A:
activity = gaming

World B:
activity = crafting
```

不修改 runtime 核心代码即可正常运行。

---

# 五、问题 4：ExternalEventQueue 的 eviction 必须尊重 urgency

当前：

```python
if len(self._pending) >= self._maxlen:
    self._pending.popleft()
```

不能无条件淘汰最老事件。

必须设计明确的 eviction policy。

最低要求：

```text
critical > high > normal > low
```

当队列满：

### 新事件比最弱事件更重要

允许：

```text
low 被淘汰
new high 被接受
```

### 新事件比所有已有事件都弱

可以：

```text
拒绝新事件
```

并返回：

```text
False
```

或者等价结果。

### critical

critical 不应该因为普通事件进入而被无条件淘汰。

---

## 新增测试

至少：

```text
TestCriticalEventSurvivesQueuePressure

TestHighUrgencyReplacesLowUrgency

TestLowUrgencyRejectedWhenQueueIsFullOfHigherPriority

TestQueueEvictionIsDeterministic
```

并且不能破坏现有：

```text
FIFO within same urgency
```

---

# 六、重新做一次“直接赋值扫描”

Phase 3 修复后，对：

```text
app/sandbox/
plugins/chat/
```

执行静态搜索。

重点检查：

```text
character.location =
character.current_action_id =
social.*
pet.*
inventory.*
needs.*
object.*
```

确保业务路径没有绕过：

```text
Mutation
Event
```

---

# 七、重新检查 External Influence 边界

External Influence Evaluator 必须继续：

- 不改状态
- 不选具体 Action
- 不调用 LLM
- 不认识角色名字
- 不认识 QQ protocol object

保持：

```text
ExternalWorldEvent
    ↓
InfluenceDecision
```

---

# 八、不要修改以下内容

本轮不要：

- Memory
- Memory Feedback
- Embedding
- Continuity
- Character Bible
- Agent Runtime
- 新 World System
- 新 EventBus
- 新 ActionSystem
- LLM Decision
- QQ Conversation Architecture

也不要执行：

```text
git filter-repo
BFG
force push
history rewrite
```

---

# 九、测试要求

完成后必须：

```powershell
ruff check .
ruff format --check .
mypy app
pytest -q
```

要求：

- 所有原有 1053 tests 保留
- 新增修复测试
- 全部通过
- CI success

---

# 十、发布流程

开发目录：

`E:\WorkSpace ZCode\CatooBot`

GitHub mirror：

`E:\WorkSpace ZCode\CatooBot_github`

不要把：

```text
config/character_bible.md
API key
API token
.env
本地数据库
Zcode 私有工作流
```

同步到 GitHub。

不要修改 Git history。

---

# 十一、最终报告

必须报告：

```text
Remediation commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

1. _start_action mutation bypass:
修复结果:

2. SocialSpace mutation bypass:
修复结果:

3. ACTIVITY_IDS:
是否删除:
activity 是否由 Seed / ActionDefinition 提供:

4. Queue eviction:
策略:
critical 测试:
high 测试:
low 测试:

直接赋值扫描:
结果:

第二角色:
结果:

是否加入 Phase 4:
No
```

本轮完成后停止。

不要开始 Phase 4。