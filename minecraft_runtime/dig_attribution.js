'use strict'
/**
 * Phase 7D Follow-up —— 挖掘**结果归因**：把「世界发生了什么」与「是不是罐头亲手做的」拆开。
 *
 * ## 为什么需要它
 *
 * mineflayer 4.39.0 的 `digging.js` 用 ``blockUpdate:<目标坐标>`` 判定挖掘结束：
 *
 *   ```js
 *   function onBlockUpdate (oldBlock, newBlock) {
 *     if (newBlock?.type !== 0) return        // 只看"变成了 air"
 *     ...
 *     bot.emit('diggingCompleted', newBlock)  // 没有任何时间/归属校验
 *   }
 *   ```
 *
 * 于是**任何**让该坐标变成 air 的来源（别的玩家挖掉、`/setblock`、世界编辑、服务器插件）
 * 都会让 `bot.dig()` 正常 resolve —— 这就是 Phase 7D §10.4 的假成功根因。
 * 客户端拿不到"方块是被谁弄掉的"，所以本模块**不**试图证明过程，只做两件事：
 *
 *   1. 如实记录与本次动作**同一作用域**（同一 action_id / 同一坐标 / 同一时间窗）的原始证据；
 *   2. 用**纯函数**（`resolveAttribution`）给出两个**互相独立**的结论：
 *      * `world_effect`   —— 世界里发生了什么（BLOCK_REMOVED / BLOCK_REMAINS / UNKNOWN）
 *      * `attribution`    —— 现有证据把它归给谁
 *                           （SELF_CONFIRMED / SELF_INFERRED / EXTERNAL_INDICATED / AMBIGUOUS）
 *
 * ## 证据来源（全部带坐标过滤 + 时间窗过滤，绝不外溢到别的动作）
 *
 *  * `blockUpdate:<目标坐标>` —— 该坐标变成 air 的时刻（mineflayer 自己也在听同一个事件）；
 *    这条**既可能**来自服务器真包，**也可能**来自 mineflayer `finishDigging()` 里的
 *    本地乐观更新（``bot._updateBlockState(pos, 0)``）→ 单凭它不能区分"谁弄掉的"。
 *  * `bot._client` 的原始 **`block_update` 服务器包**（同坐标）—— 服务器亲口说该坐标变了；
 *    与上面那条分开记录（`server_block_update`），用来标注"服务器确认过"。
 *  * `blockBreakProgressObserved / blockBreakProgressEnd`（mineflayer 由服务器
 *    `block_break_animation` 包转出，**带实体身份**）—— 别的玩家在挖同一个坐标的**正面证据**；
 *    实体 id 等于罐头自己 → 记 `self_break_progress`（自己挖的正面证据）。
 *  * `diggingCompleted / diggingAborted`（同坐标）—— 罐头自己的挖掘生命周期走完了/被中断。
 *
 * ## 判定规则（保守优先，原因码见 `ATTRIBUTION_REASONS`）
 *
 *  1. 世界效果不是 BLOCK_REMOVED → AMBIGUOUS（没有"方块没了"这件事就谈不上归因）；
 *  2. 世界效果由**按到达顺序**裁决的服务端更新决定（见 `resolveServerUpdates`）：
 *     最后一条说 air → 移除；最后一条说非 air 且仍是原方块 → 还在；
 *     最后一条说非 air 但已换成另一个方块 → 原方块被替换（不归给任何人）；
 *     服务端先确认移除、之后又报非 air → 自相矛盾，判 UNKNOWN/AMBIGUOUS；
 *     只有本地乐观视图、没有权威确认 → UNKNOWN。
 *  3. 观察到**身份已可靠解析的别人**在该坐标的破坏进度 → EXTERNAL_INDICATED；
 *  4. 观察到**自己**在该坐标的破坏进度（直接执行者证据）→ SELF_CONFIRMED（严格自证）；
 *  5. 只有自身挖掘生命周期在**预期时长之后**走完（且无外部/未知信号）→ SELF_INFERRED（自挖推断）；
 *  6. 同时观察到别人的进度/未知身份的进度**并且**自己也在预期时刻挖完 → AMBIGUOUS（冲突，不站队）；
 *  7. 其余（没有完成证据、早于预期、无预期时长、坐标在开始前就变了、身份未知）→ AMBIGUOUS + 稳定原因码。
 *
 * `SELF_CONFIRMED` 与 `SELF_INFERRED` 必须分开：客户端协议里**没有**"这个方块是我破坏的"这种回执，
 * 第 5 条靠的是"我们自己发出完成包的时刻"这个**因果分界**，是**推断**而不是过程证明。
 * `strict_self_proof` 只为 `SELF_CONFIRMED` 置真；时序推断只产生 `SELF_INFERRED`。
 *
 * 本模块**不做**任何等待/睡眠/超时修改，**不**改动作结果的成功判据（方块真的变了才算成功），
 * **不**写世界，**不**申请新的风险级别或权限。
 */

