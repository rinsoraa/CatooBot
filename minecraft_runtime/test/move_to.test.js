'use strict'
/**
 * move_to 注册与安全配置测试（Phase 3C）+ **Phase 4H.1 的 ActionRuntime 行为测试**。
 *
 * 前半段（注册表属性 / 坐标与距离校验 / 非破坏性 Movements）保持 Phase 3C 不变；
 * 后半段是 Phase 4H.1 新增的**真实生命周期测试**：假 bot 的 pathfinder 会像
 * mineflayer-pathfinder 那样发 `goal_reached` / `path_update`（含空路径）/ `goal_updated` /
 * `path_stop`，验证 CatooBot 自己定义的到达语义 —— 绝不相信 `goto()` 的 resolve。
 *
 * 覆盖（§二十五 A–L）：
 *   A  正常到达（goal_reached + 实际位置在半径内）→ SUCCEEDED + 结果形状 + goal 清空 + 摘监听
 *   A2 goal_reached 但实际位置很远 → FAILED/path.not_reached（§八 二次验证）
 *   B  核心回归：path=[] + status='success' + 实际 28 格 → 绝不 SUCCEEDED（path.not_reached）
 *   C  path=[] + status='success' + 已经在半径内 → SUCCEEDED
 *   D  path=[] + noPath → path.not_found（§十一）；D2 非空 path + noPath 同样 path.not_found
 *   E  status='timeout' → FAILED（path.not_found）
 *   F  status='partial' → 继续等待（随后 goal_reached 才成功）
 *   G  path_stop（远处）→ path.not_reached；G2 path_stop（已在半径内）→ SUCCEEDED
 *   H  goal_updated 换成别的 Goal → FAILED/goal.changed
 *   I  STOP 竞态：goal_reached + STOP → 只有一个终态（CANCELLED）+ cleanup 恰好一次
 *   J  TIMEOUT：超时后晚到的 goal_reached 仍然是 TIMEOUT；goal_reached 先到则 completed
 *   K  cleanup 恰好一次（STOP / TIMEOUT 两条路径）
 *   L  结果口径：final_position / distance_to_target（GoalNear 口径）/ raw_distance_to_target
 *
 * 运行：node minecraft_runtime/test/move_to.test.js
 */

const path = require('path')
const { Vec3 } = require('vec3')

const {
  ACTION_REGISTRY,
  configureMovements,
  MOVE_DEFAULTS,
} = require(path.join(__dirname, '..', 'runtime.js'))
const {
  ActionError,
  createActionRuntime,
} = require(path.join(__dirname, '..', 'action_runtime'))

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

//: 终态事件（ActionRuntime 契约）
const TERMINAL_EVENTS = [
  'minecraft.action.completed',
  'minecraft.action.failed',
  'minecraft.action.cancelled',
  'minecraft.action.timeout',
]

function makeEmitter() {
  const handlers = new Map()
  return {
    on(event, handler) {
      handlers.set(event, [...(handlers.get(event) || []), handler])
    },
    removeListener(event, handler) {
      handlers.set(
        event,
        (handlers.get(event) || []).filter((entry) => entry !== handler),
      )
    },
    emit(event, ...args) {
      for (const handler of [...(handlers.get(event) || [])]) handler(...args)
    },
    listenerCount(event) {
      if (event === undefined) {
        let total = 0
        for (const list of handlers.values()) total += list.length
        return total
      }
      return (handlers.get(event) || []).length
    },
  }
}

const MOVE = ACTION_REGISTRY.move_to

/**
 * 假 bot：真正的 Pathfinder 事件面 —— setGoal / goal / isMoving / goal_updated /
 * path_update / goal_reached / path_stop 全都在（§二十六：绝不再 fake 成
 * ``goto = Promise.resolve()``，否则根本测不到本阶段的问题）。
 */
function makeBot(position = new Vec3(0, 64, 0)) {
  const emitter = makeEmitter()
  const moving = { value: false }
  const bot = {
    entity: { position },
    pathfinder: {
      setGoalCalls: [],
      setGoal(goal) {
        bot.pathfinder.setGoalCalls.push(goal)
        // 与 mineflayer-pathfinder 一致：setGoal 会发 goal_updated（null 也是）
        emitter.emit('goal_updated', goal)
      },
      get goal() {
        const calls = bot.pathfinder.setGoalCalls
        return calls.length > 0 ? calls[calls.length - 1] : null
      },
      isMoving() {
        return moving.value
      },
      // 让测试可以像真 Pathfinder 那样"开始/停止移动"
      setMoving(value) {
        moving.value = Boolean(value)
      },
    },
    clearedStates: 0,
    clearControlStates() {
      bot.clearedStates += 1
    },
    ...emitter,
  }
  return bot
}

