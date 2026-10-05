'use strict'
/**
 * follow_player 单元测试（Phase 3D）：注册表属性 / 参数校验 / 目标解析 /
 * 官方 Dynamic Goal（GoalFollow + dynamic=true，真实类不 mock）/ entity 重绑 /
 * 丢失宽限 / 最大追逐距离 / cleanup。
 *
 * 通过 require('../runtime.js') 拿到导出的 ACTION_REGISTRY，用 **stub bot**
 * 直接驱动动作的 run/validate/cleanup（不需要真实连接）。
 *
 * 运行：node minecraft_runtime/test/follow_player.test.js
 */

const path = require('path')

const {
  ACTION_REGISTRY,
  FOLLOW_DEFAULTS,
} = require(path.join(__dirname, '..', 'runtime.js'))
const { ActionError } = require(path.join(__dirname, '..', 'action_runtime'))
const { goals } = require('mineflayer-pathfinder')
const Vec3 = require('vec3').Vec3 ?? require('vec3')

const TARGET = '空凛'
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function makeStubBot(options = {}) {
  const targetName = options.targetName || TARGET
  const targetPosition = options.targetPosition || { x: 2, y: 64, z: 0 }
  const state = { cleared: 0, calls: [] }
  const entity = {
    id: 7,
    username: targetName,
    position: new Vec3(targetPosition.x, targetPosition.y, targetPosition.z),
  }
  const bot = {
    username: 'Bot',
    entity: { position: new Vec3(0, 64, 0) },
    players: {},
    cleared: 0,
    pathfinder: {
      _goal: null,
      _dynamic: null,
      setGoal(goal, dynamic) {
        this._goal = goal
        this._dynamic = Boolean(dynamic)
        state.calls.push({ goal, dynamic: Boolean(dynamic) })
      },
      get goal() {
        return this._goal
      },
      isMoving() {
        return false
      },
    },
    clearControlStates() {
      state.cleared += 1
    },
  }
  if (!options.withoutPlayer) {
    bot.players[targetName] = { username: targetName, entity: options.withoutEntity ? null : entity }
  }
  return { bot, state, entity, targetName }
}

/** 按 execute 的 detached 语义驱动：start（同步启动）→ wait（后台生命周期）。
 *
 * 注意返回 {state, waiting} 而不是直接 return promise——async 函数 return 一个
 * promise 会被隐式 await，导致「start 也把整条跟随生命周期等完」的假挂起。 */
async function startFollow(bot, params, token = { cancelled: false }) {
  const def = ACTION_REGISTRY.follow_player
  const state = await def.start(bot, params, token)
  return { state, waiting: def.wait(bot, params, token, state) }
}

async function expectReject(promise, code) {
  try {
    await promise
    return null
  } catch (error) {
    if (error instanceof ActionError && error.code === code) return error
    return { unexpected: error }
  }
}

