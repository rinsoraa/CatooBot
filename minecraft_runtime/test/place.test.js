/**
 * place 注册与安全配置测试（Phase 4C）：注册表属性 / 参数校验（整数坐标 + 六个 face + item）/
 * start 阶段校验（手持物品 / 目标空 / 参考方块 / 距离）/ wait 阶段复核 / cleanup /
 * CANCELLED·TIMEOUT·race（用真实 place 动作 + 假 bot 直跑 ActionRuntime）。
 * 另含 inventorySlice 的只读切片测试（按物品名聚合、不外泄 slot/NBT）。
 *
 * 运行：node minecraft_runtime/test/place.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const {
  ACTION_REGISTRY,
  PLACE_DEFAULTS,
  PLACE_FACES,
  inventorySlice,
  normalizeItemName,
} = require('../runtime.js')
const { createActionRuntime, ActionError } = require('../action_runtime')

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

const PLACE = ACTION_REGISTRY.place

//: 目标 (2,64,2)、face=up → reference = (2,63,2)（都在 5 格内，能通过距离门）
const PARAMS = { x: 2, y: 64, z: 2, face: 'up', expected_item: 'dirt' }

function blockEntry(name, x, y, z) {
  return { name, position: new Vec3(x, y, z) }
}

/** 真实 mineflayer 的 blockAt 对空气也返回方块对象（不是 null）——假世界照此建模。 */
function airEntry(x, y, z) {
  return blockEntry('air', x, y, z)
}

function makeBot({ blocks = {}, heldItem = null, inventoryItems = [], placeBlock = null } = {}) {
  const bot = {
    entity: { position: new Vec3(0, 64, 0) },
    quickBarSlot: 0,
    heldItem,
    inventory: { items: () => inventoryItems },
    blockAt: (position) => blocks[`${position.x},${position.y},${position.z}`] ?? null,
    placeBlock: placeBlock || (async () => {}),
    cleared: 0,
    clearControlStates() {
      bot.cleared += 1
    },
    _blocks: blocks,
  }
  return bot
}

/** 有 dirt 可放的正常世界：目标 (2,64,2) 是空气，下面 (2,63,2) 是草地。 */
function makeWorldBot(overrides = {}) {
  const blocks = {
    '2,64,2': airEntry(2, 64, 2),
    '2,63,2': blockEntry('grass_block', 2, 63, 2),
  }
  const held = { name: 'dirt', count: 12, type: 11 }
  const bot = makeBot({
    blocks,
    heldItem: held,
    inventoryItems: [held, { name: 'sand', count: 24, type: 12 }],
    placeBlock: async (referenceBlock, faceVector) => {
      const dest = referenceBlock.position.plus(faceVector)
      blocks[`${dest.x},${dest.y},${dest.z}`] = blockEntry('dirt', dest.x, dest.y, dest.z)
    },
    ...overrides,
  })
  return { bot, blocks }
}

