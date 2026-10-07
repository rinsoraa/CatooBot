# CatooBot Minecraft Phase 5B
# QQ Task Entry + Unified Task Control

## 0. 阶段目标

Phase 5A / 5A.1 已经完成：

- 19 个 Minecraft 原子工具
- SAFE / LOW / MEDIUM 风险策略
- ConfirmationStore
- USER / TASK / SYSTEM / BACKGROUND / INITIATIVE TurnOrigin
- TaskRuntime
- Plan / Execution 分离
- Plan version / plan_hash / plan_history
- WORLD_CHANGED / TARGET_LOST 重规划
- Authorization Expiry
- Runtime Restart Recovery
- SQLite checkpoint
- pause / resume / cancel
- Task 状态机与 ActionRuntime
- Minecraft Chat → TaskRuntime
- WebUI → TaskRuntime

Phase 5B 的唯一核心目标：

> **把 QQ 接入现有 TaskRuntime，建立统一的任务入口、确认、暂停、继续、停止、重规划和状态反馈链路。**

本阶段不得重新实现任务执行引擎，不得绕过 TaskRuntime，不得新增 Minecraft compound tool。

---

# 一、硬性架构原则

## 1. QQ 只是入口，不是执行器

禁止：

```text
QQ message
→ LLM
→ minecraft_dig
```

禁止：

```text
QQ message
→ AgentBridge
→ MinecraftService
```

正确路径必须是：

```text
QQ message
→ QQ Task Entry
→ Task Intent / Task Planner
→ TaskRuntime.create_task()
→ SAFE observation
→ Plan
→ PENDING_CONFIRMATION
→ USER confirmation
→ TaskRuntime.confirm_and_start()
→ TaskRuntime.drive()
→ existing Tool / Policy / Agent Bridge
→ MinecraftService
```

所有真实世界动作必须经过现有 TaskRuntime。

不得为了 QQ 增加新的：

- `minecraft_task`
- `minecraft_replan`
- `minecraft_confirm`
- `minecraft_execute_task`
- `minecraft_do`
- `minecraft_stop_task`

之类的 Minecraft Tool。

---

# 二、QQ Task Entry

## 2.1 建立正式 QQ 任务入口

实现一个独立的 QQ Task Entry 层。

建议职责：

```text
QQ Event
↓
识别是否为任务入口
↓
提取任务目标
↓
绑定 QQ identity
↓
调用现有 Planner / TaskRuntime
```

不要把 Task Entry 逻辑塞进 CatooBot 普通人格回复函数。

推荐新增独立模块，例如：

```text
app/tasks/qq_entry.py
```

或等价结构。

具体文件名由现有项目架构决定，但职责必须独立。

---

# 三、任务入口识别

## 3.1 不能因为普通聊天包含“挖 / 木头 / Minecraft”等词就自动创建任务

必须有明确的任务意图。

至少支持：

```text
@罐头 帮我去挖一块橡木
@罐头 去附近找一棵橡木挖回来
@罐头 帮我把那边的木头挖掉
```

以及当前项目已有的任务语义入口。

普通聊天：

```text
“我今天想砍树”
“木头真的好难找”
“你觉得橡木好看吗”
```

不得创建 Task。

---

# 四、任务身份绑定

这是本阶段的安全重点。

## 4.1 QQ identity 不得只使用 display name

必须使用稳定的平台用户标识。

至少形成：

```text
platform = qq
user_id = QQ stable user id
session_id = existing QQ conversation/session identity
```

不要使用：

```text
nickname
card_name
display_name
message text
```

作为唯一身份。

---

# 五、私聊与群聊

## 5.1 私聊

私聊任务：

```text
task.user_id = QQ sender id
task.session_id = 当前 QQ private session
```

只有该 QQ 用户能够：

- confirm
- pause
- resume
- cancel
- continue
- replan

---

## 5.2 群聊

群聊任务必须绑定：

```text
initiator_user_id
group_id
conversation/session_id
```

任务创建者才拥有任务控制权。

群内其他成员：

```text
确认
暂停
继续
停止
```

全部拒绝。

拒绝不得改变任务状态。

例如：

```text
你不是这个任务的发起人，这个任务由 @Rinsora 创建。
```

不要执行任何 Minecraft 动作。

