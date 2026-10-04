# CatooBot v2.1 Phase 13.1 · Lane Ordering & Graceful Shutdown Integrity

## 0. 基线

当前：

```text
HEAD: dea9f9b7e44c68978863c516e7908e0aa3a7cc31
Phase: 13
Tests: 1349 passed
CI: success
```

本轮不是 Phase 14。

只修：

```text
1. lane overflow 下 Sandbox fact 的顺序完整性
2. graceful shutdown 的实际行为与 contract 对齐
3. reverse WebSocket disconnect/reconnect wiring 的真实生命周期验证
```

不要扩展 OneBot 功能。

---

# 1. 核心问题 A：Overflow 破坏 FIFO

当前：

```python
id="g7n0yq"
_spawn(_ingest_only(dropped.event))
```

会使被丢弃的 conversational turn 以独立 task 并发进入 Sandbox。

例如：

```text
M1 = 当前正在处理
M2 = queue 中最旧未开始
M3 = 新到
```

M2 被 drop 后：

```text
M2 → _ingest_only task
M3 → lane
```

可能产生：

```text
M1
M3
M2
```

的 Sandbox processing 顺序。

这违反 Phase 13：

```text
同一 character_id + social_space_id
→ FIFO
```

---

# 2. 正确语义

“丢掉回复”与“丢掉世界事实”必须继续分离。

正确：

```text
M2
↓
不执行 ConversationRuntime
↓
但仍然作为 lane 中的 ordered ingest-only item
↓
进入 Sandbox
↓
完成
↓
M3 才能继续
```

也就是说：

> Overflow item 可以取消 conversational response，但不能脱离 lane。

---

# 3. 推荐实现

不要：

```python
id="w2f4j8"
_spawn(_ingest_only(...))
```

改为让 `_LaneItem` 表达：

```python
id="m1v8z6"
respond: bool = True
```

或者：

```text
kind = "conversation" / "ingest_only"
```

例如：

```text
M2:
LaneItem(
    event=M2,
    respond=False,
)
```

这样：

```text
Lane worker
    ↓
ingest M2
    ↓
if respond:
    ConversationRuntime
```

最终：

```text
M1
↓
M2 ingest-only
↓
M3
```

仍然严格 FIFO。

---

# 4. Overflow 算法

当：

```text
len(_pending) >= max_pending
```

寻找：

```text
oldest unstarted item
```

不要直接把它放到 lane 外执行。

应当：

```text
remove oldest conversational item
↓
transform into ingest-only item
↓
reinsert at the same logical position
```

因为它是被“降级为只摄入世界事实”，不是被完全删除。

---

# 5. 最简单且安全的实现

推荐：

```text
LaneItem:
    event
    respond
    started
```

overflow：

```text
oldest unstarted item:
    respond = False
```

然后：

```text
_do_item(item):
    submit_external(event)
    wakeup()

    if not item.respond:
        return

    determine response
    ConversationRuntime
```

这样无需：

```text
new queue
new scheduler
new manager
```

---

# 6. Overflow Test 必须加强

修改现有：

```text
test_a_full_lane_drops_the_oldest_turn_but_keeps_its_fact
```

不要只验证：

```text
facts count == 3
```

必须记录 Sandbox ingestion 顺序。

例如：

```text
M1
M2
M3
```

最后断言：

```python
id="1s2k9v"
assert observed_fact_ids == ["M1", "M2", "M3"]
```

或者使用：

```text
ingest_seq
```

验证：

```text
M1 < M2 < M3
```

---

# 7. Overflow Response Test

同样验证：

```text
M1:
response may happen

M2:
response must NOT happen

M3:
response may happen
```

要求：

```text
M2 has:
    no LLM call
    no outbound response
```

但：

```text
M2 has:
    exactly one ExternalWorldEvent
```

---

# 8. Multiple overflow

不要只测试一个。

场景：

```text
M1 processing
M2
M3
M4
M5
```

queue full 多次。

要求：

```text
所有 message
```

都必须：

```text Sandbox ingestion exactly once
```

并且：

```text Sandbox order = M1 M2 M3 M4 M5
```

只是部分消息：

```text response = skipped
```

---

# 9. Cross-lane 仍可并发

修复之后不得退回全局串行。

继续验证：

```text
Group A:
A1 A2 A3

Group B:
B1 B2
```

要求：

```text A1 < A2 < A3
B1 < B2
```

但：

```text A 与 B
```

可以交错。

---

# 10. Core rule

最终必须明确：

```text
FIFO applies to Sandbox fact ingestion
```

而不是：

```text FIFO only applies to response generation
```

这是 Phase 13 本轮最重要的修复。

---

# 11. 核心问题 B：Graceful Shutdown 与实现不一致

当前 `stop()` 会：

