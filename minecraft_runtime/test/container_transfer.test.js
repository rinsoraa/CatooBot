/**
 * container_transfer 注册与行为测试（Phase 4E · P–AC）。
 *
 * 假 bot 模拟真实容器窗口（§四十一）：openContainer / window / window.slots /
 * window.inventoryStart / window.inventoryEnd / bot.closeWindow / bot.transfer /
 * bot.inventory.slots —— open → validate → transfer → reread → close 整条链都真的跑。
 *
 * 覆盖：
 *   P withdraw   Q deposit   R exact source slot   S exact destination slot
 *   T wrong item   U insufficient count   V destination occupied   W legal stack merge
 *   X post-transfer reread   Y transfer unconfirmed   Z cleanup after error
 *   AA cancellation race   AB timeout race   AC terminal exactly once
 *   + 参数校验 / 双箱 / 窗口消失 / close 失败 / 空 source
 *
 * 运行：node minecraft_runtime/test/container_transfer.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const { ACTION_REGISTRY, CONTAINER_DEFAULTS } = require('../runtime.js')
const { createActionRuntime, STATES } = require('../action_runtime')

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

const TRANSFER = ACTION_REGISTRY.container_transfer
const CHEST_SLOTS = 27
const DIRT = 28
const SAND = 57

function item(name, count, type, stackSize = 64) {
  return { name, count, type, stackSize, metadata: null, nbt: null }
}

function blockAt(name, x = 0, y = 64, z = 0) {
  return { name, position: new Vec3(x, y, z) }
}

function makeWindow({ container = {}, player = {}, type = 'minecraft:generic_9x3' } = {}) {
  const slots = new Array(CHEST_SLOTS + 36).fill(null)
  for (const [slot, value] of Object.entries(container)) slots[Number(slot)] = value
  for (const [slot, value] of Object.entries(player)) {
    slots[CHEST_SLOTS + (Number(slot) - 9)] = value
  }
  return {
    id: 3,
    type,
    slots,
    inventoryStart: CHEST_SLOTS,
    inventoryEnd: CHEST_SLOTS + 36,
    hotbarStart: CHEST_SLOTS + 27,
  }
}

/** 玩家槽位 9..44 → 容器窗口里的绝对槽位 */
function win(slot) {
  return CHEST_SLOTS + (slot - 9)
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

function makeContainerBot(options = {}) {
  const {
    block = blockAt('minecraft:chest'),
    window = makeWindow(),
    openError = null,
    closeError = null,
    transferError = null,
    transferImpl = null,
  } = options
  const inv = {
    slots: new Array(46).fill(null),
    hotbarStart: 36,
    inventoryStart: 9,
    inventoryEnd: 45,
  }
  const bot = {
    entity: { position: new Vec3(0, 64, 0) },
    registry: { items: { [DIRT]: { stackSize: 64 }, [SAND]: { stackSize: 64 } } },
    currentWindow: null,
    inventory: inv,
    openCalls: 0,
    closeCalls: [],
    transferCalls: [],
    blockAt() {
      return block
    },
    async openContainer() {
      bot.openCalls += 1
      if (openError) throw new Error(openError)
      bot.currentWindow = window
      return window
    },
    closeWindow(target) {
      bot.closeCalls.push(target)
      if (closeError) throw new Error(closeError)
      if (bot.currentWindow === target) bot.currentWindow = null
    },
    async transfer(opts) {
      bot.transferCalls.push(opts)
      if (transferImpl) return transferImpl(bot, opts)
      if (transferError) throw new Error(transferError)
      defaultTransfer(bot, opts)
    },
    clearControlStates() {},
  }
  return bot
}

function defaultTransfer(bot, opts) {
  const window = opts.window
  const source = window.slots[opts.sourceStart]
  if (!source) {
    throw new Error(
      `Can't find item in slots [${opts.sourceStart} - ${opts.sourceEnd}], (item id: ${opts.itemType})`,
    )
  }
  if (source.type !== opts.itemType) throw new Error('Invalid itemType')
  let dest = window.slots[opts.destStart]
  if (dest && dest.type !== opts.itemType) throw new Error('destination full')
  const stackSize = (dest && dest.stackSize) || source.stackSize || 64
  let remaining = Math.min(opts.count, source.count)
  let moved = 0
  while (remaining > 0) {
    const room = dest ? stackSize - dest.count : stackSize
    if (room <= 0) break
    const chunk = Math.min(room, remaining)
    if (!dest) {
      dest = { ...source, count: 0 }
      window.slots[opts.destStart] = dest
    }
    dest.count += chunk
    source.count -= chunk
    remaining -= chunk
    moved += chunk
  }
  if (source.count <= 0) window.slots[opts.sourceStart] = null
  if (moved === 0) throw new Error('destination full')
}

function makeHarness(definition = TRANSFER) {
  const events = []
  const logs = []
  const state = { bot: null, online: true }
  const runtime = createActionRuntime({
    registry: { container_transfer: definition },
    getBot: () => state.bot,
    isOnline: () => state.online,
    emit: (event, data) => events.push({ event, data }),
    log: (message) => logs.push(message),
    now: () => Date.now(),
  })
  return { runtime, events, logs, state }
}

const BASE = {
  x: 0,
  y: 64,
  z: 0,
  direction: 'withdraw',
  container_slot: 0,
  inventory_slot: 9,
  item: 'dirt',
  count: 1,
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function terminals(events, actionId) {
  return events.filter(
    (row) => row.event.startsWith('minecraft.action.') && row.data.action_id === actionId &&
      ['minecraft.action.completed', 'minecraft.action.failed', 'minecraft.action.cancelled', 'minecraft.action.timeout'].includes(row.event),
  )
}

function completedResult(events, actionId) {
  const row = events.find(
    (entry) => entry.event === 'minecraft.action.completed' && entry.data.action_id === actionId,
  )
  return row ? row.data.result : null
}

async function startAndSettle(harness, params, timeoutMs = 2000) {
  const started = await harness.runtime.execute('container_transfer', params)
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const rows = terminals(harness.events, started.action_id)
    if (rows.length > 0) return { started, terminal: rows[0] }
    await sleep(10)
  }
  console.error(
    `[transfer-test] 等待终态超时：action_id=${started.action_id} 事件=${JSON.stringify(
      harness.events.map((row) => `${row.event}:${row.data && row.data.action_id}`),
    )} 日志=${JSON.stringify(harness.logs.slice(-4))}`,
  )
  return { started, terminal: null }
}

async function main() {
  setTimeout(() => {
    console.error('[transfer-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[transfer-test] 注册表属性与参数校验')
  {
    assert(Boolean(TRANSFER), 'container_transfer 已注册')
    assert(TRANSFER.risk === 'MEDIUM', `risk = MEDIUM（得到 ${TRANSFER.risk}）`)
    assert(TRANSFER.exclusive === true, 'exclusive = true')
    assert(TRANSFER.detached === true, 'detached = true（启动即 RUNNING）')
    assert(typeof TRANSFER.validate === 'function', '有参数校验')
    assert(typeof TRANSFER.start === 'function' && typeof TRANSFER.wait === 'function', '有 start/wait')
    assert(typeof TRANSFER.cleanup === 'function', '有 cleanup')
    assert(
      CONTAINER_DEFAULTS.directions.join(',') === 'withdraw,deposit',
      'direction 只有 withdraw / deposit',
    )

    const ok = TRANSFER.validate({ ...BASE, direction: ' WITHDRAW ', item: ' minecraft:DIRT ' })
    assert(
      ok.direction === 'withdraw' && ok.item === 'dirt' && ok.inventory_slot === 9,
      `合法参数归一化（得到 ${JSON.stringify(ok)}）`,
    )
    const badCases = [
      ['direction 非法', { ...BASE, direction: 'take' }, 'action.invalid'],
      ['direction 缺失', { ...BASE, direction: undefined }, 'action.invalid'],
      ['container_slot 负数', { ...BASE, container_slot: -1 }, 'slot.invalid'],
      ['container_slot 小数', { ...BASE, container_slot: 1.5 }, 'slot.invalid'],
      ['container_slot 不是数字', { ...BASE, container_slot: '0' }, 'slot.invalid'],
      ['inventory_slot 8（合成格）', { ...BASE, inventory_slot: 8 }, 'slot.invalid'],
      ['inventory_slot 45（副手）', { ...BASE, inventory_slot: 45 }, 'slot.invalid'],
      ['count 为 0', { ...BASE, count: 0 }, 'item.invalid'],
      ['count 小数', { ...BASE, count: 2.5 }, 'item.invalid'],
      ['缺 count', { ...BASE, count: undefined }, 'item.invalid'],
      ['空 item', { ...BASE, item: '   ' }, 'item.invalid'],
      ['item 过长', { ...BASE, item: 'x'.repeat(80) }, 'item.invalid'],
      ['x 小数', { ...BASE, x: 0.5 }, 'action.invalid'],
      ['y 超边界', { ...BASE, y: 4096 }, 'action.invalid'],
    ]
    for (const [label, params, code] of badCases) {
      let got = null
      try {
        TRANSFER.validate(params)
      } catch (error) {
        got = error.code
      }
      assert(got === code, `${label} → ${code}（得到 ${got}）`)
    }
  }

  console.log('[transfer-test] P/R/S/X. withdraw：单槽 → 单槽、真实重读、关窗')
  {
    const window = makeWindow({
      container: { 0: item('minecraft:dirt', 12, DIRT), 7: item('sand', 32, SAND) },
      player: { 10: item('stone', 4, 1) },
    })
    const bot = makeContainerBot({ window })
    const harness = makeHarness()
    harness.state.bot = bot
    const { started, terminal } = await startAndSettle(harness, BASE)
    assert(started.status === STATES.RUNNING && Boolean(started.action_id), 'P. 启动即 RUNNING + action_id')
    assert(
      terminal && terminal.event === 'minecraft.action.completed',
      `P. 终态 completed（得到 ${terminal && terminal.event} ${terminal && terminal.data.code} ${
        terminal && terminal.data.error
      }）`,
    )
    const result = completedResult(harness.events, started.action_id)
    assert(result && result.direction === 'withdraw', 'P. result.direction = withdraw')
    assert(
      result.container_before.name === 'dirt' && result.container_before.count === 12,
      `P. container_before（得到 ${JSON.stringify(result.container_before)}）`,
    )
    assert(
      result.container_after.name === 'dirt' && result.container_after.count === 11,
      `P. container_after 真实重读（得到 ${JSON.stringify(result.container_after)}）`,
    )
    assert(result.inventory_before === null, 'P. inventory_before = null（原来空着）')
    assert(
      result.inventory_after.name === 'dirt' && result.inventory_after.count === 1,
      `P. inventory_after（得到 ${JSON.stringify(result.inventory_after)}）`,
    )
    assert(
      result.container_slot === 0 && result.inventory_slot === 9 && result.count === 1,
      'P. 结果带明确槽位与数量',
    )
    assert(
      result.container_type === 'chest' &&  // 方块名归一化（1.16.x 的 mineflayer 给的是 chest）
        JSON.stringify(result.position) === JSON.stringify({ x: 0, y: 64, z: 0 }),
      'P. 结果带容器类型与坐标',
    )
    assert(result.moved_out === 1 && result.gained_in === 1, `P. 真实变化量（${result.moved_out}/${result.gained_in}）`)
    const call = bot.transferCalls[0]
    assert(call.window === window, 'R/S. transfer 用的是打开的那个 window')
    assert(call.itemType === DIRT && call.count === 1, 'R/S. transfer 带 itemType 与 count')
    assert(call.sourceStart === 0 && call.sourceEnd === 1, 'R. source 钉死在单槽 0')
    assert(
      call.destStart === win(9) && call.destEnd === win(9) + 1,
      `S. destination 钉死在单槽 ${win(9)}（得到 ${call.destStart}~${call.destEnd}）`,
    )
    assert(bot.closeCalls.length === 1 && bot.currentWindow === null, 'X. 完成后窗口已关')
    assert(
      JSON.stringify(window.slots[0]) === JSON.stringify(item('minecraft:dirt', 11, DIRT)) &&
        JSON.stringify(window.slots[win(9)]) === JSON.stringify(item('minecraft:dirt', 1, DIRT)),
      'X. 真实窗口内容确实变了',
    )
  }

  console.log('[transfer-test] Q. deposit：背包槽 → 容器槽')
  {
    const window = makeWindow({ container: {}, player: { 9: item('dirt', 3, DIRT) } })
    const bot = makeContainerBot({ window })
    const harness = makeHarness()
    harness.state.bot = bot
    const params = { ...BASE, direction: 'deposit', container_slot: 5, inventory_slot: 9, count: 2 }
    const { started, terminal } = await startAndSettle(harness, params)
    assert(terminal && terminal.event === 'minecraft.action.completed', 'Q. 终态 completed')
    const result = completedResult(harness.events, started.action_id)
    assert(result.direction === 'deposit', 'Q. direction = deposit')
    assert(
      result.inventory_before.count === 3 && result.inventory_after.count === 1,
      `Q. inventory 3 → 1（得到 ${JSON.stringify(result.inventory_before)} → ${JSON.stringify(result.inventory_after)}）`,
    )
    assert(
      result.container_before === null && result.container_after.count === 2,
      `Q. container null → 2（得到 ${JSON.stringify(result.container_after)}）`,
    )
    const call = bot.transferCalls[0]
    assert(call.sourceStart === win(9), `Q. source = 背包槽 ${win(9)}（得到 ${call.sourceStart}）`)
    assert(call.destStart === 5 && call.destEnd === 6, `Q. destination = 容器槽 5（得到 ${call.destStart}）`)
    assert(window.slots[5].count === 2 && window.slots[win(9)].count === 1, 'Q. 窗口内容真实改变')
    assert(bot.closeCalls.length === 1, 'Q. 完成后关窗')
  }

  console.log('[transfer-test] W. 同名可堆叠：允许合并（容量来自真实 stackSize）')
  {
    const window = makeWindow({
      container: { 0: item('dirt', 12, DIRT) },
      player: { 9: item('dirt', 63, DIRT) },
    })
    const bot = makeContainerBot({ window })
    const harness = makeHarness()
    harness.state.bot = bot
    const { started, terminal } = await startAndSettle(harness, BASE)
    assert(terminal && terminal.event === 'minecraft.action.completed', 'W. 合并成功')
    const result = completedResult(harness.events, started.action_id)
    assert(
      result.inventory_after.count === 64 && result.container_after.count === 11,
      `W. 63+1=64（得到 ${JSON.stringify(result.inventory_after)} / ${JSON.stringify(result.container_after)}）`,
    )

    // 堆已满 → 拒绝（capacity 来自 item.stackSize，不是硬编码 64 以外的猜测）
    const fullWindow = makeWindow({
      container: { 0: item('dirt', 12, DIRT) },
      player: { 9: item('dirt', 64, DIRT) },
    })
    const fullBot = makeContainerBot({ window: fullWindow })
    const fullHarness = makeHarness()
    fullHarness.state.bot = fullBot
    let got = null
    try {
      await fullHarness.runtime.execute('container_transfer', BASE)
    } catch (error) {
      got = error.code
    }
    assert(got === 'destination.occupied', `W. 堆已满 → destination.occupied（得到 ${got}）`)
    assert(fullBot.transferCalls.length === 0, 'W. 拒绝时不调用 transfer')
    assert(fullBot.closeCalls.length === 1, 'W. 拒绝后窗口也关掉')
  }

  console.log('[transfer-test] T/U/V. 预检查：物品不符 / 数量不够 / 目标被占 / 空 source')
  {
    const cases = [
      [
        'T. 容器里是 sand，要拿 dirt',
        { container: { 0: item('sand', 5, SAND) } },
        BASE,
        'item.changed',
      ],
      [
        'U. 只有 1 个，要拿 3 个',
        { container: { 0: item('dirt', 1, DIRT) } },
        { ...BASE, count: 3 },
        'item.count_insufficient',
      ],
      [
        'V. 背包目标槽被别的物品占用',
        { container: { 0: item('dirt', 5, DIRT) }, player: { 9: item('sand', 2, SAND) } },
        BASE,
        'destination.occupied',
      ],
      ['空 source（容器槽 0 是空的）', { container: {} }, BASE, 'item.not_found'],
    ]
    for (const [label, layout, params, code] of cases) {
      const window = makeWindow(layout)
      const bot = makeContainerBot({ window })
      const harness = makeHarness()
      harness.state.bot = bot
      let got = null
      try {
        await harness.runtime.execute('container_transfer', params)
      } catch (error) {
        got = error.code
      }
      assert(got === code, `${label} → ${code}（得到 ${got}）`)
      assert(bot.transferCalls.length === 0, `${label}：绝不调用 transfer`)
      assert(
        bot.closeCalls.length === 1 && bot.currentWindow === null,
        `${label}：start 失败也关窗（close ${bot.closeCalls.length} 次）`,
      )
    }

    // deposit 方向的目标占用（容器槽被别的物品占着）
    const window = makeWindow({ container: { 5: item('sand', 9, SAND) }, player: { 9: item('dirt', 2, DIRT) } })
    const bot = makeContainerBot({ window })
    const harness = makeHarness()
    harness.state.bot = bot
    let got = null
    try {
      await harness.runtime.execute('container_transfer', {
        ...BASE,
        direction: 'deposit',
        container_slot: 5,
      })
    } catch (error) {
      got = error.code
    }
    assert(got === 'destination.occupied', `V. deposit 目标被占 → destination.occupied（得到 ${got}）`)
    assert(bot.transferCalls.length === 0, 'V. deposit 目标被占绝不调用 transfer')
  }

  console.log('[transfer-test] container_slot 超范围 / 双箱 / 未加载方块')
  {
    const window = makeWindow({ container: { 0: item('dirt', 1, DIRT) } })
    const bot = makeContainerBot({ window })
    const harness = makeHarness()
    harness.state.bot = bot
    let got = null
    try {
      await harness.runtime.execute('container_transfer', { ...BASE, container_slot: 27 })
    } catch (error) {
      got = error.code
    }
    assert(got === 'slot.invalid', `container_slot 27（越界）→ slot.invalid（得到 ${got}）`)
    assert(bot.closeCalls.length === 1, '越界后窗口已关')

    const doubleSlots = new Array(54 + 36).fill(null)
    doubleSlots[0] = item('dirt', 1, DIRT)
    const doubleWindow = {
      id: 5,
      type: 'minecraft:generic_9x6',
      slots: doubleSlots,
      inventoryStart: 54,
      inventoryEnd: 90,
      hotbarStart: 81,
    }
    const doubleBot = makeContainerBot({ window: doubleWindow })
    const harness2 = makeHarness()
    harness2.state.bot = doubleBot
    got = null
    try {
      await harness2.runtime.execute('container_transfer', BASE)
    } catch (error) {
      got = error.code
    }
    assert(got === 'container.unsupported', `双箱 → container.unsupported（得到 ${got}）`)
    assert(doubleBot.closeCalls.length === 1, '双箱拒绝后窗口已关')
  }

  console.log('[transfer-test] Y/X. transfer 完成但真实状态没变 → container.transfer_unconfirmed')
  {
    const window = makeWindow({ container: { 0: item('dirt', 12, DIRT) } })
    // transfer 什么都不做（resolve 了，但世界没变）—— 绝不能报成功
    const bot = makeContainerBot({ window, transferImpl: async () => {} })
    const harness = makeHarness()
    harness.state.bot = bot
    const { started, terminal } = await startAndSettle(harness, BASE)
    assert(terminal && terminal.event === 'minecraft.action.failed', 'Y. 终态 failed')
    assert(terminal.data.code === 'container.transfer_unconfirmed', `Y. code（得到 ${terminal.data.code}）`)
    assert(bot.closeCalls.length === 1, 'Y. 失败路径也关窗')
    const failedEvent = harness.events.find(
      (row) => row.event === 'minecraft.action.failed' && row.data.action_id === started.action_id,
    )
    assert(
      failedEvent && failedEvent.data.error && failedEvent.data.error.includes('移出 0'),
      `Y. 错误信息带真实变化量（得到 ${failedEvent && failedEvent.data.error}）`,
    )

    // 真实移动到更多（>= count）按真实数字如实上报，不硬编码 ±1
    const window2 = makeWindow({ container: { 0: item('dirt', 12, DIRT) } })
    const bot2 = makeContainerBot({
      window: window2,
      transferImpl: async (innerBot, opts) => {
        const source = opts.window.slots[opts.sourceStart]
        source.count -= 2
        opts.window.slots[opts.destStart] = item('dirt', 2, DIRT)
      },
    })
    const harness2 = makeHarness()
    harness2.state.bot = bot2
    const settled = await startAndSettle(harness2, BASE)
    assert(settled.terminal && settled.terminal.event === 'minecraft.action.completed', 'X. 真实变化 ≥ 请求量 → 成功')
    const result2 = completedResult(harness2.events, settled.started.action_id)
    assert(
      result2.moved_out === 2 && result2.gained_in === 2,
      `X. 如实上报真实变化（得到 ${result2.moved_out}/${result2.gained_in}）`,
    )
  }

  console.log('[transfer-test] J/Z. transfer 抛错 / 窗口消失 / close 失败')
  {
    // destination full
    const window = makeWindow({ container: { 0: item('dirt', 5, DIRT) } })
    const bot = makeContainerBot({ window, transferError: 'destination full' })
    const harness = makeHarness()
    harness.state.bot = bot
    const { terminal } = await startAndSettle(harness, BASE)
    assert(
      terminal && terminal.data.code === 'destination.occupied',
      `destination full → destination.occupied（得到 ${terminal && terminal.data.code}）`,
    )
    assert(bot.closeCalls.length === 1, 'Z. transfer 失败也关窗')

    // 普通失败
    const window2 = makeWindow({ container: { 0: item('dirt', 5, DIRT) } })
    const bot2 = makeContainerBot({ window: window2, transferError: 'boom' })
    const harness2 = makeHarness()
    harness2.state.bot = bot2
    const settled2 = await startAndSettle(harness2, BASE)
    assert(
      settled2.terminal && settled2.terminal.data.code === 'action.failed',
      `普通 transfer 失败 → action.failed（得到 ${settled2.terminal && settled2.terminal.data.code}）`,
    )

    // 窗口在搬运过程中被关掉（§三十二）
    const window3 = makeWindow({ container: { 0: item('dirt', 5, DIRT) } })
    const bot3 = makeContainerBot({
      window: window3,
      transferImpl: async (innerBot) => {
        innerBot.currentWindow = null
        throw new Error('window closed by server')
      },
    })
    const harness3 = makeHarness()
    harness3.state.bot = bot3
    const settled3 = await startAndSettle(harness3, BASE)
    assert(
      settled3.terminal && settled3.terminal.data.code === 'container.closed',
      `窗口消失 → container.closed（得到 ${settled3.terminal && settled3.terminal.data.code}）`,
    )

    // close 失败（世界已变但关不上）→ container.close_failed，并如实带上结果
    const window4 = makeWindow({
      container: { 0: item('dirt', 5, DIRT) },
      player: { 9: item('dirt', 1, DIRT) },
    })
    const bot4 = makeContainerBot({ window: window4, closeError: 'socket dead' })
    const harness4 = makeHarness()
    harness4.state.bot = bot4
    const settled4 = await startAndSettle(harness4, BASE)
    assert(
      settled4.terminal && settled4.terminal.data.code === 'container.close_failed',
      `close 失败 → container.close_failed（得到 ${settled4.terminal && settled4.terminal.data.code}）`,
    )
    assert(
      settled4.terminal.data.error && settled4.terminal.data.error.includes('socket dead'),
      'close 错误信息如实上报（不吞掉）',
    )
  }

  console.log('[transfer-test] AA/AB/AC. 取消竞态 / 超时竞态 / 终态恰好一次')
  {
    // AA：transfer 挂住 → STOP → 之后底层才 resolve → 只能 CANCELLED
    const hang = deferred()
    const window = makeWindow({ container: { 0: item('dirt', 12, DIRT) } })
    const bot = makeContainerBot({ window, transferImpl: () => hang.promise })
    const harness = makeHarness()
    harness.state.bot = bot
    const started = await harness.runtime.execute('container_transfer', BASE)
    await sleep(30)
    const stopResult = harness.runtime.stop()
    assert(stopResult.cancelled.length === 1, 'AA. STOP 取消了动作')
    hang.resolve(null) // 底层"完成了"，但终态已经落定
    await sleep(60)
    assert(
      terminals(harness.events, started.action_id).length === 1,
      `AC. 终态恰好一次（得到 ${terminals(harness.events, started.action_id).length}）`,
    )
    assert(
      terminals(harness.events, started.action_id)[0].event === 'minecraft.action.cancelled',
      'AA. 终态只能是 CANCELLED（绝不双终态）',
    )
    assert(bot.closeCalls.length === 1, 'AA. 取消后窗口已关（恰好一次）')

    // AB：超时竞态（transfer 一直挂着）
    const window2 = makeWindow({ container: { 0: item('dirt', 12, DIRT) } })
    const bot2 = makeContainerBot({ window: window2, transferImpl: () => deferred().promise })
    const harness2 = makeHarness({ ...TRANSFER, timeout_ms: 120 })
    harness2.state.bot = bot2
    await harness2.runtime.execute('container_transfer', BASE)
    await sleep(400)
    const rows = termsFor(harness2.events)
    assert(rows.length === 1 && rows[0].event === 'minecraft.action.timeout', `AB. 超时 → TIMEOUT（得到 ${rows[0] && rows[0].event}）`)
    assert(bot2.closeCalls.length === 1, 'AB. 超时后窗口已关（恰好一次）')
  }

  clearInterval(keepAlive)
  console.log('')
  console.log(`[transfer-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.log(`[transfer-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[transfer-test] ALL CHECKS PASSED')
}

/** 该 harness 上所有终态事件（只有一个动作时用）。 */
function termsFor(events) {
  return events.filter((row) =>
    ['minecraft.action.completed', 'minecraft.action.failed', 'minecraft.action.cancelled', 'minecraft.action.timeout'].includes(row.event),
  )
}

main().catch((error) => {
  console.error('[transfer-test] crashed:', error)
  process.exit(1)
})