---

# 六、统一任务生命周期

QQ 必须使用现有 TaskRuntime 状态。

禁止自行维护第二套任务状态机。

最终 QQ 生命周期：

```text
QQ Task Intent
      ↓
PLANNING / SAFE OBSERVATION
      ↓
PENDING_CONFIRMATION
      ↓
USER CONFIRM
      ↓
RUNNING
      ↓
WAITING_ACTION / ...
      ↓
SUCCEEDED
```

异常状态必须直接映射现有 TaskRuntime：

```text
PAUSED
REPLANNING
PENDING_CONFIRMATION
CANCELLED
FAILED
```

QQ 层不得创造平行状态：

```text
qq_running
qq_waiting
qq_done
```

---

# 七、任务确认

## 7.1 确认必须仍然是真实 USER Origin

QQ 输入：

```text
确认
```

只能通过：

```text
TurnOrigin.USER
```

进入现有确认机制。

严禁：

```text
origin=SYSTEM
origin=TASK
origin=BACKGROUND
```

冒充 QQ 用户。

---

## 7.2 Confirmation 必须绑定

至少必须继续绑定：

```text
task_id
user_id
session_id
plan_hash
plan_version
arguments
```

沿用现有 ConfirmationStore。

禁止 QQ 自己实现另外一套 confirmation token。

---

# 八、QQ 确认文本

任务计划应该使用现有 TaskRuntime 的 summary。

例如：

```text
罐头准备这样做：

1. 找附近的橡木
2. 走过去
3. 挖掉 1 块橡木
4. 捡起来
5. 检查物品栏

这是第 1 版计划。

需要你确认后我才会动手。

回复「确认」开始。
```

注意：

不要在 QQ 层重新拼出一套独立 Plan。

QQ 只是展示现有 Plan summary。

---

# 九、统一控制命令

QQ 必须支持现有 TaskRuntime 的任务控制。

至少：

```text
确认
继续
暂停
停止
```

推荐同步支持：

```text
确认这个任务
继续任务
暂停这个任务
停止这个任务
```

必要时允许当前任务上下文消除歧义。

---

# 十、控制语义

## 10.1 确认

只有：

```text
PENDING_CONFIRMATION
```

可以确认。

确认后调用：

```text
TaskRuntime.confirm_and_start(...)
```

不得直接执行某个 Minecraft tool。

---

## 10.2 暂停

调用现有：

```text
TaskRuntime.pause(...)
```

如果当前正在运行异步 Action：

必须沿用已有 action cancellation 机制。

不要在 QQ 层自己：

```text
stop bot
```

更不能直接：

```text
minecraft_stop
```

来代替 TaskRuntime pause。

---

## 10.3 继续

调用现有：

```text
TaskRuntime.resume(...)
```

必须执行所有已有安全检查。

尤其：

```text
replan_required
authorization_expired
plan_hash mismatch
task state
```

都必须继续由 TaskRuntime 判断。

QQ 层不能强行 resume。

---

## 10.4 停止

调用：

```text
TaskRuntime.cancel(...)
```

而不是自行杀 ActionRuntime。

TaskRuntime 负责：

```text
task → action cancellation
checkpoint
terminal state
```

---

# 十一、Authorization Expiry

QQ 必须复用 5A.1 的授权到期机制。

例如：

```text
授权过期
    ↓
PENDING_CONFIRMATION
    ↓
QQ 通知
    ↓
需要重新确认
```

不得重新创建一个完全不同的任务。

`plan_hash` 应保持不变。

旧 confirmation 不得复用。

---

# 十二、Replanning

QQ 必须完整支持 5A.1 的重规划语义。

例如：

```text
QQ：

确认

罐头执行任务

世界变化
↓
TaskRuntime
↓
v1 SUPERSEDED
↓
replan
↓
v2
↓
PENDING_CONFIRMATION
```

QQ 必须收到：

```text
目标已经发生变化，原计划已停止。

新的计划（第 2 版）：

...

需要重新确认。
回复「确认」继续。
```

特别注意：

**重规划期间 QQ 层不得自动说：**

```text
我帮你自动换一个目标了
```

也不得自动确认 v2。

必须真正等待 USER confirmation。

---

# 十三、QQ 状态反馈