const { Vec3 } = require('vec3')

/** 世界效果：世界里到底发生了什么（与"谁做的"无关）。 */
const WORLD_EFFECT = Object.freeze({
  /** 目标坐标上原来那个方块已经不在了（变成了 air 或别的方块） */
  BLOCK_REMOVED: 'BLOCK_REMOVED',
  /** 原来的方块还在原位 */
  BLOCK_REMAINS: 'BLOCK_REMAINS',
  /** 读不到（区块没加载 / 客户端没有该坐标的数据）—— 绝不猜 */
  UNKNOWN: 'UNKNOWN',
})

/**
 * 执行归属：现有证据把这次世界变化归给谁。刻意**没有**过强的 `EXTERNAL_CONFIRMED`。
 *
 * 7D.3 起自证明确分成两档：
 *  - `SELF_CONFIRMED`：有**直接执行者证据**（本客户端观察到**自己实体 id** 在目标坐标的破坏进度），
 *    且服务端确认了方块移除、没有未解决的冲突 —— 这是**严格**自证；
 *  - `SELF_INFERRED`：只有自身挖掘生命周期 + 时序推断（`diggingCompleted` 在预期时长之后、
 *    服务端确认移除、没有外部信号）—— 这是**推断**，不是过程证明。
 */
const ATTRIBUTION = Object.freeze({
  /** 严格自证：直接执行者证据 + 服务端移除确认（`strict_self_proof === true` 的唯一来源） */
  SELF_CONFIRMED: 'SELF_CONFIRMED',
  /** 自挖推断：自身挖掘生命周期按时序走完 + 服务端移除确认；是推断，不是严格证明 */
  SELF_INFERRED: 'SELF_INFERRED',
  /** 有正面证据表明**别人**（实体身份已可靠解析且确非自身）在挖同一个坐标 */
  EXTERNAL_INDICATED: 'EXTERNAL_INDICATED',
  /** 证据不足以归因（绝不倒向任何一边） */
  AMBIGUOUS: 'AMBIGUOUS',
})

/** 保证等级（payload 的 `assurance`）：与 `attribution` 一一对应，供上层按等级消费。 */
const ASSURANCE = Object.freeze({
  /** 严格亲自证据 */
  STRICT: 'STRICT',
  /** 自挖推断 */
  INFERRED: 'INFERRED',
  /** 外部迹象 */
  EXTERNAL: 'EXTERNAL',
  /** 无充分证据 */
  NONE: 'NONE',
})

/** 归因契约版本。2 = 引入 SELF_CONFIRMED / SELF_INFERRED 分级（1 为历史的过宽契约）。 */
const DIG_ATTRIBUTION_SCHEMA = 2

/** 自证依据（payload 的 `confirm_basis`）：正面进度 > 生命周期+时序推断。 */
const CONFIRM_BASIS = Object.freeze({
  SELF_BREAK_PROGRESS: 'self_break_progress',
  DIG_LIFECYCLE_TIMING: 'dig_lifecycle_timing',
  EXTERNAL_BREAK_PROGRESS: 'external_break_progress',
  NONE: 'none',
})

/** 稳定原因码（审计/断言用；不要改动既有字符串的含义）。 */
const ATTRIBUTION_REASONS = Object.freeze({
  SELF_PROGRESS_OBSERVED: 'self_break_progress_observed',
  SELF_COMPLETED_AT_EXPECTED: 'self_dig_completed_at_expected_time',
  EXTERNAL_PROGRESS_OBSERVED: 'external_break_progress_observed',
  CONFLICTING_EVIDENCE: 'conflicting_evidence',
  NO_BLOCK_CHANGE: 'no_block_change_observed',
  BLOCK_REMAINS: 'block_still_present',
  WORLD_EFFECT_UNKNOWN: 'world_effect_unknown',
  REMOVED_BEFORE_SELF_START: 'block_removed_before_self_dig_started',
  REMOVED_BEFORE_SELF_COMPLETION: 'block_removed_before_self_dig_completion',
  NO_SELF_COMPLETION: 'no_self_dig_completion_observed',
  EXPECTED_TIME_UNKNOWN: 'expected_dig_time_unknown',
  NO_TARGET_CHANGE: 'no_block_change_observed_at_target',
  //: 真机取证：只有本地乐观视图说方块没了，服务器没有确认过（例如出生点保护挡住了非 op 的挖掘）
  LOCAL_VIEW_ONLY: 'block_change_not_confirmed_by_server',
  //: 目标坐标被服务端改成了**另一个非 air 方块**（不是普通的挖空，不归给任何人）
  BLOCK_REPLACED: 'block_replaced_by_other_block',
  //: 目标坐标的服务端更新互相矛盾（先说没了、后又说还在），顺序无法收敛
  CONFLICTING_SERVER_UPDATES: 'conflicting_server_updates',
  //: 观察到目标坐标的破坏进度，但**无法解析**是谁（实体身份未知 / 自身 id 尚未可用）
  UNRESOLVED_BREAK_ACTOR: 'break_actor_unresolved',
})

