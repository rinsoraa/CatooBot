/**
 * container_inspect / container 窗口生命周期测试（Phase 4E · A–J / K–O）。
 *
 * 假 bot 模拟完整的容器窗口生命周期（§四十一）：openContainer / window / window.slots /
 * window.inventoryStart / bot.closeWindow / bot.transfer / bot.inventory.slots —— 不是
 * 只把 transfer 换成空函数，这样 open → read → close 整条链才真的被测试。
 *
 * 覆盖：
 *   A open success   B open failure   C inspect read   D close after success
 *   E close after failure   F close after timeout   G close after cancellation
 *   H disconnect while window open   I cleanup exactly once   J unexpected close
 *   K chest   L barrel   M trapped chest reject   N double chest reject
 *   O unsupported block reject   + 距离 / 缺方块 / 参数校验 / cleanup 契约
 *
 * 运行：node minecraft_runtime/test/container.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const {
  ACTION_REGISTRY,
  CONTAINER_BLOCK_TYPES,
  CONTAINER_DEFAULTS,
  containerSlotCountOf,
  containerTypeLabel,
  describeItem,
  itemStackCapacity,
  readContainerSlots,
  requireSingleContainerWindow,
  windowSlotForInventorySlot,
} = require('../runtime.js')
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

const INSPECT = ACTION_REGISTRY.container_inspect
const TRANSFER = ACTION_REGISTRY.container_transfer

const CHEST_SLOTS = 27
const DIRT = 28
const SAND = 57
const STONE = 1

function item(name, count, type) {
  return { name, count, type, stackSize: 64, metadata: null, nbt: null }
}

function blockAt(name, x = 0, y = 64, z = 0) {
  return { name, position: new Vec3(x, y, z) }
}

/** 造一个真实结构的容器窗口：容器 27 格 + 玩家 36 格（= prismarine-windows 的 9x3 布局）。 */
function makeWindow({ container = {}, player = {}, id = 3, type = 'minecraft:generic_9x3' } = {}) {
  const slots = new Array(CHEST_SLOTS + 36).fill(null)
  for (const [slot, value] of Object.entries(container)) slots[Number(slot)] = value
  for (const [slot, value] of Object.entries(player)) {
    slots[CHEST_SLOTS + (Number(slot) - 9)] = value
  }
  return {
    id,
    type,
    slots,
    inventoryStart: CHEST_SLOTS,
    inventoryEnd: CHEST_SLOTS + 36,
    hotbarStart: CHEST_SLOTS + 27,
    containerSlots: CHEST_SLOTS,
  }
}

/** 双箱：容器 54 格（minecraft:generic_9x6 的结构）。 */
function makeDoubleWindow({ container = {} } = {}) {
  const slots = new Array(54 + 36).fill(null)
  for (const [slot, value] of Object.entries(container)) slots[Number(slot)] = value
  return {
    id: 5,
    type: 'minecraft:generic_9x6',
    slots,
    inventoryStart: 54,
    inventoryEnd: 90,
    hotbarStart: 81,
  }
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
    blocks = null,
    window = makeWindow(),
    openError = null,
    openDelay = null,
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
    openTarget: null,
    closeCalls: [],
    closeReturns: [],
    transferCalls: [],
    clearedStates: 0,
    blockAt(position) {
      if (blocks) return blocks[`${position.x},${position.y},${position.z}`] || null
      return block
    },
    async openContainer(target) {
      bot.openCalls += 1
      bot.openTarget = target
      if (openDelay) await openDelay.promise
      if (openError) throw new Error(openError)
      bot.currentWindow = window
      return window
    },
    closeWindow(target) {
      bot.closeCalls.push(target)
      if (closeError) throw new Error(closeError)
      if (bot.currentWindow === target) bot.currentWindow = null
      return undefined
    },
    async transfer(opts) {
      bot.transferCalls.push(opts)
      if (transferImpl) return transferImpl(bot, opts)
      if (transferError) throw new Error(transferError)
      defaultTransfer(bot, opts)
    },
    clearControlStates() {
      bot.clearedStates += 1
    },
  }
  return bot
}

/** mineflayer transfer 的最小子集：来源/目标各自钉死在单槽、按 stackSize 合并。 */
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

function makeHarness(registry) {
  const events = []
  const logs = []
  const state = { bot: null, online: true }
  const runtime = createActionRuntime({
    registry,
    getBot: () => state.bot,
    isOnline: () => state.online,
    emit: (event, data) => events.push({ event, data }),
    log: (message) => logs.push(message),
    now: () => Date.now(),
  })
  return { runtime, events, logs, state }
}

