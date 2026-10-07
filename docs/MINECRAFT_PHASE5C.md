# Minecraft Phase 5C — Identity Bridge + Persistent World Memory

> 目标：让她**知道谁是谁**、**记得在这个世界里发生过什么**，并且能拿当前世界去
> 核对那些记忆 —— 但**绝不**因为"记得"就越过任何安全边界自动行动。

一句话架构（§一）：

```
WorldPerception（运行时，当前事实的唯一来源）
        ↓ 只读复核 / 对账（单向）
Minecraft Memory Domain（app/memory/minecraft/，只存语义事实）
        ↑ 写入（任务结果 / 玩家出现 / 重要观察 / 用户明确说过的事）
TaskRuntime（已有）        QQ 入口（已有）        角色回合（已有）
```

三条不变量（写死在代码里的规则）：

1. **记忆只提供上下文，不提供权限** —— 授权永远来自 TaskRuntime / Policy / Confirmation；
   记忆域没有任何"放行"出口，Policy 与 TaskRuntime 的源码里也**不认识**记忆桥。
2. **世界永远是权威** —— 对账方向只有 `WorldPerception → Memory`，绝不反向；
   世界说"不在了"就把记忆标成 `INVALIDATED`（**行不删**）。
3. **记忆里的内容不是指令** —— 试图改规则的语句照记，但只当"用户说过这样一句话"
   （置信度封在 0.40、provenance 标 `untrusted_directive`），永远不可能变成 Policy 输入。

## 一、身份（§三-§九）

### 规范身份

```python
MinecraftServerIdentity(
    server_id="mc-5a7d5e6316467e0f",
    host="127.0.0.1",
    port=25565,
    edition="java",
    world_key="",
    label="",
)
MinecraftIdentity(server_id=..., player_uuid="11111111222233334444555555555f2c", username="空凛")
```

* `server_id = "mc-" + sha256(f"{edition}|{host}|{port}|{world_key}")[:16]` ——
  **同一台服务器重启后不变**，换 host/port/世界/版本必变；拿不到 host 时返回空 id
  （绝不编一个"看起来像"的 id）。
* `player_uuid` 是 **canonical identity**（`canonical_uuid()` 统一小写去连字符），
  `username` 只是显示名 —— **改名不换人**，同名不同 uuid 也不是同一个人。
* 对外投影（QQ / WebUI）只给 `uuid_suffix`（后四位），完整 UUID 不出现在用户面前。

### 绑定（IdentityLink）

```python
IdentityLink(
    platform="qq",
    user_id="2731431246",
    server_id=...,
    player_uuid=...,
    username="空凛",
    status="VERIFIED",
    source="explicit_verification",
)
```

来源优先级（§六）：`explicit_verification`（用户自己确认）>
`configured`（运维在配置里写死）> （服务器 uuid 事实）> 无绑定。
**不存在"昵称相同就自动绑定"**，也不做任何 LLM 猜测。

QQ 流程（`app/integrations/minecraft/identity_commands.py`，认领式订阅，排在任务入口之前）：

| 用户说 | 她做什么 |
| --- | --- |
| 「把我和 Minecraft 里的 空凛 绑定」 | 找到**在线**同名玩家 → 回「服务器 + 玩家名 + UUID 尾号 + 请回『确认绑定』」（这一步**不写任何东西**） |
| 「确认绑定」 | 重新解析同名玩家，uuid 与刚才一致才写 VERIFIED；人走了 / 换了 uuid → 什么都不改 |
| 「解除绑定」 | 旧 link 变 `REVOKED`，**历史行保留**（§九） |
| 「把我和 Minecraft 绑定」（没给玩家名） | 追问玩家名，绝不拿 "Minecraft" 当玩家名 |
| 普通聊天 | **不认领**，照旧走人格回复 |

冲突（§三十五）：某个 uuid 已经属于别人时，新的绑定请求 → `identity.conflict` 拒绝，
**绝不覆盖**，并另记一条 CONFLICT 行作为证据。绑定**不授予任何权限**：
`writer.relationship()` 明确写 `grants_permission=False`，Policy 从不读它。

### 持久化