TaskRuntime 已经有任务事件。

QQ 应订阅现有 Task event，而不是自己轮询执行 Action。

事件：

```text
task.created
task.replanning
task.authorization_expired
task.recovered
task.step.*
task.completed
task.failed
task.cancelled
```

根据当前项目实际 event naming 复用，不得重复制造第二套事件。

---

# 十四、事件 → QQ 消息

QQ 反馈应该简短。

例如：

```text
🌱 我开始处理了。
```

步骤：

```text
🚶 正在过去……
⛏️ 到地方了，开始挖。
📦 正在捡东西……
🔎 最后检查一下背包。
```

完成：

```text
完成啦，已经拿到了 1 个橡木原木。
```

暂停：

```text
先停这里了。
```

重规划：

```text
刚才目标发生了变化，原计划已经作废。
我重新观察了一次，需要你重新确认新的计划。
```

授权过期：

```text
刚才的操作授权已经过期了。
计划没有改变，但需要重新确认。
```

不要把：

- action_id
- checkpoint ID
- plan hash
- raw world snapshot
- internal state
- tool call JSON

直接发给 QQ 用户。

---

# 十五、事件去重

QQ 通知必须幂等。

同一个 event 不允许重复发送：

```text
完成了
完成了
完成了
完成了
```

至少需要：

```text
task_id
event id / checkpoint id / event timestamp + deterministic fingerprint
```

进行去重。

不能依赖“这一次 Python 进程应该只有一个事件”。

---

# 十六、任务归属与并发

必须防止：

```text
用户 A
→ 创建任务 A

用户 B
→ “继续”

结果继续了 A
```

绝对禁止。

所有控制入口必须重新验证：

```text
task.user_id == caller.user_id
task.session_id / group ownership
```

---

# 十七、多个任务

第一版不要允许复杂并发。

推荐策略：

```text
同一个 user/session
最多一个 ACTIVE/PENDING_CONFIRMATION/PAUSED task
```

如果已有活动任务：

```text
你还有一个任务正在处理中。

当前：
[任务摘要]

请先：
暂停 / 停止 / 继续
```

不要同时创建第二个 Minecraft Task。

不同用户是否允许并发，由当前 TaskRuntime / ActionRuntime 的现有能力决定。

本阶段不要为了支持 QQ 而重构 ActionRuntime 并发模型。

---

# 十八、Minecraft Chat / WebUI / QQ 统一

5B 的一个核心门禁：

同一个 TaskRuntime task：

```text
创建来源 = QQ
```

之后必须能够从已有 TaskRuntime 统一控制，而不是变成：

```text
QQTaskRuntime
WebTaskRuntime
MinecraftTaskRuntime
```

必须仍然只有一个：

```text
TaskRuntime
```

入口不同：

```text
QQ
Minecraft Chat
WebUI
```

---

# 十九、QQ 不得绕过 allow_medium

保持：

```text
allow_medium=false
```

默认配置绝对不改变。

QQ：

```text
帮我挖木头
```

最终计划里有：

```text
minecraft_dig
risk=MEDIUM
```

依旧必须经过：

```text
Policy
allow_medium
Confirmation
Task authorization
```

QQ 本身不能成为 MEDIUM 白名单。

---

# 二十、QQ 不得成为 SYSTEM confirmation source

必须增加/验证安全测试：

```text
QQ task creation = USER
QQ confirmation = USER
```

但：

```text
WebUI developer action
SYSTEM
BACKGROUND
TASK
```

不能消费属于 QQ 用户的 confirmation。

---

# 二十一、Session Identity 设计

优先复用现有 CatooBot QQ session identity。

不要重新发明：

```text
qq_user_session
qq_task_session
qq_confirmation_session
```

三套对象。

应尽可能形成：

```text
QQ Event
    ↓
existing Session
    ↓
TaskRuntime session_id
```

如果现有 session ID 不适合作为 task ownership key：

新增一个明确的 canonical identity adapter，而不是散落字符串拼接。

---

# 二十二、普通聊天与任务必须隔离

非常重要。

下面：

```text
@罐头 你在干嘛
```

仍然是普通人格对话。

下面：

```text
@罐头 帮我去附近找橡木并挖一块回来
```

才进入 Task Entry。

而：

```text
我想吃蛋糕
```

