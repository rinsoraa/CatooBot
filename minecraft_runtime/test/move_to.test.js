'use strict'
/**
 * move_to 注册与安全配置测试（Phase 3C）：注册表属性 / 坐标与距离校验 /
 * 非破坏性 Movements（禁 dig、禁搭桥/搭塔）。
 *
 * 通过 require('../runtime.js') 拿到导出的注册表与助手——runtime 处于
 * require.main 守卫内，require 时不会监听端口、不注册信号处理。
 *
 * 运行：node minecraft_runtime/test/move_to.test.js
 */

const path = require('path')

const {
  ACTION_REGISTRY,
  configureMovements,
  MOVE_DEFAULTS,
} = require(path.join(__dirname, '..', 'runtime.js'))
const { ActionError } = require(path.join(__dirname, '..', 'action_runtime'))

let failures = 0
let checks = 0

function assert(condition, message) {
  checks += 1
  if (!condition) {
    failures += 1
    console.error(`  ✗ ${message}`)
  } else {
    console.log(`  ✓ ${message}`)
  }
}

function throwsInvalid(fn) {
  try {
    fn()
    return null
  } catch (error) {
    return error
  }
}

function main() {
  console.log('[move-to-test] move_to 注册表属性')
  {
    const def = ACTION_REGISTRY.move_to
    assert(Boolean(def), 'move_to 已注册')
    assert(def.exclusive === true, 'exclusive = true（同一时间只允许一个前台动作）')
    assert(def.risk === 'LOW', `risk = LOW（得到 ${def.risk}）`)
    assert(def.timeout_ms === 30000, `默认 timeout = 30s（得到 ${def.timeout_ms}）`)
    assert(typeof def.validate === 'function', '有参数校验')
    assert(typeof def.run === 'function', '有执行体')
    assert(typeof def.cleanup === 'function', '有 cleanup（停止/超时/断开时清 Goal）')
    // 注册表只允许已批准的动作（Phase 3D 起含 follow_player）
    assert(
      Object.keys(ACTION_REGISTRY).sort().join(',') === 'chat,follow_player,look_at,move_to,stop',
      `注册表不含额外动作（得到 ${Object.keys(ACTION_REGISTRY).sort().join(',')}）`,
    )
  }

  console.log('[move-to-test] MOVE_DEFAULTS（半径 / 最大距离 / 超时）')
  {
    assert(MOVE_DEFAULTS.radius === 1.5, `GoalNear radius = 1.5（得到 ${MOVE_DEFAULTS.radius}）`)
    assert(MOVE_DEFAULTS.maxDistance === 64, `max_distance 默认 64（得到 ${MOVE_DEFAULTS.maxDistance}）`)
    assert(MOVE_DEFAULTS.timeoutMs === 30000, `timeout 30s（得到 ${MOVE_DEFAULTS.timeoutMs}）`)
  }

  console.log('[move-to-test] 坐标校验（世界边界；distance 检查需要在线 bot，离线时只校验坐标）')
  {
    const def = ACTION_REGISTRY.move_to
    // 合法坐标原样通过（当前无 bot → 距离检查跳过）
    const ok = def.validate({ x: 120, y: 64, z: -230 })
    assert(ok && ok.x === 120 && ok.y === 64 && ok.z === -230, '合法坐标通过并归一化')

    const badCases = [
      ['字符串坐标', { x: '120', y: 64, z: -230 }],
      ['缺坐标', { x: 1, y: 2 }],
      ['NaN', { x: NaN, y: 64, z: 0 }],
      ['Infinity', { x: 0, y: Infinity, z: 0 }],
      ['x 超世界边界', { x: 4.0e7, y: 64, z: 0 }],
      ['y 超世界边界', { x: 0, y: 3000, z: 0 }],
      ['y 低于世界下限', { x: 0, y: -600, z: 0 }],
    ]
    for (const [label, params] of badCases) {
      const error = throwsInvalid(() => def.validate(params))
      assert(
        error instanceof ActionError && error.code === 'action.invalid',
        `${label} → action.invalid（得到 ${error && error.code}）`,
      )
    }
  }

  console.log('[move-to-test] Movements 非破坏性配置（§三）')
  {
    const movements = configureMovements({})
    assert(movements.canDig === false, 'canDig = false（绝不挖方块）')
    assert(Array.isArray(movements.scafoldingBlocks) && movements.scafoldingBlocks.length === 0, 'scafoldingBlocks = []（绝不搭桥/搭塔/放方块）')
    assert(movements.canOpenDoors === false, 'canOpenDoors = false（保守默认）')
  }

  console.log(`\n[move-to-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.error(`[move-to-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[move-to-test] ALL CHECKS PASSED')
}

main()
