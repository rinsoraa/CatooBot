'use strict'
/**
 * dig_capability 测试（Phase 4J · A–L）：只读的"这个方块现在能不能挖、大概多久"。
 *
 * 事实来源全部是假的 mineflayer 运行时（blockAt / heldItem / canDigBlock / digTime），
 * 覆盖：方块查询、主手投影、canDigBlock 真/假、digTime、空气、null 方块、太远、
 * **完全没有副作用**（不挖/不装备/不移动/不改背包），以及"前台动作在跑时照样能读"。
 *
 * 运行：node minecraft_runtime/test/dig_capability.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const {
  ACTION_REGISTRY,
  DIG_CAPABILITY_DEFAULTS,
  actionRuntime,
  digCapabilityView,
} = require('../runtime.js')
const { createActionRuntime } = require('../action_runtime.js')

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

/** 假 bot：只提供 dig_capability 会用到的那几个只读面 + 副作用哨兵。 */
function makeBot({
  block = { name: 'stone', diggable: true },
  heldItem = { name: 'stone_pickaxe', count: 1, type: 1 },
  canDig = true,
  digTime = 1250.4,
  position = new Vec3(0, 64, 0),
  digTimeThrows = false,
} = {}) {
  const bot = {
    entity: { position },
    heldItem,
    // 副作用哨兵：任何一个被调用都说明"只读"这条被破坏了
    sideEffects: [],
    dig: async () => bot.sideEffects.push('dig'),
    stopDigging: () => bot.sideEffects.push('stopDigging'),
    equip: () => bot.sideEffects.push('equip'),
    setQuickBarSlot: () => bot.sideEffects.push('setQuickBarSlot'),
    unequip: () => bot.sideEffects.push('unequip'),
    clearControlStates: () => bot.sideEffects.push('clearControlStates'),
    blockAt: (pos) => (block ? { ...block, position: block.position || pos } : null),
    canDigBlock: () => canDig,
    digTime: () => {
      if (digTimeThrows) throw new Error('digTime exploded')
      return digTime
    },
  }
  return bot
}

const PARAMS = { x: 2, y: 64, z: 2 }