迁移 28 建 `minecraft_identity_links`，两个 partial unique index 钉死"一台服务器上
一个 uuid 只能属于一个 VERIFIED 主体"、"一个主体在同一服务器上只有一个 VERIFIED 绑定"。
身份层任何读/写失败只设 `degraded_reason` 并返回稳定错误码（`identity.unavailable`），
**绝不影响**任务执行。

## 二、记忆域（§十-§十九/§四十七）

`app/memory/minecraft/` 是**现有记忆引擎的一个域适配层**，不是第二套存储：

* 复用同一张 `memories` 表、同一套 dedupe/conflict/embedding/quota/status 逻辑；
* 只用**一个专用 scope** `character:<角色>:minecraft` 与普通聊天记忆隔离；
* 每条记忆 `source="minecraft"`，`provenance` 里带 `domain=minecraft` + `server_id`
  （检索端据此做服务器隔离，§三十六）。

### 七种 kind（§十一）

`PLAYER` / `LOCATION` / `RESOURCE` / `TASK` / `EVENT` / `RELATIONSHIP` / `PREFERENCE`

映射到既有 category：`profile` / `fact` / `event` / `relationship` / `preference`
（TASK→event 走 episodic 层，其余是 semantic）。

### 来源与置信度（§十八/§十九）

| 来源 | 默认置信度 | 上限 |
| --- | --- | --- |
| `USER_STATED` | 0.95 | 0.99 |
| `OBSERVED` | 0.90 | 0.95 |
| `TASK_RESULT` | 0.90 | 0.99 |
| `SYSTEM` | 0.85 | 0.85 |
| `DERIVED` | 0.70 | **0.70** |

`DERIVED` 永远不能被写成"我亲眼看见"（检索时会加「我推测：」前缀）；
未列出的来源（例如将来真要引入的模型推断）由 `confidence_for()` 兜底为 0.50 ——
统一一套裁决，**不新造评分体系**。

### 新鲜度（§二十/§二十一）

`Freshness = ACTIVE | STALE | INVALIDATED | SUPERSEDED`，直接映射到引擎的
`status = active | stale | invalidated | superseded`（迁移 27 之前的引擎已经支持）。
**过期不删历史** —— 只改状态；`store.facts()` 默认不返回 `INVALIDATED`，
`store.all_facts()` 连历史一起给（WebUI 展示用）。

### 写入时机（§三十）

只有这些事件会写记忆：任务收尾（SUCCEEDED / FAILED / EXPIRED）、玩家出现、
重要地点/资源观察、用户明确说过的事、关系变化。
`bot moved 1 block` / `look_at` / 路径更新 / 背包轮询 / 每个 tick —— **域里根本没有这种 API**
（`MinecraftMemoryWriter` 没有 `moved/step/tick` 之类的方法，测试里有断言守着）。

**任务成功**时额外写一条世界事实：取任务里第一条带完整坐标的 `minecraft_dig` 步骤，
把「(x,y,z) 附近 有一块 \<expected_block\>」记成 `RESOURCE`，来源 `TASK_RESULT`
（"她亲手挖到过"，不是"她亲眼看见"）。每条任务**最多一条**，同一 16 格按语义身份强化。
这是"刚才那棵树在哪里"能回答、并且能被当前世界复核的基础：那条事实带坐标，
她后来把方块挖走了，对账/检索就会把它标成已失效 —— 回答自然变成"那儿现在没有了"。

任务结果只留语义摘要（objective / 结果 / 拿到什么 / 当时的位置），
**不留** action_id、超时、pathfinder 调试、Node 事件、checkpoint 树（§三十一/§三十二）。
失败的任务写成"当时的情况，未必一直如此"并标 `temporary`，不做"这里永远不能走"这种永久结论。

### 去重 / 冲突（§三十三/§三十四）

`dedupe_key = sha256(domain|server_id|kind|subject)[:24]` = 语义身份：
同一件事反复观察 → **强化那一条**（`observation_count` +1、置信度 +0.05 且不超上限）。
矛盾内容不覆盖：`LOCATION` / `PREFERENCE` / `RELATIONSHIP` 按"语义组"
（`kind:subject` 去掉坐标）保留冲突，旧的那条标 `superseded` 并记住 `conflicts_with_id`。