function makeHarness(placeDefinition = PLACE) {
  const events = []
  const logs = []
  const state = { online: true }
  const runtime = createActionRuntime({
    registry: { place: placeDefinition },
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
    console.error('[place-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  // 超时计时器在单测里是 unref 的：自己拿住事件循环
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[place-test] place 注册表属性')
  {
    assert(Boolean(PLACE), 'place 已注册')
    assert(PLACE.exclusive === true, 'exclusive = true（不能边放边走，也不能和 dig 并行）')
    assert(PLACE.risk === 'MEDIUM', `risk = MEDIUM（得到 ${PLACE.risk}）`)
    assert(PLACE.timeout_ms === 30000, `默认 timeout = 30s（得到 ${PLACE.timeout_ms}）`)
    assert(PLACE.detached === true, 'detached = true（启动即 RUNNING，终态经事件）')
    assert(typeof PLACE.validate === 'function', '有参数校验')
    assert(typeof PLACE.start === 'function' && typeof PLACE.wait === 'function', '有 start/wait')
    assert(typeof PLACE.run !== 'function', '没有阻塞式 run')
    assert(typeof PLACE.cleanup === 'function', '有 cleanup')
    assert(
      Object.keys(ACTION_REGISTRY).sort().join(',') ===
        'chat,container_inspect,container_transfer,craft,dig,equip,follow_player,inventory_move,look_at,move_to,place,recipe_lookup,stop',
      `注册表只有已批准动作（得到 ${Object.keys(ACTION_REGISTRY).sort().join(',')}）`,
    )
    // §二 禁止清单：连续建造/整理背包/容器类动作一个都不许有
    // （equip / inventory_move 从 Phase 4D 起是单个物品/单个槽位的正式动作；
    //   craft 从 Phase 4F 起是一次一个配方的正式动作 —— 故都不在清单里。
    //   批量建造 / 容器自动化 / 批量合成 / 丢弃 / 拾取 仍然必须不存在）
    for (const forbidden of [
      'place_multiple',
      'build',
      'bridge',
      'schematic',
      'craft_all',
      'auto_craft',
      'craft_chain',
      'crafting_table',
      'smelt',
      'trade',
      'container',
      'attack',
      'eat',
      'sort_inventory',
      'auto_equip',
      'drop',
      'pickup',
      'transfer_all',
    ]) {
      assert(ACTION_REGISTRY[forbidden] === undefined, `未注册 ${forbidden}`)
    }
  }

  console.log('[place-test] 常量与 face 映射（§八：只允许六个值）')
  {
    assert(PLACE_DEFAULTS.timeoutMs === 30000, `timeout 30s（得到 ${PLACE_DEFAULTS.timeoutMs}）`)
    assert(PLACE_DEFAULTS.maxDistance === 5, `max_distance 5（得到 ${PLACE_DEFAULTS.maxDistance}）`)
    assert(
      PLACE_DEFAULTS.faces.join(',') === 'up,down,north,south,east,west',
      `六个 face（得到 ${PLACE_DEFAULTS.faces.join(',')}）`,
    )
    assert(
      PLACE_FACES.up.x === 0 && PLACE_FACES.up.y === 1 && PLACE_FACES.up.z === 0,
      'up → (0,1,0)',
    )
    assert(
      PLACE_FACES.north.z === -1 && PLACE_FACES.south.z === 1 && PLACE_FACES.west.x === -1,
      'north/south/west → (0,0,±1)/(−1,0,0)',
    )
  }

  console.log('[place-test] 参数校验（§十三 整数坐标 / §八 face / §九 expected_item）')
  {
    const ok = PLACE.validate({ ...PARAMS })
    assert(ok.x === 2 && ok.y === 64 && ok.z === 2 && ok.face === 'up' && ok.expected_item === 'dirt',
      '合法参数通过')
    assert(PLACE.validate({ ...PARAMS, face: 'UP' }).face === 'up', 'face 大小写归一化')

    const badCases = [
      ['小数坐标 x', { ...PARAMS, x: 100.5 }, 'action.invalid'],
      ['小数坐标 y', { ...PARAMS, y: 64.2 }, 'action.invalid'],
      ['字符串坐标', { ...PARAMS, x: '2' }, 'action.invalid'],
      ['NaN', { ...PARAMS, z: NaN }, 'action.invalid'],
      ['Infinity', { ...PARAMS, z: Infinity }, 'action.invalid'],
      ['x 超世界边界', { ...PARAMS, x: 4.0e7 }, 'action.invalid'],
      ['y 低于世界下限', { ...PARAMS, y: -600 }, 'action.invalid'],
      ['缺 face', { x: 2, y: 64, z: 2, expected_item: 'dirt' }, 'face.invalid'],
      ['非法 face', { ...PARAMS, face: 'north_east' }, 'face.invalid'],
      ['浮点方向', { ...PARAMS, face: '1,0,0' }, 'face.invalid'],
      ['缺 expected_item', { x: 2, y: 64, z: 2, face: 'up' }, 'item.invalid'],
      ['空 expected_item', { ...PARAMS, expected_item: '   ' }, 'item.invalid'],
      ['expected_item 过长', { ...PARAMS, expected_item: 'x'.repeat(65) }, 'item.invalid'],
    ]
    for (const [label, params, code] of badCases) {
      let got = null
      try {
        PLACE.validate(params)
      } catch (error) {
        got = error.code
      }
      assert(got === code, `${label} → ${code}（得到 ${got}）`)
    }
  }

  console.log('[place-test] start 阶段校验（同步反馈，绝不进入放置）')
  {
    // A 前置：正常世界应当通过，并给出不可变快照
    const { bot } = makeWorldBot()
    const state = await PLACE.start(bot, PLACE.validate({ ...PARAMS }))
    assert(state.target && state.target.x === 2, 'start 返回 target')
    assert(state.reference.y === 63, 'face=up → reference = target + (0,-1,0)')
    assert(state.block_before === 'air', `block_before = air（得到 ${state.block_before}）`)
    assert(state.reference_before === 'grass_block', `reference_before（得到 ${state.reference_before}）`)
    assert(state.item_before.name === 'dirt' && state.item_before.count === 12, 'item_before 记录手持数量')

    // D：主手没拿东西 → held.item_missing
    await expectCode(
      PLACE.start(
        makeBot({
          blocks: { '2,64,2': airEntry(2, 64, 2), '2,63,2': blockEntry('grass_block', 2, 63, 2) },
        }),
        PLACE.validate(PARAMS),
      ),
      'held.item_missing',
      '主手空着',
    )
    // C：主手拿的不是确认的物品 → held.item_changed（带 expected/actual）
    const wrongHeld = makeBot({
      blocks: { '2,64,2': airEntry(2, 64, 2), '2,63,2': blockEntry('grass_block', 2, 63, 2) },
      heldItem: { name: 'sand', count: 3 },
    })
    const changed = await expectCode(
      PLACE.start(wrongHeld, PLACE.validate(PARAMS)),
      'held.item_changed',
      '手持物品不符',
    )
    assert(
      changed && changed.detail && changed.detail.expected === 'dirt' && changed.detail.actual === 'sand',
      'held.item_changed 带 expected/actual',
    )
    // B：目标位置已经有方块 → target.occupied
    await expectCode(
      PLACE.start(
        makeBot({
          blocks: {
            '2,64,2': blockEntry('stone', 2, 64, 2),
            '2,63,2': blockEntry('grass_block', 2, 63, 2),
          },
          heldItem: { name: 'dirt', count: 1 },
        }),
        PLACE.validate(PARAMS),
      ),
      'target.occupied',
      '目标被占用（只往空气放，不覆盖 grass/water/…）',
    )
    // E：参考方块是空气 → reference.missing
    await expectCode(
      PLACE.start(
        makeBot({
          blocks: { '2,64,2': airEntry(2, 64, 2), '2,63,2': airEntry(2, 63, 2) },
          heldItem: { name: 'dirt', count: 1 },
        }),
        PLACE.validate(PARAMS),
      ),
      'reference.missing',
      '参考方块是空气',
    )
    // 区块未加载（blockAt 返回 null）→ block.unavailable
    await expectCode(
      PLACE.start(
        makeBot({ blocks: {}, heldItem: { name: 'dirt', count: 1 }, blockAt: () => null }),
        PLACE.validate(PARAMS),
      ),
      'block.unavailable',
      '区块未加载（blockAt=null）',
    )
    // F：太远 → block.too_far
    const far = makeBot({
      blocks: {
        '30,64,30': airEntry(30, 64, 30),
        '30,63,30': blockEntry('grass_block', 30, 63, 30),
      },
      heldItem: { name: 'dirt', count: 1 },
    })
    await expectCode(
      PLACE.start(far, PLACE.validate({ x: 30, y: 64, z: 30, face: 'up', expected_item: 'dirt' })),
      'block.too_far',
      '太远（不导航）',
    )
  }

  console.log('[place-test] wait 阶段：不信任 placeBlock Promise，必须复核目标方块')
  {
    // A/H：Promise resolve 但世界没变 → block.place_unconfirmed
    const { bot, blocks } = makeWorldBot({ placeBlock: async () => {} })
    const state = await PLACE.start(bot, PLACE.validate(PARAMS))
    await expectCode(
      PLACE.wait(bot, PLACE.validate(PARAMS), { cancelled: false }, state),
      'block.place_unconfirmed',
      'Promise resolve 但目标仍是 air',
    )
    assert(blocks['2,64,2'].name === 'air', '世界确实没变（目标仍是 air，所以不能报成功）')

    // 放上去但变成了别的方块 → block.place_unconfirmed（带 expected/actual）
    let otherBlocks = null
    const other = makeWorldBot({
      placeBlock: async (referenceBlock, faceVector) => {
        const dest = referenceBlock.position.plus(faceVector)
        otherBlocks[`${dest.x},${dest.y},${dest.z}`] = blockEntry('gravel', dest.x, dest.y, dest.z)
      },
    })
    otherBlocks = other.blocks
    const otherState = await PLACE.start(other.bot, PLACE.validate(PARAMS))
    const unconfirmed = await expectCode(
      PLACE.wait(other.bot, PLACE.validate(PARAMS), { cancelled: false }, otherState),
      'block.place_unconfirmed',
      '放上去的方块与确认的不一致',
    )
    assert(unconfirmed && unconfirmed.detail && unconfirmed.detail.actual === 'gravel', '带 actual')

    // A：正常成功 → 返回 §十八 的结果形状
    const good = makeWorldBot()
    const goodState = await PLACE.start(good.bot, PLACE.validate(PARAMS))
    const result = await PLACE.wait(good.bot, PLACE.validate(PARAMS), { cancelled: false }, goodState)
    assert(result.block_before === 'air', `block_before=air（得到 ${result.block_before}）`)
    assert(result.block_after === 'dirt', `block_after=dirt（得到 ${result.block_after}）`)
    assert(result.reference_block === 'grass_block', '结果带 reference_block')
    assert(result.face === 'up', '结果带 face')
    assert(result.item_after_count === 12, `item_after_count 如实上报（得到 ${result.item_after_count}）`)
    assert(
      result.position.x === 2 && result.position.y === 64 && result.position.z === 2,
      '结果带目标坐标',
    )
  }

  console.log('[place-test] cleanup（CANCELLED/TIMEOUT；至多一次由 ActionRuntime 保证）')
  {
    const { bot } = makeWorldBot()
    PLACE.cleanup(bot)
    assert(bot.cleared === 1, 'cleanup 清控制位一次')
    PLACE.cleanup(null)
    assert(true, 'cleanup 对 null bot 安全')
  }

  console.log('[place-test] ActionRuntime：place 的 CANCELLED / TIMEOUT / race（真实动作 + 假 bot）')
  {
    // I：放置中 STOP → CANCELLED + cleanup（cleanup 函数**恰好一次**；控制面自己也会清控制位）
    const gate = deferred()
    const { bot } = makeWorldBot({ placeBlock: () => gate.promise })
    let cleanups = 0
    const harness = makeHarness({
      ...PLACE,
      cleanup(bot2) {
        cleanups += 1
        return PLACE.cleanup(bot2)
      },
    })
    harness.state.bot = bot
    const started = await harness.runtime.execute('place', { ...PARAMS })
    assert(started.status === 'RUNNING', `启动返回 RUNNING（得到 ${started.status}）`)
    await new Promise((resolve) => setTimeout(resolve, 10))
    const stop = harness.runtime.stop()
    assert(stop.cancelled.includes(started.action_id), 'stop 取消了 place')
    await new Promise((resolve) => setTimeout(resolve, 20))
    assert(cleanups === 1, `CANCELLED：place.cleanup 恰好一次（得到 ${cleanups}）`)
    assert(bot.cleared >= 1, 'CANCELLED 清过控制位（cleanup 或控制面）')
    assert(harness.runtime.snapshot().active_count === 0, '取消后无僵尸动作')
    const terminal = harness.events.filter((e) => e.event.startsWith('minecraft.action.') && e.data.action_id === started.action_id && e.data.status !== 'RUNNING')
    assert(terminal.length === 1 && terminal[0].event === 'minecraft.action.cancelled', `终态只有一个 cancelled（得到 ${terminal.map((e) => e.event)}）`)

    // J：超时 → TIMEOUT + cleanup（用真实定义只改 timeout_ms）
    const hangGate = deferred()
    const { bot: hangBot } = makeWorldBot({ placeBlock: () => hangGate.promise })
    let hangCleanups = 0
    const hangHarness = makeHarness({
      ...PLACE,
      timeout_ms: 60,
      cleanup(bot2) {
        hangCleanups += 1
        return PLACE.cleanup(bot2)
      },
    })
    hangHarness.state.bot = hangBot
    const hang = await hangHarness.runtime.execute('place', { ...PARAMS })
    assert(hang.status === 'RUNNING', '超时用例：启动 RUNNING')
    await new Promise((resolve) => setTimeout(resolve, 120))
    const timeoutEvents = hangHarness.events.filter((e) => e.event === 'minecraft.action.timeout')
    assert(timeoutEvents.length === 1, `超时事件 1 个（得到 ${timeoutEvents.length}）`)
    assert(hangHarness.runtime.currentView().status === 'TIMEOUT', '终态 TIMEOUT')
    assert(hangCleanups === 1, `TIMEOUT：place.cleanup 恰好一次（得到 ${hangCleanups}）`)

    // K：race —— stop 与 placeBlock 同时落定 → 只允许一个终态、cleanup 只跑一次
    const raceGate = deferred()
    const { bot: raceBot } = makeWorldBot({ placeBlock: () => raceGate.promise })
    let raceCleanups = 0
    const raceHarness = makeHarness({
      ...PLACE,
      cleanup(bot2) {
        raceCleanups += 1
        return PLACE.cleanup(bot2)
      },
    })
    raceHarness.state.bot = raceBot
    const race = await raceHarness.runtime.execute('place', { ...PARAMS })
    raceHarness.runtime.stop()
    raceGate.resolve() // 同时完成
    await new Promise((resolve) => setTimeout(resolve, 30))
    const raceTerminals = raceHarness.events.filter(
      (e) => e.event.startsWith('minecraft.action.') && e.data.action_id === race.action_id && e.data.status !== 'RUNNING',
    )
    assert(raceTerminals.length === 1, `race：恰好一个终态（得到 ${raceTerminals.length}）`)
    assert(raceCleanups === 1, `race：place.cleanup 恰好一次（得到 ${raceCleanups}）`)
  }

  console.log('[place-test] inventorySlice（§四：只读、按物品名聚合、不外泄 slot/NBT）')
  {
    assert(normalizeItemName('minecraft:Dirt ') === 'dirt', 'item 名归一化')
    const { bot } = makeWorldBot()
    const slice = inventorySlice(bot)
    assert(slice.online === true, 'online=true')
    assert(slice.selected_hotbar_slot === 0, '带 selected_hotbar_slot')
    assert(slice.held_item.name === 'dirt' && slice.held_item.count === 12, '带 held_item')
    assert(slice.items.length === 2, `按物品名聚合（得到 ${JSON.stringify(slice.items)}）`)
    assert(slice.items[0].name === 'sand' && slice.items[0].count === 24, '按数量倒序')
    const keys = Object.keys(slice).sort().join(',')
    assert(keys === 'held_item,items,ok,online,selected_hotbar_slot', `切片只有约定字段（得到 ${keys}）`)
    for (const forbidden of ['slots', 'window', 'nbt', 'metadata', 'armor', 'cursor']) {
      assert(!JSON.stringify(slice).includes(forbidden), `切片不含 ${forbidden}`)
    }
    // 聚合：同名物品合并计数
    const merged = inventorySlice(
      makeBot({
        heldItem: { name: 'dirt', count: 2 },
        inventoryItems: [
          { name: 'minecraft:dirt', count: 3 },
          { name: 'dirt', count: 4 },
          { name: 'sand', count: 1 },
        ],
      }),
    )
    assert(
      merged.items.find((item) => item.name === 'dirt').count === 7,
      `同名物品合并计数（得到 ${JSON.stringify(merged.items)}）`,
    )
    // 离线
    const offline = inventorySlice(null)
    assert(offline.online === false && offline.items.length === 0, '离线返回空切片')
  }

  clearInterval(keepAlive)
  console.log(`\n[place-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.error(`[place-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[place-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[place-test] crashed:', error)
  process.exit(1)
})
