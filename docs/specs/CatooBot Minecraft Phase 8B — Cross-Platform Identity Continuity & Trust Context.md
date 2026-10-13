# CatooBot Minecraft Phase 8B — Cross-Platform Identity Continuity & Trust Context

**中文标题：跨平台身份连续性与分层信任上下文**  
**阶段性质：** 集成现有身份桥与角色对话上下文；不新增 Minecraft 玩法能力，不改变任何动作授权语义。  
**目标：** 已完成明确验证的同一用户，在 QQ 与 Minecraft 游戏内聊天之间拥有一致、可审计的“是谁”与角色关系上下文；同时严格区分“身份已验证”“角色关系亲近”和“获准驱动游戏动作”这三件事。

> **执行前置门禁（必须先完成，独立提交，不得混入 8B）：** Phase 8A 当前提交 `8c4efdc578b8affac3cc8e3477e411c54a216184` 存在一个可从源码直接复核的契约不一致：`ToolOrchestrator.run()` 在候选非空时先设置 `route_target=MINECRAFT_TOOL`，模型未产生工具调用时只设置 `reason_code=model_no_tool_call`，没有把最终目标改回 `CHAT_ONLY`。现有测试只检查原因码，没有检查目标。先单独修复该问题并添加回归断言、跑相应测试与 CI；记录新 SHA 后停止并提交简短验收报告。**不得把这项 8A 修正打包进 Phase 8B commit。** Phase 8B 的正式 base SHA 应为该修正被接受后的 `main` HEAD，而不是预先写死的 SHA。

---

## §1. 基线核验与工作纪律

1. 工作开始时读取最新 `origin/main` SHA、工作区状态、最近阶段文档与 `CHANGELOG.md`；不要假定仓库停留在任务书编写时的 SHA。
2. 权威开发目录：`E:\WorkSpace ZCode\CatooBot`。发布镜像：`E:\WorkSpace ZCode\CatooBot_github`。按当前项目既定双目录发布流程操作；不要把含本地数据/凭据的工作区整体推到 GitHub。
3. 禁止读取、打印、提交或改写 `.env`、真实 `config/config.yaml`、`data/`、身份凭据、QQ/NapCat token、服务器登录数据、角色 Bible 私有源文件。需要验证配置时，只输出脱敏的布尔值/枚举与计数。
4. 不回头重审与 8B 无关的 Phase 7F 生命周期议题；只有本阶段实际复现的缺陷才列为阻塞项。不得因理论风险无限重开已验收阶段。

## §2. 必须先阅读的现有实现与文档

至少检查以下源码/文档，再决定改动点：

- `app/integrations/minecraft/identity.py`：`IdentityStore`、`IdentityLink`、`MinecraftIdentity`、`server_identity()`、`canonical_uuid()`、`verified()`、`by_player()`、`links_for()`、`revoke()`。
- `app/integrations/minecraft/memory_bridge.py`：`self.identities`、`server_id()`、`online_players()`、`player_named()`、`link_for()`、`bind_configured()`、既有记忆/关系上下文。
- `app/integrations/minecraft/identity_commands.py`：显式绑定、二次确认、撤销与身份冲突行为。
- `app/integrations/minecraft/chat_bridge.py`：`_session_id()`、`_reply()`、`origin_metadata` 与玩家身份来源；检查实际 `minecraft.chat` event 是否提供可靠 UUID，不能预先假定有。
- `minecraft_runtime/runtime.js` 及对应 Node bridge/event 测试：实际事件中 username/UUID 的权威来源。
- `app/character/runtime.py`：`respond()`、`_tool_context()`、关系写入、记忆检索、上下文构建，以及由 `TurnOrigin` 派生的意图键。
- `app/character/relationship.py`、`app/tasks/qq_entry.py`、`app/integrations/minecraft/agent.py`、`app/integrations/minecraft/confirmation.py`、`app/tasks/runtime.py`：现有关系、来源与授权边界。
- `docs/MINECRAFT_PHASE_5C.md`、`docs/MINECRAFT_PHASE_4A.md`、`docs/MINECRAFT_PHASE_7C.md`，以及最新相关 follow-up。