```text
state = stopped
↓
lane.stop() → worker.cancel()
↓
outbound worker.cancel()
↓
in-flight tasks.cancel()
↓
transport.stop()
```

这与“让已经开始的工作完成后再关闭”的文档语义不一致。

---

# 12. 正确 shutdown contract

目标：

```text
stop accepting new inbound
↓
finish already-started Sandbox-critical work
↓
finish already-committed outbound delivery
↓
discard not-started conversational work
↓
close transport
↓
no orphan tasks
```

---

# 13. 不要求无限等待

必须：

```text graceful shutdown timeout
```

建议默认：

```text shutdown_timeout = 5s
```

如果已有配置，则复用。

---

# 14. Shutdown algorithm

推荐：

```text id="c4t8q1"
state = stopped
↓
reject new inbound
↓
drain(timeout=shutdown_timeout)
↓
if idle:
    stop workers
else:
    cancel remaining tasks
↓
transport.stop()
```

---

# 15. Important distinction

已开始：

```text ConversationRuntime / LLM
```

应该优先让它完成。

未开始：

```text queued response
```

可以丢弃。

但：

```text Sandbox fact already accepted
```

不能因为 response queue 被丢弃而消失。

---

# 16. Shutdown test

新增：

```text test_graceful_stop_allows_started_turn_to_finish
```

构造：

```text M1 enters LLM
↓
LLM waits on Event
↓
call gateway.stop()
```

允许：

```text M1
```

完成。

最终：

```text no orphan tasks
transport stopped
gateway.state == stopped
```

---

# 17. Shutdown timeout test

构造：

```text LLM hangs
```

调用：

```text stop()
```

要求：

```text shutdown_timeout exceeded
→ cancel remaining task
→ transport closes
→ no orphan task
```

不能：

```text stop hangs forever
```

---

# 18. Outbound shutdown

如果 response 已经：

```text committed
```

并已经：

```text outbound queue
```

则 shutdown 应优先允许它发送。

如果：

```text outbound item
```

尚未开始且 timeout 到达：

```text may be dropped
```

---

# 19. 不新增持久化 queue

不要：

```text database
redis
outbox
message table
```

本轮仍然：

```text runtime memory only
```

---

# 20. Core Sandbox 不修改

继续：

```text no transport imports
```

不要向：

```text app/sandbox/*
```

加入：

```text websockets
aiohttp
httpx
OneBot
```

---

# 21. 核心问题 C：Reverse WebSocket 生命周期

当前架构是：

```text
CatooBot
= WebSocket server

NapCat
= WebSocket client
```

OneBot reverse WebSocket 规范本身规定 OneBot implementation 在连接断开后负责重连。CatooBot server 不需要伪装成 client 去持续主动拨号。

因此本轮只验证：

```text client disconnect
↓
transport/server reports disconnect
↓
gateway state reflects disconnected
↓
no Sandbox mutation
↓
client reconnects
↓
gateway resumes accepted events
```

不要重新设计 transport direction。

---

# 22. Disconnect callback

审计现有：

```text app/adapters/onebot_v11
```

确认：

```text socket disconnected
```

是否能够通知：

```text ServerTransport
→ OneBotGateway
```

如果当前已有 callback：

```text reuse
```

不要复制。

如果没有：

允许在现有 adapter 增加一个：

```text connection lifecycle callback
```

仅用于：

```text connected
disconnected
```

---

# 23. Gateway State

正确生命周期：

```text id="x7g5y2"
CONNECTED
    ↓
DISCONNECTED
    ↓
CONNECTED
```

而不是：

```text disconnect
↓
CONNECTED forever
```

---

# 24. Current fake test weakness

当前测试直接：

```python
id="q5j3k8"
transport.disconnect()
gateway.handle_disconnect()
```

这不是实际 lifecycle propagation。

应该新增：

```text FakeOneBotTransport
```

可以触发：

```text on_disconnect
```

然后测试：

```text transport.disconnect()
↓
gateway.state == disconnected
```

之后模拟 client reconnect：

```text transport.reconnect_client()
↓
gateway.state == connected
```

---

# 25. Reverse WebSocket reconnect semantics

不要要求：

```text ServerTransport
```

真的主动建立一条新的 outbound WebSocket connection。

在 reverse-WebSocket 模式：

```text NapCat reconnects to us
```

Server 只需要重新接受连接。

---

# 26. Bounded reconnect/backoff

现有：

```text 1
2
4
8
16
```

可以保留作为：

```text lifecycle recovery debounce / state recovery
```

但必须明确：

```text not client dialing
```

不要在代码/报告中暗示：

```text CatooBot 正在向 NapCat 建立 reverse connection
```

---

# 27. Disconnect 不产生 Sandbox fact

