'use strict'
/**
 * find_blocks 测试（Phase 4K · A–J）：只读的"附近有哪些指定方块"。
 *
 * 事实来源是假的 mineflayer 运行时（blockAt / registry / findBlocks），覆盖：
 * 名字规范化、未知方块名、距离上限、条数上限与截断、稳定排序、方块坐标、
 * 多个方块 id、**完全没有副作用**（不移动/不装备/不挖），以及前台动作跑着时照样能查。
 *
 * 运行：node minecraft_runtime/test/find_blocks.test.js
 */
'use strict'

const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')

const { ACTION_REGISTRY, FIND_BLOCKS_DEFAULTS, findBlocksView } = require('../runtime.js')
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

const OAK = registry.blocksByName['oak_log'].id
const STONE = registry.blocksByName['stone'].id

/** 假 bot：findBlocks 按传入的坐标表返回（模拟 mineflayer 的"已按距离排序 + slice 到 count"）。 */
function makeBot({
  positions = [],
  names = {},
  position = new Vec3(0.5, 64, 0.5),
  findBlocksMissing = false,
} = {}) {
  const bot = {
    registry,
    entity: { position },
    sideEffects: [],
    dig: async () => bot.sideEffects.push('dig'),
    equip: () => bot.sideEffects.push('equip'),
    setQuickBarSlot: () => bot.sideEffects.push('setQuickBarSlot'),
    setControlState: () => bot.sideEffects.push('control'),
    blockAt: (pos) => ({
      name: names[`${pos.x},${pos.y},${pos.z}`] || (pos.x === 3 ? 'oak_log' : 'stone'),
      position: pos,
    }),
    lastFind: null,
  }
  if (!findBlocksMissing) {
    bot.findBlocks = (options) => {
      bot.lastFind = options
      return positions.slice(0, options.count).map((p) => new Vec3(p[0], p[1], p[2]))
    }
  }
  return bot
}

