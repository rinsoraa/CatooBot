# CatooBot v2.1 Phase 5 Remediation
## Context Prompt Character-Neutral

### 一、当前基线

Phase 5 commit：

`604bd1a3574075cde62d868687506b90ac72264a`

当前 Phase 5 已经完成：

- CognitiveContext
- Sandbox Memory Retrieval
- Continuity Bridge
- Recent Experience
- Conversation Memory 与 Sandbox Memory 分离
- Context Trace
- Character ID 隔离
- Deterministic Retrieval
- Memory Budget
- No Mutation
- No LLM Retrieval
- No Embedding
- Initiative 复用同一桥接
- 1102 → 1118 tests passed
- CI success

本轮不要进入 Phase 6。

只修一个架构问题：

> Phase 5 的最终 Prompt 渲染层重新引入了角色性别硬编码。

---

# 二、问题

当前：

`app/character/context.py`

存在实际发送给 LLM 的文本：

```text
〖近期延续状态〗（她自己生活的近况……
〖她自己经历过的相关往事〗……
〖最近发生的经历〗（她刚经历过的事……
```

这些不是注释，而是 `CharacterContextBuilder._sandbox_blocks()` 的实际 Prompt 文本。

这违反：

```text
Character Bible
        ↓
CharacterDefinition
        ↓
Runtime
        ↓
Context
```

中的 Character-Agnostic 原则。

---

# 三、目标

所有 Sandbox Context Prompt 标签必须：

```text
character-neutral
```

不能假设：

- 女性
- 男性
- 任何固定代词
- “罐头”
- “小罐头”
- “她”
- “他”

角色身份和语言风格应该由：

```text
Persona / CharacterDefinition / LLM
```

决定。

---

# 四、修改 Context Prompt

将：

```text
〖近期延续状态〗（她自己生活的近况，仅作参考；与〖世界事实〗冲突时以世界事实为准）
```

改成类似：

```text
〖近期延续状态〗（角色近期生活状态，仅作参考；与〖世界事实〗冲突时以世界事实为准）
```

---

将：

```text
〖她自己经历过的相关往事〗
```

改成：

```text
〖相关生活记忆〗
```

或：

```text
〖过去的重要经历〗
```

---

将：

```text
〖最近发生的经历〗（她刚经历过的事，仅作参考）
```

改成：

```text
〖最近发生的经历〗（近期发生的重要经历，仅作参考）
```

保持语义不变，但完全去除角色性别假设。

---

# 五、不要修改 Memory Foundation

不要修改：

```text
app/sandbox/experience.py
app/sandbox/memory_foundation.py
```

除非为了测试当前 Prompt。

本轮只处理：

```text
app/character/context.py
```

以及必要的：

```text
tests/
```

---

# 六、Context 模块内部注释也顺便中性化

检查：

```text
app/sandbox/cognitive.py
app/character/context.py
```

把类似：

```text
what she may see
her world
her own life
她自己
她的世界
```

等角色固定性别描述改成：

```text
the character
character's world
character's life
角色
角色当前世界
```

注意：

这主要是代码可维护性，不要因为注释而大规模修改无关代码。

---

# 七、不要把“角色中性”变成“语言冷冰冰”

中性化针对的是：

> Context infrastructure 的事实标签。

不是要求：

```text
Persona
```

变成机械语言。

例如：

```text
〖过去的重要经历〗
- 完成了木工（做了一把椅子）
```

是正确的。

之后模型仍然可以根据 Character Persona 自然回答：

```text
“椅子终于做完啦ww”
```

Context Layer 不负责角色口吻。

---

# 八、增加测试

新增：

```text
TestSandboxContextIsCharacterNeutral
```

使用 synthetic Character：

```text
Lin
gender = male
```

以及现有：

```text
罐头
gender = female
```

调用：

```text
CharacterContextBuilder.build()
```

检查最终 Sandbox Context Prompt 不包含：

```text
她
他
罐头
小罐头
```

注意：

这个测试针对 **Context infrastructure 固定文本**。

不能禁止动态角色数据进入 Prompt。

例如：

```text
宠物名称
项目名称
空间名称
当前 Action 名称
```

这些来自 CharacterDefinition/World Seed，是允许的。

---

# 九、增加第三种性别场景

再做一个：

```text
gender = unspecified
```

的 synthetic Character。

确认 Context Builder：

- 正常工作
- 不出现她/他
- 不依赖 gender 字段
- 不产生异常

---

# 十、源码扫描

增加 guard：

扫描：

```text
app/character/context.py
app/sandbox/cognitive.py
```

检查固定角色代词：

```text
她
他
她的
他的
罐头
小罐头
```

允许：

```text
测试 fixture
注释中的历史文档
动态变量内容
```

禁止：

```text
业务 Prompt 模板
固定 Context 标签
```

---

# 十一、不要改变 Context 架构

本轮不要修改：

```text
CognitiveContext
SandboxMemoryStore.retrieve_relevant()
CharacterRuntime.respond()
MemoryManager
ContinuitySnapshot
```

除非测试需要极小兼容调整。

---

# 十二、不要进入 Phase 6

本轮禁止：

- Memory → Persona
- Memory → Relationship
- Memory → Action
- Memory → Decision
- LLM Memory ranking
- Embedding
- Vector Search
- EventBus 修改
- Mutation 修改
- World 修改
- ActionSystem 修改
- Character Bible 修改
- Git history rewrite

---

# 十三、测试

执行：

```powershell
ruff check .
ruff format --check .
mypy app
pytest -q
```

要求：

```text
原 1118 tests 全部保留
+
新增 remediation tests
=
全部通过
```

CI 必须 success。

---

# 十四、Git 双目录

开发目录：

`E:\WorkSpace ZCode\CatooBot`

GitHub 发布镜像：

`E:\WorkSpace ZCode\CatooBot_github`

发布前检查：

```text
API keys
API tokens
.env
真实 Character Bible
local DB
logs
Zcode private workflow
local paths
secrets
```

真实：

```text
E:\WorkSpace ZCode\CatooBot\config\character_bible.md
```

不得进入 GitHub。

不要：

```text
git filter-repo
BFG
force push
history rewrite
```

---

# 十五、最终报告

必须报告：

```text
Phase 5 Remediation commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

Context Prompt:
<是否完全 character-neutral>

固定性别词扫描:
<结果>

固定角色名扫描:
<结果>

Female Character:
<测试>

Male Character:
<测试>

Gender Unspecified:
<测试>

是否修改 Memory:
No

是否修改 Sandbox:
No

是否使用 LLM:
No

是否使用 Embedding:
No

是否进入 Phase 6:
No
```

完成后停止。

---

# 十六、验收标准

必须全部满足：

```text
✅ Context Prompt 不假定角色性别
✅ Context Prompt 不硬编码罐头身份
✅ 动态 Character Data 仍正常进入
✅ Female Character 正常
✅ Male Character 正常
✅ Unspecified Character 正常
✅ Memory retrieval 不变
✅ Continuity 不变
✅ Conversation Memory 不变
✅ Sandbox 仍完全只读
✅ 原 1118 tests 全保留
✅ 新测试通过
✅ CI success
```

本轮只完成 Character-Neutral Context 修复。

完成后停止，不进入 Phase 6。