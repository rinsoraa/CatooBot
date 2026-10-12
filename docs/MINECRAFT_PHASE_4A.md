# CatooBot Minecraft Phase 4A — Action Confirmation Gate & Minecraft Chat User Bridge

> 本阶段只做两件基础设施（**不新增任何世界修改动作**）：
> ① MEDIUM / HIGH / DESTRUCTIVE 的**确认门**；② 把**游戏内玩家聊天**正式接成 USER 回合。
> dig / place / attack / craft / eat / inventory / container / redstone / 采集 / 建造 / 战斗
> 一律**没有注册**（Phase 4B 才做第一个单方块 dig）。

---

## 一、全局链路（确认门插在哪）

```text
LLM Tool 调用
   ↓
MinecraftActionPolicy.check()          app/integrations/minecraft/agent.py
   1 注册？ 2 Minecraft 启用？ 3 工具启用？ 4 在线？ 5 风险开关？
   6 USER 回合？ 7 可信玩家？ 8 前台忙？ 9 需要确认？
   ↓ （MEDIUM/HIGH/DESTRUCTIVE → code=minecraft.confirmation_required）
MinecraftAgentBridge._resolve_confirmation()
   ↓ 消费一条匹配的 PENDING（来源必须 USER）→ CONSUMED（一次性）
MinecraftService → Action Runtime → Minecraft
```

SAFE / LOW **完全不变**：SAFE 直接允许（不需要 USER、不需要 trusted）；LOW 要 USER 回合
（Phase 3E.1），来自游戏内时还要可信玩家（本阶段新增）。确认门只作用于 MEDIUM 及以上，
而本阶段没有任何这类动作 —— 所以它是**装好的门**，由测试里的桩动作驱动验证。

## 二、确认对象（§五-§八）

`app/integrations/minecraft/confirmation.py`

| 字段 | 说明 |
|---|---|
| `confirmation_id` | `cfm_<12 hex>`，只在内存里生成 |
| `session_id` / `user_id` | 归属：**跨用户/跨会话都不可用**（§六） |
| `tool` / `risk` | 哪个动作、什么等级 |
| `arguments_hash` | 参数指纹（sha256 前 16 位，键序无关）：**确认的是哪一个具体动作不可变**（§七） |
| `arguments` | 原参数（用于复检与追溯） |
| `created_at` / `expires_at` | TTL：默认 60s（配置 10~300），到点自动 EXPIRED |
| `status` | `PENDING → CONSUMING…`：PENDING / CONFIRMED / EXPIRED / CANCELLED / CONSUMED |
| `summary` | 给用户/界面看的一句话（不含正文、不含机密） |

`ConfirmationStore`（内存、有界 `max_pending=32`、TTL、`sweep()` 落定过期）：
**重启即全部失效**，绝不把旧授权复活（§三十六）。

消费规则（§十一-§十四，顺序固定）：

1. **来源**：只有 `TurnOrigin.USER` 才能消费；INITIATIVE / BACKGROUND / SYSTEM →
   `minecraft.confirmation_not_user_turn`（模型自己的话、系统提示、上一条工具结果都不算确认）；
2. **存在/状态**：不存在或已 CONSUMED/CANCELLED → `minecraft.confirmation_invalid`；
   EXPIRED → `minecraft.confirmation_expired`；
3. **归属**：`(session_id, user_id)` 不匹配 → `minecraft.confirmation_mismatch`
   （日志里记 `reason=hijack`，用于排查冒用）；
4. **参数指纹**：hash 不同 → `minecraft.confirmation_mismatch`，并**作废旧确认、按新参数重挂一条**
   （用户要为新动作再确认一次）；
5. **一次性**：通过后立刻置 CONSUMED，同一个 id 再用就是 `confirmation_invalid`。

模型侧看到的形状（§十）：第一次调用返回结构化失败 + 待确认信息，模型应把这件事用自然的
话告诉用户，等用户明确说「确认」后**用完全相同的参数**再调用一次：