function main() {
  console.log('[find-blocks-test] A. 注册表属性与默认值')
  {
    const def = ACTION_REGISTRY.find_blocks
    assert(Boolean(def), 'find_blocks 已注册')
    assert(def.risk === 'SAFE', `risk = SAFE（得到 ${def.risk}）`)
    assert(def.exclusive === false, 'exclusive = false（可与前台动作并行读取）')
    assert(typeof def.run === 'function', '有同步 run（只读查询）')
    assert(def.detached === undefined, '不是持续型动作')
    assert(
      Object.keys(ACTION_REGISTRY).sort().join(',') ===
        'chat,container_inspect,container_transfer,craft,dig,dig_capability,dropped_items,equip,find_blocks,follow_player,inventory_move,look_at,move_to,pickup_item,place,recipe_lookup,stop',
      `注册表只多了 find_blocks（得到 ${Object.keys(ACTION_REGISTRY).sort().join(',')}）`,
    )
    assert(
      FIND_BLOCKS_DEFAULTS.maxDistance === 16 &&
        FIND_BLOCKS_DEFAULTS.maxResults === 8 &&
        FIND_BLOCKS_DEFAULTS.hardMaxDistance === 32 &&
        FIND_BLOCKS_DEFAULTS.hardMaxResults === 16 &&
        FIND_BLOCKS_DEFAULTS.maxNames === 8,
      `默认值与硬上限（得到 ${JSON.stringify(FIND_BLOCKS_DEFAULTS)}）`,
    )
  }

  console.log('[find-blocks-test] A/C/D：参数规范化与边界（validate）')
  {
    const def = ACTION_REGISTRY.find_blocks
    const ok = def.validate({ block_names: ['oak_log', 'minecraft:stone'], max_distance: 8 })
    assert(
      ok.block_names.join(',') === 'oak_log,stone' &&
        ok.max_distance === 8 &&
        ok.max_results === FIND_BLOCKS_DEFAULTS.maxResults,
      `名字规范化 + 省略的 max_results 用默认值（得到 ${JSON.stringify(ok)}）`,
    )
    const dedup = def.validate({ block_names: ['oak_log', 'minecraft:oak_log', 'OAK_LOG'] })
    assert(
      dedup.block_names.length === 1 && dedup.block_names[0] === 'oak_log',
      `同一个方块的不同写法去重（得到 ${JSON.stringify(dedup.block_names)}）`,
    )
    for (const [label, params] of [
      ['空数组', { block_names: [] }],
      ['不是数组', { block_names: 'oak_log' }],
      ['元素不是字符串', { block_names: [42] }],
      ['元素是空白', { block_names: ['   '] }],
      ['超过 8 个名字', { block_names: Array.from({ length: 9 }, (_, i) => `block_${i}`) }],
      ['max_distance 是小数', { block_names: ['stone'], max_distance: 4.5 }],
      ['max_distance 太大', { block_names: ['stone'], max_distance: 1000 }],
      ['max_distance 太小', { block_names: ['stone'], max_distance: 0 }],
      ['max_results 太大', { block_names: ['stone'], max_results: 999 }],
    ]) {
      let code = null
      try {
        def.validate(params)
      } catch (error) {
        code = error.code
      }
      assert(code === 'action.invalid', `${label} → action.invalid（得到 ${code}）`)
    }
  }

  console.log('[find-blocks-test] B/H/I：未知方块名 / 方块坐标 / 多个 id')
  {
    const bot = makeBot({ positions: [[3, 64, 0], [1, 64, 2]] })
    const view = findBlocksView(bot, {
      block_names: ['oak_log', 'stone'],
      max_distance: 16,
      max_results: 8,
    })
    assert(view.ok === true, '正常返回 ok=true')
    assert(
      JSON.stringify(bot.lastFind.matching) === JSON.stringify([OAK, STONE]),
      `把名字解析成**方块 id 数组**传给 findBlocks（得到 ${JSON.stringify(bot.lastFind.matching)}）`,
    )
    assert(
      bot.lastFind.point.x === 0 && bot.lastFind.point.y === 64 && bot.lastFind.point.z === 0,
      `起点是罐头当前位置（floored）（得到 ${JSON.stringify(bot.lastFind.point)}）`,
    )
    assert(
      bot.lastFind.maxDistance === 16 && bot.lastFind.count === 9,
      `maxDistance 透传；count = max_results + 1（用来判断截断）（得到 ${bot.lastFind.count}）`,
    )
    assert(
      view.matches.length === 2 &&
        view.matches[0].position.y === 64 &&
        typeof view.matches[0].position.x === 'number',
      `每条匹配带方块坐标（得到 ${JSON.stringify(view.matches.map((m) => m.position))}）`,
    )
    assert(
      view.matches.every((m) => typeof m.block.name === 'string' && m.block.name.length > 0),
      '每条匹配带方块名（语义投影，不是 raw Block）',
    )
    assert(
      view.matches.every(
        (m) => typeof m.distance.goal_near === 'number' && typeof m.distance.raw === 'number',
      ),
      '每条匹配带两种距离口径',
    )
    assert(view.matches[0].distance.goal_near <= view.matches[1].distance.goal_near, '按距离升序')

    let unknown = null
    try {
      findBlocksView(bot, { block_names: ['banana_ore'], max_distance: 16, max_results: 8 })
    } catch (error) {
      unknown = error
    }
    assert(
      unknown && unknown.code === 'block.name_unknown' && unknown.status === 422,
      `未知方块名 → block.name_unknown 422（得到 ${unknown && unknown.code}）`,
    )
    assert(
      unknown.detail && unknown.detail.unknown.join(',') === 'banana_ore',
      `错误里点出是哪个名字不认识（得到 ${JSON.stringify(unknown.detail)}）`,
    )

    const noFinder = makeBot({ findBlocksMissing: true })
    let unavailable = null
    try {
      findBlocksView(noFinder, { block_names: ['stone'], max_distance: 16, max_results: 8 })
    } catch (error) {
      unavailable = error
    }
    assert(
      unavailable && unavailable.code === 'block.query_unavailable',
      `运行时给不出查询 → block.query_unavailable（得到 ${unavailable && unavailable.code}）`,
    )
  }

  console.log('[find-blocks-test] E/F：截断与稳定排序')
  {
    const many = Array.from({ length: 20 }, (_, i) => [i + 1, 64, 0])
    const truncated = findBlocksView(makeBot({ positions: many }), {
      block_names: ['stone'],
      max_distance: 32,
      max_results: 4,
    })
    assert(
      truncated.matches.length === 4 && truncated.truncated === true,
      `超过条数上限 → 只返回 4 条 + truncated=true（得到 ${truncated.matches.length}/${truncated.truncated}）`,
    )
    assert(
      truncated.query.max_results === 4 && truncated.query.max_distance === 32,
      'query 块如实回显这次的查询条件',
    )
    const exact = findBlocksView(makeBot({ positions: [[1, 64, 0], [2, 64, 0]] }), {
      block_names: ['stone'],
      max_distance: 16,
      max_results: 8,
    })
    assert(exact.truncated === false, '没超过上限 → truncated=false')
    const none = findBlocksView(makeBot({ positions: [] }), {
      block_names: ['oak_log'],
      max_distance: 16,
      max_results: 8,
    })
    assert(
      none.ok === true && none.matches.length === 0 && none.truncated === false,
      `找不到就是正常空结果（不是 404 / 不是 block_not_found）（得到 ${JSON.stringify(none)}）`,
    )

    // 排序：同距离时按 x → y → z（稳定、可复现）
    const sameDistance = findBlocksView(
      makeBot({ positions: [[1, 64, 0], [0, 64, 1], [2, 64, 0], [1, 63, 0]] }),
      { block_names: ['stone'], max_distance: 16, max_results: 8 },
    )
    const order = sameDistance.matches.map(
      (m) => `${m.position.x},${m.position.y},${m.position.z}`,
    )
    // 期望顺序（goal_near → raw → x → y → z，罐头在 (0.5,64,0.5)）：
    //   (0,64,1) 与 (1,64,0) 的 goal_near 都是 1、raw 都是 ~1.58 → 按 x 分先后；
    //   (1,63,0) 的 goal_near 是 √2；(2,64,0) 最远。
    const expected = ['0,64,1', '1,64,0', '1,63,0', '2,64,0']
    assert(
      order.join('|') === expected.join('|'),
      `排序稳定可复现（得到 ${order.join('|')}，期望 ${expected.join('|')}）`,
    )
    assert(
      JSON.stringify(Object.keys(sameDistance.matches[0]).sort()) ===
        JSON.stringify(['block', 'distance', 'position']),
      '每条匹配只有 block / position / distance 三个字段',
    )
    assert(
      !('recommended' in sameDistance.matches[0]) &&
        !('best' in sameDistance.matches[0]) &&
        !('optimal' in sameDistance.matches[0]),
      '绝不返回推荐/最佳（§十五）',
    )
  }

  console.log('[find-blocks-test] G/J：完全没有副作用 + 前台动作跑着时照样能查')
  {
    const bot = makeBot({ positions: [[1, 64, 0]] })
    for (let i = 0; i < 5; i += 1) {
      findBlocksView(bot, { block_names: ['stone'], max_distance: 16, max_results: 8 })
    }
    assert(bot.sideEffects.length === 0, `查询 5 次零副作用（得到 ${JSON.stringify(bot.sideEffects)}）`)

    return (async () => {
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
      const busyBot = makeBot({ positions: [[1, 64, 0]] })
      busyBot.pathfinder = {
        setGoalCalls: [],
        setGoal(goal) {
          busyBot.pathfinder.setGoalCalls.push(goal)
        },
        goal: null,
        isMoving: () => true,
      }
      busyBot.clearControlStates = () => busyBot.sideEffects.push('clearControlStates')
      state.bot = busyBot
      const started = await runtime.execute('move_to', { x: 20, y: 64, z: 0 })
      assert(started.status === 'RUNNING', 'J. 前台动作（move_to）已经在跑')
      const read = await runtime.execute('find_blocks', {
        block_names: ['oak_log'],
        max_distance: 16,
        max_results: 8,
      })
      assert(
        read.status === 'SUCCEEDED' && read.result && read.result.ok === true,
        `J. 只读查询照样能执行（得到 ${read.status}）`,
      )
      let busyCode = null
      try {
        await runtime.execute('dig', { x: 1, y: 64, z: 0, expected_block: 'stone' })
      } catch (error) {
        busyCode = error.code
      }
      assert(busyCode === 'action.busy', `J. 独占动作仍然被拒（得到 ${busyCode}）`)
      runtime.stop()
      assert(
        busyBot.sideEffects.filter((name) => name !== 'clearControlStates').length === 0,
        'J. 全程没有挖 / 装备 / 移动',
      )
    })()
  }
}

main()
  .then(() => {
    console.log(`\n[find-blocks-test] ${checks - failures}/${checks} checks passed`)
    if (failures > 0) {
      console.error(`[find-blocks-test] FAILED: ${failures} check(s)`)
      process.exit(1)
    }
    console.log('[find-blocks-test] ALL CHECKS PASSED')
  })
  .catch((error) => {
    console.error('[find-blocks-test] crashed:', error)
    process.exit(1)
  })
