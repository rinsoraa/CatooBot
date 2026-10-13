# CatooBot Minecraft Phase 8B — Cross-Platform Identity Continuity & Trust Context

## 跨平台身份连续性与信任上下文

**性质**：集成既有身份桥与角色对话上下文。**不新增 Minecraft 玩法能力、不改变任何动作授权语义、
不新增表、不新增迁移。**

**目标**：同一个已验证的用户在 QQ 与游戏内聊天拥有一致、可审计的「是谁」与角色关系上下文，
并严格区分「身份已验证 / 角色关系亲近 / 获准驱动游戏动作」三件事。

## §1 基线与工作区

| 项 | 值 |
| --- | --- |
| 基线 SHA | `fde6d68171b71fb9b0835108f64111b087e61698`（Phase 8A FIX 之后的 main HEAD，273 commits） |
| 权威目录 | `E:\WorkSpace ZCode\CatooBot`（唯一开发真源） |
| 发布镜像 | `E:\WorkSpace ZCode\CatooBot_github`（== `origin/main`） |
| 任务书归档 | `docs/specs/CatooBot Minecraft Phase 8B — Cross-Platform Identity Continuity & Trust Context.md`（19836 bytes / 216 行，SHA256 `420FD2D5…2CEC`，与上传源逐字节一致） |

基线核对：`git status --short` 空、`HEAD == origin/main`。

## §2 现状：真实调用链与数据流（以源码为准）

### §2.1 Minecraft 入站

```text
minecraft.chat（runtime.js:3692 / 3697 / 3713 —— **只有 username，没有 uuid**）
  → MinecraftChatBridge.apply_event（丢包：未启用 / 空 / 自身发言 / 悄悄话 / 忙）
  → _reply：session = minecraft:{host}:{port}:{username}
  → bot.character.respond(session, user_id=**username**, turn_origin=USER,
                          origin_metadata={PLAYER_KEY: username}, history=...)
```

**关键事实（改动前）**：游戏内回合的「用户 ID」就是**显示名**；`origin_metadata` 只带玩家名；
没有任何身份解析。于是同一个人的游戏内关系/连续性与 QQ 侧是**两条**。

### §2.2 QQ 入站

```text
plugins/chat/plugin.py:638
  → bot.character.respond(session, user_id=**QQ 稳定 user id**, turn_origin=USER,
                          user_name=昵称, is_group=..., **origin_metadata 未传**)
```

### §2.3 既有身份基础设施（本阶段**全部复用**，不重建）

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| `IdentityStore`（真源） | `app/integrations/minecraft/identity.py` | `verify / revoke / verified / by_player / links_for / all_links / counts` + `degraded_reason`；`PLATFORMS = (qq, minecraft_chat, webui, system)`；`canonical_uuid()` 归一 UUID |
| 暴露路径 | `bot.minecraft_memory.identities` | Bot 装配的真实 Store（`bot.minecraft_identity` 是**命令处理器**，不是 Store） |
| 服务器身份 | `server_identity(host, port)` → `mc-<16 hex>` | 同一台服务器重启不变；换 host/port/世界必变 |
| 在线玩家（权威 UUID） | `MinecraftMemoryBridge.online_players() / player_named()` | UUID 来自运行时 semantic players（mineflayer）；**username 只作显示名** |
| 记忆检索已有身份消费 | `memory_bridge.context_block(platform, user_id)` | 已用 `link_for` 把记忆限定到已核验的人（QQ 侧一直有效） |

### §2.4 运行时权威 UUID 的来源（本阶段的关键取证）

* `minecraft.chat` 的**三处**上报（`runtime.js:3692/3697/3713`）payload 里**都没有 uuid**；
* `minecraft.player_joined / player_left`（`3718/3725`）**带** `uuid: player?.uuid ?? null`（Phase 5C 加的）；
* 在线玩家视图（`runtime.js:1689-1704`）**带** `uuid`，源码注释明写「Phase 5C：canonical identity —— mineflayer 给的玩家 UUID」。

→ 因此 MC 侧解析顺序固定为：`username → 在线玩家视图（权威 UUID）→ IdentityStore.by_player(server_id, uuid)`。
拿不到权威 UUID（玩家不在线 / 感知未就绪）时**不建立任何关联**。
**没有**修改 Node 事件 payload（不需要，也不扩大协议）。

