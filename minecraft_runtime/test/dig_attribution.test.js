/**
 * Phase 7D Follow-up —— 挖掘结果归因单元测试（不连服务器）。
 *
 * 覆盖任务书 §8 的判定矩阵（自证 / 外部（开始前·进行中·临近完成）/ 无归属证据 / 只有背包变化 /
 * 只有别的方块变化 / 重复与迟到事件 / 取消 · 超时 · 断开 / 作用域与坐标过滤 / 监听器零泄漏），
 * 以及 `ACTION_REGISTRY.dig` 把归因结论放进动作结果的接线。
 *
 * 运行：node minecraft_runtime/test/dig_attribution.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const { ACTION_REGISTRY } = require('../runtime.js')
const {
  ATTRIBUTION,
  ATTRIBUTION_REASONS,
  CONFIRM_BASIS,
  EVIDENCE_KINDS,
  MAX_EVIDENCE,
  SELF_MIN_RATIO,
  WORLD_EFFECT,
  createDigAttribution,
  worldEffectOf,
} = require('../dig_attribution.js')

let failures = 0
let checks = 0

function assert(condition, label) {
  checks += 1
  if (condition) {
    console.log(`  ✓ ${label}`)
  } else {
    failures += 1
    console.error(`  ✗ ${label}`)
  }
}

/** 极简 emitter：只实现本模块用到的 on/removeListener/emit，并记录监听器账目。 */
function makeEmitter() {
  const handlers = new Map() // event → [handler]
  return {
    handlers,
    on(event, handler) {
      if (!handlers.has(event)) handlers.set(event, [])
      handlers.get(event).push(handler)
      return this
    },
    removeListener(event, handler) {
      const list = handlers.get(event)
      if (!list) return this
      const index = list.indexOf(handler)
      if (index >= 0) list.splice(index, 1)
      return this
    },
    emit(event, ...args) {
      for (const handler of (handlers.get(event) || []).slice()) handler(...args)
      return true
    },
    listenerCount() {
      let total = 0
      for (const list of handlers.values()) total += list.length
      return total
    },
  }
}

function makeAirBlock(x, y, z) {
  return { name: 'air', type: 0, position: new Vec3(x, y, z) }
}

const TARGET = { x: 10, y: 64, z: -5 }
const BLOCK_NAME = 'minecraft:oak_log'

/** 建一个采集器（默认目标 / 预期 1000ms），并把"时刻"钉在 started_at 上。 */
function collector({ actionId = 'act_test', expectedDigMs = 1000, startedAtMs = 1000000 } = {}) {
  return createDigAttribution({
    actionId,
    position: TARGET,
    blockName: BLOCK_NAME,
    expectedDigMs,
    startedAtMs,
    selfEntityId: 7,
  })
}

/** 直接往采集器里塞证据（绕过监听器，专测判定规则）。 */
function withEvidence(c, items) {
  for (const item of items) c.evidence.push(item)
  return c
}

function ev(kind, elapsedMs, extra) {
  return { kind, at_ms: 1000000 + elapsedMs, elapsed_ms: elapsedMs, ...(extra || {}) }
}