async function main() {
  console.log('[dig-capability-test] A. 注册表属性（SAFE 只读 / 非独占 / 不是新 Action）')
  {
    const def = ACTION_REGISTRY.dig_capability
    assert(Boolean(def), 'dig_capability 已注册')
    assert(def.risk === 'SAFE', `risk = SAFE（得到 ${def.risk}）`)
    assert(def.exclusive === false, 'exclusive = false（可与前台动作并行读取）')
    assert(typeof def.run === 'function', '有同步 run（纯读取，不需要 start/wait）')
    assert(def.detached === undefined, '不是持续型动作')
    assert(
      Object.keys(ACTION_REGISTRY).sort().join(',') ===
        'chat,container_inspect,container_transfer,craft,dig,dig_capability,dropped_items,equip,follow_player,inventory_move,look_at,move_to,pickup_item,place,recipe_lookup,stop',
      `注册表只多了 dig_capability（得到 ${Object.keys(ACTION_REGISTRY).sort().join(',')}）`,
    )
    assert(
      DIG_CAPABILITY_DEFAULTS.reasons.join(',') === 'air,too_far,not_diggable',
      `reason 只有三种非 null 取值（得到 ${DIG_CAPABILITY_DEFAULTS.reasons.join(',')}）`,
    )
  }

  console.log('[dig-capability-test] B/C/D/E/F：投影里的每一条事实')
  {
    // B：方块名 + 主手投影（语义快照，无 raw Item）
    const ok = digCapabilityView(makeBot(), PARAMS)
    assert(
      ok.ok === true && ok.block.name === 'stone',
      `block.name（得到 ${JSON.stringify(ok.block)}）`,
    )
    assert(
      ok.held_item && ok.held_item.name === 'stone_pickaxe' && ok.held_item.count === 1,
      `held_item 投影（得到 ${JSON.stringify(ok.held_item)}）`,
    )
    assert(
      Object.keys(ok.held_item).sort().join(',') === 'count,name',
      `held_item 只有 name/count（得到 ${Object.keys(ok.held_item)}）`,
    )
    assert(ok.can_dig === true, 'canDigBlock true → can_dig true')
    assert(ok.dig_time_ms === 1250, `digTime 取整成整数毫秒（得到 ${ok.dig_time_ms}）`)
    assert(ok.reason === null, `能挖时 reason 是 null（得到 ${ok.reason}）`)

    // 两种距离口径必须分开（§十五：绝不混成一个含糊的 distance）
    assert(
      typeof ok.distance.goal_near === 'number' && typeof ok.distance.raw === 'number',
      `distance 有 goal_near 与 raw 两个字段（得到 ${JSON.stringify(ok.distance)}）`,
    )
    assert(
      ok.distance.raw > ok.distance.goal_near,
      `raw 是浮点三维距离、goal_near 是格子距离（得到 ${JSON.stringify(ok.distance)}）`,
    )

    // C：canDigBlock false → not_diggable，digTime 为 null（不猜"工具等级不够"）
    const notDiggable = digCapabilityView(makeBot({ canDig: false }), PARAMS)
    assert(
      notDiggable.can_dig === false &&
        notDiggable.reason === 'not_diggable' &&
        notDiggable.dig_time_ms === null,
      `挖不动 → can_dig=false / reason=not_diggable / dig_time_ms=null（得到 ${JSON.stringify(
        notDiggable,
      )}）`,
    )

    // D：空手（held_item = null）照样能回答
    const emptyHand = digCapabilityView(makeBot({ heldItem: null }), PARAMS)
    assert(emptyHand.held_item === null, '空手 → held_item = null')
    assert(emptyHand.can_dig === true, '空手也如实回答 can_dig（这里假 bot 说能挖）')

    // E：空气 → 正常数据（不是异常），reason=air
    const air = digCapabilityView(makeBot({ block: { name: 'air', diggable: false } }), PARAMS)
    assert(
      air.ok === true && air.block.name === 'air' && air.can_dig === false && air.reason === 'air',
      `空气 → 正常返回 + reason=air（得到 ${JSON.stringify(air)}）`,
    )
    assert(air.dig_time_ms === null, '空气不报 dig_time_ms')
    const caveAir = digCapabilityView(
      makeBot({ block: { name: 'cave_air', diggable: false } }),
      PARAMS,
    )
    assert(caveAir.reason === 'air', 'cave_air 也算空气')

    // F：太远 → too_far（复用 dig 的交互距离语义），且绝不移动
    const farBot = makeBot({ position: new Vec3(30, 64, 30) })
    const far = digCapabilityView(farBot, PARAMS)
    assert(
      far.can_dig === false && far.reason === 'too_far' && far.dig_time_ms === null,
      `太远 → reason=too_far（得到 ${JSON.stringify(far)}）`,
    )
    assert(farBot.sideEffects.length === 0, '太远也不会为了让罐头走近而做任何事')

    // digTime 坏值一律 null（绝不把负数/Infinity 传出去）
    const broken = digCapabilityView(makeBot({ digTimeThrows: true }), PARAMS)
    assert(broken.can_dig === true && broken.dig_time_ms === null, 'digTime 抛错 → dig_time_ms = null')
    const infinite = digCapabilityView(makeBot({ digTime: Infinity }), PARAMS)
    assert(infinite.dig_time_ms === null, 'digTime 返回 Infinity → null')
    const negative = digCapabilityView(makeBot({ digTime: -5 }), PARAMS)
    assert(negative.dig_time_ms === null, 'digTime 返回负数 → null')
  }

  console.log('[dig-capability-test] G/H：坐标校验与"那个位置没有方块"')
  {
    const def = ACTION_REGISTRY.dig_capability
    const ok = def.validate({ x: 100, y: 64, z: -230 })
    assert(ok.x === 100 && ok.y === 64 && ok.z === -230, '整数坐标通过')
    for (const [label, params] of [
      ['浮点坐标', { x: 100.5, y: 64, z: 0 }],
      ['字符串坐标', { x: '100', y: 64, z: 0 }],
      ['缺坐标', { x: 1, y: 2 }],
      ['NaN', { x: NaN, y: 64, z: 0 }],
      ['x 超世界边界', { x: 4.0e7, y: 64, z: 0 }],
      ['y 低于世界下限', { x: 0, y: -600, z: 0 }],
    ]) {
      let code = null
      try {
        def.validate(params)
      } catch (error) {
        code = error.code
      }
      assert(code === 'action.invalid', `${label} → action.invalid（得到 ${code}）`)
    }

    let missing = null
    try {
      digCapabilityView(makeBot({ block: null }), PARAMS)
    } catch (error) {
      missing = error
    }
    assert(
      missing && missing.code === 'block.unavailable' && missing.status === 404,
      `没有方块 → block.unavailable 404（得到 ${missing && missing.code}）`,
    )
  }

  console.log('[dig-capability-test] I/J/K：完全没有副作用，输出字段固定')
  {
    const bot = makeBot()
    let projections = 0
    for (let i = 0; i < 10; i += 1) {
      const view = digCapabilityView(bot, PARAMS)
      if (view.ok) projections += 1
    }
    assert(projections === 10, `连续查询 10 次都返回投影（得到 ${projections}）`)
    assert(
      bot.sideEffects.length === 0,
      `没有任何副作用（不挖/不装备/不切槽/不清控制位）（得到 ${JSON.stringify(bot.sideEffects)}）`,
    )
    assert(
      JSON.stringify(Object.keys(digCapabilityView(bot, PARAMS)).sort()) ===
        JSON.stringify([
          'block',
          'can_dig',
          'dig_time_ms',
          'distance',
          'held_item',
          'ok',
          'position',
          'reason',
        ]),
      '输出字段固定（绝不夹带 recommended_tool / best_tool 之类）',
    )
    assert(
      !("recommended_tool" in digCapabilityView(bot, PARAMS)) &&
        !("best_tool" in digCapabilityView(bot, PARAMS)),
      '没有推荐字段（§十七）',
    )
  }

  console.log('[dig-capability-test] L. 前台动作在跑时，只读查询照样能执行')
  {
    const events = []
    const state = { bot: null, online: true }
    const runtime = createActionRuntime({
      registry: ACTION_REGISTRY,
      getBot: () => state.bot,
      isOnline: () => state.online,
      emit: (event, data) => events.push({ event, data }),
      log: () => {},
      now: () => Date.now(),
    })
    const busyBot = makeBot()
    // move_to 是持续型：给一个"永远不发终态事件"的假 pathfinder，让它一直 RUNNING
    busyBot.pathfinder = {
      setGoalCalls: [],
      setGoal(goal) {
        busyBot.pathfinder.setGoalCalls.push(goal)
      },
      goal: null,
      isMoving: () => true,
    }
    state.bot = busyBot
    const started = await runtime.execute('move_to', { x: 20, y: 64, z: 0 })
    assert(started.status === 'RUNNING', `L. 前台动作（move_to）已经在跑（得到 ${started.status}）`)

    const read = await runtime.execute('dig_capability', PARAMS)
    assert(
      read.status === 'SUCCEEDED' && read.result && read.result.can_dig === true,
      `L. 只读查询照样能执行（得到 ${JSON.stringify(read).slice(0, 120)}）`,
    )

    // 而独占动作仍然被拒 —— 证明"非独占"没有破坏互斥契约
    let busyCode = null
    try {
      await runtime.execute('dig', { ...PARAMS, expected_block: 'stone' })
    } catch (error) {
      busyCode = error.code
    }
    assert(busyCode === 'action.busy', `L. 独占动作仍然被拒（得到 ${busyCode}）`)

    const stop = runtime.stop()
    assert(
      stop.cancelled.includes(started.action_id),
      `L. 收尾：停掉前台动作（得到 ${JSON.stringify(stop)}）`,
    )
    assert(busyBot.sideEffects.filter((name) => name === 'dig').length === 0, 'L. 全程没有挖过')
  }
}

main()
  .then(() => {
    console.log(`\n[dig-capability-test] ${checks - failures}/${checks} checks passed`)
    if (failures > 0) {
      console.error(`[dig-capability-test] FAILED: ${failures} check(s)`)
      process.exit(1)
    }
    console.log('[dig-capability-test] ALL CHECKS PASSED')
  })
  .catch((error) => {
    console.error('[dig-capability-test] crashed:', error)
    process.exit(1)
  })
