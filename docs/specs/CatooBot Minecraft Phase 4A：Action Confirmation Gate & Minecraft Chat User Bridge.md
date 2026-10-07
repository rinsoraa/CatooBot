# CatooBot Minecraft Phase 4A
## Action Confirmation Gate & Minecraft Chat User Bridge

### 阶段定位

Phase 3E.1 已完成。

当前 Minecraft Agent 已经具备：

```text
minecraft_world
minecraft_chat
minecraft_look_at
minecraft_move_to
minecraft_follow_player
minecraft_stop
```

以及：

```text
TurnOrigin
MinecraftActionPolicy
ActionRuntime
WorldPerception
Pathfinder
```

本阶段开始进入 Minecraft Interaction。

但是：

> **本阶段暂时不增加 dig / place / attack / craft / inventory 等世界修改 Action。**

本阶段只做两个基础设施：

1. 建立 MEDIUM / HIGH / DESTRUCTIVE 的 Confirmation Gate；
2. 将 Minecraft Chat 正式接入 CatooBot 的 USER Turn / Tool Loop。

完成后才能进入 Phase 4B 的第一个世界修改动作。

---

# 一、阶段目标

完成：

```text
Minecraft Chat
      ↓
TurnOrigin.USER
      ↓
CatooBot LLM
      ↓
Minecraft Tools
```

以及：

```text
LLM Action Request
      ↓
Risk Policy
      ↓
SAFE / LOW / MEDIUM / HIGH / DESTRUCTIVE
      ↓
Confirmation Gate
      ↓
Action Runtime
```

本阶段结束后：

- Minecraft 玩家可以直接通过游戏内聊天和罐头正常对话；
- 这类聊天可以触发现有 Minecraft Tools；
- Minecraft 聊天来的指令被视为 USER Turn；
- SAFE / LOW 行为继续按 Phase 3E 规则工作；
- MEDIUM / HIGH / DESTRUCTIVE 已有统一确认基础设施，但本阶段没有实际动作；
- 任何不存在的高风险 Tool 仍然无法调用。

---

# 二、严格禁止

本阶段禁止新增：

- minecraft_dig
- minecraft_place
- minecraft_attack
- minecraft_craft
- minecraft_eat
- minecraft_inventory
- minecraft_container
- minecraft_use_item
- redstone 操作
- 自动采集
- 自动建造
- 自动战斗
- 自主 Minecraft Agent Loop
- 长期 Minecraft Memory

本阶段只增加：

```text
Confirmation Infrastructure
+
Minecraft Chat → CharacterRuntime USER Turn
```

---

# 三、风险等级正式定义

继续使用：

```text
SAFE
LOW
MEDIUM
HIGH
DESTRUCTIVE
```

定义：

### SAFE

不会移动，不会修改世界。

当前：

```text
minecraft_world
minecraft_chat
minecraft_look_at
minecraft_stop
```

---

### LOW

改变罐头自身位置，但不主动修改世界。

当前：

```text
minecraft_move_to
minecraft_follow_player
```

继续使用：

```text
TurnOrigin.USER
```

作为唯一放行条件。

不需要二次确认。

---

### MEDIUM

未来用于：

```text
dig
place
use_block
container
item interaction
```

可能修改 Minecraft 世界或物品状态。

必须：

```text
USER Turn
+
Confirmation
```

---

### HIGH

未来用于：

```text
attack
drop valuable item
large destructive action
危险环境操作
```

必须：

```text
USER Turn
+
Confirmation
```

并继续受：

```text
allow_high
```

限制。

---

### DESTRUCTIVE

未来用于：

```text
大范围破坏
高价值物品消耗
大规模世界修改
```

默认：

```text
allow_destructive = false
```

即使用户提出，也不能直接执行。

本阶段不实现对应 Action。

---

# 四、不要修改现有 SAFE / LOW 行为

Phase 3E 已经正确的：

```text
SAFE
LOW + USER
LOW + 非 USER
```

保持完全不变。

不要把 Confirmation Gate 写成：

```text
所有 Minecraft Tool 都必须确认
```

错误。

应当：

```text
SAFE
    ↓
直接允许

LOW
    ↓
USER Turn
    ↓
允许

MEDIUM/HIGH
    ↓
USER Turn
    ↓
Confirmation Required

DESTRUCTIVE
    ↓
默认拒绝
```