## §3 实际缺口（有证据，逐条）

| # | 缺口 | 证据 |
| --- | --- | --- |
| G1 | 游戏内回合把**显示名当作用户 ID** → 关系/连续性按名字分叉 | `chat_bridge.py:_reply` 传 `user_id=username`；`CharacterRuntime.respond` 用 `record_interaction(user_id)` |
| G2 | 已核验玩家的**游戏内记忆取不到** | `_memory_platform("minecraft:...")` 返回 `minecraft_chat`，而绑定以 `platform="qq"` 建立 → `link_for` 返回 None → `player_uuid=""` |
| G3 | 身份解析结论**不可审计**：没有任何结构化输出说明「这一轮是谁、解析成什么」 | 仓库里没有 resolution/channel/reason 字段 |
| G4 | 未绑定/撤销/冲突/服务器不匹配**没有统一语义**，无法证明 fail-closed | 只有 `verified()`（返回 None）一种表达 |
| G5 | 身份事实**没有最小化注入**：模型不知道自己面对的是不是同一个人 | Prompt 里没有身份层 |

**与理论风险的区分**：`verified()` 只返回「该服务器上 VERIFIED 的行」，因此**不会**跨服务器命中；
冲突（同一 UUID 第二次绑定）由既有 `_record_conflict` 留档；这些是既有能力，本阶段不重做，只做**显式化**。

## §4 身份 / 信任契约（实现）

### §4.1 解析结论（有限枚举）与稳定原因码

`IdentityResolution`：`VERIFIED` / `UNBOUND` / `REVOKED` / `CONFLICT` / `SERVER_MISMATCH` / `DEGRADED`。

| 结论 | 触发条件 | 原因码示例 |
| --- | --- | --- |
| `VERIFIED` | `by_player`（MC）/ `verified`（QQ）拿到**该服务器上** VERIFIED 的行 | `verified_link` |
| `UNBOUND` | 无任何绑定；或 MC 侧**拿不到权威 UUID**；或服务器身份未知 | `no_verified_link` / `no_authoritative_uuid` / `server_identity_unavailable` |
| `REVOKED` | 有历史行且状态为 REVOKED（两侧一致） | `link_revoked` |
| `CONFLICT` | 有冲突留档行 | `conflict_record` |
| `SERVER_MISMATCH` | 有生效绑定但**不在当前服务器**（或当前服务器身份不可用）→ 绝不跨服复用 | `server_identity_mismatch` |
| `DEGRADED` | 无身份层 / 读取期间存储报错 / 解析抛异常 | `identity_layer_absent` / `identity_store_error` / `resolve_error` |

### §4.2 稳定人物键与记忆键

* **关系/连续性键** = `VERIFIED` 时**对手平台 user_id**（与 QQ 侧 `record_interaction(str(user_id))` 同键）→
  同一个人在 QQ 与游戏内**共用同一条关系**（`RelationshipManager` 用 `str(user_id)` 归一，两条入口必然同键）；
  未核验时 = 调用方原本的 `user_id`（**行为不变**，绝不按名字合并）。
* **记忆检索键** = `VERIFIED` 时 `(link.platform, link.user_id)` → 游戏内说话也能取到「这个人在 QQ 这边」的既有记忆；
  未核验时 = 本地键（与 Phase 8A 完全一致）。

### §4.3 最小只读事实注入（Prompt）

只有 `VERIFIED` / `REVOKED` / `CONFLICT` / `SERVER_MISMATCH` / `DEGRADED` 会各说**一句**；
`UNBOUND` **什么都不加**（绝大多数回合如此，保持 Prompt 不变）。块内明确写出：
「游戏内玩家 X 与你在 QQ 上认识的是同一个已核验的人；身份核验**不等于**动作授权」。
事实里**没有**完整 UUID、**没有** QQ 号、**没有**绑定备注。

## §5 变更文件（逐个）