**重要已知事实：** 仓库已经有 Phase 5C 身份桥、`minecraft_identity_links` 表和绑定命令。真正的 `IdentityStore` 经 `bot.minecraft_memory.identities` 暴露；`bot.minecraft_identity` 是命令处理器，不可误当作 Store。Phase 7C 也已经把 VERIFIED IdentityLink 用于特定任务目标解析。因此，8B 不是“重新发明绑定系统”，而是审计尚未打通的对话/关系上下文连接，并复用既有权威来源。

## §3. 目标与非目标

### 3.1 目标

- 在明确验证的身份关系存在时，让 QQ 用户与同一 Minecraft 玩家对应到一致的 canonical person identity。
- 让 CharacterRuntime 在对话/关系上下文中收到结构化、最小化、可审计的身份与信任事实，而不是只把 Minecraft username 当作用户 ID。
- 复用现有 QQ 稳定 user ID、Minecraft `player_uuid`、`server_id` 与 `IdentityStore`。服务器身份和 UUID 都必须来自既有可信运行时/存储记录。
- 保持跨平台身份连续性与每个平台的对话会话隔离：共用允许共享的身份/关系/明确记忆，不自动拼接 QQ 私聊、QQ群和游戏内聊天原文。
- 身份系统异常、未绑定、冲突、撤销或服务器不匹配时，安全降级到未知身份；普通聊天继续运行，不能凭昵称猜测。

### 3.2 明确非目标

- 不新增 Minecraft 工具、Action Runtime action、规划器、批量采集/树木采伐/合成/交付能力。
- 不更改 `ACTION_RISK`、`allow_low`、`allow_medium` 默认值、`trusted_players`、确认门、TaskAuthorization、TaskStepAuthorization、TurnOrigin 派生规则或 Action Runtime 生命周期。
- 不允许 VERIFIED IdentityLink、核心好友关系、角色好感、长期记忆、LLM 判断或用户昵称自动授予 Minecraft 动作权限。
- 不按昵称相等、MC username、QQ 昵称、群名片或模型推测自动绑定身份。
- 默认不新增数据库表/迁移，不重写记忆存储，不跨平台合并历史消息，不迁移或删除旧关系行。

## §4. 统一身份与信任上下文契约

实现前先把源码中已经存在的概念整理成明确结构。字段名可沿用仓库风格，但语义必须满足：

1. **canonical identity（“是谁”）**：只有通过 `IdentityStore` 解析出的 `VERIFIED`、当前 `server_id` 匹配、canonical UUID 匹配的绑定，才允许建立 QQ ↔ Minecraft 的确定关联。
2. **channel/session（“在哪说话”）**：QQ 私聊、QQ群与 Minecraft 游戏内聊天的 session/history 仍各自独立。不能仅因身份相同，就把完整历史或原始消息拼到另一个通道。
3. **relationship（“角色与此人关系如何”）**：沿用已有 `RelationshipManager` 与显式核心好友配置语义。只有可靠身份解析成功时，才可将新回合映射到已有稳定用户身份；不能对旧记录自动做模糊合并。需对现有 `record_interaction()` 调用点作最小改动并保证一条入站消息只计数一次。
4. **action trust（“能否驱动 Minecraft 动作”）**：必须由原有 Policy / `trusted_players` / TurnOrigin / confirmation / task authorization 独立决定。身份上下文只能被策略作为输入证据读取（若现有策略明确支持），不能绕过现有 gate。**绑定成功但玩家未在 `trusted_players` 中时，LOW 动作仍必须按原规则拒绝。**
5. **degraded/unknown（“无法可靠确认”）**：无绑定、REVOKED、CONFLICT、服务器不匹配、UUID 缺失、身份存储异常，都必须显式表示为非 VERIFIED/unknown；不得退回到名字猜测。
6. **LLM 可见性**：若身份/关系信息需要进入模型上下文，只注入最小、只读、无权限效果的事实；不要给模型传递完整 UUID、QQ 敏感标识、绑定备注、确认凭据或内部授权令牌。不要把完整 identity link 行原样序列化进 Prompt。

建议的概念模型（可按现有架构调整，不要求机械创建新类）：