---

# 五、Confirmation 对象

新增一个独立的数据模型：

```python
MinecraftActionConfirmation
```

至少包含：

```text
confirmation_id
session_id
user_id
tool
risk
arguments_hash
arguments
created_at
expires_at
status
```

状态：

```text
PENDING
CONFIRMED
EXPIRED
CANCELLED
CONSUMED
```

---

# 六、为什么必须绑定 user_id + session_id

确认不能跨用户。

例如：

```text
空凛：
确认挖掉这个方块

```

不能让：

```text
另一个 QQ 用户
```

拿同一个 confirmation_id 执行动作。

必须同时匹配：

```text
user_id
session_id
```

否则：

```text
minecraft_confirmation_hijack
```

---

# 七、arguments_hash

必须保存：

```text
arguments_hash
```

例如：

第一次：

```json
{
  "x": 120,
  "y": 64,
  "z": -230
}
```

产生：

```text
hash=A
```

确认时如果参数变成：

```json
{
  "x": 300,
  "y": 64,
  "z": -500
}
```

即使 confirmation_id 正确：

```text
hash != A
```

也必须拒绝。

目的：

> 用户确认的是哪个具体 Action，必须不可变。

---

# 八、确认有效期

默认：

```text
confirmation_ttl = 60s
```

建议配置：

```yaml
minecraft:
  agent:
    confirmation:
      ttl_seconds: 60
```

范围：

```text
10～300 秒
```

超过：

```text
EXPIRED
```

不能再使用。

---

# 九、Confirmation API

不要让 LLM 自己伪造 confirmation。

新增内部：

```python
create_confirmation(...)
consume_confirmation(...)
cancel_confirmation(...)
```

如果需要 HTTP Debug：

```text
POST /minecraft/agent/confirm
```

但是不要允许用户仅凭：

```text
confirmation_id
```

绕过：

```text
user_id
session_id
arguments_hash
risk
```

---

# 十、LLM Tool 层的 Confirmation 行为

未来 MEDIUM Tool 被调用时：

错误：

```json
{
  "ok": false,
  "error": {
    "code": "minecraft.confirmation_required",
    "message": "这个 Minecraft 动作需要用户确认"
  }
}
```

同时创建：

```text
PENDING confirmation
```

返回：

```json
{
  "ok": false,
  "error": {
    "code": "minecraft.confirmation_required"
  },
  "confirmation": {
    "confirmation_id": "...",
    "tool": "minecraft_dig",
    "risk": "MEDIUM",
    "expires_at": "...",
    "summary": "破坏坐标 (120,64,-230) 的方块"
  }
}
```

本阶段没有 `minecraft_dig`，因此只实现 Confirmation Infrastructure。

---

# 十一、为什么不能让 LLM 自己确认

禁止：

```text
LLM：
“我确认了。”
```

再：

```text
Tool：
执行
```

Confirmation 必须来自：

> **新的 USER Turn**

即：

```text
用户：
确认

↓
TurnOrigin.USER

↓
确认流程

↓
consume_confirmation
```

不能靠：

```text
assistant message
system prompt
previous tool result
```

完成确认。

---

# 十二、Confirmation 必须绑定“下一轮用户意图”

建议：

```text
PENDING confirmation
        ↓
下一 USER Turn
        ↓
用户明确确认
        ↓
consume
```

用户说：

```text
确认
可以
好的
执行吧
就这样
```

是否属于确认可以由 CatooBot 上层判断。

但最终 Action Gate 必须只接受：

```text
TurnOrigin.USER
```

不能：

```text
INITIATIVE
BACKGROUND
SYSTEM
```

消费 confirmation。

---

# 十三、不要让所有“好的”自动确认

必须结合：

```text
当前 pending confirmation
```

例如：

```text
pending:
挖掉铁矿

用户：
好的
```

可以确认。

但是：

```text
没有 pending confirmation

用户：
好的
```

不能产生任何 Action。

也不能创建 confirmation。

---

# 十四、Confirmation 消费必须一次性

成功确认后：

```text
PENDING
↓
CONSUMED
```

再次使用：

```text
confirmation_id
```

必须：

```text
minecraft.confirmation_invalid
```

不能重复执行。

---

# 十五、Confirmation 与 ActionRuntime 的关系

Confirmation：

> 只负责“有没有用户授权”。

ActionRuntime：