保持：

```text network disconnect
≠
ExternalWorldEvent
```

测试：

```text runtime.events
```

不得出现：

```text SOCIAL_INTERACTION
EXTERNAL_EVENT_RECEIVED
```

仅允许：

```text transport trace
```

---

# 28. Reconnect 后 event identity

同一个 OneBot：

```text message_id
```

在 reconnect 前后重发：

如果：

```text same self_id + same message_id
```

且仍在 dedupe TTL：

```text duplicate
```

不能二次 processing。

---

# 29. Reconnect test

完整：

```text M1
↓
disconnect
↓
reconnect
↓
M1 replay
↓
dedupe
```

要求：

```text sandbox fact = 1
LLM calls = 1
outbound = 1
```

---

# 30. No new reconnect architecture

禁止：

```text ReconnectManager
TransportSupervisor
NetworkAgent
ConnectionAgent
```

只允许：

```text existing gateway
existing transport
existing onebot server
```

最小修改。

---

# 31. Do not modify response semantics

保持：

```text reply
acknowledge
defer
silent
```

不新增 mode。

---

# 32. Do not modify influence policy

特别审计：

当前 `_mode_ceiling()` 不得偷偷变成第二套：

```text private always reply
group always silent
```

如果现有 Phase 3 / group cognition 已经存在 response eligibility：

必须：

```text reuse existing authoritative policy
```

不要在 OneBot gateway 里复制一份 social-response policy。

如果当前 gateway 的 `_mode_ceiling()` 确实已经是必要 adapter-side ceiling：

则至少把它定义为：

```text upper bound only
```

最终允许与否仍需遵守：

```text existing sandbox influence policy
```

新增测试：

```text influence = reject
→ silent

existing non-reply state
→ silent

explicit mention
→ allowed only when sandbox policy allows
```

不要因为：

```text group mention
```

直接绕过：

```text external influence state
```

---

# 33. Regression requirement

必须保留：

```text 1349 tests
```

全部通过。

新增测试至少：

```text 1. overflow preserves Sandbox FIFO
2. overflow response skipped
3. multiple overflow
4. graceful shutdown
5. shutdown timeout
6. disconnect callback
7. reconnect callback
8. reconnect duplicate event
9. disconnect no Sandbox mutation
10. response policy does not bypass influence gate
```

---

# 34. Quality Gate

必须：

```text ruff
ruff format
mypy
pytest
CI success
```

并且：

```text HEAD == origin/main
working tree clean
```

禁止：

```text force push
history rewrite
```

---

# 35. Final Report

```text
# CatooBot v2.1 Phase 13.1 Report · Lane Ordering & Runtime Lifecycle Integrity

Phase 13.1 commit:
HEAD:
origin/main:
CI:

previous tests:
new tests:
total tests:

## FIFO

same-lane Sandbox order:
overflow fact order:
overflow response behavior:
multiple overflow:

## Shutdown

started work:
queued unstarted work:
outbound committed work:
shutdown timeout:
orphan tasks:

## Connection Lifecycle

disconnect callback:
gateway state:
reconnect:
replayed message:
dedupe:

## Sandbox Safety

transport disconnect mutates Sandbox: No
transport imports in Sandbox: No
OneBot shortcut to LLM: No

## Response Policy

existing influence policy reused:
mode ceiling behavior:
mention behavior:
non-mention group behavior:

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
Phase 12.1:
Phase 13:

## Architecture

new DB: No
new queue DB: No
new Manager: No
new Agent: No
new Planner: No
new Emotion: No
new Memory system: No
new Relationship system: No
new Experience system: No
Character Bible modified: No
Git history rewritten: No
Force push: No

## Final

Phase 13.1 complete: Yes / No
Phase 14 entered: No
```

---

### 为什么我现在坚持再做这一步

Phase 13 已经不是纯 Sandbox 代码了，它第一次开始面对真实的**异步外部事件流**。此时最危险的不是“功能缺失”，而是：

```text
消息顺序错乱
↓
Sandbox 世界事实错乱
```

以及：

```text
shutdown / reconnect
↓
部分事件重复或丢失
```

这类问题通常不会被普通功能测试发现，但一旦真的让 NapCat 长时间运行，就会变成非常难查的“偶发人格异常”。

尤其当前 overflow 代码实际上已经证明这个风险存在：**报告声称“事实不丢”，但实现为了保事实而绕出了 lane。** ([github.com](https://github.com/rinsoraa/CatooBot/blob/dea9f9b7e44c68978863c516e7908e0aa3a7cc31/app/integrations/onebot/gateway.py))

完成 13.1 后，我会把它作为 **Phase 13 最终基线**；下一阶段再真正进入长期在线运行/行为观察，而不是继续堆 OneBot 基础设施。