```text
IdentityContext
  resolution: VERIFIED | UNBOUND | REVOKED | CONFLICT | SERVER_MISMATCH | DEGRADED
  canonical_person_ref: 内部稳定引用（不得直接暴露给 LLM/普通日志）
  player_uuid_suffix: 可选，仅用于确认/诊断显示
  server_id: 内部服务端身份
  channel: qq_private | qq_group | minecraft_chat | webui | ...
  relationship_stage: 现有关系阶段（如果可靠取得）
  action_trust: 只读状态/来源摘要，不作为授权凭据
```

不要仅为符合上面示例而另造第二套 `IdentityStore` 或第二套权限表。

## §5. 真实入站身份解析规则

### 5.1 QQ 入口

- 使用现有 QQ 事件中的稳定 QQ user ID，不使用昵称作为 identity key。
- 通过当前服务器对应的 `IdentityStore.verified(platform="qq", user_id=..., server_id=...)` 或等价的现有接口解析。
- 如果当前 Minecraft server identity 不可用，不能跨服务器复用上一台服务器的绑定；应降级为 QQ 本地身份上下文。

### 5.2 Minecraft 入口

- 查清 `minecraft.chat` 事件能否提供玩家 UUID。如果 Node Runtime 能可靠提供 UUID，沿用该来源；如果事件只提供 username，必须审计现有 `world_view/online_players` 能否通过实际在线玩家记录唯一匹配 UUID。
- 只有从当前连接服务器真实运行时取得的 canonical UUID 才能用于 `IdentityStore.by_player(server_id, player_uuid)`。如果只能按 username 找到玩家但拿不到权威 UUID，应标记 unknown，而不是绑定。
- 若 `by_player()` 返回 VERIFIED 且 `server_id` 与当前服务器一致，可得出跨平台身份关联；REVOKED/CONFLICT/缺失/读取异常一律不可建立关联。
- 不得直接把 `username` 改写成 QQ ID；先通过 UUID → verified link → QQ user ID 完成解析。
- 如确需修改 Node 事件 payload，只新增最小必需字段与协议测试；不得改变动作协议/鉴权。

### 5.3 昵称、改名与服务器边界

- Minecraft username 只作显示名；相同 UUID 改名后 canonical identity 不变。
- 同用户名但不同 UUID 必须是不同身份。
- 服务器 A 的 VERIFIED link 不得在服务器 B 生效；server_id 必须匹配当前连接。
- 绑定撤销后，未来回合不得继续沿用旧跨平台关联；历史存储按既有保留策略处理，不回写伪造身份。

## §6. 对话连续性、关系与记忆边界

1. 先明确当前哪些信息来自 per-session conversation history、`RelationshipManager`、普通 Memory、Minecraft Memory 与 Sandbox context；画出现状数据流后再改。
2. 跨平台共享限于**身份解析后的、原本就允许跨会话共享的结构化上下文**，例如稳定身份、已有关系阶段、被明确记录的共同经历或偏好摘要。不得把另一通道的全部原始对话复制到当前 Prompt。
3. 对话 session ID 继续包含渠道/群/用户范围；不允许群聊历史进入私聊或游戏内上下文。
4. 若要让已绑定的 Minecraft 回合复用 QQ 对应的关系记录，应只使用 VERIFIED mapping 的稳定 QQ ID，并测试 `record_interaction` 一条消息只计一次。不要因为已有 MC username 关系行就自动合并/删除它；旧数据冲突留待显式迁移设计。
5. Memory bridge 的 Minecraft 世界事实/物品/位置等必须继续按它自己的 freshness/verification 规则处理；身份链接绝不能使过期世界事实变成可信事实。
6. 身份上下文检索失败只影响额外上下文，不阻断普通聊天、事件接收或世界感知；如果某项操作依赖可靠目标身份，则由既有 Policy/Task 逻辑 fail-closed。

## §7. 信任上下文与安全不变量

必须在代码、测试和最终报告中逐条证明：