> 负责“怎么执行”。

链路必须是：

```text
LLM
↓
Tool
↓
Policy
↓
Confirmation Gate
↓
MinecraftService
↓
ActionRuntime
↓
Minecraft
```

不要：

```text
Confirmation
↓
直接操作 Mineflayer
```

---

# 十六、Minecraft Chat → USER Turn

现在 Minecraft Chat 不能进入 LLM Tool Loop。

本阶段必须正式接入。

当前：

```text
Minecraft Chat
↓
sandbox.submit_external
↓
sandbox conversation
```

需要增加：

```text
Minecraft Chat
↓
Minecraft Chat Adapter
↓
ConversationTurnRuntime / CharacterRuntime
↓
TurnOrigin.USER
↓
正常 Tool Loop
```

但要保留现有 sandbox 外部事件链。

即：

```text
Minecraft Chat
├── Sandbox external event
│
└── Character Conversation
```

二者不能互相替代。

---

# 十七、Minecraft Chat Event

runtime 已有：

```text
minecraft.chat
```

Service 已经能接收到。

本阶段新增：

```text
MinecraftChatAdapter
```

负责转换：

```text
minecraft.chat
```

成为 CatooBot Conversation 输入。

至少包含：

```text
session_id
mc_username
message
dimension
position
timestamp
```

---

# 十八、Minecraft Chat Session

建议：

```text
minecraft:{server_id}:{mc_username}
```

或者项目已有 session naming 规范则复用。

不要把 Minecraft Chat 直接塞进 QQ：

```text
private:10001
```

Minecraft 世界应该有自己的 Conversation Session。

---

# 十九、谁是 USER

Minecraft 玩家说话：

```text
空凛：罐头过来
```

属于：

```text
TurnOrigin.USER
```

因此：

```text
minecraft_move_to
```

允许。

这是本阶段最重要的行为变化。

---

# 二十、Minecraft Chat 不应该默认让“所有玩家”成为罐头主人

必须区分：

```text
Minecraft username
CatooBot user identity
```

当前可以先建立最小 mapping：

```text
Minecraft username → CatooBot user identity
```

没有 mapping 时：

```text
普通玩家
```

可以聊天。

但是涉及：

```text
LOW Action
MEDIUM Action
HIGH Action
```

建议暂时要求：

```text
trusted Minecraft user
```

才可以执行。

---

# 二十一、第一版可信用户策略

配置：

```yaml
minecraft:
  agent:
    trusted_players: []
```

例如：

```yaml
trusted_players:
  - RinsoraNeko
```

规则：

### SAFE

所有玩家可以：

```text
minecraft_world
minecraft_chat
minecraft_look_at
minecraft_stop
```

### LOW

只有：

```text
TurnOrigin.USER
+
trusted Minecraft player
```

允许。

如果玩家不可信：

```text
minecraft.user_not_trusted
```

---

# 二十二、为什么 Minecraft Chat 需要可信玩家

避免：

```text
陌生玩家：
罐头，过来

→ 罐头真的跑过来
```

第一阶段不应该出现。

QQ 用户是你现有明确身份体系。

Minecraft 玩家需要额外建立信任关系。

---

# 二十三、Minecraft Player Mapping

本阶段只设计最小结构。

例如：

```text
MinecraftIdentity
{
    mc_username
    user_id
    trusted
}
```

不做复杂管理 UI。

先支持配置文件：

```yaml
trusted_players:
  - RinsoraNeko
```

后续再进入 WebUI 管理。

---

# 二十四、Minecraft Chat 的自然语言 E2E

必须新增：

### Test A

真实 Minecraft Chat：

```text
空凛：
罐头，你过来
```

进入：

```text
TurnOrigin.USER
```

模型：

```text
minecraft_move_to
```

Policy：

```text
allowed
```

Service：

```text
move_to
```

---

### Test B

Minecraft Chat：

```text
空凛：
罐头跟着我
```

模型：

```text
minecraft_follow_player
```

必须：

```text
RUNNING
```

---

### Test C

Minecraft Chat：

```text
空凛：
停
```

模型：

```text
minecraft_stop
```

必须：

```text
CANCELLED
```

---

# 二十五、非可信玩家测试

```text
Steve：
罐头过来
```

必须：

```text
minecraft.user_not_trusted
```

且：

```text
Service.action_calls == []
```

