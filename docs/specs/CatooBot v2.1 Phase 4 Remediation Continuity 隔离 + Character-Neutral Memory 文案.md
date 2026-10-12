# CatooBot v2.1 Phase 4 Remediation
## Continuity Isolation + Character-Neutral Memory Language

### 一、当前基线

当前 Phase 4 commit：

`4614f10922e4af01094277addee8099b81b19313`

Phase 4 主体已经完成并通过：

- Sandbox Event → Experience
- Experience → MemoryCandidate
- MemoryCandidate → existing memories
- provenance
- dedupe
- importance
- character_id isolation
- restart persistence
- ContinuitySnapshot
- Memory 不反向影响 Sandbox
- 无 LLM
- 无 Embedding

当前测试：

`1096 passed`

本轮不是 Phase 5。

只修两个明确问题：

1. ContinuitySnapshot persistence 的 character isolation
2. Experience / Memory 文案中的角色性别硬编码

---

# 二、问题 1：ContinuitySnapshot 必须按 character_id 隔离

当前实现：

```text id="1jv0m1"
sandbox_state["continuity_snapshot"]
```

是全局固定 key。

这与 Phase 4 已经建立的：

```text id="1bkm0m"
character_id
```

隔离原则不一致。

## 要求

将 ContinuitySnapshot persistence 改为 character-scoped。

推荐：

```text id="hpi7o7"
continuity_snapshot:<character_id>
```

或者：

```text id="j4bub7"
continuity_snapshot/<character_id>
```

使用等价稳定 key 即可。

必须满足：

```text id="o1anbq"
Character A
→ snapshot A

Character B
→ snapshot B
```

互不覆盖。

---

# 三、Continuity Snapshot 的 load / persist 必须使用相同 scoped key

当前：

```text id="9y7g8f"
persist()
load()
```

必须同时使用：

```text id="aw20qv"
character_id
```

构造 key。

不能：

```text id="b1kzji"
persist() 使用 scoped key
load() 仍使用旧固定 key
```

必须统一。

---

# 四、保持 backwards compatibility

如果历史数据库中存在：

```text id="28jmsv"
continuity_snapshot
```

不要删除。

允许：

```text id="3610vh"
旧 key fallback 仅在 character-scoped key 不存在时读取
```

但：

> 一旦产生新的 scoped snapshot，后续必须优先使用 scoped key。

如果旧 snapshot 没有可靠 character_id：

- 可以只作为旧版本兼容数据
- 不要把它错误地归属给某个 Character

---

# 五、必须增加多角色 Continuity 测试

新增：

```text id="l1tjx9"
TestContinuityIsolation
```

同一个 DB：

```text
Runtime A
character_id=A

Runtime B
character_id=B
```

分别创建完全不同状态：

```text A location != B location
A action != B action
A memory != B memory
```

然后：

```text A.persist_snapshot()
B.persist_snapshot()
```

再分别：

```text A.load()
B.load()
```

必须：

```text load(A) == A
load(B) == B
```

并且：

```text snapshot A 不含 B 数据
snapshot B 不含 A 数据
```

这个测试必须使用**同一个数据库**。

---

# 六、问题 2：Experience 文案不得假定角色性别

当前 Phase 4 新代码中存在类似：

```text id="j6v7h7"
她做完了...
她...到一半被打断
她回来继续...
她给...
她发现...
她找...
她在...
```

这会让 Character Bible 换成男性、非二元或其他角色时，Memory 内容直接出现错误。

这是 Character-Bible-driven 架构的退化。

---

# 七、不要引入 LLM / NLG

本轮绝对不要为了修这个问题接：

```text id="o9p3x9"
LLM
```

当前 Memory foundation 应继续是确定性的。

正确方式是：

> 使用中性事实句。

---

# 八、Experience summary 改为 Character-Neutral

建议：

### Action Completed

不要：

```text id="qp4gyh"
她做完了{name}
```

改成：

```text id="5cvdk2"
完成了{name}
```

或者：

```text id="4jy7ch"
{name}已完成
```

---

### Action Interrupted

不要：

```text id="vagj3d"
她{name}到一半被打断了
```

改成：

```text id="7m5kdk"
{name}进行到一半被打断
```

---

### Action Resumed

不要：

```text id="aqf6v8"
她回来继续{name}
```

改成：

```text id="kzrq80"
继续进行{name}
```

---

### Pet Care

不要：

```text id="i8u5mm"
{pet}饿了，她给它添了{food}
```

改成：

```text id="h4gvf3"
{pet}饿了，已添{food}
```

或者：

```text id="6qqxej"
给{pet}添了{food}
```

---

### Knowledge Learned

不要：

```text id="7mjbkg"
她第一次知道了...
```

改成：

```text id="xm6if7"
首次获知：...
```

---

### External Influence

不要：

```text id="9h75lm"
{actor}找她一起做别的事，她答应了
```

改成：

```text id="ql2cn9"
{actor}发出的邀请改变了当前安排
```

注意：

不能在没有事件证据的情况下自动写：

```text
她答应了
```

External Influence 只说明：