/** 证据种类（`evidence[].kind`）。 */
const EVIDENCE_KINDS = Object.freeze({
  BLOCK_BECAME_AIR: 'block_became_air',
  SERVER_BLOCK_UPDATE: 'server_block_update',
  SELF_BREAK_PROGRESS: 'self_break_progress',
  SELF_BREAK_END: 'self_break_end',
  EXTERNAL_BREAK_PROGRESS: 'external_break_progress',
  EXTERNAL_BREAK_END: 'external_break_end',
  UNRESOLVED_BREAK_PROGRESS: 'unresolved_break_progress',
  UNRESOLVED_BREAK_END: 'unresolved_break_end',
  SELF_DIG_COMPLETED: 'self_dig_completed',
  SELF_DIG_ABORTED: 'self_dig_aborted',
})

/**
 * 自证所需的"离预期完成时刻有多近"下限。
 *
 * 真实的自行挖掘里，让 `bot.dig()` resolve 的那次坐标变化**要么**来自服务器对我们的
 * 完成包（status=2）的响应，**要么**来自 mineflayer 自己在 ``waitTime`` 上的本地乐观更新
 * —— 两者都在预期完成时刻**之后**（或紧贴着）。明显早于它的变化只可能是别的力量。
 * 0.85 给服务器 tick/延迟留了 15% 余量，同时把 Phase 7D §10.4 那种"挖到一半被外面弄没了"
 * （系数 0.78）如实判成歧义。
 */
const SELF_MIN_RATIO = 0.85

/** 证据条数上限：只留前 N 条（防止极长动画序列把 payload 撑爆） */
const MAX_EVIDENCE = 12

/** 服务器单方块变化包名（minecraft-protocol 的 `meta.name`，去掉 `packet_` 前缀后比较）。 */
const SERVER_BLOCK_PACKET_NAMES = new Set(['block_update', 'block_change'])

// ------------------------------------------------------------------ 小工具

function nowMs() {
  return Date.now()
}

function positionOf(value) {
  if (!value) return null
  const x = Number(value.x)
  const y = Number(value.y)
  const z = Number(value.z)
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return null
  return { x: Math.floor(x), y: Math.floor(y), z: Math.floor(z) }
}

function samePosition(left, right) {
  return Boolean(left) && Boolean(right) && left.x === right.x && left.y === right.y && left.z === right.z
}

function blockPosition(block) {
  return block ? positionOf(block.position) : null
}

/** `blockUpdate:<Vec3>` 这个事件名必须与 mineflayer 用**同一个** Vec3 拼出来。 */
function blockUpdateEventName(target) {
  return `blockUpdate:${new Vec3(target.x, target.y, target.z)}`
}

function entityIdOf(entity) {
  if (!entity || entity.id === undefined || entity.id === null) return null
  return entity.id
}

function entityNameOf(entity) {
  if (!entity) return null
  const username = entity.username || entity.name
  return typeof username === 'string' && username ? username : null
}

/**
 * 按**到达顺序**裁决目标坐标的服务端方块更新（7D.3 §2.4）。
 *
 * 旧的 `some(to_air)` / `first(to_air)` 会在"先说没了、后又说还在"这类自相矛盾的序列上
 * 产生不收敛的结论。这里只认**最后一条**服务端更新，并显式区分：
 *
 *  - `removed`         —— 最后一条说变成 air → 权威确认移除；
 *  - `present_same`    —— 最后一条说非 air，且本地读数仍是**原方块** → 原方块还在；
 *  - `replaced`        —— 最后一条说非 air，但本地读数已是**另一个方块** → 原方块被替换；
 *  - `conflict`        —— 先被确认移除，之后又被服务端报成非 air（互相矛盾，无法收敛）；
 *  - `present_unknown` —— 最后一条说非 air，但读不到本地方块名，无法区分"还在"与"被替换"；
 *  - `none`            —— 根本没有服务端更新（只有本地乐观视图）。
 *
 * `type != 0` 只说明"不是 air"，**不能**单独证明原方块仍然存在。
 */