function makeHarness(definition = MOVE) {
  const events = []
  const logs = []
  const state = { bot: null, online: true }
  const runtime = createActionRuntime({
    registry: { move_to: definition },
    getBot: () => state.bot,
    isOnline: () => state.online,
    emit: (event, data) => events.push({ event, data }),
    log: (message) => logs.push(message),
    now: () => Date.now(),
  })
  return { runtime, events, logs, state }
}

function terminals(events, actionId) {
  return events.filter(
    (row) => TERMINAL_EVENTS.includes(row.event) && row.data.action_id === actionId,
  )
}

function completedResult(events, actionId) {
  const row = events.find(
    (entry) => entry.event === 'minecraft.action.completed' && entry.data.action_id === actionId,
  )
  return row ? row.data.result : null
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function settle(harness, actionId, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const rows = terminals(harness.events, actionId)
    if (rows.length > 0) {
      await sleep(30) // 留出"第二个终态"可能出现的时间
      return terminals(harness.events, actionId)[0]
    }
    await sleep(10)
  }
  return null
}

/** 把动作的 timeout_ms 临时调小（定义是普通对象，execute 时读取它）。 */
async function withTimeoutMs(ms, run) {
  const original = MOVE.timeout_ms
  MOVE.timeout_ms = ms
  try {
    return await run()
  } finally {
    MOVE.timeout_ms = original
  }
}

