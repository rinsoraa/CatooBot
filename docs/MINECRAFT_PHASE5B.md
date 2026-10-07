# Minecraft Phase 5B — QQ Task Entry + Unified Task Control

> 目标：把 QQ 接进**已经存在**的 TaskRuntime，建立统一的任务入口、确认、暂停、继续、停止、
> 重规划与状态反馈链路。**不重新实现任何执行引擎**，不新增 Minecraft 工具（仍是 19 个），
> 不新增 TaskRuntime 状态，不新增 ActionRuntime action。

一句话架构（§一）：

```
QQ 消息 / Minecraft 聊天 / WebUI
        ↓  三个入口，同一个 TaskRuntime
TaskRuntime（Plan / Authorization / Checkpoint / Reconcile / Replan / Recovery）
        ↓
Policy → Confirmation → Agent Bridge → MinecraftService → ActionRuntime → Mineflayer
```

**QQ 只是入口，不是执行器**：`app/tasks/qq_entry.py` 只做"身份规范化 → 意图判断 →
调用现有 Planner/TaskRuntime → 展示现有 Plan summary / 翻译现有 Task 事件"。
它**不碰** Minecraft 工具、不自己实现确认令牌、不维护第二套状态机
（QQ 层没有 `qq_running` / `qq_waiting` / `qq_done` 这类平行状态）。

## 一、入口与消息归属（§二/§二十二/§二十三）

* 入口在 **Bot 装配时注册到事件总线**，并且**排在人格插件之前**（`app/core/bot.py`）：

  ```python
  self.event_bus.on("message", self.core_router.on_message)
  self.event_bus.on("message", self._dispatch_task_message)  # ← 任务入口
  ```

* 事件总线新增一个**最小**的认领语义：处理器返回真值 = 认领该事件，后续处理器不再跑
  （返回 `None` 的旧处理器行为完全不变）。任务入口**只在确认是任务请求/控制命令时才认领**，
  否则原样交给人格回复 —— 普通聊天照样是普通聊天，"我想吃蛋糕"绝不变成任务。
* 群里只有 **@她** 或**回复她的消息**才进任务入口（与人格插件同一套判据）。

## 二、身份（§四/§二十一）

```python
QQIdentity(platform="qq", user_id="2731431246", session_id="private:2731431246")
QQIdentity(platform="qq", user_id="2731431246", session_id="group:987654", conversation_id="987654")
```

* `user_id` 是**稳定的 QQ 号**；昵称/群名片只用于措辞，绝不作为身份（`display_name` 不进任何 ID）。
* `session_id` **复用项目既有会话身份**（`private:<uid>` / `group:<gid>`，与 `app/ai/context.py`
  的约定一致），不新造"qq_user_session / qq_task_session / qq_confirmation_session"三套对象。
* 任务记录里 `source="qq"`（§二十九/§三十）——WebUI 的 Task 面板直接显示 Source，
  checkpoint 的 detail 也带 `source=qq user_id=… session_id=… plan_version=…`（不记任何凭据）。

## 三、意图与控制（§三/§九/§十/§二十七/§二十八）

| QQ 说 | 走哪里 |
| --- | --- |
| 「帮我找附近的一块橡木」「去附近找一棵橡木挖回来」「帮我把那边的木头挖掉」 | SAFE 观察 → 冻结计划 → 回摘要请他确认 |
| 「确认」「继续任务」「暂停这个任务」「停止」 | `confirm_and_start` / `resume` / `pause` / `cancel` |
| 「任务怎么样了」「现在到哪了」「任务还在做吗」 | 状态摘要（objective / 状态 / 第 N/M 步 / 当前一步） |
| 「你在干嘛」「我今天想砍树」「木头真的好难找」「我想吃蛋糕」 | **不建任务**，照常走人格回复 |

* 确认仍然是**真实 USER 回合**（`TurnOrigin.USER`），继续复用现有 `ConfirmationStore`
  （`task_id + user_id + session_id + plan_hash + plan_version + arguments`）；QQ 不做第二套令牌。
* 暂停/继续/停止全部调用 TaskRuntime 现成方法，QQ 层**不**自己 `minecraft_stop`、
  也**不**强行 resume（`replan_required` / 授权过期 / plan_hash 不符都由 TaskRuntime 判）。
* 一个 user/session 最多一个任务（§十七）：同会话由 TaskRuntime 的 `task.busy` 挡；
  跨会话由入口的 `active_for_user` 挡住，并回「你还有一个任务正在处理中」。

## 四、归属隔离（§5.2/§十六/§三十一 D·L）

* 只有**任务发起人**能确认/暂停/继续/停止；别人（哪怕同群成员）一律：
  「你不是这个任务的发起人，这个任务由 @Rxxx 创建。」并且**拒绝不改任务状态**。
* 入口在调用任何 TaskRuntime 方法**之前**就完成归属校验（拒绝路径零副作用）。
* 跨会话隔离：任务只在自己的会话里可见（`runtime.current(session)`），
  别的群/私聊问"任务怎么样了"不会被别的会话的任务影响。

## 五、事件 → QQ（§十三/§十四/§十五）

* 任务事件走 **TaskRuntime 现有事件流**（Bot 把 `publish` 钩子接到入口），QQ 侧订阅它，
  **不**自己轮询 ActionRuntime。为此补上了 `task.created` 与 `task.confirmation_required`
  的 `publish`（原来只写 checkpoint）——这两条正是"计划给你看 / 请你确认"的载体。