function resolveServerUpdates(updates, blockBefore, blockAfter) {
  const list = (Array.isArray(updates) ? updates : []).filter(
    (item) => item && (item.to_air === true || item.to_air === false),
  )
  if (list.length === 0) {
    return { authoritative: false, kind: 'none', conflict: false, replaced: false }
  }
  const latest = list[list.length - 1]
  if (latest.to_air === true) {
    return { authoritative: true, kind: 'removed', conflict: false, replaced: false }
  }
  // 最后一条说"非 air"
  if (list.slice(0, -1).some((item) => item.to_air === true)) {
    return { authoritative: true, kind: 'conflict', conflict: true, replaced: false }
  }
  // 优先用**这条服务端更新自己解析出的**方块名（协议 `type` → 方块名），
  // 退化到调用方给出的本地读数。`type != 0` 只能说明"不是 air"，必须再看是不是原方块。
  const resolvedAfter =
    typeof latest.block_name_after === 'string' && latest.block_name_after
      ? latest.block_name_after
      : typeof blockAfter === 'string' && blockAfter
        ? blockAfter
        : null
  if (typeof blockBefore === 'string' && blockBefore && resolvedAfter) {
    if (resolvedAfter === blockBefore) {
      return { authoritative: true, kind: 'present_same', conflict: false, replaced: false }
    }
    if (resolvedAfter === 'air') {
      // 状态 id 说非 air 但解析成 air → 无法解释，绝不猜
      return { authoritative: true, kind: 'present_unknown', conflict: false, replaced: false }
    }
    return { authoritative: true, kind: 'replaced', conflict: false, replaced: true }
  }
  return { authoritative: true, kind: 'present_unknown', conflict: false, replaced: false }
}

/**
 * 由**真实读到的**方块名 + **按顺序裁决的服务端更新**推出世界效果。
 *
 * 真机取证（2026-10-10，Fabric 1.21.1）发现两件事，都必须照实处理：
 *
 *  1. mineflayer 的 `finishDigging()` 会在预期时长处做**本地乐观更新**
 *     （``bot._updateBlockState(pos, 0)``），所以"本地读出来是 air"并不等于服务器真的移除了
 *     那个方块 —— 实测反例：非 op 客户端在出生点保护范围内挖掘，客户端自称挖完、本地视图变 air，
 *     而服务器侧方块一直没变（另一个 op 客户端读到的仍是 stone）。
 *  2. 服务器会为**它拒绝的挖掘**回一个"这里还是那个方块"的纠正包（``type != 0``，即
 *     ``to_air: false``）—— 那是"没有发生移除"的**正面证据**，绝不能当成"变了"。
 *
 * 于是规则是（服务端更新按到达顺序裁决，见 `resolveServerUpdates`）：
 *
 *   * 最后一条服务端更新说 air → ``BLOCK_REMOVED``（唯一可信的移除）；
 *   * 最后一条说非 air、且仍是原方块 → ``BLOCK_REMAINS``（纠正包 = 没移除）；
 *   * 最后一条说非 air、但已换成另一个方块 → ``BLOCK_REMOVED``（原方块被替换；上层强制 AMBIGUOUS）；
 *   * 服务端自相矛盾 / 读不到方块名 → ``UNKNOWN``（**不知道**，绝不写成"世界变了"）；
 *   * 只有本地视图变了、服务器什么都没说 → ``UNKNOWN``；
 *   * 本地读数与原方块一致 → ``BLOCK_REMAINS``；读不到 → ``UNKNOWN``。
 */
function worldEffectOf(blockBefore, blockAfter, options) {
  const opts = options || {}
  const resolution = resolveServerUpdates(opts.serverUpdates, blockBefore, blockAfter)
  if (resolution.authoritative) {
    if (resolution.kind === 'removed') return WORLD_EFFECT.BLOCK_REMOVED
    if (resolution.kind === 'present_same') return WORLD_EFFECT.BLOCK_REMAINS
    if (resolution.kind === 'replaced') return WORLD_EFFECT.BLOCK_REMOVED
    return WORLD_EFFECT.UNKNOWN // conflict / present_unknown
  }
  // 没有权威服务端更新：本地读数只能证明"和原来一样"，其余一律不知道
  if (typeof blockAfter !== 'string' || !blockAfter) return WORLD_EFFECT.UNKNOWN
  if (String(blockBefore) === blockAfter) return WORLD_EFFECT.BLOCK_REMAINS
  return WORLD_EFFECT.UNKNOWN
}

// ------------------------------------------------------------------ 采集器

/**
 * 建一个**绑定到本次动作**的证据采集器。
 *
 * @param {object} options
 *   - actionId: 本次动作的 action_id（作用域键；终态事件与审计靠它对齐）
 *   - position: 目标坐标 {x,y,z}（整数）
 *   - blockName: 执行前该坐标的方块名（`expected_block` 已校验过）
 *   - expectedDigMs: `bot.digTime(block)` 的预期时长（毫秒；拿不到就是 null）
 *   - startedAtMs: 动作开始时刻（默认 now）
 *   - sessionId / dimension: 审计字段（可空）
 *   - selfEntityId: 罐头自己的实体 id（用来区分"自己的破坏进度"与"别人的"）
 */