```text
app/integrations/minecraft/identity_context.py   新增（267 行）：IdentityResolution / IdentityChannel /
                                                 channel_of / IdentityContext（facts/log_fields/memory_identity）
                                                 / IdentityResolver（只读，fail-closed，永不抛）
app/character/runtime.py                         respond() 先解析身份；关系与认知检索改用稳定人物键；
                                                 _identity_context() 助手（结构化日志，永不抛）；
                                                 _minecraft_memory_context(identity=...) 用对手平台身份键
app/character/context.py                         build(identity=...) —— 关系层前插入只读身份事实块 + trace
app/core/bot.py                                  装配 identity_resolver（bridge_getter 延迟取 minecraft_memory）
tests/test_phase8b_identity_context.py           新增 19 项（解析矩阵 / 连续性隔离 / 信任分层）
docs/MINECRAFT_PHASE8B.md / CHANGELOG.md / docs/README.md
scripts/identity_context_smoke_real.py           真实 Java 门禁（本地专用，**不发布**）
```

**未改动**：`identity.py`（既有 Store 一字未改）、`memory_bridge.py` 的既有身份 API、
`MinecraftAgentBridge` / `ToolPolicy` / `confirmation.py` / `app/tools/*` / `app/tasks/*`、
Node runtime 与事件 payload、任何配置默认值。

## §6 测试矩阵（19 项，全部确定性：真实 SQLite，无 sleep、无 LLM、无真实服务器）

| 组 | 用例 |
| --- | --- |
| A 解析矩阵 | QQ VERIFIED→canonical；MC 经权威 UUID→同一人物键；无权威 UUID→不绑；同名不同 UUID 不合并；改名不换人；UUID 大小写/连字符归一；REVOKED（两侧）；CONFLICT 不影响原绑定；SERVER_MISMATCH 不跨服；存储报错→DEGRADED；解析抛异常→DEGRADED；无身份层→DEGRADED；只读且稳定（不写库/不调 Service/重复一致）；通道识别只看运输层事实 |
| B 连续性与隔离 | 已核验玩家：游戏内消息计入**同一条** QQ 关系（一条消息只计一次，`interaction_count == 1`、`turns == 1`），显示名不再单独建关系；未核验：保持本地键、QQ 侧零变动 |
| C 信任分层 | 已核验 + 不在 `trusted_players` → LOW 动作**零世界动作**且被既有信任门拒绝；SAFE 只读查询照常；身份模块源码里不出现策略/动作/桥接入口（结构性证明） |

## §7 门禁记录（实际执行）

| 命令 | 结果 |
| --- | --- |
| `pytest tests/test_phase8b_identity_context.py -q` | **19 passed** in 10.31s（exit 0） |
| `pytest tests -q`（全量） | **3898 passed** in 700.53s（exit 0；= 基线 3879 + 19） |
| `ruff check .` | All checks passed（exit 0） |
| `ruff format --check .` | 671 files already formatted（exit 0） |
| `mypy app` | Success: no issues found in **337** source files（exit 0） |
| 迁移演练 `scripts/migrate_db.py --db <副本> --json` | `version [36,36]`、`integrity [ok,ok]`、`tables [83,83]`、`added_tables []`、`lost_rows {}`、`ok:false`（= 已最新版的预期 no-op）→ **无新表无新迁移** |
| live DB 核对 | `max_version 36`、84 张表（83 业务 + `sqlite_sequence`）、`quick_check ok`、外键违规 **0** |
| WebUI / `minecraft_runtime` npm | **未执行（本阶段未改 WebUI/API，也未改事件 payload 或 Node runtime）**；CI 会照常全跑 |

### §7.1 CI（GitHub Actions）

| 项 | 值 |
| --- | --- |
| run ID | **38332372539** |
| URL | https://github.com/rinsoraa/CatooBot/actions/runs/38332372539 |
| attempt | **1**（一次通过，无 flake、无重跑） |
| `lint · format · types · tests` | completed / **success** |
| `webui · typecheck · tests · build` | completed / **success** |
| CI 上 pytest | **3895 passed, 3 skipped in 307.54s**（= 本地 3898 − 3 条既有平台条件性 skip；新增 19 项**无一被跳过**） |
| CI 上 Browser E2E | **7 passed (16.9s)** |
| CI 上 `minecraft_runtime` | dig-capability-test ALL CHECKS PASSED；find-blocks-test ALL CHECKS PASSED；e2e `bridge auth boundary + runtime instance ✓`；e2e ALL CHECKS PASSED |
| CI 上 ruff format / mypy | 653 files already formatted（CI 只见镜像内文件）；Success: no issues found in **337** source files |
| 实际 SKIPPED（全部为既有假服务器限制，与本阶段无关） | equip / inventory_move STOP；container STOP；craft 成功路径；craft STOP；3×3 craft 成功路径；dropped_items / pickup 成功路径 |