```json
{"ok": false,
 "error": {"code": "minecraft.confirmation_required",
           "message": "这个 Minecraft 动作需要用户确认；先把这件事用你自己的话告诉用户…"},
 "confirmation": {"confirmation_id": "cfm_…", "tool": "minecraft_dig", "risk": "MEDIUM",
                  "summary": "minecraft_dig（MEDIUM）：x=120 y=64 z=-230",
                  "expires_at": 1730000060.0, "status": "PENDING"}}
```

## 三、Minecraft Chat → USER 回合（§十六-§十九）

`app/integrations/minecraft/chat_bridge.py`（`MinecraftChatBridge`，由 `Bot` 装配、
`MinecraftService.start()` 注册成事件监听器）：

```text
minecraft.chat（玩家在服务器里说话）
  ├── sandbox.submit_external（原有，世界认知/行为活动 —— 不动）
  └── MinecraftChatBridge → CharacterRuntime.respond(
          session_id = minecraft:{host}:{port}:{username},
          user_id    = MC 用户名,
          turn_origin= TurnOrigin.USER,
          origin_metadata={minecraft_player: username})
      → 正常 Tool Loop（SAFE 不限 / LOW 需可信玩家）
      → MinecraftService.send_chat(回复) → 玩家在游戏里看到她回话
```

* 会话独立于 QQ（§十八）：`minecraft:127.0.0.1:25565:空凛`，不进 `private:10000x` 的历史；
* 回合与对话都记进这个会话（`conversations.append_user_message/assistant_message`），她能记住；
* 回复长度上限 `chat.max_reply_chars`（默认 200，Minecraft 聊天本身 256）；
* 回合在**后台任务**里跑，事件通道不被 LLM 拖住（WorldPerception 照常轮询）；
* 一次游戏内消息 = **一个**角色回合（§二十九：沙盒与对话是两个消费者，互不替代、互不触发）。

**防循环硬门禁（§三十）**：`sender == 罐头自己的 MC 名` 直接丢弃 —— 判定同时看
runtime 上报的 username 与镜像里的 `connection.username`；镜像还没拿到自己的名字时
**宁可不回**（保守），否则「她说话 → 收到自己的聊天 → 模型 → 再说话」会无限循环。
此外悄悄话（`private: true`）不公开回、系统行（无 username）忽略、并发上限 4 条。

## 四、可信玩家（§二十-§二十三）

```yaml
minecraft:
  agent:
    trusted_players: []      # 例如 ["RinsoraNeko"]
```

| 风险 | 谁能触发 |
|---|---|
| SAFE（world / chat / look_at / stop） | **任何玩家**（聊天也一样） |
| LOW（move_to / follow_player） | USER 回合 **+ 可信玩家**（来自游戏内时）；QQ/WebUI 用户不受这份名单约束 |
| MEDIUM 及以上 | 目前没有这类动作；将来要 USER + 可信 + 确认 |

不可信玩家得到 `minecraft.user_not_trusted`（结构化、无 traceback），
**且不产生任何动作调用**；但他照样能跟罐头聊天（§二十五）。

身份只在**运输层**声明：`origin_metadata={minecraft_player: ...}` 由聊天桥注入，
`CharacterRuntime._tool_context` 先合并它、再用 `turn_origin` 派生的键覆盖 ——
调用方无法靠元数据把主动发言伪装成用户回合（有测试）。

## 五、Action Policy 最终结构（§三十三）