绝对不能变成 Task。

任务入口判断失败：

```text
→ 正常 CatooBot conversation pipeline
```

不能丢消息。

---

# 二十三、不要让任务系统吞掉人格回复

Task Entry 只有在确认是任务请求后才 consume event。

否则：

```text
QQ
→ normal conversation
```

继续走原人格。

---

# 二十四、任务计划来源

优先复用 Phase 5A 已有 Planner。

不要为了 QQ 新建：

```text
QQPlanner
MinecraftPlanner
```

如果当前 Planner 的输入接口不是自然语言，可以添加：

```text
TaskIntent
```

作为统一中间层：

```text
QQ text
↓
TaskIntent
↓
existing Planner
↓
TaskPlan
↓
TaskRuntime
```

---

# 二十五、TaskIntent 最小结构

建议：

```python
TaskIntent(
    objective: str,
    source: "qq",
    user_id: str,
    session_id: str,
    conversation_id: str | None,
)
```

不要把 QQ 原始消息对象直接塞进 TaskRuntime。

TaskRuntime 只应该收到规范化身份和任务目标。

---

# 二十六、错误处理

QQ 层不得泄漏 Python traceback。

至少将：

```text
TASK_NOT_FOUND
NOT_TASK_OWNER
INVALID_STATE
PENDING_CONFIRMATION
AUTHORIZATION_EXPIRED
REPLAN_REQUIRED
OFFLINE
BUSY
WORLD_CHANGED
TARGET_LOST
ACTION_FAILED
TIMEOUT
CANCELLED
```

映射成自然语言。

例如：

```text
这个任务已经结束了。
```

```text
这个任务已经需要重新规划，不能直接继续。
```

```text
罐头现在不在线，暂时做不了。
```

---

# 二十七、QQ 任务状态查询

支持自然语言：

```text
任务怎么样了
现在到哪了
任务还在做吗
```

以及已有控制语义。

优先当前 session 中唯一 active task。

如果存在多个历史任务：

不要模糊选择。

---

# 二十八、任务状态摘要

QQ 返回的状态应该类似：

```text
任务：去附近找一棵橡木并带回来

状态：进行中
第 3 / 5 步

当前：
正在捡起目标物品。
```

不要直接返回完整 TaskRecord。

---

# 二十九、WebUI

Phase 5B 不需要大规模重做 WebUI。

只需确保：

```text
QQ 创建的任务
```

能够正常显示在现有 Task Panel：

```text
source=QQ
user
task state
plan_version
plan history
authorization
replan reason
recovery
```

如果当前 UI 已经有对应字段，优先复用。

---

# 三十、审计

Task checkpoint 必须记录：

```text
source = qq
user_id
session_id
task_id
plan_version
```

但不得记录：

```text
QQ access token
QQ bot credential
private authentication secrets
```

---

# 三十一、安全测试

至少新增：

## A. QQ task creation

```text
QQ USER
→ Task created
```

PASS

## B. 普通 QQ 聊天

```text
normal chat
→ no task
```

PASS

## C. QQ confirmation

```text
QQ USER
→ confirm
→ task starts
```

PASS

## D. 非任务发起人确认

```text
User A creates task
User B says 确认
→ rejected
→ task unchanged
```

PASS

## E. SYSTEM confirmation rejection

```text
SYSTEM
→ tries confirm QQ task
→ rejected
```

PASS

## F. authorization expiry

```text
QQ task
→ expire
→ QQ receives re-confirm request
→ old confirmation rejected
→ new confirmation works
```

PASS

## G. replan

```text
QQ task
→ WORLD_CHANGED
→ v1 SUPERSEDED
→ v2
→ QQ receives new plan
→ without confirmation = zero world actions
→ QQ USER confirms
→ v2 runs
```

PASS

## H. pause

```text
QQ
→ 暂停
→ TaskRuntime.pause
```

PASS

## I. resume

```text
QQ
→ 继续
→ TaskRuntime.resume
```

PASS

## J. cancel

```text
QQ
→ 停止
→ TaskRuntime.cancel
→ no leaked action
```

PASS

## K. restart

```text
QQ task
→ process restart
→ existing 5A.1 recovery semantics
```

PASS

## L. cross-session isolation