## §8 真实 Java 服务器门禁（`scripts/identity_context_smoke_real.py`，本地专用）

**前置**：`scripts/backup_db.py` 备份 live DB（`catoobot-20261013-092342.db`，v36 / 83 表 / 89457 行 / integrity ok）→
停 CatooBot（`scripts/stop_catoobot.ps1`）→ `PYTHONIOENCODING=utf-8`。门禁用**临时 SQLite**，不写 live DB。

真实环境：连接 `ONLINE`、runtime 实例 `rt_mv601mql_dc204829`、服务器身份 `mc-5a7d5e6316467e0f`；
**真实第二个 Mineflayer 客户端** `IdSmoke8B` 上线（权威 UUID 尾号 `f3c5`，由运行时在线玩家视图给出）。

| 段落 | 结果 |
| --- | --- |
| 1 未绑定 | `UNBOUND(no_verified_link)`；关系键=本地玩家名；不注入事实；闲聊回合**零工具/世界动作**；关系记本地 1 次、QQ 侧 0 次 |
| 2 真实 UUID → VERIFIED | 真实 SQLite 写入 VERIFIED；解析 `VERIFIED(verified_link)`；**关系键 = QQ 侧同一条**；UUID 尾号来自真实运行时；`permission_effect=none`；事实里无 QQ 号/完整 UUID 且写明「核验不等于动作授权」；**零额外世界动作**；游戏内消息计入同一条关系；显示名关系不再增长 |
| 3 解绑 → REVOKED | 真实 SQLite 留下 REVOKED 行；两侧都解析为 `REVOKED(link_revoked)`；关系键回到本地；历史保留（本地关系 1→2 次）；零额外世界动作 |
| 4 无权威 UUID | 陌生名字 → `UNBOUND(no_authoritative_uuid)`；不注入事实；**没有凭空建立任何跨平台人物引用** |
| 5 信任分层 | 对照条件成立（本轮确实 VERIFIED、访客不在 `trusted_players`）；LOW 动作走了工具路径但被既有信任门拒绝 `minecraft.user_not_trusted` |
| 6 真实 QQ 发信端 | **SKIPPED**：没有可控的真实 QQ 发送端（无法凭空以用户身份发 QQ 消息），**不编造 PASS**；跨平台证据由真实 SQLite + 真实 runtime 提供 |

**结论：REAL SERVER: PASS**（首次成功运行即通过；首次运行失败是脚本 bug——`_free_port` 引用了错误的模块，已修，非产品缺陷）。

## §9 安全不变量核对（逐条）

| 不变量 | 结论 | 证据 |
| --- | --- | --- |
| `ACTION_RISK` 不变 | ✅ | `app/integrations/minecraft/agent.py` **零改动** |
| 19 工具不变 | ✅ | `app/tools/builtins/*` 零改动 |
| `allow_medium` 默认不变 | ✅ | `app/config/settings.py` 零改动；真机门禁显式断言运行时为 False |
| 两道确认门不变 | ✅ | `confirmation.py` 零改动；测试断言 SAFE 查询照常、身份不绕过 |
| `TaskAuthorization` / `TaskStepAuthorization` / Policy 不变 | ✅ | `app/tasks/*`、`app/tools/policy.py` 零改动 |
| `MinecraftAgentBridge` / Service / Action Runtime 不被绕过 | ✅ | 身份模块源码里不出现这些入口（有结构性测试）；真实门禁里 LOW 仍走原链路并被拒 |
| `TurnOrigin` 仍只由可信来源派生 | ✅ | 身份解析**不写** `origin_metadata`（那是策略读的运输层事实） |
| 绑定不授予权限 | ✅ | `PERMISSION_EFFECT = "none"` 写死并断言；VERIFIED + 非可信 → 仍拒绝 |
| 无无界重试 / 无新动作 | ✅ | 只读解析，无重试、无新增 action |

## §10 隐私与数据库

