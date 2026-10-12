# CatooBot v2.1 Phase 3.5
## State Authority Closure

### 一、基线

当前已经完成：

- Phase 1
- Phase 2
- Phase 3
- Phase 3 Remediation

当前最新 commit：

`71fa0c30983a6d0236c42ffde92de30ceddd341d`

本次不是进入 Phase 4。

本轮只做：

> **把 Sandbox 中尚未完全进入 Mutation + Event spine 的三个持久状态域收口。**

三个目标：

```text
Need
Project Progress
Character Knowledge
```

完成后停止。

---

# 二、为什么做这一阶段

当前 Sandbox 已经基本建立：

```text
Event
  ↓
Interaction / Action
  ↓
Mutation
  ↓
State Change
```

但仍存在几个旧路径：

```text
Action
  ↓
NeedSystem.relieve/add
  ↓
直接修改 need.level

Action
  ↓
project["progress"] = ...
  ↓
事后记录 Mutation

Action
  ↓
knowledge[...] = ...
```

这会让未来 Memory / Continuity 无法可靠区分：

```text
世界事实
```

和：

```text
事后推测
```

所以现在先收口。

---

# 三、目标 1：Need Mutation

## 当前问题

`NeedSystem.relieve()` / `NeedSystem.add()` 直接改变：

```python
need.level
```

而 `_complete_action()` 直接调用它们。

## 要求

保留 `NeedSystem` 作为状态容器和计算工具，但运行时业务修改必须统一经过 Runtime 的 canonical helper。

例如可以建立：

```text
adjust_need()
```

或扩展现有：

```text
_adjust_need()
```

支持：

```text
delta
```

以及：

```text
relative reduction
```

两种语义。

关键要求：

每次实际 Need 变化都必须产生：

```text
StateMutation
    target = needs:<key>
    field = level
    before
    after
    source
    reason
```

并产生：

```text
NEED_CHANGED
```

---

## Action completion

将：

```python
self.needs.relieve(definition.need_relief)
```

改成统一 mutation 路径。

例如：

```text
before
 ↓
calculate relief result
 ↓
canonical mutation
 ↓
NEED_CHANGED
```

将：

```python
self.needs.add(key, amount)
```

同样改掉。

不要继续让 ActionSystem/NeedSystem 自己偷偷改变世界状态。

---

# 四、目标 2：Project Progress Mutation

## 当前问题

当前 Action 完成时存在：

```python
project["progress"] = ...
```

虽然之后记录 Mutation，但属于：

```text
直接修改
→
补日志
```

而不是 canonical mutation。

## 要求

建立通用：

```text
update_project_progress()
```

或等价 helper。

必须支持：

```text
project_id
delta
source
reason
correlation
causation_id
```

执行：

```text
读取 before
 ↓
计算 after
 ↓
进行 canonical mutation
 ↓
记录 StateMutation
 ↓
发布 PROJECT_PROGRESS_CHANGED
```

增加：

```text
SandboxEventType.PROJECT_PROGRESS_CHANGED
```

或者语义等价事件。

---

## 项目完成

当：

```text
before < 1
after == 1
```

可以在同一事件 payload 中明确：

```text
completed = true
```

不要另造第二套 Project Event System。

---

# 五、目标 3：Character Knowledge Mutation

当前存在类似：

```python
self.knowledge[key] = ...
```

这是角色知识状态。

它以后会成为：

```text
Sandbox
 ↓
Knowledge
 ↓
Memory / Continuity
```

的重要输入。

因此必须建立统一入口。

例如：

```text
set_knowledge()
```

或者：

```text
update_knowledge()
```

要求：

```text
before
after
source
reason
```

全部可追踪。

建立：

```text
KNOWLEDGE_CHANGED
```

事件。

事件 payload 至少包含：

```text
key
before
after
source
reason
```

如果当前知识模型适合记录：

```text
learned_at
source
confidence
```

继续保留。

不要重新设计整个 Knowledge 模型。

---

# 六、Knowledge 和 Memory 必须继续分开

非常重要。

本阶段不要接：

```text
Memory
Embedding
LLM
Long-term Memory
```

当前只建立：

```text
Knowledge
    ↓
可追踪的 Sandbox State
```

以后 Phase 4 再决定：

```text
哪些 Knowledge
哪些 Events
哪些 Experiences
→
进入 Memory
```

---

# 七、统一 Mutation Spine

完成后运行时状态变化应该尽可能形成：

```text
Action / Interaction / Tick / External Influence
                    ↓
             Canonical Mutation
                    ↓
              StateMutation
                    ↓
             SandboxEvent
```

三个新域：

```text
Need
Project
Knowledge
```

