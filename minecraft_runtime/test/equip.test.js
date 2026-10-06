/**
 * equip 注册与行为测试（Phase 4D）：参数校验 / 确定性选槽 / start 阶段校验
 * （item 不存在 / 已手持）/ wait 阶段复核（equip_unconfirmed）/ cleanup /
 * CANCELLED·TIMEOUT·race（真实 equip 动作 + 假 bot 直跑 ActionRuntime）。
 *
 * 假 bot 按 mineflayer 玩家窗口语义建模：槽位 9..44（36..44 = 快捷栏），
 * heldItem = slots[36 + quickBarSlot]。
 *
 * 运行：node minecraft_runtime/test/equip.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const {
  ACTION_REGISTRY,
  EQUIP_DEFAULTS,
  PLAYER_WINDOW_SLOTS,
  findInventoryItem,
  inventorySlotBounds,
  inventorySlots,
  normalizeItemName,
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

const EQUIP = ACTION_REGISTRY.equip

function item(name, count, type) {
  return { name, count, type, stackSize: 64, metadata: null, nbt: null }
}

/** 假 bot：46 个窗口槽位；equip/transfer 会真的改动这份假 inventory。 */
function makeBot({ slots = {}, quickBarSlot = 0, equipImpl = null } = {}) {
  const inv = {
    slots: new Array(46).fill(null),
    hotbarStart: PLAYER_WINDOW_SLOTS.hotbarStart,
    inventoryStart: PLAYER_WINDOW_SLOTS.inventoryStart,
    inventoryEnd: PLAYER_WINDOW_SLOTS.inventoryEnd,
  }
  for (const [slot, value] of Object.entries(slots)) inv.slots[Number(slot)] = value
  const bot = {
    entity: { position: new Vec3(0, 64, 0) },
    quickBarSlot,
    inventory: inv,
    cleared: 0,
    equipCalls: 0,
    get heldItem() {
      return inv.slots[PLAYER_WINDOW_SLOTS.hotbarStart + this.quickBarSlot] || null
    },
    async equip(itemType) {
      bot.equipCalls += 1
      if (equipImpl) return equipImpl(bot, itemType)
      // 模拟 mineflayer：把该 type 的物品与主手槽交换
      const target = PLAYER_WINDOW_SLOTS.hotbarStart + bot.quickBarSlot
      let found = -1
      for (let slot = 9; slot < 45; slot += 1) {
        const candidate = inv.slots[slot]
        if (candidate && candidate.type === itemType) {
          found = slot
          break
        }
      }
      if (found < 0) throw new Error('Item not found')
      const swap = inv.slots[target]
      inv.slots[target] = inv.slots[found]
      inv.slots[found] = swap
    },
    clearControlStates() {
      bot.cleared += 1
    },
    _slots: inv.slots,
  }
  return bot
}