但是：

```text
Steve：
罐头你是谁？
```

仍然可以正常聊天。

---

# 二十六、QQ / WebUI 行为保持不变

QQ：

```text
用户说：
罐头，过来
```

继续：

```text
USER
→ LOW allowed
```

WebUI 对话：

```text
USER
→ LOW allowed
```

管理台：

```text
SYSTEM
→ LOW rejected
```

主动发言：

```text
INITIATIVE
→ LOW rejected
```

---

# 二十七、Confirmation Unit Tests

必须新增：

```text
test_confirmation_created
test_confirmation_requires_user_turn
test_confirmation_rejects_initiative
test_confirmation_rejects_background
test_confirmation_rejects_system
test_confirmation_matches_user
test_confirmation_matches_session
test_confirmation_matches_arguments_hash
test_confirmation_expires
test_confirmation_consumed_once
test_confirmation_cancelled
```

---

# 二十八、Minecraft Chat Tests

至少：

```text
test_minecraft_chat_becomes_user_turn
test_minecraft_chat_preserves_mc_username
test_minecraft_chat_trusted_player_allows_low
test_minecraft_chat_untrusted_player_rejects_low
test_minecraft_chat_safe_tools_work_for_untrusted
test_minecraft_chat_no_duplicate_agent_turn
```

---

# 二十九、不要双重处理 Minecraft Chat

特别注意：

```text
minecraft.chat
```

目前同时可能进入：

```text
sandbox
conversation
```

不要造成：

```text
一次 Minecraft 消息
↓
两个 CharacterRuntime Turn
```

必须明确：

```text
Sandbox external event
```

与：

```text
Chat Conversation turn
```

是两个不同消费者。

其中：

- Sandbox 用于世界认知/行为活动；
- Conversation 用于直接人格对话和 Tool；
- 两者不能互相触发无限循环。

---

# 三十、事件回流防循环

如果罐头自己在 Minecraft：

```text
罐头：
我来啦
```

这个消息：

```text
minecraft.chat
```

必须识别：

```text
sender == bot.username
```

不要再次进入 CharacterRuntime USER Turn。

否则：

```text
罐头说话
↓
自己收到自己的 chat
↓
LLM
↓
再次说话
↓
无限循环
```

这是本阶段硬门禁。

---

# 三十一、确认提示

未来真正的 MEDIUM Action 被请求时：

CatooBot 不应该自己说：

> “我已经执行了。”

必须类似：

> “这个操作会破坏 Minecraft 世界里的方块，需要你确认一下。”

然后等待用户。

本阶段虽然还没有实际 MEDIUM Action，但 Confirmation 数据模型必须支持这个流程。

---

# 三十二、WebUI Debug

Minecraft 页面增加：

## Pending Confirmation

只读：

```text
Tool
Risk
Target
User
Expires
Status
```

开发测试按钮：

```text
[ CREATE TEST CONFIRMATION ]
[ EXPIRE ]
[ CANCEL ]
```

不要允许管理员按钮直接绕过 user/session/hash 验证。

---

# 三十三、Action Policy 最终结构

建议最终：

```text
MinecraftActionPolicy
        │
        ├── registration
        ├── enabled
        ├── online
        ├── risk flag
        ├── turn origin
        ├── trusted user
        ├── busy
        └── confirmation
```

固定顺序：

```text
1. Tool registered?
2. Minecraft enabled?
3. Tool enabled?
4. Online?
5. Risk enabled?
6. USER Turn?
7. Trusted user?
8. Busy?
9. Confirmation?
10. Execute
```

但是：

- SAFE 不需要 USER；
- SAFE 不需要 trusted；
- LOW 需要 USER + trusted；
- MEDIUM/HIGH 需要 USER + trusted + confirmation；
- DESTRUCTIVE 默认拒绝。

---

# 三十四、错误码

新增：

```text
minecraft.confirmation_required
minecraft.confirmation_invalid
minecraft.confirmation_expired
minecraft.confirmation_mismatch
minecraft.user_not_trusted
```

全部结构化。

禁止 traceback。

---

# 三十五、日志

必须能够看到：

```text
[MC Policy]
tool=minecraft_dig
risk=MEDIUM
turn_origin=user
trusted=true
confirmation=required

[MC Confirmation]
created id=...

[MC Confirmation]
consumed id=...

[MC Action]
action_id=...
```

