# CatooBot Minecraft Phase 3E.1
## Intent Propagation Integrity

### 阶段目标

修复 Phase 3E 中发现的一个安全边界问题：

> LOW Minecraft Action 的“用户明确意图门”在真实 CharacterRuntime 路径中没有正确传播。

Phase 3E 的 Policy 本身正确，本阶段不修改 Minecraft Tool、Policy 风险等级、Action Runtime 或 Minecraft 底层动作。

只修复：

```text
CharacterRuntime
    ↓
ToolContext
    ↓
MinecraftActionPolicy
```

之间的“本轮是否由用户明确发起”语义传播。

---

# 一、当前问题

当前：

```python
_tool_context()
```

无条件：

```python
metadata[INTENT_KEY] = True
```

导致：

```text
用户消息
    → True

主动发言
    → True   ❌

后台回合
    → True   ❌

未来自主 Agent 回合
    → True   ❌
```

而 Policy 要求：

```text
LOW Action
    ↓
只有 USER_TURN 才允许
```

所以需要修正。

---

# 二、不要使用特殊字符串判断

禁止：

```python
if user_text == "（主动发起）":
```

禁止：

```python
if "[主动发言]" in user_text:
```

禁止通过 message 内容推断 turn origin。

意图来源必须是结构化数据。

---

# 三、增加明确的 Turn Origin

建议定义：

```python
class TurnOrigin(str, Enum):
    USER = "user"
    INITIATIVE = "initiative"
    BACKGROUND = "background"
    SYSTEM = "system"
```

如果项目已有类似枚举，直接复用，不重复创建。

---

# 四、CharacterRuntime.respond

为 `respond()` 增加明确的来源参数。

推荐：

```python
turn_origin: TurnOrigin = TurnOrigin.USER
```

或者项目现有风格对应的结构化字段。

原则：

### 正常 QQ / WebUI 用户消息

```text
turn_origin = USER
```

### compose_initiative

```text
turn_origin = INITIATIVE
```

### 后台/自主生成

```text
turn_origin = BACKGROUND
```

### 系统内部生成

```text
turn_origin = SYSTEM
```

---

# 五、不要只传 explicit_intent bool

最终 ToolContext 可以继续保留：

```text
minecraft_explicit_intent
```

但它必须由：

```python
turn_origin == USER
```

派生。

例如：

```python
metadata[INTENT_KEY] = turn_origin == TurnOrigin.USER
```

这样不会出现调用方随手传：

```python
explicit_intent=True
```

却实际上是后台任务的情况。

---

# 六、_tool_context

从：

```python
def _tool_context(...)
```

改成：

```python
def _tool_context(
    ...,
    turn_origin: TurnOrigin,
)
```

然后：

```python
metadata[INTENT_KEY] = turn_origin == TurnOrigin.USER
```

并可以同时提供：

```python
metadata["turn_origin"] = turn_origin.value
```

后者只用于调试，不需要进入 LLM。

---

# 七、_generate

继续向下传：

```text
respond
  ↓
_generate
  ↓
_tool_context
```

不要重新推断。

---

# 八、compose_initiative

明确：

```python
await self.respond(
    ...,
    turn_origin=TurnOrigin.INITIATIVE,
)
```

即使：

```text
user_text = "（主动发起）"
```

也不能使 LOW Action 放行。

---

# 九、所有 CharacterRuntime 调用点必须审计

搜索整个仓库：

```text
CharacterRuntime.respond(
```

以及：

```text
_generate(
_tool_context(
compose_initiative(
```

逐个检查来源。

要求：

### 用户输入

```text
USER
```

### 主动发言

```text
INITIATIVE
```

### 后台

```text
BACKGROUND
```

### 系统

```text
SYSTEM
```

不得依赖默认值掩盖来源不明的调用。

如果无法确定来源：

```text
BACKGROUND
```

宁可不允许 LOW Minecraft Action。

---

# 十、Policy 本身保持不变

不要改：

```python
if risk in EXPLICIT_INTENT_RISKS and not facts.explicit_intent:
```

因为这个 Policy 是正确的。

本阶段只修：

```text
事实来源
```

---

# 十一、Minecraft Tools 不修改

以下全部保持：