- `IdentityLink.VERIFIED` 只证明“这两个平台身份已验证为同一主体”，不意味着“此人受 Minecraft 动作授权”。
- `trusted_players` 仍是游戏内玩家 LOW 动作的既有信任配置；除非当前架构已有显式、经过验证的策略契约，否则 8B 不得自动将已绑定玩家加入该配置。
- MEDIUM 及以上动作仍需原有确认门；不可因为 QQ 身份已绑定而静默确认。
- `TurnOrigin.USER/BACKGROUND/TASK` 仍只能由可信调用来源派生；用户输入、模型输出或可伪造 `origin_metadata` 不能更改它。
- `ACTION_RISK`、19 项现有 Minecraft 工具、Policy、ConfirmationStore、TaskAuthorization / TaskStepAuthorization、AgentBridge、Service 与 Action Runtime 不可被旁路。
- 身份记录/记忆只能作为上下文，绝不能作为 tool-call 指令或授权 token。
- 不记录聊天原文、Prompt、思维链、凭据、完整 UUID 或不必要的 QQ user ID。日志可以记录稳定原因码、channel、resolution 枚举、server 是否匹配、UUID 后四位（若必要）与 route/correlation ID。
- 身份模块不可用时必须显式记录 `DEGRADED/unknown` 等稳定状态；不能静默地改用名字猜测。

## §8. 建议的实现顺序

1. 写一份简短的源码调用链/数据流审计：QQ 入站、Minecraft 入站分别如何生成 session、user_id、关系记录、Memory query、ToolContext 与 action trust。
2. 找出实际缺口；确认已有 `IdentityStore` 未能覆盖的准确调用点。禁止基于报告猜实现状态。
3. 优先做**只读 resolver/context adapter**，复用 `bot.minecraft_memory.identities` 与现有 `MinecraftIdentity`。不要让 CharacterRuntime 自己再维护一份绑定缓存/Store。
4. 以最小改动把 resolved context 传入关系/记忆构建链；保留 session/history 隔离及原有对话语义。
5. 若事件缺少权威 UUID，先判断现有 runtime semantic player facts 是否足够；只有真实需要且可验证时才调整事件 payload。
6. 增加测试，先跑身份/记忆/QQ/MC chat/Action Policy 的相关单测，再跑全量门禁。
7. 仅在有真实、本阶段相关的失败证据时修复额外问题；发现与 8B 无关的问题单独记录，不扩大范围。

## §9. 必须新增/更新的自动化测试

### A. 身份解析矩阵

- 同服务器 VERIFIED QQ ↔ Minecraft UUID 成功解析。
- REVOKED、缺少绑定、CONFLICT、identity store 故障分别返回独立且安全的状态。
- 服务器不匹配 / 当前 server_id 为空时不跨服命中。
- 同 username、不同 UUID 不合并；同 UUID、username 改名仍是同一身份。
- canonical UUID 大小写/连字符规范化行为正确。
- 当前 MC 入站只有 username、没有权威 UUID 时不能凭名字合并。

### B. 跨平台连续性与隔离

- 绑定后，QQ 与 Minecraft 对同一主体解析到同一个 canonical reference。
- 未绑定/撤销时两端作为独立身份处理，不出现误合并。
- QQ 私聊、QQ群、Minecraft chat session/history 仍隔离；通道 A 的 raw message 不会出现在通道 B 的 context/prompt。
- VERIFIED 关联可让现有的关系阶段/被允许共享的结构化身份上下文保持一致；每条互动仅计一次。
- IdentityStore 读错误不会导致聊天失败；不得泄漏内部异常/数据库行。
- 绑定撤销后下一回合不再注入旧的 cross-platform identity context。

### C. 身份与授权严格分层

- VERIFIED IdentityLink + 未列入 `trusted_players` 的 Minecraft 玩家发起 LOW 动作：仍拒绝，且无 `action_id`。
- VERIFIED IdentityLink 不绕过 MEDIUM confirmation。
- 显式可信配置、USER/BACKGROUND 来源矩阵与现有 Phase 4A/3E 行为完全不变。
- 恶意 `origin_metadata`、昵称、玩家名、LLM 文本均不能伪造 UUID、verified link、TurnOrigin 或权限。
- 身份上下文读取路径不直接调用 Service/Action Runtime，不增加任何动作。

测试必须是确定性的；不得依靠任意 `sleep` 规避竞态。不要因为假 Java server 缺能力就放宽断言；按现有测试惯例把真正无法在 CI 构造的案例明确标为 skip，并说明原因。

## §10. 必须运行的质量门禁