* 给模型的**只有一句话**，不含完整 UUID、QQ 号、绑定备注、凭据；日志只记稳定原因码与枚举（`log_fields()`）。
* 不记录聊天原文/Prompt/思维链（沿用既有限制）。
* **无新表、无新迁移**；不改历史数据；真机门禁使用临时 SQLite（live DB 只做了备份）。
* live DB 中 `minecraft_identity_links` 的既有行（`VERIFIED 1 / CONFLICT 1`）为**历史遗留**，本阶段未新增。

## §11 已知限制与未完成项（只列有证据的）

1. **昵称→权威 UUID 依赖在线状态**：玩家不在线/感知未就绪时只能 `UNBOUND`（这是刻意的 fail-closed；
   代价是「离线时无法续接跨平台上下文」）。
2. **QQ 实际消息场景未做真机验证**（无可控真实 QQ 发送端）；QQ 侧逻辑由真实 SQLite + 真实 Store 的单测覆盖。
3. `IdentityContext` 的 V1 只带身份/信任事实，**不**做「跨通道历史拼接」；通道原始历史仍按既有 session 隔离。
4. 旧数据冲突（历史上以 MC 玩家名建过的关系行）**不自动合并**：留给显式迁移设计，本阶段不迁移、不删除。
5. 关系键的跨平台复用只发生在「已核验」时；未核验的既有关系行保持不变（可能长期并存两行，属预期）。

## §12 裁决

**PASS**：任务书 §十二 七条 DoD 逐条满足 ——

① 真实证据下同一已验证用户在 QQ 与 MC chat 解析为同一 canonical 主体；改名不换人、同名不同 UUID 不合并、跨服不误命中（§8 段落 2 + §6 A）；
② 允许共享的身份/关系上下文连续可用（同一条关系行），但通道原始历史不混合（§4.2 + §6 B）；
③ missing/revoked/conflict/mismatch/degraded 一律 fail-closed 为未知身份、不靠猜测恢复（§8 段落 1/3/4 + §6 A）；
④ IdentityLink / 关系阶段 / Memory 与 `trusted_players` / confirmation / TaskAuthorization **彼此分离**（§9）；
⑤ 未新增 action/tool、未扩张 risk map、未绕过 Action Runtime、**无 DB schema 变化**（§7/§9）；
⑥ 新增 19 项确定性回归测试，本地门禁与真实 Java 门禁逐项诚实报告；QQ 场景如实 SKIPPED（§7/§8）；
⑦ 文档/CHANGELOG/索引更新，镜像与 `origin/main` 一致、工作区干净、无密钥/私有 Bible/运行数据入库（见发布记录）。

**范围声明**：本阶段是**身份解析与上下文连续性**，不是模型训练，也不是「记住了就有权限」——
身份核验与动作授权始终是两件事。

## §13 Follow-up 修正（Phase 8B Follow-up — Identity Store Degradation Classification Fix）

**基线 `7395553c14d0871e735422312543b3ba7e24c83d`**（Phase 8B 最终发布）。本修正**只**修降级诊断语义，不重写身份系统、不重开其他已验收阶段。

### §13.1 故障根因（实测，不是推断）

`IdentityResolver._resolve()` 用 `degraded_reason` 的**前后变化**判断本次读取是否失败；
而 `IdentityStore` 捕获读取异常后写入的是**异常类型名** —— 同类异常连续发生时字符串**不变**，
于是「读取失败」被误判成「没有绑定」，返回 `UNBOUND` 而不是 `DEGRADED`。

同一故障注入（连续两次 `sqlite3.OperationalError`）下两种判据的实测对比：

| 判据 | read#1 | read#2 | 结论 |
| --- | --- | --- | --- |
| 旧（`degraded_reason` 前后比较） | `before=after='OperationalError'` → 认为本次未失败 | 同 | **误判 UNBOUND** |
| 新（逐次读取结果） | `ok=False error='OperationalError'` | `ok=False error='OperationalError'` | **DEGRADED** |
| 恢复后 | `ok=True error=''` | — | 按真实数据正常解析 |

### §13.2 实现（最小改动、单一身份存储源）