```text
minecraft_world
minecraft_chat
minecraft_look_at
minecraft_move_to
minecraft_follow_player
minecraft_stop
```

不要新增：

```text
dig
place
attack
craft
inventory
```

---

# 十二、必须增加真实 CharacterRuntime 测试

这是本阶段最重要的一项。

不要只测：

```text
MinecraftActionPolicy(False)
```

必须真实经过：

```text
CharacterRuntime
↓
_tool_context
↓
ToolRuntime
↓
MinecraftActionPolicy
```

---

# 十三、User Turn Test

构造一个真正的用户回合：

```text
CharacterRuntime.respond(
    user_text="罐头你过来",
    turn_origin=USER
)
```

模型调用：

```text
minecraft_move_to
```

必须：

```text
allowed
```

---

# 十四、Initiative Test

真正调用：

```text
CharacterRuntime.compose_initiative(...)
```

mock 模型强制输出：

```text
minecraft_move_to
```

必须得到：

```text
minecraft.action_not_allowed
```

并且：

```text
MinecraftService
action_calls == []
```

这条测试必须存在。

---

# 十五、Background Test

构造：

```text
turn_origin=BACKGROUND
```

强制模型：

```text
minecraft_follow_player
```

必须：

```text
rejected
```

---

# 十六、System Test

构造：

```text
turn_origin=SYSTEM
```

LOW Action：

```text
move_to
follow_player
```

必须拒绝。

SAFE Tool：

```text
minecraft_world
minecraft_stop
```

按照现有 Policy 规则继续可用。

---

# 十七、Regression

现有：

```text
tests/test_minecraft_agent_e2e.py
```

全部保持通过：

- 用户“过来” → move_to
- 用户“跟着我” → follow
- 用户“停” → stop
- autonomous LOW → reject
- SAFE autonomous → allowed
- loop guard
- event 不触发新 Agent Turn

---

# 十八、增加专项回归

至少增加：

```text
test_character_user_turn_allows_low_minecraft_action

test_character_initiative_turn_rejects_low_minecraft_action

test_character_background_turn_rejects_low_minecraft_action

test_character_system_turn_rejects_low_minecraft_action

test_initiative_cannot_execute_move_to

test_initiative_cannot_execute_follow_player
```

---

# 十九、检查管理台干跑

项目文档说：

> 管理台干跑在用户回合上设置意图标记。

必须实际检查：

```text
app/web/services/tools.py
```

确保 WebUI Tool Dry Run 如果是测试用户行为：

```text
turn_origin = USER
```

如果是纯管理员/后台测试：

```text
turn_origin = SYSTEM
```

不要让管理台随便传：

```text
minecraft_explicit_intent=True
```

---

# 二十、日志

调试日志可以增加：

```text
[MC Policy] turn_origin=user
[MC Policy] turn_origin=initiative
```

但不要发送到 LLM。

不要记录：

- password
- token
- API key

---

# 二十一、不要改变 Tool description

现有：

> LOW Action 必须用户明确要求

这个设计正确。

不要因为修代码而把责任重新塞给 Tool description。

---

# 二十二、完成定义

必须满足：

```text
[ ] Turn Origin 结构化存在
[ ] USER → explicit_intent=True
[ ] INITIATIVE → explicit_intent=False
[ ] BACKGROUND → explicit_intent=False
[ ] SYSTEM → explicit_intent=False
[ ] 不再无条件 metadata[INTENT_KEY] = True
[ ] compose_initiative 无法 move_to
[ ] compose_initiative 无法 follow_player
[ ] background 无法 LOW
[ ] user 回合仍可 LOW
[ ] SAFE 行为不受影响
[ ] Policy 本身无需放宽
[ ] 所有 Phase 3E Tool 保持不变
[ ] Phase 1~3D 回归通过
[ ] Phase 3E 测试全部通过
[ ] 新增 CharacterRuntime 真实路径测试
[ ] CI 双 job 全绿
```

最终：

```text
PHASE 3E.1 COMPLETE
READY FOR PHASE 4
```

输出：

1. 修改文件清单
2. Turn Origin 设计
3. Context 传播链
4. Initiative / Background 安全验证
5. User Turn 验证
6. WebUI Dry Run 验证
7. 新增测试
8. 全量回归
9. Phase 4 readiness