## 三、对账（§二十二/§二十三）

`MinecraftMemoryReconciler.reconcile(server_id, ...)`：

| 世界复核结果 | 记忆怎么办 |
| --- | --- |
| `present`（还在） | `touch()`：`last_verified_at` 前进、观察次数 +1、回到 ACTIVE |
| `absent`（世界说不见了） | `INVALIDATED`（**行还在**） |
| `unknown`（太远 / 桥挂了 / `reason=unavailable`） | **什么都不做**（读不到 ≠ 世界变了） |
| 非位置事实（PLAYER/TASK/EVENT/…） | 只按时间变 `STALE`（默认 24h 没复核） |

位置类事实的复核口径：

* `RESOURCE`：近（≤4.5 格）用 `dig_capability` 读那一格（空气 / 换了方块 → absent）；
  远（≤32 格）用 `find_blocks` 扫一圈 —— **"没扫到"不算 absent**（只当 unknown）；
* `LOCATION`：`radius > 0` 时坐标只是 16 格聚簇锚点，锚点空说明不了什么（unknown）；
  `radius == 0` 时那一格就是被记住的东西本身，空了就是 absent。

复核只用 SAFE 只读（`dig_capability` / `find_blocks`）；记忆桥/对账器上**没有**
`move_to` / `dig` / `place` / `pickup_item` / `equip` / `container_transfer` 这些方法
（不是"没调用"，是"没有"）。

## 四、检索适配器（§二十四-§二十七/§四十九）

```python
context = await bridge.retriever.retrieve(
    server_id=..., query="刚才那棵树在哪里？", player_uuid=..., position=...
)
block = await bridge.context_block(text=..., platform="qq", user_id="2731431246")
```

* 输入：`server_id`（必须）+ 可选玩家 uuid / 当前位置 / 查询词；
* 输出：**≤5 条**、每条一句话（≤120 字，`LINE_MAX_CHARS`）、整块 ≤420 字；
* 优先级（§四十九）：当前玩家 > 附近资源/地点 > 最近任务 > 事件/关系/偏好 > 历史
  （已 `INVALIDATED` 的排在最后，超出预算就直接不进上下文）；
* 排序内的相关度是朴素 2 字滑窗命中（不叫模型）；
* 检索时对**前 2 条**位置事实做一次只读世界复核：世界说"不在了"→ 那句话当场改写成
  「（这条已经被当前世界证伪）」并把状态落盘 —— **当前世界优先**。

注入到 LLM 的位置：`CharacterRuntime.respond()` 把这块拼在她"此刻处境"那一行后面
（`builder.build(..., minecraft=minecraft_context)`），QQ 与游戏内聊天**共用同一条路径**。
`platform` 由会话前缀决定（`minecraft:` → `minecraft_chat`，其余 → `qq`）。

## 五、接入 Bot（§二十八-§三十/§三十八-§四十）

`app/core/bot.py`：

* 连接层**真的起来了之后**（`start()` 里 `minecraft.start()` 成功）才调用
  `_setup_minecraft_memory()` 建 `MinecraftMemoryBridge`（复用 `self.memory` / `self.database`），
  把 `self.minecraft_memory` 交给角色运行时，并把 `_on_minecraft_memory_event` 挂到服务事件上。
  放在这里有两个原因：记忆 scope 用**角色名**，而人设是 `start()` 里才从磁盘/库里读进来的
  （放在 `__init__` 会拿到 `default` —— 真机上就这么错过一次）；连接层起不来就干脆没有记忆能力，
  不留半死对象；
* `_on_minecraft_memory_event` **只认** `minecraft.player_joined`（其它事件一律不写记忆）；
* **她自己不算"一个玩家"**：`self_username()` 从镜像的 `connection.username` 读她自己的 MC 名字，
  凡是她自己的 `player_joined` 一律跳过，`online_players()` 也把她排除（否则会记出
  「Catodayo 在这个服务器里活动过」，甚至能把自己绑给自己 —— 真机踩到过）；
* `_publish_task_event()` 在 `task.succeeded` / `task.failed` / `task.expired` 上
  后台调 `on_task_finished(record)`（记忆写失败只记账，绝不冒泡到任务收尾）；