但绝不记录：

- password
- token
- API key

---

# 三十六、不要实现数据库永久记忆

Confirmation 不需要长期保存。

可以：

- memory
- bounded in-memory store
- SQLite 临时表

选择与项目现有架构一致的方案。

必须支持：

- TTL
- cleanup
- restart 后全部失效

不要把旧确认恢复成有效授权。

---

# 三十七、性能

Minecraft Chat 不应阻塞 WorldPerception。

Confirmation 不应进入 LLM Context 历史。

Minecraft Chat Tool Context 保持：

```text
≈ 150～250 字符
```

级别。

---

# 三十八、回归测试

必须通过：

```text
pytest
Node npm test
Node E2E
vitest
vue-tsc
Playwright
ruff
format
mypy
GitHub Actions 双 job
```

Phase 1～3E.1 全回归。

---

# 三十九、完成定义

Phase 4A COMPLETE：

```text
[ ] Turn Origin 继续可靠
[ ] SAFE 行为不受影响
[ ] LOW 行为不受影响
[ ] MEDIUM/HIGH/DESTRUCTIVE 有统一 Confirmation Infrastructure
[ ] Confirmation 绑定 user/session/action args
[ ] Confirmation TTL
[ ] Confirmation 一次性消费
[ ] 非 USER Turn 不能确认
[ ] Minecraft Chat 正式进入 CharacterRuntime
[ ] Minecraft Chat 使用 TurnOrigin.USER
[ ] trusted player 门生效
[ ] 不可信玩家不能执行 LOW
[ ] 不可信玩家仍可正常聊天
[ ] 罐头自己的 Minecraft Chat 不会进入自己的 LLM Turn
[ ] 不产生双重 Conversation Turn
[ ] 不产生事件循环
[ ] 不新增 dig/place/attack/craft
[ ] Phase 1～3E.1 全部回归
[ ] CI 双 job 全绿
```

完成后：

```text
PHASE 4A COMPLETE
READY FOR PHASE 4B
```

输出：

1. 修改文件清单
2. Risk / Confirmation 架构
3. Confirmation 数据模型
4. Minecraft Chat Adapter
5. TurnOrigin 集成
6. trusted player 设计
7. 防循环设计
8. 新增测试
9. 全量回归
10. 已知限制
11. Phase 4B readiness

---

# 四十、Phase 4B 才开始真正破坏世界

Phase 4B 第一个实际 Minecraft Interaction 建议只做：

```text
minecraft_dig
```

而且先只允许：

> **破坏指定单个目标方块。**

不要一上来：

```text
“帮我挖铁”
```

因为这会马上引入：

```text
寻找矿石
导航
工具选择
库存
耐久
掉落
连续动作
任务循环
```

复杂度一下跳上去。

Phase 4B 应该先验证：

```text
用户：
“把我面前这个方块挖掉。”

↓
World
↓
确定目标方块
↓
确认
↓
minecraft_dig
↓
ActionRuntime
↓
Mineflayer dig
↓
Block Broken
↓
WorldPerception 更新
↓
LLM 得到真实结果
```

这才是第一次真正形成：

> **感知 → 判断 → 用户确认 → 行动 → 世界改变 → 重新感知**

这个闭环。

到了这一步，你的 CatooBot 才真正开始跨过 **“会移动的 Minecraft Bot” → “能够操作 Minecraft 世界的具身 Agent”** 这条线。

另外，我确认 `650c0be` 里你这次对 `respond()` 签名的收紧确实是一次刻意的 breaking change：提交本身把它设为必填，而且父提交 `9e1e5ac` 正是上一阶段 3E。 这是我认可的做法——对这种安全上下文来说，**宁可新调用点因为缺 `turn_origin` 直接报错，也不能默默获得 LOW Action 权限。**

现在正式：

```text
Phase 3E.1 ✅
        ↓
Phase 4A 🚀
Confirmation + Minecraft Chat USER Bridge
        ↓
Phase 4B
单方块 Dig
        ↓
Phase 4C
Place
        ↓
Phase 4D
Inventory / Container
        ↓
Phase 4E
Craft / Smelt
        ↓
Phase 4F
Combat
        ↓
Phase 5
真正的 Minecraft 自主生活
```

我认为这条路线比直接把“挖矿、建造、战斗”全塞进去稳很多。 (｀・ω・´)b