```text
User A / Group A
≠
User B / Group B
```

PASS

---

# 三十二、真实 QQ 门禁

必须实际运行 NapCat / QQ 环境。

至少完成：

### Real QQ Smoke 1

```text
QQ:
“帮我找附近的一块橡木”
```

必须：

```text
Task created
→ SAFE observations
→ plan
→ confirmation
```

### Real QQ Smoke 2

```text
QQ:
确认
```

必须真实执行：

```text
move_to
dig
pickup
inventory
```

最终：

```text
FINAL INVENTORY VERIFIED
```

### Real QQ Smoke 3

重规划：

```text
QQ 创建任务
→ QQ 确认
→ 外部 /setblock 修改目标
→ TARGET_LOST / WORLD_CHANGED
→ v1 SUPERSEDED
→ QQ 收到 v2
→ 未确认前 0 world action
→ QQ “确认”
→ v2 success
```

### Real QQ Smoke 4

授权到期：

```text
QQ
→ task confirmation
→ TTL expire
→ QQ receives re-confirm
→ old confirmation rejected
→ new confirmation
→ success
```

### Real QQ Smoke 5

权限隔离：

```text
QQ User A 创建任务
QQ User B 尝试：
确认
暂停
继续
停止
```

全部：

```text
REJECTED
```

任务不能受到影响。

---

# 三十三、真实门禁判定

Phase 5B 不允许因为单元测试全部通过就 PASS。

必须：

```text
Code                   PASS
Unit                   PASS
Integration            PASS
Security               PASS
WebUI                   PASS
CI                     PASS

REAL QQ
task entry             PASS
confirmation           PASS
task execution         PASS
pause/resume            PASS
cancel                  PASS
replanning              PASS
authorization expiry   PASS
ownership isolation     PASS
```

任意一项真实 QQ 硬门禁失败：

```text
Phase 5B = BLOCKED
```

不要用 mock QQ 代替真实 QQ。

---

# 三十四、禁止范围

Phase 5B 不做：

- 新 Minecraft 工具
- 新 TaskRuntime 状态
- 新 ActionRuntime action
- Minecraft 建造
- 自动采矿
- 自动资源链
- 自动导航搜索
- 自动规划整个生存流程
- QQ 自己执行工具
- QQ 自己维护确认系统
- 长期 Minecraft memory
- 多 Agent 协作
- 自动跨进程恢复 Mineflayer action
- 任务调度器
- 定时任务

这些全部留给后续阶段。

---

# 三十五、实现顺序

建议严格按顺序：

```text
1. inspect existing QQ event/session pipeline
2. define canonical QQ identity adapter
3. implement QQ Task Entry
4. connect existing Planner
5. connect existing TaskRuntime
6. connect confirmation
7. connect pause/resume/cancel
8. connect task events → QQ
9. add ownership isolation
10. add security tests
11. add real QQ smoke
12. run complete CI
13. run real QQ gates
14. write docs
15. commit
```

不要一开始同时修改人格系统、Minecraft runtime、TaskRuntime 三处。

---

# 三十六、最终交付

必须新增：

```text
QQ Task Entry
Unified Task Control
QQ identity binding
QQ task event adapter
Security tests
Real QQ smoke script / procedure
Documentation
Changelog
```

不要修改已有：

```text
19 Minecraft tools count
allow_medium default
TaskRuntime security model
ConfirmationStore semantics
ActionRuntime contract
MinecraftService API
```

除非测试证明存在必要兼容性问题。

---

# 三十七、最终报告格式

完成后必须输出：

```text
PHASE 5B = PASS / BLOCKED

Commit:
CI:

QQ Task Entry:
PASS / FAIL

Normal chat isolation:
PASS / FAIL

Confirmation:
PASS / FAIL

Ownership:
PASS / FAIL

Pause:
PASS / FAIL

Resume:
PASS / FAIL

Cancel:
PASS / FAIL

Replanning:
PASS / FAIL

Authorization Expiry:
PASS / FAIL

Runtime Restart:
PASS / FAIL

Real QQ:
PASS / FAIL

Final inventory:
...

Real-world actions:
...

Skipped:
...

Known limitations:
...
```

最重要：

**禁止把单测 / mock QQ / WebUI E2E 当成 Real QQ PASS。**