* `_start_memory_reconcile()` 起一个周期对账任务（默认 300s，随 `shutdown()` 一起停）；
* `_apply_configured_links()` 处理运维显式配置 `minecraft.memory.linked_players`
  （QQ 号 → 玩家名）：玩家不在线就等下一轮；已有显式绑定绝不覆盖；一条失败不影响其它。

装配失败**只降级记忆**：`self.minecraft_memory = None`，聊天与任务完全不受影响（§三十九/§四十）。

## 六、配置（只增加真正需要的旋钮）

```yaml
minecraft:
  memory:
    enabled: true                     # 关掉 = 完全没有记忆能力（权限一点不变）
    reconcile_interval_seconds: 300.0 # 多久拿当前世界核对一次
    context_items: 5                  # 一次对话最多带几条（≤5）
    linked_players: {}                # 运维写死的「QQ 号 → 玩家名」（只认人，不给权限）
```

`allow_medium` 的默认值**没有变化**，记忆也不会降低任何动作的确认要求（§五十二）。

## 七、API / WebUI

`GET /api/v1/minecraft/memory`（只读、恒 200）：

```json
{"ok": true, "enabled": true,
 "status": {"enabled": true, "server_id": "mc-…", "server_label": "127.0.0.1:25565",
            "character_key": "空凛", "facts": 12, "stale": 1, "invalidated": 2,
            "links": {"VERIFIED": 1, "REVOKED": 1},
            "memory_degraded": "", "identity_degraded": "",
            "last_reconcile": {"checked": 12, "invalidated": 1}},
 "facts": [{"kind": "RESOURCE", "content": "…", "source": "OBSERVED",
            "confidence": 0.9, "fresh": "ACTIVE", "observation_count": 3}],
 "links": [{"platform": "qq", "user_id": "2731431246", "username": "空凛",
            "uuid_suffix": "9f2c", "status": "VERIFIED", "source": "explicit_verification"}]}
```

记忆能力未装配/未启用 → `enabled: false`（读端点恒 200，不是故障）；
`memory_degraded` / `identity_degraded` 非空就是**降级**（绝不假装检索成功）。

WebUI 的 Minecraft 页新增只读卡片 **Identity & World Memory**：状态 + 绑定表（只显示
UUID 尾号）+ 最近 50 条事实（kind / 内容 / 来源 / 置信度 / 新鲜度 / 观察次数）。

## 八、真机取证

```bash
# 先把 CatooBot 本体停掉（这个脚本要独占 runtime 进程）
.venv/Scripts/python.exe scripts/memory_smoke_real.py                      # 全部
.venv/Scripts/python.exe scripts/memory_smoke_real.py --sections identity,memory
.venv/Scripts/python.exe scripts/memory_smoke_real.py --sections setblock  # 交互式：你自己 /setblock
.venv/Scripts/python.exe scripts/memory_smoke_real.py --sections restart   # 跨进程持久化
```

* `identity`：真实 uuid、冲突拒绝、解绑保留历史、重新绑定；
* `memory`：玩家上线 → 事实（带 uuid）、同一件事三次观察 → 只有一条 + 观察次数 3；
* `world`：空气坐标 / 换了方块 → `INVALIDATED`，默认检索不再当成成立的事实；
* `setblock`：打印 `/setblock x y z air` 等你真的改世界，再对账 → `INVALIDATED`
  （超时未改 → 如实 SKIPPED，绝不伪造 PASS）；
* `retrieval`：`context_block` ≤5 条 / ≤420 字 + 检索时的真实世界复核；
* `restart`：父进程写标记 → **子进程**（全新 Python，同一个 SQLite）读回绑定与事实。

## 九、错误与降级语义

| 情况 | 表现 |
| --- | --- |
| 记忆 DB 读不到 | `store.degraded_reason` 非空、`context_block()` 返回空串、`status.memory_degraded` 可见 —— 聊天照常 |
| 身份层读不到 | 稳定码 `identity.unavailable` + 绑定表为空（不抛异常、不猜） |
| 世界复核读不到 | `unknown` → 记忆**不动**（读不到 ≠ 世界变了） |
| 记忆装配失败 | `minecraft_memory = None`，聊天/任务完全不受影响 |
| 坏记忆行（缺 kind / 非法 kind / 非本域） | 直接跳过，绝不猜（`to_fact()` 返回 None） |