/** 只统计**动作自己**的 cleanup 被调用几次（stop() 还会清 clearControlStates，不能混算）。 */
function withCleanupCounter(definition) {
  let calls = 0
  return {
    calls: () => calls,
    definition: {
      ...definition,
      cleanup(bot, controller) {
        calls += 1
        return definition.cleanup(bot, controller)
      },
    },
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
    // Phase 3E：move_to 与 follow_player 同为持续型动作 —— start/wait 两阶段
    // （导航几十秒，绝不能让 HTTP 调用方/LLM Tool 阻塞等待）
    assert(def.detached === true, 'detached = true（启动即返回 RUNNING）')
    assert(typeof def.start === 'function', '有启动阶段（start）')
    assert(typeof def.wait === 'function', '有生命周期（wait）')
    assert(typeof def.run !== 'function', '没有阻塞式执行体（run）')
    assert(typeof def.cleanup === 'function', '有 cleanup（停止/超时/断开时清 Goal）')
    // 注册表只允许已批准的动作（4B 起含 dig、4C 起含 place、4D 起含 equip/inventory_move；
    // 批量整理 / 容器 / 丢弃 之类的动作一个都没有）
    assert(
      Object.keys(ACTION_REGISTRY).sort().join(',') ===
        'chat,container_inspect,container_transfer,craft,dig,dig_capability,dropped_items,equip,follow_player,inventory_move,look_at,move_to,pickup_item,place,recipe_lookup,stop',
      `注册表不含额外动作（得到 ${Object.keys(ACTION_REGISTRY).sort().join(',')}）`,
    )
    // Phase 4H.1：绝不能再走 mineflayer-pathfinder 的 goto()（2.4.5 会在空路径上静默 resolve）。
    // 去掉注释再匹配 —— 源码里的注释正好在解释"为什么不再用 goto"。
    const waitBody = MOVE.wait.toString().replace(/\/\/[^\n]*/g, '')
    assert(
      !/pathfinder\.goto/.test(waitBody),
      'wait 不调用 pathfinder.goto()（改为自己挂生命周期监听）',
    )
    assert(
      /pathfinder\.setGoal\(state\.goal\)/.test(waitBody),
      'wait 自己 setGoal（§七：先挂监听、再启动导航）',
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
}

// ---------------------------------------------------------------------------
// Phase 4H.1：真实生命周期行为（需要 await，单独一个 async 主体）
// ---------------------------------------------------------------------------

async function behaviour() {
  console.log('[move-to-test] 4H.1 A/A2：正常到达 vs goal_reached 却没到（§八 二次验证）')
  {
    const query = makeHarness()
    const bot = makeBot(new Vec3(0, 64, 0))
    query.state.bot = bot
    const resp = await query.runtime.execute('move_to', { x: 4, y: 64, z: 0 })
    assert(
      resp.status === 'RUNNING' && Boolean(resp.action_id),
      `启动即 RUNNING（得到 ${JSON.stringify(resp)}）`,
    )
    // §七：setGoal 之前监听器必须全部就位
    assert(bot.listenerCount() === 4, `挂了 4 个 Pathfinder 生命周期监听器（得到 ${bot.listenerCount()}）`)
    const goal = bot.pathfinder.setGoalCalls[0]
    assert(
      goal && goal.constructor.name === 'GoalNear' && goal.rangeSq === MOVE_DEFAULTS.radius ** 2,
      `setGoal 收到的是 GoalNear（半径 ${MOVE_DEFAULTS.radius}）（得到 ${goal && goal.constructor.name}）`,
    )
    // 真的走到了：占的格子 (3,64,0) → 目标格 (4,64,0)，距离 1
    bot.entity.position = new Vec3(3.9, 64, 0.2)
    bot.emit('goal_reached', goal)
    const terminal = await settle(query, resp.action_id)
    assert(
      terminal && terminal.event === 'minecraft.action.completed',
      `终态 completed（得到 ${terminal && terminal.event}）`,
    )
    const result = completedResult(query.events, resp.action_id)
    assert(
      result && result.target.x === 4 && result.final_position.x === 3.9,
      `结果带 target / final_position（得到 ${JSON.stringify(result)}）`,
    )
    assert(
      result.distance_to_target === 1 && result.raw_distance_to_target === 0.22,
      `两种距离口径都对（GoalNear 口径 ${result.distance_to_target} / 浮点 ${result.raw_distance_to_target}）`,
    )
    assert(
      bot.pathfinder.setGoalCalls.length === 2 && bot.pathfinder.setGoalCalls[1] === null,
      '成功也自己清 Goal（SUCCEEDED 不触发 cleanup —— 4H 的教训）',
    )
    assert(bot.listenerCount() === 0, '成功后监听器摘干净')
    assert(bot.pathfinder.goal === null, 'isMoving/goal 投影也反映"已经收工"（goal=null）')

    // A2：Pathfinder 说"到了"，但实际位置在 28 格外 → 绝不 SUCCEEDED
    const liar = makeHarness()
    const liarBot = makeBot(new Vec3(0, 64, 0))
    liar.state.bot = liarBot
    const liarResp = await liar.runtime.execute('move_to', { x: 28, y: 64, z: 0 })
    liarBot.emit('goal_reached', liarBot.pathfinder.setGoalCalls[0])
    const liarTerminal = await settle(liar, liarResp.action_id)
    assert(
      liarTerminal && liarTerminal.event === 'minecraft.action.failed',
      `goal_reached 但实际 28 格 → FAILED（得到 ${liarTerminal && liarTerminal.event}）`,
    )
    assert(
      liarTerminal.data.code === 'path.not_reached',
      `错误码 path.not_reached（得到 ${liarTerminal.data.code}）`,
    )
    assert(
      liarTerminal.data.detail && liarTerminal.data.detail.distance_to_target === 28,
      `detail 带实际距离（得到 ${JSON.stringify(liarTerminal.data.detail)})`,
    )
  }

  console.log('[move-to-test] 4H.1 B/C：空路径 + success 是"本阶段最重要的回归"（§十）')
  {
    // B：2.4.5 的 goto 正是在这里静默 resolve 的
    const far = makeHarness()
    const farBot = makeBot(new Vec3(0, 64, 0))
    far.state.bot = farBot
    const farResp = await far.runtime.execute('move_to', { x: 28, y: 64, z: 0 })
    farBot.emit('path_update', { path: [], status: 'success' })
    const farTerminal = await settle(far, farResp.action_id)
    assert(
      farTerminal && farTerminal.event === 'minecraft.action.failed',
      `path=[] + success + 28 格 → 绝不 SUCCEEDED（得到 ${farTerminal && farTerminal.event}）`,
    )
    assert(
      farTerminal.data.code === 'path.not_reached',
      `错误码 path.not_reached（得到 ${farTerminal.data.code}）`,
    )
    const detail = farTerminal.data.detail
    assert(
      detail &&
        detail.target.x === 28 &&
        detail.actual.x === 0 &&
        detail.distance_to_target === 28 &&
        detail.radius === 1.5 &&
        detail.goal_reached === false &&
        detail.reason === 'empty_path_without_arrival',
      `detail 四件套 + reason（得到 ${JSON.stringify(detail)}）`,
    )
    assert(
      far.events.every((row) => row.event !== 'minecraft.action.completed'),
      '事件流里没有 completed（没有假成功）',
    )

    // C：空路径 + success 但确实已经在半径内 → 这才是真的"已经在目标"
    const here = makeHarness()
    const hereBot = makeBot(new Vec3(0, 64, 0))
    here.state.bot = hereBot
    const hereResp = await here.runtime.execute('move_to', { x: 0, y: 64, z: 1 })
    hereBot.emit('path_update', { path: [], status: 'success' })
    const hereTerminal = await settle(here, hereResp.action_id)
    assert(
      hereTerminal && hereTerminal.event === 'minecraft.action.completed',
      `已经在半径内 → completed（得到 ${hereTerminal && hereTerminal.event}）`,
    )
    assert(
      completedResult(here.events, hereResp.action_id).distance_to_target === 1,
      '结果里的距离是重新读的实际位置算出来的',
    )
  }

  console.log('[move-to-test] 4H.1 D/E/F：noPath / timeout / partial（§九/§十二/§十三）')
  {
    const empty = makeHarness()
    empty.state.bot = makeBot()
    const emptyResp = await empty.runtime.execute('move_to', { x: 10, y: 64, z: 0 })
    empty.state.bot.emit('path_update', { path: [], status: 'noPath' })
    const emptyTerminal = await settle(empty, emptyResp.action_id)
    assert(
      emptyTerminal &&
        emptyTerminal.event === 'minecraft.action.failed' &&
        emptyTerminal.data.code === 'path.not_found',
      `path=[] + noPath → path.not_found（得到 ${emptyTerminal && emptyTerminal.data.code}）`,
    )
    assert(
      emptyTerminal.data.detail && emptyTerminal.data.detail.reason === 'no_path',
      `detail.reason = no_path（得到 ${emptyTerminal.data.detail && emptyTerminal.data.detail.reason}）`,
    )

    const partialPath = makeHarness()
    partialPath.state.bot = makeBot()
    const partialResp = await partialPath.runtime.execute('move_to', { x: 10, y: 64, z: 0 })
    partialPath.state.bot.emit('path_update', { path: [{ x: 1, y: 64, z: 0 }], status: 'noPath' })
    const partialTerminal = await settle(partialPath, partialResp.action_id)
    assert(
      partialTerminal && partialTerminal.data.code === 'path.not_found',
      `非空 path + noPath 同样 path.not_found（§九）（得到 ${partialTerminal && partialTerminal.data.code}）`,
    )

    const timedOut = makeHarness()
    timedOut.state.bot = makeBot()
    const timedOutResp = await timedOut.runtime.execute('move_to', { x: 10, y: 64, z: 0 })
    timedOut.state.bot.emit('path_update', { path: [], status: 'timeout' })
    const timedOutTerminal = await settle(timedOut, timedOutResp.action_id)
    assert(
      timedOutTerminal &&
        timedOutTerminal.event === 'minecraft.action.failed' &&
        timedOutTerminal.data.code === 'path.not_found',
      `status=timeout → FAILED/path.not_found（得到 ${timedOutTerminal && timedOutTerminal.data.code}）`,
    )
    assert(
      timedOut.state.bot.pathfinder.setGoalCalls.includes(null),
      '失败后 Goal 已清（没有残留导航意图）',
    )

    // F：partial 只是"先走近一点" → 继续等
    const partial = makeHarness()
    const partialBot = makeBot()
    partial.state.bot = partialBot
    const partialRun = await partial.runtime.execute('move_to', { x: 2, y: 64, z: 0 })
    partialBot.emit('path_update', { path: [{ x: 1, y: 64, z: 0 }], status: 'partial' })
    await sleep(40)
    assert(
      terminals(partial.events, partialRun.action_id).length === 0,
      'partial 之后仍在运行（不落终态）',
    )
    partialBot.entity.position = new Vec3(1.6, 64, 0)
    partialBot.emit('goal_reached', partialBot.pathfinder.setGoalCalls[0])
    const afterPartial = await settle(partial, partialRun.action_id)
    assert(
      afterPartial && afterPartial.event === 'minecraft.action.completed',
      `partial → 继续走 → goal_reached 才 completed（得到 ${afterPartial && afterPartial.event}）`,
    )
  }

  console.log('[move-to-test] 4H.1 G：path_stop 必须验证位置（§十五）')
  {
    const stoppedFar = makeHarness()
    stoppedFar.state.bot = makeBot(new Vec3(0, 64, 0))
    const farResp = await stoppedFar.runtime.execute('move_to', { x: 12, y: 64, z: 0 })
    stoppedFar.state.bot.emit('path_stop')
    const farTerminal = await settle(stoppedFar, farResp.action_id)
    assert(
      farTerminal &&
        farTerminal.event === 'minecraft.action.failed' &&
        farTerminal.data.code === 'path.not_reached',
      `远处 path_stop → path.not_reached（得到 ${farTerminal && farTerminal.data.code}）`,
    )
    assert(
      farTerminal.data.detail.reason === 'path_stopped_short',
      `detail.reason = path_stopped_short（得到 ${farTerminal.data.detail.reason}）`,
    )

    const stoppedNear = makeHarness()
    const nearBot = makeBot(new Vec3(0, 64, 0))
    stoppedNear.state.bot = nearBot
    const nearResp = await stoppedNear.runtime.execute('move_to', { x: 1, y: 64, z: 0 })
    nearBot.entity.position = new Vec3(0.4, 64, 0)
    nearBot.emit('path_stop')
    const nearTerminal = await settle(stoppedNear, nearResp.action_id)
    assert(
      nearTerminal && nearTerminal.event === 'minecraft.action.completed',
      `已在半径内的 path_stop → completed（得到 ${nearTerminal && nearTerminal.event}）`,
    )
  }

  console.log('[move-to-test] 4H.1 H：Goal 被换成别的（§十四 防御分支）')
  {
    const query = makeHarness()
    const bot = makeBot()
    query.state.bot = bot
    const resp = await query.runtime.execute('move_to', { x: 5, y: 64, z: 0 })
    // 第一个 goal_updated 是我们自己 setGoal 发的（不能误判）；这里发一个"别的 Goal"
    bot.emit('goal_updated', { constructor: { name: 'GoalFollow' } })
    const terminal = await settle(query, resp.action_id)
    assert(
      terminal &&
        terminal.event === 'minecraft.action.failed' &&
        terminal.data.code === 'goal.changed',
      `换目标 → FAILED/goal.changed（得到 ${terminal && terminal.data.code}）`,
    )
    assert(
      terminals(query.events, resp.action_id).length === 1,
      '只有一个终态（自己的 goal_updated 没有误判）',
    )
  }

  console.log('[move-to-test] 4H.1 I/K：STOP 竞态只允许一个终态，cleanup 恰好一次')
  {
    const counter = withCleanupCounter(MOVE)
    const harness = makeHarness(counter.definition)
    const bot = makeBot()
    harness.state.bot = bot
    const resp = await harness.runtime.execute('move_to', { x: 20, y: 64, z: 0 })
    bot.entity.position = new Vec3(20, 64, 0) // 恰好"到达"了……
    bot.emit('goal_reached', bot.pathfinder.setGoalCalls[0]) // ……同时用户按了 STOP
    const stopResult = harness.runtime.stop()
    assert(
      stopResult.cancelled.includes(resp.action_id),
      `STOP 取消了进行中的 move_to（得到 ${JSON.stringify(stopResult)}）`,
    )
    const terminal = await settle(harness, resp.action_id)
    assert(
      terminal && terminal.event === 'minecraft.action.cancelled',
      `竞态下终态是 CANCELLED（得到 ${terminal && terminal.event}）`,
    )
    assert(
      terminals(harness.events, resp.action_id).length === 1,
      `恰好一个终态（得到 ${terminals(harness.events, resp.action_id).length}）`,
    )
    assert(
      harness.events.every((row) => row.event !== 'minecraft.action.completed'),
      '没有 completed（绝不允许"记录 CANCELLED、又报 SUCCEEDED"）',
    )
    assert(counter.calls() === 1, `STOP 路径 cleanup 恰好一次（得到 ${counter.calls()}）`)
    assert(
      bot.pathfinder.setGoalCalls.filter((goal) => goal === null).length >= 1,
      'STOP 之后 Goal 已清',
    )
    assert(bot.listenerCount() === 0, 'STOP 之后监听器全部摘掉')
    assert(bot.clearedStates >= 1, 'STOP 之后清过移动控制位')

    // I2：STOP 之后晚到的 goal_reached 不能产生第二个终态（也不能变成功）
    bot.emit('goal_reached', { constructor: { name: 'GoalNear' } })
    await sleep(40)
    assert(
      terminals(harness.events, resp.action_id).length === 1,
      '晚到的 goal_reached 不会产生第二个终态',
    )
  }

  console.log('[move-to-test] 4H.1 J：超时之后晚到的 goal_reached 仍然是 TIMEOUT（§十七）')
  {
    await withTimeoutMs(120, async () => {
      // 必须在把 timeout_ms 改小**之后**再拷贝定义（拷贝会带走当刻的 timeout_ms）
      const counter = withCleanupCounter(MOVE)
      const harness = makeHarness(counter.definition)
      const bot = makeBot(new Vec3(0, 64, 0))
      harness.state.bot = bot
      const resp = await harness.runtime.execute('move_to', { x: 30, y: 64, z: 0 })
      await sleep(200)
      const terminal = terminals(harness.events, resp.action_id)[0]
      assert(
        terminal && terminal.event === 'minecraft.action.timeout',
        `120ms 到点 → TIMEOUT（得到 ${terminal && terminal.event}）`,
      )
      // 超时之后 Pathfinder 才"报告到达"：绝不允许翻案成 SUCCEEDED
      bot.entity.position = new Vec3(30, 64, 0)
      bot.emit('goal_reached', bot.pathfinder.setGoalCalls[0])
      await sleep(60)
      assert(
        terminals(harness.events, resp.action_id).length === 1,
        `超时后只有一个终态（得到 ${terminals(harness.events, resp.action_id).length}）`,
      )
      assert(
        harness.events.every((row) => row.event !== 'minecraft.action.completed'),
        '超时后晚到的 goal_reached 不产生 completed',
      )
      assert(counter.calls() === 1, `超时路径 cleanup 恰好一次（得到 ${counter.calls()}）`)
      assert(
        bot.pathfinder.setGoalCalls.filter((goal) => goal === null).length >= 1,
        '超时之后 Goal 已清',
      )
      assert(bot.listenerCount() === 0, '超时之后监听器全部摘掉')

      // J3：goal_reached 先到 → completed，而且不会再有 TIMEOUT
      const okHarness = makeHarness()
      const okBot = makeBot(new Vec3(0, 64, 0))
      okHarness.state.bot = okBot
      const okResp = await okHarness.runtime.execute('move_to', { x: 1, y: 64, z: 0 })
      okBot.entity.position = new Vec3(0.6, 64, 0)
      okBot.emit('goal_reached', okBot.pathfinder.setGoalCalls[0])
      const okTerminal = await settle(okHarness, okResp.action_id)
      await sleep(150)
      assert(
        okTerminal && okTerminal.event === 'minecraft.action.completed',
        `先到 → completed（得到 ${okTerminal && okTerminal.event}）`,
      )
      assert(
        terminals(okHarness.events, okResp.action_id).length === 1,
        '成功后不会再有 TIMEOUT（timer 已被终态清掉）',
      )
    })
  }

  console.log('[move-to-test] 4H.1 L：结果口径（GoalNear 口径 vs 浮点距离，负数坐标）')
  {
    const harness = makeHarness()
    const bot = makeBot(new Vec3(-2.5, 64, -6.5))
    harness.state.bot = bot
    const resp = await harness.runtime.execute('move_to', { x: -3, y: 64, z: -7 })
    bot.emit('goal_reached', bot.pathfinder.setGoalCalls[0])
    const terminal = await settle(harness, resp.action_id)
    assert(
      terminal && terminal.event === 'minecraft.action.completed',
      `负坐标同样按 Math.floor 取整（-2.5 → -3）（得到 ${terminal && terminal.event}）`,
    )
    const result = completedResult(harness.events, resp.action_id)
    assert(
      result.distance_to_target === 0 && result.target.x === -3 && result.final_position.x === -2.5,
      `距离口径与 GoalNear 一致（得到 ${JSON.stringify(result)}）`,
    )
    assert(
      typeof result.raw_distance_to_target === 'number' && result.raw_distance_to_target > 0,
      `浮点距离仍然如实上报（得到 ${result.raw_distance_to_target}）`,
    )
  }
}

async function run() {
  const keepAlive = setInterval(() => {}, 1000)
  main()
  await behaviour()
  clearInterval(keepAlive)
  console.log(`\n[move-to-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.error(`[move-to-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[move-to-test] ALL CHECKS PASSED')
}

run().catch((error) => {
  console.error('[move-to-test] crashed:', error)
  process.exit(1)
})