```text
1 Tool registered?   （风险表里没有 → minecraft.action_invalid）
2 Minecraft enabled? （minecraft.disabled）
3 Tool enabled?      （minecraft.disabled）
4 Online?            （**默认需要在线**；只有 minecraft_world / minecraft_stop 例外 → minecraft.offline）
5 Risk enabled?      （allow_safe / allow_low / allow_medium / allow_high / allow_destructive → minecraft.action_not_allowed）
6 USER Turn?         （LOW 及以上 → minecraft.action_not_allowed）
7 Trusted user?      （来自游戏内且不在名单 → minecraft.user_not_trusted）
8 Busy?              （独占前台动作 → minecraft.action_busy）
9 Confirmation?      （MEDIUM 及以上 → minecraft.confirmation_required → 消费 PENDING）
10 Execute           （MinecraftService → Action Runtime；绝不在策略层碰 Mineflayer）
```

第 4 步本阶段从「按名字列白名单」改成「默认需要在线，两个例外」——这样 Phase 4B 加
dig/place 时不会漏掉在线门。

## 六、错误码（§三十四）

| 错误码 | 含义 |
|---|---|
| `minecraft.confirmation_required` | 这个动作需要用户确认（同时挂一条 PENDING） |
| `minecraft.confirmation_invalid` | 确认不存在 / 已使用 / 已取消 |
| `minecraft.confirmation_expired` | 确认已过期（已按当前参数重挂一条） |
| `minecraft.confirmation_mismatch` | 归属或参数与用户确认时不一致（冒用被拒） |
| `minecraft.confirmation_not_user_turn` | 非用户回合尝试确认 |
| `minecraft.user_not_trusted` | 游戏内玩家不在可信名单（LOW 及以上） |

全部结构化（`{ok:false, error:{code,message}}`），绝不把 traceback / HTTP 栈交给模型。

## 七、日志（§三十五）

```text
[MC Policy] allowed tool=minecraft_move_to risk=LOW turn_origin=user trusted=True confirmation=not_required
[MC Policy] confirmation tool=minecraft_dig risk=MEDIUM turn_origin=user trusted=True
[MC Confirmation] created id=cfm_ab12… tool=minecraft_dig risk=MEDIUM user=RinsoraNeko session=minecraft:…
[MC Confirmation] consumed id=cfm_ab12… tool=minecraft_dig risk=MEDIUM user=RinsoraNeko
[MC Confirmation] rejected id=cfm_ab12… reason=hijack user=10002/10001 session=…/…
[MC Chat] RinsoraNeko: 罐头你过来（session=minecraft:127.0.0.1:25565:RinsoraNeko, history=2）
[MC Action] tool=minecraft_move_to action_id=act_… status=RUNNING
```

绝不记录 password / token / API key / Minecraft 认证信息。

## 八、WebUI（§三十二）

连接页新增 **Pending Confirmation** 面板（只读 + 三个 Debug 按钮）：

```text
Tool            Risk     Target                        User      Expires   Status    操作
minecraft_dig   MEDIUM   minecraft_dig（MEDIUM）：x=120…  RinsoraNeko 12:34:56 待确认   CANCEL / EXPIRE
Trusted Players: RinsoraNeko    TTL: 60 秒
[ CREATE TEST CONFIRMATION ]
```

* `GET /api/v1/minecraft` 的 `agent` 块新增 `confirmations`（TTL / 上限 / pending 列表）
  与 `trusted_players`；
* `POST /api/v1/minecraft/agent/confirm` 只接受 `create_test` / `cancel` / `expire` ——
  **没有** `confirm` / `consume`：管理员按钮不能代替用户在对话里说「确认」，
  也不可能绕过 user/session/arguments_hash（非法 action → 400 `minecraft.confirmation_invalid`）。