function terminalFor(events, actionId, status) {
  return events.find(
    (row) => row.event === `minecraft.action.${status}` && row.data.action_id === actionId,
  )
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function main() {
  setTimeout(() => {
    console.error('[container-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[container-test] 注册表属性与常量')
  {
    assert(Boolean(INSPECT) && Boolean(TRANSFER), 'container_inspect / container_transfer 已注册')
    assert(INSPECT.exclusive === true, 'inspect exclusive = true（窗口是有生命周期的客户端状态）')
    assert(INSPECT.risk === 'SAFE', `inspect risk = SAFE（得到 ${INSPECT.risk}）`)
    assert(INSPECT.detached !== true, 'inspect 是同步动作（读结果直接返回）')
    assert(typeof INSPECT.validate === 'function' && typeof INSPECT.run === 'function', 'inspect 有 validate/run')
    assert(TRANSFER.exclusive === true, 'transfer exclusive = true')
    assert(TRANSFER.risk === 'MEDIUM', `transfer risk = MEDIUM（得到 ${TRANSFER.risk}）`)
    assert(TRANSFER.detached === true, 'transfer 是持续型动作（启动即 RUNNING）')
    assert(
      CONTAINER_DEFAULTS.slotCount === 27 && CONTAINER_DEFAULTS.playerSlots === 36,
      `CONTAINER_DEFAULTS（得到 ${JSON.stringify(CONTAINER_DEFAULTS)}）`,
    )
    assert(
      CONTAINER_DEFAULTS.directions.join(',') === 'withdraw,deposit',
      `direction 只有两个（得到 ${CONTAINER_DEFAULTS.directions.join(',')}）`,
    )
    assert(
      Object.keys(CONTAINER_BLOCK_TYPES).sort().join(',') === 'barrel,chest',
      `只支持 chest/barrel（得到 ${Object.keys(CONTAINER_BLOCK_TYPES).sort().join(',')}）`,
    )
    assert(
      containerTypeLabel('chest') === 'Chest' &&
        containerTypeLabel('minecraft:barrel') === 'Barrel' &&
        containerTypeLabel('minecraft:trapped_chest') === '' &&
        containerTypeLabel('minecraft:shulker_box') === '' &&
        containerTypeLabel('minecraft:furnace') === '' &&
        containerTypeLabel('minecraft:chest') === 'Chest' &&
        containerTypeLabel('minecraft:barrel') === 'Barrel',
      'trapped_chest / shulker / furnace 不是支持的类型',
    )
  }

  console.log('[container-test] 参数校验（整数坐标 / 世界边界 / 缺坐标）')
  {
    const ok = INSPECT.validate({ x: 1, y: 64, z: -3 })
    assert(ok.x === 1 && ok.y === 64 && ok.z === -3, `合法坐标（得到 ${JSON.stringify(ok)}）`)
    const badCases = [
      ['x 小数', { x: 1.5, y: 64, z: 0 }, 'action.invalid'],
      ['y 小数', { x: 1, y: 64.2, z: 0 }, 'action.invalid'],
      ['z 是字符串', { x: 1, y: 64, z: '0' }, 'action.invalid'],
      ['缺 z', { x: 1, y: 64 }, 'action.invalid'],
      ['x 超世界边界', { x: 3.1e7, y: 64, z: 0 }, 'action.invalid'],
      ['y 超过 2048', { x: 1, y: 3000, z: 0 }, 'action.invalid'],
    ]
    for (const [label, params, code] of badCases) {
      let got = null
      try {
        INSPECT.validate(params)
      } catch (error) {
        got = error.code
      }
      assert(got === code, `${label} → ${code}（得到 ${got}）`)
    }
  }

  console.log('[container-test] 窗口结构辅助（不硬编码 27：从真实 window 推导）')
  {
    const single = makeWindow({ container: { 0: item('dirt', 12, DIRT) } })
    const double = makeDoubleWindow()
    assert(containerSlotCountOf(single) === 27, '单箱 → 27 格')
    assert(containerSlotCountOf(double) === 54, '双箱 → 54 格')
    assert(containerSlotCountOf(null) === null, '没有窗口 → null')
    assert(requireSingleContainerWindow(single) === 27, '单箱通过结构校验')
    let doubleCode = null
    try {
      requireSingleContainerWindow(double)
    } catch (error) {
      doubleCode = error.code
    }
    assert(doubleCode === 'container.unsupported', `双箱 → container.unsupported（得到 ${doubleCode}）`)
    const bot = makeContainerBot({})
    assert(
      windowSlotForInventorySlot(single, bot, 9) === 27 &&
        windowSlotForInventorySlot(single, bot, 44) === 62,
      '玩家槽位 9..44 → 容器窗口 27..62',
    )
    assert(itemStackCapacity(bot, item('dirt', 1, DIRT)) === 64, '堆叠上限来自 item.stackSize')
    assert(
      itemStackCapacity(bot, { name: 'x', count: 1, type: 999 }) === 0,
      '未知物品 → 0（保守：不允许合并，绝不猜 64）',
    )
    assert(
      JSON.stringify(describeItem(null)) === 'null' &&
        describeItem(item('minecraft:dirt', 3, DIRT)).name === 'dirt',
      'describeItem 归一化并跳过空槽',
    )
    const slots = readContainerSlots(
      makeWindow({ container: { 0: item('minecraft:dirt', 12, DIRT), 7: item('sand', 32, SAND) } }),
      27,
    )
    assert(
      slots.length === 2 && slots[0].slot === 0 && slots[0].name === 'dirt' && slots[1].slot === 7,
      `只返回非空容器槽位（得到 ${JSON.stringify(slots)}）`,
    )
  }

  console.log('[container-test] A. open success + C. inspect read + K. chest + L. barrel + D. close')
  {
    for (const [blockName, label] of [
      ['minecraft:chest', 'chest'],
      ['minecraft:barrel', 'barrel'],
    ]) {
      const window = makeWindow({
        container: { 0: item('minecraft:dirt', 12, DIRT), 7: item('minecraft:sand', 32, SAND) },
      })
      const bot = makeContainerBot({ block: blockAt(blockName), window })
      const { runtime, state } = makeHarness({ container_inspect: INSPECT })
      state.bot = bot
      const result = await runtime.execute('container_inspect', { x: 0, y: 64, z: 0 })
      assert(
        result.status === STATES.SUCCEEDED && result.action === 'container_inspect',
        `${label}：同步完成（得到 ${JSON.stringify(result)}）`,
      )
      const snapshot = result.result
      assert(
        snapshot.container.type === blockName.replace('minecraft:', '') &&
          snapshot.container.size === 27,
        `${label}：容器类型与大小（得到 ${JSON.stringify(snapshot.container)}）`,
      )
      assert(
        snapshot.container.label === (blockName === 'minecraft:chest' ? 'Chest' : 'Barrel'),
        `${label}：显示名（得到 ${snapshot.container.label}）`,
      )
      assert(
        JSON.stringify(snapshot.container.position) === JSON.stringify({ x: 0, y: 64, z: 0 }),
        `${label}：坐标原样返回`,
      )
      assert(
        snapshot.slots.length === 2 &&
          snapshot.slots[0].name === 'dirt' &&
          snapshot.slots[0].count === 12 &&
          snapshot.slots[1].name === 'sand' &&
          snapshot.slots[1].count === 32,
        `${label}：内容（得到 ${JSON.stringify(snapshot.slots)}）`,
      )
      const keys = Object.keys(snapshot).sort().join(',')
      assert(keys === 'container,ok,slots', `${label}：快照只有约定字段（得到 ${keys}）`)
      const raw = JSON.stringify(snapshot)
      assert(
        !raw.includes('inventoryStart') && !raw.includes('"id"') && !raw.includes('hotbarStart'),
        `${label}：不外泄 window 结构`,
      )
      assert(bot.openCalls === 1, `${label}：真的打开了窗口（openContainer 调用 1 次）`)
      assert(bot.openTarget === bot.blockAt(new Vec3(0, 64, 0)), `${label}：打开的是目标方块`)
      assert(
        bot.closeCalls.length === 1 && bot.currentWindow === null,
        `${label}：D/A 结束后窗口已关闭（close 调用 ${bot.closeCalls.length} 次）`,
      )
    }
  }

  console.log('[container-test] B. open failure + E. close after failure + M/N/O. 类型拒绝')
  {
    const bot = makeContainerBot({ openError: 'server refused' })
    const { runtime, state } = makeHarness({ container_inspect: INSPECT })
    state.bot = bot
    await expectCode(
      runtime.execute('container_inspect', { x: 0, y: 64, z: 0 }),
      'container.open_failed',
      'B. open 失败',
    )
    assert(bot.currentWindow === null, 'B. open 失败后没有留下打开的窗口')

    // E. 读到了窗口但结构不对（双箱）→ 抛错，但窗口必须关掉
    const doubleBot = makeContainerBot({ window: makeDoubleWindow() })
    const harness2 = makeHarness({ container_inspect: INSPECT })
    harness2.state.bot = doubleBot
    await expectCode(
      harness2.runtime.execute('container_inspect', { x: 0, y: 64, z: 0 }),
      'container.unsupported',
      'E. 结构不支持（双箱）',
    )
    assert(
      doubleBot.closeCalls.length === 1 && doubleBot.currentWindow === null,
      `E. 失败路径也关窗（close ${doubleBot.closeCalls.length} 次）`,
    )

    const cases = [
      ['M. trapped chest', 'minecraft:trapped_chest'],
      ['O. furnace', 'minecraft:furnace'],
      ['O. hopper', 'minecraft:hopper'],
      ['O. shulker box', 'minecraft:shulker_box'],
      ['O. air（不是容器）', 'air'],
    ]
    for (const [label, name] of cases) {
      const badBot = makeContainerBot({ block: blockAt(name) })
      const harness = makeHarness({ container_inspect: INSPECT })
      harness.state.bot = badBot
      await expectCode(
        harness.runtime.execute('container_inspect', { x: 0, y: 64, z: 0 }),
        'container.unsupported',
        label,
      )
      assert(badBot.openCalls === 0, `${label}：绝不打开（openContainer 调用 ${badBot.openCalls} 次）`)
    }

    // N. 双箱：block 名是 chest，但窗口结构是 54 → 拒绝（在 start/read 阶段）
    const doubleChestBot = makeContainerBot({ window: makeDoubleWindow() })
    const harness3 = makeHarness({ container_inspect: INSPECT })
    harness3.state.bot = doubleChestBot
    let doubleCode = null
    try {
      await ACTION_REGISTRY.container_transfer.validate({
        x: 0,
        y: 64,
        z: 0,
        direction: 'withdraw',
        container_slot: 0,
        inventory_slot: 9,
        item: 'dirt',
        count: 1,
      })
    } catch (error) {
      doubleCode = error.code
    }
    assert(doubleCode === null, 'N. 参数校验不依赖窗口（结构校验在 start）')
  }

  console.log('[container-test] 距离 / 未加载方块')
  {
    const far = makeContainerBot({ block: blockAt('minecraft:chest', 20, 64, 0) })
    const harness = makeHarness({ container_inspect: INSPECT })
    harness.state.bot = far
    await expectCode(
      harness.runtime.execute('container_inspect', { x: 0, y: 64, z: 0 }),
      'container.too_far',
      '超距离 → container.too_far',
    )
    assert(far.openCalls === 0, '太远绝不打开')

    const missing = makeContainerBot({ blocks: {} })
    const harness2 = makeHarness({ container_inspect: INSPECT })
    harness2.state.bot = missing
    await expectCode(
      harness2.runtime.execute('container_inspect', { x: 0, y: 64, z: 0 }),
      'block.unavailable',
      '区块未加载 → block.unavailable',
    )
  }

  console.log('[container-test] F. timeout：超时后不留下打开的窗口')
  {
    const openDelay = deferred()
    const window = makeWindow({ container: { 0: item('dirt', 1, DIRT) } })
    const bot = makeContainerBot({ window, openDelay })
    const { runtime, events, state } = makeHarness({
      container_inspect: { ...INSPECT, timeout_ms: 120 },
    })
    state.bot = bot
    const pending = runtime.execute('container_inspect', { x: 0, y: 64, z: 0 }).catch((error) => error)
    await sleep(200) // 等 timeout 触发（此时 openContainer 还挂着）
    openDelay.resolve(null) // 窗口在取消之后才真的开出来
    const outcome = await pending
    assert(
      outcome && outcome.status === STATES.TIMEOUT,
      `F. 超时按 TIMEOUT 收尾（得到 ${outcome && outcome.status}）`,
    )
    await sleep(50) // 让 run 的后半段跑完
    const timedOut = events.find((row) => row.event === 'minecraft.action.timeout')
    assert(Boolean(timedOut), 'F. 收到 TIMEOUT 终态事件')
    assert(
      bot.closeCalls.length === 1 && bot.currentWindow === null,
      `F. 超时后晚到的窗口被立刻关掉（close ${bot.closeCalls.length} 次）`,
    )
  }

  console.log('[container-test] I. cleanup 恰好一次（重复调用无害）')
  {
    const window = makeWindow({ container: { 0: item('dirt', 1, DIRT) } })
    const bot = makeContainerBot({ window })
    const controller = { window }
    bot.currentWindow = window
    INSPECT.cleanup(bot, controller)
    INSPECT.cleanup(bot, controller)
    INSPECT.cleanup(bot, controller)
    assert(bot.closeCalls.length === 1, `I. cleanup 只关一次（得到 ${bot.closeCalls.length}）`)
    assert(controller.window === null, 'I. cleanup 后清掉 controller.window')
    assert(bot.currentWindow === null, 'I. 窗口真的关掉了')

    // 没有窗口 / 没有 controller：绝不抛
    INSPECT.cleanup(bot, {})
    INSPECT.cleanup(null, null)
    INSPECT.cleanup(bot, undefined)
    assert(bot.closeCalls.length === 1, 'I. 空 controller 不会多关一次')
  }

  console.log('[container-test] J. close 抛错：如实上报，不吞掉')
  {
    const window = makeWindow({ container: { 0: item('dirt', 1, DIRT) } })
    const bot = makeContainerBot({ window, closeError: 'socket dead' })
    const { runtime, events, state } = makeHarness({ container_inspect: INSPECT })
    state.bot = bot
    const error = await expectCode(
      runtime.execute('container_inspect', { x: 0, y: 64, z: 0 }),
      'container.close_failed',
      'J. close 失败',
    )
    assert(
      error && error.detail && error.detail.snapshot && error.detail.snapshot.slots.length === 1,
      'J. close 失败时把已读到的快照如实带上',
    )
    const failed = events.find((row) => row.event === 'minecraft.action.failed')
    assert(Boolean(failed) && failed.data.code === 'container.close_failed', 'J. 终态是 FAILED + close_failed')
  }

  console.log('[container-test] G/H. 取消与断开：窗口一定被关（transfer 动作）')
  {
    // G. 打开成功 → transfer 挂住 → STOP：cleanup 关窗
    const hang = deferred()
    const window = makeWindow({ container: { 0: item('dirt', 12, DIRT) } })
    const bot = makeContainerBot({ window, transferImpl: () => hang.promise })
    const { runtime, events, state } = makeHarness({ container_transfer: TRANSFER })
    state.bot = bot
    const started = await runtime.execute('container_transfer', {
      x: 0,
      y: 64,
      z: 0,
      direction: 'withdraw',
      container_slot: 0,
      inventory_slot: 9,
      item: 'dirt',
      count: 1,
    })
    assert(started.status === STATES.RUNNING, 'G. 启动返回 RUNNING')
    await sleep(30)
    const stopped = runtime.stop()
    assert(stopped.cancelled.length === 1, 'G. STOP 取消了它')
    assert(
      bot.closeCalls.length === 1 && bot.currentWindow === null,
      `G. 取消后窗口已关（close ${bot.closeCalls.length} 次）`,
    )
    assert(Boolean(terminalFor(events, started.action_id, 'cancelled')), 'G. 终态是 CANCELLED')
    assert(
      !terminalFor(events, started.action_id, 'completed'),
      'G. 绝不出现双终态（没有 completed）',
    )

    // H. 断开时窗口还开着：cancelAll 也要关窗、不崩
    const hang2 = deferred()
    const window2 = makeWindow({ container: { 0: item('dirt', 12, DIRT) } })
    const bot2 = makeContainerBot({ window: window2, transferImpl: () => hang2.promise })
    const harness2 = makeHarness({ container_transfer: TRANSFER })
    harness2.state.bot = bot2
    await harness2.runtime.execute('container_transfer', {
      x: 0,
      y: 64,
      z: 0,
      direction: 'withdraw',
      container_slot: 0,
      inventory_slot: 9,
      item: 'dirt',
      count: 1,
    })
    await sleep(30)
    const cancelled = harness2.runtime.cancelAll('disconnect')
    assert(cancelled.length === 1, 'H. 断开取消了进行中的动作')
    assert(
      bot2.closeCalls.length === 1 && bot2.currentWindow === null,
      `H. 断开后窗口已关（close ${bot2.closeCalls.length} 次）`,
    )
    assert(harness2.state.online === true, 'H. 断开收尾不崩 runtime（仍可继续服务）')
  }

  clearInterval(keepAlive)
  console.log('')
  console.log(`[container-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.log(`[container-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[container-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[container-test] crashed:', error)
  process.exit(1)
})