* 消息都很短，并且**不带** `action_id` / `plan_hash` / checkpoint id / raw world snapshot /
  工具调用 JSON：`🌱 我开始处理了。` / `🚶 正在过去……` / `⛏️ 到地方了，开始挖。` /
  `📦 正在捡东西……` / `🔎 最后检查一下背包。` / `完成啦，已经拿到了 1 个 oak_log。` /
  `先停这里了。` / `刚才目标发生了变化，原计划已经作废。` /
  `刚才的操作授权已经过期了。计划没有改变，但需要重新确认。` / `我重启过，之前那一步的动作已经作废了，不会重做。`
* **幂等**：TaskRuntime 的事件载荷带单调递增的 `event_seq`（跟着 checkpoint 落盘），
  入口按 `(task_id, event_seq)` 去重（更旧的序号直接丢），同一件事绝不发第二遍 ——
  不依赖"这一轮应该只有一个事件"。
* 用户自己刚说「确认」时，入口的回复已经代表那些事件（创建/等确认），不会重复推送。

## 六、错误处理（§二十六）

错误码映射成人话，QQ 层**绝不**泄漏 traceback：`TARGET_LOST`→「目标不见了，得重新找。」、
`WORLD_CHANGED`→「世界里的东西变了，原计划作废。」、`OFFLINE`→「罐头现在不在线，暂时做不了。」、
`RUNTIME_RESTART`→「我重启过，之前那一步不再有效。」、`BUSY`→「我手上还有别的事，先停一下。」等。

## 七、启动恢复（§三十一 K）

Bot 装配任务运行时之后会调用 `recover_persisted_tasks()`（Phase 5A.1 的能力）：
进程重启过的任务一律把"派出去过的那一步"判为失效、**旧确认作废**、需要重新规划/确认，
并通过 QQ 告诉发起人「我重启过……」。绝不重复世界动作。

## 八、自动化验证

| 层 | 内容 |
| --- | --- |
| `tests/test_qq_task_entry.py`（23） | §三十一 A–L：建任务（source=qq、计划阶段零世界动作）、普通聊天不建任务、确认后开始、**非发起人四种控制全被拒且状态不变**、SYSTEM/WebUI/BACKGROUND/INITIATIVE/TASK 不能确认、授权到期（旧确认不可复用）、重规划（v2 + 未确认零世界动作 + 新确认）、暂停/继续/停止（经 `minecraft_stop`）、状态查询、重启恢复、跨会话隔离、事件去重、事件总线认领语义 |
| `tests/test_task_turn.py` / `test_minecraft_chat_bridge.py` | 5A 的游戏内入口保持兼容（措辞集合默认值就是原来的话） |
| WebUI vitest | Task 面板显示 `source`（QQ 创建的任务在面板里一眼看得出来） |

这里用的是**真 TaskRuntime + 真 OneBot 解析出来的消息事件**，只有"发 QQ 消息"那一步是替身。

## 九、真实 QQ 门禁（§三十二/§三十三）

**必须真的跑 NapCat + QQ**，判定脚本是 `scripts/task_qq_smoke.py`（它不代替你发消息，
只从跑着的 CatooBot 的 SQLite checkpoint 与日志取证）：

```powershell
# 0) 先让 CatooBot + NapCat 跑起来（NapCat 连到 ws://127.0.0.1:8080/onebot/v11/ws）
# 1) 你在 QQ 里发「帮我找附近的一块橡木」
.venv\Scripts\python.exe scripts\task_qq_smoke.py --phase entry --user <你的QQ号>
# 2) 你发「确认」
.venv\Scripts\python.exe scripts\task_qq_smoke.py --phase confirm --user <你的QQ号>
# 3) 你依次发「暂停」「继续」「停止」
.venv\Scripts\python.exe scripts\task_qq_smoke.py --phase control --user <你的QQ号>
# 4) 换另一个 QQ 号发「确认/暂停/继续/停止」（应该全被拒）
.venv\Scripts\python.exe scripts\task_qq_smoke.py --phase ownership --user <你的QQ号>
# 5) 重规划：新建+确认后，用 op 命令把目标改成空气
.venv\Scripts\python.exe scripts\task_qq_smoke.py --phase replan --user <你的QQ号>
# 6) 授权到期：新建+确认后等 TTL 过去，再发「继续」
.venv\Scripts\python.exe scripts\task_qq_smoke.py --phase expiry --user <你的QQ号>
# 7) 汇总
.venv\Scripts\python.exe scripts\task_qq_smoke.py --phase report --save .qq_smoke.json
```

`--phase` 还支持 `restart`（重启一次 CatooBot 后判定）与 `notify`（检查通知里不泄漏内部信息）。
**单元测试 / mock QQ / WebUI E2E 都不能算 Real QQ PASS**（§三十七）。

## 十、本阶段不做（§三十四）

新 Minecraft 工具、新 TaskRuntime 状态、新 ActionRuntime action、建造、自动采矿、自动资源链、
自动导航搜索、QQ 自己执行工具、QQ 自己维护确认系统、长期 Minecraft memory、多 Agent 协作、
跨进程恢复 Mineflayer action、任务调度器、定时任务 —— 全部留给后续阶段。

默认配置**不变**：`allow_medium` 仍是 false（QQ 不是 MEDIUM 白名单，§十九）；
QQ 创建的任务里出现 `minecraft_dig`(MEDIUM) 时照样要过 Policy + 确认 + 任务授权。