## 九、测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_minecraft_confirmation.py`（16） | §二十七 十一条全在：创建/复用、四种来源（USER 可消费，INITIATIVE/BACKGROUND/SYSTEM 拒绝且不改状态）、归属（用户/会话/参数指纹）、TTL 过期、一次性、取消、未知 id、有界内存、无机密 |
| `tests/test_minecraft_agent_confirm_gate.py`（18） | 真实工具链路：`ToolRuntime` → `Policy` → 确认消费 → 执行面；需要确认→用户确认→执行→再要确认；非用户回合不创建也不消费；参数改变不重用（作废重挂）；会话绑定；过期；`allow_medium=False` 先于确认；离线先于确认；不泄漏给别的工具；**可信/不可信/SAFE/QQ 不受限**；生产注册表无 dig/place/attack/craft |
| `tests/test_minecraft_chat_bridge.py`（18） | §二十八 六个用例全在：变成 USER 回合、保留 MC 用户名、可信玩家 LOW 放行、不可信拒绝（且仍能聊天）、SAFE 对所有人开放、无重复回合；§二十四 Test A/B/C（过来→move_to、跟着→follow RUNNING、停→stop 取消）；§三十 防循环（自己的话/无用户名/悄悄话/系统行/关闭开关）；回复截断；模型失败被吞 |
| `tests/test_minecraft_intent_propagation.py`（13） | Phase 3E.1 全部保留 + `origin_metadata` 不能伪造授权 |
| `tests/test_web_api_minecraft.py`（19） | `agent.confirmations` 投影、确认端点只能缩小授权、非法 action 400、缺 Minecraft 503 |
| `webui/src/pages/__tests__/minecraft.spec.ts`（23） | 确认面板：工具/风险/目标/用户/状态/可信玩家；CANCEL/EXPIRE 请求体；页面从不发 `confirm`/`consume` |

回归：Phase 1 / 2 / 2.1 / 3A / 3B / 3B.1 / 3C / 3D / 3E / 3E.1 全部保留并通过
（Node 四套 + vitest + Playwright + pytest）。

## 十、已知限制

1. **没有 MEDIUM/HIGH 动作**（§二）：确认门由测试桩驱动验证；Phase 4B 接 dig 时只加
   工具 + 风险表条目，不需要改确认基础设施。
2. **确认只在内存**：重启后全部失效（有意为之，§三十六）；不落库、不做跨重启恢复。
3. **悄悄话不回复**：本阶段只有公共聊天发送能力，私聊内容不回（避免把私密内容公屏化）。
4. **MC 身份与 QQ 身份不绑定**：游戏内玩家是独立身份（独立会话、独立关系）；可信名单
   是唯一的"授权"机制，还没有映射表/管理 UI（§二十三，后续阶段）。
5. **游戏内聊天不经过行为引擎**（参与频率/去抖/静默判定都是 QQ 侧的）：玩家说话就会得到
   回复；并发上限 4 条、回复最长 200 字符防止刷屏。
6. 消息长度与语气的约束写在回合的 `extra_instruction` 里（"游戏内随口说的话，回复要短"）。
7. `minecraft_world` 的只读缓存 TTL 仍为 2s；确认不会进入 LLM 上下文历史（§三十七）。

## 十一、Phase 4B readiness

* `ACTION_RISK`（风险表）可通过 `MinecraftAgentBridge(service, risk_table=…)` 扩展：
  Phase 4B 只需把 `"minecraft_dig": "MEDIUM"` 写进正式表 + 注册一个 Tool 类 + 把
  `allow_medium` 打开（默认 true，但风险开关是最后一道）；确认门、可信门、事件回流、
  WebUI 面板都已就绪。
* 第一个动作只做**指定单个方块**（不要一上来做"帮我挖铁"：那会引入寻找/导航/工具选择/
  库存/耐久/掉落/连续动作，复杂度跳级）。
* 目标闭环：`用户「把我面前这个方块挖掉」→ World 感知定位方块 → 确认 → minecraft_dig →
  Action Runtime → Mineflayer dig → Block Broken → WorldPerception 更新 → LLM 得到真实结果`
  ——这才第一次形成「感知 → 判断 → 用户确认 → 行动 → 世界改变 → 重新感知」的完整闭环。
* 届时需要新增的：确认提示文案（工具 description 里写清"会破坏什么"）、破坏结果的
  结构化回执（block / position / 掉落物）、以及 `minecraft.interaction.*` 配置段。