function makeHarness(definition = EQUIP) {
  const events = []
  const logs = []
  const state = { online: true }
  const runtime = createActionRuntime({
    registry: { equip: definition },
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
    console.error('[equip-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[equip-test] equip 注册表属性')
  {
    assert(Boolean(EQUIP), 'equip 已注册')
    assert(EQUIP.exclusive === true, 'exclusive = true（改主手会改变动作语义 → 占用前台）')
    assert(EQUIP.risk === 'MEDIUM', `risk = MEDIUM（得到 ${EQUIP.risk}）`)
    assert(EQUIP.timeout_ms === 15000, `默认 timeout = 15s（得到 ${EQUIP.timeout_ms}）`)
    assert(EQUIP.detached === true, 'detached = true（启动即 RUNNING，终态经事件）')
    assert(typeof EQUIP.validate === 'function', '有参数校验')
    assert(typeof EQUIP.start === 'function' && typeof EQUIP.wait === 'function', '有 start/wait')
    assert(typeof EQUIP.run !== 'function', '没有阻塞式 run')
    assert(typeof EQUIP.cleanup === 'function', '有 cleanup')
    assert(EQUIP_DEFAULTS.timeoutMs === 15000, `EQUIP_DEFAULTS.timeoutMs（得到 ${EQUIP_DEFAULTS.timeoutMs}）`)
    assert(
      Object.keys(ACTION_REGISTRY).sort().join(',') ===
        'chat,container_inspect,container_transfer,craft,dig,dropped_items,equip,follow_player,inventory_move,look_at,move_to,pickup_item,place,recipe_lookup,stop',
      `注册表只有已批准动作（得到 ${Object.keys(ACTION_REGISTRY).sort().join(',')}）`,
    )
    // §二 禁止清单：容器 / 掉落 / 合成 / 交易 / 批量整理类动作一个都不许有
    for (const forbidden of [
      'open_container',
      'container',
      'chest',
      'furnace',
      'drop',
      'toss',
      'pickup',
      'craft_all',
      'auto_craft',
      'craft_chain',
      'crafting_table',
      'smelt',
      'trade',
      'sort_inventory',
      'auto_equip',
    ]) {
      assert(ACTION_REGISTRY[forbidden] === undefined, `未注册 ${forbidden}`)
    }
  }

  console.log('[equip-test] 参数校验 + 名字归一化（dirt ≡ minecraft:dirt）')
  {
    const ok = EQUIP.validate({ item: '  minecraft:DIrt ' })
    assert(ok.item === 'dirt', `item 归一化（得到 ${ok.item}）`)
    for (const [label, params, code] of [
      ['缺 item', {}, 'item.invalid'],
      ['空 item', { item: '   ' }, 'item.invalid'],
      ['item 不是字符串', { item: 42 }, 'item.invalid'],
      ['item 过长', { item: 'x'.repeat(65) }, 'item.invalid'],
    ]) {
      let got = null
      try {
        EQUIP.validate(params)
      } catch (error) {
        got = error.code
      }
      assert(got === code, `${label} → ${code}（得到 ${got}）`)
    }
    assert(normalizeItemName('minecraft:dirt') === 'dirt', 'normalizeItemName 去前缀')
  }

  console.log('[equip-test] 槽位辅助（§十二：不假设 0 = 第一格快捷栏）')
  {
    const bot = makeBot({ slots: { 12: item('dirt', 3, 28), 40: item('sand', 5, 57) } })
    const bounds = inventorySlotBounds(bot)
    assert(bounds.start === 9 && bounds.hotbarStart === 36 && bounds.last === 44, `9..44（得到 ${JSON.stringify(bounds)}）`)
    const view = inventorySlots(bot)
    assert(
      view.slots.length === 2 && view.slots.every((row) => row.slot >= 9 && row.slot <= 44),
      `调试槽位视图只含 9..44（得到 ${JSON.stringify(view.slots)}）`,
    )
    assert(
      view.slots.find((row) => row.slot === 40).hotbar === true &&
        view.slots.find((row) => row.slot === 12).hotbar === false,
      '槽位视图标注 hotbar',
    )
    // 确定性选槽：同名物品取**槽位最小**的那个（不随机、不按数量、不换槽）
    const found = findInventoryItem(bot, 'minecraft:dirt')
    assert(found && found.slot === 12, `同名物品取最小槽位（得到 ${found && found.slot}）`)
    const bot2 = makeBot({ slots: { 40: item('dirt', 64, 28), 12: item('dirt', 1, 28) } })
    assert(findInventoryItem(bot2, 'dirt').slot === 12, '数量更大的 stack 不优先（仍是槽位顺序）')
    assert(findInventoryItem(bot, 'gold') === null, '不存在的物品返回 null')
    assert(inventorySlots(null).online === false, '离线时槽位视图为空')
  }

  console.log('[equip-test] A. 物品存在 → 装备成功（结果带 source_slot / held_item）')
  {
    const bot = makeBot({ slots: { 12: item('dirt', 12, 28), 40: item('sand', 5, 57) } })
    const state = await EQUIP.start(bot, EQUIP.validate({ item: 'minecraft:dirt' }))
    assert(state.source_slot === 12 && state.already_equipped === false, 'start 返回 source_slot=12')
    const result = await EQUIP.wait(bot, EQUIP.validate({ item: 'dirt' }), { cancelled: false }, state)
    assert(result.item === 'dirt' && result.destination === 'hand', '结果带 item/destination')
    assert(result.held_item.name === 'dirt' && result.held_item.count === 12, '结果带 held_item')
    assert(result.already_equipped === false, 'already_equipped=false')
    assert(bot.heldItem && bot.heldItem.name === 'dirt', '主手真的换成了 dirt')
  }

  console.log('[equip-test] B. 物品不存在 → item.not_found（不装备）')
  {
    const bot = makeBot({ slots: { 12: item('sand', 1, 57) } })
    await expectCode(EQUIP.start(bot, EQUIP.validate({ item: 'dirt' })), 'item.not_found', '背包里没有 dirt')
    assert(bot.equipCalls === 0, '没有调用 equip')
  }

  console.log('[equip-test] C. 已经拿着 → already_equipped（仍实时读 heldItem，不看缓存）')
  {
    const bot = makeBot({ slots: { 36: item('dirt', 12, 28), 12: item('sand', 1, 57) } })
    const state = await EQUIP.start(bot, EQUIP.validate({ item: 'minecraft:dirt' }))
    assert(state.already_equipped === true && state.source_slot === null, 'start 判定 already_equipped')
    const result = await EQUIP.wait(bot, EQUIP.validate({ item: 'dirt' }), { cancelled: false }, state)
    assert(result.already_equipped === true, '结果 already_equipped=true')
    assert(bot.equipCalls === 0, '不重复调用 equip')
    // 主手物品数量为 0 时不算"已装备"
    const empty = makeBot({ slots: { 36: item('dirt', 0, 28), 12: item('dirt', 4, 28) } })
    const state2 = await EQUIP.start(empty, EQUIP.validate({ item: 'dirt' }))
    assert(state2.already_equipped === false && state2.source_slot === 12, '数量为 0 → 走正常装备')
  }

  console.log('[equip-test] D. equip 抛错 → action.failed（不暴露原始堆栈）')
  {
    const bot = makeBot({
      slots: { 12: item('dirt', 1, 28) },
      equipImpl: async () => {
        throw new Error('server rejected the click')
      },
    })
    const state = await EQUIP.start(bot, EQUIP.validate({ item: 'dirt' }))
    const error = await expectCode(
      EQUIP.wait(bot, EQUIP.validate({ item: 'dirt' }), { cancelled: false }, state),
      'action.failed',
      'equip 抛错',
    )
    assert(error && !String(error.message).includes('at '), '错误里没有堆栈')
  }

  console.log('[equip-test] E. equip resolve 但没换成 → equip_unconfirmed（带 expected/actual）')
  {
    const bot = makeBot({
      slots: { 12: item('dirt', 1, 28), 36: item('sand', 3, 57) },
      equipImpl: async () => {},
    })
    const state = await EQUIP.start(bot, EQUIP.validate({ item: 'dirt' }))
    const error = await expectCode(
      EQUIP.wait(bot, EQUIP.validate({ item: 'dirt' }), { cancelled: false }, state),
      'equip.unconfirmed',
      '装备后主手没变',
    )
    assert(
      error && error.detail && error.detail.expected === 'dirt' && error.detail.actual === 'sand',
      `带 expected/actual（得到 ${JSON.stringify(error && error.detail)}）`,
    )
  }

  console.log('[equip-test] F/G/H. CANCELLED / TIMEOUT / race（cleanup 恰好一次、终态唯一）')
  {
    // F：装备中 STOP
    const slowGate = deferred()
    const slowBot = makeBot({
      slots: { 12: item('dirt', 1, 28) },
      equipImpl: () => slowGate.promise,
    })
    let cleanups = 0
    const harness = makeHarness({
      ...EQUIP,
      cleanup(bot) {
        cleanups += 1
        return EQUIP.cleanup(bot)
      },
    })
    harness.state.bot = slowBot
    const started = await harness.runtime.execute('equip', { item: 'dirt' })
    assert(started.status === 'RUNNING', `启动返回 RUNNING（得到 ${started.status}）`)
    await new Promise((resolve) => setTimeout(resolve, 10))
    const stop = harness.runtime.stop()
    assert(stop.cancelled.includes(started.action_id), 'stop 取消了 equip')
    await new Promise((resolve) => setTimeout(resolve, 20))
    assert(cleanups === 1, `CANCELLED：cleanup 恰好一次（得到 ${cleanups}）`)
    const terminalF = harness.events.filter(
      (e) => e.event.startsWith('minecraft.action.') && e.data.action_id === started.action_id && e.data.status !== 'RUNNING',
    )
    assert(terminalF.length === 1 && terminalF[0].event === 'minecraft.action.cancelled', '终态唯一且为 cancelled')

    // G：超时 → TIMEOUT + cleanup
    const hangGate = deferred()
    const hangBot = makeBot({ slots: { 12: item('dirt', 1, 28) }, equipImpl: () => hangGate.promise })
    let hangCleanups = 0
    const hangHarness = makeHarness({
      ...EQUIP,
      timeout_ms: 60,
      cleanup(bot) {
        hangCleanups += 1
        return EQUIP.cleanup(bot)
      },
    })
    hangHarness.state.bot = hangBot
    const hang = await hangHarness.runtime.execute('equip', { item: 'dirt' })
    assert(hang.status === 'RUNNING', '超时用例：启动 RUNNING')
    await new Promise((resolve) => setTimeout(resolve, 120))
    assert(hangHarness.runtime.currentView().status === 'TIMEOUT', '终态 TIMEOUT')
    assert(hangCleanups === 1, `TIMEOUT：cleanup 恰好一次（得到 ${hangCleanups}）`)

    // H：race —— stop 与 equip 同时落定
    const raceGate = deferred()
    const raceBot = makeBot({ slots: { 12: item('dirt', 1, 28) }, equipImpl: () => raceGate.promise })
    let raceCleanups = 0
    const raceHarness = makeHarness({
      ...EQUIP,
      cleanup(bot) {
        raceCleanups += 1
        return EQUIP.cleanup(bot)
      },
    })
    raceHarness.state.bot = raceBot
    const race = await raceHarness.runtime.execute('equip', { item: 'dirt' })
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
  console.log(`\n[equip-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.error(`[equip-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[equip-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[equip-test] crashed:', error)
  process.exit(1)
})
