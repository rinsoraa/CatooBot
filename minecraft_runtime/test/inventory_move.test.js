/**
 * inventory_move 注册与行为测试（Phase 4D）：参数校验（槽位范围/整数/数量/物品）/
 * start 阶段校验（source 空 / 物品不符 / 数量不足 / 目标被占）/ 同名可堆叠 /
 * wait 阶段按真实状态复核（不硬编码 +count）/ CANCELLED·TIMEOUT·race。
 *
 * 假 bot 按 mineflayer 玩家窗口语义建模（槽位 9..44），transfer 按"来源/目标各自钉死
 * 单个槽位"的用法实现（与 runtime 里传的 sourceStart/sourceEnd/destStart/destEnd 一致）。
 *
 * 运行：node minecraft_runtime/test/inventory_move.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const {
  ACTION_REGISTRY,
  INVENTORY_MOVE_DEFAULTS,
  PLAYER_WINDOW_SLOTS,
  inventorySlots,
} = require('../runtime.js')
const { createActionRuntime } = require('../action_runtime')

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

async function expectCode(promise, code, label) {
  try {
    await promise
  } catch (error) {
    assert(
      error && error.code === code,
      `${label} → ${code}（得到 ${error && (error.code || error.message)}）`,
    )
    return error
  }
  assert(false, `${label} → 期望 ${code}，但成功返回了`)
  return null
}

const MOVE = ACTION_REGISTRY.inventory_move

function item(name, count, type) {
  return { name, count, type, stackSize: 64, metadata: null, nbt: null }
}

const DIRT = 28
const SAND = 57

function makeBot({ slots = {}, transferImpl = null } = {}) {
  const inv = {
    slots: new Array(46).fill(null),
    hotbarStart: PLAYER_WINDOW_SLOTS.hotbarStart,
    inventoryStart: PLAYER_WINDOW_SLOTS.inventoryStart,
    inventoryEnd: PLAYER_WINDOW_SLOTS.inventoryEnd,
  }
  for (const [slot, value] of Object.entries(slots)) inv.slots[Number(slot)] = value
  const bot = {
    entity: { position: new Vec3(0, 64, 0) },
    quickBarSlot: 0,
    inventory: inv,
    cleared: 0,
    transferCalls: [],
    get heldItem() {
      return inv.slots[PLAYER_WINDOW_SLOTS.hotbarStart + this.quickBarSlot] || null
    },
    async transfer(options) {
      bot.transferCalls.push(options)
      if (transferImpl) return transferImpl(bot, options)
      const { itemType, count, sourceStart, destStart } = options
      const source = inv.slots[sourceStart]
      if (!source || source.type !== itemType) {
        throw new Error(`Can't find item in slots [${sourceStart} - ${sourceStart + 1}]`)
      }
      let dest = inv.slots[destStart]
      if (dest && dest.type !== itemType) throw new Error('destination full')
      const stackSize = (dest && dest.stackSize) || source.stackSize || 64
      let remaining = Math.min(count, source.count)
      let moved = 0
      while (remaining > 0) {
        const room = dest ? stackSize - dest.count : stackSize
        if (room <= 0) break
        const chunk = Math.min(room, remaining)
        if (!dest) {
          dest = { ...source, count: 0 }
          inv.slots[destStart] = dest
        }
        dest.count += chunk
        source.count -= chunk
        remaining -= chunk
        moved += chunk
      }
      if (source.count <= 0) inv.slots[sourceStart] = null
      if (moved === 0) throw new Error('destination full')
    },
    clearControlStates() {
      bot.cleared += 1
    },
    _slots: inv.slots,
  }
  return bot
}

function makeHarness(definition = MOVE) {
  const events = []
  const logs = []
  const state = { online: true }
  const runtime = createActionRuntime({
    registry: { inventory_move: definition },
    getBot: () => state.bot,
    isOnline: () => state.online,
    emit: (event, data) => events.push({ event, data }),
    log: (message) => logs.push(message),
    now: () => Date.now(),
  })
  return { runtime, events, logs, state }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function main() {
  setTimeout(() => {
    console.error('[move-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[move-test] inventory_move 注册表属性')
  {
    assert(Boolean(MOVE), 'inventory_move 已注册')
    assert(MOVE.exclusive === true, 'exclusive = true（改背包与动作语义互斥）')
    assert(MOVE.risk === 'MEDIUM', `risk = MEDIUM（得到 ${MOVE.risk}）`)
    assert(MOVE.timeout_ms === 15000, `默认 timeout = 15s（得到 ${MOVE.timeout_ms}）`)
    assert(MOVE.detached === true, 'detached = true')
    assert(typeof MOVE.validate === 'function', '有参数校验')
    assert(typeof MOVE.start === 'function' && typeof MOVE.wait === 'function', '有 start/wait')
    assert(typeof MOVE.run !== 'function', '没有阻塞式 run')
    assert(typeof MOVE.cleanup === 'function', '有 cleanup')
    assert(
      INVENTORY_MOVE_DEFAULTS.timeoutMs === 15000,
      `INVENTORY_MOVE_DEFAULTS（得到 ${INVENTORY_MOVE_DEFAULTS.timeoutMs}）`,
    )
  }

  console.log('[move-test] 参数校验（槽位范围 9..44 / 整数 / count>=1 / item 非空）')
  {
    const bot = makeBot({})
    const ok = MOVE.validate({ source_slot: 37, destination_slot: 0 + 9, item: ' minecraft:DIRT ', count: 1 })
    assert(
      ok.source_slot === 37 && ok.destination_slot === 9 && ok.item === 'dirt' && ok.count === 1,
      `合法参数归一化（得到 ${JSON.stringify(ok)}）`,
    )
    const badCases = [
      ['source 是 0（合成格）', { source_slot: 0, destination_slot: 9, item: 'dirt', count: 1 }, 'slot.invalid'],
      ['source 是 45（副手）', { source_slot: 45, destination_slot: 9, item: 'dirt', count: 1 }, 'slot.invalid'],
      ['destination 超范围', { source_slot: 9, destination_slot: 99, item: 'dirt', count: 1 }, 'slot.invalid'],
      ['source 小数', { source_slot: 37.5, destination_slot: 9, item: 'dirt', count: 1 }, 'slot.invalid'],
      ['source 不是数字', { source_slot: '37', destination_slot: 9, item: 'dirt', count: 1 }, 'slot.invalid'],
      ['source == destination', { source_slot: 9, destination_slot: 9, item: 'dirt', count: 1 }, 'slot.invalid'],
      ['count 为 0', { source_slot: 37, destination_slot: 9, item: 'dirt', count: 0 }, 'item.invalid'],
      ['count 小数', { source_slot: 37, destination_slot: 9, item: 'dirt', count: 1.5 }, 'item.invalid'],
      ['缺 count', { source_slot: 37, destination_slot: 9, item: 'dirt' }, 'item.invalid'],
      ['空 item', { source_slot: 37, destination_slot: 9, item: '  ', count: 1 }, 'item.invalid'],
    ]
    for (const [label, params, code] of badCases) {
      let got = null
      try {
        MOVE.validate(params)
      } catch (error) {
        got = error.code
      }
      assert(got === code, `${label} → ${code}（得到 ${got}）`)
    }
    // 边界：9 与 44 都必须合法（两端闭区间）
    assert(MOVE.validate({ source_slot: 9, destination_slot: 44, item: 'dirt', count: 1 }).source_slot === 9, 'source 下界 9 合法')
    assert(MOVE.validate({ source_slot: 44, destination_slot: 9, item: 'dirt', count: 1 }).source_slot === 44, 'source 上界 44 合法')
    assert(inventorySlots(bot).hotbar_start === 36, '调试视图带 hotbar_start')
  }

  console.log('[move-test] A/B. 空目标 slot 与同名可堆叠目标都允许')
  {
    // A：空目标
    const empty = makeBot({ slots: { 37: item('dirt', 5, DIRT) } })
    const stateA = await MOVE.start(empty, MOVE.validate({ source_slot: 37, destination_slot: 9, item: 'dirt', count: 2 }))
    assert(stateA.destination_before === null, '空目标：destination_before=null')
    const resultA = await MOVE.wait(empty, MOVE.validate({ source_slot: 37, destination_slot: 9, item: 'dirt', count: 2 }), { cancelled: false }, stateA)
    assert(resultA.source_after.count === 3, `source 5→3（得到 ${resultA.source_after.count}）`)
    assert(resultA.destination_after.count === 2, `destination 0→2（得到 ${resultA.destination_after.count}）`)
    // B：同名可堆叠（目标已有 3 个 dirt）
    const merge = makeBot({ slots: { 37: item('dirt', 5, DIRT), 9: item('dirt', 3, DIRT) } })
    const stateB = await MOVE.start(merge, MOVE.validate({ source_slot: 37, destination_slot: 9, item: 'dirt', count: 2 }))
    assert(stateB.destination_before.count === 3, '合并：destination_before=3')
    const resultB = await MOVE.wait(merge, MOVE.validate({ source_slot: 37, destination_slot: 9, item: 'dirt', count: 2 }), { cancelled: false }, stateB)
    assert(resultB.destination_after.count === 5, `destination 3→5（得到 ${resultB.destination_after.count}）`)
    assert(resultB.source_after.count === 3, `source 5→3（得到 ${resultB.source_after.count}）`)
  }

  console.log('[move-test] C/D/E. 目标被占 / 数量不足 / 物品不符（都是 start 阶段同步拒绝）')
  {
    // C：目标被别的物品占用 → destination.occupied（绝不隐式交换）
    const occupied = makeBot({ slots: { 37: item('dirt', 5, DIRT), 9: item('sand', 2, SAND) } })
    const errorC = await expectCode(
      MOVE.start(occupied, MOVE.validate({ source_slot: 37, destination_slot: 9, item: 'dirt', count: 1 })),
      'destination.occupied',
      '目标被 sand 占用',
    )
    assert(errorC && errorC.detail && errorC.detail.actual === 'sand', '带 actual')
    assert(occupied.transferCalls.length === 0, '没有调用 transfer（不交换）')
    // D：数量不足
    const short = makeBot({ slots: { 37: item('dirt', 2, DIRT) } })
    const errorD = await expectCode(
      MOVE.start(short, MOVE.validate({ source_slot: 37, destination_slot: 9, item: 'dirt', count: 5 })),
      'item.count_insufficient',
      'source 只有 2 个',
    )
    assert(errorD && errorD.detail.available === 2 && errorD.detail.requested === 5, '带 available/requested')
    // E：source 上是别的物品
    const mismatch = makeBot({ slots: { 37: item('sand', 5, SAND) } })
    const errorE = await expectCode(
      MOVE.start(mismatch, MOVE.validate({ source_slot: 37, destination_slot: 9, item: 'dirt', count: 1 })),
      'item.changed',
      'source 是 sand',
    )
    assert(errorE && errorE.detail.expected === 'dirt' && errorE.detail.actual === 'sand', '带 expected/actual')
    // source 是空的
    const emptySource = makeBot({})
    await expectCode(
      MOVE.start(emptySource, MOVE.validate({ source_slot: 37, destination_slot: 9, item: 'dirt', count: 1 })),
      'item.not_found',
      'source 空槽',
    )
  }

  console.log('[move-test] F. 成功移动（source 减、destination 增，按真实状态计算）')
  {
    const bot = makeBot({ slots: { 40: item('sand', 12, SAND), 12: item('dirt', 4, DIRT) } })
    const params = MOVE.validate({ source_slot: 40, destination_slot: 12 + 0, item: 'sand', count: 3 })
    const _ = params
    const startState = await MOVE.start(bot, MOVE.validate({ source_slot: 40, destination_slot: 13, item: 'sand', count: 3 }))
    const result = await MOVE.wait(bot, MOVE.validate({ source_slot: 40, destination_slot: 13, item: 'sand', count: 3 }), { cancelled: false }, startState)
    assert(result.item === 'sand' && result.requested_count === 3, '结果带 item/requested_count')
    assert(result.source_after.count === 9, `source 12→9（得到 ${result.source_after.count}）`)
    assert(result.destination_after.count === 3, `destination 0→3（得到 ${result.destination_after.count}）`)
    const call = bot.transferCalls[0]
    assert(
      call.sourceStart === 40 && call.sourceEnd === 41 && call.destStart === 13 && call.destEnd === 14,
      `transfer 把两端都钉死在单槽（得到 ${JSON.stringify(call)}）`,
    )
  }

  console.log('[move-test] G. transfer resolve 但没动 → move.unconfirmed（不硬编码 +count）')
  {
    const bot = makeBot({ slots: { 40: item('sand', 12, SAND) }, transferImpl: async () => {} })
    const state = await MOVE.start(bot, MOVE.validate({ source_slot: 40, destination_slot: 13, item: 'sand', count: 3 }))
    const error = await expectCode(
      MOVE.wait(bot, MOVE.validate({ source_slot: 40, destination_slot: 13, item: 'sand', count: 3 }), { cancelled: false }, state),
      'move.unconfirmed',
      '什么都没动',
    )
    assert(error && error.detail, `带 source/destination 快照（得到 ${JSON.stringify(error && error.detail)}）`)
    // 目标放的比要求的多（服务器合并行为）：仍然算成功（按真实状态判定）
    const generous = makeBot({
      slots: { 40: item('sand', 12, SAND) },
      transferImpl: async (target, options) => {
        target._slots[options.destStart] = item('sand', 6, SAND)
        target._slots[options.sourceStart].count -= 6
      },
    })
    const state2 = await MOVE.start(generous, MOVE.validate({ source_slot: 40, destination_slot: 13, item: 'sand', count: 3 }))
    const ok = await MOVE.wait(generous, MOVE.validate({ source_slot: 40, destination_slot: 13, item: 'sand', count: 3 }), { cancelled: false }, state2)
    assert(ok.destination_after.count === 6 && ok.source_after.count === 6, '按真实状态判定（多移了也算成功）')
    // 同名**满堆**的目标：start 阶段就拒绝（63 < 64 才允许合并）
    const fullStack = makeBot({
      slots: { 40: item('sand', 64, SAND), 13: item('sand', 64, SAND) },
    })
    await expectCode(
      MOVE.start(fullStack, MOVE.validate({ source_slot: 40, destination_slot: 13, item: 'sand', count: 1 })),
      'destination.occupied',
      '同名满堆（64/64）',
    )
    assert(fullStack.transferCalls.length === 0, '满堆不调用 transfer')
    // transfer 抛 'destination full'（服务器侧放不下）→ 同样映射成 destination.occupied
    const full = makeBot({
      slots: { 40: item('sand', 8, SAND), 13: item('sand', 63, SAND) },
      transferImpl: async () => {
        throw new Error('destination full')
      },
    })
    const state3 = await MOVE.start(full, MOVE.validate({ source_slot: 40, destination_slot: 13, item: 'sand', count: 1 }))
    await expectCode(
      MOVE.wait(full, MOVE.validate({ source_slot: 40, destination_slot: 13, item: 'sand', count: 1 }), { cancelled: false }, state3),
      'destination.occupied',
      'transfer 报 destination full',
    )
  }

  console.log('[move-test] H/I/J. CANCELLED / TIMEOUT / race（cleanup 恰好一次、终态唯一）')
  {
    const slowGate = deferred()
    const slowBot = makeBot({ slots: { 40: item('sand', 4, SAND) }, transferImpl: () => slowGate.promise })
    let cleanups = 0
    const harness = makeHarness({
      ...MOVE,
      cleanup(bot) {
        cleanups += 1
        return MOVE.cleanup(bot)
      },
    })
    harness.state.bot = slowBot
    const started = await harness.runtime.execute('inventory_move', {
      source_slot: 40,
      destination_slot: 13,
      item: 'sand',
      count: 1,
    })
    assert(started.status === 'RUNNING', `启动 RUNNING（得到 ${started.status}）`)
    await new Promise((resolve) => setTimeout(resolve, 10))
    const stop = harness.runtime.stop()
    assert(stop.cancelled.includes(started.action_id), 'stop 取消了 inventory_move')
    await new Promise((resolve) => setTimeout(resolve, 20))
    assert(cleanups === 1, `CANCELLED：cleanup 恰好一次（得到 ${cleanups}）`)

    const hangGate = deferred()
    const hangBot = makeBot({ slots: { 40: item('sand', 4, SAND) }, transferImpl: () => hangGate.promise })
    let hangCleanups = 0
    const hangHarness = makeHarness({
      ...MOVE,
      timeout_ms: 60,
      cleanup(bot) {
        hangCleanups += 1
        return MOVE.cleanup(bot)
      },
    })
    hangHarness.state.bot = hangBot
    const hang = await hangHarness.runtime.execute('inventory_move', {
      source_slot: 40,
      destination_slot: 13,
      item: 'sand',
      count: 1,
    })
    assert(hang.status === 'RUNNING', '超时用例：启动 RUNNING')
    await new Promise((resolve) => setTimeout(resolve, 120))
    assert(hangHarness.runtime.currentView().status === 'TIMEOUT', '终态 TIMEOUT')
    assert(hangCleanups === 1, `TIMEOUT：cleanup 恰好一次（得到 ${hangCleanups}）`)

    const raceGate = deferred()
    const raceBot = makeBot({ slots: { 40: item('sand', 4, SAND) }, transferImpl: () => raceGate.promise })
    let raceCleanups = 0
    const raceHarness = makeHarness({
      ...MOVE,
      cleanup(bot) {
        raceCleanups += 1
        return MOVE.cleanup(bot)
      },
    })
    raceHarness.state.bot = raceBot
    const race = await raceHarness.runtime.execute('inventory_move', {
      source_slot: 40,
      destination_slot: 13,
      item: 'sand',
      count: 1,
    })
    raceHarness.runtime.stop()
    raceGate.resolve()
    await new Promise((resolve) => setTimeout(resolve, 30))
    const raceTerminals = raceHarness.events.filter(
      (e) => e.event.startsWith('minecraft.action.') && e.data.action_id === race.action_id && e.data.status !== 'RUNNING',
    )
    assert(raceTerminals.length === 1, `race：恰好一个终态（得到 ${raceTerminals.length}）`)
    assert(raceCleanups === 1, `race：cleanup 恰好一次（得到 ${raceCleanups}）`)
  }

  clearInterval(keepAlive)
  console.log(`\n[move-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.error(`[move-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[move-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[move-test] crashed:', error)
  process.exit(1)
})