## 十、不做的事（§二 绝对禁止范围）

不新增 Minecraft 工具（仍是 **19** 个）、不新增 ActionRuntime 动作、不新增 TaskRuntime 状态；
没有自动砍树/挖矿/采集链/建造/探索/战斗/寻找玩家/跨服；
不把整个 world snapshot 或原始聊天全文存进记忆；不复制整棵 Task checkpoint 树；
不因为"关系是 trusted_companion / 历史上成功过 / 认识这个玩家"就降低 MEDIUM 确认要求；
记忆永远不直接驱动 move_to / dig / place / follow / pickup。

## 十一、已知偏差与后续（决策记录）

### 0. 历史 scope（缺陷期间的数据）：**保留、不删、不盲迁 + 审计标记 + 默认排除**
（**2026-10-08 决定**）

第一版把记忆桥装配在 `__init__`（那时角色名还没从库里读出来），于是有一批事实落进了
`character:default:minecraft`。修好装配点之后这批数据**原样保留**，处理方式：

* **不删除、不盲迁**：它们是那次缺陷的证据，改数据会让证据消失；
* **代码里显式登记**：`app/memory/minecraft/store.py::LEGACY_SCOPE_KEYS = ("character:default:minecraft",)`
  + `is_legacy_scope()`，并写明成因；
* **默认排除**：`facts()` / 检索 / 对账 / 状态里的 `facts` 计数**都不含**它（`scope_key` 只匹配当前角色）；
* **只读审计出口**：`store.legacy_facts()` / `bridge.legacy_view()` / WebUI 卡片上的
  `Legacy (audit only)` 一栏 + `GET /api/v1/minecraft/memory` 的 `legacy` 数组；
  每条审计行都带 `legacy_scope: true`，一眼能看出"这不是她现在记得的事"；
* **兜底 key 改名**：角色名拿不到时的兜底从 `default` 改成 `unscoped`
  （`FALLBACK_CHARACTER_KEY`），保证新数据**永远不会**写进那个审计抽屉。

### 1. 角色隔离键的写法与沙盒不同（**2026-10-08 决定：暂选 A —— 保持现状并标记**）

| | 写法 |
| --- | --- |
| 沙盒 / 人格记忆（既有约定） | `character:<角色名>@<圣经哈希[:8]>`（`app/sandbox/runtime.py` 的 `character_id`） |
| Phase 5C 的 Minecraft 记忆域 | `character:<角色名>:minecraft` |

* **隔离性不受影响**：两者 `scope_key` 不同、`source="minecraft"` + `provenance.domain` 双重标记，
  互不串味；跨服另有 `server_id` 过滤。
* **代价（已知并接受）**：同一角色在共享列 `memories.character_id` 里会有两个值
  （沙盒是 `罐头@1dae9716`，MC 是 `罐头`）；**改角色名或改圣经内容**时，沙盒那批因为带哈希不会散，
  MC 那批会留在旧 scope（卡片上看不到旧记忆，数据仍在库里）。
* **留待后续评估**：若要与之对齐，改成 `character:<角色名>@<圣经哈希>:minecraft` 即可
  （同时要把已有的 MC 行 `character_id` 一起迁移）。在此之前**不迁移**已有数据。

### 2. 生产写入点只有五类

玩家上线（`PLAYER` + 首次见面 `EVENT`）、绑定成功（`RELATIONSHIP`）、任务终态（`TASK`）、
任务成功且有 `minecraft_dig` 步骤（`RESOURCE`）。`LOCATION` 与 `PREFERENCE` 目前**没有**生产写入点
（写入 API 与测试都在，只是没有调用方）—— 真机上不会看到这两类事实。

### 3. 继承自既有引擎的行为

"内容近似 → 旧条 `superseded`"是引擎 v0.5 就有的合并策略：两个玩家各自的
「第一次在这个世界里见到 X」文本很像，后写的那条会把先写的置为 `SUPERSEDED`
（行保留、默认检索不再返回）。这是引擎行为，本阶段没有改动它。