async function main() {
  console.log('[dig-attribution] 世界效果与世界效果的读法')
  {
    assert(worldEffectOf('minecraft:stone', 'air') === WORLD_EFFECT.BLOCK_REMOVED, '变成 air → BLOCK_REMOVED')
    assert(
      worldEffectOf('minecraft:stone', 'minecraft:dirt') === WORLD_EFFECT.BLOCK_REMOVED,
      '换成别的方块 → BLOCK_REMOVED（原方块已经不在了）',
    )
    assert(worldEffectOf('minecraft:stone', 'minecraft:stone') === WORLD_EFFECT.BLOCK_REMAINS, '还在原位 → BLOCK_REMAINS')
    assert(worldEffectOf('minecraft:stone', null) === WORLD_EFFECT.UNKNOWN, '读不到 → UNKNOWN（绝不猜）')
    assert(worldEffectOf('minecraft:stone', '') === WORLD_EFFECT.UNKNOWN, '空字符串 → UNKNOWN')
  }

  console.log('[dig-attribution] 判定矩阵（纯函数）')
  {
    // 1）自证：自己的挖掘生命周期在预期时刻走完，且没有别人的破坏信号
    const self = collector()
    withEvidence(self, [ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, 1010), ev(EVIDENCE_KINDS.SELF_DIG_COMPLETED, 1010)])
    const selfPayload = self.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED, finishedAtMs: 1010 + 1000000 })
    assert(
      selfPayload.attribution === ATTRIBUTION.SELF_CONFIRMED &&
        selfPayload.confirm_basis === CONFIRM_BASIS.DIG_LIFECYCLE_TIMING,
      `自挖（预期时刻完成）→ SELF_CONFIRMED（得到 ${selfPayload.attribution}/${selfPayload.confirm_basis}）`,
    )
    assert(
      selfPayload.reason_code === ATTRIBUTION_REASONS.SELF_COMPLETED_AT_EXPECTED,
      `原因码 = ${ATTRIBUTION_REASONS.SELF_COMPLETED_AT_EXPECTED}（得到 ${selfPayload.reason_code}）`,
    )
    assert(selfPayload.world_effect === WORLD_EFFECT.BLOCK_REMOVED, '世界效果仍然是 BLOCK_REMOVED（两个轴独立）')

    // 1b）自证（正面进度）：自己的破坏动画
    const selfProgress = collector()
    withEvidence(selfProgress, [
      ev(EVIDENCE_KINDS.SELF_BREAK_PROGRESS, 300, { entity_id: 7 }),
      ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, 1005),
      ev(EVIDENCE_KINDS.SELF_DIG_COMPLETED, 1005),
    ])
    const selfProgressPayload = selfProgress.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      selfProgressPayload.attribution === ATTRIBUTION.SELF_CONFIRMED &&
        selfProgressPayload.confirm_basis === CONFIRM_BASIS.SELF_BREAK_PROGRESS,
      `自己的破坏进度 → SELF_CONFIRMED/${CONFIRM_BASIS.SELF_BREAK_PROGRESS}`,
    )
    assert(selfProgressPayload.strict_self_proof === true, '自己的破坏进度算严格自证')

    // 2）外部：别人在挖同一个坐标（而我们自己并没有在预期时刻挖完 → 不构成冲突）
    const external = collector()
    withEvidence(external, [
      ev(EVIDENCE_KINDS.EXTERNAL_BREAK_PROGRESS, 200, { entity_id: 42, entity_name: 'Steve', stage: 3 }),
      ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, 200),
      ev(EVIDENCE_KINDS.SELF_DIG_ABORTED, 300),
    ])
    const externalPayload = external.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      externalPayload.attribution === ATTRIBUTION.EXTERNAL_INDICATED,
      `别人在挖同一个坐标 → EXTERNAL_INDICATED（得到 ${externalPayload.attribution}）`,
    )
    assert(
      externalPayload.flags.external_break_entities.includes('Steve'),
      '外部证据带实体身份（Steve）',
    )

    // 2b）外部（挖掘开始前就被弄掉了）：坐标变化早于本次动作开始
    const early = collector()
    withEvidence(early, [
      ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, -500),
      ev(EVIDENCE_KINDS.SELF_DIG_COMPLETED, 1000),
    ])
    const earlyPayload = early.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      earlyPayload.attribution === ATTRIBUTION.AMBIGUOUS &&
        earlyPayload.reason_code === ATTRIBUTION_REASONS.REMOVED_BEFORE_SELF_START,
      `开始前就没了 → AMBIGUOUS/${ATTRIBUTION_REASONS.REMOVED_BEFORE_SELF_START}（得到 ${earlyPayload.reason_code}）`,
    )

    // 2c）外部（挖掘进行中，Phase 7D §10.4 的形状：0.78 预期时长时方块消失，没有任何动画）
    const midDig = collector()
    withEvidence(midDig, [ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, 780), ev(EVIDENCE_KINDS.SELF_DIG_COMPLETED, 780)])
    const midDigPayload = midDig.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      midDigPayload.attribution === ATTRIBUTION.AMBIGUOUS &&
        midDigPayload.reason_code === ATTRIBUTION_REASONS.REMOVED_BEFORE_SELF_COMPLETION,
      `§10.4 形状（0.78 预期时长）→ AMBIGUOUS/${ATTRIBUTION_REASONS.REMOVED_BEFORE_SELF_COMPLETION}（得到 ${midDigPayload.reason_code}）`,
    )
    assert(
      midDigPayload.removal.ratio === 0.78,
      `payload 保留 removal.ratio=0.78（得到 ${midDigPayload.removal.ratio}）`,
    )

    // 2d）外部（临近完成：刚好卡在自证下限之下）
    const near = collector()
    withEvidence(near, [ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, Math.floor(1000 * SELF_MIN_RATIO) - 1), ev(EVIDENCE_KINDS.SELF_DIG_COMPLETED, 800)])
    const nearPayload = near.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      nearPayload.attribution === ATTRIBUTION.AMBIGUOUS &&
        nearPayload.reason_code === ATTRIBUTION_REASONS.REMOVED_BEFORE_SELF_COMPLETION,
      `临近完成但早于下限 → AMBIGUOUS（得到 ${nearPayload.reason_code}）`,
    )

    // 2e）冲突：别人在挖同一个坐标 + 我们自己也在预期时刻挖完 → 谁也不站
    const conflict = collector()
    withEvidence(conflict, [
      ev(EVIDENCE_KINDS.EXTERNAL_BREAK_PROGRESS, 100, { entity_id: 42 }),
      ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, 1000),
      ev(EVIDENCE_KINDS.SELF_DIG_COMPLETED, 1000),
    ])
    const conflictPayload = conflict.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      conflictPayload.attribution === ATTRIBUTION.AMBIGUOUS &&
        conflictPayload.reason_code === ATTRIBUTION_REASONS.CONFLICTING_EVIDENCE &&
        conflictPayload.flags.conflict === true,
      `外部 + 自证冲突 → AMBIGUOUS/${ATTRIBUTION_REASONS.CONFLICTING_EVIDENCE}（得到 ${conflictPayload.reason_code}）`,
    )

    // 3）方块没了但没有任何归属证据（例如服务器把方块改了而我们没有挖掘完成回执）
    const unattributed = collector()
    withEvidence(unattributed, [ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, 400)])
    const unattributedPayload = unattributed.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      unattributedPayload.attribution === ATTRIBUTION.AMBIGUOUS &&
        unattributedPayload.reason_code === ATTRIBUTION_REASONS.NO_SELF_COMPLETION,
      `方块没了但无归属证据 → AMBIGUOUS/${ATTRIBUTION_REASONS.NO_SELF_COMPLETION}（得到 ${unattributedPayload.reason_code}）`,
    )

    // 3b）世界效果是"方块还在"或"读不到"
    const remains = collector()
    withEvidence(remains, [ev(EVIDENCE_KINDS.SELF_DIG_COMPLETED, 1000)])
    const remainsPayload = remains.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMAINS })
    assert(
      remainsPayload.attribution === ATTRIBUTION.AMBIGUOUS &&
        remainsPayload.world_effect === WORLD_EFFECT.BLOCK_REMAINS,
      `方块仍在 → AMBIGUOUS + world_effect=BLOCK_REMAINS`,
    )
    const unknown = collector().resolve({ worldEffect: WORLD_EFFECT.UNKNOWN })
    assert(
      unknown.attribution === ATTRIBUTION.AMBIGUOUS &&
        unknown.reason_code === ATTRIBUTION_REASONS.WORLD_EFFECT_UNKNOWN,
      `世界效果未知 → AMBIGUOUS/${ATTRIBUTION_REASONS.WORLD_EFFECT_UNKNOWN}（得到 ${unknown.reason_code}）`,
    )

    // 3c）拿不到预期时长 → 不做时序推断，如实判歧义（绝不用"没看到别人"来冒充自证）
    const noExpected = collector({ expectedDigMs: null })
    withEvidence(noExpected, [ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, 1000), ev(EVIDENCE_KINDS.SELF_DIG_COMPLETED, 1000)])
    const noExpectedPayload = noExpected.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      noExpectedPayload.attribution === ATTRIBUTION.AMBIGUOUS &&
        noExpectedPayload.reason_code === ATTRIBUTION_REASONS.EXPECTED_TIME_UNKNOWN,
      `没有预期时长 → AMBIGUOUS/${ATTRIBUTION_REASONS.EXPECTED_TIME_UNKNOWN}（得到 ${noExpectedPayload.reason_code}）`,
    )

    // 3d）挖掘被中断（stop/超时）→ 没有自证
    const aborted = collector()
    withEvidence(aborted, [ev(EVIDENCE_KINDS.SELF_DIG_ABORTED, 400), ev(EVIDENCE_KINDS.BLOCK_BECAME_AIR, 700)])
    const abortedPayload = aborted.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      abortedPayload.attribution === ATTRIBUTION.AMBIGUOUS &&
        abortedPayload.flags.self_dig_aborted === true,
      `被中断 → AMBIGUOUS（得到 ${abortedPayload.attribution}）`,
    )

    // 3e）服务器确认过该坐标变了 → strict_self_proof 为真（但没有自证依据时仍不升级归属）
    const serverOnly = collector()
    withEvidence(serverOnly, [
      ev(EVIDENCE_KINDS.SERVER_BLOCK_UPDATE, 1000, { to_air: true }),
      ev(EVIDENCE_KINDS.SELF_DIG_COMPLETED, 1000),
    ])
    const serverOnlyPayload = serverOnly.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      serverOnlyPayload.attribution === ATTRIBUTION.SELF_CONFIRMED &&
        serverOnlyPayload.strict_self_proof === true &&
        serverOnlyPayload.flags.server_block_update_observed === true,
      '服务器包 + 自己的完成回执 + 预期时刻 → SELF_CONFIRMED 且严格自证',
    )

    // 4）重复/迟到事件不改变结论，证据条数有上限
    const duplicated = collector()
    for (let i = 0; i < 40; i += 1) duplicated.record(EVIDENCE_KINDS.BLOCK_BECAME_AIR, {})
    assert(
      duplicated.evidence.length <= MAX_EVIDENCE,
      `证据条数不超上限（${duplicated.evidence.length} ≤ ${MAX_EVIDENCE}）`,
    )
    const duplicatedPayload = duplicated.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      duplicatedPayload.evidence.length <= MAX_EVIDENCE,
      `payload 里的证据条数也不超上限（${duplicatedPayload.evidence.length}）`,
    )
    assert(
      duplicatedPayload.attribution === ATTRIBUTION.AMBIGUOUS &&
        duplicatedPayload.reason_code === ATTRIBUTION_REASONS.NO_SELF_COMPLETION,
      '重复的世界变化不会自己变成"自证"',
    )
  }

  console.log('[dig-attribution] 采集器：坐标过滤 / 作用域 / 零泄漏')
  {
    const target = { x: 3, y: 70, z: 8 }
    const bot = makeEmitter()
    const client = makeEmitter()
    bot._client = client
    const c = createDigAttribution({
      actionId: 'act_scope',
      position: target,
      blockName: 'minecraft:stone',
      expectedDigMs: 1000,
      startedAtMs: 5000,
      selfEntityId: 7,
    })
    c.attach(bot)
    const attached = bot.listenerCount() + client.listenerCount()
    assert(attached > 0, `attach 挂上了监听器（${attached} 个）`)

    // 别的坐标的变化 / 别的坐标的动画 / 别的包的服务器消息：一律不记
    bot.emit('blockUpdate:(3, 70, 9)', null, { name: 'air', type: 0, position: new Vec3(3, 70, 9) })
    bot.emit('blockBreakProgressObserved', { name: 'stone', position: new Vec3(3, 70, 9) }, 3, {
      id: 42,
      username: 'Steve',
    })
    client.emit('packet', { location: { x: 3, y: 70, z: 9 }, type: 0 }, { name: 'packet_block_update' })
    client.emit('packet', { location: { x: 3, y: 70, z: 8 }, type: 0 }, { name: 'packet_entity_move' })
    assert(c.evidence.length === 0, `别的坐标/别的包不产生证据（得到 ${c.evidence.length} 条）`)

    // 自己坐标的变化 + 自己的动画 + 别人的动画
    bot.emit('blockUpdate:(3, 70, 8)', null, { name: 'air', type: 0, position: new Vec3(3, 70, 8) })
    client.emit('packet', { location: { x: 3, y: 70, z: 8 }, type: 0 }, { name: 'packet_block_update' })
    bot.emit('blockBreakProgressObserved', { name: 'stone', position: new Vec3(3, 70, 8) }, 4, {
      id: 7,
      username: 'CatooBot',
    })
    bot.emit('blockBreakProgressObserved', { name: 'stone', position: new Vec3(3, 70, 8) }, 4, {
      id: 42,
      username: 'Steve',
    })
    bot.emit('diggingCompleted', { name: 'air', type: 0, position: new Vec3(3, 70, 8) })
    const kinds = c.evidence.map((item) => item.kind)
    assert(kinds.includes(EVIDENCE_KINDS.BLOCK_BECAME_AIR), '目标坐标变 air → 记证据')
    assert(kinds.includes(EVIDENCE_KINDS.SERVER_BLOCK_UPDATE), '目标坐标的服务器包 → 记证据')
    assert(kinds.includes(EVIDENCE_KINDS.SELF_BREAK_PROGRESS), '自己实体 id 的动画 → self_break_progress')
    assert(kinds.includes(EVIDENCE_KINDS.EXTERNAL_BREAK_PROGRESS), '别的实体 id 的动画 → external_break_progress')
    assert(kinds.includes(EVIDENCE_KINDS.SELF_DIG_COMPLETED), 'diggingCompleted（同坐标）→ self_dig_completed')

    // detach：全部摘掉；摘掉之后迟到事件一律忽略（不涨证据、不复活）
    const before = c.evidence.length
    c.detach()
    assert(bot.listenerCount() + client.listenerCount() === 0, 'detach 摘干净所有监听器')
    bot.emit('blockUpdate:(3, 70, 8)', null, { name: 'air', type: 0, position: new Vec3(3, 70, 8) })
    bot.emit('diggingCompleted', { name: 'air', type: 0, position: new Vec3(3, 70, 8) })
    assert(c.evidence.length === before, `detach 之后的迟到事件被忽略（仍是 ${before} 条）`)
    c.detach()
    assert(bot.listenerCount() + client.listenerCount() === 0, 'detach 幂等')
    const late = c.resolve({ worldEffect: WORLD_EFFECT.BLOCK_REMOVED })
    assert(
      late.attribution === ATTRIBUTION.AMBIGUOUS &&
        late.reason_code === ATTRIBUTION_REASONS.CONFLICTING_EVIDENCE,
      '摘掉之后用已有证据判定（外部动画 + 自己的完成回执 → 冲突 → 歧义）',
    )
  }

  console.log('[dig-attribution] ACTION_REGISTRY.dig 接线：归因随动作结果一起返回')
  {
    const def = ACTION_REGISTRY.dig
    const params = { x: 3, y: 64, z: 3, expected_block: 'minecraft:stone' }

    function makeDigBot(options = {}) {
      const bot = makeEmitter()
      bot.entity = { position: new Vec3(3, 64, 3), id: 7 }
      bot.heldItem = null
      bot.game = { dimension: 'minecraft:overworld' }
      // 预期时长与真实等待同口径：假 bot 用 100ms 模拟"按时挖完"
      bot.digTime = () => 100
      bot.blockAt = () => options.block || { name: 'minecraft:stone', type: 1, position: new Vec3(3, 64, 3) }
      bot.canDigBlock = () => true
      bot.stopCalls = 0
      bot.stopDigging = () => {
        bot.stopCalls += 1
      }
      bot.clearControlStates = () => {}
      bot.dig = async (block) => {
        bot.digCalls = (bot.digCalls || 0) + 1
        if (options.emitExternal) {
          bot.emit('blockBreakProgressObserved', block, 5, { id: 42, username: 'Steve' })
        }
        if (options.selfProgress) {
          bot.emit('blockBreakProgressObserved', block, 5, { id: 7, username: 'CatooBot' })
        }
        if (options.earlyRemoval) {
          // 挖到一半就被外面弄没了（Phase 7D §10.4 的形状）：立刻变 air
          bot.emit('blockUpdate:(3, 64, 3)', null, { name: 'air', type: 0, position: new Vec3(3, 64, 3) })
          bot.emit('diggingCompleted', { name: 'air', type: 0, position: new Vec3(3, 64, 3) })
        } else {
          await new Promise((resolve) => setTimeout(resolve, 100))
          bot.emit('blockUpdate:(3, 64, 3)', null, { name: 'air', type: 0, position: new Vec3(3, 64, 3) })
          bot.emit('diggingCompleted', { name: 'air', type: 0, position: new Vec3(3, 64, 3) })
        }
        // 之后再读方块 = 空气
        bot.blockAt = () => ({ name: 'air', type: 0, position: new Vec3(3, 64, 3) })
      }
      return bot
    }

    // 正常自挖：结果里带 attribution / world_effect，且监听器被摘干净
    const bot = makeDigBot()
    const controller = { record: { action_id: 'act_dig_1', action: 'dig' } }
    const state = await def.start(bot, params, { cancelled: false }, controller)
    const result = await def.wait(bot, params, { cancelled: false }, state)
    assert(result.block_before === 'minecraft:stone' && result.block_after === 'air', '原有结果字段不变')
    assert(Boolean(result.attribution), '结果里带 attribution（归因）')
    assert(
      result.attribution.attribution === ATTRIBUTION.SELF_CONFIRMED,
      `正常自挖 → SELF_CONFIRMED（得到 ${result.attribution && result.attribution.attribution}）`,
    )
    assert(result.attribution.world_effect === WORLD_EFFECT.BLOCK_REMOVED, 'world_effect = BLOCK_REMOVED')
    assert(result.attribution.action_id === 'act_dig_1', '归因绑定本次 action_id')
    assert(
      result.attribution.target.x === 3 && result.attribution.target.y === 64 && result.attribution.target.z === 3,
      '归因带目标坐标',
    )
    assert(result.attribution.dimension === 'minecraft:overworld', '归因带维度（审计）')
    assert(bot.listenerCount() === 0, `wait 之后监听器清零（得到 ${bot.listenerCount()}）`)

    // 别人挖掉的：EXTERNAL_INDICATED（绝不写成自挖成功）
    const extBot = makeDigBot({ emitExternal: true, earlyRemoval: true })
    const extState = await def.start(extBot, params, { cancelled: false }, { record: { action_id: 'act_dig_2' } })
    const extResult = await def.wait(extBot, params, { cancelled: false }, extState)
    assert(
      extResult.attribution.attribution === ATTRIBUTION.EXTERNAL_INDICATED,
      `外部破坏 → EXTERNAL_INDICATED（得到 ${extResult.attribution.attribution}）`,
    )
    assert(extBot.listenerCount() === 0, '外部路径同样零泄漏')

    // 挖到一半被外面弄没了（没有任何动画）→ AMBIGUOUS，绝不写成自挖成功
    const earlyBot = makeDigBot({ earlyRemoval: true })
    const earlyState = await def.start(earlyBot, params, { cancelled: false }, { record: { action_id: 'act_dig_4' } })
    const earlyResult = await def.wait(earlyBot, params, { cancelled: false }, earlyState)
    assert(
      earlyResult.attribution.attribution === ATTRIBUTION.AMBIGUOUS &&
        earlyResult.attribution.reason_code === ATTRIBUTION_REASONS.REMOVED_BEFORE_SELF_COMPLETION,
      `挖到一半被外面弄没 → AMBIGUOUS（得到 ${earlyResult.attribution.attribution}/${earlyResult.attribution.reason_code}）`,
    )
    assert(earlyBot.listenerCount() === 0, '早失败路径同样零泄漏')

    // 自己有破坏进度（正面自证）→ SELF_CONFIRMED/self_break_progress
    const selfBot = makeDigBot({ selfProgress: true })
    const selfState = await def.start(selfBot, params, { cancelled: false }, { record: { action_id: 'act_dig_5' } })
    const selfResult = await def.wait(selfBot, params, { cancelled: false }, selfState)
    assert(
      selfResult.attribution.attribution === ATTRIBUTION.SELF_CONFIRMED &&
        selfResult.attribution.confirm_basis === CONFIRM_BASIS.SELF_BREAK_PROGRESS,
      `自己的破坏进度 → SELF_CONFIRMED/self_break_progress（得到 ${selfResult.attribution.confirm_basis}）`,
    )

    // cleanup（取消/超时）也要摘监听器，并且仍然 stopDigging
    const cancelBot = makeDigBot()
    const cancelController = { record: { action_id: 'act_dig_3' } }
    const cancelState = await def.start(cancelBot, params, { cancelled: false }, cancelController)
    assert(cancelBot.listenerCount() > 0, 'start 之后有监听器在监听')
    def.cleanup(cancelBot, cancelController)
    assert(cancelBot.listenerCount() === 0, 'cleanup 摘掉监听器（取消/超时路径零泄漏）')
    assert(cancelBot.stopCalls === 1, 'cleanup 仍然 stopDigging')
    cancelBot.stopDigging = null
    def.cleanup(cancelBot, cancelController)
    assert(true, 'cleanup 幂等（再调一次不炸）')
    def.cleanup(null, null)
    assert(true, 'cleanup 对 null bot 安全')
    assert(Boolean(cancelState.block), 'start 仍然返回原有 state 字段')
  }

  console.log(`\n[dig-attribution] ${checks - failures}/${checks} 通过`)
  if (failures > 0) {
    console.error(`[dig-attribution] FAIL（${failures} 项）`)
    process.exit(1)
  }
  console.log('[dig-attribution] PASS')
}

main().catch((error) => {
  console.error('[dig-attribution] 崩溃', error)
  process.exit(1)
})