也必须进入这个模式。

---

# 八、允许保留的例外

不要为了“消灭所有 `=`”而过度重构。

以下允许直接赋值：

### 1. 世界初始化

```text
_seed_fresh()
```

### 2. 快照恢复

```text
_restore / _apply_snapshot
```

### 3. Derived Projection

明确声明为派生值，例如：

```text
character.energy
character.modes
```

其 canonical source 必须仍然明确。

### 4. 纯计算对象 / transient runtime data

例如：

```text
当前 action progress
queue index
temporary trace
```

如果不是持久世界状态，不强制事件化。

---

# 九、重点检查

对：

```text
app/sandbox/
```

做状态写入审计。

重点搜索：

```text
.need.level =
.progress =
knowledge[
.knowledge[
```

然后判断：

```text
canonical state mutation
```

还是：

```text
initialization / restore / derived / calculation
```

不要机械修改所有结果。

---

# 十、测试

新增至少：

## Test 1：Need action completion

验证：

```text
Action complete
→ Need changes
→ StateMutation exists
→ NEED_CHANGED exists
```

---

## Test 2：Need cost

验证：

```text
Action
→ need_cost
→ mutation
→ NEED_CHANGED
```

---

## Test 3：Project progress

验证：

```text
Action
→ project progress
→ PROJECT_PROGRESS_CHANGED
→ mutation before/after
```

并测试：

```text
progress >= 1
```

的完成状态。

---

## Test 4：Knowledge

验证：

```text
World fact
→ knowledge update
→ KNOWLEDGE_CHANGED
→ before/after trace
```

---

## Test 5：No direct Need mutation

源码 guard：

Action completion 不得直接：

```python
self.needs.relieve(...)
self.needs.add(...)
```

除非这些调用最终经过已经统一封装的 canonical mutation path。

---

## Test 6：Project canonical path

确认 Action completion 不再：

```python
project["progress"] = ...
```

而是调用统一 helper。

---

## Test 7：Knowledge canonical path

确认业务路径不再：

```python
self.knowledge[...] = ...
```

---

## Test 8：Second Character

继续使用阿澈。

确认：

```text
Need
Project
Knowledge
```

都不依赖：

```text
罐头
小喵
空凛
可乐
Minecraft
```

---

# 十一、事件因果关系

新增事件必须正确连接：

例如 Action completion：

```text
ACTION_COMPLETED
      ↓
NEED_CHANGED
PROJECT_PROGRESS_CHANGED
KNOWLEDGE_CHANGED
```

使用合理的：

```text
causation_id
correlation_id
```

不要把这些事件变成互相独立的孤岛。

---

# 十二、Persistence

不要重做 Database。

只要确保：

```text
Need
Project
Knowledge
```

现有 persistence 能正确保存。

EventBus 仍然 transient。

Sandbox EventRecord / mutation log 继续使用现有结构。

---

# 十三、不要做的事情

本阶段禁止：

- Memory
- Memory extraction
- Embedding
- Continuity 重构
- LLM
- QQ 架构修改
- External Influence 修改
- 新 EventBus
- 新 Mutation 系统
- 新 ActionSystem
- 新 WorldSystem
- Character Bible 修改
- Git history rewrite

---

# 十四、测试要求

必须执行：

```powershell
ruff check .
ruff format --check .
mypy app
pytest -q
```

必须：

```text
所有原有 1066 tests 保留
+
新增 Phase 3.5 tests
=
全部通过
```

CI 必须 success。

---

# 十五、GitHub 双目录

开发只在：

`E:\WorkSpace ZCode\CatooBot`

发布同步：

`E:\WorkSpace ZCode\CatooBot_github`

发布前继续检查：

- API keys
- tokens
- .env
- local DB
- logs
- private workflow
- real Character Bible
- local paths

禁止：

```text
git filter-repo
BFG
force push
history rewrite
```

---

# 十六、最终报告

必须报告：

```text
Phase 3.5 commit:
HEAD:
origin/main:
remote main:

ruff:
ruff format:
mypy:
pytest:

新增测试:
总测试:

Need:
canonical mutation:
event:

Project:
canonical mutation:
event:

Knowledge:
canonical mutation:
event:

直接状态写入审计:
结果:

第二角色:
结果:

是否加入 Memory:
No

是否进入 Phase 4:
No
```

完成后立即停止。

---

# 十七、验收原则

本阶段最终要证明的不是：

> “三个模块有了 helper。”

而是：

> **真实运行时产生的 Need / Project / Knowledge 状态变化，已经全部可以沿着 Mutation → Event 追溯。**

完成后停止，不进入 Phase 4。