- 相关测试：`tests/test_minecraft_identity.py`、身份命令测试、`tests/test_minecraft_memory_*.py`、QQ entry / chat integration / Minecraft chat bridge、CharacterRuntime、关系管理与现有 Policy/Confirmation tests。
- 全量 `pytest tests -q`。
- `ruff check .`。
- `ruff format --check .`。
- `mypy app`。
- WebUI `typecheck` / Vitest / build / E2E（如果改 WebUI/API 则必须全部跑）。
- `minecraft_runtime` npm tests（如果改事件 payload 或 Node runtime 则必须跑）。
- DB migration no-op 演练：如方案无 schema 变更，证明 migration version 与表集合不变、`integrity_check` 正常、行数无损。默认要求无新迁移。
- 真实 Java 服务器集成门禁：至少覆盖从真实 runtime 取得玩家 UUID → 已验证的身份 link → Minecraft chat 上下文的身份解析，并证明没有触发额外动作。若真实 QQ gateway 在当前环境不可用，QQ 实际消息场景必须明确列为 `SKIPPED`，不得伪称 PASS；仍要用真实 SQLite/真实 runtime 做可运行的跨平台 link 集成证据。

## §11. 文档、发布与隐私审计

- 新增 `docs/MINECRAFT_PHASE8B.md`，包括：基线 SHA、现状调用链、实际缺口证据、身份/信任契约、逐文件改动、测试结果、真实运行环境结果、已知限制与裁决。
- 更新 `docs/README.md` 阶段索引及 `CHANGELOG.md` 顶部条目。
- 将本任务书归档到 `docs/specs/`，遵守当前仓库文件命名规则。
- 逐项核查变更文件、删除行与 diff；重点确认无权限门绕过、无隐私扩散、无意外 migration、无私有配置或 Bible 上传。
- 只发布允许进入 GitHub 的源码/测试/公开文档。真机脚本如按项目约定属于本地专用 `scripts/`，不得塞入发布镜像。

## §12. Phase 8B 完成定义

必须同时满足：

1. 在真实证据支持的情况下，同一个已验证用户在 QQ 和 Minecraft chat 可以解析为同一 canonical identity；改名不改变身份，同名不同 UUID 不会合并，跨服务器不会误命中。
2. 已允许共享的身份/关系上下文可连续使用，但通道的原始聊天历史不混合。
3. missing / revoked / conflict / mismatch / degraded 均 fail-closed 为未知身份，不靠猜测恢复绑定；普通聊天安全降级。
4. IdentityLink、关系阶段、Memory 与 `trusted_players`/confirmation/TaskAuthorization 权限彼此分离；未授权 LOW/MEDIUM 动作仍被原有 gate 阻止。
5. 未新增 Minecraft action/tool、未扩张 risk map、未绕过 Action Runtime、默认无 DB schema 变化。
6. 新增确定性回归测试、所有必要 CI 门禁通过，真实环境 PASS/SKIPPED 逐项诚实报告。
7. 文档/CHANGELOG/索引更新，发布镜像与 `origin/main` SHA 一致，工作区干净，无密钥/私有 Bible/运行数据入库。

如果达到以上 DoD，裁定 **PASS** 并进入 Phase 8C；如果有阻塞项，只允许针对**实际失败证据**列出最小缺口，不得把已经通过的部分整体打回或引入无关架构重做。

## §13. DSH 最终报告格式

```text
CatooBot Minecraft Phase 8B — Final Report

1. Base SHA / final SHA / worktree / mirror status
2. Existing identity implementation reused (paths + functions)
3. Original gap and reproducible evidence (separate facts from hypotheses)
4. Final identity resolution path for QQ and Minecraft entries
5. Canonical identity / session isolation / relationship context contract
6. Trust-layer separation matrix (identity vs relationship vs action permission)
7. Changed files and rationale
8. Security invariants (ACTION_RISK, trusted_players, TurnOrigin, confirmation, task auth)
9. Test results and exact counts / exit codes
10. Real Java / real QQ result: PASS / FAIL / SKIPPED per scenario
11. Database / privacy / secret scanning evidence
12. Docs / changelog / release-mirror status
13. Verdict: PASS / PARTIAL / FAIL, with only evidence-backed remaining items
```

**不要宣称模型训练。** 此阶段建立的是身份解析和对话/关系上下文连续性，不是模型权重训练，也不是“记住了就能获得权限”。