function createDigAttribution(options) {
  const opts = options || {}
  const target = positionOf(opts.position)
  if (!target) throw new Error('dig attribution: 目标坐标无效')
  const collector = {
    action_id: opts.actionId === undefined || opts.actionId === null ? '' : String(opts.actionId),
    target: { ...target, block_before: String(opts.blockName || '') },
    session_id: opts.sessionId === undefined || opts.sessionId === null ? null : String(opts.sessionId),
    dimension: opts.dimension === undefined ? null : opts.dimension,
    action_started_at_ms: Number.isFinite(opts.startedAtMs) ? Number(opts.startedAtMs) : nowMs(),
    expected_dig_ms:
      Number.isFinite(opts.expectedDigMs) && opts.expectedDigMs > 0
        ? Math.round(Number(opts.expectedDigMs))
        : null,
    evidence: [],
    attached: false,
    detached: false,
    _listeners: [],
    _selfEntityId:
      opts.selfEntityId === undefined || opts.selfEntityId === null ? null : opts.selfEntityId,
  }

  function record(kind, extra) {
    if (collector.detached) return
    if (collector.evidence.length >= MAX_EVIDENCE) return
    const at = nowMs()
    collector.evidence.push({
      kind,
      at_ms: at,
      elapsed_ms: at - collector.action_started_at_ms,
      ...(extra || {}),
    })
  }

  /** 服务器是否对该坐标说过话（`block_update`/`block_change` 包，不管说的是什么）。 */
  collector.serverConfirmed = function serverConfirmed() {
    return collector.evidence.some((item) => item.kind === EVIDENCE_KINDS.SERVER_BLOCK_UPDATE)
  }

  /** 服务器对该坐标说过的**有序**更新（`to_air` 布尔），供世界效果按顺序裁决。 */
  collector.serverUpdates = function serverUpdates() {
    return collector.evidence
      .filter((item) => item.kind === EVIDENCE_KINDS.SERVER_BLOCK_UPDATE)
      .map((item) => ({
        to_air: item.to_air === true,
        block_name_after:
          typeof item.block_name_after === 'string' && item.block_name_after
            ? item.block_name_after
            : null,
        at_ms: item.at_ms,
      }))
  }

  /** 现在真的在听吗（挂了监听器）。假 bot / 已摘除 → false：等"服务器确认"没有意义。 */
  collector.observing = function observing() {
    return collector.attached && !collector.detached && collector._listeners.length > 0
  }

  /** 记一条证据（内部使用；测试可以直接喂合成时间线）。条数有上限，超出即丢弃。 */
  collector.record = record

  /** 挂上监听器（幂等；全部带坐标过滤）。 */
  collector.attach = function attach(bot) {
    if (collector.detached || collector.attached || !bot) return
    // 不是真的 emitter（例如测试用的假 bot）→ 不挂监听器，也不假装收到了证据
    if (typeof bot.on !== 'function' || typeof bot.removeListener !== 'function') return
    const eventName = blockUpdateEventName(target)

    const onBlockUpdate = (oldBlock, newBlock) => {
      // mineflayer 语义：`newBlock` 可能是 null（区块卸载）→ 不记
      if (!newBlock || !newBlock.position) return
      if (!samePosition(positionOf(newBlock.position), target)) return
      if (newBlock.type !== 0 && newBlock.name !== 'air') return
      record(EVIDENCE_KINDS.BLOCK_BECAME_AIR, {
        source: collector._serverConfirmedAt ? 'server_confirmed_local' : 'local_or_server',
      })
    }

    const onDiggingCompleted = (block) => {
      if (!samePosition(blockPosition(block), target)) return
      record(EVIDENCE_KINDS.SELF_DIG_COMPLETED, {})
    }

    const onDiggingAborted = (block) => {
      if (!samePosition(blockPosition(block), target)) return
      record(EVIDENCE_KINDS.SELF_DIG_ABORTED, {})
    }

    /**
     * 把一次破坏动画的实体解析成三态（7D.3 §2.3）：
     *  - `'self'`       —— 实体身份已可靠解析，且等于罐头自己的实体 id；
     *  - `'external'`   —— 实体身份已可靠解析，且**确非**自身（自身 id 已知）；
     *  - `'unresolved'` —— 实体对象缺失 / 无 id / 自身 id 未知 → 身份无法可靠判定。
     * 关键：未解析**绝不**当成外部。
     */
    const breakActorOf = (entity) => {
      const resolvedObject = entity !== undefined && entity !== null
      const id = entityIdOf(entity)
      if (!resolvedObject || id === null) return 'unresolved'
      if (collector._selfEntityId === null) return 'unresolved'
      return id === collector._selfEntityId ? 'self' : 'external'
    }

    const onBreakProgress = (block, destroyStage, entity) => {
      if (!samePosition(blockPosition(block), target)) return
      const actor = breakActorOf(entity)
      const kind =
        actor === 'self'
          ? EVIDENCE_KINDS.SELF_BREAK_PROGRESS
          : actor === 'external'
            ? EVIDENCE_KINDS.EXTERNAL_BREAK_PROGRESS
            : EVIDENCE_KINDS.UNRESOLVED_BREAK_PROGRESS
      record(kind, {
        entity_id: entityIdOf(entity),
        entity_name: entityNameOf(entity),
        stage: Number.isFinite(destroyStage) ? destroyStage : null,
        entity_resolved: actor !== 'unresolved',
      })
    }

    const onBreakProgressEnd = (block, entity) => {
      if (!samePosition(blockPosition(block), target)) return
      const actor = breakActorOf(entity)
      const kind =
        actor === 'self'
          ? EVIDENCE_KINDS.SELF_BREAK_END
          : actor === 'external'
            ? EVIDENCE_KINDS.EXTERNAL_BREAK_END
            : EVIDENCE_KINDS.UNRESOLVED_BREAK_END
      record(kind, {
        entity_id: entityIdOf(entity),
        entity_name: entityNameOf(entity),
        entity_resolved: actor !== 'unresolved',
      })
    }

    const client = bot._client
    let onPacket = null
    if (client && typeof client.on === 'function') {
      onPacket = (data, meta) => {
        const name = String((meta && meta.name) || '').replace(/^packet_/, '')
        if (!SERVER_BLOCK_PACKET_NAMES.has(name)) return
        if (!samePosition(positionOf(data && data.location), target)) return
        // 新版协议里 `type` 是全局方块状态 id（0 = air）；老版 `block_change` 同形。
        // 用注册表把状态 id 解析回**方块名**，才能区分"原方块还在"与"被换成另一个方块"。
        const stateId = Number(data && data.type)
        const isAir = stateId === 0
        let blockNameAfter = isAir ? 'air' : null
        if (!isAir && bot.registry && bot.registry.blocksByStateId) {
          const entry = bot.registry.blocksByStateId[stateId]
          if (entry && typeof entry.name === 'string' && entry.name) blockNameAfter = entry.name
        }
        collector._serverConfirmedAt = nowMs()
        record(EVIDENCE_KINDS.SERVER_BLOCK_UPDATE, {
          to_air: isAir,
          state_id: Number.isFinite(stateId) ? stateId : null,
          block_name_after: blockNameAfter,
        })
      }
      client.on('packet', onPacket)
    }

    bot.on(eventName, onBlockUpdate)
    bot.on('diggingCompleted', onDiggingCompleted)
    bot.on('diggingAborted', onDiggingAborted)
    bot.on('blockBreakProgressObserved', onBreakProgress)
    bot.on('blockBreakProgressEnd', onBreakProgressEnd)
    collector._listeners.push(
      [bot, eventName, onBlockUpdate],
      [bot, 'diggingCompleted', onDiggingCompleted],
      [bot, 'diggingAborted', onDiggingAborted],
      [bot, 'blockBreakProgressObserved', onBreakProgress],
      [bot, 'blockBreakProgressEnd', onBreakProgressEnd],
    )
    if (onPacket) collector._listeners.push([client, 'packet', onPacket])
    collector.attached = true
  }

  /** 摘掉全部监听器（幂等，绝不泄漏；等待与 cleanup 都会调，谁先谁后用同一份账）。 */
  collector.detach = function detach() {
    if (collector.detached) return
    for (const entry of collector._listeners) {
      const [emitter, event, handler] = entry
      try {
        if (emitter && typeof emitter.removeListener === 'function') {
          emitter.removeListener(event, handler)
        }
      } catch (_error) {
        /* 断开/退出路径上 emitter 可能已经坏了：摘不掉也不能抛 */
      }
    }
    collector._listeners = []
    collector.detached = true
    collector.attached = false
  }

  /**
   * 纯判定：把采集到的证据 + 现实里的世界效果，收敛成两个独立结论。
   * 调用方负责先 detach（本函数不改任何监听器状态）。
   */
  collector.resolve = function resolve(input) {
    const details = input || {}
    const worldEffect = String(details.worldEffect || WORLD_EFFECT.UNKNOWN)
    const finishedAt = Number.isFinite(details.finishedAtMs) ? Number(details.finishedAtMs) : nowMs()
    const evidence = collector.evidence.slice(0, MAX_EVIDENCE)
    const first = (kind) => evidence.find((item) => item.kind === kind) || null
    const removal = first(EVIDENCE_KINDS.BLOCK_BECAME_AIR) || first(EVIDENCE_KINDS.SERVER_BLOCK_UPDATE)
    const selfProgress = first(EVIDENCE_KINDS.SELF_BREAK_PROGRESS)
    const completed = first(EVIDENCE_KINDS.SELF_DIG_COMPLETED)
    const aborted = first(EVIDENCE_KINDS.SELF_DIG_ABORTED)
    const external = evidence.filter(
      (item) =>
        item.kind === EVIDENCE_KINDS.EXTERNAL_BREAK_PROGRESS ||
        item.kind === EVIDENCE_KINDS.EXTERNAL_BREAK_END,
    )
    const externalSeen = external.length > 0
    const unresolvedSeen = evidence.some(
      (item) =>
        item.kind === EVIDENCE_KINDS.UNRESOLVED_BREAK_PROGRESS ||
        item.kind === EVIDENCE_KINDS.UNRESOLVED_BREAK_END,
    )
    // 服务端更新**按到达顺序**收集；世界效果只认最后一条（7D.3 §2.4）。
    const serverUpdates = evidence
      .filter((item) => item.kind === EVIDENCE_KINDS.SERVER_BLOCK_UPDATE)
      .map((item) => ({ to_air: item.to_air === true, at_ms: item.at_ms }))
    const serverUpdate = serverUpdates.length ? serverUpdates[serverUpdates.length - 1] : null
    const serverResolution = resolveServerUpdates(
      serverUpdates,
      collector.target.block_before,
      details.blockAfter,
    )
    const removalAt = removal ? removal.at_ms : null
    const removalElapsed = removalAt === null ? null : removalAt - collector.action_started_at_ms
    const expected = collector.expected_dig_ms
    const ratio =
      removalElapsed !== null && expected !== null && expected > 0 ? removalElapsed / expected : null

    let verdict = ATTRIBUTION.AMBIGUOUS
    let basis = CONFIRM_BASIS.NONE
    let reason = ATTRIBUTION_REASONS.NO_TARGET_CHANGE

    // 「自己在预期时刻挖完」这件事**与外部信号无关**：外部信号只决定我们最后敢不敢认领，
    // 不能把它并进"自己到底有没有按时挖完"的判断里（否则冲突检测会自我抵消）。
    const selfCompletedAtExpected =
      Boolean(completed) && expected !== null && ratio !== null && ratio >= SELF_MIN_RATIO
    // 外部**和身份未知**的破坏迹象都算"竞争信号"：与自身证据冲突时绝不站队（7D.3 §2.3）。
    const rivalSeen = externalSeen || unresolvedSeen
    const conflict = rivalSeen && (Boolean(selfProgress) || selfCompletedAtExpected)
    const serverConflict = serverResolution.conflict === true
    const replaced = serverResolution.replaced === true
    const serverSaysAir = serverResolution.kind === 'removed'

    if (worldEffect === WORLD_EFFECT.BLOCK_REMAINS) {
      reason = ATTRIBUTION_REASONS.BLOCK_REMAINS
    } else if (worldEffect === WORLD_EFFECT.UNKNOWN) {
      // 本地视图说方块变了、但服务器没确认过 → 如实说明（真机踩过：出生点保护）
      reason =
        details.localViewOnly === true
          ? ATTRIBUTION_REASONS.LOCAL_VIEW_ONLY
          : serverConflict
            ? ATTRIBUTION_REASONS.CONFLICTING_SERVER_UPDATES
            : ATTRIBUTION_REASONS.WORLD_EFFECT_UNKNOWN
    } else if (serverConflict) {
      // 服务端更新自相矛盾（先说没了、后又说还在）→ 不收敛，绝不形成严格证明
      reason = ATTRIBUTION_REASONS.CONFLICTING_SERVER_UPDATES
    } else if (replaced) {
      // 目标块被换成了另一个非 air 方块：原方块没了，但这不是普通挖空，不归给任何人
      reason = ATTRIBUTION_REASONS.BLOCK_REPLACED
    } else if (conflict) {
      // 别人/身份未知的力量在挖同一个坐标，而我们自己的挖掘也在预期时刻走完了（或有自己的破坏进度）
      // → 证据冲突，绝不站队
      verdict = ATTRIBUTION.AMBIGUOUS
      reason = ATTRIBUTION_REASONS.CONFLICTING_EVIDENCE
    } else if (externalSeen) {
      verdict = ATTRIBUTION.EXTERNAL_INDICATED
      basis = CONFIRM_BASIS.EXTERNAL_BREAK_PROGRESS
      reason = ATTRIBUTION_REASONS.EXTERNAL_PROGRESS_OBSERVED
    } else if (unresolvedSeen) {
      // 有破坏迹象但"是谁"说不清 → 绝不判成外部，也不升级成自挖
      reason = ATTRIBUTION_REASONS.UNRESOLVED_BREAK_ACTOR
    } else if (selfProgress) {
      // 直接执行者证据：本客户端观察到自己的实体在目标坐标的破坏进度
      verdict = ATTRIBUTION.SELF_CONFIRMED
      basis = CONFIRM_BASIS.SELF_BREAK_PROGRESS
      reason = ATTRIBUTION_REASONS.SELF_PROGRESS_OBSERVED
    } else if (removalAt === null) {
      reason = ATTRIBUTION_REASONS.NO_BLOCK_CHANGE
    } else if (removalAt < collector.action_started_at_ms) {
      reason = ATTRIBUTION_REASONS.REMOVED_BEFORE_SELF_START
    } else if (!completed) {
      reason = ATTRIBUTION_REASONS.NO_SELF_COMPLETION
    } else if (expected === null || ratio === null) {
      reason = ATTRIBUTION_REASONS.EXPECTED_TIME_UNKNOWN
    } else if (ratio < SELF_MIN_RATIO) {
      reason = ATTRIBUTION_REASONS.REMOVED_BEFORE_SELF_COMPLETION
    } else {
      // 时序推断：自身挖掘生命周期在预期时刻走完 + 服务端确认移除 + 无外部信号
      // → 推断为自挖，但**不是**严格自证
      verdict = ATTRIBUTION.SELF_INFERRED
      basis = CONFIRM_BASIS.DIG_LIFECYCLE_TIMING
      reason = ATTRIBUTION_REASONS.SELF_COMPLETED_AT_EXPECTED
    }

    const assurance =
      verdict === ATTRIBUTION.SELF_CONFIRMED
        ? ASSURANCE.STRICT
        : verdict === ATTRIBUTION.SELF_INFERRED
          ? ASSURANCE.INFERRED
          : verdict === ATTRIBUTION.EXTERNAL_INDICATED
            ? ASSURANCE.EXTERNAL
            : ASSURANCE.NONE

    return {
      kind: 'dig_attribution',
      schema: DIG_ATTRIBUTION_SCHEMA,
      action_id: collector.action_id,
      target: { ...collector.target },
      session_id: collector.session_id,
      dimension: collector.dimension,
      world_effect: worldEffect,
      attribution: verdict,
      assurance,
      confirm_basis: basis,
      reason_code: reason,
      strict_self_proof: verdict === ATTRIBUTION.SELF_CONFIRMED,
      expected_dig_ms: expected,
      action_window: {
        started_at_ms: collector.action_started_at_ms,
        finished_at_ms: finishedAt,
        elapsed_ms: finishedAt - collector.action_started_at_ms,
      },
      removal: {
        observed_at_ms: removalAt,
        elapsed_ms: removalElapsed,
        ratio: ratio === null ? null : Number(ratio.toFixed(3)),
        source: serverUpdate ? 'server' : removal ? 'local_or_server' : 'none',
      },
      flags: {
        external_break_observed: externalSeen,
        external_break_entities: external
          .map((item) => item.entity_name || item.entity_id)
          .filter((value) => value !== null && value !== undefined),
        unresolved_break_observed: unresolvedSeen,
        self_break_observed: Boolean(selfProgress),
        self_dig_completed: Boolean(completed),
        self_dig_aborted: Boolean(aborted),
        server_block_update_observed: serverUpdates.length > 0,
        server_block_update_says_air: serverSaysAir,
        server_block_update_says_present: Boolean(serverUpdate && serverUpdate.to_air !== true),
        server_block_update_conflict: serverConflict,
        server_block_update_replaced: replaced,
        conflict,
        //: 本地视图（可能是乐观更新）读到的方块名 + "只有本地视图"标记（审计用）
        local_block_after: details.blockAfter === undefined ? null : details.blockAfter,
        local_view_only: details.localViewOnly === true,
        block_after: details.blockAfter === undefined ? null : details.blockAfter,
      },
      evidence,
    }
  }

  return collector
}

module.exports = {
  ASSURANCE,
  ATTRIBUTION,
  ATTRIBUTION_REASONS,
  CONFIRM_BASIS,
  DIG_ATTRIBUTION_SCHEMA,
  EVIDENCE_KINDS,
  MAX_EVIDENCE,
  SELF_MIN_RATIO,
  WORLD_EFFECT,
  createDigAttribution,
  resolveServerUpdates,
  worldEffectOf,
}