async function main() {
  setTimeout(() => {
    console.error('[follow-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[follow-test] 注册表属性（registered / exclusive / risk / timeout）')
  {
    const def = ACTION_REGISTRY.follow_player
    assert(Boolean(def), 'test_follow_player_registered：follow_player 已注册')
    // Phase 4B 起注册表多了 dig（MEDIUM，单方块）——仍是白名单，破坏类动作只有它一个
    assert(
      Object.keys(ACTION_REGISTRY).sort().join(',') ===
        'chat,dig,follow_player,look_at,move_to,place,stop',
      `注册表 = chat/look_at/move_to/follow_player/stop/dig（得到 ${Object.keys(ACTION_REGISTRY).sort().join(',')}）`,
    )
    assert(def.exclusive === true, 'test_follow_player_exclusive：exclusive = true')
    assert(def.risk === 'LOW', `test_follow_player_risk_low：risk = LOW（得到 ${def.risk}）`)
    assert(
      def.timeout_ms === FOLLOW_DEFAULTS.timeoutMs && def.timeout_ms === 120000,
      `test_follow_player_timeout：默认 120s（得到 ${def.timeout_ms}）`,
    )
    assert(typeof def.cleanup === 'function', '有 cleanup（STOP/超时/断开时清 Goal）')
    assert(def.detached === true, 'detached = true（持续型：启动即 RUNNING，终态由事件送达）')
    assert(typeof def.start === 'function' && typeof def.wait === 'function', 'start/wait 两阶段齐备')
    assert(FOLLOW_DEFAULTS.distance === 2.5, '默认跟随距离 2.5')
    assert(
      FOLLOW_DEFAULTS.minDistance === 1.5 && FOLLOW_DEFAULTS.maxDistance === 6,
      '距离范围 1.5 ~ 6',
    )
    assert(FOLLOW_DEFAULTS.maxChaseDistance === 64, '默认最大追逐距离 64')
  }

  console.log('[follow-test] 参数校验（invalid_username / invalid_distance）')
  {
    const def = ACTION_REGISTRY.follow_player
    const badUsernames = ['', '   ', 'a'.repeat(17), 'bad\u0000name', 'bad\nname', 'tab\tname']
    for (const username of badUsernames) {
      let error = null
      try {
        def.validate({ username })
      } catch (caught) {
        error = caught
      }
      assert(
        error instanceof ActionError && error.code === 'action.invalid',
        `test_follow_player_invalid_username：${JSON.stringify(username)} → action.invalid`,
      )
    }
    const okName = def.validate({ username: TARGET })
    assert(okName.username === TARGET && okName.distance === 2.5, '合法 username 默认距离 2.5')

    const badDistances = [0, 1.0, 1.49, 6.01, 7, -3, NaN, Infinity, '2.5']
    for (const distance of badDistances) {
      let error = null
      try {
        def.validate({ username: TARGET, distance })
      } catch (caught) {
        error = caught
      }
      assert(
        error instanceof ActionError && error.code === 'action.invalid',
        `test_follow_player_invalid_distance：distance=${String(distance)} → action.invalid`,
      )
    }
    assert(def.validate({ username: TARGET, distance: 1.5 }).distance === 1.5, '边界 1.5 通过')
    assert(def.validate({ username: TARGET, distance: 6 }).distance === 6, '边界 6 通过')
    assert(def.validate({ username: TARGET, distance: undefined }).distance === 2.5, '缺省距离用默认值')
  }

  console.log('[follow-test] 目标解析：找不到玩家/entity → 404 player.not_found，不启动 Pathfinder')
  {
    for (const options of [{ withoutPlayer: true }, { withoutEntity: true }]) {
      const { bot, state } = makeStubBot(options)
      const error = await expectReject(
        startFollow(bot, { username: TARGET, distance: 2.5 }),
        'player.not_found',
      )
      assert(
        error instanceof ActionError && error.status === 404,
        `test_follow_player_target_not_found：${JSON.stringify(options)} → 404`,
      )
      assert(state.calls.length === 0, '不启动 Pathfinder（setGoal 从未调用）')
    }
  }

  console.log('[follow-test] Dynamic Goal：真实 GoalFollow + dynamic=true + 活 entity 引用')
  {
    const { bot, state, entity } = makeStubBot()
    const token = { cancelled: false }
    const { waiting: running } = await startFollow(bot, { username: TARGET, distance: 2.5 }, token)
    await sleep(100)
    assert(state.calls.length === 1, '正好一次 setGoal（启动时）')
    assert(state.calls[0].goal instanceof goals.GoalFollow, '使用官方 goals.GoalFollow（真实类，不是 mock）')
    assert(state.calls[0].dynamic === true, 'setGoal(goal, true)：dynamic === true')
    assert(state.calls[0].goal.entity === entity, 'Goal 持有活 entity 引用（不是静态坐标）')
    assert(bot.pathfinder.goal instanceof goals.GoalFollow, 'pathfinder.goal 是 GoalFollow')
    token.cancelled = true
    await running
  }

  console.log('[follow-test] §十五：只有旧 entity 失效才重绑 Dynamic Goal')
  {
    const { bot, state, targetName } = makeStubBot()
    const token = { cancelled: false }
    const { waiting: running } = await startFollow(bot, { username: TARGET, distance: 2.5 }, token)
    await sleep(300)
    assert(state.calls.length === 1, '起步一次 setGoal')

    // 同一个 entity 正常移动 → 不得 setGoal（动态 Goal 自己会重规划）
    bot.players[targetName].entity.position = new Vec3(6, 64, 0)
    await sleep(300)
    assert(state.calls.length === 1, '实体移动不触发 setGoal（交给 pathfinder goal_moved）')

    // 服务器重建 entity（新对象）→ 才允许重绑
    const newEntity = {
      id: 8,
      username: targetName,
      position: new Vec3(3, 64, 0),
    }
    bot.players[targetName].entity = newEntity
    await sleep(300)
    assert(state.calls.length === 2, 'entity 重建 → 重绑一次 Dynamic Goal')
    assert(state.calls[1].goal.entity === newEntity, '重绑到新 entity 引用')

    token.cancelled = true
    await running
  }

  console.log('[follow-test] §十 E/§十六：丢失宽限（短暂刷新不失败，超时才 player_lost）')
  {
    process.env.MC_FOLLOW_LOST_GRACE_MS = '600'
    try {
      const { bot, targetName } = makeStubBot()
      const token = { cancelled: false }
      const { waiting: running } = await startFollow(bot, { username: TARGET, distance: 2.5 }, token)
      await sleep(100)
      bot.players[targetName].entity = null // 一帧丢失
      await sleep(150)
      bot.players[targetName].entity = { id: 9, username: targetName, position: new Vec3(2, 64, 0) }
      await sleep(200)
      let settled = false
      running.then(() => (settled = true), () => (settled = true))
      await sleep(50)
      assert(!settled, '短暂 entity 刷新不失败（宽限期内恢复）')

      // 彻底消失 → 超过宽限 → FAILED player_lost
      bot.players[targetName].entity = null
      const error = await expectReject(running, 'player.lost')
      assert(error instanceof ActionError && error.status === 500, 'test：超出宽限 → player.lost（500）')
      assert(bot.pathfinder.goal === null, 'player_lost 后 Goal 已清空（动作自己清理）')
    } finally {
      delete process.env.MC_FOLLOW_LOST_GRACE_MS
    }
  }

  console.log('[follow-test] §十七：最大追逐距离 → FAILED follow.target_too_far')
  {
    process.env.MC_FOLLOW_MAX_CHASE_DISTANCE = '8'
    try {
      const { bot } = makeStubBot({ targetPosition: { x: 20, y: 64, z: 0 } })
      const { waiting: running } = await startFollow(bot, { username: TARGET, distance: 2.5 })
      const error = await expectReject(running, 'follow.target_too_far')
      assert(error instanceof ActionError && error.status === 500, '距离 20 > 上限 8 → follow.target_too_far')
      assert(bot.pathfinder.goal === null, '失败后 Goal 已清空')
    } finally {
      delete process.env.MC_FOLLOW_MAX_CHASE_DISTANCE
    }
  }

  console.log('[follow-test] 取消令牌：run 干净收尾（由上层按 CANCELLED 落定）')
  {
    const { bot, state } = makeStubBot()
    const token = { cancelled: false }
    const { waiting: running } = await startFollow(bot, { username: TARGET, distance: 2.5 }, token)
    await sleep(100)
    token.cancelled = true
    await running // wait resolve（上层按 CANCELLED 收尾 + cleanup）
    assert(state.calls.length === 1, '取消后不再有 setGoal')
  }

  console.log('[follow-test] cleanup：清 Goal + 清控制位（goal == null / 两件套）')
  {
    const { bot, state } = makeStubBot()
    bot.pathfinder.setGoal(new goals.GoalFollow({ position: new Vec3(0, 64, 0) }, 2.5), true)
    assert(bot.pathfinder.goal !== null, '前置：有 Goal')
    ACTION_REGISTRY.follow_player.cleanup(bot)
    assert(state.calls.at(-1).goal === null, 'test_follow_player_cleanup_clears_goal：setGoal(null)')
    assert(bot.pathfinder.goal === null, 'goal == null')
    assert(state.cleared === 1, 'clearControlStates 被调用')
    // 与 move_to 的 cleanup 同形（两件套齐全）
    assert(typeof ACTION_REGISTRY.move_to.cleanup === 'function', 'move_to 同款 cleanup 保留')
  }

  clearInterval(keepAlive)
  console.log(`\n[follow-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.error(`[follow-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[follow-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[follow-test] fatal:', error)
  process.exit(1)
})