* `app/integrations/minecraft/identity.py`（**纯增量**）：新增 `LinkRead` / `LinksRead`（`ok` / `error` 语义）与
  `verified_read` / `by_player_read` / `links_for_read` / `all_links_read` —— **每次读取独立返回成功/失败**，
  **没有任何共享可变状态**，因此并发解析天然互不干扰（不需要清零全局标记，也就没有竞态）。
  旧方法（`verified` / `by_player` / `links_for` / `all_links`）保留并委托给新方法，**返回值语义与行为完全不变**。
* `app/integrations/minecraft/identity_context.py`：降级判定改为「本次解析中**任一**读取返回 error → `DEGRADED`（`identity_store_error`）」，
  删除 `degraded_reason` 变化判据；解析器优先使用 `*_read` 接口，对没有该接口的旧替身回退到「旧方法 + 前后比较」（兼容路径，仅测试替身会走到）。
* **Store API 变化：纯增量**（新增 4 个方法 + 2 个结果类型；旧签名与返回值不变，兼容既有调用方）。
* **数据库 schema：无变化**（无新表、无新迁移、不改真实运行数据）。

### §13.3 新增回归用例（7 项，全部确定性；真实 SQLite + 可控故障 DB，无 sleep、无 LLM）

| 用例 | 断言 |
| --- | --- |
| 首次读取失败 | `DEGRADED(identity_store_error)`，`degraded_reason=OperationalError` |
| **同类异常连续两次** | **两次都是 `DEGRADED`**（旧判据会漏掉第二次） |
| 已有历史 `degraded_reason` 时再次同类错误 | 当前解析仍为 `DEGRADED` |
| 存储恢复 | 后续查询按真实数据返回 `VERIFIED`（已绑定）与 `UNBOUND`（未绑定） |
| Minecraft 侧同一契约 | 连续两次 `DEGRADED`；恢复后 `VERIFIED` 且关系键=QQ 侧同一条 |
| 并发隔离 | 定向故障（只让失败请求的查询抛）+ 门闩：一个 `DEGRADED`、另一个 `VERIFIED`，互不污染 |
| 身份层异常不影响聊天 | 真实聊天栈：仍产生 1 个回合并真的把回复发进游戏 |

整套测试：`pytest tests/test_phase8b_identity_context.py -q` → **26 passed**（19 + 7）。

### §13.4 门禁与状态（见 §7 / §7.1 的完整记录）

* 相关套件（身份 / 记忆 / QQ 入口 / MC 聊天桥 / 角色 / 8A 路由 / 工具 / 确认门）**313 passed**。
* 全量 `pytest tests -q`、`ruff check .`、`ruff format --check .`、`mypy app` 与 CI 结论见发布记录（§7 之下的更新）。
* 安全不变量：`ACTION_RISK`、工具数量、`allow_medium` 默认值、`trusted_players`、`TurnOrigin` 派生、两道确认门、
  `TaskAuthorization`/`TaskStepAuthorization`/Policy/AgentBridge/Service/ActionRuntime **全部未改**；
  `DEGRADED` 依旧只代表「身份上下文不可用」，**不授予也不改变任何 Minecraft 权限**；无新动作、无无界重试；不记录原文/完整 UUID/QQ 号/密钥/思维链。

### §13.5 CI（GitHub Actions，含诚实记录的 flake）

| 项 | 值 |
| --- | --- |
| run ID | **38336177149**（URL https://github.com/rinsoraa/CatooBot/actions/runs/38336177149） |
| attempt 1 | `webui` **success**；`lint · format · types · tests` **failure** |
| attempt 1 失败步骤 | 仅 **Minecraft runtime tests（ActionRuntime 单测 + E2E）**：`[e2e] FAILED: Error: timeout waiting for: observer appears in snapshot players` —— 属**已知的环境敏感 e2e flake**（实体包/区块加载滞后，见 `minecraft_runtime/test/e2e.js:110` 注释的同族问题） |
| attempt 1 的对照证据 | 同一次运行里 **pytest 3902 passed, 3 skipped in 241.21s**（= 本地 3905 − 3 条既有平台条件性 skip，**新增 7 项无一被跳过**）、Browser E2E **7 passed**、ActionRuntime 单测全绿 |
| 处置 | **只重跑失败 job**（`POST …/rerun-failed-jobs`）；**未修改任何断言、超时或阈值** |
| attempt 2 | `lint · format · types · tests` **success**、`webui · typecheck · tests · build` **success** → run 结论 **success** |