> 外部事件改变了 Sandbox 当前安排。

---

### Social Contact

不要：

```text id="9i1z2j"
她在{space}露了个面
```

改成：

```text id="m7kqd3"
在{space}发生了社交活动
```

---

# 九、Memory content 同样检查

不仅：

```text id="33q6n3"
Experience.summary
```

需要修改。

还必须检查：

```text id="y6g9t7"
MemoryCandidate.content
```

目前 `memory_foundation.py` 中也存在：

```text id="63xki2"
她做完了...
她...被打断...
她回来继续...
她给...
她发现...
```

全部改成 Character-Neutral 表述。

---

# 十、必须保持自然，但优先“事实正确”

最终结构应该是：

```text id="4q9fp6"
Sandbox Fact
    ↓
Neutral Experience
    ↓
Neutral Memory
    ↓
未来 Phase 的 Character/NLG Layer
    ↓
自然语言表达
```

不要让 Memory foundation 负责角色口吻。

例如：

```text
Memory:
“完成了 Minecraft 小城图书馆屋顶的建造。”
```

以后 Persona/NLG 再决定：

```text
“我昨晚把图书馆屋顶搞完了www”
```

这是正确分层。

---

# 十一、第二角色回归必须加强

现有“阿澈”测试之外，增加：

```text id="1u8lva"
TestMemoryLanguageIsCharacterNeutral
```

至少验证：

阿澈完成 Action 后的：

```text id="zclv57"
Experience.summary
Memory.content
```

不得包含：

```text
她
罐头
小罐头
小喵
空凛
可乐
Minecraft
```

注意：

其中：

```text Minecraft
```

只有在阿澈 Action 本身不存在 Minecraft 时才检查。

不要机械禁止所有角色名称/世界对象进入 Memory。

真正要求是：

> 不出现未经 Seed 赋予阿澈的角色专属数据。

---

# 十二、最好增加一个第三角色测试

不要只测试：

```text
罐头
阿澈
```

构造一个非常小的 synthetic Character Bible：

```text id="g4xqav"
姓名：Lin
性别：男性
宠物：无
活动：
- 木工
- 阅读
```

验证：

```text id="1y3gq2"
Action complete
→ Experience
→ Candidate
→ Memory
```

内容自然变成：

```text
完成了木工
```

而不是：

```text
她做完了木工
```

这可以真正证明 Memory foundation 是 character-agnostic。

---

# 十三、继续保持 Phase 4 边界

本轮禁止：

- Memory → Conversation
- Memory → Decision
- Memory → Persona
- Memory → Relationship
- Embedding
- Vector Search
- LLM Memory extraction
- LLM Memory judge
- 新 EventBus
- 新 Mutation System
- 新 World System
- 新 Action System
- Character Bible 修改
- Git history rewrite

---

# 十四、Persistence 要求

不得删除现有：

```text id="5z2mgy"
migration 22
sandbox_experiences
sandbox_memory_candidates
memories.character_id
memories.provenance
memories.dedupe_key
```

如果只修改：

```text id="f0c69x"
sandbox_state key
```

无需新 migration。

优先使用现有 state API。

---

# 十五、重新做一次源码扫描

扫描：

```text id="239o8y"
app/sandbox/experience.py
app/sandbox/memory_foundation.py
app/sandbox/continuity_snapshot.py
```

检查以下角色专属 / 性别化文案：

```text
她
罐头
小罐头
小喵
空凛
可乐
play_minecraft
```

其中：

- 角色事实从 Runtime/Seed 动态取得是允许的
- 业务文本硬编码是不允许的
- 中性模板是允许的

---

# 十六、测试要求

完成后执行：

```powershell id="q4o1ip"
ruff check .
ruff format --check .
mypy app
pytest -q
```

要求：

```text
原 1096 全部保留
+
新增 remediation tests
=
全部通过
```

CI 必须 success。

---

# 十七、最终报告

必须报告：

```text id="sy1dqg"
Phase 4 Remediation commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

Continuity persistence key:
<最终格式>

Legacy snapshot fallback:
<是否存在>

Multi-character continuity isolation:
<测试结果>

Experience language:
<是否已经完全 character-neutral>

Memory language:
<是否已经完全 character-neutral>

第三角色:
<测试结果>

源码扫描:
<结果>

是否使用 LLM:
No

是否使用 Embedding:
No

是否进入 Phase 5:
No
```

完成后停止。

---

# 十八、验收标准

必须同时满足：

```text
✅ ContinuitySnapshot 按 character_id 持久化
✅ 同 DB 多角色 Snapshot 不覆盖
✅ load / persist key 一致
✅ 旧固定 snapshot key 可兼容读取
✅ Experience 不硬编码角色性别
✅ Memory Candidate 不硬编码角色性别
✅ Memory content 不硬编码角色性别
✅ 第二角色通过
✅ 第三 synthetic 角色通过
✅ Character Bible 继续作为唯一角色来源
✅ Memory 不反向修改 Sandbox
✅ 原 1096 tests 全部保留
✅ CI success
```

本轮只修这两个问题。

完成后停止，不进入 Phase 5。