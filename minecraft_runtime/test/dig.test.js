/**
 * dig 注册与安全配置测试（Phase 4B）：注册表属性 / 参数校验 / 执行前校验（start 阶段）/
 * 结果复核（wait 阶段）/ cleanup。
 *
 * 这里不连真实服务器：start/wait 的校验全用假 bot 直测（与 move_to.test.js 同一手法），
 * 真正的挖掘行为由 e2e.js（flying-squid）与真实服务器 smoke 负责。
 *
 * 运行：node minecraft_runtime/test/dig.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const { ACTION_REGISTRY, DIG_DEFAULTS, actionRuntime } = require('../runtime.js')

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

function makeBot({ block = null, position = new Vec3(0, 64, 0), canDig = true } = {}) {
  return {
    entity: { position },
    blockAt: () => block,
    canDigBlock: () => canDig,
    dig: async () => {},
    stopDigging() {},
    clearControlStates() {},
  }
}

function makeBlock(name, x, y, z) {
  return { name, position: new Vec3(x, y, z), diggable: true }
}

async function expectCode(promise, code, label) {
  try {
    await promise
  } catch (error) {
    assert(
      error && error.code === code,
      `${label} → ${code}（得到 ${error && (error.code || error.message)}）`,
    )
    return
  }
  assert(false, `${label} → 期望 ${code}，但成功返回了`)
}

// 目标离 bot（0,64,0，眼睛 +1.65）约 3.2 格：在 max_dig_distance(5) 之内
const PARAMS = { x: 2, y: 64, z: 2, expected_block: 'minecraft:stone' }

async function main() {
  console.log('[dig-test] dig 注册表属性')
  {
    const def = ACTION_REGISTRY.dig
    assert(Boolean(def), 'dig 已注册')
    assert(def.exclusive === true, 'exclusive = true（不能边挖边走）')
    assert(def.risk === 'MEDIUM', `risk = MEDIUM（得到 ${def.risk}）`)
    assert(def.timeout_ms === 30000, `默认 timeout = 30s（得到 ${def.timeout_ms}）`)
    assert(def.detached === true, 'detached = true（启动即 RUNNING，终态经事件）')
    assert(typeof def.validate === 'function', '有参数校验')
    assert(typeof def.start === 'function' && typeof def.wait === 'function', '有 start/wait')
    assert(typeof def.run !== 'function', '没有阻塞式 run')
    assert(typeof def.cleanup === 'function', '有 cleanup（stopDigging + 清控制位）')
    assert(
      Object.keys(ACTION_REGISTRY).sort().join(',') ===
        'chat,container_inspect,container_transfer,craft,dig,equip,follow_player,inventory_move,look_at,move_to,place,recipe_lookup,stop',
      `注册表只有已批准动作（得到 ${Object.keys(ACTION_REGISTRY).sort().join(',')}）`,
    )
    // §三 禁止清单：连续挖掘/范围破坏/批量放置类动作一个都不许有
    // （place 从 Phase 4C 起是正式的单方块动作，所以不在禁止清单里；
    //  place_multiple / build / bridge / schematic 这些批量能力仍然必须不存在）
    for (const forbidden of [
      'mine',
      'mine_ore',
      'collect',
      'gather',
      'strip_mine',
      'break_area',
      'dig_multiple',
      'auto_mine',
      'place_multiple',
      'attack',
      // Phase 4F 起 craft 是正式动作（单配方、需确认）→ 这里禁的是批量/链式/自动合成
      'craft_all',
      'auto_craft',
      'craft_chain',
      'crafting_table',
      'smelt',
      'trade',
      'eat',
      'inventory',
      'container',
    ]) {
      assert(ACTION_REGISTRY[forbidden] === undefined, `未注册 ${forbidden}`)
    }
  }

  console.log('[dig-test] DIG_DEFAULTS（超时 / 距离上限）')
  {
    assert(DIG_DEFAULTS.timeoutMs === 30000, `timeout 30s（得到 ${DIG_DEFAULTS.timeoutMs}）`)
    assert(DIG_DEFAULTS.maxDistance === 5, `max_dig_distance 5（得到 ${DIG_DEFAULTS.maxDistance}）`)
  }

  console.log('[dig-test] 参数校验（坐标 + expected_block 必填）')
  {
    const def = ACTION_REGISTRY.dig
    const ok = def.validate({ x: 120, y: 64, z: -230, expected_block: 'minecraft:stone' })
    assert(
      ok.x === 120 && ok.y === 64 && ok.z === -230 && ok.expected_block === 'minecraft:stone',
      '合法参数通过并归一化',
    )

    const badCases = [
      ['缺 expected_block', { x: 1, y: 2, z: 3 }, 'block.invalid'],
      ['空 expected_block', { x: 1, y: 2, z: 3, expected_block: '   ' }, 'block.invalid'],
      ['expected_block 不是字符串', { x: 1, y: 2, z: 3, expected_block: 42 }, 'block.invalid'],
      ['expected_block 过长', { x: 1, y: 2, z: 3, expected_block: 'x'.repeat(65) }, 'block.invalid'],
      ['字符串坐标', { x: '1', y: 2, z: 3, expected_block: 'stone' }, 'action.invalid'],
      ['NaN', { x: NaN, y: 2, z: 3, expected_block: 'stone' }, 'action.invalid'],
      ['Infinity', { x: 1, y: Infinity, z: 3, expected_block: 'stone' }, 'action.invalid'],
      ['x 超世界边界', { x: 4.0e7, y: 64, z: 0, expected_block: 'stone' }, 'action.invalid'],
      ['y 低于世界下限', { x: 0, y: -600, z: 0, expected_block: 'stone' }, 'action.invalid'],
    ]
    // 坐标沿用既有世界坐标校验（action.invalid）；expected_block 是 dig 特有的（block.invalid）
    for (const [label, params, expected] of badCases) {
      let code = null
      try {
        def.validate(params)
      } catch (error) {
        code = error.code
      }
      assert(code === expected, `${label} → ${expected}（得到 ${code}）`)
    }
  }

  console.log('[dig-test] start 阶段校验（同步反馈，绝不进入挖掘）')
  {
    const def = ACTION_REGISTRY.dig

    // 目标位置没有方块 / 是空气 → block.not_found
    await expectCode(
      def.start(makeBot({ block: null }), PARAMS),
      'block.not_found',
      '方块不存在',
    )
    await expectCode(
      def.start(makeBot({ block: makeBlock('air', 3, 64, 3) }), PARAMS),
      'block.not_found',
      '空气',
    )
    await expectCode(
      def.start(makeBot({ block: makeBlock('cave_air', 3, 64, 3) }), PARAMS),
      'block.not_found',
      'cave_air',
    )

    // §十四：方块已经变了 → block.changed（带 expected/actual）
    const changed = await def
      .start(makeBot({ block: makeBlock('minecraft:dirt', 3, 64, 3) }), PARAMS)
      .then(() => null)
      .catch((error) => error)
    assert(changed && changed.code === 'block.changed', `方块变了 → block.changed（得到 ${changed && changed.code}）`)
    assert(
      changed && changed.detail && changed.detail.expected === 'minecraft:stone' &&
        changed.detail.actual === 'minecraft:dirt',
      'block.changed 带 expected/actual（结构化，不回传原始异常文本）',
    )

    // §十七：太远 → block.too_far（用真实距离算：bot 在 (0,64,0)，目标 (3,64,3)）
    const farBot = makeBot({ block: makeBlock('minecraft:stone', 30, 64, 30) })
    await expectCode(def.start(farBot, { x: 30, y: 64, z: 30, expected_block: 'minecraft:stone' }), 'block.too_far', '太远')

    // §十六：canDigBlock false → block.not_diggable（不换工具、不走近）
    await expectCode(
      def.start(makeBot({ block: makeBlock('minecraft:stone', 3, 64, 3), canDig: false }), PARAMS),
      'block.not_diggable',
      '挖不动',
    )

    // 正常：start 返回句柄（不执行挖掘）
    const state = await def.start(makeBot({ block: makeBlock('minecraft:stone', 3, 64, 3) }), PARAMS)
    assert(state && state.blockName === 'minecraft:stone' && state.position, 'start 返回 {block, position, blockName}')
  }

  console.log('[dig-test] wait 阶段：不信任 dig Promise，必须复核方块真的没了')
  {
    const def = ACTION_REGISTRY.dig
    const state = {
      block: makeBlock('minecraft:stone', 3, 64, 3),
      position: new Vec3(3, 64, 3),
      blockName: 'minecraft:stone',
    }

    // 方块仍在原位（客户端/服务器不同步）→ block.break_unconfirmed
    await expectCode(
      def.wait(makeBot({ block: makeBlock('minecraft:stone', 3, 64, 3) }), PARAMS, { cancelled: false }, state),
      'block.break_unconfirmed',
      '方块仍在',
    )

    // 挖成功：复核读到空气 → 返回真实结果
    const result = await def.wait(
      makeBot({ block: makeBlock('air', 3, 64, 3) }),
      PARAMS,
      { cancelled: false },
      state,
    )
    assert(
      result.block_before === 'minecraft:stone' && result.block_after === 'air',
      `结果带 block_before/block_after（得到 ${JSON.stringify(result)}）`,
    )

    // 目标方块消失（blockAt → null）也算破坏成功
    const gone = await def.wait(makeBot({ block: null }), PARAMS, { cancelled: false }, state)
    assert(gone.block_after === 'air', 'blockAt 为 null 视为已破坏（block_after=air）')

    // 挖掘被中断（digging aborted）且 token 未取消 → 稳定失败码
    const abortedBot = makeBot({ block: makeBlock('air', 3, 64, 3) })
    abortedBot.dig = async () => {
      throw new Error('Digging aborted.')
    }
    await expectCode(
      def.wait(abortedBot, PARAMS, { cancelled: false }, state),
      'block.dig_aborted',
      '挖掘被中断',
    )

    // 其它异常 → action.failed（不回传原始堆栈）
    const brokenBot = makeBot({ block: makeBlock('air', 3, 64, 3) })
    brokenBot.dig = async () => {
      throw new Error('socket hang up')
    }
    await expectCode(def.wait(brokenBot, PARAMS, { cancelled: false }, state), 'action.failed', 'socket hang up')
  }

  console.log('[dig-test] cleanup：stopDigging + 清控制位')
  {
    const def = ACTION_REGISTRY.dig
    let stopped = 0
    let cleared = 0
    def.cleanup({
      stopDigging() {
        stopped += 1
      },
      clearControlStates() {
        cleared += 1
      },
    })
    assert(stopped === 1 && cleared === 1, `cleanup 调用了两者（stopDigging=${stopped}, clear=${cleared}）`)

    // 没有 bot（断开后）也不能炸；stopDigging 抛错也必须被吞掉
    def.cleanup(null)
    def.cleanup({
      stopDigging() {
        throw new Error('not connected')
      },
      clearControlStates() {},
    })
    assert(true, 'cleanup 对 null bot / 抛错的 stopDigging 都安全')
  }

  console.log('[dig-test] actionRuntime 仍然只有一套（未新建第二套 Action Manager）')
  {
    assert(typeof actionRuntime.execute === 'function', 'execute 在')
    assert(typeof actionRuntime.stop === 'function', 'stop 在')
    assert(typeof actionRuntime.snapshot === 'function', 'snapshot 在')
  }

  console.log(`\n[dig-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.error(`[dig-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[dig-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[dig-test] crashed:', error)
  process.exit(1)
})
